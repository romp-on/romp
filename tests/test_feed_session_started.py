#!/usr/bin/env python3
"""The feed's side of T319 (the user 2026-09-10: no card for work a session started on its own). Three rules,
each pinned here on a hermetic build_feed over synthetic stores and transcripts:
- a Workflow or Agent run the session launched shows only inside the parent card's awaiting panel (the
  local_workflow / local_agent rows), never as a card of its own;
- a step the planner demoted (born {kind: session}) renders in its parent's tree with its why, and a
  session-started ROOT that still shows (its parent gone) carries a one-line face naming what it is;
- older stores are healed read-side: a top with no human prompt whose title matches a background run the
  transcript records nests under the session's human-prompted top, the same way on every build, with a top
  that has a human prompt never touched; the heal logs once how many it nested.
Placeholder uuids, invented text, an invented project."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_session_started", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
WF_SCRIPT = "export const meta = { name: 'review-notes-api-retry', description: 'Lens reviewers over the retry diff' }\nreturn 1"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn", launch=None):
    content = [{"type": "text", "text": text}]
    if launch:
        content.append({"type": "tool_use", "id": "toolu_" + uuid, "name": launch[0], "input": launch[1]})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def tack(t, uuid, parent, tool_use_id, desc):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "toolUseResult": {"isAsync": True, "status": "async_launched", "description": desc, "taskType": "local_workflow"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "Workflow launched in background. Task ID: w1"}]}}


class _Feed(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        km._HEAL_LOG["n"] = 0
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [uline(T0, "Add retries to the notes-api client", "u1"),
                aline(T0 + 60, "Adding the retry loop.", "a1", "u1", stop="tool_use",
                      launch=("Workflow", {"script": WF_SCRIPT})),
                tack(T0 + 61, "u2", "a1", "toolu_a1", "Lens reviewers over the retry diff"),
                aline(T0 + 400, "Wrote the retry loop; the review runs in the background.", "a2", "u2")]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        km._live_map = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._bgall_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map) = self.saved
        self.td.cleanup()

    def _store(self, nodes, status=None, last=None):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": len(nodes), "lastNode": last, "nodes": nodes, "placements": {}, "status": status or {}}))

    def _feed(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            f = km.build_feed(NOW)
        return f, err.getvalue()

    @staticmethod
    def _node(nid, text, parent=None, t=T0, **kw):
        d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False,
             "trail": [], "t": t, "mt": t}
        d.update(kw)
        return d


class BornSteps(_Feed):
    def test_a_demoted_step_renders_in_its_parents_tree_with_its_why(self):
        top, step = SID + ":g1", SID + ":g2"
        self._store({top: self._node(top, "Add retries to the notes-api client", promptUuid="u1"),
                     step: self._node(step, "Adversarial review of the retry diff", parent=top, t=T0 + 400,
                                      born={"kind": "session", "via": "workflow", "why": "started a background workflow (Lens reviewers over the retry diff)"})})
        feed, err = self._feed()
        asks = [a for a in feed["asks"] if a["sid"] == SID]
        self.assertEqual([a["text"] for a in asks], ["Add retries to the notes-api client"], "one card: the ask")
        self.assertIsNone(asks[0]["sessionStarted"], "an ask's own card carries no session-started face")
        rows = {r["id"]: r for r in asks[0]["tree"]}
        self.assertEqual(rows[step]["born"]["via"], "workflow")
        self.assertIn("Lens reviewers", rows[step]["born"]["why"])
        self.assertIsNone(rows[top]["born"])

    def test_an_orphaned_born_step_is_a_root_that_names_the_request_it_served(self):
        # the parent node is gone (popped by a later reconcile): the step still has a parentId, pointing nowhere.
        # It renders as a root, and its face names the request from the record made at demotion time
        step = SID + ":g2"
        self._store({step: self._node(step, "Adversarial review of the retry diff", parent=SID + ":g1", t=T0 + 400,
                                      born={"kind": "session", "via": "workflow", "parentText": "Add retries to the notes-api client",
                                            "why": "started a background workflow (Lens reviewers over the retry diff)"})})
        feed, err = self._feed()
        card = next(a for a in feed["asks"] if a["sid"] == SID)
        self.assertEqual(card["sessionStarted"], {"why": "started a background workflow (Lens reviewers over the retry diff)",
                                                  "parent": "Add retries to the notes-api client"})


class HealOlderStores(_Feed):
    def _pre_rule_store(self):
        ask, wf, other, plain = SID + ":g1", SID + ":g2", SID + ":g3", SID + ":g4"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     # minted by the old planner off the session's narration: its anchor resolved to the session's own
                     # record (the judge latched "machine"), and its words match the recorded run
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine"),
                     # a top with a human anchor whose words also match the run: never touched
                     other: self._node(other, "Review the retry diff with fresh eyes", t=T0 + 600, promptUuid="u7", askAnchor="human"),
                     # machine-rooted with words unlike any run: the event decides, it nests too
                     plain: self._node(plain, "Tidy the colour names", t=T0 + 700, promptUuid="a9", askAnchor="machine")})
        return ask, wf, other, plain

    def test_machine_rooted_tops_nest_under_the_human_top_and_stay_there(self):
        ask, wf, other, plain = self._pre_rule_store()
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {ask, other}, "the machine-rooted tops are no cards")
        rows = {r["id"]: r for r in asks[ask]["tree"]}
        self.assertIn(wf, rows, "the one whose words match the run sits in the retry top's tree")
        self.assertEqual(rows[wf]["born"]["via"], "workflow")
        self.assertTrue(rows[wf]["born"]["healed"])
        self.assertIn("matched a background workflow the session started", rows[wf]["born"]["why"])
        # the one unlike any run: nested by the event alone, under the newest human top minted before it
        rows_other = {r["id"]: r for r in asks[other]["tree"]}
        self.assertIn(plain, rows_other)
        self.assertEqual(rows_other[plain]["born"]["via"], "work")
        self.assertIn("rooted in the session's own record", rows_other[plain]["born"]["why"])
        self.assertIn("feed: 2 session-started top(s) nested", err, "said once, on stderr")
        # the second build: the same nesting, nothing said again
        feed2, err2 = self._feed()
        asks2 = {a["itemId"]: a for a in feed2["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks2), {ask, other})
        self.assertIn(wf, {r["id"] for r in asks2[ask]["tree"]})
        self.assertIn(plain, {r["id"] for r in asks2[other]["tree"]})
        self.assertEqual(err2, "")

    def test_a_top_with_a_human_anchor_is_never_healed_away(self):
        ask, wf, other, plain = self._pre_rule_store()
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertIn(other, asks, "its words match the run, but a human asked for it")
        self.assertIsNone(asks[other]["sessionStarted"])
        self.assertIn(ask, asks)

    def test_the_heal_is_a_pure_function_of_store_and_transcript(self):
        ask, wf, other, plain = self._pre_rule_store()
        nodes = json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"]
        a = km._heal_session_tops(str(self.tpath), nodes)
        b = km._heal_session_tops(str(self.tpath), nodes)
        self.assertEqual(a, b)
        self.assertEqual(set(a), {wf, plain})
        self.assertEqual(a[wf][0], ask)
        self.assertEqual(a[plain][0], other, "the newest human top minted before it")
        self.assertEqual(km._heal_session_tops(str(self.tpath), {k: v for k, v in nodes.items() if k in (ask, other)}), {})

    def test_an_unlatched_or_human_top_and_a_delegate_tracker_are_not_candidates(self):
        ask, tracker, fresh = SID + ":g1", SID + ":g5", SID + ":g6"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     tracker: self._node(tracker, "delegated: review the retry diff", t=T0 + 500, handoff={"peer": "22222222-3333-4444-5555-666666666666", "msgId": "m1"}),
                     fresh: self._node(fresh, "Lens review of the retry diff", t=T0 + 600, promptUuid="u8")})
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertIn(fresh, asks, "a not-yet-latched prompt top keeps its card")
        self.assertIsNone(asks[fresh]["sessionStarted"])
        nested = {r["id"] for a in asks.values() for r in a["tree"] if r["id"] != a["itemId"]}
        self.assertNotIn(tracker, nested, "a delegate's tracker is never healed into another card")
        self.assertNotIn(fresh, nested)
        self.assertEqual(err, "")

    def test_a_blocked_machine_top_keeps_its_needs_you_card_and_wears_the_face(self):
        # needs-you breaks through: the heal never takes a pending question off the board, and the card still says
        # what it is instead of posing as an ask
        ask, wf = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine", blocked=True)},
                    status={wf: "blocked", ask: "working"})
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertIn(wf, asks, "still a card")
        self.assertEqual(asks[wf]["column"], "needs_input")
        self.assertIn("matched a background workflow", asks[wf]["sessionStarted"]["why"])
        self.assertIsNone(asks[wf]["sessionStarted"]["parent"], "not nested: no parent named")
        self.assertEqual(err, "", "nothing nested, nothing counted")

    def test_a_cleared_host_hides_its_row_and_a_tracker_never_hosts(self):
        # a cleared ask keeps hosting (its row hides with it, never resurfacing as a root card); a delegate's tracker
        # is no host (the delegation fold hides those), so with only a tracker the machine top keeps its card
        ask, wf, tracker = SID + ":g1", SID + ":g2", SID + ":g5"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human", cleared=True),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")},
                    status={ask: "cleared"})           # a top is cleared through the ledger; the export reads cleared
        feed, err = self._feed()
        self.assertEqual([a["itemId"] for a in feed["asks"] if a["sid"] == SID], [], "hidden with its cleared host, no root card")
        self._store({tracker: self._node(tracker, "delegated: review the retry diff", t=T0 + 100, handoff={"peer": "22222222-3333-4444-5555-666666666666", "msgId": "m1"}),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")})
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertIn(wf, asks, "no host: still a card")
        self.assertEqual(asks[wf]["sessionStarted"]["parent"], None)

    def test_a_delegated_goal_hosts_only_when_its_chain_is_proven_to_a_human(self):
        planted, wf = SID + ":g7", SID + ":g2"
        peer = "22222222-3333-4444-5555-666666666666"
        self._store({planted: self._node(planted, "Review the retry diff for the manager", origin={"peer": peer, "msgId": "m2"},
                                         userAsk={"text": "please review the retry diff", "sid": peer}),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")})
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {planted}, "the worker's process top nests under the proven delegated goal")
        self.assertIn(wf, {r["id"] for r in asks[planted]["tree"]})
        # a mid-chain coordination top with no proven ask never hosts: the machine top keeps its card and its face
        self._store({planted: self._node(planted, "Review the retry diff for the manager", origin={"peer": peer, "msgId": "m2"}),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")})
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertIn(wf, asks)
        self.assertIsNotNone(asks[wf]["sessionStarted"])

    def test_a_permission_prompt_elsewhere_leaves_the_nesting_alone(self):
        # the session is on a permission prompt, but the floor resolves to the HUMAN top (lastNode under it): the
        # machine top stays nested, no root card pops out
        ask, wf = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")}, last=ask)
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {ask})
        self.assertEqual(asks[ask]["column"], "needs_input")
        self.assertIn(wf, {r["id"] for r in asks[ask]["tree"]})

    def test_a_long_title_holding_every_word_of_a_short_description_matches(self):
        nodes = {SID + ":g1": self._node(SID + ":g1", "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                 SID + ":g2": self._node(SID + ":g2", "Lens reviewers over the retry diff of the notes-api client, round two, with findings", t=T0 + 500, promptUuid="a2", askAnchor="machine")}
        h = km._heal_session_tops(str(self.tpath), nodes, {})
        self.assertEqual(h[SID + ":g2"][1]["via"], "workflow", "shared by the smaller set: the short description's words all appear")

    def test_the_top_a_live_floor_stands_on_keeps_its_card(self):
        # the session is stopped on a permission prompt whose focus is a machine top whose host is cleared: nesting
        # would lose the needs-you floor with the hidden host, so the top keeps its card and the floor
        ask, wf = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human", cleared=True),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")},
                    status={ask: "cleared"}, last=wf)
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {wf})
        self.assertEqual(asks[wf]["column"], "needs_input")
        self.assertIn("matched a background workflow", asks[wf]["sessionStarted"]["why"])

    def test_a_floor_that_resolves_to_the_ask_leaves_the_machine_top_nested(self):
        # the session is stopped on a permission prompt whose focus is UNDER the ask: the floor resolves to the ask,
        # so the machine top stays a row in the ask's tree (a broader keep on the session's state would pop it out)
        ask, step, wf = SID + ":g1", SID + ":g3", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     step: self._node(step, "Wrote the retry loop", parent=ask, t=T0 + 100),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")}, last=step)
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {ask}, "the floor is the ask's; the machine top stays nested")
        self.assertEqual(asks[ask]["column"], "needs_input")
        self.assertIn(wf, {r["id"] for r in asks[ask]["tree"]})

    def test_a_needs_input_state_whose_floor_does_not_set_leaves_the_nesting_alone(self):
        # the focus top is the machine top but its exported status is completed, so the permission floor does not
        # set on it: no floor resolves there, and the top stays nested (a keep on the session state alone would not)
        ask, wf = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine", nodeComplete=True)},
                    status={wf: "completed"}, last=wf)
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {ask})
        self.assertIn(wf, {r["id"] for r in asks[ask]["tree"]})

    def test_a_scheduled_prompts_top_keeps_its_card_with_no_face(self):
        # the latch marks a scheduled (sdk) prompt's top "scheduled": the user's configured work, never nested, no face
        ask, night = SID + ":g1", SID + ":g6"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     night: self._node(night, "Nightly guard review of the tree", t=T0 + 500, promptUuid="c1", askAnchor="scheduled")})
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {ask, night})
        self.assertIsNone(asks[night]["sessionStarted"])
        self.assertEqual(err, "")

    def test_an_unnest_recomputes_the_hosts_row_state_and_the_parked_cue(self):
        # W (machine top) nests under the completed ask A; W holds an open agent step and a handoff edge. A permission
        # floor on W un-nests it: A must then render done and its leaf R must lose the parked cue (both derived rows)
        A, R, W, H, G = SID + ":g1", SID + ":g3", SID + ":g2", SID + ":g4", SID + ":g5"
        self._store({A: self._node(A, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human", nodeComplete=True),
                     R: self._node(R, "Write the changelog entry", parent=A, t=T0 + 100),
                     W: self._node(W, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine"),
                     H: self._node(H, "delegated: check the diff", parent=W, t=T0 + 600, handoff={"peer": "22222222-3333-4444-5555-666666666666", "msgId": "m1"}),
                     G: self._node(G, "Lens pass two", parent=W, t=T0 + 650, agentTask={"status": "open"})},
                    status={A: "completed"}, last=W)
        km._live_map = lambda: {SID: {"state": "permission", "since": NOW - 10, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None}}
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {A, W}, "the floor resolves to W: it un-nests and keeps its card")
        rows = {r["id"]: r for r in asks[A]["tree"]}
        self.assertNotIn(W, rows, "W left the tree the card shows")
        self.assertEqual(rows[A]["status"], "done", "no open agent step under A any more: the row reads done (agent_open recomputed)")
        self.assertIsNone(rows[R]["parked"], "no younger sibling with a handoff edge under A any more (parked_rows recomputed)")
        self.assertEqual(asks[W]["column"], "needs_input")

    def test_the_launch_match_reads_the_dispatch_not_the_completion_summary(self):
        # the run completes and its notification's summary overwrites the task's summary; the heal still matches on
        # the words the dispatch carried at launch (launchDesc), so the why and the parent do not change when a run ends
        done_recs = json.loads("[" + ",".join(l for l in self.tpath.read_text().splitlines() if l.strip()) + "]")
        done_recs.append({"type": "user", "timestamp": iso(T0 + 900), "uuid": "u9", "parentUuid": "a2",
                          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_a1",
                                      "content": "<task-notification><task-id>w1</task-id><tool-use-id>toolu_a1</tool-use-id><status>completed</status>"
                                                 "<summary>Findings: two nits on the colour names</summary></task-notification>"}]}})
        self.tpath.write_text("\n".join(json.dumps(r) for r in done_recs) + "\n")
        km._bgall_cache.clear()
        tasks = km._bg_scan_all_cached(str(self.tpath))
        wf_task = next(t for t in tasks if t.get("type") == "local_workflow")
        self.assertIn("two nits", wf_task["summary"], "the completion overwrote the summary")
        self.assertEqual(wf_task["launchDesc"], "Lens reviewers over the retry diff", "the dispatch's words stay")
        ask, wf = SID + ":g1", SID + ":g2"
        self._store({ask: self._node(ask, "Add retries to the notes-api client", promptUuid="u1", askAnchor="human"),
                     wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")})
        nodes = json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"]
        h = km._heal_session_tops(str(self.tpath), nodes, {})
        self.assertEqual(h[wf][0], ask)
        self.assertIn("Lens reviewers over the retry diff", h[wf][1]["why"], "matched on the launch, not the completion")
        self.assertEqual(h[wf][1]["via"], "workflow")

    def test_a_machine_top_with_no_human_top_to_nest_under_shows_as_a_card_that_says_so(self):
        wf = SID + ":g2"
        self._store({wf: self._node(wf, "Lens review of the retry diff", t=T0 + 500, promptUuid="a2", askAnchor="machine")})
        feed, err = self._feed()
        card = next(a for a in feed["asks"] if a["sid"] == SID)
        self.assertEqual(card["sessionStarted"]["parent"], None)
        self.assertIn("matched a background workflow", card["sessionStarted"]["why"])
        self.assertEqual(err, "", "nothing nested: nothing counted")


class HostsCurrentAtTheMint(_Feed):
    """The heal nests a machine-rooted top only under a host that was CURRENT when the top was minted (2026-09-18, the user's
    card): a store whose human-anchored hosts were all minted in one day had the oldest-host fallback sweep two completed
    roots from days before into the oldest host's tree (the first request of that day), where the card's button counted them as sub-goals and its fold
    listed them as reviewed earlier. Two roots, one older than every host: the host's payload never holds the other root's
    nodes, and the three counts the card derives from the payload (the button's direct non-handoff children, the fold's
    reviewed-earlier children, the rows the fold shows) agree with the store's own children."""

    def _two_roots(self):
        host, h1, h2, h3, h4 = (SID + ":g%d" % i for i in range(10, 15))
        old, o1 = SID + ":g20", SID + ":g21"
        done_at = T0 + 5500
        done = {"nodeComplete": True, "log": [{"kind": "done", "ev_t": done_at}], "mt": done_at}
        self._store({
            # the user's request, minted AFTER the old root; its boundary (a settle the user reviewed through) lies past its
            # kids' done events, so every done kid is reviewed earlier
            host: self._node(host, "Add retries to the notes-api client", t=T0 + 5000, promptUuid="u1", askAnchor="human",
                             deltaSince=T0 + 6000, **done),
            h1: self._node(h1, "Write the retry loop", parent=host, t=T0 + 5100, **done),
            h2: self._node(h2, "Cover the retry loop with tests", parent=host, t=T0 + 5200, **done),
            h3: self._node(h3, "Assert the back-off schedule", parent=h2, t=T0 + 5300, **done),
            h4: self._node(h4, "\u21aa delegated to api: review the retry diff", parent=host, t=T0 + 5400,
                           handoff={"peer": "aaaaaaaa-1111-2222-3333-888888888888", "msgId": "m-1"}, **done),
            # a machine-rooted root minted DAYS before the request, with a done step of its own: the older store's shape
            old: self._node(old, "Tidy the colour names", t=T0, promptUuid="a9", askAnchor="machine", nodeComplete=True,
                            log=[{"kind": "done", "ev_t": T0 + 100}], mt=T0 + 100),
            o1: self._node(o1, "Rename the grey tokens", parent=old, t=T0 + 50, nodeComplete=True,
                           log=[{"kind": "done", "ev_t": T0 + 90}], mt=T0 + 90)},
            status={host: "completed", old: "completed"})
        return host, (h1, h2, h3, h4), old, o1

    def test_a_root_older_than_every_host_is_not_nested_under_a_host_minted_after_it(self):
        host, kids, old, o1 = self._two_roots()
        nodes = json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"]
        h = km._heal_session_tops(str(self.tpath), nodes, {})
        self.assertEqual(set(h), {old}, "the old root is the one candidate")
        self.assertIsNone(h[old][0], "no host was current at its mint: not nested (the oldest-host fallback nested it under the request)")
        self.assertIn("rooted in the session's own record", h[old][1]["why"])

    def test_one_roots_fold_never_shows_the_other_roots_nodes_and_the_counts_agree(self):
        host, (h1, h2, h3, h4), old, o1 = self._two_roots()
        feed, err = self._feed()
        asks = {a["itemId"]: a for a in feed["asks"] if a["sid"] == SID}
        self.assertEqual(set(asks), {host, old}, "two roots, two cards: the old root keeps its own")
        self.assertEqual(asks[old]["sessionStarted"]["parent"], None, "…wearing the face with no request to name")
        rows = {r["id"]: r for r in asks[host]["tree"]}
        self.assertEqual(set(rows), {host, h1, h2, h3, h4}, "the request's payload holds its own subtree and nothing of the other root")
        self.assertEqual(set(r["id"] for r in asks[old]["tree"]), {old, o1})
        # the counts the card derives from the payload, computed the client's way (feed.ts applySections / renderTree): the
        # handoff child is out of every count, since the walk never renders it (a reviewed handoff counted in the fold's label
        # made "3 reviewed earlier" open to two rows, the 2026-09-18 read)
        direct = rows[host]["children"]
        button = [c for c in direct if rows[c]["kind"] != "handoff"]                    # "N sub-goals"
        fold = [c for c in button if rows[c].get("reviewedEarlier")]                    # "N reviewed earlier"
        shown = fold                                                                    # the rows the open fold walks
        self.assertEqual(sorted(direct), sorted([h1, h2, h4]), "the direct children are the store's, by parentId")
        self.assertEqual((len(button), len(fold), len(shown)), (2, 2, 2), "button 2, fold 2, rows 2: one count per row that renders")
        self.assertEqual(rows[h4]["kind"], "handoff")
        self.assertTrue(all(rows[c].get("reviewedEarlier") for c in (h1, h2, h4)), "every done kid predates the boundary")
        self.assertEqual(err, "", "nothing nested: nothing counted")


class AwaitingPanel(unittest.TestCase):
    def test_workflow_and_agent_rows_are_agents_for_the_awaiting_panel_never_cards(self):
        self.assertTrue(km._bg_is_agent("local_workflow"))
        self.assertTrue(km._bg_is_agent("local_agent"))
        self.assertFalse(km._bg_is_agent("local_bash"))
        # the feed's cards are goal nodes only: the card loop reads children[None], nothing else
        import inspect
        src = inspect.getsource(km._feed_session_entry)
        self.assertIn("for nid in children.get(None, [])", src)
        self.assertNotIn("local_workflow", src.split("for nid in children.get(None, [])")[0].split("healed = _heal_session_tops")[0][-4000:],
                         "no card is built from a workflow row")


if __name__ == "__main__":
    unittest.main(verbosity=2)
