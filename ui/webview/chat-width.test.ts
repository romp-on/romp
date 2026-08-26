// Chat reading column (the user 2026-06-15): the transcript + chrome fill the pane and grow with it,
// driven by --chat-col (default 100%), instead of a fixed 820px centered column that wasted wide-window
// space. The marker gutter is reserved unconditionally now that the column can fill at any width.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the chat column is fluid (--chat-col), not a hardcoded 820px cap", () => {
  assert.doesNotMatch(CSS, /820px/, "the fixed 820px caps are gone");
  assert.match(CSS, /--chat-col:\s*100%/);
  assert.match(CSS, /\.thread \{[^}]*max-width: var\(--chat-col\)/);
});

test("the time-marker left gutter is reserved unconditionally, 56px so the day label owns its column", () => {
  // tightened 54→44 (the user 2026-07-05) so the HH:MM times sit nearer the left margin and text gains width;
  // the turn's own text indent also drops 30→24, and the time-marker re-fits the smaller gutter (left/width)
  assert.match(CSS, /\.thread \{[^}]*padding: 0 24px 0 56px/);
  assert.match(CSS, /\.turn \{[^}]*padding-left: 24px/);
  assert.match(CSS, /\.time-marker \{[\s\S]*?left: -45px; width: 48px/);
  // the statusline shares the same left gutter so its controls stay aligned with the transcript column
  assert.match(CSS, /\.statusline \{[^}]*padding: 7px 24px 0 56px/);
});
