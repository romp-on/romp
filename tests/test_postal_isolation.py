#!/usr/bin/env python3
"""Postal isolation (the user 2026-06-23): a session with the timeline lane's mailbox toggled off
(postalServiceOff — legacy postalOff — in the kernel's session-flags.json) is invisible to list_agents, can't send, and can't receive —
for working privately. These pin the flag reader + the read_box RECEIVE gate at the unit level; the
end-to-end /send + /agents enforcement is in tests/romp-postal.bats.

Synthetic only — placeholder UUIDs, hermetic temp state dir, no real session data.
"""
import json
import sys
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-3333-4444-555555555555"


def _set_flag(sid, postal_off):
    pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(pm.SESSION_FLAGS.read_text()) if pm.SESSION_FLAGS.exists() else {}
    if postal_off:
        data[sid] = {"postalOff": True}     # legacy key on purpose: pins back-compat (reader honours old + new)
    else:
        data.pop(sid, None)
    pm.SESSION_FLAGS.write_text(json.dumps(data))


class PostalOff(unittest.TestCase):
    def tearDown(self):
        try:
            pm.SESSION_FLAGS.unlink()
        except OSError:
            pass

    def test_default_not_isolated(self):
        self.assertFalse(pm._postal_off(SID), "no flags file → on the Romp Postal Service")
        self.assertFalse(pm._postal_off(""), "empty sid → not isolated")

    def test_flag_toggles_isolation(self):
        _set_flag(SID, True)
        self.assertTrue(pm._postal_off(SID))
        _set_flag(SID, False)
        self.assertFalse(pm._postal_off(SID), "clearing the flag rejoins the Romp Postal Service")

    def test_other_flags_do_not_isolate(self):
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text(json.dumps({SID: {"hideFromFeed": True}}))   # muted from feed, NOT postal
        self.assertFalse(pm._postal_off(SID), "hideFromFeed alone must not isolate from postal")

    def test_malformed_flags_file_closes_cold_and_keeps_the_last_known_answer(self):
        # the flags dir is made HERE, not inherited from an earlier test: under pytest-xdist the
        # class's tests split across workers, and this one landed on a worker where no sibling had
        # created the dir yet (surfaced 2026-09-04 when the suite's test count shifted the split).
        # Until 2026-09-14 this pinned fail-open (a corrupt file read as no flags); the repo's rule is that an
        # unavailable source surfaces a fault, so a corrupt file with no flags known yet HOLDS mail (closed,
        # "unreadable", said once), and after a clean read the last known flags stand while it cannot be read
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm._FLAGS_LAST[0] = None; pm._FLAGS_FAULT_SAID[0] = False
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertTrue(pm._postal_off(SID), "a corrupt flags file with nothing known: mail held, never a quiet on")
        self.assertEqual(pm._mail_off_why(SID), "flags", "the settings file's own word (the UI: mail held, the settings file cannot be read)")
        pm.SESSION_FLAGS.write_text(json.dumps({SID: {"hideFromFeed": True}}))
        self.assertFalse(pm._postal_off(SID), "a clean read: on")
        pm.SESSION_FLAGS.write_text("{not valid json")
        self.assertFalse(pm._postal_off(SID), "corrupt again: the last known flags stand (on)")

    def _write_flags(self, flags):
        pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
        pm.SESSION_FLAGS.write_text(json.dumps(flags))

    def test_the_master_key_isolates_every_session_with_no_opinion(self):
        # The master default (the user 2026-08-27): separate sessions a person opened are separate pieces
        # of work, so cross-session mail is off unless a session opts in. Reserved "*" — never a uuid.
        self._write_flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}})
        self.assertTrue(pm._postal_off(SID), "a session with no override follows the master")
        self.assertTrue(pm._postal_off("22222222-3333-4444-5555-666666666666"), "…and so does any other")

    def test_a_session_can_opt_IN_over_a_master_default(self):
        # most-specific-wins, the notify bell's rule: an explicit False outranks a master True, so a
        # genuinely collaborating group keeps its mail while everything else stays quiet
        self._write_flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True},
                           SID: {"postalServiceOff": False}})
        self.assertFalse(pm._postal_off(SID))

    def test_a_session_override_still_isolates_with_the_master_off(self):
        self._write_flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": False},
                           SID: {"postalServiceOff": True}})
        self.assertTrue(pm._postal_off(SID))

    def test_both_readers_spell_the_master_key_alike(self):
        # Spelling only; tests/test_kernel_postal_isolation_routes.py KernelAndBusAgree pins the answers.
        self.assertEqual(pm.POSTAL_ALL_KEY, "*")
        kernel_src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertIn('POSTAL_ALL_KEY = "*"', kernel_src)
        self.assertIn('return own if own is not None else _session_flag(POSTAL_ALL_KEY, "postalServiceOff")', kernel_src,
                      "the kernel's own reader falls back to the same master default")

    def test_read_box_holds_mail_while_isolated(self):
        box = pm.MAILROOT / SID / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "msg1").write_text("From: peer\nFrom-Id: x\nDate: now\n\nhello\n")
        _set_flag(SID, True)
        self.assertEqual(pm.read_box(SID, consume=True), [],
                         "isolated → a drain delivers nothing")
        self.assertTrue((box / "msg1").exists(),
                        "the message stays in new/ (not consumed) until the session reconnects")
        _set_flag(SID, False)
        got = pm.read_box(SID, consume=True)
        self.assertEqual([m["body"] for m in got], ["hello"],
                         "reconnecting delivers the held mail")


class WiringAcrossSurfaces(unittest.TestCase):
    """The postalServiceOff flag spans three files (kernel boot exposure → timeline render/toggle → postal
    enforcement). Pin the cross-surface wiring by name so a rename can't silently disconnect a surface."""

    def test_kernel_boot_exposes_postaloff(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"postalServiceOff": _postal_isolated(sid)', src,
                      "the kernel must publish postalServiceOff in the session boot so the timeline can render it: the EFFECTIVE "
                      "state (the mailbox flag with its legacy twin, and a comment thread's mail-off default, T356)")

    def test_timeline_view_draws_and_toggles_the_mailbox(self):
        # Since 2026-07-28 the mailbox lives in the lane GEAR's drop-down (LANE_TOGGLES) rather than as
        # its own lane icon — the row still draws the mailboxIcon and toggles postalServiceOff through
        # the same _setSessionFlag persistence.
        src = open(os.path.join(os.path.dirname(BIN), "ui", "romp-timeline-view.js")).read()
        self.assertIn("mailboxIcon", src, "the gear menu draws the (monochrome) mailbox icon")
        self.assertIn("flag: 'postalServiceOff', label: 'Postal service', icon: mailboxIcon", src,
                      "the gear menu row toggles the postalServiceOff flag")
        self.assertIn("this._setSessionFlag(s, t.flag, next);", src, "menu rows persist via _setSessionFlag")



# ── a comment thread's mail is OFF until the user breaks it out (T356) ──────────────────────────────────

PARENT = "22222222-3333-4444-5555-666666666666"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
SENDER = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"


def _reg(sid, **fields):
    d = pm.SESSION_FLAGS.parent / "sdk"
    d.mkdir(parents=True, exist_ok=True)
    (d / (sid + ".json")).write_text(json.dumps({"sid": sid, "alive": True, **fields}))


def _flags(data):
    pm.SESSION_FLAGS.parent.mkdir(parents=True, exist_ok=True)
    pm.SESSION_FLAGS.write_text(json.dumps(data))


class ThreadMailOff(unittest.TestCase):
    """The bus derives a thread's mail-off default from the kernel's durable reg (threadOf) beside the flags file:
    a thread on disk with NO flag reads OFF (the user 2026-09-11: a manager's comment thread received the manager's
    mail, mailed two workers and merged a pull request as if it were the manager); the fresh key `threadMail` at the
    literal True is the one way on short of a break-out; a break-out (threadOf gone) returns the ordinary rule."""

    def tearDown(self):
        for f in (pm.SESSION_FLAGS, pm.SESSION_FLAGS.parent / "sdk" / (THREAD + ".json"), pm.SESSION_FLAGS.parent / "sdk" / (SENDER + ".json")):
            try:
                f.unlink()
            except OSError:
                pass

    def test_a_thread_with_no_flag_reads_off_and_says_why(self):
        _reg(THREAD, threadOf=PARENT)
        self.assertEqual(pm._mail_off_why(THREAD), "thread"); self.assertTrue(pm._postal_off(THREAD))
        self.assertEqual(pm._mail_off_why(PARENT), "", "the session it belongs to is untouched")
        self.assertEqual(pm._thread_of(THREAD), PARENT); self.assertEqual(pm._thread_of(PARENT), "", "no reg: an ordinary session")

    def test_the_fresh_key_at_the_literal_true_turns_it_on_and_nothing_else_does(self):
        _reg(THREAD, threadOf=PARENT)
        _flags({THREAD: {"threadMail": True}})
        self.assertEqual(pm._mail_off_why(THREAD), "")
        for v in ("true", 1, "on"):
            _flags({THREAD: {"threadMail": v}})
            self.assertEqual(pm._mail_off_why(THREAD), "thread", repr(v))
        _flags({THREAD: {"postalServiceOff": False}})
        self.assertEqual(pm._mail_off_why(THREAD), "thread", "the mailbox flag at False is no way on for a thread")
        _flags({THREAD: {"threadMail": True, "postalServiceOff": True}})
        self.assertEqual(pm._mail_off_why(THREAD), "isolation", "mail on for the thread, then the user's own isolation holds")
        _flags("{not json")
        self.assertEqual(pm._mail_off_why(THREAD), "isolation", "a corrupt flags file keeps the LAST KNOWN flags (the rule since "
                         "2026-09-14: an unavailable source is never a quiet fresh answer), so the user's isolation still holds")
        pm._FLAGS_LAST[0] = None; pm._FLAGS_FAULT_SAID[0] = False
        self.assertEqual(pm._mail_off_why(THREAD), "thread", "a corrupt flags file with nothing known cannot turn a thread's mail on")

    def test_breaking_out_returns_the_ordinary_rule(self):
        _reg(THREAD, threadOf=PARENT)
        self.assertTrue(pm._postal_off(THREAD))
        _reg(THREAD)                                   # promotion pops threadOf from the reg
        self.assertEqual(pm._mail_off_why(THREAD), ""); self.assertFalse(pm._postal_off(THREAD))
        _flags({THREAD: {"postalOff": True}})
        self.assertEqual(pm._mail_off_why(THREAD), "isolation")

    def test_the_receive_gate_holds_a_threads_mail_in_its_box(self):
        _reg(THREAD, threadOf=PARENT)
        box = pm.MAILROOT / THREAD / "new"
        box.mkdir(parents=True, exist_ok=True)
        (box / "msg1").write_text("From: peer\nFrom-Id: x\nDate: now\n\nhello\n")
        self.assertEqual(pm.read_box(THREAD, consume=True), [], "held, not delivered")
        self.assertTrue((box / "msg1").exists(), "…and kept for the day the thread is broken out")

    def test_a_thread_is_invisible_to_peers_and_a_send_to_it_is_refused_with_the_thread_reason(self):
        _reg(THREAD, threadOf=PARENT)
        rows = [{"id": PARENT, "name": "web"}, {"id": THREAD, "name": "web-comment-1", "thread": True, "parent": PARENT},
                {"id": SENDER, "name": "api"}]
        saved = pm._kernel_sessions_checked
        pm._kernel_sessions_checked = lambda threads=False: ([r for r in rows if threads or not r.get("thread")], True)
        try:
            visible = [a["id"] for a in pm.all_agents(threads=True) if not pm._postal_off(a["id"])]   # the /agents filter
            self.assertNotIn(THREAD, visible); self.assertIn(PARENT, visible)
            res = pm.resolve_recipient("web-comment-1", SENDER)
            self.assertEqual((res["kind"], res["status"]), ("error", 403))
            self.assertIn("COMMENT THREAD", res["error"]); self.assertIn("breaks it out", res["error"]); self.assertIn("final", res["error"])
            _flags({THREAD: {"threadMail": True}})
            res = pm.resolve_recipient("web-comment-1", SENDER)
            self.assertEqual(res["kind"], "direct", "with mail on, the thread is a recipient like any other")
        finally:
            pm._kernel_sessions_checked = saved


class ThreadOwnSendRefused(unittest.TestCase):
    """The thread's OWN send, over the real handler: 403 whose text says the thread's mail is off until it is
    broken out (final; the isolation refusal a peer must not route around)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        _reg(THREAD, threadOf=PARENT)
        self._saved = pm._kernel_sessions_checked
        rows = [{"id": PARENT, "name": "web"}, {"id": THREAD, "name": "web-comment-1", "thread": True, "parent": PARENT}]
        pm._kernel_sessions_checked = lambda threads=False: ([r for r in rows if threads or not r.get("thread")], True)
        pm.STATE.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        pm._kernel_sessions_checked = self._saved
        for f in (pm.SESSION_FLAGS, pm.SESSION_FLAGS.parent / "sdk" / (THREAD + ".json")):
            try:
                f.unlink()
            except OSError:
                pass

    def _send(self, frm_id, frm, to):
        import urllib.error
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/send" % self.port, method="POST",
                                     data=json.dumps({"to": to, "from": frm, "from_id": frm_id, "body": "a note", "kind": "coordinate"}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": pm.SERVE_TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_the_threads_own_send_is_refused_until_broken_out(self):
        status, body = self._send(THREAD, "web-comment-1", "web")
        self.assertEqual(status, 403, body)
        self.assertTrue(body["error"].startswith("isolation:"), "the isolation refusal a peer treats as final")
        self.assertIn("COMMENT THREAD", body["error"]); self.assertIn("breaks it out", body["error"]); self.assertIn("Nothing was sent", body["error"])
        self.assertFalse((pm.MAILROOT / PARENT / "new").exists() and any((pm.MAILROOT / PARENT / "new").iterdir()), "nothing landed")
        _reg(THREAD)                                       # broken out: the reg has no threadOf
        status, body = self._send(THREAD, "web-2", "web")
        self.assertNotEqual(status, 403, "a promoted session sends like any other: %r" % (body,))


class ScriptSenderUnderTheMaster(ThreadOwnSendRefused):
    """A script's `--from` label has no lane to opt in, so the master does not isolate it; the recipient's own
    mail state still decides."""

    def setUp(self):
        self._saved = pm._kernel_sessions_checked
        rows = [{"id": PARENT, "name": "web"}, {"id": SENDER, "name": "api"}]
        pm._kernel_sessions_checked = lambda threads=False: (rows, True)
        pm.STATE.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        super().tearDown()
        for m in (pm.MAILROOT / PARENT / "new").glob("*"):   # the delivered note, which the thread test counts as landed
            m.unlink()

    def test_a_script_reaches_an_opted_in_session(self):
        _flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}, PARENT: {"postalServiceOff": False}})
        status, body = self._send(pm.SCRIPT_SENDER_PREFIX + "cron", "cron", "web")
        self.assertEqual(status, 200, body)

    def test_a_script_to_a_master_isolated_session_gets_the_recipient_refusal(self):
        _flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}})
        status, body = self._send(pm.SCRIPT_SENDER_PREFIX + "cron", "cron", "api")
        self.assertEqual(status, 403, body)
        self.assertNotEqual(body["error"], pm.ISOLATION_SENDER, "the refusal names the recipient, not the script")

    test_the_threads_own_send_is_refused_until_broken_out = None


class MasterIsolatedIsListedAndNamed(ThreadOwnSendRefused):
    """A session only the master isolates is still listed, marked not reachable, so its branch and working note show
    before a shared repo is edited; a hand-toggled one stays hidden. Every refusal names the master and the way out."""
    MASTER = {pm.POSTAL_ALL_KEY: {"postalServiceOff": True}, SENDER: {"postalServiceOff": True}}

    def setUp(self):
        self._saved = pm._kernel_sessions_checked
        rows = [{"id": PARENT, "name": "web"}, {"id": SENDER, "name": "api"}]
        pm._kernel_sessions_checked = lambda threads=False: (rows, True)
        pm.STATE.mkdir(parents=True, exist_ok=True)
        _flags(self.MASTER)

    def _agents(self):
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/agents?me=web" % self.port,
                                     headers={"X-Romp-Token": pm.SERVE_TOKEN})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())["agents"]

    def test_the_listing_keeps_the_masters_session_marked_and_hides_the_hand_toggled_one(self):
        agents = self._agents()
        self.assertEqual([(a["id"], a.get("mailOff")) for a in agents], [(PARENT, "master")])
        self.assertIn("web  (mail off by the master default: not reachable)", pm.format_agents(agents, "", ""))

    def test_a_send_from_the_masters_session_names_the_master(self):
        status, body = self._send(PARENT, "web", "api")
        self.assertEqual((status, body["error"]), (403, pm.MASTER_SENDER))
        self.assertIn("master default", pm.MASTER_SENDER); self.assertIn("opts it in", pm.MASTER_SENDER)

    def test_a_send_to_the_masters_session_names_the_master_and_the_way_out(self):
        _flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}, SENDER: {"postalServiceOff": False}})
        status, body = self._send(SENDER, "api", "web")
        self.assertEqual(status, 403, body)
        self.assertIn("master default", body["error"]); self.assertIn("lane's mailbox", body["error"])
        self.assertIn("final", body["error"])

    def test_held_and_bounce_lines_name_the_master(self):
        self.assertIn("by the master default", pm._stuck_warn_text({"name": "web"}, PARENT, "hello"))
        self.assertNotIn("master", pm._stuck_warn_text({"name": "api"}, SENDER, "hello"), "a hand toggle keeps its own line")
        self.assertIn("master default", pm._isolated_bounce_why([{"id": PARENT, "name": "web"}], "web"))
        self.assertNotIn("master", pm._isolated_bounce_why([{"id": SENDER, "name": "api"}], "api"))

    def test_the_masters_session_publishes_its_working_note(self):
        self.assertTrue(pm._listed("master")); self.assertFalse(pm._listed("isolation")); self.assertTrue(pm._listed(""))

    test_the_threads_own_send_is_refused_until_broken_out = None


class ThreadMailOffFollowUp(unittest.TestCase):
    """The review's lows on the thread rule: a reg that exists but cannot be read fails CLOSED; the CLI judges the
    caller's own identity before a --from label substitutes a synthetic one; a sender's stuck-mail line for a thread
    says HELD, never resend; an inbound cross-host bounce names the thread refusal."""

    def tearDown(self):
        for f in (pm.SESSION_FLAGS, pm.SESSION_FLAGS.parent / "sdk" / (THREAD + ".json"), pm.SESSION_FLAGS.parent / "sdk" / (SENDER + ".json")):
            try:
                f.unlink()
            except OSError:
                pass

    def test_an_unreadable_reg_fails_closed_and_no_reg_stays_open(self):
        d = pm.SESSION_FLAGS.parent / "sdk"; d.mkdir(parents=True, exist_ok=True)
        (d / (THREAD + ".json")).write_text("{not json")
        self.assertEqual(pm._thread_of(THREAD), pm.THREAD_REG_UNREADABLE)
        self.assertEqual(pm._mail_off_why(THREAD), "unreadable", "a record that exists but cannot be read: closed, under its own reason")
        self.assertTrue(pm._postal_off(THREAD))
        (d / (THREAD + ".json")).write_text(json.dumps(["not", "a", "dict"]))
        self.assertEqual(pm._mail_off_why(THREAD), "unreadable", "…whatever shape the corruption takes")
        (d / (THREAD + ".json")).unlink()
        self.assertEqual(pm._thread_of(THREAD), ""); self.assertEqual(pm._mail_off_why(THREAD), "", "no reg at all: an ordinary session")

    def test_the_cli_judges_the_callers_own_identity_before_any_from_label(self):
        _reg(THREAD, threadOf=PARENT)
        import io
        saved = (pm._self_identity, pm.ensure, pm._http)
        calls = []
        try:
            pm._self_identity = lambda: (THREAD, "web-comment-1")
            pm.ensure = lambda: True
            pm._http = lambda *a, **k: calls.append(a) or {}
            err = io.StringIO(); real = sys.stderr; sys.stderr = err
            try:
                rc1 = pm.cli_send(["web", "a note"])
                rc2 = pm.cli_send(["--from", "nightly", "web", "a note"])
            finally:
                sys.stderr = real
            self.assertEqual((rc1, rc2), (1, 1)); self.assertEqual(calls, [], "nothing reached the bus")
            self.assertEqual(err.getvalue().count("COMMENT THREAD"), 2); self.assertIn("breaks it out", err.getvalue())
            _reg(THREAD)                                   # broken out: the same calls go through to the bus
            rc3 = pm.cli_send(["--from", "nightly", "web", "a note"])
            self.assertEqual(rc3, 0); self.assertEqual(len(calls), 1); self.assertEqual(calls[0][2]["from_id"], "ext:nightly")
        finally:
            pm._self_identity, pm.ensure, pm._http = saved

    def test_a_directory_the_bus_cannot_stat_fails_closed_without_raising(self):
        # the review's medium: the stat sat outside the try, so EACCES on sdk/ raised out of every reader
        d = pm.SESSION_FLAGS.parent / "sdk"; d.mkdir(parents=True, exist_ok=True)
        (d / (THREAD + ".json")).write_text(json.dumps({"sid": THREAD, "threadOf": PARENT}))
        if os.geteuid() == 0:
            self.skipTest("root reads through chmod 000")
        os.chmod(d, 0)
        try:
            self.assertEqual(pm._thread_of(THREAD), pm.THREAD_REG_UNREADABLE)
            self.assertEqual(pm._mail_off_why(THREAD), "unreadable")
            self.assertEqual(pm.read_box(THREAD, consume=True), [], "the receive gate answers empty, it does not raise")
            self.assertTrue(pm._postal_off(THREAD))
        finally:
            os.chmod(d, 0o755)

    def test_an_unreadable_record_gets_its_own_words_never_the_thread_diagnosis(self):
        d = pm.SESSION_FLAGS.parent / "sdk"; d.mkdir(parents=True, exist_ok=True)
        (d / (SENDER + ".json")).write_text("{corrupt")
        self.assertNotIn("COMMENT THREAD", pm.UNREADABLE_REG_SENDER); self.assertIn("cannot be read", pm.UNREADABLE_REG_SENDER)
        held = pm._stuck_warn_text({"name": "api"}, SENDER, "hello")
        self.assertTrue(held.startswith("↩ HELD")); self.assertIn("cannot read", held); self.assertNotIn("COMMENT THREAD", held)
        self.assertIn("cannot read", pm._isolated_bounce_why([{"id": SENDER, "name": "api"}], "api"))
        import io
        saved = (pm._self_identity, pm.ensure, pm._http)
        try:
            pm._self_identity = lambda: (SENDER, "api"); pm.ensure = lambda: True; pm._http = lambda *a, **k: {}
            err = io.StringIO(); real = sys.stderr; sys.stderr = err
            try:
                rc = pm.cli_send(["web", "a note"])
            finally:
                sys.stderr = real
            self.assertEqual(rc, 1); self.assertIn("cannot be read", err.getvalue()); self.assertNotIn("COMMENT THREAD", err.getvalue())
        finally:
            pm._self_identity, pm.ensure, pm._http = saved

    def test_an_inbound_relay_to_a_thread_bounces_through_relay_in_itself(self):
        # the review's medium: _relay_in listed the DEFAULT rows (no threads), so the thread arm never ran and the relay retried forever
        _reg(THREAD, threadOf=PARENT)
        rows = [{"id": PARENT, "name": "web"}, {"id": THREAD, "name": "web-comment-1", "thread": True, "parent": PARENT}]
        saved = pm._kernel_sessions_checked
        pm._kernel_sessions_checked = lambda threads=False: ([r for r in rows if threads or not r.get("thread")], True)
        try:
            verdict, bounce = pm._relay_in("TESTHOST", {"mid": "relay-thread-0001", "to": "web-comment-1", "frm": "api", "frm_id": "id-api", "body": "a note", "kind": "coordinate"})
            self.assertEqual(verdict, "bounce", (verdict, bounce))
            self.assertIn("COMMENT THREAD", bounce["why"]); self.assertIn("breaks it out", bounce["why"])
        finally:
            pm._kernel_sessions_checked = saved

    def test_the_stuck_mail_line_says_held_for_a_thread_and_resend_for_a_session(self):
        _reg(THREAD, threadOf=PARENT)
        held = pm._stuck_warn_text({"name": "web-comment-1"}, THREAD, "please  look\nat this")
        self.assertTrue(held.startswith("↩ HELD")); self.assertIn("breaks it out", held); self.assertIn("Nothing to resend", held)
        self.assertIn("Original: please look at this", held)
        stuck = pm._stuck_warn_text({"name": "api"}, SENDER, "hello")
        self.assertTrue(stuck.startswith("↩ STILL UNDELIVERED")); self.assertIn("resend", stuck)

    def test_the_stuck_mail_line_says_held_for_an_isolated_session_by_master_or_own_key(self):
        for flags in ({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}}, {SENDER: {"postalServiceOff": True}}):
            with self.subTest(flags=flags):
                _flags(flags)
                held = pm._stuck_warn_text({"name": "api"}, SENDER, "hello")
                self.assertTrue(held.startswith("↩ HELD"), held)
                self.assertIn("mail off", held); self.assertIn("Nothing to resend", held)

    def test_an_inbound_bounce_names_the_thread_refusal(self):
        _reg(THREAD, threadOf=PARENT)
        why = pm._isolated_bounce_why([{"id": THREAD, "name": "web-comment-1"}], "web-comment-1")
        self.assertIn("COMMENT THREAD", why); self.assertIn("breaks it out", why)
        _flags({SENDER: {"postalServiceOff": True}})
        self.assertEqual(pm._isolated_bounce_why([{"id": SENDER, "name": "api"}], "api"), "recipient 'api' has its mailbox off (postal isolation)")


class CliJudgesIsolationToo(unittest.TestCase):
    """`romp mail send --from <label>` judged the caller for the thread and unreadable doors only; a mailbox the user
    toggled OFF fell through (the review's low). Every closed door now stops the CLI before any label applies."""

    def tearDown(self):
        try:
            pm.SESSION_FLAGS.unlink()
        except OSError:
            pass

    def test_an_isolated_caller_is_stopped_with_the_isolation_words_with_and_without_from(self):
        _flags({SENDER: {"postalServiceOff": True}})
        import io
        saved = (pm._self_identity, pm.ensure, pm._http)
        calls = []
        try:
            pm._self_identity = lambda: (SENDER, "api"); pm.ensure = lambda: True; pm._http = lambda *a, **k: calls.append(a) or {}
            err = io.StringIO(); real = sys.stderr; sys.stderr = err
            try:
                rc1 = pm.cli_send(["web", "a note"]); rc2 = pm.cli_send(["--from", "nightly", "web", "a note"])
            finally:
                sys.stderr = real
            self.assertEqual((rc1, rc2), (1, 1)); self.assertEqual(calls, [], "nothing reached the bus")
            self.assertEqual(err.getvalue().count("YOUR OWN mailbox is OFF"), 2); self.assertNotIn("COMMENT THREAD", err.getvalue())
            self.assertEqual(pm.ISOLATION_SENDER, pm.ISOLATION_SENDER.strip()); self.assertIn("isolation:", pm.ISOLATION_SENDER)
        finally:
            pm._self_identity, pm.ensure, pm._http = saved

    def test_a_master_isolated_caller_is_stopped_with_the_master_words(self):
        _flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}})
        import io
        saved = (pm._self_identity, pm.ensure, pm._http)
        calls = []
        try:
            pm._self_identity = lambda: (SENDER, "api"); pm.ensure = lambda: True; pm._http = lambda *a, **k: calls.append(a) or {}
            err = io.StringIO(); real = sys.stderr; sys.stderr = err
            try:
                rc = pm.cli_send(["web", "a note"])
            finally:
                sys.stderr = real
            self.assertEqual((rc, calls), (1, []))
            self.assertIn(pm.MASTER_SENDER, err.getvalue())
        finally:
            pm._self_identity, pm.ensure, pm._http = saved


class ASharedNameWithAMasterIsolatedSession(unittest.TestCase):
    """Two live sessions under one name, one reachable and one only the master isolates: list_agents shows both, so
    a send by that name is refused as ambiguous rather than handed to the reachable one."""
    A, B = "aaaaaaaa-1111-2222-3333-444444444444", "bbbbbbbb-1111-2222-3333-444444444444"

    def setUp(self):
        self._saved = pm._kernel_sessions_checked
        rows = [{"id": self.A, "name": "alice"}, {"id": self.B, "name": "alice"}, {"id": SENDER, "name": "api"}]
        pm._kernel_sessions_checked = lambda threads=False: (rows, True)

    def tearDown(self):
        pm._kernel_sessions_checked = self._saved
        try:
            pm.SESSION_FLAGS.unlink()
        except OSError:
            pass

    def test_the_send_is_refused_and_names_the_unreachable_candidate(self):
        _flags({pm.POSTAL_ALL_KEY: {"postalServiceOff": True}, self.B: {"postalServiceOff": False},
                SENDER: {"postalServiceOff": False}})
        res = pm.resolve_recipient("alice", SENDER)
        self.assertEqual((res["kind"], res["status"]), ("error", 409), res)
        self.assertIn("[aaaaaaaa] (not reachable)", res["error"]); self.assertIn("[bbbbbbbb]", res["error"])

    def test_a_hand_toggled_namesake_stays_out_of_the_choice(self):
        _flags({self.A: {"postalServiceOff": True}, SENDER: {"postalServiceOff": False}})
        res = pm.resolve_recipient("alice", SENDER)
        self.assertEqual((res["kind"], res["agent"]["id"]), ("direct", self.B))

if __name__ == "__main__":
    unittest.main(verbosity=2)
