// Feed cards FLY to their new column when status changes (the user 2026-06-27), instead of teleporting. Cards
// are reused nodes reconcileCol MOVES between columns, so FLIP works: record rect+column before the move, then
// invert+play after. A column-CROSSER rides the BACK layer (z-index:-1 in the #feed-cols stacking context) so it
// never passes over other cards; a card that STAYED in a column but shifted (because a sibling left) glides IN
// PLACE so the column reflows smoothly instead of snapping (the user 2026-06-29). Source pins (no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("render() captures rects BEFORE the reconcile and flies changed cards AFTER", () => {
  // capture must precede the column reconciles… — and, since 2026-09-04, happens only when a card CAN have
  // moved (a column change, an arrival, a departure: feed-flip.ts), because the capture and the fly each
  // force a layout of the whole document on the main thread every pane shares
  assert.match(FEED, /const flipFirst = needFlip \? captureCardRects\(cols\) : new Map<string, FlipState>\(\);[\s\S]*?reconcileCol\(cols\.asks/);
  // …and the fly runs after the DOM (and scroll) settle (the identity-alias step sits just before it)
  assert.match(FEED, /list\.scrollTop = prevScroll;[\s\S]*?\/\/ FLIP step 2[\s\S]*?if \(needFlip\) flyColumnChanges\(flipFirst, cols\);/);
});

test("FLIP-across-identity: a new-key card aliases to its predecessor's rect so it slides, not pops", () => {
  // each render maps goal itemId → covering card key; a card whose key is NEW borrows its predecessor's First rect
  assert.match(FEED, /const curItemKey = new Map<string, string>\(\);/);
  assert.match(FEED, /coverInto\("a:" \+ e\.ask\.itemId, \[e\.ask\.itemId, \.\.\.\(e\.ask\.tree \|\| \[\]\)\.map\(\(n\) => n\.id\)\]\)/);
  assert.match(FEED, /const prevKey = prevItemKey\.get\(itemId\);/);
  assert.match(FEED, /if \(prevKey && prevKey !== curKey && flipFirst\.has\(prevKey\)\) flipFirst\.set\(curKey, flipFirst\.get\(prevKey\)!\);/);
  assert.match(FEED, /prevItemKey = curItemKey;/);   // remembered for next render
});

test("captureCardRects records each card's rect + column", () => {
  assert.match(FEED, /function captureCardRects\(/);
  assert.match(FEED, /m\.set\(c\.dataset\.key, \{ rect: c\.getBoundingClientRect\(\), col: colEl\.id \}\)/);
});

test("flyColumnChanges FLIPs any moved card (not new cards / non-movers); only crossers ride the back layer", () => {
  assert.match(FEED, /function flyColumnChanges\(/);
  assert.match(FEED, /if \(!prev\) continue;/);                               // brand-new card → no FLIP
  assert.match(FEED, /if \(!dx && !dy\) continue;/);                          // no real move → skip
  // staying in the same column NO LONGER aborts — an in-column shifter must glide too (the user 2026-06-29)
  assert.doesNotMatch(FEED, /prev\.col === colEl\.id\) continue/);
  // a column-crosser gets the back layer; an in-column shifter glides in normal flow (the decision is taken
  // in the read pass and carried to the write pass — feed-flip.test.ts pins the read-then-write order)
  assert.match(FEED, /crossed: prev\.col !== colEl\.id/);
  assert.match(FEED, /if \(crossed\) c\.classList\.add\("fitem-flying"\);/);
});

test("FLIP: invert to the old spot instantly, then release with a transition (two rAFs)", () => {
  assert.match(FEED, /c\.style\.transition = "none";\s*\n\s*c\.style\.transform = `translate\(\$\{dx\}px, \$\{dy\}px\)`;/);
  assert.match(FEED, /requestAnimationFrame\(\(\) => requestAnimationFrame\(\(\) => \{[\s\S]*?c\.style\.transform = "translate\(0, 0\)";/);
  // cleans up on transitionend so the card returns to normal flow + stacking (the back-layer class only on a crosser)
  assert.match(FEED, /ev\.propertyName !== "transform"/);
  assert.match(FEED, /if \(crossed\) c\.classList\.remove\("fitem-flying"\)/);
});

test("respects prefers-reduced-motion", () => {
  assert.match(FEED, /matchMedia\("\(prefers-reduced-motion: reduce\)"\)\.matches\) return;/);
});

test("the flying card sits in the BACK layer, and #feed-cols is the stacking context that makes that work", () => {
  assert.match(CSS, /\.feed-cols \{[^}]*position: relative; z-index: 0;/);
  assert.match(CSS, /\.fitem-flying \{ position: relative; z-index: -1; pointer-events: none; will-change: transform; \}/);
});
