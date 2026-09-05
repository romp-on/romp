#!/usr/bin/env python3
"""The per-push readers of states/<sid>.jsonl and timeline/messages.jsonl are served append-
incrementally (2026-09-03). Before, ten readers each opened the file and json.loads'd every line on
every call — eight of them per session per pusher cycle — so an idle kernel re-read every session's
states log about twice a second (the most-opened files in a live descriptor sample). Now they all
consume event_model's jsonl cache: an unchanged file costs one stat, a grown file parses only the
appended bytes, and every reader sees exactly the rows it saw before. Synthetic sids and rows only.
"""
import builtins
import io
import inspect
import json
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-999999999901"     # private synthetic sid: this module owns its states file
NOW = 1781100000


class _StateSandbox(unittest.TestCase):
    """Pin every judge-module store path to ONE fresh root for the test, and restore after. The kernel,
    judge and event-model modules are process-wide singletons shared by every test module (same loader
    name), and other modules re-point jd.STATE or jd.MESSAGES for their own cases — a reader that derives
    its path from jd.STATE and one that reads jd.MESSAGES must agree here, or a full-suite run diverges
    from a solo run (the ordering-dependent failure class the repo's CLAUDE.md warns about)."""

    _PIN = ("STATE", "STATESDIR", "MESSAGES", "GOALDIR", "CAPDIR", "ARCHDIR")

    def setUp(self):
        from pathlib import Path
        self._saved_paths = {k: getattr(jd, k) for k in self._PIN}
        root = Path(tempfile.mkdtemp())
        jd.STATE = root
        jd.STATESDIR = root / "states"
        jd.MESSAGES = root / "timeline" / "messages.jsonl"
        jd.GOALDIR = root / "goals"
        jd.CAPDIR = root / "captions"
        jd.ARCHDIR = root / "archive"
        for d in (jd.STATESDIR, jd.MESSAGES.parent, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, root / "sdk"):
            d.mkdir(parents=True, exist_ok=True)
        km._postal_index_memo[0] = None
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {})]
        self.addCleanup(self._restore_paths)

    def _restore_paths(self):
        for k, v in self._saved_paths.items():
            setattr(jd, k, v)
        km._postal_index_memo[0] = None
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {})]


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _append_rows(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class _OpenCounter:
    """Counts READ opens of ONE path — the proof that an unchanged file is served from cache. The tests'
    own append writes to the same path are not counted."""

    def __init__(self, path):
        self.path, self.n, self._real = str(path), 0, builtins.open

    def __enter__(self):
        self._real_io = io.open

        def hook(file, *a, **kw):
            mode = a[0] if a else kw.get("mode", "r")
            if str(file) == self.path and "r" in mode and "+" not in mode:
                self.n += 1
            return self._real_io(file, *a, **kw)
        builtins.open = hook
        io.open = hook          # pathlib's read_text/open call io.open, not the builtin (review 2026-09-03)
        return self

    def __exit__(self, *a):
        builtins.open = self._real
        io.open = self._real_io


class StatesReaders(_StateSandbox):
    ROWS = [
        {"t": NOW - 900, "state": "working"},
        {"t": NOW - 800, "awaiting": True, "why": "a build"},
        {"t": NOW - 700, "retriesRecovered": 3},
        {"t": NOW - 650, "cmdGesture": "/effort high"},
        {"t": NOW - 640, "effortApplied": "high"},
        {"t": NOW - 600, "state": "waiting"},
        {"t": NOW - 500, "retriesGaveUp": 4, "errorKind": "overloaded"},
        {"t": NOW - 400, "orphanReply": {"uuid": "u-1", "text": "the lost reply"}},
        {"t": NOW - 300, "state": "compacting"},
        {"t": NOW - 200, "state": "working"},
        {"t": NOW - 100, "state": "idle"},
    ]

    def setUp(self):
        super().setUp()
        self.path = jd.STATE / "states" / (SID + ".jsonl")
        em._JSONL_CACHE.pop(str(self.path), None)
        _write_rows(self.path, self.ROWS)

    def tearDown(self):
        em._JSONL_CACHE.pop(str(self.path), None)
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_a_every_reader_sees_exactly_the_rows_it_saw_before(self):
        self.assertEqual(km._last_state(SID), ("idle", NOW - 100))
        self.assertEqual(km._last_state_value(SID), "idle")
        # a later WORK turn supersedes the stale awaiting:true → not awaiting (the 2026-06-26 rule)
        self.assertEqual(km._states_awaiting_overlay(SID), {"awaiting": False, "why": None})
        self.assertEqual(km._retry_recoveries(SID), [{"t": NOW - 700, "retries": 3}])
        self.assertEqual(km._retry_gaveups(SID), [{"t": NOW - 500, "retries": 4, "errorKind": "overloaded"}])
        self.assertEqual(km._orphan_replies(SID), [{"t": NOW - 400, "uuid": "u-1", "text": "the lost reply"}])
        self.assertEqual(km._effort_changes(SID), [{"t": NOW - 640, "effort": "high"}])
        self.assertEqual(km._cmd_gestures(SID), [{"t": NOW - 650, "cmd": "/effort high"}])
        # the compacting band runs from its transition to the next one
        self.assertEqual(km._state_intervals(SID, "compacting", NOW), [[NOW - 300, NOW - 200]])
        self.assertEqual(km._state_intervals(SID, ("permission", "picker"), NOW), [])

    def test_a2_the_retrying_stretch_reads_from_the_same_rows(self):
        _append_rows(self.path, [{"t": NOW - 60, "state": "retrying"}, {"t": NOW - 55, "awaiting": False},
                                 {"t": NOW - 50, "state": "retrying"}])
        tm = {"state": "retrying", "retryCount": 2, "retryInfo": {"max": 10, "status": 529}}
        info = km._session_retrying(SID, tm)
        self.assertEqual((info["since"], info["count"], info["max"], info["status"]), (NOW - 60, 2, 10, 529))
        _append_rows(self.path, [{"t": NOW - 40, "state": "working"}])
        self.assertIsNone(km._session_retrying(SID, tm)["since"], "a real transition ends the stretch")
        self.assertIsNone(km._session_retrying(SID, {"state": "working"}), "not in a storm → no chip")

    def test_b_an_awaiting_overlay_not_yet_superseded_stands(self):
        _append_rows(self.path, [{"t": NOW - 50, "awaiting": True, "why": "an agent"}])
        self.assertEqual(km._states_awaiting_overlay(SID), {"t": NOW - 50, "awaiting": True, "why": "an agent"})

    def test_c_an_unchanged_file_is_never_reopened(self):
        km._last_state(SID)                                   # warm the cache
        with _OpenCounter(self.path) as c:
            for _ in range(5):
                km._last_state(SID); km._states_awaiting_overlay(SID); km._retry_recoveries(SID)
                km._retry_gaveups(SID); km._orphan_replies(SID); km._effort_changes(SID)
                km._cmd_gestures(SID); km._state_intervals(SID, "compacting", NOW)
        self.assertEqual(c.n, 0, "eight readers x five calls on a quiet log: zero opens, one stat each")

    def test_d_an_append_is_picked_up_from_the_appended_bytes_only(self):
        km._last_state(SID)
        with _OpenCounter(self.path) as c:
            _append_rows(self.path, [{"t": NOW - 10, "state": "working"}])
            self.assertEqual(km._last_state(SID), ("working", NOW - 10))
            self.assertEqual(km._state_intervals(SID, "working", NOW)[-1], [NOW - 10, NOW])
        self.assertEqual(c.n, 1, "the grown file is opened once, for its tail")

    def test_e_a_missing_file_reads_as_before(self):
        gone = "11111111-2222-3333-4444-999999999902"
        self.assertEqual(km._last_state(gone), ("", 0))
        self.assertIsNone(km._states_awaiting_overlay(gone))
        self.assertEqual(km._retry_recoveries(gone), [])
        self.assertEqual(km._state_intervals(gone, "compacting", NOW), [])

    def test_f_a_garbled_line_is_skipped_not_fatal(self):
        with open(self.path, "a") as f:
            f.write("{not json\n")
        _append_rows(self.path, [{"t": NOW - 5, "state": "waiting"}])
        self.assertEqual(km._last_state(SID), ("waiting", NOW - 5))


class PostalLogReaders(_StateSandbox):
    A = "11111111-2222-3333-4444-999999999911"
    B = "11111111-2222-3333-4444-999999999912"

    def setUp(self):
        super().setUp()
        em._JSONL_CACHE.pop(str(jd.MESSAGES), None)
        _write_rows(jd.MESSAGES, [
            {"ev": "sent", "id": "m-1", "from_id": self.A, "to_id": self.B, "t": NOW - 100, "body": "please review", "park": True},
            {"ev": "sent", "id": "m-2", "from_id": self.A, "to_id": self.B, "t": NOW - 90, "body": "and this"},
            {"ev": "exec", "id": "m-2", "t": NOW - 80},
        ])
        (jd.STATE / "postal" / "mail" / self.B / "new").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "postal" / "mail" / self.B / "new" / "m-1").write_text("{}")

    def tearDown(self):
        em._JSONL_CACHE.pop(str(jd.MESSAGES), None)
        try:
            os.unlink(jd.MESSAGES)
        except OSError:
            pass

    def test_a_parked_handoffs_and_connectors_read_the_same_rows(self):
        parked = km._parked_handoffs(NOW, alive_sids=set())
        self.assertEqual([h["msgId"] for h in parked], ["m-1"], parked)
        self.assertEqual(km._parked_handoffs(NOW, alive_sids={self.B}), [], "a revived recipient consumed it")
        conns = km._postal_messages(NOW, {self.A, self.B}, {self.A: "a", self.B: "b"})
        self.assertTrue(any(c.get("id") == "m-2" for c in conns) or conns, conns)

    def test_b_the_log_is_not_reopened_while_quiet(self):
        km._parked_handoffs(NOW, alive_sids=set())
        with _OpenCounter(jd.MESSAGES) as c:
            for _ in range(3):
                km._parked_handoffs(NOW, alive_sids=set())
                km._postal_messages(NOW, {self.A, self.B}, {self.A: "a", self.B: "b"})
        self.assertEqual(c.n, 0)


class ViewCountersAreVisible(unittest.TestCase):
    """The pusher's rebuild-vs-serve counts ride the version route beside the parse counters, so a kernel
    that rebuilds a view every cycle on a quiet board is a NUMBER, not an inference from top."""

    def test_a_an_unchanged_signature_is_served_and_counted(self):
        before = dict(km._VIEW_STATS)
        sig = ("stage0-test", 1)
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._cached_feed(NOW, {}, sig)                      # first: a build
        km._cached_feed(NOW, {}, sig)                      # same sig: served
        self.assertEqual(km._VIEW_STATS["feedBuild"] - before["feedBuild"], 1)
        self.assertEqual(km._VIEW_STATS["feedServe"] - before["feedServe"], 1)
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._cached_timeline(NOW, {}, sig)
        km._cached_timeline(NOW, {}, sig)
        self.assertEqual(km._VIEW_STATS["tlBuild"] - before["tlBuild"], 1)
        self.assertEqual(km._VIEW_STATS["tlServe"] - before["tlServe"], 1)

    def test_b_the_version_payload_carries_them(self):
        fn = getattr(km, "_version_payload", None) or getattr(km, "_kernel_version_payload", None)
        if fn is None:
            src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"), encoding="utf-8").read()
            self.assertIn('"views": dict(_VIEW_STATS)', src)
            return
        self.assertEqual(set(fn()["views"]), set(km._VIEW_STATS))


class BackendOwnsIsMemoized(unittest.TestCase):
    """Sessions.backend_for asks the SDK backend `owns(sid)` for every session in every tick job; it read
    and decoded the registry file each time. Now one stat answers while the file is unchanged."""

    SID = "11111111-2222-3333-4444-999999999921"

    def test_a_owns_reads_the_reg_once_per_file_version(self):
        sb = SourceFileLoader("romp_sdk_backend_stage0", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        self.assertFalse(be.owns(self.SID), "no reg yet")
        sb.write_reg(be.state_dir, self.SID, {"sid": self.SID, "name": "web", "cwd": "/tmp", "alive": True})
        self.assertTrue(be.owns(self.SID))
        reads = []
        real = sb.read_reg
        sb.read_reg = lambda *a, **k: (reads.append(1), real(*a, **k))[1]
        try:
            for _ in range(20):
                self.assertTrue(be.owns(self.SID))
            self.assertEqual(reads, [], "twenty asks on an unchanged reg: the file is not decoded again")
            sb.write_reg(be.state_dir, self.SID, {"sid": self.SID, "name": "web", "cwd": "/tmp", "alive": False})
            self.assertTrue(be.owns(self.SID))
            self.assertEqual(len(reads), 1, "a rewritten reg is decoded once more")
        finally:
            sb.read_reg = real
        os.unlink(sb._reg_path(be.state_dir, self.SID))
        self.assertFalse(be.owns(self.SID), "a vanished reg is not owned")


class ViewSignatureKeysOnExactInputs(_StateSandbox):
    """The feed/timeline signature used to bust every ≤3 s because the judge generation advanced after
    EVERY producer pass, changed store or not — and the per-session tuple missed the SDK snapshot facts the
    views render (subagent counts, an interrupt, a switch resolving), so those surfaced only via the 5 s
    time bucket. Now the generation moves only when a pass moved a store, a judge call starting or ending
    is its own signature input, and the snapshot facts are in the tuple."""

    SID = "11111111-2222-3333-4444-999999999931"

    def test_a_a_quiet_pass_leaves_the_judge_generation_alone(self):
        km._last_judge_fp[0] = None
        fp = km._judge_store_fp()
        km._bump_judge_gen_if_changed(fp)                    # anchor the "last look" on the current stores
        g0 = km._judge_gen[0]
        self.assertFalse(km._bump_judge_gen_if_changed(fp))
        self.assertFalse(km._bump_judge_gen_if_changed(fp), "two quiet passes → no movement")
        self.assertEqual(km._judge_gen[0], g0)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (self.SID + ".json")).write_text(json.dumps({"rompUuid": self.SID, "nodes": {}}))
        try:
            # written BETWEEN passes (a user's clear, the awaiting lift): the next look sees it — the anchor is
            # the previous look, not this pass's own start (review 2026-09-03)
            self.assertTrue(km._bump_judge_gen_if_changed(km._judge_store_fp()), "a store moved since the last look → bump")
            self.assertEqual(km._judge_gen[0], g0 + 1)
            self.assertFalse(km._bump_judge_gen_if_changed(), "…and only once")
        finally:
            os.unlink(jd.GOALDIR / (self.SID + ".json"))
            km._last_judge_fp[0] = None

    def test_b_a_judge_call_starting_and_ending_are_signature_events(self):
        c0 = jd.active_change()
        rid = jd._active_begin("planner", self.SID, 0)
        self.assertEqual(jd.active_change(), c0 + 1)
        jd._active_end(rid)
        self.assertEqual(jd.active_change(), c0 + 2)
        jd._active_end(rid)                                  # a second end of the same run is not an event
        self.assertEqual(jd.active_change(), c0 + 2)

    def test_c_snapshot_facts_move_the_signature_without_a_file_or_the_clock(self):
        now = NOW - (NOW % 5)                                 # inside one time bucket throughout
        base = {"state": "working", "model": "m", "ctx": 10, "effort": "", "mode": "", "fast": "", "since": NOW - 60,
                "subagents": [], "bgTasks": [], "interrupting": False, "modelPending": False, "retryCount": 0}
        s1 = km._fleet_view_sig(now, {self.SID: dict(base)})
        self.assertEqual(s1, km._fleet_view_sig(now + 1, {self.SID: dict(base)}), "same inputs, same bucket → same sig")
        for k, v in (("subagents", [{"id": "a1"}]), ("bgTasks", [{"toolUseId": "t1"}]), ("interrupting", True),
                     ("modelPending", True), ("retryCount", 3)):
            changed = dict(base); changed[k] = v
            self.assertNotEqual(s1, km._fleet_view_sig(now, {self.SID: changed}), k)
        rid = jd._active_begin("closer", self.SID, 0)
        try:
            self.assertNotEqual(s1, km._fleet_view_sig(now, {self.SID: dict(base)}), "a judge call in flight")
        finally:
            jd._active_end(rid)


class ActiveTabIsServedOnAnExactKey(_StateSandbox):
    """The watched chat tab used to rebuild from all atoms on every pusher cycle "by design", because its
    payload moves with inputs no file records. _active_chat_sig names every one of them, so an identical
    world serves the last build and any moved input rebuilds. Synthetic session, private sid."""

    SID = "11111111-2222-3333-4444-999999999941"

    def setUp(self):
        super().setUp()
        self.path = jd.STATE / "states" / (self.SID + ".jsonl")
        _write_rows(self.path, [{"t": NOW - 100, "state": "waiting"}])
        self.tpath = jd.STATE / ("stage0-transcript-" + self.SID + ".jsonl")
        self.tpath.write_text(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-09-03T00:00:00Z",
                                          "message": {"role": "user", "content": "hi"}}) + "\n")
        self.sess = {"sid": self.SID, "path": str(self.tpath), "anchor": self.SID}
        self.tm = {"state": "waiting", "since": NOW - 100, "model": "m", "subagents": [], "bgTasks": [], "snapT": NOW}
        km._interrupt_clicked.pop(self.SID, None); km._model_switch_pending.pop(self.SID, None); km._compact_clicked.pop(self.SID, None)

    def tearDown(self):
        for p in (self.path, self.tpath):
            try: os.unlink(p)
            except OSError: pass
        km._interrupt_clicked.pop(self.SID, None); km._model_switch_pending.pop(self.SID, None); km._compact_clicked.pop(self.SID, None)

    def test_a_an_identical_world_yields_the_same_key_and_a_volatile_stamp_does_not_matter(self):
        s1 = km._active_chat_sig(self.sess, self.tm, NOW)
        self.assertIsNotNone(s1)
        tm2 = dict(self.tm); tm2["snapT"] = NOW + 7
        self.assertEqual(s1, km._active_chat_sig(self.sess, tm2, NOW + 1), "snapT and the clock alone move nothing")

    def test_b_each_input_class_moves_the_key(self):
        s1 = km._active_chat_sig(self.sess, self.tm, NOW)
        tm = dict(self.tm); tm["state"] = "working"
        self.assertNotEqual(s1, km._active_chat_sig(self.sess, tm, NOW), "a snapshot fact")
        _append_rows(self.path, [{"t": NOW - 5, "state": "working"}])
        s2 = km._active_chat_sig(self.sess, self.tm, NOW)
        self.assertNotEqual(s1, s2, "a states row")
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        cap = jd.CAPDIR / (self.SID + ".jsonl")
        try:
            cap.write_text(json.dumps({"id": "seg-1", "gist": "did a thing"}) + "\n")
            s3 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertNotEqual(s2, s3, "a caption landed")
        finally:
            try: os.unlink(cap)
            except OSError: pass
        rid = jd._active_begin("closer", self.SID, 0)
        try:
            self.assertNotEqual(s2, km._active_chat_sig(self.sess, self.tm, NOW), "a judge call in flight")
        finally:
            jd._active_end(rid)

    def test_c_clock_predicates_flip_exactly_at_their_deadlines(self):
        tm = {"state": "ready", "since": NOW - 3599, "bgTasks": []}
        p0 = km._clock_predicates(self.SID, tm, NOW)
        self.assertEqual(p0[:5], (False, False, False, False, False))
        self.assertEqual(km._clock_predicates(self.SID, tm, NOW + 2)[0], True, "faded flips at the hour")
        odd = dict(tm, state="")                          # the build derives 'ready' from an empty raw state
        self.assertEqual(km._clock_predicates(self.SID, odd, NOW + 2)[1], True, "…keyed whatever the raw state says")
        km._interrupt_clicked[self.SID] = NOW - 119
        self.assertFalse(km._clock_predicates(self.SID, tm, NOW)[2])
        self.assertTrue(km._clock_predicates(self.SID, tm, NOW + 2)[2], "the interrupt cap ran out")
        s1 = km._active_chat_sig(self.sess, tm, NOW)
        self.assertNotEqual(s1, km._active_chat_sig(self.sess, tm, NOW + 2), "…and the key follows the flip")


    def test_d_a_live_task_expiring_moves_the_key_through_the_builds_own_filter(self):
        """The raw snapshot rows carry no deadline; the build joins each task's recorded deadline from the
        launch ledger and drops expired ones. The key carries that filtered set, so an expiry — a tid
        dropping out — moves it exactly when the awaiting box would change (review 2026-09-03)."""
        saved = km._bg_live_norm
        rows = [{"tid": "t1", "desc": "watch", "t": NOW - 60}]
        km._bg_live_norm = lambda sid, path: list(rows)
        try:
            s1 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW + 1))
            rows.clear()                                     # the watcher passed its deadline → filtered out
            self.assertNotEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW + 2))
        finally:
            km._bg_live_norm = saved

    def test_d2_a_running_tasks_output_growing_moves_the_key(self):
        """The task box shows a running task's output tail, read live from a file outside the state root; the
        key stats that file so the tail keeps moving (review 2026-09-03)."""
        out = jd.STATE / "task-out.log"; out.write_text("line 1\n")
        saved = km._bg_scan_cached
        km._bg_scan_cached = lambda path: [{"id": "toolu_1", "status": "running", "outputFile": str(out)}]
        try:
            s1 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW))
            with open(out, "a") as f:
                f.write("line 2\n")
            self.assertNotEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW), "the tail grew")
        finally:
            km._bg_scan_cached = saved

    def test_d3_the_branch_of_a_subdirectory_cwd_and_of_the_last_edited_files_tree_is_keyed(self):
        """build_session reads the branch of the registered cwd's tree and of the last edited file's tree
        (the per-session worktree); both HEADs are in the key, resolved through _tree_of like the build."""
        import subprocess
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
        sub = os.path.join(repo, "pkg"); os.makedirs(sub)
        saved_cwd = km._cwd_of
        km._cwd_of = lambda sid: sub                       # a cwd INSIDE the repo, not its top
        try:
            e1 = km._external_sig(self.SID, str(self.tpath))
            self.assertTrue(any(x is not None for x in e1[1:2]), "the repo's HEAD is stat'd for a subdirectory cwd: %r" % (e1,))
            s1 = km._active_chat_sig(self.sess, self.tm, NOW)
            subprocess.run(["git", "-C", repo, "checkout", "-q", "-b", "feature"], check=True)
            self.assertNotEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW), "a branch switch moves the key")
        finally:
            km._cwd_of = saved_cwd

    def test_e_the_backend_leg_and_the_watch_files_move_the_key(self):
        """A stub backend stands in for the SDK: the live-tail revision, the send queue and the brackets each
        move the key; so does the kernel-owned watch registry's file (review 2026-09-03)."""
        class Stub:
            rev = 0; queue = []; comp = False; clr = False
            def owns(self, sid): return True
            def live_rev(self, sid): return self.rev
            def pending_queued(self, sid): return list(self.queue)
            def compacting(self, sid): return self.comp
            def clearing(self, sid): return self.clr
            def pending_cut(self, sid): return ""
        stub = Stub(); saved = km._sdk
        km._sdk = lambda: stub
        try:
            s1 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW))
            stub.rev += 1;  s2 = km._active_chat_sig(self.sess, self.tm, NOW); self.assertNotEqual(s1, s2, "a live atom")
            stub.queue = ["x"]; s3 = km._active_chat_sig(self.sess, self.tm, NOW); self.assertNotEqual(s2, s3, "a queued send")
            stub.comp = True;  s4 = km._active_chat_sig(self.sess, self.tm, NOW); self.assertNotEqual(s3, s4, "compacting")
            (jd.STATE / "watches.json").write_text(json.dumps([{"sid": self.SID, "what": "a build"}]))
            self.assertNotEqual(s4, km._active_chat_sig(self.sess, self.tm, NOW), "a watch armed")
        finally:
            km._sdk = saved


    def test_f_the_owning_backends_live_tail_moves_the_key_for_a_tmux_session(self):
        """Review 2026-09-05: the key read only the SDK backend's tail, so a tmux session's composer echo — a
        kernel-side store the build renders — left the watched tab served stale until some file moved."""
        saved_sdk = km._sdk
        km._sdk = lambda: None                                    # this sid is tmux-owned
        km._tmux_echo.pop(self.SID, None)
        try:
            s1 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertIsNotNone(s1)
            km._tmux_echo_add(self.SID, "please also fix the header")
            s2 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertNotEqual(s1, s2, "the echo the build renders is in the key")
            for a in km._tmux_echo.get(self.SID, {}).values():
                a["dropped"] = True                               # the pane dropped the keystroke: rendered differently
            s3 = km._active_chat_sig(self.sess, self.tm, NOW)
            self.assertNotEqual(s2, s3, "…and so is its dropped flag")
            km._tmux_echo.pop(self.SID, None)                     # dismissed
            self.assertEqual(km._active_chat_sig(self.sess, self.tm, NOW), s1, "back to the world before the send")
        finally:
            km._sdk = saved_sdk
            km._tmux_echo.pop(self.SID, None)

    def test_g_the_background_key_carries_the_snapshot_facts_a_tabs_chips_render(self):
        """The judge generation no longer advances every pass, so a background tab's chips (state, retrying,
        subagents, pending picks…) are keyed on the snapshot row itself (review 2026-09-05)."""
        b1 = km._chat_build_sig(self.sess, self.tm)
        self.assertIsNotNone(b1)
        tm2 = dict(self.tm); tm2["snapT"] = NOW + 30
        self.assertEqual(b1, km._chat_build_sig(self.sess, tm2), "the volatile stamp alone moves nothing")
        for k, v in (("state", "retrying"), ("subagents", ["a"]), ("retryCount", 3), ("modelPending", True),
                     ("connected", True), ("spawning", True), ("model", "other"), ("auth", "key")):
            tm3 = dict(self.tm); tm3[k] = v
            self.assertNotEqual(b1, km._chat_build_sig(self.sess, tm3), k)
        self.assertNotEqual(b1, km._chat_build_sig(self.sess), "no row handed → a different (row-less) key, never a false hit")

    def test_h_the_spend_hold_moves_the_key_and_marks_the_views_dirty(self):
        s1 = km._active_chat_sig(self.sess, self.tm, NOW)
        before = km._views_dirty[0]
        km._set_retry_paused(True, reason="spend")
        try:
            self.assertNotEqual(s1, km._active_chat_sig(self.sess, self.tm, NOW), "retry-paused.json is a keyed side file")
            self.assertGreater(km._views_dirty[0], before, "every writer of the hold publishes the flip")
        finally:
            km._set_retry_paused(False)

    def test_i_the_active_key_reuses_the_pushers_base(self):
        base = km._chat_build_sig(self.sess, self.tm)
        self.assertEqual(km._active_chat_sig(self.sess, self.tm, NOW, base=base), km._active_chat_sig(self.sess, self.tm, NOW))


class PerBuildReadersAreCached(_StateSandbox):
    SID = "11111111-2222-3333-4444-999999999951"

    def test_a_captions_are_not_reopened_while_quiet(self):
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        cap = jd.CAPDIR / (self.SID + ".jsonl")
        try:
            _write_rows(cap, [{"id": "s1", "gist": "one"}, {"id": "s2", "gist": "two"}, {"id": "s1", "gist": "one, revised"}])
            self.assertEqual({k: v["gist"] for k, v in km._captions(self.SID).items()}, {"s1": "one, revised", "s2": "two"}, "last wins")
            with _OpenCounter(cap) as c:
                for _ in range(4):
                    km._captions(self.SID)
            self.assertEqual(c.n, 0)
        finally:
            em._JSONL_CACHE.pop(str(cap), None)
            try: os.unlink(cap)
            except OSError: pass

    def test_b_thread_reg_decodes_once_per_file_version_and_hands_out_copies(self):
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        reg = jd.STATE / "sdk" / (self.SID + ".json")
        try:
            reg.write_text(json.dumps({"sid": self.SID, "cwd": "/tmp", "threadOf": "parent"}))
            d1 = km._thread_reg(self.SID)
            self.assertEqual(d1.get("threadOf"), "parent")
            d1["threadOf"] = "mutated by a caller"
            with _OpenCounter(reg) as c:
                d2 = km._thread_reg(self.SID)
            self.assertEqual(c.n, 0, "an unchanged reg is one stat")
            self.assertEqual(d2.get("threadOf"), "parent", "a caller's edit never reaches the memo")
            reg.write_text(json.dumps({"sid": self.SID, "cwd": "/tmp", "threadOf": "other"}))
            self.assertEqual(km._thread_reg(self.SID).get("threadOf"), "other", "a rewrite is seen")
            os.unlink(reg)
            self.assertEqual(km._thread_reg(self.SID), {}, "a vanished reg reads as before")
        finally:
            km._thread_reg_memo.pop(self.SID, None)
            try: os.unlink(reg)
            except OSError: pass

    def test_c_the_live_tail_revision_moves_on_every_mutation(self):
        sb = SourceFileLoader("romp_sdk_backend_stage0b", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sid = self.SID
        r0 = be.live_rev(sid)
        be._live.setdefault(sid, {})["u1"] = {"uuid": "u1", "t": NOW, "type": "assistant"}
        be._touch_live(sid)
        self.assertEqual(be.live_rev(sid), r0 + 1)
        be.prune_live(sid, {"u1"})                        # the transcript caught up → the atom is dropped
        self.assertEqual(be.live_rev(sid), r0 + 2, "a prune is a mutation")
        self.assertEqual(be.live_atoms(sid), [])
        be.prune_live(sid, {"u1"})                        # nothing left to drop → no event
        self.assertEqual(be.live_rev(sid), r0 + 2)


class PerCycleStoreReadersAreCached(_StateSandbox):
    """The pusher's tick jobs and builders re-read growing logs and small stores from disk every cycle.
    Each now answers from a stat while its file is unchanged (2026-09-03)."""

    SID = "11111111-2222-3333-4444-999999999961"

    def test_a_last_state_is_the_literal_last_row_read_from_the_tail(self):
        """Upstream's tail reader (c84165d4) superseded this branch's memoized one: every caller wants the
        newest record, and it is read backwards from the end of the file rather than by a forward walk of a
        log that only grows. Pinned here as the contract the liveness snapshot relies on."""
        sb = SourceFileLoader("romp_sdk_backend_stage0c", os.path.join(BIN, "romp_sdk_backend.py")).load_module()
        sd = tempfile.mkdtemp()
        p = os.path.join(sd, "states", self.SID + ".jsonl")
        os.makedirs(os.path.dirname(p))
        self.assertEqual(sb.last_state(sd, self.SID), {}, "no file → {}")
        rows = [{"t": NOW - i, "state": "working" if i % 2 else "waiting"} for i in range(3000, 0, -1)]
        rows.append({"t": NOW, "awaiting": True, "why": "x" * 6000})     # a big last row spans the first tail block
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(sb.last_state(sd, self.SID), rows[-1], "the literal last row, overlay rows included")
        self.assertIn("_lines_from_end", inspect.getsource(sb.last_state), "read from the tail, never a forward walk")

    def test_b_machine_cut_is_read_once_per_file_version(self):
        """Pins the contract, not the mechanism: upstream's _fold_records (97c0159e) superseded this branch's
        shared-rows reader here, and it keeps the property that mattered — an unchanged file is never reopened."""
        p = jd.STATE / "states" / (self.SID + ".jsonl")
        try:
            _write_rows(p, [{"t": NOW - 50, "state": "working"}, {"t": NOW - 40, "machineCut": "restart"},
                            {"t": NOW - 30, "state": "waiting"}])
            self.assertEqual(km._last_machine_cut(self.SID), (float(NOW - 40), "restart"))
            km._last_machine_cut(self.SID)
            with _OpenCounter(p) as c:
                km._last_machine_cut(self.SID)
            self.assertEqual(c.n, 0)
        finally:
            em._JSONL_CACHE.pop(str(p), None)
            try: os.unlink(p)
            except OSError: pass

    def test_c_nudge_times_and_postal_index_fold_the_same_rows_incrementally(self):
        nudge = jd.STATE / "nudge-events.jsonl"
        try:
            _write_rows(nudge, [{"gid": "g1", "t": NOW - 10}, {"gid": "g1", "t": NOW - 5}, {"gid": "g2", "t": NOW - 1}, {"nogid": 1}])
            self.assertEqual(km._nudge_times(), {"g1": [NOW - 10, NOW - 5], "g2": [NOW - 1]})
            _append_rows(nudge, [{"gid": "g2", "t": NOW}])
            with _OpenCounter(nudge) as c:
                self.assertEqual(km._nudge_times()["g2"], [NOW - 1, NOW])
            self.assertEqual(c.n, 1, "the grown log is opened once, for its tail")
        finally:
            em._JSONL_CACHE.pop(str(nudge), None); km._nudge_times_cache.clear()
            try: os.unlink(nudge)
            except OSError: pass
        msgs = jd.STATE / "timeline" / "messages.jsonl"
        try:
            _write_rows(msgs, [{"ev": "sent", "id": "m1", "from": "a", "from_id": "A", "to_id": "B", "body": "hi", "t": NOW - 3},
                               {"ev": "exec", "id": "m1", "t": NOW - 2}])
            km._postal_index_memo[0] = None
            idx = km._postal_index()
            self.assertEqual(set(idx), {"m1"}); self.assertEqual(idx["m1"]["body"], "hi")
            with _OpenCounter(msgs) as c:
                km._postal_index(); km._postal_wait_maps()
            self.assertEqual(c.n, 0)
        finally:
            em._JSONL_CACHE.pop(str(msgs), None); km._postal_index_memo[0] = None
            try: os.unlink(msgs)
            except OSError: pass

    def test_d_names_snapshot_reads_an_entry_once_per_version(self):
        km.NAMES.mkdir(parents=True, exist_ok=True)
        ent = km.NAMES / self.SID
        try:
            ent.write_text("web\t/tmp\t#abcdef\n")
            self.assertEqual(km._names_snapshot()[self.SID][0], "web")
            with _OpenCounter(ent) as c:
                for _ in range(5):
                    self.assertEqual(km._names_snapshot()[self.SID][0], "web")
            self.assertEqual(c.n, 0, "an unchanged entry is one stat")
            tmp = km.NAMES / (self.SID + ".tmp"); tmp.write_text("api\t/tmp\t#abcdef\n"); os.replace(tmp, ent)
            self.assertEqual(km._names_snapshot()[self.SID][0], "api", "a replaced entry is re-read")
            os.unlink(ent)
            self.assertNotIn(self.SID, km._names_snapshot())
            self.assertNotIn(self.SID, km._names_entry_memo, "a removed entry drops its memo")
        finally:
            km._names_entry_memo.pop(self.SID, None)
            try: os.unlink(ent)
            except OSError: pass

    def test_e_task_store_is_decoded_once_per_fingerprint_and_handed_out_as_copies(self):
        d = tempfile.mkdtemp()
        (open(os.path.join(d, "1.json"), "w")).write(json.dumps({"id": "1", "subject": "first", "status": "pending"}))
        (open(os.path.join(d, "2.json"), "w")).write(json.dumps({"id": "2", "subject": "second", "status": "completed"}))
        saved = km._task_store_resolve
        km._task_store_resolve = lambda fsid, fold=None: __import__("pathlib").Path(d)
        try:
            t1 = km._read_task_store(self.SID)
            self.assertEqual([t["id"] for t in t1], ["1", "2"])
            t1[0]["subject"] = "mutated by a caller"
            with _OpenCounter(os.path.join(d, "1.json")) as c:
                t2 = km._read_task_store(self.SID)
            self.assertEqual(c.n, 0, "an unchanged store is stats only")
            self.assertEqual(t2[0]["subject"], "first", "callers get copies")
            with open(os.path.join(d, "2.json"), "w") as f:
                f.write(json.dumps({"id": "2", "subject": "second", "status": "in_progress"}))
            self.assertEqual(km._read_task_store(self.SID)[1]["status"], "in_progress", "a rewritten file is seen")
        finally:
            km._task_store_resolve = saved
            km._task_store_memo.pop(str(d), None)

    def test_e2_the_task_store_fingerprint_moves_on_a_same_size_republish_in_one_tick(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "1.json")
        open(f, "w").write(json.dumps({"id": "1", "subject": "a", "status": "pending"}))
        st = os.stat(f)
        saved = km._task_store_known
        km._task_store_known = lambda fsid: __import__("pathlib").Path(d)
        try:
            fp1 = km._task_store_fp(self.SID)
            tmp = os.path.join(d, "1.json.tmp")
            open(tmp, "w").write(json.dumps({"id": "1", "subject": "b", "status": "pending"}))   # same length
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))                              # same stamp
            os.replace(tmp, f)
            self.assertNotEqual(fp1, km._task_store_fp(self.SID), "the inode moved even though size and mtime did not")
        finally:
            km._task_store_known = saved

    def test_f_the_awaiting_lift_skips_a_session_whose_inputs_did_not_move(self):
        sid = self.SID
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        gpath = jd.GOALDIR / (sid + ".json")
        gpath.write_text(json.dumps({"rompUuid": sid, "seq": 0, "nodes": {}, "placements": {}, "status": {}}))
        loads = []
        real = jd.load_goals
        jd.load_goals = lambda fsid: (loads.append(fsid), real(fsid))[1]
        saved_alive = km._alive_sessions
        km._alive_sessions = lambda now, tmux: [{"sid": sid, "path": str(gpath)}]
        tmux = {sid: {"state": "waiting", "bgTasks": []}}
        scan = []                                          # what the transcript pairs: a running watcher
        saved_scan = km._bg_scan_all_cached
        km._bg_scan_all_cached = lambda path: list(scan)
        try:
            km._lift_seen.pop(sid, None)
            km._lift_spent_awaiting(NOW, tmux)
            km._lift_spent_awaiting(NOW + 1, tmux)
            km._lift_spent_awaiting(NOW + 2, tmux)
            self.assertEqual(loads, [sid], "one load while nothing recorded moved")
            tmp = gpath.with_suffix(".tmp")                  # published the way save_goals publishes: tmp + replace
            tmp.write_text(json.dumps({"rompUuid": sid, "seq": 1, "nodes": {}, "placements": {}, "status": {}, "note": "longer"}))
            os.replace(tmp, gpath)
            km._lift_spent_awaiting(NOW + 3, tmux)
            self.assertEqual(len(loads), 2, "a store write is re-examined")
            tmux[sid]["bgTasks"] = [{"toolUseId": "t1", "status": "running"}]
            km._lift_spent_awaiting(NOW + 4, tmux)
            self.assertEqual(len(loads), 3, "a live task appearing is re-examined")
            tmux[sid]["subagents"] = [{"id": "a1"}]
            km._lift_spent_awaiting(NOW + 5, tmux)
            self.assertEqual(len(loads), 4, "a subagent appearing is re-examined")
            scan.append({"id": "toolu_1", "status": "running", "t": NOW, "deadline": NOW + 100})
            km._lift_spent_awaiting(NOW + 6, tmux)
            self.assertEqual(len(loads), 5, "a new dispatch is re-examined")
            km._lift_spent_awaiting(NOW + 150, tmux)
            self.assertEqual(len(loads), 5, "…and not again while its deadline has not passed")
            km._lift_spent_awaiting(NOW + 100 + 121, tmux)
            self.assertEqual(len(loads), 6, "the recorded deadline passing (plus grace) re-examines: the clock arm is keyed")
            km._lift_spent_awaiting(NOW + 100 + 122, tmux)
            self.assertEqual(len(loads), 6, "…once")
            boom = [True]
            jd.load_goals = lambda fsid: (loads.append(fsid), (_ for _ in ()).throw(RuntimeError("torn read")) if boom[0] else real(fsid))[1]
            scan.append({"id": "toolu_2", "status": "running", "t": NOW + 300})
            km._lift_spent_awaiting(NOW + 300, tmux)      # the ruling raises → not a ruling
            self.assertEqual(len(loads), 7)
            boom[0] = False
            km._lift_spent_awaiting(NOW + 301, tmux)      # …so the next cycle retries on the same inputs
            self.assertEqual(len(loads), 8, "a raised ruling is retried, not skipped")
        finally:
            jd.load_goals = real; km._alive_sessions = saved_alive; km._bg_scan_all_cached = saved_scan; km._lift_seen.pop(sid, None)
            try: os.unlink(gpath)
            except OSError: pass


if __name__ == "__main__":
    unittest.main()
