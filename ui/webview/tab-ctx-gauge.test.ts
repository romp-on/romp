// Each chat tab wears a slim VERTICAL context gauge beside the session name (the user 2026-08-08):
// the statusline battery's fill % + global-colormap colour, rotated upright, no % text — so "this
// session is filling up" reads at a glance across the whole strip. Space for it comes from tightening
// the strip (inter-tab gap 0, smaller in-tab gap/padding), with the ✕ pushed to the tab's right edge
// by DOM order (dot · name · gauge · ✕). WHEN it shows is a gear → Chat picker (the user 2026-08-08
// v2, replacing the on/off toggle): only once ≥50% full (the DEFAULT — a gauge on every quiet tab is
// clutter), always, or never. Source-pin (no jsdom for the tab-bar draw path, like the sibling
// tab-*.test.ts); the mode normalization is pure and unit-tested directly.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { tabCtxMode, DEFAULT_SETTINGS } from "./settings";

const WEBVIEW = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(WEBVIEW, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(WEBVIEW, "styles.css"), "utf8");
const SETTINGS = fs.readFileSync(path.join(WEBVIEW, "settings.ts"), "utf8");
const GEAR = fs.readFileSync(path.join(WEBVIEW, "gear.js"), "utf8");

test("renderTabs appends the gauge after the label, gated on the mode + a reported ctx%", () => {
  assert.match(RENDER, /settings\.tabCtx !== "never" && s\.status\.ctx && st !== "compacting" && st !== "closed"/);
  // the 50% threshold: over50 (the default) shows the gauge only once it has news
  assert.match(RENDER, /settings\.tabCtx === "always" \|\| pct >= 50/);
  assert.match(RENDER, /tab\.appendChild\(tabCtxGauge\(s\.status\.ctx, s\.status\.ctxColor\)\)/);
  // DOM order: label first, then the gauge, then the ✕ — so the ✕ sits at the tab's right edge
  const label = RENDER.indexOf("tab.appendChild(label);");
  const gauge = RENDER.indexOf("tab.appendChild(tabCtxGauge(");
  const close = RENDER.indexOf('const close = el("span", "tab-close");');
  assert.ok(label >= 0 && label < gauge && gauge < close, "expected label < gauge < close in renderTabs");
});

test("the gauge mirrors setCtxBar: clamped %, server ctxColor, the same traffic-light fallback", () => {
  assert.match(RENDER, /function tabCtxGauge\(ctxStr: string, ctxColor\?: number\[\]\)/);
  assert.match(RENDER, /Math\.max\(0, Math\.min\(100, parseInt\(ctxStr, 10\) \|\| 0\)\)[\s\S]{0,400}fill\.style\.height = pct \+ "%"/);
  // colormap colour when the kernel ships one; setCtxBar's exact fallback ramp when it doesn't
  assert.match(RENDER, /tabCtxGauge[\s\S]{0,600}ctxColor && ctxColor\.length === 3\) \? `rgb\(\$\{ctxColor\.join\(","\)\}\)`\s*\n\s*: \(pct >= 85 \? "#c0392b" : pct >= 60 \? "#e0b020" : "#54B204"\)/);
});

test("the gauge is a slim VERTICAL bar and the strip tightened to make room for it", () => {
  // vertical: markedly taller than wide, fill anchored to the bottom
  const g = CSS.match(/\.tab-ctx \{[^}]*width: (\d+)px; height: (\d+)px/);
  assert.ok(g, ".tab-ctx must declare width+height");
  assert.ok(+g![1] < +g![2], `gauge must be vertical (w ${g![1]} < h ${g![2]})`);
  assert.match(CSS, /\.tab-ctx-fill \{ position: absolute; left: 0; right: 0; bottom: 0/);
  // the strip's spacing trade (the user 2026-08-08): inter-tab gap 0; in-tab gap/padding shrunk
  assert.match(CSS, /#tabs \{ display: flex; flex: 1 1 auto; flex-wrap: wrap; align-items: stretch; gap: 0; \}/);
  assert.match(CSS, /\.tab \{[\s\S]{0,400}gap: 4px;[^\n]*\n\s*padding: 6px 7px;/);
});

test("the mode defaults to over50 and normalizes the boolean-era store", () => {
  assert.equal(DEFAULT_SETTINGS.tabCtx, "over50");
  assert.equal(tabCtxMode("always"), "always");
  assert.equal(tabCtxMode("never"), "never");
  assert.equal(tabCtxMode("over50"), "over50");
  assert.equal(tabCtxMode(false), "never", "boolean-era false was an explicit hide");
  assert.equal(tabCtxMode(true), "over50", "boolean-era true was the shipped default nobody chose");
  assert.equal(tabCtxMode(undefined), "over50", "a fresh store gets the default");
  // loadSettings applies it, so render/gear consumers always see a mode
  assert.match(SETTINGS, /s\.tabCtx = tabCtxMode\(s\.tabCtx\);/);
});

test("gear → Chat picker (When above 50% / Always / Never), persisted as settings.tabCtx", () => {
  assert.match(GEAR, /id=rs-tabctx/);
  assert.match(GEAR, /<option value=over50>When above 50%<\/option><option value=always>Always<\/option><option value=never>Never<\/option>/);
  assert.match(GEAR, /s\.tabCtx = tc\.value; save\(s\);/);
  // modal reopens showing the stored value, normalized (gear.js can't import the TS module)
  assert.match(GEAR, /tc\.value = tabCtxMode\(s\.tabCtx\);/);
  assert.match(GEAR, /function tabCtxMode\(v\) \{ return \(v === 'always' \|\| v === 'never'\) \? v : \(v === false \? 'never' : 'over50'\); \}/);
});

test("a gear change repaints the tab strip live, not on the next kernel push", () => {
  assert.match(RENDER, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\); renderTabs\(\); rerenderAll\(\); refillOpenCommentPop\(\); \}\)/);
});
