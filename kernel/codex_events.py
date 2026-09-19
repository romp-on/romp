#!/usr/bin/env python3
"""codex_events — Codex app-server v2 notifications → transcript-shaped records (plans/codex-backend.md).

The Codex backend materializes each thread as transcript JSONL under $STATE/codex/projects/, in the
SAME record vocabulary the Claude CLI writes, so the entire read side — the event model, the judges,
all three panes — parses Codex sessions unchanged. This module is that mapping: one per-thread state
machine over plain wire dicts, no IO and no SDK import, so the goldens run anywhere.

Wire shapes come from the openai-codex SDK's generated bindings (openai_codex/generated/v2_all.py,
pinned 0.144.4) — dicts exactly as received over JSON-RPC, camelCase field names. The mapping table
and what phase 1 deliberately skips live in plans/codex-backend.md; skipped item types are COUNTED
(`skipped`), never silently dropped, so the backend can log the vocabulary it isn't rendering yet.

Chain discipline (adversarial review 2026-08-13): every record's uuid is minted through _mint, which
refuses duplicates and self-parents — a colliding uuid silently ORPHANS all prior history in the
FileAdapter's backward walk (by_uuid keeps the last record; the walk crosses the shared uuid once),
which is the worst possible failure for an append-only transcript. Synthesized uuids (boundaries,
error settles) carry a monotonic sequence for the same reason.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

# A runaway command's aggregatedOutput could balloon the materialized transcript; the Claude CLI
# caps tool results too, so a cap keeps parity. The marker says what was dropped, per house rule.
TOOL_OUTPUT_CAP = 30000

# The Claude CLI's own interrupt settle — event_model.is_interrupt_record keys on this prefix and
# ENDS the turn it lands in. Without it, an interrupt mid-tool leaves the turn open and the NEXT
# prompt is absorbed as mid-turn input instead of opening its own turn (review finding #2).
INTERRUPT_TEXT = "[Request interrupted by user]"


def _cap(text):
    if len(text) <= TOOL_OUTPUT_CAP:
        return text
    return text[:TOOL_OUTPUT_CAP] + "\n… [romp: output truncated at %d chars]" % TOOL_OUTPUT_CAP


def _iso(ms, clock):
    """Record timestamps, Claude transcript format (…T…S.mmmZ). Codex stamps item lifecycles with
    epoch millis (startedAtMs/completedAtMs); events with no wire time (a settle, a thread/compacted
    notification) take the injected clock, so the goldens run on a fixed one."""
    if ms is None:
        ms = int(clock() * 1000)
    dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + ("%03dZ" % (ms % 1000))


def _user_input_texts(content):
    """The prompt texts of a userMessage item's UserInput list, ONE ENTRY PER INPUT, each stripped of
    outer whitespace, an empty one dropped. Non-text inputs keep a readable placeholder (the Claude CLI
    does the same for pasted images), an entry of its own. Per input, never one joined text: the backend
    starts a turn from its WHOLE queue, one input per queued send, and the app-server answers with one
    userMessage item carrying them all. Joined, the record for two queued sends reads
    "first send\nsecond send", which matches neither send's echo (the backend's _append and the kernel's
    prune_live both key an echo by its text), so both echoes stay live for good and paint as user bubbles
    beside the record in every later build. A user record with several text blocks is a shape the kernel
    already reads per block (kernel._atom_user_texts, behind the echo prune and the pending-bubble pass;
    sdk_backend._landed_texts): romp bundles its own injected messages as several blocks in one record.
    So a block per input needs no kernel change."""
    parts = []
    for c in content or []:
        t = (c or {}).get("type")
        if t == "text" and c.get("text"):
            parts.append(c["text"])
        elif t in ("image", "localImage"):
            parts.append("[Image: %s]" % (c.get("path") or c.get("url") or "pasted"))
        elif t in ("skill", "mention") and c.get("name"):
            parts.append(c["name"])
    return [p.strip() for p in parts if p and p.strip()]


def _mcp_text(item):
    """One text blob for an mcpToolCall's outcome: the error message when it failed, else the
    result's MCP content blocks (text kept, the rest summarized by type)."""
    err = item.get("error") or {}
    if err.get("message"):
        return err["message"]
    res = item.get("result") or {}
    parts = []
    for b in res.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            parts.append(b["text"])
        elif isinstance(b, dict) and b.get("type"):
            parts.append("[%s]" % b["type"])
    if not parts and res.get("structuredContent") is not None:
        parts.append(json.dumps(res["structuredContent"], ensure_ascii=False, default=str))
    return "\n".join(parts)


def usage_from_token_usage(token_usage):
    """ThreadTokenUsage.last → the Anthropic usage keys the cost/token readers already understand
    (kernel _session_tokens). reasoningOutputTokens is folded into output (that is what it is).
    `last` is ONE model call's numbers — Codex sends a frame per call within a turn, every tool
    round trip adding one — so the caller sums the frames: a turn's settle carries the whole turn."""
    last = (token_usage or {}).get("last") or {}
    if not last:
        return None
    return {"input_tokens": last.get("inputTokens", 0),
            "output_tokens": last.get("outputTokens", 0) + last.get("reasoningOutputTokens", 0),
            "cache_read_input_tokens": last.get("cachedInputTokens", 0),
            "cache_creation_input_tokens": 0}


class ThreadNormalizer:
    """One Codex thread's notification → record state machine (single writer, append-only file).

    The one stateful trick: a turn's FINAL agentMessage must land already stamped
    stop_reason:"end_turn" — records are append-only, there is no retro-stamp — so a completed
    agentMessage is HELD and flushed by whatever comes next: a further item flushes it
    stop_reason:null (mid-turn text, exactly how the Claude CLI writes it), turn/completed flushes
    it "end_turn" (+ usage). Everything else is a straight mapping; see plans/codex-backend.md.

    Restart/resume: the backend re-anchors `last_uuid` on the materialized file's tail and seeds
    `seen_uuids` with every uuid already in the file, so a notification re-delivered across a
    restart (or an item spanning it) can neither re-mint an existing record nor self-parent.
    """

    def __init__(self, thread_id, cwd="", version="", model="", last_uuid=None, clock=time.time,
                 seen_uuids=None):
        self.thread_id = thread_id
        self.cwd = cwd
        self.version = version        # codex CLI version, stamped like the Claude CLI's `version`
        self.model = model            # kept current by the backend; assistant records carry it
        self.clock = clock
        self.last_uuid = last_uuid    # chain anchor; the backend re-anchors on the file tail at resume
        self.turn_id = None
        self.turn_open = False        # turn/started..turn/completed bracket (the backend's busy signal)
        self.skipped = {}             # phase-2 item vocabulary seen on the wire: type -> count
        self._usage = None            # this turn's tokenUsage frames, summed; stamped on the settle
        self.context = None           # (last-call total tokens, model context window) — the fill %
        self._pending = None          # the held agentMessage: (uuid, ts_ms, text)
        self._minted = set(seen_uuids or ())    # every uuid this FILE already carries
        self._tool_open = set(self._minted)     # item ids whose tool_use record is already on disk
        self._seen_user = set(self._minted)     # userMessage ids already written (started+completed)
        self._error_settled = set()   # turn ids whose failure card is already written (error → turn/completed dedup)
        # In-turn compaction bookkeeping (2026-09-19). _cb_seen: contextCompaction item ids already
        # accounted for, the boundary written or paired with a thread/compacted; seeded from the file
        # like _seen_user, so a re-delivered item is refused on every path. The two counters pair the
        # item and the notification per turn: boundaries written for a turn = max(items, notifications).
        self._cb_seen = set(self._minted)
        self._cb_items = {}           # turn id -> contextCompaction item/completed count
        self._cb_notes = {}           # turn id -> thread/compacted notification count
        self._seq = 0                 # uniquifier for synthesized/colliding uuids
        self._last_ms = 0             # newest timestamp emitted — the floor for clock-stamped records

    # ── record builders (every record advances the uuid chain) ────────────────────────────────
    def _mint(self, uuid):
        """A unique, never-self-parenting uuid. A collision (same uuid twice, or a re-delivered id
        after restart) would orphan the whole prior chain in the parser's walk — refuse it."""
        base = uuid or "cx"
        while uuid in self._minted or uuid == self.last_uuid or not uuid:
            self._seq += 1
            uuid = "%s~%d" % (base, self._seq)
        self._minted.add(uuid)
        return uuid

    def _stamp(self, ts_ms):
        """Timestamps are ordering: a record with no wire time (an interrupt/error settle, a
        boundary) takes the clock FLOORED at the newest emitted timestamp — the wall clock can sit
        behind the wire's item stamps (skew, or a frozen test clock), and a settle stamped earlier
        than the record it follows re-sorts mid-turn in the parse and ends the turn in the wrong
        place. Same-instant is fine (the parser tie-breaks on file order); earlier is not."""
        if ts_ms is None:
            ts_ms = max(int(self.clock() * 1000), self._last_ms)
        self._last_ms = max(self._last_ms, ts_ms)
        return ts_ms

    def _base(self, rtype, uuid, ts_ms):
        uuid = self._mint(uuid)
        r = {"type": rtype, "uuid": uuid, "parentUuid": self.last_uuid,
             "timestamp": _iso(self._stamp(ts_ms), self.clock), "sessionId": self.thread_id,
             "cwd": self.cwd, "version": self.version}
        self.last_uuid = uuid
        return r

    def _user(self, uuid, ts_ms, blocks, prompt_source="sdk"):
        r = self._base("user", uuid, ts_ms)
        r["message"] = {"role": "user", "content": blocks}
        if prompt_source:
            # "sdk" = the human on a programmatic session — author_of(sdk_human=True) renders it
            # as the human bubble, the same convention the SDK backend's sessions use.
            r["promptSource"] = prompt_source
        return r

    def _assistant(self, uuid, ts_ms, blocks, stop=None, usage=None):
        m = {"role": "assistant", "content": blocks, "stop_reason": stop}
        if self.model:
            m["model"] = self.model
        if usage:
            m["usage"] = usage
        r = self._base("assistant", uuid, ts_ms)
        r["message"] = m
        return r

    def _tool_use(self, iid, ts_ms, name, tool_input):
        self._tool_open.add(iid)
        return self._assistant(iid, ts_ms,
                               [{"type": "tool_use", "id": iid, "name": name, "input": tool_input}])

    def _tool_result(self, iid, ts_ms, text, is_error=False, raw=None):
        # content is a plain STRING — the dominant Claude CLI shape and what the kernel's output
        # fills render directly; a block list would display as a JSON dump (review finding #3)
        block = {"type": "tool_result", "tool_use_id": iid, "content": text}
        if is_error:
            block["is_error"] = True
        r = self._user(iid + "-r", ts_ms, [block], prompt_source=None)
        if raw is not None:
            r["toolUseResult"] = raw   # the raw Codex payload, where the kernel's readers look for it
        return r

    def _flush(self, stop=None, usage=None):
        """Write out the held agentMessage (see class docstring)."""
        if not self._pending:
            return []
        uuid, ts_ms, text = self._pending
        self._pending = None
        return [self._assistant(uuid, ts_ms, [{"type": "text", "text": text}],
                                stop=stop, usage=usage)]

    def drain(self):
        """Backend shutdown hook: write out anything held so process death never eats the turn's
        final message. stop stays null — the turn genuinely didn't settle."""
        return self._flush()

    def abandoned(self, turn_id, message):
        """The backend's settle for a turn whose stream ENDED WITHOUT turn/completed — the app-server
        connection died mid-turn, or a transcript write failed — so no notification will ever close
        it. Same shape as a terminal `error`: the held final reply lands mid-turn-shaped, then an
        end_turn record flagged as the error card ENDS the turn. Without it the file's turn stays open
        for good: the kernel reads working from the FILE, so the session shows working while nothing
        runs, and the NEXT prompt is absorbed into the dead turn as mid-turn input instead of opening
        its own (the interrupt settle above exists for the same reason). The turn's usage never lands
        on a later settle, as for a failed turn."""
        self.turn_open = False
        self._usage = None
        return self._error({"turnId": turn_id, "error": {"message": message}})

    # ── the dispatcher ─────────────────────────────────────────────────────────────────────────
    def handle(self, method, params):
        """Map one notification to the records to APPEND (possibly []). The caller owns the file."""
        p = params or {}
        if method == "turn/started":
            self.turn_id = (p.get("turn") or {}).get("id")
            self.turn_open = True
            return []
        if method == "item/started":
            return self._item(p.get("item") or {}, p.get("startedAtMs"), started=True,
                              turn_id=p.get("turnId"))
        if method == "item/completed":
            return self._item(p.get("item") or {}, p.get("completedAtMs"), started=False,
                              turn_id=p.get("turnId"))
        if method == "turn/completed":
            self.turn_open = False
            return self._turn_completed(p.get("turn") or {})
        if method == "thread/compacted":
            return self._compacted(p)
        if method == "thread/tokenUsage/updated":
            tu = p.get("tokenUsage") or {}
            # one frame per model CALL within the turn (`last` = that call's numbers) — summed, so
            # the settle carries the whole turn; an overwrite kept only the final call's, about 1/N
            # of an N-call turn (every tool round trip is a call). Reset at every settle path.
            u = usage_from_token_usage(tu)
            if u:
                if self._usage is None:
                    self._usage = u
                else:
                    for k, v in u.items():
                        self._usage[k] = self._usage.get(k, 0) + v
            # context fill is the LAST call's footprint (what occupies the window right now);
            # `total` is thread-cumulative — a cost number that exceeds the window within a few
            # turns (review finding #7; codex's own TUI meters on `last` too)
            last_total = (tu.get("last") or {}).get("totalTokens")
            if last_total is not None:
                self.context = (last_total, tu.get("modelContextWindow"))
            return []
        if method == "error":
            return self._error(p)
        return []

    def _item(self, item, ts_ms, started, turn_id=None):
        t = item.get("type")
        iid = item.get("id") or ""
        if t == "userMessage":
            if iid in self._seen_user:
                return []
            self._seen_user.add(iid)
            texts = _user_input_texts(item.get("content"))
            if not texts:
                return []
            # a user item mid-turn is a steer: close any held text first, then the prompt record, one
            # text block per input (see _user_input_texts), so a turn started from several queued sends
            # lands each send's text as its own block
            return self._flush() + [self._user(iid, ts_ms,
                                               [{"type": "text", "text": t} for t in texts])]
        if started:
            # calls whose INVOCATION should show the moment it starts (long commands stay visible
            # while they run, like a Claude tool_use does); results land at completed.
            if t == "commandExecution":
                return self._flush() + [self._tool_use(iid, ts_ms, "Bash",
                                                       {"command": item.get("command") or ""})]
            if t == "webSearch":
                return self._flush() + [self._tool_use(iid, ts_ms, "WebSearch",
                                                       {"query": item.get("query") or ""})]
            if t == "mcpToolCall":
                name = "mcp__%s__%s" % (item.get("server") or "?", item.get("tool") or "?")
                args = item.get("arguments")
                return self._flush() + [self._tool_use(iid, ts_ms, name,
                                                       args if isinstance(args, dict) else
                                                       ({"arguments": args} if args is not None else {}))]
            if t == "contextCompaction":
                # nothing to write at started (2026-09-19): the item carries no content, and the
                # boundary is true only at completed, which the runtime emits only after it replaced
                # the history (a compaction that fails never completes its item); the completed arm
                # below writes it
                return []
            return []   # everything else is written at completed, when its content is final
        # ── item/completed ──
        if t == "agentMessage":
            out = self._flush()
            if item.get("text"):
                self._pending = (iid, ts_ms, item["text"])
            return out
        if t == "reasoning":
            parts = [s for s in (item.get("content") or []) if s] or \
                    [s for s in (item.get("summary") or []) if s]
            if not parts:
                return []
            return self._flush() + [self._assistant(iid, ts_ms,
                                                    [{"type": "thinking",
                                                      "thinking": "\n\n".join(parts)}])]
        if t == "commandExecution":
            out = self._flush()
            if iid not in self._tool_open:   # attached mid-turn: write the invocation first
                out.append(self._tool_use(iid, ts_ms, "Bash", {"command": item.get("command") or ""}))
            exit_code = item.get("exitCode")
            status = item.get("status")
            # declined/failed commands arrive with exitCode None — the status is the error signal;
            # a sandbox refusal must never read as a clean success (review finding #8)
            failed = (exit_code not in (0, None)) or status in ("declined", "failed")
            text = item.get("aggregatedOutput") or ""
            if not text and status in ("declined", "failed"):
                text = "[command %s]" % status
            out.append(self._tool_result(iid, ts_ms, _cap(text), is_error=failed,
                                         raw={"exitCode": exit_code, "status": status,
                                              "durationMs": item.get("durationMs")}))
            return out
        if t == "fileChange":
            out = self._flush()
            status = item.get("status")
            failed = status in ("failed", "declined")
            for i, ch in enumerate(item.get("changes") or []):
                cid = "%s-%d" % (iid, i)
                kind = ch.get("kind")
                kind = kind.get("type") if isinstance(kind, dict) else kind
                name = "Write" if kind == "add" else "Edit"
                out.append(self._tool_use(cid, ts_ms, name, {"file_path": ch.get("path") or ""}))
                text = ch.get("diff") or ""
                if failed:
                    text = ("[patch %s]\n" % status) + text
                out.append(self._tool_result(cid, ts_ms, _cap(text), is_error=failed,
                                             raw={"kind": kind, "status": status}))
            return out
        if t == "mcpToolCall":
            out = self._flush()
            if iid not in self._tool_open:
                name = "mcp__%s__%s" % (item.get("server") or "?", item.get("tool") or "?")
                args = item.get("arguments")
                out.append(self._tool_use(iid, ts_ms, name,
                                          args if isinstance(args, dict) else
                                          ({"arguments": args} if args is not None else {})))
            out.append(self._tool_result(iid, ts_ms, _cap(_mcp_text(item)),
                                         is_error=bool(item.get("error"))))
            return out
        if t == "webSearch":
            out = self._flush()
            if iid not in self._tool_open:
                out.append(self._tool_use(iid, ts_ms, "WebSearch", {"query": item.get("query") or ""}))
            out.append(self._tool_result(iid, ts_ms, "done"))
            return out
        if t == "contextCompaction":
            # The boundary writer (2026-09-19). Codex 0.153.3 emits every compaction inside a running
            # turn (pre-turn on a context limit, mid-turn when a follow-up call is due) as a
            # contextCompaction item on THAT turn, completed only after the history was replaced, and
            # has deprecated thread/compacted (its app-server never constructs the notification), so
            # this item is the one exact signal; _compacted stays as a dedup for a runtime that sends
            # both. Trigger "auto" is exact: a registered turn is one romp opened with turn/start, and
            # every compaction inside one is automatic (the manual compaction runs as its own turn,
            # which romp never registers). The uuid is the wire item id, stable across a re-delivery,
            # which is what the replay guard needs. In the pre-turn shape the boundary is followed
            # directly by the turn's prompt, with no summary record between (Codex exposes none); the
            # read side's replay window arms on the summary record, never on this boundary (the
            # event-model change this one is stacked on, 2026-09-19), so that prompt survives even
            # when it repeats earlier text; the repeated-prompt parse golden pins that order. The
            # counters count a turn they have never seen instead of raising: the backend's turn worker
            # treats a raise from handle as a dead turn (interrupts it and writes the abandoned card).
            tid = turn_id or self.turn_id or "0"
            if iid and iid in self._cb_seen:
                return []   # re-delivered across a reconnect/restart: written, or paired, already
            if iid:
                self._cb_seen.add(iid)
            self._cb_items[tid] = self._cb_items.get(tid, 0) + 1
            if self._cb_items[tid] <= self._cb_notes.get(tid, 0):
                return []   # a thread/compacted for this turn already wrote this compaction's boundary
            return self._boundary(iid or "cb-%s" % tid, ts_ms, "auto")
        # phase-2 vocabulary (plan, subAgentActivity, collabAgentToolCall, imageView, …) — counted
        if t:
            self.skipped[t] = self.skipped.get(t, 0) + 1
        return []

    def _turn_completed(self, turn):
        status = turn.get("status")
        err = turn.get("error") or {}
        tid = turn.get("id") or self.turn_id or "turn"
        if status == "failed" or err.get("message"):
            self._usage = None   # never stamp this turn's numbers on a later settle (finding #9)
            out = self._flush()
            if tid in self._error_settled:
                return out       # the terminal `error` notification already wrote this failure card
            rec = self._assistant("%s-err" % tid, None,
                                  [{"type": "text", "text": err.get("message") or "turn failed"}],
                                  stop="end_turn")
            rec["isApiErrorMessage"] = True
            out.append(rec)
            return out
        if status == "interrupted":
            # partial text lands mid-turn-shaped (stop null); the CLI-convention interrupt record
            # then ENDS the turn (is_interrupt_record), so the next prompt opens its own turn
            # instead of being absorbed into this one (review finding #2)
            self._usage = None
            out = self._flush()
            out.append(self._user("%s-int" % tid, None,
                                  [{"type": "text", "text": INTERRUPT_TEXT}], prompt_source=None))
            return out
        usage, self._usage = self._usage, None
        return self._flush(stop="end_turn", usage=usage)

    def _boundary(self, uuid, ts_ms, trigger):
        """The one compact_boundary writer (2026-09-19), shared by the contextCompaction item arm and
        the thread/compacted arm so the shape the read side follows (the FileAdapter walks
        logicalParentUuid, segment_turns opens the boundary's own turn, build_session emits a compact
        event with the trigger) cannot drift between the two. The held reply is PRE-compaction
        content: it lands before the boundary so the stitch points at the true leaf and file order
        stays chronological (review finding #4). compactMetadata carries the trigger and nothing
        else: Codex exposes no summary text and no token counts for a compaction, the client draws
        the token line only when preTokens is present, and the last tokenUsage frame during a
        compaction is the compaction call's own input, so a derived count would be a guess (absent,
        not faked). No isCompactSummary record follows, so the card's "what compaction kept" stays
        absent for Codex sessions."""
        out = self._flush()
        uuid = self._mint(uuid)
        rec = {"type": "system", "subtype": "compact_boundary", "uuid": uuid, "parentUuid": None,
               "logicalParentUuid": self.last_uuid, "timestamp": _iso(self._stamp(ts_ms), self.clock),
               "sessionId": self.thread_id, "cwd": self.cwd, "version": self.version,
               "isMeta": False, "compactMetadata": {"trigger": trigger}}
        self.last_uuid = uuid
        out.append(rec)
        return out

    def _compacted(self, p):
        # thread/compacted (2026-09-19): deprecated on Codex 0.153.3 and never sent; the
        # contextCompaction arm of _item is the writer. This arm stays for a runtime that sends both
        # signals, as a dedup: boundaries written for a turn = max(items seen, notifications seen), so
        # whichever signal arrives first writes and the other is a pairing, once per compaction. The
        # pairing keys on the notification's OWN turnId, never on the self.turn_id fallback: a call
        # with no turnId (a synthesized one for a standalone compaction) must always write, and
        # self.turn_id is the LAST romp turn's id, so a fallback-keyed pairing would swallow it
        # whenever that turn carried an in-turn compaction. A wire thread/compacted always carries
        # turnId. The trigger passes through when the caller names one.
        wire_tid = p.get("turnId")
        if wire_tid:
            self._cb_notes[wire_tid] = self._cb_notes.get(wire_tid, 0) + 1
            if self._cb_notes[wire_tid] <= self._cb_items.get(wire_tid, 0):
                return []   # the item already wrote this compaction's boundary
        return self._boundary("cb-%s" % (wire_tid or self.turn_id or "0"), None,
                              p.get("trigger") or "auto")

    def _error(self, p):
        if p.get("willRetry"):
            return []   # transient — the backend may chip it; the transcript records outcomes
        err = (p.get("error") or {})
        tid = p.get("turnId") or self.turn_id or "0"
        self._error_settled.add(tid)   # turn/completed(failed) for the same turn stands down
        out = self._flush()
        rec = self._assistant("err-%s" % tid, None,
                              [{"type": "text", "text": err.get("message") or "error"}],
                              stop="end_turn")
        rec["isApiErrorMessage"] = True
        out.append(rec)
        return out
