#!/usr/bin/env python3
"""One stat rule on both sides of the mail door and in the fail-closed readers (2026-09-14): the bus and the kernel tell a
MISSING record (ENOENT, ENOTDIR, EBADF, ELOOP) from an UNREADABLE one (any other stat error) by the same errno tuple, never by
Path.exists(), whose ignored set CPython 3.14 widened to every error; a directory that cannot be read is never empty; a task
directory that exists but cannot be listed raises to its caller. Each fault staged for real (a mode-000 directory, a symlink
loop), never a stub of the call that would raise. Hermetic: temp roots; synthetic sids."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
Path(_ROOT, "romp", "session-hosts").write_text("off\n")
km = load_source("romp_kernel_stat_rule", os.path.join(BIN, "romp-kernel"))
jd = km.jd
pm = load_source("romp_postal_stat_rule", os.path.join(BIN, "romp-postal-service"))

SID = "11111111-2222-4333-8444-0000000000e1"
ROOT_ONLY = hasattr(os, "geteuid") and os.geteuid() == 0


class OneRuleOnBothSides(unittest.TestCase):
    def test_the_bus_and_the_kernel_share_the_missing_errno_tuple(self):
        self.assertEqual(tuple(pm.REG_MISSING_ERRNOS), tuple(km._REG_MISSING_ERRNOS), "the two sides must agree, or the bus holds mail while the tab paints mail on")

    def test_a_symlink_loop_reg_is_missing_on_both_sides(self):
        d = pm.SESSION_FLAGS.parent / "sdk"; d.mkdir(parents=True, exist_ok=True)
        p = d / (SID + ".json")
        os.symlink(p.name, p)
        try:
            self.assertEqual(pm._thread_of(SID), "", "the bus: an ordinary session")
            saved = jd.STATE; jd._rebind_state(pm.SESSION_FLAGS.parent)
            try:
                self.assertEqual(km._thread_reg_read(SID)[0], "missing", "the kernel: the same word")
            finally:
                jd._rebind_state(saved)
        finally:
            p.unlink()

    def test_an_unreadable_sdk_directory_is_unreadable_on_both_sides(self):
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        d = pm.SESSION_FLAGS.parent / "sdk"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".json")).write_text("{}")
        os.chmod(d, 0)
        try:
            self.assertEqual(pm._thread_of(SID), pm.THREAD_REG_UNREADABLE)
            saved = jd.STATE; jd._rebind_state(pm.SESSION_FLAGS.parent)
            try:
                km._thread_reg_memo.pop(SID, None)
                self.assertEqual(km._thread_reg_read(SID)[0], "unreadable")
            finally:
                jd._rebind_state(saved)
        finally:
            os.chmod(d, 0o755); (d / (SID + ".json")).unlink()


class UnknownIsNeverEmpty(unittest.TestCase):
    def test_dir_empty_answers_true_false_and_none(self):
        base = Path(tempfile.mkdtemp())
        self.assertTrue(pm._dir_empty(base / "absent"), "an absent directory holds nothing")
        (base / "e").mkdir(); self.assertTrue(pm._dir_empty(base / "e"))
        (base / "f").mkdir(); (base / "f" / "x").write_text(""); self.assertFalse(pm._dir_empty(base / "f"))
        (base / "file").write_text(""); self.assertTrue(pm._dir_empty(base / "file"), "a file where the directory should be is MISSING "
                                                       "in both rules (ENOTDIR): it holds nothing, and _mark_pending mints no marker for it")
        if ROOT_ONLY:
            return
        os.chmod(base / "f", 0)
        try:
            self.assertIsNone(pm._dir_empty(base / "f"), "unreadable: unknown, never empty")
        finally:
            os.chmod(base / "f", 0o755)

    def _box_with_unread_mail(self, sid):
        newd = pm.MAILROOT / sid / "new"; newd.mkdir(parents=True, exist_ok=True)
        (pm.MAILROOT / sid / "cur").mkdir(exist_ok=True); (pm.MAILROOT / sid / "tmp").mkdir(exist_ok=True)
        (newd / "m1").write_text("From: alice\n\nhi\n")
        return newd

    def test_an_unsearchable_inbox_with_unread_mail_keeps_its_marker_through_every_writer(self):
        """The medium of round two's read: _mark_pending read an unlistable new/ as no mail and UNLINKED the marker the retry
        arm had just kept, and serve() drives _reconcile_markers over every box at each bus start; the unread mail stranded
        with no wake. Executed on the real fault: the marker stands after _mark_pending, _reconcile_markers and _retry_pending."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        sid = "33333333-2222-4333-8444-0000000000e3"
        newd = self._box_with_unread_mail(sid)
        pm._mark_pending(sid)
        self.assertTrue((pm.MAILPENDING / sid).exists(), "unread mail: the marker stands")
        os.chmod(newd, 0)
        try:
            pm._mark_pending(sid)
            self.assertTrue((pm.MAILPENDING / sid).exists(), "unknown inbox: the marker stands after _mark_pending")
            pm._reconcile_markers()
            self.assertTrue((pm.MAILPENDING / sid).exists(), "and after the bus start's reconcile")
            pm._retry_pending()
            self.assertTrue((pm.MAILPENDING / sid).exists(), "and after the retry arm")
        finally:
            os.chmod(newd, 0o755)
        pm._mark_pending(sid)
        self.assertTrue((pm.MAILPENDING / sid).exists(), "readable again with mail: still standing")
        (newd / "m1").unlink(); pm._mark_pending(sid)
        self.assertFalse((pm.MAILPENDING / sid).exists(), "drained: the marker goes")

    def test_an_unsearchable_inbox_without_a_marker_gets_none(self):
        """The absent arm: unknown keeps the marker as it stands, so no marker is minted on an inbox that cannot be read."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        sid = "44444444-2222-4333-8444-0000000000e4"
        newd = self._box_with_unread_mail(sid)
        (pm.MAILPENDING / sid).unlink(missing_ok=True)
        os.chmod(newd, 0)
        try:
            pm._mark_pending(sid); pm._reconcile_markers()
            self.assertFalse((pm.MAILPENDING / sid).exists(), "absent stays absent on an unknown inbox")
        finally:
            os.chmod(newd, 0o755)


class TheQueuedLows(unittest.TestCase):
    """The lows queued at the exists() fix's reads: each fault staged for real, the answer the closed door's."""

    def test_a_file_shaped_inbox_is_missing_in_both_rules_and_mints_no_marker(self):
        sid = "55555555-2222-4333-8444-0000000000e5"
        (pm.MAILROOT / sid).mkdir(parents=True, exist_ok=True); (pm.MAILROOT / sid / "new").write_text("not a directory")
        self.assertTrue(pm._dir_empty(pm.MAILROOT / sid / "new"), "ENOTDIR is MISSING by the shared tuple: holds nothing")
        pm._mark_pending(sid)
        self.assertFalse((pm.MAILPENDING / sid).exists(), "no marker on a file-shaped inbox (a False here minted a permanent one)")

    def test_restore_answers_three_ways_and_unknown_neither_re_sends_nor_marks(self):
        """restore() over an unreadable cur/ answers UNKNOWN (the manager's correction: never a claim the message is back
        when it is not): POST /restore lists it under `unknown`, puts nothing back and re-feeds nothing; a missing claim
        stays the re-send cue; a present one is RESTORED."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        sid = "66666666-2222-4333-8444-0000000000e6"; mid = "m-claimed"
        cur = pm.MAILROOT / sid / "cur"; cur.mkdir(parents=True, exist_ok=True); (pm.MAILROOT / sid / "new").mkdir(exist_ok=True)
        (cur / mid).write_text("From: alice\n\nhi\n"); (cur / "m-ok").write_text("From: bob\n\nyo\n")
        self.assertEqual(pm.restore(sid, "m-ok"), pm.RESTORED); self.assertTrue((pm.MAILROOT / sid / "new" / "m-ok").is_file())
        self.assertEqual(pm.restore(sid, "m-gone"), pm.RESTORE_MISSING, "a missing claim: nothing to put back, the re-send cue")
        logs = []; saved = pm._log; pm._log = logs.append
        self.addCleanup(setattr, pm, "_log", saved)
        os.chmod(cur, 0)
        try:
            self.assertEqual(pm.restore(sid, mid), pm.RESTORE_UNKNOWN, "cur/ unreadable: unknown, never put back")
            woke = []; saved_wake = pm._wake_when_ready; pm._wake_when_ready = lambda s: woke.append(s)
            try:
                res, code = pm.restore_stranded({"id": sid, "mids": [mid, "m-gone"]})
            finally:
                pm._wake_when_ready = saved_wake
        finally:
            os.chmod(cur, 0o755)
        self.assertEqual((code, res["restored"], res["missing"], res["unknown"]), (200, [], [], [mid, "m-gone"]),
                         "with cur/ unreadable nothing can be answered for, the absent id included: unknown, never missing")
        self.assertTrue((cur / mid).is_file(), "the claim stands where it was")
        self.assertFalse((pm.MAILROOT / sid / "new" / mid).exists(), "nothing claimed back")
        self.assertTrue(any("could not be answered for" in m for m in logs), logs)
        import inspect
        src = inspect.getsource(pm._put_back) + inspect.getsource(pm._bounce_oversize)   # _put_back: _push's roll-back
        self.assertEqual(src.count("== RESTORE_MISSING"), 2, "both push callers re-send on MISSING alone")
        self.assertEqual(src.count("== RESTORE_UNKNOWN"), 3, "...and name the unknown answer, re-sending nothing")

    def test_an_unreadable_claude_home_keeps_the_last_known_push_answer_and_is_off_cold(self):
        """The manager's correction on the sentinels: an unreadable ~/.claude is an UNKNOWN source; the last known answer
        stands with one log line per spell, and with no answer known yet the push is off, with the line."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        home = Path(tempfile.mkdtemp()); (home / ".claude").mkdir()
        logs = []; saved = pm._log; pm._log = logs.append
        self.addCleanup(setattr, pm, "_log", saved)
        pm._PUSH_LAST[0] = None; pm._PUSH_FAULT_SAID[0] = False
        with mock.patch.object(pm.Path, "home", staticmethod(lambda: home)):
            os.chmod(home / ".claude", 0)
            try:
                self.assertTrue(pm._push_disabled(), "cold and unreadable: off")
                self.assertTrue(pm._push_disabled()); self.assertEqual(sum("no answer known yet" in m for m in logs), 1, "said once")
            finally:
                os.chmod(home / ".claude", 0o755)
            self.assertFalse(pm._push_disabled(), "readable, no sentinel: on, and the answer is now known")
            os.chmod(home / ".claude", 0)
            try:
                self.assertFalse(pm._push_disabled(), "unreadable with a known answer: the last known answer (on) stands")
                self.assertFalse(pm._push_disabled())
                self.assertEqual(sum("the last known answer stands (push on)" in m for m in logs), 1, "said once per spell")
            finally:
                os.chmod(home / ".claude", 0o755)
            (home / ".claude" / "romp-postal-nopush").write_text("")
            self.assertTrue(pm._push_disabled(), "the sentinel stands: off")
            os.chmod(home / ".claude", 0)
            try:
                self.assertTrue(pm._push_disabled(), "unreadable again: the last known answer (off) stands")
            finally:
                os.chmod(home / ".claude", 0o755)

    def test_the_flags_file_unknown_closes_the_mail_door_cold_and_keeps_the_last_answer_on_both_sides(self):
        """The same correction for the session-flags file, on the bus and on the kernel over ONE file: missing is a known
        state (mail on); unreadable or corrupt with nothing known is closed under "unreadable" on both sides; after a clean
        read the last known answer stands on both sides while the file cannot be read; a clean read re-arms."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        p = km.jd.STATE / "session-flags.json"
        saved_flags = pm.SESSION_FLAGS; pm.SESSION_FLAGS = p          # the two modules over ONE file for this test
        self.addCleanup(setattr, pm, "SESSION_FLAGS", saved_flags)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        sid = "88888888-2222-4333-8444-0000000000e8"
        (km.jd.STATE / "sdk").mkdir(exist_ok=True); (km.jd.STATE / "sdk" / (sid + ".json")).write_text('{"sid": "%s", "alive": true}' % sid)
        km._thread_reg_memo.clear()
        def cold():
            pm._FLAGS_LAST[0] = None; pm._FLAGS_FAULT_SAID[0] = False
            km._flags_cache.clear(); km._state_fault_seen.pop(str(p), None)
        def both():
            return (pm._mail_off_why(sid), km._mail_off_why_k(sid))
        cold()
        self.assertEqual(both(), ("", ""), "no flags file and no sidecar: a genuine state, mail on")
        cold(); p.write_text("{not valid json")
        self.assertEqual(pm._mail_off_why(sid), "flags", "torn bytes and nothing known: the bus closes the door under the settings word")
        self.assertEqual(km._mail_off_why_k(sid), "flags", "the kernel QUARANTINES the torn bytes (its reader moves the file aside, the "
                         "evidence kept) and holds: unknown, never a clean empty store (round two: the quarantine lifted every isolation)")
        self.assertFalse(p.exists(), "the file was moved aside by the kernel's read")
        self.assertTrue(km._flags_quarantined(p), "the sidecar stands beside the missing file")
        self.assertEqual(both(), ("flags", "flags"), "...and a missing file with a sidecar beside it is unknown on both sides, not a user who set no flags")
        self.assertIn(str(p), km._state_fault_seen, "the fault stays noted while the store is unknown")
        cold(); p.write_text('{"%s": {"postalServiceOff": true}}' % sid)
        self.assertEqual(both(), ("isolation", "isolation"), "a clean write: known again on both sides, the sidecar notwithstanding")
        os.chmod(p, 0)
        try:
            self.assertEqual(both(), ("isolation", "isolation"), "unreadable with a known answer: the last known stands")
        finally:
            os.chmod(p, 0o644)
        p.write_text("{not valid json")
        self.assertEqual(both(), ("isolation", "isolation"), "torn again WITH a known answer: the kernel quarantines and keeps the last cleanly read "
                         "flags (the isolation holds), the bus keeps its last known flags")
        self.assertIn(str(p), km._state_fault_seen)
        p.write_text("{}")
        self.assertEqual(both(), ("", ""), "a clean read: the door opens on both sides")
        self.assertNotIn(str(p), km._state_fault_seen, "the clean read ends the episode")
        os.chmod(p, 0)
        try:
            self.assertEqual(both(), ("", ""), "...and that answer is the one that stands under the next fault")
        finally:
            os.chmod(p, 0o644)

    def test_an_inbox_that_cannot_be_listed_is_a_fault_never_an_empty_inbox(self):
        """The manager's correction: read_box raises InboxUnreadable, _drain answers the fault beside empty rows (the /drain
        handler turns it into a 503, the push skips the box with one line per spell), the sweeps skip the box with a line;
        the client never reads "no mail" where mail sits unread."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        sid = "77777777-2222-4333-8444-0000000000e7"
        newd = pm.MAILROOT / sid / "new"; newd.mkdir(parents=True, exist_ok=True); (newd / "m1").write_text("From: alice\n\nhi\n")
        (pm.MAILROOT / sid / "cur").mkdir(exist_ok=True); (pm.MAILROOT / sid / "tmp").mkdir(exist_ok=True)
        logs = []; saved = pm._log; pm._log = logs.append
        self.addCleanup(setattr, pm, "_log", saved)
        saved_agents = pm.local_agents                                # the sweep returns early on an empty live listing, and
        pm.local_agents = lambda threads=False: [{"id": sid, "name": "web", "state": "idle"}]   # the seam's listing is another
        self.addCleanup(setattr, pm, "local_agents", saved_agents)  # module's business: this test owns its live agent
        pm._INBOX_UNREADABLE_SAID.clear()
        os.chmod(newd, 0)
        try:
            with self.assertRaises(pm.InboxUnreadable):
                pm.read_box(sid, consume=False)
            res = pm._drain(sid)
            self.assertEqual(res["messages"], []); self.assertIn("cannot be listed", res["unreadable"])
            pm._warn_stuck_mail(); pm._warn_stuck_mail()            # the sweep skips the box, said once
            self.assertEqual(sum("cannot be listed" in m for m in logs), 1, logs)
        finally:
            os.chmod(newd, 0o755)
        pm._warn_stuck_mail()                                        # a clean listing by the sweep alone re-arms the line
        self.assertNotIn(sid, pm._INBOX_UNREADABLE_SAID, "re-armed by any clean listing, not only a poll")
        self.assertEqual([m["id"] for m in pm.read_box(sid, consume=False)], ["m1"], "readable again: the mail is there")
        self.assertNotIn("unreadable", pm._drain(sid), "a clean drain carries no fault")

    def test_a_claim_restore_could_not_answer_for_is_held_retracted_and_put_back_by_the_retry_loop(self):
        """Round two's second medium: RESTORE_UNKNOWN inside the deferred push stranded the claim in cur/ forever with the exec
        stamp standing (no road revisited cur/). _hold_claim records the id and retracts the exec row; _retry_held_claims,
        first thing in every retry pass, puts it back once cur/ reads and drops the record; the pending marker follows."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        sid = "99999999-2222-4333-8444-0000000000e9"; mid = "m-held"
        cur = pm.MAILROOT / sid / "cur"; cur.mkdir(parents=True, exist_ok=True); (pm.MAILROOT / sid / "new").mkdir(exist_ok=True)
        (cur / mid).write_text("From: alice\nFrom-Id: a1\nDate: now\n\nhi\n")
        td = Path(tempfile.mkdtemp()); saved_tl = pm.TLDIR; pm.TLDIR = td
        self.addCleanup(setattr, pm, "TLDIR", saved_tl)
        pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": mid, "from_id": "a1", "to_id": sid})
        pm._tl_append("messages.jsonl", {"t": 2, "ev": "exec", "id": mid})
        self.assertIsNotNone(pm._sent_receipts("a1")[0]["exec"], "the claim stamped it read")
        os.chmod(cur, 0)
        try:
            self.assertEqual(pm.restore(sid, mid), pm.RESTORE_UNKNOWN)
            pm._hold_claim(sid, mid); pm._hold_claim(sid, mid)
            self.assertEqual((pm.MAILHELD / sid).read_text().split(), [mid], "recorded once")
            self.assertIsNone(pm._sent_receipts("a1")[0]["exec"], "the exec row retracted: the receipt reads pending, not read")
            pm._retry_held_claims()
            self.assertEqual((pm.MAILHELD / sid).read_text().split(), [mid], "cur/ still unreadable: the record stands")
        finally:
            os.chmod(cur, 0o755)
        pm._retry_held_claims()
        self.assertTrue((pm.MAILROOT / sid / "new" / mid).is_file(), "put back under its own id once cur/ reads")
        self.assertFalse((pm.MAILHELD / sid).exists(), "the record is dropped")
        self.assertTrue((pm.MAILPENDING / sid).exists(), "the put-back mail is pending mail for the retry")
        import inspect
        src = inspect.getsource(pm._put_back) + inspect.getsource(pm._bounce_oversize) + inspect.getsource(pm.restore_stranded)
        self.assertEqual(src.count("_hold_claim("), 4, "every site that meets RESTORE_UNKNOWN records the claim (the push's two, the "
                         "oversize bounce's, and POST /restore: round three found the fourth unwired)")
        self.assertIn("_retry_held_claims()", inspect.getsource(pm._retry_pending), "the retry pass puts held claims back first")

    def test_post_restore_over_an_unreadable_cur_holds_the_claim_and_the_drain_delivers_the_original_id_once(self):
        """Round three's first medium: restore_stranded (POST /restore) was the fourth RESTORE_UNKNOWN site, unwired: the kernel's
        caller no longer re-headed the banner (the ids came back as held), and nothing revisited cur/, so the message sat in
        cur/ forever with the receipt reading READ. Wired to _hold_claim: recorded, the exec row retracted, put back by the retry
        loop under the ORIGINAL id once cur/ reads, the pending marker set, and the drain delivers it once."""
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        sid = "aaaaaaaa-2222-4333-8444-0000000000ea"
        td = Path(tempfile.mkdtemp()); saved_tl = pm.TLDIR; pm.TLDIR = td
        self.addCleanup(setattr, pm, "TLDIR", saved_tl)
        saved_flags = pm.SESSION_FLAGS; pm.SESSION_FLAGS = td / "session-flags.json"     # no flags file: this session's mail is on
        self.addCleanup(setattr, pm, "SESSION_FLAGS", saved_flags)
        pm._FLAGS_LAST[0] = None; pm._FLAGS_FAULT_SAID[0] = False
        saved_post = pm._kernel_post; pm._kernel_post = lambda path, body, timeout=2: {"ok": True}
        self.addCleanup(setattr, pm, "_kernel_post", saved_post)
        mid = pm.deliver(sid, "web", "11111111-2222-4333-8444-0000000000e1", "the banner the kernel fed and lost", kind="coordinate")
        self.assertEqual([m["id"] for m in pm.read_box(sid, consume=True)], [mid], "claimed into cur/ (exec stamped)")
        self.assertIsNotNone(pm._sent_receipts("11111111-2222-4333-8444-0000000000e1")[0]["exec"])
        cur = pm.MAILROOT / sid / "cur"
        woke = []; saved_wake = pm._wake_when_ready; pm._wake_when_ready = lambda s: woke.append(s)
        self.addCleanup(setattr, pm, "_wake_when_ready", saved_wake)
        os.chmod(cur, 0)
        try:
            res, code = pm.restore_stranded({"id": sid, "mids": [mid]})
            self.assertEqual((code, res["restored"], res["unknown"]), (200, [], [mid]))
            self.assertEqual((pm.MAILHELD / sid).read_text().split(), [mid], "the claim is recorded for the retry loop")
            self.assertIsNone(pm._sent_receipts("11111111-2222-4333-8444-0000000000e1")[0]["exec"], "the receipt turns pending")
            pm._retry_held_claims()
            self.assertEqual((pm.MAILHELD / sid).read_text().split(), [mid], "cur/ still unreadable: held")
        finally:
            os.chmod(cur, 0o755)
        pm._retry_held_claims()
        self.assertFalse((pm.MAILHELD / sid).exists(), "put back: the record is dropped")
        self.assertTrue((pm.MAILPENDING / sid).exists(), "pending mail again")
        got = pm.read_box(sid, consume=True)
        self.assertEqual([m["id"] for m in got], [mid], "the drain delivers the ORIGINAL id once")
        self.assertEqual(pm.read_box(sid, consume=True), [], "and only once")
        self.assertEqual([p.name for p in cur.iterdir()], [mid], "claimed again under the same id")

    def test_the_two_clients_say_an_inbox_fault_as_what_it_is(self):
        """Lows 2 and 3, and round five: the Stop hook's drain (stdout is what the hook wraps; its stderr and exit code are
        dropped) and the MCP check_inbox tool never swallow a BusError or call it an internal error. The bus's own 503 for an
        unlistable inbox (the reason in the bus's log once per spell) gives the inbox sentence and no client line; a bus that
        could not be reached, another status or a decode fault (faults the bus never saw) give the SERVICE sentence and one
        client line carrying the reason, since otherwise the reason would be recorded nowhere."""
        saved_ident, saved_local = pm._self_identity, pm._LOCAL_CONFIRMED[0]
        pm._self_identity = lambda: ("11111111-2222-4333-8444-0000000000e1", "web"); pm._LOCAL_CONFIRMED[0] = True
        self.addCleanup(setattr, pm, "_self_identity", saved_ident)
        self.addCleanup(lambda: pm._LOCAL_CONFIRMED.__setitem__(0, saved_local))
        saved_ensure, saved_my, saved_http, saved_log = pm.ensure, pm.my_id, pm._http, pm._log
        pm.ensure = lambda: True; pm.my_id = lambda: "11111111-2222-4333-8444-0000000000e1"
        for name, val in (("ensure", saved_ensure), ("my_id", saved_my), ("_http", saved_http), ("_log", saved_log)):
            self.addCleanup(setattr, pm, name, val)
        import io, contextlib
        def make(text, status=None):
            def boom(method, path, payload=None):
                e = pm.BusError(text)
                if status is not None:
                    e.status = status
                raise e
            return boom
        roads = (("503 inbox", make("inbox of x cannot be listed (PermissionError: denied)", 503), "inbox"),
                 ("unreachable", make("can't reach the Romp Postal Service bus at http://127.0.0.1:1 ([Errno 111] refused)"), "service"),
                 ("500", make("internal error", 500), "service"),
                 ("decode", make("Expecting value: line 1 column 1 (char 0)"), "service"))
        for label, boom, kind in roads:
            with self.subTest(road=label):
                logs = []; pm._log = logs.append; pm._http = boom
                text, is_err = pm._mcp_call("check_inbox", {})
                self.assertTrue(is_err)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = pm.cli_drain([])
                self.assertEqual(rc, 0, "the hook's command exits clean")
                for spoken in (text, out.getvalue()):
                    self.assertNotIn("cannot be listed", spoken); self.assertNotIn("Errno", spoken); self.assertNotIn("/", spoken,
                                     "the person hears a plain sentence: no exception repr, no path, no URL")
                if kind == "inbox":
                    self.assertIn("inbox cannot be read right now", text); self.assertIn("mail could not be checked", out.getvalue())
                    self.assertEqual(logs, [], "the bus logged the 503's reason itself, once per spell; no client line")
                else:
                    self.assertIn("mail service could not be reached", text); self.assertIn("mail service could not be reached", out.getvalue())
                    self.assertNotIn("inbox", text, "a dead bus is not blamed on the inbox")
                    self.assertEqual(len(logs), 2, "each client recorded the reason once: %r" % logs)
                    self.assertTrue(all("gave no answer" in m for m in logs), logs)
                    self.assertTrue(any("refused" in m or "internal error" in m or "Expecting value" in m for m in logs), logs)


class TaskPlanLoudOnUnreadable(unittest.TestCase):
    def test_a_task_directory_that_cannot_be_listed_raises_and_a_missing_one_is_no_plan(self):
        cfg = tempfile.mkdtemp(); saved = os.environ.get("CLAUDE_CONFIG_DIR"); os.environ["CLAUDE_CONFIG_DIR"] = cfg
        try:
            self.assertIsNone(km._task_plan_cached(SID), "no directory: this session declared no plan")
            if ROOT_ONLY:
                return
            d = Path(cfg) / "tasks" / SID; d.mkdir(parents=True); (d / "1.json").write_text("{}")
            os.chmod(d, 0)
            try:
                with self.assertRaises(OSError):
                    km._task_plan_cached(SID)                    # exists but unreadable: loud, never "no plan"
            finally:
                os.chmod(d, 0o755)
            os.chmod(d.parent, 0)                                # the PARENT tasks/ unsearchable: the child's own stat fails
            try:                                                 #  with EACCES, and is_dir() read False there on 3.14 (a quiet
                with self.assertRaises(OSError):                 #  "no plan" at 1cfbae7d); by errno it is loud on every interpreter
                    km._task_plan_cached(SID)
            finally:
                os.chmod(d.parent, 0o755)
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved


if __name__ == "__main__":
    unittest.main()
