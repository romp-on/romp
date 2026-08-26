#!/usr/bin/env python3
"""Tests for build_timeline's `data.judging` feed (the judging-timeline band under the lanes).

`_derive_judging` reshapes the REAL artifacts the summarizer judges write — captions/<sid>.jsonl,
the goal tree's nodes, archive/<sid>.json — into {judge, sid, t, kind, text} marks the timeline view
draws as a second timeline. Since the P3.4 sweep (2026-07-07) done/block attribution reads each
node's verdict DIARY (the event's src field is the provenance; negComplete/negBlock retired), one
mark per witnessed verdict at its own evidence time; reconstructed (synth) history never fakes a
mark. Synthetic data only (placeholder UUID, invented captions/goals)."""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
# romp-judge must load first: romp-kernel imports it as `jd` at module load.
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 7200          # a 2-hour window for the derivation


def marks(caps, nodes, t0=T0):
    out = []
    km._derive_judging(SID, caps, {"nodes": nodes}, t0, out)
    return out


class DeriveJudging(unittest.TestCase):
    def test_captioner_one_mark_per_caption_grain_preserved(self):
        caps = {"u1": {"id": "u1", "grain": "segment", "t": NOW - 100, "caption": "Fixed the flicker"},
                "u2": {"id": "u2", "grain": "turn", "t": NOW - 40, "caption": "Wrapped the turn"}}
        cap = [m for m in marks(caps, {}) if m["judge"] == "captioner"]
        self.assertEqual(len(cap), 2)
        self.assertEqual({m["kind"] for m in cap}, {"segment", "turn"})
        self.assertTrue(all(m["sid"] == SID for m in cap))
        self.assertEqual(next(m for m in cap if m["kind"] == "turn")["text"], "Wrapped the turn")

    def test_captions_outside_the_window_are_dropped(self):
        caps = {"old": {"id": "old", "grain": "segment", "t": NOW - 99999, "caption": "ancient"}}
        self.assertEqual(marks(caps, {}), [])

    def test_captioner_volume_capped_per_session(self):
        caps = {str(i): {"id": str(i), "grain": "segment", "t": NOW - (200 - i), "caption": "c%d" % i}
                for i in range(km.JUDGE_CAP_LIMIT + 25)}
        cap = [m for m in marks(caps, {}) if m["judge"] == "captioner"]
        self.assertEqual(len(cap), km.JUDGE_CAP_LIMIT, "keeps only the most-recent JUDGE_CAP_LIMIT")
        # the kept set is the NEWEST ones (largest t)
        self.assertEqual(min(m["t"] for m in cap), NOW - (200 - 25))

    def test_planner_mint_sub_done_block(self):
        nodes = {
            "g1": {"id": "g1", "parentId": None, "t": NOW - 200, "text": "Top goal"},          # mint
            "g2": {"id": "g2", "parentId": "g1", "t": NOW - 150, "text": "a step"},             # sub
            "g3": {"id": "g3", "parentId": "g1", "t": NOW - 300, "text": "ship it",
                   "nodeComplete": True, "doneWhy": "shipped", "mt": NOW - 90,                  # sub + done
                   "log": [{"ev_t": NOW - 90, "src": "planner", "kind": "done", "why": "shipped"}]},
            "g4": {"id": "g4", "parentId": "g1", "t": NOW - 250, "text": "blocked one",
                   "blocked": True, "blockWhy": "needs a key", "mt": NOW - 80,                  # sub + block
                   "log": [{"ev_t": NOW - 80, "src": "planner", "kind": "block", "why": "needs a key"}]},
        }
        kinds = {m["kind"] for m in marks({}, nodes) if m["judge"] == "planner"}
        self.assertEqual(kinds, {"mint", "sub", "done", "block"})

    def test_grouper_courier_closer_keyed_off_node_artifacts(self):
        nodes = {
            "u": {"id": "u", "parentId": None, "t": NOW - 100, "text": "umbrella", "umbrella": True},
            "h": {"id": "h", "parentId": None, "t": NOW - 90, "text": "handoff goal",
                  "origin": {"peer": "PEER", "msgId": "m1"}},
            "c": {"id": "c", "parentId": "x", "t": NOW - 300, "text": "swept goal",
                  "nodeComplete": True, "doneWhy": "no work left", "mt": NOW - 70,
                  "log": [{"ev_t": NOW - 70, "src": "closer", "kind": "done", "why": "no work left"}]},
        }
        out = marks({}, nodes)
        by_judge = {m["judge"] for m in out}
        self.assertIn("grouper", by_judge)
        self.assertIn("courier", by_judge)
        self.assertIn("closer", by_judge)
        self.assertEqual(next(m for m in out if m["judge"] == "grouper")["kind"], "group")
        self.assertEqual(next(m for m in out if m["judge"] == "courier")["kind"], "plant")
        self.assertEqual(next(m for m in out if m["judge"] == "closer")["kind"], "close")
        # an umbrella / handoff node is owned by its judge — NOT also double-counted as a planner place
        planner_texts = {m["text"] for m in out if m["judge"] == "planner"}
        self.assertNotIn("umbrella", planner_texts)
        self.assertNotIn("handoff goal", planner_texts)

    def test_grouper_housekeeping_marks_key_on_the_groupOp_stamp(self):
        # T103: the surviving grouper ops (merge/split/retitle) append no diary events by design,
        # so the lane keys on apply_group's structure stamp — additive beside the node's own
        # mint/plant mark, while an ARCHIVED pre-T101 container still shows its old group mark
        nodes = {
            "m": {"id": "m", "parentId": None, "t": NOW - 200, "text": "merged survivor",
                  "groupOp": {"kind": "merge", "t": NOW - 50}},
            "r": {"id": "r", "parentId": None, "t": NOW - 5000, "text": "retitled card",
                  "groupOp": {"kind": "retitle", "t": NOW - 40}},
            "old": {"id": "old", "parentId": None, "t": NOW - 300, "text": "stale stamp",
                    "groupOp": {"kind": "split", "t": NOW - 99999}},
        }
        out = marks({}, nodes)
        g = {(m["text"], m["kind"]) for m in out if m["judge"] == "grouper"}
        self.assertIn(("merged survivor", "merge"), g)
        self.assertIn(("retitled card", "retitle"), g, "an old node's FRESH housekeeping still marks")
        self.assertNotIn(("stale stamp", "split"), g, "stamps outside the window stay dark")
        # the merged survivor ALSO keeps its own planner mint mark — the stamps are additive
        self.assertIn("merged survivor", {m["text"] for m in out if m["judge"] == "planner"})

    def test_the_diary_src_is_the_provenance(self):
        # one done, two ways: the closer swept it → its mark is the closer's, never the planner's
        nodes = {"c": {"id": "c", "parentId": "x", "t": NOW - 300, "text": "swept",
                       "nodeComplete": True, "doneWhy": "done", "mt": NOW - 70,
                       "log": [{"ev_t": NOW - 70, "src": "closer", "kind": "done", "why": "done"}]}}
        completions = [m for m in marks({}, nodes) if m["kind"] in ("close", "done")]
        self.assertEqual([m["judge"] for m in completions], ["closer"],
                         "the diary's src field attributes the verdict (negComplete retired)")

    def test_synth_history_never_fakes_a_judging_mark(self):
        # a backfilled (reconstructed) done is history bookkeeping, not a witnessed judge run
        nodes = {"c": {"id": "c", "parentId": "x", "t": NOW - 300, "text": "migrated",
                       "nodeComplete": True, "mt": NOW - 70,
                       "log": [{"ev_t": NOW - 70, "src": "judge", "kind": "done", "synth": True}]}}
        self.assertEqual([m for m in marks({}, nodes) if m["kind"] in ("close", "done")], [])

    def test_node_without_t_is_skipped(self):
        self.assertEqual(marks({}, {"bad": {"id": "bad", "parentId": None, "text": "no t"}}), [])

    def test_distiller_keyed_off_distilledMt_with_the_summary(self):
        nodes = {"g1": {"id": "g1", "parentId": None, "t": NOW - 300, "text": "Top goal",
                        "nodeComplete": True, "doneWhy": "done", "mt": NOW - 80,
                        "distilledMt": NOW - 70, "summary": "The key takeaway."}}
        dj = [m for m in marks({}, nodes) if m["judge"] == "distiller"]
        self.assertEqual(len(dj), 1)
        self.assertEqual(dj[0]["kind"], "distill")
        self.assertEqual(dj[0]["t"], NOW - 70)
        self.assertEqual(dj[0]["text"], "The key takeaway.", "the distiller mark carries the goal's summary")

    def test_completion_marks_land_at_the_work_END_not_the_prompt(self):
        # A goal's verdict ev_t is the completing SEGMENT'S START (its prompt/trigger time). A completion-
        # flavoured mark (done/close/block/distill/brief) must plot at that segment's work-END — passed via
        # seg_ends — so it sits AFTER the work bar, not on the prompt (the user 2026-06-19).
        nodes = {"g1": {"id": "g1", "parentId": None, "t": NOW - 300, "text": "Top", "nodeComplete": True,
                        "doneWhy": "shipped", "mt": NOW - 200, "distilledMt": NOW - 200, "summary": "key",
                        "log": [{"ev_t": NOW - 200, "src": "planner", "kind": "done", "why": "shipped"}]}}
        seg_ends = {NOW - 200: NOW - 140}                 # completing segment: started at mt, finished 60s later
        out = []
        km._derive_judging(SID, {}, {"nodes": nodes}, T0, out, seg_ends)
        self.assertEqual(next(m for m in out if m["kind"] == "done")["t"], NOW - 140, "done → work-END")
        self.assertEqual(next(m for m in out if m["kind"] == "distill")["t"], NOW - 140, "distill → work-END")

    def test_creation_marks_and_captions_stay_at_the_prompt(self):
        # A mint/sub is plotted at the prompt — a goal IS born when asked. Only completion marks move.
        nodes = {"g1": {"id": "g1", "parentId": None, "t": NOW - 200, "text": "Top"}}
        caps = {"u1": {"id": "u1", "grain": "segment", "t": NOW - 200, "caption": "cap"}}
        out = []
        km._derive_judging(SID, caps, {"nodes": nodes}, T0, out, {NOW - 200: NOW - 140})
        self.assertEqual(next(m for m in out if m["kind"] == "mint")["t"], NOW - 200, "a mint stays at the prompt")
        self.assertEqual(next(m for m in out if m["judge"] == "captioner")["t"], NOW - 200, "a caption stays at its segment")

    def test_seg_ends_absent_falls_back_to_the_evidence_time(self):
        # No seg_ends (the bare unit-test call) → completion marks keep the event's own ev_t placement.
        nodes = {"g1": {"id": "g1", "parentId": None, "t": NOW - 300, "text": "Top", "nodeComplete": True,
                        "doneWhy": "x", "mt": NOW - 80,
                        "log": [{"ev_t": NOW - 80, "src": "planner", "kind": "done", "why": "x"}]}}
        self.assertEqual(next(m for m in marks({}, nodes) if m["kind"] == "done")["t"], NOW - 80)

    def test_block_distiller_keyed_off_briefedMt_with_the_decision_brief(self):
        # The block-distiller's DECISION BRIEF (briefedMt/blockSummary) is the done-distiller's twin for a
        # BLOCKED top — it must ALSO emit a distiller mark (kind 'brief'), else the brief pops up on the
        # card but the distiller row reads as dead whenever the recent work was blocks, not completions
        # (the user 2026-06-18).
        nodes = {"g1": {"id": "g1", "parentId": None, "t": NOW - 300, "text": "Blocked top",
                        "blocked": True, "blockWhy": "owed a decision", "mt": NOW - 80,
                        "briefedMt": NOW - 60, "blockSummary": "Decide A or B; here is the context."}}
        dj = [m for m in marks({}, nodes) if m["judge"] == "distiller"]
        self.assertEqual(len(dj), 1, "a briefed blocked top emits one distiller mark")
        self.assertEqual(dj[0]["kind"], "brief")
        self.assertEqual(dj[0]["t"], NOW - 60, "the mark is keyed off briefedMt")
        self.assertEqual(dj[0]["text"], "Decide A or B; here is the context.",
                         "the block-distiller mark carries the goal's decision brief")

    def test_block_distiller_and_done_distiller_both_mark_a_block_then_done_goal(self):
        # A goal that went block->done carries briefedMt AND distilledMt independently → TWO distiller marks.
        nodes = {"g1": {"id": "g1", "parentId": None, "t": NOW - 300, "text": "Top", "nodeComplete": True,
                        "doneWhy": "done", "mt": NOW - 50, "distilledMt": NOW - 40, "summary": "Shipped.",
                        "briefedMt": NOW - 120, "blockSummary": "Earlier: decide A or B."}}
        kinds = sorted(m["kind"] for m in marks({}, nodes) if m["judge"] == "distiller")
        self.assertEqual(kinds, ["brief", "distill"], "both the brief and the takeaway mark the timeline")

    def test_closer_block_attributed_via_the_diary(self):
        nodes = {"b": {"id": "b", "parentId": "x", "t": NOW - 300, "text": "blocked top",
                       "blocked": True, "blockWhy": "needs a key", "mt": NOW - 70,
                       "log": [{"ev_t": NOW - 70, "src": "closer", "kind": "block", "why": "needs a key"}]}}
        blocks = [m for m in marks({}, nodes) if m["kind"] == "block"]
        self.assertEqual([m["judge"] for m in blocks], ["closer"], "the block event's src → closer")
        self.assertEqual(blocks[0]["text"], "needs a key")

    def test_planner_block_attributed_via_the_diary(self):
        nodes = {"b": {"id": "b", "parentId": "x", "t": NOW - 300, "text": "blocked top",
                       "blocked": True, "blockWhy": "needs input", "mt": NOW - 70,
                       "log": [{"ev_t": NOW - 70, "src": "planner", "kind": "block", "why": "needs input"}]}}
        blocks = [m for m in marks({}, nodes) if m["kind"] == "block"]
        self.assertEqual([m["judge"] for m in blocks], ["planner"], "the block event's src → planner")


if __name__ == "__main__":
    unittest.main()
