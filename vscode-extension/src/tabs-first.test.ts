// TABS-FIRST (the user 2026-06-26): the chat tab strip used to skip a tab until its session arrived, so
// tabs popped in one-by-one on a cold load. The kernel's tabOrder push now carries name+color per tab, and
// renderTabs paints the WHOLE strip up front — an id whose session hasn't landed yet draws as a
// non-interactive placeholder that fills in when build_session arrives. Source pins (no jsdom for the strip).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a tabMeta map holds the kernel's name+color per tab", () => {
  assert.match(RENDER, /const tabMeta = new Map<string, \{ name: string; color: Color \| null \}>\(\);/);
});

test("applyTabOrder REBUILDS tabMeta from the authoritative payload (closed tabs don't linger)", () => {
  assert.match(RENDER, /function applyTabOrder\(o: any, tabs\?: any\)/);
  assert.match(RENDER, /if \(Array\.isArray\(tabs\)\) \{\s*tabMeta\.clear\(\);/);
  assert.match(RENDER, /else if \(m\.type === "tabOrder"\) \{ captureViews\(m\.views \|\| null\); applyTabOrder\(m\.order, m\.tabs\); \}/);
});

test("renderTabs renders the union of arrived sessions and tabMeta, placeholders for the rest", () => {
  assert.match(RENDER, /for \(const id of tabMeta\.keys\(\)\)/);
  assert.match(RENDER, /if \(!s\) \{ bar\.appendChild\(makePlaceholderTab\(id\)\); continue; \}/);
});

test("makePlaceholderTab draws name + identity color, and is CLICKABLE while loading", () => {
  assert.match(RENDER, /function makePlaceholderTab\(id: string\): HTMLElement/);
  assert.match(RENDER, /tab\.classList\.add\("colored"\)/);
  // the user 2026-08-25: click a loading tab to be there when it opens — it rides the SAME stable
  // #tabs select delegate as a real tab (activation → MRU + peek), keyboard included; still no
  // close/drag (no session to end yet)
  const fn = RENDER.slice(RENDER.indexOf("function makePlaceholderTab"), RENDER.indexOf("function renderTabs"));
  assert.match(fn, /tab\.dataset\.act = "select";/);
  assert.match(fn, /tab\.tabIndex = 0;/);
  assert.match(fn, /tab\.addEventListener\("keydown", onTabKey\);/);
  assert.ok(!/close|draggable/.test(fn), "no close ✕, no drag — the only new power is selection");
  // …and the ACTIVE loading tab's thread area holds the pane-local romp loader until frames land in place
  assert.match(RENDER, /if \(activeId && tabMeta\.has\(activeId\)\) \{/);
  assert.match(RENDER, /wait\.appendChild\(rompLoaderInner\("opening “" \+ \(tabMeta\.get\(activeId\)\?\.name \|\| "session"\) \+ "”…"\)\);/);
  assert.match(RENDER, /document\.getElementById\("tab-loading"\)\?\.remove\(\);   \/\/ the payload landed — the real view takes over in place/);
});

test("the placeholder shows the mini romp swirl loader (not a whole-tab opacity pulse)", () => {
  // the user 2026-07-03: a still-building tab shows the spinning romp swirl glyph — the loader motif — rather
  // than the old .tab-ph-pulse opacity breathing on the whole tab.
  assert.match(RENDER, /swirl\.src = mediaSrc\("romp-swirl-glyph\.svg"\)/);
  assert.match(RENDER, /tab\.appendChild\(swirl\);/);
  assert.match(CSS, /\.tab\.tab-placeholder \{ cursor: pointer; \}/);   // clickable while loading (2026-08-25)
  assert.match(CSS, /\.tab-ph-swirl \{[\s\S]*?animation: tab-ph-swirl-spin/);
  assert.match(CSS, /@keyframes tab-ph-swirl-spin \{ to \{ transform: rotate\(-360deg\); \} \}/);
  assert.doesNotMatch(CSS, /tab-ph-pulse/, "the whole-tab opacity pulse is gone");
});

test("no white focus ring on tabs: .tab suppresses the UA outline and nothing re-adds a solid one", () => {
  // .tab { ... outline: none ... } suppresses the UA/keyboard focus ring (the user 2026-06-26)
  const tabRule = CSS.slice(CSS.indexOf(".tab {"), CSS.indexOf("}", CSS.indexOf(".tab {")) + 1);
  assert.match(tabRule, /outline: none/, ".tab clears the UA focus ring");
  // and NO rule re-adds a solid focus outline on a tab (that was the white border)
  assert.doesNotMatch(CSS, /\.tab:focus(-visible)?\s*\{[^}]*outline:\s*[0-9]/, "no solid focus outline re-added");
  // the dashed STATE outlines (awaiting/blocked/retrying) are untouched
  assert.match(CSS, /\.tab\.tab-awaiting, \.tab\.tab-blocked, \.tab\.tab-retrying \{ outline: 2px dashed/);
});
