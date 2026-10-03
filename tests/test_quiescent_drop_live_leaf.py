#!/usr/bin/env python3
"""The quiescence drop and a LIVE session's main transcript (2026-10-03).

The agent-launch fold runs over every main transcript with drop_after="quiescent" (T361/T362), so a leaf unchanged for
_DROP_AFTER_QUIESCENT_S whose new records that fold steps leaves the record cache, and the next read restores only its tail
from the checkpoint document. That is right for a file nobody folds again. It is wrong for a live session's leaf when one
of the folds over it cannot take its state back from the document: the every-task background view (bgAll) on a long
transcript is over the document's cap, so its cursor is written without a state, and the jobs pass folds it for EVERY
live session on EVERY pass (_lift_spent_awaiting). Its next run after the pop refolds from record 0 over a tail entry,
which reads the whole transcript again: on a devbox, 101 such reads of 89 GB in 16 hours, plus the auto-nudge and
interrupt parses that upgraded the same tail entries (48 more, 48 GB), each a whole-file parse on the jobs thread.

Pinned here, on synthetic transcripts under a temp root:
  - a live session's leaf whose background view is over the cap stays whole at the drop, and the next jobs pass's folds
    read nothing of it;
  - the same leaf with no live session behind it is dropped exactly as before;
  - a leaf kept for its live session is dropped at the first cycle start after the session leaves the live map, with the
    document write and the drop counters as before;
  - a live leaf whose every fold restores from the document is still dropped (T362 stands where the drop is cheap), and
    so is a live session's subagent transcript;
  - the jobs pass publishes the live leaves before its first job.
"""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_qdrop_live", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-000000000951"
TS0 = 1_800_000_000
CAP = 4 * 1024             # the test's document cap: the background view of LAUNCHES tasks is over it (about 15 KB), every
LAUNCHES = 40              #  other leaf fold far under it (under 1 KB), the shape the live cap of 8 MiB has on a long transcript


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _launch(i, parent, t):
    return {"type": "assistant", "uuid": "a%d" % i, "parentUuid": parent, "timestamp": _iso(t), "cwd": "/w/notes-api",
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [
                {"type": "tool_use", "id": "toolu_bg%d" % i, "name": "Bash",
                 "input": {"command": "make test # " + "x" * 200, "run_in_background": True, "description": "suite %d" % i}}]}}


def _note(i, t):
    body = ("<task-notification>\n<task-id>b%d</task-id>\n<tool-use-id>toolu_bg%d</tool-use-id>\n<status>completed</status>\n"
            "<summary>done</summary>\n</task-notification>" % (i, i))
    return {"type": "user", "uuid": "n%d" % i, "parentUuid": "a%d" % i, "timestamp": _iso(t), "cwd": "/w/notes-api",
            "message": {"role": "user", "content": body}}


def _leaf_records(lo, hi):
    out = [] if lo else [{"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": _iso(TS0), "cwd": "/w/notes-api",
                          "message": {"role": "user", "content": "run the suites"}}]
    parent = "u0" if lo == 0 else "n%d" % (lo - 1)
    for i in range(lo, hi):
        out += [_launch(i, parent, TS0 + 2 * i + 1), _note(i, TS0 + 2 * i + 2)]
        parent = "n%d" % i
    return out


def _write(path, recs, mode="w"):
    with open(path, mode) as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _age(path, seconds=600):
    old = time.time() - seconds
    os.utime(path, (old, old))


class Base(unittest.TestCase):
    def setUp(self):
        self.saved_state = jd.STATE
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td / "state")
        jd.STATE.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")         # a state root this test minted: no real session host (CLAUDE.md)
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        self.proj = self.td / "proj"; self.proj.mkdir()
        self.leaf = str(self.proj / (SID + ".jsonl"))
        sub = self.proj / SID / "subagents"; sub.mkdir(parents=True)
        self.agent = str(sub / "agent-aaaa.jsonl")
        saved_cap = em._CKPT_FOLD_CAP
        self.addCleanup(setattr, em, "_CKPT_FOLD_CAP", saved_cap)
        saved_sessions = km._sessions
        km._sessions = lambda now, **kw: [{"sid": SID, "path": self.leaf, "name": "web", "anchor": self.leaf, "mtime": 0}]
        self.addCleanup(setattr, km, "_sessions", saved_sessions)
        self.fresh_process()
        self.addCleanup(self.jobs_pass, {})                         # no live leaf outlives the test

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")

    def fresh_process(self):
        """A kernel restart's in-memory side: reader entries, fold cursors, restores, byte counters and cold marks gone."""
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
            em._JSONL_CACHE_BYTES[0] = 0
        em._TRAILING_CACHE.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        for c in list(em._FOLD_REG.values()):
            c.clear()
        with em._CKPT_LOCK:
            em._COLD_FOLDS.clear(); em._COLD_REASONS.clear(); em._COLD_OVER_KB.clear()
        em._CKPT_STATS["converge"] = {k: 0 for k in em._CKPT_STATS["converge"]}

    def jobs_pass(self, live_map):
        """One jobs pass over `live_map` with every job stubbed out: what the pass does around its jobs, and no job."""
        saved = km._job_stage
        km._job_stage = lambda name, thunk: None
        try:
            km._jobs_pass(int(time.time()), live_map)
        finally:
            km._job_stage = saved

    def leaf_folds(self):
        """What the jobs pass and the pusher's checkpoint stage fold over a main transcript (the five leaf folds)."""
        for fn, _cache in km._LEAF_FOLDS():
            fn(self.leaf)

    def read_of_leaf(self):
        return em.read_bytes_report().get(self.leaf, 0)

    def resident(self, path):
        with em._JSONL_CACHE_LOCK:
            return em._JSONL_CACHE.get(path)

    def settled_then_quiet(self, live, cap=CAP):
        """A long leaf, folded whole and written at a settle (the background view over `cap` is written as a cursor without
        a state), then a new burst of records the jobs pass folds, then quiet past the window: the agent-launch fold's next
        run steps the burst over a quiescent file, which is the drop. `live` is the live map the jobs pass saw."""
        em._CKPT_FOLD_CAP = cap
        _write(self.leaf, _leaf_records(0, LAUNCHES))
        self.jobs_pass({SID: {"state": "working"}} if live else {})
        self.leaf_folds()                                           # the boot's whole read, every leaf fold current
        self.assertTrue(em.checkpoint_write(self.leaf))             # the settle's write
        _write(self.leaf, _leaf_records(LAUNCHES, LAUNCHES + 2), mode="a")
        _age(self.leaf)                                             # the session went quiet after the burst: a long command, or a
        km._bg_scan_all_cached(self.leaf); jd._bg_scan(self.leaf)   #  wait on the user; the jobs pass's folds step the burst (the
        km._bg_scan_cached(self.leaf); km._session_meta(self.leaf)  #  entry carries the quiet mtime, as a read after the last write)
        self.dropped0 = em.record_cache_stats()["dropped"]
        self.kept0 = em.record_cache_stats().get("dropKept", 0)     # process-wide counters: each test reads its own delta
        km._agent_launch_state(self.leaf)                           # the checkpoint stage's launch fold steps the burst: the drop
        return os.path.getsize(self.leaf)


class LiveLeafOverTheCap(Base):

    def test_the_precondition_only_the_background_view_is_over_the_cap(self):
        self.settled_then_quiet(live=False)
        doc = json.loads(em._ckpt_file(self.leaf).read_text())
        over = sorted(n for n, f in doc["folds"].items() if "state" not in f)
        self.assertEqual(over, ["bgAll"], "the shape the devbox has: bgAll alone over the cap (%s)" % doc["folds"].keys())

    def test_a_live_sessions_leaf_stays_whole_and_the_next_jobs_pass_reads_none_of_it(self):
        size = self.settled_then_quiet(live=True)
        self.assertTrue(em.entry_whole_resident(self.leaf),
                        "a live leaf whose background view cannot restore from its document keeps its whole entry at the drop")
        self.assertEqual(em.record_cache_stats()["dropped"], self.dropped0, "nothing dropped")
        r0 = self.read_of_leaf()
        self.jobs_pass({SID: {"state": "waiting"}})
        km._bg_scan_all_cached(self.leaf)                           # the lift's fold, every live session, every pass
        self.leaf_folds()
        self.assertLess(self.read_of_leaf() - r0, size / 2,
                        "the next pass folds over records in hand: no whole re-read of the transcript (%d of %d bytes)"
                        % (self.read_of_leaf() - r0, size))
        self.assertEqual(em.record_cache_stats().get("dropKept", 0) - self.kept0, 1, "the kept drop is counted once on /perf")
        self.assertEqual(em.record_cache_stats().get("keptWhole"), 1, "and the leaf it holds is a gauge beside it")

    def test_the_background_view_stays_complete_and_current(self):
        self.settled_then_quiet(live=True)
        km._agent_launch_state(self.leaf)                           # a later checkpoint stage over the unchanged leaf
        _write(self.leaf, _leaf_records(LAUNCHES + 2, LAUNCHES + 3), mode="a")
        rows = km._bg_scan_all_cached(self.leaf)
        self.assertEqual(sorted(r["id"] for r in rows), sorted("toolu_bg%d" % i for i in range(LAUNCHES + 3)),
                         "every launch from record 0 on, the burst and the new one included: an append, never a tail-only state")

    def test_the_same_leaf_with_no_live_session_is_dropped_as_before(self):
        size = self.settled_then_quiet(live=False)
        self.assertIsNone(self.resident(self.leaf), "no live session folds it every pass: the drop releases it (T361/T362)")
        self.assertEqual(em.record_cache_stats()["dropped"], self.dropped0 + 1)
        self.assertEqual(em.record_cache_stats().get("dropKept", 0) - self.kept0, 0)
        self.assertGreater(size, 0)

    def test_a_kept_leaf_is_dropped_at_the_first_cycle_start_after_its_session_leaves(self):
        self.settled_then_quiet(live=True)
        em.checkpoint_pay_owed_drops()                              # a cycle start while the session is live: kept
        self.assertTrue(em.entry_whole_resident(self.leaf))
        self.jobs_pass({})                                          # the session ended: no longer in the live map
        em.checkpoint_pay_owed_drops()                              # the next pusher cycle's start
        self.assertIsNone(self.resident(self.leaf), "released once nothing folds it every pass")
        st = em.record_cache_stats()
        self.assertEqual((st["dropped"], st.get("keptWhole")), (self.dropped0 + 1, 0), "%s" % st)

    def test_a_kept_leaf_never_falls_out_through_the_owed_table_bound(self):
        self.settled_then_quiet(live=True)
        saved = em._DROP_OWED_MAX
        em._DROP_OWED_MAX = 1                                       # the bound, reached by one more owed drop
        self.addCleanup(setattr, em, "_DROP_OWED_MAX", saved)
        saved_take = em.checkpoint_cycle_take
        em.checkpoint_cycle_take = lambda n: False                  # the cycle's budget spent: the next drop is deferred
        self.addCleanup(setattr, em, "checkpoint_cycle_take", saved_take)
        self.addCleanup(em._FOLD_REG.pop, "probe", None)
        other = str(self.proj / "other.jsonl")
        _write(other, _leaf_records(0, 2)); _age(other)
        em.fold_records({}, other, list, lambda st, o: st + [1], ckpt="probe", drop_after="quiescent")
        self.assertTrue(em.entry_whole_resident(self.leaf), "over the bound the oldest UNKEPT mark is paid, never the live leaf")
        self.assertIsNone(self.resident(other), "here the other file's own, paid by its pop")


class DropsThatStand(Base):

    def test_a_live_leaf_whose_folds_all_restore_from_the_document_is_still_dropped(self):
        size = self.settled_then_quiet(live=True, cap=8 * 1024 * 1024)   # the live cap: every fold's state fits
        self.assertIsNone(self.resident(self.leaf), "every fold restores warm: the drop is cheap and stands (T362)")
        r0 = self.read_of_leaf()
        km._bg_scan_all_cached(self.leaf)
        self.assertLess(self.read_of_leaf() - r0, size / 2, "the restore reads the tail only")

    def test_a_live_sessions_subagent_file_is_still_dropped(self):
        em._CKPT_FOLD_CAP = CAP
        _write(self.leaf, _leaf_records(0, 2))
        _write(self.agent, [{"type": "user", "uuid": "s1", "parentUuid": None, "timestamp": _iso(TS0 + 5), "message": {"role": "user", "content": "look"}},
                            {"type": "assistant", "uuid": "s2", "parentUuid": "s1", "timestamp": _iso(TS0 + 6),
                             "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_s1", "name": "Read",
                                                                           "input": {"file_path": "/w/notes-api/web/app.ts"}}]}}])
        _age(self.agent)
        self.jobs_pass({SID: {"state": "working"}})
        km._agent_steps(self.agent)                                 # the gist fold: a whole read, then the drop
        self.assertIsNone(self.resident(self.agent), "a returned agent's file leaves memory as before")


class Wiring(Base):

    def test_the_jobs_pass_publishes_the_live_leaves_before_its_first_job(self):
        seen = []
        saved = km._job_stage
        km._job_stage = lambda name, thunk: seen.append((name, set(em.keep_whole_paths())))
        try:
            km._jobs_pass(int(time.time()), {SID: {"state": "working"}})
        finally:
            km._job_stage = saved
        self.assertEqual(seen[0], ("liftSpentAwaiting", {self.leaf}))
        self.jobs_pass({})
        self.assertEqual(set(em.keep_whole_paths()), set(), "a session gone from the live map is gone from the set")

    def test_a_dormant_entry_in_the_live_map_is_not_live(self):
        self.jobs_pass({SID: None})
        self.assertEqual(set(em.keep_whole_paths()), set(), "the lift's own rule: a sid mapped to nothing is dormant")


if __name__ == "__main__":
    unittest.main()
