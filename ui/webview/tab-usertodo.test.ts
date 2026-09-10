// The tab glyph (plans/user-todos.md, slice 2): a session tab with open user todos carries a
// small, NON-NUMERIC glyph — "something here waits on you"; the split card by the composer says
// what. Tabs deliberately carry no counts, and pips (.tab-dot) encode turn state — so the glyph
// is its own element (.tab-usertodo), derived purely from the session payload's userTodos field
// (delta-stable since slice 1), never from feed state or a client-side gate. Source pins, like
// the other tab-strip tests (the renderer has no jsdom harness).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

const renderTabs = RENDER.slice(RENDER.indexOf("function renderTabs"),
  RENDER.indexOf("\nfunction ", RENDER.indexOf("function renderTabs") + 10));

test("the glyph rides the tab, gated purely on the session payload's userTodos", () => {
  assert.match(renderTabs, /if \(s\.userTodos && s\.userTodos\.length\) \{/,
    "payload-driven: build_session already hides an ended session's todos, so no client-side gate");
  assert.match(renderTabs, /el\("span", "tab-usertodo"\)/);
});

test("the glyph is non-numeric and explains itself on hover", () => {
  assert.match(renderTabs, /ut\.textContent = "⚑";/, "one glyph, never a count (tabs carry no counts)");
  assert.match(renderTabs, /ut\.title = /);
  const block = renderTabs.slice(renderTabs.indexOf('el("span", "tab-usertodo")'));
  assert.doesNotMatch(block.slice(0, 300), /userTodos\.length\s*\+|`\$\{[^}]*length/,
    "no count reaches the strip");
});

test("the glyph is not a pip and does not fight the pip/gauge/close layout", () => {
  // pips (.tab-dot) encode turn state and the kernel's mobile scrape keys on '.tab-dot.await';
  // the glyph must be its own vocabulary, placed with the name — after the label, before the
  // ctx gauge helper call — so dot / bar / label / glyph / gauge / ✕ never collide
  const block = renderTabs.slice(renderTabs.indexOf('el("span", "tab-usertodo")'),
    renderTabs.indexOf('el("span", "tab-usertodo")') + 300);
  assert.doesNotMatch(block, /tab-dot/);
  const labelAt = renderTabs.indexOf("tab.appendChild(label)");
  const glyphAt = renderTabs.indexOf('el("span", "tab-usertodo")');
  const gaugeAt = renderTabs.indexOf("appendTabCtxGauge(");   // the strip calls the shared helper for the gauge (skeleton tabs share it)
  assert.ok(labelAt > 0 && glyphAt > 0 && gaugeAt > 0, "all three anchors exist in renderTabs");
  assert.ok(labelAt < glyphAt && glyphAt < gaugeAt, "label, then glyph, then ctx gauge");
});

test("the glyph has a quiet style of its own", () => {
  assert.match(CSS, /\.tab-usertodo \{/);
  assert.doesNotMatch(CSS, /\.tab-dot\.usertodo/, "never a dot variant — pips encode turn state");
  // themed through --dim like .tab-close, so it reads under body.theme-light too: classic tabs
  // have no chip fill, and a hardcoded near-white glyph vanishes on the light theme's page
  const rule = CSS.slice(CSS.indexOf(".tab-usertodo {"), CSS.indexOf("}", CSS.indexOf(".tab-usertodo {")));
  assert.match(rule, /color: var\(--dim\)/, "the glyph's colour is a theme token");
  assert.doesNotMatch(rule, /rgba\(255, 255, 255/, "never hardcoded white");
});

test("the coarse-pointer strip mirrors it (the mobile header scrapes the real tabs)", () => {
  // the phone hides #tabs and rebuilds rows from the desktop DOM (_CHAT_MOBILE_JS), so without a
  // scrape key the glyph simply doesn't exist on mobile
  assert.match(KERNEL, /ut:!!t\.querySelector\('\.tab-usertodo'\)/);
  assert.match(KERNEL, /className='utflag'/);          // per-row flag in the dropdown list
  assert.match(KERNEL, /\.mrow \.utflag\{/);           // …with its own mobile style
  assert.match(KERNEL, /class="utf"/);                 // the current-session button mirrors it too
  assert.match(KERNEL, /#mcur \.utf\{/);               // …styled like the row flag
});

test("the mobile scrape's existing pip keys are untouched (the test_kernel_mobile contract)", () => {
  assert.match(KERNEL, /awaitbg:!!t\.querySelector\('\.tab-dot\.await'\)/);
  assert.match(KERNEL, /working:t\.classList\.contains\('tab-working'\)/);
});
