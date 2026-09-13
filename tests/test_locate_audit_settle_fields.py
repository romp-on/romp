#!/usr/bin/env python3
"""T386 stage 1 (round one, medium 4): the chat's landing row carries the settle verdict (dist, settled, superseded, clamp),
and the kernel's locateDiag handler must write those fields to locate-audit.jsonl, or on disk `ok` stays the whole verdict.
Drives the REAL Handler's WS dispatch (_dispatch_ws with a locateDiag message, the way the pane shim delivers one) against a
hermetic state directory. Synthetic fixtures only: placeholder ids, invented numbers."""
import json
import os
import pathlib
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_laudit", os.path.join(BIN, "romp-kernel"))

WID = "11111111-2222-3333-4444-555555555555"
SID = "aaaaaaaa-1111-2222-3333-444444444444"
ANCHOR = "22222222-3333-4444-5555-000000000040"


class LocateAuditSettleFields(unittest.TestCase):
    def setUp(self):
        self._saved_state = km.jd.STATE
        self._td = tempfile.TemporaryDirectory()
        km.jd._rebind_state(pathlib.Path(self._td.name))
        self.fp = km.jd.STATE / "locate-audit.jsonl"

    def tearDown(self):
        km.jd._rebind_state(self._saved_state)
        self._td.cleanup()

    def post(self, **extra):
        msg = {"type": "locateDiag", "id": SID, "ok": True, "trail": ["pointer-exact"], "anchor": ANCHOR, "anchorT": 1789000081, "kind": None}
        msg.update(extra)
        km.Handler._dispatch_ws(None, msg, {"wid": WID})

    def rows(self):
        return [json.loads(line) for line in self.fp.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_the_settle_verdict_reaches_the_audit_row(self):
        self.post(dist=2, settled=True)
        self.post(dist=-38, settled=False, superseded=True)
        self.post(dist=0, settled=True, clamp=93)
        self.post(dist=3, settled=True, gesture=True)   # the reader took the landing over with the target on its row (round three, low 3)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual((rows[3]["settled"], rows[3]["gesture"]), (True, True), "the takeover mark reaches the audit: %r" % rows[3])
        for r in rows[:3]:
            self.assertNotIn("gesture", r, "no mark on a landing the reader left alone: %r" % r)
        self.assertEqual((rows[0]["ok"], rows[0]["dist"], rows[0]["settled"]), (True, 2, True), rows[0])
        self.assertNotIn("superseded", rows[0], "the mark is written only when the page said so")
        self.assertEqual((rows[1]["dist"], rows[1]["settled"], rows[1]["superseded"]), (-38, False, True), rows[1])
        self.assertEqual((rows[2]["settled"], rows[2]["clamp"]), (True, 93), "a landing near the tail records the spot the clamp allowed: %r" % rows[2])
        for r in rows:
            for k in ("t", "wid", "ok", "sid", "anchor", "anchorT", "kind", "keep", "trail"):
                self.assertIn(k, r, "the row keeps its older fields: %r" % r)
            self.assertEqual(r["wid"], WID); self.assertEqual(r["sid"], SID); self.assertEqual(r["anchor"], ANCHOR)

    def test_a_row_from_an_older_bundle_or_a_miss_carries_no_settle_fields(self):
        self.post(ok=False, trail=["pointer-fetch-window"])
        self.post()                       # an older bundle: no settle fields at all
        rows = self.rows()
        for r in rows:
            for k in ("dist", "settled", "superseded", "clamp"):
                self.assertNotIn(k, r, "never invented: %r" % r)

    def test_mistyped_settle_fields_are_not_copied(self):
        # round two, low 2: the four fields are typed like the fields beside them; a bool is not a distance, a word is not a mark
        self.post(dist="9", settled="yes", superseded=1, clamp=True, gesture="wheel")
        self.post(dist=True, settled=None, clamp=2.5)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        for k in ("dist", "settled", "superseded", "clamp", "gesture"):
            self.assertNotIn(k, rows[0], "a mistyped field is dropped, never written: %r" % rows[0])
        self.assertNotIn("dist", rows[1], "a bool is not a distance: %r" % rows[1])
        self.assertNotIn("settled", rows[1])
        self.assertEqual(rows[1]["clamp"], 2.5, "a float distance is one: %r" % rows[1])


if __name__ == "__main__":
    unittest.main()
