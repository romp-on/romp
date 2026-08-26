// The "done, confirming" cue (the user 2026-07-24): a Working card whose done verdict has landed but
// whose settle event (the session's attention moving on) is still pending wears a steady chip instead
// of moving columns — an early column move would flicker working↔done on any trailing touch, the exact
// flicker the judge's settle gate exists to prevent. The fact rides the kernel payload as
// `doneConfirming`, sourced from the rollup's `confirming` export (judge rollup_status), never the raw
// nodeComplete flag. Source pins, like feed-interrupted.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the badge is built once and rides the wrapping chip row", () => {
  assert.match(FEED, /const dcBadge = el\("span", "fask-doneconfirming"\)/);
  assert.match(FEED, /dcBadge\.textContent = "done, confirming"/, "text label, no emoji/glyph");
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/);
  assert.match(FEED, /a\._doneConfirming = dcBadge;/);
});

test("it.doneConfirming toggles the badge; the payload carries the kernel flag", () => {
  assert.match(FEED, /\(a\._doneConfirming as HTMLElement\)\.style\.display = it\.doneConfirming \? "" : "none";/);
  assert.match(FEED, /doneConfirming\?: boolean;/, "the card payload carries the kernel flag");
});

test("the cue is an indicator, never a column move", () => {
  // the user 2026-07-24: "I definitely don't want a working done flicker" — nothing in the feed may
  // re-route a card's column off doneConfirming; askColumn stays keyed on it.column alone.
  assert.doesNotMatch(FEED, /doneConfirming[^\n]*column/, "the flag must not touch column routing");
  assert.doesNotMatch(FEED, /column[^\n]*doneConfirming/, "column routing must not read the flag");
});

test("the tooltip explains what happens next in the user's terms", () => {
  assert.match(FEED, /ruled done — it files under Completed once the session has moved on/);
});

test("the pill wears the done-check blue family, same shape as its sibling pills", () => {
  assert.match(CSS, /\.fask-doneconfirming \{[^}]*color: var\(--check-bg\)/, "the ✓ family's blue, no new color");
  assert.match(CSS, /\.fask-doneconfirming \{[^}]*font-size: 0\.64em/, "same size as its sibling pills");
  assert.match(CSS, /\.fask-doneconfirming \{[^}]*border-radius: var\(--radius-pill\)/);
});
