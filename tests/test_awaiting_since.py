#!/usr/bin/env python3
"""_session_awaiting (bin/romp-kernel) carries `since` — the wait's OWN event time — beside why/kind,
so every awaiting surface can say how long the wait has held (the user 2026-08-23: Working shows its
running minutes; the awaiting states showed nothing, so a wait stuck for hours read the same as one
seconds old). Each source contributes its own event stamp, never wall-clock now: the oldest live
agent's start, the oldest pending dispatch, the overlay row's t, the judge stamp's awaitingAt. A source
with no event time sends None and the UI shows no duration. Synthetic inputs only.
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
km = SourceFileLoader("romp_kernel_awaitsince", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class SessionAwaitingSince(unittest.TestCase):
    """Stub each source _session_awaiting reads, in precedence order, and check its since."""

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_tmux_sessions", "_bg_live_norm", "_bg_pending", "_states_awaiting_overlay",
                        "_owned_yield_why", "_session_stamp_full", "_session_delegated_why")}
        # neutral defaults: a live CLI with nothing in flight, every deeper source empty
        km._tmux_sessions = lambda: {SID: {}}
        km._bg_live_norm = lambda sid, path: []
        km._bg_pending = lambda sid, path, tasks: []
        km._states_awaiting_overlay = lambda sid: None
        km._owned_yield_why = lambda sid, path: None
        km._session_stamp_full = lambda sid: (None, 0, None, None, ())
        km._session_delegated_why = lambda sid: None

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)

    def test_live_subagents_use_the_oldest_agents_start(self):
        km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "a", "since": 500},
                                                         {"type": "b", "since": 900}]}}
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual(aw["kind"], "agents")
        self.assertEqual(aw["since"], 500)   # the wait has held at least since the oldest live agent

    def test_pending_bg_tasks_use_the_oldest_dispatch(self):
        tasks = [{"tid": "1", "desc": "watching CI run", "t": 700, "type": "bash"},
                 {"tid": "2", "desc": "poll deploy", "t": 300, "type": "bash"}]
        km._bg_live_norm = lambda sid, path: tasks
        km._bg_pending = lambda sid, path, ts: ts
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual(aw["since"], 300)
        self.assertIn("2 background tasks", aw["why"])

    def test_overlay_rides_its_own_rows_stamp(self):
        km._states_awaiting_overlay = lambda sid: {"awaiting": True, "why": "waiting on a build",
                                                   "kind": "job", "t": 4321}
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual(aw["since"], 4321)

    def test_judge_stamp_rides_its_awaitingAt(self):
        km._session_stamp_full = lambda sid: ("g1", 8765, "waiting on the test suite", "task", ())
        aw = km._session_awaiting(SID, "/tmp/x", True, stamp=True)
        self.assertEqual(aw["why"], "waiting on the test suite")
        self.assertEqual(aw["since"], 8765)

    def test_an_event_less_source_sends_none_never_a_guess(self):
        km._owned_yield_why = lambda sid, path: "waiting on a background task: sweep"
        aw = km._session_awaiting(SID, "/tmp/x", True, stamp=True)
        self.assertIsNone(aw["since"])
        km._owned_yield_why = lambda sid, path: None
        km._session_delegated_why = lambda sid: "delegated to web; waiting on their result"
        aw = km._session_awaiting(SID, "/tmp/x", True, stamp=True)
        self.assertIsNone(aw["since"])

    def test_not_awaiting_stays_falsy(self):
        self.assertIsNone(km._session_awaiting(SID, "/tmp/x", True))
        self.assertIsNone(km._session_awaiting(SID, "/tmp/x", False))   # an open turn is just working


if __name__ == "__main__":
    unittest.main()
