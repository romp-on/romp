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
  // gear.css: 14 until 2026-09-18, when the section labels' grey #6f747c left with the all-caps dress (they wear var(--accent) now)
  // +1 (T379): the widget rows' demo draws the strip's unknown ring in the strip's own literal #8a8a8a (styles.css .tab-dot.unknown, the network strip's down-host gray), byte-equal so the demo and the strip agree; +1 (T290): .rs-log-n's #ff6b6b, the Log cue's red, a STATUS colour, byte-equal to the shell's #merr.has, deliberately a literal (no sheet defines that red as a token). Before it: T226's shadows-onto-var(--shadow-menu), the .ra-li legend joining --text-soft, and the .ra-openbtn slab joining --btn-bg (2026-09-02: it sat black with dark text in light)
  // 12 since 2026-09-19: the section heads' hard-coded #333 top rule left with the titled-divider dress (the segments wear var(--hairline))
  // +1 = 13 (2026-09-23, the tab-state-badge default flip): the badge-mode demo's counted dot wears the strip's digit ink,
  // a literal #000 (black on the magenta needs dot) mirroring styles.css .tab-badge:not(:empty); no sheet defines that ink
  // as a token (the light override resolves through var(--st-needs-fg)), so it stays a literal like styles.css keeps it.
  "gear.css": 13,
  "strip.css": 8,
  // 8 with the per-goal PR chip: its one red (closed, failing, a failing rollup, the error chip) resolves through
  // var(--vscode-errorForeground), a literal #e5484d before its review (3.82:1 dark, 3.31:1 light as text on the chip);
  // its greens, purple and yellows resolve through --pr-open, --pr-merged and --st-working-bg, its buttons through
  // --overlay-05/-10, its numbers through var(--accent-ink) and its live border and wash through var(--accent) and
  // var(--accent-wash). 9 until 2026-09-21: the hover card's Needs you mark resolved its red through --st-needs-bg
  // (plans/needs-you.md), the literal now its var() fallback
  "fleet-pane.css": 8,
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

// THE PR CHIP'S TEXT INKS, by value, both themes. theme-parity.test.ts's PAIRS skip a token a :root does not declare,
// and the dark :root declares no --vscode-errorForeground: the chip's red resolves through the rule's own fallback
// there (and the kernel's served theme, pinned equal below), so the pairs are measured here from the sheets' bytes.
// Text reads 4.5:1 on every ground it sits on: the chip, the hovered chip, the detail row, the live chip's wash, and
// the page under a rollup, which has no fill of its own; the error chip's border reads 3:1 (non-text chrome).
const hexRgb = (h: string): [number, number, number] => {
  const x = h.replace(/^#/, "");
  const full = x.length === 3 ? x.split("").map((c) => c + c).join("") : x;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
};
const over = (v: string, ground: [number, number, number]): [number, number, number] => {
  const m = v.match(/^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)$/);
  if (!m) return hexRgb(v);
  const a = parseFloat(m[4]);
  return [1, 2, 3].map((i) => Math.round(parseInt(m[i], 10) * a + ground[i - 1] * (1 - a))) as [number, number, number];
};
const lum = (c: [number, number, number]) => {
  const ch = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
};
const ratio = (a: [number, number, number], b: [number, number, number]) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const tokens = (css: string, opener: string) => {
  const at = css.indexOf(opener);
  assert.ok(at >= 0, opener + " present");
  const text = css.slice(at, css.indexOf("\n}", at)).replace(/\/\*[\s\S]*?\*\//g, "");
  return new Map([...text.matchAll(/(--[a-zA-Z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()] as const));
};

test("fleet-pane.css: the PR chip's red and number inks clear their contrast floors on every ground, both themes", () => {
  const PANE = read("fleet-pane.css").replace(/\/\*[\s\S]*?\*\//g, ""), STYLES = read("styles.css");
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const decl = (sel: string, prop: string) => {
    const rule = new RegExp("(?:^|\\})" + sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\{([^}]*)\\}", "m").exec(PANE);
    assert.ok(rule, sel + " rule present");
    const m = new RegExp("(?:^|;)\\s*" + prop + ":([^;]+)").exec(rule![1]);
    assert.ok(m, sel + " declares " + prop);
    return m![1].trim();
  };
  const RED = "var(--vscode-errorForeground,#f48771)";
  const served = KERNEL.match(/--vscode-errorForeground:(#[0-9a-fA-F]{6});/);
  assert.ok(served && RED.includes(served[1]), "the kernel's served dark theme declares the fallback's own red");
  const pageDark = decl("html,body", "background");
  for (const [name, root] of [["dark", tokens(STYLES, ":root {")], ["light", tokens(STYLES, "body.theme-light {")]] as const) {
    // a token the theme block declares wins; otherwise the var()'s own fallback, as the cascade resolves it here
    const resolve = (v: string): string => {
      const m = v.match(/^var\((--[a-zA-Z0-9-]+)\s*(?:,\s*(.+))?\)$/);
      if (!m) return v;
      const own = root.get(m[1]);
      assert.ok(own || m[2], `${name}: ${m[1]} resolves (declared, or a fallback)`);
      return resolve(own || m[2]!);
    };
    const page = hexRgb(resolve(name === "dark" ? pageDark : root.get("--vscode-editor-background")!));
    const chip = over(decl(".fl-pr", "background"), page), hover = over(decl(".fl-pr:hover", "background"), page);
    const detail = over(decl(".fl-prdet", "background"), page), wash = over(resolve("var(--accent-wash)"), page);
    const rollHover = over(decl(".fl-pr.roll:hover", "background"), page);
    const checks: Array<[string, string, string, [number, number, number], number]> = [
      [".fl-pr.err", "color", "the error chip", chip, 4.5],
      [".fl-pr.err", "color", "the hovered error chip", hover, 4.5],
      [".fl-pr.err", "border-color", "the error chip's border", chip, 3],
      [".fl-pr.ck-fail .fl-pr-ck", "color", "a failing check", chip, 4.5],
      [".fl-pr.ck-fail .fl-pr-ck", "color", "a failing check, hovered", hover, 4.5],
      [".fl-pr.roll.w-fail .fl-pr-ck", "color", "a failing rollup", page, 4.5],
      [".fl-pr.roll.w-fail .fl-pr-ck", "color", "a failing rollup, hovered", rollHover, 4.5],
      [".fl-pr.st-closed .fl-pr-state", "color", "a closed PR's glyph", chip, 4.5],
      [".fl-pr-num", "color", "the chip's number", chip, 4.5],
      [".fl-pr-num", "color", "the hovered chip's number", hover, 4.5],
      [".fl-pr-num", "color", "the live chip's number", wash, 4.5],
      [".fl-pr-num", "color", "a rollup's count", page, 4.5],
      [".fl-prdet-num", "color", "the detail row's number", detail, 4.5],
    ];
    for (const [sel, prop, what, ground, floor] of checks) {
      const v = decl(sel, prop), ink = hexRgb(resolve(v)), r = ratio(ink, ground);
      assert.ok(r >= floor, `${name}: ${what} (${sel} ${prop}: ${v} → ${resolve(v)}) reads ${r.toFixed(2)}:1 < ${floor}`);
    }
  }
});
