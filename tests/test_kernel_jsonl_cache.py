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
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = SourceFileLoader("romp_event_model_jsonl_cache",
                      os.path.join(BIN, "romp-event-model")).load_module()


def _write_jsonl(path, n, start=0):
    with open(path, "w") as f:
        for i in range(start, start + n):
            f.write(json.dumps({"uuid": f"u{i}", "type": "user"}) + "\n")


class JsonlCacheEviction(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="jsonl-cache-")
        em._JSONL_CACHE.clear()
        # Spy on the parse layer: every (bytes, base_offset) actually scanned. A cache hit
        # scans nothing, so "no new spy entries" == "served from cache".
        self._real_scan = em._scan_jsonl_bytes
        self.scans = []
        def spy(data, base_offset):
            self.scans.append((len(data), base_offset))
            return self._real_scan(data, base_offset)
        em._scan_jsonl_bytes = spy

    def tearDown(self):
        em._scan_jsonl_bytes = self._real_scan
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


if __name__ == "__main__":
    unittest.main()
