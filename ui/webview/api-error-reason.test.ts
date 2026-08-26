// An API failure says whose problem it is, in words that decide what to do next (the user 2026-07-29).
// EXECUTES ./api-error-reason; the two callers (chat retry line, bell entry) are source-pinned so neither
// can quietly go back to printing a bare status code.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { apiErrorReason } from "./api-error-reason";
import { badgeNotices, type BadgeItem } from "./badge-mirror";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("529 is named as server-side and temporary, not a fault of the session", () => {
  const s = apiErrorReason({ status: 529 });
  assert.match(s, /overloaded/i);
  assert.match(s, /server-side/i);
  // the whole point of the sentence: a long thread bouncing while a fresh one connects is NOT a broken thread
  assert.match(s, /not this session/i);
});

test("the on-you cases outrank the status code, because only they are actionable", () => {
  // a spend cap arrives as a 4xx; read by status alone it would misreport as an ordinary rejection
  assert.match(apiErrorReason({ status: 400, spendLimit: true }), /spend limit reached/i);
  assert.match(apiErrorReason({ status: 400, tooLong: true }), /prompt too long/i);
});

test("a spent MODEL allowance names the fix, not a wait (the user 2026-08-01)", () => {
  // it arrives as a 429, which by status alone reads "rate limited — the account's quota" and sends the
  // user off to wait out a window when the account is fine and one model switch unblocks the session
  const s = apiErrorReason({ status: 429, modelLimit: true });
  assert.match(s, /switch model|add credits/i);
  assert.doesNotMatch(s, /rate limited/i);
});

test("a safeguards refusal names the real fix, and outranks the status code (the user 2026-08-15)", () => {
  // a refusal is deterministic on the same input — by status alone a 400 would read as an ordinary
  // rejection, sending the user to retry the one class that can never succeed on a retry
  const s = apiErrorReason({ status: 400, refusal: true });
  assert.match(s, /safeguards refused this prompt/);
  assert.match(s, /rewrite it or drop this thread/);
  assert.equal(apiErrorReason({ refusal: true }), s, "with or without a status, the same words");
});

test("a dead network and a busy API are told apart", () => {
  assert.match(apiErrorReason({ status: 529, networkDown: true }), /offline/i);
  assert.doesNotMatch(apiErrorReason({ status: 529, networkDown: true }), /overloaded/i);
});

test("a bare 429 names BOTH account-wide causes — a key's per-minute limits are not 'quota'", () => {
  // the user 2026-08-19, on key billing, went hunting for a spent quota that wasn't the cause: keys
  // are never unlimited (per-minute org limits by tier), subscriptions have usage windows — say both
  const s = apiErrorReason({ status: 429 });
  assert.match(s, /per-minute limits or the plan's usage window/);
  assert.match(s, /not this session/);
  assert.match(s, /retries clear it/);
});

test("a quota 429 names which limit it hit", () => {
  assert.match(apiErrorReason({ status: 429, rateLimitType: "output_tokens" }), /output_tokens/);
  assert.match(apiErrorReason({ status: 429 }), /rate limited/i);
});

test("unknown facts produce no sentence rather than an invented cause", () => {
  assert.equal(apiErrorReason({}), "");
  assert.equal(apiErrorReason({ status: null }), "");
  assert.equal(apiErrorReason({ status: "not-a-number" }), "");
  assert.equal(apiErrorReason({ status: 200 }), "", "a non-error status explains nothing");
});

test("other server errors still read as server-side", () => {
  assert.match(apiErrorReason({ status: 500 }), /server-side/i);
  assert.match(apiErrorReason({ status: 503 }), /server-side/i);
  assert.match(apiErrorReason({ status: 404 }), /model/i);
});

test("the bell entry carries the reason and the attempt count, not just 'retry storm'", () => {
  const it: BadgeItem = {
    itemId: "TESTSID:g1", sid: "TESTSID", name: "api", text: "ship the notes-api",
    retrying: { since: 300, count: 7, max: 10, status: 529 },
  };
  const { notices } = badgeNotices([it], new Set());
  assert.equal(notices.length, 1);
  assert.match(notices[0].text, /attempt 7 of 10/, "how far into the backoff it is");
  assert.match(notices[0].text, /overloaded/i, "and what is actually failing");
});

test("a blocked API-error entry explains the status instead of only quoting it", () => {
  const it: BadgeItem = {
    itemId: "TESTSID:g1", sid: "TESTSID", name: "api", text: "ship the notes-api",
    blocked: { state: "apiError", status: 529 },
  };
  const { notices } = badgeNotices([it], new Set());
  assert.match(notices[0].text, /API error 529/, "the code is still there for the record");
  assert.match(notices[0].text, /server-side/i, "and now says what it means");
});

test("a spend-limit block reads as the spend limit, with no bare status pasted in front", () => {
  const it: BadgeItem = {
    itemId: "TESTSID:g1", sid: "TESTSID", name: "api", text: "ship the notes-api",
    blocked: { state: "apiError", status: 400, spendLimit: true },
  };
  const { notices } = badgeNotices([it], new Set());
  assert.match(notices[0].text, /spend limit/i);
  assert.doesNotMatch(notices[0].text, /API error 400/);
});

test("the chat's retry line shows the reason and hides the request id in the tooltip", () => {
  // progressive disclosure: the gist is on the line, the id is one hover away (CLAUDE.md)
  assert.match(RENDER, /apiErrorReason\(info\)/, "the retry line renders the shared reason");
  assert.match(RENDER, /request \$\{info\.requestId\}/, "the request id rides the tooltip");
  assert.doesNotMatch(RENDER, /err\.textContent = \[status, msg\]\.filter/,
    "the old status+message-only line is gone");
});

test("the retrying element renders whenever there is anything to say", () => {
  // networkDown alone (status 0 / no message) must still surface — it is the most actionable case of all
  assert.match(RENDER, /if \(info\.status \|\| info\.error \|\| info\.networkDown\)/);
});
