#!/usr/bin/env python3
"""The sender's "read" means the recipient's model has the mail, not that the bus handed it to the kernel (2026-10-03).

The live push claims a recipient's mail (new/ -> cur/) and POSTs the banner to the kernel's /deliver; an SDK session
QUEUES it and feeds its CLI one text at a time, and the CLI reads a text fed mid-turn only at its next tool boundary.
A session in a long shell command, or behind a queue of earlier texts, takes the banner minutes to hours after the
claim. The claim used to write the read stamp (the exec row every receipt reader shows as "read"), so a sender saw
"read" for hours while the mail sat in the queue.

Now:
- the push claims without stamping; a kernel whose backend reports takes answers /deliver with reportsTake, the bus
  records the banner's ids as waiting for the take, and the sender's receipt reads "waiting in the session's queue";
- the SDK backend reports the CLI's take (the same exact event that releases its one-text-at-a-time feed hold) to the
  bus's POST /seen, which writes the stamp then; a take also stamps every id queued before it, so a take the kernel
  could not see is settled by the next one;
- a kernel that does not promise takes (an older kernel, a Codex session, a forwarded wake) is stamped at its answer,
  as before; the session's own inbox read and its turn-end drain hand mail straight to the model and stamp at once.

SYNTHETIC fixtures only: invented text, placeholder uuids, TESTHOST; no real session names or message ids.
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: both modules resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_read_at_take", os.path.join(BIN, "romp_sdk_backend.py"))
pm = load_source("romp_postal_read_at_take", os.path.join(BIN, "romp-postal-service"))

SENDER = "11111111-2222-3333-4444-555555555555"
_N = [0]


def _fresh_sid(prefix="77777777-8888-9999-aaaa-"):
    """A placeholder recipient id of its own per test: the maildir, the records and the ledger are module-wide."""
    _N[0] += 1
    return prefix + "%012d" % _N[0]


def _timeline(mid):
    """The ledger events for one message id, in order."""
    p = pm.TLDIR / "messages.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if row.get("id") == mid:
            out.append(row.get("ev"))
    return out


def _receipt(mid):
    """The sender's receipt row for `mid`, as check_sent and `romp mail sent` read it."""
    return next(r for r in pm._sent_receipts(SENDER) if r["id"] == mid)


class _Bus(unittest.TestCase):
    """The real bus (_push, _drain, read_box, the records, the ledger) with the kernel stubbed at _kernel_post."""

    def setUp(self):
        self.to = _fresh_sid()
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # not a seam test: let _push actually post
        self.saved = (pm._kernel_post, pm._push_disabled, pm._log, pm.local_agents)
        pm._push_disabled = lambda: False
        pm.local_agents = lambda threads=False: [{"id": self.to, "name": "api", "state": "working"}]
        self.logged, self.posted = [], []
        pm._log = self.logged.append
        pm.STREAKS.pop(self.to, None)

    def tearDown(self):
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm._kernel_post, pm._push_disabled, pm._log, pm.local_agents = self.saved
        pm.STREAKS.pop(self.to, None)

    def _kernel(self, answer):
        def post(path, body, timeout=2, no_answer=None):
            self.posted.append(sb.postal_mids(body.get("text", "")))
            return no_answer if answer is pm.NO_ANSWER else dict(answer)
        pm._kernel_post = post

    def _send(self, body="the run finished; the numbers are in the usual place"):
        mid = pm.deliver(self.to, "web", SENDER, body, kind="coordinate")
        pm.STREAKS.pop(self.to, None)                     # the loop guard is not under test
        return mid

    def _push(self):
        return pm._push(self.to, {"id": self.to, "state": "working"})


class PushHoldsTheReadStampUntilTheTake(_Bus):

    def test_a_banner_the_kernel_queued_is_not_read_until_the_cli_takes_it(self):
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        mid = self._send()
        self.assertTrue(self._push(), "the kernel queued it: a landed push")
        self.assertEqual(_timeline(mid), ["sent"],
                         "no exec row at the claim: the session is busy in a long tool call and has not read it; "
                         "before the fix the claim wrote exec here and the sender read 'read' for hours")
        self.assertTrue((pm.MAILROOT / self.to / "cur" / mid).is_file(), "claimed: it is not fed a second time")
        self.assertEqual(pm.read_box(self.to, consume=False), [], "nothing left in new/ for the drain")
        r = _receipt(mid)
        self.assertIsNone(r["exec"])
        self.assertTrue(r.get("queued"))
        self.assertIn("waiting in the session's queue (not read yet)", pm.format_receipts([r]))
        self.assertEqual(pm._await_read(self.to), [mid])
        # the CLI takes the banner at its next tool boundary; the kernel reports it
        payload, status = pm.taken_report({"id": self.to, "mids": [mid]})
        self.assertEqual((status, payload), (200, {"ok": True, "stamped": [mid]}))
        self.assertEqual(_timeline(mid), ["sent", "exec"], "stamped read at the take")
        self.assertIn("read ", pm.format_receipts([_receipt(mid)]))
        self.assertEqual(pm._await_read(self.to), [], "the record is retired")
        # a repeated report (a re-post the kernel recognised) stamps nothing twice
        self.assertEqual(pm.taken_report({"id": self.to, "mids": [mid]}), ({"ok": True, "stamped": []}, 200))
        self.assertEqual(_timeline(mid), ["sent", "exec"])

    def test_a_take_also_stamps_what_was_queued_before_it(self):
        """The CLI takes its queue in order: a report for a later banner proves the earlier ones were taken, so a take
        the kernel could not see (a restart while a host-kept CLI held the banner) is settled by the next one."""
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        first = self._send("first invented note")
        self._push()
        second = self._send("second invented note")
        self._push()
        self.assertEqual(pm._await_read(self.to), [first, second])
        payload, _ = pm.taken_report({"id": self.to, "mids": [second]})
        self.assertEqual(payload["stamped"], [first, second])
        self.assertEqual((_timeline(first), _timeline(second)), (["sent", "exec"], ["sent", "exec"]))
        self.assertTrue(any("queued before it" in ln and first in ln for ln in self.logged), self.logged)

    def test_a_take_does_not_stamp_what_was_queued_after_it(self):
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        first = self._send("first invented note")
        self._push()
        second = self._send("second invented note")
        self._push()
        self.assertEqual(pm.taken_report({"id": self.to, "mids": [first]})[0]["stamped"], [first])
        self.assertEqual(_timeline(second), ["sent"], "still in the queue behind it: not read")
        self.assertEqual(pm._await_read(self.to), [second])

    def test_an_older_kernel_that_does_not_report_takes_is_stamped_at_its_answer(self):
        self._kernel({"ok": True, "injected": True})          # no reportsTake: an older kernel, or a Codex session
        mid = self._send()
        self.assertTrue(self._push())
        self.assertEqual(_timeline(mid), ["sent", "exec"], "nothing would ever report the take: the old stamp")
        self.assertEqual(pm._await_read(self.to), [])

    def test_a_record_that_cannot_be_written_falls_back_to_the_answer_stamp(self):
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        mid = self._send()
        saved = pm._await_add
        pm._await_add = lambda sid, mids: False
        try:
            self.assertTrue(self._push())
        finally:
            pm._await_add = saved
        self.assertEqual(_timeline(mid), ["sent", "exec"], "no report could find it: stamped at the answer")

    def test_mail_the_kernel_did_not_take_goes_back_and_leaves_the_record(self):
        self._kernel({"ok": True, "injected": False})
        mid = self._send()
        self.assertFalse(self._push())
        self.assertEqual([m["id"] for m in pm.read_box(self.to, consume=False)], [mid], "back in new/ for the drain")
        self.assertEqual(pm._await_read(self.to), [])
        self.assertNotIn("exec", _timeline(mid), "never read")

    def test_a_chunk_in_doubt_waits_for_the_take_and_an_answer_settles_it(self):
        self._kernel(pm.NO_ANSWER)
        mid = self._send()
        self.assertFalse(self._push())
        self.assertEqual(_timeline(mid), ["sent"], "no answer: claimed, held in doubt, not read")
        self.assertEqual(pm._await_read(self.to), [mid])
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        pm._resolve_in_doubt(self.to, {"id": self.to})
        self.assertEqual(_timeline(mid), ["sent"], "answered taken by a kernel that reports takes: still waits")
        pm.taken_report({"id": self.to, "mids": [mid]})
        self.assertEqual(_timeline(mid), ["sent", "exec"])

    def test_a_chunk_in_doubt_taken_before_its_re_post_is_stamped_once(self):
        """The kernel queued the chunk although its answer was late, the CLI took it and the take was reported, and
        then the retry pass re-posted it: the kernel recognises the ids and answers taken. The ids have left the record
        because they were READ, not because nothing will report them, so the answer must not stamp them a second time
        (a second exec row moves the sender's "read" time to the re-post, and a second cross-host receipt goes out)."""
        self._kernel(pm.NO_ANSWER)
        mid = self._send()
        self.assertFalse(self._push())
        self.assertEqual(pm.taken_report({"id": self.to, "mids": [mid]})[0]["stamped"], [mid])
        self.assertEqual(_timeline(mid), ["sent", "exec"])
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        pm._resolve_in_doubt(self.to, {"id": self.to})
        self.assertEqual(_timeline(mid), ["sent", "exec"], "read once, at the take")
        self.assertEqual(pm._doubt_read(self.to), [], "the answer settled the doubt")

    def test_a_chunk_in_doubt_answered_by_an_older_kernel_is_stamped_at_the_answer(self):
        self._kernel(pm.NO_ANSWER)
        mid = self._send()
        self._push()
        self._kernel({"ok": True, "injected": True})
        pm._resolve_in_doubt(self.to, {"id": self.to})
        self.assertEqual(_timeline(mid), ["sent", "exec"])
        self.assertEqual(pm._await_read(self.to), [])

    def test_a_banner_the_kernel_hands_back_leaves_the_record(self):
        self._kernel({"ok": True, "injected": True, "reportsTake": True})
        mid = self._send()
        self._push()
        self.assertEqual(pm.restore(self.to, mid), pm.RESTORED)   # a teardown stranded it (the kernel's /restore)
        self.assertEqual(pm._await_read(self.to), [])
        self.assertEqual(pm.taken_report({"id": self.to, "mids": [mid]})[0]["stamped"], [],
                         "a late report for mail back in new/ stamps nothing")

    def test_a_malformed_report_is_refused(self):
        for bad in ({}, {"id": self.to}, {"id": self.to, "mids": "x"}, {"id": "../x", "mids": ["a"]},
                    {"id": self.to, "mids": [""]}):
            self.assertEqual(pm.taken_report(bad)[1], 400, bad)


class DirectReadsStampAtOnce(_Bus):
    """The session's own inbox read and its turn-end drain hand the mail straight to the model: stamped at the claim."""

    def test_the_turn_end_drain_stamps_at_the_claim(self):
        mid = self._send()
        self.assertEqual([m["id"] for m in pm._drain(self.to)["messages"]], [mid])
        self.assertEqual(_timeline(mid), ["sent", "exec"])

    def test_the_inbox_read_stamps_at_the_claim(self):
        mid = self._send()
        self.assertEqual([m["id"] for m in pm.read_box(self.to, consume=True)], [mid])
        self.assertEqual(_timeline(mid), ["sent", "exec"])


class _UnstartedSdk:
    """One registered SDK session with no thread, no loop and no CLI: SdkBackend.deliver queues into it and the test
    plays the CLI's take by hand."""

    def _make(self, to):
        self.to = to
        self.state = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.state, "sdk"))
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                            # a self-minted state root pins per-session hosts off
        self._cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.state, "claude")
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd, exist_ok=True)
        tp = sb.transcript_path(self.cwd, self.to)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        open(tp, "w").close()
        self.klog = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None,
                                log=lambda m, **k: self.klog.append(str(m)))
        reg = {"sid": self.to, "name": "api", "mode": "acceptEdits", "alive": True, "cwd": self.cwd, "lastSid": self.to}
        sb.write_reg(self.be.state_dir, self.to, reg)
        self.sess = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[self.to] = self.sess
        self.be._ensure = lambda sid, on_boot_settled=None: self.be.sessions.get(sid)

    def _unmake(self):
        if self._cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg

    def _take_head(self, fresh=True):
        """Play the feeder and the CLI: the head of the queue is fed, and the CLI takes it (a turn frame after a feed
        from idle, _untaken_taken's first rule)."""
        with self.sess._lock:
            item, _meta = self.sess._pop_for_feed_locked(0)
        self.sess._untaken = {"text": item, "item": item, "fresh": fresh, "settled": False, "t": int(time.time()),
                              "off": None, "fsid": None}
        self.sess._on_message(_Asst(), _Asst, _Result, _System)
        return item


class _Asst:
    def __init__(self):
        self.content, self.model, self.uuid, self.parent_tool_use_id = [], "claude-x", "a1", None
        self.stop_reason, self.error = None, None


class _Result:
    pass


class _System:
    def __init__(self, subtype="init", data=None):
        self.subtype, self.data = subtype, data or {}


def _wait(pred, what, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for: %s" % what)


class SdkReportsTheTake(_UnstartedSdk, unittest.TestCase):
    """The kernel half: the backend promises takes only with the reporter installed, and reports the CLI's take of a
    banner (and only of a banner) with its message ids."""

    def setUp(self):
        self._make(_fresh_sid("88888888-9999-aaaa-bbbb-"))
        self.reports = []
        self.be.postal_taken = lambda sid, mids: self.reports.append((sid, list(mids)))

    def tearDown(self):
        self._unmake()

    def test_the_backend_promises_takes_only_with_the_reporter_installed(self):
        self.assertTrue(self.be.reports_postal_takes(self.to))
        self.be.postal_taken = None
        self.assertFalse(self.be.reports_postal_takes(self.to), "an older kernel installs none: the bus stamps at once")

    def test_the_cli_taking_a_banner_reports_its_ids(self):
        banner = pm.format_push([{"from": "web", "from_id": SENDER, "body": "an invented note", "id": m, "date": ""}
                                 for m in ("1700000100.1_aa.TESTHOST", "1700000101.2_bb.TESTHOST")])
        self.assertTrue(self.be.deliver(self.to, banner))
        self.assertEqual(self.reports, [], "queued is not taken: nothing reported at the delivery")
        self._take_head()
        _wait(lambda: self.reports, "the take report")
        self.assertEqual(self.reports, [(self.to, ["1700000100.1_aa.TESTHOST", "1700000101.2_bb.TESTHOST"])])

    def test_a_plain_send_taken_reports_nothing(self):
        self.sess.enqueue("an invented instruction from the person at the keyboard")
        self._take_head()
        time.sleep(0.1)
        self.assertEqual(self.reports, [])

    def test_a_banner_that_landed_before_the_cli_exited_is_reported(self):
        banner = pm.format_push([{"from": "web", "from_id": SENDER, "body": "an invented note",
                                  "id": "1700000102.3_cc.TESTHOST", "date": ""}])
        self.be._text_landed = lambda *a, **k: True
        self.sess._untaken = {"text": banner, "item": banner, "fresh": False, "settled": True, "t": int(time.time()),
                              "off": None, "fsid": None}
        self.sess._release_hold_at_exit()
        _wait(lambda: self.reports, "the take report")
        self.assertEqual(self.reports, [(self.to, ["1700000102.3_cc.TESTHOST"])])

    def test_a_report_the_bus_refuses_is_logged_not_raised(self):
        def refuse(sid, mids):
            raise RuntimeError("bus /seen answered 503")
        self.be.postal_taken = refuse
        self.be._report_postal_take(self.to, ["1700000103.4_dd.TESTHOST"], "taken")
        _wait(lambda: any("could not be told" in ln for ln in self.klog), "the log line")


class EndToEnd(_UnstartedSdk, _Bus):
    """The whole chain on synthetic data: a peer's mail reaches a session that is busy (a long tool call), the kernel
    queues it behind an earlier text, the sender's receipt says it is waiting, and it reads "read" only when the CLI
    takes it. The kernel's /deliver is played by its own two lines (deliver, then reports_postal_takes); the take report
    goes straight to the bus's handler."""

    def setUp(self):
        _Bus.setUp(self)
        self._make(self.to)
        self.be.postal_taken = lambda sid, mids: pm.taken_report({"id": sid, "mids": list(mids)})

        def post(path, body, timeout=2, no_answer=None):
            injected = bool(self.be.deliver(body["id"], body["text"]))
            return {"ok": True, "injected": injected,
                    "reportsTake": bool(injected and self.be.reports_postal_takes(body["id"]))}
        pm._kernel_post = post

    def tearDown(self):
        self._unmake()
        _Bus.tearDown(self)

    def test_mail_behind_a_queued_text_reads_read_only_when_the_cli_takes_it(self):
        self.sess.enqueue("an invented timer text sent earlier")      # already queued ahead of the mail
        mid = self._send("an invented peer question")
        self.assertTrue(self._push())
        self.assertEqual(len(self.sess.pending()), 2, "the banner waits behind the earlier text")
        self.assertEqual(_timeline(mid), ["sent"])
        self.assertTrue(_receipt(mid).get("queued"))
        self._take_head()                                             # the CLI takes the timer text
        time.sleep(0.1)
        self.assertEqual(_timeline(mid), ["sent"], "the earlier text's take does not read the mail")
        self._take_head()                                             # the CLI takes the banner
        _wait(lambda: _timeline(mid) == ["sent", "exec"], "the read stamp at the take")
        self.assertFalse(_receipt(mid).get("queued"))


class DeliverRouteAnswersReportsTake(unittest.TestCase):
    """The kernel's /deliver carries reportsTake beside injected, read off the owning backend (duck-typed)."""

    def test_the_route_reads_the_backend_and_answers_the_flag(self):
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        route = src.split('if u.path == "/deliver":', 1)[1].split("if u.path ==", 1)[0]
        self.assertIn('getattr(Sessions.backend_for(sid), "reports_postal_takes", None)', route)
        self.assertIn('"reportsTake": takes', route)
        self.assertIn("injected and callable(rt) and rt(sid)", route, "promised only for a banner it took")

    def test_the_kernel_installs_the_take_reporter(self):
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertIn("_sdk_backend.postal_taken = _bus_report_take", src)
        body = src.split("def _bus_report_take(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"/seen"', body)


if __name__ == "__main__":
    unittest.main()
