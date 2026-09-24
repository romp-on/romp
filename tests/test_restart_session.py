#!/usr/bin/env python3
"""Restart session (the user 2026-09-23): ONE action that relaunches a session's own CLI process in place.

A session keeps the Claude Code binary it launched with, so a long-running one cannot reach a model only a
newer CLI knows — its alias resolves to the old model and the new id is refused as unrecognized. The only
in-product way onto the new binary was End session then Revive: two destructive-looking steps, through a
confirm dialog, for something that destroys nothing.

What this pins, on both sides of the backend seam:

  * SdkBackend.relaunch, the primitive. It rides request_reconnect — the road /effort, per-session env and
    the billing switch already take to apply a connect-time change, whose run loop leaves its
    ClaudeSDKClient and re-enters it, spawning a FRESH CLI from the `claude` path the kernel resolved. So
    the registry row is never flipped dead, nothing is killed, no death record is written, and the session
    is live throughout: no surface can paint it dead on the way through. A RUNNING turn is cut — the
    reconnect is armed BEFORE the interrupt, so the arm exists before the interrupted turn's result fires
    it, and the cut is the polite request alone, once per stop episode, never Stop's signals, not even when
    that request fails — while a merely queued one is not cut at all. A dormant row has no process to replace and is simply
    connected. A sid romp has no record of, and a row that is not alive, refuse in words the user reads.

  * _restart_session, the door behind the WS restartSession op. It hands the work to the OWNING backend
    (nothing here duplicates the revive's resume), runs off the recv loop, and answers the pane that asked
    in the window that asked: `restarted` on success, `restartFailed` carrying the reason otherwise. It
    never focuses anything (the tab you are looking at is yours), never records a death, and leaves the
    names registry and the registry row exactly as it found them.

Every leg reds at the merge base on the behaviour, not on a missing name: the two doors do not exist there,
so the tests say so through the AttributeError the getattr guards raise as an explicit failure. Synthetic
fixtures only: a hermetic state root, private placeholder sids, the notes-api demo names.
"""
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_CLI_SCOPE"] = "0"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_restart", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_restart", os.path.join(BIN, "romp_sdk_backend.py"))
cb = load_source("romp_codex_backend_restart", os.path.join(ROOT, "kernel", "codex_backend.py"))

SID = "3a7c0001-2222-3333-4444-555555555555"      # a running session named web (this module's own sid)
OTHER = "3a7c0002-2222-3333-4444-555555555555"    # one this kernel has never heard of


def _backend(d):
    # per-session hosts OFF in this bare state root (they are on by default, T348): nothing here means to
    # start a real bin/romp-session-host, and relaunch's dormant road calls the real connect()
    Path(d, "session-hosts").write_text("off")
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)


def _reg(d, sid=SID, **extra):
    r = {"sid": sid, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": sid, "effort": "high"}
    r.update(extra)
    sb.write_reg(Path(d), sid, r)
    return r


class _Sess:
    """The SdkSession as relaunch touches it: the lock and the counter it reads, and the two calls it makes,
    recorded in the order they land (the order IS the contract — the arm before the cut)."""

    def __init__(self, calls, inflight=0, name="web"):
        self.calls, self.inflight, self.name, self.sid = calls, inflight, name, SID
        self._lock = threading.RLock()

    def request_reconnect(self, defer=True):
        self.calls.append(("request_reconnect", defer))

    def interrupt(self, climb=True):
        self.calls.append(("interrupt",) if climb else ("interrupt", "polite only"))
        return True


class RelaunchPrimitive(unittest.TestCase):
    """SdkBackend.relaunch: what it does to the process, and what it deliberately leaves alone."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.calls = []

    def relaunch(self, sid=SID):
        fn = getattr(self.be, "relaunch", None)
        self.assertIsNotNone(fn, "SdkBackend has no relaunch: Restart session has no primitive to ride")
        return fn(sid)

    def test_an_idle_session_reconnects_and_nothing_is_cut(self):
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls)
        self.assertEqual(self.relaunch(), "", "an idle running session relaunches")
        self.assertEqual(self.calls, [("request_reconnect", True)], "the reconnect road, and no interrupt")

    def test_a_running_turn_is_cut_with_the_reconnect_armed_first(self):
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls, inflight=1)
        self.assertEqual(self.relaunch(), "")
        self.assertEqual(self.calls, [("request_reconnect", True), ("interrupt", "polite only")],
                         "armed before the cut: the interrupted turn's result must find the arm standing")

    def test_a_queued_turn_with_nothing_running_is_not_interrupted(self):
        # busy() also answers True for a turn merely QUEUED; there is nothing running to cut there, and the
        # armed reconnect fires at the next turn's end
        _reg(self.d)
        s = _Sess(self.calls)
        s._pending = ["please pick the search work back up"]
        self.be.sessions[SID] = s
        self.assertEqual(self.relaunch(), "")
        self.assertEqual(self.calls, [("request_reconnect", True)])

    def test_the_session_is_never_marked_dead_and_no_death_is_recorded(self):
        # the whole point: End + Revive reached the same place by killing the session first, and every
        # surface saw it dead on the way through
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls, inflight=1)
        self.relaunch()
        reg = sb.read_reg(Path(self.d), SID)
        self.assertEqual((reg.get("alive"), reg.get("name"), reg.get("lastSid"), reg.get("effort")),
                         (True, "web", SID, "high"), "the row is untouched: alive, named, on its newest transcript")
        self.assertIn(SID, self.be.sessions, "and the session object is still the backend's")

    def test_a_dormant_row_has_no_process_to_replace_and_is_simply_connected(self):
        _reg(self.d)
        connects = []
        self.be.connect = lambda sid: (connects.append(sid) or True)
        self.assertEqual(self.relaunch(), "")
        self.assertEqual(connects, [SID], "connect() starts a fresh CLI, which is the same outcome by the shortest road")

    def test_a_dormant_row_whose_cli_will_not_start_says_so(self):
        _reg(self.d)
        self.be.connect = lambda sid: False
        self.assertIn("did not start", self.relaunch())

    def test_a_row_that_is_not_alive_refuses_and_points_at_the_revive(self):
        _reg(self.d, alive=False)
        r = self.relaunch()
        self.assertIn("not running", r)
        self.assertIn("revive", r)
        self.assertEqual(self.calls, [])

    def test_a_sid_with_no_record_refuses(self):
        self.assertIn("no record of this session", self.relaunch(OTHER))

    def test_a_backend_with_no_relaunch_primitive_refuses_in_words_for_no_backend_in_particular(self):
        # the ABC's default (session_backend.move's shape), which the unowned route and any future backend
        # inherit — so a backend never reads as relaunchable by omission
        self.assertIn("no way to relaunch", km._UNOWNED.relaunch(SID))
        self.assertIn("revive", km._UNOWNED.relaunch(SID))


class _Loop:
    """The session's event loop as interrupt() touches it: what it schedules is counted, never run."""

    def __init__(self):
        self.scheduled = 0

    def call_soon_threadsafe(self, fn):
        self.scheduled += 1


class RelaunchNeverClimbsTheStopLadder(unittest.TestCase):
    """A Restart asks a running turn to stop once, politely, and never signals the CLI (the post-merge review of
    #2059, 2026-09-24). relaunch rode Stop's interrupt, which climbs control request, SIGINT, SIGKILL on each
    press: clicking Restart again on a CLI that ignored the request killed it on the third click, or on the
    second after an unsettled Stop, and the armed reconnect waits for a result that then never comes, so the
    session was left with no CLI. A real SdkSession, its channel up and its turn ignoring every request; the
    signal rung is recorded instead of sent."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        _reg(self.d)
        s = self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = s
        s.inflight = 1
        s.loop, s.client = _Loop(), object()
        self.signals = []
        s._signal_cli = lambda sig, action: self.signals.append(action)
        s.request_reconnect = lambda defer=True: None      # the arm; RelaunchPrimitive pins its order

    def test_repeated_restarts_send_one_polite_request_and_no_signal(self):
        for _ in range(3):
            self.assertEqual(self.be.relaunch(SID), "")
        self.assertEqual(self.signals, [], "a Restart never sends SIGINT or SIGKILL")
        self.assertEqual(self.s.loop.scheduled, 1, "one control request for the episode, not one per click")

    def test_a_restart_after_an_unsettled_stop_sends_nothing_more(self):
        self.s.interrupt()                                  # Stop: the polite request, which the CLI ignores
        for _ in range(2):
            self.assertEqual(self.be.relaunch(SID), "")
        self.assertEqual((self.signals, self.s.loop.scheduled), ([], 1))

    def test_stop_still_climbs_past_a_restarts_request(self):
        # the ladder is Stop's and stays (terminal parity): a Restart's request is the episode's polite rung, so
        # the next Stop signals, and a Restart that sent nothing moved no rung: SIGINT still comes before SIGKILL
        for _ in range(2):
            self.assertEqual(self.be.relaunch(SID), "")
        self.s.interrupt()
        self.s.interrupt()
        self.assertEqual(self.signals, ["sigint", "sigkill"])


def _timed_out():
    """No answer within the SDK's timeout: a bare Exception raised from TimeoutError (see _cli_refusal)."""
    e = Exception("Control request timeout: interrupt")
    e.__cause__ = TimeoutError()
    return e


def _refused():
    """The CLI's error answer, which the SDK raises at once as a bare Exception carrying the CLI's text."""
    return Exception("no current client")


def _disconnected():
    """A client whose CLI connection is gone: the SDK raises its typed CLIConnectionError."""
    return type("CLIConnectionError", (Exception,), {})("the CLI's stdin is closed")


class _FailingControl:
    """The SDK client as _do_interrupt awaits it: every control request fails, after the gate when one is given
    (a request still waiting on its answer)."""

    def __init__(self, failure, gate=None):
        self.failure, self.gate, self.requests = failure, gate, 0

    async def interrupt(self):
        self.requests += 1
        if self.gate is not None:
            await asyncio.get_running_loop().run_in_executor(None, self.gate.wait, 10)
        raise self.failure()


class ARestartWhoseRequestFailsSendsNoSignal(unittest.TestCase):
    """A Restart's polite request that FAILS sends no signal either (the review of #2138, 2026-09-24). The request
    ran through _do_interrupt, whose failure branch is Stop's: with a turn in flight it SIGINTs the CLI on that
    same press and raises the rung to 2. So one Restart of a CLI whose request timed out, was refused, or met a
    closed connection signaled it, against "never a signal", and if the CLI survived that signal the next Stop
    sent SIGKILL. The SIGINT makes the installed CLI exit without running a text it has already taken, and with
    one held the armed reconnect never fires: the session was left with no CLI. The ladder cases above cannot
    reach that branch, since their loop never runs what it schedules; here a real event loop runs the request,
    the client fails it the ways the SDK fails, and the signal rung is recorded instead of sent. A shutdown's
    request, which says nothing about a Restart, still escalates."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.lines = []
        self.be._log_cb = self.lines.append
        _reg(self.d)
        s = self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = s
        s.inflight = 1
        self.signals = []
        s._signal_cli = lambda sig, action: self.signals.append(action)
        s.request_reconnect = lambda defer=True: None      # the arm; RelaunchPrimitive pins its order
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="restart-test-loop", daemon=True)
        self.thread.start()
        s.loop = self.loop
        self.addCleanup(self._close_loop)

    def _close_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()

    def _settled(self, failures):
        """Wait out the requests sent: each has failed and logged, the loop step that failed it has finished, and
        any signal thread that step started has run."""
        deadline = time.monotonic() + 10
        while sum("control request failed" in m for m in self.lines) < failures:
            if time.monotonic() > deadline:
                self.fail("the control request never failed; log %r" % self.lines[-6:])
            time.sleep(0.01)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.loop).result(timeout=10)
        for t in threading.enumerate():
            if t.name == "sdk-intr:web":
                t.join(timeout=10)

    def _restart_fails(self, failure):
        self.s.client = _FailingControl(failure)
        self.assertEqual(self.be.relaunch(SID), "")
        self._settled(1)
        self.assertEqual(self.s.client.requests, 1, "the polite request went out")
        self.assertEqual(self.signals, [], "a Restart never signals, not even when its request fails")
        self.assertEqual(self.s._intr_level, 1, "the rung stays at the polite one, so Stop's next press is SIGINT")
        self.assertTrue(any("restart" in m and "no signal" in m for m in self.lines),
                        "the log says what the failure led to; log %r" % self.lines[-6:])

    def test_a_restart_whose_request_times_out_sends_no_signal(self):
        self._restart_fails(_timed_out)

    def test_a_restart_whose_request_the_cli_refuses_at_once_sends_no_signal(self):
        self._restart_fails(_refused)

    def test_a_restart_whose_connection_is_gone_sends_no_signal(self):
        self._restart_fails(_disconnected)

    def test_stop_after_that_restart_sends_sigint_not_sigkill(self):
        self.s.client = _FailingControl(_timed_out)
        self.assertEqual(self.be.relaunch(SID), "")
        self._settled(1)
        self.assertTrue(self.be.interrupt(SID))             # Stop: the episode's polite rung is spent
        self.assertEqual(self.signals, ["sigint"], "Stop climbs from the polite rung: SIGINT before any SIGKILL")

    def test_a_stops_own_failed_request_still_signals_on_that_press(self):
        # the escalation is Stop's and stays (terminal parity, 2026-07-10): a turn in flight that the polite
        # channel could not stop gets SIGINT on the same press
        self.s.client = _FailingControl(_refused)
        self.assertTrue(self.be.interrupt(SID))
        self._settled(1)
        self.assertEqual((self.signals, self.s._intr_level), (["sigint-auto"], 2))

    def test_a_restart_while_a_stops_request_is_pending_leaves_that_stops_escalation_alone(self):
        # the no-signal rule belongs to the Restart's own request, not to the session: a Restart clicked while
        # Stop's request waits sends nothing, and that request's failure still escalates on Stop's press
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.s.client = _FailingControl(_timed_out, gate)
        self.assertTrue(self.be.interrupt(SID))             # Stop: its request goes out and waits
        self.assertEqual(self.be.relaunch(SID), "")         # Restart: the polite rung is spent, nothing is sent
        gate.set()                                          # Stop's request times out
        self._settled(1)
        self.assertEqual(self.s.client.requests, 1)
        self.assertEqual(self.signals, ["sigint-auto"])

    def test_a_shutdowns_failed_request_still_signals_since_climb_defaults_to_true(self):
        # shutdown() schedules _do_interrupt() with no argument, so the default is the whole of what keeps its
        # failed request escalating: a turn in flight that the polite channel could not stop still gets SIGINT
        self.s.client = _FailingControl(_refused)
        self.s.shutdown()
        self._settled(1)
        self.assertEqual(self.s.client.requests, 1, "shutdown sent the polite request")
        self.assertEqual((self.signals, self.s._intr_level), (["sigint-auto"], 2),
                         "a request that does not say it is a Restart's escalates: climb defaults to True")


class _Be:
    """The owning backend as the door touches it."""

    def __init__(self, answer="", entered=None, gate=None):
        self.answer, self.entered, self.gate, self.calls, self.threads = answer, entered, gate, [], []

    def relaunch(self, sid):
        self.calls.append(sid)
        self.threads.append(threading.current_thread())
        if self.entered is not None:
            self.entered.set()
            self.gate.wait(timeout=10)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class RestartDoor(unittest.TestCase):
    """_restart_session and the WS op behind it."""

    def patch(self, name, value):
        saved = getattr(km, name)
        setattr(km, name, value)
        self.addCleanup(setattr, km, name, saved)

    def setUp(self):
        self.sent = []
        self.patch("_send_to_view", lambda app, msg, wid: self.sent.append((app, msg, wid)))
        self.patch("_push_soon", lambda: None)
        self.reveals = []
        self.patch("_reveal_chat_for", lambda client, msg: self.reveals.append((client, msg)))
        self.patch("_name_of", lambda sid: "web" if sid == SID else None)
        self.patch("_kernel_knows", lambda sid: sid == SID)
        self.be = _Be()
        saved = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.addCleanup(setattr, km.Sessions, "backend_for", saved)
        self.client = {"app": "chat", "wid": "win-A"}
        self.door = getattr(km, "_restart_session", None)
        self.assertIsNotNone(self.door, "the kernel has no _restart_session door")

    def frames(self):
        return [(app, m["type"], m["id"], wid) for app, m, wid in self.sent]

    def test_a_restart_relaunches_through_the_owning_backend_and_answers_the_asking_pane(self):
        self.door(SID, self.client)
        self.assertEqual(self.be.calls, [SID], "the backend that owns the sid does the work")
        self.assertEqual(self.frames(), [("chat", "restarted", SID, "win-A")],
                         "the pane that asked, in the window that asked, and nowhere else")
        self.assertEqual(self.sent[0][1]["name"], "web", "named, as every answer about a session is")
        self.assertEqual(self.reveals, [], "a restart never moves the focus: the tab you are looking at is yours")

    def test_the_Sessions_panes_own_ask_is_answered_there(self):
        self.door(SID, {"app": "fleet", "wid": "win-B"})
        self.assertEqual(self.frames(), [("fleet", "restarted", SID, "win-B")])

    def test_an_asker_with_no_pane_of_ours_is_answered_in_both_that_offer_the_row(self):
        self.door(SID, {"app": "timeline", "wid": "win-C"})
        self.assertEqual([a for a, _, _, _ in self.frames()], ["chat", "fleet"])

    def test_a_backend_refusal_reaches_the_asker_verbatim(self):
        self.be.answer = "this session is not running — revive it to bring it back"
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        self.assertEqual(self.sent[0][1]["text"], "this session is not running — revive it to bring it back")

    def test_a_backend_that_raises_is_a_named_failure_not_a_silent_drop(self):
        self.be.answer = RuntimeError("the control channel is gone")
        self.door(SID, self.client)
        self.assertEqual([t for _, t, _, _ in self.frames()], ["restartFailed"])
        self.assertIn("the control channel is gone", self.sent[0][1]["text"])

    def test_a_codex_session_is_refused_in_words_not_with_an_attribute_error(self):
        # CodexBackend duck-types the backend interface without subclassing SessionBackend, so it inherits no
        # relaunch: the door met the missing method as an AttributeError and sent its text as the toast (the review
        # of #2059, 2026-09-24). A backend with no relaunch primitive answers the base refusal, as the move door does.
        def no_client():
            raise RuntimeError("this lab runs no Codex app-server")
        self.be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=no_client, log=lambda m: None)
        self.assertFalse(hasattr(self.be, "relaunch"), "the case pinned here: the Codex backend has no relaunch")
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("no way to relaunch", text, "the base refusal's words, which point at End and Revive")
        self.assertNotIn("has no attribute", text, "never a Python error as the toast")

    def test_a_sid_this_kernel_does_not_have_is_refused_before_any_backend_is_asked(self):
        self.door(OTHER, self.client)
        self.assertEqual(self.be.calls, [], "no backend is asked about a session this kernel has never heard of")
        self.assertEqual(self.frames(), [("chat", "restartFailed", OTHER, "win-A")])
        self.assertIn("no session with id", self.sent[0][1]["text"])

    def test_the_ws_op_answers_off_the_recv_loop(self):
        # the relaunch takes as long as an interrupt and a reconnect take; the socket must not wait on it
        entered, gate = threading.Event(), threading.Event()
        self.be = _Be(entered=entered, gate=gate)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        client = dict(self.client, alive=True, send=lambda raw: None)
        km.Handler._dispatch_ws(None, {"type": "restartSession", "id": SID}, client)
        self.assertTrue(entered.wait(timeout=10), "the op was never handled")
        self.assertEqual(self.sent, [], "the dispatch returned while the relaunch was still in flight")
        gate.set()
        for _ in range(500):
            if self.sent:
                break
            threading.Event().wait(0.01)
        self.assertEqual(self.frames(), [("chat", "restarted", SID, "win-A")])
        self.assertIsNot(self.be.threads[0], threading.current_thread(), "…on a thread of its own")

    def test_the_op_is_advertised_so_a_newer_page_meets_the_unknownOp_refusal_not_silence(self):
        self.assertIn("restartSession", km.KERNEL_WS_CAPS)

    def test_an_op_with_no_id_takes_no_thread_and_is_answered_unknownOp_like_any_unhandled_frame(self):
        sent = []
        client = dict(self.client, alive=True, send=lambda raw: sent.append(json.loads(raw)))
        km.Handler._dispatch_ws(None, {"type": "restartSession"}, client)
        self.assertEqual(self.be.calls, [])
        self.assertEqual([m.get("type") for m in sent], ["unknownOp"])


if __name__ == "__main__":
    unittest.main()
