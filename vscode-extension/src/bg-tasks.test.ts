// Background-task box (the user 2026-06-26): the chat shows run_in_background tasks the kernel surfaced in a
// dedicated #bg-tasks box between the transcript and the composer — each a status dot + one-line summary,
// click the header to expand the command + output. Toggle is delegated to the stable container so a rebuild
// never drops it; expansion is keyed by task id. Source pins (no jsdom for the chat render).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the box payload is {count, tasks} and rides on the session across pushes", () => {
  assert.match(SRC, /interface BgTask \{ id: string; status: string; summary: string;/);
  assert.match(SRC, /interface BgTasks \{ count: number; tasks: BgTask\[\]; \}/);
  assert.match(SRC, /bgTasks\?: BgTasks;/);
  assert.match(SRC, /bgTasks: \("bgTasks" in msg\) \? msg\.bgTasks : \(prev \? prev\.bgTasks : undefined\)/);
});

test("the header collapses to a count: one → 'Background task · name', many → 'N background tasks'", () => {
  assert.match(SRC, /count === 1 \? "Background task · " \+ \(tasks\[0\]\.summary \|\| "running"\)/);
  assert.match(SRC, /: count \+ " background tasks"/);
  // collapsed by default: when the fold isn't open, only the header renders
  assert.match(SRC, /const open = bgFoldOpen\.has\(sid\);/);
  assert.match(SRC, /if \(!open\) return;/);
});

test("three-level disclosure: header fold, then per-task detail, both keyed so they survive re-render", () => {
  assert.match(SRC, /const bgExpanded = new Set<string>\(\);/);
  assert.match(SRC, /const bgFoldOpen = new Set<string>\(\);/);
  assert.match(SRC, /head\.dataset\.act = "bg-fold"; head\.dataset\.id = sid;/);
  assert.match(SRC, /rh\.dataset\.act = "bg-toggle"; rh\.dataset\.id = t\.id;/);
  // detail body = command + output, textContent only (untrusted)
  assert.match(SRC, /cmd\.textContent = t\.command;/);
  // the fallback moved into the row spec (slice 2, 2026-09-05: one bgRow renderer for every kind of row);
  // an AGENT row carries no output — its output file IS its transcript, and the arrow is the way in
  assert.match(SRC, /output: t\.agentId \? null : \(t\.output \|\| "\(no output captured\)"\)/);
  assert.match(SRC, /if \(t\.output\) \{ const out = el\("pre", "bg-out"\); out\.textContent = t\.output; det\.appendChild\(out\); \}/);
});

test("the worst status drives the collapsed header dot so a failure is glanceable", () => {
  assert.match(SRC, /const BG_RANK: Record<string, number> = \{ failed: 3, running: 2, completed: 1 \};/);
  assert.match(SRC, /const worst = tasks\.reduce\(/);
});

test("renderBgTasks is wired into showActive and both folds are delegated to the stable container", () => {
  assert.match(SRC, /renderBgTasks\(\); \/\/ swap in the active session's background-task box/);
  assert.match(SRC, /"bg-fold": \(el\) => \{/);
  assert.match(SRC, /"bg-toggle": \(el\) => \{/);
});

test("the list and the detail bodies scroll independently (overscroll-contain) — the expanded-view fix", () => {
  assert.match(CSS, /\.bg-list \{[^}]*overflow-y: auto; overscroll-behavior: contain;/);
  assert.match(CSS, /\.bg-cmd, \.bg-out \{[\s\S]*overflow: auto; overscroll-behavior: contain;/);
});

test("expanded list is capped (never crowds the composer) and scrolls; tasks are flat lines, not boxes", () => {
  assert.match(CSS, /#bg-tasks \{ flex: 0 0 auto;[^}]*max-height: min\(50vh, 340px\);/);
  assert.match(CSS, /\.bg-list \{[^}]*flex: 1 1 auto; min-height: 0; overflow-y: auto;/);
  // a task is a flat line now — the .bg-task rule carries no border/background box
  const taskRule = CSS.slice(CSS.indexOf(".bg-task {"), CSS.indexOf(".bg-task.bg-failed"));
  assert.ok(taskRule.length > 0, "found the .bg-task rule");
  assert.doesNotMatch(taskRule, /border|background/);
});

test("status tints keep their meaning (running yellow, failed red, completed blue — not the accent)", () => {
  assert.match(CSS, /\.bg-task \{ --bgt: var\(--st-working-bg\)/);
  assert.match(CSS, /\.bg-task\.bg-failed \{ --bgt: var\(--st-blocked-bg\); \}/);
  assert.match(CSS, /\.bg-task\.bg-completed \{ --bgt: var\(--st-ready-bg\); \}/);
});
