"""A session's hook callbacks must not wait behind the kernel-wide registry lock (2026-10-09).

Each session runs its own asyncio loop on its own thread, and that loop is the only place the SDK answers the CLI's
hook callbacks (UserPromptSubmit, PostToolUse, Stop, SubagentStart, ...). The host transport acknowledges every record
it hands to that loop, and the acknowledgement wrote `hostAck` into the registry SYNCHRONOUSLY, from inside the record
hand-over (_read_socket -> _take -> _advance -> on_ack -> SdkBackend._write_host_ack -> _update_reg). _update_reg takes
SdkBackend._reg_lock, ONE lock for every session's registry read-modify-write, so while any other thread held it (another
session's write of a large registry, an HTTP handler, a long garbage collection inside a holder) the whole session loop
stood still: no hook was dispatched or answered until the CLI gave up on it (540 s for prompt, tool and stop hooks, 180 s
for subagent hooks), and queued prompts were refused one after another. A stack read of a live kernel showed most of its
session threads parked at exactly that frame.

These tests reproduce the stall with synthetic data only: one thread holds _reg_lock (standing in for the other
session's long write) while a session loop hands over a record and then has a hook to answer. The hook must be answered
while the lock is still held, and the acknowledgement must still land once the lock frees.
"""
import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_CLI_SCOPE"] = "0"
if importlib.util.find_spec("claude_agent_sdk") is None:
    _tag = "python%d.%d" % sys.version_info[:2]
    for _sp in sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(_tag + "/site-packages")):
        sys.path.insert(0, str(_sp))
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))
ht = sb._ht()

SID = "5e1f0a77-2222-4333-8444-0000000000c1"     # private synthetic sid (never a real session)
LOOP_PREFIX = getattr(sb, "SESSION_LOOP_THREAD_PREFIX", "sdk:")   # the session loop threads' name prefix (SdkSession.start)
HELLO = {"t": "hello", "host": {"pid": 4242, "start": "h1"}, "cli": {"pid": 4343, "start": "c1"}, "journal": {"next": 0}}


class HookCallbackNotBehindRegLock(unittest.TestCase):

    def _be(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "session-hosts").write_text("off")      # this test runs no host process
        logs = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=logs.append)
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "web", "alive": True})
        return d, be

    def _session_and_transport(self, be):
        """A session stand-in with a real HostTransport whose on_ack is the backend's own wiring (_new_host_transport),
        hello already received, as on a live attach."""
        s = types.SimpleNamespace(sid=SID, name="web", _host=None, _host_ack_t=0.0, _host_end_grace=None,
                                  _on_cli_stderr=lambda line: None)
        t = be._new_host_transport(s, "/nonexistent.sock", -1)
        t.hello = dict(HELLO)
        t.replay_end = 0
        s._host = t
        return s, t

    def _run_session_loop(self, t, answered, handed):
        """The session's loop thread: the SDK spawns a hook handler for a control request, then its reader pulls the next
        record from the transport, which hands it over (and acknowledges it) synchronously, as _read_socket does."""
        def run():
            async def main():
                async def hook_handler():                     # SdkSession's hook coroutine: answers at once
                    answered.set()
                    return {}
                task = asyncio.ensure_future(hook_handler())
                t._take({"t": "out", "offset": 7, "data": {"type": "assistant", "message": {"content": []}}})
                handed.set()
                await task
            asyncio.run(main())
        th = threading.Thread(target=run, name="sdk:web-test", daemon=True)
        th.start()
        return th

    def test_a_hook_is_answered_while_another_thread_holds_the_registry_lock(self):
        d, be = self._be()
        s, t = self._session_and_transport(be)
        answered, handed = threading.Event(), threading.Event()
        be._reg_lock.acquire()                               # another session's long registry write, in flight
        try:
            th = self._run_session_loop(t, answered, handed)
            ok = answered.wait(3.0)
            handed_ok = handed.is_set()
        finally:
            be._reg_lock.release()
        th.join(10.0)
        self.assertTrue(handed_ok, "the record hand-over blocked the session loop on the kernel-wide registry lock")
        self.assertTrue(ok, "the hook callback was not answered while another thread held _reg_lock")

    def test_the_acknowledgement_still_lands_once_the_lock_frees(self):
        d, be = self._be()
        s, t = self._session_and_transport(be)
        answered, handed = threading.Event(), threading.Event()
        be._reg_lock.acquire()
        try:
            th = self._run_session_loop(t, answered, handed)
            answered.wait(3.0)
            self.assertNotIn("hostAck", sb.read_reg(Path(d), SID) or {}, "nothing can be written while the lock is held")
        finally:
            be._reg_lock.release()
        th.join(10.0)
        deadline = time.time() + 10.0
        ack = None
        while time.time() < deadline:
            ack = (sb.read_reg(Path(d), SID) or {}).get("hostAck")
            if ack:
                break
            time.sleep(0.02)
        self.assertEqual(ack, {"host": "4242:h1", "cli": "4343:c1", "offset": 7},
                         "the deferred acknowledgement is written with the transport's offset once the lock frees")

    def test_a_forced_acknowledgement_is_still_written_before_it_returns(self):
        """The detach path's last ack (force=True) runs as the session leaves its loop; it stays synchronous, so the
        registry names the final offset by the time the session thread moves on."""
        d, be = self._be()
        s, t = self._session_and_transport(be)
        t.ack_offset = 11
        be._write_host_ack(s, force=True)
        self.assertEqual((sb.read_reg(Path(d), SID) or {}).get("hostAck"),
                         {"host": "4242:h1", "cli": "4343:c1", "offset": 11})

    def test_a_deferred_write_never_lands_for_a_transport_that_left(self):
        """The deferred writer reads the session's CURRENT transport under the registry lock: once the session has dropped
        its host (detach, exit), an acknowledgement that waited on the lock writes nothing rather than an older offset from
        a transport that is gone (which would land over the detach's forced, final ack)."""
        d, be = self._be()
        s, t = self._session_and_transport(be)
        be._reg_lock.acquire()
        try:
            # from a thread of its own: before the fix this call blocks on the held lock
            w = threading.Thread(target=be._write_host_ack, args=(s,), daemon=True)
            w.start()
            w.join(1.0)
        finally:
            s._host = None                                    # the session left its host before the write could run
            be._reg_lock.release()
        w.join(10.0)
        _drain_writer(be)
        self.assertNotIn("hostAck", sb.read_reg(Path(d), SID) or {},
                         "a write that waited on the lock while the session left its host lands nothing stale")


def _drain_writer(be, timeout=10.0):
    """Wait for the backend's registry writer (whatever the version calls it) to finish what is queued."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        w = getattr(be, "_reg_writer", None) or getattr(be, "_host_ack_thread", None)
        if w is None:
            return
        w.join(max(0.0, deadline - time.time()))


class HooksNotBehindRegLock(unittest.TestCase):
    """Every hook romp registers that writes the registry: answered while another thread holds _reg_lock, when it runs
    on a session loop thread (the name prefix the kernel gives them), and its write lands once the lock frees."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "host": "TESTHOST", "alive": True})
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))

    def _reg(self):
        return sb.read_reg(Path(self.d), SID) or {}

    def _answer_under_held_lock(self, make_coro):
        """Hold _reg_lock, run the hook on a session-loop-named thread, and return (answered within 3 s, its answer).
        Releases the lock and drains the writer before returning, so the caller can read what landed."""
        out = {}

        def run():
            out["answer"] = asyncio.run(make_coro())
        th = threading.Thread(target=run, name=LOOP_PREFIX + "web-hooks", daemon=True)
        self.be._reg_lock.acquire()
        try:
            th.start()
            th.join(3.0)
            answered = not th.is_alive()
        finally:
            self.be._reg_lock.release()
        th.join(10.0)
        _drain_writer(self.be)
        return answered, out.get("answer")

    def test_post_tool_use_ledger_hook(self):
        inp = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_synthetic_1",
               "tool_input": {"command": "sleep 100", "run_in_background": True, "description": "a synthetic job"},
               "tool_response": {"backgroundTaskId": "bgtask-synthetic-1"}}
        answered, ans = self._answer_under_held_lock(lambda: self.s._ledger_tool_hook(inp, "toolu_synthetic_1", None))
        self.assertTrue(answered, "the PostToolUse ledger hook waited on the registry lock")
        self.assertEqual(ans, {})
        self.assertEqual([e.get("tid") for e in self._reg().get("bgLedger") or []], ["bgtask-synthetic-1"])

    def test_post_tool_use_failure_ledger_hook(self):
        launch = {"tool_name": "Bash", "tool_use_id": "toolu_synthetic_2",
                  "tool_input": {"command": "sleep 100", "run_in_background": True},
                  "tool_response": {"backgroundTaskId": "bgtask-synthetic-2"}}
        asyncio.run(self.s._ledger_tool_hook(launch, "toolu_synthetic_2", None))   # off any session loop: at once
        self.assertEqual(len(self._reg().get("bgLedger") or []), 1)
        fail = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_use_id": "toolu_synthetic_2"}
        answered, ans = self._answer_under_held_lock(lambda: self.s._ledger_fail_hook(fail, "toolu_synthetic_2", None))
        self.assertTrue(answered, "the PostToolUseFailure hook waited on the registry lock")
        self.assertEqual(ans, {})
        self.assertEqual(self._reg().get("bgLedger"), [])
        self.assertEqual([e.get("why") for e in self._reg().get("bgLedgerEnded") or []], ["launch-failed"])

    def test_stop_hook(self):
        inp = {"hook_event_name": "Stop", "session_crons": [], "background_tasks": [], "transcript_path": "/x/t.jsonl"}
        answered, ans = self._answer_under_held_lock(lambda: self.s._stop_hook(inp, None, None))
        self.assertTrue(answered, "the Stop hook waited on the registry lock")
        self.assertEqual(ans, {})
        self.assertTrue(self._reg().get("lastStopAt"), "the turn-end stamp lands once the lock frees")

    def test_schedule_tool_hook(self):
        inp = {"hook_event_name": "PostToolUse", "tool_name": "ScheduleWakeup",
               "tool_input": {"delaySeconds": 600, "prompt": "check the synthetic build", "reason": "poll"}}
        answered, ans = self._answer_under_held_lock(lambda: self.s._sched_tool_hook(inp, None, None))
        self.assertTrue(answered, "the scheduling-tool hook waited on the registry lock")
        self.assertEqual(ans, {})
        self.assertEqual([c.get("prompt") for c in self._reg().get("sessionCrons") or []], ["check the synthetic build"])

    def test_facts_tool_hook(self):
        inp = {"hook_event_name": "PostToolUse", "tool_name": "TaskCreate", "tool_input": {"subject": "a task"},
               "tool_response": {"taskId": "7"}}
        answered, ans = self._answer_under_held_lock(lambda: self.s._facts_tool_hook(inp, None, None))
        self.assertTrue(answered, "the interaction-facts hook waited on the registry lock")
        self.assertEqual(ans, {})
        self.assertEqual([w.get("taskId") for w in self._reg().get("taskWrites") or []], ["7"])

    def test_worktree_tool_hook_transcript_path(self):
        inp = {"hook_event_name": "PostToolUse", "tool_name": "EnterWorktree", "transcript_path": "/x/moved.jsonl"}
        answered, ans = self._answer_under_held_lock(lambda: self.s._worktree_tool_hook(inp, None, None))
        self.assertTrue(answered, "the worktree hook waited on the registry lock")
        self.assertEqual(ans, {})
        self.assertEqual(self._reg().get("transcriptPath"), "/x/moved.jsonl")

    def test_context_refresh(self):
        """Review 1 reproduced the context refresh (it always writes: modelPending is set on every call) leaving a hook
        unanswered while the lock was held; at the branch tip it is queued under a per-session key."""
        class FakeClient:                              # the SDK's get_context_usage control request, answered at once
            async def get_context_usage(self):
                return {"percentage": 42, "totalTokens": 84000, "model": ""}
        self.s.client = FakeClient()

        async def refresh():
            await self.s._do_refresh_context()
            return {}
        answered, ans = self._answer_under_held_lock(refresh)
        self.assertTrue(answered, "the context refresh waited on the registry lock")
        self.assertEqual(self._reg().get("liveCtx"), 42, "the refreshed context lands once the lock frees")
        self.assertEqual(self._reg().get("liveCtxTokens"), 84000)

    def test_background_task_mirror_never_writes_back_a_cleared_set(self):
        """The bgTasks mirror reads the live set when its write runs: a mirror queued while a task ran, then a clear
        (_drop_live_work), lands [] even though the earlier mirror was queued first."""
        with self.s._sub_lock:
            self.s._bg_tasks["task-synthetic"] = {"desc": "a synthetic task", "since": 1}

        async def mirror_then_drop():
            self.s._mirror_bg_tasks()                     # queued behind the held lock, the task still live
            self.s._drop_live_work("reconnect")          # clears the set, queues the [] write
            return {}
        answered, _ = self._answer_under_held_lock(mirror_then_drop)
        self.assertTrue(answered)
        self.assertEqual(self._reg().get("bgTasks"), [], "the cleared set is what lands, never the task it cleared")


class QueueMirrorOnTheLoop(HooksNotBehindRegLock):
    """_persist_queue on a session loop: written before it returns when the lock is free (a popped text leaves the disk
    queue before it is fed, as before), queued when the lock is busy, landing the queue as it stands then."""

    def _on_loop(self, fn):
        th = threading.Thread(target=fn, name=LOOP_PREFIX + "web-queue", daemon=True)
        th.start()
        th.join(3.0)
        return not th.is_alive()

    def test_lock_free_writes_before_returning(self):
        with self.s._lock:
            self.s._pending[:] = ["first synthetic text"]
            self.s._pending_meta[:] = [{}]
        seen = {}

        def persist():
            self.s._persist_queue()
            seen["queue"] = (sb.read_reg(Path(self.d), SID) or {}).get("queue")
        self.assertTrue(self._on_loop(persist))
        self.assertEqual(seen["queue"], ["first synthetic text"], "with the lock free the write is synchronous")

    def test_lock_busy_queues_and_lands_the_latest_queue(self):
        with self.s._lock:
            self.s._pending[:] = ["first synthetic text"]
            self.s._pending_meta[:] = [{}]
        self.be._reg_lock.acquire()
        try:
            self.assertTrue(self._on_loop(self.s._persist_queue), "the queue mirror waited on the registry lock")
            with self.s._lock:
                self.s._pending.append("second synthetic text")
                self.s._pending_meta.append({})
            # every real queue change persists itself, as this does: the writer may already have taken its snapshot
            # (before it waits on the lock), so a change that never persisted would race it (2 of 4 runs at 4f116788e)
            self.assertTrue(self._on_loop(self.s._persist_queue), "the queue mirror waited on the registry lock")
        finally:
            self.be._reg_lock.release()
        _drain_writer(self.be)
        self.assertEqual(self._reg().get("queue"), ["first synthetic text", "second synthetic text"])

    # the inherited hook tests run once, in HooksNotBehindRegLock
    test_post_tool_use_ledger_hook = test_post_tool_use_failure_ledger_hook = test_stop_hook = None
    test_schedule_tool_hook = test_facts_tool_hook = test_worktree_tool_hook_transcript_path = None
    test_background_task_mirror_never_writes_back_a_cleared_set = test_context_refresh = None


def _rec(off):
    return {"t": "out", "offset": off, "data": {"type": "assistant", "message": {"content": []}}}


class HostAckNeverBackwards(unittest.TestCase):
    """Review 1 of the hostAck move (must-fix, reproduced twice): the detach's forced ack was QUEUED, so a reconnect at
    once read the registry's stale offset (consumed through 9, the registry still 5), the host replayed 6 to 9, and the
    queued writes landed 9 then 6. Now the forced ack is written before the detach moves on, the old transport's last
    offset is carried in memory into the next connect, and every hostAck write is monotonic per host identity."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "alive": True,
                                         "hostAck": {"host": "4242:h1", "cli": "4343:c1", "offset": 5}})
        # a live host lease (synthetic pids; proc_start is stubbed so both read as running)
        sb.write_lease(Path(self.d), {"sid": SID, "pid": 4343, "start": "c1", "t": time.time(),
                                      "holder": {"kind": "host", "pid": 4242, "start": "h1"}})
        self._starts = {4242: "h1", 4343: "c1"}
        self._orig_start = sb.proc_start
        sb.proc_start = lambda p, run=None: self._starts.get(p)
        self.addCleanup(setattr, sb, "proc_start", self._orig_start)
        self.s = types.SimpleNamespace(sid=SID, name="web", _host=None, _host_ack_t=0.0, _host_end_grace=None,
                                       _on_cli_stderr=lambda line: None, _host_is_attach=False, _host_reexec_wait="",
                                       _host_reexec_closed=False, _host_reexec_from=None,
                                       _seed_for_dead_cli=lambda cli: None)
        self.t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 5)
        self.t1.hello = dict(HELLO)
        self.t1.replay_end = 0
        self.s._host = self.t1
        # every hostAck value the registry holds after each write, in order
        self.acks = []
        orig = self.be._update_reg_with

        def spy(sid, make_fields):
            orig(sid, make_fields)
            a = (sb.read_reg(Path(self.d), SID) or {}).get("hostAck")
            if isinstance(a, dict):
                self.acks.append(a.get("offset"))
        self.be._update_reg_with = spy

    def _reg_ack(self):
        return (sb.read_reg(Path(self.d), SID) or {}).get("hostAck") or {}

    def test_consumed_through_9_detach_reconnect_at_once_replays_nothing_and_never_moves_back(self):
        out = {}

        def loop():
            async def main():
                for off in range(6, 10):              # consumed through 9 while another thread holds the lock
                    self.s._host_ack_t = 0.0          # past the one-second throttle: every record marks a write
                    self.t1._take(_rec(off))
                self.be._write_host_ack(self.s, force=True)   # the connect loop's finally: the last ack
                self.s._host = None
                t2 = await self.be._host_transport_for(self.s, None, None)   # the reconnect, at once
                out["replay_from"] = t2.ack_offset + 1
                t2.hello = dict(HELLO)
                t2.replay_end = 10
                for off in range(t2.ack_offset + 1, 10):   # what the host replays from that offset
                    self.s._host_ack_t = 0.0
                    t2._take(_rec(off))
                out["t2"] = t2
            asyncio.run(main())
        th = threading.Thread(target=loop, name=LOOP_PREFIX + "web-mustfix", daemon=True)
        self.be._reg_lock.acquire()                   # the writer is held: another session's long registry write
        try:
            th.start()
            th.join(1.5)
        finally:
            self.be._reg_lock.release()
        th.join(10.0)
        self.assertFalse(th.is_alive())
        _drain_writer(self.be)
        self.assertEqual(out.get("replay_from"), 10, "the reconnect replayed records 6 to 9 the kernel already consumed")
        self.assertEqual(self.acks, sorted(self.acks), "hostAck moved backwards: %r" % (self.acks,))
        self.assertEqual(self._reg_ack().get("offset"), 9)

    def test_the_in_memory_carry_alone_resumes_past_a_stale_registry(self):
        """Even when the registry never got the final offset (a write that failed or had not landed), a reconnect in the
        same kernel resumes from what the old transport consumed."""
        for off in range(6, 10):
            self.s._host_ack_t = 0.0
            self.t1._take(_rec(off))
        self.be._write_host_ack(self.s, force=True)
        self.s._host = None
        reg = sb.read_reg(Path(self.d), SID)
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 5}      # the registry trails, as under the race
        sb.write_reg(Path(self.d), SID, reg)
        t2 = asyncio.run(self.be._host_transport_for(self.s, None, None))
        self.assertEqual(t2.ack_offset, 9, "the reconnect took the registry's stale 5 over the carried 9")

    def test_a_lower_offset_for_the_same_host_is_dropped_and_another_host_lands(self):
        self.be._update_reg(SID, hostAck={"host": "4242:h1", "cli": "4343:c1", "offset": 9})
        self.be._update_reg(SID, hostAck={"host": "4242:h1", "cli": "4343:c1", "offset": 6}, lastStopAt=7)
        self.assertEqual(self._reg_ack().get("offset"), 9, "a lower offset for the same host must not land")
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastStopAt"), 7, "the other fields still land")
        self.be._update_reg(SID, hostAck={"host": "5151:h2", "cli": "5252:c2", "offset": 3})
        self.assertEqual(self._reg_ack(), {"host": "5151:h2", "cli": "5252:c2", "offset": 3}, "a new host starts over")

    def test_acks_dropped_when_the_cli_dies_are_recovered_by_the_carry(self):
        """Should-fix (c): acks still queued when the CLI dies write nothing (the transport reported its exit), so the
        registry trails; the orphan road's replay then resumes from the carried offset, not the registry's."""
        reg = sb.read_reg(Path(self.d), SID)
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 2}
        sb.write_reg(Path(self.d), SID, reg)
        self.t1.ack_offset = 2

        def loop():
            for off in range(3, 40):                  # consumed through 39 while the lock is held
                self.s._host_ack_t = 0.0
                self.t1._take(_rec(off))
            self.t1.exit_info = {"t": "exit", "cause": "died", "code": -9}
            self.be._write_host_ack(self.s, force=True)   # what the finally would do; the exit makes it write nothing
            self.s._host = None                       # the connect loop's finally, the CLI gone
        th = threading.Thread(target=loop, name=LOOP_PREFIX + "web-died", daemon=True)
        self.be._reg_lock.acquire()
        try:
            th.start()
            th.join(3.0)
        finally:
            self.be._reg_lock.release()
        th.join(10.0)
        _drain_writer(self.be)
        self.assertLess(self._reg_ack().get("offset", -1), 39, "the setup expects the queued acks to be dropped")
        # the dead host's directory: its identity and a journal of records 0..39
        hdir = ht.host_dir(Path(self.d), SID)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "identity.json").write_text('{"pid": 4242, "start": "h1"}')
        with open(hdir / "journal-0.jsonl", "w") as fh:
            for off in range(40):
                fh.write('{"type": "assistant", "n": %d}\n' % off)
        replayed = []

        class FakeClient:                             # stands in for the SDK client the replay would open
            def __init__(self, options=None, transport=None):
                replayed.append(transport.ack_offset + 1)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        fake = types.ModuleType("claude_agent_sdk")
        fake.ClaudeSDKClient = FakeClient
        with unittest.mock.patch.dict(sys.modules, {"claude_agent_sdk": fake}), \
                unittest.mock.patch.object(self.be, "_replay_drain", lambda *a, **k: asyncio.sleep(0)):
            asyncio.run(self.be._host_orphan_recover(self.s, None, None, None, died=True))
        self.assertEqual(replayed, [], "records the kernel had consumed were replayed from offset %r" % (replayed,))


class RegFlushAtDrain(unittest.TestCase):
    """Should-fix (c), the shutdown half: the registry writer is a daemon thread, so work the session loops queued dies
    with the process unless the drain waits for it; _reg_flush gives it the rest of the drain's bound."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "alive": True})

    def _queue_on_loop(self, **fields):
        th = threading.Thread(target=lambda: self.be._reg_job(None, lambda: self.be._update_reg(SID, **fields)),
                              name=LOOP_PREFIX + "web-flush", daemon=True)
        th.start()
        th.join(10.0)

    def test_the_drain_waits_for_queued_writes_within_its_bound(self):
        self.be._reg_lock.acquire()
        self._queue_on_loop(lastStopAt=11)
        threading.Timer(0.3, self.be._reg_lock.release).start()     # the other writer finishes inside the bound
        left = self.be._reg_flush(5.0)
        self.assertEqual(left, 0)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastStopAt"), 11, "the queued write landed")

    def test_work_left_at_the_bound_is_named(self):
        self.be._reg_lock.acquire()
        try:
            self._queue_on_loop(lastStopAt=12)
            left = self.be._reg_flush(0.2)
        finally:
            self.be._reg_lock.release()
        _drain_writer(self.be)
        self.assertGreaterEqual(left, 1)
        self.assertTrue(any("still pending at the shutdown bound" in str(l) for l in self.logs), self.logs)


class RegWriterStart(unittest.TestCase):
    """Should-fix (a): the registry writer was stored in its slot before start(); a failed start raised into the caller
    (the record hand-over, a hook) and left the slot stuck for the kernel's life, silently."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "host": "TESTHOST", "alive": True})
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))

    def _on_loop(self, fn):
        out = {}

        def run():
            try:
                out["value"] = fn()
            except BaseException as e:
                out["error"] = e
        th = threading.Thread(target=run, name=LOOP_PREFIX + "web-start", daemon=True)
        th.start()
        th.join(10.0)
        return out

    def test_a_failing_start_still_answers_the_hook_and_writes(self):
        real_start = threading.Thread.start

        def failing_start(th):
            if th.name == "romp-reg-writer":
                raise RuntimeError("can't start new thread")
            return real_start(th)
        tr = self.be._new_host_transport(self.s, "/nonexistent.sock", -1)
        tr.hello = dict(HELLO)
        tr.replay_end = 0
        self.s._host = tr
        self.s._host_ack_t = 0.0
        inp = {"hook_event_name": "Stop", "session_crons": [], "background_tasks": [], "transcript_path": "/x/t.jsonl"}

        def body():
            data = tr._take(_rec(7))                  # the record hand-over: must not raise
            return data, asyncio.run(self.s._stop_hook(inp, None, None))
        with unittest.mock.patch.object(threading.Thread, "start", failing_start):
            out = self._on_loop(body)
            out2 = self._on_loop(lambda: self.be._reg_job(None, lambda: self.be._update_reg(SID, lastTurnOpener="x")))
        self.assertNotIn("error", out, out.get("error"))
        self.assertNotIn("error", out2, out2.get("error"))
        self.assertEqual(out["value"][1], {}, "the Stop hook is answered")
        reg = sb.read_reg(Path(self.d), SID) or {}
        self.assertEqual((reg.get("hostAck") or {}).get("offset"), 7, "the ack is written synchronously instead")
        self.assertTrue(reg.get("lastStopAt"), "the Stop hook's write lands synchronously instead")
        self.assertEqual(reg.get("lastTurnOpener"), "x")
        self.assertEqual(sum(1 for l in self.logs if "could not start" in str(l)), 1, "logged once: %r" % (self.logs,))
        self.assertIsNone(self.be._reg_writer, "no never-started thread is left in the slot")
        # the start works again: the next queued item starts a writer, so nothing is stuck
        self._on_loop(lambda: self.be._reg_job(None, lambda: self.be._update_reg(SID, lastTurnOpener="y")))
        _drain_writer(self.be)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastTurnOpener"), "y")

    def test_a_dead_thread_in_the_slot_is_replaced(self):
        self.be._reg_writer = threading.Thread(target=lambda: None, name="romp-reg-writer")   # never started
        out = self._on_loop(lambda: self.be._reg_job(None, lambda: self.be._update_reg(SID, lastTurnOpener="z")))
        self.assertNotIn("error", out, out.get("error"))
        deadline = time.time() + 10.0
        while time.time() < deadline and (sb.read_reg(Path(self.d), SID) or {}).get("lastTurnOpener") != "z":
            time.sleep(0.02)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastTurnOpener"), "z",
                         "work queued behind a dead writer was never run")


class QueuedJobsTakeValues(HooksNotBehindRegLock):
    """Review 2 of the hook move (must-fix): a queued registry job read live session state when the writer ran, not when
    the hook fired. Stop stamped the turn opener at write time, so a romp notice fed between the human's turn end and the
    write marked the turn "injected" and the phone notice was skipped; the schedule and ledger records took their clock
    and the CLI's process generation late the same way. Every moved hook now captures those values when it fires."""

    def _hold_lock_while(self, on_loop, then):
        """Hold the registry lock; run `on_loop` on a session-loop thread (it must not wait on the lock), then `then`
        (the session moving on while the write is queued); release, drain the writer."""
        self.be._reg_lock.acquire()
        try:
            th = threading.Thread(target=on_loop, name=LOOP_PREFIX + "web-capture", daemon=True)
            th.start()
            th.join(3.0)
            self.assertFalse(th.is_alive(), "the hook waited on the registry lock")
            then()
        finally:
            self.be._reg_lock.release()
        _drain_writer(self.be)

    def test_a_notice_fed_between_the_stop_hook_and_the_writer_does_not_change_what_stop_records(self):
        self.s._note_turn_opener("human", fresh=True)        # a human's turn
        inp = {"hook_event_name": "Stop", "session_crons": [], "background_tasks": [], "transcript_path": "/x/t.jsonl"}
        fired = {}

        def hook():
            fired["at"] = time.time()
            asyncio.run(self.s._stop_hook(inp, None, None))

        def notice_fed():
            time.sleep(1.1)                                   # the writer runs later than the hook, by a whole second
            self.s._note_turn_opener("injected", fresh=True)   # the result arrived; a queued romp notice opened a turn
        self._hold_lock_while(hook, notice_fed)
        reg = self._reg()
        self.assertEqual(reg.get("lastTurnOpener"), "human", "Stop recorded the opener of the turn after it")
        self.assertLessEqual(abs(reg.get("lastStopAt", 0) - int(fired["at"])), 0, "Stop's stamp is the hook's moment")

    def test_a_delete_armed_after_the_stop_hook_is_not_completed_by_that_turn_end(self):
        calls = []
        self.be._complete_rewind_wait = lambda s: calls.append(s.sid)
        inp = {"hook_event_name": "Stop", "session_crons": [], "background_tasks": []}

        def arm():
            self.s._rewind_wait = True                        # a delete-while-busy armed after this turn ended
        self._hold_lock_while(lambda: asyncio.run(self.s._stop_hook(inp, None, None)), arm)
        self.assertEqual(calls, [], "an older turn end completed a delete armed after it")

    def test_the_ledger_and_schedule_records_keep_the_hooks_clock_and_process_generation(self):
        launch = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_synthetic_9",
                  "tool_input": {"command": "sleep 100", "run_in_background": True},
                  "tool_response": {"backgroundTaskId": "bgtask-synthetic-9", "stdout": "x" * 100_000}}
        wake = {"hook_event_name": "PostToolUse", "tool_name": "ScheduleWakeup",
                "tool_input": {"delaySeconds": 600, "prompt": "check the synthetic build", "reason": "poll"}}
        gen0, fired = self.s.proc_gen, {}

        def hooks():
            fired["at"] = time.time()
            asyncio.run(self.s._ledger_tool_hook(launch, "toolu_synthetic_9", None))
            asyncio.run(self.s._sched_tool_hook(wake, None, None))
            held = [fn for fn in self.be._reg_jobs.values()]
            fired["closure"] = repr([c.cell_contents for fn in held for c in (fn.__closure__ or ())])

        def moved_on():
            time.sleep(1.1)
            self.s.proc_gen = "a-later-cli-generation"         # the CLI was replaced before the writer ran
        self._hold_lock_while(hooks, moved_on)
        entry = (self._reg().get("bgLedger") or [{}])[0]
        self.assertEqual(entry.get("procGen"), gen0)
        self.assertEqual(entry.get("armedAt"), int(fired["at"]))
        cron = (self._reg().get("sessionCrons") or [{}])[0]
        self.assertEqual(cron.get("procGen"), gen0)
        self.assertAlmostEqual(cron.get("dueEpoch"), fired["at"] + 600, delta=0.5)
        self.assertNotIn("x" * 1000, fired["closure"], "a queued job held the whole Bash output")

    def test_a_foreground_bash_queues_nothing(self):
        fg = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "toolu_synthetic_10",
              "tool_input": {"command": "ls"}, "tool_response": {"stdout": "x" * 100_000}}
        seen = {}

        def hook():
            asyncio.run(self.s._ledger_tool_hook(fg, "toolu_synthetic_10", None))
            seen["queued"] = len(self.be._reg_jobs)
        self._hold_lock_while(hook, lambda: None)
        self.assertEqual(seen["queued"], 0)

    def test_a_requeued_key_moves_to_the_back(self):
        """Review 2 (should-fix): a re-queued key kept its old place, so its newer value ran ahead of work queued after
        the older one; it now takes the back of the line."""
        order = []

        def queue():
            self.be._reg_job(("k1", SID), lambda: order.append("k1-old"))
            self.be._reg_job(("k2", SID), lambda: order.append("k2"))
            self.be._reg_job(("k1", SID), lambda: order.append("k1-new"))
        self._hold_lock_while(queue, lambda: None)
        # the writer may have popped k1-old before the lock mattered (these jobs take no lock); what may never happen is
        # the newer k1 running ahead of k2
        self.assertIn(order, (["k2", "k1-new"], ["k1-old", "k2", "k1-new"]), order)

    def test_a_model_pick_written_while_a_refresh_is_queued_is_not_undone(self):
        """Review 2 (should-fix): the context refresh's queued write carried modelPending from queue time and landed an
        old False over a newer pick's True. The mirror is read when the write runs."""
        class FakeClient:
            async def get_context_usage(self):
                return {"percentage": 10, "totalTokens": 1000, "model": ""}
        self.s.client = FakeClient()
        self.s._model_pending = ""

        def pick():                                            # what set_model does: the field, then its own write
            self.s._model_pending = "opus"
            reg = sb.read_reg(Path(self.d), SID)
            reg["modelPending"] = True
            sb.write_reg(Path(self.d), SID, reg)              # landed while the refresh's write waited
        self._hold_lock_while(lambda: asyncio.run(self.s._do_refresh_context()), pick)
        self.assertIs(self._reg().get("modelPending"), True, "the queued refresh undid a newer model pick")

    def test_resolving_a_model_switch_does_not_wait_on_the_lock(self):
        self.s._model_pending = "opus"
        out = {}
        self._hold_lock_while(lambda: out.setdefault("cleared", self.s._resolve_model_pending("Opus 4.8")), lambda: None)
        self.assertTrue(out["cleared"])
        self.assertIs(self._reg().get("modelPending"), False)

    # the inherited hook tests run once, in HooksNotBehindRegLock
    test_post_tool_use_ledger_hook = test_post_tool_use_failure_ledger_hook = test_stop_hook = None
    test_schedule_tool_hook = test_facts_tool_hook = test_worktree_tool_hook_transcript_path = None
    test_background_task_mirror_never_writes_back_a_cleared_set = test_context_refresh = None


class RegWriterReview2(unittest.TestCase):
    """Review 2 (should-fix): the writer could stop for good silently, and the queue could grow without a word."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "alive": True})

    def _on_loop(self, fn):
        th = threading.Thread(target=fn, name=LOOP_PREFIX + "web-r2", daemon=True)
        th.start()
        th.join(10.0)

    def test_a_writer_killed_by_an_unexpected_error_says_so_and_the_next_write_starts_a_fresh_one(self):
        class Fatal(BaseException):
            pass

        def boom():
            raise Fatal()
        hook = threading.excepthook
        threading.excepthook = lambda args: None             # the writer's death is the point; keep it off the run's report
        self.addCleanup(setattr, threading, "excepthook", hook)
        self._on_loop(lambda: self.be._reg_job(None, boom))
        deadline = time.time() + 5
        while time.time() < deadline and self.be._reg_writer is not None:
            time.sleep(0.02)
        self.assertTrue(any("stopped on an unexpected error" in str(l) for l in self.logs), self.logs)
        self._on_loop(lambda: self.be._reg_job(None, lambda: self.be._update_reg(SID, lastTurnOpener="after")))
        _drain_writer(self.be)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastTurnOpener"), "after")

    def test_a_long_queue_is_logged_once(self):
        orig = sb.REG_JOBS_WARN
        sb.REG_JOBS_WARN = 5
        self.addCleanup(setattr, sb, "REG_JOBS_WARN", orig)
        self.be._reg_lock.acquire()
        try:
            self._on_loop(lambda: [self.be._reg_job(None, lambda i=i: self.be._update_reg(SID, n=i)) for i in range(12)])
        finally:
            self.be._reg_lock.release()
        _drain_writer(self.be)
        self.assertEqual(sum(1 for l in self.logs if "registry writes queued" in str(l)), 1, self.logs)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("n"), 11)


class SessionEndFlushesItsQueue(unittest.TestCase):
    """Review 3: every path where a session ends runs that session's queued registry jobs first, inline and in order,
    and writes synchronously after them. Before, a save the dying thread queued (its name is still sdk:) could land after
    the crash heal or the task-death notice and overwrite them, and a CLI death left the consumed offset only in memory."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "session-hosts").write_text("off")
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "host": "TESTHOST", "alive": True})
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.ensured = []
        self.be._ensure = lambda sid, *a, **k: self.ensured.append(list(self._reg(sid).get("queue") or []))

    def _reg(self, sid=SID):
        return sb.read_reg(Path(self.d), sid) or {}

    def _on_loop(self, fn, wait=10.0):
        th = threading.Thread(target=fn, name=LOOP_PREFIX + "web-end", daemon=True)
        th.start()
        th.join(wait)
        return th

    def _gate(self):
        """Park the writer on another piece of work (no session, no lock) so this session's jobs stay queued."""
        ev = threading.Event()
        self._on_loop(lambda: self.be._reg_job(None, lambda: ev.wait(10)))
        self.addCleanup(ev.set)
        return ev

    def test_the_held_text_survives_a_crash_heal_under_a_busy_lock(self):
        new = {}
        self.be._ensure = lambda sid, *a, **k: new.setdefault("s", sb.SdkSession(self.be, self._reg(sid)))
        with self.s._lock:
            self.s._pending[:] = ["held synthetic text"]       # put back by the hold's release at the CLI's death
            self.s._pending_meta[:] = [{}]
        ev = self._gate()
        self.be._reg_lock.acquire()
        try:
            self._on_loop(self.s._persist_queue)              # the release's save on the dying sdk: thread: queued
        finally:
            self.be._reg_lock.release()
        self.s.inflight = 1
        self.be._heal_cut_session(self.s)
        ev.set()
        _drain_writer(self.be)
        ns = new["s"]
        with ns._lock:
            mem = list(ns._pending)
        self.assertEqual(mem, [sb.CRASH_RESUME_NUDGE, "held synthetic text"], "the new session lost the held text")
        ns._persist_queue()
        self.assertEqual(self._reg().get("queue"), [sb.CRASH_RESUME_NUDGE, "held synthetic text"])

    def test_the_task_death_clear_survives_a_mirror_the_dying_session_queued(self):
        with self.s._sub_lock:
            self.s._bg_tasks["task-synthetic"] = {"desc": "a synthetic watcher", "since": 1, "type": "local_bash"}
        ev = self._gate()
        self._on_loop(self.s._mirror_bg_tasks)               # a task event just before the CLI died: queued
        self.s.inflight = 0
        self.be._on_session_gone(self.s)
        ev.set()
        _drain_writer(self.be)
        self.assertEqual(self._reg().get("bgTasks"), [], "the queued mirror wrote the reported dead task back")
        notes = [t for t in self._reg().get("queue") or [] if "synthetic watcher" in t]
        self.assertEqual(len(notes), 1, "the death is reported once")

    def test_a_restart_after_a_cli_death_does_not_replay_consumed_records(self):
        """Records 3 to 39 consumed while another thread holds the lock, the CLI dies, the session leaves its host, the
        kernel restarts before the next connect: the next kernel (no carry) replays nothing already consumed."""
        reg = self._reg()
        reg["hostAck"] = {"host": "4242:h1", "cli": "4343:c1", "offset": 2}
        sb.write_reg(Path(self.d), SID, reg)
        t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 2)
        t1.hello = dict(HELLO)
        t1.replay_end = 0
        self.s._host = t1

        def loop():
            for off in range(3, 40):
                self.s._host_ack_t = 0.0
                t1._take(_rec(off))
            t1.exit_info = {"t": "exit", "cause": "died", "code": -9}
            leave = getattr(self.s, "_leave_host", None)       # the connect loop's finally (inline before it existed)
            if leave is not None:
                leave()
            else:
                if self.s._host is not None and getattr(self.s._host, "exit_info", None) is None:
                    self.be._write_host_ack(self.s, force=True)
                self.s._host = None
        self.be._reg_lock.acquire()
        try:
            th = threading.Thread(target=loop, name=LOOP_PREFIX + "web-died3", daemon=True)
            th.start()
            th.join(1.0)
        finally:
            self.be._reg_lock.release()
        th.join(10.0)
        self.be._reg_flush(5.0)
        self.assertEqual(self._reg().get("hostAck", {}).get("offset"), 39, "the departed host's final offset was not written")
        be2 = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)   # the next kernel
        hdir = ht.host_dir(Path(self.d), SID)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "identity.json").write_text('{"pid": 4242, "start": "h1"}')
        with open(hdir / "journal-0.jsonl", "w") as fh:
            for off in range(45):                              # 40 to 44 were never consumed: they must replay
                fh.write('{"type": "assistant", "n": %d}\n' % off)
        replayed = []

        class FakeClient:
            def __init__(self, options=None, transport=None):
                replayed.append(transport.ack_offset + 1)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False
        fake = types.ModuleType("claude_agent_sdk")
        fake.ClaudeSDKClient = FakeClient
        s2 = types.SimpleNamespace(sid=SID, name="web", _host=None, _seed_for_dead_cli=lambda cli: None)
        with unittest.mock.patch.dict(sys.modules, {"claude_agent_sdk": fake}), \
                unittest.mock.patch.object(be2, "_replay_drain", lambda *a, **k: asyncio.sleep(0)):
            asyncio.run(be2._host_orphan_recover(s2, None, None, None, died=False))
        self.assertEqual(replayed, [40], "the next kernel replayed from %r, not from the first unconsumed record" % replayed)

    def test_a_clean_end_writes_no_departed_offset(self):
        t1 = self.be._new_host_transport(self.s, "/nonexistent.sock", 2)
        t1.hello = dict(HELLO)
        t1.ack_offset = 9
        t1.exit_info = {"t": "exit", "cause": "end"}
        self.s._host = t1
        self._on_loop(self.s._leave_host)
        self.assertNotIn("hostAck", self._reg(), "a clean end drops hostAck; nothing may write it back")

    def test_the_session_threads_end_leaves_no_queued_job_of_its_session(self):
        """The real exit path (_run's finally, with the connect loop stubbed out): whatever the session had queued runs
        before the end's writes, and nothing of it is left in the queue; another session's work is untouched."""
        other = "5e1f0a77-2222-4333-8444-0000000000c2"
        ev = self._gate()
        order = []

        def queue_some():
            self.be._reg_job(("stopRecord", SID), lambda: order.append("stop-record"))
            self.be._reg_job(("transcriptPath", SID), lambda: order.append("transcript"))
            self.be._reg_job(("queue", other), lambda: order.append("other-session"))
        self._on_loop(queue_some)
        gone = []
        orig_gone = self.be._on_session_gone
        self.be._on_session_gone = lambda sess: (gone.append(list(order)), orig_gone(sess))

        async def no_connect():
            return None
        self.s._amain = no_connect
        th = self._on_loop(self.s._run)
        self.assertFalse(th.is_alive())
        left = [k for k in self.be._reg_jobs if isinstance(k, tuple) and len(k) == 2 and k[1] == SID]
        self.assertEqual(left, [], "the session's end left its queued jobs behind")
        self.assertEqual(gone, [["stop-record", "transcript"]], "the session's jobs ran, in order, before its end's writes")
        self.assertIn(("queue", other), self.be._reg_jobs, "another session's queued work is left to the writer")
        ev.set()
        _drain_writer(self.be)
        self.assertEqual(order, ["stop-record", "transcript", "other-session"])

    def test_a_queued_refresh_never_lands_an_older_model_name_over_a_newer_learned_one(self):
        class FakeClient:
            async def get_context_usage(self):
                return {"percentage": 40, "totalTokens": 80000, "model": "claude-sonnet-4-5"}
        self.s.client = FakeClient()
        ev = self._gate()
        self._on_loop(lambda: asyncio.run(self.s._do_refresh_context()))
        self._on_loop(lambda: self.s._learn_model(sb.pretty_model("claude-opus-4-1"), raw="claude-opus-4-1"))
        ev.set()
        _drain_writer(self.be)
        self.assertEqual(self._reg().get("liveModel"), self.s.model, "a queued refresh landed an older model name")

    def test_the_taken_flag_set_during_the_writers_read_is_never_lost(self):
        """The writer's read-and-clear of the postal-taken flag and the loop's set are each atomic (review 3): a set that
        lands between the writer's read and its clear used to be wiped, and the taken mail ids were never written, so a
        kernel death could deliver that mail again."""
        with self.s._lock:
            self.s._pending[:] = ["a synthetic banner"]
            self.s._pending_meta[:] = [{}]
        self.s._postal_taken = ["mid-synthetic-1"]
        sess, be, loops = self.s, self.be, []

        def loop_sets_flag():                                  # the loop's lock-busy persist with taken=True
            be._reg_lock.acquire()
            try:
                th = threading.Thread(target=lambda: sess._persist_queue(taken=True), name=LOOP_PREFIX + "web-taken",
                                      daemon=True)
                th.start()
                th.join(0.5)                                   # with the fix it waits for the flag's lock here
                loops.append(th)
            finally:
                be._reg_lock.release()

        class Racing(type(sess)):
            @property
            def _persist_taken_due(self):
                v = self.__dict__.get("_ptd", False)
                if not loops:                                  # the loop runs between the writer's read and its clear
                    loop_sets_flag()
                return v

            @_persist_taken_due.setter
            def _persist_taken_due(self, v):
                self.__dict__["_ptd"] = v
        sess.__class__ = Racing
        sess._persist_queue_due()                              # the writer's queued persist, reading the flag
        for th in loops:
            th.join(10.0)
        _drain_writer(be)
        self.assertEqual(self._reg().get("postalTaken"), ["mid-synthetic-1"], "the loop's taken flag was lost")


class SessionLoopRegLockGuard(unittest.TestCase):
    """The test-time rule: a session loop thread that takes the registry lock raises (ROMP_REG_LOCK_GUARD=raise, which
    tests/conftest.py sets for the suite), so a hook or handler that writes the registry on the loop fails its test."""

    def setUp(self):
        self._before = os.environ.get(sb.REG_LOCK_GUARD_ENV)
        self._log_before = os.environ.get(sb.REG_LOCK_GUARD_LOG_ENV)
        os.environ[sb.REG_LOCK_GUARD_ENV] = "raise"
        self.addCleanup(self._restore)
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        # these tests take the lock on a loop ON PURPOSE: their hits go to a private file, never the run's
        self.hits = os.path.join(self.d, "hits.tsv")
        os.environ[sb.REG_LOCK_GUARD_LOG_ENV] = self.hits
        Path(self.d, "session-hosts").write_text("off")
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)

    def _restore(self):
        for name, before in ((sb.REG_LOCK_GUARD_ENV, self._before), (sb.REG_LOCK_GUARD_LOG_ENV, self._log_before)):
            if before is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = before

    def _on_thread(self, name, fn):
        out = {}

        def run():
            try:
                fn()
                out["ok"] = True
            except Exception as e:
                out["error"] = e
        th = threading.Thread(target=run, name=name, daemon=True)
        th.start()
        th.join(10.0)
        return out

    def test_a_registry_write_on_a_session_loop_raises(self):
        out = self._on_thread(LOOP_PREFIX + "web", lambda: self.be._update_reg(SID, hostAck={"offset": 1}))
        self.assertIsInstance(out.get("error"), sb.RegLockOnSessionLoop)
        self.assertNotIn("hostAck", sb.read_reg(Path(self.d), SID) or {})
        self.assertTrue(Path(self.hits).read_text().strip(), "the hit is on file too, so a swallowed raise still fails the run")

    def test_an_owed_writer_is_let_through_and_a_try_never_raises(self):
        """The writers named in REG_LOCK_LOOP_WRITERS_OWED pass (a name per writer still to move), and a non-blocking
        try (the queue mirror's) never waits, so it is allowed on a loop."""
        self.assertIn("_persist_cost_state", sb.REG_LOCK_LOOP_WRITERS_OWED)
        self.assertNotIn("_ledger_tool_apply", sb.REG_LOCK_LOOP_WRITERS_OWED)
        out = self._on_thread(LOOP_PREFIX + "web", lambda: self.be._update_reg_try(SID, lastStopAt=3))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastStopAt"), 3)

    def test_the_same_write_elsewhere_is_allowed(self):
        out = self._on_thread("romp-test-worker", lambda: self.be._update_reg(SID, hostAck={"offset": 1}))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("hostAck"), {"offset": 1})

    def test_queued_work_from_a_session_loop_does_not_raise(self):
        out = self._on_thread(LOOP_PREFIX + "web",
                              lambda: self.be._reg_job(None, lambda: self.be._update_reg(SID, lastStopAt=5)))
        self.assertTrue(out.get("ok"), out)
        _drain_writer(self.be)
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("lastStopAt"), 5)


if __name__ == "__main__":
    unittest.main()
