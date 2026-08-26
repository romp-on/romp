#!/usr/bin/env python3
"""A nudge's silence surfaces on the LOST-SEND EVENT, not a clock (the user 2026-08-24, W2d of the
time-windows package): a modern fire writes the goal-id marker by construction, so a turn of the
TARGET that ends after the fire carrying no marker segment — with the backend queue no longer
holding the message — proves the send never reached the transcript, and the failure stamps NOW on
that turn's own end (the _debt_reminder_outcomes precedent). While the message sits in the backend
queue it is in flight, not lost; a session that never ends a turn after the fire emits no event at
all — the one silence that keeps a named dead-man (LOST_SEND_DEADMAN_SECS). SYNTHETIC fixtures."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_lse", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-999999999999"
GID = SID + ":g1"
NOW = 1_788_000_000
FIRE = NOW - 3600                                    # ONE hour ago — the retired clock would still wait


class LostSendEvent(unittest.TestCase):
    def setUp(self):
        self.saved = km.Sessions.backend_for
        self.queued = []
        outer = self

        class _BE:
            def pending_queued(self, sid):
                return outer.queued

        km.Sessions.backend_for = lambda sid: _BE()
        self.store = {"placements": {}, "nodes": {}}
        self.rec = {"wake": True, "anchor": FIRE - 3600, "count": 1,
                    "lastTurnId": "t1", "armAtoms": 2, "at": FIRE}

    def tearDown(self):
        km.Sessions.backend_for = self.saved

    def _turns(self, end):
        # the arm turn, unchanged since the fire (2 atoms), plus a LATER ended turn with no marker
        return [{"id": "t1", "ended": True, "end": FIRE - 100, "t": FIRE - 200,
                 "atoms": [{"a": 1}, {"a": 2}]},
                {"id": "t2", "ended": True, "end": end, "t": end - 50, "atoms": [{"a": 3}]}]

    def test_a_markerless_turn_end_after_the_fire_is_the_event(self):
        ready, resp = km._nudge_response_ready(self._turns(end=FIRE + 300), self.store,
                                               self.rec, GID, NOW)
        self.assertEqual((ready, resp), (True, None),
                         "the send never landed — surface it NOW, not at hour six")

    def test_a_queued_send_is_in_flight_not_lost(self):
        self.queued = ["...<!-- romp-goal-id: %s -->..." % GID]
        ready, resp = km._nudge_response_ready(self._turns(end=FIRE + 300), self.store,
                                               self.rec, GID, NOW)
        self.assertEqual((ready, resp), (False, None),
                         "parked behind a running turn — the event has not happened")

    def test_no_ended_turn_after_the_fire_keeps_the_deadman(self):
        # the parse moved (a newer turn opened) but nothing has ENDED since the fire: an open turn
        # may still fold the send, so the event hasn't happened — the dead-man owns this residue
        turns = [{"id": "t1", "ended": True, "end": FIRE - 100, "t": FIRE - 200,
                  "atoms": [{"a": 1}, {"a": 2}]},
                 {"id": "t2", "ended": False, "end": 0, "t": FIRE + 100, "atoms": [{"a": 3}]}]
        ready, _ = km._nudge_response_ready(turns, self.store, self.rec, GID, NOW)
        self.assertFalse(ready, "no event yet — an open turn may still fold the send")
        ready, resp = km._nudge_response_ready(turns, self.store, self.rec, GID,
                                               FIRE + km.LOST_SEND_DEADMAN_SECS + 60)
        self.assertEqual((ready, resp), (True, None), "…and past the dead-man, it still surfaces")

    def test_a_landed_marker_keeps_the_judge_gates(self):
        # the send REACHED the transcript: the event never fires; placement gates rule as before
        seg = {"id": "s9"}
        saved = (km.jd._segs, km.jd._seg_nudge, km.jd._seg_followup_all)
        km.jd._segs = lambda tn, store: ([seg] if tn.get("id") == "t2" else [])
        km.jd._seg_nudge = lambda s2: True
        km.jd._seg_followup_all = lambda s2: {GID}
        try:
            ready, resp = km._nudge_response_ready(self._turns(end=FIRE + 300), self.store,
                                                   self.rec, GID, NOW)
        finally:
            km.jd._segs, km.jd._seg_nudge, km.jd._seg_followup_all = saved
        self.assertEqual((ready, resp), (False, seg),
                         "visible but unplaced → the planner's queue, no clock involved")


if __name__ == "__main__":
    unittest.main()
