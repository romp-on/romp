// A permission-mode selector sits to the LEFT of the model name in the statusline, a badge+dropdown
// like the model/effort pickers (the user 2026-06-16). There's no /mode slash command, so the host
// sets it by shift+tab cycling; the webview just posts setMode like setModel/setEffort. Source-level pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("MetaKind includes mode; the status carries it; there's a MODE_CHOICES menu", () => {
  assert.match(RENDER, /type MetaKind = "mode" \| "model" \| "effort"/);
  assert.match(RENDER, /mode\?: string;/);                       // Status.mode
  assert.match(RENDER, /const MODE_CHOICES/);
});

test("the mode button renders FIRST (left of model) and the picker posts setMode", () => {
  assert.match(RENDER, /if \(st\.mode\) meta\.appendChild\(metaButton\("mode", prettyMode\(st\.mode\), forSid\)\);\s*\n\s*if \(st\.model\)/);   // sid-scoped for the popover statusline (2026-08-25)
  assert.match(RENDER, /"setMode"/);
  assert.match(RENDER, /const META_CHOICES: Record<MetaKind/);   // model/effort + mode share the menu path
});

test("Bypass is offered, and offered ONLY on an SDK session", () => {
  // The SDK sets the mode outright (set_permission_mode), so bypassPermissions is reachable there; a
  // tmux session has nothing but the shift+tab cycle, which cannot express it. Listing it on tmux would
  // be a menu entry that silently does nothing — the state this same change made the kernel refuse.
  assert.match(RENDER, /value: "bypassPermissions", sdkOnly: true/);
  assert.match(RENDER, /\.filter\(\(c\) => !c\.sdkOnly \|\| s\.status\.backend === "sdk"\)/);
});

test("Bypass carries a sub-line saying what it costs", () => {
  // Not decoration: it is the one mode that removes the gate instead of moving it, and it also takes
  // romp's approve/deny cards with it (they render from can_use_tool, which bypass never fires).
  assert.match(RENDER, /sub: "every tool runs unasked, and romp stops showing approvals"/);
  assert.match(RENDER, /interface MetaChoice \{[^}]*sub\?: string; sdkOnly\?: boolean/);
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.meta-item-sub \{ font-size: 0\.82em; opacity: 0\.6; \}/,
    "one sub-line size across every romp menu — same as .ctx-item-sub");
});
