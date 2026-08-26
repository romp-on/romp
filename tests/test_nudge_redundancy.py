#!/usr/bin/env python3
"""Digest glossing + the nudge redundancy gate (the user 2026-08-23, via the optimizer's audit).
(a) The card-prose writers (distiller, briefer) define any NAMED artifact in one clause at first
mention — 2 of the user's 8 typed turns on 08-22 were pure 'wait, what is that?' re-asks. Prompt
pins. (b) Before firing, a nudge checks whether the session's LAST message already reports the
goal's status (2 of 3 fires came 12-13 min after the asked-about status was reported); a YES
records the report as the ANSWER and skips the fire; any check failure fires as before. SYNTHETIC."""
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class DigestGlossing(unittest.TestCase):
    def test_both_prose_writers_carry_the_glossing_rule(self):
        for name, sysp in (("distiller", jd.DISTILL_SYS), ("briefer", jd.BLOCK_BRIEF_SYS)):
            self.assertIn("one-clause definition at its first mention", sysp,
                          "%s must define named artifacts — the reader may never have seen the name" % name)


class NudgeRedundancy(unittest.TestCase):
    def test_yes_answer_skips_and_no_answer_fires(self):
        saved = jd._judge_run
        jd._judge_run = lambda *a, **k: "yes"
        try:
            self.assertTrue(jd.nudge_redundant("Ship the exporter", "The exporter shipped; suites green."))
        finally:
            jd._judge_run = saved
        jd._judge_run = lambda *a, **k: "no"
        try:
            self.assertFalse(jd.nudge_redundant("Ship the exporter", "Working on the importer now."))
        finally:
            jd._judge_run = saved

    def test_failures_and_blanks_fire_the_nudge(self):
        saved = jd._judge_run
        jd._judge_run = lambda *a, **k: ""
        try:
            self.assertFalse(jd.nudge_redundant("goal", "recent"), "the check is an optimization")
        finally:
            jd._judge_run = saved
        self.assertFalse(jd.nudge_redundant("", "recent text"))
        self.assertFalse(jd.nudge_redundant("goal text", ""))

    def test_last_assistant_text_reads_the_tail(self):
        td = tempfile.mkdtemp()
        p = os.path.join(td, "t.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "older reply"}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "text", "text": "a question"}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "the exporter shipped; nothing is blocked"}]}}) + "\n")
        self.assertEqual(km._last_assistant_text(p), "the exporter shipped; nothing is blocked")
        self.assertEqual(km._last_assistant_text(os.path.join(td, "missing.jsonl")), "")

    def test_the_fire_path_carries_the_gate_with_the_skip_cap(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        # the gate judges through _judge_batch since the fire-time freshness guard (2026-08-25):
        # the snapshot read carries its timestamp so a report landing mid-deliberation holds the
        # fire and re-judges once — the anchors below moved with it
        self.assertIn("jd.nudge_redundant(gtxt, report)", src)
        self.assertIn('recent, recent_ts = _last_assistant_report(s["path"])', src)
        self.assertIn("redundantSkips=skips + 1", src)
        self.assertIn("if skips < 2 and gtxt", src,
                      "two consecutive skips max — past that the nudge fires regardless, so the "
                      "gate can never become a forever-pause with no reviver")
        self.assertIn("redundantSkips=0", src, "a real fire resets the count")


if __name__ == "__main__":
    unittest.main()
