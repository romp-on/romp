#!/usr/bin/env python3
"""T391 (2026-09-12): the judges' incident scan walked every dead episode file of a lineage whole at every boot (`_per_file_rewound`
through `em.file_rewound` with no rompuuid: 542 MB in four reads on one devbox boot). The verdict set per FROZEN file is now the
fold `rewoundUuids` of that file's fold document: written from the walk's own read at the quiescence drop, restored at the next
process, retired by an append or rewrite, capped like every fold state, falling to the walk on a corrupt document. Synthetic
transcripts only (the golden builders)."""
import gzip
import inspect
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import em, G, SID, NOW, Harness, kernel_module, compacting_variant   # noqa: E402  the harness and the golden builders


class RewoundMemo(Harness):
    def setUp(self):
        super().setUp()
        em._REWOUND_CACHE.clear()
        with em._CKPT_LOCK:
            for k in em._REWOUND_STATS:
                em._REWOUND_STATS[k] = 0
            em._FOLD_DIRTY.clear()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        self.saved_drop = em._DROP_AFTER_QUIESCENT_S

    def tearDown(self):
        em._DROP_AFTER_QUIESCENT_S = self.saved_drop
        super().tearDown()

    def frozen(self, name="dead", scenario="rewind_off_path", age=600):
        """A dead episode's transcript: a scenario with a rewound branch, idle past the reader's quiescence window."""
        records, sent = G.SINGLE_FILE[scenario]
        path = self.write(name, records(), sent=sent)
        old = time.time() - age; os.utime(path, (old, old))
        return path

    def fresh_process(self):
        self.fresh(); em.set_checkpoint_dir(lambda: self.ck)
        for c in list(em._FOLD_REG.values()):
            c.clear()
        em._REWOUND_CACHE.clear()
        with em._CKPT_LOCK:
            for k in em._REWOUND_STATS:
                em._REWOUND_STATS[k] = 0

    def whole_reads(self, path):
        return sum(v["bytes"] for k, v in em.record_cache_stats()["wholeReads"].items())

    def test_a_frozen_file_is_walked_once_and_served_from_its_document_at_the_next_process(self):
        path = self.frozen()
        size = os.path.getsize(path)
        walked = em.file_rewound(path)                                # the verdicts the plain walk gives (the equivalence reference)
        self.assertTrue(walked, "the fixture has a rewound branch")
        self.fresh_process()
        got = em.rewound_uuids(path)
        self.assertEqual(got, walked, "the first call walks and answers the walk's verdicts")
        self.assertGreaterEqual(self.whole_reads(path), size, "the first process read the file whole, once")
        d = json.loads(em._ckpt_file(path).read_text())
        self.assertEqual(sorted(d["folds"]["rewoundUuids"]["state"]["uuids"]), sorted(walked), "the memo rides the fold document, written at the drop")
        st = em.rewound_memo_stats(); self.assertEqual((st["walked"], st["served"]), (1, 0))
        self.fresh_process()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        read0 = em.read_bytes_report().get(path, 0)
        got2 = em.rewound_uuids(path)
        self.assertEqual(got2, walked, "served from the document: the same verdicts")
        self.assertEqual(self.whole_reads(path), 0, "no whole read at the next process")
        self.assertLess(em.read_bytes_report().get(path, 0) - read0, 512, "a tail read of nothing plus the guard: %d" % (em.read_bytes_report().get(path, 0) - read0))
        st = em.rewound_memo_stats(); self.assertEqual((st["walked"], st["served"]), (0, 1))

    def test_a_file_that_grows_after_the_memo_is_walked_again_and_the_memo_rewritten(self):
        path = self.frozen("grows")
        self.fresh_process(); first = em.rewound_uuids(path)
        seq = json.loads(em._ckpt_file(path).read_text())["seq"]
        records, _sent = G.SINGLE_FILE["rewind_off_path"]
        recs = records(); last = next(r for r in reversed(recs) if r.get("uuid"))
        t1 = max((em.parse_z(r.get("timestamp")) or 0) for r in recs if r.get("timestamp")) + 60
        with open(path, "a") as fh:
            fh.write(json.dumps(G.uline(t1, "one more question after the fact", "u_more", last["uuid"])) + "\n")
        old = time.time() - 600; os.utime(path, (old, old))            # frozen again, larger
        self.fresh_process()
        got = em.rewound_uuids(path)
        self.assertEqual(got, em.file_rewound(path), "the walk ran again over the grown file")
        st = em.rewound_memo_stats(); self.assertEqual((st["walked"], st["served"]), (1, 0), "%s" % st)
        d = json.loads(em._ckpt_file(path).read_text())
        self.assertGreater(d["seq"], seq, "the memo was rewritten")
        self.assertEqual(sorted(d["folds"]["rewoundUuids"]["state"]["uuids"]), sorted(got))

    def test_the_scan_takes_the_leaf_road_for_the_leaf_and_the_memo_for_a_dead_file(self):
        km = kernel_module(); jd = km.jd
        src = inspect.getsource(jd._per_file_rewound)
        self.assertIn("em.file_rewound(fp, rompuuid=fsid", src, "the leaf: the document's pre-cut verdicts and the tail")
        self.assertIn("em.rewound_uuids(fp, drop=fp not in lineage and not _sdk_owned(fp.stem))", src, "a dead file: the memo, its entry dropped; a lineage file or a registered session's own file resident")
        self.assertLess(src.index("if fp == leaf and len(files) == 1 and em.asm_document_seeds(fp):"), src.index("em.rewound_uuids(fp, drop="),
                        "the leaf road decided first, and only for a leaf with an assembly document")

    def test_an_over_cap_set_is_recorded_as_such_and_walked_again(self):
        path = self.frozen("over")
        saved = em._CKPT_FOLD_CAP; em._CKPT_FOLD_CAP = 8                # a cap below any set: the memo is over it
        self.addCleanup(setattr, em, "_CKPT_FOLD_CAP", saved)
        self.fresh_process(); first = em.rewound_uuids(path)
        d = json.loads(em._ckpt_file(path).read_text())
        self.assertIn("over", d["folds"]["rewoundUuids"], "the cursor without its state, with the reason: %s" % d["folds"]["rewoundUuids"])
        self.fresh_process()
        got = em.rewound_uuids(path)
        self.assertEqual(got, first); self.assertEqual(em.rewound_memo_stats()["walked"], 1, "walked again: nothing to serve")

    def test_a_corrupt_document_falls_to_the_walk_with_a_counted_fallback(self):
        path = self.frozen("corrupt")
        self.fresh_process(); first = em.rewound_uuids(path)
        em._ckpt_file(path).write_text("{not json")
        self.fresh_process()
        em._CKPT_STATS["fallbacks"] = {}
        got = em.rewound_uuids(path)
        self.assertEqual(got, first, "the walk's verdicts, never a raise")
        self.assertEqual(em.rewound_memo_stats()["walked"], 1)
        self.assertTrue(em.checkpoint_stats()["fallbacks"], "the fallback counted: %s" % em.checkpoint_stats()["fallbacks"])

    def _lineage(self):
        """A live session that has cleared: its anchor <fsid>.jsonl (a rewound branch inside, idle past the quiescence window)
        and a fresh leaf beside it, the judge's state rebound to a temp dir; returns (jd, anchor, leaf, fsid)."""
        jd = kernel_module().jd
        fsid = "7a391000-2222-4333-8444-000000000391"                   # private synthetic sids (the goal-store fixture rule)
        other = "7a391000-2222-4333-8444-000000000392"
        td = Path(tempfile.mkdtemp()); (td / "state").mkdir()
        saved = jd.STATE; jd._rebind_state(td / "state")
        def restore():
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._RECON_MEMO.clear()
            jd._rebind_state(saved); shutil.rmtree(td, ignore_errors=True)
        self.addCleanup(restore)
        records, _sent = G.SINGLE_FILE["rewind_off_path"]
        anchor = td / (fsid + ".jsonl")
        anchor.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        old = time.time() - 600; os.utime(anchor, (old, old))
        leaf = td / (other + ".jsonl")
        leaf.write_text(json.dumps(G.uline(NOW, "continues after the clear", "u_leaf_0", None)) + "\n")
        return jd, anchor, leaf, fsid

    def test_a_live_sessions_anchor_stays_resident_and_the_scan_reads_nothing_after_the_first_pass(self):
        """Round one, medium: the chain walk reads a cleared session's anchor whole first at every reconcile pass; the memo
        then walked it, popped its entry as quiescent, and the next pass's chain walk read it whole again, every pass (head:
        1239 then 1303 bytes per pass, the entry popped each time). The anchor is a live session's own file: the memo keeps
        it resident, and the scan reads nothing after the first pass."""
        jd, anchor, leaf, fsid = self._lineage()
        self.fresh_process(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._RECON_MEMO.clear()
        key = str(anchor); size = os.path.getsize(anchor)
        deltas, resident = [], []
        for i in range(3):
            with open(leaf, "a") as fh:                                 # one leaf append per pass: a live session at work
                fh.write(json.dumps(G.uline(NOW + 10 * (i + 1), "one more line on the leaf %d" % i, "u_leaf_%d" % (i + 1), "u_leaf_%d" % i)) + "\n")
            before = em.read_bytes_report().get(key, 0)
            jd.reconcile_rewound_goals(fsid, str(leaf), NOW + 100 + i)
            deltas.append(em.read_bytes_report().get(key, 0) - before)
            with em._JSONL_CACHE_LOCK:
                resident.append(em._JSONL_CACHE.get(key) is not None)
        st = em.rewound_memo_stats()
        # the anchor walked once and served twice; the leaf (no assembly document) takes the memo road too and, growing by one
        # record per pass, is retired and re-walked over its resident records each pass (the leaf road walked it per pass too)
        self.assertEqual((st["walked"], st["served"], st["stale"]), (4, 2, 2), "%s" % st)
        self.assertGreaterEqual(deltas[0], size, "the first pass read the anchor whole, the chain walk's read: %r" % deltas)
        self.assertEqual(deltas[1:], [0, 0], "no read of the anchor at the later passes: %r" % deltas)
        self.assertEqual(resident, [True, True, True], "the anchor's entry stays resident: %r" % resident)

    def test_the_memo_is_stored_at_the_walks_own_witness_not_the_caches_after_it(self):
        """Round one, low 1: the witness was re-fetched from the cache after the walk; an append and a refresh between the two
        memoized pre-append verdicts at the post-append witness. The walk's own (gen, base, count) is the witness."""
        path = self.frozen("witness"); key = str(path)
        self.fresh_process()
        real = em._rewound_walk; pre = {}
        def racing(p):
            out, keys = real(p)
            pre["count"] = keys[2]
            recs = G.SINGLE_FILE["rewind_off_path"][0](); last = next(r for r in reversed(recs) if r.get("uuid"))
            with open(path, "a") as fh:
                fh.write(json.dumps(G.uline(NOW + 60, "landed between the walk and the memo", "u_race", last["uuid"])) + "\n")
            em._read_jsonl_entry(path, tail_ok=False)                   # a refresh: the cache's entry is the post-append one now
            return out, keys
        em._rewound_walk = racing
        self.addCleanup(setattr, em, "_rewound_walk", real)
        em.rewound_uuids(path, drop=False)
        cur = em._REWOUND_CACHE[key]
        self.assertEqual(cur[0], pre["count"], "the cursor's count is the walk's, not the grown file's: %r" % (cur[:2],))
        em._rewound_walk = real
        got = em.rewound_uuids(path, drop=False)
        self.assertEqual(em.rewound_memo_stats()["walked"], 2, "the grown file is walked again, never served pre-append verdicts")
        self.assertEqual(got, em.file_rewound(path))

    def test_stale_counts_a_memo_the_file_moved_under_and_not_a_moved_generation(self):
        """Round one, low 2: `stale` was gated on an in-process memo, so a document memo retired by growth in a fresh process
        was never counted while an unchanged file whose entry came back under a fresh generation counted stale."""
        path = self.frozen("stalegen"); key = str(path)
        self.fresh_process(); em.rewound_uuids(path, drop=False)
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(key, None)                               # the entry leaves memory and comes back under a fresh generation
        em._read_jsonl_entry(path, tail_ok=False)
        em.rewound_uuids(path, drop=False)
        st = em.rewound_memo_stats()
        self.assertEqual((st["walked"], st["stale"]), (2, 0), "an unchanged file is walked, not stale: %s" % st)
        path2 = self.frozen("stalegrow")
        self.fresh_process(); em.rewound_uuids(path2)                    # walked; the memo written at the drop
        recs = G.SINGLE_FILE["rewind_off_path"][0](); last = next(r for r in reversed(recs) if r.get("uuid"))
        t1 = max((em.parse_z(r.get("timestamp")) or 0) for r in recs if r.get("timestamp")) + 60
        with open(path2, "a") as fh:
            fh.write(json.dumps(G.uline(t1, "one more question after the fact", "u_more", last["uuid"])) + "\n")
        old = time.time() - 600; os.utime(path2, (old, old))
        self.fresh_process(); em.rewound_uuids(path2)
        st = em.rewound_memo_stats()
        self.assertEqual((st["walked"], st["stale"]), (1, 1), "the document memo retired by growth is a retirement: %s" % st)

    def test_a_wrong_shaped_document_state_is_walked_and_counted_as_a_fallback(self):
        """Round one, low 3: a state that is not the memo's shape walked forever with nothing counted."""
        path = self.frozen("shape")
        self.fresh_process(); first = em.rewound_uuids(path)
        f = em._ckpt_file(path); d = json.loads(f.read_text())
        d["folds"]["rewoundUuids"]["state"] = {"uuids": "not-a-list"}
        f.write_text(json.dumps(d))
        self.fresh_process()
        got = em.rewound_uuids(path)
        self.assertEqual(got, first, "the walk's verdicts")
        st = em.rewound_memo_stats()
        self.assertEqual((st["walked"], st["fallback"]), (1, 1), "%s" % st)

    def test_with_the_drop_write_off_the_walked_file_stays_resident(self):
        """Round two, low 1: with the drop's document write off (a cycle cap of 0, a documented knob) the memo road dropped the
        walked entry and read a dead file whole at EVERY pass where the old road read it once per process (1259 bytes each
        pass against 1259/0/0). The entry stays resident when the memo cannot reach the disk."""
        path = self.frozen("capoff"); key = str(path)
        saved = dict(em._CKPT_CYCLE)
        with em._CKPT_LOCK:
            em._CKPT_CYCLE["cap"] = 0
        def restore():
            with em._CKPT_LOCK:
                em._CKPT_CYCLE.update(saved)
        self.addCleanup(restore)
        self.fresh_process()
        with em._CKPT_LOCK:
            em._CKPT_CYCLE["cap"] = 0
        self.assertFalse(em.checkpoint_drop_writes_on())
        first = em.rewound_uuids(path)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNotNone(em._JSONL_CACHE.get(key), "the entry stays resident: nothing could be written")
        before = em.read_bytes_report().get(key, 0)
        self.assertEqual(em.rewound_uuids(path), first)
        self.assertEqual(em.read_bytes_report().get(key, 0) - before, 0, "the second pass reads nothing")
        st = em.rewound_memo_stats(); self.assertEqual((st["walked"], st["served"]), (1, 1), "%s" % st)

    def test_a_registered_sessions_own_file_is_not_dropped_by_another_sessions_scan(self):
        """Round two, low 2: a fork's episode log names its parent's anchor; when the fork's scan walked first and dropped the
        entry, the parent's chain walk read the anchor whole once more in that process, by pass order. A file that is a
        registered session's own (<sid>.jsonl with a reg) is never dropped by another session's scan."""
        jd = kernel_module().jd
        saved_owner = jd._SDK_OWNER_FN                                   # the registry road answers here (a kernel loaded earlier in
        jd._SDK_OWNER_FN = None                                          #  the process leaves its backend hook, which knows no reg file)
        self.addCleanup(setattr, jd, "_SDK_OWNER_FN", saved_owner)
        parent = "7a391000-2222-4333-8444-000000000393"; fork = "7a391000-2222-4333-8444-000000000394"
        td = Path(tempfile.mkdtemp()); (td / "state").mkdir()
        saved = jd.STATE; jd._rebind_state(td / "state")
        def restore():
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._rebind_state(saved); shutil.rmtree(td, ignore_errors=True)
        self.addCleanup(restore)
        records, _sent = G.SINGLE_FILE["rewind_off_path"]
        anchor = td / (parent + ".jsonl")
        anchor.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        old = time.time() - 600; os.utime(anchor, (old, old))
        leaf = td / (fork + ".jsonl")
        leaf.write_text(json.dumps(G.uline(NOW, "the fork's own first line", "u_fork_0", None)) + "\n")
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (parent + ".json")).write_text(json.dumps({"spawnedAt": NOW}))   # the parent is a registered session
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        (jd.EPIDIR / (fork + ".jsonl")).write_text(json.dumps({"head": "u_fork_0", "fsid": parent, "t": NOW}) + "\n")   # names the anchor
        self.fresh_process()
        out, fails = jd._per_file_rewound(fork, [str(leaf)])
        self.assertEqual(fails, 0); self.assertTrue(out, "the anchor's rewound branch is in the fork's scan")
        with em._JSONL_CACHE_LOCK:
            self.assertIsNotNone(em._JSONL_CACHE.get(str(anchor)), "the parent's anchor stays resident under the fork's scan")
        (jd.STATE / "sdk" / (parent + ".json")).unlink()                      # no reg: a dead episode's file, dropped as before
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(str(anchor), None)
        self.fresh_process()
        jd._per_file_rewound(fork, [str(leaf)])
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(str(anchor)), "an unregistered file is dropped after its memo is written")

    def _own_leaf(self, name, scenario="rewind_off_path"):
        """A session's own leaf under a private sid, the judge's state rebound to a temp dir; returns (jd, fsid, path)."""
        jd = kernel_module().jd
        fsid = "7a391000-2222-4333-8444-0000000003%02d" % (95 + (hash(name) % 4))
        td = Path(tempfile.mkdtemp()); (td / "state").mkdir()
        saved = jd.STATE; jd._rebind_state(td / "state")
        saved_owner = jd._SDK_OWNER_FN; jd._SDK_OWNER_FN = None
        def restore():
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._rebind_state(saved); shutil.rmtree(td, ignore_errors=True)
            jd._SDK_OWNER_FN = saved_owner
        self.addCleanup(restore)
        records, sent = G.SINGLE_FILE[scenario]
        path = td / (fsid + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in records()) + "\n")
        old = time.time() - 600; os.utime(path, (old, old))
        return jd, fsid, str(path)

    def _leaf_whole_rows(self, path):
        return {k: v["bytes"] for k, v in em.record_cache_stats()["wholeReads"].items() if k.endswith("<-_per_file_rewound")}

    def test_a_leaf_with_no_assembly_document_takes_the_memo_road_and_is_not_read_whole_at_the_next_boot(self):
        """The 104 MB row on the boot after the memo deployed: a leaf with no compaction boundary can never have an assembly
        document, so the leaf road's seeded walk had nothing to seed and read it whole at every boot (upgrade<-_per_file_rewound,
        n=1 per boot). Such a leaf takes the memo road: walked once, its verdicts in its fold document, served at the next
        process with no whole read; the memo's witness retires it on growth like any other file's."""
        jd, fsid, path = self._own_leaf("leafmemo")
        self.fresh_process()
        walked = em.file_rewound(path)                                  # the plain walk's verdicts: the reference
        self.assertTrue(walked)
        self.fresh_process()
        out, fails = jd._per_file_rewound(fsid, [path])
        self.assertEqual((fails, out), (0, walked))
        self.assertTrue(self._leaf_whole_rows(path), "the first process reads the leaf whole, once: %s" % em.record_cache_stats()["wholeReads"])
        em.checkpoint_write(path)                                       # the settle's fold document write (the memo rides it)
        self.fresh_process()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        out2, fails2 = jd._per_file_rewound(fsid, [path])
        self.assertEqual((fails2, out2), (0, walked), "served: the same verdicts")
        self.assertEqual(self._leaf_whole_rows(path), {}, "no whole read of the leaf at the next process: %s" % em.record_cache_stats()["wholeReads"])
        st = em.rewound_memo_stats(); self.assertEqual((st["served"], st["walked"]), (1, 0), "%s" % st)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNotNone(em._JSONL_CACHE.get(path), "a session's own file: never dropped")

    def test_a_leaf_with_an_assembly_document_keeps_the_leaf_road(self):
        """The seeded walk over the document's pre-cut verdicts and the tail (T323 stage 4a) stays the leaf's road when a document
        stands; the memo is for the leaf that has none."""
        jd, fsid, path = self._own_leaf("leafdoc", scenario="compaction_atom")
        self.fresh_process()
        tree = em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(path, fsid, tree=tree), em.asm_checkpoint_stats())
        self.assertTrue(em.asm_document_stands(path))
        calls = []
        real = em.file_rewound
        em.file_rewound = lambda p, **kw: (calls.append(kw.get("rompuuid")), real(p, **kw))[1]
        self.addCleanup(setattr, em, "file_rewound", real)
        self.fresh_process(); em.set_checkpoint_dir(lambda: self.ck)
        out, fails = jd._per_file_rewound(fsid, [path])
        self.assertEqual(fails, 0)
        self.assertEqual(calls, [fsid], "the leaf road, with the rompuuid: %r" % calls)
        self.assertEqual(em.rewound_memo_stats()["walked"], 0, "the memo not consulted for a documented leaf")

    def test_a_documented_leaf_of_a_two_file_lineage_takes_the_memo_road(self):
        """Round one, low 1: a cleared or resume-forked session's document is written over the leaf plus its anchor, so the leaf
        road's load refused it on the inputs comparison and the leaf was read whole at every process, at the head and the base
        alike. A two-file lineage takes the memo road: read whole once, served at the next process."""
        jd = kernel_module().jd
        fsid = "7a391000-2222-4333-8444-000000000399"
        td = Path(tempfile.mkdtemp()); (td / "state").mkdir()
        saved = jd.STATE; jd._rebind_state(td / "state")
        saved_owner = jd._SDK_OWNER_FN; jd._SDK_OWNER_FN = None
        def restore():
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._rebind_state(saved); shutil.rmtree(td, ignore_errors=True)
            jd._SDK_OWNER_FN = saved_owner
        self.addCleanup(restore)
        anchor = td / (fsid + ".jsonl")                                  # the session's anchor: _judge_candidates adds it beside the leaf
        anchor.write_text("".join(json.dumps(r) + "\n" for r in G.scenario_resume_lineage_fileA()))
        leaf = td / (G.FSID_B + ".jsonl")                                # the leaf compacts: it has a document, written over both files
        leaf.write_text("".join(json.dumps(r) + "\n" for r in compacting_variant(G.scenario_resume_lineage_fileB(), "lin")))
        for f in (anchor, leaf):
            old = time.time() - 600; os.utime(f, (old, old))
        cands = [str(anchor), str(leaf)]
        self.fresh_process()
        tree = em.parse_session(str(leaf), rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=cands, postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(str(leaf), fsid, tree=tree), em.asm_checkpoint_stats())
        self.assertTrue(em.asm_document_stands(str(leaf)))
        files = jd._judge_candidates(fsid, [str(leaf)]); self.assertEqual(len(files), 2)
        self.fresh_process()
        out, fails = jd._per_file_rewound(fsid, files)
        self.assertEqual(fails, 0)
        em.checkpoint_write(str(leaf)); em.checkpoint_write(str(anchor))   # the settle's and the dirty writer's fold documents
        self.fresh_process()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        out2, fails2 = jd._per_file_rewound(fsid, files)
        self.assertEqual((fails2, out2), (0, out))
        rows = {k: v["bytes"] for k, v in em.record_cache_stats()["wholeReads"].items() if k.endswith("<-_per_file_rewound")}
        self.assertEqual(rows, {}, "no whole read of the leaf at the next process: %s" % em.record_cache_stats()["wholeReads"])
        self.assertFalse(em.asm_document_seeds(str(leaf)), "a document over two files cannot seed the one-file walk")

    def test_the_sidecar_names_the_documents_inputs_and_a_one_file_document_seeds(self):
        jd, fsid, path = self._own_leaf("seeds", scenario="compaction_atom")
        self.fresh_process()
        tree = em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(path, fsid, tree=tree))
        meta = json.loads(em._asm_ckpt_file(path).with_name(em._asm_ckpt_file(path).name + ".meta").read_text())
        self.assertEqual(meta.get("files"), [fsid], "the sidecar carries the inputs' fsids: %r" % meta)
        self.assertTrue(em.asm_document_seeds(path))

    def test_the_flip_to_the_leaf_road_forgets_the_memo_so_the_cut_follows_the_live_folds(self):
        """Round one, low 2: a documentless leaf carries a rewoundUuids cursor in its fold document; when its first compaction lands
        the scan flips to the leaf road for good, the cursor never steps again, and the checkpoint's cut (the minimum over the
        folds) drags behind it until growth passes it. The flip drops the memo's cursor and the next write's cut follows."""
        jd, fsid, path = self._own_leaf("flip", scenario="rewind_off_path")
        self.fresh_process()
        jd._per_file_rewound(fsid, [path])                                # the memo road: a cursor at the file's record count
        self.assertIn(path, em._REWOUND_CACHE)
        n0 = em._REWOUND_CACHE[path][0]
        em.fold_records({}, path, list, lambda st, r: st, ckpt="flipFold")   # a live fold beside it, as the settle's folds are
        self.assertTrue(em.checkpoint_write(path), "the settle's write while the leaf is documentless: the memo reaches the disk")
        self.assertIn("rewoundUuids", json.loads(em._ckpt_file(path).read_text())["folds"])
        recs = G.SINGLE_FILE["rewind_off_path"][0]()
        more = compacting_variant(recs, "flip")[len(recs):]                # the first compaction lands, with turns after it
        with open(path, "a") as fh:
            for r in more:
                fh.write(json.dumps(r) + "\n")
        old = time.time() - 600; os.utime(path, (old, old))
        tree = em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(path, fsid, tree=tree), em.asm_checkpoint_stats())
        jd._per_file_rewound(fsid, [path])                                # the leaf road now
        self.assertNotIn(path, em._REWOUND_CACHE, "the memo's cursor dropped at the flip")
        em.fold_records({}, path, list, lambda st, r: st, ckpt="flipFold")   # the live fold steps to the file's record count
        self.assertTrue(em.checkpoint_write(path), "the next write")
        d = json.loads(em._ckpt_file(path).read_text())
        self.assertNotIn("rewoundUuids", d["folds"], "the next write omits the fold, though the document carried it: %r" % sorted(d["folds"]))
        self.assertEqual(d["count"], d["folds"]["flipFold"]["count"], "the cut equals the live fold's count: %r" % d)
        self.assertGreater(d["count"], n0, "past the old cursor: %d vs %d" % (d["count"], n0))
        self.assertNotIn(path, em._RETIRED_FOLDS, "the retirement honoured is done")

    def test_the_sidecar_says_whether_resume_links_joined_the_inputs_and_an_older_sidecar_is_refreshed(self):
        """Round one, lows 1 and 2: the load refuses a document on its links too, so a one-file lineage whose document was written
        with resume links among the inputs cannot seed either (`linked`); and a session that already carried a document kept
        the old sidecar shape indefinitely, since the write skips a restored entry: the restore rewrites the sidecar alone."""
        jd, fsid, path = self._own_leaf("linked", scenario="compaction_atom")
        self.fresh_process()
        tree = em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(path, fsid, tree=tree))
        cp = em._asm_ckpt_file(path); meta = cp.with_name(cp.name + ".meta")
        d = json.loads(meta.read_text()); self.assertEqual((d["files"], d["linked"]), ([fsid], False), "%r" % d)
        self.assertTrue(em.asm_document_seeds(path))
        d["linked"] = True; meta.write_text(json.dumps(d))
        self.assertFalse(em.asm_document_seeds(path), "links among the inputs: the one-file walk cannot be seeded")
        meta.write_text(json.dumps({"av": d["av"], "path": d["path"]}))          # an older sidecar, without the list
        self.assertTrue(em.asm_document_seeds(path), "an older sidecar answers as the stat did")
        before = em.checkpoint_stats()["documentBytes"]
        self.fresh_process(); modes = []
        em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"])
        d2 = json.loads(meta.read_text())
        self.assertEqual((d2.get("files"), d2.get("linked")), ([fsid], False), "the restore refreshed the older sidecar: %r" % d2)
        em.asm_document_seeds(path)
        self.assertGreater(em.checkpoint_stats()["documentBytes"], before, "the sidecar read is counted under documentBytes")

    def test_a_leaf_road_pass_over_a_clean_path_retires_nothing_and_leaves_no_work(self):
        """Round two: the forget fired at EVERY leaf-road pass, so every leaf on the leaf road sat permanently dirty (the converge
        pass's zero-cost gate never quiet) and a memo stored later in the process was popped out of the next document. A pass
        over a path with no cursor and no rewoundUuids on disk retires nothing and dirties nothing."""
        jd, fsid, path = self._own_leaf("clean", scenario="compaction_atom")
        self.fresh_process()
        tree = em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(path, fsid, tree=tree))
        em.fold_records({}, path, list, lambda st, r: st, ckpt="cleanFold")
        self.assertTrue(em.checkpoint_write(path)); self.assertFalse(em.checkpoint_has_work(), "clean after the write")
        jd._per_file_rewound(fsid, [path])                                # the leaf road, twice, over an unchanged clean path
        jd._per_file_rewound(fsid, [path])
        self.assertFalse(em.checkpoint_has_work(), "no retirement pending, nothing dirty: %r" % sorted(em.checkpoint_dirty()))
        self.assertNotIn(path, em._RETIRED_FOLDS)

    def test_a_memo_stored_after_a_leaf_road_pass_survives_the_next_write(self):
        """Round two, the verifier's probe D: after a leaf-road pass the path took the memo road (its lineage grew an anchor), the
        memo was stored, and the still-pending retirement popped it out of the next document and discarded the dirty mark: the
        next process read the file whole again. With the retirement fired only at a real flip, the memo reaches the disk."""
        jd, fsid, path = self._own_leaf("survive", scenario="compaction_atom")
        self.fresh_process()
        tree = em.parse_session(path, rompuuid=fsid, name="impl", dir="/TESTDIR", candidate_files=[path], postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(path, fsid, tree=tree))
        jd._per_file_rewound(fsid, [path])                                # the leaf road (no memo anywhere: nothing to retire)
        anchor = Path(path).with_name(fsid + ".jsonl")                    # the session clears: the leaf's file becomes an anchor
        self.assertEqual(str(anchor), path, "the leaf IS <fsid>.jsonl here; a second file makes the lineage two files")
        leaf2 = Path(path).with_name("7a391000-2222-4333-8444-000000000388.jsonl")   # outside _own_leaf's sid range
        leaf2.write_text(json.dumps(G.uline(NOW + 5, "after the clear", "u_after", None)) + "\n")
        files = jd._judge_candidates(fsid, [str(leaf2)]); self.assertEqual(len(files), 2)
        jd._per_file_rewound(fsid, files)                                 # the memo road for the anchor: the memo stored
        self.assertIn(path, em._REWOUND_CACHE)
        em.fold_records({}, path, list, lambda st, r: st, ckpt="surviveFold")
        self.assertTrue(em.checkpoint_write(path))
        em.checkpoint_write(str(leaf2))                                  # the new leaf's own fold document (its memo too)
        d = json.loads(em._ckpt_file(path).read_text())
        self.assertIn("rewoundUuids", d["folds"], "the memo reached the disk: %r" % sorted(d["folds"]))
        self.fresh_process()
        with em._JSONL_CACHE_LOCK:
            em._RECORD_CACHE_STATS["wholeReads"] = {}
        jd._per_file_rewound(fsid, files)
        rows = {k: v["bytes"] for k, v in em.record_cache_stats()["wholeReads"].items() if k.endswith("<-_per_file_rewound")}
        self.assertEqual(rows, {}, "the next process reads no whole file: %s" % em.record_cache_stats()["wholeReads"])
        self.assertEqual(em.rewound_memo_stats()["served"], 2, "both files served: %s" % em.rewound_memo_stats())


if __name__ == "__main__":
    unittest.main()
