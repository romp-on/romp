// The scroll journal is COMPLETE (T262j, the user 2026-09-08): an unwritten move of the chat transcript landed at a
// fixed value per tab, 64 px above the bottom, with scrollHeight and clientHeight unchanged and no scrollwrite row,
// and the journal could not name the mover because four movers still bypassed the one write helper: the wheel over a
// scrollbar notch (scrollBy), the arrow keys (scrollBy), the deep-link land and its re-alignments (scrollIntoView), and
// the sentence land (scrollIntoView). Every mover of #content now goes through writeScroll under its own writer name,
// scrollBy and scrollIntoView expressed as the scrollTop writes they are; a virtualization spacer re-size files a
// "spacer" row (a top re-estimate paired with a bottom one leaves scrollHeight unchanged yet moves everything under it,
// and Chrome's anchoring answers with an unwritten move); and the per-minute cap is configurable from localStorage so a
// laptop capturing does not lose the rest of the minute after a trackpad burst. Pure pieces executed; wiring pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { readScrollDiagCap, spacerRow, SCROLL_DIAG_CAP_KEY, SCROLL_DIAG_CAP_PER_MINUTE } from "./scroll-write";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const SID = "11111111-2222-4333-8444-000000000201";

test("the cap: the default, or a positive integer from localStorage; anything else is the default", () => {
  assert.equal(SCROLL_DIAG_CAP_KEY, "romp:scrollDiagCap");
  assert.equal(readScrollDiagCap(() => null), SCROLL_DIAG_CAP_PER_MINUTE);
  assert.equal(readScrollDiagCap((k) => (k === SCROLL_DIAG_CAP_KEY ? "600" : null)), 600);
  assert.equal(readScrollDiagCap(() => "0"), SCROLL_DIAG_CAP_PER_MINUTE);
  assert.equal(readScrollDiagCap(() => "-5"), SCROLL_DIAG_CAP_PER_MINUTE);
  assert.equal(readScrollDiagCap(() => "lots"), SCROLL_DIAG_CAP_PER_MINUTE);
  assert.equal(readScrollDiagCap(() => "12.5"), SCROLL_DIAG_CAP_PER_MINUTE);
  assert.equal(readScrollDiagCap(() => { throw new Error("no storage"); }), SCROLL_DIAG_CAP_PER_MINUTE, "a storage that throws is the default");
});

test("the spacer row carries both spacers' before/after and their deltas", () => {
  assert.deepEqual(spacerRow(SID, 1000, 936, 0, 64, 9114, 902), { sid: SID, top: [1000, 936], bot: [0, 64], dTop: -64, dBot: 64, sh: 9114, ch: 902 });
});

test("render.ts: every mover of #content is a writeScroll — scrollBy and scrollIntoView on it are gone", () => {
  assert.match(RENDER, /function scrollContentBy\(content: HTMLElement, dy: number, writer: string\): void \{\s*\n\s*writeScroll\(content, content\.scrollTop \+ dy, writer\);/);
  assert.match(RENDER, /function scrollElInto\(content: HTMLElement, el: Element, block: "start" \| "center" \| "nearest", writer: string\): void \{/);
  assert.doesNotMatch(RENDER, /\bc\.scrollBy\(|content\.scrollBy\(/, "no scrollBy on the transcript scroller");
  // the movers, each under its name
  assert.match(RENDER, /scrollContentBy\(c, e\.deltaY \* k, "wheel-scale"\);/, "the wheel over a scrollbar notch");
  assert.match(RENDER, /scrollContentBy\(content, e\.key === "ArrowDown" \? NAV_SCROLL_STEP : -NAV_SCROLL_STEP, "key-nav"\);/, "the arrow keys");
  // the sentence land is the deep-link land's own (T386): the quoted span, or the turn's text atom, is what the one write aligns on
  assert.match(RENDER, /landOn\(target, uuid, quoteEl \?\? firstTextAtomBelow\(target\), quote\);/, "the sentence land");
  assert.doesNotMatch(RENDER, /scrollElInto\(content0, el0, "center", "land-on"\);/, "no second write from the highlight");
  assert.match(RENDER, /const land = \(writer: string\) => \{ const c = document\.getElementById\("content"\); if \(c\) scrollElInto\(c, at, "start", writer\); \};\s*\n\s*land\("land-on"\);/, "the deep-link land; its re-alignments are the settle rule's (landing-settle.ts)");
  // a message's own `#` link (the click delegate): inside the transcript the move is a writeScroll; a target that stands
  // outside #content (a comment popover's reply, found through userContentTarget's document fallback) scrolls its own
  // container the browser's way, and the #content arm is taken first
  assert.match(RENDER, /if \(cont && cont\.contains\(target\)\) scrollElInto\(cont, target, "start", "section-link"\);\s*\n\s*else target\.scrollIntoView\(\{ block: "start" \}\);/, "the section link");
  // the scrollIntoView calls that remain are on OTHER scrollers (the tab strip, picker rows, the slash popup, the @-mention
  // card, the awaiting box, and the section link's else arm above, which the #content test keeps off the transcript)
  const rest = RENDER.split("\n").filter((l) => /scrollIntoView\(/.test(l));
  assert.equal(rest.length, 6, "six scrollIntoView calls remain, none on a #content child: " + rest.map((l) => l.trim().slice(0, 60)).join(" | "));
  for (const l of rest) if (!/else target\.scrollIntoView/.test(l)) assert.doesNotMatch(l, /target|el0|realign/, l);
});

test("render.ts: a spacer re-size of the active view files a spacer row; the cap comes from localStorage", () => {
  assert.match(RENDER, /if \(\(topAfter !== topBefore \|\| botAfter !== botBefore\) && activeId && views\.get\(activeId\) === v\) \{\s*\n\s*const content = document\.getElementById\("content"\);\s*\n\s*scrollDiagRow\("spacer", spacerRow\(activeId, topBefore, topAfter, botBefore, botAfter,/);
  assert.match(RENDER, /const scrollDiagCap = readScrollDiagCap\(\(k\) => \{ try \{ return localStorage\.getItem\(k\); \} catch \{ return null; \} \}\);\s*\n\s*const scrollDiag = new ScrollDiagBudget\(scrollDiagCap\);/);
  assert.match(RENDER, /function scrollDiagRow\(kind: "scrollwrite" \| "scrollgesture" \| "tailchange" \| "spacer" \| "tailmut" \| "unitchange" \| "windowask", data: any\): void \{/);
  assert.match(RENDER, /data: \{ sid: activeId \|\| "", perMinute: scrollDiagCap \} \}/, "the capped row says which cap");
});
