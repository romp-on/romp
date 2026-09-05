#!/usr/bin/env python3
"""The 2026-08-25 awaiting audit's surviving classes, pinned (10 stamps live: 3 real, 4 stale, 3
misfiled — every stale/misfiled one traced to a writer whose evidence the world had outrun):

- KIND-LAUNDERING (C4): the peer write gate narrowed kind=peer, and the closer began filing the
  same peer/idle waits as job/timer/agents — the kinds exempt from every mail-driven retire. The
  prompt now draws hard kind boundaries; the agents kind gains the mechanical complement (a stamp
  over a world with nothing running anywhere lifts — pinned in test_kernel_awaiting_lift).
- ASK-RECENCY (C6): _open_ask_peers admitted a peer stamp off week-old open questions to another
  host — asks that predate the GOAL cannot be what its wait is on (the waitfor gate's own
  evidence-order rule, now applied at the write gate), and the stamp's awaitPeers then named the
  wrong peers, so the pair-scoped supersede missed the reply that came.
- DIARY STAND-DOWN (C3): the closer, auditing a pre-ending segment, re-asserted a wait whose lift
  AND whose goal's done were already in the diary — the re-assert now stands down when the diary
  ended the wait after the audited evidence (the standing writer-yields rule, applied to awaiting
  asserts).
All fixtures SYNTHETIC."""
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
jd = SourceFileLoader("romp_judge_audit", os.path.join(BIN, "romp-judge")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
PEER = "66666666-7777-8888-9999-aaaaaaaaaaaa"
OLDPEER = "99999999-8888-7777-6666-bbbbbbbbbbbb"
T0 = 1_781_100_000


def _msg(i, f, t_, ts, kind):
    return json.dumps({"id": "m%d" % i, "ev": "sent", "from": "web", "from_id": f,
                       "to_id": t_, "t": ts, "kind": kind, "body": "x"})


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        self.td.cleanup()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(rows) + ("\n" if rows else ""))
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]

    def _store(self, mint=T0):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", mint, [{"do": "mint", "why": "x", "text": "Ship the exporter"}], [])
        return s, SID + ":g1"

    def _close_peer(self, s, why="asked the worker to verify; holding for the answer"):
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": why, "kind": "peer"}}}, t=T0 + 500)


class AskRecency(_Base):
    """C6: an open ask that PREDATES the goal cannot admit its peer stamp."""

    def test_a_pre_goal_ask_does_not_admit(self):
        # the specimen: week-old open questions to another host, a fresh goal, a no-reply delegate —
        # the stamp was admitted off asks that could not possibly be what this wait is on
        self._log([_msg(1, SID, OLDPEER, T0 - 7 * 86400, "question"),
                   _msg(2, SID, PEER, T0 + 10, "delegate")])
        s, gid = self._store(mint=T0)
        self._close_peer(s)
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "no ask at/after the goal's mint → the gate stands down")

    def test_an_ask_after_the_goal_admits_and_names_only_itself(self):
        self._log([_msg(1, SID, OLDPEER, T0 - 7 * 86400, "question"),
                   _msg(2, SID, PEER, T0 + 10, "question")])
        s, gid = self._store(mint=T0)
        self._close_peer(s)
        nd = s["nodes"][gid]
        self.assertEqual(nd.get("awaitingKind"), "peer")
        self.assertEqual(nd.get("awaitingPeers"), [PEER],
                         "awaitPeers carries ONLY the qualifying ask's peer — the pair supersede "
                         "then watches the right pair, not a week-old stranger")

    def test_the_gate_helper_scopes_by_since(self):
        self._log([_msg(1, SID, OLDPEER, T0 - 100, "question"),
                   _msg(2, SID, PEER, T0 + 100, "question")])
        self.assertEqual(jd._open_ask_peers(SID), sorted([OLDPEER, PEER]), "unscoped: both")
        self.assertEqual(jd._open_ask_peers(SID, since=T0), [PEER], "scoped: only at/after")


class DiaryStandDown(_Base):
    """C3: a re-assert whose audited evidence predates the diary's own ending yields."""

    def _stamped(self):
        self._log([_msg(1, SID, PEER, T0 + 10, "question")])
        s, gid = self._store()
        self._close_peer(s)
        self.assertEqual(s["nodes"][gid].get("awaitingKind"), "peer", "fixture: stamped")
        return s, gid

    def test_a_reassert_after_the_diarys_lift_stands_down(self):
        s, gid = self._stamped()
        nd = s["nodes"][gid]
        self.assertTrue(jd.record_verdict(s, nd, "romp", "awaiting", T0 + 600, lift=True),
                        "fixture: the wait ENDED in the diary (a lift row lands)")
        # the closer now audits an OLDER segment (ev t=T0+500 < the lift's arrival) and re-asserts
        # with a CHANGED why — pre-fix this filed a fresh stamp over the ended wait
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": "still holding for the verify",
                                                             "kind": "peer"}}}, t=T0 + 500)
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "the diary ended this wait after the audited evidence — the writer yields")

    def test_a_reassert_from_fresh_evidence_still_files(self):
        s, gid = self._stamped()
        nd = s["nodes"][gid]
        jd.record_verdict(s, nd, "romp", "awaiting", T0 + 600, lift=True)
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        # a NEWER audited turn (evidence past the lift's arrival) re-asserts: a genuinely new wait
        row_at = max((e.get("at") or 0) for e in nd["log"])
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": "asked again after the fix",
                                                             "kind": "peer"}}}, t=row_at + 60)
        self.assertEqual(s["nodes"][gid].get("awaitingWhy"), "asked again after the fix",
                         "fresh evidence out-orders the ending — the new wait files as ever")


class KindBoundaries(_Base):
    """C4a: the prompt's hard kind boundaries (the laundering shapes, named)."""

    def test_the_kind_boundaries_are_stated(self):
        for phrase in ("another SESSION's work is never a job",
                       # 2026-09-05: a closer stamped kind=job for a Monitor plus a background
                       # command the session itself was running — "job" is for compute the session
                       # cannot watch from inside the harness; its own commands/watchers/subagents
                       # are task/agents. The dead-man for job waits is a 6h clock, so a
                       # mislabelled in-harness wait costs hours the exact lifts would have saved.
                       "cannot watch from inside the harness",
                       "own background command, Monitor, or subagent is task or agents, never job",
                       "never one the turn "
                       "canceled",
                       "an idle recipient reads idle",
                       "Never relabel a wait to a different "
                       "kind to get it filed"):
            self.assertIn(phrase, jd.CLOSER_SYS)


if __name__ == "__main__":
    unittest.main()
