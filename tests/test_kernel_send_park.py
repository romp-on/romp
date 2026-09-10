#!/usr/bin/env python3
"""ANY drive op sent while a session compacts is PARKED in ONE FIFO queue (the user 2026-07-02): messages,
/model AND /effort. The chat renders the queue as bubbles in park order, and _apply_pending_ops delivers
in exactly that order the moment compaction ends (the rendering IS the execution order — the user hit a
parked message rendering BEFORE the model change parked ahead of it). A repeated model/effort pick
replaces its earlier parked op in place. Same event-corroborated _compacting_now gate as ever; a parked
send stamps its optimistic echo only when it actually fires (an early echo killed the compacting cue).
SYNTHETIC fixtures only."""
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_sendpark", os.path.join(BIN, "romp-kernel"))

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
THEIRS = "99999999-8888-7777-6666-555555555555"   # a session another machine's kernel owns


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
        self.assertTrue(km._set_fast_or_park(self.be, SID, "on")[0])   # (took, parked): took
        km._send_or_park(self.be, SID, "then this", echo=None)
        self.assertTrue(km._set_fast_or_park(self.be, SID, "off")[0])   # re-pick → in-place replace
        self.assertEqual(self.be.calls, [], "mid-compaction the backend is NOT touched")
        self.assertEqual(km._pending_ops.get(SID),
                         [("fast", "off"), ("send", "then this", None)])
        self.assertEqual(km._set_fast_or_park(self.be, SID, "sideways"), (False, False), "only on|off are /fast arguments")
        km.Sessions.backend_for = lambda sid: self.be
        km._compacting_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("fast", "off"), ("send", "then this")],
                         "the toggle applies and delivery continues; the send ends the pass")
        km._compacting_now = lambda sid: False
        self.assertEqual(km._set_fast_or_park(self.be, SID, "on"), (True, False), "quiet session → applies immediately, not parked")
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
    """An SDK-like backend for sends: forwards_sends() True, and send() enqueues into an in-memory queue
    exposed by pending_queued (the SDK's _pending). The actual mid-turn forward / fold happens inside the SDK,
    not here — for the kernel gate what matters is that a working send is HANDED OVER (lands in send()/the
    queue), not parked in the kernel FIFO. It also declares model_switches_live, so the live-pick ordering
    can be pinned; the real SdkBackend says False for now (see its docstring)."""

    def __init__(self):
        self.calls = []
        self._q = []

    def forwards_sends(self):
        return True

    def model_switches_live(self):
        return True     # a backend that CAN switch mid-turn — the shape the SDK's control channel would take once the
                        # CLI persists a mid-turn switch correctly; the real SdkBackend declares False for now (#923)

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
    parking it until the turn ends; slash-command drive ops still park in press order — except a model
    pick on a backend that declares model_switches_live, which fires and keeps order by going first
    (#923; no shipped backend declares it yet). Synthetic only."""

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
        # aspect) — and on a backend that can apply a model change mid-turn (model_switches_live) it is
        # kept by DELIVERING in order rather than by deferring both: the pick's control request goes over
        # first, the send that follows is handed over next (see tests/test_model_live_midturn.py). What must
        # never happen — the message reaching the model BEFORE the switch — still cannot. The parked shape
        # this test used to pin is still pinned wherever the pick DOES park: tmux, Codex, the real SDK for
        # now, a compaction, an existing queue, a limit hold (all in test_model_live_midturn.py).
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
        self.assertIn('_send_or_park(be, sid, str(msg["text"]), echo="human", qid=_client_qid(msg, sid, be))', src,
                      "the composer send parks mid-compaction")
        self.assertIn("_send_or_park(be, sid, body,", src, "the follow-up/nudge send parks mid-compaction")
        self.assertIn("_send_or_park(be, sid, cmd)", src, "the timeline sendCommand parks mid-compaction")
        self.assertIn('_set_effort_or_park(be, sid, str(msg["value"]))', src,
                      "the setEffort drive op parks mid-compaction (the user 2026-07-02: it slipped through)")
        self.assertIn("_set_effort_or_park(be, sid, value)    # mid-compaction → parked as a queued command", src,
                      "the timeline's and the composer's /effort park mid-compaction (_route_meta_command)")


class _EditableQueueBackend:
    """A backend that owns its queue and can re-word an entry (SdkBackend.edit_queued's contract): `expect`
    re-verified at swap time, the OLD text returned, None on a miss that touches nothing. Shared by the
    _edit_backend_queued tests and the executing _drive harness below (review find, 2026-09-08)."""

    def __init__(self, pending=None):
        self.q = list(pending or [])
        self.edits = []                       # every edit_queued call, so a refusal can prove it never reached the backend

    def pending_queued(self, sid):
        return list(self.q)

    def edit_queued(self, sid, idx, text, expect=None):
        self.edits.append((idx, text, expect))
        if not (0 <= idx < len(self.q)) or self.q[idx] != expect:
            return None
        old, self.q[idx] = self.q[idx], text
        return old


class QueuedEdit(unittest.TestCase):
    """The queued bubble's ✎ (the user 2026-09-08): a message that has not reached the session is still the
    user's to change. Same slot, same follow-up context, only the words; refused with the ✕'s honesty when
    the entry is gone or is with the backend this instant. SYNTHETIC fixtures only."""

    def setUp(self):
        km._pending_ops.clear()
        km._inflight_ops.clear()

    def tearDown(self):
        km._pending_ops.clear()
        km._inflight_ops.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass

    def test_edit_parked_replaces_the_send_in_place(self):
        km._pending_ops[SID] = [("model", "opus"), ("send", "draft one", "human"), ("send", "another", "human")]
        self.assertIsNone(km._edit_parked(SID, 1, "draft one", "draft two"))
        self.assertEqual(km._pending_ops[SID], [("model", "opus"), ("send", "draft two", "human"), ("send", "another", "human")],
                         "same slot, same echo author, new words")
        self.assertEqual(km._parked_md(km._pending_ops[SID][1]), "draft two", "the bubble shows the new body")

    def test_edit_parked_relocates_by_body_and_refuses_the_gone_and_the_in_flight(self):
        km._pending_ops[SID] = [("send", "a", "human"), ("send", "b", "human")]
        self.assertIsNone(km._edit_parked(SID, 0, "b", "b2"), "a stale index re-locates by body")
        self.assertEqual([op[1] for op in km._pending_ops[SID]], ["a", "b2"])
        self.assertEqual(km._edit_parked(SID, 5, "gone", "x"), km._edit_miss_text("gone"))
        km._inflight_ops[SID] = km._pending_ops[SID][0]
        self.assertEqual(km._edit_parked(SID, 0, "a", "a2"), km._edit_miss_text("a"),
                         "the head the backend holds this instant is too late, never a wrong-op rewrite")
        self.assertEqual(km._pending_ops[SID][0][1], "a")

    def test_edit_parked_refuses_a_command_chip_and_an_empty_body(self):
        km._pending_ops[SID] = [("compact",), ("command", "/model opus", "human"), ("send", "words", "human")]
        self.assertIn("only a queued message can be edited", km._edit_parked(SID, 0, "/compact", "x"))
        self.assertIn("only a queued message can be edited", km._edit_parked(SID, 1, "/model opus", "x"))
        self.assertIn("nothing to send", km._edit_parked(SID, 2, "words", "   "))
        self.assertEqual(km._pending_ops[SID][2][1], "words", "a refusal changes nothing")

    def test_a_follow_up_keeps_its_goal_quote_and_markers(self):
        fu = ("> Ship the notes API\n> its goal context\n\nfirst words\n\n"
              "<!-- romp-note: the HTML comments below are part of an external tracking system --><!-- romp-goal-id: g7 -->")
        new = km._replace_followup_body(fu, "second words")
        goal, body, is_fu, ctx = km._split_followup(new)
        self.assertEqual((goal, body, is_fu), ("Ship the notes API", "second words", True))
        self.assertEqual(ctx, "Ship the notes API\nits goal context")
        self.assertIn("<!-- romp-goal-id: g7 -->", new, "the judge still files the edited follow-up under its goal")
        self.assertTrue(new.startswith("> Ship the notes API\n> its goal context\n\nsecond words"))
        self.assertEqual(km._replace_followup_body("plain", "edited"), "edited", "a plain send IS its body")
        km._pending_ops[SID] = [("send", fu, "human")]
        self.assertIsNone(km._edit_parked(SID, 0, "first words", "second words"), "the ✎ hands the BODY the bubble showed; the kernel keeps the wrapper")
        self.assertEqual(km._split_followup(km._pending_ops[SID][0][1])[1], "second words")

    def test_edit_backend_queued_uses_the_drift_guard_and_keeps_a_followups_wrapper(self):
        be = _EditableQueueBackend(["alpha", "> goal\n\nbody\n\n<!-- romp-goal-id: g1 -->"])
        self.assertIsNone(km._edit_backend_queued(be, SID, 5, "body", "new body"), "a stale index re-locates by body")
        self.assertEqual(km._split_followup(be.q[1])[1], "new body")
        self.assertIn("<!-- romp-goal-id: g1 -->", be.q[1])
        self.assertEqual(km._edit_backend_queued(be, SID, 0, "gone", "x"), km._edit_miss_text("gone"))
        self.assertEqual(be.q[0], "alpha", "a miss rewrites nothing")

    def test_an_edit_cannot_turn_a_message_into_a_slash_command(self):
        # review find (2026-09-08): both arms replaced the body without _is_slash_command, so "/model opus" typed
        # into the edit stayed a ("send", ...) op and reached the model as TEXT, skipping the fire-alone parking
        # and the kernel-side setters every typed command gets. Both arms refuse, and nothing changes.
        km._pending_ops[SID] = [("send", "words", "human")]
        self.assertIn("cannot become a command", km._edit_parked(SID, 0, "words", "/model opus"))
        self.assertEqual(km._pending_ops[SID], [("send", "words", "human")], "a refusal changes nothing")
        be = _EditableQueueBackend(["alpha"])
        self.assertIn("cannot become a command", km._edit_backend_queued(be, SID, 0, "alpha", "/compact"))
        self.assertEqual((be.q, be.edits), (["alpha"], []), "the refusal never reaches the backend")
        # a PATH is not a command (_is_slash_command's own rule): "/tmp/x is broken" still edits
        self.assertIsNone(km._edit_parked(SID, 0, "words", "/tmp/x is broken"))
        self.assertEqual(km._pending_ops[SID][0][1], "/tmp/x is broken")

    def test_an_edited_continue_lands_as_the_users_words_not_the_canned_gesture(self):
        # review find (2026-09-08): the Continue button's body carries <!-- romp-canned: continue -->, which
        # describes the canned WORDS, and _replace_followup_body kept every trailing comment as wrapper, so the
        # typed replacement went out still marked canned and the chat drew it as the Continue gesture row.
        # Only romp's WRAPPER markers (note, injected, auto, goal-id) survive an edit.
        fu = km._followup_body(SID + ":g7", "Ship the notes API", km.CONTINUE_TEXT + "\n\n<!-- romp-canned: continue -->")
        self.assertIn("<!-- romp-canned: continue -->", fu, "the fixture is the button's real composition")
        new = km._replace_followup_body(fu, "also add the tests first")
        self.assertNotIn("romp-canned", new)
        self.assertIn("<!-- romp-goal-id: " + SID + ":g7 -->", new, "the judge still files it under its goal")
        self.assertIn("<!-- romp-note:", new)
        self.assertEqual(km._split_followup(new)[1], "also add the tests first")
        self.assertTrue(new.startswith("> Ship the notes API\n\nalso add the tests first\n\n<!-- romp-note:"), new)

    def test_a_comment_inside_the_old_body_is_not_part_of_the_marker_tail(self):
        # review find (2026-09-08): the tail regex ran with re.S and matched ANY comment, so a body holding an
        # inline <!-- x --> anchored it there and the old words after the comment came back behind the NEW
        # body. The docstring's inverse holds: _split_followup(_replace_followup_body(t, b))[1] == b.strip().
        fu = "> goal\n\nsee <!-- x --> then more\n\n<!-- romp-goal-id: g -->"
        self.assertEqual(km._replace_followup_body(fu, "new"), "> goal\n\nnew\n\n<!-- romp-goal-id: g -->")
        fu2 = ("> Ship the notes API\n\nsee <!-- a --> then words\n\n"
               "<!-- romp-note: the HTML comments below are part of an external tracking system --><!-- romp-goal-id: g7 -->")
        new2 = km._replace_followup_body(fu2, "second words")
        self.assertEqual(km._split_followup(new2)[1], "second words")
        self.assertTrue(new2.endswith("<!-- romp-note: the HTML comments below are part of an external tracking system --><!-- romp-goal-id: g7 -->"))
        self.assertNotIn("then words", new2)

    def test_drive_routes_the_three_edit_arms_and_answers_editResult(self):
        import inspect
        src = inspect.getsource(km._drive)
        self.assertIn('t == "editQueued" and msg.get("park") is not None', src)
        self.assertIn('_edit_parked(sid, int(msg["park"]), str(msg.get("md") or ""), str(msg.get("text") or ""))', src)
        self.assertIn('t == "editQueued" and msg.get("idx") is not None and hasattr(be, "edit_queued")', src)
        self.assertIn('_edit_backend_queued(be, sid, int(msg["idx"]), str(msg.get("md") or ""), str(msg.get("text") or ""))', src)
        self.assertIn('elif t == "editQueued" and msg.get("md"):', src,
                      "the optimistic stage: locate by body, the FIFO first, then the backend queue")
        self.assertEqual(src.count("_edit_frame("), 3, "every edit arm answers with an authoritative frame, one shape for the three (T306)")
        self.assertEqual(src.count('"type": "editResult"'), 1, "and the holdQueued arm answers with its own (T306)")
        ksrc = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"apiRetry", "editQueued"', ksrc, "the op routes to the owning kernel across linked machines (ID_OPS)")


class QueuedEditDrive(unittest.TestCase):
    """editQueued driven through _drive with a capturing client (tests/test_drive_foreign_sid.py's harness): the
    three arms EXECUTE, and the editResult frame the client keys its restore on (id + md, ok, text) is asserted,
    not the handler's source text (review find, 2026-09-08). SYNTHETIC sids only."""

    def setUp(self):
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s))}
        self.be = _EditableQueueBackend()
        self._saved = (km._name_of, km._sdk, km.Sessions.backend_for, km._push_soon)
        km._name_of = lambda sid: "web" if sid == SID else None
        km._sdk = lambda: None
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._push_soon = lambda *a, **k: None
        km._pending_ops.clear()
        km._inflight_ops.clear()

    def tearDown(self):
        km._name_of, km._sdk, backend_for, km._push_soon = self._saved
        km.Sessions.backend_for = staticmethod(backend_for)
        km._pending_ops.clear()
        km._inflight_ops.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass

    def _edit(self, **fields):
        msg = {"type": "editQueued", "id": SID}
        msg.update(fields)
        self.assertTrue(km._drive(msg, self.client), "a drive op is consumed")
        self.assertEqual(len(self.sent), 1, "exactly one answer frame")
        return self.sent[0]

    def test_the_park_arm_replaces_the_fifo_send_and_answers_ok(self):
        km._pending_ops[SID] = [("send", "a", "human")]
        self.assertEqual(self._edit(park=0, md="a", text="b"),
                         {"type": "editResult", "ok": True, "id": SID, "md": "a", "text": ""})
        self.assertEqual(km._pending_ops[SID], [("send", "b", "human")])

    def test_the_park_arm_answers_ok_false_with_the_reason_when_the_chip_is_not_a_message(self):
        km._pending_ops[SID] = [("compact",)]
        frame = self._edit(park=0, md="/compact", text="x")
        self.assertEqual((frame["ok"], frame["md"]), (False, "/compact"))
        self.assertIn("only a queued message can be edited", frame["text"])
        self.assertEqual(km._pending_ops[SID], [("compact",)])

    def test_the_idx_arm_rewords_the_backend_queue_under_its_drift_guard(self):
        self.be.q = ["alpha", "beta"]
        self.assertEqual(self._edit(idx=1, md="beta", text="beta 2"),
                         {"type": "editResult", "ok": True, "id": SID, "md": "beta", "text": ""})
        self.assertEqual(self.be.q, ["alpha", "beta 2"])
        self.assertEqual(self.be.edits, [(1, "beta 2", "beta")], "the swap carried the exact old text")

    def test_the_optimistic_arm_tries_the_fifo_then_the_backend_queue(self):
        km._pending_ops[SID] = [("send", "p", "human")]
        self.assertTrue(self._edit(md="p", text="p 2")["ok"], "the FIFO holds it")
        self.assertEqual((km._pending_ops[SID][0][1], self.be.edits), ("p 2", []))
        self.sent.clear()
        self.be.q = ["alpha"]
        frame = self._edit(md="alpha", text="alpha 2")          # the FIFO misses, the backend holds it
        self.assertEqual(frame, {"type": "editResult", "ok": True, "id": SID, "md": "alpha", "text": ""},
                         "the second locate WINS and the frame says so")
        self.assertEqual(self.be.q, ["alpha 2"])

    def test_the_optimistic_arm_misses_everywhere_with_the_too_late_text(self):
        frame = self._edit(md="gone", text="x")
        self.assertEqual((frame["ok"], frame["md"], frame["text"]), (False, "gone", km._edit_miss_text("gone")))

    def test_a_slash_command_is_refused_at_every_arm(self):
        km._pending_ops[SID] = [("send", "a", "human")]
        self.be.q = ["alpha"]
        for fields in ({"park": 0, "md": "a"}, {"idx": 0, "md": "alpha"}, {"md": "a"}):
            self.sent.clear()
            frame = self._edit(text="/model opus", **fields)
            self.assertFalse(frame["ok"], fields)
            self.assertIn("cannot become a command", frame["text"], fields)
        self.assertEqual((km._pending_ops[SID][0][1], self.be.q, self.be.edits), ("a", ["alpha"], []))

    def test_a_foreign_sid_is_refused_and_nothing_is_edited(self):
        km._pending_ops[SID] = [("send", "a", "human")]
        self.be.q = ["alpha"]
        self.assertTrue(km._drive({"type": "editQueued", "id": THEIRS, "park": 0, "md": "a", "text": "b"}, self.client))
        self.assertEqual(self.sent[0]["type"], "err", "refused loudly, in the pane that fired it")
        self.assertEqual(self.sent[0]["copy"], "b", "the typed words ride back")
        self.assertEqual((km._pending_ops[SID][0][1], self.be.q), ("a", ["alpha"]))


class QueuedBubble(unittest.TestCase):
    def test_build_session_renders_the_op_queue_in_park_order(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn("pending_ops = _pending_ops.get(sid) or []", src)
        self.assertIn("if queued or pending_ops:", src,
                      "the queued indicator shows even when a parked op is the only pending item")
        self.assertIn("for j, op in enumerate(pending_ops):", src,
                      "ONE loop, park order — rendering IS execution order")
        self.assertIn('{"md": _parked_md(op), "park": j, "cancelable": True, **(_queued_romp_flags(op[1]) if op[0] == "send" else {})}', src,
                      "parked ops are CANCELABLE (the user 2026-07-08): park index + shared body renderer")

    def test_drive_routes_park_cancels(self):
        import inspect
        src = inspect.getsource(km._drive)
        self.assertIn('t == "cancelQueued" and msg.get("park") is not None', src)
        self.assertIn('_cancel_parked(sid, int(msg["park"]), str(msg.get("md") or ""), qid=_wire_qid(msg))', src)
        self.assertIn('_cancel_backend_queued(be, sid, int(msg["idx"]), str(msg.get("md") or ""), qid=_wire_qid(msg))', src,
                      "the backend-queue cancel goes through the drift guard now")

    def test_a_body_only_cancel_that_finds_nothing_is_logged(self):
        # T244 (the user 2026-09-07): a ✕ on a "sending…" bubble left no evidence of which way the cancel went.
        # The body-only arm now logs a miss (sid only — the body is the user's text) so the next report carries it.
        src = open(os.path.join(BIN, "romp-kernel")).read()
        arm = src.split('elif t == "cancelQueued" and msg.get("md"):')[1].split("\n    elif ")[0]
        self.assertIn('sys.stderr.write("queued-cancel miss: %s (body-only)\\n" % sid)', arm)

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


class _FakeTodoQueueBackend(_FakeForwardBackend):
    """An SDK-like backend whose queue entries can carry the id of the user todo a message ANSWERS
    (queue_carries_todos): send() records the id it was handed, the way SdkBackend.send rides it on
    the queue entry."""

    queue_carries_todos = True

    def send(self, sid, text, user_todo=None):
        self.calls.append(("send", text, user_todo))
        self._q.append(text)
        return True


class SendReturnShape(unittest.TestCase):
    """_send_or_park reports WHICH ARM it took — "parked" when the text joined the FIFO, else the
    backend send's own result — so a caller whose side effect must key on DELIVERY can tell parked,
    sent and refused apart, and a route compares against "parked" rather than reading truthiness (a
    completed send is truthy too). A plain send is byte-for-byte what it was: a three-slot op on
    disk and the two-argument backend send; only a send that answers a user todo (user_todo) grows to
    five slots (the press id's fourth, None when none rode, then the answered ask), and only a backend
    that can carry the id is handed it (_backend_send)."""

    def setUp(self):
        self.be = _FakeBackend()
        self._saved = (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
                       km._working_now)
        km._push_all = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": None
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: False
        km._pending_ops.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass

    def tearDown(self):
        (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
         km._working_now) = self._saved
        km._pending_ops.clear()

    def test_parked_reports_parked_and_a_delivery_reports_the_backends_result(self):
        km._compacting_now = lambda sid: True
        self.assertEqual(km._send_or_park(self.be, SID, "hello", echo="human"), "parked")
        self.assertEqual(self.be.calls, [], "parked: the backend was not touched")
        km._pending_ops.clear()
        km._compacting_now = lambda sid: False
        self.assertIs(km._send_or_park(self.be, SID, "hello", echo="human"), True,
                      "delivered: the backend's own truthy result comes back, not a park verdict")
        self.be.send = lambda sid, text: "a-delivery-handle"
        self.assertEqual(km._send_or_park(self.be, SID, "hello"), "a-delivery-handle",
                         "a richer truthy result rides through untouched")
        self.be.send = lambda sid, text: False
        self.assertIs(km._send_or_park(self.be, SID, "hello"), False,
                      "a refused send comes back falsy — and is not 'parked' either")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_plain_op_keeps_three_slots_on_disk_and_an_answer_adds_the_id(self):
        km._compacting_now = lambda sid: True
        km._send_or_park(self.be, SID, "hello", echo="human")
        self.assertEqual(km._pending_ops[SID], [("send", "hello", "human")])
        on_disk = json.loads(km._PENDING_OPS_FILE.read_text())
        self.assertEqual(on_disk, {SID: [["send", "hello", "human"]]},
                         "a plain park's disk mirror is the pre-todo three-slot list, byte for byte")
        self.assertEqual(km._send_or_park(self.be, SID, "Re: the port — 8443.", echo="human",
                                          user_todo="ut-9f2c1a34"), "parked")
        self.assertEqual(km._pending_ops[SID][1], ("send", "Re: the port — 8443.", "human", None, "ut-9f2c1a34"),
                         "an answer rides its todo id as the op's fifth slot, after the press id's empty fourth")
        self.assertEqual(km._load_pending_ops()[SID][1], ("send", "Re: the port — 8443.", "human", None, "ut-9f2c1a34"),
                         "…and the id survives the disk round trip")
        self.assertEqual(km._op_todo(km._pending_ops[SID][1]), "ut-9f2c1a34")
        self.assertIsNone(km._op_qid(km._pending_ops[SID][1]), "no client id rode: the fourth slot reads as none")
        self.assertEqual(km._parked_md(km._pending_ops[SID][1]), "Re: the port — 8443.",
                         "the queued bubble renders the text exactly as a three-slot op's")

    def test_backend_send_hands_the_id_only_to_a_backend_that_can_use_it(self):
        plain = _FakeBackend()
        self.assertTrue(km._backend_send(plain, SID, "hello", user_todo="ut-9f2c1a34"))
        self.assertEqual(plain.calls, [("send", "hello")],
                         "no capability flag → the plain two-argument send, the id stays with the kernel")
        carrying = _FakeTodoQueueBackend()
        self.assertTrue(km._backend_send(carrying, SID, "hello", user_todo="ut-9f2c1a34"))
        self.assertEqual(carrying.calls, [("send", "hello", "ut-9f2c1a34")], "queue_carries_todos → the id rides")
        carrying.calls.clear()
        self.assertTrue(km._backend_send(carrying, SID, "hello"))
        self.assertEqual(carrying.calls, [("send", "hello", None)],
                         "no id → the same call shape it always got")

        class _Refusing:
            send_reports_refusal = True

            def __init__(self):
                self.calls = []

            def send(self, sid, text, user_todo=None):
                self.calls.append((text, user_todo))
                return "nonce-1"

        refusing = _Refusing()
        self.assertEqual(km._backend_send(refusing, SID, "hello", user_todo="ut-9f2c1a34"), "nonce-1")
        self.assertEqual(refusing.calls, [("hello", "ut-9f2c1a34")],
                         "send_reports_refusal → the id rides too, and the backend's handle comes back")

    def test_an_immediate_send_hands_the_id_to_the_backend_entry(self):
        be = _FakeTodoQueueBackend()
        self.assertIs(km._send_or_park(be, SID, "Re: the port — 8443.", user_todo="ut-9f2c1a34"), True)
        self.assertEqual(be.calls, [("send", "Re: the port — 8443.", "ut-9f2c1a34")])
        self.assertIs(km._send_or_park(be, SID, "plain words"), True)
        self.assertEqual(be.calls[-1], ("send", "plain words", None))

    def test_the_drain_hands_a_parked_answers_id_to_the_backend(self):
        be = _FakeTodoQueueBackend()
        km._deliver_send_batch(be, SID, [("send", "Re: the port — 8443.", None, None, "ut-9f2c1a34"),
                                         ("send", "plain words", None)])
        self.assertEqual(be.calls, [("send", "Re: the port — 8443.", "ut-9f2c1a34"), ("send", "plain words", None)],
                         "a drained answer's queue entry carries the id exactly like an immediate send's")

    def test_a_non_forwarding_backend_merges_a_run_carrying_an_id_as_before(self):
        km._deliver_send_batch(self.be, SID, [("send", "alpha", None, None, "ut-9f2c1a34"), ("send", "beta", None)])
        self.assertEqual(self.be.calls, [("send", "alpha\n\nbeta")],
                         "tmux-shaped: the fifth slot changes nothing about the merged paste")

    def test_a_queue_filled_during_the_gates_parks_the_answer_behind_it(self):
        # The race _park_behind_queue exists for (2026-09-05): the advisory queue read at the first
        # gate saw no queue, then a peer handler parked an op before the locked re-check, so this
        # send must line up BEHIND that op — never hand over ahead of it. _working_now is the last
        # gate before the locked step, so a peer's park landing inside it is the race exactly. Fails
        # if the arm is dropped (the backend gets the send) or if it claims "parked" without parking
        # (the op is missing from the queue).
        def peer_parks_then_idle(sid):
            km._park_op(sid, ("compact",))
            return False
        km._working_now = peer_parks_then_idle
        self.assertEqual(km._send_or_park(self.be, SID, "Re: the port — 8443.", user_todo="ut-9f2c1a34"), "parked")
        self.assertEqual(self.be.calls, [], "not handed over: the queue that appeared owns the order")
        self.assertEqual(km._pending_ops[SID],
                         [("compact",), ("send", "Re: the port — 8443.", None, None, "ut-9f2c1a34")],
                         "behind the peer's op, id intact")


class DrainStampFailure(unittest.TestCase):
    """A parked answer's 'answered' stamp fires at the DRAIN, after the send reached the backend. If
    the stamp's store write fails — a store the shape guard refuses to write, a disk error — the
    answer is delivered all the same, so the failure is said on stderr and the drain goes on.
    Raised, it landed in _apply_pending_ops's failure contract ("a raise anywhere in a sid's pass
    drops that sid's queue once"), which took a parked /compact or a second message down with it
    for a bookkeeping error that had nothing to do with them."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)
        km._user_todos_cache.clear()
        km._user_todos_bad.clear()
        km._UT_FLOOR_ARM.clear()                       # the floor's arm record is process state (tests/README)
        km._set_user_todos(True)                       # the switch is OFF by default (2026-09-03)
        self.tid = km._add_user_todo(SID, "Need the staging port")
        self._saved = (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
                       km._working_now)
        km._push_all = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": None
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: False
        km._pending_ops.clear()
        km._drain_hold.clear()                         # a hold another test's delivery armed is not this story

    def tearDown(self):
        (km._compacting_now, km.Sessions.backend_for, km._push_all, km._optimistic_echo,
         km._working_now) = self._saved
        km._pending_ops.clear()
        km._drain_hold.clear()
        km.jd.STATE = self.saved_state
        km._user_todos_cache.clear()
        km._user_todos_bad.clear()
        km._UT_FLOOR_ARM.clear()
        self.td.cleanup()

    def test_a_failed_stamp_after_an_immediate_delivery_warns_and_keeps_the_dispatch_alive(self):
        # the drive handler's own stamp (sent now -> stamp now) had no such guard: a store that went
        # bad between the handler's pre-check and the stamp, or a disk error in the write, raised
        # out of _drive into the WS dispatch's per-message except — the answer was delivered, the
        # exception logged, the repaint skipped, and the client heard nothing about the row that
        # stayed open. Same contract as the drain: said on stderr, said to the client, and on.
        sent, injected, pushed = [], [], []
        client = {"send": lambda s: sent.append(json.loads(s))}
        err = io.StringIO()
        with mock.patch.object(km, "_name_of", lambda sid: "web"), \
                mock.patch.object(km, "_sdk", lambda: None), \
                mock.patch.object(km, "_send_or_park",
                                  lambda be, sid, text, echo=None, user_todo=None: injected.append(text) or True), \
                mock.patch.object(km, "_push_soon", lambda: pushed.append(1)), \
                mock.patch.object(km, "_write_user_todos", side_effect=RuntimeError("write refused")), \
                contextlib.redirect_stderr(err):
            handled = km._drive({"type": "userTodoAnswer", "id": SID, "todoId": self.tid, "text": "8443"}, client)
        self.assertTrue(handled)
        self.assertEqual(len(injected), 1, "the answer was delivered")
        warns = [m["text"] for m in sent if m.get("type") == "warn"]
        self.assertEqual(len(warns), 1, sent)
        self.assertIn("reached the session", warns[0])
        self.assertIn("stays listed", warns[0])
        self.assertEqual(pushed, [1], "the dispatch went on to its repaint")
        self.assertIn("answered stamp for %s failed after delivery" % self.tid, err.getvalue())
        self.assertNotIn("resolved", km._user_todos()[SID][0], "the ask still stands, visibly")

    def test_a_failed_stamp_after_an_sdk_delivery_keeps_the_rest_of_the_queue(self):
        be = _FakeTodoQueueBackend()
        km.Sessions.backend_for = lambda sid: be
        km._pending_ops[SID] = [("send", "Re: the port — 8443.", None, None, self.tid), ("compact",)]
        err = io.StringIO()
        with mock.patch.object(km, "_write_user_todos", side_effect=RuntimeError("write refused")), \
                contextlib.redirect_stderr(err):
            km._apply_pending_ops()
        self.assertEqual(be.calls, [("send", "Re: the port — 8443.", self.tid)], "the answer was delivered")
        self.assertEqual(km._pending_ops.get(SID), [("compact",)],
                         "the op parked behind it survives — its turn comes at the next quiet cycle")
        lines = [l for l in err.getvalue().splitlines() if l.startswith("user-todos:")]
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("answered stamp for %s failed after delivery" % self.tid, lines[0])
        self.assertNotIn("pending ops apply", err.getvalue(), "no traceback: the drain never saw a raise")
        self.assertNotIn("resolved", km._user_todos()[SID][0],
                         "nothing stamped — the ask stands until the store can be written")

    def test_a_failed_stamp_after_a_tmux_merged_paste_is_said_and_swallowed(self):
        be = _FakeBackend()
        err = io.StringIO()
        with mock.patch.object(km, "_write_user_todos", side_effect=RuntimeError("write refused")), \
                contextlib.redirect_stderr(err):
            km._deliver_send_batch(be, SID, [("send", "alpha", None, None, self.tid), ("send", "beta", None)])
        self.assertEqual(be.calls, [("send", "alpha\n\nbeta")], "the merged paste went out")
        self.assertIn("answered stamp for %s failed after delivery" % self.tid, err.getvalue())


if __name__ == "__main__":
    unittest.main()
