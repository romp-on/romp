// ONE hover language on the timeline (the user 2026-07-17): cross-hover focus — a feed-card hover, the
// DAG journey, or a feed-modal line hover — draws EXACTLY like the native glyph hover: the element
// thickens/grows in its OWN color. The old thick-white-border language (DAG_HL/DAG_W outlines around
// bars, dots, and connector casings) is gone. Source pins on ui/romp-timeline-view.js.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("the white focus-border language is gone entirely", () => {
  assert.doesNotMatch(SRC, /const DAG_HL/);
  assert.doesNotMatch(SRC, /const DAG_W/);
  assert.doesNotMatch(SRC, /stroke: DAG_HL/);
});

test("a cross-lit bar is drawn GROWN + opaque in its own color, and a local hover restores that state", () => {
  assert.match(SRC, /const lit = barLit\(t, dagOrHover\);/);
  assert.match(SRC, /const bh = lit \? eh : BAR_H;/, "lit → the same eh the native mouseenter grow uses");
  assert.match(SRC, /height: bh, rx: bh \/ 2, fill: s\.color, opacity: lit \? 1 : 0\.9/);
  assert.match(SRC, /grow\(bh\); bar\.setAttribute\('opacity', lit \? '1' : '0\.9'\)/, "mouseleave restores the DRAWN state, not the unlit one");
});

test("a cross-lit connector lights via its native own-color highlight overlay, not a white casing", () => {
  assert.match(SRC, /const msgLit = dagOrHoverMsg\(mm\.id\);/);
  assert.match(SRC, /opacity: msgLit \? 0\.95 : 0/, "the hl overlay starts lit");
  // overlap-hover (2026-08-24): enters/leaves route through the shared set machinery; the lit
  // restore lives in msgSetLight's one formula, which every covered unit passes through
  assert.match(SRC, /msgSetLight\(u\.hoverSet \|\| \[i\], false\)/, "mouseleave restores the whole hovered set");
  assert.match(SRC, /u\.hl\.setAttribute\('opacity', \(on \|\| u\.lit\) \? '0\.95' : '0'\)/,
    "…and the restore formula keeps a cross-lit overlay lit");
});

test("cross-lit dots are drawn grown via dot()'s lit param — arrival and prompt dots both", () => {
  assert.match(SRC, /const dot = \(cx, cy, color, html, onClick, linkedHl, lit, msgI\) =>/);
  assert.match(SRC, /r: lit \? DOT_R \+ 2 : DOT_R/, "lit → the same +2 growth the native hover applies");
  assert.match(SRC, /c\.setAttribute\('r', lit \? DOT_R \+ 2 : DOT_R\)/, "mouseleave restores the lit radius");
  assert.match(SRC, /dot\(x\(landXT\(mm\)\), cy, col, msgHtml\(mm\), msgNav\(mm\), u && u\.hl, dagOrHoverMsg\(mm\.id\), i\)/,
    "the arrival dot carries its message index — it hovers as its whole overlap set");
  assert.match(SRC, /, null, dotLit\(t, dagOrHover\)\);/, "the prompt dot passes its lit state through");
});
