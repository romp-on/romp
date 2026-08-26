#!/usr/bin/env python3
"""The courier may DEMOTE a declared message kind, never promote it (the user 2026-07-27).

A sender-declared `question` was only "a strong prior" to the courier model, which promoted a
substantial five-question ask into a delegation — planting a recipient-side card whose whole content
was a judge summary of the reply. A question already rides the SENDER's cards (owed-reply tracking)
and the initiator summarizes the answer, so the recipient card is pure noise. The rule now:

- declared coordinate/question → filed `fyi` outright, with NO courier model call;
- declared delegate → the model may still demote (file fyi) when the body hands nothing over;
- undeclared legacy mail → the model's verdict stands, either way.

Synthetic fixtures only (placeholder UUIDs, invented text, hostname TESTHOST)."""
import json
import os
import re
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
jd = SourceFileLoader("romp_judge_kinddemote", os.path.join(BIN, "romp-judge")).load_module()

RECIP = "11111111-2222-3333-4444-555555555555"
SENDER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MID = "1781100000.11111_22222.TESTHOST"
T0 = 1781100000
DELEGATING = '{"verdict": "delegating", "goal": 0, "text": "check the subnet layout"}'
COORDINATING = '{"verdict": "coordinating", "goal": 0, "text": ""}'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class CourierKindDemoteOnly(unittest.TestCase):
    def setUp(self):
        # chain-rooted minting (2026-08-25) gates recipient tops on a user-rooted sender chain —
        # ORTHOGONAL to this file's subject, so the gate is held open here; its own truth table
        # lives in tests/test_chain_rooted_minting.py
        self._rooted_saved = jd._delegate_user_rooted
        jd._delegate_user_rooted = lambda *a, **k: True
        self.addCleanup(lambda: setattr(jd, "_delegate_user_rooted", self._rooted_saved))
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        (proj / munged).mkdir(parents=True)
        self.proj_dir = proj / munged
        names = td / "names"; names.mkdir()
        (names / RECIP).write_text("recip\t%s\t#abcdef\n" % str(cdir))
        tl = td / "timeline"; tl.mkdir()

        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.courier_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.MESSAGES = tl / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd.MESSAGES.write_text(json.dumps(
            {"t": T0 - 5, "ev": "sent", "id": MID, "from": "sender", "from_id": SENDER,
             "to_id": RECIP, "body": "what subnet is the new box on?"}) + "\n")
        self.calls = []
        jd.courier_llm = lambda *a, **k: self.calls.append(k.get("declared", "")) or self.reply
        self.reply = DELEGATING
        jd._PARSE_CACHE.clear()
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd._postal_from_memo["key"] = None

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.courier_llm) = self.saved
        jd._postal_from_memo["key"] = None
        self.td.cleanup()

    def _deliver(self, kind):
        kind_line = ("\n<!-- romp-msg-kind: %s -->" % kind) if kind else ""
        recs = [uline(T0, "what subnet is the new box on?\n<!-- romp-msg-id: %s -->%s" % (MID, kind_line), "u1"),
                aline(T0 + 30, "It's on the flat /24.", "a1", "u1")]
        (self.proj_dir / (RECIP + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in recs) + "\n")
        jd._PARSE_CACHE.clear()
        jd._discover_cache["fp"] = None
        jd.run_courier(now=T0 + 100)
        store = jd.load_goals(RECIP)
        planted = [nd for nd in store["nodes"].values() if isinstance(nd.get("origin"), dict)]
        seg_id = next(k for k in store["placements"] if not k.endswith(("#p", "#d", "#live")))
        return planted, store["placements"][seg_id]

    def test_declared_question_files_fyi_without_a_model_call(self):
        planted, placement = self._deliver("question")
        self.assertEqual(planted, [], "a declared question never plants a recipient card")
        self.assertEqual(placement, "fyi")
        self.assertEqual(self.calls, [], "resolved from the declaration alone — no courier call")

    def test_declared_coordinate_files_fyi_without_a_model_call(self):
        planted, placement = self._deliver("coordinate")
        self.assertEqual(planted, [])
        self.assertEqual(placement, "fyi")
        self.assertEqual(self.calls, [])

    def test_declared_delegate_still_plants(self):
        planted, placement = self._deliver("delegate")
        self.assertEqual(len(planted), 1, "a real delegation still plants the recipient's goal")
        self.assertEqual(self.calls, ["delegate"])

    def test_declared_delegate_can_be_demoted(self):
        self.reply = COORDINATING
        planted, placement = self._deliver("delegate")
        self.assertEqual(planted, [], "the model may still demote a delegate that hands nothing over")
        self.assertEqual(placement, "fyi")

    def test_legacy_undeclared_mail_keeps_the_model_verdict(self):
        planted, placement = self._deliver("")
        self.assertEqual(len(planted), 1, "no declaration → the courier's verdict stands")
        self.assertEqual(self.calls, [""])


if __name__ == "__main__":
    unittest.main()
