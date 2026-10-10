#!/usr/bin/env python3
"""The /perf snapshot's `gc` block (2026-09-16): the interpreter's collections, counted and timed per generation by a
gc.callbacks hook the kernel installs once at boot, with every pusher cycle's and jobs pass's split row carrying the
collections that ran inside it.

Before it nothing in the kernel exposed a collection. Pusher cycles stalled for 9-33 s and a native profile caught a 9.2 s
generation-2 collection charged to whichever stage happened to be running, with no counter to tie the one to the other.
The hook records, per generation, how many collections ran, their summed, largest and last wall milliseconds on the
collecting thread, and how many objects the last one freed; cycle_begin marks the tallies and the split row carries the
cycle's own delta as `gc`, so one slow row names the full collection it paid for.

The hard constraint pinned here (a review found a kernel-wide deadlock in a first draft): the callback never takes the
stats lock. CPython runs an automatic collection on the thread whose allocation crossed the threshold, at that thread's
next eval-breaker check, which can be INSIDE one of the stats' own `with self.lock:` regions; a callback that then took
the lock waited on its own thread forever, and since the collector's `collecting` flag stays set through the callbacks,
every later collection in the process was skipped as well (a lab reproduction: the hung callback left a second
collector's tallies empty and an explicit gc.collect() returning 0).

Pinned: the block's keys and a fresh collector's zeros (a); a forced full collection with the hook installed counts under
generation 2 with a positive pause (b); the deadlock regression, a helper thread allocating past the young threshold under
the stats lock finishes (c); the split rows carry the cycle's own delta, zero for a cycle with no collection and None for a
cycle closed without an opening mark (d); a failing callback counts an error, says the first once on stderr and raises
nothing (e); the kernel-samples
row carries the generation-2 tallies (f); main installs the hook before the boot warm (g); the reference names every
field (h). Synthetic fixtures only."""
import gc
import inspect
import io
import json
import os
import re
import tempfile
import types
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the tests/test_perf_heap_block.py preamble): the modules resolve their state root at
# import, and only pytest runs conftest's floor. The root minted here is outside conftest's belt, so session hosts are
# switched off in it as well.
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
km = load_source("romp_kernel_perf_gc", os.path.join(BIN, "romp-kernel"))

GC_KEYS = {"gen", "thresholds", "counts", "frozenAsOfCleanup", "frozenAsOfCleanupAt", "errors", "hooked", "freeze"}
FREEZE_KEYS = {"enabled", "active", "loadTrees", "backstopFoldins", "freezes", "reclaims", "collections", "survivors", "lastReleaseSurvivors", "lastReleaseSids", "endedPending", "lastReconcileMs", "lastReconcileKind", "totalReconcileMs", "errors",
               # the full-collection freeze (2026-10-07)
               "fullFreezeMs", "fullFreezes", "fullT2", "fullSinceReclaim", "fullBackstopRatio", "fullMsSinceReclaim",
               "fullRefMs", "lastFullMs", "callbackErrors", "fullFreezeSkips", "fullForceS", "forced", "owedForS",
               "reclaimSkips", "frozenAsOfCleanup", "frozenAsOfCleanupAt", "cleanupMaxS"}
GEN_KEYS = {"collections", "msSum", "msMax", "msLast", "collectedLast"}
ROW_GC_KEYS = {"n0", "n1", "n2", "ms2"}


def _total(snap):
    """Collections across every generation in a snapshot's gc block."""
    return sum(row["collections"] for row in snap["gc"]["gen"].values())


class _Hooked(unittest.TestCase):
    """A fresh collector with its hook installed for the test and removed after it, whatever happens (a hook left behind
    would count into a later test's collector, and the module's own instance is hooked by main alone)."""

    def setUp(self):
        self.assertTrue(gc.isenabled(), "the tests below need the automatic collector")
        self.st = km._PerfStats()
        self.st.install_gc_hook()
        self.addCleanup(self.st.remove_gc_hook)


class Shape(unittest.TestCase):
    """(a) The block, its keys, and a fresh collector's zeros."""

    def test_the_snapshot_carries_the_gc_block_with_the_documented_keys(self):
        snap = km._PerfStats().snapshot()
        self.assertIn("gc", snap)
        g = snap["gc"]
        self.assertEqual(set(g), GC_KEYS)
        self.assertEqual(set(g["gen"]), {"0", "1", "2"})
        for row in g["gen"].values():
            self.assertEqual(set(row), GEN_KEYS)
            self.assertEqual(row, {"collections": 0, "msSum": 0.0, "msMax": 0.0, "msLast": 0.0, "collectedLast": 0})
        self.assertEqual(g["thresholds"], list(gc.get_threshold()))
        self.assertEqual(len(g["counts"]), 3)
        self.assertEqual((g["frozenAsOfCleanup"], g["frozenAsOfCleanupAt"]),
                         (km._GC_FREEZE.frozen_as_of_cleanup, km._GC_FREEZE.frozen_as_of_cleanup_at),
                         "the count re-read at the last cleanup and its time, never a walk on read")
        self.assertEqual(g["errors"], 0)
        self.assertEqual(set(g["freeze"]), FREEZE_KEYS, "the #1735 freeze sub-block carries its state and reconcile counters")
        self.assertFalse(g["hooked"], "a collector's hook is installed only when asked")
        json.dumps(g)

    def test_the_frozen_count_is_served_without_walking_the_frozen_list(self):
        """2026-10-07 review: gc.get_freeze_count() walks the frozen list (seconds once the long-lived heap is frozen), so
        /perf serves the freeze controller's stored count and never calls it on the read path."""
        from unittest import mock
        saved = (km._GC_FREEZE.frozen_as_of_cleanup, km._GC_FREEZE.frozen_as_of_cleanup_at)
        km._GC_FREEZE.frozen_as_of_cleanup, km._GC_FREEZE.frozen_as_of_cleanup_at = 123456, 1791000000.0
        try:
            with mock.patch.object(km.gc, "get_freeze_count", side_effect=AssertionError("walked on read")) as walk, \
                    mock.patch.object(km.gc, "get_objects", side_effect=AssertionError("listed on read")) as listing:
                g = km._PerfStats().snapshot()["gc"]
            self.assertFalse(walk.called or listing.called, "the read path never walks or lists the heap")
            self.assertEqual((g["frozenAsOfCleanup"], g["frozenAsOfCleanupAt"]), (123456, 1791000000.0))
        finally:
            km._GC_FREEZE.frozen_as_of_cleanup, km._GC_FREEZE.frozen_as_of_cleanup_at = saved

    def test_a_snapshot_reads_the_tallies_and_does_not_collect(self):
        st = km._PerfStats()
        st.install_gc_hook()
        try:
            gc.disable()   # the only collection that could run below is an explicit one, and none is asked
            try:
                before = st.snapshot()
                after = st.snapshot()
            finally:
                gc.enable()
            self.assertEqual(before["gc"]["gen"], after["gc"]["gen"], "two reads move no tally")
        finally:
            st.remove_gc_hook()


class Counting(_Hooked):
    """(b) A forced full collection is counted and timed under generation 2."""

    def test_a_forced_full_collection_counts_under_generation_two_with_a_positive_pause(self):
        before = self.st.snapshot()["gc"]["gen"]["2"]
        gc.collect(2)
        snap = self.st.snapshot()
        after = snap["gc"]["gen"]["2"]
        self.assertGreaterEqual(after["collections"] - before["collections"], 1)   # >=: an automatic full collection may land too
        self.assertGreater(after["msLast"], 0.0)
        self.assertGreaterEqual(after["msMax"], after["msLast"])
        self.assertGreaterEqual(after["msSum"], after["msMax"])
        self.assertIsInstance(after["collectedLast"], int)
        self.assertTrue(snap["gc"]["hooked"])

    def test_install_is_idempotent_and_remove_leaves_no_entry(self):
        self.st.install_gc_hook()                                   # a second install: still one entry
        self.assertEqual(gc.callbacks.count(self.st.gc_event), 1)
        self.st.remove_gc_hook()
        self.assertNotIn(self.st.gc_event, gc.callbacks)
        self.st.remove_gc_hook()                                    # a second remove: a no-op, not a raise
        self.assertFalse(self.st.snapshot()["gc"]["hooked"])

    def test_reset_zeroes_the_tallies(self):
        gc.collect(2)
        self.assertGreaterEqual(_total(self.st.snapshot()), 1)
        self.st.reset()
        self.assertEqual(_total(self.st.snapshot()), 0)


class LockFree(_Hooked):
    """(c) The deadlock regression: a collection triggered on a thread that holds the stats lock finishes, because the
    callback never waits on that lock. Under the first draft's callback (one that took self.lock) the helper thread below
    never returns: the collector runs on it, inside the locked region, and the callback blocks on the lock it holds."""

    def test_a_collection_triggered_under_the_stats_lock_finishes(self):
        st = self.st
        gc.collect()                                     # the young count starts near zero, so the region's own allocations cross it
        before = _total(st.snapshot())
        seen = []                                        # (thread, lock held) at each collection's start: the proof the hazard ran

        def witness(phase, info):
            if phase == "start":
                seen.append((threading.current_thread().name, st.lock.locked()))
        gc.callbacks.append(witness)
        self.addCleanup(lambda: gc.callbacks.remove(witness))
        n = 2 * gc.get_threshold()[0] + 100              # past the young threshold whatever this interpreter's default (700 on
                                                         #  3.12, 2000 on 3.13): the collector runs HERE, on this thread, lock held
        def body():
            with st.lock:
                keep = [[i] for i in range(n)]
            del keep

        t = threading.Thread(target=body, daemon=True, name="gc-under-lock")
        t.start()
        t.join(10.0)
        self.assertFalse(t.is_alive(), "the callback waited on the lock its own thread holds")
        self.assertIn(("gc-under-lock", True), seen, "a collection started on the helper thread with the lock held: the hazard was exercised")
        self.assertGreaterEqual(_total(st.snapshot()) - before, 1)

    def test_the_callback_names_no_lock_in_its_body(self):
        body = inspect.getsource(km._PerfStats.gc_event).split('"""')[-1]   # after the docstring, which names the lock it avoids
        self.assertNotIn("self.lock", body)
        self.assertIsNone(re.search(r"^\s*with\s", body, re.M), "no lock, no context manager at all on the collector's path")


class Attribution(_Hooked):
    """(d) The split rows carry the cycle's own delta."""

    def test_the_pusher_split_row_carries_the_cycles_collections(self):
        st = self.st
        st.cycle_begin()
        gc.collect(2)
        st.stage("push", 0.001)
        st.cycle(0.01)
        snap = st.snapshot(ring_all=True)
        row = snap["pusher"]["stageRing"][-1]
        self.assertEqual(set(row), {"s", "t", "stages", "gc"})
        self.assertEqual(set(row["gc"]), ROW_GC_KEYS)
        self.assertGreaterEqual(row["gc"]["n2"], 1)
        self.assertGreater(row["gc"]["ms2"], 0.0)
        self.assertEqual(snap["pusher"]["firstCycle"]["gc"], row["gc"], "the boot's first cycle keeps its delta too")
        json.dumps(row)

    def test_a_cycle_with_no_collection_reads_zero_the_delta_not_the_tally(self):
        st = self.st
        st.cycle_begin(); gc.collect(2); st.cycle(0.01)           # a full collection inside the first cycle
        gc.disable()                                             # the second can collect only on an explicit ask, and none comes
        try:
            st.cycle_begin(); st.cycle(0.01)
        finally:
            gc.enable()
        rows = st.snapshot(ring_all=True)["pusher"]["stageRing"]
        self.assertGreaterEqual(rows[-2]["gc"]["n2"], 1)
        self.assertEqual(rows[-1]["gc"], {"n0": 0, "n1": 0, "n2": 0, "ms2": 0.0})

    def test_the_jobs_pass_split_carries_its_own_delta(self):
        st = self.st
        st.cycle_begin("jobs"); gc.collect(2); st.jobs_pass(0.01)
        snap = st.snapshot(ring_all=True)
        row = snap["jobs"]["stageRing"][-1]
        self.assertGreaterEqual(row["gc"]["n2"], 1)
        self.assertGreater(row["gc"]["ms2"], 0.0)
        self.assertEqual(snap["jobs"]["firstPass"]["gc"], row["gc"])

    def test_a_cycle_closed_without_an_opening_mark_says_so(self):
        st = km._PerfStats()
        st.cycle(0.01)                                           # no cycle_begin: there is no base to difference against
        row = st.snapshot(ring_all=True)["pusher"]["stageRing"][-1]
        self.assertIn("gc", row)
        self.assertIsNone(row["gc"])


class Robustness(_Hooked):
    """(e) A failure inside the callback is counted and never raises into the collector; the hook goes on counting. The
    first failure in the process is said once on stderr, the rest are the count alone (review round: a count nobody reads
    left a broken hook silent)."""

    def test_a_failing_callback_counts_an_error_and_raises_nothing(self):
        st = self.st
        st.gc_event("stop", None)                                # a malformed event: the body raises inside, the guard counts it
        self.assertEqual(st.snapshot()["gc"]["errors"], 1)
        before = st.snapshot()["gc"]["gen"]["2"]["collections"]
        gc.collect(2)
        self.assertGreaterEqual(st.snapshot()["gc"]["gen"]["2"]["collections"] - before, 1, "the hook still counts after its failure")

    def test_the_first_failure_is_said_once_on_stderr_and_the_rest_are_counted_only(self):
        saved = km._GC_HOOK_SAID[0]
        km._GC_HOOK_SAID[0] = False
        self.addCleanup(km._GC_HOOK_SAID.__setitem__, 0, saved)
        st = km._PerfStats()                                     # private and unhooked: no collection moves its tallies
        before = st.snapshot()["gc"]["gen"]
        err = io.StringIO()
        with redirect_stderr(err):
            st.gc_event("stop", None)                            # info None: the body raises inside, twice
            st.gc_event("stop", None)
        self.assertEqual(st.gc_errors, 2, "both failures counted")
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "said once: %r" % lines)
        self.assertTrue(lines[0].startswith("perf: gc hook: TypeError: "), lines[0])
        self.assertTrue(lines[0].endswith(" (further failures counted only)"), lines[0])
        self.assertEqual(st.snapshot()["gc"]["gen"], before, "a failed event moves no tally")
        self.assertEqual(st.snapshot()["gc"]["errors"], 2)

    def test_a_failing_stderr_never_escapes_into_the_collector(self):
        """The line itself sits inside a guard: a stderr whose write raises leaves the failure counted and raises nothing."""
        saved = km._GC_HOOK_SAID[0]
        km._GC_HOOK_SAID[0] = False
        self.addCleanup(km._GC_HOOK_SAID.__setitem__, 0, saved)
        st = km._PerfStats()

        class _Broken(io.StringIO):
            def write(self, s):
                raise OSError("stderr is closed")
        with redirect_stderr(_Broken()):
            st.gc_event("stop", None)
        self.assertEqual(st.gc_errors, 1)
        self.assertTrue(km._GC_HOOK_SAID[0], "the say was attempted once and is not retried")

    def test_a_runtime_lacking_a_collector_accessor_answers_none_for_that_key_alone_said_once(self):
        """The heap block's review find (2026-09-15) applied here: the collector accessors sit inside lambdas behind the per-key
        guard, so a runtime without gc.get_freeze_count or gc.callbacks answers None for those keys, the tallies and the rest
        of the snapshot still serve, and the failure is said once under the block's own name, not once per snapshot."""
        real_gc = km.gc
        stub_gc = types.SimpleNamespace(get_count=real_gc.get_count, get_threshold=real_gc.get_threshold)   # no freeze count, no callbacks
        km._GC_SAID.clear()
        err = io.StringIO()
        try:
            km.gc = stub_gc
            with redirect_stderr(err):
                snaps = [self.st.snapshot() for _ in range(3)]
        finally:
            km.gc = real_gc
        for snap in snaps:
            json.dumps(snap)
            g = snap["gc"]
            self.assertEqual(set(g), GC_KEYS)
            self.assertEqual(g["frozenAsOfCleanup"], km._GC_FREEZE.frozen_as_of_cleanup, "the stored count needs no accessor")
            self.assertIsNone(g["hooked"])
            self.assertIsInstance(g["thresholds"], list, "the accessors the stub has still answer")
            self.assertEqual(set(g["gen"]), {"0", "1", "2"}, "the hook's own tallies need no accessor")
        lines = [l for l in err.getvalue().splitlines() if l.startswith("perf: ")]
        mine = sorted(l.split()[1] for l in lines if l.startswith("perf: gc."))
        self.assertEqual(mine, ["gc.hooked"], "one line per key over three snapshots: %r" % lines)
        # the same stub lacks isenabled and get_stats too, which the HEAP block reads: it says so under ITS name, from its own
        # said-set, so the two blocks' `gc.*` keys never share a line or silence each other
        theirs = sorted(l.split()[1] for l in lines if l.startswith("perf: heap."))
        self.assertEqual(theirs, ["heap.gc.enabled", "heap.gc.stats"], lines)
        self.assertEqual(len(lines), 3, "nothing under any other name: %r" % lines)
        self.assertEqual(km._GC_SAID, {"hooked"})
        self.assertFalse({"frozen", "hooked"} & km._HEAP_SAID, "the heap block's said-set holds its own keys alone")


class SamplesRow(unittest.TestCase):
    """(f) The kernel-samples row carries the generation-2 tallies, cumulative, for a reader to difference per interval."""

    def test_the_row_carries_the_generation_two_collections_and_their_milliseconds(self):
        km._KERNEL_SAMPLES_TAKEN.clear()
        f = km.KERNEL_SAMPLES_FILE
        if os.path.exists(f):
            os.unlink(f)
        st = km._PERF_STATS
        st.install_gc_hook()
        try:
            gc.collect(2)
            self.assertTrue(km._kernel_sample_tick(km._STARTED + 300))
        finally:
            st.remove_gc_hook()
            km._KERNEL_SAMPLES_TAKEN.clear()
        row = json.loads(open(f).read().splitlines()[0])
        self.assertGreaterEqual(row["gcGen2Collections"], 1)
        self.assertGreater(row["gcGen2MsSum"], 0.0)


class Wiring(unittest.TestCase):
    """(g) main installs the hook on the module's collector, once, before the boot warm (the boot's own collections count)."""

    def test_main_installs_the_hook_before_the_boot_warm(self):
        src = inspect.getsource(km.main)
        self.assertEqual(src.count("_PERF_STATS.install_gc_hook()"), 1)
        self.assertLess(src.index("_PERF_STATS.install_gc_hook()"), src.index("_boot_warm()"))


class Documented(unittest.TestCase):
    """(h) docs/reference.md's GET /perf section names the block, every field, the split row's delta and the caveats."""

    def test_the_reference_names_every_field(self):
        doc = Path(HERE).parent.joinpath("docs", "reference.md").read_text()
        para = doc[doc.index("- `gc`:"):]
        para = para[:para.index("\n- `", 10)]
        for k in sorted(GC_KEYS | GEN_KEYS | ROW_GC_KEYS | (FREEZE_KEYS - {"errors"})):   # errors is a generic word; the freeze fields are named in the reference
            self.assertIn("`%s`" % k, para, k)
        self.assertIn("whichever thread triggered it", para)      # wall time, on the thread the collection ran on
        self.assertIn("shows in both rings", para)                  # the delta is process-wide: the rings never sum to gen.collections
        self.assertIn("stageRing", para)
        self.assertIn("`heap.gc`", para, "the overlap with the heap block's collector gauges is named")


if __name__ == "__main__":
    unittest.main()
