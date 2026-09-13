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
import array, bisect, collections, copy, gzip, json, os, re, sys, time, hashlib, threading
from datetime import datetime
from pathlib import Path

HOME     = Path.home()
STATE    = Path(os.environ.get("ROMP_STATE_DIR")   # per-kernel state root override (plans/multi-kernel.md)
                or Path(os.environ.get("XDG_STATE_HOME") or str(HOME / ".local/state")) / "romp")
# `or`, not a .get default: an EMPTY XDG_STATE_HOME is unset (the XDG spec, and every shell reader's
# ${XDG_STATE_HOME:-...}). A .get default kept the empty string and made the root the RELATIVE path
# romp under the process cwd, so the kernel and the shell surfaces used different roots.
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")) / "projects"   # per-kernel Claude root (plans/multi-kernel.md phase 2)
NAMES    = STATE / "names"
STATES_DIR   = STATE / "states"
MESSAGES_LOG = STATE / "timeline" / "messages.jsonl"

# A turn ends when the model hands the floor back: stream `end_turn` / `stop_sequence`.
# Mid-turn the model stops with `tool_use` (a tool cycle) — that does NOT end the turn.
END_STOPS = ("end_turn", "stop_sequence")
# The toolUseResult keys a consumer actually reads (Edit's structuredPatch → diffRows,
# AskUserQuestion's answers map → the answered box, the Agent tool's agentId / isAsync → the
# subagent join + the background-launch flag, plans/subagent-transcripts.md). The atom carry is
# GATED on one of these being present: an unconditional dict carry held every Read result's full
# file bytes in the parse cache by reference — ~a fifth of transcript bytes on read-heavy sessions —
# for shapes nothing reads. Widen this set when a new consumer appears; never revert to carry-all.
TUR_CONSUMED_KEYS = frozenset(("answers", "structuredPatch", "agentId", "isAsync"))
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
# ...and the CLI's harness PREAMBLE (Claude Code 2.1.263, the user 2026-09-07): the CLI now puts a fixed
# paragraph AHEAD of the wrapper tag so the MODEL never mistakes an injected turn for its user —
# "[SYSTEM NOTIFICATION - NOT USER INPUT]" before a background task's <task-notification>. The tag no
# longer opens the record, so the tag-anchored test above stopped seeing these as harness-injected and
# every background agent's completion authored 'human' → the blue "you typed this" bubble, wearing the
# preamble paragraph as its text, and a junk goal per completion. Authorship is FIELD-FIRST now (author_of
# reads the record's own `origin.kind`); this text shape is the fallback for a record that lacks the field
# (an older CLI, the live stream's echo, a queued copy). Matched as the record's OPENING line only, like
# the tags — a prompt that merely quotes the sentinel is still the user's.
SYSTEM_WRAPPER_RE = re.compile(r"^\s*(?:\[SYSTEM NOTIFICATION - NOT USER INPUT\]|<(?:task-notification|system-reminder)\b)")
# A scheduled task's FIRED PROMPT (origin.subkind "scheduled-trigger") wears its own preamble. Unlike a
# notification it IS a prompt — the work that follows is real and must open a turn — so it authors 'sdk'
# (a programmatic prompt: opens a turn, never the human's blue bubble), not 'system'.
SCHEDULED_PREAMBLE_RE = re.compile(r"^\s*\[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT\]")
# The preamble PARAGRAPH, wherever it sits in an injected record's text (the sentinel line and the
# explanatory lines that run to the next blank line) — what strip_harness_preamble lifts out of the shown
# text into the card's fold. Only ever applied to a record already classified as NOT the human's.
HARNESS_PREAMBLE_PARA_RE = re.compile(
    r"\[(?:SYSTEM NOTIFICATION - NOT USER INPUT|SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT)\][^\n]*"
    r"(?:\n(?!\s*\n)[^\n]*)*")
# Claude Code's NATIVE teammate/agent-message channel — one agent messages another, DISTINCT from romp's
# own postal bus (no romp-msg-id). It's delivered as a promptSource "sdk" user record whose text is a
# <prompt> wrapper: "Another Claude session sent a message:" + one or more <teammate-message
# teammate_id="…" color="…" [summary="…"]>body</teammate-message> blocks + a fixed "permission laundering"
# boilerplate. We recognize it so the chat renders it as its OWN collapsed card, not a blue "you typed
# this" bubble (the user 2026-07-05: idle_notification coordination JSON showed as a human message).
# Anchored at the START (optionally inside a one-level <prompt>/<unit> wrapper) so it matches a real
# DELIVERY and NOT a conversation SUMMARY that merely quotes one ("<turn>\nUSER ASKED: Another Claude…").
# Three text shapes, all the CLI's own fixed lead-ins (2.1.263 added the mid-turn forms and the
# <cross-session-message from="…"> envelope its cross-session SendMessage delivers): matched by that lead-in,
# never by the body. Field-first still wins — a record stamped origin.kind "peer" authors 'teammate'
# whatever its text (author_of).
TEAMMATE_MSG_RE = re.compile(r"^\s*(?:<\w+>\s*)?(?:(?:Another Claude session|A peer session) sent a message"
                             r"(?: while you were working)?:|<cross-session-message\b)", re.I)
TEAMMATE_BLOCK_RE = re.compile(r"<teammate-message\b([^>]*)>(.*?)</teammate-message>", re.S)
CROSS_SESSION_BLOCK_RE = re.compile(r"<cross-session-message\b([^>]*)>(.*?)</cross-session-message>", re.S)
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
# The harness's OWN skill load (the user 2026-09-10, confused by feed cards titled after a skill their worker
# sessions never asked for): when the CLI loads a skill for the model by itself (its orchestration mode loads
# the workflow-authoring reference with the prompt whenever that mode is on), it writes the command wrapper
# as an isMeta user record with a BARE name and a <skill-format> tag and NO arguments slot, the three tags and
# nothing else, and the skill's markdown follows in its own isMeta record. Nobody typed it: a typed command or
# skill carries its leading slash ("/jld", "/compact") in both wrapper orders, and a typed skill in the new
# format carries the same <skill-format> tag with its <command-args>, so the tag alone tells nothing and the
# three together are the shape (the arguments slot is absent only when nothing was typed at all). The command branch read the bare
# wrapper as a typed "/workflow-authoring": a human command atom that opened a segment of its own right
# after the real prompt, so the work that answered the prompt was filed under a card titled from the skill's
# name, anchored on a record the feed took for the user's words. It is harness noise of the same class as the
# other wrappers: no atom and no twin, and the prompt it rode in on keeps its segment and its work. The pre-pass
# records each one it skips on the carry, and the parse reports them as session["skillLoads"] for the judge's
# stamp on the tops older stores minted from them.
SKILL_FORMAT_RE = re.compile(r"<skill-format>")


def is_skill_load_wrapper(text):
    """True for the harness's own skill-load wrapper: a record that begins with a command wrapper, carries a
    <skill-format> tag, has NO <command-args> slot and names its command WITHOUT a leading slash. A typed
    invocation has the slash, and one with anything typed after the name has the slot; a bare name with a
    slot (the shape the echo-retire test guards) stays a command."""
    if not text or not CMD_WRAP_RE.match(text) or not SKILL_FORMAT_RE.search(text) or COMMAND_ARGS_RE.search(text):
        return False
    m = COMMAND_NAME_ANY_RE.search(text)
    return bool(m) and not m.group(1).strip().startswith("/")


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


_WF_META_DESC_RE = re.compile(r"\b(description|name)\s*:\s*(['\"])(.*?)\2", re.S)


def _meta_literal(script):
    """The text inside a Workflow script's FIRST `export const meta = {...}` literal, matched by brace depth (a
    nested object or array inside meta never ends the scan early); '' when none."""
    src = str(script or "")[:8000]
    m = re.search(r"export\s+const\s+meta\s*=\s*\{", src)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(src) and depth:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.end():i - 1] if depth == 0 else src[m.end():]


def _launch_desc(name, inp):
    """The dispatch's OWN words at launch time: an Agent/Task's description, a Workflow's meta description
    (else its meta name) read off the script's meta literal alone (never a schema field's or a prompt's quoted
    description later in the script), else ''. Kept on the task row as `launchDesc` and never overwritten by
    the completion notification, whose summary replaces `summary` when the run ends (T319: the feed matches a
    session-started goal to the launch that produced it on these words)."""
    inp = inp if isinstance(inp, dict) else {}
    d = str(inp.get("description") or "").strip()
    if not d and name == "Workflow":
        lit = _meta_literal(inp.get("script"))
        meta = {m.group(1): m.group(3) for m in _WF_META_DESC_RE.finditer(lit)} if lit else {}
        d = str(meta.get("description") or meta.get("name") or "").strip()
        if not d and inp.get("scriptPath"):
            d = os.path.splitext(os.path.basename(str(inp["scriptPath"])))[0]
    return " ".join(d.split())[:200]


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
    # result: an AGENT's closing message (the <result> body) — the report the parent's Agent tool head
    # shows once a background agent has come to rest (plans/subagent-transcripts.md); a command's
    # notification carries none. Capped like the chat's own tool output.
    return {"status": (st or "completed").lower(), "has_status": bool(st), "summary": fld("summary"),
            "output_file": fld("output-file"), "tool_use_id": fld("tool-use-id"),
            "result": fld("result")[:_RESULT_CAP]}


# A background agent's <result> text kept on its scan row (the want_all view) — the same 16000-char cap
# build_session puts on every tool's output, so the report fold and the scan row can never disagree.
_RESULT_CAP = 16000


# A non-persistent Monitor records its own lifetime ceiling at launch (timeout_ms — the harness kills
# it at the deadline), so a scan row still "running" past deadline+grace is a record whose terminal
# notification can never arrive (the CLI died with the monitor out, and the transcript never learns).
# Consumers apply this with THEIR now — baking it into the scan would freeze inside the mtime caches,
# which an idle transcript never busts. The grace absorbs kill/notify latency.
def _bg_expiry_t(task, grace=120.0):
    """The instant a scan row starts reading as expired: deadline + grace, or None for a row with no
    deadline (it never expires by the clock). One definition for the predicate below and for the judge
    gate's clock input (judge._settle_not_before), so the two cannot drift."""
    dl = (task or {}).get("deadline")
    if not dl:
        return None
    if (task or {}).get("deadlineSrc") == "hook":
        grace = 5.0   # a hook-recorded deadline is the harness's own kill moment (Monitor's
        #               required timeout_ms, journaled at launch) — exact, so only clock skew
    return dl + grace


def _bg_expired(task, now, grace=120.0):
    x = _bg_expiry_t(task, grace)
    return x is not None and now > x


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
            if note.get("result"):         # an agent's closing message → the Agent head's report fold
                tasks[tid]["result"] = note["result"]
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
                    "launchDesc": _launch_desc(b.get("name"), inp),
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
                              "launchDesc": _launch_desc(b.get("name"), inp),
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
                                  "launchDesc": (d.get("launchDesc") or str(tur.get("description") or "").strip()
                                                 or ("workflow " + str(wf) if wf else "")),
                                  "command": d.get("detail")
                                             or _clip_detail(tur.get("prompt")
                                                             or ("script: " + str(tur["scriptPath"])
                                                                 if tur.get("scriptPath") else "")),
                                  "outputFile": tur.get("outputFile") or ""}
                    if tur.get("taskType") or d.get("type"):
                        tasks[tid]["type"] = tur.get("taskType") or d["type"]
                    if tur.get("agentId"):   # the ack names the agent whose own transcript this launch writes
                        tasks[tid]["agentId"] = str(tur["agentId"])   # (plans/subagent-transcripts.md)
                    order.append(tid)
                    continue
                if async_launch and tid in tasks and not b.get("is_error") \
                        and tasks[tid]["status"] == "running":
                    # the ack of a launch the assistant branch already registered (explicit
                    # run_in_background): the ack still owns outputFile/taskType — fill what
                    # the launch row lacks, never overwrite what it has
                    tk = tasks[tid]
                    tk["outputFile"] = tk["outputFile"] or tur.get("outputFile") or ""
                    if not tk.get("launchDesc") and tur.get("description"):
                        tk["launchDesc"] = str(tur["description"]).strip()[:200]
                    if tur.get("taskType"):
                        tk["type"] = tur["taskType"]
                    if tur.get("agentId") and not tk.get("agentId"):
                        tk["agentId"] = str(tur["agentId"])
                    if not tk["command"]:
                        tk["command"] = _clip_detail(tur.get("prompt") or "")
                    continue
                note = _parse_task_notification(_result_text(b.get("content")))
                if tid in tasks and note:      # its result landed → mark it done; the keep-filter drops it
                    if tasks[tid].get("monitor") and not note.get("has_status"):
                        continue               # a wrapped monitor EVENT — not a terminal (see _mark)
                    tasks[tid].update(status=note["status"], outputFile=note["output_file"],
                                      summary=note["summary"] or tasks[tid]["summary"])
                    if note.get("result"):
                        tasks[tid]["result"] = note["result"]
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


def scan_bg_tasks_cached(path, cache, want_all=False, ckpt=None):
    """_scan_bg_tasks folded append-incrementally through `cache` (fold_records): a changed transcript
    steps only its appended records instead of re-pairing the whole file — the kernel's chat box, awaiting
    source and awaiting-stamp lift, and the judge's settled gate, all asked per push per session, and
    each re-walked a working session's entire transcript on every streamed record (measured live
    2026-09-02: four such readers over one 180 MB transcript held the push loop at a full core). `cache`
    is the caller's dict (the kernel keeps a running-only and an every-task cache; the judge its own), so
    the two views never invalidate each other and a test's `.clear()` still forces a re-fold."""
    state = fold_records(cache, path, lambda: _bg_fresh(want_all), _bg_step, ckpt=ckpt)
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
_JSONL_CACHE = {}                 # path -> (mtime, size, offset, tail_bytes, records, base, gen, offsets); dict order = LRU, hits reinsert
#                                   offsets: array of (byte offset, byte length) pairs, one per held record (T323 stage 4)
#                                   base: how many records of the file precede records[0] (0 = the whole file is held; > 0 = a
#                                   TAIL entry restored from a checkpoint, T323 stage 3); gen: a process-wide counter's value,
#                                   fresh for every from-zero read and every restored entry (a mismatch, an eviction, a first
#                                   read), kept across an upgrade from a tail entry to a whole one and across appends, so a
#                                   fold cursor keyed on it survives those and nothing else
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
# A BYTE budget beside the count (the kernel memory work, 2026-09-11): the count bounded slots, never memory, and the
# working set is files of every size (a 177 MB leaf and a 2 KB states log take one slot each), so the kernel climbed to
# 5 to 8 GB between restarts holding every live and subagent transcript's records (about 1.7 bytes resident per file
# byte). Each entry weighs the bytes it holds (the file size less a tail entry's offset); past the budget the least
# recently used entries go first, one at a time, under the same LRU order the count uses, so a hot leaf survives a
# cold flood of subagent files exactly as before. A single entry larger than the whole budget still inserts: a leaf is
# never refused, the budget then holds that one entry. Counters under /perf recordCache.
# The default is HALF of the machine's memory (the user's direction, 2026-09-11: use the memory we have), never under 4 GiB (2026-09-11, the day the budget shipped at 1 GiB):
# the working set of a devbox running 50 sessions is their live leaves, read by every build in every pusher cycle, and
# a budget below it does not save memory, it thrashes: 18 entries filled the 1 GiB, every build re-read whole
# transcripts (14.9 GB read in the first 3.5 minutes, 724 evictions, one pusher cycle of 132 s, chat builds of 3 s
# each), and glibc's arenas kept the churn, 14 GB resident over a 1 GiB cache. What is not needed until looked at
# (subagent transcripts) leaves through drop_after="quiescent" folds instead; the budget is the backstop, not the
# mechanism. ROMP_RECORD_CACHE_BUDGET_MB still sets it outright.
RECORD_CACHE_BUDGET_FLOOR_BYTES = 4 * 1024 ** 3
RECORD_CACHE_BUDGET_FRACTION = 0.5


def _record_cache_default_budget_bytes(meminfo_text=None):
    """Half of MemTotal (from /proc/meminfo, or the text given), floored at 4 GiB; the floor alone when the file
    is unreadable (macOS, a container without procfs)."""
    try:
        text = meminfo_text if meminfo_text is not None else open("/proc/meminfo", encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return max(RECORD_CACHE_BUDGET_FLOOR_BYTES, int(kb * 1024 * RECORD_CACHE_BUDGET_FRACTION))
    except Exception:
        pass
    return RECORD_CACHE_BUDGET_FLOOR_BYTES


_JSONL_CACHE_BUDGET_BYTES = (int(float(os.environ["ROMP_RECORD_CACHE_BUDGET_MB"]) * 1024 * 1024)
                             if os.environ.get("ROMP_RECORD_CACHE_BUDGET_MB") else _record_cache_default_budget_bytes())
_JSONL_CACHE_BYTES = [0]          # the sum of every held entry's weight, kept in step with _JSONL_CACHE under its lock
_RECORD_CACHE_STATS = {"inserts": 0, "evictions": 0, "evictedBytes": 0, "budgetEvictions": 0, "dropped": 0, "droppedBytes": 0,
                       "wholeReads": {}}   # "kind<-caller" -> {"count", "bytes"}: every read that pulled a file WHOLE (from zero, or a
#                                          tail entry upgraded to the whole file), named by the reader's kind and the first frame
#                                          outside this module (T384: the way hydratedBy named the planner; the 0.8 GB of whole
#                                          reads of restored leaves the per-path bytes could not attribute). A restore's tail read
#                                          and an append are not whole reads and are not counted here.
_DROP_AFTER_QUIESCENT_S = float(os.environ.get("ROMP_RECORD_CACHE_DROP_QUIESCENT_S", "120"))   # a file this long unchanged
#                                   is one whose writer has finished (a subagent that returned): its records are not kept


def _entry_weight(ent) -> int:
    """The bytes an entry holds: the file's size less the offset a tail entry started at (a checkpoint's cut)."""
    try:
        size, base = int(ent[1]), int(ent[5])
        return max(0, size - int(ent[2])) if base > 0 else size
    except Exception:
        return 0


def _cache_pop_locked(path):
    """Under _JSONL_CACHE_LOCK: drop `path`'s entry and its weight; returns the weight (0 when absent)."""
    ent = _JSONL_CACHE.pop(path, None)
    if ent is None:
        return 0
    w = _entry_weight(ent)
    _JSONL_CACHE_BYTES[0] = max(0, _JSONL_CACHE_BYTES[0] - w)
    return w


def _cache_insert_locked(path, ent):
    """Under _JSONL_CACHE_LOCK: insert `ent` at the LRU tail, evicting the least recently used entries past the count cap
    and past the byte budget (the new entry's own weight counted; an entry larger than the budget alone still inserts)."""
    _cache_pop_locked(path)
    w = _entry_weight(ent)
    while len(_JSONL_CACHE) >= _JSONL_CACHE_MAX:
        _RECORD_CACHE_STATS["evictions"] += 1
        _RECORD_CACHE_STATS["evictedBytes"] += _cache_pop_locked(next(iter(_JSONL_CACHE)))   # oldest-used first; hot entries survive any cold flood
    while _JSONL_CACHE and _JSONL_CACHE_BYTES[0] + w > _JSONL_CACHE_BUDGET_BYTES:
        _RECORD_CACHE_STATS["evictions"] += 1; _RECORD_CACHE_STATS["budgetEvictions"] += 1
        _RECORD_CACHE_STATS["evictedBytes"] += _cache_pop_locked(next(iter(_JSONL_CACHE)))
    _JSONL_CACHE[path] = ent
    _JSONL_CACHE_BYTES[0] += w
    _RECORD_CACHE_STATS["inserts"] += 1


def record_cache_stats() -> dict:
    """The record cache for /perf: entries, held bytes, the budget, and the counters (inserts, evictions by count and by
    budget, evicted bytes, drop-after-fold drops)."""
    with _JSONL_CACHE_LOCK:
        out = {"entries": len(_JSONL_CACHE), "bytes": _JSONL_CACHE_BYTES[0], "budgetBytes": _JSONL_CACHE_BUDGET_BYTES,
               "countCap": _JSONL_CACHE_MAX, **_RECORD_CACHE_STATS}
        table = _RECORD_CACHE_STATS.get("wholeReads")
        out["wholeReads"] = {k: dict(v) for k, v in table.items()} if isinstance(table, dict) else {}
        return out


_JSONL_TAIL_GUARD = 64            # bytes of pre-offset content re-verified before an incremental read
_JSONL_CACHE_LOCK = threading.Lock()   # the cache has cross-thread callers (the judge tiers' worker pools,
                                       # the pusher, the warm threads) and HITS mutate (LRU reinsert): the
                                       # lock covers only the cheap dict ops — the parse runs outside it —
                                       # and pops stay guarded so a lost race degrades to a re-parse, never
                                       # a raise


_READ_BYTES = {}                  # path -> bytes this process read from it through the reader (the exit-then-boot witness; /perf)
_READ_BYTES_LOCK = threading.Lock()


_READ_BYTES_TOTAL = [0]           # the reader's bytes off disk since the process began, one integer (T397: a stage mark)
_THREAD_BYTES = threading.local()  # the same, per THREAD (`read`, `hydrated`): the pusher's stage split reads its own thread's


def _count_read(path, n):
    with _READ_BYTES_LOCK:
        _READ_BYTES[path] = _READ_BYTES.get(path, 0) + int(n)
        _READ_BYTES_TOTAL[0] += int(n)
    _THREAD_BYTES.read = getattr(_THREAD_BYTES, "read", 0) + int(n)


def read_bytes_total():
    """What the reader pulled off disk since the process began, as one number (the per-path table is read_bytes_report)."""
    with _READ_BYTES_LOCK:
        return _READ_BYTES_TOTAL[0]


def thread_read_bytes():
    """What the reader pulled off disk on the CALLING thread since it began (T397 round one, low 2: a stage's bytes are the
    pusher's own, not the judges' first pass or a boot warm reading through the same window)."""
    return getattr(_THREAD_BYTES, "read", 0)


def thread_hydrated_bytes():
    """The assembly cut's hydrated bytes on the CALLING thread since it began (the process total is asmCheckpoint.hydratedBytes)."""
    return getattr(_THREAD_BYTES, "hydrated", 0)


def read_bytes_report():
    """{path: bytes read since this process began} plus "total": what the reader itself pulled off disk, file by
    file, so a test or /perf can say how much of a boot was tails and how much whole files."""
    with _READ_BYTES_LOCK:
        out = dict(_READ_BYTES)
        out["total"] = _READ_BYTES_TOTAL[0]              # the running total, kept for this report alone (T397 round two, low 1)
    return out


# ───────────────────────── fold checkpoints (T323 stage 3, 2026-09-11) ─────────────────────────
# A checkpoint is one small JSON file per folded JSONL file, under the state root's checkpoints/ directory, named by
# the sha1 of the file's realpath. It records the reader's PREFIX WITNESS (the byte offset past the last complete
# line, the up-to-64 bytes before it, the record count before it) and, for every fold_records fold whose cursor stood
# at that count, the fold's state (through _ckpt_encode: sets and tuples survive the JSON). A fresh process that folds
# the file restores the witness, verifies the guard bytes on disk and reads only offset..EOF; the folds resume from
# their recorded states over the tail. Anything that does not verify (version, path, a shrunk file, a rewrite under the
# guard, a same-size-new-mtime, a corrupt document) falls back to a whole read from zero, is counted per reason and
# says so on stderr. The directory is asked of a provider the kernel/judge install (it follows _rebind_state); with no
# provider, checkpoints are off and every fold behaves as before. Writes are event-keyed by the kernel (a session's
# settle, its states log moving, exit), never by a timer.
_CKPT_V = 1
_CKPT_DIR_FN = None               # () -> Path of the checkpoint directory; None = checkpoints off
_CKPT_STATS = {"restored": 0, "writes": 0, "swept": 0, "skippedFolds": 0, "fallbacks": {}, "restoredFolds": {}, "droppedRestores": 0,
               "oversizeFolds": {}, "coldFolds": {}, "coldWrites": {},
               "refolds": {},         # per fold name: {"count", "bytes"} of whole refolds that READ (a fold with no cursor and nothing to
#                                       restore over a tail entry reads the file whole; T377 named the boot's whole reads this way)
               "converge": {"passes": 0, "writes": 0, "bytes": 0, "heals": 0, "healBytes": 0, "primed": 0, "deferred": 0,
                            "failed": 0, "unhealed": 0, "docReadBytes": 0, "quiescent": 0, "skipped": 0,
                            "dropWrites": 0, "dropDeferred": 0, "viaDrop": 0}}   # T360, T361, T362
_CKPT_DOC_FOLDS = {}              # path -> {fold name: "state" | "over" | "cold" | "bare"}: the document on disk as last written or
#                                   loaded in this process, so the converge pass can tell a document lacking a state without a read
_COLD = object()                  # a restored cursor with no state (its fold was oversize): fold_records inits it and steps the tail
_COLD_FOLDS = set()               # (path, fold name) whose state in this process began cold at a cut: a TAIL-ONLY state, written as a
_COLD_OVER_KB = {}                # (path, fold name) -> the KB an over-the-cap state measured, carried through a cold write (T359)
_COLD_REASONS = {}                # (path, fold name) -> why it restarts cold: "over" (its state was over the cap: by design, every
#                                   boot) or "cold" (its document carried a cursor without a state: a tail-only state written by
#                                   a process where it began cold, or an older kernel's entry; the settle's primer heals it once)
                                  #  cursor without state until a whole refold, never as a complete one (review find, 2026-09-11)
_SAID = set()


def _say_once(line):
    """One stderr line per distinct text for the process (a skip that recurs at every settle is said the first time)."""
    if line in _SAID:
        return
    _SAID.add(line)
    try:
        sys.stderr.write(line + "\n")
    except Exception:
        pass


def _machine_memory_bytes(meminfo_text=None):
    """MemTotal from /proc/meminfo (or the text given) in bytes; 0 when unreadable (macOS, a container without procfs).
    The ceilings below are fractions of it (the user's direction, 2026-09-11: romp uses the machine's memory; every cache
    is one shared pool sized to the machine, never a small literal that sits below the working set and thrashes)."""
    try:
        text = meminfo_text if meminfo_text is not None else open("/proc/meminfo", encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _env_or(name, default, scale=1):
    """An integer knob from the environment (scaled), else the derived default."""
    try:
        v = os.environ.get(name)
        return int(float(v) * scale) if v else default
    except (TypeError, ValueError):
        return default


# 8 MiB, was 64 KB (2026-09-11, the day the checkpoints shipped): 31 leaves' bgAll states sat over 64 KB and every boot
# cold-refolded them whole, 1.74 GB read in the first pusher cycle (65 s of tick jobs); a document of a few MB reads in
# milliseconds. ROMP_CKPT_FOLD_CAP_KB sets it outright.
_CKPT_FOLD_CAP = _env_or("ROMP_CKPT_FOLD_CAP_KB", 8 * 1024 * 1024, 1024)
#                                   bytes of encoded state a fold may put in a checkpoint. A state that grows with its file (the postal
#                                   log fold's map of every sent row, the state intervals' list of every transition, the every-task
#                                   background view on a long transcript) would make the document a second copy of the file: reading
#                                   it at boot costs what the checkpoint exists to save (measured 2026-09-11: 11 MB of documents in a
#                                   44 MB world). Such a fold is left out, counted per name, and cold-folds at first touch; the folds
#                                   whose state is bounded (the gist's capped steps, the running tasks, the newest rows) are the ones
#                                   the checkpoint pays for. A bounded projection for the growing ones is a later stage's item.
_CKPT_CYCLE_CAP_DEFAULT = _env_or("ROMP_CKPT_CONVERGE_MB", 8 * 1024 * 1024, 1024 * 1024)   # the pusher cycle's byte budget for
#                                   checkpoint work (documents written and read, leaves read for a heal), shared by the converge pass
#                                   and the quiescence drop's write (T362); the kernel's knobs read these defaults, so they reach
#                                   the drop write before the first cycle begins (round two, low 3)
try:
    _CKPT_CONVERGE_MS_DEFAULT = float(os.environ.get("ROMP_CKPT_CONVERGE_MS", "150"))   # the pass's wall budget; 0 turns the pass
except ValueError:                                                                        #  off and the drop write with it
    _CKPT_CONVERGE_MS_DEFAULT = 150.0


def _ckpt_cycle_default_cap():
    """The cycle budget that stands before any pusher cycle has begun, and in a process that never begins one (the judges):
    the byte knob, or 0 (the drop write off) when either knob is zero."""
    return _CKPT_CYCLE_CAP_DEFAULT if _CKPT_CONVERGE_MS_DEFAULT > 0 and _CKPT_CYCLE_CAP_DEFAULT > 0 else 0


_CKPT_CYCLE = {"cap": _ckpt_cycle_default_cap(), "spent": 0}   # the cycle in progress: the kernel begins each pusher cycle at
#                                   its START (so the boot's first builds are capped where the volume is); before any cycle the
#                                   default above stands; a cap of 0 turns the drop write off (the drop pops as before T362)
_DROP_OWED = {}                   # path -> when its quiescence drop was deferred for the cycle's budget (T362): paid at the next
#                                   cycle's start with the room it has, oldest first, or by the next fold over the file
_DROP_OWED_MAX = 4096             # over it the OLDEST owed entry is popped unwritten (its memory released), never the table cleared
_DROP_HOLD = threading.local()    # the converge pass holds this thread's quiescence drops while it heals and primes a leaf, then pays
#                                   them once (T362 follow-up review, low 2): {path: pop} of the drops held, or absent
_CKPT_LOCK = threading.Lock()
_CKPT_PENDING = {}                # path -> {"count": N, "gen": g, "folds": {name: {"count", "state"}}} restores not yet taken
_CKPT_SEQ = {}                    # path -> the seq of the last checkpoint read or written for it
_FOLD_DIRTY = set()               # paths whose fold cursors moved since their checkpoint was last written
_FOLD_REG = {}                    # checkpoint name -> the fold's cursor dict (fold_records registers at first call)
_GEN = [0]                        # the reader's generation counter: every from-zero read and every restored entry takes the
#                                   next value, process-wide, so no two entries of a path ever share one (a cursor left by an
#                                   entry the cache evicted or a stat failure popped must never match a later read's, since
#                                   the later read may be of a rewritten file with as many records; review find, 2026-09-11)


def _next_gen():
    with _JSONL_CACHE_LOCK:
        _GEN[0] += 1
        return _GEN[0]
_FOLD_NAME_OF = {}                # id(cursor dict) -> checkpoint name, for callers that name their cache once (name_fold_cache)


def drop_cold_cursors(path):
    """Drop the cursors of the folds over `path` that began cold for want of a state (reason "cold", never "over"), so each
    is refolded whole at its next run and complete again (T359). Returns the names. For a leaf the kernel reruns its folds
    at the settle before the write; for another file the write leaves them out and the next boot reads the file whole once."""
    key = str(path)
    with _CKPT_LOCK:
        names = [n for k, n in _COLD_FOLDS if k == key and _COLD_REASONS.get((k, n), "cold") == "cold"]
        for n in names:                                   # the cold marks go with the cursor: the fold's next run is a whole refold,
            _COLD_FOLDS.discard((key, n)); _COLD_REASONS.pop((key, n), None); _COLD_OVER_KB.pop((key, n), None)   #  and until then
    for n in names:                                       #  the path is no candidate for it (review: an unhealable cold fold kept a
        cache = _FOLD_REG.get(n)                          #  document being written every cycle)
        if cache is not None:
            cache.pop(key, None)
    return names


def cold_fold_reasons(path):
    """{fold name: reason} for the folds over `path` that began cold in this process ("over": its state is over the cap, by
    design; "cold": its document carried a cursor without a state, which one whole refold heals). The settle's primer asks."""
    key = str(path)
    with _CKPT_LOCK:
        return {n: _COLD_REASONS.get((k, n), "cold") for k, n in _COLD_FOLDS if k == key}


def entry_whole_resident(path):
    """True when the reader holds `path`'s WHOLE file in memory (an entry read from byte zero): a fold over it costs
    no read. False for a tail entry or none."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(str(path))
    return ent is not None and ent[5] == 0


def fold_cursor_appendable(cache, path):
    """True when `cache` holds a cursor for `path` at the reader's current entry generation: a fold over it is a stat or
    an append over the entry's records in hand, never a whole read. False with no cursor, no entry or another generation."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(str(path))
    cur = cache.get(str(path))
    return ent is not None and cur is not None and cur[1] == ent[6]


def name_fold_cache(cache, name):
    """Give a fold's cursor dict its checkpoint name once, so every fold_records call over it is resumable without
    a `ckpt` argument at the call (a caller whose call shape other code stubs, such as the judge's background-task
    scan, keeps its signature)."""
    _FOLD_NAME_OF[id(cache)] = name
    _FOLD_REG[name] = cache


def set_checkpoint_dir(fn):
    """Kernel/judge wiring: `fn()` names the checkpoint directory (a Path) at CALL time, so a rebound state root
    moves it. None turns checkpoints off."""
    global _CKPT_DIR_FN
    _CKPT_DIR_FN = fn
    with _CKPT_LOCK:
        _CKPT_PENDING.clear(); _CKPT_SEQ.clear(); _FOLD_DIRTY.clear(); _CKPT_DOC_FOLDS.clear(); _DROP_OWED.clear()
        _CKPT_CYCLE["cap"] = _ckpt_cycle_default_cap(); _CKPT_CYCLE["spent"] = 0


def _ckpt_dir():
    fn = _CKPT_DIR_FN
    return None if fn is None else fn()


def _ckpt_file(path):
    d = _ckpt_dir()
    if d is None:
        return None
    return Path(d) / (hashlib.sha1(os.path.realpath(str(path)).encode("utf-8")).hexdigest()[:20] + ".json")


def _ckpt_fallback(path, reason, detail=""):
    """A checkpoint that did not verify: counted per reason, said once on stderr, and its file removed so the next
    write starts clean. The caller reads the whole file from zero."""
    with _CKPT_LOCK:
        _CKPT_STATS["fallbacks"][reason] = _CKPT_STATS["fallbacks"].get(reason, 0) + 1
        _CKPT_PENDING.pop(str(path), None)
        _CKPT_DOC_FOLDS.pop(str(path), None)              # the document is gone: the converge pass must not believe it whole
    try:
        sys.stderr.write("checkpoint fallback (%s) for %s%s\n" % (reason, path, (": " + detail) if detail else ""))
    except Exception:
        pass
    cp = _ckpt_file(path)
    if cp is not None:
        try:
            cp.unlink()
        except OSError:
            pass


def _ckpt_encode(o):
    """The fold states are plain data (dict, list, set, tuple, str, int, float, bool, None); JSON keeps dicts with
    string keys and lists, so sets, tuples and non-string-keyed dicts are tagged. Raises TypeError on anything else,
    which the writer treats as a fold that cannot be checkpointed (counted, never a torn state)."""
    if o is None or isinstance(o, (bool, int, float, str)):
        return o
    if isinstance(o, list):
        return [_ckpt_encode(x) for x in o]
    if isinstance(o, tuple):
        return {"~tuple": [_ckpt_encode(x) for x in o]}
    if isinstance(o, (set, frozenset)):
        return {"~set": sorted((_ckpt_encode(x) for x in o), key=lambda x: json.dumps(x, sort_keys=True))}
    if isinstance(o, dict):
        if all(isinstance(k, str) and not k.startswith("~") for k in o):
            return {k: _ckpt_encode(v) for k, v in o.items()}
        return {"~dict": [[_ckpt_encode(k), _ckpt_encode(v)] for k, v in o.items()]}
    raise TypeError("not checkpointable: %s" % type(o).__name__)


def _ckpt_decode(o):
    if isinstance(o, list):
        return [_ckpt_decode(x) for x in o]
    if isinstance(o, dict):
        if len(o) == 1:
            (k, v), = o.items()
            if k == "~tuple":
                return tuple(_ckpt_decode(x) for x in v)
            if k == "~set":
                return set(_ckpt_decode(x) for x in v)
            if k == "~dict":
                return {_ckpt_decode(a): _ckpt_decode(b) for a, b in v}
        return {k: _ckpt_decode(v) for k, v in o.items()}
    return o


def _ckpt_load(path):
    """The verified-by-shape checkpoint document for `path`, or None (absent, or a fallback was counted)."""
    cp = _ckpt_file(path)
    if cp is None or not cp.exists():
        return None
    try:
        text = cp.read_bytes()
        _count_read(str(cp), len(text))                   # the document is a read of the boot too (/perf, the bench)
        doc = json.loads(text.decode("utf-8"))
    except (OSError, ValueError) as e:
        _ckpt_fallback(path, "corrupt", str(e)[:80]); return None
    if not isinstance(doc, dict) or doc.get("v") != _CKPT_V:
        _ckpt_fallback(path, "version", repr(doc.get("v")) if isinstance(doc, dict) else "not a document"); return None
    if doc.get("path") != os.path.realpath(str(path)):
        _ckpt_fallback(path, "path", str(doc.get("path"))[:80]); return None
    try:
        offset, count, size = int(doc["offset"]), int(doc["count"]), int(doc["size"])
        guard = bytes.fromhex(doc.get("guard") or "")
        folds = doc.get("folds") or {}
        if offset < 0 or count < 0 or len(guard) > _JSONL_TAIL_GUARD or not isinstance(folds, dict):
            raise ValueError("shape")
    except (KeyError, TypeError, ValueError) as e:
        _ckpt_fallback(path, "corrupt", "shape: %s" % e); return None
    return doc


def _ckpt_verdict(doc, st_size, st_mtime):
    """What the file's stat says about a checkpoint document before its guard is read: "shrunk" (the file ends before
    the recorded offset), "rewrite" (the same size under another mtime, or shorter than recorded while past the offset:
    a rewrite the guard could miss), or None (grown, or the very file the document describes: the guard decides). Both
    restore paths (the tail read and the whole-reader-first fold) ask this, so one rewrite gets one verdict and one
    fallback reason whichever reader came first."""
    offset, size, mtime = int(doc["offset"]), int(doc["size"]), float(doc.get("mtime") or 0)
    if st_size < offset:
        return "shrunk"
    if st_size < size or (st_size == size and st_mtime != mtime):
        return "rewrite"
    return None


def _checkpoint_entry(path, st):
    """A TAIL reader entry restored from `path`'s checkpoint, (mtime, size, offset, guard, [], count, 0), or None. The
    guard bytes are verified by the reader against the file; here the document and the size are: a file shorter than
    the recorded offset is a shrink."""
    doc = _ckpt_load(path)
    _CKPT_DOC_FOLDS[str(path)] = _doc_fold_shapes(doc.get("folds")) if isinstance(doc, dict) else {}   # what the disk holds (T360)
    if doc is None:
        return None
    offset, count = int(doc["offset"]), int(doc["count"])
    verdict = _ckpt_verdict(doc, st.st_size, st.st_mtime)
    if verdict is not None:
        _ckpt_fallback(path, verdict, "size %d, recorded %d at offset %d" % (st.st_size, int(doc["size"]), offset)); return None
    gen = _next_gen()
    with _CKPT_LOCK:
        _CKPT_PENDING[str(path)] = {"count": count, "gen": gen, "folds": dict(doc.get("folds") or {})}
        _CKPT_SEQ[str(path)] = int(doc.get("seq") or 0)
    return (float(doc.get("mtime") or 0), int(doc["size"]), offset, bytes.fromhex(doc.get("guard") or ""), [], count, gen)


def _ckpt_pending(path, ent):
    """The restores waiting for `path`'s folds, given the reader's current entry: the ones a tail restore left, or,
    when a whole reader read the file first (the entry holds every record and no restore happened), the checkpoint's
    fold states after its guard is verified on disk by one 64-byte read. None when there is nothing to resume."""
    key = str(path)
    with _CKPT_LOCK:
        pend = _CKPT_PENDING.get(key)
    if pend is not None or ent is None or _CKPT_DIR_FN is None:
        return pend                                       # a tail entry the assembly checkpoint's cut read created (T323
    #                                                       stage 4a) is consulted like a whole one: the fold's recorded
    #                                                       count must lie within the records the entry holds
    with _CKPT_LOCK:
        if key in _CKPT_SEQ:                          # already consulted (or written) in this process: nothing new
            return None
    doc = _ckpt_load(path)
    _CKPT_DOC_FOLDS[str(path)] = _doc_fold_shapes(doc.get("folds")) if isinstance(doc, dict) else {}   # what the disk holds (T360)
    if doc is None:
        with _CKPT_LOCK:
            _CKPT_SEQ.setdefault(key, 0)
        return None
    offset, count, guard = int(doc["offset"]), int(doc["count"]), bytes.fromhex(doc.get("guard") or "")
    verdict = _ckpt_verdict(doc, ent[1], ent[0])          # the same facts the tail path checks: size, mtime, then the guard
    if verdict is not None:
        _ckpt_fallback(path, verdict, "whole reader first"); return None
    ok = False
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, offset - len(guard)))
            ok = fh.read(len(guard)) == guard
        _count_read(key, len(guard))
    except OSError:
        ok = False
    if not ok:
        _ckpt_fallback(path, "guard", "whole reader first"); return None
    if count < ent[5] or count > ent[5] + len(ent[4]):
        with _CKPT_LOCK:
            _CKPT_SEQ[key] = int(doc.get("seq") or 0)
        return None                                       # the fold's count is outside the held records: a cold fold, no fallback
    pend = {"count": count, "gen": ent[6], "folds": dict(doc.get("folds") or {})}
    with _CKPT_LOCK:
        _CKPT_PENDING[key] = pend
        _CKPT_SEQ[key] = int(doc.get("seq") or 0)
        _CKPT_STATS["restored"] += 1
    return pend


def _document_carries_state(key, name, ent):
    """Whether the document's pending restore for `key` carries a complete state for fold `name` (a cursor with `state`), as
    a stale-generation fold asks before taking it over a state it holds (T362 round one, medium). Consults the same pending
    record the restore would, taking nothing."""
    pend = _ckpt_pending(key, ent)
    f = pend["folds"].get(name) if pend is not None else None
    return isinstance(f, dict) and "state" in f


def _restored_cursor(key, name, ent):
    """The fold cursor (count, gen, state) a checkpoint carries for fold `name` of `key`, when it stands at a count
    the current entry can resume from; None otherwise. Each fold's restore is taken once."""
    pend = _ckpt_pending(key, ent)
    if pend is None:
        return None
    f = pend["folds"].pop(name, None)
    if not isinstance(f, dict):
        return None
    base = ent[5]
    try:
        count = int(f["count"])
        if pend["gen"] != ent[6]:                          # the entry is not the one the restore was taken for: another
            with _CKPT_LOCK:                              # read replaced it (the stripe lock makes this a residual)
                _CKPT_STATS["droppedRestores"] += 1
            try:
                sys.stderr.write("checkpoint restore dropped for fold %s of %s: the reader's entry moved under it\n" % (name, key))
            except Exception:
                pass
            return None
        if count < base or count > base + len(ent[4]):
            return None                                   # a fold's count may differ from the document's cut (T359: the cut is the
        if "state" not in f:                              #  lowest written cursor): inside the entry's held records it is an append
            reason = "over" if f.get("over") else "cold"  #  from there; outside them it is nothing to resume from
            with _CKPT_LOCK:                              # a cursor without a state: the fold starts cold at the cut, for the reason
                _CKPT_STATS["coldFolds"][name] = _CKPT_STATS["coldFolds"].get(name, 0) + 1   #  the document names (T359: the line
                _COLD_REASONS[(key, name)] = reason       #  blamed the cap whatever the reason)
                if reason == "over":
                    _COLD_OVER_KB[(key, name)] = int(f["over"])   # carried through this process's cold writes: over stays over
            if reason == "over":
                _say_once("checkpoint: fold %s of %s restarts cold over the tail: its state was %d KB, over the %d KB cap"
                          % (name, key, int(f["over"]), _CKPT_FOLD_CAP // 1024))
            else:
                _say_once("checkpoint: fold %s of %s restarts cold over the tail: its document carries its cursor without a state "
                          "(a tail-only state, or an older kernel's cursor-only entry); one whole refold heals it, at the session's "
                          "next settle for a leaf's folds, at the file's next checkpoint write otherwise" % (name, key))
            return (count, ent[6], _COLD)
        state = _ckpt_decode(f["state"])
        with _CKPT_LOCK:
            _CKPT_STATS["restoredFolds"][name] = _CKPT_STATS["restoredFolds"].get(name, 0) + 1
        return (count, ent[6], state)
    except (KeyError, TypeError, ValueError) as e:
        _ckpt_fallback(key, "corrupt", "fold %s: %s" % (name, e)); return None


_CKPT_LAG_FLOOR = 64                # a fold's count may lag the entry's by this many records, or an eighth of the entry, whichever
#                                     is more, and still be written (or carried) at its own count; further behind it is left out,
#                                     so a fold that ran early and stopped (a skipped wake-tail, a cursor dropped at quiescence)
#                                     cannot drag the document's cut, and with it every later boot's tail read, back to its count


def _lag_ok(count, c):
    return count - c <= max(_CKPT_LAG_FLOOR, count // 8)


def _count_recordable(base, total, c):
    """Whether a fold count `c` can be recorded against an entry holding records base..total: inside the held records and not
    further behind than the lag bound. The ONE count rule of the writer, the carry, the candidate check and the drop (T362)."""
    return base <= c <= total and _lag_ok(total, c)


def _cursor_recordable(cur, gen, base, total):
    """Whether a fold's cursor `cur` (count, gen, state) is one the writer records at this entry."""
    return cur is not None and cur[1] == gen and _count_recordable(base, total, cur[0])


def _path_needs_write(key, ent, at_drop=False):
    """Whether a write now would improve `key`'s document on disk with something this process holds (T360's candidate rule,
    T362's one rule): a recordable cursor whose fold holds a complete state here and has none in the document (missing, a bare
    cursor, a tail-only one). For the converge pass, a fold cold for want of a state too (the pass heals it: a whole refold,
    then the write). At the drop (`at_drop`): a dirty path too (a file about to leave memory whose folds moved since its
    document; never for the pass, for which a live leaf is dirty every turn and is written at its settle), and a cold fold
    never (the drop cannot heal it, and a write would record it cold again: no improvement, so a quiescent file whose document
    holds the dropping fold's cursor without a state is not rewritten at every fold; round one, low 1). False for a whole
    document over a clean path: nothing to write."""
    with _CKPT_LOCK:
        if at_drop and key in _FOLD_DIRTY:
            return True
        cold = {n for k, n in _COLD_FOLDS if k == key}
        if not at_drop and any(_COLD_REASONS.get((key, n), "cold") == "cold" for n in cold):
            return True
    shapes = _CKPT_DOC_FOLDS.get(key, {})
    base, gen, total = ent[5], ent[6], ent[5] + len(ent[4])
    for name, cache in list(_FOLD_REG.items()):
        if name in cold:
            continue                                      # a tail-only state is written as a cursor without one: nothing gained
        if _cursor_recordable(cache.get(key), gen, base, total) and shapes.get(name) not in ("state", "over"):
            return True
    return False


def checkpoint_cycle_begin(cap):
    """The pusher's cycle begins: the checkpoint byte budget `cap` (documents written and read, leaves read for a heal) is
    whole again; the converge pass and the quiescence drop's writes charge it (T362). A cap of 0 (the pass off, or a zero
    byte knob) turns the drop write off: the drop pops as before, holding nothing."""
    with _CKPT_LOCK:
        _CKPT_CYCLE["cap"] = int(cap); _CKPT_CYCLE["spent"] = 0


def checkpoint_cycle_take(n):
    """Take `n` bytes of the cycle's budget in ONE step (the room check and the charge under one lock, so concurrent folds
    cannot each pass a check the other's charge would fail): True and charged when they fit, False and uncharged otherwise."""
    with _CKPT_LOCK:
        if _CKPT_CYCLE["spent"] + int(n) > _CKPT_CYCLE["cap"]:
            return False
        _CKPT_CYCLE["spent"] += int(n)
        return True


def checkpoint_drop_writes_on():
    """Whether the quiescence drop writes documents this cycle (a cap above zero)."""
    with _CKPT_LOCK:
        return _CKPT_CYCLE["cap"] > 0


def _pop_owed_entry(key):
    """Release `key`'s cached entry unwritten (an owed drop paid by the pop alone), under the reader's lock only."""
    with _JSONL_CACHE_LOCK:
        w = _cache_pop_locked(key)
        if w:
            _RECORD_CACHE_STATS["dropped"] += 1
            _RECORD_CACHE_STATS["droppedBytes"] += w


class hold_quiescent_drops:
    """`with hold_quiescent_drops():` on this thread a quiescent-drop fold neither writes nor pops at its end; the drop is held
    (with whether it would have popped) for pay_held_drops, so a pass that heals and primes a leaf over its resident entry
    writes the document ONCE, after every fold is current, and pops once (a heal whose last fold was the launch fold popped the
    entry mid-heal before, and the folds never called stayed out of the document)."""
    def __enter__(self):
        _DROP_HOLD.held = {}
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            _DROP_HOLD.held = None                        # a raise inside the block: nothing stays held on this thread (an unpaid
        return False                                      #  hold would hold every later drop here; the entries fall to the reader's LRU)


def pay_held_drops():
    """Pay the drops held on this thread (see hold_quiescent_drops): {path: True when its document was written}. Each is paid
    as the fold would have (the write when the rule says so, the pop when a fold stepped records), against the cycle's budget."""
    held = getattr(_DROP_HOLD, "held", None)
    _DROP_HOLD.held = None
    out = {}
    for key, pop in (held or {}).items():
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None:
            continue
        out[key] = bool(_drop_quiescent_entry(key, ent, pop=pop))
    return out


def checkpoint_pay_owed_drops():
    """The cycle's start (T362 round one, low 2): the drops deferred for an earlier cycle's budget are paid now, oldest first,
    with the room this cycle has, since the files this serves (a returned subagent's transcript) may never be folded again and
    their whole entries would otherwise stay resident for the kernel's life. An owed path whose entry or file is gone is
    forgotten. Returns the drops paid (written or popped)."""
    with _CKPT_LOCK:
        owed = list(_DROP_OWED)
    paid = 0
    for key in owed:
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None or not os.path.exists(key):
            with _CKPT_LOCK:
                _DROP_OWED.pop(key, None)
            if ent is not None:                           # the file is gone: nothing to write, everything to release (round two,
                _pop_owed_entry(key)                      #  low 2: the memory the drop was owed for)
            continue
        _drop_quiescent_entry(key, ent, pop=True)         # takes the owed mark itself; over the budget again it re-defers
        with _CKPT_LOCK:
            if key not in _DROP_OWED:
                paid += 1
    return paid


def checkpoint_cycle_charge(n):
    """Charge `n` bytes to the cycle's budget (the pass's reads and writes, known only after the fact; a true-up may be
    negative); True while the cycle stays within it."""
    with _CKPT_LOCK:
        _CKPT_CYCLE["spent"] += int(n)
        return _CKPT_CYCLE["spent"] <= _CKPT_CYCLE["cap"]


def checkpoint_cycle_room(n):
    """Whether `n` more bytes would stay within the cycle's budget (the pass's gate between candidates)."""
    with _CKPT_LOCK:
        return _CKPT_CYCLE["spent"] + int(n) <= _CKPT_CYCLE["cap"]


def _carry_forward_states(key, folds, base, count, size, mtime):
    """The on-disk document's entries for folds this process holds no cursor for (a fold that never ran here, such as the
    transcript's wake-tail or queue-ledger folds beside the five leaf folds), carried into the next write so a write from
    this process's cursors alone does not strip states an earlier process stored (T360 review, medium 2: before the
    converge pass an idle session's document was never rewritten, so they survived). Only entries with a state, or an
    over-the-cap cursor, at a count inside this entry's held records, and only when the document's own guard still
    stands on disk (a file rewritten since voids its states); a bare or tail-only cursor is not carried: the next boot reads
    the file whole for that fold once and the write after carries its state."""
    cp = _ckpt_file(key)
    if cp is None or not cp.exists():
        return {}
    try:
        text = cp.read_bytes(); _count_read(str(cp), len(text))    # the carry's reads are the write's I/O: counted like the rest
        doc = json.loads(text.decode("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("v") != _CKPT_V or doc.get("path") != os.path.realpath(key):
        return {}
    try:
        if _ckpt_verdict(doc, size, mtime):                   # the entry's stat against the document, as the restore paths ask: a
            return {}                                         #  same-size edit under another mtime is a rewrite, and carries nothing
        off, guard = int(doc["offset"]), bytes.fromhex(doc.get("guard") or "")
        with open(key, "rb") as fh:
            fh.seek(max(0, off - len(guard)))
            ok = fh.read(len(guard)) == guard
        _count_read(key, len(guard))
        if not ok:
            return {}
    except (OSError, ValueError, TypeError, KeyError):
        return {}
    out = {}
    for name, f in (doc.get("folds") or {}).items():
        if name in folds or not isinstance(f, dict) or not ("state" in f or f.get("over")):
            continue
        try:
            c = int(f["count"])
        except (KeyError, TypeError, ValueError):
            continue
        if _count_recordable(base, count, c):                 # a carried count too far behind would drag the cut (the bound above)
            out[name] = f
    return out


def checkpoint_write(path, force=False):
    """Write `path`'s checkpoint from the reader's entry and every registered fold whose cursor stands at the entry's
    record count. False when there is nothing to write (no entry, or no fold at the witness and not `force`)."""
    key = str(path)
    cp = _ckpt_file(key)
    if cp is None:
        return False
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(key)
    if ent is None:
        return False
    mtime, size, offset, tail, records, base, gen = ent[:7]
    count = base + len(records)
    folds = {}
    for name, cache in list(_FOLD_REG.items()):
        cur = cache.get(key)
        if not _cursor_recordable(cur, gen, base, count):
            continue                                      # no cursor at this entry, one outside its held records, or one too far
        #                                                   behind to be written at its count without dragging the cut (_lag_ok)
        fcount = cur[0]                                   # the fold's OWN count (T359): a fold stepped by builds, not by the settle,
        with _CKPT_LOCK:                                  #  lags the entry and was left out silently before, to read the leaf whole
            cold = (key, name) in _COLD_FOLDS             #  at the next boot; the restore steps the held tail from its count
        if cold:                                          # a state that began cold at a cut covers the tail only: it must not
            with _CKPT_LOCK:                              #  be written as a complete one (the next process would restore it
                _CKPT_STATS["coldWrites"][name] = _CKPT_STATS["coldWrites"].get(name, 0) + 1   #  as whole and say nothing)
                over_kb = _COLD_OVER_KB.get((key, name)) if _COLD_REASONS.get((key, name)) == "over" else None
            if over_kb is not None:                       # cold BECAUSE over the cap: the reason and its KB carry through, so the
                folds[name] = {"count": fcount, "over": over_kb}   #  next boot neither promises a heal nor refolds it whole
            else:
                folds[name] = {"count": fcount, "cold": 1}   # a true tail-only state: the next process says why, and heals it
            continue
        try:
            enc = _ckpt_encode(cur[2])
            n_enc = len(json.dumps(enc, separators=(",", ":")))
            if n_enc > _CKPT_FOLD_CAP:
                with _CKPT_LOCK:                          # a state the size of its file: the document must not become the file
                    _CKPT_STATS["oversizeFolds"][name] = _CKPT_STATS["oversizeFolds"].get(name, 0) + 1
                folds[name] = {"count": fcount, "over": -(-n_enc // 1024)}   # the cursor without its state, with the reason (its KB):
                continue                                  #  the next process starts this fold cold over the tail (counted)
            folds[name] = {"count": fcount, "state": enc}
        except TypeError:
            with _CKPT_LOCK:
                _CKPT_STATS["skippedFolds"] += 1
    if not folds and not force:
        return False
    folds.update(_carry_forward_states(key, folds, base, count, size, mtime))   # the disk document's states for folds this process never ran
    cut = min([f["count"] for f in folds.values()] or [count])   # the document's cut: the LOWEST written cursor (T359), so the
    moved = None                                          #  next process's tail read holds every record a lagging fold has
    if cut < count and len(ent) >= 8 and ent[7]:          #  yet to step (its append), bounded by the records this entry holds
        off_cut = int(ent[7][(cut - base) * 2])           # the cut record's byte offset; the 64 bytes before it are the guard.
        try:                                              # The entry holds records, not bytes, so the guard is read from the file, in
            with open(key, "rb") as fh:                   #  the same open that first verifies the entry's OWN witness guard (its bytes
                fh.seek(max(0, offset - len(tail)))       #  before its offset, captured with its records): an append since the read
                if fh.read(len(tail)) != tail:            #  leaves that prefix intact and the cut move proceeds, carrying the lagging
                    raise OSError("the file was rewritten under the entry")   #  fold; a rewrite of any size or time fails it and writes
                fh.seek(max(0, off_cut - 64)); guard_cut = fh.read(min(64, off_cut))   #  the witness form, which the restore then refuses
            if off_cut and not guard_cut.endswith(b"\n"):   #  (review: a stat gate refused appends and passed a time-preserving copy)
                raise OSError("no record boundary at the cut")
        except OSError:
            guard_cut = None
        if guard_cut is not None:
            moved = (off_cut, guard_cut, records[cut - base - 1] if cut > base else None, cut)
    left_out = False
    if moved is not None:
        offset, tail, last, count = moved
    else:
        if cut < count:                                   # no cut move: the folds at the witness only; the lagging ones are left
            folds = {n: f for n, f in folds.items() if f["count"] == count}   #  out, and the path stays DIRTY below so the next
            left_out = True                               #  write (a settle, the exit drain) tries them again (review, low 3)
            if not folds and not force:
                return False
        last = records[-1] if records else None
    with _CKPT_LOCK:
        seq = _CKPT_SEQ.get(key, 0) + 1
    doc = {"v": _CKPT_V, "path": os.path.realpath(key), "size": int(size), "mtime": float(mtime), "offset": int(offset),
           "guard": tail.hex(), "count": int(count), "lastUuid": (last.get("uuid") if isinstance(last, dict) else None),
           "seq": seq, "t": time.time(), "folds": folds}
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_name("%s.%d.%x.tmp" % (cp.name, os.getpid(), threading.get_ident()))
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        os.replace(tmp, cp)
    except OSError:
        return False
    with _CKPT_LOCK:
        _CKPT_SEQ[key] = seq
        _CKPT_STATS["writes"] += 1
        _CKPT_DOC_FOLDS[key] = _doc_fold_shapes(folds)
        if left_out:
            _FOLD_DIRTY.add(key)                          # a lagging fold has no cursor in this document yet
        else:
            _FOLD_DIRTY.discard(key)
    return True


def checkpoint_dirty():
    """The paths whose fold cursors moved since their checkpoint was last written."""
    with _CKPT_LOCK:
        return sorted(_FOLD_DIRTY)


def _doc_fold_shapes(folds):
    return {n: ("state" if "state" in f else "over" if f.get("over") else "cold" if f.get("cold") else "bare")
            for n, f in (folds or {}).items() if isinstance(f, dict)}


def checkpoint_converge_candidates():
    """The dirty paths whose document on disk lacks a complete state for a fold this process holds a cursor for at the
    current entry (the fold is missing from it, or recorded as a bare or tail-only cursor), or whose fold began cold for
    want of a state: the writes that make a boot's whole reads pay ONCE (T360). Before them a fold that never ran in the
    writing process had no entry, the next boot read the leaf whole for it, and an idle session, which never settles,
    paid that at every boot. A dirty path whose document already carries every fold that ran is not one: a live session's
    leaf is dirty every turn and is written at its settle, never here (no steady stream of writes)."""
    out = []
    with _CKPT_LOCK:
        cold = {(k, n) for k, n in _COLD_FOLDS if _COLD_REASONS.get((k, n), "cold") == "cold"}
        paths = sorted(_FOLD_DIRTY | {k for k, n in cold})   # a cold cursor at the witness steps nothing and marks nothing dirty
    for key in paths:
        with _JSONL_CACHE_LOCK:
            ent = _JSONL_CACHE.get(key)
        if ent is None:
            continue
        if _path_needs_write(key, ent):                   # the one rule (T362): dirty, cold for want of a state, or a recordable
            out.append(key)                               #  cursor whose fold has no complete state in the document
    return out


def checkpoint_has_work():
    """Whether any fold cursor moved since its checkpoint was written, or any fold began cold: the converge pass's zero-cost
    gate (no lock, no stat) so a quiet cycle costs it nothing (T361)."""
    return bool(_FOLD_DIRTY) or bool(_COLD_FOLDS)


def file_quiescent(path):
    """Whether `path` has been unchanged for longer than the reader keeps a quiescent file's whole entry
    (_DROP_AFTER_QUIESCENT_S): a fold with drop_after="quiescent" drops that entry right after it steps, so a whole read
    of such a file on the pusher's cycle cannot be held long enough to write its checkpoint from (T361)."""
    try:
        return time.time() - os.stat(str(path)).st_mtime >= _DROP_AFTER_QUIESCENT_S
    except OSError:
        return False


def converge_stat(name, n=1):
    """Count a converge pass's work under checkpoints.converge (the kernel's pass reports through here)."""
    with _CKPT_LOCK:
        _CKPT_STATS["converge"][name] = _CKPT_STATS["converge"].get(name, 0) + n


def checkpoint_write_dirty(paths=None, budget_s=None):
    """Write the checkpoints of `paths` (default: every dirty path); returns how many were written. `budget_s`
    bounds the pass (the exit path, 2026-09-11: an unbounded write over a kernel life's dirty files outran the
    manager's 5 s grace and the SIGKILL lost the cut row): the first file always writes, the pass stops once the
    budget has passed, and what is left stays dirty for the next writer."""
    return len(checkpoint_write_dirty_paths(paths, budget_s))


def checkpoint_write_dirty_paths(paths=None, budget_s=None):
    """checkpoint_write_dirty, returning the paths written (the settle's converge pass skips them that cycle)."""
    out = []
    t0 = time.monotonic()
    for p in (checkpoint_dirty() if paths is None else [str(p) for p in paths]):
        if budget_s is not None and out and time.monotonic() - t0 > budget_s:
            break
        if checkpoint_write(p):
            out.append(p)
    return out


def checkpoint_sweep():
    """One pass over the checkpoint directory at boot: a document whose recorded file no longer exists, or that does
    not parse, is removed (counted as swept), so cleared and removed sessions do not grow the directory forever."""
    d = _ckpt_dir()
    if d is None or not Path(d).is_dir():
        return 0
    gone = 0
    for cp in list(Path(d).glob("*.json")) + list(Path(d).glob("*.asm.json.gz")):
        keep = False
        try:
            meta = cp.with_name(cp.name + ".meta") if cp.name.endswith(".gz") else None
            if meta is not None and meta.exists():
                text = meta.read_bytes()                      # the sidecar: the sweep never inflates a document
            else:
                text = cp.read_bytes()
                if cp.name.endswith(".gz"):
                    text = gzip.decompress(text)
            _count_read(str(cp), len(text))
            doc = json.loads(text.decode("utf-8"))
            keep = isinstance(doc, dict) and isinstance(doc.get("path"), str) and os.path.exists(doc["path"])
        except (OSError, ValueError):
            keep = False
        if not keep:
            try:
                cp.unlink(); gone += 1
                if cp.name.endswith(".gz"):                # an assembly document: counted as removed, its sidecar with it
                    _asm_removed("sweep")
                    cp.with_name(cp.name + ".meta").unlink(missing_ok=True)
            except OSError:
                pass
    with _CKPT_LOCK:
        _CKPT_STATS["swept"] += gone
    return gone


def checkpoint_stats():
    with _CKPT_LOCK:
        out = dict(_CKPT_STATS); out["fallbacks"] = dict(_CKPT_STATS["fallbacks"]); out["restoredFolds"] = dict(_CKPT_STATS["restoredFolds"])
        out["oversizeFolds"] = dict(_CKPT_STATS["oversizeFolds"]); out["coldFolds"] = dict(_CKPT_STATS["coldFolds"])
        out["coldWrites"] = dict(_CKPT_STATS["coldWrites"]); out["converge"] = dict(_CKPT_STATS["converge"])
        out["refolds"] = {k: dict(v) for k, v in _CKPT_STATS["refolds"].items()}
        out["rewoundMemo"] = dict(_REWOUND_STATS)
    d = _ckpt_dir()
    with _READ_BYTES_LOCK:
        out["documentBytes"] = sum(n for p_, n in _READ_BYTES.items() if d is not None and p_.startswith(str(d) + os.sep))
        out["dirty"] = len(_FOLD_DIRTY)
    rb = read_bytes_report()
    out["readBytes"] = rb.pop("total")
    out["readByPath"] = rb                            # per file, so a boot can be read as tails versus whole files
    return out


def _scan_jsonl_bytes(data, base_offset, offsets=None):
    """(records, consumed) for a bytes blob of jsonl starting at base_offset: parsed objects of every
    COMPLETE line, and the byte offset just past the last complete line (a trailing partial is left).
    `offsets`, an array when given, receives each parsed record's (byte offset, byte length) as two
    appended values: the assembly checkpoint names a record by where it sits (T323 stage 4)."""
    end = data.rfind(b"\n")
    if end < 0:
        return [], base_offset
    records = []
    pos = 0
    body = data[:end + 1]
    for line in body.splitlines(keepends=True):
        at, pos = pos, pos + len(line)
        line_s = line.strip()
        if not line_s:
            continue
        try:
            records.append(json.loads(line_s.decode("utf-8", "replace")))
        except Exception:
            continue
        if offsets is not None:
            offsets.append(base_offset + at); offsets.append(len(line))
    return records, base_offset + end + 1


def _entry_offsets_gen(path):
    """(record offsets from record 0, generation) of the reader's entry for `path` from ONE entry tuple under one lock
    acquisition, or (None, None) with no entry or a tail entry: what the assembly writer compares its adapter's source
    key against before trusting the entry's offsets for the records the adapter read (T396 round one, low 2: the
    _asm_gates pattern, never two reads of the cache that could see two entries)."""
    with _JSONL_CACHE_LOCK:
        ent = _JSONL_CACHE.get(str(path))
    if ent is None or len(ent) < 8 or ent[5] > 0:
        return None, None
    offs = ent[7]
    return [(offs[i], offs[i + 1]) for i in range(0, len(offs), 2)], ent[6]


def _read_jsonl_incremental(path, on_fail=None):
    """The parsed records of `path` (a list, NOT a generator), served append-incrementally per the cache
    contract above. Falls back to a full read on any surprise; [] on any error, like _read_jsonl. `on_fail`,
    when given, is called with the exception for a stat, open or read that raised on a file that EXISTS (any
    OSError but FileNotFoundError): an absent file is a state and answers [] quietly, an unreadable one is a
    failure the caller may count and log (fold_records passes it through as on("fail")). The answer is []
    either way. This is the WHOLE-file read: a tail entry a checkpoint restored is upgraded to the whole file
    first (T323 stage 3); fold_records reads the entry itself through _read_jsonl_entry with tail_ok."""
    ent = _read_jsonl_entry(path, on_fail=on_fail, tail_ok=bool(getattr(_TAIL_OK, "flag", False)))
    _LAST_ENTRY.ent = ent
    return ent[4] if ent is not None else []


_TAIL_OK = threading.local()      # .flag: the calling fold accepts a tail entry (set by fold_records around its read)
_READER_TRACE = bool(os.environ.get("ROMP_READER_TRACE"))   # one stderr line per read that pulled bytes (a diagnosis aid)
_WHOLE_READ_KINDS = ("zero", "rewrite", "guard", "shrunk", "upgrade")   # the reader's kinds that pull a file whole (T384's counter)
_WHOLE_READ_PASSTHROUGH = set()   # the CODE objects of the parse family every walker shares (this module's parse_session, the judges'
#                                   parsed_session, parse_cached and _parse_store, the kernel's _parse, each registered where it is
#                                   defined): the whole-read row names the first caller beyond them, the real walker. Matched by code
#                                   object, never by name (round two, low 3: a local helper named like one of them was skipped)


def _synthetic_scope(fr):
    """A comprehension's, generator expression's or lambda's own frame (a code name in angle brackets other than the module's):
    the attribution walks keep going to the enclosing function. Before Python 3.12 a list, set or dict comprehension runs in
    its own frame (PEP 709 inlines them from 3.12 on); a generator expression and a lambda keep theirs on every version."""
    n = fr.f_code.co_name
    return n.startswith("<") and n != "<module>"


def register_whole_read_passthrough(*fns):
    """Register functions the whole-read attribution walks past (the parse family a walker reaches the reader through)."""
    for fn in fns:
        _WHOLE_READ_PASSTHROUGH.add(fn.__code__)
_LAST_ENTRY = threading.local()   # .ent: the entry the last _read_jsonl_incremental on this thread served


_READ_STRIPES = [threading.RLock() for _ in range(64)]   # per-path serialization of the reads that pull bytes: two threads
#                                                            meeting a file's first read at once (the judges' parse and the pusher's
#                                                            folds at boot) used to read it twice and, with a checkpoint in play, the
#                                                            later store's generation orphaned the earlier restore's pending fold
#                                                            states (review find, 2026-09-11); the second thread now waits and hits.
#                                                            Striped by path hash, so at most one in 64 unrelated files waits behind another.


def _read_stripe(path):
    return _READ_STRIPES[hash(path) % len(_READ_STRIPES)]


def _read_jsonl_entry(path, on_fail=None, tail_ok=False, tail_from=None):
    """The reader's cache entry for `path`, current as of this call (the contract at _read_jsonl_entry_unlocked). A hit
    is served under the cache lock alone; a read that would pull bytes runs under the path's stripe lock and re-checks
    the cache first, so concurrent first reads of one file cost one read and one generation. `tail_from`, a
    (byte offset, record count before it, guard bytes) triple, asks for an entry that holds the records from that
    offset on (the assembly checkpoint's cut, T323 stage 4): an entry whose base is at or before it serves as is; a
    missing or later one is read from the offset after the guard verifies, and a guard mismatch reads whole."""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError as e:
        with _JSONL_CACHE_LOCK:
            _cache_pop_locked(path)
        if on_fail is not None and not isinstance(e, FileNotFoundError):
            on_fail(e)
        return None
    with _JSONL_CACHE_LOCK:
        hit = _JSONL_CACHE.get(path)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size and (tail_ok or hit[5] == 0) \
                and (tail_from is None or hit[5] <= tail_from[1]):
            _JSONL_CACHE.pop(path, None)  # reinsert at the LRU tail: a served entry is a USED entry
            _JSONL_CACHE[path] = hit
            return hit
    with _read_stripe(path):
        return _read_jsonl_entry_unlocked(path, on_fail=on_fail, tail_ok=tail_ok, tail_from=tail_from)


def _read_jsonl_entry_unlocked(path, on_fail=None, tail_ok=False, tail_from=None):
    """The reader's cache entry for `path`, (mtime, size, offset, tail, records, base, gen, offsets), current as of this call,
    or None when the file is absent or unreadable (`on_fail` as in _read_jsonl_incremental). With `tail_ok` a caller
    accepts a TAIL entry (base > 0: records[0] is the file's record number base), and a first touch with no entry
    consults the file's checkpoint (T323 stage 3): the recorded offset's guard bytes are verified on disk and only
    offset..EOF is read. Without it a tail entry is upgraded to the whole file from zero (same gen: the prefix stood).
    A mismatch anywhere (the guard, a shrink, a same-size-new-mtime) reads from zero and bumps gen; when the entry
    came from a checkpoint that is a counted fallback."""
    path = str(path)
    try:
        st = os.stat(path)
    except OSError as e:
        with _JSONL_CACHE_LOCK:
            _cache_pop_locked(path)
        if on_fail is not None and not isinstance(e, FileNotFoundError):
            on_fail(e)
        return None
    with _JSONL_CACHE_LOCK:
        hit = _JSONL_CACHE.get(path)
        if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size and (tail_ok or hit[5] == 0) \
                and (tail_from is None or hit[5] <= tail_from[1]):
            _JSONL_CACHE.pop(path, None)  # reinsert at the LRU tail: a served entry is a USED entry
            _JSONL_CACHE[path] = hit
            return hit
    restored = None
    if tail_from is not None and (hit is None or hit[5] > tail_from[1]):
        # the assembly checkpoint's cut: an entry from its offset, when the guard before it stands (else whole)
        t_off, t_base, t_guard = tail_from
        if st.st_size >= t_off:
            keep_gen = hit[6] if hit is not None else _next_gen()   # an earlier start for an existing tail entry keeps its
            hit = restored = (0.0, t_off, t_off, bytes(t_guard), [], int(t_base), keep_gen)   # generation: the folds' cursors stand
    elif hit is None and tail_ok and _CKPT_DIR_FN is not None:
        hit = restored = _checkpoint_entry(path, st)
    try:
        with open(path, "rb") as fh:
            base, gen, done, kind = 0, None, False, "zero"
            grown = hit is not None and (st.st_size > hit[1] or (restored is not None and st.st_size == hit[1]
                                                                    and (st.st_mtime == hit[0] or tail_from is not None)))
            unchanged_tail = (hit is not None and not tail_ok and hit[5] > 0 and st.st_size == hit[1]
                              and st.st_mtime == hit[0])            # a whole reader meets an unchanged tail entry
            if grown or unchanged_tail:
                _, _, offset, tail, records, base0, gen0 = hit[:7]
                fh.seek(max(0, offset - len(tail)))
                _count_read(path, len(tail))
                if fh.read(len(tail)) == tail:            # the file really is our cached prefix + more
                    if tail_ok or base0 == 0:
                        data = fh.read()
                        _count_read(path, len(data))
                        offs = array.array("q", hit[7]) if len(hit) > 7 else array.array("q")
                        new, offset = _scan_jsonl_bytes(data, offset, offs)
                        records = (records + new) if records else new   # a NEW list — never extend the served one in place
                        base, gen, done = base0, gen0, True
                        kind = "restore" if restored is not None else "grown"
                        if restored is not None and tail_from is None:
                            with _CKPT_LOCK:
                                _CKPT_STATS["restored"] += 1
                    else:                                 # a whole reader over a tail entry: the whole file, same gen
                        fh.seek(0)
                        data = fh.read()
                        _count_read(path, len(data))
                        offs = array.array("q")
                        records, offset = _scan_jsonl_bytes(data, 0, offs)
                        base, gen, done, kind = 0, gen0, True, "upgrade"
                else:
                    kind = "guard"                        # prefix changed → a rewrite → full re-read, a fresh generation
                    if restored is not None and tail_from is None:
                        _ckpt_fallback(path, "guard")
            elif hit is not None:
                kind = "shrunk" if st.st_size < hit[2] else "rewrite"   # shrank, or same size under a new mtime: a rewrite
                if restored is not None and tail_from is None:
                    _ckpt_fallback(path, kind)
            if not done:
                fh.seek(0)
                data = fh.read()
                _count_read(path, len(data))
                offs = array.array("q")
                records, offset = _scan_jsonl_bytes(data, 0, offs)
                base, gen = 0, _next_gen()                # a from-zero read: a generation no cursor of this path can hold
            tail_from = max(0, offset - _JSONL_TAIL_GUARD)
            fh.seek(tail_from)
            tail = fh.read(offset - tail_from)
            _count_read(path, len(tail))                  # the guard capture is a read too (/perf's count is what was pulled)
            if kind in _WHOLE_READ_KINDS:                 # a whole read: counted by kind and caller on /perf (T384), always on; the
                try:                                      #  frame walk runs only here, on the rare whole read, never on a tail or an
                    fr = sys._getframe(1)                 #  append
                    while fr is not None and (fr.f_code.co_filename == __file__ or fr.f_code in _WHOLE_READ_PASSTHROUGH
                                              or _synthetic_scope(fr)):   #  past this module, the parse family and any comprehension's
                        fr = fr.f_back                    #  or generator expression's own frame, to the walker (review, low 1)
                    who = fr.f_code.co_name if fr is not None else "?"
                except Exception:
                    who = "?"
                with _JSONL_CACHE_LOCK:
                    table = _RECORD_CACHE_STATS.get("wholeReads")
                    if not isinstance(table, dict):       # a harness that zeroes every counter zeroes this one too: a table again
                        table = _RECORD_CACHE_STATS["wholeReads"] = {}
                    wr = table.setdefault("%s<-%s" % (kind, who), {"count": 0, "bytes": 0})
                    wr["count"] += 1; wr["bytes"] += len(data)
            if _READER_TRACE:
                fr, inner = sys._getframe(1), []          # the caller outside this module, and the path through it
                while fr is not None and fr.f_code.co_filename == __file__:
                    inner.append(fr.f_code.co_name)
                    fr = fr.f_back
                who = ("%s:%d" % (fr.f_code.co_name, fr.f_lineno) if fr is not None else "?") + " via " + "<".join(inner[:6])
                sys.stderr.write("reader: %s %s base=%d gen=%d size=%d by %s\n" % (kind, path, base, gen, st.st_size, who))
    except OSError as e:
        with _JSONL_CACHE_LOCK:
            _cache_pop_locked(path)
        if on_fail is not None and not isinstance(e, FileNotFoundError):
            on_fail(e)
        return None
    ent = (st.st_mtime, st.st_size, offset, tail, records, base, gen, offs)
    with _JSONL_CACHE_LOCK:
        _cache_insert_locked(path, ent)      # the count cap and the byte budget, LRU order (hot entries survive any cold flood)
    return ent


def fold_records(cache, path, init, step, on=None, ckpt=None, drop_after=None):
    """Fold a JSONL file's records into a carried state, APPEND-INCREMENTALLY (issue 903, 2026-09-03):
    the states/transcript readers re-read their whole file behind an (mtime,size) key that every append
    invalidates — O(file) per push for every working session. The reader serves the parsed records of a
    growing file incrementally (the parse reads the same list, so this adds no I/O), and only the appended
    records run through `step`; a rewrite (a shrink, a same-size-new-mtime, a changed prefix under the
    reader's guard) re-folds from record 0. `init()` makes a fresh state; `step(state, record)` returns the
    next state (mutate-and-return is fine: the cached state is deep-copied before folding onto it, so a
    state a caller was handed never changes under it). Returns the state; [] records (a missing or
    unreadable file) fold to init().

    The cursor in `cache` is (count, gen, state): the record count folded and the reader entry's generation
    (T323 stage 3, 2026-09-11; it was (count, last record object, state) gated on the identity of the
    records list's objects, which no checkpoint can carry; the count stays first, as every reader of the
    cursor knew it). A same-gen entry whose count grew is an append; any other gen (a rewrite, a shrink, an
    entry the cache evicted or a failure popped and a later read replaced: every from-zero read takes a
    fresh process-wide generation) refolds. `ckpt`, a name, makes the fold RESUMABLE across processes: its
    cursor is written into the file's checkpoint (checkpoint_write, when it stands at the reader's witness)
    and restored from it at the first fold of the file in a new process, over a TAIL entry that read only the
    bytes past the checkpoint's offset; the fold then steps the tail alone. A refold over a tail entry reads
    the whole file first. Folds without `ckpt` behave as before, over whatever entry the reader holds.

    `on`, when given, is called once per call with the path the fold took: "hit" (the records are the
    cached ones; nothing stepped), "append" (only the records past the cached prefix stepped), "refold"
    (every record stepped: a rewrite, a shrink, or the first fold of this file), "restore" (the cursor came
    from the checkpoint and only the tail past it was stepped) or "fail" (the file exists and its stat, open
    or read raised: the answer is init(), the cache entry for the path is dropped and nothing is memoized, so
    the next call reads again; an ABSENT file is not a failure and folds to init() through the normal path).
    A caller's counters ride it (the kernel's `_states_awaiting_overlay`); the fold itself keeps no counters,
    since one cache dict serves many readers and a caller's counters are locked per reader.
    `drop_after` (the kernel memory work, 2026-09-11): "quiescent" drops the file's records from the shared reader's
    cache once this fold has stepped them, when the file has not changed for _DROP_AFTER_QUIESCENT_S (a subagent
    that returned; the user's rule: nothing stays resident that nobody looks at). The cursor and its checkpoint stand;
    a later fold of an unchanged file re-reads it once, a growing file keeps its records (see _drop_quiescent_entry).

    Lives here (moved from the kernel, 2026-09-03) so the judge's readers can fold too — the
    background-task pairing below is shared by both."""
    key = str(path)
    with _READ_BYTES_LOCK:
        r0 = _READ_BYTES.get(key, 0)                      # what this call reads of the file shows under refolds (T377)
    if ckpt is None:
        ckpt = _FOLD_NAME_OF.get(id(cache))               # a cache named once (name_fold_cache)
    if ckpt is not None:
        _FOLD_REG[ckpt] = cache                           # the newest caller's cursor dict (a test process loads the kernel
        #                                                   several times over one event model; production loads it once)
    failed = []                                           # the reader's failures: a stat, open or read that raised on a
    recs = _tail_read(path, failed)                       # file that exists (an absent file is [] and no failure)
    ent = _pinned_entry(key, recs)                        # the reader's entry for THIS read, pinned by identity: another
    if ent is _UNPINNED:                                  # thread (the judge pool, a handler) may advance the shared entry
        del failed[:]                                     # past our records before we look at its tail, and a newer tail
        recs = _tail_read(path, failed)                   # folded onto an older prefix would skip the records between. A
        ent = _pinned_entry(key, recs)                    # lost pin re-reads once: the newer entry pins cleanly, so the
        if ent is _UNPINNED:                              # answer is current, not a poll behind; lost twice, the fold
            ent = None                                    # answers its own records without a tail, never with the wrong
    if failed:                                            # one. The re-read's verdict is the one that counts.
        cache.pop(key, None)                              # A failed read is never memoized: the next call reads again
        if on is not None:
            on("fail")
        return init()
    base = ent[5] if ent is not None else 0               # no entry (lost twice): the records in hand, whole, no tail

    gen = ent[6] if ent is not None else 0
    total = base + len(recs)
    hit = cache.get(key)
    kind = None
    if ckpt is not None and ent is not None and (hit is None or hit[1] != gen):
        # no cursor, or one at an entry that left memory (the quiescence drop, an eviction) while the file stood: the reader's
        # new entry came from the file's document when it stands, and the document's cursor for this fold serves the fold at
        # that entry (T362: a dropped file's later fold is a tail read, not a whole one); with nothing to restore, a refold.
        # Against a HELD state the document's cursor is taken only when it carries a state of its own (round one, medium): a
        # cursor without one (an over-cap, cold or bare legacy write) would replace the complete state this process holds
        # with a tail-only one that answers empty, and for an idle file nothing would ever heal it; the fold reads whole instead
        restored = _restored_cursor(key, ckpt, ent) if hit is None or _document_carries_state(key, ckpt, ent) else None
        if restored is not None:
            hit = restored
            kind = "restore"
            if hit[2] is _COLD:                           # the cursor without its state: the fold starts at the entry's
                hit = (base, hit[1], init()); kind = "cold"   #  base and steps the tail it holds
                with _CKPT_LOCK:
                    _COLD_FOLDS.add((key, ckpt))          # and stays a tail-only state until a whole refold
    if hit is not None:
        n0, g0, state0 = hit
        if g0 == gen and n0 == total:
            if on is not None:
                on("hit" if kind is None else kind)
            if kind is not None:
                cache[key] = hit                          # a restore at the witness: the cursor stands, nothing stepped
            if drop_after == "quiescent" and ent is not None:
                _drop_quiescent_entry(key, ent, pop=False)   # T362: the document is written here too; the entry stays, since
            #                                                   nothing was stepped (unless a deferred drop is owed)
            return _fold_eof_fragment(key, ent, state0, step)   # unchanged records; a newline-less tail may still sit past them
        if g0 == gen and base <= n0 < total:
            state, start = copy.deepcopy(state0), n0 - base
            kind = kind or "append"
        else:
            kind = None
    if kind is None:
        if base > 0:                                      # a refold needs every record: the entry is a tail, so read whole
            del failed[:]
            ent = _read_jsonl_entry(path, on_fail=failed.append, tail_ok=False)
            if failed:
                cache.pop(key, None)
                if on is not None:
                    on("fail")
                return init()
            recs = ent[4] if ent is not None else []
            gen = ent[6] if ent is not None else 0
            total = len(recs)
        state, start, kind = init(), 0, "refold"
        if ckpt is not None:
            with _READ_BYTES_LOCK:
                got = _READ_BYTES.get(key, 0) - r0
            with _CKPT_LOCK:
                _COLD_FOLDS.discard((key, ckpt))          # every record stepped: the state is complete again
                _COLD_REASONS.pop((key, ckpt), None); _COLD_OVER_KB.pop((key, ckpt), None)
                if got > 0:                               # a refold that read (the whole file, over a tail entry or from zero): named
                    rf = _CKPT_STATS["refolds"].setdefault(ckpt, {"count": 0, "bytes": 0})   #  and weighed per fold on /perf (T377).
                    rf["count"] += 1; rf["bytes"] += got   #  Diagnostic: the delta is over the path's process-wide counter, so another
        #                                                    thread's read of the same file inside this call lands in it
    for r in recs[start:]:
        if isinstance(r, dict):
            state = step(state, r)
    if len(cache) > 256:                                  # bounded by the session count; never unbounded
        cache.clear()
    cache[key] = (total, gen, state)
    if ckpt is not None:
        with _CKPT_LOCK:
            _FOLD_DIRTY.add(key)
    if on is not None:
        on(kind)
    out = _fold_eof_fragment(key, ent, state, step)
    if drop_after == "quiescent" and ent is not None:
        _drop_quiescent_entry(key, ent)
    return out


def _drop_quiescent_entry(key, ent, pop=True):
    """drop_after="quiescent" (the kernel memory work, 2026-09-11): a file unchanged for _DROP_AFTER_QUIESCENT_S is one
    whose writer has finished (a subagent that returned), and its records are not kept in the shared reader's cache
    once a fold has stepped them: the fold's cursor (and its checkpoint) stands, the file's bytes leave memory. Only the
    very entry this fold read is dropped (another thread's newer entry is left alone); a file still changing keeps its
    records, since its next append would otherwise re-read it whole. Without `pop` (a fold that stepped nothing: a hit or
    a restore at the witness) only the T362 write below runs and the entry stays, as it always has on that path. Returns
    True when the document was written here."""
    try:
        if time.time() - float(ent[0]) < _DROP_AFTER_QUIESCENT_S:
            return False
    except Exception:
        return False
    held = getattr(_DROP_HOLD, "held", None)
    if held is not None:                                  # the converge pass is healing and priming this leaf on this thread: held,
        held[key] = held.get(key, False) or pop           #  paid once after (pay_held_drops), with the pop if any fold stepped
        return False
    with _CKPT_LOCK:
        if key in _DROP_OWED:                             # a drop deferred for the budget is taken at the next fold over the file
            _DROP_OWED.pop(key, None); pop = True         #  (whichever path that fold takes) or at the next cycle's start
    # T362: the entry a quiescent-drop fold ends over is a read that already happened (the boot's, by whichever fold read the
    # file whole); if the file's document lacks something this process holds (the one rule), it is written NOW, before any
    # pop, from that read: the idle sessions' documents converge here, where no settle reaches them and the converge pass must
    # refuse them (a heal on the cycle re-read them whole). The write runs with neither lock held (checkpoint_write takes
    # _CKPT_LOCK and _JSONL_CACHE_LOCK itself); it charges the pusher cycle's byte budget shared with the pass, and over the
    # budget the write AND the drop are deferred by one cycle (the entry stays; the read is not lost), counted under
    # checkpoints.converge.dropDeferred.
    try:
        needs = checkpoint_drop_writes_on() and _path_needs_write(key, ent, at_drop=True)   # off (a cap of 0): the pop alone
    except Exception:
        needs = False
    wrote = False
    if needs:
        cp = _ckpt_file(key)
        try:
            est = cp.stat().st_size if cp is not None and cp.exists() else 0
        except OSError:
            est = 0
        if est <= 0:                                      # no previous document (a first write, or one the fallback removed): an
            est = max(4096, int(ent[1]) // 8)             #  estimate from the file, never a free pass against the budget
        if not checkpoint_cycle_take(2 * est):           # the previous document read for the carry, and about as much written:
            oldest = None                                 #  taken in one step, or deferred (the drop owed) when it does not fit
            with _CKPT_LOCK:
                _CKPT_STATS["converge"]["dropDeferred"] += 1
                if pop:
                    _DROP_OWED[key] = time.time()
                    if len(_DROP_OWED) > _DROP_OWED_MAX:  # over the bound the OLDEST owed drop is paid by its pop alone (round
                        oldest = next(iter(_DROP_OWED))   #  two, low 1: a cleared mark left its entry neither paid nor popped)
                        _DROP_OWED.pop(oldest, None)
            if oldest is not None:
                _pop_owed_entry(oldest)                   # outside _CKPT_LOCK: the reader's lock alone
            return False
        if checkpoint_write(key):
            try:
                written = cp.stat().st_size if cp is not None else 0
            except OSError:
                written = 0
            checkpoint_cycle_charge(written - est)       # the true-up: what was written against the estimate taken above
            wrote = True
            with _CKPT_LOCK:
                _CKPT_STATS["converge"]["dropWrites"] += 1
    if not pop:
        return wrote
    with _JSONL_CACHE_LOCK:
        if _JSONL_CACHE.get(key) is ent:
            w = _cache_pop_locked(key)
            _RECORD_CACHE_STATS["dropped"] += 1
            _RECORD_CACHE_STATS["droppedBytes"] += w
    return wrote


_UNPINNED = object()


def _tail_read(path, failed):
    """fold_records' read: the whole-list reader (the seam every fold shares, so a test's stub or counter on
    _read_jsonl_incremental still sees every fold read) with the tail flag raised, so a checkpoint-restored tail
    entry is served instead of upgraded to the whole file."""
    _TAIL_OK.flag = True
    try:
        return _read_jsonl_incremental(path, on_fail=failed.append)
    finally:
        _TAIL_OK.flag = False


def _pinned_entry(key, recs):
    """The shared reader's cache entry for `key` if it still describes the read that returned `recs` (its
    records list IS `recs`), None when the reader holds nothing for the path, _UNPINNED when another thread
    has advanced the entry past that read. The entry the reader served on this thread is checked first
    (T323 stage 3: an entry evicted from the cache between the read and the pin still describes the read)."""
    last = getattr(_LAST_ENTRY, "ent", None)
    if last is not None and last[4] is recs:
        return last
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
        _count_read(path, ent[1] - ent[2])
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
def author_of(blocks, prompt_source, postal_index, sdk_human=False, origin=None):
    """human | romp | sdk | system | teammate | {"peer": <rompUuid|None>, "mid": <id>, "kind": <kind|"">} | None.

    Order matters: the postal marker wins over promptSource (a delivered message can
    arrive with any promptSource). A tool_result-only user atom has no author.

    origin: the record's own `origin` field — the CLI's PROVENANCE stamp on every turn it injects on its
    own (claude_agent_sdk MessageOrigin: kind task-notification / peer / coordinator / channel /
    auto-continuation / observer / unclassified…; "human" or absent for the person's own prompt). Read
    FIRST (the user 2026-09-07): the text shapes below are how each kind happened to be worded by one CLI
    build, and 2.1.263 reworded the background-task notification (a preamble ahead of the tag), which
    made every one of them a blue human bubble. The field is the authoritative source; the text tests
    stay as the fallback for records without it. Any kind that is not "human" is NOT the human, whatever
    sdk_human says — that flag exists only to read an UNSTAMPED "sdk" prompt as the composer's.

    A peer author carries the marker it resolved, not just the sender: one delivery can hold
    several messages, so every later reader (the judge's _seg_peer / _seg_peer_kind) must be
    told WHICH one this author came from rather than re-scanning and picking a different one.

    sdk_human: this session is SDK-backed, so its HUMAN input arrives over the programmatic
    stream-json channel as promptSource "sdk" (the human typed it in the composer). romp's own
    injections still carry the romp-injected marker and peers the postal marker (both handled
    above), so an UNMARKED "sdk" prompt here is the human → render it as the blue human bubble.
    Off (the default) elsewhere, where "sdk" means a genuine programmatic/autonomous injection."""
    okind = origin.get("kind") if isinstance(origin, dict) else None
    osub = origin.get("subkind") if isinstance(origin, dict) else None
    # A peer STAMP is kind "peer" OR a task-notification whose subkind is "peer-send-message", the SDK's other
    # task-channel subkind (claude_agent_sdk TaskNotificationOriginSubkind): a message sent from another of the
    # user's sessions. Another session's words, classified exactly like kind "peer": never a background
    # task's report, so it neither opens nor folds into a task turn, and the task channel's own preamble on
    # its text cannot re-read it as one (review find, 2026-09-09, on #1099).
    peer_stamp = okind == "peer" or (okind == "task-notification" and osub == "peer-send-message")
    if okind == "task-notification" and not peer_stamp:
        if osub == "scheduled-trigger":
            return "sdk"                          # a scheduled task's fired PROMPT: programmatic, but real work follows
        return "system"                           # a background task's completion → folds in, never a goal
    text = _text_of(blocks)
    if text:
        if SYSTEM_WRAPPER_RE.match(text) and not peer_stamp:   # a harness <task-notification> / <system-reminder> / its preamble
            return "system"                       # → author 'system' so _is_opener folds it in, never a goal
        if SCHEDULED_PREAMBLE_RE.match(text) and not peer_stamp:   # a scheduled task's fired prompt, unstamped → programmatic prompt
            return "sdk"
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
    if okind and okind != "human":
        # Stamped as injected, and no romp/postal marker claimed it above: a peer session's (or an
        # in-process background subagent's) message, by kind "peer" or the task channel's "peer-send-message"
        # subkind → the teammate card; everything else the CLI injects (coordinator, channel,
        # auto-continuation, observer…) → a programmatic prompt.
        return "teammate" if peer_stamp else "sdk"
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
    if not out:
        # the 2.1.263 cross-session envelope: <cross-session-message from="…" [from-name="…"]>body</…>. The
        # sender's display name when the CLI gave one, else its address; the CLI's "This came from another
        # Claude session…" boilerplate around the block falls away like the teammate wrapper's.
        for attrs, body in CROSS_SESSION_BLOCK_RE.findall(text or ""):
            a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
            out.append({"id": (a.get("from-name") or a.get("from") or "").strip(),
                        "summary": "", "body": body.strip()})
    return out


# The `origin` keys the atom carries — the SDK's documented per-kind fields (claude_agent_sdk MessageOrigin).
# `body` is the peer message with the envelope stripped, "byte-exact with what the model saw" — the SDK
# says to render it rather than re-parse the text, so it rides (capped like every other body).
_ORIGIN_KEYS = ("kind", "subkind", "name", "from", "server", "senderTaskId", "body")


def _record_origin(rec):
    """A transcript record's provenance stamp as the atom carries it, or None. Two record shapes: a user
    record's top-level `origin`, and a queued_command ATTACHMENT's (the mid-turn splice), which either
    carries `origin` outright or says `commandMode: "task-notification"` — the same fact, older spelling."""
    o = rec.get("origin")
    if not isinstance(o, dict):
        att = rec.get("attachment") if rec.get("type") == "attachment" else None
        if isinstance(att, dict):
            o = att.get("origin")
            if not isinstance(o, dict) and att.get("commandMode") == "task-notification":
                o = {"kind": "task-notification"}
    if not isinstance(o, dict) or not isinstance(o.get("kind"), str) or o["kind"] == "human":
        return None            # a "human" stamp says nothing the author does not; only INJECTED kinds ride
    out = {k: o[k] for k in _ORIGIN_KEYS if isinstance(o.get(k), str)}
    if out.get("body") and len(out["body"]) > _RESULT_CAP:
        out["body"] = out["body"][:_RESULT_CAP]
    return out


def strip_harness_preamble(text):
    """(text without the CLI's harness preamble paragraph, the paragraph) — for a record ALREADY known
    not to be the human's. The paragraph is the CLI's fixed note to the model ("[SYSTEM NOTIFICATION -
    NOT USER INPUT] This is an automated background-task event…"); the chat shows the record under its
    source label and keeps the paragraph one click away in the card's fold, never as the message."""
    if not text:
        return text, ""
    m = HARNESS_PREAMBLE_PARA_RE.search(text)
    if not m:
        return text, ""
    pre = m.group(0).strip()
    rest = (text[:m.start()].rstrip() + "\n\n" + text[m.end():].lstrip()).strip()
    return rest, pre


def injected_source(author, origin, reminders=(), preamble=""):
    """The SOURCE a harness-injected user-role record is shown under (the chat's notice head), or None for
    the human's own words. `kind`: "subagent" (a background agent came to rest — name = its description,
    the one the model itself sees in the notification's <summary>), "task" (a background command),
    "system" (a harness notice: a bare reminder, a scheduled task's firing, an automatic continuation…),
    "peer" (another session / the coordinating session / an MCP channel — postal has its own card and
    never reaches here). Pure: the kernel's chat build and its tests read it the same way.

    preamble: the harness paragraph strip_harness_preamble lifted off the record's text, when there was
    one. It is the fallback that names an UNSTAMPED scheduled firing (an older CLI, the live echo): the
    stamped path labels it "Scheduled task", and the text path must not hand the same record back as an
    unlabelled neutral note (review find, 2026-09-09, on #1099).

    The user asked whether the model can tell WHICH subagent a notification came from (2026-09-07): it
    can — the notification names the task id, the launching tool-use id and the agent's description in
    its <summary> ("Agent \"<description>\" came to rest"), so the head shows at least that description."""
    if author == "human" or author == "romp" or isinstance(author, dict):
        return None
    okind = (origin or {}).get("kind") if isinstance(origin, dict) else None
    sub = (origin or {}).get("subkind") if isinstance(origin, dict) else None
    # kind "peer", or the task channel's "peer-send-message" subkind (a message from another of the user's
    # sessions): the peer notice, never a finished background task, so the notification-naming loop below
    # never runs for it either (review find, 2026-09-09, on #1099). Same rule as author_of's peer_stamp.
    peer_stamp = okind == "peer" or (okind == "task-notification" and sub == "peer-send-message")
    if peer_stamp:
        # a "peer-send-message" stamp carries none of kind "peer"'s sender fields, so it reads "another session"
        return {"kind": "peer", "name": origin.get("name") or origin.get("from") or "another session",
                "subagent": bool(origin.get("senderTaskId"))}
    # The notification names its task only when the RECORD is the notification: a system-authored (or
    # author-less live) record, or one stamped task-notification. An unstamped programmatic prompt that
    # merely arrived with a notification attached keeps today's neutral note with the card nested under it.
    for r in (reminders or ()) if (author in ("system", None) or okind == "task-notification") else ():
        note = _parse_task_notification("<task-notification>%s</task-notification>" % r)
        if not note or not (note.get("summary") or "<task-id>" in r):
            continue                     # a plain reminder, not a task's notification (the parser defaults a status)
        summ = note.get("summary") or ""
        m = re.search(r'Agent\s+"([^"]+)"', summ)
        if m:
            return {"kind": "subagent", "name": m.group(1), "status": note["status"]}
        m = re.search(r'Background command\s+"([^"]+)"', summ)
        if m:
            return {"kind": "task", "name": m.group(1), "status": note["status"]}
        return {"kind": "task", "name": re.sub(r"\s+(?:came to rest|completed).*$", "", summ).strip() or "task",
                "status": note["status"]}
    if okind == "task-notification":
        if sub == "scheduled-trigger":
            return {"kind": "system", "label": "Scheduled task"}
        return {"kind": "task", "name": "background task", "status": ""}
    if okind == "coordinator":
        return {"kind": "peer", "name": "the coordinating session", "subagent": False}
    if okind == "channel":
        return {"kind": "peer", "name": origin.get("server") or "a channel", "subagent": False}
    if okind == "auto-continuation":
        return {"kind": "system", "label": "Automatic continuation"}
    if okind in ("observer", "observer-activity"):
        return {"kind": "system", "label": "Observer report"}
    if okind and okind != "human":
        return {"kind": "system", "label": okind.replace("-", " ")}
    if author == "system":
        return {"kind": "system", "label": "System reminder"}   # a bare <system-reminder> record, unstamped
    if preamble and SCHEDULED_PREAMBLE_RE.match(preamble):
        # unstamped, but the lifted preamble itself says what fired the prompt: the same label the stamped
        # path gives it, with the preamble kept on the event for the card's fold: nothing the text path
        # labelled before becomes an unlabelled note (review find, 2026-09-09, on #1099)
        return {"kind": "system", "label": "Scheduled task"}
    return None   # an unstamped 'sdk' prompt keeps today's neutral note; a teammate has its own card


# ═════════════════════════ FILE ADAPTER: graph recovery, quarantined ═════════════════════════
def _th(text):
    """The carry's text key: sha1 of the text. The dedup sets compare for equality only, so a hash serves them, and
    the assembly checkpoint can carry the sets without carrying every prompt ever typed (T323 stage 4)."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _emit_state():
    """The emit layer's cross-record carry — everything the per-record emit reads that EARLIER
    records established. A full parse starts one empty and folds every kept record through it;
    the assembly cache (_assemble) persists it per session and folds only appended records.
    Field-for-field these are the old atoms() locals, hoisted so both paths share one
    implementation and cannot drift."""
    return {"replay": set(),           # uuids classified post-compaction replays (never atoms)
            "seen_exact": set(),       # (second, text hash) of every kept user text — verbatim re-writes
            "seen_text": set(),        # text hashes ever seen — the restore-burst dedup's memory
            "compacted": False, "restoring": False, "last_boundary": None,
            "summaries": {},           # boundary uuid -> its compaction summary text
            "skill_ids": set(),        # Skill tool_use ids among kept assistants
            "cmd_names": {},           # promptId -> {command names} (slash-invocation twins)
            "skill_loads": {},         # wrapper uuid -> skill name: the harness's own skill loads the emit skips (T333)
            "absorbed_keys": set(),    # _absorbed's (ts, hash of the collapsed text) dedup memory
            "postal_miss_rec": set(),  # kept records whose postal marker missed the index
            "postal_miss_att": set(),  # absorbed (ts, hash of the collapsed text) keys likewise
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

    def __init__(self, candidate_files, leaf_path, leaf_override=None, resume_links=None, seed=None):
        self.seed = seed         # the assembly checkpoint's pre-cut graph (T323 stage 4): uuids, verdicts, types, the kept
        #                          chain, the gate facts and the read-counter floor of every record before the cut, so the
        #                          walks and gates over the tail answer as the whole graph would; None for a whole read
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
        self._seq_ts = []        # (seq, repaired ts) per uuid-bearing record, in read order — sorted by
        #                          construction, so _landing_t bisects it for a record's file-order
        #                          predecessor without a per-lookup scan or a per-fold sort
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
        self._src_keys = {}      # path -> the reader's (gen, base, count) the records came from (the fold's identity gate)
        self._src_stat = {}      # path -> the (size, mtime) of the file AS READ for those records: the witness a document row
        #                          carries for a file wholly before the cut, never the write-time stat (T396 round one)
        if seed is not None:
            self._seq = int(seed["seq_base"])
            self.prompt_ids |= seed["prompt_ids"]; self.boundary_pids |= seed["boundary_pids"]
            self.skill_use_ids |= seed["skill_use_ids"]; self.src_tool_links |= seed["src_tool_links"]
            self.dangling |= seed["dangling"]
            if seed.get("seq_ts") is not None:
                self._seq_ts.append(tuple(seed["seq_ts"]))   # the last pre-cut witness: a splice right after the cut lands on it
            self._last_ts = float(seed.get("last_ts") or 0)
        for fp in files:
            fsid = Path(fp).stem
            cut = (seed or {}).get("cuts", {}).get(fsid)      # (offset, base, guard) for a file read from its cut, "skip" for
            if cut == "skip":                                   #  a file wholly before the cut (a fork's prior file, immutable)
                self._src[str(fp)] = []
                self._src_keys[str(fp)] = ("skip",)
                st_skip = (seed or {}).get("stat", {}).get(fsid)   # the document's verified witness for it (the load checked it)
                if st_skip is not None:
                    self._src_stat[str(fp)] = tuple(st_skip)
                continue
            if cut is not None:
                ent = _read_jsonl_entry(fp, tail_ok=True, tail_from=tuple(cut))
            else:
                ent = _read_jsonl_entry(fp, tail_ok=False)
            recs = ent[4] if ent is not None else []
            self._src[str(fp)] = recs
            self._src_keys[str(fp)] = (ent[6], ent[5], ent[5] + len(recs)) if ent is not None else (None, 0, 0)
            if ent is not None:
                self._src_stat[str(fp)] = (ent[1], ent[0])    # the reader's (size, mtime) for this very read
            if cut is not None and ent is not None and ent[5] < cut[1]:
                recs = recs[cut[1] - ent[5]:]            # the entry holds records before the cut (a whole reader came
            #                                              first): the seed stands for those, ingest from the cut on
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
                if t != "attachment":
                    # an attachment is never a landing WITNESS (a queued_command IS the spliced item,
                    # stamped with its send time): several sends spliced at one boundary must all
                    # read that boundary, not each other — see _landing_t
                    self._seq_ts.append((seq, ts))
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
        for fs, ends in ((self.seed or {}).get("file_ends") or {}).items():   # files (or file heads) before the cut
            if ends[0]:
                first_of[fs] = ends[0]
            if ends[1]:
                last_of[fs] = ends[1]
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
        known = (self.seed or {}).get("verdicts") or {}   # records before an assembly checkpoint's cut (T323 stage 4)
        for u, r in self.by_uuid.items():
            if r.get("type") != "system" or r.get("subtype") != "compact_boundary":
                continue
            target = self.parent_of.get(u)
            if target is None or target in self.by_uuid or target in known:
                continue                          # no stitch, or stitch is intact
            seg = (r.get("compactMetadata") or {}).get("preservedSegment") or {}
            for k in ("tailUuid", "anchorUuid", "headUuid"):   # tail = the pre-compaction leaf
                cand = seg.get(k)
                if cand and (cand in self.by_uuid or cand in known):
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
        seed = self.seed
        seed_types = seed["types"] if seed is not None else {}
        seed_verdicts = seed["verdicts"] if seed is not None else {}
        # spine child map (parent -> its child ON the spine), for the fork-kind probe below
        spine_child, _u, _guard = {}, self.leaf_uuid, 0
        while _u is not None and _guard < 500000:
            _p = self.parent_of.get(_u)
            if _p is None or _p in spine_child:
                break                              # root, or a cycle already crossed
            spine_child[_p] = _u
            _u = _p; _guard += 1
        if seed is not None:                       # the pre-cut spine, root to cut, so a probe from a pre-cut fork walks on
            sp = seed["spine"]
            for i in range(len(sp) - 1):
                spine_child.setdefault(sp[i], sp[i + 1])
        _fork_memo, _fork_terminal = {}, {}

        def _type_of(u):
            r = self.by_uuid.get(u)
            if r is not None:
                return r.get("type"), r.get("subtype")
            return seed_types.get(u, (None, None))
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
                t, sub = _type_of(u)
                if t == "assistant":
                    break
                if t == "user":
                    if saw_err:
                        res = "eclipsed"
                        _fork_terminal[f] = "user"
                    break
                if t == "system" and sub == "api_error":
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
                    sv = seed_verdicts.get(u)      # a record before the cut: the checkpoint recorded its verdict
                    if sv == "active":
                        res = fork_kind(u); break  # the chain rejoins the pre-cut spine
                    if sv is not None:
                        res = sv; break
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
        latest-written reply text, tiebroken by head seq. The text witness is the eclipse
        set's text-bearing assistant records (landed_text_uuids' own test, applied to the
        eclipse set alone — the ranking reads it for sub-tree members only, and the sub-trees
        lie inside that set) MINUS isApiErrorMessage records (error-as-text failure echoes,
        which atoms() likewise refuses to treat as replies) — so junk carries no weight in the
        key, and no textless tail can move the pick whatever its file position. The unit is
        deliberately the WHOLE branch, never a leaf-chain within it: leaf-chains of one branch
        share records, and every per-chain read of shared state proved breakable (a full-chain
        gate laundered a junk tail's steal; a unique-suffix gate let a branch's own twin
        sub-branches strip their turn's text and hand the keep to an older sibling — reply
        loss, this repo's one fatal error). The accepted cost is cosmetic: a stub twin inside
        the winning branch renders one output-less duplicate tool row, and a grafted burst
        inside it costs nothing (system records never become atoms). The qualifying properties
        are the ones every incident's salvaged branch has in the author's corpus scan (49/49
        across 4,678 transcripts; the 2,809 non-eclipse rewind forks: 2,035 stub pairs, 700
        superseded retries, 42 user-gesture rollbacks, 32 other; zero overlap). Sibling
        branches demote to "rewind", exactly as their on-spine twins classify.

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
        # Everything below reads only uuids INSIDE the eclipse set, so build only that much: the
        # child map over ecl (the component walk skips any popped uuid outside ecl, and a sub-tree
        # walk skips anything outside its component, so children outside ecl were never followed)
        # and the text witness over ecl (the ranking tests it for sub-tree members only). A
        # whole-graph child map and landed_text_uuids() over every record made this walk a third
        # of chain_membership's cost on a large transcript with a handful of eclipsed records; the
        # verdicts are identical. Nothing here is cached on the adapter: _run_graph_passes mutates
        # parent_of after ingest, so a per-adapter child map would go stale under the repair passes.
        children = {}                     # parent -> its children in ecl
        forks = {}                        # fork uuid -> its eclipsed branch heads
        for u in ecl:
            p = self.parent_of.get(u)
            if p is None:
                continue
            children.setdefault(p, []).append(u)
            if p in active:
                forks.setdefault(p, []).append(u)
        landed = set()                    # text-bearing assistant uuids in ecl (the reply witness)
        for u in ecl:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "assistant" and _text_of(_content(r.get("message"))).strip():
                landed.add(u)
        for F, heads in forks.items():
            comp, stack = set(), list(heads)
            while stack:                  # the branch component: child-closure of the eclipsed heads
                x = stack.pop()
                if x in comp or x not in ecl:
                    continue
                comp.add(x)
                stack.extend(children.get(x, ()))
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
        """One synthesized user atom for a mid-turn splice, placed where the model READ it (T252d, the
        user 2026-09-08): its `t` is the LANDING time — the moment the CLI took the message off its
        queue, the file-order predecessor's stamp (_landing_t) — so the atom sits BELOW the steps that
        ran while the message waited, and the order on screen is the order the model saw. The SEND time
        stays on the atom as `sentAt` (the chat's bubble hover). Ordering rule: atoms sort by (t, _seq)
        (parse_session), and the attachment's FILE ORDER is the authority — the atom sorts after every
        record the CLI wrote before taking it (the witness's stamp, and a higher seq than the witness)
        and before the assistant record that answers it (written after the attachment, stamped no
        earlier); several sends the CLI took at one boundary share that boundary's stamp and keep
        their send order, which is their file order. A send the CLI took AFTER a witness stamped
        before the send clamps to the send time (no truthful landing precedes it); an attachment with
        no predecessor in the read keeps its send time as the only truthful place. Before T252d the
        atom sat at its send time, above those steps (the T252 in-place rule); the user's call was
        that the read position is the one that matches what the model actually saw.

        DEPLOY RULE: `t` is half of the segment id (fsid:t:texthash), so moving it changed placement
        identity for every absorbed atom whose landing differs from its send — PLACEMENTS_V 12 (the
        seal keeps dormant sessions from replaying the moved atoms as new goals);
        tests/test_placements_canary.py pins the new derivation.

        The atom carries the FULL text — any whitespace-collapsed form is for MATCHING only (the user
        2026-07-08: collapsing ate the blank line between a follow-up's quoted context and the typed
        reply, so markdown folded the reply INTO the blockquote; and the kernel's optimistic echo could
        never text-prune against the collapsed copy, so the message rendered TWICE)."""
        blocks = [{"type": "text", "text": full}]
        arec = self.by_uuid.get(auid) or {}
        origin = _record_origin(arec)   # a queued_command attachment carries its own origin / commandMode
        atom = {
            "type": "user", "uuid": auid, "session_id": rompuuid,
            "t": t, "sentAt": t, "fsid": self.fsid_of.get(auid),
            "parentUuid": arec.get("parentUuid"),
            "message": {"role": "user", "content": blocks},
            "author": author_of(blocks, None, postal_index, getattr(self, "sdk_human", False), origin),
            "absorbed": True,   # a mid-turn splice, placed where the model read it (T252d): the
            #                     atoms that FOLLOW it are the model's work after reading it — its
            #                     reply, as for any ask. (Until T252d the atom sat at its SEND time and
            #                     the following atoms were the interrupted turn's work, which the judges
            #                     had to refuse as evidence; that leg is gone with the placement.) The
            #                     flag still tells the chat and the judges how the message arrived.
            "_seq": seq,
        }
        if origin:
            atom["origin"] = origin
        landed_t = self._landing_t(seq)
        if landed_t is not None:
            if landed_t < t:
                # The witness (the attachment's file-order predecessor) is stamped BEFORE the
                # attachment's own ENQUEUE stamp. Real transcripts do this only by clock granularity
                # (the live corpus's worst case is -0.2 s, which whole-second stamps can turn into a
                # 1 s inversion); anything larger is a shape the CLI does not write. No truthful
                # landing precedes the send, and the chat's cue must never read "took it at" a time
                # before the bubble's own send time — so clamp to the send, and COUNT it: parse stats
                # (`landedT-clamp`, beside ts-repair, served on the version route) are where a run of
                # these would show up, since a silent clamp would hide a CLI write-order change. (The
                # 2026-09-06 review: two goldens had pinned a landedT 30-40 s before the send, from a
                # synthetic shape with no tool_result before the attachment.)
                _ASM_STATS["landedT-clamp"] = _ASM_STATS.get("landedT-clamp", 0) + 1
                landed_t = t
            atom["t"] = landed_t         # placed where the model READ it (T252d); `sentAt` keeps the send
        if ROMP_AUTO_RE.search(full):   # an AUTO-nudge → flag it, mirroring the native user-record path
            atom["rompAuto"] = True
        return atom

    def _landing_t(self, seq):
        """When the CLI TOOK a mid-turn prompt: the repaired stamp of the record written just BEFORE
        the queued_command attachment in file order. The attachment's own stamp is the ENQUEUE time
        (the moment the user sent it — kept on the atom as `sentAt`), but the CLI writes the record at
        the splice, right after the tool boundary it waited for, so that boundary's own record — the
        file-order predecessor — is the landing moment to within the boundary's latency, and since
        T252d it is where the atom is PLACED (its `t`): below the steps that ran while the message
        waited, where the model read it. The PREDECESSOR, not the successor, on purpose: it is always ingested when
        the atom is emitted, so a fold that sees the attachment as the newest record still stamps
        it — a successor read would find nothing there, and no later fold re-emits the atom (the
        (ts, text) dedup). Attachment records are skipped as witnesses (a run of splices at one
        boundary all read that boundary). None only when nothing precedes the attachment in the
        read. The caller clamps the result to the atom's own send time (never earlier — see
        _absorbed_atom). Placement, not metadata, since T252d: it is half of the atom's segment id."""
        i = bisect.bisect_left(self._seq_ts, (seq,)) - 1
        return self._seq_ts[i][1] if i >= 0 else None

    def _absorbed(self, qatts, kept, st, rompuuid, postal_index):
        """Mid-turn prompts spliced into a running turn. The witness is the queued_command
        ATTACHMENT record: the CLI writes one per splice, uuid-bearing and parent-chained,
        carrying the FULL prompt text and stamped with the ENQUEUE timestamp — and writes
        NONE for a dequeued prompt (that resurfaces as a native user line), a still-pending
        one, or a popAll (a recall: the queue is cleared, nothing spliced). So each
        attachment becomes one user atom, placed at its LANDING time (_absorbed_atom, T252d) with
        the send time beside it; the (send ts, text) pair stays the dedup key below.

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
            key = (q["ts"], _th(" ".join(q["text"].split())))
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
        self.skill_loads = st["skill_loads"]   # read by _assemble beside the atoms (T333)
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
                    th = _th(txt)
                    key = (int(self.ts_of.get(u) or 0), th)
                    if key in _seen_exact or (_compacted and _restoring and th in _seen_text):
                        replay_uuids.add(u)
                    else:
                        _seen_exact.add(key)
                        _seen_text.add(th)
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
        cmd_prompt_names, skill_loads = st["cmd_names"], st["skill_loads"]
        for u in order:
            r = self.by_uuid.get(u) or {}
            if r.get("type") == "user":
                btext = _text_of(_content(r.get("message"))) or ""
                if is_skill_load_wrapper(btext):   # the harness's own skill load: no invocation, so no twin to drop;
                    m = COMMAND_NAME_ANY_RE.search(btext)   # recorded for the judge's stamp (session["skillLoads"]),
                    skill_loads[u] = (m.group(1).strip() if m else "") or "skill"   # across every file this walk crossed
                    continue
                if not r.get("promptId"):
                    continue
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
                if is_skill_load_wrapper(btext):
                    continue   # the harness's own skill load (bare name + <skill-format>): noise, never a command turn
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
                origin = _record_origin(r)
                if origin:
                    atom["origin"] = origin      # the CLI's provenance stamp rides the atom (the chat's source head)
                author = author_of(blocks, ps, postal_index, getattr(self, "sdk_human", False), origin)
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
                       # T279: the refusal category (an open string; null when neither lane carried one),
                       # the API's explanation (display-only prose; null on server-lane banners) and the
                       # scope ('session' = the session model is swapped; 'local' = a subagent's or a
                       # side question's reply only; absent on older CLIs = session)
                       "refusal_category": r.get("apiRefusalCategory") or "",
                       "refusal_explanation": r.get("apiRefusalExplanation") or "",
                       "scope": r.get("scope") or "session",
                       "_seq": seq}
            # other system subtypes (turn_duration, stop_hook_summary, local_command,
            # away_summary) are harness bookkeeping, not conversational messages -> skipped.


# A session is NOT working once it has STOPPED: the states/ log records state:"waiting" when a turn's result
# lands (the agent handed the floor back — the SDK backend writes it on the ResultMessage; the tmux backend's
# Stop hook did until its removal, 2026-09-11) and state:"idle" for a stop without one (an interrupt now; the
# idle-prompt, then). Both terminate the turn — keying
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


def synthesize_orphans(states, atoms, landed_text_uuids=None, rompuuid=None, pre=None, t_floor=None):
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
    if t_floor is not None:                                           # a restored parse: only a marker the tail can hold counts (the
        states = [r for r in states or [] if isinstance(r, dict) and (r.get("t") or 0) >= t_floor]   #  caller discards the rest)
    if not any(isinstance(r, dict) and r.get("t") and isinstance(r.get("orphanReply"), dict) for r in states or []):
        return []                                                     # no marker: nothing to salvage, and no pre-cut row decoded
    # TEXT-BEARING uuids only (the user 2026-07-28): a marker whose uuid the disk knows solely as a
    # TEXTLESS record must still interleave. On some model+tool combinations (observed: fable-5 replying
    # before an AskUserQuestion) the CLI persists the streamed reply text as an EMPTY thinking record
    # under the same uuid — the very loss the marker salvages — so counting that twin as "seen" ate the
    # salvage. A retry that DID re-reply carries its text and still dedups, as does a re-orphaned marker
    # (the add below); the prefix check against disk_texts guards every remaining double.
    lazy = [a for a in atoms if a.get("lazy") is not None]           # atoms before an assembly checkpoint's cut: no body
    seen_uuids = {a.get("uuid") for a in atoms
                  if a.get("uuid") and (a["lazy"].get("nt") if a.get("lazy") is not None else _text_of(_content(a.get("message"))).strip())}
    pre_rows = []                                                     # (turn, slot, row, type, has text, hash) of the restored pre-cut
    if pre:                                                           #  atoms (stage 4c), one decode per row
        for pt in pre:
            la = pt["atoms"]
            for k, r in enumerate(la.rows()):
                typ, nt, hh = la._index.text_flags(r)
                pre_rows.append((la, k, r, typ, nt, hh))
                if nt:
                    u = la._index.uuid_of(r)                           # text-bearing uuids from the rows: no atom built
                    if u:
                        seen_uuids.add(u)
    disk_texts = [t for a in atoms if a.get("type") == "assistant" and a.get("lazy") is None
                  if (t := _text_of(_content(a.get("message"))).strip())]
    older = [None]                                                    # the lazy assistants' texts, hydrated once, only if a marker needs them

    pre_hashes = {hh for la, k, r, typ, nt, hh in pre_rows if typ == "assistant" and hh}   # the pre-cut ASSISTANTS' text hashes
    pre_texts = [None]                                                # …and their texts, built and hydrated only if a marker needs them

    def _pre_exact(txt):
        """Whether a pre-cut assistant kept exactly `txt`, from the rows' hashes: no atom built (a user prompt or a command
        with the same text is not a kept reply, so only assistants count: round 3 M2a)."""
        return hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:8] in pre_hashes

    def _pre_texts():
        """The pre-cut assistants' texts for the either-way prefix rule the whole parse applies (round 3 M2b): built and
        hydrated once, only when a tail marker survived the cheaper checks (the t_floor filter above keeps this off every
        parse with no marker the tail can hold)."""
        if pre_texts[0] is None:
            las = [la[k] for la, k, r, typ, nt, hh in pre_rows if typ == "assistant" and nt]
            if las:
                hydrate(las, rompuuid or (atoms[0].get("session_id") if atoms else None), by="synthesize_orphans")
            pre_texts[0] = [t for a in las if (t := _text_of(_content(a.get("message"))).strip())]
        return pre_texts[0]

    def _older_texts():
        if older[0] is None:
            las = [a for a in lazy if a.get("type") == "assistant" and a["lazy"].get("nt")]
            if las:
                hydrate(las, rompuuid or (atoms[0].get("session_id") if atoms else None))
            older[0] = [t for a in las if (t := _text_of(_content(a.get("message"))).strip())]
        return older[0]
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
        if lazy and any(dt.startswith(txt) or txt.startswith(dt) for dt in _older_texts()):
            continue                                                   # a reply the disk kept before the cut
        if pre_rows and (_pre_exact(txt) or any(dt.startswith(txt) or txt.startswith(dt) for dt in _pre_texts())):
            continue                                                   # …or the index kept, exactly (by hash) or as a prefix either way
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
class LazyBodyRead(RuntimeError):
    """A consumer read the message of an atom restored from the assembly checkpoint without hydrating it first
    (em.hydrate). Raised on any read, in tests and in the kernel alike: a silent empty body would misclassify
    the atom (no text, no tool blocks) where a loud failure names the consumer that bypassed hydration."""


class _Unhydrated:
    """The one value inside a _LazyBody's storage: not JSON serializable, so a lazy atom cannot leave the kernel as an
    empty message by accident."""
    __slots__ = ("uuid",)

    def __init__(self, uuid):
        self.uuid = uuid

    def __repr__(self):
        return "<unhydrated body of %s>" % self.uuid


class _LazyBody(dict):
    """The message of a lazy atom: a dict-shaped sentinel that refuses every read. `_content` sees a dict and asks
    it for content; the ask raises LazyBodyRead with the atom's uuid, so the bypassing site is on the traceback."""
    __slots__ = ("uuid",)

    def __init__(self, uuid):
        super().__init__()
        self.uuid = uuid
        super().__setitem__("lazy body", _Unhydrated(uuid))   # the C json encoder walks a dict subclass's storage
        #                                                        directly, never through get/items: the value inside makes
        #                                                        json.dumps raise (TypeError, not JSON serializable) instead
        #                                                        of shipping an empty message

    def _refuse(self, *a, **k):
        raise LazyBodyRead("atom %s: message read before hydration (em.hydrate)" % self.uuid)

    get = __getitem__ = __contains__ = items = keys = values = __iter__ = __len__ = _refuse

    def __bool__(self):
        return True

    def __eq__(self, other):
        return isinstance(other, _LazyBody) and other.uuid == self.uuid

    def __hash__(self):
        return hash(("lazy", self.uuid))

    def __repr__(self):
        return "<lazy body of %s>" % self.uuid


def is_lazy(atom):
    return isinstance(atom.get("message"), _LazyBody)


def _text_hash8(atom):
    """sha1(text)[:8] of an atom's text, the ids' content hash: read from the atom's lazy scalars when it has no body."""
    lz = atom.get("lazy")
    if lz is not None:
        return lz["h"]
    return hashlib.sha1(_text_of(_content(atom.get("message"))).encode("utf-8", "replace")).hexdigest()[:8]


def _stop_reason(atom):
    lz = atom.get("lazy")
    if lz is not None:
        return lz.get("sr")
    return (atom.get("message") or {}).get("stop_reason")


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
    lz = atom.get("lazy")
    if lz is not None:
        return bool(lz.get("ir"))
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
    trig = turn["trigger"]
    a = None
    if trig:
        a = next((x for x in atoms if x.get("uuid") == trig["uuid"]), None)
    elif atoms:
        a = atoms[0]
    h = _text_hash8(a) if a is not None else hashlib.sha1(b"").hexdigest()[:8]
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
            sr = _stop_reason(atom)
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
            last_sr = _stop_reason(a)
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
    anchor atom's identity.

    A segment opened by a MACHINE-WRITTEN trigger is keyed by its anchor atom's uuid too (T318, 2026-09-10):
    anything romp injected itself (the romp-injected marker: a kernel restart or crash notice, an auto-nudge,
    the retry message, the compaction suggestion, a Nudge-button follow-up), the CLI's own stop record
    ('[Request interrupted by user…]', is_interrupt_record), and a SCHEDULED task's fired prompt (origin
    subkind scheduled-trigger, or its preamble on an unstamped record: the CLI fires the stored prompt
    verbatim every interval, with no time or task id interpolated). Most of these are worded identically every time,
    so a content hash gave every such segment in a session the same hash and the timestamp-invariant _seg_key
    aliased them all (one session held 19 restart-notice segments and 25 stop records under three keys): a
    card whose recorded segments held one such segment resolved to whichever the parse saw last, and its
    summary click landed hours away from the work it described. Keying them by uuid is safe on the echo
    axis for a different reason than for typed prompts: every recorded key (a placement, a trail, a seam, a
    caption) is written by the judge from the TRANSCRIPT parse and the kernel only looks up, and a romp
    send's optimistic echo is hidden the moment its record lands, so the echo-time id and the landed id
    never coexist in anything recorded. A follow-up the USER typed into a card carries no romp-injected
    marker (only the Nudge button's does) and keeps its content hash, since its echo and its record must
    share a key while both are on screen."""
    anchor, texted = None, None           # texted: the atom whose text is the basis (the anchor with text)
    if trigger_uuid:
        a = next((x for x in atoms if x.get("uuid") == trigger_uuid), None)
        if a:
            anchor = a
            if _has_text(a):
                texted = a
    if texted is None and atoms:
        anchor = anchor or atoms[0]
        if _has_text(atoms[0]):
            texted = atoms[0]
    machine_written = texted is not None and (_machine_written(texted)
                                              or (anchor is not None and is_interrupt_record(anchor)))
    if texted is not None and not machine_written:
        h = _text_hash8(texted)               # the content hash: sha1(text)[:8], the same on a lazy atom
    else:
        basis = (anchor or {}).get("uuid") or next((a.get("uuid") for a in atoms if a.get("uuid")), "")
        h = hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:8]
    return "%s:%d:%s" % (rompuuid, seg_t, h)


def _has_text(atom):
    lz = atom.get("lazy")
    if lz is not None:
        return bool(lz.get("nt"))
    return bool(_text_of(_content(atom.get("message"))))


def _machine_written(atom):
    """Whether the atom's text is romp's own or a scheduled task's fired prompt (the id keys on the anchor uuid then;
    see _segment_id). Read from the lazy scalars when the atom has no body."""
    lz = atom.get("lazy")
    if lz is not None:
        return bool(lz.get("mw"))
    text = _text_of(_content(atom.get("message")))
    origin = atom.get("origin")
    return bool(text) and bool(
        ROMP_INJECT_RE.search(text) or SCHEDULED_PREAMBLE_RE.match(text)
        or (isinstance(origin, dict) and origin.get("kind") == "task-notification" and origin.get("subkind") == "scheduled-trigger"))


def segments(turn):
    """The per-input spans of a turn (what the timeline draws as bars). A segment runs
    from one input to the next (or to the turn end). Each carries a stable `id` for the
    summarizer layer. Pure function over a turn."""
    atoms = turn["atoms"]
    if turn.get("pre") and turn.get("segs") is not None:   # a restored pre-cut turn (T323 stage 4c): the spans as written; the
        return [dict({"id": s[0], "trigger": s[1], "t": s[2], "end": s[3], "atoms": atoms[s[4]:s[4] + s[5]]},   # atoms a lazy view
                     **({"w": bool(s[6]), "mids": list(s[7]), "hp": bool(s[8])} if len(s) >= 9 else {}))   # (T358: the stored verdicts)
                for s in turn["segs"]]
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
    hydrate(atoms)                                            # bodies before the assembly cut: read on demand (T323 stage 4a)
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
    for k_ in ("w", "mids", "hp"):
        head.pop(k_, None)                            # the whole segment's stored verdicts (T358) do not describe a part
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


def chain_membership(leaf_path, candidate_files=None, states=None, leaf_override=None, rompuuid=None, sdk_human=None):
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
    # The display parse's own assembly entry answers when it stands for these inputs (T323 stage 4a): a fresh
    # adapter here read the whole leaf and held its graph a second time, which on a session restored from its
    # assembly document was the one whole read left at a boot. Read under the entry's key lock (folds mutate the
    # adapter's links transiently); an entry restored from a document adds the pre-cut verdicts its seed carries.
    adapter, how = None, "whole"
    if not leaf_override and rompuuid is not None:
        key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
        with _asm_key_lock(key):
            with _ASM_LOCK:
                entry = _ASM_CACHE.get(key)
            if entry is not None and entry["cands"] == tuple(str(f) for f in candidate_files) \
                    and entry["links"] == dict(links or {}) and _entry_current(entry, candidate_files):
                if _READER_TRACE:
                    sys.stderr.write("chain: entry %s\n" % leaf_path)
                return _membership_of(entry["ad"])        # the display's own current graph, under its lock
        if _CKPT_DIR_FN is not None:
            doc = _asm_ckpt_load(leaf_path, rompuuid, sdk_human, candidate_files, links)
            if doc is not None:                              # no current entry: the document's pre-cut facts plus the
                try:                                         #  tail read now, the whole graph's verdicts without the
                    seed, _landed = _seed_from_doc(doc)      #  whole read (the emit is the parse's, not needed here)
                    adapter = FileAdapter(candidate_files, leaf_path, resume_links=links, seed=seed)
                    how = "seeded"
                except Exception as e:                       # noqa: BLE001
                    _asm_ckpt_note(leaf_path, "restore", repr(e)[:120]); adapter = None
            elif _READER_TRACE:
                sys.stderr.write("chain: no document for %s (rompuuid %s sdk_human %r cands %r)\n" % (leaf_path, rompuuid, sdk_human, [str(f) for f in candidate_files]))
    if adapter is None:
        adapter = FileAdapter(candidate_files, leaf_path, leaf_override=leaf_override, resume_links=links)
    if _READER_TRACE:
        sys.stderr.write("chain: %s %s\n" % (how, leaf_path))
    return _membership_of(adapter)


def file_rewound(path, rompuuid=None, sdk_human=None):
    """The uuids a ONE-FILE walk of `path` classifies "rewind" (the judges' per-file discriminator). When the file is a
    leaf whose assembly document stands for a single-file lineage, the walk runs over a seeded adapter (the pre-cut
    verdicts from the document, the tail read now) instead of reading the file whole; a multi-file lineage's document
    records whole-graph verdicts, which are not this walk's, so that file reads whole as before. Raises OSError when a
    non-empty file yields no records (a failed read, not an empty file)."""
    path = Path(path)
    ad = None
    if rompuuid is not None and _CKPT_DIR_FN is not None:
        doc = _asm_ckpt_load(path, rompuuid, sdk_human, [str(path)], {}, quiet_inputs=True)
        if doc is not None:
            try:
                seed, _landed = _seed_from_doc(doc)
                ad = FileAdapter([str(path)], str(path), seed=seed)
            except Exception as e:                            # noqa: BLE001
                _asm_ckpt_note(path, "restore", repr(e)[:120]); ad = None
    if ad is None:
        ad = FileAdapter([str(path)], str(path))
    if not ad.by_uuid and (ad.seed is None or not ad.seed["verdicts"]) and path.stat().st_size > 0:
        raise OSError("transcript read yielded no records")
    verdicts = dict(ad.chain_verdicts())
    if ad.seed is not None:
        for u, v in ad.seed["verdicts"].items():
            verdicts.setdefault(u, v)
    return {u for u, v in verdicts.items() if v == "rewind"}


_REWOUND_CACHE = {}               # path -> (count, gen, {"uuids": [...]}): file_rewound's verdict set over a FROZEN file, a registered
#                                   fold (T391) the fold document carries and restores, so a dead episode file is read whole once
_REWOUND_STATS = {"served": 0, "walked": 0, "stale": 0, "fallback": 0}   # the memo's answers, the walks it took, the memos an
#                                   append or rewrite retired, and the walks whose memo could not be read or stored


def _rewound_walk(path):
    """The plain one-file walk `rewound_uuids` memoizes: file_rewound's road without a rompuuid, returning the verdict set and
    the reader's (gen, base, count) the adapter's records came from (FileAdapter._src_keys), the witness the memo is stored at.
    Its own, never re-fetched from the cache after the walk: an append and a refresh between the two would memoize pre-append
    verdicts at the post-append witness (T391 round one, low 1)."""
    key = str(path)
    ad = FileAdapter([key], key)
    if not ad.by_uuid and Path(path).stat().st_size > 0:
        raise OSError("transcript read yielded no records")
    verdicts = dict(ad.chain_verdicts())
    return {u for u, v in verdicts.items() if v == "rewind"}, ad._src_keys.get(key, (None, 0, 0))


def rewound_uuids(path, drop=True):
    """`file_rewound(path)` for a file with no rompuuid road (a dead episode's transcript in a lineage, walked by the judges'
    incident scan), memoized per FROZEN file as the fold `rewoundUuids` of its fold document (T391): the memo rides the
    existing document, its witness (size, mtime, the cut's guard), its restore, its fold state cap and its counted fallbacks,
    and the quiescence drop writes it from the walk's own read, so the file is read whole once and not at the next process.
    fold_records consults it: a hit or a restore at the witness answers with no read; a file that grew or was rewritten steps
    a `step` that retires the state (None), so the walk runs again and the memo is rewritten; an over-cap set is recorded as
    such and walked again next time. The leaf road with a rompuuid never comes here.

    `drop`: whether the walk's entry leaves the reader's cache after the memo is stored (the quiescence drop, when the file
    has been idle past its window). True for a dead episode's file, which nothing else reads; False for a file of a LIVE
    session's lineage (its /clear anchor, T391 round one, medium): the chain walk reads that file whole first at every pass,
    and a memo that popped the entry made the next pass's chain walk read it whole again, every pass, while the memo itself
    found its cursor at a moved generation and walked. Resident, the anchor is read once per process and the memo's cursor
    stays at the generation the chain walk's entry holds, so the scan reads nothing at all.

    The counters (checkpoints.rewoundMemo): `served`, a memo answered in memory or from the document at the witness; `walked`,
    every walk; `stale`, the walks over a memo the file's growth or rewrite retired (an in-process cursor stepped past by an
    append, a document cursor restored and stepped past, a refold whose count moved); a walk over an unchanged file whose entry
    left memory and came back under a fresh generation is walked, not stale (round one, low 2); `fallback`, a document state of
    the wrong shape (walked, never trusted) or a walk whose reader entry was gone before the memo could be stored (round one,
    low 3), both counted so a memo that never takes is visible on /perf."""
    key = str(path)
    had = _REWOUND_CACHE.get(key)
    kinds = []
    state = fold_records(_REWOUND_CACHE, key, lambda: None, lambda st, o: None, on=kinds.append, ckpt="rewoundUuids")
    if isinstance(state, dict) and isinstance(state.get("uuids"), list):
        with _CKPT_LOCK:
            _REWOUND_STATS["served"] += 1
        return set(state["uuids"])
    if state is not None:                                 # a state that is not the memo's shape: never trusted, counted, walked
        with _CKPT_LOCK:
            _REWOUND_STATS["fallback"] += 1
        _REWOUND_CACHE.pop(key, None)
    out, (gen, base, count) = _rewound_walk(path)         # the walk (a whole read of a frozen file: the record cache holds it)
    kind = kinds[0] if kinds else None
    with _CKPT_LOCK:
        _REWOUND_STATS["walked"] += 1
        if kind in ("append", "restore") or (kind == "refold" and had is not None and had[0] != count):
            _REWOUND_STATS["stale"] += 1                  # a memo stood and the file moved under it
    if gen is None:                                       # no reader entry for the walk's records: nothing to store the memo at
        with _CKPT_LOCK:
            _REWOUND_STATS["fallback"] += 1
        return out
    _REWOUND_CACHE[key] = (count, gen, {"uuids": sorted(out)})   # the memo at the walk's own witness, dirty
    with _CKPT_LOCK:
        _FOLD_DIRTY.add(key)
    if drop and checkpoint_drop_writes_on():              # the quiescence drop over this frozen file writes the document from the
        with _JSONL_CACHE_LOCK:                           #  walk's own read and lets the records go (T362's drop, its budget and its
            ent = _JSONL_CACHE.get(key)                   #  deferral); a file still changing keeps its entry and is written at its
        if ent is not None and ent[6] == gen:             #  settle; only the very entry the walk read is dropped. With the drop's
            _drop_quiescent_entry(key, ent, pop=True)     #  document write OFF (a cycle cap of 0) the entry stays resident: the memo
    #                                                        could not reach the disk, and a drop then made the file a whole read at
    #                                                        every pass where the old road read it once per process (round two, low 1)
    return out


def rewound_memo_stats():
    with _CKPT_LOCK:
        return dict(_REWOUND_STATS)


def _membership_of(adapter):
    """The five-way membership dict from an adapter's walk; a seeded adapter's pre-cut verdicts join its own."""
    active = adapter.active_path()
    verdicts = dict(adapter.chain_verdicts(active))
    if adapter.seed is not None:
        for u, v in adapter.seed["verdicts"].items():
            verdicts.setdefault(u, v)
        active = set(active) | {u for u, v in adapter.seed["verdicts"].items() if v == "active"}
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
_ASM_STATS = {"full": 0, "fold": 0, "serve": 0, "restore": 0, "bypass": 0, "fallback": 0}   # observability + tests (restore
#                                                                                             seeded: a row without it means zero)
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
    and no pending cut (cut parses never reach the cache). A restored entry's prefix (the lazy atoms
    before the checkpoint's cut, T323 stage 4) comes first, in emit order, like a whole parse's; its skill loads
    (T333) are the carried pre-cut ones plus the tail's, in the seeded emit state."""
    return ([dict(a) for a in entry.get("prefix") or []] + [dict(a) for a in entry["atoms"]], set(entry["landed"]), None,
            dict(entry["st"].get("skill_loads") or {}), list(entry.get("preTurns") or []))


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
             "recs": dict(ad._src_keys), "n_qatts": len(ad.qatts), "prefix": []}
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
        old = entry["recs"].get(str(fp))
        if old is None:
            return _asm_demote("recs-gone")
        if old == ("skip",):                                   # a file wholly before the checkpoint's cut: immutable by
            fst = (entry.get("skipped") or {}).get(str(fp))    #  contract (a fork's prior file); its stat is the proof
            try:
                st_ = os.stat(fp)
                if fst is None or (st_.st_size, st_.st_mtime) != tuple(fst):
                    return _asm_demote("nonleaf")
            except OSError:
                return _asm_demote("nonleaf")
            continue
        ent = _read_jsonl_entry(fp, tail_ok=True)
        if ent is None:
            return _asm_demote("recs-gone")
        gen, base, recs = ent[6], ent[5], ent[4]
        ogen, obase, ocount = old
        if gen != ogen or base > ocount:
            return _asm_demote("rewrite" if Path(fp).stem == leaf_stem else "nonleaf")   # a from-zero read replaced the
        #                                                                                  entry: a rewrite, a shrink, an eviction
        count = base + len(recs)
        if Path(fp).stem != leaf_stem:
            if count != ocount:
                return _asm_demote("nonleaf")   # a lineage file grew: the closure changed under the entry
            continue
        leaf_recs = recs
        if count == ocount:
            delta = []
        elif count > ocount:
            delta = recs[ocount - base:]        # the records past what the entry folded (the reader's generation proves the prefix)
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
            _wtxt = _text_of(_content(r.get("message"))) or ""
            if r["promptId"] in ad.boundary_pids or \
                    (CMD_WRAP_RE.match(_wtxt) and not is_skill_load_wrapper(_wtxt)):   # the harness's skill load
                return _asm_demote("promptid")                                        #   re-classifies nothing (T333)
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
            key = (a.get("sentAt", a["t"]), _th(" ".join(_text_of(_content(a.get("message"))).split())))   # the (send ts,
            #                                                                            text hash) key: `t` is the landing (T252d)
            if key not in st["postal_miss_att"]:
                continue
            q = next((q for q in ad.qatts if q["ts"] == key[0]
                      and _th(" ".join(q["text"].split())) == key[1]
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
    ogen, obase, ocount = entry["recs"][leaf_key]
    entry["recs"][leaf_key] = (ogen, obase, ocount + len(delta))   # commit LAST: a bail above re-slices the same
    ad._src[leaf_key] = leaf_recs                 #  delta next visit and the uuid gate demotes it
    ad._src_keys[leaf_key] = entry["recs"][leaf_key]
    _ASM_STATS["fold"] += 1
    return _asm_serve(entry)


# ═══════════════════════ THE ASSEMBLY CHECKPOINT (T323 stage 4, 2026-09-11) ═══════════════════════
# One document per leaf transcript beside the fold checkpoints (checkpoints/<sha1(realpath)[:20]>.asm.json), written
# from a WHOLE assembly entry when the tree has a compaction boundary: everything before the CUT (the first kept record
# of the turn that holds the last compact_boundary atom, or of the /compact command turn when that boundary is an
# adopted manual one) is recorded as identities and locations, never bodies. A fresh process verifies the document
# (version, path, session, every file's witness, the cut's guard bytes), rebuilds the pre-cut turns from the document
# as LAZY atoms (every scalar the ids and the segmentation read, a _LazyBody where the message was), reads the leaf
# from the cut's byte offset only, parses that tail through a FileAdapter seeded with the pre-cut graph facts and the
# carried emit state, and proves the pre-cut part identical to the whole parse's by a sha1 over its turn ids, segment
# ids and atom uuids. Bodies come back on demand (hydrate). Anything that does not verify is a counted fallback to a
# whole parse; a compaction landing after the document demotes the tail fold to a whole parse exactly as before, and
# the next settle writes a new document with the new cut.
_ASM_CKPT_V = 5                       # 2: atom rows carry [offset, len], nt for every atom; 3: the carry holds skill_loads (T333);
#                                       4: a `turns` section over the pre-cut rows (T323 stage 4c: the lazy index)
#                                       5: lazy markers carry pc (assistant prose chars) and mid (postal message ids); a turn row
#                                          carries pcs and hT, a segment row w, mids and hp (T358: the per-cycle walkers read scalars).
#                                          The stored w and hp are atom_has_work's and seg_prompt_atom's verdicts at write time: a
#                                          change to either rule, or to what pc, mid, pcs or hT mean, is a bump of this constant
#                                          (tests/test_asm_index.py pins the rules' source beside it).
# Materialized pre-cut atoms resident across every session (the lazy index's LRU): MemTotal / 32 KiB, never under 500,000
# (3.9 million on a 118 GiB machine); ROMP_ASM_INDEX_CAP sets it outright. At 20,000 (2026-09-11, the day the index
# shipped) 50 sessions' 4,080 restored turns materialized 1.2 million atoms and evicted 1.19 million of them in 150 s,
# every chat build cold at 5.6 s: a ceiling under the working set is a thrash, not a saving.
_MAT_CAP = _env_or("ROMP_ASM_INDEX_CAP", max(500_000, _machine_memory_bytes() // (32 * 1024)))
_MAT_LRU = collections.OrderedDict()  # (id(LazyAtoms), row) → (LazyAtoms, row): eviction drops the memo, never a field in place
_MAT_LOCK = threading.Lock()
_ASM_INDEX_STATS = {"materialized": 0, "materializedBy": {}, "resident": 0, "evictions": 0, "restoredTurns": 0, "rowDecodes": 0}
_PRE_TURN_KEYS = ("pre", "uuids", "lastT", "maxT", "lastModel", "tools", "segs", "pcs", "hT")   # a pre-turn's fields beyond a plain turn's


class LazyIndexError(RuntimeError):
    """A pre-cut row the index cannot decode: names the session and the row, so the fallback is a loud one."""


class _Unmaterialized:
    """The placeholder a LazyAtoms slot holds before its atom is built. Not JSON-serializable and not a dict: a serializer
    or a copier reaching a pre-cut turn's atoms through the list's storage raises instead of shipping placeholders."""
    __slots__ = ()

    def __repr__(self):
        return "<unmaterialized atom>"


_UNMAT = _Unmaterialized()


def _materialize_caller():
    """The consumer that reached for a pre-cut atom: the first frame above the index's own methods."""
    f = sys._getframe(2)
    for _ in range(8):
        if f is None:
            break
        name = f.f_code.co_name
        if name not in ("__getitem__", "__iter__", "__reversed__", "__contains__", "__eq__", "index", "count", "copy", "__add__",
                        "__radd__", "__reduce_ex__", "_at", "materialize", "_materialize_caller", "<genexpr>", "<listcomp>", "get"):
            return name
        f = f.f_back
    return "?"


class LazyIndex:
    """One restored session's pre-cut rows (T323 stage 4c): the document's atom rows kept as BYTES, decoded one at a time
    when a consumer reaches for an atom, through a process-wide LRU (_MAT_CAP). The record rows (identity, time, file,
    parent) stay decoded: they are small and every materialization reads one."""

    def __init__(self, doc, rompuuid, leaf_path):
        self.rompuuid = str(rompuuid)
        self.leaf = str(leaf_path)
        self.rowb = [json.dumps(r, separators=(",", ":")).encode("utf-8") for r in doc["atoms"]]
        self.records = doc["records"]
        self.fsids = list(doc.get("fsids") or [])

    def build(self, k):
        with _MAT_LOCK:
            _ASM_INDEX_STATS["rowDecodes"] += 1
        try:
            row = json.loads(self.rowb[k])
        except (IndexError, ValueError) as e:
            raise LazyIndexError("session %s row %d: %s" % (self.rompuuid[:8], k, e)) from e
        if row.get("syn"):                                # a synthesized atom (idle, a salvaged reply): its message inline, if any
            a = dict(row.get("s") or {})
            if "m" in row:
                a.update(message=row["m"])                # a WRITE of the synthesized atom's message (no body read: the audit's regex)
            return a
        a = _restore_prefix_atoms([row], self.rompuuid, self.records, self.fsids)[0]
        a.pop("_seq", None)                               # the read-order tiebreak: the section fixed the order (parse_session pops it too)
        return a

    def uuid_of(self, k):
        """A row's uuid without building its atom (the record row's, else the synthesized scalars')."""
        with _MAT_LOCK:
            _ASM_INDEX_STATS["rowDecodes"] += 1
        row = json.loads(self.rowb[k])
        ri = row.get("r")
        if ri is not None:
            return self.records[ri][0]
        return (row.get("s") or {}).get("uuid")

    def text_flags(self, k):
        """(type, has text, text hash) for a row without building its atom, one decode: what an orphan marker's dedup reads.
        The hash is lz.h (the first eight hex of sha1 over the text) for a lazy row, the same digest over an inline message
        for a synthesized or inline row; None without text."""
        with _MAT_LOCK:
            _ASM_INDEX_STATS["rowDecodes"] += 1
        row = json.loads(self.rowb[k])
        ri = row.get("r")
        lz = row.get("lz")
        tname = {"u": "user", "a": "assistant", "s": "system"}
        typ = (row.get("s") or {}).get("type") or (tname.get(self.records[ri][2], "user") if ri is not None else None)
        if lz is not None:
            return typ, bool(lz.get("nt")), (lz.get("h") if lz.get("nt") else None)
        if "m" in row:
            txt = _text_of(_content(row["m"])).strip()
            return typ, bool(txt), (hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:8] if txt else None)
        return typ, False, None


class LazyAtoms(list):
    """A pre-cut turn's atoms (T323 stage 4c): a list whose slots hold placeholders until a consumer reaches for an
    atom, which the index then builds (a lazy atom, its body still on disk, hydrate() as before). Every read path a
    list offers goes through the build (indexing, slicing, iteration, membership, equality, copies, concatenation,
    pickling), so a consumer sees plain atom dicts; the placeholders reach only a serializer or copier that reads the
    list's storage directly (json's encoder, refused at __iter__ while a slot is unbuilt), and those raise. Materialized atoms live in a process-wide LRU
    (_MAT_CAP): eviction puts the placeholder back in the slot, the consumer's own reference stays whole."""

    def __init__(self, index, rows):
        list.__init__(self, [_UNMAT] * len(rows))
        self._index = index
        self._rows = list(rows)

    # ── the build ──
    def _at(self, i):
        a = list.__getitem__(self, i)
        if a is not _UNMAT:
            with _MAT_LOCK:
                key = (id(self), i)
                if key in _MAT_LRU:
                    _MAT_LRU.move_to_end(key)
            return a
        a = self._index.build(self._rows[i])
        by = _materialize_caller()
        with _MAT_LOCK:
            cur = list.__getitem__(self, i)
            if cur is not _UNMAT:                         # another thread built it first
                return cur
            list.__setitem__(self, i, a)
            _MAT_LRU[(id(self), i)] = (self, i)
            _ASM_INDEX_STATS["materialized"] += 1
            _ASM_INDEX_STATS["materializedBy"][by] = _ASM_INDEX_STATS["materializedBy"].get(by, 0) + 1
            while len(_MAT_LRU) > _MAT_CAP:
                _, (lz, j) = _MAT_LRU.popitem(last=False)
                list.__setitem__(lz, j, _UNMAT)
                _ASM_INDEX_STATS["evictions"] += 1
            _ASM_INDEX_STATS["resident"] = len(_MAT_LRU)
        return a

    def uuids(self):
        """The atoms' uuids without building them."""
        return [self._index.uuid_of(r) for r in self._rows]

    def rows(self):
        return list(self._rows)

    # ── the list surface ──
    def _unbuilt(self):
        return any(a is _UNMAT for a in list.__iter__(self))
    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(len(self))
            if step == 1:                                 # a contiguous slice (a segment's atoms) stays lazy: a view over this list
                return _LazyView(self, start, max(start, stop))
            return [self._at(j) for j in range(start, stop, step)]
        n = len(self)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError("LazyAtoms index out of range")
        return self._at(i)

    def __iter__(self):
        # json's encoder (C or Python) reaches a list SUBCLASS through iteration, so a dump of a tree would build every atom
        # and ship it: refused here, loudly, as the design asks (plain_tree is the dump's road)
        f = sys._getframe(1)
        if f is not None and f.f_code.co_name in ("iterencode", "encode", "_iterencode", "_iterencode_list", "_iterencode_dict") \
                and self._unbuilt():
            raise TypeError("a pre-cut turn's atoms are not JSON-serializable before they are built: dump em.plain_tree(session)")
        for j in range(len(self)):
            yield self._at(j)

    def __reversed__(self):
        for j in range(len(self) - 1, -1, -1):
            yield self._at(j)

    def __contains__(self, x):
        return any(a is x or a == x for a in self)

    def __eq__(self, other):
        return list(self) == list(other) if isinstance(other, list) else NotImplemented

    def __ne__(self, other):
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    __hash__ = None

    def index(self, x, *args):
        return list(self).index(x, *args)

    def count(self, x):
        return list(self).count(x)

    def copy(self):
        return list(self)

    def __add__(self, other):
        return list(self) + list(other)

    def __radd__(self, other):
        return list(other) + list(self)

    def __mul__(self, n):
        return list(self) * n

    def __reduce_ex__(self, proto):
        return (list, (list(self),))                     # a copy or a pickle is a plain list of built atoms

    def __repr__(self):
        return "LazyAtoms(%d rows, %d built)" % (len(self), sum(1 for a in list.__iter__(self) if a is not _UNMAT))

    def sort(self, *a, **k):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def append(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def extend(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def insert(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def pop(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def remove(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def __setitem__(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")

    def __delitem__(self, *a):
        raise TypeError("a pre-cut turn's atoms are read-only")


class _LazyView(LazyAtoms):
    """A contiguous slice of a LazyAtoms (a segment's atoms, T358) that builds nothing until read: a first read of a slot
    goes to the parent's slot (a build there is the parent's build under the parent's LRU key), and the view KEEPS what it
    read in its own slot for its lifetime, as the plain-list slice it replaces did, so a second reader of the same segment
    (the anchors after the ids, the jump after the anchors) touches no lock: with two builders at once (the pusher's cycle
    and a client's ready frame both building the same chat) every locked access convoyed on the index lock at about one
    atom per scheduler switch, and a restored session's first frame took 20 s on a four-core runner (T358 CI, 2026-09-12).
    A view is per build and short-lived, so what it holds is bounded by the build; an eviction in the parent is not seen by
    a slot the view already read, so while a build (or a planner pass whose returned tasks hold its views) is alive
    its read set pins atoms past the index cap by that one build's worth, as the plain-list slices did. json's encoder
    is refused while the parent's slots are unbuilt; slicing a view is a
    view of the parent."""
    def __init__(self, parent, start, stop):
        list.__init__(self, [_UNMAT] * (stop - start))
        self._parent, self._off = parent, start
        self._index, self._rows = parent._index, parent._rows[start:stop]
    def _at(self, i):
        a = list.__getitem__(self, i)
        if a is not _UNMAT:
            return a
        a = self._parent._at(self._off + i)
        list.__setitem__(self, i, a)
        return a
    def _unbuilt(self):
        return any(list.__getitem__(self._parent, self._off + j) is _UNMAT for j in range(len(self)))
    def __getitem__(self, i):
        if isinstance(i, slice):
            start, stop, step = i.indices(len(self))
            if step == 1:
                return _LazyView(self._parent, self._off + start, self._off + max(start, stop))
            return [self._at(j) for j in range(start, stop, step)]
        return LazyAtoms.__getitem__(self, i)


class PreTurn(dict):
    """A restored pre-cut turn: a plain turn's fields (id, trigger, t, end, ended, atoms) with `atoms` a LazyAtoms, plus
    the turn-level scalars the kernel's walkers read instead of the atoms (_PRE_TURN_KEYS: uuids, lastT, maxT, lastModel,
    tools, segs). A dict subclass only so a walker can tell one apart; `trigger` is {"uuid": the opener's}, as the
    segmentation shapes it, from the uuids alone."""


def _pre_turns_of(doc, index):
    """The pre-cut turns of a version-4 document as PreTurns over the index (no atom built)."""
    out = []
    for td in doc["turns"]:
        at = td.get("triggerAt")
        pt = PreTurn(id=td["id"], trigger=({"uuid": td["uuids"][at]} if at is not None else None), t=td["t"], end=td["end"],
                     ended=bool(td["ended"]), atoms=LazyAtoms(index, td["atoms"]),
                     pre=True, uuids=list(td["uuids"]), lastT=td.get("lastT"), maxT=td.get("maxT"), lastModel=td.get("lastModel"),
                     tools=[tuple(x) for x in td.get("tools") or []], segs=[list(s) for s in td["segs"]],
                     pcs={u: n for u, n in td.get("pcs") or []}, hT=td.get("hT"))
        out.append(pt)
    return out


def _tree_identity_of_doc(turns_doc, identity):
    """sha1 over a `turns` section's turn ids, segment ids and atom uuids, and the atoms' own identity digest (the whole
    parse's, verified at write time): the proof a restore reads from the section alone, no atom built."""
    h = hashlib.sha1()
    h.update((identity or "").encode()); h.update(b"#")
    for td in turns_doc:
        h.update(td["id"].encode()); h.update(b"|")
        for sg in td["segs"]:
            h.update(sg[0].encode()); h.update(b",")
        for u in td["uuids"]:
            h.update((u or "").encode()); h.update(b";")
        h.update(",".join(str(k) for k in td["atoms"]).encode()); h.update(b"/")   # the rows each turn holds (a permuted section differs)
    return h.hexdigest()


def plain_tree(session):
    """A plain copy of a parsed session for a comparison or a dump: every turn a plain dict with the six turn fields, its
    atoms built into plain dicts (bodies as they are: hydrate first for the text). The pre-turn fields are left out, so a
    restored tree and the whole parse's compare equal when they are."""
    turns = []
    for t in session.get("turns") or []:
        pt = {k: v for k, v in t.items() if k != "atoms" and k not in _PRE_TURN_KEYS}
        if isinstance(pt.get("trigger"), dict):
            pt["trigger"] = dict(pt["trigger"])
        pt["atoms"] = [dict(a) for a in t["atoms"]]
        turns.append(pt)
    out = {k: v for k, v in session.items() if k != "turns"}
    out["turns"] = turns
    return out


def asm_index_stats():
    with _MAT_LOCK:
        return {"materialized": _ASM_INDEX_STATS["materialized"], "materializedBy": dict(_ASM_INDEX_STATS["materializedBy"]),
                "resident": len(_MAT_LRU), "evictions": _ASM_INDEX_STATS["evictions"], "cap": _MAT_CAP,
                "restoredTurns": _ASM_INDEX_STATS["restoredTurns"], "rowDecodes": _ASM_INDEX_STATS["rowDecodes"]}
_ASM_CKPT_CAP = 16 * 1024 * 1024   # a document past this is not written (counted): that session parses whole as today
_ASM_CKPT_STATS = {"written": 0, "restored": 0, "fallbacks": {}, "skipped": {}, "hydratedBytes": 0, "hydratedAtoms": 0,
                   "hydratedBy": {},     # bytes per calling function: a whole-tree hydration anywhere shows here
                   "converge": {"writes": 0, "bytes": 0, "deferred": 0, "candidates": 0, "skipped": {}}}   # the pass's writes for
#                                          idle leaves from the boot's own parse (T376): looked at, written, deferred for the budget,
#                                          skipped per the writer's reason
_ASM_CKPT_LOCK = threading.Lock()
_ASM_CKPT_SAID = set()             # (path, reason) said once per process
_LAZY_FILES = {}                   # rompuuid -> {fsid: path}: where hydrate finds a lazy atom's record
_HYDRATED = {}                     # uuid -> the body fields read; dict order = LRU
_HYDRATED_BYTES = [0]
_HYDRATED_CAP = _env_or("ROMP_HYDRATED_CAP_MB", max(1024 ** 3, _machine_memory_bytes() // 32), 1024 * 1024)
#                                    the hydrated-body memo's byte cap: MemTotal / 32, never under 1 GiB (3.7 GiB on a 118 GiB
#                                    machine; 64 MB hydrated 3.1 GB of bodies in 150 s on 2026-09-11) (a judge pass re-reading one large body every cycle
#                                    shows in hydratedBytes; the memo keeps the common case at one read)
_LAZY_KINDS = ("a", "u", "c", "o", "k", "b")   # atom kinds whose message is lazy; boundary and refusal atoms carry no message


def asm_document_stands(leaf_path):
    """Whether an assembly document file exists for `leaf_path` (a stat, no read; False with no checkpoint directory): the
    judges' incident scan asks before taking the leaf road, whose seeded walk needs the document, and takes the memo road
    for a leaf that has none (a leaf with no compaction boundary can never have one, and was read whole at every boot)."""
    cp = _asm_ckpt_file(leaf_path)
    return cp is not None and cp.exists()


def _asm_ckpt_file(leaf_path):
    d = _ckpt_dir()
    if d is None:
        return None
    return Path(d) / (hashlib.sha1(os.path.realpath(str(leaf_path)).encode("utf-8")).hexdigest()[:20] + ".asm.json.gz")


_ASM_CKPT_REFUSED = {}            # realpath -> the reason a standing document was refused at this path's last restore: read
#                                   by _assemble to book full:refused, since the note below unlinks the document before the
#                                   parse decides its road (T398 round one, medium: a refused document read as none at all)


def _asm_ckpt_note(path, reason, detail=""):
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["fallbacks"][reason] = _ASM_CKPT_STATS["fallbacks"].get(reason, 0) + 1
        first = (str(path), reason) not in _ASM_CKPT_SAID
        _ASM_CKPT_SAID.add((str(path), reason))
        _ASM_CKPT_REFUSED[os.path.realpath(str(path))] = str(reason)
    if first:
        try:
            sys.stderr.write("assembly checkpoint fallback (%s) for %s%s\n" % (reason, path, (": " + detail) if detail else ""))
        except Exception:
            pass
    cp = _asm_ckpt_file(path)
    if cp is not None:
        try:
            cp.unlink()
            _asm_removed("fallback:" + str(reason))
            cp.with_name(cp.name + ".meta").unlink(missing_ok=True)
        except OSError:
            pass


def _asm_ckpt_skip(reason):
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["skipped"][reason] = _ASM_CKPT_STATS["skipped"].get(reason, 0) + 1
    return False


def asm_checkpoint_stats():
    with _ASM_CKPT_LOCK:
        out = dict(_ASM_CKPT_STATS); out["fallbacks"] = dict(out["fallbacks"]); out["skipped"] = dict(out["skipped"])
        out["hydratedBy"] = dict(out["hydratedBy"]); out["removed"] = dict(out.get("removed") or {})
        cv = out["converge"] = dict(out["converge"]); cv["skipped"] = dict(cv["skipped"])
    with _ASM_CKPT_LOCK:
        out["parse"] = dict(_ASM_STATS)               # the parse's roads (T398): serve, fold, restore, full (with its reason), bypass,
    return out                                        #  fallback, and every g:<reason> demotion, so a whole parse names its road


def _asm_removed(reason):
    """A document file removed, counted per reason (T398): the fallback that refused it, or the boot sweep."""
    with _ASM_CKPT_LOCK:
        r = _ASM_CKPT_STATS.setdefault("removed", {})
        r[reason] = r.get(reason, 0) + 1


def asm_converge_stat(name, n=1):
    """Count the converge pass's assembly work under asmCheckpoint.converge (T376)."""
    with _ASM_CKPT_LOCK:
        cv = _ASM_CKPT_STATS["converge"]
        cv[name] = cv.get(name, 0) + n


def asm_converge_skip(reason):
    with _ASM_CKPT_LOCK:
        sk = _ASM_CKPT_STATS["converge"]["skipped"]
        sk[reason] = sk.get(reason, 0) + 1


def asm_whole_entries():
    """The WHOLE (unrestored) assembly entries this process holds, as (leaf path, rompuuid, sdk_human): the parses the boot
    actually did, whatever the sessions' age. The converge pass enumerates its assembly candidates from these (T382: the
    discover window's rows left every idle leaf older than 48 hours out, the very population the step targets)."""
    with _ASM_LOCK:
        keys = [k for k, e in _ASM_CACHE.items() if e is not None and not e.get("prefix") and not e.get("preTurns")]
    return [(k[0], k[1], bool(k[2])) for k in keys]


def asm_whole_entry_for(leaf_path):
    """This process's whole assembly entries over `leaf_path` as {rompuuid: set of sdk_human flags}: how the pass names the
    session of a leaf the discover window no longer lists, and sees which flags it was parsed under (T382; a process can hold
    two whole entries for one leaf, the judges' flag and the display's)."""
    real = os.path.realpath(str(leaf_path)); out = {}
    with _ASM_LOCK:
        for k, e in _ASM_CACHE.items():
            if k[0] == real and e is not None and not e.get("prefix") and not e.get("preTurns"):
                out.setdefault(k[1], set()).add(bool(k[2]))
    return out


def asm_entry_whole(leaf_path, rompuuid, sdk_human=False):
    """Whether this process holds a WHOLE (unrestored) assembly entry for the session over `leaf_path`: the boot's own parse,
    which the converge pass writes an idle leaf's document from (T376); a restored entry's document already stands, and no
    entry means no write (never a parse of the pass's own)."""
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _ASM_LOCK:
        entry = _ASM_CACHE.get(key)
    return entry is not None and not entry.get("prefix") and not entry.get("preTurns")


def _atom_kind(a):
    """The lazy kind code of an emitted atom: which body fields hydrate rebuilds and how."""
    t = a.get("type")
    if t == "system":
        return "x" if a.get("subtype") == "compact_boundary" else "r"
    if a.get("absorbed"):
        return "b"
    if t == "user":
        return "c" if a.get("command") else "u"
    if a.get("skillMd") is not None:
        return "k"
    if a.get("command") is True:
        return "o"
    return "a"


def _atom_scalars(a):
    """Every field of an emitted atom but the bodies (message, toolUseResult, skillMd) and the read-order tiebreak."""
    return {k: v for k, v in a.items() if k not in ("message", "toolUseResult", "skillMd", "_seq")}


def _lazy_of(a, kind, rec_index):
    """The identity scalars a lazy atom carries in place of its body (what the ids, the segmentation and the gates read).
    An atom that is lazy already keeps its marker (its body is on disk; the marker was made from it)."""
    if a.get("lazy") is not None:
        return {k: v for k, v in a["lazy"].items() if k not in ("i", "at")}
    text = _text_of(_content(a.get("message"))) if a.get("message") is not None else ""
    lz = {"k": kind, "h": hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8], "nt": bool(text)}
    if _machine_written(a):                     # defaults left out: not machine-written, not an interrupt, no stop reason,
        #                                          no structured tool result, no model
        lz["mw"] = True
    if is_interrupt_record(a):
        lz["ir"] = True
    msg = a.get("message") if isinstance(a.get("message"), dict) else None
    if msg is not None and msg.get("stop_reason") is not None:
        lz["sr"] = msg["stop_reason"]
    if msg is not None and msg.get("model"):
        lz["mdl"] = msg["model"]                # the model stamp a few readers test (synthetic replies, the settle)
    if "toolUseResult" in a:
        lz["tur"] = True
    blocks = _content(msg) if msg is not None else []
    tu = [[b.get("id"), b.get("name")] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    tr = [b.get("tool_use_id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    if tu:
        lz["tu"] = tu                           # tool calls and results by id: what the settle gates and the task
    if tr:                                      #  pairings read, so they need no body
        lz["tr"] = tr
    if kind == "c":
        lz["disp"] = text                       # the invocation's display text is short: inline, no read to rebuild it
    if a.get("type") == "assistant" and not a.get("isApiError"):
        pc = _prose_chars(a)
        if pc:
            lz["pc"] = pc                       # assistant prose chars: the substantive reads and the citation gate (T358)
    mids = postal_mids(_content(msg) if msg is not None else None)
    if mids:
        lz["mid"] = mids                        # postal message ids: the timeline connector and the caption join (T358)
    return lz


def _pre_tree_identity(atoms, rompuuid):
    """sha1 over the turn ids, segment ids and atom uuids of `atoms` segmented as a session (no idle or orphan atoms):
    the proof that a restored prefix is the whole parse's prefix."""
    h = hashlib.sha1()
    for turn in segment_turns([dict(a) for a in atoms], rompuuid):
        h.update(turn["id"].encode()); h.update(b"|")
        for seg in segments(turn):
            h.update(seg["id"].encode()); h.update(b",")
        for a in turn["atoms"]:
            h.update((a.get("uuid") or "").encode()); h.update(b";")
    return h.hexdigest()


_ASM_SKIP_STRUCTURAL = ("unsplittable", "reconstruction", "unencodable", "oversize")   # true of a cut until it moves


def _tree_key(tree):
    """A parsed tree's identity for the writer's memo: its object, its turn count and its last turn's id (a new parse mints
    new dicts; the same tree yields the same section, or none, every time)."""
    turns = (tree or {}).get("turns") or []
    return (id(tree), len(turns), turns[-1].get("id") if turns else None)


def asm_checkpoint_write(leaf_path, rompuuid, sdk_human=False, tree=None, reason_out=None, who="settle"):
    """Write the leaf's assembly checkpoint from its WHOLE assembly entry. False when there is nothing to write: no
    entry, an entry restored from a document (its cut stands until a compaction moves it), no compaction boundary in
    the tree (the whole file would be the tail), a cut that would not split the chronological order the fold's gate
    needs (garbled stamps), or a document past the cap; each counted under asmCheckpoint.skipped, and appended to
    `reason_out` when a list is given (the converge pass reads its refusal there; T376 review). `who` names the caller
    in the blip line (the settle, the converge pass), said once per leaf and reason."""
    cp = _asm_ckpt_file(leaf_path)
    if cp is None:
        return False

    def _skip(reason):
        if reason_out is not None:
            reason_out.append(reason)
        return _asm_ckpt_skip(reason)
    key = (os.path.realpath(str(leaf_path)), str(rompuuid), bool(sdk_human))
    with _asm_key_lock(key):
        with _ASM_LOCK:
            entry = _ASM_CACHE.get(key)
        if entry is None:
            return _skip("noEntry")
        if entry.get("prefix") or entry.get("preTurns"):
            return _skip("restored")            # the document it came from stands
        if entry.get("docWritten") and cp.exists() and (tree is None or entry.get("docTurns")
                                                          or entry.get("docNoTurns") == _tree_key(tree)):
            return _skip("written")             # this entry's pre-cut part has not moved (a fold appends after the cut);
        #                                                  a document written without a tree is written again once one is given,
        #                                                  one whose tree yielded no section is not, for that tree (review low 4)
        ad = entry["ad"]
        atoms = entry["atoms"]
        bounds = [a for a in atoms if a.get("type") == "system" and a.get("subtype") == "compact_boundary"]
        if not bounds:
            return _skip("noBoundary")
        active = ad.active_path()
        verdicts = ad.chain_verdicts(active)
        turns = segment_turns([dict(a) for a in atoms], rompuuid)
        last_b = max(bounds, key=lambda a: (a["t"], a.get("_seq", 0)))
        memo = entry.get("docSkip")
        if memo is not None and memo[0] == last_b["uuid"]:
            return _skip(memo[1])               # this cut already failed to write: nothing rebuilt until it moves

        def skip(reason):
            if reason in _ASM_SKIP_STRUCTURAL:            # a property of this cut: memoized like a success (docWritten),
                entry["docSkip"] = (last_b["uuid"], reason)   #  re-armed when the cut moves
                _say_once("assembly checkpoint: %s not written: %s (said once until its cut moves)" % (leaf_path, reason))
            else:                                         # a blip (a stat or a write failing, a record landing between the
                _say_once("assembly checkpoint: %s not written by the %s: %s (said once per leaf; tried again at the next %s)"
                          % (leaf_path, who, reason, who))   #  parse and the offsets): named for its caller, once per leaf
            return _skip(reason)
        bi = next(i for i, t in enumerate(turns) if any(a.get("uuid") == last_b["uuid"] for a in t["atoms"]))
        if last_b["uuid"] in ad._adopted and bi > 0:
            bi -= 1                                       # an adopted manual compact: its /compact episode is the turn before
        def _rec_seq(a):
            return ad.seq_of.get(a.get("uuid"), 0)
        cut_seq = None
        for ti in range(bi, -1, -1):                      # the cut turn, then earlier ones while the chronological split fails
            seqs = [_rec_seq(a) for t in turns[ti:] for a in t["atoms"] if a.get("uuid") in ad.seq_of]
            if not seqs:
                continue
            cand = min(seqs)
            pre_ts = [ad.ts_of.get(u, 0) for u in entry["kept"] if ad.seq_of.get(u, 0) < cand and u in ad.by_uuid
                      and (ad.by_uuid[u].get("type") in ("user", "assistant") or ad.by_uuid[u].get("subtype") == "compact_boundary")]
            tail_ts = [ad.ts_of.get(u, 0) for u in entry["kept"] if ad.seq_of.get(u, 0) >= cand and u in ad.by_uuid
                       and (ad.by_uuid[u].get("type") in ("user", "assistant") or ad.by_uuid[u].get("subtype") == "compact_boundary")]
            if pre_ts and tail_ts and max(pre_ts) > min(tail_ts):
                continue                                  # a stamp out of order across the cut: the carry would not hold
            cut_seq = cand
            break
        if cut_seq is None or cut_seq <= 1:
            return skip("unsplittable")
        # the files: each one's records before the cut, its witness, and where the tail read starts
        first_seq, n = {}, 0
        for fp, recs in ad._src.items():
            first_seq[fp] = n + 1
            n += len(recs)
        files, cuts, fsid_paths, file_offs = {}, {}, {}, {}
        for fp, recs in ad._src.items():
            fsid = Path(fp).stem
            fsid_paths[fsid] = fp
            try:
                st_ = os.stat(fp)
            except OSError:
                return skip("stat")
            pre_n = max(0, min(len(recs), cut_seq - first_seq[fp]))   # records of this file before the cut
            is_leaf = Path(fp).stem == Path(leaf_path).stem
            offs, ent_gen = _entry_offsets_gen(fp)
            # The reader's entry may hold MORE records than the tree's adapter read: a live LEAF grows between the settle's
            # parse and this write (the CLI appends while the settle runs), and the reader extends its entry by a new list
            # whose prefix is the very records the adapter holds, under the same generation. The document's pre-cut part
            # is that prefix, so the prefix's offsets stand for the leaf; a shorter entry, or one under another generation
            # (a rewrite, a refold from zero), does not (T396: a continuously active 120 MB session never got a document
            # while its kernel lived, since every settle's write met an entry one record longer than its tree, and every
            # boot read it whole through whichever reader came first). A LINEAGE file longer than the tree still skips
            # (round one, medium): its row would be a skip row proven by a stat alone, and a prior file that gained a record
            # after the parse (its own session resumed elsewhere, the case _asm_gates demotes as nonleaf) would be stamped
            # as wholly before the cut with the appended record missing from every restore of this leaf's kernel life.
            src_gen, src_base = ((getattr(ad, "_src_keys", {}) or {}).get(fp, (None, None)) + (None, None))[:2]
            # The prefix is accepted for the leaf under the tree's own generation AND from base zero: a seeded adapter that
            # read the file from its cut (a degenerate document whose pre-cut part holds records but no atoms restores unseen
            # as restored) holds records the entry's prefix does not begin with, so a whole reader upgrading the entry to
            # base zero under the same generation must not lend it that prefix (round two, low 1).
            if offs is None or len(offs) < len(recs) or (len(offs) > len(recs) and not (is_leaf and src_gen == ent_gen and src_base == 0)):
                return skip("offsets")
            offs = offs[:len(recs)]                       # defensive: no row index below passes len(recs), so the extra
            file_offs[fp] = offs                          #  offsets of a grown entry are never read (round one, low 3)
            pre_uuids = [r.get("uuid") for r in recs[:pre_n] if r.get("uuid")]
            f = {"path": fp, "size": st_.st_size, "mtime": st_.st_mtime, "pre": pre_n, "n": len(recs),
                 "first": pre_uuids[0] if pre_uuids else None, "last": pre_uuids[-1] if pre_uuids else None}
            if pre_n >= len(recs) and not is_leaf:
                f["skip"] = True                          # wholly before the cut: never read at restore, stat is its proof
                st_read = (getattr(ad, "_src_stat", {}) or {}).get(fp)
                if st_read is None:
                    return skip("stat")                   # no witness for the records the tree was parsed from: no row
                f["size"], f["mtime"] = int(st_read[0]), float(st_read[1])   # the stat AS READ, never the write-time one: a
                #                                            record appended to the prior file between the parse and this write
                #                                            must fail the next boot's verification, not be stamped away (round
                #                                            one, medium, the half that needed no reader in between)
            else:
                cut_off = offs[pre_n][0] if pre_n < len(recs) else st_.st_size
                try:
                    with open(fp, "rb") as fh:
                        fh.seek(max(0, cut_off - _JSONL_TAIL_GUARD))
                        guard = fh.read(cut_off - max(0, cut_off - _JSONL_TAIL_GUARD))
                except OSError:
                    return skip("stat")
                f["cut"] = [cut_off, pre_n, guard.hex()]
            files[fsid] = f
        # the pre-cut records: identity, verdict, type, order, time, file, landed
        type_code = {"user": "u", "assistant": "a", "system": "s", "attachment": "t"}
        rows, row_of, files_order = [], {}, {}
        for u, r in ad.by_uuid.items():                   # insertion order = read order
            sq = ad.seq_of.get(u, 0)
            if sq >= cut_seq:
                continue
            fsid = ad.fsid_of.get(u)
            fp = fsid_paths.get(fsid)
            idx = sq - first_seq[fp] if fp is not None else -1
            row_of[u] = len(rows)
            fsids = files_order.setdefault("_", [])
            if fsid not in fsids:
                fsids.append(fsid)
            rows.append([u, verdicts.get(u, "broken")[0], type_code.get(r.get("type"), "?"), r.get("subtype") if r.get("type") == "system" else None,
                         sq, ad.ts_of.get(u, 0), fsids.index(fsid), idx, 1 if u in entry["landed"] else 0, r.get("parentUuid")])
        # the pre-cut atoms in emit order
        pre_atoms = []
        for a in atoms:
            u = a.get("uuid")
            sq = ad.seq_of.get(u, 0) if u in ad.seq_of else None
            if sq is None:
                if a.get("absorbed"):
                    q = next((q for q in ad.qatts if q["uuid"] == u), None)
                    if q is None or q["seq"] >= cut_seq:
                        continue
                    sq = q["seq"]
                else:
                    continue
            if sq >= cut_seq:
                continue
            kind = _atom_kind(a)
            scal = _atom_scalars(a)
            fsid = a.get("fsid")
            fp = fsid_paths.get(fsid)
            idx = (sq - first_seq[fp]) if fp is not None else -1
            ri = row_of.get(u)
            if ri is not None:                             # the record row carries uuid, type, t, fsid and parentUuid: derive them
                rr = rows[ri]
                for k_, v_ in (("uuid", u), ("fsid", fsid), ("session_id", rompuuid), ("t", rr[5]), ("parentUuid", rr[9])):
                    if scal.get(k_) == v_:
                        scal.pop(k_, None)
                if scal.get("type") == {"u": "user", "a": "assistant", "s": "system"}.get(rr[2]):
                    scal.pop("type", None)
            row = {"i": idx}
            f_offs = file_offs.get(fp)
            if f_offs is not None and 0 <= idx < len(f_offs):
                row["at"] = list(f_offs[idx])                  # (byte offset, byte length): hydrate seeks straight to it
            if scal:
                row["s"] = scal
            if ri is not None:
                row["r"] = ri
            if a.get("_seq", sq) != sq:
                row["seq"] = a.get("_seq", sq)             # an adopted boundary's emit order differs from its read order
            if kind in _LAZY_KINDS and idx >= 0 and fp is not None:
                row["lz"] = _lazy_of(a, kind, idx)
            elif kind in _LAZY_KINDS:                          # no record to read back (an absorbed attachment with no uuid, round 3):
                row["m"] = a.get("message")                    #  the body rides inline, never a lazy marker hydrate cannot fill
                if "toolUseResult" in a:
                    row["tur"] = a["toolUseResult"]
            pre_atoms.append(row)
        # the pre-cut spine, root to cut
        chain, u, guard_n = [], ad.leaf_uuid, 0
        while u is not None and guard_n < 500000:
            if ad.seq_of.get(u, 0) < cut_seq and u in ad.by_uuid:
                chain.append(u)
            u = ad.parent_of.get(u); guard_n += 1
        spine = [row_of[u] for u in reversed(chain) if u in row_of]   # record indexes, root to cut
        seq_ts = None
        i = bisect.bisect_left(ad._seq_ts, (cut_seq,)) - 1
        if i >= 0:
            seq_ts = list(ad._seq_ts[i])
        last_ts = 0
        for u, r in ad.by_uuid.items():
            if ad.seq_of.get(u, 0) < cut_seq and parse_z(r.get("timestamp")) is not None:
                last_ts = ad.ts_of.get(u, last_ts)
        # the carry as the emit left it after the pre-cut records: re-run the pre-pass over them alone (the sets
        # are the same as the whole parse's restricted to those records, since the walk is chronological)
        st = _emit_state()
        pre_order = [u for u in _chrono(ad, entry["kept"]) if ad.seq_of.get(u, 0) < cut_seq]
        ad._prepass(pre_order, st)
        for q in ad.qatts:
            if q["seq"] < cut_seq and q["ts"] is not None and (q["uuid"] is None or q["uuid"] in entry["kept"]):
                st["absorbed_keys"].add((q["ts"], _th(" ".join(q["text"].split()))))
        fsids = files_order.get("_", [])
        pre_whole = [dict(a) for a in atoms if (ad.seq_of.get(a.get("uuid")) if a.get("uuid") in ad.seq_of else
                                                 next((q["seq"] for q in ad.qatts if q["uuid"] == a.get("uuid")), cut_seq)) < cut_seq]
        identity = _pre_tree_identity(pre_whole, rompuuid)     # the WHOLE parse's ids over the pre-cut atoms (bodies in hand)
        pre_lazy = _restore_prefix_atoms(pre_atoms, rompuuid, rows, fsids)
        if _pre_tree_identity(pre_lazy, rompuuid) != identity:
            return skip("reconstruction")            # the lazy reconstruction would not reproduce the whole parse's ids
        # the `turns` section (T323 stage 4c): the parsed TREE's pre-cut turns as rows, so a restore builds the turns
        # without an atom; a synthesized atom (an idle span, a salvaged reply) has no record and rides as a row of its own
        # with its message inline. The section stops at the first turn holding a post-cut record (the cut is a turn
        # boundary: the chronological split above put every pre-cut record before every post-cut one).
        turns_doc = None
        n0 = len(pre_atoms)                               # the rows the walk appends leave with a refusal (review round 2, M1)
        if tree is not None:
            row_of_atom, row_of_scalars = {}, {}
            for k_, row_ in enumerate(pre_atoms):
                u_ = rows[row_["r"]][0] if "r" in row_ else (row_.get("s") or {}).get("uuid")
                if u_ and u_ not in row_of_atom:
                    row_of_atom[u_] = k_
                elif not u_ and "r" not in row_:            # a uuid-less absorbed atom (a queued_command attachment): its row is
                    row_of_scalars.setdefault(json.dumps(row_.get("s") or {}, sort_keys=True, default=str), k_)   #  found by its scalars
            turns_doc = []
            cut_ts = min((ad.ts_of.get(u, 0) for u in entry["kept"] if ad.seq_of.get(u, 0) >= cut_seq), default=None)
            for turn in tree.get("turns") or []:
                t_uuids = turn["uuids"] if turn.get("pre") else [a.get("uuid") for a in turn["atoms"]]   # a restored turn: no atom built
                seqs = [ad.seq_of[u_] for u_ in t_uuids if u_ in ad.seq_of]
                if seqs:
                    if max(seqs) >= cut_seq:
                        break                            # the first turn holding a post-cut record ends the pre-cut run
                elif cut_ts is None or (turn.get("t") or 0) >= cut_ts:
                    break                                # a turn of synthesized atoms alone (an idle span opening the tree): pre-cut
                #                                          by its time, else the tail's
                idxs = []
                for ai_, u_ in enumerate(t_uuids):
                    a = turn["atoms"][ai_] if not u_ or u_ not in row_of_atom else None   # built only for a uuid-less or unknown atom
                    k_ = row_of_atom.get(u_) if u_ else row_of_scalars.pop(json.dumps(_atom_scalars(a), sort_keys=True, default=str), None)
                    #  (two uuid-less attachments in one turn with the same enqueue stamp collide on the scalar key: the second
                    #   finds no row, the walk mints a synthesized one, and the coverage check refuses the section cleanly)
                    if k_ is None:
                        if u_ in ad.seq_of:
                            return skip("turnRows")      # a record atom with no row of its own: the tree and the entry disagree
                        k_ = len(pre_atoms)
                        row_ = {"i": -1, "syn": 1, "s": _atom_scalars(a)}   # a synthesized atom's row: its scalars, its message inline
                        if "message" in a:                                #  when it has one (an idle span has none)
                            row_["m"] = a["message"]
                        pre_atoms.append(row_)
                    idxs.append(k_)
                seg_rows, start = [], 0
                pre_segs = {s_[0]: s_ for s_ in (turn.get("segs") or [])} if turn.get("pre") else {}
                for sg in segments(turn):
                    ps_ = pre_segs.get(sg["id"])
                    if ps_ is not None and len(ps_) >= 9:            # a restored turn: its stored verdicts carry over, no atom built
                        w_, mids_, hp_ = ps_[6], ps_[7], ps_[8]
                    else:                                             # w is atom_has_work's verdict at write time (see _ASM_CKPT_V)
                        w_ = 1 if any(atom_has_work(a) for a in sg["atoms"]) else 0
                        mids_ = [m_ for a in sg["atoms"] for m_ in atom_mids(a)]
                        pa_ = seg_prompt_atom(sg)                     # hp: whether a MESSAGE caption is wanted (the planner's rule)
                        hp_ = 1 if pa_ is not None and pa_.get("author") == "human" else 0
                    seg_rows.append([sg["id"], sg.get("trigger"), sg["t"], sg["end"], start, len(sg["atoms"]), w_, mids_, hp_])
                    start += len(sg["atoms"])
                if turn.get("pre") and turn.get("pcs") is not None:
                    pcs_, hT_ = [[u_, n_] for u_, n_ in turn["pcs"].items()], turn.get("hT")
                    ts_ = [turn.get("lastT")] if turn.get("lastT") else []
                    ts_max, models, tools_ = turn.get("maxT"), ([turn["lastModel"]] if turn.get("lastModel") else []), [list(x) for x in turn.get("tools") or []]
                    trig_at = t_uuids.index(turn["trigger"]["uuid"]) if turn.get("trigger") and turn["trigger"].get("uuid") in t_uuids else None
                else:
                    pcs_ = [[u_, n_] for u_, n_ in ((a.get("uuid"), atom_prose_chars(a)) for a in turn["atoms"]) if u_ and n_ > 0]
                    hT_ = max((a.get("t", 0) for a in turn["atoms"] if a.get("type") == "user" and a.get("author") == "human"
                               and not is_interrupt_record(a)), default=None)
                    ts_ = [a.get("t") for a in turn["atoms"] if a.get("t")]
                    ts_max = max(ts_) if ts_ else None
                    models = [atom_model(a) for a in turn["atoms"] if a.get("type") == "assistant"]
                    models = [m_ for m_ in models if m_]
                    tools_ = [[i_, n_] for a in turn["atoms"] if a.get("type") == "assistant" for i_, n_ in atom_tool_uses(a)]
                    trig_at = next((i_ for i_, a in enumerate(turn["atoms"]) if turn["trigger"] is not None and a is turn["trigger"]), None)
                    if trig_at is None and turn["trigger"] is not None:
                        trig_at = next((i_ for i_, a in enumerate(turn["atoms"]) if a.get("uuid") == turn["trigger"].get("uuid")), None)
                if start != len(turn["atoms"]):
                    return skip("segs")                  # the segments are contiguous slices of the turn, in order
                turns_doc.append({"id": turn["id"], "t": turn["t"], "end": turn["end"], "ended": bool(turn["ended"]), "atoms": idxs,
                                  "segs": seg_rows, "uuids": list(t_uuids), "triggerAt": trig_at,
                                  "lastT": ts_[-1] if ts_ else None, "maxT": ts_max,
                                  "lastModel": models[-1] if models else None, "tools": tools_,
                                  "pcs": pcs_, "hT": hT_})
            # the section must COVER the pre-cut rows: every row in exactly one turn (a permutation of the row indexes; the
            # turns sort by time, so a row's index need not be contiguous with its neighbours'), else the tree the store
            # holds is not this entry's whole (a bare rollback armed before the last compaction cuts it short) and the
            # document is written without a section rather than restore a session missing history (review medium 1)
            if turns_doc and sorted(k_ for td in turns_doc for k_ in td["atoms"]) != list(range(len(pre_atoms))):
                _asm_ckpt_skip("turnsCoverage")
                turns_doc = None
            if not turns_doc:
                turns_doc = None                          # nothing before the cut in this tree: the atoms-only form
                del pre_atoms[n0:]                        # …whose rows are the emit's alone, the identity's count (M1)
        doc = {"av": _ASM_CKPT_V, "path": os.path.realpath(str(leaf_path)), "rompuuid": str(rompuuid), "sdkHuman": bool(sdk_human),
               "cands": list(entry["cands"]), "links": dict(entry["links"]), "files": files, "fsids": fsids, "cutSeq": cut_seq,
               "records": rows, "atoms": pre_atoms, "spine": spine, "seqTs": seq_ts, "lastTs": last_ts,
               "turns": turns_doc, "treeIdentity": _tree_identity_of_doc(turns_doc, identity) if turns_doc else None,
               "gates": {"prompt_ids": sorted(ad.prompt_ids), "boundary_pids": sorted(ad.boundary_pids),
                         "skill_use_ids": sorted(ad.skill_use_ids), "src_tool_links": sorted(ad.src_tool_links),
                         "dangling": sorted(ad.dangling)},
               "carry": _carry_encode(st), "identity": identity, "t": time.time()}
        try:
            text = json.dumps(doc, separators=(",", ":"))
        except TypeError:
            return skip("unencodable")
        data = gzip.compress(text.encode("utf-8"), compresslevel=6)   # identities and hashes compress about five to one; the
        if len(data) > _ASM_CKPT_CAP:                                  #  bytes a boot reads are the compressed ones
            return skip("oversize")
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_name("%s.%d.%x.tmp" % (cp.name, os.getpid(), threading.get_ident()))
            tmp.write_bytes(data)
            os.replace(tmp, cp)
            meta = cp.with_name(cp.name + ".meta")            # {"av", "path"}: what the boot sweep reads, never the document
            mtmp = meta.with_name(meta.name + ".%d.tmp" % os.getpid())
            mtmp.write_text(json.dumps({"av": _ASM_CKPT_V, "path": doc["path"]}))
            os.replace(mtmp, meta)
        except OSError:
            return skip("write")
        entry["docWritten"] = True
        entry["docTurns"] = bool(turns_doc)              # the turns section was written (T323 stage 4c)
        entry["docNoTurns"] = _tree_key(tree) if (tree is not None and not turns_doc) else None   # …or this tree yields none
        with _ASM_CKPT_LOCK:
            _ASM_CKPT_STATS["written"] += 1
        return True


def _carry_encode(st):
    """The emit carry as the document holds it: sets as lists, (second, hash) pairs as lists, the postal heal state
    left out (a restored entry heals only the tail it emits itself)."""
    return {"replay": sorted(st["replay"]), "seen_exact": sorted([list(k) for k in st["seen_exact"]]),
            "seen_text": sorted(st["seen_text"]), "compacted": bool(st["compacted"]), "restoring": bool(st["restoring"]),
            "last_boundary": st["last_boundary"], "summaries": dict(st["summaries"]), "skill_ids": sorted(st["skill_ids"]),
            "cmd_names": {k: sorted(v) for k, v in st["cmd_names"].items()},
            "absorbed_keys": sorted([list(k) for k in st["absorbed_keys"]]), "max_ppt": st["max_ppt"],
            "skill_loads": dict(st.get("skill_loads") or {})}     # wrapper uuid -> skill name before the cut (T333)


def _carry_decode(c):
    return {"replay": set(c["replay"]), "seen_exact": {tuple(k) for k in c["seen_exact"]}, "seen_text": set(c["seen_text"]),
            "compacted": bool(c["compacted"]), "restoring": bool(c["restoring"]), "last_boundary": c.get("last_boundary"),
            "summaries": dict(c.get("summaries") or {}), "skill_ids": set(c.get("skill_ids") or []),
            "cmd_names": {k: set(v) for k, v in (c.get("cmd_names") or {}).items()},
            "absorbed_keys": {tuple(k) for k in c.get("absorbed_keys") or []}, "max_ppt": float(c.get("max_ppt") or 0),
            "skill_loads": dict(c.get("skill_loads") or {}), "postal_miss_rec": set(), "postal_miss_att": set()}


def atom_tool_uses(atom):
    """[(tool_use id, name)] of an atom's tool calls, from the body or, for a lazy atom, its scalars: no hydration."""
    lz = atom.get("lazy")
    if lz is not None:
        return [tuple(x) for x in lz.get("tu") or []]
    return [(b.get("id"), b.get("name")) for b in _content(atom.get("message")) if isinstance(b, dict) and b.get("type") == "tool_use"]


def atom_tool_results(atom):
    """[tool_use id] of an atom's tool results, from the body or the lazy scalars."""
    lz = atom.get("lazy")
    if lz is not None:
        return list(lz.get("tr") or [])
    return [b.get("tool_use_id") for b in _content(atom.get("message")) if isinstance(b, dict) and b.get("type") == "tool_result"]


def postal_mids(content, ids=None):
    """POSTAL_RE's matches over a message's content, in document order, appended to `ids`: a bare string, the text blocks,
    and a tool_result block's content (a string, or a list of blocks whose strings are searched one by one: only the strings
    json.dumps would write can carry a marker, and a match cannot cross the encoder's separators, so encoding each string
    alone yields the ids the encoded whole would, without encoding an image block). The one search the timeline's
    connector, the message-caption join and the lazy marker share (T358; before it the kernel's _encoded_mids)."""
    if ids is None:
        ids = []
    if isinstance(content, str):
        if "romp-msg-id" in content:
            ids += POSTAL_RE.findall(content)
    elif isinstance(content, dict):
        if content.get("type") == "text":
            t = content.get("text")
            if isinstance(t, str):                    # a null text field is skipped, not a TypeError
                postal_mids(t, ids)
        elif content.get("type") == "tool_result":
            c = content.get("content")                # str | list[dict] | None from the SDK, as passed through
            if isinstance(c, str):
                postal_mids(c, ids)
            elif c is not None:
                _encoded_mids(c, ids)               # any other block (thinking, tool_use, image) carries no marker: skipped
    elif isinstance(content, (list, tuple)):
        for b in content:
            if isinstance(b, dict):                   # a bare string or a nested list inside a content list is no block: skipped,
                postal_mids(b, ids)                   #  as the kernel's body road read it
    return ids


def _encoded_mids(content, ids=None):
    """The strings-only search over an encoded value: dict keys and string values, every other value encodes to digits,
    true/false/null or brackets and cannot hold a marker."""
    if ids is None:
        ids = []
    if isinstance(content, str):
        if "romp-msg-id" in content:
            ids += POSTAL_RE.findall(json.dumps(content))
    elif isinstance(content, dict):
        for k, v in content.items():
            if isinstance(k, str) and "romp-msg-id" in k:
                ids += POSTAL_RE.findall(json.dumps(k))
            _encoded_mids(v, ids)
    elif isinstance(content, (list, tuple)):
        for v in content:
            _encoded_mids(v, ids)
    return ids


def atom_has_text(atom):
    """Whether an atom's text (the text blocks joined, stripped) is non-empty: the lazy marker's nt, else the body."""
    return _has_text(atom)


def atom_mids(atom):
    """The postal message ids an atom's message carries: the lazy marker's `mid`, else the body's (no hydration)."""
    lz = atom.get("lazy")
    if lz is not None:
        return list(lz.get("mid") or [])
    msg = atom.get("message")
    return postal_mids((msg or {}).get("content") if isinstance(msg, dict) else None)


def _prose_chars(atom):
    """Chars of the text blocks of an atom's body (each block's length, unstripped): pc's definition."""
    blocks = (atom.get("message") or {}).get("content", [])
    if not isinstance(blocks, list):
        return 0
    return sum(len(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text")


def atom_prose_chars(atom):
    """Chars of ASSISTANT prose on one atom: 0 for a non-assistant, API-error or prose-less atom. The lazy marker's `pc`
    for a lazy atom (no hydration), the body's text blocks for a resident one. The one measure behind every
    "substantive" read (the summary deep-link floor, the feed's citation gate, both against the judge's CITE_MIN_CHARS)."""
    if atom.get("type") != "assistant" or atom.get("isApiError"):
        return 0
    lz = atom.get("lazy")
    if lz is not None:
        return int(lz.get("pc") or 0)
    return _prose_chars(atom)


def atom_has_work(atom):
    """Whether an atom is real ASSISTANT output: its own text or a tool_use, on an assistant record that is not an
    API error (the captioner has nothing to gloss without one; an error record carries the error's text and is not
    work). Read from the lazy scalars (nt, tu) for a lazy atom. A segment row's `w` in the assembly document is this
    verdict at write time over the segment's atoms: changing this rule is a document version bump (_ASM_CKPT_V).
    An assistant message whose content is a bare string counts as text here (the judges' body road read no text in that
    shape): the lazy marker's nt cannot tell a bare string from a text block, and the two roads must agree; no CLI writes
    an assistant record in that shape (tests/test_asm_index.py pins the reading)."""
    if atom.get("type") != "assistant" or atom.get("isApiError"):
        return False
    return _has_text(atom) or bool(atom_tool_uses(atom))


_SETTLE_TEXT_H8 = hashlib.sha1(b"No response requested.").hexdigest()[:8]


def atom_is_settle(atom):
    """Whether an assistant atom's text is the CLI's settle reply ("No response requested.") or a synthetic model's: the
    lazy marker's text hash and model stamp answer for a lazy atom, the body for a resident one."""
    lz = atom.get("lazy")
    if lz is not None:
        return lz.get("mdl") == "<synthetic>" or (bool(lz.get("nt")) and lz.get("h") == _SETTLE_TEXT_H8)
    msg = atom.get("message") or {}
    return _text_of(_content(msg)) == "No response requested." or msg.get("model") == "<synthetic>"


def seg_prompt_atom(seg):
    """The atom a segment's MESSAGE caption glosses: its trigger, else its first atom (the caption planner's rule). One
    build at most for a restored segment; the document stores whether it is human-authored (`hp`)."""
    atoms = seg.get("atoms") or []
    return next((a for a in atoms if a.get("uuid") == seg.get("trigger")), None) or (atoms[0] if atoms else None)


def turn_scalar(turn, key):
    """A restored pre-cut turn's stored scalar (`pcs`, `hT`, ...), None for a plain turn: the walkers ask this before
    touching a turn's atoms."""
    return turn.get(key) if turn.get("pre") else None


def atom_model(atom):
    """The model stamp of an atom's message (None when absent), from the body or the lazy scalars."""
    lz = atom.get("lazy")
    if lz is not None:
        return lz.get("mdl")
    msg = atom.get("message")
    return msg.get("model") if isinstance(msg, dict) else None


def _restore_prefix_atoms(pre_atoms, rompuuid, rows, fsids):
    """The pre-cut atoms as the tree holds them: the identity fields from the record row (uuid, type, t, fsid, session,
    parentUuid), the recorded scalars over them, a _LazyBody where a message was, the lazy scalars under `lazy`, and
    the read-order tiebreak the segmentation sorts by."""
    tname = {"u": "user", "a": "assistant", "s": "system"}
    out = []
    for row in pre_atoms:
        a = {}
        ri = row.get("r")
        if ri is not None:
            rr = rows[ri]
            a.update({"type": tname.get(rr[2], "user"), "uuid": rr[0], "session_id": rompuuid, "t": rr[5],
                      "fsid": fsids[rr[6]] if 0 <= rr[6] < len(fsids) else None, "parentUuid": rr[9]})
            seq = rr[4]
        else:
            seq = row.get("seq", 0)
        a.update(row.get("s") or {})
        a["_seq"] = row.get("seq", seq)
        lz = row.get("lz")
        if lz is not None:
            a["lazy"] = dict(lz, i=row["i"], at=tuple(row["at"]) if row.get("at") else None)
            a["message"] = _LazyBody(a.get("uuid"))
        elif "m" in row:                                  # an inline body: an emitted atom with no record behind it (round 3)
            a["message"] = row["m"]
            if "tur" in row:
                a["toolUseResult"] = row["tur"]
        out.append(a)
    return out


def _asm_ckpt_load(leaf_path, rompuuid, sdk_human, candidate_files, links, quiet_inputs=False):
    """The verified document for `leaf_path`, or None after a counted fallback (a document that exists and does not
    verify) or quietly when there is none."""
    cp = _asm_ckpt_file(leaf_path)
    if cp is None or not cp.exists():
        return None
    try:
        data = cp.read_bytes()
        _count_read(str(cp), len(data))
        doc = json.loads(gzip.decompress(data).decode("utf-8"))
    except (OSError, ValueError, EOFError) as e:
        _asm_ckpt_note(leaf_path, "corrupt", str(e)[:80]); return None
    if not isinstance(doc, dict) or doc.get("av") != _ASM_CKPT_V:
        _asm_ckpt_note(leaf_path, "version"); return None
    if doc.get("path") != os.path.realpath(str(leaf_path)) or doc.get("rompuuid") != str(rompuuid) \
            or bool(doc.get("sdkHuman")) != bool(sdk_human):
        _asm_ckpt_note(leaf_path, "session"); return None
    if list(doc.get("cands") or []) != [str(f) for f in candidate_files] or dict(doc.get("links") or {}) != dict(links or {}):
        if quiet_inputs:
            return None                                       # a reader asking about other inputs (the one-file walk over a
        _asm_ckpt_note(leaf_path, "inputs"); return None      #  lineage): not this document's failure, it stands
    try:
        for fsid, f in doc["files"].items():
            st_ = os.stat(f["path"])
            if f.get("skip"):
                if (st_.st_size, st_.st_mtime) != (f["size"], f["mtime"]):
                    _asm_ckpt_note(leaf_path, "lineage", fsid); return None
                continue
            cut_off, pre_n, guard_hex = f["cut"]
            if st_.st_size < cut_off:
                _asm_ckpt_note(leaf_path, "shrunk", fsid); return None
            if st_.st_size < f["size"] or (st_.st_size == f["size"] and st_.st_mtime != f["mtime"]):
                _asm_ckpt_note(leaf_path, "rewrite", fsid); return None
            guard = bytes.fromhex(guard_hex)
            with open(f["path"], "rb") as fh:
                fh.seek(max(0, cut_off - len(guard)))
                ok = fh.read(len(guard)) == guard
            _count_read(f["path"], len(guard))
            if not ok:
                _asm_ckpt_note(leaf_path, "guard", fsid); return None
    except (OSError, KeyError, TypeError, ValueError) as e:
        _asm_ckpt_note(leaf_path, "corrupt", "files: %s" % e); return None
    return doc


def _seed_from_doc(doc):
    """(seed, landed) from a verified document: the pre-cut graph facts a FileAdapter takes, and the pre-cut uuids
    whose records carried assistant text."""
    verdict_of = {"a": "active", "r": "rewind", "e": "eclipsed", "c": "clear", "b": "broken"}
    type_of = {"u": "user", "a": "assistant", "s": "system", "t": "attachment"}
    seed = {"seq_base": int(doc["cutSeq"]) - 1, "verdicts": {}, "types": {}, "spine": [doc["records"][i][0] for i in doc["spine"]],
            "prompt_ids": set(doc["gates"]["prompt_ids"]), "boundary_pids": set(doc["gates"]["boundary_pids"]),
            "skill_use_ids": set(doc["gates"]["skill_use_ids"]), "src_tool_links": set(doc["gates"]["src_tool_links"]),
            "dangling": set(doc["gates"]["dangling"]), "seq_ts": doc.get("seqTs"), "last_ts": doc.get("lastTs"), "cuts": {},
            "file_ends": {fsid: (f.get("first"), f.get("last")) for fsid, f in doc["files"].items()}}
    landed = set()
    for row in doc["records"]:
        u, v, tc, sub = row[0], row[1], row[2], row[3]
        seed["verdicts"][u] = verdict_of.get(v, "broken")
        seed["types"][u] = (type_of.get(tc), sub)
        if row[8]:
            landed.add(u)
    for fsid, f in doc["files"].items():
        seed["cuts"][fsid] = "skip" if f.get("skip") else (int(f["cut"][0]), int(f["cut"][1]), bytes.fromhex(f["cut"][2]))
        if f.get("skip"):
            seed.setdefault("stat", {})[fsid] = (int(f["size"]), float(f["mtime"]))   # the witness a rewrite carries forward
    return seed, landed


def _entry_current(entry, candidate_files):
    """Whether an assembly entry folded everything its files hold now: each file's reader key (generation, base, count)
    equals the entry's, and a skipped lineage file's stat stands. The chain predicate may only answer from an entry
    that is current (its guard reads fresh inputs by contract)."""
    for fp in candidate_files:
        key = entry["recs"].get(str(fp))
        if key is None:
            return False
        if key == ("skip",):
            fst = (entry.get("skipped") or {}).get(str(fp))
            try:
                st_ = os.stat(fp)
            except OSError:
                return False
            if fst is None or (st_.st_size, st_.st_mtime) != tuple(fst):
                return False
            continue
        ent = _read_jsonl_entry(fp, tail_ok=True)
        if ent is None or (ent[6], ent[5] + len(ent[4])) != (key[0], key[2]):
            return False
    return True


def _asm_restore(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human):
    """The entry restored from the leaf's assembly checkpoint, served; None when there is none or it does not verify.
    The pre-cut turns come from the document as lazy atoms; the tail is read from the cut and parsed through an
    adapter seeded with the pre-cut graph facts and the carried emit state; the prefix's identity is proven."""
    doc = _asm_ckpt_load(leaf_path, rompuuid, sdk_human, candidate_files, links)
    if doc is None:
        return None
    try:
        seed, landed = _seed_from_doc(doc)
        fsids = list(doc.get("fsids") or [])
        pre_turns, prefix = [], []
        if doc.get("turns"):
            # the lazy index (T323 stage 4c): the turns from the section, their atoms built on demand; the section's own
            # digest proves it is the one the writer verified against the whole parse (no atom built here)
            if _tree_identity_of_doc(doc["turns"], doc.get("identity")) != doc.get("treeIdentity"):
                _asm_ckpt_note(leaf_path, "identity"); return None
            if sorted(k_ for td in doc["turns"] for k_ in td["atoms"]) != list(range(len(doc["atoms"]))):
                _asm_ckpt_note(leaf_path, "coverage"); return None   # the section does not cover the rows: never a short history
            index = LazyIndex(doc, rompuuid, leaf_path)
            pre_turns = _pre_turns_of(doc, index)
            doc["atoms"] = None                                # the rows live in the index as bytes from here
            with _MAT_LOCK:
                _ASM_INDEX_STATS["restoredTurns"] += len(pre_turns)
        else:
            prefix = _restore_prefix_atoms(doc["atoms"], rompuuid, doc["records"], fsids)
            if _pre_tree_identity(prefix, rompuuid) != doc.get("identity"):
                _asm_ckpt_note(leaf_path, "identity"); return None
        ad = FileAdapter(candidate_files, leaf_path, resume_links=links, seed=seed)
        ad.sdk_human = sdk_human
        st = _emit_state()
        st.update(_carry_decode(doc["carry"]))
        kept = ad.kept_uuids(ad.active_path())
        order = _chrono(ad, kept)
        ad._prepass(order, st)
        atoms = list(ad._emit_fold(order, st, rompuuid, postal_index))
        atoms += ad._absorbed(ad.qatts, kept, st, rompuuid, postal_index)
        entry = {"ad": ad, "st": st, "atoms": atoms, "kept": kept, "landed": landed | ad.landed_text_uuids(),
                 "cands": tuple(str(f) for f in candidate_files), "links": dict(links or {}),
                 "recs": dict(ad._src_keys), "n_qatts": len(ad.qatts), "prefix": prefix, "preTurns": pre_turns,
                 "skipped": {f["path"]: (f["size"], f["mtime"]) for f in doc["files"].values() if f.get("skip")}}
    except Exception as e:                                     # noqa: BLE001 — a document the code cannot use is a fallback
        _asm_ckpt_note(leaf_path, "restore", repr(e)[:120]); return None
    _LAZY_FILES[str(rompuuid)] = {fsid: f["path"] for fsid, f in doc["files"].items()}
    with _ASM_LOCK:
        _ASM_CACHE.pop(key, None)
        while len(_ASM_CACHE) >= _ASM_CACHE_MAX:
            _ASM_CACHE.pop(next(iter(_ASM_CACHE)))
        _ASM_CACHE[key] = entry
    with _ASM_CKPT_LOCK:
        _ASM_CKPT_STATS["restored"] += 1
    return _asm_serve(entry)


def _hydrate_one(a, rec):
    """Fill a lazy atom's body fields from its record, the way the emit built them. An atom another thread finished
    meanwhile (its marker gone) is left as it is (review find C)."""
    lz = a.get("lazy")
    if lz is None:
        return
    k = lz["k"]
    if k == "a":
        a["message"] = _norm_message(rec.get("message"))
    elif k == "u":
        a["message"] = _norm_message(rec.get("message"))
        if lz.get("tur") and isinstance(rec.get("toolUseResult"), dict):
            a["toolUseResult"] = rec["toolUseResult"]
    elif k == "c":
        a["message"] = {"role": "user", "content": [{"type": "text", "text": lz.get("disp", "")}]}
    elif k == "o":
        btext = _text_of(_content(rec.get("message")))
        m = LOCAL_STDOUT_RE.match(btext)
        a["message"] = {"role": "assistant", "content": [{"type": "text", "text": strip_ansi(m.group(1)).strip() if m else ""}],
                        "stop_reason": "end_turn"}
    elif k == "k":
        btext = _text_of(_content(rec.get("message")))
        a["skillMd"] = btext[:SKILL_MD_CAP] + ("\n\n…(skill content truncated)" if len(btext) > SKILL_MD_CAP else "")
        a["message"] = {"role": "assistant", "content": [], "stop_reason": None}
    elif k == "b":
        att = rec.get("attachment") or {}
        full = att.get("prompt") if isinstance(att.get("prompt"), str) else _text_of(att.get("prompt") or [])
        a["message"] = {"role": "user", "content": [{"type": "text", "text": full or ""}]}
    a.pop("lazy", None)


_HYDRATE_TEXT_READERS = set()     # the CODE objects of the shared text readers (the judges' _unit_text, _prompt_text and _atom_text,
#                                   the kernel's _atom_user_texts), each registered where it is defined: a hydration through one is
#                                   attributed with the reader's caller. Matched by code object, never by name (T384 round three: a
#                                   local function named like a reader was consumed as one)


def register_hydrate_text_reader(*fns):
    """Register shared text readers the hydration attribution names together with their caller."""
    for fn in fns:
        _HYDRATE_TEXT_READERS.add(fn.__code__)


def unregister_hydrate_text_reader(*fns):
    for fn in fns:
        _HYDRATE_TEXT_READERS.discard(fn.__code__)


def hydrate_text_reader_registered(fn):
    return fn.__code__ in _HYDRATE_TEXT_READERS


def hydrate(atoms, rompuuid=None, by=None):
    """Fill the bodies of the lazy atoms among `atoms` (a list, a turn's atoms, a whole session's turns) from their
    records on disk, one open per file and one seek-read per atom, through a byte-capped memo; returns how many
    atoms were filled. Every consumer that reads a pre-cut atom's message, toolUseResult or skillMd calls this first
    (a read without it raises LazyBodyRead). `rompuuid` names the session when the atoms carry none. The bytes read
    are counted per caller (`by`, else the calling function's name) under asmCheckpoint.hydratedBy."""
    if isinstance(atoms, dict):
        atoms = [a for t in atoms.get("turns", []) for a in t["atoms"]]
    lazy = [a for a in atoms if isinstance(a, dict) and a.get("lazy") is not None]
    if not lazy:
        return 0
    if by is None:
        try:
            f = sys._getframe(1)
            while f is not None and _synthetic_scope(f):  # a comprehension's or generator expression's own frame is no caller
                f = f.f_back
            by = f.f_code.co_name if f is not None else "?"
            if f is not None and f.f_code in _HYDRATE_TEXT_READERS:   # a text reader every walker shares says nothing about WHO
                g = f.f_back                              #  walked: the first caller outside the shared readers is recorded with it
                while g is not None and (g.f_code in _HYDRATE_TEXT_READERS or _synthetic_scope(g)):   #  (T377: naming the boot's
                    g = g.f_back                          #  reader; a reader reached through another reader, or through a
                by = "%s<-%s" % (by, g.f_code.co_name if g is not None else "?")   #  comprehension's frame, still names the walker)
        except Exception:
            by = "?"
    filled, by_file = 0, {}
    for a in lazy:
        u = a.get("uuid")
        with _ASM_CKPT_LOCK:
            hit = _HYDRATED.get(u) if u else None
            if hit is not None:
                _HYDRATED.pop(u, None); _HYDRATED[u] = hit      # a served body is a used one: to the LRU tail
        if hit is not None:
            if a.get("lazy") is not None:                       # another thread may have finished it since the filter (C)
                _hydrate_one(a, hit[0])
            filled += 1
            continue
        sid = a.get("session_id") or rompuuid
        path = (_LAZY_FILES.get(str(sid)) or {}).get(a.get("fsid"))
        if path is None:
            raise LazyBodyRead("atom %s: no file known for fsid %s" % (u, a.get("fsid")))
        by_file.setdefault(path, []).append(a)
    for path, group in by_file.items():
        # the file's read stripe is held across the group: two threads hydrating the same atoms (the judges' unit text
        # and the frame's markdown at a boot) would both miss the memo and both read; the second now waits and hits it
        with _READ_STRIPES[hash(path) % len(_READ_STRIPES)], open(path, "rb") as fh:
            for a in sorted(group, key=lambda x: (x.get("lazy") or {}).get("at") or (0, 0)):
                lz = a.get("lazy")
                if lz is None:
                    filled += 1; continue                 # another thread hydrated it between the filter and here (the feed's
                u = a.get("uuid")                         #  build and the judges both ask): its body is in place
                with _ASM_CKPT_LOCK:
                    hit = _HYDRATED.get(u) if u else None
                if hit is not None:
                    _hydrate_one(a, hit[0]); filled += 1
                    continue
                at_ln = lz.get("at")
                if not at_ln:
                    raise LazyBodyRead("atom %s: the document carries no record location" % a.get("uuid"))
                at, ln = at_ln
                fh.seek(at)
                raw = fh.read(ln)
                _count_read(path, ln)
                rec = json.loads(raw.decode("utf-8", "replace"))
                if rec.get("uuid") != a.get("uuid"):
                    raise LazyBodyRead("atom %s: the record at its offset is %s" % (a.get("uuid"), rec.get("uuid")))
                with _ASM_CKPT_LOCK:
                    _ASM_CKPT_STATS["hydratedBytes"] += ln; _ASM_CKPT_STATS["hydratedAtoms"] += 1
                    _THREAD_BYTES.hydrated = getattr(_THREAD_BYTES, "hydrated", 0) + ln   # this thread's share (T397)
                    _ASM_CKPT_STATS["hydratedBy"][by] = _ASM_CKPT_STATS["hydratedBy"].get(by, 0) + ln
                    if a.get("uuid"):
                        _HYDRATED[a["uuid"]] = (rec, ln); _HYDRATED_BYTES[0] += ln
                        while _HYDRATED_BYTES[0] > _HYDRATED_CAP and _HYDRATED:
                            _old = _HYDRATED.pop(next(iter(_HYDRATED)))    # the least recently used body goes first
                            _HYDRATED_BYTES[0] -= _old[1]
                _hydrate_one(a, rec); filled += 1
    return filled




def _assemble(leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human, leaf_override,
              mode_out=None):
    """(atoms, landed, cut_t, skill_loads) for parse_session; atoms are caller-owned copies. The one entry
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
        return ad.atoms(rompuuid, postal_index), ad.landed_text_uuids(), cut_t, dict(getattr(ad, "skill_loads", None) or {}), []
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
            elif _CKPT_DIR_FN is not None:
                served = _asm_restore(key, leaf_path, candidate_files, links, rompuuid, postal_index, sdk_human)
                if served is not None:
                    with _ASM_CKPT_LOCK:
                        _ASM_STATS["restore"] += 1
                    _mode("restore")
                    return served
            # A full parse names its road (T398): an entry the gates DEMOTED (the g:<reason> beside it: the leaf's record
            # entry replaced by a from-zero read, a lineage file moved), a leaf with NO document file, a document that
            # stood but was REFUSED at the restore (its fallback reason counted beside), or no checkpoint directory at all.
            with _ASM_CKPT_LOCK:
                refused = _ASM_CKPT_REFUSED.pop(os.path.realpath(str(leaf_path)), None)   # the restore's own refusal, if any
            if entry is not None:
                why = "demoted"
            elif _CKPT_DIR_FN is None:
                why = "noDir"
            elif refused is not None:
                why = "refused"                           # a document stood and did not verify (its reason under fallbacks); the
            else:                                         #  note unlinked it, so the stat below would have read it as none
                cp_ = _asm_ckpt_file(leaf_path)
                why = "noDocument" if cp_ is None or not cp_.exists() else "refused"
            with _ASM_CKPT_LOCK:
                _ASM_STATS["full:" + why] = _ASM_STATS.get("full:" + why, 0) + 1
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
        return ad.atoms(rompuuid, postal_index), ad.landed_text_uuids(), None, dict(getattr(ad, "skill_loads", None) or {}), []


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
    atoms, landed, cut_t, skill_loads, pre_turns = _assemble(leaf_path, candidate_files, links, rompuuid,
                                                postal_index, sdk_human, leaf_override, mode_out=asm_mode_out)
    # a restored tree's pre-cut turns (T323 stage 4c) come whole from the document, their synthesized atoms included; the
    # tail alone is segmented and synthesized over below, and a span or a marker from before the tail's first atom is the
    # document's (or nothing: the whole parse put it in a pre-cut turn)
    t_tail0 = min((a["t"] for a in atoms if a.get("t")), default=None) if pre_turns else None
    orphans = synthesize_orphans(_srows, atoms, landed_text_uuids=landed, rompuuid=rompuuid,
                                 pre=(pre_turns if pre_turns else None), t_floor=t_tail0 if pre_turns else None)
    if pre_turns:
        orphans = [a for a in orphans if t_tail0 is not None and a["t"] >= t_tail0]
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
    idle = synthesize_idle(_srows, atoms, now)
    if pre_turns:
        idle = [a for a in idle if t_tail0 is not None and a["t"] >= t_tail0]
    atoms += idle
    tail_turns = segment_turns(atoms, rompuuid) if atoms else []
    turns = list(pre_turns) + tail_turns
    for turn in tail_turns:
        for a in turn["atoms"]:
            a.pop("_seq", None)
        turn_keys = {"id": turn["id"], "trigger": turn["trigger"], "t": turn["t"],
                     "end": turn["end"], "ended": turn["ended"], "atoms": turn["atoms"]}
        turn.clear()
        turn.update(turn_keys)
    # the cut turn (T323 stage 4b): the first turn after the last one holding a lazy (pre-cut) atom, taken here before
    # any consumer hydrates; 0 for a whole parse. The chat build renders from it (its render floor).
    cut_turn = 0
    if pre_turns:
        cut_turn = min(len(pre_turns), len(turns) - 1)   # the first tail turn (stage 4c: the pre-cut turns are the index's)
    else:
        for _i in range(len(turns) - 1, -1, -1):
            if any(a.get("lazy") is not None for a in turns[_i]["atoms"]):
                cut_turn = min(_i + 1, len(turns) - 1)
                break
    out = {"rompUuid": rompuuid, "name": name or rompuuid, "dir": dir,
            "color": color, "leafFsid": leaf_path.stem, "turns": turns,
            # for the kernel chat build's own marker interleave: its dedup reads the KEPT turns
            # only, so without this a marker whose reply landed on an abandoned branch would
            # ghost back through that second door (sorted → deterministic payloads).
            "landedTextUuids": sorted(landed),
            # the harness's own skill-load wrappers the emit skipped, {uuid: skill name}, over every file the
            # walk crossed: the judge stamps the tops older stores minted from them off this (T333)
            "skillLoads": skill_loads}
    if cut_turn:
        out["cutTurn"] = cut_turn                   # a restored tree only (T323 stage 4b): where its lazy atoms ended
    return out



register_whole_read_passthrough(parse_session)   # the parse's own entry (T384)

def task_store_dir(fsid):
    """Claude Code's task store for one transcript stem: <CLAUDE_CONFIG_DIR or ~/.claude>/tasks/<fsid>,
    resolved at call time (the env var, as task_store_plan reads it)."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(HOME / ".claude")) / "tasks" / str(fsid)


def task_store_fp(fsid):
    """The task store's identity for the judge gate: None when the stem has no store dir (task_store_plan
    returns None too, and the caller folds the transcript instead), else the sorted (name, mtime_ns, size)
    of its item files. An item created, deleted or flipped in place moves it. A dir that exists but cannot
    be listed raises OSError, as task_store_plan does: the gate then runs the stage and stamps nothing,
    and the stage surfaces the failure loudly. Item files are stat'd by name, never read: a stat is all
    identity needs."""
    if not fsid:
        return None
    d = task_store_dir(fsid)
    if not d.is_dir():
        return None
    out = []
    with os.scandir(d) as it:                              # raises OSError → the caller's bypass
        for e in it:
            if not e.name.endswith(".json"):
                continue
            try:
                st = e.stat()
            except FileNotFoundError:
                continue                                   # deleted between the listing and the stat
            out.append((e.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(out))


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
    d = task_store_dir(fsid)
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


def plan_atoms(session):
    """The atoms declared_plan reads bodies from: assistant atoms calling TaskCreate or TaskUpdate and the user atoms
    carrying those calls' results. A lazy atom answers from its scalars (tu = [[id, name]], tr = [tool_use_id]), a
    hydrated one from its blocks, so the list costs no read; hydrating it reads nothing for a session that declared
    no plan (the planner asks every pass for every session with no task store: the whole prefix a pass would
    otherwise seek-read, T323 stage 4a review)."""
    out, ids = [], set()
    for turn in session.get("turns", []):
        for a in turn["atoms"]:
            if a.get("type") != "assistant":
                continue
            lz = a.get("lazy")
            if lz is not None:
                calls = [(i, nm) for i, nm in (lz.get("tu") or [])]
            else:
                calls = [(b.get("id"), b.get("name")) for b in _content(a.get("message"))
                         if isinstance(b, dict) and b.get("type") == "tool_use"]
            mine = [i for i, nm in calls if nm in ("TaskCreate", "TaskUpdate")]
            if mine:
                out.append(a); ids.update(mine)
    if not ids:
        return out
    for turn in session.get("turns", []):
        for a in turn["atoms"]:
            if a.get("type") != "user":
                continue
            lz = a.get("lazy")
            if lz is not None:
                got = lz.get("tr") or []
            else:
                got = [b.get("tool_use_id") for b in _content(a.get("message")) if isinstance(b, dict) and b.get("type") == "tool_result"]
            if ids.intersection(got):
                out.append(a)
    return out


def declared_plan(session):
    """The agent's OWN to-do list (Claude Code's Task tool) folded into ordered items
    [{key, text, activeForm, status}] — the FALLBACK behind task_store_plan for a session with no
    live task store, so downstream (the judge's plan-sync) sees a generic 'declared plan' shape
    instead of raw tool calls. Mirrors the kernel's _fold_tasks, with the same blind spots: only the
    MAIN agent's TaskCreate/TaskUpdate calls, and only those on the transcript's live chain — and the
    same skips: a TaskCreate the CLI rejected (its paired tool_result carries is_error) is not a step,
    and a rejected TaskUpdate moves none.
    `key` is the stable `Task #N` id lifted from TaskCreate's
    result text (a creation-order `cN` fallback if the result is unreadable); `status` rides each
    TaskUpdate. Only TaskCreate/TaskUpdate are folded — plain TodoWrite (no durable ids) is not
    used by romp. Empty list if the session declared no plan."""
    sel = plan_atoms(session)                              # the task calls and their results, in turn order per type
    hydrate(sel, by="declared_plan")                       # only those bodies: nothing read for a session with no plan
    results = {}                                           # tool_use_id → result content (a TaskCreate's carries 'Task #N')
    rejected = set()                                       # tool_use_ids whose result came back is_error
    for a in sel:
        if True:
            if a.get("type") != "user":
                continue
            for b in (a.get("message") or {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    results[b["tool_use_id"]] = b.get("content")
                    if b.get("is_error"):
                        rejected.add(b["tool_use_id"])
    tasks, order = {}, 0
    for a in sel:
        if True:
            if a.get("type") != "assistant":
                continue
            for b in (a.get("message") or {}).get("content", []) or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") == "TaskCreate":
                    # A TaskCreate the CLI rejected (a malformed call — no `subject`, or a {tasks: [...]}
                    # batch — answered by an is_error tool_result naming the missing field) created no task,
                    # so it is not a declared step. Folded, it became a keyless item with empty text that
                    # _sync_declared_plan minted as a standalone open "(declared step)" card no TaskUpdate
                    # could ever close. Keyed on the result's is_error, as the kernel's fold is.
                    if b.get("id") in rejected:
                        continue
                    # Only a TaskCreate's result is ever read, so only it is encoded, here, to the same text
                    # the regex saw when every result was encoded up front, as the kernel's _fold_tasks does.
                    r = results.get(b.get("id"), "")
                    m = re.search(r"Task #(\d+)", (r if isinstance(r, str) else json.dumps(r)) or "")
                    key = m.group(1) if m else "c%d" % order
                    af = inp.get("activeForm")
                    tasks[key] = {"_order": order, "key": key, "text": str(inp.get("subject") or ""),
                                  "activeForm": str(af) if af else None, "status": "pending"}
                    order += 1
                elif b.get("name") == "TaskUpdate":
                    if b.get("id") in rejected:            # the same rejection-keyed skip as the kernel's
                        continue                           # _fold_tasks: a refused update moved nothing
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
        sys.stdout.write(json.dumps(plain_tree(session), indent=1, sort_keys=True, default=lambda o: "<unserializable>"))   # a restored tree's pre-cut turns built plain (stage 4c)
        sys.stdout.write("\n")
    else:
        _dump(session)


if __name__ == "__main__":
    main()
