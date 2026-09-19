// Optimistic follow-up MOVE (the user 2026-06-30): submitting a follow-up on a blocked card moves it to
// Working IMMEDIATELY rather than waiting the kernel round-trip. The kernel stays AUTHORITATIVE — the move is a
// short-lived prediction reconciled by the next push; if the kernel never confirms it within the window, the
// card reverts AND a transient toast surfaces the inconsistency. Source-level pins, mirroring feed-clear-race.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("there is a pendingFollowMove prediction map keyed by card itemId", () => {
  assert.match(FEED, /const pendingFollowMove = new Map<string, number>\(\);/);
});

test("submitting a follow-up registers the optimistic move and re-renders the feed at once", () => {
  // postFollowUp predicts the move on the visible card (fbId), then renders so the card slides over immediately
  assert.match(FEED, /optimisticFollowMove\(fbId\);\s*\n\s*render\(\);/);
});

test("a predicted card is kept in Working at render, styled like the kernel's re-checked follow-up", () => {
  assert.match(FEED, /function applyFollowMove\(list: AskItem\[\]\)/);
  // a follow-up prediction wears the re-check styling; an "answer" flips the column only
  // (the messageless plain move was removed with its button/drag, the user 2026-07-25).
  // Styled onto a COPY, never the payload object — the federated page serves the manager's cached
  // frames by reference, and an in-place edit echoed back as a false kernel confirm
  // (follow-move-cache-echo.test.ts, the user 2026-08-02)
  assert.match(FEED, /if \(\(pendingMoveKind\.get\(a\.itemId\) \?\? "followup"\) === "followup"\) \{ c\.recheck = true; c\.followupPending = true; \}/);
  // applied at the top of render so EVERY render (push, modal close) reflects the prediction
  // (2026-07-27: render() prunes the age-provenance popover first — hiding it only when the hovered
  // stamp was torn out of the DOM — so the pin allows that line between the two. 2026-09-07: the
  // paint gate sits between them too — a hidden pane applies state and skips the paint, and this
  // styling is paint-side (a copy for the cards), so it rightly waits with the paint; see paint-gate.ts.)
  const head = FEED.slice(FEED.indexOf("function render() {"));
  const gateEnd = head.indexOf("applyFollowMove(asks);");
  assert.ok(gateEnd > 0, "applyFollowMove(asks) follows the list lookup");
  const between = head.slice(0, gateEnd);
  assert.ok(between.split("\n").length <= 16, "only the paint gate, its observer install and pruneTip may sit before it");
  assert.match(between, /paintHeld\(document\.hidden, feedIntersecting, list\.childElementCount > 0\)/, "the gate");
  assert.match(between, /pruneTip\(\);[^\n]*\n\s*$/, "pruneTip immediately precedes it");
  // the removed drag machinery must not creep back in front of it
  assert.doesNotMatch(FEED, /dragAskId|DRAG_CARDS_ENABLED|fdrop-slot/);
  assert.doesNotMatch(FEED, /"cardMove"/, "the messageless move op is gone from the feed");
});

test("the optimistic move also bumps the card's sort key to now so it lands at the BOTTOM of Working, not the top", () => {
  // the top→bottom lurch (the user 2026-07-03): the moved card kept its stale blocked-era .t and sorted to
  // the TOP, then the real work re-filed at ≈now dropped it to the bottom. Stamp now so it's at the bottom
  // from the instant of the flip; the kernel's followupAt keeps it there when this prediction clears.
  assert.match(FEED, /const nowSec = Math\.floor\(Date\.now\(\) \/ 1000\);/);
  assert.match(FEED, /if \(c\.t < nowSec\) c\.t = nowSec;/);
});

test("the kernel is authoritative: a confirming push clears the prediction, an unconfirmed one is left predicting", () => {
  // reconcile runs against the authoritative incoming payload on every feed push, carrying that payload's
  // buildId so an ACKED prediction can tell a pre-click payload from the kernel's answer (feed-move-ack)
  assert.match(FEED, /lastPayloadBuildId = typeof m\.buildId === "number" \? m\.buildId : 0;/);
  assert.match(FEED, /reconcileFollowMove\(incomingAsks, lastPayloadBuildId, perHostBuildIds, !!cardsUnknown\);/);   // the fourth argument: the payload\'s reading of its cards (T404 round seven)
  // CONFIRMED = the kernel now lists the card as working, OR no longer lists it (cleared/absorbed).
  // (An ANSWER-kind prediction additionally yields to the first payload either way — feed-card-predict.)
  assert.match(FEED, /if \(!a \|\| askColumn\(a\) === "asks" \|\| pendingMoveKind\.get\(id\) === "answer"\) \{/);   // Working by the kernel's category since the boards' phase two (askColumn reads it first, the column from an older kernel)
});

test("an UNANSWERED prediction reverts AND toasts after the backstop (so a behavior change is visible)", () => {
  // The window is a LIVENESS backstop for an ack that never came, no longer the mechanism itself
  // (feed-move-ack.test.ts). It used to be 4s, which the mid-pass goal-store freeze made unwinnable, so it
  // fired on replies that HAD landed (the user 2026-07-21).
  assert.match(FEED, /const MOVE_ACK_MS = 15000;/);
  assert.doesNotMatch(FEED, /FOLLOW_MOVE_MS/, "the old confirm-me-within-4s window is gone");
  // the timer fires only if STILL pending (a confirming push would have deleted it), then drops the prediction,
  // toasts, and re-renders to the kernel's authoritative state
  assert.match(FEED, /if \(!pendingFollowMove\.has\(itemId\)\) return;/);
  assert.match(FEED, /feedToast\(/);
  assert.match(FEED, /function feedToast\(text: string\)/);
});

test("the toast is a styled, auto-dismissing transient notice", () => {
  assert.match(FEED, /t\.classList\.add\("show"\)/);
  assert.match(CSS, /\.feed-toast \{/);
  assert.match(CSS, /\.feed-toast\.show \{ opacity: 1;/);
});
