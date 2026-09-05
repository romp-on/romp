// T226 (the user 2026-09-02, screenshot: in the light theme the settings' Theme select opened a
// near-black card with dark-on-dark options — the way back to dark was unreadable). The menu
// vocabulary (CLAUDE.md "Menus and dropdowns wear ONE vocabulary") had been pinned as LITERAL hex
// in every inline menu string (the settings pickers, the tag menu), so the light block could never
// reach it. The skin is TOKENS now — --menu-bg / --menu-fg / --menu-border / --menu-hover beside the
// existing --radius-menu / --shadow-menu / --check-bg — defined in both theme blocks of both
// self-sufficient sheets; dark resolves byte-for-byte to the literals the rule always named, the
// light block re-skins in its own palette, and inline strings carry the dark literal only as the
// var() FALLBACK (a file:// harness / a foreign host loads no sheet). The ✓ mark is themed too (the
// manager's ruling): dark keeps #1EA1EB, light wears the clay the timeline's palette already drew.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const CHAT = ui("webview", "styles.css");
const FEED = ui("webview", "feed.css");
const GEAR = ui("webview", "gear.js");
const MENU = ui("webview", "tag-menu.ts");
const TIMELINE = ui("romp-timeline-view.js");
const GEAR_CSS = ui("webview", "gear.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

/** The body of the FIRST rule whose selector line starts with `selector {` — brace-depth scan, so a
 *  comment containing braces inside the block cannot end it early. */
function block(css: string, selector: string): string {
  const at = css.indexOf("\n" + selector + " {");
  assert.ok(at >= 0, "rule present: " + selector);
  let i = css.indexOf("{", at), depth = 0;
  for (let j = i; j < css.length; j++) {
    if (css[j] === "{") depth++;
    else if (css[j] === "}" && --depth === 0) return css.slice(i + 1, j);
  }
  throw new Error("unterminated rule: " + selector);
}
/** `var(--x, <fallback>)` → `var(--x)`: the fallback is the DARK literal every inline string may
 *  carry for sheet-less hosts; what is left must reference tokens only. One level of nested parens
 *  (an rgba() fallback) is understood. */
const stripFallbacks = (s: string) => s.replace(/var\((--[\w-]+)\s*,\s*(?:[^()]|\([^()]*\))*\)/g, "var($1)");
const slice = (src: string, from: string, to: string) => {
  const a = src.indexOf(from), b = src.indexOf(to, a + 1);
  assert.ok(a >= 0 && b > a, "slice anchors present: " + from.slice(0, 40) + " … " + to.slice(0, 40));
  return src.slice(a, b);
};
const norm = (s: string) => s.replace(/\s+/g, "").toLowerCase();

const MENU_TOKENS = ["--menu-bg", "--menu-fg", "--menu-border", "--menu-hover", "--menu-ring", "--check-ring"];
// the dark spec — the literals the CLAUDE.md rule always named, now the dark theme's token values
const DARK = {
  "--menu-bg": "var(--vscode-menu-background, #252526)",
  "--menu-fg": "var(--vscode-menu-foreground, #cccccc)",
  "--menu-border": "rgba(255, 255, 255, 0.12)",
  "--menu-hover": "rgba(255, 255, 255, 0.09)",
  "--check-bg": "#1EA1EB",
  "--menu-ring": "#fff",
  "--check-ring": "rgba(30, 161, 235, 0.55)",
};
const LIGHT = {
  "--menu-bg": "#FBF6EF",           // the light block's own menu card (its --vscode-menu-background stand-in)
  "--menu-fg": "#1F1E1D",
  "--menu-border": "rgba(0, 0, 0, 0.12)",
  "--menu-hover": "rgba(0, 0, 0, 0.06)",
  "--check-bg": "#C2410C",          // the light theme's clay — the mark the timeline's PAL_LIGHT already drew
  "--menu-ring": "#1F1E1D",
  "--check-ring": "rgba(194, 65, 12, 0.55)",
};

test("the menu skin tokens live in BOTH theme blocks of BOTH sheets — dark byte-for-byte the old literals", () => {
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    const root = block(css, ":root"), light = block(css, "body.theme-light");
    for (const [tok, val] of Object.entries(DARK))
      assert.ok(root.includes(`${tok}: ${val};`), `${name} :root ${tok} = ${val}`);
    for (const [tok, val] of Object.entries(LIGHT))
      assert.ok(light.includes(`${tok}: ${val};`), `${name} body.theme-light ${tok} = ${val}`);
    // key parity for the menu set: a token one block defines and the other forgets is a theme leak
    for (const tok of MENU_TOKENS) {
      assert.equal((root.match(new RegExp(tok + ":", "g")) || []).length, 1, `${name} :root defines ${tok} once`);
      assert.equal((light.match(new RegExp(tok + ":", "g")) || []).length, 1, `${name} light defines ${tok} once`);
    }
  }
});

// Every menu SURFACE: the sheets' menu rules and the inline-styled menus. Each is stripped of its
// var() fallbacks and must then carry none of the dark spec's literals — those belong to the theme
// definitions (the two blocks above; PAL_DARK in the timeline) and nowhere else.
const DARK_LITERALS: Array<[string, RegExp]> = [
  ["#252526 card", /#252526/i],
  // ANY white-alpha wash is dark-only by construction (review round: the 0.12/0.09-only pair let a 0.25 through)
  ["white-alpha hairline/hover/wash", /rgba\(\s*255\s*,\s*255\s*,\s*255\s*,/i],
  ["rgba(0,0,0,0.35) shadow", /rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0?\.35\s*\)/],
  ["#cccccc text", /#cccccc\b/i],
  ["#ccc text", /#ccc\b/i],
  ["#1EA1EB check", /#1EA1EB/i],
  ["#1e1e1e input", /#1e1e1e\b/i],
  ["#3a3a3a input border", /#3a3a3a\b/i],
];
const SURFACES: Array<[string, string]> = [
  ["styles.css .ctx-menu", block(CHAT, ".ctx-menu")],
  ["styles.css .ctx-item:hover", block(CHAT, ".ctx-item:hover")],
  ["styles.css .ctx-tag-input", CHAT.slice(CHAT.indexOf("\n.ctx-tag-input {"), CHAT.indexOf("}", CHAT.indexOf("\n.ctx-tag-input {")))],
  ["styles.css .meta-menu", block(CHAT, ".meta-menu")],
  ["styles.css .meta-item", block(CHAT, ".meta-item")],
  ["styles.css .meta-item:hover", CHAT.slice(CHAT.indexOf("\n.meta-item:hover {"), CHAT.indexOf("}", CHAT.indexOf("\n.meta-item:hover {")))],
  ["styles.css .meta-item.current::after", block(CHAT, ".meta-item.current::after")],
  ["styles.css .ctx-sub .ctx-item.current::after", block(CHAT, ".ctx-sub .ctx-item.current::after")],
  ["feed.css .ctx-menu", block(FEED, ".ctx-menu")],
  ["feed.css .ctx-item:hover", block(FEED, ".ctx-item:hover")],
  ["gear.js housePick (the settings pickers — the Theme select)", slice(GEAR, "function housePick(", "var SCHEMES = [")],
  // from MSTYLE: the menu CARD and its rows — the trigger BUTTON above it is a closed-state control, not a menu
  ["gear.js versionMenu (model pickers + version submenus)", slice(GEAR, "    var MSTYLE = ", "versionMenu(jm);")],
  ["gear.css #rs-cmap-list", GEAR_CSS.slice(GEAR_CSS.indexOf("\n#rs-cmap-list {"), GEAR_CSS.indexOf("}", GEAR_CSS.indexOf("\n#rs-cmap-list {")))],
  ["gear.css #rs-pal-list", GEAR_CSS.slice(GEAR_CSS.indexOf("\n#rs-pal-list {"), GEAR_CSS.indexOf("}", GEAR_CSS.indexOf("\n#rs-pal-list {")))],
  ["feed.css .feed-sessmenu .fsm-row:hover", FEED.slice(FEED.indexOf("\n.feed-sessmenu .fsm-row:hover {"), FEED.indexOf("}", FEED.indexOf("\n.feed-sessmenu .fsm-row:hover {")))],
  ["styles.css .ctx-swatch.sel", CHAT.slice(CHAT.indexOf("\n.ctx-swatch.sel {"), CHAT.indexOf("}", CHAT.indexOf("\n.ctx-swatch.sel {")))],
  ["kernel.py mobile session picker (#mlist)", KERNEL.slice(KERNEL.indexOf('"#mlist{display:none;'), KERNEL.indexOf('".mrow.ph'))],
  // the shell's bell popover (2026-09-05): rows + switch pills + the test button, up to the status-red
  // refusal line (a STATUS colour, outside the menu vocabulary by the CLAUDE.md accent/status split)
  ["kernel.py bell popover (#rbell-pop)", KERNEL.slice(KERNEL.indexOf('"#rbell-back{'), KERNEL.indexOf('"#rbp-test-out.bad{'))],
  ["tag-menu.ts openTagMenu", slice(MENU, "export function openTagMenu", "export function tagMenuButton")],
  ["timeline menuStyleFor/menuCheckStyleFor", slice(TIMELINE, "const menuStyleFor", "let MENU_STYLE")],
];

test("no menu surface carries a raw dark literal outside the theme definitions (the fallback slot excepted)", () => {
  for (const [name, src] of SURFACES) {
    const bare = stripFallbacks(src);
    for (const [what, re] of DARK_LITERALS)
      assert.doesNotMatch(bare, re, `${name}: ${what} written raw — it belongs in the theme blocks; reference the token`);
  }
});

test("the inline menus wear the tokens — card, text, hairline, hover, radius, shadow, ✓ — with the dark literal as fallback", () => {
  const pick = slice(GEAR, "function housePick(", "var SCHEMES = [");
  const vers = slice(GEAR, "function versionMenu(", "versionMenu(jm);");
  const tag = slice(MENU, "export function openTagMenu", "export function tagMenuButton");
  for (const [name, src] of [["housePick", pick], ["versionMenu", vers], ["tag menu", tag]] as const) {
    assert.match(src, /background:\s*var\(--menu-bg, #252526\)/, name + " card");
    assert.match(src, /color:\s*var\(--menu-fg, #cccccc\)/, name + " text");
    assert.match(src, /border:\s*1px solid var\(--menu-border, rgba\(255,255,255,0\.12\)\)/, name + " hairline");
    assert.match(src, /border-radius:\s*var\(--radius-menu, 6px\)/, name + " radius");
    assert.match(src, /box-shadow:\s*var\(--shadow-menu, 0 4px 12px rgba\(0,0,0,0\.35\)\)/, name + " shadow");
    assert.match(src, /var\(--menu-hover, rgba\(255,255,255,0\.09\)\)/, name + " row hover");
    assert.match(src, /background:\s*var\(--check-bg, #1EA1EB\)/, name + " ✓ mark");
  }
  // fallback PARITY: every inline fallback equals the dark theme's resolved value (whitespace aside),
  // so a sheet-less host renders exactly the dark spec — never a third skin
  // a token whose dark value COMPOSES a VS Code var (--menu-bg/--menu-fg) resolves, sheet-less, to its own
  // innermost fallback; a literal value (rgba/hex) is already the resolved value
  const innermost = (v: string) => { const m = v.startsWith("var(") ? v.match(/,\s*(.+)\)\s*$/) : null; return m ? m[1] : v; };
  const GEOMETRY: Record<string, string> = { "--radius-menu": "6px", "--shadow-menu": "0 4px 12px rgba(0, 0, 0, 0.35)" };
  for (const src of [pick, vers, tag]) {
    for (const m of src.matchAll(/var\((--menu-[\w-]+|--check-bg|--radius-menu|--shadow-menu),\s*((?:[^()]|\([^()]*\))*)\)/g)) {
      const tok: string = m[1], fb = m[2];
      const want = GEOMETRY[tok] ?? innermost(DARK[tok as keyof typeof DARK]);
      assert.equal(norm(fb), norm(want), `${tok} fallback ${fb} must equal the dark token value ${want}`);
    }
  }
});

test("the ✓ mark is themed through --check-bg on every surface: dark #1EA1EB, light the clay the timeline draws", () => {
  // the sheets' checks read the token
  assert.match(block(CHAT, ".meta-item.current::after"), /background: var\(--check-bg\)/);
  assert.match(block(CHAT, ".ctx-sub .ctx-item.current::after"), /background: var\(--check-bg\)/);
  assert.match(FEED, /\.feed-viewmenu \.ctx-item\.current::after \{[^}]*var\(--check-bg\)/s, "the feed's view menu check");
  // the timeline's palette IS its theme definition — its two values are the two blocks' values
  assert.match(TIMELINE, /const PAL_DARK = \{[^}]*?accentSolid: '#1EA1EB'/, "PAL_DARK ✓ = the dark token");
  assert.match(TIMELINE, /const PAL_LIGHT = \{[^}]*?accentSolid: '#C2410C'/, "PAL_LIGHT ✓ = the light token");
  assert.match(TIMELINE, /background:' \+ p\.accentSolid \+ '/, "menuCheckStyleFor reads the palette, never a literal");
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.ok(block(css, ":root").includes("--check-bg: #1EA1EB;"), name + " dark ✓ pinned byte-for-byte");
    assert.ok(block(css, "body.theme-light").includes("--check-bg: #C2410C;"), name + " light ✓ = the clay accent");
    assert.ok(block(css, "body.theme-light").includes("--accent: #C2410C;"), name + " …which IS the light accent (one clay)");
  }
});

test("the CLAUDE.md rule names the tokens and keeps the hex as the dark theme's values", () => {
  const md = fs.readFileSync(path.resolve(process.cwd(), "..", "CLAUDE.md"), "utf8");
  const rule = slice(md, "### Menus and dropdowns wear ONE vocabulary", "The romp accent is light blue");
  for (const tok of ["--menu-bg", "--menu-fg", "--menu-border", "--menu-hover", "--radius-menu", "--shadow-menu", "--check-bg"])
    assert.ok(rule.includes("`" + tok + "`"), "the rule names " + tok);
  assert.ok(rule.includes("`#252526`") && rule.includes("`#1EA1EB`"), "the dark values stay documented");
  assert.match(rule, /fallback/i, "the one sanctioned place for a raw hex in a menu string");
});

test("the sheets' reference menu rules wear the tokens too — one card, one hover, one ring (review round)", () => {
  // .ctx-menu is the rule the CLAUDE.md text names as the spec; it and .meta-menu read --menu-bg/--menu-fg
  // (served dark: byte-identical — the token composes the VS Code colour they always followed).
  // .ctx-item:hover stays on --vscode-menu-selectionBackground BY DESIGN: the tab menu is "themed
  // like VS Code's own menus" and the served dark hover is its blue selection — the one sanctioned
  // divergence, named here so it cannot be mistaken for an oversight.
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    assert.match(block(css, ".ctx-menu"), /background: var\(--menu-bg\);/, name + " .ctx-menu card");
    assert.match(block(css, ".ctx-menu"), /color: var\(--menu-fg\);/, name + " .ctx-menu text");
  }
  assert.match(block(CHAT, ".meta-menu"), /background: var\(--menu-bg\);/, ".meta-menu card");
  assert.match(CHAT, /\.ctx-swatch\.sel \{ box-shadow: 0 0 0 2px var\(--menu-bg\), 0 0 0 3\.5px var\(--menu-ring\); \}/, "the current-swatch halo is themed");
  assert.match(CHAT, /\.ctx-tag-input \{[^}]*color: var\(--menu-fg\)/, "the New-tag input reads the MENU text (scheme-invariant, = the old #ccc in dark)");
  assert.match(FEED, /\.feed-sessmenu \.fsm-row:hover \{ background: var\(--menu-hover\); \}/, "the session combobox rows hover with the one wash");
  assert.match(FEED, /\.fask-doneconfirming \{[^}]*border: 1px solid var\(--check-ring\)/, "the done-confirming ring follows the themed ✓");
  for (const id of ["#rs-cmap-list", "#rs-pal-list"]) {
    const at = GEAR_CSS.indexOf("\n" + id + " {"); const rule = GEAR_CSS.slice(at, GEAR_CSS.indexOf("}", at));
    assert.match(rule, /background: var\(--menu-bg, #252526\)/, id + " card"); assert.match(rule, /var\(--shadow-menu, /, id + " shadow");
  }
  // the picker rows this change retokenised read the MENU text, not the chat scheme's --fg
  assert.equal((GEAR.match(/color:var\(--menu-fg, #ccc\)/g) || []).length, 2, "tabCtx + selectPick rows");
  // the mobile session picker (kernel _CHAT_MOBILE_CSS) is a dropdown too: card + hairline on the tokens,
  // and a light block for the values that have no byte-identical dark token
  const mob = KERNEL.slice(KERNEL.indexOf('"#mlist{display:none;'), KERNEL.indexOf('".mrow.ph'));
  assert.match(mob, /background:var\(--menu-bg,#252526\)/, "#mlist card");
  assert.match(mob, /border:1px solid var\(--hairline,#3a3a3a\)/, "#mlist hairline (dark #3a3a3a byte-identical)");
  assert.match(KERNEL, /body\.theme-light #mlist\{/, "the light block re-skins the mobile picker");
});

test("the shell DEFINES the menu tokens (it loads no sheet) and the bell popover reads them with the dark fallbacks", () => {
  // the shell's two theme blocks resolve the tokens to the SAME values the sheets' blocks do —
  // dark byte-for-byte the CLAUDE.md literals, light the cream card — so the popover is one more
  // wearer of the one vocabulary, not a third skin (2026-09-05)
  const dark = KERNEL.slice(KERNEL.indexOf('":root{--menu-bg:'), KERNEL.indexOf('}"', KERNEL.indexOf('":root{--menu-bg:')));
  for (const [tok, val] of Object.entries(DARK)) {
    if (tok === "--menu-ring" || tok === "--check-ring") continue;          // the popover draws no swatch grid
    assert.ok(norm(dark).includes(norm(`${tok}:${innermostOf(val)}`)), `shell :root ${tok} = ${innermostOf(val)}`);
  }
  const light = KERNEL.slice(KERNEL.indexOf('"body.theme-light{--menu-bg:'), KERNEL.indexOf('}"', KERNEL.indexOf('"body.theme-light{--menu-bg:')));
  for (const [tok, val] of Object.entries(LIGHT)) {
    if (tok === "--menu-ring" || tok === "--check-ring") continue;
    assert.ok(norm(light).includes(norm(`${tok}:${val}`)), `shell body.theme-light ${tok} = ${val}`);
  }
  const pop = KERNEL.slice(KERNEL.indexOf('"#rbell-pop{'), KERNEL.indexOf('"#rbp-test-out.bad{'));
  assert.match(pop, /background:var\(--menu-bg,#252526\)/, "popover card");
  assert.match(pop, /color:var\(--menu-fg,#cccccc\)/, "popover text");
  assert.match(pop, /border:1px solid var\(--menu-border,rgba\(255,255,255,0\.12\)\)/, "popover hairline");
  assert.match(pop, /border-radius:var\(--radius-menu,6px\)/, "popover radius");
  assert.match(pop, /box-shadow:var\(--shadow-menu,0 4px 12px rgba\(0,0,0,0\.35\)\)/, "popover shadow");
  assert.match(pop, /background:var\(--menu-hover,rgba\(255,255,255,0\.09\)\)/, "row hover");
  // fallback parity, the same check the inline menus get: every fallback IS the dark token value
  const GEOMETRY: Record<string, string> = { "--radius-menu": "6px", "--shadow-menu": "0 4px 12px rgba(0, 0, 0, 0.35)" };
  for (const m of pop.matchAll(/var\((--menu-[\w-]+|--check-bg|--radius-menu|--shadow-menu),\s*((?:[^()]|\([^()]*\))*)\)/g)) {
    const tok: string = m[1], fb = m[2];
    const want = GEOMETRY[tok] ?? innermostOf(DARK[tok as keyof typeof DARK]);
    assert.equal(norm(fb), norm(want), `${tok} fallback ${fb} must equal the dark token value ${want}`);
  }
});
function innermostOf(v: string): string { const m = v.startsWith("var(") ? v.match(/,\s*(.+)\)\s*$/) : null; return m ? m[1] : v; }
