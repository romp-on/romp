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
    it, and the cut is the polite request alone, once per stop episode, never Stop's signals — while a
    merely queued one is not cut at all. A dormant row has no process to replace and is simply
    connected. A sid romp has no record of, and a row that is not alive, refuse in words the user reads.

  * _restart_session, the door behind the WS restartSession op. It hands the work to the OWNING backend
    (nothing here duplicates the revive's resume), runs off the recv loop, and answers the pane that asked
    in the window that asked: `restarted` on success, `restartFailed` carrying the reason otherwise. It
    never focuses anything (the tab you are looking at is yours), never records a death, and leaves the
    names registry and the registry row exactly as it found them.

  * CodexBackend.relaunch, a refusal in Codex's own terms (2026-09-24, the post-merge note on #2125). Every
    Codex session runs on the kernel's one Codex app-server, so no session has a process of its own to
    replace, and End then Revive leaves it on the same app-server: End cuts a running turn and Revive resumes
    on the installed client. The refusal says what does start a fresh one, a restart of the romp kernel, and
    touches nothing; an ended Codex row is pointed at Revive, as an ended Claude row is.

Every leg reds at the merge base on the behaviour, not on a missing name: the two doors do not exist there,
so the tests say so through the AttributeError the getattr guards raise as an explicit failure. Synthetic
fixtures only: a hermetic state root, private placeholder sids, the notes-api demo names.
"""
import json
import os
import tempfile
import threading
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
UNHELD = "3a7c0003-2222-3333-4444-555555555555"   # one this kernel knows that no backend holds a row for


def _no_app_server():
    raise RuntimeError("this lab runs no Codex app-server")


def _codex_backend(dead=False):
    """A real CodexBackend over a hermetic state root holding one Codex row, web under SID, and no app-server. The
    row is seeded in the backend's own registry file, so nothing starts a worker or a pump: there is no queue to
    recover at load, and no client is ever built."""
    d = tempfile.mkdtemp()
    root = Path(d, "codex")
    root.mkdir(parents=True)
    (root / "registry.json").write_text(json.dumps({SID: {"tid": "T-1", "name": "web", "cwd": "/TESTDIR",
                                                          "dead": dead}}))
    return cb.CodexBackend(d, client_factory=_no_app_server, log=lambda m: None)


class _AppServerClient:
    """The installed Codex client, the one every Codex session's turns run on: records every call made on it."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append(name)


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


class CodexRelaunch(unittest.TestCase):
    """CodexBackend.relaunch on the rows the door cannot tell apart by routing alone (2026-09-24, the post-merge
    note on #2125): a sid the backend has no row for, and a row ended between the routing and the call."""

    def relaunch(self, be, sid=SID):
        fn = getattr(be, "relaunch", None)
        self.assertIsNotNone(fn, "CodexBackend has no relaunch of its own: a Codex session reads the generic End then "
                                 "Revive advice, which on Codex cuts a running turn and keeps the same app-server")
        return fn(sid)

    def test_a_sid_it_has_no_row_for_refuses_in_words(self):
        self.assertIn("no record of this session", self.relaunch(_codex_backend(), OTHER))

    def test_an_ended_row_is_pointed_at_revive_not_at_a_kernel_restart(self):
        r = self.relaunch(_codex_backend(dead=True))
        self.assertIn("not running", r)
        self.assertIn("revive", r)
        self.assertNotIn("app-server", r, "an ended row has no turn to lose: Revive is what brings it back")


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
        saved = self.real_backend_for = km.Sessions.__dict__["backend_for"]
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

    def test_a_backend_without_the_primitive_answers_the_base_refusal_not_an_attribute_error(self):
        # a backend may duck-type the interface without subclassing SessionBackend, and then it inherits no relaunch:
        # the door met the missing method as an AttributeError and sent its text as the toast (the review of #2059,
        # 2026-09-23). Such a backend answers the base refusal, as the move door does.
        class DuckTyped:
            pass
        self.be = DuckTyped()
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("no way to relaunch", text, "the base refusal's words")
        self.assertNotIn("has no attribute", text, "never a Python error as the toast")

    def test_a_codex_session_is_told_what_starts_a_fresh_app_server_and_nothing_is_touched(self):
        # every Codex session shares the kernel's one Codex app-server, so the base refusal's End then Revive did not
        # reach a newer binary: End cut a running turn, and Revive resumed on the same installed client (2026-09-24,
        # the post-merge note on #2125). The refusal names what does start a fresh one, and it changes nothing: the
        # turn keeps running on the client it started on, and the client is neither asked anything nor replaced.
        self.be = _codex_backend()
        s = self.be._session(SID)
        client = self.be._client = _AppServerClient()
        generation = self.be._client_generation
        with s.lock:
            s.turn_id = "t-1"                                  # a turn in flight on the shared app-server
        row = (self.be.root / "registry.json").read_bytes()
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("app-server", text, "why there is no process of this session's own to relaunch")
        self.assertIn("romp kernel", text, "what starts a fresh app-server")
        self.assertNotIn("revive", text.lower(), "End then Revive keeps a Codex session on the app-server it had")
        self.assertNotIn("has no attribute", text)
        self.assertEqual(client.calls, [], "nothing is asked of the shared app-server: no interrupt, no close")
        self.assertIs(self.be._client, client, "and the client every Codex session runs on is not replaced")
        self.assertEqual(self.be._client_generation, generation)
        self.assertEqual((s.dead, s.turn_id), (False, "t-1"), "the session is not ended and its turn is not cut")
        self.assertEqual((self.be.root / "registry.json").read_bytes(), row, "the registry row is not rewritten")

    def routed(self, be):
        """The kernel's own routing, Sessions.backend_for, instead of the stub, with this Codex backend as the kernel's
        and no Claude Code backend to own the sid first."""
        km.Sessions.backend_for = self.real_backend_for
        self.patch("_sdk", lambda: None)
        self.patch("_codex", lambda: be)

    def test_the_kernels_routing_hands_a_live_codex_session_to_the_codex_backend(self):
        self.routed(_codex_backend())
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        self.assertIn("app-server", self.sent[0][1]["text"], "the Codex backend's own refusal, not the base one")

    def test_an_ended_codex_session_is_pointed_at_revive_as_an_ended_claude_session_is(self):
        # owns() is live-only by design, so the routing alone hands an ended Codex row to the unowned route, whose
        # base refusal told the user to end a session that had already ended; the revive door routes that row to the
        # Codex backend by its record, and so does this one (2026-09-24, the post-merge note on #2125)
        self.routed(_codex_backend(dead=True))
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("not running", text)
        self.assertIn("revive", text)
        self.assertNotIn("end it", text, "the session has already ended")

    def test_a_sid_the_codex_backend_holds_no_row_for_is_not_handed_to_it(self):
        # the ended-row routing keys on the Codex backend's record, not on the unowned route alone: a sid this kernel
        # knows but no backend holds is not a Codex session, so it keeps the base refusal instead of the Codex
        # backend's "no record" (2026-09-24, the post-merge note on #2125)
        self.patch("_kernel_knows", lambda sid: sid in (SID, UNHELD))
        self.routed(_codex_backend())
        self.door(UNHELD, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", UNHELD, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("no way to relaunch", text, "the unowned route's base refusal")
        self.assertNotIn("no record", text, "the Codex backend was never asked about a sid it holds no row for")

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
