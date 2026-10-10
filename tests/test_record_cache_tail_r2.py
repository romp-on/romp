"""Tail-only record cache, round two (2026-10-08): the two defects the first live deploy measured.

1. Records were not freed, only moved out of the cache's count: a large leaf's assembly entry stayed WHOLE (its document
   was over the flat 16 MiB cap, and an idle leaf is never parsed again to re-seat), and the parse store's tree built from it
   held every body. Now a tail-only leaf's document may reach 1/16 of its pre-cut bytes, the writer releases the whole entry
   at once, and the kernel replaces the store's tree with a restore.
2. Four routine callers (tasks_for, _plan_session, _auto_nudge_session, _bg_placed_tops) read whole transcripts back from
   disk: each reached the records only through a WHOLE parse of the session, taken when a restored entry demoted. Now a
   restored entry of a tail-only leaf demoted for a reason the chain proof covers restores again (the boot's road), and its
   leaf's record window is pinned to the document's cut, so the pass reads nothing before the window.

Every test here fails on the round-one head (62e03751a). Synthetic only: invented text, placeholder uuids."""
import array
import collections
import gc
import json
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_record_cache_tail_only as T                 # noqa: E402  one kernel copy for both files: its km, jd, em and helpers
from test_asm_checkpoint_served import transcript, iso  # noqa: E402  the stage 4a fixture's builder (synthetic)

km, jd, em = T.km, T.jd, T.em
SID = T.SID
NOW = T.NOW
WINDOW = 16 * 1024


def cold():
    """(records read before a window, by caller) so far."""
    st = em.cold_read_stats()
    return st["records"], {k: v["records"] for k, v in st["byCaller"].items()}


def tail_turn(k, parent, t, boundary=False):
    """One appended turn chained on `parent`, with a compaction (boundary and summary) before it when asked."""
    out = []
    if boundary:
        out.append({"type": "system", "subtype": "compact_boundary", "uuid": "nb%d" % k, "parentUuid": None,
                    "logicalParentUuid": parent, "timestamp": iso(t),
                    "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
        out.append({"type": "user", "uuid": "ns%d" % k, "parentUuid": "nb%d" % k, "timestamp": iso(t + 1), "isCompactSummary": True,
                    "message": {"role": "user", "content": "summary so far: retry budget cache window %d" % k}})
        parent, t = "ns%d" % k, t + 2
    out.append({"type": "user", "uuid": "nu%d" % k, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed",
                "cwd": "/w/notes-api", "message": {"role": "user", "content": "next step %d: tighten the retry cap" % k}})
    out.append({"type": "assistant", "uuid": "na%d" % k, "parentUuid": "nu%d" % k, "timestamp": iso(t + 20), "cwd": "/w/notes-api",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "done with step %d " % k + "ok " * 40}],
                            "stop_reason": "end_turn"}})
    return out


class R2Base(unittest.TestCase):
    """A rebound judge state, a projects root the tail rule windows, a checkpoint directory; a leaf of TURNS turns with a
    compaction every 40 (160 turns: 306 KB), so a 16 KiB window holds it tail-only. An appended turn is 581 bytes, 1,023
    with a compaction."""
    TURNS = 160

    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()
        (self.td / "session-hosts").write_text("off\n")      # CLAUDE.md: a minted state root never spawns a session host
        self.proj = self.td / "projects" / "-w-notes-api"
        self.proj.mkdir(parents=True)
        self.saved = (jd.STATE, jd.PROJECTS, jd.plan_llm, jd.opener_llm, jd._group_store)
        jd._rebind_state(self.td)
        jd.PROJECTS = self.td / "projects"
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (self.td / "states").mkdir(exist_ok=True)
        (self.td / "judge-units-cache").mkdir(exist_ok=True)
        em.set_checkpoint_dir(lambda: self.td / "checkpoints")
        self._reset()
        self.path = str(self.proj / (SID + ".jsonl"))
        self.recs = transcript(NOW - 86400, turns=self.TURNS, compact_every=40)
        Path(self.path).write_text("".join(json.dumps(r) + "\n" for r in self.recs))
        self.parent, self.t, self.k = self.recs[-1]["uuid"], NOW - 86400 + self.TURNS * 60 + 600, 0

    def tearDown(self):
        em.set_checkpoint_dir(None)
        jd._rebind_state(self.saved[0])
        jd.PROJECTS, jd.plan_llm, jd.opener_llm, jd._group_store = self.saved[1:]
        self._reset()
        shutil.rmtree(self.td, ignore_errors=True)

    def _reset(self):
        T.fresh()
        getattr(em, "_ASM_RESEAT_DUE", {}).clear()       # a restart forgets a released entry's re-seat as it forgets the entry
        km._parse_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._BG_TOPS_CACHE.clear(); km._task_seg_cache.clear(); km._PLACEMENT_IDX.clear()
        for k in [k for k in em._ASM_STATS if k not in ("full", "fold", "serve", "restore", "bypass", "fallback")]:
            em._ASM_STATS.pop(k, None)

    def parse(self):
        return em.parse_session(self.path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[self.path],
                                states=None, postal_log=[], now=NOW)

    def append(self, boundary=False):
        recs = tail_turn(self.k, self.parent, self.t, boundary=boundary)
        with open(self.path, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
        self.parent, self.t, self.k = recs[-1]["uuid"], self.t + 120, self.k + 1
        return recs

    def restored(self):
        """The kernel's life before the measured one: a whole parse, its document written (a settle), a restart. The first
        parse after it restores from the document: the entry the routine callers' passes meet."""
        tree = self.parse()
        self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
        del tree
        self._reset()
        self.parse()
        key = (os.path.realpath(self.path), SID, False)
        self.assertIsNotNone(em._ASM_CACHE[key].get("docPre"), "the parse after the restart restored from the document")
        ent = em._JSONL_CACHE.get(self.path)
        self.assertTrue(ent is not None and T._tail(ent[4]) or (ent is not None and ent[5] > 0),
                        "the leaf is held from the document's cut or tail-only")
        self._reset_parse_store()

    def _reset_parse_store(self):
        km._parse_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def save_store(self):
        g = {"id": SID + ":g1", "text": "Tighten the retry cap", "parentId": None, "nodeComplete": False, "blocked": False,
             "cleared": False, "trail": [], "t": NOW - 86400}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": g["id"], "closedTurns": [], "nodes": {g["id"]: g}, "placements": {},
             "status": {g["id"]: "working"}, "placementsV": jd.PLACEMENTS_V}))


class RoutineCallersReadNothingBeforeTheWindow(R2Base):
    """Defect 2, one test per caller. A pass: a turn whose compaction lands in the restored entry's tail (the fold cannot
    carry it, g:boundary), then the caller. On 62e03751a the demotion takes the WHOLE parse, which streams every record
    before the window off disk under the caller's name; now it restores from the document with the window pinned to the cut."""

    def _passes(self, call, name, n=2):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.restored()
            call(-1)                                      # the caller's earlier pass: its own folds (the judges' background-task
            #                                               fold) hold cursors, as a fold checkpoint restores them at a live boot
            for i in range(n):
                self.append(boundary=True)
                c0, by0 = cold()
                call(i)
                c1, by1 = cold()
                self.assertEqual(by1.get(name, 0) - by0.get(name, 0), 0,
                                 "pass %d: %s read no record before the window (roads %r)" % (i, name, dict(em._ASM_STATS)))
                self.assertEqual(c1 - c0, 0, "pass %d: nothing at all was read before the window: %r" % (i, {k: v - by0.get(k, 0) for k, v in by1.items() if v != by0.get(k, 0)}))
            self.assertGreaterEqual(em._ASM_STATS.get("g:boundary", 0), n, "each pass demoted: %r" % dict(em._ASM_STATS))
            self.assertGreaterEqual(em._ASM_STATS.get("restore:afterDemote", 0), n,
                                    "each pass's demotion took the restore road: %r" % dict(em._ASM_STATS))

    def test_tasks_for(self):
        self.save_store()
        self._passes(lambda i: jd.tasks_for(SID, self.path, [self.path], NOW + i), "tasks_for")

    def test_plan_session(self):
        self.save_store()
        jd.plan_llm = jd.opener_llm = lambda *a, **k: '{"ops":[]}'
        jd._group_store = lambda *a, **k: None
        self._passes(lambda i: jd._plan_session(SID, self.path, NOW + i), "_plan_session")

    def test_auto_nudge_session(self):
        self.save_store()
        row = {"sid": SID, "path": self.path, "name": "api", "mtime": NOW}
        self._passes(lambda i: km._auto_nudge_session(row, NOW + i, {}, {}, {}, alive_ids={SID}), "_auto_nudge_session")

    def test_bg_placed_tops(self):
        self.save_store()
        self._passes(lambda i: km._bg_placed_tops(SID, self.path, ("toolu_none_%d" % i,)), "_bg_placed_tops")


def _upgrades():
    """{"upgrade<-caller": count} of the reader's whole reads that upgraded a tail entry to the whole file, so far."""
    table = em._RECORD_CACHE_STATS.get("wholeReads") or {}
    return {k: v["count"] for k, v in table.items() if k.startswith("upgrade<-")}


def _ckpt_counter(name, fold):
    """A per-fold checkpoint counter's value for `fold` (0 when the counter or the fold is absent)."""
    v = em.checkpoint_stats().get(name) or {}
    v = v.get(fold, 0)
    return v.get("count", 0) if isinstance(v, dict) else v


class FoldsResumeOverARestoredAssemblyEntry(R2Base):
    """A fold document whose cut lies BEFORE the assembly document's (the live shape on 7 of 8 big transcripts, 2026-10-09: a
    background-task cursor a few hundred records behind the assembly cut). The parse's restore created the leaf's tail entry at
    the assembly cut, the fold's cursor lay before the entry's first held record, the restore returned nothing without a word,
    and the fold read the file whole and walked every record from 0 (an "upgrade" whole read, one refold). Now the entry starts
    at the earlier of the two cuts and the fold resumes over the gap. RoutineCallersReadNothingBeforeTheWindow misses this: its
    first call sets the cursors in-process, so no fold ever resumes from a document there."""

    def append_bg(self, tid):
        """One turn that launches a background shell `tid` (still running at the file's end)."""
        k, p, t = self.k, self.parent, self.t
        recs = [
            {"type": "user", "uuid": "bu%d" % k, "parentUuid": p, "timestamp": iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
             "message": {"role": "user", "content": "start the slow test run %d in the background" % k}},
            {"type": "assistant", "uuid": "bt%d" % k, "parentUuid": "bu%d" % k, "timestamp": iso(t + 5), "cwd": "/w/notes-api",
             "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                 {"type": "tool_use", "id": tid, "name": "Bash",
                  "input": {"command": "make test-slow", "description": "slow tests %d" % k, "run_in_background": True}}]}},
            {"type": "user", "uuid": "br%d" % k, "parentUuid": "bt%d" % k, "timestamp": iso(t + 6), "cwd": "/w/notes-api",
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                      "content": "Command running in background with ID: b%d" % k}]}},
            {"type": "assistant", "uuid": "ba%d" % k, "parentUuid": "br%d" % k, "timestamp": iso(t + 20), "cwd": "/w/notes-api",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "started run %d " % k + "ok " * 30}],
                         "stop_reason": "end_turn"}},
        ]
        with open(self.path, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
        self.parent, self.t, self.k = recs[-1]["uuid"], self.t + 120, self.k + 1
        return recs

    def _fold_doc(self):
        return json.loads(em._ckpt_file(self.path).read_text())

    def _asm_cut(self):
        import gzip
        with gzip.open(em._asm_ckpt_file(self.path), "rt") as f:
            doc = json.load(f)
        return int(doc["files"][Path(self.path).stem]["cut"][1])

    def _fold_then_write(self):
        jd._BG_SCAN_CACHE.clear()
        jd._bg_scan(self.path)
        self.assertTrue(em.checkpoint_write(self.path, force=True), em.checkpoint_stats())
        return int(self._fold_doc()["folds"]["bgJudge"]["count"])

    def _asm_write(self):
        tree = self.parse()
        self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
        del tree
        return self._asm_cut()

    def _ref_tree(self):
        with T.knobs(0, 4, roots=[str(self.proj)]):
            self._reset()
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                return T._strip_tree(self.parse())
            finally:
                em._CKPT_DIR_FN = saved

    def _boot_then_scan(self):
        """A fresh boot: the parse restores from the assembly document, then the judge's background fold resumes. Returns
        the scan's answer and what the scan cost: (answer, records read before the window by the scan, whole-read upgrades,
        bgJudge refolds, bgJudge restores, bgJudge cursors outside the held records, the restored tree)."""
        self._reset()
        jd._BG_SCAN_CACHE.clear()
        tree = self.parse()
        key = (os.path.realpath(self.path), SID, False)
        self.assertIsNotNone(em._ASM_CACHE[key].get("docPre"), "the parse after the restart restored from the document")
        self.base_after_parse = em._JSONL_CACHE[self.path][5]   # where the restore started the leaf's entry
        em.hydrate(tree, SID)
        restored_tree = T._strip_tree(tree)
        c0, by0 = cold()
        up0, rf0 = _upgrades(), _ckpt_counter("refolds", "bgJudge")
        rs0, out0 = _ckpt_counter("restoredFolds", "bgJudge"), _ckpt_counter("cursorOutside", "bgJudge")
        got = jd._bg_scan(self.path)
        c1, by1 = cold()
        up1 = _upgrades()
        return (got, by1.get("_bg_scan", 0) - by0.get("_bg_scan", 0) + (c1 - c0),
                {k: v - up0.get(k, 0) for k, v in up1.items() if v != up0.get(k, 0)},
                _ckpt_counter("refolds", "bgJudge") - rf0, _ckpt_counter("restoredFolds", "bgJudge") - rs0,
                _ckpt_counter("cursorOutside", "bgJudge") - out0, restored_tree)

    def _assert_resumed(self, res):
        got, coldn, ups, refolds, restores, outside, _tree = res
        self.assertEqual(coldn, 0, "the scan read no record before the window")
        self.assertEqual(ups, {}, "no tail entry was upgraded to a whole read")
        self.assertEqual(refolds, 0, "the background fold did not refold the file whole")
        self.assertEqual(restores, 1, "the background fold resumed from its document")
        self.assertEqual(outside, 0, "its cursor lay inside the entry's held records")
        whole = em._scan_bg_tasks(self.path)
        self.assertEqual(got, whole, "the resumed fold answers what a whole fold answers")
        self.assertEqual({t["id"] for t in got}, {"toolu_bg_early", "toolu_bg_gap"}, "both launches, the gap's included")

    def test_a_fold_cut_before_the_assembly_cut_resumes_over_the_gap(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.append_bg("toolu_bg_early")
            fold_cut = self._fold_then_write()
            self.append_bg("toolu_bg_gap")                # launched between the fold's cut and the assembly cut
            for _ in range(3):
                self.append()
            asm_cut = self._asm_write()
            self.assertEqual(int(self._fold_doc()["folds"]["bgJudge"]["count"]), fold_cut, "the fold document stood")
            self.assertLess(fold_cut, asm_cut - 4, "the fixture: the fold's cut lies records before the assembly cut")
            res = self._boot_then_scan()
            self.assertLessEqual(self.base_after_parse, fold_cut, "the restore started the entry at or before the fold's cut")
            self._assert_resumed(res)
            restored_tree = res[-1]
        self.assertEqual(restored_tree, self._ref_tree(), "the assembly restore over the earlier entry equals a whole parse")

    def test_a_fold_cut_after_the_assembly_cut_resumes_as_before(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.append_bg("toolu_bg_early")
            for _ in range(3):
                self.append()
            self.append_bg("toolu_bg_gap")
            asm_cut = self._asm_write()
            self.append()
            fold_cut = self._fold_then_write()
            self.assertGreater(fold_cut, asm_cut, "the control: the fold's cut lies after the assembly cut")
            res = self._boot_then_scan()
            self.assertEqual(self.base_after_parse, asm_cut, "the restore started the entry at the assembly cut, the earlier")
            self._assert_resumed(res)
            restored_tree = res[-1]
        self.assertEqual(restored_tree, self._ref_tree(), "the assembly restore equals a whole parse")


class ACursorOutsideTheHeldRecordsIsCounted(FoldsResumeOverARestoredAssemblyEntry):
    """The restore's quiet refusal made loud: a fold whose document cursor lies outside the reader entry's held records is
    counted under checkpoints.cursorOutside (per fold), and every refold's walk is counted under checkpoints.refoldWalks,
    the one that read nothing (the entry already indexed from 0) included."""

    def _gap_fixture(self):
        self.append_bg("toolu_bg_early")
        fold_cut = self._fold_then_write()
        self.append_bg("toolu_bg_gap")
        for _ in range(3):
            self.append()
        return fold_cut, self._asm_write()

    def test_an_entry_started_after_the_documents_cut_is_counted(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            fold_cut, asm_cut = self._gap_fixture()
            self._reset()
            jd._BG_SCAN_CACHE.clear()
            import gzip
            with gzip.open(em._asm_ckpt_file(self.path), "rt") as f:
                cut = json.load(f)["files"][Path(self.path).stem]["cut"]
            ent = em._read_jsonl_entry(self.path, tail_ok=True, tail_from=(int(cut[0]), int(cut[1]), bytes.fromhex(cut[2])))
            self.assertEqual(ent[5], asm_cut, "an entry made at the assembly cut alone (another road than the restore)")
            out0, rf0 = _ckpt_counter("cursorOutside", "bgJudge"), _ckpt_counter("refoldWalks", "bgJudge")
            got = jd._bg_scan(self.path)
            self.assertEqual(_ckpt_counter("cursorOutside", "bgJudge") - out0, 1, em.checkpoint_stats().get("cursorOutside"))
            self.assertEqual(_ckpt_counter("refoldWalks", "bgJudge") - rf0, 1, "the walk from 0 that followed is counted")
            self.assertEqual(got, em._scan_bg_tasks(self.path))

    def test_a_fold_count_behind_its_documents_cut_is_counted(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.append_bg("toolu_bg_early")
            self._fold_then_write()
            cp = em._ckpt_file(self.path)
            doc = json.loads(cp.read_text())
            doc["folds"]["bgJudge"]["count"] = int(doc["count"]) - 3   # a cursor behind the document's own cut (hand-made)
            cp.write_text(json.dumps(doc))
            self._reset()
            jd._BG_SCAN_CACHE.clear()
            out0 = _ckpt_counter("cursorOutside", "bgJudge")
            got = jd._bg_scan(self.path)
            self.assertEqual(_ckpt_counter("cursorOutside", "bgJudge") - out0, 1, em.checkpoint_stats().get("cursorOutside"))
            self.assertEqual(got, em._scan_bg_tasks(self.path))

    def test_a_refold_that_read_nothing_is_still_a_counted_walk(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.append_bg("toolu_bg_early")
            em._read_jsonl_entry(self.path, tail_ok=True)   # the file indexed from 0 by another reader first
            jd._BG_SCAN_CACHE.clear()
            rf0, w0 = _ckpt_counter("refolds", "bgJudge"), em.checkpoint_stats().get("refoldWalks", {}).get("bgJudge", {})
            jd._bg_scan(self.path)
            w1 = em.checkpoint_stats().get("refoldWalks", {}).get("bgJudge", {})
            self.assertEqual(_ckpt_counter("refolds", "bgJudge") - rf0, 0, "the refold read nothing")
            self.assertEqual(w1.get("count", 0) - w0.get("count", 0), 1, "but its walk is counted")
            self.assertEqual(w1.get("records", 0) - w0.get("records", 0), len(self.recs) + 4, "every record walked")

    # the parent's two tests run once, in the parent
    test_a_fold_cut_before_the_assembly_cut_resumes_over_the_gap = None
    test_a_fold_cut_after_the_assembly_cut_resumes_as_before = None


def _decode_lines():
    """The (file, line) pairs of the reader's record decodes: a traced block allocated under one of them is a decoded record
    (the scan's, or a streaming pass's before a window)."""
    out = set()
    for code in (em._scan_jsonl_stream.__code__, em._TailRecords._decode_run.__code__):
        out |= {(code.co_filename, ln) for _a, _b, ln in code.co_lines() if ln}
    return out


def held_record_bytes(snap, lines):
    return sum(st.size for st in snap.statistics("traceback")
               if any((fr.filename, fr.lineno) in lines for fr in st.traceback))


def entry_record_bytes(entry, lines):
    """The decode-site bytes the record cache's entry itself keeps (its window records, the dropped records' skeletons and
    the strings they share, its index arrays), by walking what the entry references while tracemalloc runs. Measured in the
    same process as held_record_bytes, so a version's object sizes weigh on both sides alike."""
    seen, stack, total = set(), [], 0
    for a in dir(entry):
        v = getattr(entry, a, None)
        if not a.startswith("__") and isinstance(v, (list, tuple, dict, collections.deque, array.array)):
            stack.append(v)
    while stack:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        tb = tracemalloc.get_object_traceback(o)
        if tb is not None and any((fr.filename, fr.lineno) in lines for fr in tb):
            total += sys.getsizeof(o)
        if isinstance(o, dict):
            stack.extend(o.keys()); stack.extend(o.values())
        elif isinstance(o, (list, tuple, collections.deque)):
            stack.extend(o)
    return total


class AnIdleLeafReleasesItsRecordsOnceItsDocumentStands(R2Base):
    """Defect 1. An idle leaf the boot parsed whole: the kernel's display parse holds its tree, the converge pass writes its
    document. On 62e03751a the whole assembly entry and the store's tree stayed (the next parse would re-seat, and an idle
    leaf is never parsed again), so every decoded record stayed alive though the record cache counted only the window. Now the
    writer releases the entry and the kernel replaces the tree with a restore: the decoded records still held fall to the
    window's (measured by tracemalloc over the reader's decode sites).

    The bound is what the record cache's entry keeps by design, measured on the same snapshot's Python: its window records
    plus the dropped records' skeletons, which weigh about a quarter of the whole leaf's decode. It was a fixed 0.3 of the
    bytes before, which 3.12 met by 1.4 percent and 3.10 missed (0.305), its objects 14 to 18 percent heavier and every
    count the same (2,349 decode-site objects in the entry on both, nothing held outside it)."""

    def test_the_held_decoded_records_fall_to_the_window(self):
        old = time.time() - 600
        os.utime(self.path, (old, old))                  # quiescent: the converge pass's leaf
        lines = _decode_lines()
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tracemalloc.start(40)
            try:
                km._parse(self.path, SID, NOW)
                gc.collect()
                before = held_record_bytes(tracemalloc.take_snapshot(), lines)
                for name, val in (("CKPT_CONVERGE_MS", 5000.0), ("CKPT_CONVERGE_BYTES", em._CKPT_CYCLE_CAP_DEFAULT),
                                  ("ASM_CONVERGE", True)):
                    saved = getattr(km, name); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
                km._ASM_CONVERGE_DONE.clear(); km._ASM_CONVERGE_BLIP.clear(); km._ASM_CONVERGE_NOENTRY.clear()
                km._begin_checkpoint_cycle()
                self.assertEqual(km._converge_assembly(time.time(), time.monotonic()), 1, em.asm_checkpoint_stats())
                gc.collect()
                after = held_record_bytes(tracemalloc.take_snapshot(), lines)
                kept = entry_record_bytes(em._JSONL_CACHE[self.path][4], lines)
            finally:
                tracemalloc.stop()
            hot = em._JSONL_CACHE[self.path][4]
            hot_n = len(hot.hot) if T._tail(hot) else len(hot)
            tree = jd._parse_slot(SID, jd._pending_cut(SID), self.path, km._display_sdk_human(SID))
            self.assertIsNone(tree, "the store's tree built from the whole entry was dropped")
            s0 = dict(em._ASM_STATS)
            km._parse(self.path, SID, NOW)                # the next ask restores from the document just written
            self.assertEqual(em._ASM_STATS.get("restore", 0) - s0.get("restore", 0), 1)
            self.assertEqual(em._ASM_STATS.get("full", 0), s0.get("full", 0), "never a whole parse")
        self.assertGreater(before, 0)
        self.assertLess(hot_n, len(self.recs) // 4, "the entry's records are the window, not the leaf")
        self.assertLess(after, 1.1 * kept, "decoded records held outside the record cache's entry: %d bytes held after the "
                        "document, %d kept by the entry (the window holds %d of %d records), %d before"
                        % (after, kept, hot_n, len(self.recs), before))
        self.assertLess(after, 0.5 * before, "decoded records held: %d bytes after the document, %d before" % (after, before))


class ALargeLeafsDocumentIsWritten(R2Base):
    """Defect 1's cause on the devbox: a large leaf's document exceeded the flat cap, so it was never written and the entry
    stayed whole. Scaled here: the flat cap shrunk to 2 KiB, under this leaf's document and over 1/16 of its pre-cut bytes'
    worth of nothing. A tail-only leaf's document is written (the cap is 1/16 of its pre-cut bytes); with the rule off, the
    same leaf still refuses at the flat cap (the control)."""

    def _write(self, tail_bytes):
        saved = em._ASM_CKPT_CAP, getattr(em, "_ASM_CKPT_CAP_SHARE", None)
        em._ASM_CKPT_CAP = 2048
        em._ASM_CKPT_CAP_SHARE = 2                        # this fixture's records are tiny (its document is 7 percent of it; the
        #                                                   devbox's large leaves measured 0.7 to 3.3 percent), so the share scales too
        try:
            with T.knobs(tail_bytes, 4, roots=[str(self.proj)]):
                tree = self.parse()
                reasons = []
                ok = em.asm_checkpoint_write(self.path, SID, tree=tree, reason_out=reasons)
                cp = em._asm_ckpt_file(self.path)
                return ok, reasons, (cp.stat().st_size if cp.exists() else None)
        finally:
            em._ASM_CKPT_CAP = saved[0]
            if saved[1] is None:
                del em._ASM_CKPT_CAP_SHARE
            else:
                em._ASM_CKPT_CAP_SHARE = saved[1]

    def test_a_tail_only_leaf_writes_past_the_flat_cap(self):
        ok, reasons, size = self._write(WINDOW)
        self.assertTrue(ok, "written: %r" % reasons)
        self.assertGreater(size, 2048, "the document is over the flat cap")
        self.assertLessEqual(size, os.path.getsize(self.path) // 2)

    def test_control_the_rule_off_keeps_the_flat_cap(self):
        ok, reasons, size = self._write(0)
        self.assertFalse(ok)
        self.assertEqual(reasons, ["oversize"])


class TheNewRestoreRoadsEqualAWholeParse(R2Base):
    """The demotions a restored tail-only entry now takes to the restore road give the tree a cold whole parse gives (the
    rule off, no checkpoint directory), hydrated; and they take that road (on 62e03751a: the whole parse)."""

    def _ref(self):
        with T.knobs(0, 4, roots=[str(self.proj)]):
            self._reset()
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                return T._strip_tree(self.parse())
            finally:
                em._CKPT_DIR_FN = saved

    def _check(self, mutate, reason):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.restored()
            mutate()
            c0, _ = cold()
            s0 = dict(em._ASM_STATS)
            tree = self.parse()
            d = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
            self.assertEqual(d.get("g:" + reason, 0), 1, "the fold demoted for %s: %r" % (reason, d))
            self.assertEqual(d.get("restore:afterDemote", 0), 1, "…and restored, never parsed whole: %r" % d)
            self.assertNotIn("full", d)
            self.assertEqual(cold()[0] - c0, 0, "nothing read before the window")
            em.hydrate(tree, SID)
            got = T._strip_tree(tree)
        self.assertEqual(got, self._ref())

    def test_a_compaction_in_the_tail(self):
        self._check(lambda: self.append(boundary=True), "boundary")

    def test_a_record_written_twice_in_the_tail(self):
        def mutate():
            recs = self.append()
            self.parse()                                  # folded: the record is in the entry's graph
            with open(self.path, "a") as f:
                f.write(json.dumps(recs[-1]) + "\n")      # the CLI re-writes a record (a verbatim duplicate)
            self.append()
        self._check(mutate, "uuid-known")


class TheWindowStaysAtARestoredCut(R2Base):
    """The record window of a leaf with a live restored entry never starts after the entry's cut, a window that slid past it
    already comes back once (the gap read once, counted), and the window slides again when the entry goes. A 400-turn leaf
    (768 KB): its churn bound (1/8 of the pre-cut bytes, about 95 KB of tail) stays far off while the tail passes the 32 KiB
    the rule windows from."""
    TURNS = 400

    def _cut_index(self):
        key = (os.path.realpath(self.path), SID, False)
        return em._ASM_CACHE[key]["docCutOff"]

    def test_appends_past_the_window_keep_the_cut_and_a_whole_parse_releases_it(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.restored()
            cut = self._cut_index()
            for _ in range(70):                           # about 40 KB: past the 32 KiB span the rule windows, past the window
                self.append()
                self.parse()
            ent = em._JSONL_CACHE[self.path]
            first = ent[4].hot_offset() if T._tail(ent[4]) else int(ent[7][0])
            self.assertLessEqual(first, cut, "the window still starts at or before the restored cut")
            self.assertEqual(em.tail_pin_stats().get(self.path), cut)
            em.evict_document(self.path)                  # the entry goes (its release unpins)
            self.assertIsNone(em.tail_pin_stats().get(self.path))

    def test_a_window_that_slid_past_the_cut_comes_back_once(self):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tree = self.parse()
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree), em.asm_checkpoint_stats())
            del tree
            for _ in range(40):                           # about 23 KB: the window of a whole read starts past the cut
                self.append()
            self._reset()
            em._read_jsonl_entry(self.path)               # a whole reader first: a tail-only entry whose window starts past the cut
            ent = em._JSONL_CACHE[self.path]
            self.assertTrue(T._tail(ent[4]))
            self.parse()                                  # the restore pins and widens
            c0, _ = cold()
            for _ in range(3):
                self.append(boundary=True)                # each a demotion and a restore over the pinned span
                self.parse()
            self.assertEqual(cold()[0] - c0, 0, "after the one widening read, nothing before the window")


class OnlyAWholeParseAnswersWhatMovesAFrozenAtom(R2Base):
    """Review of 2026-10-08 (R1-1, R1-2, p5): the restore road was picked from an append's FIRST demoting record, so a later
    record only a whole parse can answer (a prompt stamped before the cut) was never read; the boot restore had the same hole;
    a resurrected dangling target and a summary off any boundary restored too. Each shape now gives the tree a cold whole
    parse gives (the restore refused or never taken). On 926f9199f each tree differs (turn 6 loses its late prompt; 164 turns
    where the cold parse has 124)."""

    def _ref(self):
        with T.knobs(0, 4, roots=[str(self.proj)]):
            self._reset()
            saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
            try:
                return T._strip_tree(self.parse())
            finally:
                em._CKPT_DIR_FN = saved

    def _stale(self, parent, k):
        """A prompt stamped inside turn 5 (long before the cut) and its reply, chained at the tail."""
        u = {"type": "user", "uuid": "old%d" % k, "parentUuid": parent, "timestamp": iso(NOW - 86400 + 5 * 60 + 30),
             "promptSource": "typed", "cwd": "/w/notes-api", "message": {"role": "user", "content": "a late-stamped prompt %d" % k}}
        a = {"type": "assistant", "uuid": "olda%d" % k, "parentUuid": u["uuid"], "timestamp": iso(self.t + 30), "cwd": "/w/notes-api",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "late reply ok " * 10}], "stop_reason": "end_turn"}}
        return [u, a]

    def _write(self, recs):
        with open(self.path, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))

    def _tree(self, mutate, boot=False):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.restored()
            mutate()
            if boot:
                self._reset()                             # a restart over the grown tail: the boot's restore road
            tree = self.parse()
            em.hydrate(tree, SID)
            got = T._strip_tree(tree)
        return got

    def test_a_compaction_then_a_stale_stamp_in_one_append(self):
        def mutate():
            recs = tail_turn(self.k, self.parent, self.t, boundary=True)
            self._write(recs + self._stale(recs[-1]["uuid"], 1))
        self.assertEqual(self._tree(mutate), self._ref())

    def test_a_duplicate_then_a_stale_stamp_in_one_append(self):
        def mutate():
            recs = self.append()
            self.parse()
            self._write([recs[-1]] + self._stale(recs[-1]["uuid"], 2))
        self.assertEqual(self._tree(mutate), self._ref())

    def test_a_boot_restore_over_a_stale_stamp(self):
        self.assertEqual(self._tree(lambda: self._write(self._stale(self.parent, 6)), boot=True), self._ref())

    def test_a_summary_off_any_boundary(self):
        def mutate():
            self._write([{"type": "user", "uuid": "lone_s", "parentUuid": self.parent, "timestamp": iso(self.t),
                          "isCompactSummary": True, "message": {"role": "user", "content": "summary so far: a lone summary"}}])
            self.parent, self.t = "lone_s", self.t + 2
            self.append()
        self.assertEqual(self._tree(mutate), self._ref())


class ADanglingTargetResurrectedInTheTail(OnlyAWholeParseAnswersWhatMovesAFrozenAtom):
    """A pre-cut compaction whose stitch target was never written (the parse repairs it through preservedSegment); the target
    then lands in the tail. A cold parse rebinds the stitch (124 turns); the restore froze it (164)."""

    def setUp(self):
        super().setUp()
        for r in self.recs:
            if r.get("uuid") == "b40":
                real = r["logicalParentUuid"]
                r["logicalParentUuid"] = "ghost40"
                r["compactMetadata"]["preservedSegment"] = {"tailUuid": real, "anchorUuid": real, "headUuid": real}
        Path(self.path).write_text("".join(json.dumps(x) + "\n" for x in self.recs))

    def _resurrect(self):
        self._write([{"type": "user", "uuid": "ghost40", "parentUuid": self.parent, "timestamp": iso(self.t), "promptSource": "typed",
                      "message": {"role": "user", "content": "a record whose uuid a pre-cut stitch named"}}])
        self.parent, self.t = "ghost40", self.t + 30
        self.append()

    def test_at_a_demotion(self):
        self.assertEqual(self._tree(self._resurrect), self._ref())

    def test_at_a_boot(self):
        self.assertEqual(self._tree(self._resurrect, boot=True), self._ref())


class _Roads(R2Base):
    """Helpers: the tree after a mutation (and optionally a restart), with the parse's counters, against a cold whole parse."""
    _ref = OnlyAWholeParseAnswersWhatMovesAFrozenAtom._ref
    _stale = OnlyAWholeParseAnswersWhatMovesAFrozenAtom._stale
    _write = OnlyAWholeParseAnswersWhatMovesAFrozenAtom._write

    def _go(self, mutate, boot=False):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            self.restored()
            mutate()
            if boot:
                self._reset()
            s0 = dict(em._ASM_STATS)
            tree = self.parse()
            d = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
            em.hydrate(tree, SID)
            got = T._strip_tree(tree)
        return d, got

    def _boundary(self, k, t):
        return [{"type": "system", "subtype": "compact_boundary", "uuid": "xb%d" % k, "parentUuid": None,
                 "logicalParentUuid": self.parent, "timestamp": iso(t),
                 "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}},
                {"type": "user", "uuid": "xs%d" % k, "parentUuid": "xb%d" % k, "timestamp": iso(t + 1), "isCompactSummary": True,
                 "message": {"role": "user", "content": "summary so far: %d" % k}}]

    def _orphan(self, u, parent, t):
        return {"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "isCompactSummary": True,
                "message": {"role": "user", "content": "summary so far: an orphan %s" % u}}


class TheSecondReviewsShapes(_Roads):
    """Second review of 2026-10-08 (N1 to N4): each gives the cold whole parse's tree. On c8219856f: N1 and N2 keep the old
    summary at the pre-cut card (the orphan rule ran on an append's first hit only), N3 the same at a boot, N4 and N4b keep a
    prompt where the cold parse puts the compaction card (the restore read user and assistant stamps only)."""

    def _stale_compaction(self):
        recs = self._boundary(1, self.t)                  # the boundary alone stamped inside turn 150, before the document's
        recs[0]["timestamp"] = iso(NOW - 86400 + 150 * 60 + 30)   # watermark; its summary and the next turn current
        self._write(recs)
        self.parent = recs[-1]["uuid"]
        self.append()

    def test_n4_a_compaction_stamped_before_the_watermark(self):
        self.assertEqual(self._go(self._stale_compaction)[1], self._ref())

    def test_n4b_the_same_at_a_boot(self):
        d, got = self._go(self._stale_compaction, boot=True)
        self.assertEqual(d.get("restore:stampRefused"), 1, d)
        self.assertEqual(got, self._ref())

    def test_n1_a_duplicate_then_an_orphan_summary(self):
        def mutate():
            recs = self.append()
            self.parse()
            self._write([recs[-1], self._orphan("o1", recs[-1]["uuid"], self.t + 5)])
            self.parent, self.t = "o1", self.t + 10
            self.append()
        d, got = self._go(mutate)
        self.assertEqual(d.get("g:summary:orphan"), 1, "the rest of the append was read: %r" % d)
        self.assertEqual(got, self._ref())

    def test_n2_a_compaction_then_an_orphan_summary_stamped_before_it(self):
        def mutate():
            b = self._boundary(2, self.t + 20)            # the orphan chains on the new summary (no boundary), stamped after the
            self._write(b + [self._orphan("o2", b[-1]["uuid"], self.t + 10)])   # watermark and before the new boundary
        d, got = self._go(mutate)
        self.assertEqual(got, self._ref())

    def test_n3_a_boot_restore_over_an_orphan_summary(self):
        def mutate():
            self._write([self._orphan("o3", self.parent, self.t)])
            self.parent, self.t = "o3", self.t + 5
            self.append()
        d, got = self._go(mutate, boot=True)
        self.assertEqual(d.get("restore:summaryRefused"), 1, d)
        self.assertEqual(got, self._ref())


class APreCutTwinAndPayload(_Roads):
    """A typed slash command's raw twin (u10, promptId pid-pre) and a Skill payload record (u12, linked to toolu_pre_sk) before
    the cut. A command wrapper wearing pid-pre, or a Skill tool_use on toolu_pre_sk, in the tail re-classifies the pre-cut record:
    only a whole parse answers it. After a compaction in the same append, the rest of the append is read (the demotion is named
    promptid or skill-link and no restore is attempted: these fail with _asm_rest_whole's body removed); at a boot, the restore
    refuses it from the document's pre-cut gate sets. On c8219856f the boot cases restore a different tree (163 against 162 and
    164 against 163 turns)."""
    WRAP = "<command-name>/review</command-name>\n<command-message>review</command-message>\n<command-args></command-args>"

    def setUp(self):
        super().setUp()
        for r in self.recs:
            if r.get("uuid") == "u10":
                r["promptId"] = "pid-pre"
                r["message"]["content"] = "/review"
            if r.get("uuid") == "u12":
                r["sourceToolUseID"] = "toolu_pre_sk"
                r["message"]["content"] = "instructions for the deploy skill"
        Path(self.path).write_text("".join(json.dumps(x) + "\n" for x in self.recs))

    def _wrapper(self, parent):
        return {"type": "user", "uuid": "wr1", "parentUuid": parent, "timestamp": iso(self.t + 40), "promptId": "pid-pre",
                "cwd": "/w/notes-api", "message": {"role": "user", "content": self.WRAP}}

    def _skill(self, parent):
        return {"type": "assistant", "uuid": "sk1", "parentUuid": parent, "timestamp": iso(self.t + 50), "cwd": "/w/notes-api",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_pre_sk", "name": "Skill",
                                                               "input": {"skill": "deploy"}}], "stop_reason": "tool_use"}}

    def _after_compaction(self, rec):
        def mutate():
            recs = tail_turn(self.k, self.parent, self.t, boundary=True)
            self._write(recs + [rec(recs[-1]["uuid"])])
        return mutate

    def test_a_wrapper_after_a_compaction(self):
        d, got = self._go(self._after_compaction(self._wrapper))
        self.assertEqual((d.get("g:promptid"), d.get("restore:afterDemote")), (1, None), d)
        self.assertEqual(got, self._ref())

    def test_a_skill_link_after_a_compaction(self):
        d, got = self._go(self._after_compaction(self._skill))
        self.assertEqual((d.get("g:skill-link"), d.get("restore:afterDemote")), (1, None), d)
        self.assertEqual(got, self._ref())

    def test_a_wrapper_at_a_boot(self):
        d, got = self._go(lambda: self._write([self._wrapper(self.parent)]), boot=True)
        self.assertEqual(d.get("restore:promptIdRefused"), 1, d)
        self.assertEqual(got, self._ref())

    def test_a_skill_link_at_a_boot(self):
        d, got = self._go(lambda: self._write([self._skill(self.parent)]), boot=True)
        self.assertEqual(d.get("restore:skillRefused"), 1, d)
        self.assertEqual(got, self._ref())


class ARefusedStampHeals(_Roads):
    """Second review (3): a restore refused for a stamp left the document as it was, so an idle leaf paid the refusal and a
    whole parse at every boot. Now the whole parse after the refusal rewrites the document (write:afterRefusal), and the next
    boot restores from it: no refusal, no whole parse, the cold parse's tree. On c8219856f the next boot refuses again.
    (The writer keeps stamps in order across its cut, so a record stamped early but written last puts the new cut before it.)
    Third review (2026-10-09): a stamp far back steps that cut so far that the tail is over the churn bound's share, and the
    restored entry then demoted at its first fold and parsed whole: two whole-parse equivalents a boot. That rewrite now
    declines (refusalTail) and the leaf is marked at its stat, so a later boot pays one whole parse and no write; a stamp
    near the cut (here inside turn 150, ten turns back) still heals."""

    def _stale_at(self, parent, k, t):
        u = {"type": "user", "uuid": "old%d" % k, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed",
             "cwd": "/w/notes-api", "message": {"role": "user", "content": "a late-stamped prompt %d" % k}}
        a = {"type": "assistant", "uuid": "olda%d" % k, "parentUuid": u["uuid"], "timestamp": iso(self.t + 30), "cwd": "/w/notes-api",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "late reply ok " * 10}], "stop_reason": "end_turn"}}
        return [u, a]

    def _boots(self, recs):
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            tree = self.parse()
            self.assertTrue(em.asm_checkpoint_write(self.path, SID, tree=tree))
            del tree
            self._write(recs)
            old = time.time() - 600
            os.utime(self.path, (old, old))               # idle: nothing appends after this
            self._reset()
            s0 = dict(em._ASM_STATS)
            km._parse(self.path, SID, NOW)                # boot one
            d1 = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
            self._reset()
            s0, w0 = dict(em._ASM_STATS), em._ASM_CKPT_STATS.get("written", 0)
            tree = self.parse()                           # boot two
            d2 = {k: v - s0.get(k, 0) for k, v in em._ASM_STATS.items() if v != s0.get(k, 0)}
            d2["_written"] = em._ASM_CKPT_STATS.get("written", 0) - w0
            em.hydrate(tree, SID)
            got = T._strip_tree(tree)
        return d1, d2, got

    def test_the_next_boot_restores(self):
        d1, d2, got = self._boots(self._stale_at(self.parent, 9, NOW - 86400 + 150 * 60 + 30))
        self.assertEqual(d1.get("restore:stampRefused"), 1, d1)
        self.assertEqual(d1.get("write:afterRefusal"), 1, d1)
        self.assertEqual((d2.get("restore"), d2.get("full", 0), d2.get("restore:stampRefused")), (1, 0, None), d2)
        self.assertEqual(got, self._ref())

    def test_a_stamp_far_back_marks_the_leaf(self):
        d1, d2, got = self._boots(self._stale(self.parent, 9))
        self.assertEqual((d1.get("restore:stampRefused"), d1.get("write:afterRefusalSkipped")), (1, 1), d1)
        self.assertEqual((d2.get("restore:refusedStanding"), d2.get("full", 0), d2["_written"]), (1, 1, 0), d2)
        self.assertEqual(got, self._ref())


class TheDanglingShortCut(_Roads):
    """The dangling target's own demotion takes the whole parse without a restore attempt (the chain proof would refuse it too,
    so the tree alone cannot tell): pinned by its counters."""
    _resurrect = ADanglingTargetResurrectedInTheTail._resurrect

    def setUp(self):
        super().setUp()
        for r in self.recs:
            if r.get("uuid") == "b40":
                real = r["logicalParentUuid"]
                r["logicalParentUuid"] = "ghost40"
                r["compactMetadata"]["preservedSegment"] = {"tailUuid": real, "anchorUuid": real, "headUuid": real}
        Path(self.path).write_text("".join(json.dumps(x) + "\n" for x in self.recs))

    def test_no_restore_is_attempted(self):
        d, got = self._go(self._resurrect)
        self.assertEqual((d.get("g:dangling"), d.get("restore:chainRefused")), (1, None), d)
        self.assertEqual(got, self._ref())


SID2 = "aaaaaaaa-4444-4222-8333-666666666666"


class TheConvergePassReachesALeafLargerThanItsBudget(R2Base):
    """Review of 2026-10-08 (R2-1): the converge pass took a leaf's write against the cycle's byte budget with an estimate of
    its size / 64, and broke out of its loop at the first refusal. A leaf whose estimate passes the cap (any leaf past 512 MiB
    under the 8 MiB default: the devbox's largest idle leaves) was never written, and every whole entry after it waited forever.
    Scaled here: the cap one byte under the first leaf's estimate. Now the first candidate of a cycle may take it alone, a
    refused leaf is owed and skipped, and both leaves get their documents and are released within three passes. On 926f9199f
    neither is written."""

    def test_the_large_leaf_and_the_one_behind_it_both_converge(self):
        small = str(self.proj / (SID2 + ".jsonl"))
        recs2 = transcript(NOW - 86400, turns=100, compact_every=40)
        Path(small).write_text("".join(json.dumps(r) + "\n" for r in recs2))   # another session: its own leaf
        old = time.time() - 600
        for p in (self.path, small):
            os.utime(p, (old, old))                       # quiescent: the converge pass's leaves
        est = max(4096, os.path.getsize(self.path) // 64)
        self.assertLessEqual(max(4096, os.path.getsize(small) // 64), est - 1, "the small leaf fits the cap alone")
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            km._parse(self.path, SID, NOW)                # the large leaf first: first in the pass's order
            km._parse(small, SID2, NOW)
            self.assertEqual(len(em.asm_whole_entries()), 2)
            for name, val in (("CKPT_CONVERGE_MS", 5000.0), ("CKPT_CONVERGE_BYTES", est - 1), ("ASM_CONVERGE", True)):
                saved = getattr(km, name); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
            for t in (km._ASM_CONVERGE_DONE, km._ASM_CONVERGE_BLIP, km._ASM_CONVERGE_NOENTRY, getattr(km, "_ASM_CONVERGE_OWED", {})):
                t.clear()
            written = 0
            for _ in range(3):
                km._begin_checkpoint_cycle()
                written += km._converge_assembly(time.time(), time.monotonic())
            self.assertTrue(em._asm_ckpt_file(self.path).exists(), "the leaf larger than the budget got its document")
            self.assertTrue(em._asm_ckpt_file(small).exists(), "the leaf behind it was not held")
            self.assertEqual(written, 2)
            self.assertEqual(em.asm_whole_entries(), [], "both whole entries released")


class TheConvergePassDoesNotStopAtALeafTooLarge(R2Base):
    """Second review (4): the small leaf behind a leaf over the budget is written in the FIRST pass (the large one is owed and
    skipped); with the old break back, the small leaf waits. Then the owed leaf takes the next cycle alone."""

    def test_one_pass(self):
        small = str(self.proj / (SID2 + ".jsonl"))
        Path(small).write_text("".join(json.dumps(r) + "\n" for r in transcript(NOW - 86400, turns=100, compact_every=40)))
        old = time.time() - 600
        for p in (self.path, small):
            os.utime(p, (old, old))
        est = max(4096, os.path.getsize(self.path) // 64)
        with T.knobs(WINDOW, 4, roots=[str(self.proj)]):
            km._parse(self.path, SID, NOW)
            km._parse(small, SID2, NOW)
            for name, val in (("CKPT_CONVERGE_MS", 5000.0), ("CKPT_CONVERGE_BYTES", est - 1), ("ASM_CONVERGE", True)):
                saved = getattr(km, name); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
            for t in (km._ASM_CONVERGE_DONE, km._ASM_CONVERGE_BLIP, km._ASM_CONVERGE_NOENTRY, getattr(km, "_ASM_CONVERGE_OWED", {})):
                t.clear()
            km._begin_checkpoint_cycle()
            km._converge_assembly(time.time(), time.monotonic())
            self.assertTrue(em._asm_ckpt_file(small).exists(), "the small leaf was written in the first pass")
            self.assertFalse(em._asm_ckpt_file(self.path).exists(), "the large one waits a cycle (owed)")
            km._begin_checkpoint_cycle()
            km._converge_assembly(time.time(), time.monotonic())
            self.assertTrue(em._asm_ckpt_file(self.path).exists(), "the owed leaf took the next cycle alone")


class TheHydrationMemoKeepsWhatAtomsRead(unittest.TestCase):
    """The hydration memo held every hydrated record whole: a tool result's output twice (its message block and
    toolUseResult) and its metadata, beside the message the atom shares. It keeps the fields the atom's kind reads; an atom of
    the same uuid that needs a dropped field reads the record again (on 62e03751a the helpers do not exist)."""

    REC = {"type": "user", "uuid": "u-tr", "parentUuid": "a-tr", "timestamp": "2026-10-07T00:00:00.000Z", "cwd": "/w/notes-api",
           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "out " * 50}]},
           "toolUseResult": {"stdout": "out " * 50, "stderr": ""}}

    def setUp(self):
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.pop("u-tr", None)

    tearDown = setUp

    def test_a_tool_result_without_tur_keeps_no_second_copy(self):
        keep = em._hydrated_keep(self.REC, {"k": "u"})
        self.assertNotIn("toolUseResult", keep)
        self.assertNotIn("cwd", keep)
        self.assertIs(keep["message"], self.REC["message"])
        with em._ASM_CKPT_LOCK:
            em._HYDRATED["u-tr"] = (keep, 100)
            self.assertIsNotNone(em._hydrated_hit("u-tr", {"uuid": "u-tr", "lazy": {"k": "u"}}))
            self.assertIsNone(em._hydrated_hit("u-tr", {"uuid": "u-tr", "lazy": {"k": "u", "tur": True}}),
                              "an atom that needs the dropped toolUseResult reads the record again")

    def test_with_tur_the_atom_gets_it(self):
        keep = em._hydrated_keep(self.REC, {"k": "u", "tur": True})
        a = {"uuid": "u-tr", "lazy": {"k": "u", "tur": True}}
        em._hydrate_one(a, keep)
        self.assertEqual(a["toolUseResult"], self.REC["toolUseResult"])
        self.assertEqual(a["message"], em._norm_message(self.REC["message"]))


if __name__ == "__main__":
    unittest.main()
