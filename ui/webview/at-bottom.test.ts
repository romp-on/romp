// Follow mode and the go-to-bottom chip read the TRUE bottom; the 80 px band is the send reveal's alone
// (T262c, the user 2026-09-08, who wheeled up from the tail of a working session, saw no chevron for a
// stretch, and inside that stretch had the view snap back to the bottom; they expected the chevron the
// moment they left the bottom). One 80 px band (render.ts's old nearBottom) drove BOTH follow mode and the
// chip, so for the first 80 px of a deliberate scroll-up the reader still counted as at the bottom: no chip,
// and every content-height change of a working session re-pinned them through followTail's height-changed
// branch — a snapback with no predictable interval, because it fired whenever the session appended while
// they were inside the band. Now: atBottom (2 px, the tolerance followTail already used) for every
// follow-mode decision and the chip; nearBottomForSend (80 px) only where the user's own send reveals itself.
// render.ts has import-time DOM side effects → the pure tolerance executes for real, the decisions run as a
// replica pinned to source, and every call site is pinned to the helper its intent belongs to.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { atBottomDist, AT_BOTTOM_PX, followTail } from "./scroll-keep";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the tolerance is 2 px: sub-pixel slack is the bottom, three pixels is not", () => {
  assert.equal(AT_BOTTOM_PX, 2);
  assert.equal(atBottomDist(0), true);
  assert.equal(atBottomDist(2), true, "fractional scroll positions leave up to a pixel or two of slack");
  assert.equal(atBottomDist(3), false);
  assert.equal(atBottomDist(30), false, "30 px up is a reader who left the bottom");
  assert.equal(atBottomDist(79), false, "…and so is the old band's edge");
});

// ── executed replica of appendActive's stick + the chip, pinned to source below ──────────────────
type Box = { scrollHeight: number; clientHeight: number; scrollTop: number };
const dist = (c: Box) => c.scrollHeight - c.scrollTop - c.clientHeight;
const atBottom = (c: Box) => atBottomDist(dist(c));                       // render.ts atBottom
const maxScroll = (c: Box) => Math.max(0, c.scrollHeight - c.clientHeight);
const chipShown = (c: Box) => c.clientHeight > 0 && c.scrollHeight > c.clientHeight + 2 && !atBottom(c);   // updateJumpBtn
function append(c: Box, growth: number): void {                           // appendActive
  const stick = c.scrollHeight > c.clientHeight + 2 && atBottom(c);
  const before = c.scrollTop, heightBefore = c.scrollHeight, distBefore = dist(c);
  c.scrollHeight += growth;
  if (stick && followTail(distBefore, heightBefore, c.scrollHeight)) c.scrollTop = maxScroll(c);
  else c.scrollTop = before;
}

test("a reader 30 px above the bottom is NOT re-pinned when the content grows, and the chip is shown", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 5000 - 600 - 30 };
  assert.equal(chipShown(c), true, "the chevron shows the moment they are more than 2 px off the bottom");
  append(c, 40);                                                          // a streamed line lands
  assert.equal(c.scrollTop, 5000 - 600 - 30, "the view did not move");
  append(c, 300);                                                         // a whole card lands
  assert.equal(c.scrollTop, 5000 - 600 - 30, "still not moved: only the true bottom follows");
  assert.equal(chipShown(c), true);
});

test("a reader at the true bottom follows every append; scrolling 3 px up leaves follow mode at once", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 4400 };
  assert.equal(chipShown(c), false);
  append(c, 40);
  assert.equal(c.scrollTop, 4440, "followed");
  c.scrollTop -= 3;                                                       // the smallest deliberate wheel step
  assert.equal(chipShown(c), true, "chevron up immediately");
  append(c, 40);
  assert.equal(c.scrollTop, 4437, "not re-pinned");
  c.scrollTop = maxScroll(c);                                             // back to the true bottom (or the chip's click)
  assert.equal(chipShown(c), false);
  append(c, 40);
  assert.equal(c.scrollTop, maxScroll(c), "follow mode re-engaged only by reaching the true bottom");
});

// ── source pins: two helpers, and every call site assigned to one by intent ─────────────────────
test("render.ts defines atBottom on the shared tolerance and nearBottomForSend on the 80 px band; nearBottom is gone", () => {
  assert.match(RENDER, /function atBottom\(c: HTMLElement\): boolean \{\s*\n\s*return atBottomDist\(c\.scrollHeight - c\.scrollTop - c\.clientHeight\);/);
  assert.match(RENDER, /function nearBottomForSend\(c: HTMLElement\): boolean \{\s*\n\s*return c\.scrollHeight - c\.scrollTop - c\.clientHeight < 80;/);
  assert.match(RENDER, /import \{[^}]*\batBottomDist\b[^}]*\} from "\.\/scroll-keep";/);
  assert.doesNotMatch(RENDER, /\bnearBottom\(/, "the one-band helper must not come back under its old name");
});

test("follow mode and the chip read atBottom at every site", () => {
  const follow = [
    /const stick = content\.scrollHeight > content\.clientHeight \+ 2 && atBottom\(content\);/,          // appendActive
    /const off = c\.scrollHeight > c\.clientHeight \+ 2 && !atBottom\(c\);/,                             // updateJumpBtn
    /followReader\(activeId \? views\.get\(activeId\) : null, c\.scrollTop, atBottom\(c\), pendingBuildRaf != null\);/,   // the scroll record
    /cur\.stick = atBottom\(content\);/,                                                                  // the leaving tab's save
    /_wasNear = !_scrollContent \|\| !_v0 \|\| !_v0\.shown \|\| atBottom\(_scrollContent\);/,             // the rebuild branch
    /const bottom = live && atBottom\(content!\);/,                                                          // rerenderAll's one read, before the clear: the re-show follow flag and the keep
    /\(!atBottom\(content\) \? captureScrollAnchor\(content, v\) : null\)\) : null;/,                       // showActive's keep
    /content\.clientHeight > 0 && !atBottom\(content\)\) \{\s*\n\s*writeScroll\(content, content\.scrollTop \+ \(h - lastH\), "box-resize"\);/,   // box-resize compensation
    /stick = !!content && atBottom\(content\);/,                                                          // tab-strip drag
    /const wasAtBottom = !!contentX && contentX\.scrollHeight > contentX\.clientHeight \+ 2 && atBottom\(contentX\);/,   // the ✕ on a pending bubble (T262h)
    /unitChangeRow\(id, c\.dh, c\.cls, c\.fromTail, view3\.stick, atBottom\(content\), content\.scrollHeight, content\.clientHeight\)/,   // the unit-change row's measured bottom (T262n)
    /unitChangeRow\(activeId \|\| "", dh, cls, BOX_FROM_TAIL, v\.stick, atBottom\(c\), c\.scrollHeight, c\.clientHeight\)/,   // the scroller's boxes outside the thread (T262n follow-up)
    /if \(c && v\) v\.stick = atBottom\(c\); \}/,                                                             // a deep-link landing ends follow mode unless it put the reader at the bottom (T386)
  ];
  for (const re of follow) assert.match(RENDER, re, String(re));
  assert.equal((RENDER.match(/\batBottom\(/g) || []).length, 16, "fifteen call sites plus the definition (the re-show follow rule reads the same true bottom, T262 2026-09-09; the window ask's diagnostic row reads it too, T366; the landing's follow-mode end, T386)");
});

test("only the user's own send reveal keeps the 80 px band", () => {
  assert.match(RENDER, /const wasAtBottom = !!content && nearBottomForSend\(content\);[^\n]*\n\s*appendActive\(\);\s*\n\s*if \(content && wasAtBottom\) writeScroll\(content, content\.scrollHeight, "optimistic-send", true\);/);
  assert.equal((RENDER.match(/\bnearBottomForSend\(/g) || []).length, 2, "one call site plus the definition");
});

test("the tolerance is one constant: followTail reads it too", () => {
  const KEEP = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "scroll-keep.ts"), "utf8");
  assert.match(KEEP, /export const AT_BOTTOM_PX = 2;/);
  assert.match(KEEP, /export function followTail\(distBefore: number, heightBefore: number, heightAfter: number\): boolean \{\s*\n\s*if \(atBottomDist\(distBefore\)\) return true;/);
});
