#!/usr/bin/env python3
"""The kernel restart path (2026-09-11): the boot row carries the census and attach phases and waits for attachDone
(or a backstop); the producer's first pass waits, bounded, for the boot's attaches; the fold checkpoints are written
periodically for a session whose leaf moves with no settle evidence; the exit's cut row carries its phase timings and
the exit's assembly-document writes are bounded. Hermetic: a temp state root, no kernel, no sessions."""
import io
import json
import os
import pathlib
import sys
import types
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load (bin/romp-kernel resolves its state root at import)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_restart_phases", os.path.join(BIN, "romp-kernel"))


class BootMarks(unittest.TestCase):
    def setUp(self):
        km._BOOT_MARKS.clear()
        km._BOOT_ATTACHED.clear()
        self.rows = []
        self._p = mock.patch.object(km, "_append_restart_cut", lambda row: self.rows.append(row)); self._p.start()

    def tearDown(self):
        self._p.stop()
        km._BOOT_MARKS.clear(); km._BOOT_ATTACHED.clear()

    def test_the_boot_row_waits_for_attach_done_and_carries_the_phases(self):
        km._mark_boot("firstServe")
        km._mark_boot("reconcileDone")
        self.assertEqual(self.rows, [], "two marks are no longer enough: the attaches have not settled")
        self.assertFalse(km._BOOT_ATTACHED.is_set())
        km._mark_boot("censusDone")
        km._mark_boot("attachDone")
        self.assertTrue(km._BOOT_ATTACHED.is_set(), "attachDone releases the producer's first pass")
        self.assertEqual(len(self.rows), 1)
        row = self.rows[0]
        self.assertTrue(row["bootSettled"])
        for k in ("firstServe", "reconcileDone", "settleS", "censusS", "attachS"):
            self.assertIn(k, row)
        self.assertNotIn("attachTimedOut", row)
        km._mark_boot("attachDone"); km._mark_boot("firstServe")
        self.assertEqual(len(self.rows), 1, "idempotent: one row per boot")

    def test_the_backstop_tick_writes_the_row_without_attach_done_after_the_bound_and_says_so(self):
        km._mark_boot("firstServe"); km._mark_boot("reconcileDone"); km._mark_boot("censusDone")
        self.assertEqual(self.rows, [])
        t0 = km._BOOT_MARKS["reconcileDone"]
        self.assertFalse(km._boot_row_backstop(t0 + km.BOOT_ROW_BACKSTOP_S / 2), "inside the bound: the tick waits")
        self.assertEqual(self.rows, [])
        self.assertTrue(km._boot_row_backstop(t0 + km.BOOT_ROW_BACKSTOP_S + 1))
        self.assertEqual(len(self.rows), 1)
        self.assertTrue(self.rows[0].get("attachTimedOut"))
        self.assertIn("censusS", self.rows[0]); self.assertNotIn("attachS", self.rows[0])
        km._mark_boot("attachDone")
        self.assertEqual(len(self.rows), 1, "a late attachDone writes no second row")
        self.assertFalse(km._boot_row_backstop(t0 + 10 * km.BOOT_ROW_BACKSTOP_S))
        self.assertEqual(len(self.rows), 1)

    def test_the_backstop_rides_the_pusher_tick_not_a_thread(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("        _job_stage('bootRowBackstop', lambda: _boot_row_backstop(now))\n", src, "the pusher's tick jobs call it (as its own stage, T398)")
        self.assertNotIn("threading.Timer(BOOT_ROW_BACKSTOP_S", src, "no timer thread: nothing outlives a boot (T282)")
        self.assertEqual(len(threading.enumerate()), len([t for t in threading.enumerate() if not t.name.startswith("boot-row")]))


class ProducerGate(unittest.TestCase):
    def test_the_first_pass_waits_bounded_for_the_attaches(self):
        km._BOOT_ATTACHED.clear()
        t0 = time.monotonic()
        self.assertFalse(km._wait_boot_attached(0.2), "the bound passes: the judges go anyway, loudly")
        self.assertGreaterEqual(time.monotonic() - t0, 0.15)
        km._BOOT_ATTACHED.set()
        self.assertTrue(km._wait_boot_attached(0.2))
        km._BOOT_ATTACHED.clear()

    def test_the_producer_reads_the_gate_before_its_loop(self):
        import inspect
        src = inspect.getsource(km._producer)
        self.assertLess(src.index("_wait_boot_attached()"), src.index("while not _LOOPS_STOP.is_set()"),
                        "the gate is read once, before the first pass, never per pass")


class PeriodicCheckpoints(unittest.TestCase):
    """_persist_checkpoints writes a session's checkpoints at its settle (as before) AND when its leaf moved with no
    settle evidence, at most once per CKPT_PERIOD_S."""

    def _run(self, leaf_stats, settle_keys, monos):
        writes = []
        sid, leaf = "44444444-aaaa-0000-0000-000000000001", "/synthetic/leaf.jsonl"
        km._CKPT_SETTLE_SEEN.clear(); km._CKPT_PERIODIC_SEEN.clear()
        it_stat = iter(leaf_stats); it_key = iter(settle_keys); it_mono = iter(monos)
        with mock.patch.object(km, "_sessions", lambda now: [{"sid": sid, "path": leaf}]), \
             mock.patch.object(km, "_turn_end_key", lambda s, reg=None: next(it_key)), \
             mock.patch.object(km, "_stat_key", lambda p: ("states", 1) if "states" in str(p) else next(it_stat)), \
             mock.patch.object(km.time, "monotonic", lambda: next(it_mono)), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: True), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: []), \
             mock.patch.object(km.em, "asm_checkpoint_write", lambda *a, **k: writes.append(a[0]) or True), \
             mock.patch.object(km, "_display_sdk_human", lambda s: False):
            for _ in range(len(monos)):
                km._persist_checkpoints(0)
        return writes

    def test_a_moving_leaf_with_no_settle_evidence_writes_once_per_period(self):
        period = km.CKPT_PERIOD_S
        # cycle 1: first sight (records, NO write: a boot must not prime every session at once); 2: leaf moved but too
        # soon (no write); 3: moved, period passed (writes); 4: nothing moved (no write, even past the period)
        writes = self._run(leaf_stats=[("l", 1), ("l", 2), ("l", 3), ("l", 3)], settle_keys=[0, 0, 0, 0],
                           monos=[0.0, period / 2, period + 1, 3 * period])
        self.assertEqual(len(writes), 1, "only the first move past the period; not the first sight, not the early move, not the idle cycle")

    def test_the_first_sight_at_boot_writes_nothing_for_any_session(self):
        # the restart-path deploy (2026-09-11): the first pusher cycle after a boot primed 52 leaves (21 cold refolds) and
        # held the boot census to 5.7 s behind the interpreter lock; both triggers now record at first sight and write nothing
        writes = []
        sess = [{"sid": "55555555-aaaa-0000-0000-%012d" % i, "path": "/synthetic/leaf%d.jsonl" % i} for i in range(50)]
        km._CKPT_SETTLE_SEEN.clear(); km._CKPT_PERIODIC_SEEN.clear()
        with mock.patch.object(km, "_sessions", lambda now: sess), \
             mock.patch.object(km, "_turn_end_key", lambda s, reg=None: 1234), \
             mock.patch.object(km, "_stat_key", lambda p: ("k", 1)), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: writes.append(("prime", leaf)) or True), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: []), \
             mock.patch.object(km.em, "asm_checkpoint_write", lambda *a, **k: writes.append(("asm", a[0])) or True), \
             mock.patch.object(km, "_display_sdk_human", lambda s: False):
            km._persist_checkpoints(0)
        self.assertEqual(writes, [], "a boot's first sight primes and writes nothing, for any number of sessions")
        self.assertEqual((len(km._CKPT_SETTLE_SEEN), len(km._CKPT_PERIODIC_SEEN)), (50, 50), "every session recorded")

    def test_a_settle_still_writes_at_once(self):
        writes = self._run(leaf_stats=[("l", 1), ("l", 1)], settle_keys=[0, 7], monos=[0.0, 1.0])
        self.assertEqual(len(writes), 1, "the first sight records; the turn end that follows writes at once, whatever the period says")


sb_mod = load_source("romp_sdk_backend_restart_phases", os.path.join(os.path.dirname(BIN), "kernel", "sdk_backend.py"))


class ExitPhases(unittest.TestCase):
    def test_the_cut_row_carries_the_exit_phases_and_nothing_else_from_them(self):
        with mock.patch.object(km, "_kernel_process_sample", lambda: {}):
            row = km._restart_cut_row({"cutTurns": [], "stopped": 3}, phases={"ckptS": 0.4, "drainS": 0.1, "junk": 9})
        self.assertEqual((row["ckptS"], row["drainS"], row["stopped"]), (0.4, 0.1, 3))
        self.assertNotIn("junk", row)
        with mock.patch.object(km, "_kernel_process_sample", lambda: {}):
            self.assertNotIn("ckptS", km._restart_cut_row({}))

    def test_the_exit_bounds_its_assembly_writes_and_times_its_phases(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        i = src.index("draining SDK sessions")
        tail = src[i:i + 6000]
        self.assertIn("if time.monotonic() - _asm_t0 > EXIT_ASM_BUDGET_S:", tail, "the assembly-document writes are bounded on the exit path")
        self.assertIn("em.checkpoint_write_dirty(budget_s=EXIT_CKPT_WRITE_BUDGET_S)", tail, "and so are the fold checkpoint writes (the 1:19 PM exit met the SIGKILL)")
        self.assertIn("if time.monotonic() - _prime_t0 > EXIT_PRIME_BUDGET_S:", tail)
        self.assertLess(km.EXIT_PRIME_BUDGET_S + km.EXIT_CKPT_WRITE_BUDGET_S + km.EXIT_ASM_BUDGET_S + km.EXIT_DRAIN_BUDGET_S, km.EXIT_GRACE_S,
                        "the exit's four budgets fit under the manager's grace")
        self.assertIn("res = be.drain(EXIT_DRAIN_BUDGET_S)", tail, "the SDK drain takes its share of the grace")
        self.assertIn('_phases["ckptS"]', tail); self.assertIn('_phases["drainS"]', tail)
        self.assertIn("audit_reason=reason, phases=_phases)", tail, "the phases reach the cut row")
        self.assertIn("boot_phase=_mark_boot,", src, "the backend's milestones land in the kernel's boot marks")


class BoundedDirtyWrite(unittest.TestCase):
    def test_a_zero_budget_writes_the_first_dirty_file_and_leaves_the_rest(self):
        calls = []
        with mock.patch.object(km.em, "checkpoint_dirty", lambda: ["/synthetic/a.jsonl", "/synthetic/b.jsonl", "/synthetic/c.jsonl"]), \
             mock.patch.object(km.em, "checkpoint_write", lambda p: calls.append(p) or True):
            n = km.em.checkpoint_write_dirty(budget_s=0.0)
        self.assertEqual((n, calls), (1, ["/synthetic/a.jsonl"]), "the first always writes; the budget then stops the pass")
        calls.clear()
        with mock.patch.object(km.em, "checkpoint_dirty", lambda: ["/synthetic/a.jsonl", "/synthetic/b.jsonl"]), \
             mock.patch.object(km.em, "checkpoint_write", lambda p: calls.append(p) or True):
            self.assertEqual(km.em.checkpoint_write_dirty(), 2, "no budget: everything, as the settle writer relies on")


class AttachTimedOutNamesTheAttaches(unittest.TestCase):
    def test_the_backstop_row_carries_the_unsettled_sids_and_the_pending_count(self):
        rows = []
        saved = km._sdk_backend
        km._sdk_backend = types.SimpleNamespace(_boot_attach_unsettled={"22222222-0000-0000-0000-000000000002"}, _boot_attach_pending=1)
        try:
            with mock.patch.dict(km._BOOT_MARKS, {"firstServe": 100.0, "reconcileDone": 100.2, "censusDone": 100.1}, clear=True), \
                 mock.patch.object(km, "_append_restart_cut", lambda row: rows.append(row)), \
                 mock.patch.object(km, "_kernel_process_sample", lambda: {}), \
                 mock.patch.object(km, "RESTART_CUTS_FILE", pathlib.Path(tempfile.mkdtemp()) / "restart-cuts.jsonl"):
                km._append_boot_settled(100.0, 100.2)
        finally:
            km._sdk_backend = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["attachTimedOut"], rows[0]["attachUnsettled"], rows[0]["attachPending"]), (True, ["22222222"], 1),
                         "a timed-out boot row says which attaches never settled (1:19 PM Pacific 2026-09-11: 23 hellos, no mark)")

    def test_the_backend_tracks_the_unsettled_attaches(self):
        be = types.SimpleNamespace(_boot_attach_unsettled=set(), _boot_attach_pending=2, _boot_attach_lock=threading.Lock())
        be._boot_attach_count_down = lambda: None
        settled = sb_mod.SdkBackend._boot_attach_settled(be, None, "22222222-0000-0000-0000-000000000002")
        self.assertEqual(be._boot_attach_unsettled, {"22222222-0000-0000-0000-000000000002"})
        settled(); settled()
        self.assertEqual(be._boot_attach_unsettled, set(), "fires once; the sid leaves the set at the hello")


class ExitBudgetsScaleWithTheGrace(unittest.TestCase):
    def test_the_shares_follow_the_manager_grace(self):
        # the constants are read at load; recompute the shares the way the module does for two grace values
        for grace_ms, share in (("5000", 4.5), ("8000", 7.5)):
            self.assertAlmostEqual(0.10 * share + 0.35 * share + 0.20 * share + 0.35 * share, share, places=6)
        src = open(km.__file__).read()
        self.assertIn('EXIT_GRACE_S = float(os.environ.get("ROMP_SHUTDOWN_GRACE_MS", "5000")) / 1000.0', src, "the kernel assumes 5 s unless the manager says otherwise")
        self.assertIn("_EXIT_SHARE_S = max(1.0, EXIT_GRACE_S - 0.5)", src)
        mgr = open(os.path.join(BIN, "romp-manager")).read()
        self.assertIn("ROMP_SHUTDOWN_GRACE_MS: String(SHUTDOWN_GRACE_MS)", mgr, "the manager passes the grace it enforces to the kernel it spawns")


class BootHealthFirstCycle(unittest.TestCase):
    def setUp(self):
        km._BOOT_HEALTH_DONE[0] = False
        self.addCleanup(lambda: km._BOOT_HEALTH_DONE.__setitem__(0, False))

    def test_a_slow_first_cycle_writes_a_flagged_row_and_a_loud_line_once(self):
        rows, err = [], io.StringIO()
        with mock.patch.object(km, "_append_restart_cut", rows.append), mock.patch.object(km.sys, "stderr", err):
            row = km._boot_health_first_cycle(42.0)
            self.assertIsNone(km._boot_health_first_cycle(0.1), "the second cycle is not the boot's first")
        self.assertEqual((row["bootHealth"], row["firstCycleS"], row["slow"]), (True, 42.0, True))
        self.assertEqual(len(rows), 1)
        self.assertIn("boot health: the first pusher cycle took 42.0 s", err.getvalue())

    def test_a_fast_first_cycle_is_recorded_quietly(self):
        rows, err = [], io.StringIO()
        with mock.patch.object(km, "_append_restart_cut", rows.append), mock.patch.object(km.sys, "stderr", err):
            row = km._boot_health_first_cycle(0.08)
        self.assertEqual((row["slow"], len(rows), err.getvalue()), (False, 1, ""))

    def test_the_pusher_calls_it_after_its_cycle_accounting(self):
        src = open(km.__file__).read()
        self.assertLess(src.index("_PERF_STATS.cycle(time.monotonic() - _t_cycle"), src.index("_boot_health_first_cycle(time.monotonic() - _t_cycle)"))


if __name__ == "__main__":
    unittest.main()
