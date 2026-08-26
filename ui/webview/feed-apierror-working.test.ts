// An API error is a TRANSIENT stall, not a block (the user 2026-06-29): the card STAYS in Working (not moved
// to Blocked/needs-input) and just shows the "⚠ API error" chip + Retry. Source pins (no jsdom for feed.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("askColumn maps it.column directly — no crafty it.blocked re-route (the user 2026-06-29)", () => {
  // it.column is AUTHORITATIVE now: the kernel floors a live permission/picker block to needs_input itself, so
  // the client just maps snake_case. The old `if (it.blocked && state !== "apiError") return "needsInput"`
  // override is GONE — it existed only because the kernel used to report a picker-blocked card as "working".
  assert.doesNotMatch(FEED, /it\.blocked && it\.blocked\.state !== "apiError"/, "the it.blocked override is gone");
  assert.match(FEED, /return it\.column === "needs_input" \? "needsInput" : it\.column === "completed" \? "completed" : "asks";/);
  // an apiError card keeps column=working from the kernel → lands in "asks" (Working), no special-casing needed.
});

test("the apiError chip + Retry still show (they key on blocked.state, not the column)", () => {
  assert.match(FEED, /const isApiErr = it\.blocked\?\.state === "apiError";/);
  assert.match(FEED, /a\._apiBadge\.style\.display = showApiErr \? "" : "none";/);
  // Retry shows for a transient/on-you-compactable error but is HIDDEN for a spend cap (retrying can't lift a
  // billing limit) — the user 2026-07-14; the spend-cap wiring is pinned in apierror-spend-limit.test.ts,
  // and a safeguards refusal joins the hide (the user 2026-08-15) — pinned in apierror-refusal.test.ts.
  assert.match(FEED, /a\._apiRetry\.style\.display = \(showApiErr && !spendLimit && !modelLimit && !refusal\) \? "" : "none";/);
});


// ── the Retry cleanup (the user 2026-08-24, screenshot: a recovered session — GREEN awaiting dot —
// still wearing a lone red Retry with no badge in sight, reading as arbitrary) ───────────────────
test("Retry renders WITH its explaining badge as one visual unit — never alone on the action row", () => {
  // badge immediately before its button, as DIRECT row2 children: grouped mode hides idwrap (the
  // card drops its name there), which is exactly how the screenshot got a lone Retry with no badge —
  // the badge sat in the hidden wrap while the action-row button stayed visible
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge, origin,/);
  assert.match(FEED, /actions\.append\(revive, qApprove, qDeny\);/);
  assert.doesNotMatch(FEED, /actions\.append\(apiRetry/, "no lone Retry detached from its badge");
  assert.doesNotMatch(FEED, /idwrap\.append\([^)]*apiBadge/, "…and never inside the grouped-mode-hidden idwrap");
});

test("badge + Retry RETIRE the moment the session recovers — live payload state, not the stored block record", () => {
  // recovered = working again OR awaiting its dispatched background work — the exact sets the
  // session dot reads (the screenshot's green dot), refreshed on every push; event-keyed, no timers
  assert.match(FEED, /const apiRecovered = workingSet\.has\(it\.name\) \|\| awaitingSet\.has\(it\.name\);/);
  assert.match(FEED, /const showApiErr = isApiErr && !apiRecovered;/);
  assert.match(FEED, /a\._apiBadge\.style\.display = showApiErr \? "" : "none";/);
  assert.match(FEED, /a\._apiRetry\.style\.display = \(showApiErr && !spendLimit && !modelLimit && !refusal\) \? "" : "none";/);
  // the badge-text/onclick block gates on the same live answer, so a stale record wires nothing
  assert.match(FEED, /if \(showApiErr && it\.blocked\) \{/);
  // the button's acknowledge-on-click behavior is untouched
  assert.match(FEED, /a\._apiRetry\.disabled = true; a\._apiRetry\.textContent = "Retrying…";/);
});
