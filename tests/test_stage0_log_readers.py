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
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
from git_fixture import git, init_repo

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
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {}, {})]
        self.addCleanup(self._restore_paths)

    def _restore_paths(self):
        for k, v in self._saved_paths.items():
            setattr(jd, k, v)
        km._postal_index_memo[0] = None
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {}, {})]


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _append_rows(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _repo_on_main():
    """A throwaway repository with one commit on `main`, the shape test_d3 reads a branch from. Built through
    the shared runner (tests/git_fixture.py): `git commit` spawns `git maintenance run --auto`, which on
    recent git detaches and can still be writing into .git while the temp dir is removed (the CI flake
    "Directory not empty: '.git'", 2026-09-10); init_repo also writes the no-background keys into the repo's
    own config, so the git the KERNEL forks against it (the branch memo) obeys them too."""
    repo = tempfile.mkdtemp()
    init_repo(repo, "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init", ident=("t@example.invalid", "t"))   # main is born: the branch resolves
    return repo


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
            self.assertEqual(km._state_intervals(SID, "working", NOW)[-1], [NOW - 10, NOW, True])   # open: the build clock, marked
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
        km._parked_fold_cache.clear()                 # the feed's parked-handoff fold over this log (2026-09-18)
        _write_rows(jd.MESSAGES, [
            {"ev": "sent", "id": "m-1", "from_id": self.A, "to_id": self.B, "t": NOW - 100, "body": "please review", "park": True},
            {"ev": "sent", "id": "m-2", "from_id": self.A, "to_id": self.B, "t": NOW - 90, "body": "and this"},
            {"ev": "exec", "id": "m-2", "t": NOW - 80},
        ])
        (jd.STATE / "postal" / "mail" / self.B / "new").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "postal" / "mail" / self.B / "new" / "m-1").write_text("{}")

    def tearDown(self):
        em._JSONL_CACHE.pop(str(jd.MESSAGES), None)
        km._parked_fold_cache.clear()
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
        # _version_info is the real /version brain; it must expose the same view-stat keys the counters use
        self.assertEqual(set(km._version_info()["views"]), set(km._VIEW_STATS))


class BackendOwnsIsMemoized(unittest.TestCase):
    """Sessions.backend_for asks the SDK backend `owns(sid)` for every session in every tick job; it read
    and decoded the registry file each time. Now one stat answers while the file is unchanged."""

    SID = "11111111-2222-3333-4444-999999999921"

    def test_a_owns_reads_the_reg_once_per_file_version(self):
        sb = load_source("romp_sdk_backend_stage0", os.path.join(BIN, "romp_sdk_backend.py"))
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

    def test_a_transient_read_failure_is_not_latched_as_not_owned(self):
        # read_reg returns None on ANY OSError (EMFILE/EIO/EACCES), not only a missing file; caching that
        # as "not ours" latched a live session's backend False until its reg was rewritten (review find on
        # #933, 2026-09-07). A transient failure must not be cached; the next call re-reads.
        sb = load_source("romp_sdk_backend_stage0_t", os.path.join(BIN, "romp_sdk_backend.py"))
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(be.state_dir, self.SID, {"sid": self.SID, "name": "web", "cwd": "/tmp", "alive": True})
        self.assertTrue(be.owns(self.SID))
        be._owns_memo.clear()
        real = sb.read_reg_for_rmw
        boom = {"n": 0}
        def flaky(*a, **k):
            boom["n"] += 1
            return None if boom["n"] == 1 else real(*a, **k)   # first read fails transiently, then heals
        sb.read_reg_for_rmw = flaky
        try:
            # the one transient failure answers conservatively (no live thread here) but does NOT latch
            self.assertFalse(be.owns(self.SID), "a transient failure with no live thread answers not-owned for this call")
            self.assertNotIn(self.SID, be._owns_memo, "…but it is NOT cached (the bug latched False here)")
            self.assertTrue(be.owns(self.SID), "the healed read is owned")
            self.assertIn(self.SID, be._owns_memo, "…and now cached")
        finally:
            sb.read_reg_for_rmw = real


class ViewSignatureKeysOnExactInputs(_StateSandbox):
    """The feed/timeline signature used to bust every ≤3 s because the judge generation advanced after
    EVERY producer pass, changed store or not — and the per-session tuple missed the SDK snapshot facts the
    views render (subagent rows, a switch resolving), so those surfaced only via the 5 s time bucket. Now the
    generation moves only when a pass moved a store, a judge call starting or ending
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
        base = {"state": "working", "model": "m", "context": 10, "effort": "", "mode": "", "fast": "", "since": NOW - 60,
                "subagents": [], "bgTasks": [], "modelPending": False, "retryCount": 0}
        s1 = km._fleet_view_sig(now, {self.SID: dict(base)})
        self.assertEqual(s1, km._fleet_view_sig(now + 1, {self.SID: dict(base)}), "same inputs, same bucket → same sig")
        # `interrupting` is not in the tuple: no liveness row carries it, and the WS stop op marks the views
        # dirty itself (tests/test_kernel_timeline_split.py::SkeletonFromCache pins the keyed set)
        for k, v in (("subagents", [{"id": "a1"}]), ("bgTasks", [{"toolUseId": "t1"}]), ("context", 20),
                     ("modelPending", True), ("retryCount", 3)):
            changed = dict(base); changed[k] = v
            self.assertNotEqual(s1, km._fleet_view_sig(now, {self.SID: changed}), k)
        rid = jd._active_begin("closer", self.SID, 0)
        try:
            self.assertNotEqual(s1, km._fleet_view_sig(now, {self.SID: dict(base)}), "a judge call in flight")
        finally:
            jd._active_end(rid)


class EveryTabIsServedOnTheCompleteKey(_StateSandbox):
    """The watched chat tab used to rebuild from all atoms on every pusher cycle "by design", because its
    payload moves with inputs no file records; since 2026-09-03 it was served on a separate exact key
    beside the background tabs' file-stat key. There is ONE key for every tab now, the complete per-session
    signature (_chat_build_sig), and these tests pin the same observable through it: an identical world
    yields the same key and any moved payload input moves it, attributed to its component. Of the old
    key's inputs, the captions store is keyed through what the payload embeds from it (the postal cards'
    caption values, the postal component; tests/test_chat_build_sig_inputs.py moves one), and the
    in-flight judge set is not keyed because build_session does not read it (the census there is the
    check). Synthetic session, private sid."""

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
        self.saved_sdk = km._sdk
        km._sdk = lambda: None                                    # no backend owns it unless a test says otherwise
        km._built_chat.pop(self.SID, None)
        km._live_scope.usage = km._live_scope.spend_pause = None   # a drain scope another module left on this thread
        self._clear_stamps()

    def tearDown(self):
        km._sdk = self.saved_sdk
        for p in (self.path, self.tpath):
            try: os.unlink(p)
            except OSError: pass
        self._clear_stamps()
        km._built_chat.pop(self.SID, None)
        with km._watch_lock:
            km._watches[:] = [w for w in km._watches if w.get("sid") != self.SID]

    def _clear_stamps(self):
        for d in (km._interrupt_clicked, km._model_switch_pending, km._compact_clicked):
            d.pop(self.SID, None)

    def sig(self, tm=None, now=NOW, **kw):
        """The complete key, as the pusher takes it: the session, its liveness row, the push's clock and map."""
        tm = self.tm if tm is None else tm
        return km._chat_build_sig(self.sess, tm, now, live_map={self.SID: tm} if tm is not None else {}, **kw)

    def test_a_an_identical_world_yields_the_same_key_and_a_volatile_stamp_does_not_matter(self):
        s1 = self.sig()
        self.assertIsNotNone(s1)
        self.assertEqual(len(s1), len(km._CHAT_SIG_LABELS), "one value per labelled component")
        tm2 = dict(self.tm); tm2["snapT"] = NOW + 7
        self.assertEqual(s1, self.sig(tm2, NOW + 1), "snapT and the clock alone move nothing")

    def test_b_each_input_class_moves_the_key(self):
        s1 = self.sig()
        tm = dict(self.tm); tm["state"] = "working"
        self.assertNotEqual(s1, self.sig(tm), "a snapshot fact")
        _append_rows(self.path, [{"t": NOW - 5, "state": "working"}])
        s2 = self.sig()
        self.assertNotEqual(s1, s2, "a states row")
        # the judge's OUTPUT the payload reads is the goal store (the ledger, the seams, the awaiting stamps):
        # a publish moves the key at once, through the store's live identity, and no other tab's
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        gs = jd.GOALDIR / (self.SID + ".json")
        try:
            gs.write_text(json.dumps({"rompUuid": self.SID, "nodes": {}, "status": {}}))
            s3 = self.sig()
            self.assertNotEqual(s2, s3, "a goal store published")
            self.assertEqual(km._chat_sig_miss(s2, s3), ("store",), "...attributed to the store component alone")
            g0 = km._judge_gen[0]
            km._judge_gen[0] += 1
            try:
                self.assertEqual(self.sig(), s3, "the judge-pass counter is not a chat input")
            finally:
                km._judge_gen[0] = g0
        finally:
            try: os.unlink(gs)
            except OSError: pass

    def test_c_clock_booleans_flip_exactly_at_their_deadlines(self):
        """The clock enters the key only as the booleans the build derives from it, so the key moves at each
        crossing and at no other tick (the clock component)."""
        tm = {"state": "ready", "since": NOW - 3599, "bgTasks": []}
        s0 = self.sig(tm, NOW)
        self.assertEqual(s0, self.sig(tm, NOW + 1), "one second short of the hour: nothing moved")
        s2 = self.sig(tm, NOW + 2)
        self.assertNotEqual(s0, s2, "faded flips at the hour, and the key follows the flip")
        self.assertEqual(km._chat_sig_miss(s0, s2), ("clock",))
        km._compact_clicked[self.SID] = NOW - 179
        s3 = self.sig(tm, NOW + 2)                                # 181 s: the compaction's optimistic cap ran out (stamp popped)
        self.assertEqual(s2, s3, "a cap that already ran out is no fact: the key reads as if never clicked")
        km._compact_clicked[self.SID] = NOW - 177
        s4 = self.sig(tm, NOW + 2)                                # 179 s: still compacting, optimistically
        self.assertNotEqual(s3, s4, "an armed compaction is a clock fact")
        self.assertEqual(km._chat_sig_miss(s3, s4), ("clock",))
        s5 = self.sig(tm, NOW + 4)                                # 181 s: the cap ran out, and the key follows the flip
        self.assertEqual(s5, s3, "...back to the clicked-nothing key, exactly at the crossing")

    def test_d_a_live_task_expiring_moves_the_key_through_the_builds_own_filter(self):
        """The raw snapshot rows carry no deadline; the build joins each task's recorded deadline from the
        launch ledger and drops expired ones. The key carries that filtered set (the bg component), so an
        expiry, a tid dropping out, moves it exactly when the awaiting box would change (review 2026-09-03)."""
        saved = km._bg_live_norm
        rows = [{"tid": "t1", "desc": "watch", "t": NOW - 60}]
        km._bg_live_norm = lambda sid, path: list(rows)
        try:
            s1 = self.sig()
            self.assertEqual(s1, self.sig(now=NOW + 1))
            rows.clear()                                     # the watcher passed its deadline → filtered out
            s2 = self.sig(now=NOW + 2)
            self.assertNotEqual(s1, s2)
            self.assertEqual(km._chat_sig_miss(s1, s2), ("bg",))
        finally:
            km._bg_live_norm = saved

    def test_d2_a_running_tasks_output_growing_moves_the_key(self):
        """The task box shows a running task's output tail, read live from a file outside the state root. The
        build records each output it read as a dependency of its payload (_chat_build_deps), and the key
        re-stats the recorded file every cycle (the taskout component), so the tail keeps moving (review
        2026-09-03; the old watched-tab key stat'd the running tasks' files itself)."""
        out = jd.STATE / "task-out.log"; out.write_text("line 1\n")
        rec = {"task_outs": [(str(out), km._chat_stat_key(str(out)))], "pl_pending": [], "pl_at": (), "pl_check": None,
               "postal_any": False, "postal_cards": []}
        s1 = self.sig(deps=rec)
        self.assertEqual(s1, self.sig(deps=rec))
        with open(out, "a") as f:
            f.write("line 2\n")
        s2 = self.sig(deps=rec)
        self.assertNotEqual(s1, s2, "the tail grew")
        self.assertEqual(km._chat_sig_miss(s1, s2), ("taskout",))

    def test_d3_the_branch_of_a_subdirectory_cwd_is_keyed(self):
        """build_session reads the branch of the registered cwd's tree (and of the last edited file's tree);
        the key carries them by value through the branch memo, which is keyed on the tree's HEAD."""
        repo = _repo_on_main()
        sub = os.path.join(repo, "pkg"); os.makedirs(sub)
        saved_cwd = km._cwd_of
        km._cwd_of = lambda sid: sub                       # a cwd INSIDE the repo, not its top
        try:
            s1 = self.sig()
            cwd_i = km._CHAT_SIG_LABELS.index("cwd")
            self.assertEqual(s1[cwd_i][1], "main", "the repo's branch is read for a subdirectory cwd: %r" % (s1[cwd_i],))
            git(repo, "checkout", "-q", "-b", "feature")
            s2 = self.sig()
            self.assertNotEqual(s1, s2, "a branch switch moves the key")
            self.assertEqual(s2[cwd_i][1], "feature")
        finally:
            km._cwd_of = saved_cwd

    def test_the_fixture_repos_forbid_background_git_work(self):
        """The kernel runs git against test_d3's repo through its own subprocess (_git_out, the branch memo),
        so the no-background keys must sit in the repo's own config, not only on the fixture runner's
        command line."""
        repo = _repo_on_main()
        self.assertEqual(git(repo, "config", "--local", "--get", "maintenance.auto").stdout.strip(), "false", repo)

    def test_e_the_backend_leg_and_the_watches_move_the_key(self):
        """A stub backend stands in for the SDK: the live-tail revision, the send queue and the brackets each
        move the key; so does a kernel-owned watch this session registered (review 2026-09-03; the watch
        registry is read as the build reads it, from the in-memory list its file persists)."""
        class Stub:
            rev = 0; queue = []; comp = False; clr = False
            def owns(self, sid): return True
            def live_rev(self, sid): return self.rev
            def live_atoms(self, sid): return []
            def pending_queued(self, sid): return list(self.queue)
            def compacting(self, sid): return self.comp
            def clearing(self, sid): return self.clr
            def pending_cut(self, sid): return ""
        stub = Stub()
        km._sdk = lambda: stub
        s1 = self.sig()
        self.assertEqual(s1, self.sig())
        stub.rev += 1;  s2 = self.sig(); self.assertNotEqual(s1, s2, "a live atom")
        self.assertEqual(km._chat_sig_miss(s1, s2), ("live",))
        stub.queue = ["x"]; s3 = self.sig(); self.assertNotEqual(s2, s3, "a queued send")
        stub.comp = True;  s4 = self.sig(); self.assertNotEqual(s3, s4, "compacting")
        with km._watch_lock:
            km._watches.append({"id": "w-stage0", "cmd": "true", "every": 60, "timeoutS": 600, "sid": self.SID,
                                "note": "a build", "at": NOW})
        s5 = self.sig()
        self.assertNotEqual(s4, s5, "a watch armed")
        self.assertEqual(km._chat_sig_miss(s4, s5), ("watch",))

    def test_g_the_key_carries_the_snapshot_facts_a_tabs_chips_render(self):
        """The judge generation no longer advances every pass, so a tab's chips (state, retrying, subagents,
        pending picks...) are keyed on the snapshot row itself (review 2026-09-05), minus its volatile stamp."""
        b1 = self.sig()
        self.assertIsNotNone(b1)
        tm2 = dict(self.tm); tm2["snapT"] = NOW + 30
        self.assertEqual(b1, self.sig(tm2), "the volatile stamp alone moves nothing")
        for k, v in (("state", "retrying"), ("subagents", ["a"]), ("retryCount", 3), ("modelPending", True),
                     ("connected", True), ("spawning", True), ("model", "other"), ("auth", "key")):
            tm3 = dict(self.tm); tm3[k] = v
            b3 = self.sig(tm3)
            self.assertNotEqual(b1, b3, k)
            self.assertIn("row", km._chat_sig_miss(b1, b3), k)   # modelPending is a clock input too (_model_pending_now)
        self.assertNotEqual(b1, km._chat_build_sig(self.sess, None, NOW, live_map={}),
                            "no row for the sid: a different (row-less) key, never a false hit")

    def test_h_the_spend_hold_moves_the_key_of_a_tab_with_a_queued_send_and_marks_the_views_dirty(self):
        """The account-level hold is what a QUEUED bubble names, so the key folds it exactly when the build
        can render one (a queue, parked ops, a non-forwarding backend's in-flight echo) and never asks otherwise; the old
        watched-tab key stat'd retry-paused.json for every watched tab. The writer still stamps the dirty
        mark: the feed and the timeline rebuild past it."""
        class Stub:
            def owns(self, sid): return True
            def live_rev(self, sid): return 0
            def live_atoms(self, sid): return []
            def pending_queued(self, sid): return ["a message waiting on the account"]
            def unqueue(self, sid, idx, expect=None): return None
            def queue_recallable(self, sid): return True
            def compacting(self, sid): return False
            def clearing(self, sid): return False
            def pending_cut(self, sid): return ""
            def launch_error(self, sid): return None
        km._sdk = lambda: Stub()
        # the hold reader, held for this test over the real pause file: other modules loading this same kernel
        # module stub _limit_hold to a no-op at import (the drive-op gates), so the shared copy's may be theirs
        saved_hold = km._limit_hold
        km._limit_hold = lambda sid: ({"reason": "spend", "resetsAt": None, "what": "waiting for your monthly spend limit to be raised"}
                                      if (km._retry_paused_on() and km._retry_pause_reason() == "spend") else None)
        lim = km._CHAT_SIG_LABELS.index("limit")
        try:
            s1 = self.sig()
            self.assertIsNone(s1[lim], "a queue, no hold: read, and None")
            before = km._views_dirty[0]
            km._set_retry_paused(True, reason="spend")
            try:
                s2 = self.sig()
                self.assertNotEqual(s1, s2, "the spend hold the queued bubble renders is in the key")
                self.assertEqual(km._chat_sig_miss(s1, s2), ("limit",))
                self.assertEqual(s2[lim]["reason"], "spend")
                self.assertGreater(km._views_dirty[0], before, "every writer of the hold publishes the flip to the feed and timeline")
            finally:
                km._set_retry_paused(False)
            km._sdk = lambda: None                                # nothing queued, nothing parked, no echo
            km._set_retry_paused(True, reason="spend")
            try:
                self.assertIsNone(self.sig()[lim], "with no queued bubble to render, the hold is not read and not keyed")
            finally:
                km._set_retry_paused(False)
        finally:
            km._limit_hold = saved_hold


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
        sb = load_source("romp_sdk_backend_stage0b", os.path.join(BIN, "romp_sdk_backend.py"))
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
        sb = load_source("romp_sdk_backend_stage0c", os.path.join(BIN, "romp_sdk_backend.py"))
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
        loads = []                                         # the tick's store reads: the shared-view probe
        real = jd.load_goals_shared
        jd.load_goals_shared = lambda fsid: (loads.append(fsid), real(fsid))[1]
        saved_alive = km._alive_sessions
        km._alive_sessions = lambda now, live_map: [{"sid": sid, "path": str(gpath)}]
        live = {sid: {"state": "waiting", "bgTasks": []}}
        scan = []                                          # what the transcript pairs: a running watcher
        saved_scan = km._bg_scan_lift_cached               # the lift's own view of the pairing (2026-10-03)
        km._bg_scan_lift_cached = lambda path: list(scan)
        try:
            km._lift_seen.pop(sid, None)
            km._lift_spent_awaiting(NOW, live)
            km._lift_spent_awaiting(NOW + 1, live)
            km._lift_spent_awaiting(NOW + 2, live)
            self.assertEqual(loads, [sid], "one read while nothing recorded moved")
            tmp = gpath.with_suffix(".tmp")                  # published the way save_goals publishes: tmp + replace
            tmp.write_text(json.dumps({"rompUuid": sid, "seq": 1, "nodes": {}, "placements": {}, "status": {}, "note": "longer"}))
            os.replace(tmp, gpath)
            km._lift_spent_awaiting(NOW + 3, live)
            self.assertEqual(len(loads), 2, "a store write is re-examined")
            live[sid]["bgTasks"] = [{"toolUseId": "t1", "status": "running"}]
            km._lift_spent_awaiting(NOW + 4, live)
            self.assertEqual(len(loads), 3, "a live task appearing is re-examined")
            live[sid]["subagents"] = [{"id": "a1"}]
            km._lift_spent_awaiting(NOW + 5, live)
            self.assertEqual(len(loads), 4, "a subagent appearing is re-examined")
            scan.append({"id": "toolu_1", "status": "running", "t": NOW, "deadline": NOW + 100})
            km._lift_spent_awaiting(NOW + 6, live)
            self.assertEqual(len(loads), 5, "a new dispatch is re-examined")
            km._lift_spent_awaiting(NOW + 150, live)
            self.assertEqual(len(loads), 5, "…and not again while its deadline has not passed")
            km._lift_spent_awaiting(NOW + 100 + 121, live)
            self.assertEqual(len(loads), 6, "the recorded deadline passing (plus grace) re-examines: the clock arm is keyed")
            km._lift_spent_awaiting(NOW + 100 + 122, live)
            self.assertEqual(len(loads), 6, "…once")
            boom = [True]
            jd.load_goals_shared = lambda fsid: (loads.append(fsid), (_ for _ in ()).throw(RuntimeError("torn read")) if boom[0] else real(fsid))[1]
            scan.append({"id": "toolu_2", "status": "running", "t": NOW + 300})
            km._lift_spent_awaiting(NOW + 300, live)      # the ruling raises → not a ruling
            self.assertEqual(len(loads), 7)
            boom[0] = False
            km._lift_spent_awaiting(NOW + 301, live)      # …so the next cycle retries on the same inputs
            self.assertEqual(len(loads), 8, "a raised ruling is retried, not skipped")
        finally:
            jd.load_goals_shared = real; km._alive_sessions = saved_alive; km._bg_scan_lift_cached = saved_scan; km._lift_seen.pop(sid, None)
            try: os.unlink(gpath)
            except OSError: pass


if __name__ == "__main__":
    unittest.main()
