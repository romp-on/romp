#!/usr/bin/env python3
"""ANY drive op sent while a session compacts is PARKED in ONE FIFO queue (the user 2026-07-02): messages,
/model AND /effort. The chat renders the queue as bubbles in park order, and _apply_pending_ops delivers
in exactly that order the moment compaction ends (the rendering IS the execution order — the user hit a
parked message rendering BEFORE the model change parked ahead of it). A repeated model/effort pick
replaces its earlier parked op in place. Same event-corroborated _compacting_now gate as ever; a parked
send stamps its optimistic echo only when it actually fires (an early echo killed the compacting cue).
SYNTHETIC fixtures only."""
import os
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
km = SourceFileLoader("romp_kernel_sendpark", os.path.join(BIN, "romp-kernel")).load_module()

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

# The tmux PROMPT HOLD (_hold_drain: a tmux-shaped delivery holds the sid for a moment, tested in
# tests/test_kernel_parked_ops_liveness.py) is a separate axis: off here, so back-to-back
# _apply_pending_ops calls stand for successive cycles.
km._TMUX_PROMPT_HOLD_S = 0.0

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def set_effort(self, sid, value):
        self.calls.append(("effort", value))
        return True

    def set_fast(self, sid, value):
        self.calls.append(("fast", value))
        return True


class OpQueueParkOrDeliver(unittest.TestCase):
    def setUp(self):
        self.be = _FakeBackend()
        self.echoes = []
        self._saved = (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
                       km._working_now)
        km._push_all = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": self.echoes.append((text, author))
        km._working_now = lambda sid: False            # explicit: each test picks the busy state
        km._pending_ops.clear()

    def tearDown(self):
        (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
         km._working_now) = self._saved
        km._pending_ops.clear()

    def test_not_compacting_everything_applies_immediately(self):
        km._compacting_now = lambda sid: False
        km._send_or_park(self.be, SID, "hello there", echo="human")
        km._set_model_or_park(self.be, SID, "opus")
        km._set_effort_or_park(self.be, SID, "high")
        self.assertEqual(self.be.calls, [("send", "hello there"), ("model", "opus"), ("effort", "high")])
        self.assertEqual(self.echoes, [("hello there", "human")], "the instant echo still fires")
        self.assertNotIn(SID, km._pending_ops)

    def test_compacting_parks_everything_in_order(self):
        # the user's exact repro: model change, then a message — the queue must hold THAT order
        km._compacting_now = lambda sid: True
        km._set_model_or_park(self.be, SID, "opus")
        km._send_or_park(self.be, SID, "now do the thing", echo="human")
        km._set_effort_or_park(self.be, SID, "medium")
        self.assertEqual(self.be.calls, [], "mid-compaction the backend is NOT touched")
        self.assertEqual(self.echoes, [], "no echo atom lands — an echo would kill the compacting cue")
        self.assertEqual(km._pending_ops.get(SID),
                         [("model", "opus"), ("send", "now do the thing", "human"), ("effort", "medium")],
                         "ONE queue, in park order — messages and slash commands interleaved as sent")

    def test_repeat_model_or_effort_replaces_in_place_messages_append(self):
        km._compacting_now = lambda sid: True
        km._set_model_or_park(self.be, SID, "opus")
        km._send_or_park(self.be, SID, "first", echo=None)
        km._set_model_or_park(self.be, SID, "sonnet")     # re-pick → replaces the parked model IN PLACE
        km._send_or_park(self.be, SID, "second", echo=None)
        self.assertEqual(km._pending_ops.get(SID),
                         [("model", "sonnet"), ("send", "first", None), ("send", "second", None)])

    def test_fast_toggle_parks_replaces_in_place_and_delivers_like_model_and_effort(self):
        # /fast is a slash command like /model and /effort, so it rides the SAME FIFO: parked while the
        # gate holds (compaction/open turn), a re-pick replaces the earlier parked toggle in place, and
        # _apply_pending_ops hands it to the backend once the session is quiet.
        km._compacting_now = lambda sid: True
        self.assertTrue(km._set_fast_or_park(self.be, SID, "on"))
        km._send_or_park(self.be, SID, "then this", echo=None)
        self.assertTrue(km._set_fast_or_park(self.be, SID, "off"))   # re-pick → in-place replace
        self.assertEqual(self.be.calls, [], "mid-compaction the backend is NOT touched")
        self.assertEqual(km._pending_ops.get(SID),
                         [("fast", "off"), ("send", "then this", None)])
        self.assertFalse(km._set_fast_or_park(self.be, SID, "sideways"), "only on|off are /fast arguments")
        km.Sessions.backend_for = lambda sid: self.be
        km._compacting_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("fast", "off"), ("send", "then this")],
                         "the toggle applies and delivery continues; the send ends the pass")
        km._compacting_now = lambda sid: False
        self.assertTrue(km._set_fast_or_park(self.be, SID, "on"), "quiet session → applies immediately")
        self.assertEqual(self.be.calls[-1], ("fast", "on"))

    def test_apply_delivers_sequentially_when_quiet_and_not_before(self):
        # SEQUENTIAL delivery (the user 2026-07-02, compact-mid-turn): settings ops apply and delivery
        # continues; a SEND ends the pass — its turn must finish before anything after it fires.
        km._pending_ops[SID] = [("model", "opus"), ("send", "go", "human"), ("effort", "high")]
        km.Sessions.backend_for = lambda sid: self.be
        km._compacting_now = lambda sid: True
        km._working_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "still compacting → still parked")
        self.assertIn(SID, km._pending_ops)
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: True
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "turn still open → still parked (waits for the turn END)")
        km._working_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("model", "opus"), ("send", "go")],
                         "model applies and delivery continues; the send ENDS the pass")
        self.assertEqual(km._pending_ops.get(SID), [("effort", "high")], "the effort waits for that turn")
        km._apply_pending_ops()                        # the send's turn ended (still quiet in this fixture)
        self.assertEqual(self.be.calls, [("model", "opus"), ("send", "go"), ("effort", "high")])
        self.assertEqual(self.echoes, [("go", "human")], "echo only where the send path echoed")
        self.assertNotIn(SID, km._pending_ops, "consumed — never re-delivered")

    def test_compact_clicked_mid_turn_parks_and_fires_at_turn_end(self):
        # the user 2026-07-02, who saw the compact icon blink with nothing happening while the session worked.
        # The click now parks a ("compact",) op — the queued /compact chip is the acknowledgement — and
        # fires the real /compact when the turn ends, marking compacting for every surface.
        km._pending_ops[SID] = [("compact",), ("send", "and then this", None)]
        km.Sessions.backend_for = lambda sid: self.be
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: True
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "turn still open → the /compact waits")
        km._working_now = lambda sid: False
        marked = []
        saved_mark = km._mark_compacting
        km._mark_compacting = lambda sid: marked.append(sid)
        try:
            km._apply_pending_ops()
        finally:
            km._mark_compacting = saved_mark
        self.assertEqual(self.be.calls, [("send", "/compact")], "the parked compact fires — and ONLY it")
        self.assertEqual(marked, [SID], "the compacting cue lights every surface at once")
        self.assertEqual(km._pending_ops.get(SID), [("send", "and then this", None)],
                         "the message waits for the compaction to finish (press order)")

    def test_an_open_turn_parks_everything_too(self):
        # the user 2026-07-02 ×2, who interrupted, picked a model, and sent a message — the model never
        # registered and the message vanished: input fired into a busy/tearing-down session races and
        # drops. The gate now parks EVERY drive op while a turn is open (the interrupt-settling window
        # keeps the turn open until the stop lands, so the whole scenario chains in press order).
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: True
        km._set_model_or_park(self.be, SID, "opus")
        km._send_or_park(self.be, SID, "now do it", echo="human")
        self.assertEqual(self.be.calls, [], "nothing fires into an open turn")
        self.assertEqual(self.echoes, [], "no orphan echo either")
        self.assertEqual(km._pending_ops.get(SID),
                         [("model", "opus"), ("send", "now do it", "human")], "press order, as chips")

    def test_a_nonempty_queue_chains_everything_behind_it(self):
        # strict press-order (the user 2026-07-02): once ANYTHING is parked, later ops park behind it even
        # if the session is not compacting — otherwise a send would race ahead via the backend's native queue.
        km._compacting_now = lambda sid: False
        km._pending_ops[SID] = [("compact",)]
        km._send_or_park(self.be, SID, "after the compact", echo="human")
        km._set_model_or_park(self.be, SID, "opus")
        self.assertEqual(self.be.calls, [], "nothing fires directly while a queue exists")
        self.assertEqual(km._pending_ops.get(SID),
                         [("compact",), ("send", "after the compact", "human"), ("model", "opus")])

    def test_dead_session_queue_is_dropped_not_retried(self):
        km._pending_ops[SID] = [("send", "into the void", None)]
        km._compacting_now = lambda sid: False

        def dead(sid):
            raise RuntimeError("no such session")
        km.Sessions.backend_for = dead
        km._apply_pending_ops()                         # must not raise
        self.assertNotIn(SID, km._pending_ops, "a dead session's queue is dropped, never retried forever")

    def test_the_pusher_cycle_delivers_the_parked_queue_not_the_producer(self):
        # 2026-09-03: delivery moved OFF the judge producer's tail — a pass can run for hours (one session's
        # closer sweep, alarm-killed turn after turn) and held every parked op hostage. It rides the pusher
        # cycle now, woken by the settle itself, and runs FIRST so the delivered op's echo rides the push.
        import inspect
        self.assertNotIn("_apply_pending_ops()", inspect.getsource(km._producer),
                         "the judge pass no longer gates delivery")
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_apply_pending_ops()", src, "the pusher cycle delivers the parked queue")
        self.assertLess(src.index("_apply_pending_ops()"), src.index("_push_all("), "…ahead of the push")


class _FakeForwardBackend:
    """An SDK-like backend: forwards_sends() True, and send() enqueues into an in-memory queue exposed by
    pending_queued (the SDK's _pending). The actual mid-turn forward / fold happens inside the SDK, not here
    — for the kernel gate what matters is that a working send is HANDED OVER (lands in send()/the queue),
    not parked in the kernel FIFO."""

    def __init__(self):
        self.calls = []
        self._q = []

    def forwards_sends(self):
        return True

    def send(self, sid, text):
        self.calls.append(("send", text))
        self._q.append(text)
        return True

    def pending_queued(self, sid):
        return list(self._q)

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def set_effort(self, sid, value):
        self.calls.append(("effort", value))
        return True


class SdkForwardsAndBatch(unittest.TestCase):
    """The user 2026-07-17: get typed messages in AS SOON AS POSSIBLE (no interrupt), and when a pile is
    queued, send them ALL AT ONCE — the SDK folds them into one turn, tmux merges them. A backend that
    forwards its own sends (forwards_sends) takes a composer send even MID-TURN, instead of the kernel
    parking it until the turn ends; slash-command drive ops still park in press order. Synthetic only."""

    def setUp(self):
        self.be = _FakeBackend()                       # tmux-like (no forwards_sends)
        self.fbe = _FakeForwardBackend()               # SDK-like
        self.echoes = []
        self._saved = (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
                       km._working_now)
        km._push_all = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": self.echoes.append((text, author))
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: False
        km._pending_ops.clear()

    def tearDown(self):
        (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
         km._working_now) = self._saved
        km._pending_ops.clear()

    def test_sdk_send_while_working_is_handed_over_not_parked(self):
        km._working_now = lambda sid: True             # a turn IS in flight
        km._send_or_park(self.fbe, SID, "mid-turn message", echo="human")
        self.assertEqual(self.fbe.calls, [("send", "mid-turn message")],
                         "the SDK takes the send mid-turn — the inputs() generator forwards it at the next boundary")
        self.assertNotIn(SID, km._pending_ops, "not parked in the kernel FIFO")

    def test_several_sdk_sends_while_working_all_forward_none_parked(self):
        km._working_now = lambda sid: True
        for t in ("one", "two", "three"):
            km._send_or_park(self.fbe, SID, t, echo="human")
        self.assertEqual(self.fbe.calls, [("send", "one"), ("send", "two"), ("send", "three")],
                         "all three reach the SDK queue → its inputs() folds them into one turn")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_send_after_a_live_model_pick_still_reaches_the_model_second(self):
        # press-order beats mid-turn forwarding (the user 2026-07-17, who asked to be careful with that
        # aspect) — and on a forwards_sends backend it is now kept by DELIVERING in order rather than by
        # deferring both. A model pick rides the SDK control channel, not the input stream, so an open turn
        # no longer parks it (see tests/test_model_live_midturn.py); the send that follows is handed over
        # next. What must never happen — the message reaching the model BEFORE the switch — still cannot.
        # The parked shape this test used to pin is still pinned wherever the pick DOES park: tmux, a
        # compaction, an existing queue, a limit hold (all in test_model_live_midturn.py).
        km._working_now = lambda sid: True
        km._set_model_or_park(self.fbe, SID, "opus")
        km._send_or_park(self.fbe, SID, "after the model", echo="human")
        self.assertEqual(self.fbe.calls, [("model", "opus"), ("send", "after the model")],
                         "model first, then the message — press order, both immediate")
        self.assertNotIn(SID, km._pending_ops, "nothing needed to park")

    def test_a_send_after_a_PARKED_drive_op_still_chains_behind_it(self):
        # the original 2026-07-17 shape, on the path where the pick still parks: a compaction. The send must
        # not forward past it.
        km._working_now = lambda sid: True
        km._compacting_now = lambda sid: True
        km._set_model_or_park(self.fbe, SID, "opus")
        km._send_or_park(self.fbe, SID, "after the model", echo="human")
        self.assertEqual(self.fbe.calls, [], "nothing fires: model parked, the send chained behind it")
        self.assertEqual(km._pending_ops.get(SID),
                         [("model", "opus"), ("send", "after the model", "human")], "press order held")

    def test_tmux_merges_a_run_of_queued_sends_into_one_message(self):
        km.Sessions.backend_for = lambda sid: self.be
        km._pending_ops[SID] = [("send", "alpha", None), ("send", "beta", None), ("send", "gamma", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "alpha\n\nbeta\n\ngamma")],
                         "tmux has no fold → the run merges into a single blank-line-separated message")
        self.assertNotIn(SID, km._pending_ops, "the whole run delivered at once")

    def test_sdk_delivers_a_run_as_separate_sends_to_fold(self):
        km.Sessions.backend_for = lambda sid: self.fbe
        km._pending_ops[SID] = [("send", "a", None), ("send", "b", None), ("send", "c", None)]
        km._apply_pending_ops()
        self.assertEqual(self.fbe.calls, [("send", "a"), ("send", "b"), ("send", "c")],
                         "the SDK enqueues each — its inputs() folds them into one turn, no merge")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_drive_op_then_a_run_applies_the_op_then_batches_the_sends(self):
        km.Sessions.backend_for = lambda sid: self.fbe
        km._pending_ops[SID] = [("model", "opus"), ("send", "a", None), ("send", "b", None)]
        km._apply_pending_ops()
        self.assertEqual(self.fbe.calls, [("model", "opus"), ("send", "a"), ("send", "b")],
                         "the model applies, delivery continues, and the leading send run batches")
        self.assertNotIn(SID, km._pending_ops)

    def test_compacting_still_parks_sdk_sends_then_batches_when_it_ends(self):
        # mid-compaction a send must PARK (protect the 'compacting' cue), even for the SDK — then the whole
        # parked run delivers together the instant compaction ends.
        km._compacting_now = lambda sid: True
        km._send_or_park(self.fbe, SID, "one", echo="human")
        km._send_or_park(self.fbe, SID, "two", echo="human")
        self.assertEqual(self.fbe.calls, [], "nothing fires mid-compaction — the cue would die")
        self.assertEqual(km._pending_ops.get(SID),
                         [("send", "one", "human"), ("send", "two", "human")], "parked in order")
        km._compacting_now = lambda sid: False
        km.Sessions.backend_for = lambda sid: self.fbe
        km._apply_pending_ops()
        self.assertEqual(self.fbe.calls, [("send", "one"), ("send", "two")],
                         "compaction over → the parked run delivers all at once (folds)")


class BackendQueuedNudgeGate(unittest.TestCase):
    """The nudge-suppression consults the backend queue now that composer sends live in the SDK _pending
    (the user 2026-07-17). _backend_queued must see them so a nudge never jumps the user's queued messages."""

    def setUp(self):
        self._saved = km.Sessions.backend_for

    def tearDown(self):
        km.Sessions.backend_for = self._saved

    def test_backend_queued_true_when_the_backend_holds_messages(self):
        be = _FakeForwardBackend()
        be._q = ["a queued message"]
        km.Sessions.backend_for = lambda sid: be
        self.assertTrue(km._backend_queued(SID), "a non-empty backend queue is queued intent")

    def test_backend_queued_false_when_empty(self):
        km.Sessions.backend_for = lambda sid: _FakeForwardBackend()
        self.assertFalse(km._backend_queued(SID), "empty backend queue → no queued intent")

    def test_backend_queued_survives_a_backend_error(self):
        def boom(sid):
            raise RuntimeError("no such session")
        km.Sessions.backend_for = boom
        self.assertFalse(km._backend_queued(SID), "a backend hiccup reads as 'nothing queued', never crashes the nudge check")

    def test_the_nudge_gate_consults_it(self):
        import inspect
        src = inspect.getsource(km._auto_nudge_session)
        self.assertIn("_backend_queued(sid)", src,
                      "the nudge-suppression gate checks the backend queue, not just the kernel FIFO")


class SendPathsPark(unittest.TestCase):
    """Every drive path routes through a park helper, so no path can slip a mid-compaction op."""

    def test_ws_drive_paths_use_the_parks(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn('_send_or_park(be, sid, str(msg["text"]), echo="human")', src,
                      "the composer send parks mid-compaction")
        self.assertIn("_send_or_park(be, sid, body,", src, "the follow-up/nudge send parks mid-compaction")
        self.assertIn("_send_or_park(be, sid, cmd)", src, "the timeline sendCommand parks mid-compaction")
        self.assertIn('_set_effort_or_park(be, sid, str(msg["value"]))', src,
                      "the setEffort drive op parks mid-compaction (the user 2026-07-02: it slipped through)")
        self.assertIn("_set_effort_or_park(be, sid, value)    # mid-compaction → parked as a queued command", src,
                      "the timeline's and the composer's /effort park mid-compaction (_route_meta_command)")


class QueuedBubble(unittest.TestCase):
    def test_build_session_renders_the_op_queue_in_park_order(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn("pending_ops = _pending_ops.get(sid) or []", src)
        self.assertIn("if queued or pending_ops:", src,
                      "the queued indicator shows even when a parked op is the only pending item")
        self.assertIn("for j, op in enumerate(pending_ops):", src,
                      "ONE loop, park order — rendering IS execution order")
        self.assertIn('{"md": _parked_md(op), "park": j, "cancelable": True}', src,
                      "parked ops are CANCELABLE (the user 2026-07-08): park index + shared body renderer")

    def test_drive_routes_park_cancels(self):
        import inspect
        src = inspect.getsource(km._drive)
        self.assertIn('t == "cancelQueued" and msg.get("park") is not None', src)
        self.assertIn('_cancel_parked(sid, int(msg["park"]), str(msg.get("md") or ""))', src)
        self.assertIn('_cancel_backend_queued(be, sid, int(msg["idx"]), str(msg.get("md") or ""))', src,
                      "the backend-queue cancel goes through the drift guard now")

    def test_drive_answers_every_cancel_with_an_authoritative_result_frame(self):
        # the user 2026-07-20: a ✕ whose target had already been handed to the CLI silently no-opped
        # while the client showed the message as deleted — and the CLI answered it anyway. EVERY cancel
        # arm replies with a cancelResult frame (ok + the 'too late' text on a miss) so the client
        # can toast and undo its optimistic composer restore. Three arms since 2026-08-30: park, idx,
        # and the optimistic md-only arm (a ✕ before any park/idx has round-tripped).
        import inspect
        src = inspect.getsource(km._drive)
        self.assertEqual(src.count('"type": "cancelResult"'), 3,
                         "one authoritative reply per cancel arm (park + idx + md-only)")
        self.assertIn('"ok": not err', src)
        self.assertIn('"text": err or ""', src)

    def test_the_backend_queue_bubble_cancelable_flag_rides_queue_recallable(self):
        # the ✕ renders only while a recall can still WIN (the user 2026-07-20): during a running
        # un-held turn the SDK forwards the send into the CLI within milliseconds, where no recall
        # exists — so the affordance itself must go, not just fail loudly after the fact.
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('cancelable = hasattr(_cbe, "unqueue") and _queue_recallable(_cbe, sid)', src)
        gsrc = inspect.getsource(km._queue_recallable)
        self.assertIn('getattr(be, "queue_recallable", None)', gsrc)
        self.assertIn("return True", gsrc, "fails toward offering the ✕ — the loud miss covers it")


class CancelParked(unittest.TestCase):
    """The queued bubble's X on a PARKED op (the user 2026-07-08): _cancel_parked removes exactly the op
    the user clicked — verified by the bubble's body (md), so a queue that shifted between the push and
    the click (ops applied, another cancel) re-locates by text; a GONE op returns the 'too late' text
    instead of a silent miss (the user 2026-07-20), never a wrong-op removal."""

    def setUp(self):
        km._pending_ops.clear()

    def tearDown(self):
        km._pending_ops.clear()

    def test_removes_the_indexed_op(self):
        km._pending_ops[SID] = [("model", "opus"), ("send", "now do the thing", "human")]
        self.assertIsNone(km._cancel_parked(SID, 0, "/model opus"), "a won cancel returns no error")
        self.assertEqual(km._pending_ops.get(SID), [("send", "now do the thing", "human")])

    def test_md_mismatch_relocates_by_body(self):
        # the head op fired while the click was in flight -> index 0 now holds a DIFFERENT op
        km._pending_ops[SID] = [("send", "first", "human"), ("send", "second", "human")]
        self.assertIsNone(km._cancel_parked(SID, 0, "second"))
        self.assertEqual(km._pending_ops.get(SID), [("send", "first", "human")],
                         "the clicked body wins over the stale index")

    def test_gone_op_returns_the_too_late_text(self):
        km._pending_ops[SID] = [("send", "still here", "human")]
        err = km._cancel_parked(SID, 0, "/compact")
        self.assertEqual(err, "too late to cancel /compact — the session already has it",
                         "an already-applied op fails LOUDLY (the user 2026-07-20), never silently")
        self.assertEqual(km._pending_ops.get(SID), [("send", "still here", "human")],
                         "…and cancels nothing else")

    def test_last_op_removed_drops_the_key(self):
        km._pending_ops[SID] = [("compact",)]
        self.assertIsNone(km._cancel_parked(SID, 0, "/compact"))
        self.assertNotIn(SID, km._pending_ops)

    def test_parked_md_mirrors_the_bubble_rendering(self):
        self.assertEqual(km._parked_md(("model", "opus")), "/model opus")
        self.assertEqual(km._parked_md(("effort", "high")), "/effort high")
        self.assertEqual(km._parked_md(("compact",)), "/compact")
        self.assertEqual(km._parked_md(("send", "plain text", "human")), "plain text")


class _FakeQueueBackend:
    """A backend that owns its queue (exposes unqueue), for the drift-guard tests. Mirrors the real
    SdkBackend.unqueue contract: `expect` re-verified (and re-located) at pop time, None on a miss."""

    def __init__(self, pending):
        self._p = list(pending)
        self.unqueued = []

    def pending_queued(self, sid):
        return list(self._p)

    def unqueue(self, sid, idx, expect=None):
        self.unqueued.append(idx)
        if expect is not None and not (0 <= idx < len(self._p) and self._p[idx] == expect):
            idx = next((i for i, q in enumerate(self._p) if q == expect), -1)
        return self._p.pop(idx) if 0 <= idx < len(self._p) else None


class CancelBackendQueued(unittest.TestCase):
    """The X on a backend-queue message: _cancel_backend_queued re-verifies the index against the
    bubble's body before unqueueing — the input generator consuming the head between push and click
    must never make the X cancel the WRONG message. A MISS (the message already forwarded into the
    CLI, where no recall exists) returns the 'too late' text for the caller to toast — the old silent
    no-op read as a successful delete while the CLI answered the message anyway (the user 2026-07-20)."""

    def test_exact_match_unqueues_the_index(self):
        be = _FakeQueueBackend(["alpha", "beta"])
        self.assertIsNone(km._cancel_backend_queued(be, SID, 1, "beta"), "a won cancel returns no error")
        self.assertEqual(be.unqueued, [1])
        self.assertEqual(be._p, ["alpha"])

    def test_drifted_index_relocates_by_body(self):
        # the push showed [consumed, alpha, beta]; by click time the head is gone -> idx 2 is stale
        be = _FakeQueueBackend(["alpha", "beta"])
        self.assertIsNone(km._cancel_backend_queued(be, SID, 2, "beta"))
        self.assertEqual(be.unqueued, [1], "re-located by body, not the stale index")

    def test_gone_message_returns_the_too_late_text(self):
        be = _FakeQueueBackend(["alpha"])
        err = km._cancel_backend_queued(be, SID, 0, "beta")
        self.assertEqual(err, "too late to cancel — the message already reached the session, "
                              "and will be answered in the current turn")
        self.assertEqual(be.unqueued, [], "already forwarded -> nothing recalled, nothing else touched")

    def test_no_md_keeps_raw_index_backcompat(self):
        be = _FakeQueueBackend(["alpha", "beta"])
        self.assertIsNone(km._cancel_backend_queued(be, SID, 0, ""))
        self.assertEqual(be.unqueued, [0])

    def test_the_pop_itself_verifies_the_text_under_the_backend_lock(self):
        # the TOCTOU the old two-step left open: the snapshot located 'beta' at idx 1, the generator
        # consumed the head before the pop, and a raw-index pop would have canceled the WRONG message.
        # The expect re-verify inside unqueue re-locates (or misses loudly) instead.
        be = _FakeQueueBackend(["alpha", "beta"])
        snap_idx = 1                                    # located from a snapshot listing [alpha, beta]
        be._p = ["beta"]                                # the generator consumed 'alpha' mid-click
        self.assertEqual(be.unqueue(SID, snap_idx, "beta"), "beta", "re-located, not wrong-popped")
        self.assertEqual(be._p, [])

    def test_miss_text_wording_splits_message_from_command(self):
        self.assertIn("will be answered in the current turn", km._cancel_miss_text("do the thing"))
        self.assertEqual(km._cancel_miss_text("/model opus"),
                         "too late to cancel /model — the session already has it")


class SlashCommandParksWhileTurnOpen(unittest.TestCase):
    """A typed SLASH COMMAND parks whenever a turn is open — even on a forwards_sends (SDK) backend — and
    fires ALONE at turn end (the user 2026-08-13): forwarded mid-turn, "/autocompact auto" reached the
    model as plain text, the model politely replied, and the setting never changed. Plain messages keep
    the forward-now path; slash-SHAPED paths ("/tmp/x") are not commands and keep it too."""

    def setUp(self):
        self.be = _FakeBackend()
        self.be.forwards_sends = lambda: True          # an SDK-like backend: takes sends mid-turn
        self.echoes = []
        self._saved = (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
                       km._working_now)
        km._push_all = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": self.echoes.append((text, author))
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: True             # a turn is OPEN throughout, unless a test says otherwise
        km._pending_ops.clear()

    def tearDown(self):
        (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
         km._working_now) = self._saved
        km._pending_ops.clear()

    def test_shape_matcher_commands_yes_paths_and_prose_no(self):
        for t in ("/autocompact auto", "/compact", "/model opus", "/mcp__srv__tool go", "/loop 5m /foo"):
            self.assertTrue(km._is_slash_command(t), t)
        for t in ("/tmp/x is broken", "/Users/nobody/file.txt", "hello /compact", "", "  ", "/"):
            self.assertFalse(km._is_slash_command(t), t)

    def test_plain_text_still_forwards_mid_turn_but_a_command_parks(self):
        km._send_or_park(self.be, SID, "keep going, and also check the logs", echo="human")
        self.assertEqual(self.be.calls, [("send", "keep going, and also check the logs")],
                         "plain text keeps the forward-now path — get messages in ASAP")
        km._send_or_park(self.be, SID, "/autocompact auto", echo="human")
        self.assertEqual(self.be.calls[1:], [], "the command did NOT go into the running turn")
        self.assertEqual(km._pending_ops.get(SID), [("command", "/autocompact auto", "human")])
        self.assertEqual(self.echoes, [("keep going, and also check the logs", "human")],
                         "the parked command has not echoed yet — it renders as a queued bubble instead")

    def test_parked_command_fires_alone_at_turn_end_with_its_echo_and_ends_the_pass(self):
        km._pending_ops[SID] = [("command", "/autocompact auto", "human"), ("send", "then this", None)]
        km.Sessions.backend_for = lambda sid: self.be
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "turn still open → still parked")
        km._working_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "/autocompact auto")],
                         "the command fires ALONE — never folded into a send batch")
        self.assertEqual(self.echoes, [("/autocompact auto", "human")], "echo stamps at fire time")
        self.assertEqual(km._pending_ops.get(SID), [("send", "then this", None)],
                         "the pass ends at the command — its turn must finish first")

    def test_typed_compact_marks_compacting_like_the_buttons_op(self):
        marked = []
        _saved_mark = km._mark_compacting
        km._mark_compacting = lambda sid: marked.append(sid)
        try:
            km._pending_ops[SID] = [("command", "/compact", None)]
            km.Sessions.backend_for = lambda sid: self.be
            km._working_now = lambda sid: False
            km._apply_pending_ops()
            self.assertEqual(self.be.calls, [("send", "/compact")])
            self.assertEqual(marked, [SID], "a typed /compact gets the same instant compacting cue")
        finally:
            km._mark_compacting = _saved_mark

    def test_parked_md_renders_the_command_itself(self):
        self.assertEqual(km._parked_md(("command", "/autocompact auto", "human")), "/autocompact auto")

    def test_idle_command_goes_straight_through(self):
        km._working_now = lambda sid: False
        km._send_or_park(self.be, SID, "/autocompact auto", echo="human")
        self.assertEqual(self.be.calls, [("send", "/autocompact auto")],
                         "idle → a fresh top-level prompt already, nothing to park")
        self.assertEqual(self.echoes, [("/autocompact auto", "human")])
        self.assertNotIn(SID, km._pending_ops)


if __name__ == "__main__":
    unittest.main()
