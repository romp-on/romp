#!/usr/bin/env python3
"""T323 stage 4a (2026-09-11): the assembly checkpoint. A document per leaf transcript records everything before the
cut (the turn holding the last compaction boundary) as identities and record locations, never bodies, plus the
carried emit state and the pre-cut graph facts; a fresh process verifies it, rebuilds the pre-cut turns as lazy atoms,
reads the leaf from the cut's byte offset only, parses that tail through a seeded adapter and proves the prefix by a
hash of its ids. Pinned here over every golden scenario that holds a compaction: the restored tree, hydrated, equals
the whole parse's byte for byte (turn ids, segment ids, atom uuids and bodies); folds after the restore keep equal;
a compaction landing after the document demotes to a whole parse; a rewrite under the cut's guard, a wrong version, a
moved session and a corrupt document each fall back loudly, counted; a body read before hydration is loud; the bytes
the restore reads are the document, the cut's guard and the tail. Synthetic transcripts only (the golden builders)."""
import contextlib
import io
import gzip
import json
import os
import pathlib
import time
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
import sys
sys.path.insert(0, HERE)
import test_event_model_golden as G   # noqa: E402  the synthetic scenario builders

SID = G.SID
NOW = G.NOW
COMPACTING = [n for n in G.SINGLE_FILE if any(r.get("subtype") == "compact_boundary" for r in G.SINGLE_FILE[n][0]())]


def _last_uuid(recs):
    return next((r["uuid"] for r in reversed(recs) if r.get("uuid")), None)


def compacting_variant(recs, tag):
    """A golden scenario's records followed by a compaction (the CLI's shape: the boundary anchored on the last record, its
    summary child, the conversation chaining on) and two more turns: the original scenario becomes the pre-cut part, so its
    every atom kind (forks, rewinds, clears, absorbed attachments, skill atoms, command output, postal authors) is restored
    from the document and compared with the whole parse."""
    t1 = max((em.parse_z(r.get("timestamp")) or 0) for r in recs if r.get("timestamp")) + 600
    b, sm = "b_%s" % tag, "s_%s" % tag
    more = [G.compact_line(t1, b, _last_uuid(recs)),
            G.compact_summary_line(t1 + 1, sm, b),
            G.uline(t1 + 10, "after the compaction, what remains?", "u_%s_1" % tag, sm),
            G.aline(t1 + 20, "the cap and the retry budget remain", "a_%s_1" % tag, "u_%s_1" % tag, stop="end_turn"),
            G.uline(t1 + 30, "then close them out", "u_%s_2" % tag, "a_%s_1" % tag),
            G.aline(t1 + 40, "closing both", "a_%s_2" % tag, "u_%s_2" % tag, stop="end_turn")]
    return list(recs) + more


def _doc(path):
    """The leaf's assembly document, decoded (stored gzipped)."""
    import gzip
    return json.loads(gzip.decompress(em._asm_ckpt_file(path).read_bytes()))


def _write_doc(path, d):
    import gzip
    em._asm_ckpt_file(path).write_bytes(gzip.compress(json.dumps(d).encode("utf-8")))


def _strip(tree):
    """A tree as JSON compares it: lazy scalars dropped once hydrated (the whole parse never carries them); a restored
    tree's pre-cut turns are built into plain turns first (em.plain_tree, T323 stage 4c)."""
    t = json.loads(json.dumps(em.plain_tree(tree), default=lambda o: "<unserializable>"))
    t.pop("cutTurn", None)                                  # where the lazy atoms ended: a restored tree's own fact (stage 4b)
    for turn in t["turns"]:
        for a in turn["atoms"]:
            a.pop("lazy", None)
    return t


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()   # the parse is handed the REAL path: the reader's record-cache key must match the
        #                                                 assembly cache's realpath key (a symlinked temp root on macOS kept them apart: the
        #                                                 converge pass saw noEntry for every leaf, 2026-09-16)
        self.ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: self.ck)
        self.states, self.sent = None, []                       # what parse() hands the parser until write() sets them
        self._trees = {}                                         # path → the tree parse() last returned (doc() writes with it)
        self.fresh()
        em._ASM_CKPT_STATS.update(written=0, restored=0, fallbacks={}, skipped={}, hydratedBytes=0, hydratedAtoms=0, hydratedBy={})

    def tearDown(self):
        em.set_checkpoint_dir(None)
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side: every reader entry, assembly entry, hydrated body and pending restore gone."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
        em._LAZY_FILES.clear()
        memo = getattr(em, "_ASM_DOC_MEMO", None)          # the seeded walk's document memo (2026-09-15); getattr so a copy of
        if memo is not None:                               #  this file at an older base reds on behaviour, not on the name
            with em._ASM_CKPT_LOCK:
                memo.clear()
                getattr(em, "_ASM_DOC_MEMO_BYTES", [0])[0] = 0
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()

    def write(self, name, records, states=None, sent=None):
        d = self.td / name                                      # a directory per scenario: no stale document at a reused path
        d.mkdir(exist_ok=True)
        p = d / (SID + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.states = G.IDLE_STATES if name == "idle_atom" else states
        self.sent = sent or []
        return str(p)

    def parse(self, path, modes=None):
        tree = em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=self.states, postal_log=self.sent, now=NOW, asm_mode_out=modes)
        self._trees[path] = tree
        return tree

    def doc(self, path):
        """The leaf's document, written from its whole entry with the tree the last parse() of that path returned (the kernel
        hands the store's live tree, which gives the document its turns section: T323 stage 4c). A test that parsed the
        path through em.parse_session itself gets a document with no turns section (the atoms-only form)."""
        return em.asm_checkpoint_write(path, SID, tree=self._trees.get(path))

    def cold(self, path):
        self.fresh()
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None
        try:
            return _strip(self.parse(path))
        finally:
            em._CKPT_DIR_FN = saved

    def restored(self, path):
        """A fresh process parsing with the document in place: the tree hydrated, its mode, and the lazy count; the bytes
        the leaf cost BEFORE hydration are kept in self.read_before."""
        self.fresh()
        modes = []
        tree = self.parse(path, modes)
        self.read_before = em.read_bytes_report().get(path, 0)
        n_lazy = sum(1 for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None)
        em.hydrate(tree, SID)
        return _strip(tree), modes, n_lazy

    @staticmethod
    def after(records, dt):
        """A time past every stamp in `records` by `dt` seconds (an append must not regress the fold's timestamp gate)."""
        return max(em.parse_z(r.get("timestamp")) or 0 for r in records if r.get("timestamp")) + dt


_KM = []


def kernel_module():
    """The kernel (and through it the judge, km.jd) loaded ONCE for this module: the judge module is one object for the
    whole test process, so a second load_source of it re-executes it under every test that already holds it."""
    if not _KM:
        os.environ.setdefault("ROMP_KERNEL_NO_OPEN", "1")
        _KM.append(load_source("romp_kernel_t323s4a", os.path.join(BIN, "romp-kernel")))
    return _KM[0]


class StringRowsAndRestoreSplit(Harness):
    """T401 (4): the document's atom rows are stored as pre-serialized JSON strings (version 6), so the whole-document loads
    builds strs and the lazy index takes bytes with no dumps; a version-6 document whose row is not a string is refused as
    `rows`, never read by a second road; the previous version is refused as `version` and the next settle writes version 6
    (the deploy boot is the migration); the restore's four parts are timed on the returns they name."""

    def _compacting(self, name="manual_compact_detached"):
        records, sent = G.SINGLE_FILE[name]
        path = self.write(name, records(), sent=sent)
        self.fresh(); self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        return path

    def test_a_written_document_is_version_9_with_string_rows_and_restores_equal(self):
        path = self._compacting()
        d = _doc(path)
        self.assertEqual(d["av"], 9, "version 9: the resumed-fork root stitch (version 8: the parallel tool batch keep)")
        self.assertGreater(len(d["atoms"]), 0)
        self.assertTrue(all(isinstance(r, str) for r in d["atoms"]), "every atom row is a JSON string")
        self.assertTrue(all(isinstance(json.loads(r), dict) for r in d["atoms"]), "each decodes to the row it was")
        whole = self.cold(path)
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"]); self.assertGreater(n_lazy, 0)
        self.assertEqual(got, whole, "restored equals the whole parse over string rows")

    def test_a_version_6_document_with_a_dict_row_is_refused_as_rows_and_the_whole_parse_serves(self):
        path = self._compacting()
        d = _doc(path)
        d["atoms"][0] = json.loads(d["atoms"][0])                 # one row left as a dict: not this version's document
        _write_doc(path, d)
        em._ASM_CKPT_STATS["fallbacks"] = {}
        self.fresh(); modes = []; tree = self.parse(path, modes)
        self.assertEqual(modes, ["full"], "the whole parse serves")
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "counted once under `rows`: %s" % em.asm_checkpoint_stats()["fallbacks"])
        self.assertEqual(_strip(tree), self.cold(path))

    def test_the_previous_version_is_refused_once_and_the_next_settle_writes_version_9(self):
        # the deploy boot of the resumed-fork root stitch (the batch keep's was the same road): every version 8 document
        # (stored verdicts that filed a resumed fork's pre-cut history as cleared) is refused ONCE under `version` and the
        # settle that follows the whole parse writes the version 9 document; the boot after restores
        path = self._compacting()
        d = _doc(path)
        d["av"] = 8                                                # the previous version's document: the old verdicts
        _write_doc(path, d)
        em._ASM_CKPT_STATS["fallbacks"] = {}
        self.fresh(); modes = []; self.parse(path, modes)
        self.assertEqual(modes, ["full"]); self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("version"), 1, "the migration boot's road")
        self.assertTrue(self.doc(path), "the settle rewrites it")
        self.assertEqual(_doc(path)["av"], 9)
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"])
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("version"), 1, "refused once, never again")

    def test_the_restore_split_reports_every_part_above_zero_after_one_fast_restore(self):
        """1606 low 3 and 1610 round two, medium 2: whole milliseconds, then tenths, reported zeros for the fast parts of one
        restore (raw 0.0136, 0.0146, 0.0175 ms), and the pin read the raw floats, not the report. The REPORT is pinned: three
        decimals, every part above zero after a single fast restore, a `total` on every return of _asm_restore that holds
        each part, and the report equal to the intended rounding of the raw sums."""
        with em._ASM_CKPT_LOCK:
            em._ASM_CKPT_STATS["restoreMs"] = {"load": 0.0, "verify": 0.0, "index": 0.0, "seed": 0.0, "total": 0.0}
        path = self._compacting()
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"])
        ms = em.asm_checkpoint_stats()["restoreMs"]
        self.assertEqual(set(ms), {"load", "verify", "index", "seed", "total"})
        self.assertTrue(all(isinstance(v, float) and v > 0.0 for v in ms.values()), "every part reads above zero on /perf: %r" % ms)
        with em._ASM_CKPT_LOCK:
            raw = dict(em._ASM_CKPT_STATS["restoreMs"])
        self.assertEqual(ms, {k: round(v, 3) for k, v in raw.items()}, "the report is the raw sums at three decimals")
        self.assertGreaterEqual(raw["total"], max(raw[k] for k in ("load", "verify", "index", "seed")), "the total holds each part")

    def test_a_corrupt_last_row_of_any_shape_is_refused_whole_and_the_whole_parse_serves(self):
        """1610 round two, medium 1: the rows guard decoded row 0 only, so a document whose LAST row was a bare string, a list, a
        number or not JSON took the restore road and raised at the consumer. Every row's shape is checked at load (cheaply:
        an object's braces); each variant is refused as `rows` and the parse serves cold-equal."""
        for bad in ('"a bare string"', "[1, 2]", "7", "not json at all", '{"r": 0'):
            with self.subTest(last_row=bad):
                path = self._compacting()
                d = _doc(path); d["atoms"][-1] = bad; _write_doc(path, d)
                em._ASM_CKPT_STATS["fallbacks"] = {}
                self.fresh(); modes = []; tree = self.parse(path, modes)
                self.assertEqual(modes, ["full"], "the whole parse serves")
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "counted once: %s" % em.asm_checkpoint_stats()["fallbacks"])
                self.assertEqual(_strip(tree), self.cold(path))

    def test_a_corrupt_row_refuses_the_document_on_every_accessor_road_and_the_next_parse_is_whole(self):
        """1610 round three, mediums 1 and 2: the belt covered LazyIndex.build only; text_flags and uuid_of (the orphan
        synthesis walks every pre-cut row through them) did a bare json.loads whose JSONDecodeError escaped uncounted with the
        document left on disk, so the leaf never parsed again; and user_facts returned None for the row, so the tally answered
        from one row fewer than the whole parse with nothing counted. One helper now serves every accessor: the first read on
        ANY road notes `rows` once, unlinks the document, drops the entry and raises; the next parse is whole and cold-equal."""
        km_ = kernel_module()
        roads = {
            "build": lambda la, k: la[k],
            "text_flags": lambda la, k: la._index.text_flags(la._rows[k]),
            "uuid_of": lambda la, k: la._index.uuid_of(la._rows[k]),
            "uuids": lambda la, k: la.uuids(),
            "user_facts (the tally)": lambda la, k: km_._interrupt_marks_facts([{"id": "t", "t": 0, "atoms": la}]),
        }
        for name, road in roads.items():
            with self.subTest(road=name):
                path = self._compacting()
                cold = self.cold(path)
                d = _doc(path); last = len(d["atoms"]) - 1; d["atoms"][last] = "{not json inside}"; _write_doc(path, d)
                em._ASM_CKPT_STATS["fallbacks"] = {}
                self.fresh(); modes = []; tree = self.parse(path, modes)
                self.assertEqual(modes, ["restore"], "the shape check passes: the restore serves")
                la = next(t["atoms"] for t in tree["turns"] if isinstance(t.get("atoms"), em.LazyAtoms) and last in t["atoms"]._rows)
                k = la._rows.index(last)
                with self.assertRaises(em.LazyIndexError):
                    road(la, k)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "noted once on the %s road" % name)
                self.assertFalse(em._asm_ckpt_file(path).exists(), "the document is unlinked")
                modes = []; tree2 = self.parse(path, modes)
                self.assertEqual(modes, ["full"], "the entry was dropped: the next parse is whole")
                self.assertEqual(_strip(tree2), cold)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "still counted once")

    def test_an_object_shaped_row_that_does_not_decode_is_noted_at_its_first_build_and_the_next_parse_is_whole(self):
        """1610 round two, medium 1, the residual: a row shaped as an object whose inside is not JSON passes the load's shape
        check; the first build notes the document `rows` once, drops its assembly entry and raises for that build alone; the
        next parse of the leaf is the whole parse, cold-equal, and the note counted once."""
        path = self._compacting()
        d = _doc(path); last = len(d["atoms"]) - 1; d["atoms"][last] = "{not json inside}"; _write_doc(path, d)
        em._ASM_CKPT_STATS["fallbacks"] = {}
        self.fresh(); modes = []; tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"], "the shape check passes: the restore serves")
        lazy = [t["atoms"] for t in tree["turns"] if isinstance(t.get("atoms"), em.LazyAtoms)]
        self.assertTrue(lazy)
        with self.assertRaises(em.LazyIndexError):
            for la in lazy:
                for i in range(len(la)):
                    la[i]                                                # the corrupt row's build raises, once, loudly
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "noted once at the first failing build")
        with self.assertRaises(em.LazyIndexError):
            for la in lazy:
                for i in range(len(la)):
                    la[i]                                                # a second consumer of the same index: raised again,
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "  but noted once")
        modes = []; tree2 = self.parse(path, modes)
        self.assertEqual(modes, ["full"], "the entry was dropped: the next parse is whole")
        self.assertEqual(_strip(tree2), self.cold(path))

    def test_a_version_6_document_whose_first_row_is_not_an_object_is_refused_as_rows(self):
        """1606 low 2: the rows guard checked the type only; a string row that decodes to a list took the restore road and
        raised at its first build, uncounted. One row is decoded at load."""
        path = self._compacting()
        d = _doc(path)
        d["atoms"][0] = "[1, 2]"                                   # a JSON string, not a JSON object
        _write_doc(path, d)
        em._ASM_CKPT_STATS["fallbacks"] = {}
        self.fresh(); modes = []; tree = self.parse(path, modes)
        self.assertEqual(modes, ["full"], "the whole parse serves")
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("rows"), 1, "counted once under `rows`")
        self.assertEqual(_strip(tree), self.cold(path))

    def test_an_unencodable_atom_row_is_the_counted_skip_not_a_raise(self):
        """1606 low 1: the per-row json.dumps sat above the writer's guard, so an unencodable row raised out of
        asm_checkpoint_write instead of the counted `unencodable` skip."""
        records, sent = G.SINGLE_FILE["manual_compact_detached"]
        path = self.write("unenc", records(), sent=sent)
        self.fresh(); self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        orig = em._atom_scalars
        with mock.patch.object(em, "_atom_scalars", lambda a: dict(orig(a), weird={1, 2})):   # a set: not JSON-encodable
            wrote = self.doc(path)
        self.assertFalse(wrote, "not written")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("unencodable"), 1, "counted: %s" % em.asm_checkpoint_stats()["skipped"])


class RestoredEqualsWhole(Harness):
    def test_every_compacting_golden_scenario_restores_identical(self):
        self.assertTrue(COMPACTING, "the golden set holds compaction scenarios")
        for name in COMPACTING:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                path = self.write(name, records(), sent=sent)
                whole = self.cold(path)
                self.fresh()
                self.parse(path)                                        # the whole parse the writer works from
                self.assertTrue(self.doc(path), "a document is written: %s" % em.asm_checkpoint_stats())
                doc = _doc(path)
                self.assertGreater(len(doc["atoms"]), 0, "the cut leaves atoms before it")
                got, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], "the assembly came from the document: %s" % em.asm_checkpoint_stats())
                self.assertGreater(n_lazy, 0, "the pre-cut atoms were lazy before hydration")
                self.assertEqual(got, whole, "restored and hydrated equals the whole parse")
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})
                size = os.path.getsize(path)
                self.assertLess(self.read_before, size, "the leaf was not read whole before hydration: %d of %d bytes" % (self.read_before, size))

    def test_every_golden_scenario_made_to_compact_restores_identical(self):
        """Review find (F): only the three natively compacting scenarios were restored. Every single-file golden scenario
        gets a compaction appended, so its atoms (forks, rewinds, a /clear, absorbed attachments, skill atoms, command
        output, postal authors, eclipsed and broken chains) are the pre-cut part restored from a document."""
        restored, skipped = [], {}
        for name in G.SINGLE_FILE:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                recs = compacting_variant(records(), name[:6])
                path = self.write("variant-" + name, recs, sent=sent)
                whole = self.cold(path)
                self.fresh(); self.parse(path)
                em._ASM_CKPT_STATS["skipped"] = {}
                wrote = self.doc(path)
                if not wrote:
                    skipped[name] = dict(em.asm_checkpoint_stats()["skipped"])
                    continue
                got, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], name)
                self.assertGreater(n_lazy, 0)
                self.assertEqual(got, whole, "restored and hydrated equals the whole parse: %s" % name)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})
                restored.append(name)
        self.assertEqual(sorted(restored), ["author_kinds", "broken_chain_kept", "clear_breaks_lineage", "compaction_atom",
                                            "compaction_broken_stitch", "eclipsed_branch_kept", "idle_atom", "manual_compact_detached",
                                            "multi_input_absorbed", "popall", "queued_new_turn", "retry_superseded", "rewind_off_path",
                                            "slash_command_turn"], "every single-file golden scenario, made to compact, writes and restores")
        self.assertEqual(skipped, {}, "none is unsplittable")

    def test_a_two_file_lineage_made_to_compact_restores_identical(self):
        """The resume-lineage scenario (two files, a recorded resume fork) with a compaction in the leaf: the prior file is
        wholly before the cut, witnessed by its stat and never read at restore."""
        d = self.td / "lineage"; d.mkdir()
        pa, pb = d / (G.FSID_A + ".jsonl"), d / (G.FSID_B + ".jsonl")
        recs_b = compacting_variant(G.scenario_resume_lineage_fileB(), "lin")
        pa.write_text("".join(json.dumps(r) + "\n" for r in G.scenario_resume_lineage_fileA()))
        pb.write_text("".join(json.dumps(r) + "\n" for r in recs_b))
        states = getattr(G, "RESUME_STATES", None)
        cands = [str(pa), str(pb)]

        def parse(modes=None):
            return em.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=cands, states=states,
                                    postal_log=[], now=NOW, asm_mode_out=modes)
        self.fresh(); saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
        try:
            whole = _strip(parse())
        finally:
            em._CKPT_DIR_FN = saved
        self.fresh(); parse()
        self.assertTrue(self.doc(str(pb)), em.asm_checkpoint_stats())
        doc = _doc(str(pb))
        self.assertIn(G.FSID_A, doc["files"])
        self.fresh(); modes = []
        tree = parse(modes)
        self.assertEqual(modes, ["restore"])
        self.assertTrue(doc["files"][G.FSID_A].get("skip"), "the prior file is wholly before the cut")
        self.assertEqual(em.read_bytes_report().get(str(pa), 0), 0, "a prior file wholly before the cut is never read at restore: %s" % em.read_bytes_report())
        em.hydrate(tree, SID)                                   # hydration reads its atoms' records, in the prior file too
        self.assertGreater(em.read_bytes_report().get(str(pa), 0), 0, "hydration seeks into the prior file for its atoms")
        self.assertEqual(_strip(tree), whole)
        os.utime(pa, (NOW, NOW))                                # the prior file's stat moves: the lineage witness fails
        self.fresh(); modes = []
        tree = parse(modes)
        self.assertEqual(modes, ["full"])
        self.assertIn("lineage", em.asm_checkpoint_stats()["fallbacks"])

    def test_a_resumed_fork_with_a_note_ahead_of_its_head_restores_identical(self):
        """A fresh fork whose file opens with a queued note ahead of the restart head, then compacts: the restore stitches
        the same root the whole parse's leaf walk does, so the pre-cut history and the note's clear read the same."""
        d = self.td / "note_fork"; d.mkdir()
        pa, pb = d / (G.FSID_A + ".jsonl"), d / (G.FSID_B + ".jsonl")
        note = {"type": "attachment", "timestamp": G.iso(G.T0 + 95), "uuid": "r-note", "parentUuid": None,
                "attachment": {"type": "queued_command", "prompt": "a queued note"}}
        head = [dict(r, parentUuid=None) if r["uuid"] == "u2" else r for r in G.scenario_resume_lineage_fileB()]
        pa.write_text("".join(json.dumps(r) + "\n" for r in G.scenario_resume_lineage_fileA()))
        pb.write_text("".join(json.dumps(r) + "\n" for r in compacting_variant([note] + head, "note")))
        states = [{"t": G.T0 + 90, "resumeFork": {"from": G.FSID_A, "to": G.FSID_B}}]
        cands = [str(pa), str(pb)]

        def parse(modes=None):
            return em.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=cands, states=states,
                                    postal_log=[], now=NOW, asm_mode_out=modes)
        self.fresh(); saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
        try:
            whole = _strip(parse())
        finally:
            em._CKPT_DIR_FN = saved
        self.assertIn("first ask before resume", json.dumps(whole), "the whole parse keeps the pre-cut history")
        self.fresh(); parse()
        self.assertTrue(self.doc(str(pb)), em.asm_checkpoint_stats())
        self.fresh(); modes = []
        tree = parse(modes)
        self.assertEqual(modes, ["restore"])
        em.hydrate(tree, SID)
        self.assertEqual(_strip(tree), whole)

    def test_appends_after_the_restore_fold_and_stay_equal(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = records()
        path = self.write("compaction_atom", recs)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        got, modes, _ = self.restored(path)
        last = recs[-1]
        t1 = self.after(recs, 100)
        more = [G.uline(t1, "and then the cap", "u_more", last.get("uuid")),
                G.aline(t1 + 10, "two minutes, as before", "a_more", "u_more", stop="end_turn")]
        with open(path, "a") as f:
            for r in more:
                f.write(json.dumps(r) + "\n")
        modes = []
        tree = self.parse(path, modes)
        em.hydrate(tree, SID)
        self.assertEqual(modes, ["fold"], "the appended records folded onto the restored entry")
        self.assertEqual(_strip(tree), self.cold(path))

    def test_a_compaction_after_the_document_demotes_to_a_whole_parse(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = records()
        path = self.write("compaction_atom", recs)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.restored(path)
        last = recs[-1]
        t1 = self.after(recs, 100)
        b = G.compact_line(t1, "b_new", last.get("uuid"))
        with open(path, "a") as f:
            f.write(json.dumps(b) + "\n")
            f.write(json.dumps(G.compact_summary_line(t1 + 1, "s_new", "b_new")) + "\n")
        modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["full"], "a new boundary in the tail demotes to a whole parse, as before")
        self.assertEqual(_strip(tree), self.cold(path))



def _ring_records():
    """A conversation whose resolved pre-cut graph holds a RING: the uuid u1 is reused by a later record whose parent is a2, so
    last-wins the leaf's chain reads a2 -> u2 -> a1 -> u1 -> a2 -> (revisit). Real transcripts do this (a reused uuid closing a
    ring 3 to 50 records long, 50 or more hops above the leaf; 34 of the 76 live transcripts over 10 MB on 2026-09-14). The
    compaction then cuts, and two turns follow."""
    t = NOW - 3600
    recs = [G.uline(t, "start the plan", "u1", None), G.aline(t + 5, "the plan has two steps", "a1", "u1", stop="end_turn"),
            G.uline(t + 10, "do the first", "u2", "a1"), G.aline(t + 15, "the first is done", "a2", "u2", stop="end_turn"),
            G.uline(t + 20, "start the plan", "u1", "a2")]                  # the REUSED uuid: last-wins, its parent closes the ring
    return compacting_variant(recs, "ring")


def _self_link_records():
    """A pre-cut record whose parentUuid is its own uuid: the parse resolves it as a root."""
    t = NOW - 3600
    recs = [G.uline(t, "a self-linked opener", "u1", "u1"), G.aline(t + 5, "answered anyway", "a1", "u1", stop="end_turn"),
            G.uline(t + 10, "and on", "u2", "a1"), G.aline(t + 15, "onward", "a2", "u2", stop="end_turn")]
    return compacting_variant(recs, "self")


class CyclesInThePreCutGraph(Harness):
    """Stage one of the process split (2026-09-14, plans/checkpoint-cycle-walk.md): the writer's pre-cut spine walk ends at the
    first revisit as the parse's active_path does, so a ring in the resolved graph no longer refuses the whole document and the
    document's spine is the spine the chat shows; the restore's tail proof (T402 round eight) still refuses a tail that
    re-roots the graph."""

    def _round_trip(self, name, recs):
        path = self.write(name, recs)
        whole = self.cold(path)
        self.fresh(); self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        return path, whole, wrote

    def test_a_ring_in_the_pre_cut_graph_writes_a_document_that_restores_to_the_live_parses_world(self):
        path, whole, wrote = self._round_trip("ring", _ring_records())
        self.assertTrue(wrote, "a resolved cycle no longer refuses the document: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertNotIn("cycle", em.asm_checkpoint_stats()["skipped"])
        doc = _doc(path)
        self.assertGreater(len(doc["spine"]), 0, "the spine is the chain up to the first revisit")
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"], em.asm_checkpoint_stats())
        self.assertGreater(n_lazy, 0)
        self.assertEqual(got, whole, "restored and hydrated equals the whole parse: the writer walked the ring as active_path does")
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})

    def test_a_tail_re_rooted_onto_the_ring_child_refuses_the_standing_document_and_the_cold_parse_rules(self):
        # the tip's fork guard (tipChildless, T402 round five) excludes a child of the tip that is ON the spine, the ring's own
        # member, so the ring's document is written and restores. The guard's worry, a tail re-rooting onto that child: the
        # tail then leaves the tail into the pre-cut interior, the restore's reachability proof refuses the standing document,
        # and the parse walks the file cold (whose spine now bypasses the boundary: no document by design, noBoundary)
        path, whole, wrote = self._round_trip("ring-rerooted", _ring_records())
        self.assertTrue(wrote); self.assertTrue(_doc(path).get("tipChildless"), "the ring child does not make the tip a fork")
        extra = [G.uline(NOW + 200, "re-rooted onto the ring's child", "u_late", "a1"),
                 G.aline(NOW + 205, "answered from there", "a_late", "u_late", stop="end_turn")]
        pp = Path(path); pp.write_text(pp.read_text() + "".join(json.dumps(r) + "\n" for r in extra))
        cold = self.cold(path)
        self.fresh(); modes = []
        got = _strip(self.parse(path, modes))
        self.assertNotEqual(modes, ["restore"], "the standing document is refused: the tail left into the pre-cut interior (%s)" % em.asm_checkpoint_stats())
        self.assertEqual(got, cold, "the cold parse rules")
        # stage one b: the refusal's own whole parse offers a rewrite over the RE-ROOTED spine (a1, the ring's child, is the first
        # pre-cut record on the leaf's path). The only settled cut there leaves a1 pre-cut with its parent u1 resolved (last-wins)
        # to the tail's reused record: a ring ACROSS the cut, which a restore cannot rebuild (the oracle found the restored world
        # differing from the cold parse). The writer refuses such a cut (the pre-cut part must be closed under parents), and with
        # no earlier cut the leaf has no document, as before stage one b (then: noBoundary)
        self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path), "no document: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reuse": 1},
                         "the reused uuid u1 spans every candidate cut (its first copy is the file's first record): the named refusal")
        self.fresh(); modes = []
        self.assertEqual(_strip(self.parse(path, modes)), cold); self.assertNotEqual(modes, ["restore"])

    def test_a_self_linked_pre_cut_record_is_a_root_and_the_document_restores_identical(self):
        path, whole, wrote = self._round_trip("self-link", _self_link_records())
        self.assertTrue(wrote, em.asm_checkpoint_stats()["skipped"])
        got, modes, _n = self.restored(path)
        self.assertEqual((modes, got), (["restore"], whole), "a self-link is the root the parse makes of it, on both sides")

    def test_the_tail_shapes_that_re_root_the_graph_still_refuse_the_standing_document(self):
        # the controls (round eight of the checkpoint's reviews): the RESTORE's tail proof is untouched, so a tail record that
        # self-links or reuses a pre-cut uuid still refuses the document and the parse walks the file cold
        base = compacting_variant([G.uline(NOW - 3600, "hello", "u1", None), G.aline(NOW - 3595, "hi", "a1", "u1", stop="end_turn")], "ctl")
        for label, extra in (("self-link", [G.uline(NOW + 100, "a self-linked tail record", "u9", "u9")]),
                             ("reuse", [G.uline(NOW + 100, "reusing a pre-cut uuid in the tail", "u1", "a_ctl_2")])):
            with self.subTest(shape=label):
                name = "ctl-" + label
                path, whole, wrote = self._round_trip(name, base)
                self.assertTrue(wrote)
                p = Path(path)
                p.write_text(p.read_text() + "".join(json.dumps(r) + "\n" for r in extra))     # the tail grows the shape
                self.fresh()
                modes = []
                self.parse(path, modes)
                self.assertNotEqual(modes, ["restore"], "%s: the standing document is refused at restore, the parse walks cold (%s)"
                                    % (label, em.asm_checkpoint_stats()))

    def test_the_cycle_reason_is_gone_from_the_writer(self):
        import inspect
        src = inspect.getsource(em.asm_checkpoint_write)
        self.assertNotIn('skip("cycle")', src, "no document is refused for a cycle any more")
        self.assertIn("u not in seen", src, "the walk ends at the first revisit (a visited set, as active_path)")
        self.assertNotIn("cycle", em._ASM_SKIP_STRUCTURAL)

class Fallbacks(Harness):
    def _armed(self, tag):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("compaction_atom-" + tag, records())
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        return path

    def test_each_reason_falls_back_to_a_whole_parse_and_is_counted(self):
        for reason, spoil in (
            ("version", lambda p: _write_doc(p, dict(_doc(p), av=99))),
            ("session", lambda p: _write_doc(p, dict(_doc(p), rompuuid="other"))),
            ("corrupt", lambda p: em._asm_ckpt_file(p).write_bytes(b"{nope")),
            ("guard", lambda p: self._rewrite_prefix(p)),
            ("identity", lambda p: self._spoil_identity(p)),
            ("shrunk", lambda p: self._shrink(p)),
            ("rewrite", lambda p: self._same_size_new_mtime(p)),
            ("inputs", lambda p: _write_doc(p, dict(_doc(p), cands=["/elsewhere/other.jsonl"]))),
            ("restore", lambda p: _write_doc(p, dict(_doc(p), records=[["bad"]]))),
        ):
            with self.subTest(reason=reason):
                path = self._armed(reason)
                em._ASM_CKPT_STATS["fallbacks"] = {}
                spoil(path)
                whole = self.cold(path)
                got, modes, _ = self.restored(path)
                self.assertEqual(modes, ["full"], reason)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {reason: 1}, reason)
                self.assertEqual(got, whole)
                self.assertFalse(em._asm_ckpt_file(path).exists(), "the document that did not verify is gone")

    def _rewrite_prefix(self, path):
        """A rewrite under the cut's guard: the last pre-cut line changed and the file grown past its recorded size, so
        the size check passes and the guard bytes are what catch it."""
        lines = open(path).read().splitlines(keepends=True)
        doc = _doc(path)
        pre_n = doc["files"][SID]["cut"][1]
        r = json.loads(lines[pre_n - 1]); r["message"] = {"role": r["message"].get("role", "user"), "content": "REWRITTEN under the guard " + "x" * 400}
        lines[pre_n - 1] = json.dumps(r) + "\n"                 # longer than before, so the size check passes and the guard decides
        lines.append(json.dumps(G.uline(self.after([json.loads(x) for x in lines], 100), "appended after the rewrite", "u_rw", None)) + "\n")
        with open(path, "w") as f:
            f.writelines(lines)

    def _shrink(self, path):
        cut_off = _doc(path)["files"][SID]["cut"][0]              # the file ends before the recorded cut: a shrink
        data = open(path, "rb").read()
        open(path, "wb").write(data[:max(0, cut_off - 1)])

    def _same_size_new_mtime(self, path):
        data = open(path, "rb").read()
        open(path, "wb").write(data)
        os.utime(path, (NOW + 7, NOW + 7))

    def test_the_write_valves_are_counted(self):
        """Review find (K): a document past the cap is not written; a tree whose chronological order the cut cannot split
        (a pre-cut record stamped after the tail) is not written; both counted, and the session parses whole."""
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("valves", records())
        self.fresh(); self.parse(path)
        saved = em._ASM_CKPT_CAP
        em._ASM_CKPT_CAP = 10
        try:
            self.assertFalse(self.doc(path))
        finally:
            em._ASM_CKPT_CAP = saved
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("oversize"), 1)
        self.assertFalse(em._asm_ckpt_file(path).exists())
        recs = records()
        first = recs[0]; first["timestamp"] = em.datetime.fromtimestamp(G.T0 + 10 ** 6, em.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if hasattr(em, "timezone") else "2099-01-01T00:00:00.000Z"
        path2 = self.write("valves-order", recs)
        self.fresh(); self.parse(path2)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path2)
        self.assertFalse(wrote, "a pre-cut record stamped after every tail record cannot be split off")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"unsplittable": 1})

    def _spoil_identity(self, path):
        _write_doc(path, dict(_doc(path), identity="0" * 40))


class SkillLoadCarry(Harness):
    def test_a_pre_cut_skill_load_is_reported_from_a_restored_tree(self):
        """The harness's own skill load (T333: a bare-named <skill-format> wrapper the emit skips, reported as skillLoads for
        the judges' anchor stamp) before the cut rides the document's carry: the restored tree reports it as the whole
        parse does, with no body read."""
        skill = "notes-review"
        wrapper = ("<command-message>%s</command-message>\n<command-name>%s</command-name>\n"
                   "<skill-format>true</skill-format>" % (skill, skill))
        t0 = NOW - 7200
        recs = [G.uline(t0, "Add retries to the notes-api client", "u1", None),
                dict(G.uline(t0 + 1, wrapper, "u2", "u1"), isMeta=True),
                dict(G.uline(t0 + 1, "# Notes review reference\n\nHow to review notes.", "u3", "u2"), isMeta=True),
                G.aline(t0 + 60, "Adding the retry loop to the client.", "a1", "u3", stop="tool_use"),
                G.aline(t0 + 400, "Wrote the retry loop with a test.", "a2", "a1", stop="end_turn")]
        path = self.write("skill-load", compacting_variant(recs, "skl"))
        whole = self.cold(path)
        self.assertEqual(whole["skillLoads"], {"u2": skill}, "the whole parse reports the wrapper")
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.fresh(); modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"])
        self.assertEqual(tree["skillLoads"], {"u2": skill}, "restored from the carry, before any hydration")
        em.hydrate(tree, SID)
        self.assertEqual(_strip(tree), whole)


class WriteValves(Harness):
    def _whole(self, name="valve"):
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write(name, records(), sent=sent)
        self.fresh(); self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        return path

    def test_a_reconstruction_that_would_not_reproduce_the_ids_writes_nothing(self):
        """Review find (E): the identity is the whole parse's; a document whose lazy reconstruction would not reproduce
        it is not written, counted. Driven by answering the reconstruction's hash differently from the whole's."""
        path = self._whole()
        real, calls = em._pre_tree_identity, []
        def identity(atoms, rompuuid):
            calls.append(len(atoms))
            h = real(atoms, rompuuid)
            return h if len(calls) == 1 else "0" * len(h)          # the whole's hash, then a reconstruction that differs
        em._pre_tree_identity = identity
        try:
            self.assertFalse(self.doc(path))
        finally:
            em._pre_tree_identity = real
        self.assertEqual(len(calls), 2, "the whole parse's tree and the reconstruction were both hashed")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reconstruction": 1})
        self.assertFalse(em._asm_ckpt_file(path).exists(), "no document")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reconstruction": 1})
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path), "the failure is memoized for this entry and cut")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reconstruction": 1}, "counted again, nothing rebuilt")
        self.assertEqual(em._ASM_CACHE[next(iter(em._ASM_CACHE))].get("docSkip", (None, None))[1], "reconstruction")

    def test_a_transient_failure_is_not_memoized_against_the_cut(self):
        """Review find (third round): a stat, offsets or write failure is a blip, not a property of the cut; memoizing it left
        the session without a document until its next compaction. It is counted and tried again at the next settle."""
        path = self._whole("blip")
        real = em._entry_offsets_gen
        em._entry_offsets_gen = lambda fp: (None, None)              # a record landing between the parse and the offsets (the
        try:                                                        #  writer's one entry read, T396)
            self.assertFalse(self.doc(path))
        finally:
            em._entry_offsets_gen = real
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"offsets": 1})
        self.assertIsNone(em._ASM_CACHE[next(iter(em._ASM_CACHE))].get("docSkip"), "not memoized")
        self.assertTrue(self.doc(path), "the next settle writes")

    def test_a_whole_entry_writes_its_document_once(self):
        """Review find (H): a fold appends after the cut and changes nothing before it, so the settles after the first
        write skip the build (`written`), until the entry is replaced."""
        path = self._whole("once")
        self.assertTrue(self.doc(path))
        st = em._asm_ckpt_file(path).stat()
        self.assertFalse(self.doc(path))
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"written": 1})
        self.assertEqual(em._asm_ckpt_file(path).stat().st_mtime_ns, st.st_mtime_ns, "the document was not rewritten")
        em._asm_ckpt_file(path).unlink()
        self.assertTrue(self.doc(path), "a missing document is written again from the same entry")


class PlannerOverRestored(Harness):
    """T377 (2026-09-12): the judges' planner computed every ended segment's unit text before any consumer checked placement,
    and every consumer skips placed units or reads keys only; over a restored tree that hydrated every pre-cut body from disk,
    1.06 GB per boot on the devbox (96 percent of the lazy index's atoms, in the judges' first pass). A unit the store already
    places is yielded with no text and nothing read; the rest read their text after the placement check, as before."""

    @classmethod
    def setUpClass(cls):
        cls.km = kernel_module(); cls.jd = cls.km.jd

    def _scalars(self, units):
        return [(u[0], u[1], u[2], u[4], u[5], u[6]) for u in units]     # id, phase, time, human, followup, trigger

    def test_placed_units_are_yielded_without_text_and_nothing_is_hydrated(self):
        jd = self.jd
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("planner", records(), sent=sent)
        self.fresh(); whole = self.parse(path); self.assertTrue(self.doc(path))
        empty = {"placements": {}, "nodes": {}, "seq": 0}
        ref = jd.plan_units(whole, empty)                              # the whole parse's units: the reference, text included
        self.assertTrue(ref and any(u[3] for u in ref), "the fixture yields units with text")
        placed = {"placements": {jd._unit_key(u[0], u[1]): "n1" for u in ref}, "nodes": {"n1": {"id": "n1"}}, "seq": 1}
        self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        got = jd.plan_units(tree, placed)
        self.assertEqual(self._scalars(got), self._scalars(ref), "the same units, keys, times and scalars as the whole parse's")
        self.assertTrue(all(u[3] is None and u[7] is None for u in got), "a placed unit carries no text and no quote: %r" % [(u[3], u[7]) for u in got][:3])
        self.assertTrue(any(u[7] for u in ref), "the reference carries quotes")
        st = em.asm_checkpoint_stats()
        nseg = sum(len(em.segments(t)) for t in tree["turns"])
        self.assertFalse(any(k.startswith(("_unit_text", "_prompt_text")) for k in st["hydratedBy"]), "no unit text read: %s" % st["hydratedBy"])
        self.assertLessEqual(st["hydratedAtoms"], nseg, "at most the trigger atom's text per segment (the shape checks, as before): %s" % st)

    def test_unplaced_units_read_their_text_as_before(self):
        jd = self.jd
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("planner2", records(), sent=sent)
        self.fresh(); whole = self.parse(path); self.assertTrue(self.doc(path))
        empty = {"placements": {}, "nodes": {}, "seq": 0}
        ref = jd.plan_units(whole, empty)
        self.fresh(); tree = self.parse(path)                          # restored: what a full hydration of the tree costs
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        em.hydrate(tree, SID); full = em.asm_checkpoint_stats()["hydratedBytes"]
        self.fresh(); tree = self.parse(path)
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        got = jd.plan_units(tree, empty)
        self.assertEqual(got, ref, "unplaced: byte-identical to the whole parse's units, text included")
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["hydratedBytes"], full, "unplaced units read what they always did: every pre-cut body of the units")
        self.assertTrue(any(k.startswith("_unit_text") for k in st["hydratedBy"]), "%s" % st["hydratedBy"])
class ConvergeAssembly(Harness):
    """T376 (2026-09-12): an idle session never settles, so its leaf never had an assembly document and the parse read it whole
    at every boot (31 of 60 leaves on the devbox, about 2.5 GB, the boot's remaining cost). The converge pass writes the
    document for a quiescent leaf from the whole assembly entry the boot's own parse built: no read of records (a stat per
    file and the cut's guard), charged to the cycle's byte budget, once; the next process restores it and reads the tail."""

    @classmethod
    def setUpClass(cls):
        cls.km = kernel_module()

    def setUp(self):
        super().setUp()
        em._ASM_CKPT_STATS["converge"] = {"writes": 0, "bytes": 0, "deferred": 0, "candidates": 0, "skipped": {}}
        with em._CKPT_LOCK:
            for k in em._CKPT_STATS["converge"]:                   # the fold half's counters too: the tests assert absolutes
                em._CKPT_STATS["converge"][k] = 0
        self.km._ASM_CONVERGE_DONE.clear(); self.km._ASM_CONVERGE_BLIP.clear(); self.km._ASM_CONVERGE_NOENTRY.clear()
        for name, val in (("CKPT_CONVERGE_MS", 150.0), ("CKPT_CONVERGE_BYTES", em._CKPT_CYCLE_CAP_DEFAULT), ("ASM_CONVERGE", True)):
            saved = getattr(self.km, name); setattr(self.km, name, val); self.addCleanup(setattr, self.km, name, saved)

    def idle_leaf(self, name="idle", scenario="compaction_atom", age=600, records=None):
        """A leaf idle past the reader's quiescence window, with no assembly document, parsed once by this process (the boot's
        read: a whole entry), registered as the only session."""
        import time
        if records is None:
            records, sent = G.SINGLE_FILE[scenario]
            records = records()
        else:
            sent = None
        path = self.write(name, records, sent=sent)
        old = time.time() - age; os.utime(path, (old, old))
        self.fresh(); self.parse(path)
        rows = [{"sid": SID, "path": path}]
        saved = self.km._sessions; self.km._sessions = lambda now, **kw: rows
        self.addCleanup(setattr, self.km, "_sessions", saved)
        self.assertFalse(em._asm_ckpt_file(path).exists())
        return path

    def cycle(self, now):
        self.km._begin_checkpoint_cycle()
        return self.km._converge_checkpoints(now)

    def test_the_pass_asks_the_reader_under_the_path_it_holds_when_the_root_crosses_a_symlink(self):
        """The assembly cache keys on the resolved path, the reader on the path as handed. A transcript root that crosses a
        symlink (a temp root under /var on macOS; a symlinked home or CLAUDE_CONFIG_DIR anywhere) made the pass enumerate the
        resolved path, ask the reader under it, find no entry, count noEntry every cycle and write no document, so the next
        boot paid the whole read T376 exists to avoid (the macOS triage of v0.16). The pass asks under the path the boot's
        parse handed, which is the reader's key; the document lands where the restore looks (keyed on the resolved path)."""
        import time
        records, sent = G.SINGLE_FILE["compaction_atom"]
        real = self.write("symlinked", records(), sent=sent)                 # the leaf at its physical place
        link = self.td / "alias"
        os.symlink(self.td / "symlinked", link)
        handed = str(link / (SID + ".jsonl"))                                 # the path the boot hands the parse
        self.assertNotEqual(handed, os.path.realpath(handed), "the premise: the handed path is not canonical")
        old = time.time() - 600; os.utime(real, (old, old))
        self.fresh(); self.parse(handed)                                      # the boot's whole read, under the handed path
        self.assertTrue(em.entry_whole_resident(handed), "the reader holds the leaf under the handed path")
        self.assertFalse(em.entry_whole_resident(real), "…and not under the resolved one")
        whole = em.asm_whole_entries()
        self.assertEqual([w[0] for w in whole], [handed], "the pass enumerates the path the reader holds: %r" % whole)
        saved = self.km._sessions; self.km._sessions = lambda now, **kw: []   # no discover row: the enumeration road alone
        self.addCleanup(setattr, self.km, "_sessions", saved)
        self.assertFalse(em._asm_ckpt_file(handed).exists())
        self.cycle(NOW + 600)
        cv = em.asm_checkpoint_stats()["converge"]
        self.assertEqual(cv["writes"], 1, "one document written from the boot's parse: %s" % cv)
        self.assertFalse(cv["skipped"].get("noEntry"), "nothing counted noEntry: %s" % cv)
        self.assertTrue(em._asm_ckpt_file(handed).exists(), "the document stands where the restore looks")
        self.assertEqual(em._asm_ckpt_file(handed), em._asm_ckpt_file(real), "one document, keyed on the resolved path")

    def test_the_pass_writes_an_idle_leafs_document_from_the_boots_parse_and_the_next_boot_reads_a_tail(self):
        path = self.idle_leaf()
        size = os.path.getsize(path); read0 = em.read_bytes_report().get(path, 0)
        self.cycle(NOW + 600)
        self.assertTrue(em._asm_ckpt_file(path).exists(), "written from the entry in hand")
        self.assertLess(em.read_bytes_report().get(path, 0) - read0, 512, "no read of records: the cut's guard alone")
        cv = em.asm_checkpoint_stats()["converge"]
        self.assertEqual((cv["writes"], cv["deferred"], cv["candidates"]), (1, 0, 1), "%s" % cv); self.assertGreater(cv["bytes"], 0)
        st = em._asm_ckpt_file(path).stat().st_mtime_ns
        self.cycle(NOW + 601); self.cycle(NOW + 602)
        cv = em.asm_checkpoint_stats()["converge"]
        self.assertEqual((cv["writes"], cv["candidates"]), (1, 1), "once: the document stands, the leaf is done: %s" % cv)
        self.assertEqual(em._asm_ckpt_file(path).stat().st_mtime_ns, st)
        whole = self.cold(path)
        tree, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"]); self.assertGreater(n_lazy, 0)
        self.assertLess(self.read_before, size, "the next boot reads the tail, not the whole leaf: %d of %d bytes" % (self.read_before, size))
        self.assertEqual(tree, whole)

    def test_the_dirty_leaf_boot_shape_writes_the_fold_document_and_the_assembly_document_in_one_pass(self):
        """Round one, medium: the ordinary boot shape is a dirty idle leaf (the judges' pass read it whole, its fold document lacks
        the pairing) with no assembly document. The fold half of the pass healed and primed it and its held quiescence drop
        POPPED the record entry; the assembly step then found the assembly entry but no record entry (_entry_offsets_gen (None, None)), a
        skipped write, retried once, abandoned. The assembly write now runs while the record entry is resident, inside the same
        hold, and the held drop is paid after it: one pop, both documents from the one read, and the next boot restores both."""
        path = self.idle_leaf("both")
        km = self.km; jd = km.jd
        size = os.path.getsize(path)
        jd._bg_scan(path)                                             # the boot's whole read by a fold: dirty, whole-resident
        self.assertTrue(em.entry_whole_resident(path))
        self.assertIn(path, em.checkpoint_converge_candidates(), "a fold candidate too")
        read0 = em.read_bytes_report().get(path, 0)
        self.cycle(NOW + 600)
        cv = em.checkpoint_stats()["converge"]; av = em.asm_checkpoint_stats()["converge"]
        self.assertEqual((cv["viaDrop"], cv["dropWrites"]), (1, 1), "the fold document, written at the held drop: %s" % cv)
        self.assertEqual((av["writes"], av["skipped"]), (1, {}), "the assembly document, written from the same read: %s" % av)
        self.assertTrue(em._asm_ckpt_file(path).exists())
        self.assertLess(em.read_bytes_report().get(path, 0) - read0, 1024, "no read of records for either")
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(path), "one pop, after both writes")
        self.fresh(); em.set_checkpoint_dir(lambda: self.ck)
        for c in list(em._FOLD_REG.values()): c.clear()
        before = em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0)
        modes = []; self.parse(path, modes); self.assertEqual(modes, ["restore"], "the assembly document restores")
        jd._bg_scan(path)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0), before + 1, "the fold document restores")
        self.assertLess(em.read_bytes_report().get(path, 0), size, "the next boot reads the tail")

    def test_a_leaf_older_than_the_discover_window_is_written_from_the_boots_parse(self):
        """T382: the step walked _sessions(now), the discover window's rows (48 hours), so an idle leaf older than that was never a
        candidate although the boot had parsed it (19 of the 25 boundary leaves without a document on the devbox, 20 to 714
        hours old). The candidates come from the assembly cache's whole unrestored entries, the parses the boot actually did,
        each with its sid and flag; every other guard stands."""
        path = self.idle_leaf("aged")
        self.km._sessions = lambda now, **kw: []                       # no row in the window: the session is too old for discover
        read0 = em.read_bytes_report().get(path, 0)
        self.cycle(NOW + 600)
        self.assertTrue(em._asm_ckpt_file(path).exists(), "written from the parse in the cache, whatever the session's age")
        av = em.asm_checkpoint_stats()["converge"]
        self.assertEqual((av["writes"], av["candidates"], av["deferred"]), (1, 1, 0), "%s" % av)
        self.assertLess(em.read_bytes_report().get(path, 0) - read0, 512, "no read of records")
        self.cycle(NOW + 601); self.cycle(NOW + 602)
        self.assertEqual(em.asm_checkpoint_stats()["converge"]["writes"], 1, "once")
        tree, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"])

    def _parse_as(self, path, human):
        return em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path], states=self.states,
                                postal_log=self.sent, now=NOW, sdk_human=human)

    def test_the_document_is_written_under_the_display_parses_flag_whichever_entry_the_cache_yields_first(self):
        """T382 review, medium: with two whole entries for one leaf (the judges' flag and the display's differ until the owner
        provider is wired), the step wrote under whichever entry the LRU order yielded first; under the wrong flag the display's
        next boot took a session fallback, deleted the document and read the leaf whole. The entry whose flag is the display
        parse's is chosen, in either order."""
        for order in ((False, True), (True, False)):
            with self.subTest(order=order):
                path = self.idle_leaf("flag%d%d" % order)
                self.fresh(); em.set_checkpoint_dir(lambda: self.ck)
                for c in list(em._FOLD_REG.values()): c.clear()
                self.km._ASM_CONVERGE_DONE.clear(); self.km._ASM_CONVERGE_NOENTRY.clear()
                for human in order:
                    self._parse_as(path, human)                        # two whole entries, the display's flag (False) first or second
                self.cycle(NOW + 600)
                self.assertTrue(em._asm_ckpt_file(path).exists())
                self.assertFalse(_doc(path)["sdkHuman"], "written under the display parse's flag")
                tree, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], "the display's next boot restores it: %s" % em.asm_checkpoint_stats()["fallbacks"])

    def test_a_leaf_parsed_only_under_the_other_flag_is_skipped_and_counted(self):
        path = self.idle_leaf("otherflag")
        self.fresh(); em.set_checkpoint_dir(lambda: self.ck)
        for c in list(em._FOLD_REG.values()): c.clear()
        self.km._ASM_CONVERGE_DONE.clear(); self.km._ASM_CONVERGE_NOENTRY.clear()
        self._parse_as(path, True)                                     # the judges' flag alone: a document the display would unlink
        for k in range(2):
            self.cycle(NOW + 600 + k)
        self.assertFalse(em._asm_ckpt_file(path).exists(), "no document the reader would delete")
        av = em.asm_checkpoint_stats()["converge"]
        self.assertEqual((av["writes"], av["skipped"].get("flagMismatch")), (0, 1), "skipped once, counted: %s" % av)

    def test_a_write_over_the_cycles_budget_is_deferred_to_the_next(self):
        path = self.idle_leaf("budget")
        self.km.CKPT_CONVERGE_BYTES = 1
        self.cycle(NOW + 600)
        self.assertFalse(em._asm_ckpt_file(path).exists())
        self.assertEqual(em.asm_checkpoint_stats()["converge"]["deferred"], 1)
        self.km.CKPT_CONVERGE_BYTES = em._CKPT_CYCLE_CAP_DEFAULT
        self.cycle(NOW + 601)
        self.assertTrue(em._asm_ckpt_file(path).exists())
        self.assertEqual(em.asm_checkpoint_stats()["converge"]["writes"], 1)

    def test_a_zero_byte_budget_turns_the_step_off_rather_than_deferring_forever(self):
        """Round one, low 2: with ROMP_CKPT_CONVERGE_MB=0 every attempt was deferred and candidates and deferred grew each cycle."""
        path = self.idle_leaf("zero")
        self.km.CKPT_CONVERGE_BYTES = 0
        for k in range(3):
            self.cycle(NOW + 600 + k)
        cv = em.asm_checkpoint_stats()["converge"]
        self.assertEqual((cv["candidates"], cv["deferred"], cv["writes"]), (0, 0, 0), "off, as the drop write is: %s" % cv)
        self.assertFalse(em._asm_ckpt_file(path).exists())

    def test_a_blip_is_tried_twice_then_done_and_the_writer_names_its_caller_once_per_leaf(self):
        """Round two, low 3: the writer returns its reason (a blip, here the record entry gone before the offsets are read,
        versus a property of the cut), the pass tries a blip twice for one file state and then leaves it, and the writer's blip
        line names its caller and is said once per leaf and reason."""
        path = self.idle_leaf("blip"); km = self.km
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(path, None)                        # the record entry gone: the writer has no offsets (a blip)
        err = io.StringIO(); saved = sys.stderr; sys.stderr = err
        try:
            reasons = []
            self.assertFalse(em.asm_checkpoint_write(path, SID, False, reason_out=reasons, who="converge pass"))
            self.assertFalse(em.asm_checkpoint_write(path, SID, False, reason_out=reasons, who="converge pass"))
        finally:
            sys.stderr = saved
        self.assertEqual(reasons, ["offsets", "offsets"], "the writer's own reason, per attempt")
        lines = [l for l in err.getvalue().splitlines() if "not written by the converge pass" in l]
        self.assertEqual(len(lines), 1, "the blip line names its caller and is said once per leaf: %r" % err.getvalue())
        # the pass: the blip's two tries, then done for this file state (the record entry must be resident for a write)
        st = km._stat_key_ns(path)
        self.assertFalse(km._converge_assembly_leaf(path, SID, time.monotonic()))
        self.assertEqual(km._ASM_CONVERGE_NOENTRY.get(path), st, "no record entry: nothing to write from, counted once, no try spent")
        with em._JSONL_CACHE_LOCK:                                 # the record entry back, but the offsets torn away at the write
            pass
        self.fresh(); em.set_checkpoint_dir(lambda: self.ck); self.parse(path)   # a whole record entry again
        real = em._entry_offsets_gen; em._entry_offsets_gen = lambda p: (None, None)   # the writer's one entry read (T396)
        self.addCleanup(setattr, em, "_entry_offsets_gen", real)
        self.assertFalse(km._converge_assembly_leaf(path, SID, time.monotonic()))
        self.assertEqual(km._ASM_CONVERGE_BLIP.get(path), st, "a blip: the first try spent, not done")
        self.assertFalse(km._converge_assembly_leaf(path, SID, time.monotonic()))
        self.assertEqual(km._ASM_CONVERGE_DONE.get(path), st, "the second try spent: done for this file state")
        self.assertEqual(em.asm_checkpoint_stats()["converge"]["skipped"].get("offsets"), 2)

    def test_the_done_tables_release_their_oldest_past_the_bound(self):
        """Round two, low 3: past the bound the oldest entry is released, never the table cleared."""
        table = {"k%d" % i: i for i in range(4100)}
        self.km._release_oldest(table)
        self.assertEqual(len(table), 4096); self.assertNotIn("k0", table); self.assertIn("k4099", table); self.assertNotIn("k3", table)

    def test_a_leaf_without_a_boundary_but_with_a_settled_turn_gets_a_document(self):
        # stage one b: the cut is the boundary before the last settled turn with a follower, so a leaf that never compacted
        # is documented too (before it, no boundary meant no document: `noBoundary`)
        path = self.idle_leaf("plain", scenario="author_kinds")     # three settled turns, no compaction
        self.assertFalse(any(r.get("subtype") == "compact_boundary" for r in G.SINGLE_FILE["author_kinds"][0]()))
        self.cycle(NOW + 600)
        cv = em.asm_checkpoint_stats()["converge"]
        self.assertTrue(em._asm_ckpt_file(path).exists(), "%s" % cv)
        self.assertEqual((cv["candidates"], cv["writes"]), (1, 1), "%s" % cv)

    def test_a_leaf_with_no_settled_turn_before_its_last_has_no_cut_and_is_looked_at_once(self):
        t = NOW - 3600
        recs = [G.uline(t, "one question", "u1", None), G.aline(t + 5, "one answer", "a1", "u1", stop="end_turn")]
        path = self.idle_leaf("oneturn", records=recs)
        for k in range(3):
            self.cycle(NOW + 600 + k)
        cv = em.asm_checkpoint_stats()["converge"]
        self.assertFalse(em._asm_ckpt_file(path).exists())
        self.assertEqual((cv["candidates"], cv["skipped"].get("noCut"), cv["writes"]), (1, 1, 0), "no cut, no document, said once: %s" % cv)

    def test_off_writes_nothing_and_a_live_leaf_is_left_to_the_settle(self):
        path = self.idle_leaf("off")
        self.km.ASM_CONVERGE = False
        self.cycle(NOW + 600)
        self.assertFalse(em._asm_ckpt_file(path).exists()); self.assertEqual(em.asm_checkpoint_stats()["converge"]["candidates"], 0)
        self.km.ASM_CONVERGE = True
        live = self.idle_leaf("live", age=0)                       # fresh: the settle's writer covers it
        self.cycle(NOW + 601)
        self.assertFalse(em._asm_ckpt_file(live).exists()); self.assertEqual(em.asm_checkpoint_stats()["converge"]["candidates"], 0)
        self.assertIn("converge", self.km._PERF_STATS.snapshot()["asmCheckpoint"], "the counters ride /perf")



def _turns_after(recs, tag, n, dt=100):
    """`n` settled turns chained onto the last record of `recs`, each stamped `dt` seconds apart past every stamp in `recs`."""
    t = max((em.parse_z(r.get("timestamp")) or 0) for r in recs if r.get("timestamp")) + dt
    out, parent = [], _last_uuid(recs)
    for k in range(n):
        u, a = "ut_%s_%d" % (tag, k), "at_%s_%d" % (tag, k)   # the `ut_`/`at_` prefixes: no collision with compacting_variant's `u_<tag>_n`
        out.append(G.uline(t + 2 * k * dt, "step %d of %s" % (k, tag), u, parent))
        out.append(G.aline(t + (2 * k + 1) * dt, "step %d of %s is done" % (k, tag), a, u, stop="end_turn"))
        parent = a
    return out


class SettledCut(Harness):
    """Stage one b (plans/checkpoint-settled-cut.md): the cut is the boundary before the last SETTLED turn that has a following
    turn, or the last compaction's turn, whichever is later. Every leaf with two settled turns gets a document, a compaction
    is no longer required, the tip is a regular record proven childless of pre-cut children by construction, and the tail
    proof at restore is the same reachability walk. The oracle: restore-then-hydrate equals the cold parse at every cut."""
    def _cut_turn(self, path):
        """The index of the turn the writer cuts before, from the tree the last parse returned, by the plan's rule."""
        turns = self._trees[path]["turns"]
        si = max((i for i in range(len(turns) - 1) if turns[i].get("ended")), default=None)
        return si

    def _written_and_equal(self, name, recs, sent=None):
        path = self.write(name, recs, sent=sent)
        whole = self.cold(path)
        self.fresh(); self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        return path, whole, wrote

    def test_every_golden_scenario_is_documented_when_a_settled_turn_precedes_its_last_and_restores_equal(self):
        """The oracle over the plain scenarios (no compaction appended): a document is written exactly when a settled turn with a
        following turn exists (the rule), the writer proves the tip childless, and restore-then-hydrate equals the cold parse."""
        restored, refused, undocumented = [], [], {}
        for name in G.SINGLE_FILE:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                path, whole, wrote = self._written_and_equal("plain-" + name, records(), sent=sent)
                ci = self._cut_turn(path)
                compacts = any(r.get("subtype") == "compact_boundary" for r in records())
                if ci is None and not compacts:
                    self.assertFalse(wrote); self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"noCut": 1}, name)
                    undocumented[name] = "noCut"; continue
                if ci == 0 and not compacts:
                    self.assertFalse(wrote); self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"unsplittable": 1},
                                                              "a cut before the first turn leaves nothing pre-cut: %s" % name)
                    undocumented[name] = "unsplittable"; continue
                self.assertTrue(wrote, "%s: %s" % (name, em.asm_checkpoint_stats()["skipped"]))
                self.assertIs(_doc(path).get("tipChildless"), True, "a settled-turn cut's tip is proven childless: %s" % name)
                self.fresh(); modes = []
                tree = self.parse(path, modes); em.hydrate(tree, SID)
                self.assertEqual(_strip(tree), whole, "the parse from the document, restored or refused, equals the whole parse: %s" % name)
                (restored if modes == ["restore"] else refused).append(name)
        self.assertEqual(sorted(undocumented), sorted(["multi_input_absorbed", "popall", "clear_breaks_lineage", "slash_command_turn", "retry_superseded",
                                                       "queued_new_turn", "idle_atom", "rewind_off_path", "eclipsed_branch_kept"]),
                         "one-turn and two-turn scenarios have no pre-cut part: %s" % undocumented)
        self.assertEqual(refused, ["broken_chain_kept"], "the one tail the proof cannot chain (a record parented on a uuid the file never wrote) "
                                                         "is refused and walked cold, as a re-rooted tail is today; the rest restore: %s" % refused)
        self.assertEqual(sorted(restored), sorted(["author_kinds", "compaction_atom", "compaction_broken_stitch", "manual_compact_detached"]), restored)

    def test_the_cut_moves_with_each_settled_turn_and_restores_equal_at_every_position(self):
        """The sweep over cut positions: a compacting scenario grows one settled turn at a time; at every settle (a fresh
        entry each time, so the churn bound does not hold the standing document) the document's cut is the boundary before
        the last settled turn with a follower, and restore-then-hydrate equals the cold parse at that position."""
        for name in ("compaction_atom", "manual_compact_detached"):
            records, sent = G.SINGLE_FILE[name]
            recs = list(records())
            cuts = []
            for k in range(1, 6):
                with self.subTest(scenario=name, turns_after=k):
                    recs = recs + _turns_after(recs, "%s%d" % (name[:4], k), 1)
                    path, whole, wrote = self._written_and_equal("sweep-%s-%d" % (name, k), recs, sent=sent)
                    self.assertTrue(wrote, em.asm_checkpoint_stats()["skipped"])
                    d = _doc(path)
                    cut_uuid = d["records"][len(d["records"]) - 1][0] if d.get("records") else None
                    tail_first = em.asm_checkpoint_stats()
                    cuts.append(len(d["records"]))
                    self.assertIs(d.get("tipChildless"), True)
                    got, modes, n_lazy = self.restored(path)
                    self.assertEqual(modes, ["restore"], "%s at %d: %s" % (name, k, em.asm_checkpoint_stats()))
                    self.assertEqual(got, whole)
            self.assertEqual(cuts, sorted(cuts), "the cut advances with the settled turns: %s" % cuts)
            self.assertGreater(cuts[-1], cuts[0], "the cut moved past the compaction as turns settled")

    def test_the_ring_fixture_at_a_cut_past_the_ring_restores_equal(self):
        recs = _ring_records() + _turns_after(_ring_records(), "past", 3)
        path, whole, wrote = self._written_and_equal("ring-past", recs)
        self.assertTrue(wrote, em.asm_checkpoint_stats()["skipped"])
        d = _doc(path)
        self.assertGreater(len(d["records"]), 5, "the ring and the compaction are pre-cut; the cut sits at a settled turn past them")
        got, modes, n_lazy = self.restored(path)
        self.assertEqual((modes, got), (["restore"], whole))

    def _documented_base(self, name, big=False):
        """A documented leaf with four settled turns after a compaction (the cut before the fourth), its cold parse, the records,
        and the tip's uuid. `big` makes the opener large enough that one more turn stays under the churn bound's share."""
        opener = [G.uline(NOW - 3600, "hello " * (2000 if big else 1), "u1", None), G.aline(NOW - 3595, "hi " * (4000 if big else 1), "a1", "u1", stop="end_turn")]
        base = compacting_variant(opener, name)
        recs = base + _turns_after(base, name, 2)
        path, whole, wrote = self._written_and_equal(name, recs)
        self.assertTrue(wrote, em.asm_checkpoint_stats()["skipped"])
        d = _doc(path)
        tip = d["records"][d["spine"][-1]][0]
        self.assertEqual(tip, "a_%s_2" % name, "the settled turns after the compaction are pre-cut and the tip is a regular record "
                                                "(before stage one b the cut was the boundary's turn and the tip the opener's answer)")
        return path, whole, recs, tip

    def test_each_adversarial_tail_shape_refuses_the_document_and_the_cold_parse_rules(self):
        """Correction 5: one shape per class the earlier rounds found, appended past a settled-turn cut. Each refuses the
        standing document (the tail leaves the tail's forest, or re-roots it) and restore-then-hydrate equals the cold parse.
        The control (a plain settled turn chained on the leaf) restores."""
        shapes = {
            "repeated uuid across the cut": lambda recs, tip: [G.uline(NOW + 900, "reusing a pre-cut uuid", "u1", _last_uuid(recs))],
            "self-link at the tip": lambda recs, tip: [G.uline(NOW + 900, "a self-linked record", "u_self", "u_self")],
            "rewind from the tail onto a pre-cut record": lambda recs, tip: [G.uline(NOW + 900, "rewound onto the opener", "u_rw", "a1"),
                                                                             G.aline(NOW + 905, "answered from there", "a_rw", "u_rw", stop="end_turn")],
            "a /clear fork in the tail": lambda recs, tip: [G.uline(NOW + 900, "a fresh root after /clear", "u_clear", None),
                                                            G.aline(NOW + 905, "starting over", "a_clear", "u_clear", stop="end_turn")],
            "a compaction boundary in the tail anchored on a pre-cut record": lambda recs, tip: [G.compact_line(NOW + 900, "b_pre", "a1"),
                                                                                                   G.compact_summary_line(NOW + 901, "s_pre", "b_pre")],
        }
        proven = []                                            # every shape's verdict, asserted OUTSIDE the subtests too
        for label, mk in list(shapes.items()) + [("control: a settled turn on the leaf", None)]:
            with self.subTest(shape=label):
                name = "adv-" + "".join(ch if ch.isalnum() else "-" for ch in label)[:40]
                path, whole, recs, tip = self._documented_base(name)
                extra = mk(recs, tip) if mk else _turns_after(recs, "ctl", 1)
                pp = Path(path); pp.write_text(pp.read_text() + "".join(json.dumps(r) + "\n" for r in extra))
                cold = self.cold(path)
                self.fresh(); modes = []
                tree = self.parse(path, modes)
                em.hydrate(tree, SID)
                if mk is None:
                    self.assertEqual(modes, ["restore"], "the control chains onto the tip: %s" % em.asm_checkpoint_stats())
                else:
                    self.assertNotEqual(modes, ["restore"], "the standing document is refused for %s: %s" % (label, em.asm_checkpoint_stats()))
                self.assertEqual(_strip(tree), cold, "restore-then-hydrate equals the cold parse: %s" % label)
                proven.append(label)
        self.assertEqual(len(proven), len(shapes) + 1, "every shape ran to its verdict over a settled-turn cut: %s" % proven)

    def test_the_standing_document_holds_until_the_tail_reaches_the_share_or_a_compaction_lands(self):
        """Correction 2, the churn bound: with the entry standing (no restart), a settled turn appended past the cut leaves
        the document as it is (`written`: the tail is under an eighth of the pre-cut bytes); once the tail past the standing
        cut reaches the share the settle rewrites the document with a later cut; a compaction landing past the cut rewrites
        at once."""
        base = compacting_variant([G.uline(NOW - 3600, "hello " * 2000, "u1", None), G.aline(NOW - 3595, "hi " * 4000, "a1", "u1", stop="end_turn")], "churn")
        recs = base + _turns_after(base, "churn", 2)
        path, whole, wrote = self._written_and_equal("churn", recs)
        self.assertTrue(wrote, em.asm_checkpoint_stats()["skipped"])
        d0 = _doc(path); n0 = len(d0["records"])
        pre = sum(int((f.get("cut") or [0])[0]) for f in d0["files"].values())
        # one small settled turn: under the share, the standing document stands
        recs = recs + _turns_after(recs, "small", 1)
        pp = Path(path); pp.write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path)); self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"written": 1}, "%s" % em.asm_checkpoint_stats()["skipped"])
        self.assertEqual(len(_doc(path)["records"]), n0, "the document is the one written before")
        # the tail grows to the share: the settle rewrites with a later cut
        tail = os.path.getsize(path) - pre
        k = 0
        for _k in range(400):                                  # loop-ok: bounded; the share is reached long before
            if tail * em._ASM_TAIL_SHARE >= pre:
                break
            recs = recs + _turns_after(recs, "grow%d" % k, 1); k += 1
            pp.write_text("".join(json.dumps(r) + "\n" for r in recs))
            tail = os.path.getsize(path) - pre
        self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(self.doc(path), "the tail reached the share: rewritten (%s)" % em.asm_checkpoint_stats()["skipped"])
        d1 = _doc(path)
        self.assertGreater(len(d1["records"]), n0, "the cut advanced")
        got, modes, _n = self.restored(path)
        self.assertEqual((modes, got), (["restore"], self.cold(path)))
        # a compaction landing past the cut rewrites at once, whatever the tail's share
        self.fresh(); self.parse(path); self.assertFalse(self.doc(path)); n1 = len(_doc(path)["records"])
        recs = compacting_variant(recs, "late")
        pp.write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(self.doc(path), "a compaction past the cut: rewritten (%s)" % em.asm_checkpoint_stats()["skipped"])
        self.assertGreater(len(_doc(path)["records"]), n1)
        got, modes, _n = self.restored(path)
        self.assertEqual((modes, got), (["restore"], self.cold(path)))

    def test_a_tail_that_never_settles_keeps_the_previous_cut_and_restores_equal(self):
        """The cut never chases a live turn: an open turn (a user record with no result) is the tail; the cut stands at the
        boundary before the last settled turn with a follower, the document stands, and the restore equals the cold parse."""
        path, whole, recs, tip = self._documented_base("open", big=True)
        rows0 = [row[0] for row in _doc(path)["records"]]; n0 = len(rows0)
        self.assertIn("a_open_2", rows0, "the settled turns after the compaction are pre-cut (before stage one b the cut was the boundary's turn)")
        self.assertEqual(tip, "a_open_2", "the tip is the record before the cut turn (the last settled turn with a follower): a regular record")
        extra = [G.uline(NOW + 900, "a question still being answered", "u_open", _last_uuid(recs))]
        pp = Path(path); pp.write_text(pp.read_text() + "".join(json.dumps(r) + "\n" for r in extra))
        self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path), "nothing rewritten while the turn is open: %s" % em.asm_checkpoint_stats()["skipped"])
        cold = self.cold(path)
        got, modes, _n = self.restored(path)
        self.assertEqual((modes, got), (["restore"], cold))
        self.assertEqual(len(_doc(path)["records"]), n0)
        # a restart with the open turn in place: the standing document restores (the open turn is its tail) and the restored
        # entry writes nothing (`restored`: the document stands until the fold's gate demotes it)
        self.fresh(); modes = []; self.parse(path, modes); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertEqual(modes, ["restore"]); self.assertFalse(self.doc(path)); self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"restored": 1})
        d = _doc(path)
        self.assertNotIn("u_open", {row[0] for row in d["records"]}, "the open turn is the tail, never pre-cut")

    def test_a_pre_cut_attachment_whose_uuid_a_tail_record_reuses_never_restores_without_its_atom(self):
        """Round two of stage one b, the medium (the verifier's shape): an absorbed ATTACHMENT record parented pre-cut whose uuid a
        later record reuses. At write time the reused copy is the one the parse indexes (last-wins), so the attachment was no row of
        the document, and neither guard saw it: the writer's closure rule read the kept set, the restore's reuse check read the rows;
        the document restored silently missing the attachment's atom, no counter, no refusal. Both guards read every pre-cut RECORD
        now. Two timings, at a settled cut and at a compaction cut: the reuse already in the file when the writer runs (the cut steps
        back before the attachment's first copy, so the pair is the tail and the restore equals the cold parse; when no cut is left
        the writer refuses under `reuse`); the reuse appended after the write (the restore's check over the rows and `xu` refuses
        the standing document, the offered rewrite meets the writer's guard). Every parse equals the cold parse."""
        t = NOW - 3600
        def opener(att_first):
            recs = [G.uline(t, "hello", "u1", None), G.aline(t + 5, "hi", "a1", "u1", stop="end_turn"),
                    G.attline(t + 6, "a queued prompt", "att_1", "a1"),           # an absorbed queued-command attachment, parented pre-cut,
                    #                                                                  nothing parents on it; the cold parse emits its atom
                    G.uline(t + 10, "second ask", "u2", "a1"), G.aline(t + 15, "second reply", "a2", "u2", stop="end_turn"),
                    G.uline(t + 20, "third ask", "u3", "a2"), G.aline(t + 25, "third reply", "a3", "u3", stop="end_turn")]
            if att_first:
                recs = [G.attline(t - 1, "a queued prompt", "att_1", None)] + recs[:2] + recs[3:]   # the attachment is the file's first record
            return recs
        def reuse(recs):
            return [G.uline(NOW + 900, "reusing the attachment's uuid in the tail", "att_1", _last_uuid(recs)),
                    G.aline(NOW + 905, "answered", "a_late", "att_1", stop="end_turn")]
        cuts = {"settled": lambda o: o + _turns_after(o, "att", 2), "compaction": lambda o: compacting_variant(o, "attc")}
        verdicts = []                                              # asserted OUTSIDE the subtests too
        for label, mk in cuts.items():
            with self.subTest(cut=label, timing="the reuse in the file at write time"):
                # nothing parents on the attachment, so the kept chain is whole and the old closure rule saw nothing; the reused copy
                # is the one the parse indexes (last-wins), so the attachment was no row and the old reuse check saw nothing either:
                # before the fix the document was written and restored WITHOUT the attachment's atom. Now every cut inside the
                # reuse's span is blocked and the cut falls before the attachment (a turn's bytes begin at the attachments spliced
                # before its prompt), so the attachment and its reuse are both the tail and the restore equals the cold parse
                recs = mk(opener(False)); recs = recs + reuse(recs)
                path = self.write("att-write-" + label, recs)
                cold = self.cold(path)
                self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
                self.assertTrue(self.doc(path), "the cut falls before the attachment: %s" % em.asm_checkpoint_stats()["skipped"])
                rows = [r[0] for r in _doc(path)["records"]]
                self.assertEqual(rows, ["u1", "a1"], "the attachment and its reuse are both the tail: %s" % rows)
                got, modes, _n = self.restored(path)
                self.assertEqual((modes, got), (["restore"], cold), "%s: restored equals the cold parse, the attachment's atom included" % label)
                verdicts.append(label + " at write")
            with self.subTest(cut=label, timing="the attachment the file's first record: the named refusal"):
                recs = mk(opener(True)); recs = recs + reuse(recs)
                path = self.write("att-first-" + label, recs)
                cold = self.cold(path)
                self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
                self.assertFalse(self.doc(path)); self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reuse": 1})
                self.fresh(); self.assertEqual(_strip(self.parse(path)), cold)
            with self.subTest(cut=label, timing="the reuse appended after the write"):
                recs = mk(opener(False))
                path = self.write("att-after-" + label, recs)
                self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
                self.assertTrue(self.doc(path), em.asm_checkpoint_stats()["skipped"])
                self.assertIn("att_1", [r[0] for r in _doc(path)["records"]], "the attachment is a row of the clean document")
                pp = Path(path); pp.write_text(pp.read_text() + "".join(json.dumps(r) + "\n" for r in reuse(recs)))
                cold = self.cold(path)
                self.fresh(); modes = []
                tree = self.parse(path, modes); em.hydrate(tree, SID)
                self.assertNotEqual(modes, ["restore"], "%s: the standing document is refused (the tail reuses a pre-cut uuid)" % label)
                self.assertEqual(_strip(tree), cold)
                self.fresh(); modes = []
                tree = self.parse(path, modes); em.hydrate(tree, SID)
                self.assertEqual(_strip(tree), cold, "%s: every later parse equals the cold parse too" % label)
                verdicts.append(label + " after write")
        self.assertEqual(len(verdicts), 4, "every timing at every cut kind ran to its verdict: %s" % verdicts)

    def test_a_refusal_mark_is_retired_with_the_rewrite_that_moves_the_cut(self):
        """Correction 3: a `refusedStanding` mark belongs to a cut; when a compaction lands and the rewrite moves the cut, the
        mark goes with the sidecar it stood in (its bytes kept as `<meta>.retired-<stamp>`), and the next restore proves the
        new document instead of standing down on the old mark."""
        # the shape: a turn parented on a uuid the file never wrote (the broken chain the parse keeps as its own turn) lands in
        # the tail of the settled cut, followed by a settled turn on the leaf; the proof cannot chain the orphan, the refusal's
        # whole parse offers a rewrite that reproduces the same cut (the last settled turn with a follower has not moved), and
        # the mark stands for the leaf's stat
        path, whole, recs, tip = self._documented_base("mark")
        extra = [G.uline(NOW + 900, "a record whose parent the file never wrote", "u_ghost", "ghost-missing-uuid"),
                 G.aline(NOW + 905, "answered anyway", "a_ghost", "u_ghost", stop="end_turn")]   # the last turn: the cut stays put
        recs = recs + extra
        pp = Path(path); pp.write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.fresh(); modes = []; self.parse(path, modes)
        self.assertNotEqual(modes, ["restore"]); self.assertTrue(em._asm_refusal_stands(path), "the mark stands: the rewrite reproduced the cut")
        meta = em._asm_ckpt_file(path).with_name(em._asm_ckpt_file(path).name + ".meta")
        self.assertIn("refused", json.loads(meta.read_text()))
        recs = recs + _turns_after(recs, "moved", 2)                 # two settled turns on the leaf: the cut moves past the orphan turn
        pp.write_text("".join(json.dumps(r) + "\n" for r in recs))
        em._ASM_CKPT_STATS["skipped"] = {}
        self.fresh(); modes = []; self.parse(path, modes)            # the leaf moved: the mark clears, the proof refuses the OLD document
        self.assertNotEqual(modes, ["restore"])                      #  once more (the orphan is still its tail), and the refusal's rewrite
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {})   #  moves the cut past the orphan turn: written, not marked
        self.assertNotIn("refused", json.loads(meta.read_text()), "the mark for the old cut is gone from the sidecar")
        retired = sorted(meta.parent.glob(meta.name + ".retired-*"))
        self.assertEqual(len(retired), 1, "the old sidecar's bytes are kept beside it: %s" % retired)
        self.assertIn("refused", json.loads(retired[0].read_text()))
        self.assertFalse(em._asm_refusal_stands(path))
        cold = self.cold(path)
        got, modes, _n = self.restored(path)
        self.assertEqual((modes, got), (["restore"], cold), "the next restore proves the new document: %s" % em.asm_checkpoint_stats())



class VersionOldMarksRetireAtTheSweep(Harness):
    """plans/checkpoint-mark-version-retirement.md (2026-09-15): a refusal mark belongs to the cut rule it was made under. The mark
    is read before the document, so a mark made against a version 6 document sent its idle leaf to the cold walk at every boot
    and the load's version check never reached it (sixteen leaves at the measurement boot after stage one b). The boot sweep
    retires the mark of a sidecar whose `av` is below the current version: the bytes kept as .meta.retired-<stamp>, the sidecar
    rewritten without the block, one count under removed["refusedMark:version"]; the next parse takes the version-refusal road
    once, the settle writes the current document, and the parse after that restores equal to the cold parse."""
    def _documented(self, name="vold"):
        records, sent = G.SINGLE_FILE["manual_compact_detached"]
        path = self.write(name, records(), sent=sent)
        self.fresh(); self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        cp = em._asm_ckpt_file(path)
        return path, cp, cp.with_name(cp.name + ".meta")

    def _mark(self, path, meta, av):
        """A sidecar rewritten by hand: the given `av` and a `refused` block at the leaf's current stat (the marked shape)."""
        d = json.loads(meta.read_text())
        st = em._asm_leaf_stat(path)
        d["refused"] = {"reason": "shape", "size": st[0], "mtime": st[1]}
        if av is None:
            d.pop("av", None)
        else:
            d["av"] = av
        meta.write_text(json.dumps(d))
        if av != em._ASM_CKPT_V:
            doc = _doc(path); doc["av"] = 6 if av is None else av        # the document itself is the older version too (the real shape)
            _write_doc(path, doc)
        self.assertTrue(em._asm_refusal_stands(path), "the mark stands for the leaf's stat")

    def test_a_version_6_mark_is_retired_at_the_sweep_and_the_next_settle_writes_the_current_document(self):
        path, cp, meta = self._documented()
        self._mark(path, meta, 6)
        em._ASM_CKPT_STATS["removed"] = {}; em._ASM_CKPT_STATS["fallbacks"] = {}
        em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"refusedMark:version": 1}, em.asm_checkpoint_stats()["removed"])
        self.assertTrue(cp.exists(), "the document itself stays")
        side = json.loads(meta.read_text())
        self.assertNotIn("refused", side); self.assertEqual(side["av"], 6, "the sidecar keeps its version, path, files and linked")
        self.assertEqual(sorted(side), ["av", "files", "linked", "path"])
        asides = sorted(meta.parent.glob(meta.name + ".retired-*"))
        self.assertEqual(len(asides), 1, asides)
        self.assertEqual(json.loads(asides[0].read_text())["refused"]["reason"], "shape", "the old bytes are kept beside the document")
        self.assertFalse(em._asm_refusal_stands(path), "the mark no longer stands")
        cold = self.cold(path)
        self.fresh(); modes = []; got = _strip(self.parse(path, modes))
        self.assertEqual(modes, ["full"]); self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("version"), 1, "the version-refusal road, once")
        self.assertEqual(got, cold)
        self.assertTrue(self.doc(path), "the settle writes the current document: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertEqual(_doc(path)["av"], em._ASM_CKPT_V)
        got, modes, _n = self.restored(path)
        self.assertEqual((modes, got), (["restore"], cold), "and the parse after that restores")
        em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"refusedMark:version": 1, "fallback:version": 1},
                         "nothing more to retire at the next boot; the version-6 document itself left under the load's version refusal")

    def test_a_sidecar_without_a_version_is_version_old_too(self):
        path, cp, meta = self._documented("noav")
        self._mark(path, meta, None)
        em._ASM_CKPT_STATS["removed"] = {}
        em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"refusedMark:version": 1})
        self.assertFalse(em._asm_refusal_stands(path)); self.assertNotIn("refused", json.loads(meta.read_text()))

    def test_a_current_version_mark_stands_and_a_version_old_sidecar_without_a_mark_is_untouched(self):
        path, cp, meta = self._documented("cur")
        self._mark(path, meta, em._ASM_CKPT_V)
        em._ASM_CKPT_STATS["removed"] = {}
        em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {}, "a mark made under the current rule stands")
        self.assertTrue(em._asm_refusal_stands(path)); self.assertEqual(list(meta.parent.glob(meta.name + ".retired-*")), [])
        path2, cp2, meta2 = self._documented("oldnomark")
        d = json.loads(meta2.read_text()); d["av"] = 6; meta2.write_text(json.dumps(d))
        before = meta2.read_bytes()
        em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {}, "no mark, nothing to retire")
        self.assertEqual(meta2.read_bytes(), before, "the sidecar is untouched"); self.assertEqual(list(meta2.parent.glob(meta2.name + ".retired-*")), [])

    def test_a_failed_sidecar_rewrite_leaves_the_document_and_the_mark_and_counts_its_own_reason(self):
        """The 1708 read, low 1: the retirement's sidecar rewrite sat inside the sweep loop's try, whose except reads "this
        document does not verify", so an OSError on the rewrite (a full disk, a read-only directory) UNLINKED the document, its
        sidecar and the aside just written, counted under `sweep`, and left the .meta.<pid>.tmp behind. The rewrite's errors
        stay in the retirement: the document and the mark stand, the tmp is removed, the failure is counted under its own
        reason and said once; the next boot retries."""
        path, cp, meta = self._documented("enospc")
        self._mark(path, meta, 6)
        before_doc, before_meta = cp.read_bytes(), meta.read_bytes()
        em._ASM_CKPT_STATS["removed"] = {}
        real_replace = em.os.replace
        def failing_replace(src, dst):
            if str(dst).endswith(".meta"):
                raise OSError(28, "No space left on device")
            return real_replace(src, dst)
        err = io.StringIO()
        with mock.patch.object(em.os, "replace", failing_replace), contextlib.redirect_stderr(err):
            em.checkpoint_sweep()
        self.assertTrue(cp.exists(), "the document stays"); self.assertEqual(cp.read_bytes(), before_doc, "…byte for byte")
        self.assertEqual(meta.read_bytes(), before_meta, "the sidecar stays with its mark")
        self.assertTrue(em._asm_refusal_stands(path), "the mark stands: the next boot retries")
        self.assertEqual(list(meta.parent.glob("*.tmp")), [], "no tmp left behind")
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"refusedMark:versionFailed": 1}, "counted under its own reason, nothing swept")
        self.assertIn("could not be retired", err.getvalue()); self.assertEqual(err.getvalue().count("could not be retired"), 1, "said once")
        em._ASM_CKPT_STATS["removed"] = {}
        with contextlib.redirect_stderr(io.StringIO()):
            em.checkpoint_sweep()                                        # the disk back: the retirement lands
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"refusedMark:version": 1})
        self.assertFalse(em._asm_refusal_stands(path)); self.assertTrue(cp.exists())

    def test_a_gone_document_leaves_with_its_retired_asides(self):
        path, cp, meta = self._documented("gone")
        self._mark(path, meta, 6)
        em.checkpoint_sweep()
        self.assertEqual(len(list(meta.parent.glob(meta.name + ".retired-*"))), 1)
        os.unlink(path)                                                  # the transcript is gone: the sweep removes the document
        em.checkpoint_sweep()
        self.assertFalse(cp.exists()); self.assertFalse(meta.exists()); self.assertEqual(list(meta.parent.glob(meta.name + ".retired-*")), [])



class LowsOfTheSettledCutReads(Harness):
    """The lows queued by the reads of stage one b, the mark retirement and the data-safety pair (2026-09-15)."""
    def _base(self, name, extra_turns=2, big=False):
        opener = [G.uline(NOW - 3600, "hello " * (2000 if big else 1), "u1", None), G.aline(NOW - 3595, "hi " * (4000 if big else 1), "a1", "u1", stop="end_turn")]
        recs = compacting_variant(opener, name) + _turns_after(compacting_variant(opener, name), name, extra_turns)
        path = self.write(name, recs)
        self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        return path, recs

    def test_the_young_session_floor_holds_the_first_document_and_never_a_standing_one(self):
        """The 1695 read, low 1: with uniform turns the churn bound alone is met at nearly every settle until the pre-cut part
        outgrows the two-to-three turn lag (27 rewrites over a young session's first 30 settled turns measured), so a session's
        FIRST document waits until its pre-cut part holds _ASM_FIRST_DOC_MIN bytes (an eighth of the fold cap, 1 MB live; the
        suite runs with the floor off); a standing document is never held by the floor."""
        saved = getattr(em, "_ASM_FIRST_DOC_MIN", None)              # reached with a default: the base red is the write below, not a name
        self.addCleanup(lambda: setattr(em, "_ASM_FIRST_DOC_MIN", saved) if saved is not None else delattr(em, "_ASM_FIRST_DOC_MIN"))
        path, recs = self._base("young")
        em._ASM_FIRST_DOC_MIN = 10 ** 9
        self.assertFalse(self.doc(path), "under the floor: no first document (the base wrote one)")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"young": 1}, "under the floor: no first document")
        self.assertIn("young", em._ASM_SKIP_STRUCTURAL, "structural: the memo re-arms as the cut moves")
        em._ASM_FIRST_DOC_MIN = 0
        self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(self.doc(path), "the floor off: written")
        n0 = len(_doc(path)["records"])
        em._ASM_FIRST_DOC_MIN = 10 ** 9                                   # a standing document is never held by the floor
        recs = recs + _turns_after(recs, "grow", 6)
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        self.assertTrue(wrote or em.asm_checkpoint_stats()["skipped"] == {"written": 1}, "the standing document is written on or stands: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertNotIn("young", em.asm_checkpoint_stats()["skipped"])
        if wrote:
            self.assertGreater(len(_doc(path)["records"]), n0, "the cut advanced under the churn bound, the floor silent")
        self.assertEqual(em._env_or("ROMP_CKPT_FIRST_DOC_KB", em._CKPT_FOLD_CAP // 8, 1024), 0, "the suite runs with the floor knob at 0 (conftest)")

    def test_a_pre_cut_record_whose_parent_points_forward_is_refused_under_closure(self):
        """The 1695 read, low 2: the closure guard had no failing test. A record on the kept chain whose parentUuid names a record
        the file writes only LATER closes a ring across every candidate cut (u1 parents on a_fwd, which the tail writes parented
        on the last reply): every cut before a_fwd leaves a pre-cut record whose parent resolves past it, every cut after it
        holds the reuse of nothing, so the guard refuses under its own name and the cold parse serves."""
        t = NOW - 3600
        recs = [G.uline(t, "opener parented forward", "u1", "a_fwd"), G.aline(t + 5, "reply", "a1", "u1", stop="end_turn"),
                G.uline(t + 10, "second", "u2", "a1"), G.aline(t + 15, "reply", "a2", "u2", stop="end_turn"),
                G.uline(t + 20, "third", "u3", "a2"), G.aline(t + 25, "reply", "a3", "u3", stop="end_turn"),
                G.uline(t + 30, "fourth", "u4", "a3"), G.aline(t + 35, "the forward record", "a_fwd", "u4", stop="end_turn")]
        path = self.write("closure", recs)
        cold = self.cold(path)
        self.fresh(); self.parse(path); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path)); self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"closure": 1}, "the named refusal")
        self.fresh(); modes = []
        self.assertEqual(_strip(self.parse(path, modes)), cold); self.assertEqual(modes, ["full"])

    def test_a_garbled_stamp_across_the_cut_steps_the_cut_back(self):
        """The 1695 read, low 3: the write-time stamp-order rule had no failing test. The last pre-cut reply stamped AFTER the
        tail's first record (a garbled clock) fails the chronological split at the settled cut, so the cut steps back to a turn
        whose split holds: the document's rows end before the garbled record, and the restore equals the cold parse."""
        path, recs = self._base("stamps", extra_turns=3)
        self.assertTrue(self.doc(path)); rows_clean = [r[0] for r in _doc(path)["records"]]
        garbled = list(recs)
        k = max(i for i, r in enumerate(garbled) if r.get("uuid") == rows_clean[-1])   # the clean cut's last pre-cut record
        garbled[k] = dict(garbled[k]); garbled[k]["timestamp"] = G.iso(NOW + 9999)     # stamped after every tail record
        path2 = self.write("stamps2", garbled)
        cold = self.cold(path2)
        self.fresh(); self.parse(path2); em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(self.doc(path2), em.asm_checkpoint_stats()["skipped"])
        rows = [r[0] for r in _doc(path2)["records"]]
        self.assertNotIn(rows_clean[-1], rows, "the garbled record is the tail's: the cut stepped back")
        self.assertLess(len(rows), len(rows_clean))
        got, modes, _n = self.restored(path2)
        self.assertEqual((modes, got), (["restore"], cold))

    def test_the_restores_reuse_check_reads_the_documents_xu_beside_its_rows(self):
        """The 1695 read, low 4: `xu` is empty under the guarded writer, so the restore's rows-plus-xu branch had no test. A
        hand-written document carrying a ghost uuid in `xu` refuses a tail that reuses it; the same tail over `xu` empty
        restores (the ghost was no row)."""
        for label, xu, expect_restore in (("xu names the ghost", ["ghost"], False), ("xu empty", [], True)):
            with self.subTest(case=label):
                path, recs = self._base("xu-" + label.split()[0] + label.split()[-1])
                self.assertTrue(self.doc(path))
                d = _doc(path); d["xu"] = xu; _write_doc(path, d)
                extra = [G.uline(NOW + 900, "a tail record under the ghost uuid", "ghost", _last_uuid(recs)),
                         G.aline(NOW + 905, "reply", "a_ghost", "ghost", stop="end_turn")]
                Path(path).write_text(Path(path).read_text() + "".join(json.dumps(r) + "\n" for r in extra))
                cold = self.cold(path)
                self.fresh(); modes = []
                tree = self.parse(path, modes); em.hydrate(tree, SID)
                self.assertEqual(modes == ["restore"], expect_restore, "%s: %s" % (label, em.asm_checkpoint_stats()))
                self.assertEqual(_strip(tree), cold)

    def _marked_v6(self, name):
        records, sent = G.SINGLE_FILE["manual_compact_detached"]
        path = self.write(name, records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        cp = em._asm_ckpt_file(path); meta = cp.with_name(cp.name + ".meta")
        d = json.loads(meta.read_text()); st = em._asm_leaf_stat(path)
        d["refused"] = {"reason": "shape", "size": st[0], "mtime": st[1]}; d["av"] = 6; meta.write_text(json.dumps(d))
        doc = _doc(path); doc["av"] = 6; _write_doc(path, doc)
        return path, cp, meta

    def test_a_retirement_that_keeps_failing_keeps_one_aside_not_one_per_retry(self):
        """The 1717 read, low 2: a failing rewrite retried at every sweep wrote a fresh .meta.retired-<stamp> each time."""
        path, cp, meta = self._marked_v6("dedupe")
        real_replace = em.os.replace
        def failing_replace(src, dst):
            if str(dst).endswith(".meta"):
                raise OSError(28, "No space left on device")
            return real_replace(src, dst)
        with mock.patch.object(em.os, "replace", failing_replace), contextlib.redirect_stderr(io.StringIO()):
            em.checkpoint_sweep(); em.checkpoint_sweep(); em.checkpoint_sweep()
        asides = list(meta.parent.glob(meta.name + ".retired-*"))
        self.assertEqual(len(asides), 1, "one copy of the mark, whatever the retries: %s" % asides)
        self.assertTrue(em._asm_refusal_stands(path))

    def test_an_aside_that_cannot_be_written_is_counted_and_said_and_the_retirement_proceeds(self):
        """The 1717 read, low 4: the aside write was best effort and silent."""
        path, cp, meta = self._marked_v6("aside")
        em._ASM_CKPT_STATS["removed"] = {}
        real_write = Path.write_bytes
        def failing_write(self_, data):
            if ".retired-" in self_.name:
                raise OSError(30, "Read-only file system")
            return real_write(self_, data)
        err = io.StringIO()
        with mock.patch.object(Path, "write_bytes", failing_write), contextlib.redirect_stderr(err):
            em.checkpoint_sweep()
        self.assertEqual(em.asm_checkpoint_stats()["removed"], {"refusedMark:asideFailed": 1, "refusedMark:version": 1}, "counted, and the retirement proceeded")
        self.assertIn("could not be kept aside", err.getvalue())
        self.assertFalse(em._asm_refusal_stands(path)); self.assertEqual(list(meta.parent.glob(meta.name + ".retired-*")), [])


class HydrationAttribution(Harness):
    def test_a_shared_text_reader_is_attributed_with_its_caller(self):
        """T377: the boot's 1.06 GB of hydration read as `_unit_text`, the judges' shared text reader, which every walker calls;
        the bytes are attributed to the reader AND its caller, so the counter names the walker."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("attr", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        tree, _, n_lazy = self.fresh(), None, None
        modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        atoms = [a for t in tree["turns"] for a in t["atoms"]]
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        def _unit_text(atoms):                                  # a stand-in for the judges' reader, registered like it
            return em.hydrate(atoms)
        em.register_hydrate_text_reader(_unit_text); self.addCleanup(em.unregister_hydrate_text_reader, _unit_text)
        def some_walker():
            return _unit_text(atoms)
        some_walker()
        by = em.asm_checkpoint_stats()["hydratedBy"]
        self.assertEqual(list(by), ["_unit_text<-some_walker"], "%s" % by)
        self.assertEqual(em.hydrate(atoms), 0, "hydrated already: nothing counted twice")
        self.fresh(); tree = self.parse(path); atoms = [a for t in tree["turns"] for a in t["atoms"]]
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        def _atom_text(atoms):                                  # a reader reached through another shared reader (round one, low 3)
            return em.hydrate(atoms)
        def _prompt_text(atoms):
            return _atom_text(atoms)
        em.register_hydrate_text_reader(_atom_text, _prompt_text)
        self.addCleanup(em.unregister_hydrate_text_reader, _atom_text, _prompt_text)
        def other_walker():
            return _prompt_text(atoms)
        other_walker()
        self.assertEqual(list(em.asm_checkpoint_stats()["hydratedBy"]), ["_atom_text<-other_walker"], "the first caller outside the shared readers")

    def test_the_shared_readers_are_matched_by_code_object_not_by_name(self):
        """Round three, low 2: the shared text readers were matched by NAME, so a local function named like one was consumed as
        the reader (the whole-read passthrough already matches by code object for the same reason). The real readers are
        registered where they are defined; a same-named local function is a walker, and the stand-ins the attribution tests use
        register themselves."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("byobj", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        atoms = [a for t in tree["turns"] for a in t["atoms"]]
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        def _atom_user_texts(a):                                # a walker that merely shares a shared reader's name
            return em.hydrate([a])
        def unrelated_walker():
            return [_atom_user_texts(a) for a in atoms]
        unrelated_walker()
        by = em.asm_checkpoint_stats()["hydratedBy"]
        self.assertEqual(list(by), ["_atom_user_texts"], "a same-named local function is the caller, not a shared reader: %s" % by)
        km = kernel_module()
        self.assertTrue(em.hydrate_text_reader_registered(km._atom_user_texts), "the kernel's reader is registered")
        self.assertTrue(all(em.hydrate_text_reader_registered(f) for f in (km.jd._unit_text, km.jd._prompt_text, km.jd._atom_text)),
                        "the judges' three readers are registered")

    def test_a_walk_from_inside_a_generator_expression_names_the_enclosing_function(self):
        """The 1e60c712 head went red on Python 3.10 and 3.11: a list comprehension there runs in its own frame (inlined from
        3.12 on, PEP 709), and the attribution named `<listcomp>`. A generator expression keeps its own frame on every version,
        so the first pin is red everywhere at that head; the comprehension pin is red on 3.10 and 3.11 and green above."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("scopes", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        def restored_atoms():
            self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
            em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
            return [a for t in tree["turns"] for a in t["atoms"]]
        atoms = restored_atoms()
        def genexpr_walker():
            return sum(em.hydrate([a]) for a in atoms)
        genexpr_walker()
        self.assertEqual(list(em.asm_checkpoint_stats()["hydratedBy"]), ["genexpr_walker"], "%s" % em.asm_checkpoint_stats()["hydratedBy"])

    def test_a_shared_reader_reached_from_a_list_comprehension_names_the_walker_around_it(self):
        """The comprehension case alone: red on Python 3.10 and 3.11 at 1e60c712 (`<listcomp>` has its own frame there), green
        from 3.12 on, where PEP 709 inlines it."""
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("listcomp", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        atoms = [a for t in tree["turns"] for a in t["atoms"]]
        def _atom_user_texts(a):                                # a stand-in shared reader reached from a comprehension inside a walker
            return em.hydrate([a])
        em.register_hydrate_text_reader(_atom_user_texts); self.addCleanup(em.unregister_hydrate_text_reader, _atom_user_texts)
        def listcomp_walker():
            return [_atom_user_texts(a) for a in atoms]
        listcomp_walker()
        self.assertEqual(list(em.asm_checkpoint_stats()["hydratedBy"]), ["_atom_user_texts<-listcomp_walker"], "%s" % em.asm_checkpoint_stats()["hydratedBy"])


class ReadersOverRestoredHydrateOnlyWhatTheyNeed(Harness):
    """T384 (2026-09-12): after the planner stopped hydrating every pre-cut body, the next walkers paid for the same bodies (the
    first T377 boot: _seg_prompt 723 MB, _seg_of_tool_uses 155 MB, _atom_user_texts 78 MB of 966). Each reads what it needs:
    the segment-prompt reader hydrates the one trigger atom, the tool-uses reader takes the ids from the lazy markers' scalars
    with no hydration, and the echo set reads no user text when no echo can land (an infinite floor). Per-reader bytes over a
    restored tree, on both builds."""

    @classmethod
    def setUpClass(cls):
        cls.km = kernel_module()

    def _restored(self, name):
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write(name, records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        return path, tree

    def test_the_segment_prompt_reader_hydrates_the_trigger_atom_alone(self):
        path, tree = self._restored("segprompt")
        segs = [seg for t in tree["turns"] for seg in em.segments(t)]
        pre = [seg for seg in segs if any(a.get("lazy") is not None for a in seg["atoms"])]
        self.assertTrue(pre, "pre-cut segments in play")
        prompts = [self.km._seg_prompt(seg) for seg in segs]
        st = em.asm_checkpoint_stats()
        self.assertLessEqual(st["hydratedAtoms"], len(pre), "one atom per pre-cut segment, the trigger: %s" % st)
        self.fresh(); whole = self.parse(path)
        self.assertEqual(prompts, [self.km._seg_prompt(seg) for t in whole["turns"] for seg in em.segments(t)], "the same prompts as the whole parse")

    def test_the_tool_uses_reader_takes_the_ids_from_the_markers_scalars(self):
        path, tree = self._restored("tooluses")
        whole_ids = {b.get("id") for t in self.cold(path)["turns"] for a in t["atoms"] if a.get("type") == "assistant"
                     for b in ((a.get("message") or {}).get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"}
        self.assertTrue(whole_ids, "the fixture has tool calls")
        self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        found = self.km._seg_of_tool_uses(tree, {"placements": {}, "nodes": {}, "seq": 0}, list(whole_ids))
        self.assertEqual(set(found), whole_ids, "every id resolved to its segment")
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "from the scalars, no body read: %s" % em.asm_checkpoint_stats()["hydratedBy"])

    def test_the_comments_frames_landing_walk_reads_no_user_text_below_the_oldest_echos_send(self):
        """The comments frame read every user atom of a thread's whole parse to test whether a live echo had landed (78 MB on
        the first T377 boot); a text lands at or after its send, so the atoms below the oldest echo's send are never read."""
        path, tree = self._restored("echoland")
        pre_users = [a for t in tree["turns"] for a in t["atoms"] if a.get("type") == "user" and a.get("lazy") is not None]
        self.assertTrue(pre_users, "pre-cut user atoms in play")
        newest = max(float(a.get("t") or 0) for t in tree["turns"] for a in t["atoms"] if a.get("t"))
        live = [{"_echo_text": "a typed-ahead line", "t": newest + 5}]   # an echo sent after every recorded atom
        rows = self.km._echo_landing_atoms(tree["turns"], live)
        self.assertEqual(rows, [], "no atom at or after the send: nothing to compare, nothing read")
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "%s" % em.asm_checkpoint_stats()["hydratedBy"])
        self.assertEqual(self.km._echo_landing_atoms(tree["turns"], []), [], "no live echo: no walk")
        live = [{"_echo_text": "an early line", "t": 0}]                 # an echo older than everything: every user atom is a candidate
        rows = self.km._echo_landing_atoms(tree["turns"], live)
        self.assertEqual(len(rows), sum(1 for t in tree["turns"] for a in t["atoms"] if a.get("type") == "user"))
        self.assertGreaterEqual(em.asm_checkpoint_stats()["hydratedAtoms"], len(pre_users), "below the floor they are read, as before")

    def test_a_marker_without_tool_use_scalars_is_an_answer_with_no_tool_call_and_hydrates_nothing(self):
        """Round two, medium: the writer records `tu` only when an atom has tool calls, so a marker without it is the common
        prose-only answer (the version gate and the source pin make any other marker shape unreachable). The reader takes it as
        an empty set: no body read (a fallback read here hydrated every prose-only answer the walk met). The tree holds a
        prose-only marker BESIDE a marker carrying calls before the cut, the production shape, so one tree pins both (round
        three, low 1: the earlier scenario had no tool call at all and compared two empty sets)."""
        records, sent = G.SINGLE_FILE["eclipsed_branch_kept"]          # tool calls and prose-only answers, then a compaction after
        path = self.write("notu-both", compacting_variant(records(), "notu"), sent=sent)   #  them: the whole scenario is pre-cut
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        whole_ids = {b.get("id") for t in self.cold(path)["turns"] for a in t["atoms"] if a.get("type") == "assistant"
                     for b in ((a.get("message") or {}).get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"}
        self.assertTrue(whole_ids, "the fixture has tool calls")
        self.fresh(); modes = []; tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        pre = [a for t in tree["turns"] for a in t["atoms"] if a.get("type") == "assistant" and a.get("lazy") is not None]
        with_calls = [a for a in pre if "tu" in a["lazy"]]; prose = [a for a in pre if "tu" not in a["lazy"]]
        self.assertTrue(with_calls and prose, "both shapes before the cut: %d with calls, %d prose-only" % (len(with_calls), len(prose)))
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        found = self.km._seg_of_tool_uses(tree, {"placements": {}, "nodes": {}, "seq": 0}, list(whole_ids) + ["toolu_not_anywhere"])
        self.assertEqual(set(found), whole_ids, "the calls resolved from the scalars; the missing id resolves to nothing")
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "no prose-only answer read: %s" % em.asm_checkpoint_stats()["hydratedBy"])

    def test_the_comments_frame_itself_reads_no_user_text_for_an_echo_above_the_cut(self):
        """Round two, low 1: the pin on the call site. The frame is run over the thread's restored parse with a live echo sent
        after every recorded atom; hydratedBy carries no user-texts row (the frame's inline walk hydrated every pre-cut user
        atom before)."""
        km = self.km
        path, tree = self._restored("frame")
        newest = max(float(a.get("t") or 0) for t in tree["turns"] for a in t["atoms"] if a.get("t"))
        echo = {"_echo_text": "a typed-ahead line", "t": newest + 5, "uuid": "echo-1"}
        class FakeBackend:
            def session_state(self, tsid): return "working"
            def live_atoms(self, tsid): return [echo]
            def pending_queued(self, tsid): return []
            def launch_error(self, tsid): return None
        comments = self.td / "comments.json"; comments.write_text("{}")
        saved = {n: getattr(km, n) for n in ("_comments_path", "_load_comments_cached", "_sdk", "_thread_messages", "_thread_events",
                                              "_thread_reg", "_thread_turn_read", "_thread_owes_first_reply", "_agent_landed_after")}
        try:
            km._comments_path = lambda sid: comments
            km._load_comments_cached = lambda sid: {"threads": [{"sid": "thread-1", "status": "open", "createdT": 1}]}
            km._sdk = lambda: FakeBackend()
            km._thread_messages = lambda *a, **k: []
            km._thread_events = lambda *a, **k: []
            km._thread_reg = lambda tsid: {}
            km._thread_turn_read = lambda *a, **k: (False, False, tree["turns"])
            km._thread_owes_first_reply = lambda *a, **k: False
            km._agent_landed_after = lambda *a, **k: False
            em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
            frame = km._comments_frame(SID)
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
        self.assertIsNotNone(frame, "the frame was built")
        by = em.asm_checkpoint_stats()["hydratedBy"]
        self.assertFalse(any(k.startswith("_atom_user_texts") for k in by), "the frame read no user text: %s" % by)

    def test_the_user_texts_reader_is_attributed_with_its_caller(self):
        """Round two, low 2: one row, _atom_user_texts, stood for both the frame's walk and the echo set's loop; the rows name
        the caller (T377's mechanism)."""
        km = self.km
        path, tree = self._restored("rows")
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        km._echo_landing_atoms(tree["turns"], [{"_echo_text": "an early line", "t": 0}])
        by1 = dict(em.asm_checkpoint_stats()["hydratedBy"])
        path2, tree2 = self._restored("rows")                                          # a second fresh restore for the merge sets: since
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})     #  5b they hydrate USER rows alone, so each caller
        km._merge_sets_memo.clear(); km._merge_tx_sets(tree2, SID + "-rows", 0.0)      #  must meet its rows unhydrated to be named
        by2 = dict(em.asm_checkpoint_stats()["hydratedBy"])
        self.assertIn("_atom_user_texts<-_echo_landing_atoms", by1, "%s" % by1)
        self.assertIn("_atom_user_texts<-_merge_tx_sets", by2, "%s" % by2)
        self.assertNotIn("_atom_user_texts", set(by1) | set(by2), "no bare row: %s %s" % (by1, by2))

    def test_the_floor_and_the_held_filter_share_one_holdable_echo_predicate(self):
        """Round three, low 3: the four clauses (a text key, not a command, not dropped, not landed) were spelled out twice; one
        predicate serves both, the held filter adding its two dependent clauses, so a clause added to one side cannot recreate
        round two's low 4."""
        import inspect
        km = self.km
        self.assertTrue(km._echo_holdable({"_echo_text": "a line"}))
        for bad in ({"_echo_text": "a line", "command": True}, {"_echo_text": "a line", "dropped": True},
                    {"_echo_text": "a line", "_landed": True}, {"_echo_text": ""}, {}):
            self.assertFalse(km._echo_holdable(bad), "%r" % bad)
        self.assertIn("_echo_holdable(", inspect.getsource(km._echo_floor))
        self.assertIn("_echo_floor(", inspect.getsource(km._echo_landing_atoms))
        frame = inspect.getsource(km._comments_frame)
        self.assertIn("_echo_holdable(", frame)
        self.assertNotIn('not a.get("dropped") and not a.get("_landed")', frame, "the clauses live in the predicate alone")

    def _merge_with_live(self, name, live):
        """_merge_live_atoms over a restored tree with a stubbed owning backend holding `live`: the merged rows and the
        by-text mapping prune_live received (the two consumers of the transcript-side text sets beside the landing set)."""
        km = self.km
        path, tree = self._restored(name)
        got = {}
        class _Backend:
            def live_atoms(self, sid):
                return [dict(a) for a in live]
            def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
                got["texts"] = tx_user_texts
        saved = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(lambda sid: _Backend())
        self.addCleanup(lambda: setattr(km.Sessions, "backend_for", saved))
        km._merge_sets_memo.clear()
        merged = km._merge_live_atoms(tree, SID + "-" + name)
        rows = [a.get("_echo_text") for t in merged["turns"] for a in (t.get("atoms") or []) if a.get("_echo_text")]
        return path, tree, rows, got.get("texts")

    def _pre_cut_user_text(self, path, tree):
        """A user text of a pre-cut turn (one the restored tree holds as a marker), read from the cold parse."""
        pre_uuids = {a.get("uuid") for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None}
        for t in self.cold(path)["turns"]:
            for a in t["atoms"]:
                if a.get("uuid") in pre_uuids and a.get("type") == "user":
                    for txt in self.km._atom_user_texts(a):
                        if txt:
                            return txt
        self.fail("the fixture has a pre-cut user text")

    def test_a_dropped_echo_whose_text_landed_before_the_cut_is_hidden_and_pruned(self):
        """Follow-up round one, M3: the landing set's floor was narrowed to the holdable echoes, but the text sets it floors are
        also prune_live's by-text retirement input and the chat's echo dedupe input, which must cover EVERY live echo with a
        text. A dropped echo whose text landed in a pre-cut turn: at the floor over every echo the chat hides it and prune
        would retire it; under the holdable floor (None with no holdable echo; above the cut with one in the tail) it is
        painted as a permanent never-delivered row beside the message that did land, and prune never retires it, the by-text
        prune being an already-dropped echo's only automatic exit (_mark_dropped_echoes skips it at boot)."""
        km = self.km
        for tag, tail in (("alone", []), ("withtail", [{"_echo_text": "a fresh pending line", "t": 4102444800.0, "uuid": "echo-tail"}])):
            with self.subTest(tag):
                path, tree = self._restored("m3-" + tag)
                dropped_text = self._pre_cut_user_text(path, tree)
                dropped = {"_echo_text": dropped_text, "t": 0, "dropped": True, "uuid": "echo-dropped-" + tag}
                path, tree, rows, texts = self._merge_with_live("m3-" + tag, [dropped] + tail)
                self.assertNotIn(dropped_text, rows, "the dropped echo whose text landed is hidden: rows %r" % rows)
                self.assertIsNotNone(texts, "prune_live ran")
                self.assertTrue(km._echo_landed_in(dropped_text, texts), "prune's by-text input covers the landed text")
                if tail:
                    self.assertIn("a fresh pending line", rows, "the pending echo still paints")

    def test_the_echo_landing_set_reads_no_text_below_the_oldest_echo(self):
        """The saving that stands: the landing set reads pre-cut user texts from the oldest live echo's send up, dropped or
        not; with every echo newer than the cut, no pre-cut body is hydrated."""
        km = self.km
        path, tree = self._restored("setfloor")
        newest = max(float(a.get("t") or 0) for t in tree["turns"] for a in t["atoms"] if a.get("t"))
        live = [{"_echo_text": "a new dropped line", "t": newest + 1, "dropped": True}, {"_echo_text": "a new line", "t": newest + 5}]
        km._merge_sets_memo.clear()
        km._merge_tx_sets(tree, SID + "-setfloor", min(float(e["t"]) for e in live))
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "%s" % em.asm_checkpoint_stats()["hydratedBy"])

    def test_a_dropped_echo_below_the_cut_does_not_sink_the_landing_floor(self):
        """Round two, low 4: the floor was the min over every live echo with a text, but the frame holds only echoes that are not
        commands, not dropped and not landed, and a dropped one is never popped by the backend's prune; one dropped send older
        than the compaction sank the floor and every user atom above it was read on each frame."""
        path, tree = self._restored("dropped")
        newest = max(float(a.get("t") or 0) for t in tree["turns"] for a in t["atoms"] if a.get("t"))
        live = [{"_echo_text": "an old dropped line", "t": 0, "dropped": True}, {"_echo_text": "a new line", "t": newest + 5}]
        rows = self.km._echo_landing_atoms(tree["turns"], live)
        self.assertEqual(rows, [], "the dropped echo does not set the floor: %r" % rows)
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "%s" % em.asm_checkpoint_stats()["hydratedBy"])

    def test_a_whole_read_through_the_parse_family_names_the_walker(self):
        """Review, low 1: every parse goes through parsed_session, so its whole read was one row for every walker; the counter
        walks past the parse family to the first caller beyond it."""
        jd = self.km.jd
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("walker", records(), sent=sent)
        self.fresh(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        def some_boot_walker():
            return jd.parsed_session(SID, [path], NOW)
        some_boot_walker()
        keys = list(em.record_cache_stats()["wholeReads"])
        self.assertTrue(any(k.endswith("<-some_boot_walker") for k in keys), "the walker, not the parse family: %s" % keys)
        self.assertFalse(any(k.split("<-")[-1] in ("parsed_session", "parse_session", "parse_cached", "_parse_store") for k in keys), "%s" % keys)
        # round two, low 3: the family is matched by code object, never by name: a local helper named like one is a walker
        self.fresh(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        def _parse():
            return jd.parsed_session(SID, [path], NOW)
        def outer_walker():
            return _parse()
        outer_walker()
        keys = list(em.record_cache_stats()["wholeReads"])
        self.assertTrue(any(k.endswith("<-_parse") for k in keys), "a local helper named _parse is a walker, not the family: %s" % keys)

class KernelOverRestored(Harness):
    """The kernel's and the judges' body readers over a restored tree: every consumer the audit named hydrates what it
    reads, so the same answers come from the restored tree as from the whole parse, with no LazyBodyRead."""

    @classmethod
    def setUpClass(cls):
        cls.km = kernel_module()
        cls.jd = cls.km.jd

    def answers(self, tree):
        km, jd = self.km, self.jd
        segs = [seg for t in tree["turns"] for seg in em.segments(t)]
        store = {"placements": {}, "nodes": {}, "seq": 0}
        return {
            "anchors": [km._seg_anchors(seg["atoms"]) for seg in segs],
            "jumps": [km._seg_jump(seg["atoms"]) for seg in segs],
            "prompts": [km._seg_prompt(seg) for seg in segs],
            "lastText": [km._seg_last_text(seg["atoms"]) for seg in segs],
            "prose": [km._atom_prose_chars(a) for t in tree["turns"] for a in t["atoms"]],
            "userTexts": [km._atom_user_texts(a) for t in tree["turns"] for a in t["atoms"] if a.get("type") == "user"],
            "progress": km._open_turn_progress(tree["turns"]),
            "tasks": km._fold_tasks(tree),
            "landed": [km._turn_landed(t) for t in tree["turns"]],
            "units": [(u[0], u[1]) for u in jd.plan_units(tree, store)],
            "unitText": [jd._unit_text(seg["atoms"]) for seg in segs],
            "asstWork": [jd._has_asst_work(seg["atoms"]) for seg in segs],
            "mids": [km._seg_mids(seg) for seg in segs],                       # T358: the stored ids, or the markers'
            "humanFloor": km._human_turn_floor(tree),
            "txSets": (lambda s_: (s_[0], s_[1], s_[4]))(km._merge_tx_sets(tree, SID + "-" + str(id(tree)))),
            "bgHold": jd._awaiting_bg_hold(SID, "", tree, store, now=NOW),
        }

    def test_kernel_and_judge_readers_answer_the_same_over_the_restored_tree(self):
        for name in COMPACTING:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                path = self.write(name, records(), sent=sent)
                self.fresh()
                whole = self.parse(path)
                cold = json.loads(json.dumps(self.answers(whole), default=str))
                self.assertTrue(self.doc(path))
                self.fresh()
                modes = []
                tree = self.parse(path, modes)
                self.assertEqual(modes, ["restore"])
                self.assertTrue(any(a.get("lazy") is not None for t in tree["turns"] for a in t["atoms"]), "lazy atoms in play")
                got = json.loads(json.dumps(self.answers(tree), default=str))   # every reader hydrated what it needed
                self.assertEqual(got, cold)
                self.assertGreater(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "the readers hydrated on demand")


class EventModelReaders(Harness):
    """Review find (C): the seam split and the declared plan read bodies inside the event model itself."""

    def _restored_with_doc(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = compacting_variant(records(), "seam")
        path = self.write("em-readers", recs)
        self.fresh(); whole = self.parse(path); self.doc(path)
        self.fresh(); tree = self.parse(path)
        self.assertTrue(any(a.get("lazy") is not None for t in tree["turns"] for a in t["atoms"]))
        return whole, tree

    def test_a_seam_split_inside_a_pre_cut_segment_hydrates(self):
        whole, tree = self._restored_with_doc()
        for w_turn, r_turn in zip(whole["turns"], tree["turns"]):
            for w_seg, r_seg in zip(em.segments(w_turn), em.segments(r_turn)):
                if len(w_seg["atoms"]) < 2:
                    continue
                t_split = w_seg["atoms"][0]["t"]
                r_split = em.split_segment(r_seg, t_split)            # reads the tail's bodies: hydrates, never raises
                w_split = em.split_segment(w_seg, t_split)
                if r_split is None or w_split is None:
                    self.assertEqual(r_split, w_split)
                    continue
                for part in r_split:
                    em.hydrate(part["atoms"], SID)
                self.assertEqual(_strip({"turns": [dict(r_split[0]), dict(r_split[1])]}), _strip({"turns": [dict(w_split[0]), dict(w_split[1])]}),
                                 "the seam split reads the same bodies")

    def test_the_declared_plan_over_a_restored_tree_hydrates(self):
        whole, tree = self._restored_with_doc()
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        self.assertEqual(em.declared_plan(tree), em.declared_plan(whole))
        self.assertEqual(em.declared_plan(tree), [], "this scenario declares no plan")
        self.assertEqual((em.asm_checkpoint_stats()["hydratedAtoms"], em.asm_checkpoint_stats()["hydratedBy"]), (0, {}),
                         "review find (1): a session with no plan hydrates nothing for the planner's fold (it asked for the whole tree)")

    def test_the_declared_plan_hydrates_only_the_task_call_and_its_result(self):
        t0 = NOW - 7200
        recs = [G.uline(t0, "plan the retry work", "u1", None),
                G.aline(t0 + 10, "", "a1", "u1", tools=("TaskCreate",), stop="tool_use"),
                G.trline(t0 + 11, "tu_a1_0", "r1", "a1", content="Task #7 created"),
                G.aline(t0 + 20, "the plan is filed", "a2", "r1", stop="end_turn"),
                G.uline(t0 + 100, "now do it", "u2", "a2"),
                G.aline(t0 + 130, "done with the first step", "a3", "u2", stop="end_turn")]
        path = self.write("plan", compacting_variant(recs, "pln"))
        self.fresh(); whole = self.parse(path); plan = em.declared_plan(whole)
        self.assertEqual([t["key"] for t in plan], ["7"], "the whole parse folds the declared step")
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.fresh(); modes = []
        tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        self.assertEqual(em.declared_plan(tree), plan)
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["hydratedAtoms"], 2, "the TaskCreate call and its result, nothing else: %s" % st["hydratedBy"])
        self.assertEqual(sorted(st["hydratedBy"]), ["declared_plan"])
        self.assertEqual(sorted(a["uuid"] for a in em.plan_atoms(tree)), ["a1", "r1"])


class ConcurrentHydration(Harness):
    def test_two_threads_hydrating_the_same_atoms_read_each_record_once(self):
        """CI find (2026-09-11): the judges' unit text and the frame's markdown hydrate the same restored atoms at a boot
        from two threads; both missed the memo and both read (7806 reads for 7800 atoms). The file's read stripe is held
        across a group and the memo re-checked under it, so each record is read once whoever asks first."""
        import threading
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("threads", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); modes = []
        tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        lazy = [a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None]
        self.assertGreater(len(lazy), 0)
        copies = [[dict(a, lazy=dict(a["lazy"])) for a in lazy] for _ in range(4)]   # each thread its own atom dicts, same uuids
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        gate, errors = threading.Barrier(4), []
        def run(atoms):
            try:
                gate.wait(5); em.hydrate(atoms, SID, by="thread")
            except Exception as e:                                # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=run, args=(c,)) for c in copies]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], len(lazy), "one read per record across four threads")
        for c in copies:
            self.assertEqual([a["message"] for a in c], [a["message"] for a in copies[0]], "every thread holds the same bodies")


class ClearedSessionDocument(Harness):
    def test_the_per_file_rewound_walk_leaves_a_lineage_document_standing(self):
        """Review find (B): the one-file walk asked for the leaf's document with the leaf alone as its inputs, so a
        /cleared session's document (its inputs name the anchor too) counted an inputs fallback and was unlinked on
        every reconcile pass. The walk asks quietly and the document stands."""
        jd = kernel_module().jd                                # the judge through the module's one kernel load
        d = self.td / "cleared"; d.mkdir()
        anchor = d / (SID + ".jsonl")
        leaf_sid = "77777777-2222-4333-8444-000000000777"
        leaf = d / (leaf_sid + ".jsonl")
        anchor.write_text("".join(json.dumps(r) + "\n" for r in G.SINGLE_FILE["queued_new_turn"][0]()))
        recs = compacting_variant(G.SINGLE_FILE["compaction_atom"][0](), "clr")
        leaf.write_text("".join(json.dumps(r) + "\n" for r in recs))
        cands = [str(leaf), str(anchor)]
        self.fresh()
        em.parse_session(str(leaf), rompuuid=SID, candidate_files=cands, states=None, postal_log=[], now=NOW)
        self.assertTrue(self.doc(str(leaf)), em.asm_checkpoint_stats())
        em._ASM_CKPT_STATS["fallbacks"] = {}
        saved = jd._sdk_owned
        jd._sdk_owned = lambda fsid: False
        try:
            rewound, fails = jd._per_file_rewound(SID, cands)     # the judges' walk itself, over the leaf and the anchor
        finally:
            jd._sdk_owned = saved
        self.assertIsInstance(rewound, set); self.assertEqual(fails, 0)
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {}, "a quiet ask: no fallback counted")
        self.assertTrue(em._asm_ckpt_file(str(leaf)).exists(), "the display's document stands")
        self.fresh(); modes = []
        em.parse_session(str(leaf), rompuuid=SID, candidate_files=cands, states=None, postal_log=[], now=NOW, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"], "and the next parse restores from it")


    def test_a_one_file_document_takes_the_seeded_leaf_road_whatever_else_the_session_carries(self):
        """2026-09-15: the leaf-road guard carried `len(files) == 1` beside asm_document_seeds, so a ONE-file document whose session
        had another candidate file (a dead episode file named by its episode rows) fell to the memo road, whose boot restore over a
        live growing leaf comes back without its state and walks the file whole: the largest live transcript (282 MB) decoded once at
        every boot's first judge pass. The sidecar's own inputs list is the one-file answer: the leaf takes the seeded walk (the
        pre-cut verdicts from the document, the tail read now) and the memo road stays for the other file; the rewound set is EQUAL
        on both roads."""
        jd = kernel_module().jd
        d = self.td / "leafroad"; d.mkdir()
        leaf_sid = "77777777-2222-4333-8444-000000000778"
        leaf = d / (leaf_sid + ".jsonl")
        recs = compacting_variant(G.SINGLE_FILE["rewind_off_path"][0](), "lr")          # a rewind inside, then a compaction and turns
        recs = recs + _turns_after(recs, "lr", 2)
        leaf.write_text("".join(json.dumps(r) + "\n" for r in recs))
        other = d / ("88888888-2222-4333-8444-000000000888.jsonl")                       # a second candidate: a dead episode file, no
        other.write_text("".join(json.dumps(r) + "\n" for r in G.SINGLE_FILE["queued_new_turn"][0]()))   # lineage relation to the leaf
        self.fresh()
        em.parse_session(str(leaf), rompuuid=leaf_sid, candidate_files=[str(leaf)], states=None, postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(str(leaf), leaf_sid), em.asm_checkpoint_stats())
        self.assertTrue(em.asm_document_seeds(str(leaf)), "the sidecar lists the leaf alone: the document seeds the one-file walk")
        # the memo road's answer, for the equality: a fresh state, the walk over the whole file
        self.fresh()
        memo_answer = em.rewound_uuids(str(leaf), drop=False)
        self.assertTrue(memo_answer, "the fixture holds a rewind")
        # the judges' walk over the leaf beside the other candidate
        self.fresh()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        with em._CKPT_LOCK:
            walked0 = em._REWOUND_STATS["walked"]
        saved = jd._sdk_owned; jd._sdk_owned = lambda fsid: False
        try:
            rewound, fails = jd._per_file_rewound(leaf_sid, [str(leaf), str(other)])
        finally:
            jd._sdk_owned = saved
        self.assertEqual(fails, 0)
        self.assertEqual(rewound & memo_answer, memo_answer, "the rewound set of the leaf equals the memo road's answer")
        with em._CKPT_LOCK:
            walked = em._REWOUND_STATS["walked"] - walked0
        self.assertEqual(walked, 1, "one memo walk: the OTHER file's; the leaf took the seeded road (the base walked both: 2)")
        read = em.read_bytes_report().get(str(leaf), 0)
        self.assertLess(read, os.path.getsize(leaf) // 2, "the leaf's tail was read, not the whole file: %d of %d bytes" % (read, os.path.getsize(leaf)))


class CrossSessionRewoundCandidates(Harness):
    """2026-09-15: the judges' per-file rewound walk adds every fsid a session's episode rows name to its candidates (a fork's
    episode log names its parent's leaf), and a candidate that was not this session's leaf took the memo road unconditionally;
    the memo road reads a file whole whenever its memo is stale (fold_records folds from record zero), and a live growing leaf's
    memo is stale at every boot, so three live leaves, 570 MB, were read whole on every boot's first judge pass as other sessions'
    candidates. Another live SDK session's CURRENT leaf (a reg under its stem, no lastSid fork) whose own one-file document stands
    takes the seeded walk under that document with its own owner bit: the tail read. A live leaf no registry names keeps the memo
    road (the residue the body names), its set equal. Driven on the LIVE shape: the other leaf restored first (a standing tail
    entry, the judges' restore), then the walk."""
    def test_another_sdk_sessions_documented_leaf_named_by_the_episode_rows_takes_the_seeded_walk(self):
        jd = kernel_module().jd
        verdicts = []
        for owner in (True, False):
            with self.subTest(sdk_owned=owner):
                d = self.td / ("cross-%s" % owner); d.mkdir()
                a_sid = "77777777-2222-4333-8444-00000000077%d" % (1 if owner else 0)
                b_sid = "88888888-2222-4333-8444-00000000088%d" % (1 if owner else 0)
                a_leaf = d / (a_sid + ".jsonl"); b_leaf = d / (b_sid + ".jsonl")
                a_leaf.write_text("".join(json.dumps(r) + "\n" for r in G.SINGLE_FILE["queued_new_turn"][0]()))   # this session: small, no rewind
                recs = compacting_variant(G.SINGLE_FILE["rewind_off_path"][0](), "cs")                              # the other: a rewind, compacted,
                recs = recs + _turns_after(recs, "cs", 2)                                                              #  live and growing
                b_leaf.write_text("".join(json.dumps(r) + "\n" for r in recs))
                jd.EPIDIR.mkdir(parents=True, exist_ok=True)                                                         # A's episode rows name B
                (jd.EPIDIR / (a_sid + ".jsonl")).write_text(json.dumps({"head": "h_cs", "fsid": b_sid, "t": NOW}) + "\n")
                jd._episode_memo.pop(a_sid, None)
                reg = jd.SDKDIR / (b_sid + ".json")
                if owner:                                                                                            # B is a live SDK session's
                    jd.SDKDIR.mkdir(parents=True, exist_ok=True)                                                     #  current leaf: its reg, no fork
                    reg.write_text(json.dumps({"sid": b_sid, "alive": True, "name": "web"}))
                    jd._lastsid_memo.pop(b_sid, None)
                self.assertEqual(jd._sdk_owned(b_sid), owner); self.assertIsNone(jd._sdk_last_sid(b_sid))
                try:
                    self.fresh()
                    em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW, sdk_human=owner)
                    self.assertTrue(em.asm_checkpoint_write(str(b_leaf), b_sid, sdk_human=owner), em.asm_checkpoint_stats())
                    self.assertTrue(em.asm_document_seeds(str(b_leaf)))
                    self.fresh(); answer_memo = em.rewound_uuids(str(b_leaf), drop=False)                # the memo road's answer
                    self.fresh(); answer_seed = em.file_rewound(str(b_leaf), rompuuid=b_sid, sdk_human=owner)   # the seeded road's
                    self.assertEqual(answer_memo, answer_seed, "the two roads agree on the other leaf"); self.assertTrue(answer_memo)
                    # the live shape: B restored first (a standing tail entry), then A's walk names B through its episode rows
                    self.fresh(); modes = []
                    em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW,
                                     sdk_human=owner, asm_mode_out=modes)
                    self.assertEqual(modes, ["restore"], "the judges' restore stands before the walk")
                    with em._READ_BYTES_LOCK:
                        em._READ_BYTES.clear()
                    with em._CKPT_LOCK:
                        walked0 = em._REWOUND_STATS["walked"]
                    rewound, fails = jd._per_file_rewound(a_sid, [str(a_leaf)])
                    self.assertEqual(fails, 0)
                    self.assertEqual(rewound, answer_memo, "the walk's set is B's rewound set on either road (A has no rewind)")
                    read = em.read_bytes_report().get(str(b_leaf), 0)
                    with em._CKPT_LOCK:
                        walked = em._REWOUND_STATS["walked"] - walked0
                    if owner:
                        self.assertLess(read, os.path.getsize(b_leaf) // 2, "B's tail, not the whole file: %d of %d bytes (the base read it whole)" % (read, os.path.getsize(b_leaf)))
                        self.assertEqual(walked, 1, "one memo walk, A's own undocumented leaf; B took the seeded road (the base walked both: 2)")
                    else:
                        self.assertEqual(walked, 2, "no registry names B: the memo road as before, the residue the body names")
                    verdicts.append(owner)
                finally:
                    (jd.EPIDIR / (a_sid + ".jsonl")).unlink(missing_ok=True); jd._episode_memo.pop(a_sid, None)
                    reg.unlink(missing_ok=True); jd._lastsid_memo.pop(b_sid, None)
        self.assertEqual(verdicts, [True, False], "both owner bits ran to their verdict")

    def test_another_sessions_document_under_the_other_owner_bit_stands_byte_for_byte_after_the_cross_session_walk(self):
        """2026-09-15 (the review of the cross-session fix, its data-safety low): the cross-session branch names the other leaf's
        owner bit from its registry, and a document WRITTEN under the other bit (the parse cache keys on it; a session whose
        owner changed, a hand-run writer) failed the load's session check, whose note UNLINKS the document and its sidecar: a
        reader that does not own a document removed it from under its owner. The load is own=False on that road now: the
        mismatch is refused quietly, counted under the parse's `foreign:session`, the document stands byte for byte, the walk
        takes the memo road (the whole file, as every candidate did before the seeded road) and its set is the same; the
        owner's own walk over the same document still verifies it."""
        jd = kernel_module().jd
        d = self.td / "cross-mismatch"; d.mkdir()
        a_sid = "77777777-2222-4333-8444-000000000772"; b_sid = "88888888-2222-4333-8444-000000000882"
        a_leaf = d / (a_sid + ".jsonl"); b_leaf = d / (b_sid + ".jsonl")
        a_leaf.write_text("".join(json.dumps(r) + "\n" for r in G.SINGLE_FILE["queued_new_turn"][0]()))
        recs = compacting_variant(G.SINGLE_FILE["rewind_off_path"][0](), "cm"); recs = recs + _turns_after(recs, "cm", 2)
        b_leaf.write_text("".join(json.dumps(r) + "\n" for r in recs))
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        (jd.EPIDIR / (a_sid + ".jsonl")).write_text(json.dumps({"head": "h_cm", "fsid": b_sid, "t": NOW}) + "\n")
        jd._episode_memo.pop(a_sid, None)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True); reg = jd.SDKDIR / (b_sid + ".json")
        reg.write_text(json.dumps({"sid": b_sid, "alive": True, "name": "web"})); jd._lastsid_memo.pop(b_sid, None)
        try:
            self.assertTrue(jd._sdk_owned(b_sid)); self.assertIsNone(jd._sdk_last_sid(b_sid))
            self.fresh()                                                                      # B's document under the OTHER owner bit
            em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW, sdk_human=False)
            self.assertTrue(em.asm_checkpoint_write(str(b_leaf), b_sid, sdk_human=False), em.asm_checkpoint_stats())
            self.assertTrue(em.asm_document_seeds(str(b_leaf)))
            doc_path = em._asm_ckpt_file(str(b_leaf)); meta = doc_path.with_name(doc_path.name + ".meta")
            before = (doc_path.read_bytes(), meta.read_bytes())
            self.fresh(); answer = em.rewound_uuids(str(b_leaf), drop=False); self.assertTrue(answer)
            self.fresh(); st0 = em.asm_checkpoint_stats()
            f0, r0, p0 = dict(st0["fallbacks"]), dict(st0["removed"]), st0["parse"].get("foreign:session", 0)
            rewound, fails = jd._per_file_rewound(a_sid, [str(a_leaf)])
            self.assertEqual(fails, 0); self.assertEqual(rewound, answer, "the memo road's set, as before")
            self.assertTrue(doc_path.exists() and meta.exists(),
                            "the other session's document stands: a reader that does not own it never unlinks it (the base removed both)")
            self.assertEqual((doc_path.read_bytes(), meta.read_bytes()), before, "byte for byte")
            st = em.asm_checkpoint_stats()
            self.assertEqual(dict(st["fallbacks"]), f0, "no note: the fallback belongs to the owner's parse")
            self.assertEqual(dict(st["removed"]), r0, "nothing removed")
            self.assertEqual(st["parse"].get("foreign:session", 0) - p0, 1, "the refusal is counted under the parse's foreign road")
            self.fresh()                                                                      # the owner's own walk still verifies it
            n0 = em.asm_checkpoint_stats()["parse"].get("seeded:refusedStanding", 0)
            self.assertEqual(em.file_rewound(str(b_leaf), rompuuid=b_sid, sdk_human=False), answer)
            self.assertTrue(doc_path.exists()); self.assertEqual(doc_path.read_bytes(), before[0])
            self.assertEqual(em.asm_checkpoint_stats()["parse"].get("seeded:refusedStanding", 0), n0)
        finally:
            (jd.EPIDIR / (a_sid + ".jsonl")).unlink(missing_ok=True); jd._episode_memo.pop(a_sid, None)
            reg.unlink(missing_ok=True); jd._lastsid_memo.pop(b_sid, None)

class SeededDocumentMemo(Harness):
    """2026-09-15 (the review of the cross-session fix, low 3): the judges' seeded walk decoded a leaf's document (a read, a
    gunzip, a JSON parse of every pre-cut record) on every call, and a leaf named by several sessions' episode rows is walked
    once per naming session and once by its owner at every boot's first pass: three decodes of the largest live document. The
    decode is served from a per-process memo keyed on the document file's size and mtime; the load's verification (the stat
    checks, the guard read) runs per walk on the memoized document as on a fresh one, and a rewrite (a new size or mtime)
    misses. Driven on the LIVE shape: the other leaf restored first, two naming sessions and the owner walking in one process.
    The attribution low of the same round rides along: a standing refusal mark on a leaf the reader does not own counts under
    `foreign:refusedStanding`, the owner's under `seeded:refusedStanding` as before."""
    def _world(self, tag, idx=0):
        jd = kernel_module().jd
        d = self.td / ("memo-" + tag); d.mkdir()
        b_sid = "88888888-2222-4333-8444-0000000009%02d" % idx                       # distinct per world, never by a hash
        namers = ["77777777-2222-4333-8444-0000000009%02d" % (2 * idx + i) for i in (1, 2)]
        b_leaf = d / (b_sid + ".jsonl")
        recs = compacting_variant(G.SINGLE_FILE["rewind_off_path"][0](), "dm" + tag[:1]); recs = recs + _turns_after(recs, "dm" + tag[:1], 2)
        b_leaf.write_text("".join(json.dumps(r) + "\n" for r in recs))
        leaves = {}
        jd.EPIDIR.mkdir(parents=True, exist_ok=True); jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        for a_sid in namers:
            leaves[a_sid] = d / (a_sid + ".jsonl")
            leaves[a_sid].write_text("".join(json.dumps(r) + "\n" for r in G.SINGLE_FILE["queued_new_turn"][0]()))
            (jd.EPIDIR / (a_sid + ".jsonl")).write_text(json.dumps({"head": "h_" + tag, "fsid": b_sid, "t": NOW}) + "\n")
            jd._episode_memo.pop(a_sid, None)
        reg = jd.SDKDIR / (b_sid + ".json"); reg.write_text(json.dumps({"sid": b_sid, "alive": True, "name": "web"}))
        jd._lastsid_memo.pop(b_sid, None)
        self.assertTrue(jd._sdk_owned(b_sid)); self.assertIsNone(jd._sdk_last_sid(b_sid))
        def cleanup():
            for a_sid in namers:
                (jd.EPIDIR / (a_sid + ".jsonl")).unlink(missing_ok=True); jd._episode_memo.pop(a_sid, None)
            reg.unlink(missing_ok=True); jd._lastsid_memo.pop(b_sid, None)
        self.addCleanup(cleanup)
        self.fresh()
        em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW, sdk_human=True)
        self.assertTrue(em.asm_checkpoint_write(str(b_leaf), b_sid, sdk_human=True), em.asm_checkpoint_stats())
        self.assertTrue(em.asm_document_seeds(str(b_leaf)))
        self.fresh(); answer = em.rewound_uuids(str(b_leaf), drop=False); self.assertTrue(answer)
        return jd, b_sid, b_leaf, namers, leaves, answer

    def _restored_first(self, b_leaf, b_sid):
        self.fresh(); modes = []
        em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW,
                         sdk_human=True, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"], "the judges' restore stands before the walks")
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()

    def test_a_leaf_named_by_two_sessions_episode_rows_decodes_its_document_once_per_process(self):
        jd, b_sid, b_leaf, namers, leaves, answer = self._world("two", idx=1)
        doc_path = str(em._asm_ckpt_file(str(b_leaf))); size = os.path.getsize(doc_path)
        self._restored_first(b_leaf, b_sid)
        hits0 = em.asm_checkpoint_stats()["parse"].get("seeded:asmDocMemo", 0)
        sets = []
        for sid, files in ((namers[0], [str(leaves[namers[0]])]), (namers[1], [str(leaves[namers[1]])]), (b_sid, [str(b_leaf)])):
            rewound, fails = jd._per_file_rewound(sid, files)
            self.assertEqual(fails, 0); sets.append(rewound)
        self.assertEqual(sets, [answer, answer, answer], "the three walks agree with the memo road's answer")
        read = em.read_bytes_report().get(doc_path, 0)
        self.assertEqual(read, size, "the document decoded ONCE for three walks: %d bytes read of a %d byte document (the base read it three times)" % (read, size))
        self.assertEqual(em.asm_checkpoint_stats()["parse"].get("seeded:asmDocMemo", 0) - hits0, 2, "two walks served from the memo")

    def test_a_memo_hit_still_reads_the_leafs_guard_bytes(self):
        jd, b_sid, b_leaf, namers, leaves, answer = self._world("gd", idx=2)
        doc_path = str(em._asm_ckpt_file(str(b_leaf)))
        self._restored_first(b_leaf, b_sid)
        jd._per_file_rewound(namers[0], [str(leaves[namers[0]])])                    # the miss primes the memo
        doc = json.loads(gzip.decompress(pathlib.Path(doc_path).read_bytes()))        # the document's own cut and guard
        cut_off, _pre_n, guard_hex = doc["files"][b_sid]["cut"]; guard = bytes.fromhex(guard_hex)
        self.assertTrue(guard)
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        hits0 = em.asm_checkpoint_stats()["parse"].get("seeded:asmDocMemo", 0)
        rewound, fails = jd._per_file_rewound(namers[1], [str(leaves[namers[1]])])   # the hit
        self.assertEqual((fails, rewound), (0, answer))
        self.assertEqual(em.asm_checkpoint_stats()["parse"].get("seeded:asmDocMemo", 0) - hits0, 1)
        report = em.read_bytes_report()
        self.assertEqual(report.get(doc_path, 0), 0, "the document is not read on a hit")
        leaf_read = report.get(str(b_leaf), 0)
        self.assertEqual(leaf_read, len(guard), "the hit reads the leaf's guard bytes (%d) and nothing else (the tail is served from"
                         " the record cache the restore filled): %d" % (len(guard), leaf_read))

    def test_a_document_rewritten_in_place_at_the_same_size_misses_the_memo(self):
        jd, b_sid, b_leaf, namers, leaves, answer = self._world("ip", idx=3)
        doc_path = str(em._asm_ckpt_file(str(b_leaf))); size = os.path.getsize(doc_path)
        self._restored_first(b_leaf, b_sid)
        jd._per_file_rewound(namers[0], [str(leaves[namers[0]])])                    # the miss primes the memo
        st0 = os.stat(doc_path)
        data = bytearray(pathlib.Path(doc_path).read_bytes()); data[-1] ^= 0x01        # one byte flipped, the size the same
        pathlib.Path(doc_path).write_bytes(bytes(data)); os.utime(doc_path, ns=(st0.st_atime_ns, st0.st_mtime_ns + 1_000_000_000))
        self.assertEqual(os.path.getsize(doc_path), size)
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        p0 = dict(em.asm_checkpoint_stats()["parse"]); f0 = dict(em.asm_checkpoint_stats()["fallbacks"])
        rewound, fails = jd._per_file_rewound(namers[1], [str(leaves[namers[1]])])
        self.assertEqual((fails, rewound), (0, answer), "the walk still answers (the memo road over the whole file)")
        p1 = dict(em.asm_checkpoint_stats()["parse"])
        self.assertEqual(p1.get("seeded:asmDocMemo", 0) - p0.get("seeded:asmDocMemo", 0), 0, "a moved mtime at the same size misses (the memo never serves stale bytes)")
        self.assertEqual(em.read_bytes_report().get(doc_path, 0), size, "the rewritten bytes are read again")
        self.assertEqual(p1.get("foreign:corrupt", 0) - p0.get("foreign:corrupt", 0), 1, "the flipped byte is refused quietly by the foreign reader")
        self.assertEqual(dict(em.asm_checkpoint_stats()["fallbacks"]), f0, "no note, nothing removed"); self.assertTrue(os.path.exists(doc_path))

    def test_a_rewritten_document_misses_the_memo_and_decodes_again(self):
        jd, b_sid, b_leaf, namers, leaves, answer = self._world("rw", idx=4)
        doc_path = str(em._asm_ckpt_file(str(b_leaf)))
        self._restored_first(b_leaf, b_sid)
        jd._per_file_rewound(namers[0], [str(leaves[namers[0]])])                    # the memo holds the document
        hits0 = em.asm_checkpoint_stats()["parse"].get("seeded:asmDocMemo", 0)
        recs = [json.loads(l) for l in b_leaf.read_text().splitlines()]                # the owner settles again: a new document
        b_leaf.write_text("".join(json.dumps(r) + "\n" for r in recs + _turns_after(recs, "rw2", 3)))
        em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW + 50, sdk_human=True)
        self.assertTrue(em.asm_checkpoint_write(str(b_leaf), b_sid, sdk_human=True), em.asm_checkpoint_stats())
        size2 = os.path.getsize(doc_path)
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        answer2 = em.rewound_uuids(str(b_leaf), drop=False)
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        rewound, fails = jd._per_file_rewound(namers[1], [str(leaves[namers[1]])])
        self.assertEqual((fails, rewound), (0, answer2), "the walk reads the NEW document's verdicts")
        self.assertEqual(em.read_bytes_report().get(doc_path, 0), size2, "the rewritten document is decoded again (a new size or mtime misses)")
        self.assertEqual(em.asm_checkpoint_stats()["parse"].get("seeded:asmDocMemo", 0) - hits0, 0, "no hit on a rewritten document")

    def test_a_standing_mark_on_a_leaf_the_reader_does_not_own_counts_under_the_foreign_key(self):
        jd, b_sid, b_leaf, namers, leaves, answer = self._world("mk", idx=5)
        self.assertTrue(em._asm_mark_refused(str(b_leaf), "reuse", rompuuid=b_sid, sdk_human=True), "the mark is written")
        self.assertTrue(em._asm_refusal_stands(Path(str(b_leaf))))
        self.fresh(); p0 = dict(em.asm_checkpoint_stats()["parse"])
        rewound, fails = jd._per_file_rewound(namers[0], [str(leaves[namers[0]])])   # a reader that does not own the leaf
        self.assertEqual((fails, rewound), (0, answer))
        p1 = dict(em.asm_checkpoint_stats()["parse"])
        self.assertEqual(p1.get("foreign:refusedStanding", 0) - p0.get("foreign:refusedStanding", 0), 1,
                         "the foreign reader's cold walk under the mark counts under its own key (the base counted it as the owner's)")
        self.assertEqual(p1.get("seeded:refusedStanding", 0) - p0.get("seeded:refusedStanding", 0), 0)
        rewound, fails = jd._per_file_rewound(b_sid, [str(b_leaf)])                  # the owner's walk, as before
        self.assertEqual((fails, rewound), (0, answer))
        p2 = dict(em.asm_checkpoint_stats()["parse"])
        self.assertEqual(p2.get("seeded:refusedStanding", 0) - p1.get("seeded:refusedStanding", 0), 1)
        self.assertEqual(p2.get("foreign:refusedStanding", 0) - p1.get("foreign:refusedStanding", 0), 0)

    def test_the_memo_is_bounded_by_resident_bytes_and_reported(self):
        """Round two (medium): a count cap said nothing about memory. The cap is a resident byte ceiling (compressed size times
        the measured multiple, MemTotal / 512 floored at 64 MiB); the oldest documents leave when a new one does not fit, the
        report under asmCheckpoint.asmDocMemo says entries, bytes, capBytes and the multiple."""
        worlds = [self._world("b%d" % i, idx=6 + i) for i in range(3)]
        docs = [str(em._asm_ckpt_file(str(w[2]))) for w in worlds]
        self.fresh()
        for jd, b_sid, b_leaf, namers, leaves, answer in worlds:                          # the live shape: every leaf restored
            modes = []                                                                   #  first (a restore may settle a document
            em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW,
                             sdk_human=True, asm_mode_out=modes)                         #  anew, so the sizes are read after it)
            self.assertEqual(modes, ["restore"])
        weights = [em._asm_doc_memo_weight(os.path.getsize(dp)) for dp in docs]
        saved = em._ASM_DOC_MEMO_CAP
        em._ASM_DOC_MEMO_CAP = weights[1] + weights[2] + 1                              # room for the last two, never three
        self.addCleanup(setattr, em, "_ASM_DOC_MEMO_CAP", saved)
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        for jd, b_sid, b_leaf, namers, leaves, answer in worlds:
            rewound, fails = jd._per_file_rewound(namers[0], [str(leaves[namers[0]])])
            self.assertEqual((fails, rewound), (0, answer))
        self.assertEqual([os.path.getsize(dp) for dp in docs], [w // em._ASM_DOC_MEMO_MULTIPLE for w in weights], "the documents did not move under the walks")
        with em._ASM_CKPT_LOCK:
            held = list(em._ASM_DOC_MEMO); total = em._ASM_DOC_MEMO_BYTES[0]
        self.assertEqual(held, docs[1:], "the oldest document left when the third did not fit under the ceiling (a count cap held all three)")
        self.assertEqual(total, weights[1] + weights[2]); self.assertLessEqual(total, em._ASM_DOC_MEMO_CAP)
        report = em.asm_checkpoint_stats()["asmDocMemo"]
        self.assertEqual(report, {"entries": 2, "bytes": total, "capBytes": em._ASM_DOC_MEMO_CAP, "multiple": em._ASM_DOC_MEMO_MULTIPLE})
        self.assertGreaterEqual(saved, 64 * 1024 ** 2, "the default ceiling is never under 64 MiB")

    def test_a_refused_document_is_never_memoized_and_an_owners_note_drops_the_entry(self):
        """Round two (low 1): the memo was written at the decode, before the version, rows, session, inputs and guard checks, so
        a refused document occupied a slot and its later refusals counted as hits. The write sits after the checks now, and
        an owner's note (which removes the document) drops the entry."""
        jd, b_sid, b_leaf, namers, leaves, answer = self._world("rf", idx=9)
        doc_path = str(em._asm_ckpt_file(str(b_leaf)))
        _write_doc(str(b_leaf), dict(_doc(str(b_leaf)), av=99))                          # a document the load refuses (version)
        self.fresh(); p0 = dict(em.asm_checkpoint_stats()["parse"])
        for _ in range(2):
            rewound, fails = jd._per_file_rewound(namers[0], [str(leaves[namers[0]])])
            self.assertEqual((fails, rewound), (0, answer), "the memo road still answers")
        p1 = dict(em.asm_checkpoint_stats()["parse"])
        self.assertEqual(p1.get("foreign:version", 0) - p0.get("foreign:version", 0), 2, "refused twice, decoded twice")
        self.assertEqual(p1.get("seeded:asmDocMemo", 0) - p0.get("seeded:asmDocMemo", 0), 0, "a refused document is no hit (the base counted the second refusal as one)")
        with em._ASM_CKPT_LOCK:
            self.assertNotIn(doc_path, em._ASM_DOC_MEMO, "a refused document takes no slot"); self.assertEqual(em._ASM_DOC_MEMO_BYTES[0], 0)
        em.parse_session(str(b_leaf), rompuuid=b_sid, candidate_files=[str(b_leaf)], states=None, postal_log=[], now=NOW + 5, sdk_human=True)
        self.assertTrue(em.asm_checkpoint_write(str(b_leaf), b_sid, sdk_human=True), em.asm_checkpoint_stats())   # a good one again
        self._restored_first(b_leaf, b_sid)
        jd._per_file_rewound(namers[1], [str(leaves[namers[1]])])
        with em._ASM_CKPT_LOCK:
            self.assertIn(doc_path, em._ASM_DOC_MEMO, "a verified document is memoized")
        f0 = dict(em.asm_checkpoint_stats()["fallbacks"])
        em.file_rewound(str(b_leaf), rompuuid="99999999-2222-4333-8444-000000000999", sdk_human=True)   # the OWNER road under the
        f1 = dict(em.asm_checkpoint_stats()["fallbacks"])                                                 #  wrong session: a note
        self.assertEqual(f1.get("session", 0) - f0.get("session", 0), 1)
        self.assertFalse(os.path.exists(doc_path), "the note removed the document")
        with em._ASM_CKPT_LOCK:
            self.assertNotIn(doc_path, em._ASM_DOC_MEMO, "and dropped its memo entry"); self.assertEqual(em._ASM_DOC_MEMO_BYTES[0], 0)

class LazyBodies(Harness):
    def test_a_body_read_before_hydration_is_loud_and_hydration_counts(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("compaction_atom", records())
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh()
        tree = self.parse(path)
        lazy = [a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None]
        self.assertTrue(lazy)
        with self.assertRaises(em.LazyBodyRead):
            em._text_of(em._content(lazy[0]["message"]))
        with self.assertRaises((em.LazyBodyRead, TypeError)):      # whichever the encoder trips first, a lazy body never
            json.dumps(tree)                                       #  leaves as an empty message
        n = em.hydrate(lazy, SID)
        self.assertEqual(n, len(lazy))
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["hydratedAtoms"], len([a for a in lazy]), "one read per lazy atom")
        self.assertGreater(st["hydratedBytes"], 0)
        self.assertEqual(em.hydrate(lazy, SID), 0, "nothing left to hydrate")
        json.dumps(tree)


if __name__ == "__main__":
    unittest.main()
