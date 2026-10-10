#!/usr/bin/env python3
"""Parked SENDS drain MID-TURN on a backend that forwards its own sends (the user 2026-10-08, who ruled that parked
messages always drain mid-turn, after busy sessions went hours without receiving anything: the queue drained only
when a session was quiet, and while anything was parked every new send parked behind it).

What is pinned here:
  * a parked run of sends drains while the turn is open (the drain's working gate hands it to the backend, which
    forwards at its next tool boundary), in press order;
  * a parked SETTING (env, effort, fast, auth, and a model pick where the backend switches models live) never blocks
    the sends behind it: they drain past it, and the setting stays parked until the session is quiet, where it applies;
  * an op that changes the conversation (compact, clear, a typed slash command, a move) keeps strict order: the
    sends behind it wait, as before;
  * a NEW send arriving while only settings are parked is handed over now, not parked; one arriving behind an
    earlier parked send parks behind it (press order among sends);
  * the holds still hold: compaction, an account limit, a move in flight, an op in flight;
  * a backend that does not forward sends drains nothing mid-turn, and a model pick on a backend that does not
    switch models live (Codex) stays a barrier.
Synthetic sids and fake backends only."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load: it resolves its state root at import time
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_drainmidturn", os.path.join(BIN, "romp-kernel"))

# the account gate and the prompt hold are separate axes (tests/test_kernel_limit_queue.py,
# tests/test_kernel_parked_ops_liveness.py): off here unless a test turns the limit on itself
_NO_LIMIT = lambda sid: None
km._PROMPT_HOLD_S = 0.0

SID = "11111111-2222-3333-4444-dddddddddd01"


class _ForwardBackend:
    """SDK-like: takes a send at any time (forwards_sends) and records it, with the fromUser marker when one rode.
    `live` sets model_switches_live: True for the SDK on this install, False for the Codex shape."""

    def __init__(self, live=True):
        self.calls = []
        self.live = live
        self.busy_now = True

    def forwards_sends(self):
        return True

    def model_switches_live(self):
        return self.live

    def busy(self, sid):
        return self.busy_now

    def send(self, sid, text, from_user=False):
        self.calls.append(("send", text, from_user) if from_user else ("send", text))
        return True

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def set_effort(self, sid, value):
        self.calls.append(("effort", value))
        return True

    def set_env(self, sid, value):
        self.calls.append(("env", dict(value)))
        return True

    def set_auth(self, sid, value):
        self.calls.append(("auth", value))
        return True


class _HoldBackend:
    """Hold-then-merge regime: no forwards_sends."""

    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = (km._compacting_now, km._working_now, km.Sessions.backend_for, km._push_all, km._limit_hold)
        km._push_all = lambda: None
        km._limit_hold = _NO_LIMIT
        km._compacting_now = lambda sid: False
        self.working = True
        km._working_now = lambda sid: self.working
        self.be = _ForwardBackend()
        km.Sessions.backend_for = lambda sid: self.be
        km._pending_ops.clear()
        km._inflight_ops.clear()
        km._moving.discard(SID)
        km._drain_hold.clear()
        km._held_working.clear()

    def tearDown(self):
        (km._compacting_now, km._working_now, km.Sessions.backend_for, km._push_all, km._limit_hold) = self._saved
        km._pending_ops.clear()
        km._inflight_ops.clear()
        km._moving.discard(SID)
        km._drain_hold.clear()
        km._held_working.clear()


class ParkedSendsDrainMidTurn(_Base):
    def test_a_parked_run_drains_while_the_turn_is_open(self):
        # parked while compacting; the compaction ended and the next turn is already open
        km._pending_ops[SID] = [("send", "one", None), ("send", "two", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "two")],
                         "mid-turn, the forwarding backend takes the parked run, in press order")
        self.assertFalse(km._pending_ops.get(SID), "nothing left parked")

    def test_the_from_user_marker_rides_a_mid_turn_drain(self):
        km._pending_ops[SID] = [("send", "peer note", None), ("send", "the person's words", None, None, None, None, True)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "peer note"), ("send", "the person's words", True)],
                         "the seventh slot reaches the backend, which queues that copy ahead (its fast lane)")

    def test_a_parked_env_op_does_not_block_the_sends_behind_it_and_applies_when_quiet(self):
        km._pending_ops[SID] = [("env", {"FOO": "1"}), ("send", "a", None), ("send", "b", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "a"), ("send", "b")], "the sends drain past the parked env op")
        self.assertEqual(km._pending_ops.get(SID), [("env", {"FOO": "1"})], "the env op keeps its slot, still parked")
        self.assertIn(SID, km._held_working, "the working gate still holds what is left")
        km._apply_pending_ops()
        self.assertEqual(len(self.be.calls), 2, "still mid-turn: the setting waits for a quiet session")
        self.working = False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls[-1], ("env", {"FOO": "1"}), "quiet: the parked env op applies")
        km._apply_pending_ops()
        self.assertNotIn(SID, km._pending_ops)

    def test_every_settings_kind_is_passed(self):
        km._pending_ops[SID] = [("effort", "high"), ("send", "a", None), ("auth", "x"), ("model", "opus"),
                                ("send", "b", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "a"), ("send", "b")])
        self.assertEqual(km._pending_ops.get(SID), [("effort", "high"), ("auth", "x"), ("model", "opus")],
                         "the settings stay parked, in their order")

    def test_a_parked_compact_still_blocks_the_sends_behind_it(self):
        km._pending_ops[SID] = [("compact",), ("send", "after the compact", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "the send belongs after the compaction: nothing drains")
        self.assertEqual(km._pending_ops.get(SID), [("compact",), ("send", "after the compact", None)])

    def test_sends_ahead_of_a_barrier_drain_and_those_behind_it_wait(self):
        km._pending_ops[SID] = [("send", "a", None), ("env", {"X": "1"}), ("send", "b", None), ("compact",),
                                ("send", "c", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "a"), ("send", "b")])
        self.assertEqual(km._pending_ops.get(SID), [("env", {"X": "1"}), ("compact",), ("send", "c", None)])

    def test_other_barriers_keep_strict_order(self):
        for barrier in (("command", "/autocompact auto", None), ("clear", "/clear"), ("cwd", "/tmp/elsewhere", 0)):
            with self.subTest(barrier=barrier[0]):
                self.be.calls.clear()
                km._pending_ops[SID] = [barrier, ("send", "behind", None)]
                km._apply_pending_ops()
                self.assertEqual(self.be.calls, [])
                self.assertEqual(km._pending_ops.get(SID), [barrier, ("send", "behind", None)])

    def test_a_model_pick_is_a_barrier_where_models_do_not_switch_live(self):
        # Codex: the pick lands at the next turn start while a send steers the live turn, so passing it would reach
        # the old model first, the inversion the pick parks to prevent
        self.be.live = False
        km._pending_ops[SID] = [("model", "gpt-x"), ("send", "on the new model", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [])
        self.assertEqual(km._pending_ops.get(SID), [("model", "gpt-x"), ("send", "on the new model", None)])

    def test_a_non_forwarding_backend_drains_nothing_mid_turn(self):
        self.be = _HoldBackend()
        km._pending_ops[SID] = [("send", "a", None)]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "the hold-then-merge regime waits for the turn's end")
        self.working = False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "a")])


class HoldsStillHold(_Base):
    def setUp(self):
        super().setUp()
        km._pending_ops[SID] = [("env", {"X": "1"}), ("send", "a", None)]

    def _assert_held(self):
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [])
        self.assertEqual(km._pending_ops.get(SID), [("env", {"X": "1"}), ("send", "a", None)])

    def test_compacting(self):
        km._compacting_now = lambda sid: True
        self._assert_held()

    def test_account_limit(self):
        km._limit_hold = lambda sid: {"reason": "limit", "resetsAt": None, "what": "waiting"}
        self._assert_held()

    def test_move_in_flight(self):
        km._moving.add(SID)
        self._assert_held()

    def test_op_in_flight(self):
        km._inflight_ops[SID] = km._pending_ops[SID][0]
        self._assert_held()


class NewSendBehindParkedSettings(_Base):
    def test_a_new_send_behind_a_parked_env_op_is_handed_over_now(self):
        km._pending_ops[SID] = [("env", {"FOO": "1"})]
        parked = km._send_or_park(self.be, SID, "fresh message", echo="human")
        self.assertIs(parked, False, "handed over, not queued")
        self.assertEqual(self.be.calls, [("send", "fresh message")])
        self.assertEqual(km._pending_ops.get(SID), [("env", {"FOO": "1"})], "the env op is still parked")

    def test_also_when_the_session_is_quiet(self):
        # quiet with a setting still parked (the drain's next cycle will apply it): the send is not held for that
        self.working = False
        km._pending_ops[SID] = [("effort", "low")]
        self.assertIs(km._send_or_park(self.be, SID, "now", echo=None), False)
        self.assertEqual(self.be.calls, [("send", "now")])

    def test_a_new_send_behind_an_earlier_parked_send_parks_behind_it(self):
        km._pending_ops[SID] = [("env", {"FOO": "1"}), ("send", "first", None)]
        self.assertIs(km._send_or_park(self.be, SID, "second", echo=None), True, "press order among sends")
        self.assertEqual(self.be.calls, [])
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "first"), ("send", "second")], "both drain, in order, mid-turn")

    def test_a_new_send_behind_a_parked_compact_still_parks(self):
        km._pending_ops[SID] = [("compact",)]
        self.assertIs(km._send_or_park(self.be, SID, "after", echo=None), True)
        self.assertEqual(self.be.calls, [])

    def test_a_new_slash_command_behind_a_parked_setting_still_parks(self):
        km._pending_ops[SID] = [("env", {"FOO": "1"})]
        self.assertIs(km._send_or_park(self.be, SID, "/autocompact auto", echo=None), True,
                      "a slash command must fire as its own top-level prompt, never forwarded mid-turn")
        self.assertEqual(self.be.calls, [])

    def test_a_new_send_parks_during_a_move_or_behind_an_op_in_flight(self):
        km._pending_ops[SID] = [("env", {"FOO": "1"})]
        km._moving.add(SID)
        self.assertIs(km._send_or_park(self.be, SID, "during the move", echo=None), True)
        km._moving.discard(SID)
        km._pending_ops[SID] = [("env", {"FOO": "1"})]
        km._inflight_ops[SID] = km._pending_ops[SID][0]
        self.assertIs(km._send_or_park(self.be, SID, "during the reconnect", echo=None), True)
        self.assertEqual(self.be.calls, [])

    def test_a_new_send_behind_a_parked_model_pick_parks_where_models_do_not_switch_live(self):
        self.be.live = False
        km._pending_ops[SID] = [("model", "gpt-x")]
        self.assertIs(km._send_or_park(self.be, SID, "after the pick", echo=None), True)
        self.assertEqual(self.be.calls, [])

    def test_a_non_forwarding_backend_still_parks_behind_any_queue(self):
        hb = _HoldBackend()
        self.working = False
        km._pending_ops[SID] = [("env", {"FOO": "1"})]
        self.assertIs(km._send_or_park(hb, SID, "held", echo=None), True)
        self.assertEqual(hb.calls, [])


if __name__ == "__main__":
    unittest.main()
