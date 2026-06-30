// Long code/pre blocks in the chat wrap instead of scrolling sideways, with a subtle line-number gutter
// so a soft-wrap reads distinctly from a real newline (the user 2026-06-16). No jsdom harness here —
// like feed-dead.test.ts, pin the behaviour at the source level (regex over render.ts + styles.css).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("markdown code blocks wrap instead of scrolling sideways", () => {
  assert.match(CSS, /\.md pre \{[^}]*overflow-x: hidden/);          // the <pre> no longer scrolls horizontally
  assert.match(CSS, /\.md pre code \{[^}]*white-space: pre-wrap/);  // its code wraps
});

test("wrapped code carries a subtle (faint) line-number gutter", () => {
  assert.match(CSS, /pre code\.hljs \{[^}]*counter-reset: ln/);                 // a per-block line counter
  assert.match(CSS, /pre code \.cl::before \{[^}]*counter-increment: ln/);      // each .cl draws its number
  assert.match(CSS, /pre code \.cl::before \{[^}]*opacity: 0\.3/);              // "incognito" — faint
  assert.match(CSS, /pre code \.ct \{[^}]*white-space: pre-wrap/);             // the content wraps
  // render splits each highlighted line into <span class=cl><span class=ct>…, re-opening straddling spans
  assert.match(RENDER, /function wrapCodeLines/);
  assert.match(RENDER, /class="cl"><span class="ct"/);
});

test("diffs use diffPre() with per-line +/- gutter rows (not hljs)", () => {
  assert.match(RENDER, /function diffPre/);
  assert.match(RENDER, /diff-gutter/);
});
