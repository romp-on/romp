#!/usr/bin/env python3
"""The walk->sweep handoff is a JOURNALED GATE, not a clock (the user 2026-08-24, W1b of the
time-windows package): the nudge walk returns the NAME of the session gate it stood down on, the
tick journals it (walkGates, per sid — or per gid for the two per-goal skips that strand a wake
record before the evaluator), and the outcome sweep owns exactly the wedge-held and unwalked
records. A wedge gate (api-error / parse-failed / empty-parse / the per-goal skips) has no
session-produced ending event while a wake is dead — the 2026-08-11 incident the sweep was built
for — so the sweep acts NOW, not at hour six; a transient or judge-owned gate's ending event
re-runs the walk itself, so the walk keeps those records at ANY age (no clock resurrection); the
muted opt-out stands the sweep down entirely. SYNTHETIC fixtures only."""
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
km = SourceFileLoader("romp_kernel_wgate", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-777777777777"
NOW = 1_787_800_000


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._autonudge_cache.clear()
        km._SESSION_STAMP_CACHE.clear()

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _gates(self):
        return km._auto_nudge_data().get("walkGates", {})


class JournalMechanics(_Base):
    def test_put_pop_and_first_at_kept_across_gate_flaps(self):
        km._put_walk_gate(SID, "compacting", NOW)
        self.assertEqual(self._gates()[SID], {"gate": "compacting", "at": NOW})
        km._put_walk_gate(SID, "compacting", NOW + 100)   # same gate → write-on-change: untouched
        self.assertEqual(self._gates()[SID]["at"], NOW)
        km._put_walk_gate(SID, "api-error", NOW + 200)    # name flaps → FIRST at survives
        self.assertEqual(self._gates()[SID], {"gate": "api-error", "at": NOW})
        km._pop_walk_gate(SID)
        self.assertNotIn(SID, self._gates())

    def test_the_walk_returns_the_gate_it_stood_down_on(self):
        # the cheapest two gates to arrange, pinning the return-the-name contract end to end
        s = {"sid": SID, "path": str(Path(self.td.name) / "missing.jsonl")}
        saved = km._session_flag
        km._session_flag = lambda sid, flag: flag == "hideFromFeed"
        try:
            self.assertEqual(km._auto_nudge_session(s, NOW, {}, {}, {}, {SID}), "muted")
        finally:
            km._session_flag = saved
        saved_ps = km.jd.parsed_session
        km.jd.parsed_session = lambda sid, paths, now: (_ for _ in ()).throw(RuntimeError("corrupt"))
        try:
            self.assertEqual(km._auto_nudge_session(s, NOW, {}, {}, {}, {SID}), "parse-failed")
        finally:
            km.jd.parsed_session = saved_ps


class SweepOwnership(_Base):
    """The sweep's constituency: wedge-held and unwalked records — never a clock."""

    def setUp(self):
        super().setUp()
        self.saved = {k: getattr(km, k) for k in ("_path_of", "_session_working", "_push_all",
                                                  "_log_nudge_event")}
        km._path_of = lambda sid, now=None: ""       # no transcript: the response gates get no turns
        km._session_working = lambda turns: False
        km._push_all = lambda *a, **k: None
        km._log_nudge_event = lambda *a, **k: None
        self.gid = SID + ":g1"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        super().tearDown()

    def _seed(self, gate=None, gate_key=None, age_h=300):
        nd = {"id": self.gid, "text": "Ship the exporter", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": NOW - 400 * 3600,
              "awaitingWhy": "waiting on the long job", "awaitingAt": NOW - 400 * 3600, "log": []}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))
        d = {"enabled": True,
             "nudged": {self.gid: {"wake": True, "anchor": NOW - 400 * 3600, "count": 1,
                                   "lastTurnId": "t0", "armAtoms": 0, "at": NOW - age_h * 3600}}}
        if gate:
            d["walkGates"] = {(gate_key or SID): {"gate": gate, "at": NOW - 3600}}
        (Path(self.td.name) / "auto-nudge.json").write_text(json.dumps(d))
        km._autonudge_cache.clear()

    def _blocked(self):
        return bool(km.jd.load_goals(SID)["nodes"][self.gid].get("blocked"))

    def test_a_walked_ungated_record_is_the_walks_at_any_age(self):
        self._seed(age_h=300)                        # 300h old — the retired clock would have seized it
        km._awaiting_wake_outcomes(NOW, walked={SID})
        self.assertFalse(self._blocked(), "no gate, session walked → the walk owns it, whatever the age")

    def test_a_wedge_gate_hands_it_to_the_sweep_now(self):
        self._seed(gate="api-error", age_h=1)        # ONE hour old — the retired clock would have waited
        km._awaiting_wake_outcomes(NOW, walked={SID})
        self.assertTrue(self._blocked(), "the wedge's ending event can never come — the sweep acts now")

    def test_a_per_goal_skip_gate_counts_as_wedge(self):
        self._seed(gate="all-delegated", gate_key=SID + ":g1", age_h=1)
        km._awaiting_wake_outcomes(NOW, walked={SID})
        self.assertTrue(self._blocked(), "a record stranded before the evaluator is the sweep's")

    def test_a_transient_gate_stays_with_the_walk(self):
        self._seed(gate="needs-input", age_h=300)
        km._awaiting_wake_outcomes(NOW, walked={SID})
        self.assertFalse(self._blocked(), "its ending event re-runs the walk — no clock resurrection")

    def test_muted_stands_the_sweep_down(self):
        self._seed(gate="muted", age_h=300)
        km._awaiting_wake_outcomes(NOW, walked={SID})
        self.assertFalse(self._blocked(), "the user's own opt-out — the sweep never overrides it")

    def test_an_unwalked_session_is_the_sweeps(self):
        self._seed(age_h=1)                          # fresh — but its session is gone from the roster
        km._awaiting_wake_outcomes(NOW, walked=set())
        self.assertTrue(self._blocked(), "a dead session's spent wake — the sweep's original constituency")


if __name__ == "__main__":
    unittest.main()
