#!/usr/bin/env python3
"""The nimbus false-block chain (the user 2026-07-11): the SDK snapshot carried the live bg-task set
but Sessions.live()'s merge never copied `bgTasks` into the merged map — so every consumer
(_session_awaiting source 0.5, the #bg-tasks live gate, the auto-nudge gate) read None, the session
never read awaiting, the auto-nudge fired on a genuinely-waiting session, and the failed nudge
hard-blocked its card with "it needs your direction". Two guards here:

  * the MERGE itself carries bgTasks through — tested through the REAL Sessions.live() with a fake
    backend, exactly the seam the earlier _tmux_sessions-stubbing tests bypassed;
  * _mark_nudge_failed never converts a nudge into a block while the session is AWAITING — its reply
    ("waiting on the experiment") DID explain itself.

SYNTHETIC fixtures only (placeholder UUIDs, invented text).
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_awm", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_awm", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
TIMER = {"desc": "20-minute timer for campaign-start check", "type": "local_bash",
         "since": 1, "toolUseId": "tu1", "lastTool": ""}


class _FakeSdkBackend:
    def __init__(self, snap):
        self._snap = snap

    def live_sessions(self):
        return {SID: dict(self._snap)}


class MergeCarriesBgTasks(unittest.TestCase):
    def test_the_real_merge_carries_bgTasks_and_subagents_through(self):
        # through the REAL Sessions.live(), not a _tmux_sessions stub — the seam the bug lived in
        snap = {"state": "waiting", "since": "1", "model": "Fable 5", "effort": "xhigh",
                "modelPending": False, "effortPending": False, "retryCount": 0, "retryInfo": None,
                "ctx": 10, "mode": "auto", "subagents": [], "bgTasks": [dict(TIMER)]}
        saved_sdk, saved_tmux = km._sdk, km.Sessions._TMUX_LIVE if hasattr(km.Sessions, "_TMUX_LIVE") else None
        fake = _FakeSdkBackend(snap)
        km._sdk = lambda: fake
        tmux_saved = km._TMUX.live_sessions
        km._TMUX.live_sessions = staticmethod(lambda: {})
        try:
            out = km.Sessions.live()
        finally:
            km._sdk = saved_sdk
            km._TMUX.live_sessions = tmux_saved
        self.assertIn(SID, out)
        self.assertEqual([t["desc"] for t in out[SID]["bgTasks"]],
                         ["20-minute timer for campaign-start check"],
                         "the merged map carries the live bg-task set — the awaiting/nudge gates read it here")
        self.assertEqual(out[SID]["subagents"], [])
        # ...and _session_awaiting source 0.5 fires off exactly that merged map
        saved_sessions = km._tmux_sessions
        km._tmux_sessions = lambda: out
        try:
            why = km._session_awaiting(SID, "/nonexistent", True)
        finally:
            km._tmux_sessions = saved_sessions
        self.assertEqual(why, {"kind": "task", "since": 1,   # the dispatch stamp → the chips' elapsed readout (the user 2026-08-23)
                               "why": "waiting on a background task: 20-minute timer for campaign-start check"})


class NudgeFailedRespectsAwaiting(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        # patch the KERNEL's own jd instance (km imports its own copy; a separately-loaded jd is a
        # different module object and the kernel would keep reading the live state dirs)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._autonudge_cache.clear()
        self.gid = SID + ":g1"
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": self.gid, "placements": {}, "status": {},
            "nodes": {self.gid: {"id": self.gid, "text": "run the long experiment", "parentId": None,
                                 "nodeComplete": False, "blocked": False, "cleared": False,
                                 "trail": [], "t": 100, "mt": 100, "log": []}}}))
        (td / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {self.gid: {"count": 1, "lastTurnId": "t1"}}}))
        km._path_of = lambda sid, now=None: "/nonexistent"

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of = self._saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def test_awaiting_session_never_gets_the_failure_block(self):
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "task", "why": "waiting on a background task: the experiment watcher"}
        km._mark_nudge_failed(self.gid)
        store = km.jd.load_goals(SID)
        self.assertFalse(store["nodes"][self.gid]["blocked"],
                         "an awaiting session's nudge is never converted into a needs-you block")
        rec = km._auto_nudge_data()["nudged"][self.gid]
        self.assertFalse(rec.get("failed"), "the episode isn't failed either — it re-arms cleanly")

    def test_a_genuinely_stalled_session_still_gets_the_block(self):
        km._session_awaiting = lambda sid, path, idle, stamp=False: None
        km._mark_nudge_failed(self.gid)
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][self.gid]["blocked"], "the existing stall→block behavior stands")
        self.assertTrue(km._auto_nudge_data()["nudged"][self.gid].get("failed"))


if __name__ == "__main__":
    unittest.main()
