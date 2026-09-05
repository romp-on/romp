#!/usr/bin/env python3
"""T225 rider (the user 2026-09-02): the chip must agree in number — "Awaiting agent" for exactly one,
"Awaiting agents" only for two or more — and the box must show the same count. So every source of
_session_awaiting (bin/romp-kernel) now carries `count` beside why/kind/since: the live agent count,
the pending-task count, one per armed watch, the peers a stamp or delegation names; None when the
source cannot know (a bare overlay row, an untyped stamp) — never parsed out of the why. The status
payload ships it as awaitingCount, the ONE number the chip, the box gist, the feed pill and the spin
caption derive their word from (ui kindWord). Synthetic inputs only, sources stubbed like
test_awaiting_since.
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
km = SourceFileLoader("romp_kernel_awaitcount", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"


class AwaitingCount(unittest.TestCase):

    def setUp(self):
        self._saved = {n: getattr(km, n) for n in
                       ("_tmux_sessions", "_bg_live_norm", "_bg_pending", "_states_awaiting_overlay",
                        "_owned_yield_why", "_session_stamp_full", "_session_delegated_why",
                        "_session_delegated_identities", "_watch_awaiting", "_peer_identity")}
        km._tmux_sessions = lambda: {SID: {}}
        km._bg_live_norm = lambda sid, path: []
        km._bg_pending = lambda sid, path, tasks: []
        km._states_awaiting_overlay = lambda sid: None
        km._owned_yield_why = lambda sid, path: None
        km._session_stamp_full = lambda sid: (None, 0, None, None, ())
        km._session_delegated_why = lambda sid: None
        km._session_delegated_identities = lambda sid: []
        km._watch_awaiting = lambda sid: None

    def tearDown(self):
        for n, f in self._saved.items():
            setattr(km, n, f)

    def test_one_live_agent_counts_one(self):
        km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "a", "since": 500}]}}
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual((aw["kind"], aw["count"]), ("agents", 1))
        self.assertEqual(aw["why"], "1 background agent still working", "the why already agrees in number")

    def test_several_live_agents_count_them_all(self):
        km._tmux_sessions = lambda: {SID: {"subagents": [{"type": "a", "since": 1}, {"type": "b", "since": 2},
                                                         {"type": "c", "since": 3}]}}
        aw = km._session_awaiting(SID, "/tmp/x", True)
        self.assertEqual(aw["count"], 3)
        self.assertIn("3 background agents", aw["why"])

    def test_pending_tasks_count_the_pending_ones(self):
        tasks = [{"tid": "1", "desc": "watching CI", "t": 7, "type": "bash"},
                 {"tid": "2", "desc": "polling deploy", "t": 3, "type": "bash"}]
        km._bg_live_norm = lambda sid, path: tasks
        km._bg_pending = lambda sid, path, ts: ts[:1]
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True)["count"], 1)
        km._bg_pending = lambda sid, path, ts: ts
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True)["count"], 2)

    def test_watches_count_one_per_row(self):
        km._watch_awaiting = self._saved["_watch_awaiting"]   # the real one, over stubbed registries
        saved = (km._watches, km._pr_watches)
        try:
            km._watches = [{"sid": SID, "cmd": "grep -q DONE /tmp/a.log", "at": 10},
                           {"sid": SID, "cmd": "test -f /tmp/b", "note": "the build", "at": 12}]
            km._pr_watches = [{"sid": SID, "pr": 42, "repo": "notes-api/web", "at": 14}]
            aw = km._session_awaiting(SID, "/tmp/x", True)
            self.assertEqual((aw["kind"], aw["count"]), ("job", 3))
            self.assertEqual(len(aw["tasks"]), 3)
            self.assertEqual(len(aw["items"]), 3, "one row per watch (slice 2)")
            self.assertTrue(all(it["kind"] == "watches" for it in aw["items"]))
        finally:
            km._watches, km._pr_watches = saved

    def test_an_overlay_row_carries_a_count_only_when_its_producer_said(self):
        km._states_awaiting_overlay = lambda sid: {"awaiting": True, "why": "2 background agents still working",
                                                   "kind": "agents", "t": 4321}
        self.assertIsNone(km._session_awaiting(SID, "/tmp/x", True)["count"],
                          "never parsed out of the why — data, not a heuristic")
        km._states_awaiting_overlay = lambda sid: {"awaiting": True, "why": "waiting on a build",
                                                   "kind": "job", "t": 4321, "count": 1}
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True)["count"], 1)
        km._states_awaiting_overlay = lambda sid: {"awaiting": True, "why": "x", "kind": "job", "t": 1, "count": 0}
        self.assertIsNone(km._session_awaiting(SID, "/tmp/x", True)["count"], "a non-positive count is no count")

    def test_a_peer_stamp_counts_its_peers_and_an_untyped_stamp_none(self):
        km._peer_identity = lambda p: {"name": str(p), "host": "", "sid": str(p), "color": None}
        km._session_stamp_full = lambda sid: ("g1", 8765, "delegated to two peers", "peer", ("p-a", "p-b"))
        aw = km._session_awaiting(SID, "/tmp/x", True, stamp=True)
        self.assertEqual((aw["kind"], aw["count"]), ("peer", 2))
        km._session_stamp_full = lambda sid: ("g1", 8765, "waiting on the test suite", "task", ())
        self.assertIsNone(km._session_awaiting(SID, "/tmp/x", True, stamp=True)["count"])

    def test_a_delegation_wait_counts_the_identified_peers(self):
        km._session_delegated_why = lambda sid: "delegated to web, api; waiting on their replies"
        km._session_delegated_identities = lambda sid: [{"name": "web"}, {"name": "api"}]
        aw = km._session_awaiting(SID, "/tmp/x", True, stamp=True)
        self.assertEqual((aw["kind"], aw["count"]), ("peer", 2))

    def test_an_owned_yield_is_one_dispatch(self):
        km._owned_yield_why = lambda sid, path: "waiting on a background task: the GPU batch"
        self.assertEqual(km._session_awaiting(SID, "/tmp/x", True, stamp=True)["count"], 1)

    def test_the_status_payload_ships_the_count_beside_the_kind(self):
        src = inspect.getsource(km)
        self.assertIn('"awaitingKind": awaiting_kind,', src)
        self.assertIn('"awaitingCount": ((_aw or {}).get("count") if isinstance((_aw or {}).get("count"), int) else None),',
                      src)

    def test_every_other_surface_ships_the_same_count(self):
        # T228: the one-count rule reaches the timeline lane and the goal-floored feed card too (the
        # placeholder card already threaded it); each surface words itself from THIS number, never its own
        src = inspect.getsource(km)
        self.assertIn('"awaitingCount": ((_aw_bg or {}).get("count") if isinstance((_aw_bg or {}).get("count"), int) else None),',
                      src, "the timeline lane payload")
        self.assertIn('"count": await_count,', src, "the goal card's awaiting object")
        # the or-chain tuples grew a sixth slot — the awaited ROWS (slice 2, 2026-09-05) — beside the count
        self.assertIn('(_owned_why, "task", _owned_since, None, 1, [])', src, "one owned dispatch counts one (and names no row)")
        self.assertIn('(_stamp_why, _stamp_kind, _stamp_since, _stamp_peers, (len(_stamp_peers) if _stamp_peers else None), _awaiting_peer_items(_stamp_peers))', src,
                      "a stamp counts the peers it names, and lists them as rows")

    def test_every_surface_ships_the_rows_beside_the_count(self):
        # slice 2 (plans/subagent-transcripts.md, 2026-09-05): wherever awaitingKind/awaitingCount ship,
        # the awaited ROWS ship too — the chat status, the timeline lane, the goal card, the placeholder card
        src = inspect.getsource(km)
        self.assertIn('"awaitingItems": (list((_aw or {}).get("items") or []) if awaiting_why else []),', src, "the chat status payload")
        self.assertIn('"awaitingItems": (list((_aw_bg or {}).get("items") or []) if awaiting_bg else []),', src, "the timeline lane payload")
        self.assertIn('"items": await_items,', src, "the goal card's awaiting object")
        self.assertIn('"items": list(items or []),', src, "the placeholder card's awaiting object")
        self.assertIn('count=sess_awaiting_count, items=sess_awaiting_items))', src, "…threaded from the session read")


if __name__ == "__main__":
    unittest.main()
