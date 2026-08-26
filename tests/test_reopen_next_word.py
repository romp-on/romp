#!/usr/bin/env python3
"""The reopen-orphan guarantee (the user 2026-08-23): a blocked/completed card the user's reply
reopened must get a judge's next word. Before this, the optimistic reopen cleared `blocked`, the
closer could close the reply turn without speaking to the goal, the unblocker skipped it (not
blocked), and the nudge walk could never re-arm (the reply turn is romp-injected) — while a spent
`failed` ledger latch from the pre-block episode routed the goal into the already-nudged branch
every tick. The audited card sat in Working 2h45m with zero judge calls until the user noticed by
eye. Two arms, executed here: the closer flags reply-reopened menu goals for an explicit ruling,
and the reply gesture retires the goal's SPENT nudge record so the ladder re-arms. SYNTHETIC only."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
T = 1_787_500_000


def _node(gid, rows):
    return {"id": gid, "text": "Run the export pipeline", "parentId": None, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": T, "mt": T, "log": rows}


def _row(kind, src, t, **kw):
    return {"ev_t": t, "at": t, "src": src, "kind": kind, **kw}


class ReplyReopenedFlag(unittest.TestCase):
    def test_a_reply_reopened_goal_flags_until_a_judge_speaks(self):
        gid = SID + ":g1"
        nd = _node(gid, [_row("block", "closer", T, why="run this copy command"),
                         _row("reopen", "user", T + 180, msg=True),
                         _row("reopen", "planner", T + 180)])
        self.assertEqual(jd._reply_reopened_ids([nd]), {gid})

    def test_a_later_ruling_stands_the_flag_down(self):
        gid = SID + ":g1"
        nd = _node(gid, [_row("block", "closer", T),
                         _row("reopen", "user", T + 180, msg=True),
                         _row("done", "closer", T + 400, why="delivered")])
        self.assertEqual(jd._reply_reopened_ids([nd]), set(),
                         "the judges already spoke on the reopened goal — nothing owed")

    def test_undo_and_moveless_reopens_never_flag(self):
        g1, g2 = SID + ":g1", SID + ":g2"
        undo = _node(g1, [_row("clear", "user", T),
                          _row("reopen", "user", T + 60, msg=True, undo=True)])
        judge_only = _node(g2, [_row("block", "closer", T),
                                _row("reopen", "planner", T + 60)])
        self.assertEqual(jd._reply_reopened_ids([undo, judge_only]), set(),
                         "an undo restore and a judge's own unseal are not the user's assertion")

    def test_the_closer_menu_carries_the_instruction(self):
        src = open(os.path.join(BIN, "romp-judge")).read()
        self.assertIn("_reply_reopened_ids(menu)", src)
        self.assertIn("reopened by the user's own reply after an earlier ruling", src)


class ReplyRetiresSpentLedger(unittest.TestCase):
    def test_a_failed_latch_drops_and_a_live_record_stays(self):
        gid = SID + ":g1"
        km._put_nudged(gid, {"count": 2, "lastTurnId": "turn-old", "failed": True})
        km._drop_auto_nudge_rec(gid)
        self.assertNotIn(gid, km._auto_nudge_data().get("nudged", {}),
                         "the spent episode's latch is void once the user answered")
        km._put_nudged(gid, {"count": 1, "lastTurnId": "turn-new"})
        km._drop_auto_nudge_rec(gid)
        self.assertIn(gid, km._auto_nudge_data().get("nudged", {}),
                      "a live mid-episode record is the ladder's memory — never dropped")

    def test_the_reply_gesture_wires_the_retirement(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        i = src.find("jd.optimistic_followup(sid, iid")
        self.assertGreater(i, 0)
        self.assertIn("_drop_auto_nudge_rec(str(iid))", src[i:i + 2000],
                      "the reply that reopens the card also retires its spent nudge episode")


if __name__ == "__main__":
    unittest.main()
