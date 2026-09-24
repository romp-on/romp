#!/usr/bin/env python3
"""tools/perf-bench.py against a SYNTHETIC state directory (invented sessions in the notes-api demo
domain, placeholder uuids, no real data): it runs end to end and emits the JSON shape, its cold
build_session row is a real cold parse, it leaves the copy byte-identical (every write the kernel
aims at it lands in the tool's shadow, and the census says so on the error path too), it finds
transcripts older than the discovery window through the backfill, --cwd-map resolves a redacted copy's
registry cwds, it refuses the live default state directory without the flag and mirrors it with the
flag, and --compare prints deltas. The sessions' directory is a real git
checkout with a fabricated GitHub origin, as every real state's is: the chat build's path-link git
queries (rev-parse, ls-files) reach the tripwire's allow list, which a plain directory never
exercised; the `remote get-url` pair the list also admits is checked in-process below. The tool is
driven as a subprocess with the env recipe a person would use plus the load_module() deprecation
promoted to an error (LOAD_MODULE_DEPRECATION_AS_ERROR), so the kernel never loads in-process here;
the tool module itself is loaded for direct checks of its fake client's frame labelling and of the
tripwire's allow rule (its import pulls in only the standard library), and the event model alone
(kernel/event_model.py, under a private name) for the checkpoint shadow, whose write door is the
event model's own (CheckpointShadow)."""
import atexit
import contextlib
import copy
import hashlib
import io
import json
import os
import pwd
import re
import shutil
from romp_load import load_source
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "tools", "perf-bench.py")

# Two effects. For the CHILD (the tool runs as a subprocess and sets its own state root before it loads
# the kernel) this points the default state root at a temp dir, so the refusal tests' XDG_STATE_HOME /
# ROMP_STATE_DIR overrides are the only "live" candidates the tool can see. It is also the ratchet's
# preamble for the two in-process loads below: the tool module, which loads no romp code at import, and
# the event model, whose STATE reads this variable at import (CheckpointShadow never writes under it). It
# replaces conftest's suite-wide floor with another temp dir for the modules collected after this one,
# which changes nothing for them. Every temp dir this module makes is removed when it is done with it
# (this one at interpreter exit): a suite that leaves its directories behind fills /tmp over time.
_XDG_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
atexit.register(shutil.rmtree, _XDG_TMP, ignore_errors=True)

from git_fixture import git, init_repo   # after the state-root preamble; it imports only the standard library

SID_WEB = "11111111-2222-3333-4444-555555555555"
SID_API = "22222222-3333-4444-5555-666666666666"
SID_TESTS = "33333333-4444-5555-6666-777777777777"
MANAGER_VARS = ("ROMP_MANAGER_PORT", "ROMP_SERVE_PORT", "ROMP_MANAGER_PID", "ROMP_SUPERVISED")
WEB_TURNS = 400       # a transcript large enough that the cold row's full assembly shows in the counters
EXPECTED_NEUTRALIZED = {
    "km._refresh_remote_prices", "km._warm_fleet_bg", "km._system_notify", "km._push_notify", "km._push_forward",
    "km._badge_push", "romp_kernel_perf_bench.subprocess", "romp_judge.subprocess", "romp_sdk_backend.subprocess",
    "romp_credentials.subprocess",   # loaded by sdk_backend; its credential helper runs as a subprocess (review find, 2026-09-08)
    "romp_gitpr.subprocess", "gp._kick (counted)",   # the PR chip: its local git reads pass the tripwire, its gh refresh stands down
    "km._atomic_write (shadowed)", "km._read_state_json (shadow overlay)", "km._order_audit_path (shadowed)",
    "em.set_checkpoint_dir (shadowed)",   # the event model's checkpoint directory provider, pointed at the shadow
    "pwd.getpwnam (counted)", "pwd.getpwuid (counted)"}
# The caches whose emptiness the cold rows' PROOF rests on: the event model's parse-layer caches (the
# per-sample assembly check reads _ASM_CACHE and the counters), the kernel's parse cache (the
# build_feed_noparse row empties it too) and the feed's per-session card memo (2026-09-18: an entry memoized
# under a warm parse re-reads the parse in place when the store misses, so a feed built over an emptied store
# but a warm memo takes no cold branch and asks for no warm; a fresh kernel has no memo, and the cold rows
# and build_feed_noparse empty it). The tool skips a name a revision lacks and reports what it did
# empty, and the per-sample assembly check is what proves a sample cold, so the rest of the tool's list is
# checked only to be drawn from that list: the first form pinned every kernel-private cache name at HEAD,
# which an unrelated kernel rename would have broken with the proof intact (review find, 2026-09-08).
EXPECTED_COLD_CACHES = {"kernel": {"_parse_cache", "_feed_memo"}, "event_model": {"_JSONL_CACHE", "_ASM_CACHE"}}
# The writes a normal run is known to aim at the copy, every one of which the tool's shadow takes: the
# import-time repo-root marker, the tab-order audit and the session order the push maintains, and the event
# model's checkpoints directory (recorded when the guard installs, whether or not a document lands). The test asks
# that these appear among the shadowed paths and that the copy itself changed by nothing (the tree hash
# below); it does not pin the shadowed set exactly, so a kernel that adds a write path reports it in the
# tool's output without failing the tool's test (review find, 2026-09-08), while one that reaches the copy
# fails the hash.
EXPECTED_SHADOWED = {"checkpoints", "order-audit.jsonl", "repo-root", "session-order.json"}
REDACTED_PREFIX = "/XXXX/XXXXXX"     # what a redaction tool leaves where a home path stood


def _tree_hash(root):
    """Every directory, file and symlink under root, keyed by relative path: a directory as "dir", a
    symlink as its target, a file as the sha256 of its bytes. Two equal maps mean a byte-identical tree
    (mtimes aside, which a read may not touch either; the mirror test checks those)."""
    out = {}
    for dp, dns, fns in os.walk(root):
        for d in dns:
            out[os.path.relpath(os.path.join(dp, d), root) + "/"] = "dir"
        for f in fns:
            p = os.path.join(dp, f)
            if os.path.islink(p):
                out[os.path.relpath(p, root)] = "link:" + os.readlink(p)
            else:
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _age_transcripts(claude, days):
    """Set every transcript's mtime `days` back: a copy taken from a machine whose sessions last wrote
    that long ago, outside discovery's 48 h window and inside the 365-day backfill."""
    t = time.time() - days * 86400
    for p in Path(claude, "projects").rglob("*.jsonl"):
        os.utime(p, (t, t))


def _redact_cwds(state, root):
    """Rewrite every registry cwd (names/ and sdk/) so the fixture's root reads as REDACTED_PREFIX, the
    way a redaction tool replaces a home path, while the projects/ directory keeps its original name."""
    for f in Path(state, "names").iterdir():
        f.write_text(f.read_text().replace(str(root), REDACTED_PREFIX))
    for f in Path(state, "sdk").glob("*.json"):
        reg = json.loads(f.read_text())
        reg["cwd"] = reg["cwd"].replace(str(root), REDACTED_PREFIX)
        f.write_text(json.dumps(reg))


def _module_constants(path, names):
    """Module-level `NAME = <string, tuple of strings and NAMEs, or a `+` chain of those tuples>`
    assignments, read from the source with ast and resolved against each other, so a kernel constant is
    pinned without loading any romp module in this process. Every NAME a wanted constant refers to is
    resolved too, whether or not it was asked for."""
    import ast
    found = {}

    def resolve(v):
        if isinstance(v, ast.Constant):
            return v.value
        if isinstance(v, ast.Name):
            return found[v.id]
        if isinstance(v, ast.Tuple):
            return tuple(resolve(e) for e in v.elts)
        if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Add):     # credentials.FLOOR_ENV_NAMES is a tuple sum
            return resolve(v.left) + resolve(v.right)
        raise AssertionError("unsupported constant form in %s: %s" % (path, ast.dump(v)))

    for node in ast.parse(open(path).read()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = node.value
            if isinstance(v, (ast.Constant, ast.Tuple, ast.BinOp)):
                try:
                    found[node.targets[0].id] = resolve(v)
                except (KeyError, AssertionError):
                    if node.targets[0].id in names:
                        raise
    found = {k: v for k, v in found.items() if k in names}
    missing = set(names) - set(found)
    assert not missing, "not found at module level in %s: %s" % (path, sorted(missing))
    return found


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _transcript(path, sid, cwd, n_turns, t0):
    """A synthetic Claude Code transcript: n_turns of prompt, one tool round, reply. One reply carries a
    `~someone/...` path token, which the chat build's path-link pass hands to os.path.expanduser."""
    n = [0]
    parent = [None]
    t = [t0]

    def rec(typ, message, **extra):
        n[0] += 1
        t[0] += 3
        u = "aaaaaaaa-0000-0000-0000-%012d" % n[0]
        r = {"type": typ, "timestamp": _iso(t[0]), "uuid": u, "parentUuid": parent[0], "sessionId": sid,
             "cwd": cwd, "version": "2.1.0", "gitBranch": "main", "message": message}
        r.update(extra)
        parent[0] = u
        return r

    with open(path, "w") as f:
        for i in range(n_turns):
            tid = "toolu_%06d" % i
            reply = "Step %d done: the suite passes." % i
            if i == 1:
                reply += " Notes are in `~someone/notes/index.md` for later."
            rows = [
                rec("user", {"role": "user", "content": "step %d: tighten the search index and rerun the suite" % i},
                    promptSource="typed"),
                rec("assistant", {"role": "assistant", "model": "claude-sonnet-4", "stop_reason": "tool_use",
                                  "content": [{"type": "text", "text": "Round %d: adjusting `search.py`." % i},
                                              {"type": "tool_use", "id": tid, "name": "Bash",
                                               "input": {"command": "uv run pytest -q tests/test_search.py"}}]}),
                rec("user", {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok\n"}]},
                    toolUseResult={"stdout": "ok"}),
                rec("assistant", {"role": "assistant", "model": "claude-sonnet-4", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": reply}]}),
            ]
            for r in rows:
                f.write(json.dumps(r) + "\n")


ORIGIN = "https://github.com/example-org/notes-api.git"   # fabricated; `remote get-url` reads config, no network


GIT_IDENT = {"user.email": "t@TESTHOST", "user.name": "t", "commit.gpgsign": "false"}   # the fixture's synthetic author


def _git_env():
    """No global or system config: a developer's commit signing or url.insteadOf must not bend the fixture."""
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def _git(*args, cwd):
    """A fixture git call that reads no global or system config (a developer's commit signing or
    url.insteadOf must not bend the fixture) and commits as a synthetic author. It runs through the shared
    runner (tests/git_fixture.py), which forbids background git work: `git commit` spawns `git maintenance
    run --auto`, which on recent git detaches from its parent and can still be writing into .git while the
    fixture's directory is removed (the CI flake "Directory not empty: '.git'", 2026-09-10)."""
    git(cwd, *args, env=_git_env(), ident=GIT_IDENT, text=False)


def build_synthetic(root, web_turns=WEB_TURNS, age_api_days=0):
    """A state directory at root/romp (named `romp` so XDG_STATE_HOME=root resolves it as the default)
    plus a Claude config dir at root/claude holding the transcripts. Returns (state, claude_dir). The
    state carries the credential-shaped files a real one has (synthetic contents: the serve token, the Web
    Push key and subscriptions, the remote kernels' tokens) and a placeholder sdkvenv, all of which the
    mirror must leave behind. The web and tests sessions' cwd is a one-commit
    checkout with a GitHub origin, so the kernel's read-only git queries run for real and the tripwire's
    allow list is exercised; the api session's cwd is a plain directory, where the kernel cannot cache
    the ls-files answer and re-runs the query on every build. `age_api_days` moves the api transcript's
    mtime that many days into the past, outside discovery's window."""
    root = Path(root)
    state = root / "romp"
    cwd = root / "notes-api"
    cwd.mkdir(parents=True)
    init_repo(cwd, "-q", "-b", "main", env=_git_env(), ident=GIT_IDENT)
    (cwd / "README.md").write_text("# notes-api\n")
    _git("add", "README.md", cwd=cwd)
    _git("commit", "-q", "-m", "seed", cwd=cwd)
    _git("remote", "add", "origin", ORIGIN, cwd=cwd)
    plain = root / "notes-api-docs"
    plain.mkdir()
    for d in ("names", "sdk", "states", "goals", "sdkvenv/bin"):
        (state / d).mkdir(parents=True)
    (state / "serve-token").write_text("synthetic-serve-token-not-real\n")
    (state / "push-vapid.json").write_text(json.dumps({"synthetic": True}))
    (state / "push-subscriptions.json").write_text(json.dumps({"synthetic-endpoint": {"auth": "not-a-secret"}}))
    (state / "remotes.json").write_text(json.dumps([{"host": "TESTHOST", "token": "synthetic-remote-token-not-real"}]))
    (state / "sdkvenv" / "bin" / "python").write_text("placeholder: not an interpreter\n")
    claude = root / "claude"
    now = int(time.time())
    for sid, name, color, alive, turns, wd in ((SID_WEB, "web", "#4a7bd0", True, web_turns, cwd),
                                               (SID_API, "api", "#d07b4a", True, 3, plain),
                                               (SID_TESTS, "tests", "#4ad07b", False, 1, cwd)):
        proj = claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(wd)))
        proj.mkdir(parents=True, exist_ok=True)
        (state / "names" / sid).write_text("%s\t%s\t%s\t#ffffff\n" % (name, wd, color))
        (state / "sdk" / (sid + ".json")).write_text(json.dumps(
            {"sid": sid, "name": name, "cwd": str(wd), "mode": "acceptEdits", "effort": "medium",
             "lastSid": "", "alive": alive}))
        with open(state / "states" / (sid + ".jsonl"), "w") as f:
            f.write(json.dumps({"t": now - 3600, "state": "waiting"}) + "\n")
            f.write(json.dumps({"t": now - 60, "state": "working" if name == "web" else "waiting"}) + "\n")
        _transcript(proj / (sid + ".jsonl"), sid, str(wd), turns, now - 3 * turns * 4 - 60)
        if sid == SID_API and age_api_days:
            old = now - age_api_days * 86400
            os.utime(proj / (sid + ".jsonl"), (old, old))
    (state / "goals" / (SID_WEB + ".json")).write_text(json.dumps(
        {"rompUuid": SID_WEB, "seq": 1, "rev": 1, "placementsV": 11,
         "nodes": {"g1": {"parentId": None, "t": now - 500, "text": "wire the notes search index"}},
         "status": {"g1": "working"}, "lastNode": "g1", "placements": {}}))
    return str(state), str(claude)


# The tool loads the kernel and its SDK backend by file path. Every child here runs with the load_module()
# deprecation promoted to an error (the message alone, so no other warning is promoted): a load through the
# deprecated call, removed in Python 3.15, fails the run, where Python's default filters would let it pass
# in silence (the warning is raised from importlib's own frame, which the default ignore covers). The filter
# is appended to the process environment's PYTHONWARNINGS, whose own filters stay (the later filter wins for
# this message); a PYTHONWARNINGS in env_extra replaces the variable, as every env_extra key does, so a test
# that passes one includes this constant.
LOAD_MODULE_DEPRECATION_AS_ERROR = "error:the load_module() method is deprecated:DeprecationWarning"


def run_tool(args, env_extra=None, timeout=300):
    env = {k: v for k, v in os.environ.items() if k not in MANAGER_VARS}
    env["PYTHONWARNINGS"] = ",".join(f for f in (env.get("PYTHONWARNINGS"), LOAD_MODULE_DEPRECATION_AS_ERROR) if f)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True, env=env, timeout=timeout)


class PerfBench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="perf-bench-test-")
        cls.state, cls.claude = build_synthetic(cls.root)
        cls.json_path = os.path.join(cls.root, "out.json")
        cls.transcript = os.path.join(cls.claude, "projects",
                                      re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.path.join(cls.root, "notes-api"))),
                                      SID_WEB + ".jsonl")
        cls.transcript_mtime = os.stat(cls.transcript).st_mtime_ns
        cls.state_before, cls.claude_before = _tree_hash(cls.state), _tree_hash(cls.claude)
        # a planted API-key variable (not a key), which the tool must drop before the import
        cls.main = run_tool(["--state", cls.state, "--claude-dir", cls.claude, "--repo", ROOT, "--iters", "2",
                             "--sessions", "2", "--profile", "--json", cls.json_path],
                            env_extra={"ANTHROPIC_API_KEY": "not-a-key"})
        cls.state_after, cls.claude_after = _tree_hash(cls.state), _tree_hash(cls.claude)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _scratch_root(self, prefix):
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def _ok(self, r):
        self.assertEqual(r.returncode, 0, "rc=%d\nstdout:\n%s\nstderr:\n%s" % (r.returncode, r.stdout[-4000:], r.stderr[-4000:]))

    def _out(self):
        self._ok(self.main)
        with open(self.json_path) as f:
            return json.load(f)

    def test_runs_and_reports_every_builder(self):
        out = self._out()
        self.assertEqual(out["schema"], 3)
        self.assertEqual(os.path.realpath(out["state"]), os.path.realpath(self.state))
        b = out["benchmarks"]
        for name in ("liveness_snapshot", "names_snapshot", "discover_cold", "discover_warm", "build_feed",
                     "build_feed_noparse", "build_timeline_bars", "build_timeline_skel", "warm_all_parses",
                     "build_session_cold:11111111", "build_session_emwarm:11111111", "build_session_warm:11111111",
                     "build_session_cold:22222222", "load_goals:11111111", "push_cold_cycle", "push_steady",
                     "push_connect:chat", "push_connect:feed", "push_connect:timeline"):
            self.assertIn(name, b, "missing benchmark %s in %s" % (name, sorted(b)))
            self.assertIsInstance(b[name]["median"], (int, float), name)
        self.assertNotIn("build_session_cold:33333333", b, "a closed reg (alive=false) is not a live session")
        self.assertEqual(b["warm_all_parses"]["sessions"], 2, "one parse per live session")
        self.assertEqual(out["liveness"]["live"], 2)
        self.assertEqual(out["liveness"]["closed_regs"], 1)
        self.assertEqual(out["liveness"]["states"], {"11111111": "working", "22222222": "waiting"},
                         "each row's state is the last states/ record, not the dormant mapping")
        self.assertEqual(out["live_transcripts"]["count"], 2)
        self.assertEqual(out["live_transcripts"]["no_transcript"], [])
        self.assertEqual(out["discover_cache_cleared"], 3, "the cold discover row empties the cache before the warm-up and each of the 2 samples")
        self.assertEqual(out["repo_head"], subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True,
                                                          check=True).stdout.strip()[:12], "the checkout's HEAD, read from the git files")
        self.assertIn("unset ANTHROPIC_API_KEY", out["env_changes"], "the planted key variable was dropped before the import")
        self.assertIn("set ROMP_MANAGER_PORT", out["env_changes"], "the manager port is set to a dead port, not left absent")
        self.assertEqual(out["thread_starts"], 0, "no builder started a thread (the parse-warming thread is a recorder)")
        self.assertGreaterEqual(out["warm_calls_suppressed"], 1, "build_feed's cold-parse branch asked for the background warm, which the recorder took")
        web = next(s for s in out["benched_sessions"] if s["sid8"] == "11111111")
        self.assertGreater(web["events"], 0, "the chat build saw the synthetic transcript's events")
        self.assertEqual(out["benched_sessions"][0]["sid8"], "11111111", "largest transcript first")
        self.assertEqual(out["goal_stores"][0]["nodes"], 1)
        self.assertEqual(out["spawn_attempts"], [], "no builder spawned a process")
        self.assertEqual(out["threads_new"], [], "no builder left a thread running")
        self.assertEqual(set(out["neutralized"]), EXPECTED_NEUTRALIZED)
        self.assertEqual(os.stat(self.transcript).st_mtime_ns, self.transcript_mtime, "transcripts are read-only")
        self.assertTrue(EXPECTED_SHADOWED <= set(out["writes"]["shadowed"]), out["writes"])
        self.assertEqual((out["writes"]["changed"], out["writes"]["new"], out["writes"]["removed"]), (0, 0, 0), out["writes"])
        self.assertEqual(out["refused_writes"], [], "every _atomic_write was aimed under the copy")
        for rel in out["writes"]["shadowed"]:
            self.assertFalse(os.path.isabs(rel) or rel.startswith(".."), "recorded relative to the copy: %s" % rel)
        self.assertEqual(out["live_transcripts"]["searched"], {"cwds": 2, "project_dirs": 2, "transcripts": 3})
        self.assertEqual(out["cwd_map"], [])
        prof = out["profiles"]
        for name in ("build_feed", "build_timeline_bars", "build_session_cold:11111111", "load_goals:11111111", "push_steady"):
            self.assertIn(name, prof)
            self.assertLessEqual(len(prof[name]["cumulative"]), 25)
            self.assertLessEqual(len(prof[name]["tottime"]), 25)
            self.assertEqual(set(prof[name]["cumulative"][0]), {"func", "file", "line", "ncalls", "tottime_ms", "cumtime_ms"})
        self.assertIn("build_feed", self.main.stdout)
        self.assertIn("top 25 by cumulative time", self.main.stdout)

    def test_cold_build_is_a_full_parse(self):
        b = self._out()["benchmarks"]
        cold, em = b["build_session_cold:11111111"], b["build_session_emwarm:11111111"]
        self.assertEqual(cold["asm"].get("full", 0), cold["n"], "every kept cold sample ran exactly one full assembly")
        self.assertEqual(cold["asm"].get("serve", 0), 0, "a single-session transcript is assembled once per cold build")
        self.assertEqual(cold["asm"].get("fold", 0), 0, "nothing grows between reads in a static fixture")
        self.assertEqual(em["asm"].get("full", 0), 0, "the em-warm row never re-parses")
        self.assertIn("git_per_build", cold)
        cc = {k: set(v) for k, v in self._out()["cold_caches"].items()}
        for layer, need in EXPECTED_COLD_CACHES.items():
            self.assertTrue(need <= cc[layer], "%s: %s emptied before each cold sample (emptied: %s)" % (layer, sorted(need), sorted(cc[layer])))
        pb = load_source("perf_bench_caches_under_test", TOOL)
        self.assertTrue(cc["kernel"] <= set(pb.COLD_KERNEL_CACHES) | {"_chat_fold", "_feed_memo"}, "every kernel cache emptied is one the tool names")
        self.assertTrue(cc["event_model"] <= {n for n, _lock in pb.COLD_EM_CACHES})

    def test_path_token_lookups_are_counted(self):
        out = self._out()
        self.assertGreaterEqual(out["nss_lookups"].get("getpwnam", 0), 1,
                                "the `~someone/...` token reached pwd.getpwnam through os.path.expanduser")

    def test_the_checkout_git_queries_pass_the_tripwire_and_are_counted(self):
        # the sessions' cwd is a checkout, so the chat build's path-link pass runs `git rev-parse` and
        # `git ls-files` there, and the PR chip's repo lookup runs the `remote get-url` pair once per cwd;
        # the tripwire admits exactly those and counts them (a refusal would have aborted the run —
        # spawn_attempts stays empty and every row is present). `rev-list`, the chip's ahead-of count, is
        # admitted too and runs on the push path rather than inside a benched builder.
        out = self._out()
        g = out["git_queries"]
        self.assertFalse(g["answered_as_failure"])
        self.assertGreaterEqual(g["calls"].get("rev-parse", 0), 1, g)
        self.assertGreaterEqual(g["calls"].get("ls-files", 0), 1, g)
        self.assertEqual(out["spawn_attempts"], [])
        self.assertIn("rev-parse=", self.main.stdout, "the report names each query it counted")
        self.assertIn("ls-files=", self.main.stdout)
        # Per build, beside the rows: the api session's cwd is a plain directory, where the kernel cannot cache
        # the ls-files answer on the index and tree mtimes, so every build of it runs the query once; the web
        # session's cwd is a checkout, whose answers the warm-up cached, so its kept samples run none.
        # The figures are the kernel's caching policy, not this tool's, so they are asserted as the relation just
        # described (at least one query per build in the plain directory, fewer in the checkout), never as exact
        # counts an unrelated kernel change would move (review find, 2026-09-08).
        b = out["benchmarks"]
        for row in ("build_session_cold:22222222", "build_session_emwarm:22222222", "build_session_warm:22222222"):
            self.assertGreaterEqual(b[row]["git_per_build"].get("ls-files", 0), 1.0, "%s: %s" % (row, b[row]["git_per_build"]))
            self.assertEqual(set(b[row]["git_per_build"]) - {"rev-parse", "ls-files", "remote get-url", "rev-list"}, set(),
                             "only admitted queries ran")
        checkout, plain = b["build_session_cold:11111111"]["git_per_build"], b["build_session_cold:22222222"]["git_per_build"]
        self.assertLess(sum(checkout.values()), sum(plain.values()), "the checkout's answers are cached across the kept samples: %s vs %s" % (checkout, plain))

    def test_the_fixture_repos_forbid_background_git_work(self):
        # the kernel's own git queries (rev-parse, ls-files, through the tool's tripwire, not this file's
        # runner) run against the sessions' checkout, so the no-background keys sit in that repo's own config
        repo = os.path.join(self.root, "notes-api")
        self.assertEqual(git(repo, "config", "--local", "--get", "maintenance.auto").stdout.strip(), "false")

    def test_push_rows_report_bytes_and_rebuild_flags(self):
        b = self._out()["benchmarks"]
        chat = b["push_cold_cycle"]["bytes"]["chat"]["slots"]
        self.assertIn("chat:11111111", chat, "the fake chat client received the active tab's session frame")
        self.assertGreater(chat["chat:11111111"], 0)
        self.assertIn("feed", b["push_cold_cycle"]["bytes"]["feed"]["slots"])
        self.assertIn("chat:11111111", b["push_connect:chat"]["bytes"]["slots"], "a fresh chat client gets the full session")
        self.assertIn("feed", b["push_connect:feed"]["bytes"]["slots"])
        self.assertIn("timeline", b["push_connect:timeline"]["bytes"]["slots"])
        # a connect sample is a FRESH client with empty dedup state, so it receives what the cold cycle's fresh
        # client did: the same frame count per app, and for the chat the same bytes (the session frame does not
        # depend on the parse caches; the cold cycle's feed and timeline were built before any parse, so their
        # sizes differ from the warm ones). A client reused across samples accumulates frames instead.
        for app in ("chat", "feed", "timeline"):
            self.assertEqual(b["push_connect:" + app]["bytes"]["frames"], b["push_cold_cycle"]["bytes"][app]["frames"], app)
        self.assertEqual(b["push_connect:chat"]["bytes"], b["push_cold_cycle"]["bytes"]["chat"])
        self.assertGreaterEqual(b["push_cold_cycle"]["asm"].get("full", 0), 2, "the cold cycle parsed both live transcripts inside itself")
        st = b["push_steady"]
        self.assertGreaterEqual(len(st["samples"]), st["n"])
        self.assertEqual(st["n"], 2, "the loop ran until two quiet samples existed")
        for s in st["samples"]:
            self.assertEqual(set(s), {"ms", "rebuilt_feed", "rebuilt_timeline", "bytes"})
        quiet = [s for s in st["samples"] if not (s["rebuilt_feed"] or s["rebuilt_timeline"])]
        self.assertEqual(len(quiet), st["n"])
        self.assertEqual(st["rebuild_samples"], len(st["samples"]) - len(quiet))
        for name, row in b.items():
            if name.startswith("push") and row.get("bytes"):
                self.assertNotIn("unknown", json.dumps(row["bytes"]), "%s: every frame the fake clients got was labelled" % name)

    def test_fake_client_labels_direct_delta_frames_by_their_slot(self):
        # the static fixture never changes between pushes, so no delta frame reaches a fake client in the
        # run above; this drives the client's send() directly with the three frame shapes it can see
        pb = load_source("perf_bench_under_test", TOOL)
        c = pb.fake_client(SimpleNamespace(), "timeline")      # no _perf_slot: a keyed label is str(key)
        delta = '{"type": "delta", "slot": "bars", "base": 3, "rev": 4, "coll": {}}'
        c["send"](delta)                                        # _send_slot_delta: send() directly, no curSlot
        c["curSlot"] = ("timeline",)                            # what _client_send sets around its call
        full = '{"type": "timeline", "sessions": []}'
        c["send"](full)
        del c["curSlot"]
        other = '{"type": "warn", "text": "not a slot frame"}'
        c["send"](other)
        self.assertEqual(c["bytes"], {"bars-delta": len(delta), "('timeline',)": len(full), "warn": len(other)})
        self.assertEqual(c["frames"], 3)

    def test_the_copy_is_byte_identical_after_a_run(self):
        # the paths the kernel aims at the copy (EXPECTED_SHADOWED) were redirected to the tool's shadow (the
        # checkpoints directory by its provider, whether or not a document follows), so the copy has the same
        # directories, files and bytes it started with; without the shadow the run creates repo-root,
        # session-order.json and order-audit.jsonl inside it
        self._ok(self.main)
        self.assertEqual(self.state_after, self.state_before, "the state copy is only read")
        self.assertEqual(self.claude_after, self.claude_before, "the transcripts are only read")
        self.assertIn("writes into the state copy: 0 changed, 0 new, 0 removed", self.main.stdout)
        shadowed_line = next(l for l in self.main.stdout.splitlines() if l.startswith("writes redirected to the private dir, not the copy (paths, landed or not): "))
        for name in EXPECTED_SHADOWED:
            self.assertIn(name, shadowed_line)

    def test_the_census_runs_on_the_error_path_and_names_what_was_searched(self):
        # a claude dir with no projects/ at all: discovery finds nothing, the window and the backfill both
        # count zero, and the run stops with the counts it worked from. The census still runs and prints,
        # the JSON carries it beside the error, and the copy is untouched (the kernel WAS imported, so
        # without the shadow repo-root would be in it)
        root = self._scratch_root("perf-bench-nofind-")
        state, claude = build_synthetic(root, web_turns=3)
        empty_claude = os.path.join(root, "claude-empty")
        os.makedirs(empty_claude)
        before = _tree_hash(state)
        out_json = os.path.join(root, "out.json")
        r = run_tool(["--state", state, "--claude-dir", empty_claude, "--repo", ROOT, "--iters", "1", "--json", out_json])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        # the two windows are the kernel's constants (jd.WINDOW, jd.DEATH_BACKFILL_WINDOW), so their values
        # are not pinned here, only that the error names them with the counts
        self.assertRegex(r.stderr, r"discovery found no transcript for any of the 2 live sessions: 0 in the \d+ h window, "
                                   r"0 more in the \d+-day backfill; their 2 registry cwd\(s\) resolve to 0 existing project "
                                   r"directories holding 0 transcript\(s\)")
        self.assertIn("--cwd-map", r.stderr, "the error points at the redacted-copy remedy")
        self.assertNotIn(root, r.stderr.split("perf-bench: discovery")[1], "the error names counts, not paths")
        self.assertIn("writes into the state copy: 0 changed, 0 new, 0 removed", r.stdout)
        self.assertIn("writes redirected to the private dir, not the copy (paths, landed or not): checkpoints, repo-root", r.stdout,
                      "the import-time marker, which landed, and the checkpoints directory the guard pointed the provider "
                      "at before the run stopped, which received no document")
        self.assertEqual(_tree_hash(state), before, "an error run leaves the copy byte-identical too")
        with open(out_json) as f:
            out = json.load(f)
        self.assertIn("no transcript for any", out["error"])
        self.assertEqual((out["writes"]["changed"], out["writes"]["new"], out["writes"]["removed"]), (0, 0, 0))
        self.assertEqual(out["writes"]["shadowed"], ["checkpoints", "repo-root"])
        self.assertIn("(partial: the run stopped on an error)", r.stdout)

    def test_transcripts_older_than_the_window_are_found_by_the_backfill(self):
        # every transcript is 3 days old: outside discovery's 48 h window, inside the 365-day backfill. A
        # no-transcript error raised before the backfill runs would stop this run; raised after it, the
        # backfill finds both
        root = self._scratch_root("perf-bench-old-")
        state, claude = build_synthetic(root, web_turns=3)
        _age_transcripts(claude, days=3)
        out_json = os.path.join(root, "out.json")
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1", "--sessions", "2",
                      "--clients", "", "--json", out_json])
        self._ok(r)
        with open(out_json) as f:
            out = json.load(f)
        lt = out["live_transcripts"]
        self.assertEqual((lt["in_window"], lt["backfilled"], lt["count"], lt["no_transcript"]), (0, 2, 2, []))
        self.assertIn("build_session_cold:11111111", out["benchmarks"])
        self.assertIn("build_session_cold:22222222", out["benchmarks"])
        self.assertIn("2 live transcripts (0 in the discovery window, 2 backfilled, 0 without one)", r.stdout)

    def test_cwd_map_resolves_a_redacted_copy(self):
        # the registry cwds read /XXXX/XXXXXX/notes-api while the project directory is still named after
        # the real path: without a map discovery resolves the cwd to a directory that does not exist and
        # the run stops; with --cwd-map the same run finds both transcripts and reports the rule's hits
        root = self._scratch_root("perf-bench-redacted-")
        state, claude = build_synthetic(root, web_turns=3)
        _redact_cwds(state, root)
        before = _tree_hash(state)
        out_json = os.path.join(root, "out.json")
        base = ["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1", "--clients", "", "--json", out_json]
        maps = ["--cwd-map", "/nowhere/else=/nowhere", "--cwd-map", REDACTED_PREFIX + "=" + root]
        r = run_tool(base + ["--sessions", "2"])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("their 2 registry cwd(s) resolve to 0 existing project directories", r.stderr)
        r = run_tool(base + ["--sessions", "2"] + maps)
        self._ok(r)
        with open(out_json) as f:
            out = json.load(f)
        self.assertEqual((out["live_transcripts"]["count"], out["live_transcripts"]["no_transcript"]), (2, []))
        self.assertEqual(out["live_transcripts"]["searched"], {"cwds": 2, "project_dirs": 2, "transcripts": 3})
        self.assertEqual([(m["from"], m["to"]) for m in out["cwd_map"]], [("/nowhere/else", "/nowhere"), (REDACTED_PREFIX, root)])
        self.assertEqual(out["cwd_map"][0]["hits"], 0)
        self.assertGreater(out["cwd_map"][1]["hits"], 0, "the matching rule counted its hits")
        self.assertIn("build_session_cold:11111111", out["benchmarks"])
        self.assertIn("cwd-map hits: rule 1=0, rule 2=%d" % out["cwd_map"][1]["hits"], r.stdout)
        self.assertIn("jd._proj_dir (cwd-map)", out["neutralized"], "the report says the derivation is wrapped")
        self.assertEqual(_tree_hash(state), before, "the map rewrites nothing in the copy")
        self.assertEqual(out["spawn_attempts"], [], "the git queries against the redacted cwd failed quietly, no tripwire")
        # the count is read when the run ends, so the builders' transcript-path derivations are in it: one
        # more benched session is more hits, where a count taken at discovery (which does not depend on
        # --sessions) would be the same for both runs
        r = run_tool(base + ["--sessions", "1"] + maps)
        self._ok(r)
        with open(out_json) as f:
            hits_one = json.load(f)["cwd_map"][1]["hits"]
        self.assertGreater(hits_one, 0)
        self.assertGreater(out["cwd_map"][1]["hits"], hits_one, "the hits cover the whole run, not the discovery stage alone")

    def test_an_exception_out_of_the_candidate_kernel_still_prints_the_census(self):
        # a --repo whose kernel raises at import (a broken candidate checkout) is not a BenchError. Left to
        # propagate past main(), it would take the census run() had taken with it (no census line, no JSON,
        # only the traceback). The census prints, the JSON carries the error beside it, the copy is
        # byte-identical, and the traceback still follows
        root = self._scratch_root("perf-bench-broken-")
        state, claude = build_synthetic(root, web_turns=3)
        repo = os.path.join(root, "broken-repo")
        os.makedirs(os.path.join(repo, "kernel"))
        Path(repo, "kernel", "kernel.py").write_text("raise RuntimeError('synthetic import failure')\n")
        before = _tree_hash(state)
        out_json = os.path.join(root, "out.json")
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", repo, "--iters", "1", "--json", out_json])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("writes into the state copy: 0 changed, 0 new, 0 removed", r.stdout)
        self.assertIn("writes redirected to the private dir, not the copy (paths, landed or not): none", r.stdout)
        self.assertIn("(partial: the run stopped on an error)", r.stdout)
        self.assertIn("perf-bench: RuntimeError: synthetic import failure", r.stderr)
        self.assertIn("Traceback", r.stderr, "the exception still leaves main() with its traceback")
        self.assertEqual(_tree_hash(state), before)
        with open(out_json) as f:
            out = json.load(f)
        self.assertEqual(out["error"], "RuntimeError: synthetic import failure")
        self.assertEqual((out["writes"]["changed"], out["writes"]["new"], out["writes"]["removed"]), (0, 0, 0))
        self.assertEqual(out["writes"]["shadowed"], [])

    def test_fingerprint_sees_directories_and_symlink_targets(self):
        # the census fingerprints directories and link targets too: a directory the kernel creates (its
        # mkdir sites on STATE subdirectories) or a link it retargets is a write into the copy, and one
        # that records files only would report it as 0 changed, 0 new, 0 removed
        pb = load_source("perf_bench_fingerprint_under_test", TOOL)
        root = self._scratch_root("perf-bench-fp-")
        os.makedirs(os.path.join(root, "sdk"))
        Path(root, "sdk", "a.json").write_text("{}")
        os.symlink("sdk", os.path.join(root, "alias"))
        before = pb.fingerprint(root)
        self.assertEqual(before["sdk/"], "dir")
        self.assertEqual(before["alias/"], ("link", "sdk"))
        os.makedirs(os.path.join(root, "goals"))
        os.remove(os.path.join(root, "alias"))
        os.symlink("goals", os.path.join(root, "alias"))
        diff = pb.fingerprint_diff(before, pb.fingerprint(root))
        self.assertEqual((diff["changed"], diff["new"], diff["removed"]), (1, 1, 0))
        self.assertEqual(diff["sample"], ["~ alias/", "+ goals/"])

    def test_install_cwd_map_wraps_nothing_without_rules(self):
        # the default run pays no extra frame per _proj_dir call; with a rule the wrapper counts its hits
        pb = load_source("perf_bench_cwdmap_under_test", TOOL)
        real = lambda d: "proj:" + str(d)
        jd = SimpleNamespace(_proj_dir=real)
        self.assertEqual(pb.install_cwd_map(jd, []), [])
        self.assertIs(jd._proj_dir, real)
        hits = pb.install_cwd_map(jd, [("/XXXX/XXXXXX", "/repo")])
        self.assertIsNot(jd._proj_dir, real)
        self.assertEqual(jd._proj_dir("/XXXX/XXXXXX/notes-api"), "proj:/repo/notes-api")
        self.assertEqual(jd._proj_dir("/XXXX/XXXXXXX/other"), "proj:/XXXX/XXXXXXX/other", "whole-component match only")
        self.assertEqual(hits, [1])

    def test_cwd_map_refuses_a_malformed_rule(self):
        r = run_tool(["--state", self.state, "--claude-dir", self.claude, "--repo", ROOT, "--cwd-map", "no-equals-sign"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("expected FROM=TO", r.stderr)
        r = run_tool(["--state", self.state, "--claude-dir", self.claude, "--repo", ROOT, "--cwd-map", "=/somewhere"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("FROM is empty", r.stderr)

    def test_refuses_the_live_default_dir_without_the_flag(self):
        root = self._scratch_root("perf-bench-live-")
        state, claude = build_synthetic(root, web_turns=3)
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1"],
                     env_extra={"XDG_STATE_HOME": root})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--i-know-this-is-live", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(state, "repo-root")), "the kernel was never imported against it")
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1"],
                     env_extra={"ROMP_STATE_DIR": state})
        self.assertEqual(r.returncode, 2, "ROMP_STATE_DIR names the live dir too")

    def test_live_flag_benches_a_mirror_and_leaves_the_original_alone(self):
        root = self._scratch_root("perf-bench-live-")
        state, claude = build_synthetic(root, web_turns=3)
        before = {p: os.stat(p).st_mtime_ns for p in Path(state).rglob("*") if p.is_file()}
        out_json = os.path.join(root, "out.json")
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1", "--sessions", "1",
                      "--clients", "", "--i-know-this-is-live", "--keep-mirror", "--json", out_json],
                     env_extra={"XDG_STATE_HOME": root})
        self._ok(r)
        self.assertIn("mirrored", r.stderr)
        self.assertIn("mirror kept at", r.stderr)
        after = {p: os.stat(p).st_mtime_ns for p in Path(state).rglob("*") if p.is_file()}
        self.assertEqual(before, after, "the live directory is only read")
        with open(out_json) as f:
            out = json.load(f)
        self.assertEqual(os.path.realpath(out["state_mirror_of"]), os.path.realpath(state))
        mirror = out["state"]
        self.assertNotEqual(os.path.realpath(mirror), os.path.realpath(state))
        self.assertTrue(os.path.isdir(os.path.join(mirror, "sdk")), "the mirror is a state directory")
        for name in ("serve-token", "push-vapid.json", "push-subscriptions.json", "remotes.json", "sdkvenv"):
            self.assertFalse(os.path.exists(os.path.join(mirror, name)), "%s is not copied into the mirror" % name)
        self.assertEqual(set(self.pb_mirror_ignore()), {"serve-token", "push-vapid.json", "push-subscriptions.json", "remotes.json", "sdkvenv"})
        self.assertIn("build_feed", out["benchmarks"])
        self.assertNotIn("push_steady", out["benchmarks"], "--clients '' skips the push benchmarks")
        self.assertEqual(len([k for k in out["benchmarks"] if k.startswith("build_session_cold:")]), 1,
                         "--sessions 1 benches one transcript (the two live ones are the same size here)")
        self.assertEqual([k for k in out["benchmarks"] if k.startswith("load_goals:")], ["load_goals:11111111"])
        mirror_root = os.path.dirname(os.path.realpath(mirror))      # the tool's mkdtemp dir; the mirror is its romp/
        self.assertTrue(os.path.basename(mirror_root).startswith("romp-perf-live-mirror-"), mirror_root)
        shutil.rmtree(mirror_root, ignore_errors=True)                # kept for the assertions above, not beyond
        # without --keep-mirror the mirror is removed
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1", "--sessions", "1",
                      "--clients", "", "--i-know-this-is-live", "--json", out_json], env_extra={"XDG_STATE_HOME": root})
        self._ok(r)
        self.assertIn("mirror removed", r.stderr)
        with open(out_json) as f:
            self.assertFalse(os.path.exists(json.load(f)["state"]))

    def test_compare_prints_per_benchmark_deltas(self):
        out = self._out()
        b = copy.deepcopy(out)
        feed = out["benchmarks"]["build_feed"]["median"]
        b["benchmarks"]["build_feed"]["median"] = feed * 2
        del b["benchmarks"]["discover_warm"]
        b["benchmarks"]["invented_row"] = {"n": 1, "median": 1.0}
        b["iters"] = 9
        b_path = os.path.join(self.root, "b.json")
        with open(b_path, "w") as f:
            json.dump(b, f)
        r = run_tool(["--compare", self.json_path, b_path])
        self._ok(r)
        lines = r.stdout.splitlines()
        feed_line = next(l for l in lines if l.startswith("build_feed "))
        self.assertEqual(feed_line.split(), ["build_feed", "%.2f" % feed, "%.2f" % (feed * 2), "%+.2f" % feed, "+100.0%"])
        self.assertIn("only in A: discover_warm", r.stdout)
        self.assertIn("only in B: invented_row", r.stdout)
        self.assertIn("MISMATCH", r.stdout, "a differing iters count is flagged before the table")
        self.assertIn("push_cold_cycle bytes chat", r.stdout)
        r = run_tool(["--compare", self.json_path, self.json_path])
        self._ok(r)
        self.assertNotIn("MISMATCH", r.stdout)
        # a run that stopped on a guard is not a world to diff against: its `error` is flagged first
        c = copy.deepcopy(out)
        c["error"] = "a guard tripped"
        c_path = os.path.join(self.root, "c.json")
        with open(c_path, "w") as f:
            json.dump(c, f)
        r = run_tool(["--compare", self.json_path, c_path])
        self._ok(r)
        self.assertRegex(r.stdout, r"run error\s+A=None\s+B=a guard tripped\s+MISMATCH")

    def test_requires_a_state_dir(self):
        r = run_tool([])
        self.assertEqual(r.returncode, 2)
        r = run_tool(["--state", os.path.join(self.root, "not-a-state-dir")])
        self.assertEqual(r.returncode, 2)

    def test_a_transcript_outside_the_discovery_window_is_backfilled(self):
        # the api transcript's mtime is four days old, outside discovery's 48 h window, while its registry
        # entry is alive: the tool resolves it through the long backfill window, as _alive_sessions does for
        # the builders, so the pick list and the builders see one world and the session gets its cold row
        root = self._scratch_root("perf-bench-backfill-")
        state, claude = build_synthetic(root, web_turns=3, age_api_days=4)
        out_json = os.path.join(root, "out.json")
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", ROOT, "--iters", "1", "--sessions", "2",
                      "--clients", "", "--json", out_json])
        self._ok(r)
        with open(out_json) as f:
            out = json.load(f)
        self.assertEqual(out["live_transcripts"]["count"], 2)
        self.assertEqual(out["live_transcripts"]["in_window"], 1)
        self.assertEqual(out["live_transcripts"]["backfilled"], 1)
        self.assertEqual(out["live_transcripts"]["no_transcript"], [])
        self.assertIn("build_session_cold:22222222", out["benchmarks"])
        self.assertIn("1 backfilled", r.stdout)

    def test_a_kernel_lacking_a_builder_is_refused(self):
        # --repo selects the kernel; one without the symbols the harness drives is refused with a message
        # naming the first missing one, and the partial JSON records which checkout was tried
        scratch = self._scratch_root("perf-bench-norepo-")
        os.makedirs(os.path.join(scratch, "kernel"))
        with open(os.path.join(scratch, "kernel", "kernel.py"), "w") as f:
            f.write("x = 1\n")
        open(os.path.join(scratch, "kernel", "sdk_backend.py"), "w").close()
        out_json = os.path.join(scratch, "out.json")
        r = run_tool(["--state", self.state, "--claude-dir", self.claude, "--repo", scratch, "--iters", "1", "--json", out_json])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("this kernel lacks _live_scope", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        with open(out_json) as f:
            out = json.load(f)
        self.assertEqual(out["repo"], os.path.realpath(scratch))
        self.assertNotIn("benchmarks", out)
        self.assertEqual(out["error"], "this kernel lacks _live_scope; the harness does not know how to drive it")

    def test_the_tool_loads_the_kernel_clean_under_the_load_module_deprecation_as_an_error(self):
        # the kernel and its SDK backend are loaded by file path; with the load_module() deprecation promoted
        # to an error in the child, both loads run clean and the run ends on the tool's own refusal of the
        # planted kernel, not on the warning (the filter names the message, so nothing else is promoted)
        scratch = self._scratch_root("perf-bench-loader-")
        os.makedirs(os.path.join(scratch, "kernel"))
        with open(os.path.join(scratch, "kernel", "kernel.py"), "w") as f:
            f.write("x = 1\n")
        open(os.path.join(scratch, "kernel", "sdk_backend.py"), "w").close()
        r = run_tool(["--state", self.state, "--claude-dir", self.claude, "--repo", scratch, "--iters", "1"],
                     env_extra={"PYTHONWARNINGS": LOAD_MODULE_DEPRECATION_AS_ERROR})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("this kernel lacks _live_scope", r.stderr)
        self.assertNotIn("DeprecationWarning", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_an_sdk_backend_the_kernel_already_loaded_is_not_executed_again(self):
        # the kernel loads its SDK backend under the fixed name romp_sdk_backend, and load_source executes a
        # name already in sys.modules again, into the same module object; the tool reuses the module the
        # kernel registered rather than loading the file a second time. The planted kernel registers its
        # backend at import, and the backend appends a line to a marker on every execution.
        scratch = self._scratch_root("perf-bench-backend-once-")
        marker = os.path.join(scratch, "executions")
        os.makedirs(os.path.join(scratch, "kernel"))
        with open(os.path.join(scratch, "kernel", "kernel.py"), "w") as f:
            f.write("import importlib.util, os, sys\n"
                    "_spec = importlib.util.spec_from_file_location(\n"
                    "    'romp_sdk_backend', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sdk_backend.py'))\n"
                    "_mod = importlib.util.module_from_spec(_spec)\n"
                    "sys.modules['romp_sdk_backend'] = _mod\n"
                    "_spec.loader.exec_module(_mod)\n")
        with open(os.path.join(scratch, "kernel", "sdk_backend.py"), "w") as f:
            f.write("with open(%r, 'a') as f:\n    f.write('executed\\n')\n" % marker)
        r = run_tool(["--state", self.state, "--claude-dir", self.claude, "--repo", scratch, "--iters", "1"])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("this kernel lacks _live_scope", r.stderr)   # both loads ran; the planted kernel was refused after them
        self.assertNotIn("Traceback", r.stderr)
        with open(marker) as f:
            self.assertEqual(f.read(), "executed\n")

    def pb_mirror_ignore(self):
        return load_source("perf_bench_mirror_under_test", TOOL).MIRROR_IGNORE

    def _planted_kernel(self, plant):
        """A copy of this checkout's kernel/ with one line planted inside _push's try block, right after its
        tab-order audit: the one place on the benched paths where the kernel catches every exception of its
        own build and carries on."""
        # The scratch path spells the tool's name on purpose: the tripwire's frame filter must exclude the tool's
        # own file, not every path that contains "perf-bench" (its first form did, and the spawn record's `via`
        # came out empty for a kernel living under such a directory).
        scratch = self._scratch_root("perf-bench-planted-")
        shutil.copytree(os.path.join(ROOT, "kernel"), os.path.join(scratch, "kernel"), ignore=shutil.ignore_patterns("__pycache__"))
        kp = os.path.join(scratch, "kernel", "kernel.py")
        with open(kp) as f:
            src = f.read()
        anchor = '        _order_audit("push", _last_tab_order, tab_order, only_permuted=True)\n'
        self.assertEqual(src.count(anchor), 1, "the plant's anchor line inside _push's try block")
        with open(kp, "w") as f:
            f.write(src.replace(anchor, anchor + "        " + plant + "\n"))
        return scratch

    def test_a_guard_tripped_inside_push_fails_the_run_naming_the_guard(self):
        # The tripwire raises inside _push, which catches every exception of its build (writes `push build:` to
        # stderr and returns), so the first form timed the aborted cycle as a number and exited 0 with no error
        # in the JSON and the refusal visible only as a count (review find, 2026-09-08). Now the run ends the way
        # any other tripped guard ends it: rows so far kept, JSON marked partial, exit 1, the guard named.
        root = self._scratch_root("perf-bench-tripped-")
        state, claude = build_synthetic(root, web_turns=3)
        out_json = os.path.join(root, "out.json")
        scratch = self._planted_kernel('subprocess.run(["date"], capture_output=True)')
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", scratch, "--iters", "1", "--sessions", "1", "--json", out_json])
        self.assertEqual(r.returncode, 1, "rc=%d\nstdout:\n%s\nstderr:\n%s" % (r.returncode, r.stdout[-3000:], r.stderr[-3000:]))
        self.assertIn("perf-bench: a guard tripped inside a call the kernel catches", r.stderr)
        self.assertIn("subprocess tripwire: ", r.stderr)
        self.assertIn("refused spawn", r.stderr)
        self.assertIn("push build:", r.stderr, "the kernel's own report of the exception it swallowed")
        self.assertIn("(partial: the run stopped on an error)", r.stdout)
        with open(out_json) as f:
            out = json.load(f)
        self.assertIn("subprocess tripwire", out["error"])
        self.assertTrue(out["spawn_attempts"], "the refused spawns are listed")
        for attempt in out["spawn_attempts"]:
            self.assertIn("['date']", attempt)
            self.assertIn("_push", attempt)
        self.assertEqual(out["refused_writes"], [])
        self.assertIn("push_cold_cycle", out["benchmarks"], "the rows measured before the check are kept")
        self.assertEqual(out["benchmarks"]["push_cold_cycle"]["bytes"]["feed"]["frames"], 0, "and show the aborted cycle for what it was")

    def test_an_out_of_copy_write_inside_push_fails_the_run_and_is_recorded(self):
        # The write guard raised before recording anything, so a refused write inside _push left no trace in the
        # JSON or the text report (only the kernel's stderr traceback) while the tool exited 0. The refusal is now
        # on record before the raise, and the run fails on it (review find, 2026-09-08).
        root = self._scratch_root("perf-bench-tripped-write-")
        state, claude = build_synthetic(root, web_turns=3)
        elsewhere = os.path.join(os.path.realpath(root), "elsewhere", "y.json")
        out_json = os.path.join(root, "out.json")
        scratch = self._planted_kernel('_atomic_write(%r, "{}")' % elsewhere)
        r = run_tool(["--state", state, "--claude-dir", claude, "--repo", scratch, "--iters", "1", "--sessions", "1", "--json", out_json])
        self.assertEqual(r.returncode, 1, "rc=%d\nstdout:\n%s\nstderr:\n%s" % (r.returncode, r.stdout[-3000:], r.stderr[-3000:]))
        self.assertIn("_atomic_write guard: ", r.stderr)
        self.assertIn(elsewhere, r.stderr, "the refused path is named")
        self.assertIn("refused writes: ", r.stdout, "the text report counts them")
        with open(out_json) as f:
            out = json.load(f)
        self.assertIn("_atomic_write guard", out["error"])
        self.assertTrue(out["refused_writes"])
        self.assertEqual(set(out["refused_writes"]), {elsewhere})
        self.assertEqual(out["spawn_attempts"], [])
        self.assertFalse(os.path.exists(elsewhere), "the refused write never happened")


class Tripwire(unittest.TestCase):
    """The tripwire's allow rule, in-process: exactly the kernel's read-only git queries pass —
    `rev-parse`, `ls-files`, and the pair `remote get-url` — and everything else raises. `git remote`
    is not read-only as a whole (add / set-url / remove rewrite config), so only the get-url pair is
    admitted, by both words."""

    @classmethod
    def setUpClass(cls):
        cls.pb = load_source("perf_bench_tripwire_under_test", TOOL)
        cls.root = tempfile.mkdtemp(prefix="perf-bench-tripwire-")
        cls.repo = os.path.join(cls.root, "notes-api")
        os.makedirs(cls.repo)
        init_repo(cls.repo, "-q", "-b", "main", env=_git_env(), ident=GIT_IDENT)
        _git("remote", "add", "origin", ORIGIN, cwd=cls.repo)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _tw(self, no_git=False):
        log = []
        return self.pb.SubprocessTripwire(subprocess, log, no_git=no_git), log

    def test_the_allow_rule_names_exactly_three_queries(self):
        q = self.pb.SubprocessTripwire._git_query
        self.assertEqual(q(["git", "-C", "/x", "rev-parse", "--show-toplevel"]), "rev-parse")
        self.assertEqual(q(["git", "ls-files", "-co", "--exclude-standard"]), "ls-files")
        self.assertEqual(q(["git", "-C", "/x", "remote", "get-url", "origin"]), "remote get-url")
        for argv in (["git", "-C", "/x", "remote", "set-url", "origin", "u"],
                     ["git", "-C", "/x", "remote", "add", "origin", "u"],
                     ["git", "-C", "/x", "remote", "remove", "origin"],
                     ["git", "-C", "/x", "remote"],
                     ["git", "-C", "/x", "fetch", "origin"],
                     ["git", "-C", "/x", "push"],
                     ["git"], ["gh", "pr", "view", "1"], "git rev-parse HEAD", None):
            self.assertIsNone(q(argv), argv)

    def test_the_repo_query_runs_and_is_counted_by_its_pair(self):
        tw, log = self._tw()
        r = tw.run(["git", "-C", self.repo, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), ORIGIN)
        self.assertEqual(tw.git_calls, {"remote get-url": 1})
        self.assertEqual(log, [])

    def test_a_writing_remote_form_trips(self):
        tw, log = self._tw()
        with self.assertRaises(self.pb.BenchError):
            tw.run(["git", "-C", self.repo, "remote", "set-url", "origin", "https://github.com/other-org/x.git"],
                   capture_output=True, text=True)
        self.assertEqual(len(log), 1)
        self.assertIn("remote', 'set-url'", log[0])
        self.assertEqual(tw.git_calls, {})
        with self.assertRaises(self.pb.BenchError):
            tw.run(["git", "-C", self.repo, "remote"], capture_output=True, text=True)
        r = git(self.repo, "remote", "get-url", "origin", check=False)
        self.assertEqual(r.stdout.strip(), ORIGIN, "the refused set-url never ran")

    def test_no_git_answers_the_repo_query_as_a_failure(self):
        tw, log = self._tw(no_git=True)
        r = tw.run(["git", "-C", self.repo, "remote", "get-url", "origin"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertEqual(tw.git_calls, {"remote get-url": 1}, "counted even when answered as a failure")
        self.assertEqual(log, [])


class Recorders(unittest.TestCase):
    """install_guards' recorders, in-process, called the way kernel/kernel.py calls the functions they
    replace. The kernel passes _push_notify keywords the recorder never reads (kind=, card_id=, quiet=,
    host=), and a recorder that refused them would not fail the run: _push catches every exception in
    its build, writes `push build: <traceback>` to stderr and returns, so that cycle was timed with its
    frames dropped and the notification count came up short while the tool exited 0. A safety target
    the kernel lacks is still an error, since the real function would otherwise stay in place."""

    def setUp(self):
        self.pb = load_source("perf_bench_recorders_under_test", TOOL)
        saved = pwd.getpwnam, pwd.getpwuid                 # install_guards counts these process-wide
        self.addCleanup(lambda: (setattr(pwd, "getpwnam", saved[0]), setattr(pwd, "getpwuid", saved[1])))
        saved_start = threading.Thread.start               # and wraps this
        self.addCleanup(setattr, threading.Thread, "start", saved_start)
        self.state = tempfile.mkdtemp(prefix="perf-bench-recorders-")
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        self.shadow_root = os.path.realpath(tempfile.mkdtemp(prefix="perf-bench-recorders-shadow-"))   # target() answers a resolved path (a symlinked temp root on macOS)
        self.addCleanup(shutil.rmtree, self.shadow_root, ignore_errors=True)
        self.writes = []

    def _fake_atomic_write(self, path, text, mode=None):
        """The fake kernel's write door: records the call and publishes the file where it was told to, so the
        shadow overlay's reads have a file to find."""
        self.writes.append((path, text))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)

    def _kernel(self):
        self.ckpt_providers = []                            # what the fake event model's set_checkpoint_dir was handed
        return SimpleNamespace(jd=SimpleNamespace(STATE=Path(self.state)),
                               em=SimpleNamespace(set_checkpoint_dir=self.ckpt_providers.append),
                               _atomic_write=self._fake_atomic_write,
                               _read_state_json=lambda path, st=None, expect=None: None,
                               _order_audit_path=lambda: Path(self.state) / "order-audit.jsonl",
                               _refresh_remote_prices=None, _warm_fleet_bg=None, _system_notify=None,
                               _push_notify=None, _push_forward=None, _badge_push=None,
                               gp=SimpleNamespace(_kick=None))

    def _guards(self, km):
        """install_guards the way run() calls it: a recorder and a shadow made before the kernel loads, so
        the census can read them whichever way the run ends. Returns (names, rec, shadow)."""
        rec = self.pb.new_recorder()
        shadow = self.pb.StateShadow(self.state, self.shadow_root, rec["refused_writes"])
        return self.pb.install_guards(km, None, shadow, rec), rec, shadow

    def test_the_recorders_take_every_shape_the_kernel_calls_with(self):
        km = self._kernel()
        names, rec, _shadow = self._guards(km)
        self.assertEqual(set(names), {n for n in EXPECTED_NEUTRALIZED if not n.endswith(".subprocess")},
                         "a namespace without a subprocess attribute gets no tripwire; everything else is guarded")
        # _cached_feed's two card pushes (quiet when the turn push already buzzed), the turn tick's push
        # without a badge, the federation handler's with a host, and a keyword this tree does not have
        km._push_notify("api: done", "body", SID_API, 2, kind="card", card_id="g7", quiet=True)
        km._push_notify("api: done", "body", SID_API, 2, kind="card", card_id="g7")
        km._push_notify("web finished a turn", "body", SID_WEB, kind="turn")
        km._push_notify("tests: done", "body", SID_TESTS, kind="card", card_id="g1", host="TESTHOST")
        km._push_notify("later", "body", SID_WEB, kind="card", card_id="g1", later_keyword=1)
        km._system_notify("romp: api", "body")
        km._push_forward([{"title": "api: done", "body": "body", "sid": SID_API, "kind": "card", "cardId": "g7"}])
        km._badge_push(3)
        self.assertIsNone(km._refresh_remote_prices(time.time()))
        self.assertIsNone(km._warm_fleet_bg(time.time()))
        self.assertEqual(rec["notifications"],
                         [("push", "api: done"), ("push", "api: done"), ("push", "web finished a turn"), ("push", "tests: done"),
                          ("push", "later"), ("system", "romp: api"), ("forward", 1), ("badge", 3)])
        self.assertEqual(len(rec["warm_calls"]), 1, "the background-parse call is counted, not run")
        km.gp._kick("owner/repo")                       # the chip's two shapes: the repo alone, and with the numbers to check
        km.gp._kick("owner/repo", (7, 9))
        self.assertEqual(rec["gh_kicks"], ["owner/repo", "owner/repo"], "the gh refresh is counted, not started")
        # threads started after the guards are counted (the real _warm_fleet_bg would start one per call)
        self.assertEqual(rec["thread_starts"], 0)
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        self.assertEqual(rec["thread_starts"], 1)

    def test_atomic_writes_aimed_at_the_copy_land_in_the_shadow_and_the_rest_are_refused(self):
        km = self._kernel()
        _names, rec, shadow = self._guards(km)
        km._atomic_write(os.path.join(self.state, "sub", "x.json"), "{}")
        self.assertEqual([os.path.basename(p) for p, _t in self.writes], ["x.json"], "a write aimed under the copy goes through")
        self.assertEqual(Path(self.writes[0][0]), Path(self.shadow_root) / "sub" / "x.json", "to the shadow, not the copy")
        self.assertFalse(os.path.exists(os.path.join(self.state, "sub")), "the copy gained nothing")
        self.assertEqual(shadow.written, ["checkpoints", "sub/x.json"], "and is recorded relative to the copy, after the "
                         "checkpoints directory the guard redirected at install (listed then, whether or not a document ever lands)")
        elsewhere = tempfile.mkdtemp(prefix="perf-bench-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        with self.assertRaises(self.pb.BenchError) as cm:
            km._atomic_write(os.path.join(elsewhere, "y.json"), "{}")
        self.assertIn("outside the state copy", str(cm.exception))
        self.assertEqual(len(self.writes), 1, "the refused write never reached the kernel's function")
        self.assertEqual(shadow.written, ["checkpoints", "sub/x.json"])
        self.assertEqual(rec["refused_writes"], [os.path.realpath(os.path.join(elsewhere, "y.json"))],
                         "on record before the raise, so a caller that swallows the exception cannot hide it")
        # the shadow's own files are written in place: the audit log's trim rewrites the shadowed log
        km._atomic_write(os.path.join(self.shadow_root, "order-audit.jsonl"), "")
        self.assertEqual(Path(self.writes[1][0]), Path(self.shadow_root) / "order-audit.jsonl")
        self.assertEqual(shadow.written, ["checkpoints", "sub/x.json"], "a write already inside the shadow is not a diverted one")

    def test_the_kernels_state_reads_see_the_shadow_once_it_holds_the_file(self):
        # the session order's read-modify-write: the push reads session-order.json, appends the new sids and
        # writes it back. The write lands in the shadow, so a reader still pointed at the copy would find the
        # file unchanged and every later build would append again (an audit record with a captured stack
        # each time, inside the rows this tool times); the overlaid reader serves the shadowed file instead
        km = self._kernel()
        seen = []
        km._read_state_json = lambda path, st=None, expect=None: seen.append((Path(path), st)) or None
        _names, _rec, shadow = self._guards(km)
        order = os.path.join(self.state, "session-order.json")
        km._read_state_json(order, "a-stat", expect=list)
        self.assertEqual(seen[-1], (Path(order), "a-stat"), "nothing shadowed yet: the copy's file, with the caller's stat")
        km._atomic_write(order, "[]")
        km._read_state_json(order, "a-stat", expect=list)
        self.assertEqual(seen[-1], (Path(self.shadow_root) / "session-order.json", None),
                         "the shadowed file, and the caller's stat of the copy's file is dropped")
        self.assertEqual(km._order_audit_path(), Path(self.shadow_root) / "order-audit.jsonl")
        self.assertEqual(shadow.written, ["checkpoints", "session-order.json", "order-audit.jsonl"])

    def test_the_checkpoint_directory_is_moved_into_the_shadow(self):
        # kernel/judge.py installs the provider `lambda: STATE / "checkpoints"` at import, and the event model writes every
        # checkpoint document into the directory it names, at run time, with Path.write_text and os.replace: a door neither
        # the _atomic_write shadow nor the import-time write_text diversion covers, so a run left one document per transcript
        # in the copy. The guard hands the event model a provider naming the shadow's directory, recorded as a redirected
        # path at install so the report names it whether or not a document lands in the run
        km = self._kernel()
        names, rec, shadow = self._guards(km)
        self.assertIn("em.set_checkpoint_dir (shadowed)", names)
        self.assertEqual(len(self.ckpt_providers), 1, "one provider installed")
        self.assertEqual(self.ckpt_providers[0](), Path(self.shadow_root) / "checkpoints")
        self.assertEqual(shadow.written, ["checkpoints"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "checkpoints")), "the copy gained nothing")
        self.assertEqual(rec["refused_writes"], [])

    def test_a_renamed_checkpoint_setter_is_an_error_and_an_event_model_without_checkpoints_is_skipped(self):
        km = self._kernel()
        km.em = SimpleNamespace(_CKPT_DIR_FN=None)          # the directory provider without its setter: a renamed door would
        with self.assertRaises(self.pb.BenchError) as cm:   #  leave the kernel's provider in place and the documents in the copy
            self._guards(km)
        self.assertIn("set_checkpoint_dir", str(cm.exception))
        km = self._kernel()
        km.em = SimpleNamespace()                            # an event model from before the checkpoints: nothing to divert
        names, _rec, shadow = self._guards(km)
        self.assertNotIn("em.set_checkpoint_dir (shadowed)", names)
        self.assertEqual(shadow.written, [])

    def test_a_missing_safety_target_is_an_error(self):
        km = self._kernel()
        del km._push_notify
        with self.assertRaises(self.pb.BenchError) as cm:
            self._guards(km)
        self.assertIn("_push_notify", str(cm.exception))
        km = self._kernel()
        del km._read_state_json                     # a kernel whose state reads bypass the overlay would re-fire the order's append per build
        with self.assertRaises(self.pb.BenchError) as cm:
            self._guards(km)
        self.assertIn("_read_state_json", str(cm.exception))


class CheckpointShadow(unittest.TestCase):
    """The checkpoints against the REAL event model (kernel/event_model.py under a private name: the one romp module this
    file runs in-process, because the write door under test is the event model's own). checkpoint_write composes one JSON
    document per JSONL file read resumably (fold_records with a ckpt name), writes it with Path.write_text beside its target
    and os.replace()s it into the directory the provider names; kernel/judge.py installs `lambda: STATE / "checkpoints"` at
    import. The tool's shadow took neither step (its write_text diversion covers the kernel import only; the document is not
    an _atomic_write), so a run wrote one document per transcript into the copy, listed as new by the census, and the next
    run restored from them, which made its cold rows warm. install_guards points the provider at the shadow: the document
    lands there under the event model's own name for it, and the copy's file census is what it was."""

    def setUp(self):
        self.pb = load_source("perf_bench_ckpt_under_test", TOOL)
        saved = pwd.getpwnam, pwd.getpwuid                 # install_guards counts these process-wide
        self.addCleanup(lambda: (setattr(pwd, "getpwnam", saved[0]), setattr(pwd, "getpwuid", saved[1])))
        saved_start = threading.Thread.start               # and wraps this
        self.addCleanup(setattr, threading.Thread, "start", saved_start)
        # a private name: the suite's other modules hold romp_event_model, and load_source re-executes a name it finds
        self.em = load_source("romp_event_model_perf_bench_ckpt", os.path.join(ROOT, "kernel", "event_model.py"))
        self.addCleanup(self.em.set_checkpoint_dir, None)
        root = tempfile.mkdtemp(prefix="perf-bench-ckpt-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        # a tiny state copy: one registered session and its states log, the file the kernel's states overlay reads
        self.state = os.path.join(root, "romp")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(self.state, d))
        (Path(self.state) / "names" / SID_WEB).write_text("web\t/w/notes-api\t#4a7bd0\t#ffffff\n")
        (Path(self.state) / "sdk" / (SID_WEB + ".json")).write_text(json.dumps(
            {"sid": SID_WEB, "name": "web", "cwd": "/w/notes-api", "mode": "acceptEdits", "alive": True}))
        self.log = os.path.join(self.state, "states", SID_WEB + ".jsonl")
        with open(self.log, "w") as f:
            f.write(json.dumps({"t": 1_800_000_000, "state": "waiting"}) + "\n")
            f.write(json.dumps({"t": 1_800_000_060, "state": "working"}) + "\n")
        self.shadow_root = os.path.realpath(os.path.join(root, "private", "shadow"))   # target() answers a resolved path
        os.makedirs(self.shadow_root)
        self.em.set_checkpoint_dir(lambda: Path(self.state) / "checkpoints")   # what the judge's import installs

    def _kernel(self):
        """The kernel the guards see: the real event model, the state root, and the other targets as no-ops."""
        return SimpleNamespace(jd=SimpleNamespace(STATE=Path(self.state)), em=self.em,
                               _atomic_write=lambda path, text, mode=None: None,
                               _read_state_json=lambda path, st=None, expect=None: None,
                               _order_audit_path=lambda: Path(self.state) / "order-audit.jsonl",
                               _refresh_remote_prices=None, _warm_fleet_bg=None, _system_notify=None,
                               _push_notify=None, _push_forward=None, _badge_push=None)

    def _read_log(self, kinds=None):
        """The states log read resumably under the name benchStates, the way the kernel's states overlay reads it."""
        return self.em.fold_records({}, self.log, list, lambda st, r: st + [r["state"]],
                                    on=None if kinds is None else kinds.append, ckpt="benchStates")

    def test_a_checkpoint_lands_in_the_shadow_and_the_copy_census_is_unchanged(self):
        em = self.em
        before = _tree_hash(self.state)
        rec = self.pb.new_recorder()
        shadow = self.pb.StateShadow(self.state, self.shadow_root, rec["refused_writes"])
        names = self.pb.install_guards(self._kernel(), None, shadow, rec)
        self.assertEqual(em._asm_ckpt_file(self.log).parent, Path(self.shadow_root, "checkpoints"),
                         "the assembly's documents derive their directory from the same provider, so one guard covers them")
        # the first read of the file steps every record and leaves the path dirty; the write that follows is the
        # settle's (and the exit drain's) checkpoint_write
        kinds = []
        self.assertEqual((self._read_log(kinds), kinds), (["waiting", "working"], ["refold"]))
        self.assertIn(self.log, em.checkpoint_dirty())
        self.assertTrue(em.checkpoint_write(self.log), "the document was written")
        self.assertEqual(_tree_hash(self.state), before, "the copy gained no checkpoints directory and no document")
        docs = sorted(Path(self.shadow_root, "checkpoints").glob("*.json"))
        self.assertEqual(len(docs), 1, "one document, in the shadow's checkpoints directory: %r" % docs)
        self.assertEqual(docs[0], em._ckpt_file(self.log), "under the event model's own name for the file's document")
        doc = json.loads(docs[0].read_text())
        self.assertEqual(doc["path"], os.path.realpath(self.log))
        self.assertEqual(doc["folds"]["benchStates"]["count"], 2)
        self.assertNotIn(self.log, em.checkpoint_dirty(), "the write cleared the dirty mark, as it does live")
        self.assertIn("em.set_checkpoint_dir (shadowed)", names)
        self.assertIn("checkpoints", shadow.written)
        self.assertEqual(rec["refused_writes"], [])

    def test_the_restore_in_a_later_read_uses_the_shadow_document_not_the_copy(self):
        # the second half of the promise: a fresh read of the file (the next run's cold row, or a cache the tool empties)
        # finds the document where the guard put it, so the tail read resumes from the shadow and the copy's absence of a
        # checkpoints directory is never a fallback the event model counts
        em = self.em
        rec = self.pb.new_recorder()
        shadow = self.pb.StateShadow(self.state, self.shadow_root, rec["refused_writes"])
        self.pb.install_guards(self._kernel(), None, shadow, rec)
        self._read_log()
        self.assertTrue(em.checkpoint_write(self.log))
        with em._JSONL_CACHE_LOCK:                          # what a restart does to the in-memory side
            em._JSONL_CACHE.clear()
        kinds = []
        self.assertEqual((self._read_log(kinds), kinds), (["waiting", "working"], ["restore"]))
        self.assertFalse(os.path.exists(os.path.join(self.state, "checkpoints")), "still nothing under the copy")
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {}, "the restore verified against the shadow's document")


class InProcessChecks(unittest.TestCase):
    """The tool's factored guards and folds, called directly: the two proofs a cold build_session sample
    must pass, the liveness row count, the environment rewrite, the constructor-free backend's liveness
    rows, the steady-push bucketing, the partial output a late BenchError leaves, and the mirror copy's
    tolerance for vanished files. Each is a function of the tool module (loaded from its path; the import
    pulls in only the standard library), so no kernel runs here."""

    @classmethod
    def setUpClass(cls):
        cls.pb = load_source("perf_bench_checks_under_test", TOOL)

    def _tmp(self, prefix):
        d = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    # ── check_cold_sample ────────────────────────────────────────────────────────────────────────
    def _em(self, **stats):
        return SimpleNamespace(_ASM_STATS=dict(stats), _ASM_CACHE={}, _ASM_LOCK=threading.Lock())

    def test_a_cold_sample_that_ran_no_full_assembly_is_an_error(self):
        em = self._em(full=3, serve=10)
        before = dict(em._ASM_STATS)
        em._ASM_STATS["serve"] += 1                          # served from a cache: not a cold parse
        with self.assertRaises(self.pb.BenchError) as cm:
            self.pb.check_cold_sample(em, "11111111", "/x/t.jsonl", before)
        self.assertIn("ran no full assembly", str(cm.exception))
        self.assertIn("'serve': 1", str(cm.exception), "the message carries the counters that did move")

    def test_a_cold_sample_whose_own_transcript_is_not_in_the_assembly_cache_is_an_error(self):
        em = self._em(full=3)
        before = dict(em._ASM_STATS)
        em._ASM_STATS["full"] += 1
        em._ASM_CACHE[("/x/other.jsonl", 7)] = object()      # a peer's transcript assembled, not this session's
        with self.assertRaises(self.pb.BenchError) as cm:
            self.pb.check_cold_sample(em, "11111111", "/x/t.jsonl", before)
        self.assertIn("never assembled its own transcript", str(cm.exception))
        self.assertIn("t.jsonl", str(cm.exception))

    def test_a_cold_sample_with_a_full_assembly_of_its_own_transcript_passes(self):
        em = self._em(full=3)
        before = dict(em._ASM_STATS)
        em._ASM_STATS["full"] += 1
        em._ASM_CACHE[("/x/t.jsonl", 7)] = object()
        self.assertEqual(self.pb.check_cold_sample(em, "11111111", "/x/t.jsonl", before), {"full": 1})

    # ── check_snapshot_rows ──────────────────────────────────────────────────────────────────────
    REGS = [{"sid": SID_WEB, "alive": True}, {"sid": SID_API, "alive": True}, {"sid": SID_TESTS, "alive": False},
            {"sid": "44444444-5555-6666-7777-888888888888", "alive": True, "threadOf": SID_WEB}]

    def test_a_snapshot_with_fewer_rows_than_qualifying_regs_is_an_error(self):
        with self.assertRaises(self.pb.BenchError) as cm:
            self.pb.check_snapshot_rows({SID_WEB: {}}, self.REGS, False)
        self.assertIn("1 rows but 2 registry entries", str(cm.exception))
        self.assertEqual(self.pb.check_snapshot_rows({SID_WEB: {}, SID_API: {}}, self.REGS, False), 2)
        self.assertEqual(self.pb.check_snapshot_rows({SID_WEB: {}, SID_API: {}, SID_TESTS: {}}, self.REGS, True), 3,
                         "--all-regs-live counts the closed reg too, never the comment thread")
        with self.assertRaises(self.pb.BenchError):
            self.pb.check_snapshot_rows({SID_WEB: {}, SID_API: {}}, self.REGS, True)

    # ── prepare_env ──────────────────────────────────────────────────────────────────────────────
    def test_prepare_env_drops_the_keys_and_floors_every_seam_before_the_import(self):
        root = self._tmp("perf-bench-env-")
        state, claude, private, other = (os.path.join(root, d) for d in ("romp", "claude", "private", "other"))
        for d in (state, claude, private, other):
            os.makedirs(d)
        # every key-source and credential name conftest pops, planted with synthetic values (the first form planted
        # ANTHROPIC_* only, so a key command, an OAuth token or 1Password's names would have reached the kernel
        # import; review find, 2026-09-08)
        keys = {k: "planted-" + k.lower() for k in self.pb.KEY_SOURCE_ENV}
        keys["OP_SESSION_testaccount"] = "planted-op-session"
        planted = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:1", "ROMP_MANAGER_PORT": "7432",
                   "ROMP_MANAGER_PID": "1", "ROMP_STATE_DIR": other, "TMUX": "planted", "ROMP_MODEL_CATALOG": "on", **keys}
        with mock.patch.dict(os.environ, planted):
            changes = self.pb.prepare_env(state, claude, private)
            env = dict(os.environ)
        self.assertFalse([k for k in env if k.startswith("ANTHROPIC_") or k.startswith("OP_SESSION_")], "every ANTHROPIC_* and OP_SESSION_* variable is gone")
        for k in keys:
            self.assertNotIn(k, env, k)
            self.assertIn("unset " + k, changes)
        self.assertEqual(env["ROMP_MANAGER_PORT"], "1", "a dead port, not an absent variable (absent maps to the live default)")
        self.assertNotIn("ROMP_MANAGER_PID", env)
        self.assertNotIn("TMUX", env)
        self.assertEqual(env["ROMP_STATE_DIR"], state)
        self.assertEqual(env["XDG_STATE_HOME"], os.path.dirname(state))
        self.assertEqual(env["ROMP_MODEL_CATALOG"], "off")
        self.assertEqual(env["ROMP_CLI_SCOPE"], "0")
        self.assertEqual(env["ROMP_CLAUDE_BIN"], "/bin/false")
        self.assertEqual(env["ROMP_KERNEL_NO_OPEN"], "1")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], claude)
        self.assertTrue(env["ROMP_SERVICE_ENV_FILE"].startswith(private + os.sep) and not os.path.exists(env["ROMP_SERVICE_ENV_FILE"]))
        self.assertEqual(env["ROMP_SERVICE_ENV"], env["ROMP_SERVICE_ENV_FILE"])
        for c in ("unset ANTHROPIC_API_KEY", "unset ANTHROPIC_BASE_URL", "unset ROMP_MANAGER_PID", "unset TMUX", "unset ROMP_STATE_DIR",
                  "set ROMP_MANAGER_PORT", "set ROMP_MODEL_CATALOG", "set ROMP_CLI_SCOPE", "set ROMP_CLAUDE_BIN",
                  "set ROMP_SERVICE_ENV_FILE", "set ROMP_STATE_DIR", "set XDG_STATE_HOME", "set CLAUDE_CONFIG_DIR"):
            self.assertIn(c, changes)

    def test_the_key_source_list_is_the_kernels_own(self):
        # The names come from the code's constants, the way tests/conftest.py's floor is pinned on main: a
        # provider that adds a credential name adds it there, and this fails until the tool drops it too. Read
        # from the source text, so no romp module loads in this process.
        cr = _module_constants(os.path.join(ROOT, "kernel", "credentials.py"), ("FLOOR_ENV_NAMES", "FLOOR_ENV_PREFIXES"))
        sb = _module_constants(os.path.join(ROOT, "kernel", "sdk_backend.py"), ("AUTH_ENV_NAMES",))
        expected = set(cr["FLOOR_ENV_NAMES"]) | set(sb["AUTH_ENV_NAMES"])
        self.assertEqual(set(self.pb.KEY_SOURCE_ENV), expected)
        self.assertEqual(set(self.pb.KEY_SOURCE_ENV_PREFIXES), {"ANTHROPIC_"} | set(cr["FLOOR_ENV_PREFIXES"]))

    # ── check_guards_held ────────────────────────────────────────────────────────────────────────
    def test_a_refusal_still_on_record_at_the_end_of_the_run_is_an_error_naming_the_guard(self):
        clean = {"spawns": [], "refused_writes": []}
        self.assertIsNone(self.pb.check_guards_held(clean))
        with self.assertRaises(self.pb.BenchError) as cm:
            self.pb.check_guards_held({"spawns": ["subprocess.run ['date'] via kernel.py:1 _push"], "refused_writes": []})
        self.assertIn("subprocess tripwire: 1 refused spawn(s): subprocess.run ['date']", str(cm.exception))
        self.assertIn("push build:", str(cm.exception), "says where the swallowed traceback went")
        with self.assertRaises(self.pb.BenchError) as cm:
            self.pb.check_guards_held({"spawns": [], "refused_writes": ["/x/y.json"] * 5})
        self.assertIn("_atomic_write guard: 5 refused write(s) outside the copy: /x/y.json, /x/y.json, /x/y.json, ...", str(cm.exception))
        self.assertNotIn("tripwire", str(cm.exception))

    # ── make_backend ─────────────────────────────────────────────────────────────────────────────
    def _sbmod(self):
        class FakeSdkBackend:
            def _live_row(self, reg, sid):
                return {"sid": sid, "state": "waiting", "connected": False}
        regs = [{"sid": SID_WEB, "alive": True}, {"sid": SID_API, "alive": True}, {"sid": SID_TESTS, "alive": False},
                {"sid": "44444444-5555-6666-7777-888888888888", "alive": True, "threadOf": SID_WEB}]
        return SimpleNamespace(SdkBackend=FakeSdkBackend, list_regs=lambda d: regs,
                               last_state_value=lambda d, sid: "working" if sid == SID_WEB else "")

    def test_the_bench_backend_lists_alive_regs_with_their_last_state_and_skips_threads(self):
        be = self.pb.make_backend(self._sbmod(), "/x/state", dormant_rows=False, all_regs=False)
        rows = be.live_sessions()
        self.assertEqual(set(rows), {SID_WEB, SID_API}, "alive regs only; the closed reg and the comment thread are out")
        self.assertEqual(rows[SID_WEB]["state"], "working", "the last states/ record wins over the dormant mapping")
        self.assertIs(rows[SID_WEB]["connected"], True, "and the row reads as connected, as a running session reports")
        self.assertEqual(rows[SID_API]["state"], "waiting", "an empty last state leaves _live_row's value")
        self.assertEqual(be._owns_memo, {}, "the slot owns() memoizes on")
        with self.assertRaises(self.pb.BenchError) as cm:
            be.no_such_attribute
        self.assertIn("no_such_attribute", str(cm.exception))

    def test_dormant_rows_and_all_regs_live_change_the_bench_backend_rows(self):
        dormant = self.pb.make_backend(self._sbmod(), "/x/state", dormant_rows=True, all_regs=False).live_sessions()
        self.assertEqual(dormant[SID_WEB], {"sid": SID_WEB, "state": "waiting", "connected": False}, "--dormant-rows keeps _live_row's row verbatim")
        every = self.pb.make_backend(self._sbmod(), "/x/state", dormant_rows=False, all_regs=True).live_sessions()
        self.assertEqual(set(every), {SID_WEB, SID_API, SID_TESTS}, "--all-regs-live includes the closed reg, still not the thread")

    # ── the steady push's buckets ────────────────────────────────────────────────────────────────
    def test_push_steady_reports_the_quiet_samples_and_sets_the_rebuilds_aside(self):
        # The subprocess fixture cannot force a rebuild (it depends on the 5 s view-signature bucket rolling
        # REBUILD_MIN_S after the previous build, wall-clock alignment), so its assertions hold with zero
        # rebuild samples; this drives the bucketing with samples of both kinds.
        mk = lambda ms, feed=False, tl=False: {"ms": ms, "rebuilt_feed": feed, "rebuilt_timeline": tl, "bytes": 100}
        samples = [mk(10), mk(3000, feed=True), mk(12), mk(2800, tl=True), mk(11)]
        quiet, rebuilt = self.pb.bucket_steady_samples(samples)
        self.assertEqual([q["ms"] for q in quiet], [10, 12, 11])
        self.assertEqual([r["ms"] for r in rebuilt], [3000, 2800])
        st, st2 = self.pb.steady_rows(samples, ["chat"])
        self.assertEqual((st["n"], st["median"], st["rebuild_samples"], len(st["samples"])), (3, 11, 2, 5))
        self.assertEqual((st2["n"], st2["median"]), (2, 2900))
        self.assertEqual(self.pb.steady_rows(samples[:1], ["chat"])[1], None, "no rebuild row without a rebuild sample")

    # ── a late BenchError keeps what was measured ────────────────────────────────────────────────
    def test_a_late_bench_error_still_prints_the_rows_and_writes_the_partial_json(self):
        state = self._tmp("perf-bench-partial-")
        for d in ("sdk", "names"):
            os.mkdir(os.path.join(state, d))
        out_json = os.path.join(state, "out.json")

        def fake_run(args, st, mirror_of, out, private):
            out["repo"], out["repo_head"], out["state"], out["state_mirror_of"] = "/x/repo", "abc", st, mirror_of
            out["benchmarks"] = {"build_feed": {"n": 1, "min": 1.0, "median": 1.0, "max": 1.0}}
            raise self.pb.BenchError("late guard")
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(self.pb, "run", fake_run), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = self.pb.main(["--state", state, "--json", out_json])
        self.assertEqual(rc, 1)
        with open(out_json) as f:
            out = json.load(f)
        self.assertEqual(out["error"], "late guard")
        self.assertIn("build_feed", out["benchmarks"])
        self.assertIn("build_feed", stdout.getvalue(), "the rows measured before the error are printed")
        self.assertIn("(partial: the run stopped on an error)", stdout.getvalue())
        self.assertIn("perf-bench: late guard", stderr.getvalue())

    # ── mirror_state ─────────────────────────────────────────────────────────────────────────────
    def test_the_mirror_tolerates_files_that_vanished_during_the_copy_and_nothing_else(self):
        src = self._tmp("perf-bench-mirror-src-")
        real_mkdtemp, made = tempfile.mkdtemp, []

        def tracking_mkdtemp(*a, **k):
            made.append(real_mkdtemp(*a, **k))
            return made[-1]
        self.addCleanup(lambda: [shutil.rmtree(d, ignore_errors=True) for d in made])
        vanished = shutil.Error([(os.path.join(src, "tmp"), "/x/tmp", "[Errno 2] No such file or directory: 'tmp'")])
        stderr = io.StringIO()
        with mock.patch.object(self.pb.tempfile, "mkdtemp", tracking_mkdtemp):
            with mock.patch.object(self.pb.shutil, "copytree", side_effect=vanished), contextlib.redirect_stderr(stderr):
                root, dst = self.pb.mirror_state(src)
            self.assertEqual((root, dst), (made[-1], os.path.join(made[-1], "romp")))
            self.assertIn("1 file(s) vanished during the mirror copy", stderr.getvalue())
            denied = shutil.Error([(os.path.join(src, "a"), "/x/a", "[Errno 13] Permission denied: 'a'")])
            with mock.patch.object(self.pb.shutil, "copytree", side_effect=denied):
                with self.assertRaises(shutil.Error):
                    self.pb.mirror_state(src)
            self.assertFalse(os.path.exists(made[-1]), "a copy that failed for another reason leaves no half-made mirror")
            mixed = shutil.Error([(os.path.join(src, "tmp"), "/x/tmp", "[Errno 2] No such file or directory: 'tmp'"),
                                  (os.path.join(src, "a"), "/x/a", "[Errno 13] Permission denied: 'a'")])
            with mock.patch.object(self.pb.shutil, "copytree", side_effect=mixed):
                with self.assertRaises(shutil.Error):
                    self.pb.mirror_state(src)


if __name__ == "__main__":
    unittest.main()
