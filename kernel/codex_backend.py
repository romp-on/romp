#!/usr/bin/env python3
"""codex_backend — the Codex SessionBackend (plans/codex-backend.md).

Drives OpenAI Codex sessions through the official openai-codex Python SDK's sync CodexClient
(JSON-RPC to `codex app-server` over stdio) and materializes each thread as Claude-transcript-shaped
JSONL via kernel/codex_events.ThreadNormalizer, so romp's entire read side parses Codex sessions
unchanged. Duck-types the SessionBackend ABC exactly like SdkBackend does (the kernel loads backends
via SourceFileLoader; a conformance test asserts every abstract method exists).

Shape of the machine:
- ONE CodexClient per backend — the app-server hosts many threads, unlike Claude's one-CLI-per-
  session. One GLOBAL pump thread drains thread-level notifications (tokenUsage, rateLimits);
  each session gets a WORKER thread that drains its send-queue into turns and consumes that turn's
  own notification queue (the SDK routes a started turn's events there, not to the global queue).
- The worker and global pump serialize through one per-session normalizer/file lock:
  notification → normalizer → append → poke the kernel.
- Sends NEVER block on a running turn: mid-turn they steer (turn/steer with the active turn id as
  precondition), racing a just-ended turn falls back to the queue, and the queue drains into the
  next turn_start — that is forwards_sends() on this backend.
- Auth is machine-global (`codex login`): a missing login is surfaced PER SESSION via launch_error,
  loudly, the moment a session tries to run (the 2026-07-28 rule: never a silent non-start).

Everything Claude-only returns its documented empty value and the kernel stays loud about it:
set_fast/set_auth/stop_task/rewind_files → False, on_ask → False, current_ask → None.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid as uuidlib
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(os.path.dirname(os.path.realpath(__file__)))
_events = SourceFileLoader("romp_codex_events", str(HERE / "codex_events.py")).load_module()
_runtime = SourceFileLoader("romp_codex_runtime", str(HERE / "codex_runtime.py")).load_module()

SDK_PIN = "openai-codex==0.144.4"     # bin/romp-codex-setup installs exactly this into codexvenv
SETUP_HINT = ("Session not created: the Codex backend isn't installed. "
              "Run romp-codex-setup, then try again.")
LOGIN_HINT = "Codex isn't logged in on this machine — run: codex login"

# The phase-1 posture: sandboxed full-auto (plans/codex-backend.md). In pinned 0.144.4, BOTH legacy
# workspaceWrite and the built-in :workspace profile include `:root = read`; runtimeWorkspaceRoots
# only adds roots and cannot subtract that host-wide read. Define a fail-closed custom profile:
# minimal runtime files are readable, the session workspace is writable, and network remains enabled
# for git/web. Pinned 0.144.4 cannot enforce narrower child access inside a custom writable root, so
# metadata directories remain writable (documented in docs/codex.md) rather than carrying misleading
# read-only entries that its arbitrary-process sandbox ignores.
WORKSPACE_PERMISSION = "romp_workspace"


def _workspace_profile_override(runtime_reads=()):
    entries = ['":minimal" = "read"', '":workspace_roots" = { "." = "write" }']
    entries.extend('%s = "read"' % json.dumps(str(path), ensure_ascii=False)
                   for path in runtime_reads)
    return ('permissions.romp_workspace={ filesystem = { %s }, '
            'network = { enabled = true } }') % ", ".join(entries)


_WORKSPACE_PROFILE_OVERRIDE = _workspace_profile_override()
# 0.144.4 rejects any custom [permissions] table unless default_permissions is also selected.
# Selecting it globally is a defense in depth; thread/resume/turn still name it explicitly.
CODEX_CONFIG_OVERRIDES = (_WORKSPACE_PROFILE_OVERRIDE,
                          'default_permissions="romp_workspace"')
TURN_SANDBOX = None   # explicit smoke-only legacy override (for hosts unable to run the sandbox)
APPROVAL_POLICY = "never"  # default: existing sessions remain sandboxed
MODES = ("sandboxed", "auto")


def _approval_params(mode="sandboxed"):
    if mode == "auto":
        return {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review"}
    if mode != "sandboxed":
        raise ValueError("Unsupported Codex mode: %s" % mode)
    # Reset the reviewer as well: thread/resume otherwise inherits a previous Auto selection.
    return {"approvalPolicy": APPROVAL_POLICY, "approvalsReviewer": "user"}

# romp effort names → Codex ReasoningEffort. Identity for the shared four; max/ultracode are
# Claude-only knobs and set_effort refuses them (False → the kernel warns instead of pretending).
EFFORTS = ("low", "medium", "high", "xhigh")

SEED_TAIL = 200   # records whose uuids seed the normalizer's dedup on re-attach (replay guard)
CLIENT_RETRY_MIN = 0.25
CLIENT_RETRY_MAX = 5.0
WORKER_JOIN_TIMEOUT = 2.0

_PERMANENT_RPC_ERRORS = {"ParseError", "InvalidRequestError", "MethodNotFoundError",
                         "InvalidParamsError"}
_PERMANENT_RPC_TEXT = (
    re.compile(r"\b(?:unknown|unsupported|invalid) (?:model|permission|approval|sandbox)\b", re.I),
    re.compile(r"\bmodel\b.*\b(?:does not exist|is not supported|not allowed)\b", re.I),
    re.compile(r"\bpermission profile\b.*\b(?:not found|does not exist|is not supported)\b", re.I),
)


def _is_permanent_request_rejection(error):
    """Whether an app-server request reached Codex and was rejected non-retryably.

    The pinned SDK gives request-shape/method/parameter failures distinct types. InternalRpcError,
    ServerBusyError and unknown numeric app codes can recover without changing the request, so they
    deliberately remain on automatic backoff. A few app-specific model/policy rejections arrive as
    generic CodexRpcError; park only their unambiguous text. Keep this duck-typed so the backend can
    import before the optional SDK is installed.
    """
    name = error.__class__.__name__
    if name in _PERMANENT_RPC_ERRORS:
        return True
    if name not in {"JsonRpcError", "CodexRpcError"}:
        return False
    message = str(getattr(error, "message", "") or error)
    return any(pattern.search(message) for pattern in _PERMANENT_RPC_TEXT)


# Compatibility for focused probes written against the first durable-queue implementation.
_is_permanent_turn_rejection = _is_permanent_request_rejection


class _PermanentRequestRejection(RuntimeError):
    def __init__(self, cause, operation, change_generation, client_generation):
        super().__init__(str(cause) or cause.__class__.__name__)
        self.operation = operation
        self.change_generation = change_generation
        self.client_generation = client_generation


def _execution_permissions(cwd, thread_start=False):
    """Pinned-runtime execution policy. TURN_SANDBOX remains only for the live smoke's explicit
    dangerFullAccess escape hatch; normal sessions select the named profile and exactly one root."""
    if TURN_SANDBOX is not None:
        if thread_start:
            modes = {"dangerFullAccess": "danger-full-access", "readOnly": "read-only",
                     "workspaceWrite": "workspace-write"}
            return {"sandbox": modes.get(TURN_SANDBOX.get("type"), "workspace-write")}
        return {"sandboxPolicy": TURN_SANDBOX}
    root = str(Path(cwd or ".").resolve())
    return {"permissions": WORKSPACE_PERMISSION, "runtimeWorkspaceRoots": [root]}


def _codex_config(config_cls, codex_bin, state_dir=None):
    """Launch ROMP's managed CLI, with its matching helpers, independently of PATH.

    An explicit executable remains available for callers testing another runtime.
    """
    extra = {}
    if codex_bin is None:
        exe = _runtime.runtime_path(state_dir)
        codex_bin = str(exe)
    exe = Path(codex_bin).resolve()
    package = exe.parent.parent if exe.parent.name == "bin" else exe.parent
    helpers = package / "codex-path"
    if helpers.is_dir():
        extra["env"] = {"PATH": str(helpers) + os.pathsep + os.environ.get("PATH", os.defpath)}
    # bwrap re-enters Codex to apply seccomp before launching the requested command.
    # :minimal covers OS runtime files, not an installation in the user's state directory.
    # Expose only the executable and known packaged assets, never its containing state/home dir.
    assets = (exe.parent / "codex-code-mode-host", package / "codex-package.json",
              package / "codex-resources", helpers)
    reads = tuple(dict.fromkeys([exe, *(path.resolve() for path in assets if path.exists())]))
    overrides = (_workspace_profile_override(reads), *CODEX_CONFIG_OVERRIDES[1:])
    return config_cls(codex_bin=codex_bin, client_name="romp",
                      config_overrides=overrides, **extra)


def ensure_codex_sdk(state_dir):
    """Make openai_codex importable: an already-installed copy wins, else the dedicated venv built
    by bin/romp-codex-setup ($STATE/codexvenv — never system python). True when importable."""
    import importlib.util
    import glob
    if importlib.util.find_spec("openai_codex"):
        return True
    for sp in sorted(glob.glob(str(Path(state_dir) / "codexvenv" / "lib" / "python3.*" / "site-packages"))):
        if sp not in sys.path:
            sys.path.insert(0, sp)
    return importlib.util.find_spec("openai_codex") is not None


def _enc_cwd(cwd):
    """The transcript dir name for a cwd — the same encoding the Claude CLI uses for
    ~/.claude/projects (realpath, every non-alphanumeric → '-'), so tooling that already
    understands one layout understands the other."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd or "/").resolve()))


def _dump(payload):
    """A notification payload → the camelCase wire dict the normalizer speaks. Known methods parse
    into pydantic models (model_dump by_alias restores the wire names); unknown ones keep raw params."""
    fn = getattr(payload, "model_dump", None)
    if fn:
        try:
            return fn(by_alias=True, mode="json")
        except Exception:
            return fn(by_alias=True)
    return getattr(payload, "params", None) or {}


def _tail_state(path):
    """(last_uuid, recent uuids) off a materialized file, to re-anchor the normalizer's chain and
    seed its replay dedup after a restart. Reads the whole file once; keeps only the tail's uuids —
    replay across a reconnect only ever re-delivers recent items."""
    last, tail = None, []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    u = json.loads(line).get("uuid")
                except Exception:
                    continue
                if u:
                    last = u
                    tail.append(u)
                    if len(tail) > SEED_TAIL:
                        tail.pop(0)
    except OSError:
        pass
    return last, set(tail)


class _Session:
    """One Codex session: registry row + runtime state. The worker thread owns the normalizer and
    the file; everything else only reads or enqueues."""

    def __init__(self, sid, tid, name, cwd, model="", effort="", color=""):
        self.sid = sid
        self.tid = tid                # Codex thread id == fsid == transcript filename stem
        self.name = name
        self.cwd = cwd
        self.model = model
        self.effort = effort
        self.mode = "sandboxed"
        self.mode_lock = threading.Lock()  # guards policy changes against an in-flight turn
        self.color = color            # identity bg — also in names/<sid> (fields 3/4), the shared store
        self.dead = False
        self.state = "waiting"        # waiting | working (the two states this backend can know)
        self.since = time.time()
        self.queue = []               # pending sends (persisted); drained into the next turn
        self.queue_ids = []           # stable durable identity parallel to queue (public API stays text-only)
        self.echoes = []              # optimistic user-atom echoes ahead of the materialized file
        self.turn_id = None           # the active turn (interrupt/steer target), else None
        self.loaded = False           # thread/resume done in THIS process
        self.launch_error = None      # {text, at, limit} — why the session can't run, or None
        self.norm = None              # ThreadNormalizer, built by the worker on first need
        self.worker = None
        self.kick = threading.Event() # wake the worker (new send / resume / shutdown)
        self.change_generation = 0    # explicit send/model/effort/resume changes that may fix a rejection
        self.turn_rejection = None    # rejected request generations; parked until either one changes
        self.note = ""                # postal working-note
        self.lock = threading.RLock()  # queue/turn/worker/state fields; lifecycle calls can nest
        self.norm_lock = threading.Lock()  # the turn worker and global pump share one normalizer


class CodexBackend:
    def __init__(self, state_dir, notify=None, poke=None, push=None, push_session=None,
                 codex_bin=None, log=None, client_factory=None):
        self.state = Path(state_dir)
        self.root = self.state / "codex"
        self.projects = self.root / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.notify = notify or (lambda *a, **k: None)
        self.poke = poke or (lambda: None)
        self.push = push or (lambda: None)
        self.push_session = push_session or (lambda sid: None)
        self.codex_bin = codex_bin
        self.log = log or (lambda m: sys.stderr.write("codex-backend: %s\n" % m))
        self._client_factory = client_factory   # tests inject a fake; None → real CodexClient
        self._client = None
        self._client_err = None       # why the client can't be built/authed (str), or None
        self._client_retry_at = 0.0
        self._client_failures = 0
        self._client_generation = 0   # successful app-server client installations
        self._catalog = None          # model_catalog() cache — fetched once per process
        self._client_lock = threading.Lock()
        self._sessions = {}           # sid → _Session
        self._sessions_lock = threading.RLock()
        self._reg_lock = threading.Lock()
        self._load_registry()
        # A kernel restart must not strand a durable backend queue until the user happens to send
        # again. Re-arm every live queued session immediately; client retry backoff keeps failures cool.
        for _, s in self._session_items():
            with s.lock:
                recover = bool(s.queue) and not s.dead
            if recover:
                self._ensure_worker(s)
                s.kick.set()

    # ── registry persistence ─────────────────────────────────────────────────────────────────────
    def _reg_path(self):
        return self.root / "registry.json"

    def _reg_lock_path(self):
        # Persistent sidecar, never unlinked: unlinking can split contenders across two lock inodes.
        return self.root / "registry.lock"

    def _load_registry(self):
        try:
            rows = json.loads(self._reg_path().read_text())
            if not isinstance(rows, dict):
                raise ValueError("registry root is not an object")
        except FileNotFoundError:
            rows = {}
        except Exception as e:
            # as LOUD at load as saves are: _save_registry refuses to overwrite an unreadable
            # registry, but a silent {} here made every session vanish at boot and surface later
            # as unrelated-looking spawn/send errors (2026-08-14 review)
            self.log("codex registry unreadable at load — existing sessions will be missing "
                     "until it is repaired: %s" % e)
            rows = {}
        with self._sessions_lock:
            for sid, r in rows.items():
                if not isinstance(r, dict) or not isinstance(r.get("tid"), str):
                    self.log("ignoring malformed Codex registry row: %s" % sid)
                    continue
                s = _Session(sid, r["tid"], r.get("name", ""), r.get("cwd", ""),
                             r.get("model", ""), r.get("effort", ""), r.get("color", ""))
                saved_mode = r.get("mode", "sandboxed")
                if saved_mode in MODES:
                    s.mode = saved_mode
                else:
                    self.log("unknown Codex mode in registry; retaining sandboxed mode")
                s.dead = bool(r.get("dead"))
                queue_entries = self._registry_queue_entries(sid, r.get("queue"))
                s.queue = [entry["text"] for entry in queue_entries]
                s.queue_ids = [entry["id"] for entry in queue_entries]
                s.note = r.get("note", "")
                s.launch_error = r.get("launchError") if isinstance(r.get("launchError"), dict) else None
                self._sessions[sid] = s

    def _session(self, sid):
        with self._sessions_lock:
            return self._sessions.get(sid)

    def _session_items(self):
        with self._sessions_lock:
            return list(self._sessions.items())

    def _put_session(self, s):
        with self._sessions_lock:
            self._sessions[s.sid] = s

    _names_lock = threading.Lock()   # serializes this backend's names/<sid> read-modify-writes

    @staticmethod
    def _legacy_queue_id(sid, index, text, repair=""):
        """A deterministic identity lets every overlapping loader agree on old string-only rows."""
        seed = "%s\0%d\0%s\0%s" % (sid, index, text, repair)
        return "legacy-%s" % uuidlib.uuid5(uuidlib.NAMESPACE_URL, seed).hex

    @classmethod
    def _registry_queue_entries(cls, sid, raw_queue):
        """Canonical [{id,text}] entries, including lazy migration of the former [text] schema."""
        if not isinstance(raw_queue, list):
            return []
        entries, used = [], set()
        for index, raw in enumerate(raw_queue):
            if isinstance(raw, str):
                text = raw
                entry_id = cls._legacy_queue_id(sid, index, text)
            elif isinstance(raw, dict):
                text, entry_id = raw.get("text"), raw.get("id")
            else:
                continue
            if not isinstance(text, str) or not text:
                continue
            if not isinstance(entry_id, str) or not entry_id or entry_id in used:
                # Repair malformed/duplicate ids deterministically so two processes still agree.
                entry_id = cls._legacy_queue_id(sid, index, text, str(entry_id or "repair"))
                salt = 0
                while entry_id in used:
                    salt += 1
                    entry_id = cls._legacy_queue_id(sid, index, text, "repair-%d" % salt)
            used.add(entry_id)
            entries.append({"id": entry_id, "text": text})
        return entries

    @staticmethod
    def _registry_snapshot(s):
        with s.lock:
            if len(s.queue_ids) != len(s.queue):
                raise RuntimeError("Codex in-memory queue identity invariant failed for %s" % s.sid)
            return {"tid": s.tid, "name": s.name, "cwd": s.cwd,
                    "model": s.model, "effort": s.effort, "mode": s.mode, "dead": s.dead,
                    "queue": [{"id": entry_id, "text": text}
                              for entry_id, text in zip(s.queue_ids, s.queue)],
                    "note": s.note, "color": s.color,
                    "launchError": s.launch_error}

    def _registry_rows_for_update(self):
        try:
            rows = json.loads(self._reg_path().read_text())
        except FileNotFoundError:
            return {}
        except Exception as e:
            # Never turn corruption into an empty registry and overwrite every durable queue.
            raise RuntimeError("Codex registry is unreadable: %s" % e) from e
        if not isinstance(rows, dict):
            raise RuntimeError("Codex registry root is not an object")
        return rows

    def _write_registry_locked(self, rows):
        """Atomic, durable write. Caller owns the persistent cross-process sidecar lock."""
        tmp = self._reg_path().with_name(
            "registry.tmp.%d.%s" % (os.getpid(), uuidlib.uuid4().hex[:8]))
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(rows, indent=1))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._reg_path())
            try:
                dfd = os.open(str(self.root), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass                       # some filesystems do not support directory fsync
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def _save_registry(self, s, *, fields=(), create=False, queue_append=None, queue_ack=None):
        """Apply ONE row transaction against the latest on-disk registry.

        A former whole-memory snapshot let an overlapping old kernel erase a newer kernel's queued
        sends during an unrelated rename/save. The thread lock serializes this backend; flock
        serializes processes. Under both, reload the authoritative file and touch only the named
        metadata fields. Queue mutations are operations over stable entry ids: append one newly
        accepted send, or remove the exact ids whose turn/start was ACKed. Thus a concurrent append
        survives an ACK even when its text repeats an earlier send. Every production mutator retains
        its session RLock through this transaction, so same-process snapshots cannot commit out of
        order. Snapshotting still happens before the registry lock, so no reverse lock edge exists.
        """
        allowed = {"tid", "name", "cwd", "model", "effort", "mode", "dead", "note", "color",
                   "launchError"}
        fields = set(fields)
        unknown = fields - allowed
        if unknown:
            raise ValueError("unknown Codex registry fields: %s" % sorted(unknown))
        snapshot = self._registry_snapshot(s)  # before registry locks: no reg-lock ↔ session-lock cycle
        ack_mismatch = False
        with self._reg_lock:
            lock_fd = os.open(str(self._reg_lock_path()), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                rows = self._registry_rows_for_update()
                current = rows.get(s.sid)
                reconstructed = not isinstance(current, dict) or not isinstance(current.get("tid"), str)
                if create or reconstructed:
                    row = dict(snapshot)
                else:
                    row = dict(current)
                    for field in fields:
                        row[field] = snapshot[field]

                # Normalizing every touched row lazily migrates the former raw-string queue schema.
                queue_now = self._registry_queue_entries(s.sid, row.get("queue"))
                if queue_append is not None:
                    if not (isinstance(queue_append, dict)
                            and isinstance(queue_append.get("id"), str)
                            and queue_append.get("id")
                            and isinstance(queue_append.get("text"), str)
                            and queue_append.get("text")):
                        raise ValueError("Codex queue append requires a nonempty {id,text} entry")
                    append_entry = {"id": queue_append["id"], "text": queue_append["text"]}
                    prior = next((entry for entry in queue_now
                                  if entry["id"] == append_entry["id"]), None)
                    if prior is None:
                        queue_now.append(append_entry)
                    elif prior != append_entry:
                        raise RuntimeError("Codex queue id collision for %s" % s.sid)
                if queue_ack is not None:
                    batch_ids = list(queue_ack)
                    if not all(isinstance(entry_id, str) and entry_id for entry_id in batch_ids):
                        raise ValueError("Codex queue ACK requires stable entry ids")
                    if [entry["id"] for entry in queue_now[:len(batch_ids)]] == batch_ids:
                        del queue_now[:len(batch_ids)]
                        row["queue"] = queue_now
                    elif queue_now:
                        # Preserve at-least-once delivery on an overlap instead of deleting a queue
                        # whose provenance we cannot prove. The mismatch is actionable and visible.
                        ack_mismatch = True
                row["queue"] = queue_now
                rows[s.sid] = row
                self._write_registry_locked(rows)
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
        # The ACK caller may deliberately retain s.lock through this transaction. Return the warning
        # bit so it can invoke the arbitrary log callback only after every persistence lock is gone.
        return ack_mismatch

    # ── client lifecycle ─────────────────────────────────────────────────────────────────────────
    def available(self):
        """Can this backend actually RUN a session right now? (The creation gate — mirrors
        _sdk_ready's contract.) Building the client is the real probe; a failure is surfaced and
        retried after bounded exponential backoff, never in a hot loop."""
        return self._get_client() is not None

    def _get_client(self):
        with self._client_lock:
            if self._client is not None:
                return self._client
            if time.monotonic() < self._client_retry_at:
                return None           # remembered only until the retry deadline, not for the process
            candidate = None
            try:
                if self._client_factory:
                    candidate = self._client_factory()
                else:
                    if not ensure_codex_sdk(self.state):
                        raise RuntimeError(SETUP_HINT)
                    from openai_codex.client import CodexClient, CodexConfig
                    cfg = _codex_config(CodexConfig, self.codex_bin, self.state)
                    candidate = CodexClient(config=cfg, approval_handler=self._handle_approval)
                    candidate.start()
                    candidate.initialize()
                self._check_auth(candidate)
                self._client = candidate
                self._client_err = None
                self._client_retry_at = 0.0
                self._client_failures = 0
                self._client_generation += 1
                # A replacement app-server can make a previously deterministic rejection obsolete.
                # Wake parked queued workers; their generation gate decides whether to retry.
                for _, s in self._session_items():
                    s.kick.set()
                threading.Thread(target=self._global_pump, args=(candidate,), daemon=True,
                                 name="codex-pump").start()
                return candidate
            except Exception as e:
                self._record_client_failure_locked(e, candidate)
                return None

    def _record_client_failure_locked(self, error, candidate=None):
        """Record one failed client generation. Caller owns _client_lock."""
        self._client_err = str(error) or error.__class__.__name__
        self._client_failures += 1
        delay = min(CLIENT_RETRY_MAX,
                    CLIENT_RETRY_MIN * (2 ** min(self._client_failures - 1, 8)))
        self._client_retry_at = time.monotonic() + delay
        if candidate is None:
            candidate = self._client
        if candidate is self._client and self._client is not None:
            # Invalidation is itself a generation edge. The global pump wakes queued workers after
            # releasing this lock, so a permanently parked request can build the replacement client
            # automatically rather than waiting for an unrelated user action.
            self._client_generation += 1
            self._client = None
        if candidate is not None:
            try:
                candidate.close()
            except Exception:
                pass
        self.log("client unavailable: %s (retry in %.2fs)" % (self._client_err, delay))

    def _client_retry_remaining(self):
        with self._client_lock:
            return max(0.0, self._client_retry_at - time.monotonic())

    def _client_generation_now(self):
        with self._client_lock:
            return self._client_generation

    def _client_generation_for(self, client):
        with self._client_lock:
            # If it was invalidated between _get_client and turn/start, use a deliberately stale
            # sentinel. The worker then sees the generation mismatch and rebuilds automatically.
            return self._client_generation if self._client is client else -1

    def _handle_approval(self, method, params):
        """Never inherit the SDK's permissive default for requests routed to the client.

        Auto review happens inside Codex. A request reaching this callback needs a human,
        but the pinned SDK invokes it on its single reader thread. Waiting for UI input
        here would stall responses and notifications for every session, including interrupts.
        Decline supported requests immediately and make the limitation visible.
        """
        text = ("Codex requested input or manual approval that romp cannot handle yet. "
                "The request was declined; no permission was granted.")
        self.log("manual Codex request declined: %s" % method)
        try:
            self.notify("chat", {"type": "warn", "text": text})
        except Exception:
            pass  # a disconnected UI must never turn a denial into an approval
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        # Unknown reply schemas must fail the transport rather than accidentally grant access.
        raise RuntimeError("Unsupported Codex server request: %s" % method)

    def _check_auth(self, client):
        """A missing `codex login` must surface as text on the session, not as a hung turn."""
        try:
            acct = client.account_read()
        except Exception as e:
            self.log("account_read failed: %s" % e)
            return
        needs = getattr(acct, "requires_openai_auth", None)
        if needs and not getattr(acct, "account", None):
            raise RuntimeError(LOGIN_HINT)

    def _global_pump(self, client):
        """Drain notifications NOT routed to a registered turn (thread status, rate limits, token
        usage between turns). Feeds the owning session's normalizer so nothing is dropped."""
        while True:
            try:
                n = client.next_notification()
            except Exception as e:
                self.log("global pump stopped: %s" % e)
                with self._client_lock:
                    if self._client is client:
                        self._record_client_failure_locked(e, client)
                for _, s in self._session_items():
                    with s.lock:
                        queued = bool(s.queue) and not s.dead
                    if queued:
                        self._ensure_worker(s)
                        s.kick.set()
                return
            try:
                p = _dump(getattr(n, "payload", None))
                tid = p.get("threadId")
                s = next((s for _, s in self._session_items() if s.tid == tid), None)
                if s:
                    wrote = False
                    with s.norm_lock:
                        # Placeholder recovery replaces the normalizer under this same lock. Recheck
                        # after acquiring it: a pre-lock `s.norm` test could race to None here.
                        if s.norm:
                            recs = s.norm.handle(getattr(n, "method", ""), p)
                            if recs:
                                self._append(s, recs)
                                wrote = True
                    if wrote:                     # notify OUTSIDE norm_lock (see _append)
                        self.poke()
                        self.push_session(s.sid)
            except Exception:
                self.log("global pump: %s" % traceback.format_exc())

    # ── the materialized transcript ──────────────────────────────────────────────────────────────
    def transcript_path(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            cwd, tid = s.cwd, s.tid
        d = self.projects / _enc_cwd(cwd)
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s.jsonl" % tid)

    def _ensure_norm(self, s):
        with s.norm_lock:
            if s.norm is None:
                path = self.transcript_path(s.sid)
                last, seen = _tail_state(path)
                s.norm = _events.ThreadNormalizer(s.tid, cwd=s.cwd, model=s.model,
                                                  version="codex", last_uuid=last,
                                                  seen_uuids=seen)
            return s.norm

    def _append(self, s, recs):
        """File IO + echo-prune ONLY — never notifies. Every caller holds s.norm_lock, and the
        kernel's push_session synchronously re-enters live_sessions(), which takes every session's
        norm_lock: a push from in here self-deadlocked the worker on its first appended record and
        wedged the whole liveness merge behind it (2026-08-14 review, reproduced live). Callers
        poke/push AFTER releasing the lock — and an RLock would not save a push-under-lock: two
        workers pushing concurrently AB-BA across their sessions' locks."""
        path = self.transcript_path(s.sid)
        with open(path, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # a landed user record replaces its optimistic echo (uuid-independent: match by text)
        landed = {self._rec_text(r) for r in recs if r.get("type") == "user"}
        if landed:
            with s.lock:
                s.echoes = [e for e in s.echoes if e["text"] not in landed]

    @staticmethod
    def _rec_text(rec):
        c = (rec.get("message") or {}).get("content")
        if isinstance(c, list):
            return " ".join(b.get("text", "") for b in c
                            if isinstance(b, dict) and b.get("type") == "text").strip()
        return (c or "").strip() if isinstance(c, str) else ""

    # ── liveness / identity ──────────────────────────────────────────────────────────────────────
    def owns(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            return not s.dead

    def live_sessions(self):
        out = {}
        for sid, s in self._session_items():
            with s.lock:
                if s.dead:
                    continue
                row = {"state": s.state, "model": s.model, "effort": s.effort,
                       "mode": s.mode, "since": s.since, "context": None,
                       "compactPct": None, "backend": "codex", "name": s.name,
                       "cwd": s.cwd, "color": s.color or None}
            with s.norm_lock:
                if s.norm and s.norm.context and s.norm.context[1]:
                    used, window = s.norm.context
                    row["context"] = max(0, min(100, round(100 * used / window)))
            out[sid] = row
        return out

    def busy(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            return None if s.dead else bool(s.turn_id or s.queue)

    # ── control ──────────────────────────────────────────────────────────────────────────────────
    def send(self, sid, text):
        s = self._session(sid)
        if not s or not text:
            return False
        c = self._get_client()
        with s.lock:
            if s.dead:
                return False
            s.echoes.append({"text": text.strip(), "t": time.time(),
                             "uuid": "echo-%s" % uuidlib.uuid4().hex[:8]})
            turn_id = s.turn_id
            tid = s.tid
        if c is not None and turn_id:
            # mid-turn: steer, with the active turn as precondition; a race with the turn's end
            # falls through to the queue (the worker delivers it in the next turn)
            try:
                c.turn_steer(tid, turn_id, [{"type": "text", "text": text}])
                return True
            except Exception:
                pass
        with s.lock:
            if s.dead:
                s.echoes = [e for e in s.echoes if e["text"] != text.strip()]
                return False
            entry_id = "q-%s" % uuidlib.uuid4().hex
            s.queue.append(text)
            s.queue_ids.append(entry_id)
            s.change_generation += 1
            # Keep append order identical in memory and on disk. _save_registry snapshots this RLock
            # reentrantly before taking either registry lock; it never takes a session lock afterward.
            self._save_registry(s, queue_append={"id": entry_id, "text": text})
        self._ensure_worker(s)
        s.kick.set()
        return True

    def interrupt(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            if s.dead or not s.turn_id:
                return False
            tid, turn_id = s.tid, s.turn_id
        c = self._client
        if c is None:
            return False
        try:
            c.turn_interrupt(tid, turn_id)
            return True
        except Exception as e:
            self.log("interrupt %s: %s" % (s.name, e))
            return False

    def set_model(self, sid, value):
        s = self._session(sid)
        if not s or not value or not str(value).startswith("gpt"):
            # a Claude alias (the other engine's vocabulary — a mis-aimed menu or script) would ride
            # the next turn_start straight into a 400 that breaks the session's next turn; refusing
            # here keeps the failure a loud kernel warn instead (2026-08-14 UI review)
            return False
        with s.lock:
            s.model = value                # applied on the next turn_start; Codex persists it
            s.change_generation += 1
            queued = bool(s.queue) and not s.dead
            self._save_registry(s, fields=("model",))
        with s.norm_lock:
            if s.norm:
                # A later set_model may have completed while this caller waited for norm_lock.
                # Publish the current persisted value, never this caller's possibly-stale argument.
                with s.lock:               # norm_lock → session lock matches _ensure_norm/_append
                    s.norm.model = s.model
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def model_catalog(self):
        """[{value,label}] for the UI's model picker — the app-server's own model list (the ONE
        authoritative source), fetched once per process and cached. [] when the client is
        unavailable (the picker then shows nothing rather than another vendor's list). A plan
        account may still refuse some listed models per turn — that failure surfaces loudly as
        the turn's error card, and switching back is one click."""
        if self._catalog is not None:
            return self._catalog
        c = self._get_client()
        if c is None:
            return []
        try:
            ms = c.model_list()
            self._catalog = [{"value": m.id, "label": getattr(m, "display_name", None) or m.id}
                             for m in (getattr(ms, "data", None) or [])
                             if not getattr(m, "hidden", False)]
        except Exception as e:
            self.log("model_list failed: %s" % e)
            return []
        return self._catalog

    def set_mode(self, sid, mode):
        s = self._session(sid)
        if not s or mode not in MODES or not s.mode_lock.acquire(blocking=False):
            return False
        try:
            with s.lock:
                if s.dead:
                    return False
                previous = s.mode
                s.mode = mode
                try:
                    self._save_registry(s, fields=("mode",))
                except BaseException:
                    s.mode = previous
                    raise
                s.change_generation += 1
                queued = bool(s.queue)
            if queued:
                self._ensure_worker(s)
                s.kick.set()
        finally:
            s.mode_lock.release()
        self.push_session(sid)
        return True

    def set_effort(self, sid, value):
        s = self._session(sid)
        if not s or value not in EFFORTS:
            return False                   # max/ultracode are Claude-only — refuse loudly
        with s.lock:
            s.effort = value
            s.change_generation += 1
            queued = bool(s.queue) and not s.dead
            self._save_registry(s, fields=("effort",))
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def set_fast(self, sid, value):
        return False   # no Codex equivalent

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────────
    def _publish_spawn_name(self, s, bg="", fg=""):
        """spawn's names/ write, transactional: the durable row already landed, so a raising
        names write would leave a live row that holds no name — a retry of the same name then
        mints a duplicate live session (the v1.3.12 audit's hole, re-opened on exactly this
        raising path — the r29 verification). Retire the row, loudly."""
        try:
            self._write_name(s, bg, fg)
        except BaseException:
            with s.lock:
                s.dead = True
                try:
                    self._save_registry(s, fields=("dead",))
                except Exception as e2:
                    self.log("codex spawn: could not retire %s after its name write failed (%s)"
                             % (s.sid, e2))
            with self._sessions_lock:
                self._sessions.pop(s.sid, None)
            raise

    def _write_name(self, s, bg="", fg=""):
        """The shared identity/discovery file names/<sid> (name, cwd, bg, fg) — the same four-field
        format both other backends write, so name/identity surfaces read Codex sessions for free.
        Discovery itself finds Codex transcripts via the codex registry, not this file.
        ATOMIC (tmp + os.replace, like sdk_backend.write_name): the old in-place write_text was
        torn-readable mid-write and its crash residue armed a decode landmine every later reader
        tripped on; the corrupt-read catch below also HEALS that residue by rewriting whole (the
        r31 verification). The backend lock serializes backend writers — kernel-side writers
        (_set_session_color, _set_palette) write atomically themselves, so cross-module races
        degrade to last-writer-wins of a whole valid file, never a torn one."""
        d = self.state / "names"
        d.mkdir(parents=True, exist_ok=True)
        with self._names_lock:
            try:
                old = (d / s.sid).read_text().rstrip("\n").split("\t")
            except (OSError, UnicodeDecodeError):
                old = []
            bg = bg or (old[2] if len(old) > 2 else "")
            fg = fg or (old[3] if len(old) > 3 else "")
            tmp = d / (s.sid + ".tmp")
            try:
                tmp.unlink(missing_ok=True)   # a planted FIFO at the fixed staging name blocks
            except OSError:                   # open(); a leaked stray must not shadow the write
                pass
            try:
                tmp.write_text("%s\t%s\t%s\t%s\n" % (s.name, s.cwd, bg, fg))
                os.replace(str(tmp), str(d / s.sid))
            finally:
                tmp.unlink(missing_ok=True)   # never LEAK the staging file: names consumers
                #                               read the dir, and a stray .tmp rendered as a
                #                               phantom session (the r32 verification)

    def spawn(self, name, cwd, bg="", fg="", sid=None, auth=""):
        sid = sid or str(uuidlib.uuid4())
        c = self._get_client()
        if c is None:
            # the entry still exists so the failure is VISIBLE on the lane (launch_error),
            # never a silently-missing session
            s = _Session(sid, "pending-%s" % sid[:8], name, cwd)
            s.launch_error = {"text": self._client_err or SETUP_HINT, "at": time.time(),
                              "limit": False}
            with s.lock:
                self._put_session(s)
                try:
                    self._save_registry(s, create=True)
                except BaseException:
                    # no durable row → no in-memory row: the phantom rendered as a live lane
                    # with NO names file, so a retry of the same name minted a duplicate — the
                    # r27 hole re-opened on exactly the raising path (the r28 verification)
                    with self._sessions_lock:
                        self._sessions.pop(sid, None)
                    raise
            self._publish_spawn_name(s)    # a LIVE launch-error row without a shared name let a
            #                                retry mint a duplicate live "web" (the v1.3.12 audit)
            return sid
        try:
            resp = c.thread_start({"cwd": cwd, **_approval_params(),
                                   **_execution_permissions(cwd, thread_start=True)})
            tid = resp.thread.id
            model = getattr(resp, "model", "") or ""
        except Exception as e:
            s = _Session(sid, "failed-%s" % sid[:8], name, cwd)
            s.launch_error = {"text": "codex thread/start failed: %s" % e, "at": time.time(),
                              "limit": False}
            with s.lock:
                self._put_session(s)
                try:
                    self._save_registry(s, create=True)
                except BaseException:
                    with self._sessions_lock:
                        self._sessions.pop(sid, None)
                    raise
            self._publish_spawn_name(s)    # same rule as the client-missing branch above
            return sid
        s = _Session(sid, tid, name, cwd, model=model, color=bg)
        s.loaded = True
        with s.lock:
            self._put_session(s)
            try:
                self._save_registry(s, create=True)
            except BaseException:
                with self._sessions_lock:
                    self._sessions.pop(sid, None)
                raise
        self._ensure_norm(s)
        # touch the materialized transcript NOW: discovery lists real files, and an empty jsonl
        # parses to an empty session — the tab opens immediately instead of waiting for turn one
        self.transcript_path(sid).touch()
        self._publish_spawn_name(s, bg, fg)
        self.push()
        return sid

    def resume(self, name, sid, cwd=None):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            prior = (s.dead, s.loaded, s.name, s.cwd, s.state, s.since, s.change_generation)
            s.dead = False
            s.loaded = False               # the worker thread/resumes before the next turn
            if name and not s.name:
                s.name = name              # ADVISORY only: adopting the caller's echo overwrote
            #                                a rename that landed while the revive was in flight,
            #                                silently reverting the acked new name in the durable
            #                                registry (the r37 verification); the registry's own
            #                                name is fresher by construction
            if cwd:
                s.cwd = cwd
            s.state = "waiting"
            s.since = time.time()
            s.change_generation += 1
            queued = bool(s.queue)
            try:
                self._save_registry(s, fields=("dead", "name", "cwd"))
            except BaseException:
                # roll the flip back: with dead=False already published in memory, a FAILED
                # revive rendered a live lane beside its own reviveFailed message, and the next
                # kernel restart silently killed it again (the r28 verification, executed)
                (s.dead, s.loaded, s.name, s.cwd, s.state, s.since,
                 s.change_generation) = prior
                raise
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def kill(self, sid):
        s = self._session(sid)
        if not s:
            return False
        save_error = None
        with s.lock:
            s.dead = True
            turn_id, tid, worker = s.turn_id, s.tid, s.worker
            # Persist the lifecycle mutation before releasing the session lock. A concurrent resume
            # must order after this write instead of being overwritten by a delayed kill snapshot.
            try:
                self._save_registry(s, fields=("dead",))
            except Exception as e:
                save_error = e
        c = self._client
        if turn_id and c is not None:
            try:
                c.turn_interrupt(tid, turn_id)
            except Exception as e:
                self.log("kill interrupt %s: %s" % (s.name, e))
        s.kick.set()                       # wake a retry wait or idle worker so it can exit
        if worker and worker is not threading.current_thread():
            worker.join(WORKER_JOIN_TIMEOUT)
        worker_stopped = not worker or not worker.is_alive()
        if not worker_stopped:
            self.log("worker did not stop after kill: %s" % s.name)
        if worker_stopped:
            drained = False
            with s.norm_lock:
                held = s.norm.drain() if s.norm else []
                if held:
                    self._append(s, held)  # never eat a held final message; serialize file appends
                    drained = True
            if drained:                    # notify OUTSIDE norm_lock (see _append)
                self.poke()
                self.push_session(s.sid)
        if save_error is not None:
            raise save_error
        return True

    def rename(self, sid, new_name):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            old_name = s.name
            s.name = new_name
            tid = s.tid
            try:
                self._save_registry(s, fields=("name",))
            except BaseException:
                # durable registry FIRST, and a raising write publishes NOTHING: the old order
                # moved the shared names file and the in-memory name before the raise, so three
                # stores disagreed under a false "keeps its old name" message (the r28
                # verification — the r28 kernel-layer reorder missed this layer)
                s.name = old_name
                raise
            nf = self.state / "names" / s.sid
            try:
                old_line = nf.read_bytes()
            except OSError:
                old_line = None
            try:
                self._write_name(s)       # keep the shared identity file in sync (colours preserved)
            except BaseException:
                s.name = old_name         # compensate: the registry write above is re-run with
                #                           the old name so the stores stay agreed; the raise
                #                           still reaches the caller (loud)
                if old_line is not None:
                    try:
                        nf.write_bytes(old_line)   # write_text TRUNCATES before it fails — an
                        #                            ENOSPC left the identity file (and its
                        #                            colours) empty (the r29 verification)
                    except OSError as e2:
                        self.log("codex rename: names/%s left truncated by a failed write (%s)"
                                 % (s.sid, e2))
                else:
                    try:
                        nf.unlink(missing_ok=True)  # _write_name may have CREATED a partial file
                        #                             holding the NEW name — a failed rename must
                        #                             not stay published (the r30 verification)
                    except OSError as e2:
                        self.log("codex rename: a partial names/%s could not be removed (%s)"
                                 % (s.sid, e2))
                try:
                    self._save_registry(s, fields=("name",))
                except Exception as e2:
                    # a silent pass here hid the ONE moment the code knows the stores disagree:
                    # the durable registry alone holds the NEW name and will apply the rename
                    # the caller was told failed at the next restart (the r29 verification)
                    self.log("codex rename compensation failed for %s: the registry alone holds "
                             "the new name and will apply it at the next restart (%s)"
                             % (s.sid, e2))
                raise
        c = self._client
        if c is not None:
            try:
                c.thread_set_name(tid, new_name)
            except Exception:
                pass                       # cosmetic on the Codex side; romp's registry is the truth
        return True

    # ── the per-session worker: queue → turns → records ────────────────────────────────────────
    def _ensure_worker(self, s):
        with s.lock:
            if s.worker and s.worker.is_alive():
                return
            s.worker = threading.Thread(target=self._work, args=(s,), daemon=True,
                                        name="codex-%s" % s.name)
            s.worker.start()

    def _prepare_thread(self, s, c):
        """Resume a durable thread, or turn a visible pending/failed placeholder into a real one."""
        with s.lock:
            if s.dead:
                return False
            tid, cwd = s.tid, s.cwd
            create = tid.startswith("pending-") or tid.startswith("failed-")
        if create:
            resp = c.thread_start({"cwd": cwd, **_approval_params(s.mode),
                                   **_execution_permissions(cwd, thread_start=True)})
            with s.lock:
                if s.dead:
                    return False
                prior = (s.tid, s.model, s.loaded)
                s.tid = resp.thread.id
                s.model = getattr(resp, "model", "") or s.model
                s.loaded = True
                try:
                    self._save_registry(s, fields=("tid", "model"))
                except BaseException:
                    # publish NOTHING on a raise: with the real tid only in memory, every retry
                    # took the resume path and never re-saved it — the next kernel restart
                    # loaded 'pending-…' and silently started a FRESH Codex thread (the r29
                    # verification). Rolled back, the loud retry re-runs thread_start; an
                    # orphaned server-side thread beats a silently forked conversation.
                    (s.tid, s.model, s.loaded) = prior
                    raise
            with s.norm_lock:
                s.norm = None
            self._ensure_norm(s)
            self.transcript_path(s.sid).touch()
            nf = self.state / "names" / s.sid
            try:
                old_line = nf.read_bytes()
            except OSError:
                old_line = None
            try:
                self._write_name(s)
            except (OSError, UnicodeDecodeError) as e:
                # the thread is HEALTHY — failing the turn over a cosmetic identity write would
                # be worse (a decode failure from crash residue sailed through an OSError-only
                # catch and DID fail the turn, unhealed forever — the r31 verification) — but
                # the write must not leave residue either. Restore the old line, or remove the
                # partial file.
                try:
                    if old_line is not None:
                        nf.write_bytes(old_line)
                        self.log("codex: names/%s write failed after thread start (%s) — the "
                                 "identity file was restored; it refreshes on the next rename"
                                 % (s.sid, e))
                    else:
                        nf.unlink(missing_ok=True)
                        self.log("codex: names/%s could not be published after thread start "
                                 "(%s) — the session runs UNNAMED on shared surfaces until a "
                                 "rename lands; a same-name create may collide meanwhile"
                                 % (s.sid, e))
                except OSError:
                    self.log("codex: names/%s left in an unknown state by a failed write (%s)"
                             % (s.sid, e))
            self.push()
            return True
        c.thread_resume(tid, {"cwd": cwd, **_approval_params(s.mode),
                              **_execution_permissions(cwd, thread_start=True)})
        with s.lock:
            if s.dead:
                return False
            s.loaded = True
        return True

    def _work(self, s):
        retry_delay = CLIENT_RETRY_MIN
        try:
            while True:
                s.kick.wait()
                s.kick.clear()
                while True:
                    with s.lock:
                        if s.dead:
                            return
                        queued = bool(s.queue)
                        rejection = s.turn_rejection
                        change_generation = s.change_generation
                    if not queued:
                        break
                    client_generation = self._client_generation_now()
                    if rejection == (change_generation, client_generation):
                        # A background wake/push is not evidence that the rejected request changed.
                        # Park without a timer; the four explicit session changes above or a newly
                        # installed client generation set kick and make this tuple differ.
                        break
                    if rejection is not None:
                        with s.lock:
                            if s.turn_rejection == rejection:
                                s.turn_rejection = None
                    try:
                        progressed = self._run_turn(s)
                    except _PermanentRequestRejection as e:
                        self.log("%s rejected (%s): %s" % (e.operation, s.name, e))
                        try:
                            with s.lock:
                                s.launch_error = {"text": "codex %s rejected: %s" % (e.operation, e),
                                                  "at": time.time(), "limit": False}
                                s.state = "waiting"
                                s.turn_id = None
                                # Record the generations of the REJECTED request, not whatever is
                                # current after its RPC returned. A send/model change racing the RPC
                                # must remain a fresh kick and immediately retry the new request.
                                s.turn_rejection = (e.change_generation, e.client_generation)
                                self._save_registry(s, fields=("launchError",))
                        except Exception:
                            self.log("turn rejection registry save: %s" % traceback.format_exc())
                        self.push_session(s.sid)
                        break
                    except Exception as e:
                        self.log("turn failed (%s): %s" % (s.name, traceback.format_exc()))
                        try:
                            with s.lock:
                                s.launch_error = {"text": "codex turn failed: %s" % e,
                                                  "at": time.time(), "limit": False}
                                s.state = "waiting"
                                s.turn_id = None
                                self._save_registry(s, fields=("launchError",))
                        except Exception:
                            self.log("turn failure registry save: %s" % traceback.format_exc())
                        self.push_session(s.sid)
                        progressed = False
                    if progressed:
                        retry_delay = CLIENT_RETRY_MIN
                        continue
                    delay = max(retry_delay, self._client_retry_remaining())
                    retry_delay = min(CLIENT_RETRY_MAX, retry_delay * 2)
                    # Clear stale send kicks before the backoff. A concurrent kill sets dead and/or
                    # wakes this wait, so shutdown stays prompt while repeated sends cannot spin it.
                    s.kick.clear()
                    with s.lock:
                        if s.dead:
                            return
                    s.kick.wait(delay)
                    s.kick.clear()
        finally:
            with s.lock:
                if s.worker is threading.current_thread():
                    s.worker = None

    def _run_turn(self, s):
        # Mode changes take effect between turns. Nonblocking set_mode refuses while this
        # lock is held, including thread preparation and the turn/start acknowledgement gap.
        with s.mode_lock:
            return self._run_turn_in_mode(s)

    def _run_turn_in_mode(self, s):
        c = self._get_client()
        if c is None:
            try:
                with s.lock:
                    s.launch_error = {"text": self._client_err or SETUP_HINT, "at": time.time(),
                                      "limit": False}
                    self._save_registry(s, fields=("launchError",))
            except Exception:
                self.log("client failure registry save: %s" % traceback.format_exc())
            self.push_session(s.sid)
            return False                   # queue stays parked; worker retries after the deadline
        with s.lock:
            loaded = s.loaded
            prepare_change_generation = s.change_generation
        if not loaded:
            prepare_client_generation = self._client_generation_for(c)
            try:
                prepared = self._prepare_thread(s, c)
            except Exception as e:
                if _is_permanent_request_rejection(e):
                    raise _PermanentRequestRejection(
                        e, "thread preparation", prepare_change_generation,
                        prepare_client_generation) from e
                raise
            if not prepared:
                return True                # killed while the resume/create RPC was in flight
        norm = self._ensure_norm(s)
        with s.lock:
            if s.dead:
                return True
            batch = list(s.queue)           # retain the durable prefix until turn/start ACKs
            batch_ids = list(s.queue_ids)
            if len(batch_ids) != len(batch):
                raise RuntimeError("Codex in-memory queue identity invariant failed for %s" % s.sid)
            if not batch:
                return True
            params = {**_approval_params(s.mode), "cwd": s.cwd,
                      **_execution_permissions(s.cwd)}
            if s.model:
                params["model"] = s.model
            if s.effort:
                params["effort"] = s.effort
            tid = s.tid
            change_generation = s.change_generation
        # Do not hold the lifecycle lock across an app-server RPC: kill must stay prompt even when
        # turn/start itself stalls. Sends may append meanwhile; the snapshotted prefix stays in place.
        client_generation = self._client_generation_for(c)
        try:
            started = c.turn_start(tid, [{"type": "text", "text": t} for t in batch], params)
        except Exception as e:
            if _is_permanent_request_rejection(e):
                raise _PermanentRequestRejection(
                    e, "turn", change_generation, client_generation) from e
            raise
        turn_id = started.turn.id
        ack_persisted = False
        try:
            with s.lock:
                if s.queue[:len(batch)] != batch or s.queue_ids[:len(batch_ids)] != batch_ids:
                    raise RuntimeError("Codex send queue prefix changed during turn/start")
                del s.queue[:len(batch)]
                del s.queue_ids[:len(batch_ids)]
                s.turn_id = turn_id
                s.state = "working"
                s.since = time.time()
                s.launch_error = None
                s.turn_rejection = None
                killed_during_start = s.dead
                try:
                    ack_mismatch = self._save_registry(
                        s, fields=("launchError",), queue_ack=batch_ids)
                except Exception:
                    # turn/start already succeeded, but the durable ACK did not. Restore the exact
                    # prefix before retrying so this process agrees with the still-queued disk row.
                    s.queue[:0] = batch
                    s.queue_ids[:0] = batch_ids
                    raise
                ack_persisted = True
            if ack_mismatch:
                self.log("registry queue ACK mismatch for %s; preserving durable queue" % s.sid)
            self.push_session(s.sid)
            if killed_during_start:
                try:
                    c.turn_interrupt(tid, turn_id)
                except Exception as e:
                    self.log("kill interrupt %s: %s" % (s.name, e))
            while True:
                n = c.next_turn_notification(turn_id)
                method = getattr(n, "method", "")
                wrote = False
                with s.norm_lock:
                    recs = norm.handle(method, _dump(getattr(n, "payload", None)))
                    if recs:
                        self._append(s, recs)
                        wrote = True
                if wrote:                          # notify OUTSIDE norm_lock (see _append)
                    self.poke()
                    self.push_session(s.sid)
                if method == "turn/completed":
                    break
        except Exception:
            if not ack_persisted:
                # The request is still durable, so prevent an untracked acknowledged turn from
                # continuing alongside its retry. unregister in finally always releases routing.
                try:
                    c.turn_interrupt(tid, turn_id)
                except Exception as e:
                    self.log("unpersisted turn interrupt %s: %s" % (s.name, e))
            raise
        finally:
            try:
                c.unregister_turn_notifications(turn_id)
            except Exception:
                pass
            with s.lock:
                s.turn_id = None
                s.state = "waiting"
                s.since = time.time()
            self.poke()                    # the turn END is the kernel's cue (parked ops deliver on it): every
            self.push_session(s.sid)       # in-loop poke above fired while turn_id was set, i.e. busy() True
        return True

    # ── chat tail ────────────────────────────────────────────────────────────────────────────────
    def pending_queued(self, sid):
        s = self._session(sid)
        if not s:
            return []
        with s.lock:
            return list(s.queue)

    def live_atoms(self, sid):
        s = self._session(sid)
        if not s:
            return []
        with s.lock:
            return [{"type": "user", "uuid": e["uuid"], "session_id": sid, "fsid": s.tid,
                     "t": e["t"], "parentUuid": None, "author": "human",
                     "message": {"role": "user",
                                 "content": [{"type": "text", "text": e["text"]}]}}
                    for e in s.echoes]

    def prune_live(self, sid, tx_uuids, tx_user_texts=()):
        s = self._session(sid)
        if not s:
            return
        texts = {t.strip() for t in tx_user_texts or ()}
        with s.lock:
            s.echoes = [e for e in s.echoes
                        if e["uuid"] not in (tx_uuids or ()) and e["text"] not in texts]

    # ── ask picker (no Codex equivalent in phase 1) ─────────────────────────────────────────────
    def on_ask(self, sid, kind, payload=None):
        return False

    def current_ask(self, sid):
        return None

    # ── the loud degradations ────────────────────────────────────────────────────────────────────
    def launch_error(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            return s.launch_error

    def forwards_sends(self):
        return True    # sends steer mid-turn or queue; the kernel hands them over immediately

    def set_auth(self, sid, value):
        return False   # Codex auth is machine-global (codex login); no per-session pick

    def stop_task(self, sid, task_id):
        return False

    def mcp_status(self, sid):
        return [], ("Codex sessions load MCP servers from ~/.codex/config.toml; "
                    "per-session MCP controls aren't available here yet")

    def mcp_action(self, sid, name, action, enabled=True):
        return "Codex sessions load MCP servers from ~/.codex/config.toml; edit that file instead"

    def rewind_files(self, sid, uuid):
        return False

    # ── coordination ─────────────────────────────────────────────────────────────────────────────
    def working_note(self, sid):
        s = self._session(sid)
        if not s:
            return ""
        with s.lock:
            return s.note

    def set_working_note(self, sid, text):
        s = self._session(sid)
        if s:
            with s.lock:
                s.note = text or ""
                self._save_registry(s, fields=("note",))

    def wake(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            if s.dead or not s.queue:
                return False
        self._ensure_worker(s)
        s.kick.set()
        return True

    def deliver(self, sid, text):
        """Postal deliver-time wake: a busy session gets the banner steered into the running turn;
        an idle one gets a turn started with it — either way the mail is in front of the agent NOW."""
        return self.send(sid, text)
