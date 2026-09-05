// The chat pane's share of the tab-click lag on a many-session dashboard (2026-09-04). Measured with a
// headless browser replaying a real seventeen-session board (see the timeline and feed tests of the same
// date for the other panes' shares): every tail a background tab received marked its view stale, so the
// idle rebuild — or the click, when idle never came — rebuilt its whole 80-unit window; every inbound tail
// rebuilt the whole tab strip and forced a layout, sixteen times a cycle; and the idle pre-build checked its
// budget only after doing the work, on a fallback deadline that never counted down. Source-level pins, the
// repo convention for render.ts (no jsdom): see prebuild-wiring.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const chatTail = /^function chatTail\([\s\S]*?\n\}/m.exec(RENDER)![0];

test("a background tab's tail lowers `rendered` to the changed point instead of marking the view stale", () => {
  const bg = /\} else \{\n([\s\S]*?)\n  \}\n\}$/.exec(chatTail)![1];
  assert.match(bg, /v\.rendered = Math\.min\(v\.rendered, from\);/);
  assert.match(bg, /const atTail = \(v\.winEnd \?\? Infinity\) >= \(v\.unitTotal \?\? 0\);\n\s*if \(shrank \|\| \(!atTail && from < \(v\.winEnd \?\? 0\)\)\) v\.stale = true;/,
    "a truncation, or a change inside a scrolled-away window, still needs the window rebuilt");
  assert.doesNotMatch(bg, /if \(v\) v\.stale = true;/, "the blanket stale mark is gone");
  assert.match(bg, /schedulePrebuild\(\);/, "the off-screen view still catches up in idle");
});

test("inbound tails repaint the tab strip once per animation frame; gestures still repaint at once", () => {
  assert.match(RENDER, /let tabsRaf: number \| null = null;\nfunction scheduleRenderTabs\(\): void \{\n\s*if \(tabsRaf != null\) return;\n\s*tabsRaf = requestAnimationFrame\(\(\) => \{ tabsRaf = null; renderTabs\(\); \}\);/);
  assert.match(chatTail, /scheduleRenderTabs\(\);/);
  assert.doesNotMatch(chatTail, /\n\s*renderTabs\(\);/, "the tail handler no longer rebuilds the strip synchronously");
  const setActive = /^function setActive\([\s\S]*?\n\}/m.exec(RENDER)![0];
  assert.match(setActive, /\n\s*renderTabs\(\);/, "a click repaints the strip in the same task");
});

test("the idle pre-build checks its budget before each tab, on a deadline that counts down", () => {
  const run = /^function runPrebuild\([\s\S]*?\n\}/m.exec(RENDER)![0];
  const check = run.indexOf("if (deadline.timeRemaining() < 3 && !deadline.didTimeout) { schedulePrebuild(); break; }");
  const work = run.indexOf("syncView(id); // build the hidden view now");
  assert.ok(check > 0 && work > 0 && check < work, "the budget check precedes the work");
  assert.match(RENDER, /cb\(\{ timeRemaining: \(\) => Math\.max\(0, 12 - \(performance\.now\(\) - t0\)\) \}\)/);
  assert.doesNotMatch(RENDER, /timeRemaining: \(\) => 12 \}/, "the constant-12 deadline is gone");
});
