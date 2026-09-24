// The chat's JUMP CLUSTER (the user 2026-09-24, paraphrased: after new content arrives they lose their place and want to
// get back to their last message and read everything since, especially on the phone, and they want comments reachable
// without hunting on the rail). A small persistent cluster in the chat pane's bottom-left corner steps through the
// user's OWN messages (previous / next) and the COMMENT threads (previous / next), and carries the unread-reply count as
// a badge that jumps to the next unread thread; it replaced the two "↑ N replies unread" / "↓ N reply unread" chips.
// This module is the PURE half (node-tested, no DOM): where a stop sits against the reader's position, which stop a
// move picks, and which unread thread the badge goes to next, plus the copy. render.ts (updateJumpCluster / runJump)
// measures the page and lands through scrollToAnchor, the reply chips' and the rail's one landing road.
import { WINDOWED_OUT, placeWindowed, replyWord, type Dir, type ReadyChip } from "./reply-ready";

export type Move = "prevMine" | "nextMine" | "prevComment" | "nextComment" | "nextUnread";
export const MOVES: readonly Move[] = ["prevMine", "nextMine", "prevComment", "nextComment", "nextUnread"];

/** Where a stop sits against the reader's position: above it (a previous move reaches it), below it (a next move), or
 *  HERE (neither: it is what the reader is on). `dist` orders the stops of one direction, least = nearest. */
export type Place = { dir: Dir | "here"; dist: number };

/** How close to the reading line a rendered stop's top must sit to count as HERE, in px: a landing puts its target's top
 *  on the line (landOn aligns it to the viewport top), and a settle re-align or a rounding pixel must not make the stop
 *  the reader just landed on their "next" again. */
export const HERE_PX = 6;

/** A RENDERED stop by its top against the reading line (both in px from the scroller's top edge). The line is the
 *  viewport's top while the reader is scrolled up: a stop above it was passed, one below it is ahead, one on it is here.
 *  At the bottom of the transcript the line is the viewport's BOTTOM with no here band (`here` 0), so every stop on
 *  screen counts as above: from the bottom, one "previous" lands on the user's last message even when it is in view
 *  (the stated case), since at the bottom nothing is below. */
export function placeRendered(top: number, line: number, here = HERE_PX): Place {
  const d = top - line;
  if (d < -here) return { dir: "above", dist: -d };
  if (d > here) return { dir: "below", dist: d };
  return { dir: "here", dist: 0 };
}

/** A stop with no rendered turn (the chat renders a window of units between two spacers): by its display unit against the
 *  rendered window, the reply chips' placement (reply-ready.ts placeWindowed, in a tier past every pixel distance). A
 *  unit INSIDE the window with no turn of its own sits in a folded run on screen: here. */
export function placeUnplaced(unit: number, winStart: number, winEnd: number): Place {
  const p = placeWindowed(unit, winStart, winEnd);
  return p.dir === "in" ? { dir: "here", dist: 0 } : { dir: p.dir, dist: p.dist };
}

/** A span of UNLOADED history (a gap the page does not hold) by its edges against the reading line: a previous move
 *  meets its bottom edge, a next move its top. A gap spanning the line is being filled already (its observer asked),
 *  so it stands in neither direction. */
export function placeSpan(top: number, bottom: number, line: number, here = HERE_PX): Place {
  if (bottom < line - here) return { dir: "above", dist: line - bottom };
  if (top > line + here) return { dir: "below", dist: top - line };
  return { dir: "here", dist: 0 };
}

/** Every stop by ORDER against a REMEMBERED position: the stop the last jump landed on, as (display unit, rank within
 *  the unit). Used while that landing still stands (the reader has not scrolled since, and no message of theirs was
 *  added): a landing the scroll could not top-align (near the end, the view clamped at the bottom) leaves its target
 *  below the line, and read by pixels the reader's next "previous" would land on it again. Rank breaks ties between
 *  stops on one unit (two threads on one passage), so both are reachable. Unit -1 = not in the loaded history (a comment
 *  anchored in history the page has not loaded): above everything, farthest. */
export const RANK_SPAN = 1000;
export function placeByOrder(unit: number, rank: number, cur: { unit: number; rank: number }): Place {
  const a = unit * RANK_SPAN + rank, b = cur.unit * RANK_SPAN + cur.rank;
  if (a < b) return { dir: "above", dist: b - a };
  if (a > b) return { dir: "below", dist: a - b };
  return { dir: "here", dist: 0 };
}

/** The farthest-above tier: history older than everything the page holds (the older-history loader's side). */
export const HEAD_DIST = WINDOWED_OUT * 2;

/** A stop a move can land on (`uuid`), or a BARRIER: unloaded history between the reader and anything a move could name
 *  (`load`), which the move asks for through the page's own older-history loaders and resumes on when it lands. */
export interface NavStop<L = unknown> { uuid: string; place: Place; rank?: number; tid?: string; load?: L }

/** The move's target: the nearest stop in `dir` (least dist; a tie keeps the earlier in input order), or null when
 *  nothing lies that way. A barrier nearer than every stop wins: the stops beyond it are not the next ones, since the
 *  unloaded span between may hold nearer ones. */
export function pickNav<L>(stops: readonly NavStop<L>[], dir: Dir): NavStop<L> | null {
  let best: NavStop<L> | null = null;
  for (const s of stops) if (s.place.dir === dir && (!best || s.place.dist < best.place.dist)) best = s;
  return best;
}

/** The badge's next unread thread: in the direction the last badge press went while any unread reply waits that way,
 *  else the other way; with no last press, below first (reading on from where the reader is), then above. Continuing
 *  the direction is what walks every unread thread: "nearest" or "below first" on every press can bounce between two
 *  (a press lands one, the one passed is now on the other side and nearer). Null when none is off screen (a thread in
 *  view counts in neither direction, the reply chips' rule, and its ring is already showing). */
export function unreadNext(chips: { above: ReadyChip | null; below: ReadyChip | null }, last: Dir | null): { dir: Dir; chip: ReadyChip } | null {
  const order: Dir[] = last === "above" ? ["above", "below"] : last === "below" ? ["below", "above"] : ["below", "above"];
  for (const d of order) { const c = chips[d]; if (c) return { dir: d, chip: c }; }
  return null;
}

/** How many unread replies wait off screen, both directions: the badge's number. */
export function unreadCount(chips: { above: ReadyChip | null; below: ReadyChip | null }): number {
  return (chips.above ? chips.above.count : 0) + (chips.below ? chips.below.count : 0);
}

// ── copy: the user's words, one line per control ──────────────────────────────────────────────────────
/** Each control's tooltip base (render.ts appends the move's current key through titleWithKey). */
export const MOVE_TITLE: Record<Move, string> = {
  prevMine: "Your previous message",
  nextMine: "Your next message",
  prevComment: "Previous comment",
  nextComment: "Next comment",
  nextUnread: "Next unread reply",
};
/** The badge's tip: how many, and where the next press goes. */
export function badgeTip(n: number, dir: Dir, line: string): string {
  return replyWord(n) + " · next one " + dir + (line ? ": " + line : "");
}
export function badgeAria(n: number, dir: Dir): string {
  return replyWord(n) + ", go to the next one " + dir;
}
