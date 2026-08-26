#!/usr/bin/env python3
"""Optimistic concurrency on the goal store (the user 2026-07-22).

Writers are concurrent and uncoordinated: every judge pass holds its store across a minutes-long model
call, while the kernel's nudge tick stamps blocks on its own thread. save_goals used to rename blindly, so
last-writer-wins silently ERASED the other's events — a card the nudge had just blocked flashed back to
'working' for one push before the next load healed it from the override journal.

save_goals now compares the revision it loaded at against the one on disk and REBASES (union of verdict
logs) instead of clobbering. The store is an append-only event log, so two writers appending different
events never really conflicted: the right answer is both sets. All fixtures SYNTHETIC.
"""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


class StoreCas(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _nid(self, n):
        return "%s:g%d" % (SID, n)

    def _seed(self):
        """One working top goal, published."""
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "A goal"}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)

    def test_rev_advances_on_every_publish(self):
        self._seed()
        r1 = jd._disk_rev(SID)
        self.assertGreater(r1, 0, "a published store carries a revision")
        s = jd.load_goals(SID)
        # A REAL change: since 2026-07-22 a save whose content matches disk is not a publish at all and
        # leaves `rev` where it is (see tests/test_judge_store_noop_publish.py). `rev` counts publications.
        jd.record_verdict(s, s["nodes"][self._nid(1)], "romp", "block", T0 + 30, why="needs a decision")
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.assertGreater(jd._disk_rev(SID), r1, "each publish advances the revision")

    def test_load_stamps_a_base_rev_that_is_never_serialized(self):
        self._seed()
        s = jd.load_goals(SID)
        self.assertIn("_baseRev", s, "the loaded revision is remembered for the CAS")
        jd.save_goals(SID, s)
        self.assertNotIn("_baseRev", json.loads((jd.GOALDIR / (SID + ".json")).read_text()),
                         "the transient base revision is never written to disk")

    def test_a_stale_pass_no_longer_erases_a_concurrent_block(self):
        # THE BUG: pass A loads, goes off to its model call; the nudge tick blocks the card and publishes;
        # pass A then saves its pre-block snapshot and wipes the block -> the card flashes back to working.
        self._seed()
        gid = self._nid(1)
        a = jd.load_goals(SID)                       # pass A's snapshot (pre-block)
        nudge = jd.load_goals(SID)                   # the nudge tick, on its own thread
        jd.record_verdict(nudge, nudge["nodes"][gid], "nudge", "block", T0 + 100, why="owed")
        jd.rollup_status(nudge, session_closed=False)
        jd.save_goals(SID, nudge)                    # the block is published
        self.assertEqual(jd.load_goals(SID)["status"][gid], "blocked", "premise: the block landed")
        jd.save_goals(SID, a)                        # pass A publishes its STALE snapshot
        healed = jd.load_goals(SID)
        self.assertTrue(any(e.get("kind") == "block" for e in healed["nodes"][gid].get("log") or []),
                        "the concurrent block survives the stale pass's save")
        self.assertEqual(healed["status"][gid], "blocked",
                         "and the rolled-up status still reads blocked - no working flicker")

    def test_both_writers_events_survive_a_rebase(self):
        # two passes append DIFFERENT events; the store is an event log, so the answer is BOTH
        self._seed()
        gid = self._nid(1)
        a, b = jd.load_goals(SID), jd.load_goals(SID)
        jd.record_verdict(a, a["nodes"][gid], "planner", "block", T0 + 50, why="a's block")
        jd.record_verdict(b, b["nodes"][gid], "closer", "done", T0 + 60, why="b's done")
        jd.save_goals(SID, a)
        jd.save_goals(SID, b)                        # b rebases onto a instead of clobbering
        log = jd.load_goals(SID)["nodes"][gid].get("log") or []
        kinds = {(e.get("src"), e.get("kind")) for e in log}
        self.assertIn(("planner", "block"), kinds, "the first writer's event survives")
        self.assertIn(("closer", "done"), kinds, "the second writer's event is there too")

    def test_a_stale_pass_no_longer_erases_a_fresh_takeaway(self):
        # "Stuck on Distilling" (the user 2026-08-23): distill-family fields are STATE, not log rows,
        # so the event fold never carried them — a pass holding a pre-distill snapshot across its model
        # call erased the freshly-published summary on save, the card flipped back to "Distilling…",
        # and the distiller re-ran, oscillating for as long as writers overlapped.
        self._seed()
        gid = self._nid(1)
        a = jd.load_goals(SID)                       # pass A's snapshot (pre-distill)
        d = jd.load_goals(SID)                       # the distiller
        d["nodes"][gid]["summary"] = "Shipped the exporter end to end."
        d["nodes"][gid]["distilledMt"] = T0 + 500
        d["nodes"][gid]["blockSummary"] = "Decide: keep or drop the legacy path."
        d["nodes"][gid]["briefedMt"] = T0 + 500
        jd.save_goals(SID, d)
        jd.record_verdict(a, a["nodes"][gid], "planner", "unblock", T0 + 600, why="a's own event")
        jd.save_goals(SID, a)                        # the stale pass publishes; must rebase, not clobber
        nd = jd.load_goals(SID)["nodes"][gid]
        self.assertEqual(nd.get("summary"), "Shipped the exporter end to end.",
                         "the takeaway survives a stale writer's save")
        self.assertEqual(nd.get("distilledMt"), T0 + 500)
        self.assertEqual(nd.get("blockSummary"), "Decide: keep or drop the legacy path.")

    def test_the_newer_distill_episode_wins_and_a_deliberate_reopen_is_kept(self):
        self._seed()
        gid = self._nid(1)
        # disk holds an OLD episode; our snapshot re-distilled a NEWER one → ours stands
        d0 = jd.load_goals(SID)
        d0["nodes"][gid]["summary"] = "old episode"
        d0["nodes"][gid]["distilledMt"] = T0 + 100
        jd.save_goals(SID, d0)
        mine = jd.load_goals(SID)
        stale = jd.load_goals(SID)
        mine["nodes"][gid]["summary"] = "new episode"
        mine["nodes"][gid]["distilledMt"] = T0 + 200
        jd.save_goals(SID, stale)                    # move the rev so mine must rebase
        jd.save_goals(SID, mine)
        self.assertEqual(jd.load_goals(SID)["nodes"][gid].get("summary"), "new episode")
        # the blocked path's deliberate ""→None re-open keeps its OLD briefedMt on purpose: an equal
        # disk stamp must not resurrect the "" it nulled
        b0 = jd.load_goals(SID)
        b0["nodes"][gid]["blockSummary"] = ""
        b0["nodes"][gid]["briefedMt"] = T0 + 300
        jd.save_goals(SID, b0)
        reopener = jd.load_goals(SID)
        mover = jd.load_goals(SID)
        reopener["nodes"][gid]["blockSummary"] = None
        jd.save_goals(SID, mover)                    # rev moves; the re-opener must rebase
        jd.save_goals(SID, reopener)
        self.assertIsNone(jd.load_goals(SID)["nodes"][gid].get("blockSummary"),
                          "an equal disk stamp keeps the re-opener's pending state")

    def test_a_node_minted_by_the_other_writer_is_adopted(self):
        self._seed()
        a = jd.load_goals(SID)                       # snapshot before the other writer mints
        b = jd.load_goals(SID)
        jd.apply_plan(b, "s2", T0 + 20, [{"do": "mint", "why": "x", "text": "Their new goal"}],
                      jd.open_menu(b))
        jd.save_goals(SID, b)
        jd.save_goals(SID, a)                        # a must not delete a goal it never saw
        nodes = jd.load_goals(SID)["nodes"]
        self.assertIn(self._nid(2), nodes, "the other writer's minted node survives the stale save")
        self.assertIn(self._nid(1), nodes)

    def test_rebase_folds_a_duplicate_verdict_instead_of_doubling_it(self):
        # verdict identity is (ev_t, src, kind) - the same triple _replay_overrides dedups on
        self._seed()
        gid = self._nid(1)
        a, b = jd.load_goals(SID), jd.load_goals(SID)
        jd.record_verdict(a, a["nodes"][gid], "nudge", "block", T0 + 100, why="owed")
        jd.record_verdict(b, b["nodes"][gid], "nudge", "block", T0 + 100, why="owed")
        jd.save_goals(SID, a)
        jd.save_goals(SID, b)
        log = jd.load_goals(SID)["nodes"][gid].get("log") or []
        blocks = [e for e in log if e.get("kind") == "block" and int(e.get("ev_t") or 0) == T0 + 100]
        self.assertEqual(len(blocks), 1, "the same verdict from both writers folds to one entry")

    def test_an_uncontended_save_does_not_rebase(self):
        self._seed()
        gid = self._nid(1)
        s = jd.load_goals(SID)
        jd.record_verdict(s, s["nodes"][gid], "planner", "done", T0 + 30, why="shipped")
        jd.save_goals(SID, s)                        # nobody else wrote → straight publish
        self.assertTrue(jd.load_goals(SID)["nodes"][gid].get("nodeComplete"))


if __name__ == "__main__":
    unittest.main()
