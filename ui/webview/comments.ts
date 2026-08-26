// Comment threads (the user 2026-08-13): highlight a passage in the chat, comment on it, and a side
// conversation opens right there — an anchored highlight + popover, powered kernel-side by a fork of
// the session cut at the anchored message. This module is the PURE half (node-testable, no DOM):
// thread types, the whitespace-tolerant exact-text matcher that re-finds a highlight inside a
// re-rendered turn, and the small derivations the popover renders from. All DOM wiring lives in
// render.ts (source-pinned by comments.test.ts, the repo convention).

export type CommentMsg = { who: "you" | "agent"; text: string; t: number };

export type CommentThread = {
  tid: string;
  name?: string;              // the thread's editable name (<session>-comment-<N> by default)
  color?: string;             // the comment's identity color — picked distinct from its parent's
  anchorUuid: string;
  exact: string;
  status: "open" | "resolved" | "promoting" | "promoted" | "merging" | "merged";   // merging/merged: folded back into the parent (the user 2026-08-23)
  createdT: number;
  state: string;              // the thread session's live state ("working"/"waiting"/…, "" when dormant)
  error?: string;             // the thread CLI's launch error, when it could not start
  unread: boolean;            // an agent reply newer than the read watermark
  sinceEpoch?: number;        // ms epoch the thread's current state began — the popover chip's timer
  mode?: string;              // the thread's permission mode — the popover statusline's Auto badge
  fast?: string;              // fast-mode state ("on"/"off"/"cooldown"; "" = unknown → no badge)
  modelColor?: number[];      // the chat statusline's rank tints, so metaColor paints the popover
  effortColor?: number[];     //   badges exactly as the chat's (the 2026-08-25 color rider)
  promotedName: string;       // the board session it became, when status === "promoted"
  model?: string;             // the thread's live/chosen model (the popover's switchable chip)
  effort?: string;            // the thread's effort level (ditto)
  msgs: CommentMsg[];
  events?: unknown[];         // the CHAT's own ChatEvents from the branch point on (render parity)
};

export type CommentsFrame = { type: "comments"; id: string; threads: CommentThread[] };

/** Threads grouped by the turn they anchor to — what the mark/badge pass walks per rendered view. */
export function threadsByAnchor(threads: CommentThread[]): Map<string, CommentThread[]> {
  const by = new Map<string, CommentThread[]>();
  for (const th of threads) {
    const list = by.get(th.anchorUuid);
    if (list) list.push(th); else by.set(th.anchorUuid, [th]);
  }
  return by;
}

/** The thread session is mid-turn — the popover shows its thinking dots. */
export function threadBusy(state: string): boolean {
  return state === "working" || state === "retrying" || state === "compacting";
}

/** The thread session is stuck on an interactive prompt the popover can't answer — say so, and point
 *  at Break out (a full session can). */
// A reply is OWED the moment the user's message is the thread's newest with no agent reply landed
// since (the user 2026-08-24, second report: the mark flashed green on create, dropped to YELLOW
// while the thread CLI was still booting — its live state read idle, a flapping boot-time proxy —
// then went green again once generation started). The in-flight color keys on the EXCHANGE's own
// events: user message in → green until the reply message lands, however the worker session's state
// wobbles on the way. The find-the-event rule, applied to a color.
export function replyOwed(th: CommentThread): boolean {
  const last = th.msgs.length ? th.msgs[th.msgs.length - 1] : null;
  return !!last && last.who === "you";
}
// THE EXCHANGE LATCH (T102, the user 2026-08-26 — replacing the push-count settle): the busy pulse
// is exchange-scoped. It LATCHES at the user's SEND gesture (client-side, optimistic — before any
// kernel round-trip; thread-open must never be the start trigger) and CLEARS only on the
// REPLY-ARRIVED event for that send: the agent's reply RECORD landing in the thread's projected
// msgs — concretely, th.msgs holding MORE who==="agent" entries than it held at the send. Counts of
// the exchange's own records: never wall clocks (cross-host transcripts skew) and never push counts
// (the banned proxy — the old two-quiet-pushes settle counter killed the create-window green
// while the fork booted, and any stall in its 0→1→2 stepping parked green forever with no event to
// clear it). agentCount is the reply-arrived detector's datum; render.ts holds the per-send base.
export function agentCount(th: CommentThread): number {
  return (th.msgs || []).filter((m) => m.who === "agent").length;
}
export function threadStuck(state: string): boolean {
  return state === "permission" || state === "picker";
}

// ── exact-text re-anchoring ────────────────────────────────────────────────────────────────────
// A highlight is stored as the selected text (`exact`); every re-render must re-find it inside the
// anchor turn's rendered text. Rendered whitespace is not byte-stable (markdown collapses runs,
// wraps lines), so both sides are matched through a whitespace-NORMALIZED view with an index map
// back into the raw string. First occurrence wins — same-turn duplicate phrases anchor to their
// first appearance, a known and acceptable simplification.

/** Collapse whitespace runs to single spaces; `map[i]` = raw index of normalized char i. */
function normalize(raw: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = [];
  let inWs = false;
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i];
    if (/\s/.test(c)) {
      inWs = true;
      continue;
    }
    if (inWs && norm.length) {
      norm += " ";
      map.push(i);              // the space stands for the run; anchor it at the run's end
    }
    inWs = false;
    norm += c;
    map.push(i);
  }
  return { norm, map };
}

/** Find `exact` inside `hay`, whitespace-tolerantly. Returns raw [start, end) in `hay`, or null. */
export function findExact(hay: string, exact: string): { start: number; end: number } | null {
  const target = normalize(exact).norm;
  if (!target) return null;
  const { norm, map } = normalize(hay);
  const at = norm.indexOf(target);
  if (at < 0) return null;
  return { start: map[at], end: map[at + target.length - 1] + 1 };
}

/** findExact with a LONGEST-PREFIX fallback (the user 2026-08-13, who wanted the comment visible
 *  in context every time): a selection that spanned several messages anchors to its FIRST turn,
 *  whose rendered text holds only the selection's head — the full exact-match fails and the thread
 *  fell back to the tiny badge alone. Binary-search the longest word-prefix that still matches, so
 *  the portion that lives in the anchored turn highlights. A too-short remnant (under 3 words and
 *  under 12 characters) stays null — highlighting a stray "The" would mark the wrong thing. */
export function findAnchorRange(hay: string, exact: string):
    { start: number; end: number; partial: boolean } | null {
  const full = findExact(hay, exact);
  if (full) return { ...full, partial: false };
  const words = exact.trim().split(/\s+/);
  let lo = 1, hi = words.length - 1, best: { start: number; end: number; k: number } | null = null;
  while (lo <= hi) {
    const k = (lo + hi) >> 1;
    const r = findExact(hay, words.slice(0, k).join(" "));
    if (r) { best = { ...r, k }; lo = k + 1; } else hi = k - 1;
  }
  if (!best) return null;
  const matched = words.slice(0, best.k).join(" ");
  if (best.k < 3 && matched.length < 12) return null;
  return { start: best.start, end: best.end, partial: true };
}

/** Split a global [start, end) character range over consecutive text-node lengths into per-node
 *  slices — what the DOM pass wraps in <mark> elements. */
export function sliceRanges(nodeLens: number[], start: number, end: number):
    { idx: number; s: number; e: number }[] {
  const out: { idx: number; s: number; e: number }[] = [];
  let off = 0;
  for (let i = 0; i < nodeLens.length && off < end; i++) {
    const len = nodeLens[i];
    const s = Math.max(start, off);
    const e = Math.min(end, off + len);
    if (e > s) out.push({ idx: i, s: s - off, e: e - off });
    off += len;
  }
  return out;
}

/** Optimistic pending sends, reconciled against the kernel's frame (the registerOptimistic pattern):
 *  each landed 'you' message spends AT MOST ONE pending row with its text — a count-based match, so
 *  sending the same words twice keeps the second bubble until its own message lands. Returns the
 *  still-pending remainder to render after the server messages. */
export function prunePending(pending: { text: string; t: number }[], msgs: CommentMsg[]):
    { text: string; t: number }[] {
  const counts = new Map<string, number>();
  for (const m of msgs) {
    if (m.who !== "you") continue;
    const k = normalize(m.text).norm;
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return pending.filter((p) => {
    const k = normalize(p.text).norm;
    const c = counts.get(k) || 0;
    if (c > 0) {
      counts.set(k, c - 1);
      return false;
    }
    return true;
  });
}
