#!/usr/bin/env python3
"""The SessionBackend contract — ONE clean, backend-agnostic session API.

romp drives Claude Code sessions through two backends: the legacy TMUX backend (a Claude Code TUI running
in a tmux pane, controlled by shelling `tmux`) and the SDK backend (`romp_sdk_backend.SdkBackend`, the
Agent SDK). Historically the kernel + the postal bus reached straight past this split and shelled tmux
inline, so tmux assumptions leaked all over the higher layers. This ABC formalizes the contract both
backends already (de-facto) honor, so EVERYTHING above the backend speaks one API and nothing shells tmux
except the one TmuxBackend that implements it (a guard test enforces that — see tests/test_session_api.py).

The API is SID-KEYED (a romp session uuid), the kernel's native identity, even though tmux is keyed by
session NAME — a backend maps sid→its own handle internally. `SdkBackend` conforms by duck-typing (it is
SDK-gated, so it can't import this module when the SDK dep is absent); `TmuxBackend` inherits this ABC.
A conformance test asserts SdkBackend implements every abstract method, so the duck-typing can't drift.

Method groups:
  liveness/identity — owns, live_sessions
  control           — send, interrupt, set_model, set_mode, set_effort, set_fast
  lifecycle         — spawn, resume, move, connect, kill, rename
  coordination      — working_note, set_working_note, wake   (backend-agnostic; tmux used @romp-working +
                      send-keys, the SDK now gets a store + an enqueue-wake so it has both too)
  chat tail         — pending_queued, live_atoms, prune_live
  ask picker        — on_ask, current_ask

A backend that genuinely cannot do an op returns the documented empty value (False / [] / "" / None) rather
than raising, so callers never need to know which backend they hold.
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod


def echo_text_key(text) -> str:
    """The one rule under which an input echo's text and a transcript record's user text are compared:
    outer whitespace stripped, nothing else. Every reader shares it and must agree: the kernel's
    _atom_user_text(s) (the keys of the `tx_user_texts` mapping prune_live receives, and the sets the
    queued fold, _merge_live_atoms, _comments_frame and _tmux_echo_prune compare against),
    the SDK and Codex prune_live by-text retire (the echo side of that comparison), and SdkBackend._text_landed
    / _landed_texts (the transcript scan behind the boot and dead-spawn duplicate guard). Until 2026-09-06
    the scan collapsed internal whitespace while the prune compared the raw echo text against stripped
    keys, so a send whose text carried a trailing newline (`romp send` passes its argument verbatim) was
    FOUND by the scan, neither re-fed nor flagged, and never pruned or dismissable. Strip is as wide as
    the data needs: the CLI stores user text verbatim (checked against recorded SDK transcripts,
    2026-09-06 — double spaces, bare CRs and line-trailing blanks all preserved). Not a str → ""."""
    return text.strip() if isinstance(text, str) else ""


# A command-name-shaped token: a slash, then characters with no further slash or whitespace ("/deploy",
# "/plugin:skill"). A path ("/tmp/build.log") has a further slash and is not one; a message that starts
# with a path keeps the plain rule alone.
_COMMAND_TOKEN_RE = re.compile(r"^/[^/\s]+$")


def command_text_key(text) -> str:
    """The second rule, for a SLASH-COMMAND send only: the text's whitespace-delimited tokens joined by
    single spaces, or "" when the first token is not command-name-shaped (_COMMAND_TOKEN_RE; a plain
    message has no command key). Claude Code records a slash send as a wrapper (<command-name>,
    <command-args>) and, for a skill, the skill body as a second record: the wrapper is the record it
    always writes, and a verbatim copy of the typed text is not guaranteed (some CLI versions also write a
    raw twin record, which matches under echo_text_key when present). The event model reads the wrapper
    back as a command atom whose text is "/name args" with ONE space between them, whatever whitespace
    the sender typed there. So a send like "/deploy \\n\\nstaging now" never met its own record under
    echo_text_key: the chat kept the sending bubble forever while the turn ran, and the boot/spawn scan,
    which reads the raw records, called the send lost and re-delivered it (reported from a live dashboard,
    2026-09-10). The match is the same words in the same order: the whitespace the CLI drops between the
    name and the arguments, or reflows inside them, never decides, and a different command or different
    arguments never lands the echo. Every reader that compares under echo_text_key compares under this
    key too, on BOTH sides: the kernel's _atom_user_texts adds it for each slash-shaped record text, the
    backend's _landed_texts does the same (and, for a wrapper record, adds the "/name args" the event
    model would read), and the echo side asks for either key through echo_keys (kernel._echo_landed_in,
    SdkBackend.prune_live, _text_landed and qids_for_landing). Not a str → "". The common case, a text
    that does not start with a slash, is settled before any split: this runs for every user text on
    every build."""
    if not isinstance(text, str):
        return ""
    s = text.lstrip()
    if not s.startswith("/"):
        return ""
    toks = s.split()
    if not _COMMAND_TOKEN_RE.match(toks[0]):
        return ""
    return " ".join(toks)


def echo_keys(text) -> tuple:
    """The keys an echo's text is looked up under: echo_text_key, and command_text_key when the text is a
    slash send. Empty and duplicate keys dropped, so a plain text yields one key and a slash send one or
    two. The one place the "either key" rule is written; every echo-side reader (kernel._echo_landed_in
    and the thread's held count, SdkBackend.prune_live, _text_landed, qids_for_landing) reads it from
    here, and two texts match when their key tuples intersect."""
    keys = []
    for k in (echo_text_key(text), command_text_key(text)):
        if k and k not in keys:
            keys.append(k)
    return tuple(keys)


class SessionBackend(ABC):
    # True when busy() may be overruled by the cached transcript parse, so the parked-op drain must keep that
    # parse current for a sid before it reads busy() (_refresh_parked_parse); a backend whose busy() is the
    # whole truth (SDK, Codex) leaves it False and its parked sids are never re-parsed on its account.
    corroborates_with_transcript = False

    # ── liveness / identity ──────────────────────────────────────────────────────────────────────
    @abstractmethod
    def owns(self, sid: str) -> bool:
        """True if THIS backend currently drives `sid`. The dispatcher routes per-sid ops to whichever
        backend owns the sid (see Sessions.backend_for)."""

    @abstractmethod
    def live_sessions(self) -> dict:
        """{sid: {state, model, effort, mode, since, context, color, backend, ...}} for every session this
        backend currently runs. state ∈ working|waiting|idle|permission|compacting (compacting/context% are
        tmux-only → None elsewhere). The kernel MERGES every backend's map for one fleet-wide liveness view."""

    # ── control (per-sid) ────────────────────────────────────────────────────────────────────────
    @abstractmethod
    def send(self, sid: str, text: str) -> bool:
        """Deliver a user message / command to `sid` (the chat composer, /compact, retry, an injected
        nudge). Truthy if delivered/queued — a backend may answer with a richer truthy value than
        True (a handle its caller threads into later bookkeeping about that delivery), so callers
        gate on truthiness, never on `is True`."""

    @abstractmethod
    def interrupt(self, sid: str) -> bool:
        """Stop the in-flight turn (Esc) and leave the input clean."""

    def busy(self, sid: str) -> "bool | None":
        """AUTHORITATIVE 'is a turn in flight (or queued) right now' from the backend that actually drives
        the CLI — or None when the backend has no such signal, so the caller falls back to the event-model
        parse. The kernel's park-or-fire gate (_ops_gate) otherwise reads the CACHED transcript parse, which
        LAGS a just-started turn: the transcript isn't written until the turn produces output, so a drive op
        pressed in that window saw 'not working', bypassed the FIFO, and fired immediately — /compact jumped
        ahead of a model/message pressed right after it, and the parked ops then stalled (the user 2026-07-14,
        reproduced: compact→model→send pressed 150ms apart delivered out of order). The SDK knows its inflight
        count exactly; tmux reads the hook-maintained @claude-state — working / permission / picker → True,
        waiting / idle → False, compacting → None (the compacting gate owns it), and None for an unknown
        word, no row, or a row another backend owns — an answer the transcript overrules when the cached
        parse's newest record is newer than the row's since (an Esc fires no hook, so a stale row can say
        working over an ended turn)."""
        return None

    def compacting(self, sid: str) -> "bool | None":
        """AUTHORITATIVE 'is a /compact in progress right now', or None when the backend has no such signal
        (→ the kernel's optimistic/tmux compacting derivation). The kernel otherwise infers SDK compaction
        from an OPTIMISTIC stamp with a 180s cap: when /compact finds nothing to compact, no compact_boundary
        event ever lands, so that cap held parked ops (a model pick, a queued message) for up to 3 minutes
        (the user 2026-07-14). The SDK brackets it exactly — set on /compact delivery, cleared by the boundary
        or the /compact turn's settle; tmux keeps the None default (its @claude-state path is unchanged)."""
        return None

    def clearing(self, sid: str) -> "bool | None":
        """AUTHORITATIVE 'is a /clear in progress right now', or None when the backend has no such signal.
        The bracket exists so the chat can show a live "clearing" indicator instead of a dead gap: between
        the /clear delivery and the CLI minting the fresh transcript there is otherwise NO observable state
        anywhere (the episode boundary is only detected after the fact, by the episodes tick). SDK: set on
        /clear delivery, cleared event-based by the init that flips lastSid (the fork landing) or the turn's
        settle. tmux keeps the None default — a TUI /clear there surfaces as a fork lane, with no bracket
        (the known tmux gap in plans/clear-episodes.md)."""
        return None

    def launch_error(self, sid: str):
        """Why this session's CLI could NOT start — {text, at, limit} — or None when it started fine (and
        on a backend with no such signal). A launch failure is otherwise invisible: the session settles
        'waiting', the message the user typed stays in the queue, and nothing anywhere says why (the user
        2026-07-28, whose send into an out-of-usage account simply never flipped to working). `limit` is
        True when the cause is the ACCOUNT being out of usage rather than a broken session — the queue is
        parked, not lost, and the kernel says so (_limit_hold) instead of showing a red error.

        tmux keeps the None default: its CLI launches into a pane where the failure is on screen."""
        return None

    def forwards_sends(self) -> bool:
        """True if this backend accepts a plain composer send at ANY time — even mid-turn — and manages its
        own delivery: forwarding the message to the model at the next tool boundary, folding several queued
        sends into one turn, and holding them across an interrupt until the turn settles (SdkSession._pending
        + its inputs() generator). The kernel then hands composer sends straight to send() the instant they
        arrive (the user 2026-07-17, who wanted them in as soon as possible), instead of parking them itself.
        False (default) means the backend has no such queue, so the kernel holds sends while a turn runs and
        merges them into one message at turn end (tmux). Slash-command drive ops (/compact, /effort, …) still
        park in the kernel FIFO on BOTH backends to preserve press-order — this flag governs plain text sends
        only; a model pick has its own capability, model_switches_live."""
        return False

    def model_switches_live(self) -> bool:
        """True if a model pick may be applied to a RUNNING session mid-turn — then the kernel fires it into
        an open turn instead of parking it in the FIFO until the turn ends (PR #923). False (default) means
        the pick waits for the turn: tmux TYPES /model into the pane; Codex's set_model lands at the next
        turn_start while its sends steer the live turn, so a send typed after a mid-turn pick would reach
        the OLD model first; and the SDK, whose set_model does ride the CLI's control channel, still says
        False because the CLI mis-parents a mid-turn switch's transcript breadcrumbs and orphans the rest of
        the turn (see SdkBackend.model_switches_live for the evidence and the flip conditions). Deliberately
        distinct from forwards_sends: forwarding a plain send says nothing about how a model change applies."""
        return False

    @abstractmethod
    def set_model(self, sid: str, value: str) -> bool:
        """Switch the session's model. `value` is a family alias (opus — the CLI resolves it to the newest),
        an explicit version id (a pin), or 'default'. Every surface lands here — the statusline picker, the
        timeline lane menu, a '/model X' typed into the composer or sent by `romp send` (kernel
        _route_meta_command) — so the registry and the remembered defaults never drift from what the CLI
        runs. The SDK persists the value and applies it LIVE over the SDK's set_model control request, no
        reconnect; a refusal reverts every layer (SdkBackend.set_model). tmux types the CLI's own '/model X'
        into the pane and presses Enter once more to accept the confirmation the CLI asks for
        (TmuxBackend.set_model, `_tmux_send(model_cmd=True)`): the dashboard cannot press it in the pane,
        and the next send's clear-guard would refuse to paste over the open dialog.
        False when the backend can tell the change did not land, so the kernel can be loud instead of
        pretending. The SDK and Codex refuse an unknown sid (no registry row, no session); Codex refuses a
        model outside its engine; the SDK's own writes are optimistic (True at once, every layer reverted
        when the CLI refuses the value live — SdkSession._do_set_model). The tmux side cannot tell: the
        command is pasted into the pane from a daemon thread whose delivery failures land on stderr
        (`_tmux_send`), and a bad value is answered by the CLI in the pane, so TmuxBackend.set_model
        returns True unconditionally and the caller vouches for the value first — _route_meta_command
        checks it against the kernel's catalog (_vouched_model) and the pickers offer only catalog rows;
        POST /new passes its model through verbatim by design, so a typo there reaches the pane and the
        CLI answers it there."""

    @abstractmethod
    def set_mode(self, sid: str, mode: str) -> bool:
        """Set the permission mode (auto/default/acceptEdits/plan/…)."""

    @abstractmethod
    def set_effort(self, sid: str, value: str) -> bool:
        """Set the reasoning effort (one of the kernel's effort choices, 'low' through 'ultracode'). The two
        backends land it differently from each other and from set_model: effort is a connect-time CLI flag
        (--effort) with no SDK control request, so the SDK persists the value and RECONNECTS to apply it —
        at once if the session is idle, at the end of the turn if it's busy — with `effortPending` driving
        the badge's switching-dots and the chat's "Reloading session…" notice (SdkBackend.set_effort). tmux
        types '/effort X' into the pane, which the TUI applies in place: no reconnect, no confirmation, so
        no second Enter (TmuxBackend.set_effort). False when the backend can tell the change did not land,
        so the kernel can be loud instead of pretending: the SDK and Codex refuse an unknown sid (no
        registry row, no session); the SDK also refuses a value outside EFFORT_LEVELS, Codex a level its
        engine lacks. tmux cannot tell (it types the command; the TUI answers a bad value in the pane), so
        TmuxBackend.set_effort returns True unconditionally and the
        caller vouches for the value first — _route_meta_command checks _EFFORT_VALUES and the picker
        offers only those; POST /new passes its effort through verbatim by design, so a typo there reaches
        the pane and the CLI answers it there."""

    @abstractmethod
    def set_fast(self, sid: str, value: str) -> bool:
        """Toggle fast mode (value 'on'|'off'). tmux delivers the literal '/fast on|off' text into the
        pane; the SDK opts in at connect (the `fastMode` flag-settings key) and takes the literal text
        only on a connection made with that flag — see SdkBackend.set_fast for the hybrid. False when
        it can't be applied (bad value, unknown sid) so the kernel can be loud instead of pretending."""

    def set_auth(self, sid: str, value: str) -> bool:
        """Pick which account this session bills — 'login' (the machine's Claude login) or 'key' (the
        API key the manager's environment carries). SDK-only control: an SDK session's CLI inherits
        the KERNEL's environment, so the kernel owns the choice (SdkBackend injects or withholds the
        key per session at connect). A tmux session's CLI lives in the tmux server's environment,
        which the kernel does not control — the default False means "no such control here" and the
        kernel warns on a refusal instead of pretending."""
        return False

    def stop_task(self, sid: str, task_id: str) -> bool:
        """Stop ONE background task (the SDK's designed stop_task control request). SDK-only control:
        the chat's bg-task box only ever shows live tasks for SDK sessions (the CLI's task lifecycle
        stream), so this default False just means "no such control here" (tmux)."""
        return False

    def mcp_status(self, sid: str):
        """(servers, error) — the live MCP server list for this session (the SDK's get_mcp_status).
        SDK-only: tmux sessions render the CLI's own /mcp panel in their pane, so the default says so
        rather than pretending an empty list is the truth."""
        return [], "MCP status is available on SDK sessions; this one runs in a terminal — use /mcp there"

    def mcp_action(self, sid: str, name: str, action: str, enabled: bool = True) -> str:
        """"" on success, else why not. Enable/disable or reconnect one MCP server (SDK control requests)."""
        return "MCP controls are available on SDK sessions; this one runs in a terminal — use /mcp there"

    def rewind_files(self, sid: str, uuid: str) -> bool:
        """Restore workspace files to their state before a user message (the SDK's rewind_files,
        backed by file checkpointing). SDK-only control — the default False means "no such control
        here" (tmux), and the kernel warns the user on a refusal."""
        return False

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────────
    @abstractmethod
    def spawn(self, name: str, cwd: str, bg: str = "", fg: str = "", sid: str | None = None,
              auth: str = "") -> str | None:
        """Start a NEW session; return its sid (or None on failure). `auth` ('login'|'key'|'') is the
        picker's per-session billing pick — meaningful on the SDK backend only (see set_auth)."""

    @abstractmethod
    def resume(self, name: str, sid: str, cwd: str | None = None) -> bool:
        """Revive a DEAD session by sid (resumes its conversation)."""

    def move(self, sid: str, cwd: str) -> str:
        """Move a session's working directory to `cwd` — its conversation, transcript and identity
        follow it (the user 2026-09-01, who wanted a session to follow a subproject promoted to its own
        repo). "" on success; "busy" when a turn is in flight (the kernel parks the op and retries at
        turn end); any other string is the reason it did not happen, shown to the user verbatim. SDK-only
        control (the CLI's `set_cwd` control request — see SdkBackend.move): a backend with no relocation
        primitive romp can drive inherits this refusal, worded for no backend in particular, so a new
        backend never reads as movable by omission (TmuxBackend.move spells out the terminal case)."""
        return ("this session's backend has no way to move a running session — "
                "start a new session in that folder instead")

    @abstractmethod
    def kill(self, sid: str) -> bool: ...

    @abstractmethod
    def rename(self, sid: str, new_name: str) -> bool: ...

    # ── coordination (working-note + deliver-time wake) ──────────────────────────────────────────
    # Concrete no-op defaults so the EXISTING contract (what SdkBackend already has) stays the abstract
    # surface for P0; P3 makes these real on both backends (tmux had @romp-working + a pane Enter; the SDK
    # gets a backend-agnostic store + an enqueue-wake) and promotes them to part of the enforced contract.
    def working_note(self, sid: str) -> str:
        """The session's published 'what I'm working on' ownership note for the postal bus (list_agents),
        or '' if none. Backend-agnostic: tmux stored it in @romp-working, the SDK in a kernel-side store."""
        return ""

    def set_working_note(self, sid: str, text: str) -> None:
        """Publish (text) or clear (text='') the session's working-note."""
        return None

    def wake(self, sid: str) -> bool:
        """Nudge `sid` to PROCESS pending input now (e.g. mail just delivered to a session sitting idle).
        tmux pressed Enter in the pane; the SDK enqueues a drain. True if a wake was issued."""
        return False

    def deliver(self, sid: str, text: str) -> bool:
        """Live-deliver a postal banner to `sid` as the deliver-time WAKE — put it into the session's input so
        an idle recipient surfaces the mail NOW instead of on its next turn. tmux pastes it into the pane
        (draft-preserving); the SDK enqueues it. True iff delivered. Default: not delivered (the postal bus
        then leaves the mail for its maildir-drain backstop). The bus reaches this via the kernel's POST
        /deliver so it never shells tmux."""
        return False

    # ── chat tail ────────────────────────────────────────────────────────────────────────────────
    @abstractmethod
    def pending_queued(self, sid: str) -> list:
        """User messages submitted while busy that haven't started yet (the chat's 'queued' indicator)."""

    @abstractmethod
    def live_atoms(self, sid: str) -> list:
        """In-memory chat-tail atoms AHEAD of the transcript on disk (the optimistic input echo + any live
        stream), [] if none. Merged before the on-disk parse so a just-sent message shows instantly."""

    @abstractmethod
    def prune_live(self, sid: str, tx_uuids, tx_user_texts=(), human_floor: int = 0) -> None:
        """Drop live atoms the transcript now carries (by uuid or echo text), so they don't double-show.
        THE KERNEL'S CALL SHAPE, which every backend must accept (kernel._merge_live_atoms passes all four
        positionally; tests/test_backend_call_parity.py pins the shape against each backend): `tx_uuids` is
        the set of record uuids on disk; `tx_user_texts` maps each landed user text (keyed by echo_text_key)
        to the NEWEST record time carrying it, so an echo lands by text only through a record written at or
        after its own send (T237b: "ok" twice must not retire the second before its record), while a plain
        set from an older caller keeps the unfloored match; `human_floor` is the newest genuine-human
        record's time, the event that says the transcript has moved past anything typed before it. No floor
        retires a plain input echo on any backend: the SDK uses it to retire stale COMMAND feedback atoms,
        the tmux backend to SETTLE an overtaken echo as dropped, and the Codex backend accepts it and retires
        nothing by it. A backend that declares a narrower signature than this raises TypeError on every live
        merge of one of its sessions that holds a live atom."""

    # ── ask picker ───────────────────────────────────────────────────────────────────────────────
    @abstractmethod
    def on_ask(self, sid: str, kind: str, payload=None) -> bool:
        """Drive a live AskUserQuestion picker (answer/focus/toggle/submit/custom/cancel/text)."""

    @abstractmethod
    def current_ask(self, sid: str):
        """The session's live AskUserQuestion state for the webview, or None."""
