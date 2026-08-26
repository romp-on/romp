#!/usr/bin/env python3
"""The read-only observability GETs (both teams' surveys, 2026-08-24): GET /feed.json — exactly what
build_feed ships to the board — and GET /classify?id=<sid> — one session's live classification as
the kernel derives it, a JOIN over reads that already exist (never a second predicate
implementation). Both serve-token-gated, read-only, no side effects. Drives the REAL Handler over
HTTP (the test_tag_route idiom). Synthetic only."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_obs", os.path.join(BIN, "romp-kernel")).load_module()
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


def _state_snapshot(root):
    """Every file under STATE with its (mtime_ns, size) — the read-only-ness witness."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (st.st_mtime_ns, st.st_size)
    return out


class ObservabilityRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._flags_cache.clear()
        self._saved = km._tmux_sessions
        km._tmux_sessions = lambda: {}
        km._built_feed[:] = [None, None, 0, 0]     # a cold cache: /feed.json builds once, like a connect

    def tearDown(self):
        km._tmux_sessions = self._saved
        jd.STATE = self._state
        km._flags_cache.clear()
        km._built_feed[:] = [None, None, 0, 0]
        self.td.cleanup()

    def _get(self, path, token=True):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        req = urllib.request.Request(url, headers=(
            {"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]} if token else {}))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode()

    def test_both_routes_are_token_gated(self):
        self.assertEqual(self._get("/feed.json", token=False)[0], 403)
        self.assertEqual(self._get("/classify?id=" + SID, token=False)[0], 403)

    def test_feed_json_is_exactly_the_boards_payload_and_read_only(self):
        (jd.STATE / "goals").mkdir(parents=True, exist_ok=True)
        before = _state_snapshot(self.td.name)
        st, d = self._get("/feed.json")
        self.assertEqual(st, 200)
        self.assertEqual(d.get("type"), "feed", "the exact build_feed shape, not a re-derivation")
        self.assertIn("asks", d)
        self.assertIn("now", d)
        self.assertEqual(_state_snapshot(self.td.name), before, "a GET writes nothing")

    def test_classify_requires_an_id(self):
        st, _ = self._get("/classify")
        self.assertEqual(st, 400)

    def test_classify_joins_the_existing_reads_and_is_read_only(self):
        # seed the stores the joined reads consume: a progressing state transition, and a nudge
        # ledger holding this session's goal record (deadWait flag) + a walk-gate journal entry
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "states" / (SID + ".jsonl")).write_text(
            json.dumps({"state": "working", "t": 1000}) + "\n")
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({
            "enabled": True,
            "nudged": {SID + ":g1": {"deadWait": True, "anchor": 5, "at": 6},
                       "someone-else:g9": {"at": 7}},
            "walkGates": {SID: {"gate": "compacting", "at": 8},
                          "someone-else": {"gate": "open-turn", "at": 9}}}))
        km._autonudge_cache.clear()
        before = _state_snapshot(self.td.name)
        st, d = self._get("/classify?id=" + SID)
        self.assertEqual(st, 200)
        self.assertEqual(d["id"], SID)
        self.assertFalse(d["live"], "no live snapshot in this world")
        self.assertEqual(d["state"], {"value": "working", "t": 1000}, "_last_state verbatim")
        self.assertFalse(d["idle"], "the nudge gate's own idle rule: working is progressing")
        self.assertIn("_PROGRESSING_STATES", d["idleRule"], "the input's provenance rides the payload")
        self.assertIsNone(d["awaiting"])
        self.assertIsNone(d["waitingOn"])
        self.assertEqual(d["owesAsks"], [])
        self.assertEqual(d["nudge"]["records"], {SID + ":g1": {"deadWait": True, "anchor": 5, "at": 6}},
                         "only THIS session's ledger rows — deadWait flags ride verbatim")
        self.assertEqual(d["nudge"]["walkGates"], {SID: {"gate": "compacting", "at": 8}},
                         "…and its walk-gate journal entries")
        self.assertTrue(d["nudge"]["enabled"])
        self.assertEqual(_state_snapshot(self.td.name), before, "a GET writes nothing")

    def test_classify_idle_when_the_state_says_stopped(self):
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "states" / (SID + ".jsonl")).write_text(
            json.dumps({"state": "waiting", "t": 2000}) + "\n")
        st, d = self._get("/classify?id=" + SID)
        self.assertTrue(d["idle"])


if __name__ == "__main__":
    unittest.main()
