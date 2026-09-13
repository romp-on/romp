#!/usr/bin/env python3
"""Deferral records retire on their reasons' OWN events, and everything standing presents
(the user 2026-08-12/13, cluster C of the stuck-card program).

Six live records were dark for up to 20 hours: two hold reasons were permanently screened from
display, one had no reader at all, and a record frozen mid-walk froze its own backstop — because
retirement was coupled to the goal walk's position and the screens/counters existed to compensate.
All three compensations are deleted; ONE per-tick sweep over auto-nudge.json pops each record on its
reason's exact event, so a record that exists genuinely stands, and every standing record presents —
in-flight-class reasons as the card's Analyzing… swirl, everything else as the Stalled section.

Plus the user's HARD RULE (2026-08-13): a stalled card on a session that isn't doing anything files
under BLOCKED — it needs eyes, whoever's bottleneck it is.

All fixtures synthetic (placeholder UUIDs, invented text).
"""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_sweep", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
GID = SID + ":g1"


class SweepBase(unittest.TestCase):
    def setUp(self):
        self._saved = (km._auto_nudge_data, km._write_auto_nudge, jd.active_runs,
                       km._retry_paused_on, km._path_of)
        self._d = {"nudged": {}, "deferred": {}}
        km._auto_nudge_data = lambda: self._d
        km._write_auto_nudge = lambda d: self._d.update(d)
        jd.active_runs = lambda: []
        km._retry_paused_on = lambda: False
        km._path_of = lambda sid: ""
        self._store()

    def tearDown(self):
        (km._auto_nudge_data, km._write_auto_nudge, jd.active_runs,
         km._retry_paused_on, km._path_of) = self._saved
        try:
            (jd.GOALDIR / (SID + ".json")).unlink()
        except OSError:
            pass

    def _store(self, over=None):
        st = {"rompUuid": SID, "seq": 1, "placements": {},
              "status": {GID: "working"}, "confirming": [],
              "nodes": {GID: {"id": GID, "parentId": None, "t": NOW - 500, "mt": NOW - 500,
                              "text": "an invented goal", "log": []}}}
        st.update(over or {})
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(st))
        return st

    def _rec(self, why, **kw):
        self._d["deferred"] = {GID: {"at": NOW - 300, "why": why, "sid": SID, **kw}}


class ReasonsRetireOnTheirEvents(SweepBase):
    def test_a_resolved_goal_pops_whatever_the_why(self):
        self._store({"status": {GID: "blocked"}})
        self._rec("the agent's to-do sync is due")
        km._deferral_sweep_tick(NOW)
        self.assertEqual(self._d["deferred"], {}, "the goal left plain working — nothing to hold")

    def test_a_judging_hold_stands_while_the_call_runs_and_pops_when_it_returns(self):
        self._rec(jd.WHY_JUDGING)
        jd.active_runs = lambda: [{"judge": "closer", "fsid": SID, "sent": 1.0}]
        km._deferral_sweep_tick(NOW)
        self.assertIn(GID, self._d["deferred"], "the call is genuinely in flight — the record stands")
        jd.active_runs = lambda: []
        km._deferral_sweep_tick(NOW + 5)
        self.assertEqual(self._d["deferred"], {}, "the call returned — the exact event, popped that tick")

    def test_a_turn_in_flight_hold_pops_when_its_turn_ends(self):
        self._rec(jd.WHY_TURN_IN_FLIGHT, evT=NOW - 100)
        saved = jd.parsed_session
        jd.parsed_session = lambda sid, files, now: {"turns": [
            {"id": "t1", "t": NOW - 200, "end": NOW - 150, "ended": True, "atoms": []}]}
        km._path_of = lambda sid: "/synthetic/t.jsonl"
        try:
            km._deferral_sweep_tick(NOW)
            self.assertIn(GID, self._d["deferred"],
                          "the held-on evidence (evT) postdates every ended turn — still in flight")
            jd.parsed_session = lambda sid, files, now: {"turns": [
                {"id": "t2", "t": NOW - 200, "end": NOW - 50, "ended": True, "atoms": []}]}
            km._deferral_sweep_tick(NOW + 5)
            self.assertEqual(self._d["deferred"], {},
                             "an ended turn reached the evidence time — the exact retire event")
        finally:
            jd.parsed_session = saved

    def test_a_paused_tiers_hold_pops_on_resume(self):
        km._retry_paused_on = lambda: True
        self._rec("the judge tiers are paused (nothing could revive it)")
        km._deferral_sweep_tick(NOW)
        self.assertIn(GID, self._d["deferred"])
        km._retry_paused_on = lambda: False
        km._deferral_sweep_tick(NOW + 5)
        self.assertEqual(self._d["deferred"], {})

    def test_a_followup_hold_pops_when_the_reply_is_judged(self):
        st = self._store()
        st["nodes"][GID]["followupPending"] = True
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(st))
        self._rec("your reply to the card is still being judged")
        km._deferral_sweep_tick(NOW)
        self.assertIn(GID, self._d["deferred"], "the reply is still pending — the record stands")
        st["nodes"][GID]["followupPending"] = False
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(st))
        km._deferral_sweep_tick(NOW + 5)
        self.assertEqual(self._d["deferred"], {})

    def test_a_confirming_hold_pops_when_the_export_clears(self):
        self._store({"confirming": [GID]})
        self._rec("the card is already complete and merely unsettled")
        km._deferral_sweep_tick(NOW)
        self.assertIn(GID, self._d["deferred"], "genuinely confirming — the record stands")
        self._store({"confirming": []})
        km._deferral_sweep_tick(NOW + 5)
        self.assertEqual(self._d["deferred"], {},
                         "no longer confirming (settled, or completion refused) — popped")

    def test_an_unknown_why_stands(self):
        self._rec("a reason a future reviver minted")
        km._deferral_sweep_tick(NOW)
        self.assertIn(GID, self._d["deferred"],
                      "the honest default: present until the walk re-runs it — never a silent pop")

    def test_the_sweep_never_rewrites_a_why(self):
        # single writer per duty: the walk writes, the sweep pops. A rewrite here raced the walk and
        # could re-dress a durable hold as in-flight whenever any judge call happened to be running.
        self._rec("the agent's to-do sync is due")
        jd.active_runs = lambda: [{"judge": "captioner", "fsid": SID, "sent": 1.0}]
        saved = km._plan_sync_pending
        km._plan_sync_pending = lambda sid, nodes: True
        try:
            km._deferral_sweep_tick(NOW)
        finally:
            km._plan_sync_pending = saved
        self.assertEqual(self._d["deferred"][GID]["why"], "the agent's to-do sync is due",
                         "a live captioner call must not re-dress a durable hold")


class SharedViewRead(SweepBase):
    """The sweep's one store read per session takes the shared read-only view (kernel/judge.py
    load_goals_shared): it reads nodes, status and confirming and writes nothing, so two ticks over an
    unchanged store parse it once."""

    def test_the_sweep_reads_the_shared_view(self):
        self._rec("a reason a future reviver minted")       # an unknown why stands: the record survives both ticks
        jd._shared_clear()
        private, o_load = [], jd.load_goals
        jd.load_goals = lambda fsid: (private.append(fsid), o_load(fsid))[1]
        stats0 = jd.shared_store_stats()
        try:
            km._deferral_sweep_tick(NOW)
            km._deferral_sweep_tick(NOW)
        finally:
            jd.load_goals = o_load
        stats = jd.shared_store_stats()
        self.assertIn(GID, self._d["deferred"])
        self.assertEqual(private, [], "the writer's loader is never asked")
        self.assertEqual((stats["miss"] - stats0["miss"], stats["hit"] - stats0["hit"]), (1, 1), "one parse, then a hit")


class HardRuleAndRoutingPins(unittest.TestCase):
    """build_feed's presentation, pinned by source (the build_feed test pattern)."""

    def test_stalled_plus_idle_files_under_blocked(self):
        src = inspect.getsource(km._feed_session_entry)
        # the user's hard rule (2026-08-13), superseding the 2026-07-23 Working-only stance
        self.assertIn('_stall_block = bool(_stall_rec and not who_working and not sess_awaiting_why)', src)
        self.assertIn('or nid == perm_top or _stall_block', src)
        self.assertIn('"blocked": _stall_block}', src)

    def test_in_flight_holds_route_to_the_swirl(self):
        src = inspect.getsource(km._feed_session_entry)
        self.assertIn('_stall_rec.get("why") in jd.WHY_IN_FLIGHT', src)
        self.assertIn('bool((sess_judging or _stall_inflight) and column == "working")', src)

    def test_the_sweep_runs_every_tick_independent_of_the_toggle(self):
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_job_stage('deferralSweep', lambda: _deferral_sweep_tick(now))", src)   # a tick job, its own stage (T398)
        sweep_pos = src.index("_deferral_sweep_tick")
        nudge_pos = src.index("_auto_nudge_tick")
        self.assertLess(sweep_pos, nudge_pos, "retirement runs before the walk that would re-fire")


if __name__ == "__main__":
    unittest.main()
