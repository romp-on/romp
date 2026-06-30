// "Nudge" button (the user 2026-06-18): on the WORKING card itself (moved off the modal footer), beside
// Clear. One click sends the canned AUTO_NUDGE_TEXT status question (mirrored from bin/romp-kernel) via
// askFollowUp with nudge:true (romp-authored → gray bubble) — the SAME path Follow up uses, so the kernel
// quotes the goal as context. Source-level pin (no jsdom for the feed renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("the card builds a Nudge button in its actions row, beside Clear", () => {
  assert.match(FEED, /const nudge = el\("button", "fdismiss ffollow fask-nudge"\).*nudge\.textContent = "Nudge"/);
  // the action row is buttons only now (the state badges moved up to the name row, 2026-06-19)
  assert.match(FEED, /actions\.append\(apiRetry, revive, nudge, cardFup, clr\)/);
  assert.match(FEED, /a\._nudge = nudge;/);
});

test("Nudge sends the canned status question via the askFollowUp path (goal quoted as context)", () => {
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "askFollowUp", itemId: it\.itemId, nudge: true, text: "Status on the goal above: what's done, what's left, and is anything blocked waiting on a decision from me\?" \}\);/);
});

test("Nudge acknowledges the click immediately (the user 2026-06-24): guards re-fire, relabels, self-restores", () => {
  // a nudge with no visible change invites a re-click → double-nudge; disable + relabel on click, then restore.
  assert.match(FEED, /if \(nudge\.disabled\) return;/);
  assert.match(FEED, /nudge\.disabled = true;/);
  assert.match(FEED, /nudge\.textContent = "Nudged";/);
  assert.match(FEED, /if \(nudge\.isConnected\) \{ nudge\.disabled = false; nudge\.textContent = "Nudge"; \} \}, 1500\)/);
});

test("Nudge shows ONLY on a real working card (not provisional/recheck/apiError)", () => {
  assert.match(FEED, /a\._nudge\.style\.display = \(it\.column === "working" && !it\.provisional && !it\.recheck && it\.blocked\?\.state !== "apiError"\) \? "" : "none";/);
});

test("Nudge is NO LONGER in the modal footer (it moved to the card)", () => {
  assert.doesNotMatch(FEED, /feed-modal-checkstatus/);
  assert.doesNotMatch(FEED, /wireCheckStatus/);
  assert.match(FEED, /footRow\.append\(age, fup, clr\)/);   // footer back to age · Follow up · Clear
});

test("a card 'Follow up' button on blocked/completed cards jumps straight into the modal composer (the user 2026-06-22)", () => {
  // the button sits in the action row beside Nudge/Clear
  assert.match(FEED, /const cardFup = el\("button", "fdismiss ffollow fask-fup"\); cardFup\.textContent = "Follow up"/);
  assert.match(FEED, /actions\.append\(apiRetry, revive, nudge, cardFup, clr\)/);
  // shown ONLY on blocked (needs_input) or completed cards — mutually exclusive with Nudge (working only)
  assert.match(FEED, /a\._cardFup\.style\.display = \(\(it\.column === "needs_input" \|\| it\.column === "completed"\) && !it\.provisional\) \? "" : "none"/);
  assert.match(FEED, /a\._nudge\.style\.display = \(it\.column === "working" && !it\.provisional && !it\.recheck && it\.blocked\?\.state !== "apiError"\)/);
  // click → open THIS goal's modal AND request the composer pop open on the next render
  assert.match(FEED, /cardFup\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); fullscreenAskId = it\.itemId; openFollowUpOnRender = true; renderModal\(\); \}/);
  // renderModal consumes the flag: pops the box open + focuses, but only when the modal's Follow up is visible
  // (so it no-ops for a standalone deliverable, whose Follow up is hidden) — the modal's own Follow up stays.
  assert.match(FEED, /if \(openFollowUpOnRender\) \{\s*openFollowUpOnRender = false;\s*if \(fupEl && fupEl\.style\.display !== "none" && fuboxEl && fuinEl\) \{\s*fuboxEl\.style\.display = ""; growFollowUp\(fuinEl\); fuinEl\.focus\(\);/);
  assert.match(FEED, /wireFollowUp\(fupEl, fuboxEl, fuinEl, fusendEl/, "the modal's own Follow up composer is still wired");
});
