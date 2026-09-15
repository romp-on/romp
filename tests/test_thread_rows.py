#!/usr/bin/env python3
"""GET /sessions?threads=1 (the user 2026-08-22): comment-thread sessions ride the unified session
list ONLY when asked — the postal bus asks, so a thread can mail its parent under its own name;
every existing consumer (Obsidian picker, romp sessions, the default bus listing) is unchanged.
Drives the REAL Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "11111111-2222-3333-4444-555555555555"
PARENT_NAME = "web"
TSID = "66666666-7777-8888-9999-000000000000"


def _mk_thread(parent, tsid, name="web-comment-1", status="open", reg_name=None, alive=True, root=None):
    """A comment thread as the kernel keeps it: a row in the parent's comments store + an SDK reg
    carrying threadOf (reg_name None = the row's name, the modern shape). `root` defaults to the
    kernel's own state dir (the HTTP doors read it); a test that drives a backend INSTANCE passes
    its private dir — km.jd.STATE is a shared module object every test module's load re-points."""
    root = root or km.jd.STATE
    cdir = root / "comments"
    cdir.mkdir(parents=True, exist_ok=True)
    row = {"tid": tsid, "sid": tsid, "status": status, "createdT": 1, "lastSeenT": 1}
    if name:
        row["name"] = name
    (cdir / (parent + ".json")).write_text(json.dumps({"threads": [row]}))
    sdir = root / "sdk"
    sdir.mkdir(parents=True, exist_ok=True)
    reg = {"sid": tsid, "cwd": "/tmp", "alive": alive, "threadOf": parent, "lastSid": tsid}
    if reg_name or name:
        reg["name"] = reg_name or name
    (sdir / (tsid + ".json")).write_text(json.dumps(reg))


def _rm_threads():
    for f in list((km.jd.STATE / "comments").glob("*.json")) + list((km.jd.STATE / "sdk").glob("*.json")):
        f.unlink()


class RealListingSplit(unittest.TestCase):
    """Both directions of the design against the REAL backend filter (the user 2026-08-22): a threadOf
    reg is absent from live_sessions/_session_rows — what GET /sessions and the pusher's tab payload
    are built from — and present under thread_sessions/?threads=1."""

    def test_a_thread_reg_splits_the_real_way(self):
        # a hermetic backend INSTANCE over a PRIVATE state dir — never km._sdk() (its lazy build runs
        # boot reconcile inside the test process) and never the shared km.jd.STATE (a module object
        # every later test module's load re-points at its own tempdir)
        from pathlib import Path
        sb = load_source("romp_sdk_backend_rows", os.path.join(BIN, "romp_sdk_backend.py"))
        root = Path(tempfile.mkdtemp())
        be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        _mk_thread(PARENT, TSID, name="web-comment-1", root=root)
        saved = km._sdk
        km._sdk = lambda: be
        try:
            self.assertNotIn(TSID, be.live_sessions(), "hidden from the tab/listing source")
            self.assertIn(TSID, be.thread_sessions(), "served only to callers that ask")
            self.assertNotIn(TSID, [r["id"] for r in km._session_rows()])
        finally:
            km._sdk = saved


class ThreadRowsRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._saved = (km._session_rows, km._thread_rows)
        if hasattr(km, "_sessions_listing_reset"):
            km._sessions_listing_reset()                         # the kept listing is process-global: a stubbed builder must not serve an earlier build
        km._session_rows = lambda: [{"id": PARENT, "name": "web", "state": "working"}]
        km._thread_rows = lambda: [{"id": TSID, "name": "web-comment-1", "state": "working",
                                    "thread": True, "parent": PARENT}]

    def tearDown(self):
        km._session_rows, km._thread_rows = self._saved

    def _get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def test_new_with_a_threads_name_opens_the_thread_never_a_namesake(self):
        # T223 (2026-09-01): a model-set sweep drove `romp new --model … <name>` over every reg,
        # threads included; /new's already-live check hides threads by design, so for 20 dormant
        # thread names it CREATED 18 namesake top-level sessions (each with its own CLI process,
        # each a tab). A thread's name must answer as the existing thread — prefs applied to it,
        # nothing minted.
        created, prefs = [], []
        saved = (km._sdk_ready, km._create_sdk_session, km._apply_new_session_prefs, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._apply_new_session_prefs = lambda sid, b: (prefs.append((sid, b.get("model"))), {"model": b.get("model")})[1]
        km._live_names = lambda live_map: {}
        _mk_thread(PARENT, TSID, name="web-comment-1")
        try:
            out = self._post("/new", {"name": "web-comment-1", "dir": "/tmp", "model": "claude-fable-5-1"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._apply_new_session_prefs, km._live_names = saved
            _rm_threads()
        self.assertEqual(created, [], "a thread's name must never mint a namesake session")
        self.assertTrue(out.get("ok") and out.get("existing"), out)
        self.assertEqual(out.get("id"), TSID, "the idempotent open lands on the thread")
        self.assertTrue(out.get("thread"), "the caller learns it addressed a thread")
        self.assertEqual(out.get("parent"), PARENT)
        self.assertEqual(prefs, [(TSID, "claude-fable-5-1")], "the sweep's intent — prefs on the thread")

    def test_new_with_a_fresh_name_still_creates(self):
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda live_map: {}
        try:
            out = self._post("/new", {"name": "brand-new", "dir": "/tmp"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
        self.assertEqual(created, ["brand-new"])
        self.assertFalse(out.get("thread"))

    def test_a_legacy_threads_registry_name_is_gated_too(self):
        # the review's catch: a pre-naming thread's store row has NO name — its reg says
        # "thread-<hash>" (what the sweep read) and _thread_rows shows the bare hash; both must
        # refuse, or the exact three "thread-<hash>" namesakes of T223 recur
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda live_map: {}
        _mk_thread(PARENT, TSID, name=None, reg_name="thread-" + TSID[:8])
        try:
            for nm in ("thread-" + TSID[:8], TSID[:8]):
                out = self._post("/new", {"name": nm, "dir": "/tmp"})
                self.assertTrue(out.get("thread"), (nm, out))
                self.assertEqual(out.get("id"), TSID)
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
            _rm_threads()
        self.assertEqual(created, [])

    def test_a_dormant_thread_whose_parent_was_ended_is_still_gated(self):
        # _comment_kill_all flips open threads alive=False with their rows still open — a revived
        # parent finds them again, so their names stay taken
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda live_map: {}
        _mk_thread(PARENT, TSID, name="web-comment-1", alive=False)
        try:
            out = self._post("/new", {"name": "web-comment-1", "dir": "/tmp"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
            _rm_threads()
        self.assertTrue(out.get("thread"))
        self.assertEqual(created, [])

    def test_a_promoted_threads_old_name_is_free(self):
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda live_map: {}
        _mk_thread(PARENT, TSID, name="web-comment-1", status="promoted")
        try:
            self._post("/new", {"name": "web-comment-1", "dir": "/tmp"})
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names = saved
            _rm_threads()
        self.assertEqual(created, ["web-comment-1"], "promoted = a real session now; its old row holds nothing")

    def test_an_unreadable_store_refuses_instead_of_minting(self):
        # the first cut returned {} on any exception — a silently reopened door. Unverifiable refuses.
        created = []
        saved = (km._sdk_ready, km._create_sdk_session, km._live_names, km._thread_names)
        km._sdk_ready = lambda: True
        km._create_sdk_session = lambda nm, cwd, **kw: (created.append(nm), ("99999999-0000-0000-0000-000000000000", {}))[1]
        km._live_names = lambda live_map: {}
        km._thread_names = lambda: None
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post("/new", {"name": "anything", "dir": "/tmp"})
            self.assertEqual(cm.exception.code, 503)
        finally:
            km._sdk_ready, km._create_sdk_session, km._live_names, km._thread_names = saved
        self.assertEqual(created, [])

    def test_fork_and_rename_refuse_a_threads_name(self):
        saved = km._live_names
        km._live_names = lambda live_map: {PARENT_NAME: PARENT}
        _mk_thread(PARENT, TSID, name="web-comment-1")
        try:
            out = self._post("/fork", {"parent": PARENT_NAME, "name": "web-comment-1"})
            self.assertFalse(out.get("ok"))
            self.assertIn("comment thread", out.get("error") or "")
            out = self._post("/rename", {"target": PARENT_NAME, "name": "web-comment-1"})
            self.assertFalse(out.get("ok"))
            self.assertIn("comment thread", out.get("error") or "")
        finally:
            km._live_names = saved
            _rm_threads()

    def test_the_ws_doors_wear_the_same_gate_before_they_act(self):
        import inspect
        src = inspect.getsource(km.Handler._dispatch_ws)
        self.assertLess(src.index("elif _thread_name_refusal(nm, _thread_names())"),
                        src.index('elif msg.get("backend") in (None, "", "sdk"):'),
                        "the create dialog refuses a thread's name BEFORE the sdk create arm")
        ws = inspect.getsource(km._drive) if hasattr(km, "_drive") else ""
        src2 = ws or inspect.getsource(km.Handler._dispatch_ws)
        self.assertIn("_thread_name_refusal(str(msg[\"name\"]).strip(), _thread_names())", src2 + ws,
                      "forkSession refuses a thread's name")
        self.assertIn("elif _thread_name_refusal(new, _thread_names()):", src2 + ws,
                      "renameSession refuses a thread's name")

    def test_sid_of_reaches_a_thread_by_name(self):
        _mk_thread(PARENT, TSID, name="web-comment-1")
        try:
            self.assertEqual(km._sid_of("web-comment-1"), TSID, "an explicit send by name lands on the thread")
            self.assertEqual(km._sid_of("no-such-name"), "no-such-name")
        finally:
            _rm_threads()

    def test_thread_names_maps_every_name_a_thread_answers_to(self):
        _mk_thread(PARENT, TSID, name="web-comment-1", reg_name="web-comment-1")
        try:
            self.assertEqual(km._thread_names(), {"web-comment-1": (TSID, PARENT)})
        finally:
            _rm_threads()

    def test_default_listing_hides_threads(self):
        rows = self._get("/sessions")
        self.assertEqual([r["id"] for r in rows], [PARENT], "every existing consumer sees exactly what it saw")

    def test_threads_param_appends_flagged_rows(self):
        rows = self._get("/sessions?threads=1")
        self.assertEqual([r["id"] for r in rows], [PARENT, TSID])
        t = rows[1]
        self.assertTrue(t.get("thread"), "flagged so the bus can mark it as a minor player")
        self.assertEqual(t.get("parent"), PARENT, "the parent sid rides for reply resolution")


class ThreadWakePinsStand(unittest.TestCase):
    """A dormant comment thread wakes on the model its reg holds — PINS STAND. The T223 rider had the
    backend's `_ensure` consult a kernel-installed hook (`SdkBackend.thread_wake_model`) that re-points a
    dormant thread registered on a SUPERSEDED full id to its family's newest at its next explicit wake.
    That targeted the artefact where a FAMILY click wrote the head's full id into the reg — an
    accidental pin. With the alias as the family default a full id in reg.model is a DELIBERATE one: the
    version submenu writes the pick verbatim, the create dialog sends a pinned family's id, and the
    marker-gated boot pass treats every post-migration head as the user's (the way back to floating is
    the Latest gesture, never a pass). With no accidental heads left to heal, the remap would override
    only deliberate pins — so the kernel wires no wake hook, and a thread pinned to a legacy version
    comes up ON that version; a thread on an alias floats as before. The backend's consult in `_ensure`
    stays as it is (inert with no hook installed)."""

    THREAD = {"threadOf": PARENT, "spawnedAt": 1700000000}   # has run before: dormant, not a fresh fork

    class _Rec:
        made = []

        def __init__(self, backend, reg):
            self.reg = dict(reg)
            self.thread = mock.Mock(is_alive=lambda: True)
            self.on_boot_settled = None
            ThreadWakePinsStand._Rec.made.append(self.reg)

        def start(self):
            pass

    def setUp(self):
        # a catalog in which claude-fable-5 IS superseded — the one shape a wake remap would act on
        self._saved = {fam: [dict(v) for v in vs] for fam, vs in km.MODEL_VERSIONS.items()}
        km.MODEL_VERSIONS["fable"][:] = [{"value": "claude-fable-5-1", "label": "Fable 5.1"},
                                         {"value": "claude-fable-5", "label": "Fable 5"},
                                         {"value": "fable", "label": "Fable (newest)"}]

    def tearDown(self):
        for fam, vs in self._saved.items():
            km.MODEL_VERSIONS[fam][:] = vs

    @staticmethod
    def _kernel_wired_hook():
        """The wake hook exactly as the kernel installs it on the backend it builds, read off
        `_sdk_locked`'s source (building the real backend in-process runs a boot reconcile — this
        file's first test says why not) and resolved against the kernel module: None when the kernel
        wires none. So the wake below runs under whatever hook the kernel would give a live backend."""
        import inspect
        import re
        m = re.search(r"thread_wake_model\s*=\s*(\w+)", inspect.getsource(km._sdk_locked))
        return getattr(km, m.group(1)) if m else None

    def _wake(self, model):
        """Wake a dormant thread registered on `model` through a hermetic backend INSTANCE wired the
        kernel's way; returns (the model the spawned session got, the model the reg holds after)."""
        from pathlib import Path
        sb = load_source("romp_sdk_backend_rows", os.path.join(BIN, "romp_sdk_backend.py"))
        root = Path(tempfile.mkdtemp())
        be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        be.thread_wake_model = self._kernel_wired_hook()
        sb.write_reg(root, TSID, {"sid": TSID, "name": "web-comment-1", "cwd": "/tmp", "alive": True,
                                  "lastSid": TSID, "model": model, **self.THREAD})
        self._Rec.made = []
        with mock.patch.object(sb, "SdkSession", self._Rec):
            be._ensure(TSID)
        return self._Rec.made[0]["model"], sb.read_reg(root, TSID).get("model")

    def test_a_dormant_thread_pinned_to_a_legacy_version_wakes_on_it(self):
        self.assertEqual(self._wake("claude-fable-5"), ("claude-fable-5", "claude-fable-5"),
                         "a full id in the reg is the user's pin — it stands at the wake, on disk too")

    def test_a_dormant_thread_on_an_alias_wakes_floating(self):
        self.assertEqual(self._wake("fable"), ("fable", "fable"),
                         "an alias auto-tracks in the CLI; nothing to do at the wake")

    def test_the_kernel_wires_no_wake_remap(self):
        import inspect
        src = inspect.getsource(km._sdk_locked)
        self.assertNotIn("thread_wake_model = _family_newest_model", src,
                         "the remap would re-point deliberate pins")
        self.assertIsNone(self._kernel_wired_hook(), "no hook at all: the backend's consult stays inert")


class ThreadRowsBuilder(unittest.TestCase):
    def test_thread_rows_join_the_name_from_the_parents_comments_store(self):
        class FakeBE:
            def thread_sessions(self):
                return {TSID: {"state": "waiting", "threadOf": PARENT}}
        saved = (km._sdk, km._load_comments, km._cwd_of)
        km._sdk = lambda: FakeBE()
        km._load_comments = lambda sid: ({"threads": [{"tid": TSID, "name": "web-comment-1"}]}
                                         if sid == PARENT else {})
        km._cwd_of = lambda sid: ""
        try:
            rows = km._thread_rows()
        finally:
            km._sdk, km._load_comments, km._cwd_of = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "web-comment-1",
                         "the comments store is where a thread's editable name lives")
        self.assertEqual(rows[0]["parent"], PARENT)
        self.assertTrue(rows[0]["thread"])


OLD_FSID = "aaaaaaaa-2222-3333-4444-555555555501"   # web's transcript before its /clear
NEW_FSID = "aaaaaaaa-2222-3333-4444-555555555502"   # the one the /clear minted: web's reg lastSid now
FORK_FSID = "aaaaaaaa-2222-3333-4444-555555555503"  # the far end of a resume fork api made off OLD_FSID
WEB = "11111111-2222-3333-4444-5555555555a1"
API = "11111111-2222-3333-4444-5555555555a2"
UNKNOWN_FSID = "aaaaaaaa-2222-3333-4444-555555555599"
T_WEB = "66666666-7777-8888-9999-0000000000a1"      # a comment thread of web
OLD_TFSID = "aaaaaaaa-2222-3333-4444-555555555511"  # the thread's transcript before ITS /clear
NEW_TFSID = "aaaaaaaa-2222-3333-4444-555555555512"  # the one its /clear minted: the thread reg's lastSid now
FORK = "11111111-2222-3333-4444-5555555555a3"       # a deliberate fork of web, made while web ran a transcript below
FORK_HEAD = "aaaaaaaa-2222-3333-4444-555555555521"  # the fork's own later /clear head


class SessionByFsidRoute(unittest.TestCase):
    """GET /sessions/by-fsid?fsid=<id> (2026-09-15): the ONE live session's /sessions row whose transcript ids
    include the given one — the sid, the current transcript, each /clear episode head, both ends of every resume
    fork. The postal bus asks it for a session whose CLAUDE_CODE_SESSION_ID matches no row by id or lastSid: a
    session's postal MCP server keeps the id its CLI started with for the process's life, a /clear moves the
    row's lastSid, and every message the session then sent through its tools arrived unattributed. Driven over
    HTTP against the real Handler with a hermetic backend INSTANCE over the kernel's own state root (the row's
    lastSid and the backend's transcript records must read the SAME regs); Sessions.live and the names helpers
    stubbed the way the listing tests stub them. A session born as a fork carries its parent's fork-time
    transcript in its own records (the seed row, the reg's birth lastSid): lineage the route never reads as
    ownership, pinned below. Synthetic ids only."""

    @classmethod
    def setUpClass(cls):
        ThreadingHTTPServer = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.sb = load_source("romp_sdk_backend_rows", os.path.join(BIN, "romp_sdk_backend.py"))
        self.root = km.jd.STATE
        self.be = self.sb.SdkBackend(self.root, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        self.asked = []                                   # the sids whose transcript records the route read
        real = getattr(self.be, "known_fsids", None)      # absent on a source without the surface: the tests
        if real is not None:                              # below then fail on the route's ANSWER, not here
            self.be.known_fsids = lambda sid: (self.asked.append(sid), real(sid))[1]
        names = self.names = {WEB: ("web", "/tmp/notes-api", "#112233", "#ffffff"),
                              API: ("api", "/tmp/notes-api", "#445566", "#ffffff")}
        self.live = {WEB: {"state": "idle", "backend": "sdk"}, API: {"state": "working", "backend": "sdk"}}
        self._saved = (km._sdk, km.Sessions.live, km._name_of, km._cwd_of, km._identity_of)
        km._sdk = lambda: self.be
        km.Sessions.live = staticmethod(lambda: dict(self.live))
        km._name_of = lambda sid: names.get(sid, (sid[:8],))[0]
        km._cwd_of = lambda sid: names.get(sid, ("", ""))[1]
        km._identity_of = lambda sid: names.get(sid, ("", "", "", ""))[2:4]
        # web /cleared once: its reg's lastSid moved OLD → NEW and its episode log holds both heads (the real
        # writers: the backend's reg, the judge's episode row); api never did
        self.sb.write_reg(self.root, WEB, {"sid": WEB, "cwd": "/tmp/notes-api", "alive": True, "lastSid": NEW_FSID})
        self.sb.write_reg(self.root, API, {"sid": API, "cwd": "/tmp/notes-api", "alive": True, "lastSid": API})
        km.jd.append_episode(WEB, "head-1", OLD_FSID, 1)
        km.jd.append_episode(WEB, "head-2", NEW_FSID, 2)

    def tearDown(self):
        km._sdk, km.Sessions.live, km._name_of, km._cwd_of, km._identity_of = self._saved
        for f in (self.root / "sdk" / (WEB + ".json"), self.root / "sdk" / (API + ".json"),
                  self.root / "sdk" / (T_WEB + ".json"), self.root / "sdk" / (FORK + ".json"),
                  self.root / "comments" / (WEB + ".json"),
                  self.root / "episodes" / (WEB + ".jsonl"), self.root / "episodes" / (T_WEB + ".jsonl"),
                  self.root / "episodes" / (FORK + ".jsonl"),
                  self.root / "states" / (WEB + ".jsonl"), self.root / "states" / (API + ".jsonl"),
                  self.root / "states" / (T_WEB + ".jsonl"), self.root / "states" / (FORK + ".jsonl")):
            if f.is_dir():
                shutil.rmtree(f)      # the fault test parks a directory where a ledger goes
            elif f.exists():
                f.unlink()

    def _module_reads(self):
        """Count the backend module's known_fsids reads by sid — the ledger parse the memo exists to skip.
        Returns (the list, restore)."""
        reads, real = [], self.sb.known_fsids
        self.sb.known_fsids = lambda state_dir, sid, *a, **k: (reads.append(sid), real(state_dir, sid, *a, **k))[1]
        return reads, lambda: setattr(self.sb, "known_fsids", real)

    def _get(self, path, token=True):
        """(status, body) — the body parsed as JSON when it is JSON, else the text."""
        headers = {"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]} if token else {}
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                body = json.loads(body)
            except ValueError:
                pass
            return e.code, body

    def _by(self, fsid):
        return self._get("/sessions/by-fsid?fsid=" + urllib.parse.quote(fsid, safe=""))

    def _fork_web(self, at=OLD_FSID, born=False):
        """web forked into FORK while its transcript was `at`, recorded the way the kernel records a fork: the
        reg backend.fork births — lastSid the PARENT's transcript plus the forkOf/forkAt launch flags and the
        durable forkedFrom — left in that pre-init window when `born`, else with lastSid flipped to the fork's
        own sid and the flags spent, as the CLI's init leaves it; and the two episode rows _seed_fork_stores
        writes (the conversation root under the parent's fsid, the cut under the fork's own). The fork is
        live and listed."""
        reg = {"sid": FORK, "name": "web-fork", "cwd": "/tmp/notes-api", "alive": True,
               "lastSid": at if born else FORK,
               "forkedFrom": {"sid": WEB, "name": "web", "cut": "cut-1", "t": 5}}
        if born:
            reg["forkOf"], reg["forkAt"] = WEB, "cut-1"
        self.sb.write_reg(self.root, FORK, reg)
        km.jd.append_episode(FORK, "root-1", at, 1)
        km.jd.append_episode(FORK, "cut-1", FORK, 5)
        self.names[FORK] = ("web-fork", "/tmp/notes-api", "#778899", "#ffffff")
        self.live[FORK] = {"state": "idle", "backend": "sdk"}

    def test_a_cleared_sessions_prior_transcript_answers_with_its_row(self):
        # red on main: no such route (a 404 "not found"). The pre-/clear id — what web's postal MCP server still
        # carries — answers with web's row: the stable sid, the live name, and lastSid the CURRENT transcript,
        # exactly as /sessions publishes it
        code, row = self._by(OLD_FSID)
        self.assertEqual(code, 200, row)
        self.assertEqual((row["id"], row["name"], row["lastSid"]), (WEB, "web", NEW_FSID))
        self.assertEqual(row, next(r for r in km._session_rows() if r["id"] == WEB),
                         "the same row GET /sessions serves — one builder, so the two cannot drift")
        self.assertEqual(self._by(NEW_FSID)[1]["id"], WEB, "the current transcript id resolves too")
        self.assertEqual(self._by(WEB)[1]["id"], WEB, "and the sid itself")
        self.assertEqual(self._by(API)[1]["id"], API, "a session that never /cleared answers to its sid")

    def test_an_unknown_transcript_is_a_404_that_says_so(self):
        code, body = self._by(UNKNOWN_FSID)
        self.assertEqual(code, 404)
        self.assertEqual(body.get("ok"), False, body)
        self.assertIn("no live session has transcript " + UNKNOWN_FSID, body.get("error", ""))

    def test_a_transcript_two_live_sessions_claim_is_refused_not_guessed(self):
        # two live sessions whose records both hold one id — a resume-fork row written from web's transcript
        # into api's ledger stands in for any such pair (a restored or hand-moved ledger) — the kernel refuses
        self.sb.append_resume_fork(self.root, API, OLD_FSID, FORK_FSID, 3)
        code, body = self._by(OLD_FSID)
        self.assertEqual(code, 409, body)
        self.assertEqual(body.get("ok"), False)
        self.assertIn("2 live sessions claim transcript " + OLD_FSID, body["error"])
        self.assertIn("refusing to guess", body["error"])
        for sid in (WEB, API):
            self.assertIn(sid, body["error"], "the claimants are named, so the operator can see which two")
        self.assertEqual(self._by(FORK_FSID)[1]["id"], API, "the fork's far end is api's alone")

    def test_a_malformed_id_is_refused_before_any_record_is_read(self):
        for bad in ("", "../" + OLD_FSID, "." + OLD_FSID, OLD_FSID + "/x", "a" * 129):
            del self.asked[:]
            code, body = self._by(bad)
            self.assertEqual(code, 400, (bad, body))
            self.assertEqual(body.get("ok"), False, (bad, body))
            self.assertEqual(self.asked, [], "no backend record is consulted for %r" % bad)
        self.assertEqual(self._get("/sessions/by-fsid")[0], 400, "no fsid at all is the same refusal")

    def test_the_route_is_token_gated_like_the_listing(self):
        self.assertEqual(self._get("/sessions/by-fsid?fsid=" + OLD_FSID, token=False)[0], 403)

    def test_a_resumed_comment_threads_prior_transcript_answers_with_its_thread_row(self):
        # the bus's listing is the ?threads=1 one, so a thread's stale id reaches this route too; a route over
        # Sessions.live alone (which hides threads) 404'd it forever, the bus fell back to the stale id, and the
        # sender gate — which reads the reg under the RESOLVED id — found none there and let the thread mail as
        # an ordinary session, past the rule that holds a thread's mail (T356). The thread's CLI was resumed on
        # OLD_TFSID after a kernel restart and the resume landed on a fresh head, NEW_TFSID: the backend's
        # resumeFork row is what records the old id. (The boundary tick's episode rows are written for the
        # discovered, tabbed sessions; a thread's /clear leaves none, so a thread's prior transcript is on record
        # here only through its reg's lastSid and its resume forks — the records this test writes.)
        _mk_thread(WEB, T_WEB, name="web-comment-1", root=self.root)
        self.sb.write_reg(self.root, T_WEB, {"sid": T_WEB, "cwd": "/tmp/notes-api", "alive": True,
                                              "threadOf": WEB, "lastSid": NEW_TFSID})
        self.sb.append_resume_fork(self.root, T_WEB, OLD_TFSID, NEW_TFSID, 2)
        code, row = self._by(OLD_TFSID)
        self.assertEqual(code, 200, row)
        self.assertEqual((row["id"], row["name"], row["lastSid"]), (T_WEB, "web-comment-1", NEW_TFSID))
        self.assertIs(row.get("thread"), True)
        self.assertEqual(row.get("parent"), WEB)
        self.assertEqual(row.get("mailOffWhy"), "thread", "the row says its mail is off, as the listing's does")
        self.assertEqual(row, next(r for r in km._thread_rows() if r["id"] == T_WEB),
                         "the same row GET /sessions?threads=1 serves — one builder")
        self.assertEqual(self._by(NEW_TFSID)[1]["id"], T_WEB, "the thread's current transcript too")
        self.assertEqual(self._by(OLD_FSID)[1]["id"], WEB, "the parent's own prior transcript is still the parent's alone")

    def test_a_comment_threads_own_clear_head_is_on_no_record_and_keeps_the_fallback(self):
        # the documented gap, pinned so nobody reads it as covered: a thread's /clear moves its reg's lastSid and
        # writes no row anywhere — the boundary tick's episode rows are for the discovered, tabbed sessions, and
        # the init flip records no resume fork on a /clear — so the pre-clear id is nobody's here (the JSON 404),
        # and the bus keeps its env-id fallback for that thread exactly as on main; recording thread heads is the
        # tick's change, not this route's. Red on main only in shape (a plain-text 404 where the route's JSON is)
        _mk_thread(WEB, T_WEB, name="web-comment-1", root=self.root)
        self.sb.write_reg(self.root, T_WEB, {"sid": T_WEB, "cwd": "/tmp/notes-api", "alive": True,
                                              "threadOf": WEB, "lastSid": NEW_TFSID})
        code, body = self._by(OLD_TFSID)
        self.assertEqual(code, 404, body)
        self.assertIs(body.get("ok"), False, body)
        self.assertEqual(self._by(NEW_TFSID)[1]["id"], T_WEB, "its current transcript still resolves by lastSid")

    def test_a_forks_seed_row_is_lineage_not_a_claim_on_its_parents_transcript(self):
        # red before the lineage rule: the fork's episodes ledger opens with the parent's fork-time transcript
        # (_seed_fork_stores' root row), so web's pre-/clear id had TWO claimants and the route refused (409) —
        # and web's postal MCP server, the process that asks, fell back to its stale id for good. The fork
        # yields: web answers for its own prior transcript, the fork for its own sid and its own /clear head
        self._fork_web()
        km.jd.append_episode(FORK, "head-f2", FORK_HEAD, 6)
        code, row = self._by(OLD_FSID)
        self.assertEqual((code, row.get("id")), (200, WEB), row)
        self.assertEqual(self._by(NEW_FSID)[1]["id"], WEB)
        self.assertEqual(self._by(WEB)[1]["id"], WEB)
        self.assertEqual(self._by(FORK)[1]["id"], FORK, "the fork's own sid is its own")
        self.assertEqual(self._by(FORK_HEAD)[1]["id"], FORK, "and so is its own /clear head")

    def test_a_fork_in_its_birth_window_yields_its_parents_current_transcript(self):
        # until the CLI's init flips it, a fork's reg carries the parent's CURRENT transcript as its lastSid, and
        # a comment thread's reg is born the same way: two rows' lastSid name one id, and the parent — whose
        # process carries it — answers, not a 409
        self._fork_web(at=NEW_FSID, born=True)
        self.assertEqual(self._by(NEW_FSID)[1]["id"], WEB)
        self.assertEqual(self._by(OLD_FSID)[1]["id"], WEB)
        _mk_thread(WEB, T_WEB, name="web-comment-1", root=self.root)
        self.sb.write_reg(self.root, T_WEB, {"sid": T_WEB, "cwd": "/tmp/notes-api", "alive": True, "threadOf": WEB,
                                              "forkOf": WEB, "forkAt": "", "lastSid": NEW_FSID})
        self.assertEqual(self._by(NEW_FSID)[1]["id"], WEB, "a comment thread being born yields the same way")

    def test_an_ended_parents_prior_transcript_is_nobodys_not_the_forks(self):
        # web ended (its reg alive:false, off the listing) while its fork lives on: the fork's seed row still
        # names web's transcript, but no live session owned it — a 404, so a stale asker keeps its fallback
        # rather than being answered with a session it never was
        self._fork_web()
        self.sb.write_reg(self.root, WEB, {"sid": WEB, "cwd": "/tmp/notes-api", "alive": False, "lastSid": NEW_FSID})
        del self.live[WEB]
        code, body = self._by(OLD_FSID)
        self.assertEqual(code, 404, body)
        self.assertEqual(self._by(FORK)[1]["id"], FORK, "the fork still answers to its own id")

    def test_an_unchanged_session_costs_no_ledger_read_on_the_next_ask(self):
        # the bus asks per command and per 30 s heartbeat from every post-/clear session, for the rest of its
        # CLI's life; the first ask parses every live session's ledgers (web's, whose records hold the id, and
        # api's, whose sid and lastSid did not match), the next asks parse none, and only a session whose
        # records CHANGED (a /clear head appended, a resume fork recorded) is read again — that one alone
        reads, restore = self._module_reads()
        try:
            self.assertEqual(self._by(OLD_FSID)[1]["id"], WEB)
            self.assertEqual(sorted(reads), sorted([WEB, API]), "the first ask reads both sessions' records")
            del reads[:]
            for _ in range(3):
                self.assertEqual(self._by(OLD_FSID)[1]["id"], WEB)
            self.assertEqual(reads, [], "unchanged records: no ledger is read again")
            self.assertEqual(self._by(UNKNOWN_FSID)[0], 404)
            self.assertEqual(reads, [], "a miss over the same records reads nothing either")
            km.jd.append_episode(WEB, "head-3", "aaaaaaaa-2222-3333-4444-555555555504", 3)
            self.assertEqual(self._by(OLD_FSID)[1]["id"], WEB)
            self.assertEqual(reads, [WEB], "web's episode log grew: web alone is re-read")
            del reads[:]
            self.sb.append_resume_fork(self.root, API, UNKNOWN_FSID, FORK_FSID, 4)
            self.assertEqual(self._by(FORK_FSID)[1]["id"], API)
            self.assertEqual(reads, [API], "api's states ledger grew: api alone is re-read")
            del reads[:]
            self.assertEqual(self._by(API)[1]["id"], API)
            self.assertEqual(reads, [], "a session whose sid or lastSid IS the id is never asked for its ledgers")
        finally:
            restore()

    def test_a_record_that_would_not_read_is_answered_but_never_latched(self):
        # a ledger that is THERE but cannot be read (a directory where states/<sid>.jsonl goes stands in for
        # EACCES/EIO/EMFILE: the stat succeeds, the read raises) must not memoize the partial set: the next ask
        # re-reads that session, while an unaffected session's memo stands
        (self.root / "states").mkdir(exist_ok=True)
        (self.root / "states" / (API + ".jsonl")).mkdir()
        reads, restore = self._module_reads()
        try:
            for _ in range(2):
                self.assertEqual(self._by(OLD_FSID)[1]["id"], WEB, "the route still answers off the readable records")
            self.assertEqual(reads.count(API), 2, "the faulted session is re-read on the next ask, not latched")
            self.assertEqual(reads.count(WEB), 1, "the clean one is memoized")
        finally:
            restore()


if __name__ == "__main__":
    unittest.main()
