#!/usr/bin/env python3
"""The PR chip's wiring into live payloads, executed: the pusher's ledger row carries the PR slice, the
closer stamps PR refs from the parse it judged, and the Outline's error chip reaches a retry.

The walk's prNums and the ledger memo's repo key are executed in tests/test_chat_fixed_cost_memos.py
(LedgerMemo). Synthetic fixtures only: private sids, invented text, the notes-api demo world."""
import inspect
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_pr_wiring", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "44444444-5555-6666-7777-888888888888"
REPO = "notes-api-org/notes-api"
T0 = 1781100000


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class LedgerRow(unittest.TestCase):
    """The pusher attaches one row per built session; the PR slice must ride it."""

    def test_the_row_carries_the_pr_slice(self):
        slice_ = {"branch": "dev/notes-index", "prNum": 7, "prs": {"7": {"num": 7}}, "prError": None}
        with mock.patch.object(km, "_session_pr_payload", lambda sid, ledger, wt: dict(slice_)), \
                mock.patch.object(km, "_mail_off_fields", lambda sid: {}), \
                mock.patch.object(km, "_fleet_archived_tops", lambda sid: []):
            row = km._outline_ledger_row({"id": SID, "name": "web", "ledger": {"tree": []}})
        self.assertEqual({k: row[k] for k in slice_}, slice_)
        self.assertEqual(row["ledger"]["archivedTops"], [])

    def test_a_failing_slice_leaves_the_row_standing(self):
        def boom(*a):
            raise RuntimeError("dictionary changed size during iteration")

        with mock.patch.object(km, "_session_pr_payload", boom), \
                mock.patch.object(km, "_mail_off_fields", lambda sid: {}), \
                mock.patch.object(km, "_fleet_archived_tops", lambda sid: []):
            row = km._outline_ledger_row({"id": SID, "name": "web", "ledger": None})
        self.assertIsNone(row["prs"])
        self.assertEqual(row["name"], "web")

    def test_the_push_builds_its_rows_through_it(self):
        self.assertIn('feed["ledgers"] = [_outline_ledger_row(m) for m in chat_sessions]', inspect.getsource(km._push))


class CloserStamps(unittest.TestCase):
    """_close_session hands the goal store and the parse it judged to _record_pr_refs."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self._llm = jd.closer_llm
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [], "block": []}'

    def tearDown(self):
        jd.closer_llm = self._llm
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        jd._rebind_state(self.saved_state)
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_judged_turn_stamps_from_its_segments(self):
        store = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "status": {}, "placements": {},
                 "nodes": {SID + ":g1": {"id": SID + ":g1", "text": "Ship the notes-api index", "parentId": None,
                                         "nodeComplete": False, "blocked": False, "cleared": False,
                                         "trail": [], "t": T0, "log": []}}}
        jd.save_goals(SID, store)
        recs = [{"type": "user", "timestamp": _iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
                 "message": {"role": "user", "content": "open the index PR"}},
                {"type": "assistant", "timestamp": _iso(T0 + 30), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Opened."}],
                             "stop_reason": "end_turn"}}]
        path = os.path.join(self.td, SID + ".jsonl")
        Path(path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        calls = []
        real = jd._record_pr_refs
        with mock.patch.object(jd, "_record_pr_refs", lambda st, segs: (calls.append((st, segs)), real(st, segs))[1]):
            jd._close_session(SID, path, T0 + 5000)
        self.assertEqual(len(calls), 1)
        st, segs = calls[0]
        self.assertIn(SID + ":g1", st["nodes"], "the session's own store")
        self.assertTrue(segs, "the parse's segments, not None: a turn was judged")


class ErrorChipRetry(unittest.TestCase):
    """The Outline's error chip posts prRetry; the kernel maps it to a re-read of that session's repo."""

    def test_the_handler_routes_to_the_retry(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('msg.get("type") == "prRetry" and msg.get("id")', src)
        self.assertIn('if _pr_retry(str(msg["id"])):', src)

    def test_the_retry_reaches_gitpr(self):
        retried = []
        km._pr_repo_seen[SID] = REPO
        with mock.patch.object(km.gp, "retry", lambda repo: retried.append(repo)):
            self.assertTrue(km._pr_retry(SID))
        self.assertEqual(retried, [REPO])


if __name__ == "__main__":
    unittest.main()
