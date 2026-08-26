#!/usr/bin/env python3
"""A MACHINE cut leaves no "no response — turn settled" line (the user 2026-07-22).

When a turn is cut mid-flight the model closes it with a null settle-reply, which the chat draws as a
compact seam line. That is useful feedback when YOU pressed stop. It is noise when romp itself caused the
cut — a kernel restart or a mid-turn process death — because it narrates romp's own plumbing, and the
interrupt marker directly above already reads "interrupted — kernel restart".

Keyed on the SAME signatures the nudge gate uses (INTR_RESTART_SIG / INTR_CRASH_SIG in the resume notice
romp injects as the next user-role event) — an event, never a time window. No notice → a genuine user
stop → the line stays.

Synthetic only — invented prompt text, placeholder uuids.
"""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = SourceFileLoader("romp_kernel_seam", os.path.join(BIN, "romp-kernel")).load_module()

U1 = "11111111-2222-3333-4444-555555555555"
U2 = "11111111-2222-3333-4444-666666666666"
U3 = "11111111-2222-3333-4444-777777777777"


def seam(notice_md):
    """marker → null settle-reply → the next user-role event (a romp resume notice, or a typed prompt)."""
    evs = [
        {"kind": "user", "md": "draft the migration plan", "uuid": U1, "human": True},
        {"kind": "user", "md": "[Request interrupted by user]", "uuid": U2, "interruptMarker": True},
        {"kind": "assistant", "md": "No response requested.", "uuid": U3, "interruptSettle": True},
    ]
    if notice_md is not None:
        evs.append({"kind": "user", "md": notice_md, "uuid": U1, "rompSystem": True})
    else:
        evs.append({"kind": "user", "md": "actually, do the other one first", "uuid": U1, "human": True})
    return evs


class InterruptSettleOnMachineCut(unittest.TestCase):
    def _settles(self, evs):
        return [e for e in evs if e.get("interruptSettle")]

    def test_a_kernel_restart_cut_drops_the_settle_line(self):
        evs = seam("[romp] The romp kernel " + km.INTR_RESTART_SIG + " this session's in-flight turn.")
        km._stamp_interrupt_causes(evs)
        self.assertEqual(self._settles(evs), [], "a restart-cut turn shows no 'turn settled' line")
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertEqual(marker.get("interruptCause"), "restart", "...the marker still names the cause")

    def test_a_crash_cut_drops_it_too(self):
        evs = seam("[romp] the session's claude process " + km.INTR_CRASH_SIG + " and was resumed.")
        km._stamp_interrupt_causes(evs)
        self.assertEqual(self._settles(evs), [])
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertEqual(marker.get("interruptCause"), "crash")

    def test_a_GENUINE_user_stop_keeps_the_settle_line(self):
        # no romp resume notice → the user pressed stop → the line is real feedback, so it stays
        evs = seam(None)
        km._stamp_interrupt_causes(evs)
        self.assertEqual(len(self._settles(evs)), 1, "a user-initiated interrupt keeps its seam line")
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertIsNone(marker.get("interruptCause"), "a user stop is unlabeled")

    def test_nothing_else_is_dropped_and_no_scratch_key_leaks(self):
        evs = seam("[romp] The romp kernel " + km.INTR_RESTART_SIG + " this session's in-flight turn.")
        n_before = len(evs)
        km._stamp_interrupt_causes(evs)
        self.assertEqual(len(evs), n_before - 1, "exactly the settle event is removed")
        self.assertTrue(any(e.get("md") == "draft the migration plan" for e in evs), "real turns survive")
        self.assertFalse(any("_dropSettle" in e for e in evs), "the scratch flag never reaches the client")

    def test_a_machine_cut_aliases_the_dropped_settle_uuid_onto_the_marker(self):
        # The dropped settle atom is the cut turn's LAST assistant atom — exactly where verdicts and
        # cards get anchored — so its uuid must still land somewhere real: the marker carries it
        # (settleUuids) and the chat's seam answers to it (the user 2026-08-25, a card click that
        # honest-failed "couldn't locate" on a dozens-of-restarts session).
        evs = seam("[romp] The romp kernel " + km.INTR_RESTART_SIG + " this session's in-flight turn.")
        km._stamp_interrupt_causes(evs)
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertEqual(marker.get("settleUuids"), [U3], "the seam answers to the dropped settle's uuid")

    def test_a_user_stop_keeps_the_settle_and_mints_no_alias(self):
        evs = seam(None)
        km._stamp_interrupt_causes(evs)
        self.assertEqual(len(self._settles(evs)), 1, "a genuine user stop keeps its settle line")
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertNotIn("settleUuids", marker, "no alias when nothing was dropped")

    def test_the_client_still_renders_the_line_for_user_stops(self):
        # the renderer keeps its seam branch — this change is server-side only
        r = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "render.ts")).read()
        self.assertIn("no response — turn settled", r)


if __name__ == "__main__":
    unittest.main()
