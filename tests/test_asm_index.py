#!/usr/bin/env python3
"""T323 stage 4c: the lazy assembly index. A restored session's pre-cut turns come from the document's `turns` section
with their atoms as LazyAtoms, list-shaped slots built one at a time when a consumer reaches for them, through a
process-wide LRU. Pinned: the list surface (indexing, slicing, iteration, membership, equality, copies, concatenation,
pickling) hands out plain atom dicts; a serializer reading the list's storage raises instead of shipping placeholders;
the atoms are read-only; eviction drops the memo and a rebuilt atom equals the first; a row that will not decode names
the session and the row; a version-3 document and a version-4 document read by a kernel pinned at 3 both fall back to
a whole parse; the kernel's sources concatenate or dump no turn's atoms; a restored tree's pre-turns carry the scalars the
walkers read. Synthetic goldens only."""
import copy
import gzip
import json
import os
import pickle
import re
import sys
import unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import test_asm_checkpoint as T                                   # noqa: E402  the stage 4a harness, its event model
from test_asm_checkpoint_served import transcript                 # noqa: E402  the served fixture's builder: many turns, compacting
em, G = T.em, T.G
SID = T.SID
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


class Restored(T.Harness):
    def setUp(self):
        super().setUp()
        with em._MAT_LOCK:                                        # the index's counters and LRU are process-wide: per test
            em._MAT_LRU.clear()
            em._ASM_INDEX_STATS.update(materialized=0, materializedBy={}, resident=0, evictions=0, restoredTurns=0, rowDecodes=0)

    def restored_tree(self, name="compaction_atom"):
        records, sent = G.SINGLE_FILE[name]
        recs = T.compacting_variant(records(), name[:6]) if name not in ("compaction_atom", "compaction_broken_stitch", "manual_compact_detached") else records()
        path = self.write("idx-" + name, recs, sent=sent)
        whole = self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.fresh(); modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"])
        return path, whole, tree


class ListSurface(Restored):
    def test_the_list_surface_hands_out_plain_atoms_and_the_storage_stays_out_of_reach(self):
        path, whole, tree = self.restored_tree()
        cut = tree["cutTurn"]; self.assertGreater(cut, 0)
        pre = tree["turns"][0]
        self.assertIsInstance(pre, em.PreTurn); self.assertTrue(pre.get("pre"))
        la = pre["atoms"]
        self.assertIsInstance(la, em.LazyAtoms); self.assertIsInstance(la, list)
        n = len(la); self.assertGreater(n, 0)
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "nothing built by the restore or the parse join: %s" % em.asm_index_stats()["materializedBy"])
        self.assertEqual(em.asm_index_stats()["rowDecodes"], 0, "…and no row decoded: no orphan marker, no pre-cut walk (review medium 2)")
        a0 = la[0]
        self.assertIsInstance(a0, dict); self.assertEqual(a0["uuid"], pre["uuids"][0])
        self.assertIs(la[0], a0, "the built atom is memoized in its slot")
        self.assertIs(la[-1], la[n - 1])
        self.assertEqual([a["uuid"] for a in la[1:]], pre["uuids"][1:], "a slice builds its atoms")
        self.assertEqual([a["uuid"] for a in la], pre["uuids"]); self.assertEqual([a["uuid"] for a in reversed(la)], pre["uuids"][::-1])
        self.assertIn(a0, la); self.assertEqual(la.index(a0), 0); self.assertEqual(la.count(a0), 1)
        self.assertEqual(la, list(la)); self.assertEqual(list(la), la); self.assertNotEqual(la, [])
        self.assertIsInstance(la.copy(), list); self.assertNotIsInstance(la.copy(), em.LazyAtoms)
        self.assertEqual(la + [1], list(la) + [1]); self.assertEqual([1] + la, [1] + list(la))
        self.assertEqual(sorted(la, key=lambda a: a["t"]), sorted(list(la), key=lambda a: a["t"]))
        uu = lambda l: [a["uuid"] for a in l]
        em.hydrate(la, SID)                                     # a copy or a pickle reads the bodies: hydrated first, as every reader
        self.assertEqual(uu(pickle.loads(pickle.dumps(la))), pre["uuids"]); self.assertIsInstance(pickle.loads(pickle.dumps(la)), list)
        self.assertNotIsInstance(copy.deepcopy(la), em.LazyAtoms); self.assertEqual(uu(copy.deepcopy(la)), pre["uuids"])
        with self.assertRaises(IndexError):
            la[n]
        for bad in (lambda: la.append({}), lambda: la.sort(), lambda: la.pop(), lambda: la.__setitem__(0, {}), lambda: la.remove(a0)):
            with self.assertRaises(TypeError):
                bad()
        st = em.asm_index_stats()
        self.assertEqual(st["materialized"], n, "each row built once"); self.assertGreaterEqual(st["resident"], n)
        self.assertIn("test_the_list_surface_hands_out_plain_atoms_and_the_storage_stays_out_of_reach", st["materializedBy"], "counted by the consumer")

    def test_a_serializer_reaching_the_storage_raises_and_a_plain_tree_dumps(self):
        path, whole, tree = self.restored_tree()
        pre = tree["turns"][0]
        for obj in (pre["atoms"], {"turns": tree["turns"]}, [pre]):
            with self.assertRaises(TypeError):
                json.dumps(obj)                                 # json reaches a list subclass by iteration: refused, loudly
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "…and built nothing on the way")
        em.hydrate(tree, SID)                                   # the bodies: a plain tree dumps once they are read
        pt = em.plain_tree(tree)
        self.assertNotIsInstance(pt["turns"][0], em.PreTurn); self.assertNotIsInstance(pt["turns"][0]["atoms"], em.LazyAtoms)
        json.dumps(pt, default=lambda o: "<unserializable>")      # the lazy bodies stay unread: hydrate() is the reader
        for k in em._PRE_TURN_KEYS:
            self.assertNotIn(k, pt["turns"][0])
        self.assertEqual(sorted(pt["turns"][0]), sorted(whole["turns"][0]), "a plain pre-turn has the whole parse's turn fields")

    def test_a_pre_turn_carries_the_scalars_the_walkers_read(self):
        path, whole, tree = self.restored_tree("author_kinds")
        for i in range(tree["cutTurn"]):
            pt, wt = tree["turns"][i], whole["turns"][i]
            self.assertEqual(pt["uuids"], [a.get("uuid") for a in wt["atoms"]])
            ts = [a["t"] for a in wt["atoms"] if a.get("t")]
            self.assertEqual((pt["lastT"], pt["maxT"]), (ts[-1], max(ts)))
            models = [em.atom_model(a) for a in wt["atoms"] if a.get("type") == "assistant"]
            models = [m for m in models if m]
            self.assertEqual(pt["lastModel"], models[-1] if models else None)
            self.assertEqual(pt["tools"], [(i_, n_) for a in wt["atoms"] if a.get("type") == "assistant" for i_, n_ in em.atom_tool_uses(a)])
            self.assertEqual(pt["trigger"], wt["trigger"]); self.assertEqual((pt["id"], pt["t"], pt["end"], pt["ended"]), (wt["id"], wt["t"], wt["end"], wt["ended"]))
            segs_r, segs_w = em.segments(pt), em.segments(wt)
            self.assertEqual([(s["id"], s["trigger"], s["t"], s["end"], len(s["atoms"])) for s in segs_r],
                             [(s["id"], s["trigger"], s["t"], s["end"], len(s["atoms"])) for s in segs_w], "the spans as written")
        self.assertEqual(em.asm_index_stats()["materialized"], sum(len(s["atoms"]) for i in range(tree["cutTurn"]) for s in em.segments(tree["turns"][i])) and em.asm_index_stats()["materialized"], "segments build their slices only")


class Eviction(Restored):
    def test_eviction_drops_the_memo_and_a_rebuilt_atom_equals_the_first(self):
        path, whole, tree = self.restored_tree("author_kinds")
        atoms = [a for i in range(tree["cutTurn"]) for a in [None] * len(tree["turns"][i]["uuids"])]
        n = len(atoms); self.assertGreater(n, 3)
        saved = em._MAT_CAP
        em._MAT_CAP = 3
        try:
            with em._MAT_LOCK:
                em._MAT_LRU.clear()
            first = [dict(a) for i in range(tree["cutTurn"]) for a in tree["turns"][i]["atoms"]]   # builds n, evicting down to 3
            st = em.asm_index_stats()
            self.assertEqual(st["resident"], 3); self.assertEqual(st["evictions"], n - 3)
            la = tree["turns"][0]["atoms"]
            self.assertIs(list.__getitem__(la, 0), em._UNMAT, "an evicted slot holds the placeholder again")
            again = dict(la[0])
            self.assertEqual(again, first[0], "a rebuilt atom equals the first build")
            self.assertEqual(em.asm_index_stats()["materialized"], n + 1)
        finally:
            em._MAT_CAP = saved

    def test_a_row_that_will_not_decode_names_the_session_and_the_row(self):
        path, whole, tree = self.restored_tree()
        la = tree["turns"][0]["atoms"]
        la._index.rowb[la._rows[0]] = b"{not json"
        with self.assertRaises(em.LazyIndexError) as cm:
            la[0]
        self.assertIn(SID[:8], str(cm.exception)); self.assertIn("row %d" % la._rows[0], str(cm.exception))


class Versions(Restored):
    def test_a_version_3_document_and_a_version_4_one_read_by_an_older_kernel_both_fall_back(self):
        path, whole, tree = self.restored_tree()
        d = T._doc(path)
        self.assertEqual(d["av"], em._ASM_CKPT_V); self.assertTrue(d["turns"]); self.assertTrue(d["treeIdentity"])
        T._write_doc(path, dict(d, av=3, turns=None, treeIdentity=None))   # the document an older kernel wrote
        self.fresh(); modes = []
        got = T._strip(self.parse(path, modes))
        self.assertEqual(modes, ["full"]); self.assertGreaterEqual(em.asm_checkpoint_stats()["fallbacks"].get("version", 0), 1)
        self.assertEqual(got, T._strip(whole))
        T._write_doc(path, d)                                                # this kernel's document, read by a kernel pinned at 3
        saved = em._ASM_CKPT_V
        em._ASM_CKPT_V = 3
        try:
            self.fresh(); modes = []
            self.parse(path, modes)
            self.assertEqual(modes, ["full"]); self.assertGreaterEqual(em.asm_checkpoint_stats()["fallbacks"].get("version", 0), 1)
        finally:
            em._ASM_CKPT_V = saved
        T._write_doc(path, d)                                                # (a fallback unlinks the document it refused)
        self.fresh(); modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["restore"], "and this kernel restores it")

    def test_a_spoiled_section_digest_falls_back_before_any_atom_is_built(self):
        path, whole, tree = self.restored_tree()
        d = T._doc(path)
        T._write_doc(path, dict(d, treeIdentity="0" * 40))
        self.fresh(); modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["full"]); self.assertGreaterEqual(em.asm_checkpoint_stats()["fallbacks"].get("identity", 0), 1)
        self.assertEqual(em.asm_index_stats()["materialized"], 0)


class Coverage(Restored):
    """Review medium 1: the section must cover every pre-cut row, proven at write and at restore."""

    def test_a_tree_cut_short_writes_no_section_and_the_document_restores_the_atoms(self):
        path = self.write("cov-short", transcript(T.NOW - 86400, turns=40, compact_every=20))   # many turns before the last compaction
        whole = self.parse(path)
        n_pre = max(i for i, t in enumerate(whole["turns"]) if any(a.get("subtype") == "compact_boundary" for a in t["atoms"]))
        self.assertGreaterEqual(n_pre, 2, "the fixture has several pre-cut turns")
        short = {**whole, "turns": whole["turns"][:1]}                   # a rollback armed early: the tree stops after one turn
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(em.asm_checkpoint_write(path, SID, tree=short), em.asm_checkpoint_stats())
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("turnsCoverage"), 1, "the short section was refused, counted")
        d = T._doc(path)
        self.assertIsNone(d["turns"], "a turnless document")
        self.fresh(); modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"]); self.assertFalse(any(isinstance(t, em.PreTurn) for t in tree["turns"]), "the atoms-only restore")
        em.hydrate(tree, SID)
        self.assertEqual(T._strip(tree), T._strip(whole), "…and it equals the whole")

    def test_a_section_missing_a_row_falls_back_whole_at_restore(self):
        path = self.write("cov-miss", transcript(T.NOW - 86400, turns=40, compact_every=20))
        whole = self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        d = T._doc(path)
        self.assertGreaterEqual(len(d["turns"]), 2)
        cut = dict(d, turns=d["turns"][:-1])                            # a section short of its last turn's rows
        cut["treeIdentity"] = em._tree_identity_of_doc(cut["turns"], cut.get("identity"))   # its digest agreeing with itself
        T._write_doc(path, cut)
        self.fresh(); modes = []
        got = self.parse(path, modes)
        self.assertEqual(modes, ["full"]); self.assertGreaterEqual(em.asm_checkpoint_stats()["fallbacks"].get("coverage", 0), 1)
        self.assertEqual(T._strip(got), T._strip(whole))

    def test_a_synthesized_atom_leading_the_first_turn_is_in_the_section(self):
        """An idle span from before the first record leads the first turn (a non-opener opens the turn the prompt then
        absorbs into); its row is a synthesized one and the section covers it like a record's."""
        name = "queued_new_turn"
        records, sent = G.SINGLE_FILE[name]
        recs = T.compacting_variant(records(), "idl")
        t0 = min(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp"))
        path = self.write("cov-idle", recs, states=[{"t": int(t0) - 100, "state": "waiting"}, {"t": int(t0) + 5, "state": "working"}], sent=sent)
        whole = self.cold(path)
        self.assertEqual(whole["turns"][0]["atoms"][0]["type"], "idle", "the idle span leads the whole parse's first turn")
        self.fresh(); self.parse(path)
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        d = T._doc(path)
        self.assertIsNotNone(d["turns"]); self.assertIsNone(d["turns"][0]["uuids"][0], "the idle atom's row leads the section's first turn")
        row = json.loads(d["atoms"][d["turns"][0]["atoms"][0]])                     # v6: the rows are JSON strings
        self.assertEqual((row.get("syn"), "m" in row, row["s"]["type"]), (1, False, "idle"), "…as a synthesized row (an idle span has no message)")
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"]); self.assertEqual(got, whole)

    def test_a_refused_section_leaves_no_row_behind_and_the_document_restores_in_a_fresh_process(self):
        """Review round 2, M1: the walk appended synthesized rows before the coverage check, so a refused document held more
        rows than its identity counted and every fresh process fell back whole under 'identity' for good."""
        recs = transcript(T.NOW - 86400, turns=40, compact_every=20)
        t0 = min(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp"))
        path = self.write("cov-leak", recs, states=[{"t": int(t0) - 100, "state": "waiting"}, {"t": int(t0) + 5, "state": "working"}])
        whole = self.cold(path)
        self.assertEqual(whole["turns"][0]["atoms"][0]["type"], "idle", "an idle span leads: a synthesized row the walk appends")
        self.fresh(); full = self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(em.asm_checkpoint_write(path, SID, tree={**full, "turns": full["turns"][:1]}))   # a short tree: refused
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("turnsCoverage"), 1)
        d = T._doc(path)
        self.assertIsNone(d["turns"]); self.assertFalse(any(json.loads(r).get("syn") for r in d["atoms"]), "no synthesized row left behind")
        em._ASM_CKPT_STATS["fallbacks"] = {}
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"], "a fresh process restores the atoms-only document: %s" % em.asm_checkpoint_stats()["fallbacks"])
        self.assertEqual(got, whole)

    def test_a_uuid_less_absorbed_attachment_matches_its_row_and_the_section_is_written(self):
        """A GENUINE uuid-less queued_command attachment before the cut (Claude Code writes the attachment record with no uuid):
        its atom has a row from the absorbed branch and no uuid, so the walk finds that row by its scalars rather than mint a
        duplicate synthesized one (which made a correct tree fail coverage), and, having no record to read back, its body
        rides inline in the row (a lazy marker there could never hydrate: round 3)."""
        records, sent = G.SINGLE_FILE["queued_new_turn"]
        recs = records()
        t_last = max(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp"))
        last = next(r for r in reversed(recs) if r.get("uuid"))
        recs = recs + [G.attline(t_last + 30, "a queued follow-up, sent while the turn ran", None, last["uuid"])]   # no uuid
        path = self.write("cov-attach", T.compacting_variant(recs, "att"), sent=sent)
        whole = self.cold(path)
        att = [a for t in whole["turns"] for a in t["atoms"] if a.get("absorbed") or (a.get("type") == "user" and a.get("uuid") is None)]
        self.assertTrue(att, "the fixture holds a uuid-less absorbed atom before the cut")
        self.fresh(); self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.assertIsNone(em.asm_checkpoint_stats()["skipped"].get("turnsCoverage"), "not refused")
        d = T._doc(path)
        self.assertIsNotNone(d["turns"], "the section was written")
        rows = [r for r in (json.loads(r_) for r_ in d["atoms"]) if "r" not in r and not r.get("syn")]   # v6: string rows
        self.assertTrue(rows, "the attachment's row: no record behind it")
        self.assertTrue(all("lz" not in r and "m" in r for r in rows), "…its body inline, no lazy marker")
        got, modes, n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"]); self.assertEqual(got, whole, "restored and hydrated equals the whole, the attachment included")

    def test_a_tail_marker_is_dedupped_like_the_whole_parse_against_assistants_only_and_by_prefix(self):
        """Round 3 M2a and M2b: a marker equal to a pre-cut USER prompt is salvaged (a prompt is not a kept reply), and a marker
        that is a strict prefix of a pre-cut reply, or extends one, is dropped, as the whole parse decides both."""
        recs = transcript(T.NOW - 86400, turns=40, compact_every=20)
        t_last = max(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp"))
        prompt = next(r for r in recs if r.get("type") == "user" and isinstance(r["message"].get("content"), str))["message"]["content"]
        reply = next(r for r in recs if r.get("type") == "assistant")["message"]["content"][0]["text"]
        cases = [("same-as-prompt", prompt, True), ("prefix-of-reply", reply[: max(1, len(reply) // 2)], False), ("extends-reply", reply + " and more", False)]
        for name, text, salvaged in cases:
            with self.subTest(case=name):
                path = self.write("orph-" + name, recs, states=[{"t": int(t_last) + 1, "orphanReply": {"uuid": "orph-" + name, "text": text}}])
                whole = self.cold(path)
                self.assertEqual(any(a.get("orphaned") for t in whole["turns"] for a in t["atoms"]), salvaged, "the whole parse's verdict")
                self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
                self.fresh(); modes = []
                tree = self.parse(path, modes)
                self.assertEqual(modes, ["restore"])
                em.hydrate(tree, SID)
                self.assertEqual(T._strip(tree), whole, "restored equals whole: %s" % name)

    def test_a_permuted_section_is_refused_by_the_digest(self):
        """Review round 2, low a: two equal-length turns with their row lists swapped cover the rows and would restore the wrong
        tree; the digest covers each turn's rows, so it differs."""
        path = self.write("cov-perm", transcript(T.NOW - 86400, turns=40, compact_every=20))
        self.parse(path); self.assertTrue(self.doc(path))
        d = T._doc(path)
        pairs = [(i, j) for i in range(len(d["turns"])) for j in range(i + 1, len(d["turns"])) if len(d["turns"][i]["atoms"]) == len(d["turns"][j]["atoms"])]
        self.assertTrue(pairs, "two turns of equal length")
        i, j = pairs[0]
        d["turns"][i]["atoms"], d["turns"][j]["atoms"] = d["turns"][j]["atoms"], d["turns"][i]["atoms"]
        T._write_doc(path, d)                                             # the stored digest unchanged
        self.fresh(); modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["full"]); self.assertGreaterEqual(em.asm_checkpoint_stats()["fallbacks"].get("identity", 0), 1)

    def test_an_old_marker_walks_no_pre_cut_row_and_a_tail_marker_dedups_by_hash_without_building(self):
        """Review round 2, M2: one marker stamped in the pre-cut history walked and hydrated every pre-cut assistant; the
        markers the tail can hold decide the walk, and a tail marker matching a pre-cut reply exactly is answered by the rows'
        text hashes, no atom built."""
        recs = transcript(T.NOW - 86400, turns=40, compact_every=20)
        t0 = min(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp"))
        first_reply = next(r for r in recs if r.get("type") == "assistant")["message"]["content"][0]["text"]
        path = self.write("orph-old", recs, states=[{"t": int(t0) + 30, "orphanReply": {"uuid": "orph-old", "text": "a reply from before the cut"}}])
        whole = self.cold(path)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"])
        self.assertEqual(em.asm_index_stats()["rowDecodes"], 0, "an old marker: no pre-cut row decoded")
        em.hydrate(tree, SID); self.assertEqual(T._strip(tree), whole)
        t_last = max(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp"))
        path2 = self.write("orph-tail", recs, states=[{"t": int(t_last) + 1, "orphanReply": {"uuid": "orph-tail", "text": first_reply}}])
        whole2 = self.cold(path2)
        self.assertFalse(any(a.get("orphaned") for t in whole2["turns"] for a in t["atoms"]), "the whole parse dedups the exact text")
        self.fresh(); self.parse(path2); self.assertTrue(self.doc(path2))
        self.fresh(); modes = []
        with em._MAT_LOCK:                                            # the counters, after this test's own hydration above
            em._ASM_INDEX_STATS.update(materialized=0, materializedBy={}, rowDecodes=0)
        tree2 = self.parse(path2, modes)
        self.assertEqual(modes, ["restore"])
        st = em.asm_index_stats()
        self.assertGreater(st["rowDecodes"], 0, "the tail marker consults the rows' hashes"); self.assertEqual(st["materialized"], 0, "…and builds nothing: %s" % st["materializedBy"])
        em.hydrate(tree2, SID); self.assertEqual(T._strip(tree2), whole2, "deduped like the whole parse")

    def test_a_tree_that_yields_no_section_is_not_rewritten_at_every_settle(self):
        name = "compaction_atom"
        records, sent = G.SINGLE_FILE[name]
        path = self.write("cov-none", records(), sent=sent)
        self.parse(path)
        none_tree = {"turns": []}
        self.assertTrue(em.asm_checkpoint_write(path, SID, tree=none_tree))
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(em.asm_checkpoint_write(path, SID, tree=none_tree), "the same tree: remembered as yielding none")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("written"), 1)
        self.assertTrue(em.asm_checkpoint_write(path, SID, tree=self.parse(path)), "another tree: written again, with its section")
        self.assertIsNotNone(T._doc(path)["turns"])


class AuditGuard(unittest.TestCase):
    """The kernel's sources reach a turn's atoms only through the list surface: no concatenation of an atoms list with `+`
    (list.__add__ reads the storage of a plain list, and a LazyAtoms answers it; a plain list on the left would not), no
    json dump of a tree or its turns, no deepcopy of them."""

    def test_no_source_concatenates_or_dumps_a_turns_atoms(self):
        bad = []
        for name in ("kernel.py", "judge.py", "event_model.py"):
            src = open(os.path.join(ROOT, "kernel", name)).read()
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(r'\+\s*\w+\["atoms"\]|\["atoms"\]\s*\+', line) and "LazyAtoms" not in line and "def __add__" not in line and "def __radd__" not in line:
                    bad.append("%s:%d %s" % (name, i, line.strip()[:100]))
                if re.search(r'json\.dumps\((session|tree|parsed|turns|turn)\b', line):
                    bad.append("%s:%d %s" % (name, i, line.strip()[:100]))
                if re.search(r'copy\.deepcopy\((session|tree|parsed|turns|turn)\b', line):
                    bad.append("%s:%d %s" % (name, i, line.strip()[:100]))
        allowed = ('segs[0]["atoms"] = lead + segs[0]["atoms"]',                 # the segmentation over a plain turn's atoms
                   'entry["atoms"] = entry["atoms"] + new_atoms')                    # the assembly entry's TAIL atoms: a plain list
        bad = [b for b in bad if not (b.startswith("event_model.py") and any(x in b for x in allowed))]
        self.assertEqual(bad, [], "a reader that would copy a turn's storage or dump a tree: %s" % bad)



class ScalarWalkers(Restored):
    """T358: the per-cycle walkers over a restored store read the scalars the document carries and build no atom. A
    restored turn carries `pcs` (assistant prose chars by uuid) and `hT` (the newest genuine-human time); each of its
    segment rows carries `w` (the has-work verdict) and `mids` (postal message ids); a segment's atoms are a lazy VIEW
    that builds only what is read. The caption planner skips captioned units before any atom is built; the kernel's
    transcript-side sets, human floor and message-id join read the scalars. The has-work rule is pinned beside the
    document version: a change to the rule (or to what the stored fields mean) is a version bump. The kernel is loaded
    once, before any tree is parsed: loading it re-executes the shared event model module, and a tree parsed before
    that would hold the placeholder sentinel of the earlier execution."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.km = T.kernel_module()

    def _pre_and_whole_segs(self, whole, tree):
        cut = tree["cutTurn"]
        return [(em.segments(w), em.segments(r)) for w, r in zip(whole["turns"][:cut], tree["turns"][:cut])]

    def test_a_restored_turn_carries_the_walkers_scalars(self):
        path, whole, tree = self.restored_tree()
        cut = tree["cutTurn"]
        for w_turn, r_turn in zip(whole["turns"][:cut], tree["turns"][:cut]):
            self.assertTrue(r_turn.get("pre"))
            self.assertEqual(r_turn["pcs"], {a["uuid"]: em.atom_prose_chars(a) for a in w_turn["atoms"]
                                             if a.get("uuid") and em.atom_prose_chars(a) > 0}, "pcs: the assistant prose chars by uuid")
            self.assertEqual(r_turn["hT"], max((a.get("t", 0) for a in w_turn["atoms"] if a.get("type") == "user"
                                                and a.get("author") == "human" and not em.is_interrupt_record(a)), default=None))
            for w_seg, r_seg in zip(em.segments(w_turn), em.segments(r_turn)):
                self.assertEqual(r_seg["w"], any(em.atom_has_work(a) for a in w_seg["atoms"]), "w: the has-work verdict")
                self.assertEqual(r_seg["mids"], [m for a in w_seg["atoms"] for m in em.atom_mids(a)], "mids: the ids in order")
                pa = em.seg_prompt_atom(w_seg)
                self.assertEqual(r_seg["hp"], bool(pa and pa.get("author") == "human"), "hp: a message caption is wanted")
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "reading the scalars builds nothing")

    def test_a_segments_atoms_are_a_view_that_builds_only_what_is_read(self):
        path, whole, tree = self.restored_tree()
        pre = tree["turns"][0]; la = pre["atoms"]
        segs = em.segments(pre)
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "segmenting a restored turn builds nothing")
        seg = segs[0]; view = seg["atoms"]
        with self.assertRaises(TypeError):
            json.dumps(view)                                      # placeholders never ship: refused while a slot is unbuilt
        self.assertIsInstance(view, em.LazyAtoms); self.assertIsInstance(view, list)
        self.assertEqual(len(view), pre["segs"][0][5])
        a0 = view[0]
        self.assertEqual(em.asm_index_stats()["materialized"], 1, "one read, one build")
        self.assertIs(a0, la[pre["segs"][0][4]], "the view's build is the parent's slot")
        self.assertEqual(view.uuids(), pre["uuids"][pre["segs"][0][4]:pre["segs"][0][4] + len(view)])
        self.assertIsInstance(view[0:1], em.LazyAtoms, "a slice of a view is a view")
        self.assertEqual([a["uuid"] for a in view], view.uuids())
        n_built = em.asm_index_stats()["materialized"]
        self.assertEqual([a["uuid"] for a in view], view.uuids(), "a second pass over the view")
        self.assertEqual(em.asm_index_stats()["materialized"], n_built, "...builds nothing and, holding what it read, asks the parent nothing")
        with em._MAT_LOCK:                                        # an eviction in the parent: the view keeps what it read (its build's
            for key in list(em._MAT_LRU):                         # lifetime), as the plain-list slice it replaces did; a slot it never
                ref, j = em._MAT_LRU.pop(key); list.__setitem__(ref(), j, em._UNMAT)   # read is rebuilt from the parent (the LRU
                #                                                                        holds the list weakly: the value is (ref, row))
        self.assertTrue(view._unbuilt(), "the json guard reads the parent's slots")
        self.assertIs(view[0], a0, "a slot the view read stays")
        fresh = em.segments(pre)[0]["atoms"]
        self.assertEqual(fresh[0]["uuid"], a0["uuid"], "a new view rebuilds from the parent: a rebuilt atom equals the first")
        self.assertEqual(em.asm_index_stats()["materialized"], n_built + 1)

    def test_the_planner_skips_captioned_units_before_any_atom_is_built(self):
        path, whole, tree = self.restored_tree()
        jd = self.km.jd
        all_tasks = jd._ready_tasks(whole)
        ids = {w["id"] for t in all_tasks for w in t["writes"]}
        self.assertTrue(ids)
        h0 = em.asm_checkpoint_stats()["hydratedAtoms"]
        self.assertEqual(jd._ready_tasks(tree, None, ids), [], "every unit captioned: nothing planned")
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "...and no atom built: %s" % em.asm_index_stats()["materializedBy"])
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], h0, "...and no body read")
        first_ids = {sg["id"] for sg in em.segments(tree["turns"][0])}
        pre_work = next(t for t in all_tasks if t["kind"] == "work" and not t.get("live") and any(w["id"] in first_ids for w in t["writes"]))
        undone = [w["id"] for w in pre_work["writes"]]
        got = jd._ready_tasks(tree, None, ids - set(undone))
        self.assertEqual([w["id"] for t in got for w in t["writes"]], undone, "the one uncaptioned unit is planned, with its undone writes only")
        self.assertLessEqual(em.asm_index_stats()["materialized"], len(pre_work["atoms"]), "at most that segment's atoms were built")
        self.assertEqual([t["writes"] for t in jd._ready_tasks(tree)], [t["writes"] for t in all_tasks], "nothing captioned: the whole plan")
        with em._MAT_LOCK:
            em._ASM_INDEX_STATS.update(materialized=0, materializedBy={})
        self.assertEqual(jd._ready_tasks(tree, None, ids), [])
        work_ids = {w["id"] for t in all_tasks if t["kind"] == "work" for w in t["writes"]}   # every work caption filed, no
        path2, whole2, tree2 = self.restored_tree("compaction_broken_stitch")                #  prompt caption: with hp False
        with em._MAT_LOCK:                                                                   #  nothing is planned. A FRESH
            em._ASM_INDEX_STATS.update(materialized=0, materializedBy={})                    #  restored tree: the one above is
        work_ids2 = {w["id"] for t in jd._ready_tasks(whole2) if t["kind"] == "work" for w in t["writes"]}   # built and hydrated
        real_segments, real_hydrate, calls = em.segments, em.hydrate, []                     #  whole by now, so its counters
        em.segments = lambda turn: [dict(sg, hp=False) for sg in real_segments(turn)]      # cannot move; every segment's prompt
        em.hydrate = lambda atoms, *a, **k: calls.append(len(atoms)) or real_hydrate(atoms, *a, **k)
        try:
            self.assertEqual(jd._ready_tasks(tree2, None, work_ids2), [])
        finally:
            em.segments, em.hydrate = real_segments, real_hydrate
        self.assertEqual((em.asm_index_stats()["materialized"], calls), (0, []),
                         "...and no segment is built or handed to hydrate (arm low 1): %s" % em.asm_index_stats()["materializedBy"])
        self.assertEqual(em.asm_index_stats()["materialized"], 0,
                         "a segment with no human message (no #p caption ever) is not re-checked by building its trigger: %s"
                         % em.asm_index_stats()["materializedBy"])

    def test_the_kernel_walkers_read_the_scalars(self):
        path, whole, tree = self.restored_tree()
        km = self.km
        cut = tree["cutTurn"]
        self.assertEqual(km._human_turn_floor(tree), km._human_turn_floor(whole))
        w_sets, r_sets = km._merge_tx_sets(whole, SID), km._merge_tx_sets(tree, SID)
        self.assertEqual((r_sets[0], r_sets[1], r_sets[4]), (w_sets[0], w_sets[1], w_sets[4]), "uuids, text uuids and the human floor agree")
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "the sets and the floor built nothing: %s" % em.asm_index_stats()["materializedBy"])
        tail_texts = {t for turn in whole["turns"][cut:] for a in turn["atoms"] for t in km._atom_user_texts(a)}
        self.assertEqual(r_sets[2], frozenset(tail_texts), "with no echo floor, the user texts are the tail's")
        floor = min(a["t"] for a in whole["turns"][0]["atoms"] if a.get("t"))
        r_old = km._merge_tx_sets(tree, SID, floor)
        self.assertEqual(r_old[2], w_sets[2], "an echo floor at the first turn: every user text, as the whole parse's")
        self.assertIs(km._merge_tx_sets(tree, SID, floor + 1), r_old, "a later floor is served by the entry covering it")
        for w_segs, r_segs in self._pre_and_whole_segs(whole, tree):
            for w_seg, r_seg in zip(w_segs, r_segs):
                self.assertEqual(km._seg_mids(r_seg), km._seg_mids(w_seg))
        h0 = em.asm_checkpoint_stats()["hydratedAtoms"]
        for w_segs, r_segs in self._pre_and_whole_segs(whole, tree):
            for w_seg, r_seg in zip(w_segs, r_segs):
                self.assertEqual(km._seg_anchors(r_seg["atoms"]), km._seg_anchors(w_seg["atoms"]))
                self.assertEqual(km._seg_jump(r_seg["atoms"]), km._seg_jump(w_seg["atoms"]))
                self.assertEqual(km._seg_last_text(r_seg["atoms"]), km._seg_last_text(w_seg["atoms"]))
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], h0, "the anchors, jump and last-text reads hydrate nothing")

    def test_the_work_rule_is_pinned_beside_the_document_version(self):
        """The document stores atom_has_work's verdict (w), seg_prompt_atom's (hp), _prose_chars (pc, pcs) and postal_mids
        (mid, mids): a change to any of them changes what a stored document means, so it is a version bump. Re-pin here
        WITH the bump."""
        import hashlib, inspect
        rule = "\n".join(inspect.getsource(f).strip() for f in (em.atom_has_work, em._has_text, em.atom_tool_uses, em.seg_prompt_atom,
                                                                em._prose_chars, em.atom_prose_chars, em.postal_mids, em._encoded_mids,
                                                                em.is_interrupt_record, em._content, em._text_of, em._lazy_of, em.atom_mids,
                                                                em._machine_written)) + "\n" + em.POSTAL_RE.pattern
        self.assertEqual((em._ASM_CKPT_V, hashlib.sha1(rule.encode()).hexdigest()[:10]), (7, "56888f276a"),   # v7 (stage one b): the cut
        #                                                                                      rule moved, the work rule did not
        #                                                                                      (v6, T401 (4): the row format)
                         "the stored verdicts' rules changed: bump em._ASM_CKPT_V and re-pin the digest here")

    def test_the_stored_rules_on_odd_content_shapes(self):
        """The readings the document stores where the body road's shape is odd: an assistant message whose content is a bare
        string counts as text (work) for a resident atom and for its lazy marker alike, unlike the judges' body road, which
        read no text there (documented at atom_has_work); a bare string or a nested list inside a content list carries no
        message id (as the body road read it), while a bare-string content does."""
        bare = {"type": "assistant", "uuid": "a-bare", "message": {"role": "assistant", "content": "a bare reply"}}
        self.assertTrue(em.atom_has_work(bare))
        lazy = dict(bare, lazy=em._lazy_of(bare, "a", 0), message=None)
        self.assertTrue(em.atom_has_work(lazy), "the marker's nt reads the bare string as text: the two roads agree")
        mk = "<!-- romp-msg-id: 1700000000.1_1.TESTHOST -->"
        self.assertEqual(em.postal_mids(mk), ["1700000000.1_1.TESTHOST"], "a bare-string content")
        self.assertEqual(em.postal_mids([mk, [{"type": "text", "text": mk}]]), [], "no block: no id")
        self.assertEqual(em.postal_mids([{"type": "text", "text": mk}, "x"]), ["1700000000.1_1.TESTHOST"])


class CapsSizedToTheMachine(unittest.TestCase):
    """2026-09-11: the index's 20,000-atom literal evicted 1.19 million atoms in 150 s on a 50-session devbox, the 64 MB body memo
    hydrated 3.1 GB, and the 64 KB fold-state cap cold-refolded 31 leaves (1.74 GB) at every boot. The user's direction: caches are
    one shared pool sized to the machine, never a small literal."""

    def test_the_index_cap_is_a_fraction_of_the_machine_with_a_floor(self):
        text = "MemTotal:       123634396 kB\n"
        self.assertEqual(em._machine_memory_bytes(text), 123634396 * 1024)
        self.assertEqual(em._machine_memory_bytes("garbage"), 0, "unreadable: zero, so the floors apply")
        self.assertGreaterEqual(em._MAT_CAP, 500_000)
        self.assertGreaterEqual(em._HYDRATED_CAP, 1024 ** 3)
        self.assertEqual(em._CKPT_FOLD_CAP, 8 * 1024 * 1024)

    def test_the_environment_sets_each_cap_outright(self):
        with mock.patch.dict(os.environ, {"ROMP_ASM_INDEX_CAP": "777", "ROMP_HYDRATED_CAP_MB": "3", "ROMP_CKPT_FOLD_CAP_KB": "2"}):
            self.assertEqual(em._env_or("ROMP_ASM_INDEX_CAP", 1), 777)
            self.assertEqual(em._env_or("ROMP_HYDRATED_CAP_MB", 1, 1024 * 1024), 3 * 1024 * 1024)
            self.assertEqual(em._env_or("ROMP_CKPT_FOLD_CAP_KB", 1, 1024), 2048)
        with mock.patch.dict(os.environ, {"ROMP_ASM_INDEX_CAP": "junk"}):
            self.assertEqual(em._env_or("ROMP_ASM_INDEX_CAP", 5), 5, "a bad value: the derived default")

    def test_the_caps_are_derived_in_source_not_literals(self):
        src = open(em.__file__).read()
        self.assertIn('_MAT_CAP = _env_or("ROMP_ASM_INDEX_CAP", max(500_000, _machine_memory_bytes() // (32 * 1024)))', src)
        self.assertIn('_HYDRATED_CAP = _env_or("ROMP_HYDRATED_CAP_MB", max(1024 ** 3, _machine_memory_bytes() // 32), 1024 * 1024)', src)
        self.assertIn('_CKPT_FOLD_CAP = _env_or("ROMP_CKPT_FOLD_CAP_KB", 8 * 1024 * 1024, 1024)', src)


if __name__ == "__main__":
    unittest.main()
