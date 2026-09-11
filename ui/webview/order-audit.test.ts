// The tab-order AUDIT instrumentation (the user 2026-07-02): tabs still occasionally reorder themselves
// and code reading has never found why, so render.ts watches the RENDERED order itself — whenever two tabs
// present in both the previous and the current render swap relative slots, it captures the JS stack of
// whoever triggered the render and reports it to the kernel (orderAudit → order-audit.jsonl), tagged
// drag:true when a user drag explains it. Source pins on render.ts: the audit must run inside renderTabs
// (the single place every order change funnels through) and must never re-sort anything itself.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("renderTabs audits the rendered order on every rebuild", () => {
  assert.match(SRC, /auditTabOrder\(ids\);/);
  const render = SRC.slice(SRC.indexOf("function renderTabs"));
  assert.ok(render.indexOf("auditTabOrder(ids);") >= 0, "the audit runs inside renderTabs, on the final ids");
});

test("a permutation of tabs present in BOTH renders is what fires the report", () => {
  assert.match(SRC, /const both = new Set\(ids\.filter\(\(id\) => lastTabIds\.includes\(id\)\)\);/);
  assert.match(SRC, /if \(prev !== next\) \{/);
});

test("the report carries old/new orders, the JS stack, and the drag tag, to the kernel", () => {
  assert.match(SRC, /type: "orderAudit", surface: "chat-tabs", old: lastTabIds\.slice\(\), new: ids\.slice\(\)/);
  assert.match(SRC, /new Error\("tab order permuted"\)\.stack/);
  assert.match(SRC, /drag: tabDragJustCommitted/);
  assert.match(SRC, /console\.warn\("\[romp\] tab order permuted", rec\);/);
});

test("a user drag tags the very next render as drag-explained, then the tag resets", () => {
  const reorder = SRC.slice(SRC.indexOf("function reorderTo"), SRC.indexOf("function reorderTo") + 1100);   // 1100 (was 700): the pinned-slot hold sits ahead of the tag (2026-09-10)
  assert.match(reorder, /tabDragJustCommitted = true;/);
  const audit = SRC.slice(SRC.indexOf("function auditTabOrder"), SRC.indexOf("let draggedId"));
  assert.match(audit, /tabDragJustCommitted = false;/);
});

test("the audit only observes — renderTabs still renders `order` verbatim, no re-sort", () => {
  const render = SRC.slice(SRC.indexOf("function renderTabs"), SRC.indexOf("function renderTabs") + 1600);
  assert.doesNotMatch(render, /ids\.sort|order\.sort/, "the instrumentation must never become a re-sort");
});
