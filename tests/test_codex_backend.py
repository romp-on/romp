#!/usr/bin/env python3
"""CodexBackend contract tests — a scripted FAKE client (no SDK, no network, no login) drives the
backend through spawn/send/steer/interrupt/kill/resume and pins: the worker's turn loop writes the
materialized transcript, state transitions are event-based, the uuid chain survives a backend
restart (the _tail_state re-anchor), Claude-only knobs refuse loudly, and a missing `codex login`
surfaces as launch_error text instead of a silent non-start. All data synthetic per CLAUDE.md.

Run:    python3 tests/test_codex_backend.py
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
cb = SourceFileLoader("romp_codex_backend", os.path.join(ROOT, "kernel", "codex_backend.py")).load_module()
sb = SourceFileLoader("romp_session_backend", os.path.join(ROOT, "kernel", "session_backend.py")).load_module()


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
            SimpleNamespace(id="gpt-5-test", display_name="GPT-5 Test", hidden=False),
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
            script = [
                ("item/completed", {"threadId": tid, "turnId": turn_id, "completedAtMs": ms,
                                    "item": {"type": "userMessage", "id": "u-%d" % self._n,
                                             "content": [{"type": "text", "text": text}]}}),
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
        return self._global.get()   # blocks forever — the global pump just parks in tests


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
        with self.assertRaises(RuntimeError):
            be._handle_approval("unknown/requestApproval", {})
        self.assertTrue(notices)
        self.assertTrue(all(msg["type"] == "warn" for msg in notices))

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
        # high, a second attempt inside the observation window can only be a real hot spin;
        # the recovery phase then releases the backoff EXPLICITLY instead of racing a timer.
        saved_floor = cb.CLIENT_RETRY_MIN
        cb.CLIENT_RETRY_MIN = 30.0
        self.addCleanup(setattr, cb, "CLIENT_RETRY_MIN", saved_floor)
        be, _, _ = build(factory=flaky_factory)
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.send(sid, "retry me"))
        self.assertTrue(until(lambda: len(attempts) == 1), "the first attempt fires")
        time.sleep(0.05)
        self.assertEqual(len(attempts), 1, "unavailable client must not hot-spin")
        with be._client_lock:
            be._client_retry_at = 0.0                  # the explicit release, not a timer race
        for _, s in be._session_items():
            s.kick.set()
        # a GENEROUS bound: the recovery is event-shaped (the explicit release above is
        # the event), so only "eventually" matters — the 3s bound starved the worker
        # thread on a runner at load 20+ (the r63 release gate: a 55-minute full suite)
        self.assertTrue(until(lambda: len(attempts) >= 2 and not be.busy(sid), timeout=20))
        self.assertEqual(len(fake.called("thread_start")), 1,
                         "the pending placeholder must become a real Codex thread")
        self.assertFalse(be.pending_queued(sid))

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
from importlib.machinery import SourceFileLoader
cb = SourceFileLoader("codex_child", sys.argv[1]).load_module()
be = cb.CodexBackend(sys.argv[2], client_factory=lambda: None, log=lambda message: None)
s = be._session(sys.argv[3])
for i in range(20):
    text = "%s:%d" % (sys.argv[4], i)
    be._save_registry(s, queue_append={"id": "child-" + text, "text": text})
'''
        backend_path = str(Path(ROOT) / "kernel" / "codex_backend.py")
        procs = [subprocess.Popen([sys.executable, "-c", code, backend_path, tmp, sid, str(n)])
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

    def test_claude_only_knobs_refuse(self):
        be, _, _ = build()
        sid = be.spawn("web", "/TESTDIR")
        self.assertTrue(be.set_effort(sid, "xhigh"))    # Codex takes xhigh natively
        self.assertFalse(be.set_effort(sid, "max"))     # Claude-only → loud refusal
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
        self.assertEqual(cat, [{"value": "gpt-5-test", "label": "GPT-5 Test"}])
        be.model_catalog()
        self.assertEqual(len(fake.called("model_list")), 1, "catalog is fetched once, then cached")

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


class LaunchErrorNames(unittest.TestCase):
    """A LIVE launch-error row without a shared name let a retry mint a duplicate live session
    under the same name (the v1.3.12 audit's P2) — both failure branches now write names/."""

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

    def test_a_truncating_names_write_in_rename_is_restored(self):
        # the r30 mutant hunt: the chmod-0444 fixture fails AT OPEN without truncating, so
        # deleting the bytes-restore stayed green — this drives the ENOSPC shape the fix names
        # (write_text truncates, THEN fails)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            nf = os.path.join(td, "names", sid)
            old_line = open(nf).read()

            def truncating_write(s2, bg="", fg=""):
                open(nf, "w").close()              # the open('w') truncation
                raise OSError(28, "No space left on device")
            with mock.patch.object(be, "_write_name", side_effect=truncating_write):
                with self.assertRaises(OSError):
                    be.rename(sid, "newname")
            self.assertEqual(open(nf).read(), old_line,
                             "the compensation restores the truncated identity line")
            self.assertEqual(be._session(sid).name, "webby")

    def test_a_failed_rename_of_an_absent_names_file_stays_unpublished(self):
        # the r30 verification: with no file to snapshot, _write_name CREATED a partial file
        # holding the NEW name — the failed rename stayed published, silently
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = cb.CodexBackend(td, client_factory=lambda: None)
            sid = be.spawn("webby", "/tmp")
            nf = os.path.join(td, "names", sid)
            os.unlink(nf)                          # a legacy row predating the names write

            def partial_write(s2, bg="", fg=""):
                with open(nf, "w") as f:
                    f.write("newname\t")           # partial line lands, then the write dies
                raise OSError(28, "No space left on device")
            with mock.patch.object(be, "_write_name", side_effect=partial_write):
                with self.assertRaises(OSError):
                    be.rename(sid, "newname")
            self.assertFalse(os.path.exists(nf),
                             "the partial NEW-name file is removed — a failed rename must not "
                             "stay published")

    def test_a_names_write_failure_after_thread_start_is_restored_not_fatal(self):
        # the r30 verification: _prepare_thread's unguarded names write truncated the identity
        # file permanently — no later path rewrites it (resume skips the create branch)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            nf = os.path.join(td, "names", sid)
            old_line = open(nf).read()
            s.tid = "pending-%s" % sid[:8]         # force the create path
            s.loaded = False

            def truncating_write(s2, bg="", fg=""):
                open(nf, "w").close()
                raise OSError(28, "No space left on device")
            with mock.patch.object(be, "_write_name", side_effect=truncating_write):
                ok = be._prepare_thread(s, fake)
            self.assertTrue(ok, "the thread is healthy — the turn proceeds")
            self.assertEqual(open(nf).read(), old_line,
                             "the truncation cannot outlive the failure")
            self.assertTrue(s.loaded)

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
        # the duplicate-name hole, which the log must NAME
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake = FakeClient()
            be = cb.CodexBackend(td, client_factory=lambda: fake)
            sid = be.spawn("webby", "/tmp")
            s = be._session(sid)
            nf = os.path.join(td, "names", sid)
            os.unlink(nf)                          # no prior file to restore
            s.tid = "pending-%s" % sid[:8]
            s.loaded = False
            logs = []
            with mock.patch.object(be, "log", side_effect=lambda m: logs.append(m)):
                with mock.patch.object(be, "_write_name",
                                       side_effect=OSError(28, "No space left on device")):
                    ok = be._prepare_thread(s, fake)
            self.assertTrue(ok, "the healthy turn still proceeds")
            self.assertTrue(any("could not be published" in m for m in logs), logs)
            self.assertFalse(any("was restored" in m for m in logs),
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
            prior_model = s.model

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
