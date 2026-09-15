#!/usr/bin/env python3
"""Postal isolation holds on every sanctioned route (the user 2026-07-10): the mailbox-off boundary was
enforced only inside the postal bus (send_message), so agent mail could reach an isolated session through
the kernel's /send or /deliver — a peer's probe did exactly that. Now /deliver refuses a mailbox-off
target outright (the bus keeps the banner parked until the mailbox reopens), and /send refuses
postal-SHAPED content (the same recognizers _genuine_queued uses) to isolated targets while plain text
still passes — /send is the HUMAN channel and the user must always reach their own isolated session.
Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_isolation", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class PostalShaped(unittest.TestCase):
    """Agent mail by shape: the banner markers, and nothing else."""

    def test_banner_markers_are_agent_mail(self):
        self.assertTrue(km._postal_shaped("#################### POSTAL\nDELEGATE: take this"))
        self.assertTrue(km._postal_shaped("a delivered note <!-- romp-msg-id: 123 -->"))
        self.assertTrue(km._postal_shaped("\U0001F4EC mail from a peer"))

    def test_plain_user_text_is_not(self):
        self.assertFalse(km._postal_shaped("please write the handoff plan to a file"))
        self.assertFalse(km._postal_shaped(""))
        self.assertFalse(km._postal_shaped(None))


class PostalIsolated(unittest.TestCase):
    """_postal_isolated reads the session's mailbox flag, legacy key included."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = km.jd.STATE
        km.jd.STATE = Path(self.td.name)

    def tearDown(self):
        km.jd.STATE = self.saved
        self.td.cleanup()

    def _flags(self, flags):
        (km.jd.STATE / "session-flags.json").write_text(json.dumps({SID: flags}))

    def test_reads_the_mailbox_flag_and_legacy_twin(self):
        for key in ("postalServiceOff", "postalOff"):
            self._flags({key: True})
            self.assertTrue(km._postal_isolated(SID), key)
        self._flags({})
        self.assertFalse(km._postal_isolated(SID))


class RouteGates(unittest.TestCase):
    """Source pins: both routes gate on isolation, with the intended semantics."""

    def setUp(self):
        self.src = open(os.path.join(BIN, "romp-kernel")).read()

    def test_send_refuses_postal_shaped_mail_to_isolated_targets(self):
        # the gate sits in the one delivery door (_deliver_text, T370) that POST /send and a notice card's /send action both take
        self.assertIn('if _postal_shaped(text) and _postal_isolated(sid):', self.src)
        self.assertIn('ok, err, queued = _deliver_text(sid, body["text"])', self.src.split('u.path == "/send"')[1][:3000], "the route takes the door")

    def test_deliver_refuses_isolated_targets_outright_and_parks(self):
        self.assertIn('if _postal_isolated(sid):', self.src)
        self.assertIn('"injected": False', self.src.split('u.path == "/deliver"')[1][:2000],
                      "the bus reads injected:false and keeps the banner parked in its maildir")

    def test_the_refusals_name_the_boundary(self):
        self.assertGreaterEqual(self.src.count("mailbox is OFF"), 2)


class PolicyPins(unittest.TestCase):
    """The norms declare an isolation refusal final — the residual a route gate can't close — and teach
    send_message's `kind` parameter."""

    SKILL = os.path.join(os.path.dirname(BIN), "claude", "skills", "romp-postal", "SKILL.md")

    def test_mcp_instructions_declare_refusal_final(self):
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        self.assertIn("An isolation refusal is FINAL", src)
        self.assertIn("do NOT reroute", src)

    def test_skill_declares_refusal_final(self):
        src = open(self.SKILL).read()
        self.assertIn("An isolation refusal is final", src)

    def test_skill_teaches_the_kind_parameter(self):
        # the SessionStart hook's kind bullet is pinned in tests/romp-postal-context.bats; this pins the
        # skill's (the #964 review): the REQUIRED `kind` parameter with its three values, never the retired
        # DELEGATE:/COORDINATE:/QUESTION: body prefix beside it
        src = open(self.SKILL).read()
        self.assertIn("Set `kind` to `delegate`", src)
        for kind in ("`delegate`", "`coordinate`", "`question`"):
            self.assertIn(kind, src)
        self.assertNotIn("DELEGATE:", src)


if __name__ == "__main__":
    unittest.main()
