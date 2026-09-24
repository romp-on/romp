// Replies ready (the user 2026-09-08): a reply lands on a comment you scrolled away from, and you forget
// to come back — the mark's ring and the rail tick say so only where you happen to be looking. The unread
// landed replies whose marks sit wholly ABOVE / BELOW the viewport are counted per direction with the NEAREST
// of each; two chips beside the go-to-bottom chip showed them until 2026-09-24, when they folded into the jump
// cluster's badge (jump-nav.ts; render.ts readyAround / updateJumpCluster). This module is the PURE half
// (node-testable, no DOM): the one predicate for "a landed reply waits here", the placement of a mark against
// the viewport, the per-direction count + nearest pick, and the phrase.
import type { CommentThread } from "./comments";

export type Dir = "above" | "below";

/** THE predicate: the kernel's `unread` bit ("a FINISHED reply you have not seen", T237) on an OPEN thread —
 *  exactly what the mark's `.unread` class and the rail tick's `.unread` read (styleCommentMark /
 *  updateCommentRail keep the verbatim form; this is the same expression, named). A resolved or merged
 *  thread with a stale bit is not a reply waiting for anyone. */
export function isReplyReady(th: Pick<CommentThread, "unread" | "status">): boolean {
  return !!th.unread && th.status === "open";
}

/** Where a rendered mark sits against the viewport, from its box in VIEWPORT-relative pixels (0 = the
 *  scroller's top edge, viewH = its bottom edge). Intersecting the viewport by any amount = "in" (counts
 *  in neither direction — the reader can see it); wholly above the top edge = "above"; wholly below the
 *  bottom edge = "below". `dist` is the gap to the nearer edge, so the nearest mark per direction is the
 *  one with the least — pixels, so a mark 40px up beats one two screens up. */
export function placeMark(top: number, bottom: number, viewH: number): { dir: Dir | "in"; dist: number } {
  if (bottom <= 0) return { dir: "above", dist: Math.abs(bottom) };   // abs, not negation: -0 is not 0 to a deepEqual
  if (top >= viewH) return { dir: "below", dist: top - viewH };
  return { dir: "in", dist: 0 };
}

/** A mark whose turn is WINDOWED OUT (the chat virtualizes: a rendered window between two spacers) has
 *  no box, but its direction is exact from event order: the window always extends past the viewport, so
 *  a unit before the window's first rendered unit is above, one at/after its end is below. Its distance
 *  lives in a tier ABOVE every pixel distance (WINDOWED_OUT + units from the window edge), so a rendered
 *  mark is always nearer than a windowed-out one and windowed-out marks still order among themselves.
 *  `unit < 0` = not in the resident events at all (older than the streamed-in head): above, farthest. A
 *  unit INSIDE the window with no turn of its own (a member of a folded tool run) reads "in" — it is on
 *  screen in folded form, and the rail tick still reaches it. */
export const WINDOWED_OUT = 1e9;
export function placeWindowed(unit: number, winStart: number, winEnd: number): { dir: Dir | "in"; dist: number } {
  if (unit < 0) return { dir: "above", dist: WINDOWED_OUT * 2 };
  if (unit < winStart) return { dir: "above", dist: WINDOWED_OUT + (winStart - unit) };
  if (unit >= winEnd) return { dir: "below", dist: WINDOWED_OUT + (unit - winEnd + 1) };
  return { dir: "in", dist: 0 };
}

export type ReadyMark = { tid: string; uuid: string; line: string; dir: Dir | "in"; dist: number };
export type ReadyChip = { count: number; nearest: ReadyMark };

/** Per direction: how many wait there, and the nearest of them (least dist; a tie keeps the earlier). */
export function readyChips(marks: ReadyMark[]): { above: ReadyChip | null; below: ReadyChip | null } {
  let above: ReadyChip | null = null, below: ReadyChip | null = null;
  for (const m of marks) {
    if (m.dir === "in") continue;
    const cur = m.dir === "above" ? above : below;
    const next: ReadyChip = !cur ? { count: 1, nearest: m }
      : { count: cur.count + 1, nearest: m.dist < cur.nearest.dist ? m : cur.nearest };
    if (m.dir === "above") above = next; else below = next;
  }
  return { above, below };
}

/** The first non-empty line of a text, clipped to `max` characters with an ellipsis — the tooltip's
 *  "nearest:" tail. */
export function firstLine(text: string, max = 60): string {
  const line = (text || "").split("\n").map((l) => l.trim()).find((l) => l.length > 0) || "";
  return line.length > max ? line.slice(0, max - 1).trimEnd() + "…" : line;
}

/** What names the nearest thread in the tip: the last thing YOU said in it (the message the landed
 *  reply answers), else the thread's name, else the highlighted passage — never empty for a real thread. */
export function replyLine(th: Pick<CommentThread, "msgs" | "name" | "exact">): string {
  const mine = (th.msgs || []).filter((m) => m.who === "you");
  const said = mine.length ? mine[mine.length - 1].text : "";
  return firstLine(said || th.name || th.exact || "");
}

// ── copy: the user's own words ────────────────────────────────────────────────────────────────────
// "reply"/"replies", not "comment": the unread thing is the ANSWER to their comment (the coordinator's
// refinement, 2026-09-08). The jump cluster's badge tip reads this phrase (jump-nav.ts badgeTip).
export function replyWord(n: number): string {
  return n === 1 ? "1 reply unread" : n + " replies unread";
}
