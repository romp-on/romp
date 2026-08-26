#!/usr/bin/env python3
"""A postal connector's PENDING flag keys on the message's own deciding events, never its age (the
user 2026-08-24, the time-windows ruling): pending = never read (no surviving exec row), never
recalled, never bounced, and the recipient can still read it (the TRUE live set — a dead recipient
can never read its mail; a cross-host relay's far end is not locally knowable, so it stays honestly
pending until the far receipt lands). MSG_INFLIGHT_MAX — the 30-minute age window that approximated
all four endings — is retired; an unread message to a live recipient is pending at any age the draw
horizon shows. The one ledger hole is closed at the writer: the orphan sweep now records its destroy
as a terminal bounced row on the original mid, so a same-sid revival can never flip destroyed mail
back to pending. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_tlpend", os.path.join(BIN, "romp-kernel")).load_module()
pm = SourceFileLoader("romp_postal_tlpend", os.path.join(BIN, "romp-postal-service")).load_module()

SENDER = "11111111-1111-1111-1111-111111111111"
RECIP = "22222222-2222-2222-2222-222222222222"
NOW = 1_787_700_000


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self._saved_state = km.jd.STATE
        km.jd.STATE = td
        (td / "timeline").mkdir(parents=True)

    def tearDown(self):
        km.jd.STATE = self._saved_state
        self.td.cleanup()

    def _log(self, rows):
        (km.jd.STATE / "timeline" / "messages.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")

    def _row(self, live=(RECIP,), to=RECIP):
        out = km._postal_messages(NOW, {SENDER, RECIP}, {SENDER: "web", RECIP: "api"},
                                  live_sids=set(live))
        rows = [r for r in out if r["toId"] == to or r["fromId"] == SENDER]
        return rows[0] if rows else None

    def _sent(self, t=NOW - 47 * 3600, to=RECIP):
        return {"t": t, "ev": "sent", "id": "m1", "from": "web", "from_id": SENDER,
                "to_id": to, "body": "please review the exporter"}


class PendingEvents(_Base):
    def test_unread_to_a_live_recipient_is_pending_at_any_age(self):
        # 47h old — the retired 30-minute window would have aged this out; age says nothing
        self._log([self._sent()])
        r = self._row()
        self.assertTrue(r["pending"], "no event ended it → it can still land, whatever its age")
        self.assertFalse(r["hasExec"])

    def test_exec_ends_it_and_unexec_revives_it(self):
        self._log([self._sent(), {"t": NOW - 100, "ev": "exec", "id": "m1"}])
        self.assertFalse(self._row()["pending"], "read is the designed ending event")
        self._log([self._sent(), {"t": NOW - 100, "ev": "exec", "id": "m1"},
                   {"t": NOW - 50, "ev": "unexec", "id": "m1"}])
        self.assertTrue(self._row()["pending"], "a rolled-back claim never reached the recipient")

    def test_recall_ends_it(self):
        self._log([self._sent(), {"t": NOW - 100, "ev": "recall", "id": "m1"}])
        self.assertFalse(self._row()["pending"], "the sender unsent it — it can never land")

    def test_bounce_ends_it(self):
        self._log([self._sent(), {"t": NOW - 100, "ev": "bounced", "id": "m1",
                                  "why": "recipient exited"}])
        self.assertFalse(self._row()["pending"], "the bus refused/destroyed it — terminal")

    def test_a_dead_recipient_cannot_read_it(self):
        self._log([self._sent()])
        r = self._row(live=(SENDER,))                  # recipient absent from the TRUE live set
        self.assertFalse(r["pending"], "nothing can read a dead session's mail")

    def test_a_cross_host_relay_stays_pending_until_the_far_receipt(self):
        self._log([self._sent(to="peer:boxa")])
        r = self._row(live=(SENDER,), to="peer:boxa")
        self.assertTrue(r["pending"], "the far end's liveness is not locally knowable — stay honest")

    def test_the_age_window_is_gone(self):
        import inspect
        src = inspect.getsource(km._postal_messages)
        self.assertNotIn("(now - st) <", src, "no clock resurrection")
        self.assertFalse(hasattr(km, "MSG_INFLIGHT_MAX"), "the constant itself is retired")


class OrphanSweepTerminalRow(_Base):
    def test_the_sweep_records_its_destroy_on_the_original_mid(self):
        # a dead recipient's unread mail, past the grace: the sweep destroys it — the destroy is the
        # message's terminal event and must land on the LEDGER, or a same-sid revival flips the
        # (destroyed) message back to pending forever
        self._saved_pm = (pm.STATE, pm.MAILROOT, pm.TLDIR, pm.local_agents)
        pm.STATE = km.jd.STATE
        pm.MAILROOT = km.jd.STATE / "mail"
        pm.TLDIR = km.jd.STATE / "timeline"
        pm.local_agents = lambda threads=False: [{"id": SENDER, "name": "web"}]
        try:
            box = pm.MAILROOT / RECIP / "new"
            box.mkdir(parents=True)
            mid = "1787700000.123_1.TESTHOST"
            f = box / mid
            f.write_text("From: web\n\nplease review the exporter")
            old = time.time() - pm.ORPHAN_GRACE - 60
            os.utime(f, (old, old))
            pm._sweep_orphans()
            rows = [json.loads(l) for l in
                    (km.jd.STATE / "timeline" / "messages.jsonl").read_text().splitlines()]
            term = [r for r in rows if r.get("ev") == "bounced" and r.get("id") == mid]
            self.assertEqual(len(term), 1, "the destroy landed as a terminal bounced row")
            self.assertFalse(f.exists(), "…and the mail is gone")
        finally:
            pm.STATE, pm.MAILROOT, pm.TLDIR, pm.local_agents = self._saved_pm


if __name__ == "__main__":
    unittest.main()
