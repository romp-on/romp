#!/usr/bin/env python3
"""Thread mail on the timeline (the user 2026-08-23): a comment thread has no lane — its visual home
is the comment's anchor square on the parent's lane. _postal_messages rewrites a thread endpoint to
the parent's lane with fromThreadT/toThreadT pinned to the square's x, keeps the THREAD's name for
the tooltip, and the raw self-send check runs before the rewrite so thread↔parent mail (one lane,
two identities) survives. SYNTHETIC fixtures only."""
import json
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
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

PARENT = "11111111-2222-3333-4444-555555555555"
TSID = "66666666-7777-8888-9999-000000000000"
NOW = 1_787_500_000
ANCHOR_T = NOW - 3600


def _seed_mail(rows):
    d = jd.STATE / "timeline"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "messages.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class ThreadArc(unittest.TestCase):
    def setUp(self):
        self._saved = (km._load_comments, km._msg_summaries)
        km._load_comments = lambda sid: ({"threads": [{"tid": TSID, "anchorT": ANCHOR_T,
                                                       "name": "web-comment-1", "status": "open"}]}
                                         if sid == PARENT else {})
        km._msg_summaries = lambda: {}

    def tearDown(self):
        km._load_comments, km._msg_summaries = self._saved
        p = jd.STATE / "timeline" / "messages.jsonl"
        if p.exists():
            p.unlink()

    def test_thread_to_parent_mail_rides_the_parents_lane_from_the_square(self):
        _seed_mail([{"ev": "sent", "id": "m1", "from_id": TSID, "to_id": PARENT,
                     "from": "web-comment-1", "t": NOW - 600, "body": "the anchor section needs a pass"},
                    {"ev": "exec", "id": "m1", "t": NOW - 590}])
        rows = km._postal_messages(NOW, {PARENT}, {PARENT: "web"})
        self.assertEqual(len(rows), 1, "thread↔parent mail survives the raw self-check by design")
        m = rows[0]
        self.assertEqual(m["fromId"], PARENT, "the endpoint is the parent's LANE")
        self.assertEqual(m["fromThreadT"], ANCHOR_T, "…pinned to the comment square's x")
        self.assertEqual(m["from"], "web-comment-1", "…but the tooltip says who really spoke")
        self.assertEqual(m["toId"], PARENT)
        self.assertNotIn("toThreadT", m, "the landing end is ordinary parent mail")

    def test_parent_reply_arcs_back_into_the_square(self):
        _seed_mail([{"ev": "sent", "id": "m2", "from_id": PARENT, "to_id": TSID,
                     "from": "web", "t": NOW - 300, "body": "good catch, apply it"},
                    {"ev": "exec", "id": "m2", "t": NOW - 295}])
        rows = km._postal_messages(NOW, {PARENT}, {PARENT: "web"})
        self.assertEqual(len(rows), 1)
        m = rows[0]
        self.assertEqual(m["toId"], PARENT)
        self.assertEqual(m["toThreadT"], ANCHOR_T, "the reply lands AT the square")
        self.assertEqual(m["to"], "web-comment-1")

    def test_promoted_threads_resolve_nowhere_here(self):
        km._load_comments = lambda sid: ({"threads": [{"tid": TSID, "anchorT": ANCHOR_T,
                                                       "name": "web-comment-1", "status": "promoted"}]}
                                         if sid == PARENT else {})
        self.assertEqual(km._thread_anchors({PARENT}), {},
                         "a promoted thread has its own lane — the square is no longer its home")


if __name__ == "__main__":
    unittest.main()
