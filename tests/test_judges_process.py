#!/usr/bin/env python3
"""Stage three of the process split (plans/judges-process.md), the kernel's side: with STATE/judges-process `on` the producer
sends one `pass` line per wake to a long-lived `romp-judge --serve` child and feeds its `done` line to /perf's judge block, its
own bookkeeping standing around the request in the loop's order (the goals snapshot opened before, closed after; the generation
bumped only when a store moved); a child that exits mid-pass, hangs past the hard bound, answers a malformed line or speaks a
protocol version this kernel does not know costs a lost pass, never a silent accept, and comes back on the next wake; with the
switch off (the default) the in-process tiers run and no child starts; tracking off sends no request. The child is a stand-in
script answering the protocol romp_metrics's child speaks, through ROMP_JUDGE_SERVE_CMD."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import kernel_module   # noqa: E402  the hermetic kernel over a temp state root

SID = "11111111-2222-3333-4444-777777777777"
FAKE = r'''
import json, os, signal, sys, time
mode = os.environ.get("FAKE_MODE", "")
if mode == "wedged":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)         # a child that answers neither the quit nor the terminate
log = os.environ.get("FAKE_LOG", "")
store = os.environ.get("FAKE_STORE", "")
sys.stdout.write(json.dumps({"op": "ready", "pid": os.getpid(), "judgeVersion": "fake",
                             "protocolVersion": int(os.environ.get("FAKE_PROTO", "1"))}) + "\n")
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    if req.get("op") == "quit":
        if mode == "wedged":
            time.sleep(60)
        break
    if log:
        with open(log, "a") as f:
            f.write(line)
    seq = req["seq"]
    if mode == "exit-on-2" and seq == 2:
        sys.exit(3)
    if mode == "hang-on-2" and seq == 2:
        time.sleep(30)
    if mode == "garbage-on-2" and seq == 2:
        sys.stdout.write("romp-judge: not a protocol line\n"); sys.stdout.flush()
        continue
    if mode == "stray-on-2" and seq == 2:
        sys.stdout.write("romp-judge: a stray diagnostic on the protocol channel\n"); sys.stdout.flush()   # then the real done below
    if mode == "partial-on-2" and seq == 2:
        sys.stdout.write('{"op": "done", "seq": 2, "wallMs": 1'); sys.stdout.flush()   # half a line, then a stall
        time.sleep(30)
    if store:
        with open(store, "w") as f:
            f.write(json.dumps({"rompUuid": os.path.basename(store)[:-5], "seq": seq, "closedTurns": [], "nodes": {}, "placements": {}, "status": {}}))
    may = req.get("mayStart", False)
    sys.stdout.write(json.dumps({"op": "done", "seq": seq, "wallMs": 12.5, "tierStarts": 2 if may else 0,
                                 "tierCpuMs": 3.0, "workerCpuMs": 4.0, "failures": None, "recovered": mode == "recovered",
                                 "recordCache": {"entries": 1}, "asmCheckpoint": {"restore": 1}}) + "\n")
    sys.stdout.flush()
'''


class _Child(unittest.TestCase):
    def setUp(self):
        self.km = km = kernel_module()
        self.jd = km.jd
        self.td = tempfile.mkdtemp()
        self.script = os.path.join(self.td, "fake-judge.py")
        Path(self.script).write_text(FAKE)
        self.log = os.path.join(self.td, "requests.jsonl")
        self.env_saved = {k: os.environ.get(k) for k in ("ROMP_JUDGE_SERVE_CMD", "FAKE_MODE", "FAKE_LOG", "FAKE_STORE", "FAKE_PROTO")}
        os.environ["ROMP_JUDGE_SERVE_CMD"] = "%s %s" % (sys.executable, self.script)
        os.environ["FAKE_LOG"] = self.log
        for k in ("FAKE_MODE", "FAKE_STORE", "FAKE_PROTO"):
            os.environ.pop(k, None)
        self.saved = (km._wait_boot_attached, km._live_map, km._retry_paused_on, km._sdk, km._task_tracking_on,
                      getattr(km, "JUDGE_CHILD_PASS_HARD_S", None), self.jd.run_index, self.jd.run_triage)
        km._wait_boot_attached = lambda: True
        km._live_map = lambda: {SID: {"state": "waiting", "since": 1}}
        km._retry_paused_on = lambda: False
        km._sdk = lambda: None
        if hasattr(km, "_PRODUCER_ONE_PASS"):
            km._PRODUCER_ONE_PASS[0] = True
        if hasattr(km, "_JUDGE_CHILD"):
            km._JUDGE_CHILD.__init__()                    # a fresh child slot: no process, seq 0, never started
        self._reset_judge_counters()
        self.switch = self.jd.STATE / getattr(km, "JUDGES_PROCESS_FILE", "judges-process")
        self.jd.GOALDIR.mkdir(parents=True, exist_ok=True)

    def _reset_judge_counters(self):
        with self.km._PERF_STATS.lock:
            self.km._PERF_STATS.judge.update({"passes": 0, "tierStarts": 0})
            for k, v in (("passesLost", 0), ("childRestarts", 0), ("orphansSwept", 0), ("cpu_ms_child_workers", 0.0), ("child", None)):
                if k in self.km._PERF_STATS.judge:
                    self.km._PERF_STATS.judge[k] = v

    def tearDown(self):
        km = self.km
        if hasattr(km, "_JUDGE_CHILD"):
            km._JUDGE_CHILD.stop()
        if hasattr(km, "_PRODUCER_ONE_PASS"):
            km._PRODUCER_ONE_PASS[0] = False
        (km._wait_boot_attached, km._live_map, km._retry_paused_on, km._sdk, km._task_tracking_on,
         km.JUDGE_CHILD_PASS_HARD_S, self.jd.run_index, self.jd.run_triage) = self.saved
        for k, v in self.env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            self.switch.unlink()
        except OSError:
            pass
        for p in (self.jd.GOALDIR / (SID + ".json"),):
            try:
                p.unlink()
            except OSError:
                pass

    def _on(self):
        self.switch.write_text("on\n")

    def _pass(self):
        """One producer pass. The head's loop returns after a pass on the test sentinel; a kernel without it (the base) is
        driven on a thread and stopped after its first pass, so every test here is red at the base on its assertion."""
        km = self.km
        if hasattr(km, "_PRODUCER_ONE_PASS"):
            km._producer()
            return
        import threading
        passes0 = km._PERF_STATS.judge["passes"]
        t = threading.Thread(target=km._producer, daemon=True)
        t.start()
        deadline = time.monotonic() + 20
        while km._PERF_STATS.judge["passes"] == passes0 and time.monotonic() < deadline:   # loop-ok: a bounded wait on the pass counter
            time.sleep(0.02)
        km._LOOPS_STOP.set(); km._producer_wake.set()
        t.join(10)
        km._LOOPS_STOP.clear()

    def _requests(self):
        try:
            return [json.loads(l) for l in Path(self.log).read_text().splitlines() if l.strip()]
        except OSError:
            return []

    def _judge(self):
        return self.km._PERF_STATS.snapshot()["judge"]


class ChildRoad(_Child):
    def test_one_pass_through_the_child_feeds_perf_and_keeps_the_kernels_bookkeeping_in_order(self):
        km = self.km
        self._on()
        os.environ["FAKE_STORE"] = str(self.jd.GOALDIR / (SID + ".json"))   # the child's judges write a store during the pass
        order = []
        child = getattr(km, "_JUDGE_CHILD", None)         # absent at the base: the order then never shows a pass, the red below
        real_begin, real_end, real_pass = km._begin_goals_pass, km._end_goals_pass, getattr(child, "pass_", None)
        km._begin_goals_pass = lambda: (order.append("begin"), real_begin())[1]
        km._end_goals_pass = lambda: (order.append("end"), real_end())[1]
        if child is not None:
            child.pass_ = lambda now, tracking=True: (order.append("pass"), real_pass(now, tracking))[1]
        called = []
        self.jd.run_index = lambda now=None: called.append("index")
        self.jd.run_triage = lambda now=None: called.append("triage")
        gen0 = km._judge_gen[0]
        cpu0 = km._PERF_STATS.judge["cpu_ms_sum"]
        try:
            self._pass()
        finally:
            km._begin_goals_pass, km._end_goals_pass = real_begin, real_end
            if child is not None:
                child.pass_ = real_pass
        reqs = self._requests()
        self.assertEqual(len(reqs), 1, "one pass line per wake: %r" % reqs)
        self.assertEqual((reqs[0]["op"], reqs[0]["seq"], reqs[0].get("mayStart")), ("pass", 1, True), "the gate's verdict rides the request as mayStart")
        self.assertNotIn("tracking", reqs[0], "one word on the wire: mayStart alone")
        self.assertEqual(sorted(reqs[0]), ["mayStart", "now", "op", "seq"])
        self.assertAlmostEqual(reqs[0]["now"], time.time(), delta=30)
        self.assertEqual(called, [], "no in-process tier ran")
        self.assertEqual(order[:3], ["begin", "pass", "end"], "the goals snapshot opens before the request and closes after it: %r" % order)
        self.assertEqual(km._judge_gen[0], gen0 + 1, "the generation bumped once: the child's store write, seen after its done")
        j = self._judge()
        self.assertEqual((j["passes"], j["tierStarts"], j.get("passesLost"), j.get("childRestarts")), (1, 2, 0, 0))
        self.assertEqual(j.get("cpu_ms_child_workers"), 4.0)
        self.assertEqual(j["cpu_ms_sum"] - cpu0, 7.0, "tier plus workers, as the in-process figure counts")
        self.assertEqual((j.get("child") or {}).get("seq"), 1)
        self.assertEqual((j.get("child") or {}).get("pid"), getattr(getattr(km, "_JUDGE_CHILD", None), "pid", None))
        self.assertEqual((j.get("child") or {}).get("recordCache"), {"entries": 1})

    def test_a_pass_with_no_store_moved_bumps_no_generation(self):
        km = self.km
        self._on()
        self._pass()                                      # anchors the fingerprint (the first pass)
        gen0 = km._judge_gen[0]
        self._pass()
        self.assertEqual(km._judge_gen[0], gen0, "nothing moved: the views keep their signature")
        self.assertEqual(len(self._requests()), 2, "and the one child served both passes")
        self.assertEqual(self._judge()["childRestarts"], 0)

    def test_the_default_runs_the_tiers_in_process_and_starts_no_child(self):
        km = self.km
        called = []
        self.jd.run_index = lambda now=None: called.append("index")
        self.jd.run_triage = lambda now=None: called.append("triage")
        self._pass()
        self.assertEqual(sorted(called), ["index", "triage"], "the in-process tiers ran")
        self.assertIsNone(getattr(km, "_JUDGE_CHILD", None) and km._JUDGE_CHILD.proc, "no child started")
        self.assertEqual(self._requests(), [])
        self.assertEqual(self._judge()["tierStarts"], 2)

    def test_tracking_off_sends_no_request_and_starts_no_child(self):
        km = self.km
        self._on()
        km._task_tracking_on = lambda: False
        self._pass()
        self.assertEqual(self._requests(), [])
        self.assertIsNone(getattr(km, "_JUDGE_CHILD", None) and km._JUDGE_CHILD.proc)
        self.assertEqual(self._judge()["tierStarts"], 0)


class FailureRoads(_Child):
    def test_a_child_that_exits_mid_pass_loses_the_pass_and_comes_back_on_the_next_wake(self):
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "exit-on-2"
        self._pass(); self._pass()
        j = self._judge()
        self.assertEqual((j["passes"], j.get("passesLost"), j.get("childRestarts")), (2, 1, 0), "the second pass is lost: %r" % j)
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "the dead child is cleared")
        self._pass()
        j = self._judge()
        self.assertEqual((j["passes"], j.get("passesLost"), j.get("childRestarts")), (3, 1, 1), "the third wake restarts it and is served")
        self.assertEqual([r["seq"] for r in self._requests()], [1, 2, 3])

    def test_a_child_silent_past_the_hard_bound_is_killed_and_the_pass_lost(self):
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "hang-on-2"
        km.JUDGE_CHILD_PASS_HARD_S = 1.5
        self._pass()
        t0 = time.monotonic(); self._pass(); dt = time.monotonic() - t0
        j = self._judge()
        self.assertEqual((j.get("passesLost"), j.get("childRestarts")), (1, 0))
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "killed")
        self.assertLess(dt, 10, "the pass ended at the bound, not at the child's leisure")
        self._pass()
        self.assertEqual((self._judge().get("passesLost"), self._judge().get("childRestarts")), (1, 1))

    def test_a_malformed_done_is_a_lost_pass_never_an_accept(self):
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "garbage-on-2"
        self._pass()
        child_before = self._judge().get("child")
        self._pass()
        j = self._judge()
        self.assertEqual(j.get("passesLost"), 1)
        self.assertEqual(j.get("child"), child_before, "nothing of the garbage line landed on the block")
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "the child is killed: its pipe cannot be trusted (round two, high 2)")
        self.assertEqual(j["tierStarts"], 4, "the tiers were asked for on both requests; the lost pass changes no count after the fact")
        self._pass()
        self.assertEqual((self._judge()["passes"], self._judge().get("childRestarts")), (3, 1), "a fresh child serves the next pass")

    def test_a_stray_line_before_the_done_kills_the_child_so_the_pipe_never_desyncs(self):
        """Round two, high 2: the stray line used to be read as not-its-done and the child kept, so the real done sat in the
        pipe and every later pass read the previous one (seq mismatch, lost, judge.child frozen)."""
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "stray-on-2"
        self._pass(); self._pass()
        j = self._judge()
        self.assertEqual(j.get("passesLost"), 1, "the stray pass is lost")
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "and the child is killed: its pipe holds a done nobody asked for")
        self._pass(); self._pass()
        j = self._judge()
        self.assertEqual((j["passes"], j.get("passesLost"), j.get("childRestarts")), (4, 1, 1), "the next passes are served by a fresh child: %r" % j)
        self.assertEqual((j.get("child") or {}).get("seq"), 4, "judge.child follows the passes again")

    def test_a_partial_line_and_a_stall_are_held_to_the_hard_bound(self):
        """Round two, medium 1: a blocking readline after select held the pass 25 s against a 2 s bound."""
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "partial-on-2"
        km.JUDGE_CHILD_PASS_HARD_S = 1.5
        self._pass()
        t0 = time.monotonic(); self._pass(); dt = time.monotonic() - t0
        self.assertLess(dt, 8, "the pass ended at the bound: %.1f s" % dt)
        self.assertEqual(self._judge().get("passesLost"), 1)
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "killed at the bound")

    def test_the_exit_road_ends_the_child_and_drops_its_pid_record(self):
        """Round two, high 1: a child mid-pass outlived the kernel's os._exit and wrote stores beside the successor's child."""
        import inspect
        km = self.km
        self._on()
        self._pass()
        child = getattr(km, "_JUDGE_CHILD", None)
        proc = getattr(child, "proc", None)
        self.assertIsNotNone(proc, "a child runs after a pass on the child road (the base has none)")
        rec = self.jd.STATE / ("judge-child.%d.json" % os.getpid())
        self.assertTrue(rec.exists(), "the pid record is written at the spawn, under this kernel's pid")
        self.assertEqual(json.loads(rec.read_text())["parent"], os.getpid())
        child.end()
        self.assertIsNotNone(proc.poll(), "the child is gone")
        self.assertFalse(rec.exists(), "and its record with it")
        src = inspect.getsource(km._drain_and_exit)
        self.assertLess(src.index("_JUDGE_CHILD.end()"), src.index("os._exit(0)"), "the exit road ends the child before it leaves")

    def test_an_orphan_left_by_a_dead_kernel_is_swept_at_the_first_request(self):
        import subprocess
        km = self.km
        self._on()
        orphan = subprocess.Popen([sys.executable, self.script, "--serve", "romp-judge"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        orphan.stdout.readline()                                  # its ready line: it now waits on stdin like a child mid-life; the
        #                                                           extra words let the sweep's command check name it as the judge child
        gone = subprocess.Popen(["true"]); gone.wait(); dead = gone.pid   # a pid nothing runs under any more: the "kernel" that left it
        try:
            (self.jd.STATE / ("judge-child.%d.json" % dead)).write_text(json.dumps({"pid": orphan.pid, "parent": dead, "t": 1}))
            if hasattr(km, "_JUDGE_CHILD"):
                km._JUDGE_CHILD.__init__()
            n0 = self._judge().get("orphansSwept")
            self._pass()                                          # the boot sweep and the first request's both run before it spawns
            try:
                orphan.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.assertIsNotNone(orphan.poll(), "the orphan is ended (the base sweeps nothing)")
            self.assertEqual((self._judge().get("orphansSwept") or 0) - (n0 or 0), 1)
            self.assertFalse((self.jd.STATE / ("judge-child.%d.json" % dead)).exists(), "the dead kernel's record is gone")
            self.assertEqual(json.loads((self.jd.STATE / ("judge-child.%d.json" % os.getpid())).read_text())["pid"],
                             getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None) and km._JUDGE_CHILD.proc.pid,
                             "this kernel's own record names its own child")
        finally:
            if orphan.poll() is None:
                orphan.kill()

    def test_a_record_naming_a_live_parent_is_left_alone(self):
        import subprocess
        km = self.km
        self._on()
        other = subprocess.Popen([sys.executable, self.script, "--serve", "romp-judge"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        other.stdout.readline()
        try:
            peer = self.jd.STATE / ("judge-child.%d.json" % os.getppid())
            peer.write_text(json.dumps({"pid": other.pid, "parent": os.getppid(), "t": 1}))
            if hasattr(km, "_JUDGE_CHILD"):
                km._JUDGE_CHILD.__init__()
            self._pass()
            self.assertIsNone(other.poll(), "another live kernel's child is its own")
            self.assertEqual(self._judge().get("orphansSwept") or 0, 0)
            self.assertIsNotNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "and this kernel's own child was started beside it")
            self.assertTrue(peer.exists(), "the peer's record stands: records are per kernel pid, never overwritten")
            self.assertTrue((self.jd.STATE / ("judge-child.%d.json" % os.getpid())).exists(), "and this kernel's is its own file")
        finally:
            other.kill()

    def test_three_lost_spawns_in_a_row_fall_back_to_the_in_process_tiers_until_the_switch_is_written_again(self):
        """Round two, medium 2: a crash-looping child respawned on every wake with no cap and no word where the user looks."""
        km = self.km
        self._on()
        os.environ["FAKE_PROTO"] = "2"                             # refused at every spawn
        called = []
        self.jd.run_index = lambda now=None: called.append("index")
        self.jd.run_triage = lambda now=None: called.append("triage")
        with km._SYNC_LOCK:
            del km._SYNC_NOTICES[:]
        self._pass(); self._pass(); self._pass()
        j = self._judge()
        self.assertEqual((j.get("passesLost"), j.get("childFallbacks")), (3, 1), "the third lost spawn latches the fallback: %r" % j)
        self.assertEqual(called, [], "no in-process tier ran while the child was tried")
        with km._SYNC_LOCK:
            texts = [n["text"] for n in km._SYNC_NOTICES]
        self.assertTrue(any("judges' process lost 3 passes" in t and km.JUDGES_PROCESS_FILE in t for t in texts), "said where the user looks: %r" % texts)
        self.assertFalse(getattr(km, "_judges_in_child", lambda: False)(), "the switch reads off while the latch stands")
        self._pass()
        self.assertEqual(sorted(called), ["index", "triage"], "the fourth pass runs the tiers in process")
        self.assertEqual(self._judge().get("passesLost"), 3, "and loses nothing more")
        self.switch.write_text("on \n")                            # the file written again: the latch lifts
        self.assertTrue(getattr(km, "_judges_in_child", lambda: False)())

    def test_a_dead_kernels_child_is_swept_at_boot_with_the_switch_off_and_no_request_sent(self):
        """Round three: the sweep ran only at the first request, so a kernel with the switch off (or one that never judged)
        left a dead kernel's child running for good."""
        import subprocess
        km = self.km
        orphan = subprocess.Popen([sys.executable, self.script, "--serve", "romp-judge"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        orphan.stdout.readline()
        gone = subprocess.Popen(["true"]); gone.wait(); dead = gone.pid
        rec = self.jd.STATE / ("judge-child.%d.json" % dead)
        try:
            rec.write_text(json.dumps({"pid": orphan.pid, "parent": dead, "t": 1}))
            self.jd.run_index = lambda now=None: None
            self.jd.run_triage = lambda now=None: None
            self._pass()                                          # the switch is off: the producer boots, sweeps, judges in process
            try:
                orphan.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.assertIsNotNone(orphan.poll(), "the orphan is ended at boot (the base sweeps only at a request, so never here)")
            self.assertEqual(self._judge().get("orphansSwept") or 0, 1)
            self.assertFalse(rec.exists(), "the dead kernel's record is gone")
            self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "and no child was started: the switch is off")
            self.assertEqual(self._requests(), [], "no request was sent")
        finally:
            if orphan.poll() is None:
                orphan.kill()

    def test_the_exit_road_ends_a_child_under_a_pass_in_flight_at_once_and_the_pass_is_counted_lost(self):
        """Round three, high: end() waited on the pass lock, so a kernel leaving under a long pass sat out the pass's hard bound
        (fifteen minutes) against a SIGTERM grace of seconds, and the manager's kill took the child's stores mid-write."""
        import threading
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "hang-on-2"                     # the second pass sleeps thirty seconds in the child
        grace_saved = getattr(km, "EXIT_GRACE_S", None)
        km.EXIT_GRACE_S = 2.0
        try:
            self._pass()
            child = getattr(km, "_JUDGE_CHILD", None)
            proc = getattr(child, "proc", None)
            self.assertIsNotNone(proc, "a child runs after the first pass (the base has none)")
            lost0 = self._judge().get("passesLost") or 0
            t = threading.Thread(target=self._pass, daemon=True)
            t.start()
            deadline = time.monotonic() + 10
            while len(self._requests()) < 2 and time.monotonic() < deadline:   # loop-ok: a bounded wait for the child to take the request
                time.sleep(0.02)
            self.assertEqual(len(self._requests()), 2, "the second request reached the child, which now sleeps on it")
            t0 = time.monotonic()
            child.end()
            dt = time.monotonic() - t0
            self.assertIsNotNone(proc.poll(), "the child is gone")
            self.assertLess(dt, km.EXIT_GRACE_S, "end() returned inside the grace, not after the pass's hard bound")
            t.join(10)
            self.assertFalse(t.is_alive(), "the pass thread came back on the closed pipe")
            self.assertEqual((self._judge().get("passesLost") or 0) - lost0, 1, "the pass in flight is counted lost")
            self.assertIsNone(child.proc, "and no child stands")
            self.assertFalse((self.jd.STATE / ("judge-child.%d.json" % os.getpid())).exists(), "its record is gone")
        finally:
            if grace_saved is not None:
                km.EXIT_GRACE_S = grace_saved

    def test_a_wedged_child_is_ended_inside_the_grace_with_budgets_that_scale_with_it(self):
        """Round three, medium: the quit and the terminate each waited a fixed five seconds, so a child that answered neither
        cost ten seconds of an eight-second grace."""
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "wedged"                        # ignores SIGTERM, sleeps on the quit
        grace_saved = getattr(km, "EXIT_GRACE_S", None)
        budgets = getattr(km, "_judge_child_budgets", None)
        self.assertIsNotNone(budgets, "the child's end budgets derive from the grace (the base waits fixed seconds)")
        try:
            km.EXIT_GRACE_S = 4.0
            self.assertAlmostEqual(sum(budgets()), 1.0, places=6, msg="a quarter of the grace in all")
            km.EXIT_GRACE_S = 8.0
            self.assertAlmostEqual(sum(budgets()), 2.0, places=6, msg="and it scales with the grace, never a constant")
            km.EXIT_GRACE_S = 2.0
            self._pass()
            child = km._JUDGE_CHILD
            proc = child.proc
            self.assertIsNotNone(proc)
            t0 = time.monotonic()
            child.end()
            dt = time.monotonic() - t0
            self.assertIsNotNone(proc.poll(), "the wedged child is killed")
            self.assertEqual(proc.returncode, -9, "by the kill, after the quit and the terminate went unanswered")
            self.assertLess(dt, km.EXIT_GRACE_S, "inside the grace")
        finally:
            if grace_saved is not None:
                km.EXIT_GRACE_S = grace_saved

    def test_the_spawns_preexec_does_no_work_after_fork(self):
        """Round three, medium: the preexec imported ctypes and opened libc in the forked child of a many-threaded kernel, an
        import lock or an allocator lock away from a deadlock at every spawn."""
        import inspect
        km = self.km
        pre = getattr(km, "_judge_child_preexec", None)
        self.assertIsNotNone(pre, "the spawn asks for the parent-death signal between fork and exec (the base spawns nothing)")
        src = inspect.getsource(pre)
        self.assertNotIn("import", src.split('"""')[-1], "no import after fork")
        self.assertNotIn("CDLL", src.split('"""')[-1], "no dlopen after fork")
        self.assertIn("_PRCTL(", src, "one call through the pointer bound at import")
        if sys.platform.startswith("linux"):
            self.assertIsNotNone(getattr(km, "_PRCTL", None), "the pointer is bound at import on Linux")

    def test_the_switch_turning_off_ends_an_idle_child_on_that_pass(self):
        km = self.km
        self._on()
        self._pass()
        self.assertIsNotNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "a child runs with the switch on")
        self.switch.unlink()
        called = []
        self.jd.run_index = lambda now=None: called.append("index")
        self.jd.run_triage = lambda now=None: called.append("triage")
        self._pass()
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "the child is ended on the off pass")
        self.assertEqual(sorted(called), ["index", "triage"])

    def test_an_unreadable_switch_reads_off_and_says_so_once(self):
        km = self.km
        with km._SYNC_LOCK:
            del km._SYNC_NOTICES[:]
        self.switch.write_bytes(b"\xff\xfe on")                    # not UTF-8: used to raise inside the pass and skip every wake
        read = getattr(km, "_judges_in_child", lambda: None)
        self.assertFalse(read()); self.assertFalse(read())
        with km._SYNC_LOCK:
            texts = [n["text"] for n in km._SYNC_NOTICES]
        self.assertEqual(len(texts), 1, "said once: %r" % texts)
        self.assertIn("could not be read", texts[0])
        self.switch.unlink(); self.switch.mkdir()                  # a directory where the file should be: an OSError, off, said
        self.assertFalse(read())
        self.switch.rmdir()
        with km._SYNC_LOCK:
            self.assertEqual(len(km._SYNC_NOTICES), 2)

    def test_a_recovered_flag_on_the_done_re_arms_the_given_up_cards(self):
        km = self.km
        self._on()
        os.environ["FAKE_MODE"] = "recovered"
        calls = []
        real = self.jd.rearm_failed_summaries
        self.jd.rearm_failed_summaries = lambda now, auto=False: (calls.append(auto), 2)[1]
        try:
            self._pass()
        finally:
            self.jd.rearm_failed_summaries = real
        self.assertEqual(calls, [True], "the child's recovery edge drives the auto re-arm")

    def test_a_protocol_version_this_kernel_does_not_speak_is_refused(self):
        km = self.km
        self._on()
        os.environ["FAKE_PROTO"] = "2"
        self._pass()
        j = self._judge()
        self.assertEqual((j["passes"], j.get("passesLost"), j["tierStarts"]), (1, 1, 0), "refused: the pass is lost, no request written, no tier start counted")
        self.assertIsNone(getattr(getattr(km, "_JUDGE_CHILD", None), "proc", None), "and the child is not kept")
        self.assertEqual(self._requests(), [], "no request reached it")


if __name__ == "__main__":
    unittest.main()
