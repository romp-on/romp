#!/usr/bin/env python3
"""Card-first filing (the user 2026-07-08): the planner picks the CARD, not a leaf from a flat list —
open_menu returns tree (DFS) order, _menu_text renders indentation, _card_route_subs walks a deep sub
target up to its card and asks the scoped placer (place_llm) only when the card actually has open
sub-goals, with any placer failure attaching at the card; _coerce_place floors onto the newest CARD.
All fixtures SYNTHETIC."""
import os
import shutil
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_cardfirst", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000


def node(nid, text, parent=None, t=NOW - 600, done=False, **kw):
    nd = {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
          "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t}
    nd.update(kw)
    return nd


def store(*nodes):
    return {"rompUuid": SID, "seq": len(nodes), "placements": {}, "status": {},
            "nodes": {nd["id"]: nd for nd in nodes}}


def gid(n):
    return "%s:g%d" % (SID, n)


class MenuTreeOrder(unittest.TestCase):
    """open_menu groups each card's open subtree under it depth-first (cards oldest-first), so the
    planner sees structure instead of a flat time-ordered list."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_dfs_groups_children_under_their_card(self):
        st = store(node(gid(1), "Card A", t=100),
                   node(gid(2), "Card B", t=200),
                   node(gid(3), "step under A", parent=gid(1), t=300),
                   node(gid(4), "sub-step of A's step", parent=gid(3), t=400))
        menu = jd.open_menu(st)
        self.assertEqual([nd["id"] for nd in menu], [gid(1), gid(3), gid(4), gid(2)],
                         "A's whole open subtree rides under A; B follows as the next card")

    def test_menu_text_indents_by_depth(self):
        st = store(node(gid(1), "Card A", t=100),
                   node(gid(2), "step", parent=gid(1), t=200),
                   node(gid(3), "deeper", parent=gid(2), t=300))
        lines = jd._menu_text(st, jd.open_menu(st)).split("\n")
        self.assertEqual(lines[0], "1. Card A")
        self.assertEqual(lines[1], "    2. step")
        self.assertEqual(lines[2], "        3. deeper")

    def test_menu_text_anchors_an_orphan_to_its_card_in_words(self):
        # a scoped list can hold a sub-goal whose card is not on it (e.g. the nudge/delegation
        # subset menus): it renders flush-left but names the card it lives inside
        st = store(node(gid(1), "Card A", t=100),
                   node(gid(2), "buried step", parent=gid(1), t=200))
        text = jd._menu_text(st, [st["nodes"][gid(2)]])
        self.assertEqual(text, "1. buried step  (inside: Card A)")


class CardRouting(unittest.TestCase):
    """_card_route_subs: subs route to the card; the placer runs only when the card has open
    sub-goals; every failure mode lands at the card."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _tree(self):
        return store(node(gid(1), "Card A", t=100),
                     node(gid(2), "step", parent=gid(1), t=200),
                     node(gid(3), "deeper", parent=gid(2), t=300),
                     node(gid(4), "Card B", t=400))

    def test_bare_card_sub_makes_no_placer_call(self):
        st = store(node(gid(1), "Card A", t=100), node(gid(2), "Card B", t=200))
        menu = jd.open_menu(st)
        jd.place_llm = lambda *a, **k: self.fail("placer must not be called for a card with no open sub-goals")
        ops = jd._card_route_subs(st, [{"do": "sub", "under": 2, "text": "x", "why": "w"}], menu)
        self.assertEqual(ops[0]["under"], 2, "a bare card is its own spot; one call total")

    def test_deep_target_walks_up_and_placer_picks_the_spot(self):
        st = self._tree()
        menu = jd.open_menu(st)                       # DFS: A, step, deeper, B
        calls = []
        jd.place_llm = lambda text, why, card_menu, **k: calls.append(card_menu) or '{"under": 2}'
        ops = jd._card_route_subs(st, [{"do": "sub", "under": 3, "text": "x", "why": "w"}], menu)
        self.assertEqual(ops[0]["under"], 2, "the placer's pick (#2 = step) re-points the op")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("1. Card A"), "the scoped tree leads with the card as #1")
        self.assertNotIn("Card B", calls[0], "the placer sees only the chosen card's subtree")

    def test_placer_failure_attaches_at_the_card(self):
        st = self._tree()
        menu = jd.open_menu(st)
        jd.place_llm = lambda *a, **k: "no json here"
        ops = jd._card_route_subs(st, [{"do": "sub", "under": 3, "text": "x", "why": "w"}], menu)
        self.assertEqual(ops[0]["under"], 1, "unusable placer reply → the card itself")

    def test_placer_out_of_range_attaches_at_the_card(self):
        st = self._tree()
        menu = jd.open_menu(st)
        jd.place_llm = lambda *a, **k: '{"under": 99}'
        ops = jd._card_route_subs(st, [{"do": "sub", "under": 2, "text": "x", "why": "w"}], menu)
        self.assertEqual(ops[0]["under"], 1)

    def test_placer_false_routes_to_card_with_no_call(self):
        st = self._tree()
        menu = jd.open_menu(st)
        jd.place_llm = lambda *a, **k: self.fail("prompt/live runs never make the second call")
        ops = jd._card_route_subs(st, [{"do": "sub", "under": 3, "text": "x", "why": "w"}],
                                  menu, placer=False)
        self.assertEqual(ops[0]["under"], 1)

    def test_non_sub_ops_and_ref_subs_pass_through(self):
        st = self._tree()
        menu = jd.open_menu(st)
        jd.place_llm = lambda *a, **k: self.fail("nothing here should consult the placer")
        ops = [{"do": "mint", "text": "new", "why": "w"},
               {"do": "sub", "ref": 1, "text": "x", "why": "w"},
               {"do": "done", "goal": 2, "why": "w"}]
        routed = jd._card_route_subs(st, [dict(o) for o in ops], menu)
        self.assertEqual(routed, ops)


class CoercePlaceCard(unittest.TestCase):
    """The never-lose-a-user-message floor files under the newest CARD, not the newest leaf."""

    def test_floor_targets_newest_card(self):
        st = store(node(gid(1), "Old card", t=50),
                   node(gid(2), "New card", t=100),
                   node(gid(3), "newest node is a leaf", parent=gid(2), t=200))
        menu = jd.open_menu(st)                       # DFS: Old, New, leaf
        ops = jd._coerce_place(menu, "USER ASKED: keep me")
        self.assertEqual(ops[0]["do"], "sub")
        self.assertEqual(menu[ops[0]["under"] - 1]["id"], gid(2),
                         "the floor lands on the newest card, never chained under a leaf")

    def test_empty_board_still_mints(self):
        self.assertEqual(jd._coerce_place([], "USER ASKED: hello")[0]["do"], "mint")

    def test_floor_skips_a_blocked_card(self):
        # the user 2026-07-21: an aside unrelated to the lone blocked card was coerced INTO it, and the
        # placement pulled the card back to working — a blocked card is not a landing spot for the floor
        st = store(node(gid(1), "Old unblocked card", t=50),
                   node(gid(2), "Newest card, awaiting the user", t=100, blocked=True))
        ops = jd._coerce_place(jd.open_menu(st), "USER ASKED: unrelated aside")
        self.assertEqual(ops[0]["do"], "sub")
        self.assertEqual(jd.open_menu(st)[ops[0]["under"] - 1]["id"], gid(1),
                         "the floor lands on the newest card NOT waiting on the user")

    def test_floor_skips_a_card_with_a_blocked_sub(self):
        # blocked rolls up: a card whose sub awaits the user reads needs-you on the board, so the
        # floor treats the whole card as blocked, not just the flagged node itself
        st = store(node(gid(1), "Old unblocked card", t=50),
                   node(gid(2), "Newest card", t=100),
                   node(gid(3), "sub awaiting the user", parent=gid(2), t=200, blocked=True))
        ops = jd._coerce_place(jd.open_menu(st), "USER ASKED: unrelated aside")
        self.assertEqual(jd.open_menu(st)[ops[0]["under"] - 1]["id"], gid(1))

    def test_every_card_blocked_mints_a_new_top(self):
        st = store(node(gid(1), "The one card, awaiting the user", t=100, blocked=True))
        ops = jd._coerce_place(jd.open_menu(st), "USER ASKED: unrelated aside")
        self.assertEqual(ops[0]["do"], "mint",
                         "a one-card board whose card is blocked gets a fresh top, never a graft")

    def test_coerced_ops_are_marked(self):
        st = store(node(gid(1), "A card", t=100))
        self.assertTrue(jd._coerce_place(jd.open_menu(st), "USER ASKED: x")[0].get("coerced"))
        self.assertTrue(jd._coerce_place([], "USER ASKED: x")[0].get("coerced"))


class CoercedPlacementLeavesBlocks(unittest.TestCase):
    """apply_plan's new-work-filed unblock is for placements the planner CHOSE. A coerced op is the
    never-vanish floor — bookkeeping, not the user re-engaging the branch — so blocks stand
    (the user 2026-07-21: an unrelated aside quietly retired the card's needs-you)."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _blocked_store(self):
        st = store(node(gid(1), "Card awaiting the user", t=100, blocked=True))
        st["nodes"][gid(1)]["log"] = [{"kind": "block", "src": "planner", "at": NOW - 500,
                                       "ev_t": NOW - 500, "why": "needs the user's pick"}]
        return st

    def test_coerced_sub_leaves_the_block_standing(self):
        st = self._blocked_store()
        ops = [{"do": "sub", "under": 1, "text": "unrelated aside", "why": "w", "coerced": True}]
        jd.apply_plan(st, "seg1", NOW, ops, jd.open_menu(st))
        self.assertTrue(st["nodes"][gid(1)]["blocked"],
                        "a coerced placement never clears the branch's blocks")

    def test_planner_chosen_sub_still_unblocks(self):
        st = self._blocked_store()
        ops = [{"do": "sub", "under": 1, "text": "the user's answer, filed as work", "why": "w"}]
        jd.apply_plan(st, "seg1", NOW, ops, jd.open_menu(st))
        self.assertFalse(st["nodes"][gid(1)]["blocked"],
                         "a deliberate placement keeps the newest-wins unblock")

    def test_coerced_twin_landing_leaves_the_block_standing(self):
        # the coerced label can dup an existing node under the card — the twin landing is still coerced
        st = self._blocked_store()
        st["nodes"][gid(2)] = node(gid(2), "unrelated aside", parent=gid(1), t=NOW - 400)
        ops = [{"do": "sub", "under": 1, "text": "unrelated aside", "why": "w", "coerced": True}]
        jd.apply_plan(st, "seg1", NOW, ops, jd.open_menu(st))
        self.assertTrue(st["nodes"][gid(1)]["blocked"])


class PlacePromptPins(unittest.TestCase):
    def test_depth_budget_lives_in_place_sys(self):
        self.assertIn("%d levels deep" % jd.MAX_DEPTH, jd.PLACE_SYS,
                      "the depth budget is embedded in the placer prompt, kept in sync with MAX_DEPTH")

    def test_plan_prompts_describe_the_tree_menu(self):
        for sys_prompt in (jd.PLAN_SYS, jd.OPENER_SYS):
            self.assertIn("top-level **card", sys_prompt,
                          "both planner runs file subs against top-level cards")

    def test_menu_marks_blocked_nodes_and_prompts_explain_it(self):
        # the annotation and the guidance travel together: the menu line says a card is waiting on the
        # user, and both filing prompts say what filing under such a card claims (the user 2026-07-21)
        st = store(node(gid(1), "Card awaiting the user", t=100, blocked=True))
        self.assertIn("· blocked: awaiting the user", jd._menu_text(st, jd.open_menu(st)))
        for sys_prompt in (jd.PLAN_SYS, jd.OPENER_SYS):
            self.assertIn("blocked: awaiting the user", sys_prompt)
            self.assertIn("only the user can answer", sys_prompt)


class EchoTwinGuard(unittest.TestCase):
    """apply_plan's echo/twin guard (the user 2026-07-08): a sub that exactly restates its parent's
    title lands ON the parent; a sub identical to an OPEN sibling reuses that sibling's node; a
    completed sibling never matches (a repeated step is new work, not a resurrection)."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _apply(self, st, ops, key):
        jd.apply_plan(st, key, NOW, ops, jd.open_menu(st), place_key=key)

    def test_parent_echo_lands_on_the_parent(self):
        st = store(node(gid(1), "Fix the rejudge bug", t=100))
        self._apply(st, [{"do": "sub", "under": 1, "text": "Fix the  REJUDGE bug!", "why": "w"}], "segA")
        self.assertEqual(len(st["nodes"]), 1, "a step restating its parent (any case/punctuation) mints nothing")
        self.assertEqual(st["placements"]["segA"], gid(1))
        self.assertIn("segA", st["nodes"][gid(1)]["trail"])

    def test_open_twin_sibling_is_reused(self):
        st = store(node(gid(1), "Card A", t=100),
                   node(gid(2), "Diagnose the bump", parent=gid(1), t=200))
        self._apply(st, [{"do": "sub", "under": 1, "text": "Diagnose the bump", "why": "w"}], "segB")
        self.assertEqual(len(st["nodes"]), 2, "no twin sibling minted")
        self.assertIn("segB", st["nodes"][gid(2)]["trail"], "the step lands as evidence on the existing node")

    def test_completed_sibling_is_not_resurrected(self):
        st = store(node(gid(1), "Card A", t=100),
                   node(gid(2), "run the tests", parent=gid(1), t=200, done=True))
        self._apply(st, [{"do": "sub", "under": 1, "text": "run the tests", "why": "w"}], "segC")
        self.assertEqual(len(st["nodes"]), 3, "a repeated step next to a FINISHED twin gets its own node")

    def test_distinct_title_still_mints(self):
        st = store(node(gid(1), "Card A", t=100))
        self._apply(st, [{"do": "sub", "under": 1, "text": "wire the tests", "why": "w"}], "segD")
        self.assertEqual(len(st["nodes"]), 2)


class PromptAndGrouperPins(unittest.TestCase):
    def test_goal_num_note_forbids_restating_the_ask(self):
        from unittest import mock
        with mock.patch.object(jd, "_judge_run", return_value="{}") as m:
            jd.plan_llm("seg", "menu", goal_num=2)
            note = m.call_args.args[2]
        self.assertIn("already recorded as #2", note)
        self.assertIn("never restate #2's own title", note)

    def test_grouper_marks_and_owns_todo_mirrors(self):
        self.assertIn("to-do mirror", jd.GROUP_SYS)
        # T101 (2026-08-26): nesting retired — a mirror that duplicates an existing line MERGES;
        # a distinct one stays its own card
        self.assertIn("merge it into that line", jd.GROUP_SYS)
        self.assertIn("otherwise leave it as its", jd.GROUP_SYS)
        st = store(node(gid(1), "Tests green, merge, push", t=100, agentTask={"key": "k1", "status": "open"}),
                   node(gid(2), "Build the widget", t=200))
        text = jd._group_menu_text(st, jd._group_tops(st))
        self.assertIn("Tests green, merge, push  · from the agent's own to-do list", text)
        self.assertNotIn("Build the widget  ·", text, "only to-do mirrors wear the mark")


if __name__ == "__main__":
    unittest.main()
