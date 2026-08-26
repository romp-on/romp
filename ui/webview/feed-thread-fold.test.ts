// COLLAPSIBLE THREADS in the grouped feed (the user 2026-07-31): a caret right of each session-header name
// folds that thread away to its name alone, and it STAYS folded — including for cards that do not exist yet
// — until you expand it again. Persisted with the rest of the feed's disclosure state.
//
// Keyed by SID rather than by column-run on purpose: a card's column is not knowable when you fold, so a
// per-column fold could not honour "future cards stay folded" the moment a card moved columns.
//
// No jsdom for the feed renderer, so the render side is pinned at the source; the STATE side is executed in
// feed-view-state.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the caret sits to the RIGHT of the session name, where it was asked for", () => {
  assert.match(FEED, /const fold = el\("button", "feed-sess-fold"\);/);
  assert.match(FEED, /h\.append\(nm, fold, cnt, svc, svcList\);/, "name, then caret — not a leading tree triangle");
});

test("it is a caret, and it says which way it will go", () => {
  assert.match(FEED, /fold\.textContent = shut \? "▸" : "▾";/);
  assert.match(FEED, /fold\.setAttribute\("aria-expanded", shut \? "false" : "true"\);/);
  assert.match(FEED, /fold\.setAttribute\("aria-label", \(shut \? "expand " : "collapse "\) \+ e\.name\);/);
});

test("folding hides that thread's cards and counts them onto the header", () => {
  // the header stands in for the run: entries are counted, not rendered
  assert.match(FEED, /if \(collapsedThreads\.has\(s\)\) \{ if \(head\) head\.folded \+= entryCards\(e\); continue; \}/);
  assert.match(FEED, /foldn\.textContent = String\(e\.folded\);/, "bare number (the user 2026-08-26) — words on hover only");
  assert.match(FEED, /foldn\.style\.display = shut && e\.folded \? "" : "none";/);
});

test("the column count reports the BOARD, not what you have open", () => {
  // folding a thread must not read as its work having left the column
  assert.match(FEED, /const nCards = \(es: Entry\[\]\) => es\.reduce\(\(n, e\) => n \+ entryCards\(e\), 0\);/);
});

test("the fold is per-SESSION, so a card that does not exist yet inherits it", () => {
  assert.match(FEED, /const collapsedThreads = new Set<string>\(\);/);
  assert.match(FEED, /if \(collapsedThreads\.has\(e\.sid\)\) collapsedThreads\.delete\(e\.sid\); else collapsedThreads\.add\(e\.sid\);/);
  // …and it survives a reload with the rest of the disclosure state
  assert.match(FEED, /for \(const k of st\.threads\) collapsedThreads\.add\(k\);/);
  assert.match(FEED, /threads: \[\.\.\.collapsedThreads\]/);
});

test("the click acknowledges immediately and cannot be lost to a re-render", () => {
  // the header ELEMENT is reused across renders (sessHeadEls), so the button node survives; the handler is
  // re-pointed at the current entry each update, and the fold is its own acknowledgement (local + render)
  assert.match(FEED, /fold\.onclick = \(ev\) => \{\s*\n\s*ev\.stopPropagation\(\);[\s\S]{0,200}?render\(\);\s*\n\s*\};/);
  assert.match(FEED, /card = sessHeadEls\.get\(key\) \|\| makeSessHead\(\);/);
});

test("a jump into a folded thread unfolds it instead of landing on nothing", () => {
  // revealCards scrolls to a DOM element; a folded card has none, so the navigation would silently no-op
  assert.match(FEED, /if \(collapsedThreads\.has\(a\.sid\) && extHoverMatches\("a:" \+ a\.itemId, keys\)\) \{/);
  assert.match(FEED, /if \(opened\) render\(\);/);
});

test("the caret is a bare glyph, and a folded header keeps its group's spacing", () => {
  // a chip outline on every header would draw a border down the whole column
  assert.match(CSS, /\.feed-sess-fold \{[^}]*border: 0;/);
  assert.match(CSS, /\.feed-sess-fold \{[^}]*cursor: pointer/);
  assert.match(CSS, /\.feed-sess-fold:hover, \.feed-sess-fold:focus-visible \{ color: var\(--fg\); \}/);
  assert.match(CSS, /\.feed-sess-head\.folded \{ margin-bottom: 7px; \}/);
  // the count reuses the header's existing label size rather than adding one
  assert.match(CSS, /\.feed-sess-foldn \{[^}]*font-size: 0\.74em/);
});
