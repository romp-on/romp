#!/usr/bin/env python3
"""The awaited ROWS (plans/subagent-transcripts.md slice 2, the user 2026-09-05).

_session_awaiting (bin/romp-kernel) used to answer with ONE kind chosen by source precedence — live
subagents, else the pending background launches (kind "agents" only if EVERY pending row was an agent,
else the generic "task"), else armed watches. So one situation read "agents" or "tasks" by accident of
ordering, a background shell command plus a background agent read "Awaiting 2 tasks" with the agent
silently absorbed, and nothing on screen listed what was awaited. The user's call: the kinds are
different things — show them as SEPARATE ROWS grouped by kind. Now every live source contributes
`items` (one row per awaited thing: {kind, id, label, since, agentId?, detail?, watchId?}, kind in
agents / commands / watches / peer / timer), the legacy kind/count/why are DERIVED from the union, and
several kinds at once make kind "mixed" (jd.AWAIT_KINDS) with count = every row.

Synthetic inputs only; sources stubbed like test_awaiting_count.
"""
import inspect
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = SourceFileLoader("romp_kernel_awaitrows", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-666666666666"
AID = "a0123456789abcdef"          # a synthetic agent id (the hook's agent_id shape)


class AwaitingRows(unittest.TestCase):

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_tmux_sessions", "_bg_live_norm", "_bg_pending", "_states_awaiting_overlay",
                        "_owned_yield_why", "_session_stamp_full", "_session_delegated_why",
                        "_session_delegated_identities", "_watches", "_pr_watches", "_peer_identity")}
        km._tmux_sessions = lambda: {SID: {}}
        km._bg_live_norm = lambda sid, path: []
        km._bg_pending = lambda sid, path, tasks: tasks
        km._states_awaiting_overlay = lambda sid: None
        km._owned_yield_why = lambda sid, path: None
        km._session_stamp_full = lambda sid: (None, 0, None, None, ())
        km._session_delegated_why = lambda sid: None
        km._session_delegated_identities = lambda sid: []
        km._watches, km._pr_watches = [], []

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)

    # ---- all sources at once ----

    def test_all_three_sources_contribute_rows_and_several_kinds_read_mixed(self):
        km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "explore", "since": 100, "agentId": AID}]}}
        km._bg_live_norm = lambda sid, path: [
            {"tid": "tu_agent2", "desc": "map the parser", "t": 120, "type": "local_agent", "agentId": "a1111111111111111"},
            {"tid": "tu_bash1", "desc": "build the docs", "t": 130, "type": "local_bash"}]
        km._watches = [{"id": "w1", "sid": SID, "cmd": "test -f /tmp/notes-api.done", "note": "the CI run", "at": 90}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual((aw["kind"], aw["count"]), ("mixed", 4), "several kinds → mixed; every row counted")
        self.assertEqual([it["kind"] for it in aw["items"]], ["agents", "agents", "commands", "watches"],
                         "grouped in display order: agents, then commands, then watches")
        self.assertEqual(aw["why"], "waiting on 2 background agents, 1 background command and 1 armed watch")
        self.assertEqual(aw["since"], 90, "the oldest row's own event time")
        self.assertEqual(aw["tasks"], ["explore", "map the parser", "build the docs", "the CI run"],
                         "the legacy descriptions list every row, for consumers still reading tasks")

    def test_a_shell_and_an_agent_pending_are_two_rows_never_the_collapse_word(self):
        # the exact defect: a background shell command plus a background agent read "Awaiting 2 tasks"
        km._bg_live_norm = lambda sid, path: [
            {"tid": "tu_bash1", "desc": "build the docs", "t": 10, "type": "local_bash"},
            {"tid": "tu_agent1", "desc": "audit the sampler", "t": 11, "type": "local_agent"}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual(aw["kind"], "mixed")
        self.assertNotEqual(aw["kind"], "task")
        self.assertEqual(sorted(it["kind"] for it in aw["items"]), ["agents", "commands"])
        self.assertEqual(aw["count"], 2)
        self.assertNotIn("task", aw["why"])

    def test_a_hook_seen_agent_and_its_launch_row_merge_into_one_row(self):
        # the same agent is in BOTH the hook set (SubagentStart) and the task stream (the launch ack):
        # one row, wearing the launch's id (Stop's handle), its description, and the earliest start
        km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "explore", "since": 100, "agentId": AID}]}}
        km._bg_live_norm = lambda sid, path: [
            {"tid": "tu_agent1", "desc": "map the parser", "t": 95, "type": "local_agent", "agentId": AID}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual((aw["kind"], aw["count"]), ("agents", 1))
        self.assertEqual(aw["items"], [{"kind": "agents", "id": "tu_agent1", "label": "map the parser",
                                        "since": 100, "agentId": AID}])
        self.assertEqual(aw["why"], "1 background agent still working")

    # ---- single-kind sentences (byte-identical to the pre-rows whys where the word did not change) ----

    def test_single_kind_sentences_wear_the_plain_words(self):
        km._bg_live_norm = lambda sid, path: [{"tid": "b1", "desc": "build the docs", "t": 10, "type": "local_bash"}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual((aw["kind"], aw["why"]), ("task", "waiting on a background command: build the docs"))
        km._bg_live_norm = lambda sid, path: [{"tid": "b1", "desc": "build the docs", "t": 10, "type": "local_bash"},
                                              {"tid": "b2", "desc": "run the suite", "t": 12, "type": "local_bash"}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual((aw["kind"], aw["count"], aw["why"]), ("task", 2, "waiting on 2 background commands — build the docs, …"))
        km._bg_live_norm = lambda sid, path: []
        km._watches = [{"id": "w1", "sid": SID, "cmd": "test -f /tmp/a", "note": "the CI run", "at": 90},
                       {"id": "w2", "sid": SID, "cmd": "test -f /tmp/b", "at": 91}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual((aw["kind"], aw["count"], aw["why"]), ("job", 2, "waiting on 2 armed watches — the CI run, …"))
        km._watches = km._watches[:1]
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True)["why"], "waiting on the CI run")
        km._watches = []
        km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "a", "since": 1}, {"type": "b", "since": 2}]}}
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True)["why"], "2 background agents still working")

    def test_a_monitor_is_a_command_row(self):
        km._bg_live_norm = lambda sid, path: [{"tid": "m1", "desc": "watch the deploy log", "t": 10, "type": "local_monitor"}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual([it["kind"] for it in aw["items"]], ["commands"])

    # ---- watch rows ----

    def test_a_generic_watch_row_carries_its_cancel_handle_and_a_pr_watch_does_not(self):
        km._watches = [{"id": "w1", "sid": SID, "cmd": "gh run view 12 --exit-status", "note": "the CI run", "at": 90}]
        km._pr_watches = [{"sid": SID, "pr": 42, "repo": "notes-api/web", "at": 95}]
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual(aw["items"], [
            {"kind": "watches", "id": "watch:w1", "label": "the CI run", "since": 90,
             "detail": "gh run view 12 --exit-status", "watchId": "w1"},   # cancel_watch's handle — the box's Cancel
            {"kind": "watches", "id": "pr:notes-api/web#42", "label": "PR #42 (notes-api/web)", "since": 95}])   # no early-retire path today → no handle
        self.assertEqual(aw["kind"], "job")

    def test_the_awaiting_box_cancel_reaches_the_same_cancel_watch_as_the_cli(self):
        # the WS door (slice 2) hands the row's watchId to cancel_watch — the path `romp watch --cancel`
        # and POST /watch {"cancel"} already take; LOUD on a miss
        src = inspect.getsource(km)
        self.assertIn('msg.get("type") == "cancelWatch" and msg.get("watchId")', src)
        self.assertIn('if not cancel_watch(str(msg["watchId"]).strip()):', src)
        self.assertIn("Couldn't cancel that watch — it may have already fired or been cancelled.", src)
        row, err = km.add_watch("test -f /tmp/notes-api.done", SID, note="the CI run")
        self.assertIsNone(err)
        self.assertEqual([it["watchId"] for it in km._watch_awaiting(SID)["items"]], [row["id"]])
        self.assertTrue(km.cancel_watch(row["id"]))
        self.assertIsNone(km._watch_awaiting(SID), "cancelled → the row is gone, so the wait is gone")
        self.assertFalse(km.cancel_watch(row["id"]), "a second cancel is a miss the door reports loudly")

    # ---- the row shape ----

    def test_a_row_carries_agentId_and_detail_only_when_known(self):
        self.assertEqual(km._awaiting_item("commands", "b1", " build ", None),
                         {"kind": "commands", "id": "b1", "label": "build", "since": None})
        self.assertEqual(km._awaiting_item("agents", "t1", "map", 5, agent_id=AID),
                         {"kind": "agents", "id": "t1", "label": "map", "since": 5, "agentId": AID})
        with self.assertRaises(AssertionError):
            km._awaiting_item("task", "x", "legacy kind keys are not row kinds", None)

    # ---- the arms that name no live rows ----

    def test_arms_that_cannot_enumerate_ship_an_empty_row_list(self):
        km._states_awaiting_overlay = lambda sid: {"awaiting": True, "why": "waiting on a build", "kind": "job", "t": 4321}
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True)["items"], [])
        km._states_awaiting_overlay = lambda sid: None
        km._owned_yield_why = lambda sid, path: "waiting on a background command: the GPU batch"
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True, stamp=True)["items"], [])

    def test_a_peer_wait_lists_its_peers_as_rows(self):
        km._peer_identity = lambda p: {"name": str(p), "host": "", "sid": str(p), "color": None}
        km._session_stamp_full = lambda sid: ("g1", 8765, "delegated to two peers", "peer", ("api", "web"))
        aw = km._session_awaiting(SID, "/tmp/x", True, stamp=True)
        self.assertEqual(aw["items"], [{"kind": "peer", "id": "peer:api", "label": "api", "since": None},
                                       {"kind": "peer", "id": "peer:web", "label": "web", "since": None}])
        self.assertEqual(aw["count"], 2)


class MixedKind(unittest.TestCase):
    """"mixed" is a LIVE-read kind only: the enum every surface validates against accepts it, while the
    judge's parse sites keep filing a specific kind — an LLM emitting "mixed" degrades to kindless."""

    def test_the_enum_carries_mixed_and_the_judged_tuple_does_not(self):
        self.assertIn("mixed", jd.AWAIT_KINDS)
        self.assertNotIn("mixed", jd.AWAIT_KINDS_JUDGED)
        self.assertEqual(jd.AWAIT_KINDS[:-1], jd.AWAIT_KINDS_JUDGED)

    def test_a_closer_filing_mixed_is_kindless(self):
        got = jd._parse_close('{"done": [], "block": [], "awaiting": [{"goal": 1, "why": "w", "kind": "mixed"},'
                              '{"goal": 2, "why": "x", "kind": "job"}]}', 3)
        self.assertEqual(got["awaiting"], {1: {"why": "w", "kind": None}, 2: {"why": "x", "kind": "job"}})

    def test_a_nudge_planner_filing_mixed_is_kindless(self):
        ops = jd._parse_plan('{"ops":[{"do":"awaiting","goal":1,"why":"w","kind":"mixed"}]}', 3)
        self.assertEqual(ops, [{"do": "awaiting", "why": "w", "goal": 1}])

    def test_an_overlay_row_saying_mixed_is_accepted_as_data(self):
        saved = km._states_awaiting_overlay, km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {}}
        km._states_awaiting_overlay = lambda sid: {"awaiting": True, "why": "several things", "kind": "mixed", "t": 1, "count": 3}
        try:
            aw = km._session_awaiting(SID, "/tmp/x", True)
        finally:
            km._states_awaiting_overlay, km._tmux_sessions = saved
        self.assertEqual((aw["kind"], aw["count"], aw["items"]), ("mixed", 3, []))


if __name__ == "__main__":
    unittest.main()
