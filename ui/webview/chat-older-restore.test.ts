// Scroll-back history loading must be INVISIBLE — it must never move what the reader is looking at
// (the user 2026-08-02).
//
// The reported bug: clicking a card's distilled summary landed on the summary, then a second later the chat
// jumped to an unrelated old Bash tool card; clicking the summary again then worked. The locate audit caught
// it exactly — two rows a second apart, the first the user's click (pointer-exact, with an anchorT), the
// second a jump to a different uuid with anchorT null that no click produced.
//
// The chain: the click lands pointer-exact → landOn top-aligns the summary → when that summary sits within
// WINDOW_RADIUS of the resident head and older history is still on the server, the resulting scrollTop trips
// virtualizeToViewport's "at the top of the resident events" branch → requestOlder fetches the previous chunk
// → chatHead lands its re-anchor as a DEEP-LINK (top-align + flash) onto the FIRST TURN IN THE DOM, a whole
// window-radius above what the reader was reading. Merely scrolling up after the click did the same thing.
//
// Two things were wrong and both are pinned here: requestOlder anchored on the wrong row (first-in-DOM, not
// the row at the viewport top), and chatHead landed a position-restore as if it were a navigation.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function bodyOf(name: string): string {
  const at = RENDER.indexOf("function " + name + "(");
  assert.ok(at > 0, name + " must exist");
  const end = RENDER.indexOf("\n}", at);
  return RENDER.slice(at, end);
}

test("requestOlder anchors on the row at the VIEWPORT TOP, not the first turn in the DOM", () => {
  const body = bodyOf("requestOlder");
  assert.match(body, /captureScrollAnchor\(content, v\)/,
    "the re-anchor row is the one the reader is looking at (captureScrollAnchor), not v.el's first child");
  assert.ok(!/const firstTurn = v\.el\.querySelector\(["'`]\.turn\[data-uuid\]/.test(body),
    "first-in-DOM must not be the PRIMARY anchor — after a deep-link it is a window-radius off-screen");
});

test("requestOlder records a keep-offset, so the arrival restores rather than jumps", () => {
  const body = bodyOf("requestOlder");
  assert.match(body, /pendingOlderKeepY\.set\(sid, keep\?\.y \?\? 0\)/,
    "a scroll-back stashes the row's on-screen offset (0 when nothing was capturable — still never a jump)");
});

test("a deep-link fetch stays a deep-link: no keep-offset, so it top-aligns and flashes", () => {
  const body = bodyOf("fetchOlderForAnchor");
  assert.match(body, /pendingOlderKeepY\.delete\(sid\)/,
    "fetchOlderForAnchor is a real navigation — it must clear any stale keep-offset for that session");
  // The short-circuit branch matters just as much: when a fetch is ALREADY in flight fetchOlderForAnchor
  // returns false without running, and that in-flight fetch may be a requestOlder whose keep-offset would
  // silently demote this click to a position restore. scrollToAnchor clears it at the re-point.
  const sca = bodyOf("scrollToAnchor");
  assert.match(sca, /pendingOlderAnchor\.set\(activeId, uuid\);\s*pendingOlderKeepY\.delete\(activeId\);/,
    "re-pointing the arrival at a clicked uuid also drops the scroll-back offset");
});

test("chatHead never overwrites a deep-link that is still waiting to land", () => {
  const body = bodyOf("chatHead");
  assert.match(body, /if \(anchorUuid && !pendingAnchor\)/,
    "a pending deep-link (where the user asked to GO) outranks a scroll-back re-anchor (where they WERE)");
  assert.match(body, /pendingAnchorKeepY = keepY \?\? null/,
    "the arrival carries the keep-offset through to the landing");
});

test("scrollToAnchor restores a keep-offset instead of landing on it", () => {
  const body = bodyOf("scrollToAnchor");
  assert.match(body, /if \(pendingAnchorKeepY != null\) \{/, "the keep-offset branch exists");
  const at = body.indexOf("if (pendingAnchorKeepY != null) {");
  const branch = body.slice(at, body.indexOf('landTrail.push("pointer-exact")', at));   // the keep branch alone
  assert.match(branch, /landTrail\.push\("pointer-keep-offset"\)/,
    "the audit trail names the restore, so it is never mistaken for a click again");
  assert.match(branch, /content\.scrollTop = yNow - keepY/,
    "the row comes back at its captured offset");
  assert.ok(!/landOn\(/.test(branch), "a restore must NOT top-align + flash the row like a jump");
  // …and the ordinary path still does land properly.
  assert.match(body, /landTrail\.push\("pointer-exact"\);\s*\n\s*landOn\(target, uuid\);/,
    "a genuine deep-link still lands via landOn (uuid = the one-flash-per-navigation key)");
});

test("a keep-offset restore that misses does not toast the reader 'couldn't locate'", () => {
  // Nobody asked to locate anything — they scrolled. The audit row still records it.
  assert.match(RENDER, /if \(!scrolled && !anchorPendingOlder && !att\.keep && !\(seek && att\.anchor === seek\.uuid\)\) \{/,
    "the couldn't-locate toast + error-center entry are for user navigations only — and a live SEEK "
    + "keeps working instead (its backstop owns the failure; see seek-indicator.test.ts)");
  assert.match(RENDER, /keep: att\.keep \|\| undefined,/, "…but the audit row still carries the flag");
});

test("the kernel persists the keep flag, so click and scroll-restore stay distinguishable in the audit", () => {
  const kernel = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");
  const at = kernel.indexOf('elif msg and msg.get("type") == "locateDiag":');
  assert.ok(at > 0, "the locateDiag handler must exist");
  assert.match(kernel.slice(at, at + 1600), /"keep": bool\(msg\.get\("keep"\)\)/,
    "locate-audit.jsonl rows say whether a landing was a user navigation");
});

// executed: the offset restore itself — the arithmetic that keeps the reader still.
test("restoring by offset keeps the anchored row exactly where it was on screen", () => {
  // Model: content scrolled to `scrollTop`; the anchor row sits `y` px below the viewport top. Older history
  // is prepended ABOVE it, growing everything above by `prependH`. Restoring must put it back at the same y.
  const restore = (scrollTopNow: number, rowTopInScrollSpace: number, keepY: number) => rowTopInScrollSpace - keepY;
  const keepY = 12;                       // the row was 12px below the viewport top when the fetch went out
  const rowAfter = 4000 + 300;            // 4000px of older history prepended above a row that was at 300
  const st = restore(0, rowAfter, keepY);
  assert.equal(st, 4288, "scrollTop lands so the row is 12px below the viewport top again");
  assert.equal(rowAfter - st, keepY, "…i.e. its on-screen offset is unchanged");
  // The old behaviour (landOn → scrollIntoView block:"start") would have forced offset 0, and on the WRONG
  // row at that: the first turn in the DOM rather than the reader's own.
  assert.notEqual(0, keepY, "top-aligning is a visible jump for any reader not already flush at the top");
});
