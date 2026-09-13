// Tab drag is LIVE REORDER (T127, the user 2026-08-27): while a tab drags, the other tabs
// rearrange in real time to make room, the way browser tab strips do — the old two-px landing
// marker is gone. The deciding event is the pointer crossing the MIDPOINT of the tab under it
// (never a timer); the dragged tab's in-flow element moves through the strip's DOM, the wrap
// layout reflows rows natively (true cross-row reorder: a tab pushed past a row's end wraps
// mid-drag), and siblings FLIP to their new rects. Verified end-to-end headless (synthetic drag
// events over the built bundle): same-row displacement, cross-row reflow, a push deferred across
// the whole drag, drop persistence surviving a forced re-render, and cancel animating home.
// Source pins per the repo convention (render.ts builds the strip imperatively; no jsdom here).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const between = (a: string, b: string) => {
  const at = RENDER.indexOf(a);
  assert.ok(at > 0, a + " must exist");
  return RENDER.slice(at, RENDER.indexOf(b, at));
};

test("exactly ONE thing on screen looks like the dragged tab (T133)", () => {
  // the user 2026-08-27: the native drag image following the pointer PLUS the dimmed in-flow tab
  // read as a ghost duplicate. The native image is blanked at dragstart; the dimmed in-flow
  // element — the one that live-reorders — is the single provisional visual, browser-style.
  assert.match(RENDER, /e\.dataTransfer\.setDragImage\(dragImageBlank\(\), 0, 0\);/);
  const at = RENDER.indexOf("function dragImageBlank(");
  assert.ok(at > 0);
  const body = RENDER.slice(at, RENDER.indexOf("\n}", at));
  assert.match(body, /position:fixed;top:-10px;left:-10px;width:1px;height:1px;opacity:0/,
    "a rendered but invisible node — Chromium snapshots the drag image at dragstart, so it must be in the DOM");
});

test("the slot comes from the VIRTUAL layout — boundaries that cannot move under the insert", () => {
  // the drag-flap fix (2026-08-28, the user's recording): hit-testing the LIVE rects fed back —
  // the insert re-wrapped the row and the next hit-test landed on the other side. The pointer is
  // now tested against dragslot.ts's simulated wrap of the NON-dragged tabs, widths snapshotted
  // once at dragstart.
  const body = between('tabs.addEventListener("dragover"', "});");
  // section headers + the untagged separator (tab groups, 2026-09-04) take width in the real
  // layout, so they join the virtual one as boxes — otherwise the simulated wrap drifts from the
  // strip's on every sectioned row
  assert.match(body, /const others = Array\.from\(tabs\.querySelectorAll<HTMLElement>\("\.tab\[data-id\], \.tab-group-head, \.tab-group-sep"\)\)\.filter\(\(t\) => t !== dragged\);/,
    "the dragged tab never participates in its own hit geometry");
  // the boxes are measured by getBoundingClientRect, which excludes margins — so no strip member
  // may carry a horizontal margin, or the virtual row holds more than the real one and the slot
  // hops in a band at every wrap boundary (the separator's 6px gutters were margin: a 12px drift)
  // the row openers are the breaks and the header a break precedes; the trail's first tab, which follows its break, is
  // not one (the break already opened its row — two openers on one row is what the themed-gap bug rode, 2026-09-11)
  assert.match(body, /\?\? t\.getBoundingClientRect\(\)\.width,\s*\n\s*br: isBreak\(t\) \|\| \(t\.classList\.contains\("tab-group-head"\) && isBreak\(before\(t\)\)\) \}\)\);/);
  // T264 (under the one-group-per-row setting, on by default): the untagged trail's boundary is a zero-height
  // ROW BREAK, not a box in the real layout, so it joins the virtual one as a zero-width row opener (w: 0, br),
  // and a header after a break opens a row too; the simulation then wraps exactly where the strip does
  const brk = CSS.match(/^\.tab-group-break \{[^}]*\}/m)![0];
  assert.match(brk, /flex: 0 0 100%; height: 0; margin: 0; padding: 0;/, "the break spans the row and has no box of its own");
  assert.match(body, /w: isBreak\(t\) \? 0 : /, "…so it measures 0 in the virtual layout, never its full-row rect");
  // with the setting off the trail's boundary is the 13px divider, a real box the virtual layout measures by its
  // rect, whose gutters are padding (a margin would drift the simulated row); its rule is scoped off the break
  assert.doesNotMatch(CSS, /^\.tab-group-sep \{/m, "no unscoped separator rule: the break's boundary class gives it no box");
  const sepM = CSS.match(/^\.tab-group-sep:not\(\.tab-group-break\) \{[^}]*\}/m);
  assert.ok(sepM, "the divider's rule exists, scoped off the break");
  const sep = sepM[0];
  assert.match(sep, /width: 13px; padding: 8px 6px;/, "the divider's footprint is its box");
  assert.doesNotMatch(sep, /margin/, "no margin on the divider: the virtual row holds what the real one holds");
  const head = CSS.match(/^\.tab-group-head \{[^}]*\}/m)![0];
  assert.doesNotMatch(head, /margin/, "headers carry no horizontal margin either");
  assert.match(body, /dragSlotIndex\(boxes, dragGeom\.containerW, dragGeom\.gapX, dragGeom\.rowH,/);
  assert.match(body, /if \(ref !== dragged && dragged\.nextElementSibling !== ref\)/,
    "already-in-place is a no-op, so a pointer resting in one slot never churns the DOM");
  assert.doesNotMatch(body, /setTimeout|debounce|Date\.now/, "no time-based logic in the reorder decision");
  // the stable inputs are captured once, at the gesture's own start — an event, not a poll
  assert.match(RENDER, /snapshotDragGeometry\(tab\);/);
  assert.match(RENDER, /widths\.set\(t\.dataset\.id, t\.getBoundingClientRect\(\)\.width\)/);
});

test("the DOM insertion still does the cross-row move; the simulation only decides WHERE", () => {
  assert.match(CSS, /#tabs \{ display: flex; flex: 1 1 auto; flex-wrap: wrap; align-items: stretch; gap: 0; position: relative; \}/);
  const body = between('tabs.addEventListener("dragover"', "});");
  assert.match(body, /flipTabs\(\(\) => tabs\.insertBefore\(dragged, ref\)\);/,
    "one insert per boundary crossing — the wrap layout itself performs the visual reflow");
});

test("the hover popover never survives a drag (defect 2, the user's recording)", () => {
  const ds = between('tab.addEventListener("dragstart"', "});");
  assert.match(ds, /hideTabTip\(\);/, "dismissed at dragstart");
  const stt = between("function showTabTip(", "\n}");
  assert.match(stt, /if \(draggedId\) return;/, "…and suppressed for the whole gesture");
});

test("drop commits through the SAME reorderTo — neighbor + side, hidden-view ids keep their places", () => {
  const body = between('tabs.addEventListener("drop"', "});");
  assert.match(body, /if \(prev\?\.dataset\?\.id\) tabDragCommitted = reorderTo\(draggedId, prev\.dataset\.id, true\);/);   // committed iff the reorder happened (2026-09-10)
  assert.match(body, /else if \(next\?\.dataset\?\.id\) tabDragCommitted = reorderTo\(draggedId, next\.dataset\.id, false\);/,
    "committed only when a reorder ran (T264b): no neighbour → dragend's cancel path FLIPs the copy home");
  // the neighbours are TABS (tab groups, 2026-09-04): a section header or separator beside the
  // dropped tab is skipped, so a drop at a section's edge still names the nearest tab and its side
  assert.match(body, /const prevIn = tabBefore\(dragged\.previousElementSibling\), nextIn = tabAfter\(dragged\.nextElementSibling\);/,
    "the neighbours inside the dragged copy's own group first (T264b)");
  assert.match(body, /const prev = prevIn \?\? \(nextIn \? null : walk\(dragged\.previousElementSibling, back, false\)\);/,
    "…falling back across groups only when the group holds no other tab");
  // …and a drop changes no membership: a tab landing in another section re-sections on the next
  // render — the tab menu's "Move to" rows are the membership path (v1)
  assert.doesNotMatch(body, /editUnion|moveUnion|editTag/, "no tag write on a tab drop");
  // …and reorderTo still persists exactly as before
  const rt = between("function reorderTo(", "\n}");
  assert.match(rt, /commitTabOrder\(\);/);
  assert.match(rt, /renderTabs\(\);/);
});

test("the drag covers the whole gesture against pushes, and dragend releases the hold by hand", () => {
  // pointerdown latches the hold BEFORE dragstart can fire; the drag swallows the pointerup, so
  // dragend clears it and flushes anything a push deferred mid-drag
  assert.match(RENDER, /tabs\.addEventListener\("pointerdown", \(\) => \{ tabPointerHeld = true; \}\);/);
  const de = between('tab.addEventListener("dragend"', "});");
  assert.match(de, /tabPointerHeld = false;/);
  assert.match(de, /const pending = renderPendingWhilePressed;/);
  assert.match(de, /else if \(pending\) setTimeout\(\(\) => renderTabs\(\), 0\);/);
});

test("cancel (Escape / dropped outside) re-renders from the untouched order, FLIP-animated home", () => {
  const de = between('tab.addEventListener("dragend"', "});");
  assert.match(de, /const cancelled = !tabDragCommitted;/);
  assert.match(de, /if \(cancelled\) flipTabs\(\(\) => renderTabs\(\)\);/);
  // the drop handler is what marks a commit, and it does so from the reorder's own word: a refused reorder (a page
  // without its manager, 2026-09-10) is a cancelled drag, so the strip FLIPs home
  const drop = between('tabs.addEventListener("drop"', "});");
  assert.match(drop, /tabDragCommitted = reorderTo\(/);
  assert.doesNotMatch(drop, /tabDragCommitted = true;/);
});

test("reduced motion: the mutation still happens, only the transition is skipped", () => {
  const flip = between("function flipTabs(", "\n}");
  assert.match(flip, /mutate\(\);\s*\n\s*if \(matchMedia\("\(prefers-reduced-motion: reduce\)"\)\.matches\) return;/,
    "the guard sits AFTER the mutation — positions always update; the reorder IS the information");
  assert.match(flip, /transform 0\.12s ease/, "duration is presentation, not logic — nothing waits on it");
});

test("the old landing marker is fully retired", () => {
  for (const [name, src] of [["render.ts", RENDER], ["styles.css", CSS]] as const) {
    assert.ok(!/drop-(?:before|after)/.test(src), name + " carries no landing-marker classes");
  }
});

test("the shell hears the drag: tabDrag on at dragstart after the geometry snapshot, off at dragend ahead of the cancel branch (the chat split, 2026-09-11)", () => {
  // the shell (kernel.py _LANDING_SPLIT_JS) mounts its drop zones — the other columns, the right edge — for the
  // gesture's length; the sid and the name ride the message, so it reads nothing from dataTransfer
  const ds = between('tab.addEventListener("dragstart"', "});");
  assert.match(ds, /draggedId = id; draggedEl = tab; tabDragCommitted = false;/, "the pinned head is unchanged (tab-groups and tab-strip-skip pin it)");
  const snap = ds.indexOf("snapshotDragGeometry(tab);"), post = ds.indexOf("postTabDrag(true, id);");
  assert.ok(snap > 0 && post > snap, "the shell is told after the snapshot");
  const de = between('tab.addEventListener("dragend"', "});");
  const off = de.indexOf("postTabDrag(false);"), cancel = de.indexOf("if (cancelled) flipTabs(() => renderTabs());");
  assert.ok(off > 0 && cancel > off, "the off ahead of the cancel branch: the zones go before the strip re-renders from the new sets");
  const fn = between("function postTabDrag(", "\n}");
  assert.match(fn, /if \(!inRompShell\(\)\) return;/, "nothing posted outside the shell");
  assert.match(fn, /window\.parent\.postMessage\(\{ romp: "tabDrag", on: false \}, "\*"\)/);
  assert.match(fn, /const bar = document\.getElementById\("tabbar"\);/, "stripH is the strip's bottom in this page's pixels");
  assert.match(fn, /window\.parent\.postMessage\(\{ romp: "tabDrag", on: true, sid: id, name, stripH: bar \? bar\.getBoundingClientRect\(\)\.bottom : 0 \}, "\*"\)/);
  assert.doesNotMatch(fn, /dataTransfer/, "the sid rides the message, never dataTransfer");
});

test("a create in flight is not draggable, and the page answers the shell's two questions before a move or a close (the chat split, review finds 2026-09-11)", () => {
  // a provisional tab has no session to move: draggable, the shell's zones would have opened a column on an id the kernel
  // does not know (it flashed open and shut); a sub-agent viewer was already not draggable
  assert.match(RENDER, /tab\.draggable = !s\.sub && !fedMissing && !isProvisionalId\(id\) && !settings\.tabsLocked;/);   // …nor while the tabs are locked (T395)
  // the palette's DOM read can still name either: the shell asks the page at its one mutation (tests/test_chat_split.py
  // runs the refusals), and whether this column has a create in flight before it closes it under one
  assert.match(RENDER, /\(window as any\)\.__rompMovableSession = \(sid: unknown\): boolean => typeof sid === "string" && !!sid && !isProvisionalId\(sid\) && !isSubId\(sid\) && !settings\.tabsLocked;/);   // the shell's move question answers no while the tabs are locked (T395)
  assert.match(RENDER, /\(window as any\)\.__rompColumnBusy = \(\): boolean => !!provisionalId \|\| failedProvisionals\.size > 0;/);
});
