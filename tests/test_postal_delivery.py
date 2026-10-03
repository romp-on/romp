#!/usr/bin/env python3
"""The bus's live-push (auto-wake on deliver) goes through the kernel (POST /deliver), which enqueues the
mail as the session's next turn (the user 2026-06-26): drain the maildir, hand the banner to the kernel, and
put the mail BACK if the kernel reports the session did not take it — so the maildir-drain stays the backstop
and the bus never touches a session directly. Synthetic only.
"""
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_delivery", os.path.join(BIN, "romp-postal-service"))


class PushThroughKernel(unittest.TestCase):
    def setUp(self):
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)   # not a seam test: let _push actually run
        self.saved = (pm._drain, pm._kernel_post, pm.deliver, pm._push_disabled)
        self.posted, self.redelivered = [], []
        pm._push_disabled = lambda: False
        pm._drain = lambda sid, **k: {"messages": [{"from": "alpha", "from_id": "uuid-a", "body": "hi"}]}
        pm.deliver = lambda sid, frm, frm_id, body, **k: self.redelivered.append((sid, body))

    def tearDown(self):
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm._drain, pm._kernel_post, pm.deliver, pm._push_disabled = self.saved

    def test_injected_consumes_and_returns_true(self):
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: (self.posted.append((path, body)),
                                                         {"ok": True, "injected": True})[1]
        self.assertTrue(pm._push("sid-b", {"id": "sid-b", "state": "idle"}))
        self.assertEqual(self.posted[0][0], "/deliver")
        self.assertEqual(self.posted[0][1]["id"], "sid-b")
        self.assertTrue(self.posted[0][1]["text"], "the banner text is handed to the kernel")
        self.assertEqual(self.redelivered, [], "injected → nothing put back")

    def test_not_injected_redelivers_for_the_drain(self):
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: {"ok": True, "injected": False}
        self.assertFalse(pm._push("sid-b", {"id": "sid-b", "state": "idle"}))
        self.assertEqual(self.redelivered, [("sid-b", "hi")], "not injected → mail put back for the drain")

    def test_unreachable_kernel_redelivers(self):
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: None
        self.assertFalse(pm._push("sid-b", {"id": "sid-b", "state": "idle"}))
        self.assertEqual(self.redelivered, [("sid-b", "hi")])

    def test_skips_not_ready_local_without_draining(self):
        pm._kernel_post = lambda *a, **k: self.fail("must not POST for a skipped session")
        pm._drain = lambda sid, **k: self.fail("must not drain a skipped session")
        self.assertFalse(pm._push("sid-b", {"id": "sid-b", "state": "permission"}))
        self.assertEqual(self.redelivered, [])

    def test_remote_agent_is_woken_through_the_kernel(self):
        # A REMOTE (federated) peer has no local 'state' here; _push must still POST /deliver so the local
        # kernel's wake-router forwards it over the host's -L tunnel to the owning kernel. (Regression: the
        # bus used to SKIP remote agents, so an idle remote peer could never be woken cross-machine.)
        self.posted = []
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: (self.posted.append((path, body)),
                                                         {"ok": True, "injected": True})[1]
        self.assertTrue(pm._push("sid-r", {"id": "sid-r", "remote": True}))
        self.assertEqual(self.posted[0][0], "/deliver")
        self.assertEqual(self.posted[0][1]["id"], "sid-r")

    # The bus's /heartbeat handler decides locality from ITS OWN listing (the kernel's GET /sessions,
    # thread rows included) and answers with the bit, so a local session's MCP can stop beating
    # (2026-09-06). The listing is stubbed at _kernel_sessions_checked, the seam every bus-side
    # fetch goes through, so the tests also count fetches.
    LOCAL, THREAD = "local-1", "22222222-3333-4444-5555-666666666666"
    REMOTE = "11111111-2222-3333-4444-555555555555"

    def _listing(self, rows, answered=True):
        self.fetches = []
        pm._kernel_sessions_checked = lambda threads=False: (self.fetches.append(threads), (rows, answered))[1]

    def _with_bus_listing(self, rows, answered=True):
        saved = pm._kernel_sessions_checked
        self._listing(rows, answered)
        pm.HEARTBEATS.clear()
        self.addCleanup(lambda: (setattr(pm, "_kernel_sessions_checked", saved), pm.HEARTBEATS.clear()))

    def test_heartbeat_records_remote_but_ignores_local(self):
        # Only sids the local kernel does NOT own get remote-presence; a local session is already visible via
        # /sessions and must not linger as a phantom [remote] after it dies.
        self._with_bus_listing([{"id": self.LOCAL, "name": "mysess"}])
        self.assertFalse(pm._record_heartbeat(self.REMOTE, "remotetest"), "not local → recorded, answer False")
        self.assertTrue(pm._record_heartbeat(self.LOCAL, "mysess"), "local → ignored, answer True")
        self.assertIn(self.REMOTE, pm.HEARTBEATS)
        self.assertNotIn(self.LOCAL, pm.HEARTBEATS)
        self.assertEqual(self.fetches, [True, True], "one listing fetch per beat, thread rows included")

    def test_heartbeat_unanswered_listing_is_not_local(self):
        # A kernel mid-restart leaves the listing UNANSWERED: the bus must not tell anyone it is local
        # (a remote session that heard that would stop beating for good), and it records the beat
        # exactly as before, so presence survives the restart the way it always did.
        # The rows are NON-empty and hold the sid on purpose: only the answered bit can make this
        # False, so dropping the `answered and` guard fails here (the seam never returns rows with
        # answered=False today; the guard is the docstring's promise, and this is its test).
        self._with_bus_listing([{"id": self.LOCAL, "name": "mysess"}], answered=False)
        self.assertFalse(pm._record_heartbeat(self.LOCAL, "mysess"),
                         "the sid is in the rows, but the rows did not ANSWER: never local")
        self.assertIn(self.LOCAL, pm.HEARTBEATS, "an unanswered listing records the beat, as before")

    def test_heartbeat_from_a_thread_row_is_local(self):
        # A comment thread heartbeats under its own row; the default listing hides thread rows, so the
        # handler asks for them or it would file every live thread as a phantom remote peer.
        self._with_bus_listing([{"id": self.LOCAL, "name": "mysess"},
                                {"id": self.THREAD, "name": "mysess-t1", "thread": True, "parent": self.LOCAL}])
        self.assertTrue(pm._record_heartbeat(self.THREAD, "mysess-t1"))
        self.assertNotIn(self.THREAD, pm.HEARTBEATS)
        self.assertEqual(self.fetches, [True], "the handler asks for thread rows")

    def test_source_wakes_through_the_kernel_deliver(self):
        src = open(os.path.join(BIN, "romp-postal-service"), encoding="utf-8").read()
        self.assertIn('_kernel_post("/deliver"', src, "the live-push wakes via the kernel")
        # the two POSTs that painted the removed backend's status chrome are gone with it (2026-09-11)
        self.assertNotIn('"/mail-badge"', src)
        self.assertNotIn('"/deliver-chrome"', src)


class PushIsChunkedUnderTheKernelsCap(unittest.TestCase):
    """The kernel reads a POST body only up to _POST_MAX_BYTES (1 MiB). The bus used to hand it ONE
    /deliver banner for a recipient's whole box: past the cap the kernel refused it with 413 before
    reading a byte, _kernel_post folded the refusal into None, and _push filed it as the same "deferred"
    every not-taken deferral logs and re-posted the identical banner on every retry pass, forever (review
    find, 2026-09-08). Now the box crosses in chunks measured against the exact wire size, a refusal is
    logged by status and reads as one, and a single message no chunk can carry is bounced to its sender."""
    SID = "11111111-2222-3333-4444-555555555555"
    SENDER = "22222222-3333-4444-5555-666666666666"

    def setUp(self):
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = (pm._drain, pm._kernel_post, pm.deliver, pm.restore, pm._push_disabled, pm._log,
                      pm._name_for_id, pm._tl_append)
        self.posted, self.delivered, self.restored, self.logged = [], [], [], []
        pm._push_disabled = lambda: False
        pm.deliver = lambda sid, frm, frm_id, body, **k: self.delivered.append((sid, frm, frm_id, body, k)) or "n1"
        pm.restore = lambda sid, mid: self.restored.append((sid, mid)) or True
        pm._log = lambda msg: self.logged.append(msg)
        pm._name_for_id = lambda sid: "api" if sid == self.SID else None
        pm._tl_append = lambda fname, obj: None
        pm._OVERSIZE_NAMED.clear()

    def tearDown(self):
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        (pm._drain, pm._kernel_post, pm.deliver, pm.restore, pm._push_disabled, pm._log,
         pm._name_for_id, pm._tl_append) = self.saved
        pm._OVERSIZE_NAMED.clear()

    def _msg(self, mid, size, **k):
        m = {"from": "web", "from_id": self.SENDER, "body": "x" * size, "id": mid, "kind": "coordinate",
             "park": False, "from_host": ""}
        m.update(k)
        return m

    def _inject_all(self, path, body, timeout=2, no_answer=None):
        self.posted.append(body)
        return {"ok": True, "injected": True}

    def test_one_body_for_the_whole_box_would_cross_the_cap_so_it_is_split_oldest_first(self):
        msgs = [self._msg("m1", 600_000), self._msg("m2", 600_000), self._msg("m3", 1000)]
        self.assertGreater(pm._deliver_body_bytes(self.SID, msgs), pm._POST_MAX_BYTES,
                           "the shape the chunking replaces: one banner for these three is over the cap")
        chunks, oversize = pm._push_chunks(self.SID, msgs)
        self.assertEqual(([[m["id"] for m in c] for c in chunks], oversize), ([["m1"], ["m2", "m3"]], []))

    def test_a_box_past_the_cap_crosses_in_chunks_each_under_it(self):
        msgs = [self._msg("m1", 600_000), self._msg("m2", 600_000), self._msg("m3", 1000)]
        pm._drain = lambda sid, **k: {"messages": msgs}
        pm._kernel_post = self._inject_all
        self.assertTrue(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual(len(self.posted), 2, "two bodies: m1 alone, then m2 with m3")
        for body in self.posted:
            self.assertEqual(body["id"], self.SID)
            self.assertLessEqual(len(json.dumps(body).encode("utf-8")), pm._PUSH_MAX_BYTES,
                                 "measured as _kernel_post serializes it")
        self.assertIn("<!-- romp-msg-id: m1 -->", self.posted[0]["text"])
        self.assertIn("<!-- romp-msg-id: m2 -->", self.posted[1]["text"])
        self.assertIn("<!-- romp-msg-id: m3 -->", self.posted[1]["text"])
        self.assertEqual((self.restored, self.delivered), ([], []), "everything landed: nothing put back")

    def test_a_chunk_that_does_not_land_holds_it_and_the_rest_and_names_the_cause(self):
        msgs = [self._msg("m1", 600_000), self._msg("m2", 600_000), self._msg("m3", 1000)]
        pm._drain = lambda sid, **k: {"messages": msgs}
        answers = iter([{"ok": True, "injected": True}, None])
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: (self.posted.append(body), next(answers))[1]
        self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}), "not everything landed")
        self.assertEqual(len(self.posted), 2, "the run stops at the first chunk that does not land")
        self.assertEqual(self.restored, [(self.SID, "m2"), (self.SID, "m3")],
                         "the chunk that failed and everything after it go back under their own ids")
        self.assertEqual(self.delivered, [])
        line = self.logged[-1]
        self.assertIn("deferred (kernel unreachable)", line)
        self.assertIn("2 msg(s) restored", line)
        self.assertIn("after 1 landed", line)

    def test_a_not_taken_deferral_and_a_kernel_refusal_read_differently_in_the_log(self):
        # both used to log the same "deferred" line, so a size refusal was indistinguishable from a
        # session that did not take the wake
        pm._drain = lambda sid, **k: {"messages": [self._msg("m1", 1000)]}
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: {"ok": True, "injected": False}
        self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertIn("deferred (not injected)", self.logged[-1])
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: {"ok": False, "status": 413,
                                                         "error": "request body of 2000000 bytes exceeds the 1048576-byte limit"}
        self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertIn("deferred (kernel answered HTTP 413)", self.logged[-1])
        self.assertEqual(self.restored, [(self.SID, "m1"), (self.SID, "m1")], "restored under its own id either way")

    def test_kernel_post_logs_a_refusal_by_status_and_returns_it_as_one(self):
        saved = urllib.request.urlopen

        def refuse(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 413, "Payload Too Large", {}, io.BytesIO(
                b'{"ok": false, "error": "request body of 2000000 bytes exceeds the 1048576-byte limit"}'))
        urllib.request.urlopen = refuse
        try:
            r = pm._kernel_post("/deliver", {"id": self.SID, "text": "hello"})
        finally:
            urllib.request.urlopen = saved
        self.assertEqual((r["ok"], r["status"]), (False, 413), "a refusal is not None (None is an unreachable kernel)")
        self.assertIn("exceeds the 1048576-byte limit", r["error"])
        self.assertEqual(len(self.logged), 1, self.logged)
        self.assertIn("kernel refused POST /deliver: HTTP 413", self.logged[0])
        self.assertIn("exceeds the 1048576-byte limit", self.logged[0], "the kernel's own reason reaches the log")

        def refuse_long(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b"y" * 100_000))
        urllib.request.urlopen = refuse_long
        try:
            r = pm._kernel_post("/working", {"id": self.SID, "text": ""})
        finally:
            urllib.request.urlopen = saved
        self.assertEqual(r["status"], 400)
        self.assertLess(len(self.logged[-1]), 600, "a bounded slice of the refusal body, never the whole")

        def unreachable(req, timeout=None):
            raise urllib.error.URLError("connection refused")
        urllib.request.urlopen = unreachable
        try:
            self.assertIsNone(pm._kernel_post("/working", {"id": self.SID, "text": ""}), "unreachable stays None")
        finally:
            urllib.request.urlopen = saved

    def test_a_single_oversize_message_is_bounced_to_its_local_sender_not_reposted(self):
        big = self._msg("m-big", 1_000_000)
        pm._drain = lambda sid, **k: {"messages": [big, self._msg("m-small", 1000)]}
        pm._kernel_post = self._inject_all
        self.assertTrue(pm._push(self.SID, {"id": self.SID, "state": "idle"}), "the rest of the box landed")
        self.assertEqual(len(self.posted), 1)
        self.assertNotIn("m-big", self.posted[0]["text"], "the oversize message is never posted")
        self.assertEqual(self.restored, [], "and not put back: it left the recipient's box")
        self.assertEqual(len(self.delivered), 1, "its sender hears it")
        sid, frm, frm_id, note, k = self.delivered[0]
        self.assertEqual((sid, frm, frm_id, k.get("kind")), (self.SENDER, "romp-postal", "", "coordinate"))
        self.assertIn("undeliverable to 'api'", note)
        self.assertIn("%d-byte limit" % pm._PUSH_MAX_BYTES, note)
        self.assertNotIn("xxxxxxxx", note, "the note names the size instead of repeating the body")
        self.assertLess(len(note), 600)
        self.assertTrue(any("m-big" in l and "bounced to its sender" in l for l in self.logged), self.logged)

    def test_the_bounce_retracts_the_drains_read_stamp_so_the_senders_receipt_reads_bounced_not_read(self):
        # The live push drains the box as a CLAIM, and read_box(consume=True) stamps an `exec` row ("the
        # recipient read it") for every message it claims. The local-sender arm of _bounce_oversize wrote only
        # its `bounced` row, so the ledger read exec + bounced, and format_receipts tests exec first: the
        # sender's check_sent said "read HH:MM" for a message that was never handed over and was returned
        # (while the dashboard's card, which ranks bounced first, said Bounced). The real deliver, drain,
        # ledger and receipts here; only the kernel leg is stubbed. Private sids: the ledger is module-wide.
        rcp, snd = "55555555-6666-7777-8888-999999999999", "66666666-7777-8888-9999-aaaaaaaaaaaa"
        pm._drain, pm.deliver, pm.restore, pm._tl_append = self.saved[0], self.saved[2], self.saved[3], self.saved[7]
        pm._name_for_id = lambda sid, rows=None: "api" if sid == rcp else None
        saved_agents = pm.local_agents
        pm.local_agents = lambda threads=False: []           # the receipts' name lookup never reaches a kernel
        try:
            mid = pm.deliver(rcp, "web", snd, "x" * 1_000_000, kind="coordinate")
            pm._kernel_post = self._inject_all
            pm._push(rcp, {"id": rcp, "state": "idle"})
            self.assertEqual(self.posted, [], "the oversize message is never posted")
            self.assertFalse((pm.MAILROOT / rcp / "new" / mid).exists(), "it left the recipient's box")
            self.assertEqual(len(pm.read_box(snd, consume=False)), 1, "its sender holds the bounce note")
            recs = pm._sent_receipts(snd)
            text = pm.format_receipts(recs)
            self.assertNotIn("read ", text, "check_sent must not say a returned message was read")
            self.assertIn("undeliverable, returned to you", text)
            self.assertEqual([(r["id"], r["exec"], bool(r["bounced"])) for r in recs], [(mid, None, True)])
            rows = [json.loads(l) for l in (pm.TLDIR / "messages.jsonl").read_text().splitlines()]
            self.assertEqual([r["ev"] for r in rows if r.get("id") == mid], ["sent", "unexec", "bounced"],
                             "the push claims without a read stamp (2026-10-03); the bounce's unexec still retracts any "
                             "stamp an older bus wrote, before its terminal row")
        finally:
            pm.local_agents = saved_agents
            pm.STREAKS.pop(rcp, None)

    def test_an_oversize_message_with_no_local_sender_waits_for_the_drain_and_is_named_once(self):
        relayed = self._msg("m-far", 1_000_000, from_host="TESTHOST")   # acked to its host at relay time
        pm._drain = lambda sid, **k: {"messages": [relayed]}
        pm._kernel_post = lambda *a, **k: self.fail("an oversize body must never be posted")
        for _ in range(3):                                   # the retry pass re-claims it every 5 s
            self.assertFalse(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual(self.restored, [(self.SID, "m-far")] * 3, "put back under its own id each pass")
        self.assertEqual(self.delivered, [], "a remote sender has no local box to bounce into")
        named = [l for l in self.logged if "m-far" in l]
        self.assertEqual(len(named), 1, named)
        self.assertIn("waits in new/ for the turn-end drain", named[0])

    def test_an_oversize_message_from_a_from_label_sender_is_put_back_and_the_rest_still_land(self):
        # `romp mail send --from cron` mails under ext:cron (cli_send), which is no mailbox: the REAL deliver()
        # raises ValueError for it (_safe_id has no ':'). Before 2026-09-10 the local-sender arm took it, the
        # ValueError escaped into _push's catch-all as "push error", nothing was posted and nothing put back:
        # every message the drain had claimed sat in cur/ while its sender's receipt read "read".
        pm.deliver = self.saved[2]                            # the real one: its refusal is the point
        big = self._msg("m-big", 1_000_000, **{"from": "cron", "from_id": "ext:cron"})
        pm._drain = lambda sid, **k: {"messages": [big, self._msg("m-small", 1000)]}
        pm._kernel_post = self._inject_all
        self.assertTrue(pm._push(self.SID, {"id": self.SID, "state": "idle"}), "the rest of the box landed")
        self.assertEqual([l for l in self.logged if "push error" in l], [], "nothing escaped to the catch-all")
        self.assertEqual(len(self.posted), 1)
        self.assertIn("<!-- romp-msg-id: m-small -->", self.posted[0]["text"])
        self.assertNotIn("m-big", self.posted[0]["text"], "the oversize message is never posted")
        self.assertEqual(self.restored, [(self.SID, "m-big")],
                         "put back under its own id for the drain and check_inbox, which have no size cap")
        self.assertFalse((pm.MAILROOT / "ext:cron").exists(), "no mailbox is made for the label")
        named = [l for l in self.logged if "m-big" in l]
        self.assertEqual(len(named), 1, named)
        self.assertIn("has no local sender to bounce to", named[0])

    def test_publish_working_reads_a_refusal_as_failure(self):
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: {"ok": False, "status": 400, "error": "id required"}
        self.assertFalse(pm._publish_working(self.SID, "note"), "a refusal used to pass the `is not None` test")
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: {"ok": True}
        self.assertTrue(pm._publish_working(self.SID, "note"))
        pm._kernel_post = lambda path, body, timeout=2, no_answer=None: None
        self.assertFalse(pm._publish_working(self.SID, "note"))


class _FakeKernel(BaseHTTPRequestHandler):
    """The kernel's /deliver gate, in shape: 413 before reading a body announced past the cap; within it,
    the body is read and injected."""
    protocol_version = "HTTP/1.1"
    seen = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > pm._POST_MAX_BYTES:
            self.close_connection = True
            self._answer(413, {"ok": False, "error": "request body of %d bytes exceeds the %d-byte limit"
                               % (n, pm._POST_MAX_BYTES)})
            return
        raw = self.rfile.read(n)
        _FakeKernel.seen.append((self.path, len(raw)))
        self._answer(200, {"ok": True, "injected": True})

    def _answer(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if code != 200:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class PushOverTheWire(unittest.TestCase):
    """_push with the real _kernel_post, a real maildir and a fake kernel wearing the real gate's shape:
    two 600 KB messages, which as one banner would be refused, cross as two POSTs and the box empties."""
    SID = "33333333-4444-5555-6666-777777777777"

    def setUp(self):
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = (pm.KERNEL_BASE, pm._push_disabled, pm._log)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeKernel)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % self.srv.server_address[1]
        pm._push_disabled = lambda: False
        self.logged = []
        pm._log = lambda msg: self.logged.append(msg)
        _FakeKernel.seen = []

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm.KERNEL_BASE, pm._push_disabled, pm._log = self.saved
        pm.STREAKS.pop(self.SID, None)

    def test_two_large_messages_cross_as_two_posts_and_the_box_empties(self):
        pm.deliver(self.SID, "web", "22222222-3333-4444-5555-666666666666", "a" * 600_000, kind="coordinate")
        pm.deliver(self.SID, "web", "22222222-3333-4444-5555-666666666666", "b" * 600_000, kind="coordinate")
        self.assertTrue(pm._push(self.SID, {"id": self.SID, "state": "idle"}))
        self.assertEqual([p for p, n in _FakeKernel.seen], ["/deliver", "/deliver"])
        for path, n in _FakeKernel.seen:
            self.assertLessEqual(n, pm._POST_MAX_BYTES)
        self.assertEqual(pm.read_box(self.SID, consume=False), [], "both landed: nothing left in new/")
        self.assertEqual([l for l in self.logged if "deferred" in l or "refused" in l], [])


class HeartbeatRoute(unittest.TestCase):
    """POST /heartbeat answers {ok, local} over the wire: the contract a session's MCP loop reads to
    stop beating (2026-09-06). The real Handler serves on a loopback ThreadingHTTPServer (the token
    gate's pattern) and the bus's listing is stubbed at _kernel_sessions_checked, so an edit that drops
    the bit, or answers local for a sid the listing does not hold, fails here and not as a remote
    session that never stops beating."""
    LOCAL, REMOTE = PushThroughKernel.LOCAL, PushThroughKernel.REMOTE

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self._saved = pm._kernel_sessions_checked
        pm._kernel_sessions_checked = lambda threads=False: ([{"id": self.LOCAL, "name": "mysess"}], True)
        pm.HEARTBEATS.clear()
        pm.STATE.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        pm._kernel_sessions_checked = self._saved
        pm.HEARTBEATS.clear()

    def _beat(self, sid, name):
        req = urllib.request.Request("http://127.0.0.1:%d/heartbeat" % self.port, method="POST",
                                     data=json.dumps({"id": sid, "name": name}).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": pm.SERVE_TOKEN})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_a_sid_the_listing_holds_hears_local(self):
        self.assertEqual(self._beat(self.LOCAL, "mysess"), {"ok": True, "local": True})
        self.assertNotIn(self.LOCAL, pm.HEARTBEATS, "a local beat is not remote presence")

    def test_a_sid_the_listing_lacks_hears_remote_and_is_recorded(self):
        self.assertEqual(self._beat(self.REMOTE, "remotetest"), {"ok": True, "local": False})
        self.assertIn(self.REMOTE, pm.HEARTBEATS, "the beat is presence, as it always was")


if __name__ == "__main__":
    unittest.main()
