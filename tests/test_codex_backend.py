#!/usr/bin/env python3
"""CodexBackend contract tests — a scripted FAKE client (no SDK, no network, no login) drives the
backend through spawn/send/steer/interrupt/kill/resume and pins: the worker's turn loop writes the
materialized transcript, state transitions are event-based, the uuid chain survives a backend
restart (the _tail_state re-anchor), Claude-only knobs refuse loudly, and a missing `codex login`
surfaces as launch_error text instead of a silent non-start. All data synthetic per CLAUDE.md.

Run:    python3 tests/test_codex_backend.py
"""
import contextlib
import errno
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from romp_load import load_source

from tests.conftest import thread_census, wait_for_census
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
cb = load_source("romp_codex_backend", os.path.join(ROOT, "kernel", "codex_backend.py"))
sb = load_source("romp_session_backend", os.path.join(ROOT, "kernel", "session_backend.py"))
em = load_source("romp_event_model", os.path.join(ROOT, "bin", "romp-event-model"))


MODEL_CHOICES = [{"value": "gpt-5-test", "model": "gpt-5-test", "label": "GPT-5 Test", "isDefault": True,
                  "efforts": [{"value": v, "label": v, "sub": ""} for v in ("low", "medium", "high", "xhigh")]}]

@contextlib.contextmanager
def _disk_full(nf):
    """ENOSPC beneath the REAL names writer: the publish (os.replace onto names/<sid>) fails, and an
    in-place write to that file does what ENOSPC does to one — truncates, then fails. Every other path
    proceeds, so only the writer under test, and any restore aimed at its file, feel the full disk."""
    want = os.path.realpath(str(nf))
    real_replace, real_wb = os.replace, Path.write_bytes

    def replace(src, dst, *a, **k):
        if os.path.realpath(str(dst)) == want:
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *a, **k)

    def write_bytes(self, data, *a, **k):
        if os.path.realpath(str(self)) == want:
            open(self, "w").close()
            raise OSError(28, "No space left on device")
        return real_wb(self, data, *a, **k)
    with mock.patch.object(os, "replace", replace), mock.patch.object(Path, "write_bytes", write_bytes):
        yield


def until(fn, timeout=5.0, step=0.01):
    dl = time.time() + timeout
    while time.time() < dl:
        if fn():
            return True
        time.sleep(step)
    return False


def registry_queue_entries(rows, sid):
    """Canonical durable entries; tests that seed the legacy schema may still contain strings."""
    return [entry if isinstance(entry, dict) else {"id": None, "text": entry}
            for entry in rows[sid].get("queue", [])]


def registry_queue_texts(rows, sid):
    return [entry["text"] for entry in registry_queue_entries(rows, sid)]


class _Payload:
    def __init__(self, d):
        self._d = d

    def model_dump(self, by_alias=True, mode=None):
        return self._d


def note(method, params):
    return SimpleNamespace(method=method, payload=_Payload(params))


_CLIENTS = []                    # every FakeClient made by this module
_BACKENDS = []                   # every CodexBackend made by this module
_CENSUS0 = []
_ORIG_INIT = []


def setUpModule():
    # Residue hygiene (T282): each backend this module builds starts a global pump per client and a worker per
    # session, daemon threads that parked on the fake client forever and outlived the module (64 of them under
    # the full suite). Every backend and client is recorded here and ended in tearDownModule, which then pins
    # the thread census back to what it was when the module started.
    _CENSUS0[:] = [thread_census()]
    orig = cb.CodexBackend.__init__

    def recording_init(self, *a, **k):
        orig(self, *a, **k)
        _BACKENDS.append(self)
    _ORIG_INIT[:] = [orig]
    cb.CodexBackend.__init__ = recording_init


def tearDownModule():
    cb.CodexBackend.__init__ = _ORIG_INIT[0]
    for be in _BACKENDS:
        for _, sess in be._session_items():          # a worker returns when it wakes to a dead session
            with sess.lock:
                sess.dead = True
            sess.kick.set()
    for c in _CLIENTS:
        c.close()                                    # a pump returns when its client reads as closed
    _BACKENDS.clear(); _CLIENTS.clear()
    left = wait_for_census(_CENSUS0[0], timeout=10)
    assert left == [], "threads outlived this module: %r" % left


class FakeClient:
    """Scripted app-server: turn_start opens a queue and streams either the injected script or a
    default echo turn (userMessage + agentMessage + tokenUsage + completed)."""

    def __init__(self):
        self.calls = []
        self.turn_queues = {}
        self.scripts = []           # each turn_start pops one, [] → default echo turn
        self.hold_open = False      # script the turn to stay open (steer/interrupt tests)
        self._n = 0
        self._global = queue.Queue()
        self._closed = False
        _CLIENTS.append(self)       # every client ever made is closed when the module ends (T282)

    # bookkeeping helpers ------------------------------------------------------------------
    def _rec(self, name, *a):
        self.calls.append((name,) + a)

    def called(self, name):
        return [c for c in self.calls if c[0] == name]

    # client surface the backend uses ------------------------------------------------------
    def account_read(self, *a, **k):
        self._rec("account_read")
        return SimpleNamespace(requires_openai_auth=False, account={"ok": True})

    def initialize(self):
        self._rec("initialize")

    def close(self):
        self._rec("close")
        self._closed = True
        self._global.put(None)      # wakes the backend's global pump, which reads the close and stops (T282)

    def thread_start(self, params=None):
        self._rec("thread_start", params)
        return SimpleNamespace(thread=SimpleNamespace(id="T-%d" % len(self.called("thread_start"))),
                               model="gpt-5-test")

    def thread_resume(self, tid, params=None):
        self._rec("thread_resume", tid, params)
        return SimpleNamespace(thread=SimpleNamespace(id=tid))

    def thread_set_name(self, tid, name):
        self._rec("thread_set_name", tid, name)

    def model_list(self, *a, **k):
        self._rec("model_list")
        return SimpleNamespace(data=[
            SimpleNamespace(id="gpt-5-test", display_name="GPT-5 Test", hidden=False, is_default=True,
                            supported_reasoning_efforts=[SimpleNamespace(reasoning_effort=v, description="")
                                                        for v in ("low", "medium", "high", "xhigh")]),
            SimpleNamespace(id="gpt-5-hidden", display_name="Hidden", hidden=True)])

    def turn_start(self, tid, input_items, params=None):
        self._n += 1
        turn_id = "t-%d" % self._n
        self._rec("turn_start", tid, input_items, params, turn_id)
        q = queue.Queue()
        self.turn_queues[turn_id] = q
        script = self.scripts.pop(0) if self.scripts else None
        ms = 1781100000000 + self._n * 100000
        text = " ".join(i.get("text", "") for i in input_items)
        if script is None:
            # the app-server's shape: ONE userMessage item carrying the turn's input list, an entry per
            # input (the worker sends one input per queued send), never the inputs joined into one text
            script = [
                ("item/completed", {"threadId": tid, "turnId": turn_id, "completedAtMs": ms,
                                    "item": {"type": "userMessage", "id": "u-%d" % self._n,
                                             "content": list(input_items)}}),
                ("item/completed", {"threadId": tid, "turnId": turn_id, "completedAtMs": ms + 1000,
                                    "item": {"type": "agentMessage", "id": "a-%d" % self._n,
                                             "text": "ack: " + text}}),
                ("thread/tokenUsage/updated",
                 {"threadId": tid, "turnId": turn_id,
                  "tokenUsage": {"last": {"inputTokens": 900, "outputTokens": 40,
                                          "cachedInputTokens": 500, "reasoningOutputTokens": 10,
                                          "totalTokens": 54400},
                                 "total": {"inputTokens": 9000, "outputTokens": 400,
                                           "cachedInputTokens": 5000,
                                           "reasoningOutputTokens": 100, "totalTokens": 500000},
                                 "modelContextWindow": 272000}}),
                ("turn/completed", {"threadId": tid,
                                    "turn": {"id": turn_id, "items": [], "status": "completed"}}),
            ]
        for m, p in script:
            q.put(note(m, p))
        if not self.hold_open and not any(m == "turn/completed" for m, _ in script):
            q.put(note("turn/completed", {"threadId": tid,
                                          "turn": {"id": turn_id, "items": [],
                                                   "status": "completed"}}))
        return SimpleNamespace(turn=SimpleNamespace(id=turn_id))

    def next_turn_notification(self, turn_id):
        return self.turn_queues[turn_id].get(timeout=10)

    def unregister_turn_notifications(self, turn_id):
        self._rec("unregister", turn_id)

    def turn_steer(self, tid, expected_turn_id, input_items):
        self._rec("turn_steer", tid, expected_turn_id, input_items)

    def turn_interrupt(self, tid, turn_id):
        self._rec("turn_interrupt", tid, turn_id)
        self.turn_queues[turn_id].put(note("turn/completed",
                                           {"threadId": tid,
                                            "turn": {"id": turn_id, "items": [],
                                                     "status": "interrupted"}}))

    def next_notification(self):
        n = self._global.get()      # parks the backend's global pump until a notification or the close
        if n is None or self._closed:
            raise RuntimeError("client closed")   # the pump logs "global pump stopped" and returns
        return n


def build(tmp=None, factory=None):
    tmp = tmp or tempfile.mkdtemp()
    fake = FakeClient()
    be = cb.CodexBackend(tmp, client_factory=(factory or (lambda: fake)))
    return be, fake, tmp


class Conformance(unittest.TestCase):
    def test_every_abstract_method_exists(self):
        missing = [m for m in sb.SessionBackend.__abstractmethods__
                   if not callable(getattr(cb.CodexBackend, m, None))]
        self.assertEqual(missing, [], "CodexBackend must duck-type the full ABC")

    def test_only_deterministic_rpc_rejections_park(self):
        def error_type(name, code, message):
            cls = type(name, (RuntimeError,), {})
            err = cls(message)
            err.code, err.message = code, message
            return err

        self.assertTrue(cb._is_permanent_request_rejection(
            error_type("InvalidParamsError", -32602, "invalid model")))
        self.assertTrue(cb._is_permanent_request_rejection(
            error_type("CodexRpcError", -32000, "model gpt-x is not supported for this account")))
        self.assertFalse(cb._is_permanent_request_rejection(
            error_type("InternalRpcError", -32603, "internal error")))
        self.assertFalse(cb._is_permanent_request_rejection(
            error_type("CodexRpcError", -32001, "temporary backend failure")))


class ApprovalModes(unittest.TestCase):
    def test_auto_mode_persists_and_is_sent_on_resume_and_every_turn(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertEqual(be.live_sessions()[sid]["mode"], "sandboxed")
        self.assertTrue(be.set_mode(sid, "auto"))
        restored = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertEqual(restored.live_sessions()[sid]["mode"], "auto")
        for text in ("first synthetic turn", "second synthetic turn"):
            self.assertTrue(restored.send(sid, text))
            self.assertTrue(until(lambda: not restored.busy(sid)))
        requests = [c[2] for c in fake.called("thread_resume")]
        requests += [c[3] for c in fake.called("turn_start")]
        self.assertEqual(len(requests), 3)
        for params in requests:
            self.assertEqual(params["approvalPolicy"], "on-request")
            self.assertEqual(params["approvalsReviewer"], "auto_review")
            self.assertEqual(params["permissions"], cb.WORKSPACE_PERMISSION)
            self.assertEqual(params["runtimeWorkspaceRoots"], ["/TESTDIR"])
            self.assertNotIn("sandboxPolicy", params)
        restored.kill(sid)

    def test_switch_back_resets_reviewer_and_legacy_rows_stay_sandboxed(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_mode(sid, "auto"))
        self.assertTrue(be.set_mode(sid, "sandboxed"))
        self.assertTrue(be.send(sid, "synthetic turn"))
        self.assertTrue(until(lambda: not be.busy(sid)))
        params = fake.called("turn_start")[-1][3]
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["approvalsReviewer"], "user")
        be.kill(sid)
        rows = json.loads(be._reg_path().read_text())
        rows[sid].pop("mode")
        be._reg_path().write_text(json.dumps(rows))
        restored = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertEqual(restored._sessions[sid].mode, "sandboxed")

    def test_pending_session_recovery_uses_selected_mode(self):
        def unavailable():
            raise RuntimeError("synthetic unavailable client")
        be, _, tmp = build(factory=unavailable)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_mode(sid, "auto"))
        fake = FakeClient()
        restored = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertTrue(restored.send(sid, "synthetic recovery"))
        self.assertTrue(until(lambda: not restored.busy(sid)))
        params = fake.called("thread_start")[-1][1]
        self.assertEqual(params["approvalPolicy"], "on-request")
        self.assertEqual(params["approvalsReviewer"], "auto_review")
        restored.kill(sid)

    def test_pending_session_recovery_keeps_the_picked_model(self):
        # a model picked while the row was still a placeholder vanished at the first send:
        # thread/start went out without it, and the server's reply (its default, never empty)
        # overwrote the pick in memory and in the registry, so the turn ran on the default and
        # the picker showed it, with no word to the user
        def unavailable():
            raise RuntimeError("synthetic unavailable client")
        be, _, tmp = build(factory=unavailable)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_model(sid, "gpt-5-picked"))
        fake = FakeClient()
        restored = cb.CodexBackend(tmp, client_factory=lambda: fake)
        try:
            self.assertTrue(restored.send(sid, "synthetic recovery"))
            self.assertTrue(until(lambda: not restored.busy(sid)))
            self.assertEqual(fake.called("turn_start")[-1][3].get("model"), "gpt-5-picked",
                             "the turn runs on the pick, not the server's default")
            self.assertEqual(restored.live_sessions()[sid]["model"], "gpt-5-picked")
            rows = json.loads(restored._reg_path().read_text())
            self.assertEqual(rows[sid]["model"], "gpt-5-picked", "the registry keeps the pick")
            self.assertEqual(fake.called("thread_start")[-1][1].get("model"), "gpt-5-picked",
                             "the thread is created on the pick")
        finally:
            restored.kill(sid)

    def test_failed_placeholder_recovery_keeps_the_picked_model(self):
        # the same loss without a restart: thread/start raised at spawn (a failed- row on a live
        # client), the user picked a model on the idle row, then sent
        class BoomOnce(FakeClient):
            def __init__(self):
                super().__init__()
                self.boom = True

            def thread_start(self, params=None):
                if self.boom:
                    self.boom = False
                    raise RuntimeError("synthetic thread/start failure")
                return super().thread_start(params)
        fake = BoomOnce()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be._sessions[sid].tid.startswith("failed-"))
        self.assertTrue(be.set_model(sid, "gpt-5-picked"))
        try:
            self.assertTrue(be.send(sid, "synthetic recovery"))
            self.assertTrue(until(lambda: not be.busy(sid)))
            self.assertEqual(fake.called("turn_start")[-1][3].get("model"), "gpt-5-picked")
            self.assertEqual(be.live_sessions()[sid]["model"], "gpt-5-picked")
            self.assertEqual(fake.called("thread_start")[-1][1].get("model"), "gpt-5-picked")
        finally:
            be.kill(sid)

    def test_model_picked_while_the_thread_is_being_created_stays_picked(self):
        # the create RPC runs under mode_lock alone and set_model takes the session lock alone, so
        # a pick can land while thread/start is in flight; the reply must read the live model, not
        # the snapshot the params were built from, or the server's default overwrites that pick
        class Holding(FakeClient):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def thread_start(self, params=None):
                self.entered.set()
                assert self.release.wait(timeout=10), "the create was never released"
                return super().thread_start(params)
        def unavailable():
            raise RuntimeError("synthetic unavailable client")
        be, _, tmp = build(factory=unavailable)
        sid = be.spawn("web", "/TESTDIR")
        fake = Holding()
        restored = cb.CodexBackend(tmp, client_factory=lambda: fake)
        try:
            self.assertEqual(restored._sessions[sid].model, "", "no pick before the send")
            self.assertTrue(restored.send(sid, "synthetic recovery"))
            self.assertTrue(fake.entered.wait(timeout=5), "thread/start never went out")
            self.assertTrue(restored.set_model(sid, "gpt-5-picked"))
            fake.release.set()
            self.assertTrue(until(lambda: not restored.busy(sid)))
            self.assertNotIn("model", fake.called("thread_start")[-1][1],
                             "the pick landed after the create went out")
            self.assertEqual(fake.called("turn_start")[-1][3].get("model"), "gpt-5-picked",
                             "the turn runs on the pick that landed during the create")
            self.assertEqual(restored.live_sessions()[sid]["model"], "gpt-5-picked")
            rows = json.loads(restored._reg_path().read_text())
            self.assertEqual(rows[sid]["model"], "gpt-5-picked", "the registry keeps the pick")
        finally:
            fake.release.set()             # a failure above must not leave the worker parked
            restored.kill(sid)

    def test_mode_change_refuses_inflight_turn_and_rolls_back_failed_save(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._sessions[sid]
        with s.mode_lock:
            self.assertFalse(be.set_mode(sid, "auto"))
        self.assertFalse(be.set_mode(sid, "bypassPermissions"))
        with mock.patch.object(be, "_save_registry", side_effect=OSError("synthetic save failure")):
            with self.assertRaises(OSError):
                be.set_mode(sid, "auto")
        self.assertEqual(s.mode, "sandboxed")
        be.kill(sid)
        self.assertFalse(be.set_mode(sid, "auto"))

    def test_inflight_turn_keeps_its_mode_and_interrupt_still_works(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[]]
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_mode(sid, "auto"))
        self.assertTrue(be.send(sid, "synthetic held turn"))
        try:
            self.assertTrue(until(lambda: be._sessions[sid].turn_id is not None))
            self.assertFalse(be.set_mode(sid, "sandboxed"))
            self.assertEqual(be.live_sessions()[sid]["mode"], "auto")
            self.assertTrue(be.interrupt(sid))
            self.assertTrue(until(lambda: not be.busy(sid)))
            self.assertTrue(be.set_mode(sid, "sandboxed"))
        finally:
            be.kill(sid)

    def test_invalid_saved_mode_defaults_to_sandboxed(self):
        be, _, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        rows = json.loads(be._reg_path().read_text())
        rows[sid]["mode"] = "bypassPermissions"
        be._reg_path().write_text(json.dumps(rows))
        logs = []
        restored = cb.CodexBackend(tmp, client_factory=FakeClient, log=logs.append)
        self.assertEqual(restored.live_sessions()[sid]["mode"], "sandboxed")
        self.assertTrue(logs)
        restored.kill(sid)

    def test_manual_approval_fallback_never_accepts_or_blocks_the_reader(self):
        be, _, _ = build()
        notices = []
        be.notify = lambda app, msg: notices.append(msg)
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            self.assertEqual(be._handle_approval(method, {}), {"decision": "decline"})
        self.assertEqual(be._handle_approval("item/permissions/requestApproval", {}),
                         {"permissions": {}, "scope": "turn"})
        # an UNKNOWN request answers the SDK's own default, `{}` — never a raise: the handler runs inline on
        # the SDK's single reader thread, and a raise there ends the reader and fails every Codex session's
        # in-flight turn at once (review find on #930, 2026-09-07); the app-server reads an empty approval
        # answer as a denial, scoped to that one request
        self.assertEqual(be._handle_approval("unknown/requestApproval", {}), {})
        self.assertTrue(notices)
        self.assertTrue(all(msg["type"] == "warn" for msg in notices))

    def test_a_dead_stderr_never_raises_out_of_the_approval_handler(self):
        # the handler's FIRST line is a log write, before any answer is returned, and it runs inline on the
        # SDK's single reader thread (the case above). With a stderr that raises on write (a log disk at
        # ENOSPC, the pipe a supervisor's end closed) that write raised out of the handler: the reader ended
        # with no reply written and every in-flight request of every Codex session failed at once. The log
        # callback is wrapped ONCE at construction, so the raising logger goes in through the constructor: a
        # post-construction `be.log = ...` replaces the wrap and would prove nothing
        def dead_stderr(m):
            raise OSError(28, "No space left on device")
        be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=FakeClient, log=dead_stderr)
        notices = []
        be.notify = lambda app, msg: notices.append(msg)
        answers = (("item/commandExecution/requestApproval", {"decision": "decline"}),
                   ("item/fileChange/requestApproval", {"decision": "decline"}),
                   ("item/permissions/requestApproval", {"permissions": {}, "scope": "turn"}),
                   ("item/tool/requestUserInput", {"answers": {}}),
                   ("unknown/requestApproval", {}))
        for method, want in answers:
            self.assertEqual(be._handle_approval(method, {}), want, method)
        self.assertEqual(len(notices), len(answers), "the session still hears every denial")
        self.assertTrue(all(msg["type"] == "warn" for msg in notices))
        # the DEFAULT logger (no log= handed in: the bare `codex-backend:` stderr line) is wrapped the same way
        be2 = cb.CodexBackend(tempfile.mkdtemp(), client_factory=FakeClient)
        with mock.patch.object(sys, "stderr", SimpleNamespace(write=dead_stderr, flush=lambda: None)):
            self.assertEqual(be2._handle_approval("item/commandExecution/requestApproval", {}),
                             {"decision": "decline"})

    def test_a_dead_stderr_does_not_end_the_pump_before_it_records_the_failure(self):
        # the same shape on the pump thread: its except branch logs FIRST, then uninstalls the client
        # (_record_client_failure_locked) and wakes queued workers. Under a raising stderr the pump died at
        # that log line, so the dead client stayed installed: the next send's _get_client handed it back
        # and turn_start parked forever in the SDK's untimed wait, every Codex session wedged until a
        # restart, with nothing on the log to say so
        def dead_stderr(m):
            raise OSError(28, "No space left on device")
        fake = FakeClient()
        be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: fake, log=dead_stderr)
        self.assertIs(be._get_client(), fake)
        fake.close()                                  # next_notification raises: the pump's except branch runs
        self.assertTrue(until(lambda: be._client is None), "the dead client is uninstalled")
        self.assertIn("client closed", be._client_err or "", "the failure is recorded, not lost with the line")

    def test_a_dead_stderr_does_not_end_the_worker_before_it_files_the_failure(self):
        # the third site with this shape, on each session's worker thread: _work's except branch logs the
        # traceback FIRST, then files launch_error and sets the session "waiting". Under a raising stderr the
        # worker died at that log line, and _work's finally only clears s.worker: nothing was filed, the
        # queued batch parked with no visible reason, and the session read busy forever. A plain RuntimeError
        # from turn_start (not a permanent rejection) reaches that branch; the second attempt holds at its
        # entry, so the filed error is read before the ack that clears it
        def dead_stderr(m):
            raise OSError(28, "No space left on device")

        class FailOnceClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.attempts = 0
                self.retry_entered = threading.Event()
                self.allow_retry = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("synthetic pre-ack failure")
                self.retry_entered.set()
                self.allow_retry.wait(5)
                return super().turn_start(tid, input_items, params)

        fake = FailOnceClient()
        be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: fake, log=dead_stderr)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "first"))
        self.assertTrue(fake.retry_entered.wait(5), "the worker never came back for the retry: it died at the log line")
        err = be.launch_error(sid)
        self.assertIsNotNone(err, "the failure is filed, not lost with the line")
        self.assertEqual(err["text"], "codex turn failed: synthetic pre-ack failure")
        self.assertEqual(be.live_sessions()[sid]["state"], "waiting")
        fake.allow_retry.set()
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(fake.attempts, 2)
        # the contract itself, in one line: the wrap is at construction, so the callback the backend holds never
        # raises, whichever site on whichever thread calls it. A guard at each of the three sites above would
        # pass the three cases and fail here
        be.log("probe")

    def test_real_client_is_constructed_with_fail_closed_handler(self):
        be, _, _ = build()
        be._client_factory = None
        be.codex_bin = "/TESTBIN/codex"
        fake = FakeClient()
        fake.start = lambda: None
        module = SimpleNamespace(CodexClient=mock.Mock(return_value=fake),
                                 CodexConfig=lambda **kwargs: kwargs)
        with mock.patch.object(cb, "ensure_codex_sdk", return_value=True), \
             mock.patch.dict(sys.modules, {"openai_codex.client": module}):
            self.assertIs(be._get_client(), fake)
        self.assertEqual(module.CodexClient.call_args.kwargs["approval_handler"], be._handle_approval)


class ExplicitBinFailures(unittest.TestCase):
    """A codex that cannot be started from an EXPLICIT ROMP_CODEX_BIN is reported in the operator's words.
    The kernel hands ROMP_CODEX_BIN through unchecked, and _get_client kept whatever the SDK's start() raised
    as _client_err verbatim: for a missing file the pinned SDK's "Codex binary not found at X. Set
    CodexConfig.codex_bin to a valid binary path." (a Python field the operator has never seen); for a file
    that exists but cannot run (no exec bit, a directory) Popen's bare "[Errno 13] Permission denied: X", with
    no remedy at all. Every surface that shows the record (the chat's red card, the creation refusals, the
    resume detail, the /models note) repeated it, and nothing named the knob to fix (2026-09-11). The record
    now names ROMP_CODEX_BIN and the managed-runtime alternative (_explicit_bin_failure). Same harness as
    test_real_client_is_constructed_with_fail_closed_handler: the SDK module is the seam, its client's start()
    raising exactly what the real one raises; the managed runtime's location is stubbed only so the
    codex_bin=None case reaches start() on a state root with no runtime installed."""

    def _probe(self, codex_bin, exc):
        be, _, _ = build()
        be._client_factory = None
        be.codex_bin = codex_bin
        fake = FakeClient()

        def start():
            raise exc
        fake.start = start
        module = SimpleNamespace(CodexClient=mock.Mock(return_value=fake),
                                 CodexConfig=lambda **kwargs: kwargs)
        with mock.patch.object(cb, "ensure_codex_sdk", return_value=True), \
             mock.patch.dict(sys.modules, {"openai_codex.client": module}), \
             mock.patch.object(cb._runtime, "runtime_path", return_value=Path("/TESTBIN/managed/bin/codex")):
            self.assertIsNone(be._get_client())
        return be._client_err or ""

    def test_a_missing_file_names_the_knob_not_the_sdk_field(self):
        text = self._probe("/TESTBIN/codex", FileNotFoundError(
            "Codex binary not found at /TESTBIN/codex. Set CodexConfig.codex_bin to a valid binary path."))
        self.assertIn("ROMP_CODEX_BIN=/TESTBIN/codex", text)
        self.assertIn("unset ROMP_CODEX_BIN", text)
        self.assertIn("restart the ROMP kernel", text,
                      "the knob was read once, at construction: unset alone changes nothing: %r" % text)
        self.assertIn("No such file or directory", text)
        self.assertNotIn("CodexConfig", text, "the SDK's dataclass field means nothing to an operator: %r" % text)

    def test_a_file_that_cannot_run_names_the_knob_and_a_remedy(self):
        # exists() passes a non-executable file or a directory; Popen then raises the errno with no remedy
        text = self._probe("/TESTBIN/codex", PermissionError(13, "Permission denied", "/TESTBIN/codex"))
        self.assertIn("ROMP_CODEX_BIN=/TESTBIN/codex", text)
        self.assertIn("unset ROMP_CODEX_BIN", text)
        self.assertIn("restart the ROMP kernel", text)
        self.assertIn("Permission denied", text, "the OS's reason is kept: %r" % text)

    def test_a_file_that_is_not_a_binary_names_the_knob(self):
        # exists() passes a text file or a wrong-arch binary; execve then answers ENOEXEC, the third path error
        # the gate names
        text = self._probe("/TESTBIN/codex", OSError(errno.ENOEXEC, "Exec format error", "/TESTBIN/codex"))
        self.assertIn("ROMP_CODEX_BIN=/TESTBIN/codex", text)
        self.assertIn("Exec format error", text, "the OS's reason is kept: %r" % text)

    def test_a_host_fault_with_an_explicit_bin_stays_raw(self):
        # the knob is set, but running out of descriptors is the host's fault, not the path's: the gate is the
        # three path errors, not every OSError, so a fault Popen raised is recorded as it came, and a non-OSError
        # from start() passes through untouched
        err = OSError(errno.EMFILE, "Too many open files")
        self.assertEqual(self._probe("/TESTBIN/codex", err), str(err))
        err = RuntimeError("synthetic start failure")
        self.assertEqual(self._probe("/TESTBIN/codex", err), str(err))

    def test_a_directory_the_kernel_cannot_read_names_the_knob_not_a_sibling_path(self):
        # ROMP_CODEX_BIN under a directory the kernel's user cannot traverse: _codex_config's look at the helpers
        # beside the executable raises before start() is reached (pathlib passes EACCES through from is_dir()),
        # and the record named <package>/codex-path, a path the operator never typed. The filesystem's answer
        # for that one path is the seam; _codex_config itself runs for real, and start() is never reached.
        real_is_dir = Path.is_dir

        def is_dir(path, *a, **k):
            if str(path) == "/TESTBIN/codex-path":
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_is_dir(path, *a, **k)
        with mock.patch.object(Path, "is_dir", is_dir):
            text = self._probe("/TESTBIN/codex", AssertionError("start() must not be reached: the config step raised"))
        self.assertIn("ROMP_CODEX_BIN=/TESTBIN/codex", text)
        self.assertIn("unset ROMP_CODEX_BIN", text)
        self.assertIn("Permission denied", text, "the OS's reason is kept: %r" % text)
        self.assertNotIn("codex-path", text, "the path the operator set is named, not one beside it: %r" % text)

    def test_the_managed_runtime_keeps_its_raw_errno_line(self):
        # codex_bin None: there is no knob to name, and tests/test_codex_launch_error_card.py pins the raw
        # errno line as the shape _client_failure_text frames — the managed path is left exactly alone
        err = OSError(2, "No such file or directory", "codex")
        self.assertEqual(self._probe(None, err), str(err))


class Lifecycle(unittest.TestCase):
    def test_spawn_send_turn_materializes_transcript(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.owns(sid))
        self.assertEqual(be.live_sessions()[sid]["state"], "waiting")
        self.assertTrue(be.send(sid, "hello codex"))
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "waiting"
                              and not be.busy(sid)))
        path = be.transcript_path(sid)
        recs = [json.loads(l) for l in Path(path).read_text().splitlines()]
        self.assertEqual([r["type"] for r in recs], ["user", "assistant"])
        self.assertEqual(recs[0]["promptSource"], "sdk")
        self.assertEqual(recs[1]["message"]["stop_reason"], "end_turn")
        self.assertEqual(recs[1]["parentUuid"], recs[0]["uuid"])
        # the optimistic echo was pruned when its record landed
        self.assertTrue(until(lambda: be.live_atoms(sid) == []))
        # context % from tokenUsage: 54400/272000 = 20
        self.assertEqual(be.live_sessions()[sid]["context"], 20)
        # Both thread creation and each turn carry the pinned runtime's named workspace profile.
        # The stale legacy workspaceWrite shape has unrestricted reads and must never reappear.
        thread_params = fake.called("thread_start")[0][1]
        self.assertEqual(thread_params["permissions"], "romp_workspace")
        self.assertEqual(thread_params["runtimeWorkspaceRoots"], ["/TESTDIR"])
        self.assertNotIn("sandbox", thread_params)
        _, tid, items, params, _ = fake.called("turn_start")[0]
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertEqual(params["permissions"], "romp_workspace")
        self.assertEqual(params["runtimeWorkspaceRoots"], ["/TESTDIR"])
        self.assertNotIn("sandboxPolicy", params)

    def test_two_sends_queued_before_the_turn_land_as_two_blocks_and_retire_both_echoes(self):
        # Two sends queued before the worker starts a turn (an idle session sent twice quickly, sends
        # while the client is down or backing off, a resume after a kernel restart) go out as ONE
        # turn_start with an input per send; the app-server answers one userMessage item carrying both,
        # and the normalizer lands it as one record with a text block per input. Joined into one block
        # the record matches neither echo, and both echoes stay live for good, painted beside the record
        # in every later build.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        be._ensure_worker = lambda s: None             # hold the worker so both sends queue
        self.assertTrue(be.send(sid, "first send"))
        self.assertTrue(be.send(sid, "second send"))
        del be._ensure_worker                          # the class method is back in place
        self.assertEqual([a["_echo_text"] for a in be.live_atoms(sid)], ["first send", "second send"])
        self.assertTrue(be.wake(sid))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        starts = fake.called("turn_start")
        self.assertEqual(len(starts), 1, "one turn for the whole queue")
        self.assertEqual([i["text"] for i in starts[0][2]], ["first send", "second send"])
        recs = [json.loads(l) for l in Path(be.transcript_path(sid)).read_text().splitlines()]
        users = [r for r in recs if r["type"] == "user"]
        self.assertEqual(len(users), 1, "one user record for the one item")
        self.assertEqual(users[0]["message"]["content"],
                         [{"type": "text", "text": "first send"}, {"type": "text", "text": "second send"}],
                         "a text block per send, never the sends joined")
        self.assertTrue(until(lambda: be.live_atoms(sid) == []), "both echoes retired, one per block")
        s = be._session(sid)
        with s.lock:
            self.assertEqual(s.echoes, [])

    def test_send_during_open_turn_steers(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "long job"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "long job")
        # wait for the TURN to open (busy() is already true while merely queued — a steer needs
        # the active turn id)
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        self.assertTrue(be.send(sid, "also check the docs"))
        self.assertEqual(len(fake.called("turn_steer")), 1)
        _, tid, expected_turn_id, input_items = fake.called("turn_steer")[0]
        self.assertEqual(expected_turn_id, "t-1")
        self.assertEqual(input_items, [{"type": "text", "text": "also check the docs"}])
        fake.turn_queues["t-1"].put(note("turn/completed",
                                         {"threadId": tid,
                                          "turn": {"id": "t-1", "items": [],
                                                   "status": "completed"}}))
        self.assertTrue(until(lambda: not be.busy(sid)))

    def test_interrupt_targets_active_turn_and_settles(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "run forever"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "run forever")
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        self.assertTrue(be.interrupt(sid))
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1")
        self.assertTrue(until(lambda: not be.busy(sid)))
        recs = [json.loads(l) for l in Path(be.transcript_path(sid)).read_text().splitlines()]
        self.assertTrue(any("[Request interrupted" in json.dumps(r) for r in recs))

    def test_turn_abandoned_by_a_dead_transport_settles_on_disk(self):
        """A turn whose stream ends WITHOUT turn/completed (the app-server died mid-turn: the SDK puts the
        transport failure into the turn's queue and next_turn_notification raises it) used to leave the
        file turn open for good: nothing wrote the held final reply or ended the turn, so the session read
        as working on every surface while nothing ran, and the next prompt was absorbed into the dead turn
        as mid-turn input. The settle lands the held reply mid-turn-shaped, then an end_turn record
        carrying the failure, and the next prompt opens its own turn. No interrupt goes out on the dead
        transport: the SDK's request path waits on a reply with no timeout and its reader is gone, so a
        worker that sent one would sit there for good (busy True, mode locked, kill's join timing out);
        the pump's teardown of that client is what stops the turn."""
        class DyingClient(FakeClient):
            def next_turn_notification(self, turn_id):
                n = super().next_turn_notification(turn_id)
                if isinstance(n, BaseException):    # the SDK router's shape: fail_all → queue → raise
                    raise n
                return n

            def turn_interrupt(self, tid, turn_id):
                self._rec("turn_interrupt", tid, turn_id)
                self.reply.wait()                   # the SDK's shape with its reader gone: no reply, ever

        def item(turn, ms, it):
            return ("item/completed", {"threadId": "T-1", "turnId": turn, "completedAtMs": ms, "item": it})

        fake = DyingClient()
        fake.reply = threading.Event()
        self.addCleanup(fake.reply.set)             # a worker wedged on the interrupt is freed at the end
        fake.hold_open = True
        # wire stamps track the wall clock, as the app-server's do: the settle is clock-stamped, and a
        # fixed-past fixture stamp would sort the SECOND turn's items ahead of it in the parse
        ms = int(time.time() * 1000)
        fake.scripts = [
            [item("t-1", ms, {"type": "userMessage", "id": "u-1",
                              "content": [{"type": "text", "text": "run the build"}]}),
             item("t-1", ms + 1000, {"type": "agentMessage", "id": "a-1", "text": "partial reply"})],
            [item("t-2", ms + 5000, {"type": "userMessage", "id": "u-2",
                                     "content": [{"type": "text", "text": "try again"}]}),
             item("t-2", ms + 6000, {"type": "agentMessage", "id": "a-2", "text": "ack: try again"}),
             ("turn/completed", {"threadId": "T-1",
                                 "turn": {"id": "t-2", "items": [], "status": "completed"}})]]
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        path = Path(be.transcript_path(sid))

        def recs():
            return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        self.assertTrue(be.send(sid, "run the build"))
        self.assertTrue(until(lambda: any(r["type"] == "user" for r in recs())))
        fake.turn_queues["t-1"].put(RuntimeError("synthetic: app-server connection lost"))
        self.assertTrue(until(lambda: not be.busy(sid) and be.launch_error(sid) is not None),
                        "worker never settled; interrupt calls: %r" % fake.called("turn_interrupt"))
        self.assertIn("connection lost", be.launch_error(sid)["text"])
        rows = recs()
        self.assertEqual([r["type"] for r in rows], ["user", "assistant", "assistant"])
        held, settle = rows[1], rows[2]
        self.assertEqual(held["message"]["content"][0]["text"], "partial reply")
        self.assertIsNone(held["message"]["stop_reason"])       # the turn genuinely didn't settle
        self.assertEqual(settle["message"]["stop_reason"], "end_turn")
        self.assertTrue(settle.get("isApiErrorMessage"))
        self.assertIn("connection lost", settle["message"]["content"][0]["text"])
        self.assertEqual(settle["parentUuid"], held["uuid"])
        self.assertEqual(fake.called("turn_interrupt"), [])
        # the next prompt opens its OWN turn: the event model reads the abandoned one as ended
        self.assertTrue(be.send(sid, "try again"))
        self.assertTrue(until(lambda: not be.busy(sid) and len(recs()) == 5))
        parsed = em.parse_session(str(path), rompuuid=sid, name="web", dir="/TESTDIR",
                                  candidate_files=[str(path)], sdk_human=True)
        self.assertEqual([t["ended"] for t in parsed["turns"]], [True, True])
        be.kill(sid)

    def test_turn_abandoned_by_a_failed_write_is_interrupted(self):
        """The other way a turn is abandoned after its ACK: the transcript append raises (a full disk)
        with the app-server alive and still running the instruction. The turn is told to stop (the
        transport is up, so the reply comes), the settle's own failed write is logged, and launch_error
        names the original fault, not the settle's."""
        logs = []
        fake = FakeClient()
        be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: fake, log=logs.append)
        sid = be.spawn("web", "/TESTDIR")

        writes = []
        def failing_append(s, recs):
            # the loop's write fails first; the settle's own write fails with a DIFFERENT error, so the
            # test can tell which of the two launch_error and the log each carry
            writes.append(recs)
            raise (OSError(28, "No space left on device") if len(writes) == 1
                   else OSError(5, "Input/output error"))
        be._append = failing_append
        self.assertTrue(be.send(sid, "write fails"))
        self.assertTrue(until(lambda: not be.busy(sid) and be.launch_error(sid) is not None))
        self.assertIn("No space left", be.launch_error(sid)["text"])
        self.assertNotIn("Input/output", be.launch_error(sid)["text"])
        self.assertEqual([c[2] for c in fake.called("turn_interrupt")], ["t-1"])
        settle_logs = [m for m in logs if m.startswith("abandoned turn settle")]
        self.assertEqual(len(settle_logs), 1, logs)
        self.assertIn("Input/output error", settle_logs[0])
        self.assertEqual(len(writes), 2)             # the loop's, then the settle's; nothing else wrote
        be.kill(sid)

    def test_kill_resume_roundtrip(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "one")
        self.assertTrue(until(lambda: not be.busy(sid)))
        self.assertTrue(be.kill(sid))
        self.assertFalse(be.owns(sid))
        self.assertNotIn(sid, be.live_sessions())
        self.assertTrue(be.resume("web", sid))
        self.assertTrue(be.owns(sid))
        be.send(sid, "two")
        self.assertTrue(until(lambda: len(fake.called("thread_resume")) == 1))
        resume_params = fake.called("thread_resume")[0][2]
        self.assertEqual(resume_params["permissions"], "romp_workspace")
        self.assertEqual(resume_params["runtimeWorkspaceRoots"], ["/TESTDIR"])
        self.assertNotIn("sandbox", resume_params)
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))

    def test_turn_start_failure_keeps_the_unacknowledged_batch_for_retry(self):
        class FailOnceClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.attempts = []
                self.failed = threading.Event()
                self.retry_entered = threading.Event()
                self.allow_retry = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.attempts.append([i["text"] for i in input_items])
                if len(self.attempts) == 1:
                    self.failed.set()
                    raise RuntimeError("synthetic pre-ack failure")
                self.retry_entered.set()
                self.allow_retry.wait(5)
                return super().turn_start(tid, input_items, params)

        fake = FailOnceClient()
        be, _, tmp = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        # Build one deterministic two-message batch before allowing the worker to start.
        ensure = be._ensure_worker
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "first"))
        self.assertTrue(be.send(sid, "second"))
        be._ensure_worker = ensure
        self.assertTrue(be.wake(sid))
        self.assertTrue(fake.failed.wait(2))
        self.assertEqual(be.pending_queued(sid), ["first", "second"])
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["first", "second"])
        self.assertTrue(fake.retry_entered.wait(2))
        fake.allow_retry.set()
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(fake.attempts, [["first", "second"], ["first", "second"]])

    def test_turn_ack_persistence_failure_cleans_up_and_retries_the_durable_batch(self):
        class BlockingRetryClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.start_attempts = 0
                self.retry_entered = threading.Event()
                self.allow_retry = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.start_attempts += 1
                if self.start_attempts == 2:
                    self.retry_entered.set()
                    self.allow_retry.wait(5)
                return super().turn_start(tid, input_items, params)

        fake = BlockingRetryClient()
        be, _, tmp = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        save = be._save_registry
        failed = threading.Event()

        def fail_first_ack(s, **kwargs):
            if kwargs.get("queue_ack") is not None and not failed.is_set():
                failed.set()
                raise OSError("synthetic registry ACK failure")
            return save(s, **kwargs)

        be._save_registry = fail_first_ack
        self.assertTrue(be.send(sid, "survive ACK failure"))
        self.assertTrue(failed.wait(2))
        self.assertTrue(fake.retry_entered.wait(2))
        self.assertEqual(fake.called("unregister"), [("unregister", "t-1")])
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1")
        self.assertEqual(be.pending_queued(sid), ["survive ACK failure"])
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["survive ACK failure"])

        fake.allow_retry.set()
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(fake.start_attempts, 2)
        self.assertEqual([call[1] for call in fake.called("unregister")], ["t-1", "t-2"])
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), [])

    def test_permanent_turn_rejection_parks_without_spin_until_explicit_change(self):
        class InvalidParamsError(RuntimeError):
            def __init__(self, message):
                super().__init__(message)
                self.code = -32602

        class RejectingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.attempts = []

            def turn_start(self, tid, input_items, params=None):
                self.attempts.append((list(input_items), dict(params or {})))
                raise InvalidParamsError("model is not available")

        fake = RejectingClient()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(until(lambda: len(fake.attempts) == 1))
        time.sleep(0.65)  # exceeds the old 0.25s retry; a permanent rejection has no timer
        self.assertEqual(len(fake.attempts), 1)
        self.assertEqual(be.pending_queued(sid), ["keep this durable"])
        self.assertIn("model is not available", be.launch_error(sid)["text"])
        self.assertTrue(be.wake(sid))
        time.sleep(0.35)
        self.assertEqual(len(fake.attempts), 1, "an ordinary wake must not repeat a rejected RPC")
        self.assertTrue(be.set_model(sid, "gpt-5-fixed"))
        self.assertTrue(until(lambda: len(fake.attempts) == 2))
        self.assertEqual(fake.attempts[1][1]["model"], "gpt-5-fixed")
        time.sleep(0.65)
        self.assertEqual(len(fake.attempts), 2, "the replacement request parks if it is rejected too")
        self.assertTrue(be.kill(sid))

    def test_a_queue_parked_on_a_permanent_rejection_reads_not_busy_until_a_change_re_arms_it(self):
        # The kernel takes busy() as its authoritative "turn open" word: a model or effort pick parks behind
        # it (a Codex pick applies at the next turn_start), and its drain skips the session for as long as
        # busy() holds. A queue the worker parked on a permanent rejection read busy — non-empty, yet nothing
        # in flight and no timer — so the very pick that would have unparked it was parked in turn, with no
        # way out: this backend has no unqueue, and kill + resume re-arm the same queue with the same model
        # (review, 2026-09-11). Parked reads not busy; an explicit change re-arms the retry and reads busy
        # again from that event on, so a pick pressed after it parks behind the retry in press order.
        class InvalidParamsError(RuntimeError):
            def __init__(self, message):
                super().__init__(message)
                self.code = -32602

        class RejectingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.attempts = []
                self.retry_started = threading.Event()   # the accepted retry is held INSIDE turn_start
                self.release_retry = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.attempts.append((list(input_items), dict(params or {})))
                if (params or {}).get("model") != "gpt-5-fixed":
                    raise InvalidParamsError("model is not available")
                self.retry_started.set()
                self.release_retry.wait(5)
                return super().turn_start(tid, input_items, params)

        fake = RejectingClient()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(until(lambda: be.launch_error(sid) is not None))   # written with the park, under the lock
        self.assertEqual(len(fake.attempts), 1)
        self.assertEqual(be.pending_queued(sid), ["keep this durable"], "the send stays visible as queued")
        self.assertFalse(be.busy(sid), "a queue parked on a permanent rejection is neither in flight nor about to run")
        # The event is the explicit change, not the worker's wake: with the worker still asleep in kick.wait(),
        # a setter's generation bump alone re-arms the retry, and busy() says so before the worker clears the
        # rejection — so a send handed over right then never arms a hold waiting for a turn to open.
        s = be._session(sid)
        with s.lock:
            s.change_generation += 1
        self.assertTrue(be.busy(sid), "a moved generation is a retry about to run")
        with s.lock:
            s.change_generation -= 1
        self.assertFalse(be.busy(sid))
        self.assertTrue(be.set_model(sid, "gpt-5-fixed"))
        self.assertTrue(fake.retry_started.wait(5))
        self.assertTrue(be.busy(sid), "the retry the pick armed is in flight: busy, so a later pick parks behind it")
        fake.release_retry.set()
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(fake.attempts[1][1]["model"], "gpt-5-fixed")
        self.assertTrue(be.kill(sid))

    def test_permanent_placeholder_prepare_parks_until_cwd_change(self):
        class InvalidParamsError(RuntimeError):
            code = -32602

        class CwdRejectingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.prepare_attempts = []

            def thread_start(self, params=None):
                self.prepare_attempts.append(dict(params or {}))
                if (params or {}).get("cwd") != "/FIXED":
                    raise InvalidParamsError("unknown permission profile for cwd")
                return super().thread_start(params)

        fake = CwdRejectingClient()
        be, _, tmp = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")       # visible failed-* placeholder
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(until(lambda: len(fake.prepare_attempts) == 2))
        time.sleep(0.65)                          # exceeds the old automatic retry
        self.assertEqual(len(fake.prepare_attempts), 2)
        self.assertEqual(be.pending_queued(sid), ["keep this durable"])
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["keep this durable"])
        self.assertIn("thread preparation rejected", be.launch_error(sid)["text"])
        self.assertTrue(be.wake(sid))
        time.sleep(0.35)
        self.assertEqual(len(fake.prepare_attempts), 2,
                         "an ordinary wake must not repeat rejected thread preparation")
        self.assertTrue(be.resume("web", sid, cwd="/FIXED"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(len(fake.prepare_attempts), 3)
        self.assertEqual(fake.prepare_attempts[-1]["cwd"], "/FIXED")

    def test_permanent_resume_prepare_retries_on_new_client_generation(self):
        class InvalidRequestError(RuntimeError):
            code = -32600

        class ResumeRejectingClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.resume_attempts = 0

            def thread_resume(self, tid, params=None):
                self.resume_attempts += 1
                raise InvalidRequestError("stored thread is unavailable on this server")

        old, replacement = ResumeRejectingClient(), FakeClient()
        clients = [old, replacement]
        be, _, tmp = build(factory=lambda: clients.pop(0))
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.resume("web", sid))
        self.assertTrue(be.send(sid, "retry after replacement"))
        self.assertTrue(until(lambda: old.resume_attempts == 1))
        time.sleep(0.65)
        self.assertEqual(old.resume_attempts, 1)
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["retry after replacement"])
        with be._client_lock:
            be._record_client_failure_locked(RuntimeError("replace generation"), old)
            be._client_retry_at = 0.0
        self.assertTrue(be.available())
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(len(replacement.called("thread_resume")), 1)
        self.assertEqual(len(replacement.called("turn_start")), 1)

    def test_transient_thread_prepare_still_retries(self):
        class ResumeFailsOnceClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.resume_attempts = 0

            def thread_resume(self, tid, params=None):
                self.resume_attempts += 1
                if self.resume_attempts == 1:
                    raise RuntimeError("synthetic transient resume failure")
                return super().thread_resume(tid, params)

        fake = ResumeFailsOnceClient()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.resume("web", sid))
        self.assertTrue(be.send(sid, "retry transient prepare"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid), timeout=3))
        self.assertEqual(fake.resume_attempts, 2)

    def test_new_client_generation_retries_a_parked_permanent_rejection(self):
        class InvalidRequestError(RuntimeError):
            code = -32600

        class RejectOnceClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.rejected = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.rejected.set()
                raise InvalidRequestError("old server rejected request")

        old, replacement = RejectOnceClient(), FakeClient()
        clients = [old, replacement]
        be, _, _ = build(factory=lambda: clients.pop(0))
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "retry on replacement"))
        self.assertTrue(old.rejected.wait(2))
        with be._client_lock:
            be._record_client_failure_locked(RuntimeError("replace generation"), old)
            be._client_retry_at = 0.0
        self.assertTrue(be.available())
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(len(replacement.called("turn_start")), 1)

    def test_replacement_server_resumes_a_thread_the_dead_server_had_loaded(self):
        # `loaded` meant "thread/resume done in this process", but the process that has to know the
        # thread is the app-server, and the backend replaces that one on the fly when the pump sees
        # it die. The thread stayed "loaded" on a server that had never seen it, turn/start went out
        # with no thread/resume before it, and the app-server refuses that with "thread not found":
        # a permanent rejection, so every resend met the same refusal until a kernel restart. The fake
        # answers the way the pinned app-server does, so the turn completing proves the resume came
        # first; the old server started the thread itself and must not be resumed on the way there.
        class InvalidRequestError(RuntimeError):
            code = -32600

        class ThreadAwareClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.known = set()

            def thread_start(self, params=None):
                resp = super().thread_start(params)
                self.known.add(resp.thread.id)
                return resp

            def thread_resume(self, tid, params=None):
                self.known.add(tid)
                return super().thread_resume(tid, params)

            def turn_start(self, tid, input_items, params=None):
                if tid not in self.known:
                    raise InvalidRequestError("thread not found: %s" % tid)
                return super().turn_start(tid, input_items, params)

        old, replacement = ThreadAwareClient(), ThreadAwareClient()
        clients = [old, replacement]
        be, _, _ = build(factory=lambda: clients.pop(0))
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "first"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        self.assertEqual(len(old.called("turn_start")), 1)
        with be._client_lock:                  # the old app-server died: the pump's invalidation
            be._record_client_failure_locked(RuntimeError("server died"), old)
            be._client_retry_at = 0.0
        self.assertTrue(be.send(sid, "second"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)),
                        "the second turn never ran on the replacement server")
        resumes = replacement.called("thread_resume")
        self.assertEqual([r[1] for r in resumes], [be._session(sid).tid])
        self.assertEqual(len(replacement.called("turn_start")), 1)
        self.assertLess(replacement.calls.index(resumes[0]),
                        replacement.calls.index(replacement.called("turn_start")[0]))
        self.assertEqual(old.called("thread_resume"), [])

    def test_client_factory_failure_backs_off_then_recovers_queued_session(self):
        fake = FakeClient()
        attempts = []

        def flaky_factory():
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise RuntimeError("synthetic unavailable client")
            return fake

        # a HUGE backoff floor makes the no-hot-spin phase DETERMINISTIC (the r48 release
        # gate, twice on the same runner): the real 0.25s floor raced the main thread — a
        # descheduled runner let the LEGITIMATE 250ms retry fire before the assertion ran,
        # and the test read its own backoff expiring as a hot spin. With the floor pinned
        # high, a second attempt before the worker parks can only be a real hot spin.
        saved_floor = cb.CLIENT_RETRY_MIN
        cb.CLIENT_RETRY_MIN = 30.0
        self.addCleanup(setattr, cb, "CLIENT_RETRY_MIN", saved_floor)
        be, _, _ = build(factory=flaky_factory)
        sid = be.spawn("web", "/TESTDIR")
        s = dict(be._session_items())[sid]
        self.assertTrue(be.send(sid, "retry me"))
        self.assertTrue(until(lambda: len(attempts) == 1), "the first attempt fires")
        # Release the backoff only once the worker is PARKED in it (s.parked, set right before its
        # wait). The worker clears stale kicks just before parking, so a release kicked earlier —
        # after a fixed sleep, as this test did — was discarded whenever the runner descheduled the
        # worker between the failed attempt and that clear (its registry save and push sit there),
        # and the worker slept the whole floor while the assertion below timed out (pulls 1026 and
        # 991, 2026-09-08; reproduced by slowing that stretch). Waiting on the park makes the release
        # an event the worker cannot miss, whatever the runner's speed.
        self.assertTrue(until(lambda: s.parked.is_set(), timeout=20), "the worker parks in its backoff")
        self.assertEqual(len(attempts), 1, "unavailable client must not hot-spin before parking")
        with be._client_lock:
            be._client_retry_at = 0.0                  # the explicit release, not a timer race
        s.kick.set()
        # a GENEROUS bound: the recovery is event-shaped (the explicit release above is
        # the event), so only "eventually" matters — the 3s bound starved the worker
        # thread on a runner at load 20+ (the r63 release gate: a 55-minute full suite)
        self.assertTrue(until(lambda: len(attempts) >= 2 and not be.busy(sid), timeout=20))
        self.assertEqual(len(fake.called("thread_start")), 1,
                         "the pending placeholder must become a real Codex thread")
        self.assertFalse(be.pending_queued(sid))
        # A thread the server itself just created is loaded on that server: the next turn goes
        # straight to turn/start, with no thread/resume ahead of it. `loaded` counts only while its
        # recorded app-server generation matches the current one, so the create path has to record
        # the generation it created on; left unrecorded (None), every later turn would re-resume
        # the same live server first, an extra RPC per turn that one turn never shows.
        self.assertTrue(be.send(sid, "and again"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid), timeout=20))
        self.assertEqual(fake.called("thread_resume"), [],
                         "a thread the server itself created is never resumed on that server")
        self.assertEqual(len(fake.called("turn_start")), 2)

    def test_turn_end_pokes_after_busy_clears(self):
        # the kernel's parked-op drain wakes on the poke (2026-09-03): every in-loop poke fires while the
        # turn is still open (turn_id set → busy() True), so the finally must poke once more AFTER it clears,
        # or a parked op on a Codex session waits out the pusher's backstop instead of firing on the event
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        seen = []
        real = be.poke
        be.poke = lambda: (seen.append(be.busy(sid)), real())
        self.assertTrue(be.send(sid, "one"))
        self.assertTrue(until(lambda: not be.busy(sid)))
        self.assertTrue(until(lambda: False in seen), "a poke observed the settled turn (busy() False)")

    def test_kill_interrupts_an_active_turn_and_worker_exits(self):
        be, fake, _ = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "keep running"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "keep running")
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        worker = be._sessions[sid].worker
        self.assertTrue(be.kill(sid))
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1")
        self.assertTrue(until(lambda: not worker.is_alive()))
        self.assertFalse(be.owns(sid))

    def test_kill_cleans_up_an_active_turn_when_dead_registry_write_fails(self):
        be, fake, tmp = build()
        fake.hold_open = True
        fake.scripts = [[("item/completed",
                          {"threadId": "T-1", "turnId": "t-1", "completedAtMs": 1781100000000,
                           "item": {"type": "userMessage", "id": "u-1",
                                    "content": [{"type": "text", "text": "keep running"}]}})]]
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "keep running"))
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"))
        worker = be._session(sid).worker
        save = be._save_registry

        def fail_dead_save(s, **kwargs):
            if "dead" in kwargs.get("fields", ()):
                raise OSError("synthetic dead-state persistence failure")
            return save(s, **kwargs)

        be._save_registry = fail_dead_save
        with self.assertRaisesRegex(OSError, "synthetic dead-state persistence failure"):
            be.kill(sid)
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1")
        self.assertTrue(until(lambda: not worker.is_alive()))
        self.assertFalse(be.owns(sid))
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertFalse(rows[sid]["dead"], "the failed durable mutation must not pretend it landed")

    def test_kill_can_mark_dead_while_turn_start_is_in_flight_then_interrupts_the_ack(self):
        class BlockingStartClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.start_entered = threading.Event()
                self.release_start = threading.Event()

            def turn_start(self, tid, input_items, params=None):
                self.start_entered.set()
                self.release_start.wait(2)
                return super().turn_start(tid, input_items, params)

        fake = BlockingStartClient()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "start slowly"))
        self.assertTrue(fake.start_entered.wait(1))
        result = []
        killer = threading.Thread(target=lambda: result.append(be.kill(sid)))
        killer.start()
        self.assertTrue(until(lambda: not be.owns(sid), timeout=0.5),
                        "turn/start must not hold the session lock needed by kill")
        fake.release_start.set()
        killer.join(2)
        self.assertFalse(killer.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(fake.called("turn_interrupt")[0][2], "t-1",
                         "an ACK that races kill must be interrupted as soon as its id exists")

    def test_concurrent_worker_ensure_starts_exactly_one_thread(self):
        be, _, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._sessions[sid]
        started = []
        release = threading.Event()

        def parked_worker(session):
            started.append(threading.get_ident())
            release.wait(5)

        be._work = parked_worker
        callers = [threading.Thread(target=be._ensure_worker, args=(s,)) for _ in range(20)]
        for t in callers:
            t.start()
        for t in callers:
            t.join(2)
        self.assertEqual(len(started), 1)
        release.set()
        self.assertTrue(until(lambda: not s.worker.is_alive()))

    def test_registry_and_chain_survive_backend_restart(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "first")
        self.assertTrue(until(lambda: not be.busy(sid)))
        # a NEW backend over the same state dir (kernel restart): same session, resumed lazily,
        # and the file's uuid chain continues off the pre-restart tail (_tail_state re-anchor)
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertTrue(be2.owns(sid))
        be2.send(sid, "second")
        self.assertTrue(until(lambda: not be2.busy(sid) and not be2.pending_queued(sid)))
        recs = [json.loads(l) for l in Path(be2.transcript_path(sid)).read_text().splitlines()]
        for prev, r in zip(recs, recs[1:]):
            self.assertEqual(r["parentUuid"], prev["uuid"],
                             "chain broke across the restart at %s" % r["uuid"])
        self.assertEqual(len({r["uuid"] for r in recs}), len(recs))

    def test_a_torn_trailing_line_does_not_swallow_the_next_record(self):
        # A torn write (write(2) short under ENOSPC, a kill between pages) leaves the transcript ending
        # mid-record with no newline. The next batch's first record was written straight after the
        # fragment: one unparseable line every reader skips, so the record vanished — after a restart
        # that is the user's next prompt — while _append still retired its echo by text, leaving the
        # prompt nowhere in the UI. The fix closes the fragment's line before writing.
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "first"))
        self.assertTrue(until(lambda: not be.busy(sid)))
        p = Path(be.transcript_path(sid))
        whole = p.read_text()
        before = [json.loads(l) for l in whole.splitlines()]
        self.assertEqual([r["type"] for r in before], ["user", "assistant"])
        fragment = json.dumps({"type": "user", "uuid": "11111111-2222-4333-8444-555555555555",
                               "parentUuid": before[-1]["uuid"], "timestamp": "2026-06-01T00:00:00.000Z",
                               "message": {"role": "user", "content": "a record the tear cut short"}})[:60]
        p.write_text(whole + fragment)          # the tear: a partial record, no line end
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fake)   # the restart after the kill
        self.assertTrue(be2.send(sid, "now add a test"))
        self.assertTrue(until(lambda: not be2.busy(sid) and not be2.pending_queued(sid)))
        lines = p.read_text().split("\n")
        parsed = []
        for l in lines:
            try:
                parsed.append(json.loads(l))
            except ValueError:
                pass                             # the fragment: skipped, as every reader skips it
        landed = [r for r in parsed if r["type"] == "user" and "now add a test" in json.dumps(r)]
        self.assertEqual(len(landed), 1, "the prompt sent after the torn line is not in the transcript")
        self.assertIn(fragment, lines, "the fragment must stay its own line, not carry the next record")
        # the chain continues off the last WHOLE record; the fragment is not a link
        self.assertEqual(landed[0]["parentUuid"], before[-1]["uuid"])
        # and the echo retire that ran for the landed prompt is now a retire for a record readers can see
        self.assertTrue(until(lambda: be2.live_atoms(sid) == []))

    def test_load_registry_logs_malformed_json_and_falls_back_empty(self):
        tmp = tempfile.mkdtemp()
        root = Path(tmp) / "codex"
        root.mkdir(parents=True)
        (root / "registry.json").write_text("{synthetic malformed json")
        logs = []

        be = cb.CodexBackend(tmp, client_factory=lambda: None, log=logs.append)

        self.assertEqual(be._session_items(), [])
        self.assertEqual(len(logs), 1)
        self.assertIn("codex registry unreadable at load", logs[0])
        self.assertIn("existing sessions will be missing until it is repaired", logs[0])

    def test_load_registry_logs_valid_non_object_roots_and_falls_back_empty(self):
        for value in ([], None, True, 7, "synthetic-root"):
            with self.subTest(value=value):
                tmp = tempfile.mkdtemp()
                root = Path(tmp) / "codex"
                root.mkdir(parents=True)
                (root / "registry.json").write_text(json.dumps(value))
                logs = []

                be = cb.CodexBackend(tmp, client_factory=lambda: None, log=logs.append)

                self.assertEqual(be._session_items(), [])
                self.assertEqual(len(logs), 1)
                self.assertIn("codex registry unreadable at load", logs[0])
                self.assertIn("registry root is not an object", logs[0])

    def test_stale_backend_metadata_save_cannot_erase_newer_durable_queue(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        stale = cb.CodexBackend(tmp, client_factory=lambda: fake)
        ensure = be._ensure_worker
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "must survive stale save"))
        be._ensure_worker = ensure
        self.assertEqual(be.pending_queued(sid), ["must survive stale save"])
        self.assertTrue(stale.rename(sid, "web-renamed"))
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["must survive stale save"])
        self.assertEqual(rows[sid]["name"], "web-renamed")

    def test_same_process_metadata_mutations_commit_in_session_order(self):
        be, _, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        snapshotted, release = threading.Event(), threading.Event()
        snapshot = be._registry_snapshot

        def gated_snapshot(s):
            row = snapshot(s)
            if row["model"] == "gpt-a" and not snapshotted.is_set():
                snapshotted.set()
                release.wait(5)
            return row

        be._registry_snapshot = gated_snapshot
        first = threading.Thread(target=be.set_model, args=(sid, "gpt-a"))
        second = threading.Thread(target=be.set_model, args=(sid, "gpt-b"))
        first.start()
        self.assertTrue(snapshotted.wait(2))
        second.start()
        time.sleep(0.05)
        self.assertTrue(second.is_alive(), "the later mutation must wait through the first save")
        release.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(be._session(sid).model, "gpt-b")
        self.assertEqual(rows[sid]["model"], "gpt-b")

    def test_delayed_ack_cannot_delete_a_new_identical_text_send(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "same text"))
        self.assertTrue(be.kill(sid))          # let the stale backend load without consuming the queue
        stale = cb.CodexBackend(tmp, client_factory=lambda: fake)

        current = be._session(sid)
        with current.lock:
            old_id = current.queue_ids[0]
            del current.queue[:1]
            del current.queue_ids[:1]
            self.assertFalse(be._save_registry(current, queue_ack=[old_id]))
        self.assertTrue(be.resume("web", sid))
        self.assertTrue(be.send(sid, "same text"))
        with current.lock:
            new_id = current.queue_ids[0]
        self.assertNotEqual(new_id, old_id)

        old = stale._session(sid)
        with old.lock:
            self.assertEqual(old.queue_ids, [old_id])
            del old.queue[:1]
            del old.queue_ids[:1]
            mismatch = stale._save_registry(old, queue_ack=[old_id])
        self.assertTrue(mismatch, "the delayed ACK must reject the newer entry id")
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["same text"])
        self.assertEqual([entry["id"] for entry in registry_queue_entries(rows, sid)], [new_id])

        self.assertTrue(be.kill(sid))          # a dead restart leaves the surviving queue observable
        restarted = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertEqual(restarted.pending_queued(sid), ["same text"])
        self.assertEqual(restarted._session(sid).queue_ids, [new_id])

    def test_legacy_string_queue_migrates_with_stable_ids_across_restart(self):
        tmp = tempfile.mkdtemp()
        root = Path(tmp) / "codex"
        root.mkdir(parents=True)
        sid = "legacy-session"
        (root / "registry.json").write_text(json.dumps({sid: {
            "tid": "legacy-thread", "name": "old", "cwd": "/TESTDIR", "dead": True,
            "queue": ["repeat", "repeat"],
        }}))
        first = cb.CodexBackend(tmp, client_factory=lambda: None)
        overlap = cb.CodexBackend(tmp, client_factory=lambda: None)
        self.assertEqual(first.pending_queued(sid), ["repeat", "repeat"])
        ids = list(first._session(sid).queue_ids)
        self.assertEqual(overlap._session(sid).queue_ids, ids)
        self.assertEqual(len(set(ids)), 2)

        self.assertTrue(first.rename(sid, "migrated"))  # any transaction lazily upgrades the row
        rows = json.loads((root / "registry.json").read_text())
        entries = registry_queue_entries(rows, sid)
        self.assertTrue(all(entry["id"] and entry["text"] for entry in entries))
        self.assertEqual([entry["id"] for entry in entries], ids)
        restarted = cb.CodexBackend(tmp, client_factory=lambda: None)
        self.assertEqual(restarted.pending_queued(sid), ["repeat", "repeat"])
        self.assertEqual(restarted._session(sid).queue_ids, ids)

    def test_other_backend_append_survives_exact_prefix_ack(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        other = cb.CodexBackend(tmp, client_factory=lambda: fake)  # stale before the queue changes
        ensure = be._ensure_worker
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "accepted prefix"))
        be._ensure_worker = ensure
        other._save_registry(other._session(sid),
                             queue_append={"id": "other-suffix", "text": "concurrent suffix"})
        s = be._session(sid)
        with s.lock:
            self.assertEqual(s.queue, ["accepted prefix"])
            prefix_id = s.queue_ids[0]
            del s.queue[:1]                 # the same mutation made after turn/start ACK
            del s.queue_ids[:1]
            be._save_registry(s, fields=("launchError",), queue_ack=[prefix_id])
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(registry_queue_texts(rows, sid), ["concurrent suffix"])

    def test_concurrent_local_sends_keep_memory_and_registry_order(self):
        be, _, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        be._ensure_worker = lambda s: None
        entered, release = threading.Event(), threading.Event()
        save = be._save_registry

        def gated_save(s, **kwargs):
            if (kwargs.get("queue_append") or {}).get("text") == "first":
                entered.set()
                release.wait(5)
            return save(s, **kwargs)

        be._save_registry = gated_save
        first = threading.Thread(target=be.send, args=(sid, "first"))
        second = threading.Thread(target=be.send, args=(sid, "second"))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        time.sleep(0.05)
        self.assertTrue(second.is_alive(), "the second append must wait behind the first transaction")
        release.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        self.assertEqual(be.pending_queued(sid), ["first", "second"])
        self.assertEqual(registry_queue_texts(rows, sid), ["first", "second"])

    def test_registry_queue_appends_are_atomic_across_processes(self):
        be, _, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.kill(sid))       # child backends load it without starting queue workers
        code = r'''
import sys
sys.path.insert(0, sys.argv[5])          # the tests dir, where romp_load lives
from romp_load import load_source
cb = load_source("codex_child", sys.argv[1])
be = cb.CodexBackend(sys.argv[2], client_factory=lambda: None, log=lambda message: None)
s = be._session(sys.argv[3])
for i in range(20):
    text = "%s:%d" % (sys.argv[4], i)
    be._save_registry(s, queue_append={"id": "child-" + text, "text": text})
'''
        backend_path = str(Path(ROOT) / "kernel" / "codex_backend.py")
        procs = [subprocess.Popen([sys.executable, "-c", code, backend_path, tmp, sid, str(n), HERE])
                 for n in range(4)]
        for p in procs:
            self.assertEqual(p.wait(timeout=15), 0)
        rows = json.loads((Path(tmp) / "codex" / "registry.json").read_text())
        entries = registry_queue_entries(rows, sid)
        self.assertEqual(len(entries), 80)
        self.assertEqual(len({entry["id"] for entry in entries}), 80)
        self.assertEqual(len({entry["text"] for entry in entries}), 80)

    def test_backend_restart_rearms_a_persisted_queue_without_another_send(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        ensure = be._ensure_worker
        be._ensure_worker = lambda s: None
        self.assertTrue(be.send(sid, "survive restart"))
        be._ensure_worker = ensure
        self.assertEqual(be.pending_queued(sid), ["survive restart"])
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertTrue(until(lambda: not be2.busy(sid) and not be2.pending_queued(sid)))
        self.assertIn("survive restart", Path(be2.transcript_path(sid)).read_text())

    def test_launch_error_survives_backend_restart(self):
        def bad_factory():
            raise RuntimeError(cb.LOGIN_HINT)
        be, _, tmp = build(factory=bad_factory)
        sid = be.spawn("web", "/TESTDIR")
        be2 = cb.CodexBackend(tmp, client_factory=bad_factory)
        self.assertIn("codex login", be2.launch_error(sid)["text"])

    def test_missing_login_is_loud_not_silent(self):
        def bad_factory():
            raise RuntimeError(cb.LOGIN_HINT)
        be, _, _ = build(factory=bad_factory)
        sid = be.spawn("web", "/TESTDIR")
        self.assertIsNotNone(sid)                       # the session EXISTS, visibly broken
        err = be.launch_error(sid)
        self.assertIn("codex login", err["text"])
        self.assertFalse(err["limit"])
        self.assertFalse(be.available())

    def test_a_handshake_that_never_answers_is_ended_and_named(self):
        # A codex that starts, holds stdout open and never writes its first frame (a start-up stalled on a hung
        # mount, a stub that sleeps) left _get_client in the SDK's untimed wait with _client_lock HELD: every
        # Codex creation, resume, send and /models read queued behind it, the tab whose receive loop made the
        # call read no more ops, and nothing was logged or recorded (2026-09-11). The handshake now runs under
        # a clock whose expiry ends the child, the one event the SDK's wait answers to, and names the reason.
        # The reason then stands for at least the clock: a re-probe costs the whole clock again, lock held, so
        # the ordinary backoff (cap 5s) would have re-run it almost continuously while the fault lasted.
        class SilentClient(FakeClient):
            """Every account_read parks until the next close(): the shape of a live child that never answers,
            however often it is probed. (The injected path has no start/initialize; account_read is the
            handshake call both paths share.)"""

            def __init__(self):
                super().__init__()
                self.gate = threading.Event()

            def account_read(self, *a, **k):
                self._rec("account_read")
                self.gate = gate = threading.Event()
                gate.wait()
                raise RuntimeError("Codex process closed stdout.")   # what the SDK's wait raises after close()

            def close(self):
                super().close()
                self.gate.set()
        saved = getattr(cb, "HANDSHAKE_TIMEOUT_S", None)
        cb.HANDSHAKE_TIMEOUT_S = 0.3                     # the clock under test
        self.addCleanup(setattr, cb, "HANDSHAKE_TIMEOUT_S", saved)
        fake = SilentClient()
        self.addCleanup(lambda: fake.gate.set())         # without the clock the probe parks forever; let it out
        logs = []
        be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: fake, log=logs.append)
        out = []
        probe = threading.Thread(target=lambda: out.append(be.available()), name="probe", daemon=True)
        probe.start()
        probe.join(5)
        self.assertFalse(probe.is_alive(), "available() must return once the handshake clock runs out")
        self.assertEqual(out, [False])
        self.assertTrue(fake.called("close"), "the clock ends the child; that is what unblocks the SDK's wait")
        self.assertIn("did not answer", be._client_err or "")
        recorded = [m for m in logs if "did not answer" in m]
        self.assertTrue(recorded, logs)
        delay = float(re.search(r"retry in ([\d.]+)s", recorded[0]).group(1))
        self.assertGreaterEqual(delay, cb.HANDSHAKE_TIMEOUT_S,
                                "a probe that costs the whole clock is not re-run before the clock: %s" % recorded[0])
        sid = be.spawn("web", "/TESTDIR")                 # the session exists, visibly broken, with the reason
        self.assertIn("did not answer", be.launch_error(sid)["text"])

    def test_a_handshake_ended_by_its_clock_names_the_reason_not_the_transport(self):
        # On a real client the parked call is initialize() inside bring_up: the clock's close() ends the child,
        # the SDK's reader fails its waiter with the transport's text ("Codex process closed stdout. stderr_tail=
        # ...") and that RAISES out of the handshake, the branch the injected path above never reaches (its
        # account_read error is swallowed by the login check). The user reads the plain reason, not the
        # transport's text and a stderr tail; the transport error stays attached as the cause.
        class EndedClient(FakeClient):
            def __init__(self):
                super().__init__()
                self.ended = threading.Event()

            def close(self):
                super().close()
                self.ended.set()
        fake = EndedClient()
        transport = RuntimeError("Codex process closed stdout. stderr_tail=")   # the SDK's TransportClosedError shape

        def bring_up():                                   # start + initialize on a real client: parks until the close
            fake.ended.wait()
            raise transport
        saved = getattr(cb, "HANDSHAKE_TIMEOUT_S", None)
        cb.HANDSHAKE_TIMEOUT_S = 0.3
        self.addCleanup(setattr, cb, "HANDSHAKE_TIMEOUT_S", saved)
        self.addCleanup(fake.ended.set)
        be, _, _ = build(factory=lambda: fake)
        with self.assertRaises(RuntimeError) as cm:
            be._handshake(fake, bring_up)
        self.assertIsInstance(cm.exception, cb._HandshakeTimeout)   # the class the record floors the retry on
        self.assertIn("did not answer", str(cm.exception))
        self.assertNotIn("stderr_tail", str(cm.exception))
        self.assertIs(cm.exception.__cause__, transport)
        self.assertTrue(fake.called("close"), "the clock ended the child")
        self.assertEqual(fake.called("account_read"), [], "the login check never ran: the raise came first")

    def test_a_handshake_that_settles_first_dismisses_its_clock(self):
        # The other side of the gate: a good handshake cancels its clock, so no clock fires after it settled to
        # close the installed client. The clock's thread ending is the event; a live one could still fire.
        saved = getattr(cb, "HANDSHAKE_TIMEOUT_S", None)
        cb.HANDSHAKE_TIMEOUT_S = 0.2
        self.addCleanup(setattr, cb, "HANDSHAKE_TIMEOUT_S", saved)
        be, fake, _ = build()
        self.assertTrue(be.available())
        self.assertTrue(until(lambda: not any(t.name == "codex-handshake-clock" for t in threading.enumerate())),
                        "a settled handshake dismisses its clock")
        self.assertEqual(fake.called("close"), [], "a clock dismissed by the handshake never ends the client")
        self.assertIs(be._client, fake)

    def test_claude_only_knobs_refuse(self):
        be, _, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_effort(sid, "xhigh"))    # Codex takes xhigh natively
        self.assertFalse(be.set_effort(sid, "max"))     # not advertised by this synthetic model
        self.assertFalse(be.set_fast(sid, "on"))
        self.assertFalse(be.set_mode(sid, "plan"))
        self.assertFalse(be.set_auth(sid, "key"))
        self.assertFalse(be.rewind_files(sid, "u1"))
        self.assertTrue(be.set_model(sid, "gpt-5-codex"))
        self.assertEqual(be.live_sessions()[sid]["model"], "gpt-5-codex")
        # a Claude alias would 400 the next turn — refused here so the kernel warns instead
        self.assertFalse(be.set_model(sid, "sonnet"))
        self.assertEqual(be.live_sessions()[sid]["model"], "gpt-5-codex")

    def test_client_launch_defines_the_fail_closed_workspace_profile(self):
        captured = []

        class CaptureConfig:
            def __init__(self, **kwargs):
                captured.append(kwargs)

        cb._codex_config(CaptureConfig, "/opt/codex")
        self.assertEqual(captured[0]["codex_bin"], "/opt/codex")
        self.assertEqual(captured[0]["client_name"], "romp")
        overrides = captured[0]["config_overrides"]
        profile = overrides[0]
        self.assertIn('"/opt/codex" = "read"', profile)
        self.assertNotIn('"/opt" = "read"', profile)
        self.assertIn('":minimal" = "read"', profile)
        self.assertIn('"." = "write"', profile)
        for metadata in (".git", ".agents", ".codex"):
            self.assertNotIn('"%s"' % metadata, profile)
        self.assertIn("network = { enabled = true }", profile)
        self.assertEqual(overrides[1], 'default_permissions="romp_workspace"')

    def test_model_catalog_from_app_server(self):
        be, fake, _ = build()
        cat = be.model_catalog()
        self.assertEqual(cat, MODEL_CHOICES)
        be.model_catalog()
        self.assertEqual(len(fake.called("model_list")), 1, "catalog is fetched once, then cached")
        self.assertIsNone(be.model_catalog_error(), "a held catalog carries no error")

    def test_efforts_come_from_each_models_catalog_including_future_values(self):
        from enum import Enum
        class Level(str, Enum):
            ultra = "ultra"
        be, fake, _ = build()
        fake.model_list = lambda: SimpleNamespace(data=[
            SimpleNamespace(id="gpt-test-web", model="gpt-test-web", display_name="Web", hidden=False,
                            is_default=True, supported_reasoning_efforts=[
                                SimpleNamespace(reasoning_effort=Level.ultra, description="Detailed reasoning"),
                                SimpleNamespace(reasoning_effort="future-level", description="New level")]),
            SimpleNamespace(id="gpt-test-api", model="gpt-test-api", display_name="API", hidden=False,
                            is_default=False, supported_reasoning_efforts=[
                                SimpleNamespace(reasoning_effort="low", description="Quick")])])
        rows = be.model_catalog()
        self.assertEqual(rows[0]["efforts"], [
            {"value": "ultra", "label": "ultra", "sub": "Detailed reasoning"},
            {"value": "future-level", "label": "future-level", "sub": "New level"}])
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_model(sid, "gpt-test-web"))
        self.assertTrue(be.set_effort(sid, "ultra"))
        self.assertTrue(be.set_effort(sid, "future-level"))
        self.assertFalse(be.set_effort(sid, "low"))
        self.assertEqual(be.live_sessions()[sid]["effort"], "future-level")
        self.assertTrue(be.set_model(sid, "gpt-test-api"))
        self.assertFalse(be.set_effort(sid, "ultra"))
        self.assertTrue(be.set_effort(sid, "low"))

    def test_effort_refuses_unknown_model_or_unavailable_catalog(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_model(sid, "gpt-not-listed"))
        self.assertFalse(be.set_effort(sid, "high"))
        self.assertEqual(be.live_sessions()[sid]["effort"], "")
        fake.model_list = lambda: SimpleNamespace(data=[])
        be._catalog = None
        self.assertTrue(be.set_model(sid, "gpt-5-test"))
        self.assertFalse(be.set_effort(sid, "high"))
        self.assertEqual(be.live_sessions()[sid]["effort"], "")

    def test_effort_uses_catalog_default_alias_and_model_after_catalog_read(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        rows = [{"value": "gpt-test-choice", "model": "gpt-test-alias", "isDefault": True,
                 "efforts": [{"value": "ultra"}]}]
        with be._session(sid).lock:
            be._session(sid).model = ""
        with mock.patch.object(be, "model_catalog", return_value=rows):
            self.assertTrue(be.set_effort(sid, "ultra"), "unset model uses the advertised default")
            self.assertTrue(be.set_model(sid, "gpt-test-alias"))
            self.assertTrue(be.set_effort(sid, "ultra"), "the app-server model alias also matches")
        def changed():
            be.set_model(sid, "gpt-not-listed")
            return rows
        with mock.patch.object(be, "model_catalog", side_effect=changed):
            self.assertFalse(be.set_effort(sid, "ultra"), "validate after the possibly blocking catalog read")

    def test_model_catalog_empty_answer_is_not_cached_and_is_named(self):
        # The picker opened on a blank menu and stayed blank for the life of the kernel: the app-server's
        # first answer was an empty page, and `[] is not None`, so the empty list was cached as the catalog.
        # Only a non-empty list is held; an empty answer is named and the next read asks again.
        be, fake, _ = build()
        logged = []
        be.log = logged.append
        real = fake.model_list
        pages, asked = [SimpleNamespace(data=[])], []

        def paged(*a, **k):
            asked.append(1)
            return pages.pop() if pages else real(*a, **k)
        fake.model_list = paged
        self.assertEqual(be.model_catalog(), [])
        self.assertEqual(be.model_catalog(), MODEL_CHOICES,
                         "an empty answer is not the catalog: the next read asks the app-server again")
        self.assertEqual(len(asked), 2)
        self.assertEqual(logged.count("the Codex app-server listed no models"), 1, "the empty answer is named, once")
        self.assertIsNone(be.model_catalog_error(), "the held list clears the reason")
        be.model_catalog()
        self.assertEqual(len(asked), 2, "the non-empty list is the one that is cached")

    def test_model_catalog_failure_is_named_and_logged_once_per_reason(self):
        # model_list raised: the backend answered [] and logged a line per call, and the caller had no way to
        # read why. The reason is readable (model_catalog_error) and logged once per DISTINCT reason: the
        # kernel re-reads the catalog on every picker open, so a per-call line repeats for as long as the
        # fault lasts. A later good answer clears the reason.
        be, fake, _ = build()
        logged = []
        be.log = logged.append
        real = fake.model_list
        faults = [RuntimeError("app-server not ready"), RuntimeError("app-server not ready"), RuntimeError("pump died")]

        def flaky(*a, **k):
            if faults:
                raise faults.pop(0)
            return real(*a, **k)
        fake.model_list = flaky
        named = lambda: [l for l in logged if l.startswith("model_list failed")]
        self.assertEqual(be.model_catalog(), [])
        self.assertEqual(be.model_catalog_error(), "model_list failed: app-server not ready")
        self.assertEqual(be.model_catalog(), [], "the same fault again")
        self.assertEqual(named(), ["model_list failed: app-server not ready"], "one line for one reason, however many reads")
        self.assertEqual(be.model_catalog(), [])
        self.assertEqual(be.model_catalog_error(), "model_list failed: pump died")
        self.assertEqual(named(), ["model_list failed: app-server not ready", "model_list failed: pump died"],
                         "a different reason is a new line")
        self.assertEqual(be.model_catalog(), MODEL_CHOICES,
                         "the next read retries instead of serving the failed answer")
        self.assertIsNone(be.model_catalog_error())

    def test_model_catalog_without_a_client_names_the_client_failure(self):
        # _get_client() None (the factory failed; the client sits in its retry backoff) answered [] with
        # nothing logged and nothing for the caller to show. The reason carries the client's own failure text.
        be, _, _ = build(factory=lambda: (_ for _ in ()).throw(RuntimeError("codex login missing")))
        logged = []
        be.log = logged.append
        self.assertEqual(be.model_catalog(), [])
        want = "the Codex app-server client is unavailable: codex login missing"
        self.assertEqual(logged.count(want), 1, logged)
        self.assertEqual(be.model_catalog_error(), want)
        self.assertEqual(be.model_catalog(), [])
        self.assertEqual(logged.count(want), 1, "the same reason is logged once, not once per read")

    def test_deliver_and_wake_reach_the_agent(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.deliver(sid, "you have mail from web"))
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)))
        texts = Path(be.transcript_path(sid)).read_text().splitlines()
        self.assertTrue(any("you have mail" in t for t in texts))

    def test_push_session_may_reenter_live_sessions(self):
        # The kernel's push_session synchronously re-enters Sessions.live() → live_sessions(),
        # which takes every session's norm_lock. A notify issued while holding norm_lock
        # self-deadlocked the worker on its first appended record and wedged the whole liveness
        # merge behind it (2026-08-14 review, reproduced live). Wiring the reentrant push here is
        # the regression: with the notify under the lock, this test hangs and times out.
        tmp = tempfile.mkdtemp()
        fake = FakeClient()
        be = cb.CodexBackend(tmp, client_factory=lambda: fake,
                             push_session=lambda sid: be.live_sessions())
        sid = be.spawn("web", "/TESTDIR")
        be.send(sid, "hello reentrant push")
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid), timeout=10),
                        "worker wedged — a notify ran under a lock live_sessions() needs")
        recs = [json.loads(l) for l in Path(be.transcript_path(sid)).read_text().splitlines()]
        self.assertTrue(any(r["type"] == "assistant" for r in recs))
        self.assertTrue(be.kill(sid))   # kill's held-final drain notifies too — same reentry


class _Clock:
    """A clock the backend module reads through its `time` global: time() answers `now`, everything
    else (monotonic, sleep) is the real module's. Patched onto the backend MODULE for one send() call,
    never onto the time module, so nothing outside the backend sees it."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


class PruneLive(unittest.TestCase):
    """The kernel's _merge_live_atoms calls be.prune_live(sid, tx_uuids, tx_text_t, human_floor), four
    positional arguments, and CodexBackend.prune_live took three: every live merge of a Codex session
    holding an echo raised TypeError (the chat build and the feed's merge failed outright; the timeline
    bars logged a live-merge failure). The call shape is pinned across every backend in
    tests/test_backend_call_parity.py; this class covers the behaviour in the kernel's REAL shapes:
    record times are parse_z's whole seconds (the mapping's values are floats of them), and the echo's
    own stamp is int(time.time()), as the SDK echo stamps its own (and the removed tmux echo did)."""

    def _with_echo(self, text="ship it", t=1000):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        with s.lock:
            s.echoes.append({"text": text, "t": t, "uuid": "echo-11111111"})
        return be, sid

    def _sent(self, be, sid, text, now):
        """An echo through send() itself, on the steer path (an open turn: nothing queues, no worker
        thread runs), with the backend's clock reading `now`."""
        s = be._session(sid)
        with s.lock:
            s.turn_id = "t-live"
        with mock.patch.object(cb, "time", _Clock(now)):
            self.assertTrue(be.send(sid, text))

    def test_accepts_the_kernels_four_positional_arguments_and_no_floor_retires_a_plain_echo(self):
        be, sid = self._with_echo()
        be.prune_live(sid, frozenset(), {}, 2000)          # the exact caller shape: a set, a mapping, a floor
        self.assertEqual(len(be.live_atoms(sid)), 1,
                         "no floor retires a plain echo: a send the app-server never records must stay visible")

    def test_text_lands_only_through_a_record_at_or_after_the_send(self):
        # "ok" sent twice: the first record predates the second echo, so it must not retire it; a record
        # stamped at or after the send does. Whole-second record times, as the kernel derives them.
        be, sid = self._with_echo("ok", t=1000)
        be.prune_live(sid, frozenset(), {"ok": 999.0}, 0)
        self.assertEqual(len(be.live_atoms(sid)), 1, "an older record with the same text is not this send")
        be.prune_live(sid, frozenset(), {"ok": 1000.0}, 0)
        self.assertEqual(be.live_atoms(sid), [], "a record at the send's second lands it")

    def test_a_record_later_in_the_sends_own_second_lands_the_echo(self):
        # The echo is stamped in WHOLE seconds like the SDK's (and the removed tmux echo's). A float stamp (1000.3)
        # would keep an echo whose record was written at 1000.7: parse_z reads that record as 1000, and
        # 1000 >= 1000.3 is false, the same-second case the SDK retires.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self._sent(be, sid, "ok", 1000.3)
        atom, = be.live_atoms(sid)
        self.assertIsInstance(atom["t"], int)
        self.assertEqual(atom["t"], 1000)
        be.prune_live(sid, frozenset(), {"ok": 999.0}, 0)
        self.assertEqual(len(be.live_atoms(sid)), 1, "the second before the send is not this send")
        be.prune_live(sid, frozenset(), {"ok": 1000.0}, 0)
        self.assertEqual(be.live_atoms(sid), [], "a record later in the send's own second lands it")

    def test_text_compares_under_the_shared_key_rule_on_both_sides(self):
        # The ECHO side: an echo whose stored text carries outer whitespace (injected directly, past
        # send()'s own keying) still lands against the kernel's stripped key.
        be, sid = self._with_echo("  ship it\n", t=1000)
        be.prune_live(sid, frozenset(), {"ship it": 1001.0}, 0)
        self.assertEqual(be.live_atoms(sid), [], "the echo's text is keyed before the comparison")
        # The SET side: an older caller's plain set, unstripped, keyed the same way and unfloored.
        be2, sid2 = self._with_echo("ship it", t=1000)
        be2.prune_live(sid2, frozenset(), {"  ship it\n"}, 0)
        self.assertEqual(be2.live_atoms(sid2), [], "a plain set keeps the unfloored match, keyed the same way")

    def test_uuid_retires(self):
        be, sid = self._with_echo("ship it", t=1000)
        s = be._session(sid)
        with s.lock:
            s.echoes.append({"text": "and the tests", "t": 1000, "uuid": "echo-22222222"})
        be.prune_live(sid, frozenset({"echo-11111111"}), {}, 0)
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-22222222"], "by uuid, that echo only")

    def test_unknown_sid_is_a_no_op(self):
        be, _, _ = build()
        be.prune_live("7c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f", frozenset(), {}, 0)


class EchoAtoms(unittest.TestCase):
    """What the kernel reads off a Codex echo, and how the backend's own retire takes echoes back.
    `_echo_text` is the marker every kernel reader of an input echo keys on (SdkBackend's echo atoms carry
    it): _merge_live_atoms hides the echo behind its queued bubble and never counts it as live work, and
    build_session's queued-bubble pass enlists it while the session is busy. Without it a Codex echo would
    paint as a solid user atom beside its own queued bubble and force the last turn open (the merge itself
    is pinned in tests/test_codex_echo_merge.py). Each echo is ONE send: _append takes one echo per landed
    text BLOCK (a turn started from several queued sends lands as one record with a block per send), the
    oldest carrying the text, and send()'s dead path takes back only the echo it minted."""

    def test_the_echo_atom_is_marked_as_an_input_echo(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        with s.lock:
            s.turn_id = "t-live"                           # an open turn: send() steers, nothing queues
        self.assertTrue(be.send(sid, "  ship it\n"))
        atom, = be.live_atoms(sid)
        self.assertEqual(atom["_echo_text"], "ship it", "the sent text, under the shared key rule")
        self.assertEqual(atom["message"]["content"][0]["text"], "ship it")
        self.assertEqual(atom["author"], "human")
        self.assertNotIn("command", atom, "a plain echo carries no command flag")

    @staticmethod
    def _rec(kind, uid, text):
        return {"type": kind, "uuid": uid, "message": {"role": kind, "content": [{"type": "text", "text": text}]}}

    def test_a_landed_record_retires_one_echo_the_oldest_carrying_its_text(self):
        # One block is one send (each record here has one). Dropping EVERY echo carrying the landed text
        # loses the second echo of a text sent twice when the first record lands, and a second send dropped
        # after that (the client dying mid-queue) leaves nothing visible. The kernel's prune_live, floored
        # by record time, is the other retire; this one sees only the records it just wrote.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        with s.lock:
            s.echoes.extend([{"text": "ok", "t": 1000, "uuid": "echo-11111111"},
                             {"text": "ok", "t": 1001, "uuid": "echo-22222222"},
                             {"text": "ship it", "t": 1002, "uuid": "echo-33333333"}])
        with s.norm_lock:                                  # _append's contract: the caller holds it
            be._append(s, [self._rec("user", "u-1", "  ok \n")])   # the record side is keyed the same way
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-22222222", "echo-33333333"],
                         "one record, one echo: the oldest carrying its text")
        with s.norm_lock:
            be._append(s, [self._rec("user", "u-2", "ok"), self._rec("assistant", "a-1", "ship it")])
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-33333333"],
                         "the second record takes the second echo; an assistant record takes none")
        with s.norm_lock:
            be._append(s, [self._rec("user", "u-3", "ok")])
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-33333333"],
                         "a record with no echo left to take retires nothing else")

    @staticmethod
    def _two_block_record(uid="u-1"):
        """Shaped like the record the normalizer writes for a turn started from two queued sends (the
        app-server's one userMessage item, a block per input; codex_events._user_input_texts), with one
        block left padded so the key rule on the record side is exercised."""
        return {"type": "user", "uuid": uid,
                "message": {"role": "user", "content": [{"type": "text", "text": "first send"},
                                                         {"type": "text", "text": "  second send\n"}]}}

    def test_a_two_block_record_retires_one_echo_per_block(self):
        # A turn started from two queued sends lands one record with a block per send. One echo per block,
        # the oldest carrying the text: a joined reading of the record ("first send second send") matches
        # neither echo and both stay live for good, and taking every echo carrying a landed text would take
        # a later repeat of the first send with it.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        with s.lock:
            s.echoes.extend([{"text": "first send", "t": 1000, "uuid": "echo-11111111"},
                             {"text": "second send", "t": 1000, "uuid": "echo-22222222"},
                             {"text": "third send", "t": 1001, "uuid": "echo-33333333"},
                             {"text": "first send", "t": 1002, "uuid": "echo-44444444"}])   # sent again, later
        with s.norm_lock:
            be._append(s, [self._two_block_record()])
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-33333333", "echo-44444444"],
                         "one record with two blocks retires two echoes, one per block, the oldest of each")

    def test_a_joined_text_echo_does_not_match_a_two_block_record(self):
        # Per block, never the blocks joined: an echo spelling both texts in ONE message is a third send
        # the app-server has not recorded, and a joined key (space- or newline-joined) would retire it
        # against the two-send record. (The kernel's prune_live does match the joined text, floored by
        # record time; this pins the backend's own retire.) The record is spelled unpadded so a space-join
        # of its blocks is exactly the first echo's text: a padded block would join to three spaces, match
        # nothing, and pass for the wrong reason.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        with s.lock:
            s.echoes.extend([{"text": "first send second send", "t": 1000, "uuid": "echo-11111111"},
                             {"text": "first send\nsecond send", "t": 1000, "uuid": "echo-22222222"}])
        rec = {"type": "user", "uuid": "u-1",
               "message": {"role": "user", "content": [{"type": "text", "text": "first send"},
                                                        {"type": "text", "text": "second send"}]}}
        with s.norm_lock:
            be._append(s, [rec])
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-11111111", "echo-22222222"],
                         "neither joined spelling is a block of the record")
        self.assertEqual(be._rec_texts(self._two_block_record()), ["first send", "second send"],
                         "one key per block, stripped")
        self.assertEqual(be._rec_texts({"type": "user", "message": {"role": "user", "content": "  plain \n"}}),
                         ["plain"], "a string content is one entry")
        self.assertEqual(be._rec_texts({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "   "}, {"type": "image", "source": {}}]}}), [],
            "blank and non-text blocks key nothing")

    def test_a_send_that_finds_the_session_dead_takes_back_only_its_own_echo(self):
        # send() minted an echo, steered, and found the session dead under its second lock (it died during
        # the steer RPC). It takes back the echo it minted, by uuid, and no other: an earlier same-text
        # send the app-server never recorded must stay visible, the rule prune_live states.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        with s.lock:
            s.turn_id = "t-live"                           # an open turn: send() steers
            s.echoes.append({"text": "ok", "t": 1000, "uuid": "echo-11111111"})   # an earlier, unrecorded send

        def dies_mid_steer(tid, expected_turn_id, input_items):
            with s.lock:
                s.dead = True
            raise RuntimeError("synthetic: the app-server went away during the steer")

        fake.turn_steer = dies_mid_steer
        self.assertFalse(be.send(sid, "ok"))
        self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-11111111"],
                         "the earlier echo survives; only the failed send's own echo is taken back")


class LaunchErrorNames(unittest.TestCase):
    """A LIVE launch-error row without a shared name let a retry mint a duplicate live session
    under the same name (the v1.3.12 audit's P2) — both failure branches now write names/. Both
    also keep the identity colour the caller picked: a placeholder that dropped it wrote an empty
    colour into names/ and the registry row, and every later writer (the thread create once the
    app-server was back, the load-time republish) copied that empty colour forward, so the
    session ran colourless on every identity surface for its whole life while the kernel's
    picker, which counts held colours from names/, handed its colour to the next session."""

    def test_a_clientless_spawn_writes_its_shared_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            name_file = os.path.join(td, "names", sid)
            self.assertTrue(os.path.exists(name_file),
                            "the launch-error session claims its name slot")
            self.assertIn("webby", open(name_file).read())

    def test_a_thread_start_failure_writes_its_shared_name(self):
        import tempfile

        class BoomClient(FakeClient):
            def thread_start(self, params):
                raise RuntimeError("no threads today")
        with tempfile.TemporaryDirectory() as td:
            fake = BoomClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("webby", "/tmp")
            name_file = os.path.join(td, "names", sid)
            self.assertTrue(os.path.exists(name_file))
            self.assertIn("webby", open(name_file).read())

    def _assert_wears_the_picked_colour(self, td, be, sid):
        parts = (Path(td) / "names" / sid).read_text().rstrip("\n").split("\t")
        self.assertEqual(parts, ["web", "/TESTDIR", "#336699", "#ffffff"],
                         "the shared identity file carries the picked colour, both fields")
        self.assertEqual(be.live_sessions()[sid]["color"], "#336699",
                         "so does the row: it is what the later thread create republishes from")
        be2 = cb.CodexBackend(td, client_factory=lambda: None)
        self.assertEqual(be2.live_sessions()[sid]["color"], "#336699",
                         "and durably: the row rebuilt at the next load still carries it")

    def test_a_clientless_spawn_keeps_the_picked_identity_colour(self):
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("web", "/TESTDIR", "#336699", "#ffffff")
            self.assertTrue(be._session(sid).tid.startswith("pending-"), "the client-missing branch")
            self._assert_wears_the_picked_colour(td, be, sid)

    def test_a_thread_start_failure_keeps_the_picked_identity_colour(self):
        class BoomClient(FakeClient):
            def thread_start(self, params):
                raise RuntimeError("no threads today")
        with tempfile.TemporaryDirectory() as td:
            fake = BoomClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("web", "/TESTDIR", "#336699", "#ffffff")
            self.assertTrue(be._session(sid).tid.startswith("failed-"), "the thread/start-failure branch")
            self._assert_wears_the_picked_colour(td, be, sid)

    def test_the_placeholder_colour_outlives_the_thread_it_later_gets(self):
        # the loss was for LIFE, not just while the row was red: once the app-server was back, the
        # create path turned the placeholder into a real thread and republished names/ from the
        # row, whose colour was the same empty string. With the colour on the row, that republish
        # carries it forward.
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("web", "/TESTDIR", "#336699", "#ffffff")
            s = be._session(sid)
            self.assertTrue(be._prepare_thread(s, FakeClient()), "the placeholder became a real thread")
            self.assertFalse(s.tid.startswith("pending-"))
            parts = (Path(td) / "names" / sid).read_text().rstrip("\n").split("\t")
            self.assertEqual(parts[2:], ["#336699", "#ffffff"],
                             "the republish after thread start keeps the colour")
            self.assertEqual(be.live_sessions()[sid]["color"], "#336699")


class RegistryNamesHeal(unittest.TestCase):
    """spawn writes the durable registry row, then names/<sid>. A kernel death between the two left a
    LIVE row with no shared identity file: the next boot rebuilt the session from the registry but
    republished nothing, so every names/-derived surface (sender name and colour, cwd, the duplicate-
    name claim) was blind to it, and the one heal a user could reach, a rename, wrote an empty colour
    although the registry knew it (2026-09-11). All data synthetic per CLAUDE.md."""

    def _crash_between_row_and_publish(self):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR", "#336699", "#ffffff")
        nf = Path(tmp) / "names" / sid
        nf.unlink()                            # the crash window: the row landed, the publish never ran
        return be, fake, tmp, sid, nf

    def test_a_live_row_missing_its_names_entry_is_republished_at_load(self):
        be, fake, tmp, sid, nf = self._crash_between_row_and_publish()
        logs = []
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fake, log=logs.append)
        self.assertTrue(be2.owns(sid), "the row is live in the registry")
        self.assertTrue(nf.is_file(), "the next boot republishes the identity file from the registry")
        parts = nf.read_text().rstrip("\n").split("\t")
        self.assertEqual(parts[:3], ["web", "/TESTDIR", "#336699"],
                         "name, cwd and colour come from the registry, the durable source")
        self.assertEqual(sorted(p.name for p in nf.parent.iterdir()), [sid], "no staging file left behind")
        self.assertTrue(any(sid in m and "missing at load" in m for m in logs),
                        "the heal is loud: the log names the sid and the state it repaired")

    def test_a_failed_republish_at_load_leaves_the_boot_alive_and_names_the_unnamed_state(self):
        # the heal's failure branch: the boot must not die over an identity file, and the log must
        # NAME the state it leaves — a live row with no published name is the duplicate-name hole.
        # The REAL writer runs under _disk_full, so the publish (os.replace onto names/<sid>) fails
        # with ENOSPC exactly as it would on a full disk
        be, fake, tmp, sid, nf = self._crash_between_row_and_publish()
        logs = []
        with _disk_full(nf):
            be2 = cb.CodexBackend(tmp, client_factory=lambda: fake, log=logs.append)
        self.assertTrue(be2.owns(sid), "the boot survived the failed write and the row is still live")
        self.assertFalse(nf.exists(), "a failed publish creates nothing")
        self.assertEqual(sorted(p.name for p in nf.parent.iterdir()), [], "no staging file left behind")
        said = [m for m in logs if sid in m and "could not be republished at load" in m]
        self.assertEqual(len(said), 1, logs)
        self.assertIn("[Errno 28]", said[0], "the log names the errno")
        self.assertIn("UNNAMED", said[0], "the log names the state the session is left in")

    def test_a_row_whose_names_entry_exists_is_left_untouched_at_load(self):
        # green on origin/main as well (it wrote nothing at load): the pin that the heal is for the
        # MISSING file only — the names producers watch the mtime, and a kernel-side recolour lives
        # in the file alone, so a rewrite from the registry would both flap and revert it
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR", "#336699", "#ffffff")
        nf = Path(tmp) / "names" / sid
        nf.write_text("web\t/TESTDIR\t#abcdef\t#000000\n")   # a kernel-side recolour: names/ only
        before = (nf.read_bytes(), nf.stat().st_mtime_ns)
        logs = []
        cb.CodexBackend(tmp, client_factory=lambda: fake, log=logs.append)
        self.assertEqual((nf.read_bytes(), nf.stat().st_mtime_ns), before,
                         "byte-identical and no mtime bump: nothing was missing, so nothing was written")
        self.assertEqual([m for m in logs if "names/" in m], [])

    def test_a_dead_row_missing_its_names_entry_is_not_republished(self):
        # green on origin/main as well: a dead row claims no name slot — republishing it would
        # hold the name against the create it no longer owns
        be, fake, tmp, sid, nf = self._crash_between_row_and_publish()
        self.assertTrue(be.kill(sid))
        cb.CodexBackend(tmp, client_factory=lambda: fake)
        self.assertFalse(nf.exists(), "a dead row claims no name slot")

    def test_the_rename_heal_carries_the_registry_colour(self):
        be, fake, tmp, sid, nf = self._crash_between_row_and_publish()
        self.assertTrue(be.rename(sid, "api"))
        parts = nf.read_text().rstrip("\n").split("\t")
        self.assertEqual(parts[:3], ["api", "/TESTDIR", "#336699"],
                         "the healed file carries the colour the registry knows, not an empty one")

    def test_a_recolour_in_the_names_file_outranks_the_registry_colour(self):
        # green on origin/main as well: the pin for the ORDER of the fallback (the file first) — a
        # kernel-side recolour (_set_session_color) writes names/ only and never updates the
        # registry's colour, so a rename must keep the file's, not revert to spawn's
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR", "#336699", "#ffffff")
        nf = Path(tmp) / "names" / sid
        nf.write_text("web\t/TESTDIR\t#abcdef\t#000000\n")
        self.assertTrue(be.rename(sid, "api"))
        self.assertEqual(nf.read_text(), "api\t/TESTDIR\t#abcdef\t#000000\n")


class RaisingRegistryTransactions(unittest.TestCase):
    """The r28 verification, executed on the real backend: every durable-write failure must
    publish NOTHING — no moved names file, no in-memory lifecycle flip, no phantom row."""

    def _corrupt(self, td):
        os.makedirs(os.path.join(td, "codex"), exist_ok=True)
        with open(os.path.join(td, "codex", "registry.json"), "w") as f:
            f.write("{ not json")

    def test_a_raising_registry_rename_moves_no_store(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            self._corrupt(td)
            with self.assertRaises(RuntimeError):
                be.rename(sid, "newname")
            s = be._session(sid)
            self.assertEqual(s.name, "webby", "the in-memory name never moved")
            self.assertIn("webby", open(os.path.join(td, "names", sid)).read(),
                          "the shared names file never moved — three stores stay agreed")

    def test_a_raising_registry_resume_rolls_the_flip_back(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            s.dead = True
            prior_state = s.state
            self._corrupt(td)
            with self.assertRaises(RuntimeError):
                be.resume("webby", sid)
            self.assertTrue(s.dead,
                            "a FAILED revive must not come up as a live lane beside its own "
                            "failure message")
            self.assertEqual(s.state, prior_state)

    def test_a_raising_registry_spawn_leaves_no_phantom(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self._corrupt(td)
            be = cb.CodexBackend(td, client_factory=lambda: None)
            with self.assertRaises(RuntimeError):
                be.spawn("webby", "/tmp")
            self.assertEqual(len(be._sessions), 0,
                             "no durable row means no in-memory row — a phantom live lane with "
                             "no names file re-opened the duplicate-name hole")
            self.assertFalse(os.path.exists(os.path.join(td, "names")) and
                             os.listdir(os.path.join(td, "names")))

    def test_a_raising_registry_success_path_spawn_leaves_no_phantom(self):
        # the r29 verification: only the two ERROR branches were pinned — a wrong-key mutant in
        # the success branch's rollback survived every test
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self._corrupt(td)
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            with self.assertRaises(RuntimeError):
                be.spawn("webby", "/tmp")
            self.assertEqual(len(be._sessions), 0,
                             "the success branch rolls back like its two siblings")

    def test_a_names_write_failure_retires_the_spawned_row(self):
        # the r29 verification: the durable row landed, then the UNGUARDED names write raised —
        # a live row holding no name is the duplicate-name hole (the v1.3.12 audit) re-opened
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "names"), "w") as f:
                f.write("not a dir")               # names/ is uncreatable: mkdir raises
            be = cb.CodexBackend(td, client_factory=lambda: None)
            with self.assertRaises(Exception):
                be.spawn("webby", "/tmp")
            self.assertEqual(len(be._sessions), 0,
                             "no live row without a shared name — the row is retired, loudly")
            import json as _json
            rows = _json.loads(open(os.path.join(td, "codex", "registry.json")).read())
            self.assertTrue(all(r.get("dead") for r in rows.values()),
                            "the durable row is retired too: %r" % rows)

    def test_a_names_write_failure_in_rename_keeps_all_three_stores_agreed(self):
        # the r29 verification: the compensation branch was unpinned — deleting it entirely
        # stayed green while the registry alone moved to the new name under a false
        # "keeps its old name" message
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            nf = os.path.join(td, "names", sid)
            os.chmod(os.path.dirname(nf), 0o555)   # the ATOMIC write's tmp create raises; the
            try:                                    # file survives untouched (r32 made the write
                with self.assertRaises(Exception):  # tmp+replace, so a read-only FILE no longer
                    be.rename(sid, "newname")       # fails it — only the dir does)
            finally:
                os.chmod(os.path.dirname(nf), 0o755)
            self.assertEqual(be._session(sid).name, "webby", "memory kept the old name")
            self.assertIn("webby", open(nf).read(), "the names file kept the old name")
            import json as _json
            rows = _json.loads(open(os.path.join(td, "codex", "registry.json")).read())
            self.assertEqual(rows[sid]["name"], "webby",
                             "the COMPENSATION re-ran the registry write with the old name — "
                             "without it the registry alone holds the new name and applies the "
                             "'failed' rename at the next restart")

    def test_a_failed_publish_in_rename_leaves_the_names_file_untouched(self):
        # rewritten to the truth (review, 2026-09-08): the old form mocked _write_name with a fake that
        # truncated the file IN PLACE — something the real tmp+os.replace writer cannot do — and pinned
        # an in-place RESTORE that, under the very ENOSPC it existed for, truncated a good file to
        # nothing and then blamed "a failed write". _disk_full drives the REAL writer with the fault
        # beneath it and models ENOSPC for any in-place write aimed at the file; on origin/main the
        # restore itself empties the file
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            nf = Path(td) / "names" / sid
            old_line = nf.read_text()
            with _disk_full(nf):
                with self.assertRaises(OSError):
                    be.rename(sid, "newname")
            self.assertEqual(nf.read_text(), old_line,
                             "byte-identical: the atomic writer left it, and nothing rewrote it in place")
            self.assertEqual(sorted(p.name for p in nf.parent.iterdir()), [sid], "no staging file left behind")
            self.assertEqual(be._session(sid).name, "webby", "memory kept the old name")
            rows = json.loads((Path(td) / "codex" / "registry.json").read_text())
            self.assertEqual(rows[sid]["name"], "webby", "the registry write was re-run with the old name")

    def test_a_failed_first_publish_in_rename_creates_nothing(self):
        # rewritten to the truth (review, 2026-09-08): the old fake CREATED a partial file, which the
        # real writer never can (it publishes whole or not at all). Green on origin/main as well — its
        # unlink branch was a no-op there — kept as the pin for the absent-entry shape
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            nf = Path(td) / "names" / sid
            os.unlink(nf)                          # a legacy row predating the names write
            with _disk_full(nf):
                with self.assertRaises(OSError):
                    be.rename(sid, "newname")
            self.assertEqual(sorted(p.name for p in nf.parent.iterdir()), [],
                             "neither the file nor its temp exists: a failed rename publishes nothing")
            self.assertEqual(be._session(sid).name, "webby")

    def test_a_failed_names_write_after_thread_start_leaves_the_file_untouched(self):
        # the r30 verification, rewritten to the truth (2026-09-09, the twin of #1138's rename
        # rewrite): the old form mocked _write_name with a fake that truncated the file IN PLACE —
        # something the real tmp+os.replace writer cannot do — and pinned an in-place RESTORE that,
        # under the very ENOSPC it existed for, truncated a good file to nothing and then blamed "a
        # failed write". _disk_full drives the REAL writer with the fault beneath it and models
        # ENOSPC for any in-place write aimed at the file; on origin/main the restore itself
        # empties the file. No later path rewrites it (resume skips the create branch)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            logs = []
            be = cb.CodexBackend(td, client_factory=lambda: fake, log=logs.append)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            nf = Path(td) / "names" / sid
            old_line = nf.read_bytes()
            old_mtime = nf.stat().st_mtime_ns
            s.tid = "pending-%s" % sid[:8]         # force the create path
            s.loaded = False
            with _disk_full(nf):
                ok = be._prepare_thread(s, fake)
            self.assertTrue(ok, "the thread is healthy — the turn proceeds")
            self.assertTrue(s.loaded)
            self.assertEqual(nf.read_bytes(), old_line,
                             "byte-identical: the atomic writer left it, and nothing rewrote it in place")
            self.assertEqual(nf.stat().st_mtime_ns, old_mtime,
                             "no rewrite for no content change — the names producers watch the mtime")
            self.assertEqual(sorted(p.name for p in nf.parent.iterdir()), [sid], "no staging file left behind")
            said = [m for m in logs if "after thread start" in m]
            self.assertEqual(len(said), 1, logs)
            self.assertIn("[Errno 28]", said[0], "the log names the errno")
            self.assertNotIn("restored", said[0], "nothing was restored, so the log does not say so")

    def test_a_corrupt_names_file_is_healed_by_the_next_write(self):
        # the r31 verification: non-UTF-8 names bytes sailed through _write_name's OSError-only
        # read catch, failed the turn with a wrong-subsystem error, and no path ever healed the
        # file — the session's identity vanished from every kernel surface
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            nf = os.path.join(td, "names", sid)
            with open(nf, "wb") as f:
                # tab-shaped residue: a mutant that decodes the corrupt parts leniently would
                # CARRY the bad bg forward (the r33 mutant hunt) — exact-equality catches it
                f.write(b"webby\t/tmp\t\xff\xfe\t\x80fg")
            s.tid = "pending-%s" % sid[:8]         # force the create path
            s.loaded = False
            ok = be._prepare_thread(s, fake)
            self.assertTrue(ok)
            healed = open(nf, "rb").read()
            self.assertEqual(healed, b"webby\t/tmp\t\t\n",
                             "healed means CLEAN and WHOLE — a heal that decoded the corrupt "
                             "parts leniently carried the bad colours forward, re-arming the "
                             "landmine (the r32/r33 mutant hunts)")

    def test_the_names_write_is_atomic(self):
        # the r31 verification: the in-place write_text was torn-readable mid-write and its
        # crash residue armed the decode landmine — tmp+os.replace, like sdk_backend.write_name
        import inspect
        src = inspect.getsource(cb.CodexBackend._write_name)
        self.assertIn("tmp.write_text", src, "the payload lands on a TMP file first")
        self.assertIn("os.replace", src, "and moves into place atomically")

    def test_the_names_staging_file_never_leaks(self):
        # the r32 verification: a failing replace left names/<sid>.tmp behind, and NAMES
        # consumers read the stray as a phantom session forever
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            names_dir = os.path.join(td, "names")
            os.unlink(os.path.join(names_dir, sid))
            os.mkdir(os.path.join(names_dir, sid))   # os.replace onto a non-empty dir raises
            os.mkdir(os.path.join(names_dir, sid, "x"))
            try:
                with self.assertRaises(OSError):
                    be._write_name(s)
            finally:
                os.rmdir(os.path.join(names_dir, sid, "x"))
                os.rmdir(os.path.join(names_dir, sid))
            self.assertEqual([n for n in os.listdir(names_dir) if n.endswith(".tmp")], [],
                             "the staging file is unlinked on every path")

    def test_an_unpublishable_name_after_thread_start_says_so(self):
        # the r31 verification: the no-prior-file leg logged "was restored" when the partial
        # file was actually unlinked — and the state it leaves (live row, no published name) is
        # the duplicate-name hole, which the log must NAME. Since 2026-09-09 the REAL writer runs
        # under _disk_full (green on origin/main as well — its unlink branch was a no-op there);
        # kept as the pin for the absent-entry shape: a failed first publish creates nothing
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            logs = []
            be = cb.CodexBackend(td, client_factory=lambda: fake, log=logs.append)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            nf = Path(td) / "names" / sid
            os.unlink(nf)                          # a legacy row predating the names write
            s.tid = "pending-%s" % sid[:8]
            s.loaded = False
            with _disk_full(nf):
                ok = be._prepare_thread(s, fake)
            self.assertTrue(ok, "the healthy turn still proceeds")
            self.assertEqual(sorted(p.name for p in nf.parent.iterdir()), [],
                             "neither the file nor its temp exists: a failed publish leaves nothing")
            self.assertTrue(any("could not be published" in m and "[Errno 28]" in m for m in logs), logs)
            self.assertFalse(any("restored" in m for m in logs),
                             "the log must not claim a restore that never happened")

    def test_resume_never_overwrites_a_fresher_registry_name(self):
        # the r37 verification: a rename landing during an in-flight revive was silently
        # reverted in the durable registry by resume's adoption of the caller's stale echo
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("web", "/tmp")
            s = be._session(sid)
            s.dead = True
            be.rename(sid, "api")                      # the rename that landed mid-revive
            self.assertTrue(be.resume("web", sid),
                            "the revive resolved its name BEFORE the rename")
            self.assertEqual(be._session(sid).name, "api",
                             "the registry's fresher name survives the stale echo")

    def test_resume_adopts_the_echo_only_for_a_nameless_row(self):
        # the r38 mutant hunt: only the negative leg was pinned — deleting the adoption
        # entirely stayed green (the SDK twin pins both directions)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("web", "/tmp")
            s = be._session(sid)
            s.dead = True
            s.name = ""                                # a nameless registry row (legacy load)
            self.assertTrue(be.resume("adopted", sid))
            self.assertEqual(be._session(sid).name, "adopted",
                             "a nameless row ADOPTS the caller's echo — that half must hold too")

    def test_a_raising_tid_save_rolls_the_thread_flip_back(self):
        # the r29 verification: with the real tid only in memory, every retry took the resume
        # path and never re-saved it — the next restart loaded 'pending-…' and silently started
        # a FRESH Codex thread (server-side context lost, nothing looking wrong)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            s.tid = "pending-%s" % sid[:8]         # force the create path
            s.loaded = False
            s.model = ""                           # no pick on the placeholder: the server's answer
            prior_model = s.model                  # fills the field, so the flip is observable

            class OtherModel(type(fake)):
                def thread_start(self2, params):
                    resp = super().thread_start(params)
                    resp.model = "gpt-other"       # a DIFFERENT answer, so the model half of
                    return resp                    # the rollback is observable (the r30 hunt)
            fake2 = OtherModel()
            self._corrupt(td)
            with self.assertRaises(RuntimeError):
                be._prepare_thread(s, fake2)
            self.assertTrue(s.tid.startswith("pending-"),
                            "the real tid is never published to memory alone")
            self.assertFalse(s.loaded)
            self.assertEqual(s.model, prior_model, "all THREE rolled-back fields, not two")

    def test_a_raising_registry_thread_start_failure_spawn_leaves_no_phantom(self):
        import tempfile

        class BoomClient(FakeClient):
            def thread_start(self, params):
                raise RuntimeError("no threads today")
        with tempfile.TemporaryDirectory() as td:
            self._corrupt(td)
            fake = BoomClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            with self.assertRaises(RuntimeError):
                be.spawn("webby", "/tmp")
            self.assertEqual(len(be._sessions), 0)

    def test_a_raising_registry_send_keeps_nothing_in_memory(self):
        # send() was the one durable mutator without a rollback: the text stayed in the queue, its
        # echo in the live atoms and busy() read True — a queued bubble on a busy session for a send
        # whose caller was told it failed — with no worker kicked, so nothing ever drained it. The
        # fault is the REAL one beneath the real writer (an unreadable registry.json), not a mock
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("web", "/TESTDIR")
            self._corrupt(td)
            with self.assertRaises(RuntimeError):
                be.send(sid, "lost to an unreadable registry")
            self.assertEqual(be.pending_queued(sid), [],
                             "the entry never reached disk, so it leaves memory too")
            self.assertEqual(be._session(sid).queue_ids, [], "and its id with it")
            self.assertEqual(be.live_atoms(sid), [], "this send's echo goes with it")
            self.assertFalse(be.busy(sid), "nothing is queued, so nothing reads as work to drive")

    def test_a_raising_registry_send_takes_back_only_its_own_entry_and_echo(self):
        # The take-back is scoped by the failed send's entry id and echo uuid, never by text or by a
        # clear. An earlier SAME-text send is still queued (parked behind a backing-off worker) with
        # its echo still awaiting the app-server's record: a clear would leave that earlier copy on
        # disk but not in memory (the ACK-mismatch face this fix removes, reintroduced); a by-text
        # take-back would remove the EARLIER copy's slot and echo and keep the failed send's own — the
        # phantom back, and a real message gone from the chat. Same shape as the dead-path steer test
        # (test_a_send_that_finds_the_session_dead_takes_back_only_its_own_echo): the earlier state is
        # planted under the lock and expected to survive alone.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("web", "/TESTDIR")
            s = be._session(sid)
            with s.lock:                           # an earlier same-text send, queued and echoed
                s.queue.append("deploy the change")
                s.queue_ids.append("q-11111111")
                s.echoes.append({"text": "deploy the change", "t": 1000, "uuid": "echo-11111111"})
            self._corrupt(td)
            with self.assertRaises(RuntimeError):
                be.send(sid, "deploy the change")
            self.assertEqual(be.pending_queued(sid), ["deploy the change"],
                             "the earlier copy stays queued: the take-back is no clear")
            self.assertEqual(s.queue_ids, ["q-11111111"],
                             "and under its own id: a by-text take-back would have removed this slot "
                             "and kept the failed send's fresh id")
            self.assertEqual([a["uuid"] for a in be.live_atoms(sid)], ["echo-11111111"],
                             "the earlier send's echo survives; only the failed send's own is taken back")
            self.assertTrue(be.busy(sid), "the earlier send still reads as work to drive")

    def test_a_raising_registry_send_does_not_ride_the_next_turn(self):
        # the second face of the same hole: the copy kept in memory rode the NEXT send's kick into
        # that turn, so a user who retyped after freeing the disk had the agent read the instruction
        # twice in one turn (and the ACK of both ids against a disk row holding one preserved the
        # durable entry for a third delivery at the next restart)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("web", "/TESTDIR")
            reg = Path(td) / "codex" / "registry.json"
            good = reg.read_bytes()
            self._corrupt(td)
            with self.assertRaises(RuntimeError):
                be.send(sid, "deploy the change")
            reg.write_bytes(good)                  # the registry is readable again: the user retypes
            self.assertTrue(be.send(sid, "deploy the change"))
            self.assertTrue(until(lambda: not be.busy(sid) and fake.called("turn_start")))
            starts = fake.called("turn_start")
            self.assertEqual(len(starts), 1)
            self.assertEqual([i["text"] for i in starts[0][2]], ["deploy the change"],
                             "the failed send is not delivered beside the retype")
            rows = json.loads(reg.read_text())
            self.assertEqual(registry_queue_entries(rows, sid), [],
                             "one id appended, one id acked: the durable queue drains")


class EnsureCodexSdk(unittest.TestCase):
    """ensure_codex_sdk adds the codexvenv's site-packages only when they were built for the interpreter
    this process runs (the twin of the SDK venv's rule, tests/test_sdk_venv_abi.py). Every
    codexvenv/lib/python3.* used to join sys.path[0] whatever the interpreter, so a codexvenv built with a
    newer python failed deep inside the import under the kernel's python and shadowed shared
    dependencies for every later import. find_spec is stubbed so the outcome does not depend on
    whether this machine has openai_codex installed."""

    # The tag venv names this interpreter's lib directory with, from sys itself (not the code under test).
    TAG = "%d.%d%s" % (sys.version_info[0], sys.version_info[1], "t" if "t" in getattr(sys, "abiflags", "") else "")

    def setUp(self):
        import io
        self.state = tempfile.mkdtemp()
        self.venv = Path(self.state) / "codexvenv"
        self.path_before = list(sys.path)
        self.err = io.StringIO()
        cb._CODEX_VENV_BUILT_FOR = []

    def tearDown(self):
        import shutil
        sys.path[:] = self.path_before
        shutil.rmtree(self.state, ignore_errors=True)

    def _site(self, pyname):
        sp = self.venv / "lib" / pyname / "site-packages"
        sp.mkdir(parents=True)
        return str(sp)

    def _run(self, importable_from=None):
        import importlib.util
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *a, **k):
            if name != "openai_codex":
                return real_find_spec(name, *a, **k)
            if importable_from and any(p.startswith(importable_from) for p in sys.path):
                return SimpleNamespace(name=name)
            return None
        with mock.patch.object(importlib.util, "find_spec", fake_find_spec), \
             mock.patch.object(sys, "stderr", self.err):
            return cb.ensure_codex_sdk(self.state)

    def test_a_venv_for_another_python_is_not_added_and_is_named(self):
        sp = self._site("python3.99")
        self.assertFalse(self._run(importable_from=sp))
        self.assertNotIn(sp, sys.path, "a 3.99 venv must never join a python%s process" % self.TAG)
        line = self.err.getvalue()
        self.assertIn("built for python 3.99", line)
        self.assertIn("runs " + self.TAG, line)
        self.assertIn("romp-codex-setup", line, "the remedy, named")
        self.assertEqual(cb._CODEX_VENV_BUILT_FOR, ["3.99"])
        self.assertFalse(self._run(importable_from=sp))
        self.assertEqual(self.err.getvalue(), line, "one line per verdict, not one per launch")

    def test_a_matching_venv_is_added_and_the_sdk_imports(self):
        self.assertEqual(cb._running_python_tag(), self.TAG)
        sp = self._site("python" + self.TAG)
        self.assertTrue(self._run(importable_from=sp))
        self.assertIn(sp, sys.path)
        self.assertEqual(self.err.getvalue(), "", "nothing to say when the venv matches")

    def test_only_the_matching_directory_joins_when_both_exist(self):
        old = self._site("python3.99")
        new = self._site("python" + self.TAG)
        self.assertTrue(self._run(importable_from=new))
        self.assertIn(new, sys.path)
        self.assertNotIn(old, sys.path)

    def test_a_free_threaded_build_is_its_own_tag(self):
        # venv names a 3.99t build's lib directory python3.99t: that venv is the 3.99t process's own, and a
        # python3.99 venv in a 3.99t process is a mismatch, whatever the shared minor says
        own = self._site("python3.99t")
        with mock.patch.multiple(sys, abiflags="t", version_info=(3, 99, 0, "final", 0), create=True):
            self.assertEqual(cb._running_python_tag(), "3.99t")
            self.assertTrue(self._run(importable_from=own))
            self.assertIn(own, sys.path)
            sys.path[:] = self.path_before
            import shutil
            shutil.rmtree(self.venv)
            other = self._site("python3.99")
            self.assertFalse(self._run(importable_from=other))
            self.assertNotIn(other, sys.path)
        self.assertIn("built for python 3.99 but the kernel runs 3.99t", self.err.getvalue())

    def test_no_venv_is_the_plain_not_importable_path(self):
        self.assertFalse(self._run())
        self.assertEqual(self.err.getvalue(), "", "no venv: nothing about versions to say")
        self.assertEqual(sys.path, self.path_before)


def _hold_turn_open(fake, tid="T-1", turn="t-1", text="long"):
    """Script the NEXT turn to stay open. hold_open alone changes nothing about the default echo turn, which
    carries its own turn/completed; an injected script WITHOUT one is what keeps the turn open (the steer and
    interrupt tests' idiom), and hold_open then stops the fake from appending the completion itself."""
    fake.hold_open = True
    fake.scripts = [[("item/completed", {"threadId": tid, "turnId": turn, "completedAtMs": 1781100000000,
                                        "item": {"type": "userMessage", "id": "u-1",
                                                 "content": [{"type": "text", "text": text}]}})]]


def _lock_free(be, sid, timeout=5.0):
    """The turn is over AND the worker has released the turn lock. Between the finally that clears turn_id
    (busy() False from there) and the `with s.mode_lock` exit there is a real sliver in which
    CodexBackend.clear answers "busy" (the worker still holds the lock a clear must take); a test that
    wants the idle verdict waits for the lock itself, not for busy()."""
    s = be._session(sid)

    def free():
        if be.busy(sid):
            return False
        if s.mode_lock.acquire(blocking=False):
            s.mode_lock.release()
            return True
        return False
    return until(free, timeout=timeout)


class NativeClear(unittest.TestCase):
    """CodexBackend.clear (2026-09-19): a fresh conversation for the SAME session. A typed /clear on a Codex
    session used to reach the model as a prompt (the app-server has no slash parser): the thread, the
    transcript file and the parent chain were unchanged, so no romp surface saw a boundary. clear() mints a
    new app-server thread under the same sid — thread/start with sessionStartSource "clear" and the row's
    cwd, mode and model — swaps the registry row's tid to it under the turn lock, resets the normalizer on
    the new empty file (its first record is a ROOT, the episode boundary's cue), leaves an acknowledging
    "/clear" chip, and keeps name, mode, model, effort, note, color and the durable queue. The bracket
    clearing() holds from before thread/start until the new tid is durable, or the attempt raises."""

    def _turned(self, mode="auto", model="gpt-5-picked"):
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        if mode:
            self.assertTrue(be.set_mode(sid, mode))
        if model:
            self.assertTrue(be.set_model(sid, model))
        self.assertTrue(be.send(sid, "first synthetic turn"))
        self.assertTrue(_lock_free(be, sid))
        return be, fake, tmp, sid

    def _row(self, be, sid):
        return json.loads(be._reg_path().read_text())[sid]

    def test_clear_mints_a_new_thread_under_the_same_sid(self):
        be, fake, tmp, sid = self._turned()
        old = self._row(be, sid)
        enc = cb._enc_cwd("/TESTDIR")
        old_file = Path(tmp) / "codex" / "projects" / enc / "T-1.jsonl"
        self.assertTrue(old_file.read_text().strip(), "the first turn materialized")
        self.assertIsNotNone(be.live_sessions()[sid]["context"], "the first turn's tokenUsage set the fill")
        self.assertEqual(be.clear(sid), "")
        starts = fake.called("thread_start")
        self.assertEqual(len(starts), 2, "one thread at spawn, one at the clear")
        self.assertEqual(starts[-1][1], {"cwd": "/TESTDIR", "approvalPolicy": "on-request",
                                         "approvalsReviewer": "auto_review",
                                         "permissions": cb.WORKSPACE_PERMISSION,
                                         "runtimeWorkspaceRoots": ["/TESTDIR"],
                                         "model": "gpt-5-picked", "sessionStartSource": "clear"},
                         "the row's cwd, mode and picked model ride the create, tagged as Codex's own /clear")
        row = self._row(be, sid)
        self.assertEqual(row["tid"], "T-2")
        for key in ("name", "cwd", "model", "effort", "mode", "dead", "queue", "note", "color"):
            self.assertEqual(row[key], old[key], key)
        self.assertIsNone(row["launchError"])
        new_file = Path(tmp) / "codex" / "projects" / enc / "T-2.jsonl"
        self.assertTrue(new_file.exists(), "the fresh transcript exists before the row names it")
        self.assertEqual(new_file.read_text(), "", "an empty file parses as an empty conversation")
        self.assertTrue(old_file.exists() and old_file.read_text().strip(), "the cleared conversation stays on disk")
        self.assertEqual(fake.called("thread_set_name")[-1], ("thread_set_name", "T-2", "web"))
        live = be.live_sessions()[sid]
        self.assertIsNone(live["context"], "the normalizer reset: no fill until the fresh thread's first turn")
        self.assertEqual(live["model"], "gpt-5-picked")
        self.assertIs(be.clearing(sid), False)
        self.assertTrue(be.send(sid, "second synthetic turn"))
        self.assertTrue(until(lambda: not be.busy(sid)))
        self.assertEqual(fake.called("turn_start")[-1][1], "T-2", "the next turn runs on the fresh thread")
        self.assertEqual(fake.called("thread_resume"), [], "loaded stands: same client generation, no resume")
        first = json.loads(new_file.read_text().splitlines()[0])
        self.assertIsNone(first.get("parentUuid"), "a ROOT head: the episode boundary's cue")
        self.assertEqual(first["sessionId"], "T-2")

    def test_clear_brackets_clearing_until_the_tid_is_saved(self):
        class Blocking(FakeClient):
            def __init__(self):
                super().__init__()
                self.block = threading.Event()
                self.entered = threading.Event()

            def thread_start(self, params=None):
                if self.called("thread_start"):          # spawn's first create returns at once
                    self.entered.set()
                    self.block.wait(5)
                return super().thread_start(params)

        fake = Blocking()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertIs(be.clearing(sid), False, "no bracket before a clear")
        out = []
        th = threading.Thread(target=lambda: out.append(be.clear(sid)), daemon=True)
        th.start()
        self.assertTrue(fake.entered.wait(5))
        self.assertIs(be.clearing(sid), True, "latched before thread/start returns")
        self.assertEqual(self._row(be, sid)["tid"], "T-1", "the row still names the old thread")
        fake.block.set()
        th.join(5)
        self.assertEqual(out, [""])
        self.assertIs(be.clearing(sid), False, "dropped the instant the new tid is durable")
        self.assertEqual(self._row(be, sid)["tid"], "T-2")

    def test_a_model_pick_landing_inside_the_bracket_survives_the_swap(self):
        # Nothing reads busy through the bracket and the model setter never takes the turn lock the bracket holds,
        # so a /model pick can land while thread/start is in flight: accepted and saved — and the swap then wrote
        # the model read BEFORE the request back onto the row (review find, 2026-09-19): memory, the registry, the
        # live listing and the next turn's params all back on the pre-clear model. The swap takes the row's current
        # value, as _create_thread does; the pre-request model rides the start params only.
        class Blocking(FakeClient):
            def __init__(self):
                super().__init__()
                self.block = threading.Event()
                self.entered = threading.Event()

            def thread_start(self, params=None):
                if self.called("thread_start"):          # spawn's first create returns at once
                    self.entered.set()
                    self.block.wait(5)
                return super().thread_start(params)

        fake = Blocking()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_model(sid, "gpt-5-picked"))
        out = []
        th = threading.Thread(target=lambda: out.append(be.clear(sid)), daemon=True)
        th.start()
        self.assertTrue(fake.entered.wait(5), "thread/start is in flight")
        self.assertIs(be.clearing(sid), True)
        self.assertTrue(be.set_model(sid, "gpt-5-later"), "the pick lands inside the bracket")
        self.assertEqual(self._row(be, sid)["model"], "gpt-5-later", "and is saved")
        fake.block.set()
        th.join(5)
        self.assertEqual(out, [""])
        self.assertEqual(fake.called("thread_start")[-1][1]["model"], "gpt-5-picked",
                         "the start carried the model read before the request")
        s = be._session(sid)
        with s.lock:
            self.assertEqual(s.model, "gpt-5-later", "memory keeps the pick")
        self.assertEqual(self._row(be, sid)["model"], "gpt-5-later", "the registry keeps the pick")
        self.assertEqual(be.live_sessions()[sid]["model"], "gpt-5-later", "the live listing keeps the pick")
        self.assertTrue(be.send(sid, "next synthetic turn"))
        self.assertTrue(until(lambda: not be.busy(sid)))
        last = fake.called("turn_start")[-1]
        self.assertEqual((last[1], last[3]["model"]), ("T-2", "gpt-5-later"),
                         "the next turn runs the pick on the fresh thread")

    def test_the_chip_carries_the_command_as_typed(self):
        # The composer retires its optimistic bubble only by the exact text it sent (or a copy id, which a clear
        # does not carry), so a typed /new or "/clear now" acknowledged by a literal "/clear" chip left the sending
        # bubble standing (review find, 2026-09-19): the words as typed ride the verb onto the echo, its uuid and the
        # durable twin; the contract's default is "/clear".
        be, fake, tmp, sid = self._turned()
        self.assertEqual(be.clear(sid, "/new"), "")
        a = be.live_atoms(sid)[-1]
        self.assertEqual((a["_echo_text"], a["command"], a["uuid"]), ("/new", "/new", "cmd:%d:new" % a["t"]))
        self.assertEqual(be.clear(sid, "/clear now"), "")
        a = be.live_atoms(sid)[-1]
        self.assertEqual((a["_echo_text"], a["command"]), ("/clear now", "/clear now"))
        rows = [json.loads(l) for l in (Path(tmp) / "states" / (sid + ".jsonl")).read_text().splitlines()]
        self.assertEqual([r["cmdGesture"] for r in rows if "cmdGesture" in r], ["/new", "/clear now"],
                         "the twins carry the same words")
        self.assertEqual(self._row(be, sid)["tid"], "T-3")

    def test_create_thread_touches_the_file_before_the_save_that_names_its_tid(self):
        # The order the clear keeps, in the create branch too (review find, 2026-09-19): discovery skips a row whose
        # file does not stat, so a registry write naming a tid whose file did not exist yet opened a gap in which a
        # discovery (the restart fallback's included) listed the board without the session until the next save.
        # Pinned at the save itself: every save naming tid finds the file; a rollback removes it, on both branches.
        be, fake, tmp = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        enc = cb._enc_cwd("/TESTDIR")
        real_save = be._save_registry
        at_save = []

        def save(sess, *, fields=(), **k):
            if "tid" in fields:
                at_save.append(be._path_for(sess.cwd, sess.tid).exists())
            return real_save(sess, fields=fields, **k)
        be._save_registry = save
        self.assertTrue(be._create_thread(s, fake, "/TESTDIR", ""))
        self.assertEqual(at_save, [True], "the file exists at the save that names the tid")
        self.assertEqual(self._row(be, sid)["tid"], "T-2")
        self.assertEqual((Path(tmp) / "codex" / "projects" / enc / "T-2.jsonl").read_text(), "")
        # a raising save: the swap rolls back and the touched file leaves with it
        be._save_registry = mock.Mock(side_effect=OSError(28, "no space"))
        with self.assertRaises(OSError):
            be._create_thread(s, fake, "/TESTDIR", "")
        with s.lock:
            self.assertEqual(s.tid, "T-2", "rolled back")
        self.assertFalse((Path(tmp) / "codex" / "projects" / enc / "T-3.jsonl").exists(),
                         "the touched file left with the rollback")
        self.assertEqual(self._row(be, sid)["tid"], "T-2", "nothing published")
        # a session killed while the create was in flight: nothing published, no file left behind
        be._save_registry = real_save

        class Killing(FakeClient):
            def thread_start(self, params=None):
                self._rec("thread_start", params)
                with s.lock:
                    s.dead = True
                return SimpleNamespace(thread=SimpleNamespace(id="K-1"), model="gpt-5-test")
        self.assertFalse(be._create_thread(s, Killing(), "/TESTDIR", ""))
        self.assertFalse((Path(tmp) / "codex" / "projects" / enc / "K-1.jsonl").exists(),
                         "the dead branch leaves no file behind")
        self.assertEqual(self._row(be, sid)["tid"], "T-2")

    def test_clear_rolls_back_when_the_registry_write_raises(self):
        be, fake, tmp, sid = self._turned()
        s = be._session(sid)
        new_file = Path(tmp) / "codex" / "projects" / cb._enc_cwd("/TESTDIR") / "T-2.jsonl"
        at_save = []

        def save(*a, **k):
            # the file the write would have named exists ALREADY (review find, 2026-09-19): the verb catches
            # Exception, so an assert here would surface only as the refusal's text; recorded, asserted after
            at_save.append(new_file.exists())
            raise OSError(28, "no space")
        with mock.patch.object(be, "_save_registry", side_effect=save):
            why = be.clear(sid)
        self.assertEqual(at_save, [True], "the fresh file existed at the registry write that would have named it")
        self.assertTrue(why.startswith("Couldn't start a fresh conversation"), why)
        self.assertIn("no space", why)
        self.assertIs(be.clearing(sid), False)
        with s.lock:
            self.assertEqual(s.tid, "T-1", "the swap rolled back")
            self.assertEqual(s.model, "gpt-5-picked")
        self.assertFalse(new_file.exists(), "the touched file leaves with the rollback")
        self.assertEqual(self._row(be, sid)["tid"], "T-1")
        self.assertTrue(be.send(sid, "after the failed clear"))
        self.assertTrue(until(lambda: not be.busy(sid)))
        self.assertEqual(fake.called("turn_start")[-1][1], "T-1", "the conversation continues where it was")

    def test_clear_refuses_busy_while_a_turn_is_open(self):
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        _hold_turn_open(fake)
        self.assertTrue(be.send(sid, "long"))
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"), "the turn is open")
        self.assertEqual(be.clear(sid), "busy")
        self.assertEqual(len(fake.called("thread_start")), 1, "no thread minted under an open turn")
        self.assertIs(be.clearing(sid), False)
        self.assertTrue(be.interrupt(sid))
        self.assertTrue(until(lambda: not be.busy(sid)))

    def test_clear_refuses_busy_for_a_queued_send_the_worker_has_not_taken(self):
        # The belt behind the turn lock: a send that is queued but whose worker has not yet taken the lock
        # reads busy() True to the kernel, and a clear that took the lock first would land that message on
        # the fresh thread — a message typed BEFORE the /clear, answered without its context.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        s = be._session(sid)
        stop = threading.Event()
        stand_in = threading.Thread(target=stop.wait, daemon=True)   # alive, so no real worker starts
        stand_in.start()
        with s.lock:
            s.worker = stand_in
        try:
            self.assertTrue(be.send(sid, "typed before the clear"))
            self.assertTrue(be.busy(sid))
            self.assertEqual(be.clear(sid), "busy")
            self.assertEqual(len(fake.called("thread_start")), 1)
        finally:
            stop.set()
            stand_in.join(5)
            with s.lock:
                s.worker = None
        self.assertTrue(be.wake(sid))
        self.assertTrue(until(lambda: not be.busy(sid)))
        self.assertEqual(fake.called("turn_start")[-1][1], "T-1", "the queued send ran on the thread it was typed into")

    def test_a_prompt_typed_during_the_bracket_starts_the_fresh_conversation_at_a_root(self):
        # The normalizer reset belongs UNDER the turn lock (review find, 2026-09-19). A prompt typed while
        # thread/start is in flight is handed over (busy() reads False through the bracket) and queues a worker
        # on mode_lock; the instant the clear releases it, the worker takes _ensure_norm as a local for its whole
        # turn. With the reset AFTER the release, a scheduler switch let that read return the OLD normalizer (the
        # old thread id, the old leaf as parent) while transcript_path already followed the NEW tid: the fresh
        # file's first record chained to the old conversation, stamped with the old thread — no root head, so no
        # boundary, ever, for that file. The stand-in lock makes the switch certain rather than rare: the clear's
        # release returns only after the worker has read its normalizer, so the ORDER of reset and release is the
        # whole outcome.
        class Blocking(FakeClient):
            def __init__(self):
                super().__init__()
                self.block = threading.Event()
                self.entered = threading.Event()

            def thread_start(self, params=None):
                if self.called("thread_start"):          # spawn's first create returns at once
                    self.entered.set()
                    self.block.wait(5)
                return super().thread_start(params)

        fake = Blocking()
        be, _, tmp = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "first synthetic turn"))
        self.assertTrue(_lock_free(be, sid))
        s = be._session(sid)
        worker_read = threading.Event()
        real_ensure = be._ensure_norm

        def ensure(sess):
            out = real_ensure(sess)
            if sess is s and threading.current_thread() is getattr(sess, "worker", None):
                worker_read.set()                        # the worker holds its normalizer for the turn from here
            return out
        be._ensure_norm = ensure
        clearer = []

        class Handover:
            """s.mode_lock's stand-in: every take and release passes through; the CLEAR's release returns only
            once the worker queued behind it has read its normalizer."""
            def __init__(self, real):
                self._real = real

            def acquire(self, blocking=True, timeout=-1):
                return self._real.acquire(blocking, timeout)

            def release(self):
                self._real.release()
                if clearer and threading.current_thread() is clearer[0]:
                    worker_read.wait(5)

            def __enter__(self):
                self._real.acquire()
                return self

            def __exit__(self, *exc):
                self._real.release()
                return False
        with s.lock:
            s.mode_lock = Handover(s.mode_lock)
        out = []
        th = threading.Thread(target=lambda: out.append(be.clear(sid)), daemon=True)
        clearer.append(th)
        th.start()
        self.assertTrue(fake.entered.wait(5), "thread/start is in flight")
        self.assertIs(be.clearing(sid), True)
        self.assertFalse(be.busy(sid), "the kernel's gate reads not busy through the bracket: the send is handed over")
        self.assertTrue(be.send(sid, "typed during the bracket"))
        fake.block.set()
        th.join(10)
        self.assertEqual(out, [""])
        self.assertTrue(worker_read.is_set(), "the worker read its normalizer on the release")
        self.assertTrue(until(lambda: not be.busy(sid) and not be.pending_queued(sid)), "the typed prompt ran")
        self.assertEqual(fake.called("turn_start")[-1][1], "T-2", "on the fresh thread")
        new_file = Path(tmp) / "codex" / "projects" / cb._enc_cwd("/TESTDIR") / "T-2.jsonl"
        first = json.loads(new_file.read_text().splitlines()[0])
        self.assertIsNone(first.get("parentUuid"),
                          "the fresh conversation's first record is a ROOT, not a child of the old leaf: %r" % first)
        self.assertEqual(first["sessionId"], "T-2", "and it carries the fresh thread's id, not the old one")

    def test_a_raise_after_the_swap_leaves_the_clear_done_and_kicks_the_queue(self):
        # Everything after the swap is bookkeeping on a clear that HAPPENED (review find, 2026-09-19): the live
        # chip, its durable twin on disk, the Codex-side name. Unguarded, the twin's write raising at ENOSPC
        # escaped a verb whose contract promises a string — the route logged it and said nothing, the drain's
        # per-sid except dropped the sid's whole queue, and the kick a queue parked on the old thread's
        # rejection waits for never came: it sat un-run on a thread that no longer existed.
        class InvalidParamsError(RuntimeError):
            def __init__(self, message):
                super().__init__(message)
                self.code = -32602

        class Rejecting(FakeClient):
            def turn_start(self, tid, input_items, params=None):
                if tid == "T-1":
                    self._rec("turn_start", tid, input_items, params, None)
                    raise InvalidParamsError("model is not available")
                return super().turn_start(tid, input_items, params)

        fake = Rejecting()
        logs = []
        tmp = tempfile.mkdtemp()
        be = cb.CodexBackend(tmp, client_factory=lambda: fake, log=logs.append)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(until(lambda: be.launch_error(sid) is not None))
        self.assertTrue(_lock_free(be, sid))
        with mock.patch.object(cb, "_append_cmd_gesture", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(be.clear(sid), "", "the clear happened: the answer says so whatever the tail did")
        self.assertEqual(self._row(be, sid)["tid"], "T-2", "the swap is durable")
        self.assertIs(be.clearing(sid), False)
        self.assertIn("/clear", [a.get("command") for a in be.live_atoms(sid)],
                      "the live chip was appended before the twin's write failed (beside the queued send's echo)")
        rows = (Path(tmp) / "states" / (sid + ".jsonl"))
        self.assertFalse(rows.exists() and "cmdGesture" in rows.read_text(), "the twin did not land")
        self.assertTrue(any("No space left" in m for m in logs), "the raise is logged, not swallowed: %r" % logs)
        self.assertTrue(until(lambda: not be.pending_queued(sid) and not be.busy(sid)),
                        "the queue parked on the old thread's rejection was kicked into the fresh one")
        last = fake.called("turn_start")[-1]
        self.assertEqual(last[1], "T-2")
        self.assertEqual([i["text"] for i in last[2]], ["keep this durable"])

    def test_clear_leaves_a_command_chip_that_the_next_human_record_retires(self):
        be, fake, tmp, sid = self._turned()
        self.assertEqual(be.clear(sid), "")
        atoms = be.live_atoms(sid)
        self.assertEqual(len(atoms), 1)
        a = atoms[0]
        self.assertEqual((a["_echo_text"], a["command"]), ("/clear", "/clear"))
        self.assertTrue(a["uuid"].startswith("cmd:"), "the SDK's acknowledging-chip id form, not a kernel echo id")
        self.assertEqual(a["fsid"], "T-2", "the chip belongs to the fresh conversation")
        rows = [json.loads(l) for l in (Path(tmp) / "states" / (sid + ".jsonl")).read_text().splitlines()]
        self.assertIn({"t": a["t"], "cmdGesture": "/clear"}, rows,
                      "the durable twin: it renders in the fresh conversation until the boundary tick, then closes the "
                      "cleared conversation's episode")
        be.prune_live(sid, set(), {}, human_floor=a["t"])
        self.assertEqual(len(be.live_atoms(sid)), 1, "a human record in the chip's own second keeps it")
        be.prune_live(sid, set(), {}, human_floor=a["t"] + 1)
        self.assertEqual(be.live_atoms(sid), [], "the next human record retires the chip")
        s = be._session(sid)
        with s.lock:
            s.echoes.append({"text": "plain", "t": a["t"], "uuid": "echo-22222222"})
        be.prune_live(sid, set(), {}, human_floor=a["t"] + 100)
        self.assertEqual(len(be.live_atoms(sid)), 1, "no floor retires a PLAIN echo (the existing rule)")

    def test_a_queue_parked_on_a_rejection_rides_into_the_fresh_thread(self):
        class InvalidParamsError(RuntimeError):
            def __init__(self, message):
                super().__init__(message)
                self.code = -32602

        class Rejecting(FakeClient):
            def turn_start(self, tid, input_items, params=None):
                if tid == "T-1":
                    self._rec("turn_start", tid, input_items, params, None)
                    raise InvalidParamsError("model is not available")
                return super().turn_start(tid, input_items, params)

        fake = Rejecting()
        be, _, _ = build(factory=lambda: fake)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(until(lambda: be.launch_error(sid) is not None))
        self.assertFalse(be.busy(sid), "parked on the rejection: not busy")
        self.assertTrue(_lock_free(be, sid))
        self.assertEqual(be.clear(sid), "")
        self.assertTrue(until(lambda: not be.pending_queued(sid) and not be.busy(sid)))
        last = fake.called("turn_start")[-1]
        self.assertEqual(last[1], "T-2")
        self.assertEqual([i["text"] for i in last[2]], ["keep this durable"], "the durable queue rode the swap")
        self.assertIsNone(be.launch_error(sid), "the fresh thread starts clean")

    def test_a_clear_on_the_turn_end_poke_finds_the_lock_free(self):
        # The turn-end poke is the kernel's cue to drain parked ops (the drain then calls clear). It used to
        # fire from inside the worker's turn lock, so a clear parked mid-turn met "busy" on the very poke
        # that announced the turn's end and waited for the pusher's clock instead. The poke now follows the
        # lock's release: the event and the retry are the same moment.
        be, fake, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        _hold_turn_open(fake)
        answers = []
        real = be.poke

        def poke():
            if not be.busy(sid) and not answers:
                answers.append(be.clear(sid))
            real()
        be.poke = poke
        self.assertTrue(be.send(sid, "long"))
        self.assertTrue(until(lambda: be.live_sessions()[sid]["state"] == "working"), "the turn is open")
        self.assertTrue(be.interrupt(sid))
        self.assertTrue(until(lambda: answers))
        self.assertEqual(answers, [""], "the clear pressed on the turn-end poke runs, never 'busy'")
        self.assertEqual(fake.called("thread_start")[-1][1]["sessionStartSource"], "clear")
        self.assertEqual(self._row(be, sid)["tid"], "T-2")

    def test_clear_on_a_dead_or_unknown_session_refuses_in_words(self):
        be, fake, _, sid = self._turned()
        self.assertTrue(be.kill(sid))
        why = be.clear(sid)
        self.assertIn("ended", why)
        self.assertNotIn("backend", why)
        self.assertEqual(len(fake.called("thread_start")), 1)
        why = be.clear("11111111-2222-4333-8444-000000000000")
        self.assertTrue(why and why != "busy", "an unknown sid gets the contract's refusal, never a raise")

    # ── the fresh thread across a kernel restart ─────────────────────────────────────────────────

    def _restarted(self, tmp, client):
        be2 = cb.CodexBackend(tmp, client_factory=lambda: client)
        return be2

    class _NoRolloutError(RuntimeError):
        """The pinned SDK's InvalidRequestError shape for `-32600 no rollout found for thread id …`, which the
        app-server answers a thread/resume of a thread with no turn yet (live probe, 2026-09-19)."""
        def __init__(self, message):
            super().__init__(message)
            self.code = -32600
    _NoRolloutError.__name__ = "InvalidRequestError"

    def test_a_restart_before_the_first_turn_recreates_the_fresh_thread_instead_of_parking_the_resume(self):
        outer = self

        class Fresh(FakeClient):
            """A new app-server after a kernel restart: it has never seen T-2 and no rollout exists for a
            thread that ran no turn, so its resume fails; its own thread ids must not collide with the
            files the first server's threads left."""
            def thread_resume(self, tid, params=None):
                self._rec("thread_resume", tid, params)
                raise outer._NoRolloutError("JSON-RPC error -32600: Invalid request: no rollout found for thread id %s" % tid)

            def thread_start(self, params=None):
                self._rec("thread_start", params)
                return SimpleNamespace(thread=SimpleNamespace(id="R-%d" % len(self.called("thread_start"))),
                                       model="gpt-5-test")

        be, fake, tmp, sid = self._turned()
        self.assertEqual(be.clear(sid), "")
        self.assertEqual(self._row(be, sid)["tid"], "T-2")
        for _, sess in be._session_items():                  # the old kernel's worker goes
            with sess.lock:
                sess.dead = True
            sess.kick.set()
        fake.close()
        logs = []
        fresh = Fresh()
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fresh, log=logs.append)
        self.assertEqual(be2._session(sid).tid, "T-2", "the restart reads the fresh tid")
        self.assertTrue(be2.send(sid, "first prompt after the restart"))
        self.assertTrue(until(lambda: not be2.busy(sid) and not be2.pending_queued(sid)))
        self.assertEqual([c[1] for c in fresh.called("thread_resume")], ["T-2"], "one resume, refused by the server")
        starts = fresh.called("thread_start")
        self.assertEqual(len(starts), 1, "ONE re-create, no loop")
        self.assertEqual(starts[0][1]["cwd"], "/TESTDIR")
        self.assertEqual(starts[0][1]["model"], "gpt-5-picked", "the pick outlives the re-create")
        self.assertEqual(starts[0][1]["approvalPolicy"], "on-request", "the row's mode rides")
        row = self._row(be2, sid)
        self.assertEqual(row["tid"], "R-1", "the row names the re-created thread")
        self.assertIsNone(row["launchError"])
        self.assertEqual(fresh.called("turn_start")[-1][1], "R-1", "the prompt ran on it")
        enc = cb._enc_cwd("/TESTDIR")
        self.assertFalse((Path(tmp) / "codex" / "projects" / enc / "T-2.jsonl").exists(),
                         "the empty file of the thread that never ran leaves with it")
        new_file = Path(tmp) / "codex" / "projects" / enc / "R-1.jsonl"
        first = json.loads(new_file.read_text().splitlines()[0])
        self.assertIsNone(first.get("parentUuid"), "still a ROOT head: the boundary lands on this prompt")
        self.assertTrue(any("no rollout" in m and "T-2" in m for m in logs), "loudly logged: %r" % logs)

    def test_a_no_rollout_answer_for_a_thread_that_did_run_stays_a_parked_rejection(self):
        # The re-create is keyed on BOTH facts: the server's exact refusal AND romp's own record that no turn
        # ever ran on the thread (its materialized file is empty). A refusal for a thread whose file holds
        # records is a real inconsistency (the rollout went missing server-side) and parks loudly as before,
        # never re-creates over a conversation romp has records of.
        outer = self

        class Fresh(FakeClient):
            def thread_resume(self, tid, params=None):
                self._rec("thread_resume", tid, params)
                raise outer._NoRolloutError("JSON-RPC error -32600: Invalid request: no rollout found for thread id %s" % tid)

        be, fake, tmp, sid = self._turned()      # T-1 ran a turn: its file has records
        for _, sess in be._session_items():
            with sess.lock:
                sess.dead = True
            sess.kick.set()
        fake.close()
        fresh = Fresh()
        be2 = cb.CodexBackend(tmp, client_factory=lambda: fresh)
        self.assertTrue(be2.send(sid, "after the restart"))
        self.assertTrue(until(lambda: be2.launch_error(sid) is not None))
        self.assertIn("no rollout found", be2.launch_error(sid)["text"])
        self.assertEqual(fresh.called("thread_start"), [], "no re-create over a conversation with records")
        self.assertEqual(self._row(be2, sid)["tid"], "T-1")
        self.assertEqual(be2.pending_queued(sid), ["after the restart"], "the send stays, parked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
