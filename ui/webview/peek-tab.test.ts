// EPHEMERAL PEEK TABS (the user 2026-08-24, superseding the kernel's reveal-rule view mutation):
// focusing/clicking a session the current view hides opens it as a TEMPORARY tab — real and
// scrollable, dressed as outside the view — that auto-closes the moment any other tab is activated.
// Per-dashboard client state; never persisted, federated, or written to timeline-views.json. The
// kernel half (a focus never mutates the views blob) is executed in tests/test_timeline_views.py.
// render.ts has no jsdom harness → source pins (the apierror-retry-now.test.ts idiom); the CSS
// cascade order is pinned executably below.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("peek OPEN: every activation routes the peek decision — setActive derives peek-vs-normal from the CURRENT views", () => {
  // the single entry: setActive (tab clicks, the focus handler, jumpSession, cycleTab and
  // nav-history's apply all land here), before its already-active early-return
  // the window between the derivation and the early return widened on 2026-09-05: the subagent viewer's
  // two lines sit there (pruneSubViews — an activation is the event that closes an unpinned viewer — and the
  // reopen of a viewer id whose tab is gone), both BEFORE the return by design, see plans/subagent-transcripts.md
  assert.match(RENDER, /function setActive\(id: string[\s\S]{0,500}?assertPeekFor\(id\);[\s\S]{0,900}?if \(activeId === id && anchor == null && anchorT == null\) return;/);
  // the derivation: in-view → no peek; out-of-view → THIS session is the peek
  assert.match(RENDER, /const next = chatVisible\(id\) \? null : id;\s*\n\s*if \(next !== peekId\) \{ peekId = next; renderTabs\(\); \}/);
});

test("peek AUTO-CLOSE: activating any other tab drops it — same derivation, no explicit close affordance", () => {
  // an in-view target computes next=null and a different hidden target computes next=id — either
  // way a click-away replaces/clears the peek in one place; and a dismissed session can't hold it
  assert.match(RENDER, /if \(peekId === id\) peekId = null;\s*\/\/ its tab is going — the peek goes with it/);
  // no message-send path touches the peek: sending from a peeked session does NOT pin it (decided
  // 2026-08-24) — the ONLY writers are assertPeekFor and the dismiss clear above
  const writers = RENDER.match(/peekId = /g) || [];
  assert.equal(writers.length, 2, "peekId writers: assertPeekFor and dismissSession — nothing else (send never pins; the typed declaration matches separately)");
  assert.match(RENDER, /let peekId: string \| null = null;/);
  // …and the DERIVATION call sites, each named (the census, extended 2026-08-24 with the two
  // views-arrival paths): setActive (every activation), the focus fast path (already-active),
  // captureViews (kernel-pushed views), postViews (local optimistic edit). Nothing else derives.
  const sites = RENDER.match(/assertPeekFor\(/g) || [];
  // 6 → 7 on 2026-09-05: the subagent viewer's PIN control re-derives its own tab (pinned → in the chat
  // lens → sheds the peek dress; unpinned → back to a peek) through the same derivation — no second
  // peek mechanism (plans/subagent-transcripts.md; chatVisible() answers pinnedSubs for a viewer id)
  assert.equal(sites.length, 7, "definition + 6 call sites: setActive, focus fast path, captureViews, postViews, the feed click echo (2026-08-24 — the instant ack derives the peek before the kernel frame), and the subagent viewer's pin toggle (2026-09-05)");
});

test("a view change that excludes the ACTIVE session converts it into the peek — never a bounce (the user 2026-08-24)", () => {
  // both views-arrival paths re-derive the active session's peek: the kernel-pushed blob…
  assert.match(RENDER, /pendingSessionViews = null; pendingViewsAge = 0;\s*\n\s*\}[\s\S]{0,700}?if \(activeId\) assertPeekFor\(activeId\);\s*\n\}/);
  // …and the local optimistic edit, BEFORE its renderTabs so the repaint sees the fresh peek state
  assert.match(RENDER, /pendingSessionViews = v; pendingViewsAge = 0;\s*\n\s*if \(activeId\) assertPeekFor\(activeId\);[\s\S]{0,200}?renderTabs\(\);/);
  // the derivation is symmetric, so a view that now INCLUDES the active peek sheds the dress — the
  // same next-null branch the auto-close pin above holds; and the fallback's fire-time revalidation
  // (below) re-checks tabInView, so a converted peek can never be bounced by an in-flight timeout
  assert.match(RENDER, /setTimeout\(\(\) => \{ if \(activeId !== next && activeId && !tabInView\(activeId\)\) setActive\(next\); \}, 0\);/);
});

test("peek is FIRST-CLASS in nav history by storing only the sid — apply lands in setActive, re-deriving peek", () => {
  assert.match(RENDER, /const navHist = new NavHistory\(\{[\s\S]{0,900}?apply: \(spot\) => \{[\s\S]{0,400}?setActive\(spot\.sid\);/);
  // documented: back/forward re-pops as peek or normal per the views AT NAVIGATION TIME
  assert.match(RENDER, /Nav history stores only the sid/);
});

test("the first-tab fallback never fires on an active peek: tabInView counts the peek as visible", () => {
  assert.match(RENDER, /function tabInView\(id: string\): boolean \{ return id === peekId \|\| chatVisible\(id\); \}/);
  // the #only=-era bounce reads visibleIds, which is built from tabInView — an active peek is in it
  assert.match(RENDER, /const inViewIds = ids\.filter\(tabInView\);/);
  assert.match(RENDER, /if \(activeId && ids\.includes\(activeId\) && !visibleIds\.includes\(activeId\) && visibleIds\.length\) \{/);
  // …and the DEFERRED bounce re-validates at fire time: an activation between schedule and fire
  // (the feed click that just opened this peek) makes the active tab visible — no bounce then
  assert.match(RENDER, /setTimeout\(\(\) => \{ if \(activeId !== next && activeId && !tabInView\(activeId\)\) setActive\(next\); \}, 0\);/);
});

test("the focus fast path (already-active live jump) still re-asserts the peek — setActive is skipped there", () => {
  assert.match(RENDER, /assertPeekFor\(m\.id\);\s*\/\/ an out-of-view focus peeks even on the already-active fast path/);
  // …and the reveal-pane pin stays first in the branch (tests/test_per_viewer_focus.py's contract)
  assert.match(RENDER, /else if \(m\.type === "focus"\) \{\n    revealSelfPane\(\);/);
});

test("the strip dresses the peek: .tab-peek on the tab, ghost treatment in CSS, no status color stolen", () => {
  assert.match(RENDER, /if \(id === peekId\) tab\.classList\.add\("tab-peek"\);/);
  assert.match(CSS, /\.tab\.tab-peek \{ outline: [\d.]+px dashed rgba\(255, 255, 255, [\d.]+\); outline-offset: -2px; \}/);
  assert.match(CSS, /\.tab\.tab-peek \.tab-label \{ font-style: italic; opacity: [\d.]+; \}/);
  assert.doesNotMatch(CSS.match(/\.tab\.tab-peek[^\n]*/g)!.join("\n"), /--st-|var\(--state\)/,
    "structure, not status — the peek never wears a status color");
});

test("CASCADE: the peek outline is declared BEFORE the state outlines, so a real state wins at equal specificity", () => {
  const peekAt = CSS.indexOf(".tab.tab-peek {");
  const stateAt = CSS.indexOf(".tab.tab-awaiting, .tab.tab-blocked, .tab.tab-retrying { outline:");
  assert.ok(peekAt >= 0 && stateAt >= 0, "both rules present");
  assert.ok(peekAt < stateAt, "peek before states — order IS the tiebreak (competing `outline` at equal specificity)");
});

test("the peek is a PEEK, not a view edit: the client never posts a views change from focus, and the picker's reveal row keeps the real unhide", () => {
  // the only setTimelineViews writers are the deliberate gestures (hide menu-item, picker reveal,
  // timeline dialog) via postViews — the focus handler and setActive never call it
  const focusBlock = (RENDER.match(/else if \(m\.type === "focus"\) \{[\s\S]*?\n  \}/) || [""])[0];
  assert.ok(focusBlock.length > 100, "found the focus handler");
  assert.doesNotMatch(focusBlock, /postViews|setTimelineViews|revealSession/);
  assert.match(RENDER, /function revealSession\(id: string\) \{ postViews\(revealIn\(effViews\(\), id\)\); \}/);
});
