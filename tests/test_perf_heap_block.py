#!/usr/bin/env python3
"""The /perf snapshot's `heap` block (the lag investigation, 2026-09-15): where the kernel's resident memory sits at the
moment of the read, so a large VmRSS can be attributed live, without a restart or a debugger.

Before it the snapshot carried the resident size and cumulative counters (bytes read, atoms built, bodies hydrated since
boot) and no occupancy at all, so a 5-6 GiB resident size was attributed from source-side byte counters and lab tracemalloc
runs. The block reads the occupancy of every in-process cache that holds session content (hydrated bodies, assembly
entries, parse slots, live lazy indexes, the materialized-atom LRU's slots, the judge-usage rows, the built chat tabs and
their serialized strings, the preview images) beside the allocator's and the collector's own gauges.

Pinned here: the gauges move by exactly what a test adds and fall back when it removes it, and two snapshots move no
cumulative counter (a); the block serializes and every leaf is a plain scalar (b); a container the source lacks or an
accessor that raises yields None for its key, the snapshot still serves, and the failure is said once per key, not per
snapshot, a built-chat entry of a shape the gauge does not know being such a failure and never a silent under-count (c); a snapshot reads no file, builds nothing, evicts nothing and collects nothing (d); and the read stays under
5 ms over populated caches (e). Synthetic fixtures only: invented strings and placeholder uuids."""
import contextlib
import gc
import io
import json
import os
import statistics
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the tests/test_perf_stats.py preamble): the modules resolve their state root at import,
# and only pytest runs conftest's floor. The root minted here is outside conftest's belt, so session hosts are switched off
# in it as well (a state root with no `session-hosts` file starts a real host for any session it connects; none is
# connected here, and the file makes that so whatever a later test in this module does).
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_perf_heap", os.path.join(BIN, "romp-kernel"))
em, jd = km.em, km.jd

HEAP_KEYS = {"allocatedBlocks", "gc", "tracing", "hydrated", "assemblyEntries", "parseSlots", "lazyIndexes",
             "unownedIndexes",                                                    # the indexes no entry owns (2026-10-19)
             "materializedLruSlots", "judgeUsageRows", "builtChat", "imgCache"}
SID = "11111111-2222-3333-4444-%012d"       # placeholder uuids, one per synthetic tab
IMG_URL = "data:image/png;base64,"           # the image cache holds data URLs; the payload here is invented


def _leaves(v):
    """Every scalar inside a nested dict/list value."""
    if isinstance(v, dict):
        for x in v.values():
            yield from _leaves(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _leaves(x)
    else:
        yield v


@contextlib.contextmanager
def _without(mod, *names):
    """The module with these attributes ABSENT (an older source beside this kernel, a test's stub), restored on exit."""
    saved = {n: getattr(mod, n) for n in names}
    for n in names:
        delattr(mod, n)
    try:
        yield
    finally:
        for n, v in saved.items():
            setattr(mod, n, v)


def _em_lacking(*names):
    """The kernel's `em` swapped for a stub that carries everything the event model has EXCEPT these names: the snapshot's
    other reads (checkpoint_stats, asm_index_stats, ...) are the real functions with the real module's globals, so only the
    heap reads see the absence, as they would beside an older event model that has not got the container."""
    stub = types.SimpleNamespace(**{k: v for k, v in vars(em).items() if k not in names})
    return mock.patch.object(km, "em", stub)


class _Caches(unittest.TestCase):
    """Synthetic entries in the pusher's caches (images, built tabs), the reader's hydrated-body memo, the materialized LRU
    and the judge-usage rows, every one removed on exit whatever the test did, with the byte counters put back."""

    def setUp(self):
        self.st = km._PerfStats()
        self.imgs, self.tabs, self.hyd, self.mat = [], [], [], []
        self.rows_before = None
        km._HEAP_SAID.clear()

    def tearDown(self):
        for k in self.imgs:
            km._img_cache.pop(k, None)
        for k in self.tabs:
            km._built_chat.pop(k, None)
        with em._ASM_CKPT_LOCK:
            for k in self.hyd:
                old = em._HYDRATED.pop(k, None)
                if old is not None:
                    em._HYDRATED_BYTES[0] -= old[1]
        with em._MAT_LOCK:
            for k in self.mat:
                em._MAT_LRU.pop(k, None)
        if self.rows_before is not None:
            km._JUDGE_USAGE_CACHE["rows"] = self.rows_before
        km._HEAP_SAID.clear()

    def add_images(self, n, size):
        for i in range(n):
            k = "/notes-api/web/img-%d.png:1:2:%d" % (i, len(self.imgs))
            km._img_cache[k] = IMG_URL + "A" * (size - len(IMG_URL))
            self.imgs.append(k)

    def add_tabs(self, n, size, serialized=True, events=0):
        """Built tabs as the pusher caches them: (sig, payload, serialized, deps). The shipped wire stores no serialized
        string (None); only an index-wire client leaves one, so `serialized` plants that shape when asked."""
        for i in range(n):
            sid = SID % (len(self.tabs) + 1)
            km._built_chat[sid] = (("sig",), {"events": [None] * events}, ("x" * size) if serialized else None, None)
            self.tabs.append(sid)

    def add_hydrated(self, n, size):
        with em._ASM_CKPT_LOCK:                          # the insertion the reader's own path does, under its lock (no helper exists)
            for i in range(n):
                u = "heap-%d-%d" % (len(self.hyd), i)
                em._HYDRATED[u] = ({"uuid": u}, size)
                em._HYDRATED_BYTES[0] += size
                self.hyd.append(u)

    def add_mat_slots(self, n):
        with em._MAT_LOCK:
            for i in range(n):
                k = ("synthetic-heap", len(self.mat))
                em._MAT_LRU[k] = ([], 0)
                self.mat.append(k)

    def set_rows(self, n):
        self.rows_before = km._JUDGE_USAGE_CACHE["rows"]
        km._JUDGE_USAGE_CACHE["rows"] = [{"t": i, "in": 1, "out": 1} for i in range(n)]

    def remove_all(self):
        self.tearDown()
        self.imgs, self.tabs, self.hyd, self.mat = [], [], [], []
        self.rows_before = None


class Gauges(_Caches):
    """(a) The numbers are what is held: they move by exactly what was added, fall back on removal, and taking the snapshot
    moves no cumulative counter."""

    def test_the_block_has_the_documented_keys(self):
        h = self.st.snapshot()["heap"]
        self.assertEqual(set(h), HEAP_KEYS)
        self.assertEqual(set(h["gc"]), {"enabled", "counts", "thresholds", "stats"})
        self.assertEqual(set(h["hydrated"]), {"entries", "bytes", "capBytes", "hits", "evictions", "evictedBytes"})
        self.assertEqual(set(h["builtChat"]), {"tabs", "events", "serializedBytes"})
        self.assertEqual(set(h["imgCache"]), {"entries", "bytes"})
        self.assertIsInstance(h["tracing"], bool)
        self.assertEqual(h["hydrated"]["capBytes"], em._HYDRATED_CAP)
        self.assertEqual(h["gc"]["enabled"], gc.isenabled())
        self.assertEqual(h["gc"]["thresholds"], list(gc.get_threshold()))
        self.assertEqual(len(h["gc"]["stats"]), len(gc.get_stats()))
        for k in ("allocatedBlocks", "assemblyEntries", "parseSlots", "lazyIndexes", "materializedLruSlots", "judgeUsageRows"):
            self.assertIsInstance(h[k], int, k)
            self.assertGreaterEqual(h[k], 0, k)

    def test_the_gauges_move_by_exactly_what_was_added_and_fall_back_on_removal(self):
        h0 = self.st.snapshot()["heap"]
        self.add_images(3, 40)
        self.add_tabs(2, 1000, events=5)                   # index-wire shape: a cached JSON string beside the payload
        self.add_tabs(1, 1000, serialized=False, events=3) # the shipped wire's shape: events cached, no string, zero bytes
        self.add_hydrated(4, 250)
        self.add_mat_slots(7)
        self.set_rows(len(km._JUDGE_USAGE_CACHE["rows"]) + 11)
        h1 = self.st.snapshot()["heap"]
        self.assertEqual(h1["imgCache"]["entries"] - h0["imgCache"]["entries"], 3)
        self.assertEqual(h1["imgCache"]["bytes"] - h0["imgCache"]["bytes"], 120)
        self.assertEqual(h1["builtChat"]["tabs"] - h0["builtChat"]["tabs"], 3)
        self.assertEqual(h1["builtChat"]["serializedBytes"] - h0["builtChat"]["serializedBytes"], 2000)
        self.assertEqual(h1["builtChat"]["events"] - h0["builtChat"]["events"], 13, "the resident measure counts every tab's events")
        self.assertEqual(h1["hydrated"]["entries"] - h0["hydrated"]["entries"], 4)
        self.assertEqual(h1["hydrated"]["bytes"] - h0["hydrated"]["bytes"], 1000)
        self.assertEqual(h1["materializedLruSlots"] - h0["materializedLruSlots"], 7)
        self.assertEqual(h1["judgeUsageRows"] - h0["judgeUsageRows"], 11)
        self.remove_all()
        h2 = self.st.snapshot()["heap"]
        for k in ("imgCache", "builtChat", "hydrated", "materializedLruSlots", "judgeUsageRows"):
            self.assertEqual(h2[k], h0[k], k)

    def test_an_entry_removed_under_the_readers_lock_leaves_both_the_count_and_the_bytes(self):
        """The gauge reads the reader's own counter and dict, so a removal done the way the reader's cap path does it (pop the
        entry, subtract its bytes, under the same lock) shows as a fall in both, not a stale sum; the reader's cap path itself
        is exercised by the event model's own tests."""
        self.add_hydrated(2, 100)
        h1 = self.st.snapshot()["heap"]["hydrated"]
        with em._ASM_CKPT_LOCK:                          # what the reader does past its cap: the least recently used body goes
            old = em._HYDRATED.pop(self.hyd[0])
            em._HYDRATED_BYTES[0] -= old[1]
        self.hyd.pop(0)
        h2 = self.st.snapshot()["heap"]["hydrated"]
        self.assertEqual((h1["entries"] - h2["entries"], h1["bytes"] - h2["bytes"]), (1, 100))

    def test_two_snapshots_move_no_cumulative_counter(self):
        self.add_images(2, 30)
        self.add_tabs(1, 100)
        self.add_hydrated(2, 50)
        s1 = self.st.snapshot()
        s2 = self.st.snapshot()
        for path in (("recordCache", "inserts"), ("asmIndex", "materialized"), ("builds", "chat", "built"),
                     ("asmCheckpoint", "hydratedAtoms"), ("asmCheckpoint", "hydratedBytes")):
            a, b = s1, s2
            for k in path:
                a, b = a[k], b[k]
            self.assertEqual(a, b, "/".join(path))
        self.assertEqual(s1["heap"]["hydrated"], s2["heap"]["hydrated"])
        self.assertEqual(s1["heap"]["builtChat"], s2["heap"]["builtChat"])


class Serialization(unittest.TestCase):
    """(b) The block rides GET /perf as JSON: every leaf is an int, a bool, a float or None."""

    def test_the_snapshot_serializes_and_every_heap_leaf_is_a_plain_scalar(self):
        snap = km._PerfStats().snapshot()
        json.dumps(snap)
        leaves = list(_leaves(snap["heap"]))
        self.assertGreater(len(leaves), 20)
        for v in leaves:
            self.assertIsInstance(v, (int, bool, float, type(None)), repr(v))
        self.assertTrue(any(isinstance(v, bool) for v in leaves), "tracing and gc.enabled are bools")


class AbsentSupport(_Caches):
    """(c) A container the source lacks, or an accessor that raises, is None for its key; the snapshot still serves; the
    failure is said once per key across repeated snapshots, never once per snapshot."""

    def test_missing_containers_and_a_raising_accessor_yield_none_said_once(self):
        err = io.StringIO()
        with _em_lacking("_HYDRATED", "_MAT_LRU", "_LIVE_INDEXES"), \
             mock.patch.object(gc, "get_stats", side_effect=RuntimeError("no stats here")), \
             redirect_stderr(err):
            snaps = [self.st.snapshot() for _ in range(3)]
        for snap in snaps:
            json.dumps(snap)
            h = snap["heap"]
            self.assertEqual(set(h), HEAP_KEYS, "the key set holds with gauges missing")
            self.assertIsNone(h["hydrated"])
            self.assertIsNone(h["materializedLruSlots"])
            self.assertIsNone(h["lazyIndexes"])
            self.assertIsNone(h["gc"]["stats"])
            self.assertIsInstance(h["gc"]["counts"], list, "the other collector accessors still answer")
            self.assertIsInstance(h["assemblyEntries"], int, "the containers still present still answer")
            self.assertIsInstance(h["builtChat"]["tabs"], int)
        lines = [l for l in err.getvalue().splitlines() if l.startswith("perf: heap.")]
        said = sorted(l.split()[1] for l in lines)
        self.assertEqual(said, ["heap.gc.stats", "heap.hydrated", "heap.lazyIndexes", "heap.materializedLruSlots"],
                         "one line per failed key over three snapshots, none twice: %r" % lines)
        self.assertIn("RuntimeError", next(l for l in lines if "gc.stats" in l))
        self.assertIn("AttributeError", next(l for l in lines if "hydrated" in l))
        err2 = io.StringIO()
        with redirect_stderr(err2):
            h = self.st.snapshot()["heap"]
        self.assertIsInstance(h["hydrated"], dict, "the containers restored, the gauges are back")
        self.assertIsInstance(h["gc"]["stats"], list)
        self.assertEqual(err2.getvalue(), "", "nothing more to say once the reads succeed")

    def test_a_runtime_lacking_an_accessor_yields_none_for_that_key_alone(self):
        """A bare attribute passed to _heap_read is evaluated before the guard runs; the lookups sit inside lambdas so a
        runtime without sys.getallocatedblocks, gc.isenabled or tracemalloc.is_tracing answers None for that key and the
        snapshot still serves (review find, 2026-09-15: the first cut raised AttributeError past the guard)."""
        import types
        real_sys, real_gc, real_tm = km.sys, km.gc, km.tracemalloc
        stub_sys = types.SimpleNamespace(stderr=real_sys.stderr, exc_info=real_sys.exc_info)   # no getallocatedblocks
        stub_gc = types.SimpleNamespace(get_count=real_gc.get_count, get_threshold=real_gc.get_threshold,
                                        get_stats=real_gc.get_stats)                          # no isenabled
        stub_tm = types.SimpleNamespace()                                                      # no is_tracing
        km._HEAP_SAID.clear()
        try:
            km.sys, km.gc, km.tracemalloc = stub_sys, stub_gc, stub_tm
            h = self.st.snapshot()["heap"]
        finally:
            km.sys, km.gc, km.tracemalloc = real_sys, real_gc, real_tm
        self.assertIsNone(h["allocatedBlocks"]); self.assertIsNone(h["gc"]["enabled"]); self.assertIsNone(h["tracing"])
        self.assertIsInstance(h["gc"]["counts"], list, "the accessors the stub has still answer")
        self.assertIsInstance(h["imgCache"], dict, "the rest of the block is untouched")
        self.assertTrue({"allocatedBlocks", "gc.enabled", "tracing"} <= km._HEAP_SAID, "each said once")

    def test_a_missing_kernel_container_is_none_for_its_key_alone(self):
        err = io.StringIO()
        with _without(km, "_JUDGE_USAGE_CACHE", "_img_cache"), redirect_stderr(err):
            h1 = self.st.snapshot()["heap"]
            h2 = self.st.snapshot()["heap"]
        for h in (h1, h2):
            self.assertIsNone(h["judgeUsageRows"])
            self.assertIsNone(h["imgCache"])
            self.assertIsInstance(h["builtChat"], dict)
            self.assertIsInstance(h["hydrated"], dict)
        self.assertEqual(sorted(l.split()[1] for l in err.getvalue().splitlines() if l.startswith("perf: heap.")),
                         ["heap.imgCache", "heap.judgeUsageRows"])


class NoSideEffects(_Caches):
    """(d) A snapshot observes: no file read, no build, no eviction, no collection."""

    def test_two_snapshots_read_no_file_build_nothing_evict_nothing_and_collect_nothing(self):
        self.add_images(5, 10)
        self.add_tabs(2, 100)
        self.add_hydrated(3, 100)
        self.add_mat_slots(4)

        def lens():
            with em._ASM_CKPT_LOCK:
                hyd = (len(em._HYDRATED), em._HYDRATED_BYTES[0])
            with em._MAT_LOCK:
                mat = len(em._MAT_LRU)
            with em._ASM_LOCK:
                asm = len(em._ASM_CACHE)
            with jd._PARSE_CACHE_LOCK:
                slots = len(jd._PARSE_CACHE)
            return (hyd, mat, asm, slots, len(km._img_cache), len(km._built_chat), len(km._JUDGE_USAGE_CACHE["rows"]))

        gc.disable()   # the only way a collection can happen below is an explicit collect: the tallies then prove none ran
        try:
            keep = [[] for _ in range(20000)]              # tracked allocations: the young count climbs past any threshold and a
            count0 = gc.get_count()[0]                    #  collect would zero it (the snapshot's own allocations are far fewer)
            self.assertGreaterEqual(count0, 20000)
            reads0, inserts0 = em.read_bytes_total(), em.record_cache_stats()["inserts"]
            collections0 = [s["collections"] for s in gc.get_stats()]
            lens0 = lens()
            s1 = self.st.snapshot()
            s2 = self.st.snapshot()
            self.assertEqual(em.read_bytes_total(), reads0, "no file read")
            self.assertEqual(em.record_cache_stats()["inserts"], inserts0, "no record parsed into the cache")
            self.assertEqual([s["collections"] for s in gc.get_stats()], collections0, "no collection, in any generation")
            self.assertGreaterEqual(gc.get_count()[0], count0, "the young count was not reset by the read")
            self.assertEqual(lens(), lens0, "no eviction, no fill, in any container")
            self.assertEqual(s1["builds"], s2["builds"], "no build")
            self.assertEqual(s1["heap"]["hydrated"], s2["heap"]["hydrated"])
            self.assertEqual(s1["heap"]["materializedLruSlots"], s2["heap"]["materializedLruSlots"])
            del keep
        finally:
            gc.enable()


class Overhead(_Caches):
    """(e) The read is cheap over populated caches: 1,000 images, 50 tabs with 100 KB strings, 100,000 hydrated entries,
    10,000 LRU slots, 50,000 usage rows; the median of 20 calls under 5 ms."""

    def test_the_read_stays_under_five_milliseconds_over_populated_caches(self):
        self.add_images(1000, 100)
        self.add_tabs(50, 100 * 1024)
        self.add_hydrated(100_000, 10)
        self.add_mat_slots(10_000)
        self.set_rows(50_000)
        km._heap_stats()                                  # one warm call, the code paths compiled
        took = []
        for _ in range(20):
            t0 = time.perf_counter()
            h = km._heap_stats()
            took.append(time.perf_counter() - t0)
        self.assertEqual(h["imgCache"]["entries"] - 0, len(km._img_cache))
        self.assertGreaterEqual(h["hydrated"]["entries"], 100_000)
        self.assertGreaterEqual(h["materializedLruSlots"], 10_000)
        self.assertGreaterEqual(h["judgeUsageRows"], 50_000)
        self.assertGreaterEqual(h["builtChat"]["serializedBytes"], 50 * 100 * 1024)
        med = statistics.median(took)
        print("heap block: median %.0f us over 20 calls (min %.0f us, max %.0f us)"
              % (med * 1e6, min(took) * 1e6, max(took) * 1e6))
        self.assertLess(med, 0.005, "median %.3f ms" % (med * 1000))


class ShapeChange(_Caches):
    """A built-chat entry of a shape the gauge does not know is a failed read, not a silent under-count: the gauge is None and
    the key is said once, as every other gauge does (review round: a first cut skipped non-tuples and read the fields by
    position with a fallback, so a later change to what the pusher caches would have read as fewer tabs and events)."""

    def test_a_malformed_entry_turns_the_gauge_none_and_says_the_key_once(self):
        self.add_tabs(2, 100, events=4)
        h0 = self.st.snapshot()["heap"]["builtChat"]
        self.assertGreaterEqual(h0["tabs"], 2)
        bad = SID % 999                                           # a private key, popped with the module's other plants
        km._built_chat[bad] = {"events": [None] * 4}              # the payload alone, not the (sig, payload, serialized, deps) tuple
        self.tabs.append(bad)
        err = io.StringIO()
        with redirect_stderr(err):
            h1 = self.st.snapshot()["heap"]
            h2 = self.st.snapshot()["heap"]
        self.assertIsNone(h1["builtChat"]); self.assertIsNone(h2["builtChat"])
        self.assertIsInstance(h1["imgCache"], dict, "the rest of the block still serves")
        lines = [l for l in err.getvalue().splitlines() if l.startswith("perf: heap.")]
        self.assertEqual(len(lines), 1, "said once over two snapshots: %r" % lines)
        self.assertTrue(lines[0].startswith("perf: heap.builtChat unavailable: ValueError: "), lines[0])
        self.assertIn("dict", lines[0], "the line names the shape it met")
        km._built_chat.pop(bad); self.tabs.remove(bad)
        err2 = io.StringIO()
        with redirect_stderr(err2):
            h3 = self.st.snapshot()["heap"]["builtChat"]
        self.assertEqual(h3, h0, "well-formed entries alone: the counts are what they were")
        self.assertEqual(err2.getvalue(), "", "nothing more to say once the read succeeds")

    def test_a_short_tuple_and_a_payload_of_another_type_are_shapes_too(self):
        for bad, entry, word in ((SID % 998, (("sig",), {"events": []}), "length 2"),
                                 (SID % 997, (("sig",), ["not", "a", "dict"], None, None), "payload is a list"),
                                 (SID % 996, (("sig",), {"events": {}}, None, None), "events is a dict")):   # falsy, not a list: a shape, never an empty list
            km._HEAP_SAID.clear()
            km._built_chat[bad] = entry
            self.tabs.append(bad)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(self.st.snapshot()["heap"]["builtChat"], word)
            self.assertIn("perf: heap.builtChat unavailable: ValueError", err.getvalue(), word)
            self.assertIn(word, err.getvalue())
            km._built_chat.pop(bad); self.tabs.remove(bad)


class Documented(unittest.TestCase):
    """docs/reference.md's GET /perf section names the block, every gauge in it, and the two caveats: gauge against
    counter (gc.stats is cumulative) and slots against atoms (materializedLruSlots is an upper bound under weak ownership),
    and points at the record cache, the largest resident holder, which is not a heap gauge but rides the same response."""

    def test_the_reference_names_every_gauge_and_both_caveats(self):
        doc = Path(HERE).parent.joinpath("docs", "reference.md").read_text()
        para = doc[doc.index("- `heap`:"):]
        para = para[:para.index("\n- `", 10)]
        h = km._PerfStats().snapshot()["heap"]
        for k in h:
            self.assertIn("`%s`" % k, para, k)
        for k in ("entries", "bytes", "capBytes", "tabs", "events", "serializedBytes", "enabled", "counts", "thresholds", "stats",
                  "collections", "collected", "uncollectable"):
            self.assertIn("`%s`" % k, para, k)
        self.assertIn("GAUGE", para)
        self.assertIn("cumulative", para)
        self.assertIn("upper bound", para)
        self.assertIn("`null`", para)
        self.assertIn("`recordCache`", para, "the record cache is pointed at from inside the heap bullet")
        self.assertIn("`budgetBytes`", para, "with the key its occupancy is read against")


if __name__ == "__main__":
    unittest.main()
