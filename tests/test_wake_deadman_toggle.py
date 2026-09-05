#!/usr/bin/env python3
"""The awaiting dead-man runs whether or not auto-nudge is on, and reaches a stamped top the
all-delegated gate would otherwise skip (2026-09-05).

The 6h wake in _wake_goal is the designed backstop for waits whose ending romp cannot observe
(kind=job: external compute). It was unreachable twice over for the live specimen — a kind=job
stamp that stood 17 hours over nothing pending: (a) _auto_nudge_tick returned before the goal walk
because the user had auto-nudge OFF, and (b) even with it on, the walk skipped the top via
_all_outstanding_delegated (its children complete, the top carrying a courier `handoff`), a gate
that exists to suppress the plain status nudge for peer work — a job/agents/task/timer wait is the
session's OWN wait, not delegated work. The toggle governs the nudge (an injected status check the
user opted out of); the dead-man is the reachability floor every Working card is promised, so it
runs from the pusher's tick regardless, the way _interrupt_block_tick does — but with nudges OFF it
injects NOTHING (romp interrupts only when the human is the bottleneck, and the user said no
unprompted messages): it files the stamp's lift, the exact row the orphan lift files, so the card
returns to plain Working and the closer is re-nominated. With nudges ON the injected check-in is
unchanged. Either way it acts once per stamp episode (the wake record / the lifted stamp), never
again per tick. SYNTHETIC fixtures only; a PRIVATE sid (goal-store fixture rule)."""
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
km = SourceFileLoader("romp_kernel_wdt", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "55555555-6666-7777-8888-999999999999"     # private to this module (the fixture rule)
NOW = 1_787_900_000
H = 3600


class _FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, sid, body):
        self.sent.append((sid, body))

    def pending_queued(self, sid):
        return []


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in (
            "_alive_sessions", "_wait_for_graph", "_session_flag", "_compacting_now", "_api_error",
            "_session_working", "_interrupt_suppresses_nudge", "_backend_rewind_pending", "_last_state",
            "_session_awaiting", "_turn_romp_injected", "_closer_settled", "_revivers_pending",
            "_pending_ops", "_log_nudge_event", "_push_all", "_mark_views_dirty", "_path_of",
            "_debt_backstop_tick", "_PREV_ALIVE")}
        self.saved_jd = {k: getattr(jd, k) for k in ("STATE", "GOALDIR", "parsed_session", "_segs", "plan_units")}
        self.saved_backend = km.Sessions.backend_for
        jd.STATE = td
        jd.GOALDIR = td / "goals"; jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        self.fb = _FakeBackend()
        km.Sessions.backend_for = lambda sid: self.fb
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": "/nonexistent.jsonl"}]
        km._wait_for_graph = lambda now, sids: {}
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="": False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: None
        km._turn_romp_injected = lambda tn: False
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a, **k: ""
        km._pending_ops = {}
        km._log_nudge_event = lambda *a, **k: None
        km._push_all = lambda *a, **k: None
        km._mark_views_dirty = lambda *a, **k: None
        km._path_of = lambda sid, now=None: "/nonexistent.jsonl"
        km._debt_backstop_tick = lambda now: None
        km._PREV_ALIVE = {SID}                       # no death transition pending
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store: []
        self.turns = [{"id": "t1", "ended": True, "end": NOW - 8 * H, "t": NOW - 8 * H - 10, "atoms": []}]
        jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}
        self.gid = SID + ":g1"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        for k, v in self.saved_jd.items():
            setattr(jd, k, v)
        km.Sessions.backend_for = self.saved_backend
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        self.td.cleanup()

    def _toggle(self, enabled):
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({"enabled": enabled, "nudged": {}}))
        km._autonudge_cache.clear()

    def _seed(self, kind="job", age=7 * H, delegated=True, stamped=True):
        """A working top whose only open leaf is a courier handoff (all-delegated), carrying a stamp."""
        at = NOW - age
        why = "the index rebuild is still running; picking the result up when it lands"
        top = {"id": self.gid, "text": "rebuild the notes-api index", "parentId": None,
               "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": 100, "mt": 100,
               "log": []}
        if stamped:
            top.update({"awaitingWhy": why, "awaitingAt": at, **({"awaitingKind": kind} if kind else {})})
            top["log"].append({"ev_t": at, "src": "closer", "kind": "awaiting", "why": why,
                               **({"awaitKind": kind} if kind else {}), "at": at})
        nodes = {self.gid: top}
        if delegated:
            kid = self.gid + "c"
            nodes[kid] = {"id": kid, "text": "web session: wire the watcher", "parentId": self.gid,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                          "t": 100, "mt": 100, "log": [],
                          "handoff": {"to": "web", "msgId": "11111111-2222-3333-4444-000000000001"}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {self.gid: "working"}, "nodes": nodes}))
        km._SESSION_STAMP_CACHE.clear()

    def _tick(self, now=NOW):
        km._auto_nudge_tick(now, {SID: {"state": ""}})

    def _wakes(self):
        return [b for _s, b in self.fb.sent if km.AWAITING_BACKSTOP_TEXT in b]

    def _node(self):
        return jd.load_goals(SID)["nodes"][self.gid]

    def _lifts(self):
        return [e for e in self._node().get("log") or [] if e.get("kind") == "awaiting" and e.get("lift")]


class DeadmanIgnoresTheToggle(_Base):
    def test_a_7h_job_stamp_on_an_all_delegated_top_files_a_lift_and_injects_nothing(self):
        self._toggle(False)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [], "nudges off: no unprompted message, whatever the clock says")
        self.assertIsNone(self._node().get("awaitingWhy"), "the wait romp cannot vouch for is withdrawn")
        row = self._lifts()[-1]
        self.assertEqual((row.get("src"), row.get("ev_t")), ("romp", NOW - 7 * H),
                         "the orphan lift's exact row: romp/awaiting/lift at the stamp's anchor")
        self.assertEqual(jd.load_goals(SID)["status"].get(self.gid, "working"), "working",
                         "no block — every procedural block copy would misstate what happened")
        self.assertNotIn(self.gid, km._auto_nudge_data()["nudged"], "no injection → no wake record")

    def test_a_second_cycle_does_not_file_again(self):
        self._toggle(False)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self._tick(NOW + 5)
        self._tick(NOW + 60)
        self.assertEqual(len(self._lifts()), 1, "once per stamp — the lifted stamp no longer reads as one")
        self.assertEqual(self.fb.sent, [])

    def test_a_5h_stamp_is_still_patient(self):
        self._toggle(False)
        self._seed(kind="job", age=5 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [], "the 6h constant stands")
        self.assertIsNotNone(self._node().get("awaitingWhy"), "…and nothing is filed either")

    def test_the_toggle_still_governs_the_plain_nudge(self):
        # an unstamped working top with nudges off: nothing fires — the opt-out is the nudge's
        self._toggle(False)
        self._seed(stamped=False, delegated=False)
        self._tick()
        self.assertEqual(self.fb.sent, [], "no injected status check while auto-nudge is off")

    def test_the_kinds_take_the_same_path(self):
        for kind in ("agents", "task", "timer"):
            self._toggle(False)
            self._seed(kind=kind, age=7 * H)
            self._tick()
            self.assertEqual(len(self._lifts()), 1, "kind=%s is the session's own wait" % kind)
            self.assertEqual(self.fb.sent, [])


class NudgesOnKeepTheCheckIn(_Base):
    def test_with_nudges_on_the_all_delegated_top_still_takes_its_wake(self):
        self._toggle(True)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self.assertEqual(len(self._wakes()), 1, "nudges on: today's injected check-in, unchanged")
        self.assertIsNotNone(self._node().get("awaitingWhy"), "the check-in is the action — the stamp stands")
        self.assertEqual(self._lifts(), [])
        rec = km._auto_nudge_data()["nudged"][self.gid]
        self.assertTrue(rec.get("wake"), "…recorded as a wake episode in the shared ledger")
        self.assertEqual(rec.get("anchor"), NOW - 7 * H)
        self.assertNotIn(self.gid, km._auto_nudge_data().get("walkGates", {}),
                         "the walk reached the goal — no all-delegated hold is journaled for it")

    def test_a_second_cycle_is_silent_with_nudges_on(self):
        self._toggle(True)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self._tick(NOW + 5)
        self._tick(NOW + 60)
        self.assertEqual(len(self._wakes()), 1, "once per stamp episode — keyed on the wake record")


class OwnWaitOutranksTheDelegatedGate(_Base):

    def test_a_peer_stamp_keeps_the_gate(self):
        # a peer wait IS the delegated shape the gate exists for — it stays behind it
        self._toggle(True)
        self._seed(kind="peer", age=7 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [])
        self.assertEqual(km._auto_nudge_data()["walkGates"][self.gid]["gate"], "all-delegated")

    def test_a_kindless_stamp_keeps_the_gate(self):
        self._toggle(True)
        self._seed(kind=None, age=7 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [], "a kindless stamp may be a peer wait — conservative, as before")


if __name__ == "__main__":
    unittest.main()
