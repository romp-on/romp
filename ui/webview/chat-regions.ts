// The chat's HISTORY REGIONS (T386 stage 2, plans/chat-history-regions.md Part B): a session's transcript is an ordered list of
// regions over its turns, RUNS the page holds (rendered, virtualised as before) and GAPS it does not (empty space of an estimated
// height with the loading glyph while a request is in flight). The TAIL run is always resident and always live, so no client is
// ever detached, and the kernel never withholds a delta. Pure: node executes every rule here (chat-regions.test.ts); render.ts
// wires them to the DOM.
//
// Spans are TURN indices, [lo, hi), as the kernel numbers them (its pages are PAGE_TURNS-aligned in that numbering). The tail run's
// `hi` is null: it ends wherever the transcript ends now, and grows with every chatTail.
import { keyOf, mergeWindow, type Ev } from "./chat-window";

export type { Ev } from "./chat-window";
export interface Run { kind: "run"; lo: number; hi: number | null; events: Ev[]; }
export interface Gap { kind: "gap"; lo: number; hi: number; }
export type Region = Run | Gap;

export const DEFAULT_TURN_PX = 120;   // the per-TURN estimate the spacers use until a run is measured (a turn is ~two rows; render.ts sizeSpacers measures px-per-turn)

/** The regions a set of runs implies: the runs in turn order with a gap between each pair that does not touch, and a head gap
 *  [0, first.lo) when the first run does not start at the head. Runs must not overlap (insertRun keeps that). */
export function regionsFromRuns(runs: readonly Run[]): Region[] {
  const sorted = runs.slice().sort((a, b) => a.lo - b.lo);
  const out: Region[] = [];
  let at = 0;
  for (const r of sorted) {
    if (r.lo > at) out.push({ kind: "gap", lo: at, hi: r.lo });
    out.push(r);
    if (r.hi == null) return out;   // the tail run ends the list
    at = r.hi;
  }
  return out;
}

export function runsOf(regions: readonly Region[]): Run[] {
  return regions.filter((r): r is Run => r.kind === "run");
}

/** A window's events with the kernel's turn span become a run; the gaps on either side shrink or split; a run that touches or
 *  overlaps a held run merges into it, the T323 order rule carrying over (the held run's part before the window, the window,
 *  the held run's part after, every key once). A window that touches the TAIL run merges into it and the merged run is the
 *  tail (its `hi` stays null). */
export function insertRun(regions: readonly Region[], run: Run): Region[] {
  const runs = runsOf(regions);
  let cur: Run = { kind: "run", lo: run.lo, hi: run.hi, events: run.events.slice() };
  const kept: Run[] = [];
  for (const r of runs) {
    if (touches(r, cur)) cur = mergeRuns(r, cur);
    else kept.push(r);
  }
  kept.push(cur);
  return regionsFromRuns(kept);
}

/** The kernel's live overlay cards: a to-do box, a compacting or clearing notice, a reconnecting or retrying notice, the queued
 *  group, an api-error card. They come and go between builds and ride every frame's suffix, so the kernel anchors a client's base on
 *  the last TRANSCRIPT event, never on one of these (its _last_anchor), and the frame-recency reading below skips them the same way.
 *  ONE constant serves the page: send-pending.ts OVERLAY_KINDS (kernel.py _OVERLAY_KINDS, pinned equal by
 *  tests/test_send_pending_overlay_kinds.py), re-exported here for the reading and its tests. A second copy of the set lived here
 *  (2026-09-19), so a kernel-constant change broke two pins for one change. */
export { OVERLAY_KINDS } from "./send-pending";

export interface HeldSplit {
  before: Ev[];      // the held events positioned BEFORE the frame's first shared key: history the frame did not carry, kept above it
  dropped: Ev[];     // the held events at or after that key whose key the frame lacks: covered by the frame's span and missing from it
  behind: boolean;   // the frame's last transcript key is resident and a transcript row the client holds sits after it: the frame is older than what the page shows
  afterLast: number; // how many dropped transcript rows sit after the frame's last transcript key (the behind reading's evidence)
}

/** What a proto-2 FULL frame's open-ended tail run does to a held run it overlaps (2026-09-19). The frame is authoritative for
 *  [tailLo, end): every event from its first turn to the transcript's end. A held event is placed by POSITION, never by key
 *  absence alone: one positioned before the frame's first shared key is history the frame did not carry (`before`, kept above
 *  the frame as a run ending at tailLo); one at or after it and absent from the frame is covered by the frame's span and
 *  missing from it (`dropped`): retracted (a rewind, a canceled queued message, an echo replaced by its record, a retired live
 *  atom, a bubble the client injected) or the frame is BEHIND (a full built from an older list). Filing the absent ones above
 *  the frame by key absence put the newest row above older ones (t3,t1,t2), the bottom of the view then showed older content
 *  and the next delta duplicated the row. `behind` reads the frame's last TRANSCRIPT key (the kernel's own anchor rule,
 *  OVERLAY_KINDS skipped: a frame ending in a to-do card whose key is not resident must not hide a behind frame): resident, and
 *  a held transcript row (`transcript`: not an overlay card, not a client-injected group) dropped after it. A frame carrying
 *  events PAST the last shared key (the echo landing: the record and the reply after the held echo) is therefore not behind;
 *  a same-list stale full is. No shared key at all: the whole held run is `before` (the floor-cut shape). */
export function splitHeldAgainstFrame(held: readonly Ev[], frame: readonly Ev[], transcript: (e: Ev) => boolean): HeldSplit {
  const frameKeys = new Set<string>();
  for (const e of frame) { const k = keyOf(e); if (k) frameKeys.add(k); }
  let at = -1;
  for (let i = 0; i < held.length; i++) { const k = keyOf(held[i]); if (k && frameKeys.has(k)) { at = i; break; } }
  if (at < 0) return { before: held.slice(), dropped: [], behind: false, afterLast: 0 };
  const before = held.slice(0, at);
  const dropped: Ev[] = [];
  for (let i = at; i < held.length; i++) { const k = keyOf(held[i]); if (!k || !frameKeys.has(k)) dropped.push(held[i]); }
  let frameLast: string | undefined;
  for (let i = frame.length - 1; i >= 0; i--) { if (transcript(frame[i])) { frameLast = keyOf(frame[i]); break; } }
  if (frameLast === undefined && frame.length) frameLast = keyOf(frame[frame.length - 1]);
  let lastAt = -1;
  if (frameLast) for (let i = 0; i < held.length; i++) if (keyOf(held[i]) === frameLast) { lastAt = i; break; }
  let afterLast = 0;
  if (lastAt >= 0) for (let i = lastAt + 1; i < held.length; i++) { const k = keyOf(held[i]); if ((!k || !frameKeys.has(k)) && transcript(held[i])) afterLast++; }
  return { before, dropped, behind: afterLast > 0, afterLast };
}

function touches(a: Run, b: Run): boolean {
  const aHi = a.hi ?? Infinity, bHi = b.hi ?? Infinity;
  return b.lo <= aHi && a.lo <= bHi;
}

/** Two touching or overlapping runs as one, events in turn order. `held` is the run the page had, `win` the arrival. */
function mergeRuns(held: Run, win: Run): Run {
  const lo = Math.min(held.lo, win.lo);
  const hi = held.hi == null || win.hi == null ? null : Math.max(held.hi, win.hi);
  let events: Ev[];
  const keysOverlap = win.events.some((e) => { const k = keyOf(e); return !!k && held.events.some((h) => keyOf(h) === k); });
  if (keysOverlap) events = mergeWindow(held.events, win.events).events;
  else if (win.lo < held.lo || (win.lo === held.lo && (win.hi ?? Infinity) < (held.hi ?? Infinity))) events = win.events.concat(held.events);
  else events = held.events.concat(win.events);
  return { kind: "run", lo, hi, events };
}

/** A gap's height in the thread: its TURN count × the measured px-per-TURN (not px-per-display-unit: a turn is a user row plus its
 *  reply and any tool rows, so multiplying a turn count by a per-unit average drew gaps roughly half their true height), at least a turn. */
export function gapHeight(gap: { lo: number; hi: number }, perTurnPx: number | null | undefined): number {
  const per = perTurnPx ?? DEFAULT_TURN_PX;
  return Math.max(Math.round(per), Math.round((gap.hi - gap.lo) * per));
}

/** The page-aligned span to ask for when a gap enters the viewport: the page nearest the viewport's edge first. Scrolling UP
 *  through a gap meets its bottom edge, so its bottom page; scrolling DOWN into a gap meets its top edge, so its top page; a
 *  landing asks for the anchor's pages instead (loadAround). Pages are PAGE_TURNS-aligned in the kernel's numbering, clipped to
 *  the gap. */
export function pagesToAsk(gap: { lo: number; hi: number }, edge: "top" | "bottom", pageTurns: number): { lo: number; hi: number } {
  const P = Math.max(1, pageTurns | 0);
  if (edge === "bottom") {
    const lo = Math.max(gap.lo, Math.floor((gap.hi - 1) / P) * P);
    return { lo, hi: gap.hi };
  }
  const hi = Math.min(gap.hi, (Math.floor(gap.lo / P) + 1) * P);
  return { lo: gap.lo, hi };
}

/** Where inside a gap an anchor with a time falls, as a fraction of the gap's height: the time's proportion between the gap's
 *  neighbours' times (the run above's last, the run below's first). Without both neighbours the gap's start (0) or, with only
 *  the run above, its end (1) is the honest guess; the caller does not move at all when it has no time. */
export function gapFraction(t: number, tBefore: number | null | undefined, tAfter: number | null | undefined): number {
  if (tBefore != null && tAfter != null && tAfter > tBefore) return Math.max(0, Math.min(1, (t - tBefore) / (tAfter - tBefore)));
  if (tBefore != null && tAfter == null) return 1;
  return 0;
}

/** The ONE landing notice's sentence (the user 2026-09-12): the time in the reader's clock as the strip renders it, or the plain
 *  form when the anchor carries no time. */
export function landingNotice(t: number | null | undefined, clock: (epochS: number) => string): string {
  return t != null ? "Going to the message from " + clock(t) + ", click to stay here" : "Going to the earlier message, click to stay here";
}

/** The set-aside notice (2026-09-19): a `rebased` full frame said the held tail run's turns are gone from the current
 *  session (a fork or a rewind), so the page set them aside. Never silent: this names how many and why. */
export function setAsideNotice(n: number): string {
  return n + (n === 1 ? " earlier message was" : " earlier messages were") + " set aside; this session was continued";
}

export interface LandingState { target: string | null; notice: boolean; askInFlight: boolean; }
/** What the notice's click (the only cancel) leaves: no target and no notice, the ask still in flight (its reply inserts the run;
 *  nothing is thrown away) and the view where it is. */
export function landingCancel(state: LandingState): LandingState {
  return { target: null, notice: false, askInFlight: state.askInFlight };
}

/** The HISTORY run (never the tail run) holding the event keyed `key`, or null (2026-09-30): what a chatTail's `changedBelow`
 *  names — a change the kernel saw above the client's tail run. A history run that holds it shows a stale copy and is re-asked
 *  by its span (render.ts chatTail); the tail run is the delta's own business, and a key in a gap is nothing the page holds. */
export function runHoldingKey(regions: readonly Region[] | null | undefined, key: string | null | undefined): Run | null {
  if (!regions || !key) return null;
  for (const r of regions) {
    if (r.kind !== "run" || r.hi == null) continue;
    for (const e of r.events) if (keyOf(e) === key) return r;
  }
  return null;
}

export interface SpanAsk { sid: string; lo: number; hi: number; }
/** The deferred re-asks of `sid` that may go now (2026-09-30): a changedBelow that arrived while the run's span already had a
 *  page ask on the wire was not doubled but REMEMBERED, since that ask's reply may carry the older copy (its build read the
 *  list before the change); once no ask is in flight for the span — the reply landed — it is asked once more, one coalesced
 *  follow-up however many changes arrived meanwhile. `inFlight` is the page's own in-flight test (render.ts gapHasAsk). */
export function redoSpans(deferred: Iterable<SpanAsk>, sid: string, inFlight: (lo: number, hi: number) => boolean): SpanAsk[] {
  const out: SpanAsk[] = [];
  for (const d of deferred) if (d.sid === sid && !inFlight(d.lo, d.hi)) out.push(d);
  return out;
}

/** The gap a turn span falls in, if any. */
export function gapAt(regions: readonly Region[], turn: number): Gap | null {
  for (const r of regions) if (r.kind === "gap" && turn >= r.lo && turn < r.hi) return r;
  return null;
}

/** The turn count a region list covers before the tail (the tail run's lo), for the scrollbar's estimate. */
export function turnsBeforeTail(regions: readonly Region[]): number {
  const tail = regions.find((r): r is Run => r.kind === "run" && r.hi == null);
  return tail ? tail.lo : regions.reduce((n, r) => Math.max(n, r.hi ?? 0), 0);
}
