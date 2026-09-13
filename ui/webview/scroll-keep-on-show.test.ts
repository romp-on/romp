// T249 (the user 2026-09-07, a screen recording): on a session served through the relay from an attached
// kernel, the chat pane snapped the reader's scroll back to an earlier position ~3 s after they scrolled to
// the bottom — the exact spot the tab had been left at, a blue bubble at the top of the viewport.
//
// The writer: landActive's closing rule, `if (!v.shown || v.stick) bottom else v.scrollTop`, fed by a saved
// spot nothing updated while the reader scrolled (only a tab switch's leaving-tab save, the jump button,
// the resize compensation and the nav trail wrote it). Any full show of an already-shown tab — a
// fork/first-build frame, a settings rerender, a revive failure, a dismissal's fallback — landed the reader
// on that stale spot. Fix: (1) the #content scroll listener keeps the active view's saved spot and
// follow-mode current; (2) a show of a view already on screen anchors the reader's place across its rebuild
// like the live append does, and the big-view re-collapse-to-tail is a switch-only rule. Review folds
// (2026-09-08): a DEFERRED build's reveal clamps the scroll and fires a scroll event before the build lands,
// so nothing is recorded while a build is pending (else a compact-mode switch to a tab with an unbuilt update
// landed at the bottom, not its saved spot); rerenderAll captures the reader's anchor before it empties the
// DOM and hands it to showActive; a live seek counts as navigation. Executed rules + render.ts wiring pins,
// red on the previous render.ts. Synthetic values only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { followReader, landSpot, keepPlaceAcrossShow, reshowStick, atBottomDist, followTailShrink, followBoxBelow, type KeepView } from "./scroll-keep";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the recording: a tab left scrolled up, the reader scrolls to the bottom, a full show lands at the bottom — not the stale spot", () => {
  const v: KeepView = { scrollTop: 1180, stick: false, shown: true };   // saved when the tab was last LEFT: a bubble at the top
  assert.equal(landSpot(v), 1180, "before any gesture a re-show lands on the saved spot (unchanged rule)");
  followReader(v, 9400, true);                                          // the user's smooth scroll to the bottom
  assert.equal(landSpot(v), "bottom", "a full show now follows the reader to the bottom");
  followReader(v, 3020, false);                                         // they scroll back up to read
  assert.equal(landSpot(v), 3020, "…and lands exactly where they are reading");
});

test("followReader touches only a shown view, and a missing view is a no-op", () => {
  const fresh: KeepView = { scrollTop: 0, stick: true, shown: false };
  followReader(fresh, 500, false);
  assert.deepEqual(fresh, { scrollTop: 0, stick: true, shown: false }, "an unshown view's first land still goes to the bottom");
  assert.doesNotThrow(() => followReader(null, 1, true));
  assert.doesNotThrow(() => followReader(undefined, 1, true));
});

test("a deferred build's reveal clamp is NOT the reader: nothing is recorded while a build is pending, the land's own scroll is", () => {
  // the compact-mode switch the review traced: B was left mid-read (3000, not following); A's long scroll
  // reveals B's stale, shorter DOM → the browser clamps to B's bottom and fires a scroll event BEFORE the
  // deferred build lands. Recorded, that read as follow-mode and the land went to the bottom.
  const b: KeepView = { scrollTop: 3000, stick: false, shown: true };
  followReader(b, 9000, true, /* building */ true);
  assert.deepEqual(b, { scrollTop: 3000, stick: false, shown: true }, "the clamp left the saved spot alone");
  assert.equal(landSpot(b), 3000, "the deferred land goes where the tab was left");
  followReader(b, 3000, false, false);                                 // the land's own scroll event, build done
  assert.equal(landSpot(b), 3000);
  followReader(b, 9000, true, false);                                  // the reader really scrolls to the bottom later
  assert.equal(landSpot(b), "bottom");
});

test("keepPlaceAcrossShow: only a displayed, visible, already-shown view with no navigation pending", () => {
  const v: KeepView = { scrollTop: 300, stick: false, shown: true };
  assert.equal(keepPlaceAcrossShow(v, true, true, false), true, "a re-show of the view on screen");
  assert.equal(keepPlaceAcrossShow(v, false, true, false), false, "a tab SWITCH: the entering view is not displayed yet — its explicit spot semantics stand");
  assert.equal(keepPlaceAcrossShow(v, true, false, false), false, "a hidden pane has nothing to keep");
  assert.equal(keepPlaceAcrossShow(v, true, true, true), false, "a deep link / moment lands where it says");
  assert.equal(keepPlaceAcrossShow({ ...v, shown: false }, true, true, false), false, "a first show lands at the bottom");
});

test("render.ts: the reshow decision counts a live durable seek for the tab as navigation, and rerenderAll hands showActive the anchor it captured BEFORE emptying the DOM", () => {
  assert.match(RENDER, /const navigating = !!pendingAnchor \|\| pendingAnchorT != null \|\| \(!!seek && seek\.sid === activeId\);/);
  assert.match(RENDER, /^function showActive\(keep\?: \{ uuid: string; y: number \} \| null\) \{/m);
  assert.match(RENDER, /const keepAnchor = reshow \? \(keep !== undefined \? keep : \(!atBottom\(content\) \? captureScrollAnchor\(content, v\) : null\)\) : null;[^\n]*/);
  const ra = RENDER.match(/^function rerenderAll\(\): void \{([\s\S]*?)\n\}/m);
  assert.ok(ra, "rerenderAll");
  const body = ra![1];
  const cap = body.indexOf("captureScrollAnchor(content!, av!)");
  const clear = body.indexOf("v.el.removeChild(v.el.firstChild)");
  const show = body.indexOf("showActive(keep);");
  assert.ok(cap >= 0 && clear >= 0 && show >= 0, "capture, clear and show are all there");
  assert.ok(cap < clear && clear < show, "captured before the clear, handed to the show");
});

test("render.ts: the #content scroll listener keeps the active view's saved spot current (passive, no timer)", () => {
  assert.match(RENDER, /import \{ followReader, keepPlaceAcrossShow, followTail, atBottomDist, followBoxBelow, followTailShrink, reshowStick \} from "\.\/scroll-keep";/);   // + followTail (T262: follow only on new content)
  // …and stands down while a deferred build is pending: the reveal's clamp fires a scroll event before the land
  assert.match(RENDER, /c\.addEventListener\("scroll", \(\) => \{\n\s*if \(c\.clientHeight <= 0\) return;\n\s*followReader\(activeId \? views\.get\(activeId\) : null, c\.scrollTop, atBottom\(c\), pendingBuildRaf != null\);\n(?:.*\n){0,12}?\s*\}, \{ passive: true \}\);/);   // up to twelve lines follow the follow rule (three of them the settle's gesture-evidence note, T386 round two): the gesture classification, its mark on the view for the edge check (T366), the gesture row and the sh/ch note
  // landActive's landing rule itself is unchanged — its INPUT is what the fix repairs
  assert.match(RENDER, /if \(!v\.shown \|\| v\.stick\) writeScroll\(content, content\.scrollHeight, "land-bottom", true\);\n\s*else writeScroll\(content, v\.scrollTop, "land-saved"\);/);   // (T262: every #content write rides writeScroll)
});

test("render.ts: showActive keeps the reader's place across a re-show of the view already on screen, on both build paths", () => {
  const m = RENDER.match(/^function showActive\(keep\?: \{ uuid: string; y: number \} \| null\) \{([\s\S]*?)\n\}/m);
  assert.ok(m, "showActive");
  const body = m![1];
  assert.match(body, /const reshow = keepPlaceAcrossShow\(v, v\.el\.style\.display !== "none", content\.clientHeight > 0, navigating\);/);
  assert.match(body, /const keepAnchor = reshow \? \(keep !== undefined \? keep : \(!atBottom\(content\) \? captureScrollAnchor\(content, v\) : null\)\) : null;[^\n]*/, "captured BEFORE the rebuild, like appendActive — or handed in by a caller that had to empty the DOM first");
  const restores = body.match(/if \(keepAnchor(?: && cc)?\) keepPlaceAcrossWindow\(/g) || [];
  assert.equal(restores.length, 2, "restored after landActive on the light path AND inside the deferred heavy build (through the window-aware keep, T262l)");
  // the big-view re-collapse to the tail is a SWITCH rule: a re-show of the view on screen must not snap it to the bottom
  assert.match(body, /if \(!reshow && !pendingAnchor && pendingAnchorT == null\n\s*&& v\.el\.querySelectorAll\("\.turn"\)\.length > WINDOW_CAP\)/);
});

// T262 (the user 2026-09-08/09): the chat snapped UP the moment they scrolled to the bottom, in every column.
// The journal's rows for every such jump: writer `land-saved`, scrollTop + clientHeight == scrollHeight (the reader
// at the true bottom), the recorded follow flag off — a wheel gesture that ended on the bottom while a deferred
// build was pending was not recorded (the clamp rule above), so `stick` stayed false and `scrollTop` a screen
// above, and the next full show landed there. At a re-show the DOM is the truth: a reader at the true bottom
// follows it, whatever the flag last said.
test("the journal's case: recorded follow off, the reader at the true bottom, a re-show lands at the bottom — never the stale spot", () => {
  const v: KeepView = { scrollTop: 8483, stick: false, shown: true };   // the stale spot: 1110 px above the bottom the reader sat at
  assert.equal(landSpot(v), 8483, "without the rule the full show lands on the stale spot (the snap)");
  v.stick = reshowStick(v.stick, true);                                  // showActive, at the re-show, with atBottom(content) true
  assert.equal(landSpot(v), "bottom");
});

test("reshowStick only ever turns following ON: off the bottom the recorded flag stands, so a scrolled-up reader keeps their place", () => {
  assert.equal(reshowStick(false, true), true, "at the true bottom → follow, whatever was recorded");
  assert.equal(reshowStick(true, true), true);
  assert.equal(reshowStick(true, false), true, "a recorded follow is not revoked by a rebuild that grew the tail under the reader");
  assert.equal(reshowStick(false, false), false, "scrolled up and not following → the saved spot / the anchor, as before");
});

test("render.ts: showActive re-derives follow mode from the true bottom at the re-show decision, before the rebuild", () => {
  assert.match(RENDER, /const reshow = keepPlaceAcrossShow\(v, v\.el\.style\.display !== "none", content\.clientHeight > 0, navigating\);\n(?:\s*\/\/[^\n]*\n)*\s*if \(reshow && keep === undefined\) v\.stick = reshowStick\(v\.stick, atBottom\(content\)\);\n\s*const keepAnchor = reshow \?/,
    "the flag is re-derived between the reshow decision and the keep-anchor capture (which reads the same true bottom), for a caller that left the DOM in place");
  assert.match(RENDER, /import \{[^}]*reshowStick[^}]*\} from "\.\/scroll-keep";/);
});

// Review find on the rule above (2026-09-10): rerenderAll (a settings change) EMPTIES every view before it calls
// showActive, and an emptied scroller reads as the bottom: reading scrollHeight forces layout, the browser clamps
// scrollTop to the new maximum (0 once what is left of #content fits), so the distance to the bottom is 0. Read
// there, the rule put a scrolled-up reader into follow mode, and the same frame's observers (the view's tail-shrink
// rule when the rebuilt transcript is shorter, a compact toggle; the boxes-below rule when a box under the scroller
// changed height, the dense-chrome toggle) wrote them to the bottom over the anchor the re-show had just restored:
// the snap the T249 fix removed, back on the settings-change path. So rerenderAll reads the true bottom BEFORE the
// clear, where it already reads it for the anchor, and showActive leaves the flag alone for a caller that handed
// the keep in. The scroller is the replica at-bottom.test.ts uses (no jsdom for the chat renderer).
type Box = { scrollHeight: number; clientHeight: number; scrollTop: number };
const atBottom = (c: Box) => atBottomDist(c.scrollHeight - c.scrollTop - c.clientHeight);   // render.ts atBottom
function emptyViews(c: Box): void {                                                       // rerenderAll's removeChild loop, as the browser sees it
  c.scrollHeight = c.clientHeight;                                                          // what is left of #content fits
  c.scrollTop = Math.min(c.scrollTop, Math.max(0, c.scrollHeight - c.clientHeight));        // the clamp to the new maximum
}

test("the emptied scroller reads as the bottom: a scrolled-up reader keeps follow mode OFF only when the rule reads the DOM before the clear", () => {
  const c: Box = { scrollHeight: 12000, clientHeight: 800, scrollTop: 8483 };   // 2717 px above the bottom, reading
  const v: KeepView = { scrollTop: 8483, stick: false, shown: true };
  const before = reshowStick(v.stick, atBottom(c));                              // rerenderAll, before the clear
  assert.equal(before, false, "off the bottom the recorded flag stands");
  emptyViews(c);                                                                  // every view emptied; the browser clamps
  assert.equal(atBottom(c), true, "the emptied scroller is at its bottom, wherever the reader was");
  assert.equal(reshowStick(v.stick, atBottom(c)), true, "read after the clear, the rule puts the scrolled-up reader into follow mode");
  // what that flag feeds in the same frame, before any scroll event can correct it:
  assert.equal(followTailShrink(true, 9800 - 12000), true, "a shorter rebuilt transcript writes them to the bottom");
  assert.equal(followBoxBelow(true, 36), true, "a box below the scroller changing height writes them to the bottom");
  v.stick = before;                                                               // the flag as rerenderAll now records it
  assert.equal(followTailShrink(v.stick, 9800 - 12000), false, "read before the clear: the observers leave the reader alone");
  assert.equal(followBoxBelow(v.stick, 36), false);
  assert.equal(landSpot(v), 8483, "and the land goes to the saved spot the anchor restore then refines");
});

test("render.ts: rerenderAll re-derives follow mode from the true bottom BEFORE the clear, and showActive leaves the flag alone for a caller that handed the keep in", () => {
  const ra = RENDER.match(/^function rerenderAll\(\): void \{([\s\S]*?)\n\}/m);
  assert.ok(ra, "rerenderAll");
  const body = ra![1];
  assert.match(body, /const bottom = live && atBottom\(content!\);\n(?:\s*\/\/[^\n]*\n)*\s*if \(live\) av!\.stick = reshowStick\(av!\.stick, bottom\);\n\s*const keep = live && !bottom \? captureScrollAnchor\(content!, av!\) : null;/,
    "one read of the true bottom drives both the follow flag and the keep");
  const stick = body.indexOf("av!.stick = reshowStick(");
  const clearAt = body.indexOf("v.el.removeChild(v.el.firstChild)");
  assert.ok(stick >= 0 && clearAt >= 0 && stick < clearAt, "the flag is re-derived before the DOM is emptied");
  assert.match(RENDER, /if \(reshow && keep === undefined\) v\.stick = reshowStick\(v\.stick, atBottom\(content\)\);/,
    "showActive reads the DOM only when no caller emptied it first (the keepAnchor ternary's own gate)");
  assert.doesNotMatch(RENDER, /if \(reshow\) v\.stick = reshowStick\(/, "the ungated read saw the emptied scroller as the bottom");
});
