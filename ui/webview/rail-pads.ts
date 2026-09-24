// The comment ticks' hit pads on the chat's scroll rail (the user 2026-09-24: comments are what they click there, and
// a 4px-tall tick was hard to hit). Each tick's hit box reaches up to `pad` px above and below its paint (the CSS
// ::before reads them as --hit-t / --hit-b), clamped so it never reaches a neighbour's paint: the free gap to the
// nearest tick at ANOTHER height is split in half, an odd pixel left unclaimed. Ticks sharing a top (two threads on
// one passage) pad against the ticks around them, not each other: they overlap on purpose, the unread one stacked
// above. The rail's own ends clamp too, so the last tick's pad never reaches past the pane into the composer. The
// scroll notches clamp the same way inline in paintScrollMarks; this is the pure, node-tested half for the ticks.

/** The cap on a tick's pad, in px: a 14px target for a 4px tick when nothing is near. */
export const TICK_PAD = 5;

/** For each tick (top `y`, painted height `h`, in rail px, any order), its [above, below] pad. */
export function railPads(items: ReadonlyArray<{ y: number; h: number }>, pad: number, height: number): Array<[number, number]> {
  return items.map((it) => {
    let prevBottom = -Infinity, nextTop = Infinity;
    for (const o of items) {
      if (o.y < it.y) prevBottom = Math.max(prevBottom, o.y + o.h);
      else if (o.y > it.y) nextTop = Math.min(nextTop, o.y);
    }
    const up = prevBottom === -Infinity ? it.y : Math.floor((it.y - prevBottom) / 2);
    const bottom = it.y + it.h;
    const down = nextTop === Infinity ? height - bottom : Math.floor((nextTop - bottom) / 2);
    return [Math.max(0, Math.min(pad, up)), Math.max(0, Math.min(pad, down))];
  });
}
