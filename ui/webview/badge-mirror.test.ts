// Card trouble badges mirror into the shell's bell (the user 2026-07-27) — the chip stays on the
// card; the bell gets ONE durable entry per episode. EXECUTES ./badge-mirror; the feed plumbing
// (seen-set persistence + the {romp:'notify'} post) is source-pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { badgeNotices, clearBoundaryNotices, type BadgeItem, type ClearNoticeRow } from "./badge-mirror";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

const base = (over: Partial<BadgeItem>): BadgeItem =>
  ({ itemId: "TESTSID:g1", sid: "TESTSID", name: "api", text: "ship the notes-api", ...over });

test("every trouble chip becomes one entry, in the session's name", () => {
  const items = [base({
    warns: [{ kind: "distill", t: 100, msg: "the summarizer gave up" }],
    nudgeFailed: true,
    retrying: { since: 300 },
    blocked: { state: "apiError", status: 529 },
  })];
  const { notices } = badgeNotices(items, new Set());
  assert.deepEqual(notices.map((n) => n.kind), ["warn", "nudge", "retry", "apierror"]);
  assert.ok(notices.every((n) => n.text.startsWith("api — ")), "each entry names the session");
  // every notice carries its jump target so the bell entry can lead back to the card (2026-07-28)
  assert.ok(notices.every((n) => n.sid === "TESTSID" && n.itemId === "TESTSID:g1"));
  assert.match(notices[0].text, /warning: the summarizer gave up/);
  assert.match(notices[3].text, /API error 529/);
});

test("a stalled hold mints NO log entry — the card chip is its only surface", () => {
  // The user 2026-07-29: the log filled with "stalled" rows for holds that resolved in seconds
  // (the judge ruled, or the nudge fired and worked) — the mechanism doing its job, not a problem.
  // A stall that defeats the nudge escalates to nudgeFailed, which does log.
  const it = { ...base({}), stalled: { why: "reviver not retiring", since: 200, note: "romp is holding this" } };
  const { notices, active } = badgeNotices([it], new Set());
  assert.equal(notices.length, 0);
  assert.equal(active.size, 0, "no sig either — nothing for the seen-set to hold");
});

test("a seen signature stays quiet; the SAME badge next push logs nothing", () => {
  const items = [base({ warns: [{ kind: "distill", t: 200, msg: "w" }] })];
  const first = badgeNotices(items, new Set());
  assert.equal(first.notices.length, 1);
  const second = badgeNotices(items, new Set(first.active));
  assert.equal(second.notices.length, 0, "the persisted active set is exactly the next call's seen set");
});

test("a NEW episode (different since/t) is a new entry; a cleared badge leaves the active set", () => {
  const s1 = badgeNotices([base({ warns: [{ kind: "distill", t: 200, msg: "w" }] })], new Set());
  const s2 = badgeNotices([base({ warns: [{ kind: "distill", t: 999, msg: "w" }] })], new Set(s1.active));
  assert.equal(s2.notices.length, 1, "a fresh warn episode logs again");
  const gone = badgeNotices([base({})], new Set(s2.active));
  assert.equal(gone.active.size, 0, "no badge → no active sigs → the next occurrence re-logs");
});

test("only the API-error block mirrors — an ordinary permission ask is not an error", () => {
  const { notices } = badgeNotices([base({ blocked: { state: "ask" } })], new Set());
  assert.equal(notices.length, 0);
});

test("spend-limit and prompt-too-long blocks say what they are", () => {
  const sl = badgeNotices([base({ blocked: { state: "apiError", spendLimit: true } })], new Set()).notices[0];
  const tl = badgeNotices([base({ blocked: { state: "apiError", tooLong: true } })], new Set()).notices[0];
  assert.match(sl.text, /spend limit reached/);
  assert.match(tl.text, /prompt too long/);
});

test("a refusal block names the refusal and the remedy — never a bare \"API error\"", () => {
  // the fifth on-you class (the user 2026-08-15): a refusal ships state:"apiError" + refusal:true with
  // no status, which fell through to the generic label while the card itself said "Safeguards refused"
  const rf = badgeNotices([base({ blocked: { state: "apiError", refusal: true } })], new Set()).notices[0];
  assert.equal(rf.kind, "apierror");
  assert.match(rf.text, /safeguards refused this prompt — rewrite it or drop this thread/);
  assert.doesNotMatch(rf.text, /API error/);
  // a refusal that rides a 4xx must still read as the refusal, not "the request itself was rejected"
  const rf400 = badgeNotices([base({ blocked: { state: "apiError", status: 400, refusal: true } })], new Set()).notices[0];
  assert.match(rf400.text, /safeguards refused/);
  assert.doesNotMatch(rf400.text, /API error 400/);
});

test("a refusal after a plain error on the same card is a NEW episode — its own bell entry", () => {
  // the signature's class slot must discriminate the refusal, or a refusal arriving while a status-less
  // transient episode is still in the seen set (error → auto-retry → refusal) mints no entry at all
  const plain = badgeNotices([base({ blocked: { state: "apiError" } })], new Set());
  const after = badgeNotices([base({ blocked: { state: "apiError", refusal: true } })], new Set(plain.active));
  assert.equal(after.notices.length, 1, "the refusal logs afresh under its own signature");
  assert.match(after.notices[0].text, /safeguards refused/);
});

test("a /clear boundary that dropped cards logs once, naming them and the way back", () => {
  // the user 2026-07-27: the boundary settle was fully silent — cards left the board with one
  // stderr line. The bell entry is the durable "you saw it happen" record.
  const rows: ClearNoticeRow[] = [{ sid: "TESTSID", name: "web", t: 1234,
    titles: ["ship the notes-api", "tune the rate limits"] }];
  const first = clearBoundaryNotices(rows, new Set());
  assert.equal(first.notices.length, 1);
  assert.equal(first.notices[0].kind, "cleared");
  assert.match(first.notices[0].text, /^web — \/clear dropped 2 open cards: ship the notes-api, tune the rate limits/);
  assert.match(first.notices[0].text, /Undo on the feed/, "the way back is in the entry itself");
  const again = clearBoundaryNotices(rows, new Set(first.active));
  assert.equal(again.notices.length, 0, "the same boundary never re-logs across pushes/reloads");
});

test("a NEWER boundary on the same session is a new entry — its own t keys the signature", () => {
  const a = clearBoundaryNotices([{ sid: "TESTSID", name: "web", t: 1, titles: ["x"] }], new Set());
  const b = clearBoundaryNotices([{ sid: "TESTSID", name: "web", t: 2, titles: ["y"] }], new Set(a.active));
  assert.equal(b.notices.length, 1, "a fresh clear logs afresh");
  assert.match(b.notices[0].text, /1 open card: y/);
});

test("the feed posts each notice to the shell and persists only the ACTIVE set", () => {
  assert.match(FEED, /mirrorBadges\(incomingAsks, Array\.isArray\(m\.clearNotices\) \? m\.clearNotices : \[\],/,
    "runs on every feed payload, against the FULL list + the kernel's clear notices");
  assert.match(FEED, /clearBoundaryNotices\(clears, seenSet\)/, "clear drops ride the same seen-set");
  assert.match(FEED,
    /window\.parent\?\.postMessage\(\{ romp: "notify", kind: n\.kind, text: n\.text, sid: n\.sid, itemId: n\.itemId \}, "\*"\)/,
    "each notice carries its jump target (sid + itemId) for the bell's click-through");
  assert.match(FEED, /localStorage\.setItem\(BADGE_SEEN_KEY, JSON\.stringify\(Array\.from\(active\)\)\)/,
    "active-only persistence is what re-arms a cleared badge and bounds the store");
});


test("the feed answers revealCard: scroll to the card, pulse it accent, session fallback", () => {
  // the return path of the bell's jump (the user 2026-07-28): the shell posts {romp:'revealCard'};
  // the feed finds the card by its data-key, scrolls + pulses; a gone card opens the session instead.
  assert.match(FEED, /if \(m\.romp === "revealCard"\) \{/);
  assert.match(FEED, /\[data-key="a:\$\{String\(m\.itemId \|\| ""\)\}"\]/);
  assert.match(FEED, /target\.scrollIntoView\(\{ block: "center", behavior: "smooth" \}\);/);
  assert.match(FEED, /target\.classList\.add\("reveal-pulse"\);/);
  assert.match(FEED, /animationend.*reveal-pulse.*once: true/, "the pulse is one-shot");
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "openSession", id: String\(m\.sid\) \}\)/);
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
  assert.match(CSS, /\.reveal-pulse \{ animation: revealPulse 1\.6s ease; \}/);
  assert.match(CSS, /box-shadow: 0 0 0 2px var\(--accent\)/, "accent chrome, not a status colour");
});
