"""Global test isolation (2026-07-07): point XDG_STATE_HOME at a fresh temp dir BEFORE any test module
loads bin/romp-judge or bin/romp-kernel — both resolve their state root at import time. Without this,
any test that skips its own rebind writes into the REAL ~/.local/state/romp (the diary guard's
judge-errors.jsonl lines from legacy-flag fixtures made that visible). conftest.py imports before every
test module, so this is a suite-wide floor; per-class _rebind_state/tempdir isolation still layers on
top exactly as before."""
import atexit
import importlib.util
import os
import re
import shutil
import sys
import tempfile

import pytest
from _pytest._code.code import ReprExceptionInfo, ReprFileLocation, ReprTracebackNative

# Temp-directory hygiene, the child-process half (2026-09-06): every temp path a run creates lives
# under ONE private root, removed when the run ends. tests/__init__.py's mkdtemp hook (the other
# half; its comment has the leak's history) records and removes what THIS process mints through
# tempfile.mkdtemp, but a test's children — kernels, git, `mktemp -d` in a shell — and mkstemp or
# os.mkdir paths are outside its sight (a full run left ~5,600 of those per run at up to ten a
# second). So the process's temp dir is redirected: tempfile.tempdir is set directly (gettempdir()
# caches its first answer, and tests/__init__.py has already called it by the time this runs), and
# TMPDIR is exported so every child inherits the same root. Import-time, not pytest_configure: this
# module's own XDG floor below and every module-level mkdtemp at collection must land inside it.
# The two removals compose without overlap: pytest_sessionfinish runs the hook's sweep, whose scope
# is gettempdir() and so the inside of this root; pytest_unconfigure then removes the root whole
# (whatever the sweep could not see) and tests/__init__.py's romp-tests-state-* dir — the package
# imports first, so that dir and this root were minted before the redirect and are the two things a
# run puts outside the root; the hook recorded both but skips them as outside its scope. Under
# pytest-xdist both hooks run in the controller and in every worker: each imported this file and so
# owns a root of its own (a worker's sits inside the controller's, since it inherits that TMPDIR).
# The atexit registrations are silent fallbacks for a normal exit that skipped the hooks, each a
# no-op on what the other removed; nothing runs after an os._exit (pytest-timeout's thread method
# ends a hung run that way), so a hang leaves two top-level entries in the system temp dir, this
# root and that state dir, both under the romp-tests- prefix.
# The system temp dir — the one the RUN was handed, before any redirect — is recorded once, by the
# first conftest to import: an xdist worker inherits the controller's record along with its TMPDIR
# (setdefault, not an assignment: a worker's own gettempdir() is the controller's root, and
# recording that put the worker's fallback one level deeper than a socket path can bear under a
# long TMPDIR — four socket tests failed at bind under -n 2). A test that must leave the root (an
# AF_UNIX socket path that would not fit sun_path under a nested root) falls back to it, and only
# to it — a literal system path in a `dir=` would bypass the redirect (one did).
os.environ.setdefault("ROMP_TESTS_SYSTEM_TMPDIR", tempfile.gettempdir())
_TMP_ROOT = tempfile.mkdtemp(prefix="romp-tests-")
tempfile.tempdir = _TMP_ROOT
os.environ["TMPDIR"] = _TMP_ROOT
_PACKAGE_STATE_DIR = getattr(sys.modules.get("tests"), "STATE_DIR", None)


def _remove_run_dirs(report=False):
    """Remove the root and the package state dir. A survivor is named on stderr when asked: rmtree
    with ignore_errors swallows a child still writing under the root or a 000-mode directory a test
    left behind, and the run would otherwise end green with the root standing. Only unconfigure
    asks; the atexit fallback stays silent so it neither repeats the notice nor contradicts it."""
    for d in (_TMP_ROOT, _PACKAGE_STATE_DIR):
        if d:
            shutil.rmtree(d, ignore_errors=True)
            if report and os.path.isdir(d):
                print("[tests] not removed at run end: %s" % d, file=sys.stderr)


atexit.register(_remove_run_dirs)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """The in-process half (tests/__init__.py): remove every directory this process made through
    tempfile.mkdtemp inside the root, this module's state root included, when the session ends. Runs
    in the controller and in every xdist worker, since each is its own pytest session, and last, after
    pytest's own runner sessionfinish has performed the deferred teardown an interrupted run leaves
    behind, so nothing is swept from under a fixture still closing. The package's atexit hook does the
    same at interpreter exit; both are idempotent, and pytest_unconfigure below takes the root itself
    afterwards."""
    try:
        from tests import remove_made_dirs
    except Exception:
        return
    remove_made_dirs()


def pytest_unconfigure(config):
    _remove_run_dirs(report=True)


# No test's git reads the developer's configuration (2026-09-06). Fixture repos are built by `git
# init` + `git commit` in temp dirs, and those commands honoured the developer's global config: a
# global core.hooksPath ran their pre-commit hook on every seed commit, an LFS filter would run on
# every checkout, and a credential helper or insteadOf rewrite could reach a real remote
# (tests/test_file_github.py pins its own environment for exactly that reason). CI has no global git
# config, so a test that leans on one is already broken there; this makes every run match.
# GIT_CONFIG_GLOBAL is honoured by git >= 2.32; the identity is synthetic, and it is set rather than
# defaulted so a developer's own GIT_AUTHOR_* cannot leak into fixture commits either. The env
# identity outranks `git config user.*` and `-c user.*`, so a test that must pin a particular author
# exports its own GIT_AUTHOR_* / GIT_COMMITTER_* per call; other config keys still yield to `-c`.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "romp tests"
os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "tests@example.invalid"

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="romp-tests-state-")   # inside the root; the hook records it
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel exports this to its sessions; it outranks the XDG floor


# No test may reach a REAL manager control port (2026-08-27): on a machine running a live romp,
# every shell the manager tree spawns inherits ROMP_MANAGER_PORT, and any test kernel that dials
# "the manager" through the inherited value restarts the ACTUAL deployment — the serve-layer
# restart test's pop-then-restore raced the /restart handler's post-ack env read and took a
# self-hosted instance down mid-suite, repeatedly. POISONED to a dead port, never popped: an
# absent var is the one unsafe state, because _restart_this_kernel treats absent as "no manager"
# but _run_main_update maps absent to the DEFAULT port — the live one — so only a dead value is
# safe against every consumer. Import-time, so collection-time code is floored too.
os.environ["ROMP_MANAGER_PORT"] = "1"

# No test may read the REAL service.env (2026-09-04): sdk_backend.work_api_key now reads the manager
# env file LIVE (kernel/keysource.py) instead of popping os.environ once, so on a machine running a
# live romp every auth test would otherwise resolve the developer's ACTUAL API key — quietly billing
# nothing, but making the key material a test input, putting it one assertion message away from a
# terminal, and making the pinned fixture-key tests pass or fail on whether this box happens to have
# a key configured. Pointed at a path inside the temp state root that is never created, so every read
# is the "no file" case and the startup-pop fallback governs, exactly as before the live source
# existed. Both spellings, because keysource accepts both. Import-time (collection is floored too)
# plus a per-test re-assert below, on the same reasoning as the manager port.
_NO_SERVICE_ENV = os.path.join(os.environ["XDG_STATE_HOME"], "no-such-service.env")
os.environ["ROMP_SERVICE_ENV_FILE"] = _NO_SERVICE_ENV
os.environ["ROMP_SERVICE_ENV"] = _NO_SERVICE_ENV
# No test starts with a KEY SOURCE the developer's shell configured (2026-09-08). Every session shell
# under a romp-managed manager inherits the manager's credentials: ANTHROPIC_API_KEY (the startup key
# sdk_backend.startup_api_key claims), ROMP_API_KEY_CMD and ROMP_API_KEY_REF (the runtime providers
# keysource._env_provider selects straight from the environment when the isolated env file above is
# absent), ROMP_EXPECTED_AUTH (the box-wide auth declaration), the competing token credentials
# sdk_backend.startup_auth_env claims, and 1Password's own names keysource.claim_op_env takes. A
# test that constructs a backend, resolves a key or asks default_auth then reads the DEVELOPER'S
# configuration: 73 tests across eight modules went red on a box running the command key source
# while CI, which exports none of these, stayed green (the manager's full run, 2026-09-08). Popped at
# import so module-level loads see the clean baseline, and re-asserted per test below; a test that
# wants a source sets a synthetic one itself in setUp, which runs after the fixture. The list is the
# code's own constants (tests/test_key_source_floor.py pins it against keysource.SOURCE_VARS,
# keysource.OP_ENV_NAMES / OP_ENV_PREFIX and sdk_backend.AUTH_ENV_NAMES).
KEY_SOURCE_ENV_NAMES = (
    "ANTHROPIC_API_KEY", "ROMP_API_KEY_REF", "ROMP_API_KEY_CMD",          # keysource.SOURCE_VARS
    "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",                     # sdk_backend.AUTH_ENV_NAMES (the rest)
    "ROMP_EXPECTED_AUTH",                                                  # the auth declaration
    "OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT",   # keysource.OP_ENV_NAMES
)
KEY_SOURCE_ENV_PREFIXES = ("OP_SESSION_",)                                # keysource.OP_ENV_PREFIX


def _scrub_key_source_env():
    for name in KEY_SOURCE_ENV_NAMES:
        os.environ.pop(name, None)
    for name in [k for k in os.environ if k.startswith(KEY_SOURCE_ENV_PREFIXES)]:
        os.environ.pop(name, None)


_scrub_key_source_env()
# Every shell under a romp-managed session inherits ROMP_SUPERVISED=1 from the kernel (the service
# unit exports it), and keysource gives that variable authority: a supervised manager reads the env
# file only and ignores a startup key. Twenty-five tests that stage a startup key went red when the
# suite ran from inside a romp session while CI stayed green (review find, 2026-09-05). The floor is
# the unsupervised case; a test that wants supervision sets the variable itself.
os.environ.pop("ROMP_SUPERVISED", None)


def _reset_keysource_state():
    """keysource remembers which path selected which source for the PROCESS (that is the resurrection
    guard); under one pytest process that memory would leak between test modules. Every loaded copy of
    the module (each SourceFileLoader name is its own module object) is reset."""
    import sys
    for name, m in list(sys.modules.items()):
        if "keysource" in name and hasattr(m, "_AUTHORITATIVE_PATHS"):
            m._AUTHORITATIVE_PATHS.clear()
            getattr(m, "_ENV_PROVIDER_PATHS", set()).clear()
            m._CACHE = ((), "")


@pytest.fixture(autouse=True)
def _no_real_service_env():
    """Re-asserted, not defaulted: a module-level write in one test file executes during collection
    and would otherwise hold for the whole run. A test that needs its own env file points the vars at
    a temp path in setUp, which runs AFTER this fixture (pytest fills fixtures in the item's setup
    phase, before TestCase.run calls setUp) — so per-test intent still wins."""
    for var in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV"):
        os.environ[var] = _NO_SERVICE_ENV
    _scrub_key_source_env()
    os.environ.pop("ROMP_SUPERVISED", None)
    _reset_keysource_state()
    yield


@pytest.fixture(autouse=True)
def _dead_manager_port():
    """The import-time poison above covers collection, but a module-level env write in a test file
    ALSO executes during collection — so one module's write (or pop) would otherwise hold for the
    entire run phase, erasing the floor for every test after it. Re-assert per test: no
    module-level write can outlive collection against this."""
    os.environ["ROMP_MANAGER_PORT"] = "1"
    yield


# No test may reach the REAL `claude` CLI (2026-08-12): _judge_claude_bin honors ROMP_CLAUDE_BIN
# first, so this floors every judge call a test forgot to stub at /bin/false — empty stdout, the
# dead-CLI row, byte-for-byte what a claude-less CI runner produces. Found when an unstubbed
# _judge_run in the kernel suite exec'd the live CLI on a dev machine: run alone it made a real
# (billed!) model call and passed; in the full suite the process env's key had already been claimed
# by an sdk-backend construction, the live CLI refused "Not logged in", and the judge-auth latch
# that refusal now correctly feeds floored the synthetic session's cards — 25 stays-in-Working
# tests red locally, green on CI, purely machine-dependent. Tests that assert _judge_claude_bin's
# own resolution pop this var themselves (test_judge.py), as they always had to.
os.environ["ROMP_CLAUDE_BIN"] = "/bin/false"

# No test kernel may fetch the Models API (2026-09-02): the kernel's lazy _sdk() build (_sdk_locked)
# fires the T222 catalog refresh, `_refresh_model_catalog("boot")` — an async GET to
# api.anthropic.com on any credential the process carries: the manager-env key
# sdk_backend.work_api_key claimed, else a bare ANTHROPIC_API_KEY, else an ANTHROPIC_AUTH_TOKEN
# bearer. A DEFENSIVE floor: no test reached the network before this line (checked, not assumed —
# the one in-process _sdk() driver, test_kernel_headless_ops' SdkSingleFlight, runs the refresh
# inside the test process with the module loader mocked, and it stopped only because the mocked
# module's work_api_key handed http.client a credential it rejects before a socket opens), but any
# in-process _sdk() call is one exported key away from a real request no test asserts on, on a key
# the test never chose. The kernel-SPAWNING tests floor it in their subprocess env
# (test_gear_select_matrix, test_ship_reship, test_awaiting_box_sync); this floors every test,
# whatever the developer's shell exports.
# Set, not setdefault: "off" is the only value the switch recognises, so no outer intent is being
# overridden. The catalog suite unsets the var inside its own tests — FetchAndFallback pops it in
# setUp to drive the fetch against a local fake server; StalenessEvent and ModelsRoute set it in
# setUp and pop it in tearDown — leaving it absent for every test after that module in a serial
# run; hence the per-test re-assert below, on the same reasoning as the manager-port one
# (tests/test_model_catalog_floor.py pins both). Those pops still win inside their own tests:
# pytest fills every fixture, autouse included, in the item's setup phase, before runtest hands
# the case to TestCase.run(), which is what calls setUp.
os.environ["ROMP_MODEL_CATALOG"] = "off"


@pytest.fixture(autouse=True)
def _no_model_catalog_fetch():
    os.environ["ROMP_MODEL_CATALOG"] = "off"
    yield


# No test may reach the REAL `systemd-run` (2026-09-05): constructing the SDK backend decides once
# whether to spawn CLIs inside per-session transient scopes (sdk_backend.cli_scope_supported), and
# that verdict defaults to ON under the supervised service — ROMP_SUPERVISED=1 is inherited by every
# tool shell of a session running on a self-hosted romp, so a suite run from one would probe the
# live user manager at every backend construction and route every _options() through the wrapper.
# Floored to the explicit off value; the truth-table tests pass their own environ and are unaffected.
# Per-test re-assert below, on the same reasoning as the manager-port floor. The per-session limits
# (ROMP_CLI_SCOPE_MEMORY_MAX and the others, sdk_backend.CLI_SCOPE_LIMITS) are floored to unset the same
# way: the kernel hands them to every session's CLI, whose tool shells inherit them, so a suite run from a
# session on a self-hosted romp with limits in service.env would see them at every backend construction
# and in every exact argv pin.
os.environ["ROMP_CLI_SCOPE"] = "0"
_CLI_SCOPE_LIMIT_VARS = ("ROMP_CLI_SCOPE_MEMORY_MAX", "ROMP_CLI_SCOPE_MEMORY_HIGH", "ROMP_CLI_SCOPE_MEMORY_SWAP_MAX",
                         "ROMP_CLI_SCOPE_OOM_SCORE_ADJ")
for _v in _CLI_SCOPE_LIMIT_VARS:
    os.environ.pop(_v, None)


@pytest.fixture(autouse=True)
def _no_cli_scope():
    os.environ["ROMP_CLI_SCOPE"] = "0"
    for v in _CLI_SCOPE_LIMIT_VARS:
        os.environ.pop(v, None)
    yield


# No test may reach the machine's REAL tmux server (2026-09-06): keysource.claim_op_env scrubs the tmux
# server's globals the moment romp becomes the op consumer, which any test that configures a reference
# and constructs the SDK backend (or resolves a key) does — and on a developer's box that `tmux
# set-environment -gu` would land on the live server every session runs in. The same private socket
# directory the bats suites use (tests/tmux-private.bash): tmux puts every socket, `-L` ones included,
# under $TMUX_TMPDIR/tmux-<uid>/, and the directory must exist or tmux 3.4 silently falls back to the
# default. No server ever exists there, so a scrub from a test exits with "no server running".
os.environ["TMUX_TMPDIR"] = tempfile.mkdtemp(prefix="romp-tests-tmux-")
os.environ.pop("TMUX", None)
os.environ.pop("ROMP_TMUX_SOCKET", None)


@pytest.fixture(autouse=True)
def _no_live_tmux_server():
    os.environ["TMUX_TMPDIR"] = _TMUX_PRIVATE
    yield


_TMUX_PRIVATE = os.environ["TMUX_TMPDIR"]


@pytest.fixture(autouse=True)
def _stub_place_llm(monkeypatch):
    """Card-first placer floor (2026-07-08): every loaded romp-judge instance gets a no-op place_llm so
    no test can reach a real `claude -p` subprocess through _card_route_subs (a plan test whose mocked
    sub lands on a card with open sub-goals would otherwise fire the real second call). Placer tests
    override jd.place_llm in-body; monkeypatch restores whatever was there after each test."""
    seen = set()
    for m in list(sys.modules.values()):
        for j in (m, getattr(m, "jd", None)):
            if j is not None and id(j) not in seen and getattr(j, "_card_route_subs", None) is not None:
                seen.add(id(j))
                monkeypatch.setattr(j, "place_llm", lambda *a, **k: "")
    yield


# No test report may carry a process-environment VALUE, or a credential-shaped token (2026-09-05). A
# test that renders an env mapping in an assertion (assertNotIn on os.environ, on a _judge_env() copy
# of it, on a launch env) prints the whole mapping when it fails, and on a developer's box that
# mapping holds live credentials. Assertions that test membership and name the key are the fix; this
# hook is the safety net for an assertion still written the other way. Two nets, applied to every
# report's text (the longrepr and the captured-output sections) whatever the outcome, and to
# collection reports:
#   * every value seen in this process's environment, 16 characters or longer, is replaced with one
#     marker, and so is each whitespace-separated chunk of such a value that is 16 characters or
#     longer (pprint renders a value with spaces as adjacent literals on separate lines, so a
#     whole-value replace misses the pieces), and so is each piece of such a value that pytest or
#     unittest left beside a cut (`'<head>...<tail>'`, `[N chars]`: a failed `==` keeps 12 and 13
#     characters of each operand, so most of a 30-character value showed on the assert line and in
#     the short summary, 2026-09-07). Values are noted the moment they are WRITTEN into
#     os.environ (the mutation path is wrapped below: a plain assignment, update, setdefault,
#     os.putenv, os.environb, mock.patch.dict), and sampled at import, around each test and at
#     report time as well, for values that entered by another route (inherited from the parent
#     process, written by a C extension). Exempt, and never when the name is credential-shaped: a
#     path-valued variable by NAME (the shell's, this conftest's own dirs, the interpreter and
#     workspace paths GitHub Actions exports); a variable whose value is public by NAME (the ones
#     GitHub Actions exports to describe the run: the server URLs, the sha, the ref, the workflow
#     and job names, the repository and the actor, each of which a CI failure report was showing as
#     the marker, 2026-09-07; and the synthetic git identity this conftest sets at import); a name
#     family that is never a credential (XDG_*, and pytest's own PYTEST_*: PYTEST_CURRENT_TEST holds
#     the running test's node id and is written for every phase of every test, so noting it grew the
#     set by one value per test, slowed every report's scrub in step and made the node id of every
#     test already run a target in later reports); and any value that IS a path this machine has
#     (one absolute path that exists, or a PATH-style list of them), because a traceback quotes the
#     interpreter's prefix on every frame and a developer's shell names it under any variable (a
#     pyenv root, a conda prefix).
#   * credential-shaped tokens by PATTERN (tests/credential_patterns.py: the public key prefixes, and
#     a long token in a value position), whatever their provenance: a token that never touched the
#     environment (read from a file, printed by a child) is caught by this one.
# A report the hook leaves alone keeps pytest's own object and rendering. One it changes is rebuilt
# from the scrubbed text as a native-style traceback with its crash location kept (its message
# scrubbed too), so the short test summary still ends in the assertion message, junitxml keeps its
# message and xdist carries it to the controller; that report loses colour and source highlighting,
# nothing else (_redacted_longrepr).
ENV_VALUE_MIN_LEN = 16
ENV_VALUE_REDACTED = "[REDACTED-ENV-VALUE]"
_ENV_VALUE_PATH_NAMES = frozenset((
    "PWD", "OLDPWD", "HOME", "PATH", "TMPDIR", "SHELL", "VIRTUAL_ENV", "PYTHONPATH", "LS_COLORS",
    "ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV", "ROMP_DIR", "ROMP_STATE_DIR", "ROMP_CLAUDE_BIN",
    "ROMP_SYSTEMD_DIR", "ROMP_LAUNCHD_DIR", "CLAUDE_CONFIG_DIR", "TMUX_TMPDIR", "ROMP_TESTS_SYSTEM_TMPDIR",
    # GitHub Actions: the runner's workspace and tool cache, and the interpreter prefix setup-python
    # exports under six names (every stdlib and site-packages frame of a CI traceback is under it)
    "GITHUB_WORKSPACE", "RUNNER_WORKSPACE", "RUNNER_TEMP", "RUNNER_TOOL_CACHE", "pythonLocation",
    "Python_ROOT_DIR", "Python2_ROOT_DIR", "Python3_ROOT_DIR", "LD_LIBRARY_PATH", "PKG_CONFIG_PATH"))
# Public by name, so never a value a report must hide. GitHub Actions describes the run in these (its
# secrets are GITHUB_TOKEN, ACTIONS_RUNTIME_TOKEN and ACTIONS_ID_TOKEN_REQUEST_TOKEN, credential-shaped
# names this set is never consulted for); without them a CI failure read `assert '[REDACTED-ENV-VALUE]'
# == 'x'` where a test compared the ref, the repository or the actor. Listed by name rather than by the
# GITHUB_ prefix so that a token GitHub adds under a name this list does not know still qualifies. The
# GIT_* names are the synthetic identity this conftest writes at import (`romp tests`,
# `tests@example.invalid`), under which every fixture commit is made.
_ENV_VALUE_PUBLIC_NAMES = frozenset((
    "GITHUB_SERVER_URL", "GITHUB_API_URL", "GITHUB_GRAPHQL_URL", "GITHUB_SHA", "GITHUB_REF", "GITHUB_REF_NAME",
    "GITHUB_HEAD_REF", "GITHUB_BASE_REF", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW", "GITHUB_WORKFLOW_REF",
    "GITHUB_WORKFLOW_SHA", "GITHUB_JOB", "GITHUB_ACTION", "GITHUB_ACTION_REF", "GITHUB_ACTION_REPOSITORY",
    "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER", "GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR", "RUNNER_NAME",
    "RUNNER_ARCH",
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"))
_ENV_VALUE_EXEMPT_PREFIXES = ("XDG_", "PYTEST_")   # never a credential: the XDG base dirs, pytest's bookkeeping
_ENV_VALUES_SEEN: set = set()


def _load_credential_patterns():
    """tests/credential_patterns.py, by path beside this file (a subprocess run against a copy of the
    conftest carries a copy of it too). A missing module is an error, never a silent net less."""
    p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "credential_patterns.py")
    spec = importlib.util.spec_from_file_location("romp_tests_credential_patterns", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_credpat = _load_credential_patterns()
CREDENTIAL_REDACTED = _credpat.REDACTED


def _credential_shaped(name: str) -> bool:
    n = name.upper()
    return (n == "ANTHROPIC_API_KEY" or n.endswith("_API_KEY") or n.endswith("_TOKEN") or n.startswith("ANTHROPIC_")
            or "SECRET" in n or "PASSWORD" in n or "APIKEY" in n)


def _is_existing_path(value: str) -> bool:
    """Whether `value` is a path this machine has: one absolute path that exists, or a PATH-style list
    of them (every non-empty os.pathsep chunk). No token is an absolute path that exists, so this
    exempts no token; a path that does not exist is a value like any other."""
    chunks = [c for c in value.split(os.pathsep) if c]
    return bool(chunks) and all(os.path.isabs(c) and os.path.exists(c) for c in chunks)


def env_value_qualifies(name: str, value: str) -> bool:
    """Whether one environment entry's value is one a report must not show: ENV_VALUE_MIN_LEN
    characters or more, unless the name is exempt (a path-valued variable by name, a variable whose
    value is public by name, or a name family that is never a credential) or the value is a path this
    machine has; a credential-shaped name is never exempt. The one rule, for the sampler and for the
    write hook."""
    if len(value) < ENV_VALUE_MIN_LEN:
        return False
    if _credential_shaped(name):
        return True
    if name in _ENV_VALUE_PATH_NAMES or name in _ENV_VALUE_PUBLIC_NAMES or name.startswith(_ENV_VALUE_EXEMPT_PREFIXES):
        return False
    return not _is_existing_path(value)


def env_values_to_redact(environ=None) -> set:
    """The values of `environ` (the process environment by default) a report must not show."""
    env = os.environ if environ is None else environ
    return {value for name, value in env.items() if env_value_qualifies(name, value)}


def note_env_value(name, value) -> bool:
    """Note one value at the moment it is written into the environment (the hook below). Bytes
    (os.environb, os.putenv) are decoded the way os.environ decodes them. Returns whether the value
    qualified; anything that is not a name and a string value does not."""
    if isinstance(name, bytes):
        name = os.fsdecode(name)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(name, str) or not isinstance(value, str):
        return False
    if not env_value_qualifies(name, value):
        return False
    _ENV_VALUES_SEEN.add(value)
    return True


def _install_env_write_hook() -> None:
    """Wrap the one method every os.environ write goes through and os.putenv beside it. A plain
    assignment, update, setdefault, mock.patch.dict (an update) and os.environb all reach
    _Environ.__setitem__; os.putenv is the module global that method calls and the path that writes
    to the process without touching the mapping. Idempotent: a second import stacks no wrapper."""
    if getattr(os._Environ.__setitem__, "_romp_notes_values", False):
        return
    orig_setitem = os._Environ.__setitem__
    orig_putenv = os.putenv

    def setitem(self, key, value):
        note_env_value(key, value)
        return orig_setitem(self, key, value)

    def putenv(key, value):
        note_env_value(key, value)
        return orig_putenv(key, value)

    setitem._romp_notes_values = putenv._romp_notes_values = True
    os._Environ.__setitem__ = setitem
    os.putenv = putenv


_install_env_write_hook()


# A piece of a value beside a cut pytest or unittest made: a maximal run of ENV_CUT_FRAGMENT_MIN_LEN or
# more token characters that abuts `...` or `[N chars]` on at least one side (the marker before it, the
# marker after it, or a quote on one side and the marker on the other). pytest renders a failed `==` at
# default verbosity with each operand cut to 12 and 13 characters around `...` (`'abcdefghijkl...rstuvwxyzabcd'`
# for a 30-character value), its saferepr of a local or a `+  where` operand keeps 117 on each side,
# the short summary cuts the message at the terminal's width with `...` appended, a long explanation
# is cut at 640 characters the same way, and unittest shortens a container repr with `[N chars]`; none
# of those pieces is the whole value or a whitespace chunk of it, so the replace above left them
# standing (2026-09-07). A candidate is replaced only when it is a substring of a noted value: exact,
# never a guess from its shape (the pattern net's fragment rule does that for tokens of no known
# provenance). Two alternatives so each maximal run is tried once from its start, which keeps the pass
# linear on a long run that reaches no cut.
ENV_CUT_FRAGMENT_MIN_LEN = 8
_ENV_CUT_FRAG_RE = re.compile(
    r"(?:(?<=\.\.\.)|(?<=chars\]))[A-Za-z0-9_\-]{%d,}"                                  # after a cut
    r"|(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{%d,}(?=\.\.\.|\[\d+ chars\])"                    # before one
    % (ENV_CUT_FRAGMENT_MIN_LEN, ENV_CUT_FRAGMENT_MIN_LEN))


def redact_env_values(text: str, values) -> str:
    """`text` with every occurrence of every value replaced by ENV_VALUE_REDACTED, longest first (a
    value that contains another is replaced whole), then every whitespace-separated chunk of a
    value that is ENV_VALUE_MIN_LEN characters or more (pprint renders a long value with spaces as
    adjacent string literals on separate lines, so the token half of `Authorization: Bearer <token>`
    survived a whole-value replace, and unittest's shortened repr shows a differing tail on its own),
    and then every piece of a value left beside a cut (_ENV_CUT_FRAG_RE: a run of token characters
    against `...` or `[N chars]` that is a substring of a value)."""
    parts = set()
    for v in values:
        if not v:
            continue
        parts.add(v)
        chunks = v.split()
        if len(chunks) > 1:
            parts.update(c for c in chunks if len(c) >= ENV_VALUE_MIN_LEN)
    for v in sorted(parts, key=len, reverse=True):
        text = text.replace(v, ENV_VALUE_REDACTED)
    if not parts:
        return text

    def cut_piece(m):
        frag = m.group(0)
        return ENV_VALUE_REDACTED if any(frag in v for v in parts) else frag
    return _ENV_CUT_FRAG_RE.sub(cut_piece, text)


def redact_credential_tokens(text):
    """The pattern net: credential-shaped tokens, whatever their provenance (tests/credential_patterns.py)."""
    return _credpat.scrub(text)


def redact_report_text(text: str, values=None) -> str:
    """Both nets over one report string: the environment's values (and their chunks), then the
    credential-shaped tokens. `values` defaults to everything noted so far."""
    return redact_credential_tokens(redact_env_values(text, _ENV_VALUES_SEEN if values is None else values))


def _note_env_values():
    _ENV_VALUES_SEEN.update(env_values_to_redact())


_note_env_values()


@pytest.fixture(autouse=True)
def _remember_env_values():
    """The sampling half. A value a test writes itself is noted at the write (note_env_value), so one
    present only between these samples is redacted too; the samples at every test's setup and
    teardown, and at report time, are for values that entered the environment by a route the write
    hook does not see (inherited from the parent process before this file loaded, written by a C
    extension)."""
    _note_env_values()
    yield
    _note_env_values()


def _redact_crash_message(message: str) -> str:
    """A crash message scrubbed as the report body renders it. pytest writes the message's lines under
    the `E` marker (`E   ` + line), and the pattern net's rules for a failed comparison's diff lines
    and quoted elements are keyed on that marker; the bare message (`  - <token>` after the diff's
    header) is a rendering the rules do not know, so it is scrubbed marked and unwrapped. Under CI
    pytest prints the whole message, every line, in the short test summary."""
    marked = "\n".join("E   " + line for line in message.split("\n"))
    return "\n".join(line[4:] if line.startswith("E   ") else line
                     for line in redact_report_text(marked).split("\n"))


def _redacted_longrepr(lr, text: str):
    """The scrubbed `text` of a longrepr as a longrepr again. A failure's keeps its crash location
    (pytest's own ReprFileLocation, the message scrubbed too) over a native-style traceback whose one
    entry is the text: the short test summary ends in reprcrash.message, junitxml's message attribute
    reads it, and xdist serializes a longrepr with a traceback and a crash structurally, where a plain
    str showed the traceback's first line in the summary instead (`def test_x():`, or `self = <Case
    testMethod=...>`). pytest renders a native entry as is, so that report loses colour and source
    highlighting and nothing else. A longrepr with no crash location (a collection error's) becomes
    the plain text."""
    crash = getattr(lr, "reprcrash", None)
    if crash is None:
        return text
    return ReprExceptionInfo(reprtraceback=ReprTracebackNative([text + "\n"]),
                             reprcrash=ReprFileLocation(crash.path, crash.lineno, _redact_crash_message(crash.message)))


def _redact_report(rep) -> None:
    """Every text a report carries, whatever its outcome: the longrepr (a failure's text; a skip's is
    a (path, line, reason) tuple, whose reason is the text) and the captured-output sections (which
    -rA and -rP print for passed tests too). A longrepr the nets leave unchanged keeps pytest's own
    object and rendering; one they change is rebuilt by _redacted_longrepr."""
    _note_env_values()
    lr = getattr(rep, "longrepr", None)
    if isinstance(lr, tuple) and len(lr) == 3 and isinstance(lr[2], str):
        red = redact_report_text(lr[2])
        if red != lr[2]:
            rep.longrepr = (lr[0], lr[1], red)
    elif lr is not None:
        text = str(lr)
        red = redact_report_text(text)
        if red != text:
            rep.longrepr = _redacted_longrepr(lr, red)
    if getattr(rep, "sections", None):
        rep.sections = [(name, redact_report_text(content)) for name, content in rep.sections]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    _redact_report(outcome.get_result())


@pytest.hookimpl(hookwrapper=True)
def pytest_collectreport(report):
    """A collection error (an import-time exception whose message names a value) is a report too.
    Redacted BEFORE the other implementations see it: the terminal reporter files it from here."""
    _redact_report(report)
    yield
