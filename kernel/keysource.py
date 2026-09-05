"""Live API-key configuration, separate from credential retrieval.

The design, in four rules:

* A SOURCE is configuration, not a secret: a static key line, or a 1Password reference
  (``ROMP_API_KEY_REF=op://vault/item/field``) that names where the key lives. Everything that
  needs to KNOW about the key — the Billing picker, status displays, ``romp keyswap``'s listing and
  identity checks — reads the source. Only resolve() ever runs ``op read``, at the moment a Claude
  session launches, an API-key-billed judge call is made, or the model catalog refreshes; the value
  is handed to that one operation and never written to disk or cached for a later one.
* A selected source is AUTHORITATIVE. A file that once carried a key line or a reference keeps
  governing this process: emptying it, removing the line, or making it unreadable is an error the
  operation reports, never permission to fall back to the key the manager started with or to a
  login. That is what makes ``romp keyswap`` a swap — nothing an operator removed can come back.
* Supervised managers (``ROMP_SUPERVISED=1``: the systemd/launchd service) read the FILE only. The
  manager process keeps the environment it started with across every kernel restart, so a key it
  inherited would otherwise resurrect after the operator removed it from the file. A startup key
  the kernel therefore ignores is said once on the log wire (sdk_backend.work_api_key_source).
* ``op``'s OWN credential (a service-account token, a session token) is the one secret that must
  reach the kernel's environment for headless use — and nothing else: claim_op_env() takes those
  names out of os.environ at startup and resolve() hands them back to the ``op read`` subprocess
  alone, so no Claude session, judge child or tmux launch inherits a vault-wide credential.

Legacy environment/file keys and Claude login remain supported without 1Password.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass, field

KEY_VAR = "ANTHROPIC_API_KEY"
REF_VAR = "ROMP_API_KEY_REF"
OP_TIMEOUT = 15
# The environment names the 1Password CLI authenticates from. Claimed out of the kernel's environment
# once (claim_op_env) and given back to the `op read` subprocess only (resolve): a service-account
# token reads every field the account can see, and a child that inherits it — a Claude session's
# Bash, a judge call, a subagent — could print it with `env`. OP_SESSION_<account> is the name shape
# `op signin` exports; anything with that prefix is treated the same way.
OP_ENV_NAMES = ("OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT")
OP_ENV_PREFIX = "OP_SESSION_"
_OP_ENV: dict[str, str] = {}
_OP_CLAIM_SAID = False


def is_op_env_name(name: str) -> bool:
    return name in OP_ENV_NAMES or name.startswith(OP_ENV_PREFIX)


def op_consumer() -> bool:
    """Is romp itself the one running `op`? Only when a 1Password reference is selected (in the env file
    or the manager's environment). A box whose SESSIONS fetch their key through Claude Code's apiKeyHelper
    calling `op` needs op's credential in every session's environment, and romp then leaves it alone."""
    if REF_VAR in os.environ:
        return True
    return read_source().kind in ("op", "error") if os.path.exists(service_env_path()) else False


def claim_op_env() -> dict[str, str]:
    """Take op's credential names out of os.environ and return the running stash — but only while romp
    is the op consumer (see op_consumer): otherwise the environment is left exactly as found, since a
    session-side helper may need it. Idempotent and cheap; a value that appears later is claimed too. The
    claimed NAMES (never values) are said once on the log wire so a helper that stops working has a
    line to be found by."""
    global _OP_CLAIM_SAID
    if not op_consumer():
        return _OP_ENV
    names = [k for k in os.environ if is_op_env_name(k)]
    for k in names:
        _OP_ENV[k] = os.environ.pop(k)
    if names and not _OP_CLAIM_SAID:
        _OP_CLAIM_SAID = True
        import sys
        sys.stderr.write("op credentials claimed from the environment for `op read`: %s — sessions, judge "
                         "calls and tmux launches will not see them\n" % ", ".join(sorted(names)))
    return _OP_ENV


def strip_op_env(env: dict) -> dict:
    """The same names removed from a child environment built before the claim (a standalone judge, a
    kernel that spawned a tmux launch before its backend existed) — while romp is the op consumer; a
    helper box keeps its environment. Returns `env` for chaining."""
    if _OP_ENV or op_consumer():
        for k in [k for k in env if is_op_env_name(k)]:
            env.pop(k, None)
    return env


def tmux_unset_global(names, socket: str = "") -> list:
    """Remove op's credential names from a tmux SERVER's global environment — the environment every new
    pane inherits, which the manager-started server carried from service.env (review find, 2026-09-05:
    a tmux session's `exec claude` saw the token although the launching client had been scrubbed). Best
    effort: no tmux, no server, an old tmux → nothing happens. Returns the commands run, for tests."""
    ran = []
    for name in sorted(set(n for n in names if is_op_env_name(n))):
        cmd = ["tmux"] + (["-L", socket] if socket else []) + ["set-environment", "-gu", name]
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
            ran.append(cmd)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ran


def runtime_reserved_names(auth: str, source) -> tuple:
    """The credential names a per-session environment may NOT carry while runtime retrieval governs.
    A per-session ANTHROPIC_API_KEY always competes with the selected source. A KEYED launch (an explicit
    key pick, or no pick with a configured source) must carry no token beside the key it resolves either;
    a LOGIN session's own token override bills the account the user chose for it and never touches the
    key source, so it stays (review find, 2026-09-05). One rule for the doors, the launch and the fork."""
    if source is None or source.kind not in ("op", "error"):
        return ()
    keyed = auth == "key" or (auth != "login" and source.configured)
    return ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN") if keyed else ("ANTHROPIC_API_KEY",)

# Cache file configuration only. ctime/mode also invalidate permission changes; a formerly
# readable credential must not survive a chmod merely because its content did not change.
_CACHE: tuple = ((), "")
_AUTHORITATIVE_PATHS: dict[str, str] = {}
_ENV_PROVIDER_PATHS: set[str] = set()


class KeySourceError(RuntimeError):
    """A credential failure whose message is safe for user-visible logs."""


@dataclass(frozen=True)
class KeySource:
    kind: str
    value: str = field(default="", repr=False)
    error: str = ""

    @property
    def configured(self) -> bool:
        # An invalid provider remains an explicit choice, never permission to use a login.
        return self.kind in ("op", "error") or bool(self.value)

    def validate(self) -> None:
        if self.kind == "error":
            raise KeySourceError(self.error or "Cannot read the configured API key source")
        if self.kind == "op":
            parts = self.value[5:].split("/") if self.value.startswith("op://") else []
            if (len(parts) not in (3, 4) or not all(parts)
                    or any(c in self.value for c in ("\r", "\n", "\0"))):
                raise KeySourceError("ROMP_API_KEY_REF must be an op://vault/item/[section/]field reference")
        elif self.kind not in ("file", "environment", "none"):
            raise KeySourceError("Unknown API key source")
        elif any(c in self.value for c in ("\r", "\n", "\0")):
            raise KeySourceError("API keys must be a single line")

    def fingerprint(self) -> str:
        """Configuration identity; for op this hashes the reference, never retrieves its value."""
        if self.kind == "op":
            return fingerprint("op:" + self.value)
        if self.kind == "error":
            return ""
        return fingerprint(self.value)

    def resolve(self) -> str:
        self.validate()
        if self.kind != "op":
            return self.value
        # op authenticates from the credential names claimed at startup; they ride into THIS subprocess
        # and no other (see claim_op_env). The rest of the environment goes along so op finds its PATH,
        # HOME and config the way the manager's shell would have given them.
        op_env = dict(os.environ)
        op_env.update(claim_op_env())
        try:
            result = subprocess.run(
                ["op", "read", "--no-newline", self.value], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=OP_TIMEOUT,
                check=False, env=op_env,
            )
        except FileNotFoundError:
            raise KeySourceError("1Password CLI (op) is not on the manager's PATH") from None
        except subprocess.TimeoutExpired:
            raise KeySourceError("1Password credential retrieval timed out; check op authentication") from None
        except OSError:
            raise KeySourceError("Cannot run 1Password CLI; check the manager's op installation") from None
        if result.returncode:
            # Neither subprocess stderr nor its exception repr is safe to log.
            raise KeySourceError("1Password credential retrieval failed; check op authentication and vault access")
        try:
            value = result.stdout.decode("utf-8")
        except UnicodeError:
            raise KeySourceError("1Password returned an invalid API key") from None
        if not value or len(value) > 16384 or any(c.isspace() or c == "\0" for c in value):
            raise KeySourceError("1Password returned an empty or invalid API key")
        return value


def service_env_path() -> str:
    """The path of the env file the manager is configured from.

    `ROMP_SERVICE_ENV_FILE` is the name the installer and the macOS launcher already use
    (`bin/romp-service`, `bin/romp-node-launch`), so it is the primary; `ROMP_SERVICE_ENV` is
    accepted as an alias. Default `${XDG_CONFIG_HOME:-~/.config}/romp/service.env` — the same
    expression those two scripts compute, so all three always name one file.
    """
    for var in ("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV"):
        p = (os.environ.get(var) or "").strip()
        if p:
            return os.path.expanduser(p)
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "romp", "service.env")


def sibling_path(name: str, path: str | None = None) -> str:
    """The candidate file a keyswap reads from. A bare name (`highprio`) means the sibling
    `service.env.<name>` beside the live file — the convention that keeps candidate files together
    with mode 0600 (a private parent directory needs 0700). Anything with a separator, or an explicit
    path, is taken as given."""
    name = str(name or "").strip()
    if not name:
        return ""
    if os.sep in name or name.startswith("~"):
        return os.path.expanduser(name)
    return (path or service_env_path()) + "." + name


def _assignments(text: str) -> dict[str, str]:
    """Read literal env assignments; never execute or expand the file's contents."""
    out = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or name.strip() not in (KEY_VAR, REF_VAR):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]     # systemd strips one layer; without this the CLI gets the quotes
        out[name.strip()] = value
    return out


def _garbled(values: dict) -> str:
    """The name of a key/reference line that did not decode as UTF-8 (the read replaces bad bytes with
    U+FFFD, which no real key contains) — "" when both are clean. A garbled key must fail HERE, with a
    readable note, not at the API as an unexplained invalid-key error."""
    for name in (REF_VAR, KEY_VAR):
        if "\ufffd" in values.get(name, ""):
            return name
    return ""


def parse_key(text: str) -> str:
    return _assignments(text).get(KEY_VAR, "")


def parse_source(text: str) -> KeySource:
    values = _assignments(text)
    bad = _garbled(values)
    if bad:
        return KeySource("error", error="the %s line in the API key source configuration is not valid UTF-8" % bad)
    if REF_VAR in values:
        return KeySource("op", values[REF_VAR])
    if KEY_VAR in values:
        return KeySource("file", values[KEY_VAR])
    return KeySource("none")


def read_source(path: str | None = None) -> KeySource:
    """Read configuration only. Read errors remain errors, never an absent credential."""
    global _CACHE
    p = path or service_env_path()
    try:
        st = os.stat(p)
        ident = (p, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode)
    except FileNotFoundError:
        _CACHE = ((p, "absent"), KeySource("none"))
        return _CACHE[1]
    except OSError:
        return KeySource("error", error="Cannot read the API key source configuration")
    if _CACHE[0] == ident:
        return _CACHE[1]
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:   # a stray byte in a comment is not an outage
            source = parse_source(fh.read())
    except OSError:
        return KeySource("error", error="Cannot read the API key source configuration")
    _CACHE = (ident, source)
    return source


def select_source(startup_key: str = "") -> KeySource:
    """Choose a source without fetching it; only a never-configured file permits env fallback.

    Track the path, not one global flag, so isolated kernels/tests with different config roots
    cannot change each other's source policy. An unreadable file is always an error. Once an op
    reference has been selected from a file or the environment, deleting it is an error until a new
    source is configured. A previously selected file stays authoritative over the environment.
    Supervised services always use the file: their manager may still hold an old assignment across
    kernel restarts, so inherited credentials cannot establish a fallback for a fresh kernel.
    """
    path = service_env_path()
    source = read_source(path)
    if source.kind != "none":
        if source.kind != "error":
            _AUTHORITATIVE_PATHS[path] = source.kind
        return source
    previous = _AUTHORITATIVE_PATHS.get(path)
    if previous == "op":
        return KeySource("error", error="The 1Password reference was removed; configure an API key source explicitly")
    if previous:
        return KeySource("file")
    if os.environ.get("ROMP_SUPERVISED") == "1":
        if REF_VAR in os.environ:
            _AUTHORITATIVE_PATHS[path] = "op"
            return KeySource("error", error="The 1Password reference was removed; configure an API key source explicitly")
        _AUTHORITATIVE_PATHS[path] = "file"
        return KeySource("file")
    if REF_VAR in os.environ:
        _ENV_PROVIDER_PATHS.add(path)
        return KeySource("op", os.environ[REF_VAR].strip())
    if path in _ENV_PROVIDER_PATHS:
        return KeySource("error", error="The 1Password reference was removed from the environment; configure an API key source explicitly")
    return KeySource("environment", startup_key) if startup_key else source


def read_key(path: str | None = None) -> str:
    """Legacy file reader. Provider references are never interpreted as raw keys."""
    source = read_source(path)
    return source.value if source.kind == "file" else ""


def fingerprint(key: str) -> str:
    """The ONLY renderable form of a key: first 12 hex of its sha256, "" for no key. Same value in
    the kernel log and in `romp keyswap`'s output, so an operator can check that the kernel reads
    the key they just wrote without either side ever printing it."""
    key = str(key or "")
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:12]


def write_source(source: KeySource, path: str | None = None) -> dict:
    """Atomically select a source, removing competing key/reference assignments.

    Every other line survives byte for byte, in place — the file also carries things like
    `ROMP_PERF=1` and `ROMP_EXPECTED_AUTH`, and a rewrite that dropped them would change the
    manager's behaviour on its next start for reasons nobody would connect to a key swap. The
    key line keeps its POSITION (last assignment replaced in place, any earlier duplicate
    removed so the file cannot disagree with itself); a file with no such line gets one appended.

    Written to a temp file in the same directory, created 0600 with O_EXCL, then `os.replace` —
    so no reader ever sees a half-written file and the key is never briefly world-readable. The
    mode is the original file's, narrowed to 0600 if it granted group or other any access at all;
    a new file is 0600.

    A SYMLINKED env file is written THROUGH (2026-09-04): a dotfiles-managed `service.env` is a link,
    and an `os.replace` onto the link's own name would swap the link for a plain file and leave its
    target — what the operator's repo tracks and what a re-link would restore — on the old key.

    Returns {"path", "old", "new", "mode", "tightened", "lines", "target"} — `old`/`new` are source
    descriptors, so the caller can report their identities without resolving provider credentials;
    `target` is the file actually rewritten (the link's target, else `path`). Raises OSError on a real
    failure (the caller reports it).
    """
    source.validate()
    if source.kind not in ("op", "file", "environment"):
        raise KeySourceError("Select an API key or a 1Password reference")
    given = path or service_env_path()
    p = os.path.realpath(given) if os.path.islink(given) else given
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        existed = True
    except FileNotFoundError:
        body, existed = "", False
    old = parse_source(body)
    lines = body.splitlines()
    trailing_nl = (not body) or body.endswith("\n")
    # Which physical lines assign the key: the LAST one is rewritten in place, earlier ones drop.
    hits = [i for i, raw in enumerate(lines)
            if raw.strip() and not raw.strip().startswith("#")
            and raw.strip().partition("=")[1]
            and raw.strip().partition("=")[0].strip() in (KEY_VAR, REF_VAR)]
    new_line = "%s=%s" % (REF_VAR if source.kind == "op" else KEY_VAR, source.value)
    if hits:
        lines[hits[-1]] = new_line
        for i in reversed(hits[:-1]):
            del lines[i]
    else:
        lines.append(new_line)
        trailing_nl = True
    out = "\n".join(lines) + ("\n" if trailing_nl else "")
    mode, tightened = 0o600, False
    if existed:
        try:
            mode = os.stat(p).st_mode & 0o777
            if mode & 0o077:            # never leave a key group- or world-readable
                mode, tightened = 0o600, True
        except OSError:
            mode = 0o600
    d = os.path.dirname(p) or "."
    # mkstemp: same directory (so the rename is atomic — a cross-filesystem one is a copy), a name
    # nothing can collide with, and 0600 from the moment the file exists, so the key is never
    # briefly readable by anyone else.
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(p) + ".keyswap.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"path": given, "old": old, "new": source, "mode": mode, "tightened": tightened,
            "lines": len(lines), "target": p}


def write_key(key: str, path: str | None = None) -> dict:
    """Compatibility API for callers explicitly writing a legacy static key."""
    result = write_source(KeySource("file", key), path)
    return dict(result, old=result["old"].value if result["old"].kind == "file" else "", new=key)
