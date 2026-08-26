#!/usr/bin/env python3
"""A host the probe corroborates as REALLY away (ssh fails too) stops being reported (the user
2026-08-23): the remembered session list clears — _host_for_sid and the /remotes payload had kept
serving the last successful poll's sids all day while the Mac was off — and remotes-known stops
claiming attached (lastAttachedAt keeps the history; unreachableAt says when the claim ended). Both
heal on reconnect: the next poll repopulates sids, the next attach re-marks known. The trigger is
the probe's OWN verdict (sshOk false on a dial death), never a timer. SYNTHETIC fixtures."""
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
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

HOST = "TESTHOST"


class KnownUnreachable(unittest.TestCase):
    def setUp(self):
        with km._known_lock:
            km._known.clear()

    def tearDown(self):
        with km._known_lock:
            km._known.clear()
        if km.KNOWN_FILE.exists():
            km.KNOWN_FILE.unlink()

    def test_corroborated_away_stops_the_attached_claim_but_keeps_history(self):
        km._known_note(HOST, trust="directed", attached=True)
        with km._known_lock:
            last = km._known[HOST]["lastAttachedAt"]
        km._mark_known_unreachable(HOST)
        with km._known_lock:
            e = dict(km._known[HOST])
        self.assertFalse(e["attached"], "the claim about NOW ends with the corroborated probe")
        self.assertEqual(e["lastAttachedAt"], last, "…but the history survives")
        self.assertTrue(e.get("unreachableAt"), "…and says when the claim ended")
        rows = json.loads(km.KNOWN_FILE.read_text())
        row = next(r for r in rows if r.get("host") == HOST)
        self.assertFalse(row["attached"], "the durable file carries the flip")

    def test_never_attached_and_unknown_hosts_are_quiet_noops(self):
        km._mark_known_unreachable("never-seen")
        km._known_note(HOST, trust="directed")          # trust-only row: no attached flag
        km._mark_known_unreachable(HOST)
        with km._known_lock:
            self.assertNotIn("attached", km._known[HOST],
                             "a trust-only row never gains an attachment claim from the probe")
            self.assertNotIn("unreachableAt", km._known[HOST])

    def test_a_reattach_remarks_the_claim(self):
        km._known_note(HOST, attached=True)
        km._mark_known_unreachable(HOST)
        km._known_note(HOST, attached=True)             # the next real attach
        with km._known_lock:
            self.assertTrue(km._known[HOST]["attached"])

    def test_the_poll_clears_remembered_sids_on_corroborated_away(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('_remotes[r["host"]]["sids"] = []', src,
                      "a really-away host stops serving its last poll's sessions as live")
        self.assertIn("_mark_known_unreachable(r[\"host\"])", src)


if __name__ == "__main__":
    unittest.main()
