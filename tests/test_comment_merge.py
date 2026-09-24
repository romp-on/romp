#!/usr/bin/env python3
"""The comment thread's third exit (the user 2026-08-23): MERGE folds the discussion back into the
parent session — the exchange lands as the person's own handoff (_merge_body), the thread settles to
'merged', and its CLI shuts down like a resolve. Once per thread (the CAS refuses a re-merge), loud
when there is nothing to merge, and the latch reverts on a failed delivery. SYNTHETIC fixtures."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

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

    def test_a_file_threads_handoff_names_the_file(self):
        with km._comments_lock:
            data = km._load_comments(PARENT)
            data["threads"][0]["src"] = "~/notes/a.md"
            km._save_comments(PARENT, data)
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertIn("~/notes/a.md", self.be.sent[0][1])

    def test_a_refused_handoff_reverts_the_latch_and_kills_nothing(self):
        # T315 (the commit-13 review's second item): the merge sent with the default and checked only for an
        # exception, so a parent whose backend refused the handoff was still marked merged and its thread's CLI
        # ended; the merge is the user's gesture (user=True when the send takes it) and a False reverts
        class Refusing(FakeBE):
            def send(self, sid, text, user=False):
                self.sent.append((sid, text, user)); return False
        self.be = Refusing()
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("refused", err or "")
        self.assertEqual(self._row().get("status"), "open", "nothing marked merged on a refusal")
        self.assertEqual(self.be.killed, [], "the thread's CLI lives on: its discussion is not folded back yet")
        self.assertEqual([u for _, _, u in self.be.sent], [True], "the merge speaks as the user")

    def test_nothing_to_merge_reverts_the_latch_loudly(self):
        km._thread_messages = lambda tsid, cut, floor_t=0: []
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("no discussion to send back", err or "")
        self.assertEqual(self._row().get("status"), "open", "the latch never sticks on a refusal")
        self.assertEqual(self.be.sent, [])

    def test_failed_delivery_reverts_the_latch(self):
        self.be.send = lambda sid, text: (_ for _ in ()).throw(RuntimeError("backend down"))
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("could not be delivered", err or "")
        self.assertEqual(self._row().get("status"), "open")

    def test_a_relayed_thread_stays_talkable_and_a_second_relay_sends_only_the_new_tail(self):
        # T145 (the user 2026-08-28): a relay does NOT close the thread — talking continues, and the
        # next relay carries only what the session hasn't seen (the evidence-time relayedT floor).
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual(len(self.be.sent), 1)
        self.assertGreater(self._row().get("relayedT") or 0, 0, "the sent-back stamp persists on the row")
        # nothing new yet → the CAS refuses in relay vocabulary, pointing at the reply path
        err = km._comment_merge(PARENT, TSID)
        self.assertIn("already relayed", err or "")
        # a reply REOPENS the relayed thread exactly like a resolved one…
        self.be.fork = lambda *a, **k: None      # the reply path gates on the SDK shape (fork/resume)
        self.be.resume = lambda name, sid: None
        _send0 = self.be.send                    # the reply path checks send's truthiness; the fake returns None
        self.be.send = lambda sid, text: (_send0(sid, text) or True)
        self.assertIsNone(km._comment_reply(PARENT, TSID, "one more thought"))
        self.assertEqual(self._row().get("status"), "open")
        # …and the next relay sends ONLY messages past the floor
        base = km._thread_messages(TSID, "", 0)
        newer = base + [{"who": "user", "text": "one more thought", "t": (base[-1]["t"] if base else 0) + 10}]
        km._thread_messages = lambda tsid, cut, floor_t=0: newer
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        self.assertEqual(len(self.be.sent), 3, "the relay + the reply + the tail-only relay")
        self.assertIn("one more thought", self.be.sent[-1][1])
        for m in base:
            if (m.get("text") or "").strip():
                self.assertNotIn(m["text"], self.be.sent[-1][1], "already-relayed content never repeats")

    def test_the_relay_arrives_machine_dressed_with_the_whole_exchange(self):
        # T145: the arrival wears the T130 machine attribution (markers), never a plain user bubble,
        # and the body carries BOTH sides of the exchange in full.
        self.assertIsNone(km._comment_merge(PARENT, TSID))
        body = self.be.sent[0][1]
        self.assertIn("<!-- romp-injected --><!-- romp-tag: relay -->", body)
        self.assertNotIn("romp-system", body, "relayed content is a conversation, not a kernel status notice")
        for m in km._thread_messages(TSID, "", 0):
            if (m.get("text") or "").strip():
                self.assertIn(m["text"], body, "the WHOLE exchange goes — no summary, no slice")

    def test_the_transcript_cap_keeps_the_recent_tail(self):
        # T145 raised the cap to 48k (the whole exchange goes; 6k trimmed real discussions — the
        # user's 'only part merged'); the trim marker remains the honest pathological backstop
        body = km._merge_body("q", [{"who": "user", "text": "x" * 9000}])
        self.assertNotIn("earlier discussion trimmed", body, "9k is a normal discussion now — untrimmed")
        big = km._merge_body("q", [{"who": "user", "text": "x" * 60000}])
        self.assertIn("earlier discussion trimmed", big)
        self.assertLess(len(big), 60000, "the pathological backstop still bounds the injection")


if __name__ == "__main__":
    unittest.main()
