#!/usr/bin/env python3
"""_awaiting_card (bin/romp-kernel): a LIVE, IDLE session awaiting a dispatched BACKGROUND TASK with no
open goal to floor gets a lightweight working-column placeholder in the FEED — so the wait shows there, not
only on the timeline's faded awaiting stretch (the user 2026-07-13, who noted there's no card there). Synthetic
inputs only: placeholder UUID, no real session data.
"""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_awaitcard", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
COLOR = {"bg": "#9cd2ff", "fg": "#0c1a2e"}


class AwaitingCard(unittest.TestCase):
    def setUp(self):
        # _awaiting_card reads the parse (for the last-activity time) and the live bg-task descriptions;
        # stub both so the card is a pure function of its inputs (no live session / backend needed).
        self._parse, self._descs = km._parse, km._awaiting_task_descs
        km._parse = lambda path, fsid, now: {"turns": [{"t": 1000}]}
        km._awaiting_task_descs = lambda fsid, path=None: ["watching CI run"]

    def tearDown(self):
        km._parse, km._awaiting_task_descs = self._parse, self._descs

    def _card(self, why="waiting on a background task: watching CI run", live=True, now=2000):
        return km._awaiting_card({"sid": SID, "path": "/tmp/x"}, "docs", COLOR, SID, live, now, why)

    def test_shape_is_a_working_column_provisional_awaiting_card(self):
        c = self._card()
        self.assertEqual(c["itemId"], "awaiting:" + SID)
        self.assertEqual(c["sid"], SID)
        self.assertEqual(c["column"], "working")
        self.assertTrue(c["provisional"])          # ephemeral placeholder, no goal node → open-on-click
        self.assertFalse(c["judging"])             # idle-awaiting, not analyzing → the pill carries the state
        self.assertEqual(c["tree"], [])
        self.assertIsNone(c["blocked"])            # never needs-input — the wait is NOT on you

    def test_awaiting_carries_the_live_task_descriptions_for_the_pill(self):
        c = self._card()
        self.assertEqual(c["awaiting"]["tasks"], ["watching CI run"])
        self.assertEqual(c["awaiting"]["why"], "waiting on a background task: watching CI run")

    def test_awaiting_carries_the_waits_own_start_for_the_elapsed_readout(self):
        # the user 2026-08-23: Working shows how long it has been running, the awaiting states showed
        # nothing — `since` (the wait's own event time, never wall-clock now) feeds the chips' duration
        c = km._awaiting_card({"sid": SID, "path": "/tmp/x"}, "docs", COLOR, SID, True, 2000,
                              "waiting on a background task", kind="task", since=1234)
        self.assertEqual(c["awaiting"]["since"], 1234)
        self.assertIsNone(self._card()["awaiting"]["since"])   # absent → None: the UI shows no duration, never a guess

    def test_headline_capitalizes_the_why(self):
        self.assertTrue(self._card()["text"].startswith("Waiting on a background task"))

    def test_empty_why_falls_back_to_a_generic_headline(self):
        self.assertEqual(self._card(why="")["text"], "Waiting on a background task")

    def test_a_dead_session_gets_no_card(self):
        self.assertIsNone(self._card(live=False))


if __name__ == "__main__":
    unittest.main()
