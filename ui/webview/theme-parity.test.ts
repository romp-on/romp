// The light theme's two structural guarantees (2026-08-28), so it cannot rot as features land
// dark-first:
//  1. KEY PARITY — body.theme-light re-declares EVERY custom property the sheet's :root defines
//     (a new token added to :root without a light value fails here, in the same commit).
//  2. CONTRAST — the designated (fg, bg) token pairs clear WCAG in BOTH themes; a feature that
//     adds a pair adds it to PAIRS.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");

function block(css: string, opener: string): string {
  const at = css.indexOf(opener);
  assert.ok(at >= 0, opener + " present");
  // COMMENT-BLIND parsing swallowed tokens whose declarations follow a multi-line comment and
  // corrupted values that carry one inline (PR #763 item 6: --accent/--card-border/--err skipped
  // in silence and the suite stayed green) — strip comments FIRST, always
  return css.slice(at, css.indexOf("\n}", at)).replace(/\/\*[\s\S]*?\*\//g, "");
}
function props(blockText: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of blockText.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/gi)) out.set(m[1], m[2].trim());
  return out;
}

// resolve a declared value to solid RGB over a background: hex directly; var(x, fallback) via the
// fallback (the stand-ins are absent in this static read); rgba composited over the bg
function rgbOf(v: string, bg: [number, number, number]): [number, number, number] | null {
  const hex = v.match(/^#([0-9a-f]{6})$/i);
  if (hex) { const h = hex[1]; return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]; }
  const vr = v.match(/^var\([^,]+,\s*(.+)\)$/);
  if (vr) return rgbOf(vr[1].trim(), bg);
  const ra = v.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$/);
  if (ra) {
    const a = ra[4] === undefined ? 1 : parseFloat(ra[4]);
    return [1, 2, 3].map((i) => Math.round(parseInt(ra[i], 10) * a + bg[i - 1] * (1 - a))) as [number, number, number];
  }
  return null;   // fonts, shadows, sizes — not a color
}
function lum(rgb: [number, number, number]): number {
  const ch = (c: number) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2]);
}
function contrast(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// (fg token, ground token, floor) — 4.5 for reading text, 3 for large/secondary chrome
const PAIRS: Array<[string, string, number]> = [
  ["--fg", "--bg", 4.5],
  ["--dim", "--bg", 4.5],
  ["--fg", "--surface-raised", 4.5],
  ["--accent", "--bg", 3],
  ["--accent-fg", "--accent", 3],
  ["--warn", "--bg", 3],
  ["--err", "--bg", 3],
  ["--green", "--bg", 3],
  ["--code-fg", "--bg", 4.5],
  ["--text-muted", "--surface-raised", 4.5],
  ["--st-working-fg", "--st-working-bg", 3],
  ["--st-ready-fg", "--st-ready-bg", 3],
  ["--st-blocked-fg", "--st-blocked-bg", 3],
  ["--link", "--bg", 4.5],          // hyperlink ink (2026-09-02: the light theme's first link ink sat on --err)
  ["--hl-fg", "--bg", 4.5],         // the hljs syntax palette (tokenized 2026-09-02; was dark-only raw hex)
  ["--hl-kw", "--bg", 4.5],
  ["--hl-str", "--bg", 4.5],
  ["--hl-num", "--bg", 4.5],
  ["--hl-cmt", "--bg", 3],          // comments are deliberately quiet — the dark set sits just above 3
  ["--hl-title", "--bg", 4.5],
  ["--hl-meta", "--bg", 4.5],
  ["--hl-attr", "--bg", 4.5],
];

for (const sheet of ["styles.css", "feed.css"]) {
  const css = read(sheet);
  const dark = props(block(css, ":root {"));
  const light = props(block(css, "body.theme-light {"));

  test(sheet + ": key parity — the light block re-declares every :root token", () => {
    const missing = [...dark.keys()].filter((k) => !light.has(k));
    assert.deepEqual(missing, [], sheet + " light block is missing tokens");
    // a silent parse regression must fail LOUDLY (PR #763 item 6): both blocks hold dozens of
    // tokens — a parser that suddenly sees fewer is broken, not a tidier sheet
    assert.ok(dark.size >= 30, sheet + " parsed only " + dark.size + " dark tokens — parser broken?");
    assert.ok(light.size >= dark.size, sheet + " parsed fewer light tokens than dark");
  });

  test(sheet + ": the designated pairs clear WCAG in BOTH themes — and the evaluated COUNT is pinned", () => {
    for (const [name, theme] of [["dark", dark], ["light", light]] as const) {
      const bgv = theme.get("--bg"); assert.ok(bgv, name + " --bg");
      const page = rgbOf(bgv!, [30, 30, 30])!;
      let evaluated = 0;
      for (const [fgTok, bgTok, floor] of PAIRS) {
        const f = theme.get(fgTok), g = theme.get(bgTok);
        if (!f || !g) continue;   // feed.css :root deliberately holds a SUBSET of tokens
        const ground = rgbOf(g, page); const fore = ground && rgbOf(f, ground);
        if (!ground || !fore) continue;
        evaluated++;
        assert.ok(contrast(fore, ground) >= floor,
          `${sheet} ${name}: ${fgTok} on ${bgTok} = ${contrast(fore, ground).toFixed(2)} < ${floor}`);
      }
      // a skip must be loud (PR #763 item 6): pin how many pairs actually ran per sheet/theme —
      // grow these numbers when PAIRS grows, never let them silently shrink
      const expected = sheet === "styles.css" ? PAIRS.length : 19;   // feed's :root holds a deliberate subset
      assert.ok(evaluated >= expected,
        `${sheet} ${name}: only ${evaluated}/${expected} contrast pairs evaluated — silent skip`);
    }
  });
}
