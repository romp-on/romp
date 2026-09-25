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
/** OKLCH (L 0..1, C, hue in degrees) to sRGB channels 0..255, clamped to the gamut: the tinted ground of an incoming card. */
function oklchToRgb(L: number, C: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b, m_ = L - 0.1055613458 * a - 0.0638541728 * b, s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  const lin = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
               -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s];
  return lin.map((c) => { c = Math.max(0, Math.min(1, c)); const v = c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; return Math.round(v * 255); }) as [number, number, number];
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
  ["--st-5xx-bg", "--bg", 3],      // the API-health cell's 5xx bar on the tip's ground (PR 1935 round three: the first purple sat at 2.39:1)
  ["--st-5xx-ink", "--bg", 4.5],   // the 5xx digits in the tip
  ["--st-5xx-fg", "--st-5xx-bg", 3],   // text on the 5xx fill (the pair every state token carries)
  ["--cmt-hl-outline", "--bg", 3],   // the comment notch (the rail tick's fill): a LINE, so it must read against the page (T310)
  ["--st-awaiting-bg", "--bg", 3],   // the unread passage's dashed box and ring, and the tick's halo (2026-09-12): a line in the needs-you red
  ["--accent-fg", "--accent", 3],
  ["--accent-ink", "--box-bg", 4.5],   // accent-coloured LABELS on the Needs you box's wash (the second contributor's post-merge note on PR 2014: the accent read 4.07:1 there)
  ["--deny", "--box-bg", 4.5],         // the deny button's label on the wash (the same note: #e5484d read 3.08:1 on the light wash)
  ["--deny-fg", "--deny", 4.5],        // the deny button's hover text on its own fill (the manager's read of PR 2039: white read 3.19:1 on the dark fill)
  ["--warn", "--bg", 3],
  ["--err", "--bg", 3],
  ["--green", "--bg", 3],
  ["--code-fg", "--bg", 4.5],
  ["--text-muted", "--surface-raised", 4.5],
  ["--postal-coordinate", "--bg", 4.5],   // the postal kind word's three-step ramp (T320): text, so 4.5:1 on the page in both themes...
  ["--postal-delegate", "--bg", 4.5],
  ["--postal-question", "--bg", 4.5],
  ["--postal-coordinate", "--box-bg", 4.5],   // ...and on a BOXED card (an incoming message paints --box-bg over --bg), where the
  ["--postal-delegate", "--box-bg", 4.5],     // review found the light coordination step at 4.33:1 (2026-09-10)
  ["--postal-question", "--box-bg", 4.5],
  ["--st-working-fg", "--st-working-bg", 3],
  ["--st-ready-fg", "--st-ready-bg", 3],
  ["--st-blocked-fg", "--st-blocked-bg", 3],
  ["--st-retrying-fg", "--st-retrying-bg", 3],       // 2026-09-08: the retrying amber tokenised (#e67e22/#2a1500 dark, #9C4A0C/#fff light)
  ["--st-needs-fg", "--st-needs-bg", 4.5],           // 2026-09-20: the Needs you magenta (#d946ef/#2a0a2a dark, #a21caf/#fff light; plans/needs-you.md). 4.5 (text) not 3: the fg IS the numbered badge's digit ink (5.17 dark, 6.32 light), so the token pair carries the floor the digit-ink parse pins (PR 2017 review)
  ["--st-needs-bg", "--bg", 3],                      // …and the Needs you RING is a line on the page (the tab's dashed outline, the folded header's pip)
  // (--st-compacting-fg on --st-compacting-bg is deliberately NOT paired: the dark teal + white pairing predates
  // this file and sits at 2.49:1, and decision 3 of the 2026-09-08 notice audit keeps dark byte-identical; the
  // light re-ink — #0F766E, 4.30:1 on the card, white on it 5.47:1 — is pinned by value in notice-vocab.test.ts)
  ["--link", "--bg", 4.5],          // hyperlink ink (2026-09-02: the light theme's first link ink sat on --err)
  ["--hl-fg", "--bg", 4.5],         // the hljs syntax palette (tokenized 2026-09-02; was dark-only raw hex)
  ["--hl-kw", "--bg", 4.5],
  ["--hl-str", "--bg", 4.5],
  ["--hl-num", "--bg", 4.5],
  ["--hl-cmt", "--bg", 3],          // comments are deliberately quiet
  ["--hl-cmt", "--box-bg", 4.5],    // ...but readable on the code block they sit in (the fence's fill is --box-bg over --bg)
  ["--hl-title", "--bg", 4.5],
  ["--hl-meta", "--bg", 4.5],
  ["--hl-attr", "--bg", 4.5],
  // the classic scrollbars (2026-09-25: the chat's thumb was a white alpha, invisible on the light page). A thumb is chrome the
  // eye hunts for, not text: 2 here holds the dark thumb the chat has always had (2.20:1) from fading further; the light
  // theme, where the bar went missing, is held to the 3:1 non-text floor over its own track in the test below
  ["--scroll-thumb", "--bg", 2],
  ["--scroll-thumb-hover", "--bg", 3],
  ["--scroll-thumb-thin", "--input-bg", 2],   // the composer's thin bar, inside the box
];

test("styles.css: the provisional wash the kind pairs are computed against is the one the sheet paints", () => {
  const css = read("styles.css");
  const SHARED = ".queued-bubble, .notice.queued-bubble, .notice.notice-slim.queued-bubble,";   // the list runs on to the echo of a slash command (T403)
  const bubble = css.slice(css.indexOf(SHARED), css.indexOf("\n}\n", css.indexOf(SHARED)));
  assert.ok(css.indexOf(SHARED) > 0, "the shared provisional rule is where the slice looks");
  assert.match(bubble, /background: color-mix\(in srgb, var\(--you\) 8\.5%, transparent\);/);
  assert.doesNotMatch(bubble, /opacity:/, "the fade is in the colours: no element opacity dims the words on the card");
});

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
      const expected = sheet === "styles.css" ? PAIRS.length : 24;   // feed's :root holds a deliberate subset (+ the retrying pair, 2026-09-08; + the two ask pairs, 2026-09-14: the settings' ring demo reads the token there; + the two 5xx pairs, PR 1935 rounds three and four, 2026-09-21: --st-5xx-bg on --bg and --st-5xx-fg on --st-5xx-bg, the feed's root declaring the fill and its text ink)
      // T337: the postal kind words also sit on the PROVISIONAL card (a sent card not yet landed wears the pending
      // bubble's dress: an 8.5% wash of --you over the page, styles.css .queued-bubble, no element opacity since the
      // fade moved into the dress's colours), the darkest ground they meet; each reads at 4.5:1 there too
      if (sheet === "styles.css") {
        const you = rgbOf(theme.get("--you")!, page)!;
        const wash = [0, 1, 2].map((i) => Math.round(you[i] * 0.085 + page[i] * 0.915)) as [number, number, number];
        for (const tok of ["--postal-coordinate", "--postal-delegate", "--postal-question"]) {
          const fore = rgbOf(theme.get(tok)!, wash)!;
          assert.ok(contrast(fore, wash) >= 4.5, `${sheet} ${name}: ${tok} on the provisional wash = ${contrast(fore, wash).toFixed(2)} < 4.5`);
        }
      }
      // T337c: the INCOMING card no longer wears --box-bg but the peer's hue at the ground's lightness (styles.css: an oklch
      // relative colour from the rail, the tokens --postal-wash-l and --postal-wash-c), a different ground for every peer;
      // each kind word reads at 4.5:1 there for EVERY hue (the sent boxed card still wears --box-bg: those pairs stand)
      if (sheet === "styles.css") {
        const L = parseFloat(theme.get("--postal-wash-l")!), C = parseFloat(theme.get("--postal-wash-c")!);
        assert.ok(L > 0 && L < 1 && C > 0, `${sheet} ${name}: the wash tokens parse (${L}, ${C})`);
        for (const tok of ["--postal-coordinate", "--postal-delegate", "--postal-question"]) {
          let worst = Infinity, worstHue = -1;
          for (let h = 0; h < 360; h++) {
            const ground = oklchToRgb(L, C, h);
            const fore = rgbOf(theme.get(tok)!, ground)!;
            const c = contrast(fore, ground);
            if (c < worst) { worst = c; worstHue = h; }
          }
          assert.ok(worst >= 4.5, `${sheet} ${name}: ${tok} on the tinted ground = ${worst.toFixed(2)} at hue ${worstHue} < 4.5`);
        }
      }
      assert.ok(evaluated >= expected,
        `${sheet} ${name}: only ${evaluated}/${expected} contrast pairs evaluated — silent skip`);
    }
  });
}

test("styles.css: the light theme's scrollbar thumbs clear 3:1 over the ground they sit on; the dark ones keep their values", () => {
  const css = read("styles.css");
  const dark = props(block(css, ":root {")), light = props(block(css, "body.theme-light {"));
  // dark resolves byte for byte to the white alphas the chat's bar always wore (only the light theme was broken)
  assert.equal(dark.get("--scroll-track"), "rgba(255, 255, 255, 0.04)");
  assert.equal(dark.get("--scroll-thumb"), "rgba(255, 255, 255, 0.24)");
  assert.equal(dark.get("--scroll-thumb-hover"), "rgba(255, 255, 255, 0.36)");
  assert.equal(dark.get("--scroll-thumb-thin"), "rgba(255, 255, 255, 0.3)");
  const page = rgbOf(light.get("--bg")!, [255, 255, 255])!;
  const track = rgbOf(light.get("--scroll-track")!, page)!;   // the chat thumb sits on its track, the track on the page
  for (const [tok, ground, floor] of [["--scroll-thumb", track, 3], ["--scroll-thumb-hover", track, 4], ["--scroll-thumb", page, 3]] as const) {
    const c = contrast(rgbOf(light.get(tok)!, ground)!, ground);
    assert.ok(c >= floor, `light ${tok} = ${c.toFixed(2)}:1 < ${floor}`);
  }
  const input = rgbOf(light.get("--input-bg")!, page)!;   // the composer's box
  const thin = contrast(rgbOf(light.get("--scroll-thumb-thin")!, input)!, input);
  assert.ok(thin >= 3, `light --scroll-thumb-thin on --input-bg = ${thin.toFixed(2)}:1 < 3`);
});

// THE RING HUES, ALL PAIRS PER THEME (the rings-as-widgets change, 2026-09-14; the Needs you magenta, 2026-09-21): the three
// dashed rings a tab can wear (the two reds, the magenta, the amber) are told apart by colour alone (same shape, same dash,
// on different tabs), so every pair of ring hues must stay apart for full-colour readers (OKLab distance x100 at least 15)
// AND under the two red-green deficiencies (at least 8 after the Machado, Oliveira and Fernandes 2009 simulation at severity
// 1.0), the floors the dataviz palette validator applies to categorical marks; the Needs you ring against the two DOTS it can
// sit beside (the working gold and the await-green, a 7px disc inside a 2px outline: shape and position tell them apart too)
// needs the deficiency floor only. The magenta clears every floor with room in both themes: dark #d946ef sits 24 to 31 from
// the other rings for full-colour readers, 23.7 to 28.7 under the deficiencies, 27.5 to 32.5 from the dots, 38.6 from the
// working gold; light #a21caf 22 to 25 from the rings, 21.7 to 22.3 under the deficiencies, 22.7 to 30.2 from the dots, 29.4
// from the gold (the yellow it replaced sat 9.5 from the gold in the dark theme and needed a lightness trick in the light one).
// The ring also reads at 3:1 on the hovered tab and the selected tab's fill, the two washes a ring can sit on besides the page.
function oklab(rgb: [number, number, number]): [number, number, number] {
  const lin = rgb.map((c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }) as [number, number, number];
  return oklabFromLin(lin);
}
function oklabFromLin([r, g, b]: [number, number, number]): [number, number, number] {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b), s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
}
// Machado, Oliveira and Fernandes (2009), severity 1.0, on linear RGB
const CVD: Record<string, number[][]> = {
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
};
function simulate(rgb: [number, number, number], kind: string): [number, number, number] {
  const lin = rgb.map((c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); });
  const M = CVD[kind];
  return [0, 1, 2].map((i) => Math.max(0, Math.min(1, M[i][0] * lin[0] + M[i][1] * lin[1] + M[i][2] * lin[2]))) as [number, number, number];
}
function deltaE(a: [number, number, number], b: [number, number, number], kind?: string): number {
  const x = kind ? oklabFromLin(simulate(a, kind)) : oklab(a), y = kind ? oklabFromLin(simulate(b, kind)) : oklab(b);
  return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}
const cvdWorst = (a: [number, number, number], b: [number, number, number]) => Math.min(deltaE(a, b, "protan"), deltaE(a, b, "deutan"));

test("the ring hues stay apart in BOTH themes, every pair: rings against rings for full-colour readers and under red-green deficiencies, the Needs you ring against the dots under the deficiencies; the magenta reads on every tab ground", () => {
  const css = read("styles.css");
  for (const [name, theme] of [["dark", props(block(css, ":root {"))], ["light", props(block(css, "body.theme-light {"))]] as const) {
    const page = rgbOf(theme.get("--bg")!, [30, 30, 30])!;
    const tok = (t: string) => rgbOf(theme.get(t)!, page)!;
    const rings: Record<string, [number, number, number]> = { awaiting: tok("--st-awaiting-bg"), blocked: tok("--st-blocked-bg"), ask: tok("--st-needs-bg"), retrying: tok("--st-retrying-bg") };
    const dots: Record<string, [number, number, number]> = { working: tok("--st-working-bg"), awaitbg: tok("--st-awaitbg-bg") };
    for (const other of ["awaiting", "blocked", "retrying"]) {
      const n = deltaE(rings.ask, rings[other]), c = cvdWorst(rings.ask, rings[other]);
      assert.ok(n >= 15, `${name}: the Needs you ring against the ${other} ring reads ${n.toFixed(1)} to full-colour readers (floor 15)`);
      assert.ok(c >= 8, `${name}: the Needs you ring against the ${other} ring reads ${c.toFixed(1)} under a red-green deficiency (floor 8)`);
    }
    for (const dot of Object.keys(dots)) {
      const c = cvdWorst(rings.ask, dots[dot]);
      assert.ok(c >= 8, `${name}: the Needs you ring against the ${dot} dot reads ${c.toFixed(1)} under a red-green deficiency (floor 8)`);
    }
    // the light theme clears the full-colour floor against the dots too; the dark magenta clears the full-colour floor against the gold as well
    const gold = deltaE(rings.ask, dots.working);
    assert.ok(gold >= 15, `${name}: the Needs you ring against the working gold reads ${gold.toFixed(1)}`);
    // the grounds a ring sits on: the page (PAIRS above), the hovered tab (a 6% white wash) and the selected tab's fill
    const hover = [0, 1, 2].map((i) => Math.round(255 * 0.06 + page[i] * 0.94)) as [number, number, number];
    const active = rgbOf(theme.get("--tab-active-bg")!, page)!;
    // and the Needs you box's wash (`--box-bg` over the page), the ground under its edge, its header's dot and its rows (the second contributor's
    // review of PR 1967: the header's ground is that wash, opaque, so one ground covers the dot too)
    const box = rgbOf(theme.get("--box-bg")!, page)!;
    for (const [g, ground] of [["hovered tab", hover], ["selected tab", active], ["Needs you box wash", box]] as const) {
      assert.ok(contrast(rings.ask, ground) >= 3, `${name}: the Needs you ring on the ${g} = ${contrast(rings.ask, ground).toFixed(2)} < 3`);
    }
    // the RETRYING amber is a HOLLOW LEFT DOT (a ring) under badge mode (plans/tab-state-badge.md), so its amber outline
    // sits on the tab face: 3:1 (large chrome, not text) on the page, the hovered tab and the selected tab's fill, both themes.
    for (const [g, ground] of [["page", page], ["hovered tab", hover], ["selected tab", active]] as const) {
      assert.ok(contrast(rings.retrying, ground) >= 3, `${name}: the retrying amber dot on the ${g} = ${contrast(rings.retrying, ground).toFixed(2)} < 3`);
    }
    // the box's ok button rests in a border of the accent at 90% over the wash (color-mix in srgb): a non-text edge, so the 3:1 floor on its ground
    // in both themes (the round-fourteen verifier of PR 1967 measured 60% at 2.33:1 on the light theme)
    const accent = rgbOf(theme.get("--accent")!, page)!;
    const shareM = css.match(/\.ntc-btn\.ntc-ok \{[^}]*color-mix\(in srgb, var\(--accent\) (\d+)%, transparent\)/);
    assert.ok(shareM, "the ok button's border is the accent's color-mix over its ground");
    const share = Number(shareM![1]) / 100;   // the share the sheet carries, mixed as the sheet mixes it (the first contributor's note on PR 2014: a fixed 0.9 beside a literal pin let a lockstep re-ink pass)
    const okBorder = [0, 1, 2].map((i) => Math.round(accent[i] * share + box[i] * (1 - share))) as [number, number, number];
    assert.ok(contrast(okBorder, box) >= 3, `${name}: the ok button's resting border (the accent at ${shareM![1]}%) on the box wash = ${contrast(okBorder, box).toFixed(2)} < 3`);
    // the deny button's resting border: the per-theme deny token at the share the sheet carries, mixed as the sheet mixes it, 3:1 on the wash in
    // both themes (the second contributor's post-merge note on PR 2014: rgba(229, 72, 77, 0.6) read 2.20:1 dark and 2.03:1 light)
    const deny = rgbOf(theme.get("--deny")!, page)!;
    const denyM = css.match(/\.ntc-btn\.ntc-deny \{[^}]*color-mix\(in srgb, var\(--deny\) (\d+)%, transparent\)/);
    assert.ok(denyM, "the deny button's border is the deny token's color-mix over its ground");
    const denyShare = Number(denyM![1]) / 100;
    const denyBorder = [0, 1, 2].map((i) => Math.round(deny[i] * denyShare + box[i] * (1 - denyShare))) as [number, number, number];
    assert.ok(contrast(denyBorder, box) >= 3, `${name}: the deny button's resting border (the deny token at ${denyM![1]}%) on the box wash = ${contrast(denyBorder, box).toFixed(2)} < 3`);
    // the deny button's HOVER text on its own fill: text, so 4.5:1, read from the hover rule as the sheet inks it (a token or a literal; the manager's
    // read of PR 2039: a white literal read 3.19:1 on the dark fill, a regression stated in the body and not to be shipped)
    const hoverM = css.match(/\.ntc-btn\.ntc-deny:hover:not\(:disabled\) \{ color: ([^;]+); background: var\(--deny\);/);
    assert.ok(hoverM, "the deny button's hover rule inks its text and fills with the deny token");
    const inkV = hoverM![1].trim(); const inkTok = inkV.match(/^var\((--[a-z-]+)\)$/);
    const expand = (v: string) => v.replace(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i, "#$1$1$2$2$3$3");   // a three-digit literal (the old #fff) measures too
    const ink = inkTok ? rgbOf(expand(theme.get(inkTok[1]) || ""), deny) : rgbOf(expand(inkV), deny);
    assert.ok(ink, `${name}: the deny hover ink resolves (${inkV})`);
    assert.ok(contrast(ink!, deny) >= 4.5, `${name}: the deny button's hover text (${inkV}) on its fill = ${contrast(ink!, deny).toFixed(2)} < 4.5`);
    // the state badge's digit is 9px bold TEXT (plans/tab-state-badge.md), so the 4.5:1 text floor, NOT the 3:1 chrome
    // floor. Black on the dark magenta reads ~6.07:1 and clears; on the lighter light-theme magenta (#a21caf) black is
    // only 3.32:1, short of 4.5, so the light theme inks the digit in the state's own foreground token (--st-needs-fg,
    // white: ~6.32:1). The CSS is `color:#000` with a `body.theme-light` override to var(--st-needs-fg). The user chose
    // black in the dark theme; this is the user's call to veto (the manager relayed the light-theme swap, 2026-09-22).
    // PARSE the ink from the sheet, never restate the constant (the second contributor on PR 2017): the base rule's
    // color (a 3-digit hex, expanded), and for light the `body.theme-light` override resolved by cascade through the token.
    const colorIn = (re: RegExp) => { const m = css.match(re); const c = m && m[1].match(/color:\s*([^;]+);/); return c ? c[1].trim() : ""; };
    const rawInk = name === "light"
      ? colorIn(/body\.theme-light \.tab-badge:not\(:empty\) \{([^}]*)\}/)
      : colorIn(/\n\.tab-badge:not\(:empty\) \{([^}]*)\}/);
    const varInk = rawInk.match(/^var\((--[a-z0-9-]+)\)$/i);
    const hex3 = /^#[0-9a-f]{3}$/i.test(rawInk) ? "#" + rawInk.slice(1).split("").map((c) => c + c).join("") : rawInk;
    const digit = varInk ? tok(varInk[1]) : rgbOf(hex3, page)!;   // the token by cascade, or the (expanded) literal
    assert.ok(digit, `${name}: the badge digit ink parses from the sheet (read ${JSON.stringify(rawInk)})`);
    assert.ok(contrast(digit, rings.ask) >= 4.5, `${name}: the badge's digit ink ${JSON.stringify(rawInk)} on the magenta = ${contrast(digit, rings.ask).toFixed(2)} < 4.5`);
  }
  // the light value itself, so a re-ink is a deliberate change here and in feed.css (tab-rings.test.ts pins the two sheets equal)
  assert.match(block(css, "body.theme-light {"), /--st-needs-bg: #a21caf; --st-needs-fg: #ffffff;/);
});

test("the 5xx marks of the API-health cell clear the validator's two floors against every colour they share a surface with, in both themes; the one conceded pair is the dark ink against the Needs you magenta under a deficiency, recorded", () => {
  // plans/needs-you.md: the cell sits on the landing page beside every session's state. The FILL is the histogram's 5xx band (beside the
  // accent, 429 and other bands); the INK is a count's digits in the tip's line (beside the accent ink, the 429 ink and the words gray);
  // both against the Needs you token, since the magenta is on the same page. Floors: 15 to full-colour readers, 8 under protan and deutan
  // (cvdWorst), the ring pairs' own. The ink falls back to the landing sheet's literal when the token is absent, so a tree without the
  // token reds here on a DISTANCE, not on a missing name.
  const css = read("styles.css"); const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const hex = (v: string) => rgbOf(v, [0, 0, 0])!;
  const pairs: Array<[string, string, string, number, number]> = [];   // theme, a, b, floor full, floor cvd
  for (const theme of [":root {", "body.theme-light {"]) {
    const b = block(css, theme), dark = theme === ":root {";
    const needs = b.match(/--st-needs-bg: (#[0-9a-fA-F]{6});/)![1], fill = b.match(/--st-5xx-bg: (#[0-9a-fA-F]{6});/)![1], accent = b.match(/--accent: (#[0-9a-fA-F]{6});/)![1];
    const inkTok = b.match(/--st-5xx-ink: (#[0-9a-fA-F]{6});/);
    const inkLit = KERNEL.match(dark ? /\.ah-c-r5xx\{color:(?:var\(--st-5xx-ink,)?(#[0-9a-fA-F]{6})\)?\}/ : /body\.theme-light \.ah-c-r5xx\{color:(?:var\(--st-5xx-ink,)?(#[0-9a-fA-F]{6})\)?\}/);
    const ink = inkTok ? inkTok[1] : inkLit![1];
    // the cell's other colours, the landing's literals (T340): the 429 fill and ink, the other band, the tip's words
    // the 429 BAND is var(--st-blocked-bg,#e5484d) in both themes (the landing paints no light override for it; the fourth review, 2026-09-21); the 429 INK is re-inked for the light
    const red429 = "#e5484d", ink429 = dark ? "#ef6b6f" : "#B02A1C", other = dark ? "#d9f99d" : "#4f46e5", words = dark ? "#a9b1ba" : "#5D574E";
    const name = dark ? "dark" : "light";
    const check = (what: string, a: string, bb: string, floorFull: number, floorCvd: number) => {
      const full = deltaE(hex(a), hex(bb)), cvd = cvdWorst(hex(a), hex(bb));
      assert.ok(full >= floorFull, `${name}: ${what} ${a} against ${bb} =${full.toFixed(1)} < ${floorFull} (full colour)`);
      assert.ok(cvd >= floorCvd, `${name}: ${what} ${a} against ${bb} =${cvd.toFixed(1)} < ${floorCvd} (under a red-green deficiency)`);
    };
    check("the 5xx fill against the Needs you token", fill, needs, 15, 8);
    check("the 5xx fill against the accent band", fill, accent, 15, 8);
    check("the 5xx fill against the 429 band", fill, red429, 15, 8);
    check("the 5xx fill against the other band", fill, other, 15, 8);
    check("the 5xx ink against the accent ink on the same line", ink, accent, 15, 8);
    check("the 5xx ink against the 429 ink on the same line", ink, ink429, 15, 8);
    check("the 5xx ink against the tip's words", ink, words, 15, 8);
    check("the 5xx ink against the other-count ink on the same line", ink, other, 15, 8);
    // the ink against the Needs you token: 15 to full-colour readers in both themes; under a deficiency the light clears 8 and the
    // DARK pair is conceded on purpose at 2.7 (no violet or blue ink clears the accent ink, the other-count ink and the magenta at
    // once under red-green CVD while reading 4.5:1 on the detail's hover wash; the two never share a line), recorded in
    // plans/needs-you.md and styles.css: the floor here is 2, so a drift lower still reddens
    check("the 5xx ink against the Needs you token", ink, needs, 15, dark ? 2 : 8);
    // the contrasts the marks need where they sit: the fill 3:1 and the ink 4.5:1 on the TIP's ground, read from the landing's
    // #ah-tip rule per theme (#1e1e1e dark, #FFFFFF light): the bars and the rows live in #ah-tip, not on the page (the fifth review,
    // 2026-09-21: this read the page's --bg, cream in the light, a stricter ground for a dark ink than the tip's white, so nothing was
    // falsely green; PAIRS keeps both tokens on the page's --bg as well)
    const tipRule = KERNEL.match(dark ? /"#ah-tip,#ru-tip\{[^"]*?background:(#[0-9a-fA-F]{6})/ : /"body\.theme-light #ah-tip,body\.theme-light #ru-tip\{[^"]*?background:(#[0-9a-fA-F]{6})/);
    assert.ok(tipRule, `${name}: the landing paints the tip's ground`);
    const tip = hex(tipRule![1]);
    assert.ok(contrast(hex(fill), tip) >= 3, `${name}: the 5xx fill on the tip's ground ${tipRule![1]} = ${contrast(hex(fill), tip).toFixed(2)} < 3`);
    assert.ok(contrast(hex(ink), tip) >= 4.5, `${name}: the 5xx ink on the tip's ground ${tipRule![1]} = ${contrast(hex(ink), tip).toFixed(2)} < 4.5`);
    // ...and the BARS sit on the graph's wash inside the tip (the landing's .ru-tip-graph svg{background:rgba(255,255,255,0.04)}, no
    // light override), so the fill is measured there too, the wash parsed from the rule and composited over the tip's ground (1935's
    // sixth review, 2026-09-21: a fill reading 3.02:1 on the bare tip and 2.71:1 on the wash passed here; 6.19:1 dark, 11.90:1 light today)
    const graphRule = KERNEL.match(/"\.ru-tip-graph svg\{[^"]*?background:(rgba\([^)]*\))/);
    assert.ok(graphRule, `${name}: the landing paints the graph's wash`);
    const graph = rgbOf(graphRule![1], tip);
    assert.ok(graph, `${name}: the graph wash ${graphRule![1]} parses`);
    assert.ok(contrast(hex(fill), graph!) >= 3, `${name}: the 5xx fill on the graph's wash ${graphRule![1]} over ${tipRule![1]} = ${contrast(hex(fill), graph!).toFixed(2)} < 3`);
    // ...and on the detail's row hover wash, where a waiting row's 5xx code sits in the same ink (the fourth review, 2026-09-21: the violet
    // before this read 3.93:1 there). The wash is PARSED from the landing's .ah-row:hover rule per theme (6% white dark, 5% black
    // light today) and composited over the tip's ground, so a changed wash moves the measurement rather than a text pin (the fifth review)
    const washRule = KERNEL.match(dark ? /"\.ah-row:hover\{background:(rgba\([^)]*\))\}/ : /"body\.theme-light \.ah-row:hover\{background:(rgba\([^)]*\))\}/);
    assert.ok(washRule, `${name}: the landing paints the row hover wash`);
    const hover = rgbOf(washRule![1], tip);
    assert.ok(hover, `${name}: the hover wash ${washRule![1]} parses`);
    assert.ok(contrast(hex(ink), hover!) >= 4.5, `${name}: the 5xx ink on the row hover wash ${washRule![1]} over ${tipRule![1]} = ${contrast(hex(ink), hover!).toFixed(2)} < 4.5`);
    pairs.push([name, fill, ink, 15, 8]);
  }
  assert.equal(pairs.length, 2);
  assert.ok(KERNEL.includes(".ah-c-r5xx{color:var(--st-5xx-ink,#8a8aff)}") && KERNEL.includes("body.theme-light .ah-c-r5xx{color:var(--st-5xx-ink,#4c1b7e)}"), "the landing's inks ride the token");
});

// BADGE MODE, the left-slot dots (plans/tab-state-badge.md): working (a filled gold disc), awaiting (a filled green
// disc) and retrying (a HOLLOW amber ring since 2026-09-23). Two dots in the same slot must be told apart, and colour
// alone is not enough under a red-green deficiency (the review's math: dark retrying vs awaiting 2.7 deutan, far below
// the categorical floor 8). The amended rule: a pair whose hues clear the floors (15 full colour, 8 under a deficiency)
// needs no shape; a pair whose hues cannot must wear a distinct SHAPE. The retrying ring is that shape, so it needs no
// colour floor against the filled dots. TWO same-shape pairs are PRE-EXISTING misses conceded on the user's decision, both
// filled with no shape apart, not fixed in this PR (like the compacting pair above): dark working gold vs awaiting green
// (18.4 full / 4.2 protan) and light working gold vs opening accent (12.7 full / 2.4 deutan).
test("badge mode: the SIX left-slot dot classes are told apart by shape or colour; each pre-existing filled miss is conceded on the user's decision", () => {
  const css = read("styles.css");
  const body = (re: RegExp, what: string) => { const m = css.match(re); assert.ok(m, what + " rule present"); return m[1]; };
  const nc = (x: string) => x.replace(/\/\*[\s\S]*?\*\//g, "");
  // (a) the SHAPE of each of the six classes styles.css paints in the left slot. HOLLOW (an inset ring, a distinct form):
  // retrying (amber) and unknown (grey). FILLED discs: working (gold), awaiting (green), idle (dim grey, 45% opacity),
  // opening (accent). retrying now SHARES the hollow form with unknown.
  const retrying = nc(body(/\n\.tab-dot\.retrying \{([^}]*)\}/, ".tab-dot.retrying"));
  assert.match(retrying, /background:\s*transparent/, "retrying is HOLLOW"); assert.match(retrying, /box-shadow:\s*inset 0 0 0 [\d.]+px var\(--st-retrying-bg\)/, "…an inset amber ring");
  const unknown = nc(body(/\n\.tab-dot\.unknown \{([^}]*)\}/, ".tab-dot.unknown"));
  assert.match(unknown, /background:\s*transparent/, "unknown is HOLLOW"); assert.match(unknown, /box-shadow:\s*inset 0 0 0 [\d.]+px/, "…an inset ring (retrying shares this form)");
  // LOW b: read unknown's stroke colour and idle's opacity from the SHEET the shapes were parsed from, so a re-ink is graded (not a hardcoded #8a8a8a / 0.45).
  const unknownStroke = (unknown.match(/box-shadow:\s*inset\s+0\s+0\s+0\s+[\d.]+px\s+(#[0-9a-fA-F]+|var\([^)]+\))/) || [])[1];
  assert.ok(unknownStroke, "unknown's inset stroke colour is read from styles.css");
  const idleRule = nc(body(/\n\.tab-dot\.idle \{([^}]*)\}/, ".tab-dot.idle"));
  const idleOp = parseFloat((idleRule.match(/opacity:\s*([\d.]+)/) || [])[1]);
  assert.ok(idleOp > 0 && idleOp < 1, "idle's opacity is read from styles.css");
  for (const [sel, tok] of [["\\n\\.tab-dot ", "--st-working-bg"], ["\\n\\.tab-dot\\.await ", "--st-awaitbg-bg"], ["\\n\\.tab-dot\\.idle ", "--dim"], ["\\n\\.tab-dot\\.opening ", "--accent"]]) {
    const d = nc(body(new RegExp(sel + "\\{([^}]*)\\}"), sel));
    assert.match(d, new RegExp("background:\\s*var\\(" + tok + "\\)"), sel + " is a FILLED disc (" + tok + ")");
    assert.doesNotMatch(d, /box-shadow/, sel + " has no ring");
  }
  const SHAPE: Record<string, string> = { working: "filled", awaiting: "filled", idle: "filled", opening: "filled", retrying: "hollow", unknown: "hollow" };
  // (b) the amended rule for every pair among the six, both themes: a pair whose shapes DIFFER is told apart by form (no
  // colour floor). A same-shape pair needs the categorical floors (15 full colour, 8 under a red-green deficiency). A
  // same-shape pair whose hues cannot reach them is a PRE-EXISTING miss conceded on the user's decision (the gold-vs-green
  // pair relayed by the manager 2026-09-22, the light gold-vs-opening pair the same way 2026-09-23), recorded here with BOTH
  // its measured full-colour and deficiency figures, each floored at that value LESS a 0.3 margin, so a drift
  // toward collapse still reds while today's value passes. retrying's hue vs the filled dots is BELOW the floor (dark
  // retrying-vs-awaiting 2.7, light retrying-vs-working 3.3); the hollow shape is what carries those, so they skip the floor.
  const CONCEDE: Record<string, { full: number; def: number }> = {   // theme|a|b (a,b sorted) -> the measured [full colour, deficiency] of a same-shape pair below the floors; each guarded at its figure LESS a 0.3 margin (LOW a), so a drift toward collapse reds while today passes
    "dark|awaiting|working": { full: 18.4, def: 4.2 },    // dark working GOLD vs awaiting GREEN: full clears 15, the protan deficiency 4.2 does not; both filled discs, no shape apart
    "light|opening|working": { full: 12.7, def: 2.4 },    // light working GOLD vs opening ACCENT: full 12.7 AND deutan 2.4 both below the floors; both filled discs, no shape apart
  };
  for (const [name, theme] of [["dark", props(block(css, ":root {"))], ["light", props(block(css, "body.theme-light {"))]] as const) {
    const page = rgbOf(theme.get("--bg")!, [30, 30, 30])!;
    const tok = (t: string) => rgbOf(theme.get(t)!, page)!;
    const dim = tok("--dim");
    const rgb: Record<string, [number, number, number]> = { working: tok("--st-working-bg"), awaiting: tok("--st-awaitbg-bg"), retrying: tok("--st-retrying-bg"),
                  unknown: rgbOf(unknownStroke, page)!, opening: tok("--accent"),
                  idle: [0, 1, 2].map((i) => Math.round(dim[i] * idleOp + page[i] * (1 - idleOp))) as [number, number, number] };
    const names = Object.keys(SHAPE);
    for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) {
      const a = names[i], b = names[j];
      if (SHAPE[a] !== SHAPE[b]) continue;   // a hollow vs a filled dot: FORM separates them, no colour floor
      const full = deltaE(rgb[a], rgb[b]), c = cvdWorst(rgb[a], rgb[b]);
      const key = name + "|" + [a, b].sort().join("|");
      if (key in CONCEDE) {
        const rec = CONCEDE[key];   // a conceded pair: guard BOTH figures at the measured value less 0.3, so a drift on EITHER axis reds (LOW a)
        assert.ok(full >= rec.full - 0.3, `${name}: ${a} vs ${b} (conceded pre-existing miss) full ${full.toFixed(1)} < ${(rec.full - 0.3).toFixed(1)} (drift below the recorded ${rec.full})`);
        assert.ok(c >= rec.def - 0.3, `${name}: ${a} vs ${b} (conceded pre-existing miss) deficiency ${c.toFixed(1)} < ${(rec.def - 0.3).toFixed(1)} (drift below the recorded ${rec.def})`);
      } else {
        assert.ok(full >= 15 && c >= 8, `${name}: ${a} vs ${b} (same shape ${SHAPE[a]}) reads ${full.toFixed(1)} full / ${c.toFixed(1)} deficiency; below 15/8 and not conceded`);
      }
    }
  }
});

test("the shape cue reaches the tag-overview row pip and the collapsed-group header pip: retrying is a HOLLOW amber ring there too, told from the filled working/awaiting pips by form (the keycap-width PR's carried PR 2080 point)", () => {
  const css = read("styles.css");
  const body = (re: RegExp, what: string) => { const m = css.match(re); assert.ok(m, what + " rule present"); return m![1]; };
  const nc = (x: string) => x.replace(/\/\*[\s\S]*?\*\//g, "");
  // The row pip (.snap-pip.retrying) and the header pip (.tab-group-pip.retrying) reuse the tab dot's status tokens, so
  // retrying's amber cannot clear the categorical deficiency floor against the filled working gold (light 3.3) or the
  // awaiting green (dark 2.7 on the overview) by COLOUR alone. Both are now HOLLOW amber rings, the tab dot's shape cue,
  // so FORM tells them apart. RED at the base, where both were filled amber discs.
  for (const [sel, label] of [["\\.snap-pip\\.retrying", "the tag-overview row pip"], ["\\.tab-group-pip\\.retrying", "the collapsed-group header pip"]] as const) {
    const rule = nc(body(new RegExp("\\n" + sel + " \\{([^}]*)\\}"), label));
    assert.match(rule, /background:\s*transparent/, label + " retrying is HOLLOW (a transparent fill), not a filled amber disc");
    assert.match(rule, /box-shadow:\s*inset 0 0 0 [\d.]+px var\(--st-retrying-bg\)/, label + " is an inset amber ring (the tab dot's shape cue)");
  }
  // the states the pips paint as FILLED discs stay filled (no ring), so the shape difference from hollow retrying is real
  for (const sel of ["\\.snap-pip\\.working", "\\.snap-pip\\.awaiting", "\\.tab-group-pip\\.blocked"]) {
    const rule = nc(body(new RegExp("\\n" + sel + " \\{([^}]*)\\}"), sel));
    assert.match(rule, /background:\s*var\(--st-/, sel + " is a FILLED disc");
    assert.doesNotMatch(rule, /box-shadow/, sel + " has no ring (a filled disc, a distinct shape from hollow retrying)");
  }
});
