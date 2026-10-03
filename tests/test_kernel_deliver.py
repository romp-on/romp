#!/usr/bin/env python3
"""The deliver-time WAKE moved into the kernel (the user 2026-06-26): the postal bus drains its maildir and
hands the banner to the kernel (POST /deliver), which routes it to the owning backend (the SDK backend
enqueues it). The bus never shells a session itself. Also the bus's one notice door (POST /postal-notice).
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_deliver", os.path.join(BIN, "romp-kernel"))


class SdkDeliverSourcePin(unittest.TestCase):
    def test_sdk_backend_defines_deliver_as_a_no_echo_enqueue(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py"), encoding="utf-8").read()
        body = src.split("def deliver(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("s.enqueue_postal(text, ", body,
                      "SDK deliver enqueues the banner (the deliver-time wake), its repeat check and its queue entry "
                      "one step (2026-09-29)")
        self.assertNotIn("_echo_text", body, "no optimistic human echo — it's a peer's mail, not the user's input")

    def test_post_deliver_routes_through_backend_for(self):
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertIn('u.path == "/deliver"', src)
        self.assertIn("Sessions.backend_for(sid).deliver(sid, text)", src)


class PostalNoticeRoute(unittest.TestCase):
    """POST /postal-notice: the bus's one door to the dashboard (review find, 2026-09-08). A mail file
    it had to move aside, a return note it could not deliver, temps a crash left: each lands as one
    row on the ring the bell mirrors, under the `refused` kind, with the bus's own text. Token-gated
    like every POST; a body with no text files nothing."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, body, token=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Content-Type": "application/json"}
        if token:
            hdrs["X-Romp-Token"] = km.TOKEN
        c.request("POST", "/postal-notice", json.dumps(body), hdrs)
        r = c.getresponse()
        raw = r.read().decode()
        c.close()
        try:
            return r.status, json.loads(raw or "{}")
        except ValueError:
            return r.status, raw

    def test_a_notice_files_one_refused_row_and_an_empty_one_files_nothing(self):
        before = km._sync_notice_count()
        status, out = self._post({"text": "mail for web: 1.2_ab.TESTHOST could not be read (errno 13); moved aside"})
        self.assertEqual((status, out.get("ok")), (200, True))
        self.assertEqual(km._sync_notice_count(), before + 1, "exactly one row")
        row = km._sync_notice_rows()[-1]
        self.assertEqual((row["kind"], row["ok"]), ("refused", False), "the kind a fault wears, never a machine sync")
        self.assertIn("moved aside", row["text"], "the bus's own text, unchanged")
        status, out = self._post({"text": "   "})
        self.assertEqual((status, out.get("ok")), (400, False))
        self.assertIn("text required", out.get("error", ""))
        status, out = self._post("not an object")
        self.assertEqual(status, 400)
        self.assertEqual(km._sync_notice_count(), before + 1, "a refused body files nothing")
        status, _ = self._post({"text": "anonymous"}, token=False)
        self.assertEqual(status, 403, "token-gated like every POST")
        self.assertEqual(km._sync_notice_count(), before + 1)


if __name__ == "__main__":
    unittest.main()
