#!/usr/bin/env python3
"""A session whose CARD reads awaiting must not read READY at the session level (the user 2026-08-01).

The live case: a session dispatched a long GPU batch from a thread that was carrying a block. The feed
card yielded blocked → awaiting and said so — "waiting on a background task: <desc>" — while the timeline
lane, the rail chip and the chat chip all showed plain READY, so at a glance the session looked idle with
nothing happening, right next to its own card saying otherwise.

Neither session-level source could see it, and both were behaving as designed:
  * the LIVE bg-task source counts only launches the judge has NOT placed yet; once placed, a task with no
    live ⏳ stamp is a SERVICE (the mkdocs-serve rule, 2026-07-24) — furniture, not a wait;
  * the durable stamp source skips rolled-up nodes, and every stamp this goal had sat on sub-goals the
    judge had since marked done.
The feed CARD's own blocked-yield was the only rule looking at the right evidence: a dispatch from the
blocked card's own subtree, newer than the block.

So that rule becomes a session-level source too (_owned_yield_why), and the feed's straw dot is taken
from the cards the same build already produced. The user's invariant, stated in their words: a card in
Working must be explained by an active session, a judgment in flight, an awaiting, or an error.

SYNTHETIC only: placeholder UUIDs, invented task descriptions.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_ownyield", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-888888888888"
TOP = SID + ":g1"
BORN, BLOCKED, DISPATCH = 100, 200, 300      # goal minted / blocked / the background task launched
DESC = "nightly GPU batch: done-marker watch"


class OwnedYieldAwaiting(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        self.path = str(td / (SID + ".jsonl"))
        Path(self.path).write_text("")
        self.saved = {k: getattr(km, k) for k in ("_bg_live_norm", "_bg_placed_tops", "_tmux_sessions")}
        # one live background task, attributed by the judge to TOP's subtree
        km._bg_live_norm = lambda sid, path: [{"tid": "t1", "desc": DESC, "t": DISPATCH, "type": ""}]
        km._bg_placed_tops = lambda sid, path, tids: {"t1": TOP}
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": DISPATCH, "model": "",
                                           "effort": "", "context": None, "compactPct": None,
                                           "color": None}}
        km._SESSION_STAMP_CACHE.clear()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _store(self, status="blocked", blocked=True, blk_t=BLOCKED):
        nd = {"id": TOP, "text": "run the batch", "parentId": None, "nodeComplete": False,
              "blocked": blocked, "blockWhy": "need your call" if blocked else None,
              "cleared": False, "trail": [], "t": BORN, "mt": blk_t}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": TOP, "placements": {},
             "status": {TOP: status}, "nodes": {TOP: nd}}))

    # ---- the rule itself ----

    def test_a_dispatch_that_outran_the_block_is_the_session_awaiting_why(self):
        self._store()
        why = km._owned_yield_why(SID, self.path)
        self.assertEqual(why, "waiting on a background task: " + DESC,
                         "the same why the card shows — one story, both surfaces")

    def test_a_dispatch_that_predates_the_block_proves_nothing(self):
        # the block is the NEWER event: the thread has not moved past it, so this is a genuine needs-you
        self._store(blk_t=DISPATCH + 50)
        self.assertIsNone(km._owned_yield_why(SID, self.path))

    def test_a_service_under_a_working_goal_stays_furniture(self):
        # THE REGRESSION GUARD for the 2026-07-24 split: a dev server (mkdocs serve) under a working goal
        # has no block to outrun, so it must not light awaiting anywhere.
        self._store(status="working", blocked=False)
        self.assertIsNone(km._owned_yield_why(SID, self.path))

    def test_no_live_task_no_why(self):
        self._store()
        km._bg_live_norm = lambda sid, path: []
        self.assertIsNone(km._owned_yield_why(SID, self.path))

    def test_an_unattributable_launch_never_masks_a_block(self):
        # a launch the judge cannot place owns nothing — the conservative failure the ownership rule exists
        # for (an unproven dispatch must never dress a real needs-you as awaiting)
        self._store()
        km._bg_placed_tops = lambda sid, path, tids: {}
        self.assertIsNone(km._owned_yield_why(SID, self.path))

    # ---- what the session-scoped surfaces (timeline lane, rail chip, chat chip) read ----

    def test_the_session_surfaces_light_from_it_when_idle(self):
        self._store()
        self.assertEqual(km._session_awaiting(SID, self.path, True, stamp=True),
                         {"kind": "task", "why": "waiting on a background task: " + DESC,
                          "since": None},   # the owned-yield read has no single event time → no duration
                         "the lane/chip say awaiting instead of READY")

    def test_the_feed_path_is_unchanged_no_session_wide_floor(self):
        # stamp=False is the FEED's call: it scopes awaiting per goal, so a session-wide why here would
        # floor every sibling card of this session to awaiting. The feed lights its dot off the CARDS.
        self._store()
        self.assertIsNone(km._session_awaiting(SID, self.path, True, stamp=False))

    def test_an_open_turn_is_just_working(self):
        # idle=False → an actively producing session is working, never awaiting (unchanged contract)
        self._store()
        self.assertIsNone(km._session_awaiting(SID, self.path, False, stamp=True))


if __name__ == "__main__":
    unittest.main()
