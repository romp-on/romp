// The "Awaiting <peer>" chip (the user 2026-06-22): when it.waitingOn is set (the kernel's _wait_for_graph
// found this session has an unanswered message out to a LIVE peer), the card shows a teal pill "Awaiting
// <peer>" on the wrapping chip row — the peer NAME in its native identity colour, NO emoji; a mutual-wait
// CYCLE keeps the red variant with a "Deadlock" label instead of "Awaiting". Source pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("a waiting-on chip is built and rides the wrapping chip row (its own line when it doesn't fit)", () => {
  assert.match(FEED, /const waitOnBadge = el\("span", "fask-waiton"\)/);
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/);
  assert.match(FEED, /a\._waitOn = waitOnBadge;/);
});

test("it.waitingOn drives the chip: 'Awaiting <peer>' / 'Handed off to <peer>' (native-colour name, NO emoji) / 'Deadlock' for a cycle", () => {
  // the label is "Awaiting " / "Handed off to " (delegate kind) / "Deadlock " — no ⏳/⟲ emoji
  // (the user 2026-06-22 / 2026-07-25: a delegation handoff is not "awaiting background agents")
  assert.match(FEED, /woPre\.textContent = wo\.inCycle \? "Deadlock " : wo\.kind === "delegate" \? "Handed off to " : "Awaiting "/);
  assert.doesNotMatch(FEED, /⏳ waiting on|⟲ deadlock/, "no emoji prefix anymore");
  // the peer NAME is a separate span in its OWN identity colour (like the ↪ from provenance)
  assert.match(FEED, /woName\.textContent = wo\.name/);
  assert.match(FEED, /if \(wo\.color && wo\.color\.bg\) woName\.style\.color = wo\.color\.bg/);
  assert.match(FEED, /"fask-waiton" \+ \(wo\.inCycle \? " fask-waiton-cycle" : ""\)/);   // teal pill / red cycle kept
  assert.match(FEED, /a\._waitOn\.style\.display = "";/, "shown when waitingOn is set");
  assert.match(FEED, /a\._waitOn\.style\.display = "none";/, "hidden when it isn't");
});

test("the chip has its own teal style + a distinct red cycle variant", () => {
  assert.match(CSS, /\.fask-waiton \{[^}]*color: #4ec9b0/);
  assert.match(CSS, /\.fask-waiton-cycle \{ color: #ff6a6a/);
});
