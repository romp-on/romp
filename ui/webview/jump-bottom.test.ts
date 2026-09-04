// The jump-to-newest chip (the user 2026-08-31): scrolled-up reading leaves follow mode — and the
// send gate keeps it that way — so a floating ↓ at the transcript's bottom-center is the deliberate
// way back: click = snap to the bottom + re-engage follow (today's at-bottom behavior). Visibility
// reads the SAME nearBottom threshold the stick rule and the send gate use — one definition.
// render.ts has import-time DOM side effects → source pins + an executed replica of the decisions
// (send-scroll-preserve.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the chip lives on BODY, never inside the re-rendered #content — click-safety is structural", () => {
  assert.match(RENDER, /document\.body\.appendChild\(jumpBtn\);/);
  assert.match(RENDER, /jumpBtn\.id = "jump-bottom";/);
});

test("visibility reads the one nearBottom definition, off a passive scroll listener — no polling", () => {
  assert.match(RENDER, /const off = c\.scrollHeight > c\.clientHeight \+ 2 && !nearBottom\(c\);/);
  assert.match(RENDER, /c\.addEventListener\("scroll", updateJumpBtn, \{ passive: true \}\);/);
  // a hidden pane measures 0 — the chip must not float over nothing
  assert.match(RENDER, /if \(!c \|\| c\.clientHeight <= 0\) \{ jumpBtn\.hidden = true; return; \}/);
  // appends and tab switches re-read the truth (per-tab correctness rides the restored position)
  assert.match(RENDER, /scheduleRailSticky\(\);\s*\n\s*updateJumpBtn\(\);\s*\/\/ appends can cross the overflow boundary/);
  assert.match(RENDER, /v\.shown = true;\s*\n\s*scheduleRailSticky\(\);\s*\n\s*updateJumpBtn\(\);\s*\/\/ per-tab truth/);
});

test("click snaps to the bottom AND sets the view's stick — the explicit re-entry into follow mode", () => {
  assert.match(RENDER, /c\.scrollTop = c\.scrollHeight;\s*\/\/ the snap IS the acknowledgment/);
  assert.match(RENDER, /if \(v\) \{ v\.stick = true; v\.scrollTop = c\.scrollTop; \}/);
});

test("the send gate stays byte-intact — the chip is the sanctioned mover, sends are not", () => {
  // T187's contract, cross-pinned from this feature so a regression here names both
  assert.match(RENDER, /const wasAtBottom = !!content && nearBottom\(content\);\s*\n\s*appendActive\(\);\s*\n\s*if \(content && wasAtBottom\) content\.scrollTop = content\.scrollHeight;/);
});

test("the chip wears the menu-card vocabulary and survives [hidden] against its own display:flex", () => {
  // bottom-LEFT (the user 2026-09-03: the centered pill went unnoticed — they were still clicking
  // into the transcript and hitting End), and the border reads through the menu token: the raw
  // white-alpha it wore vanished on the light page (cream on cream)
  assert.match(CSS, /#jump-bottom \{\s*\n\s*position: fixed; left: 14px;/);
  assert.match(CSS, /#jump-bottom \{[\s\S]{0,700}?border: 1px solid var\(--menu-border\);/);
  assert.match(RENDER, /setTip\(jumpBtn, "go to bottom — then follow new content"\);/,
    "the tooltip says what the user asked it to say, in the one styled tip");
  assert.match(CSS, /#jump-bottom\[hidden\] \{ display: none; \}/);
  assert.match(CSS, /#jump-bottom:hover \{ border-color: var\(--accent\); color: var\(--accent\); \}/);
  // the phone target WIDENS, never heightens — the compact pill is the ask (the user 2026-08-31)
  assert.match(CSS, /@media \(pointer: coarse\) \{ #jump-bottom \{ width: 64px; height: 32px; \} \}/);
});

test("a short PILL with a stemless chevron — the circle + full arrow were the user's revision (2026-08-31)", () => {
  assert.match(CSS, /width: 40px; height: 22px; border-radius: var\(--radius-pill\);/);
  // the glyph is the chevron alone: one polyline, no <line> stem
  assert.match(RENDER, /jumpBtn\.innerHTML = [^;]*<polyline points="6 9\.5 12 15\.5 18 9\.5"\/><\/svg>/);
  assert.doesNotMatch(RENDER, /jumpBtn\.innerHTML = [^;]*<line /);
});

// ── executed replica: visibility + click + follow ─────────────────────────────────────────────────
type Box = { scrollHeight: number; clientHeight: number; scrollTop: number };
const nearBottom = (c: Box) => c.scrollHeight - c.scrollTop - c.clientHeight < 80;   // render.ts nearBottom
const maxScroll = (c: Box) => Math.max(0, c.scrollHeight - c.clientHeight);
const visible = (c: Box) => c.clientHeight > 0 && c.scrollHeight > c.clientHeight + 2 && !nearBottom(c);
const clickJump = (c: Box, v: { stick: boolean }) => { c.scrollTop = maxScroll(c); v.stick = true; };
const append = (c: Box, growth: number) => {          // appendActive's stick rule
  const stick = c.scrollHeight > c.clientHeight + 2 && nearBottom(c);
  const before = c.scrollTop;
  c.scrollHeight += growth;
  c.scrollTop = stick ? maxScroll(c) : before;
};

test("replica: shown only when overflowing AND off the bottom", () => {
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 600, scrollTop: 4400 }), false, "at bottom");
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 600, scrollTop: 4360 }), false, "inside the 80px band");
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 600, scrollTop: 1000 }), true, "mid-history");
  assert.equal(visible({ scrollHeight: 500, clientHeight: 600, scrollTop: 0 }), false, "no overflow, no chip");
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 0, scrollTop: 0 }), false, "hidden pane");
});

test("replica: click lands the bottom, hides the chip, and the next appends keep descending", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 1000 };
  const v = { stick: false };
  assert.equal(visible(c), true);
  clickJump(c, v);
  assert.equal(c.scrollTop, 4400);
  assert.equal(v.stick, true);
  assert.equal(visible(c), false, "at the bottom the chip is gone");
  append(c, 300);
  assert.equal(c.scrollTop, 4700, "follow mode engaged — the append descended");
  assert.equal(visible(c), false, "still at the bottom, still no chip");
});

test("replica: scrolling back up disengages follow and re-shows the chip; appends then stay put", () => {
  const c: Box = { scrollHeight: 5300, clientHeight: 600, scrollTop: 4700 };
  c.scrollTop = 3000;                                  // the user scrolls up to read
  assert.equal(visible(c), true, "off the bottom — the chip is back");
  append(c, 300);
  assert.equal(c.scrollTop, 3000, "reading position preserved exactly — the never-yank rule holds");
  assert.equal(visible(c), true);
});
