#!/usr/bin/env python3
"""The transcript-cache thrash regression (the user 2026-08-15: UI clicks lagged machine-wide while the
kernel sat at ~30-60% CPU with judges idle).

_read_jsonl_incremental's cache is what keeps append-only transcript reads O(delta): the 2026-07-05
incident (a 40MB transcript re-parsed from byte zero on every push, every click queued behind the
parse) added the offset cache. Its eviction was `clear()` past a cap sized to the session count — but the
working set is FILES, not sessions (every subagent writes its own transcript), and once more distinct
files than slots passed through one push cycle, the clear nuked the hot entries too: every push
re-parsed every active transcript from byte zero again, as a permanent background burn that only grew
with transcript count. Eviction must therefore never drop the recently-used. These tests pin:
(1) a hot unchanged file keeps serving from cache while arbitrarily many cold one-off reads pass
through, (2) an append costs only its delta, (3) the cap still bounds the cache. Synthetic fixtures.
"""
import json
import os
import sys
import shutil
from pathlib import Path
import tempfile
import threading
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model_jsonl_cache",
                      os.path.join(BIN, "romp-event-model"))


def _write_jsonl(path, n, start=0):
    with open(path, "w") as f:
        for i in range(start, start + n):
            f.write(json.dumps({"uuid": f"u{i}", "type": "user"}) + "\n")


class JsonlCacheEviction(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jsonl-cache-")
        em._JSONL_CACHE.clear()
        # Spy on the parse layer: every (bytes, base_offset) actually scanned. A cache hit
        # scans nothing, so "no new spy entries" == "served from cache". The reader streams its scan off the open
        # file (measured 2026-09-15: the whole-blob scan was the read's transient peak), so the bytes a scan cost are
        # what it returns having read them, not the length of a blob handed in.
        self._real_scan = em._scan_jsonl_stream
        self.scans = []
        def spy(fh, base_offset, *a, **k):       # the reader hands the scan an offsets array too (T323 stage 4)
            out = self._real_scan(fh, base_offset, *a, **k)
            self.scans.append((out[2], base_offset))
            return out
        em._scan_jsonl_stream = spy

    def tearDown(self):
        em._scan_jsonl_stream = self._real_scan
        em._JSONL_CACHE.clear()

    def _p(self, name):
        return os.path.join(self.dir, name)

    def test_hot_file_survives_a_flood_of_cold_reads(self):
        # The push-loop shape: the active session's transcript is re-read every cycle,
        # interleaved with one-off reads of other files (subagents, sibling sessions,
        # captioned history). The hot, UNCHANGED file must never re-parse, no matter how
        # many distinct cold files pass through. Under clear-at-cap eviction this fails
        # at the first cap crossing.
        hot = self._p("hot.jsonl")
        _write_jsonl(hot, 5)
        self.assertEqual(len(em._read_jsonl_incremental(hot)), 5)
        for i in range(em._JSONL_CACHE_MAX * 2):
            cold = self._p(f"cold{i}.jsonl")
            _write_jsonl(cold, 1)
            em._read_jsonl_incremental(cold)
            if i % 25 == 0:
                before = len(self.scans)
                self.assertEqual(len(em._read_jsonl_incremental(hot)), 5)
                self.assertEqual(len(self.scans), before,
                                 f"hot unchanged transcript re-parsed after {i + 1} cold reads — "
                                 "eviction dropped a recently-used entry")

    def test_append_costs_only_the_delta(self):
        p = self._p("grow.jsonl")
        _write_jsonl(p, 3)
        self.assertEqual(len(em._read_jsonl_incremental(p)), 3)
        size_before = os.path.getsize(p)
        with open(p, "a") as f:
            f.write(json.dumps({"uuid": "u-new", "type": "user"}) + "\n")
        recs = em._read_jsonl_incremental(p)
        self.assertEqual([r["uuid"] for r in recs], ["u0", "u1", "u2", "u-new"])
        n_bytes, base = self.scans[-1]
        self.assertEqual(base, size_before, "incremental read did not resume at the cached offset")
        self.assertEqual(n_bytes, os.path.getsize(p) - size_before,
                         "read more than the appended delta")

    def test_cache_stays_bounded(self):
        for i in range(em._JSONL_CACHE_MAX + 50):
            p = self._p(f"b{i}.jsonl")
            _write_jsonl(p, 1)
            em._read_jsonl_incremental(p)
        self.assertLessEqual(len(em._JSONL_CACHE), em._JSONL_CACHE_MAX)


class JsonlCacheThreadSafety(unittest.TestCase):
    """The LRU made cache HITS mutate (pop + reinsert) and evictions iterate, while the
    cache has cross-thread callers: the index and triage judge tiers run as parallel
    threads each producer pass, each parsing via worker pools, alongside the pusher's
    per-cycle parse and the parse-warm/boot-warm threads. One thread's eviction can pop
    the entry another just matched — KeyError from an unconditional pop, RuntimeError
    from next(iter()) over a dict resizing under it — and the callers catch-and-degrade
    (the tier runner's per-session futures swallow + log), so the raise surfaces as
    silently missing judge output for that pass, not a crash. So the cache must never
    raise cross-thread, and a lost race must degrade to a re-parse, never to wrong
    records."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jsonl-cache-mt-")
        em._JSONL_CACHE.clear()
        self._max = em._JSONL_CACHE_MAX
        self._switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)   # the race windows are a few bytecodes wide — preempt often

    def tearDown(self):
        sys.setswitchinterval(self._switch)
        em._JSONL_CACHE_MAX = self._max
        em._JSONL_CACHE.clear()

    def test_hit_survives_concurrent_eviction(self):
        em._JSONL_CACHE_MAX = 2       # at-cap from the third file on: every miss evicts
        paths, expected = [], {}
        for i in range(8):
            p = os.path.join(self.dir, f"t{i}.jsonl")
            _write_jsonl(p, 2, start=i * 10)
            paths.append(p)
            expected[p] = [f"u{i * 10}", f"u{i * 10 + 1}"]
        stop = time.monotonic() + 3.0   # unfixed, the first raise lands in ≤~1s across trials
        errors = []

        def hammer(k):
            # Half the threads hammer two SHARED hot files (the mutating hit path), half
            # rotate cold ones (constant eviction of exactly those entries) — the collision
            # the judge tier workers + pusher produce over a live transcript set.
            i = 0
            try:
                while time.monotonic() < stop and not errors:
                    p = paths[k % 2] if k < 4 else paths[2 + (i % 6)]
                    recs = em._read_jsonl_incremental(p)
                    if [r["uuid"] for r in recs] != expected[p]:
                        raise AssertionError(f"wrong records served for {os.path.basename(p)}")
                    i += 1
            except Exception as e:   # noqa: BLE001 — the raise IS the defect under test
                errors.append(e)

        threads = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"cache raised or served wrong records cross-thread: {errors[:3]!r}")

    def test_hit_serves_the_same_records_as_a_cold_read(self):
        # The locked lookup+move path must serve exactly what a cold parse produces.
        p = os.path.join(self.dir, "hit.jsonl")
        _write_jsonl(p, 4)
        em._read_jsonl_incremental(p)               # warm
        served = em._read_jsonl_incremental(p)      # the hit path
        em._JSONL_CACHE.clear()
        self.assertEqual(served, em._read_jsonl_incremental(p))


class RecordCacheByteBudget(unittest.TestCase):
    """The kernel memory work (2026-09-11): a BYTE budget beside the count cap. Entries weigh the bytes they hold; past
    the budget the least recently used go, one at a time, in the same order as the count cap, so a hot file survives a
    cold flood; a single entry larger than the budget still inserts; the ledger stays in step; /perf sees it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jsonl-budget-")
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        for k in em._RECORD_CACHE_STATS: em._RECORD_CACHE_STATS[k] = 0
        self._budget = em._JSONL_CACHE_BUDGET_BYTES
        self._real_scan = em._scan_jsonl_stream
        self.scans = []
        def spy(fh, base_offset, *a, **k):
            out = self._real_scan(fh, base_offset, *a, **k); self.scans.append((out[2], base_offset)); return out
        em._scan_jsonl_stream = spy

    def tearDown(self):
        em._JSONL_CACHE_BUDGET_BYTES = self._budget
        em._scan_jsonl_stream = self._real_scan
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0

    def _file(self, name, n):
        path = os.path.join(self.dir, name); _write_jsonl(path, n); return path

    def _ledger_ok(self):
        with em._JSONL_CACHE_LOCK:
            return em._JSONL_CACHE_BYTES[0] == sum(em._entry_weight(e) for e in em._JSONL_CACHE.values())

    def test_the_budget_evicts_the_least_recently_used_and_a_hot_file_survives(self):
        hot = self._file("hot.jsonl", 40)
        em._read_jsonl_incremental(hot)
        one = os.path.getsize(hot)
        em._JSONL_CACHE_BUDGET_BYTES = int(one * 3.5)            # room for three such files, not four
        cold = [self._file("cold%d.jsonl" % i, 40) for i in range(6)]
        for c in cold:
            em._read_jsonl_incremental(hot)                       # the hot file is USED between every cold read
            em._read_jsonl_incremental(c)
            self.assertTrue(self._ledger_ok())
            self.assertLessEqual(em._JSONL_CACHE_BYTES[0], em._JSONL_CACHE_BUDGET_BYTES)
        self.assertIn(hot, em._JSONL_CACHE, "the hot file survived six cold reads under a three-file budget")
        self.assertLessEqual(len(em._JSONL_CACHE), 3)
        n = len(self.scans)
        em._read_jsonl_incremental(hot)
        self.assertEqual(len(self.scans), n, "and still serves from the cache")
        st = em.record_cache_stats()
        self.assertGreaterEqual(st["budgetEvictions"], 4); self.assertGreater(st["evictedBytes"], 0)
        self.assertEqual(st["budgetBytes"], em._JSONL_CACHE_BUDGET_BYTES)
        for k in ("entries", "bytes", "countCap", "inserts", "evictions", "dropped", "droppedBytes"):
            self.assertIn(k, st)

    def test_an_entry_larger_than_the_whole_budget_still_inserts_alone(self):
        big = self._file("big.jsonl", 200); small = self._file("small.jsonl", 5)
        em._read_jsonl_incremental(small)
        em._JSONL_CACHE_BUDGET_BYTES = os.path.getsize(big) // 2
        em._read_jsonl_incremental(big)
        self.assertIn(big, em._JSONL_CACHE, "a leaf is never refused")
        self.assertNotIn(small, em._JSONL_CACHE, "the budget then holds that one entry")
        self.assertTrue(self._ledger_ok())

    def test_the_ledger_follows_appends_and_failures(self):
        path = self._file("grow.jsonl", 10)
        em._read_jsonl_incremental(path); w1 = em._JSONL_CACHE_BYTES[0]
        with open(path, "a") as f:
            for i in range(10, 20): f.write(json.dumps({"uuid": "u%d" % i, "type": "user"}) + "\n")
        em._read_jsonl_incremental(path)
        self.assertEqual(em._JSONL_CACHE_BYTES[0], os.path.getsize(path)); self.assertGreater(em._JSONL_CACHE_BYTES[0], w1)
        os.unlink(path)
        em._read_jsonl_incremental(path)                          # an absent file pops its entry: the ledger follows
        self.assertEqual(em._JSONL_CACHE_BYTES[0], 0); self.assertTrue(self._ledger_ok())


class DropAfterQuiescentFold(unittest.TestCase):
    """fold_records(drop_after="quiescent"): a file unchanged for _DROP_AFTER_QUIESCENT_S (a subagent that returned) is
    dropped from the reader's cache once the fold stepped it; the cursor stands; a fresh file keeps its records."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jsonl-drop-")
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        for k in em._RECORD_CACHE_STATS: em._RECORD_CACHE_STATS[k] = 0
        self._real_scan = em._scan_jsonl_stream; self.scans = []
        def spy(fh, base_offset, *a, **k):
            out = self._real_scan(fh, base_offset, *a, **k); self.scans.append((out[2], base_offset)); return out
        em._scan_jsonl_stream = spy

    def tearDown(self):
        em._scan_jsonl_stream = self._real_scan
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0

    def _fold(self, cache, path, **kw):
        return em.fold_records(cache, path, lambda: {"n": 0}, lambda st, r: {"n": st["n"] + 1}, **kw)

    def test_a_quiescent_file_is_dropped_after_the_fold_and_a_fresh_one_kept(self):
        old = os.path.join(self.dir, "returned-agent.jsonl"); _write_jsonl(old, 30)
        t = time.time() - em._DROP_AFTER_QUIESCENT_S - 60
        os.utime(old, (t, t))
        fresh = os.path.join(self.dir, "running-agent.jsonl"); _write_jsonl(fresh, 30)
        cache = {}
        self.assertEqual(self._fold(cache, old, drop_after="quiescent")["n"], 30)
        self.assertNotIn(old, em._JSONL_CACHE, "the returned agent's records leave memory once folded")
        self.assertIn(old, cache, "its fold cursor stands")
        self.assertEqual(self._fold(cache, fresh, drop_after="quiescent")["n"], 30)
        self.assertIn(fresh, em._JSONL_CACHE, "a file still fresh keeps its records: its next append is a delta")
        self.assertEqual(self._fold(cache, fresh)["n"], 30)
        self.assertIn(fresh, em._JSONL_CACHE, "no policy, no drop")
        st = em.record_cache_stats()
        self.assertEqual(st["dropped"], 1); self.assertGreater(st["droppedBytes"], 0)
        self.assertEqual(em._JSONL_CACHE_BYTES[0], os.path.getsize(fresh), "the ledger followed the drop")
        # the dropped file folds again: one re-read, the same answer, no double-count in the cursor
        n = len(self.scans)
        self.assertEqual(self._fold(cache, old, drop_after="quiescent")["n"], 30)
        self.assertEqual(len(self.scans), n + 1, "one read for the second fold of a dropped file")

    def test_the_kernel_drops_only_its_subagent_folds_and_reports_the_cache(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        for name in ("agentLaunchIds", "agentGist", "agentLaunches"):
            self.assertIn('ckpt="%s", drop_after="quiescent")' % name, src, "the %s fold drops a returned agent's records" % name)
        self.assertNotIn('ckpt="bgAll", drop_after', src, "a LEAF fold never drops: the live session's records stay warm")
        self.assertIn('"recordCache": em.record_cache_stats(),', src, "/perf carries the record cache")
        unit = open(os.path.join(BIN, "romp-service")).read()
        self.assertIn("Environment=MALLOC_ARENA_MAX=2", unit, "the service unit hands the allocator setting to the manager and its kernels")


class WholeReadsByCaller(unittest.TestCase):
    """T384: every read that pulls a file whole is counted on /perf by the reader's kind and the first frame outside the event
    model, so a boot's whole reads are named the way hydratedBy named the planner. A tail entry served, an append, and a
    restore's tail read are not whole reads."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear(); em._RECORD_CACHE_STATS["wholeReads"] = {}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_tail_entry_upgraded_to_the_whole_file_is_named_as_an_upgrade(self):
        """Review, low 3: a restored tail entry met by a whole reader is read whole (the reader's `upgrade`), counted as such."""
        ck = os.path.join(self.dir, "ck"); os.makedirs(ck)
        em.set_checkpoint_dir(lambda: Path(ck))
        try:
            path = os.path.join(self.dir, "leaf.jsonl"); _write_jsonl(path, 30)
            cache = {}
            em.fold_records(cache, path, lambda: 0, lambda st, o: st + 1, ckpt="upgradeFold")
            self.assertTrue(em.checkpoint_write(path))
            with em._JSONL_CACHE_LOCK:
                em._JSONL_CACHE.clear(); em._RECORD_CACHE_STATS["wholeReads"] = {}
            cache.clear(); em.set_checkpoint_dir(lambda: Path(ck))       # a fresh process: the fold restores over a TAIL entry
            em.fold_records(cache, path, lambda: 0, lambda st, o: st + 1, ckpt="upgradeFold")
            self.assertEqual(em.record_cache_stats()["wholeReads"], {}, "the restore's tail read is not a whole read")
            def some_whole_reader():
                return em._read_jsonl_incremental(path)
            self.assertEqual(len(some_whole_reader()), 30)
            wr = em.record_cache_stats()["wholeReads"]
            self.assertEqual(list(wr), ["upgrade<-some_whole_reader"], "%s" % wr)
        finally:
            em.set_checkpoint_dir(None)

    def test_a_whole_read_from_inside_a_generator_expression_names_the_enclosing_function(self):
        """A generator expression's own frame is no caller: the row names the function around it (the same exposure the
        hydration attribution had to a comprehension's frame before Python 3.12)."""
        path = os.path.join(self.dir, "leaf.jsonl"); _write_jsonl(path, 10)
        def genexpr_reader():
            return sum(len(em._read_jsonl_incremental(p)) for p in [path])
        self.assertEqual(genexpr_reader(), 10)
        self.assertEqual(list(em.record_cache_stats()["wholeReads"]), ["zero<-genexpr_reader"], "%s" % em.record_cache_stats()["wholeReads"])

    def test_a_from_zero_read_is_named_for_its_caller_and_an_append_is_not(self):
        path = os.path.join(self.dir, "leaf.jsonl"); _write_jsonl(path, 20)
        size = os.path.getsize(path)
        def some_boot_reader():
            return em._read_jsonl_incremental(path)
        self.assertEqual(len(some_boot_reader()), 20)
        wr = em.record_cache_stats()["wholeReads"]
        self.assertEqual(wr, {"zero<-some_boot_reader": {"count": 1, "bytes": size}}, "%s" % wr)
        _write_jsonl(path, 25)                                         # an append: a tail read, not a whole one
        self.assertEqual(len(some_boot_reader()), 25)
        self.assertEqual(em.record_cache_stats()["wholeReads"]["zero<-some_boot_reader"]["count"], 1, "the append did not count")
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        cache = {}
        em.fold_records(cache, path, lambda: 0, lambda st, o: st + 1)   # a fold's first read: whole, named for the fold's caller
        keys = em.record_cache_stats()["wholeReads"]
        self.assertTrue(any(k.startswith("zero<-") and k.endswith("test_a_from_zero_read_is_named_for_its_caller_and_an_append_is_not") for k in keys), "%s" % keys)


class RecordCacheDefaultBudget(unittest.TestCase):
    """The budget shipped at 1 GiB (2026-09-11) and sat below a 50-session working set: every build re-read whole transcripts
    (14.9 GB in 3.5 min, 132 s pusher cycles). The default is a quarter of the machine's memory, never under 4 GiB."""

    def test_half_of_the_machine(self):
        text = "MemTotal:       123634396 kB\nMemFree:        1 kB\n"
        self.assertEqual(em._record_cache_default_budget_bytes(text), int(123634396 * 1024 * 0.5))

    def test_never_under_four_gib(self):
        self.assertEqual(em._record_cache_default_budget_bytes("MemTotal:  8000000 kB\n"), 4 * 1024 ** 3, "half of 8 GB is the floor")
        self.assertEqual(em._record_cache_default_budget_bytes("garbage"), 4 * 1024 ** 3, "no MemTotal: the floor")

    def test_the_environment_sets_it_outright(self):
        src = open(em.__file__).read()
        self.assertIn('int(float(os.environ["ROMP_RECORD_CACHE_BUDGET_MB"]) * 1024 * 1024)', src)
        self.assertIn("else _record_cache_default_budget_bytes()", src, "the default is derived, not a literal")


if __name__ == "__main__":
    unittest.main()
