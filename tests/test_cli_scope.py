#!/usr/bin/env python3
"""Per-session transient systemd scopes (2026-09-05). Under the systemd user service every process
the service tree starts shares the service's cgroup, and KillMode=control-group (deliberately kept)
empties it on `systemctl --user restart romp-manager` — sessions' tmux jobs died with it. The fix
spawns each session's CLI through bin/romp-cli-scope (exec-in-place `systemd-run --scope`), decided
ONCE per backend by cli_scope_supported and applied in _options.

Under test here, with NO real systemd-run ever invoked (which/run are injected; conftest floors
ROMP_CLI_SCOPE=0 for every backend construction):
  * the cli_scope_supported truth table and the exact probe argv, and which off verdicts are filed
    as problems (wanted on Linux and unavailable: the error center must show that sessions run inside
    the service cgroup) rather than plain log lines (off by request, or no systemd on the platform);
  * the backend caches one verdict at construction, and _options honours it: cli_path becomes the
    wrapper and ROMP_CLI_REAL carries the real CLI when on; both untouched when off;
  * a missing or non-executable wrapper degrades loudly (a problem line, once) to the direct path;
  * the wrapper's fallback notice (a failed pre-flight, the CLI run directly; its `fallback:` form)
    is logged the moment it arrives, as a problem naming the session, since on that path the CLI
    starts and nothing else would ever read it; its `refused:` line (ROMP_CLI_REAL unset, exit 127)
    is only buffered — no CLI started, and the launch-error path reports it;
  * the per-session limits (2026-09-06; cli_scope_limits, CLI_SCOPE_LIMITS): the size and
    oom_score_adj rules, agreement with the wrapper's own shell rules on one shared corpus, the
    once-per-backend read, the _options overlay (vetted values down as themselves, refused ones down
    empty, unset ones not sent), and the wrapper's third stderr form (`ignored:`), logged at arrival
    as a problem naming the session;
  * the boot probe (_cli_scope_settle, on a scripted runner): the property probe scope with the
    wrapper's retry chain and the deciding failure quoted, the memory-controller check inside a probe
    scope (its verdict a marker the command prints, so a scope that never started is unsettled, not
    "no controller"), the adjustment write in a throwaway child — what each refuses lands in
    `rejected`, the controller verdict is kept on the backend as cli_scope_memory_delegated, a check
    that does not answer settles nothing and the boot line lists each value under its own verdict
    (never an unsettled value as in force, never a settled one as unknown), `unsettled` names the
    checks that settled nothing, and no probe runs with the scopes off, without a runner, or for a
    limit that is not set; every cell of that table is pinned in SettleTable.
Synthetic fixtures only: placeholder sid, /bin/true as the CLI.
"""
import os
import subprocess
import sys
import tempfile
import types
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"   # the conftest floor, re-asserted for a bare unittest run
for _v in ("ROMP_CLI_SCOPE_MEMORY_MAX", "ROMP_CLI_SCOPE_MEMORY_HIGH", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX",
           "ROMP_CLI_SCOPE_OOM_SCORE_ADJ"):
    os.environ.pop(_v, None)         # and the limits floor (the same reasoning: tool shells inherit them)
sb = SourceFileLoader("romp_sdk_backend_cli_scope", os.path.join(BIN, "romp_sdk_backend.py")).load_module()

SID = "11111111-2222-3333-4444-555555555555"
PROBE = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", "true"]


def _which(found=True):
    return lambda name: ("/usr/bin/" + name) if found else None


class _Log:
    """A recording stand-in for SdkBackend._log, the callback cli_scope_supported is handed: every call
    as (line, problem). `lines` is the text alone; `problems` the text of the calls flagged problem=True."""

    def __init__(self):
        self.calls = []

    def __call__(self, m, problem=None):
        self.calls.append((m, problem))

    @property
    def lines(self):
        return [m for m, _ in self.calls]

    @property
    def problems(self):
        return [m for m, p in self.calls if p]


class _Run:
    """A recording stand-in for subprocess.run: returns a fixed result, or raises."""

    def __init__(self, returncode=0, stderr=b"", raise_=None):
        self.calls = []
        self.rc, self.stderr, self.raise_ = returncode, stderr, raise_

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if self.raise_:
            raise self.raise_
        return subprocess.CompletedProcess(argv, self.rc, stdout=b"", stderr=self.stderr)


class Supported(unittest.TestCase):
    """cli_scope_supported's truth table, on injected inputs."""

    def test_unsupervised_is_off_without_probing(self):
        run = _Run()
        self.assertFalse(sb.cli_scope_supported({}, which=_which(), platform="linux", run=run))
        self.assertEqual(run.calls, [], "no probe when the switch is off")

    def test_explicit_off_under_supervision(self):
        run = _Run()
        logged = _Log()
        self.assertFalse(sb.cli_scope_supported({"ROMP_SUPERVISED": "1", "ROMP_CLI_SCOPE": "0"},
                                                which=_which(), platform="linux", run=run, log=logged))
        self.assertEqual(run.calls, [])
        self.assertEqual(logged.problems, [], "off by request is not a problem")
        self.assertIn("ROMP_CLI_SCOPE=0", logged.lines[0])

    # The three ways a WANTED switch fails on Linux — no systemd-run, a probe that exits non-zero, a
    # probe that raises — are each filed as a problem (the error center shows them), and the line says
    # what follows: sessions run inside the service cgroup. Before 2026-09-06 these were plain log lines,
    # and a box whose user manager could not start scopes showed nothing anywhere the user looks.
    def test_supervised_without_systemd_run_is_off(self):
        run = _Run()
        logged = _Log()
        self.assertFalse(sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=_which(False), platform="linux", run=run,
                                                log=logged))
        self.assertEqual(run.calls, [], "nothing to probe with")
        self.assertEqual(len(logged.calls), 1)
        self.assertTrue(logged.lines[0].startswith("cli scope: off — "), logged.lines)
        self.assertIn("systemd-run", logged.lines[0])
        self.assertEqual(logged.problems, logged.lines, "wanted and unavailable: a problem entry")
        self.assertIn("inside the service cgroup", logged.lines[0], "the line says where sessions land")

    def test_supervised_with_a_failing_probe_is_off(self):
        run = _Run(returncode=1, stderr=b"Failed to start transient scope unit: Access denied\n")
        logged = _Log()
        self.assertFalse(sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=_which(), platform="linux", run=run,
                                                log=logged))
        self.assertEqual(len(run.calls), 1)
        self.assertTrue(logged.lines[0].startswith("cli scope: off — "), logged.lines)
        self.assertIn("Access denied", logged.lines[0], "the probe's stderr is the reason the user reads")
        self.assertEqual(logged.problems, logged.lines, "a probe the user manager refused: a problem entry")

    def test_a_probe_that_raises_is_off(self):
        for exc in (subprocess.TimeoutExpired(PROBE, 10), OSError("boom")):
            run = _Run(raise_=exc)
            logged = _Log()
            self.assertFalse(sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=_which(), platform="linux", run=run,
                                                    log=logged))
            self.assertTrue(logged.lines[0].startswith("cli scope: off — "), logged.lines)
            self.assertEqual(logged.problems, logged.lines, "%r: a problem entry" % (exc,))

    def test_explicit_on_that_cannot_be_honoured_is_a_problem_too(self):
        # ROMP_CLI_SCOPE=1 outside the service is the user asking by hand; a box that cannot oblige
        # reports it the same way
        logged = _Log()
        self.assertFalse(sb.cli_scope_supported({"ROMP_CLI_SCOPE": "1"}, which=_which(False), platform="linux",
                                                run=_Run(), log=logged))
        self.assertEqual(len(logged.problems), 1, logged.calls)

    def test_supervised_with_a_passing_probe_is_on(self):
        run = _Run()
        logged = _Log()
        self.assertTrue(sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=_which(), platform="linux", run=run,
                                               log=logged))
        self.assertEqual(len(logged.calls), 1)
        self.assertTrue(logged.lines[0].startswith("cli scope: on — "), logged.lines)
        self.assertEqual(logged.problems, [], "on is not a problem")

    def test_explicit_on_outside_supervision_probes_and_turns_on(self):
        run = _Run()
        self.assertTrue(sb.cli_scope_supported({"ROMP_CLI_SCOPE": "1"}, which=_which(), platform="linux", run=run))
        self.assertEqual(len(run.calls), 1)

    def test_the_probe_argv_is_exact_and_bounded(self):
        run = _Run()
        sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=_which(), platform="linux", run=run)
        argv, kw = run.calls[0]
        self.assertEqual(argv, PROBE)
        self.assertEqual(sb.CLI_SCOPE_PROBE, PROBE)
        self.assertEqual(kw.get("timeout"), sb.CLI_SCOPE_PROBE_TIMEOUT)
        self.assertGreater(sb.CLI_SCOPE_PROBE_TIMEOUT, 0)
        self.assertLessEqual(sb.CLI_SCOPE_PROBE_TIMEOUT, 10)

    def test_an_empty_switch_value_is_not_on(self):
        run = _Run()
        self.assertFalse(sb.cli_scope_supported({"ROMP_CLI_SCOPE": ""}, which=_which(), platform="linux", run=run))
        self.assertEqual(run.calls, [])

    def test_no_log_callback_is_fine(self):
        self.assertTrue(sb.cli_scope_supported({"ROMP_CLI_SCOPE": "1"}, which=_which(), platform="linux", run=_Run()))

    def test_not_linux_is_off_before_which_or_probe(self):
        # the macOS launchd plist sets ROMP_SUPERVISED=1 too; scopes are a systemd feature, so the
        # verdict there is off with a reason that says so — not "systemd-run is not on PATH"
        for plat in ("darwin", "freebsd13", "win32"):
            run = _Run()
            which_calls = []
            logged = _Log()
            ok = sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=lambda n: which_calls.append(n),
                                        run=run, log=logged, platform=plat)
            self.assertFalse(ok, plat)
            self.assertEqual(which_calls, [], "no PATH lookup off Linux")
            self.assertEqual(run.calls, [], "no probe off Linux")
            self.assertEqual(len(logged.calls), 1)
            self.assertTrue(logged.lines[0].startswith("cli scope: off — not Linux"), logged.lines)
            self.assertNotIn("PATH", logged.lines[0])
            # ...and not a problem: every macOS kernel start would otherwise open with an error-center
            # entry for a feature the platform does not have
            self.assertEqual(logged.problems, [], plat)

    def test_linux_variants_pass_the_platform_check(self):
        for plat in ("linux", "linux2"):
            self.assertTrue(sb.cli_scope_supported({"ROMP_SUPERVISED": "1"}, which=_which(), run=_Run(),
                                                   platform=plat), plat)

    def test_the_switch_off_reasons_win_over_the_platform(self):
        # off Linux, an explicit 0 or an unsupervised run still reports ITS reason, not the platform's
        logged = _Log()
        sb.cli_scope_supported({"ROMP_SUPERVISED": "1", "ROMP_CLI_SCOPE": "0"}, which=_which(), run=_Run(),
                               log=logged, platform="darwin")
        self.assertIn("ROMP_CLI_SCOPE=0", logged.lines[0])

    def test_the_platform_defaults_to_this_process(self):
        # no injected platform → sys.platform; on Linux that is the on path, elsewhere the off one
        run = _Run()
        ok = sb.cli_scope_supported({"ROMP_CLI_SCOPE": "1"}, which=_which(), run=run)
        self.assertEqual(ok, sys.platform.startswith("linux"))


class _Backend(unittest.TestCase):
    """A backend on a temp state dir, no real CLI, no real key claim, no SDK dependency."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._stash_before = sb._WORK_KEY
        sb._WORK_KEY = ""
        self._fetch_before = sb._fetch_key_fast_org
        sb._fetch_key_fast_org = lambda key: None
        self._fake_sdk = "claude_agent_sdk" not in sys.modules and not sb.sdk_importable()
        if self._fake_sdk:
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: kw
            sys.modules["claude_agent_sdk"] = fake
        self.logged = []
        # A backend whose verdict is ON exports ROMP_CLI_REAL into os.environ (the SDK's version probe
        # needs it there). Tests that turn the verdict on restore the variable themselves; this snapshot
        # is the backstop, so no test in these classes can leak it into the rest of the pytest process.
        self._real_before = os.environ.get("ROMP_CLI_REAL")
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logged.append)

    def tearDown(self):
        sb._WORK_KEY = self._stash_before
        sb._fetch_key_fast_org = self._fetch_before
        if self._fake_sdk:
            sys.modules.pop("claude_agent_sdk", None)
        if self._real_before is None:
            os.environ.pop("ROMP_CLI_REAL", None)
        else:
            os.environ["ROMP_CLI_REAL"] = self._real_before

    def _sess(self):
        return sb.SdkSession(self.be, {"sid": SID, "name": "web", "cwd": self.d, "mode": "acceptEdits"})

    def _kw(self):
        return self.be._options(self._sess(), dict)


class ConstructionVerdict(_Backend):
    """One verdict per backend, at construction."""

    def test_the_test_floor_leaves_the_scope_off(self):
        self.assertFalse(self.be.cli_scope)
        self.assertTrue(any(m.startswith("cli scope: off — ") for m in self.logged), self.logged)
        self.assertEqual([r for r in self.be.problems() if r["text"].startswith("cli scope:")], [],
                         "off by request (the floor) files nothing in the error center")

    def test_a_wanted_but_unavailable_verdict_is_filed_as_a_problem(self):
        # the boot verdict reaches the error center through the backend's own _log, the way the launch-time
        # fallbacks do: the real cli_scope_supported, on a supervised Linux box with no systemd-run
        real = sb.cli_scope_supported
        sb.cli_scope_supported = lambda **kw: real({"ROMP_SUPERVISED": "1"}, which=_which(False),
                                                   platform="linux", run=_Run(), **kw)
        try:
            be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        finally:
            sb.cli_scope_supported = real
        self.assertFalse(be.cli_scope)
        rows = [r for r in be.problems() if r["text"].startswith("cli scope: off — ")]
        self.assertEqual(len(rows), 1, be.problems())
        self.assertIn("systemd-run is not on PATH", rows[0]["text"])
        self.assertIn("inside the service cgroup", rows[0]["text"])

    def test_the_verdict_is_taken_once_and_cached(self):
        calls = []
        before = sb.cli_scope_supported
        real_before = os.environ.pop("ROMP_CLI_REAL", None)   # an ON verdict exports it — restore below
        sb.cli_scope_supported = lambda **kw: calls.append(kw) or True
        try:
            be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        finally:
            sb.cli_scope_supported = before
            os.environ.pop("ROMP_CLI_REAL", None)
            if real_before is not None:
                os.environ["ROMP_CLI_REAL"] = real_before
        self.assertEqual(len(calls), 1)
        self.assertIn("log", calls[0])
        self.assertTrue(be.cli_scope)

    # The SDK's per-connect version check runs `[cli_path, "-v"]` with the KERNEL's os.environ, not
    # options.env (claude_agent_sdk 0.2.132), so with the verdict on the wrapper must find ROMP_CLI_REAL
    # in the process environment too — or the probe exits 127 and the version warning is silently off.
    def test_on_exports_the_real_cli_into_the_kernels_environment(self):
        before = os.environ.pop("ROMP_CLI_REAL", None)
        saved = sb.cli_scope_supported
        sb.cli_scope_supported = lambda **kw: True
        try:
            be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
            self.assertTrue(be.cli_scope)
            self.assertEqual(os.environ.get("ROMP_CLI_REAL"), "/bin/true")
        finally:
            sb.cli_scope_supported = saved
            os.environ.pop("ROMP_CLI_REAL", None)
            if before is not None:
                os.environ["ROMP_CLI_REAL"] = before

    def test_off_leaves_the_kernels_environment_alone(self):
        before = os.environ.pop("ROMP_CLI_REAL", None)
        try:
            be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)   # the test floor: off
            self.assertFalse(be.cli_scope)
            self.assertNotIn("ROMP_CLI_REAL", os.environ)
        finally:
            if before is not None:
                os.environ["ROMP_CLI_REAL"] = before


class OptionsWiring(_Backend):
    """_options routes the spawn through the wrapper exactly when the verdict is on."""

    def test_off_leaves_cli_path_and_env_untouched(self):
        self.be.cli_scope = False
        kw = self._kw()
        self.assertEqual(kw["cli_path"], "/bin/true")
        self.assertNotIn("ROMP_CLI_REAL", kw["env"])

    def test_on_spawns_the_wrapper_with_the_real_cli_in_the_env(self):
        self.be.cli_scope = True
        kw = self._kw()
        self.assertEqual(kw["cli_path"], sb.cli_scope_wrapper())
        self.assertEqual(kw["env"]["ROMP_CLI_REAL"], "/bin/true")
        # the identity vars the CLI relied on are still there — the overlay is additive
        self.assertEqual(kw["env"]["ROMP_SID"], SID)
        self.assertEqual(kw["env"]["ROMP_SESSION_NAME"], "web")

    def test_the_wrapper_is_the_repos_executable(self):
        w = sb.cli_scope_wrapper()
        self.assertTrue(os.path.isabs(w))
        self.assertEqual(os.path.realpath(w), os.path.realpath(os.path.join(BIN, "romp-cli-scope")))
        self.assertTrue(os.access(w, os.X_OK), "bin/romp-cli-scope must be executable in the checkout")

    def test_a_missing_wrapper_falls_back_loudly_once(self):
        self.be.cli_scope = True
        before = sb.cli_scope_wrapper
        sb.cli_scope_wrapper = lambda: os.path.join(self.d, "no-such-wrapper")
        problems = []
        self.be._log = lambda m, problem=None: problems.append((m, problem))
        try:
            kw1 = self._kw()
            kw2 = self._kw()
        finally:
            sb.cli_scope_wrapper = before
        for kw in (kw1, kw2):
            self.assertEqual(kw["cli_path"], "/bin/true", "the session still starts, on the direct path")
            self.assertNotIn("ROMP_CLI_REAL", kw["env"])
        loud = [(m, p) for m, p in problems if "no-such-wrapper" in m]
        self.assertEqual(len(loud), 1, "reported once per backend, as a problem: %r" % (problems,))
        self.assertTrue(loud[0][1])

    def test_a_non_executable_wrapper_falls_back_too(self):
        self.be.cli_scope = True
        p = os.path.join(self.d, "romp-cli-scope")
        with open(p, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(p, 0o644)
        before = sb.cli_scope_wrapper
        sb.cli_scope_wrapper = lambda: p
        try:
            kw = self._kw()
        finally:
            sb.cli_scope_wrapper = before
        self.assertEqual(kw["cli_path"], "/bin/true")


class FallbackNotice(_Backend):
    """bin/romp-cli-scope writes one stderr line, starting `romp-cli-scope: fallback:`, when it runs
    the CLI directly after a failed pre-flight. The CLI then STARTS, so _record_launch_error never
    drains the stderr tail and the line was never read (2026-09-05): the boot verdict kept saying
    scopes were on while the session's work sat in the service cgroup. _on_cli_stderr now logs that
    line at once, as a problem naming the session; every other line is still only buffered — the
    wrapper's `romp-cli-scope: refused:` line included, since on that path no CLI started and the
    launch-error card reports it from the tail."""

    NOTICE = ("romp-cli-scope: fallback: systemd-run cannot start a transient scope (Failed to connect to bus: "
              "No such file or directory) — running the CLI directly, outside a scope; a service restart will "
              "take its background work down")
    REFUSAL = ("romp-cli-scope: refused: ROMP_CLI_REAL is unset or empty; it must name the real claude CLI, "
               "and this wrapper does not guess one")

    def _capture(self):
        problems = []
        self.be._log = lambda m, problem=None: problems.append((m, problem))
        return problems

    def test_the_notice_is_logged_once_as_a_problem_naming_the_session(self):
        problems = self._capture()
        sess = self._sess()
        sess._on_cli_stderr(self.NOTICE + "\n")
        self.assertEqual(len(problems), 1, problems)
        m, p = problems[0]
        self.assertTrue(p, "a problem line — the error center shows it, not only the log file")
        self.assertIn("web", m, "the session's name")
        self.assertIn(SID[:8], m, "and its sid")
        self.assertIn(self.NOTICE, m, "the wrapper's own reason, verbatim")
        self.assertEqual(sess.stderr_tail(), self.NOTICE, "buffered too, like any other line")

    def test_an_ordinary_line_is_only_buffered(self):
        problems = self._capture()
        sess = self._sess()
        sess._on_cli_stderr("some CLI chatter\n")
        # the prefix mid-line is not the wrapper speaking (a shell naming the wrapper's path, say)
        sess._on_cli_stderr("sh: /x/bin/romp-cli-scope: Permission denied\n")
        self.assertEqual(problems, [], "nothing logged per line — a chatty CLI must not drown the log")
        self.assertEqual(sess.stderr_tail().splitlines(),
                         ["some CLI chatter", "sh: /x/bin/romp-cli-scope: Permission denied"])

    def test_the_refusal_line_is_not_logged_as_a_fallback(self):
        # the wrapper's other line (ROMP_CLI_REAL unset, exit 127): no CLI started, so nothing ran
        # outside a scope, and the launch fails — _record_launch_error reports the line from the
        # stderr tail. Logging it here too reported the one event twice, the first time as a CLI
        # "started outside a scope" when none had started.
        problems = self._capture()
        sess = self._sess()
        sess._on_cli_stderr(self.REFUSAL + "\n")
        self.assertEqual(problems, [], "left to the launch-error path")
        self.assertEqual(sess.stderr_tail(), self.REFUSAL, "buffered: the launch-error card reads it from here")

    def test_the_generic_prefix_alone_is_not_a_fallback(self):
        # only the fallback FORM is logged: a line with the wrapper's prefix and neither second word
        # (a future third message, say) is buffered and nothing more, rather than misreported
        problems = self._capture()
        self._sess()._on_cli_stderr(sb.CLI_SCOPE_NOTICE_PREFIX + " something else entirely\n")
        self.assertEqual(problems, [])

    def test_the_prefixes_are_what_the_wrapper_writes(self):
        # the constants and the script agree: every stderr line the wrapper writes starts with the
        # generic prefix, exactly one in the fallback form, one in the refusal form and one in the
        # ignored form
        with open(os.path.join(BIN, "romp-cli-scope")) as f:
            src = f.read()
        lines = [ln for ln in src.splitlines() if ">&2" in ln and "echo" in ln]
        self.assertEqual(len(lines), 3, "the refusal, the fallback and the ignored form: %r" % (lines,))
        for ln in lines:
            self.assertIn('"%s ' % sb.CLI_SCOPE_NOTICE_PREFIX, ln, ln)
        self.assertEqual(sum('"%s ' % sb.CLI_SCOPE_FALLBACK_PREFIX in ln for ln in lines), 1, lines)
        self.assertEqual(sum('"%s ' % sb.CLI_SCOPE_REFUSAL_PREFIX in ln for ln in lines), 1, lines)
        self.assertEqual(sum('"%s ' % sb.CLI_SCOPE_IGNORED_PREFIX in ln for ln in lines), 1, lines)
        # all three forms are instances of the generic prefix, so "every line starts with it" still
        # holds, and none is a prefix of another
        forms = (sb.CLI_SCOPE_FALLBACK_PREFIX, sb.CLI_SCOPE_REFUSAL_PREFIX, sb.CLI_SCOPE_IGNORED_PREFIX)
        for p in forms:
            self.assertTrue(p.startswith(sb.CLI_SCOPE_NOTICE_PREFIX + " "), p)
            for q in forms:
                if p != q:
                    self.assertFalse(p.startswith(q), (p, q))
        # and the fixtures above are what the script writes, word for word up to the reason
        self.assertTrue(self.NOTICE.startswith(sb.CLI_SCOPE_FALLBACK_PREFIX + " systemd-run cannot start"))
        self.assertIn(self.REFUSAL.split(";")[0], src)


# ---- the per-session limits (2026-09-06) ----

# One corpus for both rule-holders: the kernel's regexes (cli_scope_limits) and the wrapper's shell
# functions (size_ok, adj_ok) must give the same verdict on every value, or a value the kernel accepts
# and hands down is refused at launch (or the reverse: never reported at boot, refused per launch).
SIZE_OK = ["16G", "8192M", "1024", "0", "1T", "5K", "infinity", "016M", "12345678901234567890"]
SIZE_BAD = ["16g", "abc", "16GB", "-1", "G", "Infinity", "16GG", " 16G", "16G\n", "1_000", "infinity ", "K16", "0x10",
            "50%", "1.5G", "16 G", "16P", "1G 512M",      # the five forms systemd takes, which the docs name as refused
            "16E",                            # an E suffix: not in the rule (and 16E is past systemd's own range too)
            "\u0663M", "\uff11\uff10M"]   # other scripts' digits: not [0-9]
ADJ_OK = ["500", "-1000", "0", "1000", "-1", "-0", "999"]
ADJ_BAD = ["1001", "-1001", "+5", "5x", "--5", "-", "1e3", "5 ", "10000", "-10000", "abc", "5\n", "1 000",
           "0100", "-0100", "01000", "00", "\uff15\uff10\uff10"]   # leading zeros (octal to Linux); other digits
LIMIT_VARS = ("ROMP_CLI_SCOPE_MEMORY_MAX", "ROMP_CLI_SCOPE_MEMORY_HIGH", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX",
              "ROMP_CLI_SCOPE_OOM_SCORE_ADJ")
IGNORED = ("romp-cli-scope: ignored: ROMP_CLI_SCOPE_MEMORY_MAX is not a size (digits with an optional K, M, G or T "
           "suffix, or infinity) — the CLI runs in its scope without it")


def _wrapper_verdicts(fn, values):
    """Run the wrapper's own `fn` (size_ok / adj_ok), lifted verbatim out of bin/romp-cli-scope, over
    `values` under sh: a list of True/False. The script execs, so it cannot be sourced; the function
    bodies are cut from `\nNAME() {` to the first line that is exactly `}`."""
    with open(os.path.join(BIN, "romp-cli-scope")) as f:
        src = f.read()

    def body(name):
        i = src.index("\n%s() {" % name)
        j = src.index("\n}\n", i)
        return src[i:j + 3]
    script = (body("size_ok") + body("adj_ok")
              + '\nfor v in "$@"; do if %s "$v"; then echo ok; else echo bad; fi; done\n' % fn)
    r = subprocess.run(["sh", "-s", "--"] + list(values), input=script, capture_output=True, text=True, timeout=30)
    out = r.stdout.split("\n")[:-1]
    assert r.returncode == 0 and len(out) == len(values), (r.returncode, r.stderr, out)
    return [o == "ok" for o in out]


class LimitRules(unittest.TestCase):
    """cli_scope_limits: the size and adjustment rules, what is in force and what is refused, the log."""

    def _log(self):
        rows = []
        return rows, (lambda m, problem=False: rows.append((m, bool(problem))))

    def test_nothing_set_is_nothing(self):
        rows, log = self._log()
        self.assertEqual(sb.cli_scope_limits({}, log=log), ({}, {}, None, []))
        self.assertEqual(sb.cli_scope_limits({v: "" for v in LIMIT_VARS}, log=log), ({}, {}, None, []),
                         "empty is unset — what the kernel sends down for a refused one")
        self.assertEqual(rows, [])

    def test_every_valid_size_is_in_force_under_its_api_key(self):
        for v in SIZE_OK:
            env = {"ROMP_CLI_SCOPE_MEMORY_MAX": v, "ROMP_CLI_SCOPE_MEMORY_HIGH": v, "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX": v}
            in_force, rejected, delegated, unsettled = sb.cli_scope_limits(env)
            self.assertEqual(in_force, {"memoryMax": v, "memoryHigh": v, "memorySwapMax": v}, v)
            self.assertIsNone(delegated, "no runner, no probe")
            self.assertEqual(rejected, {}, v)

    def test_every_bad_size_is_refused_and_logged_as_a_problem_naming_the_variable_and_the_rule(self):
        for v in SIZE_BAD:
            rows, log = self._log()
            in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_HIGH": v}, log=log)
            self.assertEqual(in_force, {}, v)
            self.assertEqual(rejected, {"ROMP_CLI_SCOPE_MEMORY_HIGH": v}, v)
            self.assertEqual(len(rows), 1, (v, rows))
            m, problem = rows[0]
            self.assertTrue(problem, v)
            self.assertIn("ROMP_CLI_SCOPE_MEMORY_HIGH", m)
            self.assertIn("not a size", m)
            self.assertIn("K, M, G or T", m, "the rule, so the fix is in the line")
            self.assertIn("without that limit", m, "and what happens meanwhile")

    def test_the_adjustment_rule(self):
        for v in ADJ_OK:
            in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": v})
            self.assertEqual((in_force, rejected), ({"oomScoreAdj": v}, {}), v)
        for v in ADJ_BAD:
            rows, log = self._log()
            in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": v}, log=log)
            self.assertEqual((in_force, rejected), ({}, {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": v}), v)
            self.assertIn("-1000..1000", rows[0][0], v)
            self.assertIn("no leading zero", rows[0][0], v)

    def test_a_refused_value_leaves_the_others_in_force(self):
        rows, log = self._log()
        in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "abc", "ROMP_CLI_SCOPE_MEMORY_HIGH": "12G",
                                                     "ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"}, log=log)
        self.assertEqual(in_force, {"memoryHigh": "12G", "oomScoreAdj": "500"})
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"})
        self.assertEqual([p for _m, p in rows], [True, False], "one problem, then the in-force line")
        self.assertIn("in force", rows[1][0])
        self.assertIn("memoryHigh=12G", rows[1][0])
        self.assertIn("oomScoreAdj=500", rows[1][0])

    def test_with_the_scopes_off_the_in_force_line_says_the_limits_apply_to_nothing(self):
        rows, log = self._log()
        in_force, _, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "16G"}, log=log, scope_on=False)
        self.assertEqual(in_force, {"memoryMax": "16G"}, "still read, for the report")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0][1], "not a problem: a setting, idle")
        self.assertIn("apply to nothing", rows[0][0])
        self.assertIn("scopes are off", rows[0][0])

    def test_with_the_scopes_off_a_refused_value_is_still_a_problem_but_the_line_places_no_session_in_a_scope(self):
        # the rule holds either way (a bad value in the environment is worth a problem line), but with
        # the scopes off there is no scope for "sessions run in their scopes without that limit" to be
        # true of, so that clause is left off
        rows, log = self._log()
        in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"}, log=log, scope_on=False)
        self.assertEqual((in_force, rejected), ({}, {"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"}))
        self.assertEqual(len(rows), 1, rows)
        m, problem = rows[0]
        self.assertTrue(problem)
        self.assertIn("not a size", m)
        self.assertIn("not applied", m)
        self.assertNotIn("in their scopes", m)
        rows, log = self._log()
        sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"}, log=log, scope_on=True)
        self.assertIn("sessions run in their scopes without that limit", rows[0][0], "and with them on, it is said")

    def test_no_log_callback_is_fine(self):
        self.assertEqual(sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"})[1], {"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"})

    def test_the_table_names_the_four_variables_once_each(self):
        self.assertEqual(tuple(row[0] for row in sb.CLI_SCOPE_LIMITS), LIMIT_VARS)
        self.assertEqual(len({row[1] for row in sb.CLI_SCOPE_LIMITS}), 4, "distinct api keys")

    def test_the_wrapper_agrees_with_the_kernel_on_every_size(self):
        values = SIZE_OK + SIZE_BAD
        expected = [sb._cli_scope_size_ok(v) for v in values]
        self.assertEqual(expected, [True] * len(SIZE_OK) + [False] * len(SIZE_BAD), "the corpus is what it claims")
        got = _wrapper_verdicts("size_ok", values)
        self.assertEqual(dict(zip(values, got)), dict(zip(values, expected)))

    def test_the_wrapper_agrees_with_the_kernel_on_every_adjustment(self):
        values = ADJ_OK + ADJ_BAD
        expected = [sb._cli_scope_adj_ok(v) for v in values]
        self.assertEqual(expected, [True] * len(ADJ_OK) + [False] * len(ADJ_BAD), "the corpus is what it claims")
        got = _wrapper_verdicts("adj_ok", values)
        self.assertEqual(dict(zip(values, got)), dict(zip(values, expected)))


class LimitsOnTheBackend(_Backend):
    """Read once at construction from the manager's environment; handed down by _options; the wrapper's
    `ignored:` line logged at arrival as a problem naming the session."""

    def _construct(self, **env):
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logged.append)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_the_default_backend_has_no_limits(self):
        self.assertEqual(self.be.cli_scope_limits, {})
        self.assertEqual(self.be.cli_scope_rejected, {})
        self.assertIsNone(self.be.cli_scope_memory_delegated, "no memory limit set, so no probe, so no verdict")

    def test_the_limits_are_read_once_at_construction(self):
        be = self._construct(ROMP_CLI_SCOPE_MEMORY_MAX="16G", ROMP_CLI_SCOPE_MEMORY_HIGH="", ROMP_CLI_SCOPE_OOM_SCORE_ADJ="500")
        self.assertEqual(be.cli_scope_limits, {"memoryMax": "16G", "oomScoreAdj": "500"})
        self.assertEqual(be.cli_scope_rejected, {})
        # the test floor keeps the scope off, so the boot line says the limits are idle
        self.assertTrue(any("apply to nothing" in m for m in self.logged), self.logged)
        # the environment changed after construction: the backend's view does not
        before = os.environ.get("ROMP_CLI_SCOPE_MEMORY_MAX")
        os.environ["ROMP_CLI_SCOPE_MEMORY_MAX"] = "1G"
        try:
            self.assertEqual(be.cli_scope_limits["memoryMax"], "16G")
        finally:
            if before is None:
                os.environ.pop("ROMP_CLI_SCOPE_MEMORY_MAX", None)
            else:
                os.environ["ROMP_CLI_SCOPE_MEMORY_MAX"] = before

    def test_a_refused_value_is_recorded_and_the_rest_stand(self):
        be = self._construct(ROMP_CLI_SCOPE_MEMORY_MAX="lots", ROMP_CLI_SCOPE_MEMORY_SWAP_MAX="0")
        self.assertEqual(be.cli_scope_limits, {"memorySwapMax": "0"})
        self.assertEqual(be.cli_scope_rejected, {"ROMP_CLI_SCOPE_MEMORY_MAX": "lots"})
        self.assertTrue(any("ROMP_CLI_SCOPE_MEMORY_MAX" in m and "not a size" in m for m in self.logged), self.logged)

    def test_options_hands_down_the_set_and_the_refused_variables_and_not_the_unset_ones(self):
        # a vetted value as itself; a refused one EMPTY (it masks the manager environment's bad value,
        # which the SDK's spawn would otherwise inherit); an unset one not at all — there is nothing to
        # mask, and a review found all four names set-empty in every session's tool shell (2026-09-06),
        # where an install with nothing configured must look exactly as it did before the limits existed
        self.be.cli_scope = True
        self.be.cli_scope_limits = {"memoryMax": "16G", "oomScoreAdj": "500"}
        self.be.cli_scope_rejected = {"ROMP_CLI_SCOPE_MEMORY_HIGH": "abc"}
        env = self._kw()["env"]
        self.assertEqual(env["ROMP_CLI_SCOPE_MEMORY_MAX"], "16G")
        self.assertEqual(env["ROMP_CLI_SCOPE_OOM_SCORE_ADJ"], "500")
        self.assertEqual(env["ROMP_CLI_SCOPE_MEMORY_HIGH"], "", "refused: down empty, so the wrapper reads it as unset")
        self.assertNotIn("ROMP_CLI_SCOPE_MEMORY_SWAP_MAX", env, "unset: not sent")
        self.assertEqual(env["ROMP_CLI_REAL"], "/bin/true", "the rest of the overlay is unchanged")

    def test_options_sends_none_of_the_four_when_none_is_set(self):
        # the unset path is what an install with nothing configured runs: identical to the overlay
        # before the limits existed
        self.be.cli_scope = True
        self.assertEqual((self.be.cli_scope_limits, self.be.cli_scope_rejected), ({}, {}))
        env = self._kw()["env"]
        for v in LIMIT_VARS:
            self.assertNotIn(v, env, v)
        self.assertEqual(set(env), {"ROMP_SID", "ROMP_SESSION_NAME", "ROMP_CLI_REAL"} | ({"PATH"} if "PATH" in env else set()),
                         "the overlay carries the identity, the real CLI, and nothing about limits")

    def test_a_value_the_box_refused_goes_down_empty_like_one_the_rule_refused(self):
        # rejected by the boot probe (systemd or the oom_score_adj floor): the manager environment still
        # holds the value, and the wrapper must not try it again on every launch
        self.be.cli_scope = True
        self.be.cli_scope_limits = {"memoryHigh": "12G"}
        self.be.cli_scope_rejected = {"ROMP_CLI_SCOPE_MEMORY_MAX": "16G", "ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "0"}
        env = self._kw()["env"]
        self.assertEqual(env["ROMP_CLI_SCOPE_MEMORY_HIGH"], "12G")
        self.assertEqual(env["ROMP_CLI_SCOPE_MEMORY_MAX"], "")
        self.assertEqual(env["ROMP_CLI_SCOPE_OOM_SCORE_ADJ"], "")
        self.assertFalse("ROMP_CLI_SCOPE_MEMORY_SWAP_MAX" in env, "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX present")

    def test_options_sends_nothing_when_off_or_when_the_wrapper_is_missing(self):
        self.be.cli_scope_limits = {"memoryMax": "16G"}
        self.be.cli_scope = False
        for v in LIMIT_VARS:
            self.assertNotIn(v, self._kw()["env"], v)
        self.be.cli_scope = True
        before = sb.cli_scope_wrapper
        sb.cli_scope_wrapper = lambda: os.path.join(self.d, "no-such-wrapper")
        self.be._log = lambda m, problem=None: None
        try:
            env = self._kw()["env"]
        finally:
            sb.cli_scope_wrapper = before
        for v in LIMIT_VARS:
            self.assertNotIn(v, env, "no wrapper, no scope, nothing for a limit to apply to")

    def test_the_ignored_line_is_logged_at_once_as_a_problem_naming_the_session(self):
        problems = []
        self.be._log = lambda m, problem=None: problems.append((m, problem))
        sess = self._sess()
        sess._on_cli_stderr(IGNORED + "\n")
        self.assertEqual(len(problems), 1, problems)
        m, p = problems[0]
        self.assertTrue(p, "a problem line: the CLI starts, so nothing else would ever read it")
        self.assertIn("web", m)
        self.assertIn(SID[:8], m)
        self.assertIn("without a per-session limit", m)
        self.assertIn(IGNORED, m, "the wrapper's own line, verbatim")
        self.assertEqual(sess.stderr_tail(), IGNORED, "buffered too")

    def test_the_fixture_is_what_the_wrapper_writes(self):
        with open(os.path.join(BIN, "romp-cli-scope")) as f:
            src = f.read()
        self.assertTrue(IGNORED.startswith(sb.CLI_SCOPE_IGNORED_PREFIX + " ROMP_CLI_SCOPE_MEMORY_MAX is not a size"))
        self.assertIn('"romp-cli-scope: ignored: $1 — the CLI runs in its scope without it"', src)
        self.assertIn('is not a size (digits with an optional K, M, G or T suffix, or infinity)', src)

    def test_the_backend_settles_the_limits_against_this_box_when_the_scopes_are_on(self):
        # the wiring end to end: the verdict on, two values set, and subprocess.run answering as a box
        # whose systemd takes the properties, whose user manager lacks the memory controller, and whose
        # oom_score_adj floor is above the value asked for. The scripted runner stands in for
        # subprocess.run for the constructor only, and answers only the probe argvs.
        real_run = subprocess.run
        runs = _Runs((0, b""),                                             # the property probe scope
                     NO,                                                   # no memory.max in its cgroup
                     (1, b"sh: 1: cannot create /proc/self/oom_score_adj: Permission denied\n"),
                     passthrough=real_run)
        saved = sb.cli_scope_supported
        sb.cli_scope_supported = lambda **kw: True
        real_before = os.environ.pop("ROMP_CLI_REAL", None)
        env = {"ROMP_CLI_SCOPE_MEMORY_MAX": "16G", "ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "0"}
        os.environ.update(env)
        sb.subprocess.run = runs
        try:
            be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logged.append)
        finally:
            sb.subprocess.run = real_run
            sb.cli_scope_supported = saved
            for k in env:
                os.environ.pop(k, None)
            os.environ.pop("ROMP_CLI_REAL", None)
            if real_before is not None:
                os.environ["ROMP_CLI_REAL"] = real_before
        self.assertEqual([c[0] for c in runs.calls], ["systemd-run", "systemd-run", "sh"], runs.calls)
        self.assertTrue(be.cli_scope)
        self.assertEqual(be.cli_scope_limits, {"memoryMax": "16G"})
        self.assertEqual(be.cli_scope_rejected, {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "0"})
        self.assertIs(be.cli_scope_memory_delegated, False)
        self.assertEqual(be.cli_scope_unsettled, [], "every due check answered")
        problems = [m for m in self.logged if m.startswith("cli scope:") and ("not delegated" in m or "cannot be written" in m)]
        self.assertEqual(len(problems), 2, self.logged)
        # and _options hands the refused adjustment down empty, the size as itself, the unset two not at all
        sess = sb.SdkSession(be, {"sid": SID, "name": "web", "cwd": self.d, "mode": "acceptEdits"})
        env = be._options(sess, dict)["env"]
        self.assertEqual(env["ROMP_CLI_SCOPE_MEMORY_MAX"], "16G")
        self.assertEqual(env["ROMP_CLI_SCOPE_OOM_SCORE_ADJ"], "")
        self.assertFalse("ROMP_CLI_SCOPE_MEMORY_HIGH" in env, "ROMP_CLI_SCOPE_MEMORY_HIGH present")
        self.assertFalse("ROMP_CLI_SCOPE_MEMORY_SWAP_MAX" in env, "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX present")

    def test_a_boot_probe_that_does_not_settle_leaves_a_null_verdict_and_hands_the_value_down(self):
        # end to end for the deciding property probe raising: nothing rejected, the value handed down as
        # read, the controller verdict None beside the limit and `unsettled` naming the check, the log
        # says so in a plain line, and no line calls the limits in force
        real_run = subprocess.run
        runs = _Runs((1, b"Failed to connect to bus: Connection timed out\n"), (0, b""),
                     subprocess.TimeoutExpired(PROPS_PROBE, 10), passthrough=real_run)
        saved = sb.cli_scope_supported
        sb.cli_scope_supported = lambda **kw: True
        real_before = os.environ.pop("ROMP_CLI_REAL", None)
        os.environ["ROMP_CLI_SCOPE_MEMORY_MAX"] = "16G"
        sb.subprocess.run = runs
        try:
            be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logged.append)
        finally:
            sb.subprocess.run = real_run
            sb.cli_scope_supported = saved
            os.environ.pop("ROMP_CLI_SCOPE_MEMORY_MAX", None)
            os.environ.pop("ROMP_CLI_REAL", None)
            if real_before is not None:
                os.environ["ROMP_CLI_REAL"] = real_before
        self.assertEqual([c[0] for c in runs.calls], ["systemd-run"] * 3, runs.calls)
        self.assertEqual((be.cli_scope_limits, be.cli_scope_rejected, be.cli_scope_memory_delegated, be.cli_scope_unsettled),
                         ({"memoryMax": "16G"}, {}, None, ["memoryLimits"]))
        lines = [m for m in self.logged if m.startswith("cli scope:")]
        self.assertTrue(any("could not be settled" in m and "timed out after 10 seconds" in m for m in lines), lines)
        self.assertTrue(any("not settled" in m and "memoryMax=16G" in m for m in lines), lines)
        self.assertFalse(any("in force" in m for m in lines), lines)
        self.assertEqual([p["text"] for p in be.problems() if p["text"].startswith("cli scope:")], [],
                         "plain lines: the wrapper reports on each launch")
        env = be._options(sb.SdkSession(be, {"sid": SID, "name": "web", "cwd": self.d, "mode": "acceptEdits"}), dict)["env"]
        self.assertEqual(env["ROMP_CLI_SCOPE_MEMORY_MAX"], "16G", "handed down as read")

    def test_with_the_scopes_off_the_backend_runs_no_probe_however_the_limits_read(self):
        real_run = subprocess.run
        runs = _Runs(passthrough=real_run)
        sb.subprocess.run = runs
        try:
            be = self._construct(ROMP_CLI_SCOPE_MEMORY_MAX="16G", ROMP_CLI_SCOPE_OOM_SCORE_ADJ="0")
        finally:
            sb.subprocess.run = real_run
        self.assertFalse(be.cli_scope, "the test floor")
        self.assertEqual([c for c in runs.calls if c[0] in ("systemd-run", "sh")], [], "nothing probed")
        self.assertEqual(be.cli_scope_limits, {"memoryMax": "16G", "oomScoreAdj": "0"}, "read, for the report")
        self.assertIsNone(be.cli_scope_memory_delegated)


class _Runs:
    """A scripted stand-in for subprocess.run: `script` items are (returncode, stderr bytes), or
    (returncode, stderr bytes, stdout bytes), or an exception to raise, consumed one per call; a call
    past the script's end passes (0, b"") with nothing on stdout. As with the real thing, stdout reaches
    the caller only when it asked for a PIPE. Records every (argv, kwargs). With `passthrough`, argvs
    that are not a probe's (systemd-run, sh) go to it."""

    def __init__(self, *script, passthrough=None):
        self.script, self.calls, self.kws, self.passthrough = list(script), [], [], passthrough

    def __call__(self, argv, **kw):
        argv = list(argv)
        if self.passthrough is not None and argv[0] not in ("systemd-run", "sh"):
            return self.passthrough(argv, **kw)
        self.calls.append(argv)
        self.kws.append(kw)
        item = self.script.pop(0) if self.script else (0, b"")
        if isinstance(item, BaseException):
            raise item
        rc, err, out = (tuple(item) + (b"",))[:3]
        return subprocess.CompletedProcess(argv, rc, stdout=out if kw.get("stdout") == subprocess.PIPE else None,
                                           stderr=err)


PROPS = ["-p", "MemoryMax=16G", "-p", "MemorySwapMax=0", "-p", "OOMPolicy=continue"]
PROPS_PROBE = PROBE[:-2] + PROPS + ["--", "true"]
DELEGATION_PROBE = PROBE[:-2] + PROPS + ["--", "sh", "-c", 'test -e "/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.max" '
                                                         '&& echo has-memory-max || echo no-memory-max']
HAS = (0, b"", b"has-memory-max\n")   # the controller probe's answer on a box with the controller…
NO = (0, b"", b"no-memory-max\n")     # …and on a user manager without it: the scope ran, the file is absent
ADJ_PROBE = ["sh", "-c", 'true > /proc/self/oom_score_adj || exit 3; echo "$1" > /proc/self/oom_score_adj', "sh", "500"]
BOTH = {"ROMP_CLI_SCOPE_MEMORY_MAX": "16G", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX": "0", "ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"}


class LimitsSettledAtBoot(unittest.TestCase):
    """cli_scope_limits with a runner: the wrapper's own steps run once at the kernel's start, against
    this box, so a value the syntax passes but the box refuses (OOMPolicy= on scopes before systemd 253;
    an adjustment below the inherited oom_score_adj floor) lands in `rejected` once instead of being
    refused on every launch — one `ignored:` line and one problem each — while the boot log called
    it in force. A user manager without the memory controller, which takes the
    properties and applies nothing, is caught inside a probe scope and reported."""

    def _log(self):
        rows = []
        return rows, (lambda m, problem=False: rows.append((m, bool(problem))))

    def test_a_box_that_takes_everything_probes_three_times_and_refuses_nothing(self):
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, (0, b""))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual(in_force, {"memoryMax": "16G", "memorySwapMax": "0", "oomScoreAdj": "500"})
        self.assertEqual(rejected, {})
        self.assertIs(delegated, True)
        self.assertEqual([p for _m, p in rows], [False], "one line, the in-force one, no problem")
        self.assertIn("in force", rows[0][0])
        for argv, kw in zip(runs.calls, runs.kws):
            self.assertEqual(kw.get("timeout"), sb.CLI_SCOPE_PROBE_TIMEOUT, "every probe is bounded")
            self.assertEqual(kw.get("stdout"), subprocess.PIPE if argv == DELEGATION_PROBE else subprocess.DEVNULL,
                             "only the controller probe's stdout is read: its marker")
            self.assertEqual(kw.get("stderr"), subprocess.PIPE)

    def test_the_property_words_are_the_wrappers_own(self):
        # the probe must start the scope a session would get: run the real wrapper under a fake
        # systemd-run that records its argv and compare the `-p` words of its pre-flight
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "systemd-run")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_LOG"\nexit 0\n')
        os.chmod(fake, 0o755)
        env = dict(os.environ, PATH=d + ":" + os.environ.get("PATH", ""), FAKE_LOG=os.path.join(d, "argv"),
                   ROMP_CLI_REAL="/bin/true", ROMP_SID=SID, ROMP_CLI_SCOPE_MEMORY_MAX="16G",
                   ROMP_CLI_SCOPE_MEMORY_HIGH="12G", ROMP_CLI_SCOPE_MEMORY_SWAP_MAX="0")
        env.pop("ROMP_CLI_SCOPE", None)
        env.pop("ROMP_CLI_SCOPE_OOM_SCORE_ADJ", None)
        r = subprocess.run([os.path.join(BIN, "romp-cli-scope")], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(d, "argv")) as f:
            argv = f.read().split("\n")[:-1]
        words = argv[argv.index("-p"):argv.index("--")]
        self.assertEqual(words, sb._cli_scope_props({"memoryMax": "16G", "memoryHigh": "12G", "memorySwapMax": "0"}))
        self.assertEqual(sb._cli_scope_props({"memorySwapMax": "0", "oomScoreAdj": "500"}),
                         ["-p", "MemorySwapMax=0", "-p", "OOMPolicy=continue"], "the adjustment is no property")
        self.assertEqual(sb._cli_scope_props({"oomScoreAdj": "500"}), [])

    def test_properties_systemd_rejects_twice_land_in_rejected_quoting_the_deciding_failure(self):
        # the wrapper's chain: with the properties (fails), bare (passes), with them again (fails) — the
        # SECOND failure decides and is quoted; the first may have been a passing fault, as here
        rows, log = self._log()
        runs = _Runs((1, b"Failed to connect to bus: Connection timed out\n"),
                     (0, b""),
                     (1, b"Failed to start transient scope unit: Unknown assignment: OOMPolicy=continue\nmore\n"))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, PROBE, PROPS_PROBE, ADJ_PROBE], "no controller check for a rejected scope")
        self.assertEqual(in_force, {"oomScoreAdj": "500"}, "the adjustment stands: it is no systemd property")
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_MEMORY_MAX": "16G", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX": "0"})
        self.assertIsNone(delegated)
        self.assertEqual([p for _m, p in rows], [True, False])
        m = rows[0][0]
        self.assertIn("rejected the per-session memory limits", m)
        self.assertIn("-p MemoryMax=16G -p MemorySwapMax=0 -p OOMPolicy=continue", m, "the words it refused")
        self.assertIn("Unknown assignment", m, "the deciding failure")
        self.assertNotIn("Connection timed out", m, "not the first, which passed on retry")
        self.assertNotIn("more", m.split("Unknown assignment")[1][:40], "systemd-run's first stderr line only")
        self.assertIn("systemd 253", m, "the likely cause, so the fix is in the line")
        self.assertEqual(rows[1][0], "cli scope: per-session limits — oomScoreAdj=500 in force")

    def test_a_rejection_that_does_not_name_oompolicy_gets_no_systemd_253_hint(self):
        # a size the rule passes and systemd refuses (out of range): the line quotes systemd and adds
        # nothing about scope OOMPolicy= support, which is not the cause
        rows, log = self._log()
        runs = _Runs((1, b"Failed to parse MemoryMax=99999999999999999999T: Numerical result out of range\n"),
                     (0, b""),
                     (1, b"Failed to parse MemoryMax=99999999999999999999T: Numerical result out of range\n"))
        _in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "99999999999999999999T"}, log=log, run=runs)
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_MEMORY_MAX": "99999999999999999999T"})
        self.assertIn("Numerical result out of range", rows[0][0])
        self.assertNotIn("253", rows[0][0])

    def test_a_passing_fault_on_the_first_try_costs_nothing(self):
        rows, log = self._log()
        runs = _Runs((1, b"Failed to connect to bus: Connection timed out\n"), (0, b""), (0, b""), HAS, (0, b""))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, PROBE, PROPS_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual(rejected, {})
        self.assertEqual(len(in_force), 3)
        self.assertIs(delegated, True)
        self.assertEqual([p for _m, p in rows], [False])

    def test_a_probe_that_raises_is_a_failed_try(self):
        rows, log = self._log()
        runs = _Runs(subprocess.TimeoutExpired(PROPS_PROBE, 10), (0, b""),
                     (1, b"Failed to start transient scope unit: Unknown assignment: OOMPolicy=continue\n"))
        in_force, rejected, _, _ = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, PROBE, PROPS_PROBE, ADJ_PROBE])
        self.assertEqual(sorted(rejected), ["ROMP_CLI_SCOPE_MEMORY_MAX", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX"])
        self.assertIn("Unknown assignment", rows[0][0])

    def test_a_bare_scope_failing_too_settles_nothing_and_says_so_without_a_problem(self):
        # the bus went away between the scope verdict and here: the wrapper reports per launch (its
        # fallback line, a problem each), so this is one plain line and the values stand as read — and
        # the last line says they are set but not settled, never that they are in force
        rows, log = self._log()
        runs = _Runs((1, b"Failed to connect to bus: No such file or directory\n"),
                     (1, b"Failed to connect to bus: No such file or directory\n"))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, PROBE, ADJ_PROBE])
        self.assertEqual(rejected, {})
        self.assertEqual(len(in_force), 3)
        self.assertIsNone(delegated)
        self.assertEqual([p for _m, p in rows], [False, False])
        self.assertIn("could not be settled", rows[0][0])
        self.assertIn("No such file or directory", rows[0][0])
        self.assertEqual(rows[1][0], "cli scope: per-session limits — memoryMax=16G memorySwapMax=0 set but not settled "
                                     "(the memory-limits probe settled nothing at start, as logged above); oomScoreAdj=500 in force",
                         "the values as read, each under its own verdict: the adjustment's check did answer")
        self.assertEqual(unsettled, ["memoryLimits"])

    def test_a_deciding_probe_that_does_not_answer_settles_nothing_and_says_so(self):
        # the chain's THIRD probe (with the properties, after a bare pass) raises — the 10 s bound, an
        # OSError: one refusal and one non-answer decide nothing. As with a bare failure, one plain line
        # says so, quoting both, the values stand as read (the wrapper reports on each launch: its
        # `ignored:` line if the properties are refused, its fallback line if the bus is away), and the
        # last line does not claim the limits are in force. Before this, the None path logged nothing
        # and the boot log said "in force" (a review finding, 2026-09-06).
        rows, log = self._log()
        runs = _Runs((1, b"Failed to connect to bus: Connection timed out\n"), (0, b""),
                     subprocess.TimeoutExpired(PROPS_PROBE, 10), (0, b""))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, PROBE, PROPS_PROBE, ADJ_PROBE],
                         "no controller check: no scope with the properties is known to start")
        self.assertEqual(rejected, {})
        self.assertEqual(len(in_force), 3)
        self.assertIsNone(delegated)
        self.assertEqual([p for _m, p in rows], [False, False])
        self.assertIn("could not be settled", rows[0][0])
        self.assertIn("timed out after 10 seconds", rows[0][0], "the non-answer, quoted")
        self.assertIn("Connection timed out", rows[0][0], "and the one refusal")
        self.assertIn("wrapper reports on each launch", rows[0][0])
        self.assertIn("memoryMax=16G memorySwapMax=0 set but not settled (the memory-limits probe settled nothing", rows[1][0])
        self.assertIn("; oomScoreAdj=500 in force", rows[1][0])
        self.assertEqual(unsettled, ["memoryLimits"])

    def test_a_controller_probe_scope_that_never_starts_is_unsettled_not_undelegated(self):
        # systemd-run exits 1 both when the scope ran and the file was absent and when the scope never
        # started (a bus fault moments after the property probe's scope did start). The exit status alone
        # cannot tell them apart, so the verdict comes from a marker the command prints (has-memory-max /
        # no-memory-max, exit 0 either way), and a non-zero exit means the scope never ran: retried once,
        # then UNSETTLED — a problem line quoting systemd, the verdict null, never `false` with the
        # DelegateControllers advice. Before this, a transient scope-start failure here read as "not
        # delegated" for the kernel's whole life (a review finding, 2026-09-06).
        rows, log = self._log()
        fault = (1, b"Failed to start transient scope unit: Connection timed out\n")
        runs = _Runs((0, b""), fault, fault, (0, b""))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, DELEGATION_PROBE, ADJ_PROBE], "one retry")
        self.assertEqual(rejected, {})
        self.assertEqual(len(in_force), 3)
        self.assertIsNone(delegated)
        self.assertEqual([p for _m, p in rows], [True, False])
        m = rows[0][0]
        self.assertIn("memory-controller check", m)
        self.assertIn("its probe failed to start its scope (Failed to start transient scope unit: Connection timed out), "
                      "moments after one with the same properties started, and again on the retry", m,
                      "systemd's own words, both tries; the remark on the refusal it is about")
        self.assertNotIn("not delegated", m)
        self.assertNotIn("DelegateControllers", m)
        self.assertIn("memoryMax=16G memorySwapMax=0 set but not settled (the memory-controller check settled nothing", rows[1][0])
        self.assertIn("; oomScoreAdj=500 in force", rows[1][0])
        self.assertEqual(unsettled, ["memoryController"])

    def test_the_controller_verdict_is_the_marker_not_the_exit_status(self):
        # both markers come back with exit 0; only the text differs
        for answer, verdict in ((HAS, True), (NO, False)):
            rows, log = self._log()
            runs = _Runs((0, b""), answer, (0, b""))
            _in_force, rejected, delegated, _unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
            self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, ADJ_PROBE])
            self.assertEqual(rejected, {})
            self.assertIs(delegated, verdict, answer)
            self.assertEqual([p for _m, p in rows], [False] if verdict else [True, False], answer)

    def test_a_passing_fault_on_the_controller_probe_costs_nothing(self):
        rows, log = self._log()
        runs = _Runs((0, b""), (1, b"Failed to start transient scope unit: Connection timed out\n"), HAS, (0, b""))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual((rejected, len(in_force), delegated), ({}, 3, True))
        self.assertEqual([p for _m, p in rows], [False])
        self.assertIn("in force", rows[0][0])

    def test_an_unexpected_answer_from_the_controller_probe_is_unsettled_and_loud(self):
        # exit 0 and neither marker: not a verdict either way, and not something to guess about — nor a
        # passing fault, so no retry (the command ran; what it printed is the contract broken)
        rows, log = self._log()
        odd = (0, b"", b"Running scope as unit: run-r1.scope\n")
        runs = _Runs((0, b""), odd, (0, b""))
        _in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual(rejected, {})
        self.assertIsNone(delegated)
        self.assertEqual([p for _m, p in rows], [True, False])
        self.assertIn("could not be settled", rows[0][0])
        self.assertIn("'Running scope as unit: run-r1.scope'", rows[0][0], "what it printed, quoted")
        self.assertIn("has-memory-max or no-memory-max", rows[0][0])
        self.assertNotIn("after a first try", rows[0][0], "no retry, so no first try to report")
        self.assertIn("memoryMax=16G memorySwapMax=0 set but not settled (the memory-controller check settled nothing", rows[1][0])
        self.assertEqual(unsettled, ["memoryController"])

    def test_a_user_manager_without_the_memory_controller_is_a_problem_and_a_false_verdict(self):
        # systemd took the properties (the first probe passed), and the probe scope's cgroup has no
        # memory.max: the values stay set (systemd holds them; the verdict says whether they apply),
        # the in-force line says they apply to nothing, and the problem line names the check to run
        rows, log = self._log()
        runs = _Runs((0, b""), NO, (0, b""))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual(rejected, {})
        self.assertEqual(len(in_force), 3)
        self.assertIs(delegated, False)
        self.assertEqual([p for _m, p in rows], [True, False])
        self.assertIn("memory controller is not delegated", rows[0][0])
        self.assertIn("applies nothing", rows[0][0])
        self.assertIn("DelegateControllers", rows[0][0])
        self.assertEqual(rows[1][0], "cli scope: per-session limits — memoryMax=16G memorySwapMax=0 set but applied to nothing "
                                     "until the memory controller is delegated to the user manager; oomScoreAdj=500 in force",
                         "the memory limits' verdict and the adjustment's, each its own")
        self.assertEqual(unsettled, [])

    def test_a_controller_check_that_never_answers_is_unsettled_and_a_problem(self):
        # a raise, twice (the retry the scope-start failure gets too): the verdict is null, and the line
        # is a problem — unlike the property and adjustment checks, which the wrapper repeats per launch,
        # nothing reports this one again until the next kernel start
        rows, log = self._log()
        runs = _Runs((0, b""), OSError("boom"), OSError("boom"), (0, b""))
        _in_force, rejected, delegated, _unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual(rejected, {})
        self.assertIsNone(delegated)
        self.assertEqual([p for _m, p in rows], [True, False])
        self.assertIn("could not be settled", rows[0][0])
        self.assertIn("did not answer", rows[0][0])
        self.assertIn("boom", rows[0][0])
        self.assertIn("until the next kernel start", rows[0][0])
        self.assertIn("its probe did not answer (boom), and again on the retry;", rows[0][0])
        self.assertNotIn("failed to start", rows[0][0], "a raise is not a start failure")
        self.assertIn("memoryMax=16G memorySwapMax=0 set but not settled (the memory-controller check settled nothing", rows[1][0])
        self.assertIn("; oomScoreAdj=500 in force", rows[1][0])

    def test_an_adjustment_below_the_floor_lands_in_rejected_and_the_memory_limits_stand(self):
        # exit 1 from the probe is echo's own failure: the file opened and Linux refused the write,
        # which for an in-range value is EACCES, the floor. dash reports it as an I/O error; the text is
        # quoted, and the verdict rides the status
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, (1, b"sh: 1: echo: echo: I/O error\n"))
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(runs.calls, [PROPS_PROBE, DELEGATION_PROBE, ADJ_PROBE])
        self.assertEqual(in_force, {"memoryMax": "16G", "memorySwapMax": "0"})
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"})
        self.assertIs(delegated, True)
        self.assertEqual([p for _m, p in rows], [True, False])
        m = rows[0][0]
        self.assertIn("ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500", m)
        self.assertIn("user manager's own oom_score_adj", m, "the floor, named")
        self.assertIn("privilege", m)
        self.assertIn("the write was refused (sh: 1: echo: echo: I/O error)", m, "the shell's own text, quoted")
        self.assertIn("without it", m)
        self.assertEqual(rows[1][0], "cli scope: per-session limits — memoryMax=16G memorySwapMax=0 in force")

    def test_an_adjustment_file_that_cannot_be_opened_is_rejected_as_that_not_as_the_floor(self):
        # the probe's own exit for a failed open (a read-only /proc in a hardened container): the line
        # quotes the shell and never sends the operator hunting a floor (a review finding, 2026-09-06)
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, (3, b"sh: 1: cannot create /proc/self/oom_score_adj: Read-only file system\n"))
        in_force, rejected, _d, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"})
        self.assertEqual(in_force, {"memoryMax": "16G", "memorySwapMax": "0"})
        self.assertEqual([p for _m, p in rows], [True, False])
        m = rows[0][0]
        self.assertIn("/proc/self/oom_score_adj could not be opened for writing (sh: 1: cannot create "
                      "/proc/self/oom_score_adj: Read-only file system)", m)
        for floor in ("privilege", "floor", "user manager's own"):
            self.assertNotIn(floor, m)
        self.assertEqual(unsettled, [])
        # any other status is reported as the status and the text, with no cause guessed at
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, (2, b"sh: 1: cannot create /proc/self/oom_score_adj: Permission denied\n"))
        _i, rejected, _d, _u = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"})
        self.assertIn("its probe exited 2 (sh: 1: cannot create /proc/self/oom_score_adj: Permission denied)", rows[0][0])
        self.assertNotIn("privilege", rows[0][0], "Permission denied on the OPEN is not the floor")

    def test_an_adjustment_child_killed_by_a_signal_settles_nothing(self):
        # like the controller check: a negative status is no verdict, so the value stands as read and the
        # wrapper reports per launch — never `rejected` on a write that did not happen
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, (-9, b""))
        in_force, rejected, _d, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual((rejected, in_force["oomScoreAdj"], unsettled), ({}, "500", ["oomScoreAdj"]))
        self.assertEqual([p for _m, p in rows], [False, False])
        self.assertEqual(rows[0][0], "cli scope: the oom_score_adj check was killed by signal 9 before it wrote; "
                                     "ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500 stands as read, and the wrapper reports on each launch")
        self.assertIn("oomScoreAdj=500 set but not settled (the oom_score_adj check settled nothing at start", rows[1][0])

    def test_an_adjustment_check_that_cannot_run_leaves_the_value_standing(self):
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, OSError("no sh"))
        in_force, rejected, _, unsettled = sb.cli_scope_limits(BOTH, log=log, run=runs)
        self.assertEqual(rejected, {})
        self.assertEqual(in_force["oomScoreAdj"], "500")
        self.assertEqual([p for _m, p in rows], [False, False])
        self.assertIn("oom_score_adj check could not run", rows[0][0])
        self.assertIn("no sh", rows[0][0])
        # the memory limits' checks answered and the adjustment's did not: the boot line says so of each,
        # never that whether ALL of them apply is unknown (a review finding, 2026-09-06)
        self.assertEqual(rows[1][0], "cli scope: per-session limits — memoryMax=16G memorySwapMax=0 in force; oomScoreAdj=500 "
                                     "set but not settled (the oom_score_adj check settled nothing at start, as logged above)")
        self.assertEqual(unsettled, ["oomScoreAdj"])

    def test_the_adjustment_alone_probes_only_the_write(self):
        rows, log = self._log()
        runs = _Runs()
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits({"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"}, log=log, run=runs)
        self.assertEqual(runs.calls, [ADJ_PROBE], "no scope is started for a limit that is no property")
        self.assertEqual((in_force, rejected, delegated), ({"oomScoreAdj": "500"}, {}, None))

    def test_a_memory_limit_alone_probes_no_write(self):
        runs = _Runs((0, b""), HAS)
        in_force, rejected, delegated, unsettled = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_HIGH": "12G"}, run=runs)
        self.assertEqual(runs.calls, [PROBE[:-2] + ["-p", "MemoryHigh=12G", "-p", "OOMPolicy=continue", "--", "true"],
                                      PROBE[:-2] + ["-p", "MemoryHigh=12G", "-p", "OOMPolicy=continue", "--"] + sb.CLI_SCOPE_MEMORY_PROBE_CMD])
        self.assertEqual((in_force, rejected, delegated), ({"memoryHigh": "12G"}, {}, True))

    def test_no_probe_without_a_runner_with_the_scopes_off_or_with_nothing_set(self):
        runs = _Runs()
        self.assertEqual(sb.cli_scope_limits(BOTH)[2], None)
        self.assertEqual(sb.cli_scope_limits(BOTH, run=runs, scope_on=False)[2], None)
        self.assertEqual(sb.cli_scope_limits({}, run=runs)[2], None)
        self.assertEqual(sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"}, run=runs), ({}, {"ROMP_CLI_SCOPE_MEMORY_MAX": "abc"}, None, []),
                         "a value its rule refused never reaches a probe")
        self.assertEqual(runs.calls, [])

    def test_a_rule_refusal_and_a_box_refusal_share_rejected(self):
        rows, log = self._log()
        runs = _Runs((0, b""), HAS, (1, b"Permission denied\n"))
        in_force, rejected, _, _ = sb.cli_scope_limits({"ROMP_CLI_SCOPE_MEMORY_MAX": "16G", "ROMP_CLI_SCOPE_MEMORY_HIGH": "lots",
                                                     "ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "0"}, log=log, run=runs)
        self.assertEqual(in_force, {"memoryMax": "16G"})
        self.assertEqual(rejected, {"ROMP_CLI_SCOPE_MEMORY_HIGH": "lots", "ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "0"})
        self.assertEqual([p for _m, p in rows], [True, True, False], "two problems, then the in-force line last")
        self.assertIn("in force", rows[2][0])

    def test_the_probe_commands_are_what_the_docs_and_the_wrapper_describe(self):
        # the controller check reads the cgroup v2 path the docs tell a user to read by hand, and the
        # adjustment write is the wrapper's own (`echo` into /proc/self/oom_score_adj), in a child
        self.assertEqual(sb.CLI_SCOPE_MEMORY_PROBE_CMD[:2], ["sh", "-c"])
        self.assertIn('/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.max', sb.CLI_SCOPE_MEMORY_PROBE_CMD[2])
        # the controller command's contract, on this box's own sh (no scope): one of its two markers on
        # stdout and exit 0 either way, so a non-zero exit from systemd-run can only mean the scope never ran
        rc, err, out = sb._cli_scope_probe(subprocess.run, sb.CLI_SCOPE_MEMORY_PROBE_CMD, stdout=True)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn(out, sb.CLI_SCOPE_MEMORY_PROBE_MARKS)
        self.assertEqual(sb.CLI_SCOPE_MEMORY_PROBE_MARKS, {"has-memory-max": True, "no-memory-max": False})
        self.assertEqual(sb.CLI_SCOPE_ADJ_PROBE_CMD,
                         ["sh", "-c", 'true > /proc/self/oom_score_adj || exit 3; echo "$1" > /proc/self/oom_score_adj', "sh"])
        self.assertEqual(sb.CLI_SCOPE_ADJ_PROBE_UNOPENABLE, 3)
        with open(os.path.join(BIN, "romp-cli-scope")) as f:
            src = f.read()
        # the wrapper makes the same two checks, in this order, on its own file
        self.assertIn('apply_adj "$adj" /proc/self/oom_score_adj', src)
        self.assertLess(src.index('{ true > "$2"; } 2>/dev/null'), src.index('{ echo "$1" > "$2"; } 2>/dev/null'))
        # and the write really is refused below the floor / accepted at it, on this box — the child
        # keeps its own /proc/self, so this process's value is untouched either way
        if os.path.exists("/proc/self/oom_score_adj") and os.getuid() != 0:
            with open("/proc/self/oom_score_adj") as f:
                cur = int(f.read().strip())
            if cur > -1000:
                rc, err, _out = sb._cli_scope_probe(subprocess.run, sb.CLI_SCOPE_ADJ_PROBE_CMD + ["-1000"])
                self.assertEqual(rc, 1, "below every floor: the file opens, the write is refused, echo exits 1")
                self.assertNotIn("/proc/self/oom_score_adj", err, "a refused write does not name the file; a failed open does")
            rc, err, out = sb._cli_scope_probe(subprocess.run, sb.CLI_SCOPE_ADJ_PROBE_CMD + [str(cur)])
            self.assertEqual((rc, err, out), (0, "", ""), "the inherited value itself is always writable; stdout unread")
            with open("/proc/self/oom_score_adj") as f:
                self.assertEqual(int(f.read().strip()), cur)
            # a file this user cannot open for writing (pid 1's, root-owned) is the probe's own exit 3,
            # whatever the shell says about it — `true >` failing is not fatal to sh, so `|| exit 3` runs
            unopenable = [w.replace("/proc/self/", "/proc/1/") for w in sb.CLI_SCOPE_ADJ_PROBE_CMD]
            rc, err, _out = sb._cli_scope_probe(subprocess.run, unopenable + [str(cur)])
            self.assertEqual(rc, sb.CLI_SCOPE_ADJ_PROBE_UNOPENABLE, err)
            self.assertIn("/proc/1/oom_score_adj", err, "the shell names the file it could not open")


# ---- every cell of the boot probe's table (2026-09-06, after two review passes) ----

FAULT = (1, b"Failed to start transient scope unit: Connection timed out\n")
FAULT_B = (1, b"Failed to start transient scope unit: Transport endpoint is not connected\n")
BUS_GONE = (1, b"Failed to connect to bus: No such file or directory\n")
REJECT = (1, b"Failed to start transient scope unit: Unknown assignment: OOMPolicy=continue\n")
KILLED = (-9, b"")           # subprocess reports a child killed by a signal as -N: the scope started, its sh was killed
KILLED_SAID = (-9, b"sh: killed\n")
SILENT = (1, b"")            # a non-zero exit with nothing on stderr: not systemd-run's, which always says when it cannot start
SILENT_2 = (2, b"")
ODD = (0, b"", b"Running scope as unit: run-r1.scope\n")
STDERR_MARK = (0, b"has-memory-max\n", b"")   # the marker on the wrong stream: no verdict
OK = (0, b"")
TIMEOUT = lambda: subprocess.TimeoutExpired(PROBE, 10)    # str(): "Command '[…]' timed out after 10 seconds"
RAISE = lambda text: (lambda: OSError(text))
TIMED_OUT = "timed out after 10 seconds"
# the adjustment write, a two-step probe whose exit status says what failed (CLI_SCOPE_ADJ_PROBE_CMD)
FLOOR = (1, b"sh: 1: echo: echo: I/O error\n")                          # the file opened, the write was refused: dash's text
FLOOR_BASH = (1, b"sh: line 1: echo: write error: Permission denied\n")  # the same under bash or busybox as sh
UNOPENABLE = (3, b"sh: 1: cannot create /proc/self/oom_score_adj: Read-only file system\n")   # the probe's own exit 3
ADJ_ODD = (2, b"sh: 1: cannot create /proc/self/oom_score_adj: Permission denied\n")          # a status the probe does not produce
ADJ_KILLED = (-9, b"")
ADJ_KILLED_SAID = (-15, b"sh: terminated\n")
P, B, D, A = PROPS_PROBE, PROBE, DELEGATION_PROBE, ADJ_PROBE
MEM = {"memoryMax": "16G", "memorySwapMax": "0"}
ADJ = {"oomScoreAdj": "500"}
ALL3 = dict(MEM, **ADJ)
MEM_REJECTED = {"ROMP_CLI_SCOPE_MEMORY_MAX": "16G", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX": "0"}
ADJ_REJECTED = {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"}
ADJ_ONLY = {"ROMP_CLI_SCOPE_OOM_SCORE_ADJ": "500"}
# the boot line's clauses (each value under its own verdict; _cli_scope_boot_line), as (words, verdict):
# the line merges neighbours with one verdict, so a box that takes everything reads ALL_IN_FORCE
MEM_WORDS, ADJ_WORDS = "memoryMax=16G memorySwapMax=0", "oomScoreAdj=500"
IN_FORCE = "in force"
NOT_DELEGATED = "set but applied to nothing until the memory controller is delegated to the user manager"
UNSETTLED_BY = lambda check: "set but not settled (the %s settled nothing at start, as logged above)" % sb.CLI_SCOPE_CHECK_NAMES[check]
MEM_IN_FORCE = MEM_WORDS + " " + IN_FORCE
ADJ_IN_FORCE = ADJ_WORDS + " " + IN_FORCE
ALL_IN_FORCE = MEM_WORDS + " " + ADJ_WORDS + " " + IN_FORCE
MEM_UNSETTLED_LIMITS = MEM_WORDS + " " + UNSETTLED_BY("memoryLimits")
MEM_UNSETTLED_CONTROLLER = MEM_WORDS + " " + UNSETTLED_BY("memoryController")
# the problem/plain lines before the boot line: (problem?, substrings present, substrings absent)
NOT_DELEGATED_LINE = (True, ["memory controller is not delegated", "applies nothing", "DelegateControllers"], [])
LIMITS_UNSETTLED_LINE = lambda t1, t2: (False, ["cli scope: the per-session memory limits could not be settled at start — a probe scope "
                                                 "with them failed (%s) and so did one without (%s); the values stand as read, and the "
                                                 "wrapper reports on each launch" % (t1, t2)], [])
LIMITS_RETRY_UNSETTLED_LINE = lambda t1, t3: (False, ["cli scope: the per-session memory limits could not be settled at start — a probe "
                                                       "scope with them failed (%s), one without passed, and the retry with them did not "
                                                       "answer (%s); the values stand as read, and the wrapper reports on each launch" % (t1, t3)], [])
MEM_REJECTED_LINE = (True, ["cli scope: systemd-run rejected the per-session memory limits (%s: Failed to start transient scope unit: "
                            "Unknown assignment: OOMPolicy=continue) — not applied; sessions run in their scopes without them "
                            "(OOMPolicy= on scopes needs systemd 253)" % " ".join(PROPS)], ["Connection timed out", TIMED_OUT])
CONTROLLER_UNSETTLED_LINE = lambda why, absent=(): (True, ["cli scope: the memory-controller check could not be settled — %s; whether the "
                                                           "memory limits (%s) apply is unknown until the next kernel start, and the values "
                                                           "stand as read" % (why, " ".join(PROPS))],
                                                    ["not delegated", "DelegateControllers", "twice"] + list(absent))
ADJ_UNSETTLED_LINE = lambda what: (False, ["cli scope: the oom_score_adj check %s; ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500 stands as read, "
                                           "and the wrapper reports on each launch" % what], [])
ADJ_REJECTED_LINE = lambda why, absent=(): (True, ["cli scope: ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500 cannot be written by this process — %s — "
                                                   "not applied; sessions run in their scopes without it" % why], list(absent))
FLOOR_WHY = lambda quoted: ("the write was refused (%s): a value below the systemd user manager's own oom_score_adj (the floor every "
                            "process under it inherits; 100 on a typical machine) needs a privilege it does not have" % quoted)
NOT_THE_FLOOR = ("privilege", "floor", "user manager's own", "the write was refused")
MOMENTS = ", moments after one with the same properties started"
FAULT_TEXT = "failed to start its scope (Failed to start transient scope unit: Connection timed out)"
FAULT_B_TEXT = "failed to start its scope (Failed to start transient scope unit: Transport endpoint is not connected)"
KILLED_TEXT = "was killed by signal 9 in its scope before it printed a marker"
SILENT_TEXT = ("exited 1 with no marker and nothing on stderr — the scope started (systemd-run says when one cannot) "
               "and its command did not finish the check")
EXPECTED_MARK = " where has-memory-max or no-memory-max was expected"


def _cell(name, script, calls, in_force, rejected, delegated, unsettled, lines, boot, env=None):
    """One row: the probes' answers in order → (argvs run, values handed down, rejected, delegated,
    unsettled, the lines before the boot line, the boot line's body or None)."""
    return dict(name=name, script=script, calls=calls, in_force=in_force, rejected=rejected, delegated=delegated,
                unsettled=unsettled, lines=lines, boot=boot, env=BOTH if env is None else env)


# A few rows with their line written out in full, as the wording's anchors; every other cell comes from
# the axes below (_settle_rows), whose expected texts are assembled by the same rule the kernel follows.
SETTLE_ANCHORS = [
    _cell("controller fault, then silent: the remark rides the start refusal, not the sentence's end",
          [OK, FAULT, SILENT, OK], [P, D, D, A], ALL3, {}, None, ["memoryController"],
          [CONTROLLER_UNSETTLED_LINE("its probe failed to start its scope (Failed to start transient scope unit: Connection timed "
                                     "out), moments after one with the same properties started, and the retry exited 1 with no "
                                     "marker and nothing on stderr — the scope started (systemd-run says when one cannot) and its "
                                     "command did not finish the check", ["again on the retry", "did not answer"])],
          MEM_UNSETTLED_CONTROLLER + "; " + ADJ_IN_FORCE),
    _cell("controller fault, then raise: a start failure then a non-answer (systemd's text kept, the remark on it)",
          [OK, FAULT, TIMEOUT, OK], [P, D, D, A], ALL3, {}, None, ["memoryController"],
          [CONTROLLER_UNSETTLED_LINE("its probe failed to start its scope (Failed to start transient scope unit: Connection timed "
                                     "out), moments after one with the same properties started, and the retry did not answer (%s)"
                                     % str(TIMEOUT()), ["again on the retry"])],
          MEM_UNSETTLED_CONTROLLER + "; " + ADJ_IN_FORCE),
    _cell("controller raise, then fault: a non-answer then a start failure (never 'twice failed to start')",
          [OK, TIMEOUT, FAULT, OK], [P, D, D, A], ALL3, {}, None, ["memoryController"],
          [CONTROLLER_UNSETTLED_LINE("its probe did not answer (%s), and the retry failed to start its scope (Failed to start "
                                     "transient scope unit: Connection timed out), moments after one with the same properties "
                                     "started" % str(TIMEOUT()), ["again on the retry"])],
          MEM_UNSETTLED_CONTROLLER + "; " + ADJ_IN_FORCE),
    _cell("controller fault A, fault B: two start failures, both texts, the remark once",
          [OK, FAULT, FAULT_B, OK], [P, D, D, A], ALL3, {}, None, ["memoryController"],
          [CONTROLLER_UNSETTLED_LINE("its probe failed to start its scope (Failed to start transient scope unit: Connection timed "
                                     "out), moments after one with the same properties started, and the retry failed to start its "
                                     "scope (Failed to start transient scope unit: Transport endpoint is not connected)",
                                     ["again on the retry"])],
          MEM_UNSETTLED_CONTROLLER + "; " + ADJ_IN_FORCE),
    _cell("controller fault, then exit 0 with neither marker: the odd print, and the first try named with its remark",
          [OK, FAULT, ODD, OK], [P, D, D, A], ALL3, {}, None, ["memoryController"],
          [CONTROLLER_UNSETTLED_LINE("its probe printed 'Running scope as unit: run-r1.scope' where has-memory-max or no-memory-max "
                                     "was expected, after a first try that failed to start its scope (Failed to start transient "
                                     "scope unit: Connection timed out), moments after one with the same properties started")],
          MEM_UNSETTLED_CONTROLLER + "; " + ADJ_IN_FORCE),
    _cell("P fail, B ok, P raise: the refusal and the non-answer both quoted",
          [FAULT, OK, RAISE("boom"), OK], [P, B, P, A], ALL3, {}, None, ["memoryLimits"],
          [(False, ["cli scope: the per-session memory limits could not be settled at start — a probe scope with them failed "
                    "(Failed to start transient scope unit: Connection timed out), one without passed, and the retry with them did "
                    "not answer (boom); the values stand as read, and the wrapper reports on each launch"], [])],
          MEM_UNSETTLED_LIMITS + "; " + ADJ_IN_FORCE),
    _cell("adj floor: rejected with the floor named, the shell's text quoted, the memory limits in force",
          [OK, HAS, FLOOR], [P, D, A], MEM, ADJ_REJECTED, True, [],
          [(True, ["cli scope: ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500 cannot be written by this process — the write was refused (sh: 1: "
                   "echo: echo: I/O error): a value below the systemd user manager's own oom_score_adj (the floor every process "
                   "under it inherits; 100 on a typical machine) needs a privilege it does not have — not applied; sessions run in "
                   "their scopes without it"], [])],
          MEM_IN_FORCE),
    _cell("adj unopenable: rejected as a file that would not open, quoted, and never as the floor",
          [OK, HAS, UNOPENABLE], [P, D, A], MEM, ADJ_REJECTED, True, [],
          [(True, ["cli scope: ROMP_CLI_SCOPE_OOM_SCORE_ADJ=500 cannot be written by this process — /proc/self/oom_score_adj could "
                   "not be opened for writing (sh: 1: cannot create /proc/self/oom_score_adj: Read-only file system) — not applied; "
                   "sessions run in their scopes without it"], list(NOT_THE_FLOOR))],
          MEM_IN_FORCE),
    _cell("one memory limit alone, controller raise then fault: one clause, not settled, no adjustment",
          [OK, TIMEOUT, FAULT], [PROBE[:-2] + ["-p", "MemoryMax=16G", "-p", "OOMPolicy=continue", "--", "true"]]
          + [PROBE[:-2] + ["-p", "MemoryMax=16G", "-p", "OOMPolicy=continue", "--"] + sb.CLI_SCOPE_MEMORY_PROBE_CMD] * 2,
          {"memoryMax": "16G"}, {}, None, ["memoryController"],
          [(True, ["cli scope: the memory-controller check could not be settled — its probe did not answer (%s), and the retry "
                   "failed to start its scope (Failed to start transient scope unit: Connection timed out), moments after one with "
                   "the same properties started; whether the memory limits (-p MemoryMax=16G -p OOMPolicy=continue) apply is unknown "
                   "until the next kernel start, and the values stand as read" % str(TIMEOUT())], [])],
          "memoryMax=16G " + UNSETTLED_BY("memoryController"), env={"ROMP_CLI_SCOPE_MEMORY_MAX": "16G"}),
]

# The axes. Each part is what one step of the settle contributes to a row: the probes' answers, the
# argvs those answers consume, the values it leaves in force and rejected, the checks it leaves
# unsettled, the lines it logs, and its clause of the boot line — (words, verdict), None for none.
# ATTEMPTS: every way one controller attempt gives no verdict (_cli_scope_attempt's kinds, with a second
# text for each so the "and again on the retry" / "and the retry …" choice is pinned both ways), as
# (label, scripted answer, the text the line gives it, its kind).
ATTEMPTS = [
    ("fault", FAULT, FAULT_TEXT, "no-start"),
    ("fault B", FAULT_B, FAULT_B_TEXT, "no-start"),
    ("raise", RAISE("boom"), "did not answer (boom)", "no-answer"),
    ("raise B", RAISE("bang"), "did not answer (bang)", "no-answer"),
    ("timeout", TIMEOUT, "did not answer (%s)" % str(TIMEOUT()), "no-answer"),
    ("killed", KILLED, KILLED_TEXT, "no-marker"),
    ("killed, stderr", KILLED_SAID, KILLED_TEXT + " (stderr: sh: killed)", "no-marker"),
    ("silent", SILENT, SILENT_TEXT, "no-marker"),
    ("exit 2", SILENT_2, SILENT_TEXT.replace("exited 1", "exited 2"), "no-marker"),
]
MARKS = [("has", HAS, True, [], (MEM_WORDS, IN_FORCE)), ("no", NO, False, [NOT_DELEGATED_LINE], (MEM_WORDS, NOT_DELEGATED))]
ODDS = [("odd print", ODD, "'Running scope as unit: run-r1.scope'"), ("marker on stderr", STDERR_MARK, "''")]


def _said(attempt, remark=True):
    """An attempt's text in the line: a start refusal carries the remark, once, on the attempt it is about."""
    _label, _answer, text, kind = attempt
    return text + (MOMENTS if remark and kind == "no-start" else "")


def _pair_why(first, second):
    if first[2] == second[2]:
        return "its probe %s, and again on the retry" % _said(first)
    return "its probe %s, and the retry %s" % (_said(first), _said(second, remark=first[3] != "no-start"))


def _part(script=(), calls=(), in_force=None, rejected=None, delegated=None, unsettled=(), lines=(), clause=None):
    return dict(script=list(script), calls=list(calls), in_force=dict(in_force or {}), rejected=dict(rejected or {}),
                delegated=delegated, unsettled=list(unsettled), lines=list(lines), clause=clause)


def _controller_parts():
    """Every outcome of the controller check, after a property probe that passed: (label, part)."""
    settled = [("controller " + label, _part([answer], [D], MEM, delegated=verdict, lines=lines, clause=clause))
               for label, answer, verdict, lines, clause in MARKS]
    no_verdict = lambda script, why, absent=(): _part(script, [D] * len(script), MEM, unsettled=["memoryController"],
                                                      lines=[CONTROLLER_UNSETTLED_LINE(why, absent)],
                                                      clause=(MEM_WORDS, UNSETTLED_BY("memoryController")))
    odd_why = lambda shown: "its probe printed %s%s" % (shown, EXPECTED_MARK)
    parts = list(settled)
    for first in ATTEMPTS:
        # recovered by either marker: a passing fault costs nothing
        for label, answer, verdict, lines, clause in MARKS:
            parts.append(("controller %s, then %s" % (first[0], label),
                          _part([first[1], answer], [D, D], MEM, delegated=verdict, lines=lines, clause=clause)))
        # no verdict on the retry either: what each attempt did, the remark on a start refusal
        for second in ATTEMPTS:
            absent = ([MOMENTS] if "no-start" not in (first[3], second[3]) else []) + (
                ["again on the retry"] if first[2] != second[2] else ["and the retry"])
            parts.append(("controller %s, then %s" % (first[0], second[0]),
                          no_verdict([first[1], second[1]], _pair_why(first, second), absent)))
        # an odd print on the retry: no third try; the first named
        for label, answer, shown in ODDS:
            parts.append(("controller %s, then %s" % (first[0], label),
                          no_verdict([first[1], answer], odd_why(shown) + ", after a first try that " + _said(first))))
    for label, answer, shown in ODDS:   # an odd print first: no retry at all
        parts.append(("controller " + label, no_verdict([answer], odd_why(shown), ["after a first try"])))
    return parts


def _chain_parts():
    """Every outcome of the property chain (P with the properties; B bare on a failure; P again on a bare
    pass), each path that reaches the controller paired with both markers: (label, part)."""
    text = lambda answer: str(answer()) if callable(answer) else answer[1].decode().strip()
    first_tries = [("fail", FAULT), ("raise", TIMEOUT)]
    bare_tries = [("fail", BUS_GONE), ("raise", RAISE("bus gone"))]
    retries = [("ok", OK), ("reject", REJECT), ("raise", RAISE("boom"))]
    limits_unsettled = lambda script, calls, line: _part(script, calls, MEM, unsettled=["memoryLimits"], lines=[line],
                                                         clause=(MEM_WORDS, UNSETTLED_BY("memoryLimits")))
    reaching = [("P ok", _part([OK], [P]))]
    parts = []
    for l1, a1 in first_tries:
        for l2, a2 in bare_tries:
            parts.append(("P %s, B %s" % (l1, l2), limits_unsettled([a1, a2], [P, B], LIMITS_UNSETTLED_LINE(text(a1), text(a2)))))
        for l3, a3 in retries:
            label = "P %s, B ok, P %s" % (l1, l3)
            if l3 == "ok":
                reaching.append((label, _part([a1, OK, a3], [P, B, P])))
            elif l3 == "reject":
                parts.append((label, _part([a1, OK, a3], [P, B, P], rejected=MEM_REJECTED, lines=[MEM_REJECTED_LINE])))
            else:
                parts.append((label, limits_unsettled([a1, OK, a3], [P, B, P], LIMITS_RETRY_UNSETTLED_LINE(text(a1), text(a3)))))
    for label, chain in reaching:
        for mark, answer, verdict, lines, clause in MARKS:
            parts.append(("%s, controller %s" % (label, mark),
                          _part(chain["script"] + [answer], chain["calls"] + [D], MEM, delegated=verdict, lines=lines, clause=clause)))
    return parts


# Every outcome of the adjustment write: (label, part). The verdict rides the probe's exit status —
# 1 is echo's, the file opened and Linux refused the write (the floor); 3 is the probe's own, the
# file would not open; any other status is quoted as it is; no answer or a signal settles nothing.
ADJ_PARTS = [
    ("adj ok", _part([OK], [A], ADJ, clause=(ADJ_WORDS, IN_FORCE))),
    ("adj floor", _part([FLOOR], [A], rejected=ADJ_REJECTED, lines=[ADJ_REJECTED_LINE(FLOOR_WHY("sh: 1: echo: echo: I/O error"))])),
    ("adj floor, bash's text", _part([FLOOR_BASH], [A], rejected=ADJ_REJECTED,
                                     lines=[ADJ_REJECTED_LINE(FLOOR_WHY("sh: line 1: echo: write error: Permission denied"))])),
    ("adj unopenable", _part([UNOPENABLE], [A], rejected=ADJ_REJECTED,
                             lines=[ADJ_REJECTED_LINE("/proc/self/oom_score_adj could not be opened for writing (sh: 1: cannot create "
                                                      "/proc/self/oom_score_adj: Read-only file system)", NOT_THE_FLOOR)])),
    ("adj exit 2", _part([ADJ_ODD], [A], rejected=ADJ_REJECTED,
                         lines=[ADJ_REJECTED_LINE("its probe exited 2 (sh: 1: cannot create /proc/self/oom_score_adj: Permission denied)",
                                                  NOT_THE_FLOOR)])),
    ("adj raise", _part([RAISE("no sh")], [A], ADJ, unsettled=["oomScoreAdj"], lines=[ADJ_UNSETTLED_LINE("could not run (no sh)")],
                        clause=(ADJ_WORDS, UNSETTLED_BY("oomScoreAdj")))),
    ("adj timeout", _part([TIMEOUT], [A], ADJ, unsettled=["oomScoreAdj"], lines=[ADJ_UNSETTLED_LINE("could not run (%s)" % str(TIMEOUT()))],
                          clause=(ADJ_WORDS, UNSETTLED_BY("oomScoreAdj")))),
    ("adj killed", _part([ADJ_KILLED], [A], ADJ, unsettled=["oomScoreAdj"],
                         lines=[ADJ_UNSETTLED_LINE("was killed by signal 9 before it wrote")], clause=(ADJ_WORDS, UNSETTLED_BY("oomScoreAdj")))),
    ("adj killed, stderr", _part([ADJ_KILLED_SAID], [A], ADJ, unsettled=["oomScoreAdj"],
                                 lines=[ADJ_UNSETTLED_LINE("was killed by signal 15 before it wrote (stderr: sh: terminated)")],
                                 clause=(ADJ_WORDS, UNSETTLED_BY("oomScoreAdj")))),
]
# Every memory verdict a boot line can carry beside the adjustment's: (label, part).
MEM_PARTS = [
    ("memory in force", _part([OK, HAS], [P, D], MEM, delegated=True, clause=(MEM_WORDS, IN_FORCE))),
    ("controller no", _part([OK, NO], [P, D], MEM, delegated=False, lines=[NOT_DELEGATED_LINE], clause=(MEM_WORDS, NOT_DELEGATED))),
    ("memory limits unsettled", _part([FAULT, BUS_GONE], [P, B], MEM, unsettled=["memoryLimits"],
                                      lines=[LIMITS_UNSETTLED_LINE("Failed to start transient scope unit: Connection timed out",
                                                                   "Failed to connect to bus: No such file or directory")],
                                      clause=(MEM_WORDS, UNSETTLED_BY("memoryLimits")))),
    ("controller unsettled", _part([OK, FAULT, FAULT], [P, D, D], MEM, unsettled=["memoryController"],
                                   lines=[CONTROLLER_UNSETTLED_LINE("its probe " + FAULT_TEXT + MOMENTS + ", and again on the retry")],
                                   clause=(MEM_WORDS, UNSETTLED_BY("memoryController")))),
    ("memory rejected", _part([FAULT, OK, REJECT], [P, B, P], rejected=MEM_REJECTED, lines=[MEM_REJECTED_LINE])),
    ("no memory limit", _part()),
]


def _compose(name, env, *parts):
    """A row from the parts of one settle, in the order the kernel runs them: memory steps, then the
    adjustment. The boot line merges neighbouring clauses with one verdict, as _cli_scope_boot_line does."""
    script, calls, in_force, rejected, unsettled, lines, clauses = [], [], {}, {}, [], [], []
    delegated = None
    for part in parts:
        script += part["script"]
        calls += part["calls"]
        in_force.update(part["in_force"])
        rejected.update(part["rejected"])
        delegated = part["delegated"] if part["delegated"] is not None else delegated
        unsettled += part["unsettled"]
        lines += part["lines"]
        if part["clause"]:
            words, verdict = part["clause"]
            if clauses and clauses[-1][1] == verdict:
                clauses[-1][0] += " " + words
            else:
                clauses.append([words, verdict])
    boot = "; ".join("%s %s" % (words, verdict) for words, verdict in clauses) or None
    return _cell(name, script, calls, in_force, rejected, delegated, unsettled, lines, boot, env=env)


def _settle_rows():
    """The axes' rows: every chain outcome and every controller outcome beside an adjustment that passes,
    and every adjustment outcome beside every memory verdict."""
    adj_ok = ADJ_PARTS[0][1]
    rows = [_compose(label, BOTH, part, adj_ok) for label, part in _chain_parts()]
    p_ok = _part([OK], [P])
    rows += [_compose(label, BOTH, p_ok, part, adj_ok) for label, part in _controller_parts()]
    for mem_label, mem in MEM_PARTS:
        env = ADJ_ONLY if mem_label == "no memory limit" else BOTH
        rows += [_compose("%s, %s" % (mem_label, adj_label), env, mem, adj) for adj_label, adj in ADJ_PARTS]
    return rows


SETTLE_TABLE = SETTLE_ANCHORS + _settle_rows()


class SettleTable(_Backend):
    """Every cell of the boot probe's table, pinned as one row each (SETTLE_TABLE): the probes' answers
    → the argvs run, the values handed down, `rejected`, the controller verdict, `unsettled`, every log
    line (its kind and, for the lines the settle words, its whole text), and the exact boot line. The
    rows come from the axes (_settle_rows): the property chain's eleven outcomes,
    each path that reaches the controller with both markers; every controller outcome — both markers,
    each no-verdict attempt kind (ATTEMPTS) recovered by either marker, paired with every kind on the
    retry, or followed by either odd print, and each odd print alone; and every adjustment outcome
    (ADJ_PARTS) beside every memory verdict a boot line can carry (MEM_PARTS) — plus SETTLE_ANCHORS, a
    few rows whose line is written out in full. Review found the wording keyed on the controller retry
    alone (a raise then a start failure read as two start failures; a start failure then a raise lost
    systemd's text) and the boot line calling settled values unknown when only the adjustment check did
    not answer; a later pass found the table pinning a third of the controller pairs while claiming every
    cell, the start-refusal remark at the sentence's end (about the retry, whichever attempt it was), and
    the adjustment line naming the floor for every status while dropping the shell's text — so the rows
    are now enumerated rather than listed."""

    def test_every_cell(self):
        seen = set()
        for row in SETTLE_TABLE:
            self.assertNotIn(row["name"], seen, "one row per cell")
            seen.add(row["name"])
            with self.subTest(row["name"]):
                logged = []
                runs = _Runs(*[item() if callable(item) else item for item in row["script"]])
                in_force, rejected, delegated, unsettled = sb.cli_scope_limits(
                    row["env"], log=lambda m, problem=False: logged.append((m, bool(problem))), run=runs)
                self.assertEqual(runs.calls, row["calls"])
                self.assertEqual(runs.script, [], "every scripted answer was consumed")
                self.assertEqual(in_force, row["in_force"])
                self.assertEqual(rejected, row["rejected"])
                self.assertIs(delegated, row["delegated"])
                self.assertEqual(unsettled, row["unsettled"])
                for check in unsettled:
                    self.assertIn(check, sb.CLI_SCOPE_CHECKS)
                lines = logged[:-1] if row["boot"] is not None else logged
                self.assertEqual(len(lines), len(row["lines"]), logged)
                for (m, p), (problem, present, absent) in zip(lines, row["lines"]):
                    self.assertIs(p, problem, m)
                    self.assertTrue(m.startswith("cli scope: "), m)
                    for text in present:
                        self.assertIn(text, m)
                    for text in absent:
                        self.assertNotIn(text, m)
                if row["boot"] is None:
                    self.assertFalse(any("per-session limits" in m for m, _p in logged), logged)
                else:
                    self.assertEqual(logged[-1], ("cli scope: per-session limits — " + row["boot"], False))
                    # the line is true of each value: one whose check did not answer is never "in force",
                    # and one whose check answered is never "not settled"
                    for clause in row["boot"].split("; "):
                        words, verdict = clause.split(" in force") if " in force" in clause else clause.split(" set but ")
                        for word in words.split():
                            key = word.split("=")[0]
                            check_unsettled = (("memoryLimits" in unsettled or "memoryController" in unsettled)
                                               if key in sb.CLI_SCOPE_MEMORY_PROPS else "oomScoreAdj" in unsettled)
                            self.assertEqual("not settled" in clause, check_unsettled, clause)

    def test_the_table_covers_every_check_and_every_attempt_kind(self):
        # every check name occurs as unsettled in some cell, and each way a controller attempt can give
        # no verdict (_cli_scope_attempt) is pinned in some cell
        named = {c for row in SETTLE_TABLE for c in row["unsettled"]}
        self.assertEqual(named, set(sb.CLI_SCOPE_CHECKS))
        self.assertEqual(set(sb.CLI_SCOPE_CHECK_NAMES), set(sb.CLI_SCOPE_CHECKS))
        self.assertEqual(sb._cli_scope_attempt(None, "boom"), ("no-answer", "did not answer (boom)"))
        self.assertEqual(sb._cli_scope_attempt(None, ""), ("no-answer", "did not answer (no detail)"))
        self.assertEqual(sb._cli_scope_attempt(1, "Failed to start transient scope unit: x"),
                         ("no-start", "failed to start its scope (Failed to start transient scope unit: x)"))
        self.assertEqual(sb._cli_scope_attempt(-9, ""), ("no-marker", KILLED_TEXT))
        self.assertEqual(sb._cli_scope_attempt(-15, "sh: terminated"),
                         ("no-marker", "was killed by signal 15 in its scope before it printed a marker (stderr: sh: terminated)"))
        self.assertEqual(sb._cli_scope_attempt(1, ""), ("no-marker", SILENT_TEXT))
        for _label, answer, text, kind in ATTEMPTS:   # the axis agrees with the function it enumerates
            rc, err = (None, str(answer())) if callable(answer) else (answer[0], answer[1].decode().strip())
            self.assertEqual(sb._cli_scope_attempt(rc, err), (kind, text))
        self.assertEqual({kind for *_r, kind in ATTEMPTS}, {"no-start", "no-answer", "no-marker"})
        # the axes are what the docstring says: 11 chain outcomes (3 reaching the controller, × 2 markers), the
        # controller's 2 + 9 × (2 + 9 + 2) + 2, the adjustment's 9 beside the memory's 6, plus the anchors
        self.assertEqual(len(_chain_parts()), 8 + 3 * 2)
        self.assertEqual(len(_controller_parts()), 2 + len(ATTEMPTS) * (2 + len(ATTEMPTS) + 2) + 2)
        self.assertEqual(len(SETTLE_TABLE), len(SETTLE_ANCHORS) + 14 + 121 + len(ADJ_PARTS) * len(MEM_PARTS))
        # every anchor's cell is on an axis too, under the same answers (an exception by its text)
        keyed = lambda row: (tuple(sorted(row["env"].items())), tuple(str(i()) if callable(i) else i for i in row["script"]))
        axis_keys = {keyed(row) for row in _settle_rows()}
        for row in SETTLE_ANCHORS[:-1]:   # the one-limit row's env is on no axis, by design
            self.assertIn(keyed(row), axis_keys, row["name"])


if __name__ == "__main__":
    unittest.main()
