// Compact mode + settings gear wiring (the user 2026-06-14). The pure logic is covered by
// compact.test.ts / settings.test.ts; the chat renderer has no jsdom harness, so — like the other
// webview tests — these pin the DOM wiring at the source level: the compact branch in syncView, the
// tool-group summary line, and the gear → settings modal.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("compact mode folds the stream via compactDisplay, rendered through the unified window path", () => {
  // compact is no longer a separate rebuild path: displayItems() returns compactDisplay's folded units when
  // the setting is on, and renderWindowItems renders them the same way as per-event units.
  assert.match(RENDER, /if \(!settings\.compact\) \{[\s\S]*?\}\s*\n\s*return compactDisplay\(s\.events\.map\(/);
});

test("a collapsed tool run renders bold tool labels via toolCounts and is click-to-expand", () => {
  assert.match(RENDER, /el\("div", "toolgroup-line"\)/);
  assert.match(RENDER, /toolCounts\(tools\.map\(/);
  assert.match(RENDER, /el\("span", "toolgroup-tool"\)/, "each tool word is its own bold span");
  // the "N Edits" summary shows only when collapsed; expanded → just the arrow
  assert.match(RENDER, /if \(!open\) \{/);
  // clicking the line toggles expand → the full non-compact cards for that span, indented
  assert.match(RENDER, /line\.addEventListener\("click",[\s\S]*?toggleToolGroup\(key\)/);
  assert.match(RENDER, /function toggleToolGroup/);
  assert.match(RENDER, /expandedGroups\.has\(key\)/);
  assert.match(RENDER, /classList\.add\("tg-child"\)/, "expanded children are tagged for indent");
  assert.match(CSS, /\.toolgroup-tool \{[^}]*font-weight: 700/);
  assert.match(CSS, /\.tg-child \{[^}]*margin-left/);
});

test("the chat has NO gear of its own — it only consumes the shared setting (gear is on the timeline)", () => {
  assert.doesNotMatch(RENDER, /chat-settings-gear/, "the gear was moved to the timeline");
  // renderTabs rides the change too: the tab strip reads settings (the context gauge toggle,
  // the user 2026-08-08) and rerenderAll only rebuilds the transcript views.
  assert.match(RENDER, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\); renderTabs\(\); rerenderAll\(\); refillOpenCommentPop\(\); \}\)/);
});

test("the + New session button sends the picker's backend toggle, defaulting to the gear's (the user 2026-06-23)", () => {
  // the per-session toggle wins; it RESETS to the gear default (read fresh via loadSettings()) on each open
  assert.match(RENDER, /startCreate\(\{ name, backend: beSel\?\.dataset\.be \|\| loadSettings\(\)\.backend,/);
  assert.match(RENDER, /const def = loadSettings\(\)\.backend \|\| "tmux";/);   // toggle defaults to the gear setting
});
