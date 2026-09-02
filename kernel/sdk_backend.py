"""SdkBackend — non-tmux romp sessions driven by the Claude Agent SDK.

A second SessionBackend that coexists with tmux (selectable per session). It runs
the SAME `claude` binary romp launches in tmux, so it writes the SAME transcripts
to the SAME paths (the read side — event model, judges, panes — is unchanged).
What changes is the control channel: a long-lived SDK client per session instead
of TUI-scraping. Design: docs/sdk-backend.md.

Concurrency: the kernel is threaded and synchronous (no asyncio). Each SDK session
runs in its own daemon thread that owns a private asyncio loop (quarantined — the
loop never escapes the thread); the kernel bridges via thread-safe scheduling.
State is event-based (a turn enqueued -> working; ResultMessage -> waiting), per
the repo's "events over heuristics" rule.

The module imports cleanly WITHOUT claude_agent_sdk; the SDK is imported lazily
when a session actually starts, so the tmux-only path keeps zero third-party deps
and the kernel degrades gracefully when the SDK is absent.
"""
from __future__ import annotations
import asyncio
import difflib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

# ---------------------------------------------------------------------------
# Pure translation logic (no SDK import — unit-tested in CI without the dep).
# ---------------------------------------------------------------------------

# Identity palette — SELECTABLE now (the user 2026-07-12): the sets live in romp_palette (the single
# source of truth; bin/romp reads the kernel-maintained STATE/palette-colors mirror of the same data).
# The tmux launcher picks the first unused colour; for an SDK session we pick deterministically by a
# stable hash of the sid (the launcher's own fallback when all are taken), so the session gets a
# consistent colour without cross-backend "used" bookkeeping.
from importlib.machinery import SourceFileLoader as _SFL
_pal = _SFL("romp_palette", str(Path(__file__).resolve().parent / "palette.py")).load_module()


def _bin_on_path_env(environ) -> dict:
    """The env overlay for a spawned CLI: the repo's bin/ prepended to PATH, or {} when it is already
    there. Pure on its input for tests; _options passes os.environ. Kept additive on purpose — the SDK
    merges options.env over the inherited environment, so returning only PATH changes nothing else."""
    rbin = str(Path(__file__).resolve().parent.parent / "bin")
    cur = environ.get("PATH", "")
    if rbin in cur.split(os.pathsep):
        return {}
    return {"PATH": (rbin + os.pathsep + cur) if cur else rbin}


def pick_identity_color(sid: str, state_dir=None) -> tuple[str, str]:
    """A stable (bg, fg) for a session, hashed from its sid into the ACTIVE identity palette
    (STATE/palette, the gear's Session-colors pick; the default set when state_dir is unknown)."""
    import zlib
    name = _pal.active_name(state_dir) if state_dir else _pal.DEFAULT
    bgs, fgs = _pal.colors(name), _pal.fgs(name)
    i = zlib.crc32(sid.encode()) % len(bgs)
    return bgs[i], fgs[i]


# Reasoning effort for SDK sessions. effort is a CONNECT-TIME CLI flag (--effort) with no runtime control,
# and the init message does NOT echo it back, so romp sets it explicitly and tracks it (otherwise the picker
# can't show a true value). "high" suits agentic coding; the user changes it per session via the picker.
DEFAULT_EFFORT = "high"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultracode")   # ultracode = xhigh + workflow orchestration (per session)
# Cap on a SINGLE stdout JSON message from the CLI. The SDK default is 1 MB, and one message routinely
# exceeds that in a coding session (a big file Read, a large Bash/grep result, a base64 image echoed in a
# tool_result). When it does, the SDK transport raises "JSON message exceeded maximum buffer size", which
# kills our receive loop (drain) → the client tears down → the CLI's stdin closes → every PENDING permission
# request (an AskUserQuestion picker or an Allow/Deny) is rejected with "Tool permission stream closed before
# response received". The symptom: a picker renders and instantly returns without ever asking (the user
# 2026-07-04, reproduced live). Raise the ceiling well past any realistic single message; it's a transient
# per-message buffer, not a standing allocation, so a high cap costs nothing until a message actually needs it.
SDK_MAX_BUFFER = 100 * 1024 * 1024


def pretty_model(raw: str) -> str:
    """A raw SDK model id → the short badge the tmux statusline shows, so SDK and tmux sessions read the
    same and the model picker's 'current' highlight (which matches on the leading word) lights up.
    'claude-opus-4-8' → 'Opus 4.8', 'claude-haiku-4-5-20251001' → 'Haiku 4.5', 'claude-fable-5' → 'Fable 5'.
    Unrecognised ids pass through verbatim."""
    if not raw:
        return ""
    m = re.match(r"claude-([a-z]+)-(\d+)(?:[-.](\d+))?", raw)
    if not m:
        return raw
    fam, maj, minor = m.groups()
    return f"{fam.capitalize()} {maj}" + (f".{minor}" if minor else "")


def model_label(live: str, chosen: str) -> str:
    """The model badge to show for an SDK session. Prefer the LIVE name once the init / assistant message has
    echoed it; otherwise fall back to a best-effort label from the CHOSEN alias so a freshly-created session
    shows its model RIGHT AWAY (the user 2026-06-24) — like a tmux session does on launch — instead of a blank
    until the first turn. A raw id → pretty_model ('claude-opus-4-8' → 'Opus 4.8'); a CLI alias (opus/sonnet/…)
    → capitalised (matches set_model's live-change label); 'default'/unset → '' (the real default name fills in
    on connect from get_context_usage(), which _amain pulls before the first turn)."""
    if live:
        return live
    if not chosen or chosen == "default":
        return ""
    return pretty_model(chosen) if chosen.startswith("claude-") else chosen.capitalize()


def _alias_label(alias: str) -> str:
    """A best-effort DISPLAY label for a chosen model ALIAS before the real live name lands — the same
    label model_label falls back to (pretty id, or capitalised alias, '' for default)."""
    if not alias or alias == "default":
        return ""
    return pretty_model(alias) if alias.startswith("claude-") else alias.capitalize()


def _is_compact_cmd(text: str) -> bool:
    """True if `text` is a /compact invocation (bare or with custom-summary args) — the CLI interprets it as
    the compaction command whether it comes from the compact button, a parked-op delivery, or the user
    typing it. Drives the authoritative SdkSession._compacting bracket."""
    t = (text or "").strip()
    return t == "/compact" or t.startswith("/compact ")


def _is_clear_cmd(text: str) -> bool:
    """True if `text` is a /clear invocation. Drives the authoritative SdkSession._clearing bracket, the
    chat's live "clearing" indicator — without it the stretch between the /clear delivery and the CLI
    minting the fresh transcript has no observable state at all (the episode boundary is detected only
    after the fact)."""
    t = (text or "").strip()
    return t == "/clear" or t.startswith("/clear ")


def _model_reflects_alias(live_pretty: str, alias: str) -> bool:
    """Does the LIVE model display name reflect the chosen ALIAS — i.e. the switch has taken effect? A
    bare alias ('opus') is a substring of its pretty name ('Opus 4.8'), case-insensitively; 'default'/''
    matches any real name (the resolved default). Used to clear the switching-dots the instant the new
    model actually lands (the user 2026-07-03), so the badge never lingers on stale dots OR a stale name."""
    if not live_pretty:
        return False
    if not alias or alias == "default":
        return True
    a = alias.lower()
    a = a.split("-")[1] if a.startswith("claude-") and "-" in a else a   # claude-opus-4-8 → opus
    return a in live_pretty.lower()


# ── conversation rewind (the chat's edit-message branch) ──────────────────────
# Editing a past user message rewinds the conversation to just before it and sends the edited
# text as the next turn — the cloud-UI edit semantics. Mechanism (verified live 2026-07-16):
# the CLI's designed `--resume-session-at <record uuid>` flag loads only messages up to and
# including the target, and the next turn is appended to the SAME transcript file with
# parentUuid=target — an IN-PLACE branch, same fsid, no lastSid churn. The event model's
# leaf→root walk (FileAdapter) already drops the abandoned tail as a "rewind" line, so chat,
# timeline and judge all heal from the same parse with no extra plumbing. A bogus/pre-compaction
# target makes the CLI exit 1 with "No message found" BEFORE touching the transcript — the
# failure mode is loud and lossless (see SdkSession._rewind_failed for how romp surfaces it).

def transcript_path(cwd: str, fsid: str) -> str:
    """The on-disk transcript for session `fsid` launched from `cwd` — Claude's projects layout.
    Realpath first (a symlinked launch dir writes under the PHYSICAL path), then every
    non-alphanumeric char becomes '-' (matches the CLI exactly; '_' and ' ' included)."""
    proj = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.path.expanduser(cwd or "~")))
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
                    "projects", proj, fsid + ".jsonl")   # per-kernel Claude root (plans/multi-kernel.md phase 2)


def last_record_uuid(path, tail_bytes: int = 262144) -> str:
    """The uuid of the LAST uuid-bearing record in a transcript — the conversation's current
    leaf. Reads only the file's tail (a transcript can be tens of MB; the leaf is always within
    the last few records — uuid-less trailers like last-prompt/queue-operation are skipped).
    '' when the file is missing, empty, or holds no uuid in the window."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except OSError:
        return ""
    for line in reversed(chunk.splitlines()):
        if b'"uuid"' not in line:
            continue
        try:
            u = json.loads(line).get("uuid")
        except Exception:
            continue        # a partial first line of the window / junk — keep scanning back
        if u:
            return u
    return ""


def rewind_disposition(rewind_to: str, rewind_leaf: str, leaf_now: str) -> str:
    """Should a (re)connect apply a pending conversation rewind? ONE-SHOT and event-guarded:
    "apply"  — a rewind is pending and the transcript's leaf is still the one recorded at
               request time (nothing has landed since the user asked) → launch with
               --resume-session-at.
    "spent"  — a rewind is pending but the conversation MOVED past the recorded leaf: the
               rewind turn itself landed (the normal case — e.g. a crash-heal resume mid-turn),
               so re-applying would truncate real work. Drop the flag, resume plainly.
    "none"   — no rewind pending."""
    if not rewind_to:
        return "none"
    return "apply" if (leaf_now and leaf_now == rewind_leaf) else "spent"


def _block_to_dict(b):
    """One SDK content block → the transcript/event-model block dict (by type name, so no SDK import)."""
    n = type(b).__name__
    if n == "TextBlock":
        return {"type": "text", "text": getattr(b, "text", "")}
    if n == "ThinkingBlock":
        return {"type": "thinking", "thinking": getattr(b, "thinking", ""), "signature": getattr(b, "signature", None)}
    if n == "ToolUseBlock":
        return {"type": "tool_use", "id": getattr(b, "id", ""), "name": getattr(b, "name", ""), "input": getattr(b, "input", {})}
    if n == "ToolResultBlock":
        return {"type": "tool_result", "tool_use_id": getattr(b, "tool_use_id", ""),
                "content": getattr(b, "content", ""), "is_error": bool(getattr(b, "is_error", False))}
    return None


# Claude Code's slash-command wrappers, TWINS of bin/romp-event-model's (kept in lockstep — see its
# "slash-command transcript wrappers" block): the CLI streams its /model, /compact etc. feedback as
# UserMessages wrapped in these markers, and the LIVE atom must classify them exactly like the file
# adapter classifies the matching transcript records.
_COMMAND_NAME_RE = re.compile(r"^\s*<command-name>([^<]*)</command-name>")
# …and the tag ANYWHERE inside a wrapper record (the event model's COMMAND_NAME_ANY_RE twin): a SKILL /
# custom command writes <command-message> first, so the anchored form missed it and the live stream
# swallowed the invocation as wrapper noise (2026-08-13 — the same shape the file adapter fixed 2026-07-22).
_COMMAND_NAME_ANY_RE = re.compile(r"<command-name>([^<]*)</command-name>")
_COMMAND_ARGS_RE = re.compile(r"<command-args>([\s\S]*?)</command-args>")
_LOCAL_STDOUT_RE = re.compile(r"^\s*<local-command-stdout>([\s\S]*?)</local-command-stdout>")
_CMD_WRAP_RE = re.compile(r"^\s*<(?:command-(?:name|message|args|contents)|local-command-(?:stdout|caveat))>")
# The Skill tool's INSTRUCTIONS payload — twin of the event model's SKILL_CONTENT_RE/SKILL_MD_CAP (the
# user 2026-07-08). On the STREAM it arrives as a plain UserMessage (the isMeta flag exists only on the
# transcript record), so as a raw user atom it rendered as a fully-expanded note box for the whole live
# turn. The live atom gets the file adapter's classification instead: flagged, content-EMPTY, markdown
# in skillMd — the kernel folds it into the invoking Skill tool event, collapsed by default.
_SKILL_CONTENT_RE = re.compile(r"^\s*Base directory for this skill:")
_SKILL_MD_CAP = 16000
# An image fed to the model via a tool (a Read of a PNG, a screenshot) — Claude Code emits a synthetic
# UserMessage carrying JUST this human-readable placeholder alongside the image block. On the transcript
# the record is isMeta and the file adapter skips it; on the STREAM the flag is absent, so without this
# it fell through to a raw user atom and rendered as a bare "you typed this" bubble mid-conversation (the
# tool that fed the image already shows in the rail). Twin of the event model's IMG_ECHO_RE skip. The
# `:` after Image is what tells it apart from the composer's `[Image #N]` paste chips, which never stand
# alone as a whole turn anyway.
_IMG_ECHO_RE = re.compile(r"^\[Image:[^\]]*\]$")


def _note_skill_tool_ids(atom, ids):
    """Collect Skill tool_use block ids from a streamed ASSISTANT atom into `ids` — the live twin's
    anchor set for the newer skill-instructions shape (2026-07-10): the payload UserMessage carries
    parent_tool_use_id naming the invoking Skill tool_use (the transcript record's sourceToolUseID),
    and its text no longer starts with the "Base directory…" preamble. The tool_use always streams
    before its payload, so noting ids as atoms flow keeps the set ahead of every lookup."""
    if atom and atom.get("type") == "assistant":
        for b in (atom.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and \
                    b.get("name") == "Skill" and b.get("id"):
                ids.add(b["id"])


def msg_to_atom(msg, sid, fsid, t, skill_tool_ids=()):
    """An SDK stream message → an event-model atom (the SAME shape the file adapter emits from a
    transcript line), so the chat renders a LIVE atom identically and it dedups against the transcript
    by uuid (verified: the SDK message uuid == the transcript atom uuid). Returns None for messages
    with no renderable content (init/result/etc.).

    Slash-command wrappers get the FILE ADAPTER's classification, not a raw user atom (the user
    2026-07-02): client.set_model() makes the CLI stream a `<local-command-stdout>Set model to …`
    UserMessage; as a raw user atom it OPENED a turn no reply would ever close — the chat chip then read
    "working" forever while the timeline (disk-only; the CLI persists no transcript for a turn-less
    control request) showed nothing. Mirroring the adapter, the output becomes a synthetic ASSISTANT
    command atom with stop_reason end_turn — the turn closes, the chip stays consistent, and the chat
    still shows the confirmation line."""
    n = type(msg).__name__
    u = getattr(msg, "uuid", None)
    # SIDECHAIN traffic is not the parent's conversation (the user 2026-08-17, screenshot of a
    # subagent's full kickoff prompt painted as a huge expanded box below the collapsed tool group):
    # the CLI streams a Task/Agent subagent's OWN turns tagged with parent_tool_use_id — its kickoff
    # prompt as a UserMessage, its replies and tool calls as AssistantMessages. Untagged, the prompt
    # fell through to the generic user atom below and, being a live-tail atom whose uuid never
    # appears in the parent's transcript (the subagent writes its own file), the leak persisted
    # instead of being superseded. The subagent's designed surfaces are the Task/Agent head's
    # prompt+report folds and the background-task rows — the parent stream carries only the parent's
    # turns. The ONE tagged shape that IS the parent's own is a Skill payload (parent_tool_use_id
    # naming a Skill tool_use from this session's set — skills run inline, not as subagents); it
    # passes through to the skillMd classification in the UserMessage branch.
    ptid = getattr(msg, "parent_tool_use_id", None)
    if ptid and ptid not in (skill_tool_ids or ()):
        return None
    if n == "AssistantMessage":
        content = [d for b in (getattr(msg, "content", []) or []) if (d := _block_to_dict(b))]
        if not content:
            return None
        a = {"type": "assistant", "uuid": u, "session_id": sid, "t": t, "fsid": fsid, "parentUuid": None,
             "message": {"role": "assistant", "model": getattr(msg, "model", "") or "",
                         "content": content, "stop_reason": getattr(msg, "stop_reason", None)}}
        if getattr(msg, "error", None):
            # the CLI's failure settle (error-stamped, see _handle_message) — the SAME tag the file
            # adapter derives from isApiErrorMessage, so the live atom renders as the error card, is
            # never orphaned as a lost reply, and never re-asserts 'working' (_forward skips it).
            a["isApiError"] = True
        return a
    if n == "UserMessage":
        c = getattr(msg, "content", None)
        content = [d for b in c if (d := _block_to_dict(b))] if isinstance(c, list) else (
            [{"type": "text", "text": str(c)}] if c else [])
        if not content:
            return None
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
        mcmd = _COMMAND_NAME_RE.match(text) or (_COMMAND_NAME_ANY_RE.search(text)
                                                if _CMD_WRAP_RE.match(text) else None)
        if mcmd:                                     # the command INVOCATION → the command-flagged user atom
            name = mcmd.group(1).strip() or "/?"
            if not name.startswith("/"):
                name = "/" + name
            margs = _COMMAND_ARGS_RE.search(text)
            args = (margs.group(1).strip() if margs else "")
            disp = name + ((" " + args) if args else "")
            return {"type": "user", "uuid": u, "session_id": sid, "t": t, "fsid": fsid, "parentUuid": None,
                    "author": "human", "command": name,
                    "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
        mout = _LOCAL_STDOUT_RE.match(text)
        if mout:                                     # the command OUTPUT → a synthetic assistant atom that ENDS the turn
            return {"type": "assistant", "uuid": u, "session_id": sid, "t": t, "fsid": fsid, "parentUuid": None,
                    "command": True,
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": mout.group(1).strip()}],
                                "stop_reason": "end_turn"}}
        if _CMD_WRAP_RE.match(text):                 # the remaining wrappers (message/args/contents/caveat) — noise
            return None
        has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        if text and not has_tool_result and _IMG_ECHO_RE.match(text):
            return None                              # the synthetic image-read placeholder — harness noise, not a message
        if text and not has_tool_result and \
                (_SKILL_CONTENT_RE.match(text) or
                 getattr(msg, "parent_tool_use_id", None) in (skill_tool_ids or ())):
            # a Skill invocation's instructions → the flagged join atom. Two shapes, like the file
            # adapter: the legacy "Base directory…" preamble, and the newer parent_tool_use_id link to
            # the invoking Skill tool_use (the caller's per-session id set; the user 2026-07-10). The
            # tool_result guard keeps the Skill tool's own "Launching skill: X" result a normal atom.
            return {"type": "assistant", "uuid": u, "session_id": sid, "t": t, "fsid": fsid, "parentUuid": None,
                    "skillMd": text[:_SKILL_MD_CAP] + ("\n\n…(skill content truncated)"
                                                       if len(text) > _SKILL_MD_CAP else ""),
                    "message": {"role": "assistant", "content": [], "stop_reason": None}}
        atom = {"type": "user", "uuid": u, "session_id": sid, "t": t, "fsid": fsid, "parentUuid": None,
                "message": {"role": "user", "content": content}}
        # The record-level STRUCTURED result rides the stream too: the CLI emits the transcript
        # record's toolUseResult on the wire as `tool_use_result`, and the SDK parser maps it onto
        # UserMessage.tool_use_result (verified in claude-agent-sdk 0.2.132 / CLI 2.1.224). Carry it
        # onto the live atom under the SAME key and gates as the file adapter (a tool_result-bearing
        # atom, dict form only — a dismissed picker records a plain string no consumer reads), or the
        # live-tail window structurally bypasses the authoritative consumers: a JUST-answered
        # AskUserQuestion rendered via the lossy regex scrape until the disk record was parsed, and
        # Edit's diffRows lagged a parse behind the stream.
        tur = getattr(msg, "tool_use_result", None)
        if has_tool_result and isinstance(tur, dict) and (set(tur) & TUR_CONSUMED_KEYS):
            # same consumed-keys gate as the file adapter: a Read result's dict holds the whole
            # file — carrying shapes nothing reads only bloats the live tail
            atom["toolUseResult"] = tur
        return atom
    return None


# The toolUseResult keys a consumer actually reads — MIRRORS event_model.TUR_CONSUMED_KEYS (this
# module loads standalone, so it cannot import the event model; a drift pin in test_sdk_backend.py
# holds the two sets equal). Widen both together when a new consumer appears; never carry-all.
TUR_CONSUMED_KEYS = frozenset(("answers", "structuredPatch"))

TYPE_SOMETHING = "Type something"   # meta-option label the webview turns into the inline "add your own" field


def ask_question_to_live(question: dict, qi: int, total: int, selected=None, customs=None) -> dict:
    """Translate ONE AskUserQuestion question into the askLive `ask` shape the
    existing picker UI already renders (the same shape bin/romp-askparse emits),
    so SDK sessions reuse the pane-scraper's UI with zero changes.

    `question` is one element of the tool input's `questions[]`:
      {question, header, multiSelect, options:[{label, description, preview?}]}.
    `selected` is the set of 1-based option numbers currently toggled (multi).
    `customs` are free-text answers the user has typed so far (multi-select shows them as already-checked
    rows). A trailing "Type something" meta option is ALWAYS appended so the webview renders the inline
    "add your own answer" field — the TUI always offers it, but the SDK's raw tool input doesn't, so the
    SDK backend synthesizes it (the user 2026-06-27). The webview filters the meta row out of the pickable
    options (isMetaOption); numbering stays contiguous (real → customs → meta) so a toggle ordinal maps
    back here unambiguously.
    """
    selected = selected or set()
    customs = customs or []
    multi = bool(question.get("multiSelect"))
    opts = []
    for i, o in enumerate(question.get("options") or []):
        n = i + 1
        opt = {"n": n, "label": o.get("label", ""), "desc": o.get("description", "")}
        if multi:
            opt["checked"] = n in selected
        else:
            opt["selected"] = n in selected
        if o.get("preview"):
            opt["preview"] = o["preview"]
        opts.append(opt)
    for c in customs:                                 # already-typed free-text (multi) → checked rows
        opt = {"n": len(opts) + 1, "label": c}
        opt["checked" if multi else "selected"] = True
        opts.append(opt)
    meta = {"n": len(opts) + 1, "label": TYPE_SOMETHING, "desc": "add your own answer"}
    meta["checked" if multi else "selected"] = False
    opts.append(meta)
    ask = {
        "kind": "multi" if multi else "single",
        "header": question.get("header", ""),
        "question": question.get("question", ""),
        "options": opts,
        "multiSelect": multi,
        "cursor": 0,
        "cursorFound": True,
    }
    if total > 1:
        ask["progress"] = {"i": qi + 1, "n": total}   # "question 2 of 3"
    if question.get("preview"):
        ask["preview"] = question["preview"]
    return ask


def label_for_target(question: dict, target) -> str:
    """Map a 1-based option number (what the UI sends as `target`) to its label.
    A non-numeric / out-of-range target is returned verbatim (free-text answer)."""
    opts = question.get("options") or []
    try:
        n = int(target)
    except (TypeError, ValueError):
        return str(target)
    if 1 <= n <= len(opts):
        return opts[n - 1].get("label", str(target))
    return str(target)


def build_answers(questions: list, picks: dict) -> dict:
    """Assemble the AskUserQuestion `answers` mapping (question-text -> label or
    [labels]) from per-question picks keyed by question index."""
    answers = {}
    for i, q in enumerate(questions):
        if i not in picks:
            continue
        answers[q.get("question", "")] = picks[i]
    return answers


_PREVIEW_MAX_LINES = 200             # cap a diff/plan preview so a huge edit can't bloat the push


def _clip_lines(lines: list[str]) -> str:
    if len(lines) > _PREVIEW_MAX_LINES:
        kept = lines[:_PREVIEW_MAX_LINES]
        kept.append("… (%d more lines)" % (len(lines) - _PREVIEW_MAX_LINES))
        lines = kept
    return "\n".join(lines)


def _unified(path: str, old: str, new: str) -> list[str]:
    """A unified diff old→new, headed by the path. +/- prefixed so the webview can colorize it."""
    a, b = (old or "").splitlines(), (new or "").splitlines()
    body = list(difflib.unified_diff(a, b, lineterm="", n=2))
    # difflib emits ---/+++ file headers; drop them (we print our own path header) but keep @@ hunks.
    body = [ln for ln in body if not ln.startswith("--- ") and not ln.startswith("+++ ")]
    head = path or "(file)"
    return [head] + (body if body else ["(no textual change)"])


def tool_preview(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """A monospace preview for a tool-permission prompt — (kind, text) or None when there's nothing
    visual to show. kind is "diff" (Edit/Write/MultiEdit, colorizable +/- lines) or "plan"
    (ExitPlanMode). Lets the user SEE what they're approving instead of a bare tool name, the way the
    tmux pane scrape shows the TUI's diff/plan (the user 2026-06-27). Pure → unit-tested."""
    ti = tool_input or {}
    if tool_name == "ExitPlanMode":
        plan = str(ti.get("plan") or "").rstrip()
        return ("plan", _clip_lines(plan.split("\n"))) if plan else None
    if tool_name in ("Edit", "NotebookEdit"):
        path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
        return ("diff", _clip_lines(_unified(path, ti.get("old_string", ""), ti.get("new_string", ""))))
    if tool_name == "MultiEdit":
        path = ti.get("file_path") or ti.get("path") or ""
        lines: list[str] = []
        for e in (ti.get("edits") or []):
            lines += _unified(path, e.get("old_string", ""), e.get("new_string", ""))
            lines.append("")
        return ("diff", _clip_lines(lines)) if lines else None
    if tool_name == "Write":
        path = ti.get("file_path") or ti.get("path") or ""
        content = str(ti.get("content") or "")
        # a new/overwritten file → show its content as all-additions
        return ("diff", _clip_lines([path or "(file)"] + ["+" + ln for ln in content.split("\n")]))
    return None


def permission_to_live(tool_name: str, tool_input: dict, context=None) -> dict:
    """Render an ordinary tool-permission request as an askLive picker — Allow / Deny, plus an
    "Allow & don't ask again" option when the SDK offers permission-rule suggestions (the user
    2026-06-27). Uses the SDK's own prompt sentence (context.title) and subtitle (context.description)
    when present — the DESIGNED text — instead of reconstructing from the tool name, and attaches a
    diff/plan preview so the user can see what they're approving."""
    title = getattr(context, "title", None)
    desc = getattr(context, "description", None)
    summary = tool_input.get("command") or tool_input.get("file_path") \
        or tool_input.get("path") or tool_input.get("description") or ""
    q = title or (f"Allow {tool_name}?" + (f"\n{str(summary)[:300]}" if summary else ""))
    options = [{"n": 1, "label": "Allow", "desc": desc or f"Run {tool_name} once", "selected": False}]
    if getattr(context, "suggestions", None):
        options.append({"n": 2, "label": "Allow & don't ask again",
                        "desc": "Allow and remember this for the session", "selected": False})
    options.append({"n": len(options) + 1, "label": "Deny", "desc": "Refuse this call", "selected": False})
    ask = {
        "kind": "single",
        "header": "Permission",
        "question": q,
        "options": options,
        "multiSelect": False,
        "cursor": 0,
        "cursorFound": True,
        "permission": True,
    }
    pv = tool_preview(tool_name, tool_input)
    if pv:
        ask["previewKind"], ask["preview"] = pv[0], pv[1]
    return ask


# State-log helpers — match the kernel's `states/<sid>.jsonl` format exactly
# ({"t": epoch, "state": ...}) so the timeline + judges read both backends
# uniformly.
_STATES = ("working", "waiting", "idle", "permission", "compacting", "picker")


def append_state(state_dir: Path, sid: str, state: str, t: int | None = None) -> None:
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()) if t is None else int(t), "state": state}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def append_retry_recovered(state_dir: Path, sid: str, retries: int, t: int | None = None) -> None:
    """Record that a stalled api_retry turn RESUMED real output after `retries` backoff attempts — a durable
    marker in states/<sid>.jsonl the kernel turns into a persistent "Recovered after N retries" chat note.
    Same file/format as append_state, with its own key ("retriesRecovered") so the state/awaiting readers,
    which filter by their own keys, skip it."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()) if t is None else int(t), "retriesRecovered": int(retries)}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def append_retry_gave_up(state_dir: Path, sid: str, retries: int, kind: str = "",
                         t: int | None = None) -> None:
    """Record that an api_retry storm EXHAUSTED — the CLI gave up and settled the turn with its error
    message instead of output (the user 2026-07-25: a 10-attempt storm ended and the chat's durable note
    read "Recovered after 10 retries", the opposite of what happened). Twin of append_retry_recovered,
    its own key ("retriesGaveUp") so every keyed reader skips it; the kernel turns it into a persistent
    "gave up after N retries" chat note right where the storm died. `kind` is the CLI's own error stamp
    (AssistantMessage.error: "server_error", "rate_limit", …), kept for the note's tooltip."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()) if t is None else int(t), "retriesGaveUp": int(retries),
           "errorKind": str(kind or "")}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


CLI_MODEL_BLOCKS_FILE = "cli-model-blocks.json"
# The CLI's own refusal when a model id is valid on the API but newer than the installed binary
# (verified live 2026-09-01 on 2.1.221 vs claude-fable-5-1): the ONLY shape this recognises, so a
# different error can never be misfiled as a version block.
_CLI_MIN_VERSION_RE = re.compile(
    r"Claude Code (\S+) does not support this model; version (\S+) or newer is required")


def _assistant_text(msg) -> str:
    """The text blocks of an AssistantMessage joined — the error settle's own words."""
    out = []
    for b in (getattr(msg, "content", None) or []):
        t = getattr(b, "text", None)
        if isinstance(t, str):
            out.append(t)
        elif isinstance(b, dict) and isinstance(b.get("text"), str):
            out.append(b["text"])
    return "".join(out)


def _rewrite_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, p)


def note_cli_model_block(state_dir, model_id, error_text) -> bool:
    """Record that the INSTALLED CLI refused `model_id` by minimum version (T222): the live model
    catalog can list ids newer than the binary — the API is the catalog's source, the CLI gates by
    version — so the kernel's /models stamps the refusal on that version row the moment it is known,
    not only after the next pick fails. Durable ({model: {needs, cli, t}} in STATE/cli-model-blocks.json)
    and event-cleared by the first real reply on that model (clear_cli_model_block). Returns whether the
    text was the version refusal; anything else records nothing."""
    m = _CLI_MIN_VERSION_RE.search(error_text or "")
    mid = str(model_id or "")
    if not m or "claude" not in mid.lower():
        return False
    p = Path(state_dir) / CLI_MODEL_BLOCKS_FILE
    try:
        d = json.loads(p.read_text())
        d = d if isinstance(d, dict) else {}
    except Exception:
        d = {}
    d[mid] = {"needs": m.group(2), "cli": m.group(1), "t": int(time.time())}
    _rewrite_json(p, d)
    return True


def clear_cli_model_block(state_dir, model_id) -> bool:
    """A real reply on `model_id` proves the installed CLI serves it now — drop its block (the CLI was
    updated; event-based, never a timer). Returns whether a block was removed."""
    mid = str(model_id or "")
    p = Path(state_dir) / CLI_MODEL_BLOCKS_FILE
    try:
        d = json.loads(p.read_text())
    except Exception:
        return False
    if not isinstance(d, dict) or mid not in d:
        return False
    del d[mid]
    _rewrite_json(p, d)
    return True


ORPHAN_REPLY_CAP = 8000   # text cap per orphan marker — a lost reply is worth keeping, but bound the file


def append_orphan_reply(state_dir: Path, sid: str, uuid: str, text: str, t: int | None = None) -> None:
    """Record an assistant reply that STREAMED LIVE but the transcript never kept (the user 2026-07-21): when a
    turn hits an API error, the CLI discards the partial reply it was streaming and (on retry) writes a fresh
    record with a NEW uuid — so the reply the user watched appear is on disk NOWHERE, and retire_live_work drops
    the live atom at settle, leaving only the "Recovered after N retries" note in its place. This durable marker
    (its own "orphanReply" key, skipped by the state/awaiting/recovery readers) lets build_session interleave the
    lost text back at its timestamp — DEDUP'd against the disk so a retry that DID re-reply never doubles."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()) if t is None else int(t),
           "orphanReply": {"uuid": str(uuid or ""), "text": (text or "")[:ORPHAN_REPLY_CAP]}}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _atom_text(a: dict) -> str:
    """The joined text blocks of a live assistant atom (thinking/tool_use skipped)."""
    msg = a.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def append_effort_applied(state_dir: Path, sid: str, effort: str, t: int | None = None) -> None:
    """Record that an /effort change TOOK EFFECT — written the instant the reconnect that carries --effort
    lands (idle → at once; busy → at turn end), so it pins the moment the new effort is real, not when it was
    asked for. A durable marker in states/<sid>.jsonl the kernel turns into a persistent "effort set to X"
    chat note (the user 2026-07-16: the reconnect leaves no transcript record, so the synthesized /effort chip
    self-destructs on the next message and history keeps no trace of when effort changed). Its own key
    ("effortApplied") so the state/awaiting/recovery readers, which filter by their own keys, skip it."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()) if t is None else int(t), "effortApplied": str(effort)}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def append_cmd_gesture(state_dir: Path, sid: str, text: str, t: int | None = None) -> None:
    """Record a command GESTURE — a /model-/effort-/auth-style pick — at the moment it was ASKED FOR. The
    synthesized live chip that acknowledges the pick is in-memory only and prune_live's stale_cmd retires it
    on the next human turn, so the user's own gesture vanished from their side of the history (the user
    2026-08-14: the right side should keep what you did; the applied note keeps that it happened). The kernel
    interleaves a durable `cmdGesture` chat event from this marker, deduped against the still-live chip by
    (t, text). Its own key ("cmdGesture") so the state/awaiting/recovery readers, which filter by their own
    keys, skip it. `text` is the full display form, e.g. "/effort high"."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()) if t is None else int(t), "cmdGesture": str(text)}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def append_machine_cut(state_dir: Path, sid: str, cause: str, t: float | None = None) -> None:
    """Record that ROMP cut this session's turn and is continuing it — written at the instant a resume
    notice is QUEUED (boot reconcile → "restart"; _heal_cut_session → "crash"), which is the event the
    kernel's transcript scan can only INFER later, once that notice reaches disk.

    Why this marker exists (the user 2026-08-12, who sees the false block "again and again"): a machine
    cut mints the same "[Request interrupted by user]" record as a real Esc, and _machine_cut_cause tells
    them apart by scanning FORWARD for the resume notice. But the stop record is on disk the moment the
    turn is cut, while the notice only lands once the resumed CLI writes it — seconds later. Every
    _interrupt_block_tick inside that window reads a bare stop with nothing after it, concludes the user
    stopped the session, and blocks the focus card on them with INTERRUPT_BLOCK_WHY. Measured on the live
    machine: 30 such false blocks in 30 hours, each one an interrupt/block row the user never caused,
    followed by an unblock once the notice landed — a card round-trip on an event that carried no new
    information at all (CLAUDE.md: cards move on new information, never on inference flaps).

    Time-ordering is what makes the marker exact, so no grace period is needed: the cut always precedes
    the resume we are queueing here, so an interrupt record at or before `t` belongs to THIS cut, and a
    genuine stop the user makes later is always past it. That also makes the marker self-limiting — a
    stale one can never relabel a newer stop — so nothing has to expire or be swept. `t` stays a FLOAT
    (the other appenders truncate to int, which would move the bound EARLIER and could drop a stop record
    written in the same second). Its own "machineCut" key, so the state/awaiting/recovery readers, which
    filter by their own keys, skip it."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": time.time() if t is None else float(t), "machineCut": str(cause)}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def append_resume_fork(state_dir: Path, sid: str, from_fsid: str, to_fsid: str, t: float | None = None) -> None:
    """Record that a RESUME landed on a fresh-headed transcript fork: the CLI's init reported a NEW
    fsid for a conversation we asked it to continue (--resume), with no /clear in flight and no
    born-as-a-fork copy pending. On disk that fork is byte-indistinguishable from a /clear
    (parentUuid-null head, no cross-file back-link), so without this row the parser dropped the entire
    pre-cut conversation and the episode check settled its open cards — a machine-cut watch turn's
    finding vanished to two mid-turn restarts (the user 2026-08-14). Written at the init flip, the one
    event that knows the old->new binding (the registry can't serve: lastSid is overwritten per fork).
    Consumers: em.resume_fork_links/_stitch_resume_forks (the parse) and jd.resume_lineage (the
    kernel's episode-boundary stand-down). Its own "resumeFork" key, like machineCut, so the
    state/awaiting readers skip it."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": time.time() if t is None else float(t),
           "resumeFork": {"from": str(from_fsid), "to": str(to_fsid)}}
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


def append_awaiting(state_dir: Path, sid: str, awaiting: bool, why: str = "") -> None:
    """Append an "awaiting" OVERLAY record to states/<sid>.jsonl (interleaved with the state
    records; the kernel reader scans for the latest line carrying an "awaiting" key). "Awaiting" =
    the session is idle but waiting on dispatched/background work — a flavour of working, exempt from
    auto-nudge (bugz's event-model awaiting, contract confirmed 2026-06-22). awaiting:true carries a
    "why"; awaiting:false clears it."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"t": int(time.time()), "awaiting": bool(awaiting)}
    if awaiting and why:
        rec["why"] = why
    with open(p, "a") as f:
        f.write(json.dumps(rec) + "\n")


# How far apart two readings of ONE rate-limit window's reset can sit and still be that window. The two
# sources romp reads date the same window differently — RateLimitEvents carry the API response headers'
# reset, the get_usage snapshot carries the /usage endpoint's — and on 2026-08-02 those were 10 minutes
# apart for the 5h window. The windows themselves are 5 hours and 7 days wide, so a genuine roll moves the
# stamp by hours at minimum: an hour of slack cannot swallow one, and it absorbs any skew between sources.
WINDOW_SLACK = 3600


def _same_window(a, b) -> bool:
    """Whether two reset stamps name the SAME rate-limit window. Equality is the wrong test: the two
    sources quantize the boundary differently, and reading their disagreement as a window ROLL is what
    reset the bars to 0 seconds after an exact reading landed (see _record_rate_limit)."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(a - b) <= WINDOW_SLACK


def last_state(state_dir: Path, sid: str) -> dict:
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    try:
        line = ""
        with open(p) as f:
            for line in f:
                pass
        return json.loads(line) if line.strip() else {}
    except (OSError, ValueError):
        return {}


def last_state_value(state_dir: Path, sid: str) -> str:
    """The latest STATE record's value in states/<sid>.jsonl, skipping the interleaved awaiting
    OVERLAY records ('' if none). last_state() returns the literal last LINE — which can be an
    overlay (the boot heal itself appends awaiting:false) — so anything keying on the state tail
    (the boot reconcile's cut-turn detector) must read through the overlays, not the last line."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    val = ""
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and "state" in rec:
                    val = str(rec["state"])
    except OSError:
        pass
    return val


def last_awaiting(state_dir: Path, sid: str) -> bool | None:
    """The latest awaiting-OVERLAY value in states/<sid>.jsonl — the most recent line carrying an
    "awaiting" key (state records interleave with overlays, so the very last line isn't necessarily one).
    None if the session has no awaiting overlay. Used to heal a stale awaiting:true that lost its clearing
    writer (the Stop hook) to a kernel restart / thread death."""
    p = Path(state_dir) / "states" / (sid + ".jsonl")
    val = None
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and "awaiting" in rec:
                    val = bool(rec["awaiting"])
    except OSError:
        return None
    return val


def write_name(state_dir: Path, sid: str, name: str, cwd: str, bg: str = "", fg: str = "") -> None:
    """Write the shared identity/discovery file `names/<sid>` in the kernel's
    tab-delimited format (name\\tcwd\\tbg\\tfg), so discover() finds the
    transcript and the UI gets the identity colour."""
    p = Path(state_dir) / "names" / sid
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\t".join([name, cwd, bg, fg]) + "\n")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Session registry (which sids are SDK-backed; survives kernel restart).
# ---------------------------------------------------------------------------

def _reg_path(state_dir: Path, sid: str) -> Path:
    return Path(state_dir) / "sdk" / (sid + ".json")


def acct_digest() -> str:
    """The signed-in Claude account as an opaque 12-hex digest, "" when logged out — duplicated tiny
    from the kernel's _claude_account (same file, same field, same hash) so usage-window WRITES can
    stamp whose reading they hold without a kernel import. The stamp is what lets the kernel drop bars
    the INSTANT that login goes away (the user 2026-08-04), instead of waiting for a next reading that
    never comes after a logout."""
    try:
        acct = ((json.loads(open(os.path.expanduser("~/.claude.json"), encoding="utf-8").read()) or {})
                .get("oauthAccount") or {}).get("accountUuid")
        return hashlib.sha256(str(acct).encode("utf-8")).hexdigest()[:12] if acct else ""
    except Exception:
        return ""


def read_reg(state_dir: Path, sid: str) -> dict | None:
    try:
        return json.loads(_reg_path(state_dir, sid).read_text())
    except (OSError, ValueError):
        return None


def read_reg_for_rmw(state_dir: Path, sid: str) -> "dict | None":
    """read_reg for a READ-MODIFY-WRITE on one reg FIELD: {} when the reg genuinely does not
    exist (a fresh session — an empty base is correct), None when the reg EXISTS but would not
    read (a transient failure). A None caller MUST skip its write: rebuilding a list field from
    an empty base and persisting it silently wipes the field — bgLedger/bgLedgerEnded, pushNotes,
    taskWrites, sessionCrons all carried this shape (the 2026-09-01 field-level gutting class,
    the field-sized sibling of _update_reg's whole-reg guard). Losing one update is the far
    smaller harm: the next lands on the healed read."""
    reg = read_reg(state_dir, sid)
    if reg is not None:
        return reg
    return None if _reg_path(state_dir, sid).exists() else {}


def write_reg(state_dir: Path, sid: str, reg: dict) -> None:
    p = _reg_path(state_dir, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Writer-unique temp name: during a kernel restart the OUTGOING kernel's session machinery and
    # the incoming kernel's boot reconcile can write the same sid's registry concurrently, and a
    # SHARED "<sid>.tmp" let one writer's os.replace steal the other's temp file mid-write
    # (FileNotFoundError, seen live 2026-07-06). os.replace stays atomic; last writer wins.
    tmp = p.with_name("%s.%d.%s.tmp" % (p.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(json.dumps(reg))
        os.replace(tmp, p)
    finally:
        try:                                        # never leave a stray temp on a failed write
            os.unlink(tmp)
        except OSError:
            pass


# The continuation nudge the boot reconcile prepends to a CUT session's queue (a turn the previous
# kernel's death interrupted): visible in the chat as a gray romp card, so the recovery is never
# silent, and instructing the model — whose transcript tail is an unanswered user message — to pick
# the work back up. romp-injected → author 'romp' (gray bubble), skipped by the planner as a goal.
# The middle sentence DISARMS the CLI's stop record (the user 2026-08-08): a machine cut writes the
# same "[Request interrupted by user]" record as a real Esc, and a resumed model that wasn't told
# otherwise read it as the user's intent — across the fleet's transcripts roughly one restart-cut
# session in six answered this notice by standing down and awaiting direction instead of resuming. Naming the record verbatim and disowning it is what
# lets "pick the work back up" win. Lockstep: kernel INTR_RESTART_SIG/INTR_CRASH_SIG match on these
# texts (test_kernel_interrupt_machine_cut), so the leading sentences must keep their phrases.
BOOT_RESUME_NUDGE = (
    "<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted and cut this session's "
    "in-flight turn; the session has been resumed with its history intact. If the conversation tail "
    "shows '[Request interrupted by user]', that record came from this cut, not from the user: nobody "
    "asked you to stop. Re-read the tail of the conversation and pick the work back up where it "
    "stopped, without asking whether to continue. Any messages queued before the restart follow "
    "this one.")

# T214: the restart also killed a QUESTION the session had up — the ask future lived only in the
# old process, so the user's answer (often flushed by the reconnecting page) had nowhere to land,
# and the resume nudge alone told the session to continue WITHOUT asking. Same sanctioned [romp]
# mechanics family as the restart notice above; queued right after it, before the restored backlog.
ASK_DIED_NOTICE = (
    "<!-- romp-injected --><!-- romp-system -->[romp] The restart also killed a question this "
    "session had up awaiting the user's answer — it was never delivered, and any answer they sent "
    "could not land. Ask the question again so they can answer it.")

# Staggered boot-resume (the user 2026-07-20): spawning every reconciled session's CLI at once
# detonated a fleet-wide CPU storm — each resumed claude burns ~a full core catching up on its
# transcript, so a 13-session restart pegged the machine (load ~20) and starved the kernel's own
# boot; the restart itself looked hung. At most this many CLIs spawn concurrently; a slot frees on
# the EVENT that the CLI is past its catch-up burst (its first init message, or its thread dying).
BOOT_RESUME_CONCURRENCY = max(1, int(os.environ.get("ROMP_BOOT_RESUME_CONCURRENCY", "3")))
# Backstop ONLY (never the mechanism): a CLI that wedges before init would otherwise hold its slot
# forever and trap the whole sweep — after this long the sweep proceeds anyway, loudly.
BOOT_RESUME_SLOT_S = float(os.environ.get("ROMP_BOOT_RESUME_SLOT_S", "180"))

# The rename ping (the user 2026-08-24): a renamed session hears its OWN new name — one line ahead
# of whatever next enters it (send() below), never a wake of its own. Same [romp] mechanics-notice
# family as the restart notices above (the housekeeping note in the session prompt gives the prefix
# meaning), but MARKER-FREE: this line joins an EXISTING message, and the romp-injected marker
# would re-author the host message's echo and transcript atom.
RENAME_NUDGE = "[romp] This session was renamed: it is now '%s'. A note for your own records — carry on."
# the dressed ping's detectable head — the drain's feed-hold keys on it (see _deliver_rename_ping)
RENAME_PING_HEAD = "<!-- romp-injected --><!-- romp-system -->[romp] This session was renamed"

# Same recovery, different death: the session's OWN claude process died mid-turn (killed or crashed)
# while the kernel stayed up, so the kernel itself resumes it (_heal_cut_session) instead of waiting
# for the next boot's reconcile.
CRASH_RESUME_NUDGE = (
    "<!-- romp-injected --><!-- romp-system -->[romp] This session's claude process died mid-turn "
    "(killed or crashed); the session has been resumed with its history intact. If the conversation "
    "tail shows '[Request interrupted by user]', that record came from this cut, not from the user: "
    "nobody asked you to stop. Re-read the tail of the conversation and pick the work back up where "
    "it stopped, without asking whether to continue.")


# A CLI that cannot even START says so on the way out — and the ONE cause that reliably does this is the
# account being out of usage: `claude` refuses the handshake and exits with the limit in its own words
# ("You've hit your session limit · resets 1:10pm (America/Los_Angeles)"). romp used to swallow that
# entirely (the user 2026-07-28, on a fresh install): the message they typed sat in the persisted queue,
# the session settled 'waiting', and NOTHING said why — the send simply never flipped to working. The
# limit was observable the whole time (romp's own judge calls were getting the same envelope), just never
# read on this path. usage.json is no help here either: it is written from a RateLimitEvent the CLI streams
# once CONNECTED, so a limit that blocks the connect blocks its own reporting — _limit_hold stayed None and
# the queued bubble had nothing to say. This is that missing edge.
_LAUNCH_LIMIT_RE = re.compile(
    r"hit your (?:session|usage|weekly|5-hour) limit"      # the CLI's own phrasing
    r"|usage limit reached"
    r"|out of (?:usage|credits)"
    r"|rate.?limit(?:ed)? .{0,40}(?:account|session|usage)", re.I)


# The ONE dependency the whole SDK backend rests on. When it is absent every SDK session is a session
# that can accept a message and never, ever run it — which is exactly what a fresh install looked like on
# 2026-07-28: romp-sdk-setup had bailed (a python with no ensurepip), the kernel logged one stderr line
# and BUILT THE BACKEND ANYWAY, and from the user's side sends vanished, no session flipped to working,
# and the model/effort/usage readouts stayed blank (all three publish only AFTER a connect that could
# never happen). tmux sessions worked the whole time, which made it read as an Anthropic outage. The
# remedy is one command, so the error names it rather than describing the symptom.
SDK_MISSING_TEXT = (
    "romp's Agent SDK backend isn't installed, so this session can't run — its messages are being kept, "
    "not sent. Install it with bin/romp-sdk-setup (it prints the OS package to add if one is missing), "
    "then restart romp. tmux-backed sessions are unaffected.")


def sdk_importable() -> bool:
    """Is claude_agent_sdk actually importable RIGHT NOW? Checked at backend construction so the failure
    is reported ONCE, up front, for every session — rather than one session at a time as each one's
    thread dies at the lazy import inside _amain."""
    try:
        import importlib.util
        return importlib.util.find_spec("claude_agent_sdk") is not None
    except Exception:
        return False


# What the SDK puts on ProcessError.stderr when NOBODY registered an options.stderr callback: it does
# not pipe the child's stderr at all, and substitutes this literal (subprocess_cli.py). Surfacing it is
# worse than useless — it tells the user to go read an output romp never captured, and it outranked the
# exception text that would at least have named the error class. Every SDK session dying at launch showed
# exactly this and nothing else on 2026-07-29 (a moved repo, so every --resume hit "No conversation
# found"; the CLI's own stderr said so, and it went nowhere). romp now registers the callback (see
# SdkSession._on_cli_stderr), so this is only reachable if that ever regresses — treat it as no text.
SDK_STDERR_PLACEHOLDER = "Check stderr output for details"

# How many trailing stderr lines to keep per session. A launch failure's cause is always in the last
# handful (the CLI prints one line and exits); the cap is what keeps a chatty or looping CLI from
# growing the buffer without bound.
STDERR_TAIL_LINES = 40


def launch_failure_text(exc: BaseException, tail: str = "") -> str:
    """The most SPECIFIC human text a failed CLI launch carries. The SDK's ProcessError keeps the CLI's
    own stderr on the exception (the class that actually names the cause); `tail` is what romp captured
    off the child's stderr itself, used when the exception carries only the SDK's placeholder; everything
    else falls back to the exception's own text. Truncated, since a stderr dump can run long and this
    lands in a chat card."""
    parts = []
    for attr in ("stderr", "stdout"):
        v = getattr(exc, attr, None)
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        if isinstance(v, str) and v.strip() and SDK_STDERR_PLACEHOLDER not in v:
            parts.append(v.strip())
    if tail.strip():
        parts.append(tail.strip())
    parts.append(("%s: %s" % (type(exc).__name__, exc)).strip())
    # prefer whichever part NAMES a limit — that's the line worth showing — else the first non-empty
    named = next((p for p in parts if _LAUNCH_LIMIT_RE.search(p)), None)
    text = (named or parts[0]).strip()
    if len(text) > 600:
        text = text[:600].rstrip() + "…"
    return text


def is_launch_limit(text: str) -> bool:
    """True when a launch failure is the ACCOUNT being out of usage rather than a broken install. Drives
    the kernel's _limit_hold (the queue parks and says what it is waiting for) instead of the plain
    'this session could not start' card."""
    return bool(text and _LAUNCH_LIMIT_RE.search(text))


def task_death_notice(tasks: list) -> str:
    """The visible romp notice for BACKGROUND TASKS that died with their claude process. Bg tasks are
    the CLI's children, so a kernel restart or CLI crash silently kills a session's timers/watchers —
    and a session idle-waiting on one would wait FOREVER for a completion notification that can never
    arrive (nimbus's dead campaign watcher, the user 2026-07-11). The notice names what was lost (the
    task descriptions from the lifecycle stream) so the session can relaunch exactly what still
    matters. Enqueued by _on_session_gone (CLI died, kernel alive) or the boot reconcile (kernel died;
    read from the reg's bgTasks mirror)."""
    n = len(tasks)
    descs = "; ".join(d for d in ((t.get("desc") or "").strip() for t in tasks[:4]) if d)
    return ("<!-- romp-injected --><!-- romp-system -->[romp] %d background task%s this session had "
            "running died with its claude process (a restart or crash)%s. Their completion "
            "notifications will never arrive — relaunch any that are still needed, or carry on if "
            "they aren't." % (n, "" if n == 1 else "s", (": " + descs) if descs else ""))

# The marker only SDK-driven claude CLIs carry (the kernel drives them over stdin); a tmux session's
# interactive `claude --resume` never has it, so the orphan reap can never touch a tmux CLI.
_SDK_CLI_MARK = "--input-format stream-json"


def _cli_carries_sid(cmd: str, sids) -> bool:
    """True when this CLI's argv names one of OUR conversation ids. The Agent SDK has spelled the
    flag BOTH ways across versions — `--resume <sid>` (space) and `--resume=<sid>` (equals) — and a
    fresh-spawned, never-resumed CLI carries its id only as `--session-id[= ]<sid>`. The space-only
    `--resume` match went blind when the SDK moved to the equals form: every boot reconcile logged
    'reaped 0 orphaned CLI(s)' while a real orphan kept working the repo for over an hour
    (2026-07-25, the twin incident — the restart's drain timed out on a busy session, the kernel
    exited, and the census that should have caught the leftover matched nothing), and the interrupt
    escalation could not find its own child to signal. Match every spelling."""
    for s in sids:
        if not s:
            continue
        for flag in ("--resume", "--session-id"):
            if (flag + " " + s) in cmd or (flag + "=" + s) in cmd:
                return True
    return False


def find_orphan_clis(ps_lines: list[str], lastsids: list[str]) -> list[int]:
    """PIDs of ORPHANED SDK-driven `claude` CLIs holding one of OUR sessions (--resume/--session-id
    in either flag spelling, + the stream-json mark — see _cli_carries_sid). Orphaned = re-parented
    to launchd (ppid 1): a live SDK CLI is always a child of the kernel that spawned it, so only a
    dead kernel's leftover — a zombie writer that would fight the resume for the transcript —
    reaches ppid 1. The parent check is load-bearing: matching on the command line alone let a
    duplicate backend's reconcile reap freshly-resumed LIVE sessions mid-turn (2026-07-06). Pure
    (takes `ps -axo pid=,ppid=,command=` lines) so tests need no live processes."""
    out = []
    for ln in ps_lines:
        parts = ln.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, ppid, cmd = int(parts[0]), int(parts[1]), parts[2]
        if ppid != 1:
            continue
        if _SDK_CLI_MARK not in cmd:
            continue
        if _cli_carries_sid(cmd, lastsids):
            out.append(pid)
    return out


def find_session_cli(ps_lines: list[str], sids: list[str], parent_pid: int) -> int | None:
    """The LIVE CLI pid holding one of `sids` as a child of `parent_pid` (this kernel), or None.
    The interrupt escalation's (and the drain reap's) target: same signature match as
    find_orphan_clis (_cli_carries_sid + the stream-json mark) but the OPPOSITE parent check — it
    may only signal our own child, never a tmux CLI (no mark), never another kernel's, never an
    orphan (ppid 1, the reaper's territory). Pure (takes `ps -axo pid=,ppid=,command=` lines) so
    tests need no live processes."""
    for ln in ps_lines:
        parts = ln.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, ppid, cmd = int(parts[0]), int(parts[1]), parts[2]
        if ppid != parent_pid or _SDK_CLI_MARK not in cmd:
            continue
        if _cli_carries_sid(cmd, sids):
            return pid
    return None


def recurring_crons(reg):
    """The RECURRING entries of a reg's armed-timer set (sessionCrons) — THE shared predicate for
    "this session needs a live CLI process", read by BOTH conserve_idle (refuses to close on it) and
    ensure_scheduled (revives on it), so the two sides can never drift (the T148/769 seam, refined
    2026-08-28 with the 769 author: recurring-only). One-shots are deliberately excluded on both
    sides: a THREADLESS session's one-shot belongs to a dead process generation — a fresh CLI cannot
    re-hydrate a lost wakeup (verified live 2026-08-28), so pinning or reviving a process buys the
    wakeup nothing; deliver_lost_wakeups owns dead-generation one-shots outright and delivers the
    prompt itself at due, which revives a dormant session through the normal send path. Recurring
    entries pin the process for TIMELINESS: their live CLI is the only ON-TIME scheduler. (A resumed
    CLI does catch-up-fire a recurring slot that passed while its previous process was dead —
    verified live 2026-09-01, T211 — but that is a replay hazard as much as a recovery: every kernel
    restart re-fired already-delivered slots until _prompt_submit_hook's delivered-slot gate.)
    Reads the entry's `recurring` field, never the cron string's shape — toolhook-minted one-shots
    carry an empty cron string."""
    return [c for c in (reg.get("sessionCrons") or []) if isinstance(c, dict) and c.get("recurring")]


def wakeup_due_epoch(cron: str, armed_t: float) -> "float | None":
    """The due moment of a ONE-SHOT wakeup cron ("<min> <hour> * * *", the shape ScheduleWakeup mints
    from delaySeconds, local time) — the first occurrence of H:M at/after the arm record. Any other
    shape returns None: romp only ever self-fires timers it can reason about exactly; everything else
    stays the CLI scheduler's job. Pure for tests."""
    try:
        m, h, dom, mon, dow = str(cron).split()
        if (dom, mon, dow) != ("*", "*", "*"):
            return None
        m, h = int(m), int(h)
        if not (0 <= m <= 59 and 0 <= h <= 23):
            return None
    except (ValueError, AttributeError):
        return None
    from datetime import datetime, timedelta   # local, like the reg-timestamp reader's (this module
    #                                            otherwise never needs datetime at import time)
    base = datetime.fromtimestamp(float(armed_t))
    due = base.replace(hour=h, minute=m, second=0, microsecond=0)
    if due.timestamp() < armed_t - 60:            # H:M already passed at arm time → the wrap is tomorrow
        due += timedelta(days=1)
    return due.timestamp()


def _cron_field(spec: str, lo: int, hi: int) -> "set[int] | None":
    """One 5-field cron field as the set of ints it names (vixie shapes: *, steps, ranges, lists),
    or None for anything outside that grammar — the caller stands down rather than guessing."""
    out: set[int] = set()
    for part in str(spec).split(","):
        part, step = part.strip(), 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                return None
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            aa, _, bb = part.partition("-")
            if not (aa.isdigit() and bb.isdigit()):
                return None
            a, b = int(aa), int(bb)
        elif part.isdigit():
            a = b = int(part)
        else:
            return None
        if a < lo or b > hi or a > b:
            return None
        out.update(range(a, b + 1, step))
    return out or None


def cron_prev_due(cron: str, now: float) -> "float | None":
    """The most recent moment at/before `now` a 5-field RECURRING cron names — the SLOT identity the
    replay gate keys on (_prompt_submit_hook): a delivered fire is recorded against this epoch, and a
    later fire computing the SAME slot is a replay by definition, however long its process was down
    in between — exact event identity, never an age window. Vixie semantics: lists/ranges/steps,
    day-of-month OR day-of-week when both are restricted, dow 0/7 = Sunday. Local time, matching the
    CLI's own scheduler. Anything unparseable returns None and the gate stands down — romp only
    reasons about schedules it understands exactly (wakeup_due_epoch's rule). Lookback bounded at
    ~400 days so a never-matching string (a Feb 30) terminates. Pure for tests."""
    try:
        m, h, dom, mon, dow = str(cron).split()
    except (ValueError, AttributeError):
        return None
    mins, hrs = _cron_field(m, 0, 59), _cron_field(h, 0, 23)
    doms, mons, dows = _cron_field(dom, 1, 31), _cron_field(mon, 1, 12), _cron_field(dow, 0, 7)
    if None in (mins, hrs, doms, mons, dows):
        return None
    dows = {d % 7 for d in dows}                       # 7 is Sunday too
    from datetime import date, timedelta               # local, like wakeup_due_epoch
    lt = time.localtime(now)
    day0 = date(lt.tm_year, lt.tm_mon, lt.tm_mday)
    for back in range(400):
        day = day0 - timedelta(days=back)
        if day.month not in mons:
            continue
        dom_ok, dow_ok = day.day in doms, ((day.weekday() + 1) % 7) in dows
        if not ((dom_ok or dow_ok) if (dom != "*" and dow != "*")
                else (dow_ok if dom == "*" and dow != "*" else dom_ok)):
            continue
        if back == 0:
            cand = [(H, M) for H in hrs for M in mins if (H, M) <= (lt.tm_hour, lt.tm_min)]
            if not cand:
                continue                                # today matches but only later than now
            H, M = max(cand)
        else:
            H, M = max(hrs), max(mins)
        return time.mktime((day.year, day.month, day.day, H, M, 0, -1, -1, -1))
    return None


def cron_slot_key(cron: str, prompt: str) -> str:
    """The SCHEDULE IDENTITY half of the replay gate's key. Deliberately NOT the CLI's cron id: a
    resume re-hydration or a delete/re-create mints fresh ids for the same nightly job (observed
    live — the same schedule wore two ids across two process generations), and an id-keyed record
    would greet every re-mint as a brand-new schedule and wave its replay through. The (cron string,
    prompt) pair is what makes two fires "the same scheduled work", so it IS the identity."""
    return hashlib.sha1(("%s\n%s" % (cron, prompt)).encode("utf-8", "replace")).hexdigest()[:16]


def interrupt_action(level: int, channel_up: bool) -> tuple[str, int]:
    """The interrupt escalation ladder (terminal parity, the user 2026-07-10): what a stop press does,
    given how far the current episode has climbed. In a terminal ctrl+c is a SIGNAL the process cannot
    silently drop; the SDK interrupt is a control-channel REQUEST a wedged CLI simply ignores — so a
    press never repeats a rung that already failed to produce the settle event. Level 0 with a live
    channel → the polite control request; a press with no channel, or after an unsettled request →
    SIGINT the CLI; beyond that → SIGKILL (its stream death runs the existing crash-heal + resume).
    The episode resets to 0 when a turn settles (ResultMessage) or a fresh turn starts. Pure for tests;
    returns (action, new_level)."""
    if channel_up and level == 0:
        return ("control", 1)
    if level <= 1:
        return ("sigint", 2)
    return ("sigkill", 3)


# list_regs' per-file parse cache (the 2026-08-10 CPU fix). The registry holds a reg file for EVERY
# session ever created — a couple hundred, all but a handful dormant — and list_regs sits on the
# kernel's hottest path: every liveness snapshot (Sessions.live) sweeps it, several times a second.
# Re-opening + json-parsing a few hundred unchanged files per sweep was a measurable slice of the
# pusher thread's sustained CPU burn. The file IS the truth, so the cache keys on exactly what
# changes when the file does — (mtime_ns, size, inode); write_reg's os.replace mints a new inode, so
# even a same-size same-instant rewrite misses. Entries are handed out as SHALLOW COPIES: the common
# mutation (_update_reg's reg.update) lands on the copy, never the cache; read_reg stays uncached on
# purpose — it feeds read-modify-writes and must always be the fresh file.
_REG_CACHE = {}   # path -> ((mtime_ns, size, ino), parsed reg)


_REG_SERVE_WARNED = set()      # paths currently being served from cache — one loud line per incident


def list_regs(state_dir: Path) -> list[dict]:
    """COMPLETENESS is this scan's contract (the user 2026-08-31, closing the listing-blink class):
    absence from the returned rows reads as SESSION DEATH downstream — the postal bus refuses sends
    on it — so a reg this scan has ever seen may drop out only by being GENUINELY GONE (unlinked).
    A transient stat/read/parse failure serves the reg's LAST GOOD row from the cache instead,
    loudly; silence was how a live 'working' session got ruled dead while eight sibling sends in
    the same tick succeeded (specimens 2026-08-31 01:01 and 18:30)."""
    d = Path(state_dir) / "sdk"
    out = []
    try:
        entries = list(os.scandir(d))
    except OSError:
        # the WHOLE-LISTING twin of the per-entry contract (review find 2026-09-01): a transient
        # enumeration failure blanked every row at once — the loudest possible false-death signal
        # under this very docstring. Serve every reg's last good row instead; a genuinely empty or
        # missing dir enumerates FINE (scandir yields []/raises only on real faults), so truth is
        # untouched. A fresh process with an empty cache still returns [] — it has nothing to claim.
        if _REG_CACHE:
            if "\x00scan" not in _REG_SERVE_WARNED:   # sentinel key: incidents log once, not per scan
                _REG_SERVE_WARNED.add("\x00scan")
                sys.stderr.write("list_regs: scan of %s failed — serving every last good row\n" % d)
            return [dict(v[1]) for v in _REG_CACHE.values()]
        return out
    _REG_SERVE_WARNED.discard("\x00scan")             # healed → the next scan incident logs afresh
    seen = set()
    for de in entries:
        if not de.name.endswith(".json"):
            continue
        seen.add(de.path)
        hit = _REG_CACHE.get(de.path)
        try:
            st = de.stat()
        except OSError:
            # an exists() corroboration here defeats itself — it stats the same path the same way
            # (review find). A genuinely unlinked reg stops being LISTED by the next scandir, so
            # serving the cached row costs at most one stale scan; a transient stat error costs
            # a dropped live session, the very failure this contract closes.
            if hit is not None:
                if de.path not in _REG_SERVE_WARNED:
                    _REG_SERVE_WARNED.add(de.path)
                    sys.stderr.write("list_regs: stat failed for %s — serving the last good row\n" % de.name)
                out.append(dict(hit[1]))
            continue
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        if hit is not None and hit[0] == key:
            _REG_SERVE_WARNED.discard(de.path)         # a healed STAT blip lands here (cache hit, no
            out.append(dict(hit[1]))                   # re-read) — without this the once-per-incident
            continue                                   # latch never cleared and later blips logged
        #                                                nothing (review find 2026-09-01)
        try:
            r = json.loads(Path(de.path).read_text())
        except (OSError, ValueError) as e:
            if de.path not in _REG_SERVE_WARNED:   # once per incident, not per scan (scans run
                _REG_SERVE_WARNED.add(de.path)     # several times a second — review find)
                sys.stderr.write("list_regs: read failed for %s (%s) — %s\n"
                                 % (de.name, e.__class__.__name__,
                                    "serving the last good row" if hit is not None
                                    else "no prior row to serve"))
            if hit is not None:
                # a reg we KNOW, briefly unreadable: the last good row beats a silent drop (the
                # cache keys by content-stat, so the next healthy read replaces this at once)
                out.append(dict(hit[1]))
            continue
        _REG_SERVE_WARNED.discard(de.path)         # healed → the next incident logs afresh
        r.setdefault("sid", de.name[: -len(".json")])
        _REG_CACHE[de.path] = (key, r)
        out.append(dict(r))
    if len(_REG_CACHE) > len(seen) + 64:          # deleted regs leave the cache once the drift is real
        for p in list(_REG_CACHE):
            if p not in seen:
                _REG_CACHE.pop(p, None)
    return out


# ---------------------------------------------------------------------------
# Remembered SDK defaults (model + effort) for NEW sessions. A brand-new SDK session starts at the hardcoded
# fallbacks (DEFAULT_EFFORT + the account-default model); but the moment the user picks a model or effort on
# ANY session, we remember it here and seed the NEXT new session with it — so "what I last chose" becomes the
# startup default (the user 2026-06-27). No desync risk: the remembered value is written into the new
# session's OWN reg, which is exactly what _options launches with AND what the badge reads. Per-session
# changes still persist per-session (the reg, restored on resume); this is only the seed for new sessions.
# Stored OUTSIDE sdk/ so list_regs' sdk/*.json glob never mistakes it for a session.
# ---------------------------------------------------------------------------

FLAG_SETTINGS_DIR = "sdk-flag-settings"   # per-session --settings payloads, one file per sid

# fast_mode_disabled_reason tokens humanized for the refusal toast (_adopt_fast_state's refused-ask
# path). An unmapped token is shown raw — a loud unfamiliar word beats a silent vanish.
_FAST_REFUSALS = {
    "extra_usage_disabled": "the account has extra usage turned off, and fast mode bills through it "
                            "(claude.ai → Settings → Usage)",
}


def flag_settings_path(state_dir, sid: str, *, ultracode: bool = False, fast: bool = False) -> str:
    """The settings file handed to the CLI (options.settings — the flag-settings layer, the CLI's
    documented per-session hook for keys the SDK has no typed field for). Returns "" when a session
    needs none, which is the common case.

    Two keys ride here, both per-session:
    - `ultracode`: the SDK's typed EffortLevel has no such value — ultracode IS xhigh plus standing
      dynamic-workflow orchestration, so the typed field carries "xhigh" and this key switches the
      orchestration on.
    - `fastMode`: the CLI REFUSES fast mode to any non-interactive client ("Fast mode is not available
      in the Agent SDK") unless this exact key is true in the flag-settings layer — that check is the
      host's designed opt-in, not a loophole (verified against claude 2.1.224 on 2026-08-07: with the
      key, a headless run reports fast_mode_state "on"; without it, "off" with the disabled reason
      "sdk_opt_in_required").

    One file PER SESSION (not the single shared file the ultracode key used to get): the content now
    varies by session, so a shared file would hand one session's fast mode to every other one. Rewritten
    on every use — a couple of boolean keys, atomic enough."""
    keys = {}
    if ultracode:
        keys["ultracode"] = True
    if fast:
        keys["fastMode"] = True
    if not keys:
        return ""
    d = os.path.join(str(state_dir), FLAG_SETTINGS_DIR)
    p = os.path.join(d, "%s.json" % sid)
    try:
        os.makedirs(d, exist_ok=True)
        with open(p, "w") as f:
            f.write(json.dumps(keys) + "\n")
    except OSError:
        return ""     # no settings file → the session still launches, just without these keys
    return p


def _defaults_path(state_dir: Path) -> Path:
    return Path(state_dir) / "sdk-defaults.json"


def read_sdk_defaults(state_dir: Path) -> dict:
    """{'model': <alias|'default'>, 'effort': <level>, 'mode': <permission mode>} — whatever the user last
    picked on any session, seeded into the next new session by spawn(); {} if never set."""
    try:
        d = json.loads(_defaults_path(state_dir).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_sdk_default(state_dir: Path, **fields) -> None:
    """Merge {model?, effort?} into the remembered defaults (atomic tmp+rename). Only non-None keys passed
    are touched, so remembering a model never clobbers the remembered effort and vice-versa."""
    d = read_sdk_defaults(state_dir)
    d.update({k: v for k, v in fields.items() if v is not None})
    p = _defaults_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


_WORK_KEY: str | None = None   # process-lifetime stash; None = not yet claimed from the environment


def work_api_key() -> str:
    """The API key the manager's environment carried at startup (service.env, or however the manager
    was launched), CLAIMED OUT of os.environ on first read — "" when it carried none. The SDK's
    transport hands the CLI this process's environment wholesale (options.env merges OVER it), so an
    ambient ANTHROPIC_API_KEY bills EVERY session to the key no matter what auth the user picked for
    it; popping it here makes the key explicit per session — _options injects it only where the
    session's auth says so, and a login session launches with a genuinely clean environment (the CLI
    treats even an EMPTY var as "API-key mode, no key" and refuses with "Not logged in" — verified
    live 2026-08-08 — so removal, not blanking, is the only correct strip). Module-level so a
    re-constructed backend (tests, the WS handler's lazy construction) still finds the key after the
    first pop; the kernel process never re-execs itself, so a manager restart re-inherits the
    service env and a fresh process re-stashes."""
    global _WORK_KEY
    if _WORK_KEY is None:
        _WORK_KEY = os.environ.pop("ANTHROPIC_API_KEY", "") or ""
    return _WORK_KEY


def _expected_auth() -> str:
    """The box-wide INTENDED auth, declared in the manager's environment (service.env):
    ROMP_EXPECTED_AUTH=key|login; unset or any other value declares nothing. On a machine whose
    sessions authenticate through Claude Code's apiKeyHelper the key never rides service.env, so
    _launched_keyed reads "login" for every session while key auth IS the box's design — and the
    per-init apiKeySource mismatch line was a permanent false alarm (the user 2026-08-15). Under a
    declaration _note_auth_source compares the landing against the DECLARED side instead: the
    intended landing is quiet, the contradicting one is flagged — the never-silent property inverts
    rather than disappears. Read fresh per call (the cost is a dict lookup, and a cached read would
    outlive a test's env change); never popped — unlike ANTHROPIC_API_KEY it is not a secret and
    nothing downstream misreads it."""
    v = (os.environ.get("ROMP_EXPECTED_AUTH") or "").strip().lower()
    return v if v in ("key", "login") else ""


def _declared_auth(state_dir) -> tuple:
    """The EFFECTIVE box-wide auth expectation and where it came from: ("key"|"login"|"", "pick"|"env"|"").
    One explicit gear Billing pick makes ROMP_EXPECTED_AUTH INERT (Q3, 2026-08-26): set_auth is the
    ONLY writer of the remembered auth default, so that entry existing IS "the user has picked
    billing by hand at least once" — from then on the remembered pick is the box's expectation and
    the env declaration stops speaking (it described the box's unpicked design; once billing is
    hand-managed it is stale doctrine, and its per-init alarms fought the user's own choice on
    every re-seeded spawn). A SPAWN re-seeding reg.auth from the remembered default never counts
    as explicit — inertness keys on the PICK EVENT's durable trace (the defaults entry), never on
    any session's seeded state; hand-editing sdk-defaults.json stays the sanctioned escape hatch
    (the mode precedent)."""
    try:
        d = read_sdk_defaults(Path(state_dir))
    except Exception:
        d = {}
    a = d.get("auth")
    if a in ("key", "login"):
        return a, "pick"
    v = _expected_auth()
    return v, ("env" if v else "")


# ---------------------------------------------------------------------------
# Fast-mode permission for key-billed sessions — ask the account that PAYS.
# ---------------------------------------------------------------------------

FAST_ORG_PATH = "/api/claude_code_penguin_mode"   # the CLI's own fast-mode availability endpoint

_FAST_ORG_VERDICTS: dict[str, bool] = {}   # key -> the server's last definitive answer


def _fetch_key_fast_org(key: str) -> bool | None:
    """The fast-mode availability answer for the API key's OWN account, from the same endpoint the
    CLI asks — True/False on a definitive server answer, None on any failure (network, bad status,
    unexpected shape). Split out so tests replace it; the policy lives in key_fast_org_env."""
    import urllib.request
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    req = urllib.request.Request(base + FAST_ORG_PATH, headers={"x-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    v = d.get("enabled") if isinstance(d, dict) else None
    return v if isinstance(v, bool) else None


def key_fast_org_env(key: str, log) -> dict[str, str]:
    """Env additions that make the CLI's fast-mode PERMISSION follow the session's BILLING (the user
    2026-08-14, who found fast mode refused on a key-billed session the key's account allows). The
    CLI's availability probe asks the saved claude.ai login whenever one exists, even on a session
    whose inference bills the injected key (verified against claude 2.1.228 on 2026-08-14: the probe
    prefers the stored OAuth token over the key, so a machine whose login has extra usage off refuses
    the fast mode the paying account permits). So at every key-billed connect the kernel asks the
    paying account itself — the same endpoint, credentialed with the key — and translates the answer
    into the CLI's own switches:
      * enabled  -> CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK=1 — the CLI skips its wrong-account probe.
      * disabled -> CLAUDE_CODE_DISABLE_FAST_MODE=1 — the wrong-account probe could just as well say
        YES to a fast mode the paying org turned OFF; permission follows billing in both directions.
      * unknown  -> last definitive answer if one exists (logged), else {} — no answer is no licence
        to skip, so the CLI's own behavior stands and the failure is logged where the Log panel
        shows it. Retry is event-keyed on the next connect; there is no timer.
    Asked per connect (connects are rare and already network-bound; a fast toggle reconnects, so the
    check is fresh exactly when it matters), capped at 3s so a black-holed network cannot hang a
    connect. Never injected for login sessions: there the CLI's probe already asks the account that
    pays."""
    verdict = _fetch_key_fast_org(key)
    if verdict is None:
        if key in _FAST_ORG_VERDICTS:
            verdict = _FAST_ORG_VERDICTS[key]
            log("fast-mode org check (key account): unreachable — standing on the last answer "
                "(%s)" % ("enabled" if verdict else "disabled"))
        else:
            log("fast-mode org check (key account): unreachable and no prior answer — the CLI's "
                "own check stands (it may consult the claude.ai login, not the paying account)",
                problem=True)
            return {}
    else:
        if _FAST_ORG_VERDICTS.get(key) != verdict:
            log("fast-mode org check (key account): %s" % ("enabled" if verdict else "disabled"))
        _FAST_ORG_VERDICTS[key] = verdict
    if verdict:
        return {"CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1"}
    return {"CLAUDE_CODE_DISABLE_FAST_MODE": "1"}


# ---------------------------------------------------------------------------
# The live session (one quarantined asyncio thread).
# ---------------------------------------------------------------------------

class _AskCancelled(Exception):
    pass



_MODEL_TIERS = ("haiku", "sonnet", "opus", "fable", "mythos")   # rank = index; fable/mythos share the top in
#                                                                 practice but a swap between them is lateral,
#                                                                 not a fallback, so distinct ranks are fine


def _model_rank(name):
    """The model FAMILY rank of a model id/display name, or None when no family matches — substring
    match, so 'claude-sonnet-5', 'Sonnet 5' and 'us.anthropic.claude-sonnet-…' all rank the same."""
    low = str(name or "").lower()
    for i, fam in enumerate(_MODEL_TIERS):
        if fam in low:
            return i
    return None


def _model_downgrade(frm, to):
    """True only for a KNOWN down-tier transition — the shape of an API capacity fallback. Unknown
    names never qualify: a mint on a user's own exotic pick is worse than a missed exotic fallback."""
    a, b = _model_rank(frm), _model_rank(to)
    return a is not None and b is not None and b < a


class SdkSession:
    """One long-lived SDK client running in its own thread + asyncio loop."""

    # One diagnostic per process if an api_retry payload stops matching every spelling we know (see the
    # api_retry branch): the retry detail goes blank silently otherwise, which is how it stayed broken.
    _retry_shape_warned = False

    def __init__(self, backend: "SdkBackend", reg: dict):
        self.backend = backend
        self.sid = reg["sid"]
        self.name = reg.get("name", self.sid)
        self.cwd = reg.get("cwd") or os.path.expanduser("~")
        self.mode = reg.get("mode") or "acceptEdits"
        # The PROCESS GENERATION stamp: session-scoped timers live in THIS CLI process's memory, so
        # every armed-timer record carries the generation that armed it (procGen). A recorded timer
        # whose generation is still the live one WILL be fired by the CLI itself — the kernel must
        # never race it; a timer whose generation is gone can never fire CLI-side — the kernel owns
        # its delivery. Exact ownership, no grace-window guessing (review of #769, 2026-08-28).
        # A uuid, never a clock: two objects born in the same millisecond aliased a rounded
        # timestamp (CI caught it), and aliased generations would hand a live process's timer to
        # the kernel. The time prefix is for humans reading regs, not for identity.
        self.proc_gen = "%d.%s" % (int(time.time()), uuid.uuid4().hex[:10])
        self.resume_sid = reg.get("lastSid") or None  # resume target after a restart/crash
        # A FORK being born (backend.fork): lastSid points at the PARENT's transcript and forkOf marks the
        # resume as a fork, so _options adds fork_session (+ the resume-session-at cut) and the CLI copies
        # the conversation into a NEW file pinned to THIS sid instead of continuing the parent's. One-shot:
        # cleared the moment the init's lastSid flip says the fork landed (see _on_message).
        self._fork_of = reg.get("forkOf") or ""
        # the OWNING session for a highlight-reply comment thread (reg threadOf, durable): the
        # thread's spend bills the OWNER's sid (T144 — fork sids minted phantom cheap sessions in
        # spend.json and undercounted the owner: one session split $360.31 own + $3.11 + $5.36
        # fork sids). A deliberate user fork has no threadOf and bills itself — it IS a session.
        self.thread_of = reg.get("threadOf") or ""
        self._fork_at = reg.get("forkAt") or ""
        # Heal STRANDED pending-switch flags (the user 2026-07-11, who reported the three dots sitting there
        # forever): a /model or /effort switch that was mid-flight when the previous kernel/process
        # died can never be cleared by its in-memory switch path — but the persisted flags keep the
        # badge's switching-dots alive whenever the session is served from the reg (the dormant path
        # in live_sessions). A FRESH construction makes them moot by definition: effort is a
        # connect-time flag this session's next _options applies, and the chosen model alias
        # (reg['model']) rides the same connect — the switch is effectively applied, so pending is over.
        if reg.get("effortPending") or reg.get("modelPending") or reg.get("authPending"):
            backend._update_reg(self.sid, effortPending=False, modelPending=False, authPending=False)
        # protocol/runtime state
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client = None
        self.inflight = 0
        # The TEXTS of turns fed to the current client whose ResultMessage hasn't landed — the fed-turn
        # twin of `inflight` (append at feed, cleared at the authoritative settle), all on the loop
        # thread. Exists for the reconnect teardown: a turn fed into a client being torn down is in NO
        # store (inputs() already removed it from the persisted queue), and without its text the
        # loop-top reconcile could only settle counters while the turn itself vanished (2026-08-16).
        self._inflight_texts: list[str] = []
        # The CLI's own stderr, last few lines (see _on_cli_stderr). The SDK only PIPES the child's
        # stderr when this callback is registered, so without it a launch failure's real cause — the
        # line the CLI printed before exiting — is discarded by the transport and never reaches the
        # user or the log. Bounded: a chatty CLI must not grow this without limit.
        self._stderr_tail = deque(maxlen=STDERR_TAIL_LINES)
        # AUTHORITATIVE compacting signal (the user 2026-07-14): set when a /compact is delivered, cleared by
        # the compact_boundary event (a real compaction landed → the continuation is normal work) OR by the
        # /compact turn's ResultMessage (nothing-to-compact → no boundary ever comes). This replaces the
        # kernel's optimistic _compact_clicked + 180s cap for SDK sessions — that cap held parked ops (a
        # model pick, a message) hostage for up to 3 minutes whenever /compact found nothing to compact.
        self._compacting = False
        # AUTHORITATIVE clearing signal, the /clear twin of _compacting: set when a /clear is delivered,
        # cleared by the init whose session_id flips lastSid (the fork landed — the fresh conversation
        # exists) or by the turn's ResultMessage (backstop). Drives the chat's live "clearing" indicator
        # and the "clearing" chip so a /clear never reads as a dead gap or "No messages yet.".
        self._clearing = False
        # BUSY is an EVENT, not a count. The CLI is "working" from the moment we hand it input (or it
        # streams output) until it emits a ResultMessage; a ResultMessage means the CLI has drained
        # EVERYTHING we sent — however many messages were forwarded mid-turn — and is idle again. We
        # therefore never DECREMENT toward idle: the Result settles it in one step (see _on_message).
        # _cli_working mirrors the last lifecycle state we persisted so the live stream can re-assert
        # 'working' if a stamp ever falls behind actual output, with no reliance on feed-vs-result counting.
        self._cli_working = False
        self._skill_tool_ids = set()   # Skill tool_use ids seen on THIS stream → classify their injected
        #                                instructions payload (parent_tool_use_id link) as a skillMd atom
        self.since = 0
        self.model = reg.get("liveModel") or ""   # seed from the last-known model so the badge/picker show on
        #                                           OPEN (even once eager-connected, before init/a turn reports)
        _lc0 = reg.get("liveCtx")                 # context-window fill %, as the SDK reports it (see _ctx_pct).
        self._ctx: int | None = _lc0 if isinstance(_lc0, (int, float)) else None  # seeded from the last persisted
        #   value so the bar survives idle/restart; refreshed live from get_context_usage() on connect + each turn.
        _lt0 = reg.get("liveCtxTokens")           # raw totalTokens from the same get_context_usage payload —
        self._ctx_tokens: int | None = (int(_lt0) if isinstance(_lt0, (int, float)) else None)   # the kernel's
        #   compaction-suggestion thresholds key on true tokens, window-independent (2026-08-30)
        self._ctx_over = bool(reg.get("liveCtxOver"))   # the CLI's percentage ran past 100 (tokens exceed the
        #   CURRENT model's window — a 1M→200k model switch does this instantly); the battery caps at 100 but
        #   says so instead of presenting a silent full battery (the user 2026-09-02)
        self._ctx_refreshing = False             # one get_context_usage control request in flight at a time
        self._ctx_refresh_again = False          # a refresh asked for WHILE one was in flight → run once more
        self._usage_refreshing = False           # one get_usage control request in flight at a time
        self.retrying = False                        # an api_retry storm (API rate-limit/overload) is stalling the turn → 'retrying', not 'working'
        self.retry_count = 0                          # api_retry backoff attempts in the CURRENT storm; → the live 'attempt N' + the 'Recovered after N retries' note, reset each turn
        self.retry_info = None                        # the CURRENT storm's latest api_retry detail (attempt/max, error status+message, next-attempt epoch) → the chat retrying element's extra context (the user 2026-07-10); lives and dies with `retrying`
        self._interrupted = False                    # user interrupted the in-flight turn → snapshot reads 'waiting' (display only; inflight stays event-driven)
        self._intr_level = 0                         # interrupt escalation rung this episode (interrupt_action); reset on settle / fresh turn
        self._subagents: dict[str, dict] = {}        # LIVE Task-spawned subagents: agent_id -> {"type","since"}. Fed
        #   by the SubagentStart/SubagentStop hooks — the exact, event-based "what's running right now" signal the
        #   tmux backend never had. Keeps the session 'working' while any run and surfaces a live count on the lane.
        self._bg_tasks: dict[str, dict] = {}         # LIVE background tasks (a run_in_background Bash, a bg agent):
        #   task_id -> {"desc","type","since","toolUseId","lastTool"}. Fed by the CLI's DESIGNED task lifecycle
        #   stream (system/task_started..task_updated — see _on_message), terminal statuses clear — so an idle
        #   session waiting on a timer/watcher it launched reads AWAITING instead of plain idle (the user
        #   2026-07-11: nimbus's 20-minute campaign timer). Replaces transcript-scrape liveness for SDK sessions.
        self._sub_lock = threading.Lock()            #   hooks mutate on the loop thread; snapshot() reads from the kernel thread
        #                                                (guards _subagents AND _bg_tasks)
        self.chosen_model = reg.get("model") or ""   # the alias the user picked (opus/sonnet/…); self.model is the display name
        self._model_pending = ""                     # target ALIAS while a /model switch is resolving: the badge shows
        #   animated dots until the LIVE model actually reflects the pick (the user 2026-07-03: a switch stamped the
        #   chosen alias but left liveModel stale, and model_label PREFERS liveModel → the badge kept the OLD name).
        #   Cleared the instant _learn_model / _do_refresh_context reports a model matching the alias (event-based).
        self.effort = reg.get("effort") or DEFAULT_EFFORT   # connect-time --effort; tracked since the init msg doesn't echo it
        self._effort_pending = ""                    # target LEVEL while an /effort switch RECONNECTS to apply (--effort is
        #   a connect-time flag, no runtime control): the effort badge shows the switching-dots + the chat shows a
        #   "Reloading session…" notice until the reconnect completes (the user 2026-07-06). Cleared the instant the
        #   new client connects (reconnect loop) — event-based, mirroring _model_pending's dots.
        self.perm_mode = self.mode
        self.fast = reg.get("liveFast") or ""   # fast-mode state as the CLI last reported it ("on"/"off"/
        #   "cooldown"); "" = unknown → the chat shows no fast badge rather than a guess. Seeded from the
        #   reg (the liveModel pattern) so a kernel restart doesn't blank every session's badge until its
        #   next turn (the user 2026-08-10, who found the fast toggle nowhere). Flipped optimistically by
        #   set_fast (the /fast command is delivered like any typed one) and re-asserted by
        #   _adopt_fast_state at every connect and every init, so a refused toggle or a stale seed can't
        #   stick past the next connect.
        self.fast_reason = reg.get("liveFastReason") or ""   # the CLI's fast_mode_disabled_reason —
        #   non-empty means /fast would refuse (org-gated / unsupported), so the chat hides the toggle
        #   instead of offering a dead control. Persisted and seeded beside liveFast.
        self.fast_opt = bool(reg.get("fast"))   # the user's PERSISTED fast-mode ask (the reg's `fast`).
        #   The CLI refuses /fast to a non-interactive client unless the connect carried the `fastMode`
        #   flag-settings opt-in, so this drives that key in _options at every connect. Per-session on
        #   purpose: fast mode draws credits at a higher rate, so it is never a remembered default.
        self._fast_unlocked = False   # whether THIS connection was made with the opt-in flag — the
        #   per-CONNECTION snapshot of fast_opt, taken where _connect_once builds options and never
        #   persisted. Only an unlocked connection accepts literal '/fast on|off' sends; without the
        #   flag the CLI refuses them, so set_fast reconnects instead.
        self._fast_expect = ""   # one-shot: the word a literal '/fast' send asked for. The toggle's own
        #   turn opens with an init whose fast_mode_state is the state at turn START — one word stale,
        #   since the toggle applies after it — and taking it verbatim stomps the optimistic flip until
        #   the NEXT turn's init (the badge reads off for a whole turn right after the CLI acknowledged
        #   the toggle). The init re-sync lets that single stale word yield to this; the next init wins
        #   unconditionally.
        _aka = reg.get("apiKeyAuth")   # the persisted CLI truth (written by _note_auth_source on flip)
        self.api_key_auth = bool(_aka)   # THIS session's init said it authenticates with an API key — a
        #   PER-SESSION fact (the user 2026-08-08: one keyed session must not speak for the login's
        #   windows). Gates this session's get_usage polls + its RateLimitEvent records; set by
        #   _note_auth_source on every init, PERSISTED (reg apiKeyAuth) and restored here — as a
        #   runtime-only default-False flag, a keyed session's rate-limit events landed in the login's
        #   usage.json between a kernel restart and its next init (the max-merge contamination the
        #   gate exists to prevent).
        self.auth_live = ("key" if _aka else "login") if isinstance(_aka, bool) else ""   # what the
        #   CLI's init actually reported ("" until one ever lands) → snapshot()'s authLive, the
        #   Billing row's live truth; restored with the flag so the hover stays honest across restarts
        self.auth = reg.get("auth") if reg.get("auth") in ("login", "key") else ""   # the user's
        #   per-session auth pick (the user 2026-08-08: some sessions on the personal login, some on
        #   the work key). "" = no explicit pick → effective_auth() preserves the pre-selector world.
        self._auth_pending = ""      # target while the applying reconnect is in flight (auth is
        #   connect-time env, no runtime control) — mirrors _effort_pending's dots + notice
        self._launched_keyed = False  # what _options actually handed the CLI (key injected or not);
        #   _note_auth_source compares the init's apiKeySource against THIS, so a CLI that lands on
        #   the other auth (a stale login, a key found via apiKeyHelper) is flagged loudly instead
        #   of silently billing the wrong account
        self._last_cost_total = 0.0   # the CLI's totalCostUSD is CUMULATIVE per process (verified in
        #   the bundle: the result event's total_cost_usd sits beside total_duration/lines counters),
        #   so spend folds the DELTA between results — folding the raw value re-added the whole
        #   session-so-far cost every turn (the user 2026-08-08, whose spend line was fiction). Reset
        #   at each connect: a fresh CLI process starts its counter at zero.
        self._last_usage_totals = {}  # same for the TOKEN counts: the result event's usage is the
        #   process-lifetime `this.totalUsage` counter (verified in the bundle beside total_cost_usd),
        #   so each field folds as a delta too — raw folding compounded the token readout exactly like
        #   the dollars (the user 2026-08-08, round two: the hover's 5h/7d/month $-per-token ratios
        #   diverged wildly because each window carried a different inflation factor).
        # Pending conversation REWIND (the chat's edit-message branch): the target record uuid +
        # the transcript leaf recorded at request time (the one-shot guard — see rewind_disposition).
        # Seeded from the reg so a kernel death mid-rewind re-applies it iff nothing landed since.
        # _rewind_armed = THIS connect was launched with --resume-session-at (set by _options), the
        # event the input generator waits for before releasing the held edit turn — feeding it any
        # earlier would land the edit on the un-rewound branch.
        self._rewind_to = reg.get("rewindTo") or ""
        self._rewind_leaf = reg.get("rewindLeaf") or ""
        self._rewind_bare = bool(reg.get("rewindBare"))   # a DELETE rollback: no replacement turn enqueued
        self._rewind_armed = False
        # delete-while-busy (the user 2026-08-29): the gesture landed mid-turn — the interrupt is in
        # flight and the arm completes at the turn's actual END (_complete_rewind_wait, keyed on the
        # Stop hook / inflight settle, never a sleep). Reg-seeded so a kernel death mid-window is
        # VISIBLE at the next connect (the leaf verification there reads the flag as spent and
        # restores loudly — never a wrong-branch cut). The revalidate closure is the kernel's
        # arm-time re-check (the compaction boundary lives in the kernel's parse); in-memory on
        # purpose — it cannot survive a restart, and the spent path covers that honestly.
        self._rewind_wait = bool(reg.get("rewindWait"))
        self._rewind_revalidate = None
        # reconnect machinery: effort changes (a connect-time flag) reconnect the client; _wake breaks the
        # receive loop cleanly for shutdown OR reconnect even when idle (a bare async-for would block forever).
        self._wake: asyncio.Event | None = None
        self._reconnect = False                 # the current break is a reconnect (not a shutdown)
        self._reconnect_when_idle = False        # a reconnect was requested mid-turn → apply at turn end
        self.ended = False
        # input + ask bridging
        # Queued user turns are held in a VISIBLE list (not flushed into the SDK) until the
        # in-flight turn ends, so the kernel can render them as the chat's "queued" indicator
        # (pending_queued). One turn in flight at a time; the not-yet-started turns persist here
        # across a reconnect. _input_wake is set whenever a turn may have become releasable.
        # Seeded from the registry's persisted queue (mirrored on every mutation — _persist_queue)
        # so a kernel death can DELAY queued messages but never lose them; the boot reconcile
        # resumes any session with a non-empty persisted queue and this seed delivers it.
        self._pending: list[str] = [t for t in (reg.get("queue") or []) if isinstance(t, str) and t]
        self._ping_feeding = False   # a rename ping was fed and its turn hasn't streamed yet: hold the
        #                              queue so no message can share its pre-turn window (the CLI batches
        #                              everything pre-start into ONE record — the 2026-08-25 fold); cleared
        #                              by the turn's first streamed message, an exact event, or a reconnect
        # A RESTORED /compact must light the compacting bracket too (the user 2026-07-22). send() sets
        # _compacting when it enqueues a compact command, but a persisted queue lands here INSTEAD of
        # going through send() — any /compact still queued when the kernel died arrives this way. Without
        # this the flag stayed False for the whole compaction: the chip read plain "working" for minutes
        # with nothing visibly happening, and drive ops that should have PARKED were fed into the
        # compacting CLI. (Found via the since-removed resume gate, whose "compact on resume" queued it.)
        # Same enqueue-time semantics as send(); cleared event-based by the boundary / the turn's result.
        if any(_is_compact_cmd(t) for t in self._pending):
            self._compacting = True
        if any(_is_clear_cmd(t) for t in self._pending):   # same restored-queue rule for a queued /clear
            self._clearing = True
        self._input_wake: asyncio.Event | None = None
        self._cur_ask_fut: asyncio.Future | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        # Boot-stagger hook (see BOOT_RESUME_CONCURRENCY): fired exactly once when this session's CLI
        # is demonstrably past its spawn+catch-up burst (first init message) or its thread dies —
        # whichever comes first. The boot reconcile parks a semaphore release here.
        self.on_boot_settled = None
        self.thread = threading.Thread(target=self._run, name=f"sdk:{self.name}", daemon=True)

    # ---- kernel-thread API (thread-safe) ----

    def start(self):
        self.thread.start()

    def _fire_boot_settled(self):
        """Invoke the parked on_boot_settled callback exactly once (init arrived OR the thread died —
        both mean the spawn's CPU burst is over or moot). Pop-under-lock makes the exactly-once hold
        across the init path (asyncio thread) racing the death path (_run's finally)."""
        with self._lock:
            cb, self.on_boot_settled = self.on_boot_settled, None
        if cb:
            try:
                cb()
            except Exception as e:
                self.backend._log("boot-settled callback (%s) failed: %s" % (self.name, e))

    def enqueue(self, text: str):
        """Deliver a user turn (called from the kernel thread). Held in self._pending —
        VISIBLE to pending_queued — until the input generator releases it at turn end. Works
        before the loop is ready too (the generator drains _pending on its first pass)."""
        with self._lock:
            self._pending.append(text)
            loop, wake = self.loop, self._input_wake
        self._persist_queue()
        if loop is not None and wake is not None:
            loop.call_soon_threadsafe(wake.set)

    def pending(self) -> list[str]:
        """The queued user turns not yet started (oldest first); thread-safe. The kernel
        renders these as the chat's 'queued' indicator for this SDK session."""
        with self._lock:
            return list(self._pending)

    def unqueue(self, idx: int, expect: str | None = None) -> str | None:
        """Remove the queued turn at position `idx` (the chat's queued list is this same _pending order)
        and return its raw text, or None if it's gone. Lets the user CANCEL a message they queued
        behind a busy turn — click it in the chat to pull it back out and re-edit (the user 2026-06-27).
        Only pending (not-yet-started) turns are cancelable; once the input generator has fed a turn to
        the CLI there is no recall (the control protocol has no queue-remove), so a miss here is the
        caller's cue to say so loudly. `expect` is the exact text the click meant: verified (and, on a
        shifted index, re-located) UNDER the lock, so the input generator consuming entries between the
        caller's snapshot and this pop can never cancel the wrong message."""
        with self._lock:
            if expect is not None and not (0 <= idx < len(self._pending) and self._pending[idx] == expect):
                idx = next((i for i, q in enumerate(self._pending) if q == expect), -1)
            item = self._pending.pop(idx) if 0 <= idx < len(self._pending) else None
        if item is not None:
            self._persist_queue()
        return item

    def _persist_queue(self):
        """Mirror _pending to the registry (reg['queue']) so queued turns survive a kernel death —
        the boot reconcile resumes any session whose persisted queue is non-empty and the __init__
        seed re-delivers it. Called on every mutation (enqueue / unqueue / the input generator's
        pop), from the kernel thread AND the loop thread — _update_reg serializes the writes. A
        turn already FED to the SDK is out of the persisted queue by design: it reaches the
        transcript as a user atom, which is the cut-turn resume's territory, not replay's."""
        with self._lock:
            snap = list(self._pending)
        try:
            self.backend._update_reg(self.sid, queue=snap)
        except Exception:
            self.backend._log("persist queue (%s): %s" % (self.name, traceback.format_exc()))

    def interrupt(self):
        """Escalating stop (the user 2026-07-10, terminal parity). The old body was `if self.loop and
        self.client: <control request>` — a wedged CLI ignored the request ('no current client', 14
        deep in manager.log while nimbus sat unresponsive) and a missing client made the press a
        SILENT no-op, so the stop button had no path to the kill that the design itself named as the
        recovery. Now every press climbs interrupt_action's ladder — control request, SIGINT the CLI,
        SIGKILL (its stream death runs the existing crash-heal + resume) — and every rung logs what it
        did. The episode resets when a turn settles or a fresh turn starts, so a later stop is polite
        again."""
        with self._lock:
            action, self._intr_level = interrupt_action(self._intr_level, bool(self.loop and self.client))
        if action == "control":
            # Flip the in-flight flag SYNCHRONOUSLY, here on the kernel thread, before scheduling the async
            # _do_interrupt. The kernel stamps _interrupt_clicked and pushes the instant it returns from this
            # call, and the very next snapshot() must already read 'interrupting' — otherwise the chip/feed
            # badge would miss the click and only catch up an event-loop tick later (the flicker this whole
            # signal exists to kill). _do_interrupt sets it again (harmless); the ResultMessage clears it.
            self._interrupted = True
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._do_interrupt()))
            return
        self._signal_cli(signal.SIGINT if action == "sigint" else signal.SIGKILL, action)

    def _signal_cli(self, sig, action):
        """Deliver an escalated interrupt as a real signal to this session's own CLI (the child of THIS
        kernel resuming our sid — find_session_cli can't match anything else). Loud on every outcome:
        the whole bug was a stop that vanished without a trace."""
        pid = self.backend._session_cli_pid(self)
        if pid is None:
            self.backend._log("interrupt (%s): %s escalation found no CLI process — nothing to signal" % (self.name, action))
            return
        if self.inflight > 0:
            # Latch only when a turn exists for this signal to stop: that turn's ResultMessage (or the
            # kill's stream-death reconcile) clears the flag. On an idle session there is no such settle
            # event, so latching would strand 'Interrupting…' (the 2026-07-20 strand, escalation flavor).
            self._interrupted = True
        try:
            os.kill(pid, sig)
            self.backend._log("interrupt (%s): escalated to %s pid %d" % (self.name, action, pid))
        except ProcessLookupError:
            self.backend._log("interrupt (%s): CLI pid %d already gone" % (self.name, pid))
        self.backend._poke()

    def set_model_live(self, model):
        """Change the model on a CONNECTED session via the SDK control channel. No-op if not yet
        connected — _options applies chosen_model on connect instead."""
        if self.loop and self.client:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._do_set_model(model)))

    def set_mode_live(self, mode, prev="default"):
        """Change the permission mode on a CONNECTED session via the SDK control channel. `prev` is
        the last CONFIRMED mode, captured by set_mode before its optimistic flip — the value every
        layer reverts to if the CLI refuses the switch (T139)."""
        if self.loop and self.client:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._do_set_mode(mode, prev)))

    def resolve_ask(self, kind: str, payload=None) -> bool:
        """Deliver a picker/permission UI action (answer/toggle/submit/custom/
        cancel/text) into the waiting can_use_tool coroutine. Returns whether a live
        ask was actually WAITING (T214, verified live 2026-09-01: an answer flushed
        across a kernel restart found _cur_ask_fut None — the ask died with the old
        process — and this silently no-opped while the caller reported success and
        the board flipped to Working; the delivery outcome must be the truth). The
        synchronous check races a concurrently-presenting ask by microseconds
        against human-scale answers — the guarded _set below keeps the delivery
        itself safe either way."""
        if not self.loop:
            return False
        fut = self._cur_ask_fut
        if fut is None or fut.done():
            return False                       # nothing is waiting — the ask died (a restart) or is already resolved
        def _set():
            f = self._cur_ask_fut
            if f and not f.done():
                f.set_result((kind, payload))
        self.loop.call_soon_threadsafe(_set)
        return True

    def shutdown(self):
        self.ended = True
        if self.loop:
            self.loop.call_soon_threadsafe(self._wake_set)   # break the receive loop even if idle (no msg coming)
        if self.loop and self.client:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._do_interrupt()))   # stop an in-flight turn promptly

    def _wake_set(self):
        if self._wake is not None:
            self._wake.set()

    def request_reconnect(self):
        """Apply a connect-time option change (effort) by reconnecting the client — resume continues the same
        conversation. Reconnect NOW when idle; defer to the end of the current turn when busy. No-op if the
        session is shutting down or not yet connected (the new value is in the registry → it applies on connect)."""
        if self.loop is None or self.ended:
            return
        self.loop.call_soon_threadsafe(self._do_request_reconnect)

    def _do_request_reconnect(self):
        # a rewind-HELD queue must not defer the reconnect: those turns can't start until the
        # reconnect arms them (the input gate) — deferring on their account would deadlock the rewind
        held = bool(self._rewind_to and not self._rewind_armed)
        if self.inflight == 0 and (held or not self._pending):
            self._reconnect = True
            self._wake_set()
        else:
            self._reconnect_when_idle = True   # the ResultMessage handler fires it when the turn ends

    def _reconcile_stranded(self):
        """RECONCILE ACROSS A RECONNECT, at the loop's top where no client is connected so nothing can
        legitimately be in flight (the user 2026-07-01, who switched the model on a new session and it
        said working indefinitely). A reconnect abandons the previous client; a turn it left in flight
        can NEVER get its ResultMessage on the new connection — so inflight, and the "working" signal it
        drives, would be stranded elevated FOREVER. request_reconnect defers while inflight>0, but a race
        (it fired at inflight==0, then the input generator fed a turn before the teardown ran) can still
        strand a turn here: settle the counters to idle. A not-yet-STARTED _pending turn survives as
        before (never fed to the dead client; the new inputs() re-feeds it). No-op on the first connect
        and on a clean reconnect. Event-based on the reconnect itself, not a time/age heuristic.

        And the FED turn itself must not vanish with the client it was fed to (2026-08-16: a spawn's -m
        kickoff, fed just as an effort-pin's teardown fired, landed nowhere — not in _pending, not in the
        persisted queue, not in any transcript — and its echo read "sent" for 5.5h until the next thread
        spawn finally flagged it dropped). `_inflight_texts` carries the fed-but-unresulted texts across
        the teardown:
        - NO conversation ever materialized (no init streamed → resume_sid never set): RE-HEAD the queue —
          the next client starts the conversation fresh, so re-feeding cannot duplicate anything the user
          can see. (The one theoretical overlap — the dead CLI landed the atom in the same sid-keyed file
          after our receive loop was cancelled — repeats a kickoff message, which beats silently losing
          it.)
        - A RESUMABLE conversation: the atom may genuinely have landed, so re-feeding risks a real
          duplicate. Surface the loss instead: the drop-mark flips the echo to "never delivered" NOW,
          rather than hours later at the next thread spawn."""
        if not self.inflight:
            return
        stranded = list(self._inflight_texts)      # fed to the abandoned client, never resulted
        self._inflight_texts.clear()
        self.inflight = 0
        self._interrupted = False
        self._intr_level = 0
        self._compacting = False   # an abandoned /compact turn can't emit its boundary/result on the dead client
        self._clearing = False     # same: an abandoned /clear turn can't emit its init/result either
        self._mark("waiting")
        self.backend.retire_live_work(self.sid)    # the abandoned turn's stream is gone with its client
        if stranded and not self.resume_sid:
            with self._lock:
                self._pending[0:0] = stranded
            self._persist_queue()                  # the fresh inputs() drains _pending on its first pass
        elif stranded:
            self.backend._mark_dropped_echoes(self.sid, self.pending())
        self.backend._poke()

    # ---- async internals (run inside the quarantined loop) ----

    async def _do_interrupt(self):
        # ACKNOWLEDGE FIRST, then send the control request. client.interrupt() BLOCKS until the CLI
        # acknowledges the interrupt — and the CLI won't acknowledge until the in-flight model call reaches
        # a boundary, which can take SECONDS mid-stream. Setting _interrupted only AFTER that await meant the
        # snapshot kept reading 'working' the whole time, so a stopped turn still looked like it was spinning
        # (the user 2026-06-30, who interrupted but it said working for a while). Flip + poke up front so the
        # lane reads 'waiting' the instant the user hits stop; the interrupt itself completes below.
        #
        # Don't touch inflight or release the next queued turn here. A normal interrupt aborts the turn and the
        # SDK emits its ResultMessage, which does the SINGLE decrement + the natural release in _on_message;
        # forcing inflight=0 here too would double-count and corrupt the next turn's release. The snapshot
        # reads 'waiting' while inflight>0 (kills the 2026-06-23 zombie-working); a truly-wedged turn that
        # never results keeps inflight>0 and PAUSES the queue — honest (kill recovers). A fresh turn clears
        # _interrupted (see inputs()), and the ResultMessage clears it too.
        self._interrupted = True
        self.backend._poke()
        try:
            await self.client.interrupt()
        except Exception as e:
            # NEVER swallow a failed stop (the nimbus wedge, the user 2026-07-10): the request failing
            # IS an event. Log it, and when the CLI provably owes us a turn (inflight > 0) escalate to
            # SIGINT on this same press — the polite channel just demonstrated it can't stop that turn.
            # An idle-session refusal ('no current client') only logs: there may be nothing to stop, and
            # the next press escalates via the ladder anyway. The signal runs on a plain thread so this
            # loop thread never blocks on `ps`.
            self.backend._log("interrupt (%s): control request failed: %s" % (self.name, e))
            if self.inflight > 0:
                threading.Thread(target=self._signal_cli, args=(signal.SIGINT, "sigint-auto"),
                                 name=f"sdk-intr:{self.name}", daemon=True).start()
                with self._lock:
                    self._intr_level = max(self._intr_level, 2)
        # The completed control round-trip IS the settle when nothing is in flight. A stop can race the
        # turn's own death (the user 2026-07-20: stop pressed one second after the turn died of its
        # API-retry storm) — the ResultMessage had already settled inflight to 0 and cleared the flag,
        # then the set above re-latched it with no turn left to emit a clearing event, so an IDLE session
        # wore 'Interrupting…' until the next fed turn (or the kernel's 120s cap). With inflight==0 no
        # ResultMessage is coming: drop the flag here, on the ack/failure event itself. A live turn
        # (inflight>0) keeps it — its own ResultMessage clears it, as designed.
        if self.inflight == 0 and self._interrupted:
            self._interrupted = False
            self.backend._poke()

    async def _do_set_model(self, model):
        try:
            await self.client.set_model(model)
        except Exception as e:
            self.backend._log("set_model (%s -> %s) refused by the SDK: %s: %s" % (self.name, model, type(e).__name__, e))
        # Pull the real new name NOW rather than waiting for the next turn's assistant message — an idle
        # session the user switched but doesn't drive again would otherwise sit on the switching-dots
        # indefinitely (the user 2026-07-03). get_context_usage reports the current model, so this
        # resolves the pending switch the moment the CLI applies it.
        await self._do_refresh_context()
        self.backend._poke()

    async def _do_set_mode(self, mode, prev="default"):
        try:
            await self.client.set_permission_mode(mode)
        except Exception as e:
            # the T124 rule, applied to modes (T139): set_mode flips the DISPLAYED mode
            # optimistically — a refusal that only logged left the switcher asserting a mode the
            # CLI never accepted, indefinitely. Revert every layer to the last confirmed mode
            # (snapshot, session, registry — the next connect must not re-apply the refused pick
            # either) and ring the problems so the failed switch is unmissable.
            self.perm_mode = prev
            self.mode = prev
            self.backend._update_reg(self.sid, mode=prev)
            self.backend._log("set_permission_mode (%s -> %s) refused by the SDK: %s: %s — the switch "
                              "did NOT apply; the mode reverted to %s"
                              % (self.name, mode, type(e).__name__, e, prev), problem=True)
            self.backend._poke()

    async def _do_refresh_context(self):
        """Pull authoritative context-window usage from the SDK — the DESIGNED source. `get_context_usage()` is
        the SDK's native control request behind the CLI's `/context`: it returns a `percentage` already computed
        against the real window AND the autocompact buffer, plus the live model id. This replaces inferring the
        window from peak prompt sizes (the user 2026-06-24: the SDK read 14% where tmux read 3% on a 1M-context
        model — a wrong-window guess). Updates the live % + model and persists both (so a dormant / restarted
        session keeps showing them). Cheap; guarded so only one is in flight."""
        if not self.client:
            return
        if self._ctx_refreshing:
            # a model/effort switch can land while the turn-end refresh is mid-flight; DROPPING this
            # call left the old model's percentage standing until the next turn (the user 2026-09-02,
            # who watched the battery misreport right after a switch) — queue exactly one rerun
            self._ctx_refresh_again = True
            return
        self._ctx_refreshing = True
        try:
            cu = await self.client.get_context_usage()
        except Exception:
            cu = None
        finally:
            self._ctx_refreshing = False
        if not isinstance(cu, dict):
            return
        changed = False
        pct = cu.get("percentage")
        if isinstance(pct, (int, float)):
            # the CLI documents percentage as "0-100+": past 100 the tokens exceed the CURRENT
            # model's window (a 1M→200k model switch does this instantly, and the next turn
            # compacts or refuses). The clamp below is right for every gauge, but the overflow is
            # NEWS — _ctx_over lets the battery say "over this model's window" instead of a
            # silent, wrong-looking 100% (the user 2026-09-02).
            over = round(pct) > 100
            if over != self._ctx_over:
                self._ctx_over, changed = over, True
            v = max(0, min(100, round(pct)))
            if v != self._ctx:
                self._ctx, changed = v, True
        tt = cu.get("totalTokens")
        if isinstance(tt, (int, float)) and int(tt) != self._ctx_tokens:
            self._ctx_tokens, changed = int(tt), True
        pm = pretty_model(cu.get("model"))
        if pm and self._resolve_model_pending(pm):
            changed = True
        if pm and pm != self.model:
            self.model, changed = pm, True
        upd = {}
        if self.model:
            upd["liveModel"] = self.model
        upd["modelPending"] = bool(self._model_pending)
        if self._ctx is not None:
            upd["liveCtx"] = self._ctx
            upd["liveCtxOver"] = self._ctx_over
        if self._ctx_tokens is not None:
            upd["liveCtxTokens"] = self._ctx_tokens
        if upd:
            try:
                self.backend._update_reg(self.sid, **upd)
            except Exception as e:
                self.backend._log("context refresh (%s): registry write failed: %s" % (self.name, e))
        # A moved context % or model is news the panes should see now, not at the next backstop. (This
        # poke had been separated from the `changed` it reads and left sitting in refresh_usage below,
        # where the name is undefined — so every /usage click raised NameError instead, and a context
        # change waited for the backstop.)
        if changed:
            self.backend._poke()
        if self._ctx_refresh_again:
            # someone asked while this refresh was in flight (their answer may predate their event —
            # e.g. a model switch mid-refresh): run once more so the number reflects the newest world
            self._ctx_refresh_again = False
            await self._do_refresh_context()

    def _adopt_fast_state(self, d) -> bool:
        """Adopt fast-mode truth from a CLI payload that carries it. The per-turn init message and the
        connect-time initialize response (get_server_info) share the exact field names (verified live
        2026-08-10 on 2.1.226): fast_mode_state "on"/"off"/"cooldown" plus fast_mode_disabled_reason.
        An absent field (older CLI) leaves the last truth standing — never fabricate "off". Returns
        whether anything changed; a change persists to the reg (liveFast/liveFastReason, the liveModel
        pattern) so a kernel restart doesn't blank a dormant session's badge."""
        fast = d.get("fast_mode_state")
        if not (isinstance(fast, str) and fast):
            return False
        # …except the one init opened by a literal /fast send itself, whose word predates the
        # toggle it carries. A disabled_reason is real refusal evidence, so it always wins.
        if self._fast_expect and fast != self._fast_expect \
                and not d.get("fast_mode_disabled_reason"):
            fast = self._fast_expect
        self._fast_expect = ""
        reason = str(d.get("fast_mode_disabled_reason") or "")
        # 'sdk_opt_in_required' is NOT a refusal to respect — it is the one refusal romp is
        # BUILT to cure (set_fast reconnects with the fastMode flag-settings opt-in), and the
        # CLI stamps it on EVERY connect made without the flag (verified live 2026-08-10 on
        # 2.1.226: opus/fable/sonnet headless connects all report off + this reason). Keeping
        # it in fast_reason hid the chat toggle on every SDK session — the control that
        # GRANTS the opt-in was gated on already having it (the user 2026-08-10, who switched
        # to Opus and looked for the toggle). Blank it: the badge shows "Slow" and a
        # click cures the reason; every OTHER reason still hides the dead control.
        reason = "" if reason == "sdk_opt_in_required" else reason
        # A refusal that lands while the user's ask is ARMED (fast_opt — they picked On) is the CLI
        # ANSWERING that ask, not standing state to hide behind: adopting it silently made the toggle
        # the user had just clicked vanish without a word, with the ask left armed on disk forever
        # (the user 2026-08-11, whose tap on a phone was refused with extra_usage_disabled). Fail
        # loudly instead: tell the user WHY in a warn toast, clear the ask (reg + fast_opt) so the
        # opt-in flag doesn't stay armed, and reconnect — the flagless connect reports
        # sdk_opt_in_required, which blanks the reason above, so the badge comes BACK instead of
        # disappearing under the dead-control rule.
        refused_ask = bool(reason) and self.fast_opt and fast != "on"
        if refused_ask:
            self.fast_opt = False
        changed = fast != self.fast or reason != self.fast_reason
        self.fast, self.fast_reason = fast, reason
        if changed or refused_ask:
            try:
                kw = dict(liveFast=fast, liveFastReason=reason)
                if refused_ask:
                    kw["fast"] = False
                self.backend._update_reg(self.sid, **kw)
            except Exception as e:
                self.backend._log("fast-state persist (%s): registry write failed: %s" % (self.name, e))
        if refused_ask:
            why = _FAST_REFUSALS.get(reason, "the CLI reports %r" % reason)
            self.backend._log("fast mode (%s): the CLI refused the toggle — %s" % (self.name, reason),
                              problem=True)
            try:
                self.backend._notify("chat", {"type": "warn", "text":
                    "fast mode isn't available for %s — %s; the pick is back off" % (self.name, why)})
            except Exception as e:
                self.backend._log("fast mode (%s): could not tell the chat about the refusal: %s"
                                  % (self.name, e))
            self.request_reconnect()
        return changed or refused_ask

    async def _do_adopt_server_info(self):
        """Fast-mode state at CONNECT, before any turn. The init message _adopt_fast_state feeds on
        only streams WITH a turn — so after a kernel restart every session's fast badge sat blank
        until it next spoke, and a /model switch never made one appear at all (the user 2026-08-10,
        who switched a session to Opus and found no toggle). The initialize response the SDK stored
        at connect (get_server_info — the designed connect-time snapshot, this path's
        get_context_usage) carries the same fast fields, so adopt them the moment we connect."""
        if not self.client:
            return
        try:
            info = await self.client.get_server_info()
        except Exception:
            return
        if isinstance(info, dict) and self._adopt_fast_state(info):
            self.backend._poke()

    def effective_auth(self) -> str:
        """'key' or 'login' — what _options launches this session with. An explicit pick wins; unset
        preserves the pre-selector world, where a manager environment that carried a key billed every
        session to it (so absent a choice, the key still wins when one exists). 'key' with no key to
        inject falls to login rather than launching with a var the CLI would refuse on — _options
        logs that fall loudly (it is a misconfiguration, not a preference)."""
        if self.auth == "login":
            return "login"
        return "key" if self.backend.work_key else "login"

    async def _do_refresh_usage(self):
        """Pull the EXACT account-wide /usage snapshot from the CLI — the designed data behind the /usage
        screen itself. `get_usage` is a CLI control request (the bridge's onGetUsage handler; the Python
        SDK doesn't wrap it yet, so it goes through _send_control_request directly — found 2026-07-02 by
        mining the binary next to get_context_usage). Its rate_limits.limits[] carries a true percent for
        EVERY window — session, weekly_all, and the model-scoped weekly (Fable) — numbers the
        RateLimitEvent stream only ever supplies in the warning band and the statusline never carries at
        all (it lacks the Fable window entirely). Refreshed at every turn end (event-based, beside the
        context refresh) and on demand from the kernel's /usage click. Guarded: one in flight."""
        if not self.client or self._usage_refreshing:
            return
        if self.api_key_auth:
            return   # THIS session bills an API key (per-session — see _note_auth_source): it has no
            #          subscription windows and get_usage only times out — nothing to poll
        self._usage_refreshing = True
        r = None
        try:
            q = getattr(self.client, "_query", None)
            if q is None:
                # The control channel is the ONLY source of an exact reading; without it the bars can
                # only creep up from the sparse event stream, which is precisely the silently-wrong
                # number this must not become.
                self.backend._log("usage refresh (%s): no control channel on the SDK client — the rail "
                                  "bars will fall back to the rate-limit event stream" % self.name,
                                  problem=True)
            else:
                r = await q._send_control_request({"subtype": "get_usage"})
        except Exception as e:
            # LOUD (the user 2026-08-02, whose 5h bar read 18% against a real 45%). This used to swallow
            # every failure, so a refresh that never landed was indistinguishable from one that did: the
            # file kept its event-derived floor and the rail presented it as the reading.
            self.backend._log("usage refresh (%s) failed: %s: %s" % (self.name, type(e).__name__, e))
        finally:
            self._usage_refreshing = False
        if isinstance(r, dict):
            self.backend._record_usage_snapshot(r)

    def refresh_usage(self):
        """Thread-safe trigger for _do_refresh_usage (the kernel's /usage click path). Returns whether the
        refresh was actually scheduled, so the backend can move on to another session when this one has no
        live loop to run it on."""
        if self.loop and self.client:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._do_refresh_usage()))
            return True
        return False

    async def _next_ask_action(self):
        fut = asyncio.get_running_loop().create_future()
        self._cur_ask_fut = fut
        try:
            return await fut
        finally:
            self._cur_ask_fut = None

    def _run(self):
        try:
            # A FRESH CLI is about to spawn for this sid: stamp when (the kernel's bg-tasks box drops
            # unfinished tasks that predate the live CLI — they died with the old one, their completion
            # notifications can never arrive) and clear any stale awaiting overlay the old CLI's death
            # stranded (the Stop hook that clears it died too). Both previously healed only at KERNEL
            # boot, so a session restart inside a live kernel kept ghost '25 background tasks' /
            # waiting displays that read as a wedged session (nimbus, the user 2026-07-10).
            self.backend._update_reg(self.sid, spawnedAt=int(time.time()))
            self.backend._heal_stale_awaiting(self.sid)
            # The SPAWN half of the dropped-echo marking (boot half: _reseed_echoes): a fresh CLI means
            # whatever held any earlier send is gone. An echo neither in self._pending (delivered to the
            # new CLI) nor landed has no holder left — flag it so the chat says "never delivered".
            self.backend._mark_dropped_echoes(self.sid, self.pending())
            asyncio.run(self._amain())
        except Exception as e:                       # surfaced for debugging; never crash kernel
            # The TRACEBACK too, not just the type and message: a bare "KeyError: <uuid>" names no line,
            # so a crash that killed a session left nothing to fix it by. The error center shows the
            # first line; the kernel log keeps the whole thing.
            self.backend._log(f"sdk session {self.name} crashed: {type(e).__name__}: {e}\n"
                              f"{traceback.format_exc()}")
        finally:
            self._fire_boot_settled()   # a dead thread must free its boot-stagger slot (first, so
            #                             a raising _on_session_gone can never leak the slot)
            self.backend._on_session_gone(self)

    async def _amain(self):
        # Lazy SDK import — keeps the module importable without the dep. RECORD a failure here before it
        # propagates: this is the FIRST thing a session does, so with the dep missing every session dies
        # right here, and the only trace used to be one kernel stderr line per crash while the user's
        # messages piled up unsent (the user 2026-07-28).
        try:
            from claude_agent_sdk import (
                ClaudeSDKClient, ClaudeAgentOptions,
                AssistantMessage, ResultMessage, SystemMessage,
            )
        except Exception as e:
            self.backend._record_launch_error(self, e)
            raise
        self.loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        with self._lock:
            self._input_wake = asyncio.Event()
            self._ready.set()
        # Turns enqueued before the loop was ready are already in self._pending; the input
        # generator below picks them up on its first pass (no separate pre-buffer needed).

        async def inputs():
            # Forward queued turns to the SDK AS SOON AS they're available — even while a turn is in flight —
            # so a message you send mid-turn reaches the model at its NEXT tool boundary instead of being held
            # until the whole turn finishes (the user 2026-06-27, who wanted it forwarded in as soon as possible, as they
            # do with a queued message). The CLI's streaming input owns the boundary timing; romp just
            # stops artificially holding. EXCEPTION: when the current turn is INTERRUPTED/wedged (inflight>0
            # AND _interrupted), HOLD the queue — feeding the next turn into a stuck CLI is the double-count /
            # zombie hazard the interrupt path guards against; it releases once that turn's ResultMessage
            # settles inflight to 0. Recreated per reconnect; self._pending carries unstarted turns across it.
            while not self.ended:
                self._input_wake.clear()
                with self._lock:
                    blocked = self.inflight > 0 and self._interrupted   # wedged turn → don't feed a stuck CLI
                    # a pending REWIND holds the queue until a client launched with --resume-session-at is
                    # up (_rewind_armed, set by _options) — feeding the edit turn to the CURRENT client
                    # would land it on the un-rewound branch (the exact wrong-branch delivery this guards)
                    blocked = blocked or bool(self._rewind_to and not self._rewind_armed)
                    blocked = blocked or self._ping_feeding   # the ping's record must not share its window
                    # a parked deploy restart is DRAINING (T121): hold NEW turn starts — the queued
                    # prompt persists (the checkpoint) and /busy falls to 0 on this box's own
                    # turn-end events. Mid-turn forwards keep flowing (inflight > 0), so the
                    # in-flight turn can still finish; the wedged/rewind holds above are untouched.
                    blocked = blocked or (self.inflight == 0 and self.backend.drain_holding())
                    item = self._pending.pop(0) if (self._pending and not blocked) else None
                    fresh = item is not None and self.inflight == 0     # starting from idle, not mid-turn
                if item is None:
                    await self._input_wake.wait()   # idle, or holding behind a wedged turn → wait for a change
                    continue
                self._persist_queue()               # the fed turn leaves the persisted queue (it lands in the transcript)
                if fresh:
                    self.since = int(time.time())    # a new turn starts now (mid-turn forwards keep the turn's clock)
                    self._interrupted = False        # a fresh turn → clear any stale interrupt flag
                    self._intr_level = 0             #   ...and its escalation episode (a new stop starts polite)
                self.inflight += 1
                self._inflight_texts.append(item)   # the fed-turn twin — see its init comment
                if item.startswith(RENAME_PING_HEAD):
                    self._ping_feeding = True       # hold feeds until this turn's first streamed message
                self._mark("working")
                self.backend._poke()
                yield {"type": "user",
                       "message": {"role": "user", "content": [{"type": "text", "text": item}]}}

        async def drain(client):
            # Feed turns and receive messages CONCURRENTLY: query() with a streaming input iterable BLOCKS
            # until the iterable ends (it writes each turn to stdin), and our input generator never ends —
            # so awaiting it before receiving would starve the receive loop. The control channel
            # (can_use_tool) has its own reader; the message stream does not, so it's drained here.
            async for msg in client.receive_messages():
                if self.ended:
                    break
                self._on_message(msg, AssistantMessage, ResultMessage, SystemMessage)

        # Reconnect loop: one persistent client per iteration. A connect-time option change (effort, a CLI
        # flag with no runtime control) reconnects with fresh options — resume_sid continues the conversation
        # and self._pending carries any not-yet-started turns. The receive loop is RACED against a wake Event so it stops
        # cleanly for BOTH shutdown and reconnect even when idle (a bare async-for would block forever with no
        # incoming message, leaking the client + its claude subprocess).
        while not self.ended:
            self._wake.clear()
            self._reconnect = False
            self._ping_feeding = False   # a reconnect restarts the feed — a stale hold must not wedge it
            # settle + recover anything the abandoned client stranded — see _reconcile_stranded
            self._reconcile_stranded()
            opts = self.backend._options(self, ClaudeAgentOptions)
            # Whether THIS connection carries the fastMode opt-in — snapshotted at the same moment
            # _options composes the flag-settings file, so the two can never disagree. The CLI only
            # interprets literal '/fast on|off' sends on a connection made with the flag; set_fast
            # reads this to choose between the live send and an applying reconnect.
            self._fast_unlocked = self.fast_opt
            self._fast_expect = ""   # a fresh connection's first init speaks for the flag, not for any
            #   toggle sent on the old one — never hold a pre-reconnect expectation against it
            connected = False
            try:
                async with ClaudeSDKClient(options=opts) as client:
                    connected = True
                    self.client = client
                    # The handshake IS the "this session is open" event (snapshot `connected`, the flip
                    # the kernel's opening chip stands down on) — push THIS session now. Left to the
                    # periodic cycle, a fresh session wore the opening dots seconds after its CLI was
                    # ready to take a message (measured live 2026-08-10: connect done ~1.5s after
                    # create, the ready chip landing at 5-12s with the cycle).
                    self.backend._push_session(self.sid)
                    self._last_cost_total = 0.0   # a fresh CLI process starts its cumulative cost at zero
                    self._last_usage_totals = {}  # …and its cumulative token counters
                    # The CLI is demonstrably up, so any recorded launch failure is HISTORY — clear it
                    # here, at the proof, rather than on a timer. This is what lifts the usage-limit
                    # hold once the window resets: the next _ensure connects, the error record goes, and
                    # the queue the limit was holding drains on the following producer pass.
                    self.backend._clear_launch_error(self.sid)
                    # A pending /effort switch is APPLIED the instant this (re)connect lands (--effort rode _options
                    # above) → clear the switching-dots + "Reloading session…" notice (the user 2026-07-06). Covers
                    # the immediate (idle) reconnect, the deferred (turn-end) one, and a first connect that picked up
                    # a pending value from the reg. Event-based on the connect itself; pokes a push so it clears now.
                    if self._effort_pending:
                        # a DURABLE "effort set to X" marker at the apply moment: the reconnect writes no
                        # transcript record, so without this the only trace is the synthesized /effort chip,
                        # which prunes on the next message (the user 2026-07-16). Written here, not at request
                        # time, so it pins when the new effort became REAL — turn-end for a busy session, whose
                        # in-flight turn ran at the OLD effort (recording at request would misdate it).
                        # self.backend.state_dir, not self.state_dir: a session has no state_dir of its own,
                        # and the typo raised straight out of the connect path — killing the session thread
                        # on any /effort switch that applied at reconnect (found 2026-07-28 in the backend's
                        # own crash log, which until now nothing showed the user).
                        append_effort_applied(self.backend.state_dir, self.sid, self._effort_pending)
                        self._effort_pending = ""
                        self.backend._update_reg(self.sid, effortPending=False)
                        self.backend._poke()
                    # A pending AUTH switch is applied the same way — the key rode (or was withheld
                    # from) _options' env on THIS connect. The init's apiKeySource is the CLI's own
                    # confirmation and _note_auth_source flags a mismatch loudly; here we just clear
                    # the switching-dots, event-based on the connect like effort above.
                    if self._auth_pending:
                        self._auth_pending = ""
                        self.backend._update_reg(self.sid, authPending=False)
                        self.backend._poke()
                    # PRE-TURN PUBLISH (the user 2026-06-27): pull the live model + context % the INSTANT we
                    # connect — before any turn — so a freshly-created SDK session shows its model and context on
                    # OPEN, like a tmux session does on launch. The old path keyed model/ctx resolution off the
                    # `init` SystemMessage (see _on_message's init branch), but that message is NOT emitted on a
                    # turn-less streaming connection: it only arrives with the FIRST user turn (verified against the
                    # SDK — get_context_usage() answers pre-turn, but no init/system message streams until a turn is
                    # sent). That false assumption is why every prior fix left the model/context blank until the
                    # first message. get_context_usage() is the DESIGNED control request behind the CLI's /context
                    # and returns BOTH the live model id and the % pre-turn, so this one refresh fills both. Runs on
                    # every (re)connect; guarded + idempotent + pokes only on change.
                    asyncio.ensure_future(self._do_refresh_context())
                    # …and the fast-mode fields the same way: they ride the initialize response the
                    # SDK already holds (get_server_info), so the badge exists pre-turn too — without
                    # this, nothing showed after a kernel restart until each session's next turn.
                    asyncio.ensure_future(self._do_adopt_server_info())
                    feeder = asyncio.ensure_future(client.query(inputs()))
                    recv = asyncio.ensure_future(drain(client))
                    waker = asyncio.ensure_future(self._wake.wait())
                    try:
                        await asyncio.wait({recv, waker}, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for tk in (feeder, recv, waker):
                            tk.cancel()
                        for tk in (feeder, recv, waker):
                            try:
                                await tk
                            except asyncio.CancelledError:
                                pass
                            except Exception as e:                 # a genuine stream/transport error — surface it
                                self.backend._log(f"sdk session {self.name}: {type(e).__name__}: {e}")
                        self.client = None
            except Exception as e:
                # A REWIND-armed connect the CLI refused (a bad --resume-session-at target exits 1 with
                # "No message found" BEFORE the handshake) must not crash-loop: the flag would re-apply on
                # every heal (the leaf never moved — nothing was written) and brick the session. Fail LOUDLY
                # once — drop the flag, pull the edited message off the queue (it must NOT quietly land on
                # the un-rewound branch), toast the user — then reconnect plainly: the conversation is
                # untouched. Everything else keeps the existing behavior (surface + crash-heal).
                if self._rewind_armed and not connected:
                    self._rewind_failed(e)
                    continue
                if not connected:
                    # The CLI never came up. RECORD why, where the user can see it: this thread is about
                    # to die, and everything downstream of it (_on_session_gone settling 'waiting') is
                    # silent by design. Without this the only trace is a kernel stderr line nobody reads.
                    self.backend._record_launch_error(self, e)
                raise
            if self.ended or not self._reconnect:
                break        # drain ended on its own (process exit) or we're shutting down → done

    def _rewind_failed(self, exc):
        """The CLI refused a rewind connect. Drop the one-shot flag (never re-offer a target the CLI just
        refused), pull the held edit turn off the queue, and surface both a warn toast (with the message
        text, so nothing is silently lost) and a kernel-log line. The session then reconnects plainly.
        A BARE rollback (delete) enqueued nothing — the queue's head, if any, is an unrelated held
        message (postal etc.) that must survive and ride the un-rewound branch."""
        bare = self._rewind_bare
        self._rewind_to = self._rewind_leaf = ""
        self._rewind_bare = False
        self._rewind_armed = False
        dropped = None
        if not bare:
            with self._lock:
                dropped = self._pending.pop(0) if self._pending else None
            self._persist_queue()
        self._rewind_wait = False
        try:
            self.backend._update_reg(self.sid, rewindTo="", rewindLeaf="", rewindBare=False,
                                     rewindWait=False)
        except Exception as e:
            self.backend._log("rewind (%s): registry clear failed: %s" % (self.name, e))
        self.backend._log("rewind (%s): the CLI refused --resume-session-at (%s: %s) — flag dropped, "
                          "%s" % (self.name, type(exc).__name__, exc,
                                  "the rollback did not happen" if bare else "edited message returned to the user"))
        try:
            self.backend._notify("chat", {"type": "warn", "text":
                ("the rollback failed (the session's CLI refused it) — the conversation is unchanged" if bare else
                 "the rewind failed (the session's CLI refused it) — your edited message was NOT sent%s"
                 % ((": " + dropped) if dropped else ""))})
        except Exception as e:
            self.backend._log("rewind (%s): could not tell the chat the rewind failed: %s" % (self.name, e))
        # the conversation is unchanged → the kernel RESTORES the cards its gesture-time hold hid
        self.backend._rewind_resolved(self.sid, "failed")
        self.backend._poke()

    def _learn_model(self, pm):
        """Record a freshly-observed display model (from the init message or an assistant turn). Updates the
        live value AND persists it to the registry as `liveModel`, so a DORMANT / post-restart session still
        shows its model via live_sessions' registry path — the registry's `model` field is the user's CHOSEN
        alias, which is absent for a default-model session, so without this the badge (and, on the timeline,
        the effort too) goes blank whenever the session isn't actively running (the user 2026-06-24). Pokes a
        push so the badge updates promptly. No-op when unchanged, so it doesn't rewrite the reg every turn.
        Also resolves a pending /model switch: once the observed name reflects the chosen alias, the
        switching-dots clear (the user 2026-07-03)."""
        if not pm:
            return
        cleared = self._resolve_model_pending(pm)
        if pm == self.model:
            if cleared:
                self.backend._poke()
            return
        if self.model and not self._model_pending and not cleared \
                and _model_downgrade(self.model, pm):
            # A DOWN-TIER transition nobody asked for (no /model pick pending): the API fell back
            # mid-turn. Surface it as a completed card via the kernel-wired hook (the user
            # 2026-08-23) — never silently (the swap was invisible before this). DOWNGRADES ONLY
            # (the user 2026-08-23, from a card reading "fallback" on their own upgrade): romp only
            # sees ITS OWN picks pending — a /model typed inside the CLI, or any user-side switch,
            # arrives here as an unrequested transition too, and a capacity fallback never moves a
            # session UP-tier. An up-tier, lateral, or unknown-name change is treated as the user's
            # doing and just updates the badge, exactly as before the card existed.
            fb = getattr(type(self.backend), "on_model_fallback", None)
            if fb:
                try:
                    fb(self.sid, self.model, pm)
                except Exception as e:
                    self.backend._log("model-fallback card (%s): %s" % (self.name, e), problem=True)
        self.model = pm
        try:
            self.backend._update_reg(self.sid, liveModel=pm, modelPending=bool(self._model_pending))
        except Exception as e:
            self.backend._log("model learn (%s): registry write failed: %s" % (self.name, e))
        self.backend._poke()

    def _resolve_model_pending(self, pm) -> bool:
        """If a /model switch is pending and the observed live name `pm` now reflects the chosen alias,
        clear the pending marker (badge stops showing dots) and persist it. Returns True if it cleared."""
        if not self._model_pending or not _model_reflects_alias(pm, self._model_pending):
            return False
        self._model_pending = ""
        try:
            self.backend._update_reg(self.sid, modelPending=False)
        except Exception as e:
            self.backend._log("model-pending clear (%s): registry write failed: %s" % (self.name, e))
        return True

    def _ctx_pct(self):
        """Current context-window fill %, as the SDK reports it via get_context_usage() — the same number the
        CLI's `/context` shows (it already divides by the real window and accounts for the autocompact buffer;
        we no longer guess the window). Refreshed on connect/init and after every turn by _do_refresh_context.
        None until the first refresh lands."""
        return self._ctx

    def _on_cli_stderr(self, line: str) -> None:
        """One line off the CLI's stderr. Registering this at all is the point: the SDK transport pipes
        the child's stderr ONLY when options.stderr is set, and otherwise drops it and reports the
        literal SDK_STDERR_PLACEHOLDER instead — so the CLI's own explanation of why it refused to start
        was thrown away before anything could show it.

        Buffered, not logged per line: a healthy CLI writes plenty of stderr nobody needs, and a running
        session would drown the kernel log. The tail is drained where it matters — _record_launch_error
        logs it and puts it on the session's error card.

        Called from the SDK's stderr reader task; it isolates exceptions per line, but keep it total
        anyway (a raise here would lose the very diagnostics this exists to keep)."""
        try:
            if line and line.strip():
                self._stderr_tail.append(line.rstrip("\n"))
        except Exception:
            pass

    def stderr_tail(self) -> str:
        """What the CLI last wrote to stderr, newest-last, as one block ('' if it wrote nothing)."""
        try:
            return "\n".join(self._stderr_tail)
        except Exception:
            return ""

    def _mark(self, state: str) -> None:
        """Persist a lifecycle STATE to states/<sid>.jsonl AND track whether the CLI is producing.
        Every session-lifecycle state write goes through here so `_cli_working` stays truthful: the live
        stream (_forward) re-asserts 'working' off this flag if a write ever falls behind real output —
        the event-based replacement for the feed-vs-result COUNTER that could strand the signal 'working'
        forever when the two diverged (mid-turn forwards, a dropped result)."""
        self._cli_working = (state == "working")
        append_state(self.backend.state_dir, self.sid, state)

    def _on_message(self, msg, AssistantMessage, ResultMessage, SystemMessage):
        if getattr(self, "_ping_feeding", False):   # getattr: __new__-built test doubles skip __init__
            # the ping's turn is streaming — the CLI demonstrably started it, so a message fed from
            # here on lands MID-TURN as its own record (the CLI's designed forward behavior); the
            # queue can flow again
            self._ping_feeding = False
            if self._input_wake is not None:
                self._input_wake.set()   # same-loop thread — the settle path sets it the same way
        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            self._fire_boot_settled()   # the CLI is up and streaming — its transcript catch-up burst
            #                             is over, so the boot-stagger slot (if any) frees NOW
            d = msg.data if isinstance(msg.data, dict) else {}
            self._learn_model(pretty_model(d.get("model")))
            self.perm_mode = d.get("permissionMode") or self.perm_mode
            # Fast-mode truth rides the init payload — the AUTHORITATIVE re-assert behind set_fast's
            # optimistic flip, shared with the connect-time initialize response (_adopt_fast_state).
            self._adopt_fast_state(d)
            # HOW this CLI authenticates (verified live 2026-08-04: 'ANTHROPIC_API_KEY' on API-key auth;
            # the field is absent on a subscription login). An auth flip is the deciding event for the
            # rail's /usage bars — see _note_auth_source.
            self.backend._note_auth_source(self, d.get("apiKeySource"))
            fsid = d.get("session_id")
            if fsid and fsid != self.resume_sid:
                old = self.resume_sid
                self.resume_sid = fsid
                self.backend._update_reg(self.sid, lastSid=fsid)
                # A lastSid flip IS a fork landing — for a /clear, the fresh conversation now exists, so the
                # clearing bracket ends here (event-based; the ResultMessage below is only the backstop).
                clearing = self._clearing
                self._clearing = False
                # A RESUME landing on a NEW fsid = a fresh-headed fork: record the old->new lineage
                # (see append_resume_fork for the full story — the parser stitches the chain from it,
                # the user 2026-08-14). A /clear's flip and a born-as-a-fork copy record nothing.
                if old and not clearing and not self._fork_of:
                    append_resume_fork(self.backend.state_dir, self.sid, old, fsid)
            if self._fork_of and fsid == self.sid:
                # The BORN-AS-A-FORK session's copy landed (the CLI now owns a transcript pinned to this
                # sid). Spend the fork flags: a later reconnect must resume the fork's OWN conversation
                # plainly — re-applying fork_session would copy it into yet another session.
                self._fork_of = self._fork_at = ""
                self.backend._update_reg(self.sid, forkOf="", forkAt="")
                reg_now = read_reg(self.backend.state_dir, self.sid) or {}
                _ff = reg_now.get("forkedFrom") or {}
                self.backend._log("fork spent (%s): src %.1fMB, create->spend %ds"
                          % (self.sid[:8], (reg_now.get("forkSrcBytes") or 0) / 1048576.0,
                             max(0, int(time.time()) - int(_ff.get("t") or time.time()))))
            cli_cwd = d.get("cwd")
            if isinstance(cli_cwd, str) and cli_cwd and cli_cwd != self.cwd:
                # The CLI's own cwd is the AUTHORITATIVE string: its projects-dir/transcript encoding
                # is keyed on it (getcwd returns true on-disk casing), while romp's transcript_path is
                # keyed on the registry string. A create-time variant (case, symlink) launches a real
                # session whose transcript discovery then never finds (the silent wrong-case launch, the
                # user 2026-07-17) — adopt the CLI's string the moment init reports it.
                self.backend._log("sdk %s: adopting CLI cwd %r (registry had %r)" % (self.sid[:8], cli_cwd, self.cwd))
                self.cwd = cli_cwd
                self.backend._update_reg(self.sid, cwd=cli_cwd)
            self.backend._poke()   # publish the model + permission-mode from init promptly: the snapshot reads
                                   # self.model, but with no poke the new model would wait out the 3s producer
                                   # backstop. NB: this init branch fires only once the FIRST turn arrives — the
                                   # CLI emits no init message on a turn-less connect — so the PRE-turn publish
                                   # is _amain's on-connect _do_refresh_context() (get_context_usage); this is
                                   # the refinement once a real turn lands.
            asyncio.ensure_future(self._do_refresh_context())   # re-pull the real context % + model from the SDK
        elif isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
            self._compacting = False   # a real compaction LANDED → done; the CLI's continuation is normal work
            # Compaction just landed: the active context dropped to the summary. Re-pull the % NOW, on the
            # boundary event itself, rather than waiting for the next turn's ResultMessage — the CLI auto-runs
            # a continuation turn after /compact that can work for minutes, and until it settled the bar kept
            # showing the STALE pre-compaction % (the user 2026-06-30, who compacted but it still said 72%).
            # get_context_usage() reads current state, so it reports the post-compaction number here.
            asyncio.ensure_future(self._do_refresh_context())
        elif isinstance(msg, SystemMessage) and msg.subtype == "api_retry":
            # the API returned a retryable error (rate-limit / overload); the CLI is backing off + retrying.
            # Surface a distinct 'retrying' state so a stall reads as an API issue, not a silent hang (the
            # user 2026-06-23). Cleared the moment real output flows again (assistant text / result).
            self.retrying = True
            self.retry_count += 1                      # one backoff attempt → the live 'attempt N' count
            # The event's own detail — attempt number/budget, the error behind the backoff, and when the
            # next attempt fires — so the chat's retrying element can say WHAT is failing and what happens
            # next, not just that a storm exists (the user 2026-07-10).
            #
            # The field names were GUESSED when this was written (number / max_retries / retry_delay_ms /
            # error_status / retryAt) and every one of them was wrong, so `retry_info` came back all-None on
            # every real storm and the whole detail UI below it rendered blank — the user saw a bare "API
            # retrying" with no attempt count, no countdown and no reason, for months (the user 2026-07-29).
            # The names are now VERIFIED against two authoritative sources rather than guessed:
            #   * the WIRE frame (SDKAPIRetryMessage, subtype api_retry) — snake_case, per the CLI's own
            #     embedded schema: retry_in_ms / is_network_down / is_ssl_error / rate_limit_type;
            #   * the TRANSCRIPT twin the same frame is written from (system / subtype api_error) — camelCase:
            #     retryAttempt / maxRetries / retryInMs / error{status,formatted,requestId,isNetworkDown,
            #     rateLimits}.
            # We accept BOTH spellings (plus the old guesses) because the two surfaces genuinely differ and
            # either may reach us; `error` arrives as a dict on the transcript side and a string on the wire.
            d = msg.data if isinstance(msg.data, dict) else {}
            _now = time.time()

            def _pick(*names, want=(int, float, str)):
                """First present, correctly-typed value among alternate spellings of one field."""
                for nm in names:
                    v = d.get(nm)
                    if isinstance(v, want) and not isinstance(v, bool):
                        return v
                return None

            _e = d.get("error") if isinstance(d.get("error"), dict) else {}
            _ra = _pick("retryAt", "retry_at")
            _in_ms = _pick("retry_in_ms", "retryInMs", "retry_delay_ms")
            retry_at = (_ra / 1000.0 if isinstance(_ra, (int, float)) and _ra > 1e12 else
                        _ra if isinstance(_ra, (int, float)) and _ra > 1e9 else
                        _now + _in_ms / 1000.0 if isinstance(_in_ms, (int, float)) else None)
            # The human string: the transcript's error.formatted ("529 Overloaded") is the best of these;
            # error.message carries the raw JSON envelope, so it is the last resort.
            _err = (_e.get("formatted") or _e.get("message") if _e else None) \
                or _pick("display_message", "message", want=(str,))
            _status = _pick("status_code", "error_status", "status") \
                or (_e.get("status") if isinstance(_e.get("status"), (int, str)) else None)
            _net = d.get("is_network_down", d.get("isNetworkDown", _e.get("isNetworkDown")))
            self.retry_info = {
                "attempt": _pick("retry_attempt", "retryAttempt", "number", want=(int,)) or self.retry_count,
                "max": _pick("max_retries", "maxRetries", want=(int,)),
                "status": _status,
                "error": _err[:300] if isinstance(_err, str) else None,
                "retryAt": retry_at,
                # New detail the guessed reads never reached. requestId is what support/debugging actually
                # needs; networkDown separates "this machine fell off the internet" from "the API is busy",
                # which are opposite problems wearing the same red card; rateLimitType names WHICH quota a
                # 429 hit (null unless it is a quota 429, per the CLI's schema).
                "requestId": (_e.get("requestId") if _e else None) or _pick("request_id", want=(str,)),
                "networkDown": bool(_net) if isinstance(_net, bool) else None,
                "rateLimitType": _pick("rate_limit_type", want=(str,)),
            }
            # Fail LOUDLY on an unrecognised payload instead of quietly rendering an empty banner — the exact
            # failure mode above. If a future CLI renames these again we get a one-line diagnostic naming the
            # keys it actually sent, rather than months of silently blank detail (CLAUDE.md: authoritative
            # sources — a visible error beats data that looks fine and misleads). Once per process.
            if d and self.retry_info["max"] is None and self.retry_info["status"] is None \
                    and self.retry_info["error"] is None and not SdkSession._retry_shape_warned:
                SdkSession._retry_shape_warned = True
                sys.stderr.write(
                    "romp: api_retry payload has no field this build understands — keys=%r. The retry "
                    "detail (attempt/max, status, countdown) will be blank until these are mapped.\n"
                    % (sorted(d)[:20],))
            self._mark("retrying")
            self.backend._poke()
        elif isinstance(msg, SystemMessage) and msg.subtype in (
                "task_started", "task_progress", "task_updated", "task_notification"):
            # The CLI's DESIGNED background-task lifecycle (task_started → task_progress* →
            # task_updated/task_notification): the authoritative live set of what this session has running
            # in the background — a run_in_background Bash timer/watcher, a backgrounded agent. Duck-typed
            # off subtype+data (the typed subclasses need a newer SDK; the raw payload is identical).
            # Terminal statuses clear from EITHER message kind — a TaskStop can suppress the notification.
            self._on_task_event(msg.subtype, msg.data if isinstance(msg.data, dict) else {})
        elif isinstance(msg, AssistantMessage):
            # The CLI's FAILURE settle wears an AssistantMessage too: when a storm exhausts its retries it
            # writes the error itself as the reply text ("API Error: 529 Overloaded…") and stamps the
            # message with `error` (the SDK's designed flag — "server_error", "rate_limit", …; the same
            # stamp the transcript record carries as isApiErrorMessage). Treating THAT message as "real
            # output" recorded the storm as recovered — the chat's durable note read "Recovered after 10
            # retries" over a turn that produced nothing (the user 2026-07-25). A stamped-error message
            # ends the storm as a GIVE-UP: its own durable marker, never a recovery.
            if getattr(msg, "error", None):
                if self.retrying and self.retry_count:
                    append_retry_gave_up(self.backend.state_dir, self.sid, self.retry_count,
                                         kind=str(msg.error))
                # the installed CLI refusing a model by MINIMUM VERSION is worth remembering for every
                # picker (T222): the model catalog can list ids newer than the binary
                try:
                    note_cli_model_block(self.backend.state_dir, self.chosen_model or self.model,
                                         _assistant_text(msg))
                except Exception:
                    pass
            elif self.retrying and self.retry_count:   # first real output after a storm → durable recovery marker
                append_retry_recovered(self.backend.state_dir, self.sid, self.retry_count)
            if not getattr(msg, "error", None):
                # a real reply on a model proves the CLI serves it — its block (if any) lifts on the event
                try:
                    for _mid in {str(getattr(msg, "model", None) or ""), str(self.chosen_model or "")}:
                        if "claude" in _mid.lower():
                            clear_cli_model_block(self.backend.state_dir, _mid)
                except Exception:
                    pass
            self.retrying = False                      # either way the storm is over (recovered, or settled in error)
            self.retry_count = 0
            self.retry_info = None
            m = getattr(msg, "model", None)
            # SIDECHAIN turns never teach the session its model (the user 2026-08-24): a Task/Agent
            # subagent streams its OWN AssistantMessages tagged parent_tool_use_id, routinely on a
            # LOWER tier than the parent — learning one filed a false "Model changed automatically"
            # downgrade card (the subagent's model read as a capacity fallback) and wrote the
            # subagent's model over the registry's liveModel, which the statusline badge reads,
            # until _do_refresh_context healed it after the turn. The same drop the chat-atom path
            # applies to sidechain traffic (msg_to_atom) — the parent stream teaches only the
            # parent's own turns.
            if getattr(msg, "parent_tool_use_id", None):
                m = None
            # Only adopt a REAL model id. Injected / synthetic assistant turns carry model="<synthetic>" (and
            # the CLI writes it to the transcript too); pretty_model passes unrecognised ids through verbatim,
            # so an unguarded assign would CORRUPT the model badge to "<synthetic>". A real id always contains
            # "claude" (claude-opus-4-8, us.anthropic.claude-…); keep the last good one otherwise.
            if m and "claude" in m.lower():
                self._learn_model(pretty_model(m))
        elif isinstance(msg, ResultMessage):
            # total_cost_usd is CUMULATIVE per CLI process (the result event's totalCostUSD counter, beside
            # total_duration/lines) — fold only THIS turn's delta, or every result re-adds the whole
            # session-so-far cost and the spend readout compounds into fiction (the user 2026-08-08). A
            # total below the last seen means a counter we didn't watch reset — fold it whole, never negative.
            total = getattr(msg, "total_cost_usd", None)
            if isinstance(total, (int, float)) and total > 0:
                delta = total - self._last_cost_total if total >= self._last_cost_total else total
                self._last_cost_total = float(total)
                # the usage dict is the SAME kind of counter (`usage: this.totalUsage` in the bundle):
                # per-field deltas, a shrunken field folding whole — see _last_usage_totals in __init__
                u = getattr(msg, "usage", None)
                u = u if isinstance(u, dict) else {}
                turn_u = {}
                for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                    v = u.get(k)
                    v = int(v) if isinstance(v, (int, float)) else 0
                    last = self._last_usage_totals.get(k, 0)
                    turn_u[k] = v - last if v >= last else v
                    self._last_usage_totals[k] = v
                self.backend._record_spend(delta, turn_u, keyed=self.api_key_auth,
                                           sid=self.thread_of or self.sid)   # the rail's spend —
                #   a comment THREAD bills its owning session (T144: whole-session truth for the
                #   rail and the optimizer; a deliberate fork has no threadOf and bills itself)
                #   + token readout; keyed = THIS session's init-reported auth, so the API sum stays
                #   honest on a mixed host (see _record_spend)
            self.retrying = False
            self.retry_count = 0                    # turn over → clear the storm count (a turn that errored out without recovering leaves no "recovered" note)
            self.retry_info = None
            self._interrupted = False              # this turn's result settled it (whether it finished or was interrupted)
            self._intr_level = 0                   # settle ends the escalation episode — the next stop starts polite
            # A ResultMessage is the AUTHORITATIVE turn-end: the CLI has processed everything we handed it
            # — one message or a stream of mid-turn forwards that folded into this turn — and is now idle.
            # So settle to idle in ONE step and UNCONDITIONALLY, never gated on a feed-vs-result count.
            # (The old `inflight -= 1; if inflight == 0:` guard stranded the session 'working' forever
            # whenever the two diverged: each mid-turn forward did inflight += 1 but the CLI emits ONE
            # Result for the merged turn, so inflight never returned to 0 and the settle never ran — the
            # phantom-working bug, the user 2026-07-09.) If a genuinely separate turn is still queued in
            # the CLI, its next streamed atom re-asserts 'working' via _forward — the stream is the truth.
            self.inflight = 0
            self._inflight_texts.clear()           # the CLI processed everything fed — same settle semantics
            # A /compact that found NOTHING to compact emits no boundary — the turn just settles here. Clear
            # the authoritative flag so parked ops proceed immediately, instead of waiting out a 180s cap.
            self._compacting = False
            self._clearing = False   # /clear backstop: the turn settled, whatever the init did or didn't flip
            if self._rewind_to and getattr(self, "_rewind_wait", False):
                # delete-while-busy: THIS settle is the interrupted turn ending — the flag is being
                # ARMED here, not consumed. Second observer of the turn-end fact (the Stop hook is
                # the first; _complete_rewind_wait is idempotent, first one wins) — it exists for
                # the turn shapes where Stop never fires (an interrupt that dies straight to the
                # ResultMessage).
                try:
                    self.backend._complete_rewind_wait(self)
                except Exception as e:
                    self.backend._log("rewind (%s): delete-while-busy completion failed at the "
                                      "settle: %s" % (self.name, e))
            elif self._rewind_to and self._rewind_armed:
                # the rewind turn settled — the flag is CONSUMED (the leaf moved past the recorded one, so
                # rewind_disposition would drop it on the next connect anyway; this just tidies the reg now).
                # A bare rollback consumes here too: the settled turn IS the branch's first (leaf moved).
                # ARMED is part of the guard (delete-while-busy): the interrupted turn's own settle
                # lands moments after the Stop hook armed the flag, and an unguarded consume read
                # that fresh arm as the branch-take — flags could never accompany an UNARMED
                # running turn before, so armed-only is byte-identical for the idle paths.
                self._rewind_to = self._rewind_leaf = ""
                self._rewind_bare = False
                self._rewind_armed = False
                self._rewind_wait = False
                try:
                    self.backend._update_reg(self.sid, rewindTo="", rewindLeaf="", rewindBare=False,
                                             rewindWait=False)
                except Exception as e:
                    self.backend._log("rewind (%s): registry clear failed after the turn landed: %s" % (self.name, e))
                # the BRANCH-TAKE event: the settled turn is the new branch's first landed record —
                # the kernel's held goal cleanup archives on exactly this (two-phase rewind timing)
                self.backend._rewind_resolved(self.sid, "taken")
            self.backend._turn_completed(self.sid)   # a landed result re-arms the crash-resume budget
            self._mark("waiting")
            self.backend.retire_live_work(self.sid)   # turn over → a work atom that never landed never will
            asyncio.ensure_future(self._do_refresh_context())   # refresh ctx % + model from the SDK and
            #   persist them, so the bar reflects the turn that just landed and survives idle/restart.
            asyncio.ensure_future(self._do_refresh_usage())     # + the exact /usage snapshot (rail bars)
            self.backend._poke()
            if self._input_wake is not None:   # turn done → release the next queued turn, if any
                self._input_wake.set()
            # a pending rename ping delivers HERE, as its own turn (the user answered first; the
            # empty-queue guard + the feed-hold make its record unfoldable — see _deliver_rename_ping)
            self.backend._deliver_rename_ping(self)
            if self._reconnect_when_idle and not self.ended:   # an effort change waited for this turn to end
                self._reconnect_when_idle = False
                self._reconnect = True
                self._wake_set()
        elif getattr(msg, "rate_limit_info", None) is not None:
            # A RateLimitEvent: the account-wide /usage limits (5h + weekly) the CLI streams when the limit state
            # changes — the SDK's designed source for the rail usage bars. Duck-typed (no SDK-type import needed).
            # (The 2026-07-01 TEMPORARY cadence instrumentation lived here; its 19h/452-event answer — events
            # arrive ~per-API-call but carry utilization ONLY in the allowed_warning band — is baked into
            # _record_rate_limit's status-aware merge, so the jsonl capture is gone.)
            # PER-SESSION auth gate (the user 2026-08-08): an API-keyed session's events describe the KEY's
            # limits, not the login's subscription windows — writing them into usage.json contaminated the
            # login's bars with another allowance's numbers.
            if not self.api_key_auth:
                self.backend._record_rate_limit(msg.rate_limit_info)
        # Forward the raw message to the kernel for live chat/event use.
        self.backend._forward(self, msg)

    # ---- the permission/AskUserQuestion callback (the headless-parity piece) ----

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        if tool_name == "AskUserQuestion":
            try:
                answers = await self._ask_user(tool_input)
            except _AskCancelled:
                return PermissionResultDeny(behavior="deny",
                                            message="User cancelled the question.", interrupt=False)
            return PermissionResultAllow(
                behavior="allow",
                updated_input={"questions": tool_input.get("questions", []), "answers": answers})
        if tool_name == "ExitPlanMode":
            return await self._approve_plan(tool_input)
        # Ordinary tool permission. FIRST, the declared-intent guard (T139, the exp specimen): the
        # SDK's own contract says bypassPermissions auto-approves every call before this callback
        # is consulted (explicit deny rules aside) — yet CLI 2.1.221 consulted it once for a plain
        # main-thread Bash, 30s after an equivalent command sailed through, and the session sat
        # BLOCKED eleven hours on an ask the user had declared they never wanted. romp is the
        # registered permission authority, so it re-imposes the declared intent: under bypass,
        # auto-allow and ring the problems (the CLI broke its contract — visible, never silent),
        # and never block a bypass session on an ask. Other modes keep asking: when the CLI
        # consults under default/plan/acceptEdits, an ask IS the honest behavior for whatever its
        # own evaluation could not auto-decide.
        if self.perm_mode == "bypassPermissions":
            self.backend._log("permission consult under bypassPermissions (%s, tool %s) — the CLI's "
                              "contract says this cannot happen; auto-allowed per the declared mode"
                              % (self.name, tool_name), problem=True)
            return PermissionResultAllow(behavior="allow")
        # Options are Allow (1), optionally Allow-&-don't-ask-again (2 when the
        # SDK supplied permission suggestions), then Deny (last). _next_ask_action returns the chosen
        # ordinal so we map it back to the action here.
        ask = permission_to_live(tool_name, tool_input, context)
        remember_n = 2 if getattr(context, "suggestions", None) else None
        self._mark("permission")
        self.backend._emit_ask(self, ask)
        decision = "deny"
        try:
            while True:
                kind, payload = await self._next_ask_action()
                if kind == "answer":
                    n = str(payload)
                    decision = "allow" if n == "1" else "remember" if n == str(remember_n) else "deny"
                    break
                if kind == "cancel":
                    decision = "deny"
                    break
        finally:
            self.backend._clear_ask(self)
            if self.inflight:
                self._mark("working")
        if decision == "remember":
            return PermissionResultAllow(behavior="allow", updated_permissions=list(context.suggestions))
        if decision == "allow":
            return PermissionResultAllow(behavior="allow")
        return PermissionResultDeny(behavior="deny", message="Denied by user.", interrupt=False)

    async def _approve_plan(self, tool_input: dict):
        """Plan-mode approval (ExitPlanMode): show the PLAN itself, not a bare 'Allow ExitPlanMode?'.
        Options: proceed (exit plan mode), proceed + auto-accept edits (also flip the session into
        acceptEdits via a setMode permission update), or keep planning (deny → stay in plan mode).
        (the user 2026-06-27.)"""
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, PermissionUpdate
        pv = tool_preview("ExitPlanMode", tool_input)
        ask = {
            "kind": "single", "header": "Plan ready",
            "question": "Proceed with this plan?",
            "options": [
                {"n": 1, "label": "Yes, proceed", "desc": "Exit plan mode and start", "selected": False},
                {"n": 2, "label": "Yes, auto-accept edits",
                 "desc": "Proceed and don't prompt for each edit", "selected": False},
                {"n": 3, "label": "No, keep planning", "desc": "Stay in plan mode", "selected": False},
            ],
            "multiSelect": False, "cursor": 0, "cursorFound": True, "permission": True,
        }
        if pv:
            ask["previewKind"], ask["preview"] = pv[0], pv[1]
        self._mark("permission")
        self.backend._emit_ask(self, ask)
        choice = "3"
        try:
            while True:
                kind, payload = await self._next_ask_action()
                if kind == "answer":
                    choice = str(payload); break
                if kind == "cancel":
                    choice = "3"; break
        finally:
            self.backend._clear_ask(self)
            if self.inflight:
                self._mark("working")
        if choice == "1":
            return PermissionResultAllow(behavior="allow")
        if choice == "2":
            return PermissionResultAllow(behavior="allow",
                                         updated_permissions=[PermissionUpdate(type="setMode", mode="acceptEdits")])
        return PermissionResultDeny(behavior="deny", message="Keep planning.", interrupt=False)

    async def _ask_user(self, tool_input: dict) -> dict:
        """Drive the picker UI for each question (sequentially for multi-question),
        returning the AskUserQuestion `answers` mapping."""
        questions = tool_input.get("questions") or []
        picks: dict[int, object] = {}
        self._mark("picker")
        try:
            for qi, q in enumerate(questions):
                picks[qi] = await self._ask_one(q, qi, len(questions))
        finally:
            self.backend._clear_ask(self)
            if self.inflight:
                self._mark("working")
        return build_answers(questions, picks)

    async def _ask_one(self, question: dict, qi: int, total: int):
        multi = bool(question.get("multiSelect"))
        nreal = len(question.get("options") or [])
        selected: set[int] = set()
        customs: list[str] = []                       # free-text answers the user typed (multi accumulates)
        def emit():
            self.backend._emit_ask(self, ask_question_to_live(question, qi, total, selected, customs))
        emit()
        while True:
            kind, payload = await self._next_ask_action()
            if kind == "cancel":
                raise _AskCancelled()
            if kind in ("custom", "text") and payload:
                if not multi:
                    return str(payload)               # single-select: the typed answer IS the answer
                customs.append(str(payload)); emit(); continue   # multi: add it, keep going until Submit
            if not multi and kind == "answer":
                return label_for_target(question, payload)
            if multi and kind == "toggle":
                try:
                    n = int(payload)
                except (TypeError, ValueError):
                    n = -1
                if 1 <= n <= nreal:
                    selected.discard(n) if n in selected else selected.add(n)
                elif nreal < n <= nreal + len(customs):   # unchecking a typed custom row removes it
                    del customs[n - nreal - 1]
                emit(); continue
            if multi and kind == "submit":
                return [label_for_target(question, n) for n in sorted(selected)] + customs
            # single-select that received a toggle, or vice versa: re-emit.
            emit()

    # ---- the awaiting overlay (bugz's event-model overlay) ----

    async def _stop_hook(self, inp, tool_use_id, context):
        """At turn-end, CLEAR the awaiting overlay (awaiting:false). Background SHELL tasks don't ride
        this overlay: they were excluded from awaiting entirely on 2026-07-07 (a leftover dev server /
        `tail -f` pinned an idle session to a working flavor off a lossy transcript scrape), and when the
        user reversed that on 2026-07-11 (nimbus's 20-minute campaign timer deserved an 'awaiting' read)
        the signal came from the CLI's DESIGNED task lifecycle stream instead — the live _bg_tasks set
        (see _on_task_event), terminal-status-cleared, no overlay records needed. So this hook still
        ignores inp['background_tasks'] and just clears any stale awaiting:true — keeping the overlay
        channel available for signals that need durability across a backend restart."""
        append_awaiting(self.backend.state_dir, self.sid, False)
        # Record the ARMED TIMER SET (the hook payload's session_crons: CronCreate crons, ScheduleWakeup
        # wakeups, /loop ticks). Session-scoped timers live ONLY in the CLI process's memory — the tool
        # result itself says "dies when Claude exits" — so a session romp leaves DORMANT has no process
        # at the due moment and the timer silently never fires (the user 2026-08-28: wakeups armed for
        # 26 minutes out missed across 21 hours; agents built crontab workarounds). This record is what
        # lets the kernel keep timer-armed sessions CONNECTED (its scheduled-session sweep) so the CLI's
        # own scheduler — which fires fine on a live idle process — does the firing. Written on every
        # turn end, including [] when the last timer is cancelled, so the reg never pins a stale set.
        try:
            crons = inp.get("session_crons") if isinstance(inp, dict) else None
            if isinstance(crons, list):
                prev = read_reg_for_rmw(self.backend.state_dir, self.sid)
                if prev is None:
                    # the except below logs it — skip, never wipe the armed set (field-gutting class)
                    raise OSError("reg unreadable — session_crons record skipped rather than wiping the armed set")
                nw = time.time()
                known = {c.get("id"): c for c in (prev.get("sessionCrons") or []) if isinstance(c, dict)}
                # Adoption pool for the CALL-TIME records (_sched_tool_hook): a toolhook entry knows the
                # EXACT dueEpoch but minted its own id; the payload entry carries the CLI's id but no
                # date. Marry them by prompt + near-due so the reg ends with the CLI's id AND the exact
                # date, and the toolhook duplicate drops.
                toolhook = [c for c in (prev.get("sessionCrons") or [])
                            if isinstance(c, dict) and c.get("src") == "toolhook"]

                def adopt(c):
                    p = str(c.get("prompt") or "")[:500]
                    hint = wakeup_due_epoch(str(c.get("schedule") or c.get("cron") or ""),
                                            float((known.get(str(c.get("id") or "")) or {}).get("armedAt") or nw))
                    for t in toolhook:
                        if t.get("prompt") == p and (hint is None or abs(float(t.get("dueEpoch") or 0) - hint) < 90):
                            return t
                    return None

                slim = []
                for c in crons:
                    if not isinstance(c, dict):
                        continue
                    src = adopt(c) or known.get(str(c.get("id") or "")) or {}
                    slim.append({"id": str(c.get("id") or ""), "cron": str(c.get("schedule") or c.get("cron") or ""),
                                 "prompt": str(c.get("prompt") or "")[:500], "kind": str(c.get("kind") or ""),
                                 "recurring": bool(c.get("recurring")),
                                 # armedAt/dueEpoch/procGen ride each ENTRY from its first record (the
                                 # call-time hook when it saw the arm; else first-seen here), so a
                                 # one-shot's due moment never shifts when later turns rewrite the set
                                 "armedAt": float(src.get("armedAt") or nw),
                                 "dueEpoch": (float(src["dueEpoch"]) if src.get("dueEpoch") else None),
                                 "procGen": str(src.get("procGen") or self.proc_gen)})
                # MERGE, don't overwrite: a recycled process LOSES ScheduleWakeup one-shots (verified
                # live 2026-08-28), so the fresh process's payload lacks them — and blindly writing the
                # payload would erase the very record deliver_lost_wakeups needs. An absent one-shot
                # survives while ITS PROCESS GENERATION is gone (the CLI that could have fired it no
                # longer exists, so absence from a NEW process's payload is not evidence it fired) —
                # ownership is exact, no due-time guessing. A one-shot recorded by the LIVE generation
                # and absent from its own payload genuinely fired or was cancelled: it drops.
                have = {c.get("id") for c in slim}
                adopted = {id(t) for c in crons if isinstance(c, dict) for t in [adopt(c)] if t is not None}
                for c in (prev.get("sessionCrons") or []):
                    if (isinstance(c, dict) and not c.get("recurring") and c.get("id") not in have
                            and id(c) not in adopted
                            and str(c.get("procGen") or "") != self.proc_gen):
                        slim.append(c)
                if slim != prev.get("sessionCrons"):
                    self.backend._update_reg(self.sid, sessionCrons=slim, sessionCronsAt=int(nw))
        except Exception as e:
            self.backend._log("stop hook (%s): session_crons record failed: %s" % (self.name, e))
        # Reconcile the LAUNCH LEDGER against the payload's background_tasks — the per-turn snapshot
        # this hook used to discard (2026-08-29). The ledger is launch-authoritative; the payload is
        # the CLI's own answer to "what is still running", so: a live-generation ledger entry absent
        # from the payload ENDED (tombstone, why:"gone"); a DEAD-generation entry died with its
        # process (background tasks are children of the CLI); a payload task the ledger never saw is
        # adopted (src:"stopReconcile" — the hook missed a shape, or the entry predates the ledger).
        try:
            bg = inp.get("background_tasks") if isinstance(inp, dict) else None
            if isinstance(bg, list):
                nw = int(time.time())
                lr = self._ledger_read()
                if lr is None:
                    raise OSError("reg unreadable — reconcile skipped")   # the except below logs it
                live, ended = lr
                running = {str(t.get("id")): t for t in bg
                           if isinstance(t, dict) and t.get("status") == "running" and t.get("id")}
                kept = []
                for e in live:
                    tid = str(e.get("tid") or "")
                    if tid and tid in running:
                        kept.append(e)
                    elif str(e.get("procGen") or "") != self.proc_gen:
                        ended.append({"tid": e.get("tid"), "why": "processDied", "at": nw})
                    elif tid and e.get("tool") == "bash":
                        # only SHELLS are proven to ride the payload (probe 2026-08-28) — a monitor
                        # absent from it may simply be uncovered, and a false "gone" would silently
                        # un-wait the session; monitors end by deadline, fail, stop, or processDied
                        ended.append({"tid": e.get("tid"), "why": "gone", "at": nw})
                    else:
                        kept.append(e)   # no id / unproven coverage — the reconciler cannot rule on it
                have = {str(e.get("tid")) for e in kept}
                for tid, t in running.items():
                    if tid not in have:
                        kept.append({"tid": tid, "toolUseId": None, "tool": str(t.get("type") or "shell"),
                                     "desc": str(t.get("description") or "")[:300], "armedAt": nw,
                                     "deadlineEpoch": None, "persistent": False,
                                     "procGen": self.proc_gen, "agentId": None, "src": "stopReconcile"})
                lr0 = self._ledger_read()
                if lr0 is not None and (kept != lr0[0] or ended != lr0[1]):
                    self._ledger_write(kept, ended)
        except Exception as e:
            self.backend._log("stop hook (%s): bg-ledger reconcile failed: %s" % (self.name, e))
        # The turn-end EVENT itself, durable and kernel-readable (2026-08-29, for the nudge layer's
        # memo re-arm and any consumer that needs "this session settled a turn at T" as a fact
        # rather than an inference from transcript mtimes — romp_cards' round keys on it).
        try:
            self.backend._update_reg(self.sid, lastStopAt=int(time.time()))
        except Exception:
            pass
        # delete-while-busy: the turn this delete interrupted has ENDED — complete the arm here,
        # on the same turn-end fact that stamps lastStopAt (never a sleep)
        if getattr(self, "_rewind_wait", False):
            try:
                self.backend._complete_rewind_wait(self)
            except Exception as e:
                self.backend._log("rewind (%s): delete-while-busy completion failed: %s" % (self.name, e))
        self.backend._poke()
        return {}

    async def _sched_tool_hook(self, inp, tool_use_id, context):
        """PostToolUse on the scheduling tools (ScheduleWakeup / CronCreate / CronDelete): record the
        arm at the CALL moment with an ABSOLUTE due time, instead of waiting for the turn's Stop hook.
        End-of-turn-only recording had three holes (review of #769, 2026-08-28): armedAt stamped at
        turn END wrapped a same-turn-past-due wakeup to TOMORROW (the cron string carries no date); a
        restart mid-turn lost the arm entirely (the Stop hook never ran); and reconstruction from a
        dateless "M H * * *" string is guesswork the tool INPUT makes unnecessary — delaySeconds is
        right there, so dueEpoch = now + delaySeconds is exact. Entries minted here carry
        src:"toolhook" and a synthetic id; the Stop hook's reconciler marries them to the CLI's own
        ids (see adopt() above) and remains the safety net if this hook ever misses a shape.
        Defensive throughout: an unrecognized payload records nothing and logs, never raises."""
        try:
            tname = str((inp or {}).get("tool_name") or "")
            targs = (inp or {}).get("tool_input") or {}
            if not isinstance(targs, dict):
                return {}
            nw = time.time()
            prev = read_reg_for_rmw(self.backend.state_dir, self.sid)
            if prev is None:
                self.backend._log("sched tool hook (%s): reg unreadable — skipping this record "
                                  "rather than wiping the armed set" % self.name)
                return {}
            cur = [c for c in (prev.get("sessionCrons") or []) if isinstance(c, dict)]
            if tname == "ScheduleWakeup":
                if targs.get("stop"):
                    # a dynamic loop ending: drop this session's pending toolhook one-shots — the CLI
                    # cancels its own pending wakeup on stop, and keeping ours would fabricate a wake
                    cur = [c for c in cur if not (c.get("src") == "toolhook" and not c.get("recurring"))]
                    self.backend._update_reg(self.sid, sessionCrons=cur, sessionCronsAt=int(nw))
                    return {}
                delay = targs.get("delaySeconds")
                prompt = str(targs.get("prompt") or "")[:500]
                if not isinstance(delay, (int, float)) or delay <= 0 or not prompt:
                    return {}
                due = nw + max(60.0, min(3600.0, float(delay)))   # the runtime clamps to [60, 3600]
                # ONE pending wakeup per session, matching ScheduleWakeup's own semantics (arming
                # replaces the previous pending wakeup): drop earlier toolhook one-shots first
                cur = [c for c in cur if not (c.get("src") == "toolhook" and not c.get("recurring"))]
                cur.append({"id": "toolhook-%d" % int(due), "cron": "", "prompt": prompt,
                            "kind": str(targs.get("reason") or "")[:120], "recurring": False,
                            "armedAt": nw, "dueEpoch": due, "procGen": self.proc_gen, "src": "toolhook"})
            elif tname == "CronCreate":
                nm = str(targs.get("name") or targs.get("id") or ("cron-%d" % int(nw)))
                cur = [c for c in cur if c.get("id") != nm]
                cur.append({"id": nm, "cron": str(targs.get("schedule") or ""),
                            "prompt": str(targs.get("prompt") or "")[:500], "kind": "cron",
                            "recurring": True, "armedAt": nw, "dueEpoch": None,
                            "procGen": self.proc_gen, "src": "toolhook"})
            elif tname == "CronDelete":
                nm = str(targs.get("name") or targs.get("id") or "")
                if not nm:
                    return {}
                cur = [c for c in cur if c.get("id") != nm]
            else:
                return {}
            self.backend._update_reg(self.sid, sessionCrons=cur, sessionCronsAt=int(nw))
        except Exception as e:
            self.backend._log("sched tool hook (%s): %s" % (self.name, e))
        return {}

    async def _prompt_submit_hook(self, inp, tool_use_id, context):
        """UserPromptSubmit: the RECURRING-CRON REPLAY GATE (T211, 2026-09-01). A resumed CLI
        catch-up-fires a recurring cron whose slot passed while its previous process was dead, so
        under immediate deploy restarts every kernel restart bought one spurious fire per recurring
        cron: revive (ensure_scheduled) → --resume → sub-second re-fire of an already-delivered
        slot. Field damage: one nightly fired ~21x on 2026-08-31, each extra fire within ~1s of a
        "reviving …" log line and preceded by the fresh process's own startup records — the CLI's
        last-fired memory dies with its process, and nothing durable said the slot already ran. This
        hook is that durable memory, at the only moment that counts — DELIVERY, never arm time: a
        prompt matching an armed recurring cron computes its slot (cron_prev_due) and consults the
        reg's cronDelivered map, keyed (cron_slot_key, slot epoch). Recorded → the fire is a replay:
        blocked before the model runs, loudly. Not recorded → record, then let it through — so a
        slot the dead process genuinely never delivered still fires exactly once after a restart
        (the CLI's own catch-up becomes the recovery instead of the bug). Every uncertain path fails
        OPEN (unreadable reg, unparseable schedule, hook error → the prompt runs): a duplicate fire
        costs a turn, a swallowed slot costs the schedule itself."""
        try:
            prompt = str((inp or {}).get("prompt") or "")
            if not prompt:
                return {}
            reg = read_reg_for_rmw(self.backend.state_dir, self.sid)
            if reg is None:
                self.backend._log("cron dedupe (%s): reg unreadable — prompt allowed rather than "
                                  "risking a swallowed schedule slot" % self.name, problem=True)
                return {}
            hits = [c for c in recurring_crons(reg)
                    if str(c.get("prompt") or "") == prompt[:500] and str(c.get("cron") or "").strip()]
            if not hits:
                return {}
            delivered = reg.get("cronDelivered")
            delivered = dict(delivered) if isinstance(delivered, dict) else {}
            now = time.time()
            record, replay_of = {}, None
            for c in hits:
                slot = cron_prev_due(str(c.get("cron")), now)
                if slot is None:
                    return {}                  # a shape we can't reason about exactly → stand down
                k = cron_slot_key(str(c.get("cron")), prompt[:500])
                if float(delivered.get(k) or 0) >= slot:
                    replay_of = slot           # this schedule's current slot already delivered
                else:
                    record[k] = slot
            if record:
                # Record AT the delivery moment; keys whose schedule left the armed set drop here
                # (natural GC — the map can never outgrow the armed set + this delivery).
                live = {cron_slot_key(str(c.get("cron")), str(c.get("prompt") or ""))
                        for c in recurring_crons(reg)}
                delivered = {k: v for k, v in delivered.items() if k in live}
                delivered.update(record)
                self.backend._update_reg(self.sid, cronDelivered=delivered)
                return {}
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(replay_of))
            self.backend._log("cron dedupe (%s): blocked a replayed schedule fire — its %s slot was "
                              "already delivered (a fresh process re-fires passed slots on resume)"
                              % (self.name, when), problem=False)
            return {"decision": "block",
                    "reason": "This scheduled prompt already ran for its %s slot — skipping the "
                              "duplicate." % when}
        except Exception as e:
            self.backend._log("cron dedupe (%s): %s — prompt allowed" % (self.name, e))
            return {}

    # ---- subagent tracking (the transparency tmux never had) ----

    # ── the background-launch LEDGER (2026-08-29, the hookable-tools round) ────────────────────
    # Launch facts recorded at the CALL moment from the tools' own inputs/acks, replacing the
    # transcript scrape as the durable source for SDK sessions (the scrape stays for tmux and
    # pre-ledger sessions — see kernel._bg_live_norm's source ladder). What the hook knows exactly
    # that every other channel guesses: the launch's tool_use_id and task id pairing (the ack's
    # backgroundTaskId, probe-verified 2026-08-28), Monitor's REQUIRED timeout_ms/persistent (an
    # exact watcher deadline — retires the +120s grace for ledger entries), the arming process
    # generation (launches die with their process), and the acting agent_id (subagent launches are
    # invisible to the transcript fold). Live set in the reg (bounded: running entries only) plus a
    # small tombstone tail (bgLedgerEnded) so "called off its own wait" and "died with its process"
    # are events the kernel can say, not guesses.
    # Bash-bg + Monitor ONLY (2026-08-29 review): their ack/input shapes are probe-verified
    # (backgroundTaskId; required timeout_ms/persistent). Task/Workflow launches ride the
    # lifecycle stream + the Subagent hooks — journaling them here recorded every FOREGROUND
    # subagent call as a phantom launch and flooded the tombstone tail.
    _LEDGER_TOOLS = ("Bash", "Monitor")
    _LEDGER_TOMBSTONES = 20
    _LEDGER_CAP = 40

    def _ledger_read(self):
        """(live, ended) — or None when the reg exists but would not read this instant: the
        caller must SKIP its write (writing rebuilt empties back wipes the ledger — the
        field-gutting class; see read_reg_for_rmw)."""
        reg = read_reg_for_rmw(self.backend.state_dir, self.sid)
        if reg is None:
            self.backend._log("bg ledger (%s): reg unreadable — skipping this pass rather than "
                              "wiping the ledger" % self.name)
            return None
        live = [e for e in (reg.get("bgLedger") or []) if isinstance(e, dict)]
        ended = [e for e in (reg.get("bgLedgerEnded") or []) if isinstance(e, dict)]
        return live, ended

    def _ledger_write(self, live, ended):
        self.backend._update_reg(self.sid, bgLedger=live,
                                 bgLedgerEnded=ended[-self._LEDGER_TOMBSTONES:])

    async def _ledger_tool_hook(self, inp, tool_use_id, context):
        """PostToolUse on the launch/stop tools. Defensive throughout: an unrecognized shape records
        nothing and logs — the Stop-hook reconciler and the lifecycle stream remain the safety nets."""
        try:
            tname = str((inp or {}).get("tool_name") or "")
            targs = (inp or {}).get("tool_input") or {}
            tresp = (inp or {}).get("tool_response") or {}
            if not isinstance(targs, dict):
                return {}
            if not isinstance(tresp, dict):
                tresp = {}
            nw = time.time()
            lr = self._ledger_read()
            if lr is None:
                return {}                             # skip, never wipe — the next event re-records
            live, ended = lr
            if tname == "TaskStop":
                tid = str(targs.get("task_id") or targs.get("taskId") or targs.get("id")
                          or targs.get("shell_id") or "")   # shell_id: the deprecated param the CLI still accepts
                if tid:
                    kept = [e for e in live if str(e.get("tid")) != tid]
                    if len(kept) != len(live):
                        ended.append({"tid": tid, "why": "stopped", "at": int(nw)})
                        self._ledger_write(kept, ended)   # the session called off its own wait — an
                        self.backend._poke()              # event, not a crash (review map item 4)
                return {}
            if tname == "Bash" and not targs.get("run_in_background"):
                return {}                                 # foreground Bash is a turn, not a launch
            if tname not in self._LEDGER_TOOLS:
                return {}
            tid = str(tresp.get("backgroundTaskId") or tresp.get("task_id") or tresp.get("taskId")
                      or tresp.get("id") or "")
            deadline, persistent = None, False
            if tname == "Monitor":
                persistent = bool(targs.get("persistent"))
                tmo = targs.get("timeout_ms")
                if not persistent and isinstance(tmo, (int, float)) and tmo > 0:
                    deadline = nw + float(tmo) / 1000.0   # the harness kills the watcher then — exact,
                    #                                       not the job's ETA (research map item 2)
            desc = str(targs.get("description") or targs.get("prompt") or targs.get("command") or "")[:300]
            tuid = str(tool_use_id or (inp or {}).get("tool_use_id") or "") or None
            if not tid and not tuid:
                return {}   # nothing to key on — an entry no event could ever clear must not be born
            entry = {"tid": tid or None, "toolUseId": tuid,
                     "tool": tname.lower(), "desc": desc, "armedAt": int(nw),
                     "deadlineEpoch": deadline, "persistent": persistent,
                     "procGen": self.proc_gen,
                     "agentId": str((inp or {}).get("agent_id") or "") or None, "src": "hook"}
            live = [e for e in live
                    if not (tid and str(e.get("tid")) == tid)
                    and not (tuid and str(e.get("toolUseId")) == tuid)] + [entry]
            if len(live) > self._LEDGER_CAP:   # bounded like every reg field — oldest launches evict, loudly
                self.backend._log("bg-ledger (%s): cap %d hit, evicting oldest" % (self.name, self._LEDGER_CAP))
                live = live[-self._LEDGER_CAP:]
            self._ledger_write(live, ended)
        except Exception as e:
            self.backend._log("bg-ledger hook (%s): %s" % (self.name, e))
        return {}

    async def _ledger_fail_hook(self, inp, tool_use_id, context):
        """PostToolUseFailure on the launch tools: a launch whose ack ERRORED never started — drop any
        entry recorded for it so it cannot hold an awaiting gate open as a phantom wait (probe-verified
        2026-08-28: the event emits with error + is_interrupt; a DENIED tool does not reach here)."""
        try:
            tuid = str((inp or {}).get("tool_use_id") or tool_use_id or "")
            if not tuid:
                return {}
            lr = self._ledger_read()
            if lr is None:
                return {}                             # skip, never wipe
            live, ended = lr
            kept = [e for e in live if str(e.get("toolUseId")) != tuid]
            if len(kept) != len(live):
                why = "interrupted" if (inp or {}).get("is_interrupt") else "launch-failed"
                ended.append({"tid": next((e.get("tid") for e in live
                                           if str(e.get("toolUseId")) == tuid), None),
                              "why": why, "at": int(time.time())})
                self._ledger_write(kept, ended)
                self.backend._poke()
        except Exception as e:
            self.backend._log("bg-ledger fail hook (%s): %s" % (self.name, e))
        return {}

    # ── session FACTS from the interaction tools (2026-08-29, hook round 2) ────────────────────
    # Three fact families, all probe-verified shapes (CLI 2.1.221): the task STORE stays the
    # authoritative snapshot per the repo rule — the TaskCreate/TaskUpdate hook is the POKE to
    # re-read it now plus the attribution the store can never provide (which agent wrote, incl.
    # subagent writes the transcript fold structurally misses); PushNotification's ack carries
    # pushSent/localSent, so an agent's own "come look" judgment stops being invisible when the
    # OS push was suppressed; Skill's ack names the role/command the session just took on.
    _PUSH_NOTES_CAP = 10
    _TASK_WRITES_CAP = 15

    async def _facts_tool_hook(self, inp, tool_use_id, context):
        try:
            tname = str((inp or {}).get("tool_name") or "")
            targs = (inp or {}).get("tool_input") or {}
            tresp = (inp or {}).get("tool_response") or {}
            if not isinstance(targs, dict):
                targs = {}
            if not isinstance(tresp, dict):
                tresp = {}
            nw = int(time.time())
            aid = str((inp or {}).get("agent_id") or "") or None
            if tname in ("TaskCreate", "TaskUpdate"):
                # a capped TAIL, not a slot (review 2026-08-29): parallel subagents cluster task
                # writes faster than any consumer's read cadence, and a last-write-wins slot loses
                # every agentId but the newest — the attribution this field exists for
                tid = str(tresp.get("taskId") or targs.get("taskId") or "") or None
                reg = read_reg_for_rmw(self.backend.state_dir, self.sid)   # sole writer; no await
                if reg is None:
                    self.backend._log("facts hook (%s): reg unreadable — skipping the taskWrites "
                                      "record rather than wiping it" % self.name)
                    return {}
                #                                                          before the write below —
                #                                                          the event loop IS the lock
                writes = [w for w in (reg.get("taskWrites") or []) if isinstance(w, dict)]
                writes.append({"at": nw, "tool": tname, "taskId": tid, "agentId": aid})
                self.backend._update_reg(self.sid, taskWrites=writes[-self._TASK_WRITES_CAP:])
                self.backend._poke()          # the store is authoritative — this says "re-read it NOW"
            elif tname == "PushNotification":
                reg = read_reg_for_rmw(self.backend.state_dir, self.sid)   # sole writer of pushNotes;
                if reg is None:
                    self.backend._log("facts hook (%s): reg unreadable — skipping the pushNotes "
                                      "record rather than wiping it" % self.name)
                    return {}
                #                              NO await between this read and the write below — the
                #                              session's single event loop is what serializes this
                #                              RMW, not _reg_lock. Inserting an await here reopens
                #                              the lost-note race (review 2026-08-29).
                notes = [n for n in (reg.get("pushNotes") or []) if isinstance(n, dict)]
                notes.append({"at": nw, "message": str(targs.get("message") or "")[:200],
                              "pushSent": bool(tresp.get("pushSent")),
                              "localSent": bool(tresp.get("localSent")), "agentId": aid})
                self.backend._update_reg(self.sid, pushNotes=notes[-self._PUSH_NOTES_CAP:])
                self.backend._poke()          # a suppressed push is still the agent saying "come look"
            elif tname == "Skill":
                if aid:
                    return {}                 # a SUBAGENT's skill is not the session's role — the
                    #                           stamp answers "what is this session acting as", and a
                    #                           drafting subagent invoking jld must not clobber it
                nm = str(tresp.get("commandName") or "") or None
                # commandName is the ONE authority (probe-verified on 2.1.221; failures route to
                # PostToolUseFailure, unregistered here). No tool_input fallback: recording the
                # REQUESTED alias when the resolved name vanished would be a silent degrade.
                if nm:
                    self.backend._update_reg(self.sid, lastSkill={"at": nw, "name": nm})
                elif tresp:
                    self.backend._log("facts hook (%s): Skill ack carried no commandName — not recording" % self.name)
        except Exception as e:
            self.backend._log("facts hook (%s): %s" % (self.name, e))
        return {}

    async def _subagent_start_hook(self, inp, tool_use_id, context):
        """A Task-spawned subagent just STARTED. Record it live (agent_id -> type + start time) so the session
        reads 'working' while it runs and the lane can show how many are in flight. The SDK's SubagentStart
        hook input carries agent_id + agent_type. Best-effort; never raises inside the hook."""
        aid = inp.get("agent_id") if isinstance(inp, dict) else None
        if aid:
            with self._sub_lock:
                self._subagents[aid] = {"type": (inp.get("agent_type") or ""), "since": int(time.time())}
            self.backend._poke()
        return {}

    async def _subagent_stop_hook(self, inp, tool_use_id, context):
        """A Task-spawned subagent FINISHED — drop it from the live set (SubagentStop carries the same agent_id).
        When the last one clears, the session falls back to its real state (working if the main turn is still in
        flight, else idle)."""
        aid = inp.get("agent_id") if isinstance(inp, dict) else None
        if aid:
            with self._sub_lock:
                self._subagents.pop(aid, None)
            self.backend._poke()
        return {}

    def _live_subagents(self) -> list:
        """The Task subagents running RIGHT NOW: [{"type","since"}], oldest first. Copied under the lock (hooks
        mutate on the loop thread; snapshot() reads on the kernel thread)."""
        with self._sub_lock:
            return sorted((dict(v) for v in self._subagents.values()), key=lambda d: d.get("since") or 0)

    # ---- background-task tracking (the CLI's task lifecycle stream) ----

    _TERMINAL_TASK = frozenset(("killed", "failed", "stopped", "completed"))

    def _on_task_event(self, subtype: str, d: dict):
        """One system/task_* lifecycle message → the live _bg_tasks set. task_started adds; task_progress
        refreshes (and SELF-HEALS an unknown id — a backend that attached mid-task still converges);
        task_notification always ends its task; task_updated ends it only on a terminal patch.status.
        Pokes a push only when the set actually changed, so progress chatter stays cheap."""
        tid = str(d.get("task_id") or "")
        if not tid:
            return
        changed = False
        with self._sub_lock:
            if subtype in ("task_started", "task_progress"):
                entry = self._bg_tasks.get(tid)
                if entry is None:
                    self._bg_tasks[tid] = entry = {"desc": "", "type": d.get("task_type") or "",
                                                   "since": int(time.time()),
                                                   "toolUseId": d.get("tool_use_id") or "", "lastTool": ""}
                    changed = True
                if d.get("description"):
                    entry["desc"] = str(d.get("description"))
                if d.get("last_tool_name"):
                    entry["lastTool"] = str(d.get("last_tool_name"))
            else:
                status = (d.get("status") if subtype == "task_notification"
                          else (d.get("patch") or {}).get("status") if isinstance(d.get("patch"), dict) else None)
                if subtype == "task_notification" or (isinstance(status, str) and status in self._TERMINAL_TASK):
                    changed = self._bg_tasks.pop(tid, None) is not None
        if changed:
            # MIRROR the live set to the reg: bg tasks die with the CLI, and the in-memory set dies with
            # the backend — the persisted mirror is what lets a later boot's reconcile tell the session
            # its tasks were killed (task_death_notice) instead of leaving it waiting forever on a
            # notification that can never arrive (nimbus's dead campaign watcher, the user 2026-07-11).
            # Cleared when the deaths are reported (reconcile / _on_session_gone); tasks that END
            # normally clear here, so the mirror never false-alarms.
            try:
                self.backend._update_reg(self.sid, bgTasks=self._live_bg_tasks())
            except Exception as e:
                self.backend._log("background tasks (%s): registry mirror write failed: %s" % (self.name, e))
            self.backend._poke()

    def request_stop_task(self, tool_use_id: str) -> bool:
        """Stop ONE background task by the id the chat box shows (its tool-use id). Resolved to the
        CLI's lifecycle task id (the _bg_tasks key — what the SDK's stop_task control request takes);
        either id form is accepted. False when the task is unknown/already terminal or the client
        isn't connected — the kernel warns the user then, never a silent no-op."""
        with self._sub_lock:
            tid = next((k for k, v in self._bg_tasks.items()
                        if k == tool_use_id or v.get("toolUseId") == tool_use_id), "")
        if not tid or not (self.loop and self.client):
            return False
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._do_stop_task(tid)))
        return True

    async def _do_stop_task(self, tid):
        # loud on failure, like every stop (the interrupt lesson): a stop that vanishes without a trace
        # is the bug; the task's own terminal lifecycle event is what clears it from the box
        try:
            await self.client.stop_task(tid)
        except Exception as e:
            self.backend._log("stop_task (%s): control request failed for %s: %s" % (self.name, tid, e))
        self.backend._poke()

    # ---- MCP servers (the SDK's designed control requests) ----
    # `/mcp` in a romp session hits the CLI's INTERACTIVE panel, which an SDK-driven session cannot
    # render — the CLI just says "use a terminal" (the user 2026-08-05). The SDK exposes the same facts
    # and actions as control requests, so romp serves its own panel from them: get_mcp_status for the
    # list, toggle_mcp_server / reconnect_mcp_server for the two repairs. Each runs ON the session's
    # loop and blocks the calling (kernel) thread only for a bounded wait.
    def _call_on_loop(self, coro_fn, what, timeout=20.0):
        """Run a client coroutine on this session's loop and return its result. (None, reason) on any
        failure — the kernel surfaces the reason; never a silent empty panel."""
        if not (self.loop and self.client):
            return None, "this session's CLI isn't connected"
        try:
            fut = asyncio.run_coroutine_threadsafe(coro_fn(), self.loop)
            return fut.result(timeout), ""
        except Exception as e:
            self.backend._log("%s (%s) failed: %s: %s" % (what, self.name, type(e).__name__, e))
            return None, "%s: %s" % (type(e).__name__, e)

    def mcp_status(self):
        r, err = self._call_on_loop(lambda: self.client.get_mcp_status(), "mcp status")
        servers = (r or {}).get("mcpServers") if isinstance(r, dict) else None
        return (servers if isinstance(servers, list) else []), err

    def mcp_toggle(self, name: str, enabled: bool):
        _r, err = self._call_on_loop(lambda: self.client.toggle_mcp_server(name, enabled), "mcp toggle")
        return err

    def mcp_reconnect(self, name: str):
        _r, err = self._call_on_loop(lambda: self.client.reconnect_mcp_server(name), "mcp reconnect")
        return err

    def request_rewind_files(self, uuid: str) -> bool:
        """Restore the workspace's files to their state just before the given user message — the SDK's
        designed rewind_files control request (enable_file_checkpointing is on for every session). The
        CONVERSATION is untouched; the chat's edit/delete rewinds cover that. False when the client
        isn't connected — the kernel warns then, never a silent no-op."""
        if not (uuid and self.loop and self.client):
            return False
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._do_rewind_files(uuid)))
        return True

    async def _do_rewind_files(self, uuid):
        # loud on failure (a restore that vanishes without a trace is the bug): the common refusal is a
        # message from before checkpointing was enabled — the CLI rejects it and the log says why
        try:
            await self.client.rewind_files(uuid)
            self.backend._log("rewind_files (%s): workspace restored to before %s" % (self.name, uuid))
        except Exception as e:
            self.backend._log("rewind_files (%s): control request failed for %s: %s" % (self.name, uuid, e))
        self.backend._poke()

    def _live_bg_tasks(self) -> list:
        """The background tasks running RIGHT NOW: [{"desc","type","since","toolUseId","lastTool"}], oldest
        first. Copied under the lock, like _live_subagents."""
        with self._sub_lock:
            return sorted((dict(v) for v in self._bg_tasks.values()), key=lambda d: d.get("since") or 0)

    # ---- snapshot for live_sessions() ----

    def snapshot(self) -> dict:
        # Parked in can_use_tool/_ask_user waiting on the USER (a permission Allow/Deny or an
        # AskUserQuestion picker)? The turn stays inflight through that wait, so reporting "working" made
        # the feed/timeline miss it — the kernel floors a card to BLOCKED off the live "permission"/"picker"
        # state, exactly as it does for tmux (the user 2026-06-27: an SDK AskUserQuestion didn't register as
        # blocked the way tmux's does). _pending_ask is set for the whole ask (both kinds), and the ask
        # handlers append the needs-input state BEFORE raising it, so last_state is authoritative here.
        parked = self.backend._pending_ask.get(self.sid) is not None
        subs = self._live_subagents()
        if self.inflight > 0 and not parked:
            # actively producing. A user interrupt shows 'waiting' (stopped) even though inflight stays 1
            # until the aborted turn's ResultMessage settles it — see _do_interrupt. A synchronous Task keeps
            # the main turn in flight, so this branch already reads 'working' the whole time it runs.
            state = "waiting" if self._interrupted else ("retrying" if self.retrying else "working")
            since = self.since
        elif subs and not parked and not self._interrupted:
            # The main turn settled (inflight 0) but a Task subagent is still running — a BACKGROUNDED one that
            # outlives the turn. The session IS still working; surface that instead of idling to 'waiting' (the
            # user 2026-06-30: "mark it working when it has subagents running"). Clears itself when the last
            # SubagentStop lands and the set empties.
            state, since = "working", (min((s.get("since") or 0) for s in subs) or self.since)
        else:
            ls = last_state(self.backend.state_dir, self.sid)
            state, since = ls.get("state") or "waiting", ls.get("t") or 0
        return {"state": state, "since": str(since) if since else "",
                "model": model_label(self.model, self.chosen_model), "effort": self.effort,
                "modelPending": bool(self._model_pending),   # a /model switch resolving → the badge shows switching-dots
                "effortPending": bool(self._effort_pending),   # an /effort switch reconnecting → effort-badge dots + "Reloading session…"
                "auth": self.effective_auth(),   # which account this session bills ('login'|'key') → gear badge
                "authLive": self.auth_live,   # what the CLI's init actually reported ("" until one
                #   lands) — the Billing row says so when it disagrees with the launch intent above
                #   (a key found via apiKeyHelper bills the key while `auth` still reads login)
                "authPending": bool(self._auth_pending),   # an /auth switch reconnecting → badge dots
                "mode": self.perm_mode, "ctx": self._ctx_pct(), "ctxTokens": self._ctx_tokens,
                "ctxOver": self._ctx_over,   # the % above is CLAMPED — true when the CLI reported 100+
                #   (tokens exceed the current model's window, e.g. right after a 1M→200k switch)
                "summary": "",
                "connected": bool(self.client),   # the SDK handshake is up (set at connect, cleared at
                #   teardown) — the "this session is OPEN" event for a transcript-less fresh session:
                #   the kernel's opening-chip override stands down on it, so a new SDK session reads
                #   ready the moment it can take a message instead of wearing the opening dots until
                #   its first turn writes a transcript (the user 2026-08-08, whose fresh session sat
                #   on animated dots for minutes while fully up)
                "spawning": not self.client,   # the spawn/handshake window is open RIGHT NOW (this
                #   thread is up, the client isn't yet) — the ONLY window the kernel's opening chip
                #   may cover. Its ABSENCE must mean "not opening": the chip once keyed on `connected`
                #   being falsy, which a DORMANT created session also reports (a kernel restart kills
                #   idle CLIs, boot reconcile leaves them lazy, and a never-messaged session has no
                #   transcript either) — so the dots outlived the create by hours when one message
                #   would wake it in seconds (the user 2026-08-13). Dormant rows carry no spawning
                #   key at all, so they read ready.
                "fast": self.fast,   # fast-mode state from init ("on"/"off"/"cooldown"; "" = unknown → no badge)
                "fastReason": self.fast_reason,   # init's disabled_reason — non-empty hides the chat toggle
                "retryCount": self.retry_count,   # api_retry backoff attempts in the current storm → the live 'attempt N' in the chat's retrying element
                "retryInfo": self.retry_info,     # the latest attempt's detail (attempt/max, error status+message, next-attempt epoch) → the retrying element's context lines (the user 2026-07-10)
                "interrupting": bool(self._interrupted),   # a user interrupt is IN FLIGHT: set at dispatch,
                #   cleared EXACTLY when the aborted turn's ResultMessage settles → the kernel holds the chip +
                #   feed badge on 'interrupting' across that whole window, no dependence on the flickering tail
                "snapT": time.time(),   # when THIS snapshot was taken. The kernel's push loop snapshots every
                #   backend once, then builds for a while; a stop clicked mid-loop postdates the snapshot, so
                #   its interrupting:False is pre-click evidence and must not settle the click (the user
                #   2026-08-04: Interrupting… fell back to Working, then Ready — the stale snapshot popped
                #   the click stamp). _interrupting compares this against the click time.
                "subagents": subs,   # live Task subagents (count + types) → lane affordance; [] when none
                "bgTasks": self._live_bg_tasks()}   # live background tasks (task lifecycle stream) → an idle
                #   session waiting on a timer/watcher reads AWAITING, why = the task's description; [] when none


LIVE_TAIL_CAP = 100

# A path-looking token (absolute or ~-rooted): the one case where an echo's text-match against the
# transcript can structurally FAIL — the transcript extracts an attached file's path out of the user
# text (the user 2026-06-25) — so only these echoes stay eligible for the genuine-human-turn floor
# retire in prune_live. Everything else prunes by its own text landing, or persists (a visible loss).
_PATHY_RE = re.compile(r"(?:^|[\s'\"`(])(?:~/|/)[^\s'\"`)]+")


def _path_bearing(text: str) -> bool:
    return bool(_PATHY_RE.search(text or ""))


def _evict_live_overflow(d: dict, cap: int = LIVE_TAIL_CAP) -> None:
    """Bound a session's in-memory live tail when no client ever drains/prunes it — but never at the
    cost of an INPUT ECHO or a command-feedback line. A stream WORK atom is disposable (the transcript
    supersedes it by uuid within a second), but an echo is the ONLY record of a send the transcript
    hasn't caught up on — evicting it makes an in-flight or dropped message silently invisible (the
    user 2026-07-20: a reply vanished from the chat with no trace). Oldest work atoms go first; only
    in the pathological all-echo case does the cap fall back to evicting oldest-regardless, because a
    bounded store still beats an unbounded leak."""
    if len(d) <= cap:
        return
    for k in list(d.keys()):
        if len(d) <= cap:
            return
        a = d[k]
        if not a.get("_echo_text") and not a.get("command"):
            del d[k]
    while len(d) > cap:
        del d[next(iter(d))]


# ---------------------------------------------------------------------------
# The backend.
# ---------------------------------------------------------------------------

class SdkBackend:
    """Manages SDK-backed sessions. Constructed by the kernel with callbacks for
    pushing to clients and a few launch parameters that mirror the tmux launch."""

    def __init__(self, state_dir, claude_bin: str, notify, poke=None, push=None,
                 push_session=None,
                 mcp_config: str | None = None, append_prompt_path: str | None = None,
                 log=None, reconcile: bool = False):
        self.state_dir = Path(state_dir)
        self.claude_bin = claude_bin
        self.thread_wake_model = None      # kernel-installed: model_id -> replacement or None, consulted
        #                                    ONLY when a comment THREAD is explicitly woken (T223 rider) —
        #                                    the catalog lives in the kernel; the backend never imports it
        self._notify = notify              # notify(app, msg) -> push to clients (kernel._send_to_app)
        self._poke_cb = poke               # wake the kernel's producer/judges (optional)
        self._push_cb = push               # wake the kernel's PUSHER → immediate chat push (live tail)
        self._push_session_cb = push_session   # targeted ONE-session push (kernel _push_session_now) for
        #   per-session chip events (the connect handshake): a wake alone leaves the flip riding the next
        #   full push cycle, which runs seconds on a busy fleet (the user 2026-08-10)
        self.mcp_config = mcp_config
        self.append_prompt_path = append_prompt_path
        self._log_cb = log
        self.sessions: dict[str, SdkSession] = {}
        self._lock = threading.Lock()
        self._reg_lock = threading.Lock()         # serializes _update_reg read-modify-writes (queue mirror
        #                                           writes come from kernel AND loop threads)
        self._pending_ask: dict[str, bool] = {}   # sid -> has an ask awaiting answer
        self._live: dict[str, dict] = {}          # sid -> {key -> atom}: the in-memory LIVE TAIL (ahead of disk)
        self._rl_lock = threading.Lock()          # serializes usage.json read-merge-write (_record_rate_limit)
        self._drain_hold_until = 0.0              # deploy-drain lease (T121): RUNTIME-ONLY — a fresh boot starts clear by construction
        self._drain_hold_since = 0.0
        self._drain_hold_rang = False
        self._drain_wake_timer = None
        self.login_ok = lambda: True              # the kernel wires its credential-store probe (T124); permissive unwired
        self._usage_all_keyed = False             # refresh_usage's one-shot: the last refresh found only
        #                                           keyed candidates (already logged); reset when a
        #                                           pollable session exists again, so the 60s rail timer
        #                                           logs the condition once per episode, not per call
        self._fork_children_memo = None           # (sdk/ dir mtime_ns, {parent sid: [child lineage]}) — fork_children()
        self.work_key = work_api_key()            # the manager env's API key, claimed out of os.environ
        #   ("" = none): per-session auth injects it via _options; its presence is the "key" half of
        #   the availability the kernel publishes to the picker/gear (the user 2026-08-08)
        # Backend PROBLEMS, kept in a bounded ring so the dashboard can show them (see _log): until
        # 2026-07-28 every SDK failure went to the kernel log alone, which nobody tails, so a session
        # whose stream died or whose model switch was refused just looked odd with no way to find out.
        self._problems: list[dict] = []
        self._problem_seq = 0
        self._problem_lock = threading.Lock()
        # The dependency check, done ONCE here: absent → every session this backend owns reports the same
        # launch error (launch_error), instead of each one silently dying at its own lazy import.
        self._sdk_missing = not sdk_importable()
        if self._sdk_missing and log:
            self._log("claude_agent_sdk is NOT importable — every SDK session will report itself unable to "
                      "start (run bin/romp-sdk-setup). tmux sessions are unaffected.", problem=True)
        self._heal_attempts: dict[str, int] = {}  # sid -> crash-resume attempts since its last COMPLETED turn
        #                                           (bounds _heal_cut_session to one resume per cut; a completed
        #                                           turn resets it, so a crash LOOP can't respawn forever)
        self._drive_marks: dict[str, tuple] = {}  # sid -> (path, pos, ts) of the last idle-queue drive's newest
        #                                           DRIVEN wrapper — the in-memory face of the reg's driveMark
        #                                           (the per-watermark latch; see drive_idle_queue)
        self._drive_inflight: set[str] = set()    # sids a drive worker currently carries: acceptance stands
        #                                           down on these WITHOUT latching, so two workers can never
        #                                           overlap on one sid — an overlap let a stand-down's mark
        #                                           restore clobber the newer worker's watermark and the model
        #                                           heard the same notifications twice (2026-08-18 review)
        self._spawn_sem = threading.Semaphore(BOOT_RESUME_CONCURRENCY)   # the ONE machine-wide spawn-stagger
        #                                           budget: boot reconcile's resume sweep AND the idle-queue
        #                                           drive's dormant spawns draw slots from this same semaphore
        #                                           (as two same-sized budgets they burst to 2x the cap after
        #                                           a restart — 2026-08-18 review); a spawn holds its slot
        #                                           until the CLI proves up or its thread dies
        # Kernel-restart heal: nothing is running yet, so any alive session still reading awaiting:true is stale
        # — its background tasks (and the Stop hook that clears the overlay) died with the previous kernel. Left
        # uncleared it reads working/awaiting forever, climbing a ghost work-timer (reorder_bug 2026-06-24).
        regs = list_regs(self.state_dir)
        for reg in regs:
            if reg.get("alive"):
                self._heal_stale_awaiting(reg["sid"])
        self._reseed_echoes(regs)   # unlanded input echoes survive the restart (reg['echoes'] mirror)
        # Boot reconcile (reconcile=True: the KERNEL passes it at boot; tests and ad-hoc constructions
        # opt in explicitly): recover what the previous kernel's death left behind — reap orphaned
        # CLIs, resume cut turns, deliver persisted queues. In a thread: it spawns claude processes
        # and must not block construction (the WS handler constructs this backend lazily).
        if reconcile:
            threading.Thread(target=self._boot_reconcile, args=(regs,),
                             name="sdk-boot-reconcile", daemon=True).start()
        # NOTE: we deliberately do NOT heal a stale in-flight STATE ("working"/…) on restart. A session whose
        # turn was killed by the kernel restart (e.g. the user hit Refresh mid-turn) keeps its last "working" in
        # the log, so the auto-nudge GENUINE-STOP GATE (_last_state_value in _PROGRESSING_STATES) correctly SKIPS
        # it — it was interrupted, not stopped, and must not be nudged (the user 2026-06-29: refresh was nudging
        # in-progress sessions). A session that genuinely FINISHED a turn before the restart already logged
        # "waiting" (ResultMessage), so it stays nudge-eligible. The dormant in-flight→waiting DISPLAY heal lives
        # independently in live_sessions, so the feed/fleet still render dormant sessions as waiting.

    def _heal_stale_awaiting(self, sid: str) -> None:
        """Clear a stale awaiting:true overlay for a NOT-running session. A dormant SDK session can't have live
        background tasks — its claude subprocess (and the Stop hook that would write awaiting:false) is gone — so
        a lingering awaiting:true is stale. Idempotent: writes only when the overlay is currently true, so it
        never spams the log. The exact event-based analogue of live_sessions' dormant in-flight→waiting heal."""
        try:
            if last_awaiting(self.state_dir, sid) is True:
                append_awaiting(self.state_dir, sid, False)
        except Exception as e:
            self._log("awaiting heal (%s): %s" % (sid[:8], e))

    def _session_cli_pid(self, session) -> int | None:
        """The live CLI pid for `session` — a child of THIS kernel resuming its sid (or lastSid, the
        fork-tracking twin) — for the interrupt escalation's signal. ps-scan through the pure
        find_session_cli matcher, so the signal can only ever land on our own child. None (logged by
        the caller) when no such process exists."""
        try:
            ps = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                                capture_output=True, text=True, timeout=10)
            reg = read_reg(self.state_dir, session.sid) or {}
            sids = [session.sid, str(reg.get("lastSid") or "")]
            return find_session_cli(ps.stdout.splitlines(), sids, os.getpid())
        except Exception as e:
            self._log("session cli pid (%s): %s" % (session.name, e))
            return None

    def _boot_reconcile(self, regs: list[dict]) -> None:
        """The kernel just booted — reconcile what the previous kernel's death left behind. Event-keyed
        on the boot itself plus each session's state tail, never on ages or timers:
          * REAP orphaned SDK CLIs still resuming our sessions: a dead kernel's children re-parent to
            launchd and keep writing the transcript, so a resume would give the conversation two writers.
          * A session whose state tail is 'working' had its turn CUT by the kernel death — a user
            interrupt writes 'idle', a finished turn 'waiting'; only a kill leaves 'working' — so resume
            it with a visible continuation nudge (BOOT_RESUME_NUDGE) ahead of its restored queue. The
            same marker makes the auto-nudge genuine-stop gate skip these sessions, which is correct for
            a USER interrupt but was exactly the silent purgatory for a kernel-death cut (2026-07-05:
            every SDK session stranded mid-turn until hand-resumed).
          * A session with a persisted queue (reg['queue'], the _persist_queue mirror): resume it so the
            queue delivers via the __init__ seed.
        Everything else stays lazy/dormant. Loud one-line summary whenever anything was recovered."""
        try:
            alive = [r for r in regs if r.get("alive") and r.get("sid")]
            reaped = 0
            lastsids = [str(r.get("lastSid") or "") for r in alive if r.get("lastSid")]
            if lastsids:
                try:
                    ps = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                                        capture_output=True, text=True, timeout=10).stdout
                    for pid in find_orphan_clis(ps.splitlines(), lastsids):
                        if pid == os.getpid():
                            continue
                        try:
                            os.kill(pid, signal.SIGTERM)
                            reaped += 1
                        except (ProcessLookupError, PermissionError):
                            pass
                except Exception:
                    self._log("boot reconcile: orphan reap failed: %s" % traceback.format_exc())
            resumed, restored, notified = 0, 0, 0
            to_start: list[str] = []   # sids to spawn — collected first, spawned STAGGERED below
            for r in alive:
                # Per-session isolation: one session's hiccup (a reg-write race with the outgoing
                # kernel, a corrupt state file) must not abort the sweep and strand the REST —
                # exactly that happened live 2026-07-06: a write_reg FileNotFoundError on the first
                # session killed two whole reconcile passes.
                try:
                    sid = str(r["sid"])
                    # A /model / /effort switch mid-flight at the kernel's death can never clear its
                    # pending flags (the in-memory switch died) — and the dormant path serves them
                    # verbatim, so the badge's switching-dots sat there forever (the user 2026-07-11).
                    # The switch is moot at the next connect (effort + chosen alias both ride
                    # _options), so heal here for sessions that stay DORMANT; SdkSession.__init__
                    # heals the same way for ones that respawn.
                    if r.get("effortPending") or r.get("modelPending"):
                        self._update_reg(sid, effortPending=False, modelPending=False)
                    queued = [t for t in (r.get("queue") or []) if isinstance(t, str) and t]
                    if r.get("threadOf") and not queued:
                        # a comment THREAD is never auto-resumed at boot (the user 2026-09-01: threads
                        # persist on disk and come alive only on an explicit reply/branch; a deploy
                        # boot resuming a cut thread turn put dormant threads back in RAM). Its
                        # orphaned CLI was reaped above and its pending flags healed just now; only a
                        # PERSISTED QUEUE — the user's own typed reply the CLI never started — earns
                        # the resume, because delivering it IS honoring an explicit gesture.
                        continue
                    # A cut turn is any MACHINE-ACTIVE last state, not just "working" (the user
                    # 2026-08-19, whose figure session sat blocked-on-you after every restart that
                    # landed mid-API-retry): "retrying" is a long-lived open-turn state — the CLI is
                    # mid-turn waiting out an API error — and "compacting" likewise. A restart
                    # during either cut the turn exactly as a working cut does, but the == "working"
                    # test skipped the machineCut stamp AND the resume nudge, so the bare stop
                    # record read as the user's Esc (INTERRUPT_BLOCK_WHY) and nothing ever resumed
                    # the session. "permission"/"picker" stay excluded: those turns were already
                    # waiting on the user, so blocked-on-you is the truth there.
                    cut = last_state_value(self.state_dir, sid) in ("working", "retrying", "compacting")
                    # bg tasks the dead kernel's CLI took with it (the reg mirror, _on_task_event):
                    # the session must HEAR about them or it waits forever on a dead timer/watcher.
                    dead_tasks = [t for t in (r.get("bgTasks") or []) if isinstance(t, dict)]
                    ask_died = bool(r.get("pendingAsk"))   # a question was up when the old kernel died (T214)
                    if not (cut or queued or dead_tasks or ask_died):
                        continue                   # idle, empty-queued, nothing died → stays lazy
                    # Prepend to the PERSISTED queue (not enqueue()) so it is fed FIRST, before the
                    # restored backlog, and survives even a death mid-reconcile. Order: the resume
                    # nudge (continuation context), then the task-death notice.
                    prepend = ([BOOT_RESUME_NUDGE] if cut else []) \
                            + ([ASK_DIED_NOTICE] if ask_died else []) \
                            + ([task_death_notice(dead_tasks)] if dead_tasks else [])
                    if prepend or dead_tasks:
                        with self._reg_lock:
                            reg = read_reg(self.state_dir, sid) or dict(r)
                            reg["queue"] = prepend + [t for t in (reg.get("queue") or [])
                                                      if isinstance(t, str) and t and t not in prepend]
                            if dead_tasks:
                                reg["bgTasks"] = []   # reported — never re-notify for the same deaths
                            if ask_died:
                                reg["pendingAsk"] = False   # asked once per death — the re-raise mints a fresh flag
                            write_reg(self.state_dir, sid, reg)
                    if cut:
                        # Durable "romp cut this, romp is continuing it" stamp, written with the resume
                        # notice rather than waiting for it to reach disk — so the interrupt-block tick
                        # cannot read the intervening bare stop record as the user stopping the session
                        # (see append_machine_cut).
                        append_machine_cut(self.state_dir, sid, "restart")
                    resumed += 1 if cut else 0
                    notified += 1 if dead_tasks else 0
                    restored += len(queued)
                    to_start.append(sid)
                except Exception:
                    self._log("boot reconcile: session %s failed (sweep continues): %s"
                              % (r.get("sid"), traceback.format_exc()))
            if reaped or resumed or restored or notified:
                self._log("boot reconcile: resumed %d cut turn(s), restored %d queued message(s), "
                          "notified %d session(s) of dead background tasks, reaped %d orphaned CLI(s)"
                          % (resumed, restored, notified, reaped))
                self._poke()
            # STAGGERED spawn (see BOOT_RESUME_CONCURRENCY): every reg above is already fixed —
            # queues persisted, heals applied — so even a death mid-stagger loses nothing (the next
            # boot's sweep picks the rest up). Spawns hold a slot on the backend-wide _spawn_sem —
            # SHARED with the idle-queue drive, whose first ticks run inside this very stagger
            # window, so the two together never exceed the one machine-wide budget — until the
            # session's CLI is past its catch-up burst (init message) or its thread dies
            # (_fire_boot_settled); the acquire timeout is a loud BACKSTOP for a pre-init wedge,
            # never the pacing itself (and an expired backstop holds NO slot, so nothing releases —
            # a stray release would permanently inflate the process-lifetime budget).
            # NO resume gate (the user 2026-07-22). The high-context hold used to divert these sessions
            # behind a Proceed / Compact on resume / Skip card; that card is cut. It went stale the moment
            # anything else resumed the session (nothing ever retired it), and its premise did not hold
            # regardless: measuring context and running /compact both need the session LIVE, so Proceed and
            # Compact each paid in full the reload the gate existed to avoid, leaving Skip as the only
            # option that did what the card said. Context is managed by hand for now. Every cut/queued
            # session resumes here, exactly as it did before the gate.
            for sid in to_start:
                slot = self._spawn_sem.acquire(timeout=BOOT_RESUME_SLOT_S)
                if not slot:
                    self._log("boot reconcile: resume slot backstop expired (a CLI is wedged "
                              "pre-init?) — continuing the sweep anyway")
                try:   # same per-session isolation as above: one bad spawn must not strand the rest
                    self._ensure(sid, on_boot_settled=(self._spawn_sem.release if slot else None))
                except Exception:
                    if slot:
                        self._spawn_sem.release()   # the parked release never got attached — free the slot here
                    self._log("boot reconcile: spawn %s failed (sweep continues): %s"
                              % (sid, traceback.format_exc()))
        except Exception:
            self._log("boot reconcile failed: %s" % traceback.format_exc())

    def drive_idle_queue(self, cands, wait: bool = False) -> None:
        """Deliver wake signals stuck in a STUCK-regime session's CLI queue (the user 2026-08-18; the
        two-regime story — in MOST sessions the CLI delivers this class itself from idle, and the
        kernel tick's age floor keeps those out of here — lives on kernel._undelivered_wake_tail's
        comment block). Each candidate is {sid, path, entries, mark} from the kernel's tick: the
        transcript's still-pending enqueues and the newest one's (pos, ts) watermark. The same
        recovery boot reconcile performs for romp's persisted queues, for the queue that lives in the
        CLI: reconnect if dormant, then deliver — via enqueue(), the exact channel restored queues
        ride, carrying the CLI's OWN queued texts verbatim (joined in queue order), never a synthetic
        prompt. Gates, each an exact event, checked at acceptance AND re-checked at the send moment
        in _drive_deliver (acceptance alone was a TOCTOU hole — a turn could open while earlier
        candidates in the batch spawned their CLIs):
          * an OPEN turn / compaction / clearing / a pending rewind stands down WITHOUT latching —
            the next parse re-checks once the operation settles. Mid-turn arrivals SURVIVE the parse
            (a turn's records clear only the entries whose text they carry — see
            _undelivered_wake_tail) and drive once the turn settles;
          * an ENDED session (reg alive=False) never revives for housekeeping, and a CUT turn (any
            machine-active state tail — 'working'/'retrying'/'compacting', boot reconcile's own cut
            discriminator) belongs to the boot/crash resume machinery;
          * a recorded launchError stands down (the usage-limit queue hold owns that session until a
            connect proves the CLI up again);
          * ONE DRIVE PER WATERMARK: the newest driven wrapper's (path, pos, ts) — the tick marks
            the aged subset it hands over, so a still-young entry stays past the mark and drives
            once aged — is latched IN MEMORY the moment a drive is accepted — that alone stops the
            next tick's re-parse re-firing while the worker is in flight — and persisted to the reg
            (driveMark) only AFTER the enqueue lands, in _drive_deliver: a kernel death between
            acceptance and delivery leaves the reg unlatched, so the next boot's tick re-produces
            the candidate and drives it, instead of a persisted-at-acceptance mark silently
            discarding the never-delivered backlog forever. Only a NEWER enqueue re-arms, and a
            re-armed drive delivers only the entries past the previous mark. A drive that fails
            AFTER the enqueue stays latched on purpose — the failure is problem-ring loud, and a
            recurring signal's next enqueue re-arms — never a silent retry storm.
          * ONE WORKER PER SID (_drive_inflight): while a worker carries a sid, acceptance stands
            down WITHOUT latching even for a strictly newer enqueue — the next parse re-checks,
            like every other gate. Overlapping workers double-delivered: the first one's send-moment
            stand-down restored its pre-acceptance mark over the second's newer latch, the restored
            mark re-drove the whole backlog, and the second worker then delivered its slice again.
            Acceptance runs only on the pusher tick thread, so check-then-add cannot race another
            acceptor; a worker's discard racing the check at worst defers one tick.
        Delivery runs on a worker thread (`wait=True` runs it inline, the test seam); see
        _drive_deliver for the send-moment re-resolve/re-check and the spawn stagger, which draws on
        the SAME _spawn_sem budget as boot reconcile — one machine-wide BOOT_RESUME_CONCURRENCY cap,
        not two."""
        todo = []
        for c in cands:
            sid = str(c.get("sid") or "")
            try:
                if sid in self._drive_inflight:
                    continue               # a worker already carries this sid — stand down WITHOUT
                #                            latching (even for a newer enqueue); the next parse
                #                            re-checks, and overlap is double delivery (docstring)
                path = str(c.get("path") or "")
                pos, ts = c["mark"]
                prev = self._drive_marks.get(sid)
                if prev is None:
                    pm = (read_reg(self.state_dir, sid) or {}).get("driveMark")
                    prev = ((str(pm.get("path") or ""), pm.get("pos", -1), str(pm.get("ts") or ""))
                            if isinstance(pm, dict) else ("", -1, ""))
                    self._drive_marks[sid] = prev
                fresh = prev[0] != path or pos > prev[1] or ts > prev[2]
                if not fresh:
                    continue                       # this backlog was already driven; a newer enqueue re-arms
                with self._lock:
                    s = self.sessions.get(sid)
                s = s if (s and s.thread.is_alive()) else None
                if s is not None:
                    if s.ended or s.inflight > 0 or s._compacting or s._clearing \
                            or (s._rewind_to and not s._rewind_armed):
                        continue                   # mid-operation → stand down, no latch: re-check next parse
                else:
                    reg = read_reg(self.state_dir, sid)
                    if not reg or not reg.get("alive"):
                        continue                   # the user ended this session — never revive it for housekeeping
                    if reg.get("launchError"):
                        continue                   # can't even start (usage limit etc.) — that hold owns it
                    if last_state_value(self.state_dir, sid) in ("working", "retrying", "compacting"):
                        continue                   # a CUT turn — any MACHINE-ACTIVE last state, the same
                    #                                discriminator boot reconcile uses (a restart mid-retry or
                    #                                mid-compact cuts the turn exactly as a working cut does) —
                    #                                is the boot/crash resume machinery's recovery, not the drive's
                texts = [e["text"] for e in c["entries"]
                         if e.get("wrapper") and e.get("text")
                         and (prev[0] != path or e["pos"] > prev[1] or e["ts"] > prev[2])]
                if not texts:
                    continue                       # every wake signal here was already driven
                others = sum(1 for e in c["entries"] if e.get("text") and not e.get("wrapper"))
                # LATCH in memory only: one drive per watermark within this process. The reg's
                # driveMark is written by _drive_deliver AFTER the enqueue lands — persisting here
                # made a kernel death in the latch→delivery window (seconds to minutes behind the
                # spawn stagger) discard the backlog forever, silently (2026-08-18 review).
                self._drive_marks[sid] = (path, pos, ts)
                self._drive_inflight.add(sid)     # released in _drive_deliver's finally, whatever path
                todo.append((sid, texts, others, (path, pos, ts), prev))
            except Exception:
                self._log("idle-queue drive (%s): %s" % (sid[:8] or "?", traceback.format_exc()))
        if not todo:
            return
        if wait:
            self._drive_deliver(todo)
        else:
            threading.Thread(target=self._drive_deliver, args=(todo,),
                             name="sdk-idle-queue-drive", daemon=True).start()

    def _drive_deliver(self, todo) -> None:
        """The idle-queue drive's delivery half (worker thread): re-resolve each session and re-check
        the stand-down gates at the SEND moment, reconnect dormant candidates under the shared spawn
        stagger, then enqueue each backlog as one turn and persist its watermark. Loud both ways:
        every drive logs one kernel-log line naming the session and the queued count (normal
        operation, not a problem); every failure — a refused reconnect, a failed send — lands in the
        problem ring, never silent."""
        for sid, texts, others, mark, prev in todo:
            try:
                # Re-resolve at the send moment, never the acceptance-time snapshot: earlier
                # candidates' spawns can hold this delivery back for minutes, and a session that died
                # and respawned in that window must get its LIVE object — enqueueing into the stale
                # one mirrored the dead queue over reg['queue'] and the texts evaporated with the
                # object, latched against any re-drive (2026-08-18 review, repro-verified).
                with self._lock:
                    s = self.sessions.get(sid)
                s = s if (s and s.thread.is_alive()) else None
                if s is not None:
                    if s.ended or s.inflight > 0 or s._compacting or s._clearing \
                            or (s._rewind_to and not s._rewind_armed):
                        # The acceptance gates, re-checked at the send moment: a turn that opened
                        # while earlier candidates spawned must not have stale pings injected into
                        # it. Stand down WITHOUT latching — restore the pre-acceptance mark (the reg
                        # was never written); the next parse re-checks once the operation settles,
                        # and the surviving tail re-drives then. Unconditional restore is safe ONLY
                        # because _drive_inflight bars a second worker for this sid: with overlap,
                        # this write clobbered a newer drive's watermark and double-delivered.
                        self._drive_marks[sid] = prev
                        self._log("idle-queue drive (%s): a turn opened between acceptance and "
                                  "delivery — standing down; the next parse re-checks" % sid[:8])
                        continue
                else:
                    slot = self._spawn_sem.acquire(timeout=BOOT_RESUME_SLOT_S)
                    if not slot:
                        self._log("idle-queue drive: stagger slot backstop expired (a CLI is wedged "
                                  "pre-init?) — driving %s anyway" % sid[:8], problem=True)
                    try:
                        s = self._ensure(sid, on_boot_settled=(self._spawn_sem.release if slot else None))
                    except Exception:
                        if slot:
                            self._spawn_sem.release()   # the parked release never got attached (or can
                            #                             never fire: an unstarted thread runs no finally)
                            #                             — free the slot, then fail loud below
                        raise
                    if s is None:
                        self._log("idle-queue drive (%s): the session could not be started (reconnect "
                                  "refused) — %d queued notification(s) stay undelivered"
                                  % (sid[:8], len(texts)), problem=True)
                        continue
                    # No gate re-check on this arm: a just-spawned session's only possible open turn
                    # is its persisted-queue seed, and riding behind that in queue order IS correct
                    # delivery — standing down here would strand the backlog instead.
                s.enqueue("\n\n".join(texts))
                try:
                    # Persist the watermark only now the texts are in the queue (and its reg mirror,
                    # via enqueue's _persist_queue): a death BEFORE this line re-drives on the next
                    # boot; a death after enqueue but before this write means at worst one re-drive —
                    # the same degraded semantics the except below already accepts.
                    self._update_reg(sid, driveMark={"path": mark[0], "pos": mark[1], "ts": mark[2]})
                except Exception:
                    self._log("idle-queue drive (%s): drive mark not persisted (a kernel restart may "
                              "re-drive this backlog once): %s" % (sid[:8], traceback.format_exc()))
                self._log("idle-queue drive (%s): waking the session for %d queued notification(s)%s"
                          % (s.name, len(texts),
                             " (+%d other queued item(s))" % others if others else ""))
                self._poke()
            except Exception:
                self._log("idle-queue drive (%s): delivery failed: %s" % (sid[:8], traceback.format_exc()))
            finally:
                self._drive_inflight.discard(sid)   # every exit — delivery, stand-down, refused
                #                                     reconnect, raise — frees the sid for the next
                #                                     parse's acceptance

    def drain(self, timeout: float = 2.0, kill=os.kill) -> dict:
        """Graceful-shutdown drain (the kernel's SIGTERM handler): stop every running session cleanly
        within `timeout` — interrupt any in-flight turn and close the SDK clients so the claude
        subprocesses exit with us instead of being orphaned to launchd as zombie transcript-writers.
        Queued turns are already mirrored to the registry (_persist_queue). A cut turn's state log
        keeps its trailing 'working' (shutdown() writes no idle/waiting; _on_session_gone skips the
        settle when ended is set) — exactly the marker the NEXT kernel's boot reconcile resumes by.
        Bounded so a routine `romp refresh` stays snappy.

        A session STILL CLOSING when the bound expires gets its CLI process reaped before we exit —
        the bound alone orphaned a busy session's CLI (2026-07-25: 'still closing: logic', the
        kernel exited, and the leftover CLI kept executing its turn — tools run in the CLI, so it
        needs nothing from us — for over an hour while the next boot resumed the same conversation
        into a second process). The turn is already marked working, so the reap loses nothing the
        resume does not restore. SIGTERM first, a short existence poll, then SIGKILL; `kill` is the
        test seam."""
        with self._lock:
            sessions = list(self.sessions.values())
        # the identities of the turns this drain is about to cut — the restart-cut ledger's rows
        # (T121: the drain's effect is measured by what still gets cut; sid-keyed like spend)
        # EVERY session with an in-flight turn is a cut — including one already flagged `ended`
        # (mid-shutdown with a live turn: its CLI is reaped below all the same). The old
        # `and not s.ended` clause was a FILTER where a join was meant (T143: romp_cards counted 10
        # transcript-verified cuts against 7 ledger rows — the missing three were mid-shutdown).
        cut = [{"sid": s.sid, "name": s.name} for s in sessions if s.inflight]
        inflight = len(cut)
        for s in sessions:
            try:
                s.shutdown()
            except Exception as e:
                self._log("drain: shutdown failed for %s: %s" % (s.name, e))
        deadline = time.time() + timeout
        for s in sessions:
            if s.thread is not None:   # a constructed-but-never-started session has no thread —
                #   joining None raised here and the WHOLE drain died recordless (T143: 2 of 18
                #   restarts lost their ledger rows to exactly this)
                s.thread.join(max(0.05, deadline - time.time()))
        unjoined = [s for s in sessions if s.thread is not None and s.thread.is_alive()]
        reaped = []
        for s in unjoined:
            try:
                pid = self._session_cli_pid(s)
                if pid is None:
                    continue
                kill(pid, signal.SIGTERM)
                for _ in range(20):                      # ~1s for the TERM to land
                    time.sleep(0.05)
                    try:
                        kill(pid, 0)
                    except ProcessLookupError:
                        break
                else:
                    kill(pid, signal.SIGKILL)            # a wedged CLI still never outlives us
                reaped.append("%s(pid %d)" % (s.name, pid))
            except ProcessLookupError:
                pass                                     # exited between the join and the reap — fine
            except Exception:
                self._log("drain: reap failed for %s: %s" % (s.name, traceback.format_exc()))
        if sessions:
            names = [s.name for s in unjoined]
            self._log("drain: stopped %d session(s), %d in-flight turn(s) interrupted%s%s"
                      % (len(sessions), inflight,
                         "; still closing: " + ", ".join(names) if names else "",
                         "; reaped: " + ", ".join(reaped) if reaped else ""))
        return {"stopped": len(sessions), "inflight": inflight, "unjoined": len(unjoined),
                "reaped": len(reaped), "cutTurns": cut}

    def available(self) -> bool:
        """Can this backend actually RUN a session? False when claude_agent_sdk isn't importable.

        The backend object exists either way, on purpose — it owns the registry, the persisted queues and
        the chat those sessions render from. So `_sdk()` returning something is NOT proof the SDK works,
        and every caller gating on "is the SDK usable" must ask THIS instead. Both session-creation paths
        already carried the right refusal ("never silently fall back — say what's missing") and both
        tested the object, so the refusal was unreachable in precisely the case it was written for: the
        user created a session from the browser, got no error at all, and got a session that could never
        run (the user 2026-07-28)."""
        return not self._sdk_missing

    def busy_count(self) -> int:
        """How many SDK sessions have a turn IN FLIGHT right now — the manager's quiet-window gate
        for deferred deploy restarts (the kernel's /busy route). Authoritative: the same per-session
        inflight counter the drain uses to count the turns a restart would cut. Queued-but-unstarted
        turns don't count — the persisted queue survives a bounce losslessly; only an in-flight turn
        gets interrupted."""
        with self._lock:
            sessions = list(self.sessions.values())
        return sum(1 for s in sessions if s.inflight and not s.ended)

    # ── deploy-drain hold (T121 part 1) ─────────────────────────────────────
    # While a quiet deploy restart is PARKED at the manager, this kernel holds NEW turn starts so
    # /busy genuinely drains to 0 on its own turn-end events (before this, kernels kept starting
    # queued turns and a busy box only ever "drained" via the manager's backstop cut). A LEASE, not
    # a latch: the manager's quiet poll refreshes it every ~3s (/busy?drain=1), it expires seconds
    # after the holder dies, and it is RUNTIME-ONLY — a freshly booted kernel starts clear by
    # construction (the restart the hold served already happened), the manager-approved stale-flag
    # guard. Mid-turn forwards keep flowing (the in-flight turn must be able to finish); queued
    # prompts persist to disk — that IS the checkpoint. Visibility: arming logs the parked state
    # once per episode, and a drain that outlives DRAIN_LOUD_S escalates to the problems ring so a
    # long-held box never reads as mysteriously idle.
    DRAIN_HOLD_TTL = 12.0     # seconds; ~4 manager polls — the lease outlives a missed poll, not a dead manager
    DRAIN_LOUD_S = 300.0      # a drain still holding after 5 min rings — visible, never mysterious

    def refresh_drain_hold(self) -> None:
        """Arm/extend the drain lease (the manager's parked quiet poll calls this each tick)."""
        now = time.time()
        with self._lock:
            first = self._drain_hold_until <= now
            self._drain_hold_until = now + self.DRAIN_HOLD_TTL
            if first:
                self._drain_hold_since = now
                self._drain_hold_rang = False
            t = self._drain_wake_timer
            self._drain_wake_timer = threading.Timer(self.DRAIN_HOLD_TTL + 0.5, self._wake_all_inputs)
            self._drain_wake_timer.daemon = True
            nt = self._drain_wake_timer
        if t is not None:
            t.cancel()
        nt.start()
        if first:
            self._log("deploy restart parked: draining — %d in-flight turn(s); new turn starts held "
                      "until this box quiets (queued prompts persist and start after the bounce)"
                      % self.busy_count())
        elif now - self._drain_hold_since > self.DRAIN_LOUD_S and not self._drain_hold_rang:
            with self._lock:
                self._drain_hold_rang = True
            self._log("deploy restart still parked after %d min — %d in-flight turn(s) have not "
                      "finished; new turn starts remain held (the manager's backstop will apply "
                      "the restart regardless)" % (int((now - self._drain_hold_since) / 60),
                                                   self.busy_count()), problem=True)

    def drain_holding(self) -> bool:
        """Whether new turn starts are currently held for a parked deploy restart."""
        with self._lock:
            return self._drain_hold_until > time.time()

    def _wake_all_inputs(self) -> None:
        """Nudge every session's input generator to re-check its gate — the lease just expired
        (holder gone), so held fresh turns must start without waiting for another event."""
        with self._lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            try:
                loop, wake = s.loop, s._input_wake
                if loop is not None and wake is not None:
                    loop.call_soon_threadsafe(wake.set)
            except Exception:
                pass

    def refresh_usage(self):
        """Ask a live connected session for the exact /usage snapshot (get_usage control request). The
        kernel's /usage click calls this so the NEXT read is fresh; per-turn-end refreshes keep the file
        current the rest of the time.

        Every candidate is tried until one accepts, not just the first (the user 2026-08-02): one session
        whose loop has gone away is enough to make a click do nothing at all, and the rail then shows a
        stale reading with no sign that the refresh never happened. Says so in the log when none can be
        asked, rather than returning quietly. API-keyed sessions are not candidates (per-session auth,
        the user 2026-08-08): their get_usage only times out, and the windows belong to the login. A box
        where EVERY live session is keyed says so too (the all-keyed branch) instead of clicking into
        silence — once per episode, not per call: both standing callers repeat (the rail's 60s timer and
        the kernel's 15-minute usage poll, _usage_poll_tick), so the one-shot is load-bearing."""
        with self._lock:
            sessions = list(self.sessions.values())
        connected = [s for s in sessions if s.client and s.loop and not s.ended]
        live = [s for s in connected if not s.api_key_auth]
        if not live:
            # Every live session bills an API key (distinct from "no live sessions at all", which the
            # per-turn refreshes make unremarkable): nobody can poll the subscription windows, and the
            # log line below sat inside `if live:`, so the condition was COMPLETELY silent (the user
            # 2026-08-15). Under a ROMP_EXPECTED_AUTH=key declaration this is the box working as
            # designed — an info line; anything else rings: undeclared (the surprising case), and a
            # declared-LOGIN box gone all-keyed, which CONTRADICTS its own declaration — `not
            # _expected_auth()` read that contradiction as quiet, muting the exact state the
            # declaration exists to flag.
            if connected and not self._usage_all_keyed:
                self._usage_all_keyed = True
                self._log("usage refresh: %d live session(s), all billing API keys — no session can "
                          "poll the subscription windows, so rate-limit telemetry is unavailable"
                          % len(connected), problem=_declared_auth(self.state_dir)[0] != "key")
            return
        if any(s.auth_live == "login" for s in live):
            # Re-armed only by an init that CONFIRMED a login — a fresh spawn's default-False flag
            # means "unknown", not "login", and re-arming on it re-rang once per session creation.
            self._usage_all_keyed = False
        for s in live:
            if s.refresh_usage():
                return
        self._log("usage refresh: %d live session(s), none with a loop to run it on — the rail bars "
                  "keep their last reading" % len(live), problem=True)

    def _record_spend(self, cost, usage=None, keyed=False, sid=None) -> None:
        """Accumulate a turn's total_cost_usd AND its token counts into spend.json, keyed by LOCAL date —
        the rail's spend readout where the subscription bars sat, under API-key auth (the user
        2026-08-04; tokens added the same day, who wanted them beside the dollars). Recorded on every
        result regardless of auth (subscription results report a computed cost too; the DISPLAY is gated
        on the auth mode, the record is not — flipping auth mid-day keeps the number honest). A turn
        billed to an API KEY (keyed=True — the session's own init said so) additionally folds into the
        bucket's `key` sub-counters: with per-session auth a host holds both kinds at once, and the
        rail's API readout must sum ONLY the key's turns — a login turn's computed cost there would be
        dollars nobody is billed (the user 2026-08-08). Token fields mirror the ResultMessage usage
        dict: input/output plus the two cache flavors, kept separately so the tooltip can break them
        down. Pruned to the last 90 days; atomic.
        PER-SESSION ATTRIBUTION (T100, the nightly optimizer's accepted ask 2026-08-24: key-billed
        cost per session — 63%% of a day was untraceable): each bucket carries a `bySid` sub-map,
        {sid: {usd, turns, tok, key:{usd,turns,tok}}} — SID-keyed (rename-proof; names flap), the
        keyed split carried PER SID because that split is the point. Living inside the buckets, the
        maps inherit the prune and the atomic write; rows without bySid stay readable (lossless
        legacy, the T18 discipline). Inherited edge, unrecoverable here: a restart-killed turn
        never emits its ResultMessage, so its cost is missing from every dimension alike."""
        if not isinstance(cost, (int, float)) or cost <= 0:
            return
        u = usage if isinstance(usage, dict) else {}
        def _tok(k):
            v = u.get(k)
            return int(v) if isinstance(v, (int, float)) else 0
        day = time.strftime("%Y-%m-%d")
        hour = time.strftime("%Y-%m-%dT%H")
        with self._rl_lock:
            p = self.state_dir / "spend.json"
            try:
                d = json.loads(p.read_text())
                days = d.get("days") if isinstance(d, dict) and isinstance(d.get("days"), dict) else {}
                hours = d.get("hours") if isinstance(d, dict) and isinstance(d.get("hours"), dict) else {}
            except Exception:
                days, hours = {}, {}

            def _fold(buckets, key, keep):
                e = buckets.get(key) if isinstance(buckets.get(key), dict) else {}
                n = {"usd": round(float(e.get("usd") or 0) + float(cost), 6),
                     "turns": int(e.get("turns") or 0) + 1,
                     "tokIn": int(e.get("tokIn") or 0) + _tok("input_tokens"),
                     "tokOut": int(e.get("tokOut") or 0) + _tok("output_tokens"),
                     "tokCacheR": int(e.get("tokCacheR") or 0) + _tok("cache_read_input_tokens"),
                     "tokCacheW": int(e.get("tokCacheW") or 0) + _tok("cache_creation_input_tokens")}
                ke = e.get("key") if isinstance(e.get("key"), dict) else {}
                tok_total = sum(_tok(k) for k in ("input_tokens", "output_tokens",
                                                  "cache_read_input_tokens", "cache_creation_input_tokens"))
                if keyed or ke:   # carry an existing key split forward even on a login turn
                    n["key"] = {"usd": round(float(ke.get("usd") or 0) + (float(cost) if keyed else 0), 6),
                                "turns": int(ke.get("turns") or 0) + (1 if keyed else 0),
                                "tok": int(ke.get("tok") or 0) + (tok_total if keyed else 0)}
                by = e.get("bySid") if isinstance(e.get("bySid"), dict) else {}
                if sid:
                    se = by.get(sid) if isinstance(by.get(sid), dict) else {}
                    sn = {"usd": round(float(se.get("usd") or 0) + float(cost), 6),
                          "turns": int(se.get("turns") or 0) + 1,
                          "tok": int(se.get("tok") or 0) + tok_total}
                    ske = se.get("key") if isinstance(se.get("key"), dict) else {}
                    if keyed or ske:   # the keyed split rides each sid too — carried forward on login turns
                        sn["key"] = {"usd": round(float(ske.get("usd") or 0) + (float(cost) if keyed else 0), 6),
                                     "turns": int(ske.get("turns") or 0) + (1 if keyed else 0),
                                     "tok": int(ske.get("tok") or 0) + (tok_total if keyed else 0)}
                    by[sid] = sn
                if by:   # a sid-less fold (a non-rail caller) must not drop the attribution already there
                    n["bySid"] = by
                buckets[key] = n
                for k in sorted(buckets)[:-keep]:
                    buckets.pop(k, None)

            _fold(days, day, 90)      # the daily ledger: month-to-date + history
            _fold(hours, hour, 192)   # hour buckets, 8 days — the rolling 5h/7d windows read these
            try:
                tmp = self.state_dir / "spend.json.tmp"
                tmp.write_text(json.dumps({"days": days, "hours": hours}))
                os.replace(tmp, p)
            except Exception as ex:
                self._log("spend record failed: %s" % ex)

    def _note_auth_source(self, sess, source) -> None:
        """An init message named HOW its CLI authenticates — a PER-SESSION fact, not a backend one (the
        user 2026-08-08): with a real ANTHROPIC_API_KEY in the service env, only the sessions whose
        project had approved the key used it, while every other session rode the subscription login —
        yet the old backend-global flag let whichever init arrived LAST speak for all of them, wiping
        the login's usage windows and re-wiping them on every keyed session's reconnect. The flag now
        lives on the session: it gates that session's own get_usage polls (which only time out on
        API-key auth) and its RateLimitEvents (which describe the KEY's limits, not the login's
        windows) — see _do_refresh_usage, refresh_usage, and the _on_message rate-limit branch. The
        subscription windows belong to the LOGIN, whose lifecycle the kernel already tracks read-side:
        the acct stamp drops bars when the login changes, and a machine with NO login shows spend
        (kernel _usage() keys that on the credential store now, not on a wipe marker written here).

        A subscription login answers with the ABSENCE of an API key, and the CLI has said that two
        ways: the field simply absent (verified live 2026-08-04), and — since about CLI 2.1.222 — the
        literal string 'none' (two hosts' journals, 2026-08-08). Logged once per per-session change,
        so every host's kernel log still self-documents who authenticates how.

        The mismatch check compares against ROMP_EXPECTED_AUTH when the box declares one
        (_expected_auth) and the session carries no explicit per-session pick — a pick outranks
        the declaration — else against _launched_keyed as before; see the comment at the check."""
        keyed = bool(source) and str(source).strip().lower() != "none"
        # The CLI landed on a DIFFERENT auth than EXPECTED — the expected side is the box-wide
        # ROMP_EXPECTED_AUTH declaration when one is set (an apiKeyHelper box injects no key at
        # launch, so _launched_keyed said "login" while key auth was the design and every init rang
        # a false alarm — the user 2026-08-15), else what _options actually launched with (a key
        # found some other way — apiKeyHelper, a project setting — or a login where the key was
        # expected). Either way the unexpected landing is a session billing the wrong account, the
        # one failure this feature must never let pass silently (the user 2026-08-08). Flagged on
        # every init that disagrees, not just flips, and into the problems ring so the Log panel
        # shows it; a landing that MATCHES the declaration is the intended, quiet state.
        # An EXPLICIT per-session Billing pick outranks the declaration: the declaration describes
        # the box's UNPICKED design, while set_auth's contract is that the next init confirms the
        # PICK — judged (and worded) against what the pick launched, so a landing honoring the pick
        # stays quiet whatever the box declares, and one contradicting it still rings.
        exp, exp_src = ("", "") if sess.auth in ("login", "key") else _declared_auth(self.state_dir)
        if keyed != ((exp == "key") if exp else sess._launched_keyed):
            if exp:
                what = ("ROMP_EXPECTED_AUTH=%s" % exp) if exp_src == "env" \
                    else ("the remembered Billing pick is %s" % exp)
                self._log("auth (%s): %s but the CLI reports apiKeySource=%r — this "
                          "session is billing the %s. Check the helper and service.env."
                          % (sess.name, what, source, "API key" if keyed else "login"), problem=True)
            else:
                self._log("auth (%s): launched for %s but the CLI reports apiKeySource=%r — this session "
                          "is billing the %s. Check the login (claude /login) and service.env."
                          % (sess.name, "the API key" if sess._launched_keyed else "the login", source,
                             "API key" if keyed else "login"), problem=True)
        sess.auth_live = "key" if keyed else "login"   # the CLI's own report, for the Billing row
        if keyed == sess.api_key_auth:
            return
        sess.api_key_auth = keyed
        # Persisted with the flip: runtime-only, a kernel restart reset a keyed session to False and
        # its rate-limit events landed in the login's usage.json until the next init corrected it —
        # exactly the max-merge contamination the per-session gate exists to prevent.
        self._update_reg(sess.sid, apiKeyAuth=keyed)
        self._log("auth (%s): apiKeySource=%r — %s" % (sess.name, source,
                  "this session bills an API key: its usage polls and rate-limit events are ignored"
                  if keyed else "subscription auth: this session's usage polls resume"))

    def _record_usage_snapshot(self, r) -> None:
        """Fold a get_usage control-request snapshot into usage.json — EXACT utilization for every window,
        the /usage screen's own data (2026-07-02). rate_limits.limits[] maps: kind session → five_hour,
        weekly_all → seven_day, weekly_scoped whose scope.model.display_name says Fable → fable (the
        included Fable 5 weekly allowance — the window NO other in-band source carries: RateLimitEvents
        attach a number only in the warning band and the statusline payload lacks the window entirely).
        resets_at arrives ISO-8601 → stored as epoch. This is an authoritative FULL snapshot, so mapped
        windows are overwritten outright (the merge games in _record_rate_limit are for partial sources);
        windows the snapshot doesn't carry keep their file value. Writes only on change (t stays the time
        of the last real reading — the rail tooltip's "updated … ago")."""
        rl = r.get("rate_limits") if isinstance(r, dict) else None
        lims = rl.get("limits") if isinstance(rl, dict) else None
        if not isinstance(lims, list):
            return

        def _epoch(v):
            if isinstance(v, (int, float)):
                return int(v)
            try:
                from datetime import datetime
                return int(datetime.fromisoformat(str(v)).timestamp())
            except Exception:
                return None
        out = {}
        for l in lims:
            if not isinstance(l, dict) or not isinstance(l.get("percent"), (int, float)):
                continue
            kind = l.get("kind")
            scope_model = str((((l.get("scope") or {}).get("model") or {}).get("display_name")) or "")
            key = ("five_hour" if kind == "session"
                   else "seven_day" if kind == "weekly_all"
                   else "fable" if (kind == "weekly_scoped" and "fable" in scope_model.lower())
                   else None)
            if key:
                out[key] = {"pct": max(0, min(100, round(l["percent"]))), "resets_at": _epoch(l.get("resets_at"))}
        if not out:
            return
        with self._rl_lock:
            try:
                cur = json.loads((self.state_dir / "usage.json").read_text())
                if not isinstance(cur, dict):
                    cur = {}
            except Exception:
                cur = {}
            data = {"t": int(time.time()),
                    "acct": acct_digest(),   # whose reading this is — the kernel drops the bars the moment that login is gone
                    "five_hour": out.get("five_hour", cur.get("five_hour")),
                    "seven_day": out.get("seven_day", cur.get("seven_day")),
                    "fable": out.get("fable", cur.get("fable"))}
            if all(data[k] == cur.get(k) for k in ("five_hour", "seven_day", "fable")):
                return                                # no change → keep t honest
            try:
                tmp = self.state_dir / "usage.json.tmp"
                tmp.write_text(json.dumps(data))
                os.replace(tmp, self.state_dir / "usage.json")
            except Exception:
                self._log("usage.json write failed: %s" % traceback.format_exc())   # never silent
                return
            self._record_usage_history(data)
        self._poke()

    def _record_usage_history(self, data, now=None) -> None:
        """Append the reading to usage-history.json — the per-window utilization ledger (the user
        2026-08-13: the window bars kept only the CURRENT snapshot, so nothing could be graphed).
        Nothing renders it today — the per-window hover sparks it fed were removed (the user
        2026-08-14, who wanted only the one fleet $/h graph) — but it keeps recording so a future
        utilization graph starts with history instead of a blank. Same bounded-hours
        shape as spend.json so both series share one x-axis: per hour keep the MAX pct seen per window
        (utilization only climbs within a window; a ROLL is a new resets_at, which takes the fresh
        reading outright), pruned to 192 hours — 8 days covers the 7-day graph. Caller holds _rl_lock;
        a write failure logs and never blocks the snapshot that fed it. `now` stamps the hour bucket,
        injectable so a frozen-clock caller (tests) never straddles an hour boundary mid-assertion."""
        p = self.state_dir / "usage-history.json"
        try:
            hist = json.loads(p.read_text())
        except Exception:
            hist = {}
        hours = hist.get("hours") if isinstance(hist, dict) and isinstance(hist.get("hours"), dict) else {}
        hour = time.strftime("%Y-%m-%dT%H", time.localtime(time.time() if now is None else now))
        ent = hours.get(hour) if isinstance(hours.get(hour), dict) else {}
        ent["acct"] = data.get("acct") or ""
        for k in ("five_hour", "seven_day", "fable"):
            s = data.get(k)
            if not (isinstance(s, dict) and isinstance(s.get("pct"), (int, float))):
                continue
            prev = ent.get(k) if isinstance(ent.get(k), dict) else None
            if not prev or s.get("resets_at") != prev.get("ra") or int(s["pct"]) >= int(prev.get("pct") or 0):
                ent[k] = {"pct": int(s["pct"]), "ra": s.get("resets_at")}
        hours[hour] = ent
        for k in sorted(hours)[:-192]:
            hours.pop(k, None)
        try:
            tmp = self.state_dir / "usage-history.json.tmp"
            tmp.write_text(json.dumps({"hours": hours}))
            os.replace(tmp, p)
        except Exception:
            self._log("usage-history.json write failed: %s" % traceback.format_exc())

    def _record_rate_limit(self, info) -> None:
        """Persist the account-wide rate-limit /usage the CLI streams as a RateLimitEvent — the SDK's DESIGNED
        source for the rail's usage bars (the user 2026-06-30) — into the SAME usage.json the tmux statusline
        writes. Status-aware (the user 2026-07-02): 19h of cadence instrumentation showed the CLI attaches
        `utilization` ONLY in the allowed_warning band (4 of 452 events); plain `allowed` and even `rejected`
        events carry utilization=None. The old util-only path silently dropped 97% of events — including every
        REJECTED one, so romp never showed "limit reached" (the user hit the session limit without realizing),
        and after an account switch nothing ever replaced the old account's reading (bars stuck). Every event
        DOES always carry `status` + `resets_at`, so use those:

        - `resets_at` names the LIVE window for that limit type, straight from the running CLI. A file reading
          from a DIFFERENT window (the old account's, or a window that rolled) is dead — replace it, never let
          it out-compete the live one. (The old later-resets_at-wins rule protected the statusline's fresh file
          from this backend's stale ACCUMULATOR replaying hours-old windows; the accumulator is gone — each
          event now touches only its own window — so that guard is obsolete, and across an account switch it
          was exactly what STUCK the bars: the old account's later reset beat the new account's live window.)
          "Different window" is decided by _same_window, NOT by equality: romp's two sources date the SAME
          window differently (the events read API response headers, the get_usage snapshot reads the /usage
          endpoint), and on 2026-08-02 they sat 10 minutes apart on the 5h window. Under equality every
          event after a snapshot read as a fresh roll and reset the bar to 0, so the exact 45% the snapshot
          had just written was wiped within a second and the rail crept back up from zero on the rare
          warning-band events — the user's bar read 18% against a real 45%.
        - `rejected` IS the limit: pct=100 even with utilization=None → the rail's limited banner + retry-pause
          engage (previously this event was dropped and romp missed the limit entirely).
        - `allowed` with unknown utilization in a brand-new window reads pct=0 (a window rolls with ~0 usage;
          an account switch lands near the statusline's next fresh write). Within the SAME window, an unknown
          utilization keeps the file's pct (usage only climbs in-window; statusline/warning events refine it),
          and a KNOWN pct merges as max(file, event). An `allowed` event also proves we are NOT limited, so a
          same-window pct that claims 100 is capped to 99 — a stale limited banner can't outlive the CLI
          saying "allowed".

        The write happens only when the merged segment actually CHANGES, so usage.json's `t` (the rail
        tooltip's "updated … ago") stays the time of the last real reading, not of the last no-op event."""
        rlt = getattr(info, "rate_limit_type", None)
        # `seven_day_overage_included` is the included-Fable-5 weekly allowance (the CLI's /usage labels it
        # "Fable 5 limit", 2026-07-02) → the rail's third bar. It rides usage.json as `fable`; the tmux
        # statusline payload carries ONLY five_hour/seven_day, so these events are its one in-band source.
        if rlt not in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet",
                       "seven_day_overage_included"):
            return   # overage / unknown windows are ignored (no bar shows them)
        key = ("five_hour" if rlt == "five_hour"
               else "fable" if rlt == "seven_day_overage_included" else "seven_day")
        status = getattr(info, "status", None)
        util = getattr(info, "utilization", None)
        ra = getattr(info, "resets_at", None)
        ra = int(ra) if isinstance(ra, (int, float)) else None
        pct = None
        if isinstance(util, (int, float)):
            pct = max(0, min(100, round(util * 100)))
        elif status == "rejected":
            pct = 100                                 # rejected IS 100%: the CLI sends no utilization with it
        with self._rl_lock:                           # one read-merge-write at a time (many sessions stream events)
            try:
                cur = json.loads((self.state_dir / "usage.json").read_text())
                if not isinstance(cur, dict):
                    cur = {}
            except Exception:
                cur = {}
            seg = cur.get(key) if isinstance(cur.get(key), dict) else None
            cur_ra = seg.get("resets_at") if seg and isinstance(seg.get("resets_at"), (int, float)) else None
            if ra is not None and seg is None:
                new = {"pct": pct if pct is not None else 0, "resets_at": ra}   # nothing on file to reconcile with
            elif ra is not None and cur_ra is not None and not _same_window(ra, cur_ra):
                new = {"pct": pct if pct is not None else 0, "resets_at": ra}   # a real roll: this window is new
            elif seg:
                # SAME window — either the event names no window, or its stamp is the same window said
                # differently (see _same_window). Keep the file's stamp and let usage climb, never fall.
                new = dict(seg)
                if cur_ra is None and ra is not None:
                    new["resets_at"] = ra             # the file simply had no stamp yet; adopt this one
                if pct is not None:
                    new["pct"] = max(int(new.get("pct") or 0), pct)   # in-window usage only climbs
            elif pct is not None:
                new = {"pct": pct, "resets_at": ra}
            else:
                return                                # no file seg, no pct, no window id → nothing to say
            if status == "allowed" and (new.get("pct") or 0) >= 100:
                new["pct"] = 99                       # the CLI says allowed → we are provably NOT limited
            if new == seg:
                return                                # no change → keep t honest (the tooltip's "updated … ago")
            cur[key] = new
            data = {"t": int(time.time()), "acct": acct_digest(),   # whose reading — see the snapshot writer
                    "five_hour": cur.get("five_hour"),
                    "seven_day": cur.get("seven_day"), "fable": cur.get("fable")}
            try:
                tmp = self.state_dir / "usage.json.tmp"
                tmp.write_text(json.dumps(data))
                os.replace(tmp, self.state_dir / "usage.json")
            except Exception:
                self._log("usage.json write failed: %s" % traceback.format_exc())   # never silent (the user 2026-07-02)
                return
            self._record_usage_history(data)
        self._poke()   # nudge the producer so the rail re-reads usage.json promptly, not on the next backstop

    # ---- logging / wakeups ----
    PROBLEM_RING = 100        # how many backend problems are kept for the dashboard (oldest dropped)

    def _log(self, m, problem=None):
        """Every backend line goes to the kernel log; the ones that report a FAILURE also land in a ring
        the dashboard's error center reads, so an SDK problem shows up where the user is looking instead
        of only in a file nobody tails (the user 2026-07-28, who could tell exceptions were happening and
        was never shown any).

        What counts as a problem is the exact event, not a keyword sniff on the message: a line logged
        while an exception is being handled IS that exception's report. sys.exc_info() is live for the
        whole dynamic extent of an `except` block — helpers called from inside one included — so the
        classification follows the code path that produced the line. `problem=` overrides either way."""
        if problem is None:
            problem = sys.exc_info()[0] is not None
        if problem:
            with self._problem_lock:
                self._problem_seq += 1
                self._problems.append({"seq": self._problem_seq, "t": time.time(), "text": str(m)})
                if len(self._problems) > self.PROBLEM_RING:
                    del self._problems[:-self.PROBLEM_RING]
        if self._log_cb:
            self._log_cb(m)

    def problem_seq(self) -> int:
        """How many problems this backend has recorded — a cheap cache key for the view builders, so a
        fresh failure busts the feed on the event instead of riding the next unrelated change."""
        with self._problem_lock:
            return self._problem_seq

    def problems(self, limit: int = 0) -> list[dict]:
        """The recorded problems, oldest first (a copy — callers must not mutate the ring)."""
        with self._problem_lock:
            rows = list(self._problems)
        return rows[-limit:] if limit else rows

    def _poke(self):
        if self._poke_cb:
            try:
                self._poke_cb()
            except Exception as e:
                self._log("producer wake failed: %s" % e)

    # ---- SDK option assembly (mirrors the tmux launch flags) ----
    def _options(self, sess: SdkSession, ClaudeAgentOptions):
        from claude_agent_sdk import HookMatcher
        kw = dict(
            cli_path=self.claude_bin,
            cwd=sess.cwd,
            # The repo's bin/ on the CLI's PATH — the postal MCP command (`romp-postal-service`) and
            # the mail CLI the inbox footer suggests both resolve from it, and a kernel started from a
            # non-login shell (the attach bootstrap's ssh) hands down a PATH with neither (the
            # 2026-07-27 federation shakedown: a remote session's `romp mail send` died
            # command-not-found and re-prompted for permission on the absolute-path retry). options.env
            # merges OVER the inherited environment in the SDK's transport, so this is additive.
            # ROMP_SID gives the CLI process (and every Bash it runs) the session's STABLE identity —
            # what lets `romp end self` resolve itself to the kernel (the user 2026-08-15). And
            # ROMP_SESSION_NAME gives child processes the session's human NAME (the user 2026-08-16):
            # a generic identity surface, deliberately coupled to no consumer. Env is spawn-frozen, so
            # a rename after spawn is NOT reflected here — the sid stays the stable identity; the name
            # is a spawn-time label, right for attribution and logging, wrong for addressing.
            env={**_bin_on_path_env(os.environ), "ROMP_SID": str(sess.sid),
                 "ROMP_SESSION_NAME": str(sess.name)},
            # Registering this is what makes the CLI's stderr EXIST for romp at all: the SDK transport
            # pipes the child's stderr only when options.stderr is set (otherwise it hands the child
            # our own stderr and reports SDK_STDERR_PLACEHOLDER on failure). Without it, a CLI that
            # refuses to start takes its reason to the grave — see _on_cli_stderr.
            stderr=sess._on_cli_stderr,
            can_use_tool=sess._can_use_tool,
            hooks={"Stop": [HookMatcher(matcher=None, hooks=[sess._stop_hook])],          # awaiting overlay producer
                   # recurring-cron replay gate: a resumed CLI re-fires passed slots (T211)
                   "UserPromptSubmit": [HookMatcher(matcher=None, hooks=[sess._prompt_submit_hook])],
                   "SubagentStart": [HookMatcher(matcher=None, hooks=[sess._subagent_start_hook])],  # live subagent
                   "SubagentStop": [HookMatcher(matcher=None, hooks=[sess._subagent_stop_hook])],    #   count/types
                   # scheduling tools record their arm at the CALL moment, with the exact due time the
                   # tool input carries — the Stop hook's set-record reconciles (see _sched_tool_hook)
                   "PostToolUse": [HookMatcher(matcher="ScheduleWakeup|CronCreate|CronDelete",
                                               hooks=[sess._sched_tool_hook]),
                                   # launch ledger: exact launch facts at the call moment (2026-08-29)
                                   HookMatcher(matcher="Bash|Monitor|TaskStop",
                                               hooks=[sess._ledger_tool_hook]),
                                   # interaction facts: store-poke + attribution, push mirror, role
                                   HookMatcher(matcher="TaskCreate|TaskUpdate|PushNotification|Skill",
                                               hooks=[sess._facts_tool_hook])],
                   # a launch whose ack errored never started — drop it before it phantom-waits
                   "PostToolUseFailure": [HookMatcher(matcher="Bash|Monitor",
                                                      hooks=[sess._ledger_fail_hook])]},
            permission_mode=sess.mode,
            # File-checkpoint rewind (the user 2026-08-04): the CLI backs files up before modifying them,
            # so rewind_files() can put the workspace back to its state at any user message. The uuid a
            # restore takes comes from the TRANSCRIPT (romp's own parse) — the replay-user-messages extra
            # flag exists only for SDK consumers with no transcript access, so it stays off.
            enable_file_checkpointing=True,
            include_partial_messages=False,
            # connect-time --effort (no runtime control); a change reconnects. "ultracode" is not a typed
            # EffortLevel — it rides as xhigh + the `ultracode` settings key (added below).
            effort=("xhigh" if (sess.effort or DEFAULT_EFFORT) == "ultracode" else (sess.effort or DEFAULT_EFFORT)),
            max_buffer_size=SDK_MAX_BUFFER,   # a >1MB stdout message would crash the receive loop → kill any live picker
        )
        # romp's harness prompt is APPENDED via the SDK's DESIGNED system_prompt field — the Claude Code preset
        # plus an `append` (types.py SystemPromptPreset) — NOT extra_args={"append-system-prompt"}. Same effect
        # (append to the default Claude Code system prompt) but it's the typed, documented option; extra_args is
        # the SDK's last-resort passthrough for CLI flags that have NO field, which this one does (the user
        # 2026-06-24: implement things the way the SDK designed them, not via raw-flag escape hatches).
        if self.append_prompt_path and os.path.exists(self.append_prompt_path):
            try:
                kw["system_prompt"] = {"type": "preset", "preset": "claude_code",
                                       "append": Path(self.append_prompt_path).read_text()}
            except OSError as e:
                self._log("system-prompt append unreadable (%s) — sessions start WITHOUT it: %s" % (self.append_prompt_path, e))
        if sess.chosen_model and sess.chosen_model != "default":
            kw["model"] = sess.chosen_model    # keep the picked model across a reconnect (runtime set_model is per-connection)
        if sess.resume_sid:
            kw["resume"] = sess.resume_sid
            if sess._fork_of:
                # A FORK resume (backend.fork): fork_session makes the CLI copy the loaded conversation
                # into a NEW session instead of continuing the parent's, and session_id pins the new
                # fsid to the romp sid (the SDK's typed contract: session_id combines with resume only
                # under fork_session — types.py). The optional cut rides the CLI's designed
                # --resume-session-at, same passthrough the rewind uses.
                kw["fork_session"] = True
                kw["session_id"] = sess.sid
                if sess._fork_at:
                    kw.setdefault("extra_args", {})["resume-session-at"] = sess._fork_at
        else:
            kw["session_id"] = sess.sid
        # A pending conversation REWIND (the chat's edit-message branch) rides --resume-session-at —
        # the SDK has no typed field for it, so extra_args (the SDK's designed passthrough for exactly
        # this) carries it. ONE-SHOT, event-guarded (rewind_disposition): applied only while the
        # transcript's leaf is STILL the one recorded at request time; the moment any record lands past
        # it (the rewind turn itself, normally) the flag is SPENT — re-applying would truncate real work
        # (a crash-heal resume mid-rewind-turn must not re-rewind). _rewind_armed releases the held edit
        # turn to THIS client (the inputs() gate).
        sess._rewind_armed = False
        disp = rewind_disposition(sess._rewind_to, sess._rewind_leaf,
                                  last_record_uuid(transcript_path(sess.cwd, sess.resume_sid or sess.sid)))
        if disp == "apply":
            kw.setdefault("extra_args", {})["resume-session-at"] = sess._rewind_to   # merge-safe beside the fork's args
            sess._rewind_armed = True
        elif disp == "spent":
            sess._rewind_to = sess._rewind_leaf = ""
            sess._rewind_bare = False
            sess._rewind_wait = False       # a kernel death mid-delete-window lands here: loud spent
            #                                 restore, never a wrong-branch cut — the wait dies with the flags
            try:
                self._update_reg(sess.sid, rewindTo="", rewindLeaf="", rewindBare=False,
                                 rewindWait=False)
            except Exception as e:
                self._log("rewind (%s): registry clear failed: %s" % (sess.name, e))
            self._log("rewind (%s): conversation moved since the request — flag spent, resuming plainly"
                      % sess.name)
            # ambiguous consumption: the leaf moved either because the take landed pre-crash or
            # because the old branch grew — the kernel discriminates by the recorded leaf's chain
            # membership and archives or restores the held cards accordingly
            self._rewind_resolved(sess.sid, "spent")
        if self.mcp_config:
            kw["mcp_servers"] = self.mcp_config
        # The flag-settings layer carries the two keys the SDK has no typed field for — ultracode
        # (effort) and fastMode. Both are connect-time, which is why changing either reconnects.
        fs = flag_settings_path(self.state_dir, sess.sid,
                                ultracode=(sess.effort or "") == "ultracode", fast=sess.fast_opt)
        if fs:
            kw["settings"] = fs
        # Per-session auth (the user 2026-08-08): the work key was claimed OUT of this process's env at
        # startup (work_api_key), so a login session's CLI inherits a clean environment and finds the
        # login on its own; a key session gets the key injected here, explicitly. options.env merges
        # OVER the inherited env in the SDK's transport, which is exactly the one-way door we need —
        # inject or stay silent; never blank (an empty var reads as "API-key mode, no key" to the CLI).
        launch_keyed = sess.effective_auth() == "key"
        if launch_keyed:
            kw["env"] = dict(kw["env"], ANTHROPIC_API_KEY=self.work_key,
                             **key_fast_org_env(self.work_key, self._log))
        elif sess.auth == "key":
            # picked "key", but this manager's env carries none — falling to login silently would bill
            # the wrong account with nothing to see; say so where the Log panel shows it.
            self._log("auth (%s): session is set to the API key but the manager environment carries "
                      "none (service.env) — launching on the login instead" % sess.name, problem=True)
        sess._launched_keyed = launch_keyed
        return ClaudeAgentOptions(**kw)

    # ---- lifecycle (kernel-thread API) ----
    def spawn(self, name: str, cwd: str, bg: str = "", fg: str = "", sid: str | None = None,
              auth: str = "") -> str:
        sid = sid or str(uuid.uuid4())
        cwd = os.path.realpath(cwd) if os.path.exists(cwd) else cwd
        if not bg:                                   # give the session a stable identity colour like tmux sessions get
            bg, fg = pick_identity_color(sid, self.state_dir)
        write_name(self.state_dir, sid, name, cwd, bg, fg)
        # Seed model + effort from the REMEMBERED defaults (the user's last pick on any session), falling back
        # to the hardcoded ones (the user 2026-06-27). effort always has a value (the connect flag). A model is
        # recorded ONLY when a real choice was remembered: an unset / 'default' model stays the account default
        # (model_label + _options both treat '' and 'default' as "no override"), and the real default name still
        # fills in on connect from get_context_usage(). The seed lands in THIS session's reg — exactly what
        # _options launches with and what the badge reads — so the display can never desync from what's used.
        d = read_sdk_defaults(self.state_dir)
        eff = d.get("effort") if d.get("effort") in EFFORT_LEVELS else DEFAULT_EFFORT
        mode = d.get("mode") or "acceptEdits"   # seed the permission mode from the remembered default too (the user 2026-06-27)
        reg = {"sid": sid, "name": name, "cwd": cwd, "mode": mode,
               "effort": eff, "lastSid": "", "alive": True}
        if d.get("model") and d["model"] != "default":
            reg["model"] = d["model"]
        # Auth: the picker's explicit pick wins; else the remembered default (a gear /auth pick on any
        # session); unset stays unset — effective_auth's fallback IS the pre-selector behavior.
        a = auth if auth in ("login", "key") else (d.get("auth") if d.get("auth") in ("login", "key") else "")
        if a:
            reg["auth"] = a
        self._write_reg_locked(sid, reg)
        append_state(self.state_dir, sid, "waiting")
        self._poke()
        return sid

    def fork(self, name: str, parent_sid: str, cut_uuid: str = "", bg: str = "", fg: str = "",
             sid: str | None = None, thread_of: str = "", model: str = "", effort: str = "",
             fast: str = "") -> str:
        """Mint a NEW session that is a FORK of `parent_sid`'s conversation — up to `cut_uuid` when given
        (a transcript record uuid; empty = the whole conversation), the parent untouched either way (the
        user 2026-08-13). The new session gets its OWN sid, registry, name and identity colour — a fork
        with the parent's title would be discovered as a fork LANE of the parent and hidden. Mechanism:
        the reg is born with lastSid = the parent's newest fsid plus forkOf/forkAt, which _options turns
        into resume + fork_session + session_id (+ resume-session-at) on first connect; the init's
        lastSid flip to this sid spends the flags. Model / effort / mode / auth inherit from the parent —
        it is that conversation, continued elsewhere. Resumable by construction: a fork whose CLI dies
        before init still carries the flags and retries on the next connect.

        `thread_of` (the user 2026-08-13, who asked to comment on a highlighted passage and keep a side
        conversation there): this fork is a COMMENT THREAD of parent session `thread_of` — a side
        conversation the chat surfaces as an anchored highlight + popover, not as a session of its own.
        The reg carries threadOf and the names/ entry is withheld, so it gets no tab, no lane, no feed
        cards and no judge pass (names/ is the discoverability trigger; live_sessions skips threadOf
        regs for the tab side) — its ENTIRE user surface is the parent chat's comment UI, which is why
        this is not the hidden-running-session failure mode the 2026-08-11 rule removed: every thread is
        visible and reachable right where it was made. Everything else is a normal session: the reg
        keeps its CLI under the boot reconcile's orphan reap, a mid-turn kernel death resumes it, and
        promote_thread() later turns it into a full board session."""
        parent = read_reg(self.state_dir, parent_sid) or {}
        cwd = parent.get("cwd") or os.path.expanduser("~")
        sid = sid or str(uuid.uuid4())      # the kernel pre-mints it so the judge seeds can precede us
        if not bg:
            bg, fg = pick_identity_color(sid, self.state_dir)
        reg = {"sid": sid, "name": name, "cwd": cwd,
               "mode": parent.get("mode", "acceptEdits"),
               "effort": parent.get("effort", DEFAULT_EFFORT),
               "lastSid": parent.get("lastSid") or parent_sid,
               "forkOf": parent_sid, "forkAt": cut_uuid or "", "alive": True}
        # Durable LINEAGE (the user 2026-08-13: branching must SHOW in the UI): forkOf/forkAt above
        # are one-shot launch flags, spent the moment the init lands, so the branch point is recorded
        # separately and forever. A tip fork (no cut) stamps the parent's CURRENT leaf — that is
        # where the two histories diverge. The chat renders this as the child's branch divider and
        # the parent's branch chip (build_session `branch`/`branches`).
        lineage_cut = cut_uuid or last_record_uuid(
            transcript_path(cwd, parent.get("lastSid") or parent_sid))
        reg["forkedFrom"] = {"sid": parent_sid, "name": parent.get("name", ""),
                             "cut": lineage_cut, "t": int(time.time())}
        # Fork-boot OBSERVABILITY (T156, measured no-build): the tail-copy optimization died on
        # evidence — a 46MB resume+fork cost ~5s more than a 4.4MB one; the wall is fixed costs —
        # so instead every fork spend logs its source size + create-to-spend duration (the spend
        # site in _on_message), and a recurrence of 200s-class boots reopens the case with data.
        try:
            reg["forkSrcBytes"] = os.path.getsize(
                transcript_path(cwd, parent.get("lastSid") or parent_sid))
        except OSError:
            reg["forkSrcBytes"] = 0
        # Two probe findings recorded where the fork lives (T156 step-0, 2026-08-28):
        # - PRINT-MODE --resume-session-at refused every record uuid tried (user + assistant,
        #   pre- and post-compaction) with "No message found with message.uuid of:" on CLI
        #   2.1-era — the SDK connect path works (threads fork daily), so the print-mode flag
        #   matches a DIFFERENT id; the first person to fork through `-p` will hit this.
        # - The CLI refuses resume-at targets from before its last compaction boundary by design
        #   (the rewind doc above) — a comment anchored to a pre-compaction turn is a WATCH ITEM
        #   for the SDK path too: live forks work today, but nothing pins that.
        if thread_of:
            reg["threadOf"] = thread_of
        if parent.get("model") and parent["model"] != "default":
            reg["model"] = parent["model"]
        # fast rides the fork like model/effort do (the user 2026-08-25: a comment made from an
        # Opus-high-FAST session came up slow) — the reg's `fast` is the persisted ask fast_opt
        # reads at connect, so the thread's first frame already reports it. `fast` (the user
        # 2026-08-29) is the per-fork override: "" inherits the parent's ask, "on" arms it
        # regardless of the parent, "off" launches plain from a fast one. An arm the CLI refuses
        # (a model /fast won't run) surfaces through _adopt_fast_state's refusal toast and clears
        # the ask — same model, normal speed, never a silent substitute.
        if fast == "on" or (not fast and parent.get("fast")):
            reg["fast"] = True
        # Per-fork model/effort OVERRIDES (the user 2026-08-17: a comment thread on a different model
        # or effort, without touching the parent). Applied HERE, in the reg the first connect reads —
        # never via set_model, whose write_sdk_default side effect would make a thread's pick the seed
        # for every future session. 'default' clears the inherited model back to the account default.
        if model:
            if model == "default":
                reg.pop("model", None)
            else:
                reg["model"] = model
        if effort in EFFORT_LEVELS:
            reg["effort"] = effort
        if parent.get("auth") in ("login", "key"):
            reg["auth"] = parent["auth"]
        self._write_reg_locked(sid, reg)
        # the names/ entry LAST — it is the discoverability trigger (discover() iterates names/), and
        # everything above must exist before any judge pass can see the session. A comment thread never
        # writes it: promote_thread() does, after the kernel seeds the judge stores.
        if not thread_of:
            write_name(self.state_dir, sid, name, cwd, bg, fg)
        append_state(self.state_dir, sid, "waiting")
        self._poke()
        return sid

    def thread_of(self, sid: str) -> str:
        """The parent sid when `sid` is a comment thread, else ''."""
        reg = read_reg(self.state_dir, sid) or {}
        return str(reg.get("threadOf") or "")

    def promote_thread(self, sid: str, name: str, bg: str = "", fg: str = "") -> bool:
        """Break a comment thread out into a FULL board session (the user 2026-08-13: 'a button that
        breaks it out into its own session'). The caller (kernel _comment_promote) must have seeded the
        judge stores FIRST — the names/ write below is the discoverability trigger, same ordering
        contract as fork(). Clears threadOf (the reg becomes an ordinary session's) and registers the
        identity; the running CLI, transcript and queue carry over untouched."""
        with self._reg_lock:                       # the threadOf pop is a listing-visibility flip
            reg = self._reg_for_flip(sid)
            if not reg or not reg.get("threadOf"):
                return False
            reg.pop("threadOf", None)
            reg["name"] = name
            write_reg(self.state_dir, sid, reg)
        if not bg:
            bg, fg = pick_identity_color(sid, self.state_dir)
        write_name(self.state_dir, sid, name, reg.get("cwd", ""), bg, fg)
        s = self.sessions.get(sid)
        if s:
            s.name = name
            s.thread_of = ""   # a promoted session bills ITSELF from this moment (T144's owner billing)
        self._poke()
        return True

    def resume(self, name: str, sid: str, cwd: str | None = None) -> bool:
        """Mark a dormant/dead SDK session alive again so _ensure/connect restarts it. PRESERVE the
        registry (spread) and especially its lastSid when set — lastSid tracks the NEWEST transcript
        fsid (a /clear or relaunch mints new fsids under the same romp sid) and SdkSession resumes from
        it; stamping the original sid here would silently resume an OLD conversation state (the
        picker-revive fix, the user 2026-07-05).

        A REVIVAL PRESERVES IDENTITY (2026-08-17): when the reg already carries a name, that name WINS
        over the caller's — the caller's copy came from the names/ registry or a discovery row, both of
        which other paths rewrite (the tmux launcher's dead-name freeing, fork-lane rows whose "sid" is
        a transcript stem), and trusting it let a machine-panic relaunch respawn a session under a name
        nobody chose while it carried its whole history (the local session incident, 2026-08-17: reg
        name X, caller name Y → revived as Y with X's past, and the spawn-frozen env made Y permanent).
        The caller's name is only ever ADOPTED when the reg has none — the create-from-nothing revive
        of a session this backend has never seen, which is also logged, because a sid with no reg is
        usually a transcript fsid that leaked out of a discovery row rather than a real romp sid."""
        with self._reg_lock:                       # the alive=True flip must not lose to an RMW snapshot
            reg = self._reg_for_flip(sid) or {}
            kept = str(reg.get("name") or "").strip()
            if not reg:
                self._log("resume minting a reg for unknown sid %s as %r — a sid with no reg is usually "
                          "a transcript fsid, not a romp session" % (sid[:13], name))
            cwd = cwd or reg.get("cwd") or os.path.expanduser("~")
            write_reg(self.state_dir, sid, {**reg, "sid": sid, "name": kept or name, "cwd": cwd,
                                            "mode": reg.get("mode", "acceptEdits"),
                                            "effort": reg.get("effort", DEFAULT_EFFORT),
                                            "lastSid": reg.get("lastSid") or sid, "alive": True})
        append_state(self.state_dir, sid, "waiting")
        self._poke()
        return True

    def _ensure(self, sid: str, on_boot_settled=None) -> SdkSession | None:
        """Start (or return the already-running) SdkSession for `sid`. `on_boot_settled` (the boot
        stagger's slot release) is parked on a FRESH spawn and fired once its CLI proves up or dies;
        the no-spawn paths fire it immediately — no CPU burst will ever happen, so no slot is held."""
        def _settled_now():
            if on_boot_settled:
                try:
                    on_boot_settled()
                except Exception as e:
                    self._log("boot-settled callback failed: %s" % e)
        with self._lock:
            s = self.sessions.get(sid)
            if s and s.thread.is_alive():
                _settled_now()
                return s
            reg = read_reg(self.state_dir, sid)
            if not reg or not reg.get("alive"):
                _settled_now()
                return None
            reg["sid"] = sid
            if reg.get("threadOf") and reg.get("spawnedAt") and self.thread_wake_model is not None:
                # A DORMANT comment thread (spawnedAt: it has run before — a fresh fork's FIRST connect
                # keeps the model the dialog explicitly chose) registered on a SUPERSEDED full model
                # id comes up on its family's newest at this explicit wake (T223 rider, the user
                # 2026-09-01) — the one moment a dormant thread may be touched. Persisted with the
                # label the popover reads, so the next wake needs no remap; no chat note (the model
                # badge already shows what it runs on).
                try:
                    nm = self.thread_wake_model(reg.get("model") or "")
                except Exception:
                    nm = None
                if nm and nm != reg.get("model"):
                    self._log("thread %s wakes on %s (registered on superseded %s)"
                              % (sid[:8], nm, reg.get("model")))
                    reg["model"] = nm
                    reg["liveModel"] = _alias_label(nm)
                    self._update_reg(sid, model=nm, liveModel=_alias_label(nm))   # _reg_lock, not self._lock
            s = SdkSession(self, reg)
            s.on_boot_settled = on_boot_settled
            self.sessions[sid] = s
            s.start()
            return s

    def connect(self, sid: str) -> bool:
        """Eagerly start (connect) a session WITHOUT sending a turn, so its model + context % publish right
        away — like a tmux session shows them on launch — instead of only after the first message (the user
        2026-06-23). The streaming `init` SystemMessage does NOT arrive until the first turn, so _amain pulls
        the model + % on connect via get_context_usage() (the designed control request that answers pre-turn);
        permission-mode shows the registry default immediately, refined by init once a turn lands. Idempotent;
        a no-op if already running."""
        return self._ensure(sid) is not None

    def pending_queued(self, sid: str) -> list[str]:
        """Queued-but-not-yet-started user turns for an SDK session (oldest first), or [] if the
        session isn't SDK-backed / not running. The kernel calls this to build the chat's
        kind:"queued" event for SDK sessions — the SDK keeps its queue in memory, so there are no
        transcript queue-operation records for _pending_queued to read.

        A session that is NOT running falls back to the PERSISTED queue (the reg mirror _persist_queue
        writes on every mutation). That is not a guess: it is the same list, and it is what the boot
        reconcile re-delivers, so it is exactly what is still owed to the user. Returning [] there made
        the message VANISH from the chat the moment the CLI died — which is how a send into an
        out-of-usage account looked like romp had eaten it (the user 2026-07-28). While the session IS
        running the in-memory queue stays authoritative: the mirror trails it by one write."""
        with self._lock:
            s = self.sessions.get(sid)
        if s:
            return s.pending()
        try:
            q = (read_reg(self.state_dir, str(sid)) or {}).get("queue")
        except Exception:
            return []
        return [t for t in q if isinstance(t, str) and t] if isinstance(q, list) else []

    def unqueue(self, sid: str, idx: int, expect: str | None = None) -> str | None:
        """Cancel the queued turn at `idx` for an SDK session (the kernel's cancelQueued route). Returns
        its text, or None on a MISS — the message already left the queue (handed to the CLI, no recall
        exists), and the caller must surface that loudly rather than show a fake delete (the user
        2026-07-20). tmux has no equivalent (its queue lives in Claude Code), so only SDK sessions
        expose this — the kernel gates the chat's cancel affordance on the backend having `unqueue`.
        `expect` (the exact queued text the click meant) is re-verified under the session lock.

        ALSO drops the message's optimistic echo from the live tail: send() adds a blue 'you' bubble that
        normally prunes when the real user atom lands in the transcript — but a CANCELED message never
        lands, so without this the echo lingered and the canceled message kept rendering as 'sent' even
        though it wasn't (the user 2026-06-27)."""
        with self._lock:
            s = self.sessions.get(sid)
        if not s:
            return None
        text = s.unqueue(idx, expect)
        if text is not None:
            live = self._live.get(sid) or {}
            for k, a in list(live.items()):                # snapshot: the live-tail thread may mutate concurrently
                if a.get("_echo_text") == text:
                    live.pop(k, None)                      # one echo per canceled message
                    break
            self._persist_echoes(sid)                      # the canceled echo leaves the restart mirror too
            self._wake_push()                              # repaint without the echo so it stops reading as sent
        return text

    def queue_recallable(self, sid: str) -> bool:
        """Can a ✕ on this session's queued bubble still win? False while a turn is running UN-HELD:
        there the input generator forwards a queued send to the CLI within milliseconds, and once it's
        inside the CLI no recall exists — so offering a cancel would be a lie that ends in "too late".
        True when the queue is genuinely romp-held — the interrupt hold, the rewind hold — or when no
        turn is in flight (idle/connecting: entries sit in _pending until the client drains them).
        The kernel reads this to decide whether the queued bubble gets its ✕ at all; the loud
        unqueue-miss toast covers the races this gate can't (a click on a just-stale push)."""
        with self._lock:
            s = self.sessions.get(sid)
        if not s:
            return True
        with s._lock:
            if s.inflight <= 0:
                return True
            return bool(s._interrupted or (s._rewind_to and not getattr(s, "_rewind_armed", False)))

    def send(self, sid: str, text: str) -> bool:
        s = self._ensure(sid)
        if not s:
            return False
        if _is_compact_cmd(text):
            # Delivering /compact: mark the session compacting NOW (authoritative), covering the gap between
            # this send and the CLI actually starting the turn — so a drive op the producer tick checks in
            # that window still parks. Cleared event-based by the boundary / the turn's ResultMessage.
            s._compacting = True
        if _is_clear_cmd(text):
            # Delivering /clear: light the clearing bracket NOW, so the chat shows "Clearing conversation…"
            # from the instant of the send. Cleared event-based by the lastSid-flipping init (the fresh
            # conversation exists) / the turn's ResultMessage.
            s._clearing = True
        s.enqueue(text)
        # optimistic input echo: show the user's own message INSTANTLY (neither the transcript nor the
        # stream has it yet at send time — only we know the text). Synthetic uuid; pruned by text once the
        # transcript writes the real user atom.
        key = "echo:" + uuid.uuid4().hex
        # AUTHOR the echo from the romp markers, exactly as the event model authors the REAL atom — else a
        # romp-injected nudge/auto-nudge sent through send() echoed as a BLUE HUMAN bubble (a "Follow-up"),
        # not the GRAY "from romp" auto-nudge it is, until the transcript atom replaced it (the user
        # 2026-06-28). romp-injected → author 'romp'; romp-auto → the romp-logo (rompAuto) marker.
        # COMMENT FORM only (the user 2026-07-08, mirroring the event model's ROMP_INJECT_RE): a bare
        # substring also matched CONTENT that merely mentions the marker — a typed follow-up quoting a
        # card summary about romp-injected echoed as a GRAY romp card.
        injected = "<!-- romp-injected -->" in text
        echo = {
            "type": "user", "uuid": key, "session_id": sid, "t": int(time.time()), "parentUuid": None,
            "author": "romp" if injected else "human", "_echo_text": text,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        if injected and "<!-- romp-auto -->" in text:
            echo["rompAuto"] = True                          # auto-nudge → romp-logo on the chat/timeline
        self._live.setdefault(sid, {})[key] = echo
        self._persist_echoes(sid)                            # unlanded echoes survive a kernel restart (reg mirror)
        self._wake_push()
        return True

    def _persist_echoes(self, sid: str) -> None:
        """Mirror the sid's UNLANDED input echoes to the registry (reg['echoes']), the way _persist_queue
        mirrors the not-yet-started queue — so a kernel restart re-seeds them instead of wiping the only
        visible record of an in-flight send. The failure this closes (the user 2026-07-20): a message
        forwarded into the CLI's stdin, then the kernel restarted — the CLI died holding it, the in-memory
        echo died with the kernel, and the message vanished with NO trace anywhere. With the mirror, the
        reseeded echo persists unanswered in the chat, so the LOSS is visible and the user can resend.
        Command-feedback lines (/model etc.) are deliberately not mirrored — replaying a stale confirmation
        after a restart would assert something that may no longer be true."""
        d = self._live.get(sid) or {}
        snap = [{"t": a.get("t", 0), "text": a["_echo_text"], "author": a.get("author") or "human",
                 "rompAuto": bool(a.get("rompAuto")), "dropped": bool(a.get("dropped"))}
                for a in d.values() if a.get("_echo_text") and not a.get("command")]
        try:
            self._update_reg(sid, echoes=snap)
        except Exception as e:
            self._log("echo mirror (%s): registry write failed: %s" % (sid[:8], e))

    def _reseed_echoes(self, regs: list[dict]) -> None:
        """Kernel boot: re-create each alive session's persisted unlanded echoes in the live store, so a
        send in flight across the restart stays visible until its real record lands (then the normal
        text-prune retires it and the mirror empties). Reseeding is also the BOOT half of the dropped
        marking: whatever process held these sends died with the previous kernel, so any echo whose text
        is not in the persisted queue (which _boot_reconcile is about to re-deliver) has provably lost
        its message — _mark_dropped_echoes flags it so the chat can say so."""
        for reg in regs:
            if not (reg.get("alive") and reg.get("sid")):
                continue
            for e in reg.get("echoes") or []:
                text = e.get("text")
                if not isinstance(text, str) or not text:
                    continue
                key = "echo:" + uuid.uuid4().hex
                atom = {"type": "user", "uuid": key, "session_id": reg["sid"],
                        "t": int(e.get("t") or 0) or int(time.time()), "parentUuid": None,
                        "author": e.get("author") or "human", "_echo_text": text,
                        "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
                if e.get("rompAuto"):
                    atom["rompAuto"] = True
                if e.get("dropped"):
                    atom["dropped"] = True
                self._live.setdefault(reg["sid"], {})[key] = atom
            if self._live.get(reg["sid"]):
                self._mark_dropped_echoes(reg["sid"], reg.get("queue") or [])

    def _mark_dropped_echoes(self, sid: str, queued_texts) -> None:
        """A fresh CLI is spawning for this sid, or the kernel just booted: whatever process held any
        earlier send is gone. An input echo whose text is neither in the surviving queue (about to be
        delivered to the new CLI) nor landed in the transcript has no holder left — its send is provably
        LOST. Flag it `dropped`, so the chat renders "never delivered" with restore/dismiss instead of a
        sent-looking bubble that rides the live tail with a stale timestamp forever (the user 2026-07-29:
        a two-day-old lost send kept resurfacing mid-chat, hopping turns as new ones landed, posing as
        history). Event-based — keyed on the spawn/boot that orphaned the send, never on age — and
        self-correcting: an echo whose text actually LANDED still prunes by text on the next build, so a
        premature flag can never stick to a delivered message. The flag rides the registry mirror
        (_persist_echoes), so it survives further restarts."""
        d = self._live.get(sid)
        if not d:
            return
        qs = {q for q in queued_texts if isinstance(q, str)}
        newly = [a for a in d.values()
                 if a.get("_echo_text") and not a.get("command") and not a.get("dropped")
                 and a["_echo_text"] not in qs]
        if not newly:
            return
        # RE-DELIVER, don't just flag (the user 2026-08-23, their strongest point in the restart
        # audit: "it would be very, very bad if we ever lost stuff because of restarts" — a typed
        # prompt queued at 11:20 was silently discarded by the 11:25 restart). A HUMAN send whose
        # loss is proven — and whose text a direct transcript scan confirms never landed as a user
        # record (the flag path self-corrects on a wrong guess; a re-delivery would DUPLICATE, so it
        # verifies first) — goes back into the persisted queue in send order: the next spawn feeds
        # it to the fresh CLI exactly like the surviving queue, recreating the pre-restart state.
        # romp-authored echoes (nudges) keep the flag path: re-delivering one could double-nudge,
        # and its content is regenerable machinery, not the user's words.
        redeliver = []
        for a in sorted(newly, key=lambda x: x.get("t") or 0):
            if a.get("author") == "human" and not self._text_landed(sid, a["_echo_text"]):
                redeliver.append(a)
        if redeliver:
            with self._reg_lock:
                reg = read_reg(self.state_dir, sid)
                if reg is not None:
                    have = [t for t in (reg.get("queue") or []) if isinstance(t, str) and t]
                    add = [a["_echo_text"] for a in redeliver if a["_echo_text"] not in have]
                    if add:
                        reg["queue"] = have + add          # behind the surviving queue: original send order
                        write_reg(self.state_dir, sid, reg)
                        for a in redeliver:
                            self._log("%s: re-delivering a typed send the dead CLI was holding: %.80r"
                                      % (sid[:8], a["_echo_text"]))
        rekeyed = {a["_echo_text"] for a in redeliver}
        for a in newly:
            if a["_echo_text"] in rekeyed:
                continue                                   # now in the queue → renders as queued, prunes on landing
            a["dropped"] = True
            self._log("%s: a send never reached its CLI (the process died holding it) — kept in the chat "
                      "as never-delivered: %.80r" % (sid[:8], a["_echo_text"]), problem=True)
        self._persist_echoes(sid)
        self._wake_push()

    def _text_landed(self, sid: str, text: str) -> bool:
        """Did `text` land as a USER record in the sid's transcript? The re-delivery guard: the echo
        prune is lazy (a landed echo may still be un-pruned at boot), so a queue re-add without this
        scan would duplicate a delivered message. Reads the transcript TAIL (the loss window is recent
        by construction — the send predates the death that orphaned it); unreadable → True, the safe
        side: never re-deliver on doubt, the flag path still surfaces the loss for a human call."""
        try:
            reg = read_reg(self.state_dir, sid) or {}
            path = transcript_path(reg.get("cwd") or "", reg.get("lastSid") or sid)
            want = " ".join(text.split())
            with open(path, "rb") as f:
                f.seek(max(0, os.path.getsize(path) - 2_000_000))
                for line in f.read().decode(errors="replace").splitlines():
                    if '"user"' not in line or want[:60] not in " ".join(line.split()):
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("type") != "user":
                        continue
                    c = ((rec.get("message") or {}).get("content"))
                    txt = c if isinstance(c, str) else " ".join(
                        b.get("text", "") for b in c if isinstance(b, dict)) if isinstance(c, list) else ""
                    if want in " ".join(txt.split()):
                        return True
            return False
        except Exception:
            return True

    def dismiss_echo(self, sid: str, uuid: str | None = None, t: int | None = None) -> str | None:
        """✕ on a never-delivered bubble: retire a DROPPED echo the user has acknowledged. Matched by the
        live-store key first (the uuid this kernel painted), falling back to the echo's send time — the
        key regenerates on every boot reseed, so a click racing a kernel restart still lands. DROPPED
        only: a live (pending) echo is the sole visible record of an in-flight send and must stay until
        it lands or its loss is proven. Returns the retired text, or None on a miss (already gone —
        idempotent; the next push simply paints without it)."""
        live = self._live.get(sid) or {}
        for k, a in list(live.items()):                    # snapshot: the live-tail thread may mutate concurrently
            if not (a.get("_echo_text") and a.get("dropped")):
                continue
            if (uuid is not None and k == uuid) or (t is not None and int(a.get("t") or 0) == t):
                live.pop(k, None)
                if not live:
                    self._live.pop(sid, None)
                self._persist_echoes(sid)
                self._wake_push()
                return a.get("_echo_text")
        return None

    def rewind(self, sid: str, target_uuid: str, text: str) -> "tuple[bool, str]":
        """Rewind the conversation to `target_uuid` (a transcript record uuid the KERNEL has validated:
        on the active chain, newer than the last compaction) and send `text` as the next turn — the
        chat's edit-message branch. In-place: the CLI appends the new turn to the SAME transcript with
        parentUuid=target (same fsid, no lastSid churn) and the event model's leaf walk drops the
        abandoned tail on every surface. Sequence: persist the one-shot flag (reg FIRST, so a fresh
        thread seeds it), enqueue the edit (HELD by the inputs() gate until a rewound client is up),
        reconnect. Refused while busy/compacting or with messages queued — a rewind under a running
        turn is a data-loss hazard, and queued strangers would ride the new branch unasked."""
        return self._arm_rewind(sid, target_uuid, text)

    def rollback(self, sid: str, target_uuid: str, revalidate=None) -> "tuple[bool, str]":
        """The chat's delete-message rollback: the edit rewind with NO replacement turn. The one-shot
        flag arms the same --resume-session-at reconnect, but nothing is enqueued — the conversation
        just stands at `target_uuid`, and the user's NEXT message (whenever it comes) takes the branch.
        Until then no record lands past the recorded leaf, so the flag stays pending (rewindBare) and
        the kernel parse renders the cut (pending_cut) instead of the abandoned tail. `revalidate`
        (delete-while-busy) is the kernel's arm-time re-check, run at the interrupted turn's end —
        None keeps the idle path exactly as before."""
        return self._arm_rewind(sid, target_uuid, None, revalidate=revalidate)

    def _arm_rewind(self, sid, target_uuid, text, revalidate=None):
        reg = read_reg(self.state_dir, sid)
        if not reg or not reg.get("alive"):
            return False, "the session is not running — revive it first"
        if any(t for t in (reg.get("queue") or []) if isinstance(t, str) and t):
            return False, "messages are queued for this session — send or cancel them first"
        if self.busy(sid) or self.compacting(sid):
            # Delete-while-busy (the user 2026-08-29): at an explicit DELETE the old data-loss
            # hazard inverts — the user is knowingly discarding the turn their deleted message
            # started, and its partial output becomes the abandoned branch tail, the rewind
            # family's normal shape. So a BARE rollback on a live in-flight session takes the
            # interrupt-then-arm pipeline instead of refusing. Everything else keeps an honest
            # refusal: a compaction is not the user's turn to discard (interrupting it is its own
            # hazard); queued strangers would ride the new branch unasked (s._pending checked in
            # the pipeline); an edit rewind (text) still needs the idle path — its replacement
            # turn should not race a dying one.
            if self.compacting(sid):
                return False, ("the session is compacting its context — interrupting that is its "
                               "own hazard; wait for it to finish, then delete")
            s = self.sessions.get(sid)
            if text is None and s is not None and not s.ended:
                with s._lock:
                    strangers = bool(s._pending)
                if strangers:
                    return False, "messages are queued for this session — send or cancel them first"
                return self._arm_rollback_busy(s, sid, target_uuid, revalidate)
            return False, "the session is busy — wait for the current turn to finish"
        leaf = last_record_uuid(transcript_path(reg.get("cwd") or "~", reg.get("lastSid") or sid))
        if not leaf:
            return False, "no conversation on disk to rewind"
        bare = text is None
        self._update_reg(sid, rewindTo=target_uuid, rewindLeaf=leaf, rewindBare=bare, rewindWait=False)
        s = self._ensure(sid)
        if not s:
            self._update_reg(sid, rewindTo="", rewindLeaf="", rewindBare=False, rewindWait=False)
            return False, "the session could not start"
        s._rewind_leaf, s._rewind_to = leaf, target_uuid   # already-running thread: the reg seed didn't apply
        s._rewind_bare = bare
        if not bare:
            s.enqueue(text)
        s.request_reconnect()   # idle → reconnects now; a fresh thread's FIRST connect applies the flag
        self._poke()
        return True, ""

    def _arm_rollback_busy(self, s, sid, target_uuid, revalidate):
        """Delete-while-busy, the GESTURE half: set the pending-rewind fields NOW — the existing
        inputs() gate holds message intake on exactly these fields (rewind set, not armed), and
        pending_cut renders the cut immediately via _rewind_wait, so the UI acknowledges at the
        click like an idle delete — then interrupt the running turn through the owner-scoped
        interrupt and let the turn's actual END complete the arm (_complete_rewind_wait). The leaf
        is recorded THERE, after the dust settles: the interrupted turn's partial records are the
        abandoned branch tail. rewindLeaf stays "" across the window on purpose — a kernel death
        mid-window makes the next connect's leaf verification read the flag as SPENT, which
        restores the cards loudly (never a wrong-branch cut)."""
        self._update_reg(sid, rewindTo=target_uuid, rewindLeaf="", rewindBare=True, rewindWait=True)
        s._rewind_to, s._rewind_leaf, s._rewind_bare = target_uuid, "", True
        s._rewind_wait, s._rewind_revalidate = True, revalidate
        self.interrupt(sid)
        # the turn may have settled BETWEEN the busy() read and the fields landing — with intake
        # held, no later turn-end event would ever complete the arm. Third observer, idempotent.
        with s._lock:
            settled = s.inflight == 0 and not s._pending
        if settled:
            self._complete_rewind_wait(s)
        self._poke()
        return True, ""

    def _complete_rewind_wait(self, s):
        """Delete-while-busy, the ARM half — run on the turn-end fact (the Stop hook, with the
        inflight settle as a second observer; idempotent, first one wins under the lock). Re-check
        through the kernel's closure — a mid-window auto-compaction lands a boundary record that
        can put the target out of reach, and only the kernel's parse can see that — then record
        the ACTUAL post-turn leaf and reconnect: from here it is the idle rollback path exactly.
        Any failure restores loudly through _rewind_resolved("failed"), the same event the
        two-phase card cleanup already keys its restore on."""
        with s._lock:
            if not s._rewind_wait:
                return
            s._rewind_wait = False
        sid = s.sid
        revalidate, s._rewind_revalidate = s._rewind_revalidate, None

        def _restore(why):
            self._update_reg(sid, rewindTo="", rewindLeaf="", rewindBare=False, rewindWait=False)
            s._rewind_to, s._rewind_leaf, s._rewind_bare = "", "", False
            self._log("rewind (%s): delete-while-busy restored — %s" % (s.name, why))
            self._rewind_resolved(sid, "failed")
            self._poke()

        err = None
        try:
            err = revalidate() if revalidate else None
        except Exception as e:
            err = "the arm-time re-check raised: %s" % e
        if err:
            return _restore(err)
        leaf = last_record_uuid(transcript_path(s.cwd, s.resume_sid or s.sid))
        if not leaf:
            return _restore("no conversation on disk at the turn's end")
        self._update_reg(sid, rewindTo=s._rewind_to, rewindLeaf=leaf, rewindBare=True, rewindWait=False)
        s._rewind_leaf = leaf
        s.request_reconnect()
        self._poke()

    def rewind_flags(self, sid: str) -> "tuple[str, str, bool]":
        """The session's armed rewind flags (rewindTo, rewindLeaf, rewindBare) — live session first,
        registry fallback (a kernel restart mid-window). ("", "", False) when nothing is armed. The
        kernel's two-phase goal cleanup reads the recorded LEAF here at gesture time: it is the graph
        anchor its spent-flag discriminator later checks chain membership of."""
        s = self.sessions.get(sid)
        if s is not None and not s.ended:
            return (s._rewind_to or "", s._rewind_leaf or "", bool(getattr(s, "_rewind_bare", False)))
        reg = read_reg(self.state_dir, sid)
        if not reg:
            return ("", "", False)
        return (reg.get("rewindTo") or "", reg.get("rewindLeaf") or "", bool(reg.get("rewindBare")))

    def rewind_pending(self, sid: str) -> bool:
        """A rewind flag still APPLICABLE — leaf-verified like the connect path (rewind_disposition
        against the transcript's CURRENT leaf), never raw flag presence. The kernel's boot pass
        latches a hold only on this: a flag the transcript already moved past is spent, and its
        consumption event may never fire (an out-of-band CLI-native continuation while no kernel
        was up leaves the reg armed indefinitely) — raw presence kept those cards hidden with no
        resolving event while the leaf-verified pending_cut let the chat render the full tail."""
        s = self.sessions.get(sid)
        if s is not None and not s.ended:
            to, leaf = s._rewind_to or "", s._rewind_leaf or ""
            cwd, fsid = s.cwd, s.resume_sid or s.sid
        else:
            reg = read_reg(self.state_dir, sid)
            if not reg:
                return False
            to, leaf = reg.get("rewindTo") or "", reg.get("rewindLeaf") or ""
            cwd, fsid = reg.get("cwd") or "~", reg.get("lastSid") or sid
        if not to:
            return False
        return rewind_disposition(to, leaf, last_record_uuid(transcript_path(cwd, fsid))) == "apply"

    def _rewind_resolved(self, sid: str, outcome: str):
        """Tell the kernel a pending rewind RESOLVED (outcome: "taken" — the branch took; "failed" —
        the CLI refused, conversation intact; "spent" — the flag was dropped at connect because the
        leaf moved, ambiguous between a crash-heal take and a dissolved rollback). These are the
        exact flag-consumption events the kernel's two-phase goal cleanup keys its archive/restore
        on (rewind-holds); no callback wired (tests, standalone) → no-op."""
        cb = getattr(self, "rewind_resolved_cb", None)
        if not cb:
            return
        try:
            cb(sid, outcome)
        except Exception as e:
            self._log("rewind (%s): resolve callback failed: %s" % (sid, e))

    def pending_cut(self, sid: str) -> str:
        """The uuid a PENDING bare rollback (rollback(), no replacement turn) truncates the conversation
        at, or "". The kernel parse keys on this so every surface renders the rolled-back state the
        moment the user deletes — without it the abandoned tail would keep rendering until the next
        message finally lands past the recorded leaf (the event the one-shot flag is spent on). The
        live session's in-memory flags are the fresh source; the reg covers a dead/unstarted session
        (kernel restart mid-pending). Verified against the transcript leaf exactly like the connect
        path (rewind_disposition), so the cut expires the instant any record lands."""
        s = self.sessions.get(sid)
        if s is not None and not s.ended:
            to, leaf, bare = s._rewind_to, s._rewind_leaf, getattr(s, "_rewind_bare", False)
            wait = bool(getattr(s, "_rewind_wait", False))
            cwd, fsid = s.cwd, s.resume_sid or s.sid
        else:
            reg = read_reg(self.state_dir, sid)
            if not reg:
                return ""
            to, leaf, bare = reg.get("rewindTo") or "", reg.get("rewindLeaf") or "", bool(reg.get("rewindBare"))
            wait = bool(reg.get("rewindWait"))
            cwd, fsid = reg.get("cwd") or "~", reg.get("lastSid") or sid
        if not to or not bare:
            return ""
        if wait:
            # delete-while-busy window: the interrupted turn's records are STILL landing — on the
            # abandoned tail, by design — so the leaf check below would expire the cut the user
            # just made. The UI acknowledges at the click; verification resumes the moment the
            # turn ends and the arm records the real leaf.
            return to
        now_leaf = last_record_uuid(transcript_path(cwd, fsid))
        return to if rewind_disposition(to, leaf, now_leaf) == "apply" else ""

    def deliver(self, sid: str, text: str) -> bool:
        """Deliver-time wake for an SDK session: enqueue the postal banner so the session processes it on its
        next turn — the SDK analogue of the tmux pane-inject (the user 2026-06-26). NO optimistic human echo:
        it's a peer's mail, not the user's composer input; the transcript records it and the chat renders it as
        a postal card. True if the session is live/resumable (so the bus consumed the maildir copy), else False
        (the bus keeps it for the drain backstop)."""
        s = self._ensure(sid)
        if not s:
            return False
        s.enqueue(text)
        self._poke()
        return True

    def interrupt(self, sid: str) -> bool:
        s = self.sessions.get(sid)
        if not s:
            return False
        s.interrupt()
        append_state(self.state_dir, sid, "idle", int(time.time()) - 1)
        self._poke()
        return True

    def forwards_sends(self) -> bool:
        """True (see SessionBackend.forwards_sends): the SDK holds queued turns in _pending and its inputs()
        generator forwards them at the next tool boundary, folds several into one turn, and holds them across
        an interrupt. So the kernel hands composer sends straight to send() even mid-turn instead of parking
        them; the reconciliation renders the still-waiting message as a queued bubble until it forwards."""
        return True

    def busy(self, sid: str) -> "bool | None":
        """Authoritative in-flight signal (see SessionBackend.busy): a turn is running (inflight>0) OR one is
        queued and about to run (_pending). Either means a drive op pressed now must PARK to hold press-order,
        with no wait for the transcript to catch up. None when we don't run this sid (→ cached-parse fallback)."""
        s = self.sessions.get(sid)
        if not s:
            return None
        with s._lock:
            return s.inflight > 0 or bool(s._pending)

    def compacting(self, sid: str) -> "bool | None":
        """Authoritative 'is a /compact in progress' (see SessionBackend.compacting): set when /compact is
        delivered, cleared event-based by the compact_boundary or the /compact turn's ResultMessage — so a
        no-op compaction (nothing to compact, no boundary) can't strand the kernel's optimistic latch for
        180s. None when we don't run this sid (→ the kernel's optimistic/tmux path)."""
        s = self.sessions.get(sid)
        if not s:
            return None
        return s._compacting

    def clearing(self, sid: str) -> "bool | None":
        """Authoritative 'is a /clear in progress' (see SessionBackend.clearing): set when /clear is
        delivered, cleared event-based by the lastSid-flipping init (the fork landed) or the turn's
        ResultMessage. None when we don't run this sid (tmux has no bracket — the known fork-lane gap)."""
        s = self.sessions.get(sid)
        if not s:
            return None
        return s._clearing

    def kill(self, sid: str) -> bool:
        with self._reg_lock:                       # the alive flip must not lose to an RMW snapshot
            reg = self._reg_for_flip(sid)
            if reg:
                reg["alive"] = False
                write_reg(self.state_dir, sid, reg)
        s = self.sessions.pop(sid, None)
        if s:
            s.shutdown()
        self._poke()
        return True

    def running_sids(self) -> list:
        """Sids with a LIVE session thread — the conserve sweep's candidates (T148)."""
        with self._lock:
            return [s.sid for s in self.sessions.values()
                    if s.thread is not None and s.thread.is_alive() and not s.ended]

    def conserve_idle(self, sid: str) -> bool:
        """Whether this session is safe to conserve-close: no in-flight turn, nothing queued,
        no ask parked (T148 — never close work) — and no armed RECURRING timers (the T148/769
        seam: a recurring cron's CLI process is its only scheduler, no catch-up exists, and
        ensure_scheduled revives any recurring-armed session with no thread every producer pass —
        closing one here would be a close/revive loop, one respawn per sweep, forever). One-shot
        wakeups do NOT hold the close (refined 2026-08-28 with the 769 author): the entry's
        absolute dueEpoch + arming-generation stamp make the close safe — the dead generation
        hands the wakeup to deliver_lost_wakeups, which delivers the prompt at due and revives
        the session then. Both this guard and ensure_scheduled read recurring_crons(reg), the
        same predicate over the same record, so the two sides can never disagree."""
        with self._lock:
            s = self.sessions.get(sid)
        if not s or s.ended:
            return False
        if s.inflight or s.pending() or s._cur_ask_fut is not None:
            return False
        reg = read_reg(self.state_dir, sid) or {}
        if recurring_crons(reg):
            return False
        return True

    def conserve_close(self, sid: str) -> bool:
        """Close ONE idle session's claude process, keeping it a tab-click from reviving (T148):
        the reg stays alive=True and untouched (unlike kill), the queue/transcript persist, and the
        dormant row keeps serving from the reg — the ordinary on-demand _ensure (a send, an open,
        a scheduled wake) is the revive. Re-checks idleness under the lock; a turn that started
        since the sweep's read stands the close down."""
        if not self.conserve_idle(sid):
            return False
        with self._lock:
            s = self.sessions.pop(sid, None)
        if not s:
            return False
        try:
            s.shutdown()
        except Exception as e:
            self._log("conserve close (%s): shutdown failed: %s" % (s.name, e))
        self._poke()
        return True

    def thread_sessions(self) -> dict[str, dict]:
        """{sid: {state, threadOf}} for every alive COMMENT-THREAD session — exactly the rows
        live_sessions deliberately hides (a thread's surface is the parent chat's comment UI, never a
        tab). Served only to callers that ask (kernel /sessions?threads=1 → the postal bus), so a
        thread can mail its PARENT under its own name (the user 2026-08-22) without ever gaining a
        tab, a lane, or a feed card."""
        out = {}
        for reg in list_regs(self.state_dir):
            if not reg.get("alive") or not reg.get("threadOf"):
                continue
            sid = reg["sid"]
            s = self.sessions.get(sid)
            st = (s.snapshot() if s and s.thread.is_alive() else {"state": "waiting", "backend": "sdk"})
            out[sid] = {"state": st.get("state", "waiting"), "threadOf": str(reg.get("threadOf") or "")}
        return out

    def fork_children(self) -> dict:
        """{parent sid: [{sid, name, cut, t}, …]} for every session carrying a durable forkedFrom —
        the parent chat's branch chips. Comment threads are skipped (their anchor is the comment
        highlight; a PROMOTED thread has threadOf cleared, so it joins here). Memoized on the sdk/
        dir's mtime: every reg write publishes via os.replace into that dir, bumping it, so the
        steady state costs one stat instead of a full registry sweep per session build."""
        d = Path(self.state_dir) / "sdk"
        try:
            mt = d.stat().st_mtime_ns
        except OSError:
            return {}
        memo = self._fork_children_memo
        if memo and memo[0] == mt:
            return memo[1]
        out: dict = {}
        for reg in list_regs(self.state_dir):
            ff = reg.get("forkedFrom")
            if not isinstance(ff, dict) or not ff.get("sid") or reg.get("threadOf"):
                continue
            out.setdefault(str(ff["sid"]), []).append(
                {"sid": reg["sid"], "name": reg.get("name", ""),
                 "cut": ff.get("cut") or "", "t": ff.get("t") or 0})
        for kids in out.values():
            kids.sort(key=lambda k: k["t"])
        self._fork_children_memo = (mt, out)
        return out

    def session_state(self, sid: str) -> str:
        """Live state ('working'/'waiting'/…) for ONE sid, comment threads included — live_sessions
        deliberately filters threadOf regs, so the comments frame reads its thread's pulse here."""
        with self._lock:
            s = self.sessions.get(sid)
        if s and s.thread.is_alive():
            try:
                return str(s.snapshot().get("state") or "")
            except Exception:
                return ""
        return ""

    def session_since(self, sid: str) -> int:
        """Epoch seconds the sid's current state began (0 unknown) — session_state's twin, read by
        the comments frame so the popover's working chip can carry the chat's counting timer."""
        with self._lock:
            s = self.sessions.get(sid)
        if s and s.thread.is_alive():
            try:
                return int(float(s.snapshot().get("since") or 0))
            except Exception:
                return 0
        return 0

    def session_meta(self, sid: str) -> dict:
        """{'mode','fast'} from the live snapshot ({} unknown) — the comments frame's statusline
        parity: the popover shows the chat statusline's FULL element set (the user 2026-08-25)."""
        with self._lock:
            s = self.sessions.get(sid)
        if s and s.thread.is_alive():
            try:
                snap = s.snapshot()
                return {"mode": str(snap.get("mode") or ""), "fast": str(snap.get("fast") or "")}
            except Exception:
                return {}
        return {}

    def rename(self, sid: str, new_name: str) -> bool:
        reg = read_reg(self.state_dir, sid)
        if not reg:
            return False
        # renameNote: the one-line "you were renamed" ping, delivered by send() as its OWN
        # machine-dressed record ahead of whatever NEXT enters the session (the user 2026-08-24: a
        # renamed worker learned its own new name only by inference from a peer's prose).
        # Reg-persisted so it survives restarts; never a wake of its own — the queue wakes
        # sessions, this rides a send that already happens. ONLY when prior turns exist under the
        # old name (the transcript is the record of prior turns): a rename before the first turn
        # has no stale self-knowledge to correct — the fresh session learns its name the normal
        # way, and pinging it put bookkeeping ahead of the user's very first words (the user
        # 2026-08-25).
        _ls = reg.get("lastSid")
        _tp = Path(transcript_path(reg.get("cwd") or "", _ls)) if _ls else None
        _has_history = bool(_tp) and _tp.exists() and _tp.stat().st_size > 0
        self._update_reg(sid, name=new_name,
                         **({"renameNote": new_name} if _has_history else {}))   # locked RMW — see set_effort's race note
        # keep the shared names/ identity file in sync (preserve colours)
        try:
            parts = (Path(self.state_dir) / "names" / sid).read_text().rstrip("\n").split("\t")
        except OSError:
            parts = [new_name, reg.get("cwd", "")]
        parts += ["", "", ""]
        write_name(self.state_dir, sid, new_name, parts[1], parts[2], parts[3])
        s = self.sessions.get(sid)
        if s:
            s.name = new_name
        return True

    def set_model(self, sid: str, value: str) -> bool:
        """Change the session's model. Persisted in the registry (so a reconnect keeps it) and applied
        LIVE on a connected session via the SDK control channel — NOT a /model slash injection, which the
        SDK input stream does not interpret. 'default' resets to the CLI default (set_model(None))."""
        if not read_reg(self.state_dir, sid):
            return False
        write_sdk_default(self.state_dir, model=value)   # remember as the seed for the NEXT new session (the user 2026-06-27)
        s = self.sessions.get(sid)
        if s:
            s.chosen_model = value
            # PENDING: show switching-dots, NOT a premature/stale name, until the LIVE model reflects the
            # pick (the user 2026-07-03). The old code stamped s.model = value.capitalize() ("Opus"), but
            # left reg.liveModel stale — and model_label PREFERS liveModel, so a session that hadn't yet run
            # a turn in the new model kept showing the OLD name. Now the pick marks pending; _do_set_model
            # pulls the real name, which clears it (and the dormant/never-driven trap can't happen — the
            # refresh resolves an idle session, and _on_session_gone resolves a thread that exits mid-switch).
            already = _model_reflects_alias(s.model, value)
            s._model_pending = "" if already else value
            self._update_reg(sid, model=value, modelPending=bool(s._model_pending))   # locked RMW — see set_effort
            s.set_model_live(None if value in ("", "default") else value)
            # Synthesize the INVOCATION atom the CLI never streams (it streams only the stdout
            # confirmation): the chat gets the same "/model sonnet" command chip the tmux path reads from
            # its transcript, and the timeline's dot lands in REAL TIME instead of after the next disk
            # write (the user 2026-07-02). _echo_text lets the disk's own command record retire it by
            # text match if one ever lands; the human-floor prune covers a session that never writes one.
            t = int(time.time())
            disp = "/model " + value
            uid = "cmd:%d:model" % t
            self._live.setdefault(sid, {})[uid] = {
                "type": "user", "uuid": uid, "session_id": sid, "fsid": s.resume_sid, "parentUuid": None,
                "t": t, "author": "human", "command": "/model", "_echo_text": disp,
                "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
            # The DURABLE twin of the live chip (the user 2026-08-14): same t and text, so build_session
            # can dedup while the chip is live and take over seamlessly once stale_cmd retires it.
            append_cmd_gesture(self.state_dir, sid, disp, t=t)
            self._wake_push()
        else:
            # DORMANT (no live thread): no turn is coming to report a real name, so resolve to the chosen
            # alias's best-effort label immediately — never leave the badge on a stale liveModel or trapped
            # on dots. The value applies for real on the next connect (chosen_model → _options).
            self._update_reg(sid, model=value, liveModel=_alias_label(value), modelPending=False)
        return True

    def set_fast(self, sid: str, value: str) -> bool:
        """Toggle fast mode ('on'|'off'). The CLI's /fast descriptor is marked supportsNonInteractive,
        so the SDK input stream DOES interpret the literal '/fast on|off' text (unlike /model — see
        set_model) — but only on a connection made with the `fastMode` flag-settings opt-in; without
        it the CLI refuses the command outright ("Fast mode is not available in the Agent SDK",
        verified against claude 2.1.224). So this is a hybrid:

        - The reg's `fast` mirrors EVERY toggle first — the persisted ask that drives the connect-time
          opt-in (_options → flag_settings_path), so a lingering flag on a session the user turned off
          is impossible, and a dormant session applies the pick at its next connect. Deliberately NOT
          write_sdk_default: fast mode draws credits at a higher rate and carries its own rate limits,
          so it stays per-session rather than quietly spreading to every new session.
        - A connection made WITH the flag (_fast_unlocked) takes the literal send in both directions:
          the send's echo is the chat's acknowledgement, the flip here is optimistic for the badge,
          and fast_mode_state on the next init re-asserts the truth.
        - A connection made WITHOUT the flag can't take the send, but 'off' needs none (fast mode is
          already off there) and 'on' reconnects — the flag applies at the (re)connect, immediately
          if idle, at the end of the current turn if busy (request_reconnect, the /effort machinery)."""
        if value not in ("on", "off"):
            return False
        if not read_reg(self.state_dir, sid):
            return False
        # liveFast mirrors the optimistic flip where the badge reads it while dormant / across a
        # restart; _adopt_fast_state re-asserts at the next connect. Locked RMW — see set_effort.
        self._update_reg(sid, fast=(value == "on"), liveFast=value)
        s = self.sessions.get(sid)
        if not s or not s.thread.is_alive():
            return True                        # dormant: the persisted ask applies at the next connect
        s.fast_opt = (value == "on")
        if s._fast_unlocked:                   # opted in at connect → the CLI interprets the literal send
            if not self.send(sid, "/fast " + value):
                return False
            s.fast = value
            s._fast_expect = value             # the send's own turn-init is one word stale; let it yield
            self._wake_push()
            return True
        if value == "off":                     # no flag at connect → fast mode is already off; nothing to send
            return True
        s.request_reconnect()                  # first opt-in: the flag applies at the (re)connect
        s.fast = "on"                          # optimistic for the badge; init re-asserts the truth
        self._wake_push()
        return True

    # bypassPermissions is the one mode that must not become the remembered default. Every other pick
    # here is a preference worth inheriting; this one removes the approval gate, and spawn() seeds a new
    # session from the remembered mode with NOTHING in the create UI that shows it — so one click on one
    # tab would quietly hand every session you started afterwards an unprompted agent. It stays where you
    # set it: this session, until you change it (the user 2026-08-15, on the picker adding the entry).
    # The carve-out is HERE and not in spawn() on purpose: romp declines to remember bypass off a click,
    # but a mode written into sdk-defaults.json by hand is still honoured, so the escape hatch is open to
    # anyone who genuinely wants every new session unprompted — they just have to say so deliberately.
    STICKY_MODE_EXCLUDES = {"bypassPermissions"}

    def set_mode(self, sid: str, mode: str) -> bool:
        """Change the permission mode. Persisted in the registry and applied LIVE via the SDK control
        channel (set_permission_mode) — not merely stored for the next reconnect."""
        if not read_reg(self.state_dir, sid):
            return False
        self._update_reg(sid, mode=mode)   # locked RMW — see set_effort's race note
        if mode not in self.STICKY_MODE_EXCLUDES:
            write_sdk_default(self.state_dir, mode=mode)   # remember as the seed for the NEXT new session, like model/effort (the user 2026-06-27)
        s = self.sessions.get(sid)
        if s:
            prev = s.perm_mode      # the last CONFIRMED mode — the revert target if the CLI refuses (T139)
            s.mode = mode
            s.perm_mode = mode      # snapshot reflects it immediately (clears the picker's meta-pending)
            if mode == "bypassPermissions" and prev != "bypassPermissions":
                # the CLI REFUSES a live switch INTO bypass unless the process was LAUNCHED with
                # --dangerously-skip-permissions (probed on CLI 2.1.221, T139: 'Cannot set permission
                # mode to bypassPermissions because the session was not launched with…'). The honest
                # apply is the RECONNECT (effort's pattern): the relaunch carries
                # permission_mode=bypassPermissions, which the SDK maps to the launch flag — resume
                # continues the conversation and the next init confirms perm_mode.
                s.request_reconnect()
            else:
                s.set_mode_live(mode, prev=prev)
        return True

    def stop_task(self, sid: str, task_id: str) -> bool:
        s = self.sessions.get(sid)
        return bool(s and s.request_stop_task(task_id))

    def mcp_status(self, sid: str):
        s = self.sessions.get(sid)
        return s.mcp_status() if s else ([], "romp has no live SDK session for this tab")

    def mcp_action(self, sid: str, name: str, action: str, enabled: bool = True) -> str:
        s = self.sessions.get(sid)
        if not s:
            return "romp has no live SDK session for this tab"
        if action == "reconnect":
            return s.mcp_reconnect(name)
        return s.mcp_toggle(name, enabled)

    def rewind_files(self, sid: str, uuid: str) -> bool:
        s = self.sessions.get(sid)
        return bool(s and s.request_rewind_files(uuid))

    def set_effort(self, sid: str, value: str) -> bool:
        """Change the reasoning effort. effort is a connect-time CLI flag (--effort) with no SDK runtime
        control, so this persists it and RECONNECTS to apply (resume continues the conversation): immediately
        if the session is idle, at the end of the current turn if it's busy. The label updates at once."""
        if value not in EFFORT_LEVELS:
            return False
        if not read_reg(self.state_dir, sid):
            return False
        # LOCKED read-modify-write (_update_reg), never the bare read→mutate→write this used to do: the
        # loop threads run their own locked RMWs on the same reg (queue/echo mirrors, liveCtx), and an
        # interleaving could silently drop the effort field — the pick LOOKED applied (in-memory label
        # right), then reverted at the next respawn when __init__ re-read the reg (the user 2026-08-14,
        # whose ultracode sessions seemed to downgrade at random). Whole setter family fixed alike.
        # effortPending: the applying reconnect hasn't completed yet → dots + "Reloading session…"
        self._update_reg(sid, effort=value, effortPending=True)
        # Remember EVERY pick as the new-session seed, ultracode included (the user 2026-08-14, who
        # picks ultracode and expects new sessions to follow; the old never-remember guard kept the
        # seed at their one historical max pick, so every new session opened at max — reading as a
        # downgrade). The CLI itself cannot persist ultracode as a default (its settings enum stops at
        # xhigh; /effort says "this session only") — but romp's seed is romp's own store, and spawn
        # hands each NEW session its own per-session launch shape (--effort xhigh + the ultracode
        # settings key), so the CLI's session-scoping is preserved, one session at a time.
        write_sdk_default(self.state_dir, effort=value)
        s = self.sessions.get(sid)
        if s:
            s.effort = value        # picker label reflects it now; the reconnect makes it real
            s._effort_pending = value   # switching-dots on the effort badge + "Reloading session…" notice until the reconnect lands
            s.request_reconnect()
            # Synthesize the "/effort X" invocation atom, exactly as set_model does for "/model X": the
            # reconnect leaves NO transcript record at all, so without this an idle-session effort change
            # showed nothing in the chat while a busy one (parked) showed a queued chip — the same pick,
            # visibly acknowledged or not depending on timing (the user 2026-07-05, who called it somewhat
            # inconsistent). One chip, both paths.
            t = int(time.time())
            disp = "/effort " + value
            uid = "cmd:%d:effort" % t
            self._live.setdefault(sid, {})[uid] = {
                "type": "user", "uuid": uid, "session_id": sid, "fsid": s.resume_sid, "parentUuid": None,
                "t": t, "author": "human", "command": "/effort", "_echo_text": disp,
                "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
            append_cmd_gesture(self.state_dir, sid, disp, t=t)   # durable twin — see set_model
            self._wake_push()
        return True

    def set_auth(self, sid: str, value: str) -> bool:
        """Change which account this session bills — 'login' (the machine's Claude login) or 'key'
        (the manager environment's API key). Auth is connect-time (the key rides _options' env;
        there is no runtime control), so this persists the pick and RECONNECTS to apply, exactly
        like set_effort: immediately if idle, at the end of the current turn if busy. The CLI's
        next init confirms via apiKeySource (_note_auth_source flags a landing on the wrong side)."""
        if value not in ("login", "key"):
            return False
        if value == "key" and not self.work_key:
            return False   # nothing to inject — the UI never offers this; refuse rather than half-apply
        if value == "login" and not self.login_ok():
            # the SAME bar the key side always had (T124: set_auth accepted 'login' unconditionally,
            # so on a login-less box the pick sat in the UI as applied fact while the reconnect
            # errored or landed keyed via an apiKeyHelper — the silent-degrade class). login_ok is
            # the kernel's credential-store probe (the authority the usage bars trust); the default
            # is permissive so a bare backend (tests, no kernel wiring) keeps the old behavior.
            return False
        if not read_reg(self.state_dir, sid):
            return False
        # authPending: the applying reconnect hasn't completed → badge dots. Locked RMW — see set_effort.
        # apiKeyAuth=None: the persisted CLI report described the process this reconnect replaces,
        # so a restart must restore "no init has landed yet", never the old side (both readers guard
        # with isinstance(..., bool), so None reads as absent).
        self._update_reg(sid, auth=value, authPending=True, apiKeyAuth=None)
        write_sdk_default(self.state_dir, auth=value)   # the seed for the NEXT new session, like model/effort
        s = self.sessions.get(sid)
        if s:
            s.auth = value
            s._auth_pending = value
            s.auth_live = ""   # the last init's report predates this switch — the Billing row shows
            #   the plain intent (no "CLI reports" parenthetical) until the next init re-confirms
            s.request_reconnect()
            # Acknowledge the pick in the chat exactly as set_effort does: the reconnect writes no
            # transcript record, so without a synthesized chip an idle session's auth change shows
            # nothing at all.
            t = int(time.time())
            disp = "/auth " + value
            uid = "cmd:%d:auth" % t
            self._live.setdefault(sid, {})[uid] = {
                "type": "user", "uuid": uid, "session_id": sid, "fsid": s.resume_sid, "parentUuid": None,
                "t": t, "author": "human", "command": "/auth", "_echo_text": disp,
                "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
            append_cmd_gesture(self.state_dir, sid, disp, t=t)   # durable twin — see set_model
            self._wake_push()
        return True

    def default_auth(self, reg: dict | None = None) -> str:
        """The auth a session with no live SdkSession object would launch with — the dormant twin of
        SdkSession.effective_auth(), reading the same registry field with the same fallback."""
        a = (reg or {}).get("auth")
        if a == "login":
            return "login"
        return "key" if self.work_key else "login"

    def owns(self, sid: str) -> bool:
        return read_reg(self.state_dir, sid) is not None

    def ensure_scheduled(self) -> int:
        """Keep a CLI process ALIVE for every session with ARMED SESSION TIMERS (reg sessionCrons, the
        Stop hook's record of CronCreate/ScheduleWakeup//loop arms). Session-scoped timers exist only in
        the CLI process's memory and its scheduler fires fine on a live IDLE process — but a DORMANT
        session has no process at the due moment and the timer silently never fires (the user
        2026-08-28: a wakeup armed for 26 minutes out missed across 21 hours; a cron missed every tick
        while parked, then fired reliably for days once its session stayed active). So the kernel's
        producer calls this every pass: any alive, timer-armed session with no running thread is
        reconnected — resume re-hydrates its timer set (verified live 2026-08-28: CronList shows the
        same cron in a fresh process after a kill), and the CLI does ALL the firing natively; romp
        never fires a timer itself. A recurring slot due inside a down window is CAUGHT UP by the
        resumed CLI itself (it re-fires passed slots on --resume, verified live 2026-09-01) — and
        the same behavior re-fires slots that already ran, once per restart, so the catch-up is
        only safe because _prompt_submit_hook's delivered-slot gate blocks the replays (T211).
        Residual, accepted: a ONE-SHOT due inside the down window still misses CLI-side
        (deliver_lost_wakeups owns those).

        A 120s per-sid retry floor bounds the failure mode where a timer-armed session's CLI dies
        instantly on every spawn — without it this sweep would respawn it every producer pass
        (~3s), a spawn storm. Returns how many sessions were revived this pass."""
        now = time.time()
        cool = getattr(self, "_sched_attempts", None)
        if cool is None:
            cool = self._sched_attempts = {}
        n = 0
        for reg in list_regs(self.state_dir):
            if not reg.get("alive") or reg.get("threadOf"):
                continue
            rec = recurring_crons(reg)
            if not rec:
                # one-shot-only armed sets do NOT pin a process (refined 2026-08-28): a threadless
                # session's one-shots are dead-generation — a fresh CLI cannot re-hydrate them, so a
                # revival here buys nothing and would loop against conserve_close forever (close,
                # revive, close…). deliver_lost_wakeups owns them and revives at due instead.
                continue
            sid = reg["sid"]
            with self._lock:
                s = self.sessions.get(sid)
                if s and s.thread.is_alive():
                    continue
            if now - cool.get(sid, 0) < 120:
                continue
            cool[sid] = now
            # Spawns draw a slot on the ONE machine-wide _spawn_sem, like boot reconcile and the
            # idle-queue drive: without it a kernel restart with N timer-armed sessions launched N
            # claude processes in one pass — the exact CPU storm the stagger exists to prevent
            # (review of #769, 2026-08-28). No slot this pass → the cooldown map retries later.
            slot = self._spawn_sem.acquire(timeout=0.1)
            if not slot:
                cool[sid] = 0                       # don't burn the 120s floor on a full stagger
                continue
            try:
                if self._ensure(sid, on_boot_settled=self._spawn_sem.release) is not None:
                    n += 1
                    self._log("scheduled-session sweep: reviving %s — %d recurring timer(s) need a live process"
                              % (reg.get("name") or sid[:13], len(rec)))
                else:
                    self._spawn_sem.release()       # nothing spawned — the parked release never attaches
            except Exception:
                self._spawn_sem.release()
                raise
        return n

    def deliver_lost_wakeups(self) -> int:
        """Fire ONE-SHOT wakeups the CLI can no longer fire. A process recycle LOSES ScheduleWakeup
        wakeups (verified live 2026-08-28: after a kill + revive, CronList re-hydrates the recurring
        cron but the pending wakeup is gone) — so a wakeup armed before a kernel restart, an orphan
        reap, or a reconnect never fires, however alive the session is now. The reg's armed-set record
        (the Stop hook's sessionCrons) survives the recycle; when a one-shot recorded there is PAST DUE
        by a grace and still recorded, nobody fired it: a live process fires one-shots at their exact
        minute, and the fired turn's Stop hook rewrites the record without it. Deliver the wakeup's own
        prompt through the normal send path (revives a dormant session) and strip the record first, so
        the next pass can never double-deliver.

        OWNERSHIP IS BY PROCESS GENERATION, not by guessing from timing (review of #769, 2026-08-28:
        the first cut used a 90s grace + an inflight check, and both were blind to turns the CLI's own
        scheduler starts — a genuinely-fired wakeup whose turn outlasted the grace got delivered a
        second time, mid-turn). Every recorded entry carries procGen, the stamp of the CLI process
        that armed it. That generation still live → the CLI itself will fire it at due; the kernel
        NEVER touches it, however late the record looks. That generation gone → no process can ever
        fire it; the kernel owns it outright and delivers at due (a small 5s grace covers clock skew
        between the stamp and this sweep, not any behavioral window)."""
        now = time.time()
        fired = 0
        for reg in list_regs(self.state_dir):
            if not reg.get("alive") or reg.get("threadOf"):
                continue
            crons = reg.get("sessionCrons") or []
            shots = [c for c in crons if isinstance(c, dict) and not c.get("recurring")]
            if not shots:
                continue
            sid = reg["sid"]
            with self._lock:
                s = self.sessions.get(sid)
                live_gen = s.proc_gen if (s and s.thread.is_alive()) else None
            due_shots = []
            for c in shots:
                if live_gen is not None and str(c.get("procGen") or "") == live_gen:
                    continue                      # its arming process is alive — the CLI fires it, not us
                due = c.get("dueEpoch")
                if not due:                       # legacy entry without the exact date → reconstruct
                    due = wakeup_due_epoch(c.get("cron"),
                                           float(c.get("armedAt") or reg.get("sessionCronsAt") or (now - 3600)))
                if due is not None and now >= float(due) + 5:
                    due_shots.append(c)
            if not due_shots:
                continue
            keep = [c for c in crons if c not in due_shots]
            try:                                  # strip FIRST — a send failure must not re-fire forever;
                self._update_reg(sid, sessionCrons=keep)   # the loss is logged loudly below instead
            except Exception as e:
                self._log("lost-wakeup (%s): reg strip failed, not delivering: %s" % (reg.get("name") or sid[:13], e))
                continue
            for c in due_shots:
                ok = False
                try:
                    ok = bool(self.send(sid, c.get("prompt") or ""))
                except Exception as e:
                    self._log("lost-wakeup (%s): delivery raised: %s" % (reg.get("name") or sid[:13], e))
                self._log("lost-wakeup (%s): one-shot %s was due %.0fs ago with no process to fire it — %s"
                          % (reg.get("name") or sid[:13], c.get("id") or "?",
                             now - (wakeup_due_epoch(c.get("cron"), float(c.get("armedAt") or now)) or now),
                             "prompt delivered" if ok else "DELIVERY FAILED (prompt lost — see above)"))
                fired += 1
        return fired

    def live_sessions(self) -> dict[str, dict]:
        """{sid: state-dict} for every alive SDK session — merged by the kernel
        into its session enumeration so SDK sessions appear in the UI."""
        out = {}
        for reg in list_regs(self.state_dir):
            if not reg.get("alive"):
                continue
            if reg.get("threadOf"):
                continue   # a comment thread: its surface is the parent chat's comment UI, never a tab
            sid = reg.get("sid")
            if not sid:
                continue
            try:
                out[sid] = self._live_row(reg, sid)
            except Exception:
                # One session's bad row must not hide the OTHERS — this loop used to run unguarded
                # under the kernel merge's single try, so one snapshot() exception silently dropped
                # EVERY SDK session from an otherwise-successful listing, and absence from a listing
                # reads as death downstream (the postal bus refused sends to live peers, 2026-08-31).
                # The failing session itself stays VISIBLE as a minimal waiting row: present beats
                # perfect, and the loud line makes the real bug findable.
                sys.stderr.write("live_sessions: row for %s failed (kept as minimal row): %s\n"
                                 % (sid, traceback.format_exc()))
                out[sid] = {"state": "waiting", "since": "", "model": "", "modelPending": False,
                            "effortPending": False, "effort": "", "auth": "", "authLive": "",
                            "authPending": False, "mode": "", "fast": "", "fastReason": "",
                            "color": None, "connected": False, "spawning": False, "retryCount": 0,
                            "retryInfo": None, "ctx": None, "subagents": [], "bgTasks": []}
        return out

    def _live_row(self, reg, sid):
        """One session's live_sessions row (running snapshot, else the dormant reg row) — factored
        so live_sessions can guard it PER SESSION (one bad row must not hide the other sessions)."""
        s = self.sessions.get(sid)
        if s and s.thread.is_alive():
            return s.snapshot()
        ls = last_state(self.state_dir, sid)
        st = ls.get("state") or "waiting"
        # A NOT-running (dormant, resumable) SDK session can't actually be mid-turn: after a kernel
        # restart its thread is gone, but the state log still reads its last in-flight state
        # ("working"/"permission"/"picker"/…). Reporting that verbatim makes a dormant session look
        # FALSELY blocked/working with NO live ask to resolve it — the prompt died with the thread (the
        # user 2026-06-24: reorder_bug showed "blocked, needs approval" with no prompt after a refresh).
        # Map any in-flight state → "waiting", the true state of a dormant session (it resumes on the
        # next drive). A GENUINELY-blocked session is RUNNING → snapshot() above (with a real
        # current_ask), so it's unaffected. Keyed on thread-not-running, not a time heuristic.
        if st in ("working", "permission", "picker", "compacting", "retrying"):
            st = "waiting"
        lc = reg.get("liveCtx")   # last persisted context fill → bar survives idle/restart
        return {"state": st,
                    "since": str(ls.get("t") or ""),
                    # not running (e.g. post-restart): prefer the last LIVE model we persisted
                    # (liveModel), else the chosen alias — so the badge isn't blank while dormant.
                    "model": model_label(reg.get("liveModel") or "", reg.get("model") or ""),
                    "modelPending": bool(reg.get("modelPending")),
                    "effortPending": bool(reg.get("effortPending")),
                    "effort": reg.get("effort", ""),
                    "auth": self.default_auth(reg),
                    # the persisted CLI truth (apiKeyAuth, the liveModel pattern) so a dormant
                    # session's Billing row keeps telling it; absent = no init ever landed
                    "authLive": ("key" if reg.get("apiKeyAuth") else "login")
                                if isinstance(reg.get("apiKeyAuth"), bool) else "",
                    "authPending": bool(reg.get("authPending")),
                    "mode": reg.get("mode", ""),
                    # last persisted fast state (liveFast, like liveCtx above) → the badge
                    # survives idle/restart instead of vanishing until the next turn
                    "fast": reg.get("liveFast", ""),
                    "fastReason": reg.get("liveFastReason", ""),
                    "ctx": lc if isinstance(lc, (int, float)) else "",
                    "ctxOver": bool(reg.get("liveCtxOver")),   # persisted beside liveCtx — same survival
                    "ctxTokens": (int(reg["liveCtxTokens"])
                                  if isinstance(reg.get("liveCtxTokens"), (int, float)) else None),
                    "summary": ""}

    # ---- picker/permission UI bridge (kernel-thread API) ----
    def on_ask(self, sid: str, kind: str, payload=None) -> bool:
        """Route an inbound picker action (answer/toggle/submit/custom/cancel/text)
        to the SDK session's waiting callback. Returns False if not SDK-backed."""
        s = self.sessions.get(sid)
        if not s:
            return False
        if kind == "focus":
            return True   # ↑/↓ preview-step: SDK options carry their OWN preview, so the webview swaps it
                          # locally — there's no TUI cursor to drive, and it must NOT resolve the ask.
        return s.resolve_ask(kind, payload)    # the DELIVERY outcome, not mere routing (T214)

    # ---- callbacks used by sessions ----
    def _emit_ask(self, sess: SdkSession, ask: dict):
        # STORE the ask (not just a bool): the kernel's _ask_poll replays it to chat clients each tick, so a
        # blocked SDK session still shows its prompt to a client that connects/refocuses/reloads AFTER the ask
        # was raised — the durable replay tmux gets from pane-scraping. The immediate push below is just for
        # snappiness (no 1.2s wait); the poll is the source of truth. (the user 2026-06-24: blocked-no-prompt.)
        self._pending_ask[sess.sid] = ask
        # …and a DURABLE marker (T214): the in-memory ask dies with the kernel, and a restart-cut
        # question was never re-presented — the boot reconcile reads this flag off the reg and asks
        # the resumed session to raise its question again (ASK_DIED_NOTICE), so the user's answer
        # has somewhere real to land. Cleared on resolve below and by the reconcile after it notices.
        self._update_reg(sess.sid, pendingAsk=True)
        self._notify("chat", {"type": "askLive", "id": sess.sid, "ask": ask})
        self._poke()

    def _clear_ask(self, sess: SdkSession):
        self._pending_ask.pop(sess.sid, None)
        self._update_reg(sess.sid, pendingAsk=False)   # resolved in THIS life — nothing owed at the next boot (T214)
        self._notify("chat", {"type": "askLiveClear", "id": sess.sid})

    def current_ask(self, sid: str):
        """The live ask a blocked SDK session is waiting on (the dict _emit_ask stored), or None. The kernel's
        _ask_poll calls this for SDK-backed sids instead of scraping a tmux pane (there is none), so the prompt
        replays durably to chat clients — and so the poll never clobbers it with an askLiveClear."""
        return self._pending_ask.get(sid)

    def _forward(self, sess: SdkSession, msg):
        # LIVE TAIL: translate the streamed message to an atom and stash it in memory, AHEAD of the
        # transcript on disk (the SDK stream leads the disk write), then wake the kernel's pusher for an
        # immediate chat push. build_session merges these and the transcript supersedes them by uuid.
        atom = msg_to_atom(msg, sess.sid, sess.resume_sid, int(time.time()),
                           skill_tool_ids=sess._skill_tool_ids)
        if not (atom and atom.get("uuid")):
            return
        _note_skill_tool_ids(atom, sess._skill_tool_ids)   # a Skill tool_use arms its payload's classification
        d = self._live.setdefault(sess.sid, {})
        d[atom["uuid"]] = atom
        _evict_live_overflow(d)                  # safety cap if no client ever drains/prunes — never an echo
        # The stream is the AUTHORITATIVE busy signal: a genuine WORK atom (streamed assistant/tool
        # output — not an input echo, not a /model-style command line) means the CLI is producing RIGHT
        # NOW, so re-assert 'working' if a prior state write settled ahead of it (e.g. a separate turn
        # queued in the CLI that started streaming after the previous turn's Result). Only on the
        # transition, so it never spams the log. This is what makes the signal self-heal without a count.
        if not atom.get("_echo_text") and not atom.get("command") and not atom.get("isApiError") \
                and not sess._cli_working:
            sess._mark("working")   # (an isApiError settle is the turn DYING, not producing — never 'working')
        self._wake_push()

    def _wake_push(self):
        if self._push_cb:
            try:
                self._push_cb()
            except Exception as e:
                self._log("chat push wake failed: %s" % e)

    def _push_session(self, sid: str) -> None:
        """Targeted one-session push (kernel _push_session_now), for per-session events the chat chip
        keys on — today the connect handshake, the exact flip the opening chip stands down on. THREADED:
        the callback builds and serializes that session's payload, which must never run on the session's
        asyncio loop thread (it would stall the stream it is reporting on). Falls back to the plain
        pusher wake when the kernel didn't wire the callback (older kernel / tests)."""
        if not self._push_session_cb:
            self._wake_push()
            return
        def run():
            try:
                self._push_session_cb(sid)
            except Exception as e:
                self._log("session push (%s) failed: %s" % (sid, e))
        threading.Thread(target=run, name="sdk-push-session", daemon=True).start()

    def live_atoms(self, sid: str) -> list:
        """The session's in-memory live-tail atoms (newest last), for build_session to merge ahead of disk."""
        d = self._live.get(sid)
        return sorted(d.values(), key=lambda a: a.get("t", 0)) if d else []

    def prune_live(self, sid: str, tx_uuids, tx_user_texts=(), human_floor: int = 0) -> None:
        """Drop live atoms the transcript has now caught up on — by uuid (assistant/tool/user from the
        stream) or by text (the optimistic input echo, which has a synthetic uuid).

        FIFO floor for echoes — PATH-BEARING ONLY (narrowed, the user 2026-07-20): an input echo whose text
        can't match because the transcript EXTRACTED an image path out of the user text (`_atom_user_text`
        no longer contains the echoed path — the screenshots-piling-up-at-the-bottom bug, the user
        2026-06-25) is retired once the transcript's newest GENUINE-HUMAN turn is at/after the echo's send
        time. The floor used to apply to EVERY echo, justified by "a still-queued send keeps showing via
        the queued indicator" — but since queued sends FORWARD into the CLI mid-turn (2026-07-17) a message
        can be neither queued nor landed, and the blanket floor hid exactly that in-flight message the
        moment any other human record landed. A plain-text echo now prunes ONLY by its own text landing —
        and a genuinely dropped send's echo PERSISTS, so the loss shows (the tmux echo's semantics).
        (Echo-only: real stream atoms have no _echo_text and prune by uuid as before.)"""
        d = self._live.get(sid)
        if not d:
            return
        echo_removed = False
        for k in list(d.keys()):
            a = d[k]
            et = a.get("_echo_text")
            landed = a.get("uuid") in tx_uuids or (et and et in tx_user_texts)
            stale_echo = (bool(et) and human_floor and a.get("t", 0) <= human_floor
                          and not a.get("command") and _path_bearing(et))
            # A COMMAND atom (the CLI's streamed /model, /compact feedback) from a TURN-LESS control
            # request may never get a transcript record to land against — retire it once a genuine human
            # turn postdates it, so the stale confirmation line doesn't ride pinned inside every later
            # turn forever (the user 2026-07-02, with the live_work command exemption).
            stale_cmd = bool(a.get("command")) and human_floor and a.get("t", 0) <= human_floor
            if landed or stale_echo or stale_cmd:
                echo_removed = echo_removed or bool(et and not a.get("command"))
                del d[k]
        if not d:
            self._live.pop(sid, None)
        if echo_removed:
            self._persist_echoes(sid)   # keep the restart mirror in step (empty once everything landed)

    def retire_live_work(self, sid: str) -> None:
        """Drop the sid's live-tail WORK atoms (stream messages — not input echoes, not command feedback)
        at a TURN BOUNDARY: the ResultMessage settle, the reconnect reconcile, or the session process
        going away. Past any of these the stream that produced them is over — everything the transcript
        will keep is already on disk (and prune_live retires it by uuid), so a work atom still here
        belongs to an attempt that produced NO transcript record (an API-errored/rate-limited try, a
        killed process). Left in place it is merged forever, and its live_work forces the turn open —
        the chat chip read WORKING with a 3h20m timer on a session whose turn died in a usage-limit
        retry storm, while the timeline lane said READY (the user 2026-07-03)."""
        d = self._live.get(sid)
        if not d:
            return
        for k in list(d.keys()):
            a = d[k]
            if not a.get("_echo_text") and not a.get("command"):
                # An assistant atom carrying real TEXT that never landed on disk is a reply the user WATCHED
                # stream but the transcript dropped (an API-errored try — the CLI discards the partial and the
                # retry writes a fresh record with a new uuid). Persist it durably BEFORE dropping the live
                # atom, so build_session can interleave it back at its timestamp — dedup'd against the disk so a
                # retry that DID re-reply never doubles. Without this the reply vanishes at settle and only the
                # "Recovered after N retries" note remains where it was (the user 2026-07-21).
                if a.get("type") == "assistant" and not a.get("isApiError"):
                    txt = _atom_text(a)
                    # "(no content)" is the CLI's placeholder for contentless command feedback (an SDK
                    # /clear streams one) — nothing the user watched, nothing to salvage. Persisted, it
                    # resurfaced as a worked reply on the bare command turn (the user 2026-07-27); the
                    # parse-side guard in synthesize_orphans covers markers already written.
                    # VERIFIED AT THE WRITE MOMENT (the user 2026-08-26): a marker claims "this reply
                    # is on disk nowhere", and the claim's precondition — prune_live retired every
                    # landed atom — holds only for sessions the chat BUILDS. A comment thread is never
                    # built (hidden by design), so its landed replies were still live at settle and
                    # EVERY thread reply minted a spurious marker: states/ litter, and the judge's
                    # per-push marker scan grows with it. One tail read of the sid's own transcript
                    # per would-be marker (settles are rare; healthy sessions already pruned) keeps
                    # the salvage honest for every hidden-session shape, threads and future ones. A
                    # genuinely-lost reply (the API-error discard) is on disk nowhere and still mints.
                    if txt.strip() and txt.strip() != "(no content)" \
                            and not self._reply_on_disk(sid, a.get("uuid") or ""):
                        try:
                            append_orphan_reply(self.state_dir, sid, a.get("uuid") or "", txt, t=a.get("t"))
                        except Exception:
                            self._log("orphan-reply persist failed: %s" % traceback.format_exc())
                del d[k]
        if not d:
            self._live.pop(sid, None)

    def _reply_on_disk(self, sid: str, uuid: str) -> bool:
        """Does the sid's transcript already hold this uuid? The orphan salvage's own precondition,
        checked against the file it makes claims about (see retire_live_work). Tail-windowed: the
        settle runs moments after the write, so a landed reply sits near the end; a discarded one's
        uuid appears nowhere at any offset that matters."""
        if not uuid:
            return False
        try:
            reg = read_reg(self.state_dir, sid) or {}
            p = transcript_path(reg.get("cwd") or "", reg.get("lastSid") or sid)
            size = os.path.getsize(p)
            with open(p, "rb") as f:
                if size > 4_000_000:
                    f.seek(size - 4_000_000)
                data = f.read()
            if uuid.encode() not in data:
                return False
            # TEXT-AWARE (T112, guarding the 2026-07-28 textless-twin class): the CLI can persist a
            # record under the SAME uuid with EMPTY text (a thinking-only twin) while the text the
            # user watched streamed only. Byte-containment alone then refused the salvage marker and
            # the reply vanished everywhere. Only a record that actually CARRIES text counts as
            # landed — mirroring landed_text_uuids, the build-time dedup's own rule.
            for ln in data.split(b"\n"):
                if uuid.encode() not in ln or not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("uuid") == uuid and r.get("type") == "assistant" and _atom_text(r).strip():
                    return True
            return False
        except OSError:
            return False

    def live_atom_kinds(self, sid: str) -> list:
        """Read-only DEBUG summary of the sid's live-tail atoms: uuid/type + the flags that decide their
        fate at settle (echo, command, apiError, hasText). The kernel's chat-divergence tripwire logs it
        to pin WHICH atom held a chat turn open after the backend settled — the 2026-07-25 stale-"running"
        chat could not be diagnosed because a restart cleared exactly this state before anyone read it."""
        d = self._live.get(sid)
        if not d:
            return []
        out = []
        for a in list(d.values()):                 # copy: loop threads mutate the dict mid-iteration
            if not isinstance(a, dict):
                continue
            out.append({"uuid": a.get("uuid") or "", "type": a.get("type") or "",
                        "echo": bool(a.get("_echo_text")), "command": bool(a.get("command")),
                        "apiError": bool(a.get("isApiError")), "hasText": bool(_atom_text(a).strip())})
        return out

    def _deliver_rename_ping(self, s) -> bool:
        """Deliver a pending rename ping (reg renameNote) as its OWN turn, at a turn's SETTLE — the
        only window where its record cannot fold into anyone else's (the user 2026-08-25: the
        enqueue-AHEAD form fed the ping and the user's message back-to-back, and the CLI batches
        every message that arrives before a turn starts into ONE user record — the user's own words
        rendered inside the ping's machine bubble, worse than the bug it replaced). Three gates make
        the fold unreachable: delivery only at settle (the user is answered first), only when the
        queue is EMPTY (a queued message would share the pre-turn window), and the drain's feed-hold
        (RENAME_PING_HEAD) keeps the next item back until the ping's turn demonstrably starts — its
        first streamed message, an exact event — after which a racing send lands mid-turn as its own
        record by the CLI's own design. A held note simply waits for a later settle; it survives
        restarts in the reg."""
        try:
            reg = read_reg(self.state_dir, s.sid) or {}
        except Exception:
            return False
        note = reg.get("renameNote")
        if not note:
            return False
        with s._lock:
            if s._pending:
                return False               # a queued turn would share the pre-turn window — hold the note
        self._update_reg(s.sid, renameNote=None)
        s.enqueue("<!-- romp-injected --><!-- romp-system -->" + RENAME_NUDGE % note)
        return True

    def _update_reg(self, sid: str, **fields):
        with self._reg_lock:                       # kernel + loop threads both write (queue mirror);
            reg = read_reg(self.state_dir, sid)    # unserialized RMWs would drop fields
            if reg is None:
                if _reg_path(self.state_dir, sid).exists():
                    # the reg EXISTS but would not read: writing {sid}+fields here GUTS it — no
                    # alive, no name — and a gutted reg vanishes from every listing until a full
                    # rewrite (the 2026-08-31 blink class). Losing one mirror update is the far
                    # smaller harm: the next update lands on the healed read.
                    sys.stderr.write("update_reg: %s unreadable — skipping a %s write rather than "
                                     "gutting the reg\n" % (sid[:8], "/".join(sorted(fields))))
                    return
                reg = {"sid": sid}
            reg.update(fields)
            write_reg(self.state_dir, sid, reg)

    def _reg_for_flip(self, sid: str):
        """The RMW base for a VISIBILITY FLIP (kill/resume/promote) when the disk read fails but
        the reg exists: the scan's last good cached row. A flip that silently no-ops on a bad read
        leaves a killed session listed alive — or a revived one dead — until an unrelated write
        heals it (review find, 2026-08-31); flips are exactly the writes that must never be lost."""
        reg = read_reg(self.state_dir, sid)
        if reg is not None:
            return reg
        p = str(_reg_path(self.state_dir, sid))
        hit = _REG_CACHE.get(p)
        if hit is not None and os.path.exists(p):
            sys.stderr.write("reg flip for %s: disk read failed — using the scan's last good row\n"
                             % sid[:8])
            return dict(hit[1])
        return None

    def _write_reg_locked(self, sid: str, reg: dict) -> None:
        """Full-reg writes from backend methods take the SAME lock the RMW mirror holds — a raw
        write racing _update_reg's read-modify-write could get its fields overwritten by the RMW's
        pre-write snapshot (kill's alive=False undone = a dead session resurrected in the listing;
        resume's alive=True undone = a live one blinked out of it)."""
        with self._reg_lock:
            write_reg(self.state_dir, sid, reg)

    def _record_launch_error(self, sess: SdkSession, exc: BaseException) -> None:
        """A session's CLI refused to start — persist WHY onto the session so the user is told, loudly,
        on the surface they were typing into. The reg is the right home: it outlives this thread (which
        is dying) and it is what the boot reconcile and the chat build already read, so the error
        survives a kernel restart exactly like the queue it is holding up.

        `limit` marks the account-out-of-usage flavor, which reads differently to the user: nothing is
        broken, the queue is simply parked until the window resets (kernel _limit_hold), and the message
        they typed is still there. Anything else is a real failure and gets the plain error card."""
        # A missing dependency gets the REMEDY as its text, not the raw ModuleNotFoundError: "No module
        # named 'claude_agent_sdk'" tells a user nothing about what to run.
        dep = isinstance(exc, ImportError)
        tail = "" if dep else sess.stderr_tail()   # what the CLI itself said before it exited
        text = SDK_MISSING_TEXT if dep else launch_failure_text(exc, tail)
        rec = {"text": text, "at": int(time.time()), "limit": is_launch_limit(text), "dep": dep}
        try:
            self._update_reg(sess.sid, launchError=rec)
        except Exception:
            self._log("record launch error (%s): %s" % (sess.name, traceback.format_exc()))
        self._log("session %s: claude CLI failed to start%s — %s"
                  % (sess.name, " (account usage limit)" if rec["limit"] else "", text))
        # The FULL captured stderr to the log, once, at the failure. The card gets one truncated line
        # (it has to stay glanceable); the log is where the whole thing belongs, and it is what the
        # user goes looking for the moment a session won't start (the user 2026-07-29, whose fleet all
        # died at launch and whose only clue was an SDK placeholder telling them to read a stderr that
        # was never captured). Skipped when the tail is already the card's text, so it never doubles.
        if tail and tail.strip() != text.strip():
            self._log("session %s: claude CLI stderr (last %d lines):\n%s"
                      % (sess.name, len(sess._stderr_tail), tail))
        self._poke()

    def _clear_launch_error(self, sid: str) -> None:
        """Drop a recorded launch failure — called from the connect that DISPROVES it. Read-then-write so
        a session that never failed doesn't churn the reg on every reconnect."""
        try:
            if not (read_reg(self.state_dir, sid) or {}).get("launchError"):
                return
            self._update_reg(sid, launchError=None)
        except Exception:
            self._log("clear launch error (%s): %s" % (sid, traceback.format_exc()))
        self._poke()

    def launch_error(self, sid: str):
        """The reason this session's CLI could not start, or None — {text, at, limit}. The kernel reads
        it for the chat's error card and for the usage-limit queue hold (see SessionBackend.launch_error).

        A MISSING SDK outranks any per-session record: it is true of every session immediately, needs no
        session to have died to be known, and it is the actionable one."""
        if self._sdk_missing:
            return {"text": SDK_MISSING_TEXT, "at": 0, "limit": False, "dep": True}
        try:
            rec = (read_reg(self.state_dir, str(sid)) or {}).get("launchError")
        except Exception:
            return None
        return rec if isinstance(rec, dict) and rec.get("text") else None

    def _on_session_gone(self, sess: SdkSession):
        with self._lock:
            if self.sessions.get(sess.sid) is sess:
                self.sessions.pop(sess.sid, None)
        if not sess.ended:
            if sess.inflight > 0 and not sess._interrupted:
                # ABNORMAL death mid-turn (killed / crashed — not a user interrupt, not a clean
                # ResultMessage finish, not our own shutdown). Do NOT settle 'waiting': that masked
                # the cut (2026-07-06: reaped sessions settled 'waiting', so last_state_value never
                # read 'working' and nothing ever resumed them — the silent stall). The trailing
                # 'working' IS the cut marker; heal by resuming, bounded.
                self._heal_cut_session(sess)
            else:
                # process exited on its own while idle (crash / EOF) — settle state; next send resumes
                append_state(self.state_dir, sess.sid, "waiting")
        # the thread (and its claude subprocess) is gone, so any background work is too — clear a stale
        # awaiting overlay so the session doesn't read working/awaiting forever (reorder_bug 2026-06-24).
        self._heal_stale_awaiting(sess.sid)
        self.retire_live_work(sess.sid)   # no stream left → unlanded work atoms must not hold the turn open
        if sess._model_pending:           # a switch that never resolved before the thread died → don't trap the dots
            sess._model_pending = ""
            try:
                self._update_reg(sess.sid, liveModel=_alias_label(sess.chosen_model), modelPending=False)
            except Exception as e:
                self._log("session gone (%s): model-pending clear failed: %s" % (sess.name, e))
        if sess._effort_pending:          # an /effort reconnect that never landed before the thread died → clear the dots/notice
            sess._effort_pending = ""
            try:
                self._update_reg(sess.sid, effortPending=False)
            except Exception as e:
                self._log("session gone (%s): effort-pending clear failed: %s" % (sess.name, e))
        # Background tasks are the CLI's children, so they just died too. A session idle-waiting on a
        # timer/watcher would wait FOREVER for a completion that can never arrive — tell it, visibly,
        # and wake it so it can relaunch what still matters (the user 2026-07-11: nimbus's campaign
        # watcher died with a kernel restart and the session never knew). Skipped when `ended` (our own
        # drain/shutdown): the reg's bgTasks mirror survives for the NEXT kernel's boot reconcile to
        # deliver the same notice.
        died = sess._live_bg_tasks()
        if died and not sess.ended:
            try:
                note = task_death_notice(died)
                with self._reg_lock:
                    reg = read_reg(self.state_dir, sess.sid) or {"sid": sess.sid}
                    reg["queue"] = [t for t in (reg.get("queue") or [])
                                    if isinstance(t, str) and t and t != note] + [note]
                    reg["bgTasks"] = []           # reported — never re-notify for the same deaths
                    write_reg(self.state_dir, sess.sid, reg)
                self._ensure(sess.sid)            # wake it to hear the notice (no-op if the heal respawned)
            except Exception:
                self._log("bg-task death notice (%s): %s" % (sess.name, traceback.format_exc()))
        self._poke()

    def _heal_cut_session(self, sess: SdkSession):
        """A session's claude process died ABNORMALLY mid-turn while the kernel stayed up (killed by
        something external, OOM, a transport crash — the boot reconcile only heals cuts across a
        kernel RESTART, so without this the session stalls until the next restart). Resume it the
        same way the reconcile does: prepend the visible crash nudge to the persisted queue and
        re-ensure. BOUNDED to one resume per cut — the counter only resets when a turn COMPLETES
        (_turn_completed), so a CLI that keeps dying before finishing a turn is a crash loop and is
        left cut (loudly) for the next boot reconcile instead of respawning forever."""
        sid = sess.sid
        with self._lock:
            attempts = self._heal_attempts.get(sid, 0)
            self._heal_attempts[sid] = attempts + 1
        if attempts >= 1:
            self._log("session %s: claude process died mid-turn AGAIN before completing a turn — "
                      "crash loop; NOT resuming again, turn left cut for the next kernel restart"
                      % sess.name)
            return
        self._log("session %s: claude process died mid-turn — resuming with history intact" % sess.name)
        try:
            with self._reg_lock:
                reg = read_reg(self.state_dir, sid) or {"sid": sid}
                reg["queue"] = [CRASH_RESUME_NUDGE] + [t for t in (reg.get("queue") or [])
                                                       if isinstance(t, str) and t and t != CRASH_RESUME_NUDGE]
                write_reg(self.state_dir, sid, reg)
            append_machine_cut(self.state_dir, sid, "crash")   # romp's cut, romp's resume — never a user stop
            self._ensure(sid)
        except Exception:
            self._log("session %s: crash-resume FAILED (turn left cut for the next kernel restart): %s"
                      % (sess.name, traceback.format_exc()))

    def _turn_completed(self, sid: str):
        """A turn's ResultMessage landed — the session is demonstrably able to finish turns again, so
        re-arm the crash-resume budget (_heal_cut_session's one-resume-per-cut bound)."""
        with self._lock:
            self._heal_attempts.pop(sid, None)
