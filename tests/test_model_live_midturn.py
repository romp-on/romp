#!/usr/bin/env python3
"""A model pick fires into an OPEN turn on a backend that says it can take one (model_switches_live), instead
of parking in the FIFO until the turn ends — and keeps parking on every backend that cannot.

Why: on an hours-long agentic turn a parked pick sat as a queued chip for a day while the session kept
answering on the old model (PR #923). A pick is not a slash injection on the SDK — SdkBackend.set_model rides
the CLI's control channel — so the reason _send_or_park parks a typed slash command mid-turn does not apply
to it, and an open turn alone need not defer it. The rule is keyed on the backend's OWN capability, NOT on
forwards_sends: forwarding a plain send says nothing about how a model change applies (Codex forwards sends
but applies set_model at the next turn_start). The real SDK backend declares True on this install and
False upstream, where on CLI 2.1.257 a mid-turn switch mis-parented its transcript breadcrumbs and the rest
of the turn was lost as a rewound branch — see SdkBackend.model_switches_live. The fakes below pin the rule
each way. Every other park reason stands.
Synthetic only — no real session data."""
import os
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from types import SimpleNamespace

from tests.conftest import thread_census, wait_for_census

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate: importing the kernel must not touch live state
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_model_live", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class _Typed:
    """A backend of the ABC's base shape: it cannot take input mid-turn (forwards_sends False) and its set_model
    lands as text typed at the CLI (the removed terminal backend typed "/model X" into its pane), so a pick
    mid-turn races the running turn. No shipped backend has this shape since that backend's removal
    (2026-09-11); the fake keeps the rule's parking arm pinned."""
    def __init__(self): self.calls = []
    def owns(self, sid): return True
    def forwards_sends(self): return False
    def set_model(self, sid, v): self.calls.append(("model", v))
    def send(self, sid, t): self.calls.append(("send", t))


class _Sdk(_Typed):
    """A backend that takes a send mid-turn (forwards_sends) AND can apply a model change mid-turn
    (model_switches_live) — the shape the open-turn exception is for, and the real SdkBackend's shape on
    this install (see its docstring)."""
    def forwards_sends(self): return True
    def model_switches_live(self): return True
    def unqueue(self, sid, idx, expect=None): return expect


class _Codex(_Sdk):
    """The Codex shape: forwards sends (they STEER the live turn) but set_model lands at the NEXT turn_start —
    so a pick must still wait for the turn, or a send typed after it would steer the OLD model first."""
    def model_switches_live(self): return False


class ModelLiveMidTurn(unittest.TestCase):
    def setUp(self):
        self.typed, self.sdk = _Typed(), _Sdk()
        km._pending_ops.pop(SID, None)
        self._saved = (km._compacting_now, km._working_now, km._limit_hold, km._mark_model_pending,
                       km._note_model_pick, km.Sessions.backend_for)
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: True            # a turn is OPEN for every test here
        km._limit_hold = lambda sid: None             # the account gate is its own axis (test_kernel_limit_queue)
        km._mark_model_pending = lambda *a, **k: None
        km._note_model_pick = lambda *a, **k: None
        km.Sessions.backend_for = lambda sid: self.sdk
        km._moving.discard(SID)                       # a plain set (kernel _moving)

    def tearDown(self):
        (km._compacting_now, km._working_now, km._limit_hold, km._mark_model_pending,
         km._note_model_pick, km.Sessions.backend_for) = self._saved
        km._pending_ops.pop(SID, None)
        km._moving.discard(SID)

    # ── the change ──
    def test_an_open_turn_does_not_park_a_model_pick_on_a_backend_that_switches_live(self):
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [("model", "claude-fable-5-1")],
                         "the backend takes it now; it lands on the turn's next request")
        self.assertNotIn(SID, km._pending_ops, "and it creates no queue, so nothing chains behind it")

    def test_press_order_holds_when_a_send_follows_a_live_pick(self):
        # the invariant the old park protected: a message typed after a model pick must not reach the model
        # BEFORE the switch. It still cannot — both fire, in the order pressed.
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        km._send_or_park(self.sdk, SID, "now use the new one", echo="human")
        self.assertEqual(self.sdk.calls,
                         [("model", "claude-fable-5-1"), ("send", "now use the new one")],
                         "model first, then the message — press order, both immediate")
        self.assertNotIn(SID, km._pending_ops)

    # ── every other park reason still stands ──
    def test_a_typing_backend_still_parks_while_a_turn_is_open(self):
        km.Sessions.backend_for = lambda sid: self.typed
        km._set_model_or_park(self.typed, SID, "claude-fable-5-1")
        self.assertEqual(self.typed.calls, [], "typing /model into a busy composer is the race the FIFO exists for")
        self.assertEqual(km._pending_ops.get(SID), [("model", "claude-fable-5-1")])

    def test_a_compaction_still_parks_it(self):
        km._compacting_now = lambda sid: True
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [], "the client is being torn down; the control channel has no peer")
        self.assertEqual(km._pending_ops.get(SID), [("model", "claude-fable-5-1")])

    def test_an_existing_queue_still_parks_it_in_press_order(self):
        # an earlier op is still owed its turn (the user 2026-07-02 x2) — jumping it would reorder
        km._park_op(SID, ("send", "typed earlier", "human"))
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [])
        self.assertEqual(km._pending_ops.get(SID),
                         [("send", "typed earlier", "human"), ("model", "claude-fable-5-1")],
                         "behind the send that was pressed first")

    def test_a_limit_hold_still_parks_it(self):
        km._limit_hold = lambda sid: {"reason": "limit", "resetsAt": None, "what": "waiting"}
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [], "the account cannot serve a request at all")
        self.assertEqual(km._pending_ops.get(SID), [("model", "claude-fable-5-1")])

    def test_a_quiet_session_is_unchanged(self):
        km._working_now = lambda sid: False
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [("model", "claude-fable-5-1")])
        self.assertNotIn(SID, km._pending_ops)

    def test_a_forwards_sends_backend_whose_switch_is_not_live_still_parks(self):
        # the exception is keyed on model_switches_live, NOT forwards_sends: Codex forwards a send mid-turn
        # (steering the live turn) but applies set_model only at the next turn_start — fired live, the pick
        # would let the send typed after it reach the old model first, the inversion the press-order rule
        # forbids (review fold, 2026-09-04)
        codex = _Codex()
        km._set_model_or_park(codex, SID, "claude-fable-5-1")
        km._send_or_park(codex, SID, "after the pick", echo="human")
        self.assertEqual(codex.calls, [], "the pick parks, and the send chains behind it")
        self.assertEqual(km._pending_ops.get(SID),
                         [("model", "claude-fable-5-1"), ("send", "after the pick", "human")], "press order held")

    def test_a_backend_without_the_capability_reads_as_not_live(self):
        # getattr-guarded like _forwards_sends: a fake / backend that never heard of the capability parks,
        # which is exactly the pre-#923 rule
        class _Bare:
            def forwards_sends(self): return True
        self.assertFalse(km._model_switches_live(_Bare()))
        self.assertTrue(km._model_switches_live(self.sdk))
        self.assertFalse(km._model_switches_live(_Codex()))

    def test_a_move_in_flight_still_parks_it(self):
        km._moving.add(SID)
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [], "a move is tearing the client down; the control channel has no peer")
        self.assertEqual(km._pending_ops.get(SID), [("model", "claude-fable-5-1")])

    # ── the setter's verdict is what the send route reports ──
    def test_the_setter_says_whether_it_parked(self):
        self.assertFalse(km._set_model_or_park(self.sdk, SID, "claude-fable-5-1"), "fired now")
        km._compacting_now = lambda sid: True
        self.assertTrue(km._set_model_or_park(self.sdk, SID, "claude-fable-5-1"), "parked")

    def test_a_live_pick_is_not_reported_queued_by_the_send_route(self):
        # POST /send "/model X" (and `romp send`) read `queued` from _route_meta_command's `state`. Before the
        # fold that was inferred from _ops_gate — which an open turn still trips — so a pick that had ALREADY
        # applied live was answered `queued: true` (review find on #923, 2026-09-04). The setter's own verdict
        # is read now.
        state = {}
        self.assertTrue(km._route_meta_command(self.sdk, SID, "/model opus", state=state))
        self.assertEqual(self.sdk.calls, [("model", "opus")], "applied live")
        self.assertEqual(state, {"queued": False}, "and said so")

    def test_a_parked_pick_is_still_reported_queued_by_the_send_route(self):
        km._compacting_now = lambda sid: True
        state = {}
        self.assertTrue(km._route_meta_command(self.sdk, SID, "/model opus", state=state))
        self.assertEqual(self.sdk.calls, [])
        self.assertEqual(state, {"queued": True})
        self.assertEqual(km._pending_ops.get(SID), [("model", "opus")])

    def test_only_the_sdk_backend_declares_the_capability(self):
        # Codex applies a pick at the next turn_start and the base shape types it at the CLI, so both park.
        # The SDK, which HAS the control channel, declares True on this install (the user 2026-09-16, who
        # wanted picks applied mid-turn again: parked picks on busy sessions held every later send behind
        # them). Upstream it declares False because on CLI 2.1.257 a switch applied inside a turn mis-parented
        # its transcript breadcrumbs (review of #923, 2026-09-04); that caveat is watched, and the revert is
        # the one line in SdkBackend.model_switches_live plus this pin and the next.
        root = os.path.dirname(HERE)
        base = load_source("romp_session_backend_cap", os.path.join(root, "kernel", "session_backend.py"))
        sdk = load_source("romp_sdk_backend_cap", os.path.join(root, "kernel", "sdk_backend.py"))
        codex = load_source("romp_codex_backend_cap", os.path.join(root, "kernel", "codex_backend.py"))
        self.assertFalse(base.SessionBackend.model_switches_live(None))
        self.assertTrue(sdk.SdkBackend.model_switches_live(None),
                        "the SDK applies a mid-turn pick over its control channel on this install")
        self.assertFalse(codex.CodexBackend.model_switches_live(None))

    def test_the_real_sdk_backend_fires_a_pick_into_an_open_turn(self):
        # the rule read through the REAL backend's word: a fake wearing SdkBackend.model_switches_live takes
        # the pick now, creates no queue, and the send typed after it follows in press order, both immediate
        sdk = load_source("romp_sdk_backend_cap2", os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))
        class _RealWord(_Sdk):
            def model_switches_live(self): return sdk.SdkBackend.model_switches_live(None)
        be = _RealWord()
        km._set_model_or_park(be, SID, "claude-fable-5-1")
        km._send_or_park(be, SID, "after the pick", echo="human")
        self.assertEqual(be.calls, [("model", "claude-fable-5-1"), ("send", "after the pick")],
                         "the pick fires live and the send follows it, in press order")
        self.assertNotIn(SID, km._pending_ops, "nothing parked")

    def test_the_real_codex_backend_parked_on_a_rejected_model_lets_the_pick_that_fixes_it_through(self):
        # The wedge this rule met on the REAL backend (review, 2026-09-11): a Codex send the worker parked on a
        # permanent request rejection (the account refuses the model) kept busy() True with nothing in flight,
        # the real _working_now took that as an open turn, and this setter parked the pick — the one change
        # that re-arms the retry — behind a queue that would never move; the drain skipped the sid on the same
        # word every cycle, and no unqueue, kill or resume led out. A parked queue reads not-working now, so
        # the pick fires into set_model and the worker retries with the picked model.
        root = os.path.dirname(HERE)
        cb = load_source("romp_codex_backend_parked", os.path.join(root, "kernel", "codex_backend.py"))

        class InvalidParamsError(RuntimeError):
            def __init__(self, message):
                super().__init__(message)
                self.code = -32602

        class RejectingClient:
            """The slice of the app-server client that spawn and a rejected turn_start touch; nothing streams,
            and the pump parks on next_notification until close."""
            def __init__(self):
                self.attempts = []
                self._closed = threading.Event()
            def account_read(self, *a, **k):
                return SimpleNamespace(requires_openai_auth=False, account={"ok": True})
            def thread_start(self, params=None):
                return SimpleNamespace(thread=SimpleNamespace(id="T-1"), model="gpt-5-test")
            def thread_set_name(self, tid, name):
                pass
            def turn_start(self, tid, input_items, params=None):
                self.attempts.append(dict(params or {}))
                raise InvalidParamsError("model is not available")
            def next_notification(self):
                self._closed.wait()
                raise RuntimeError("client closed")
            def close(self):
                self._closed.set()

        def settle(fn, timeout=5.0):
            deadline = time.monotonic() + timeout
            while not fn() and time.monotonic() < deadline:
                time.sleep(0.01)
            return fn()

        census0 = thread_census()
        self.addCleanup(lambda: self.assertEqual(wait_for_census(census0, timeout=10), [],
                                                 "the backend's worker and pump end with the test"))
        fake = RejectingClient()
        be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: fake, log=lambda m: None)
        sid = be.spawn("web", "/TESTDIR")
        self.addCleanup(lambda: km._pending_ops.pop(sid, None))
        self.addCleanup(fake.close)
        self.addCleanup(be.kill, sid)
        km._working_now = self._saved[1]                  # the REAL gate: this case is about its answer
        km.Sessions.backend_for = lambda s: be
        self.assertTrue(be.send(sid, "keep this durable"))
        self.assertTrue(settle(lambda: be.launch_error(sid) is not None))   # written with the park, under the lock
        self.assertEqual(len(fake.attempts), 1)
        self.assertEqual(be.pending_queued(sid), ["keep this durable"])
        self.assertFalse(km._set_model_or_park(be, sid, "gpt-5-fixed"), "nothing is in flight: the pick fires now")
        self.assertIsNone(km._pending_ops.get(sid), "no chip parked behind the stuck queue")
        self.assertTrue(settle(lambda: len(fake.attempts) == 2))
        self.assertEqual(fake.attempts[1].get("model"), "gpt-5-fixed", "the worker retried with the picked model")

    # ── the neighbour that must NOT change ──
    def test_effort_still_parks_while_working_because_it_is_connect_time(self):
        # --effort is a connect-time CLI flag with no runtime control, so it genuinely has to wait for the
        # turn to end; only the model rides a control channel. Guards against over-reach.
        km._set_effort_or_park(self.sdk, SID, "xhigh")
        self.assertEqual(self.sdk.calls, [], "effort cannot apply mid-turn")
        self.assertEqual(km._pending_ops.get(SID), [("effort", "xhigh")])


if __name__ == "__main__":
    unittest.main()
