#!/usr/bin/env python3
"""Dissolution's resurfaced cards must distill (the user 2026-08-26, T110: completed tops stuck at
'Distilling…' after the T101 sweep re-parented them). Three pins, one per verified hole:
(1) THE TRIGGER — a dissolved completed child rides the NORMAL event pipeline end to end: the same
    rollup that re-parents it settles it to status=completed (or exports it via `confirming` while
    it is still the focus), _done_owed's null-summary arm marks it owed, and the next distill pass
    CALLS the distiller; a later rollup + pass re-distills nothing (no churn — cards move on new
    information only).
(2) THE STRAGGLERS — a store whose session the fleet walk never visits (outside discover's window,
    or transcripts gone) could never meet the distiller at all: _drain_undiscovered visits exactly
    the stores still owing (completed/confirming top, summary None), resolves transcripts by
    windowless direct lookup so an out-of-window session still gets its REAL summary, and settles a
    transcript-less one loudly through the existing no-work branch ("" sentinel + warn). The drain
    self-retires: the sentinel is non-null, so a drained store never re-enters.
(3) LOUD FAILURES — run_distill logs a session pass that dies instead of swallowing it (one
    poisoned goal used to kill a whole store's distills with zero calls and zero errors).
SYNTHETIC fixtures only; private synthetic sids."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
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
jd = SourceFileLoader("romp_judge_dissdistill", os.path.join(BIN, "romp-judge")).load_module()
em = jd.em

NOW = 1_787_700_000
T0 = NOW - 3600
SID = "f44e0001-1111-4222-8333-000000000001"    # private synthetic sids — never the shared placeholder
DEAD = "f44e0001-1111-4222-8333-000000000002"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _node(nid, text, parent, t=T0, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


RECORDS = [uline(T0, "please ship the demo ask", "u1", ps="typed"),
           aline(T0 + 60, "Shipped: the demo ask is done, with a regression test.", "a1", "u1")]


class DissolvedChildDistills(unittest.TestCase):
    """Pin (1): the trigger. Dissolution re-parents an already-completed child to top level, and the
    NORMAL pipeline distills it on the very next pass — no new stamp, no timer, no re-distill churn."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / (SID + ".jsonl")
        self.path.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
        s = em.parse_session(str(self.path), rompuuid=SID, candidate_files=[str(self.path)], now=NOW)
        self.segs = [sg["id"] for turn in s["turns"] for sg in em.segments(turn)]
        self._distill = jd.distill_llm
        self.calls = []
        jd.distill_llm = lambda *a, **k: (self.calls.append(a[0]),
                                          "BACKGROUND: b.\n\nTAKEAWAY: it shipped.")[1]

    def tearDown(self):
        jd.distill_llm = self._distill
        for d in (jd.GOALDIR, jd.GOALARCHDIR):
            try:
                (d / (SID + ".json")).unlink()
            except OSError:
                pass
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        self.td.cleanup()

    def _world(self, focus_child=False):
        st = {"rompUuid": SID, "seq": 3, "nodes": {}, "placements": {}, "status": {}}
        st["nodes"]["u"] = jd.GuardedNode(_node("u", "round umbrella", None, umbrella=True))
        st["nodes"]["c"] = jd.GuardedNode(_node("c", "the finished ask", "u", trail=list(self.segs)))
        jd.record_verdict(st, st["nodes"]["c"], "closer", "done", T0 + 100, why="shipped with a test")
        jd._mark_node_done(st, "c", "shipped with a test", T0 + 100)
        if focus_child:
            st["lastNode"] = "c"
        jd.rollup_status(st, False)                    # the dissolution sweep runs in THIS rollup
        jd.save_goals(SID, st)
        return st

    def test_the_dissolving_rollup_leaves_the_child_owed(self):
        st = self._world()
        self.assertIsNone(st["nodes"]["c"].get("parentId"), "re-parented to top")
        self.assertNotIn("u", st["nodes"], "the container is gone")
        self.assertEqual(st["status"].get("c"), "completed",
                         "the SAME rollup settles the resurfaced top — dissolution needs no extra stamp")
        self.assertTrue(jd._done_owed(st, "c"), "null summary → owed")

    def test_the_next_pass_distills_it_and_later_rollups_do_not_churn(self):
        self._world()
        n = jd._distill_session(SID, str(self.path), NOW)
        self.assertEqual(n, 1)
        self.assertEqual(self.calls, ["the finished ask"], "the distiller was CALLED for the child")
        st = jd.load_goals(SID)
        self.assertEqual(st["nodes"]["c"].get("summary"), "it shipped.")
        # idempotency: another writer rollup + another pass move nothing (no new information)
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        self.calls.clear()
        self.assertEqual(jd._distill_session(SID, str(self.path), NOW + 60), 0)
        self.assertEqual(self.calls, [], "no re-distill churn on later rollups")

    def test_a_focus_child_rides_the_confirming_export(self):
        st = self._world(focus_child=True)
        self.assertIn("c", set(st.get("confirming") or ()),
                      "still the focus → not yet settled, but exported for the distiller")
        n = jd._distill_session(SID, str(self.path), NOW)
        self.assertEqual(n, 1)
        self.assertEqual(self.calls, ["the finished ask"])


class StragglerDrain(unittest.TestCase):
    """Pin (2): stores the fleet walk never visits. The drain is keyed on the owed predicate, finds
    the transcript by windowless lookup when one exists, settles loudly when none does, and
    self-retires once nothing owes."""

    def setUp(self):
        self._distill = jd.distill_llm
        self._disc = jd.discover
        self.calls = []
        jd.distill_llm = lambda *a, **k: (self.calls.append(a[0]),
                                          "BACKGROUND: b.\n\nTAKEAWAY: found late.")[1]

    def tearDown(self):
        jd.distill_llm = self._distill
        jd.discover = self._disc
        for sid in (SID, DEAD):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
            try:
                (jd._overrides_dir() / (sid + ".jsonl")).unlink()
            except OSError:
                pass

    def _store(self, sid, trail, with_work_recorded=True):
        st = {"rompUuid": sid, "seq": 2, "nodes": {}, "placements": {}, "status": {}}
        st["nodes"]["g"] = jd.GuardedNode(_node("g", "an old finished ask", None,
                                                trail=trail if with_work_recorded else []))
        jd.record_verdict(st, st["nodes"]["g"], "closer", "done", T0 + 100, why="finished long ago")
        jd._mark_node_done(st, "g", "finished long ago", T0 + 100)
        jd.rollup_status(st, False)
        jd.save_goals(sid, st)

    def test_an_out_of_window_session_still_gets_its_real_summary(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
            s = em.parse_session(str(path), rompuuid=SID, candidate_files=[str(path)], now=NOW)
            segs = [sg["id"] for turn in s["turns"] for sg in em.segments(turn)]
            self._store(SID, segs)
            # the recency-windowed fleet is EMPTY; the windowless walk still knows the session
            jd.discover = lambda now, window=None, forks=True: (
                [] if window is None else [(SID, path, SID, "web")])
            n = jd.run_distill(now=NOW)
            self.assertEqual(n, 1)
            self.assertEqual(self.calls, ["an old finished ask"])
            self.assertEqual(jd.load_goals(SID)["nodes"]["g"].get("summary"), "found late.")

    def test_a_transcript_less_store_settles_loudly_not_forever_spinning(self):
        self._store(DEAD, ["seg-that-resolves-nowhere"])
        jd.discover = lambda now, window=None, forks=True: []
        n = jd.run_distill(now=NOW)
        self.assertEqual(self.calls, [], "no transcript → no LLM call, ever")
        nd = jd.load_goals(DEAD)["nodes"]["g"]
        self.assertEqual(nd.get("summary"), "", "the sentinel ends the spinner")
        self.assertTrue(any(w.get("kind") == "summary-unreadable" for w in (nd.get("warns") or [])),
                        "recorded work that resolved nowhere warns — loud, never a silent blank")

    def test_the_drain_self_retires(self):
        self._store(DEAD, ["seg-that-resolves-nowhere"])
        jd.discover = lambda now, window=None, forks=True: []
        jd.run_distill(now=NOW)
        before = json.dumps(jd.load_goals(DEAD), sort_keys=True, default=str)
        visits = []
        saved = jd._distill_session
        try:
            jd._distill_session = lambda *a, **k: (visits.append(a[0]), saved(*a, **k))[1]
            jd.run_distill(now=NOW + 60)
        finally:
            jd._distill_session = saved
        self.assertEqual(visits, [], "nothing owes → the drain visits no store at all")
        self.assertEqual(json.dumps(jd.load_goals(DEAD), sort_keys=True, default=str), before)

    def test_a_cleared_or_summarized_top_never_triggers_the_drain(self):
        self._store(DEAD, [])
        st = jd.load_goals(DEAD)
        st["nodes"]["g"]["summary"] = "already told"
        jd.save_goals(DEAD, st)
        jd.discover = lambda now, window=None, forks=True: []
        visits = []
        saved = jd._distill_session
        try:
            jd._distill_session = lambda *a, **k: (visits.append(a[0]), 0)[1]
            jd.run_distill(now=NOW)
        finally:
            jd._distill_session = saved
        self.assertEqual(visits, [])


class LoudFailures(unittest.TestCase):
    """Pin (3): a dying session pass is LOGGED, never swallowed — the 'zero calls, zero errors'
    shape must be impossible to reproduce silently."""

    def test_a_poisoned_session_pass_logs_a_judge_error(self):
        saved_ds, saved_disc, saved_log = jd._distill_session, jd.discover, jd._log_judge_error
        rows = []
        try:
            jd.discover = lambda now, window=None, forks=True: (
                [] if window is not None else [(SID, Path("/dev/null"), SID, "web")])
            jd._distill_session = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("poisoned goal"))
            jd._log_judge_error = lambda judge, fsid, err, note=None, goal=None, seg=None: \
                rows.append((judge, fsid, err, note))
            jd.run_distill(now=NOW)
        finally:
            jd._distill_session, jd.discover, jd._log_judge_error = saved_ds, saved_disc, saved_log
        self.assertEqual(len(rows), 1)
        judge, fsid, err, note = rows[0]
        self.assertEqual((judge, fsid, err), ("distiller", SID, "pass-crash"))
        self.assertIn("poisoned goal", note or "")


if __name__ == "__main__":
    unittest.main()
