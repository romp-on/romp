#!/usr/bin/env python3
"""A delegation's completion lands on the ask-goal it fulfills (the user 2026-08-24, the
resurfaced-ask specimen): a user ask sat OPEN with zero verdict rows while the work it named was
delegated, merged, and reported — because the courier linked the dispatch to a LATER restatement of
the same ask on another card, so run_propagate's completion had no path back and the original card
resurfaced three times demanding status on finished work.

Two strengthenings of the join, precision-first (a wrong link closes the wrong card — worse than
none): the courier's prompt now says to pick the OLDEST open goal when several ask for the same work
(completion must land on the original) and to prefer 0 over a merely-adjacent guess; and the LINK
menu's cap doubles (open_menu is oldest-first, so a busy sender's default cap starved exactly the
candidates a dispatch serves). The mechanical chain below the link is pinned end to end: a tracking
node planted under a NESTED ask completes via run_propagate, and the ask becomes a closer
subtree-done nomination — a real ruling, never done-by-association. Where no confident link exists,
the parked cue covers top-level asks (sibling-scoped by the measured design); a nested ask under a
different umbrella cannot see cross-top dispatch traffic — the join IS the fix for that shape,
documented here. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from inspect import getsource

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge_asklink", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_asklink", os.path.join(BIN, "romp-kernel")).load_module()

SENDER = "11111111-2222-3333-4444-aaaaaaaaaaaa"
RECIP = "55555555-6666-7777-8888-bbbbbbbbbbbb"
MID = "msg-t11-0001"
T = 1_787_000_000                              # in the PAST vs wall clock: filings stamp at=now, and the
#                                               nomination gate compares them against the mint
U, X, H = SENDER + ":g1", SENDER + ":g2", None   # umbrella, the nested ask; H planted by the test


def _node(nid, text, parent, t=T, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return jd.GuardedNode(base)


class CourierJoinRules(unittest.TestCase):
    def test_the_prompt_lands_on_the_original_ask_and_never_guesses(self):
        self.assertIn("pick the OLDEST", jd.COURIER_SYS,
                      "several goals asking for one work → the original, or it resurfaces")
        self.assertIn("a wrong "
                      "link closes the wrong card, which is worse than none", jd.COURIER_SYS)

    def test_the_link_menu_covers_a_busy_senders_recent_asks(self):
        self.assertIn("open_menu(sender_store, cap=40)", getsource(jd.run_courier),
                      "oldest-first cap-20 starved the very candidates a dispatch serves")


class AskLinkChain(unittest.TestCase):
    """The mechanical chain below the link, end to end on a NESTED ask."""

    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (RECIP, "/dev/null", None, "api")]

    def tearDown(self):
        jd.discover = self._saved
        for f in jd.GOALDIR.glob("*"):
            f.unlink()

    def test_linked_nested_ask_completes_via_propagate_then_nomination(self):
        st = jd.load_goals(SENDER)
        st["nodes"][U] = _node(U, "Improve the notes app timeline", None)
        st["nodes"][X] = _node(X, "Highlight notes green in flight, amber when done", U, t=T + 10)
        st["seq"] = 2                                 # g1/g2 exist above; the plant mints g3, never a collision
        # the courier links the dispatch to the ask it fulfills: the tracking node plants UNDER it
        hid = jd._plant_handoff_track(st, X, "highlight states in the timeline", RECIP, "api",
                                      T + 20, MID)
        jd.save_goals(SENDER, st)
        rt = jd.load_goals(RECIP)
        rt["nodes"][RECIP + ":g5"] = _node(RECIP + ":g5", "Highlight states shipped", None,
                                           origin={"peer": SENDER, "goalId": hid, "msgId": MID})
        jd.record_verdict(rt, rt["nodes"][RECIP + ":g5"], "closer", "done", T + 100)
        jd.save_goals(RECIP, rt)
        self.assertEqual(jd.run_propagate(now=T + 200), 1, "the completion had a path back")
        st = jd.load_goals(SENDER)
        self.assertTrue(st["nodes"][hid].get("nodeComplete"), "the handed-off piece checked off")
        self.assertFalse(st["nodes"][X].get("nodeComplete"),
                         "the ask itself is NOT closed by association…")
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertIn(X, cands,
                      "…it is NOMINATED: every child done → the closer rules it with real evidence")

    def test_an_unlinked_dispatch_leaves_the_nested_ask_dark_the_join_is_the_fix(self):
        # the specimen's shape, pinned as the KNOWN failure mode: link_id landed elsewhere (here:
        # nothing, a top-level plant), so the nested ask sees no sibling handoff edge — the parked
        # cue is sibling-scoped by the measured design (cross-board counting was the noisy variant)
        # and top-level traffic is invisible under the umbrella. The ask gains no verdict path and
        # no cue: exactly why the LINK is where this class gets fixed.
        st = jd.load_goals(SENDER)
        st["nodes"][U] = _node(U, "Improve the notes app timeline", None)
        st["nodes"][X] = _node(X, "Highlight notes green in flight, amber when done", U, t=T + 10)
        st["seq"] = 2
        jd._plant_handoff_track(st, None, "highlight states in the timeline", RECIP, "api",
                                T + 20, MID)                     # top-level: the miss
        jd.save_goals(SENDER, st)
        nodes = jd.load_goals(SENDER)["nodes"]
        children = {}
        for nid, nd in nodes.items():
            children.setdefault(nd.get("parentId"), []).append(nid)
        self.assertNotIn(X, km._parked_rows(nodes, children),
                         "documented scope: no sibling edge → no cue; the courier link is the cover")


if __name__ == "__main__":
    unittest.main()
