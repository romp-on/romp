"""The bus's port record (postal_service.py PORTFILE, STATE/postal/postal-port; 2026-09-18): {"port", "pid"} written after the
bind and removed on a clean exit under the pid it names, so the kernel's loopback dials read the bus's own answer ahead of
the environment; and the decision refusal that names itself when a kernel dials a bus that does not serve the recipient.
Hermetic: a temp state root; the bus module loaded fresh; the kernel listing stubbed."""
import hashlib
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
ps = load_source("romp_postal_port_record", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-3333-4444-555555555555"      # web, the notes-api demo world
OTHER = "11111111-2222-3333-4444-666666666666"    # a session no bus here serves


class PortRecord(unittest.TestCase):
    def setUp(self):
        ps.STATE.mkdir(parents=True, exist_ok=True)
        try:
            ps.PORTFILE.unlink()
        except FileNotFoundError:
            pass

    def test_the_record_names_the_bound_port_and_this_pid_and_lives_beside_the_pid_file(self):
        self.assertEqual(ps.PORTFILE, ps.STATE / "postal-port"); self.assertEqual(ps.PORTFILE.parent, ps.PIDFILE.parent)
        ps._write_port_record(41234)
        rec = json.loads(ps.PORTFILE.read_text())
        self.assertEqual(rec, {"port": 41234, "pid": os.getpid(), "tok": ps._token_mark()}, "the port the bus bound, the pid that bound it, and its token mark")
        self.assertEqual(rec["tok"], hashlib.sha256(str(ps.SERVE_TOKEN or "").encode()).hexdigest()[:16], "the mark is a sha256 prefix of the serve token, never the token")
        self.assertEqual(len(rec["tok"]), 16)
        self.assertEqual([p.name for p in ps.STATE.glob("postal-port.*")], [], "written atomically: no temp left beside it")

    def test_a_clean_exit_removes_the_record_only_when_it_names_this_process(self):
        ps._write_port_record(41234)
        ps._remove_port_record()
        self.assertFalse(ps.PORTFILE.exists(), "this process's record goes on its clean exit")
        # a NEWER bus's record (another pid) is left standing: the exiting process must not erase its successor's answer
        ps.PORTFILE.write_text(json.dumps({"port": 41235, "pid": os.getpid() + 100000}))
        ps._remove_port_record()
        self.assertEqual(json.loads(ps.PORTFILE.read_text())["port"], 41235, "another process's record stands")
        ps.PORTFILE.write_text("not json")
        ps._remove_port_record()                       # a torn record is left alone, never a raise on the exit path
        self.assertTrue(ps.PORTFILE.exists())

    def test_the_write_fault_is_said_and_never_raises(self):
        saved = ps.PORTFILE
        try:
            blocker = ps.STATE / "a-file-not-a-dir"; blocker.write_text("x")
            ps.PORTFILE = blocker / "postal-port"    # the parent is a FILE: the put's mkdir raises, under root too
            lines = []
            saved_log = ps._log; ps._log = lambda m: lines.append(m)
            try:
                ps._write_port_record(1)               # the put cannot make its directory: the fault is said
            finally:
                ps._log = saved_log
            self.assertTrue(any("port record could not be written" in l and "ROMP_POSTAL_PORT" in l for l in lines), lines)
        finally:
            ps.PORTFILE = saved
            (ps.STATE / "a-file-not-a-dir").unlink()


class DecisionForASessionThisBusDoesNotServe(unittest.TestCase):
    def setUp(self):
        self._saved = ps.local_agents_checked
        ps.local_agents_checked = lambda threads=False: ([{"name": "web", "id": SID, "dir": ""}], True)
        ps.QUARANTINE.mkdir(parents=True, exist_ok=True)
        for f in ps.QUARANTINE.glob("*.json"):
            f.unlink()

    def tearDown(self):
        ps.local_agents_checked = self._saved

    def test_a_decision_naming_a_recipient_this_bus_does_not_serve_says_so_and_points_at_the_record(self):
        # the kernel that asked dialed a bus that is not its own (the pair read the port differently, or a stale legacy
        # forward): the fault names itself instead of "no held message"
        ok, err = ps.quarantine_decide("px-1.2_abc.TESTHOST", "approve", sid=OTHER)
        self.assertFalse(ok)
        self.assertIn("this bus holds nothing for session %s" % OTHER[:8], err)
        self.assertIn("not one of this machine's", err); self.assertIn("dialing its own bus", err); self.assertIn("postal-port", err)

    def test_a_served_recipient_with_no_file_keeps_the_plain_refusal_and_an_unanswered_listing_never_accuses(self):
        ok, err = ps.quarantine_decide("px-1.2_abc.TESTHOST", "approve", sid=SID)
        self.assertEqual((ok, err), (False, "no held message 'px-1.2_abc.TESTHOST'"), "this bus serves the session: the file is what is missing")
        ok, err = ps.quarantine_decide("px-1.2_abc.TESTHOST", "approve")
        self.assertEqual((ok, err), (False, "no held message 'px-1.2_abc.TESTHOST'"), "an older kernel names no sid: the plain refusal")
        ps.local_agents_checked = lambda threads=False: ([], False)   # the kernel did not answer: unanswered is not absence
        ok, err = ps.quarantine_decide("px-1.2_abc.TESTHOST", "approve", sid=OTHER)
        self.assertEqual((ok, err), (False, "no held message 'px-1.2_abc.TESTHOST'"))


if __name__ == "__main__":
    unittest.main()
