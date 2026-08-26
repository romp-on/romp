#!/usr/bin/env python3
"""First-class PR-landing watches (the user 2026-08-24, both teams' surveys): a session registers
interest in a PR, the KERNEL polls gh for the terminal state — MERGED, CLOSED, or a FAILED check,
both ends of the standing watcher rule — and delivers ONE [romp] mail, surviving the kernel
restarts that killed every shell loop this replaces. Registrations persist and re-arm on boot (the
reconnect-intent idiom); a gh failure retires the watch LOUDLY after three consecutive errors.
Synthetic only — gh fully stubbed."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_prw", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


class Verdict(unittest.TestCase):
    """The pure gh-payload reading, executed."""

    def test_merged_and_closed_are_terminal(self):
        self.assertEqual(km._pr_watch_verdict({"state": "MERGED"}), ("merged", ""))
        self.assertEqual(km._pr_watch_verdict({"state": "CLOSED"}), ("closed", ""))

    def test_a_failed_check_is_terminal_with_its_name(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            {"name": "Python 3.12", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "Shell (bats)", "status": "COMPLETED", "conclusion": "FAILURE"}]}
        self.assertEqual(km._pr_watch_verdict(d), ("failed", "Shell (bats)"))

    def test_in_flight_says_busy_for_the_cadence_hint(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            {"name": "x", "status": "IN_PROGRESS", "conclusion": ""}]}
        self.assertEqual(km._pr_watch_verdict(d), (None, "busy"))
        self.assertEqual(km._pr_watch_verdict({"state": "OPEN", "statusCheckRollup": []}), (None, ""))


class Notice(unittest.TestCase):
    """The [romp] mechanics-notice voice, executed (the injected-voice rule: the notice is ABOUT
    romp — like the restart notice it names romp — and carries none of the board vocabulary)."""

    def test_each_terminal_reads_plainly_and_wears_the_machine_tag(self):
        for verdict, must in (("merged", "has MERGED"), ("closed", "CLOSED without merging"),
                              ("failed", "FAILED check"), ("error", "could not read")):
            n = km._pr_watch_notice(verdict, "TESTORG/testrepo", 7, "Shell (bats)")
            self.assertTrue(n.startswith("[romp] "), verdict)
            self.assertIn("TESTORG/testrepo#7", n)
            self.assertIn(must, n)
            self.assertIn("<!-- romp-tag: pr-watch -->", n, "machine-sent dress, never the user's words")
            for word in ("card", "board", "goal", "column", "nudge"):
                self.assertNotIn(word, n.lower(), "no board vocabulary in an injected body")


class Persistence(unittest.TestCase):
    """Registration is intent: survives a restart, re-arms fresh (the reconnect-intent idiom)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches))
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []

    def tearDown(self):
        km.PR_WATCH_FILE = self._saved[0]
        km._pr_watches[:] = self._saved[1]
        self.td.cleanup()

    def test_a_watch_survives_the_restart_and_rearms_fresh(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000)
        km._pr_watches[0]["_fails"] = 2                      # runtime state must NOT persist
        km._pr_watches[0]["_next"] = 99999
        km._pr_watches[:] = []
        km._pr_watches_load()                                # the boot path
        self.assertEqual(len(km._pr_watches), 1)
        r = km._pr_watches[0]
        self.assertEqual((r["pr"], r["repo"], r["sid"], r["at"]), (7, "TESTORG/testrepo", SID, 1000))
        self.assertEqual((r["_next"], r["_fails"]), (0, 0), "re-armed fresh: polls immediately")

    def test_registration_is_idempotent(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID)
        km.add_pr_watch(7, "TESTORG/testrepo", SID)
        self.assertEqual(len(km._pr_watches), 1)


class SupervisorReachesTheTick(unittest.TestCase):
    """The sweep must run on a box with NO remotes (the 2026-08-25 audit's #664 specimen): `now` was
    bound only inside the supervisor's per-remote loop, so with remotes.json = [] every pass died on
    UnboundLocalError inside the catch-all before reaching _pr_watch_tick — the landing mail never
    sent, and a merged PR's awaiting stamp sat stale for hours with no retire path short of the
    dead-man. The binding is per-pass now; this pins the ORDER (bound before the loop)."""

    def test_now_is_bound_per_pass_before_the_tick(self):
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("now = time.time()               # bound per PASS", src,
                      "the unconditional per-pass binding exists (loop-local bindings do not count)")
        self.assertLess(src.index("now = time.time()               # bound per PASS"),
                        src.index("_pr_watch_tick(now)"),
                        "…and it precedes the tick, so zero remotes can never unbind it")


class Tick(unittest.TestCase):
    """The sweep: terminal delivers + retires; gh failure retires LOUDLY after three; in-flight
    backs off while checks run."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._pr_watch_read, km._pr_watch_deliver)
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        self.mail = []
        km._pr_watch_deliver = lambda sid, text: self.mail.append((sid, text)) or True

    def tearDown(self):
        km.PR_WATCH_FILE, km._pr_watches[:], km._pr_watch_read, km._pr_watch_deliver = \
            self._saved[0], self._saved[1], self._saved[2], self._saved[3]
        self.td.cleanup()

    def test_merged_delivers_once_and_retires(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_tick(100.0)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("has MERGED", self.mail[0][1])
        self.assertEqual(self.mail[0][0], SID)
        self.assertEqual(km._pr_watches, [], "terminal → the watch retires")
        km._pr_watch_tick(200.0)
        self.assertEqual(len(self.mail), 1, "…and never mails twice")

    def test_a_failed_check_is_just_as_terminal(self):
        km.add_pr_watch(8, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("failed", "Shell (bats)")
        km._pr_watch_tick(100.0)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("FAILED check (Shell (bats))", self.mail[0][1])
        self.assertEqual(km._pr_watches, [])

    def test_gh_failure_retires_loudly_after_three_never_silently(self):
        km.add_pr_watch(9, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("error", "auth required")
        km._pr_watch_tick(100.0)
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(self.mail, [], "two failures → still trying, still quiet")
        km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
        self.assertEqual(len(self.mail), 1, "the third delivers the loud retire")
        self.assertIn("could not read", self.mail[0][1])
        self.assertIn("auth required", self.mail[0][1])
        self.assertEqual(km._pr_watches, [])

    def test_in_flight_backs_off_while_checks_run(self):
        km.add_pr_watch(10, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: (None, "busy")
        km._pr_watch_tick(100.0)
        self.assertEqual(km._pr_watches[0]["_next"], 100.0 + km.PR_WATCH_BUSY_EVERY)
        km._pr_watch_read = lambda pr, repo: (None, "")
        km._pr_watch_tick(100.0 + km.PR_WATCH_BUSY_EVERY)
        self.assertEqual(km._pr_watches[0]["_next"],
                         100.0 + km.PR_WATCH_BUSY_EVERY + km.PR_WATCH_EVERY)
        self.assertEqual(self.mail, [])


class Route(unittest.TestCase):
    """POST /watch-pr over the real Handler: registration + refusals; token-gated."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._sid_of)
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        km._sid_of = lambda who: SID if who in (SID, "web") else ""

    def tearDown(self):
        km.PR_WATCH_FILE, km._pr_watches[:], km._sid_of = self._saved
        self.td.cleanup()

    def _post(self, body, token=True):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/watch-pr" % self.port, data=json.dumps(body).encode(),
            headers=dict({"Content-Type": "application/json"},
                         **({"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]} if token else {})))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode()

    def test_registers_by_name_and_persists(self):
        st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"})
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(r["watch"]["sid"], SID)
        self.assertEqual(json.loads(km.PR_WATCH_FILE.read_text())[0]["pr"], 7)

    def test_refusals_are_loud_and_shaped(self):
        self.assertEqual(self._post({"repo": "TESTORG/testrepo", "name": "web"})[0], 400)
        self.assertEqual(self._post({"pr": 7, "repo": "not-a-repo", "name": "web"})[0], 400)
        st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "ghost"})
        self.assertFalse(r["ok"]); self.assertIn('no session answers to "ghost"', r["error"])
        self.assertEqual(self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"},
                                    token=False)[0], 403)


if __name__ == "__main__":
    unittest.main()
