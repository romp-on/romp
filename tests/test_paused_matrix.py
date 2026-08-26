#!/usr/bin/env python3
"""The PAUSED-CARD matrix (the user 2026-08-24, after three "Paused — resumes on the session's next
turn" cards sat stuck for hours on LIVE sessions): every mint path for the paused presentation x
every retire path, pinned synthetically — the live-owner cousin of tests/test_awaiting_peer_matrix.py.

The paused caption is the feed's quiet FLOOR (ui/webview/spin-caption.ts), rendered for a
Working-column card with no richer story on a session between turns. The mint under test is the
READ-TIME peer-answer supersede: a peer's reply at/after a kind=peer/kindless stamp's WRITE time
hides the stamp from every reader (_goal_awaiting_stamp_full's answered_at guard), which drops the
card's awaiting story AND disarms the 6h wake (the nudge walk reads the same predicate) — but the
supersede filed NOTHING, so the stamp sat invisibly forever: the closer's filed-since nomination
never re-armed (the newest diary row was the stamp itself), and the plain-nudge fire gate read the
RAW node fields and vetoed every fire, recordless. A LIVE session's card had no reviver left,
breaking the recorded 2026-08-22 promise that every Working card is nudged/woken until it lands in
Completed or Blocked (_dead_wait_block's docstring). Retires under test, both ends of the fix:
  * _lift_spent_awaiting's superseded-peer arm files the DURABLE lift — the peer's answer is the
    wait's designed exact ending event — re-arming the closer (a filing past closerLookT) and the
    escalation ladder (the spent-ledger drop), peer-scoped exactly like the reader;
  * _nudge_fire_list's stamp veto applies the same answered_at read as the walk and the wake's own
    re-check — a hidden stamp cannot veto the ladder it already disarmed;
  * dormant owners are untouched: the dead-wait conversion reads the stamp RAW on purpose and owns
    that ending.
All fixtures SYNTHETIC: placeholder UUIDs, invented prose."""
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
km = SourceFileLoader("romp_kernel_pausedmx", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-666666666666"   # the session under test
PEER = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"  # the peer it delegated to / asked
BORN, STAMP_EV, STAMP_AT = 100, 300, 360       # goal minted / stamp anchor (ev_t) / stamp WRITE (at)
REPLY = 500                                    # the peer's answer (postdates the write → supersedes)
NOW = 9_000


def _msg(i, f, t_, ts, kind):
    return json.dumps({"id": "m%d" % i, "ev": "sent", "from": "web", "from_id": f,
                       "to_id": t_, "t": ts, "kind": kind, "body": "x"})


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self._saved_fns = {k: getattr(km, k) for k in ("_alive_sessions", "_mark_views_dirty")}
        self.path = str(Path(self.td.name) / (SID + ".jsonl"))
        Path(self.path).write_text("")           # empty transcript: no bg dispatches anywhere
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": self.path}]
        km._mark_views_dirty = lambda *a, **k: None
        self.gid = SID + ":g1"
        self._reset_caches()

    def tearDown(self):
        for k, v in self._saved_fns.items():
            setattr(km, k, v)
        jd._rebind_state(self._saved_state)
        self._reset_caches()
        self.td.cleanup()

    def _reset_caches(self):
        jd._PEER_ASK_CACHE[:] = [None, ({}, {})]
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        km._SESSION_STAMP_CACHE.clear()
        km._autonudge_cache.clear()
        km._bgall_cache.clear()
        km._bgtasks_cache.clear()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(rows) + ("\n" if rows else ""))
        self._reset_caches()

    def _node(self, kind=None, written=STAMP_AT, why="waiting to hear back on the handed-off design"):
        return {"id": self.gid, "text": "Ship the exporter", "parentId": None,
                "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                "t": BORN, "mt": BORN, "awaitingWhy": why, "awaitingAt": STAMP_EV,
                **({"awaitingKind": kind} if kind else {}),
                "closerLookT": written,
                "log": [{"ev_t": STAMP_EV, "src": "closer", "kind": "awaiting", "why": why,
                         **({"awaitKind": kind} if kind else {}), "at": written}]}

    def _seed_store(self, nd):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
             "nodes": {nd["id"]: nd}}))
        km._SESSION_STAMP_CACHE.clear()

    def _read_node(self):
        return json.loads((jd.GOALDIR / (SID + ".json")).read_text())["nodes"][self.gid]

    def _sweep(self, dormant=False):
        km._lift_spent_awaiting(NOW, {SID: (None if dormant else {"state": ""})})


class MatrixMint(_Base):
    """The mint grid: which (kind x reply-timing) cells go DARK at read time — the exact population
    the paused floor inherits — pinned against _goal_awaiting_stamp_full + _peer_answered_at."""

    def _dark(self, nd):
        return km._goal_awaiting_stamp_full(
            {self.gid: nd}, self.gid, answered_at=km._peer_answered_at(SID)) is None

    def test_kind_grid_under_an_answered_pair(self):
        # one answered outbound pair (a delegate the peer replied to after the stamp's write):
        # peer + kindless stamps go dark; job/agents/task/timer stand through mail
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])
        for kind, dark in (("peer", True), (None, True), ("job", False),
                           ("agents", False), ("task", False), ("timer", False)):
            self.assertEqual(self._dark(self._node(kind=kind)), dark,
                             "kind=%s: dark should be %s" % (kind, dark))

    def test_an_unrelated_pairs_answer_also_supersedes(self):
        # the pair-blind trade (documented in _peer_answered_at): the answered exchange is with a
        # DIFFERENT topic/peer than the stamp's subject, and the stamp still goes dark. The sweep's
        # durable lift is what turns this cell from paused-forever into a fresh closer ruling.
        other = "99999999-aaaa-bbbb-cccc-dddddddddddd"
        self._log([_msg(1, SID, other, STAMP_EV - 50, "coordinate"),
                   _msg(2, other, SID, REPLY, "coordinate")])
        self.assertTrue(self._dark(self._node(kind="peer")),
                        "any answered outbound pair supersedes a peer stamp (the known trade)")

    def test_a_stamp_written_after_the_reply_stands(self):
        # the write-time contract (2026-08-19 audit): the closer stamped KNOWING the reply
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])
        self.assertFalse(self._dark(self._node(kind="peer", written=REPLY + 10)),
                         "a stamp filed after the reply is fresher than the answer it already saw")

    def test_no_reply_means_the_stamp_stands(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate")])
        self.assertFalse(self._dark(self._node(kind="peer")),
                         "nothing answered → the wait stands (the 6h wake owns it)")


class MatrixSweepLift(_Base):
    """The durable retire: _lift_spent_awaiting's superseded-peer arm files the lift the readers
    already act on, on LIVE sessions only."""

    def _answered_log(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])

    def test_superseded_peer_stamp_lifts_durably(self):
        self._answered_log()
        self._seed_store(self._node(kind="peer"))
        self._sweep()
        nd = self._read_node()
        self.assertIsNone(nd.get("awaitingWhy"), "the stamp is retired in the STORE, not just at read")
        lifts = [e for e in nd["log"] if e.get("kind") == "awaiting" and e.get("lift")]
        self.assertEqual(len(lifts), 1, "exactly one lift row filed")

    def test_kindless_stamp_lifts_like_the_reader(self):
        self._answered_log()
        self._seed_store(self._node(kind=None))
        self._sweep()
        self.assertIsNone(self._read_node().get("awaitingWhy"),
                          "kindless mirrors the reader's supersede scope")

    def test_job_stamp_stands_through_mail(self):
        self._answered_log()
        self._seed_store(self._node(kind="job", why="CI run 12 still going"))
        self._sweep()
        self.assertIsNotNone(self._read_node().get("awaitingWhy"),
                             "a job wait is not ended by postal traffic")

    def test_a_stamp_written_after_the_reply_is_not_lifted(self):
        self._answered_log()
        self._seed_store(self._node(kind="peer", written=REPLY + 10))
        self._sweep()
        self.assertIsNotNone(self._read_node().get("awaitingWhy"),
                             "write-time contract: the closer already saw that reply")

    def test_no_reply_no_lift(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate")])
        self._seed_store(self._node(kind="peer"))
        self._sweep()
        self.assertIsNotNone(self._read_node().get("awaitingWhy"),
                             "a standing wait keeps its stamp (and its wake)")

    def test_dormant_owner_is_left_to_the_dead_wait_conversion(self):
        self._answered_log()
        self._seed_store(self._node(kind="peer"))
        self._sweep(dormant=True)
        self.assertIsNotNone(self._read_node().get("awaitingWhy"),
                             "the dead-wait sweep reads the stamp RAW on purpose and owns this ending")

    def test_the_lift_fires_exactly_once(self):
        self._answered_log()
        self._seed_store(self._node(kind="peer"))
        self._sweep()
        first = self._read_node()
        self._sweep()
        second = self._read_node()
        self.assertEqual(len(first["log"]), len(second["log"]),
                         "the lift materializes the fold: the second tick sees no stamp")

    def test_the_lift_rearms_the_closers_nomination(self):
        # the filed-since gate is the closer's ONE re-ask event; the stamp itself froze it
        # (newest filing == closerLookT). The lift row must move it.
        self._answered_log()
        nd0 = self._node(kind="peer")
        self._seed_store(nd0)
        nodes0 = {self.gid: dict(nd0)}
        children = {None: [self.gid]}
        self.assertFalse(jd._filed_since(nodes0, children, self.gid, jd._look_stamp(nd0)),
                         "precondition: the stamped node is invisible to the closer's gate")
        self._sweep()
        nd = self._read_node()
        self.assertTrue(jd._filed_since({self.gid: nd}, children, self.gid, jd._look_stamp(nd)),
                        "the lift is a filing: the closer re-nominates and can rule afresh")

    def test_the_lift_drops_the_spent_ledger_record(self):
        # a latched failed/moot/answered episode assumed the world the stamp described; the lift is
        # the new information that voids it (same rule as the dispatch arms)
        self._answered_log()
        self._seed_store(self._node(kind="peer"))
        km._put_nudged(self.gid, {"wake": True, "anchor": STAMP_EV, "failed": True, "at": STAMP_AT})
        self._sweep()
        self.assertNotIn(self.gid, km._auto_nudge_data().get("nudged", {}),
                         "the spent episode is erased so the ladder can re-engage")


class MatrixFireGate(_Base):
    """The plain-nudge fire gate reads the stamp the same way the walk and the wake do: a STANDING
    stamp holds the fire (its wake is the backstop); a SUPERSEDED one cannot (it disarmed that wake)."""

    def _keep(self, nd):
        fresh = {"nodes": {self.gid: nd}, "status": {}, "confirming": []}
        return bool(km._nudge_fire_list(fresh, [(self.gid, 1, "stalled")]))

    def test_standing_stamp_holds_the_fire(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate")])
        self.assertFalse(self._keep(self._node(kind="peer")),
                         "unanswered wait: the wake owns it, the status ask stays off")

    def test_superseded_peer_stamp_cannot_veto(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])
        self.assertTrue(self._keep(self._node(kind="peer")),
                        "the hidden stamp already disarmed the wake — it cannot also gag the nudge")

    def test_superseded_kindless_stamp_cannot_veto(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])
        self.assertTrue(self._keep(self._node(kind=None)))

    def test_superseded_job_stamp_still_holds(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])
        self.assertFalse(self._keep(self._node(kind="job", why="CI run 12 still going")),
                         "mail never ends a job wait: stamp visible, wake armed, veto stands")

    def test_stamp_written_after_the_reply_still_holds(self):
        self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                   _msg(2, PEER, SID, REPLY, "coordinate")])
        self.assertFalse(self._keep(self._node(kind="peer", written=REPLY + 10)),
                         "a fresher-than-the-reply stamp is standing everywhere; the veto is right")


class MatrixTwins(_Base):
    """The reader's supersede and the sweep's durable lift are ONE predicate — pinned against each
    other over the full (kind x timing) grid so they can never drift apart (the same twin-pinning
    the awaiting-peer matrix does for the two postal-log readers)."""

    def test_sweep_lifts_exactly_where_the_reader_hides(self):
        grid = [(k, w) for k in ("peer", None, "job", "agents", "task", "timer")
                for w in (STAMP_AT, REPLY + 10)]
        for kind, written in grid:
            with self.subTest(kind=kind, written=written):
                self._log([_msg(1, SID, PEER, STAMP_EV - 50, "delegate"),
                           _msg(2, PEER, SID, REPLY, "coordinate")])
                nd = self._node(kind=kind, written=written)
                hides = km._goal_awaiting_stamp_full(
                    {self.gid: nd}, self.gid, answered_at=km._peer_answered_at(SID)) is None
                self._seed_store(self._node(kind=kind, written=written))
                self._sweep()
                lifted = self._read_node().get("awaitingWhy") is None
                self.assertEqual(hides, lifted,
                                 "reader and sweep disagree on kind=%s written=%s" % (kind, written))


if __name__ == "__main__":
    unittest.main()
