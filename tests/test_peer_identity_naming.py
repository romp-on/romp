#!/usr/bin/env python3
"""Every peer-kind wait names the ACTUAL session (the user 2026-08-26, screenshot of an 'Awaiting
peer · delegated to a peer' chip): 'a peer' is a bug to trace, not a style. The traced class — three
writer arms could ship a peer wait nameless: the session-scoped delegation why resolved through a
bare registry read (so every cross-host "<host>:<name>" composite fell to 'a peer'), the judge-stamp
arm hardcoded peers=None even when the stamp recorded its awaitPeers, and build_feed's handoff scan
filtered on raw nodeComplete while its gate pierced done-markers for agent-open nodes (an empty scan
minted a nameless, durationless wait the gate saw as real). One identity ladder (_peer_identity) now
resolves every recorded shape: the names REGISTRY first — identity persists for DORMANT sessions,
liveness is never a prerequisite for naming — else the composite's own parts (display-join on the
canonical pair, per the federation rule), else the sid stub. All fixtures SYNTHETIC."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_peerid", os.path.join(BIN, "romp-kernel")).load_module()

# PRIVATE synthetic sid (the goal-store fixture rule, CLAUDE.md 2026-08-24): stores minted under the
# shared placeholder sid get re-flagged by other modules' journaled overrides on load_goals replay.
SID = "aaaa1108-bbbb-cccc-dddd-eeeeffff0001"
PEER = "aaaa1108-bbbb-cccc-dddd-eeeeffff0002"   # a local peer with a registry entry (dormant)
FARSID = "aaaa1108-bbbb-cccc-dddd-eeeeffff0003"


def _node(nid, parent=None, complete=False):
    return {"id": nid, "text": "work " + nid, "parentId": parent, "nodeComplete": complete,
            "blocked": False, "cleared": False, "trail": [], "t": 100, "mt": 100, "log": []}


class _Base(unittest.TestCase):
    """Registry + goal store in a temp root; a live-but-idle session so every live source falls
    through to the durable arms (the SessionLevelStamp idiom)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km.NAMES, km._tmux_sessions,
                       km._states_awaiting_overlay)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km.NAMES = td / "names"
        km.NAMES.mkdir()
        # a DORMANT peer's registry entry: written at launch, PERSISTS after the session dies —
        # the authoritative identity source, no liveness involved
        (km.NAMES / PEER).write_text("web\t/tmp/notes-api\t#1EA1EB\twhite\n")
        km._SESSION_STAMP_CACHE.clear()
        km._states_awaiting_overlay = lambda sid: None
        km._tmux_sessions = lambda: {SID: {"state": "", "since": None, "subagents": [], "bgTasks": []}}

    def tearDown(self):
        (km.jd.STATE, km.jd.GOALDIR, km.NAMES, km._tmux_sessions,
         km._states_awaiting_overlay) = self._saved
        km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _seed(self, nodes, status=None):
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 9, "placements": {}, "status": status or {}, "nodes": nodes}))


class PeerIdentityLadder(_Base):
    """_peer_identity — the ONE resolve for every recorded peer shape."""

    def test_registry_names_a_dormant_session(self):
        # no live session anywhere in this fixture: the registry file alone names it, with colours
        self.assertEqual(km._peer_identity(PEER),
                         {"name": "web", "host": "", "sid": PEER,
                          "color": {"bg": "#1EA1EB", "fg": "#ffffff"}})

    def test_cross_host_name_composite_splits_on_the_canonical_pair(self):
        # the courier's remote arm records toName ("<host>:<name>") — display-join, never 'a peer'
        self.assertEqual(km._peer_identity("TESTHOST:api"),
                         {"name": "api", "host": "TESTHOST", "sid": "TESTHOST:api", "color": None})

    def test_wait_map_key_shape_resolves_too(self):
        # the judge's awaitPeers record "peer:<host>:<name>" for an unresolved cross-host recipient
        self.assertEqual(km._peer_identity("peer:TESTHOST:api"),
                         {"name": "api", "host": "TESTHOST", "sid": "TESTHOST:api", "color": None})

    def test_uuid_tail_keeps_the_sid_stub(self):
        got = km._peer_identity("TESTHOST:" + FARSID)
        self.assertEqual((got["name"], got["host"]), (FARSID[:8], "TESTHOST"),
                         "a uuid tail is a sid, not a name — stub it like one")

    def test_bare_unknown_sid_stubs(self):
        got = km._peer_identity(FARSID)
        self.assertEqual((got["name"], got["host"], got["color"]), (FARSID[:8], "", None))


class DelegatedWaitNames(_Base):
    """The session-scoped delegation arm — the traced specimen's writer (_session_delegated_why)."""

    def _delegated(self, peer):
        h = _node("h1", parent="g1")
        h["handoff"] = {"peer": peer, "msgId": "1111111111.00000_00000.TESTHOST"}
        done = _node("s1", parent="g1", complete=True)
        self._seed({"g1": _node("g1"), "s1": done, "h1": h})

    def test_cross_host_delegation_names_the_pair_not_a_peer(self):
        # the specimen's path: handoff.peer = "<host>:<name>"; the bare registry read said 'a peer'
        self._delegated("TESTHOST:api")
        aw = km._session_awaiting(SID, "/p", True, stamp=True)
        self.assertEqual(aw["why"], "delegated to TESTHOST:api; waiting on their result")
        self.assertEqual(aw["peers"],
                         [{"name": "api", "host": "TESTHOST", "sid": "TESTHOST:api", "color": None}])

    def test_local_dormant_peer_names_from_the_registry(self):
        self._delegated(PEER)
        aw = km._session_awaiting(SID, "/p", True, stamp=True)
        self.assertEqual(aw["why"], "delegated to web; waiting on their result")
        self.assertEqual(aw["peers"][0]["color"], {"bg": "#1EA1EB", "fg": "#ffffff"},
                         "identity colour rides from the registry — no live session exists here")


class StampArmNamesPeers(_Base):
    """The judge-stamp arm — its awaitPeers record now reaches every surface as identities."""

    def _stamped(self, kind="peer", peers=(PEER,)):
        g1 = _node("g1")
        g1["awaitingWhy"] = "asked the manager which port"
        g1["awaitingAt"] = 200
        g1["awaitingKind"] = kind
        if peers is not None:
            g1["awaitingPeers"] = list(peers)
        self._seed({"g1": g1})

    def test_goal_reader_carries_the_recorded_keys(self):
        self._stamped()
        self.assertEqual(km._goal_awaiting_stamp_full({"g1": dict(_node("g1"), awaitingWhy="w",
                                                                  awaitingAt=200, awaitingKind="job",
                                                                  awaitingPeers=[PEER])}, "g1"),
                         (200, "w", "job", (PEER,)))

    def test_session_arm_resolves_the_stamp_peers(self):
        self._stamped()
        aw = km._session_awaiting(SID, "/p", True, stamp=True)
        self.assertEqual(aw["kind"], "peer")
        self.assertEqual(aw["peers"][0]["name"], "web")

    def test_non_peer_and_recordless_stamps_keep_the_legacy_shape(self):
        # byte-identical dicts for every arm that has nothing to name — no key, not a null
        self._stamped(kind="job", peers=None)
        self.assertNotIn("peers", km._session_awaiting(SID, "/p", True, stamp=True))
        km._SESSION_STAMP_CACHE.clear()
        self._stamped(kind="peer", peers=None)
        self.assertNotIn("peers", km._session_awaiting(SID, "/p", True, stamp=True))


class OpenLeavesAlignment(_Base):
    """build_feed's handoff scan walks the SAME open set its gate proved non-empty — the raw
    nodeComplete filter it replaces could see nothing where the gate (whose agent-open pierce
    ignores a done marker) saw the delegation, minting a nameless, durationless wait."""

    def test_agent_open_pierced_handoff_is_in_both_reads(self):
        h = _node("h1", parent="g1", complete=True)          # done marker…
        h["agentTask"] = {"status": "open"}                  # …pierced: the agent's own list says open
        h["handoff"] = {"peer": PEER, "msgId": "1111111111.00000_00001.TESTHOST"}
        nodes = {"g1": _node("g1"), "h1": h}
        self.assertTrue(km._all_outstanding_delegated(nodes, "g1"), "the gate fires")
        aligned = [x for x in km._open_leaves(nodes, "g1")
                   if isinstance(nodes[x].get("handoff"), dict)]
        self.assertEqual(aligned, ["h1"], "the aligned scan names the peer the gate saw")
        legacy = [x for x in nodes
                  if isinstance(nodes[x].get("handoff"), dict)
                  and not nodes[x].get("nodeComplete") and not nodes[x].get("cleared")]
        self.assertEqual(legacy, [], "the raw filter this replaces was blind here — the bug window")

    def test_legacy_blank_peer_is_the_only_honest_fallback(self):
        # a record with NO peer sid at all is truly unknowable — identities stay None and the card
        # keeps the 'a peer' prose (with the UI's explanatory tooltip); nothing else may reach it
        h = _node("h1", parent="g1")
        h["handoff"] = {"peer": "", "msgId": "1111111111.00000_00002.TESTHOST"}
        self.assertIsNone(km._handoff_peer_identities({"g1": _node("g1"), "h1": h}, ["h1"]))


if __name__ == "__main__":
    unittest.main()
