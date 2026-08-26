// The Stack toggle (the user 2026-08-18): force the feed's one-column layout at ANY width, as a
// standing choice — not only when the container narrows past 540px. The pref drives a style()
// container condition on #feed-list, OR-combined with the existing size query, so the CSS block
// stays the single owner of what stacking means and the two triggers can never drift apart.
// Verified headless in the shipped Chromium: wide+forced stacks, wide+off doesn't, narrow always
// stacks. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("one stacked block, two triggers: the narrow size query OR the forced style query", () => {
  assert.match(CSS, /@container \(max-width: 540px\) or style\(--romp-stack: on\) \{/);
  const count = (CSS.match(/^@container /gm) || []).length;
  assert.equal(count, 1, "still exactly one container query — no duplicated stacked rules");
});

test("stacked sections stretch — every card spans the container, whatever its text (the user 2026-08-24)", () => {
  // the row layout's `align-items: flex-start` rides in from the base .feed-cols rule; in COLUMN
  // direction that same declaration content-sizes each section horizontally, so a long Completed
  // card spanned the pane while short Working cards hugged their titles (the second
  // width-inconsistency report; verified headless before/after in the shipped Chromium)
  assert.match(CSS, /\.feed-cols \{ flex-direction: column; align-items: stretch; \}/);
  // the cascade: the stretch lives INSIDE the stacked query, after the base rule, same specificity —
  // the base flex-start must still exist for the side-by-side layout's top alignment
  const baseAt = CSS.indexOf(".feed-cols { display: flex; align-items: flex-start;");
  const stretchAt = CSS.indexOf(".feed-cols { flex-direction: column; align-items: stretch; }");
  assert.ok(baseAt >= 0 && stretchAt > baseAt, "override follows the base rule it ties");
});

test("the view menu's Single-column row writes the pref and applies the style var; boot applies a persisted one", () => {
  assert.match(FEED, /stacked: s\.stacked === true/);
  // the footer "Stack" word-button folded into the view menu (the user 2026-08-24) — same pref, same var
  assert.match(FEED, /set\(1, "Single column view", \{/);
  assert.match(FEED, /mk\(true, \(\) => setViewPref\("stacked", !feedPrefs\(\)\.stacked, applyStacked\)\)/);
  assert.match(FEED, /\.style\.setProperty\("--romp-stack", on \? "on" : "off"\)/);
  assert.match(FEED, /applyStacked\(feedPrefs\(\)\.stacked\);\s*\/\/ boot/);
  assert.match(FEED, /applyStacked\(p\.stacked\);/, "a settings change from any surface re-applies it");
  assert.match(FEED, /ensureViewMenuBtn\(\)\.style\.display = showCA \? "" : "none";/, "docked in the footer row");
});
