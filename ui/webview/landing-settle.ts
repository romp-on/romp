// A deep-link landing SETTLES before it is called good (T386 stage 1, the user 2026-09-12: a card click deep into
// history did not land the first time; the landing audit had filed the first landing as exact). The landing write puts
// the target's top at the viewport top; what follows in the next second can move it (the transcript's own boxes
// sizing in, a rewindow write of the virtualiser, a rebuild's placement), and until now nothing measured where the
// target ended up. This module is the rule; render.ts feeds it the target's distance from the viewport top as the
// page's own events arrive (the target or the view resizing, another writer moving #content, two bounded timers) and
// acts on the step: re-land, or finish and file the landing row with the measured distance. Pure, so node executes it.

/** The settle window: the span landOn already re-aligned within for the boxes above the transcript. */
export const SETTLE_MS = 1200;
/** A reader's gesture needs its own evidence (round two, medium): an input event on the scroller (a pointer down or a drag,
 *  a touch, a wheel, a key) at most this many ms before the scroll event it caused. The browser's own scroll anchoring moves
 *  scrollTop with no write and no input (a node inserted or a spacer re-estimated above the viewport), and the classifier
 *  reads that as a gesture too; without evidence such a scroll is a SAMPLE for the settle, never a takeover. A scroll follows
 *  its input within a frame or two; the window only bounds staleness. */
export const SETTLE_INPUT_MS = 120;
/** The one early backstop sample beside the event samples: the first paint after the landing's own render, a measurement
 *  of where the target came to rest once the page laid out (round one, low 5). It settles nothing by itself: the window's end
 *  files the landing (round two, low 1). */
export const SETTLE_FIRST_PAINT_MS = 250;
/** The row a landing must sit within is the aligned element's own height, capped at this fraction of the viewport: a
 *  600 px miss on a 900 px message is a miss (round one, low 1). */
export const SETTLE_ROW_VIEWPORT_CAP = 0.25;

/** How far short of the viewport top the scroll clamp stops a target: with the target `targetY` px into the scroll space
 *  and the scroller able to scroll at most scrollHeight − clientHeight, the target's top can come no closer to the viewport
 *  top than this many px. 0 when the target can reach the top. A landing near the tail is judged against this spot, not the
 *  top (round one, medium 3: a correct landing within a viewport of the tail read as a 93 px miss and re-landed no-op writes). */
export function reachableOffset(targetY: number, scrollHeight: number, clientHeight: number): number {
  const maxScroll = Math.max(0, scrollHeight - clientHeight);
  return Math.max(0, Math.round(targetY - maxScroll));
}

/** Whether a scroll at `scrollAt` (ms) has a reader's input behind it: the pointer HELD on the scroller itself (a scrollbar
 *  thumb drag: one pointerdown on the scroller, then scrolls with no pointer moves at all until the release, round three), or
 *  an input at `lastInputAt` within SETTLE_INPUT_MS before it (a wheel, a key, a touch, a drag inside the content). 0 or null =
 *  no timed input seen. */
export function gestureEvidence(lastInputAt: number | null | undefined, scrollAt: number, held: boolean = false): boolean {
  if (held) return true;
  return !!lastInputAt && scrollAt - lastInputAt >= 0 && scrollAt - lastInputAt <= SETTLE_INPUT_MS;
}

/** Whether a pointerdown grabbed the SCROLLER: its target is the scroller element itself (the content's children take a press
 *  on the text; the scrollbar gutter belongs to the scroller), or its offset lies in the gutter beyond the client box. The
 *  hold this starts stands until the pointer's release or cancel, and every scroll meanwhile is the reader's (round three). */
export function scrollerGrab(targetIsScroller: boolean, offsetX: number, offsetY: number, clientWidth: number, clientHeight: number): boolean {
  return targetIsScroller || offsetX >= clientWidth || offsetY >= clientHeight;
}

/** THE CENSUS of #content's writers (round five): every writer name writeScroll is given, classified. "reader": the write is
 *  the reader's own input arriving as a write (a key or a chord, a click on a fragment link or the jump chip or the feed's
 *  go-to-the-live-tail chip, the wheel over a scroll notch), which the scroll classifier reads as a write echo and never as a
 *  gesture; a settling landing yields to it. "page": the page's own mover (the landing's writes, a restore, an append, the tail's
 *  shrink, a rewindow, a fold, a box, the bar, an ask's reveal, a send, a cancel), which the settle samples and re-lands over.
 *  A writer must be listed here to write at all: landing-settle.test.ts reads every writer literal out of render.ts and holds
 *  it to this table, and the table to render.ts, so a new writer cannot land unclassified (round four named three reader
 *  writers and missed three: the history chords, a fragment link, the live-tail chip, each written back by land-realign). */
export const WRITER_CLASS: Readonly<Record<string, "reader" | "page">> = {
  "key-nav": "reader", "wheel-scale": "reader", "jump-button": "reader", "nav-history": "reader", "section-link": "reader", "focus-live": "reader",
  "land-on": "page", "land-realign": "page", "land-bottom": "page", "land-saved": "page", "keep-offset": "page", "anchor-restore": "page",
  "reload-restore": "page", "append-stick": "page", "append-raw": "page", "tail-shrink": "page", "rewindow": "page", "box-resize": "page",
  "box-below": "page", "tabbar-drag": "page", "toolgroup-toggle": "page", "liveask-reveal": "page", "optimistic-send": "page", "queued-x": "page",
};
/** The reader's own writers, derived from the census. */
export const READER_WRITERS: ReadonlySet<string> = new Set(Object.keys(WRITER_CLASS).filter((w) => WRITER_CLASS[w] === "reader"));
export function writerIsReader(writer: string): boolean {
  return WRITER_CLASS[writer] === "reader";
}

export interface SettleSample { at: number; dist: number }   // at: ms since the landing write; dist: the target's top vs the viewport top, px

export type SettleStep = "wait" | "realign" | "settled" | "unsettled" | "gave-up";

/** Within its own row: the target's top sits inside [−rowH, rowH] of the viewport top (a row's height, never less than a
 *  few px, so a sub-pixel or border rounding never reads as a miss). */
export function withinRow(dist: number, rowH: number): boolean {
  return Math.abs(dist) <= Math.max(8, rowH);
}

/** The rule, over the samples taken so far (oldest first), the target's row height, whether the reader has taken over
 *  (a wheel or key of their own), and the clock:
 *  - the reader's gesture ends the settle: the landing gave up its place to them ("gave-up"; the row records the last
 *    distance as it stood, settled false when it was off);
 *  - a sample outside the row before the window's end asks for a re-land ("realign"): the page moved the target, the
 *    landing puts it back;
 *  - the window's end settles on the last sample ("settled" within the row, else "unsettled"), so a landing that never
 *    quietens is still filed, with its distance, rather than held forever. Nothing settles EARLY (round two, low 1): two
 *    adjacent quiet samples used to end the settle about 60 ms in, and a displacement later in the window (the tab bar
 *    wrapping to a second row) was neither sampled nor corrected; quiet means the window ran out with the target on its row;
 *  - otherwise "wait". */
export function settleStep(samples: readonly SettleSample[], rowH: number, gesture: boolean, now: number): SettleStep {
  if (gesture) return "gave-up";
  const last = samples.length ? samples[samples.length - 1] : null;
  if (last && !withinRow(last.dist, rowH) && now < SETTLE_MS) return "realign";
  if (now >= SETTLE_MS) return last && withinRow(last.dist, rowH) ? "settled" : "unsettled";
  return "wait";
}

/** The fields the landing row gains when the settle ends: the last measured distance (px, the target's top vs the
 *  viewport top; null when the target was never measured) and whether the landing settled. A landing the reader
 *  took over ("gave-up") is settled when its last distance was within the row: the reader's own move is not a miss; the row
 *  says so with `gesture` (round three, low 3). */
export function settleRowFields(step: SettleStep, samples: readonly SettleSample[], rowH: number): { dist: number | null; settled: boolean; gesture?: true } {
  const last = samples.length ? samples[samples.length - 1] : null;
  // the takeover MARK (round three, low 3): the audit could not tell a landing the reader abandoned from one that held, since a
  // gave-up landing within the row files settled true like a held one; `gesture` says the reader took it over, the distance
  // stays the target's last measured one
  const mark = step === "gave-up" ? { gesture: true as const } : {};
  if (!last) return { dist: null, settled: false, ...mark };
  return { dist: Math.round(last.dist), settled: step === "settled" || (step === "gave-up" && withinRow(last.dist, rowH)), ...mark };
}
