#!/usr/bin/env python3
"""Drive ops must ACK fast (the user 2026-07-05: model/effort picks and card replies lagged their visible
feedback). Two contracts:

1. A WS drive op NEVER builds the fleet payload synchronously on the handler thread — it mutates state,
   then wakes the pusher (_push_soon / _mark_views_dirty), which coalesces bursts. The old inline
   _push_all() made every click wait out a full multi-hundred-ms build before the UI heard anything,
   and serialized the client's next message behind it.

2. Optimistic kernel-side mutations (a parked-op chip, a model-pending stamp, an interrupt click, a
   follow-up reopen) live in MEMORY or a goal store — no file-mtime signature sees them — so they stamp
   _views_dirty, which _cached_feed/_cached_timeline honor past both the sig and REBUILD_MIN_S (that
   cache staleness was the "reply → card flies to Working, but with a delay" lag).

SYNTHETIC fixtures only.
"""
import inspect
import os
import time
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_pushresp", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(("send", sid, text)); return True

    def interrupt(self, sid):
        self.calls.append(("interrupt", sid)); return True

    def set_model(self, sid, value):
        self.calls.append(("set_model", sid, value)); return True

    def set_effort(self, sid, value):
        self.calls.append(("set_effort", sid, value)); return True

    def set_mode(self, sid, value):
        self.calls.append(("set_mode", sid, value)); return True


class DriveOpsAckFast(unittest.TestCase):
    """A drive op wakes the pusher instead of building the whole payload inline."""

    def setUp(self):
        self.be = _FakeBackend()
        self.pushes = []
        self._saved_name_of = km._name_of
        self._saved = (km._push_all, km._ops_gate, km.Sessions.backend_for, km._optimistic_echo)
        km._push_all = lambda: self.pushes.append(1)
        km._name_of = lambda sid: "web"   # these tests drive ops on a session this kernel HAS; _drive refuses one it doesn't (2026-07-29)
        km._ops_gate = lambda sid: False
        km.Sessions.backend_for = lambda sid: self.be
        km._optimistic_echo = lambda *a, **k: None
        km._pusher_wake.clear()
        km._pending_ops.clear()
        km._interrupt_clicked.clear()

    def tearDown(self):
        (km._push_all, km._ops_gate, km.Sessions.backend_for, km._optimistic_echo) = self._saved
        km._name_of = self._saved_name_of
        km._pusher_wake.clear()
        km._pending_ops.clear()
        km._interrupt_clicked.clear()
        km._model_switch_pending.clear()

    def _drive(self, msg):
        return km._drive(msg, {"send": lambda s: None})

    def test_send_message_wakes_the_pusher_without_an_inline_build(self):
        self.assertTrue(self._drive({"type": "sendMessage", "id": SID, "text": "hi"}))
        self.assertEqual(self.pushes, [], "no synchronous _push_all on the WS handler thread")
        self.assertTrue(km._pusher_wake.is_set(), "the pusher is woken to carry the echo NOW")

    def test_set_model_wakes_the_pusher_without_an_inline_build(self):
        self.assertTrue(self._drive({"type": "setModel", "id": SID, "value": "sonnet"}))
        self.assertEqual([c for c in self.be.calls if c[0] == "set_model"], [("set_model", SID, "sonnet")])
        self.assertEqual(self.pushes, [])
        self.assertTrue(km._pusher_wake.is_set())

    def test_set_effort_wakes_the_pusher_without_an_inline_build(self):
        self.assertTrue(self._drive({"type": "setEffort", "id": SID, "value": "high"}))
        self.assertEqual([c for c in self.be.calls if c[0] == "set_effort"], [("set_effort", SID, "high")])
        self.assertEqual(self.pushes, [])
        self.assertTrue(km._pusher_wake.is_set())

    def test_interrupt_stamps_and_marks_the_views_dirty(self):
        # the stamp is gated on a turn actually being open (_working_now): an idle press stops
        # nothing, so it paints nothing — stamping it stranded 'Interrupting…' for the 120s wedge
        # cap per press (the user 2026-08-14). Busy here, so the optimistic stamp lands as designed.
        saved = km._working_now
        km._working_now = lambda sid: True
        try:
            before = km._views_dirty[0]
            self.assertTrue(self._drive({"type": "interrupt", "id": SID}))
            self.assertIn(SID, km._interrupt_clicked, "the optimistic 'Interrupting…' stamp lands first")
            self.assertGreater(km._views_dirty[0], before,
                               "the stamp is in-memory — no sig sees it, so the views must dirty-rebuild")
            self.assertTrue(km._pusher_wake.is_set())
        finally:
            km._working_now = saved

    def test_an_idle_interrupt_paints_nothing_but_still_dispatches(self):
        saved = km._working_now
        km._working_now = lambda sid: False
        try:
            self.assertTrue(self._drive({"type": "interrupt", "id": SID}))
            self.assertNotIn(SID, km._interrupt_clicked,
                             "no open turn → no 'Interrupting…' stamp to strand (the user 2026-08-14)")
            self.assertEqual([c for c in self.be.calls if c[0] == "interrupt"], [("interrupt", SID)],
                             "the Esc/stop itself still reaches the backend")
        finally:
            km._working_now = saved

    def test_no_drive_op_calls_push_all_inline(self):
        src = inspect.getsource(km._drive)
        self.assertNotIn("_push_all()", src,
                         "drive ops wake the pusher (_push_soon/_mark_views_dirty); a synchronous "
                         "_push_all blocks the click behind a full fleet build")


class OptimisticMutationsDirtyTheViews(unittest.TestCase):
    """Every sig-invisible mutation stamps _views_dirty so the very next push rebuilds past the cache."""

    def tearDown(self):
        km._pending_ops.clear()
        km._model_switch_pending.clear()
        km._pusher_wake.clear()

    def test_park_op_marks_dirty(self):
        before = km._views_dirty[0]
        km._park_op(SID, ("model", "opus"))
        self.assertGreater(km._views_dirty[0], before, "the queued chip lives in _pending_ops (memory)")

    def test_mark_model_pending_marks_dirty(self):
        before = km._views_dirty[0]
        km._mark_model_pending(SID, "opus")
        self.assertGreater(km._views_dirty[0], before, "switching-dots live in _model_switch_pending (memory)")

    def test_apply_pending_ops_marks_dirty_when_the_queue_drains(self):
        src = inspect.getsource(km._apply_pending_ops)
        self.assertIn("_mark_views_dirty()", src, "a drained queue must retire its chips past the cache")

    def test_followup_reopen_marks_dirty(self):
        src = inspect.getsource(km._drive)
        self.assertIn("_mark_views_dirty()", src.split('t == "askFollowUp"')[-1].split("elif")[0],
                      "the optimistic goal-store reopen is invisible to the fleet sig")

    def test_clear_and_undo_mark_dirty(self):
        src = inspect.getsource(km.Handler._dispatch_ws)
        for op in ('"askClear"', '"clearAll"', '"undoClear"', '"nodeOverride"', '"dismissLane"'):
            # up to the next TOP-LEVEL handler, not the next "elif": a handler may branch internally
            # (nodeOverride distinguishes resolve from clear, and resolve from an already-resolved node),
            # and splitting on a bare "elif" cut the segment short at the first nested branch.
            seg = src.split(op)[1].split("elif msg and")[0]
            self.assertIn("_mark_views_dirty()", seg,
                          "%s writes state no sig sees — it must dirty-rebuild the views" % op)


if __name__ == "__main__":
    unittest.main()


class ModelPickerFlashSuppression(unittest.TestCase):
    """A kernel-driven /model switch pops the CLI's TUI picker for a beat before the confirm Enter lands
    (tmux; the user 2026-07-06, FRO: 'something popped up and then disappeared') — romp's own action, not
    a decision the human owes. _ask_poll suppresses a MODEL-titled ask while the switch is pending; the
    20s pending cap means a genuinely-stuck picker still surfaces for rescue."""

    def tearDown(self):
        km._model_switch_pending.clear()

    def test_model_picker_suppressed_while_the_switch_is_pending(self):
        km._model_switch_pending[SID] = {"target": "fable", "until": 1000 + 20}
        ask = {"title": "Switch model?", "options": ["Fable 5", "Opus 4.8"]}
        self.assertTrue(km._suppress_kernel_driven_ask(SID, ask, now=1005))

    def test_a_real_permission_ask_still_surfaces_mid_switch(self):
        km._model_switch_pending[SID] = {"target": "fable", "until": 1000 + 20}
        ask = {"title": "Bash command", "options": ["Yes", "No"]}
        self.assertFalse(km._suppress_kernel_driven_ask(SID, ask, now=1005),
                         "only the switch's own picker is romp's — a racing permission prompt is the human's")

    def test_expired_pending_surfaces_the_stuck_picker(self):
        km._model_switch_pending[SID] = {"target": "fable", "until": 1000 + 20}
        ask = {"title": "Switch model?"}
        self.assertFalse(km._suppress_kernel_driven_ask(SID, ask, now=1030),
                         "past the cap the confirm evidently failed → the human must see the picker")

    def test_no_pending_never_suppresses(self):
        self.assertFalse(km._suppress_kernel_driven_ask(SID, {"title": "Switch model?"}, now=1005))

    def test_ask_poll_wires_the_suppression(self):
        import inspect
        src = inspect.getsource(km._ask_poll)
        self.assertIn("_suppress_kernel_driven_ask(sid, ask)", src)
