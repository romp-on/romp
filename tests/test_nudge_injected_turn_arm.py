#!/usr/bin/env python3
"""A verdict about a ROMP-INJECTED turn must not gag the nudge forever (the user 2026-08-01).

The incident, all fixtures SYNTHETIC: the `web` session's card was blocked on a question ("restart the
service now, or should I?"). A kernel restart then cut the session mid-turn; romp injected its resume
notice, which opened the next turn; the session answered in that turn ("already restarted and verified")
and stopped. The unblocker read that turn and lifted the block, so the card dropped back to plain
'working' on an idle session — exactly the stall the auto-nudge exists for.

No nudge ever came. _nudge_fire_list dropped the goal on EVERY tick, because its diary held a verdict
whose ev_t (the injected turn's trigger) was newer than the ARM turn — and an injected turn can never
BECOME the arm (the arm is by definition the newest GENUINELY-triggered ended turn, so romp's own
messages never re-arm a nudge). The only thing that could have moved the arm was a genuine new turn,
and the only thing that would have produced one was the nudge itself. The card sat in Working with no
nudge, no block, no ⏳ stamp, nothing being judged, and — because this drop wrote no deferral record —
nothing to read anywhere in the state dir. The user found it by eye half an hour later.

The fix keys the guard on the newest turn romp has SEEN END (`seen_t`), with the arm as its floor. The
2026-07-29 guard the fix must not break stands on turns still IN FLIGHT: there, the judges have ruled on
something romp has not watched finish, and the stall read really is a world old. Once a turn has ended,
a verdict about it that leaves the goal working is the considered verdict — the same argument the
2026-07-30 `at`→`ev_t` narrowing made for the arm turn, which is just this turn's floor.

And a held goal is now DEFERRED, never dropped: it takes a deferral record (why + the 6h backstop) like
every other nudge hold, so the wait is inspectable. The why is screened out of the stall surfaces the
way the judging hold is (jd.stall_why_stands) — the turn's own end event clears it, so it is a beat romp
is working through, not a wedge to interrupt anyone about.
"""
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
km = SourceFileLoader("romp_kernel_injarm", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
NOW = 1781100000
ARM_T = NOW - 1200               # the genuine turn the stall armed on (the restart cut it)
INJ_T = NOW - 600                # the resume notice romp injected — trigger of the turn that followed


def _node(nid, text, **kw):
    d = {"id": nid, "text": text, "parentId": None, "nodeComplete": False, "blocked": False,
         "cleared": False, "trail": [], "t": NOW - 7200, "mt": NOW - 600, "log": []}
    d.update(kw)
    return d


def _store(nodes, status=None):
    return {"rompUuid": SID, "seq": len(nodes), "lastNode": G1, "nodes": nodes, "placements": {},
            "status": status if status is not None else {n: "working" for n in nodes}}


def _incident_log():
    """The audited diary: blocked on a question, then unblocked off the INJECTED turn romp opened."""
    return [{"ev_t": ARM_T, "src": "closer", "kind": "block",
             "why": "restart the service now, or should I?", "at": ARM_T + 120},
            {"ev_t": INJ_T, "src": "unblocker", "kind": "unblock",
             "why": "answered in passing: already restarted and verified", "at": INJ_T + 70}]


class FireListSeenTurn(unittest.TestCase):
    """The guard's yardstick is the newest turn romp has watched END, not the arm."""

    def _fresh(self):
        return _store({G1: _node(G1, "Ship the reconnect banner", log=_incident_log())})

    def test_a_verdict_about_an_ended_injected_turn_keeps_the_fire(self):
        out = km._nudge_fire_list(self._fresh(), [(G1, 1, False)], arm_t=ARM_T, seen_t=INJ_T)
        self.assertEqual([f[0] for f in out], [G1],
                         "the injected turn ENDED and the goal is still working — that verdict is the "
                         "considered 'working' one, and the arm can never advance to reach it")

    def test_the_same_verdict_still_drops_while_its_turn_is_in_flight(self):
        # the 2026-07-29 guard, intact: romp has not seen that turn end (seen_t is still the arm), so
        # the stall read describes a world one turn old
        self.assertEqual(km._nudge_fire_list(self._fresh(), [(G1, 1, False)],
                                             arm_t=ARM_T, seen_t=ARM_T), [],
                         "a ruling on a turn romp hasn't watched finish still stands the nudge down")

    def test_evidence_newer_than_the_seen_turn_drops(self):
        log = _incident_log() + [{"ev_t": INJ_T + 300, "src": "unblocker", "kind": "unblock",
                                  "why": "answered in the thread", "at": INJ_T + 310}]
        fresh = _store({G1: _node(G1, "Ship the reconnect banner", log=log)})
        self.assertEqual(km._nudge_fire_list(fresh, [(G1, 1, False)], arm_t=ARM_T, seen_t=INJ_T), [],
                         "a verdict from a turn NEWER than the newest ended one is the live race")

    def test_a_held_goal_is_reported_to_the_caller(self):
        held = []
        self.assertEqual(km._nudge_fire_list(self._fresh(), [(G1, 1, False)],
                                             arm_t=ARM_T, seen_t=ARM_T, held=held), [])
        self.assertEqual([f[0] for f, _why, _ev in held], [G1],
                         "the hold must reach the caller so it can be recorded, not silently dropped")
        self.assertTrue(all(isinstance(_ev, int) and _ev for _f, _why, _ev in held
                            if _why == jd.WHY_TURN_IN_FLIGHT),
                        "…with the offending evidence time riding along (the sweep's retire event)")

    def test_a_resolved_goal_is_never_held(self):
        # the status re-read runs FIRST: a goal the judges finished mid-tick is out of the tick
        # entirely, so the backstop can never resurrect a nudge for a card that is already done
        fresh = _store({G1: _node(G1, "Ship the reconnect banner", log=_incident_log(),
                                  nodeComplete=True)}, status={G1: "completed"})
        held = []
        self.assertEqual(km._nudge_fire_list(fresh, [(G1, 1, False)],
                                             arm_t=ARM_T, seen_t=ARM_T, held=held), [])
        self.assertEqual(held, [], "a finished card is dropped, never held for later")

    def test_callers_that_pass_no_seen_turn_are_unchanged(self):
        self.assertEqual(km._nudge_fire_list(self._fresh(), [(G1, 1, False)], arm_t=ARM_T), [],
                         "the arm alone stays the floor — the pre-fix contract for existing callers")

    def test_the_hold_why_is_in_flight_class(self):
        self.assertIn(jd.WHY_TURN_IN_FLIGHT, jd.WHY_IN_FLIGHT,
                      "a wait that ends on the turn's own end event presents as the Analyzing… "
                      "swirl, never the stalled chip (routing replaced the screen, 2026-08-13)")


class InjectedTurnDeadlockEndToEnd(unittest.TestCase):
    """Through _auto_nudge_session: the restart-resume shape must produce a nudge, and the in-flight
    shape must produce a deferral RECORD rather than silence."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._autonudge_cache.clear()
        self._orig_km = {n: getattr(km, n) for n in (
            "_session_flag", "_compacting_now", "_api_error", "_session_working",
            "_interrupt_suppresses_nudge", "_backend_queued", "_backend_rewind_pending",
            "_last_state", "_session_awaiting", "_closer_settled", "_revivers_pending",
            "_pending_ops")}
        self._orig_jd = {n: getattr(jd, n) for n in ("parsed_session", "load_goals", "_segs", "plan_units")}
        self._orig_backend = km.Sessions.backend_for
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="": False
        km._backend_queued = lambda sid: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: False
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a: ""          # every other reviver exhausted
        km._pending_ops = {}
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store: []
        # _turn_romp_injected stays REAL: the deadlock lives in its interaction with the arm scan.
        self.turns = [self._turn("t1", ARM_T, "human", ended=True),
                      self._turn("t2", INJ_T, "romp", ended=True)]
        jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}
        self.store = _store({G1: _node(G1, "Ship the reconnect banner", log=_incident_log())})
        jd.load_goals = lambda sid: self.store
        self.sent = []
        test = self

        class FakeBackend:
            def send(self, sid, body):
                test.sent.append(body)
        km.Sessions.backend_for = staticmethod(lambda sid: FakeBackend())

    def tearDown(self):
        for n, v in self._orig_km.items():
            setattr(km, n, v)
        for n, v in self._orig_jd.items():
            setattr(jd, n, v)
        km.Sessions.backend_for = self._orig_backend
        jd.STATE = self.saved_state
        km._autonudge_cache.clear()
        self.td.cleanup()

    @staticmethod
    def _turn(tid, t, author, ended=True):
        uid = "u-" + tid
        return {"id": tid, "t": t, "end": t + 60, "ended": ended, "trigger": {"uuid": uid},
                "atoms": [{"uuid": uid, "type": "user", "author": author, "t": t}, {}, {}]}

    def _tick(self, now=NOW):
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        return km._auto_nudge_session({"sid": SID, "path": "/nonexistent.jsonl"}, now, {}, nudged, {})

    def test_the_restart_resume_shape_gets_its_nudge(self):
        self.assertTrue(km._turn_romp_injected(self.turns[-1]),
                        "the fixture is the real shape: romp's resume notice opened the last turn")
        self.assertTrue(self._tick(), "the idle session's still-working card must be status-checked")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("<!-- romp-goal-id: %s -->" % G1, self.sent[0])
        rec = km._auto_nudge_data()["nudged"][G1]
        self.assertEqual(rec["lastTurnId"], "t1",
                         "the record still pins the GENUINE arm — romp's own turn never re-arms a nudge")

    def test_the_fire_leaves_no_stale_deferral_record_behind(self):
        km._nudge_deferred_ok(G1, "the agent's to-do sync is due", NOW - 30, SID)
        self.assertIn(G1, km._auto_nudge_data()["deferred"])
        self._tick()
        self.assertNotIn(G1, km._auto_nudge_data().get("deferred", {}),
                         "the hold ended when the nudge went out — a stale why must not outlive it")

    def test_a_turn_still_in_flight_holds_the_fire_and_says_so(self):
        self.turns[-1]["ended"] = False               # the judges ruled on a turn romp hasn't seen end
        self.assertFalse(self._tick(), "the 2026-07-29 stand-down still applies")
        self.assertEqual(self.sent, [])
        rec = km._auto_nudge_data()["deferred"][G1]
        self.assertEqual(rec["why"], jd.WHY_TURN_IN_FLIGHT,
                         "the hold is RECORDED — the silent drop is what made the deadlock unreadable")
        self.assertEqual(rec["sid"], SID)

    def test_a_wedged_hold_still_fires_on_the_owning_pass(self):
        self.turns[-1]["ended"] = False
        self._tick()
        self.assertEqual(self.sent, [])
        # W2c: the wedge event is the owning tier completing a pass over this fsid with the hold
        # still standing — never a clock (the retired 6h bound)
        km.jd.pass_done("close", SID)
        self.assertTrue(self._tick(now=NOW + 120), "a hold the pass did not retire is wedged, not a veto")
        self.assertEqual(len(self.sent), 1)

    def test_a_resolved_card_is_never_nudged_either_way(self):
        self.store["status"][G1] = "completed"
        self.assertFalse(self._tick())
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
