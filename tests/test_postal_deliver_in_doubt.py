#!/usr/bin/env python3
"""A /deliver the kernel took but answered too late must not deliver the same message again (2026-09-29).

The bus claims a recipient's mail (new/ -> cur/, an exec row), POSTs the banner to the kernel's /deliver and waits
DELIVER_TIMEOUT (12 s) for the answer. The kernel does not stop when the bus stops waiting: it finishes and queues
the banner. Before this fix the bus read the missing answer as "kernel unreachable" and put every message back
(restore: cur/ -> new/, an unexec row, the pending marker raised), so the same message came in again three ways:
the next Stop-hook drain claimed it and fed it, the retry pass claimed it and posted it again, and each new claim
took the whole box, so the banner grew and the next post was slower still. Under a slow kernel one message was
delivered thirty-odd times in eighteen minutes, as exec/unexec pairs about every half minute.

The fix has two halves, and these tests pin both:
- the bus tells "the request never left" (a refused connect: put the mail back, as before) from "the request was
  sent and no answer came" (a read timeout or reset: the outcome is unknown). An unknown outcome leaves the mail
  claimed in cur/ with no unexec row, records the chunk as IN DOUBT, claims nothing more for that recipient, and
  the retry pass re-posts the SAME chunk under the same message ids until the kernel answers for it;
- the kernel's /deliver is safe to repeat: SdkBackend.deliver answers True without queueing a second copy of a
  banner whose message ids the session has all taken already (a bounded list, persisted in the registry so it
  outlives a kernel restart), and forgets the ids it hands back to the bus when a teardown strands a banner.

The review of the first cut added four more, pinned here too:
- a chunk nothing will re-post is RELEASED back to new/ under its own ids (an unexec row, a log line): when the
  kernel's answered listing no longer carries the recipient (or a remote peer's heartbeat has lapsed), when it has
  gone unlisted past DOUBT_GONE_GRACE with no answer either way, and when the live push is switched off. Held in cur/
  it read as delivered for good, out of reach of the orphan bounce and the stuck-mail warning, and the retry pass
  asked for a session list on every pass for ever;
- the kernel queues a banner and records its ids as taken in one lock hold and one registry write, so a kernel death
  or a racing re-post can never meet the ids taken with the banner in no queue;
- a stranded banner's ids are forgotten BEFORE the bus is asked to take the mail back, since the bus re-posts at once.

The review of round two added three more here (a fourth, the judge-usage prune's back-off after a failed write, is
pinned in tests/test_timeline_bars_resilience.py):
- a remote peer is told from a local session by a mark set only on a real remote heartbeat (no local registry record),
  never by presence in the heartbeat table, where a local session's beat lands whenever the kernel's list is slow; a
  lapsed beat leaves the table;
- the gone-clock goes with the hold, whatever path settles it (a send's or a wake's own push, a hand-back, a release);
- a stranded banner is handed back IN HAND: a bus re-post of it queued before the hand-back settles is the one copy,
  even when the bus's answer then times out.

SYNTHETIC fixtures only: invented text, placeholder uuids, TESTHOST; no real session names or message ids.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: both modules resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_in_doubt", os.path.join(BIN, "romp_sdk_backend.py"))
pm = load_source("romp_postal_in_doubt", os.path.join(BIN, "romp-postal-service"))

TO = "66666666-7777-8888-9999-aaaaaaaaaaaa"       # the recipient session (the kernel-side tests)
_N = [0]


def _fresh_sid(prefix="66666666-7777-8888-9999-"):
    """A placeholder recipient id of its own per test: the maildir and ledger are module-wide, so a shared id would
    carry one test's claims into the next."""
    _N[0] += 1
    return prefix + "%012d" % _N[0]


SENDER = "11111111-2222-3333-4444-555555555555"
MID1 = "1700000000.111111.TESTHOST"
MID2 = "1700000001.222222.TESTHOST"

CLIENT_WAIT = 1.0      # the bus's wait for an answer, shortened for the test (production: DELIVER_TIMEOUT, 12 s)
SLOW_ANSWER = 2.0      # how long the slow kernel takes to answer AFTER it has queued the banner


def _banner(*mids, body="the pod is up; please take the next fire"):
    """A bus banner exactly as _push hands it to /deliver: one message per id, the bus's own formatter."""
    return pm.format_push([{"from": "web", "from_id": SENDER, "body": body, "id": m, "date": ""} for m in mids])


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


class _SdkWorld:
    """A registry + empty transcript for one live SDK session registered without its thread (no loop, no CLI): the
    harness the stranded-mail tests use. _ensure is stubbed to hand back this session, the way the running kernel's
    would for a live one."""

    def _make_backend(self, to=TO):
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
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=self.klog.append)
        self.sess = self._new_session()
        self.be._ensure = lambda sid, on_boot_settled=None: self.be.sessions.get(sid)

    def _new_session(self):
        """A fresh SdkSession seeded from the registry on disk, as a kernel restart seeds it."""
        reg = sb.read_reg(self.be.state_dir, self.to) or {"sid": self.to, "name": "api", "mode": "acceptEdits",
                                                           "alive": True, "cwd": self.cwd, "lastSid": self.to}
        sb.write_reg(self.be.state_dir, self.to, reg)
        s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[self.to] = s
        return s

    def _drop_backend(self):
        if self._cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg

    def copies(self, mid):
        """How many banners queued in the session carry `mid`."""
        return sum(1 for t in self.sess.pending() if ("<!-- romp-msg-id: %s -->" % mid) in t)


class _SlowKernel(BaseHTTPRequestHandler):
    """The kernel's /deliver in shape: read the body, hand the banner to the backend's deliver (which queues it),
    and only THEN answer, after `delays` seconds (one per post, popped in order; none left means at once). A client
    that stopped waiting has closed its end by then; the write fails and is ignored, as the real handler's would."""
    protocol_version = "HTTP/1.1"
    backend = None
    delays = []
    posts = []
    done = None

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            injected = bool(_SlowKernel.backend.deliver(body["id"], body["text"]))
            _SlowKernel.posts.append(sb.postal_mids(body["text"]))
            time.sleep(_SlowKernel.delays.pop(0) if _SlowKernel.delays else 0)
            out = json.dumps({"ok": True, "injected": injected}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            _SlowKernel.done.release()

    def log_message(self, *a):
        pass


class SlowKernelDoesNotRedeliver(_SdkWorld, unittest.TestCase):
    """The loop, end to end: the real bus (_push, _drain, _retry_pending, the real _kernel_post over a real socket, a
    real maildir and ledger) against a kernel that queues each banner on a real SdkBackend and answers too late."""

    def setUp(self):
        self._make_backend(_fresh_sid())
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # not a seam test: let _push actually post
        self.saved = (pm.KERNEL_BASE, pm._push_disabled, pm._log, pm._kernel_sessions_checked, urllib.request.urlopen)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowKernel)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % self.srv.server_address[1]
        pm._push_disabled = lambda: False
        self.logged = []
        pm._log = self.logged.append
        # the listing, stubbed at its one source (local_agents and local_agents_checked both read it)
        pm._kernel_sessions_checked = lambda threads=False: ([{"id": self.to, "name": "api", "state": "idle"}], True)
        real = self.saved[4]
        # the bus's own wait, shortened: whatever timeout the caller passes, the socket gives up after CLIENT_WAIT
        urllib.request.urlopen = lambda req, timeout=None, **k: real(req, timeout=CLIENT_WAIT, **k)
        _SlowKernel.backend, _SlowKernel.posts, _SlowKernel.done = self.be, [], threading.Semaphore(0)
        _SlowKernel.delays = [SLOW_ANSWER]                        # the first post is answered late; later ones at once
        pm.STREAKS.pop(self.to, None)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm.KERNEL_BASE, pm._push_disabled, pm._log, pm._kernel_sessions_checked, urllib.request.urlopen = self.saved
        pm.STREAKS.pop(self.to, None)
        self._drop_backend()

    def _kernel_finished(self, n=1):
        for _ in range(n):
            self.assertTrue(_SlowKernel.done.acquire(timeout=10), "the fake kernel never finished a post")

    def test_a_late_answer_leaves_the_mail_claimed_not_back_in_new_for_a_second_feed(self):
        mid = pm.deliver(self.to, "web", SENDER, "the pod is up; please take the next fire", kind="coordinate")
        self.assertFalse(pm._push(self.to, {"id": self.to, "state": "idle"}), "no answer: not a landed push")
        self._kernel_finished()
        self.assertEqual(self.copies(mid), 1, "the kernel queued the banner although the bus stopped waiting")
        self.assertEqual(pm.read_box(self.to, consume=False), [],
                         "the mail stays claimed in cur/: before the fix it went back to new/, where the next "
                         "drain or retry claimed it again")
        self.assertEqual(pm._drain(self.to)["messages"], [],
                         "the session's Stop-hook drain does not feed it a second time")
        self.assertEqual(_timeline(mid), ["sent", "exec"], "no unexec row: the claim is not rolled back")
        self.assertTrue((pm.MAILROOT / self.to / "cur" / mid).is_file())
        self.assertTrue(any("no answer" in ln and self.to in ln for ln in self.logged),
                        "the log names the missing answer, never 'kernel unreachable': %r" % (self.logged,))

    def test_the_retry_pass_re_posts_the_same_ids_and_the_session_gets_one_copy(self):
        mid = pm.deliver(self.to, "web", SENDER, "the pod is up; please take the next fire", kind="coordinate")
        pm._push(self.to, {"id": self.to, "state": "idle"})
        self._kernel_finished()
        pm._retry_pending()                                   # the bus's 5 s pass
        self.assertEqual(self.copies(mid), 1,
                         "one message, one copy in the session: before the fix the retry claimed it again and the "
                         "kernel queued a second banner (and a third, and ... while the kernel stayed slow)")
        self.assertEqual(_timeline(mid), ["sent", "exec"], "one claim, never rolled back")
        self.assertEqual(_SlowKernel.posts[-1], [mid], "the re-post carries the same message id")
        pm._retry_pending()
        self.assertEqual(len(_SlowKernel.posts), 2, "answered for: nothing in doubt, nothing more is posted")
        self.assertEqual(pm._doubt_read(self.to), [], "the in-doubt record is retired once the kernel answers")
        self.assertEqual(pm.read_box(self.to, consume=False), [])

    def test_mail_that_arrives_while_a_chunk_is_in_doubt_waits_behind_it(self):
        first = pm.deliver(self.to, "web", SENDER, "first", kind="coordinate")
        pm._push(self.to, {"id": self.to, "state": "idle"})
        self._kernel_finished()
        second = pm.deliver(self.to, "web", SENDER, "second", kind="coordinate")
        _SlowKernel.delays = [SLOW_ANSWER]                    # the kernel is still slow: the re-post goes unanswered too
        self.assertFalse(pm._push(self.to, {"id": self.to, "state": "idle"}))
        self._kernel_finished()
        self.assertEqual(_SlowKernel.posts, [[first], [first]],
                         "only the in-doubt chunk is re-posted; the new mail is not claimed on top of it, so the "
                         "banner no longer grows with every retry")
        self.assertEqual([m["id"] for m in pm.read_box(self.to, consume=False)], [second])
        self.assertEqual(self.copies(first), 1, "the repeat was recognised by id and not queued")
        pm._retry_pending()                                   # the kernel answers now: the doubt clears, then the new mail
        self.assertEqual(_SlowKernel.posts[-2:], [[first], [second]])
        self.assertEqual((self.copies(first), self.copies(second)), (1, 1))
        self.assertEqual(pm.read_box(self.to, consume=False), [])


class InDoubtResolution(unittest.TestCase):
    """The retry side of an in-doubt chunk, with the kernel stubbed at _kernel_post: an answer settles it, a missing
    answer or a refused connect keeps holding it, and a message that left cur/ meanwhile is dropped from it."""

    def setUp(self):
        self.SID = _fresh_sid("33333333-4444-5555-6666-")
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = (pm._kernel_post, pm._push_disabled, pm._log)
        pm._push_disabled = lambda: False
        self.logged, self.posted = [], []
        pm._log = self.logged.append
        pm.STREAKS.pop(self.SID, None)
        self.mid = pm.deliver(self.SID, "web", SENDER, "an invented note", kind="coordinate")
        self._answer(pm.NO_ANSWER)
        pm._push(self.SID, {"id": self.SID, "state": "idle"})
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]], "the unanswered chunk is recorded in doubt")

    def tearDown(self):
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm._kernel_post, pm._push_disabled, pm._log = self.saved
        pm.STREAKS.pop(self.SID, None)

    def _answer(self, resp):
        def post(path, body, timeout=2, no_answer=None):
            self.posted.append(body)
            return no_answer if resp is pm.NO_ANSWER else resp
        pm._kernel_post = post

    def test_no_answer_again_keeps_holding_it(self):
        self._answer(pm.NO_ANSWER)
        self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]])
        self.assertEqual(_timeline(self.mid), ["sent", "exec"])

    def test_a_refused_connect_on_the_re_post_keeps_holding_it(self):
        # the first post may have landed; a kernel that is down now (mid-restart) says nothing about it
        self._answer(None)
        self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]])
        self.assertEqual(pm.read_box(self.SID, consume=False), [], "never put back while the outcome is unknown")
        self.assertEqual(_timeline(self.mid), ["sent", "exec"])

    def test_an_answer_that_it_was_taken_retires_it(self):
        self._answer({"ok": True, "injected": True})
        self.assertTrue(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual(pm._doubt_read(self.SID), [])
        self.assertTrue((pm.MAILROOT / self.SID / "cur" / self.mid).is_file(), "consumed: it stays in cur/")
        self.assertEqual(_timeline(self.mid), ["sent", "exec"])

    def test_an_answer_that_it_was_not_taken_puts_it_back_for_the_drain(self):
        self._answer({"ok": True, "injected": False})
        self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual(pm._doubt_read(self.SID), [])
        self.assertEqual([m["id"] for m in pm.read_box(self.SID, consume=False)], [self.mid],
                         "an ANSWERED not-taken is the old roll-back: back in new/ under its own id")
        self.assertEqual(_timeline(self.mid), ["sent", "exec", "unexec"])

    def test_a_message_restored_meanwhile_leaves_the_record(self):
        # the kernel handed it back (a teardown stranded the banner): restore() moves it to new/ and the doubt drops it
        self.assertEqual(pm.restore(self.SID, self.mid), pm.RESTORED)
        self.assertEqual(pm._doubt_read(self.SID), [])

    def test_a_record_that_cannot_be_written_falls_back_to_the_put_back_and_says_so(self):
        # nothing would ever re-post a chunk with no record, so it goes back to new/ (a possible second copy, which the
        # kernel's repeat check mostly absorbs, beats mail stranded in cur/ for good)
        mid = pm.deliver(self.SID, "web", SENDER, "another invented note", kind="coordinate")
        saved = pm._doubt_add
        pm._doubt_add = lambda sid, mids: False
        try:
            self._answer({"ok": True, "injected": True})      # the old chunk settles first
            self.assertTrue(pm._resolve_in_doubt(self.SID, {"id": self.SID})[0])
            self._answer(pm.NO_ANSWER)
            self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        finally:
            pm._doubt_add = saved
        self.assertEqual([m["id"] for m in pm.read_box(self.SID, consume=False)], [mid])
        self.assertEqual(_timeline(mid), ["sent", "exec", "unexec"])
        self.assertTrue(any("could not be written" in ln for ln in self.logged), self.logged)

    def test_the_record_survives_a_bus_restart(self):
        # the record is a file: a fresh bus process (code-change re-exec) reads the same chunk
        self.assertTrue((pm.MAILDOUBT / self.SID).is_file())
        self.assertEqual((pm.MAILDOUBT / self.SID).read_text().split(), [self.mid])


class _PrivateMaildir:
    """A maildir of its own per test (the module's is shared, and the orphan sweep and the retry pass walk every box
    in it): the mail, pending, held and in-doubt roots moved under a fresh temp dir; the ledger stays shared (its
    rows are keyed by message ids no other test mints)."""
    _ROOTS = ("MAILROOT", "MAILPENDING", "MAILHELD", "MAILDOUBT", "WARNED")

    def _private_maildir(self):
        self._roots = {k: getattr(pm, k) for k in self._ROOTS}
        base = tempfile.mkdtemp()
        for k in self._ROOTS:
            setattr(pm, k, pm.Path(base) / k.lower())

    def _shared_maildir(self):
        for k, v in self._roots.items():
            setattr(pm, k, v)


class InDoubtRecipientGone(_PrivateMaildir, unittest.TestCase):
    """A chunk held in doubt that nothing will re-post is RELEASED back to new/ under its own ids, loudly (review of
    2026-09-29). Before, a recipient that never came back live kept its claim in cur/ for good, read in the sender's
    receipts; the orphan bounce and the stuck-mail warning read new/ only, so the sender was never told; and every
    retry pass asked the kernel for a session list for ever. The event keyed on is the kernel's ANSWERED listing
    without the recipient (and no durable record holding it alive); where no event can come (the listing does not
    answer, or a blink the durable record vouches for persists) the bound is DOUBT_GONE_GRACE."""

    def setUp(self):
        self._private_maildir()
        self.SID = _fresh_sid("44444444-5555-6666-7777-")
        self.sender = _fresh_sid("11111111-2222-3333-4444-")
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = (pm._kernel_post, pm._push_disabled, pm._log, pm._kernel_sessions_checked, pm.ORPHAN_GRACE)
        pm._push_disabled = lambda: False
        self.logged, self.posted, self.fetches = [], [], []
        pm._log = self.logged.append
        # the sender is live; the recipient is not listed
        self.rows, self.answered = [{"id": self.sender, "name": "web", "state": "idle"}], True
        pm._kernel_sessions_checked = lambda threads=False: (self.fetches.append(threads),
                                                             (list(self.rows), self.answered))[1]
        self.resp = pm.NO_ANSWER

        def post(path, body, timeout=2, no_answer=None):
            self.posted.append(body)
            return no_answer if self.resp is pm.NO_ANSWER else self.resp
        pm._kernel_post = post
        pm.STREAKS.pop(self.SID, None)
        self.mid = pm.deliver(self.SID, "web", self.sender, "an invented note about the build", kind="coordinate")
        pm._push(self.SID, {"id": self.SID, "state": "idle"})
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]], "the unanswered chunk is held in doubt")
        self.posted.clear()
        self.resp = {"ok": True, "injected": True}            # from here on every post is answered taken
        self.reg = pm.STATE.parent / "sdk" / (self.SID + ".json")

    def tearDown(self):
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm._kernel_post, pm._push_disabled, pm._log, pm._kernel_sessions_checked, pm.ORPHAN_GRACE = self.saved
        for d in (getattr(pm, "_DOUBT_GONE_SINCE", {}), pm.HEARTBEATS, pm.STREAKS):
            d.pop(self.SID, None)
        getattr(pm, "_REMOTE_BEATS", set()).discard(self.SID)
        pm.STREAKS.pop(self.sender, None)
        if self.reg.exists():
            self.reg.unlink()
        self._shared_maildir()

    def _in_new(self):
        return [m["id"] for m in pm.read_box(self.SID, consume=False)]

    def _posted_to(self, sid):
        return [b for b in self.posted if b.get("id") == sid]

    def _gone_since(self):
        return getattr(pm, "_DOUBT_GONE_SINCE", {})

    def _run_out_the_bound(self):
        """Move the moment the recipient was first found unlisted back past DOUBT_GONE_GRACE."""
        if self.SID in self._gone_since():
            self._gone_since()[self.SID] -= pm.DOUBT_GONE_GRACE + 1

    def test_a_recipient_the_kernel_no_longer_lists_is_released_the_sender_told_and_the_asking_stops(self):
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid],
                         "back in new/ under its own id: before the fix it stayed claimed in cur/ for good")
        self.assertEqual(pm._doubt_read(self.SID), [], "the record is gone")
        self.assertEqual(_timeline(self.mid), ["sent", "exec", "unexec"],
                         "the exec row is retracted, so the sender's receipt reads pending, not read")
        self.assertTrue(any("released" in ln and self.SID in ln and "no longer carries" in ln for ln in self.logged),
                        "said in the log, with the reason: %r" % (self.logged,))
        self.assertEqual(self._posted_to(self.SID), [], "nothing posted to a recipient that is not live")
        # the orphan sweep reads new/, so the sender hears; its note is pushed on a thread, joined here
        pm.ORPHAN_GRACE = 0
        before = set(threading.enumerate())
        pm._sweep_orphans()
        for t in set(threading.enumerate()) - before:
            t.join(10)
        self.assertEqual(_timeline(self.mid)[-1], "bounced", "the orphan sweep reached the mail")
        self.assertTrue(any("UNDELIVERED" in b.get("text", "") for b in self._posted_to(self.sender)),
                        "the sender was woken with the bounce: %r" % (self.posted,))
        self.fetches.clear()
        for _ in range(49):
            pm._retry_pending()
        self.assertEqual(self.fetches, [],
                         "49 more passes ask the kernel for nothing: before the fix every pass fetched the session "
                         "list for the held chunk, for ever")

    def test_a_listing_blink_the_durable_record_vouches_for_waits_then_the_bound_releases_it(self):
        self.reg.parent.mkdir(parents=True, exist_ok=True)
        self.reg.write_text(json.dumps({"sid": self.SID, "alive": True}))
        pm._retry_pending()
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]],
                         "an answered listing without a session whose record reads alive is a blink: it waits")
        self.assertEqual(self._in_new(), [])
        self._run_out_the_bound()
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid])
        self.assertEqual(pm._doubt_read(self.SID), [])
        self.assertTrue(any("released" in ln and "durable record reads alive" in ln for ln in self.logged), self.logged)

    def test_an_unanswered_listing_waits_then_the_bound_releases_it(self):
        self.rows, self.answered = [], False                   # the kernel does not answer the listing
        pm._retry_pending()
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]], "no answer is no word on the recipient: it waits")
        self._run_out_the_bound()
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid])
        self.assertTrue(any("released" in ln and "did not answer" in ln for ln in self.logged), self.logged)

    def test_seen_live_again_the_clock_starts_over(self):
        self.answered = False
        pm._retry_pending()
        self.assertIn(self.SID, self._gone_since(), "unlisted, unanswered: the wait starts")
        self.rows = self.rows + [{"id": self.SID, "name": "api", "state": "working"}]
        self.answered = True
        self.resp = pm.NO_ANSWER                               # listed again, and still no answer to the re-post
        pm._retry_pending()
        self.assertEqual(len(self._posted_to(self.SID)), 1, "a listed recipient's chunk is re-posted")
        self.assertNotIn(self.SID, self._gone_since(), "listed again: the wait for a gone recipient starts over")
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]])

    def test_a_remote_peer_whose_heartbeat_stands_is_re_posted_not_released(self):
        pm.HEARTBEATS[self.SID] = ("api", time.time())         # a remote peer: its wake rides the kernel's wake-router
        pm._retry_pending()
        self.assertEqual([b["id"] for b in self.posted], [self.SID],
                         "re-posted like a local session's: before, only the local listing counted, and a remote "
                         "peer's chunk was never re-posted")
        self.assertEqual(pm._doubt_read(self.SID), [], "answered taken: retired")
        self.assertEqual(self._in_new(), [])

    def _beat(self, age=0.0):
        """`self.SID` heartbeats through the real handler (the listing the harness serves decides local or remote),
        and the beat is then aged by `age` seconds: its beats have stopped since."""
        local = pm._record_heartbeat(self.SID, "api")
        if self.SID in pm.HEARTBEATS:
            pm.HEARTBEATS[self.SID] = ("api", time.time() - age)
        return local

    def _released_lines(self):
        return [ln for ln in self.logged if "released" in ln and self.SID in ln]

    def test_a_remote_peer_whose_heartbeat_lapsed_is_released(self):
        self.assertFalse(self._beat(age=pm.HEARTBEAT_TTL + 5), "not in the kernel's list, no local record: remote")
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid])
        self.assertTrue(any("released" in ln and "heartbeat has lapsed" in ln for ln in self.logged), self.logged)

    def test_a_remote_peer_that_beat_while_the_list_did_not_answer_is_still_a_remote_peer(self):
        # no local registry record for it: the kernel owns no such session, whatever the listing said
        self.answered = False
        self.assertFalse(self._beat(age=pm.HEARTBEAT_TTL + 5))
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid])
        self.assertTrue(any("heartbeat has lapsed" in ln for ln in self._released_lines()), self.logged)

    def _local_record(self):
        self.reg.parent.mkdir(parents=True, exist_ok=True)
        self.reg.write_text(json.dumps({"sid": self.SID, "alive": True}))

    def test_a_local_sessions_beat_filed_while_the_list_was_slow_does_not_make_it_a_remote_peer(self):
        # review of round two: a local session's first beat is filed as presence whenever the kernel's session list
        # takes over 6 s, and the release read ANY id ever filed there as a remote peer. Under a slow kernel its held
        # chunk was released on the first unanswered pass, skipping DOUBT_GONE_GRACE, the log naming a heartbeat
        self._local_record()
        self.answered = False                                  # the list is too slow to answer
        self.assertFalse(self._beat(age=pm.HEARTBEAT_TTL + 5), "an unanswered listing never says local")
        pm._retry_pending()
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]],
                         "a local session unlisted by a list that did not answer waits the grace: before the fix it "
                         "was released at once as a remote peer whose heartbeat had lapsed")
        self.assertEqual(self._in_new(), [])
        self._run_out_the_bound()
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid], "past the grace it is released")
        lines = self._released_lines()
        self.assertTrue(lines and all("did not answer" in ln and "heartbeat" not in ln for ln in lines),
                        "and the log names the true reason: %r" % (self.logged,))

    def test_a_local_sessions_beat_does_not_turn_a_listing_blink_into_a_release(self):
        # the answered list drops the session for a pass while its registry record reads alive: a blink, which waits
        self._local_record()
        self.answered = False
        self._beat(age=pm.HEARTBEAT_TTL + 5)
        self.answered = True                                   # the list answers now, without it
        pm._retry_pending()
        self.assertEqual(pm._doubt_read(self.SID), [[self.mid]],
                         "its durable record reads alive: before the fix the stale beat released it as a remote peer")
        self.assertEqual(self._released_lines(), [])

    def test_the_release_never_reads_presence_as_a_remote_peer(self):
        # the decision itself, with the beat still in the table: a local session (its registry record on disk) is
        # judged by the local rules whatever presence says; only a beat marked remote reads as a remote peer
        self._local_record()
        pm.HEARTBEATS[self.SID] = ("api", time.time() - pm.HEARTBEAT_TTL - 5)
        self.assertEqual(pm._doubt_gone_why(self.SID, False), "", "unanswered: wait the grace")
        self.assertEqual(pm._doubt_gone_why(self.SID, True), "", "answered without it, record alive: a blink")
        self.reg.unlink()
        self.answered = True
        self.assertFalse(self._beat(age=pm.HEARTBEAT_TTL + 5), "no local record now: a remote peer's beat")
        self.assertIn("heartbeat has lapsed", pm._doubt_gone_why(self.SID, True))

    def test_a_lapsed_heartbeat_is_dropped_and_a_released_peer_forgotten(self):
        other = _fresh_sid("55555555-6666-7777-8888-")
        pm.HEARTBEATS[other] = ("web", time.time() - pm.HEARTBEAT_TTL - 5)
        self.addCleanup(pm.HEARTBEATS.pop, other, None)
        self._beat(age=pm.HEARTBEAT_TTL + 5)                   # a remote peer holding a chunk in doubt
        pm._retry_pending()
        self.assertNotIn(other, pm.HEARTBEATS, "a lapsed beat leaves the table: before the fix nothing removed one")
        self.assertEqual(self._in_new(), [self.mid], "the peer's chunk is released, with its reason")
        self.assertTrue(any("heartbeat has lapsed" in ln for ln in self._released_lines()), self.logged)
        pm._retry_pending()
        self.assertNotIn(self.SID, pm.HEARTBEATS)
        self.assertNotIn(self.SID, getattr(pm, "_REMOTE_BEATS", set()),
                         "nothing held for it any more: the peer is forgotten too")

    def test_a_hold_a_sends_own_push_settles_clears_the_gone_clock(self):
        # review of round two: the clock was cleared only by a release and by the retry pass seeing the recipient
        # listed, so a hold settled by a send's or a wake's own push kept its old start, and the NEXT hold was released
        # on its first unlisted pass, the grace long spent
        self.answered = False
        pm._retry_pending()                                    # unlisted, unanswered: the wait starts
        self.assertIn(self.SID, self._gone_since())
        self._run_out_the_bound()                              # ... and runs past the grace
        self.assertTrue(pm._push(self.SID, {"id": self.SID, "state": "idle"}),
                        "the recipient is live to a send's own push, which settles the hold (answered taken)")
        self.assertEqual(pm._doubt_read(self.SID), [])
        self.assertNotIn(self.SID, self._gone_since(), "settled: the clock goes with the hold")
        self.resp = pm.NO_ANSWER
        second = pm.deliver(self.SID, "web", self.sender, "a second invented note", kind="coordinate")
        pm._push(self.SID, {"id": self.SID, "state": "idle"})
        self.assertEqual(pm._doubt_read(self.SID), [[second]], "a new hold")
        pm._retry_pending()                                    # its first unlisted pass
        self.assertEqual(pm._doubt_read(self.SID), [[second]],
                         "the new hold waits its own grace: before the fix it was released at once on the old clock")
        self.assertEqual(self._in_new(), [])

    def test_a_hold_the_kernel_hands_back_clears_the_gone_clock(self):
        self.answered = False
        pm._retry_pending()
        self.assertIn(self.SID, self._gone_since())
        self.assertEqual(pm.restore(self.SID, self.mid), pm.RESTORED)   # a teardown stranded the banner: handed back
        self.assertEqual(pm._doubt_read(self.SID), [])
        self.assertNotIn(self.SID, self._gone_since(), "the hold is settled, whatever path settled it")

    def test_with_the_push_switched_off_the_chunk_goes_back_for_the_drain(self):
        # romp-postal-nopush: _push returns before it looks at a chunk in doubt, so nothing would ever re-post it, and
        # the Stop-hook drain reads new/ only; before the fix the claim sat in cur/ out of every road's reach
        self.rows = self.rows + [{"id": self.SID, "name": "api", "state": "idle"}]   # live, and ready
        pm._push_disabled = lambda: True
        pm._retry_pending()
        self.assertEqual(self._in_new(), [self.mid], "back in new/, where the drain reads")
        self.assertEqual(pm._doubt_read(self.SID), [])
        self.assertEqual(_timeline(self.mid), ["sent", "exec", "unexec"])
        self.assertEqual(self.posted, [], "no push while it is switched off")
        self.assertTrue(any("released" in ln and "switched off" in ln for ln in self.logged), self.logged)
        self.assertEqual(pm._drain(self.SID)["messages"][0]["id"], self.mid, "the drain gets it")


class KernelPostTellsUnsentFromUnanswered(unittest.TestCase):
    """_kernel_post: a request that never left (urllib wraps a connect or send failure in URLError) is None, as
    before; a request sent in full and then not answered is the caller's `no_answer`, and None for every caller
    that does not ask (the working-note, the notice door, the redial keep their contract)."""

    def setUp(self):
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self.saved
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam

    def _raise(self, exc):
        def urlopen(req, timeout=None, **k):
            raise exc
        urllib.request.urlopen = urlopen

    def test_a_refused_connect_is_none_even_when_the_caller_asks(self):
        self._raise(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        self.assertIsNone(pm._kernel_post("/deliver", {"id": TO, "text": "x"}, timeout=12, no_answer=pm.NO_ANSWER))

    def test_a_read_timeout_is_no_answer_for_a_caller_that_asks_and_none_for_the_rest(self):
        self._raise(TimeoutError("timed out"))
        self.assertIs(pm._kernel_post("/deliver", {"id": TO, "text": "x"}, timeout=12, no_answer=pm.NO_ANSWER),
                      pm.NO_ANSWER)
        self.assertIsNone(pm._kernel_post("/working", {"id": TO, "text": ""}))

    def test_a_reset_after_the_send_is_no_answer(self):
        import http.client
        self._raise(http.client.RemoteDisconnected("Remote end closed connection without response"))
        self.assertIs(pm._kernel_post("/deliver", {"id": TO, "text": "x"}, no_answer=pm.NO_ANSWER), pm.NO_ANSWER)

    def test_no_answer_is_falsy_so_a_truth_test_never_reads_it_as_taken(self):
        self.assertFalse(pm.NO_ANSWER)


class KernelDeliverIsSafeToRepeat(_SdkWorld, unittest.TestCase):
    """SdkBackend.deliver: a banner whose message ids the session has all taken is answered True and not queued again."""

    def setUp(self):
        self._make_backend()

    def tearDown(self):
        self._drop_backend()

    def test_the_same_banner_twice_is_queued_once(self):
        b = _banner(MID1, MID2)
        self.assertTrue(self.be.deliver(TO, b))
        self.assertTrue(self.be.deliver(TO, b), "a repeat is answered taken, so the bus retires its claim")
        self.assertEqual(self.sess.pending(), [b], "one copy: before the fix every repeat queued another banner")
        self.assertTrue(any("taken already" in ln and MID1 in ln for ln in self.klog), self.klog)

    def test_a_repeat_is_recognised_across_a_kernel_restart(self):
        b = _banner(MID1)
        self.assertTrue(self.be.deliver(TO, b))
        self.sess = self._new_session()                       # a restarted kernel seeds the session from the registry
        self.assertEqual(self.sess.pending(), [b], "the queue mirror carried the banner")
        self.assertTrue(self.be.deliver(TO, b))
        self.assertEqual(self.sess.pending(), [b], "and the taken ids came with it")

    def test_racing_posts_of_one_banner_queue_it_once(self):
        b = _banner(MID1)
        start = threading.Barrier(8)

        def post():
            start.wait()
            self.be.deliver(TO, b)
        ts = [threading.Thread(target=post) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(self.copies(MID1), 1)

    def test_a_banner_with_a_new_id_beside_a_taken_one_is_queued_whole_and_said(self):
        self.be.deliver(TO, _banner(MID1))
        both = _banner(MID1, MID2)
        self.assertTrue(self.be.deliver(TO, both))
        self.assertEqual(self.sess.pending(), [_banner(MID1), both],
                         "the banner cannot be split here, and a message delivered twice beats one never delivered")
        self.assertTrue(any("1 of 2" in ln for ln in self.klog), self.klog)

    def test_a_text_with_no_message_ids_is_queued_every_time(self):
        self.be.deliver(TO, "plain wake text")
        self.be.deliver(TO, "plain wake text")
        self.assertEqual(self.sess.pending(), ["plain wake text", "plain wake text"])

    def test_the_taken_list_is_bounded(self):
        for i in range(sb.POSTAL_TAKEN_KEEP + 20):
            self.be.deliver(TO, _banner("1700%06d.000000.TESTHOST" % i))
        reg = sb.read_reg(self.be.state_dir, TO) or {}
        self.assertEqual(len(reg.get("postalTaken") or []), sb.POSTAL_TAKEN_KEEP)
        self.assertEqual(reg["postalTaken"][-1], "1700%06d.000000.TESTHOST" % (sb.POSTAL_TAKEN_KEEP + 19),
                         "the newest ids are kept")

    # The bus bounds a chunk by its BYTES (_PUSH_MAX_BYTES, 900 KiB), never by how many messages it carries, so a
    # recipient back from a long absence gets its whole parked box in one banner: about 380 messages of 2 KB each fit.
    # A taken list cut at POSTAL_TAKEN_KEEP inside that banner kept only its last 256 ids, so the bus's re-post of the
    # same chunk (no answer within its wait, a slow kernel) met ids it did not know, and the banner was queued whole a
    # second time: every message in the box delivered twice (2026-10-03).
    _BIG = sb.POSTAL_TAKEN_KEEP + 44
    _BODY = ("a synthetic status line for the test, about the size of a peer's note. " * 30)[:2000]

    def _big_box(self, n=_BIG, first=0):
        """A banner of `n` messages that the bus's own chunker sends as ONE chunk -> (ids, banner)."""
        mids = ["1701%06d.000000.TESTHOST" % i for i in range(first, first + n)]
        msgs = [{"from": "web", "from_id": SENDER, "body": self._BODY, "id": m, "date": ""} for m in mids]
        chunks, oversize = pm._push_chunks(TO, msgs)
        self.assertEqual((len(chunks), len(oversize)), (1, 0), "the bus sends this box as one banner")
        return mids, pm.format_push(msgs)

    def test_a_banner_carrying_more_ids_than_the_cap_is_still_a_repeat(self):
        mids, b = self._big_box()
        self.assertTrue(self.be.deliver(TO, b))
        self.assertTrue(self.be.deliver(TO, b), "the bus re-posts the chunk it got no answer for")
        self.assertEqual(self.copies(mids[0]), 1, "one copy: before the fix the cap cut the banner's own ids and the "
                                                  "re-post was queued whole again")
        self.assertFalse(any("queued whole" in ln for ln in self.klog), self.klog)

    def test_a_banner_carrying_more_ids_than_the_cap_is_a_repeat_across_a_kernel_restart(self):
        mids, b = self._big_box()
        self.assertTrue(self.be.deliver(TO, b))
        self.sess = self._new_session()                       # the restarted kernel seeds from the registry
        self.assertTrue(self.be.deliver(TO, b))
        self.assertEqual(self.copies(mids[0]), 1, "the registry carried every id of the banner, and the restart "
                                                  "loaded them all (before, it loaded the last 256)")

    def test_a_banner_that_carries_an_id_taken_long_ago_keeps_that_id_too(self):
        # an id already on the list sat at its OLD end, where the trim after the banner's new ids cut it first; the
        # re-post of that banner then met one unknown id and was queued whole again
        old = "1700000000.000000.TESTHOST"
        self.be.deliver(TO, _banner(old))
        for i in range(1, sb.POSTAL_TAKEN_KEEP):
            self.be.deliver(TO, _banner("1700%06d.000000.TESTHOST" % i))
        mixed = _banner(old, *["1702%06d.000000.TESTHOST" % i for i in range(10)])
        self.assertTrue(self.be.deliver(TO, mixed))           # queued whole and said: one id was taken already
        self.assertTrue(self.be.deliver(TO, mixed), "the bus re-posts it")
        self.assertEqual(sum(1 for t in self.sess.pending() if t == mixed), 1)

    def test_after_a_big_banner_the_list_falls_back_to_the_cap(self):
        self.be.deliver(TO, self._big_box()[1])
        self.assertEqual(len(self.sess._postal_taken), self._BIG, "the banner just taken is kept whole")
        for i in range(3):
            self.be.deliver(TO, _banner("1703%06d.000000.TESTHOST" % i))
        reg = sb.read_reg(self.be.state_dir, TO) or {}
        self.assertEqual(len(reg.get("postalTaken") or []), sb.POSTAL_TAKEN_KEEP, "and the bound holds once the "
                                                                                  "next banner is taken")
        self.assertEqual(reg["postalTaken"][-1], "1703000002.000000.TESTHOST")

    def _strand(self, b):
        """`b` fed to a client that a teardown then abandoned: the loop top's reconcile finds it stranded."""
        self.sess._pending.clear()
        self.sess.inflight = 1
        self.sess._inflight_texts.append(b)
        self.sess._reconcile_stranded()

    def test_a_re_post_that_lands_while_the_bus_takes_the_mail_back_is_queued(self):
        # the bus's put-back wakes the session and re-posts at once: here the re-post lands INSIDE the bus's answer,
        # before the kernel's handback returns. Before the fix the ids were forgotten only after the bus answered, so
        # that re-post read them as taken, was answered taken and never queued: the mail was lost
        b = _banner(MID1, MID2)
        self.be.deliver(TO, b)
        answers = []

        def restore_and_repost(sid, mids):
            answers.append(self.be.deliver(TO, b))
            return set(mids)
        self.be.postal_restore = restore_and_repost
        self._strand(b)
        self.assertEqual(answers, [True])
        self.assertEqual(self.sess.pending(), [b], "the re-post is queued: new mail, not a repeat")
        self.assertTrue(self.be.deliver(TO, b))
        self.assertEqual(self.copies(MID1), 1, "and its ids are taken again, so a further repeat is not")

    def test_a_banner_the_bus_does_not_take_back_is_re_headed_with_its_ids_still_taken(self):
        # the ids are forgotten before the bus is asked; a bus that gives no answer leaves the banner here, re-headed,
        # and its ids are taken again in the same step, so the bus's own re-post of them is still a repeat
        b = _banner(MID1)
        self.be.deliver(TO, b)
        self.be.postal_restore = lambda sid, mids: None
        self._strand(b)
        self.assertEqual(self.sess.pending(), [b], "re-headed")
        self.assertTrue(self.be.deliver(TO, b))
        self.assertEqual(self.sess.pending(), [b], "one copy")
        self.assertIn(MID1, (sb.read_reg(self.be.state_dir, TO) or {}).get("postalTaken") or [])

    def _repost_then(self, b, outcome):
        """A bus hook that puts the mail back and re-posts `b` at once (the re-post lands while the kernel still holds
        the stranded banner), then gives the kernel `outcome`: an exception to raise, or an answer to return."""
        def hook(sid, mids):
            self.assertTrue(self.be.deliver(TO, b))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return hook

    def test_a_bus_that_re_posts_and_then_answers_too_late_leaves_one_copy(self):
        # review of round two: the real hook (_bus_restore_mail) waits 5 s and RAISES on a read timeout. A bus that had
        # already put the mail back and re-posted it by then had its re-post queued as new mail (the ids forgotten),
        # and the kernel, reading the raise as "not taken back", re-headed the banner too: two copies
        b = _banner(MID1, MID2)
        self.be.deliver(TO, b)
        self.be.postal_restore = self._repost_then(b, TimeoutError("timed out"))
        self._strand(b)
        self.assertEqual((self.copies(MID1), self.copies(MID2)), (1, 1),
                         "the bus's re-post is the one copy: before the fix the banner was re-headed beside it")
        self.assertTrue(self.be.deliver(TO, b), "a later repeat is answered taken")
        self.assertEqual(self.copies(MID1), 1)
        self.assertTrue(any("re-post" in ln and MID1 in ln and "not re-headed" in ln for ln in self.klog), self.klog)

    def test_a_bus_that_re_posts_and_then_gives_no_answer_leaves_one_copy(self):
        b = _banner(MID1)
        self.be.deliver(TO, b)
        self.be.postal_restore = self._repost_then(b, None)
        self._strand(b)
        self.assertEqual(self.copies(MID1), 1)

    def test_a_bus_that_answers_too_late_and_re_posts_after_leaves_one_copy(self):
        # the other order: the kernel re-heads first, taking the ids again, so the late re-post is a repeat
        b = _banner(MID1)
        self.be.deliver(TO, b)

        def hook(sid, mids):
            raise TimeoutError("timed out")
        self.be.postal_restore = hook
        self._strand(b)
        self.assertEqual(self.copies(MID1), 1, "re-headed")
        self.assertTrue(self.be.deliver(TO, b), "the bus's late re-post")
        self.assertEqual(self.copies(MID1), 1, "answered taken, not queued")

    def test_a_re_post_carrying_part_of_a_banner_still_re_heads_the_rest(self):
        # a re-post that carries only some of a stranded banner's ids cannot stand for the rest: the banner is
        # re-headed whole (it cannot be split), a message twice before one never
        b = _banner(MID1, MID2)
        self.be.deliver(TO, b)
        self.be.postal_restore = self._repost_then(_banner(MID1), TimeoutError("timed out"))
        self._strand(b)
        self.assertEqual(self.copies(MID2), 1, "MID2 rides the re-headed banner: never lost")

    def test_a_banner_the_bus_takes_back_leaves_nothing_in_hand(self):
        # a take-back that succeeds settles the banner: a later strand of a banner with the same ids starts clean
        b = _banner(MID1)
        self.be.deliver(TO, b)
        self.be.postal_restore = self._repost_then(b, {MID1})
        self._strand(b)
        self.assertEqual(self.copies(MID1), 1)
        self.assertEqual(getattr(self.sess, "_postal_inhand", {}), {}, "nothing left marked in hand")

    def test_a_kernel_death_between_the_writes_never_leaves_an_id_taken_without_its_banner(self):
        # the first cut wrote the taken ids and the queue in two registry writes: a kernel that died between them came
        # back with the ids taken and the banner gone, answered the bus's re-post "taken", and the bus retired the mail
        class _KernelDied(BaseException):
            pass
        b = _banner(MID1)

        def die(*a, **k):
            raise _KernelDied()
        self.sess._persist_queue = die                        # the queue's registry write never happens
        with self.assertRaises(_KernelDied):
            self.be.deliver(TO, b)
        self.sess = self._new_session()                       # the restarted kernel seeds from the registry
        self.assertTrue(self.be.deliver(TO, b), "the bus re-posts the chunk it got no answer for")
        self.assertEqual(self.copies(MID1), 1, "queued: before the fix the ids were on disk and the banner was not, "
                                               "so the re-post was answered taken and queued nowhere")

    def test_ids_handed_back_to_the_bus_are_forgotten_so_their_re_delivery_is_queued(self):
        b = _banner(MID1, MID2)
        self.be.deliver(TO, b)
        self.sess._pending.clear()                            # fed to the CLI...
        self.be.postal_restore = lambda sid, mids: set(mids)  # ...then stranded by a teardown and handed back
        self.sess.inflight = 1
        self.sess._inflight_texts.append(b)
        self.sess._reconcile_stranded()
        self.assertTrue(self.be.deliver(TO, b), "the bus re-delivers the mail it was handed back")
        self.assertEqual(self.sess.pending(), [b], "queued again: a forgotten id is not a repeat")


class ConcurrentPushesToOneRecipient(_SdkWorld, unittest.TestCase):
    """Two bus pushes to ONE recipient never interleave (review of 2026-10-03).

    The kernel keeps the banner it took last whole in its taken list, past the cap if need be, and falls back to the
    cap when it takes the next one. A push runs on a thread per send (and per wake, and on the retry pass), so before
    this fix a second push could claim and post new mail while the first was still waiting on an answer for a big
    banner, before it had recorded that banner in doubt: the kernel took the small banner, trimmed the list back to
    the cap and cut the big banner's oldest ids, and the retry pass's re-post of the big banner then read as new mail
    and was queued whole a second time. Each recipient's whole push (resolve the chunks in doubt, claim, post, record)
    now holds one lock per recipient, so a second push starts only after the first has recorded its chunk in doubt,
    and its first act is to settle that chunk.

    The interleaving is pinned with events, never sleeps: the fake kernel queues the big banner and then holds its
    answer until the second push has either posted (before the fix) or is waiting on the recipient's lock (after)."""

    _BIG = sb.POSTAL_TAKEN_KEEP + 44
    _BODY = ("a synthetic status line about the size of a short peer note. " * 4)[:240]

    def setUp(self):
        self._make_backend(_fresh_sid())
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # not a seam test: let _push actually post
        self.saved = (pm._push_disabled, pm._log, pm._kernel_post, getattr(pm, "_push_lock", None))
        pm._push_disabled = lambda: False
        self.logged = []
        pm._log = self.logged.append
        self.posts = []
        self.big_taken = threading.Event()      # the kernel has queued the big banner, its answer not yet sent
        self.progress = threading.Event()       # the second push posted, or is waiting on the recipient's lock
        self.held = [False]
        self.second = None                      # the second push's thread
        pm._kernel_post = self._fake_post
        real_lock = self.saved[3]
        if real_lock is not None:
            def watched(sid, _real=real_lock):
                if threading.current_thread() is self.second:
                    self.progress.set()         # entering the lock: it blocks there until the first push is done
                return _real(sid)
            pm._push_lock = watched
        pm.STREAKS.pop(self.to, None)

    def tearDown(self):
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm._push_disabled, pm._log, pm._kernel_post = self.saved[:3]
        if self.saved[3] is not None:
            pm._push_lock = self.saved[3]
        pm.STREAKS.pop(self.to, None)
        self._drop_backend()

    def _fake_post(self, path, body, timeout=2, no_answer=None):
        """The kernel's /deliver: queue the banner on the real backend, then answer. The FIRST post (the big banner)
        is queued and its answer withheld until the second push has made its move; the bus's wait then runs out
        (`no_answer`), as it does against a slow kernel that has already queued the banner."""
        self.assertEqual(path, "/deliver")
        self.posts.append(sb.postal_mids(body["text"]))
        injected = bool(self.be.deliver(body["id"], body["text"]))
        if not self.held[0]:
            self.held[0] = True
            self.big_taken.set()
            if not self.progress.wait(10):
                raise AssertionError("the second push neither posted nor waited on the recipient's lock")
            return no_answer
        self.progress.set()                     # a post by the second push (before the fix, while the first waits)
        return {"ok": True, "injected": injected}

    def _copies(self, mids):
        """How many queued banners carry each id in `mids`, as a set of counts."""
        q = self.sess.pending()
        return {sum(1 for t in q if ("<!-- romp-msg-id: %s -->" % m) in t) for m in mids}

    def test_a_push_that_overlaps_a_big_banner_in_doubt_cannot_get_it_queued_twice(self):
        agent = {"id": self.to, "state": "idle"}
        big = [pm.deliver(self.to, "web", SENDER, "%s %d" % (self._BODY, i), kind="coordinate")
               for i in range(self._BIG)]
        first = threading.Thread(target=pm._push, args=(self.to, agent), daemon=True)
        first.start()
        self.assertTrue(self.big_taken.wait(10), "the first push never posted the big banner")
        self.assertEqual([sorted(p) for p in self.posts], [sorted(big)], "the whole box went over as one banner, more ids than the cap")
        late = pm.deliver(self.to, "web", SENDER, "a note sent while the big banner awaits its answer",
                          kind="coordinate")
        self.second = threading.Thread(target=pm._push, args=(self.to, agent), daemon=True)
        self.second.start()
        first.join(10)
        self.second.join(10)
        self.assertFalse(first.is_alive() or self.second.is_alive(), "a push never finished")
        pm._push(self.to, agent)                # the retry pass: re-posts whatever is still in doubt
        self.assertEqual(self._copies(big), {1},
                         "every message of the big banner is queued once: before the fix the second push's banner "
                         "trimmed the taken list back to the cap before the big banner's re-post arrived, and the "
                         "re-post was queued whole a second time (posts: %r)" % ([len(p) for p in self.posts],))
        self.assertEqual(self._copies([late]), {1})
        self.assertEqual(pm._doubt_read(self.to), [], "nothing is left in doubt")
        self.assertEqual(pm.read_box(self.to, consume=False), [])
        self.assertEqual([len(p) for p in self.posts], [self._BIG, self._BIG, 1],
                         "the big banner, its re-post settling the doubt, and only then the late note")
        self.assertEqual(getattr(pm, "_PUSH_LOCKS", {}), {}, "no per-recipient lock outlives its pushes")


if __name__ == "__main__":
    unittest.main()
