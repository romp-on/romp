#!/usr/bin/env python3
"""Board dedup (the user 2026-07-11): the same work used to land on the board several times — the
opener, the planner work-run, and the to-do mirror share no dedup, and the grouper could only nest,
never fuse (the ui-session audit: one chat-overflow fix wore three sibling subs, one of them done
while another was blocked). Three mechanisms close it:
  - the grouper's `merge` op (_merge_nodes / apply_group over a _group_menu that numbers steps too),
  - planner-prompt rules (mirror lines annotated in _menu_text; no to-do bookkeeping subs; a step the
    same segment finished pairs with a done ref; the anti-restate rule in the base prompt),
  - the opener's queued-fragment `extend` (a rapid-fire message with no work since the previous one
    may land ON that message's node instead of minting a sibling sub) — judge-side only, read from
    the transcript after delivery, so cancelling a queued message needs no special case.
All fixtures SYNTHETIC."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = SourceFileLoader("romp_event_model_dedup", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge_dedup", os.path.join(BIN, "romp-judge")).load_module()

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "user", "content": text}}
    if ps is not None:
        r["promptSource"] = ps
    return r


def aline(t, text, uuid, parent=None, tools=(), stop="end_turn"):
    content = [{"type": "text", "text": text}] if text else []
    for i, n in enumerate(tools):
        content.append({"type": "tool_use", "id": "tu_%s_%d" % (uuid, i), "name": n, "input": {}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def build_session(records, now=NOW, rompuuid=SID):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (rompuuid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return em.parse_session(str(p), rompuuid=rompuuid, candidate_files=[str(p)], now=now)


def gid(n):
    return "%s:g%d" % (SID, n)


def node(nid, text, parent=None, t=T0, done=False, **kw):
    nd = {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
          "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t}
    nd.update(kw)
    return nd


def store(*nodes):
    return {"rompUuid": SID, "seq": len(nodes), "placementsV": jd.PLACEMENTS_V, "placements": {},
            "status": {}, "nodes": {nd["id"]: nd for nd in nodes}}


def audit_store():
    """The ui-session audit shape: a card with two user-ask steps, plus a to-do-mirror TOP that
    straddles the same fix (minted flat by plan-sync, for the grouper to place)."""
    return store(
        node(gid(1), "Fix mobile UI bugs", t=100, trail=["s0"]),
        node(gid(2), "Fix chat pane too wide", parent=gid(1), t=200, trail=["s1"],
             quote="its like 20 percent to wide", promptUuid="pu-2"),
        node(gid(3), "Address chat pane too tall", parent=gid(1), t=300, trail=["s2"]),
        node(gid(4), "Chat pane overflows viewport (wide + tall)", t=400, trail=["s2"],
             agentTask={"key": "9", "status": "open", "raw": "pending"}, agentBornOpen=True),
    )


class GroupMenu(unittest.TestCase):
    """_group_menu numbers tops AND their open steps in one index space; _group_menu_text renders the
    indented tree with the to-do-mirror annotation on any line (steps included)."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._state_td = tempfile.mkdtemp()
        jd.STATE = Path(self._state_td)

    def tearDown(self):
        jd.STATE = self._saved_state
        shutil.rmtree(self._state_td, ignore_errors=True)

    def test_menu_numbers_steps_and_marks_mirrors(self):
        s = audit_store()
        tops = jd._group_tops(s)
        menu = jd._group_menu(s, tops)
        self.assertEqual([nd["id"] for nd in menu], [gid(1), gid(2), gid(3), gid(4)],
                         "each top followed by its open steps, one shared index space")
        lines = jd._group_menu_text(s, menu).split("\n")
        self.assertEqual(lines[0], "1. Fix mobile UI bugs")
        self.assertEqual(lines[1], "    2. Fix chat pane too wide", "steps are indented AND numbered")
        self.assertTrue(lines[3].startswith("4. "), "the mirror top is flush-left")
        self.assertIn("· from the agent's own to-do list", lines[3])

    def test_completed_and_cleared_steps_stay_off_the_menu(self):
        s = store(node(gid(1), "Card", t=100),
                  node(gid(2), "done step", parent=gid(1), t=200, done=True),
                  node(gid(3), "cleared step", parent=gid(1), t=300, cleared=True),
                  node(gid(4), "live step", parent=gid(1), t=400),
                  node(gid(5), "Other card", t=500))
        menu = jd._group_menu(s, jd._group_tops(s))
        self.assertEqual([nd["id"] for nd in menu], [gid(1), gid(4), gid(5)])

    def test_menu_caps_at_the_newest_six_steps(self):
        nodes = [node(gid(1), "Card", t=100)]
        nodes += [node(gid(2 + i), "step %d" % i, parent=gid(1), t=200 + i) for i in range(8)]
        s = store(*nodes)
        menu = jd._group_menu(s, jd._group_tops(s))
        texts = [nd["text"] for nd in menu]
        self.assertNotIn("step 0", texts, "oldest steps fall off the cap")
        self.assertIn("step 7", texts, "the newest step stays: a twin is usually recent")
        self.assertEqual(texts[1:], ["step %d" % i for i in range(2, 8)], "chronological order kept")


class MergeOp(unittest.TestCase):
    """The grouper's merge op: fold a semantic twin into the node that already tracks the work."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._state_td = tempfile.mkdtemp()
        jd.STATE = Path(self._state_td)

    def tearDown(self):
        jd.STATE = self._saved_state
        shutil.rmtree(self._state_td, ignore_errors=True)

    # ── parse ──
    def test_parse_merge_and_guards(self):
        self.assertEqual(jd._parse_group('{"ops":[{"why":"x","do":"merge","goal":4,"into":2}]}', 4),
                         [{"do": "merge", "why": "x", "goal": 4, "into": 2}])
        self.assertEqual(jd._parse_group('{"ops":[{"why":"x","do":"merge","goal":2,"into":2}]}', 4), [],
                         "self-merge is dropped")
        self.assertEqual(jd._parse_group('{"ops":[{"why":"x","do":"merge","goal":9,"into":1}]}', 4), [],
                         "out-of-range target is dropped")
        self.assertEqual(jd._parse_group('{"ops":[{"why":"x","do":"merge","goal":1}]}', 4), [],
                         "merge without into is dropped")

    def test_prompt_carries_the_merge_op(self):
        self.assertIn('"do":"merge"', jd.GROUP_SYS)
        self.assertIn("true twins", jd.GROUP_SYS)
        self.assertIn("never merge two lines that are both from the agent's own to-do list",
                      jd.GROUP_SYS.replace("\n", " "))

    # ── apply ──
    def test_merge_folds_the_mirror_into_the_users_step(self):
        # the audit shape: the to-do mirror (top #4) duplicates the card's own steps → fold it into
        # the step carrying the user's ask. The mirror's to-do link moves, so the agent crossing it
        # off completes the survivor from now on.
        s = audit_store()
        s["placements"] = {"s2": gid(4)}
        s["lastNode"] = gid(4)
        s["status"][gid(4)] = "working"
        menu = jd._group_menu(s, jd._group_tops(s))            # [card, wide, tall, mirror]
        n = jd.apply_group(s, menu, [{"do": "merge", "why": "same fix twice", "goal": 4, "into": 2}], T0 + 50)
        self.assertEqual(n, 1)
        self.assertNotIn(gid(4), s["nodes"], "the twin left the board")
        self.assertNotIn(gid(4), s["status"], "its status entry is gone too")
        surv = s["nodes"][gid(2)]
        self.assertEqual(surv["agentTask"], {"key": "9", "status": "open", "raw": "pending"},
                         "the live to-do link moved to the keeper")
        self.assertTrue(surv["agentBornOpen"])
        self.assertIn("s2", surv["trail"], "the twin's evidence segment rides the survivor's trail")
        self.assertEqual(surv["trail"][0], "s1", "the survivor's own anchor stays first")
        self.assertEqual(s["placements"]["s2"], gid(2), "placements re-point at the survivor")
        self.assertEqual(s["lastNode"], gid(2), "lastNode re-points too")
        self.assertEqual(surv["mergedFrom"][0]["id"], gid(4), "provenance is kept on the survivor")
        self.assertEqual(surv["quote"], "its like 20 percent to wide", "the survivor keeps its own ask")

    def test_merge_keeps_survivor_title_and_adopts_children(self):
        s = store(node(gid(1), "Keeper", t=100),
                  node(gid(2), "Twin", t=200),
                  node(gid(3), "twin's step", parent=gid(2), t=300))
        menu = jd._group_menu(s, jd._group_tops(s))
        jd.apply_group(s, menu, [{"do": "merge", "why": "x",
                                  "goal": next(i for i, nd in enumerate(menu, 1) if nd["id"] == gid(2)),
                                  "into": next(i for i, nd in enumerate(menu, 1) if nd["id"] == gid(1))}], T0 + 50)
        self.assertEqual(s["nodes"][gid(1)]["text"], "Keeper")
        self.assertEqual(s["nodes"][gid(3)]["parentId"], gid(1), "the twin's children move to the survivor")

    def test_merge_refuses_two_mirrors(self):
        s = store(node(gid(1), "todo one", t=100, agentTask={"key": "1", "status": "open", "raw": "pending"}),
                  node(gid(2), "todo two", t=200, agentTask={"key": "2", "status": "open", "raw": "pending"}))
        menu = jd._group_menu(s, jd._group_tops(s))
        n = jd.apply_group(s, menu, [{"do": "merge", "why": "x", "goal": 2, "into": 1}], T0 + 50)
        self.assertEqual(n, 0, "two distinct to-do items are never one goal")
        self.assertIn(gid(1), s["nodes"])
        self.assertIn(gid(2), s["nodes"])

    def test_merge_into_own_descendant_splices_without_cycle(self):
        # fold a card into its own step: the step takes the card's place, siblings move under it
        s = store(node(gid(1), "Card restating its step", t=100),
                  node(gid(2), "the real step", parent=gid(1), t=200),
                  node(gid(3), "sibling step", parent=gid(1), t=300))
        menu = jd._group_menu(s, jd._group_tops(s))
        n = jd.apply_group(s, menu, [{"do": "merge", "why": "x", "goal": 1, "into": 2}], T0 + 50)
        self.assertEqual(n, 1)
        self.assertNotIn(gid(1), s["nodes"])
        self.assertIsNone(s["nodes"][gid(2)]["parentId"], "the survivor took the merged card's place")
        self.assertEqual(s["nodes"][gid(3)]["parentId"], gid(2), "siblings moved under the survivor")
        self.assertEqual(jd._top_ancestor(s["nodes"], gid(3)), gid(2), "no cycle: the walk terminates")

    def test_group_ops_apply_nothing_anywhere(self):
        # T101: the group op is retired outright — steps stay put and tops stay tops
        s = audit_store()
        menu = jd._group_menu(s, jd._group_tops(s))            # [card, wide(step), tall(step), mirror(top)]
        n = jd.apply_group(s, menu, [{"do": "group", "why": "x", "goal": 2, "under": 4}], T0 + 50)
        self.assertEqual(n, 0)
        self.assertEqual(s["nodes"][gid(2)]["parentId"], gid(1), "the step stayed put")
        n = jd.apply_group(s, menu, [{"do": "group", "why": "x", "goal": 4, "under": 3}], T0 + 60)
        self.assertEqual(n, 0, "no walk-up either — nothing nests")
        self.assertIsNone(s["nodes"][gid(4)].get("parentId"), "the mirror top stays its own card")

    def test_group_op_on_a_merged_away_target_is_skipped(self):
        s = audit_store()
        menu = jd._group_menu(s, jd._group_tops(s))
        n = jd.apply_group(s, menu, [{"do": "merge", "why": "x", "goal": 4, "into": 2},
                                     {"do": "group", "why": "x", "goal": 4, "under": 1}], T0 + 50)
        self.assertEqual(n, 1, "the merge applied; the group on the now-gone node skipped")

    def test_group_store_applies_a_merge_end_to_end(self):
        s = audit_store()
        saved = jd.group_llm
        try:
            jd.group_llm = lambda menu, **k: '{"ops":[{"why":"same fix","do":"merge","goal":4,"into":2}]}'
            n = jd._group_store(s, SID, T0 + 100)
            self.assertEqual(n, 1)
            self.assertNotIn(gid(4), s["nodes"])
            self.assertEqual(s["nodes"][gid(2)]["agentTask"]["key"], "9")
        finally:
            jd.group_llm = saved


class PlannerPromptRules(unittest.TestCase):
    """The three PLAN_SYS additions and the planner-menu mirror annotation."""

    def test_menu_text_marks_todo_mirrors(self):
        s = store(node(gid(1), "Card", t=100),
                  node(gid(2), "mirror step", parent=gid(1), t=200,
                       agentTask={"key": "3", "status": "open", "raw": "pending"}, agentBornOpen=True))
        lines = jd._menu_text(s, jd.open_menu(s)).split("\n")
        self.assertNotIn("to-do list", lines[0], "a plain goal wears no annotation")
        self.assertIn("· from the agent's own to-do list", lines[1])

    def test_plan_sys_bans_todo_bookkeeping_subs(self):
        flat = jd.PLAN_SYS.replace("\n", " ")
        self.assertIn('marked "from the agent\'s own to-do list"', flat)
        self.assertIn("never file a sub that records the agent creating, updating, or checking off "
                      "its to-do items", flat)
        self.assertIn("that bookkeeping is already on the board", flat)

    def test_plan_sys_pairs_finished_steps_with_done(self):
        flat = jd.PLAN_SYS.replace("\n", " ")
        self.assertIn("pair its sub with a done on it in this same reply", flat)

    def test_plan_sys_anti_restate_is_unconditional(self):
        # the rule used to ride only the known-continuation <note> (goal_num calls); the ui audit's
        # g640 restated its card from a plain segment, so the base prompt now carries it
        flat = jd.PLAN_SYS.replace("\n", " ")
        self.assertIn("Never file a sub that merely restates card #n's own title or ask", flat)


class OpenerExtend(unittest.TestCase):
    """The queued-fragment path: detection (_queued_sibling), the gated extend op, and its landing."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._state_td = tempfile.mkdtemp()
        jd.STATE = Path(self._state_td)

    def tearDown(self):
        jd.STATE = self._saved_state
        shutil.rmtree(self._state_td, ignore_errors=True)
        if hasattr(self, "_saved"):
            (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.plan_llm, jd.opener_llm, jd.group_llm) = self._saved
            jd._PARSE_CACHE.clear()

    # ── parse gating ──
    def test_extend_parses_only_when_offered(self):
        raw = '{"ops":[{"why":"same ask","do":"extend","goal":2}]}'
        self.assertIsNone(jd._parse_plan(raw, 3), "extend is dropped when the note never offered it")
        self.assertEqual(jd._parse_plan(raw, 3, allow_extend=True),
                         [{"do": "extend", "why": "same ask", "goal": 2}])
        self.assertIsNone(jd._parse_plan('{"ops":[{"why":"x","do":"extend","goal":9}]}', 3,
                                         allow_extend=True), "out-of-range extend is dropped")

    def test_opener_note_rides_only_with_a_sibling(self):
        import unittest.mock as mock
        with mock.patch.object(jd, "_judge_run", return_value="{}") as m:
            jd.opener_llm("msg", "menu", sibling_num=3)
            self.assertIn('"do": "extend"', m.call_args.args[2])
            self.assertIn("#3", m.call_args.args[2])
            jd.opener_llm("msg", "menu")
            self.assertNotIn("extend", m.call_args.args[2])

    # ── landing ──
    def test_apply_plan_extend_lands_on_the_node_without_minting(self):
        s = store(node(gid(1), "Card", t=100),
                  node(gid(2), "fix chat width", parent=gid(1), t=200, trail=["s1"]))
        menu = jd.open_menu(s)
        num = next(i for i, nd in enumerate(menu, 1) if nd["id"] == gid(2))
        jd.apply_plan(s, "s2", T0 + 10, [{"do": "extend", "why": "same ask", "goal": num}], menu,
                      place_key="s2#p")
        self.assertEqual(len(s["nodes"]), 2, "nothing new minted")
        self.assertEqual(s["placements"]["s2#p"], gid(2), "the fragment's phase points at the node")
        self.assertIn("s2", s["nodes"][gid(2)]["trail"], "the fragment is evidence on the node")

    def test_extend_lifts_a_block_like_new_user_input(self):
        s = store()
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Card"}], [])
        menu = jd.open_menu(s)
        jd.apply_plan(s, "s2", T0 + 10, [{"do": "block", "why": "which option?", "goal": 1}], menu)
        top = s["placements"]["s1"]
        self.assertTrue(s["nodes"][top]["blocked"])
        jd.apply_plan(s, "s3", T0 + 20, [{"do": "extend", "why": "the answer", "goal": 1}], menu,
                      place_key="s3#p")
        self.assertFalse(s["nodes"][top]["blocked"], "a fragment on the thread answers the block")

    # ── detection ──
    def _seg_index(self, records, st):
        sess = build_session(records)
        return {seg["id"]: seg for turn in sess["turns"] for seg in jd._segs(turn, st)}

    def test_queued_sibling_found_when_prior_message_saw_no_work(self):
        st = store(node(gid(1), "Fix chat width", t=100))
        records = [uline(T0 + 100, "chat pane is too wide", "u1", ps="typed"),
                   uline(T0 + 101, "slightly too tall as well", "u2", "u1", ps="typed"),
                   aline(T0 + 110, "on it", "a2", "u2", stop=None)]
        seg_by_id = self._seg_index(records, st)
        ids = list(seg_by_id)
        self.assertEqual(len(ids), 2, "two human triggers → two segments")
        st["placements"][ids[0]] = gid(1)
        self.assertEqual(jd._queued_sibling(st, seg_by_id, ids[1]), gid(1))
        # the #p phase placement works too (whichever run landed first)
        st["placements"] = {ids[0] + "#p": gid(1)}
        self.assertEqual(jd._queued_sibling(st, seg_by_id, ids[1]), gid(1))

    def test_no_sibling_when_work_landed_between_the_messages(self):
        st = store(node(gid(1), "Fix chat width", t=100))
        records = [uline(T0 + 100, "chat pane is too wide", "u1", ps="typed"),
                   aline(T0 + 105, "probing the layout", "a1", "u1", tools=("Bash",), stop=None),
                   uline(T0 + 200, "slightly too tall as well", "u2", "a1", ps="typed"),
                   aline(T0 + 210, "on it", "a2", "u2", stop=None)]
        seg_by_id = self._seg_index(records, st)
        ids = list(seg_by_id)
        st["placements"][ids[0]] = gid(1)
        self.assertIsNone(jd._queued_sibling(st, seg_by_id, ids[1]),
                          "work between the messages → separate follow-up, no extend offer")

    def test_no_sibling_for_the_first_message_or_a_missing_placement(self):
        st = store(node(gid(1), "Card", t=100))
        records = [uline(T0 + 100, "first ask", "u1", ps="typed"),
                   uline(T0 + 101, "second ask", "u2", "u1", ps="typed"),
                   aline(T0 + 110, "on it", "a2", "u2", stop=None)]
        seg_by_id = self._seg_index(records, st)
        ids = list(seg_by_id)
        self.assertIsNone(jd._queued_sibling(st, seg_by_id, ids[0]), "no prior segment")
        self.assertIsNone(jd._queued_sibling(st, seg_by_id, ids[1]),
                          "prior message not placed yet → nothing to extend")

    # ── end to end: the prompt-run offers extend and the fragment lands on the sibling's node ──
    def test_prompt_run_extends_instead_of_minting_a_sibling_sub(self):
        records = [uline(T0 + 100, "the chat pane is too wide on mobile", "u1", ps="typed"),
                   uline(T0 + 101, "slightly too tall as well", "u2", "u1", ps="typed"),
                   aline(T0 + 110, "on it", "a2", "u2", stop=None)]
        td = Path(tempfile.mkdtemp())
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        (pdir / (SID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in records) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self._saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.plan_llm, jd.opener_llm, jd.group_llm)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR = names, proj, td / "goals"
        jd._PARSE_CACHE.clear()
        offered = []
        jd.plan_llm = (lambda text, menu, **kw:
                       '{"ops":[{"why":"x","do":"mint","text":"Fix mobile chat width"}]}')

        # Since 2026-07-25 (the workless-ended-segment guard) the reply-less FIRST fragment places
        # via its own mint-only PROMPT-run instead of a full work-run (a work-run on a segment with no
        # assistant output is the judge-fabrication hole) — so the opener now fields BOTH fragments:
        # the first with no sibling (mints), the second offered the first's node (extends).
        def fake_opener(text, menu, sibling_num=None, **kw):
            offered.append(sibling_num)
            if sibling_num:
                return '{"ops":[{"why":"same ask","do":"extend","goal":%d}]}' % sibling_num
            return '{"ops":[{"why":"new ask","do":"mint","text":"Fix mobile chat width"}]}'
        jd.opener_llm = fake_opener
        jd.group_llm = lambda menu, **k: '{"ops":[]}'
        jd.run_plan(now=T0 + 5000)
        st = jd.load_goals(SID)
        self.assertEqual(offered, [None, 1],
                         "first fragment mints (no sibling); the second is offered the sibling's number")
        self.assertEqual(len(st["nodes"]), 1, "the fragment minted nothing beyond the one node")
        top = next(iter(st["nodes"]))
        pkeys = [k for k in st["placements"] if k.endswith("#p")]
        self.assertEqual(len(pkeys), 2, "each fragment's prompt-run placed once")
        for k in pkeys:
            self.assertEqual(st["placements"][k], top, "both fragments landed on the one node")
        self.assertEqual(len(st["nodes"][top].get("trail") or []), 2,
                         "both segments are evidence on the one node")


if __name__ == "__main__":
    unittest.main()
