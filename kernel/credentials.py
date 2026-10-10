#!/usr/bin/env python3
"""romp holds no API key for its sessions. This module is the whole of romp's contact with API credentials
since 2026-09-08 (the user, after a contributor PR's test printed a key from a session's environment: remove
whatever let a less secure key path exist, and keep only the most secure one). One exception since
2026-10-05: the kernel holds the key for its own judge children (HeldKey, at the bottom), because one
helper run per judge call exhausted the operator's secret-manager quota:

- Sessions authenticate through Claude Code's OWN credential resolution, and so do judge children except
  a key-billed one on a box whose helper sits in the user's settings, which gets the kernel's HeldKey in
  its environment with its own helper turned off. The user
  configures `apiKeyHelper` in Claude Code's settings; the CLI runs it and holds the key. A login-billed
  launch gets `"apiKeyHelper": ""` in its per-session settings layer (the SDK's settings option, the
  CLI's --settings), which disables the helper for that one process: verified on Claude Code 2.1.257
  with a marker-writing fixture helper, the --settings layer outranks the settings files for the same
  key, and the empty string is the value that disables it (null falls through to the files).
- The kernel's own two API calls (the model catalog, the fast-mode org probe) read the same
  `apiKeyHelper` from the same settings files, in Claude Code's own precedence, and run it in-process
  (helper_key). The value lives in process memory for the helper's TTL and goes to the one request that
  asked; it is never written to an environment variable, a file or a log line.
- The retired providers (a ROMP_API_KEY_CMD command, a ROMP_API_KEY_REF 1Password reference, an
  ANTHROPIC_API_KEY= line in service.env, the service.env.source marker) and a key in the kernel's own
  environment are a boot FAILURE with migration text (check_boot_environment): a key romp holds is a key
  a session can print.

stdlib only, loaded by path as `romp_credentials` from the kernel, the SDK backend and the judges."""
import json
import os
import subprocess
import sys
import threading
import time

# The provider variables romp used to read. Any of them in service.env or in the kernel's environment
# stops the kernel at boot (check_boot_environment); the names are the whole of what the check ever says.
RETIRED_VARS = ("ROMP_API_KEY_CMD", "ROMP_API_KEY_REF", "ANTHROPIC_API_KEY")
RETIRED_MARKER_SUFFIX = ".source"           # service.env.source: the retired provider marker beside the file
# Login credentials the kernel claims out of its own environment at startup and hands back to a
# login-billed launch only (sdk_backend.startup_auth_env). Not key material; unchanged by the retirement.
LOGIN_TOKEN_VARS = ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
# The 1Password CLI's own names. romp no longer runs `op`, so it neither claims nor forwards them; the
# judges still keep them out of their children (a service-account token reads every field the account
# can see), and the test floor keeps them out of every test's environment.
OP_ENV_NAMES = ("OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT")
OP_ENV_PREFIX = "OP_SESSION_"
# What tests/conftest.py pops out of every test's environment before any romp module loads, pinned by
# tests/test_key_source_floor.py: no test may see a real credential or a real declaration.
FLOOR_ENV_NAMES = RETIRED_VARS + LOGIN_TOKEN_VARS + ("ROMP_EXPECTED_AUTH",) + OP_ENV_NAMES
FLOOR_ENV_PREFIXES = (OP_ENV_PREFIX,)

HELPER_KEY = "apiKeyHelper"
HELPER_TTL_VAR = "CLAUDE_CODE_API_KEY_HELPER_TTL_MS"      # the CLI's own refresh interval, in milliseconds
HELPER_TTL_DEFAULT_MS = 300_000                          # five minutes: the CLI's documented default
HELPER_TIMEOUT_S = 15
# The environment the helper runs with, and nothing more (a whitelist, never a copy: the kernel's
# environment carries the serve token, full control of every session, which no third-party script may
# see). PATH to run, HOME and CLAUDE_CONFIG_DIR and the XDG names to find its own config, the rest for
# ordinary command-line behaviour. A helper that needs a credential of its own reads it from a file.
HELPER_ENV_PASSTHROUGH = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TERM",
                          "CLAUDE_CONFIG_DIR")
HELPER_ENV_PREFIXES = ("LC_", "XDG_")
# Why a box cannot bill one side of the per-session pick, as ONE sentence each: the Billing menu's greyed
# option (its hover), set_auth's refusal, the launch's fall-to-the-other-side notice and the picker's
# reply all say the same words (the user 2026-09-08: the picker never disappears, the unavailable
# option is greyed with the reason). Shared here because the kernel, the SDK backend and the judges each
# load this module and none imports another.
WHY_NO_LOGIN = "no Claude login signed in on this machine"
WHY_NO_HELPER = "no apiKeyHelper configured"
WHY_MANAGED_HELPER = "the apiKeyHelper is set in managed settings, login cannot apply"
# Claude Code's managed settings, the top of its precedence: one path per platform (its documentation).
MANAGED_SETTINGS = {"darwin": "/Library/Application Support/ClaudeCode/managed-settings.json"}
MANAGED_SETTINGS_DEFAULT = "/etc/claude-code/managed-settings.json"


class CredentialError(Exception):
    """A helper that did not produce a key, or a settings file that could not be read, said in STATIC
    words plus at most a file path: no helper output ever rides the message (stderr is discarded, stdout
    is the key), so quoting one names no value."""


# ---------------------------------------------------------------------------
# The service environment file (ROMP_EXPECTED_AUTH and service knobs; never a key)
# ---------------------------------------------------------------------------

def service_env_path() -> str:
    """The path of the env file the manager is configured from. `ROMP_SERVICE_ENV_FILE` is the name the
    installer and the macOS launcher use (`bin/romp-service`, `bin/romp-node-launch`), so it is the
    primary; `ROMP_SERVICE_ENV` is accepted as an alias. Default `${XDG_CONFIG_HOME:-~/.config}/romp/
    service.env`, the same expression those two scripts compute, so all three always name one file."""
    for var in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV"):
        p = (os.environ.get(var) or "").strip()
        if p:
            return os.path.expanduser(p)
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "romp", "service.env")


def env_file_assignments(text, names=None) -> dict:
    """The literal NAME=VALUE assignments in an env file's text for the given names (every name when None):
    the last assignment wins, one layer of matching quotes is stripped (systemd strips one too), comments
    and blank lines are skipped. Never sourced, never expanded."""
    out = {}
    want = set(names) if names is not None else None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not name or (want is not None and name not in want):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[name] = value
    return out


def is_op_env_name(name) -> bool:
    """The 1Password CLI's own credential names, the ones the retired reference kind read: romp no longer
    runs `op`, so they are refused at boot like the provider variables (a token beside a retired line was
    the documented shape, and a token left in the manager's environment would ride into every session)."""
    return name in OP_ENV_NAMES or str(name).startswith(OP_ENV_PREFIX)


def retired_in_env_file(path=None) -> list:
    """The retired NAMES a service.env still assigns (an empty value counts: the line is what the migration
    removes): the provider variables first, then the 1Password CLI's names. [] for a missing file. An
    unreadable file is a boot failure in its own words."""
    p = path or service_env_path()
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise RuntimeError("%s: cannot read the service environment file (%s). romp did NOT start. Fix the "
                           "file's permissions, then start again." % (p, e.__class__.__name__))
    found = env_file_assignments(text)
    return [n for n in RETIRED_VARS if n in found] + sorted(n for n in found if is_op_env_name(n))


def check_boot_environment(path=None, environ=None) -> None:
    """Stop the kernel BEFORE anything is spawned when a retired key path is still configured: a retired
    variable in service.env, the provider marker beside it, or a retired variable in the kernel's own
    environment (the SDK's transport hands every session this process's environment, so a key there
    would bill every session and could be printed by any of them). RuntimeError in the shape the
    serve-token read uses (kernel._serve_token_read_or_mint): what was found, what romp did NOT do, the
    fix, then start again. Variable NAMES and paths only, never a value."""
    p = path or service_env_path()
    env = os.environ if environ is None else environ
    in_file = retired_in_env_file(p)
    marker = p + RETIRED_MARKER_SUFFIX
    has_marker = os.path.exists(marker)
    in_env = [n for n in RETIRED_VARS if n in env] + sorted(n for n in env if is_op_env_name(n))
    if not (in_file or has_marker or in_env):
        return
    found = []
    if in_file:
        found.append("%s carries %s" % (p, ", ".join(in_file)))
    if has_marker:
        found.append("%s (the retired provider marker) exists" % marker)
    if in_env:
        found.append("the manager's environment carries %s" % ", ".join(in_env))
    op_line = ("; the 1Password CLI's names (OP_SERVICE_ACCOUNT_TOKEN and the rest) go too, since romp no longer "
               "runs op, and a helper that needs that token reads it from a file of its own"
               if any(is_op_env_name(n) for n in in_file + in_env) else "")
    several = (len(in_file) + len(in_env) + int(has_marker)) > 1
    raise RuntimeError(
        "%s. romp no longer holds an API key (a key romp holds is a key a session can print), so romp did NOT "
        "start. Remove %s%s, configure apiKeyHelper in Claude Code's settings (%s: {\"apiKeyHelper\": \"<a script "
        "that prints the key>\"}), declare the billing with ROMP_EXPECTED_AUTH=key in %s, then start again."
        % ("; ".join(found), "them" if several else "it", op_line, os.path.join(claude_config_dir(), "settings.json"), p))


# ---------------------------------------------------------------------------
# Claude Code's settings and its apiKeyHelper
# ---------------------------------------------------------------------------

def claude_config_dir() -> str:
    """Where Claude Code keeps its user settings: $CLAUDE_CONFIG_DIR, else ~/.claude (its documentation)."""
    return os.path.expanduser((os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or "~/.claude")


def managed_settings_path() -> str:
    return MANAGED_SETTINGS.get(sys.platform, MANAGED_SETTINGS_DEFAULT)


def settings_files(cwd=None, operator_only=False) -> list:
    """The settings files a `claude` launched in `cwd` reads, highest precedence first, in Claude Code's own
    order: managed settings, the project's local file, the project's shared file, the user's file. The
    per-session --settings layer (sdk_backend.flag_settings_path) sits between the first two and is romp's
    own, so it is not listed here. `operator_only` keeps the two files the OPERATOR of this box controls
    (managed and user) and drops the project pair: that is the scope the kernel itself acts on (below)."""
    files = [managed_settings_path()]
    if not operator_only:
        try:
            cwd = os.path.realpath(cwd or os.getcwd())
        except OSError:
            cwd = None              # a working directory removed under the process: no project files to read
        if cwd:
            files += [os.path.join(cwd, ".claude", "settings.local.json"),
                      os.path.join(cwd, ".claude", "settings.json")]
    files.append(os.path.join(claude_config_dir(), "settings.json"))
    return files


# _read_settings's memo: path -> ((inode, mtime ns, ctime ns, size), the parsed dict). The helper readers run
# per push from the kernel's pusher thread (helper_source through _auth_both and _auth_avail) and per auth
# check from the SDK backend; before the memo each call opened and parsed the user and the managed file
# (measured on a live box: ~57 opens a second of each). One stat per call now, a read only when it changed.
_SETTINGS_CACHE = {}


def _read_settings(path):
    """One settings file as a dict; None when absent. Unreadable or unparsable is loud: the CLI would refuse
    it too, and a silently skipped file would misreport the box as helper-less.

    Memoized per path on the file's stat identity (inode, mtime in ns, ctime in ns, size): one stat per call,
    a read and a parse only when that changed, so the hot reload api_key_helper promises holds (a helper the
    user just added counts at the next call) while the per-push callers cost a stat rather than an open. A
    rewrite in place within one timestamp tick that keeps the byte length is the accepted blind spot, and so is
    a chmod within the tick of the last write (kernels before multigrain timestamps, Linux 6.13, stamp ctime
    with the coarse tick); past that tick a rename into place is a new inode and a chmod is a new ctime, so
    both are seen (the chmod matters: a file made unreadable takes the loud path below at the next call
    instead of serving its old parse). Nothing loud is memoized: an unreadable or unparsable file raises on
    every call. An absent file drops its entry. Callers read the returned dict and never mutate it: it is the
    memoized object."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        _SETTINGS_CACHE.pop(path, None)
        return None
    except OSError:
        raise CredentialError("Claude Code settings file cannot be read: %s" % path)
    ident = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size)
    hit = _SETTINGS_CACHE.get(path)
    if hit is not None and hit[0] == ident:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        _SETTINGS_CACHE.pop(path, None)      # removed between the stat and the open: absent, as the stat would say
        return None
    except OSError:
        raise CredentialError("Claude Code settings file cannot be read: %s" % path)
    try:
        d = json.loads(text)
    except ValueError:
        raise CredentialError("Claude Code settings file is not valid JSON: %s" % path)
    d = d if isinstance(d, dict) else {}
    _SETTINGS_CACHE[path] = (ident, d)     # the stat from BEFORE the read: a write in between re-reads next time
    return d


def api_key_helper(cwd=None, operator_only=False):
    """The `apiKeyHelper` command Claude Code would run for a process in `cwd`: the value in the
    highest-precedence settings file that DEFINES it as a string. "" when that file sets it to "" (the
    value that disables the helper; a login launch's per-session layer uses it), None when no file defines
    it (a null falls through to the next file, as it does in the CLI). Resolved fresh on every call: Claude
    Code hot-reloads its settings files, and a helper the user just added must count at once; the cost is
    four stats (_read_settings re-reads a file only when its stat changed). `operator_only`: see
    settings_files."""
    for p in settings_files(cwd, operator_only):
        d = _read_settings(p)
        if d is None or HELPER_KEY not in d:
            continue
        v = d.get(HELPER_KEY)
        if isinstance(v, str):
            return v.strip()
    return None


def helper_source():
    """Which operator file defines the helper the box runs: "managed", "user", or None when neither defines a
    non-empty one. A MANAGED helper outranks the per-session settings layer in the CLI's precedence, so a
    login pick cannot disable it: the backend refuses the pick with that reason instead of billing the key
    quietly (review 2026-09-08)."""
    for label, p in (("managed", managed_settings_path()), ("user", os.path.join(claude_config_dir(), "settings.json"))):
        d = _read_settings(p)
        if d is None or HELPER_KEY not in d:
            continue
        v = d.get(HELPER_KEY)
        if isinstance(v, str):
            return label if v.strip() else None
    return None


def key_available() -> bool:
    """Whether this box has a key side: an apiKeyHelper is configured in the OPERATOR's settings (managed or
    user). Read, never run. A project's own .claude/settings.json may carry a helper too; Claude Code runs
    it for sessions in that project (behind its trust prompt), and the per-init auth check reports the
    landing, but romp neither counts it as the box's key side nor ever runs it (review 2026-09-08: a
    checked-in settings file must never be a command the kernel executes)."""
    return bool(api_key_helper(None, operator_only=True))


def project_helper_differs(cwd) -> bool:
    """True when a session launched in `cwd` would resolve a DIFFERENT helper than the operator's (a project
    or local settings file defines its own, or disables the operator's). The kernel's own calls then stand
    down for that session rather than ask with a key the session does not bill."""
    return api_key_helper(cwd) != api_key_helper(None, operator_only=True)


def helper_ttl_s() -> float:
    raw = (os.environ.get(HELPER_TTL_VAR) or "").strip()
    try:
        ms = int(raw) if raw else HELPER_TTL_DEFAULT_MS
    except ValueError:
        ms = HELPER_TTL_DEFAULT_MS
    return max(0, ms) / 1000.0


def helper_env() -> dict:
    return {k: v for k, v in os.environ.items()
            if k in HELPER_ENV_PASSTHROUGH or k.startswith(HELPER_ENV_PREFIXES)}


def run_helper(cmd, label: str = "apiKeyHelper", timeout_s=None) -> str:
    """Run the helper once, the way Claude Code runs it: through /bin/sh, stdin /dev/null (a prompt would
    hang until the timeout), stderr discarded and never logged (a secret manager's diagnostics can quote
    its own token), stdout the key: non-empty, one line, no whitespace, at most 16 KiB, one trailing
    newline forgiven (a script's echo adds one). Every failure is a CredentialError in static words that
    open with `label`: the box's own apiKeyHelper by default, a stored login's token command when a launch
    or a judge call runs one the same way (logins.token_value, the environment road since 2026-09-14).
    `timeout_s` overrides the bound for a test; HELPER_TIMEOUT_S otherwise."""
    bound = HELPER_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        r = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=bound, check=False, env=helper_env())
    except FileNotFoundError:
        raise CredentialError("%s could not run: /bin/sh is not available" % label) from None
    except subprocess.TimeoutExpired:
        raise CredentialError("%s timed out after %d s" % (label, bound)) from None
    except OSError:
        raise CredentialError("%s could not be run" % label) from None
    if r.returncode:
        raise CredentialError("%s is not on the manager's PATH (exit 127)" % label if r.returncode == 127
                              else "%s failed (non-zero exit)" % label)
    try:
        value = r.stdout.decode("utf-8")
    except UnicodeError:
        raise CredentialError("%s printed bytes that are not a key" % label) from None
    if value.endswith("\n"):
        value = value[:-2] if value.endswith("\r\n") else value[:-1]
    if not value or len(value) > 16384 or any(c.isspace() or c == "\0" for c in value):
        raise CredentialError("%s printed an empty or invalid key (one line on stdout, exit 0)" % label)
    return value


_HELPER_LOCK = threading.Lock()
_HELPER_MEMO = {"cmd": None, "value": "", "at": 0.0}    # in process memory only, and only within the TTL
_HELPER_TIMER = [None]                                   # the expiry that clears the value when the TTL ends


def helper_key(now=None) -> str:
    """The key the OPERATOR's configured helper prints (managed or user settings, never a project's: the
    kernel executes no command a repository checked in), for the kernel's OWN API calls (the model catalog,
    the fast-mode org probe). "" when no helper is configured, so the caller says so in its own words.
    Memoized in process memory for the helper's TTL (CLAUDE_CODE_API_KEY_HELPER_TTL_MS, five minutes by
    default, the CLI's own interval) keyed on the helper command: a changed helper re-runs at once, a
    rotated vault item is picked up within the TTL. `now` is a monotonic clock, injectable by tests."""
    cmd = api_key_helper(None, operator_only=True)
    if not cmd:
        forget_helper_key()                  # the helper is gone: so is its value
        return ""
    now = time.monotonic() if now is None else now
    ttl = helper_ttl_s()
    with _HELPER_LOCK:
        m = _HELPER_MEMO
        if m["cmd"] == cmd and m["value"] and now - m["at"] < ttl:
            return m["value"]
    forget_helper_key()                      # expired, or another helper's: nothing stale is held while we run
    try:
        value = run_helper(cmd)
    except CredentialError:
        forget_helper_key()                  # a failed run leaves no value behind
        raise
    with _HELPER_LOCK:
        _HELPER_MEMO.update(cmd=cmd, value=value, at=now)
        # The value lives exactly as long as the CLI would keep its own copy: a timer forgets it at the TTL
        # even if nothing asks again (the user's principle: romp holds no key material beyond what one call
        # needs). Daemon, so it never holds the kernel open.
        t = _HELPER_TIMER[0]
        if t is not None:
            t.cancel()
        t = threading.Timer(ttl, _forget_if_still, args=(cmd, now))
        t.daemon = True
        _HELPER_TIMER[0] = t
        t.start()
    return value


def _forget_if_still(cmd, at) -> None:
    with _HELPER_LOCK:
        if _HELPER_MEMO["cmd"] == cmd and _HELPER_MEMO["at"] == at:
            _HELPER_MEMO.update(cmd=None, value="", at=0.0)


def forget_helper_key() -> None:
    with _HELPER_LOCK:
        _HELPER_MEMO.update(cmd=None, value="", at=0.0)
        t = _HELPER_TIMER[0]
        if t is not None:
            t.cancel()
            _HELPER_TIMER[0] = None


# ---------------------------------------------------------------------------
# The key the kernel holds for its judge children (2026-10-05)
# ---------------------------------------------------------------------------
# Until 2026-10-05 every key-billed judge child (`claude -p`, about 90 a minute on a busy box) ran the
# apiKeyHelper inside its own CLI, so every judge call was one read from the operator's secret manager, and
# when the secret manager's read quota refused most of them the cards went stale. The machine's owner ruled
# that the kernel holds the key for its judge children (the user 2026-10-05, who wanted one helper run where
# there had been one per call). HeldKey is that holder: it runs a command once, keeps what it printed in
# process memory, and hands it to each child through the child's environment, while the child's per-call
# settings layer turns its own helper off (judge._judge_cmd). Sessions are untouched: they still run the
# helper inside their own CLI, and nothing here reaches the kernel's own environment.

HELD_KEY_REFRESH_GAP_S = 60.0   # the command runs at most once per this many seconds, whatever asks for it


class HeldKey:
    """One credential the kernel holds in memory for the children it spawns. Its source is only a command: run
    it, read one line of stdout (run_helper's contract), so a static API key's helper and a short-lived token's
    command plug in the same way; `env_name` is the variable the child reads it from (ANTHROPIC_API_KEY for a
    key, sent as x-api-key; ANTHROPIC_AUTH_TOKEN for a bearer token).

    The command runs:
      - on the first value() call (lazily, never at import or boot);
      - when the command itself changes (the operator edited the settings): at once, nothing held for the old
        command counts;
      - when the held value is older than max_age_fn() seconds (None: no age limit, a static key);
      - after a child reports the held value refused (refused()).
    and never more than once per `refresh_gap_s` (60 s), counted from the start of the last run whatever its
    outcome. Runs hold a lock, so a pool of callers asking at once waits for one run instead of starting one
    each. A failed run is remembered for the gap: callers in it get the same CredentialError without a new
    run, so a secret manager that refuses is not asked again at the judges' call rate.

    The value lives in this object only: never an environment variable of this process, a file or a log line.
    scrub() blanks the values it has held out of text a caller is about to write anywhere. No value is
    logged, and CredentialError messages are run_helper's static words."""

    def __init__(self, command_fn, env_name="ANTHROPIC_API_KEY", label=HELPER_KEY, max_age_fn=None,
                 refresh_gap_s=HELD_KEY_REFRESH_GAP_S, clock=None, runner=None):
        self.command_fn = command_fn                  # () -> the command, or None/"" when there is none
        self.env_name = env_name
        self.label = label
        self.max_age_fn = max_age_fn or (lambda: None)
        self.refresh_gap_s = float(refresh_gap_s)
        self._clock = clock or time.monotonic
        self._runner = runner or (lambda cmd: run_helper(cmd, label=self.label))
        self._lock = threading.Lock()
        self.runs = 0                                 # how many times the command has run (for tests and diagnosis)
        self._recent = []                             # the last two values held, for scrub()
        self._drop_locked(None)

    def value(self) -> str:
        """The credential for the next child, or "" when the source names no command (the caller keeps the old
        road: the child resolves its own credential). Raises CredentialError when the command fails, when the
        last run failed less than a gap ago, or when a child reported the held value refused less than a gap
        after the last run (the next run is due when the gap ends)."""
        cmd = self.command_fn()
        with self._lock:
            if not cmd:
                self._drop_locked(None)               # the source is gone: so is what it printed
                return ""
            if cmd != self._cmd:
                self._drop_locked(cmd)
            now = self._clock()
            if self._value and not self._refused and not self._expired_locked(now):
                return self._value
            if self._in_gap_locked(now):
                if self._error is not None:
                    raise CredentialError(str(self._error))
                if self._refused:
                    raise CredentialError("the API refused the key %s printed; romp runs %s again at most once "
                                          "every %d s" % (self.label, self.label, self.refresh_gap_s))
                if self._value:
                    return self._value                # past its age limit inside the gap: the gap is the floor
            return self._run_locked(cmd, now)

    def refused(self, used, allow_run=True) -> str:
        """A child given `used` reported a credential failure (a 401 or the CLI's words for one). Returns the
        value to retry that child with once, or "" for no retry. When another caller already replaced the value,
        the replacement is returned and nothing runs. Otherwise the held value is marked refused and the command
        runs again, unless it ran less than a gap ago or `allow_run` is False (a retry that was itself refused:
        then "", and value() refuses until the gap ends). Raises CredentialError when that run fails."""
        cmd = self.command_fn()
        with self._lock:
            if not used or not cmd or cmd != self._cmd:
                return ""
            if self._value and self._value != used and not self._refused:
                return self._value                    # another child's refusal already brought a new value
            if self._value != used:
                return ""
            self._refused = True
            now = self._clock()
            if not allow_run or self._in_gap_locked(now):
                return ""
            return self._run_locked(cmd, now)

    def forget(self) -> None:
        with self._lock:
            self._drop_locked(None)

    def scrub(self, text):
        """`text` with every value this holder has held replaced by a placeholder, in its raw and its
        JSON-escaped form: for any writer about to put a child's output in a file or a log line."""
        if not text or not self._recent:
            return text
        s = str(text)
        for v in list(self._recent):
            for form in {v, json.dumps(v)[1:-1]}:
                if form:
                    s = s.replace(form, "[key withheld]")
        return s

    def _drop_locked(self, cmd):
        self._cmd = cmd
        self._value = ""
        self._at = None                               # clock reading of the run that produced _value
        self._ran_at = None                           # clock reading of the last run, whatever its outcome
        self._error = None                            # that run's CredentialError, kept for the gap
        self._refused = False

    def _expired_locked(self, now):
        age = self.max_age_fn()
        return age is not None and self._at is not None and now - self._at >= age

    def _in_gap_locked(self, now):
        return self._ran_at is not None and now - self._ran_at < self.refresh_gap_s

    def _run_locked(self, cmd, now):
        self._ran_at = now
        self.runs += 1
        self._value = ""                              # nothing stale is held while it runs or after it fails
        self._at = None
        self._refused = False
        try:
            value = self._runner(cmd)
        except CredentialError as e:
            self._error = e
            raise
        except Exception:
            # anything else is the runner's own fault; it is remembered for the gap like a failed run, in static
            # words (an exception's text could quote what the command printed), so the gap holds whatever broke
            self._error = CredentialError("%s could not be run" % self.label)
            raise self._error from None
        if not isinstance(value, str) or not value:
            self._error = CredentialError("%s printed an empty or invalid key (one line on stdout, exit 0)" % self.label)
            raise self._error
        self._error = None
        self._value = value
        self._at = now
        self._recent = ([v for v in self._recent if v != value] + [value])[-2:]
        return value


def child_key_helper():
    """The helper command the kernel may run on behalf of its judge children: the operator's own, from the
    USER settings file. A helper set in MANAGED settings outranks the per-call settings layer that turns a
    child's helper off, so the child would run it anyway: None there, and those children keep resolving the
    helper themselves. A project's helper is never run by the kernel (key_available). An unreadable settings
    file raises CredentialError, the caller's loud road."""
    if helper_source() != "user":
        return None
    return api_key_helper(None, operator_only=True) or None


def helper_ttl_if_set():
    """The age limit for the judges' held key: the helper's TTL when the operator set
    CLAUDE_CODE_API_KEY_HELPER_TTL_MS, else None. Unset, the key is held until the API refuses it (a static key
    lasts until it is rotated, and a rotated key's refusal brings the new one); the CLI's five-minute default
    would mean twelve secret-manager reads an hour for nothing."""
    if not (os.environ.get(HELPER_TTL_VAR) or "").strip():
        return None
    return helper_ttl_s()
