// Raw-color census for the four small standalone sheets (gear/strip/sessions-pane/timeline-pane) —
// pins the token paydown of 2026-08-28 so raw literals cannot creep back in. Each sheet resolves its
// shared colors through var(--token, <literal>) with the literal as the standalone fallback; a hex
// INSIDE a var() fallback is paid down, a bare hex is not. The counts below are today's post-paydown
// numbers pinned as MAXIMUMS: they may only go DOWN. If this fails, resolve the new color through an
// existing token (styles.css :root / TOKENS the coordinator defines) instead of raising the ceiling —
// raise it only for a genuinely new semantic one-off (a status color, a session identity swatch), and
// say why in the same commit.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");

// hexes outside comments and outside var() fallbacks; (?!-) keeps id selectors like #feed-head out
const rawHexes = (css: string) => {
  // a body.theme-light block's hexes are the theme's token DEFINITIONS (its one sanctioned home),
  // not debt — strip those blocks before counting (2026-08-28, when the light theme landed)
  // strip the token BLOCK and any body.theme-light-scoped rule (single-line overrides included):
  // theme definitions are the light theme's one sanctioned home for values, not debt
  css = css.replace(/body\.theme-light \{[\s\S]*?\n\}/g, "").replace(/body\.theme-light [^{]*\{[^}]*\}/g, "");
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/var\([^)]*\)/g, "V");
  return stripped.match(/#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b(?!-)/g) || [];
};
const rawDims = (css: string) => {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/var\([^)]*\)/g, "V");
  return stripped.match(/rgba\(\s*0,\s*0,\s*0,\s*0?\.55\s*\)/g) || [];
};

// remaining literals are deliberate: semantic one-offs (status colors, identity blues, #fff full-white
// hovers, one-off shadow alphas) and the two rules css-vocab.test.ts pins as literals in gear.css.
// EXACT counts, not ceilings (PR #763 item 7: a <= pin with slack lets new raw hexes arrive
// unnoticed) — a count that moves in EITHER direction is a deliberate change to name here.
const EXACT: Record<string, number> = {
  "gear.css": 13,   // T226's shadows-onto-var(--shadow-menu) (14) plus the .ra-li legend joining --text-soft (2026-08-31)
  "strip.css": 8,
  "fleet-pane.css": 9,
  "timeline-pane.css": 10,
};

for (const [file, exact] of Object.entries(EXACT)) {
  test(`${file}: raw hex count is pinned EXACTLY (${exact})`, () => {
    const hexes = rawHexes(read(file));
    assert.equal(hexes.length, exact,
      `${file} has ${hexes.length} raw hexes (pinned ${exact}): ${hexes.join(" ")} — resolve new colors through a token, or re-pin deliberately`);
  });
}

test("no literal modal dims outside var() fallbacks — except gear's pinned #ranalytics-back", () => {
  // css-vocab.test.ts pins #ranalytics-back's `background: rgba(0, 0, 0, 0.55)` (and #ranalytics's
  // shadow) as literals, so gear.css keeps exactly that one; every other dim in these sheets
  // resolves through var(--overlay-dim, ...) with the literal as its standalone fallback.
  assert.equal(rawDims(read("gear.css")).length, 1, "gear.css: only the vocab-pinned analytics dim");
  for (const f of ["strip.css", "fleet-pane.css", "timeline-pane.css"]) {
    assert.equal(rawDims(read(f)).length, 0, f + " has no bare 0.55 dim literal");
  }
});
