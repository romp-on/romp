// The red "follow-up failed" chip (plans/stalled-open-todos-nudge.md): romp auto-nudges a stalled goal
// ONCE; if the response turn ends with the goal still working-stalled, it is never re-asked — the card
// carries the red pill instead. RENAMED off "stalled" (the user 2026-07-23, superseding their 2026-07-02
// label): "stalled" now belongs exclusively to the yellow Stalled section (romp holding a WORKING card),
// and this chip means the opposite — romp already asked, the thread waits on YOU. Two surfaces, two words.
// Source pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the follow-up-failed chip is built once and rides the wrapping chip row", () => {
  assert.match(FEED, /const nfBadge = el\("span", "fask-nudgefailed"\)/);
  assert.match(FEED, /nfBadge\.textContent = "follow-up failed"/,
    "the label is 'follow-up failed' (the user 2026-07-23) — 'stalled' is the yellow section's word; no emoji/glyph");
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge\)/);
  assert.match(FEED, /a\._nudgeFailed = nfBadge;/);
});

test("the chip and the Stalled section never share a word — one word per meaning", () => {
  // the RED chip (waiting on you) must not say "stalled"; the YELLOW section (romp holding it) keeps it
  assert.doesNotMatch(FEED, /nfBadge\.textContent = "stalled"/);
  assert.match(FEED, /stallBtn\.textContent = "Stalled";/, "the yellow section keeps the word");
  assert.doesNotMatch(FEED, /nfBadge\.title = "[^"]*stalled/, "the chip tooltip drops the word too");
});

test("it.nudgeFailed toggles the chip (the stalled FLOOR retired 2026-07-07: a failed nudge records a real block)", () => {
  assert.match(FEED, /a\._nudgeFailed\.style\.display = it\.nudgeFailed \? "" : "none";/);
  // the '⏸ stalled' badge + the blocked.state "stalled" three-way went with the floor: the card now
  // reaches Needs-you via the normal ladder (block verdict + decision brief), wearing only the chip
  assert.doesNotMatch(FEED, /⏸ stalled/);
});

test("the chip has its own red pill style (waiting on the human now)", () => {
  assert.match(CSS, /\.fask-nudgefailed \{[^}]*color: #ff6a6a/);
});

// nudge HISTORY (the user 2026-07-02): the EVIDENCE that romp did follow up — how many times, and when —
// rides the card as `nudged` (kernel _nudge_times) and surfaces on the chip tooltip + a modal line. Born
// of the SSH-thread confusion: two auto-nudges had fired, but nothing card-side said so, so the chip read
// like romp never tried.
test("the card carries the auto-nudge history and the chip tooltip cites it", () => {
  assert.match(FEED, /nudged\?: \{ count: number; times: number\[\] \} \| null;/);
  assert.match(FEED, /a\._nudgeFailed\.title = it\.nudged && it\.nudged\.times\.length/,
    "with history the tooltip is dynamic…");
  assert.match(FEED, /romp followed up \$\{it\.nudged\.count\}× \(\$\{it\.nudged\.times\.map\(clockHM\)\.join\(", "\)\}\)/);
  assert.match(FEED, /: "romp followed up once; the response didn't resolve it/,
    "…and the static wording stays as the no-history floor");
});

test("the modal shows the follow-up history line for a single ask", () => {
  assert.match(FEED, /const nudges = el\("div", "feed-modal-nudges"\)/, "built once in the modal foot chrome");
  assert.match(FEED, /nudEl\.textContent = `romp followed up \$\{nu\.count\}× — \$\{nu\.times\.map\(clockHM\)\.join\(", "\)\}`;/);
  assert.match(FEED, /nudEl\.style\.display = "none";/, "hidden when the target has no recorded fires");
  assert.match(CSS, /\.feed-modal-nudges \{[^}]*color: var\(--dim\)/, "dim meta text, not a shouting banner");
});

test("the dead 'reopened' chip is gone (2026-07-07): cleared-is-sealed made it unreachable", () => {
  assert.doesNotMatch(FEED, /fask-reopened/);
  assert.doesNotMatch(FEED, /it\.reopened/);
  assert.doesNotMatch(CSS, /fask-reopened/);
});
