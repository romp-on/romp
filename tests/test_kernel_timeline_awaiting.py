#!/usr/bin/env python3
"""The timeline lane carries the AWAITING-background-work signal (the user 2026-07-01, working-state
audit): the chat chip folds _session_awaiting into its yellow working dot, but the timeline lane showed a
bare READY — the last designed split between the surfaces' working models. build_timeline now emits
`awaitingBg` (the same _session_awaiting why-line, live lanes only) on BOTH the skeleton and the bars
build; the view renders an AWAITING badge in the working-yellow family. Named awaitingBg because the
lane's legacy 'awaiting' STATE and `awaiting` intervals both mean blocked-on-YOU. SYNTHETIC fixtures only
(placeholder UUIDs, invented text)."""
import datetime
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = SourceFileLoader("romp_judge_tlaw", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel_tlaw", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 600


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TimelineAwaiting(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []                            # isolate from the real kernel-downtime.jsonl
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / km.jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [{"type": "user", "timestamp": _iso(T0), "uuid": "u1", "parentUuid": None,
                 "promptSource": "typed",
                 "message": {"role": "user", "content": "run the long benchmark in the background"}},
                {"type": "assistant", "timestamp": _iso(T0 + 10), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "Launched it."}],
                             "stop_reason": "end_turn"}}]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(self.tpath, (NOW - 30, NOW - 30))       # recently-touched → discovered as a lane
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.STATE, km.NAMES, km._tmux_sessions)
        km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR = names, proj, td / "goals"
        km.jd.STATE = td                                 # sandbox states/ + usage/ + session-flags reads
        km.NAMES = names
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        (td / "states").mkdir()
        self.states = td / "states" / (SID + ".jsonl")
        km._parse_cache.pop(str(self.tpath), None)

    def tearDown(self):
        (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.STATE, km.NAMES, km._tmux_sessions) = self.saved
        self.td.cleanup()

    def _lane(self, with_bars=True):
        tl = km.build_timeline(NOW, with_bars=with_bars)
        return next(l for l in tl["sessions"] if l["id"] == SID)

    def test_awaiting_overlay_reaches_the_lane(self):
        self.states.write_text(json.dumps({"t": T0 + 20, "state": "waiting"}) + "\n"
                               + json.dumps({"t": T0 + 21, "awaiting": True, "why": "background benchmark running"}) + "\n")
        self.assertEqual(self._lane()["awaitingBg"], "background benchmark running",
                         "the SDK producer's awaiting overlay reaches the timeline lane, same as the chat")

    def test_cleared_overlay_means_no_awaiting(self):
        self.states.write_text(json.dumps({"t": T0 + 21, "awaiting": True, "why": "bg"}) + "\n"
                               + json.dumps({"t": T0 + 30, "awaiting": False}) + "\n")
        self.assertIsNone(self._lane()["awaitingBg"], "an explicitly cleared overlay reads NOT awaiting")

    def test_skeleton_build_carries_it_too(self):
        # the client renders the lane badge from the SKELETON's state; the bars message carries none — the
        # awaiting cue must not flicker off between the two builds (same lesson as 'compacting', 2026-06-29)
        self.states.write_text(json.dumps({"t": T0 + 21, "awaiting": True, "why": "bg agents"}) + "\n")
        self.assertEqual(self._lane(with_bars=False)["awaitingBg"], "bg agents")

    def test_dead_lane_never_awaits(self):
        km._tmux_sessions = lambda: {}                   # session process gone → window-dead lane
        self.states.write_text(json.dumps({"t": T0 + 21, "awaiting": True, "why": "bg"}) + "\n")
        lane = self._lane()
        self.assertFalse(lane["live"])
        self.assertIsNone(lane["awaitingBg"], "a dead session cannot be awaiting background work")

    def test_bg_task_wait_carries_the_task_descriptions(self):
        # the dashed idle-but-waiting stretch (the user 2026-07-13): the lane carries the live bg-task
        # descriptions beside the why, so the stretch's hover lists exactly what's pending
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None, "mode": "",
                                           "bgTasks": [{"task_id": "t1", "desc": "Watch for round3 copy"}]}}
        lane = self._lane()
        self.assertEqual(lane["awaitingBg"], "waiting on a background command: Watch for round3 copy")   # "command" since slice 2 (2026-09-05)
        self.assertEqual(lane["awaitingTasks"], ["Watch for round3 copy"])

    def test_the_lane_ships_the_awaited_count_beside_the_kind(self):
        # T228 (the user's one-count rule): the lane badge words its kind from the SAME count the chat chip
        # uses — one live task/agent reads "Awaiting task"/"Awaiting agent" on the lane too. The number is
        # the snapshot's own; a source that cannot count ships None (the view keeps its historic plural).
        base = {"state": "waiting", "since": NOW - 100, "model": "", "effort": "", "context": None,
                "compactPct": None, "color": None, "mode": ""}
        km._tmux_sessions = lambda: {SID: dict(base, bgTasks=[{"task_id": "t1", "desc": "Watch for round3 copy"}])}
        lane = self._lane()
        self.assertEqual((lane["awaitingKind"], lane["awaitingCount"]), ("task", 1))
        km._tmux_sessions = lambda: {SID: dict(base, bgTasks=[{"task_id": "t1", "desc": "Watch for round3 copy"},
                                                              {"task_id": "t2", "desc": "poll the deploy"}])}
        self.assertEqual(self._lane()["awaitingCount"], 2)
        km._tmux_sessions = lambda: {SID: dict(base, subagents=[{"type": "explore", "since": T0 + 5}])}
        lane = self._lane()
        self.assertEqual((lane["awaitingKind"], lane["awaitingCount"]), ("agents", 1), "one agent → the badge reads singular")
        km._tmux_sessions = lambda: {SID: dict(base)}
        self.states.write_text(json.dumps({"t": T0 + 21, "awaiting": True, "why": "bg agents"}) + "\n")
        lane = self._lane()
        self.assertEqual(lane["awaitingBg"], "bg agents")
        self.assertIsNone(lane["awaitingCount"], "a bare overlay row names no count — never parsed from the why")

    def test_overlay_flavor_awaiting_carries_no_task_rows(self):
        self.states.write_text(json.dumps({"t": T0 + 21, "awaiting": True, "why": "bg agents"}) + "\n")
        self.assertEqual(self._lane()["awaitingTasks"], [], "no live tasks → the stretch hover falls back to the why")


class TimelineLiveTail(TimelineAwaiting):
    """build_timeline merges the LIVE TAIL like the chat (the user 2026-07-02): a /model change streams
    the CLI's confirmation as a live command atom, but the CLI persists no transcript record until a
    later turn writes the file — the timeline's dot appeared only RETROACTIVELY while the chat showed
    the event at once. Reuses the TimelineAwaiting sandbox (setUp/tearDown)."""

    def _fake_backend(self, atoms):
        class FakeBE:
            def live_atoms(self, sid):
                return list(atoms)

            def prune_live(self, *a, **k):
                pass
        return FakeBE()

    def test_live_command_atoms_land_a_dot_immediately(self):
        # the SDK backend's synthetic /model INVOCATION atom (a segment opener) + the CLI's streamed
        # confirmation — exactly what setModel puts in the live tail (romp_sdk_backend.set_model).
        inv = {"type": "user", "uuid": "cmd:1:model", "t": NOW - 20, "author": "human", "command": "/model",
               "_echo_text": "/model sonnet", "session_id": SID, "fsid": None, "parentUuid": None,
               "message": {"role": "user", "content": [{"type": "text", "text": "/model sonnet"}]}}
        out = {"type": "assistant", "uuid": "live-c1", "t": NOW - 19, "command": True,
               "session_id": SID, "fsid": None, "parentUuid": None,
               "message": {"role": "assistant", "content": [{"type": "text", "text": "Set model to sonnet"}],
                           "stop_reason": "end_turn"}}
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = lambda sid: self._fake_backend([inv, out])
        try:
            tl = km.build_timeline(NOW, with_bars=True)
            bars = tl["turns"].get(SID) or []
            live_bar = next((b for b in bars if b["start"] >= NOW - 21), None)
            self.assertIsNotNone(live_bar, "the /model invocation forms a segment NOW, not after a later "
                                 "disk write: %r" % [(b["start"], b["end"]) for b in bars])
            self.assertEqual(live_bar["promptId"], "cmd:1:model", "the dot anchors on the invocation atom")
            lane = next(l for l in tl["sessions"] if l["id"] == SID)
            self.assertNotEqual(lane["state"], "working",
                                "a merged command exchange never flips the lane to working")
        finally:
            km.Sessions.backend_for = saved

    def test_dead_lane_skips_the_live_merge(self):
        km._tmux_sessions = lambda: {}                   # session process gone
        called = []
        saved = km.Sessions.backend_for
        km.Sessions.backend_for = lambda sid: (called.append(sid), self._fake_backend([]))[1]
        try:
            km.build_timeline(NOW, with_bars=True)
            self.assertNotIn(SID, called, "no live process → no live tail to merge")
        finally:
            km.Sessions.backend_for = saved


class CommandTailNeutral(unittest.TestCase):
    """_session_working: trailing COMMAND atoms are neutral (the user 2026-07-02) — a /model confirmation
    merged AFTER the tail idle span must not hide the idle and read the turn as working again."""

    def test_command_after_idle_tail_stays_not_working(self):
        km._downtime[:] = []
        turns = [{"id": "t", "t": NOW - 100, "end": NOW, "ended": False, "trigger": None,
                  "atoms": [{"type": "user", "t": NOW - 100},
                            {"type": "idle", "t": NOW - 50},
                            {"type": "assistant", "t": NOW - 10, "command": True}]}]
        self.assertFalse(km._session_working(turns), "the idle tail still counts through a trailing command atom")

    def test_genuinely_open_turn_still_reads_working(self):
        km._downtime[:] = []
        turns = [{"id": "t", "t": NOW - 100, "end": NOW, "ended": False, "trigger": None,
                  "atoms": [{"type": "user", "t": NOW - 100},
                            {"type": "assistant", "t": NOW - 10}]}]
        self.assertTrue(km._session_working(turns))


if __name__ == "__main__":
    unittest.main()
