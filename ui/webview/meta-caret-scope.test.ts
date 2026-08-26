// The Fast toggle's caret floated mid-sidebar (the user 2026-08-25): the version-submenu work added
// a LATER, bare `.meta-caret { position: absolute; … }` for menu rows — same specificity as the
// statusline-badge caret rule above it, so source order won and every badge's ▾ absolutely
// positioned against some distant ancestor. The row rule is now SCOPED to `.meta-item .meta-caret`
// (the row is the position:relative anchor), and this pins the TIEBREAK, not just existence — the
// css-state-rules-must-win-cascade lesson.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the badge caret stays in flow; only MENU-ROW carets are absolutely positioned", () => {
  // the badge rule: in-flow, sized/dimmed only
  assert.match(CSS, /\.meta-caret \{ font-size: 0\.78em; opacity: 0\.65; \}/);
  // the row rule: scoped under .meta-item (its position:relative anchor)
  assert.match(CSS, /\.meta-item \.meta-caret \{ position: absolute; right: 22px; top: 50%;/);
  // the cascade pin: NO bare .meta-caret rule may carry position — a later unscoped one is exactly
  // the regression this guards (scan every bare-selector block for the property)
  for (const m of CSS.matchAll(/(^|\n)\.meta-caret \{([^}]*)\}/g)) {
    assert.ok(!/position\s*:/.test(m[2]), "a bare .meta-caret rule must never set position: " + m[2].trim());
  }
  // …and .meta-item is the anchor the scoped rule assumes
  assert.match(CSS, /\.meta-item \{[^}]*position: relative;/s);
});
