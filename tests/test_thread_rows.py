#!/usr/bin/env python3
"""GET /sessions?threads=1 (the user 2026-08-22): comment-thread sessions ride the unified session
list ONLY when asked — the postal bus asks, so a thread can mail its parent under its own name;
every existing consumer (Obsidian picker, romp sessions, the default bus listing) is unchanged.
Drives the REAL Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

PARENT = "11111111-2222-3333-4444-555555555555"
TSID = "66666666-7777-8888-9999-000000000000"


class ThreadRowsRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._saved = (km._session_rows, km._thread_rows)
        km._session_rows = lambda: [{"id": PARENT, "name": "web", "state": "working"}]
        km._thread_rows = lambda: [{"id": TSID, "name": "web-comment-1", "state": "working",
                                    "thread": True, "parent": PARENT}]

    def tearDown(self):
        km._session_rows, km._thread_rows = self._saved

    def _get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_default_listing_hides_threads(self):
        rows = self._get("/sessions")
        self.assertEqual([r["id"] for r in rows], [PARENT], "every existing consumer sees exactly what it saw")

    def test_threads_param_appends_flagged_rows(self):
        rows = self._get("/sessions?threads=1")
        self.assertEqual([r["id"] for r in rows], [PARENT, TSID])
        t = rows[1]
        self.assertTrue(t.get("thread"), "flagged so the bus can mark it as a minor player")
        self.assertEqual(t.get("parent"), PARENT, "the parent sid rides for reply resolution")


class ThreadRowsBuilder(unittest.TestCase):
    def test_thread_rows_join_the_name_from_the_parents_comments_store(self):
        class FakeBE:
            def thread_sessions(self):
                return {TSID: {"state": "waiting", "threadOf": PARENT}}
        saved = (km._sdk, km._load_comments, km._cwd_of)
        km._sdk = lambda: FakeBE()
        km._load_comments = lambda sid: ({"threads": [{"tid": TSID, "name": "web-comment-1"}]}
                                         if sid == PARENT else {})
        km._cwd_of = lambda sid: ""
        try:
            rows = km._thread_rows()
        finally:
            km._sdk, km._load_comments, km._cwd_of = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "web-comment-1",
                         "the comments store is where a thread's editable name lives")
        self.assertEqual(rows[0]["parent"], PARENT)
        self.assertTrue(rows[0]["thread"])


if __name__ == "__main__":
    unittest.main()
