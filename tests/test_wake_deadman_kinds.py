#!/usr/bin/env python3
"""The wake's clock is a DEAD-MAN'S SWITCH for unobservable waits only (the user 2026-08-24, W1a of
the time-windows package): a LOCAL, ALIVE peer wait takes NO wake at all — its endings are all
events (the pair-aware answer supersede + its durable lift, the awaited peer's death conversion,
the debtor-side debt ladder) — while the named residents keep the clock: kind=job (external
compute), cross-host peers (no local death owner), legacy peer stamps with no recorded identity,
and a dead local peer whose conversion the sweep missed. The peer-death conversion arm itself:
a corroborated death converts every LIVE holder's peer wait ON the dead sid to a procedural block
naming the death (the dead-wait precedent). SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
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
km = SourceFileLoader("romp_kernel_wdk", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-888888888888"    # the wait's holder
PEER = "99999999-aaaa-bbbb-cccc-dddddddddddd"   # the awaited local peer
NOW = 1_787_900_000


class _FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, sid, body):
        self.sent.append((sid, body))


def _stamped(gid, kind=None, peers=None, at=NOW - 20 * 3600):
    nd = {"id": gid, "text": "a goal", "parentId": None, "nodeComplete": False,
          "blocked": False, "cleared": False, "trail": [], "t": 100, "mt": 100,
          "awaitingWhy": "asked the worker which port; holding for the answer", "awaitingAt": at,
          "log": [{"ev_t": at, "src": "closer", "kind": "awaiting",
                   "why": "asked the worker which port; holding for the answer", "at": 1,
                   **({"awaitKind": kind} if kind else {}),
                   **({"awaitPeers": peers} if peers else {})}]}
    if kind:
        nd["awaitingKind"] = kind
    if peers:
        nd["awaitingPeers"] = list(peers)
    return nd


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in
                      ("_session_working", "_log_nudge_event", "_push_all", "_revivers_pending",
                       "_path_of", "_session_awaiting", "_mark_views_dirty")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR, km.jd.parsed_session)
        self.saved_backend = km.Sessions.backend_for
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"; km.jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        (td / "auto-nudge.json").write_text(json.dumps({"enabled": True, "nudged": {}}))
        self.fb = _FakeBackend()
        km.Sessions.backend_for = lambda sid: self.fb
        km._session_working = lambda turns: False
        km._log_nudge_event = lambda *a, **k: None
        km._push_all = lambda *a, **k: None
        km._mark_views_dirty = lambda *a, **k: None
        km._revivers_pending = lambda *a, **k: ""
        km._path_of = lambda sid, now=None: "/p"
        km._session_awaiting = lambda sid, path, idle, stamp=False: None
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

    def _seed(self, **kw):
        nd = _stamped(self.gid, **kw)
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {},
            "status": {self.gid: "working"}, "nodes": {self.gid: nd}}))

    def _wake(self, tmux):
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        store = km.jd.load_goals(SID)
        stamp = km._goal_awaiting_stamp_full(store.get("nodes", {}), self.gid)
        self.assertIsNotNone(stamp)
        return km._wake_goal(SID, self.gid, stamp, dict(km._auto_nudge_data().get("nudged", {})),
                             self.turns, store, NOW, self.turns[-1], tmux)


class DeadmanKinds(_Base):
    def test_a_local_alive_peer_wait_takes_no_wake(self):
        self._seed(kind="peer", peers=[PEER])
        fired = self._wake({SID: {"state": ""}, PEER: {"state": ""}})
        self.assertFalse(fired)
        self.assertEqual(self.fb.sent, [], "every ending is an observable event — no clock at all")

    def test_a_dead_local_peer_falls_to_the_deadman(self):
        self._seed(kind="peer", peers=[PEER])
        self._wake({SID: {"state": ""}})             # the peer is gone from the live map
        self.assertEqual(len(self.fb.sent), 1,
                         "the missed-conversion residue keeps the named dead-man")

    def test_a_cross_host_peer_keeps_the_deadman(self):
        self._seed(kind="peer", peers=["peer:boxa:worker_two"])
        self._wake({SID: {"state": ""}})
        self.assertEqual(len(self.fb.sent), 1, "no local death owner exists cross-host")

    def test_a_legacy_peer_stamp_with_no_identity_keeps_the_deadman(self):
        self._seed(kind="peer")
        self._wake({SID: {"state": ""}})
        self.assertEqual(len(self.fb.sent), 1, "no recorded identity → no event to route; clock stays")

    def test_a_job_wait_keeps_the_deadman(self):
        self._seed(kind="job")
        self._wake({SID: {"state": ""}})
        self.assertEqual(len(self.fb.sent), 1, "external compute: romp sees only the carrier")


class PeerDeathConversion(_Base):
    def test_a_corroborated_death_converts_the_live_holders_wait(self):
        self._seed(kind="peer", peers=[PEER])
        (km.jd.GOALDIR / (PEER + ".json")).write_text(json.dumps(
            {"rompUuid": PEER, "seq": 0, "placements": {}, "status": {}, "nodes": {}}))
        saved = (km._dead_wait_corroborated, km._name_of, getattr(km, "_PREV_ALIVE"))
        km._dead_wait_corroborated = lambda sid, scan=None, stats=None: True
        km._name_of = lambda sid: "worker_two"
        km._PREV_ALIVE = {SID, PEER}
        try:
            km._dead_wait_sweep({SID}, dict(km._auto_nudge_data().get("nudged", {})), NOW)
        finally:
            km._dead_wait_corroborated, km._name_of, km._PREV_ALIVE = saved
        nd = km.jd.load_goals(SID)["nodes"][self.gid]
        self.assertTrue(nd.get("blocked"), "the awaited peer's death is the wait's own ending event")
        self.assertIn("worker_two", nd.get("blockWhy") or "", "…and the block names the dead peer")
        self.assertIn("exited with the ask unanswered", nd.get("blockWhy") or "")


if __name__ == "__main__":
    unittest.main()
