// The timeline's corner control panel (the user 2026-08-18; filter-chip form + TAG model
// 2026-08-23): "Filter ▾" in the bottom-left corner — the strip under the lane gutter, left of the
// time labels. The dropdown picks the active VIEW (All — literally everything, the default
// since 2026-08-24 — / the (no tags) built-in / the named tags),
// holds New tag… / Sessions & tags…, and carries the two timeline display toggles (collapse idle
// gaps, active only) so they finally work in every host. The dialog is TAG-CENTRIC: one row per
// session wearing its tag chips (✕ leaves a tag; [+] joins or mints one) — a tagged session leaves
// the default view and shows under its tags. House pattern: execute the pure helpers + reconcile
// on a bare prototype, regex-pin the SVG/menu wiring.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const VIEW_PATH = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const SRC = fs.readFileSync(VIEW_PATH, "utf8");
const { TimelinePanel, viewVisible, viewLabel, viewMoreCount, viewToggleMember, viewTagUnion, lensAll, lensToggle, lensVisible, lensLabel, timelineLens } = requireCjs(VIEW_PATH);

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2", "s3"] };
const V = (active: string, hidden: string[] = [], tags: any[] = [G]) => ({ active, hidden, tags });

test("executed: All shows literally everything (hidden retired 2026-08-24); untagged the tagless; a tag view its members", () => {
  assert.equal(viewVisible(null, "s1"), true, "no blob yet → everything shows");
  assert.equal(viewVisible(V("all", ["s9"]), "s9"), true, "a legacy hidden entry is IGNORED — nothing hides from All (the user 2026-08-24)");
  assert.equal(viewVisible(V("all"), "s2"), true, "TAGGED → All still shows it (the user 2026-08-24)");
  assert.equal(viewVisible(V("all"), "s1"), true, "untagged → shown");
  assert.equal(viewVisible(V("untagged", ["s9"]), "s9"), true, "…and legacy hidden does not hide in untagged either");
  assert.equal(viewVisible(V("untagged"), "s2"), false, "TAGGED → out of the untagged view (the user 2026-08-23)");
  assert.equal(viewVisible(V("untagged"), "s1"), true, "tagless → the untagged view shows it");
  assert.equal(viewVisible(V("g1", ["s2"]), "s2"), true, "a tag view shows its members");
  assert.equal(viewVisible(V("g1"), "s1"), false, "a tag view shows exactly its members");
  assert.equal(viewVisible(V("ghost", [], []), "s1"), true, "an orphaned active falls back open");
  assert.equal(viewVisible({ active: "untagged", groups: [G] }, "s2"), false,
    "the legacy `groups` key an un-updated kernel pushes reads identically");
});

test("executed: the trigger label and the N-more cue (live sessions outside the view)", () => {
  // the views are named for what they show: "All" (literally everything — the hidden set retired
  // 2026-08-24) and "(no tags)" — the user-chosen words (2026-08-24), parens kept as the built-in marker
  assert.equal(viewLabel(null), "All");
  assert.equal(viewLabel(V("untagged")), "(no tags)");
  assert.equal(viewLabel(V("g1")), "pool");
  const sessions = [{ id: "s1", live: true }, { id: "s2", live: true }, { id: "s4", live: false }];
  assert.equal(viewMoreCount(V("g1"), sessions), 1, "s1 is live and outside; dead s4 never counts");
  assert.equal(viewMoreCount(V("all", ["s1"]), sessions), 0, "NOTHING sits outside All — legacy hidden ignored");
  assert.equal(viewMoreCount(V("untagged", ["s1"]), sessions), 1, "tagged s2 sits outside untagged; legacy-hidden s1 shows");
});

test("executed: an optimistic edit holds until the kernel echoes it — then yields to authority", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  p._views = null; p._pendingViews = V("g1"); p._pendingViewsAge = 0;
  p._reconcileViews();
  assert.ok(p._pendingViews, "no echo yet → still pending");
  // the kernel echoes the same shape with re-sorted lists → canonical comparison clears it
  p._views = { active: "g1", hidden: [], tags: [{ id: "g1", name: "pool", color: "#DD42FF", members: ["s3", "s2"] }] };
  p._reconcileViews();
  assert.equal(p._pendingViews, null, "echo match (order-insensitive) clears the pending edit");
  // a pending edit the kernel never echoes yields after three pushes — the kernel is authoritative
  p._pendingViews = V("g1"); p._pendingViewsAge = 0;
  p._views = { active: "all", hidden: [], tags: [] };
  p._reconcileViews(); p._reconcileViews();
  assert.ok(p._pendingViews, "two silent pushes → still holding");
  p._reconcileViews();
  assert.equal(p._pendingViews, null, "the third silent push adopts the kernel's blob");
});

test("the lane gate composes the view filter first, and the all-quiet fallback respects it", () => {
  assert.match(SRC, /const inView = \(s\) => \{ const v = this\._curViews\(\); return lensVisible\(timelineLens\(v\), viewTagUnion\(v\), s\.id\); \};/,
    "lanes key on THIS surface's lens (per-surface selections, 2026-08-25)");
  assert.match(SRC, /let vis = data\.sessions\.filter\(inView\)\.filter\(active\);/);
  assert.match(SRC, /if \(this\._activeOnly && !vis\.length\) vis = data\.sessions\.filter\(inView\)\.filter\(\(s\) => s\.live \|\| hasWork\(s\)\);/,
    "the fallback can never resurrect a view-hidden lane");
});

test("the trigger sits in the corner strip and opens on pointerdown, like every timeline control", () => {
  assert.match(SRC, /this\._renderCornerBar\(\);/);
  // two icon buttons since 2026-08-25 (display options + the tag filter) — REAL HTML <button>s in
  // the persistent corner layer since round three — both pointerdown-opened, tooltips carry the words
  assert.match(SRC, /b\.addEventListener\('pointerdown', \(e\) => \{ e\.preventDefault\(\); e\.stopPropagation\(\); open\(b\); \}\);/);
  assert.match(SRC, /btn\('filter these lanes by tag',/);
  assert.match(SRC, /const tailStr = more \? more \+ ' more' : '';/, "a filtered-out live session is always one glance away");
});

test("an active tag is a REMOVABLE CHIP: outline only in its colour, a dim separate ✕, air below (the user 2026-08-24)", () => {
  // the chip's own pointerdown clears the filter without a menu trip; stopPropagation keeps the
  // text element's menu handler out of it (both are pointerdown — the redraw-eats-click rule)
  assert.match(SRC, /nv\.actives = Object\.assign\(\{\}, nv\.actives, \{ timeline: lensToggle\(lens, c\.pick\) \}\);/,
    "each chip's ✕ unselects THAT pick (per-selection chips, the user 2026-08-25)");
  // OUTLINE only on the page's own ground (the tinted fill was too much — the user 2026-08-24),
  // and the ✕ is dim and SEPARATE, the composer context chip's read — never baked into the name
  assert.match(SRC, /chip\.style\.borderColor = c\.color; chip\.style\.color = c\.color;/,
    "outline + text in the pick's colour on the page's own ground");
  assert.match(SRC, /\.romp-tl-chip\{display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;/,
    "the chip anatomy is the shared syncTagFilter chip's, by value");
  // a SENTINEL view's chip dims to the corner line's own gray at the N-more opacity (the user
  // 2026-08-24: at #cccccc it read bright as a tag) — real tag chips keep their tag colors, full strength
  // per-selection chips since 2026-08-25: each pick derives its own colour (no-tags in the gray)
  // ORDER (the user 2026-08-25): "no tags" leftmost, then the tags in the USER'S order — the
  // chips walk the ordered unions instead of the raw selection (superseding the none-last form)
  assert.match(SRC, /if \(lens\.none\) chips\.push\(\{ label: 'no tags', color: MODEL_FG, pick: 'none' \}\);/,
    "no-tags sits leftmost in the corner's selection render");
  assert.match(SRC, /if \(lens\.none\) chips\.push[\s\S]{0,400}for \(const u of unions\) \{/,
    "…and the tag chips follow by walking the ORDERED unions");
  assert.match(SRC, /x\.textContent = '\\u2715';/);
  assert.match(SRC, /\.romp-tl-chipx\{cursor:pointer;opacity:0\.75;/, "the ✕ dim and separate");
  assert.match(SRC, /remove \\u201c' \+ c\.label \+ '\\u201d from this timeline/,
    "the ✕ hover names the ONE pick it removes");
  // no chip on All — the unfiltered default; the untagged view IS a filter now, so it wears one
  assert.match(SRC, /const active = !lensAll\(lens\);/, "any non-All lens selection shows the chip (2026-08-25)");
  // …and the bottom strip grew so the taller chip has air
  assert.match(SRC, /bottom: 27 \}/);
});

test("the corner bar wears the FEED FOOTER's typography — stated, since the pane may live in a foreign document", () => {
  // round three (the user 2026-08-25): the corner is the feed footer's control row now, so it
  // carries the feed's own font stack EXPLICITLY (the menu rule: an adopted/foreign host would
  // substitute its own family otherwise — how the gear menu once drifted off-brand)
  assert.match(SRC, /\.romp-tl-corner\{[^}]*font-family:var\(--vscode-font-family,-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif\)/,
    "the bar states the shared stack every romp surface resolves to");
  assert.match(SRC, /'font-weight': 650, 'font-size': 12, fill: F\(s\.color\)/, "the lane-name reference is untouched");
  // …and the width/ellipsis math measures in the SAME family the text renders in: _font resolves
  // the wrap's computed family (FONT is only the unstyled/bare-node fallback), so box and ellipsis
  // can never drift from the rendered glyphs
  assert.match(SRC, /_font\(b\) \{ this\._mc\.font = \(b \? '700 ' \+ BADGE_FS \+ 'px ' : '650 12px '\) \+ this\._fontFace\(\); \}/);
  assert.match(SRC, /getComputedStyle\(this\.wrap\)\.fontFamily\) \|\| FONT;/);
  // …and EVERY measure goes through it: the only ctx.font writers left are _font/_fontFace-based
  // (ctxWidth and the two inline 9px/10px axis measures included), so no measure site can drift
  const MEASURES = SRC.match(/this\._mc\.font = [^;]+;/g) || [];
  assert.ok(MEASURES.length >= 4, "the known measure sites are present");
  for (const m of MEASURES) assert.match(m, /this\._fontFace\(\)/, "a measure bypasses _fontFace: " + m);
});

test("a pointerdown-opened menu survives its OWN opening click (the user 2026-08-24, click-and-hold bug)", () => {
  // the browser fires a click after pointerup; unstopped it bubbles to the document's menu-closer
  // and shuts the menu the instant it opened — only a mid-press redraw (element swapped, no click
  // at all) let it survive, which read as "hold to open". Every pointerdown anchor swallows it.
  assert.match(SRC, /open\(b\); \}\);[\s\S]{0,400}b\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
  assert.match(SRC, /this\._openLaneMenu\(s, ghit\);\n\s*\}\);[\s\S]{0,300}ghit\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/);
  assert.match(SRC, /x\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/, "the chip ✕ swallows its own click too");
});

test("the dropdown and dialog wear the shared menu vocabulary and adopt into the menu host", () => {
  assert.match(SRC, /'position:fixed;z-index:1001;min-width:200px;' \+ MENU_STYLE/);
  assert.match(SRC, /c\.setAttribute\('style', MENU_CHECK_STYLE\);/, "the ✓-in-circle current mark");
  assert.match(SRC, /'position:fixed;inset:0;z-index:1002;background:rgba\(0,0,0,0\.55\);'/,
    "the one modal dim, over the topmost same-origin document");
  assert.match(SRC, /const h = this\._menuHost\(anchorEl\.getBoundingClientRect\(\)\);[\s\S]{0,400}this\._viewsMenu = menu;/);
});

test("the sessions dialog is a TABLE speaking romp's own conventions (the user 2026-08-24, JLD-designed)", () => {
  // one grid, columns [name | chips | + | feed | (spare)] — the un-hide eye retired 2026-08-24; the [+] column's ALIGNMENT carries the
  // table structure (JLD: sequence in space suggests structure)
  assert.match(SRC, /grid-template-columns:max-content max-content 1fr;/);
  // the session NAME wears its identity colour directly (JLD: label directly, never a legend-like
  // proxy dot), the host: prefix is quiet lowercase italic, a dead session is struck — the same
  // read as the lanes and the feed. No model column, no instruction caption, no ellipsized names.
  assert.match(SRC, /font-weight:650;color:' \+ \(s\.color \|\| '#cccccc'\)/);
  assert.match(SRC, /font-style:italic;font-size:0\.88em;/);
  // closed sessions LEFT the membership table (the user 2026-08-25 revision) — live rows only,
  // so the strike variant is gone with them
  assert.match(SRC, /\.filter\(\(s\) => s\.live\)/, "the crossed-out ones don't show");
  const DLG = SRC.slice(SRC.indexOf('_openViewsDialog'), SRC.indexOf('_openLaneMenu('));
  assert.doesNotMatch(DLG, /s\.model/, "no model column");
  assert.doesNotMatch(SRC, /Tags mark specialized sessions/, "the display explains itself");
  // SEARCH (name or host — one string, the host prefix rides the name) + the bulk controls that
  // act on the FILTERED set: search is how a batch is selected (the user 2026-08-24)
  assert.match(SRC, /q\.placeholder = 'search name or host…';/);
  // 'tag all' moved to its OWN line as plain text (the user 2026-08-25 revision: it surprised where it sat)
  assert.match(SRC, /const tagAll = bulkLine\.createSpan\(\{ text: 'tag all' \}\);/);
  assert.ok(!SRC.includes("mute feed for all"), "the feed bulk control left the dialog (2026-08-25) — the flag lives on in the lane gear");
  assert.ok(!SRC.includes("const flagVal = ft.value(!anyOn);"), "the bulk feed wiring left with its control (2026-08-25)");
  // chips: outline in the tag's colour, dim separate ✕, hover changes colour (menu chrome)
  // the chips live in the SHARED name-keyed builder now (user ruling 2026-08-24): one chip per
  // tag NAME, ✕ = remove-everywhere via the union dispatcher — the dialog and the gear both call it
  assert.match(SRC, /ch\.createSpan\(\{ text: g\.name \}\);/);
  assert.match(SRC, /const chx = ch\.createSpan\(\{ text: '✕' \}\);/, "the ✕ is its own dim span — the composer chip's read");
  assert.doesNotMatch(SRC, /background:color-mix/, "no tinted chip grounds anywhere in the dialog");
  assert.match(SRC, /this\._editTagUnion\(g, \{ remove: \[s\.id\] \}\); rebuild\(\);/,
    "chip ✕ = remove-everywhere, through the one dispatcher");
  assert.match(SRC, /ni\.placeholder = 'new tag…';/, "minting a tag right from a row or the bulk bar");
  assert.match(SRC, /delete nv\.groups;/, "a write normalizes onto the tags key, never re-emitting the legacy one");
  // (the un-hide eye retired with the hidden set, the user 2026-08-24 — tags cover backgrounding)
  assert.doesNotMatch(SRC, /viewToggleHidden/);
  // the feed toggle still rides every live row (the user 2026-08-19 pool-builder rule), aligned in
  // its own column; NOT auto-coupled to membership.
  // (the dialog's hideFromFeed lookup left with the feed column, 2026-08-25 — the lane gear keeps the flag)/);
  // the per-row feed toggle left the dialog (2026-08-25); the lane gear still writes the same
  // optimistic sticky flags — pinned in its own test block

  // the menu is multi-select toggles since 2026-08-25: All first (a plain pick), (no tags) a
  // toggle second, tag toggles after; the one management entry is Configure tags… — creation
  // moved into the dialog's bulk bar
  assert.match(SRC, /item\('Configure tags…', \{ dim: true \}\)/);
  assert.match(SRC, /item\('All', \{ current: lensAll\(lens\) \}\)/);
  assert.match(SRC, /item\('\(no tags\)', \{ current: !lensAll\(lens\) && !!lens\.none \}\)/);
  assert.match(SRC, /item\('All',[\s\S]{0,300}item\('\(no tags\)',/, "All sits ABOVE (no tags) in the menu");
  assert.match(SRC, /item\('\(no tags\)',[\s\S]{0,600}for \(const g of viewTagUnion\(v\)\)/,
    "…and the tag rows come AFTER both built-ins — the DoD's menu order, pinned end to end (the rows are the NAME-KEYED union since the 2026-08-24 ruling)");
});

test("membership rows drag-reorder into the SHARED session order (the user 2026-08-25 revision)", () => {
  // EXECUTED: a drop permutes only the VISIBLE rows within the full order (out-of-dialog sessions
  // hold their absolute slots), applies optimistically, and persists through the ONE shared store
  // (session-order.json — the same write the tab-drag and lane-drag use, so all three surfaces
  // reorder together).
  const p: any = Object.create(TimelinePanel.prototype);
  p.data = { sessions: [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }] };
  // dialog shows a, c, d (b filtered out by search) — dragging d above a yields d,a,c with b fixed
  assert.deepEqual(p._mergeVisibleOrder(["d", "a", "c"]), ["d", "b", "a", "c"],
    "only the shown rows permute; the hidden one keeps its absolute slot");
  p._applyOrderToData(["d", "b", "a", "c"]);
  assert.deepEqual(p.data.sessions.map((s: any) => s.id), ["d", "b", "a", "c"], "optimistic — no snap-back before the next poll");
  // the wiring: grab a NAME cell; the insertion cue moves WITHOUT rebuilding mid-drag (the
  // redraw-eats-pointer rule) — the rebuild and the persist happen on the drop
  assert.match(SRC, /nameCell\.setAttribute\('style', 'white-space:nowrap;cursor:grab;'\);/);
  assert.match(SRC, /nameCell\._sid = s\.id;/);
  assert.match(SRC, /cells\[toIdx\]\.style\[toIdx > fromIdx \? 'borderBottom' : 'borderTop'\] = '2px solid #9cd2ff';/,
    "the accent insertion cue rides the target cell's border — no mid-drag rebuild");
  assert.match(SRC, /const full = this\._mergeVisibleOrder\(vis\);\s*\/\/ only the shown rows permute within the full order\n\s*this\._applyOrderToData\(full\);[\s\S]{0,200}this\._persistOrder\(full\);/,
    "drop = merge, apply, persist — the lane-drag's exact sequence");
  assert.match(SRC, /renderRows\(\);\n\s*\};\n\s*nameCell\.addEventListener\('pointermove', onMove\);/,
    "the rebuild happens on the drop, after the persist");
});

test("the dialog sizes to the screen: 90% ceiling both axes, padded edges, wrap only when narrow (the user 2026-08-25)", () => {
  // 560px read cramped — no padding at the edges, rows wrapping with plenty of screen left. The
  // card grows with the window to the big panels' ~90% family norm (capped 1200px so a huge
  // monitor doesn't stretch a form unreadably wide) with real breathing room inside the edges.
  // the card declarations come AFTER MENU_STYLE: the menu spec opens with ITS padding (4px), and
  // in one style string the later declaration wins — stated first, the dialog's padding had been
  // silently 4px all along (found by headless computed-style measurement, 2026-08-25)
  assert.match(SRC, /MENU_STYLE \+ 'box-sizing:border-box;width:min\(1200px,90vw\);max-height:90vh;'\s*\n\s*\+ 'overflow:hidden;display:flex;flex-direction:column;padding:22px 26px;font-size:13px;'/,
    "the card's screen-sized border-box footprint + edge padding, declared after the menu spec");
  // wrap stays GRACEFUL, not needless: the wide card lays the rows out on their lines; these
  // containers wrap only when the window genuinely narrows
  assert.match(SRC, /row\.setAttribute\('style', 'display:flex;align-items:center;gap:6px;margin:2px 0;flex-wrap:wrap;'\);/,
    "the five filter rows fold only under real pressure");
  assert.match(SRC, /chips\.setAttribute\('style', 'display:flex;gap:5px;flex-wrap:wrap;align-items:center;min-width:0;'\);/,
    "membership chip cells ditto");
  assert.match(SRC, /gridBox\.setAttribute\('style', 'flex:1 1 auto;min-height:0;overflow-y:auto;'\);/,
    "only the session rows pan when height runs out — the card itself never scrolls whole");
});

test("federation, NAME-KEYED (user ruling 2026-08-24): one name = one row/label/union — kernels are plumbing", () => {
  // the ruling superseded the v0 host-marked two-rows render: "if the UX requires understanding
  // that tags exist across different kernels, it is not good". Executed on the mirror:
  const both = { active: "TESTHOST-A:g1",
                 tags: [{ id: "gL", name: "team", color: "#123456", members: ["local1"] }],
                 remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", color: "#DD42FF", members: ["m1"] }] };
  assert.equal(viewLabel(both), "team", "the NAME, never a host prefix");
  assert.equal(viewVisible(both, "m1"), true, "the union: the remote store's member shows");
  assert.equal(viewVisible(both, "local1"), true, "…and the local store's, under ONE view");
  assert.equal(viewVisible(both, "other"), false);
  const u = viewTagUnion(both);
  assert.equal(u.length, 1, "one name = one identity — the twin-chip render is gone");
  assert.equal(u[0].color, "#123456", "the LOCAL store's colour wins the render, deterministically");
  assert.deepEqual(u[0].members.slice().sort(), ["local1", "m1"]);
  assert.equal(viewVisible({ active: "TESTHOST-A:g1", tags: [] }, "m2"), true, "gone → falls open");
  // the menu: one row per union tag, picked via the handiest id (local first)
  assert.match(SRC, /for \(const g of viewTagUnion\(v\)\)/);
  assert.match(SRC, /apply\(lensToggle\(lens, \{ tag: g\.name \}\), false\)/);
  assert.doesNotMatch(SRC, /border:1px dashed/, "no dashed twin chips anywhere — one solid chip per name");
  // the gray-glyph fix (unchanged): on an exact miss the colour join retries by the bare sid tail
  assert.match(SRC, /s = data\.sessions\.find\(\(x\) => x\.id === bare \|\| String\(x\.id\)\.endsWith\(':' \+ bare\)\);/);
});

test("the N-more count opens the views menu — what am I not seeing answers itself (the user 2026-08-24)", () => {
  assert.match(SRC, /t\.addEventListener\('pointerdown', \(e\) => \{ e\.preventDefault\(\); e\.stopPropagation\(\); this\._openViewsMenu\(t\); \}\);/);
  assert.match(SRC, /t\.addEventListener\('click', \(e\) => e\.stopPropagation\(\)\);/, "and survives its own opening click");
});

test("the two display toggles write the host's own romp:settings — reachable in every host now", () => {
  assert.match(SRC, /item\('Collapse idle gaps', \{ current: !!this\._collapseGaps, dim: true \}\)/);
  assert.match(SRC, /item\('Active sessions only', \{ current: !!this\._activeOnly, dim: true \}\)/);
  assert.match(SRC, /localStorage\.setItem\('romp:settings', JSON\.stringify\(s\)\);/);
});

test("_setViews posts through the host hook with a GUARDED, atomic Obsidian fallback", () => {
  assert.match(SRC, /window\.__rompTimelineSetViews === 'function'/);
  // Electron-gated (a bare-node test run must never touch the real file — the 2026-07-02 lesson),
  // env-aware state root, tmp+rename so a reader never sees a torn blob
  assert.match(SRC, /process\.versions && process\.versions\.electron/);
  assert.match(SRC, /process\.env\.ROMP_STATE_DIR\n?\s*\|\| path\.join\(process\.env\.XDG_STATE_HOME \|\| path\.join\(os\.homedir\(\), '\.local', 'state'\), 'romp'\)/);
  assert.match(SRC, /fs\.renameSync\(fp \+ '\.tmp', fp\);/);
  assert.match(SRC, /this\._pendingViews = v; this\._pendingViewsAge = 0;/);
  assert.match(SRC, /this\._reconcileViews\(\);\s*\/\/ \.\.\.and an optimistic view edit/);
});

test("executed: the dialog's membership mutation, pure (viewToggleHidden retired 2026-08-24 with the hidden set)", () => {
  const v = { active: "all", tags: [{ id: "g1", members: ["m"] }] };
  assert.deepEqual(viewToggleMember(v, "g1", "m").tags[0].members, [], "leave");
  assert.deepEqual(viewToggleMember(v, "g1", "n").tags[0].members, ["m", "n"], "join");
  assert.deepEqual(viewToggleMember(v, "ghost", "n"), v, "an unknown tag mutates nothing");
  assert.ok(!("viewToggleHidden" in requireCjs(VIEW_PATH)), "the hide mutation is gone from the exports");
});

test("the trigger measures its WHOLE string against the gutter, and the dialog's Escape hook dies on every close", () => {
  // the fit measures the whole line as LAID OUT: trigger + gap + padded chip + gap + tail
  // per-chip budgeting since 2026-08-25: chips render while they fit; the rest collapse into +N
  assert.match(SRC, /const chipW = \(c\) => 18 \+ this\.labelWidth\(c\.label\) \+ 6 \+ 8;/);
  assert.match(SRC, /const budget = this\.M\.left - PADL - \(BTNW \* 2 \+ GAP\) - \(tailStr \? GAP \+ this\.labelWidth\(tailStr\) : 0\);/);
  assert.match(SRC, /if \(used \+ w \+ restW > budget\) break;/);

  assert.match(SRC, /this\._viewsDialogKey = \{ doc: h\.doc, fn: onKey \};/);
  assert.match(SRC, /this\._viewsDialogKey\.doc\.removeEventListener\('keydown', this\._viewsDialogKey\.fn\);/);
});

test("the views menu closes with its siblings on outside click / Escape / pagehide", () => {
  assert.match(SRC, /this\._onDocClick = \(\) => \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \};/);
  assert.match(SRC, /if \(e\.key === 'Escape'\) \{ this\._closeMetaMenu\(\); this\._closeLaneMenu\(\); this\._closeViewsMenu\(\); \}/);
  assert.match(SRC, /this\._closeViewsMenu\(\); this\._closeViewsDialog\(\);/, "pagehide drops both overlays");
});

test("executed: a remote-tag edit renders optimistically, echoes on the poll, yields after three silences", () => {
  // the sessionViews reconcile precedent, per remote tag (federation v1)
  const p: any = Object.create(TimelinePanel.prototype);
  p._pendingViews = null; p._pendingTagEdits = {}; p._views = { active: "all", hidden: [], tags: [],
    remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", color: "#DD42FF", members: ["m1"] }] };
  // the optimistic overlay: a member add renders immediately
  p._pendingTagEdits["TESTHOST-A:g1"] = { tag: { id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team",
    color: "#DD42FF", members: ["m1", "m2"] }, age: 0 };
  assert.deepEqual(p._curViews().remoteTags[0].members, ["m1", "m2"], "pending copy renders");
  // the owner's poll echoes (order-insensitive) → the pending clears
  p._views = { active: "all", hidden: [], tags: [], remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A",
    name: "team", color: "#DD42FF", members: ["m2", "m1"] }] };
  p._reconcileTagEdits();
  assert.deepEqual(p._pendingTagEdits, {}, "echo match clears the pending edit");
  // never echoed → three silent pushes yield to the polled truth
  p._pendingTagEdits["TESTHOST-A:g1"] = { tag: { id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "renamed",
    color: "#DD42FF", members: [] }, age: 0 };
  p._reconcileTagEdits(); p._reconcileTagEdits();
  assert.ok(p._pendingTagEdits["TESTHOST-A:g1"], "two silences → still holding");
  p._reconcileTagEdits();
  assert.deepEqual(p._pendingTagEdits, {}, "the third yields — the owner refused or another dashboard won");
  // a pending DELETE hides the tag meanwhile
  p._pendingTagEdits["TESTHOST-A:g1"] = { tag: null, age: 0 };
  assert.equal((p._curViews().remoteTags || []).length, 0, "a pending delete renders as gone");
});

test("executed: tagEditFailed reverts the optimistic copy and keeps the reason for the dialog", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  p._pendingViews = null; p._views = { active: "all", hidden: [], tags: [] };
  p._pendingTagEdits = { "TESTHOST-A:g1": { tag: null, age: 0 }, "TESTHOST-B:g2": { tag: null, age: 0 } };
  p._viewsDialog = null; p._viewsDialogBuild = null; p.draw = () => {};
  p.tagEditFailed({ host: "TESTHOST-A", name: "team", error: "not reachable" });
  assert.deepEqual(Object.keys(p._pendingTagEdits), ["TESTHOST-B:g2"],
    "only the failing owner's pendings revert — B's edit is still in flight");
  assert.equal(p._tagEditErr.error, "not reachable");
});

test("federation v1+ruling source pins: header/chips route through the UNION dispatcher, loudly on failure", () => {
  // rename/recolor/delete fan out to EVERY home; chip ✕ removes everywhere; add prefers local
  assert.match(SRC, /this\._editTagUnion\(tg, \{ rename: nv2 \}\);/);
  assert.match(SRC, /this\._editTagUnion\(tg, \{ color: c \}\); build\(\);/);
  assert.match(SRC, /this\._editTagUnion\(tg, \{ delete: true \}\);/);
  assert.match(SRC, /this\._editTagUnion\(g, \{ remove: \[s\.id\] \}\); rebuild\(\);/);
  assert.match(SRC, /this\._editTagUnion\(g, \{ add: rowIds\.filter\(\(id\) => g\.members\.indexOf\(id\) < 0\) \}\); rebuild\(\);/);
  // the remote transport underneath is unchanged: no hook (the Obsidian panel) → read-only + an
  // immediate visible refusal; the error line is dismissible and names the owner
  assert.match(SRC, /typeof window\.__rompTimelineEditTag !== 'function'/);
  assert.match(SRC, /er\.createSpan\(\{ text: '⚠ ' \+ \(this\._tagEditErr\.host \? this\._tagEditErr\.host \+ ': ' : ''\) \+ this\._tagEditErr\.error \}\);/);
  // a NEW tag still mints locally, posting the whole blob (zero local-path change)
  assert.match(SRC, /nv\.tags = viewTags\(nv\)\.concat/);
});

test("executed: the union dispatcher — add prefers local, remove reaches every store, delete fans out", () => {
  const p: any = Object.create(TimelinePanel.prototype);
  const setViews: any[] = []; const remote: any[] = [];
  p._setViews = (v: any) => setViews.push(v);
  p._editRemoteTag = (rt: any, e: any) => remote.push([rt.id, e]);
  const rtA = { id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", members: ["m1", "x"] };
  const rtB = { id: "TESTHOST-B:g7", host: "TESTHOST-B", name: "team", members: ["m1"] };
  const local = { id: "gL", name: "team", color: "#123456", members: ["m1"] };
  p._views = { active: "all", hidden: [], tags: [local], remoteTags: [rtA, rtB] };
  p._pendingViews = null; p._pendingTagEdits = {};
  const g = { name: "team", color: "#123456", members: ["m1", "x"], ids: ["gL", rtA.id, rtB.id],
              localId: "gL", homes: ["TESTHOST-A", "TESTHOST-B"], remotes: [rtA, rtB] };
  // ADD prefers the local store when the name exists locally — no remote call at all
  p._editTagUnion(g, { add: ["new1"] });
  assert.equal(setViews.length, 1);
  assert.deepEqual(setViews[0].tags[0].members.slice().sort(), ["m1", "new1"]);
  assert.equal(remote.length, 0, "add lands locally, never forks to remotes");
  // …and on the single home when the name is remote-only
  const gRemote = { ...g, localId: null, ids: [rtA.id], homes: ["TESTHOST-A"], remotes: [rtA] };
  p._editTagUnion(gRemote, { add: ["new2"] });
  assert.deepEqual(remote.pop(), ["TESTHOST-A:g1", { add: ["new2"] }]);
  // REMOVE removes the (name, member) pair from EVERY store holding it — never half-works
  setViews.length = 0; remote.length = 0;
  p._editTagUnion(g, { remove: ["m1"] });
  assert.equal(setViews.length, 1, "the local store cleans");
  assert.deepEqual(remote.map((r: any) => r[0]).sort(), ["TESTHOST-A:g1", "TESTHOST-B:g7"],
    "…and BOTH remote stores holding the pair");
  // a remote NOT holding the member is left alone
  setViews.length = 0; remote.length = 0;
  p._editTagUnion(g, { remove: ["x"] });
  assert.deepEqual(remote.map((r: any) => r[0]), ["TESTHOST-A:g1"], "only the holder is touched");
  // DELETE fans out to every home
  setViews.length = 0; remote.length = 0;
  p._editTagUnion(g, { delete: true });
  assert.equal(setViews.length, 1);
  assert.equal(setViews[0].tags.length, 0, "the local tag goes");
  assert.deepEqual(remote.map((r: any) => r[1].delete), [true, true], "…and every remote home");
});

test("the lane gear carries the SAME tag editor — the shared builders, never a fork (the user 2026-08-24)", () => {
  // both surfaces call the one chip builder and the one join menu
  assert.ok((SRC.match(/this\._tagChips\(/g) || []).length >= 2, "dialog rows AND the gear");
  assert.ok((SRC.match(/this\._tagJoinMenu\(/g) || []).length >= 2, "dialog [+] menus AND the gear");
  // the gear's section: compact label row + [+] behind it, menu vocabulary throughout
  assert.match(SRC, /const tlab = trow\.createSpan\(\{ text: 'Tags' \}\);/);
  assert.match(SRC, /this\._tagJoinMenu\(am, \[s\.id\], build\);/);
});

test("the lane model menu exposes VERSIONS: submenu affordance, remembered default, keyboard (the user 2026-08-25)", () => {
  // families with >1 live version wear a side submenu — hover or ArrowRight reveals it, every
  // version directly pickable with the current-✓; clicking the family picks its remembered DEFAULT
  // (the kernel /models `default` field), never a bare shorthand that silently resolves to newest
  assert.match(SRC, /const versions = kind === 'model' \? \(c\.versions \|\| \[\]\) : \[\]/);
  assert.match(SRC, /pick\(kind === 'model' \? \(c\.default \|\| c\.value\) : c\.value\)/,
    "family click sends the remembered default");
  assert.match(SRC, /versions\.length > 1 \?/, "single-version families stay flat");
  assert.match(SRC, /text: '\\u25B8'/, "the caret affordance marks expandable families");
  assert.match(SRC, /e\.key === 'ArrowRight' && openSub/, "right-arrow expands (keyboard operable)");
  assert.match(SRC, /e\.key === 'ArrowLeft'[\s\S]{0,80}closeSub\(\); item\.focus\(\)/,
    "left-arrow collapses back to the family row");
  assert.match(SRC, /pick\(v\.value\)/, "version rows pick their own full id");
  assert.match(SRC, /\(s\.model \|\| ''\)\.toLowerCase\(\) === v\.label\.toLowerCase\(\)/,
    "the ✓ marks the lane's current version");
  assert.match(SRC, /if \(this\._metaMenu\._sub\) this\._metaMenu\._sub\.remove\(\)/,
    "closing the menu drops an open submenu too");
});

test("executed: the timeline lens — toggles, All exclusivity, last-off→All, union visibility (the user 2026-08-25)", () => {
  // parity with ui/webview/tag-lens.ts (the shared model, promoted from the feed's branch)
  let l = { all: true };
  l = lensToggle(l, { tag: "infra" });
  assert.deepEqual(l, { tags: ["infra"] }, "picking a tag leaves All");
  l = lensToggle(l, "none");
  assert.deepEqual(l, { none: true, tags: ["infra"] }, "arbitrary combinations");
  l = lensToggle(l, { tag: "infra" });
  l = lensToggle(l, "none");
  assert.deepEqual(l, { all: true }, "toggling the last selection off returns to All");
  assert.deepEqual(lensToggle({ none: true, tags: ["a", "b"] }, "all"), { all: true }, "All is exclusive");
  const unions = [
    { name: "infra", members: ["s1", "alpha:r1"] },
    { name: "workers", members: ["s2"] },
  ];
  assert.ok(lensVisible({ all: true }, unions, "anything"));
  assert.ok(lensVisible({ tags: ["infra"] }, unions, "alpha:r1"), "union members incl. remote");
  assert.ok(!lensVisible({ tags: ["infra"] }, unions, "s2"));
  assert.ok(lensVisible({ none: true }, unions, "loose"), "none = in no tag home");
  assert.ok(!lensVisible({ none: true }, unions, "s1"));
  assert.ok(lensVisible({ none: true, tags: ["workers"] }, unions, "s2"), "union over buckets");
  assert.equal(lensLabel({ none: true, tags: ["infra"] }), "infra + no tags");
  assert.deepEqual(timelineLens({ actives: { timeline: { tags: ["x"] } } }), { tags: ["x"] });
  assert.deepEqual(timelineLens({}), { all: true }, "a pre-lens blob with no view reads All");
  // a legacy scalar SEEDS the lens client-side too — the kernel's migration rule, mirrored, so an
  // un-upgraded blob keeps its exact old behavior instead of falling open
  assert.deepEqual(timelineLens({ active: "untagged" }), { none: true });
  assert.deepEqual(
    timelineLens({ active: "g1", tags: [{ id: "g1", name: "pool", members: [] }] }),
    { tags: ["pool"] });
});

test("the corner grew two icon buttons and the menus split (the user 2026-08-25)", () => {
  // display options (sliders) LEFT of the tag filter (tag icon); both monochrome, tooltip-worded
  assert.match(SRC, /btn\('timeline display options',[\s\S]{0,900}btn\('filter these lanes by tag',/,
    "sliders sit left, the tag button beside it (append order = flex order)");
  assert.match(SRC, /_openDisplayMenu\(b\)/);
  // the menu scope captions retired 2026-08-25 (the user: the tooltip already says it) — the
  // button TOOLTIP is the one scope carrier now, and the menus open straight onto their rows
  assert.ok(!SRC.includes("capSep("), "the tag menu's scope caption is gone");
  assert.ok(!SRC.includes("how this timeline draws"), "the display menu's header is gone");
  assert.match(SRC, /btn\('filter these lanes by tag',/, "…the tooltip names the surface instead");
  assert.match(SRC, /item\('Configure tags…', \{ dim: true \}\)/, "one management entry");
  assert.ok(!/item\('New tag…', \{ dim: true \}\)/.test(SRC), "New tag left the menu…");
  assert.match(SRC, /text: '\+ New tag'/,
    "…and lives in the tag TABLE's final row (the 18:17 revision — the bulk-bar copy died)");
  assert.match(SRC, /apply\(lensToggle\(lens, \{ tag: g\.name \}\), false\)/,
    "tag rows TOGGLE and the menu stays open (repaint in place)");
  assert.match(SRC, /apply\(\{ all: true \}, true\)/, "All is a plain pick and closes");
  assert.match(SRC, /nv\.actives = Object\.assign\(\{\}, nv\.actives, \{ timeline: nl \}\)/,
    "writes land on THIS surface's lens only");
});

test("cross-pane dismissal rides the storage echo (the user 2026-08-25)", () => {
  assert.match(SRC, /e\.key === 'romp:menu-echo' && e\.newValue/, "every open menu listens");
  assert.match(SRC, /localStorage\.setItem\('romp:menu-echo'/, "every pane writes the pointerdown echo");
  assert.match(SRC, /addEventListener\('storage', this\._onMenuEcho\)/);
});

test("dialog polish + reachable tag management (the user 2026-08-25)", () => {
  // the dialog reads at the page's 13px form scale (the menu 12px was the too-small complaint)
  assert.match(SRC, /padding:22px 26px;font-size:13px;'/,
    "the 13px form scale rides the card's own declarations (after MENU_STYLE, whose 4px padding they beat)");
  // the session table scrolls WITHIN the modal — chrome stays put
  assert.match(SRC, /gridBox\.setAttribute\('style', 'flex:1 1 auto;min-height:0;overflow-y:auto;'\)/);
  // [+] is a rounded RECTANGLE in its own column between name and tags
  assert.match(SRC, /padding:1px 7px;'\n\s*\+ 'border-radius:5px;/, "the standard button anatomy, not a circle");
  assert.ok(!/width:17px;height:17px;'\n?\s*\+ 'border-radius:50%/.test(SRC), "the circle plus is gone");
  // the feed column + mute-all left the dialog; the flag lives on in the lane gear
  assert.ok(!SRC.includes("its prompts make feed cards"), "no per-row feed toggle here");
  assert.ok(!SRC.includes("mute feed for all"), "no bulk feed control here");
  // tag management reachable from EVERY open: rows with rename/recolor/delete via the union dispatcher
  assert.match(SRC, /text: 'the tags'/);
  assert.match(SRC, /this\._editTagUnion\(tg, \{ delete: true \}\);\n\s*build\(\);/, "delete without a tag-scoped open");
  assert.match(SRC, /this\._editTagUnion\(tg, \{ color: c \}\); build\(\);/, "the identity-palette recolor");
});

test("the dialog redesign: tag TABLE with delete/rename/color actions, five filter rows (the user 2026-08-25, revised same day)", () => {
  // TAGS: a TABLE (the user's revision of the chip cloud) — each row the tag pill at normal size
  // with NO ✕ on it, then delete | rename | color swatches as their own columns; delete wears the
  // destructive convention (dim at rest, red on hover); [+ New tag] is the table's FINAL row
  assert.match(SRC, /grid-template-columns:max-content max-content max-content 1fr;/, "the tag table's four columns");
  assert.match(SRC, /the tag itself: the normal pill, NO ✕ — actions live beside it, never on it/);
  assert.match(SRC, /this\._tagEditorFor = this\._tagEditorFor === tg\.name \? null : tg\.name;/, "rename toggles the pill into an input");
  assert.match(SRC, /d\.style\.color = '#F85B5A'/, "delete goes red on hover — destructive, unlike membership ✕");
  assert.match(SRC, /DELETE the tag/, "the hover says what delete does");
  assert.match(SRC, /text: '\+ New tag'/, "creation is the table's final row");
  assert.match(SRC, /grid-column:1 \/ -1;/, "…spanning the table's full width");
  // FILTERS: five rows — All surfaces / Chat / Sessions / Outline / Feed (the pane names), each
  // the full lens vocabulary as pills editing ONLY its surface; All-surfaces fans to all four
  assert.match(SRC, /\[\['All surfaces', '\*'\], \['Chat', 'chat'\], \['Sessions', 'timeline'\], \['Outline', 'outline'\], \['Feed', 'feed'\]\]/);
  assert.match(SRC, /const upd = key === '\*' \? \{ chat: lens, timeline: lens, outline: lens \} : \{\};/,
    "All-surfaces writes all three kernel lenses…");
  assert.match(SRC, /if \(key === '\*' \|\| key === 'feed'\) \{\n\s*try \{ localStorage\.setItem\('romp:feedTags-set'/,
    "…and reaches the feed (and the feed row itself) through the adoption echo — the placeholder died");
  assert.match(SRC, /return four\.every\(\(l\) => canonL\(l\) === canonL\(four\[0\]\)\) \? four\[0\] : null;/,
    "the All-surfaces row shows agreement, or (mixed)");
  assert.match(SRC, /as last set from this dialog/, "the feed row's display is honest about its source");
  assert.match(SRC, /pane filters — what each pane shows/, "the sections are separately captioned");
});

