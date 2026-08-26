// The "⚠ retrying since HH:MM" chip (the user 2026-07-09): an api-retry storm INSIDE an open turn used to
// render as plain healthy "Working" — the API-error badge only fires once the session is idle-stalled, so
// nimbus's card said nothing through an ~80-minute storm. The kernel's _session_retrying (live backend
// state + the states log's stretch start) rides the working card as `retrying`; the card wears a faded
// red chip on the session-name row. Source-level pin like feed-interrupting.test.ts (no jsdom harness);
// the kernel behavior itself is tested in tests/test_kernel_retrying_chip.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("the retrying badge is built once and rides the session-state row beside the API-error badge", () => {
  assert.match(FEED, /const retryBadge = el\("span", "fask-retrying"\)/);
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge,/,
    "session-STATE badges ride the name row, off the action row");
  assert.match(FEED, /a\._retryBadge = retryBadge;/);
});

test("it.retrying toggles the chip; the stopped-on-error badge outranks it", () => {
  assert.match(FEED, /retrying\?: \{ since\?: number \| null; count\?: number \} \| null;/,
    "the card payload carries the kernel signal");
  assert.match(FEED, /\(rt && !isApiErr\) \? "" : "none";/,
    "shown only while retrying and NOT idle-stalled on an API error — never both red badges");
  assert.match(FEED, /rt\.since \? `⚠ retrying since \$\{clockHM\(rt\.since\)\}` : "⚠ retrying"/,
    "the storm's start renders as a clock time; a not-yet-logged stretch renders timeless");
});

test("the tooltip says the session is in motion, not stalled", () => {
  assert.match(FEED, /still in motion, not stalled; it resumes on its own when the API recovers/);
});

test("it wears the red api-trouble family, faded because the session is still in motion", () => {
  assert.match(CSS, /\.fask-retrying \{[^}]*color: #e5484d/);
  assert.match(CSS, /\.fask-retrying \{[^}]*font-size: 0\.7em/, "same size as .fask-apierror — same information type");
  assert.match(CSS, /\.fask-retrying \{[^}]*opacity: 0\.85/, "faded — in motion, unlike the stopped-on-error badge");
});

test("the kernel emits `retrying` on the working card only — a chip, never a column move", () => {
  assert.match(KERNEL, /sess_retrying = _session_retrying\(fsid, tm\)/);
  assert.match(KERNEL, /"retrying": \(sess_retrying if column == "working" else None\)/);
});
