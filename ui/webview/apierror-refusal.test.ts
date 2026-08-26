// Safeguards-refusal handling (the user 2026-08-15): a classifier refusal ("<Model>'s safeguards
// flagged this message…") is DETERMINISTIC on the same input — a retry re-sends the same prompt and
// manufactures the same refusal (one refused prompt drew twelve auto-retries in ~6 minutes before this),
// and in fallback configurations each retry manufactures another model downgrade. The kernel classifies
// it (_api_error.refusal — the system model_refusal_* record linked by parentUuid, plus the CLI's own
// wording as a co-equal signature) and never auto-retries it; the client skips it in the retry tick,
// renders it red/on-you, and names the real fix: rewrite the prompt or drop the thread. The chat/feed
// renderers have no jsdom harness, so pin the wiring at source. (Kernel side:
// tests/test_kernel_refusal_retry.py.)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const F = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("Status carries the per-session apiRefusal flag", () => {
  assert.match(R, /apiRefusal\?: boolean/);
});

test("the auto-retry tick SKIPS a refused thread (a retry manufactures the same refusal)", () => {
  assert.match(R, /!s\.status\.retrySuppressed && !s\.status\.apiSpendLimit && !s\.status\.apiModelLimit && !s\.status\.apiAuthErr && !s\.status\.apiRefusal/);
});

test("a refusal paints the tab alarm-red (on-you), not amber retrying", () => {
  assert.match(R, /\(s\.status\.apiTooLong \|\| s\.status\.apiSpendLimit \|\| s\.status\.apiModelLimit \|\| s\.status\.apiAuthErr \|\| s\.status\.apiRefusal\) \? "tab-blocked" : "tab-retrying"/);
});

test("the chat error card drops Retry on a refusal and names the real fix in plain terms", () => {
  const apiErr = R.slice(R.indexOf("function renderApiError"), R.indexOf("// ── API-error auto-retry"));
  // the refusal joins the no-Retry gate (the button cannot work — same contract as the other on-you classes)
  assert.match(apiErr, /const refusal = !!st\?\.apiRefusal;/);
  assert.match(apiErr, /const spendCap = !!st\?\.apiSpendLimit \|\| !!st\?\.apiModelLimit \|\| !!st\?\.apiAuthErr \|\| refusal;/);
  // …and the countdown line says what happened + the fix, never "retrying soon…" — via the ONE shared
  // remedy string, so this write and the tick's re-assert below cannot drift into different words
  assert.match(R, /const REFUSAL_REMEDY = "the model's safeguards refused this prompt — rewrite it or drop this thread";/);
  assert.match(apiErr, /if \(refusal\) countdown\.textContent = REFUSAL_REMEDY;/);
  // no Dismiss-dialog dead button either: the Esc-sender is for the CLI's spend-limit menu, which a
  // refusal never parks
  assert.match(apiErr, /\} else if \(st\?\.backend === "tmux" && !refusal\) \{/);
});

test("the 1s countdown tick RE-ASSERTS the refusal remedy — it must never write \"retrying soon…\" over it", () => {
  // apiRetryTick's countdown writer runs every second; without its own branch it fell through to the
  // else arm (a refusal session is out of the retry set, so `at` is always undefined) and overwrote the
  // remedy with "retrying soon…" one second after render — promising a retry the kernel never fires.
  // The branch leads the ladder (the global pause is about auto-retry, which a refusal never gets), and
  // WRITING each tick, rather than skipping, also heals the Stop/Resume-all handler's blanket rewrite.
  const tick = R.slice(R.indexOf("function apiRetryTick"), R.indexOf("function renderTool"));
  assert.match(tick, /if \(active\?\.status\.apiRefusal\) \{/);
  assert.match(tick, /cd\.textContent = REFUSAL_REMEDY;\s*\} else if \(globalRetryPaused\) \{/,
    "the remedy branch sits ABOVE the paused/suppressed/countdown chain");
});

test("the feed card badges a refusal and HIDES Retry (a useless click there)", () => {
  assert.match(F, /const refusal = !!\(it\.blocked && it\.blocked\.refusal\)/);
  assert.match(F, /a\._apiRetry\.style\.display = \(showApiErr && !spendLimit && !modelLimit && !refusal\) \? "" : "none"/);
  assert.match(F, /refusal \? "⚠ Safeguards refused"/);
  assert.match(F, /refusal\?: boolean/);
  // the badge tooltip carries the kernel's plain-terms remedy, not the CLI's boilerplate
  assert.match(F, /\(spendLimit \|\| refusal\) \? it\.blocked\.what/);
});
