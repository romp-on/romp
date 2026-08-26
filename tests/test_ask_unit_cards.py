#!/usr/bin/env python3
"""T101 (the user 2026-08-26): the board's unit is the individual ASK — the planner's decomposition
of the dictated prompt, in the user's own phrasing, with a definite done. Rounds are never a
tracked unit and dispatches never multiply an ask into near-duplicate cards:
- a dispatch whose chain roots to an ask that ALREADY HAS A CARD (the courier's link resolved)
  LINKS — the tracking node plants under the ask, fan-out lives INSIDE the ask card, the recipient
  gets NO standalone top, and the tracker wears the quiet mark so the reply-sweep owns its ending;
- one ask fanned to two workers = ONE card with two handoff children; two asks to one worker =
  TWO cards; a rooted dispatch with NO resolvable ask still mints the recipient top (the fallback
  that keeps every user ask carded somewhere), frame intact;
- linking alone never moves the ask card's column (planting a tracking child writes no verdict);
- the grouper/consolidator lose the umbrella-mint and group-relink ops (merge/split/retitle stay),
  and legacy umbrellas DISSOLVE idempotently in every writer's rollup — children re-parent to top
  level with their provenance intact, placements at the container retire.
SYNTHETIC fixtures only; private synthetic sids; per-sid override journals cleaned in tearDown."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_askunit", os.path.join(BIN, "romp-judge")).load_module()

NOW = 1_787_500_000
T0 = NOW - 3600
MGR = "a23f0001-1111-4222-8333-000000000001"   # private synthetic sids — never the shared placeholder
WK1 = "a23f0001-1111-4222-8333-000000000002"
WK2 = "a23f0001-1111-4222-8333-000000000003"
MID1 = "1787499000.000001_1.TESTHOST"
MID2 = "1787499000.000002_1.TESTHOST"
BODY1 = ("DELEGATE: drag-range selection over run rows (user ask, dictated)\n"
         "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID1)
BODY2 = ("DELEGATE: drag-range selection, the table half (user ask, dictated)\n"
         "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID2)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _node(nid, text, parent, t=T0, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


class CourierWorld(unittest.TestCase):
    """run_courier end to end over a manager + two workers, the courier's link resolving to the
    manager's ask node (menu #1)."""

    def setUp(self):
        self._rooted = jd._delegate_user_rooted
        jd._delegate_user_rooted = lambda *a, **k: True   # rooting has its own suite; the LINK is under test
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.paths = {}
        for sid, records in ((WK1, [uline(T0, BODY1, "m1", ps="sdk"), aline(T0 + 60, "On it.", "a1", "m1")]),
                             (WK2, [uline(T0 + 5, BODY2, "m2", ps="sdk"), aline(T0 + 65, "On it.", "a2", "m2")]),
                             (MGR, [uline(T0 - 600, "the drag-range round, dictated", "hu")])):
            p = d / (sid + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            self.paths[sid] = p
        self._msgs = jd.MESSAGES
        jd.MESSAGES = d / "messages.jsonl"
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in [
            {"t": T0, "ev": "sent", "id": MID1, "from": "web", "from_id": MGR,
             "to_id": WK1, "kind": "delegate", "body": BODY1},
            {"t": T0 + 5, "ev": "sent", "id": MID2, "from": "web", "from_id": MGR,
             "to_id": WK2, "kind": "delegate", "body": BODY2}]) + "\n")
        self._disc = jd.discover
        fleet = [(WK1, str(self.paths[WK1]), None, "api"), (WK2, str(self.paths[WK2]), None, "tests"),
                 (MGR, str(self.paths[MGR]), None, "web")]
        jd.discover = lambda now, window=None, forks=True: fleet
        self._llm = jd.courier_llm
        jd.courier_llm = lambda text, menu, declared=None: '{"verdict": "delegating", "goal": 1, "text": "drag-range selection"}'
        jd._PARSE_CACHE.clear()

    def tearDown(self):
        jd._delegate_user_rooted = self._rooted
        jd.MESSAGES = self._msgs
        jd.discover = self._disc
        jd.courier_llm = self._llm
        for sid in (MGR, WK1, WK2):
            for dd in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (dd / (sid + ".json")).unlink()
                except OSError:
                    pass
            try:
                (jd.STATE / "overrides" / (sid + ".jsonl")).unlink()
            except OSError:
                pass
        self.td.cleanup()

    def _mgr(self, asks=1):
        nodes = {MGR + ":g1": _node(MGR + ":g1", "Drag-range selection over run rows", None,
                                    promptUuid="hu")}
        if asks > 1:
            nodes[MGR + ":g2"] = _node(MGR + ":g2", "Drag-range selection, the table half", None,
                                       promptUuid="hu")
        jd.save_goals(MGR, {"rompUuid": MGR, "seq": 2, "nodes": nodes, "placements": {}, "status": {}})

    def _trackers_under(self, ask):
        m = jd.load_goals(MGR)
        return [nd for nd in m["nodes"].values()
                if isinstance(nd.get("handoff"), dict) and nd.get("parentId") == ask]

    def test_one_ask_two_workers_is_one_card(self):
        self._mgr()
        jd.run_courier(now=NOW)
        trackers = self._trackers_under(MGR + ":g1")
        self.assertEqual(len(trackers), 2, "the fan-out lives INSIDE the ask card")
        self.assertTrue(all(t["handoff"].get("quiet") for t in trackers),
                        "no recipient goal will carry these msgIds — the reply-sweep owns the endings")
        for wk in (WK1, WK2):
            w = jd.load_goals(wk)
            self.assertEqual([nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)], [],
                             "no near-duplicate recipient top")
            self.assertIn("fyi", set(w["placements"].values()), "the recipient files quietly")

    def test_two_asks_one_worker_is_two_cards(self):
        self._mgr(asks=2)
        # both mails go to WK1; the courier links each mail to ITS ask, found by title in the
        # menu text (menu numbering shifts as trackers plant — resolve like the model would)
        def link_by_title(text, menu, declared=None):
            want = "table half" if "table half" in text else "run rows"
            for line in menu.splitlines():
                if want in line and line.strip().startswith("#"):
                    num = line.strip().split(".")[0].lstrip("#")
                    return '{"verdict": "delegating", "goal": %s, "text": "drag-range"}' % num
            for line in menu.splitlines():
                if want in line:
                    import re as _re
                    m = _re.search(r"(\d+)", line)
                    if m:
                        return '{"verdict": "delegating", "goal": %s, "text": "drag-range"}' % m.group(1)
            return '{"verdict": "delegating", "goal": 0, "text": "drag-range"}'
        jd.courier_llm = link_by_title
        self.paths[WK1].write_text("\n".join(json.dumps(r) for r in [
            uline(T0, BODY1, "m1", ps="sdk"), aline(T0 + 60, "On it.", "a1", "m1"),
            uline(T0 + 120, BODY2, "m2", "a1", ps="sdk"), aline(T0 + 180, "And that.", "a2", "m2")]) + "\n")
        self.paths[WK2].write_text(json.dumps(aline(T0, "idle.", "z1")) + "\n")   # WK2 out of this world
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in [
            {"t": T0, "ev": "sent", "id": MID1, "from": "web", "from_id": MGR,
             "to_id": WK1, "kind": "delegate", "body": BODY1},
            {"t": T0 + 120, "ev": "sent", "id": MID2, "from": "web", "from_id": MGR,
             "to_id": WK1, "kind": "delegate", "body": BODY2}]) + "\n")
        jd._PARSE_CACHE.clear()
        jd.run_courier(now=NOW)
        self.assertEqual(len(self._trackers_under(MGR + ":g1")), 1)
        self.assertEqual(len(self._trackers_under(MGR + ":g2")), 1,
                         "several asks to one worker stay several cards")
        w = jd.load_goals(WK1)
        self.assertEqual([nd for nd in w["nodes"].values() if isinstance(nd.get("origin"), dict)], [])

    def test_a_rooted_linkless_dispatch_still_mints_the_fallback_card(self):
        self._mgr()
        jd.courier_llm = lambda text, menu, declared=None: '{"verdict": "delegating", "goal": 0, "text": "drag-range selection"}'
        jd.run_courier(now=NOW)
        w1 = jd.load_goals(WK1)
        planted = [nd for nd in w1["nodes"].values() if isinstance(nd.get("origin"), dict)]
        self.assertEqual(len(planted), 1, "no ask node resolved → the recipient card IS the ask's card")
        self.assertTrue(planted[0].get("frame"), "the fallback mint keeps the frame enrichment")

    def test_linking_never_moves_the_ask_cards_column(self):
        self._mgr()
        st = jd.load_goals(MGR)
        jd.rollup_status(st, False)
        before = st["status"].get(MGR + ":g1")
        jd.save_goals(MGR, st)
        jd.run_courier(now=NOW)
        st2 = jd.load_goals(MGR)
        jd.rollup_status(st2, False)
        self.assertEqual(st2["status"].get(MGR + ":g1"), before,
                         "planting tracking children writes no verdict — the column stands")


class GrouperRetirement(unittest.TestCase):
    def test_mint_and_group_ops_parse_away(self):
        raw = ('{"ops":[{"why":"w","do":"mint","text":"Umbrella"},'
               '{"why":"w","do":"group","goal":1,"under":2},'
               '{"why":"w","do":"retitle","goal":1,"text":"Better title"}]}')
        ops = jd._parse_group(raw, 3)
        self.assertEqual([o["do"] for o in ops], ["retitle"],
                         "the retired ops never reach apply; housekeeping ops survive")

    def test_apply_group_ignores_hand_built_retired_ops(self):
        st = {"rompUuid": MGR, "seq": 2, "nodes": {
            MGR + ":g1": _node(MGR + ":g1", "Ask one", None),
            MGR + ":g2": _node(MGR + ":g2", "Ask two", None)}, "placements": {}, "status": {}}
        menu = [st["nodes"][MGR + ":g1"], st["nodes"][MGR + ":g2"]]
        n = jd.apply_group(st, menu, [{"do": "mint", "why": "w", "text": "Umbrella"},
                                      {"do": "group", "why": "w", "goal": 1, "under": 2}], T0)
        self.assertEqual(n, 0)
        self.assertEqual(len(st["nodes"]), 2, "no container minted")
        self.assertIsNone(st["nodes"][MGR + ":g1"].get("parentId"), "no nesting applied")

    def test_the_prompt_teaches_no_containers(self):
        self.assertNotIn('\\"do\\":\\"mint\\"', jd.GROUP_SYS)
        self.assertNotIn('\\"do\\":\\"group\\"', jd.GROUP_SYS)
        self.assertIn("its own card by design", jd.GROUP_SYS)


class UmbrellaDissolution(unittest.TestCase):
    def tearDown(self):
        for dd in (jd.GOALDIR, jd.GOALARCHDIR):
            try:
                (dd / (MGR + ".json")).unlink()
            except OSError:
                pass
        try:
            (jd.STATE / "overrides" / (MGR + ".jsonl")).unlink()
        except OSError:
            pass

    def _legacy(self):
        st = {"rompUuid": MGR, "seq": 5, "nodes": {
            MGR + ":u1": _node(MGR + ":u1", "Improve the runs dashboard", None, umbrella=True),
            MGR + ":u2": _node(MGR + ":u2", "Nested container", MGR + ":u1", umbrella=True),
            MGR + ":a1": _node(MGR + ":a1", "Fix the color rows", MGR + ":u1", promptUuid="hu"),
            MGR + ":a2": _node(MGR + ":a2", "Cap the scroll pane", MGR + ":u2", promptUuid="hu2"),
            MGR + ":s1": _node(MGR + ":s1", "a step of a1", MGR + ":a1")},
            "placements": {"segX": MGR + ":u1"}, "status": {}}
        return st

    def test_legacy_umbrellas_dissolve_idempotently(self):
        st = self._legacy()
        jd.rollup_status(st, False)
        self.assertNotIn(MGR + ":u1", st["nodes"])
        self.assertNotIn(MGR + ":u2", st["nodes"])
        self.assertIsNone(st["nodes"][MGR + ":a1"].get("parentId"), "the ask is its own card again")
        self.assertIsNone(st["nodes"][MGR + ":a2"].get("parentId"),
                          "a nested container's child re-parents to the first NON-container ancestor")
        self.assertEqual(st["nodes"][MGR + ":s1"].get("parentId"), MGR + ":a1",
                         "real subtrees keep their shape — only containers dissolve")
        self.assertIsNone(st["placements"].get("segX"),
                          "a placement at the container retires processed, never dangles")
        snap = json.dumps(st["nodes"], sort_keys=True, default=dict)
        jd.rollup_status(st, False)
        self.assertEqual(json.dumps(st["nodes"], sort_keys=True, default=dict), snap,
                         "second pass is a no-op — idempotent")

    def test_archived_container_sibling_rescue(self):
        # archives keep their containers (dissolution is live-store only) — the TRACE recovers the
        # stranded class there: a chain dead-ending at an archived umbrella looks once at the
        # container's children for the round's evidence (the audit's 22/23 predicate, now in-walk)
        saved = jd.parsed_session
        jd.parsed_session = lambda sid, files, now: {"turns": [{"atoms": [
            {"uuid": "hu", "type": "user", "author": "human",
             "message": {"role": "user", "content": "the dictated round"}}]}]}
        try:
            jd.save_goal_archive(MGR, {"rompUuid": MGR, "nodes": {
                "u1": _node("u1", "Round container", None, umbrella=True),
                "ask": _node("ask", "Fix the color rows", "u1", promptUuid="hu"),
                "trk": _node("trk", "tracking node", "u1")}, "placements": {}, "status": {}})
            jd.save_goals(MGR, {"rompUuid": MGR, "nodes": {}, "placements": {}, "status": {}})
            self.assertTrue(jd._delegate_user_rooted(MGR, "trk", {MGR: "/dev/null"}, NOW),
                            "the dead-end container's sibling holds the round's human record")
            # and WITHOUT a user-rooted sibling it stays quiet — the rescue is confident, not a guess
            jd.save_goal_archive(MGR, {"rompUuid": MGR, "nodes": {
                "u1": _node("u1", "Round container", None, umbrella=True),
                "trk": _node("trk", "tracking node", "u1")}, "placements": {}, "status": {}})
            self.assertFalse(jd._delegate_user_rooted(MGR, "trk", {MGR: "/dev/null"}, NOW))
        finally:
            jd.parsed_session = saved

    def test_a_peer_adopted_container_dissolves_on_the_very_next_rollup(self):
        # the ADOPT copy route (T103 audit): _rebase_onto_disk adopts a concurrent writer's nodes
        # wholesale, so a pre-T101 peer save can re-introduce an umbrella VERBATIM — including one
        # carrying diary rows. The dissolution is self-healing: the next rollup (any writer's)
        # dissolves it regardless of its payload, and its children stand alone with provenance.
        st = {"rompUuid": MGR, "seq": 3, "nodes": {
            MGR + ":u9": _node(MGR + ":u9", "adopted container", None, umbrella=True,
                               log=[{"ev_t": T0, "src": "romp", "kind": "settle", "at": T0}]),
            MGR + ":a9": _node(MGR + ":a9", "the adopted ask", MGR + ":u9", promptUuid="hu9")},
            "placements": {}, "status": {}}
        jd.rollup_status(st, False)
        self.assertNotIn(MGR + ":u9", st["nodes"])
        self.assertIsNone(st["nodes"][MGR + ":a9"].get("parentId"))
        self.assertEqual(st["nodes"][MGR + ":a9"].get("promptUuid"), "hu9")
        snap = json.dumps(st["nodes"], sort_keys=True, default=dict)
        jd.rollup_status(st, False)
        self.assertEqual(json.dumps(st["nodes"], sort_keys=True, default=dict), snap, "idempotent")

    def test_provenance_survives_dissolution(self):
        st = self._legacy()
        jd.rollup_status(st, False)
        self.assertEqual(st["nodes"][MGR + ":a1"].get("promptUuid"), "hu",
                         "the un-stranded ask carries its own evidence — the trace can reach it now")


if __name__ == "__main__":
    unittest.main()
