#!/usr/bin/env python3
"""The gc-freeze controller's FULL-COLLECTION freeze (2026-10-07, kernel/gc_freeze.py).

The load fold-in keys on the record cache's inserts, so once the cache's decoded records were untracked the heap that
grows outside the cache was frozen only at start-up, and every organic full collection walked it (on the live kernel about
67 an hour, 5.9 s on average, 11% of wall time). The controller now freezes from the collector's own stop callback after an
organic full collection that paused at least `full_freeze_ms`, raises the third collection threshold to `full_t2`, and
owes a backstop reclaim once full-collection pauses reach `full_backstop_ratio` times the largest walk. These tests pin: the trigger (generation, pause, organic only),
the backstop, the safe knob parses and the OFF switch (no callback, thresholds untouched), that a freeze at the stop
callback pins no garbage while a cycle formed later among frozen objects waits for the backstop, and the kernel's wiring.
Every fixture is synthetic. Real-collector tests unfreeze and restore the thresholds in tearDown.
"""
import gc
import os
import re
import sys
import time
import tempfile
import unittest
import weakref
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
GC_FREEZE = os.path.join(ROOT, "kernel", "gc_freeze.py")

sys.path.insert(0, os.path.join(ROOT, "tests"))
from romp_load import load_source   # brought in before the state preamble (the mkdtemp hook and TMPDIR redirect ride it)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off")
os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"

gf = load_source("romp_gc_freeze_full", GC_FREEZE)


class FakeGc:
    def __init__(self):
        self.calls = []
        self.callbacks = []
        self.threshold = (2000, 10, 10)
    def collect(self, *a):
        self.calls.append("collect"); return 0
    def freeze(self):
        self.calls.append("freeze")
    def unfreeze(self):
        self.calls.append("unfreeze")
    def get_threshold(self):
        return self.threshold
    def set_threshold(self, *t):
        self.threshold = tuple(t)


class Clock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t


def _collection(c, clock, generation, ms):
    """Drive one collection through the controller's callback: start, `ms` of pause, stop."""
    c.gc_callback("start", {"generation": generation})
    clock.t += ms / 1000.0
    c.gc_callback("stop", {"generation": generation, "collected": 0, "uncollectable": 0})


class Trigger(unittest.TestCase):
    def _c(self, **kw):
        fake, clock = FakeGc(), Clock()
        kw.setdefault("full_freeze_ms", 250.0)
        return gf.GcFreeze(enabled=True, gc=fake, clock=clock, **kw), fake, clock

    def test_an_expensive_organic_full_collection_freezes_at_its_stop(self):
        c, fake, clock = self._c()
        _collection(c, clock, 2, 300.0)
        self.assertEqual(fake.calls, ["freeze"], "one freeze, no collect: the collector just walked everything")
        self.assertEqual((c.full_freezes, c.frozen, round(c.last_full_ms)), (1, True, 300))

    def test_a_cheap_full_collection_and_the_young_generations_do_not_freeze(self):
        c, fake, clock = self._c()
        _collection(c, clock, 2, 249.0)
        _collection(c, clock, 1, 900.0)
        _collection(c, clock, 0, 900.0)
        self.assertEqual(fake.calls, [], "under the threshold, or not a full collection: no freeze")
        self.assertEqual(c.full_freezes, 0)

    def test_the_controllers_own_collections_never_trigger_it(self):
        c, fake, clock = self._c()
        def slow_collect(*a):
            fake.calls.append("collect")
            _collection(c, clock, 2, 5000.0)      # the reconcile's own collection, as slow as you like
            return 0
        fake.collect = slow_collect
        c.tick(1)                                 # the initial freeze: one collect, one freeze of its own
        self.assertEqual(fake.calls, ["collect", "freeze"])
        self.assertEqual(c.full_freezes, 0, "a collection _run issued re-freezes itself; the callback stands down")

    def test_the_backstop_is_owed_when_full_pauses_reach_the_ratio_of_the_largest_walk(self):
        c, fake, clock = self._c(full_backstop_ratio=3)
        c.tick(1)                                 # initial
        _collection(c, clock, 2, 1000.0)          # a whole-heap walk: freezes, and is the reference (1000 ms)
        _collection(c, clock, 2, 300.0)
        _collection(c, clock, 2, 300.0)           # 1600 ms of full pauses since: under 3 x 1000
        self.assertIsNone(c.tick(1), "under the ratio: nothing owed")
        for _ in range(5):
            _collection(c, clock, 2, 300.0)       # 3100 ms: at 3 x the largest walk
        fake.calls.clear()
        self.assertEqual(c.tick(1), "backstop", "the cheap collections' own spending has paid for a backstop's walk")
        self.assertEqual(fake.calls, ["unfreeze", "collect", "freeze"])
        p = c.perf()
        self.assertEqual((p["fullSinceReclaim"], p["fullMsSinceReclaim"]), (0, 0.0), "and the sums start over")
        self.assertIsNone(c.tick(1))

    def test_no_backstop_is_owed_while_no_full_freeze_has_run_since_the_last_reclaim(self):
        c, fake, clock = self._c(full_backstop_ratio=1)
        c.tick(1)
        for _ in range(10):
            _collection(c, clock, 2, 200.0)       # cheap full collections only: nothing frozen by the callback, nothing pinned
        self.assertIsNone(c.tick(1))
        self.assertEqual(c.full_freezes, 0)

    def test_off_means_no_callback_no_threshold_change_and_no_freeze(self):
        for kw in ({"full_freeze_ms": None}, {"enabled": False}):
            fake, clock = FakeGc(), Clock()
            c = gf.GcFreeze(gc=fake, clock=clock, full_t2=1000, **dict({"enabled": True, "full_freeze_ms": 250.0}, **kw))
            self.assertFalse(c.install_full_freeze(), kw)
            self.assertEqual(fake.callbacks, [], kw)
            self.assertEqual(fake.threshold, (2000, 10, 10), "today's thresholds, untouched: %r" % (kw,))
            _collection(c, clock, 2, 10000.0)
            self.assertEqual(fake.calls, [], kw)

    def test_install_hooks_once_raises_the_third_threshold_and_remove_restores_it(self):
        c, fake, clock = self._c(full_t2=1000)
        self.assertTrue(c.install_full_freeze())
        self.assertTrue(c.install_full_freeze())
        self.assertEqual(fake.callbacks, [c.gc_callback], "one entry however often it is installed")
        self.assertEqual(fake.threshold, (2000, 10, 1000), "the first two thresholds kept, the third raised")
        c.remove_full_freeze()
        self.assertEqual((fake.callbacks, fake.threshold), ([], (2000, 10, 10)))

    def test_a_failure_inside_the_callback_is_counted_never_raised(self):
        c, fake, clock = self._c()
        def boom():
            raise RuntimeError("synthetic")
        fake.freeze = boom
        _collection(c, clock, 2, 999.0)           # must not raise into the collector
        self.assertEqual(c.callback_errors, 1)

    def test_perf_reports_the_full_freeze(self):
        c, fake, clock = self._c(full_t2=1000)
        _collection(c, clock, 2, 300.0)
        p = c.perf()
        self.assertEqual((p["fullFreezeMs"], p["fullFreezes"], p["fullT2"], p["fullSinceReclaim"]), (250.0, 1, 1000, 1))


class OwedUnderLoad(unittest.TestCase):
    """2026-10-07 review: the owed backstop ran only on an idle tick, and the live kernel once read no idle pusher cycle in
    5.5 hours, while the callback kept freezing. Now an owed backstop stops further freezing and runs on a busy cycle once
    it has waited full_force_s."""
    def _c(self, **kw):
        fake, clock = FakeGc(), Clock()
        kw.setdefault("full_freeze_ms", 250.0)
        return gf.GcFreeze(enabled=True, gc=fake, clock=clock, **kw), fake, clock

    def test_no_freeze_while_a_backstop_is_owed_and_each_skip_is_counted(self):
        c, fake, clock = self._c(full_backstop_ratio=2)
        _collection(c, clock, 2, 300.0)           # freeze 1 (reference 300 ms)
        _collection(c, clock, 2, 300.0)           # 600 ms = 2 x 300: owed, so this one is NOT frozen
        _collection(c, clock, 2, 300.0)
        self.assertEqual((fake.calls.count("freeze"), c.full_freezes, c.full_freeze_skips), (1, 1, 2))
        self.assertTrue(c.full_backstop_owed())

    def test_a_busy_cycle_runs_the_owed_backstop_only_after_the_bound(self):
        c, fake, clock = self._c(full_backstop_ratio=1, full_force_s=600)
        c.tick(1)
        _collection(c, clock, 2, 300.0)           # freeze and owed at once (ratio 1)
        stats = lambda: {"inserts": 1}
        errs = []
        self.assertIsNone(gf.pusher_tick(c, False, False, stats, errs.append), "owed, but within the bound: the idle tick may still come")
        clock.t += 599.0
        self.assertIsNone(gf.pusher_tick(c, False, False, stats, errs.append))
        clock.t += 2.0
        self.assertIsNone(gf.pusher_tick(c, False, True, stats, errs.append), "never on the boot's first cycle")
        fake.calls.clear()
        self.assertEqual(gf.pusher_tick(c, False, False, stats, errs.append), "forced", "past the bound: the busy cycle runs it")
        self.assertEqual(fake.calls, ["unfreeze", "collect", "freeze"])
        self.assertEqual((c.forced, c.reclaims, c.full_backstop_owed(), errs), (1, 1, False, []))
        self.assertIsNone(c.perf()["owedForS"])

    def test_force_off_waits_for_the_idle_tick_only(self):
        c, fake, clock = self._c(full_backstop_ratio=1, full_force_s=None)
        c.tick(1)
        _collection(c, clock, 2, 300.0)
        clock.t += 1e6
        self.assertIsNone(gf.pusher_tick(c, False, False, lambda: {"inserts": 1}, lambda e: None))
        self.assertEqual(gf.pusher_tick(c, True, False, lambda: {"inserts": 1}, lambda e: None), "backstop")

    def test_force_knob(self):
        f = gf.full_force_s_from_env
        self.assertEqual(f({}), (gf.DEFAULT_FULL_FORCE_S, None))
        for off in ("off", "0", "false"):
            self.assertEqual(f({"ROMP_GC_FREEZE_FULL_FORCE_S": off}), (None, None), off)
        self.assertEqual(f({"ROMP_GC_FREEZE_FULL_FORCE_S": "120"}), (120.0, None))
        for bad in ("soon", "-1", "nan"):
            self.assertEqual(f({"ROMP_GC_FREEZE_FULL_FORCE_S": bad}), (gf.DEFAULT_FULL_FORCE_S, bad), bad)


class StatsGc(FakeGc):
    """A collector double whose collect() may silently not run, as the real one returns 0 without collecting while another
    thread is inside a collection (the collecting flag stays set through the callbacks with the interpreter lock released)."""
    def __init__(self):
        super().__init__()
        self.runs = True
        self.n2 = 0
    def collect(self, *a):
        self.calls.append("collect")
        if self.runs:
            self.n2 += 1
        return 0
    def get_stats(self):
        return [{"collections": 0}, {"collections": 0}, {"collections": self.n2}]


class ReclaimThatDidNotRun(unittest.TestCase):
    """2026-10-07 review of 69dc51e33: a reclaim's gc.collect() can return without collecting; it must not be recorded as a
    reclaim (owed state cleared, the frozen cycles never walked). It stays owed and the next boundary retries."""
    def test_a_backstop_whose_collect_did_not_run_stays_owed_and_retries(self):
        fake, clock = StatsGc(), Clock()
        c = gf.GcFreeze(enabled=True, gc=fake, clock=clock, full_freeze_ms=250.0, full_backstop_ratio=1, full_force_s=600)
        c.tick(1)
        _collection(c, clock, 2, 300.0)           # a freeze, and owed at once (ratio 1)
        fake.runs = False
        fake.calls.clear()
        self.assertIsNone(c.tick(1), "the collect did not run: not a backstop")
        self.assertEqual(fake.calls, ["unfreeze", "collect", "freeze"], "re-frozen, so the controller's state is restored")
        self.assertEqual((c.reclaims, c.reclaim_skips, c.full_backstop_owed()), (0, 1, True))
        self.assertEqual(c.collections, 1, "only the initial freeze's collect ran: a collect that did not run is not counted, so "
                                           "organic = gen-2 collections less `collections` holds")
        clock.t += 700.0
        self.assertIsNone(gf.pusher_tick(c, False, False, lambda: {"inserts": 1}, lambda e: None), "a forced run that did not run")
        self.assertEqual((c.forced, c.reclaim_skips), (0, 2))
        fake.runs = True
        self.assertEqual(gf.pusher_tick(c, False, False, lambda: {"inserts": 1}, lambda e: None), "forced", "retried and ran")
        self.assertEqual((c.forced, c.reclaims, c.full_backstop_owed()), (1, 1, False))

    def test_a_release_whose_full_collect_did_not_run_keeps_its_owed_sessions(self):
        fake, clock = StatsGc(), Clock()
        c = gf.GcFreeze(enabled=True, gc=fake, clock=clock)
        c.tick(1)
        class Owner: sid = "aaaaaaaa-0000"
        o = Owner()
        c.note_ended(o)                           # no thread: judged a surviving cycle, a release is owed
        fake.runs = False
        self.assertIsNone(c.tick(1))
        self.assertEqual((c.reclaims, c.reclaim_skips, len(c._ended)), (0, 1, 1), "the owed session is registered again")
        fake.runs = True
        self.assertEqual(c.tick(1), "release")
        self.assertEqual(c.reclaims, 1)


class OwedClockStartsAtTheDebt(unittest.TestCase):
    """2026-10-07 review of 69dc51e33: the forced-run clock started at the next CHECK, not when the debt arose: the fold-in
    increment and the cheap-collection path created debt without stamping it, so a forced run could wait up to twice the
    bound. The clock now starts where the debt arises."""
    def test_a_foldin_that_creates_the_debt_starts_the_clock(self):
        fake, clock = FakeGc(), Clock()
        c = gf.GcFreeze(enabled=True, load_trees=1, backstop_foldins=1, gc=fake, clock=clock, full_freeze_ms=None,
                        full_force_s=600)
        c.tick(1)
        clock.t = 50.0
        self.assertEqual(c.tick(2), "load")       # this fold-in makes the backstop owed, at t=50
        clock.t = 650.0                           # the first check comes 600 s later
        self.assertTrue(c.force_due(), "600 s since the debt arose, not since this check")

    def test_a_cheap_collection_that_creates_the_debt_starts_the_clock(self):
        fake, clock = FakeGc(), Clock()
        c = gf.GcFreeze(enabled=True, gc=fake, clock=clock, full_freeze_ms=250.0, full_backstop_ratio=2, full_force_s=600)
        c.tick(1)
        _collection(c, clock, 2, 300.0)           # a freeze: reference 300 ms, 300 ms spent
        _collection(c, clock, 2, 200.0)           # cheap: 500 ms, not owed yet
        self.assertFalse(c.full_backstop_owed())
        _collection(c, clock, 2, 200.0)           # cheap: 700 ms >= 2 x 300: the debt arises here, on the early-return path
        t_debt = clock.t
        clock.t = t_debt + 600.0
        self.assertTrue(c.force_due(), "600 s since the cheap collection that created the debt")


class WalkingGc(FakeGc):
    """A collector double whose UNFROZEN collection costs clock time: after an unfreeze the next collect walks the whole
    heap, `walk_s` seconds, and the next cleanup's walk is `grow` times longer (the pinned heap grows between cleanups)."""
    def __init__(self, clock, walk_s, grow):
        super().__init__()
        self.clock, self.walk_s, self.grow = clock, walk_s, grow
        self.unfrozen = False
        self.walks = []                           # each cleanup's walk, in order
    def unfreeze(self):
        self.calls.append("unfreeze"); self.unfrozen = True
    def collect(self, *a):
        self.calls.append("collect")
        if self.unfrozen:
            self.clock.t += self.walk_s
            self.walks.append(self.walk_s)
            self.walk_s *= self.grow
            self.unfrozen = False
        return 0


class CleanupIntervalIsCapped(unittest.TestCase):
    """2026-10-07 live finding on 69dc51e33: a cleanup was owed at full_backstop_ratio x the largest walk, and each
    cleanup's own walk raised that reference, so cleanups got rarer as the pinned heap grew (live: about every 2 h and
    stretching, the tracked heap back on the no-fix curve by 9.7 h). Once anything is frozen, a cleanup is now owed at most
    full_cleanup_max_s after the last one, however long its walk."""
    def test_cleanups_keep_a_bounded_interval_while_each_walk_is_longer_than_the_last(self):
        clock = Clock()
        fake = WalkingGc(clock, walk_s=30.0, grow=2.0)
        c = gf.GcFreeze(enabled=True, gc=fake, clock=clock, full_freeze_ms=250.0, full_backstop_ratio=10, full_force_s=600)
        c.full_cleanup_max_s = 3600.0             # set as an attribute, so the same test reads the commit before the cap
        stats = lambda: {"inserts": 1}
        gf.pusher_tick(c, True, False, stats, lambda e: None)   # the start-up freeze (nothing walked)
        reclaim_times = [clock.t]
        last = c.reclaims
        for _ in range(int(14 * 3600 / 60)):      # 14 hours of one-minute pusher cycles, every one idle
            _collection(c, clock, 2, 700.0)       # a slow organic full collection each minute: freezes its survivors
            clock.t += 60.0
            gf.pusher_tick(c, True, False, stats, lambda e: None)
            if c.reclaims != last:
                last = c.reclaims
                reclaim_times.append(clock.t)
        gaps = [b - a for a, b in zip(reclaim_times, reclaim_times[1:])]
        self.assertGreaterEqual(len(gaps), 10, "cleanups keep coming as walks grow: %d in 14 h" % len(gaps))
        # each gap against the cap, one cycle and the walk of the cleanup that ENDED it (not the final, doubled walk)
        over = [(round(g), round(w)) for g, w in zip(gaps, fake.walks) if g > 3600.0 + 61.0 + w]
        self.assertEqual(over, [], "gaps beyond the cap plus their own cleanup's walk: %r" % over)

    def test_with_the_full_freeze_off_the_cap_never_owes_a_cleanup(self):
        """2026-10-07 review of 25c43358b: the cap applied with ROMP_GC_FREEZE_FULL_MS=off too (the start-up freeze set the
        pinned clock), so the documented rollback still ran an hourly whole-heap cleanup, forced onto busy cycles. The cap
        belongs to the full-collection freeze and is off with it."""
        fake, clock = FakeGc(), Clock()
        c = gf.GcFreeze(enabled=True, gc=fake, clock=clock, full_freeze_ms=None, full_force_s=600, full_cleanup_max_s=3000)
        stats = lambda: {"inserts": 1}
        self.assertEqual(gf.pusher_tick(c, True, False, stats, lambda e: None), "initial")
        for _ in range(10):
            clock.t += 3600.0
            self.assertIsNone(gf.pusher_tick(c, False, False, stats, lambda e: None), "no forced cleanup on a busy cycle")
            self.assertIsNone(gf.pusher_tick(c, True, False, stats, lambda e: None), "and none on an idle one")
        self.assertEqual((c.reclaims, c.forced), (0, 0))

    def test_the_cap_knob(self):
        f = gf.full_cleanup_max_s_from_env
        self.assertEqual(f({}), (gf.DEFAULT_FULL_CLEANUP_MAX_S, None))
        for off in ("off", "0", "false"):
            self.assertEqual(f({"ROMP_GC_FREEZE_CLEANUP_MAX_S": off}), (None, None), off)
        self.assertEqual(f({"ROMP_GC_FREEZE_CLEANUP_MAX_S": "1800"}), (1800.0, None))
        for bad in ("hourly", "-5", "nan"):
            self.assertEqual(f({"ROMP_GC_FREEZE_CLEANUP_MAX_S": bad}), (gf.DEFAULT_FULL_CLEANUP_MAX_S, bad), bad)


class FoldinBackstopUnderLoad(unittest.TestCase):
    """2026-10-07 review: the older fold-in backstop (after `backstop_foldins` load fold-ins) also ran only on an idle tick,
    and the live kernel once read 4 idle pusher cycles in 91. It now shares the full backstop's owed clock and forced run."""
    def test_an_owed_foldin_backstop_runs_on_a_busy_cycle_after_the_bound(self):
        fake, clock = FakeGc(), Clock()
        c = gf.GcFreeze(enabled=True, load_trees=1, backstop_foldins=2, gc=fake, clock=clock, full_freeze_ms=None,
                        full_force_s=600)
        stats = {"inserts": 0}
        def st():
            return dict(stats)
        for n in (1, 2, 3):                       # idle ticks: the initial freeze, then two load fold-ins
            stats["inserts"] = n
            gf.pusher_tick(c, True, False, st, lambda e: None)
        self.assertTrue(c._backstop_owed(), "two fold-ins: the fold-in backstop is owed")
        for _ in range(5):                        # every later cycle BUSY: within the bound nothing runs
            clock.t += 100.0
            self.assertIsNone(gf.pusher_tick(c, False, False, st, lambda e: None))
        clock.t += 101.0
        fake.calls.clear()
        self.assertEqual(gf.pusher_tick(c, False, False, st, lambda e: None), "forced")
        self.assertEqual(fake.calls, ["unfreeze", "collect", "freeze"])
        self.assertEqual((c.forced, c.reclaims, c._foldins, c._backstop_owed()), (1, 1, 0, False))

    def test_the_two_backstops_share_one_owed_clock(self):
        fake, clock = FakeGc(), Clock()
        c = gf.GcFreeze(enabled=True, load_trees=1, backstop_foldins=1, gc=fake, clock=clock, full_freeze_ms=250.0,
                        full_backstop_ratio=1, full_force_s=600)
        c.tick(1); c.tick(2)                      # initial, then one fold-in: the fold-in backstop is owed at t=0
        self.assertTrue(c._backstop_owed())
        clock.t += 400.0
        _collection(c, clock, 2, 300.0)           # a slow full collection while owed: no freeze (skipped), and no new clock
        self.assertEqual((c.full_freezes, c.full_freeze_skips), (0, 1))
        clock.t += 200.0
        self.assertTrue(c.force_due(), "600 s since the FIRST owed reading, whichever backstop it was")

    def test_a_cycle_pinned_by_a_load_foldin_is_reclaimed_on_busy_cycles(self):
        gc.unfreeze(); gc.collect()
        self.addCleanup(lambda: (gc.unfreeze(), gc.collect()))
        clock = Clock()
        c = gf.GcFreeze(enabled=True, load_trees=1, backstop_foldins=1, gc=gc, clock=clock, full_freeze_ms=None,
                        full_force_s=10)
        c.tick(1)
        a = Cyclic(); b = Cyclic()
        c.tick(2)                                 # a load fold-in freezes a and b alive; the fold-in backstop is now owed
        a.o = b; b.o = a
        w = weakref.ref(a)
        del a, b
        gc.collect()
        self.assertIsNotNone(w(), "pinned in the frozen set")
        st = lambda: {"inserts": 2}
        self.assertIsNone(gf.pusher_tick(c, False, False, st, lambda e: None), "busy, inside the bound")
        clock.t += 11.0
        self.assertEqual(gf.pusher_tick(c, False, False, st, lambda e: None), "forced")
        self.assertIsNone(w(), "the forced backstop reclaimed it on a busy cycle")


class StepClock:
    """Every read advances `step` seconds: a full collection's start and stop reads are `step` apart (a pause of step*1000 ms)."""
    def __init__(self, step):
        self.t, self.step = 0.0, step
    def __call__(self):
        self.t += self.step
        return self.t


class BusyRealCollector(unittest.TestCase):
    """The regression on the REAL collector: many rounds of objects alive at a freeze that then become unreachable cycles,
    with EVERY pusher tick busy. Before the fix the callback froze each round's cycles and nothing ever reclaimed them, so
    the pinned set grew with the rounds; now freezing stops once a backstop is owed, the forced backstop runs within its
    bound on a busy tick, and what is pinned stays bounded."""
    ROUNDS, PAIRS = 40, 300

    def setUp(self):
        self._thr = gc.get_threshold()
        gc.unfreeze(); gc.collect()

    def tearDown(self):
        gc.unfreeze()
        gc.set_threshold(*self._thr)
        gc.collect()

    def test_pinning_stops_when_owed_the_forced_backstop_reclaims_and_retention_stays_bounded(self):
        clock = StepClock(0.3)                    # every full collection reads 300 ms; the force bound is in the same clock
        c = gf.GcFreeze(enabled=True, gc=gc, clock=clock, full_freeze_ms=250.0, full_backstop_ratio=3, full_force_s=6.0,
                        full_cleanup_max_s=None)  # the forced path alone (this clock advances per read; the cap has its own test)
        self.assertTrue(c.install_full_freeze())
        self.addCleanup(c.remove_full_freeze)
        c.tick(1)                                 # the start-up freeze
        frozen0 = gc.get_freeze_count()
        stats = lambda: {"inserts": 1}
        errs = []
        refs, alive_peak, frozen_peak, owed_freezes = [], 0, 0, []
        for _ in range(self.ROUNDS):
            live = []
            for _ in range(self.PAIRS):
                a = Cyclic(); b = Cyclic(); a.o = b; b.o = a
                live.append(a); refs.append(weakref.ref(a))
            owed_before = c.full_backstop_owed()
            n_before = c.full_freezes
            gc.collect()                          # organic and slow (300 ms): frozen at its stop unless a backstop is owed
            if owed_before:
                owed_freezes.append(c.full_freezes - n_before)
            del live, a, b                        # alive at the freeze, unreachable cycles now: pinned if frozen
            gf.pusher_tick(c, False, False, stats, errs.append)   # every cycle BUSY: no idle tick ever comes
            alive_peak = max(alive_peak, sum(1 for r in refs if r() is not None))
            frozen_peak = max(frozen_peak, gc.get_freeze_count() - frozen0 - len(refs))   # less the test's own live weakrefs
        self.assertEqual(errs, [])
        self.assertGreater(c.full_freeze_skips, 0, "slow collections came while owed and froze nothing")
        self.assertEqual(set(owed_freezes), {0}, "no freeze ever ran while a backstop was owed")
        self.assertGreaterEqual(c.forced, 2, "the owed backstop ran on busy ticks, repeatedly")
        bound = 6 * self.PAIRS                    # a few rounds' cycles at most (owed after 3 x 300 ms, forced within ~6 s of clock)
        self.assertLessEqual(alive_peak, bound, "pinned cycles stay bounded across %d rounds (peak %d)" % (self.ROUNDS, alive_peak))
        self.assertLess(frozen_peak, 6 * 2 * self.PAIRS + 2000, "the frozen set stays bounded too (peak %d)" % frozen_peak)
        c.remove_full_freeze()
        gc.unfreeze(); gc.collect()
        self.assertEqual(sum(1 for r in refs if r() is not None), 0, "and every cycle is reclaimable")


class Knobs(unittest.TestCase):
    def test_full_freeze_ms(self):
        f = gf.full_freeze_ms_from_env
        self.assertEqual(f({}), (gf.DEFAULT_FULL_FREEZE_MS, None))
        self.assertEqual(f({"ROMP_GC_FREEZE_FULL_MS": " "}), (gf.DEFAULT_FULL_FREEZE_MS, None))
        for off in ("off", "OFF", "0", "false"):
            self.assertEqual(f({"ROMP_GC_FREEZE_FULL_MS": off}), (None, None), off)
        self.assertEqual(f({"ROMP_GC_FREEZE_FULL_MS": "750"}), (750.0, None))
        for bad in ("lots", "-5", "nan", "inf"):
            self.assertEqual(f({"ROMP_GC_FREEZE_FULL_MS": bad}), (gf.DEFAULT_FULL_FREEZE_MS, bad), bad)

    def test_full_t2(self):
        f = gf.full_t2_from_env
        self.assertEqual(f({}), (gf.DEFAULT_FULL_T2, None))
        for off in ("off", "0", "false"):
            self.assertEqual(f({"ROMP_GC_FREEZE_FULL_T2": off}), (None, None), off)
        self.assertEqual(f({"ROMP_GC_FREEZE_FULL_T2": "250"}), (250, None))
        for bad in ("x", "-1", "2.5"):
            self.assertEqual(f({"ROMP_GC_FREEZE_FULL_T2": bad}), (gf.DEFAULT_FULL_T2, bad), bad)

    def test_the_defaults_are_on(self):
        self.assertGreater(gf.DEFAULT_FULL_FREEZE_MS, 0)
        self.assertGreater(gf.DEFAULT_FULL_T2, 10, "above CPython's default third threshold, or it changes nothing")


class Cyclic:
    # Slots keep a pair at two gc-tracked objects on every Python. Without them Python 3.10 tracks each instance's
    # __dict__ as well (3.11 and later hold the attributes inline), so a pair is four objects there and the frozen-set
    # bound, written as 2 objects per pair, failed on 3.10 alone (peak 6005 against 5600) with nothing retained.
    __slots__ = ("o", "__weakref__")


class RealCollector(unittest.TestCase):
    def setUp(self):
        self._thr = gc.get_threshold()

    def tearDown(self):
        gc.unfreeze()
        gc.set_threshold(*self._thr)
        gc.collect()

    def _controller(self, **kw):
        c = gf.GcFreeze(enabled=True, gc=gc, full_freeze_ms=1e-6, **kw)   # any full collection counts as expensive here
        self.assertTrue(c.install_full_freeze())
        self.addCleanup(c.remove_full_freeze)
        return c

    @staticmethod
    def _cycle():
        a = Cyclic(); b = Cyclic(); a.o = b; b.o = a
        return weakref.ref(a)

    def test_the_stop_freeze_pins_no_garbage_and_takes_the_survivors_out_of_the_walk(self):
        gc.unfreeze(); gc.collect()
        frozen0 = gc.get_freeze_count()
        c = self._controller()
        heap = [{"k": [i]} for i in range(20000)]             # long-lived, tracked
        dead = self._cycle()                                  # garbage when the collection runs
        gc.collect()                                          # an organic full collection: the callback freezes at its stop
        self.assertEqual(c.full_freezes, 1)
        self.assertIsNone(dead(), "the cycle that was garbage at the collection is freed, never pinned")
        self.assertGreaterEqual(gc.get_freeze_count() - frozen0, 40000, "the heap's dicts and lists are frozen")
        self.assertFalse(any(o is heap for o in gc.get_objects(2)), "and gone from the oldest generation's walk")

    def test_no_freeze_lists_the_heap_and_the_count_is_read_at_each_cleanup(self):
        """2026-10-07 review of 69dc51e33: counting what a freeze adds by gc.get_objects listed every survivor inside each
        freeze, the whole-heap walk the stored count was meant to avoid. No freeze lists anything now; the count is re-read
        once at each cleanup (inside the pause it pays) and served as of that cleanup, with its time."""
        class Listing:
            def __init__(self):
                self.listings = 0
            def __getattr__(self, name):
                return getattr(gc, name)
            def get_objects(self, *a, **k):
                self.listings += 1
                return gc.get_objects(*a, **k)
        proxy = Listing()
        gc.unfreeze(); gc.collect()
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=proxy, full_freeze_ms=1e-6, full_backstop_ratio=1000)
        self.assertTrue(c.install_full_freeze())
        self.addCleanup(c.remove_full_freeze)
        heap = [{"k": [i]} for i in range(20000)]
        c.tick(1)                                             # the start-up freeze
        gc.collect()                                          # a callback freeze
        c.tick(2)                                             # a load fold-in
        self.assertEqual(proxy.listings, 0, "no freeze lists the heap")
        self.assertIsNone(c.frozen_as_of_cleanup, "no cleanup yet: no count served")
        self.assertIsNone(c.frozen_as_of_cleanup_at)
        c._foldins = c.backstop_foldins                       # owe the fold-in backstop
        t0 = time.time()
        self.assertEqual(c.tick(3), "backstop")
        self.assertEqual(proxy.listings, 0)
        self.assertLessEqual(abs(c.frozen_as_of_cleanup - gc.get_freeze_count()), 50, "re-read at the cleanup")
        self.assertGreaterEqual(c.frozen_as_of_cleanup, len(heap))
        self.assertGreaterEqual(c.frozen_as_of_cleanup_at, t0 - 1)
        p = c.perf()
        self.assertEqual((p["frozenAsOfCleanup"], p["frozenAsOfCleanupAt"]), (c.frozen_as_of_cleanup, c.frozen_as_of_cleanup_at))

    def test_a_cycle_formed_later_among_frozen_objects_waits_for_the_backstop(self):
        gc.unfreeze(); gc.collect()
        c = self._controller(full_backstop_ratio=1)
        c.tick(1)                                             # the controller's initial freeze
        a = Cyclic(); b = Cyclic()
        gc.collect()                                          # organic: a and b frozen at its stop
        a.o = b; b.o = a
        w = weakref.ref(a)
        del a, b                                              # now cyclic garbage, but frozen
        c.remove_full_freeze()                                # so the check's own collection freezes nothing new
        gc.collect()
        self.assertIsNotNone(w(), "a full collection never walks the frozen set: the cycle is pinned")
        self.assertEqual(c.tick(1), "backstop", "the full freeze owed a backstop")
        self.assertIsNone(w(), "the backstop's unfreeze-and-collect frees it")

    def test_off_leaves_the_real_collector_alone(self):
        before = gc.get_threshold()
        c = gf.GcFreeze(enabled=True, gc=gc, full_freeze_ms=None, full_t2=1000)
        self.assertFalse(c.install_full_freeze())
        self.assertNotIn(c.gc_callback, gc.callbacks)
        self.assertEqual(gc.get_threshold(), before)


class KernelWiring(unittest.TestCase):
    NAMES = ("ROMP_GC_FREEZE", "ROMP_GC_FREEZE_FULL_MS", "ROMP_GC_FREEZE_FULL_T2")

    def _load(self, tag, **env):
        saved = {k: os.environ.get(k) for k in self.NAMES}
        try:
            for k in self.NAMES:
                os.environ.pop(k, None)
            os.environ.update(env)
            return load_source("romp_kernel_gcf_full_" + tag, os.path.join(BIN, "romp-kernel"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_kernel_reads_the_knobs_and_off_restores_todays_collector(self):
        km = self._load("on", ROMP_GC_FREEZE="on", ROMP_GC_FREEZE_FULL_MS="300", ROMP_GC_FREEZE_FULL_T2="77")
        self.assertEqual((km._GC_FREEZE.full_freeze_ms, km._GC_FREEZE.full_t2), (300.0, 77))
        km = self._load("default", ROMP_GC_FREEZE="on")
        self.assertEqual((km._GC_FREEZE.full_freeze_ms, km._GC_FREEZE.full_t2), (gf.DEFAULT_FULL_FREEZE_MS, gf.DEFAULT_FULL_T2))
        km = self._load("off", ROMP_GC_FREEZE="on", ROMP_GC_FREEZE_FULL_MS="off")
        self.assertIsNone(km._GC_FREEZE.full_freeze_ms)
        before = gc.get_threshold()
        self.assertFalse(km._GC_FREEZE.install_full_freeze(), "off: nothing installed")
        self.assertEqual(gc.get_threshold(), before)
        self.assertNotIn(km._GC_FREEZE.gc_callback, gc.callbacks)

    def test_main_installs_it_after_the_perf_hook(self):
        src = Path(ROOT, "kernel", "kernel.py").read_text()
        body = src[src.index("\ndef main():"):]
        nxt = body.find("\ndef ", 1)               # main may be the file's last function
        body = body if nxt < 0 else body[:nxt]
        i_perf = body.index("_PERF_STATS.install_gc_hook()")
        i_full = body.index("_GC_FREEZE.install_full_freeze()")
        self.assertLess(i_perf, i_full, "the perf hook first, so its timing of a collection never includes the freeze")
        self.assertTrue(re.search(r"\n    _GC_FREEZE\.install_full_freeze\(\)", body), "called at main's top level")


if __name__ == "__main__":
    unittest.main()
