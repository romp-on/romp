#!/usr/bin/env python3
"""The JUDGE's durable awaiting stamp reaching the kernel (2026-07-22): the closer's `awaiting` verdict
(kernel/judge.py) lands awaitingWhy/awaitingAt on the goal node — store-backed, so it survives kernel
restarts, where the LIVE awaiting sources (in-memory subagents/bgTasks) go dark. Consumers here:

  * _goal_awaiting_stamp — the subtree scan both the feed floor and the nudge gates share;
  * _mark_nudge_failed — a stamped goal's NUDGE is never converted into a needs-you block (the
    restart-proof twin of the session-level awaiting re-check): this is exactly the false "stalled"
    a genuinely-waiting session showed after a kernel restart. The awaiting WAKE (wake=True) is the
    deliberate exception: it fires BECAUSE of the stamp, so an unanswered wake escalates through it;
  * _wake_goal / _awaiting_wake_outcomes — the wake itself (2026-08-11): the stamped goal's seat in
    the nudge ladder, with the outcome leg the retired one-shot backstop never had.

SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import json
import tempfile
import time
import unittest
import os
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_awstamp", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-999999999999"
NOW = int(time.time())


def _node(nid, parent=None, why=None, at=None, rolled=False):
    nd = {"id": nid, "text": "a goal", "parentId": parent, "nodeComplete": False,
          "blocked": False, "cleared": False, "trail": [], "t": 100, "mt": 100}
    if why:
        nd["awaitingWhy"], nd["awaitingAt"] = why, at or 100
        nd["log"] = [{"ev_t": at or 100, "src": "closer", "kind": "awaiting", "why": why, "at": 1}]
    if rolled:
        nd["rolledUp"] = True
    return nd


class GoalAwaitingStamp(unittest.TestCase):
    def test_finds_the_tops_own_stamp(self):
        nodes = {"g1": _node("g1", why="the sweep it launched; will analyze when done")}
        self.assertEqual(km._goal_awaiting_stamp(nodes, "g1"),
                         "the sweep it launched; will analyze when done")

    def test_freshest_descendant_stamp_wins(self):
        nodes = {"g1": _node("g1"),
                 "s1": _node("s1", parent="g1", why="the older wait", at=100),
                 "s2": _node("s2", parent="g1", why="the newer wait", at=200)}
        self.assertEqual(km._goal_awaiting_stamp(nodes, "g1"), "the newer wait")

    def test_rolled_up_cache_is_not_a_verdict(self):
        # a rolledUp node's flags are tree-derived display state the materialize pass skips — a stale
        # awaitingWhy could sit there forever, so the scan must never read it
        nodes = {"g1": _node("g1"), "s1": _node("s1", parent="g1", why="stale", rolled=True)}
        self.assertIsNone(km._goal_awaiting_stamp(nodes, "g1"))

    def test_none_without_a_stamp_and_other_tops_never_leak(self):
        nodes = {"g1": _node("g1"), "g2": _node("g2", why="someone else's wait")}
        self.assertIsNone(km._goal_awaiting_stamp(nodes, "g1"), "another top's stamp never floors this one")


class NudgeFailedRespectsTheStamp(unittest.TestCase):
    """_mark_nudge_failed re-checks awaiting AT THE WRITE. The live re-check already exists; the stamp
    re-check is its restart-proof twin — after a kernel restart the live sources read None while the
    store still says the goal waits on async work, and without this the fork-nudge floor manufactured
    a false needs-you 'stalled' card on a genuinely-waiting session."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._autonudge_cache.clear()
        self.gid = SID + ":g1"
        km._session_awaiting = lambda sid, path, idle, stamp=False: None   # the LIVE sources are dark (post-restart)
        km._path_of = lambda sid, now=None: "/nonexistent"
        (td / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {self.gid: {"count": 1, "lastTurnId": "t1"}}}))

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of = self._saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _seed(self, why=None):
        nd = _node(self.gid, why=why, at=200)
        nd["text"] = "run the long parameter sweep"
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": self.gid, "placements": {}, "status": {},
            "nodes": {self.gid: nd}}))

    def test_a_stamped_goal_never_gets_the_failure_block(self):
        self._seed(why="the sweep it dispatched; will file results when it lands")
        km._mark_nudge_failed(self.gid)
        store = km.jd.load_goals(SID)
        self.assertFalse(store["nodes"][self.gid]["blocked"],
                         "the judge says the goal awaits async work — a nudge is never converted to a block")
        self.assertFalse(km._auto_nudge_data()["nudged"][self.gid].get("failed"),
                         "the episode isn't failed either — it re-arms cleanly when the wait ends")

    def test_without_a_stamp_the_stall_block_stands(self):
        self._seed(why=None)
        km._mark_nudge_failed(self.gid)
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][self.gid]["blocked"], "the existing stall→block behavior stands")
        self.assertTrue(km._auto_nudge_data()["nudged"][self.gid].get("failed"))


class SessionLevelStamp(unittest.TestCase):
    """Source 2 of _session_awaiting (2026-07-22): the durable stamp reaching the SESSION-scoped surfaces
    (rail chip / chat-view chip / timeline lane) via stamp=True, so a genuinely-awaiting session stays
    green/faded across a kernel restart where the live sources go dark. The FEED passes stamp=False (its own
    per-goal _goal_awaiting_stamp scoping) so one goal's stamp never floors its siblings."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions, km._states_awaiting_overlay)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear()
        km._states_awaiting_overlay = lambda sid: None
        # a LIVE snapshot with an EMPTY bg-task set (SDK-style): sources 0-1 find nothing and fall through to
        # the stamp; the present "bgTasks" key means source 0.75 (transcript pairing) is skipped as well
        km._tmux_sessions = lambda: {SID: {"state": "", "since": None, "subagents": [], "bgTasks": []}}

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions, km._states_awaiting_overlay = self._saved
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _seed(self, *stamps):
        nodes = {nid: _node(nid, why=why, at=at) for nid, why, at in stamps}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": nodes}))

    def test_a_stamped_kind_rides_the_session_signal(self):
        # the judge classified WHAT the wait is on (jd.AWAIT_KINDS); the kind travels as data beside
        # the why so surfaces can word it and rules can scope by it (the user 2026-08-15)
        nodes = {"g1": _node("g1", why="slurm 4821 regenerating the parts", at=200)}
        nodes["g1"]["awaitingKind"] = "job"
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": nodes}))
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": "job", "why": "slurm 4821 regenerating the parts",
                          "since": 200})   # the stamp's awaitingAt → the chips' elapsed readout (the user 2026-08-23)
        self.assertEqual(km._session_stamp_full(SID),
                         ("g1", 200, "slurm 4821 regenerating the parts", "job", ()))

    def test_session_stamp_takes_the_freshest_across_ALL_tops(self):
        # session-level, so it scans every goal (not one subtree like _goal_awaiting_stamp) for the newest
        self._seed(("g1", "the older wait, padded", 100), ("g2", "the newer wait", 300))
        self.assertEqual(km._session_stamp_full(SID)[2], "the newer wait")

    def test_stamp_true_lifts_a_live_session_whose_live_sources_are_dark(self):
        self._seed(("g1", "the watcher it armed; files the clip when it triggers", 200))
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": None, "since": 200,
                          "why": "the watcher it armed; files the clip when it triggers"})

    def test_stamp_false_stays_none_so_the_feed_scopes_per_goal(self):
        # the crux: the feed calls stamp=False, so the session-level signal is None for a stamp-only session
        # and _await_ok can never floor a SIBLING working goal — only _goal_awaiting_stamp floors the one goal
        self._seed(("g1", "some async wait", 200))
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=False))

    def test_a_dormant_session_never_resurrects_off_a_stale_stamp(self):
        self._seed(("g1", "a wait whose CLI is gone", 200))
        km._tmux_sessions = lambda: {}          # SID not in the live set → live is None
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True))

    def test_an_open_turn_is_working_not_awaiting_even_with_a_stamp(self):
        self._seed(("g1", "async wait", 200))
        self.assertIsNone(km._session_awaiting(SID, "/p", False, stamp=True), "idle=False short-circuits")

    def test_the_chip_reads_awaitingBg_for_a_stamp_only_live_session(self):
        # end to end: no live source, only the stamp → the shared _session_chip derivation still says awaiting
        self._seed(("g1", "the watcher it armed", 200))
        saved = (km._session_working, km._api_error, km._compacting, km._interrupting)
        km._session_working = lambda turns: False
        km._api_error = lambda path: None
        km._compacting = lambda *a, **k: False
        km._interrupting = lambda *a, **k: False
        try:
            chip = km._session_chip(SID, "/p", {"turns": []}, km._tmux_sessions()[SID], NOW)
        finally:
            km._session_working, km._api_error, km._compacting, km._interrupting = saved
        self.assertEqual(chip, "awaitingBg")

    def test_the_cache_invalidates_when_the_store_changes(self):
        self._seed(("g1", "first wait", 200))
        self.assertEqual(km._session_stamp_full(SID)[2], "first wait")
        self._seed(("g1", "second wait, a different length so size differs", 300))
        self.assertEqual(km._session_stamp_full(SID)[2], "second wait, a different length so size differs")


class SessionLevelDelegation(unittest.TestCase):
    """Source 2.5 of _session_awaiting (the user 2026-08-08, who saw three surfaces answer one question
    two ways): a session whose only outstanding work is a courier HANDOFF wore the feed's green awaiting
    dot (the card flavor reads _deleg_why off the handoff graph) while the rail chip, chat chip, and
    timeline lane — which read _session_awaiting, blind to delegation — said plain ready. The delegation
    evidence now reaches the session-scoped surfaces through the same stamp-gated branch, computed in
    _session_stamp_read's one cached store pass."""

    PEER = "33333333-4444-5555-6666-777777777777"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions,
                       km._states_awaiting_overlay, km._name_of)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear()
        km._states_awaiting_overlay = lambda sid: None
        km._name_of = lambda s: "probe" if s == self.PEER else None
        # LIVE snapshot, empty bg sets (SDK-style): every live source falls through, like SessionLevelStamp
        km._tmux_sessions = lambda: {SID: {"state": "", "since": None, "subagents": [], "bgTasks": []}}

    def tearDown(self):
        (km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions,
         km._states_awaiting_overlay, km._name_of) = self._saved
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _seed(self, nodes):
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": nodes}))

    def _handoff(self, nid, parent, complete=False):
        nd = _node(nid, parent=parent)
        nd["handoff"] = {"peer": self.PEER, "msgId": "1111111111.00000_00000.TESTHOST"}
        nd["nodeComplete"] = complete
        return nd

    def _delegated_store(self):
        """A top with one COMPLETED own-work leaf and one OPEN handoff — the synth shape: the card shows
        (not pure delegation), and its only open work is the peer's."""
        done = _node("s1", parent="g1"); done["nodeComplete"] = True
        return {"g1": _node("g1"), "s1": done, "h1": self._handoff("h1", "g1")}

    def test_a_fully_delegated_session_reads_awaiting_on_the_session_surfaces(self):
        self._seed(self._delegated_store())
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": "peer", "why": "delegated to probe; waiting on their result",
                          "since": None,   # the handoff graph has no single event time here → no duration
                          "peers": [{"name": "probe", "host": "", "sid": self.PEER, "color": None}]})

    def test_handoff_peer_identities_carry_name_host_and_colour_for_the_card(self):
        # the card's awaiting box names the peers the way the origin line does (the user 2026-08-23):
        # identity colour + quiet host: prefix. The helper resolves the live registry name first; a
        # sid it cannot resolve keeps federation's recorded host marker and falls to the sid stub.
        saved = km._name_color
        km._name_color = lambda s: {"bg": "#abc", "fg": "#000"}
        try:
            far = "farhost:88888888-9999-aaaa-bbbb-cccccccccccc"
            nodes = self._delegated_store()
            h2 = self._handoff("h2", "g1"); h2["handoff"]["peer"] = far
            h3 = self._handoff("h3", "g1")                       # duplicate peer → one identity
            nodes.update({"h2": h2, "h3": h3})
            got = km._handoff_peer_identities(nodes, ["h1", "h2", "h3"])
            self.assertEqual(got, [
                {"name": "88888888", "host": "farhost", "sid": far, "color": {"bg": "#abc", "fg": "#000"}},
                {"name": "probe", "host": "", "sid": self.PEER, "color": {"bg": "#abc", "fg": "#000"}},
            ])
            self.assertIsNone(km._handoff_peer_identities(nodes, []), "no handoffs, no list — never []")
        finally:
            km._name_color = saved

    def test_stamp_false_stays_none_so_the_feed_keeps_scoping_per_card(self):
        self._seed(self._delegated_store())
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=False))

    def test_the_judge_stamp_outranks_delegation_like_the_feeds_own_precedence(self):
        nodes = self._delegated_store()
        nodes["g1"]["awaitingWhy"], nodes["g1"]["awaitingAt"] = "the sweep it launched", 200
        self._seed(nodes)
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": None, "why": "the sweep it launched", "since": 200})

    def test_a_pure_delegation_top_stays_dark_matching_its_suppressed_card(self):
        # EVERY leaf a handoff → the feed suppresses the card in every column, so its dot never lights;
        # the session surfaces must not say MORE than the feed does
        self._seed({"g1": _node("g1"), "h1": self._handoff("h1", "g1")})
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True))

    def test_a_completed_handoff_ends_the_wait_on_the_graphs_own_event(self):
        nodes = self._delegated_store()
        nodes["h1"]["nodeComplete"] = True             # run_propagate checked it off — peer delivered
        self._seed(nodes)
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True))

    def test_own_open_work_keeps_the_session_plain_not_awaiting(self):
        nodes = self._delegated_store()
        own = _node("s2", parent="g1")                 # an open OWN leaf → the session can still act
        nodes["s2"] = own
        self._seed(nodes)
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True))

    def test_a_dormant_session_never_lights_off_the_graph(self):
        self._seed(self._delegated_store())
        km._tmux_sessions = lambda: {}
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True))

    def test_the_chip_reads_awaitingBg_end_to_end(self):
        # the shared _session_chip derivation (chat chip AND timeline lane) lights off the same arm
        self._seed(self._delegated_store())
        saved = (km._session_working, km._api_error, km._compacting, km._interrupting)
        km._session_working = lambda turns: False
        km._api_error = lambda path: None
        km._compacting = lambda *a, **k: False
        km._interrupting = lambda *a, **k: False
        try:
            chip = km._session_chip(SID, "/p", {"turns": []}, km._tmux_sessions()[SID], NOW)
        finally:
            km._session_working, km._api_error, km._compacting, km._interrupting = saved
        self.assertEqual(chip, "awaitingBg")


class KindScopedRules(unittest.TestCase):
    """The kind-scoped rules (the user 2026-08-15): a peer's answer supersedes only PEER waits (kindless
    keeps the legacy trade); the session-level stamp pick takes only stamps whose TOP still rolls up
    working (the wake ladder's own gate), while the stamped-TOPS set stays status-blind for _bg_split."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions, km._states_awaiting_overlay,
                       km._peer_answered_at)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear()
        km._states_awaiting_overlay = lambda sid: None
        km._peer_answered_at = lambda sid: 900          # a peer exchange answered AFTER every stamp below
        km._tmux_sessions = lambda: {SID: {"state": "", "since": None, "subagents": [], "bgTasks": []}}

    def tearDown(self):
        (km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions, km._states_awaiting_overlay,
         km._peer_answered_at) = self._saved
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _seed(self, kind, status=None):
        nodes = {"g1": _node("g1", why="the wait", at=200)}
        if kind:
            nodes["g1"]["awaitingKind"] = kind
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": status or {}, "nodes": nodes}))
        km._SESSION_STAMP_CACHE.clear()

    def test_a_peer_answer_supersedes_only_peer_waits(self):
        self._seed("job")
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": "job", "why": "the wait", "since": 200},
                         "unrelated mail cannot end a wait on an external job")
        self._seed("peer")
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True),
                          "a peer wait IS what the answer ends")
        self._seed(None)
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True),
                          "a kindless stamp keeps the legacy supersede — it may well be a peer wait")

    def test_the_goal_level_read_scopes_the_same_way(self):
        nodes = {"g1": dict(_node("g1", why="the wait", at=200), awaitingKind="job")}
        self.assertEqual(km._goal_awaiting_stamp_full(nodes, "g1", answered_at=900),
                         (200, "the wait", "job", ()))
        nodes["g1"]["awaitingKind"] = "peer"
        self.assertIsNone(km._goal_awaiting_stamp_full(nodes, "g1", answered_at=900))
        nodes["g1"].pop("awaitingKind")
        self.assertIsNone(km._goal_awaiting_stamp_full(nodes, "g1", answered_at=900),
                          "kindless keeps the legacy supersede at the goal level too")

    def test_the_session_pick_takes_working_tops_only(self):
        km._peer_answered_at = lambda sid: 0
        self._seed("job", status={"g1": "blocked"})
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True),
                          "a stamp under a blocked top has no wake ladder behind it — the chip may not"
                          " claim a wait the feed and the wake both disown")
        self.assertEqual(km._session_stamped_tops(SID), frozenset({"g1"}),
                         "the classifier's tops set stays status-blind: the top's live task is still"
                         " awaited while the block resolves")


class OverlayDoesNotVeto(unittest.TestCase):
    """The production regression (the user 2026-07-27): the SDK Stop hook writes awaiting:false at EVERY
    turn end, and nothing has written true since 2026-07-07 — so every real SDK session carries a trailing
    false overlay row. That row is ambient noise, not an answer: it must fall THROUGH to the durable
    stamp, never veto it (the veto made source 2 unreachable fleet-wide; when bf55a2f narrowed the live
    sources on 2026-07-24 the awaiting badge visibly vanished). The classes above stub
    _states_awaiting_overlay to None — precisely the condition production never hits — so this class
    runs the REAL reader over a real states file."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        (td / "states").mkdir()
        km._SESSION_STAMP_CACHE.clear()
        km._tmux_sessions = lambda: {SID: {"state": "", "since": None, "subagents": [], "bgTasks": []}}

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR, km._tmux_sessions = self._saved
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _overlay(self, *rows):
        (km.jd.STATE / "states" / (SID + ".jsonl")).write_text(
            "".join(json.dumps(r) + "\n" for r in rows))

    def _seed(self, why="a dispatched release watch; tags when green"):
        nodes = {"g1": _node("g1", why=why, at=200)}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": nodes}))

    def test_a_bare_false_row_falls_through_to_the_stamp(self):
        self._overlay({"t": 100, "awaiting": False})
        self._seed()
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": None, "why": "a dispatched release watch; tags when green",
                          "since": 200},
                         "the Stop hook's ambient false must not hide the judge's stamp")

    def test_a_live_true_row_still_wins_with_its_own_why(self):
        self._overlay({"t": 100, "awaiting": True, "why": "a job the hook reported"})
        self._seed()
        self.assertEqual(km._session_awaiting(SID, "/p", True, stamp=True),
                         {"kind": None, "why": "a job the hook reported", "since": 100},
                         "a positive overlay row keeps its channel")

    def test_false_row_and_no_stamp_is_plain_none(self):
        self._overlay({"t": 100, "awaiting": False})
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {}}))
        self.assertIsNone(km._session_awaiting(SID, "/p", True, stamp=True))


class _FakeBackend:
    def __init__(self): self.sent = []
    def send(self, sid, body): self.sent.append((sid, body))
    def pending_queued(self, sid): return False       # → _backend_queued False; no pending_cut → rewind False


class AwaitingWake(unittest.TestCase):
    """The awaiting WAKE (2026-08-11, replacing the one-shot backstop): a stamped goal past the 6h window
    takes a check-in through the NUDGE ladder — recorded in the shared `nudged` ledger, its response judged
    by the same gates, and a wake NOBODY answers escalated to a real block (Needs-you). The one-shot design
    marked itself spent at SEND time keyed on the stamp anchor, so a wake whose response turn died (an API
    error) left the card asleep in Working forever: the anchor never moves (identical re-asserts coalesce)
    and the stamp also stood down the whole ladder. Episodes re-arm on the ANSWER, not the anchor.
    SYNTHETIC fixtures only."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in
                      ("_session_working", "_log_nudge_event", "_push_all", "_revivers_pending",
                       "_peer_answered_at", "_path_of", "_session_awaiting")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR, km.jd.parsed_session)
        self.saved_backend = km.Sessions.backend_for
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"; km.jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        (td / "auto-nudge.json").write_text(json.dumps({"enabled": True, "nudged": {}}))
        self.fb = _FakeBackend()
        km.Sessions.backend_for = lambda sid: self.fb
        km._session_working = lambda turns: False           # idle by default
        km._log_nudge_event = lambda *a, **k: None
        km._push_all = lambda *a, **k: None
        km._revivers_pending = lambda *a, **k: ""           # no other reviver holds the wake
        km._peer_answered_at = lambda sid: 0
        km._path_of = lambda sid, now=None: "/p"
        km._session_awaiting = lambda sid, path, idle, stamp=False: None
        km._pending_ops.pop(SID, None)
        self.gid = SID + ":g1"
        self.turns = [{"id": "t1", "ended": True, "end": 100, "t": 90, "atoms": []}]
        km.jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km.jd.STATE, km.jd.GOALDIR, km.jd.parsed_session = self.saved_jd
        km.Sessions.backend_for = self.saved_backend
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        self.td.cleanup()

    def _seed(self, at, row_at=1):
        nd = _node(self.gid, why="the trace it dispatched; reports when it returns", at=at)
        nd["log"][0]["at"] = row_at                  # when the closer FILED the stamp (arrival time)
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))

    def _wake(self, now, rec=None, tmux=None):
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()   # deterministic: never a stale cache
        if rec is not None:
            d = json.loads((Path(self.td.name) / "auto-nudge.json").read_text())
            d["nudged"] = {self.gid: rec}
            (Path(self.td.name) / "auto-nudge.json").write_text(json.dumps(d))
        store = km.jd.load_goals(SID)
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        stamp = km._goal_awaiting_stamp_full(store.get("nodes", {}), self.gid)
        self.assertIsNotNone(stamp, "fixture: the goal must be stamped")
        out = km._wake_goal(SID, self.gid, stamp, nudged, self.turns, store, now,
                            self.turns[-1], {SID: {"state": ""}} if tmux is None else tmux)
        km._autonudge_cache.clear()
        return out

    def test_stamp_full_exposes_gid_and_at(self):
        self._seed(at=500)
        self.assertEqual(km._session_stamp_full(SID),
                         (self.gid, 500, "the trace it dispatched; reports when it returns", None, ()))

    def test_fires_past_the_window_and_records_the_episode(self):
        now = 1_000_000
        self._seed(at=now - 7 * 3600)                # older than the 6h window
        self.assertTrue(self._wake(now))
        self.assertEqual(len(self.fb.sent), 1, "a stamp open past the window gets a wake")
        rec = km._auto_nudge_data()["nudged"][self.gid]
        self.assertTrue(rec.get("wake"))
        self.assertEqual(rec.get("anchor"), now - 7 * 3600)
        self.assertEqual(rec.get("at"), now)
        self.assertEqual(rec.get("lastTurnId"), "t1")

    def test_stays_patient_inside_the_window(self):
        now = 1_000_000
        self._seed(at=now - 3600)                    # only an hour old
        self.assertFalse(self._wake(now))
        self.assertEqual(self.fb.sent, [], "a legitimate wait inside the window is left alone")

    def test_in_flight_wake_does_not_refire(self):
        now = 1_000_000
        self._seed(at=now - 20 * 3600)
        rec = {"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t1",
               "armAtoms": 0, "at": now - 3600}      # fired an hour ago, response not judged yet
        self.assertFalse(self._wake(now, rec=rec))
        self.assertEqual(self.fb.sent, [], "one wake in flight — never a second while unjudged")

    def test_silent_wake_escalates_to_a_needs_you_block(self):
        # THE 2026-08-11 ui wedge: wake fired, its response turn died on an API error, nothing ever
        # landed — under the one-shot design the spent anchor-mark slept forever and the stamp stood
        # down the ladder. Now: past the window with no response, the wake fails and files a real block.
        now = 1_000_000
        self._seed(at=now - 20 * 3600)
        rec = {"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t0",
               "armAtoms": 0, "at": now - 7 * 3600}  # fired 7h ago, nothing since
        self.assertTrue(self._wake(now, rec=rec))
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][self.gid]["blocked"],
                        "an unanswered wake IS a block — the card must reach Needs-you")
        self.assertEqual(store["nodes"][self.gid].get("blockWhy"), km.jd.WAKE_BLOCK_WHY)
        self.assertTrue(km._auto_nudge_data()["nudged"][self.gid].get("failed"))

    def test_silent_escalation_evidence_is_the_fire_not_the_stale_turn(self):
        # The stamp's own diary row is FILED (at) after the sleeping session's last turn END — anchoring
        # the block at the turn end would let the moot guard read the stamp itself as "a judge ruled
        # after the evidence" and the escalation would stand itself down on the very stamp it fired for.
        now = 1_000_000
        fire = now - 7 * 3600
        self._seed(at=now - 20 * 3600, row_at=5000)  # stamp filed AFTER the turn end (100), before the fire
        rec = {"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t0",
               "armAtoms": 0, "at": fire}
        self.assertTrue(self._wake(now, rec=rec), "the stamp row must not moot the silent escalation")
        nd = km.jd.load_goals(SID)["nodes"][self.gid]
        rows = [e for e in nd["log"] if e.get("kind") == "block"]
        self.assertEqual(rows[-1].get("ev_t"), fire, "the block's evidence time is the unanswered fire")

    def test_answered_wake_re_arms_from_the_answer(self):
        now = 1_000_000
        self._seed(at=now - 20 * 3600)
        rec = {"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t1",
               "armAtoms": 0, "at": now - 7 * 3600}
        saved = km._nudge_response_ready
        km._nudge_response_ready = lambda *a, **k: (True, {"id": "s9", "t": now - 6 * 3600})
        try:
            self.assertFalse(self._wake(now, rec=rec), "an answered wake never escalates")
        finally:
            km._nudge_response_ready = saved
        rec2 = km._auto_nudge_data()["nudged"][self.gid]
        self.assertEqual(rec2.get("answeredAt"), now - 6 * 3600,
                         "the episode re-arms from the ANSWER — the anchor may never move (coalesced "
                         "re-asserts keep the original stamp), so it cannot be the episode key")
        self.assertFalse(km.jd.load_goals(SID)["nodes"][self.gid]["blocked"])
        # THE ANSWER IS A FILED EVENT (the user 2026-08-25, C2 — closing the awaiting audit's last
        # live mechanism): the response segment was often placed under NO goal, so an answered wake
        # re-affirmed a dead wait forever with nothing ever re-nominating the closer. The answered
        # leg now files a same-why re-assert AT THE ORIGINAL ANCHOR — the anchor never moves, but
        # the row's ARRIVAL opens the closer's filed-since gate, so it re-audits with the answer in
        # view; the re-affirm-forever shape is impossible by construction.
        nd = km.jd.load_goals(SID)["nodes"][self.gid]
        filed = [e for e in nd["log"] if e.get("src") == "nudge" and e.get("kind") == "awaiting"
                 and not e.get("lift")]
        self.assertEqual(len(filed), 1, "the answered wake filed its outcome into the diary")
        self.assertEqual(filed[0].get("ev_t"), now - 20 * 3600, "…at the frozen anchor (no churn)")
        self.assertEqual(nd.get("awaitingAt"), now - 20 * 3600, "the stamp's anchor never moved")
        look = {"closerLookT": nd["log"][0]["at"]}    # the closer last looked when the stamp filed
        kids = {None: [self.gid]}
        self.assertTrue(km.jd._filed_since({self.gid: dict(nd, **look)}, kids, self.gid,
                                           km.jd._look_stamp(dict(nd, **look))),
                        "…and the filing re-nominates: the closer's gate opens on the answer")
        # ...and the next wake fires once the ANSWER goes stale, same stamp anchor throughout
        self.assertFalse(self._wake(now - 6 * 3600 + 60, rec=rec2), "still patient after the answer")
        self.assertTrue(self._wake(now + 3600, rec=rec2), "re-armed: 6h past the answer it asks again")

    def test_failed_episode_never_refires_until_a_new_anchor(self):
        now = 1_000_000
        anchor = now - 20 * 3600
        self._seed(at=anchor)
        rec = {"wake": True, "anchor": anchor, "count": 1, "lastTurnId": "t0",
               "armAtoms": 0, "at": now - 8 * 3600, "failed": True, "failedAt": now - 3600}
        self.assertFalse(self._wake(now, rec=rec))
        self.assertEqual(self.fb.sent, [], "a failed episode is settled — the anti-loop rule")
        self._seed(at=now - 7 * 3600)                # the closer filed a genuinely NEW wait
        self.assertTrue(self._wake(now, rec=rec), "a fresh anchor re-arms the wake")

    def test_dormant_session_is_not_woken_but_converts_to_the_dead_wait_block(self):
        # (the user 2026-08-22) a dormant CLI still gets no WAKE — its dispatched work is gone, not
        # asleep — but the branch no longer dead-ends: the stamped Working card converts once to the
        # dead-wait procedural block, so it reaches a terminal column instead of pausing forever.
        # The conversion is owner-corroborated: the session carries its launch record (the names
        # entry both backends write — without it no owner here could answer for the sid), and the
        # owner scan is pinned to an authoritative empty answer rather than this box's real tmux.
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        (km.jd.NAMES / SID).write_text("web\t~/notes-api\t#3355aa\t#ffffff\n")
        self.addCleanup(lambda: (km.jd.NAMES / SID).unlink())
        km._TMUX.available = lambda: True
        km._TMUX.alive_sids = lambda t=3: set()
        self.addCleanup(lambda: [km._TMUX.__dict__.pop(nm, None)
                                 for nm in ("available", "alive_sids")])
        now = 1_000_000
        self._seed(at=now - 7 * 3600)
        (km.jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "states" / (SID + ".jsonl")).write_text(
            json.dumps({"state": "idle", "t": now - 6 * 3600}) + "\n")
        self.assertTrue(self._wake(now, tmux={}), "the conversion fired (the tick pushes once)")
        self.assertEqual(self.fb.sent, [], "no wake message: nothing that could answer is running")
        nd = km.jd.load_goals(SID)["nodes"][self.gid]
        self.assertTrue(nd.get("blocked"), "the card lands in Blocked, the ladder's promised terminal")
        self.assertTrue(str(nd.get("blockWhy") or "").startswith(km.jd.DEAD_WAIT_WHY_PREFIX))

    def test_wake_that_judges_resolved_mid_tick_stands_down(self):
        now = 1_000_000
        self._seed(at=now - 7 * 3600)
        store = km.jd.load_goals(SID)
        stamp = km._goal_awaiting_stamp_full(store["nodes"], self.gid)
        # the fresh-store re-read at send time sees the goal already blocked → no wake
        d = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())
        d["status"] = {self.gid: "blocked"}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(d))
        self.assertFalse(km._wake_goal(SID, self.gid, stamp, {}, self.turns, store, now,
                                       self.turns[-1], {SID: {"state": ""}}))
        self.assertEqual(self.fb.sent, [], "the last-moment store re-read keys on the judges' landing")


class AwaitingWakeOutcomeSweep(unittest.TestCase):
    """_awaiting_wake_outcomes: the outcome leg for wake records whose sessions the goal walk can't reach.
    A dead wake leaves its session in EXACTLY a walk-gated state (the ui case: the response turn died on an
    API error, so the _api_error session gate skipped every later evaluation) — the sweep escalates a wake
    past the window with no response regardless of session gates, and re-arms an answered one."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in
                      ("_session_working", "_path_of", "_session_awaiting", "_peer_answered_at",
                       "_log_nudge_event", "_push_all")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR, km.jd.parsed_session)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"; km.jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        km._session_working = lambda turns: False
        km._path_of = lambda sid, now=None: "/p"
        km._session_awaiting = lambda sid, path, idle, stamp=False: None
        km._peer_answered_at = lambda sid: 0
        km._log_nudge_event = lambda *a, **k: None
        km._push_all = lambda *a, **k: None
        self.gid = SID + ":g1"
        self.turns = [{"id": "t1", "ended": True, "end": 100, "t": 90, "atoms": []}]
        km.jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km.jd.STATE, km.jd.GOALDIR, km.jd.parsed_session = self.saved_jd
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        self.td.cleanup()

    def _seed_goal(self, at):
        nd = _node(self.gid, why="the reinstaller it detached; reports when the box is quiet", at=at)
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))

    def _seed_rec(self, rec):
        (Path(self.td.name) / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {self.gid: rec}}))
        km._autonudge_cache.clear()

    def test_sweep_escalates_a_silent_wake_the_walk_cannot_reach(self):
        now = 1_000_000
        self._seed_goal(at=now - 20 * 3600)
        self._seed_rec({"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t0",
                        "armAtoms": 0, "at": now - 7 * 3600})
        self.assertTrue(km._awaiting_wake_outcomes(now))
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][self.gid]["blocked"],
                        "the sweep runs without the walk's session gates — an api-errored session's dead "
                        "wake still surfaces")
        self.assertEqual(store["nodes"][self.gid].get("blockWhy"), km.jd.WAKE_BLOCK_WHY)

    def test_sweep_leaves_a_walked_ungated_wake_to_the_walk(self):
        # ownership is the JOURNALED gate + the walked roster now, never age (the user 2026-08-24,
        # W1b): the walk visited this session and stood down on no gate, so the record is the
        # walk's — at ANY age (the retired 6h clock is not resurrected by this sweep)
        now = 1_000_000
        self._seed_goal(at=now - 20 * 3600)
        self._seed_rec({"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t1",
                        "armAtoms": 0, "at": now - 3600})
        self.assertFalse(km._awaiting_wake_outcomes(now, walked={SID}))
        self.assertFalse(km.jd.load_goals(SID)["nodes"][self.gid]["blocked"])

    def test_sweep_ignores_a_goal_the_world_moved_past(self):
        now = 1_000_000
        self._seed_goal(at=now - 20 * 3600)
        d = km.jd._guard_nodes(json.loads((km.jd.GOALDIR / (SID + ".json")).read_text()))
        # a COHERENT completed goal (one truth, 2026-08-13): completion is a VERDICT in the log —
        # rollup re-derives the flags from it — never a bare hand-set nodeComplete
        km.jd.record_verdict(d, d["nodes"][self.gid], "closer", "done", now - 3600, why="test done")
        km.jd.rollup_status(d, session_closed=False)
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(d))
        self._seed_rec({"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t0",
                        "armAtoms": 0, "at": now - 7 * 3600})
        self.assertFalse(km._awaiting_wake_outcomes(now))
        self.assertFalse(km.jd.load_goals(SID)["nodes"][self.gid].get("blocked"),
                         "a completed goal's stale record is inert")

    def test_sweep_re_arms_an_answered_wake_the_walk_never_saw(self):
        now = 1_000_000
        self._seed_goal(at=now - 20 * 3600)
        self._seed_rec({"wake": True, "anchor": now - 20 * 3600, "count": 1, "lastTurnId": "t1",
                        "armAtoms": 0, "at": now - 7 * 3600})
        saved = km._nudge_response_ready
        km._nudge_response_ready = lambda *a, **k: (True, {"id": "s9", "t": now - 6 * 3600})
        try:
            self.assertFalse(km._awaiting_wake_outcomes(now))
        finally:
            km._nudge_response_ready = saved
        self.assertEqual(km._auto_nudge_data()["nudged"][self.gid].get("answeredAt"), now - 6 * 3600)
        self.assertFalse(km.jd.load_goals(SID)["nodes"][self.gid]["blocked"])


class WakeBodyKeepsItsCopy(unittest.TestCase):
    """_followup_body(wake=True): the wake's ask survives the hierarchical enumeration branch. The generic
    status ask invites an answer from memory — the audited session twice reassured from memory that its
    wait was deliberate while the pid its stamp named was long dead — and the enumeration branch was
    silently REPLACING the wake's 'go check the background work' body with exactly that generic ask
    (the user 2026-08-11)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"; km.jd.GOALDIR.mkdir(parents=True)
        self.gid = SID + ":g1"
        nodes = {self.gid: _node(self.gid),
                 SID + ":s1": _node(SID + ":s1", parent=self.gid),
                 SID + ":s2": _node(SID + ":s2", parent=self.gid)}
        nodes[self.gid]["text"] = "fix the pusher burn"
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": nodes}))

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        self.td.cleanup()

    def test_wake_body_survives_the_hierarchical_branch(self):
        body = km._followup_body(self.gid, None, km.AWAITING_BACKSTOP_TEXT,
                                 injected=True, auto=True, wake=True)
        self.assertIn("Still open on this:", body, "the enumerated quote still rides along")
        self.assertIn(km.AWAITING_BACKSTOP_TEXT, body, "the wake's own ask is never replaced")
        self.assertNotIn("Where does each of those stand?", body)

    def test_plain_nudge_keeps_the_status_ask(self):
        body = km._followup_body(self.gid, None, km.AUTO_NUDGE_TEXT, injected=True, auto=True)
        self.assertIn("Where does each of those stand?", body,
                      "the regular nudge's hierarchical replacement is unchanged")


if __name__ == "__main__":
    unittest.main()


class SupersedeKeysOnWriteTime(unittest.TestCase):
    """The peer-answer supersede keys on the stamp's WRITE time, not its anchor (2026-08-19 audit):
    awaitingAt is the audited turn's TRIGGER, which predates the very replies that turn solicited —
    so a fresh re-stamp was superseded the instant it was filed, contradicting the machinery's own
    'a stamp filed AFTER the reply survives' contract."""

    def _nodes(self, anchor, written, kind="peer"):
        return {"g1": {"id": "g1", "parentId": None, "awaitingWhy": "waiting on the peer's report",
                       "awaitingAt": anchor, "awaitingKind": kind,
                       "log": [{"ev_t": anchor, "at": written, "src": "closer", "kind": "awaiting",
                                "why": "waiting on the peer's report", "awaitKind": kind}]}}

    def test_a_stamp_written_after_the_reply_survives(self):
        # anchor 100 (trigger), reply 150, stamp WRITTEN 200: the closer knew the reply — stamp stands
        got = km._goal_awaiting_stamp_full(self._nodes(100, 200), "g1", answered_at=150)
        self.assertIsNotNone(got, "written after the reply → the ruling already weighed it")

    def test_a_stamp_written_before_the_reply_is_superseded(self):
        got = km._goal_awaiting_stamp_full(self._nodes(100, 120), "g1", answered_at=150)
        self.assertIsNone(got, "the reply IS the awaited event — the stamp yields")

    def test_job_stamps_never_yield_to_mail(self):
        got = km._goal_awaiting_stamp_full(self._nodes(100, 120, kind="job"), "g1", answered_at=150)
        self.assertIsNotNone(got, "peer-scoped: a slurm wait keeps standing through unrelated mail")
