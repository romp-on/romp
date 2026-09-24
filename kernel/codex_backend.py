#!/usr/bin/env python3
"""codex_backend — the Codex SessionBackend (plans/codex-backend.md).

Drives OpenAI Codex sessions through the official openai-codex Python SDK's sync CodexClient
(JSON-RPC to `codex app-server` over stdio) and materializes each thread as Claude-transcript-shaped
JSONL via kernel/codex_events.ThreadNormalizer, so romp's entire read side parses Codex sessions
unchanged. Duck-types the SessionBackend ABC exactly like SdkBackend does (the kernel loads backends
by file path; a conformance test asserts every abstract method exists).

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

- Mail: every thread carries the six postal tools as Codex DYNAMIC TOOLS (POSTAL_TOOL_SPECS); the app-server's
  `item/tool/call` lands in _handle_approval and the kernel's `postal` callable posts to the bus AS the session,
  so no credential enters the sandbox and Sandboxed and Auto mail alike (2026-09-19).

Everything Claude-only returns its documented empty value and the kernel stays loud about it:
set_fast/set_auth/stop_task/rewind_files → False, on_ask → False, current_ask → None.
"""
from __future__ import annotations

import copy
import errno
import fcntl
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid as uuidlib
import importlib.util
from pathlib import Path

HERE = Path(os.path.dirname(os.path.realpath(__file__)))
_ls_spec = importlib.util.spec_from_file_location("romp_loadsource", str(HERE / "loadsource.py"))
_ls_mod = importlib.util.module_from_spec(_ls_spec)
_ls_spec.loader.exec_module(_ls_mod)
load_source = _ls_mod.load_source   # file-path imports with load_module()'s sys.modules semantics (kernel/loadsource.py)
_events = load_source("romp_codex_events", HERE / "codex_events.py")
_runtime = load_source("romp_codex_runtime", HERE / "codex_runtime.py")
# The by-text KEY RULE (session_backend.echo_text_key): the one normalization under which an input echo's
# text is compared with a transcript record's, shared with the kernel's _atom_user_texts and
# SdkBackend.prune_live, so an echo whose text carries a trailing newline still lands. The kernel's own
# copy of that module when it is loaded (kernel.py loads it as romp_session_backend, and its
# _UnownedBackend subclasses that copy's ABC); otherwise the file is loaded under its OWN module name, as sdk_backend
# does, so re-executing the source never rebinds the ABC out from under a subclass.
_contract = (sys.modules.get("romp_session_backend")
             or load_source("romp_session_backend_keys", HERE / "session_backend.py"))
echo_text_key = _contract.echo_text_key

SDK_PIN = "openai-codex==0.144.4"     # bin/romp-codex-setup installs exactly this into codexvenv
# The postal tools ride fields this SDK's generated params do not describe (`dynamicTools`; `developerInstructions`
# it does) and its _params_dict passes a plain dict through unchanged. Live-probed 2026-09-19 on runtime 0.153.3
# through this client (tests/smoke_codex_live.py re-runs the wire-shape half): a bump that validates dicts against
# ThreadStartParams would drop the key silently, and the unit tests' fake accepts any dict, so re-run the smoke.
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

# ── the postal tools a Codex thread carries (2026-09-19) ─────────────────────────────────────────────────────
# A Codex session mails through TOOL CALLS the kernel performs on its behalf, never through a shell command. The bus
# and the kernel accept one credential, the kernel's serve token, and the sandbox profile above leaves the state
# directory that holds it unmounted on purpose (docs/codex.md, Sandboxing): `romp mail` inside the sandbox fails on
# credential, identity and reachability alike, and the one way it ever worked was an out-of-sandbox escalation that
# Codex's own reviewer allowed on some threads and refused on others. So the six postal tools are registered as Codex
# DYNAMIC TOOLS on thread/start (and again on thread/resume, harmless: the registration persists in the rollout's
# session_meta; probed live 2026-09-19 on runtime 0.153.3 through the pinned 0.144.4 client, whose _params_dict passes
# a plain dict through unchanged, its ThreadStartParams knowing no such field). Each call comes back as the
# `item/tool/call` server request, which _handle_approval routes to _postal_tool_call, and the kernel's callable
# (`postal`, kernel.py _codex_postal_call) posts to the bus over loopback AS the session: no credential enters the
# sandbox, the sender is the thread's own sid by construction, and Sandboxed and Auto behave the same (no approval or
# reviewer step touches a dynamic tool call in either mode: probed).
#
# POSTAL_TOOL_SPECS is a KEEP-IN-SYNC copy of the bus's MCP_TOOLS (bin/romp-postal-service) as function specs
# (`type`, `name`, `description`, `inputSchema`; a spec without a description refuses the whole thread/start), and
# POSTAL_INSTRUCTIONS of its MCP_INSTRUCTIONS minus the paragraph about Claude Code's own messaging (a Codex model has
# no such thing to be steered away from). A copy, not an import, on purpose: the bus is its own process behind the
# kernel's _restart_class boundary and imports nothing from kernel/, the kernel imports nothing from postal/, and the
# two already carry one text each way under that rule (_serve_token_read_or_mint, the same function in both).
# tests/test_codex_postal_tools.py loads the bus by path and pins the two equal, name for name and schema for schema.
POSTAL_TOOL_SPECS = [
    {"type": "function", "name": "send_message",
     "description": "Message a live romp session by name; it arrives at the end of the recipient's current turn. They share none of your context, so put the whole point in your first sentence. Live-only (see list_agents).",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "recipient romp session name"},
                                    "body": {"type": "string", "description": "message text"},
                                    "kind": {"type": "string", "enum": ["delegate", "coordinate", "question"],
                                             "description": "what this message does: delegate = the recipient owns the work now; coordinate = aligning or a heads-up, reply optional; question = you need an answer"},
                                    "tracked": {"type": "boolean",
                                                "description": "delegate only: a report-back handoff — the work stays tracked under YOU as the one view, with the recipient's live progress; their copy files as its satellite. Omit for a plain handoff the recipient owns outright."}},
                     "required": ["to", "body", "kind"]}},
    {"type": "function", "name": "check_inbox",
     "description": "Read and clear any messages other romp sessions have sent you. Messages are also delivered automatically at the end of each turn, so you rarely need to call this.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"type": "function", "name": "list_agents",
     "description": "List live romp sessions you can message (yours marked), each with its git branch and working-note. Check before editing shared files to avoid collisions; discount a note flagged '(idle now, claim may be stale)' and never wake an idle peer to ask if it still owns a file.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"type": "function", "name": "set_working",
     "description": "Publish what you're working on (files/surface) so peers steer clear; your branch shows automatically. Empty text clears it (romp also auto-clears once your work is done and the session idles).",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string", "description": "short note, e.g. 'editing postal/postal_service.py + the drain hook'"}}}},
    {"type": "function", "name": "check_sent",
     "description": "See your recently sent messages and whether each was read/acted on by the recipient yet, or is still pending — instead of asking 'did you get it?'.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"type": "function", "name": "recall_message",
     "description": "Withdraw a message that is still here when the ask went moot: unread mail to a session on this machine, or mail to another machine that has not left for it yet (check_sent shows which). Give 'to' to withdraw your message(s) to them, or add 'id' (from check_sent) for one. Only your own; anything already read, or already on its way to another machine, is gone.",
     "inputSchema": {"type": "object",
                     "properties": {"to": {"type": "string", "description": "recipient session name (or UUID) whose queued message(s) from you to cancel"},
                                    "id": {"type": "string", "description": "optional specific message id (from check_sent) to recall just that one"}}}},
]
# the arguments each tool declares: the only ones _postal_tool_call forwards (a forged from_id or id is dropped)
_POSTAL_TOOL_ARGS = {t["name"]: frozenset((t["inputSchema"].get("properties") or {}).keys()) for t in POSTAL_TOOL_SPECS}

POSTAL_INSTRUCTIONS = """\
Messaging peer romp sessions. A peer shares none of your context, only the bytes you send.

Message a peer only for something substantive: a question, information they need, or a result worth sharing. A message wakes the recipient and costs it a turn, so never send just to acknowledge, and stop once the exchange is done.

Write so the recipient can act from your first line:
- Declare the message kind via the required `kind` parameter: delegate (the recipient owns this now), coordinate (aligning/heads-up, reply optional), or question (reply required).
- First sentence is the whole point (the ask or conclusion), not how you got there.
- Name things exactly: files by path, sessions by name. Mark verified vs. suspected, and whose ask it is.
- End with the reply you need, or that none is. One point per message.

Before editing a shared repo, run list_agents and read peers' branches + working-notes (overlap only collides on the SAME branch), and publish yours with set_working. Resolve ownership by reading that state, never by messaging "do you still own this?": an idle peer's note may be stale, and a peer with no note holds nothing. Declare what you own in your first line. Never wake an idle session just to coordinate.

Addressing is live-only: you can message only currently-live sessions (list_agents). Dead names error, with no parked mail or reviving. A session's stable id (the uuid in list_agents) also works as the recipient — rename-proof, unique by construction.

A name is not guaranteed unique. When more than one live session answers to it the send is refused and the candidates are listed as `host:name`: pick one and resend rather than assuming the first. Your OWN name is refused outright, because a message there lands in your own inbox looking exactly like a reply from someone else. Your row in list_agents is the one marked `(you)`.

An isolation refusal is FINAL. A mailbox toggled off is a boundary the user drew: if send_message refuses for isolation, do NOT reroute the content through any other door (the kernel's /send route, shared files, another peer as relay). Report the refusal to the user and stop — only they lift the isolation.
"""


def _postal_tools(postal):
    """The thread/start and thread/resume params that register the postal tools: the six dynamic tools, and the
    bus's instructions as the thread's developer instructions (a dynamic tool carries no instructions block of its
    own, so the kind semantics and the isolation rule would otherwise never reach the model; the pinned
    ThreadStartParams and ThreadResumeParams both know `developerInstructions`). {} when the backend was handed no
    `postal` callable: then no tool is registered, rather than a tool that answers nothing. dynamicTools on
    turn/start is accepted and IGNORED by the runtime (probed 2026-09-19), so turn params carry none.

    What each call site's copy does (probed 2026-09-19 on 0.153.3): at thread/START both fields count, and both
    persist with the thread (the tools in the rollout's session_meta, the instructions as the thread's first
    developer message, replayed on every resume and across kernel restarts). At thread/RESUME the tools are
    accepted and harmless (the persisted registration stands) and the instructions are accepted and IGNORED, so
    a resume cannot revise an existing thread's instructions: a changed text reaches only threads started after
    it. Nor can a resume ADD a registration (read against openai/codex at rust-v0.153.3 for the review of
    2026-09-19: ThreadResumeParams has no dynamic_tools field, resume_thread_with_history builds its
    StartThreadOptions with dynamic_tools empty, and the session falls back to the rollout's persisted
    session_meta), so a thread started without the tools stays without them for its life; the smoke's resume leg
    shows persistence, not addition. The same dict rides both calls anyway: the resume's copy is harmless, and one
    shape is one test."""
    if postal is None:
        return {}
    return {"dynamicTools": copy.deepcopy(POSTAL_TOOL_SPECS), "developerInstructions": POSTAL_INSTRUCTIONS}


SEED_TAIL = 200   # records whose uuids seed the normalizer's dedup on re-attach (replay guard)
CLIENT_RETRY_MIN = 0.25
CLIENT_RETRY_MAX = 5.0
WORKER_JOIN_TIMEOUT = 2.0
HANDSHAKE_TIMEOUT_S = 30.0   # a new app-server answers its start-up requests within this, or the child is ended

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


def _is_response_model_mismatch(error):
    """Whether a request reached Codex, was answered with success, and only the pinned SDK's model of
    the REPLY could not read it.

    The pinned client validates every success reply against its generated models after the app-server
    has already acted on the request, so pydantic's ValidationError out of a client method means the
    server did the work and the client cannot parse the answer. Matched by class name plus a callable
    errors(), the shape pydantic gives it, and nothing looser: a JsonRpcError keeps its park, a
    RuntimeError its retry. Duck-typed for the same reason _is_permanent_request_rejection is: pydantic
    lives only in codexvenv and the backend must import without the SDK (2026-09-19).
    """
    return (error.__class__.__name__ == "ValidationError"
            and callable(getattr(error, "errors", None)))


def _installed_sdk_version():
    """The version of the openai-codex distribution this process imports, from its metadata, or None.

    The mismatch log names the SDK that raised, and that is the installed copy, not necessarily SDK_PIN:
    ensure_codex_sdk lets an already-importable openai_codex win over codexvenv and never reads its
    version, so a box carrying another copy would otherwise be told a pin it does not run cannot read
    the reply, and pointed at a pin bump that cannot reach it (2026-09-19). The caller words the
    fallback as the pin.
    """
    try:
        import importlib.metadata
        return importlib.metadata.version("openai-codex")
    except Exception:
        return None


def _sdk_for_mismatch_log():
    """How the mismatch log names the SDK: the installed version when its metadata reads, else the pin,
    each worded as what it is (2026-09-19)."""
    installed = _installed_sdk_version()
    if installed:
        return "the installed SDK (openai-codex %s)" % installed
    return "the pinned SDK (%s)" % SDK_PIN


_MISMATCH_POSITIONS_LISTED = 12   # item positions named in the mismatch line before "and N more"


def _response_model_mismatch_summary(error):
    """One phrase for a response-model mismatch: the error count and the DISTINCT thread items that
    failed, by position (thread.turns.N.items.M), never a value and never the first error's location.

    pydantic's str() lists every failing input, and a 'missing' error's input is the WHOLE item dict:
    for a text-bearing item kind that is transcript content, which must never reach the kernel log, the
    registry or a card. The first error's own location is no pointer either: the item union is plain,
    so pydantic reports its members in declaration order, and every unknown item fails first on the
    FIRST member's fields (UserMessageThreadItem.content, 'missing'), a member and a field that have
    nothing to do with the drift. The positions of the failing items are what a reader can act on,
    and they are indices, so they carry nothing from the reply. Errors outside the items (a reply
    missing a top-level field) are counted apart, so a malformed reply reads as one (2026-09-19).
    """
    try:
        errs = list(error.errors())
    except Exception:
        errs = []
    count = len(errs)
    counter = getattr(error, "error_count", None)
    if callable(counter):
        try:
            count = counter()
        except Exception:
            pass
    positions = []
    outside = 0
    for err in errs:
        loc = tuple(err.get("loc") or ()) if isinstance(err, dict) else ()
        at = loc.index("items") + 1 if "items" in loc else 0
        if not at or at >= len(loc) or not isinstance(loc[at], int):
            outside += 1
            continue
        pos = ".".join(str(part) for part in loc[:at + 1])
        if pos not in positions:
            positions.append(pos)
    text = "%s validation %s" % (count, "error" if count == 1 else "errors")
    if not positions:
        return text + ", none under a thread item"
    listed = ", ".join(positions[:_MISMATCH_POSITIONS_LISTED])
    if len(positions) > _MISMATCH_POSITIONS_LISTED:
        listed += ", and %d more" % (len(positions) - _MISMATCH_POSITIONS_LISTED)
    text += " over %d %s (%s)" % (len(positions), "item" if len(positions) == 1 else "items", listed)
    if outside:
        text += " and %d outside the items" % outside
    return text


class _PermanentRequestRejection(RuntimeError):
    def __init__(self, cause, operation, change_generation, client_generation):
        super().__init__(str(cause) or cause.__class__.__name__)
        self.operation = operation
        self.change_generation = change_generation
        self.client_generation = client_generation


def _is_compaction_refusal(error):
    """The app-server's answer to a turn/start while a compaction turn runs on the thread (live probe of runtime
    0.153.3 through the pinned client, 2026-09-19): -32603 "failed to submit turn input: ActiveTurnNotSteerable {
    turn_kind: Compact }", which the pinned SDK raises as InternalRpcError. A STATE, not a failure: the compaction
    is the turn in flight and turn/start is refused until it ends, so the worker latches the compacting bracket
    and waits for the bracket's end instead of backing off into it (_is_permanent_request_rejection classes the
    code as retryable, so before this the worker retried every 0.25 to 5 s for the compaction's whole run, and
    would have filed each refusal as a turn failure). Duck-typed like its sibling: the backend imports before the
    optional SDK is installed."""
    if getattr(error, "code", None) != -32603 and error.__class__.__name__ != "InternalRpcError":
        return False
    message = str(getattr(error, "message", "") or error)
    return "ActiveTurnNotSteerable" in message and "Compact" in message


class _CompactionInFlight(RuntimeError):
    """turn/start was refused because a compaction turn is running on the thread (_is_compaction_refusal,
    2026-09-19). `compact_ends` is the session's bracket-end counter as the worker read it beside the request's
    thread id, and `compact_idles` the count of idle statuses seen for the thread beside it: _work latches the
    bracket only while BOTH are unchanged, because an advance of either means the compaction the server named has
    already been SEEN to end — its statuses travel on the global queue and the refusal on the worker's request, two
    paths with no ordering between them (the probe saw the refusal land 4 ms before the compaction's own active
    status; the inverse order is the same race) — and a re-latch then would stand until nothing. The end counter
    alone missed a compaction romp did not start (no bracket to end: its idle advances it by nothing), hence the
    idle count (review find, 2026-09-19)."""
    def __init__(self, cause, compact_ends, compact_idles=0):
        super().__init__(str(cause) or cause.__class__.__name__)
        self.compact_ends = compact_ends
        self.compact_idles = compact_idles   # the thread's idle count beside the request: the second key (2026-09-19)


class _HandshakeTimeout(RuntimeError):
    """The handshake clock ran out and the child was ended (_handshake). Its own class because its retry floor
    differs: re-probing a child that never answers costs the whole clock again, under _client_lock, so the
    record does not retry it before HANDSHAKE_TIMEOUT_S. The ordinary backoff (cap CLIENT_RETRY_MAX = 5s) would
    have re-run the probe almost continuously while the fault lasted, holding the creation door and the
    models list for the clock out of every clock-plus-cap."""


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


def _running_python_tag():
    """This interpreter as venv names its lib directory: `3.14`, or `3.14t` for a free-threaded build.
    The twin of kernel.py's _running_python_tag and sdk_backend.running_python_tag; this module loads
    on its own, so it carries its own copy."""
    return "%d.%d%s" % (sys.version_info[0], sys.version_info[1],
                        "t" if "t" in getattr(sys, "abiflags", "") else "")


_CODEX_VENV_BUILT_FOR = []   # the tags a mismatched codexvenv was last seen built for: one stderr line per verdict


def ensure_codex_sdk(state_dir):
    """Make openai_codex importable: an already-installed copy wins, else the dedicated venv built by
    bin/romp-codex-setup ($STATE/codexvenv, never system python), and of that venv ONLY the
    site-packages built for the python this process runs (_running_python_tag), as kernel.py's
    _ensure_sdk_on_path does for the SDK venv. The venv's compiled extensions are per-interpreter. Every
    codexvenv/lib/python3.*/site-packages used to be inserted at sys.path[0] whatever the interpreter,
    so a codexvenv built with a newer python (the picker before 2026-09-06 took the newest on PATH)
    failed deep inside the import under the kernel's python, with an error naming a module rather than
    the venv, and shadowed shared dependencies for every later lazy import in the process. A venv for
    another tag adds nothing and is named on stderr once, with the remedy. True when importable."""
    import importlib.util
    import glob
    global _CODEX_VENV_BUILT_FOR
    if importlib.util.find_spec("openai_codex"):
        return True
    running = _running_python_tag()
    found = sorted(glob.glob(str(Path(state_dir) / "codexvenv" / "lib" / "python3.*" / "site-packages")))
    match = [sp for sp in found if Path(sp).parent.name == "python" + running]
    for sp in match:
        if sp not in sys.path:
            sys.path.insert(0, sp)
    if found and not match:
        built = sorted(Path(sp).parent.name[len("python"):] for sp in found)
        if built != _CODEX_VENV_BUILT_FOR:          # one line per verdict, not one per launch
            _CODEX_VENV_BUILT_FOR = built
            sys.stderr.write("codex-backend: codexvenv is built for python %s but the kernel runs %s: re-run "
                             "bin/romp-codex-setup to rebuild it for %s\n" % (" and ".join(built), running, running))
        return False
    _CODEX_VENV_BUILT_FOR = []
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


def _append_cmd_gesture(state_dir, sid, text, t):
    """Record a command GESTURE at the moment it was asked for — the twin of sdk_backend.append_cmd_gesture,
    same record ({"t", "cmdGesture"}) in the same file (<state>/states/<sid>.jsonl), because this module cannot
    import the SDK-gated one and the kernel reads both (_cmd_gestures). How the twin renders (2026-09-19): it is
    stamped at the clear, before the fresh conversation's first record, so it shows in the fresh conversation only
    until the boundary tick, which records the boundary at that first record's time and floors the live build's
    notes there; from then on it renders as the last gesture row of the cleared conversation's episode. Written by
    clear() for its acknowledging chip, with the command as typed."""
    p = Path(state_dir) / "states" / (str(sid) + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": int(t), "cmdGesture": str(text)}) + "\n")


def _unlink_quiet(path):
    """Remove a file this module touched and no longer wants (a rollback's), swallowing a filesystem refusal: the
    caller is already on its failure path, and a second raise there would mask the first (2026-09-19)."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _tail_state(path):
    """(last_uuid, recent uuids, newest record millis) off a materialized file, to re-anchor the normalizer's chain,
    seed its replay dedup and floor its clock after a restart. Reads the whole file once; keeps only the tail's
    uuids — replay across a reconnect only ever re-delivers recent items. The newest stamp is the lexical maximum,
    the latest in the one fixed-width format the normalizer writes (codex_events._iso), parsed once at the end; 0
    for a file with none (2026-09-22, the floor for the notice a restart cut writes). Undecodable bytes read as
    replacement characters, the way the kernel's _api_error_pass reads (2026-09-23, the review of this lane): a write
    torn mid-character at an exit left one stray byte of a multibyte character at the tail, and a strict decode raised
    UnicodeDecodeError, which is not an OSError, out of every _ensure_norm; the torn line is unparseable either way and
    is skipped like any other."""
    last, tail, newest = None, [], ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    u = rec.get("uuid")
                except Exception:
                    continue
                ts = rec.get("timestamp")
                if isinstance(ts, str) and ts > newest:
                    newest = ts
                if u:
                    last = u
                    tail.append(u)
                    if len(tail) > SEED_TAIL:
                        tail.pop(0)
    except OSError:
        pass
    return last, set(tail), (_events.record_ms(newest) or 0) if newest else 0


def _turn_ended_after(path, anchor):
    """Did a record after `anchor` (a uuid; None = the file's start) END a turn (codex_events.ends_turn)? True or
    False as the file says; None when the anchor is not in the file, which proves nothing either way. The restart
    check behind _settle_restart_turn (2026-09-22, the review of the restart-cut fix): the row marks a turn open from
    its acknowledgement to its end, and the transcript is romp's own record of whether that end landed, so a mark
    whose turn ended in the file is a kernel death between the two writes, or a clear that failed, never a cut. A
    missing file is a thread with no record yet, where nothing ended.

    The precondition (2026-09-23, the review of this lane): a finished turn is recognized when its END RECORD landed.
    Every completion writes one: a turn that completed with no final reply held, its last item a command say, gets an
    empty end record from codex_events.ThreadNormalizer._turn_completed (2026-09-23, the post-merge review of the
    restart-cut fix), so a lost clear on it reads as ended here and is not settled as a cut. Undecodable bytes read as
    replacement characters (_tail_state says why), so a torn tail is one skipped line, never a raise."""
    found = anchor is None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not found:
                    found = isinstance(rec, dict) and rec.get("uuid") == anchor
                elif _events.ends_turn(rec):
                    return True
    except FileNotFoundError:
        return False if anchor is None else None
    return False if found else None


def _ends_mid_line(path):
    """True when the file is non-empty and its last byte is not a newline: an earlier write was torn
    (write(2) returned short under ENOSPC, the process was killed between pages, power was lost) and
    left a partial line, a record with no line end. The next record must start its own line, or the
    two join in ONE unparseable line every reader skips."""
    try:
        with open(path, "rb") as f:
            if f.seek(0, os.SEEK_END) == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except FileNotFoundError:
        return False


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
        self.state = "waiting"        # waiting | working | compacting (the compaction bracket, CodexBackend.compact, 2026-09-19)
        self.since = time.time()
        self.queue = []               # pending sends (persisted); drained into the next turn
        self.queue_ids = []           # stable durable identity parallel to queue (public API stays text-only)
        self.echoes = []              # optimistic user-atom echoes ahead of the materialized file
        self.turn_id = None           # the active turn (interrupt/steer target), else None
        self.turn_mark = None         # the accepted turn as the registry row names it, {id, tid, at, after}, from its
                                      # acknowledgement to its end (2026-09-22): a row still naming one at load is a
                                      # turn a kernel exit may have cut (_settle_restart_turn); `after` is the file's
                                      # last uuid when the turn was accepted, where its records begin
        self.clearing = False         # the clear bracket (CodexBackend.clear, 2026-09-19): latched before
                                      # thread/start, dropped when the new tid is durable or the attempt raises
        self.compacting = False       # the compaction bracket (CodexBackend.compact, 2026-09-19): latched before
                                      # thread/compact/start, or by the worker on the server's Compact refusal; ended
                                      # by the thread's idle status after an active one, a loud status, the client's
                                      # death, kill, or a turn/start the server accepted (_end_compact_locked). In the
                                      # registry row too (2026-09-21): saved by both latches and by every end's own
                                      # transaction (_save_compacting_locked); a row still reading True at load is a
                                      # compaction whose outcome this kernel can never learn, and _load_registry ends
                                      # it loudly
        self.compact_active_seen = False  # an "active" thread status (or the refusal, which says the same) seen while
                                          # the bracket stands: only an idle AFTER it ends the bracket, so the previous
                                          # turn's stale idle, drained late from the global queue, cannot
        self.compact_ends = 0         # advanced by every end of the bracket: the worker's Compact refusal re-latches
                                      # only while it is unchanged since its request went out (_CompactionInFlight)
        self.compact_idles = 0        # every "idle" status seen for the thread, bracket or no bracket (_compact_status;
                                      # review find, 2026-09-19): the refusal's second staleness key. A compaction romp
                                      # did not start has no bracket to end, so its idle advances compact_ends by
                                      # nothing; a refusal handled after that idle would re-latch a compaction already
                                      # over, with no status left to end it. A previous turn's late idle costs one
                                      # extra refused retry, never a wedge.
        self.compact_last = None      # the bracket's last end, {kind, text, at} (_end_compact_locked, 2026-09-21): with
                                      # compact_ends the record compact_end() publishes on the /sessions row for
                                      # `romp compact --wait`, which the next accepted turn does not erase, where it
                                      # clears launch_error within milliseconds of a loud end that drained a parked message
        self.turn_ended = False       # a turn ended under mode_lock; _run_turn pokes for it after the release
        self.loaded = False           # thread/resume done in THIS process
        self.loaded_client_generation = None  # ...on WHICH app-server (client generation): a
                                              # replacement server has never seen the thread, so
                                              # `loaded` counts only while this matches the current one
        self.launch_error = None      # {text, at, limit, an optional noRetry (SessionBackend.launch_error's contract,
                                      # kernel/session_backend.py)}: why the session can't run, or None
        self.norm = None              # ThreadNormalizer, built by the worker on first need
        self.worker = None
        self.kick = threading.Event() # wake the worker (new send / resume / shutdown)
        self.parked = threading.Event()  # the worker is inside its backoff wait: a kick from now on wakes it
                                         # (set just before the wait, cleared after; tests wait on this
                                         # before releasing a backoff, instead of racing the worker to it)
        self.change_generation = 0    # explicit send/model/effort/resume changes that may fix a rejection
        self.turn_rejection = None    # rejected request generations; parked until either one changes
        self.note = ""                # postal working-note
        self.lock = threading.RLock()  # queue/turn/worker/state fields; lifecycle calls can nest
        self.norm_lock = threading.Lock()  # the turn worker and global pump share one normalizer


class CodexBackend:
    def __init__(self, state_dir, notify=None, poke=None, push=None, push_session=None,
                 codex_bin=None, log=None, client_factory=None, postal=None):
        self.state = Path(state_dir)
        self.root = self.state / "codex"
        self.projects = self.root / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)
        self.notify = notify or (lambda *a, **k: None)
        self.poke = poke or (lambda: None)
        self.push = push or (lambda: None)
        self.push_session = push_session or (lambda sid: None)
        self.codex_bin = codex_bin
        # postal(tool, sid, name, args) -> (ok, text): the kernel's loopback call to the bus AS the session, behind the
        # six dynamic tools every thread registers (_postal_tools); None registers no tool, said once (_postal_params)
        self.postal = postal
        self._postal_unset_said = False
        raw_log = log or (lambda m: sys.stderr.write("codex-backend: %s\n" % m))

        def _log(m):
            # Best-effort, like the kernel's _exit_log: no log line may raise on the thread that wrote it. The
            # kernel hands a bare sys.stderr.write, and a stderr that raises on write (a log disk at ENOSPC, the
            # pipe a supervisor's end closed) used to raise out of every site that logs first and acts second:
            # _handle_approval, inline on the pinned SDK's single reader thread (the reader ended with no reply
            # written and every in-flight request of every Codex session failed at once); the pump's except
            # branch, before _record_client_failure_locked (the dead client stayed installed and the next turn
            # parked forever on it); each worker's, before launch_error is filed (the session stayed "working").
            try:
                raw_log(m)
            except Exception:
                pass
        self.log = _log
        self._client_factory = client_factory   # tests inject a fake; None → real CodexClient
        self._client = None
        self._client_err = None       # why the client can't be built/authed (str), or None
        self._client_retry_at = 0.0
        self._client_failures = 0
        self._client_generation = 0   # successful app-server client installations
        self._catalog = None          # model_catalog() cache: a NON-EMPTY list, fetched once per process
        self._catalog_err = None      # why the last model_catalog() answered [] (str), or None
        self._catalog_lock = threading.Lock()   # one model_catalog() read at a time (see its docstring)
        self._client_lock = threading.Lock()
        self._sessions = {}           # sid → _Session
        self._sessions_lock = threading.RLock()
        self._reg_lock = threading.Lock()
        self._load_registry()
        self._republish_missing_names()
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
        self._registry_unreadable = False   # end_marker: while set, "not held" is a reader's fault, not no record
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
            self._registry_unreadable = True
            rows = {}
        restart_ended = []          # rows still reading compacting: their bracket ends here (below the loop)
        restart_cut = []            # rows still naming an accepted turn: settled against the transcript (below the loop)
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
                if bool(r.get("compacting")):
                    # A row still reading compacting at load is a compaction whose outcome this kernel can never learn
                    # (2026-09-21, the post-merge review of the native compaction): the app-server was the previous
                    # kernel's child and ended with it, so no status will ever end the bracket, and the compaction may
                    # have finished, failed, or never run. Before the bracket was in the row, the load restored the
                    # launch error and no bracket, so after a restart mid-compaction the row read compacting False with
                    # no notice, and a wait that had seen compacting printed done over an outcome nobody recorded. The
                    # bracket is not restored (nothing would end it, and every send would park behind it untimed); it
                    # ends HERE, loudly, as every bracket end does: a noRetry notice, the mark the CLI's wait reads as a
                    # compaction's end, replacing a restored notice, which is older than the restart. compacting False
                    # is written back below the loop so a second restart does not re-fire it. The notice, like every
                    # bracket end's, is cleared by the next accepted turn, and __init__ re-arms every queued session
                    # at boot, so a message parked at the restart clears it within milliseconds of this constructor,
                    # before the CLI's first poll after the kernel returns; so the end is also RECORDED below the loop
                    # (compact_ends 1, compact_last loud with these words, the record compact_end() publishes), which
                    # no delivery erases and which `romp compact --wait` judges as the restart's end (2026-09-22, the
                    # docs lane that followed the post-merge note on #1998: the load wrote the notice and no record,
                    # so on a restart the wait printed done over the cleared notice, or from a zero baseline ran to
                    # its never-recorded line). The notice says what this load knows and nothing about the
                    # conversation's markers (2026-09-22, the post-merge note on the durable bracket): it used to end by
                    # claiming that no compaction is marked in the conversation, but the clean end appends its
                    # compact_boundary to the transcript and then saves the bit to the registry row, two writes in one
                    # locked section, so a kernel death between them leaves the boundary in the transcript with this
                    # row still reading compacting, and the wait then exited 1 quoting a claim the transcript
                    # contradicted (_compact_status keeps that order on purpose, and says why).
                    s.launch_error = {"text": ("romp restarted while this conversation was compacting; whether "
                                               "Codex compacted it is unknown"),
                                      "at": time.time(), "limit": False, "noRetry": True}
                    restart_ended.append(s)
                # A row still naming an accepted turn (2026-09-22): the previous kernel exited with the turn open, or
                # after its end landed and before the mark was cleared. Settled below the loop against the transcript.
                # A dead row too: a kill whose interrupt never landed before the exit leaves the same open turn, which
                # a revive would show as Working.
                mark = r.get("turn")
                if isinstance(mark, dict) and isinstance(mark.get("id"), str) and mark["id"]:
                    restart_cut.append((s, mark))
                self._sessions[sid] = s
        for s in restart_ended:
            with s.lock:
                text = s.launch_error["text"]
                # the restart end is an end of THIS kernel's record too (2026-09-22): the fresh session's bit is already
                # down, so _end_compact_locked only advances the count to one and records the notice's words as a loud
                # end, the record the wait judges once a parked message has cleared the notice; it writes nothing, so
                # the row's bit and notice still land in the one save below, and the end is logged once
                self._end_compact_locked(s, "loud", text)
                self._save_compacting_locked(s, "launchError")
            self.log("compaction of %s ended: %s" % (s.name, text))
        for s, mark in restart_cut:
            self._settle_restart_turn(s, mark)

    # The notice a turn a kernel restart cut ends with (2026-09-22): in the transcript's end record and the launch error.
    CUT_TURN_TEXT = ("romp restarted while Codex was working on this and the turn was cut short; nothing picks it "
                     "back up on its own, so send a message to carry on")

    def _settle_restart_turn(self, s, mark):
        """Settle a turn the registry row still names at load (2026-09-22). Before, the exit stopped only the Claude
        sessions, the acknowledgement had already taken the prompt off the durable queue, and the row held no trace
        of the turn: the next kernel loaded the session idle with no notice and no retry, while the transcript's turn
        stayed open, so the chat read Working over a turn nothing ran and the next prompt was absorbed into it.

        The transcript decides first (the review of the fix, which found a finished turn called cut): a record that
        ended a turn after the mark's anchor means the turn finished and only its mark's clear was lost (a death
        between the two writes, or a failed save), so the mark is dropped and nothing is written. Otherwise the turn
        was cut. A finished turn is recognized only by its end record, which a turn that completed with no final reply
        does not leave (_turn_ended_after names the precondition), so a lost clear on that shape is settled as a cut
        until codex_events writes an end for it. The app-server was the previous kernel's child and ended with it, so
        no notification will ever end the turn; it ends here, loudly, the way a dead connection's turn already ends
        (norm.abandoned): the held state flushed, then an end record carrying the notice, marked rompNoRetry, so the
        chat stops reading Working, the next prompt opens a turn of its own, and nothing sends a blind retry into the
        thread; the person decides whether to pick the work back up. The same words become the launch error, WITHOUT
        noRetry: on a launch error that mark is what `romp compact --wait` reads as a compaction's end. The transcript
        write comes before the row's: a death between them leaves the mark, and the next load finds the notice's own
        end record after the anchor and drops it, when the file holds the anchor (a mark whose anchor is not in the
        file cannot show the notice landed, so such a death gets a second notice; 2026-09-23, the fold's verify pass).

        The mark leaves the row only once the notice landed (2026-09-23, the review of this lane): the row used to drop
        it whatever the append did, so a notice that failed to write (a full disk, a refused write) was written zero
        times, the transcript's turn stayed open, and no later load could retry: the defect back for good. Now a failed
        write saves the launch error alone, keeps the mark, logs why, and the next load tries again; until one
        succeeds the chat still reads Working (the launch-error card shows only once the transcript's turn is closed),
        so the log is where the failure shows. A turn accepted before that load replaces the kept mark with its own,
        and its end record closes the file's open turn, with no notice for the cut. The normalizer the failed write
        advanced is dropped, so the next one is rebuilt from the file and nothing chains to records that never landed.
        An anchor the file does not hold proves nothing, and is settled as a cut: a false notice is visible, a lost
        turn is not."""
        turn_id = mark["id"]
        with s.lock:
            tid = s.tid
        why = ""
        if isinstance(mark.get("tid"), str) and mark["tid"] != tid:
            ended = True                   # the row moved to a later conversation (a clear), which no open turn allows
        else:
            try:
                ended = _turn_ended_after(self.transcript_path(s.sid), mark.get("after"))
            except Exception as e:
                ended, why = None, "%s: %s" % (type(e).__name__, e)
        if ended:
            try:
                with s.lock:
                    self._save_registry(s, fields=("turn",))   # s.turn_mark is None: the row stops naming it
            except Exception:
                self.log("turn %s of %s had ended before the restart, but its stale mark could not be dropped; the "
                         "next load drops it: %s" % (turn_id, s.name, traceback.format_exc()))
                return
            self.log("turn %s of %s had ended before the restart; its stale mark was dropped" % (turn_id, s.name))
            return
        if ended is None:
            self.log("turn %s of %s: the transcript cannot show whether it ended (%s); settled as cut"
                     % (turn_id, s.name, why or "the mark's anchor is not in it"))
        landed, write_err = False, None
        try:
            norm = self._ensure_norm(s)
            with s.norm_lock:
                recs = norm.abandoned(turn_id, self.CUT_TURN_TEXT, no_retry=True)
                if recs:
                    self._append(s, recs)
                    landed = True
        except Exception:
            write_err = traceback.format_exc()
        if not landed:
            # the in-memory chain advanced past records the file never got: rebuilt from the file on next use
            with s.norm_lock:
                s.norm = None
        try:
            with s.lock:
                s.launch_error = {"text": self.CUT_TURN_TEXT, "at": time.time(), "limit": False}
                # the mark leaves the row with the notice, never without it (2026-09-23, the review of this lane)
                self._save_registry(s, fields=("launchError", "turn") if landed else ("launchError",))
        except Exception:
            self.log("cut turn registry save %s: %s" % (s.name, traceback.format_exc()))
        if landed:
            self.log("turn %s of %s was cut by a kernel restart: ended with a notice" % (turn_id, s.name))
        else:
            self.log("turn %s of %s was cut by a kernel restart, and the notice that ends it could NOT be written; "
                     "the row keeps the turn so the next load tries again, and until then the chat reads Working: %s"
                     % (turn_id, s.name, write_err or "the settle produced no record"))

    def _republish_missing_names(self):
        """spawn writes the durable registry row, then names/<sid>. A kernel death between the two
        left a LIVE row with no shared identity file, and nothing rewrote it: _load_registry rebuilt
        the session from the row, the first turn took _prepare_thread's resume branch (no names
        write), and only a rename would have healed it. Every surface that reads names/ alone
        (sender name and colour, cwd, the duplicate-name claim, which sees live names through that
        file only) was blind to the session, so a same-name create could mint a second live one
        (2026-09-11). The registry IS the durable source for name, cwd and colour, so a live row
        whose file is missing is republished from it here, once, at load. A row whose file exists
        is left alone (the names consumers watch the mtime); a dead row claims no name slot. fg is
        not in the registry and comes back empty, exactly as a rename's heal leaves it."""
        d = self.state / "names"
        for sid, s in self._session_items():
            with s.lock:
                dead = s.dead
            if dead or (d / sid).is_file():
                continue
            try:
                self._write_name(s)
            except (OSError, UnicodeDecodeError) as e:
                self.log("codex: names/%s could not be republished at load (%s) — the session runs "
                         "UNNAMED on shared surfaces until a rename lands; a same-name create may "
                         "collide meanwhile" % (sid, e))
            else:
                self.log("codex: republished names/%s, missing at load for a live registry row "
                         "(a kernel death between spawn's registry write and its name publish)" % sid)

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
                    "launchError": s.launch_error,
                    "compacting": s.compacting,   # the bracket, in the row since 2026-09-21 (_save_compacting_locked)
                    # the accepted turn, from its acknowledgement to its end (2026-09-22): a row still naming one at
                    # load is settled against the transcript (_settle_restart_turn)
                    "turn": dict(s.turn_mark) if s.turn_mark else None}

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
                   "launchError", "compacting", "turn"}
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
                    self._handshake(candidate, lambda: None)
                else:
                    if not ensure_codex_sdk(self.state):
                        raise RuntimeError(SETUP_HINT)
                    from openai_codex.client import CodexClient, CodexConfig
                    cfg = self._naming_explicit_bin(lambda: _codex_config(CodexConfig, self.codex_bin, self.state))
                    candidate = CodexClient(config=cfg, approval_handler=self._handle_approval)

                    def bring_up():
                        self._naming_explicit_bin(candidate.start)
                        candidate.initialize()
                    self._handshake(candidate, bring_up)
                    self.log("app-server runtime: %s" % (self.codex_bin or "ROMP-managed %s" % getattr(_runtime, "VERSION", "")))
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

    def _naming_explicit_bin(self, step):
        """Run one step that reaches for the explicit ROMP_CODEX_BIN, re-raising an OSError about the path as
        _explicit_bin_failure's sentence, cause attached. Two steps reach for it and both need this: the child's
        start (Popen's errno line, the SDK's FileNotFoundError), and before it the config's look at the helpers
        and assets beside the executable (_codex_config), which under a directory the kernel's user cannot
        traverse raises first (pathlib passes EACCES through from is_dir() and exists(), verified on 3.10 to
        3.12), naming <package>/codex-path, a path the operator never typed; a pathlib that answers False there
        instead reaches start(), whose PermissionError this same wrap names (review find, 2026-09-14)."""
        try:
            return step()
        except OSError as e:
            named = self._explicit_bin_failure(e)
            if named is None:
                raise
            raise named from e

    def _explicit_bin_failure(self, error):
        """The failure to record when a codex could not be started from an EXPLICIT ROMP_CODEX_BIN, or None
        when the error is not that. The kernel hands the operator's ROMP_CODEX_BIN through unchecked and the
        pinned SDK's start() raises, for a missing file, FileNotFoundError("Codex binary not found at X. Set
        CodexConfig.codex_bin to a valid binary path.") — a Python field the operator has never seen — and,
        for a file that exists but cannot run (no exec bit, a directory, a wrong-arch binary), Popen's bare
        errno line, which names no remedy at all; both reached every surface that shows the record verbatim
        (2026-09-11). The knob to fix is ROMP_CODEX_BIN, so the recorded text names it and the alternative,
        keeping the OS's reason without the SDK's advice. Only the errors that are about the path qualify: a
        host fault Popen can raise (out of descriptors, out of memory) is not the knob's, and stays raw. The
        managed runtime (codex_bin None) has no knob to name, so its errno line stays raw too
        (tests/test_codex_launch_error_card.py pins that shape). The two remedies apply at different events,
        and the sentence says which: a file repaired at the same path is picked up by the next probe after
        backoff, but the knob was read once, when kernel.py built this backend from its environment, so
        unsetting it changes nothing until the kernel starts again (the wording codex_runtime.py uses for the
        same event)."""
        if not self.codex_bin or not isinstance(error, OSError):
            return None
        if not (isinstance(error, (FileNotFoundError, PermissionError)) or error.errno == errno.ENOEXEC):
            return None
        reason = error.strerror or (os.strerror(errno.ENOENT) if isinstance(error, FileNotFoundError)
                                    else str(error) or error.__class__.__name__)
        return RuntimeError("ROMP_CODEX_BIN=%s is not a runnable Codex executable (%s). Fix the file at that path, "
                            "or unset ROMP_CODEX_BIN and restart the ROMP kernel to use the managed runtime "
                            "(romp-codex-setup)." % (self.codex_bin, reason))

    def _handshake(self, candidate, bring_up):
        """Run a new client's start-up requests (`bring_up`: start + initialize on a real client; nothing for
        an injected one) and the login check under ONE clock, ending the child when it runs out.

        The pinned SDK's request wait has no timeout: the one event that unblocks it is the reader thread
        failing every waiter, which happens when the child's stdout ends. So a codex that starts, holds stdout
        open and never writes its first frame (a start-up stalled on a hung ~/.codex or state mount, a stub
        that sleeps) parked _get_client in that wait with _client_lock HELD, forever: every Codex creation,
        resume, send and turn worker queued behind it, /models blocked under _catalog_lock, the tab whose
        receive loop made the call read no more ops, and nothing was logged or recorded (2026-09-11). The
        child offers no event of its own, so the clock stands in for one; its expiry is candidate.close(),
        the SDK's own unblocking event (child terminated, reader sees EOF, the wait raises), and the failure
        is recorded as the plain reason rather than the transport's text. The gate makes the two outcomes
        exclusive: a clock that fires after the handshake settled must not close an installed client, and a
        handshake that settled after the clock fired must not install a closed one (_check_auth swallows the
        account_read error the close provokes, so the flag is read after it, not only on the raise path).
        The expiry is its own class, _HandshakeTimeout, so _record_client_failure_locked floors the retry at
        the clock: the next probe costs the whole clock again with the lock held, and the ordinary backoff (cap
        5s) would have re-run it almost continuously while the fault lasted; inside the floor every caller gets
        the recorded reason at once."""
        gate = threading.Lock()
        state = {"expired": False, "settled": False}

        def expire():
            with gate:
                if state["settled"]:
                    return
                state["expired"] = True
            try:
                candidate.close()
            except Exception:
                pass

        def settle():
            timer.cancel()
            with gate:
                state["settled"] = True
                return state["expired"]

        timer = threading.Timer(HANDSHAKE_TIMEOUT_S, expire)
        timer.daemon = True
        timer.name = "codex-handshake-clock"
        timer.start()
        text = ("The Codex app-server (%s) did not answer within %.0fs of starting, so it was ended; "
                "check the codex binary, then try again"
                % (self.codex_bin or "managed runtime", HANDSHAKE_TIMEOUT_S))
        try:
            bring_up()
            self._check_auth(candidate)
        except Exception as e:
            if settle():
                raise _HandshakeTimeout(text) from e
            raise
        if settle():
            raise _HandshakeTimeout(text)

    def _record_client_failure_locked(self, error, candidate=None):
        """Record one failed client generation. Caller owns _client_lock."""
        self._client_err = str(error) or error.__class__.__name__
        self._client_failures += 1
        delay = min(CLIENT_RETRY_MAX,
                    CLIENT_RETRY_MIN * (2 ** min(self._client_failures - 1, 8)))
        if isinstance(error, _HandshakeTimeout):
            delay = max(delay, HANDSHAKE_TIMEOUT_S)   # a re-probe costs the whole clock, lock held: not before then
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

    def _client_failure_text(self):
        """The launch_error text for a session the client cannot serve right now: the recorded client
        failure, framed as the app-server's when it is a raw one. SETUP_HINT and LOGIN_HINT are whole
        sentences that name codex and carry their remedy, so they stand as written; anything else is
        whatever building, starting or draining the client raised — str(error), or the bare class name
        (_record_client_failure_locked): a missing binary's errno line, "TimeoutError" — and names no
        process. The chat's red card shows a Codex session's text as the backend wrote it (kernel
        build_session, 2026-09-11), so the frame is written here, at the two writers of this record.
        _client_err itself stays raw: model_catalog and the kernel's creation refusals wrap it in
        sentences of their own, and a frame there would double."""
        err = self._client_err or SETUP_HINT
        if err in (SETUP_HINT, LOGIN_HINT):
            return err
        return "The Codex app-server isn't available — %s" % err

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
        if method == "item/tool/call":
            # a dynamic tool call is not a request for a human (2026-09-19): the postal tools are the kernel's to
            # answer, and this branch comes BEFORE the declined-request log line and the chat warn below, which
            # would otherwise toast on every send
            return self._postal_tool_call(params)
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
        # An UNKNOWN server request must not raise: the pinned SDK invokes this handler inline on its single
        # stdout reader thread, whose loop has no per-request error handling — a raise ends the reader
        # (`except BaseException: router.fail_all`), no JSON-RPC reply is ever written, and every in-flight
        # request and turn of EVERY Codex session fails at once (review find on #930, 2026-09-07). Answer the
        # SDK's own default for an unrecognised method, `{}`: the app-server's parse fallbacks read an empty
        # or malformed approval answer as a denial (deny / decline / empty at 0.153.3), scoped to that one
        # request, so nothing is granted and the transport stays up. Loud on the log and the session.
        self.log("unknown Codex server request %s declined with an empty answer; no permission granted" % method)
        return {}

    def _postal_params(self):
        """_postal_tools for this backend's callable, merged into every thread/start and thread/resume. A backend
        built without one says so ONCE, at the first thread it starts, so a Codex session with no mail is a logged
        decision and never a quiet absence (2026-09-19)."""
        extra = _postal_tools(self.postal)
        if not extra and not self._postal_unset_said:
            self._postal_unset_said = True
            self.log("postal tools not registered on Codex threads: this backend was built without a postal "
                     "callable, so its sessions cannot mail peers")
        return extra

    def _postal_tool_call(self, params):
        """Answer one `item/tool/call` server request (a postal tool the thread carries, _postal_tools) in the shape the
        app-server accepts: {"success", "contentItems": [{"type": "inputText", "text"}]} (2026-09-19).

        Runs inline on the pinned SDK's single reader thread, the constraint the #930 find recorded in
        _handle_approval, so it NEVER raises, whatever the request carries: a fault is a failed result carrying the
        exception's text, and a log line. And it holds NO backend lock while the kernel's callable runs: the bus's
        /send resolves its recipient through the kernel's GET /sessions, which calls live_sessions() here and takes
        _sessions_lock and every s.lock, so a lock held across the call would make every Codex send wait out the
        bus's kernel timeout and then this side's bus timeout, silently. The sid and name are copied out under the
        locks, which are released before the call; the callable itself is bounded (the kernel's: 3 s on loopback).

        The session is bound by the request's threadId ONLY. The backend minted the sid and started the thread, so
        the sender is exact by construction; a threadId no live session holds is refused with a retry sentence and
        never resolved by name or by the only live session. No turnId fallback: the worker stores s.turn_id after
        turn_start returns, the call arrives on the reader thread, and an early call would miss. Only the arguments
        the tool's schema declares are forwarded, so a `from_id` or `id` a model puts into send_message's arguments
        is dropped here and the bus never sees a claimed sender. A call can arrive with no callable installed (the
        registration persists in the rollout across kernels): refused, never a tool that pretends."""
        def answer(ok, text):
            return {"success": bool(ok), "contentItems": [{"type": "inputText", "text": str(text)}]}
        tool = None
        try:
            p = params if isinstance(params, dict) else {}
            tool = p.get("tool")
            allowed = _POSTAL_TOOL_ARGS.get(tool) if isinstance(tool, str) else None
            if allowed is None:
                self.log("codex tool call refused: unknown tool %r" % (tool,))
                return answer(False, "Unknown tool: %s" % tool)
            if self.postal is None:
                self.log("codex tool call refused: %s called but no postal callable is installed" % tool)
                return answer(False, "Mail is not available in this session: the %s tool is not connected." % tool)
            tid = p.get("threadId")
            sid = name = None
            if isinstance(tid, str) and tid:
                for _sid, s in self._session_items():
                    with s.lock:
                        if s.tid == tid and not s.dead:
                            sid, name = s.sid, s.name
                            break
            if sid is None:
                self.log("codex tool call refused: %s from thread %r, which no live session holds" % (tool, tid))
                return answer(False, "This session is not matched to its thread yet; try %s again in a moment."
                              % tool)
            raw = p.get("arguments")
            args = {k: v for k, v in raw.items() if k in allowed} if isinstance(raw, dict) else {}
            ok, text = self.postal(tool, sid, name, args)      # no backend lock held here (see above)
            return answer(ok, text)
        except BaseException as e:
            self.log("codex tool call %s failed: %s" % (tool, traceback.format_exc()))
            return answer(False, "The %s call failed: %s" % (tool or "tool", str(e) or e.__class__.__name__))

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
        usage between turns). Feeds the owning session's normalizer so nothing is dropped. The thread's status
        is also the compaction bracket's deciding event (_compact_status, 2026-09-19): read here first, outside
        norm_lock, before the normalizer sees the notification."""
        while True:
            try:
                n = client.next_notification()
            except Exception as e:
                self.log("global pump stopped: %s" % e)
                with self._client_lock:
                    if self._client is client:
                        self._record_client_failure_locked(e, client)
                for _, s in self._session_items():
                    ended = None
                    with s.lock:
                        if s.compacting and not s.dead:
                            # the app-server is gone, and the compaction's outcome with it (2026-09-19): the bracket
                            # ends LOUDLY — a red card, as every launch error, cleared by the next accepted turn — and
                            # no divider is written for an end nobody saw; the failure path rebuilds the client.
                            # noRetry: nothing retries a compaction, so the card carries no Retry (review, 2026-09-21).
                            # The save stays under s.lock, _save_registry's rule (the _work handlers' shape): saved after
                            # the release, a turn accepted in the window cleared the field and saved None, and this
                            # stale snapshot then committed the red card over it (review find, 2026-09-21).
                            text = ("The Codex app-server ended while this conversation was compacting — %s"
                                    % (str(e) or e.__class__.__name__))
                            self._end_compact_locked(s, "loud", text)
                            s.launch_error = {"text": text, "at": time.time(), "limit": False, "noRetry": True}
                            self._save_compacting_locked(s, "launchError")   # the end and its notice in one write
                            ended = (s.name, text)     # built under the lock, logged after the release (below)
                        queued = bool(s.queue) and not s.dead
                    if ended:
                        self.poke()                    # the end is the event a parked message waits on (review find, 2026-09-21)
                        # the line the systemError end writes (_compact_status), naming the session: this end logged only
                        # "global pump stopped" and "client unavailable" (review find, 2026-09-21)
                        self.log("compaction of %s ended: %s" % ended)
                        self.push_session(s.sid)
                    if queued:
                        self._ensure_worker(s)
                        s.kick.set()
                return
            try:
                p = _dump(getattr(n, "payload", None))
                method = getattr(n, "method", "")
                tid = p.get("threadId")
                s = next((s for _, s in self._session_items() if s.tid == tid), None)
                if s:
                    if self._compact_status(s, method, p):
                        continue                          # the bracket's end: written, poked and pushed there (2026-09-19)
                    wrote = False
                    with s.norm_lock:
                        # Placeholder recovery replaces the normalizer under this same lock. Recheck
                        # after acquiring it: a pre-lock `s.norm` test could race to None here.
                        if s.norm:
                            recs = s.norm.handle(method, p)
                            if recs:
                                self._append(s, recs)
                                wrote = True
                    if wrote:                     # notify OUTSIDE norm_lock (see _append)
                        self.poke()
                        self.push_session(s.sid)
            except Exception:
                self.log("global pump: %s" % traceback.format_exc())

    # ── the materialized transcript ──────────────────────────────────────────────────────────────
    def _path_for(self, cwd, tid):
        """The one path rule: projects/<encoded cwd>/<tid>.jsonl, its directory made. transcript_path reads
        it for the session's CURRENT tid; clear() for a tid the row does not name yet — the fresh file must
        exist BEFORE the registry names it (2026-09-19)."""
        d = self.projects / _enc_cwd(cwd)
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s.jsonl" % tid)

    def transcript_path(self, sid):
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            cwd, tid = s.cwd, s.tid
        return self._path_for(cwd, tid)

    def _ensure_norm(self, s):
        with s.norm_lock:
            if s.norm is None:
                path = self.transcript_path(s.sid)
                last, seen, newest_ms = _tail_state(path)
                # floor_ms: a record this normalizer stamps from the clock never sorts before the file's tail
                # (2026-09-22; codex_events ThreadNormalizer._stamp)
                s.norm = _events.ThreadNormalizer(s.tid, cwd=s.cwd, model=s.model,
                                                  version="codex", last_uuid=last,
                                                  seen_uuids=seen, floor_ms=newest_ms)
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
            if _ends_mid_line(path):
                # A torn earlier write left a partial line. Written straight after it, this batch's first
                # record would join it in ONE unparseable line every reader skips (_tail_state, the event
                # model's readers), so the record vanished while the retire below still took its echo: a
                # prompt sent after the tear (the first record after a kill and restart) was nowhere in
                # the UI. Close the fragment first: it stays its own skipped line and the record lands
                # whole. Logged so the tear is seen, not silently papered over (review find, 2026-09-11).
                self.log("transcript for %s ended mid-line (a torn write); closing that line" % s.name)
                f.write("\n")
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # A landed user record replaces its optimistic echoes (uuid-independent: match by text, under
        # echo_text_key on both sides). ONE echo per landed text BLOCK, the OLDEST carrying the text. A
        # block is one send: the worker starts a turn from its whole queue, one input per queued send, and
        # the normalizer writes the app-server's one userMessage item as one record with a block per input
        # (codex_events._user_input_texts), so a turn started from two queued sends lands both echoes here;
        # a one-send record is one block. One per key, not every echo carrying it: a second identical
        # STEER (delivered mid-turn, never queued) keeps its echo when the first one's record lands, and a
        # second send dropped after that (the client dying mid-queue) stays visible. The kernel's
        # prune_live is the other retire, floored by record time; this one sees only the records it just
        # wrote.
        landed = [t for r in recs if r.get("type") == "user" for t in self._rec_texts(r)]
        if landed:
            with s.lock:
                kept = list(s.echoes)
                for text in landed:
                    for i, e in enumerate(kept):
                        if echo_text_key(e.get("text")) == text:
                            del kept[i]
                            break
                if len(kept) != len(s.echoes):
                    s.echoes = kept

    @staticmethod
    def _rec_texts(rec):
        """A user record's texts under the shared key rule (echo_text_key: outer whitespace stripped,
        nothing else), the key send() stores on the echo and _append and prune_live compare: one entry per
        text BLOCK (a string content is one entry), empty ones dropped. Per block and never the blocks
        joined: each block of a Codex user record is one send (see _append), and this retire takes exactly
        one echo per send. The kernel's prune_live also matches the record's space-joined text
        (_atom_user_texts yields the joined text and each block), floored by record time; that match is
        not repeated here."""
        c = (rec.get("message") or {}).get("content")
        if isinstance(c, list):
            keys = [echo_text_key(b.get("text")) for b in c if isinstance(b, dict) and b.get("type") == "text"]
        else:
            keys = [echo_text_key(c)]
        return [k for k in keys if k]

    # ── liveness / identity ──────────────────────────────────────────────────────────────────────
    def end_marker(self, sid):
        """What this backend's own record says about `sid` after it refused a send — never a liveness
        probe: True when the session is marked dead (the end marker set when its client dies or the
        session is ended), None when it holds no session by that id, False when the session stands
        (the refusal was something else; the caller retries). With the registry UNREADABLE at load the
        backend holds none of the sessions it should, so "not held" RAISES instead of answering None:
        a reader's fault the caller waits on, never a record's absence it can act on (review find,
        2026-09-08; before it, a corrupt registry.json read every uuid as no record and ended a Codex
        registrant's watch). The flag lasts the process: the registry is read once, at load."""
        s = self._session(sid)
        if not s:
            if getattr(self, "_registry_unreadable", False):
                raise RuntimeError("Codex registry was unreadable at load: no record of %s can be read" % sid)
            return None
        with s.lock:
            return bool(s.dead)

    def owns(self, sid):
        s = self._session(sid)
        if not s:
            return False
        with s.lock:
            return not s.dead

    def has_record(self, sid):
        """True while the registry holds a row for sid, alive or ended. The kernel's answer to whether a session's
        typed prompts were a person's (the shared parse's sdk_human, read by the display and the judges) must not
        flip the moment the row is marked dead, so it reads record presence here, never owns(), which send routing
        keeps live-only."""
        return self._session(sid) is not None

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

    def _cuts(self):
        """[(row, dead)] for every session a kernel exit NOW cuts: the one predicate inflight_turns() and would_cut()
        both read (2026-09-23, the promise in #2055's body; inflight_turns says what counts and why). The row and the
        dead bit are read under the session's one lock, so would_cut's filter cannot see a session end between two
        reads."""
        out = []
        for sid, s in self._session_items():
            with s.lock:
                if s.turn_id or (s.compacting and s.compact_active_seen):
                    out.append(({"sid": sid, "name": s.name, "backend": "codex"}, s.dead))
        return out

    def inflight_turns(self):
        """The Codex work a kernel exit cuts, [{sid, name, backend}], for the restart's cut row (kernel _drain_and_exit,
        2026-09-22), which counted a Codex cut as a clean restart: every session with an accepted turn open, or with a
        compaction running as its own turn. The app-server is the kernel's child and ends with both, and the next load
        ends each loudly: a row still naming a turn is settled with a notice (_settle_restart_turn), a row still
        reading compacting is ended with the restart notice (_load_registry).

        This is the ONE predicate for what a restart cuts (2026-09-23, the promise in #2055's body that whichever of
        #2052 and #2055 landed second would make one for both): the restart gates read it through would_cut(), minus
        the ended sessions. Before, the two counted differently, and the cut row read open turns alone, so a restart
        over a running compaction wrote an empty row, a clean restart to every reader of the ledger, while /busy had
        held the quiet window for that same compaction and the next load ended it with its restart notice.

        A compaction counts once its active status was seen (compacting and compact_active_seen: the test the worker
        itself uses before it stops sending turns, _work, and the rule #2055's review set for the gates). A bracket
        latched with no active status yet may be one Codex acknowledged and never ran (the limit docs/codex.md
        describes), which is no cut, and counting it in the gates would hold a quiet refresh taken as that limit's way
        out to the 15-minute backstop. The race this accepts, here as in the gates: a restart in the moment between
        the ACK and the active status cuts a compaction that was starting, the row does not name it, and the next load
        still ends its bracket with the notice that its outcome is unknown.

        An ENDED session counts too (2026-09-23, the review of #2052): a kill whose interrupt never landed before the
        exit leaves the turn open, and the next load writes its notice as it does for any row still naming a turn, so
        a row that skipped dead sessions called that cut a clean restart while the transcript said otherwise. A dead
        row's compaction counts by the same rule, since the load ends every row still reading compacting; kill ends
        the bracket and the worker latches none on a dead row, so none is expected."""
        return [row for row, _dead in self._cuts()]

    def busy(self, sid):
        """A turn is open, or a queued send is about to open one. A queue the worker has PARKED on a permanent
        request rejection (a model the account refuses: _work breaks to kick.wait() with no timer) is neither:
        nothing is in flight and nothing runs until an explicit change bumps change_generation. It must read
        NOT busy, because the kernel takes busy() as its authoritative "turn open" word and parks a model or
        effort pick behind it (Codex applies a pick at the next turn_start, model_switches_live False), and its
        drain skips the session for as long as busy() holds — so a parked queue that read busy parked the very
        pick that would have unparked it, with no way out: Codex has no unqueue, and kill + resume re-arm the
        same queue with the same model (review, 2026-09-11). The rejection is stale the moment an explicit
        change moves the generation (send, set_model, set_mode, set_effort, resume all bump it and kick), so
        busy() flips back to True right then, before the worker wakes and clears the tuple, and a pick pressed
        after that one parks behind the retry in press order. A new client generation is the worker's own
        clear (it is kicked for it), a scheduling quantum later.

        A standing compaction bracket reads busy too (2026-09-19; a widening of "turn in flight" the server itself
        makes): the compaction runs as its own turn on the app-server, which refuses a turn/start for its whole run
        (live probe), so the kernel's drain and gates hold as they would for an open turn, and a drive op it
        hands over right after a compaction fire sees busy() True and arms no hold (_after_turn_opening)."""
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            if s.dead:
                return None
            if s.turn_id or s.compacting:
                return True
            parked = s.turn_rejection is not None and s.turn_rejection[0] == s.change_generation
            return bool(s.queue) and not parked

    def would_cut(self):
        """The Codex work a kernel restart NOW would cut, [{sid, name}] in SdkBackend.would_cut's shape, for the
        kernel's restart gates (2026-09-22, the review that found the quiet window and the converge blind to Codex:
        both read the SDK backend alone, so a quiet refresh applied over an open Codex turn): the drain's own
        predicate, inflight_turns(), minus the ended sessions (2026-09-23, the promise in #2055's body). So an open
        turn counts, and a compaction once its active status was seen, by the one rule the cut row counts them by.
        An ended session's open turn is a cut the next load still settles, so the row names it, but it is not work a
        quiet window should wait for: the session was ended, and a kill whose interrupt never landed can leave its turn
        open with nothing left to end it, so a gate that waited on it would hold a quiet refresh to the 15-minute
        backstop.

        busy()'s queued half is left out on purpose, as it is from the cut row: a queued send is on disk until the
        turn/start ACK, and the next kernel sends it, while an open turn's prompt left the durable queue at that ACK,
        so the next kernel has nothing to run it from."""
        return [{"sid": row["sid"], "name": row["name"]} for row, dead in self._cuts() if not dead]

    def busy_breakdown(self):
        """(in flight, background) in SdkBackend.busy_breakdown's shape (2026-09-22): each session would_cut() names
        is a turn in flight. Codex runs no background work romp tracks, so the second count is always 0. The kernel's
        /busy reports the sum in its own `codex` field and in `busy`, never in `inflight` (2026-09-23, the review of
        this lane): `inflight` is what the manager asks the drain hold for, and the hold pauses Claude sessions only,
        since the Codex worker never reads it."""
        return len(self.would_cut()), 0

    def clearing(self, sid):
        """AUTHORITATIVE 'is a clear in progress right now' (SessionBackend.clearing): the bracket clear() holds
        from before thread/start until the new thread id is durable, or the attempt raises. None when no session
        or ended. The kernel's _clearing_now reads it for the chip, the chat's live "Clearing conversation…"
        element and the fold of a queued "/clear" chip — no kernel literal changes for Codex (2026-09-19)."""
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            if s.dead:
                return None
            return bool(s.clearing)

    def compacting(self, sid):
        """AUTHORITATIVE 'is a compaction in progress right now' (SessionBackend.compacting): the bracket compact()
        latches before thread/compact/start, or the worker latches on the server's Compact refusal, and the observed
        idle-after-active thread status ends (_compact_status). None when no session or ended. The kernel's
        _compacting reads it first, so the chip, the chat's compacting element, the drive-op gates, the drain's gate,
        the nudge skip and the /sessions rows all follow with no kernel literal change (2026-09-19)."""
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            if s.dead:
                return None
            return bool(s.compacting)

    def compact_end(self, sid):
        """The compaction bracket's end record (SessionBackend.compact_end, 2026-09-21): {ends, kind, text, at}, the
        bracket-end counter every end advances (_end_compact_locked) with the last end's kind ("clean" for a compaction
        Codex completed, "loud" for every end after which romp cannot vouch the thread was compacted, "" before any
        end), its words (empty for a clean end) and its stamp (None before any end); None for no session or a dead one.
        Published on the /sessions row for `romp compact --wait`, which judges it against the count it read before its
        request. The notice alone (launch_error) was erased by the next accepted turn: a message parked on the bracket
        drains at the loud end's poke and its accepted turn/start clears the field within milliseconds, before a wait
        polling every two seconds reads again, so the row read quiet with no notice, a clean end's shape, and the
        wait printed done over an uncompacted thread (the post-merge review of the wait). In memory: the bracket's bit
        is in the registry row too (_save_compacting_locked), so a kernel restart starts the count over, and a row still
        reading compacting at load is ended as a loud end this kernel RECORDS (_load_registry, 2026-09-22: the count at
        one, the restart notice's words, a stamp) beside its noRetry notice, so the wait reads the restart's end from
        the record by its identity, the count with this end's stamp, from every baseline (a count below the baseline,
        or the restart's count of one under a new stamp against a baseline of one), not from a notice the message
        parked at the restart clears within milliseconds of the boot (before this, the load wrote the notice and no
        record)."""
        s = self._session(sid)
        if not s:
            return None
        with s.lock:
            if s.dead:
                return None
            last = s.compact_last or {}
            return {"ends": s.compact_ends, "kind": last.get("kind", ""), "text": last.get("text", ""),
                    "at": last.get("at")}

    # ── control ──────────────────────────────────────────────────────────────────────────────────
    def send(self, sid, text):
        s = self._session(sid)
        if not s or not text:
            return False
        c = self._get_client()
        with s.lock:
            if s.dead:
                return False
            # WHOLE seconds, as the SDK echoes stamp theirs: record times are parse_z's int
            # seconds and prune_live lands an echo by text only through a record at or after its send,
            # so a float stamp would keep an echo whose record was written later in the same second. The
            # text is stored under the shared key rule (echo_text_key), the key prune_live and _append
            # compare against. The uuid is kept: it names THIS send's echo on the dead path below.
            echo_uuid = "echo-%s" % uuidlib.uuid4().hex[:8]
            s.echoes.append({"text": echo_text_key(text), "t": int(time.time()), "uuid": echo_uuid})
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
                # The session died during the steer RPC: take back THIS send's echo, by the uuid minted
                # above, and no other. Retiring by text would take an earlier same-text send the
                # app-server never recorded with it. prune_live's rule applies here too: a send the
                # app-server never records stays visible, and no other send's failure retires it.
                s.echoes = [e for e in s.echoes if e["uuid"] != echo_uuid]
                return False
            entry_id = "q-%s" % uuidlib.uuid4().hex
            s.queue.append(text)
            s.queue_ids.append(entry_id)
            # Keep append order identical in memory and on disk. _save_registry snapshots this RLock
            # reentrantly before taking either registry lock; it never takes a session lock afterward.
            try:
                self._save_registry(s, queue_append={"id": entry_id, "text": text})
            except BaseException:
                # A raising durable write publishes NOTHING, as every sibling mutator keeps it: the entry
                # never reached disk, so it leaves memory too, and this send's echo with it — by its id
                # and uuid, never by position or text, so no other send's copy goes. Kept, they showed a
                # queued bubble on a busy session for a send the caller was told failed, with no worker
                # kicked to drain it, and the copy rode the next kick into that turn beside the retype.
                if entry_id in s.queue_ids:
                    at = s.queue_ids.index(entry_id)
                    del s.queue[at]
                    del s.queue_ids[at]
                s.echoes = [e for e in s.echoes if e["uuid"] != echo_uuid]
                raise
            s.change_generation += 1
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
        """Model choices and their supported efforts from the app-server's own model list (the ONE
        authoritative source), fetched once per process and cached. [] when the list cannot be had,
        and then model_catalog_error() says WHY (the picker shows nothing rather than another vendor's
        list, and the kernel's /models hands the reason on). Three ways to [], each recorded: the
        client is unavailable (_get_client() None: the factory failed, or the client sits in its retry
        backoff), model_list raised, or the app-server answered an EMPTY page. Only a NON-EMPTY list is
        cached: an empty page `is not None`, and caching it would hold the empty catalog for the life of
        the process, a blank menu on every later picker open after the app-server has models to list.
        Each failure is logged once per DISTINCT reason, not once per call: the kernel
        re-reads the catalog on every picker open and every models frame, and a per-call line repeats
        for as long as the fault lasts. The read runs under _catalog_lock: /models is served from handler
        threads, and two readers racing the same first read would otherwise both log the same reason, or
        one that found no client would record its reason after the other had stored the list and cleared
        it, a stale reason beside a held catalog. A plan account may still refuse some listed models per
        turn — that failure surfaces loudly as the turn's error card, and switching back is one click."""
        with self._catalog_lock:
            if self._catalog:
                return self._catalog
            c = self._get_client()
            if c is None:
                self._note_catalog_error("the Codex app-server client is unavailable: %s"
                                         % (self._client_err or "not started yet"))
                return []
            try:
                ms = c.model_list()
                rows = []
                for m in (getattr(ms, "data", None) or []):
                    if getattr(m, "hidden", False):
                        continue
                    efforts = []
                    for option in getattr(m, "supported_reasoning_efforts", None) or []:
                        level = getattr(option, "reasoning_effort", None)
                        level = getattr(level, "value", level)  # SDK enums preserve newly advertised values (2026-09-17)
                        if isinstance(level, str) and level and not any(e["value"] == level for e in efforts):
                            efforts.append({"value": level, "label": level,
                                            "sub": getattr(option, "description", "") or ""})
                    rows.append({"value": m.id, "label": getattr(m, "display_name", None) or m.id,
                                 "model": getattr(m, "model", None) or m.id,
                                 "isDefault": bool(getattr(m, "is_default", False)), "efforts": efforts})
            except Exception as e:
                self._note_catalog_error("model_list failed: %s" % (str(e) or e.__class__.__name__))
                return []
            if not rows:
                self._note_catalog_error("the Codex app-server listed no models")
                return []
            self._catalog = rows
            self._catalog_err = None
            return rows

    def _note_catalog_error(self, why):
        """Record why model_catalog() answered [] and log it once per distinct reason (see model_catalog).
        Caller owns _catalog_lock."""
        if why != self._catalog_err:
            self.log(why)
        self._catalog_err = why

    def model_catalog_error(self):
        """Why the last model_catalog() answered [] (one sentence for a picker to show), or None when
        a catalog is held or none has been asked for yet. The kernel's /models reads it after an empty
        answer; the failure is the app-server's or the client's, so the sentence names that side.
        Read under _catalog_lock like every writer of the reason, so a concurrent read that is mid-way
        through storing a list or recording its own reason cannot hand this caller a half-updated value
        (review find, 2026-09-09)."""
        with self._catalog_lock:
            return self._catalog_err

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
        if not s:
            return False
        catalog = self.model_catalog()                  # may contact the app-server; never under the session lock
        with s.lock:
            # Validate the CURRENT model after the catalog read: a concurrent model pick may have landed.
            model = next((m for m in catalog if (s.model in (m["value"], m["model"]) if s.model
                                                else m["isDefault"])), None)
            if s.dead or model is None or not any(e["value"] == value for e in model["efforts"]):
                return False                            # unknown catalog/model/value never borrows another model's levels
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

    def clear(self, sid, text="/clear"):
        """A fresh conversation for the SAME session (SessionBackend.clear, 2026-09-19): a new app-server thread
        under the same sid — thread/start with sessionStartSource "clear" (Codex's own tag for its /clear and
        /new; the runtime validates the enum and reflects nothing back, live probe 2026-09-19) and the row's
        cwd, mode and picked model — swapped into the registry row under the turn lock, the normalizer reset on
        the new EMPTY file so its first record is a ROOT (what the kernel's episode boundary keys on: the old
        cards settle and the "Conversation cleared" card appears on the fresh thread's first prompt), and an
        acknowledging chip carrying `text` as typed ("/clear", "/new", "/clear now") left in the live tail: the
        composer retires its optimistic bubble by that exact text, so a literal "/clear" chip for a typed /new
        left the bubble standing (review find, 2026-09-19). Everything else on the row survives because it lives
        on the row, keyed by sid: name, cwd, model, effort, mode, color, note, the durable queue; tags and the
        mailbox are kernel stores keyed by sid. The old thread is LEFT on the app-server: romp reads only its own
        materialized file, which stays where build_episode's sibling lookup finds it; an archive would be one
        more RPC that can raise after the swap committed, and a thread with no turn has no rollout to archive.

        Idle only. The worker holds mode_lock across thread preparation and the whole turn (_run_turn), so a
        non-blocking take of it is the exact "a turn is in flight" test set_mode uses, and the swap under it can
        never race a worker between _prepare_thread and turn_start (a worker that read the OLD normalizer and the
        NEW tid would chain the new file's first record to the old leaf: no root head, no boundary). The belt
        behind it is busy()'s own rule: a queued send whose worker has not yet taken the lock reads busy() True
        to the kernel and would otherwise land on the fresh thread — a message typed BEFORE the /clear, answered
        without its context — so it answers "busy" too; a queue PARKED on a permanent rejection rides into the
        fresh thread instead (busy() says not busy for it, and this explicit change is what re-arms it, as
        set_mode does). A standing compaction bracket (s.compacting, CodexBackend.compact) answers "busy" too
        (review find, 2026-09-19): the compaction runs as its own turn on the app-server, and busy() already reads
        it, but the kernel's gate read and this handover are two steps with no atomicity by design, so a clear
        that slipped through would swap the tid under the bracket — and the pump matches the compaction's active
        and idle statuses to a session by tid, so after the swap they would match nothing and the bracket would
        stand until a kill. "busy" is the kernel's word to park on and is never shown: the parked clear runs at the
        turn's end poke or at the compaction bracket's end, which pokes whether it ended cleanly (the boundary write)
        or loudly (systemError, notLoaded, the client's death: they only pushed and kicked until the review of
        2026-09-21, leaving the parked clear to the pusher's half-second backstop).

        The bracket: s.clearing is latched before thread/start and dropped when the new tid is durable (the
        deciding event) or the attempt raises; clearing() publishes it. The swap's order: the fresh file is
        touched BEFORE the registry names its tid, because discovery signs registry.json's mtime and skips a row
        whose file does not stat — saved first, a discover landing in the gap would cache a list WITHOUT this
        session until the next registry write. A raising registry write publishes NOTHING (_prepare_thread's
        discipline): the in-memory swap rolls back and the touched file leaves with it. The chip lives here and
        not in the kernel because the composer's optimistic "/clear" bubble ends only on a landed user event with
        its text (the "cmd:<t>:<command>" id is not the kernel-echo form, so send-pending takes it by text) and the
        kernel has no live-atom store of its own — the SDK puts the same chip in its setters (_ack_cmd_chip)."""
        cmd = str(text or "").strip() or "/clear"   # the words as typed ride the chip, its uuid and the twin (see above)
        s = self._session(sid)
        if not s:
            return _contract.SessionBackend.clear(self, sid, cmd)
        c = self._get_client()
        if c is None:
            return self._client_failure_text()
        if not s.mode_lock.acquire(blocking=False):
            return "busy"
        try:
            with s.lock:
                if s.dead:
                    return "this session has ended — revive it first"
                parked = s.turn_rejection is not None and s.turn_rejection[0] == s.change_generation
                if s.turn_id or s.compacting or (s.queue and not parked):
                    return "busy"                  # s.compacting: the compaction bracket, see the docstring (2026-09-19)
                cwd, mode, model, name = s.cwd, s.mode, s.model, s.name
                s.clearing = True
            self.push_session(sid)             # the chip reads "clearing" from here
            params = {"cwd": cwd, **_approval_params(mode), **_execution_permissions(cwd, thread_start=True),
                      **self._postal_params(), "sessionStartSource": "clear"}
            # the postal tools ride thread/start alone (a resume cannot add a registration, see _postal_tools), so
            # a thread minted here without them would carry no mail for its whole life (2026-09-20)
            if model:
                params["model"] = model
            touched = None
            try:
                resp = c.thread_start(params)
                new_tid = resp.thread.id
                loaded_client_generation = self._client_generation_for(c)
                touched = self._path_for(cwd, new_tid)
                touched.touch()                # the fresh file exists before the row names it (see above)
                with s.lock:
                    if s.dead:
                        touched.unlink(missing_ok=True)
                        s.clearing = False
                        return "this session has ended — revive it first"
                    prior = (s.tid, s.model, s.loaded, s.loaded_client_generation, s.launch_error)
                    s.tid = new_tid
                    # the row's CURRENT model, as _create_thread reads it (review find, 2026-09-19): nothing reads busy
                    # through the bracket and set_model never takes mode_lock, so a /model pick can land while
                    # thread/start is in flight, accepted and saved — and the model read before the request, written
                    # back here, reverted it in memory, the registry, the live listing and the next turn's params. The
                    # pre-request model rides the start params only; the server's reply fills an empty pick
                    s.model = s.model or getattr(resp, "model", "") or ""
                    s.loaded = True
                    s.loaded_client_generation = loaded_client_generation
                    s.launch_error = None      # the fresh thread starts clean; a parked rejection named the old one
                    try:
                        self._save_registry(s, fields=("tid", "model", "launchError"))
                    except BaseException:
                        (s.tid, s.model, s.loaded, s.loaded_client_generation, s.launch_error) = prior
                        raise
                    s.clearing = False         # THE deciding event: the new tid is durable
                    s.change_generation += 1   # re-arms a queue parked on a rejection of the old thread
                    queued = bool(s.queue)
            except Exception as e:
                if touched is not None:
                    try:
                        touched.unlink(missing_ok=True)
                    except OSError:
                        pass
                with s.lock:
                    s.clearing = False
                self.push_session(sid)
                self.log("clear %s: %s" % (s.name, e))
                return "Couldn't start a fresh conversation: %s" % str(e)[:200]
            # A fresh normalizer anchored on the new empty file: last_uuid None, so the first record is a ROOT. Reset
            # UNDER the turn lock, as _create_thread does under the worker's (review find, 2026-09-19): a prompt typed
            # while thread/start was in flight has queued a worker on mode_lock (busy() reads False through the
            # bracket, so the kernel hands it over), and that worker takes _ensure_norm as a local for its whole turn
            # the instant the lock is released — reset AFTER the release, a scheduler switch let it read the OLD
            # normalizer with the NEW tid and chain the fresh file's first record to the old leaf, stamped with the old
            # thread (no root head, so no boundary, ever, for that file: transcript_head memoizes the first record).
            # Reset under norm_lock and rebuilt after ITS release — _ensure_norm takes the same non-reentrant lock
            # itself, exactly as _prepare_thread does; the global pump routes by s.tid, so a straggler for the old
            # thread is dropped, and none is expected between turns. Guarded: the swap is durable, the answer is
            # already "", and a worker rebuilds a missing normalizer itself.
            try:
                with s.norm_lock:
                    s.norm = None
                self._ensure_norm(s)
            except Exception as e:
                self.log("clear %s: normalizer reset after the swap: %s" % (s.name, e))
        finally:
            s.mode_lock.release()
        # After the swap (review find, 2026-09-19): everything from here is bookkeeping on a clear that HAPPENED, and
        # the answer is "" whatever it does. Unguarded, a raise here (the durable twin's write at ENOSPC — the class
        # this module's own _log wrapper exists for) escaped a verb whose contract promises a string: the route logged
        # it and said nothing (the bracket had dropped, the composer's optimistic bubble stood), the drain's per-sid
        # except dropped the sid's whole queue, and the kick a queue parked on the old thread's rejection waits for
        # never came. The kick and the pushes run whatever the tail did.
        t = int(time.time())
        try:
            with s.lock:
                s.echoes.append({"text": cmd, "t": t, "uuid": "cmd:%d:%s" % (t, cmd.lstrip("/")), "command": cmd})
            _append_cmd_gesture(self.state, sid, cmd, t)     # the durable twin, on disk before the push that rebuilds the chat
            try:
                c.thread_set_name(new_tid, name)
            except Exception:
                pass                           # cosmetic on the Codex side, as rename treats it; the registry is the truth
        except Exception as e:
            self.log("clear %s: after the swap: %s" % (s.name, e))
        finally:
            if queued:
                self._ensure_worker(s)
                s.kick.set()
            self.push()
            self.push_session(sid)
        return ""

    def compact(self, sid):
        """Compact the conversation in place with Codex's own compaction (SessionBackend.compact, 2026-09-19):
        thread/compact/start on the session's thread. Built on what the live probe of runtime 0.153.3 through the
        pinned client showed: the request is acked {} in about a millisecond (the ack means queued, nothing more);
        the compaction then runs as ITS OWN server turn (about 4 s on a two-turn thread) whose one item is
        contextCompaction; a turn/start meanwhile is refused with "ActiveTurnNotSteerable { turn_kind: Compact }";
        thread/compacted never arrives; and the pinned client's router parks that unregistered turn's notifications
        and discards them at its turn/completed — so the ONLY evidence that reaches this backend is the thread's
        status on the global queue: active, then idle. The bracket (s.compacting; compacting() publishes it, busy()
        reads True under it) is therefore latched HERE, before the RPC, so an active status that lands during the
        round trip is seen while it stands, and ended by _compact_status on the first idle AFTER an active (a stale
        idle, the previous turn's tail drained late from the global queue, is ignored), which also writes the
        compact_boundary record through the normalizer's thread/compacted writer — the backend's own call, since the
        runtime sends no notification for it. Loud ends: a systemError status (the failed compaction's shape: the
        live leg of 2026-09-19 forced one with a model the account cannot use, and Codex answered the active status
        with systemError and no idle, the turn's own error staying on the discarded turn queue), a notLoaded status,
        the client's death, a raising request. The same leg compacted a thread resumed on a FRESH app-server (the
        precondition below, proven live: ack in 11 ms, active 80 ms later, idle 1.8 s after the ack, a turn after
        it answered), and showed thread/resume itself publishing the thread's idle status just before the request —
        exactly the stale idle the active-seen rule ignores. A turn/start the server ACCEPTS while a bracket compact() latched stands ends it with a log
        line and no divider: the accepted turn is the exact event that no compaction is active (a compaction the
        server acked and never ran, or one that finished before the request; its late statuses meet no bracket).
        The worker attempts a turn/start under a bracket with no active seen yet — the server is the authority from
        the ack on — and stands down under one that has (_work): the answer is exact either way.

        "" on success (the bracket is up); "busy" when a turn is open, a compaction already stands (the kernel parks
        a second press and runs it after, as the SDK's second press does), or a send is queued but not yet taken by
        the worker (the belt clear() has; a queue PARKED on a permanent rejection is not that, as busy() reads it) —
        the kernel's word to park on, never shown; else the reason, shown verbatim. The worker's own loading
        precondition comes first: on a fresh app-server (a kernel restart, a rebuilt client) the thread is not loaded
        until thread/resume, and the worker's 2026-09-11 note records the server refusing a request on an unloaded
        thread, so an unloaded thread is prepared here, under the turn lock as _run_turn holds it (set_mode's
        non-blocking take still refuses meanwhile), a raise answered in words and never parked — a compaction has no
        retry queue. A pending-/failed- placeholder has no thread to compact and is refused in words rather than
        minted; a conversation with no records (a fresh spawn, a fresh /clear) is refused before any request, since
        a compaction the server acks and never takes up would leave the bracket standing, and no message typed into
        the session probes it (the kernel's gates read compacting() and park, and the drain skips the session); what
        ends such a bracket is a send that bypasses the kernel's gates (a peer's mail, a retry press, a raw-mode save:
        the worker attempts turn/start under a bracket with no active seen, and the accepted turn ends it), a kernel
        restart, which ends the bracket as an unknown outcome (the load's notice, below) and delivers the parked
        message from the kernel's disk mirror, or End then Revive, which keeps the thread and its history but hands a
        parked message the user typed back as not delivered and drops the rest of the parked queue with the session,
        logged (the kernel's End doors cancel the ending session's parked ops, 2026-09-21). docs/codex.md names the
        two the user can reach, and which of them keeps the message.
        The normalizer is built BEFORE the latch and outside norm_lock (_ensure_norm takes it): a session revived or
        restored in this process that has not run a turn has none, and the boundary writer would otherwise meet
        s.norm None inside the pump's try and lose the record silently; built here it also seeds last_uuid from the
        file, which is what makes the boundary's logicalParentUuid the true leaf.

        Esc does not stop a running compaction (interrupt() needs a turn id romp never learns: the pinned wheel has
        no thread/turns/list binding and thread/read's full hydration is deprecated by the server) and a kill leaves
        it to finish server-side. The bracket is in the registry row too (compacting, saved by both latches and by
        every end's own transaction, 2026-09-21): the app-server is the kernel's child and ends with it, so a kernel
        restart mid-compaction can never learn the outcome, and _load_registry turns a row still reading compacting
        into a loud end worded as an unknown outcome (a noRetry notice, the mark `romp compact --wait` reads as a
        compaction's end), writing compacting False back, so the row restores no bracket that nothing would end and a
        second restart does not re-fire the notice; before, the load restored the launch error and no bracket, and a
        wait that had seen compacting printed done over an outcome nobody recorded. A compaction romp did not start
        (Codex's own, or one a restart ended this way while it ran on) re-latches the bracket through the worker's
        Compact refusal. A thread/resume during a foreign compaction is unprobed. Codex takes no compaction
        instructions; the kernel refuses words after the head before reaching here."""
        s = self._session(sid)
        if not s:
            return _contract.SessionBackend.compact(self, sid)
        c = self._get_client()
        if c is None:
            return self._client_failure_text()
        if not s.mode_lock.acquire(blocking=False):
            return "busy"
        try:
            with s.lock:
                if s.dead:
                    return "this session has ended — revive it first"
                parked = s.turn_rejection is not None and s.turn_rejection[0] == s.change_generation
                if s.turn_id or s.compacting or (s.queue and not parked):
                    return "busy"
                if s.tid.startswith("pending-") or s.tid.startswith("failed-"):
                    return "this session has no conversation to compact yet"
                loaded = s.loaded and s.loaded_client_generation == self._client_generation_for(c)
            if not loaded:
                try:
                    if not self._prepare_thread(s, c):
                        return "this session has ended — revive it first"
                except Exception as e:
                    self.log("compact %s: thread preparation failed: %s" % (s.name, e))
                    return "Couldn't compact this conversation: %s" % str(e)[:200]
            norm = self._ensure_norm(s)
            with s.norm_lock:
                empty = norm.last_uuid is None
            if empty:
                return "this conversation has nothing to compact yet"
            with s.lock:
                if s.dead:
                    return "this session has ended — revive it first"
                s.compacting = True
                s.compact_active_seen = False
                s.state = "compacting"
                s.since = time.time()
                tid = s.tid
                # the latch is in the row (2026-09-21): a restart mid-compaction ends it loudly (_load_registry). The
                # save is part of the request: a latch the row cannot hold would start a compaction whose restart
                # outcome nothing could record, a false durability claim, so it is unlatched here, under the same
                # lock and before anything was published, and refused in words (review find, 2026-09-21). No request
                # went out and no bracket stood for anyone, so the end counter does not move: a refusal could name
                # no compaction of this bracket's.
                unrecorded = self._save_compacting_locked(s)
                if unrecorded is not None:
                    s.compacting = False
                    s.compact_active_seen = False
                    s.state = "waiting"
                    s.since = time.time()
            if unrecorded is not None:
                self.log("compact %s: refused, the bracket could not be recorded: %s" % (s.name, unrecorded))
                return ("Couldn't compact this conversation: romp could not record it on disk, so a restart could "
                        "not tell how it ended (%s)" % str(unrecorded)[:160])
            self.push_session(sid)                 # the chip flips now; the ack is not the deciding event
            try:
                c.thread_compact(tid)
            except Exception as e:
                why = "Couldn't compact this conversation: %s" % str(e)[:200]
                with s.lock:
                    self._end_compact_locked(s, "loud", why)   # the words the caller shows are the end's record (2026-09-21)
                    self._save_compacting_locked(s)
                self.push_session(sid)
                self.log("compact %s: %s" % (s.name, e))
                return why
        finally:
            s.mode_lock.release()
        return ""

    def _end_compact_locked(self, s, kind, text=""):
        """End the compaction bracket; the caller holds s.lock (2026-09-19). Every end comes through here so the
        bracket-end counter advances with each — the clean idle-after-active end and the loud status end
        (_compact_status), the client's death (_global_pump), kill, a raising thread/compact/start (compact) and the
        worker's accepted turn/start (_run_turn_in_mode) — which is what lets a Compact refusal that reaches the
        worker after the compaction it names has ended stand down (_CompactionInFlight). The state falls back to
        waiting from compacting only; the worker's accepted turn sets working right after. The row's half of the end,
        compacting False in the registry, rides each caller's own transaction (_save_compacting_locked for the ends
        that write nothing else; kill's dead, resume's flip and the accepted turn's ACK carry the field in theirs),
        never a write of its own here: the loud ends' notice lands in the same write as the bit, so a kernel death
        between two writes cannot leave a row reading compacting False with no notice (2026-09-21).

        Every end also names its `kind` and is recorded with its words and a stamp (compact_last, published with the
        counter by compact_end(), 2026-09-21): "clean" for a compaction Codex completed (the idle after an active, a
        thread/compacted; a clean end with no normalizer to write its divider is still one, since the thread was
        compacted), "loud" for every end after which romp cannot vouch the thread was compacted, with `text` saying
        why: the systemError and notLoaded statuses, the client's death, a raising request, kill, and the worker's
        accepted turn, whose words say its end was never seen (no divider was written, whether the compaction ran or
        not; the pump's and the worker's queues have no order between them, so "never started" is not knowable). A kind for
        EVERY end, because `romp compact --wait` judges the record: an end with none would read as neither done
        nor failed, and the wait would run to its timeout. The record is in memory, unlike the row's bit, so a kernel
        restart starts the count over; the end _load_registry gives a row still reading compacting (a compaction the
        previous kernel never saw end) comes through here too (2026-09-22): the fresh session's bit is already down, so
        the call advances the count to one and records the restart notice's words as a loud end, which the wait reads
        as the restart's end by the record's identity, its count with this end's stamp, from every baseline (a count
        below the baseline, or the same count under a new stamp: the restart's record starts at one, so its count alone
        matches a baseline of one). Before that, the load wrote its notice and no record, and a message parked at the
        restart cleared the notice within milliseconds of the boot's re-arm."""
        s.compacting = False
        s.compact_active_seen = False
        s.compact_ends += 1
        s.compact_last = {"kind": kind, "text": text, "at": time.time()}
        if s.state == "compacting":
            s.state = "waiting"
            s.since = time.time()

    def _save_compacting_locked(self, s, *beside):
        """Save the bracket's bit to the registry row, with the fields the same end writes beside it in ONE transaction
        (2026-09-21, the post-merge review of the native compaction: the bracket lived in memory only, so a kernel
        restart mid-compaction read the row as compacting False with no notice). The caller holds s.lock,
        _save_registry's rule. A failing save is logged and RETURNED (the exception; None when it landed), never
        raised: compact()'s latch reads it and refuses the compaction, since a bracket the row cannot hold would start
        a compaction whose restart outcome nothing could record; every end ignores it, since the end has happened
        whatever the row says, and stands in memory as it did before the bit was durable. The worker's next save
        (the ACK, a rejection, a failure) carries the in-memory value whenever the registry is writable again, so
        the residual is a save that fails and a restart before the next successful one: that load reads the row as
        the last save left it (a bit still up after an end in memory fires the unknown-outcome notice over an end
        this kernel did see; a bit still down after a re-latch restores no notice for a compaction that ran)."""
        try:
            self._save_registry(s, fields=("compacting",) + tuple(beside))
        except Exception as e:
            self.log("compaction bracket registry save: %s" % traceback.format_exc())
            return e
        return None

    def _compact_status(self, s, method, p):
        """The pump's half of the compaction bracket (2026-09-19; the other halves are compact() and the worker's
        Compact refusal). Read for every global notification of the session's thread, outside norm_lock, and only
        while the bracket stands: False otherwise (the normalizer sees the notification as before), True when this
        notification ENDED the bracket, in which case the record, the poke and the push were done here.

        thread/status/changed: "active" marks that the compaction turn has been seen running; the first "idle" AFTER
        it is the deciding event — the compact_boundary is written through the normalizer's thread/compacted writer,
        the same golden-covered record an emitted notification would have written (uuid cb-<last turn id> with
        _mint's suffix on a repeat, parentUuid None, logicalParentUuid the pre-compaction leaf; no summary record,
        since Codex exposes no summary text), marked manual, and the bracket ends in the SAME section, under norm_lock
        and then s.lock (the order _append and set_model take), with the poke, the push and the worker's kick after
        both are released (a queue parked on the bracket drains): the record lands before the end is published, so a
        reader that sees the session no longer compacting also finds the divider, and no other end can land between
        the write and the end. Before (review find, 2026-09-22), the divider was written under norm_lock alone and the
        bracket ended in a later s.lock block: the worker's accepted turn/start, attempted under a bracket with no
        active seen, could end the bracket in that window, loud and worded as no divider having been written, over a
        divider already on disk, and the pump then found no bracket and recorded nothing, so compact_end() published
        loud over a compacted thread and `romp compact --wait` exited saying the session did not compact. The section
        re-checks the bracket (and, for an idle, the active seen: a bracket re-latched after another end has seen none)
        before writing, and answers False when it is gone, so the notification is the normalizer's as with no
        bracket. Runtime 0.153.3 sends no thread/compacted for a
        thread/compact/start and the pinned client's router discards the compaction turn's items, so the observed
        idle-after-active is the backend's own call to write it (live probe). An idle with NO active seen is the
        previous turn's tail — its idle status and its turn/completed leave the server at the same instant on two
        queues the pump and the worker drain independently, so a stale idle can be read after the latch — and is
        ignored. Named residual: if BOTH the previous turn's active and idle are still unread at the latch (the pump
        lagging a whole turn), the bracket ends falsely on that idle with a boundary written, busy() drops, a parked
        send's turn/start meets the Compact refusal and re-latches, and the real idle writes a second boundary; the
        envelope carries no time the pinned client exposes, so no exact fix exists with it. The same stale idle has a
        second face on the REFUSAL path (review find, 2026-09-19): a compaction romp did not start has no bracket, so
        its idle ends nothing here, and the worker's Compact refusal handled after it would re-latch a compaction
        already over — hence compact_idles, bumped for EVERY idle before the bracket test, the refusal's second
        staleness key (_CompactionInFlight). A failed compaction is told apart by the status itself (live leg
        2026-09-19, a model the account cannot use set on the thread at thread/resume): after the active status
        Codex sends "systemError" for the thread, and NO idle; the compaction turn's error notification (willRetry
        false, "Error running remote compact task: …") and its turn/completed with status failed carry the turn id,
        so the pinned router parks and discards them, and systemError is the whole signal that reaches this queue.
        thread/turns/list afterwards lists that turn as failed with the message, and the thread stays usable: a
        fresh app-server resumed it and ran a turn. So the idle-after-active writer never runs for a failed
        compaction, and "systemError" ends the bracket LOUDLY with no divider — as launch_error, the red card the
        next accepted turn clears, worded as Codex not having compacted; the failure's own message is not on this
        queue (a thread/turns/list read after the end would carry it: a follow-up, since it is one more RPC on the
        pump's thread). "notLoaded" ends it the same way, worded as the thread having stopped being available
        (it also follows romp's own thread/archive, and possibly eviction).

        thread/compacted for the thread while the bracket stands: the record is written here, marked manual, and the
        bracket ends; the idle that follows finds no bracket, so it is written once. Reachable only with a future
        runtime AND a future client that deliver it globally: runtime 0.153.3 never sends it, and the pinned client's
        router parks a turn-stamped one under its (unregistered) turn and discards it at that turn's completion, so
        under the pin the idle path is the one that runs (review find, 2026-09-19)."""
        loud = None
        with s.lock:
            kind = None
            if method == "thread/status/changed":
                status = p.get("status")
                kind = status.get("type") if isinstance(status, dict) else status
                if kind == "idle":
                    s.compact_idles += 1               # bracket or no bracket: the refusal's second staleness key
            if not s.compacting:
                return False
            if method == "thread/compacted":
                params = dict(p, trigger="manual")
            elif method == "thread/status/changed":
                if kind == "active":
                    s.compact_active_seen = True
                    return False
                if kind == "idle":
                    if not s.compact_active_seen:
                        return False                   # the previous turn's tail (see above)
                    params = {"threadId": s.tid, "trigger": "manual"}
                elif kind == "systemError":
                    params = None                  # the failed compaction's shape (live leg 2026-09-19): no idle follows
                    loud = ("Codex could not compact this conversation (it reported systemError); "
                            "the conversation continues as it was")
                elif kind == "notLoaded":
                    params = None
                    loud = ("This conversation stopped being available while it was compacting (Codex reported "
                            "notLoaded); nothing was compacted as far as romp can tell")
                else:
                    return False
            else:
                return False
        if params is not None:
            # The divider and the clean end are ONE section: norm_lock, then s.lock, the order _append (its echo prune)
            # and set_model already take, so no new lock edge; s.lock is an RLock, so _append's own take nests. With
            # the write under norm_lock alone and the end in a later s.lock block, the worker's accepted turn/start
            # ended the bracket between them, loud and worded as no divider having been written, over a divider on
            # disk, and this block then found no bracket and recorded nothing (review find, 2026-09-22; the docstring
            # has the consequence). Now every other end waits on s.lock while the divider lands and finds the bracket
            # gone; an end that landed first is found here instead, and nothing is written: the notification is the
            # normalizer's, as with no bracket. An idle re-checks the active too: a bracket compact() re-latched after
            # that end has seen none, and this idle is not its.
            # Within the section, two writes to two files in a fixed order, the divider to the transcript (_append)
            # first and the bit to the registry row (_save_compacting_locked) second, kept on purpose (2026-09-22, the
            # post-merge note on the durable bracket). A kernel death between them leaves the divider recorded with the
            # row still reading compacting, and the next load fires its unknown-outcome notice over a compaction that
            # did end, which is why that notice claims nothing about the conversation's markers (_load_registry). The
            # other order would leave, on the same death, a row reading compacting False with no notice over a
            # compaction whose divider was never written, and nothing revisits a clean row: a loud notice over a
            # recorded end beats a silent clean row over a compaction that may never have run.
            wrote = False
            with s.norm_lock:
                with s.lock:
                    if not s.compacting or (method == "thread/status/changed" and not s.compact_active_seen):
                        return False
                    if s.norm:
                        recs = s.norm.handle("thread/compacted", params)
                        if recs:
                            self._append(s, recs)
                            wrote = True
                    self._end_compact_locked(s, "clean", "")
                    # the end is in the row (2026-09-21): compacting False
                    self._save_compacting_locked(s)
                    queued = bool(s.queue) and not s.dead
            if not wrote:
                self.log("compaction of %s ended with no normalizer to write its boundary" % s.name)
        else:
            with s.lock:
                if s.compacting:                       # kill may have ended it meanwhile: one end, one advance
                    self._end_compact_locked(s, "loud", loud)
                # noRetry: nothing retries a compaction, so the chat's card carries no Retry and no countdown for this
                # notice (build_session lifts it onto the status as apiNoRetry; review, 2026-09-21). Saved UNDER s.lock,
                # _save_registry's rule and the _work handlers' shape: saved after the release, a turn accepted in that
                # window cleared the field and saved None, and this stale snapshot then committed the red card over
                # it, so the next restart restored a card the turn had cleared (review find, 2026-09-21).
                s.launch_error = {"text": loud, "at": time.time(), "limit": False, "noRetry": True}
                # the end is in the row (2026-09-21): compacting False, and the loud end's notice beside it in the same write
                self._save_compacting_locked(s, "launchError")
                queued = bool(s.queue) and not s.dead
        # Every end pokes, OUTSIDE norm_lock (see _append) and s.lock: the end is the event a message parked on the
        # bracket waits on. The clean end poked through its boundary write; the loud ends only pushed and kicked, so a
        # parked message waited for the pusher's half-second backstop instead (review find, 2026-09-21).
        self.poke()
        if loud:
            self.log("compaction of %s ended: %s" % (s.name, loud))
        self.push_session(s.sid)
        if queued:
            self._ensure_worker(s)
        s.kick.set()                                   # a worker parked on the bracket wakes; an idle one re-checks and waits
        return True

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
            bg = bg or (old[2] if len(old) > 2 else "") or s.color
            #    the FILE first: a kernel-side recolour (_set_session_color) writes names/ only
            #    and never updates s.color. The registry's colour is the fallback for a file with
            #    none — a rename that healed a MISSING file wrote an empty colour although the
            #    registry knew it (2026-09-11)
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
            # never a silently-missing session. The identity colour the caller picked rides on
            # the row and into names/ exactly as on the success path: a placeholder that dropped
            # it ran colourless for its whole life (the later thread create and the load-time
            # republish both copy the row's empty colour forward) and the kernel's picker, which
            # counts held colours from names/, handed the same colour to the next session
            s = _Session(sid, "pending-%s" % sid[:8], name, cwd, color=bg)
            s.launch_error = {"text": self._client_failure_text(), "at": time.time(),
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
            self._publish_spawn_name(s, bg, fg)    # a LIVE launch-error row without a shared name
            #                                        let a retry mint a duplicate live "web" (the
            #                                        v1.3.12 audit)
            return sid
        try:
            resp = c.thread_start({"cwd": cwd, **_approval_params(),
                                   **_execution_permissions(cwd, thread_start=True), **self._postal_params()})
            tid = resp.thread.id
            model = getattr(resp, "model", "") or ""
        except Exception as e:
            s = _Session(sid, "failed-%s" % sid[:8], name, cwd, color=bg)
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
            self._publish_spawn_name(s, bg, fg)    # same rules as the client-missing branch above
            return sid
        s = _Session(sid, tid, name, cwd, model=model, color=bg)
        s.loaded = True
        s.loaded_client_generation = self._client_generation_for(c)
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
            prior = (s.dead, s.loaded, s.name, s.cwd, s.state, s.since, s.change_generation,
                     s.compacting, s.compact_active_seen)
            s.dead = False
            s.loaded = False               # the worker thread/resumes before the next turn
            s.compacting = False           # a bracket kill ended, or a late latch on the dead row (review find,
            s.compact_active_seen = False  # 2026-09-19): the revived row starts with none; a compaction that outlived
                                           # the kill finishes server-side and gets no divider, as kill's rule says
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
                # compacting: the reset above, in the row (2026-09-21)
                self._save_registry(s, fields=("dead", "name", "cwd", "compacting"))
            except BaseException:
                # roll the flip back: with dead=False already published in memory, a FAILED
                # revive rendered a live lane beside its own reviveFailed message, and the next
                # kernel restart silently killed it again (the r28 verification, executed)
                (s.dead, s.loaded, s.name, s.cwd, s.state, s.since,
                 s.change_generation, s.compacting, s.compact_active_seen) = prior
                raise
        if queued:
            self._ensure_worker(s)
            s.kick.set()
        return True

    def relaunch(self, sid):
        """Restart session (the kernel's _restart_session door; see SessionBackend.relaunch): always a refusal here,
        in Codex's own terms. Every Codex session runs on this backend's ONE client, whose app-server is the kernel's
        child, so no session has a process of its own to replace. The client, and with it the binary its app-server
        started from, changes only when the kernel restarts or a client failure is recorded. So the base refusal's
        advice, end it and revive it, never reached a newer binary: kill cuts a running turn on the shared client,
        and resume marks the row live for its next turn on the same client (2026-09-24, the post-merge note on
        #2125). The refusal names what does start a fresh app-server instead, and touches nothing: not the session,
        not its turn, not the client. A row that is not live is pointed at Revive in SdkBackend.relaunch's words: the
        door hands an ended row here by its record, since owns() is live-only, and a row ended after the door's
        routing lands here too."""
        s = self._session(sid)
        if not s:
            return "romp has no record of this session"
        with s.lock:
            dead = s.dead
        if dead:
            return "this session is not running — revive it to bring it back"
        return ("Codex sessions share the romp kernel's Codex app-server, so this session has no process of its own "
                "to relaunch — restarting the romp kernel starts a fresh one for every Codex session, and cuts any "
                "Codex turn still running")

    def kill(self, sid):
        s = self._session(sid)
        if not s:
            return False
        save_error = None
        with s.lock:
            s.dead = True
            if s.compacting:
                # the compaction finishes server-side; romp cannot stop it, and writes nothing for a session it ended
                # (2026-09-19). Recorded loud: revived, the row cannot vouch the thread was compacted (2026-09-21)
                self._end_compact_locked(s, "loud", "This session was ended while it was compacting; the compaction "
                                         "finishes on Codex's side, and romp recorded no outcome for it")
            turn_id, tid, worker = s.turn_id, s.tid, s.worker
            # Persist the lifecycle mutation before releasing the session lock. A concurrent resume
            # must order after this write instead of being overwritten by a delayed kill snapshot.
            try:
                # compacting: a bracket kill ended, in the row (2026-09-21)
                self._save_registry(s, fields=("dead", "compacting"))
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
            try:
                self._write_name(s)       # keep the shared identity file in sync (colours preserved)
            except BaseException:
                s.name = old_name         # compensate: the registry write above is re-run with
                #                           the old name so the stores stay agreed; the raise
                #                           still reaches the caller (loud)
                # No restore write for the names file: _write_name is tmp + os.replace and removes
                # its own temp (the r32 shape), so a raise leaves names/<sid> exactly as it was — and
                # creates nothing when there was no file. The in-place nf.write_bytes the r29/r30
                # branches carried predates that: it was the one non-atomic write on this path, an
                # mtime bump for no content change, and under the very ENOSPC it existed for it
                # truncated a good file to nothing, then blamed "a failed write" (review, 2026-09-08).
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
                                        name="codex:%s" % s.name)   # kind:payload: the kernel's stack sample keeps the kind
            s.worker.start()

    def _create_thread(self, s, c, cwd, model):
        """thread/start for `s` and the swap of its row onto the new thread: the body _prepare_thread's
        placeholder branch always had, shared since 2026-09-19 with the restart fallback below. False when
        the session was killed while the create was in flight."""
        params = {"cwd": cwd, **_approval_params(s.mode),
                  **_execution_permissions(cwd, thread_start=True), **self._postal_params()}
        if model:
            params["model"] = model    # picked while the row was a placeholder: born on it
        resp = c.thread_start(params)
        loaded_client_generation = self._client_generation_for(c)
        touched = self._path_for(cwd, resp.thread.id)
        touched.touch()                # BEFORE the row names the tid (review find, 2026-09-19): discovery skips a row whose
        #                                file does not stat, so saved first, a discover landing in the gap (the restart
        #                                fallback's included) listed the board without this session until the next save;
        #                                the order clear() keeps. Both rollbacks below remove it again
        with s.lock:
            if s.dead:
                _unlink_quiet(touched)
                return False
            prior = (s.tid, s.model, s.loaded, s.loaded_client_generation)
            s.tid = resp.thread.id
            # The pick outlives the create. The server's reply names ITS model, never empty
            # (ThreadStartResponse.model is a required string), so `resp.model or s.model` let
            # the default overwrite a model the user chose on the pending-/failed- row, saved
            # it below, and ran every turn on it with no word to anyone (2026-09-11).
            s.model = s.model or getattr(resp, "model", "") or ""
            s.loaded = True
            s.loaded_client_generation = loaded_client_generation
            try:
                self._save_registry(s, fields=("tid", "model"))
            except BaseException:
                # publish NOTHING on a raise: with the real tid only in memory, every retry
                # took the resume path and never re-saved it — the next kernel restart
                # loaded 'pending-…' and silently started a FRESH Codex thread (the r29
                # verification). Rolled back, the loud retry re-runs thread_start; an
                # orphaned server-side thread beats a silently forked conversation.
                (s.tid, s.model, s.loaded, s.loaded_client_generation) = prior
                _unlink_quiet(touched)
                raise
        with s.norm_lock:
            s.norm = None
        self._ensure_norm(s)
        return True

    @staticmethod
    def _never_turned(path):
        """romp's own record that no turn ever ran on a thread: the materialized file it touched at the
        thread's birth (spawn, clear) is still EMPTY — every turn appends at least its user record. An
        unreadable or missing file is not that record (2026-09-19)."""
        try:
            return os.path.getsize(str(path)) == 0
        except OSError:
            return False

    def _prepare_thread(self, s, c):
        """Resume a durable thread, or turn a visible pending/failed placeholder into a real one.

        A thread that never ran a turn has no rollout on the app-server, so a fresh app-server (a kernel
        restart, a replaced client) refuses to resume it — `no rollout found for thread id …`, an
        InvalidRequestError the permanent-rejection rule would park the queue on for good (live probe,
        2026-09-19). clear() mints exactly such a thread and saves its tid (spawn's thread is the same until
        its first turn), so on THAT refusal, for a thread romp's own record says never turned (its file is
        empty, _never_turned), the thread is re-created ONCE with the row's params and the row swapped onto
        the new one — the create branch's own transaction, with its rollback — loudly logged; the empty file
        of the thread that never ran leaves with it. Keyed on both facts: the same refusal for a thread whose
        file holds records is a real inconsistency (a rollout gone missing server-side) and parks as before,
        never a re-create over a conversation romp has records of. The re-created thread is still turn-less
        with an empty file, so its first record is a ROOT and the episode boundary lands where it would have.

        The resume reply is never read here, and the pinned SDK validates it only after the app-server
        has answered success, so a reply its models cannot parse is not a failed resume: it is logged
        once, without any value from the reply, and the turn goes on to turn/start, the next gate, which
        stays loud about a thread the server does not hold (2026-09-19, after a session parked forever
        on such a reply across a kernel restart).
        """
        with s.lock:
            if s.dead:
                return False
            tid, cwd, model = s.tid, s.cwd, s.model
            create = tid.startswith("pending-") or tid.startswith("failed-")
        if create:
            if not self._create_thread(s, c, cwd, model):
                return False
            try:
                self._write_name(s)
            except (OSError, UnicodeDecodeError) as e:
                # the thread is HEALTHY — failing the turn over a cosmetic identity write would
                # be worse (a decode failure from crash residue sailed through an OSError-only
                # catch and DID fail the turn, unhealed forever — the r31 verification). No
                # restore write for the names file: _write_name is tmp + os.replace and removes
                # its own temp (the r32 shape), so a raise leaves names/<sid> exactly as it was —
                # and creates nothing when there was no file. The in-place nf.write_bytes(old_line)
                # this branch carried predates that: it was the one non-atomic write on this path,
                # an mtime bump for no content change, and under the very ENOSPC it existed for it
                # truncated a good file to nothing, then blamed "a failed write" (#1138 dropped the
                # same shape from rename; this is its twin, 2026-09-09). The log still says which
                # of the two states the file is in — READ after the fact, never rewritten.
                if (self.state / "names" / s.sid).is_file():
                    self.log("codex: names/%s write failed after thread start (%s) — the identity "
                             "file kept its old line; it refreshes on the next rename" % (s.sid, e))
                else:
                    self.log("codex: names/%s could not be published after thread start (%s) — "
                             "the session runs UNNAMED on shared surfaces until a rename lands; "
                             "a same-name create may collide meanwhile" % (s.sid, e))
            self.push()
            return True
        try:
            c.thread_resume(tid, {"cwd": cwd, **_approval_params(s.mode),
                                  **_execution_permissions(cwd, thread_start=True), **self._postal_params()})
        except Exception as e:
            if _is_response_model_mismatch(e):
                # The only RPC replies this backend consumes are thread/start's thread id and turn/start's
                # turn id; the resume reply is discarded, and the SDK raises on it AFTER the server's
                # success answer, with the thread resumed. A thread whose history held an item kind the
                # SDK's generated models predate (a subAgentActivity kind the runtime added later) raised
                # here on every attempt, each filed as a failed turn with the whole error text as its
                # launchError, and the session could not run another turn until it was ended and its
                # thread lost (2026-09-19). Say what happened once, in identifiers only (never str(e) or an
                # input: a 'missing' error's input is the whole item; the failing items by position, not
                # the first error's location, which names whichever union member pydantic tries first),
                # naming the SDK that actually raised (the installed one, which need not be the pin), and
                # proceed: turn/start is the authoritative gate and stays loud if the thread is really not
                # there.
                self.log("codex thread/resume for %s succeeded, but %s cannot read the reply: %s; the "
                         "app-server has resumed the thread, so the turn proceeds and turn/start decides. "
                         "A newer openai-codex reads those items."
                         % (s.sid, _sdk_for_mismatch_log(), _response_model_mismatch_summary(e)))
            else:
                # not the reply parse: the two 2026-09-19 arms meet here (native clear + resume tolerance)
                stale = self._path_for(cwd, tid)
                if "no rollout found" not in str(e) or not self._never_turned(stale):
                    raise
                self.log("codex: thread %s of %s has no rollout on this app-server and never ran a turn; "
                         "re-creating it instead of parking the resume (%s)" % (tid, s.name, e))
                if not self._create_thread(s, c, cwd, model):
                    return False
                try:
                    stale.unlink(missing_ok=True)  # the empty file of the thread that never ran
                except OSError:
                    pass
                self.push()
                return True
        loaded_client_generation = self._client_generation_for(c)
        with s.lock:
            if s.dead:
                return False
            s.loaded = True
            s.loaded_client_generation = loaded_client_generation
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
                        compaction_seen = s.compacting and s.compact_active_seen
                    if not queued:
                        break
                    if compaction_seen:
                        # A compaction turn is running on the thread (2026-09-19): the server refuses turn/start until
                        # it ends, so no attempt is made. The bracket's end kicks (_compact_status); no timer, no
                        # backoff, no launch_error — waiting is a state, not a failure. A bracket compact() latched
                        # with NO active seen yet is not this: the worker attempts, since the server is the authority
                        # from the ack on (the live probe's refusal preceded the active status), and the answer is
                        # exact either way — accepted ends the bracket, refused latches it with the active seen.
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
                    except _CompactionInFlight as e:
                        with s.lock:
                            dead = s.dead
                            stale = s.compact_ends != e.compact_ends or s.compact_idles != e.compact_idles
                            if not dead and not stale:
                                s.compacting = True
                                s.compact_active_seen = True   # the server said the compaction turn is active
                                if s.state != "compacting":
                                    s.state = "compacting"
                                    s.since = time.time()
                                self._save_compacting_locked(s)   # the re-latch is in the row too (2026-09-21)
                        if dead:
                            # a kill landed between the request and this handler (it found no bracket to end, so the
                            # counters are unchanged): no latch on a dead row, which resume() would revive as compacting
                            # with nothing left to end it (review find, 2026-09-19); the loop exits on the dead row
                            break
                        if stale:
                            # the compaction the server named has been seen to end since this request went out (its
                            # statuses reached the pump first): a re-latch would stand until nothing. Retry at once;
                            # retry_delay is untouched, since nothing failed.
                            self.log("compaction refusal for %s arrived after its end; retrying" % s.name)
                            continue
                        self.log("compaction in flight on %s: the turn waits for its end" % s.name)
                        self.push_session(s.sid)
                        break                          # the untimed wait: the bracket's end kicks
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
                                # compacting: the in-memory bracket rides the worker's every save, so the last write
                                # before a restart carries an end whose own save failed (2026-09-21)
                                self._save_registry(s, fields=("launchError", "compacting"))
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
                                # compacting: as the rejection's save above (2026-09-21)
                                self._save_registry(s, fields=("launchError", "compacting"))
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
                    # parked is set only once the clear above is behind us, so a kick that arrives
                    # after a reader saw it can no longer be discarded before the wait sees it
                    s.parked.set()
                    s.kick.wait(delay)
                    s.parked.clear()
                    s.kick.clear()
        finally:
            with s.lock:
                if s.worker is threading.current_thread():
                    s.worker = None

    def _run_turn(self, s):
        # Mode changes take effect between turns. Nonblocking set_mode refuses while this
        # lock is held, including thread preparation and the turn/start acknowledgement gap.
        # The turn-end poke and push fire AFTER the lock is released (2026-09-19): the kernel's parked-op
        # drain runs on that poke, and a clear() it fires takes this same lock non-blocking — poked from
        # inside the lock, every clear parked mid-turn met "busy" on the very poke that announced the
        # turn's end and waited for the pusher's clock instead. Released first, the poke IS the retry
        # event. _run_turn_in_mode records that a turn ended and leaves the announcing to here.
        try:
            with s.mode_lock:
                return self._run_turn_in_mode(s)
        finally:
            with s.lock:
                ended, s.turn_ended = s.turn_ended, False
            if ended:
                self.poke()                    # the turn END is the kernel's cue (parked ops deliver on it): every
                self.push_session(s.sid)       # in-loop poke above fired while turn_id was set, i.e. busy() True

    def _run_turn_in_mode(self, s):
        c = self._get_client()
        if c is None:
            try:
                with s.lock:
                    s.launch_error = {"text": self._client_failure_text(), "at": time.time(),
                                      "limit": False}
                    # compacting: as the worker's other saves (2026-09-21)
                    self._save_registry(s, fields=("launchError", "compacting"))
            except Exception:
                self.log("client failure registry save: %s" % traceback.format_exc())
            self.push_session(s.sid)
            return False                   # queue stays parked; worker retries after the deadline
        prepare_client_generation = self._client_generation_for(c)
        with s.lock:
            # `loaded` holds per app-server. When the pump saw the old server die and _get_client
            # built this replacement, the thread stayed "loaded" on a process that had never seen
            # it, and turn/start went out with no thread/resume before it: the server refuses that
            # with "thread not found", a permanent rejection every resend met again until a kernel
            # restart re-read the registry (2026-09-11). Resume it on the server it will run on.
            loaded = s.loaded and s.loaded_client_generation == prepare_client_generation
            prepare_change_generation = s.change_generation
        if not loaded:
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
            compact_ends = s.compact_ends          # the bracket-end counter beside the request (_CompactionInFlight)
            compact_idles = s.compact_idles        # ...and the thread's idle count, its second staleness key
        # Do not hold the lifecycle lock across an app-server RPC: kill must stay prompt even when
        # turn/start itself stalls. Sends may append meanwhile; the snapshotted prefix stays in place.
        client_generation = self._client_generation_for(c)
        try:
            started = c.turn_start(tid, [{"type": "text", "text": t} for t in batch], params)
        except Exception as e:
            if _is_compaction_refusal(e):
                raise _CompactionInFlight(e, compact_ends, compact_idles) from e   # a state the worker latches, never a failure (2026-09-19)
            if _is_permanent_request_rejection(e):
                raise _PermanentRequestRejection(
                    e, "turn", change_generation, client_generation) from e
            raise
        turn_id = started.turn.id
        ack_persisted = False
        stream_failed = False
        bracket_ended = None
        try:
            # where this turn's records will begin: the file's last record now, the mark's anchor (2026-09-22). Read under
            # norm_lock and before s.lock, the order _append takes them; the turn's own notifications wait in its queue
            # until the loop below, so none of them is written yet
            with s.norm_lock:
                anchor = norm.last_uuid
            with s.lock:
                if s.queue[:len(batch)] != batch or s.queue_ids[:len(batch_ids)] != batch_ids:
                    raise RuntimeError("Codex send queue prefix changed during turn/start")
                del s.queue[:len(batch)]
                del s.queue_ids[:len(batch_ids)]
                if s.compacting:
                    # An ACCEPTED turn is the exact event that no compaction is active on the thread (2026-09-19): a
                    # bracket compact() latched that the server never took up (acked, no active status ever), or
                    # whose compaction finished before this request went out. Ended here, with no divider written
                    # for it, and the line below says which. Its statuses, if they come late, meet no bracket: the
                    # pump re-checks the bracket under this same lock, in the one section that writes the divider
                    # and ends it (_compact_status, 2026-09-22), so an idle whose divider is already landing holds
                    # this ACK until the bracket has ended clean, and this end and that divider never cross.
                    bracket_ended = ("finished server-side before this accepted turn" if s.compact_active_seen
                                     else "started nothing on this thread")
                    # Recorded LOUD on either face, worded as the end never having been seen (2026-09-21): no divider
                    # was written, so romp cannot vouch the thread was compacted, and `romp compact --wait` must not
                    # print done over it. The no-active face is not worded as the compaction never having started: the
                    # pump and the worker drain independent queues, so the compaction may have run with its statuses
                    # still unread when the turn was accepted, an order romp cannot know; the log line above keeps the
                    # faces apart for the record's reader
                    self._end_compact_locked(s, "loud", (
                        "the compaction's end was never seen: Codex accepted a turn while it stood, so if it finished "
                        "on Codex's side romp wrote no divider for it" if s.compact_active_seen else
                        "the compaction's end was never seen: Codex accepted a turn while it stood and romp saw no status "
                        "for it, so whether it ran or not, no divider was written"))
                s.turn_id = turn_id
                s.state = "working"
                s.since = time.time()
                s.launch_error = None
                s.turn_rejection = None
                # the row names the accepted turn in the same write that takes its prompt off the queue (2026-09-22):
                # a kernel exit from here to the turn's end leaves the mark, which the next load settles
                s.turn_mark = {"id": turn_id, "tid": tid, "at": s.since, "after": anchor}
                killed_during_start = s.dead
                try:
                    # compacting: a bracket this turn ended, in the row (2026-09-21)
                    ack_mismatch = self._save_registry(
                        s, fields=("launchError", "compacting", "turn"), queue_ack=batch_ids)
                except Exception:
                    # turn/start already succeeded, but the durable ACK did not. Restore the exact
                    # prefix before retrying so this process agrees with the still-queued disk row.
                    s.queue[:0] = batch
                    s.queue_ids[:0] = batch_ids
                    raise
                ack_persisted = True
            if ack_mismatch:
                self.log("registry queue ACK mismatch for %s; preserving durable queue" % s.sid)
            if bracket_ended:
                self.log("codex compaction %s (%s): no divider for it" % (bracket_ended, s.name))
            self.push_session(s.sid)
            if killed_during_start:
                try:
                    c.turn_interrupt(tid, turn_id)
                except Exception as e:
                    self.log("kill interrupt %s: %s" % (s.name, e))
            while True:
                try:
                    n = c.next_turn_notification(turn_id)
                except Exception:
                    stream_failed = True       # the transport is down: see the except below
                    raise
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
        except Exception as exc:
            # Whatever ended the loop, the app-server's turn is now UNTRACKED: unregister in finally
            # releases its routing, so its later notifications are dropped, and with turn_id cleared
            # neither interrupt() nor kill() can reach it. Before the ACK the request is still durable,
            # so this keeps the acknowledged turn from continuing alongside its retry; after it, the
            # turn would keep executing in the sandbox with busy() False and no way to stop it.
            # ONLY when the failure was on our side (a transcript write, a normalizer raise) with the
            # transport up, though. A raise from the READ means the SDK's reader thread is gone: the one
            # writer of an exception into a turn queue is the router's fail_all, run once from that
            # thread's own except (pinned wheel, client.py _reader_loop). An RPC now would wedge this
            # worker for good: _request_raw waits on its reply with no timeout, fail_all has already
            # failed every waiter it will ever fail, and close() fails none, so the request is written to
            # a process nothing reads answers from. busy() would read True forever, mode_lock stay held,
            # kill() time out on the join and skip the drain. The global pump reads the same failure and
            # closes that client, terminating the app-server, so the turn dies with it and there is
            # nothing left to interrupt.
            if not stream_failed:
                try:
                    c.turn_interrupt(tid, turn_id)
                except Exception as e:
                    if ack_persisted:
                        self.log("abandoned turn interrupt %s: %s" % (s.name, e))
                    else:
                        self.log("unpersisted turn interrupt %s: %s" % (s.name, e))
            if ack_persisted:
                # The ACK consumed the prompt from the queue, so this turn has no retry and the file is
                # the only place its end can be recorded: settle it there (the held final reply lands,
                # then an end_turn record carrying the failure, codex_events.abandoned). No notification
                # will do it — a dead transport sends none, and a turn the SDK no longer routes drops its
                # own turn/completed — and an open file turn reads as working on every surface and
                # absorbs the next prompt. The finally's poke/push announce the records. The append may
                # be exactly what raised: its failure is logged, never allowed to mask the original.
                try:
                    with s.norm_lock:
                        recs = norm.abandoned(turn_id, "codex turn failed: %s" % exc)
                        if recs:
                            self._append(s, recs)
                except Exception:
                    self.log("abandoned turn settle %s: %s" % (s.name, traceback.format_exc()))
            raise
        finally:
            try:
                c.unregister_turn_notifications(turn_id)
            except Exception:
                pass
            end_save_err = None
            with s.lock:
                s.turn_id = None
                s.state = "waiting"
                s.since = time.time()
                s.turn_ended = True        # _run_turn pokes and pushes once the turn lock is released (see there)
                s.turn_mark = None
                if ack_persisted:
                    # the turn is over, its end written above: the row stops naming it (2026-09-22), one more
                    # registry write per turn. A failed clear leaves a stale mark, which the next load drops once
                    # the transcript shows the turn ended (_settle_restart_turn)
                    try:
                        self._save_registry(s, fields=("turn",))
                    except Exception:
                        end_save_err = traceback.format_exc()
            if end_save_err:
                self.log("turn end registry save (%s): the row still names the ended turn until the next load "
                         "drops it: %s" % (s.name, end_save_err))
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
            # `_echo_text` marks the atom as an INPUT ECHO to the kernel, as SdkBackend's echo atoms do:
            # _merge_live_atoms hides it behind its queued bubble (shown_texts) and never counts it as
            # live work (an echo-only merge keeps the turn's real ended state), and build_session's
            # queued-bubble pass enlists it while the session is busy. Without the marker an echo paints
            # as a solid user atom beside its own queued bubble and forces the last turn open: a false
            # "working" chip for a session whose only live item is a pending send.
            # `command` rides only on clear()'s acknowledging chip (2026-09-19): the kernel dedups the durable
            # gesture against it, renders it on the user's side, and _merge_live_atoms never counts a command
            # atom as live work; prune_live retires it by the human floor, the one exit a chip whose text never
            # lands has
            return [{"type": "user", "uuid": e["uuid"], "session_id": sid, "fsid": s.tid,
                     "t": e["t"], "parentUuid": None, "author": "human", "_echo_text": e["text"],
                     **({"command": e["command"]} if e.get("command") else {}),
                     "message": {"role": "user",
                                 "content": [{"type": "text", "text": e["text"]}]}}
                    for e in s.echoes]

    def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
        """Drop the optimistic input echoes the transcript has caught up on, in the SessionBackend
        contract's full call shape (the kernel's _merge_live_atoms passes sid, tx_uuids, tx_text_t and
        human_floor positionally; tests/test_backend_call_parity.py pins the shape against every backend).

        An echo retires on the events SdkBackend.prune_live names: its uuid is on disk, or its text
        LANDED. `tx_user_texts` as a MAPPING (text -> the newest record time carrying it) lands the echo
        only through a record written at or after its own send: this prune sees the whole transcript, and
        without that floor a repeated text ("ok" twice) would retire the second echo the moment it was
        sent (the SDK's T237b case); a plain set (an older caller) keeps the unfloored match. Texts are
        compared under echo_text_key on BOTH sides. Record times are parse_z's whole seconds, so send()
        stamps the echo with int(time.time()) as the SDK echoes do: a float stamp would keep an
        echo whose record was written later in the same second. The backend's own _append retire is the
        other exit; it sees only the records it just wrote and takes one echo per landed text block, the
        oldest carrying the text (a turn started from several queued sends lands as one record with a
        block per send; _atom_user_texts yields each block, so this prune lands every echo of such a turn
        too).

        `human_floor` (the newest genuine-human record's time) retires only a COMMAND chip by it (2026-09-19:
        clear()'s acknowledging "/clear" echo, the one echo here carrying `command`) — the SDK's stale-command
        rule, the one exit a chip whose text never lands has, and STRICTLY later (the kernel's _echo_overtaken
        rule, not the SDK's at-or-later), so a first prompt into the fresh conversation in the chip's own second
        leaves the chip until the next human record. No floor retires a PLAIN input echo on any backend: a send
        the app-server never records must stay visible; send() mints plain echoes only."""
        s = self._session(sid)
        if not s:
            return
        uuids = tx_uuids or ()
        text_t = tx_user_texts if isinstance(tx_user_texts, dict) else None
        keys = None if text_t is not None else {echo_text_key(t) for t in (tx_user_texts or ())} - {""}

        def _landed(e):
            if e.get("uuid") in uuids:
                return True
            if e.get("command") and human_floor and float(human_floor) > float(e.get("t") or 0):
                return True                    # the next human record retires the acknowledging chip
            key = echo_text_key(e.get("text"))
            if not key:
                return False
            if text_t is None:
                return key in keys
            return key in text_t and float(text_t[key] or 0) >= float(e.get("t") or 0)

        with s.lock:
            kept = [e for e in s.echoes if not _landed(e)]
            if len(kept) != len(s.echoes):
                s.echoes = kept

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

    def model_switches_live(self):
        # set_model lands at the NEXT turn_start (above) while a send steers the LIVE turn: a pick fired
        # mid-turn would let a send typed right after it steer the old model first — so it parks (#923 fold)
        return False

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
