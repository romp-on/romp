// Long to-do lists auto-collapse (the user 2026-08-24): a continuously-dispatched session (a manager
// especially) accumulates dozens of finished tasks and the chat's to-do card showed every one. The
// DEFAULT view is the on-deck work (in_progress + pending); the finished bulk folds into one
// "+N more completed" row — click to expand, click to re-fold, nothing lost, state keyed so it
// survives the per-push re-renders. Source pins (render.ts has no jsdom harness — the repo
// convention); the acceptance case was screenshot-verified headless against a synthetic list of the
// live report's scale (52 completed + 1 in progress).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const TODO = RENDER.slice(RENDER.indexOf("function renderTodo"), RENDER.indexOf("function renderCompact"));

test("the card partitions on-deck from finished, and on-deck ALWAYS renders", () => {
  assert.match(TODO, /const onDeck = ev\.tasks\.filter\(\(t\) => t\.status === "in_progress" \|\| t\.status === "pending"\);/);
  // finished = anything NOT on deck — completed, and cancelled/deleted should the store carry them
  // (the kernel's _TASK_DONE_STATUSES semantics, client-side)
  assert.match(TODO, /const finished = ev\.tasks\.filter\(\(t\) => t\.status !== "in_progress" && t\.status !== "pending"\);/);
  assert.match(TODO, /for \(const t of onDeck\) card\.appendChild\(row\(t\)\);/, "on-deck rows render unconditionally");
});

test("the finished bulk folds at 3+, and a tiny list stays inline (no click for two rows)", () => {
  assert.match(TODO, /if \(finished\.length >= 3\) \{/);
  assert.match(TODO, /\} else for \(const t of finished\) card\.appendChild\(row\(t\)\);/);
});

test("the fold row reads '+N more completed', flips on click, and the flip acknowledges in place", () => {
  assert.match(TODO, /tog\.textContent = open \? `hide \$\{finished\.length\} completed` : `\+ \$\{finished\.length\} more completed`;/);
  assert.match(TODO, /rememberFold\(doneBox, "todo-open", foldKey\);\s*\n\s*label\(\);/,
    "the toggle records the keyed state and re-labels — the visible flip is the acknowledgement");
  assert.match(TODO, /tog\.addEventListener\("click", \(e\) => \{/);
});

test("expand state survives re-renders — the openFolds keyed idiom, keyed per session", () => {
  assert.match(TODO, /const foldKey = "todo-done:" \+ \(renderingSid \|\| ""\);/);
  assert.match(TODO, /applyFold\(doneBox, "todo-open", foldKey\);/);
});

test("the fold's chrome is the card's own dress: hidden box, quiet row, no new font sizes", () => {
  assert.match(CSS, /\.todo-done \{ display: none; \}/);
  assert.match(CSS, /\.todo-done\.todo-open \{ display: block; \}/);
  assert.match(CSS, /\.todo-fold \{ display: block; background: none; border: none; color: var\(--dim\); font: inherit;/,
    "font: inherit — the standing no-new-font-sizes rule");
});
