// Chat TEXT schemes (the user 2026-08-24): raise body-text contrast without collapsing the
// tool-dimmer-than-prose hierarchy, pickable in the gear, persisted in romp:settings, applied live.
// The tier audit found the chat's text riding THREE variables — --fg (prose, 78 uses), --dim (tool
// heads/commands/meta, 127 uses), --code-fg (code) — so a scheme is just a tier set on a body class.
// settings.ts is executable; the CSS/render/gear wiring is pinned at the source (repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { chatScheme, loadSettings, saveSettings, DEFAULT_SETTINGS } from "./settings";

const UI = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const GEAR = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.js"), "utf8");

// minimal localStorage for the executable round-trip
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
};

test("executable: the scheme persists round-trip, unknown/legacy values normalize to default", () => {
  store.clear();
  assert.equal(DEFAULT_SETTINGS.chatScheme, "default");
  assert.equal(loadSettings().chatScheme, "default", "a fresh install is Default — zero visual change");
  saveSettings({ chatScheme: "solarized-dark" });
  assert.equal(loadSettings().chatScheme, "solarized-dark", "the pick survives a reload (localStorage)");
  store.set("romp:settings", JSON.stringify({ chatScheme: "neon-vaporwave" }));
  assert.equal(loadSettings().chatScheme, "default", "junk in the blob can never wedge the chat unreadable");
  assert.equal(chatScheme(undefined), "default");
  assert.equal(chatScheme("high-contrast"), "high-contrast");
});

const tierBlock = (cls: string) => {
  const m = CSS.match(new RegExp("body\\." + cls + " \\{([^}]*)\\}", "s"));
  return m ? m[1] : null;
};
const tier = (block: string, name: string) => {
  const m = block.match(new RegExp("--" + name + ": (#[0-9a-fA-F]{6})"));
  return m ? m[1] : null;
};
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};

test("Default IS today's values: the :root tiers are untouched and no scheme-default rule exists", () => {
  assert.match(CSS, /--fg: var\(--vscode-foreground, #cccccc\);/);
  assert.match(CSS, /--dim: var\(--vscode-descriptionForeground, #9a9a9a\);/);
  assert.match(CSS, /--code-fg: #e1c08d;/);
  assert.doesNotMatch(CSS, /scheme-default/, "default applies NOTHING — absence is the guarantee");
});

test("every scheme defines ALL three tiers, and the dim tier stays measurably dimmer than prose", () => {
  for (const cls of ["scheme-high-contrast", "scheme-solarized-dark"]) {
    const block = tierBlock(cls);
    assert.ok(block, cls + " block exists");
    const fg = tier(block!, "fg"), dim = tier(block!, "dim"), code = tier(block!, "code-fg");
    assert.ok(fg && dim && code, cls + " defines --fg, --dim, --code-fg");
    assert.ok(lum(dim!) < lum(fg!) * 0.75, cls + ": the hierarchy holds — dim visibly below prose, never collapsed");
  }
});

test("high contrast actually raises contrast: prose and dim both sit above the defaults", () => {
  const block = tierBlock("scheme-high-contrast")!;
  assert.ok(lum(tier(block, "fg")!) > lum("#cccccc"), "prose brighter than today's default");
  assert.ok(lum(tier(block, "dim")!) > lum("#9a9a9a"), "the dim tier lifts with it — proportionally, not collapsed");
});

test("Solarized Light is deliberately absent — an unreadable preset is worse than none", () => {
  assert.doesNotMatch(CSS, /solarized-light/i);
  assert.doesNotMatch(GEAR, /solarized-light/i);
});

test("the scheme applies live as a body class, at startup and on every settings change", () => {
  assert.match(UI, /function applyChatScheme\(s: RompSettings\): void \{/);
  assert.match(UI, /classList\.toggle\("scheme-high-contrast", s\.chatScheme === "high-contrast"\);/);
  assert.match(UI, /classList\.toggle\("scheme-solarized-dark", s\.chatScheme === "solarized-dark"\);/);
  assert.match(UI, /applyChatScheme\(settings\);   \/\/ the persisted pick applies at startup — it survives reloads/);
  assert.match(UI, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\); renderTabs\(\); rerenderAll\(\); refillOpenCommentPop\(\); \}\);/);
});

test("the gear picker is PREVIEW CARDS: each row painted with its own tiers, current \u2713-marked, click applies", () => {
  // the user 2026-08-24, on the live check: "I need to see a preview of the solarized stuff in the
  // selection" — a native <select> can't paint options, so each scheme is a card sampling its own
  // prose/tool/code tiers; the current pick wears the menu vocabulary's ✓ (#1EA1EB)
  assert.match(GEAR, /<div id=rs-chatscheme style='margin-top:5px;display:flex;flex-direction:column;gap:4px'><\/div>/);
  assert.match(GEAR, /var SCHEMES = \[/);
  assert.match(GEAR, /row\.addEventListener\('click', function \(\) \{ var s = load\(\); s\.chatScheme = sc\.id; save\(s\); csPaint\(\); \}\);/,
    "save() dispatches romp:settings + settingsSync — the chat re-applies live, event-based");
  assert.match(GEAR, /#1EA1EB/);
  assert.match(GEAR, /csPaint\(\);/);
});

test("the gear's preview hexes mirror styles.css exactly — the cards can never lie about a scheme", () => {
  const gearRow = (id: string) => {
    const m = GEAR.match(new RegExp("\\{ id: '" + id + "'[^}]*\\}"));
    assert.ok(m, id + " row exists in the gear's SCHEMES table");
    const g = (k: string) => (m![0].match(new RegExp(k + ": '(#[0-9a-fA-F]{6})'")) || [])[1];
    return { fg: g("fg"), dim: g("dim"), code: g("code") };
  };
  for (const [id, cls] of [["high-contrast", "scheme-high-contrast"], ["solarized-dark", "scheme-solarized-dark"]] as const) {
    const block = tierBlock(cls)!;
    const want = { fg: tier(block, "fg"), dim: tier(block, "dim"), code: tier(block, "code-fg") };
    assert.deepEqual(gearRow(id), want, id + ": gear preview === styles.css tiers");
  }
  // the Default card previews the stock :root values
  assert.deepEqual(gearRow("default"), { fg: "#cccccc", dim: "#9a9a9a", code: "#e1c08d" });
});

test("no scheme's prose ever drops below the default's luminance — a 'scheme' that darkens the chat is a regression", () => {
  // the user 2026-08-24, live check on the first Solarized Dark cut (base0 prose, ≈0.28 luminance vs
  // the default's ≈0.60): "it's darkening everything … the exact opposite of what I wanted". A text
  // scheme on romp's dark ground exists to RAISE readability; base2 is solarized's bright content
  // tone and the right prose tier here.
  for (const cls of ["scheme-high-contrast", "scheme-solarized-dark"]) {
    const block = tierBlock(cls)!;
    assert.ok(lum(tier(block, "fg")!) >= lum("#cccccc"), cls + ": prose at or above the default");
    assert.ok(lum(tier(block, "dim")!) >= lum("#9a9a9a") * 0.9, cls + ": the dim tier never sinks below its default either");
  }
});
