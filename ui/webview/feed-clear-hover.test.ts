// Clearing a feed card must flush its cross-surface hover highlight immediately (the user 2026-07-03): hovering
// a card lights its journey on the timeline (and chat); Clear REMOVES the card, so the card's own `mouseleave`
// never fires and the highlight stuck until you moved the mouse. Each card's Clear handler dispatches a synthetic
// `mouseleave` first, reusing the exact leave logic (clear the highlight, or restore a pinned card's). Source-pin
// (the hover plumbing has no jsdom harness here, like the other feed hover/focus tests).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("the ask card's Clear flushes the hover highlight, right before the header conjunction + pendingCleared", () => {
  // the one-motion header exit (2026-08-24) rides between the flush and the suppression — same click
  assert.match(FEED, /card\.dispatchEvent\(new MouseEvent\("mouseleave"\)\);\s*\n\s*dressHeaderIfLast\(card, it\.sid\);[^\n]*\n\s*pendingCleared\.add\(it\.itemId\);/);
});

test("the group card's Clear flushes the hover highlight too", () => {
  assert.match(FEED, /card\.dispatchEvent\(new MouseEvent\("mouseleave"\)\);[^\n]*\n\s*const cur = \(card as any\)\._g as AskGroup;/);
});
