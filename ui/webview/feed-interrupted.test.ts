// The "interrupted" badge (the user 2026-07-05): the user stopped a session mid-turn and hasn't messaged
// it since — its quiet is user-chosen, not a stall. The kernel suppresses auto-nudge until the user's next
// message (the user-message EVENT, never a timer) and stamps `interrupted` on the working card so it says
// why it's sitting still instead of reading like an orphaned goal. The stalled/nudge-failed chips outrank
// it (they carry a romp-ask outcome; this only explains silence) — a card never wears both. Source pin,
// like feed-nudge-failed.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the interrupted badge is built once and rides the wrapping chip row", () => {
  assert.match(FEED, /const intBadge = el\("span", "fask-interrupted"\)/);
  assert.match(FEED, /intBadge\.textContent = "interrupted"/, "text label, no emoji/glyph");
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/);
  assert.match(FEED, /a\._interrupted = intBadge;/);
});

test("it.interrupted toggles the badge, yielding to the stalled chips and the in-flight interrupting badge", () => {
  assert.match(FEED,
    /\(it\.interrupted && !it\.interrupting && !it\.nudgeFailed\) \? "" : "none";/,
    "past-tense badge shows only once the interrupt has SETTLED (not while interrupting) and no nudge-failed story covers the card");
  assert.match(FEED, /interrupted\?: boolean;/, "the card payload carries the kernel flag");
});

test("the tooltip explains the suppression contract", () => {
  assert.match(FEED, /you stopped this session mid-turn; romp won't follow up on its own until you message it again/);
});

test("the badge is a warning-yellow pill, same treatment as the warn chip", () => {
  // was neutral gray and too easy to miss (the user 2026-07-06); one warning yellow across the card
  assert.match(CSS, /\.fask-interrupted \{[^}]*color: #ffd166/);
  assert.match(CSS, /\.fask-warnchip \{[^}]*color: #ffd166/, "shares the warn chip's yellow, no new color");
  assert.match(CSS, /\.fask-interrupted \{[^}]*font-size: 0\.64em/, "same size as its sibling pills");
});
