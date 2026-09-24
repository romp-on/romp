"""A session that enters a Claude Code worktree relocates its transcript to the WORKTREE cwd's
projects dir — the launch-dir walk then finds nothing, and every surface read a WORKING session as
'opening' / dropped it from the list (the user 2026-08-20). The CLI itself reports where it writes
(every hook payload carries transcript_path); the Stop hook records it and discovery prefers it.
Re-ported onto v0.15 2026-09-15. Review fixes 2026-09-21: the recorded leaf is noted like a walked one
(one tree per session), the recorded file's DIRECTORY mtime is signed (a /clear's new file re-runs
discover), and the worktree tools record the path the moment the call returns (not at turn end).
Synthetic fixtures only (placeholder sids, /TESTDIR paths)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
KDIR = os.path.join(os.path.dirname(HERE), "kernel")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load — judge.py resolves its state root at import time, and only pytest
# runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_wtt", os.path.join(KDIR, "judge.py"))
sb = load_source("romp_sdk_backend_wtt", os.path.join(KDIR, "sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
FORK = "22222222-3333-4444-5555-666666666666"


class DiscoverFollowsTheRecordedPath(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self._saved = (jd.NAMES, jd.SDKDIR)
        jd.NAMES = base / "names"; jd.NAMES.mkdir()
        jd.SDKDIR = base / "sdk"; jd.SDKDIR.mkdir()
        # the LAUNCH dir's project folder — EMPTY (the CLI moved the transcript away)
        self.launch_proj = base / "projects" / "-repo"
        self.launch_proj.mkdir(parents=True)
        # the WORKTREE dir's project folder — where the transcript actually lives
        self.wt_proj = base / "projects" / "-repo--claude-worktrees-syn"
        self.wt_proj.mkdir(parents=True)
        self.moved = self.wt_proj / (SID + ".jsonl")
        self.moved.write_text(json.dumps({"type": "user", "uuid": "u1",
                                          "message": {"role": "user", "content": "hi"}}) + "\n")
        (jd.NAMES / SID).write_text("web\t/repo\t#888888\tblack")
        self._saved_pd = jd._proj_dir
        jd._proj_dir = lambda cdir: str(self.launch_proj)   # launch cwd → the EMPTY dir
        self._reset()

    def tearDown(self):
        jd.NAMES, jd.SDKDIR = self._saved
        jd._proj_dir = self._saved_pd
        self._reset()
        self.td.cleanup()

    def _reset(self):
        for memo in (jd._lastsid_memo, jd._recpath_memo, jd._namefp_memo):
            memo.clear()
        jd._discover_cache.clear()
        jd._LEAF_SEEN.clear(); jd._LEAF_RETIRED.clear()   # the leaves discover handed out — module globals
        jd.parse_cache_clear()

    def _reg(self, **extra):
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "alive": True, **extra}))
        # the registry reads are mtime-memoized; a second write inside one filesystem timestamp tick would
        # be served stale, and the memo is not what these tests are about
        jd._lastsid_memo.clear(); jd._recpath_memo.clear()

    def _touch(self, p, delta=10):
        """Move an mtime EXPLICITLY (the fingerprint memo test's idiom): a write inside one filesystem
        timestamp tick would otherwise be indistinguishable, and the test is about the signal."""
        st = os.stat(p)
        os.utime(p, (st.st_atime + delta, st.st_mtime + delta))

    def test_without_the_record_the_session_is_lost(self):
        self._reg()
        got = jd._discover_impl(int(self.moved.stat().st_mtime) + 10, window=3600)
        self.assertEqual(got, [], "the launch-dir walk cannot see a relocated transcript (the bug)")

    def test_the_recorded_path_resolves_the_relocated_transcript(self):
        self._reg(transcriptPath=str(self.moved))
        got = jd._discover_impl(int(self.moved.stat().st_mtime) + 10, window=3600)
        self.assertEqual(len(got), 1)
        fsid, path, anchor, name = got[0]
        self.assertEqual((fsid, str(path), name), (SID, str(self.moved), "web"))

    def test_a_clear_fork_after_relocation_follows_lastSid_in_the_new_dir(self):
        fork = self.wt_proj / "22222222-3333-4444-5555-666666666666.jsonl"
        fork.write_text(self.moved.read_text())
        self._reg(transcriptPath=str(self.moved), lastSid="22222222-3333-4444-5555-666666666666")
        got = jd._discover_impl(int(fork.stat().st_mtime) + 10, window=3600)
        self.assertEqual(str(got[0][1]), str(fork), "the fork resolves in the RECORDED file's dir")

    def test_a_stale_record_falls_back_to_the_walk(self):
        anchor = self.launch_proj / (SID + ".jsonl")
        anchor.write_text(self.moved.read_text())
        self._reg(transcriptPath=str(self.wt_proj / "gone.jsonl"))   # recorded but deleted
        got = jd._discover_impl(int(anchor.stat().st_mtime) + 10, window=3600)
        self.assertEqual(str(got[0][1]), str(anchor), "a dead record never hides the launch-dir file")

    def test_the_recorded_leaf_is_noted_and_a_clear_retires_the_previous_one(self):
        """One tree per session (2026-09-11): discover notes the leaf it hands out, and a flip drops the previous
        leaf's parse slots. The recorded-path branch skipped the note while both walk branches make it, and every
        SDK session carries a record after its first Stop, so every /clear of one left the pre-clear tree resident
        until the store's LRU evicted it (review fix)."""
        self._reg(transcriptPath=str(self.moved))
        now = int(self.moved.stat().st_mtime) + 10
        got = jd._discover_impl(now, window=3600)
        self.assertEqual(str(got[0][1]), str(self.moved))
        self.assertEqual(jd._LEAF_SEEN.get(SID), str(self.moved), "the recorded leaf is noted like a walked one")
        jd._parse_store(SID, "", "k1", object(), str(self.moved), False)   # a tree parsed under the pre-clear leaf
        self.assertEqual(len([k for k in jd._PARSE_CACHE if k[0] == SID]), 1)
        fork = self.wt_proj / (FORK + ".jsonl")
        fork.write_text(self.moved.read_text())                            # the /clear's new transcript lands
        self._reg(transcriptPath=str(self.moved), lastSid=FORK)
        got = jd._discover_impl(now, window=3600)
        self.assertEqual(str(got[0][1]), str(fork))
        self.assertEqual(jd._LEAF_SEEN.get(SID), str(fork), "the flip is noted at the read that hands out the new leaf")
        self.assertTrue(jd._leaf_retired(SID, str(self.moved)), "the pre-clear leaf is retired")
        self.assertEqual([k for k in jd._PARSE_CACHE if k[0] == SID], [], "...and its tree is dropped")

    def test_the_new_transcript_landing_in_the_recorded_dir_moves_the_fingerprint(self):
        """A /clear in a relocated session flips lastSid BEFORE the CLI writes the new file: discover falls back to
        the recorded (pre-clear) file and caches the list. The new file's arrival bumps only the recorded file's
        DIRECTORY mtime, which the fingerprint never signed (it signs the launch dir's), so every surface stayed on
        the pre-clear transcript until the next Stop hook re-recorded the path (review fix)."""
        fork = self.wt_proj / (FORK + ".jsonl")
        self._reg(transcriptPath=str(self.moved), lastSid=FORK)   # the fork is named, not yet on disk
        now = int(self.moved.stat().st_mtime) + 10
        a = jd.discover(now, window=3600)
        self.assertEqual(str(a[0][1]), str(self.moved), "the race window: the recorded file stands in")
        fp_a = jd._discover_fingerprint()
        fork.write_text(self.moved.read_text())                   # the CLI writes the new transcript...
        self._touch(self.wt_proj)                                 # ...its dir's mtime moves; the launch dir's does not
        fp_b = jd._discover_fingerprint()
        self.assertNotEqual(fp_a, fp_b, "the recorded file's DIRECTORY mtime is signed")
        b = jd.discover(now, window=3600)
        self.assertEqual(str(b[0][1]), str(fork), "...so discover re-runs and resolves the new file")

    def test_the_fingerprint_signs_the_record(self):
        src = open(os.path.join(KDIR, "judge.py")).read()
        self.assertIn('_sdk_transcript_path(f.name) or ""', src,
                      "a relocation must bust the discover cache the moment it lands")
        self.assertIn("_mtime_or_none(os.path.dirname(rec))", src,
                      "the recorded file's directory mtime is signed beside the launch dir's")
        sbsrc = open(os.path.join(KDIR, "sdk_backend.py")).read()
        self.assertIn('self.backend._update_reg(self.sid, transcriptPath=str(tp))', sbsrc,
                      "the hooks record the CLI-reported path")
        self.assertIn('HookMatcher(matcher="EnterWorktree|ExitWorktree"', sbsrc,
                      "the worktree tools' PostToolUse hook is registered beside the others")


class TheWorktreeToolsRecordThePath(unittest.TestCase):
    """The CLI renames the transcript INSIDE the EnterWorktree / ExitWorktree call, and the record was written only
    at the turn's Stop hook, so for the whole turn that entered the worktree the record was absent (or named the
    moved-away file and fell to the walk) and the session stayed invisible (review fix): a PostToolUse hook on the
    two tools records the payload's transcript_path the moment the call returns, through the helper the Stop hook
    shares. Hermetic: a backend on a temp state dir, no CLI."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})
        self.sess = sb.SdkSession(self.be, sb.read_reg(self.d, SID))
        self.moved = "/TESTDIR/projects/-repo--claude-worktrees-syn/%s.jsonl" % SID
        self.home = "/TESTDIR/projects/-repo/%s.jsonl" % SID

    def _reg(self):
        return sb.read_reg(self.d, SID) or {}

    def _tool(self, tname, tp, tuid="toolu_01WT"):
        asyncio.run(self.sess._worktree_tool_hook(
            {"hook_event_name": "PostToolUse", "tool_name": tname, "tool_use_id": tuid,
             "transcript_path": tp, "tool_input": {"name": "syn"}, "tool_response": {"ok": True}}, tuid, None))

    def test_entering_a_worktree_records_the_relocated_path_before_the_turn_ends(self):
        self._tool("EnterWorktree", self.moved)
        self.assertEqual(self._reg().get("transcriptPath"), self.moved, "recorded at the call, not at turn end")
        self.assertFalse([m for m in self.logs if "transcriptPath" in str(m)], "the record did not fail")

    def test_leaving_moves_the_record_back_and_the_stop_hook_shares_the_dedupe(self):
        calls = []
        orig = self.be._update_reg

        def counting(sid, **fields):
            calls.append(sorted(fields))
            return orig(sid, **fields)

        self.be._update_reg = counting
        self._tool("EnterWorktree", self.moved, "toolu_01A")
        self._tool("ExitWorktree", self.home, "toolu_01B")
        self.assertEqual(self._reg().get("transcriptPath"), self.home, "leaving the worktree moves the record back")
        self.assertEqual(calls.count(["transcriptPath"]), 2)
        asyncio.run(self.sess._stop_hook({"transcript_path": self.home}, None, None))
        self.assertEqual(calls.count(["transcriptPath"]), 2,
                         "the Stop hook reads the same record and skips an unchanged path")
        asyncio.run(self.sess._stop_hook({"transcript_path": self.moved}, None, None))
        self.assertEqual(self._reg().get("transcriptPath"), self.moved, "...and writes a changed one")

    def test_a_payload_without_the_field_records_nothing(self):
        asyncio.run(self.sess._worktree_tool_hook({"tool_name": "EnterWorktree"}, "toolu_01C", None))
        self.assertNotIn("transcriptPath", self._reg(), "absence is not a location")


if __name__ == "__main__":
    unittest.main()
