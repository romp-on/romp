#!/usr/bin/env python3
"""The re-asking finished delegation (the user 2026-08-25): a delegated ask whose tracking child
completed via run_propagate stayed open forever — the steps-finished nomination showed the closer
an ask whose only visible history was the DISPATCH (the recipient's resolution lives on another
session's tree), the closer correctly omitted, and closerLookT sealed it (nothing files in a
finished subtree again) while the auto-nudge re-asked the finished question and the spliced-done
strip cut off every answer. Three pieces close the class:
(1) run_propagate carries the RECIPIENT'S OWN RESOLUTION (doneWhy, else summary head) into the
    sender-side tracking node's done-why — the report-back's substance travels at the report-back
    event;
(2) the closer's steps-finished nomination shows that report as a marked "Delegation reports"
    section, so the ruling has the completion evidence;
(3) _deleg_unseen re-arms the nomination for any candidate whose done handoff child no look has
    seen WITH the report visible (delegLookT, stamped beside closerLookT) — the sealed backlog
    re-nominates exactly once post-upgrade, future completions re-arm by their own done filing.
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_deleg_report", os.path.join(BIN, "romp-judge")).load_module()

T = 1_787_000_000
SENDER = "d17a0001-1111-4222-8333-000000000001"   # private synthetic sids — never the shared placeholder
RECIP = "d17a0001-1111-4222-8333-000000000002"
MID = "1787000000.000000_1.TESTHOST"


def _node(nid, text, parent, t=T, **kw):
    return {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": [], **kw}


class World(unittest.TestCase):
    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/tmp/none-a.jsonl", None, "mgr"),
            (RECIP, "/tmp/none-b.jsonl", None, "api")]

    def tearDown(self):
        jd.discover = self._saved
        for sid in (SENDER, RECIP):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
            try:
                (jd.STATE / "overrides" / (sid + ".jsonl")).unlink()
            except OSError:
                pass

    def _plant(self, done_why=None, summary=None):
        """Sender ask X with a courier-planted tracking child; recipient completes its goal."""
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":g1"] = _node(SENDER + ":g1", "Show a cancellable finding indicator", None)
        st["seq"] = 1
        hid = jd._plant_handoff_track(st, SENDER + ":g1", "seek indicator plus silent-seek fix",
                                      RECIP, "api", T + 20, MID)
        jd.save_goals(SENDER, st)
        rt = jd.load_goals(RECIP)
        rn = _node(RECIP + ":g5", "Cancellable seek indicator", None,
                   origin={"peer": SENDER, "goalId": hid, "msgId": MID})
        if summary:
            rn["summary"] = summary
        rt["nodes"][RECIP + ":g5"] = rn
        jd.record_verdict(rt, rt["nodes"][RECIP + ":g5"], "closer", "done", T + 100, why=done_why)
        jd.save_goals(RECIP, rt)
        return hid


class PropagateCarriesTheResolution(World):
    def test_done_why_travels(self):
        hid = self._plant(done_why="Shipped the cancellable indicator; suite green; merged")
        self.assertEqual(jd.run_propagate(now=T + 200), 1)
        st = jd.load_goals(SENDER)
        self.assertTrue(st["nodes"][hid].get("nodeComplete"))
        self.assertIn("Shipped the cancellable indicator", st["nodes"][hid].get("doneWhy") or "",
                      "the recipient's own resolution rides the sender-side why")

    def test_summary_head_is_the_fallback(self):
        hid = self._plant(summary="The indicator shipped with a cancel X.\nMore detail below.")
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        why = st["nodes"][hid].get("doneWhy") or ""
        self.assertIn("The indicator shipped with a cancel X.", why)
        self.assertNotIn("More detail below", why, "the head only — capped like every quoted why")

    def test_bare_form_when_the_recipient_carries_nothing(self):
        hid = self._plant()
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        self.assertTrue((st["nodes"][hid].get("doneWhy") or "").startswith("completed by api (delegated)"))


class DelegUnseenReArm(World):
    def _sealed_world(self):
        hid = self._plant(done_why="Shipped it")
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        # pre-fix seal: the closer looked AFTER the propagate filing, without the report in view
        st["nodes"][SENDER + ":g1"]["closerLookT"] = jd._newest_filed(
            st["nodes"], {None: [SENDER + ":g1"], SENDER + ":g1": [hid]}, SENDER + ":g1") + 10
        jd.save_goals(SENDER, st)
        return hid, jd.load_goals(SENDER)

    def test_a_sealed_candidate_re_nominates_exactly_once(self):
        hid, st = self._sealed_world()
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertIn(SENDER + ":g1", cands,
                      "sealed pre-fix world: the report was never in view — nominate once")
        # the post-fix look stamps delegLookT AT/after the done filing (production stamps now(),
        # always >= every existing `at`) → sealed again on THIS evidence
        done_at = max(e["at"] for e in st["nodes"][hid]["log"] if e.get("kind") == "done")
        st["nodes"][SENDER + ":g1"]["delegLookT"] = done_at + 1
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertNotIn(SENDER + ":g1", cands, "a look that saw the report seals the re-arm")

    def test_plain_children_keep_the_old_gate_exactly(self):
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":g1"] = _node(SENDER + ":g1", "Plain parent", None)
        st["nodes"][SENDER + ":g2"] = _node(SENDER + ":g2", "Plain step", SENDER + ":g1", t=T + 10)
        jd.record_verdict(st, st["nodes"][SENDER + ":g2"], "closer", "done", T + 50)
        st["nodes"][SENDER + ":g1"]["closerLookT"] = jd._newest_filed(
            st["nodes"], {None: [SENDER + ":g1"], SENDER + ":g1": [SENDER + ":g2"]}, SENDER + ":g1") + 10
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertNotIn(SENDER + ":g1", cands,
                         "no handoff child → the closerLookT seal is byte-identical to before")

    def test_a_future_completion_re_arms_naturally(self):
        hid, st = self._sealed_world()
        done_at = max(e["at"] for e in st["nodes"][hid]["log"] if e.get("kind") == "done")
        st["nodes"][SENDER + ":g1"]["delegLookT"] = done_at + 1   # the first report was seen
        # a SECOND delegation completes LATER (a plain-dict fixture, so the filing's arrival
        # time is controlled — record_verdict stamps wall-clock `at`)
        st["nodes"][SENDER + ":g9"] = _node(
            SENDER + ":g9", "↪ delegated to api: follow-on", SENDER + ":g1", t=T + 600,
            handoff={"peer": RECIP, "msgId": "m2"}, nodeComplete=True,
            log=[{"ev_t": T + 700, "src": "courier", "kind": "done",
                  "why": "completed by api (delegated): follow-on shipped", "at": done_at + 100}])
        cands = {nd["id"] for nd in jd._subtree_done_candidates(st)}
        self.assertIn(SENDER + ":g1", cands, "a new report is new information — nominate again")


class ReportLines(World):
    def test_enriched_why_supplies_the_substance(self):
        hid = self._plant(done_why="Shipped the indicator")
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        lines = jd._deleg_report_lines(st, SENDER + ":g1")
        self.assertEqual(len(lines), 1)
        self.assertIn("Shipped the indicator", lines[0])

    def test_bare_why_fetches_the_recipient_read_only(self):
        hid = self._plant(done_why="Shipped the indicator with a cancel X")
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        # simulate a PRE-FIX store: strip the substance back to the bare form
        with jd._authority():
            st["nodes"][hid]["doneWhy"] = "completed by api (delegated)"
        lines = jd._deleg_report_lines(st, SENDER + ":g1")
        self.assertIn("Shipped the indicator with a cancel X", lines[0],
                      "the recipient join recovers the report for pre-fix stores")

    def test_missing_recipient_still_reports_the_completion(self):
        hid = self._plant(done_why="Shipped")
        jd.run_propagate(now=T + 200)
        (jd.GOALDIR / (RECIP + ".json")).unlink()
        st = jd.load_goals(SENDER)
        with jd._authority():
            st["nodes"][hid]["doneWhy"] = "completed by api (delegated)"
        lines = jd._deleg_report_lines(st, SENDER + ":g1")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("completed it."), "no substance, but never silence")

    def test_cross_host_peer_skips_the_fetch(self):
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":g1"] = _node(SENDER + ":g1", "Remote ask", None)
        st["nodes"][SENDER + ":g2"] = _node(SENDER + ":g2", "↪ delegated to web", SENDER + ":g1",
                                            t=T + 10, handoff={"peer": "TESTHOST:" + RECIP, "msgId": "m3"})
        jd.record_verdict(st, st["nodes"][SENDER + ":g2"], "courier", "done", T + 50,
                          why="reported back by TESTHOST:" + RECIP + " (delegated cross-host)")
        st["nodes"][SENDER + ":g2"]["nodeComplete"] = True
        lines = jd._deleg_report_lines(st, SENDER + ":g1")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("completed it."),
                        "another kernel's stores are not ours to read")


class CloseTurnCarriesTheReport(World):
    def test_the_menu_block_and_the_ruling_complete_the_ask(self):
        hid = self._plant(done_why="Shipped the cancellable indicator")
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":g1"]["closerLookT"] = jd._newest_filed(
            st["nodes"], {None: [SENDER + ":g1"], SENDER + ":g1": [hid]}, SENDER + ":g1") + 10
        jd.save_goals(SENDER, st)
        st = jd.load_goals(SENDER)
        seen = {}
        saved = jd.closer_llm
        jd.closer_llm = lambda work, menu_text, hist, lifts=None: (
            seen.__setitem__("menu", menu_text) or
            '{"done":[{"goal":1,"why":"the delegate shipped it and reported back"}],"block":[]}')
        try:
            turn = {"id": SENDER + ":t9", "t": T + 900, "ended": True, "atoms": [],
                    "end": T + 901, "trigger": None}
            newly = jd._close_turn(st, turn, seg_by_id={})
        finally:
            jd.closer_llm = saved
        self.assertIn("Delegation reports", seen.get("menu") or "",
                      "the cross-session report is a marked evidence section")
        self.assertIn("Shipped the cancellable indicator", seen["menu"])
        self.assertIn(SENDER + ":g1", newly, "the closer's done lands on the nominated ask")
        self.assertTrue(st["nodes"][SENDER + ":g1"].get("nodeComplete"))
        self.assertTrue(int(st["nodes"][SENDER + ":g1"].get("delegLookT") or 0) > 0,
                        "the look that saw the report seals the re-arm")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turn(records, rompuuid=SENDER):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (rompuuid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        s = jd.em.parse_session(str(p), rompuuid=rompuuid, candidate_files=[str(p)], now=T + 900)
        return s["turns"][0]


class CloserHandoffStandDown(World):
    """apply_close leg: a done verdict aimed at a '↪ delegated' tracking node stands down — its
    deciding event is the RECIPIENT's completion (run_propagate / the reply sweep), never this
    session's own dispatch-time prose. The audited specimen: the closer done'd the tracker at send
    time ('queued to the peer'), propagate's real completion then no-op'd on the already-done node,
    and the parent ask's nomination sealed on dispatched-only evidence."""

    def _sender(self):
        st = {"rompUuid": SENDER, "seq": 3, "nodes": {}, "placements": {}, "status": {}}
        st["nodes"]["t1"] = _node("t1", "\u21aa delegated to web: seek indicator", None, t=T + 10,
                                  handoff={"peer": RECIP, "msgId": MID})
        return st

    def test_a_dispatch_time_done_is_refused_and_a_plain_done_lands(self):
        hid = self._plant(done_why="indicator wired; suite green")
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":p1"] = _node(SENDER + ":p1", "write the release note", None, t=T + 5)
        tracker = st["nodes"][hid]
        menu = [tracker, st["nodes"][SENDER + ":p1"]]
        newly = jd.apply_close(st, menu, {"done": {1: "queued to the peer (T67)", 2: "note written"},
                                          "block": {}}, t=T + 500)
        self.assertFalse(tracker.get("nodeComplete"),
                         "the tracker's ending event belongs to run_propagate, not this closer")
        self.assertEqual([r for r in tracker.get("log") or [] if r.get("kind") == "done"], [],
                         "a stand-down files nothing in either direction")
        self.assertIn(SENDER + ":p1", newly,
                      "the same reply's verdict on a plain node still lands")

    def test_propagate_still_owns_the_completion(self):
        hid = self._plant(done_why="indicator wired; suite green")
        st = jd.load_goals(SENDER)
        jd.apply_close(st, [st["nodes"][hid]], {"done": {1: "queued"}, "block": {}}, t=T + 500)
        self.assertFalse(st["nodes"][hid].get("nodeComplete"))
        jd.save_goals(SENDER, st)
        jd.run_propagate(now=T + 900)
        st2 = jd.load_goals(SENDER)
        self.assertTrue(st2["nodes"][hid].get("nodeComplete"),
                        "the authoritative writer's filing still lands after the stand-down")
        self.assertIn("indicator wired", st2["nodes"][hid].get("doneWhy") or "",
                      "…and it carries the recipient's own resolution")


NUDGE = ("<!-- romp-injected -->Status check please. <!-- romp-goal-id: U -->")


class CitedLeavesAreTopsNow(World):
    """T103: the cited-umbrella descendants channel retired with containers — T101's dissolution
    (every writer's rollup) makes a once-stranded leaf its own TOP, which rides the PLAIN
    status-report channel. One pin end to end: the umbrella world dissolves and the leaf rides."""

    def test_the_dissolved_leaf_rides_the_plain_channel(self):
        st = {"rompUuid": SENDER, "seq": 9, "nodes": {}, "placements": {}, "status": {}}
        st["nodes"]["U"] = _node("U", "Feed renders correctly", None, umbrella=True)
        st["nodes"]["leaf"] = _node("leaf", "Show cancellable seek indicator", "U")
        jd.rollup_status(st, False)                    # T101: the container dissolves here
        self.assertNotIn("U", st["nodes"])
        self.assertIsNone(st["nodes"]["leaf"].get("parentId"), "the leaf is its own card")
        turn = _turn([
            {"type": "user", "timestamp": _iso(T + 600), "uuid": "n1", "parentUuid": None,
             "promptSource": "sdk", "message": {"role": "user", "content": NUDGE}},
            {"type": "assistant", "timestamp": _iso(T + 610), "uuid": "a1", "parentUuid": "n1",
             "message": {"role": "assistant", "content": [{"type": "text",
                         "text": "The seek indicator shipped and merged; suite green."}],
                         "stop_reason": "end_turn"}}])
        got = [nd["id"] for nd in jd._status_report_candidates(st, turn)]
        self.assertIn("leaf", got, "a top rides the plain widened menu — no special channel needed")


if __name__ == "__main__":
    unittest.main()
