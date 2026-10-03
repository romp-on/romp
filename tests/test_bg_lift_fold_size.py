#!/usr/bin/env python3
"""The awaiting-stamp lift's background-task fold must fit in the fold checkpoint (2026-10-03).

The lift (_lift_spent_awaiting) folds every background task a transcript records, for every alive session, on every
pusher cycle, before its skip check (the skip's fingerprint is built from the fold). It read the every-task view
(_bg_scan_all_cached, checkpoint name bgAll), whose rows carry each agent's closing report (up to 16,000 characters),
each command or prompt, the summaries and the output paths. On a long transcript that state is over the checkpoint's
per-fold cap (8 MiB), so the document records bgAll's cursor without its state. When the reader's entry for the
transcript leaves memory (the quiescent drop, an eviction), the next fold cannot restore from the document and reads
the whole transcript again. Measured live: 97 such reads in 15.7 hours, 85.5 GB, about 0.88 GB each, over half of all
whole-read bytes the kernel pulled.

The lift needs only id, status, t, endT, type, deadline and monitor per task, so it now reads its own fold (bgLift)
that keeps those fields and nothing else; the every-task view stays as it was for the chat build's agent reports, the
task-output view and the feed's top-goal heal. Pinned here:
  - after a drop, the lift's next fold restores from the checkpoint and reads the tail, not the whole transcript;
  - on a transcript whose every-task state is over the cap, the lift's state is far under it;
  - the lift's rows are exactly the every-task rows narrowed to the lift's fields, across every launch and return shape;
  - the lift's state grows by under 200 bytes per task, so the cap is reached only past 40,000 tasks;
  - a cache built for one view refuses to answer the other.
Hermetic: synthetic records under a temp root; placeholder ids only."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_bglift", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-000000000501"
TS0 = 1_800_000_000
LIFT_KEYS = {"id", "status", "t", "endT", "type", "deadline", "monitor"}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _tid(i):
    return "toolu_%024d" % i                                    # the harness's id length


def _user(content, uuid, t, **kw):
    r = {"type": "user", "uuid": uuid, "timestamp": _iso(t), "message": {"role": "user", "content": content}}
    r.update(kw)
    return r


def _assistant(blocks, uuid, t):
    return {"type": "assistant", "uuid": uuid, "timestamp": _iso(t), "message": {"role": "assistant", "content": blocks}}


def _note(tid, status="completed", summary="done", result=None, with_status=True):
    return ("<task-notification>\n<task-id>x</task-id>\n<tool-use-id>%s</tool-use-id>\n%s<summary>%s</summary>\n%s"
            "</task-notification>" % (tid, ("<status>%s</status>\n" % status) if with_status else "", summary,
                                      ("<result>%s</result>\n" % result) if result is not None else ""))


def _big_transcript(n_agents, n_shell, prompt_chars=4000, result_chars=16000, command_chars=2000):
    """A long-lived session's shape: background agents with long briefs and long closing reports, and background shell
    commands with long command lines, each launched and returned."""
    prompt = ("check the notes-api width handling and report back " * (prompt_chars // 50 + 1))[:prompt_chars]
    result = ("the web layer clamps the width before the api sees it; " * (result_chars // 55 + 1))[:result_chars]
    command = ("python3 tools/bench.py --case width --repeat 3 && " * (command_chars // 50 + 1))[:command_chars]
    recs, t, i = [], TS0, 0
    for k in range(n_agents):
        i += 1; t += 1
        recs.append(_assistant([{"type": "tool_use", "id": _tid(i), "name": "Agent",
                                 "input": {"description": "review part %d" % k, "prompt": prompt, "run_in_background": True}}],
                               "a%d" % i, t))
        recs.append(_user(_note(_tid(i), summary="Agent finished part %d" % k, result=result), "n%d" % i, t + 0.5))
    for k in range(n_shell):
        i += 1; t += 1
        recs.append(_assistant([{"type": "tool_use", "id": _tid(i), "name": "Bash",
                                 "input": {"command": command, "description": "bench %d" % k, "run_in_background": True}}],
                               "a%d" % i, t))
        recs.append(_user(_note(_tid(i), summary="bench %d done" % k), "n%d" % i, t + 0.5))
    return recs


def _write(path, recs, age_s=0):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    if age_s:
        old = time.time() - age_s                               # unchanged for longer than the quiescence window
        os.utime(path, (old, old))


def _encoded_bytes(state):
    return len(json.dumps(em._ckpt_encode(state), separators=(",", ":")))


class Base(unittest.TestCase):
    def setUp(self):
        self.saved_state = jd.STATE
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td / "state")
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")        # a minted state root pins the per-session hosts off
        self.fresh_process()
        em._CKPT_STATS["refolds"].clear()                      # reset, never injected
        em._CKPT_STATS.update(restored=0, writes=0, skippedFolds=0, fallbacks={}, restoredFolds={}, droppedRestores=0,
                              oversizeFolds={}, coldFolds={}, coldWrites={})
        self.saved_alive = km._alive_sessions
        self.leaf = str(self.td / (SID + ".jsonl"))
        km._alive_sessions = lambda now, live_map: [{"sid": SID, "path": self.leaf}]
        km._lift_seen.clear()

    def tearDown(self):
        km._alive_sessions = self.saved_alive
        km._lift_seen.clear()
        jd._rebind_state(self.saved_state)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")

    def fresh_process(self):
        """What a kernel restart does to the in-memory side; the checkpoint files on disk stay."""
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
        km._CKPT_SETTLE_SEEN.clear()

    def lift(self, now):
        km._lift_spent_awaiting(now, {SID: {"state": "idle", "bgTasks": []}})

    def read_of_leaf(self):
        return em.read_bytes_report().get(self.leaf, 0)

    def doc(self):
        return json.loads(em._ckpt_file(self.leaf).read_text())


class LiftReadsAfterADrop(Base):
    def test_after_a_quiescent_drop_the_lift_restores_from_the_checkpoint_and_reads_only_the_tail(self):
        """The live shape: an idle long transcript (unchanged past the quiescence window). The lift folds it every cycle;
        a quiescent-drop fold over the same leaf (its agent-launch state) writes the checkpoint and pops the reader's entry.
        The lift's next fold must restore from that document. Before the fix the lift's fold was over the cap, recorded as
        a cursor without a state, and its next fold read the whole transcript again."""
        _write(self.leaf, _big_transcript(n_agents=500, n_shell=300), age_s=600)
        size = os.path.getsize(self.leaf)
        self.lift(TS0 + 10_000)                                 # the boot's first cycle: a whole read, as before
        self.assertGreaterEqual(self.read_of_leaf(), size)
        km._agent_launch_state(self.leaf)                       # a drop_after="quiescent" fold: writes the document, pops
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "the drop popped the reader's entry")
        self.assertTrue(em._ckpt_file(self.leaf).exists(), "and wrote the leaf's checkpoint first")
        before = self.read_of_leaf()
        refolds = {k: dict(v) for k, v in em.checkpoint_stats()["refolds"].items()}   # the boot's whole read is one
        self.lift(TS0 + 10_001)                                 # the next cycle
        got = self.read_of_leaf() - before
        self.assertLess(got, size // 100,
                        "the lift's fold restores from the checkpoint and reads the tail; it read %d of %d bytes" % (got, size))
        self.assertEqual(em.checkpoint_stats()["refolds"], refolds, "no fold refolded the transcript whole after the drop")
        restored = em.checkpoint_stats()["restoredFolds"]
        self.assertTrue(restored, "the lift's fold was restored from the document: %s" % restored)

    def test_every_fold_the_lift_runs_leaves_its_state_in_the_checkpoint(self):
        """What the document records after a cycle of the lift alone: every fold it ran carries a state, none a bare
        over-the-cap cursor (an over-the-cap fold is what reads whole after a drop)."""
        _write(self.leaf, _big_transcript(n_agents=500, n_shell=300))
        self.lift(TS0 + 10_000)
        self.assertTrue(em.checkpoint_write(self.leaf))
        folds = self.doc()["folds"]
        self.assertTrue(folds, "the lift ran at least one fold over the leaf")
        over = sorted(n for n, f in folds.items() if "state" not in f)
        self.assertEqual(over, [], "folds recorded without their state: %s (%s)"
                         % (over, {n: folds[n].get("over") for n in over}))


class LiftFoldSize(Base):
    def test_the_lift_state_is_far_under_the_cap_where_the_every_task_state_is_over_it(self):
        _write(self.leaf, _big_transcript(n_agents=500, n_shell=300))
        fat = em.fold_records({}, self.leaf, lambda: em._bg_fresh(True), em._bg_step)
        km._bg_scan_lift_cached(self.leaf)
        slim = km._bglift_cache[self.leaf][2]
        self.assertGreater(_encoded_bytes(fat), em._CKPT_FOLD_CAP, "the fixture reproduces the live shape: over the cap")
        self.assertLess(_encoded_bytes(slim), em._CKPT_FOLD_CAP // 50, "the lift's state is a small fraction of the cap")
        km._bg_scan_all_cached(self.leaf)
        self.assertTrue(em.checkpoint_write(self.leaf))
        folds = self.doc()["folds"]
        self.assertIn("over", folds["bgAll"], "the every-task view is still over the cap (unchanged: its rows feed the chat)")
        self.assertIn("state", folds["bgLift"], "the lift's fold is written with its state")

    def test_the_lift_state_grows_by_under_200_bytes_a_task(self):
        """A bound on growth beside the size test: under 200 bytes a task in the encoded state, so the 8 MiB cap is
        reached only past 40,000 tasks on one transcript. Built in memory through the fold's own step."""
        n = 4000
        state = em._bg_fresh(True, slim=True)
        for i in range(n):
            tid = _tid(i)
            em._bg_step(state, _assistant([{"type": "tool_use", "id": tid, "name": "Agent",
                                             "input": {"description": "d", "prompt": "p" * 4000, "run_in_background": True}}],
                                           "a%d" % i, TS0 + i + 0.123))
            em._bg_step(state, _user([{"type": "tool_result", "tool_use_id": tid, "content": "launched"}], "k%d" % i,
                                     TS0 + i + 0.2, toolUseResult={"isAsync": True, "status": "async_launched",
                                                                   "outputFile": "/tmp/notes-api/out-%d.txt" % i,
                                                                   "taskType": "local_agent", "agentId": "a%016x" % i}))
            em._bg_step(state, _user(_note(tid, summary="s" * 300, result="r" * 16000), "n%d" % i, TS0 + i + 0.456))
        per_task = _encoded_bytes(state) / n
        self.assertLess(per_task, 200, "the lift's state grows by %.0f bytes a task" % per_task)
        self.assertGreater(em._CKPT_FOLD_CAP / per_task, 40_000)


class LiftRowsAreExact(Base):
    def mixed(self):
        """Every launch and return shape the pairing knows, including the ones that must NOT end a task."""
        t = TS0
        return [
            _assistant([{"type": "tool_use", "id": _tid(1), "name": "Bash",
                         "input": {"command": "make test", "run_in_background": True, "description": "suite"}}], "a1", t + 1),
            _assistant([{"type": "tool_use", "id": _tid(2), "name": "Monitor",
                         "input": {"command": "tail -f web.log", "timeout_ms": 60000, "description": "watch"}}], "a2", t + 2),
            _assistant([{"type": "tool_use", "id": _tid(3), "name": "Monitor",
                         "input": {"command": "tail -f api.log", "persistent": True}}], "a3", t + 3),       # furniture: skipped
            _assistant([{"type": "tool_use", "id": _tid(4), "name": "Agent",
                         "input": {"description": "check width", "prompt": "check the width", "run_in_background": True}}], "a4", t + 4),
            _user([{"type": "tool_result", "tool_use_id": _tid(4), "content": "launched"}], "k4", t + 4.5,
                  toolUseResult={"isAsync": True, "status": "async_launched", "outputFile": "/tmp/o4", "taskType": "local_agent",
                                 "agentId": "a0000000000000004"}),
            _assistant([{"type": "tool_use", "id": _tid(5), "name": "Workflow", "input": {"script": "run()"}}], "a5", t + 5),
            _user([{"type": "tool_result", "tool_use_id": _tid(5), "content": "started"}], "k5", t + 5.5,
                  toolUseResult={"isAsync": True, "status": "async_launched", "workflowName": "sweep"}),
            _assistant([{"type": "tool_use", "id": _tid(6), "name": "Bash",
                         "input": {"command": "false", "run_in_background": True}}], "a6", t + 6),
            _user([{"type": "tool_result", "tool_use_id": _tid(6), "content": "denied", "is_error": True}], "k6", t + 6.5),
            _user(_note(_tid(2), with_status=False, summary="a line"), "e2", t + 7),                    # a monitor EVENT
            _user([{"type": "tool_result", "tool_use_id": _tid(1), "content": _note(_tid(1), "completed", "suite ok")}], "n1", t + 8),
            {"type": "queue-operation", "operation": "enqueue", "content": _note(_tid(4), "completed", "width ok", result="clamped"),
             "timestamp": _iso(t + 9)},
            _user([{"type": "tool_result", "tool_use_id": _tid(4), "content": "launched"}], "k4b", t + 9.5,     # a replayed ack
                  toolUseResult={"isAsync": True, "status": "async_launched", "taskType": "local_agent"}),
            _user(_note(_tid(5), "failed", "sweep broke"), "n5", t + 10),
        ]

    def test_the_lift_rows_equal_the_every_task_rows_narrowed_to_the_lift_fields(self):
        _write(self.leaf, self.mixed())
        every = km._bg_scan_all_cached(self.leaf)
        lift = km._bg_scan_lift_cached(self.leaf)
        self.assertEqual(lift, [{k: v for k, v in r.items() if k in LIFT_KEYS} for r in every])
        self.assertEqual([(r["id"], r["status"]) for r in lift],
                         [(_tid(1), "completed"), (_tid(2), "running"), (_tid(4), "completed"), (_tid(5), "failed"),
                          (_tid(6), "failed")], "the fixture drives every shape")
        self.assertTrue(any("deadline" in r and r.get("monitor") for r in lift), "the monitor keeps its deadline")
        self.assertTrue(all("endT" in r for r in lift if r["status"] != "running"), "every return keeps its end time")
        self.assertTrue(any(r.get("type") == "local_workflow" for r in lift))

    def test_appending_record_by_record_equals_one_fold_over_the_whole_file(self):
        recs = self.mixed()
        _write(self.leaf, recs[:1])
        for k in range(2, len(recs) + 1):
            with open(self.leaf, "a") as f:
                f.write(json.dumps(recs[k - 1]) + "\n")
            st = os.stat(self.leaf)
            os.utime(self.leaf, (st.st_atime, st.st_mtime + 1))
            km._bg_scan_lift_cached(self.leaf)
        stepped = km._bg_scan_lift_cached(self.leaf)
        cold = em.fold_records({}, self.leaf, lambda: em._bg_fresh(True, slim=True), em._bg_step)
        self.assertEqual(stepped, em._bg_finish(cold, True))

    def test_a_cache_built_for_one_view_refuses_the_other(self):
        _write(self.leaf, self.mixed())
        cache = {}
        em.scan_bg_tasks_cached(self.leaf, cache, want_all=True, slim=True)
        with self.assertRaises(ValueError):
            em.scan_bg_tasks_cached(self.leaf, cache, want_all=True)
        cache = {}
        em.scan_bg_tasks_cached(self.leaf, cache, want_all=True)
        with self.assertRaises(ValueError):
            em.scan_bg_tasks_cached(self.leaf, cache, want_all=True, slim=True)
        with self.assertRaises(ValueError):                    # the view's other half of the guard stands
            em.scan_bg_tasks_cached(self.leaf, cache, want_all=False)


class DocumentsWrittenBeforeTheLiftFold(Base):
    """The first boot of this kernel finds documents an older kernel wrote: bgAll's entry (a state, or a cursor over the cap)
    and no bgLift. Without a seed the lift's first fold of every alive transcript would read it whole in the first cycle."""

    def old_kernel_document(self, recs, age_s=0):
        _write(self.leaf, recs, age_s=age_s)
        km._bg_scan_all_cached(self.leaf)                       # the older kernel's lift fold
        self.assertTrue(em.checkpoint_write(self.leaf))
        self.assertNotIn("bgLift", self.doc()["folds"], "the document predates the lift's fold")
        self.fresh_process()

    def narrowed_cold_answer(self):
        return [{k: v for k, v in r.items() if k in LIFT_KEYS} for r in em._scan_bg_tasks(self.leaf, want_all=True)]

    def test_over_the_cap_the_lift_fold_restarts_cold_over_the_tail_reads_nothing_whole_and_the_settle_heals_it(self):
        self.old_kernel_document(_big_transcript(n_agents=500, n_shell=300))
        self.assertIn("over", self.doc()["folds"]["bgAll"])
        size = os.path.getsize(self.leaf)
        self.lift(TS0 + 10_000)                                 # the boot's first cycle
        self.assertLess(self.read_of_leaf(), size // 100, "the lift's first fold reads the tail, as bgAll's cold start did")
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgLift": "cold"}, "a tail-only state, marked for the settle's heal")
        self.assertEqual(km._heal_cold_folds(self.leaf), ["bgLift"], "the settle heals it by one whole refold")
        self.assertEqual(km._bg_scan_lift_cached(self.leaf), self.narrowed_cold_answer(), "complete again")
        self.assertTrue(em.checkpoint_write(self.leaf))
        self.assertIn("state", self.doc()["folds"]["bgLift"], "and written with its own state")
        self.fresh_process()
        self.lift(TS0 + 10_001)                                 # the next boot
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgLift"), 1, "restored warm from its own entry")
        self.assertLess(self.read_of_leaf(), size // 100)
        self.assertEqual(km._bg_scan_lift_cached(self.leaf), self.narrowed_cold_answer())

    def test_where_the_every_task_state_fits_the_lift_fold_restores_warm_from_its_projection(self):
        recs = LiftRowsAreExact.mixed(self)
        self.old_kernel_document(recs[:8])
        with open(self.leaf, "a") as f:                         # the session wrote on after the older kernel's write
            for r in recs[8:]:
                f.write(json.dumps(r) + "\n")
        st = os.stat(self.leaf); os.utime(self.leaf, (st.st_atime, st.st_mtime + 2))
        got = km._bg_scan_lift_cached(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgLift"), 1, "a warm restore from bgAll's state")
        self.assertEqual(em.cold_fold_reasons(self.leaf), {})
        self.assertEqual(got, self.narrowed_cold_answer(), "and the tail stepped on it equals a whole fold")

    def test_the_projection_equals_the_slim_fold_state_after_every_record(self):
        recs = LiftRowsAreExact.mixed(self)
        fat, slim = em._bg_fresh(True), em._bg_fresh(True, slim=True)
        for r in recs:
            em._bg_step(fat, r); em._bg_step(slim, r)
            self.assertEqual(em._ckpt_decode(em.bg_slim_encoded(em._ckpt_encode(fat))), slim)

    def test_the_projection_refuses_every_other_shape(self):
        self.assertIsNone(em.bg_slim_encoded(em._ckpt_encode(em._bg_fresh(False))), "the running-only view")
        self.assertIsNone(em.bg_slim_encoded(em._ckpt_encode(em._bg_fresh(True, slim=True))), "an already narrowed state")
        self.assertIsNone(em.bg_slim_encoded({"~dict": []}))
        self.assertIsNone(em.bg_slim_encoded(None))


if __name__ == "__main__":
    unittest.main()
