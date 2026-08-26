// Monthly spend-cap handling (the user 2026-07-14): a billing cap ("You've hit your monthly spend limit")
// is on YOU and has no readable reset, so unlike a transient API error it must NEVER auto-retry — retrying
// can't lift a spend cap, and without this the 10s loop stormed forever ("retry retry retry…"). The kernel
// classifies it (_api_error.spendLimit) and auto-engages the global pause (reason "spend"); the client
// skips it in the retry tick, renders it red/on-you, and shows a "raise your cap" line. The chat/feed
// renderers have no jsdom harness, so pin the wiring at source. (Kernel side: tests/test_kernel_usage_limit.py
// AutoPauseOnSpendLimit + tests/test_kernel.py TestApiError.)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const F = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("Status carries the per-session apiSpendLimit flag", () => {
  assert.match(R, /apiSpendLimit\?: boolean/);
});

test("the auto-retry tick SKIPS a spend-capped thread (retrying can't fix a billing cap)", () => {
  // a spent MODEL allowance is skipped by the same rule (the user 2026-08-01), and a dead credential
  // too (per-session auth, the user 2026-08-08): every retry re-presents the same broken login/key
  assert.match(R, /!s\.status\.retrySuppressed && !s\.status\.apiSpendLimit && !s\.status\.apiModelLimit && !s\.status\.apiAuthErr/);
});

test("a spend cap paints the tab alarm-red (on-you), not amber retrying", () => {
  // a safeguards refusal is the fifth on-you class (the user 2026-08-15) — see apierror-refusal.test.ts
  assert.match(R, /\(s\.status\.apiTooLong \|\| s\.status\.apiSpendLimit \|\| s\.status\.apiModelLimit \|\| s\.status\.apiAuthErr \|\| s\.status\.apiRefusal\) \? "tab-blocked" : "tab-retrying"/);
});

test("the paused line names the spend cap and points at the settings page (no fake countdown)", () => {
  assert.match(R, /globalRetryReason === "spend"/);
  assert.match(R, /monthly spend limit reached — raise it at claude\.ai\/settings\/usage/);
});

test("the globalRetryPaused push reason is threaded into the client", () => {
  assert.match(R, /globalRetryReason = typeof m\.reason === "string" \? m\.reason : ""/);
});

test("the feed card badges a spend cap and HIDES Retry (a useless click there)", () => {
  assert.match(F, /const spendLimit = !!\(it\.blocked && it\.blocked\.spendLimit\)/);
  assert.match(F, /a\._apiRetry\.style\.display = \(showApiErr && !spendLimit && !modelLimit && !refusal\) \? "" : "none"/);
  assert.match(F, /spendLimit \? "⚠ Spend limit"/);
  assert.match(F, /spendLimit\?: boolean/);
});

// A spent MODEL allowance is the same shape and gets the same treatment (the user 2026-08-01): Retry
// re-fails until the model changes or its window resets, so the button goes and the badge names the fix.
test("the feed card badges a spent model allowance and HIDES Retry too", () => {
  assert.match(F, /const modelLimit = !!\(it\.blocked && it\.blocked\.modelLimit\)/);
  assert.match(F, /modelLimit \? "⚠ Model limit"/);
  assert.match(F, /modelLimit\?: boolean/);
});
