// How render.ts WIRES the skeleton-tab state machine (skeleton-tabs.ts; its policy is executable in
// skeleton-tabs.test.ts). After a redial the kernel sends only the active tab in full and lists the rest as
// `skeleton` on the tab strip, each carrying a status frame instead of a transcript; the page keeps the stale
// pre-outage sessions underneath and must never DISPLAY one as current. No jsdom for the webview, so these are
// source-level pins (the prebuild-wiring.test.ts convention), plus three executing cases at the end that lift
// showActive and the chip functions with esbuild (the chat-exact-tail-exec.test.ts pattern) and drive them over a
// fake pane. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { atBottomDist } from "./scroll-keep";
import { isReplyReady } from "./reply-ready";
import { hostOf } from "./host-prefix";
import { isProvisionalId } from "./provisional";   // the loading branch's one "opening" gate (2026-09-11): the real module

const requireCjs = createRequire(__filename);
const WEBVIEW = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(WEBVIEW, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(WEBVIEW, "styles.css"), "utf8");

const fn = (name: string): string => {
  const i = RENDER.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} not found`);
  return RENDER.slice(i, RENDER.indexOf("\n}\n", i) + 3);
};

test("render.ts holds ONE skeleton set, declared beside tabMeta, and reads the active session through liveSession", () => {
  assert.match(RENDER, /import \{ newSkeletonState, applyTabOrderSkeleton, onStatus, holdStatus, onFull, onDismiss, onSocketUp, nextPrefetch, renderKind \} from "\.\/skeleton-tabs";/);
  // beside tabMeta / closingTabs / pendingTabMeta (below them: tab-close-optimistic.test.ts wants closingTabs within
  // 900 characters of tabMeta) — renderTabs reads it and can run before the module finishes evaluating
  assert.match(RENDER, /const pendingTabMeta = new Map<string, PendingTabMeta>\(\);\n(?:\/\/[^\n]*\n)*const skeletonTabs = newSkeletonState\(\);/);
  assert.ok(RENDER.indexOf("const skeletonTabs = newSkeletonState();") < RENDER.indexOf("function renderTabs()"));
  assert.match(RENDER, /function liveSession\(id: string \| null \| undefined\): Session \| undefined \{\s*\n\s*return id && !skeletonTabs\.ids\.has\(id\) \? sessions\.get\(id\) : undefined;/);
});

test("the tabOrder frame applies the skeleton list BEFORE applyTabOrder, so its one renderTabs paints the final set", () => {
  // the dispatch's own tabOrder branch is pinned byte-for-byte by tab-meta.test.ts, so the pre-step is a
  // statement AHEAD of the chain — it runs first, and applyTabOrder's renderTabs sees the final state
  assert.match(RENDER, /if \(m\.type === "tabOrder"\) noteSkeletonTabOrder\(m\);[^\n]*\n\s*if \(m\.type === "session"\) upsert\(m\);/);
  const note = fn("noteSkeletonTabOrder");
  assert.match(note, /const kernelOrder: string\[\] = Array\.isArray\(m\.order\) \? m\.order\.filter\(\(x: any\) => typeof x === "string"\) : \[\];/,
    "the same string-only kernel order applyTabOrder adopts");
  assert.match(note, /const changed = applyTabOrderSkeleton\(skeletonTabs, m\.skeleton, kernelOrder\);/);
  // one client-diag row per reconnect that produced a set: armed by the socket opening, spent by the first strip
  assert.match(note, /if \(skeletonDiagArmed && Array\.isArray\(m\.skeleton\) && skeletonTabs\.ids\.size\) \{\s*\n\s*skeletonDiagArmed = false;\s*\n\s*vscodeApi\?\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "skeleton", data: \{ n: skeletonTabs\.ids\.size, active: activeId \} \}\);/);
  assert.match(RENDER, /else if \(m\.type === "wsup"\) \{ onSocketUp\(skeletonTabs\); skeletonDiagArmed = true; \}/,
    "a new socket forgets which fulls the dead one delivered and re-arms the row");
  // the tab we are ON became a skeleton (a click in the redial gap, a stale active hint) → re-show, keyed on change
  assert.match(note, /if \(changed && activeId && skeletonTabs\.ids\.has\(activeId\)\) showActive\(\);/);
  // the pre-step must come before the message handler's chain, and the definition sits with applyTabOrder
  assert.ok(RENDER.indexOf("function noteSkeletonTabOrder(") > RENDER.indexOf("function applyTabOrder("));
});

test("renderTabs draws a skeleton tab BEFORE the placeholder branch; both draw the chip through ONE shared helper", () => {
  const rt = fn("renderTabs");
  // (a sectioned strip stamps the copy's group on the skeleton and the placeholder alike — T264b, flipTabs keys per copy)
  assert.match(rt, /const s = sessions\.get\(id\);\s*\n(?:\s*\/\/[^\n]*\n)?\s*if \(renderKind\(skeletonTabs, id, !!s\) === "skeleton"\) \{\s*\n\s*const sk = makeSkeletonTab\(id\);\s*\n\s*if \(copyGroup !== undefined\) sk\.dataset\.copy = copyGroup \?\? "";[^\n]*\n\s*bar\.appendChild\(sk\); continue;\s*\n\s*\}\s*\n\s*if \(!s\) \{\s*\n\s*const ph = makePlaceholderTab\(id\);/,
    "skeleton first: a stale session entry must not make the tab read as loaded");
  assert.match(rt, /const st = applyTabStatus\(tab, s\);/, "the loaded tab's chip comes from the shared helper");
  assert.match(rt, /appendTabAfterWidgets\(tab, s\);/, "…and its after-the-name widgets (the gauge, the hot-key keycap; T379)");
  assert.match(rt, /wireTabDrag\(tab, id\);/, "…and its drag listeners");
  // exactly ONE status→class/dot block in the file: the helper (the "MISSING state" ring included — since T262g the
  // dot's class is tab-state.ts's tabDotClass, one slot in every state, so the file has ONE call and no ring literal)
  assert.equal(RENDER.split("const dotCls = tabDotClass(st);").length - 1, 0, "no dot-slot site in render.ts: the dot is a widget (tab-widgets.ts, T379)");
  assert.equal(RENDER.split('composeTabWidgets(tab, "before"').length - 1, 1, "one before-slot composition: applyTabStatus");
  assert.equal(RENDER.split('el("span", "tab-dot unknown")').length - 1, 0, "no hand-rolled unknown ring anywhere");
  // …and the state → class step inside it is tab-state.ts's shared rule (tab groups, 2026-09-04: the folded
  // section header's pip reads the same function), so the file has ONE such call and no hand-rolled class literal
  assert.equal(RENDER.split("const stateCls = tabStateClass(s.status);").length - 1, 1, "one state-class site: applyTabStatus wears the shared rule");
  assert.equal(RENDER.split('tab.classList.add("tab-working")').length - 1, 0, "no hand-rolled state class anywhere");
  const chip = fn("applyTabStatus");
  assert.match(chip, /^function applyTabStatus\(tab: HTMLElement, s: \{ id\?: string; status: Partial<Status> \}\): ChipState \| undefined \{\s*\n\s*const st = s\.status\.state;/);
  assert.match(chip, /composeTabWidgets\(tab, "before", s\.id \|\| "", s\.status, settings\.tabWidgets\);/, "no state → the honest unknown ring: the dot widget renders tabDotClass, a missing state is the gray ring (T379)");
  assert.match(chip, /return st;\s*\n\}/);
});

test("makeSkeletonTab: the loaded-tab chrome minus what it does not know — no swirl, honest chip, click-safe, draggable, closable", () => {
  const sk = fn("makeSkeletonTab");
  assert.match(sk, /el\("div", "tab tab-skeleton" \+ \(id === activeId \? " active" : ""\)\)/);
  assert.match(sk, /const meta = tabMeta\.get\(id\);/, "name + color from the pushed tab meta (fresh)");
  assert.match(sk, /const name = meta\?\.name \|\| stale\?\.name \|\| "";/, "…falling back to the stale session's name — a name is still the name");
  assert.match(sk, /tab\.tabIndex = 0;/);
  assert.match(sk, /tab\.dataset\.id = id;/);
  assert.match(sk, /tab\.dataset\.act = "select";/, "the stable #tabs delegate — click-safe like every tab");
  assert.match(sk, /tab\.addEventListener\("keydown", onTabKey\);/);
  assert.match(sk, /tab\.draggable = !fedMissing && !settings\.tabsLocked;\s*\n\s*wireTabDrag\(tab, id\);/, "a real live session: reordering is legitimate — unless the page has no manager to arrange it (2026-09-10), or the tabs are locked (T395)");
  assert.match(sk, /tab\.classList\.add\("colored"\)/);
  assert.match(sk, /const status = skeletonTabs\.status\.get\(id\) as Status \| undefined;\s*\n\s*applyTabStatus\(tab, \{ id, status: status \?\? \{\} \}\);/,
    "the chip reads ONLY the kernel's status frames; none yet → an empty status → the unknown ring");
  assert.match(sk, /if \(status\) appendTabAfterWidgets\(tab, \{ id, status \}\);/, "the gauge (and the keycap) only from a kernel-sent status");
  assert.match(sk, /const dead = status\?\.state === "closed";\s*\n\s*closeBtn\.title = dead \? "Close tab" : "End session";\s*\n\s*if \(dead\) closeBtn\.dataset\.dead = "1";/,
    "a dead session drawn as a skeleton drops like a dead loaded tab (the delegate's dead branch), no End confirm (review find 2026-09-08)");
  assert.match(sk, /tab\.title = "Not loaded yet — click to load";/);
  assert.match(sk, /closeBtn\.dataset\.act = "close";\s*\n\s*closeBtn\.dataset\.id = id;/, "the ✕ is delegated too");
  assert.doesNotMatch(sk, /tab-ph-swirl/, "NO swirl: that means 'romp is generating this', a transient");
  assert.doesNotMatch(sk, /stale\.(events|status)/, "the stale session's events/status are never read");
  assert.doesNotMatch(sk, /showTabTip/, "no rich hover tip — it reads a session's dir/model");
  assert.match(fn("makePlaceholderTab"), /tab-ph-swirl/, "the placeholder keeps its swirl");
  // DOM order label → gauge → ✕ (the ✕ keeps the right edge), as on a loaded tab
  const label = sk.indexOf("tab.appendChild(label);"), gauge = sk.indexOf("appendTabAfterWidgets(tab"), close = sk.indexOf('const closeBtn = el("span", "tab-close");');
  assert.ok(label >= 0 && label < gauge && gauge < close);
  // the builders sit ABOVE makePlaceholderTab: tabs-first.test.ts slices makePlaceholderTab→renderTabs and
  // forbids close/drag there, and tab-ctx-gauge.test.ts orders the file's first label < gauge < `const close`
  const ph = RENDER.indexOf("function makePlaceholderTab(");
  for (const f of ["applyTabStatus", "wireTabDrag", "makeSkeletonTab", "appendTabAfterWidgets"]) assert.ok(RENDER.indexOf(`function ${f}(`) < ph, f + " above the placeholder builder");
});

test("statusOnly begins with the skeleton branch: store + scheduleRenderTabs (one frame for a burst), never renderTabs; a status for a session the page holds nothing of is HELD for its strip, never the no-base ask", () => {
  const body = RENDER.split("function statusOnly(msg: any) {")[1].split("\n}")[0];
  const first = body.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("//"))[0];
  assert.equal(first, 'if (onStatus(skeletonTabs, msg.id, msg.status) === "skeleton") { scheduleRenderTabs(); return; }');
  // The shim's FIFO carries a newer strip to the END of a burst, so a skeleton's statuses can land ahead of the strip that
  // names the set (a later chat column's open sends two strips, 2026-09-11): the status waits for the strip. The ask that
  // stood here loaded the whole board into a column opened as a view of one session, one ask per withheld tab.
  assert.match(body, /const s = sessions\.get\(msg\.id\);\s*\n\s*if \(!s\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*holdStatus\(skeletonTabs, msg\.id, msg\.status\); return;\s*\n\s*\}/,
    "no session and not a skeleton: the status is held for the strip");
  assert.doesNotMatch(body, /"nobase"/, "statusOnly never asks for a full: a status frame is only ever a skeleton tab's");
  const skel = body.slice(0, body.indexOf("const s = sessions.get(msg.id);"));
  assert.doesNotMatch(skel, /\brenderTabs\(\)/, "sixteen status frames land in one burst — one animation frame, not sixteen synchronous repaints");
});

test("chatTail and update ask for the full on a skeleton id BEFORE their no-base check (a delta with no trusted base)", () => {
  const tail = fn("chatTail");
  assert.match(tail, /^function chatTail\(msg: any\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(skeletonTabs\.ids\.has\(msg\.id\)\) \{ requestFullSession\(msg\.id, "skeleton-delta"\); return; \}\s*\n\s*const s = sessions\.get\(msg\.id\);\s*\n\s*if \(!s\) \{/);
  const upd = fn("update");
  assert.match(upd, /if \(skeletonTabs\.ids\.has\(msg\.id\)\) \{ requestFullSession\(msg\.id, "skeleton-delta"\); return; \}[^\n]*\n\s*const s = sessions\.get\(msg\.id\);\s*\n\s*if \(!s\) \{ requestFullSession\(msg\.id, "nobase"\); return; \}/);
});

test("showActive gates the active session on the set, shows the loader with LOADING copy, and asks after notifyActive", () => {
  const sa = fn("showActive");
  assert.match(sa, /const s = activeId \? liveSession\(activeId\) : null;\s*\n\s*if \(!s\) \{/, "a skeleton active takes the existing !s branch");
  assert.match(sa, /const skeleton = skeletonTabs\.ids\.has\(activeId\);\s*\n\s*skeletonLoading = skeleton \? activeId : null;[^\n]*\n\s*if \(isProvisionalId\(activeId\)\) wait\.appendChild\(rompLoaderInner\("opening " \+ what \+ "…"\)\);\s*\n\s*else wait\.appendChild\(rompLoaderInner\("loading " \+ what \+ "…"\)\);/,
    "a running session is LOADING, a skeleton or not; 'opening' is the provisional's alone — a new split column's session on its way said 'opening' and read as a create (2026-09-11)");
  assert.match(sa, /if \(skeleton\) requestFullSession\(activeId, "skeleton-click"\);/);
  // activeTab (notifyActive) precedes needFull on the wire → the kernel builds the new active first
  assert.ok(sa.indexOf("notifyActive();") < sa.indexOf('requestFullSession(activeId, "skeleton-click")'));
  // the ask lives in showActive — the ONE place every path that lands on a skeleton active goes through (the
  // click via setActive, dismissSession's MRU fallback, the strip re-listing the tab we are on); the idle
  // prefetch skips the active tab by design, so nothing else would load it
  assert.equal(RENDER.split('"skeleton-click"').length - 1, 2, "one call site (+ the type's literal)");
  assert.match(fn("setActive"), /renderTabs\(\);\s*\n\s*showActive\(\);/, "the click path: strip repaint, then showActive → loader + ask");
  assert.match(fn("dismissSession"), /activeId = next\.activeId;[\s\S]*?showActive\(\);/, "the fallback path (and the unfocused one, T357) lands in showActive too");
  // The branch ends by taking the leaving tab's chips down (run in the chip tests below). They were measured
  // against ITS transcript, and a tab left at the top of a long one fires none of the chips' events on the switch:
  // no scroll clamp (scrollTop is 0 and stays 0) and no #content resize (the pane's height is the body minus its
  // siblings; hiding the views changes only its scroll extent), so nothing else would take them down.
  assert.match(sa, /removeProperty\("--active-accent"\);[^\n]*\n(?:\s*\/\/[^\n]*\n)*\s*updateStatusline\(\);[^\n]*\n(?:\s*\/\/[^\n]*\n)*\s*updateJumpBtn\(\);[^\n]*\n\s*return;\s*\n\s*\}/,
    "the no-session branch ends updateStatusline(); updateJumpBtn(); return;");
  // ...and the jump chip yields to the set before it measures: the loader's min-height overflows a short pane, and
  // the measure alone would paint the chip over the loader (a resize or scroll during the load did that before too)
  assert.match(fn("updateJumpBtn"), /if \(!liveSession\(activeId\)\) \{ jumpBtn\.hidden = true; updateReplyChips\(\); return; \}/,
    "no live session under the active tab: the chip hides and the reply chips re-read their own gate");
});

test("upsert computes wasSkeleton beside awaitingFull.delete and routes a just-loaded skeleton to showActive, not appendActive", () => {
  const up = fn("upsert");
  assert.match(up, /awaitingFull\.delete\(msg\.id\);[^\n]*\n\s*const wasSkeleton = onFull\(skeletonTabs, msg\.id\);/);
  assert.match(up, /if \(wasSkeleton \|\| skeletonLoading === msg\.id\) showActive\(\);[^\n]*\n\s*else if \(existed && !forked && !firstBuild && !adopted\) \{\s*\n\s*appendActive\(\);/,
    "the loader is up and the view hidden — appendActive would append onto a hidden view");
  assert.match(fn("appendActive"), /if \(!content \|\| !activeId \|\| skeletonTabs\.ids\.has\(activeId\)\) \{ showActive\(\); return; \}/,
    "appendActive itself refuses a skeleton active — the loader stays");
});

test("the idle prefetch: runPrebuild asks nextPrefetch (hidden = document.hidden || the pane display:none) for exactly one, and viewState is null for a skeleton", () => {
  const run = fn("runPrebuild");
  assert.match(run, /if \(pendingBuildRaf != null\) \{ schedulePrebuild\(\); return; \}[^\n]*\n(?:\s*\/\/[^\n]*\n)*\s*const next = nextPrefetch\(skeletonTabs, activeId, awaitingFull, document\.hidden \|\| paneHidden\(\), tabInView\);\s*\n\s*if \(next\) requestFullSession\(next, "prefetch"\);/);
  assert.match(run, /const viewState = \(id: string\): ViewState \| null => \{\s*\n\s*if \(skeletonTabs\.ids\.has\(id\)\) return null;/,
    "the pure planner never builds DOM for a stale session");
  assert.match(RENDER, /function paneHidden\(\): boolean \{\s*\n\s*try \{ return \(window\.parent !== window && \(window\.innerWidth === 0 \|\| window\.innerHeight === 0\)\) \|\| \(window as PaneHiddenHost\)\.__rompPaneHidden === true; \}/,
    "the shim's own display:none test, mirrored: the zero-viewport probe OR the pane's published word (paint-gate.ts states the rule; chat-visibility.ts publishes it on this page)");
  assert.match(RENDER, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(!document\.hidden\) schedulePrebuild\(\); \}\);/,
    "coming back to the tab is the event that re-arms the chain");
});

test("run: render.ts's paneHidden() over window stand-ins says hidden when the probe OR a published word of true does, never the word first", () => {
  // The function lifted from render.ts (esbuild at run time, the chat-exact-tail-exec.test.ts pattern) is the third
  // consumer of the question the pane shim and perf telemetry answer; the eight cases are the shim's
  // (tests/test_kernel_disconnect_banner.py runs the served function over the same stand-ins).
  const a = RENDER.indexOf("function paneHidden(): boolean {"), b = RENDER.indexOf("// The prefetch never runs while the browser tab is hidden", a);
  assert.ok(a > 0 && b > a, "paneHidden's anchors moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
  const verdict = (framed: boolean, iw: number, ih: number, word?: unknown): boolean => {
    const win: any = { innerWidth: iw, innerHeight: ih };
    win.parent = framed ? {} : win;
    if (word !== undefined) win.__rompPaneHidden = word;
    return (new Function("window", js + "\nreturn paneHidden();") as (w: unknown) => boolean)(win);
  };
  assert.equal(verdict(true, 0, 0), true, "a framed pane with a zero viewport: hidden since load, by the probe");
  assert.equal(verdict(true, 600, 400), false, "a framed pane with a viewport and no word: shown");
  assert.equal(verdict(true, 600, 400, true), true, "a viewport and a word of true: hidden after a first show (Chromium keeps the iframe's size)");
  assert.equal(verdict(true, 600, 400, false), false, "a viewport and a word of false: shown");
  assert.equal(verdict(true, 0, 0, false), true, "a zero viewport and a stale word of false: hidden (Firefox zeroes the viewport and stalls the observer)");
  assert.equal(verdict(false, 600, 400, true), true, "a standalone page's word is its tab's hiding; the probe never applies there");
  assert.equal(verdict(false, 0, 0), false, "a standalone page with no word: never hidden by the probe");
  assert.equal(verdict(true, 600, 400, "yes"), false, "only a boolean true is the word");
});

test("requestFullSession(id, why): every ask names its why, from the fixed vocabulary", () => {
  assert.match(RENDER, /type NeedFullWhy = "gap" \| "nobase" \| "skeleton-click" \| "prefetch" \| "skeleton-delta" \| "reattach";/);   // reattach: a proto-2 window back at the tail (T323 stage 4b)
  assert.match(RENDER, /function requestFullSession\(id: string, why: NeedFullWhy\): void \{\s*\n\s*if \(!id \|\| awaitingFull\.has\(id\)\) return;\s*\n\s*awaitingFull\.add\(id\);\s*\n\s*vscodeApi\?\.postMessage\(\{ type: "needFull", id, why \}\);/);
  const calls = [...RENDER.matchAll(/requestFullSession\(([^()]*?)\)/g)].map((m) => m[1]).filter((a) => !a.startsWith("id: string"));
  assert.ok(calls.length >= 11, "the gap ×4, no-base ×2 (chatTail and update; statusOnly holds a status for its strip instead, 2026-09-11), skeleton-delta ×2, skeleton-click, prefetch and reattach sites");
  for (const c of calls) assert.match(c, /, "(gap|nobase|skeleton-click|prefetch|skeleton-delta|reattach)"$/, `call site without a why: requestFullSession(${c})`);
  const why = (w: string) => RENDER.split(`, "${w}")`).length - 1;
  assert.equal(why("gap"), 4); assert.equal(why("nobase"), 2); assert.equal(why("skeleton-delta"), 2); assert.equal(why("reattach"), 1);   // gap ×4: the index tail's, the uuid tail's, a missing chatHead's and a missing chatMore's (an anchor gone from the transcript); nobase ×2: chatTail and update (statusOnly holds a status for its strip instead)
  assert.equal(why("skeleton-click"), 1); assert.equal(why("prefetch"), 1);
});

test("dismissSession is the one removal site: onDismiss right after the session map forgets the id", () => {
  assert.match(fn("dismissSession"), /sessions\.delete\(id\);\s*\n\s*onDismiss\(skeletonTabs, id\);/);
  // applyTabOrder's omission teardown goes through dismissSession (pinned in draft-teardown.test.ts), so it is covered
  assert.match(fn("applyTabOrder"), /dismissSession\(id, "omitted", omitted\);/);
});

test("every ACTIVE-tab display path reads through liveSession; only name reads and the fork ACTION gate keep sessions.get(activeId)", () => {
  const raw = [...RENDER.matchAll(/sessions\.get\(activeId[^\n]*/g)].map((m) => m[0]);
  assert.equal(raw.length, 4, "two name-only reads (a stale name is still the name) + the two fork-prompt gates (an action on a running session, not a display of its transcript):\n" + raw.join("\n"));
  for (const r of raw) assert.match(r, /\?\.name|showForkPrompt\(activeId/);
  const live = RENDER.split("liveSession(activeId)").length - 1;
  assert.ok(live >= 17, `the sweep covers the display paths (${live} sites)`);
  for (const f of ["updateStatusline", "renderBgTasks", "renderSubHead", "paintScrollMarks", "updateCommentRail", "landNearestMoment", "virtualizeToViewport", "updateJumpBtn", "updateReplyChips"]) {
    assert.match(fn(f), /liveSession\(activeId\)/, f + " reads the gated session");
  }
  assert.match(fn("renderLiveAsk"), /if \(!activeId \|\| skeletonTabs\.ids\.has\(activeId\) \|\| !liveAsks\.has\(activeId\) \|\| snapView\) \{/,
    "a skeleton's pre-outage picker is stale — hidden until the tab loads (and none shows under a section at a glance: snapView)");
});

test("styles: the skeleton label wears the placeholder's own muted value, and nothing else is new", () => {
  assert.match(CSS, /\.tab\.tab-skeleton \.tab-label \{ opacity: 0\.75; \}/);
  assert.match(CSS, /\.tab\.tab-placeholder\.colored \.tab-label \{ opacity: 0\.75; \}/, "the same number — no new opacity vocabulary");
  assert.equal((CSS.match(/tab-skeleton/g) || []).length, 1, "one rule; the chip/dot/ring rules are the shared ones");
});

test("the click path's loader latch: showActive latches the skeleton it is loading; upsert re-shows on it; a real view clears it", () => {
  // on the click path the strip that RELEASES the id lands before its full, so onFull() reports no skeleton —
  // the latch is what routes that full to showActive (review find 2026-09-07)
  assert.match(RENDER, /let skeletonLoading: string \| null = null;/);
  const show = RENDER.slice(RENDER.indexOf("function showActive("), RENDER.indexOf("function showActive(") + 9000);   // the section-at-a-glance branch sits ahead of the loading branch
  assert.match(show, /const skeleton = skeletonTabs\.ids\.has\(activeId\);\s*\n\s*skeletonLoading = skeleton \? activeId : null;/);
  assert.match(show, /document\.getElementById\("tab-loading"\)\?\.remove\(\);[^\n]*\n\s*skeletonLoading = null;/);
  assert.doesNotMatch(RENDER, /window\.addEventListener\("romp:wsup", \(\) => \{ onSocketUp/, "the socket flip is a frame now, never the onopen event");
});

test("the statusline over a skeleton tab says Loading, the word its loader uses, not Opening", () => {
  const usl = fn("updateStatusline");
  assert.match(usl, /sl\.replaceChildren\(openingLine\(isProvisionalId\(activeId\) \? "Opening session" : "Loading session"\)\);/,
    "Loading for every id whose payload has not landed, a skeleton or not; Opening only for a provisional (2026-09-11: a new split column's session on its way is not being created)");
  assert.match(RENDER, /function openingLine\(text = "Opening session"\): HTMLElement \{/);
});

test("the strip's repaint gate sees a skeleton: the signature reads renderKind and the stored status, not the stale session", () => {
  const rt = fn("renderTabs");
  const sig = rt.slice(rt.indexOf("const stripSig = JSON.stringify(["), rt.indexOf("const mslotEl = "));
  assert.ok(sig.length > 0, "the signature located");
  assert.match(sig, /renderKind\(skeletonTabs, id, !!s\) === "skeleton"/);
  assert.match(sig, /skeletonTabs\.status\.get\(id\)/);
});

// ── run: a switch to a loading tab takes the leaving tab's chips down ─────────────────────────────
// showActive, updateJumpBtn and updateReplyChips lifted from render.ts (esbuild at run time, the
// chat-exact-tail-exec.test.ts pattern) and driven over a fake pane whose scroll extent is what its SHOWN children
// stack to, as a browser's is. The chips refresh only on events (the pane's scroll and resize, a land, an append),
// and a tab left at the top of a long transcript fires none of them on the switch, so the switch itself has to
// take them down; and the go-to-bottom chip has to read the set, not the loader, whose min-height (styles.css)
// overflows a short pane. Synthetic ids; atBottomDist and isReplyReady are the real modules and run (hostOf is
// wired in and not reached: the skeleton's name comes from tabMeta).
const LOADER_VH = Number((/\.tab-loading-wait \{[^}]*min-height: (\d+)vh;/.exec(CSS) || [])[1]);
const PANE_PAD = 20;   // #content's vertical padding, 8px 0 12px (pinned in chipWorld)

/** Enough of Element for the switch: a node with a height when shown, children, a style, remove(). */
class ChipEl {
  id = ""; children: ChipEl[] = []; parent: ChipEl | null = null; style: Record<string, string> = {}; textContent = "";
  constructor(public cls: string, public h: number) {}   // h: a view's transcript, the loader's min-height, 0 for the rest
  appendChild(c: ChipEl): ChipEl { c.parent = this; this.children.push(c); return c; }
  remove(): void { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
}
/** #content: a fixed client height (the body minus its siblings), a scroll extent its shown children decide. */
class ChipPane extends ChipEl {
  scrollTop = 0;
  constructor(public clientHeight: number, public bottom: number) { super("", 0); this.id = "content"; }
  get scrollHeight(): number {   // a browser's scrollHeight is never under clientHeight
    return Math.max(this.clientHeight, PANE_PAD + this.children.filter((c) => c.style.display !== "none").reduce((a, c) => a + c.h, 0));
  }
  getBoundingClientRect(): { top: number; bottom: number } { return { top: this.bottom - this.clientHeight, bottom: this.bottom }; }
}
type ChipHooks = { fulls: string[]; captions: string[] };
type ChipApi = { showActive: () => void; updateJumpBtn: () => void; sig: () => string;
                 set: (p: { activeId?: string | null; replyChipSig?: string }) => void };

/** A page with two tabs: A, a loaded session whose transcript is `transcript` px tall; B, a skeleton (a stale
 *  pre-outage copy under it with an unread reply on it, its hidden view kept, its name in tabMeta). The pane is
 *  `clientHeight` px in a `innerHeight` px window. `awaiting` adds a third tab the strip listed (its name in tabMeta)
 *  whose payload has not landed: neither a session nor a skeleton, a new column's own session on its way. */
function chipWorld(opts: { clientHeight: number; innerHeight: number; transcript: number; awaiting?: string }) {
  assert.equal(LOADER_VH, 60, "the loader's min-height rule (styles.css) is the model's premise");
  assert.match(CSS, /^#content \{[^}]*padding: 8px 0 12px;/m, "the pane's 20px of vertical padding");
  const pane = new ChipPane(opts.clientHeight, opts.innerHeight - 40);
  const win = { innerHeight: opts.innerHeight };
  const doc = { getElementById: (id: string): ChipEl | null => id === "content" ? pane : (pane.children.find((c) => c.id === id) ?? null),
                body: { style: { removeProperty(_k: string): void {} },
                        classList: { add(..._c: string[]): void {}, remove(..._c: string[]): void {}, toggle(_c: string, _on?: boolean): void {}, contains(_c: string): boolean { return false; } } } };   // body.snap-mode (T322): showActive toggles the mode class
  const viewA = new ChipEl("session", opts.transcript);
  pane.appendChild(viewA);
  const viewB = new ChipEl("session", 900); viewB.style.display = "none";   // the stale copy's view, hidden as a non-active view is
  pane.appendChild(viewB);
  const HOOKS: ChipHooks = { fulls: [], captions: [] };
  const jumpBtn = { hidden: true, offsetHeight: 28, style: {} as Record<string, string> };
  const replyChips = { hidden: true, style: {} as Record<string, string> };
  const tabMeta = new Map<string, { name: string; color: null }>([["B", { name: "api", color: null }]]);
  if (opts.awaiting) tabMeta.set(opts.awaiting, { name: "tests", color: null });
  const W = {
    sessions: new Map<string, unknown>([["A", { id: "A", events: [], status: { state: "idle" } }],
                                        ["B", { id: "B", events: [], status: { state: "working" } }]]),   // B's stale copy stays underneath
    views: new Map<string, unknown>([["A", { el: viewA, scrollTop: 0, stick: false, shown: true, stale: false, winStart: 0 }],
                                     ["B", { el: viewB, scrollTop: 0, stick: false, shown: false, stale: true, winStart: 0 }]]),
    tabMeta,
    skeletonTabs: { ids: new Set(["B"]) },
    // B's stale copy carries an unread open reply: with its view kept and a ready thread, the liveSession read is
    // the ONE clause of updateReplyChips' gate that hides the chips over the skeleton (the read #1226 narrowed)
    commentThreads: new Map<string, unknown[]>([["B", [{ tid: "t1", anchorUuid: "22222222-3333-4444-5555-666666666666", status: "open", unread: true }]]]),
    jumpBtn, replyChips, atBottomDist, isReplyReady, hostOf, isProvisionalId, HOOKS,
    el: (_tag: string, cls?: string): ChipEl => new ChipEl(cls || "", cls === "tab-loading-wait" ? Math.round(LOADER_VH / 100 * win.innerHeight) : 0),
    rompLoaderInner: (caption: string): ChipEl => { HOOKS.captions.push(caption); return new ChipEl("romp-loader", 0); },
  };
  const js = requireCjs("esbuild").transformSync(["liveSession", "atBottom", "showActive", "updateJumpBtn", "updateReplyChips"].map(fn).join("\n"), { loader: "ts" }).code;
  const prelude = `
    const { sessions, views, tabMeta, skeletonTabs, commentThreads, jumpBtn, replyChips, atBottomDist, isReplyReady, hostOf, isProvisionalId, el, rompLoaderInner, HOOKS } = W;
    let activeId = null, skeletonLoading = null, replyChipSig = "";
    const placeReviveLoader = () => {}, notifyActive = () => {}, renderLedger = () => {}, renderLiveAsk = () => {}, renderBgTasks = () => {}, renderSubHead = () => {}, updateStatusline = () => {};
    // the unfocused body's painter and the box's name overlay (T357): inert here, the strip test is about the chips
    const paintEmptyState = () => {}, syncComposerPh = () => {}, order = [];
    // the section-at-a-glance view, inert: no section shows (snapView null), so showActive's branch is not taken
    let snapView = null, snapKeep = null;
    const renderSnapshot = () => false, hideSnapshot = () => {}, composerRestingPlaceholder = () => "";
    const setSnapMode = () => {}, growComposer = () => {};   // the overview mode's switch and the box re-measure on leaving it (T322)
    const requestFullSession = (id, why) => { HOOKS.fulls.push(why + ":" + id); };
  `;
  const epilogue = `
    return { showActive, updateJumpBtn, sig: () => replyChipSig,
             set: (p) => { if ("activeId" in p) activeId = p.activeId; if ("replyChipSig" in p) replyChipSig = p.replyChipSig; } };
  `;
  const make = new Function("W", "document", "window", prelude + js + epilogue) as (w: unknown, d: unknown, win: unknown) => ChipApi;
  return { api: make(W, doc, win), pane, HOOKS, jumpBtn, replyChips, doc };
}

test("run: switching from a tab left at the top of a long transcript to a loading tab takes both chips down with the loader", () => {
  const w = chipWorld({ clientHeight: 600, innerHeight: 800, transcript: 4980 });
  w.api.set({ activeId: "A" });
  w.api.updateJumpBtn();   // the leaving tab: 5000px of transcript in a 600px pane, the reader at the top
  assert.equal(w.jumpBtn.hidden, false, "the chip shows over the long transcript");
  w.replyChips.hidden = false; w.api.set({ replyChipSig: "A|1:t1||8" });   // a reply chip painted for that tab by hand (the placement is reply-ready.test.ts's; the lift stops short of it)
  // the switch (setActive: renderTabs, then showActive) to the skeleton: scrollTop is 0 and stays 0, so no scroll
  // event; the pane's height is unchanged, so no resize; the switch is the only event there is
  w.api.set({ activeId: "B" });
  w.api.showActive();
  assert.deepEqual(w.HOOKS.fulls, ["skeleton-click:B"], "the branch ran: the full was asked for");
  assert.deepEqual(w.HOOKS.captions, ["loading “api”…"], "the loader wears the LOADING copy (the showActive test pins its source)");
  const loader = w.doc.getElementById("tab-loading");
  assert.ok(loader && loader.cls === "tab-loading-wait", "the loader is up");
  assert.equal(w.pane.scrollHeight, 600, "the hidden transcript no longer counts, and the loader fits this pane");
  assert.equal(w.jumpBtn.hidden, true, "no go-to-bottom chip over the loader");
  assert.equal(w.replyChips.hidden, true, "no reply chip over the loader: B's stale copy has an unread reply and a kept view, and liveSession alone says no");
  assert.equal(w.api.sig(), "", "the reply chips' signature is cleared, so the landing tab's first paint is not skipped as unchanged");
});

test("run: the loader itself overflows a short pane; the chip reads the set, not that measure", () => {
  // a pane under 60vh + 18px (a short window, a tall composer): the loader's min-height is more than the pane
  const w = chipWorld({ clientHeight: 300, innerHeight: 500, transcript: 4980 });
  w.api.set({ activeId: "A" }); w.api.updateJumpBtn();
  assert.equal(w.jumpBtn.hidden, false);
  w.replyChips.hidden = false; w.api.set({ replyChipSig: "A|1:t1||8" });
  w.api.set({ activeId: "B" }); w.api.showActive();
  assert.ok(w.pane.scrollHeight > w.pane.clientHeight + 2, "the loader overflows the pane: a bare measure would show the chip");
  assert.equal(w.jumpBtn.hidden, true, "over a loading tab there is nothing to go to the bottom of");
  assert.equal(w.replyChips.hidden, true, "no reply chip over the loader");
  w.api.updateJumpBtn();   // a resize or scroll during the load runs the same measure through its own listeners
  assert.equal(w.jumpBtn.hidden, true, "…and keeps it down");
});

test("run: the gate reads the skeleton set, not the session map; the last tab closing drops the chips with the no-sessions copy", () => {
  const w = chipWorld({ clientHeight: 300, innerHeight: 500, transcript: 4980 });
  w.api.set({ activeId: "A" }); w.api.updateJumpBtn();
  assert.equal(w.jumpBtn.hidden, false, "a loaded session's long transcript keeps its chip: the gate is the set, and A is not in it");
  w.replyChips.hidden = false; w.api.set({ replyChipSig: "A|1:t1||8" });
  w.api.set({ activeId: null }); w.api.showActive();   // dismissSession's fallback with no tab left
  assert.ok(w.doc.getElementById("empty-state"), "the no-sessions copy is up");
  assert.deepEqual(w.HOOKS.fulls, [], "nothing to ask for");
  assert.deepEqual(w.HOOKS.captions, [], "no loader, so no caption");
  assert.equal(w.jumpBtn.hidden, true, "no chip over the no-sessions copy");
  assert.equal(w.replyChips.hidden, true, "no reply chip over the no-sessions copy");
});

test("run: a column's own session, listed by the strip but not among its skeletons, is shown loading and asked for by nobody while its full is in flight", () => {
  // The second full on a new chat column's socket (a slow runner, 2026-09-12) was a kernel race, not this path
  // (tests/test_chat_skeleton_reconnect.py test_11_d). A new column opens as a skeleton client of one session: the strip's
  // skeleton list names every OTHER tab (the kernel excludes the active hint), and the page's restore of its wanted tab
  // can land here between that strip and the session's full of the same burst. showActive's ask fires only for a tab the
  // skeleton set names, so the restore shows the loader and posts nothing: the full on its way is the burst's own.
  const w = chipWorld({ clientHeight: 600, innerHeight: 800, transcript: 4980, awaiting: "C" });
  w.api.set({ activeId: "C" }); w.api.showActive();
  assert.deepEqual(w.HOOKS.fulls, [], "no needFull for the session whose full the burst carries");
  assert.deepEqual(w.HOOKS.captions, ["loading “tests”…"], "the loader over the tab, with the LOADING word");
  const loader = w.doc.getElementById("tab-loading");
  assert.ok(loader && loader.cls === "tab-loading-wait", "the loader is up");
  // …the neighbouring skeleton still asks on its pick: the gate is the set, not the missing session
  w.api.set({ activeId: "B" }); w.api.showActive();
  assert.deepEqual(w.HOOKS.fulls, ["skeleton-click:B"]);
});
