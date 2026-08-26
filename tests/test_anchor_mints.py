#!/usr/bin/env python3
"""Anchor mints prefer atoms a click can actually land on (the user 2026-08-25, routed from the
settle-alias diagnosis): (1) the reply anchor skips a machine-cut turn's null settle-reply ("No
response requested." / model "<synthetic>") whenever a substantive assistant atom exists in the
same segment — the settle survives as the anchor of last resort on settle-only turns (the chat's
alias belt covers that residue); (2) the PROMPT anchor resolves to the enclosing user MESSAGE's
uuid, never a type:"attachment" record — attachments never become chat events, so a title click on
a card whose promptUuid names one could never land by id. Unaffected anchors are byte-identical.
SYNTHETIC fixtures only."""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_anch", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd


def _asst(uuid, text, model="claude-x"):
    return {"type": "assistant", "uuid": uuid,
            "message": {"model": model, "content": [{"type": "text", "text": text}]}}


class ReplyAnchorSkipsTheSettle(unittest.TestCase):
    def test_cut_turn_anchors_at_the_substantive_atom(self):
        atoms = [{"type": "user", "uuid": "u1"},
                 _asst("a1", "the exporter is wired; tests pass"),
                 _asst("a2", "No response requested.", model="<synthetic>")]
        work, reply = km._seg_anchors(atoms)
        self.assertEqual(reply, "a1", "the last REAL reply wins — the settle is a seam, not a reply")
        self.assertEqual(work, "a1", "the work anchor is untouched (first assistant atom)")

    def test_settle_only_turn_keeps_the_settle(self):
        atoms = [{"type": "user", "uuid": "u1"},
                 _asst("a2", "No response requested.", model="<synthetic>")]
        _, reply = km._seg_anchors(atoms)
        self.assertEqual(reply, "a2", "nothing else exists — the alias belt covers this residue")

    def test_the_synthetic_model_marks_the_settle_even_with_other_text(self):
        atoms = [_asst("a1", "done and verified"),
                 _asst("a2", "anything the CLI stamped synthetic", model="<synthetic>")]
        _, reply = km._seg_anchors(atoms)
        self.assertEqual(reply, "a1")

    def test_ordinary_turns_are_byte_identical(self):
        atoms = [{"type": "user", "uuid": "u1"},
                 _asst("a1", "thinking through it"),
                 _asst("a2", "here is the answer")]
        self.assertEqual(km._seg_anchors(atoms), ("a1", "a2"), "unaffected anchors never move")


class PromptAnchorSkipsAttachments(unittest.TestCase):
    def test_attachment_trigger_resolves_to_the_user_message(self):
        seg = {"trigger": "att1",
               "atoms": [{"type": "attachment", "uuid": "att1"},
                         {"type": "user", "uuid": "u1"},
                         _asst("a1", "on it")]}
        self.assertEqual(jd._prompt_anchor_uuid(seg), "u1",
                         "a title click lands on the USER message — attachments are not chat events")
        self.assertEqual(jd._seg_anchor(seg), "u1", "…and every prompt mint flows through the rule")

    def test_a_user_trigger_is_byte_identical(self):
        seg = {"trigger": "u1", "atoms": [{"type": "user", "uuid": "u1"}, _asst("a1", "ok")]}
        self.assertEqual(jd._prompt_anchor_uuid(seg), "u1")
        self.assertEqual(jd._seg_anchor(seg), "u1")

    def test_no_trigger_keeps_the_none_and_the_head_fallback(self):
        seg = {"trigger": None,
               "atoms": [{"type": "attachment", "uuid": "att1"}, {"type": "user", "uuid": "u1"}]}
        self.assertIsNone(jd._prompt_anchor_uuid(seg), "no trigger → callers keep their own fallbacks")
        self.assertEqual(jd._seg_anchor(seg), "u1",
                         "the minted-node fallback skips the attachment for the segment's real head")

    def test_attachment_only_segment_keeps_the_raw_trigger(self):
        seg = {"trigger": "att1", "atoms": [{"type": "attachment", "uuid": "att1"}]}
        self.assertEqual(jd._prompt_anchor_uuid(seg), "att1",
                         "nothing better exists — the chat's alias/time fallback owns the residue")


if __name__ == "__main__":
    unittest.main()
