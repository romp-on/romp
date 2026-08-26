#!/usr/bin/env python3
"""The comment thread's third exit (the user 2026-08-23): MERGE folds the discussion back into the
parent session — the exchange lands as the person's own handoff (_merge_body), the thread settles to
'merged', and its CLI shuts down like a resolve. Once per thread (the CAS refuses a re-merge), loud
when there is nothing to merge, and the latch reverts on a failed delivery. SYNTHETIC fixtures."""
import os
import tempfile
import unittest
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


class FakeBE:
    def __init__(self):
        self.sent, self.killed = [], []

    def send(self, sid, text):
        self.sent.append((sid, text))

    def kill(self, sid):
        self.killed.append(sid)


class CommentMerge(unittest.TestCase):
    def setUp(self):
        self.be = FakeBE()
        self._saved = (km.Sessions.backend_for, km._thread_messages, km._push_soon)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._thread_messages = lambda tsid, cut, floor_t=0: [
            {"who": "user", "text": "should the cache be write-through?", "t": 1},
            {"who": "assistant", "text": "Yes; it removes the stale-read window.", "t": 2}]
        km._push_soon = lambda: None
        with km._comments_lock:
            km._save_comments(PARENT, {"threads": [{
                "tid": TSID, "sid": TSID, "name": "web-comment-1", "status": "open",
                "anchorUuid": "aaaabbbb-1111-2222-3333-444455556666", "anchorT": 100,
                "cutUuid": "aaaabbbb-1111-2222-3333-444455556666",
                "exact": "the caching layer", "createdT": 100, "lastSeenT": 100}]})

    def tearDown(self):
        km.Sessions.backend_for, km._thread_messages, km._push_soon = self._saved
        with km._comments_lock:
            km._save_comments(PARENT, {"threads": []})

    def _row(self):
        return km._comment_thread(PARENT, TSID)

    def test_merge_delivers_the_handoff_and_settles_the_thread(self):
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual(len(self.be.sent), 1)
        sid, body = self.be.sent[0]
        self.assertEqual(sid, PARENT, "the handoff lands in the PARENT session")
        self.assertIn("> the caching layer", body, "grounded by the quoted passage")
        self.assertIn("Me: should the cache be write-through?", body)
        self.assertIn("Them: Yes; it removes the stale-read window.", body)
        self.assertIn("account for it", body, "the going-forward direction rides the handoff")
        self.assertEqual(self._row().get("status"), "merged")
        self.assertEqual(self.be.killed, [TSID], "the CLI has nothing left; its work is folded back")

    def test_nothing_to_merge_reverts_the_latch_loudly(self):
        km._thread_messages = lambda tsid, cut, floor_t=0: []
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("no discussion to merge", err or "")
        self.assertEqual(self._row().get("status"), "open", "the latch never sticks on a refusal")
        self.assertEqual(self.be.sent, [])

    def test_failed_delivery_reverts_the_latch(self):
        self.be.send = lambda sid, text: (_ for _ in ()).throw(RuntimeError("backend down"))
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("could not be delivered", err or "")
        self.assertEqual(self._row().get("status"), "open")

    def test_a_merged_thread_refuses_a_second_merge_and_replies(self):
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("already merged", err or "")
        self.assertEqual(len(self.be.sent), 1, "once per thread")

    def test_the_transcript_cap_keeps_the_recent_tail(self):
        body = km._merge_body("q", [{"who": "user", "text": "x" * 9000}])
        self.assertIn("earlier discussion trimmed", body)
        self.assertLess(len(body), 9000, "capped — a huge thread never floods the parent's turn")


if __name__ == "__main__":
    unittest.main()
