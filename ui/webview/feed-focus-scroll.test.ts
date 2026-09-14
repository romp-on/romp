import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

// T414 (the user 2026-09-15): with the focused-session section on, a click on a card's summary line that jumps into
// another session (the chat's active tab changes; the kernel relays activeChat) must scroll the feed's box to the top,
// so the section, now showing that session's cards, is in view. The pins below hold the mechanism's shape; the served
// lab (tests/test_feed_focus_scroll_served.py) executes it on the real page in both layouts.
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("the summary line's click records the session it jumps to, only with the section on; the already-focused session scrolls at once", () => {
  assert.match(FEED, /dl\.onclick = \(ev: Event\) => \{ ev\.stopPropagation\(\); focusEcho\(it\.sid\); noteFocusJump\(it\.sid\); vscodeApi\?\.postMessage\(\{ type: "showOnTimeline"/,
    "the summary line's jump notes the target before posting the jump");
  const fn = FEED.slice(FEED.indexOf("function noteFocusJump("), FEED.indexOf("function settleFocusScroll("));
  assert.match(fn, /if \(!showFocused\) return;/, "with the section off nothing changes");
  assert.match(fn, /if \(sid === focusedSid\) \{ scrollFeedTop\(\); return; \}/, "a click on the session already focused: no tab change, no frame, so the top at once (the named choice)");
  assert.match(fn, /pendingFocusScroll = sid;/, "a jump to another session waits for the frame that changes the focus");
});

test("the activeChat frame settles the pending scroll with one plain scroll to the top, after the section repainted; a frame for another session drops it", () => {
  assert.match(FEED, /focusedSid = typeof m\.id === "string" && m\.id \? m\.id : null;\s*\n\s*if \(pendingFocusScroll && focusedSid !== pendingFocusScroll\) pendingFocusScroll = null;/,
    "the focus went elsewhere: the jump scroll is moot");
  assert.match(FEED, /render\(\);\s*\n\s*settleFocusScroll\(\);/, "the scroll follows the repaint that shows the new session's cards");
  assert.match(FEED, /else if \(focusStale\) \{ render\(\); settleFocusScroll\(\); \}/, "a frame deferred by the hover-freeze settles the scroll when its render runs");
  const settle = FEED.slice(FEED.indexOf("function settleFocusScroll("), FEED.indexOf("document.getElementById(\"feed-list\")?.addEventListener(\"scroll\""));
  assert.match(settle, /if \(pendingFocusScroll && focusedSid === pendingFocusScroll\) \{ pendingFocusScroll = null; scrollFeedTop\(\); \}/, "settled once, for the session the click named");
  const top = FEED.slice(FEED.indexOf("function scrollFeedTop("), FEED.indexOf("function noteFocusJump("));
  assert.match(top, /list\.scrollTop = 0;/, "a plain scroll to the top of the feed's scroll box: the reader's own gesture's consequence");
  assert.doesNotMatch(top, /scrollTo\(|behavior|smooth|scrollIntoView/, "no animated chase");
});

test("a user scroll in between cancels the pending scroll, and the feed's own scrollTop writes are told apart from the reader's", () => {
  assert.match(FEED, /document\.getElementById\("feed-list"\)\?\.addEventListener\("scroll", \(\) => \{\s*\n\s*if \(!progScrollGuard\) pendingFocusScroll = null;/,
    "the reader scrolled: the pending jump scroll yields (the standing scroll rule)");
  assert.match(FEED, /progScrollGuard = true;\s*\n\s*list\.scrollTop = 0;\s*\n\s*requestAnimationFrame\(\(\) => \{ progScrollGuard = false; \}\);/, "our scroll to the top is guarded");
  assert.match(FEED, /progScrollGuard = true;[^\n]*\n\s*list\.scrollTop = prevScroll;\s*\n\s*requestAnimationFrame\(\(\) => \{ progScrollGuard = false; \}\);/, "…and so is the render's own scroll restore");
});
