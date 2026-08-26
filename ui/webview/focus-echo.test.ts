// Click→peek must FEEL instant (the user 2026-08-24: "clicking into a not-currently-shown session
// is slow … even if it involves just having a provisional thing showing"). Measured split: the
// chat's own first render of a 6000-event hidden transcript is ~25-80ms (the windowed deferred
// build) — the felt seconds were the kernel ROUND-TRIP (click → WS → _reveal_chat_for → focus
// frame), unbounded under kernel load. So the feed click drops a same-origin echo the chat hears
// in milliseconds and acknowledges SYNCHRONOUSLY (tab + peek + loader), and the kernel's focus
// frame follows to land the anchor, idempotently. Harness-verified: ack same-tick with the echo,
// the romp loader holding an unbuilt 6000-event view, content ≤80ms, loader gone on content, the
// trailing kernel frame a no-op. Source pins (no jsdom for the monolith).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("every feed card click echoes the sid BEFORE the kernel round-trip — no showOnTimeline post without it", () => {
  assert.match(FEED, /function focusEcho\(sid: string\): void \{/);
  assert.match(FEED, /localStorage\.setItem\("romp:focus-echo", JSON\.stringify\(\{ sid, t: Date\.now\(\) \}\)\);/);
  const posts = (FEED.match(/type: "showOnTimeline"/g) || []).length;
  const echoes = (FEED.match(/focusEcho\(/g) || []).length - 1;   // minus the definition
  assert.equal(posts, echoes, "every showOnTimeline click site carries its focusEcho — " + posts + " posts, " + echoes + " echoes");
});

test("the chat acknowledges the echo synchronously: reveal, un-suppress, peek, activate — anchor left to the kernel frame", () => {
  const at = RENDER.indexOf('if (e.key !== "romp:focus-echo" || !e.newValue) return;');
  assert.ok(at > 0, "the storage listener exists");
  const block = RENDER.slice(at, at + 700);
  assert.ok(block.includes("if (!sid || (!sessions.has(sid) && !tabMeta.has(sid))) return;"),
    "an unknown sid falls through to the kernel (it may route a revive/confirm)");
  const order = ["revealSelfPane();", "closingTabs.delete(sid);", "assertPeekFor(sid);", "setActive(sid);"];
  let last = -1;
  for (const step of order) {
    const i = block.indexOf(step);
    assert.ok(i > last, step + " runs, in order");
    last = i;
  }
});

test("the first-visit wait is the ROMP LOADER, not a bare text hint — and the build event removes it", () => {
  // the standing wait-state rule: swirl (reverse spin) + wordmark + pulsing accent dots
  assert.match(RENDER, /sw\.src = mediaSrc\("romp-swirl-glyph\.svg"\); sw\.alt = ""; sw\.onerror = \(\) => sw\.remove\(\);/);
  assert.match(RENDER, /wm\.textContent = "romp";/);
  assert.match(RENDER, /dots\.append\(el\("i"\), el\("i"\), el\("i"\)\);/);
  assert.doesNotMatch(RENDER, /ld\.textContent = "Loading transcript…"/, "the bare hint is gone");
  assert.match(CSS, /\.tx-loading-swirl \{ width: 18px; height: 18px; animation: tx-swirl-spin 1\.6s linear infinite reverse; \}/);
  assert.match(CSS, /\.tx-loading-dots i \{[^}]*background: var\(--accent\);/s, "the pulsing dots wear the accent, never a status color");
  // removal is EVENT-based: the deferred build replaces the view's children (harness-verified gone-on-content)
  assert.match(RENDER, /truly empty → the ROMP LOADER holds the spot/);
});
