// The jump-to-newest chip (the user 2026-08-31): scrolled-up reading leaves follow mode — and the
// send gate keeps it that way — so a floating ↓ at the transcript's bottom-center is the deliberate
// way back: click = snap to the bottom + re-engage follow (today's at-bottom behavior). Visibility
// reads the SAME atBottom read the stick rule uses — one definition, the TRUE bottom (T262c: the send
// gate keeps its own wider 80 px band, nearBottomForSend, so the chevron appears the moment the reader
// leaves the bottom rather than 80 px later).
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

test("visibility reads the one atBottom definition, off a passive scroll listener — no polling", () => {
  assert.match(RENDER, /const off = c\.scrollHeight > c\.clientHeight \+ 2 && !atBottom\(c\);/);
  assert.match(RENDER, /c\.addEventListener\("scroll", updateJumpBtn, \{ passive: true \}\);/);
  // a hidden pane measures 0 — the chip must not float over nothing
  assert.match(RENDER, /if \(!c \|\| c\.clientHeight <= 0\) \{ jumpBtn\.hidden = true; return; \}/);
  // appends and tab switches re-read the truth (per-tab correctness rides the restored position)
  assert.match(RENDER, /scheduleRailSticky\(\);\s*\n\s*updateJumpBtn\(\);\s*\/\/ appends can cross the overflow boundary/);
  assert.match(RENDER, /v\.shown = true;\s*\n\s*scheduleRailSticky\(\);\s*\n\s*updateJumpBtn\(\);\s*\/\/ per-tab truth/);
});

test("click snaps to the bottom AND sets the view's stick — the explicit re-entry into follow mode", () => {
  assert.match(RENDER, /writeScroll\(c, c\.scrollHeight, "jump-button", true\);\s*\/\/ the snap IS the acknowledgment/);   // (T262: every #content write rides writeScroll)
  assert.match(RENDER, /if \(v\) \{ v\.stick = true; v\.scrollTop = c\.scrollTop; \}/);
});

test("the send gate stays byte-intact — the chip is the sanctioned mover, sends are not", () => {
  // T187's contract, cross-pinned from this feature so a regression here names both
  assert.match(RENDER, /const wasAtBottom = !!content && nearBottomForSend\(content\);[^\n]*\n\s*appendActive\(\);\s*\n\s*if \(content && wasAtBottom\) writeScroll\(content, content\.scrollHeight, "optimistic-send", true\);/);
});

test("the chip wears the menu-card vocabulary and survives [hidden] against its own display:flex", () => {
  // bottom-LEFT (the user 2026-09-03: the centered pill went unnoticed — they were still clicking
  // into the transcript and hitting End), and the border reads through the menu token: the raw
  // white-alpha it wore vanished on the light page (cream on cream). Since 2026-09-25 the dress is
  // the ONE rule it shares with the jump cluster's capsules (jump-cluster.test.ts pins the rest).
  assert.match(CSS, /#jump-bottom \{\s*\n\s*position: fixed; left: 14px;/);
  assert.match(CSS, /\n#jump-bottom, \.jc-group \{[^}]*border: 1px solid var\(--menu-border\);/);
  assert.match(RENDER, /setTip\(jumpBtn, "go to bottom — then follow new content"\);/,
    "the tooltip says what the user asked it to say, in the one styled tip");
  assert.match(CSS, /#jump-bottom\[hidden\] \{ display: none; \}/);
  // hover: the capsule arrows' wash over the chip's own surface and the glyph lit, never a harsh accent edge
  assert.match(CSS, /#jump-bottom:hover \{ box-shadow: inset 0 0 0 99px var\(--menu-hover\), var\(--shadow-toast\); \}/);
  assert.doesNotMatch(CSS, /#jump-bottom:hover \{[^}]*border-color/);
  assert.match(CSS, /#jump-bottom:hover svg, #jump-bottom:focus-visible svg \{ opacity: 1; \}/);
  assert.match(CSS, /\.jc-btn:focus-visible, #jump-bottom:focus-visible \{ outline: 1\.5px solid var\(--accent\); outline-offset: -2px; \}/, "focus: the arrows' accent ring");
});

test("a short capsule the column's width, with the arrows' own stemless chevron (the user 2026-08-31, then 2026-09-25)", () => {
  // the width and height are the cluster's tokens: the column's width, and a height of its own (the phone's from height, the arrows' touch size)
  assert.match(CSS, /#jump-bottom \{[^}]*width: var\(--jc-w\); height: var\(--jc-chip-h\); cursor: pointer; padding: 0;/);
  assert.match(CSS, /\n#jump-bottom, \.jc-group \{\s*\n\s*border-radius: var\(--radius-pill\);/);
  assert.doesNotMatch(CSS, /#jump-bottom \{[^}]*width: \d+px/, "no pixel width of its own: it can only be the column's");
  assert.doesNotMatch(CSS, /@media \(pointer: coarse\) \{ #jump-bottom \{ width:/, "the old phone rule that widened it is gone");
  // the glyph is the jump cluster's down chevron, from the same builder: one polyline, no <line> stem
  assert.match(RENDER, /jumpBtn\.innerHTML = jcChevron\(false\);/);
  const chev = RENDER.slice(RENDER.indexOf("const jcChevron = "), RENDER.indexOf("const jumpBtn = "));
  assert.ok(chev.length > 0 && RENDER.indexOf("const jcChevron = ") < RENDER.indexOf("jumpBtn.innerHTML"), "the builder is defined before the chip uses it");
  assert.match(chev, /\(up \? "6 14\.5 12 8\.5 18 14\.5" : "6 9\.5 12 15\.5 18 9\.5"\)/);
  assert.match(chev, /stroke-width="2"/);
  assert.doesNotMatch(chev, /<line /);
});

// ── executed replica: visibility + click + follow ─────────────────────────────────────────────────
type Box = { scrollHeight: number; clientHeight: number; scrollTop: number };
const atBottom = (c: Box) => c.scrollHeight - c.scrollTop - c.clientHeight <= 2;   // render.ts atBottom (T262c: the true bottom)
const maxScroll = (c: Box) => Math.max(0, c.scrollHeight - c.clientHeight);
const visible = (c: Box) => c.clientHeight > 0 && c.scrollHeight > c.clientHeight + 2 && !atBottom(c);
const clickJump = (c: Box, v: { stick: boolean }) => { c.scrollTop = maxScroll(c); v.stick = true; };
const append = (c: Box, growth: number) => {          // appendActive's stick rule
  const stick = c.scrollHeight > c.clientHeight + 2 && atBottom(c);
  const before = c.scrollTop;
  c.scrollHeight += growth;
  c.scrollTop = stick ? maxScroll(c) : before;
};

test("replica: shown only when overflowing AND off the bottom", () => {
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 600, scrollTop: 4400 }), false, "at bottom");
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 600, scrollTop: 4398 }), false, "2 px of sub-pixel slack is the bottom");
  assert.equal(visible({ scrollHeight: 5000, clientHeight: 600, scrollTop: 4360 }), true, "40 px up: the old band hid the chip here (T262c), now it shows at once");
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
