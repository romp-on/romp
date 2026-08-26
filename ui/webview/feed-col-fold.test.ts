// Column controls (the user 2026-08-16; drag extended 2026-08-24): a caret folds each category to
// its header (STACKED-only — side by side always shows every card), and the category CHIP drags the
// section to a new slot — in BOTH layouts since the user's 2026-08-24 ask reversed the stacked-only
// exclusion (vertically stacked, horizontally side by side, ONE persisted order feeding both).
// Both controls live on the build-once headers (click-safe across the feed's constant re-renders)
// and persist with the view state. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the caret folds a category to its header and persists like every other disclosure", () => {
  assert.match(FEED, /const fold = el\("button", "fcol-fold"\);/);
  assert.match(FEED, /if \(collapsedCols\.has\(key\)\) collapsedCols\.delete\(key\); else collapsedCols\.add\(key\);/);
  assert.match(FEED, /cols: \[\.\.\.collapsedCols\],\s*\n\s*order: colOrder\.slice\(\)/, "rides the persisted view state (the key stays `order` across the 2026-08-24 merge)");
  // The fold BITES only in the stacked layout (the user 2026-08-18): collapsed while stacked, then
  // widened to three columns, the section stayed hidden with no caret to reopen it. The rule must
  // live INSIDE the stacked container query — side by side, every card always shows.
  const stacked = CSS.slice(CSS.indexOf("@container (max-width: 540px) or style(--romp-stack: on)"));
  assert.match(stacked, /\.feed-col\.col-collapsed \.feed-col-list \{ display: none; \}/,
    "the collapse rule is scoped to the stacked layout");
  assert.doesNotMatch(CSS.slice(0, CSS.indexOf("@container (max-width: 540px) or style(--romp-stack: on)")),
    /\.col-collapsed \.feed-col-list/,
    "and no unscoped copy survives to hide cards side-by-side");
  assert.match(FEED, /fold\.textContent = folded \? "▸" : "▾";/);
  // consistency with the session headers' fold (the user 2026-08-16): same side — caret RIGHT of the
  // label — and the same rendered size (the header's 0.72em compensated back to the feed's base)
  assert.match(FEED, /head\.append\(name, fold, count\);/);
  assert.match(CSS, /\.fcol-fold \{ display: inline-block; flex: none; padding: 0 5px; margin-left: -2px;[^}]*font-size: 1\.389em;/);
});

test("the chip drags in BOTH layouts — grab affordance, live provisional movement, one shared order", () => {
  // the user 2026-08-16 (chip as the drag surface, dropping the earlier grip handle), extended
  // 2026-08-24 by the user's own ask — REVERSING the 2026-08-16 stacked-only exclusion: side by
  // side the chip now reorders the three columns horizontally, just as the sections already drag
  // stacked, and ONE persisted order feeds both renderings.
  assert.match(CSS, /\.feed-col-head \.fcol-chip \{ cursor: grab; touch-action: none; user-select: none; \}/);
  assert.match(CSS, /\.feed-col\.col-dragging \{ position: relative; z-index: 5;/, "lifted over siblings while following");
  assert.match(CSS, /\.feed-col\.col-completed\s+\{ order: var\(--col-order, 1\); \}/,
    "the stacked default rules consume the SAME var the base layout's rules do");
  assert.match(CSS, /\.feed-col\.col-asks\s+\{ order: var\(--col-order, 1\); \}/,
    "…and the base layout keeps its own Working-first fallback");
  // THE CASCADE (the repo's recurring bug class): same var, same specificity — the stacked rules
  // must sit LATER than the base fallbacks AND INSIDE the container query (later-but-flattened
  // would win the cascade in BOTH layouts and impose Completed-first side by side)
  const baseAt = CSS.indexOf(".feed-col.col-asks       { order: var(--col-order, 1); }");
  const queryStart = CSS.indexOf("@container (max-width: 540px) or style(--romp-stack: on)");
  const queryEnd = CSS.indexOf("\n}", queryStart);
  const queryAt = CSS.indexOf(".feed-col.col-completed  { order: var(--col-order, 1); }");
  assert.ok(baseAt >= 0 && baseAt < queryStart, "base fallbacks live before (outside) the query");
  assert.ok(queryAt > queryStart && queryAt < queryEnd, "the stacked overrides live INSIDE the query");
  assert.match(FEED, /wireColDrag\(name, col, key\);/, "no separate handle — the chip is the drag surface");
  assert.doesNotMatch(FEED, /fcol-grip/, "the grip is gone");
  assert.match(FEED, /const vertical = getComputedStyle\(colsEl\)\.flexDirection === "column";/,
    "the layout picks the drag AXIS now — never a capture refusal (the 2026-08-16 exclusion is reversed)");
  assert.match(FEED, /const fallback = vertical \? STACK_DEFAULT : ROW_DEFAULT;/,
    "each layout's own default order seeds the drag until a custom order exists");
  assert.match(FEED, /const ROW_DEFAULT = \["asks", "needsInput", "completed"\];/);
  assert.match(FEED, /else col\.style\.removeProperty\("--col-order"\);/,
    "no custom order → the var comes OFF and each layout keeps its own default");
  assert.match(FEED, /chip\.setPointerCapture\(down\.pointerId\);/, "the drag survives leaving the chip");
  assert.match(FEED, /col\.style\.transform = translate\(pos\(ev\) - start - slotShift\);/,
    "the grabbed section follows the pointer along the drag axis");
  assert.match(FEED, /e\.animate\(\[\{ transform: translate\(d\) \}, \{ transform: translate\(0\) \}\]/,
    "displaced sections FLIP into their provisional slots, axis-generic");
  assert.match(FEED, /\{ slotShift -= d; continue; \}/, "a re-slot never yanks the section out from under the pointer");
  assert.match(FEED, /chip\.addEventListener\("pointercancel", up\);/, "a cancelled drag still settles + persists");
  // a no-op drag leaves no trace: ending on the layout's own default with no pre-existing custom
  // order resets to [] — an explicit order would silently re-arrange the OTHER layout (review 2026-08-24)
  assert.match(FEED, /if \(!hadCustom && colOrder\.length === 3 && colOrder\.join\(\) === fallback\.join\(\)\) \{/);
  // the keyboard card cursor walks the VISUAL order — the effective column `order`, var resolved
  assert.match(FEED, /slot\.set\(e, col \? parseInt\(getComputedStyle\(col\)\.order \|\| "0", 10\) \|\| 0 : 0\);/);
  assert.match(FEED, /\.sort\(\(a, b\) => a\.s - b\.s \|\| a\.i - b\.i\)/, "column slot first, DOM order within");
});

test("both controls live on the build-once header — click-safe across re-renders", () => {
  const build = FEED.slice(FEED.indexOf("function ensureCols"), FEED.indexOf("return {", FEED.indexOf("function ensureCols")));
  assert.ok(build.includes('el("button", "fcol-fold")') && build.includes("wireColDrag(name, col, key)"),
    "wired inside ensureCols' one-time scaffold, never in a render loop");
});

test("the Single-column menu row says so when narrow width already forces stacking (2026-08-19)", () => {
  // at or under the container query's 540px the layout stacks regardless of the pref, so the row
  // (the footer Stack button before 2026-08-24) is a no-op there: still ✓-checked — stacking IS
  // on — but faded (aria-disabled), click no-ops, tooltip names the way out. The width constant
  // must match the CSS query — the two stacking owners cannot drift.
  assert.match(FEED, /const STACK_FORCED_W = 540;/);
  assert.match(CSS, /@container \(max-width: 540px\) or style\(--romp-stack: on\)/);
  assert.match(FEED, /list\.clientWidth <= STACK_FORCED_W/);
  assert.match(FEED, /widen the feed to unstack into three columns/);
  assert.match(FEED, /current: p\.stacked \|\| stackForced, forced: stackForced/,
    "forced = checked AND inert — the ✓ never strips while stacking is active");
  assert.match(FEED, /if \(r\.classList\.contains\("forced"\)\) return;/,
    "keyboard activation bypasses pointer-events, so the handler itself no-ops");
  assert.match(FEED, /new ResizeObserver\(\(\) => refreshStackForced\(\)\)/,
    "width changes are the event — never a poll");
  assert.match(FEED, /if \(viewMenuEl\) paintViewMenu\(viewMenuEl\);   \/\/ an open menu repaints on the deciding event/);
  assert.match(CSS, /\.feed-viewmenu \.ctx-item\.forced \{ opacity: 0\.55; cursor: default; \}/,
    "faded but never stripped of its checked state (the user 2026-08-19)");
  assert.match(CSS, /\.feed-viewmenu \.ctx-item\.forced:hover \{ background: transparent; color: inherit; \}/,
    "an inert row makes no hover promise — .ctx-item:hover recolours as well as washes");
  assert.doesNotMatch(CSS, /feed-modetoggle\.forced/, "the footer-button forced rules are gone with the button");
});
