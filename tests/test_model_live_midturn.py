#!/usr/bin/env python3
"""A model pick applies LIVE mid-turn on a forwards_sends backend, instead of parking until the turn ends.

Why: SDKBackend.set_model applies the change "LIVE on a connected session via the SDK control channel —
NOT a /model slash injection, which the SDK input stream does not interpret". So the reason _send_or_park
parks a typed slash command mid-turn (the CLI only EXECUTES one as a fresh top-level prompt) does not apply
to a model pick, and an open turn alone was deferring it for the whole turn. On a long agentic turn that is
hours, and the interactive CLI applies /model to its very next request — so the queue behaviour surprised
users who expected the terminal's.

The rule now mirrors _send_or_park's own, for the same backends: a forwards_sends backend takes input
mid-turn, so the pick goes over now and lands on the turn's next request. Every other park reason stands.
Synthetic only — no real session data."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate: importing the kernel must not touch live state
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = SourceFileLoader("romp_kernel_model_live", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class _Tmux:
    """A backend that cannot take input mid-turn (forwards_sends False) — set_model TYPES /model into a pane."""
    def __init__(self): self.calls = []
    def owns(self, sid): return True
    def forwards_sends(self): return False
    def set_model(self, sid, v): self.calls.append(("model", v))
    def send(self, sid, t): self.calls.append(("send", t))


class _Sdk(_Tmux):
    """A forwards_sends backend — the SDK/codex shape: takes a send, and a control-channel model change, mid-turn."""
    def forwards_sends(self): return True
    def unqueue(self, sid, idx, expect=None): return expect


class ModelLiveMidTurn(unittest.TestCase):
    def setUp(self):
        self.tmux, self.sdk = _Tmux(), _Sdk()
        km._pending_ops.pop(SID, None)
        self._saved = (km._compacting_now, km._working_now, km._limit_hold, km._mark_model_pending,
                       km._note_model_pick, km.Sessions.backend_for)
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: True            # a turn is OPEN for every test here
        km._limit_hold = lambda sid: None             # the account gate is its own axis (test_kernel_limit_queue)
        km._mark_model_pending = lambda *a, **k: None
        km._note_model_pick = lambda *a, **k: None
        km.Sessions.backend_for = lambda sid: self.sdk
        km._moving.discard(SID) if hasattr(km._moving, "discard") else None

    def tearDown(self):
        (km._compacting_now, km._working_now, km._limit_hold, km._mark_model_pending,
         km._note_model_pick, km.Sessions.backend_for) = self._saved
        km._pending_ops.pop(SID, None)
        if hasattr(km._moving, "discard"):
            km._moving.discard(SID)

    # ── the change ──
    def test_an_open_turn_no_longer_parks_a_model_pick_on_a_forwards_sends_backend(self):
        km._set_model_or_park(self.sdk, SID, "claude-fable-5-1")
        self.assertEqual(self.sdk.calls, [("model", "claude-fable-5-1")],
                         "the control channel takes it now; it lands on the turn's next request")
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
    def test_tmux_still_parks_while_a_turn_is_open(self):
        km.Sessions.backend_for = lambda sid: self.tmux
        km._set_model_or_park(self.tmux, SID, "claude-fable-5-1")
        self.assertEqual(self.tmux.calls, [], "typing /model into a busy pane is the race the FIFO exists for")
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

    # ── the neighbour that must NOT change ──
    def test_effort_still_parks_while_working_because_it_is_connect_time(self):
        # --effort is a connect-time CLI flag with no runtime control, so it genuinely has to wait for the
        # turn to end; only the model rides a control channel. Guards against over-reach.
        km._set_effort_or_park(self.sdk, SID, "xhigh")
        self.assertEqual(self.sdk.calls, [], "effort cannot apply mid-turn")
        self.assertEqual(km._pending_ops.get(SID), [("effort", "xhigh")])


if __name__ == "__main__":
    unittest.main()
