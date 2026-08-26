#!/usr/bin/env python3
"""An interrupt pause must not make a finished goal uncompletable (2026-08-26, the
Completed→Working sighting): a goal whose work finished while the session sat interrupt-blocked
was sealed out of EVERY closer completion channel — the nomination gates skip blocked nodes, and
an interrupt block only lifts on the user's re-engagement — so it rested in Needs-You looking
done, then bounced to Working on every user touch instead of resting at Completed. The fix is at
the gates: a node whose ONLY standing block is src='interrupt' (romp's own stop-bookkeeping, not
a question owed to the user) rides the steps-finished and status-report nominations; an
ask-shaped block (planner/nudge/closer) keeps the seal exactly — blocked stays the unblocker's.
A done ruled from that nomination completes the card through the ordinary evidence-ordered fold.
SYNTHETIC fixtures only; private synthetic sids."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_intrpause", os.path.join(BIN, "romp-judge")).load_module()

T = 1_787_400_000
SID = "e22f0001-1111-4222-8333-000000000001"   # private synthetic sid — never the shared placeholder
G = SID + ":g1"
S1 = SID + ":g2"


def _store():
    st = {"rompUuid": SID, "seq": 2, "nodes": {}, "placements": {}, "status": {}}
    st["nodes"][G] = jd.GuardedNode({"id": G, "text": "Build the staggered grid", "parentId": None,
                                     "nodeComplete": False, "blocked": False, "cleared": False,
                                     "trail": [], "t": T, "mt": T, "log": []})
    st["nodes"][S1] = jd.GuardedNode({"id": S1, "text": "suite green, report sent", "parentId": G,
                                      "nodeComplete": False, "blocked": False, "cleared": False,
                                      "trail": [], "t": T, "mt": T, "log": []})
    jd.record_verdict(st, st["nodes"][S1], "closer", "done", T + 100, why="all lanes shipped")
    jd._mark_node_done(st, S1, "all lanes shipped", T + 100)
    return st


class IntrPausedOnly(unittest.TestCase):
    def test_interrupt_block_reads_paused(self):
        st = _store()
        jd.record_verdict(st, st["nodes"][G], "interrupt", "block", T + 200,
                          why="you stopped this session mid-turn")
        self.assertTrue(jd._intr_paused_only(st["nodes"][G]))

    def test_ask_block_does_not(self):
        st = _store()
        jd.record_verdict(st, st["nodes"][G], "planner", "block", T + 200, why="pick A or B")
        self.assertFalse(jd._intr_paused_only(st["nodes"][G]))

    def test_interrupt_then_ask_block_seals(self):
        st = _store()
        jd.record_verdict(st, st["nodes"][G], "interrupt", "block", T + 200, why="stopped")
        jd.record_verdict(st, st["nodes"][G], "user", "unblock", T + 300, why="re-engaged")
        jd.record_verdict(st, st["nodes"][G], "nudge", "block", T + 400, why="needs your direction")
        self.assertFalse(jd._intr_paused_only(st["nodes"][G]),
                         "the newest standing block is ask-shaped — the seal holds")

    def test_unblocked_is_not_paused(self):
        st = _store()
        self.assertFalse(jd._intr_paused_only(st["nodes"][G]))


class NominationGates(unittest.TestCase):
    def test_an_interrupt_paused_finished_goal_nominates(self):
        st = _store()
        jd.record_verdict(st, st["nodes"][G], "interrupt", "block", T + 200, why="stopped")
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertIn(G, cands,
                      "a pause is not a question — the finished work must be rulable")

    def test_an_ask_blocked_goal_stays_sealed(self):
        st = _store()
        jd.record_verdict(st, st["nodes"][G], "planner", "block", T + 200, why="pick A or B")
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertNotIn(G, cands, "blocked stays the unblocker's — byte-identical")

    def test_a_done_after_the_pause_completes_through_the_fold(self):
        st = _store()
        jd.record_verdict(st, st["nodes"][G], "interrupt", "block", T + 200, why="stopped")
        ok = jd.record_verdict(st, st["nodes"][G], "closer", "done", T + 500,
                               why="the grid shipped; report sent")
        self.assertTrue(ok)
        jd._mark_node_done(st, G, "the grid shipped; report sent", T + 500)
        jd.rollup_status(st, False)
        self.assertTrue(st["nodes"][G].get("nodeComplete"),
                        "a completion whose evidence postdates the pause rests at Completed")
        self.assertNotEqual(st["status"].get(G), "blocked")


if __name__ == "__main__":
    unittest.main()
