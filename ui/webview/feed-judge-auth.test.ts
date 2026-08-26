// The "⚠ Can't analyze · API key / login" chip (the user 2026-08-12): when a session's judges fail on
// their CREDENTIAL, romp cannot analyze that session at all — no verdict can move its cards — and for 13
// hours on a key-only host that read as a board silently frozen in Working. The kernel floors the latched
// session's focus card to needs_input carrying blocked.state "judgeAuth" (judge.py's latch, set by a
// credential-class error envelope, cleared by the next successful call); the card wears a FILLED red chip
// — deliberately a new style, inverted from the outlined api-trouble family: those say "the session hit
// trouble", this one says "romp can't even look" — and the card face carries the explanation itself.
// Source-level pin like feed-retrying.test.ts (no jsdom harness); the latch + billing behavior is tested
// in tests/test_judge_auth_billing.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("the judge-auth chip is built once, rides the session-state row, and keys on blocked.state", () => {
  assert.match(FEED, /const jauthBadge = el\("span", "fask-jauth"\)/);
  assert.match(FEED, /row2\.append\(idwrap, retryBadge, apiBadge, apiRetry, jauthBadge, blkBadge,/,
    "the chip rides the name row with its api-trouble siblings");
  assert.match(FEED, /a\._jauthBadge = jauthBadge;/);
  assert.match(FEED, /const isJudgeAuth = it\.blocked\?\.state === "judgeAuth"/);
});

test("the chip names WHICH credential is refused, and the ⏸ picker chip stands down", () => {
  assert.match(FEED, /"⚠ Can't analyze · API key" : "⚠ Can't analyze · login"/,
    "mode 'key' vs 'login' — the label says what to go fix");
  assert.match(FEED, /!isApiErr && !isJudgeAuth && it\.blocked\.state !== "quarantine"/,
    "the generic ⏸ approval/picker chip must not misread a judgeAuth block as a picker");
});

test("the card face carries the explanation itself — a message, not just a chip", () => {
  // no decision brief can exist here (the distiller is one of the judges that are down), so the
  // distill line shows blocked.what instead of sitting empty; applyDistillLine re-runs every push,
  // so the line restores itself the moment the latch clears.
  assert.match(FEED, /if \(isJudgeAuth && it\.blocked && !distillShown\)/);
  assert.match(FEED, /dle\.textContent = it\.blocked\.what \|\| ""/);
});

test("filled red — a new chip style, same size as its api-trouble siblings (same information type)", () => {
  assert.match(CSS, /\.fask-jauth \{[^}]*font-size: 0\.7em/, "same size as .fask-apierror / .fask-retrying");
  assert.match(CSS, /\.fask-jauth \{[^}]*background: #c0392b/, "filled, not outlined — 'romp can't even look'");
});

test("the kernel floors a latched session's focus card with the judgeAuth story", () => {
  assert.match(KERNEL, /_jauth_map = jd\._auth_down_map\(\)/, "one latch read per build");
  assert.match(KERNEL, /jerr and api_top is None and perm_top is None/,
    "yields to the LIVE floors — one interrupt at a time, the present event first");
  assert.match(KERNEL, /"state": "judgeAuth"/);
  assert.match(KERNEL, /the API key its judges bill is being refused/, "the key copy names the fix");
  assert.match(KERNEL, /the login its judges bill is being refused/, "the login copy names the fix");
});
