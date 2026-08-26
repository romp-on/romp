// The footer VIEW MENU (the user 2026-08-24): the three view controls — sort direction, single-column
// layout, by-session grouping — live behind ONE monochrome icon button, replacing the Modified/Stack/
// Group word-buttons. The popup wears the repo-wide menu vocabulary (.ctx-menu chrome + the ✓-in-circle
// current mark); prefs still write the shared romp:settings, so the gear watcher and other panes read
// the same keys. feed.ts has no jsdom harness → source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("one 'View ▴' word-button replaces the three toggles; the old footer toggles stay gone", () => {
  assert.match(FEED, /function ensureViewMenuBtn\(\): HTMLElement/);
  assert.match(FEED, /b\.id = "feed-viewbtn";/);
  // a WORD, not an icon (the user 2026-08-24, round two: the ordering glyph read as "not what I
  // would expect for what to show") — the footer's word-button vocabulary, the Sessions caret
  assert.match(FEED, /b\.textContent = "View \\u25b4";/);
  assert.doesNotMatch(FEED, /viewMenuGlyph/, "the guessable glyph is gone outright");
  assert.doesNotMatch(FEED, /ensureFeedToggle|makeFeedToggle/, "the word-button helper went with its buttons");
  assert.doesNotMatch(FEED, /feed-newestfirst|feed-stacked|feed-grouped/, "no old toggle ids survive");
  assert.match(FEED, /b\.setAttribute\("aria-haspopup", "menu"\);/);
});

test("the menu holds exactly the three rows, in order: sort direction, single column, group", () => {
  const sortAt = FEED.indexOf('set(0, "Sort by most recent');
  const stackAt = FEED.indexOf('set(1, "Single column view');
  const groupAt = FEED.indexOf('set(2, "Group by session');
  assert.ok(sortAt > 0 && stackAt > sortAt && groupAt > stackAt, "three rows, this order");
  // each ✓ row declares its role; the direction row stays a plain menuitem (feed-sort.test.ts owns that)
  assert.match(FEED, /r\.setAttribute\("role", check \? "menuitemcheckbox" : "menuitem"\);/);
  assert.match(FEED, /r\.setAttribute\("aria-checked", opts\.current \? "true" : "false"\);/);
});

test("rows are real <button>s — the keyboard operability the replaced footer buttons had", () => {
  // the old Modified/Stack/Group controls were Tab-reachable, Enter/Space-activatable <button>s; div
  // rows would have made all three controls mouse-only (found in review, 2026-08-24)
  assert.match(FEED, /const r = el\("button", "ctx-item"\);/);
  assert.match(FEED, /\(r as HTMLButtonElement\)\.type = "button";/);
  assert.match(FEED, /\?\.focus\(\);   \/\/ a keyboard open lands on the first row/);
  assert.match(FEED, /if \(viewMenuEl\?\.contains\(document\.activeElement\)\) document\.getElementById\("feed-viewbtn"\)\?\.focus\(\);/,
    "closing from inside the menu hands focus back to the button");
  // buttons need the UA chrome stripped and the menu font re-inherited — a button that doesn't
  // inherit is exactly how a menu drifts onto the host font (the 2026-08-09 timeline-gear bug)
  assert.match(CSS, /\.feed-viewmenu \.ctx-item \{ position: relative; padding-right: 30px; display: block; width: 100%;\s*\n\s*text-align: left; background: none; border: 0; font: inherit; color: inherit; \}/);
  assert.match(CSS, /\.feed-viewmenu \.ctx-item:focus-visible \{ outline: 1px solid var\(--accent\); outline-offset: -1px; \}/);
});

test("a live menu syncs IN PLACE — rows are never rebuilt under a pressed pointer", () => {
  // paintViewMenu runs on an OPEN menu (settings change from another pane, the width crossing 540px);
  // rebuilding rows there would drop a click landing between mousedown and mouseup (click-safety)
  const paint = FEED.slice(FEED.indexOf("function paintViewMenu"), FEED.indexOf("function openViewMenu"));
  assert.ok(paint.includes('const rows = menu.querySelectorAll(".ctx-item");'), "sync finds the existing rows");
  assert.ok(!paint.includes("replaceChildren") && !paint.includes("appendChild"),
    "paint never rebuilds — buildViewMenu appends once per open");
  assert.match(FEED, /act\(\);\s*\n\s*closeViewMenu\(\);/, "handlers attach once in buildViewMenu");
  assert.match(FEED, /mk\(false, \(\) => setViewPref\("newestFirst", !feedPrefs\(\)\.newestFirst\)\)/,
    "clicks read the prefs at CLICK time, not a paint-time capture");
});

test("one menu at a time: the view menu and the session menu close each other on open", () => {
  // the pointerdown-away closers cover mouse opens; a keyboard Enter fires click with NO pointerdown,
  // so each opener must close the other explicitly (found in review, 2026-08-24)
  assert.match(FEED, /function openViewMenu\(btn: HTMLElement\): void \{\s*\n\s*closeSessList\(\);/);
  assert.match(FEED, /function openSessList\(\): void \{\s*\n\s*closeViewMenu\(\);/);
});

test("the popup wears the repo menu vocabulary: .ctx-menu chrome + the ✓-in-circle current mark", () => {
  assert.match(FEED, /el\("div", "ctx-menu feed-viewmenu"\)/, "the shared chrome, not a bespoke card");
  // the ✓-in-circle every romp menu uses (styles.css .ctx-sub/.meta-item.current), ported here because
  // the feed page loads only feed.css — on --check-bg, the panel-wide checkmark disc
  assert.match(CSS, /\.feed-viewmenu \.ctx-item\.current::after \{\s*\n\s*content: "✓"; position: absolute; right: 8px; top: 50%; transform: translateY\(-50%\);\s*\n\s*background: var\(--check-bg\); color: #fff; border-radius: 50%;\s*\n\s*width: 13px; height: 13px; font-size: 9px; font-weight: 900;/);
  // THE CASCADE (the repo's recurring bug class): the word-button rides the shared #feed-foot
  // .fdismiss metrics — NO bespoke #feed-viewbtn rule survives to lose (or half-win) a specificity
  // tie; and the row reset ties .ctx-item:hover at (0,2,0), so the reset must sit EARLIER in the
  // file for the hover wash to win its tie by order.
  assert.doesNotMatch(CSS, /#feed-viewbtn/, "no bespoke button rule at all — shared metrics only");
  const resetAt = CSS.indexOf(".feed-viewmenu .ctx-item {");
  const hoverAt = CSS.indexOf(".ctx-item:hover {");
  assert.ok(resetAt >= 0 && hoverAt > resetAt,
    "the button reset precedes .ctx-item:hover — the hover wash wins the background tie by order");
});

test("menu mechanics mirror the session menu: on document.body, opens upward, away/Escape close", () => {
  // on document.body, OUTSIDE render()'s reconcile — a push can never rebuild it mid-press (click-safety)
  const open = FEED.slice(FEED.indexOf("function openViewMenu"), FEED.indexOf("function ensureViewMenuBtn"));
  assert.ok(open.includes("document.body.appendChild(menu);"));
  assert.ok(open.includes('menu.style.bottom = Math.round(window.innerHeight - r.top + 6) + "px";'), "opens upward from the footer");
  assert.match(FEED, /function viewMenuAway\(ev: Event\): void/);
  assert.match(FEED, /function viewMenuKey\(ev: KeyboardEvent\): void \{ if \(ev\.key === "Escape"\) closeViewMenu\(\); \}/);
  assert.match(FEED, /document\.addEventListener\("pointerdown", viewMenuAway, true\);/);
});

test("rows write the shared romp:settings and signal the same-doc re-render, like the buttons did", () => {
  assert.match(FEED, /function setViewPref\(key: string, on: boolean, after\?: \(on: boolean\) => void\): void/);
  assert.match(FEED, /localStorage\.setItem\("romp:settings", JSON\.stringify\(s\)\);/);
  assert.match(FEED, /window\.dispatchEvent\(new Event\("romp:settings"\)\);/);
  // a settings change from ANY surface (the gear, another pane) repaints an open menu — its rows
  // display those prefs, so a stale menu would lie
  assert.match(FEED, /if \(viewMenuEl\) paintViewMenu\(viewMenuEl\);   \/\/ an open view menu re-reads the prefs it shows/);
});
