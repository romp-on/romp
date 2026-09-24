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
pm = load_source("romp_postal_isolation_twin", os.path.join(BIN, "romp-postal-service"))

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

    def _raw_flags(self, flags):
        (km.jd.STATE / "session-flags.json").write_text(json.dumps(flags))

    def test_the_master_key_isolates_a_session_with_no_override(self):
        # The master default (the user 2026-08-27): sessions a person opened separately are separate work,
        # so cross-session mail is off unless a session opts in. "*" can never collide with a sid.
        self._raw_flags({km.POSTAL_ALL_KEY: {"postalServiceOff": True}})
        self.assertTrue(km._postal_isolated(SID))
        self.assertTrue(km._postal_isolated("99999999-8888-7777-6666-555555555555"))

    def test_a_session_opt_IN_beats_the_master(self):
        # most-specific-wins, the notify bell's rule — an explicit False is a real answer, not an absence
        self._raw_flags({km.POSTAL_ALL_KEY: {"postalServiceOff": True}, SID: {"postalServiceOff": False}})
        self.assertFalse(km._postal_isolated(SID))

    def test_a_session_override_isolates_with_the_master_absent(self):
        self._raw_flags({SID: {"postalServiceOff": True}})
        self.assertTrue(km._postal_isolated(SID))

    def test_no_flags_at_all_leaves_the_postal_service_on(self):
        self._raw_flags({})
        self.assertFalse(km._postal_isolated(SID), "romp's shipped default is unchanged — mail works")

    def _stored(self):
        return json.loads((km.jd.STATE / "session-flags.json").read_text())

    def test_the_setter_opts_a_session_out_of_a_master_isolation(self):
        # the lane toggle's own write path: False under a master True must STICK as an explicit False
        self._raw_flags({km.POSTAL_ALL_KEY: {"postalServiceOff": True}})
        km._set_session_flag(SID, "postalServiceOff", False)
        self.assertEqual(self._stored()[SID], {"postalServiceOff": False})
        self.assertFalse(km._postal_isolated(SID))
        self.assertFalse(km._painted_flag_value(SID, "postalServiceOff"), "the toggle repaints as mail on")

    def test_isolation_is_pinned_so_a_later_master_flip_cannot_open_it(self):
        self._raw_flags({km.POSTAL_ALL_KEY: {"postalServiceOff": True}, SID: {"postalServiceOff": False}})
        km._set_session_flag(SID, "postalServiceOff", True)
        self.assertEqual(self._stored()[SID], {"postalServiceOff": True})
        km._set_session_flag(km.POSTAL_ALL_KEY, "postalServiceOff", False)
        self.assertTrue(km._postal_isolated(SID), "the session's last choice was isolate")

    def test_an_opt_in_with_no_isolating_master_stores_nothing(self):
        self._raw_flags({SID: {"postalServiceOff": True}})
        km._set_session_flag(SID, "postalServiceOff", False)
        self.assertNotIn(SID, self._stored())
        self.assertFalse(km._postal_isolated(SID))

    def test_the_setter_clears_a_legacy_key_so_it_cannot_outvote_the_choice(self):
        self._raw_flags({SID: {"postalOff": True}})
        km._set_session_flag(SID, "postalServiceOff", False)
        self.assertNotIn(SID, self._stored())
        self.assertFalse(km._postal_isolated(SID))

    def test_the_master_itself_is_set_through_the_same_setter(self):
        km._set_session_flag(km.POSTAL_ALL_KEY, "postalServiceOff", True)
        self.assertTrue(km._postal_isolated(SID))
        km._set_session_flag(km.POSTAL_ALL_KEY, "postalServiceOff", False)
        self.assertNotIn(km.POSTAL_ALL_KEY, self._stored(), "off is the absent master")
        self.assertFalse(km._postal_isolated(SID))

    def test_an_opt_in_under_a_non_isolating_master_entry_stores_nothing(self):
        self._raw_flags({km.POSTAL_ALL_KEY: {"notify": True}})
        km._set_session_flag(SID, "postalServiceOff", False)
        self.assertNotIn(SID, self._stored())
        km._set_session_flag(km.POSTAL_ALL_KEY, "postalServiceOff", True)
        self.assertTrue(km._postal_isolated(SID))

    def test_the_flag_route_sets_and_clears_the_master_and_takes_an_opt_out(self):
        route = lambda sid, value: km._state_write_route("/flag", {"id": sid, "flag": "postalServiceOff", "value": value})
        self.assertEqual(route(km.POSTAL_ALL_KEY, True)[0], 200)
        self.assertTrue(km._postal_isolated(SID))
        route(SID, False)
        self.assertEqual(self._stored()[SID], {"postalServiceOff": False})
        self.assertFalse(km._postal_isolated(SID))
        route(SID, True); route(km.POSTAL_ALL_KEY, False)
        self.assertNotIn(km.POSTAL_ALL_KEY, self._stored())

    def test_the_socket_op_sets_and_clears_the_master_and_takes_an_opt_out(self):
        client = {"app": "timeline", "wid": "w1", "alive": True, "send": lambda raw: None}
        op = lambda sid, value: km.Handler._dispatch_ws(
            None, {"type": "setSessionFlag", "id": sid, "flag": "postalServiceOff", "value": value}, client)
        op(km.POSTAL_ALL_KEY, True)
        self.assertTrue(km._postal_isolated(SID))
        op(SID, False)
        self.assertEqual(self._stored()[SID], {"postalServiceOff": False})
        op(SID, True); op(km.POSTAL_ALL_KEY, False)
        self.assertNotIn(km.POSTAL_ALL_KEY, self._stored())


class KernelAndBusAgree(unittest.TestCase):
    """The kernel's and the bus's isolation readers, fed the same flags file, give the same answer."""
    OTHER = "99999999-8888-7777-6666-555555555555"
    SHAPES = [                                                            # (flags, reason for SID, for OTHER)
        ({}, "", ""),
        ({"*": {"postalServiceOff": True}}, "master", "master"),
        ({"*": {"postalServiceOff": False}}, "", ""),
        ({"*": {"notify": True}}, "", ""),
        ({"*": {"postalServiceOff": True}, SID: {"postalServiceOff": False}}, "", "master"),
        ({"*": {"postalServiceOff": True}, SID: {"postalServiceOff": None}}, "master", "master"),
        ({"*": {"postalServiceOff": True}, SID: {"postalServiceOff": True}}, "isolation", "master"),
        ({SID: {"postalServiceOff": None, "postalOff": True}}, "isolation", ""),
        ({SID: {"postalServiceOff": None}}, "", ""),
        ({SID: {"postalOff": True}}, "isolation", ""),
        ({SID: {"postalServiceOff": False, "postalOff": True}}, "", ""),
        ({SID: {"postalServiceOff": True, "postalOff": False}}, "isolation", ""),
    ]

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = km.jd.STATE, pm.SESSION_FLAGS
        km.jd.STATE = Path(self.td.name)
        pm.SESSION_FLAGS = km.jd.STATE / "session-flags.json"

    def tearDown(self):
        km.jd.STATE, pm.SESSION_FLAGS = self.saved
        self.td.cleanup()

    def test_every_shape_resolves_as_expected_on_both_sides(self):
        for shape, *expected in self.SHAPES:
            pm.SESSION_FLAGS.write_text(json.dumps(shape))
            pm._FLAGS_LAST[0] = None
            for sid, want in zip((SID, self.OTHER), expected):
                with self.subTest(shape=shape, sid=sid):
                    self.assertEqual((km._mail_off_why_k(sid), pm._mail_off_why(sid)), (want, want))
                    self.assertEqual((km._postal_isolated(sid), pm._postal_off(sid)), (bool(want), bool(want)))


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
