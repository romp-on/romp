// The per-host card-loading hint (the user 2026-08-25): after attaching a remote host, its sessions
// land via the faster channels while its cards trail with no cue — the board now says cards are on
// the way, one quiet loader line per pending host, retiring on the exact event of that host's first
// merged contribution. The SIGNAL executes here (mergeHostFeeds is pure); the feed wiring is
// source-pinned (the repo convention). Synthetic hosts only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeHostFeeds } from "./federation";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

const local = { type: "feed", asks: [], sessions: [], order: [] };

test("an attached host with no contribution yet is PENDING; its first payload retires it", () => {
  const before = mergeHostFeeds({ "": local }, ["", "TESTHOST"]);
  assert.deepEqual(before.pendingHosts, ["TESTHOST"], "sessions shown, cards pending — the hint window");
  const after = mergeHostFeeds({ "": local, TESTHOST: { type: "feed", asks: [{ itemId: "TESTHOST:x", sid: "TESTHOST:a" }] } },
    ["", "TESTHOST"]);
  assert.deepEqual(after.pendingHosts, [], "the first contribution is the retire event");
});

test("an EMPTY contribution is a valid arrival — retire, never wait forever", () => {
  const m = mergeHostFeeds({ "": local, TESTHOST: { type: "feed", asks: [] } }, ["", "TESTHOST"]);
  assert.deepEqual(m.pendingHosts, [], "a host with nothing to send has still answered");
});

test("the local kernel never pends; two remotes pend independently", () => {
  assert.deepEqual(mergeHostFeeds({ "": local }, [""]).pendingHosts, []);
  const m = mergeHostFeeds({ "": local, HOSTA: { type: "feed", asks: [] } }, ["", "HOSTA", "HOSTB"]);
  assert.deepEqual(m.pendingHosts, ["HOSTB"], "each host retires on ITS OWN payload");
});

test("reconnect re-arms by construction: detach deletes the contribution, reattach re-pends", () => {
  // dropHost deletes perHostFeed[host] and re-emits; the next merge recomputes pendingHosts from
  // presence alone — no timers anywhere in the signal (pinned at the source)
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /delete this\.perHostFeed\[host\];/);
  assert.match(FED, /merged\.pendingHosts = hostSeq\.filter\(\(h\) => h !== LOCAL && !\(h in perHost\)\);/);
  const gone = mergeHostFeeds({ "": local }, ["", "TESTHOST"]);
  assert.deepEqual(gone.pendingHosts, ["TESTHOST"], "post-detach reattach looks exactly like first attach");
});

test("the hint covers the WHOLE pending window: escalate at 45s, remove only on the real events", () => {
  assert.match(FEED, /pendingHosts = Array\.isArray\(m\.pendingHosts\) \? m\.pendingHosts\.filter\(\(h: any\) => typeof h === "string"\) : \[\];/);
  assert.match(FEED, /pendingDead = Array\.isArray\(m\.pendingDead\) \? m\.pendingDead\.filter\(\(h: any\) => typeof h === "string"\) : \[\];/);
  assert.match(FEED, /syncHostloadBackstops\(\);/);
  // both board states: the strip rides under the cards AND under the empty wordmark
  assert.match(FEED, /ensureHostLoad\(list\);   \/\/ an attached host's cards may be the ONLY thing coming — say so here too/);
  assert.match(FEED, /ensureHostLoad\(list\);\s*\n\s*list\.scrollTop = prevScroll;/);
  // round two (the user 2026-08-25): the first cut's 45s backstop RETIRED the hint while the host
  // genuinely still pended — the signal knew, the backstop overrode it. Now 45s = COPY ESCALATION,
  // never removal; the pending list itself (payload/detach recompute) is the only way off the board.
  assert.match(FEED, /window\.setTimeout\(\(\) => \{ hostloadLong\.add\(h\); render\(\); \}, 45000\)/);
  assert.match(FEED, /if \(!pendingHosts\.length\) \{ strip\?\.remove\(\); return; \}   \/\/ the real events emptied the list — the only way off/);
  assert.match(FEED, /if \(!pendingHosts\.includes\(h\)\) \{   \/\/ the payload landed \(or the host detached\) — the ONLY removals/);
  assert.doesNotMatch(FEED, /hostloadGaveUp/, "no hide-set survives — nothing ever hides a true wait");
  // the three honest copies: loading → still waiting (45s) → a dead link names itself (fail-loudly)
  assert.match(FEED, /"loading cards from " \+ h \+ "\\u2026"/);
  assert.match(FEED, /"still waiting on " \+ h \+ "\\u2026"/);
  assert.match(FEED, /"can\\u2019t reach " \+ h \+ " \\u2014 its cards return when it reconnects"/);
  // the swirl (the user's pick over the dots): the SHARED reverse-spin glyph, LEFT of the text
  assert.match(FEED, /const swirl = el\("span", "fask-awaiting-swirl"\);/);
  assert.match(FEED, /line\.append\(swirl, txt\);/);
  assert.match(CSS, /\.hostload-line \{ display: flex; align-items: center; gap: 7px; color: var\(--dim\); font-size: 0\.82em; \}/,
    "quiet, dim, existing scale — a hint, never a takeover");
  assert.doesNotMatch(CSS, /hostload-dots/, "the dots retired with the swirl swap");
});

test("the merge names DEAD pending links from socket truth; the feed says that instead of waiting", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /merged\.pendingDead = merged\.pendingHosts\.filter\(\(h: string\) => deadHosts\.includes\(h\)\);/);
  assert.match(FED, /return !c \|\| !c\.ws \|\| c\.ws\.readyState === 3;   \/\/ no socket \/ closed = a dead link right now/);
  const m = mergeHostFeeds({ "": local }, ["", "TESTHOST"], [], ["TESTHOST"]);
  assert.deepEqual(m.pendingDead, ["TESTHOST"], "pending + dead socket = named");
  const alive = mergeHostFeeds({ "": local }, ["", "TESTHOST"], [], []);
  assert.deepEqual(alive.pendingDead, [], "pending on a live socket stays a plain wait");
});
