#!/usr/bin/env python3
"""romp-event-model — the rebuilt bottom-layer parser (docs/event-model.md).

Turns a romp session's transcript(s) into the Session -> Turn -> Atom tree the
design doc pins down: the Claude streaming protocol made graph-aware. An atom is
a streaming message, a turn is the user-to-`end_turn` cycle, and the on-disk
transcript is those messages plus the graph metadata needed to rebuild them
after rewinds and resume-forks.

NOT wired into the live pipeline. This is a standalone module built for
side-by-side evaluation against bin/romp-events (inventory Decision 1); it does
NO summaries / relevance / asks / links / model calls — only the event layer.

The Session/Turn/Atom shapes are substrate-neutral. Everything specific to
rebuilding them from the append-only on-disk graph lives in the FILE ADAPTER
section and only there; on a future stream substrate the same tree is filled
directly (steps that recover linearity, `ended`, and idle simply disappear).

CLI:
  romp-event-model --test <transcript>   # human dump of one session's turn/atom tree
  romp-event-model --emit <transcript>   # the Session tree as JSON

Auxiliary inputs the file adapter may read (same category as the transcript):
  states/<sid>.jsonl        -> idle atoms (real idle transitions, not a silence heuristic)
                               + salvaged assistant atoms (orphanReply markers — replies the
                               transcript lost to an API-errored try; judge parse only)
  timeline/messages.jsonl   -> peer rompUuid for a postal atom (join on the msg id)
"""
import copy, json, os, re, sys, time, hashlib, threading
from datetime import datetime
from pathlib import Path

HOME     = Path.home()
STATE    = Path(os.environ.get("ROMP_STATE_DIR")   # per-kernel state root override (plans/multi-kernel.md)
                or Path(os.environ.get("XDG_STATE_HOME", str(HOME / ".local/state"))) / "romp")
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")) / "projects"   # per-kernel Claude root (plans/multi-kernel.md phase 2)
NAMES    = STATE / "names"
STATES_DIR   = STATE / "states"
MESSAGES_LOG = STATE / "timeline" / "messages.jsonl"

# A turn ends when the model hands the floor back: stream `end_turn` / `stop_sequence`.
# Mid-turn the model stops with `tool_use` (a tool cycle) — that does NOT end the turn.
END_STOPS = ("end_turn", "stop_sequence")
# The toolUseResult keys a consumer actually reads (Edit's structuredPatch → diffRows,
# AskUserQuestion's answers map → the answered box). The atom carry is GATED on one of these
# being present: an unconditional dict carry held every Read result's full file bytes in the
# parse cache by reference — ~a fifth of transcript bytes on read-heavy sessions — for shapes
# nothing reads. Widen this set when a new consumer appears; never revert to carry-all.
TUR_CONSUMED_KEYS = frozenset(("answers", "structuredPatch"))
# romp's own postal marker, injected into a delivered message body. It is the ONLY
# postal signal — never the generic "Stop hook feedback:" prefix (any blocking Stop
# hook produces that). The sender rompUuid is resolved from timeline/messages.jsonl
# by joining this id (the on-disk marker carries the id but not the sender).
# COMMENT FORM ONLY — the same rule ROMP_INJECT_RE/ROMP_AUTO_RE were given on 2026-07-08, and the
# rule docs/event-model.md already documents ("Postal is detected by the `<!-- romp-msg-id: <id> -->`
# marker"). Both emitters have only ever written the literal comment (postal_service format_inbox /
# format_push, where the file calls that marker a stable CONSUMER CONTRACT), but a bare word-match
# also fired on text that merely MENTIONS the marker: an agent quoting the mail it just received, a
# hook or tool output echoing one (a grep of a transcript, a fetched page), a human prompt about the
# marker itself. The failure is not cosmetic — with no matching id in the log author_of still
# returned {"peer": None}, and that is a dict, so the segment reads peer-rather-than-human and both
# the planner and the courier drop it: the user's ask silently gets no card.
POSTAL_RE = re.compile(r"<!--\s*romp-msg-id:\s*(\S+?)\s*-->")
POSTAL_KIND_RE = re.compile(r"<!--\s*romp-msg-kind:\s*(delegate|coordinate|question)\s*-->")
# Both markers, IN ORDER, so a sender / message id / declared kind always describe the SAME message.
# A drain writes id-then-kind per message and concatenates every pending message into one injected
# text (postal_service format_inbox/format_push), so a two-message delivery carries two of each —
# and three separate scans of that text picked three different answers: the author from the last
# marker, the id and the kind from the first. That filed one peer's identity against another peer's
# message, planting the delegation-tracking node on the wrong board. postal_pairs() below is the one
# parser author_of and both judge scans now share.
_POSTAL_ANY_RE = re.compile(r"<!--\s*romp-msg-(id|kind):\s*(\S+?)\s*-->")
_POSTAL_KINDS = ("delegate", "coordinate", "question")
# romp's marker on a message IT injected straight into a pane (a feed NUDGE / auto-nudge / Retry — NOT a
# peer message, and NOT a follow-up YOU typed). It means "render this as a romp-injected system message"
# (the gray bubble), distinct from a human prompt or a peer's postal message. ONLY romp-injected authors
# romp: romp-goal-id is orthogonal "which goal" metadata that rides EVERY feed follow-up, INCLUDING ones
# you type yourself — those are yours (blue human bubble), so a goal-id alone must NOT author romp; the
# kernel adds romp-injected for nudges only (the user 2026-06-20).
# COMMENT FORM ONLY (the user 2026-07-08): every real emitter writes the literal `<!-- romp-injected -->`
# (kernel _followup_body / RETRY_MSG, the sdk backend's restart notices; romp-judge's NUDGE_MARKER_RE
# already matches this way). A bare word-match also fired on message CONTENT that merely *mentions* the
# marker — the user's typed follow-up quoted a card summary discussing romp-injected and rendered as a
# GRAY ROMP CARD. \s* so the absorbed-atom path's historical whitespace-collapse still matches.
ROMP_INJECT_RE = re.compile(r"<!--\s*romp-injected\s*-->")
# romp-AUTO: an AUTO-nudge (the kernel's background _auto_nudge_tick), distinct from a Nudge BUTTON click or a
# typed follow-up — both of which are romp-injected too. Only auto-nudges (+ postal) are "from romp" for the
# romp-logo marker; the user's own button/follow-ups are not (the user 2026-06-23). Rides alongside
# romp-injected; an atom carrying it gets atom["rompAuto"]=True for the timeline/chat to mark.
# Comment form only, same reason as ROMP_INJECT_RE (content mentioning the marker must not match).
ROMP_AUTO_RE = re.compile(r"<!--\s*romp-auto\s*-->")
# A sender-declared RENDER HINT on an injected message (the user 2026-08-15): auto-generated text — a
# kickoff template, a scripted brief — carries `<!-- romp-tag: <label> -->` (romp send --tag, or the
# marker appended by hand) so the chat shows it as machine-sent under that label instead of posing it
# as the user's typed words. The label is the SENDER's own word; romp attaches no meaning to it — a
# render hint, not a message type. Comment form only, same reason as ROMP_INJECT_RE.
MSG_TAG_RE = re.compile(r"<!--\s*romp-tag:\s*([A-Za-z0-9][A-Za-z0-9-]{0,23})\s*-->")
# Harness-injected SYSTEM wrappers that are NOT the user: a background-task completion (`<task-notification>`,
# fired when a backgrounded Agent/Task finishes) and `<system-reminder>` blocks. In an SDK session these arrive
# over the stream as promptSource "sdk", so sdk_human would author them 'human' → _is_opener opens a turn →
# the planner force-pins a junk goal titled "<task-notification>" (the user 2026-06-30, screenshot). Anchored
# at the START so a real user message with a reminder APPENDED isn't caught (the kernel splits those off).
SYSTEM_WRAPPER_RE = re.compile(r"^\s*<(?:task-notification|system-reminder)\b")
# Claude Code's NATIVE teammate/agent-message channel — one agent messages another, DISTINCT from romp's
# own postal bus (no romp-msg-id). It's delivered as a promptSource "sdk" user record whose text is a
# <prompt> wrapper: "Another Claude session sent a message:" + one or more <teammate-message
# teammate_id="…" color="…" [summary="…"]>body</teammate-message> blocks + a fixed "permission laundering"
# boilerplate. We recognize it so the chat renders it as its OWN collapsed card, not a blue "you typed
# this" bubble (the user 2026-07-05: idle_notification coordination JSON showed as a human message).
# Anchored at the START (optionally inside a one-level <prompt>/<unit> wrapper) so it matches a real
# DELIVERY and NOT a conversation SUMMARY that merely quotes one ("<turn>\nUSER ASKED: Another Claude…").
TEAMMATE_MSG_RE = re.compile(r"^\s*(?:<\w+>\s*)?Another Claude session sent a message:", re.I)
TEAMMATE_BLOCK_RE = re.compile(r"<teammate-message\b([^>]*)>(.*?)</teammate-message>", re.S)
# Claude Code's slash-command transcript wrappers. The INVOCATION (`<command-name>`) and its OUTPUT
# (`<local-command-stdout>`) become a tracked COMMAND TURN (the user 2026-06-29) — see atoms(); the rest
# (`<command-message|args|contents>`, `<local-command-caveat>`) stay skipped as harness noise.
CMD_WRAP_RE = re.compile(r"^\s*<(?:command-(?:name|message|args|contents)|local-command-(?:stdout|caveat))>")
COMMAND_NAME_RE = re.compile(r"^\s*<command-name>([^<]*)</command-name>")           # the slash command itself, e.g. "/usage"
# ...but the CLI does NOT fix the wrapper ORDER: a built-in writes <command-name> first, while a SKILL /
# custom command writes <command-message> first ("/jld" → "<command-message>jld</command-message>\n
# <command-name>/jld</command-name>…"). Anchored-only matching therefore MISSED every skill invocation: the
# record fell through to the harness-noise skip, so the command never became an atom, and the work it
# triggered was absorbed into whatever segment came BEFORE it — a JLD session ran with its request buried in
# the preceding "/model" command segment and no card of its own (the user 2026-07-22). Find the tag anywhere,
# but ONLY inside a record that already begins with a command wrapper (CMD_WRAP_RE), so prose that merely
# quotes "<command-name>" can never be mistaken for an invocation.
COMMAND_NAME_ANY_RE = re.compile(r"<command-name>([^<]*)</command-name>")
COMMAND_ARGS_RE = re.compile(r"<command-args>([\s\S]*?)</command-args>")            # its arguments (often empty)
LOCAL_STDOUT_RE = re.compile(r"^\s*<local-command-stdout>([\s\S]*?)</local-command-stdout>")   # the command's output
# A slash command's stdout is captured from the TUI VERBATIM, so it can carry ANSI SGR color codes
# (e.g. /rate-limit-options prints "\x1b[38;5;114mRemoved monthly spend limit\x1b[39m" in green). The
# ESC byte is invisible but the "[38;5;114m…[39m" renders as LITERAL text in the chat (the user
# 2026-07-16). Strip the full CSI/SGR family at the atom source — the one place both the chat and the
# timeline read — so the codes never reach any renderer. Only local-command-stdout needs this; model
# API text never contains ANSI.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(s: str) -> str:
    """Remove ANSI CSI/SGR escape sequences (color, cursor) from captured terminal output."""
    return ANSI_RE.sub("", s) if s else s
# The Skill tool's INSTRUCTIONS record (the user 2026-07-08): after a `Skill` tool_use + its "Launching
# skill: X" tool_result, the CLI writes the skill's full markdown as an isMeta user record opening with
# this line. It's the ONE isMeta payload worth keeping — surfaced as a flagged, content-EMPTY atom (the
# text rides atom["skillMd"], so no assistant-text reader — judge work text, captions, summary anchors —
# ever mistakes the skill's instructions for something the agent wrote). The kernel folds it into the
# invoking Skill tool event, collapsed by default.
SKILL_CONTENT_RE = re.compile(r"^\s*Base directory for this skill:")
SKILL_MD_CAP = 16000              # transport cap for the joined skill markdown (skills run ~2-15k chars)
# An image fed to the model via a tool (a Read of a PNG, a screenshot) — Claude Code emits a synthetic
# user record carrying JUST this human-readable placeholder alongside the image block. On disk it's isMeta
# (skipped below), but that flag is absent on the SDK live stream, so the twin skip in sdk_backend keys on
# this pattern instead; matching here too keeps the two adapters in lockstep and covers any Claude build
# that omits isMeta on the record. The `:` after Image distinguishes it from the composer's `[Image #N]`
# paste chips, which never stand alone as a whole turn.
IMG_ECHO_RE = re.compile(r"^\[Image:[^\]]*\]$")
SUMMARY_CAP = 8000                # cap the compaction-summary text attached to a compact_boundary atom (a raw
#   summary runs ~16k chars; the head carries the key sections, and it re-ships with the tail — keep it bounded)


# ───────────────────────── small helpers ─────────────────────────
def parse_z(s):
    """Transcript timestamp (ISO-8601 UTC, '…Z') -> epoch int, or None.

    Every transcript record carries a timestamp, so this runs tens of thousands of times per fleet parse
    — the C-accelerated datetime.fromisoformat (3.11+, accepts a '+00:00' offset + fractional secs) is
    ~13x faster than strptime and was a real chunk of "startup is slow" (the user 2026-07-03). strptime
    stays as a fallback for any exotic form fromisoformat rejects, so behavior is unchanged."""
    if not s:
        return None
    s = s.strip()
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        pass
    s = s.replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except Exception:
            pass
    return None


def _result_text(content):
    """The text of a tool_result's content, whether it's a plain string or a list of {type:text} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _parse_task_notification(txt):
    """Parse a <task-notification> block's fields, or None if it isn't one. Keys on the exact tags the
    harness emits (status / summary / output-file / tool-use-id), not a guess. tool_use_id is the join
    key back to the LAUNCHING tool_use — the standalone (string-content) notification shape carries it
    only inside the text, unlike the older tool_result wrapper whose block names it."""
    if not txt or "<task-notification>" not in txt:
        return None

    def fld(tag):
        a = txt.find("<" + tag + ">")
        if a < 0:
            return ""
        a += len(tag) + 2
        b = txt.find("</" + tag + ">", a)
        return txt[a:b].strip() if b >= 0 else ""
    st = fld("status")
    # has_status: whether <status> was PRESENT, distinct from the "completed" default. A Monitor's
    # per-EVENT notification carries no status tag (only its terminal one does) — without this bit the
    # default would let a wrapped event read as "completed" and end a watch that is still live.
    return {"status": (st or "completed").lower(), "has_status": bool(st), "summary": fld("summary"),
            "output_file": fld("output-file"), "tool_use_id": fld("tool-use-id")}


# A non-persistent Monitor records its own lifetime ceiling at launch (timeout_ms — the harness kills
# it at the deadline), so a scan row still "running" past deadline+grace is a record whose terminal
# notification can never arrive (the CLI died with the monitor out, and the transcript never learns).
# Consumers apply this with THEIR now — baking it into the scan would freeze inside the mtime caches,
# which an idle transcript never busts. The grace absorbs kill/notify latency.
def _bg_expired(task, now, grace=120.0):
    dl = (task or {}).get("deadline")
    if (task or {}).get("deadlineSrc") == "hook":
        grace = 5.0   # a hook-recorded deadline is the harness's own kill moment (Monitor's
        #               required timeout_ms, journaled at launch) — exact, so only clock skew
    return bool(dl) and now > dl + grace


# The detail an agent/workflow row expands to (the Agent prompt / the Workflow script) is clipped:
# the box's detail pre scrolls, but a workflow script can run to 512KB and the payload ships whole.
_DETAIL_CAP = 4000


def _clip_detail(text):
    text = str(text or "").strip()
    return text if len(text) <= _DETAIL_CAP else text[:_DETAIL_CAP] + "\n… (truncated)"


def _scan_bg_tasks(path, want_all=False):
    """Walk the transcript pairing async LAUNCHES with their <task-notification> results, and surface a task
    ONLY while it's still RUNNING (in flight across turns). A finished task drops out the instant its result
    lands. Newest-launched first, capped. No output content here (a running task's output grows independently
    of the transcript). Shared substrate: the kernel's chat box + awaiting sources read it through their
    mtime caches, and the judge's settled gate reads it as the DURABLE awaited-work source — the pairing
    lives in the transcript, so unlike any live snapshot it survives a kernel restart (2026-08-08).

    Launches come in THREE durable shapes: a Bash tool_use with run_in_background:true, a non-persistent
    Monitor tool_use (see below), and an async
    Agent/Workflow dispatch — its ack is a user record whose TOP-LEVEL toolUseResult says
    isAsync/"async_launched" (the tool_result block names the launching tool_use id). The ack names the
    work at best in one line (description / the workflow meta's summary), so the LAUNCHING tool_use is
    remembered too: its description is the gist when the ack has none, and its full ask — the Agent
    prompt / the Workflow script — rides `command`, the detail block the box already expands (the user
    2026-08-15, whose background agent expanded to a generic label with nothing inside). The ack's
    taskType rides `type`, so the scan rows carry the same agent-vs-shell fact the lifecycle set does.
    Results come in THREE shapes: the notification inside a tool_result block (the older wrapper), a
    standalone user record whose message.content IS the notification string (the current dominant shape —
    missing this left finished tasks reading 'running' forever), and a queue-operation enqueue holding
    the notification while the session is busy — the task itself is already finished the moment any of
    the three exists.
    Returns [{id,status,summary,command,outputFile}] (+ type on agent/workflow rows; + endT — the
    transcript time its result LANDED — on rows a notification has terminal-marked, so the awaiting-stamp
    lift can tell a return that ENDED a stamped wait from one the stamping judge had already seen)."""
    state = _bg_fresh(want_all)
    for o in _read_jsonl(path):
        if isinstance(o, dict):
            _bg_step(state, o)
    return _bg_finish(state, want_all)


def _bg_fresh(want_all=False):
    """A fresh pairing state. `all`: keep every task's row (the launch-ordered history view); otherwise a
    task that reaches a terminal status is dropped from the state at once — the running-only view can
    never show it again (no record the harness emits returns an existing task to "running"; a literal
    <status>running</status> notification after a terminal one is the one shape the old whole-file walk
    would have resurrected, and the fold refuses), so a long-lived
    transcript's fold state stays the size of its in-flight work, not its history. `done` is that view's
    tombstone set: transcripts DO replay records — a duplicate async ack (same tool-use id, even the same
    uuid) can land after the terminal notification — and with the row gone such a replay would pass the
    `tid not in tasks` creation guards and resurrect the task as running, where a retained row ignored it.
    The tombstone keeps that answer while releasing the row and its prompt text."""
    return {"tasks": {}, "order": [], "dispatch": {}, "all": bool(want_all), "done": set()}


def _bg_forget_terminal(state, tid):
    if not state["all"] and state["tasks"].get(tid, {}).get("status") != "running":
        state["done"].add(tid)
        state["tasks"].pop(tid, None)
        try:
            state["order"].remove(tid)
        except ValueError:
            pass


def _bg_step(state, o):
    """One transcript record of the pairing — see _scan_bg_tasks. Returns the state (the fold_records
    contract)."""
    tasks, order, dispatch, done = state["tasks"], state["order"], state["dispatch"], state["done"]

    def _mark(note, end_t=None):
        # a notification keyed by its INNER <tool-use-id> (the string/queue shapes have no wrapper block)
        tid = (note or {}).get("tool_use_id")
        if tid and tid in tasks:
            if tasks[tid].get("monitor") and not note.get("has_status"):
                return                     # a monitor EVENT (no <status> tag) — the watch is still live
            tasks[tid].update(status=note["status"], outputFile=note["output_file"],
                              summary=note["summary"] or tasks[tid]["summary"])
            if end_t:                      # WHEN the result landed — the awaiting-stamp lift keys the
                tasks[tid]["endT"] = end_t  # "returned after the stamp was written" test on it (2026-08-16)
            _bg_forget_terminal(state, tid)

    t = o.get("type")
    c = (o.get("message") or {}).get("content")
    if t == "assistant" and isinstance(c, list):
        for b in c:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            inp = b.get("input") or {}
            if b.get("name") in ("Agent", "Task", "Workflow") and b.get("id") \
                    and b["id"] not in tasks and b["id"] not in done:
                # remember the dispatch's own words — consumed by its async ack below, or
                # right here when an explicit run_in_background rides the input (the dominant
                # real Agent shape, which registers via the launch branch below instead). A
                # replayed launch record for a task already registered or already returned can
                # never be consumed (both creation paths refuse the id), so it is not captured.
                dispatch[b["id"]] = {
                    "desc": str(inp.get("description") or "").strip(),
                    "detail": _clip_detail(inp.get("prompt") or inp.get("script")
                                           or ("script: " + str(inp["scriptPath"])
                                               if inp.get("scriptPath") else "")),
                    "type": "local_workflow" if b.get("name") == "Workflow" else "local_agent"}
            # The THIRD durable launch shape: a Monitor tool_use. A non-persistent monitor is
            # dispatched background work exactly like a backgrounded Bash — a session idle
            # behind one read as plain 'ready', its goal stamps could lift only by the 6h
            # backstop, and the nudge gates couldn't see the wait. A PERSISTENT monitor is
            # skipped: a session-length subscription (a log tail) never returns, so counting it
            # would hold "awaiting" forever — it is furniture, not awaited work.
            is_mon = b.get("name") == "Monitor"
            if is_mon and inp.get("persistent"):
                continue
            if not (inp.get("run_in_background") or is_mon):
                continue
            tid = b.get("id")
            if tid and tid not in tasks and tid not in done:
                tasks[tid] = {"id": tid, "status": "running", "t": parse_z(o.get("timestamp")),
                              "summary": (inp.get("description") or b.get("name") or "Background task"),
                              "command": inp.get("command") or (inp.get("ws") or {}).get("url", ""),
                              "outputFile": ""}
                d = dispatch.pop(tid, None)
                if d:   # an Agent/Task/Workflow with an explicit run_in_background lands
                        # HERE, not at its ack (tid already registered) — same enrichment
                    tasks[tid]["command"] = tasks[tid]["command"] or d["detail"]
                    tasks[tid]["type"] = d["type"]
                if is_mon:
                    tasks[tid]["monitor"] = True
                    # its recorded lifetime ceiling → the deadline consumers expire on
                    # (see _bg_expired); the harness clamps timeout_ms to [1s, 1h]
                    tmo = inp.get("timeout_ms")
                    tmo = float(tmo) if isinstance(tmo, (int, float)) else 300000.0
                    if tasks[tid]["t"]:
                        tasks[tid]["deadline"] = tasks[tid]["t"] + min(max(tmo, 1000.0), 3600000.0) / 1000.0
                order.append(tid)
    elif t == "user" and isinstance(c, list):
        tur = o.get("toolUseResult")
        tur = tur if isinstance(tur, dict) else {}
        async_launch = bool(tur.get("isAsync")) or tur.get("status") == "async_launched"
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if not async_launch:
                    # the tool_use's ONE result has landed synchronously — a remembered Agent/Task/Workflow
                    # dispatch can no longer be acked async, so its words are dead weight in the state
                    dispatch.pop(tid, None)
                if async_launch and tid and tid not in tasks and tid not in done:
                    # an async Agent/Workflow dispatch ack — the durable "this work is now
                    # running" record; the gist prefers the ack's own words, the launching
                    # tool_use fills what the ack omits (see docstring). Acks carry the ask
                    # too (prompt / scriptPath) — the fallback when the launch predates the
                    # transcript tail or the block went unseen.
                    d = dispatch.pop(tid, {})
                    wf = tur.get("workflowName")
                    tasks[tid] = {"id": tid, "status": "running", "t": parse_z(o.get("timestamp")),
                                  "summary": (tur.get("description") or tur.get("summary")
                                              or d.get("desc")
                                              or ("workflow " + str(wf) if wf else "Background agent")),
                                  "command": d.get("detail")
                                             or _clip_detail(tur.get("prompt")
                                                             or ("script: " + str(tur["scriptPath"])
                                                                 if tur.get("scriptPath") else "")),
                                  "outputFile": tur.get("outputFile") or ""}
                    if tur.get("taskType") or d.get("type"):
                        tasks[tid]["type"] = tur.get("taskType") or d["type"]
                    order.append(tid)
                    continue
                if async_launch and tid in tasks and not b.get("is_error") \
                        and tasks[tid]["status"] == "running":
                    # the ack of a launch the assistant branch already registered (explicit
                    # run_in_background): the ack still owns outputFile/taskType — fill what
                    # the launch row lacks, never overwrite what it has
                    tk = tasks[tid]
                    tk["outputFile"] = tk["outputFile"] or tur.get("outputFile") or ""
                    if tur.get("taskType"):
                        tk["type"] = tur["taskType"]
                    if not tk["command"]:
                        tk["command"] = _clip_detail(tur.get("prompt") or "")
                    continue
                note = _parse_task_notification(_result_text(b.get("content")))
                if tid in tasks and note:      # its result landed → mark it done; the keep-filter drops it
                    if tasks[tid].get("monitor") and not note.get("has_status"):
                        continue               # a wrapped monitor EVENT — not a terminal (see _mark)
                    tasks[tid].update(status=note["status"], outputFile=note["output_file"],
                                      summary=note["summary"] or tasks[tid]["summary"])
                    et = parse_z(o.get("timestamp"))
                    if et:                     # the return's moment (see _mark)
                        tasks[tid]["endT"] = et
                    _bg_forget_terminal(state, tid)
                elif tid in tasks and note is None and b.get("is_error") \
                        and tasks[tid]["status"] == "running":
                    # the LAUNCH's own ack errored (refused permission, bad input) → nothing ever
                    # started, and no notification will ever come. Without this, the phantom
                    # reads "running" forever and holds awaiting/nudge gates open on nothing.
                    tasks[tid]["status"] = "failed"
                    et = parse_z(o.get("timestamp"))
                    if et:
                        tasks[tid]["endT"] = et
                    _bg_forget_terminal(state, tid)
    elif t == "user" and isinstance(c, str):
        _mark(_parse_task_notification(c), parse_z(o.get("timestamp")))
    elif t == "queue-operation" and o.get("operation") == "enqueue":
        _mark(_parse_task_notification(o.get("content") or ""), parse_z(o.get("timestamp")))
    return state


def _bg_finish(state, want_all=False):
    """The scan's answer from a pairing state — row COPIES, so a holder of one answer is never changed by
    the next fold step (fold_records deep-copies the cached state before folding, and a caller scribbling
    on its rows must not reach the cache either)."""
    tasks, order = state["tasks"], state["order"]
    if want_all:
        # EVERY task the transcript knows, launch-ordered, each carrying its launch `t` and its CURRENT
        # status (still "running", or the terminal status its notification reported). The awaiting-stamp
        # lift (_lift_spent_awaiting) needs the RETURNED ones too: "this goal's dispatches have all come
        # back" is precisely the event that ends a wait, and the running-only view cannot express it.
        return [dict(tasks[tid]) for tid in order]
    keep = [dict(tasks[tid]) for tid in order if tasks[tid]["status"] == "running"]
    keep.reverse()
    return keep[:60]


def scan_bg_tasks_cached(path, cache, want_all=False):
    """_scan_bg_tasks folded append-incrementally through `cache` (fold_records): a changed transcript
    steps only its appended records instead of re-pairing the whole file — the kernel's chat box, awaiting
    source and awaiting-stamp lift, and the judge's settled gate, all asked per push per session, and
    each re-walked a working session's entire transcript on every streamed record (measured live
    2026-09-02: four such readers over one 180 MB transcript held the push loop at a full core). `cache`
    is the caller's dict (the kernel keeps a running-only and an every-task cache; the judge its own), so
    the two views never invalidate each other and a test's `.clear()` still forces a re-fold."""
    state = fold_records(cache, path, lambda: _bg_fresh(want_all), _bg_step)
    if state["all"] != bool(want_all):                    # the view is baked into the state at init: a cache handed to
        raise ValueError("bg fold cache for %s is shaped for want_all=%r, asked for %r"   # both views would answer
                         % (path, state["all"], bool(want_all)))                          # one of them wrong
    return _bg_finish(state, want_all)


def _read_jsonl(path):
    """Yield parsed json objects from a .jsonl file; [] on any error."""
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


# Append-incremental transcript reads (the user 2026-07-05: the dashboard re-parsed a 40MB streaming
# transcript FROM BYTE ZERO on every push — ~0.6s of json.loads per append, saturating the push loop and
# queueing every click behind it). Transcripts are append-only, so cache each file's parsed records with
# the byte offset of the last COMPLETE line: a grown file loads only the appended bytes. Guards, in order:
#  - same (mtime, size)   → serve the cached records as-is (the common no-change poll);
#  - grew                 → verify the cached tail bytes still sit before the cached offset (a REWRITE that
#                           happens to be larger would otherwise serve a corrupt splice), then parse just
#                           the new bytes. Mismatch → full re-read;
#  - shrank / same-size-new-mtime → full re-read (a rewrite, not an append).
# A trailing line with no "\n" yet (a writer caught mid-append) is NOT consumed — the offset stays before
# it, so the next read picks the completed line up. Records are treated as IMMUTABLE by every consumer
# (FileAdapter builds fresh atom dicts; nothing writes into a record), matching the kernel's existing
# whole-parse cache contract. The cached list itself is never extended in place — a grown file stores a
# NEW list — so a concurrent reader holding the old list is never surprised mid-iteration.
_JSONL_CACHE = {}                 # path -> (mtime, size, offset, tail_bytes, records); dict order = LRU, hits reinsert
_JSONL_CACHE_MAX = 1024           # bounds MEMORY only (384 → 1024 on 2026-09-03: the per-session states,
                                  # captions and the postal/nudge logs became tenants — a few KB each — and
                                  # must never evict a live transcript's slot) — past the cap, evict the least-recently-USED entry, one per
                                  # insert, never clear(). The old clear-at-cap was sized to the session count, but
                                  # the working set is FILES, not sessions (every subagent writes its own transcript):
                                  # once more distinct files than slots passed through one push cycle, the clear
                                  # nuked the HOT entries too and every push re-parsed every active transcript from
                                  # byte zero — the exact stall this cache exists to prevent, back as a permanent
                                  # background burn (recurred 2026-08-15, kernel pinned at ~30-60% CPU; the survival
                                  # guarantee is pinned by tests/test_kernel_jsonl_cache.py). 256 -> 384 when the
                                  # states/postal logs became tenants too (2026-09-01): one states file per session
                                  # plus messages.jsonl now share the slots, and an evicted LEAF slot also demotes
                                  # the assembly cache's identity gate to a full parse.
_JSONL_TAIL_GUARD = 64            # bytes of pre-offset content re-verified before an incremental read
_JSONL_CACHE_LOCK = threading.Lock()   # the cache has cross-thread callers (the judge tiers' worker pools,
                                       # the pusher, the warm threads) and HITS mutate (LRU reinsert): the
                                       # lock covers only the cheap dict ops — the parse runs outside it —
                                       # and pops stay guarded so a lost race degrades to a re-parse, never
                                       # a raise


def _scan_jsonl_bytes(data, base_offset):
    """(records, consumed) for a bytes blob of jsonl starting at base_offset: parsed objects of every
    COMPLETE line, and the byte offset just past the last complete line (a trailing partial is left)."""
    end = data.rfind(b"\n")
    if end < 0:
        return [], base_offset
    records = []
    for line in data[:end + 1].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line.decode("utf-8", "replace")))
        except Exception:
            continue
    return records, base_offset + end + 1


def _read_jsonl_incremental(path):
    """The parsed records of `path` (a list, NOT a generator), served append-incrementally per the cache
    contract above. Falls back to a full read on any surprise; [] on any error, like _read_jsonl."""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError:
        with _JSONL_CACHE_LOCK:
            _JSONL_CACHE.pop(path, None)
        return []
    with _JSONL_CACHE_LOCK:
        hit = _JSONL_CACHE.get(path)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
            _JSONL_CACHE.pop(path, None)  # reinsert at the LRU tail: a served entry is a USED entry
            _JSONL_CACHE[path] = hit
            return hit[4]
    try:
        with open(path, "rb") as fh:
            if hit is not None and st.st_size > hit[1]:
                _, _, offset, tail, records = hit
                fh.seek(max(0, offset - len(tail)))
                if fh.read(len(tail)) == tail:            # the file really is our cached prefix + more
                    new, offset = _scan_jsonl_bytes(fh.read(), offset)
                    records = records + new               # a NEW list — never extend the served one in place
                else:
                    fh.seek(0)                            # prefix changed → a rewrite → full re-read
                    records, offset = _scan_jsonl_bytes(fh.read(), 0)
            else:
                records, offset = _scan_jsonl_bytes(fh.read(), 0)
            tail_from = max(0, offset - _JSONL_TAIL_GUARD)
            fh.seek(tail_from)
            tail = fh.read(offset - tail_from)
    except OSError:
        with _JSONL_CACHE_LOCK:
            _JSONL_CACHE.pop(path, None)
        return []
    with _JSONL_CACHE_LOCK:
        _JSONL_CACHE.pop(path, None)
        while len(_JSONL_CACHE) >= _JSONL_CACHE_MAX:
            _JSONL_CACHE.pop(next(iter(_JSONL_CACHE)))   # oldest-used first; hot entries survive any cold flood
        _JSONL_CACHE[path] = (st.st_mtime, st.st_size, offset, tail, records)
    return records


def fold_records(cache, path, init, step):
    """Fold a JSONL file's records into a carried state, APPEND-INCREMENTALLY (issue 903, 2026-09-03):
    the states/transcript readers re-read their whole file behind an (mtime,size) key that every append
    invalidates — O(file) per push for every working session. _read_jsonl_incremental already serves the
    parsed records of a growing file incrementally (the parse reads the same list, so this adds no I/O),
    and its contract — a grown file returns a NEW list whose prefix objects are the SAME — is the
    identity gate here: when the cached prefix is intact, only the appended records run through `step`;
    a rewrite (prefix identity lost, a shrink, a same-size-new-mtime) re-folds from record 0. `init()`
    makes a fresh state; `step(state, record)` returns the next state (mutate-and-return is fine: the
    cached state is deep-copied before folding onto it, so a state a caller was handed never changes
    under it). Returns the state; [] records (a missing or unreadable file) fold to init().

    Lives here (moved from the kernel, 2026-09-03) so the judge's readers can fold too — the
    background-task pairing below is shared by both."""
    key = str(path)
    recs = _read_jsonl_incremental(path)
    ent = _pinned_entry(key, recs)                        # the reader's entry for THIS read, pinned by identity: another
    if ent is _UNPINNED:                                  # thread (the judge pool, a handler) may advance the shared entry
        recs = _read_jsonl_incremental(path)              # past our records before we look at its tail, and a newer tail
        ent = _pinned_entry(key, recs)                    # folded onto an older prefix would skip the records between. A
        if ent is _UNPINNED:                              # lost pin re-reads once — the newer entry pins cleanly, so the
            ent = None                                    # answer is current, not a poll behind; lost twice, the fold
    hit = cache.get(key)                                  # answers without the tail, never with the wrong one
    if hit is not None:
        n0, last0, state0 = hit
        if n0 == len(recs) and (n0 == 0 or recs[-1] is last0):
            return _fold_eof_fragment(key, ent, state0, step)   # unchanged records; a newline-less tail may still sit past them
        if 0 < n0 < len(recs) and recs[n0 - 1] is last0:
            state, start = copy.deepcopy(state0), n0
        else:
            state, start = init(), 0
    else:
        state, start = init(), 0
    for r in recs[start:]:
        if isinstance(r, dict):
            state = step(state, r)
    if len(cache) > 256:                                  # bounded by the session count; never unbounded
        cache.clear()
    cache[key] = (len(recs), recs[-1] if recs else None, state)
    return _fold_eof_fragment(key, ent, state, step)


_UNPINNED = object()


def _pinned_entry(key, recs):
    """The shared reader's cache entry for `key` if it still describes the read that returned `recs` (its
    records list IS `recs`), None when the reader holds nothing for the path, _UNPINNED when another thread
    has advanced the entry past that read."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(key)
    if ent is not None and ent[4] is recs:
        return ent
    if ent is None and not recs:
        return None                                        # the reader holds nothing because there is nothing (or no file)
    return _UNPINNED                                       # advanced past our read — or evicted from under it (LRU)


_TRAILING_CACHE = {}          # path -> ((mtime, size, offset), record|None): the tail verdict per file version, so a
#                               torn tail that never completes (a CLI killed mid-write) costs one read, not one per poll.
#                               LRU at _TRAILING_CACHE_MAX (the least recently used entry goes, never the whole memo:
#                               a clear-at-cap above the cap re-reads every pending tail per poll — see _JSONL_CACHE_MAX)
_TRAILING_CACHE_MAX = 256
_TRAILING_LOCK = threading.Lock()   # the memo is shared by the pusher, the handler threads and the judge pools: the
#                                     LRU pop/reinsert/evict are several dict ops, not one (an unlocked evict raised
#                                     KeyError under two inserters at the cap, reproduced 2026-09-04)


def _trailing_record(path, ent):
    """The complete JSON record sitting past _read_jsonl_incremental's consumed offset WITHOUT its newline
    yet, or None. `ent` is the reader's cache entry for the read being folded (mtime, size, offset, tail,
    records; None when there is none). The incremental reader leaves such a line unconsumed by design (a
    writer caught mid-append must not enter the cache torn); the readers folded here used to walk the text
    to EOF and saw a final newline-less record — so the fold reads it provisionally (see _fold_eof_fragment).
    The verdict is memoized per (mtime, size, offset): an unchanged file answers from memory, whether its
    tail was a record or a torn fragment, with no I/O and no failed parse per poll (review find, 2026-09-04:
    a CLI killed mid-write left a torn line that every fold reader re-read and re-failed on every push)."""
    if ent is None or ent[1] <= ent[2]:                   # nothing past the consumed offset (the common case)
        return None
    ver = (ent[0], ent[1], ent[2])
    with _TRAILING_LOCK:
        hit = _TRAILING_CACHE.get(path)
        if hit is not None and hit[0] == ver:
            _TRAILING_CACHE.pop(path, None)               # a served verdict is a used one: to the LRU tail
            _TRAILING_CACHE[path] = hit
            return hit[1]
    o = None
    try:
        with open(path, "rb") as fh:
            fh.seek(ent[2])
            frag = fh.read(ent[1] - ent[2]).strip()
        if frag and b"\n" not in frag:
            o = json.loads(frag.decode("utf-8", "replace"))
    except (OSError, ValueError):
        o = None
    o = o if isinstance(o, dict) else None
    with _TRAILING_LOCK:                                  # the read above ran unlocked; two readers of one version
        _TRAILING_CACHE.pop(path, None)                   # agree on the verdict, so the last writer wins harmlessly
        while len(_TRAILING_CACHE) >= _TRAILING_CACHE_MAX:
            del _TRAILING_CACHE[next(iter(_TRAILING_CACHE))]   # the least recently used goes first
        _TRAILING_CACHE[path] = (ver, o)
    return o


def _fold_eof_fragment(key, ent, state, step):
    """fold_records' answer with a newline-less final record folded in PROVISIONALLY: applied to a copy,
    never to the cached state, so the record is folded for good exactly once — when its newline lands and
    the incremental reader serves it as a record. Keeps the old whole-file readers' answer (they saw that
    final line) without giving up the cache's torn-write safety. `ent` is the reader's entry for the very
    read being folded (fold_records pins it by the identity of its records list), never a fresh lookup."""
    frag = _trailing_record(key, ent)
    if frag is None:
        return state
    return step(copy.deepcopy(state), frag)


def _is_tool_result(r):
    """True when a user-typed record is a machine-made tool_result (its first content block),
    not a person's prompt — the discriminator the eclipse stand-down needs."""
    c = _content((r or {}).get("message"))
    return bool(c) and isinstance(c[0], dict) and c[0].get("type") == "tool_result"


def _content(message):
    """The content[] of a message, normalized: a bare string becomes one text block,
    so every atom carries a list of blocks (the 'one atom, many blocks' shape)."""
    if not isinstance(message, dict):
        return []
    c = message.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}] if c.strip() else []
    return c if isinstance(c, list) else []


def _text_of(blocks):
    """Joined text of the text blocks in a content list (thinking/tool_use/tool_result skipped)."""
    return " ".join(b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text").strip()


def _block_types(blocks):
    return [b.get("type") for b in blocks if isinstance(b, dict)]


def _is_real_prompt(blocks):
    """A user line is a genuine PROMPT (vs a tool_result-only harness line) when it
    carries any text. A tool_result-only line has no text block."""
    return bool(_text_of(blocks))


def _has_tool_result(blocks):
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)


def _norm_message(message):
    """The Anthropic message object kept verbatim where it exists (role, content blocks,
    model, usage, stop_reason). Content is normalized to a block list."""
    if not isinstance(message, dict):
        return None
    out = {"role": message.get("role"), "content": _content(message)}
    for k in ("model", "stop_reason", "usage"):
        if message.get(k) is not None:
            out[k] = message[k]
    return out


def postal_pairs(text):
    """[(mid, kind), ...] in delivery order; kind is "" when the sender declared none (CLI mail).
    Position is the only thing that pairs them — the markers carry no cross-reference."""
    pairs = []
    for typ, val in _POSTAL_ANY_RE.findall(text or ""):
        if typ == "id":
            pairs.append([val, ""])
        elif pairs and not pairs[-1][1] and val in _POSTAL_KINDS:
            pairs[-1][1] = val                   # binds to the id it follows, never a later one
    return [(a, b) for a, b in pairs]


# ───────────────────────── authorship (the one real addition over the stream) ─────────────────────────
# A user atom's author is the ONE field the stream lacks: it cannot tell a peer romp
# message from a human prompt (both are `user` messages). Everything else the old
# typed/queued/absorbed/decision/postal enum encoded is derived from position
# (opener vs mid-turn) and content, not stored here.
def author_of(blocks, prompt_source, postal_index, sdk_human=False):
    """human | romp | sdk | system | {"peer": <rompUuid|None>, "mid": <id>, "kind": <kind|"">} | None.

    Order matters: the postal marker wins over promptSource (a delivered message can
    arrive with any promptSource). A tool_result-only user atom has no author.

    A peer author carries the marker it resolved, not just the sender: one delivery can hold
    several messages, so every later reader (the judge's _seg_peer / _seg_peer_kind) must be
    told WHICH one this author came from rather than re-scanning and picking a different one.

    sdk_human: this session is SDK-backed, so its HUMAN input arrives over the programmatic
    stream-json channel as promptSource "sdk" (the human typed it in the composer). romp's own
    injections still carry the romp-injected marker and peers the postal marker (both handled
    above), so an UNMARKED "sdk" prompt here is the human → render it as the blue human bubble.
    Off (the default) elsewhere, where "sdk" means a genuine programmatic/autonomous injection."""
    text = _text_of(blocks)
    if text:
        if SYSTEM_WRAPPER_RE.match(text):         # a harness <task-notification> / <system-reminder>, not the user
            return "system"                       # → author 'system' so _is_opener folds it in, never a goal
        if TEAMMATE_MSG_RE.match(text):           # Claude Code's native agent-to-agent delivery, not the user typing
            return "teammate"                     # → its own collapsed chat card; a non-opener (like 'system'), so
            #   high-frequency coordination pings never pin a junk goal. Checked before the postal marker: the
            #   OUTER native wrapper wins even if a forwarded body happened to carry a romp-msg-id.
        pairs = postal_pairs(text)
        if pairs:
            # LAST first: the delivery appends its markers AFTER the body, so when a body itself
            # carries one — a peer forwarding mail it received, an agent quoting its own — the real
            # sender's marker is the trailing one. Taking the first let the quoted id name the author.
            peer, mid, kind = None, pairs[-1][0], pairs[-1][1]
            for m, k in reversed(pairs):
                if postal_index.get(m):
                    peer, mid, kind = postal_index[m], m, k
                    break
            # The chosen marker travels WITH the author, so the judge reads the same one rather than
            # re-scanning and landing on a different message.
            return {"peer": peer, "mid": mid, "kind": kind}
        if ROMP_INJECT_RE.search(text):           # romp pasted this into the pane (a feed nudge) → system, not human
            return "romp"
    if prompt_source == "sdk":
        return "human" if sdk_human else "sdk"
    if prompt_source == "system":
        return "system"
    if prompt_source in ("typed", "queued"):
        return "human"
    # promptSource absent: a genuine prompt with no SDK/system/postal signal is presumed
    # human (a typed prompt the harness recorded without the field — ~10% on disk). A
    # tool_result-only line (no text) gets no author.
    if _is_real_prompt(blocks):
        return "human"
    return None


def _author_final(author, text):
    """True when a postal author can never change again: the TRAILING marker resolved. author_of
    scans markers tail-first for the first index hit, so a resolution landing later than the
    CHOSEN marker re-decides the author — a record whose quoted/forwarded earlier marker resolved
    while its trailing real one hadn't yet gets a plausible-but-PROVISIONAL author, not just a
    peer-None one (the postal log is append-only and first-wins per id, so resolutions only
    arrive, never change). The assembly heal re-emits any non-final author each visit until the
    tail settles; non-postal authors are trivially final."""
    if not isinstance(author, dict):
        return True
    pairs = postal_pairs(text)
    return bool(author.get("peer") is not None and pairs and author.get("mid") == pairs[-1][0])


def parse_teammate_message(text):
    """Split a native Claude Code teammate-message delivery (see TEAMMATE_MSG_RE) into per-sender blocks
    for the chat to render its own way: a list of {"id", "summary", "body"}. `color` is DELIBERATELY
    dropped — these get a neutral treatment, NOT the per-peer color chrome of a romp postal card, so the
    two are tellable apart (the user 2026-07-05). The fixed "permission laundering" boilerplate and the
    <prompt> wrapper fall away naturally (only the <teammate-message> block contents are kept). Returns []
    when there are no blocks (a delivery with no parseable block → caller shows the raw text)."""
    out = []
    for attrs, body in TEAMMATE_BLOCK_RE.findall(text or ""):
        a = dict(re.findall(r'(\w+)="([^"]*)"', attrs))
        out.append({"id": (a.get("teammate_id") or "").strip(),
                    "summary": (a.get("summary") or "").strip(),
                    "body": body.strip()})
    return out


# ═════════════════════════ FILE ADAPTER: graph recovery, quarantined ═════════════════════════
def _emit_state():
    """The emit layer's cross-record carry — everything the per-record emit reads that EARLIER
    records established. A full parse starts one empty and folds every kept record through it;
    the assembly cache (_assemble) persists it per session and folds only appended records.
    Field-for-field these are the old atoms() locals, hoisted so both paths share one
    implementation and cannot drift."""
    return {"replay": set(),           # uuids classified post-compaction replays (never atoms)
            "seen_exact": set(),       # (second, text) of every kept user text — verbatim re-writes
            "seen_text": set(),        # texts ever seen — the restore-burst dedup's memory
            "compacted": False, "restoring": False, "last_boundary": None,
            "summaries": {},           # boundary uuid -> its compaction summary text
            "skill_ids": set(),        # Skill tool_use ids among kept assistants
            "cmd_names": {},           # promptId -> {command names} (slash-invocation twins)
            "absorbed_keys": set(),    # _absorbed's (ts, collapsed-text) dedup memory
            "postal_miss_rec": set(),  # kept records whose postal marker missed the index
            "postal_miss_att": set(),  # absorbed (ts, collapsed-text) keys likewise
            "max_ppt": 0.0}            # chronological watermark of folded pre-pass-relevant records


def _chrono(ad, uuids):
    """`uuids` in the emit layer's canonical order — ascending (timestamp, read order), exactly
    the key the replay pre-pass has always walked in."""
    return sorted(uuids, key=lambda x: (ad.ts_of.get(x) or 0, ad.seq_of.get(x, 0)))


class FileAdapter:
    """Rebuilds the active linear message sequence from the append-only on-disk graph.

    Reads every candidate transcript once into a uuid index, then does a DIRECTED
    backward walk from the leaf via parentUuid (crossing files on resume), so the
    active path is exactly the leaf->root ancestors. Rewound branches are non-
    ancestors and drop out for free; `/clear` leaves no parent link so the walk
    stops there and pre-clear history drops out naturally."""

    def __init__(self, candidate_files, leaf_path, leaf_override=None, resume_links=None):
        self.resume_links = dict(resume_links or {})   # {to_fsid: from_fsid} — recorded resume forks (states/ rows)
        self.by_uuid = {}        # uuid -> record
        self.fsid_of = {}        # uuid -> transcript file stem (provenance / click-to-open)
        self.seq_of = {}         # uuid -> global read order (tie-break for equal timestamps)
        self.parent_of = {}      # uuid -> parentUuid (or logicalParentUuid, for the compaction stitch)
        self.qatts = []          # queued_command attachment records IN FILE ORDER — the CLI's own splice
                                 #   witnesses: {uuid, ts (the ENQUEUE timestamp the record carries),
                                 #   text (full prompt, markers intact), seq}
        self.leaf_uuid = None
        self._seq = 0            # global read-order counter — continued by assembly folds
        self.ts_of = {}          # uuid -> repaired epoch seconds — THE timestamp every consumer
        #                          reads (_chrono, the emit, the boundary splice). A record whose
        #                          stamp does not parse borrows the last good stamp seen in file
        #                          order (its real neighbor): the atom stays VISIBLE at a truthful
        #                          adjacent time instead of t=None TypeError-ing segment_turns'
        #                          sort and taking the whole session view down (T210). Fold-safe
        #                          with no carry: _ingest is the one shared path and re-ingestion
        #                          replays the same order, so fold and full repair identically.
        self._last_ts = 0        # the borrow source — last parseable stamp ingested
        # Graph-level facts the assembly fast-path gates read, collected at INGEST so they cover
        # the whole graph (kept or not): every user record's promptId (a delta record repeating
        # one forces a full parse — command twins/episodes key on it), every assistant Skill
        # tool_use id (a delta invocation an old payload record already references forces full),
        # and every parent target that dangled at ingest time (a delta record RESURRECTING one
        # would rebind repaired stitches, so it forces full).
        self.prompt_ids = set()
        self.boundary_pids = set()
        self.skill_use_ids = set()
        self.src_tool_links = set()
        self.dangling = set()
        self._src = {}           # path -> the exact records list ingested (the jsonl cache's list
        #                          object; identity anchors the fold's pure-append gate)
        leaf_stem = Path(leaf_path).stem
        # read the leaf last so its trailing uuid wins as the walk anchor even if a
        # sibling file happens to sort after it
        files = [f for f in candidate_files if Path(f).stem != leaf_stem] + [Path(leaf_path)]
        for fp in files:
            fsid = Path(fp).stem
            recs = _read_jsonl_incremental(fp)
            self._src[str(fp)] = recs
            self._ingest(recs, fsid, fsid == leaf_stem)
        # PRISTINE graph state — parent links / leaf exactly as the records say, BEFORE the three
        # repair passes mutate them. The assembly fold re-derives the passes from this each time
        # (extend pristine with the delta, re-run the passes on a working copy), so folded graph
        # state is literally the fresh-__init__ computation — never a latched earlier repair.
        self._pristine_parent = dict(self.parent_of)
        self._pristine_leaf = self.leaf_uuid
        # A PENDING bare rollback (chat delete): the kernel passes the cut point as leaf_override so
        # the walk starts there and the not-yet-abandoned tail drops exactly as it will once the CLI's
        # --resume-session-at branch takes. Applied only when the uuid is really in this graph — a
        # stale override (wrong file, raced clear) falls back to the true file leaf, never an empty parse.
        if leaf_override and leaf_override in self.by_uuid:
            self.leaf_uuid = leaf_override
        self._run_graph_passes()

    def _ingest(self, records, fsid, is_leaf):
        """Index `records` into the graph maps — the ONE ingestion path the fresh build and the
        assembly fold share, so a folded index is field-identical to a freshly built one."""
        seq = self._seq
        for r in records:
            seq += 1
            t = r.get("type")
            u = r.get("uuid")
            if u:
                self.by_uuid[u] = r
                self.fsid_of[u] = fsid
                self.seq_of[u] = seq
                ts = parse_z(r.get("timestamp"))
                if ts is None:
                    ts = self._last_ts
                    if u not in _TS_REPAIRED_SEEN:   # count DISTINCT corrupt records — a full
                        #                              parse re-ingests every line, and a stable
                        #                              corrupt one must not inflate per parse
                        if len(_TS_REPAIRED_SEEN) >= 4096:
                            _TS_REPAIRED_SEEN.clear()
                        _TS_REPAIRED_SEEN.add(u)
                        _ASM_STATS["ts-repair"] = _ASM_STATS.get("ts-repair", 0) + 1
                    if fsid not in _TS_REPAIR_NOTED:
                        if len(_TS_REPAIR_NOTED) >= 64:
                            _TS_REPAIR_NOTED.clear()   # re-arm — capped silence must not become
                            #                            permanent silence for every NEW file
                        _TS_REPAIR_NOTED.add(fsid)
                        print("romp event-model: unparseable timestamp on record %s in %s.jsonl — "
                              "shown at the previous record's time (ts-repair in parse stats)"
                              % (u, fsid), file=sys.stderr)
                else:
                    self._last_ts = ts
                self.ts_of[u] = ts
                # parentUuid normally; compact_boundary carries parentUuid:null +
                # logicalParentUuid:<pre-compaction leaf> — follow that so the active
                # path survives compaction instead of orphaning every pre-compaction turn.
                # A SELF-referential link (corrupt record) becomes a root: kept as-is it
                # 1-cycles every walk that starts or passes there.
                p = r.get("parentUuid") or r.get("logicalParentUuid")
                self.parent_of[u] = None if p == u else p
                if p and p != u and p not in self.by_uuid:
                    self.dangling.add(p)   # may resolve later in this read — swept below
                if is_leaf:
                    self.leaf_uuid = u
                if t == "user":
                    if r.get("promptId"):
                        self.prompt_ids.add(r["promptId"])
                        if r.get("isCompactSummary") is True:
                            # a compaction summary's promptId is the designed link the boundary
                            # ADOPTION keys on — episode records wearing it can re-seat a card
                            self.boundary_pids.add(r["promptId"])
                    if r.get("sourceToolUseID"):
                        self.src_tool_links.add(r["sourceToolUseID"])
                elif t == "assistant":
                    for b in _content(r.get("message")) or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and \
                                b.get("name") == "Skill" and b.get("id"):
                            self.skill_use_ids.add(b["id"])
            if t == "attachment":
                a = r.get("attachment") or {}
                if a.get("type") == "queued_command" and a.get("prompt"):
                    # the prompt can be a plain string OR a content-block LIST (the SDK injection
                    # path) — extract the TEXT either way; str() of a list keyed the Python repr,
                    # which no enqueue content ever matches (the user 2026-07-06)
                    ptext = a["prompt"] if isinstance(a["prompt"], str) else _text_of(a["prompt"])
                    # the repaired stamp when the record is uuid-bearing (real CLI splices are):
                    # raw-None dropped the user's typed prompt from the chat (T210 review)
                    self.qatts.append({"uuid": u, "ts": self.ts_of.get(u, parse_z(r.get("timestamp"))),
                                       "text": ptext, "seq": seq})
        self._seq = seq
        self.dangling -= self.by_uuid.keys()   # a target that landed later in the read is not dangling

    def _run_graph_passes(self):
        self._adopted = {}       # boundary uuid -> its episode's splice record (the /compact stdout),
        #                          filled by _adopt_detached_compactions. Downstream consumers key on
        #                          membership: an ADOPTED boundary is a LIVE manual compact, so the
        #                          replay dedup must not arm on it (nothing after it is a replayed
        #                          tail) and its atom must sort AFTER the episode's stdout.
        self._repair_compaction_stitches()
        self._stitch_resume_forks()
        self._adopt_detached_compactions()

    def _stitch_resume_forks(self):
        """Some CLI resumes of a machine-cut turn FORK the transcript with a FRESH head (parentUuid
        null, no cross-file back-link) instead of continuing the chain. On disk that fork is
        byte-indistinguishable from a /clear, so kept_uuids dropped the ENTIRE pre-cut conversation
        and the judges never saw the cut turn's work again — an hourly watch's finding lost its card
        to two mid-turn restarts (the user 2026-08-14). The kernel records the fork the moment the
        resumed CLI's init reports the new fsid (states/ resumeFork rows -> resume_links), so the
        lineage is an exact recorded event, never a guess: re-point the fork head's parent at the
        resumed file's last uuid-bearing record, restoring ONE chain the walk can cross (the
        compaction-stitch precedent above). Only a genuinely fresh head is stitched — an intact
        back-link is never overridden — and a /clear records no lineage, so its history keeps
        dropping by design."""
        if not self.resume_links:
            return
        first_of, last_of = {}, {}
        for u in self.by_uuid:               # insertion order = file read order
            fs = self.fsid_of.get(u)
            if fs not in first_of:
                first_of[fs] = u
            last_of[fs] = u
        for to, frm in self.resume_links.items():
            head, tail = first_of.get(to), last_of.get(frm)
            if head and tail and head != tail and self.parent_of.get(head) is None:
                self.parent_of[head] = tail

    def _repair_compaction_stitches(self):
        """Claude Code sometimes writes a compact_boundary whose logicalParentUuid points
        at a message that exists in compactMetadata.allUuids but was NEVER written as its
        own transcript line (3/69 compactions in the live corpus). Followed blindly, that
        dangling stitch orphans ALL pre-compaction history. The real in-file pre-compaction
        leaf is in compactMetadata.preservedSegment (tail/anchor/head), so when the stitch
        target is missing, re-point parent_of there — reconnecting the pre-compaction tree.
        (Verified: rescues 100% of the corpus's broken stitches.)"""
        for u, r in self.by_uuid.items():
            if r.get("type") != "system" or r.get("subtype") != "compact_boundary":
                continue
            target = self.parent_of.get(u)
            if target is None or target in self.by_uuid:
                continue                          # no stitch, or stitch is intact
            seg = (r.get("compactMetadata") or {}).get("preservedSegment") or {}
            for k in ("tailUuid", "anchorUuid", "headUuid"):   # tail = the pre-compaction leaf
                cand = seg.get(k)
                if cand and cand in self.by_uuid:
                    self.parent_of[u] = cand
                    break

    def _adopt_detached_compactions(self):
        """A LIVE manual /compact writes its compact_boundary + summary as a DETACHED side
        branch: the boundary carries parentUuid:null + logicalParentUuid:<pre-compact leaf>,
        the summary record is its only child, and NOTHING chains through them — the visible
        conversation parents through the /compact invocation records (caveat/wrapper/stdout)
        instead. The backward walk never visits the side branch, so the compaction atom — the
        chat's "Context compacted" card — was silently never emitted for a live manual
        compact, while auto-compactions (whose continuation chains THROUGH boundary+summary)
        kept theirs (the user 2026-08-19).

        Adopt the orphaned pair by splicing it in AFTER its own invocation episode's stdout
        record: …anchor ← caveat ← wrapper ← stdout ← boundary ← summary ← former child. Two
        deliberate choices there, both corrections of the first cut (2026-08-19 review):

        * The boundary's own EPISODE — not its bare anchor — is both the gate and the splice
          point. The designed link is the summary record's promptId, which the CLI stamps
          with the invoking /compact's promptId (13/13 manual boundaries in the live corpus;
          file-order adjacency is the fallback for summaries carrying NO promptId at all, and
          is genuinely a fallback: one corpus episode is appended BEFORE its boundary). Gating
          on the bare anchor resurrected compactions the user had REWOUND AWAY (next prompt
          re-parents at the pre-compact leaf: wrappers off-path, anchor still on it), and two
          compactions sharing one anchor threaded through each other. Episode off the active
          path → its /compact was undone → the boundary stays hidden with it; no episode →
          nothing witnesses the invocation on the visible history → stays hidden — and a
          promptId that names NOTHING on record stays hidden the same way, never handed to
          adjacency: that is the crash-truncated write this module models mid-write, and
          adjacency in its place stole a later same-anchor /compact's episode.
        * Splicing BEFORE the stdout pulled that stdout atom out of its /compact command
          segment into the boundary's fresh triggerless turn, minting a brand-new
          judge-visible WORK unit ("Compacted (ctrl+o…)") for every manual compact in every
          existing session. After the stdout, the command segment keeps its output and the
          boundary's turn holds nothing — no assistant work, no unit.

        Keyed on the SHAPE (boundary off the active path, its episode on it), never on
        trigger=manual: an attached boundary of either kind (auto, or the resume re-splice a
        manual pair arrives back in) no-ops here. When the stdout IS the leaf (the user
        compacted and has not typed since), the pair becomes the chain's new tail — the card
        must not wait for the next prompt. Runs after the stitch repair and the resume
        stitching (the active path must already cross files). parent_of/leaf_uuid only —
        records are never mutated, so the shared _read_jsonl_incremental cache lists stay
        pristine. Adopted boundaries are recorded in self._adopted for the two downstream
        consumers that must NOT treat them as attached: the replay dedup (a live manual
        compact replays no tail — arming it ate the user's next genuine prompt whenever its
        text repeated an earlier one) and the emit-order override (the boundary record is
        appended BEFORE the stdout, so raw (t, seq) order would put the card inside the
        command exchange it belongs after).

        Placement note (re-derived 2026-08-19 against the golden scenario AND every
        boundary-bearing live-corpus transcript, plan_units pre vs post — the first cut
        asserted no-bump from intention and was wrong, so only measurements are recorded
        here; tests/test_placements_canary.py pins the class): the added boundary atom's
        turn holds no user ask and no assistant work of its own, so on the golden scenario
        and 11 of 12 corpus transcripts the unit sets are byte-identical pre/post — no
        recorded placement key shifts, no unit appears or disappears. The residue: when
        assistant work FOLLOWS the manual compact with no new opener (a queued prompt
        spliced through it — 1 of 12), that continuation moves from the /compact command
        segment (where the old parse misfiled it as a human-triggered "/compact worked"
        unit) into the boundary's own turn — the same non-human continuation unit an
        attached auto-compact has always produced. One re-attributed unit per such
        transcript. DECIDED no-bump (2026-08-19, from traced evidence): the orphaned row
        is inert — every placements consumer queries only units the CURRENT parse yields,
        and _migrate_placements never removes old rows, so orphans are the normal
        post-bump state of every store since v2; the fuzzy _placed_key path reads them
        only in the dedup direction (prevents replay, never causes one). The one NEW unit
        costs a single planner call. A bump would be strictly worse: it seals every
        store's currently-ready unplaced units — measured ~29 across the live corpus,
        including genuinely pending work — the silent drop of a real ask this repo calls
        its one fatal error."""
        active = self.active_path()
        if not any(r.get("type") == "system" and r.get("subtype") == "compact_boundary"
                   and u not in active for u, r in self.by_uuid.items()):
            return                        # nothing detached — skip the episode scan entirely
        # the active CHILD of each on-path uuid — the chain has at most one per node
        child_of, u = {}, self.leaf_uuid
        while u is not None:
            p = self.parent_of.get(u)
            if p is None or p in child_of:
                break
            child_of[p] = u
            u = p
        # /compact invocation EPISODES, keyed by promptId: head = first record in file order
        # (the caveat/raw twin, parented on the pre-compact leaf); splice = the FIRST stdout
        # record — a restore burst can replay the episode verbatim with the promptId preserved,
        # and a later copy must never re-seat the card off the original splice (the copy
        # seq-nearest the boundary+summary pair; the replayed copy's atoms fall to the dedup) —
        # else the last record seen: a MID-WRITE episode, one parse wide, never hidden, each
        # phase self-correcting at the next record. Boundary- or summary-as-leaf the pair is ON
        # the active path (attached by shape, emits natively; the dedup arms there on an empty
        # window — the file ends at the pair); caveat- or wrapper-as-leaf it adopts AT the
        # episode's last landed record — adopted, so unarmed — and re-seats once the stdout lands.
        episodes = {}
        for eu, er in self.by_uuid.items():          # insertion order = file read order
            pid = er.get("promptId")
            if not pid or er.get("type") != "user" or er.get("isCompactSummary"):
                continue
            blocks = _content(er.get("message"))
            btext = (_text_of(blocks) if blocks else "") or ""
            g = episodes.setdefault(pid, {"head_parent": self.parent_of.get(eu),
                                          "head_seq": self.seq_of.get(eu, 0),
                                          "splice": eu, "stdout": None, "compact": False})
            m = COMMAND_NAME_ANY_RE.search(btext)
            if m:
                name = m.group(1).strip()
                if (name if name.startswith("/") else "/" + name) == "/compact":
                    g["compact"] = True              # the episode invokes /compact, not some other command
            if LOCAL_STDOUT_RE.match(btext) and g["stdout"] is None:
                g["stdout"] = eu                     # first stdout wins — see the splice rule above
            g["splice"] = g["stdout"] or eu
        boundaries = sorted((self.seq_of.get(u, 0), u) for u, r in self.by_uuid.items()
                            if r.get("type") == "system" and r.get("subtype") == "compact_boundary")
        for _, b in boundaries:
            if b in active:
                continue                  # attached (auto / resume re-splice) — never double-emit
            summary = next((s for s, sr in self.by_uuid.items()
                            if sr.get("isCompactSummary") is True and self.parent_of.get(s) == b),
                           None)
            pid = (self.by_uuid.get(summary) or {}).get("promptId") if summary else None
            if pid:
                # the designed link, and the ONLY one honored when present: a promptId that
                # names no on-record /compact episode (a crash-truncated compaction —
                # boundary+summary landed, the episode records never did) keeps its boundary
                # HIDDEN. Degrading to adjacency stole a later same-anchor /compact's episode:
                # the stale summary rendered at the live splice, and the already-claimed guard
                # below then hid the real compact's card (2026-08-19 second review).
                ep = episodes.get(pid)
                if ep is not None and not ep["compact"]:
                    ep = None             # the summary's promptId names some OTHER exchange — not a witness
            else:
                # fallback ONLY for summaries carrying no promptId at all (older writes,
                # synthetic shapes): b's own episode is the nearest /compact invoked from b's
                # anchor and appended after b — the CLI writes boundary+summary first, then
                # the episode records
                anchor = self.parent_of.get(b)
                cands = [g for g in episodes.values()
                         if g["compact"] and g["head_parent"] == anchor
                         and g["head_seq"] > self.seq_of.get(b, 0)]
                ep = min(cands, key=lambda g: g["head_seq"]) if cands else None
            if ep is None:
                continue                  # no on-record /compact invocation owns this boundary — stays hidden
            sp = ep["splice"]
            if sp not in active or sp == b or sp in self._adopted.values():
                continue                  # the episode was rewound/cleared away (or already claimed) — hidden
            c = child_of.get(sp)
            tail = summary if summary is not None else b
            self.parent_of[b] = sp        # …stdout <- b (<- summary) <- former child
            if c is not None:
                self.parent_of[c] = tail
            else:
                self.leaf_uuid = tail     # the stdout was the leaf: the adopted pair is the new tail
            active.add(b)
            child_of[sp] = b
            if summary is not None:
                active.add(summary)
                child_of[b] = summary
                if c is not None:
                    child_of[summary] = c
            elif c is not None:
                child_of[b] = c
            self._adopted[b] = sp

    def active_path(self):
        """The set of uuids on the leaf->root chain (directed walk, O(chain length))."""
        active, u, guard = set(), self.leaf_uuid, 0
        while u is not None and u not in active and guard < 500000:
            active.add(u)
            u = self.parent_of.get(u)
            guard += 1
        return active

    def landed_text_uuids(self):
        """Uuids whose record carries NON-EMPTY assistant text — on ANY branch, kept or dropped.
        The orphan salvage dedups against this rather than the kept path alone: a reply that
        LANDED and was then abandoned by a chat-delete rollback (its branch forks away once the
        CLI relaunches with --resume-session-at) is not a loss, and its orphanReply marker must
        never resurrect it. The parse_session leaf_override filter covers only the ARMED window;
        after the rollback is CONSUMED the abandoned reply left the kept path and its marker
        re-fired forever — a durable ghost bubble, visible to the judges too (the user
        2026-08-03). Text-bearing only, so a TEXTLESS twin record (the fable+AskUserQuestion
        empty-thinking case) still does not eat its marker's salvage."""
        out = set()
        for u, r in self.by_uuid.items():
            if r.get("type") == "assistant" and _text_of(_content(r.get("message"))).strip():
                out.add(u)
        return out

    def chain_verdicts(self, active=None):
        """THE chain-membership identity, one verdict per uuid in the graph — the single
        implementation every consumer must share (the goal-store rewind cleanup grew four
        hand-rolled partial twins of this walk before it was exported, and they disagreed
        on exactly the cases that matter — resume forks, pending cuts, broken chains):
          "active" — on the leaf->root spine (what the chat shows).
          "rewind" — the chain rejoins the active spine: this line was REWOUND AWAY. The
                     only verdict that ever justifies dropping/sweeping content.
          "eclipsed" — the chain rejoins the active spine, but the spine leaves the fork
                     through MACHINE bookkeeping (an api_error spur): nobody rewound
                     anything — the CLI flushed its buffered retry-storm records chained
                     off the turn's opener and hung the next prompt on that spur, so the
                     turn's real output fell off the spine with no user gesture. KEPT:
                     it is the ONLY copy of a reply the user watched render (T209, the
                     user 2026-09-01 — a five-minute turn's answer vanished behind a
                     "Recovered after retries" note when the next send flushed the spur;
                     no orphanReply marker exists because the disk DID keep the text).
                     Within the fork's component the verdict is NARROWED to the one
                     machine-orphaned reply chain — sibling stub pairs, error bursts and
                     user-headed branches drop exactly as they do at any other fork
                     (see _select_eclipsed_chains).
          "clear"  — the chain reaches a clean null root the leaf does not share: `/clear`
                     jurisdiction (the episode machinery settles those) — never swept as
                     a rewind.
          "broken" — parentUuid points at a uuid in NO transcript, or a cycle: unprovable,
                     KEPT (silently dropping a real ask is this repo's one fatal error).
        `active` defaults to active_path(); pass it when already computed."""
        if active is None:
            active = self.active_path()
        # spine child map (parent -> its child ON the spine), for the fork-kind probe below
        spine_child, _u, _guard = {}, self.leaf_uuid, 0
        while _u is not None and _guard < 500000:
            _p = self.parent_of.get(_u)
            if _p is None or _p in spine_child:
                break                              # root, or a cycle already crossed
            spine_child[_p] = _u
            _u = _p; _guard += 1
        _fork_memo, _fork_terminal = {}, {}
        def fork_kind(f):
            """rewind vs eclipsed for a chain rejoining the spine at f. A genuine rollback
            re-parents the user's NEXT PROMPT directly at the cut (the spine leaves f with a
            conversational record); the SDK's buffered-flush geometry puts >=1 system
            api_error record STRICTLY BETWEEN f and the next conversational record — an
            exact machine event, so this can never re-show content a person deleted. An
            assistant record ending the probe means the spine re-replied: the fork is a
            superseded attempt (the orphan salvage's jurisdiction), not an eclipse.
            An eclipse also records WHICH terminal decided it in _fork_terminal — "user"
            (the flush completed: the next prompt sits past the spur) or "exhausted" (the
            spur is the transcript's tail, or a corrupt cycle) — because the two terminals
            keep differently (see _select_eclipsed_chains)."""
            if f in _fork_memo:
                return _fork_memo[f]
            res, u, saw_err, _seen = "rewind", spine_child.get(f), False, set()
            while u is not None and u not in _seen:
                _seen.add(u)
                r = self.by_uuid.get(u) or {}
                t = r.get("type")
                if t == "assistant":
                    break
                if t == "user":
                    if saw_err:
                        res = "eclipsed"
                        _fork_terminal[f] = "user"
                    break
                if t == "system" and r.get("subtype") == "api_error":
                    saw_err = True
                u = spine_child.get(u)
            else:
                # The spine RAN OUT (or cycled — _seen is the guard classify's own cycle branch
                # has and this probe first shipped without: a multi-node cycle of system records
                # hung every parse, found by adversarial review) before any conversational
                # record: the spur is the transcript's current TAIL — a parse racing the CLI's
                # multi-line flush, or a session that died in the storm. An api_error on the
                # spine out of the fork is a machine artifact whatever follows, and defaulting
                # this terminal to the one SWEEPABLE verdict re-opened the T209 loss for
                # exactly those windows (one-way goal archives in the race; a permanent eat on
                # the mid-storm death). A pending-cut fork is untouched: the cut leaf has no
                # spine child, so the loop never runs and saw_err stays False.
                if saw_err:
                    res = "eclipsed"
                    _fork_terminal[f] = "exhausted"
            _fork_memo[f] = res
            return res
        verdict = {}
        def classify(start):
            path, u = [], start
            while True:
                if u in active:
                    res = fork_kind(u); break      # chain rejoins the active spine -> rewound fork,
                    #                                unless a machine spur abandoned it (eclipsed)
                if u in verdict:
                    res = verdict[u]; break
                if u not in self.by_uuid:
                    res = "broken"; break           # dangling target uuid (corruption)
                if u in path:
                    res = "broken"; break           # cycle -> unprovable, keep
                path.append(u)
                p = self.parent_of.get(u)
                if p is None:
                    res = "clear"; break            # clean null root, not the leaf's -> pre-clear
                u = p
            for x in path:
                verdict[x] = res
            return res
        out = {}
        for u in self.by_uuid:
            out[u] = "active" if u in active else classify(u)
        self._select_eclipsed_chains(out, active, _fork_terminal)
        return out

    def _select_eclipsed_chains(self, verdict, active, fork_terminal):
        """WHICH uuids an eclipsed fork keeps (2026-09-01). fork_kind above decides WHETHER a
        fork was machine-abandoned — >=1 system api_error record strictly between the fork and
        the spine's next conversational record (T209) — and classify hands every chain
        rejoining there the "eclipsed" verdict. But an eclipsed fork can hold SIBLING chains
        that are not the bypassed reply: a parallel tool-call stub pair (textless twins the
        walk drops everywhere else), a second flushed api_error burst, an older persisted
        attempt, or a branch headed by a USER record. Keeping the whole component re-renders
        the stub twins beside their kept originals, doubles a two-attempt turn, and — the one
        direction the eclipsed verdict promised never to take — can re-show a prompt the user
        deleted: a rollback typed DURING a storm produces exactly this geometry, because the
        CLI flushes its buffered api_error records at the next enqueue, so the user's
        replacement prompt lands past the error spur and their abandoned branch rejoins at an
        api_error fork. So the eclipse keeps ONE BRANCH — the sub-tree under one fork-side
        head — selected by what it carries, never by the CLI's byte order of writes (nothing
        contracts flush order; an order-keyed pick let a late-written stub pair steal the keep,
        and a late-written burst veto it). A branch qualifies when its fork-side HEAD is an
        assistant record (a user gesture's head is always the user's own record) and it
        carries reply text anywhere in the sub-tree; the winner is the branch with the
        latest-written reply text, tiebroken by head seq. The text witness is landed_text_uuids
        MINUS isApiErrorMessage records (error-as-text failure echoes, which atoms() likewise
        refuses to treat as replies) — so junk carries no weight in the key, and no textless
        tail can move the pick whatever its file position. The unit is deliberately the WHOLE
        branch, never a leaf-chain within it: leaf-chains of one branch share records, and
        every per-chain read of shared state proved breakable (a full-chain gate laundered a
        junk tail's steal; a unique-suffix gate let a branch's own twin sub-branches strip
        their turn's text and hand the keep to an older sibling — reply loss, this repo's one
        fatal error). The accepted cost is cosmetic: a stub twin inside the winning branch
        renders one output-less duplicate tool row, and a grafted burst inside it costs
        nothing (system records never become atoms). The qualifying properties are the ones
        every incident's salvaged branch has in the author's corpus scan (49/49 across 4,678
        transcripts; the 2,809 non-eclipse rewind forks: 2,035 stub pairs, 700 superseded
        retries, 42 user-gesture rollbacks, 32 other; zero overlap). Sibling branches demote
        to "rewind", exactly as their on-spine twins classify.

        When NO chain qualifies, the two eclipse terminals part ways, each on its own event:
          "user"      — the flush COMPLETED (the next prompt sits past the spur), so a machine-
                        orphaned reply would qualify if one existed; a USER-HEADED branch here
                        is indistinguishable from a rollback the storm raced, and re-showing
                        deleted content is the hazard this verdict was designed around. The
                        user-headed branches demote to "rewind" — and ONLY those, where
                        user-headed means a user PROMPT at the fork-side head: an assistant-
                        headed textless branch (a turn of pure tool activity) and a stranded
                        tool_result head (user-TYPED but machine-MADE) are both provably not
                        rollback residue, and they stay "eclipsed"/kept.
          "exhausted" — the spur is the transcript's TAIL (a parse racing the multi-line
                        flush, a session dead mid-storm) or a corrupt machine cycle: no next
                        prompt exists, so no user gesture can have abandoned anything, and
                        keep-on-unprovable — this module's bias — holds. Everything stays
                        "eclipsed".
        The CLI's buffered flush is the root cause (filed as anthropics/claude-code#91113);
        this parse-side keep defends regardless."""
        ecl = {u for u, v in verdict.items() if v == "eclipsed"}
        if not ecl:
            return
        children = {}
        for u in self.by_uuid:
            p = self.parent_of.get(u)
            if p is not None:
                children.setdefault(p, []).append(u)
        forks = {}                        # fork uuid -> its eclipsed branch heads
        for u in ecl:
            p = self.parent_of.get(u)
            if p in active:
                forks.setdefault(p, []).append(u)
        landed = None                     # text-bearing assistant uuids (the reply witness) — computed
        #                                   lazily: chain_verdicts runs on every build of a held
        #                                   session, and most transcripts carry no eclipse at all
        for F, heads in forks.items():
            comp, stack = set(), list(heads)
            while stack:                  # the branch component: child-closure of the eclipsed heads
                x = stack.pop()
                if x in comp or x not in ecl:
                    continue
                comp.add(x)
                stack.extend(children.get(x, ()))
            if landed is None:
                landed = self.landed_text_uuids()
            # SELECTION IS PER BRANCH — the sub-tree under each fork-side head — never per
            # leaf-chain. Leaf-chains of one branch SHARE records, and any per-chain read of
            # shared state proved breakable by construction: a full-chain gate let a junk tail
            # inherit the prefix's text and steal the pick on write order, and a unique-suffix
            # gate let a branch's own twin sub-branches strip their turn's text record from
            # both suffixes and disenfranchise the REAL reply (an older sibling then stole the
            # keep — reply loss, this repo's one fatal error). The branch is the unit the CLI
            # persisted; it is kept or dropped WHOLE. The known cost is cosmetic and deliberate:
            # a parallel stub twin inside the winning branch renders one output-less duplicate
            # tool row, and a grafted error burst inside it costs nothing at all (system
            # records never become atoms, kept or not). Never trade a possible reply for a
            # duplicate row.
            def _subtree(h):
                seen, st = set(), [h]
                while st:
                    x = st.pop()
                    if x in seen or x not in comp:
                        continue
                    seen.add(x)
                    st.extend(children.get(x, ()))
                return seen
            qual, user_heads = [], []
            for h in heads:
                hr = self.by_uuid.get(h) or {}
                head_t = hr.get("type")
                if head_t == "user" and not _is_tool_result(hr):
                    user_heads.append(h)  # rollback-shaped: the "user" stand-down's one target.
                #                           A tool_result head is user-TYPED but machine-MADE (a
                #                           result the storm stranded beside its on-spine tool_use)
                #                           — provably not rollback residue, so it stays kept; its
                #                           output even reunites with the spine's tool call.
                if head_t != "assistant":
                    continue              # fork-side head isn't a streamed reply record (a user gesture, a burst)
                sub = _subtree(h)
                txt = [self.seq_of.get(x, 0) for x in sub
                       if x in landed and not (self.by_uuid.get(x) or {}).get("isApiErrorMessage")]
                #                           failure echoes (isApiErrorMessage: error-as-text) are not
                #                           replies — mirrors atoms()' own refusal to anchor on them
                if not txt:
                    continue              # no reply text anywhere in the branch → a stub pair, a burst
                qual.append(((max(txt), self.seq_of.get(h, 0)), h, sub))
                #             ^ ranked by the branch's LATEST REPLY TEXT — the CLI's final word on the
                #               turn. Junk records carry no text, so nothing textless can move the key.
            if qual:
                keep = max(qual)[2]
                for u in comp - keep:
                    verdict[u] = "rewind"     # sibling branches drop exactly as their on-spine twins do
            elif fork_terminal.get(F) == "user":
                for h in user_heads:          # completed flush, no reply branch → drop ONLY rollback-shaped
                    for u in _subtree(h):     # branches (fork-side head = the user's own deleted record);
                        verdict[u] = "rewind" # an assistant-headed textless branch (a tool-only turn) is
                #                               provably not rollback residue and stays kept
            # "exhausted" with no qualifying branch: everything stays eclipsed (keep-on-unprovable)

    def kept_uuids(self, active):
        """The active leaf-ancestors PLUS any line on a BROKEN chain (its parentUuid points
        at a uuid that exists in NO transcript — corruption / a partial write). The two
        kinds of off-path line we DO drop are both intentional: a rewind fork (its chain
        rejoins the active spine) and a clear branch (its chain reaches a clean null root
        the leaf does not share — `/clear` breaks the parent link, spec-mandated drop). A
        dangling chain is the one thing we cannot prove dead, and silently dropping a real
        ask is this repo's one fatal error, so we keep it. (Verified 0 dangling cases
        across the live corpus: this is a safety net, not a behavior change.)
        An ECLIPSED chain is also kept: a machine-written api_error spur stole the leaf's
        ancestry from a turn's real output (see chain_verdicts) — dropping it ate the only
        visible copy of a rendered reply (T209).
        Derived from chain_verdicts — one implementation, so the exported membership
        predicate (chain_membership) can never diverge from what the parse keeps.
        set(active) is unioned as-is: the walk can record a dangling FINAL ancestor that is
        in no file's index, and it has always been kept. Within an eclipsed fork's component,
        _select_eclipsed_chains has already narrowed the verdict — to the one machine-orphaned
        reply chain when one qualifies, and otherwise per that walk's terminal rules (user-
        headed chains demote behind a completed flush; a tail spur keeps everything) — so this
        union keeps exactly what the eclipse salvages."""
        return set(active) | {u for u, v in self.chain_verdicts(active).items()
                              if v in ("broken", "eclipsed")}

    def _absorbed_atom(self, full, t, seq, auid, rompuuid, postal_index):
        """One synthesized user atom for a mid-turn splice. The atom carries the FULL text — any
        whitespace-collapsed form is for MATCHING only (the user 2026-07-08: collapsing ate the blank
        line between a follow-up's quoted context and the typed reply, so markdown folded the reply
        INTO the blockquote; and the kernel's optimistic echo could never text-prune against the
        collapsed copy, so the message rendered TWICE)."""
        blocks = [{"type": "text", "text": full}]
        atom = {
            "type": "user", "uuid": auid, "session_id": rompuuid,
            "t": t, "fsid": self.fsid_of.get(auid),
            "parentUuid": (self.by_uuid.get(auid) or {}).get("parentUuid"),
            "message": {"role": "user", "content": blocks},
            "author": author_of(blocks, None, postal_index, getattr(self, "sdk_human", False)),
            "absorbed": True,   # a mid-turn splice: the turn's FOLLOWING atoms are the interrupted
            #                     ask's continuing work, not provably a reply to this — judges must
            #                     not read them as one (jd._seg_spliced / _strip_unevidenced_dones).
            #                     Metadata only: the atom set and seg ids are unchanged (no
            #                     PLACEMENTS_V bump).
            "_seq": seq,
        }
        if ROMP_AUTO_RE.search(full):   # an AUTO-nudge → flag it, mirroring the native user-record path
            atom["rompAuto"] = True
        return atom

    def _absorbed(self, qatts, kept, st, rompuuid, postal_index):
        """Mid-turn prompts spliced into a running turn. The witness is the queued_command
        ATTACHMENT record: the CLI writes one per splice, uuid-bearing and parent-chained,
        carrying the FULL prompt text and stamped with the ENQUEUE timestamp — and writes
        NONE for a dequeued prompt (that resurfaces as a native user line), a still-pending
        one, or a popAll (a recall: the queue is cleared, nothing spliced). So each
        attachment becomes one user atom, at its own timestamp: the moment the user sent it.

        The queue-operation ledger is deliberately NOT read at all: its dequeue/remove
        records are anonymous, and a CLI killed with items queued never writes their
        resolutions — one missing resolution shifted EVERY later FIFO pairing, so a message
        typed at 16:56 was stamped with another message's resolution time and rendered as
        the NEWEST message in the chat, hours out of place, while never-delivered
        task-notifications rendered as absorbed prompts at junk times (the user 2026-07-10,
        the nimbus session). The witness is universal: 0 of the live corpus's 104
        remove-bearing transcripts lack attachments, so dropping the ledger loses nothing.

        DEPLOY RULE: changing WHICH atoms this class emits from an existing transcript (here
        or in atoms()) changes placement identity just like an id drift — previously-invisible
        atoms become fresh plannable segments and dormant sessions replay them as new goals
        (2026-07-10). Bump jd.PLACEMENTS_V in the same commit; tests/test_placements_canary.py
        pins both dimensions."""
        atoms, emitted = [], st["absorbed_keys"]   # dedup memory rides the carry: one set whether
        #                                            the qatts arrive in one full parse or many folds
        for q in qatts:
            if q["ts"] is None:
                continue   # unparseable timestamp — nowhere truthful to place it
            key = (q["ts"], " ".join(q["text"].split()))
            if key in emitted:
                continue   # identical (ts, text) copies are the SAME splice written more than
                           # once (compaction/resume replays the record verbatim — x2 is common
                           # in the live corpus, one retry storm hit x24)
            if q["uuid"] is not None and q["uuid"] not in kept:
                continue   # this copy sits on a rewound branch — a kept twin may still emit
            emitted.add(key)
            a = self._absorbed_atom(q["text"], q["ts"], q["seq"], q["uuid"],
                                    rompuuid, postal_index)
            if not _author_final(a.get("author"), q["text"]):
                st["postal_miss_att"].add(key)   # trailing marker unresolved — the heal re-checks
            atoms.append(a)
        return atoms

    def atoms(self, rompuuid, postal_index):
        """Every emitted atom on the active path (plus broken-chain survivors and eclipsed
        machine-orphaned reply chains — see _select_eclipsed_chains), plus synthesized
        absorbed atoms. (Idle atoms are added separately from the state log.)
        One fold from an EMPTY carry over every kept record in chronological order — the
        assembly cache (_assemble) folds later appends through the same _prepass/_emit_fold
        code with the carried state, so full parse and fold are one implementation."""
        active = self.active_path()
        kept = self.kept_uuids(active)
        st = _emit_state()
        order = _chrono(self, kept)
        self._prepass(order, st)
        out = list(self._emit_fold(order, st, rompuuid, postal_index))
        out += self._absorbed(self.qatts, kept, st, rompuuid, postal_index)
        return out

    def _prepass(self, order, st):
        """Fold the cross-record facts of `order` (kept records, ascending (t, seq)) into the
        carry `st`: replay classification, boundary summaries, Skill payload ids, command twins,
        and the chronological watermark. The emit loop reads these and never writes them. The
        assembly fold calls this with only the appended kept records — valid because its gates
        guarantee the delta sorts at-or-after everything already folded."""
        replay_uuids, _seen_text, _compacted = st["replay"], st["seen_text"], st["compacted"]
        _seen_exact, _restoring = st["seen_exact"], st["restoring"]
        summaries, last_boundary = st["summaries"], st["last_boundary"]
        # Post-compaction REPLAY dedup (the user 2026-06-22): a compact_boundary restores the recent message
        # tail VERBATIM with NEW uuids/timestamps. A replayed user prompt is the same text as an EARLIER one,
        # after a boundary — NOT new work; left in, it gets a fresh seg-id and the judges re-mint an
        # already-done (even CLEARED) goal. Identify replays in CHRONOLOGICAL order (the emit loop is
        # order-agnostic) so we keep the ORIGINAL and drop the later replay — then placements dedup still holds.
        for u in order:
            r = self.by_uuid.get(u)
            if not r:
                continue
            if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
                _compacted = True
                if u not in self._adopted:
                    # the restore burst starts here and ends at the next assistant. An ADOPTED
                    # boundary (a LIVE manual compact) never arms it: its transcript replays NO
                    # tail — the records after it are the user's genuine next actions, and the
                    # armed window silently ate the next typed prompt whenever its text repeated
                    # any earlier message ("continue", a nudge) — a dropped real ask (2026-08-19).
                    # Attached boundaries (auto, and the resume re-splice, which DOES replay) keep it.
                    _restoring = True
                last_boundary = u
            elif r.get("type") == "assistant":
                _restoring = False         # work resumed → anything later is new, not restored context
            elif r.get("type") == "user" and r.get("isCompactSummary") is True:
                stext = _text_of(_content(r.get("message")))
                if last_boundary and stext:            # attach to the boundary just seen; cap for transport
                    summaries[last_boundary] = stext[:SUMMARY_CAP] + (
                        "\n\n…(summary truncated)" if len(stext) > SUMMARY_CAP else "")
            elif r.get("type") == "user" and not r.get("isMeta"):
                txt = _text_of(_content(r.get("message")))
                if txt:
                    # A REPLAY is one of two measured shapes, and neither is "this text was said before"
                    # (the user 2026-08-01). Text alone identified something else entirely: any message a
                    # person ever repeats. Once a session had compacted, the SECOND "Now?", "retry",
                    # "[Request interrupted by user]" or romp notice was read as a replay of the first and
                    # dropped from the chat outright, however many days apart — the live case was a
                    # one-word question whose ANSWER rendered while the question itself did not, in a
                    # session that had compacted two days earlier. Over 22 compacted transcripts, 179
                    # duplicate texts differ in timestamp (genuine repeats, all being eaten) against 90
                    # that share one. So:
                    #   * a VERBATIM re-write — same text at the same SECOND — is the same record written
                    #     twice (resume/compaction re-splice; the absorbed-send dedupe below has keyed on
                    #     exactly this pair all along, and its comment records x2 as common, x24 once);
                    #   * a RESTORED TAIL — duplicate text inside the burst that follows a boundary, before
                    #     any assistant work resumes — is the shape this guard was written for.
                    # Outside those, a repeat is a message the person actually sent, and it renders.
                    # The exact-pair case needs NO compaction gate: a re-write carries the original's
                    # timestamp, so this chronological walk meets it right beside the original — before
                    # the boundary that produced it — and gating on _compacted would never fire. It is
                    # the same record either way. Only the restore-burst case is compaction-scoped.
                    key = (int(self.ts_of.get(u) or 0), txt)
                    if key in _seen_exact or (_compacted and _restoring and txt in _seen_text):
                        replay_uuids.add(u)
                    else:
                        _seen_exact.add(key)
                        _seen_text.add(txt)
        st["compacted"], st["restoring"], st["last_boundary"] = _compacted, _restoring, last_boundary
        # The chronological watermark the assembly fold's monotonic gate compares appended records
        # against: a delta that sorts at-or-after everything already folded is the invariant that
        # makes carrying the sets above across folds valid at all. `order` is ascending by
        # (ts_of, seq), so the LAST pre-pass-relevant record carries the max — one lookup, not a
        # second sweep of the whole session (that re-walk measured 33ms at 30k). MUST read the
        # repaired ts_of, the exact key the sort used: reading the raw stamp here let a garbled
        # TAIL record (which borrows the max stamp and sorts last) zero the watermark and disarm
        # the g:ts gate — a reproduced fold!=full divergence (T210 review).
        for u in reversed(order):
            r = self.by_uuid.get(u) or {}
            if r.get("type") in ("user", "assistant") or (
                    r.get("type") == "system" and r.get("subtype") == "compact_boundary"):
                ts = self.ts_of.get(u)
                if ts and ts > st["max_ppt"]:
                    st["max_ppt"] = ts
                break
        # Skill tool_use block ids: the anchor for the NEW skill-instructions shape (2026-07-10). Newer
        # CLIs inject the payload as an isMeta user record whose sourceToolUseID names the invoking Skill
        # tool_use — the text no longer starts with the "Base directory for this skill:" preamble
        # SKILL_CONTENT_RE keys on, so the designed link is the id, not a prefix.
        skill_tool_ids = st["skill_ids"]
        for u in order:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "assistant":
                for b in _content(r.get("message")) or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and \
                            b.get("name") == "Skill" and b.get("id"):
                        skill_tool_ids.add(b["id"])
        # Bare invocation TWINS (CLI 2.1.215+, the user 2026-07-20): a typed slash command lands TWICE —
        # a raw-text user record (the submitted prompt verbatim, carrying promptId) AND the
        # <command-name> wrapper (same promptId). The wrapper becomes the tracked command atom below;
        # the raw twin carries no wrapper, no isMeta, no isCompactSummary, so it would fall through as
        # a genuine HUMAN atom — and the planner minted a feed card from a /compact ("Compact
        # conversation context", the rescue thread). Collect wrapper promptIds so the twin drops as the
        # invocation echo it is.
        cmd_prompt_names = st["cmd_names"]
        for u in order:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "user" and r.get("promptId"):
                btext = _text_of(_content(r.get("message"))) or ""
                # the SAME matcher the emit path uses (COMMAND_NAME_ANY_RE inside a wrapper record): the
                # anchored-only form missed every <command-message>-FIRST invocation (skills / custom
                # commands), so shape-B twins survived as phantom human segments beside the real command
                # atom — same hash, different t (2026-08-13; the emit path got this fix on 2026-07-22 and
                # this pre-pass silently didn't).
                m = COMMAND_NAME_RE.match(btext) or (COMMAND_NAME_ANY_RE.search(btext)
                                                     if CMD_WRAP_RE.match(btext) else None)
                if m:
                    name = m.group(1).strip() or "/?"
                    cmd_prompt_names.setdefault(r["promptId"], set()).add(
                        name if name.startswith("/") else "/" + name)

    def _emit_fold(self, order, st, rompuuid, postal_index):
        """Yield each kept record's atom (some records emit none) — record-local given the carry
        `st` that _prepass filled and the graph maps. The full parse and the assembly fold drive
        this SAME generator; the fold passes only the appended kept records, so the two paths
        cannot drift."""
        replay_uuids, summaries = st["replay"], st["summaries"]
        skill_tool_ids, cmd_prompt_names = st["skill_ids"], st["cmd_names"]
        for u in order:
            r = self.by_uuid.get(u)
            if not r:
                continue
            t = r.get("type")
            ts = self.ts_of.get(u, 0)   # repaired at ingest — never None (T210)
            fsid = self.fsid_of.get(u)
            seq = self.seq_of.get(u, 0)
            if t == "assistant":
                a = {"type": "assistant", "uuid": u, "session_id": rompuuid,
                     "t": ts, "fsid": fsid, "parentUuid": r.get("parentUuid"),
                     "message": _norm_message(r.get("message")), "_seq": seq}
                if r.get("isApiErrorMessage"):
                    a["isApiError"] = True   # a FAILURE record — Claude Code writes the error as an
                                             # assistant text block, so it carries text but is NOT a
                                             # reply; deep-link anchors must skip it (kernel _seg_anchors)
                    if isinstance(r.get("apiErrorStatus"), int):
                        a["apiErrorStatus"] = r["apiErrorStatus"]   # → the durable chat card's badge ("API error · 529")
                yield a
            elif t == "user":
                blocks = _content(r.get("message"))
                btext = _text_of(blocks) if blocks else ""
                # SLASH-COMMAND TURN (the user 2026-06-29): a "/usage"-style command is no longer dropped — its
                # INVOCATION becomes a `command`-flagged user atom (an opener → a tracked, working turn that
                # shows in the chat + timeline) and its OUTPUT a synthetic assistant atom (so the turn has a
                # reply and ENDS naturally). The `command` flag makes the planner/judge skip it (never a goal /
                # feed card — see _seg_command). This runs BEFORE the isMeta skip because some Claude versions
                # mark these records isMeta. The other wrappers (message/args/contents/caveat) stay skipped.
                mcmd = COMMAND_NAME_RE.match(btext) or (COMMAND_NAME_ANY_RE.search(btext)
                                                        if CMD_WRAP_RE.match(btext) else None)
                if mcmd and u not in replay_uuids:
                    name = mcmd.group(1).strip() or "/?"
                    if not name.startswith("/"):
                        name = "/" + name
                    margs = COMMAND_ARGS_RE.search(btext)
                    args = (margs.group(1).strip() if margs else "")
                    disp = name + ((" " + args) if args else "")
                    yield {"type": "user", "uuid": u, "session_id": rompuuid, "t": ts,
                           "fsid": fsid, "parentUuid": r.get("parentUuid"), "_seq": seq,
                           "author": "human", "command": name,
                           "message": {"role": "user", "content": [{"type": "text", "text": disp}]}}
                    continue
                mout = LOCAL_STDOUT_RE.match(btext)
                if mout and u not in replay_uuids:
                    yield {"type": "assistant", "uuid": u, "session_id": rompuuid, "t": ts,
                           "fsid": fsid, "parentUuid": r.get("parentUuid"), "_seq": seq, "command": True,
                           "message": {"role": "assistant",
                                       "content": [{"type": "text", "text": strip_ansi(mout.group(1)).strip()}],
                                       "stop_reason": "end_turn"}}
                    continue
                has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result"
                                      for b in (blocks or []))
                twins = cmd_prompt_names.get(r.get("promptId") or "")
                if twins and not has_tool_result and any(
                        btext.strip() == n or btext.strip().startswith(n + " ") for n in twins):
                    continue   # the raw-text TWIN of a slash invocation (see the pre-pass above) — the
                               # wrapper is the one tracked command atom; this is its echo, not a message
                if btext and not has_tool_result and u not in replay_uuids and \
                        (SKILL_CONTENT_RE.match(btext) or r.get("sourceToolUseID") in skill_tool_ids):
                    # a Skill invocation's INSTRUCTIONS payload — kept, but flagged and content-EMPTY:
                    # assistant-flavored with no stop so it can neither open nor close the running turn,
                    # and the markdown rides skillMd where generic text readers never look. BEFORE the
                    # isMeta skip (the CLI marks the record isMeta), like the command paths above (the
                    # user 2026-07-08). TWO shapes: the legacy "Base directory for this skill:" preamble
                    # (SKILL_CONTENT_RE) and the newer sourceToolUseID link to the invoking Skill
                    # tool_use (the user 2026-07-10 — a 151KB skill md rendered as a giant note box
                    # because the prefix missed, the isMeta skip ate the record, and the un-superseded
                    # LIVE atom stuck around forever). The tool_result guard keeps the Skill tool's own
                    # "Launching skill: X" result out of this branch if it ever carries the same link.
                    yield {"type": "assistant", "uuid": u, "session_id": rompuuid, "t": ts,
                           "fsid": fsid, "parentUuid": r.get("parentUuid"), "_seq": seq,
                           "skillMd": btext[:SKILL_MD_CAP] + ("\n\n…(skill content truncated)"
                                                              if len(btext) > SKILL_MD_CAP else ""),
                           "message": {"role": "assistant", "content": [], "stop_reason": None}}
                    continue
                # A postal DELIVERY is a message, not harness noise, even though the CLI flags it isMeta:
                # Claude Code hands romp mail to a session as Stop-hook feedback, and that record carries
                # isMeta, so this skip ate every hook-delivered message whole. It never became an atom, so
                # no user event reached _hydrate_postal, no incoming card was built, and nothing carried
                # the message id — which is why a timeline arc into one of these landed nowhere while the
                # transcript plainly contained it (the user 2026-07-23; romp_docs found the record).
                # Deliveries arriving by other paths were unaffected, which is why it failed for some
                # messages and not others. Keyed on the romp-msg-id marker, so only real mail is admitted
                # and the `<command-…>` echoes and caveats stay skipped.
                if IMG_ECHO_RE.match(btext) and not has_tool_result:
                    continue   # synthetic image-read placeholder (twin of sdk_backend's _IMG_ECHO_RE) — the
                               # tool that fed the image already shows; on disk it's also isMeta (below)
                if r.get("isMeta") is True and not POSTAL_RE.search(btext):
                    continue   # `<command-…>` echoes / caveats — harness noise, not a message
                if r.get("isCompactSummary") is True:
                    continue   # the compaction SUMMARY payload — kept in the graph (above) but not an atom;
                               # the compaction itself is the system:compact_boundary atom below
                if u in replay_uuids:
                    continue   # a post-compaction REPLAY of restored context (see the pre-pass above) — not new
                               # work; dropping it keeps the planner's seg-id dedup intact, so no goal re-mints
                if not blocks:
                    continue
                if CMD_WRAP_RE.match(btext):
                    continue   # the remaining slash-command wrappers (message/args/contents/caveat) — noise
                ps = r.get("promptSource")
                atom = {"type": "user", "uuid": u, "session_id": rompuuid, "t": ts,
                        "fsid": fsid, "parentUuid": r.get("parentUuid"),
                        "message": _norm_message(r.get("message")), "_seq": seq}
                if has_tool_result and isinstance(r.get("toolUseResult"), dict) \
                        and (set(r["toolUseResult"]) & TUR_CONSUMED_KEYS):
                    # The record's top-level toolUseResult — Claude Code's STRUCTURED result (Edit's
                    # structuredPatch, AskUserQuestion's answers map). The kernel's chat build reads it
                    # at tool_result attach time; the atom used to drop it, so every consumer silently
                    # fell to its lossy fallback (regex-scraping the flat output string — which is how
                    # quote-bearing AskUserQuestion answers vanished from the answered box). Dict form
                    # only: an errored result records a plain string, which no consumer reads. Carried
                    # ONLY when a consumed key is present (_TUR_CONSUMED_KEYS): an unconditional carry
                    # held every Read result's full file bytes in the parse cache by reference —
                    # roughly a fifth of transcript bytes on read-heavy sessions — for shapes nothing
                    # reads. Widen the key set when a new consumer appears; never back to carry-all.
                    atom["toolUseResult"] = r["toolUseResult"]
                if ps:
                    atom["promptSource"] = ps
                author = author_of(blocks, ps, postal_index, getattr(self, "sdk_human", False))
                if author is not None:
                    atom["author"] = author
                    if not _author_final(author, btext):
                        st["postal_miss_rec"].add(u)   # trailing marker unresolved — the assembly
                        #                                heal re-authors this record until it lands
                if ROMP_AUTO_RE.search(_text_of(blocks)):   # an AUTO-nudge → flag it (vs a button/typed follow-up)
                    atom["rompAuto"] = True
                yield atom
            elif t == "system" and r.get("subtype") == "compact_boundary":
                if (r.get("parentUuid") or r.get("logicalParentUuid")) == u:
                    continue   # self-anchored (corrupt): a boundary claiming to compact itself
                               # anchors nothing — no card, and no cycle for the turn builder
                sp = self._adopted.get(u)
                if sp is not None:
                    # an ADOPTED boundary's record is appended BEFORE its episode's stdout, so raw
                    # (t, seq) order would drop the card into the middle of the /compact exchange —
                    # and the stdout atom would then fold into the boundary's fresh turn as
                    # "assistant work", minting a phantom WORK unit (2026-08-19). Sort it right
                    # after the stdout instead: the moment the compaction visibly completed.
                    spt = self.ts_of.get(sp)
                    if spt is not None and (ts is None or spt > ts):
                        ts = spt
                    seq = self.seq_of.get(sp, seq) + 0.5
                cm = r.get("compactMetadata") or r.get("compact_metadata") or {}
                yield {"type": "system", "subtype": "compact_boundary", "uuid": u,
                       "session_id": rompuuid, "t": ts, "fsid": fsid,
                       "parentUuid": self.parent_of.get(u),   # the repaired stitch (see _repair_compaction_stitches);
                       #                                        for an ADOPTED boundary, its episode's stdout record
                       "compact_metadata": {"trigger": cm.get("trigger"),
                                            "pre_tokens": cm.get("preTokens") or cm.get("pre_tokens"),
                                            "post_tokens": cm.get("postTokens") or cm.get("post_tokens")},
                       "summary": summaries.get(u),   # the compaction SUMMARY captured in the pre-pass (or None)
                       "_seq": seq}
            elif t == "system" and r.get("subtype") == "model_refusal_fallback":
                # The model's safeguards flagged the prompt and the CLI silently retried the turn on a
                # fallback model. That is CONVERSATION state, not harness bookkeeping — the reply that
                # follows came from a different model, and the user must see the swap where it happened
                # (the user 2026-08-03, after a fable→opus swap mid-turn was invisible in the chat).
                # The record's timestamp is the retry start, so the atom sorts BEFORE the fallback
                # model's reply. Non-opener (not a user atom), so it folds into the running turn.
                yield {"type": "system", "subtype": "model_refusal_fallback", "uuid": u,
                       "session_id": rompuuid, "t": ts, "fsid": fsid,
                       "parentUuid": self.parent_of.get(u),
                       "content": r.get("content") or "",          # the CLI's full explanation
                       "fallback_from": r.get("originalModel") or "",
                       "fallback_to": r.get("fallbackModel") or "",
                       "_seq": seq}
            # other system subtypes (turn_duration, stop_hook_summary, local_command,
            # away_summary) are harness bookkeeping, not conversational messages -> skipped.


# A session is NOT working once it has STOPPED: the tmux hook writes state:"waiting" on the Stop event (the
# agent handed the floor back) and state:"idle" on the idle-prompt later. Both terminate the turn — keying
# only on "idle" left a finished session whose last assistant message wasn't a clean end_turn (e.g. it ended
# on a tool_use) stuck reading "working" from Stop until the idle-prompt eventually landed (the user 2026-06-25,
# "reverting working when stuff isn't working"). Event-based, not a grace timer.
_IDLE_STATES = ("idle", "waiting")


def synthesize_idle(states, atoms, now):
    """Idle atoms from real idle/stopped transitions in states/<sid>.jsonl — NOT a 15-minute silence
    heuristic. An idle span runs from an `state` in _IDLE_STATES (the Stop "waiting" or the idle-prompt
    "idle") to the next state record (or to `now` if it is the last). Only spans overlapping the session's
    atom timespan are kept, so unrelated history doesn't leak in."""
    rows = sorted([r for r in states if isinstance(r, dict) and r.get("t") is not None],
                  key=lambda r: r["t"])
    if not rows or not atoms:
        return []
    lo = min(a["t"] for a in atoms)
    hi = max(a.get("end", a["t"]) for a in atoms)
    out = []
    for i, r in enumerate(rows):
        if r.get("state") not in _IDLE_STATES:
            continue
        start = r["t"]
        end = rows[i + 1]["t"] if i + 1 < len(rows) else (now if now is not None else hi)
        if end <= start:
            continue
        if end < lo or start > max(hi, now or hi):
            continue   # span entirely outside the session's activity window
        out.append({"type": "idle", "uuid": None, "session_id": atoms[0]["session_id"],
                    "t": start, "end": end, "_seq": 10 ** 12 + start})
    return out


def synthesize_orphans(states, atoms, landed_text_uuids=None):
    """Salvaged assistant replies from orphanReply markers in states/<sid>.jsonl — text that STREAMED
    live but the transcript never kept (an API-errored try; the SDK backend persists it at settle,
    see its append_orphan_reply). The kernel's chat build has interleaved these since 2026-07-21, but
    this parse never did — so every judge (planner/closer/distiller/briefer) read those turns as
    having NO reply at all (found by a peer session 2026-07-25, ~1,600 markers fleet-wide): the
    planner confabulated outcomes for them, and once the workless-segment guard landed it flipped to
    never filing genuinely finished work as done. Each marker becomes a real assistant atom at its
    timestamp, DEDUP'd the same way the chat build dedups — by uuid, then exact-or-either-way-prefix
    text against what the disk kept (a retry that re-replied never doubles) and against earlier
    markers (settles can re-orphan the same reply). A marker carrying the CLI's own error text
    ("API Error: …") is skipped: markers written before the backend tagged error settles isApiError
    hold that noise, and it must not resurface as work.

    landed_text_uuids (FileAdapter.landed_text_uuids): text-bearing assistant uuids on ANY branch
    of the transcript graph, kept or dropped. A marker whose uuid landed SOMEWHERE is never a loss
    — when its record is off the kept path, that is a rollback's deliberate abandonment, and
    resurrecting it un-deletes the tail the user rolled back (the ghost-reply bug, the user
    2026-08-03: the deleted message vanished but its reply came back once the rollback was
    consumed, since the atoms-only dedup below can no longer see the abandoned record)."""
    if not atoms:
        return []
    # TEXT-BEARING uuids only (the user 2026-07-28): a marker whose uuid the disk knows solely as a
    # TEXTLESS record must still interleave. On some model+tool combinations (observed: fable-5 replying
    # before an AskUserQuestion) the CLI persists the streamed reply text as an EMPTY thinking record
    # under the same uuid — the very loss the marker salvages — so counting that twin as "seen" ate the
    # salvage. A retry that DID re-reply carries its text and still dedups, as does a re-orphaned marker
    # (the add below); the prefix check against disk_texts guards every remaining double.
    seen_uuids = {a.get("uuid") for a in atoms
                  if a.get("uuid") and _text_of(_content(a.get("message"))).strip()}
    disk_texts = [t for a in atoms if a.get("type") == "assistant"
                  if (t := _text_of(_content(a.get("message"))).strip())]
    sid = atoms[0]["session_id"]
    out = []
    for r in states or []:
        if not isinstance(r, dict) or not r.get("t"):
            continue
        orq = r.get("orphanReply")
        if not isinstance(orq, dict):
            continue
        txt = (orq.get("text") or "").strip()
        if not txt or txt.startswith("API Error:") or txt == "(no content)":
            # "(no content)" is the CLI's own placeholder for a contentless command-feedback message
            # (an SDK /clear streams one; its transcript record is a system/local_command row). Salvaged
            # as a real assistant atom it read as MODEL WORK: _seg_command_worked turned True for the
            # bare /clear turn and the planner minted a "clearing conversation history" card (the user
            # 2026-07-27). Every such marker in the live corpus rides a command turn, which plans no
            # units at all once skipped — so this drops no real work and needs no PLACEMENTS_V bump.
            continue
        u = orq.get("uuid") or ""
        if u and landed_text_uuids is not None and u in landed_text_uuids:
            continue   # the disk kept this reply on SOME branch — possibly one a rollback abandoned
        if u and u in seen_uuids:
            continue
        if any(dt.startswith(txt) or txt.startswith(dt) for dt in disk_texts):
            continue
        out.append({"type": "assistant", "uuid": u or ("orphan:%d" % int(r["t"])), "session_id": sid,
                    "t": int(r["t"]), "fsid": None, "parentUuid": None, "orphaned": True,
                    "message": {"role": "assistant", "content": [{"type": "text", "text": txt}],
                                "stop_reason": "end_turn"},   # the marker is written AT settle — the turn ended
                    "_seq": 10 ** 12 + int(r["t"])})
        if u:
            seen_uuids.add(u)
        disk_texts.append(txt)
    return out


# ═════════════════════════ SUBSTRATE-NEUTRAL: turns over atoms ═════════════════════════
def is_interrupt_record(atom):
    """The CLI's own stop record — a user atom reading '[Request interrupted by user]' (Esc) or
    '[Request interrupted by user for tool use]' (a permission prompt dismissed). It is the interrupt
    EVENT itself, written by the CLI whether the stop came from romp's Stop button or a raw Esc in the
    pane — so it must END its turn (the user 2026-07-05: without this, the dangling user atom read as
    an OPEN turn, so the chip latched 'Interrupting…' for the full 120s cap and _ops_gate parked a
    /model pick against a session that was actually idle). Public: the kernel's auto-nudge gate keys
    on the same event."""
    if atom.get("type") != "user":
        return False
    return _text_of(_content(atom.get("message"))).startswith("[Request interrupted by user")


def _is_opener(atom):
    """A genuine new prompt opens a turn: author human / sdk / peer / romp. `system`
    (`<task-notification>`) and tool_result-only atoms fold in, never open. A romp follow-up
    (a feed NUDGE / auto-nudge carrying the romp-injected marker, author 'romp') IS a fresh
    prompt to the agent — it MUST open its own turn so the planner reads the romp-goal-id off
    the trigger, reopens that goal, and files the reply under it. Without this it folds into the
    prior (often already-completed) turn, so the judges never see the follow-up and the goal
    never reopens (the user 2026-06-21)."""
    if atom["type"] != "user":
        return False
    a = atom.get("author")
    return a in ("human", "sdk", "romp") or isinstance(a, dict)


def _turn_id(rompuuid, turn):
    """`${rompUuid}:${t}:${hash}` — anchor-keyed, fork-stable (the trigger's text, or the
    first atom's text for an autonomous turn)."""
    atoms = turn["atoms"]
    text = ""
    trig = turn["trigger"]
    if trig:
        a = next((x for x in atoms if x.get("uuid") == trig["uuid"]), None)
        if a:
            text = _text_of(_content(a.get("message")))
    elif atoms:
        text = _text_of(_content(atoms[0].get("message")))
    h = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]
    return "%s:%d:%s" % (rompuuid, turn["t"], h)


def segment_turns(atoms, rompuuid):
    """Group atoms into `end_turn`-bounded turns. A turn opens at an opener atom (or at
    the first non-opener if work begins without one) and runs until the next opener
    that arrives AFTER the turn hit `end_turn`. A new prompt arriving while the turn is
    still open (last assistant stop_reason != end_turn) is a mid-turn input (absorb),
    kept inside the turn — that is how one turn holds several inputs."""
    atoms = sorted(atoms, key=lambda a: (a["t"], a.get("_seq", 0)))
    turns = []
    cur = None
    ended = False     # has the current turn hit end_turn since its last opener?
    for atom in atoms:
        if atom["type"] == "system" and atom.get("subtype") == "compact_boundary":
            # Compaction always opens a FRESH turn (the user 2026-07-13): a non-opener would absorb into
            # the current turn, and _finalize_turn's end = max(atom ends) then stretched that turn's bar
            # to the boundary's timestamp — the timeline drew a phantom work period spanning the whole
            # idle gap "leading up to the moment of compaction", growing live while the compact ran. The
            # boundary anchors its own turn instead. ended=True so a GENUINE post-compact prompt opens
            # its own turn (it's a real ask — the planner needs it as a trigger); the CLI's autonomous
            # continuation (assistant atoms, non-openers) still files under the boundary turn, so its
            # bar starts AT the compaction, never before.
            cur = {"trigger": None, "atoms": [atom]}
            turns.append(cur)
            ended = True
            continue
        if _is_opener(atom):
            if cur is None or ended:
                cur = {"trigger": {"uuid": atom.get("uuid")}, "atoms": [atom]}
                turns.append(cur)
                ended = False
            else:
                cur["atoms"].append(atom)   # mid-turn input (absorbed)
        else:
            if cur is None:
                cur = {"trigger": None, "atoms": [atom]}   # autonomous / continuation
                turns.append(cur)
                ended = False
            else:
                cur["atoms"].append(atom)
        if atom["type"] == "assistant":
            sr = (atom.get("message") or {}).get("stop_reason")
            ended = sr in END_STOPS
        if atom["type"] == "user" and atom.get("command"):
            ended = True   # a slash-command invocation is self-contained → ends its turn so the NEXT prompt opens fresh
        if atom["type"] == "user" and is_interrupt_record(atom):
            ended = True   # the CLI's stop record — the interrupted turn is OVER; the next prompt opens fresh
    for turn in turns:
        _finalize_turn(turn, rompuuid)
    turns.sort(key=lambda t: t["t"])
    return turns


def _finalize_turn(turn, rompuuid):
    atoms = turn["atoms"]
    turn["t"] = atoms[0]["t"]
    turn["end"] = max(a.get("end", a["t"]) for a in atoms)
    # ended (FILE substrate): inferred from the turn's last assistant stop_reason, since
    # the transcript carries no `result` line. Interrupted / still-streaming -> False.
    last_sr = None
    for a in atoms:
        if a["type"] == "assistant":
            last_sr = (a.get("message") or {}).get("stop_reason")
    turn["ended"] = last_sr in END_STOPS
    # a slash-COMMAND turn with no reply/output atom is SELF-CONTAINED → ended (the user 2026-06-29). Without
    # this, a command that produced no output (a hung /usage, a control command) leaves the turn open forever,
    # so the session reads as "working" indefinitely and a stuck provisional card never resolves (the JLD case).
    # A command WITH output / model work ends naturally on that assistant atom's stop_reason above; this only
    # catches the bare-invocation case. (Working-during-execution is the live backend state's job, not this.)
    if not turn["ended"] and atoms[0].get("command") and not any(a["type"] == "assistant" for a in atoms):
        turn["ended"] = True
    # a compaction turn with no assistant work yet is likewise SELF-CONTAINED (the user 2026-07-13): the
    # boundary is a completed event, not in-flight work — left open it reads as a phantom open bar/WORKING
    # until the CLI's continuation lands (whose stop_reason then owns `ended` via the rule above).
    if (not turn["ended"] and atoms[0].get("type") == "system" and atoms[0].get("subtype") == "compact_boundary"
            and not any(a["type"] == "assistant" for a in atoms)):
        turn["ended"] = True
    # an INTERRUPT record at the turn's tail ends it (the user 2026-07-05): the CLI's stop record is the
    # interrupt event — the aborted assistant work before it never wrote an end_turn, so without this the
    # turn read open forever (stuck 'Interrupting…' chip, /model picks parked against an idle session).
    # Tail = last atom ignoring idle spans (a states overlay lands one after the record) and command
    # confirmations (a completed exchange, same skip _session_working does). An interrupt record MID-turn
    # (later work follows) means the turn resumed — that later work decides `ended`, so only the tail counts.
    if not turn["ended"]:
        i = len(atoms) - 1
        while i >= 0 and (atoms[i].get("command") or atoms[i]["type"] == "idle"):
            i -= 1
        if i >= 0 and is_interrupt_record(atoms[i]):
            turn["ended"] = True
    turn["id"] = _turn_id(rompuuid, turn)


# ── segment derivation: a turn split at its input atoms (timeline grain). DERIVED, not stored.
def _is_segment_input(atom):
    """A segment boundary is a genuine new input (opener or absorbed human/peer prompt).
    tool_result and `system` (task-notification) atoms do not start a segment; a
    higher layer MAY additionally split at a decision atom — the bottom layer does not."""
    return _is_opener(atom)


def _segment_id(rompuuid, seg_t, atoms, trigger_uuid):
    """`${rompUuid}:${seg.t}:${hash}` — parallel to the turn id; the summarizer layer's
    dedup key for a segment. Hash of the trigger atom's text (or the first atom's text
    for a triggerless/autonomous segment).

    A TEXT-LESS segment (a settle-seam tail, a tool-only continuation) has no content to hash —
    sha1("") is the SAME for every one, so a content key would alias them ALL under the
    timestamp-invariant _seg_key: a fresh working seam inherited a long-done seam's placement, and a
    session working past a completed goal showed a blank board (the user 2026-07-22). Its identity is
    instead its ANCHOR ATOM's uuid — unique per atom, present in the transcript, and STABLE across the
    judge parse (which carries the states/idle overlay) and the kernel render parse (which omits it),
    since the anchor is the segment's opener, a real atom the overlay never displaces (verified).
    Text-BEARING segments keep the content hash: it is drift-invariant across the SDK optimistic echo
    (send time) and the real transcript atom (process time), which share text but NOT uuid — so an
    atom-uuid key there would MISS its own echo. Hash the content, or — only when there is none — the
    anchor atom's identity."""
    text = ""
    anchor = None
    if trigger_uuid:
        a = next((x for x in atoms if x.get("uuid") == trigger_uuid), None)
        if a:
            text = _text_of(_content(a.get("message")))
            anchor = a
    if not text and atoms:
        anchor = anchor or atoms[0]
        text = _text_of(_content(atoms[0].get("message")))
    basis = text or (anchor or {}).get("uuid") \
        or next((a.get("uuid") for a in atoms if a.get("uuid")), "")   # first uuid-bearing atom if the anchor has none
    h = hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:8]
    return "%s:%d:%s" % (rompuuid, seg_t, h)


def segments(turn):
    """The per-input spans of a turn (what the timeline draws as bars). A segment runs
    from one input to the next (or to the turn end). Each carries a stable `id` for the
    summarizer layer. Pure function over a turn."""
    atoms = turn["atoms"]
    rompuuid = atoms[0]["session_id"] if atoms else ""
    starts = [i for i, a in enumerate(atoms) if _is_segment_input(a)]
    if not starts:
        segs = [{"t": turn["t"], "end": turn["end"],
                 "trigger": turn["trigger"]["uuid"] if turn["trigger"] else None,
                 "atoms": list(atoms)}]
    else:
        bounds = starts + [len(atoms)]
        segs = []
        for k, i0 in enumerate(starts):
            i1 = bounds[k + 1]
            segs.append({"t": atoms[i0]["t"],
                         "end": atoms[i1]["t"] if i1 < len(atoms) else turn["end"],
                         "trigger": atoms[i0].get("uuid"),
                         "atoms": atoms[i0:i1]})
        if starts[0] > 0:   # leading atoms before the first input attach to the first segment
            lead = atoms[:starts[0]]
            segs[0]["atoms"] = lead + segs[0]["atoms"]
            segs[0]["t"] = turn["t"]
    for seg in segs:        # id last: after the leading-attach may have moved seg[0]'s t/atoms
        seg["id"] = _segment_id(rompuuid, seg["t"], seg["atoms"], seg["trigger"])
    return segs


# ── settle-time SEAM split (plans/segment-regrowth.md): when a goal settles while its segment is
# still growing, the post-settle tail becomes its OWN segment so the planner can see it. The split
# primitive lives here (pure over a segment); WHICH segments split — ownership via the goal store's
# placements — is the judge's call (jd.apply_seams), keeping this layer store-free.
SEAM_PROSE_FLOOR = 80                     # tail "real work" = a tool_use atom or assistant prose past this


def _seam_real_work(atoms):
    """True if `atoms` hold REAL work — any assistant tool_use, or assistant prose ≥ SEAM_PROSE_FLOOR
    chars (above connective stubs). The event condition that gates a seam split: post-settle wrap-up
    chatter never mints a noise segment."""
    for a in atoms:
        if a.get("type") != "assistant":
            continue
        blocks = _content(a.get("message"))
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                return True
        if len(_text_of(blocks)) >= SEAM_PROSE_FLOOR:
            return True
    return False


def split_segment(seg, t):
    """(head, tail) or None — split `seg` after the last atom at/before wall-clock `t` (a goal's settle
    moment, plans/segment-regrowth.md). None unless BOTH sides are non-empty and the tail holds real
    work (_seam_real_work). The head keeps the original id (its t + trigger text are unchanged, so an
    existing placement still matches); the tail is trigger-less, `seam`-flagged, with a STABLE id from
    its own first atom — every pass re-derives the same split, so placement idempotency holds."""
    atoms = seg.get("atoms") or []
    head_a = [a for a in atoms if a.get("t", 0) <= t]
    tail_a = [a for a in atoms if a.get("t", 0) > t]
    if not head_a or not tail_a or not _seam_real_work(tail_a):
        return None
    rompuuid = atoms[0].get("session_id", "")
    head = dict(seg, atoms=head_a, end=tail_a[0]["t"])
    tail = {"t": tail_a[0]["t"], "end": seg["end"], "trigger": None, "atoms": tail_a, "seam": True,
            "id": _segment_id(rompuuid, tail_a[0]["t"], tail_a, None)}
    return head, tail


# ═════════════════════════ assembly ═════════════════════════
def _load_postal_index(postal_log):
    """{msg-id -> sender rompUuid} from timeline/messages.jsonl (`from_id` is the sender's
    anchor sid). Accepts a path or an in-memory list of rows (tests)."""
    idx = {}
    # append-incremental like the transcripts (the log is append-only; a rewrite fails the reader's
    # tail guard and degrades to a full re-read) — the full _read_jsonl here was a measured 22ms on
    # EVERY parse_session call against a 4MB live log
    rows = postal_log if isinstance(postal_log, list) else _read_jsonl_incremental(postal_log or MESSAGES_LOG)
    for o in rows:
        if not isinstance(o, dict):
            continue
        mid, frm = o.get("id"), o.get("from_id")
        if mid and frm and mid not in idx:
            idx[mid] = frm
    return idx


def _load_states(states):
    if states is None:
        return []
    # incremental for the same reason as the postal index above: states/<sid>.jsonl is append-only
    return list(states) if isinstance(states, list) else list(_read_jsonl_incremental(states))


def resume_fork_links(srows):
    """{to_fsid: from_fsid} from states/ resumeFork rows — the kernel's exact record that a resume of
    a machine-cut turn FORKED the transcript (fresh head) instead of continuing the chain. See
    FileAdapter._stitch_resume_forks for why the parse needs it; the kernel's episode-boundary check
    reads the same rows (jd.resume_lineage) to keep the fork from being processed as a /clear. Last
    row wins per fork head; malformed rows are skipped (a missing lineage simply keeps the old drop)."""
    links = {}
    for r in srows or []:
        rf = r.get("resumeFork")
        if isinstance(rf, dict) and rf.get("from") and rf.get("to"):
            links[str(rf["to"])] = str(rf["from"])
    return links


def _lineage_closure(leaf_path, candidate_files, links):
    """The candidate-file set CLOSED over the recorded resume lineage: a fork chain across several
    restarts needs every resumed-from file present for the stitched walk to cross (a caller's anchor
    covers only one hop). Shared by parse_session AND chain_membership so the exported membership
    predicate reads the exact same file set the display parse does. From-files are frozen after
    their fork (the CLI writes only the new file), so callers' cache keys stay honest."""
    if not links:
        return list(candidate_files)
    have = {Path(f).stem for f in candidate_files}
    stem, hops = Path(leaf_path).stem, 0
    candidate_files = list(candidate_files)
    while stem in links and hops < 16:
        stem = links[stem]
        hops += 1
        fp = Path(leaf_path).with_name(stem + ".jsonl")
        if stem not in have and fp.exists():
            candidate_files.append(str(fp))
            have.add(stem)
    return candidate_files


def chain_membership(leaf_path, candidate_files=None, states=None, leaf_override=None):
    """THE exported chain-membership fact — {"kept", "rewind", "clear", "broken", "eclipsed"}
    uuid sets, built from the DISPLAY parse's exact inputs (resume links + lineage closure +
    leaf_override = the kernel's pending bare-rollback cut) so it can never disagree with what the
    user sees. This is the one predicate every rewind-cleanup consumer must use (goal sweeps,
    mint-time stand-downs, the dead-branch reconciliation): before it was exported, four partial
    hand-rolled twins of this walk disagreed on resume forks, pending cuts and broken chains
    (2026-08-17).

    "rewind" is the ONLY set that ever justifies sweeping a goal: "clear" branches are /clear
    jurisdiction (the episode machinery settles those cards), "broken" chains are kept by design,
    "eclipsed" chains are KEPT content a machine spur abandoned (never a user gesture — T209;
    narrowed by _select_eclipsed_chains to the fork's one machine-orphaned reply chain when one
    qualifies, else per its terminal rules, so nothing sweeps a reply nobody abandoned and
    nothing keeps a sibling the walk drops on-spine),
    and a uuid in NO set is unprovable (a synthetic orphan:<t> salvage id, a cross-file uuid whose
    file is outside the lineage, a legacy None) — callers must treat unknown as NOT abandoned.
    Caveat (resume-fork stitch shape): a recorded fork's fresh head is re-pointed at the from-file's
    LAST record — if that tip was itself an abandoned tail, the stitch makes it active again; this
    predicate follows the stitch exactly as the display parse does (kept semantics, by design)."""
    leaf_path = Path(leaf_path)
    if candidate_files is None:
        candidate_files = [str(leaf_path)]
    links = resume_fork_links(_load_states(states))
    candidate_files = _lineage_closure(leaf_path, candidate_files, links)
    adapter = FileAdapter(candidate_files, leaf_path, leaf_override=leaf_override, resume_links=links)
    active = adapter.active_path()
    verdicts = adapter.chain_verdicts(active)
    # kept, derived from the verdicts already in hand — BY DEFINITION the same set kept_uuids
    # computes (active ∪ broken ∪ eclipsed; see its docstring: "derived from chain_verdicts — one
    # implementation"), without paying the graph walk a second time inside it. The hold view
    # re-asks this on every build of a held session, so the walk count matters there.
    out = {"kept": set(active) | {u for u, v in verdicts.items() if v in ("broken", "eclipsed")},
           "rewind": set(), "clear": set(), "broken": set(), "eclipsed": set()}
    for u, v in verdicts.items():
        if v != "active":
            out[v].add(u)
    return out


# ═════════════════════ assembly cache: fold appends, don't re-emit the world ═════════════════════
# (2026-09-01.) _read_jsonl_incremental already amortizes bytes -> records; what still re-ran in full
# on every append was everything ABOVE the records. Measured on the live corpus: the emit layer is
# 0.2-0.5s per parse at 20-130MB transcripts, while the graph semantics (active_path, chain_verdicts
# incl. the eclipsed probe, kept_uuids) cost 2-6ms even at 28k records. So a fold RE-DERIVES all
# graph semantics exactly, every time, and folds ONLY the emit: appended records run through the
# same _prepass/_emit_fold/_absorbed code with the carried emit state. Gates demote anything the
# carry cannot provably absorb to a full parse — a fold never guesses; wrong means full, never
# divergent:
#   - candidates / resume links / sdk_human changed, or a pending cut is armed        -> full/bypass
#   - a non-leaf candidate changed at all, or the leaf did anything but grow          -> full
#   - the new leaf does not descend from the old leaf through the delta               -> full
#   - the delta contains: a compact boundary or summary; a uuid already in the graph
#     or among its recorded dangling parent targets; a promptId already seen; a Skill
#     tool_use an old payload record already references; an unparseable or
#     watermark-regressing conversational timestamp                                   -> full
#   - after the fold's graph recompute, ANY old record's kept-membership changed      -> full
# Postal is the one input with no gate, on the log's own invariant: rows are append-only and
# resolution is first-wins per id, so an author can only go from marker-missed to resolved — and
# _asm_heal re-authors exactly those atoms each visit until they resolve.
# The entry is mutated only under its per-key lock, held across gates + fold + commit — a
# deliberate departure from _JSONL_CACHE_LOCK's cheap-ops-only discipline, because this cache's
# hits MUTATE the entry (two concurrent parses of one path must serialize or they double-fold the
# same delta). Served atom lists are copy-on-fold and served atoms are per-serve shallow copies,
# so callers pop _seq / rebuild turns as they always have without reaching into the cache. Any
# exception on the fold path logs ONCE and falls back to plain full parses — loud, never wrong.
_ASM_CACHE = {}          # key -> entry; dict order = LRU, hits reinsert. Evicts oldest-used one at
#                          a time, never clear-at-cap (the _JSONL_CACHE 2026-08-15 lesson: a
#                          wholesale clear nukes the hot entries and every push re-parses in full)
_ASM_CACHE_MAX = 256
_ASM_LOCK = threading.Lock()       # guards the cache dict + the per-key lock registry only
_ASM_KEYLOCKS = {}                 # key -> Lock; never pruned (a Lock is tiny, and swapping a
#                                    key's lock mid-flight would let two folds interleave)
_ASM_STATS = {"full": 0, "fold": 0, "serve": 0, "bypass": 0, "fallback": 0}   # observability + tests
_ASM_WARNED = [False]
_TS_REPAIR_NOTED = set()     # file stems already warned about a garbled stamp — once per file;
#                              the cap CLEARS and re-arms (an occasional repeat note beats silence)
_TS_REPAIRED_SEEN = set()    # record uuids already counted in ts-repair — distinct corruption,
#                              not parse volume; races only overcount by one, acceptable


def _asm_demote(reason):
    """Count WHY a fold demoted to a full parse (g:<reason> in _ASM_STATS) and return None —
    the hit-rate diagnosis this cache lives or dies by, in prod and in the corpus replay."""
    k = "g:" + reason
    _ASM_STATS[k] = _ASM_STATS.get(k, 0) + 1
    return None


def _asm_key_lock(key):
    with _ASM_LOCK:
        lk = _ASM_KEYLOCKS.get(key)
        if lk is None:
            lk = _ASM_KEYLOCKS[key] = threading.Lock()
        return lk


def _asm_serve(entry):
    """A caller-owned copy of the entry's emit outputs: fresh top-level atom dicts (parse_session
    pops _seq and the turn builder sorts in place; the pristine list keeps both), a landed copy,
    and no pending cut (cut parses never reach the cache)."""
    return [dict(a) for a in entry["atoms"]], set(entry["landed"]), None


def _asm_full(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human):
    """Full parse + a fresh cache entry — the same steps atoms() runs, inlined so the entry keeps
    the carry (st) the emit actually used; later folds continue from it."""
    ad = FileAdapter(candidate_files, leaf_path, resume_links=links)
    ad.sdk_human = sdk_human
    st = _emit_state()
    kept = ad.kept_uuids(ad.active_path())
    order = _chrono(ad, kept)
    ad._prepass(order, st)
    atoms = list(ad._emit_fold(order, st, rompuuid, postal_index))
    atoms += ad._absorbed(ad.qatts, kept, st, rompuuid, postal_index)
    entry = {"ad": ad, "st": st, "atoms": atoms, "kept": kept,
             "landed": ad.landed_text_uuids(),
             "cands": tuple(str(f) for f in candidate_files), "links": dict(links or {}),
             "recs": dict(ad._src), "n_qatts": len(ad.qatts)}
    with _ASM_LOCK:
        _ASM_CACHE.pop(key, None)
        while len(_ASM_CACHE) >= _ASM_CACHE_MAX:
            _ASM_CACHE.pop(next(iter(_ASM_CACHE)))   # oldest-used first; hot entries survive floods
        _ASM_CACHE[key] = entry
    _ASM_STATS["full"] += 1
    return _asm_serve(entry)


def _asm_gates(entry, leaf_path, candidate_files, links):
    """None -> full parse. Else (delta, leaf_records): the leaf's appended records (possibly
    empty) and the records list they came from, gate-checked per the block comment above."""
    if entry["cands"] != tuple(str(f) for f in candidate_files) or \
            entry["links"] != dict(links or {}):
        return _asm_demote("inputs")
    ad = entry["ad"]
    leaf_stem = Path(leaf_path).stem
    files = [f for f in candidate_files if Path(f).stem != leaf_stem] + [Path(leaf_path)]
    delta = leaf_recs = None
    for fp in files:
        recs = _read_jsonl_incremental(fp)
        old = entry["recs"].get(str(fp))
        if old is None:
            return _asm_demote("recs-gone")
        if Path(fp).stem != leaf_stem:
            if recs is not old:
                return _asm_demote("nonleaf")   # changed — or its reader slot was evicted and
            continue             #  rebuilt; identity is the proof, its absence means full parse
        leaf_recs = recs
        if recs is old:
            delta = []
        elif len(recs) > len(old) > 0 and recs[len(old) - 1] is old[-1]:
            delta = recs[len(old):]   # the reader's contract: a grown file returns the SAME
            #                           prefix objects + the parsed new bytes
        else:
            return _asm_demote("rewrite")
    if delta is None:
        return _asm_demote("no-leaf-slot")
    if not delta:
        return [], leaf_recs
    max_ppt = entry["st"]["max_ppt"]
    old_leaf = ad._pristine_leaf
    if old_leaf is None:
        return _asm_demote("empty-graph")
    parent_d, new_leaf = {}, old_leaf
    for r in delta:
        t, u = r.get("type"), r.get("uuid")
        if u:
            if u in ad.by_uuid or u in ad.dangling:
                return _asm_demote("uuid-known")   # a re-write rebinds last-write-wins index
                #                  state; a resurrected dangling target rebinds repaired stitches
            p = r.get("parentUuid") or r.get("logicalParentUuid")
            parent_d[u] = None if p == u else p
            new_leaf = u
        if t == "system" and r.get("subtype") == "compact_boundary":
            return _asm_demote("boundary")   # arms the restore dedup and can re-seat adoptions
        if r.get("isCompactSummary") is True:
            return _asm_demote("summary")    # attaches to boundaries in the chronological pre-pass
        if t == "user" and r.get("promptId") and r["promptId"] in ad.prompt_ids:
            # A repeated promptId is ROUTINE — every record of a turn wears its prompt's id, so
            # tool results repeat it on nearly every append (measured: this gate, unshaped,
            # demoted 1863 of 1869 bursts on a live 46MB replay). Only two shapes can
            # re-classify OLD atoms: a command-wrapper-family record (it grows the twins map
            # retroactively) and any record wearing an adoptable boundary's episode pid (it can
            # re-seat the adopted card's splice without changing kept — invisible to the
            # invariance check). Everything else folds: the carried twins map serves the DELTA
            # record's own classification record-locally.
            if r["promptId"] in ad.boundary_pids or \
                    CMD_WRAP_RE.match(_text_of(_content(r.get("message"))) or ""):
                return _asm_demote("promptid")
        if t == "assistant":
            for b in _content(r.get("message")) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and \
                        b.get("name") == "Skill" and b.get("id") in ad.src_tool_links:
                    return _asm_demote("skill-link")   # an already-ingested payload references it
        if t in ("user", "assistant"):
            ts = parse_z(r.get("timestamp"))
            if ts is None or ts < max_ppt:
                return _asm_demote("ts")   # the carry is valid only for a delta sorting at-or-
                #                            after everything folded (the pre-pass sorts None first)
    # Leaf descent: the new file-leaf must chain back to the old one THROUGH the delta — an
    # api_error spur re-parent, a rewind, and a /clear fork all fail here. parent_d.pop doubles
    # as the cycle guard; a delta with no uuid-bearing record passes trivially (leaf unmoved).
    cur, hops = new_leaf, 0
    while cur != old_leaf:
        if cur is None or cur not in parent_d or hops > len(delta) + 1:
            return _asm_demote("descent")
        cur = parent_d.pop(cur)
        hops += 1
    return delta, leaf_recs


def _asm_heal(entry, rompuuid, postal_index):
    """Re-author atoms whose postal author is still PROVISIONAL (_author_final) against the
    current index: replace on any change so serves match a full parse NOW, and retire the miss
    only once the trailing marker resolves — a record whose quoted earlier marker resolved first
    would otherwise latch the wrong sender forever (review catch, 2026-09-01). Record-local by
    design: author_of reads only the record's text + the index. The qatt pick mirrors
    _absorbed's kept filter exactly — the first same-key twin can sit on a rewound branch, and
    healing from it would flip the atom's identity to the abandoned record (same review)."""
    st, ad = entry["st"], entry["ad"]
    if st["postal_miss_rec"]:
        pos = {a.get("uuid"): i for i, a in enumerate(entry["atoms"]) if a.get("uuid")}
        done = set()
        for u in list(st["postal_miss_rec"]):
            i = pos.get(u)
            if i is None:
                done.add(u)      # no atom carries it any more (entry rebuilt around it) — retire
                continue
            a2 = next(iter(ad._emit_fold([u], st, rompuuid, postal_index)), None)
            if a2 is None:
                done.add(u)
                continue
            if a2 != entry["atoms"][i]:
                entry["atoms"][i] = a2   # in place is safe: serves copy, and we hold the key lock
            if _author_final(a2.get("author"), _text_of(_content(a2.get("message")))):
                done.add(u)
        st["postal_miss_rec"] -= done
    if st["postal_miss_att"]:
        kept = entry["kept"]
        for i, a in enumerate(entry["atoms"]):
            if not a.get("absorbed"):
                continue
            key = (a["t"], " ".join(_text_of(_content(a.get("message"))).split()))
            if key not in st["postal_miss_att"]:
                continue
            q = next((q for q in ad.qatts if q["ts"] == key[0]
                      and " ".join(q["text"].split()) == key[1]
                      and (q["uuid"] is None or q["uuid"] in kept)), None)
            if q is None:
                st["postal_miss_att"].discard(key)
                continue
            a2 = ad._absorbed_atom(q["text"], q["ts"], q["seq"], q["uuid"], rompuuid, postal_index)
            if a2 != a:
                entry["atoms"][i] = a2
            if _author_final(a2.get("author"), q["text"]):
                st["postal_miss_att"].discard(key)


def _asm_fold(entry, delta, leaf_recs, leaf_key, leaf_stem, rompuuid, postal_index):
    """Fold `delta` into the entry: extend the PRISTINE graph, re-derive the repair passes on a
    working copy (fold state is literally the fresh-__init__ computation — never a latched earlier
    repair), recompute ALL graph verdicts exactly, then emit only the appended kept records with
    the carried state. None -> the kept-invariance check failed; caller demotes to full."""
    ad = entry["ad"]
    ad.parent_of = ad._pristine_parent
    ad.leaf_uuid = ad._pristine_leaf
    n_qatts = entry["n_qatts"]
    ad._ingest(delta, leaf_stem, True)
    ad._pristine_parent = ad.parent_of
    ad._pristine_leaf = ad.leaf_uuid
    ad.parent_of = dict(ad._pristine_parent)
    ad._run_graph_passes()
    kept = ad.kept_uuids(ad.active_path())
    dset = {r.get("uuid") for r in delta if r.get("uuid")}
    if kept - dset != entry["kept"]:
        return _asm_demote("kept")   # ancestry moved under an old record — the fold cannot
        #                              re-emit history; only a full parse can
    st = entry["st"]
    order = _chrono(ad, kept & dset)
    ad._prepass(order, st)
    new_atoms = list(ad._emit_fold(order, st, rompuuid, postal_index))
    new_atoms += ad._absorbed(ad.qatts[n_qatts:], kept, st, rompuuid, postal_index)
    for u in dset:
        r = ad.by_uuid.get(u) or {}
        if r.get("type") == "assistant" and _text_of(_content(r.get("message"))).strip():
            entry["landed"].add(u)
    entry["atoms"] = entry["atoms"] + new_atoms   # a NEW list — outstanding serves stay stable
    entry["kept"] = kept
    entry["n_qatts"] = len(ad.qatts)
    entry["recs"][leaf_key] = leaf_recs           # commit LAST: a bail above re-slices the same
    ad._src[leaf_key] = leaf_recs                 #  delta next visit and the uuid gate demotes it
    _ASM_STATS["fold"] += 1
    return _asm_serve(entry)


def _assemble(leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, leaf_override,
              mode_out=None):
    """(atoms, landed, cut_t) for parse_session — atoms are caller-owned copies. The one entry
    point that decides bypass vs serve vs fold vs full; see the block comment above. `mode_out`,
    when a list, receives the path taken ("serve" | "fold" | "full" | "bypass" | "fallback"): the
    kernel's chat-payload fold (issue 903) keys the validity of its cached prefix on it — a serve or
    a fold leaves every previously emitted atom in place (heals aside, which ride the postal index),
    while a full parse may have re-emitted history. Kept OUT of the returned tree on purpose: the
    golden fixtures and the fold==full replay compare that tree byte for byte."""
    def _mode(m):
        if mode_out is not None:
            mode_out.append(m)
    if leaf_override:
        # A pending cut changes the walk anchor and is transient: always a plain full parse,
        # never cached — a cut parse's truncated emit state must not seed later folds.
        _ASM_STATS["bypass"] += 1
        _mode("bypass")
        ad = FileAdapter(candidate_files, leaf_path, leaf_override=leaf_override, resume_links=links)
        ad.sdk_human = sdk_human
        cut_t = None
        if leaf_override in ad.by_uuid:
            cut_t = ad.ts_of.get(leaf_override)
        return ad.atoms(rompuuid, postal_index), ad.landed_text_uuids(), cut_t
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    try:
        with _asm_key_lock(key):
            with _ASM_LOCK:
                entry = _ASM_CACHE.get(key)
                if entry is not None:
                    _ASM_CACHE.pop(key, None)
                    _ASM_CACHE[key] = entry       # a served entry is a USED entry (LRU touch)
            if entry is not None:
                got = _asm_gates(entry, leaf_path, candidate_files, links)
                if got is not None:
                    delta, leaf_recs = got
                    _asm_heal(entry, rompuuid, postal_index)
                    if not delta:
                        _ASM_STATS["serve"] += 1
                        _mode("serve")
                        return _asm_serve(entry)
                    served = _asm_fold(entry, delta, leaf_recs, str(leaf_path),
                                       Path(leaf_path).stem, rompuuid, postal_index)
                    if served is not None:
                        _mode("fold")
                        return served
                with _ASM_LOCK:                   # gate/invariance demotion: the entry is stale
                    _ASM_CACHE.pop(key, None)
            _mode("full")
            return _asm_full(key, leaf_path, candidate_files, links, rompuuid,
                             postal_index, sdk_human)
    except Exception as e:
        _ASM_STATS["fallback"] += 1
        _mode("fallback")
        if not _ASM_WARNED[0] or _ASM_STATS["fallback"] in (10, 100, 1000, 10000):
            _ASM_WARNED[0] = True    # once, then at count milestones — a PERSISTENT fold bug
            #                          must not hide behind a single line in an old log
            print("romp-event-model: assembly fold failed (%r) — serving plain full parses "
                  "(stats %r); the fallback is correct but slow, fix the fold"
                  % (e, dict(_ASM_STATS)), file=sys.stderr)
        with _ASM_LOCK:
            _ASM_CACHE.pop(key, None)
        ad = FileAdapter(candidate_files, leaf_path, resume_links=links)
        ad.sdk_human = sdk_human
        return ad.atoms(rompuuid, postal_index), ad.landed_text_uuids(), None


def parse_session(leaf_path, rompuuid=None, name=None, color="#888888", dir=None,
                  candidate_files=None, states=None, postal_log=None, now=None, sdk_human=False,
                  leaf_override=None, asm_mode_out=None):
    """Build one session's Session -> Turn -> Atom tree from the on-disk transcript graph.

    leaf_path        the newest (leaf) transcript file; the walk's start pointer.
    rompuuid         stable session identity (binds everything). Real runs resolve it from
                     names/<sid> (the anchor sid); defaults to the leaf file stem for --test.
    candidate_files  the session's transcript files the resume walk may cross into.
                     Defaults to JUST [leaf_path] — a safe single-file parse. Cross-file
                     resume requires the caller to pass the explicit session file set;
                     we deliberately do NOT glob the project dir (that would read every
                     unrelated transcript in it). Session->files resolution is a
                     higher-layer concern, not the parser's.
    states           states/<sid>.jsonl path or rows -> idle atoms.
    postal_log       timeline/messages.jsonl path or rows -> peer rompUuid.
    leaf_override    start the walk at this record instead of the file's last (a PENDING
                     bare rollback — the kernel's pending_cut); ignored if absent from the graph.
    asm_mode_out     a list, when given, that receives the assembly path taken (see _assemble);
                     never part of the returned tree.
    """
    leaf_path = Path(leaf_path)
    if dir is None:
        dir = str(leaf_path.parent)
    if rompuuid is None:
        rompuuid = leaf_path.stem
    if candidate_files is None:
        candidate_files = [str(leaf_path)]
    postal_index = _load_postal_index(postal_log)
    _srows = _load_states(states)
    links = resume_fork_links(_srows)
    # The lineage closure joins the candidate set: a fork chain across several restarts needs
    # every resumed-from file present for the stitched walk to cross (the caller's anchor covers
    # only one hop). Resolved HERE so both parses (the judge's and the kernel's) inherit it from
    # the one states plumbing they already share; chain_membership shares the same helper. The
    # callers' cache keys — candidate files + the states file, whose mtime moves when a lineage
    # row lands — stay honest without knowing about these.
    candidate_files = _lineage_closure(leaf_path, candidate_files, links)
    # The assembly cache serves/folds/rebuilds as the gates decide — a streamed append folds only
    # the new records through the shared emit code; everything else is a full parse. atoms/landed
    # come back caller-owned (fresh top-level dicts), so the mutations below never reach the cache.
    atoms, landed, cut_t = _assemble(leaf_path, candidate_files, links, rompuuid,
                                     postal_index, sdk_human, leaf_override, mode_out=asm_mode_out)
    orphans = synthesize_orphans(_srows, atoms, landed_text_uuids=landed)
    #                                            # salvaged replies FIRST: they are real atoms the turn
    #                                              grouping must absorb (idle spans overlay afterwards)
    # A salvaged reply has NO position in the transcript graph — that absence is the very thing the
    # marker exists to paper over — so the leaf_override walk cannot drop it the way it drops the
    # abandoned chain. Unfiltered, deleting a message left its reply standing alone in the chat: the
    # prompt vanished (it was on the chain) while the answer stayed (it was re-synthesized from
    # states/), which reads as though the delete half-worked (the user 2026-08-01). Filter on the CUT
    # RECORD's own timestamp — an exact event, not a window — since time is the only ordering a
    # graph-less atom has. Idle spans are deliberately NOT filtered: they describe the session's
    # working state NOW (an open span runs to `now`), not conversation content.
    if cut_t:
        orphans = [a for a in orphans if a["t"] <= cut_t]
    atoms += orphans
    atoms += synthesize_idle(_srows, atoms, now)
    turns = segment_turns(atoms, rompuuid)
    for turn in turns:
        for a in turn["atoms"]:
            a.pop("_seq", None)
        turn_keys = {"id": turn["id"], "trigger": turn["trigger"], "t": turn["t"],
                     "end": turn["end"], "ended": turn["ended"], "atoms": turn["atoms"]}
        turn.clear()
        turn.update(turn_keys)
    return {"rompUuid": rompuuid, "name": name or rompuuid, "dir": dir,
            "color": color, "leafFsid": leaf_path.stem, "turns": turns,
            # for the kernel chat build's own marker interleave: its dedup reads the KEPT turns
            # only, so without this a marker whose reply landed on an abandoned branch would
            # ghost back through that second door (sorted → deterministic payloads).
            "landedTextUuids": sorted(landed)}


def task_store_plan(fsid):
    """The agent's to-do list read from Claude Code's LIVE task store (<config>/tasks/<fsid>/<N>.json,
    honoring $CLAUDE_CONFIG_DIR) — the AUTHORITATIVE state TaskList/TaskGet read, updated by EVERY
    writer including subagents. The transcript fold (declared_plan below) is a lossy reconstruction:
    it misses a completion whose record fell off the transcript's live chain — an api-error retry
    forks the parent graph and the abandoned branch keeps the TaskUpdate that actually RAN (store
    updated, transcript forgot), leaving a mirror card phantom-open that re-mints itself after every
    clear (the 2026-07-09 g204 loop). Same item shape as declared_plan: [{key, text, activeForm,
    status}], ordered by numeric id. Returns None when the fsid has no store dir — a session that
    never declared a plan there (the caller may fall back to the fold). Raises OSError when the dir
    EXISTS but can't be listed: that is the authoritative source failing, and the caller must surface
    it loudly, never silently fold (repo policy). A single corrupt item file is skipped."""
    if not fsid:
        return None
    d = Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")) / "tasks" / fsid
    if not d.is_dir():
        return None
    items = []
    for n in os.listdir(d):                                # raises OSError → the caller surfaces it
        if not n.endswith(".json"):
            continue
        try:
            t = json.loads((d / n).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(t, dict):
            continue
        key = str(t.get("id") or n.rsplit(".", 1)[0])
        af = t.get("activeForm")
        items.append({"key": key, "text": str(t.get("subject") or ""),
                      "activeForm": str(af) if af else None,
                      "status": str(t.get("status") or "pending")})
    items.sort(key=lambda t: (0, int(t["key"])) if t["key"].isdigit() else (1, t["key"]))
    return items


def declared_plan(session):
    """The agent's OWN to-do list (Claude Code's Task tool) folded into ordered items
    [{key, text, activeForm, status}] — the FALLBACK behind task_store_plan for a session with no
    live task store, so downstream (the judge's plan-sync) sees a generic 'declared plan' shape
    instead of raw tool calls. Mirrors the kernel's _fold_tasks, with the same blind spots: only the
    MAIN agent's TaskCreate/TaskUpdate calls, and only those on the transcript's live chain.
    `key` is the stable `Task #N` id lifted from TaskCreate's
    result text (a creation-order `cN` fallback if the result is unreadable); `status` rides each
    TaskUpdate. Only TaskCreate/TaskUpdate are folded — plain TodoWrite (no durable ids) is not
    used by romp. Empty list if the session declared no plan."""
    results = {}                                           # tool_use_id → result text (carries 'Task #N')
    for turn in session["turns"]:
        for a in turn["atoms"]:
            if a.get("type") != "user":
                continue
            for b in (a.get("message") or {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    c = b.get("content")
                    results[b["tool_use_id"]] = c if isinstance(c, str) else json.dumps(c)
    tasks, order = {}, 0
    for turn in session["turns"]:
        for a in turn["atoms"]:
            if a.get("type") != "assistant":
                continue
            for b in (a.get("message") or {}).get("content", []) or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "TaskCreate":
                    m = re.search(r"Task #(\d+)", results.get(b.get("id"), "") or "")
                    key = m.group(1) if m else "c%d" % order
                    af = inp.get("activeForm")
                    tasks[key] = {"_order": order, "key": key, "text": str(inp.get("subject") or ""),
                                  "activeForm": str(af) if af else None, "status": "pending"}
                    order += 1
                elif b.get("name") == "TaskUpdate":
                    t = tasks.get(str(inp.get("taskId", "")))
                    if t:
                        t["status"] = str(inp.get("status") or t["status"])
    return sorted(tasks.values(), key=lambda t: t["_order"])


# ───────────────────────── CLI ─────────────────────────
def _hh(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "--:--:--"


def _atom_line(a):
    t = a["type"]
    if t == "idle":
        return "    · idle            %s-%s  (not working)" % (_hh(a["t"]), _hh(a.get("end")))
    if t == "system":
        cm = a.get("compact_metadata") or {}
        return "    · system:%-9s %s  trigger=%s pre_tokens=%s" % (
            a.get("subtype", "?"), _hh(a["t"]), cm.get("trigger"), cm.get("pre_tokens"))
    blocks = _content(a.get("message"))
    kinds = _block_types(blocks)
    if t == "assistant":
        sr = (a.get("message") or {}).get("stop_reason")
        tools = [b.get("name") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        extra = ("tools=" + ",".join(tools)) if tools else ""
        return "    · assistant       %s  %s %s (stop=%s)" % (_hh(a["t"]), "+".join(kinds), extra, sr)
    # user
    author = a.get("author")
    auth = (("peer:" + str(author.get("peer"))) if isinstance(author, dict)
            else (author or "-"))
    snippet = _text_of(blocks)[:48].replace("\n", " ")
    if not snippet and _has_tool_result(blocks):
        snippet = "(tool_result)"
    return "    · user/%-11s %s  %s" % (auth, _hh(a["t"]), snippet)


def _dump(session):
    s = session
    print("Session %s  [%s]  leaf=%s" % (s["name"], s["rompUuid"], s["leafFsid"]))
    print("  dir=%s  color=%s  turns=%d" % (s["dir"], s["color"], len(s["turns"])))
    for i, turn in enumerate(s["turns"], 1):
        trig = turn["trigger"]
        tatom = next((a for a in turn["atoms"] if trig and a.get("uuid") == trig["uuid"]), None)
        tlabel = "autonomous"
        if tatom:
            author = tatom.get("author")
            tlabel = (("peer:" + str(author.get("peer"))) if isinstance(author, dict)
                      else (author or "?")) + " " + repr(_text_of(_content(tatom.get("message")))[:40])
        segs = segments(turn)
        print("\n  Turn %d  [%s-%s]  ended=%s  segments=%d  trigger=%s" % (
            i, _hh(turn["t"]), _hh(turn["end"]), turn["ended"], len(segs), tlabel))
        for a in turn["atoms"]:
            print(_atom_line(a))


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("--test", "--emit"):
        sys.stderr.write("usage: romp-event-model [--test | --emit] <transcript> "
                         "[--rompuuid X] [--states PATH] [--name N]\n")
        sys.exit(2)
    mode, path = args[0], args[1]
    opts = {}
    rest = args[2:]
    for i in range(0, len(rest) - 1, 2):
        opts[rest[i].lstrip("-")] = rest[i + 1]
    states = opts.get("states")
    if states is None:                       # default: states/<leaf-stem>.jsonl if present
        cand = STATES_DIR / (Path(path).stem + ".jsonl")
        states = str(cand) if cand.exists() else None
    session = parse_session(path, rompuuid=opts.get("rompuuid"), name=opts.get("name"),
                            states=states, now=int(time.time()))
    if mode == "--emit":
        sys.stdout.write(json.dumps(session, indent=1, sort_keys=True))
        sys.stdout.write("\n")
    else:
        _dump(session)


if __name__ == "__main__":
    main()
