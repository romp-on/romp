#!/usr/bin/env python3
"""The housekeeping jobs run on their own thread (2026-09-13). The pusher's cycle used to run every tick job after its push, on
one thread, so at boot the reminder walk and the interrupt tick (a cold read of every session's transcript: 24 s and 6 s on the
11:55 AM boot that decided this) held every browser frame behind a 30 to 60 s first cycle, and all day a slow job delayed the
next refresh. The user asked why the reminder walk had to finish before the UI showed at all. Now `_pusher_cycle_jobs` keeps what
feeds a frame or shares the cycle's checkpoint byte budget, `_jobs_pass` runs the rest in the order it always had on the jobs
thread (`_jobs_loop`, one pass per JOBS_PASS_S) with its own liveness snapshot, /perf keeps the two loops' splits apart (`pusher`
and `jobs`), and the boot-health row carries both firsts (`firstCycleS`, the browser's wait; `jobsFirstPassS`, the housekeeping's)."""
import inspect
import io
import os
import re
import sys
import threading
import time
import unittest
from unittest import mock
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import kernel_module   # noqa: E402

JOB_NAME_RE = r"_job_stage\(['\"](\w+)['\"]"

PUSHER_JOBS = ("beginCheckpointCycle", "sessionsListing", "applyPendingOps", "turnNotify", "persistCheckpoints", "convergeCheckpoints",
               "bootRowBackstop", "kernelSample", "apiHealth")
HOUSEKEEPING = ("liftSpentAwaiting", "deathSweep", "endOnIdle", "deferralSweep", "autoNudge", "interruptBlock",
                "persistTickSeen", "persistIntrMarks", "persistSpendTrees", "autoPauseOnLimit", "usagePoll", "autoPauseOnSpend",
                "spendGuard", "autoResumeRetry", "autoResumeSession", "autoRetry", "idleQueueDrive", "clearDoneNotes")
QUIET = ("_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep", "_deferral_sweep_tick", "_interrupt_block_tick",
         "_persist_tick_seen", "_persist_intr_marks", "_persist_spend_trees", "_auto_pause_on_limit", "_usage_poll_tick",
         "_auto_pause_on_spend_limit", "_spend_guard_tick", "_auto_resume_retry", "_auto_resume_session_retry",
         "_auto_retry_tick", "_idle_queue_drive_tick", "_clear_done_working_notes", "_turn_notify_tick", "_apply_pending_ops",
         "_persist_checkpoints", "_converge_checkpoints", "_kernel_sample_tick", "_api_health_push")


class Partition(unittest.TestCase):
    """Which job runs where, pinned by exact membership: a job added to either list must be placed on purpose."""

    def setUp(self):
        self.km = kernel_module()

    def test_the_two_lists_partition_the_jobs_census_in_the_old_order(self):
        km = self.km
        pusher = re.findall(JOB_NAME_RE, inspect.getsource(km._pusher_cycle_jobs))
        jobs = re.findall(JOB_NAME_RE, inspect.getsource(km._jobs_pass))
        self.assertEqual(tuple(pusher), PUSHER_JOBS, "the pusher keeps what feeds a frame or shares the checkpoint budget")
        self.assertEqual(tuple(jobs), HOUSEKEEPING, "the housekeeping, in the order it always ran")
        self.assertFalse(set(pusher) & set(jobs), "no job on both threads")
        self.assertEqual(set(pusher) | set(jobs), set(km._PerfStats.JOBS), "together they are the JOBS census")

    def test_the_jobs_pass_keeps_its_ordering_reasons(self):
        src = inspect.getsource(self.km._jobs_pass)
        order = [src.index(s) for s in ("_lift_spent_awaiting(now, live_map)", "_deferral_sweep_tick(now)",
                                        "_auto_nudge_tick(now, live_map)", "_interrupt_block_tick(now, live_map)",
                                        "_persist_tick_seen()", "_auto_pause_on_spend_limit(now, live_map)",
                                        "_spend_guard_tick(now, live_map)", "_auto_resume_retry(now, live_map)")]
        self.assertEqual(order, sorted(order), "lift before the walk before the interrupt tick before the memo persist; "
                                               "the deferral sweep before the walk; the pause before the guard before the resume")
        self.assertIn('_PERF_STATS.jobs.get("passes", 0) >= 1', src, "the guard skips the boot's first PASS, its own loop's first")
        self.assertNotIn("_push_all", src, "the jobs thread never builds a frame")
        self.assertIn('_PERF_STATS.stage("jobsPass"', src, "the pass is its own container stage")

    def test_main_starts_the_jobs_thread_and_the_docs_list_the_kind(self):
        km = self.km
        src = inspect.getsource(km.main)
        self.assertIn('threading.Thread(target=_jobs_loop, daemon=True, name="jobs").start()', src)
        self.assertLess(src.index("_JOBS_THREAD_STARTED[0] = True"), src.index("target=_jobs_loop"),
                        "the boot row learns of the thread before its first pass can report")
        doc = open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8").read()
        self.assertIn("`jobs` (the housekeeping loop split off the pusher)", doc, "the thread kind is documented")
        self.assertIn("`jobsFirstPassS`", doc, "the boot row's second first is documented")

    def test_the_two_persisted_memos_stage_under_a_per_thread_tmp_name(self):
        km = self.km
        for fn in (km._persist_tick_seen, km._persist_spend_trees):
            src = inspect.getsource(fn)
            self.assertIn('".tmp.%d.%x" % (os.getpid(), threading.get_ident())', src,
                          "%s: the exit's force write runs beside the jobs thread's; two writers under one tmp name unlink "
                          "each other's" % fn.__name__)


class _LabCycles(unittest.TestCase):
    """A hermetic kernel module, every job quiet unless a test speaks for it, the boot row captured, the two loops' first
    marks re-armed as a fresh boot's."""

    def setUp(self):
        self.km = km = kernel_module()
        self.saved = (km.NAMES, km._live_map, km._push_all, km._append_restart_cut, km._BOOT_HEALTH_DONE[0],
                      km._JOBS_THREAD_STARTED[0], dict(km._BOOT_FIRST), km._BOOT_FIRST_CLOSED_MONO[0])
        self.saved_jobs = {nm: getattr(km, nm) for nm in QUIET}
        for nm in QUIET:
            setattr(km, nm, lambda *a, **k: None)
        km.NAMES = {}
        km._live_map = lambda: {}
        km._push_all = lambda live_map=None: None
        self.rows = []
        km._append_restart_cut = lambda row: self.rows.append(row)
        km._BOOT_HEALTH_DONE[0] = False
        km._JOBS_THREAD_STARTED[0] = False
        km._BOOT_FIRST.update({"pusher": None, "jobs": None})
        km._BOOT_FIRST_CLOSED_MONO[0] = None
        self.saved_samplers = (dict(km._FIRST_CYCLE_SAMPLER), dict(km._FIRST_PASS_SAMPLER))
        for slot in (km._FIRST_CYCLE_SAMPLER, km._FIRST_PASS_SAMPLER):
            slot.update({"started": False, "stop": threading.Event(), "rows": [], "thread": None, "failed": 0})
        km._PERF_STATS.reset()

    def tearDown(self):
        km = self.km
        (km.NAMES, km._live_map, km._push_all, km._append_restart_cut, km._BOOT_HEALTH_DONE[0],
         km._JOBS_THREAD_STARTED[0], first, km._BOOT_FIRST_CLOSED_MONO[0]) = self.saved
        km._BOOT_FIRST.update(first)
        for nm, fn in self.saved_jobs.items():
            setattr(km, nm, fn)
        for slot, saved in zip((km._FIRST_CYCLE_SAMPLER, km._FIRST_PASS_SAMPLER), self.saved_samplers):
            slot.clear(); slot.update(saved)
        km._PERF_STATS.reset()


class TheBrowserNeverWaitsOnTheHousekeeping(_LabCycles):
    def test_a_slow_housekeeping_job_lengthens_the_jobs_pass_and_not_the_pusher_cycle(self):
        """The change itself: the reminder walk sleeping 0.4 s costs the jobs pass 0.4 s and the pusher's cycle nothing. At
        the base the walk ran inside the pusher's cycle, so the cycle carried the sleep."""
        km = self.km
        calls = []
        km._auto_nudge_tick = lambda now, live_map: (calls.append(1), time.sleep(0.4))
        self.addCleanup(setattr, km, "_auto_nudge_tick", self.saved_jobs.get("_auto_nudge_tick", km._auto_nudge_tick))
        t0 = time.monotonic(); km._pusher_cycle(); cycle = time.monotonic() - t0
        self.assertEqual(calls, [], "the pusher's cycle ran no walk")
        self.assertLess(cycle, 0.3, "and did not carry its sleep: %.3f s" % cycle)
        t0 = time.monotonic(); km._jobs_cycle(); pas = time.monotonic() - t0
        self.assertEqual(calls, [1], "the jobs pass ran it once")
        self.assertGreaterEqual(pas, 0.4)
        snap = km._PERF_STATS.snapshot()
        self.assertEqual(snap["jobs"]["passes"], 1, "counted under /perf jobs, not pusher")
        self.assertEqual(snap["pusher"]["cycles"], 1)
        self.assertGreaterEqual(snap["stages_ms"]["jobs.autoNudge"], 400.0)
        self.assertLess(snap["pusher"]["cycle_ms_max"], 300.0, "the pusher's own ring never saw the sleep")

    def test_the_jobs_pass_has_its_own_split_and_scope(self):
        km = self.km
        km._jobs_cycle()
        snap = km._PERF_STATS.snapshot()
        first = snap["jobs"]["firstPass"]
        self.assertIsNotNone(first, "the boot's first pass's split is kept under jobs.firstPass")
        self.assertIn("jobsPass", first["stages"], sorted(first["stages"]))
        self.assertIn("jobs.prelude", first["stages"])
        self.assertTrue(any(k.startswith("jobs.") and k != "jobs.prelude" for k in first["stages"]), "the jobs as sub-stages")
        self.assertIsNone(snap["pusher"]["firstCycle"], "the pusher's split is untouched by the jobs thread's pass")
        self.assertEqual(snap["jobs"]["stageRingLen"], 1)
        for k in ("pass_ms_p50", "pass_ms_p90", "pass_ms_ring_max", "passFailed", "splitFailed"):
            self.assertIn(k, snap["jobs"], k)
        self.assertIn("jobsPass", snap["stages_ms"]); self.assertIn("jobs.prelude", snap["stages_ms"])
        self.assertIsNone(km._live_scope.snapshot, "the pass closes its scope")
        self.assertIsNone(km._live_scope.names)

    def test_the_nudge_walk_names_its_parts_on_the_pass_split(self):
        """plans/nudge-walk-events.md, the measurement's first step: the walk's key stats, its snapshot reads and its looks are
        sub-stages of jobs.autoNudge on the pass's split, so a pass that spikes names what it paid; the parts sum to at most
        the job."""
        km = self.km
        # the pass body directly, under the job's own stage: a test earlier in this module leaves a pass in flight on a blocked
        # thread, and the tick's single-flight guard would stand this walk down
        now = int(time.time())
        km._job_stage("autoNudge", lambda: km._auto_nudge_pass(now, km._live_map(), False))
        st = km._PERF_STATS.snapshot()["stages_ms"]
        have = sorted(k for k in st if k.startswith("jobs.autoNudge"))
        self.assertEqual(have, ["jobs.autoNudge", "jobs.autoNudge.key", "jobs.autoNudge.looks", "jobs.autoNudge.snapshot"], have)
        parts = sum(st[k] for k in st if k.startswith("jobs.autoNudge."))
        self.assertLessEqual(parts, st["jobs.autoNudge"] + 1.0, "the parts sum to at most the job")

    def test_the_stats_keep_two_owners_apart(self):
        """A stage closed on the jobs thread lands in the jobs split and never in the pusher's, and the other way round, while
        the cumulative totals take both."""
        km = self.km
        ps = km._PerfStats()
        ps.cycle_begin()                                              # this thread is the pusher
        ps.stage("push.chat", 0.010); ps.stage("push", 0.010)
        done = threading.Event()
        def jobs_thread():
            ps.cycle_begin("jobs")
            ps.stage("jobs.autoNudge", 0.020); ps.stage("jobsPass", 0.020)
            ps.jobs_pass(0.021)
            done.set()
        th = threading.Thread(target=jobs_thread); th.start(); th.join(5)
        self.assertTrue(done.is_set())
        ps.stage("jobs.apiHealth", 0.001); ps.stage("jobs", 0.001)
        ps.cycle(0.012)
        snap = ps.snapshot()
        self.assertEqual(sorted(snap["pusher"]["firstCycle"]["stages"]), ["jobs", "jobs.apiHealth", "push", "push.chat"])
        self.assertEqual(sorted(snap["jobs"]["firstPass"]["stages"]), ["jobs.autoNudge", "jobsPass"])
        self.assertAlmostEqual(snap["jobs"]["firstPass"]["stages"]["jobsPass"]["ms"], 20.0)
        self.assertAlmostEqual(snap["stages_ms"]["jobs.autoNudge"], 20.0, "the totals take every thread's stages")
        self.assertEqual(ps._mine(), "pusher")
        self.assertEqual(snap["jobs"]["passes"], 1)
        self.assertEqual(snap["pusher"]["cycles"], 1)


class TheBootRowCarriesBothFirsts(_LabCycles):
    def test_with_the_jobs_thread_started_the_row_waits_for_the_later_first(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._pusher_cycle()
        self.assertEqual(self.rows, [], "the pusher's first cycle alone writes no row: the jobs pass is still open")
        self.assertFalse(km._BOOT_HEALTH_DONE[0])
        km._auto_nudge_tick = lambda now, live_map: time.sleep(0.05)
        self.addCleanup(setattr, km, "_auto_nudge_tick", self.saved_jobs.get("_auto_nudge_tick", km._auto_nudge_tick))
        km._jobs_cycle()
        self.assertEqual(len(self.rows), 1, "the jobs thread's first pass, the later of the two, wrote it")
        row = self.rows[0]
        self.assertTrue(row["bootHealth"])
        self.assertIn("firstCycleS", row); self.assertIn("slow", row)
        self.assertIn("jobsFirstPassS", row); self.assertIn("jobsSlow", row)
        self.assertGreaterEqual(row["jobsFirstPassS"], 0.05)
        self.assertIn("prelude", row["stages"], "the pusher's split rides the row")
        self.assertIn("jobsPass", row["stages"], "and the jobs thread's")
        self.assertIn("jobs.autoNudge", row["stages"])
        self.assertIsInstance(row["firstCycleStacks"], list)
        self.assertIsInstance(row["firstPassStacks"], list, "the jobs thread's first pass was sampled too")
        self.assertNotIn("jobsFirstPassPending", row)
        self.assertTrue(km._BOOT_HEALTH_DONE[0])
        self.assertEqual(km._BOOT_FIRST, {"pusher": None, "jobs": None}, "the firsts are cleared once the row is written")
        km._pusher_cycle(); km._jobs_cycle()
        self.assertEqual(len(self.rows), 1, "later cycles and passes write nothing")

    def test_whichever_loop_finishes_its_first_last_writes_the_row(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._jobs_cycle()
        self.assertEqual(self.rows, [], "the jobs pass first: the row waits for the pusher")
        km._pusher_cycle()
        self.assertEqual(len(self.rows), 1)
        self.assertIn("jobsFirstPassS", self.rows[0]); self.assertIn("firstCycleS", self.rows[0])

    def test_without_a_jobs_thread_the_pushers_first_cycle_writes_the_row_at_once(self):
        """A test driving one cycle, or a kernel whose main never started the thread: the old contract holds."""
        km = self.km
        km._pusher_cycle()
        self.assertEqual(len(self.rows), 1)
        self.assertIn("firstCycleS", self.rows[0])
        self.assertNotIn("jobsFirstPassS", self.rows[0])
        self.assertNotIn("firstPassStacks", self.rows[0], "no jobs thread, no second sample list")
        self.assertIsNone(km._boot_health_first_cycle(0.1), "the second cycle is not the boot's first")

    def test_a_jobs_pass_that_never_ends_still_leaves_a_row_by_the_backstop(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._pusher_cycle()
        self.assertEqual(self.rows, [])
        self.assertIsNone(km._boot_health_row_backstop(time.monotonic()), "well inside the bound: nothing yet")
        with mock.patch.object(km, "BOOT_JOBS_PASS_ROW_BACKSTOP_S", 0.0):
            km._pusher_cycle()                                        # a later cycle runs the backstop
        self.assertEqual(len(self.rows), 1, "the row without the jobs pass")
        row = self.rows[0]
        self.assertTrue(row.get("jobsFirstPassPending"))
        self.assertIn("firstCycleS", row); self.assertNotIn("jobsFirstPassS", row)
        self.assertTrue(km._BOOT_HEALTH_DONE[0])
        km._jobs_cycle()
        self.assertEqual(len(self.rows), 1, "the late pass writes no second row")

    def test_the_slow_lines_name_their_thread(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        err = io.StringIO()
        with mock.patch.object(km, "BOOT_FIRST_CYCLE_BOUND_S", 0.0), mock.patch.object(km.sys, "stderr", err):
            km._pusher_cycle(); km._jobs_cycle()
        self.assertIn("the first pusher cycle took", err.getvalue())
        self.assertIn("the first housekeeping pass took", err.getvalue())
        self.assertIn("the browser did not wait on it", err.getvalue())


class TheJobsLoop(_LabCycles):
    def test_a_pass_that_raises_is_counted_and_the_loop_goes_on(self):
        km = self.km
        n = [0]
        def cycle():
            n[0] += 1
            if n[0] <= 2:
                raise RuntimeError("no thread slot")
            km._LOOPS_STOP.set()
        err = io.StringIO()
        saved_stop = km._LOOPS_STOP.is_set()
        with mock.patch.object(km, "_jobs_cycle", cycle), mock.patch.object(km, "_JOBS_FAILED_SAID", {}), \
             mock.patch.object(km.sys, "stderr", err), mock.patch.dict(km._PERF_STATS.jobs, {"passFailed": 0}), \
             mock.patch.object(km, "PUSHER_FAIL_BACKOFF_S", (0.01, 0.01, 0.01, 0.01)):
            try:
                km._jobs_loop()
                self.assertEqual(n[0], 3, "two raising passes were skipped and the third ran")
                self.assertEqual(km._PERF_STATS.jobs["passFailed"], 2)
            finally:
                if not saved_stop:
                    km._LOOPS_STOP.clear()
        self.assertEqual(err.getvalue().count("jobs: a pass raised RuntimeError"), 1, "said once per kind: %r" % err.getvalue())
        self.assertIn("passFailed", km._PERF_STATS.snapshot()["jobs"])

    def test_the_loop_paces_at_the_pushers_backstop_and_stops_with_the_loops(self):
        km = self.km
        self.assertEqual(km.JOBS_PASS_S, 0.5)
        n = [0]
        def cycle():
            n[0] += 1
            if n[0] == 2:
                km._LOOPS_STOP.set()
        saved_stop = km._LOOPS_STOP.is_set(); km._LOOPS_STOP.clear()
        t0 = time.monotonic()
        try:
            with mock.patch.object(km, "_jobs_cycle", cycle), mock.patch.object(km, "JOBS_PASS_S", 0.05):
                km._jobs_loop()
        finally:
            if saved_stop: km._LOOPS_STOP.set()
            else: km._LOOPS_STOP.clear()
        self.assertEqual(n[0], 2)
        self.assertGreaterEqual(time.monotonic() - t0, 0.05, "one pace between the two passes")


if __name__ == "__main__":
    unittest.main()
