#!/usr/bin/env python3
"""The user-reopen floor, exercised through its SURVIVING producer (the user 2026-07-25: the messageless
"Move to Working" — user_move — was removed; a reply to the card, optimistic_followup, is the one
user-reopen gesture left): reopen/unblock the goal wherever the block sits; the reopen event derives the
followupAt evidence floor and HOLDS the top open when the subtree is all-done (stub nodes retired
2026-07-07) — plus the _done_is_stale guard (a done verdict from evidence at/before the reopen must not
snap the card back to Completed) and the grouper's once-done-guard REMOVAL (a reopened once-done top is
groupable again). All fixtures SYNTHETIC."""
import json
import shutil
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
import os

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_usermove", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
G2 = SID + ":g2"
G3 = SID + ":g3"
NOW = 1781100000


def node(nid, text, parent=None, done=False, blocked=False, **kw):
    nd = {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
          "blocked": blocked, "cleared": False, "trail": [], "t": NOW - 600, "mt": NOW - 300}
    nd.update(kw)
    return nd


class FollowupReopenBlocked(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _write(self, store):
        jd.save_goals(SID, store)

    def test_blocked_to_working_clears_descendant_block(self):
        # the block sits on a DESCENDANT (the planner blocked a sub) — _reopen alone wouldn't reach it
        store = {"rompUuid": SID, "seq": 2, "placements": {}, "status": {},
                 "nodes": {G1: node(G1, "Ship the feature", blocked=True, blockWhy="pick a name"),
                           G2: node(G2, "Decide the name", parent=G1, blocked=True)}}
        jd.rollup_status(store, False)
        self.assertEqual(store["status"][G1], "blocked")
        self._write(store)

        self.assertTrue(jd.optimistic_followup(SID, G1, now=NOW))
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][G1], "working")
        self.assertFalse(st["nodes"][G1]["blocked"])
        self.assertFalse(st["nodes"][G2]["blocked"])
        self.assertEqual(st["nodes"][G1]["followupAt"], NOW)     # sort floor + staleness floor armed
        self.assertTrue(st["nodes"][G1].get("followupPending"),  # a reply IS in flight: the chip shows
                        "msg-marked reopen carries the Followed-up chip")
        # an open sub exists, so no stub was needed
        self.assertFalse(any(n.get("provisional") for n in st["nodes"].values()))

    def test_completed_to_working_holds_open_and_stays_working(self):
        # all children genuinely done — the exact shape the retired stub node covered: the unanswered
        # user reopen EVENT must hold the top at working against bottom-up is_complete (2026-07-07).
        store = {"rompUuid": SID, "seq": 2, "placements": {}, "status": {},
                 "nodes": {G1: node(G1, "Build the exporter", done=True, settledDone=True,
                                    settledAt=NOW - 100),
                           G2: node(G2, "Write the writer", parent=G1, done=True)}}
        jd.migrate_store(store)                        # legacy-flag fixture → the boot sweep adopts it
        jd.rollup_status(store, True)
        self.assertEqual(store["status"][G1], "completed")
        self._write(store)

        self.assertTrue(jd.optimistic_followup(SID, G1, now=NOW))
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][G1], "working")
        self.assertEqual(sorted(st["nodes"]), [G1, G2],
                         "no stub node is minted — the reopen event alone holds the top open")
        # _reopen effects rode along: settledAt → deltaSince for the delta re-distill; once-done
        # history lives in the diary now (everDone retired 2026-07-08)
        self.assertTrue(any(e["kind"] == "done" for e in st["nodes"][G1]["log"]))
        self.assertNotIn("everDone", st["nodes"][G1])
        self.assertNotIn("settledAt", st["nodes"][G1])
        self.assertEqual(st["nodes"][G1]["deltaSince"], NOW - 100)
        # a SECOND reopen is idempotent: still working, still exactly the same two nodes
        self.assertTrue(jd.optimistic_followup(SID, G1, now=NOW + 5))
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][G1], "working")
        self.assertEqual(sorted(st["nodes"]), [G1, G2])

    def test_missing_goal_refused(self):
        self._write({"rompUuid": SID, "seq": 0, "placements": {}, "status": {}, "nodes": {}})
        self.assertFalse(jd.optimistic_followup(SID, G1, now=NOW))


class StaleDoneGuard(unittest.TestCase):
    """A done verdict from evidence at/before the user's move is VOID; newer evidence completes normally."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _moved_store(self):
        store = {"rompUuid": SID, "seq": 2, "placements": {}, "status": {},
                 "nodes": {G1: node(G1, "Build the exporter", done=True),
                           G2: node(G2, "Write the writer", parent=G1, done=True)}}
        jd.rollup_status(store, True)
        jd.save_goals(SID, store)
        jd.optimistic_followup(SID, G1, now=NOW)
        return jd.load_goals(SID)

    def test_closer_stale_done_void_fresh_done_lands(self):
        store = self._moved_store()
        menu = [store["nodes"][G1]]
        # replayed verdict, evidence STRICTLY before the move → void
        self.assertEqual(jd.apply_close(store, menu, {"done": {1: "did it"}}, t=NOW - 30), [])
        self.assertFalse(store["nodes"][G1]["nodeComplete"])
        # genuinely newer evidence → completes normally (the floor, never a pin)
        self.assertEqual(jd.apply_close(store, menu, {"done": {1: "did it"}}, t=NOW + 60), [G1])
        self.assertTrue(store["nodes"][G1]["nodeComplete"])

    def test_done_at_exactly_the_stamp_lands(self):
        # the deliberate </<= asymmetry vs _block_is_stale (the user 2026-07-06): a nudge/follow-up's own
        # turn carries trigger t == followupAt, and its work RESOLVING the goal must land — with <= the
        # resolving turn voided itself and the card wedged in Working (the stuck 'drag' card).
        store = self._moved_store()
        menu = [store["nodes"][G1]]
        self.assertEqual(jd.apply_close(store, menu, {"done": {1: "did it"}}, t=NOW), [G1])
        self.assertTrue(store["nodes"][G1]["nodeComplete"])

    def test_planner_stale_done_void_fresh_done_lands(self):
        store = self._moved_store()
        menu = [{"id": G1, "text": "Build the exporter"}]
        ops = [{"do": "done", "goal": 1, "why": "already finished"}]
        jd.apply_plan(store, "seg-stale", NOW - 30, list(ops), menu, place_key="seg-stale")
        self.assertFalse(store["nodes"][G1]["nodeComplete"])
        jd.apply_plan(store, "seg-fresh", NOW + 60, list(ops), menu, place_key="seg-fresh")
        self.assertTrue(store["nodes"][G1]["nodeComplete"])

    def test_stale_block_still_void_too(self):
        # the same followupAt floor keeps guarding blocks (pre-existing behavior, same stamp)
        store = self._moved_store()
        menu = [store["nodes"][G1]]
        jd.apply_close(store, menu, {"block": {1: "waiting on you"}}, t=NOW)
        self.assertFalse(store["nodes"][G1]["blocked"])


class GrouperMovesOnceDone(unittest.TestCase):
    """The never-move-a-once-done-node guard is REMOVED (the user 2026-07-06): a reopened once-done top
    is live work again, so the grouper may nest it — an erroneous split pushed back to Working re-merges.
    (The everDone flag itself was retired 2026-07-08; once-done history lives in the diary.)"""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_group_never_relinks_anything_anymore(self):
        # T101: the once-done guard question is moot — the group op itself is retired
        store = {"rompUuid": SID, "seq": 3, "placements": {}, "status": {},
                 "nodes": {G1: node(G1, "Fix the parser",
                                    log=[{"ev_t": NOW - 400, "src": "closer", "kind": "done", "at": NOW - 400},
                                         {"ev_t": NOW - 200, "src": "user", "kind": "reopen", "at": NOW - 200}]),
                           G2: node(G2, "Parser rewrite effort"),
                           G3: node(G3, "Add parser tests")}}
        tops = [store["nodes"][G1], store["nodes"][G2], store["nodes"][G3]]
        ops = [{"do": "group", "goal": 1, "under": 2, "why": "same parser effort"}]
        self.assertEqual(jd.apply_group(store, tops, ops, NOW), 0)
        self.assertIsNone(store["nodes"][G1].get("parentId"), "every top stays its own card")


if __name__ == "__main__":
    unittest.main()
