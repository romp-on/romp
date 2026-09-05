// The outline pane rebuilds only when it can be seen (2026-09-04). The dashboard shell keeps it in a
// display:none iframe by default, yet every feed push rebuilt its whole list on the main thread the chat
// pane's clicks share. The first content still paints through while hidden, so revealing the pane shows
// the list at once instead of the pane loader fading over nothing. Pinned at the source, like the other
// pane-wiring tests.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "fleet.ts"), "utf8");

test("render() defers while the list is off screen, lets the first content through, and paints once when shown", () => {
  assert.match(SRC, /function render\(\) \{[\s\S]*?if \(!paneWatching\) \{ paneWatching = true; watchPaneVisibility\(list\); \}/);
  assert.match(SRC, /if \(!paneVisible && list\.childElementCount > 0\) \{ paneDirty = true; return; \}/);
  assert.match(SRC, /new IntersectionObserver\(\(entries\) => \{\n\s*paneVisible = entries\.some\(\(e\) => e\.isIntersecting\);\n\s*if \(paneVisible && paneDirty\) \{ paneDirty = false; render\(\); \}/);
  assert.match(SRC, /if \(typeof IntersectionObserver === "undefined"\) return;/, "no observer → always render, as before");
  assert.match(SRC, /let paneVisible = true;/, "visible until told otherwise: the first paint is never withheld");
});
