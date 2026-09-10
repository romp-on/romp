// The quiet feed-card marker + the escalated card's badge text (plans/user-todos.md, slice 2). Todos
// are session-scoped, so EVERY card of the owning session wears the marker (no feed strip in v1); the
// idle-escalation floor's card carries the story on the standard blocked badge instead, so one card
// never says it twice. Data rides build_feed's top-level sid-keyed open-count map, the way
// working[]/bgServices ride the payload. Source pins for the render (feed.ts has no jsdom harness) +
// EXECUTED federation merges (mergeHostFeeds/prefixInbound are importable).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeHostFeeds, prefixInbound } from "./federation";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const FEED_CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the feed payload's sid-keyed map lands in module state", () => {
  assert.match(FEED, /let userTodosMap: Record<string, number> = \{\};/);
  assert.match(FEED, /userTodosMap = m\.userTodos && typeof m\.userTodos === "object" && !Array\.isArray\(m\.userTodos\)/);
});

test("every card of the owning session wears the quiet marker", () => {
  assert.match(FEED, /el\("a", "fask-usertodo"\)/);
  assert.match(FEED, /const utn = userTodosMap\[it\.sid\] \|\| 0;/);
  assert.match(FEED, /waiting on you/, "the split card's own heading vocabulary — one term everywhere");
});

test("the marker yields on the escalated card (the blocked badge carries the story there)", () => {
  assert.match(FEED, /utn > 0 && it\.blocked\?\.state !== "userTodos"/,
    "one card never says it twice");
});

test("the marker is quiet, and click-safe via updateAskCard's per-push rewire", () => {
  assert.match(FEED_CSS, /\.fask-usertodo \{/);
  assert.match(FEED_CSS, /\.fask-usertodo[^}]*var\(--dim\)/, "dim by default — quiet, never an alarm");
  // the pill's border is a theme token in both blocks (the tab glyph's rule, tab-usertodo.test.ts): a
  // white alpha reads on the dark page and is invisible on the light one
  const rule = (FEED_CSS.match(/\.fask-usertodo \{[^}]*\}/) || [""])[0];
  const hover = (FEED_CSS.match(/\.fask-usertodo:hover \{[^}]*\}/) || [""])[0];
  assert.ok(rule && hover, "both rules exist");
  assert.match(rule, /border: 1px solid var\(--box-border\)/);
  assert.match(hover, /border-color: var\(--dim\)/, "hover brightens through the same tokens");
  assert.doesNotMatch(rule + hover, /rgba\(255, 255, 255/, "never hardcoded white");
  // the marker opens the owning session's chat (where the split card with Reply/Dismiss lives)
  const marker = FEED.slice(FEED.indexOf("const utn = userTodosMap"));
  assert.match(marker.slice(0, 900), /openSession/, "click lands on the session, live");
  assert.match(marker.slice(0, 900), /ev\.stopPropagation\(\)/, "the card's own body click never fires underneath");
});

test("the marker's text counts past one and its tip says what to do", () => {
  const marker = FEED.slice(FEED.indexOf("const utn = userTodosMap"), FEED.indexOf("const utn = userTodosMap") + 900);
  assert.match(marker, /utn > 1 \? `⚑ waiting on you · \$\{utn\}` : "⚑ waiting on you"/);
  assert.match(marker, /click to open its chat and reply/);
  assert.match(marker, /utMark\.style\.display = "none";/, "a card with no open todo shows nothing — no empty pill");
});

test("the escalated card wears the userTodos badge text, never the ⏸ picker fallthrough", () => {
  assert.match(FEED, /it\.blocked\.state === "userTodos" \? "⚑ waiting on you"/,
    "its own badge text — never the ⏸ picker fallthrough");
  const at = FEED.indexOf('=== "userTodos" ? "⚑ waiting on you"');
  const badge = FEED.slice(at - 400, at + 1200);
  assert.match(badge, /live: true/, "the badge click opens the chat at its live bottom (the split card)");
  assert.match(badge, /click to open its chat and reply/, "the reply-flavored tip, not the jump-to-prompt one");
  assert.match(badge, /: " — click to jump to the prompt in the chat"\)\);/, "…which permission/picker keep");
});

test("the blocked payload type admits the floor's count", () => {
  assert.match(FEED, /count\?: number;\s*\/\/ userTodos/);
});

test("federation: the map's keys are host-prefixed inbound and merged across hosts", () => {
  const pre: any = prefixInbound("TESTHOST", { type: "feed", userTodos: { "11111111-2222": 2 } });
  assert.deepEqual(pre.userTodos, { "TESTHOST:11111111-2222": 2 });
  const merged: any = mergeHostFeeds({
    "": { type: "feed", asks: [], userTodos: { "11111111-2222": 1 } },
    "TESTHOST": { type: "feed", asks: [], userTodos: { "TESTHOST:33333333-4444": 2 } },
  }, ["", "TESTHOST"]);
  assert.deepEqual(merged.userTodos, { "11111111-2222": 1, "TESTHOST:33333333-4444": 2 });
});

test("federation: the local host is the identity transform, and a non-feed frame's map is left alone", () => {
  const local: any = prefixInbound("", { type: "feed", userTodos: { "11111111-2222": 2 } });
  assert.deepEqual(local.userTodos, { "11111111-2222": 2 });
  const other: any = prefixInbound("TESTHOST", { type: "chat", userTodos: { "11111111-2222": 2 } });
  assert.deepEqual(other.userTodos, { "11111111-2222": 2 }, "only the feed frame carries the sid-keyed map");
});

test("a host too old to send the map contributes nothing and breaks nothing", () => {
  const merged: any = mergeHostFeeds({
    "": { type: "feed", asks: [] },
    "TESTHOST": { type: "feed", asks: [] },
  }, ["", "TESTHOST"]);
  assert.deepEqual(merged.userTodos, {});
});

test("a malformed map (an array, a scalar) contributes nothing rather than throwing", () => {
  const merged: any = mergeHostFeeds({
    "": { type: "feed", asks: [], userTodos: [3] },
    "TESTHOST": { type: "feed", asks: [], userTodos: 7 },
  }, ["", "TESTHOST"]);
  assert.deepEqual(merged.userTodos, {});
  const pre: any = prefixInbound("TESTHOST", { type: "feed", userTodos: [3] });
  assert.deepEqual(pre.userTodos, [3], "prefixInbound passes an unrecognized shape through untouched");
});
