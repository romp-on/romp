#!/usr/bin/env python3
"""First-class PR-landing watches (the user 2026-08-24, both teams' surveys): a session registers
interest in a PR, the KERNEL polls gh for the terminal state — MERGED, CLOSED, or a FAILED check,
both ends of the standing watcher rule — and delivers ONE [romp] mail, surviving the kernel
restarts that killed every shell loop this replaces. Registrations persist and re-arm on boot (the
reconnect-intent idiom); a gh failure retires the watch LOUDLY after three consecutive errors.
Durability at both ends (DurableRegistration, DeliveryOrder): a watch is acknowledged only once it
is on disk, and its landing mail retires it only once the injection is accepted, stamped first.
Synthetic only — gh fully stubbed."""
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_prw", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


_ENOSPC_CALLS = [0]
ENOSPC = "[Errno 28] No space left on device"


def _enospc(path, text, mode=None):
    """The disk refusing the state file — _atomic_write's shape, raising what a full disk raises:
    errno + strerror + the TEMP path, which carries a per-call sequence, so str(e) differs on every
    call and only the errno projection can dedupe an episode."""
    _ENOSPC_CALLS[0] += 1
    raise OSError(28, "No space left on device", "%s.tmp.%d" % (path, _ENOSPC_CALLS[0]))


_MODS = {}


def _backend_mod(which):
    """The real sdk_backend / codex_backend modules, loaded once for the tests that execute their readers."""
    if which not in _MODS:
        _MODS[which] = load_source("romp_%s_prw" % which,
                                        os.path.join(os.path.dirname(BIN), "kernel", which + ".py"))
    return _MODS[which]


class _FakeBackend:
    """SDK-shaped, answering from a record the TEST writes: `regs` maps sid → alive flag (a reg
    present); an absent sid is no reg; `names` maps a session name → sid (the regs' name field).
    send() REFUSES (returns False, never raises) unless the reg is present and alive — a dormant
    alive reg and a comment thread are both taken, as SdkBackend._ensure does; `refuse` forces a
    refusal for a live one; `marker_raises` makes the record reader itself fail. Nothing here
    consults a live set: that is the point. What this fake cannot stage is the real routing —
    ownership by owns(), the fall-through to tmux — which RealRouting covers with real backends."""

    def __init__(self, regs=None, refuse=False, marker_raises=False, names=None):
        self.regs, self.refuse, self.marker_raises, self.sent = dict(regs or {}), refuse, marker_raises, []
        self.names = dict(names or {})

    def owns(self, sid):
        return sid in self.regs

    def forwards_sends(self):
        return True

    def busy(self, sid):
        return False

    def send(self, sid, text):
        if not self.regs.get(sid) or self.refuse:
            return False
        self.sent.append((sid, text))
        return True

    def end_marker(self, sid):
        if self.marker_raises:
            raise RuntimeError("the reg reader broke")
        if sid not in self.regs:
            return None
        return not self.regs[sid]

    def sid_for_name(self, name):
        return self.names.get(name, "")


class _NoRecordBackend(_FakeBackend):
    """A backend with no record reader at all (tmux-like), whose send still refuses."""
    end_marker = None
    sid_for_name = None


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
            # T130 (the user 2026-08-27): the chat classifies by MARKER — without romp-injected this
            # notice wore the generic tagged dress instead of the romp attribution the nudges wear
            self.assertIn("<!-- romp-injected --><!-- romp-system -->", n,
                          "the mechanics-notice family's markers, so the one machine-injected rendering applies")
            for word in ("card", "board", "goal", "column", "nudge"):
                self.assertNotIn(word, n.lower(), "no board vocabulary in an injected body")

    def test_a_replayed_notice_says_it_may_repeat(self):
        # a delivery stamp that outlived a restart: the mail may already have landed, and the
        # notice says so instead of posing as the first copy (DeliveryOrder pins when this rides)
        n = km._pr_watch_notice("merged", "TESTORG/testrepo", 7, replay=True)
        self.assertIn("This may repeat a notice sent just before romp last restarted.", n)
        self.assertTrue(n.endswith("<!-- romp-injected --><!-- romp-system --><!-- romp-tag: pr-watch -->"),
                        "the marker tail stays last")
        for word in ("card", "board", "goal", "column", "nudge"):
            self.assertNotIn(word, n.lower())
        self.assertNotIn("may repeat", km._pr_watch_notice("merged", "TESTORG/testrepo", 7),
                         "…and only when replayed")

    def test_a_hand_off_to_the_escalation_contact_names_the_ended_session(self):
        # the registrant ENDED and the notice goes to its escalation contact, who never asked: the
        # body names the session it was watching for and why it arrives here — never "you asked"
        n = km._pr_watch_notice("merged", "TESTORG/testrepo", 7, ended_owner=SID)
        self.assertIn("romp was watching for session 11111111 has MERGED: TESTORG/testrepo#7", n)
        self.assertNotIn("you asked", n)
        self.assertIn("That session has ended, so this comes to you as the escalation contact named for it.", n)
        self.assertTrue(n.endswith("<!-- romp-injected --><!-- romp-system --><!-- romp-tag: pr-watch -->"))
        for word in ("card", "board", "goal", "column", "nudge"):
            self.assertNotIn(word, n.lower())


class DurableRegistration(unittest.TestCase):
    """A watch is acknowledged only once it is ON DISK. Before: the save ran outside the lock,
    swallowed its exception and returned nothing; add_pr_watch handed back the row regardless, the
    route acked it, and the next kernel restart forgot the watch — its landing mail never came."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._atomic_write)
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        getattr(km, "_pr_watch_save_faults", {}).clear()
        km._SYNC_NOTICES.clear()

    def tearDown(self):
        km.PR_WATCH_FILE, km._pr_watches[:], km._atomic_write = self._saved
        getattr(km, "_pr_watch_save_faults", {}).clear()
        km._SYNC_NOTICES.clear()
        self.td.cleanup()

    def test_a_failed_save_refuses_the_watch_and_keeps_no_row(self):
        km._atomic_write = _enospc
        with redirect_stderr(io.StringIO()):
            row, fault = km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000)
        self.assertIsNone(row, "no row is acknowledged that a restart would forget")
        self.assertEqual(fault, ENOSPC, "the fault is named — errno text, never the per-call temp path")
        self.assertEqual(km._pr_watches, [], "the append is rolled back — memory and disk agree")
        self.assertFalse(km.PR_WATCH_FILE.exists())
        self.assertFalse(km._kernel_watch_armed(SID), "nothing is armed for the session")

    def test_a_save_fault_is_said_once_per_episode_and_a_landed_write_re_arms(self):
        km._atomic_write = _enospc
        err = io.StringIO()
        with redirect_stderr(err):
            km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000)
            km.add_pr_watch(8, "TESTORG/testrepo", SID, now=1001)       # the same fault, again
        self.assertEqual(err.getvalue().count("pr-watches: could not save"), 1, "one line per fault episode")
        self.assertIn("[Errno 28] No space left on device", err.getvalue())
        rows = km._sync_notice_rows()
        self.assertEqual([(r["ok"], "could not save pr-watches.json" in r["text"]) for r in rows],
                         [(False, True)], "…and one Log row, marked as a failure")
        self.assertEqual([r["kind"] for r in rows], ["refused"],
                         "a state file that could not be written is the bell's refused kind, never a machine sync")
        km._atomic_write = self._saved[2]                                # the disk takes writes again
        self.assertEqual(km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000)[1], "")
        self.assertEqual(km._pr_watch_save_faults, {}, "a landed write ends the episode")
        km._atomic_write = _enospc
        with redirect_stderr(io.StringIO()):
            self.assertIsNone(km.add_pr_watch(9, "TESTORG/testrepo", SID, now=1002)[0])
        self.assertEqual(len(km._sync_notice_rows()), 2, "a new episode is a new line")
        self.assertEqual([r["pr"] for r in km._pr_watches], [7], "the refused row is gone; the saved one stands")

    def test_an_escalation_target_added_to_an_existing_watch_is_persisted_or_refused(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000)
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=1000, escalate="mgr")
        self.assertEqual(json.loads(km.PR_WATCH_FILE.read_text())[0].get("escalate"), "mgr",
                         "the edit reaches the file, not just memory")
        km.add_pr_watch(8, "TESTORG/testrepo", SID, now=1000)
        km._atomic_write = _enospc
        with redirect_stderr(io.StringIO()):
            row, fault = km.add_pr_watch(8, "TESTORG/testrepo", SID, now=1000, escalate="mgr")
        self.assertEqual((row["pr"], fault), (8, ENOSPC), "the standing watch is returned WITH the fault: two truths")
        self.assertNotIn("escalate", km._pr_watches[1], "the target that did not land is rolled back")
        self.assertEqual(len(km._pr_watches), 2, "…and the watch itself, already durable, stands")

    def test_every_save_runs_under_the_watch_lock(self):
        # the claim "one locked transaction" and "the write sits under the lock", executed: a fresh
        # row, an escalation target, the tick's stamp and its retire each publish with the lock HELD
        # (a save outside it let a concurrent registration observe and persist a row later rolled back)
        held, real = [], km._atomic_write

        def spy(path, text, mode=None):
            held.append(km._pr_watch_lock.locked())
            return real(path, text, mode)
        km._atomic_write = spy
        saved = (km._pr_watch_read, km._pr_watch_deliver, getattr(km, "_pr_watch_end_marker", None))
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_deliver = lambda sid, text: True
        km._pr_watch_end_marker = lambda sid: False                              # the registrant stands
        try:
            km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)                    # the fresh row
            km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")    # the escalation target
            km._pr_watch_tick(100.0)                                             # the stamp, then the retire
        finally:
            km._pr_watch_read, km._pr_watch_deliver, km._pr_watch_end_marker = saved
        self.assertEqual(held, [True, True, True, True], "four saves, every one under _pr_watch_lock")
        self.assertEqual(km._pr_watches, [])


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

    def test_a_persisted_stamp_that_is_not_a_verdict_is_dropped_on_load(self):
        # the strict-reader rule applied to one field: only the three verdicts the tick files are a
        # stamp; a torn or hand-edited value must not replay as a notice the tick never wrote
        km.PR_WATCH_FILE.write_text(json.dumps([
            {"pr": 7, "repo": "TESTORG/testrepo", "sid": SID, "at": 1, "sent": "bogus", "sentDetail": "x"},
            {"pr": 8, "repo": "TESTORG/testrepo", "sid": SID, "at": 2, "sent": "merged", "sentDetail": ""}]))
        km._pr_watches_load()
        bogus, good = km._pr_watches
        self.assertNotIn("sent", bogus)
        self.assertNotIn("sentDetail", bogus)
        self.assertFalse(bogus["_replay"], "nothing to replay: the row polls gh afresh")
        self.assertEqual((good["sent"], good["_replay"]), ("merged", True))

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
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._pr_watch_read, km._pr_watch_deliver,
                       getattr(km, "_pr_watch_refusal", None), getattr(km, "_pr_watch_end_marker", None))
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        self.mail = []
        km._pr_watch_deliver = lambda sid, text: self.mail.append((sid, text)) or True
        km._pr_watch_end_marker = lambda sid: False                                    # the registrant stands
        km._pr_watch_refusal = lambda r, sid: ("wait", "a live registrant refusing")   # DeliveryTargets covers the rest

    def tearDown(self):
        (km.PR_WATCH_FILE, km._pr_watches[:], km._pr_watch_read, km._pr_watch_deliver,
         km._pr_watch_refusal, km._pr_watch_end_marker) = self._saved
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

    def test_a_failed_check_holds_mails_once_and_escalates_on_the_bound(self):
        # T143 (the ~6h invisible-ask specimen): a failed check MAILS THE OWNER ONCE and HOLDS —
        # never a silent retire the owner's death can orphan. Resolution is event-keyed; only the
        # no-event gap (nobody acted) takes the bounded escalation to the NAMED target.
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._sid_of_saved = getattr(km, "_sid_of")
        km._sid_of = lambda who: "22222222-2222-3333-4444-555555555555" if who == "mgr" else ""
        try:
            km._pr_watch_read = lambda pr, repo: ("failed", "Shell (bats)")
            km._pr_watch_tick(100.0)
            self.assertEqual(len(self.mail), 1, "the owner is mailed once")
            self.assertIn("FAILED check", self.mail[0][1])
            self.assertEqual(len(km._pr_watches), 1, "…and the watch HOLDS instead of retiring")
            km._pr_watch_tick(200.0)
            self.assertEqual(len(self.mail), 1, "no re-mail while held")
            # the bound passes with the failure still standing → ONE escalation to the named target
            km._pr_watch_tick(100.0 + km.PR_WATCH_ESCALATE_S + 61)
            self.assertEqual(len(self.mail), 2)
            self.assertEqual(self.mail[1][0], "22222222-2222-3333-4444-555555555555")
            self.assertIn("sat with a FAILED check", self.mail[1][1])
            km._pr_watch_tick(100.0 + km.PR_WATCH_ESCALATE_S + 200)
            self.assertEqual(len(self.mail), 2, "the escalation is ONE mail, ever")
            # the terminal event still ends the watch normally
            km._pr_watch_read = lambda pr, repo: ("merged", "")
            km._pr_watch_tick(100.0 + km.PR_WATCH_ESCALATE_S + 300)
            self.assertEqual(len(self.mail), 3)
            self.assertIn("has MERGED", self.mail[2][1])
            self.assertEqual(km._pr_watches, [])
        finally:
            km._sid_of = km._sid_of_saved

    def test_resolution_events_clear_the_held_failure_without_a_clock(self):
        km.add_pr_watch(9, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._pr_watch_read = lambda pr, repo: ("failed", "x")
        km._pr_watch_tick(100.0)
        self.assertTrue(km._pr_watches[0].get("failedAt"))
        km._pr_watch_read = lambda pr, repo: (None, "busy")   # checks re-running — the EVENT
        km._pr_watch_tick(200.0)
        self.assertFalse(km._pr_watches[0].get("failedAt"),
                         "the held state clears on the resolution event, no bound involved")
        self.assertEqual(len(self.mail), 1, "clearing is silent — the watch just continues")

    def test_no_target_means_no_ping_ever(self):
        km.add_pr_watch(11, "TESTORG/testrepo", SID, now=0)   # no escalate; no box default in this temp state
        km._pr_watch_read = lambda pr, repo: ("failed", "x")
        km._pr_watch_tick(100.0)
        km._pr_watch_tick(100.0 + km.PR_WATCH_ESCALATE_S + 61)
        self.assertEqual(len(self.mail), 1, "the kernel never guesses a manager — unset = no ping")

    def test_failed_state_survives_the_save_load_cycle(self):
        # the specimen window held ~21 restarts — a clock that reset each boot would never fire
        km.add_pr_watch(13, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._pr_watch_read = lambda pr, repo: ("failed", "x")
        km._pr_watch_tick(100.0)
        km._pr_watches_save()
        km._pr_watches[:] = []
        km._pr_watches_load()
        r = km._pr_watches[0]
        self.assertEqual((r.get("failedAt"), r.get("escalate")), (100, "mgr"),
                         "failedAt and the target persist across the restart")

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


class DeliveryOrder(unittest.TestCase):
    """STAMP → deliver → retire. Before: deliver's bool was ignored and the row retired regardless —
    a refused injection lost the one event a delegating session was waiting on — and a crash between
    the two mailed it again at boot with nothing to say so."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.PR_WATCH_FILE, list(km._pr_watches), km._pr_watch_read, km._pr_watch_deliver,
                       km._atomic_write, getattr(km, "_pr_watch_refusal", None),
                       getattr(km, "_pr_watch_end_marker", None))
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        getattr(km, "_pr_watch_save_faults", {}).clear()
        km._SYNC_NOTICES.clear()
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._pr_watch_end_marker = lambda sid: False                                    # the registrant stands
        km._pr_watch_refusal = lambda r, sid: ("wait", "a live registrant refusing")   # DeliveryTargets covers an ended one
        self.mail = []

    def tearDown(self):
        (km.PR_WATCH_FILE, km._pr_watches[:], km._pr_watch_read, km._pr_watch_deliver,
         km._atomic_write, km._pr_watch_refusal, km._pr_watch_end_marker) = self._saved
        getattr(km, "_pr_watch_save_faults", {}).clear()
        km._SYNC_NOTICES.clear()
        self.td.cleanup()

    def _on_disk(self):
        return json.loads(km.PR_WATCH_FILE.read_text())

    def _accept(self, sid, text):
        self.mail.append((sid, text))
        return True

    def test_the_stamp_is_on_disk_when_the_injection_runs(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        seen = []

        def deliver(sid, text):
            seen.append([(r.get("sent"), r.get("sentDetail")) for r in self._on_disk()])
            return True
        km._pr_watch_deliver = deliver
        km._pr_watch_tick(100.0)
        self.assertEqual(seen, [[("merged", "")]], "the verdict was durable BEFORE the mail went out")
        self.assertEqual((km._pr_watches, self._on_disk()), ([], []), "…and retired once it was accepted")

    def test_a_refused_injection_keeps_the_row_and_retries_next_tick(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        tries = []
        km._pr_watch_deliver = lambda sid, text: tries.append(text) or False
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual(len(tries), 1)
        self.assertEqual(len(km._pr_watches), 1, "not retired: nothing was delivered")
        self.assertEqual(self._on_disk()[0]["sent"], "merged", "the stamp stays — the verdict is filed")
        self.assertTrue(km._kernel_watch_armed(SID), "the session's wait is still carried")
        km._pr_watch_read = lambda pr, repo: self.fail("a filed verdict is never re-derived from gh")
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY - 1)
        self.assertEqual(len(tries), 1, "rate-gated like any row")
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(len(tries), 2, "retried on the next tick")
        self.assertNotIn("may repeat", tries[1], "a KNOWN failure: nothing landed, so no repeat warning")
        rows = km._sync_notice_rows()
        self.assertEqual([r["ok"] for r in rows], [False], "the refusal is on the Log once, not once per tick")
        self.assertIn("was not accepted by session", rows[0]["text"])
        km._pr_watch_deliver = self._accept
        km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("has MERGED", self.mail[0][1])
        self.assertEqual((km._pr_watches, self._on_disk()), ([], []), "retired on the confirmed delivery")

    def test_a_stamp_that_cannot_land_never_delivers(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_deliver = self._accept
        km._atomic_write = _enospc
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual(self.mail, [], "no mail without a durable stamp: at boot it would read as a fresh verdict")
        self.assertEqual(len(km._pr_watches), 1)
        self.assertNotIn("sent", km._pr_watches[0], "a stamp that did not land is not held in memory either")
        self.assertIsNone(self._on_disk()[0].get("sent"))
        km._atomic_write = self._saved[4]                                 # the disk is back
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(len(self.mail), 1, "stamped, delivered, retired")
        self.assertEqual(km._pr_watches, [])

    def test_a_crash_between_the_stamp_and_the_retire_is_told_never_silent(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)

        def died(sid, text):                       # the kernel dies with the mail in flight
            raise RuntimeError("kernel killed mid-delivery")
        km._pr_watch_deliver = died
        with self.assertRaises(RuntimeError):
            km._pr_watch_tick(100.0)
        self.assertEqual(self._on_disk()[0]["sent"], "merged", "the crash left the stamp on disk")
        km._pr_watches[:] = []                     # the restart
        km._pr_watches_load()
        km._pr_watch_read = lambda pr, repo: self.fail("a stamped verdict is never re-derived from gh")
        km._pr_watch_deliver = self._accept
        km._pr_watch_tick(200.0)
        self.assertEqual(len(self.mail), 1, "delivered once more — whether the first copy landed is unknowable")
        self.assertIn("has MERGED", self.mail[0][1])
        self.assertIn("This may repeat a notice sent just before romp last restarted.", self.mail[0][1],
                      "…and the notice SAYS it may be a repeat: never silently twice")
        self.assertEqual((km._pr_watches, self._on_disk()), ([], []))
        km._pr_watch_tick(300.0)
        self.assertEqual(len(self.mail), 1, "and never a third")

    def test_the_awaiting_box_says_the_notice_is_owed_once_the_pr_landed(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        aw = km._watch_awaiting(SID)
        self.assertEqual((aw["items"][0]["label"], aw["why"]),
                         ("PR #7 (TESTORG/testrepo)", "waiting on PR #7 (TESTORG/testrepo) to land"))
        km._pr_watch_deliver = lambda sid, text: False        # the PR landed; the notice is what is owed
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        aw = km._watch_awaiting(SID)
        self.assertEqual(aw["items"][0]["label"], "PR #7 (TESTORG/testrepo): merged — mail pending",
                         "the box says what is owed, not 'to land' for a PR that landed")
        self.assertEqual(aw["why"], "waiting on PR #7 (TESTORG/testrepo): merged — mail pending")
        self.assertEqual(aw["items"][0]["id"], "pr:TESTORG/testrepo#7", "same row identity")

    def test_the_gh_failure_retire_keeps_the_same_order(self):
        km.add_pr_watch(9, "TESTORG/testrepo", SID, now=0)
        km._pr_watch_read = lambda pr, repo: ("error", "auth required")
        km._pr_watch_deliver = lambda sid, text: False
        with redirect_stderr(io.StringIO()):
            for i in range(3):
                km._pr_watch_tick(100.0 + i * km.PR_WATCH_EVERY)
        self.assertEqual(len(km._pr_watches), 1, "the loud retire is owed, not lost")
        self.assertEqual((self._on_disk()[0]["sent"], self._on_disk()[0]["sentDetail"]), ("error", "auth required"))
        km._pr_watch_deliver = self._accept
        km._pr_watch_tick(100.0 + 3 * km.PR_WATCH_EVERY)
        self.assertEqual(len(self.mail), 1)
        self.assertIn("auth required", self.mail[0][1])
        self.assertEqual(km._pr_watches, [])


MGR = "22222222-2222-3333-4444-555555555555"
THREAD = "33333333-2222-3333-4444-555555555555"


def _live_raises():
    raise RuntimeError("tmux list-sessions timed out")


class DeliveryTargets(unittest.TestCase):
    """The decision procedure against an SDK-shaped fake whose record the test writes. Before the
    first fix, _pr_watch_deliver returned True whenever the send did not raise, and no backend raises
    for a dead session (SDK/Codex send return False, tmux's send reports a missing session only on
    stderr; _send_or_park dropped be.send's return), so an ended registrant's landing mail was
    dropped silently. The first fix pre-classified from Sessions.live() — which reads EMPTY on a slow
    `tmux list-sessions` or a swallowed live_sessions error, and never lists a comment thread — so a
    blip diverted a running registrant's mail with a false "has ended". Now the durable records
    decide (an explicit end marker before any send; a uuid nobody holds is never sent; a record that
    says alive sends first), refusals are classified from the same records, a uuid with no record has
    ended on the first read (an absent reg is durable: the backend never unlinks one, and a reg it
    cannot read RAISES, which waits uncounted), and an unresolved contact name waits, bounded. Runs
    the REAL _pr_watch_deliver →
    _send_or_park → backend path, with the record readers and Sessions.backend_for routed to the fake
    and Sessions.live() EMPTY throughout (or raising). What the fake cannot stage — ownership by
    owns() falling through to tmux — RealRouting covers with real backends."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = {n: getattr(km, n) for n in
                       ("PR_WATCH_FILE", "_pr_watch_read", "_compacting_now", "_working_now", "_limit_hold",
                        "_optimistic_echo", "_sid_of", "_name_of", "_watch_escalate_default",
                        "_pr_watch_record_backends")}
        self._saved_rows = list(km._pr_watches)
        self._saved_live, self._saved_be = km.Sessions.live, km.Sessions.backend_for
        km.PR_WATCH_FILE = Path(self.td.name) / "pr-watches.json"
        km._pr_watches[:] = []
        km._pr_watch_save_faults.clear()
        km._SYNC_NOTICES.clear()
        km._pending_ops.clear()
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._compacting_now = lambda sid, *a, **k: False
        km._working_now = lambda sid, *a, **k: False
        km._limit_hold = lambda sid, *a, **k: False
        km._optimistic_echo = lambda *a, **k: None
        km._sid_of = lambda who: MGR if who == "mgr" else who
        km._name_of = lambda sid: "mgr" if sid == MGR else None
        km._watch_escalate_default = lambda: ""
        self.be = _FakeBackend()
        km._pr_watch_record_backends = lambda: [self.be]
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km.Sessions.live = staticmethod(lambda: {})        # a blip's answer, held for the whole class

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km._pr_watches[:] = self._saved_rows
        km.Sessions.live, km.Sessions.backend_for = staticmethod(self._saved_live), staticmethod(self._saved_be)
        km._pr_watch_save_faults.clear()
        km._SYNC_NOTICES.clear()
        km._pending_ops.clear()
        self.td.cleanup()

    def _on_disk(self):
        return json.loads(km.PR_WATCH_FILE.read_text())

    def _log(self):
        return [(r["ok"], r["text"]) for r in km._sync_notice_rows()]

    def _kinds(self):
        return [r["kind"] for r in km._sync_notice_rows()]

    # a live registrant refused by return: wait, said once per reason, closed on delivery
    def test_a_live_registrant_refused_by_return_keeps_the_row_and_retries(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.be.regs, self.be.refuse = {SID: True}, True      # the record says alive; the send is declined
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
            km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(self.be.sent, [])
        self.assertEqual(len(km._pr_watches), 1, "not accepted → not retired; never diverted")
        self.assertEqual(self._on_disk()[0]["sent"], "merged", "stamped, awaiting the retry")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False], "the same reason twice is said once")
        self.assertIn("was not accepted by session 11111111 (its record says it is alive, yet the send was refused)",
                      log[0][1])
        self.assertEqual(self._kinds(), ["refused"],
                         "a notice that could not be placed is a fault, filed under the bell's refused kind "
                         "(review find, 2026-09-08)")
        self.be.refuse = False
        km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
        self.assertEqual([s for s, _ in self.be.sent], [SID])
        self.assertNotIn("may repeat", self.be.sent[0][1], "a known failure carried no repeat warning")
        self.assertEqual(km._pr_watches, [])
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False, True], "the story that opened on the Log closes on it")
        self.assertIn("reached session 11111111 after the earlier refusal", log[1][1])
        self.assertEqual(self._kinds(), ["refused", "sync"], "the closing row is news, not a fault")

    def test_a_changed_reason_is_said_once_more_and_a_repeated_one_stays_silent(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.be.regs, self.be.refuse = {SID: True}, True
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
            km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)                 # same reason: silent
            self.be.marker_raises = True                                  # the reason changes
            km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
            km._pr_watch_tick(100.0 + 3 * km.PR_WATCH_EVERY)             # repeated: silent
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False, False])
        self.assertIn("its record says it is alive", log[0][1])
        self.assertIn("its record could not be read", log[1][1])

    # the record says ended: no send to the registrant, the contact leg
    def test_an_ended_registrant_with_a_contact_hands_the_notice_to_the_contact(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        self.be.regs = {SID: False, MGR: True}                # alive=false: the explicit end marker
        km._pr_watch_tick(100.0)
        self.assertEqual([s for s, _ in self.be.sent], [MGR], "the notice went to the contact; none to the ended sid")
        self.assertIn("romp was watching for session 11111111 has MERGED: TESTORG/testrepo#7", self.be.sent[0][1])
        self.assertIn("That session has ended", self.be.sent[0][1])
        self.assertEqual((km._pr_watches, self._on_disk()), ([], []), "settled: retired")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [True])
        self.assertIn("went to mgr — the session that registered the watch (11111111) has ended "
                      "(its record says it ended)", log[0][1])

    def test_an_ended_registrant_with_no_contact_retires_loudly_never_silently(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.be.regs = {SID: False}
        with redirect_stderr(io.StringIO()) as err:
            km._pr_watch_tick(100.0)
        self.assertEqual(self.be.sent, [], "nothing can take it — and nothing was sent to the ended sid")
        self.assertEqual((km._pr_watches, self._on_disk()), ([], []), "…so the row retires — never a 60 s loop forever")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False], "…and LOUDLY: one Log row")
        for must in ("TESTORG/testrepo#7", "could not be delivered", "has ended (its record says it ended)",
                     "no escalation contact is named", "dropped"):
            self.assertIn(must, log[0][1])
        self.assertNotIn("a copy may have gone out", log[0][1], "no restart happened — no such caveat")
        self.assertEqual(self._kinds(), ["refused"], "a dropped notice is a fault the bell files under refused")
        self.assertIn("could not be delivered", err.getvalue())

    def test_an_ended_registrant_whose_contact_has_ended_too_retires_loudly(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        self.be.regs = {SID: False, MGR: False}
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.be.sent, km._pr_watches), ([], []))
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False])
        self.assertIn("its escalation contact mgr has ended too (its record says it ended)", log[0][1])

    def test_an_ended_registrant_whose_contact_refuses_waits_then_hands_off(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        self.be.regs, self.be.refuse = {SID: False, MGR: True}, True    # the contact stands, declines
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.be.sent, len(km._pr_watches)), ([], 1), "kept for the contact's retry")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False])
        self.assertIn("was not accepted by the escalation contact mgr (its record says it is alive, yet the send "
                      "was refused); the session that registered the watch (11111111) has ended", log[0][1])
        self.be.refuse = False
        km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(([s for s, _ in self.be.sent], km._pr_watches), ([MGR], []))
        self.assertIn("went to mgr after the earlier refusal", self._log()[1][1])

    def test_a_contact_that_is_the_ended_session_itself_is_no_contact(self):
        km._sid_of = lambda who: SID if who == "mgr" else who   # the target names the registrant
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        self.be.regs = {SID: False}
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.be.sent, km._pr_watches), ([], []))
        self.assertIn("its escalation contact is that same session", self._log()[0][1])

    def test_a_loud_retire_of_a_replayed_row_says_a_copy_may_have_gone_out(self):
        # the stamp outlived a restart: whether the pre-crash injection landed is unknowable, and
        # the loud retire must not claim "could not be delivered" without that caveat
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        km._pr_watches[0]["sent"], km._pr_watches[0]["sentDetail"] = "merged", ""
        km._pr_watches_save()
        km._pr_watches[:] = []
        km._pr_watches_load()                                 # the restart
        self.be.regs = {SID: False}
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual(km._pr_watches, [])
        self.assertIn("the watch is dropped (a copy may have gone out before the last restart)", self._log()[0][1])

    # a comment thread: never in the live set, its record says alive, taken by its backend
    def test_a_thread_registrant_is_delivered_on_the_first_attempt(self):
        km.add_pr_watch(7, "TESTORG/testrepo", THREAD, now=0, escalate="mgr")
        self.be.regs = {THREAD: True, MGR: True}              # Sessions.live() stays {} — threads are never listed
        km._pr_watch_tick(100.0)
        self.assertEqual([s for s, _ in self.be.sent], [THREAD], "delivered to the thread itself, first try")
        self.assertIn("you asked romp to watch", self.be.sent[0][1])
        self.assertEqual((km._pr_watches, self._log()), ([], []), "retired, nothing to log")

    # no record for a uuid: never sent, ended on the first read (an absent reg is durable)
    def test_a_uuid_with_no_record_is_never_sent_and_ends_on_the_first_check(self):
        # the three-check count this replaced stood in for an event the readers now report themselves:
        # an absent reg is a durable fact, since the SDK backend never unlinks one, and a reg that
        # exists but would not read RAISES and waits uncounted (review find, 2026-09-08)
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.be.regs = {}                                     # the fake holds no record for the sid
        with redirect_stderr(io.StringIO()) as err:
            km._pr_watch_tick(100.0)
        self.assertEqual((km._pr_watches, self.be.sent), ([], []), "ended on the first read, and never once sent")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False], "one row tells the whole story")
        self.assertIn("has ended (no record of the session) and no escalation contact is named; "
                      "the watch is dropped", log[0][1])
        self.assertNotIn("check", log[0][1], "no count to advance")
        self.assertEqual(self._kinds(), ["refused"])
        self.assertIn("could not be delivered", err.getvalue())

    def test_a_backend_without_a_record_reader_reads_as_no_record(self):
        self.be = _NoRecordBackend()                          # send refuses; nothing to read
        km._pr_watch_record_backends = lambda: [self.be]
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.be.sent, km._pr_watches), ([], []), "no reader, no record: ended, never sent")
        self.assertIn("has ended (no record of the session) and no escalation contact", self._log()[0][1])

    # the live set is irrelevant: empty or raising, the send is attempted
    def test_a_live_set_that_reads_empty_or_raises_never_stops_the_send(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        self.be.regs = {SID: True, MGR: True}
        km.Sessions.live = staticmethod(_live_raises)         # the probe itself is broken
        km._pr_watch_tick(100.0)
        self.assertEqual([s for s, _ in self.be.sent], [SID], "the registrant got its own mail")
        self.assertEqual((km._pr_watches, self._log()), ([], []))

    def test_a_record_reader_that_raises_fails_open(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.be.regs, self.be.refuse, self.be.marker_raises = {SID: True}, True, True
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
            km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual(len(km._pr_watches), 1, "a reader's fault never ends a watch — and is never counted")
        self.assertEqual(len(self._log()), 1)
        self.assertIn("(its record could not be read)", self._log()[0][1])
        self.be.refuse, self.be.marker_raises = False, False
        km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
        self.assertEqual(([s for s, _ in self.be.sent], km._pr_watches), ([SID], []))

    # the contact's name: durable resolution, and an unresolved name only waits
    def test_an_unresolved_contact_name_waits_uncounted_and_is_never_sent_bare(self):
        # _sid_of reads the live set — the probe — and hands an unresolvable NAME back unchanged; a
        # send to it would fall to tmux, whose send never refuses. A name the live set failed to
        # list is not a session that ended: the row waits, said once, and is never dropped for it
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._sid_of = lambda who: who
        self.be.regs = {SID: False}
        with redirect_stderr(io.StringIO()):
            for i in range(5):
                km._pr_watch_tick(100.0 + i * km.PR_WATCH_EVERY)
        self.assertEqual((self.be.sent, len(km._pr_watches)), ([], 1), "nothing went to a bare name; nothing ended")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False], "said once, however long it waits")
        self.assertIn("its escalation contact mgr does not resolve to a session; retrying", log[0][1])
        self.assertEqual(self._kinds(), ["refused"])
        self.assertIn("mail pending", km._watch_awaiting(SID)["why"], "the box shows what is owed")
        self.be.names = {"mgr": MGR}                          # the durable record now carries the name
        self.be.regs[MGR] = True
        km._pr_watch_tick(100.0 + 5 * km.PR_WATCH_EVERY)
        self.assertEqual(([s for s, _ in self.be.sent], km._pr_watches), ([MGR], []))

    def test_an_unresolved_contact_name_is_waited_on_to_the_bound_then_dropped_loudly(self):
        # the wait above is BOUNDED (review find, 2026-09-08): a name that never resolves (mistyped, or
        # a box default naming a session nobody starts again) otherwise kept the row armed forever, one
        # tmux fork per tick with the once-said Log row its only trace. At PR_WATCH_ESCALATE_S from the
        # first unresolved tick, the bound this module already keeps for waiting on the named contact,
        # the row retires the loud way: one stderr line and one bell row under the refused kind
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._sid_of = lambda who: who
        self.be.regs = {SID: False}
        with redirect_stderr(io.StringIO()) as err:
            km._pr_watch_tick(100.0)                                            # the wait starts here
            km._pr_watch_tick(100.0 + km.PR_WATCH_ESCALATE_S - 1)
            self.assertEqual(len(km._pr_watches), 1, "inside the bound: still waiting")
            self.assertEqual([ok for ok, _ in self._log()], [False], "…and still said once")
            km._pr_watch_tick(100.0 + km.PR_WATCH_ESCALATE_S + km.PR_WATCH_EVERY)
        self.assertEqual((self.be.sent, km._pr_watches), ([], []), "past the bound: dropped, never sent to a bare name")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False, False], "the wait's one row, then the drop's one row")
        for must in ("TESTORG/testrepo#7", "could not be delivered", "has ended (its record says it ended)",
                     "its escalation contact mgr did not resolve to a session for 2h", "the watch is dropped"):
            self.assertIn(must, log[1][1])
        self.assertEqual(self._kinds(), ["refused", "refused"])
        self.assertIn("did not resolve to a session for 2h", err.getvalue())

    def test_a_contact_name_resolves_through_the_backends_record_when_the_live_set_fails(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._sid_of = lambda who: who                          # the live set lists nobody
        self.be.regs, self.be.names = {SID: False, MGR: True}, {"mgr": MGR}
        km._pr_watch_tick(100.0)
        self.assertEqual(([s for s, _ in self.be.sent], km._pr_watches), ([MGR], []))

    def test_send_or_park_tells_a_refused_handover_apart_and_echoes_nothing_for_it(self):
        echoed = []
        km._optimistic_echo = lambda sid, text, author="human": echoed.append(text)
        # the return is "parked" for the FIFO arm, else the backend's own send result: a refusal is the
        # backend's False, handed over is its truthy result — never the bare bool the first cut answered
        self.assertIs(km._send_or_park(self.be, SID, "hello", echo="human"), False, "refused: neither parked nor handed")
        self.assertEqual(echoed, [], "no echo for a message the session never got")
        self.be.regs = {SID: True}
        got = km._send_or_park(self.be, SID, "hello", echo="human")
        self.assertNotEqual(got, "parked", "handed over now")
        self.assertTrue(got, "the backend's own truthy result")
        self.assertEqual(echoed, ["hello"])


class RealRouting(unittest.TestCase):
    """Through the REAL Sessions.backend_for and REAL backends: a fresh SdkBackend over a temp state
    dir, a fresh CodexBackend, and tmux's send replaced by a recorder. The routing fact this class
    pins: Sessions.backend_for picks by owns(), and owns() is False for exactly the records that
    decide a landing mail — an SDK reg that is absent or unreadable, a Codex session marked dead — so
    those sids fall to _TMUX, whose send returns True unconditionally; before this change their
    notices "were accepted" by a shell that never existed and the rows retired with no Log row. The
    decision now reads the records first, independent of ownership, and a uuid nobody holds is never
    sent. Only the gates the module already patches are patched; sends never reach a live SDK reg (a
    real send there would spawn a CLI)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        state = Path(self.td.name)
        sb, cb = _backend_mod("sdk_backend"), _backend_mod("codex_backend")
        self.sb, self.cb = sb, cb
        self.sdk = sb.SdkBackend(state, "claude", lambda *a, **k: None)             # reconcile=False: no boot sweep
        self.cx = cb.CodexBackend(state, notify=lambda *a, **k: None, log=lambda m: None)
        self.tmux = []
        self._saved = {n: getattr(km, n) for n in
                       ("PR_WATCH_FILE", "NAMES", "_pr_watch_read", "_compacting_now", "_working_now", "_limit_hold",
                        "_optimistic_echo", "_tmux_send", "_sdk", "_codex", "_watch_escalate_default")}
        self._saved_rows = list(km._pr_watches)
        km.PR_WATCH_FILE = state / "pr-watches.json"
        km.NAMES = state / "names"
        km.NAMES.mkdir()
        km._pr_watches[:] = []
        km._pr_watch_save_faults.clear()
        km._SYNC_NOTICES.clear()
        km._pending_ops.clear()
        km._pr_watch_read = lambda pr, repo: ("merged", "")
        km._compacting_now = lambda sid, *a, **k: False
        km._working_now = lambda sid, *a, **k: False
        km._limit_hold = lambda sid, *a, **k: False
        km._optimistic_echo = lambda *a, **k: None
        km._watch_escalate_default = lambda: ""
        km._tmux_send = lambda name, text, **kw: self.tmux.append((name, text))
        km._sdk = lambda: self.sdk
        km._codex = lambda: self.cx

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km._pr_watches[:] = self._saved_rows
        km._pr_watch_save_faults.clear()
        km._SYNC_NOTICES.clear()
        km._pending_ops.clear()
        self.td.cleanup()

    def _log(self):
        return [(r["ok"], r["text"]) for r in km._sync_notice_rows()]

    def _kinds(self):
        return [r["kind"] for r in km._sync_notice_rows()]

    def _dead_codex(self, sid):
        s = self.cb._Session(sid, "t1", "web", "/tmp")
        s.dead = True
        self.cx._put_session(s)

    def _tmux_contact(self, name):
        (km.NAMES / name).write_text("%s\t/tmp\n" % name)    # a tmux session: its sid IS its name

    def test_a_dead_codex_registrant_never_reaches_tmux_and_retires_loudly(self):
        self._dead_codex(SID)
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.assertIs(km.Sessions.backend_for(SID), km._TMUX, "the routing fact: a dead Codex sid falls to tmux")
        self.assertIs(km._pr_watch_end_marker(SID), True, "…while its record says it ended")
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.tmux, km._pr_watches), ([], []), "never sent to a shell that never existed; retired")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False])
        self.assertIn("has ended (its record says it ended) and no escalation contact is named", log[0][1])

    def test_a_uuid_whose_record_reader_raises_waits_and_never_reaches_tmux(self):
        # the SDK reader raising leaves the marker UNREAD, so the send still goes to the router —
        # which disowns the sid (owns() stats the reg) and picks tmux. The deliver guard refuses a
        # uuid there, the refusal classifies as "could not be read", and the row waits, uncounted
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)

        def boom(sid):
            raise OSError(5, "input/output error")
        self.sdk.end_marker = boom                       # the fresh instance's reader faults this tick
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        del self.sdk.end_marker                          # the class's real reader is back
        self.assertEqual(self.tmux, [], "a uuid is never handed to tmux, however it reached the router")
        self.assertEqual(len(km._pr_watches), 1, "the row waits")
        self.assertIsNone(km._pr_watches[0].get("_norec", {}).get(SID), "a reader's fault is not counted")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False])
        self.assertIn("its record could not be read", log[0][1])
        # the reader recovering resolves it: the reg says alive=false → ended → loud retire (no contact)
        self.sb.write_reg(Path(self.td.name), SID, {"sid": SID, "alive": False, "name": "web"})
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(200.0)
        self.assertEqual((self.tmux, km._pr_watches), ([], []))
        self.assertIn("has ended (its record says it ended)", self._log()[-1][1])

    def test_a_dead_codex_registrant_with_a_tmux_contact_hands_off(self):
        self._dead_codex(SID)
        self._tmux_contact("mgr")
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        km._pr_watch_tick(100.0)
        self.assertEqual([n for n, _ in self.tmux], ["mgr"], "the contact — a tmux session by name — got it")
        self.assertIn("romp was watching for session 11111111 has MERGED", self.tmux[0][1])
        self.assertEqual(km._pr_watches, [])
        self.assertIn("went to mgr", self._log()[0][1])

    def test_an_sdk_reg_with_alive_false_hands_off_to_the_contact(self):
        self.sb.write_reg(Path(self.td.name), SID, {"sid": SID, "name": "web", "alive": False})
        self._tmux_contact("mgr")
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        self.assertIs(km.Sessions.backend_for(SID), self.sdk, "owned (the reg exists) — the one case the old order reached")
        km._pr_watch_tick(100.0)
        self.assertEqual(([n for n, _ in self.tmux], km._pr_watches), (["mgr"], []))

    def test_an_absent_sdk_reg_for_a_uuid_sid_never_reaches_tmux_and_ends_on_the_first_check(self):
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.assertIs(km.Sessions.backend_for(SID), km._TMUX, "the routing fact: an absent reg falls to tmux")
        self.assertIsNone(km._pr_watch_end_marker(SID), "no reg file: no record, a durable fact")
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.tmux, km._pr_watches), ([], []), "ended on the first read; never sent")
        self.assertIn("has ended (no record of the session) and no escalation contact is named", self._log()[-1][1])

    def _reg_path(self):
        return Path(self.td.name) / "sdk" / (SID + ".json")

    def _unreadable_reg(self):
        """A reg that EXISTS but would not read: the path becomes a directory, so stat succeeds and
        read_text raises (EISDIR, the OSError class EMFILE, EIO and EACCES belong to), root or not."""
        p = self._reg_path()
        if p.exists():
            p.unlink()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.mkdir()

    def _running(self, sid):
        """A session running in THIS process, as SdkBackend.sessions holds one: a live thread. Its send
        is recorded, never run (a real send there would spawn a CLI)."""
        import types
        self.sdk.sessions[sid] = types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True))
        self.sdk_sent = []
        self.sdk.send = lambda s, text: self.sdk_sent.append((s, text)) or True

    def test_a_running_registrant_whose_reg_would_not_read_is_sent_its_mail_never_ended(self):
        # BEFORE (review find, 2026-09-08): SdkBackend.end_marker read through read_reg, which answers
        # None for an unreadable reg exactly as for an absent one, and never looked at the running
        # sessions. A live registrant whose reg was transiently unreadable (EMFILE, EIO, EACCES) read
        # as "no record": its mail was withheld and, three ticks later, its watch retired as ended.
        # main delivered it: owns() takes the live thread as proof and _ensure serves it from memory
        self.sb.write_reg(Path(self.td.name), SID, {"sid": SID, "alive": True, "name": "web"})
        self._unreadable_reg()
        self._running(SID)
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.assertIs(km.Sessions.backend_for(SID), self.sdk, "the routing fact: a live thread is proof enough")
        self.assertIs(km._pr_watch_end_marker(SID), False, "running in this process: the record stands")
        km._pr_watch_tick(100.0)
        self.assertEqual([s for s, _ in self.sdk_sent], [SID], "the mail went to the registrant, first tick")
        self.assertIn("has MERGED", self.sdk_sent[0][1])
        self.assertEqual((self.tmux, km._pr_watches, self._log()), ([], [], []), "retired; nothing false on the Log")

    def test_a_reg_that_would_not_read_with_no_running_session_waits_uncounted_and_recovers(self):
        self.sb.write_reg(Path(self.td.name), SID, {"sid": SID, "alive": True, "name": "web"})
        self._unreadable_reg()
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.assertFalse(self.sdk.owns(SID), "the routing fact: an unreadable reg with no thread is not owned")
        with self.assertRaises(OSError):
            self.sdk.end_marker(SID)
        self.assertEqual(km._pr_watch_end_marker(SID), km._PR_WATCH_UNREAD, "a reader's fault, never no record")
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
            km._pr_watch_tick(100.0 + km.PR_WATCH_EVERY)
        self.assertEqual((self.tmux, len(km._pr_watches)), ([], 1), "waits; never handed to tmux")
        self.assertNotIn("_norec", km._pr_watches[0], "uncounted")
        log = self._log()
        self.assertEqual([ok for ok, _ in log], [False], "said once")
        self.assertIn("its record could not be read", log[0][1])
        self.assertEqual(self._kinds(), ["refused"])
        self._reg_path().rmdir()                              # the read heals onto an ended reg
        self.sb.write_reg(Path(self.td.name), SID, {"sid": SID, "alive": False, "name": "web"})
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0 + 2 * km.PR_WATCH_EVERY)
        self.assertEqual((self.tmux, km._pr_watches), ([], []))
        self.assertIn("has ended (its record says it ended)", self._log()[-1][1])

    def test_a_corrupt_sdk_reg_is_a_readers_fault_that_waits_never_a_tmux_session_nor_an_ending(self):
        # torn JSON reads like a read error: the file is there, so it is not "no record"
        p = self._reg_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        self.assertFalse(self.sdk.owns(SID), "the routing fact: an unreadable reg is not owned")
        self.assertIs(km.Sessions.backend_for(SID), km._TMUX)
        self.assertEqual(km._pr_watch_end_marker(SID), km._PR_WATCH_UNREAD)
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.tmux, len(km._pr_watches)), ([], 1))
        self.assertIn("(its record could not be read)", self._log()[0][1])
        self.assertNotIn("no record", self._log()[0][1])

    def test_a_codex_registry_unreadable_at_load_leaves_a_uuid_registrant_waiting_never_ended(self):
        # the Codex record-holder's twin of the SDK case: with its registry unreadable at load the
        # backend holds NO sessions, so every uuid read as "no record" and a Codex registrant's watch
        # ended (on the first read now; on the third before). Unreadable at load is a reader's fault
        # the backend now reports for every sid it does not hold (review find, 2026-09-08)
        p = self.cx._reg_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        self.cx = self.cb.CodexBackend(Path(self.td.name), notify=lambda *a, **k: None, log=lambda m: None)
        km._codex = lambda: self.cx
        with self.assertRaises(Exception):
            self.cx.end_marker(SID)
        self.assertEqual(km._pr_watch_end_marker(SID), km._PR_WATCH_UNREAD)
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0)
        with redirect_stderr(io.StringIO()):
            km._pr_watch_tick(100.0)
        self.assertEqual((self.tmux, len(km._pr_watches)), ([], 1), "waits, uncounted; never tmux, never ended")
        self.assertIn("its record could not be read", self._log()[0][1])
        self._dead_codex(SID)                                 # a session it DOES hold still answers from its mark
        self.assertIs(self.cx.end_marker(SID), True)

    def test_a_name_shaped_registrant_goes_to_tmux_the_standing_gap(self):
        km.add_pr_watch(7, "TESTORG/testrepo", "web", now=0)
        km._pr_watch_tick(100.0)
        self.assertEqual([n for n, _ in self.tmux], ["web"], "a tmux registrant is sent as ever")
        self.assertEqual((km._pr_watches, self._log()), ([], []),
                         "tmux's send never refuses: accepted, retired, nothing to log — the documented gap")

    def test_a_contact_name_resolves_through_the_sdk_regs_when_the_live_set_lists_nobody(self):
        self._dead_codex(SID)
        self.sb.write_reg(Path(self.td.name), MGR, {"sid": MGR, "name": "mgr", "alive": True})
        km.add_pr_watch(7, "TESTORG/testrepo", SID, now=0, escalate="mgr")
        saved_live, saved_deliver = km.Sessions.live, km._pr_watch_deliver
        km.Sessions.live = staticmethod(lambda: {})           # the probe lists nobody
        got = []
        km._pr_watch_deliver = lambda sid, text: got.append(sid) or True   # a real send here would spawn a CLI
        try:
            self.assertEqual(self.sdk.sid_for_name("mgr"), MGR)
            km._pr_watch_tick(100.0)
        finally:
            km.Sessions.live, km._pr_watch_deliver = staticmethod(saved_live), saved_deliver
        self.assertEqual((got, km._pr_watches, self.tmux), ([MGR], [], []),
                         "resolved from the reg's name, not the live set; nothing fell to tmux")


class EndMarkerReaders(unittest.TestCase):
    """The backends' own durable-record readers, executed against real files and real session
    objects: True = an explicit end marker, False = the record says the session stands, None = no
    record. Three answers and a RAISE (a record that exists but would not read: a reader's fault the
    caller waits on, review find 2026-09-08); a live set is never consulted, the RUNNING set is (a
    thread in this process is a record too)."""

    @classmethod
    def setUpClass(cls):
        cls.sb, cls.cb = _backend_mod("sdk_backend"), _backend_mod("codex_backend")

    def test_the_sdk_name_reader_resolves_exactly_one_alive_reg(self):
        import types
        with tempfile.TemporaryDirectory() as td:
            be = types.SimpleNamespace(state_dir=Path(td))
            self.assertEqual(self.sb.SdkBackend.sid_for_name(be, "mgr"), "", "no regs → nothing")
            self.sb.write_reg(Path(td), MGR, {"sid": MGR, "name": "mgr", "alive": True})
            self.assertEqual(self.sb.SdkBackend.sid_for_name(be, "mgr"), MGR)
            self.sb.write_reg(Path(td), THREAD, {"sid": THREAD, "name": "mgr", "alive": True, "threadOf": MGR})
            self.assertEqual(self.sb.SdkBackend.sid_for_name(be, "mgr"), MGR, "a thread never answers to a name")
            self.sb.write_reg(Path(td), SID, {"sid": SID, "name": "mgr", "alive": True})
            self.assertEqual(self.sb.SdkBackend.sid_for_name(be, "mgr"), "", "two alive → ambiguous → nothing")
            self.sb.write_reg(Path(td), SID, {"sid": SID, "name": "mgr", "alive": False})
            self.assertEqual(self.sb.SdkBackend.sid_for_name(be, "mgr"), MGR, "an ended reg does not compete")

    def test_the_sdk_reader_reads_the_reg_alive_flag_the_running_set_and_nothing_else(self):
        import types
        with tempfile.TemporaryDirectory() as td:
            be = types.SimpleNamespace(state_dir=Path(td), sessions={})
            self.assertIsNone(self.sb.SdkBackend.end_marker(be, SID), "no reg file → no record (durable: never unlinked)")
            self.sb.write_reg(Path(td), SID, {"sid": SID, "alive": True})
            self.assertIs(self.sb.SdkBackend.end_marker(be, SID), False, "alive → the session stands")
            self.sb.write_reg(Path(td), SID, {"sid": SID, "alive": True, "threadOf": MGR})
            self.assertIs(self.sb.SdkBackend.end_marker(be, SID), False, "a thread's reg reads the same way")
            self.sb.write_reg(Path(td), SID, {"sid": SID, "alive": False})
            self.assertIs(self.sb.SdkBackend.end_marker(be, SID), True, "alive=false → the explicit end marker")
            (Path(td) / "sdk" / (SID + ".json")).write_text("{not json")
            with self.assertRaises(OSError, msg="exists but would not read → a reader's fault, RAISED, never no record"):
                self.sb.SdkBackend.end_marker(be, SID)
            # a session running in this process stands whatever the disk says this instant, the proof
            # owns() and _ensure already take (review find, 2026-09-08)
            be.sessions[SID] = types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True))
            self.assertIs(self.sb.SdkBackend.end_marker(be, SID), False, "running → stands, over an unreadable reg")
            self.sb.write_reg(Path(td), SID, {"sid": SID, "alive": False})
            self.assertIs(self.sb.SdkBackend.end_marker(be, SID), False, "…and over an alive=false reg, this instant")
            be.sessions[SID].thread.is_alive = lambda: False
            self.assertIs(self.sb.SdkBackend.end_marker(be, SID), True, "a finished thread is no proof: the disk decides")

    def test_the_codex_reader_reads_the_dead_mark(self):
        import types
        s = types.SimpleNamespace(lock=threading.RLock(), dead=False)
        be = types.SimpleNamespace(_session=lambda sid: s, _registry_unreadable=False)
        self.assertIs(self.cb.CodexBackend.end_marker(be, SID), False)
        s.dead = True
        self.assertIs(self.cb.CodexBackend.end_marker(be, SID), True)
        none = types.SimpleNamespace(_session=lambda sid: None, _registry_unreadable=False)
        self.assertIsNone(self.cb.CodexBackend.end_marker(none, SID), "no session by that id → no record")
        # its registry unreadable at load: the backend holds none of the sessions it should, so "not
        # held" is a reader's fault, raised, never no record (review find, 2026-09-08)
        broken = types.SimpleNamespace(_session=lambda sid: None, _registry_unreadable=True)
        with self.assertRaises(Exception):
            self.cb.CodexBackend.end_marker(broken, SID)
        held = types.SimpleNamespace(_session=lambda sid: s, _registry_unreadable=True)
        self.assertIs(self.cb.CodexBackend.end_marker(held, SID), True, "a session it holds still answers from its mark")


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

    def test_a_watch_whose_save_failed_is_refused_retryably_never_acked(self):
        saved = km._atomic_write
        km._atomic_write = _enospc
        getattr(km, "_pr_watch_save_faults", {}).clear()
        try:
            with redirect_stderr(io.StringIO()):
                st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"})
        finally:
            km._atomic_write = saved
            getattr(km, "_pr_watch_save_faults", {}).clear()
        self.assertEqual(st, 200)
        self.assertEqual((r["ok"], r["retryable"]), (False, True), "the route's refusal shape, marked retryable")
        self.assertIn("could not be saved (%s)" % ENOSPC, r["error"])
        self.assertIn("nothing is watching TESTORG/testrepo#7", r["error"])
        self.assertEqual(km._pr_watches, [], "the refused row is not held in memory either")
        self.assertFalse(km.PR_WATCH_FILE.exists())

    def test_a_standing_watch_whose_new_escalation_target_failed_to_save_is_told_apart(self):
        st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"})
        self.assertTrue(r["ok"])
        saved = km._atomic_write
        km._atomic_write = _enospc
        try:
            with redirect_stderr(io.StringIO()):
                st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web", "escalate": "mgr"})
        finally:
            km._atomic_write = saved
            km._pr_watch_save_faults.clear()
        self.assertEqual((st, r["ok"], r["retryable"]), (200, False, True))
        self.assertIn("the watch on TESTORG/testrepo#7 stands, but the escalation target could not be saved (%s)"
                      % ENOSPC, r["error"], "the truth: the watch stands; only the target was refused")
        self.assertNotIn("nothing is watching", r["error"])
        self.assertEqual(r["watch"]["pr"], 7, "…and the standing watch rides the reply")
        self.assertEqual([(x["pr"], x.get("escalate")) for x in json.loads(km.PR_WATCH_FILE.read_text())],
                         [(7, None)], "on disk: the watch, no target")
        self.assertNotIn("escalate", km._pr_watches[0])

    def test_refusals_are_loud_and_shaped(self):
        self.assertEqual(self._post({"repo": "TESTORG/testrepo", "name": "web"})[0], 400)
        self.assertEqual(self._post({"pr": 7, "repo": "not-a-repo", "name": "web"})[0], 400)
        st, r = self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "ghost"})
        self.assertFalse(r["ok"]); self.assertIn('no session answers to "ghost"', r["error"])
        self.assertEqual(self._post({"pr": 7, "repo": "TESTORG/testrepo", "name": "web"},
                                    token=False)[0], 403)



class VerdictJudgesEachCheckByItsNewestRun(unittest.TestCase):
    """T256: gh's statusCheckRollup keeps SUPERSEDED runs beside their re-runs — the label workflow
    left a failed run next to the passing one on every `gh pr create --label` PR, and any manual
    re-run does the same. The first FAILURE in list order was treated as terminal, so a stale red run
    mailed a false "FAILED check … will not land" (twice on 2026-09-07). The merge box's own rule:
    group by check name, judge each name by its NEWEST run (completion time), decide only then."""

    def _run(self, name, conclusion, completed, status="COMPLETED"):
        return {"name": name, "conclusion": conclusion, "status": status,
                "startedAt": completed.replace("Z", "") and "2026-09-07T05:00:00Z", "completedAt": completed}

    def test_a_stale_failure_beside_a_newer_success_of_the_same_name_is_not_failed(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            self._run("label check", "FAILURE", "2026-09-07T05:40:00Z"),     # the superseded run, listed first
            self._run("Python 3.12", "SUCCESS", "2026-09-07T05:45:00Z"),
            self._run("label check", "SUCCESS", "2026-09-07T05:50:00Z"),     # the re-run that actually counts
        ]}
        self.assertEqual(km._pr_watch_verdict(d), (None, ""),
                         "every name's NEWEST run passed and the PR is open → keep watching, never 'failed'")

    def test_a_newer_failure_after_an_older_success_is_failed(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            self._run("Shell (bats)", "SUCCESS", "2026-09-07T05:40:00Z"),
            self._run("Shell (bats)", "FAILURE", "2026-09-07T05:50:00Z"),
        ]}
        self.assertEqual(km._pr_watch_verdict(d), ("failed", "Shell (bats)"), "the newest run of that name is red")

    def test_a_running_rerun_of_a_failed_name_keeps_watching(self):
        d = {"state": "OPEN", "statusCheckRollup": [
            self._run("Shell (bats)", "FAILURE", "2026-09-07T05:40:00Z"),
            {"name": "Shell (bats)", "conclusion": "", "status": "IN_PROGRESS", "startedAt": "2026-09-07T05:55:00Z", "completedAt": None},
        ]}
        self.assertEqual(km._pr_watch_verdict(d), (None, "busy"), "its newest run is still in flight — the old red is superseded")


if __name__ == "__main__":
    unittest.main()
