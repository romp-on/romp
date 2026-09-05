// Click-safe, always-acknowledged dashboard buttons (the user 2026-06-24): the bug where End session had to be clicked
// several times. The dashboard re-renders on every kernel push; a handler hung on a node that a
// re-render rebuilds is destroyed mid-click and the click is dropped. The fix is systemic — delegate to a
// stable ancestor (HTML lists) or defer the rebuild while a pointer is pressed (the SVG timeline) — plus an
// immediate press acknowledgement so the user never re-clicks because nothing visibly happened.
// No jsdom harness for these renderers, so pin the wiring at the source (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const ACTIONS = ui("webview", "actions.ts");
const RENDER = ui("webview", "render.ts");
const FLEET = ui("webview", "fleet.ts");
const FEED = ui("webview", "feed.ts");
const TIMELINE = ui("romp-timeline-view.js");
const STYLES = ui("webview", "styles.css");
const FEEDCSS = ui("webview", "feed.css");

test("actions.ts is the shared primitive: delegate() matches data-act on a stable root and flashes", () => {
  assert.match(ACTIONS, /export function delegate\(root: HTMLElement \| Document, handlers: Record<string, ActionHandler>\)/);
  assert.match(ACTIONS, /export function flash\(el: HTMLElement\)/);
  // delegation routes by the nearest [data-act] ancestor — so a swapped-mid-click target still resolves via
  // the stable root, and a ✕ nested in a row routes to its own action without stopPropagation.
  assert.match(ACTIONS, /closest\("\[data-act\]"\)/);
  assert.match(ACTIONS, /const act = el\.dataset\.act;/);
  assert.match(ACTIONS, /const h = handlers\[act\];/);
  // every matched activation gets immediate feedback before the handler runs
  assert.match(ACTIONS, /flash\(el\);\s*\n\s*h\(el, ev\);/);
  // flash() is layout-safe (filter/opacity via a CSS class), survives node teardown, and re-triggers cleanly
  assert.match(ACTIONS, /classList\.add\("romp-acted"\)/);
  assert.match(ACTIONS, /void el\.offsetWidth;/);
});

test("chat tab bar: select + ✕ (Close / End session) are DELEGATED to the stable #tabs, not per-node", () => {
  assert.match(RENDER, /import \{ delegate \} from "\.\/actions";/);
  // each tab/✕ only DECLARES its action via data-act (+ data-id / data-dead); no action handler on the node
  assert.match(RENDER, /tab\.dataset\.act = "select";/);
  assert.match(RENDER, /close\.dataset\.act = "close";/);
  assert.match(RENDER, /close\.dataset\.id = id;/);
  assert.match(RENDER, /if \(dead\) close\.dataset\.dead = "1";/);
  // the per-node click handlers that renderTabs() used to rebuild every push are GONE (they were the bug)
  assert.ok(!/tab\.addEventListener\("click", \(\) => setActive\(id\)\)/.test(RENDER), "tab click must be delegated, not per-node");
  assert.ok(!/close\.addEventListener\("click"/.test(RENDER), "✕ click must be delegated, not per-node");
  // delegation is installed ONCE on the stable #tabs container, with both actions
  assert.match(RENDER, /const tabs = document\.getElementById\("tabs"\);/);
  assert.match(RENDER, /delegate\(tabs, \{/);
  // close: a DEAD tab just drops; a LIVE one shows the End/Close confirm IMMEDIATELY (client-side, no
  // closeSession→confirmClose kernel round-trip that made the ✕ feel unresponsive — the user 2026-06-24)
  assert.match(RENDER, /close: \(el\) => \{[\s\S]*el\.dataset\.dead === "1"[\s\S]*type: "closeTab"/);
  assert.match(RENDER, /close: \(el\) => \{[\s\S]*showConfirm\(`End/);
  assert.doesNotMatch(RENDER, /type: el\.dataset\.dead === "1" \? "closeTab" : "closeSession"/, "the round-trip close is gone");
  // and the tab is dropped + a new one reselected OPTIMISTICALLY (don't wait for the kernel's closed event →
  // no stale content from the just-closed session — the user 2026-06-24)
  assert.match(RENDER, /closeTab", id \}\);\s*\n\s*closeTabLocally\(id\);/);   // …and it STAYS gone: tab-close-optimistic.test.ts
  assert.match(RENDER, /function dismissSession\(id: string, why: DismissWhy, doomed\?: ReadonlySet<string>\): void/);
  // the kernel's own death event reuses it — naming its reason (T236: federation's stand-in for a dropped host is stamped, not an end)
  assert.match(RENDER, /m\.type === "closed"\) dismissSession\(m\.id, m\.hostDrop === true \? "hostDrop" : "end"\)/);
});

test("Fleet: header / row open + caret fold are DELEGATED to the stable #fleet-list, not per-node", () => {
  assert.match(FLEET, /import \{ delegate, flash \} from "\.\/actions";/);   // flash: the fold-mode buttons' press pulse rides the shared helper (2026-08-28)
  assert.match(FLEET, /head\.dataset\.act = "open"; head\.dataset\.sid = s\.sid;/);
  assert.match(FLEET, /row\.dataset\.act = "open"; row\.dataset\.sid = s\.sid;/);
  assert.match(FLEET, /tri\.dataset\.act = "fold"; tri\.dataset\.sid = s\.sid; tri\.dataset\.nid = n\.id; tri\.dataset\.folded =/);
  // the old onclick handlers that render() rebuilt via replaceChildren() every push are GONE
  assert.ok(!/head\.onclick = \(\) => openSession/.test(FLEET), "header open must be delegated");
  assert.ok(!/row\.onclick = \(\) => openSession/.test(FLEET), "row open must be delegated");
  assert.ok(!/tri\.onclick = \(ev\) =>/.test(FLEET), "fold caret must be delegated");
  assert.match(FLEET, /const list = document\.getElementById\("fleet-list"\);/);
  assert.match(FLEET, /delegate\(list, \{/);
  // fold preserves the exact toggle the per-node handler had (folded state carried on the node)
  assert.match(FLEET, /if \(el\.dataset\.folded === "1"\) \{ expanded\.add\(k\); folded\.delete\(k\); \} else \{ folded\.add\(k\); expanded\.delete\(k\); \}/);
});

test("Timeline: the EXTERNAL redraws (poll + live-tick) are held under a pressed pointer, NOT user gestures", () => {
  assert.match(TIMELINE, /this\.svg\.addEventListener\('pointerdown', \(\) => \{ this\._pointerHeld = true; \}\);/);
  // the poll update() buffers (reusing the freeze-on-hover _dirtyWhileTip path) instead of relaying out
  assert.match(TIMELINE, /\|\| this\._pointerHeld\) \{ this\._dirtyWhileTip = true; return; \}/);
  // the live-edge tick skips its look's draw but keeps the loop alive so it resumes on release — since
  // 2026-09-04 the loop sleeps between looks (see timeline-live-tick.test.ts), so a held pointer re-arms it
  // on a short sleep rather than the next animation frame
  assert.match(TIMELINE, /if \(this\._pointerHeld\) \{ this\._liveResume = true; return; \}/);
  assert.match(TIMELINE, /if \(this\._liveResume\) \{ this\._liveResume = false; this\._startLiveTick\(\); \}/, "the release event restarts the loop");
  // release repaints the buffered catch-up AFTER the click fires (setTimeout 0) — event-based, no time heuristic
  assert.match(TIMELINE, /window\.addEventListener\('pointerup', _release\);/);
  assert.match(TIMELINE, /window\.addEventListener\('pointercancel', _release\);/);
  assert.match(TIMELINE, /if \(this\._dirtyWhileTip && !tipUp\) \{ this\._dirtyWhileTip = false; setTimeout\(\(\) => \{ if \(this\.data\) this\.draw\(\); \}, 0\); \}/);
  // draw() itself is NOT guarded — pan/zoom/lane-reorder/touch drive it directly and must stay live
  assert.ok(!/if \(this\._pointerHeld\) \{ this\._drawDeferred/.test(TIMELINE), "no blanket draw() guard — that would freeze pans");
});

test("Timeline: blur force-releases a stuck press/tip so the lanes can't freeze on a stale frame (the user 2026-06-25)", () => {
  // the band is thin: a press/hover begun inside it often releases OUTSIDE it, so window 'pointerup' /
  // the tip mouseleave never fire and _pointerHeld (or a shown tip) sticks → update() buffers every push
  // and the lanes freeze on a stale frame (e.g. a live session reading 'not running'). blur (focus leaving
  // the iframe) is the release proxy: it clears a stuck tip via hideTip and runs _release (clears the press
  // + flushes the buffered redraw).
  assert.match(TIMELINE, /window\.addEventListener\('blur', \(\) => \{/);
  assert.match(TIMELINE, /window\.addEventListener\('blur', \(\) => \{[\s\S]*?contains\('show'\)\) this\.hideTip\(\);[\s\S]*?_release\(\);/);
});

// The manual Nudge button was REMOVED (the user 2026-06-30) — once Auto Nudge is robust you never hand-nudge.
// Its click-acknowledgement test is gone with it; pin that no Nudge button remains on the feed card.
test("the manual Nudge button is gone (Auto Nudge replaces it)", () => {
  // exact-string pin: the passive "nudge failed" CHIP (fask-nudgefailed, a status cue from the
  // auto-nudge — plans/stalled-open-todos-nudge.md) is a different thing and allowed to exist.
  assert.doesNotMatch(FEED, /"fask-nudge"/);
  assert.doesNotMatch(FEED, /nudge\.onclick/);
});

test("the .romp-acted press pulse exists in BOTH stylesheets (feed page loads only feed.css)", () => {
  for (const css of [STYLES, FEEDCSS]) {
    assert.match(css, /@keyframes romp-acted-pulse \{/);
    assert.match(css, /\.romp-acted \{ animation: romp-acted-pulse/);
  }
});
