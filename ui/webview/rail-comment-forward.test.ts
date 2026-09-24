// A comment-forward scroll rail (the user 2026-09-24, who rarely jumps by their own turns on the rail but often
// clicks comments, which were hard to pick out and to hit on a long transcript). The rail keeps its two mark kinds
// in one column; the change is geometry only:
//   · a turn notch is SHORTER: 4px out from the edge (was 8), the same 2px line in the same blue;
//   · a comment tick is LONGER: 14px out from the edge (was 8), unread 16 (was 10), and its HIT BOX is a padded
//     ::before — 4px further in, to the pane edge, and up to 5px above and below — each vertical pad clamped to half
//     the free gap to the nearest tick at another height, so no pad reaches a neighbour's paint.
// Colours (the ink, the unread halo, writing green, resolved faded) and the click (jump + open the thread) are
// untouched; scroll-marks.test.ts and comments.test.ts keep pinning those. The pad helper is driven by execution;
// the CSS and the render.ts wiring are source pins (the repo convention). Synthetic numbers only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { railPads, TICK_PAD } from "./rail-pads";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const RAIL = RENDER.slice(RENDER.indexOf("function updateCommentRail(): void {"), RENDER.indexOf("function unwrapCommentMark("));
const rule = (sel: string): string => {
  const at = CSS.indexOf("\n" + sel + " {");
  assert.ok(at >= 0, "rule " + sel + " exists");
  return CSS.slice(at + 1, CSS.indexOf("}", at));
};
const px = (r: string, prop: string): number => {
  const m = r.match(new RegExp("(?:^|[\\s;{])" + prop + ": (-?\\d+)(?:px)?[;\\s]"));   // a bare 0 carries no unit
  assert.ok(m, prop + " in " + r);
  return Number(m![1]);
};

test("a turn notch is shorter: 4px out from the edge, still a 2px line in the user's blue", () => {
  const notch = rule(".scroll-marks .scroll-mark");
  assert.equal(px(notch, "width"), 4, "shorter than it was (8px): the user rarely navigates by their own turns");
  assert.equal(px(notch, "height"), 2);
  assert.equal(px(notch, "right"), 1);
  assert.match(notch, /background: var\(--you\); opacity: 0\.65;/, "the colour is unchanged");
});

test("a comment tick is longer: 14px out from the edge (unread 16), same height, same ink and halo", () => {
  const tick = rule(".cmt-tick");
  assert.equal(px(tick, "width"), 14);
  assert.equal(px(tick, "height"), 4);
  assert.equal(px(tick, "right"), 1);
  assert.match(tick, /background: var\(--cmt-hl-outline\); opacity: 0\.75; pointer-events: auto; cursor: pointer;/, "the ink and the pointer contract unchanged");
  const unread = rule(".cmt-tick.unread");
  assert.equal(px(unread, "width"), 16, "unread fills the rail, as it did");
  assert.equal(px(unread, "height"), 6);
  assert.equal(px(unread, "right"), 0);
  assert.match(unread, /box-shadow: 0 0 0 1\.5px var\(--bg\), 0 0 0 3px var\(--st-awaiting-bg\);/, "the needs-you halo unchanged");
  assert.match(CSS, /\.cmt-tick\.resolved \{ opacity: 0\.3; \}/, "resolved still fades");
  assert.match(CSS, /\.cmt-tick\.busy \{ background: var\(--st-awaitbg-bg\); \}/, "writing is still green");
  assert.match(CSS, /\.cmt-rail \{ position: fixed; width: 16px; z-index: 40; pointer-events: none; \}/, "the rail box holds the unread width");
  assert.match(RAIL, /rail\.style\.left = \(r\.right - 16\) \+ "px";/, "…and its right edge is still the pane's right edge");
});

test("the comment tick's HIT box is a padded ::before, clamped per tick by the painter", () => {
  const hit = rule(".cmt-tick::before");
  assert.match(hit, /content: ""; position: absolute;/);
  assert.equal(px(hit, "left"), -4, "4px further in than the paint");
  assert.equal(px(hit, "right"), -1, "out to the pane edge");
  assert.match(hit, /top: calc\(-1 \* var\(--hit-t, 5px\)\); bottom: calc\(-1 \* var\(--hit-b, 5px\)\);/, "per-tick pads, 5px when nothing is near");
  assert.equal(TICK_PAD, 5, "the painter's cap matches the CSS fallback");
  // the painter sets the pads on BOTH DOM paths (the in-place move and the rebuild), from the one helper
  assert.match(RAIL, /const pads = railPads\(ticks\.map\(\(t\) => \(\{ y: t\.y, h: t\.th\.unread && t\.th\.status === "open" \? 6 : 4 \}\)\), TICK_PAD, r\.height\);/,
    "the painted height per tick: unread 6, else 4 — the same predicate the class reads");
  assert.match(RAIL, /const dress = \(tick: HTMLElement, t: typeof ticks\[number\], i: number\) => \{/);
  assert.match(RAIL, /tick\.style\.setProperty\("--hit-t", pads\[i\]\[0\] \+ "px"\);\s*\n\s*tick\.style\.setProperty\("--hit-b", pads\[i\]\[1\] \+ "px"\);/);
  assert.match(RAIL, /ticks\.forEach\(\(t, i\) => dress\(kids\[i\], t, i\)\);/, "the in-place path dresses");
  assert.match(RAIL, /dress\(tick, t, i\);/, "…and so does the rebuild");
});

test("a comment tick's hit box is larger than a turn notch's, both ways", () => {
  // the notch: paint width + its ::before's left/right pads; height: 2px paint + up to 2px above and below
  const notchW = px(rule(".scroll-marks .scroll-mark"), "width");
  const nHit = CSS.match(/\.scroll-marks \.scroll-mark\[data-act\]::before \{ content: ""; position: absolute; left: -(\d+)px; right: -(\d+)px; top: calc\(-1 \* var\(--hit-t, (\d+)px\)\)/);
  assert.ok(nHit, "the notch hit pseudo");
  const notchHitW = notchW + Number(nHit![1]) + Number(nHit![2]);
  const notchHitH = 2 + 2 * Number(nHit![3]);
  const tick = rule(".cmt-tick");
  const hit = rule(".cmt-tick::before");
  const tickHitW = px(tick, "width") - px(hit, "left") - px(hit, "right");
  const tickHitH = px(tick, "height") + 2 * TICK_PAD;
  assert.equal(notchHitW, 8);
  assert.equal(tickHitW, 19);
  assert.ok(tickHitW > notchHitW && tickHitH > notchHitH, `tick ${tickHitW}x${tickHitH} vs notch ${notchHitW}x${notchHitH}`);
});

test("railPads: 5px each side when alone, clamped at the rail's ends", () => {
  assert.deepEqual(railPads([{ y: 100, h: 4 }], 5, 500), [[5, 5]]);
  assert.deepEqual(railPads([{ y: 2, h: 4 }], 5, 500), [[2, 5]], "never above the rail's top");
  assert.deepEqual(railPads([{ y: 494, h: 4 }], 5, 500), [[5, 2]], "never below its bottom (the pane's composer edge)");
  assert.deepEqual(railPads([], 5, 500), []);
});

test("railPads: neighbours split the free gap, so no pad reaches a neighbour's paint", () => {
  // 100..104 and 108..112: 4px free → 2 each
  assert.deepEqual(railPads([{ y: 100, h: 4 }, { y: 108, h: 4 }], 5, 500), [[5, 2], [2, 5]]);
  // 5px free → floor(2.5) each; an odd pixel stays unclaimed rather than shared
  assert.deepEqual(railPads([{ y: 100, h: 4 }, { y: 109, h: 4 }], 5, 500), [[5, 2], [2, 5]]);
  // an unread tick paints 6 tall: the gap is measured from ITS bottom
  assert.deepEqual(railPads([{ y: 100, h: 6 }, { y: 110, h: 4 }], 5, 500), [[5, 2], [2, 5]]);
  // touching or overlapping paints: no pad between them, never negative
  assert.deepEqual(railPads([{ y: 100, h: 4 }, { y: 102, h: 4 }], 5, 500), [[5, 0], [0, 5]]);
  // order-free: the input is the store's order, not the rail's
  assert.deepEqual(railPads([{ y: 108, h: 4 }, { y: 100, h: 4 }], 5, 500), [[2, 5], [5, 2]]);
  // no pad ever meets a neighbour's: for every adjacent pair, bottom+down <= next top-up
  const ys = [3, 9, 30, 31, 44, 60, 61, 90];
  const items = ys.map((y, i) => ({ y, h: i % 3 === 0 ? 6 : 4 }));
  const pads = railPads(items, 5, 100);
  const order = items.map((_, i) => i).sort((a, b) => items[a].y - items[b].y);
  for (let k = 0; k + 1 < order.length; k++) {
    const a = order[k], b = order[k + 1];
    if (items[a].y === items[b].y) continue;
    assert.ok(items[a].y + items[a].h + pads[a][1] <= items[b].y - pads[b][0] || items[a].y + items[a].h > items[b].y,
      `pads of ${a} and ${b} stay apart: ${JSON.stringify([items[a], pads[a], items[b], pads[b]])}`);
  }
});

test("railPads: two threads on one passage share a top — they pad against the NEXT height, not each other", () => {
  // the unread one paints above its read sibling (z-index 1); both keep the full pad toward the ticks around them
  assert.deepEqual(railPads([{ y: 100, h: 4 }, { y: 100, h: 6 }], 5, 500), [[5, 5], [5, 5]]);
  assert.deepEqual(railPads([{ y: 100, h: 4 }, { y: 100, h: 6 }, { y: 112, h: 4 }], 5, 500), [[5, 4], [5, 3], [3, 5]],
    "the next tick's top pad yields to the TALLER sibling's bottom");
});
