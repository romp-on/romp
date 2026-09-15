#!/usr/bin/env python3
"""The shell's pane gutters must WORK with split chat columns in the row, not just parse (2026-09-08).

The dashboard's chat|fleet|feed panes are sized by flex-grow weights the gutter script (_LANDING_JS in
kernel.py) keeps in sync with `--g-<k>` vars on .row and persists in localStorage. Split screen (the user
2026-09-08, who wanted several sessions side by side instead of tabbing through them) adds chat columns to
that row AFTER the script has run, so the script grew a registry: a column registers its pane id and grow
key, takes a fair grow (the average of what is already on screen) or the width it was dragged to before a
reload, and gets its own chat|chat gutter wired through the same drag code. The review of the first cut found
the fair grow averaging the new column's own undefined grow in as NaN, so the first split opened 0px wide;
that is the regression this file pins by running the code, since a source pin cannot tell NaN from 50.

The chat ROWS (the user 2026-09-15) put every chat column inside #chat-area, a wrapper that takes the outer
row's --g-chat weight, holding two flex rows of columns (the first pane on its own inner weight --g-chat1):
a weight is relative to its CONTAINER, so a grab, a halving or a hand-back normalises the grabbed pane's
siblings alone and never moves the outer weights; gv-a/gv-b/gv-c pair the chat AREA with the outline, the
feed and the files; and the gutter between the two rows rides the same drag code vertically, reporting the
top row's share instead of writing a grow (__rompRowGutter). A store from before the rows seeds chat1 from
chat, so a stored later column keeps its proportion against the first pane.

This EXECUTES the real _LANDING_JS in node against a DOM stub (the test_error_center.py pattern) and drives
the whole story over one JSON result: the boot defaults; a fresh column's fair grow over its row; the reload
paths of __rompGrowFairIfNew (a stored width is kept, a missing one falls to the average) by re-running the
blob against a re-seeded store; a drag on a chat|chat gutter moving only that pair inside the row; gv-a
pairing the chat area with the outline (fleet) pane; unregistering a closed column; the unregistered fallback
keys; the halving and the hand-back inside a row; the row gutter's drag; and the pre-rows store's seed. The
stub records the `--g-*` vars the script sets on .row, because the script's `grow` object is a closure.

Synthetic only: no network, no real DOM, invented widths.
"""
import json
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_gutters", os.path.join(BIN, "romp-kernel"))

# Everything _LANDING_JS touches, and nothing else: .col (the timeline band's --tl var, never driven here),
# .row (records the --g-* vars), the gutters + panes by id — each pane with the parentElement the served
# markup gives it (the chat area and the other panes in .row, the chat panes in #chat-row-1, the two chat
# rows in #chat-area), since a grab normalises the grabbed pane's siblings — getComputedStyle(el).display
# for shown(), body.classList (po-fleet / po-timeline reads, drag/dragv/dragh writes), localStorage, and the
# window's mousemove/mouseup listeners a drag installs and removes. f-timeline is stubbed PRESENT (the served
# shell always carries the iframe, kernel.py `<iframe id=f-timeline …>`), so the 'load' hookup runs; the
# script also guards the null case (`tf&&…`, `tf?…:0`), so a stub without it would pass too.
HARNESS = r"""
'use strict';
const STORE = {};
global.localStorage = {
  getItem: (k) => (k in STORE ? STORE[k] : null),
  setItem: (k, v) => { STORE[k] = String(v); },
  removeItem: (k) => { delete STORE[k]; },
};
const ROW = {};   // the --g-* vars the script sets on .row (its `grow` object is a closure)
const rowEl = { style: { setProperty: (k, v) => { ROW[k] = v; }, removeProperty: (k) => { delete ROW[k]; } }, offsetWidth: 1007,   // the shell's row: 1007 px wide (the upgrade hook's W)
                getBoundingClientRect: () => ({ top: 0, height: 800, left: 0, bottom: 800 }) };   // the landing line is placed by the row's rect (main, 2026-09)
const colEl = { style: { setProperty() {} }, getBoundingClientRect: () => ({ bottom: 800 }) };
// the containers the panes sit in: the chat rows inside the chat area (the served markup, kernel.py _landing)
const areaEl = { id: 'chat-area-box', getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 807, bottom: 807 }) };
const row1El = { id: 'chat-row-1-box', getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 400, bottom: 400 }) };
function mkEl(id, w, display, parent, h) {
  return {
    id: id, offsetWidth: w, offsetHeight: h || 800, _display: display, _ls: {}, parentElement: parent || rowEl, style: {}, isConnected: true,
    getBoundingClientRect() { return { left: 0, top: this._top || 0, width: this.offsetWidth, height: this.offsetHeight, bottom: (this._top || 0) + this.offsetHeight }; },   // the drag's landing line reads the left pane's rect
    addEventListener(k, f) { (this._ls[k] = this._ls[k] || []).push(f); },
    fire(k, ev) { (this._ls[k] || []).slice().forEach((f) => f(ev)); },
  };
}
let EL = {};
let WL = {};
let DL = {};        // the document's listeners (visibilitychange: a gesture's cancel source)
let APPLIED = [];   // the shares the row gutter's release reports
// Fresh gutters, panes and window listeners for every boot of the blob: a re-run models a RELOAD, and the
// previous instance's mousedown handlers must not linger on the same stub gutter (two closures dragging
// one row would corrupt every width below).
function resetDom() {
  EL = {};
  WL = {};
  DL = {};
  APPLIED = [];
  document.visibilityState = 'visible';
  ['gh', 'gv-a', 'gv-b', 'gv-c', 'gv-chat-2', 'gv-chat-3', 'gv-rows', 'f-timeline', 'gv-ghost', 'gv-ghost-h'].forEach((id) => { EL[id] = mkEl(id, 0, 'flex'); });
  EL['chat-area'] = mkEl('chat-area', 600, 'flex');   // the chat area: the outer row's chat side (the rows, 2026-09-15)
  EL['chat-pane'] = mkEl('chat-pane', 600, 'flex', row1El);
  EL['fleet-pane'] = mkEl('fleet-pane', 300, 'none');   // hidden by default, like the real rail
  EL['feed-pane'] = mkEl('feed-pane', 400, 'flex');
  EL['files-pane'] = mkEl('files-pane', 400, 'none');   // the fourth top pane (main, 2026-09), hidden by default
  EL['chat-pane-2'] = mkEl('chat-pane-2', 500, 'flex', row1El);   // the split's client-made column (shown once made), in the top row
  EL['chat-pane-3'] = mkEl('chat-pane-3', 450, 'flex', row1El);
  EL['chat-pane-4'] = mkEl('chat-pane-4', 300, 'flex', row1El);
  EL['chat-row-1'] = mkEl('chat-row-1', 600, 'flex', areaEl, 400);   // the two chat rows, 400 px each behind the 7 px row gutter
  EL['chat-row-2'] = mkEl('chat-row-2', 600, 'flex', areaEl, 400); EL['chat-row-2']._top = 407;
  for (const k in ROW) delete ROW[k];
}
const BODY = new Set(['po-chat', 'po-feed']);
global.window = {
  innerHeight: 900,
  addEventListener: (k, f) => { (WL[k] = WL[k] || []).push(f); },
  removeEventListener: (k, f) => { WL[k] = (WL[k] || []).filter((g) => g !== f); },
};
global.getComputedStyle = (el) => ({ display: el._display });
global.document = {
  querySelector: (sel) => (sel === '.col' ? colEl : sel === '.row' ? rowEl : null),
  getElementById: (id) => EL[id] || null,
  visibilityState: 'visible',
  addEventListener: (k, f) => { (DL[k] = DL[k] || []).push(f); },
  body: { classList: {
    contains: (c) => BODY.has(c),
    add: (...cs) => cs.forEach((c) => BODY.add(c)),
    remove: (...cs) => cs.forEach((c) => BODY.delete(c)),
  } },
};
function docFire(k, ev) { (DL[k] || []).slice().forEach((f) => f(ev)); }
function showFleet(on) { EL['fleet-pane']._display = on ? 'flex' : 'none'; if (on) BODY.add('po-fleet'); else BODY.delete('po-fleet'); }
function winFire(k, ev) { (WL[k] || []).slice().forEach((f) => f(ev)); }
function grows() { return Object.assign({}, ROW); }
function store() { return JSON.parse(STORE['romp-pane-grow'] || 'null'); }
function ghostH() { return Object.assign({}, EL['gv-ghost-h'].style); }
// the browser's flex arithmetic for one container: `avail` px shared by weight (flex-basis 0, every item shown)
function flex(avail, ws) { const T = ws.reduce((a, b) => a + b, 0); return ws.map((w) => avail * w / T); }
// the widths a PRE-ROWS shell rendered from a pane store: every chat column and every shown outer pane in ONE row, a 7 px
// gutter between each pair — and the widths the rows shell renders from the weights the upgrade hook leaves: the outer
// row (the area, then the shown outer panes), then the chat columns inside the area behind their own gutters
// …a column the pre-rows store held NO weight for took the pre-rows fair grow as it was made: the mean of the finite weights
// of the panes then ON SCREEN (the first pane and the columns made before it while the chat pane is shown, the shown outer
// panes), in restoration order, 50 with nothing on screen; the pre-rows defaults stand in for keys the store lacks (chat 60,
// feed 40, fleet 34, files 40). With the chat pane HIDDEN at the restore, the rail's Chat later fair-grew the first pane over
// the panes on screen at that moment (the chat panes still hidden), then showed it: the show-time step below.
const OLD_DEFAULTS = { chat: 60, fleet: 34, feed: 40, files: 40 };
const mean = (v) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : 50);
function legacyResolved(store, colKeys, outerShown, chatHidden) {
  const w = Object.assign({}, OLD_DEFAULTS, store); const made = ['chat'];
  colKeys.forEach((k) => { if (typeof w[k] !== 'number') w[k] = mean((chatHidden ? [] : made).concat(outerShown).map((j) => w[j])); made.push(k); });
  if (chatHidden) w.chat = mean(outerShown.map((j) => w[j]));
  return w;
}
function legacyWidths(store, colKeys, outerShown, chatHidden) {
  const w = legacyResolved(store, colKeys, outerShown, chatHidden);
  const chat = ['chat'].concat(colKeys), items = chat.concat(outerShown);
  const px = flex(1007 - 7 * (items.length - 1), items.map((k) => w[k]));
  const o = {}; items.forEach((k, i) => { o[k === 'chat' ? 'chat1' : k] = px[i]; }); return o;
}
function rowsWidths(g, colKeys, outerShown) {
  const outer = ['chat'].concat(outerShown), opx = flex(1007 - 7 * (outer.length - 1), outer.map((k) => g[k]));
  const o = {}; outer.forEach((k, i) => { if (k !== 'chat') o[k] = opx[i]; });
  const inner = ['chat1'].concat(colKeys), ipx = flex(opx[0] - 7 * (inner.length - 1), inner.map((k) => g[k]));
  inner.forEach((k, i) => { o[k] = ipx[i]; }); return o;
}
function drag(gid, x0, x1) {
  const snap = {};
  EL[gid].fire('mousedown', { preventDefault() {}, clientX: x0 });
  snap.afterDown = grows();           // every shown pane normalised to its px width
  snap.dragging = BODY.has('drag') && BODY.has('dragv');
  snap.listeners = { move: (WL['mousemove'] || []).length, up: (WL['mouseup'] || []).length };
  winFire('mousemove', { clientX: x1 });
  snap.afterMove = grows();
  winFire('mouseup', {});
  snap.afterUp = grows();
  snap.dragAfterUp = BODY.has('drag') || BODY.has('dragv');
  snap.listenersAfterUp = { move: (WL['mousemove'] || []).length, up: (WL['mouseup'] || []).length };
  snap.store = store();
  return snap;
}
// the ROW gutter's drag: the pointer's Y, the horizontal landing line, the share reported at release
function dragRows(y0, y1) {
  const snap = {};
  EL['gv-rows'].fire('mousedown', { preventDefault() {}, clientY: y0 });
  snap.afterDown = { grows: grows(), ghost: ghostH(), dragging: BODY.has('drag') && BODY.has('dragh'), dragv: BODY.has('dragv') };
  winFire('mousemove', { clientY: y1 });
  snap.afterMove = { grows: grows(), ghost: ghostH(), applied: APPLIED.slice() };
  winFire('mouseup', {});
  snap.afterUp = { grows: grows(), ghost: ghostH(), applied: APPLIED.slice(), drag: BODY.has('drag') || BODY.has('dragh'), store: store(),
                   listeners: { move: (WL['mousemove'] || []).length, up: (WL['mouseup'] || []).length } };
  return snap;
}
"""

DRIVER = r"""
const out = {};
// 1) boot with an empty store: the defaults land on .row
resetDom();
BOOT();
out.boot = { grows: grows(), store: store() };
// 2) a split column registers, then asks for a fair grow while it has NO grow of its own yet: the average
//    of its ROW's shown panes' finite grows (the first pane's chat1 60 — the outer row's chat 60 and feed 40 are
//    another container's scale), never NaN (review find 2026-09-08)
window.__rompRegisterPane('chat-pane-2', 'chat2');
window.__rompGrowFair('chat2');
out.fair = { grows: grows(), store: store(),
  finite: typeof ROW['--g-chat2'] === 'number' && isFinite(ROW['--g-chat2']) };
// 3a) a RELOAD with a stored width for the column: __rompGrowFairIfNew keeps it (no re-fair). The stores from here to 9)
//     carry chat1: current-shape stores, since a pre-rows one (no chat1) is upgraded by the split's boot call and keeps its
//     pre-rows shape until then — tests 11 to 13 cover that path; these are about fair grows and drags
resetDom();
STORE['romp-pane-grow'] = JSON.stringify({ chat: 60, chat1: 60, fleet: 34, feed: 40, chat2: 123 });
BOOT();
window.__rompRegisterPane('chat-pane-2', 'chat2');
window.__rompGrowFairIfNew('chat2');
out.ifNewKept = { grows: grows(), store: store() };
// 3b) a RELOAD with nothing stored for the column: it falls through to the fair average
resetDom();
STORE['romp-pane-grow'] = JSON.stringify({ chat: 60, chat1: 60, fleet: 34, feed: 40 });
BOOT();
window.__rompRegisterPane('chat-pane-2', 'chat2');
window.__rompGrowFairIfNew('chat2');
out.ifNewFair = { grows: grows(), store: store() };
// 4) the column's own chat|chat gutter, wired the way the split script wires it (left = the previous
//    column, here the first chat pane; right = the new column). Drag 100px to the right: the top row's two
//    panes to px, then the pair moves; the outer row's weights are not touched.
window.__rompGutter('gv-chat-2', function () { return 'chat-pane'; }, 'chat-pane-2');
out.dragChatChat = drag('gv-chat-2', 500, 600);
// 5) gv-a's left neighbour is the chat AREA: with the outline (fleet) pane shown, a drag on gv-a moves chat|fleet in
//    the outer row (its shown panes to px first) and leaves the row's inner weights where the last drag left them
showFleet(true);
out.dragGvA = drag('gv-a', 500, 560);
showFleet(false);
// 6) closing the column: its grow leaves the store and .row; a later fair grow (a new column made after
//    the close) averages only what is still registered, though the closed pane's element is still there
window.__rompUnregisterPane('chat-pane-2');
out.unregister = { grows: grows(), store: store() };
window.__rompRegisterPane('chat-pane-3', 'chat3');
window.__rompGrowFair('chat3');
out.fairAfterClose = { grows: grows(), store: store() };
window.__rompUnregisterPane('chat-pane-3');
// 7) no split at all: gv-b with fleet hidden pairs the chat area with feed, through the
//    unregistered fallback keys (key('chat-area') → chat, key('feed-pane') → feed)
out.dragGvB = drag('gv-b', 500, 480);
// 8) a NEW chat column takes HALF the rightmost column of its row (the chat split, 2026-09-11): __rompSplitGrow normalises
//    the ROW's shown panes to their pixels first (the outer row is another container: not written), then the left pane's
//    key and the new key each take half the left pane's width, persisted; a hidden or missing left pane writes nothing
resetDom();
STORE['romp-pane-grow'] = JSON.stringify({ chat: 60, chat1: 60, fleet: 34, feed: 40, chat2: 25 });
BOOT();
window.__rompRegisterPane('chat-pane-2', 'chat2');
out.splitGrow = { wrote: window.__rompSplitGrow('chat-pane-2', 'chat3'), grows: grows(), store: store(),
                  hidden: window.__rompSplitGrow('fleet-pane', 'chat9'), missing: window.__rompSplitGrow('chat-pane-77', 'chat9'), after: grows() };
// 9) a CLOSING column hands its width to the column on its LEFT (review find 2026-09-11, the halving's twin): the row's
//    shown panes to their pixels first, then the left pane's key takes the closing pane's width plus the 7 px gutter that
//    goes with it (the row keeps its width: one gutter fewer); the outer row is not written; a hidden or missing pane on
//    either side writes nothing; the closing pane's own key is dropped by the unregister that follows
resetDom();
STORE['romp-pane-grow'] = JSON.stringify({ chat: 60, chat1: 60, fleet: 34, feed: 40, chat2: 25 });
BOOT();
window.__rompRegisterPane('chat-pane-2', 'chat2');
out.splitShrink = { wrote: window.__rompSplitShrink('chat-pane', 'chat-pane-2'), grows: grows(), store: store(),
                    hiddenLeft: window.__rompSplitShrink('fleet-pane', 'chat-pane-2'), missingGone: window.__rompSplitShrink('chat-pane', 'chat-pane-77'), after: grows() };
window.__rompUnregisterPane('chat-pane-2');
out.splitShrink.unregistered = { grows: grows(), store: store() };
// 10) the ROW gutter (the chat rows, 2026-09-15): wired by the split script through __rompRowGutter, its drag writes NO
//     grow (a row pair is a share, not px), moves the horizontal landing line across the chat area, clamps at the pair's
//     minimum (min(120, a quarter): 120 of 800), and the release reports the top row's share, once, to the hook
resetDom();
for (const k in STORE) delete STORE[k];   // a fresh browser: the boot defaults
BOOT();
window.__rompRowGutter('gv-rows', 'chat-row-1', 'chat-row-2', function (s) { APPLIED.push(s); });
out.rows = dragRows(400, 500);
out.rowsFar = dragRows(400, 1200);
out.rowsAfter = { grows: grows(), store: store() };
// 11) a store from BEFORE the rows (no chat1): the first pane's inner weight starts at the chat weight it wore, so a
//     stored later column keeps its proportion against it rather than facing a default 60
resetDom();
for (const k in STORE) delete STORE[k];
STORE['romp-pane-grow'] = JSON.stringify({ chat: 640, fleet: 34, feed: 400, chat2: 400 });
BOOT();
out.legacy = { grows: grows(), store: store() };
// 12) THE UPGRADE of a pre-rows store (review find 2026-09-15): the old chat weight was ONE column's share of the row and had
//     become the whole area's, so a persisted chat 640 / chat2 400 / feed 400 shrank the chat and grew the feed with no gesture.
//     __rompSeedAreaWeight, called by the split with the top-row columns it restored, converts the pre-rows layout to pixels:
//     the chat columns take theirs inside the area, the outer panes theirs beside it, and the area the chat columns' pixels plus
//     the gutters between them — so the rows shell renders the widths the pre-rows shell did. One, two, three and four columns
//     (the fourth with the outline shown), a stale key of a closed column left out of the sum, a store that already carries
//     chat1 untouched, and the hook a one-shot: the second call is a no-op
const LEGACY = [
  { name: 'one', store: { chat: 700, fleet: 34, feed: 300, files: 40 }, cols: [], fleet: false },
  { name: 'two', store: { chat: 640, fleet: 34, feed: 400, chat2: 400 }, cols: ['chat2'], fleet: false },
  { name: 'three', store: { chat: 500, chat2: 300, chat3: 200, fleet: 34, feed: 400 }, cols: ['chat2', 'chat3'], fleet: false },
  { name: 'four', store: { chat: 400, chat2: 300, chat3: 200, chat4: 100, fleet: 200, feed: 300, files: 40 }, cols: ['chat2', 'chat3', 'chat4'], fleet: true },
  { name: 'stale', store: { chat: 640, chat2: 400, chat3: 999, fleet: 34, feed: 400 }, cols: ['chat2'], fleet: false },
  // a restored column with NO stored weight (review find 2026-09-15): the pre-rows shell gave it the average of chat 640 and
  // feed 400, 520 — where make()'s rows' rule, run before the upgrade, gave it its row sibling's 640 and the upgrade froze that
  { name: 'missingOne', store: { chat: 640, fleet: 34, feed: 400 }, cols: ['chat2'], fleet: false },
  // two missing: the second averages the first's resolved 520 in — (640 + 520 + 400) / 3 = 520 again
  { name: 'missingTwo', store: { chat: 640, fleet: 34, feed: 400 }, cols: ['chat2', 'chat3'], fleet: false },
  // an EMPTY legacy object: the pre-rows defaults (chat 60, feed 40), both columns at their average 50
  { name: 'empty', store: {}, cols: ['chat2', 'chat3'], fleet: false },
];
out.upgrade = {};
LEGACY.forEach((c) => {
  resetDom(); for (const k in STORE) delete STORE[k];
  STORE['romp-pane-grow'] = JSON.stringify(c.store);
  showFleet(c.fleet);
  BOOT();
  c.cols.forEach((k) => { window.__rompRegisterPane('chat-pane-' + k.slice(4), k); window.__rompGrowFairIfNew(k); });   // what the split's make() does for a restored column
  const outerShown = (c.fleet ? ['fleet'] : []).concat(['feed']);
  const before = legacyWidths(c.store, c.cols, outerShown);
  const wrote = window.__rompSeedAreaWeight();
  const g = Object.assign({}, ROW); const gk = {}; Object.keys(g).forEach((k) => { gk[k.slice(4)] = g[k]; });
  out.upgrade[c.name] = { wrote, before, after: rowsWidths(gk, c.cols, outerShown), grows: g, store: store(), again: window.__rompSeedAreaWeight(), growsAgain: grows() };
  showFleet(false);
});
resetDom(); for (const k in STORE) delete STORE[k];
STORE['romp-pane-grow'] = JSON.stringify({ chat: 900, chat1: 640, chat2: 400, fleet: 34, feed: 400, files: 40 });
BOOT();
window.__rompRegisterPane('chat-pane-2', 'chat2'); window.__rompGrowFairIfNew('chat2');
out.upgrade.current = { wrote: window.__rompSeedAreaWeight(), grows: grows(), store: store() };
// 13) the CHAT PANE OFF at a legacy restore (review find 2026-09-15, third pass): the missing weight resolves over what is on
//     screen — the feed alone, 400 — and is persisted; the pixels WAIT (the store keeps its pre-rows shape, no chat1); a reload
//     while hidden is pending again with the resolved weight in hand; the rail's Chat (the toggle's __rompGrowFair('chat'),
//     ahead of its class flip) fair-grows the first pane over the feed alone — 400 — then finalises: equal columns, the widths
//     the old shell gave; a reload after keeps them
const CHAT_IDS = ['chat-area', 'chat-pane', 'chat-pane-2', 'chat-pane-3', 'chat-pane-4'];
const gk = (g) => { const o = {}; Object.keys(g).forEach((k) => { o[k.slice(4)] = g[k]; }); return o; };
const HSTORE = { chat: 640, fleet: 34, feed: 400 };
resetDom(); for (const k in STORE) delete STORE[k];
STORE['romp-pane-grow'] = JSON.stringify(HSTORE);
CHAT_IDS.forEach((id) => { EL[id]._display = 'none'; });
BOOT();
window.__rompRegisterPane('chat-pane-2', 'chat2'); window.__rompGrowFairIfNew('chat2');
const hb = { madeGrow: ROW['--g-chat2'], storeAfterMake: store() };
hb.deferred = window.__rompSeedAreaWeight(); hb.grows = grows(); hb.store = store();
resetDom(); CHAT_IDS.forEach((id) => { EL[id]._display = 'none'; });
BOOT(); window.__rompRegisterPane('chat-pane-2', 'chat2'); window.__rompGrowFairIfNew('chat2');
hb.reloadHidden = { deferred: window.__rompSeedAreaWeight(), grows: grows(), store: store() };
window.__rompGrowFair('chat');   // the rail's Chat: togglePane fair-grows the pane, then flips the class
CHAT_IDS.forEach((id) => { EL[id]._display = 'flex'; });
hb.shown = { grows: grows(), store: store(), before: legacyWidths(HSTORE, ['chat2'], ['feed'], true), after: rowsWidths(gk(grows()), ['chat2'], ['feed']) };
resetDom(); BOOT(); window.__rompRegisterPane('chat-pane-2', 'chat2'); window.__rompGrowFairIfNew('chat2');
hb.reloadShown = { wrote: window.__rompSeedAreaWeight(), after: rowsWidths(gk(grows()), ['chat2'], ['feed']), store: store() };
// …and with the chat pane ON at the restore the same store resolves the column over chat 640 and feed 400 (test 12's missingOne)
out.upgrade.hiddenChat = hb;
// 14) THE MATRIX against the pre-rows shell itself (the fourth review pass ran it: 1-4 columns x saved / missing / mixed weights
//     x the 8 outer visibility combinations x the chat pane shown or hidden at boot, 192 cases). OLD_SHELL is the pane-weight
//     helper of _LANDING_JS at ab112d49, verbatim (the registry, the fair grow, the store); both shells restore the same store
//     into the same stub, the hidden ones are shown through the toggle's fair-grow call, and the widths each lays out — the old
//     one row, the new the outer row then the top chat row — must agree to the pixel
const OLD_SHELL = function () {
var row=document.querySelector('.row');
var PANES=['chat-pane','fleet-pane','feed-pane','files-pane'];
var GK='romp-pane-grow',grow={chat:60,fleet:34,feed:40,files:40};
try{var g=JSON.parse(localStorage.getItem(GK)||'null');if(g)grow=Object.assign(grow,g);}catch(e){}
function setGrow(k,v){grow[k]=v;row.style.setProperty('--g-'+k,v);}
for(var k in grow)setGrow(k,grow[k]);
var KEYS={};
window.__rompRegisterPane=function(id,k){KEYS[id]=k;if(PANES.indexOf(id)<0)PANES.splice(PANES.indexOf('fleet-pane'),0,id);};
window.__rompUnregisterPane=function(id){var k=KEYS[id];delete KEYS[id];var i=PANES.indexOf(id);if(i>=0)PANES.splice(i,1);
if(k){delete grow[k];row.style.removeProperty('--g-'+k);try{localStorage.setItem(GK,JSON.stringify(grow));}catch(e){}}};
function key(id){return KEYS[id]||(id==='chat-pane'?'chat':id==='fleet-pane'?'fleet':id==='feed-pane'?'feed':'files');}
function shown(id){var p=document.getElementById(id);return p&&getComputedStyle(p).display!=='none';}
window.__rompGrowFair=function(k){if(k==='timeline')return;var v=PANES.filter(shown).map(function(id){return grow[key(id)];})
.filter(function(g){return typeof g==='number'&&isFinite(g);});
var avg=v.length?v.reduce(function(a,b){return a+b;},0)/v.length:50;setGrow(k,avg);
try{localStorage.setItem(GK,JSON.stringify(grow));}catch(e){}};
window.__rompGrowFairIfNew=function(k){if(typeof grow[k]==='number'&&isFinite(grow[k])){setGrow(k,grow[k]);return;}window.__rompGrowFair(k);};
};
const OUTER_ALL = ['fleet', 'feed', 'files'];
function setChat(on) { CHAT_IDS.forEach((id) => { if (EL[id]) EL[id]._display = on ? 'flex' : 'none'; }); }
function setOuter(combo) { OUTER_ALL.forEach((k, i) => { EL[k + '-pane']._display = combo[i] ? 'flex' : 'none'; }); }
function oldWidths(cols, outerShown) { const g = gk(grows()); const items = ['chat'].concat(cols).concat(outerShown); const px = flex(1007 - 7 * (items.length - 1), items.map((k) => g[k])); const o = {}; items.forEach((k, i) => { o[k === 'chat' ? 'chat1' : k] = px[i]; }); return o; }
// …the body's po-fleet follows the outline's display, as the rail keeps them: BOOT wires gv-b as the outline | feed gutter when it is on, else chat | feed
function fresh(store, chatShown, combo) { resetDom(); for (const k in STORE) delete STORE[k]; STORE['romp-pane-grow'] = JSON.stringify(store); setChat(chatShown); setOuter(combo); if (combo[0]) BODY.add('po-fleet'); else BODY.delete('po-fleet'); }
function restore(cols) { cols.forEach((k) => { window.__rompRegisterPane('chat-pane-' + k.slice(4), k); window.__rompGrowFairIfNew(k); }); }
// one case, both shells: `between` runs after the restore (the old shell's version of the mutation, then the new's) while the
// chat pane is still hidden; the show is the toggle's fair-grow call, then the class flip
function compare(store, cols, combo, chatShown, between) {
  const outerShown = OUTER_ALL.filter((k, i) => combo[i]);
  fresh(store, chatShown, combo); OLD_SHELL(); restore(cols); if (between) between.old();
  if (!chatShown) { window.__rompGrowFair('chat'); setChat(true); }
  const before = oldWidths((between && between.cols) || cols, (between && between.outer) || outerShown), oldStore = store_();
  fresh(store, chatShown, combo); BOOT(); restore(cols); window.__rompSeedAreaWeight(); if (between) between.now();
  if (!chatShown) { window.__rompGrowFair('chat'); setChat(true); }
  const after = rowsWidths(gk(grows()), (between && between.cols) || cols, (between && between.outer) || outerShown), newStore = store_();
  let maxDiff = 0; Object.keys(before).forEach((k) => { maxDiff = Math.max(maxDiff, Math.abs(before[k] - after[k])); });
  return { before, after, maxDiff, keys: Object.keys(before).length, oldStore, newStore };
}
function store_() { return JSON.parse(STORE['romp-pane-grow'] || 'null'); }
const matrix = { cases: 0, mismatches: [], maxDiff: 0 };
[1, 2, 3, 4].forEach((n) => ['saved', 'missing', 'mixed'].forEach((pattern) => [0, 1, 2, 3, 4, 5, 6, 7].forEach((bits) => [true, false].forEach((chatShown) => {
  const cols = []; for (let c = 2; c <= n; c++) cols.push('chat' + c);
  const store = { chat: 640, fleet: 200, feed: 400, files: 300 };
  cols.forEach((k, i) => { if (pattern === 'saved' || (pattern === 'mixed' && i % 2 === 0)) store[k] = 100 * (i + 2); });
  const combo = [!!(bits & 1), !!(bits & 2), !!(bits & 4)];
  const r = compare(store, cols, combo, chatShown, null);
  matrix.cases++; matrix.maxDiff = Math.max(matrix.maxDiff, r.maxDiff);
  if (r.maxDiff > 1e-6 || r.keys !== cols.length + 1 + combo.filter(Boolean).length) matrix.mismatches.push({ n, pattern, bits, chatShown, r });
}))));
out.matrix = matrix;
// 15) MUTATIONS during the wait (the fourth review pass: a boot snapshot lost them). The chat pane hidden at the restore, the
//     legacy store missing the column's weight, and before the rail's Chat: (a) the rail reveals the outline — the toggle's
//     fair grow, then its class flip; (b) the outer gutter between the outline and the feed is dragged 50 px; (c) the restored
//     column is closed (unregistered, its pane gone); (d) a peer dashboard publishes a CURRENT-shape store and its arrangement
//     makes a third column here: adopted, the store left as the peer wrote it
const L = { chat: 640, fleet: 34, feed: 400 };
out.mut = {};
out.mut.reveal = compare(L, ['chat2'], [false, true, false], false, {
  old() { window.__rompGrowFair('fleet'); EL['fleet-pane']._display = 'flex'; },
  now() { window.__rompGrowFair('fleet'); EL['fleet-pane']._display = 'flex'; },
  cols: ['chat2'], outer: ['fleet', 'feed'] });
const LG = { chat: 640, fleet: 200, feed: 400 };   // the outline shown: gv-b is the outline | feed gutter
out.mut.drag = (() => {
  // the old shell's gutter normalised every shown pane to px and moved the pair by the pointer: here the outline (300 px) and
  // the feed (400 px), +50 → 350 / 350; the pre-rows fair grow gave chat2 (200 + 400) / 2 = 300 at the restore, and the first
  // pane (350 + 350) / 2 = 350 at the show
  fresh(LG, false, [true, true, false]); BOOT(); restore(['chat2']); window.__rompSeedAreaWeight();
  const d = drag('gv-b', 300, 350);   // BOOT's gv-b: the outline | feed gutter (po-fleet on)
  window.__rompGrowFair('chat'); setChat(true);
  const after = rowsWidths(gk(grows()), ['chat2'], ['fleet', 'feed']);
  const items = ['chat', 'chat2', 'fleet', 'feed'], oldW = { chat: 350, chat2: 300, fleet: 350, feed: 350 };
  const px = flex(1007 - 21, items.map((k) => oldW[k])); const before = {}; items.forEach((k, i) => { before[k === 'chat' ? 'chat1' : k] = px[i]; });
  let maxDiff = 0; Object.keys(before).forEach((k) => { maxDiff = Math.max(maxDiff, Math.abs(before[k] - after[k])); });
  return { before, after, maxDiff, dragged: d.afterUp, storeAtDrag: d.store };
})();
out.mut.close = compare(L, ['chat2'], [false, true, false], false, {
  old() { window.__rompUnregisterPane('chat-pane-2'); delete EL['chat-pane-2']; },
  now() { window.__rompUnregisterPane('chat-pane-2'); delete EL['chat-pane-2']; },
  cols: [], outer: ['feed'] });
const PEER = { chat: 669, chat1: 331, chat2: 331, chat3: 331, fleet: 34, feed: 331, files: 40 };
out.mut.peer = (() => {
  fresh(L, false, [false, true, false]); BOOT(); restore(['chat2']); window.__rompSeedAreaWeight();
  const pending = { store: store_(), grows: grows() };
  STORE['romp-pane-grow'] = JSON.stringify(PEER);   // the peer's upgraded store lands…
  restore(['chat3']);                                // …and its arrangement makes a third column here (the split's reconcile)
  const adopted = { store: store_(), grows: grows(), seed: window.__rompSeedAreaWeight() };
  window.__rompGrowFair('chat'); setChat(true);       // the rail's Chat later: a current dashboard's show, no upgrade
  return { pending, adopted, shown: { store: store_(), grows: grows() } };
})();
// 16) a PEER'S UPGRADE ALREADY WRITTEN when this pending dashboard acts (the fifth review pass: adopting at the final write threw
//     the action away). The peer's current store is in localStorage and — but for (4) — its storage event has NOT been heard here.
//     The first local action must start from the ingested state and its result must reach the store:
//     (1) the outline | feed gutter dragged +50: the two panes end where the drag put them, every other weight is the peer's, one write carries both;
//     (2) the outline revealed from the rail: fair-grown by the CURRENT rule, the value a page booted current from the same store gets;
//     (3) the restored column closed: the store loses chat2, the CSS property goes, the write carries the deletion;
//     (4) the peer writes while this dashboard is idle: the storage listener ingests, no local action;
//     (5) the peer's write lands mid-drag: on release the dragged pair stands, the rest is the peer's.
const PEER2 = { chat: 669, chat1: 331, chat2: 331, chat3: 331, fleet: 34, feed: 331, files: 40 };
function pendingWithPeer(fleetShown) {   // this dashboard pending (chat hidden, the column restored at the pre-rows fair grow), the peer's store written, no event
  fresh(L, false, [fleetShown, true, false]); BOOT(); restore(['chat2']); window.__rompSeedAreaWeight();
  STORE['romp-pane-grow'] = JSON.stringify(PEER2);
}
out.peerFirst = {};
pendingWithPeer(true);
const d1 = drag('gv-b', 300, 350);   // BOOT's gv-b: the outline | feed gutter
out.peerFirst.drag = { grows: grows(), store: store_(), legacy: window.__rompGrowLegacy(), afterDown: d1.afterDown };
pendingWithPeer(false);
window.__rompGrowFair('fleet'); EL['fleet-pane']._display = 'flex';
out.peerFirst.reveal = { fleet: ROW['--g-fleet'], store: store_(), legacy: window.__rompGrowLegacy() };
resetDom(); for (const k in STORE) delete STORE[k]; STORE['romp-pane-grow'] = JSON.stringify(PEER2); setChat(false); setOuter([false, true, false]);
BOOT(); restore(['chat2']); window.__rompSeedAreaWeight();   // a page booted CURRENT from the same store, the same panes on screen
window.__rompGrowFair('fleet'); EL['fleet-pane']._display = 'flex';
out.peerFirst.revealCurrent = { fleet: ROW['--g-fleet'], store: store_(), legacy: window.__rompGrowLegacy() };
pendingWithPeer(false);
window.__rompUnregisterPane('chat-pane-2'); delete EL['chat-pane-2'];
out.peerFirst.close = { grows: grows(), store: store_(), legacy: window.__rompGrowLegacy() };
pendingWithPeer(false);
const before4 = { grows: grows(), legacy: window.__rompGrowLegacy() };
winFire('storage', { key: 'romp-pane-grow' });
out.peerFirst.idle = { before: before4, grows: grows(), store: store_(), legacy: window.__rompGrowLegacy(), seed: window.__rompSeedAreaWeight() };
fresh(L, false, [true, true, false]); BOOT(); restore(['chat2']); window.__rompSeedAreaWeight();   // pending, the peer not yet written
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 300 });   // BOOT's gv-b: the outline | feed gutter
const midDown = { grows: grows(), legacy: window.__rompGrowLegacy() };
STORE['romp-pane-grow'] = JSON.stringify(PEER2); winFire('storage', { key: 'romp-pane-grow' });   // …lands mid-drag
const midNoted = { grows: grows(), legacy: window.__rompGrowLegacy() };
winFire('mousemove', { clientX: 350 }); winFire('mouseup', {});
out.peerFirst.midDrag = { down: midDown, mid: midNoted, grows: grows(), store: store_() };
// 17) ONE TRANSACTION per drag (the sixth review pass). The outline | feed | files panes shown at 300 / 400 / 300 px, the chat pane
//     hidden and this dashboard pending. (1) the outline | feed divider dragged +50 while a peer's upgrade sets files to 900: nothing
//     moves under the hand, and at the release the divider lands within a pixel of the line while the store carries the peer's 900
//     and chat1 (the pair rebased against the ingested sibling); (2) the same with no peer: the plain write, 350 / 350, 4e3c6215's
//     result; (3) a drag abandoned — the window's blur, a pointercancel, the document going hidden — puts the press-time weights
//     back and writes nothing, and the peer's upgrade after it is taken in whole, the pair included, so a later fair grow's write
//     carries the PEER's pair; (4) a new press while one stands cancels the prior (its 350 never written), and a stale release is
//     a no-op; (5) the row gutter shares the path: abandoned by blur it reports nothing and hides its line, and its stale release
//     never commits a pane gutter's drag
const PEER3 = { chat: 669, chat1: 331, chat2: 331, fleet: 34, feed: 331, files: 900 };
const THREE = { chat: 640, fleet: 150, feed: 200, files: 150 };   // proportional to the 300 / 400 / 300 px on screen, so the press's px normalisation is visible on the vars
function pendingThree() { fresh(THREE, false, [true, true, true]); EL['files-pane'].offsetWidth = 300; BODY.add('po-files'); BOOT(); restore(['chat2']); window.__rompSeedAreaWeight(); }
function outerPx(g) { const px = flex(1000, [g['--g-fleet'], g['--g-feed'], g['--g-files']]); return { fleet: px[0], feed: px[1], files: px[2], divider: px[0] }; }   // the outer row's three shown panes over their 1000 px
function ghostLeft() { return EL['gv-ghost'].style.left; }
function listeners() { return { move: (WL['mousemove'] || []).length, up: (WL['mouseup'] || []).length }; }
out.tx = {};
pendingThree();
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 300 });
const t1down = grows();
winFire('mousemove', { clientX: 350 });
STORE['romp-pane-grow'] = JSON.stringify(PEER3); winFire('storage', { key: 'romp-pane-grow' });
out.tx.peerMid = { during: { grows: grows(), legacy: window.__rompGrowLegacy(), ghost: ghostLeft(), sameAsDown: JSON.stringify(grows()) === JSON.stringify(t1down) } };
winFire('mousemove', { clientX: 350 }); winFire('mouseup', {});
out.tx.peerMid.after = { grows: grows(), px: outerPx(grows()), store: store_(), legacy: window.__rompGrowLegacy(), listeners: listeners(), drag: BODY.has('drag') };
pendingThree();
const t2 = drag('gv-b', 300, 350);
out.tx.noPeer = { grows: grows(), px: outerPx(grows()), store: store_(), afterUp: t2.afterUp };
const abandon = (how) => {
  pendingThree(); const before = { grows: grows(), bytes: STORE['romp-pane-grow'] };
  EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 300 }); const pressed = grows();
  winFire('mousemove', { clientX: 350 });
  if (how === 'blur') winFire('blur', {}); else if (how === 'pointercancel') winFire('pointercancel', {}); else { document.visibilityState = 'hidden'; docFire('visibilitychange', {}); }
  const cancelled = { grows: grows(), bytes: STORE['romp-pane-grow'], listeners: listeners(), drag: BODY.has('drag') || BODY.has('dragv'), ghost: EL['gv-ghost'].style.display };
  STORE['romp-pane-grow'] = JSON.stringify(PEER3); winFire('storage', { key: 'romp-pane-grow' });
  const ingested = { grows: grows(), legacy: window.__rompGrowLegacy() };
  window.__rompGrowFair('chat');   // a later fair grow (the rail's Chat on a current dashboard): its write carries the pair the PEER holds
  return { before, pressed, cancelled, ingested, later: { store: store_() } };
};
out.tx.abandoned = { blur: abandon('blur'), pointercancel: abandon('pointercancel'), hidden: abandon('hidden') };
pendingThree();
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 300 }); winFire('mousemove', { clientX: 350 });
const staleUp = WL['mouseup'][0];
EL['gv-c'].fire('mousedown', { preventDefault() {}, clientX: 700 });   // a second press while the first stands: the first is cancelled
const secondPress = { grows: grows(), bytes: STORE['romp-pane-grow'], listeners: listeners() };
winFire('mousemove', { clientX: 720 }); winFire('mouseup', {});
const committed = { grows: grows(), store: store_() };
staleUp();
out.tx.replaced = { secondPress, committed, afterStale: { grows: grows(), store: store_() } };
pendingThree();
window.__rompRowGutter('gv-rows', 'chat-row-1', 'chat-row-2', function (s) { APPLIED.push(s); });
EL['gv-rows'].fire('mousedown', { preventDefault() {}, clientY: 400 }); winFire('mousemove', { clientY: 500 });
const rowMid = { ghost: ghostH().display, dragh: BODY.has('dragh') };
winFire('blur', {});
out.tx.row = { mid: rowMid, cancelled: { ghost: ghostH().display, dragh: BODY.has('dragh'), applied: APPLIED.slice(), listeners: listeners(), bytes: STORE['romp-pane-grow'], grows: grows() } };
EL['gv-rows'].fire('mousedown', { preventDefault() {}, clientY: 400 }); winFire('mousemove', { clientY: 500 });
const rowStaleUp = WL['mouseup'][0];
EL['gv-b'].fire('mousedown', { preventDefault() {}, clientX: 300 });   // a pane gutter's press cancels the row drag
winFire('mousemove', { clientX: 350 });
rowStaleUp();
out.tx.row.crossed = { applied: APPLIED.slice(), grows: grows(), bytes: STORE['romp-pane-grow'], listeners: listeners() };
winFire('mouseup', {});
out.tx.row.crossed.committed = { grows: grows(), store: store_(), applied: APPLIED.slice() };
// 19) THE STORE ON DISK IS THE ONE SOURCE (the eighth review pass). A pending page handles its storage events late: a peer wrote a
//     current store twice before the first event is heard. (1) the first event, carrying the OLDER value, adopts the store as it
//     stands — the newer — and the second event changes nothing; the next local action, the outline revealed, persists the newer
//     store with the outline fair-grown, never the older's numbers. (2) By construction no page running this code puts a pre-rows
//     shape over a current store: with a peer's current store on disk and no event heard, every write path of a pending page
//     leaves the store current-shaped. (3) So an event whose own value is current while the store on disk is pre-rows-shaped
//     (an old build's write, the one way that state can arise) adopts nothing: the disk is the truth.
const V1 = { chat: 669, chat1: 331, chat2: 331, fleet: 34, feed: 331, files: 40 };
const V2 = { chat: 800, chat1: 400, chat2: 400, fleet: 500, feed: 200, files: 100 };
function pendingLate() { fresh(L, false, [false, true, false]); BOOT(); restore(['chat2']); window.__rompSeedAreaWeight(); }
pendingLate();
STORE['romp-pane-grow'] = JSON.stringify(V2);   // both writes landed before this page heard the first
winFire('storage', { key: 'romp-pane-grow', newValue: JSON.stringify(V1) });
const afterFirst = { grows: grows(), legacy: window.__rompGrowLegacy() };
winFire('storage', { key: 'romp-pane-grow', newValue: JSON.stringify(V2) });
const afterSecond = { grows: grows() };
window.__rompGrowFair('fleet'); EL['fleet-pane']._display = 'flex';   // the next local action: the rail's Outline
out.source = { queued: { afterFirst, afterSecond, revealed: { fleet: ROW['--g-fleet'], store: store_() } } };
const paths = {};
pendingLate(); STORE['romp-pane-grow'] = JSON.stringify(V1); window.__rompGrowFair('fleet'); paths.fairGrow = store_();
pendingLate(); STORE['romp-pane-grow'] = JSON.stringify(V1); window.__rompUnregisterPane('chat-pane-2'); paths.unregister = store_();
pendingLate(); STORE['romp-pane-grow'] = JSON.stringify(V1); BODY.add('po-fleet'); EL['fleet-pane']._display = 'flex'; drag('gv-b', 300, 350); paths.dragRelease = store_();
pendingLate(); STORE['romp-pane-grow'] = JSON.stringify(V1); paths.seed = { wrote: window.__rompSeedAreaWeight(), store: store_(), legacy: window.__rompGrowLegacy() };
out.source.byConstruction = paths;
pendingLate();
const legacyOnDisk = STORE['romp-pane-grow'];
winFire('storage', { key: 'romp-pane-grow', newValue: JSON.stringify(V1) });   // a current value in the event, a pre-rows shape on disk
out.source.diskWins = { legacy: window.__rompGrowLegacy(), grows: grows(), bytes: STORE['romp-pane-grow'], sameBytes: STORE['romp-pane-grow'] === legacyOnDisk };
console.log(JSON.stringify(out));
"""


# BOTH scripts, run together (the seventh review pass drove them so): the pane-weight helper (_LANDING_JS) and the split
# (_LANDING_SPLIT_JS) against one DOM stub with the served markup — the shell's row, the chat area and its rows, the first
# pane, the outer panes and gutters, the landing lines — a document that hears listeners, a body whose po-* classes the
# stub's getComputedStyle reads as the stylesheet would, elements that know whether they are connected, a store that
# records every write, a console that records every warning. A peer's action is its two writes on the store and the two
# storage events that follow, in the order a real close makes them.
HARNESS2 = r"""
'use strict';
let STORE = {}, WRITES = [], BYID = {}, WL = {}, DL = {}, WARNS = [];
let BODY = new Set();
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = String(v); WRITES.push([k, String(v)]); }, removeItem: (k) => { delete STORE[k]; } };
console.warn = (...a) => { WARNS.push(a.join(' ')); };
function isChatish(el) { const c = el.className || ''; return el.id === 'chat-area' || el.id === 'chat-pane' || /\bchat-col\b/.test(c) || /\bgv-chat\b/.test(c); }
function disp(el) {
  if (el._display) return el._display;
  if (isChatish(el)) return BODY.has('po-chat') ? 'flex' : 'none';
  if (el.id === 'fleet-pane' || el.id === 'gv-a') return BODY.has('po-fleet') ? 'flex' : 'none';
  if (el.id === 'feed-pane') return BODY.has('po-feed') ? 'flex' : 'none';
  if (el.id === 'files-pane') return BODY.has('po-files') ? 'flex' : 'none';
  if (el.id === 'chat-row-2' || el.id === 'gv-rows') { const a = BYID['chat-area']; return a && /\brows\b/.test(a.className) ? 'flex' : 'none'; }
  return 'flex';
}
function mkEl(tag) {
  const el = {
    tagName: tag, className: '', title: '', textContent: '', src: '', parentElement: null, _id: '', _w: 400, _h: 800, _display: null, _root: false,
    style: { _props: {}, setProperty(k, v) { this._props[k] = v; }, removeProperty(k) { delete this._props[k]; } },
    _attrs: {}, _ls: {}, children: [],
    _rect: { left: 0, top: 0, width: 0, height: 0 }, getBoundingClientRect() { return Object.assign({}, this._rect); },
    get offsetWidth() { return this._w; }, get offsetHeight() { return this._h; },
    get isConnected() { let n = this; while (n) { if (n._root) return true; n = n.parentElement; } return false; },
    contains(n) { return n === this || this.children.some((c) => c.contains && c.contains(n)); },
    setAttribute(k, v) { this._attrs[k] = String(v); }, getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    appendChild(c) { c.parentElement = this; this.children.push(c); return c; },
    insertBefore(c, ref) { c.parentElement = this; const i = this.children.indexOf(ref); if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; },
    remove() { const p = this.parentElement; if (p) { const i = p.children.indexOf(this); if (i >= 0) p.children.splice(i, 1); } const drop = (n) => { if (n._id) delete BYID[n._id]; n.children.forEach(drop); }; drop(this); this.parentElement = null; },
    addEventListener(k, f) { (this._ls[k] = this._ls[k] || []).push(f); },
    fire(k, ev) { (this._ls[k] || []).slice().forEach((f) => f(ev || { preventDefault() {}, stopPropagation() {} })); },
  };
  Object.defineProperty(el, 'id', { get() { return this._id; }, set(v) { if (this._id) delete BYID[this._id]; this._id = String(v); if (v) BYID[v] = this; } });
  const cls = () => el.className.split(/\s+/).filter(Boolean);
  el.classList = { contains: (c) => cls().includes(c), add: (...cs) => { const l = cls(); cs.forEach((c) => { if (!l.includes(c)) l.push(c); }); el.className = l.join(' '); },
    remove: (...cs) => { el.className = cls().filter((c) => !cs.includes(c)).join(' '); }, toggle: (c, force) => { const want = force === undefined ? !cls().includes(c) : !!force; if (want) el.classList.add(c); else el.classList.remove(c); return want; } };
  if (tag === 'iframe') { el.contentWindow = { postMessage() {}, focus() {} }; el.contentDocument = { querySelector() { return null; } }; }
  return el;
}
let ROW = null, COL = null;
global.document = { body: { classList: { contains: (c) => BODY.has(c), add: (...cs) => cs.forEach((c) => BODY.add(c)), remove: (...cs) => cs.forEach((c) => BODY.delete(c)) } },
  querySelector(sel) { return sel === '.row' ? ROW : sel === '.col' ? COL : null; }, getElementById(id) { return BYID[id] || null; }, createElement(tag) { return mkEl(tag); },
  visibilityState: 'visible', addEventListener: (k, f) => { (DL[k] = DL[k] || []).push(f); } };
global.getComputedStyle = (el) => ({ display: disp(el) });
global.window = global; global.innerHeight = 900;
global.addEventListener = (t, f) => { (WL[t] = WL[t] || []).push(f); };
global.removeEventListener = (t, f) => { WL[t] = (WL[t] || []).filter((g) => g !== f); };
global.dispatchEvent = (ev) => { (WL[ev.type] || []).forEach((f) => f(ev)); return true; };
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = (o || {}).detail; } };
function el(tag, id, cls, parent, w) { const e = mkEl(tag); if (id) e.id = id; if (cls) e.className = cls; if (w) e._w = w; if (parent) parent.appendChild(e); return e; }
// a fresh dashboard: the stores as given, the body's pane classes, the served markup, then the two scripts in the shell's order
function boot(grow, cols, classes) {
  STORE = {}; WRITES = []; BYID = {}; WL = {}; DL = {}; BODY = new Set(classes);
  if (grow) STORE['romp-pane-grow'] = JSON.stringify(grow); if (cols) STORE['romp-chat-cols'] = JSON.stringify(cols);
  Object.keys(global).filter((k) => k.startsWith('__romp')).forEach((k) => { delete global[k]; });
  COL = el('div', null, 'col'); COL._root = true; COL._rect = { left: 0, top: 0, width: 1007, height: 900, bottom: 900 };
  ROW = el('div', null, 'row', COL, 1007); ROW._rect = { left: 0, top: 30, width: 1007, height: 800 };
  const area = el('div', 'chat-area', '', ROW, 600); area._rect = { left: 0, top: 30, width: 600, height: 800 };
  const r1 = el('div', 'chat-row-1', 'chat-row', area, 600); r1._rect = { left: 0, top: 30, width: 600, height: 800 };
  const cp = el('div', 'chat-pane', 'pane', r1, 400); el('iframe', 'f-chat', '', cp);
  el('div', 'gv-rows', 'gh', area); const r2 = el('div', 'chat-row-2', 'chat-row', area, 600); r2._rect = { left: 0, top: 830, width: 600, height: 0 };
  el('div', 'gv-a', 'gv', ROW); el('div', 'fleet-pane', 'pane', ROW, 300); el('div', 'gv-b', 'gv', ROW); el('div', 'feed-pane', 'pane', ROW, 400); el('div', 'gv-c', 'gv', ROW); el('div', 'files-pane', 'pane', ROW, 300);
  el('div', 'gv-ghost', '', COL); el('div', 'col-ghost', '', COL); el('div', 'gv-ghost-h', '', COL);
  (0, eval)(LANDING_JS); (0, eval)(SPLIT_JS);
}
const LANDING_JS = __LANDING_JS__;
const SPLIT_JS = __SPLIT_JS__;
const gk = () => { const o = {}; Object.keys(ROW.style._props).forEach((k) => { if (k.startsWith('--g-')) o[k.slice(4)] = ROW.style._props[k]; }); return o; };
const store = () => JSON.parse(STORE['romp-pane-grow'] || 'null');
const frames = () => window.__rompChatFrameIds();
const legacy = () => window.__rompGrowLegacy();
const listeners = () => ({ move: (WL.mousemove || []).length, up: (WL.mouseup || []).length });
const press = (gid, x) => BYID[gid].fire('mousedown', { preventDefault() {}, clientX: x });
const move = (x) => (WL.mousemove || []).slice().forEach((f) => f({ clientX: x }));
const up = () => (WL.mouseup || []).slice().forEach((f) => f({}));   // a copy of the list: a listener the release removes is still called once, as a stale one would be
const storageEvent = (key, value) => (WL.storage || []).slice().forEach((f) => f({ key, newValue: value }));
const growWrites = (from) => WRITES.slice(from).filter((w) => w[0] === 'romp-pane-grow').map((w) => JSON.parse(w[1]));
"""

DRIVER2 = r"""
const out = {};
const X = '11111111-2222-3333-4444-555555555501', Y = '11111111-2222-3333-4444-555555555502';
const HIDDEN = ['po-fleet', 'po-feed', 'po-files'];   // the chat pane off; the outline, the feed and the files pane on (300 / 400 / 300 px)
const LEGACY = { chat: 640, fleet: 300, feed: 400, files: 150 };
const PEER_CLOSED = { chat: 669, chat1: 331, fleet: 34, feed: 331, files: 900 };
const PEER_OPEN = { chat: 669, chat1: 331, chat2: 331, fleet: 34, feed: 331, files: 900 };
const CURRENT = { chat: 669, chat1: 331, chat2: 331, fleet: 34, feed: 331, files: 40 };
// (1) legacy A mid-drag; the peer closes the restored column — its column-store event reaches A first, its pane-store event after
boot(LEGACY, { v: 2, cols: [{ n: 2, ids: [X] }] }, HIDDEN);
out.s1 = { boot: { frames: frames(), legacy: legacy(), chat2: gk().chat2 } };
press('gv-b', 300); move(350);
out.s1.pressed = { grows: gk(), listeners: listeners(), drag: BODY.has('drag') };
const mark1 = WRITES.length;
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [] }); STORE['romp-pane-grow'] = JSON.stringify(PEER_CLOSED);
storageEvent('romp-chat-cols', STORE['romp-chat-cols']);
out.s1.afterCols = { frames: frames(), legacy: legacy(), grows: gk(), listeners: listeners(), store: store(), drag: BODY.has('drag') };
storageEvent('romp-pane-grow', STORE['romp-pane-grow']);
up();
out.s1.after = { grows: gk(), store: store(), legacy: legacy(), writes: growWrites(mark1), listeners: listeners(), warns: WARNS.length };
// (2) legacy A mid-drag; the peer's upgraded store is on disk but its event is delayed past the release
boot(LEGACY, { v: 2, cols: [{ n: 2, ids: [X] }] }, HIDDEN);
press('gv-b', 300); move(350);
const mark2 = WRITES.length;
STORE['romp-pane-grow'] = JSON.stringify(PEER_OPEN);
up();
out.s2 = { released: { grows: gk(), store: store(), legacy: legacy(), listeners: listeners() } };
storageEvent('romp-pane-grow', JSON.stringify(PEER_OPEN));
out.s2.afterEvent = { grows: gk(), store: store(), writes: growWrites(mark2) };
// (3) a CURRENT dashboard, its chat pane on, drags the gutter left of column 2; the peer closes column 2
boot(CURRENT, { v: 2, cols: [{ n: 2, ids: [X] }] }, ['po-chat', 'po-feed']);
out.s3 = { boot: { frames: frames(), legacy: legacy(), key2: window.__rompPaneKey('chat-pane-2') } };
press('gv-chat-2', 400); move(450);
out.s3.pressed = { grows: gk(), listeners: listeners() };
const mark3 = WRITES.length;
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [] });
storageEvent('romp-chat-cols', STORE['romp-chat-cols']);
out.s3.afterCols = { frames: frames(), grows: gk(), listeners: listeners(), key2: window.__rompPaneKey('chat-pane-2'), drag: BODY.has('drag') };
up();
out.s3.after = { grows: gk(), store: store(), writes: growWrites(mark3), listeners: listeners() };
// (4) the keys: the fixed panes, a registered column, and nothing else; a write on no key is a no-op
boot(CURRENT, { v: 2, cols: [{ n: 2, ids: [X] }] }, ['po-chat', 'po-feed']);
out.s4 = { none: window.__rompPaneKey('chat-pane-9'), files: window.__rompPaneKey('files-pane'), area: window.__rompPaneKey('chat-area'), first: window.__rompPaneKey('chat-pane'),
           fleet: window.__rompPaneKey('fleet-pane'), feed: window.__rompPaneKey('feed-pane'), col2: window.__rompPaneKey('chat-pane-2') };
const before4 = { grows: JSON.stringify(gk()), store: store() };
window.__rompGrowFair(null);
out.s4.nullFair = { sameGrows: JSON.stringify(gk()) === before4.grows, storeBefore: before4.store, storeAfter: store() };
// (5) a palette command mid-drag — Close this column, then Move this session to a new column — ends the drag first
boot(CURRENT, { v: 2, cols: [{ n: 2, ids: [X] }] }, ['po-chat', 'po-feed']);
press('gv-b', 600); move(650);   // the outline off: gv-b is the chat area | feed gutter
out.s5 = { pressed: { chat: gk().chat, feed: gk().feed } };
window.__rompCloseSplit(2);
out.s5.close = { grows: gk(), frames: frames(), listeners: listeners(), drag: BODY.has('drag') };
up();
out.s5.close.afterStale = { grows: gk(), store: store() };
press('gv-b', 600); move(650);
window.__rompMoveTab(Y, 'new');
out.s5.move = { grows: gk(), frames: frames(), listeners: listeners(), drag: BODY.has('drag') };
up();
out.s5.move.afterStale = { grows: gk(), store: store() };
// (6) the guard in persist: a pre-rows shape is never written over a peer's upgrade — reached by no path of the scripts (no
//     warning so far), so the write itself is called
boot(LEGACY, { v: 2, cols: [{ n: 2, ids: [X] }] }, HIDDEN);
const warnsBefore = WARNS.length;
STORE['romp-pane-grow'] = JSON.stringify(PEER_OPEN);
window.__rompPersistGrow();
out.s6 = { warnsBefore, warns: WARNS.length, bytes: STORE['romp-pane-grow'], peer: JSON.stringify(PEER_OPEN), legacy: legacy() };
console.log(JSON.stringify(out));
"""


class GestureUnderStructuralChange(unittest.TestCase):
    """The seventh review pass: a peer closing a column while a drag is live. Both scripts, the real ones, against the served
    markup's stub; the peer's action is its writes and the storage events they raise, in the close path's order."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        script = HARNESS2.replace("__LANDING_JS__", json.dumps(km._LANDING_JS)).replace("__SPLIT_JS__", json.dumps(km._LANDING_SPLIT_JS)) + DRIVER2
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the two scripts threw: " + r.stderr[:1500]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_18_a_peer_closing_a_column_under_a_drag_ends_the_drag_and_a_pre_rows_shape_is_never_written_over_its_store(self):
        peer_closed = {"chat": 669, "chat1": 331, "fleet": 34, "feed": 331, "files": 900}
        s = self.out["s1"]
        self.assertEqual(s["boot"], {"frames": ["f-chat", "f-chat-2"], "legacy": True, "chat2": 850 / 3}, "pending: the column restored at the pre-rows fair grow over the outline, the feed and the files pane")
        self.assertEqual((s["pressed"]["grows"]["fleet"], s["pressed"]["grows"]["feed"], s["pressed"]["grows"]["files"]), (300, 400, 300), "the press normalised the outer row to px")
        self.assertEqual(s["pressed"]["listeners"], {"move": 1, "up": 1}); self.assertTrue(s["pressed"]["drag"])
        a = s["afterCols"]
        self.assertEqual(a["frames"], ["f-chat"], "the peer's arrangement closed the column here…")
        self.assertEqual(a["listeners"], {"move": 0, "up": 0}, "…the drag ended first"); self.assertFalse(a["drag"])
        self.assertFalse(a["legacy"], "…and the peer's upgraded store was taken in at the drag's end")
        self.assertEqual(a["grows"], {"chat": 669, "chat1": 331, "fleet": 34, "feed": 331, "files": 900}, "the peer's weights on the vars, the closed column's gone")
        self.assertEqual(a["store"], peer_closed, "the unregister's write is the current shape: chat1 kept, chat2 gone, the peer's weights")
        f = s["after"]
        self.assertEqual(f["store"], peer_closed, "the pane-store event and the stale release changed nothing")
        self.assertTrue(all("chat1" in w for w in f["writes"]), "no pre-rows-shaped write at any point after the peer's upgrade: %r" % f["writes"])
        self.assertTrue(all("chat2" not in w for w in f["writes"]))
        self.assertEqual(f["listeners"], {"move": 0, "up": 0}); self.assertEqual(f["warns"], 0, "no refusal reached")
        peer_open = {"chat": 669, "chat1": 331, "chat2": 331, "fleet": 34, "feed": 331, "files": 900}
        r = self.out["s2"]["released"]
        self.assertFalse(r["legacy"], "the release ingested the store on disk though its event had not arrived")
        self.assertAlmostEqual(r["grows"]["fleet"], 1050, places=6, msg="…and rebased the pair against the peer's files 900: 900 · 700 / 300, split 350 : 350"); self.assertAlmostEqual(r["grows"]["feed"], 1050, places=6)
        self.assertEqual((r["grows"]["files"], r["grows"]["chat"], r["grows"]["chat1"], r["grows"]["chat2"]), (900, 669, 331, 331))
        self.assertEqual(r["store"], dict(peer_open, fleet=1050, feed=1050), "the store stays current")
        e = self.out["s2"]["afterEvent"]
        self.assertEqual(e["store"], r["store"], "the delayed event changes nothing"); self.assertTrue(all("chat1" in w for w in e["writes"]))
        c = self.out["s3"]
        self.assertEqual(c["boot"], {"frames": ["f-chat", "f-chat-2"], "legacy": False, "key2": "chat2"})
        self.assertEqual((c["pressed"]["grows"]["chat1"], c["pressed"]["grows"]["chat2"]), (400, 400), "the press normalised the top row to px")
        ac = c["afterCols"]
        self.assertEqual(ac["frames"], ["f-chat"]); self.assertIsNone(ac["key2"], "the closed column's id has no key any more"); self.assertEqual(ac["listeners"], {"move": 0, "up": 0}); self.assertFalse(ac["drag"])
        self.assertEqual(ac["grows"]["files"], 40, "the files pane is untouched by the close under the drag")
        af = c["after"]
        self.assertEqual(af["grows"]["files"], 40, "…and by the stale release"); self.assertEqual(af["store"]["files"], 40)
        self.assertNotIn("chat2", af["store"]); self.assertTrue(all("chat1" in w for w in af["writes"])); self.assertEqual(af["listeners"], {"move": 0, "up": 0})
        k = self.out["s4"]
        self.assertEqual({x: k[x] for x in ("none", "files", "area", "first", "fleet", "feed", "col2")}, {"none": None, "files": "files", "area": "chat", "first": "chat1", "fleet": "fleet", "feed": "feed", "col2": "chat2"},
                         "an explicit map: the fixed panes, a registered column, and nothing for any other id")
        self.assertTrue(k["nullFair"]["sameGrows"], "a fair grow on no key changes no weight")
        self.assertEqual(k["nullFair"]["storeAfter"], k["nullFair"]["storeBefore"], "…and the store holds the same weights (its write is the same content)")
        p = self.out["s5"]
        self.assertEqual((p["pressed"]["chat"], p["pressed"]["feed"]), (600, 400), "the press normalised the outer row")
        self.assertEqual((p["close"]["grows"]["chat"], p["close"]["grows"]["feed"]), (669, 331), "the palette's close ended the drag first: the press-time weights back")
        self.assertEqual(p["close"]["frames"], ["f-chat"]); self.assertEqual(p["close"]["listeners"], {"move": 0, "up": 0}); self.assertFalse(p["close"]["drag"])
        self.assertEqual((p["close"]["afterStale"]["grows"]["chat"], p["close"]["afterStale"]["grows"]["feed"]), (669, 331), "the stale release wrote nothing")
        self.assertEqual((p["move"]["grows"]["chat"], p["move"]["grows"]["feed"]), (669, 331), "the palette's move ended the drag first")
        self.assertEqual(p["move"]["frames"], ["f-chat", "f-chat-2"], "the new column takes the lowest free number, the closed column's"); self.assertEqual(p["move"]["listeners"], {"move": 0, "up": 0}); self.assertFalse(p["move"]["drag"])
        self.assertEqual((p["move"]["afterStale"]["grows"]["chat"], p["move"]["afterStale"]["grows"]["feed"]), (669, 331))
        g = self.out["s6"]
        self.assertEqual(g["warnsBefore"], 0, "no path of the scripts reached the refusal in the scenarios above")
        self.assertEqual(g["warns"], 1, "the write itself, pre-rows-shaped against a peer's upgrade on disk, refuses and says so")
        self.assertEqual(g["bytes"], g["peer"], "…and writes nothing"); self.assertTrue(g["legacy"])


class PaneGuttersExecute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the blob is a self-invoking function; wrapping it as BOOT lets the driver re-run it against a
        # re-seeded store, which is exactly what a reload of the shell does
        script = HARNESS + "const BOOT = function () {" + km._LANDING_JS + "};\n" + DRIVER
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the gutter JS threw: " + r.stderr[:800]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_1_boot_with_an_empty_store_sets_the_defaults_on_the_row(self):
        a = self.out["boot"]
        self.assertEqual(a["grows"], {"--g-chat": 60, "--g-chat1": 60, "--g-fleet": 34, "--g-feed": 40, "--g-files": 40}, "the chat area's weight and the first pane's inner one")
        self.assertIsNone(a["store"], "booting alone writes nothing: the store fills on the first drag or fair grow")

    def test_2_a_fresh_columns_fair_grow_averages_only_finite_grows(self):
        # the zero-width regression: chat-pane-2 is shown and registered but has no grow yet, so the old
        # average was (60 + undefined + 40) / 3 = NaN, which flex read as 0 and the column opened 0px wide
        a = self.out["fair"]
        self.assertTrue(a["finite"], "the new column's grow is a finite number, never NaN")
        self.assertEqual(a["grows"]["--g-chat2"], 60, "the average of its row's finite grows: the first pane's chat1 60 (the outer row's chat and feed are another scale)")
        self.assertEqual(a["grows"]["--g-chat"], 60)
        self.assertEqual(a["grows"]["--g-feed"], 40)
        self.assertEqual(a["store"], {"chat": 60, "chat1": 60, "fleet": 34, "feed": 40, "files": 40, "chat2": 60}, "and it persists")

    def test_3_grow_fair_if_new_keeps_a_stored_width_and_fairs_a_missing_one(self):
        kept = self.out["ifNewKept"]
        self.assertEqual(kept["grows"]["--g-chat2"], 123, "a dragged width survives the reload: setGrow applied, no re-fair")
        self.assertEqual(kept["store"]["chat2"], 123)
        fair = self.out["ifNewFair"]
        self.assertEqual(fair["grows"]["--g-chat2"], 60, "nothing stored for it → the fair average over its row")
        self.assertEqual(fair["store"], {"chat": 60, "chat1": 60, "fleet": 34, "feed": 40, "files": 40, "chat2": 60})

    def test_4_a_chat_chat_gutter_drag_moves_only_that_pair(self):
        a = self.out["dragChatChat"]
        # the grab normalises the ROW's shown panes to their px widths (the first pane's inner weight and the column's);
        # the outer row's chat, feed and hidden fleet keep their weights
        self.assertEqual(a["afterDown"], {"--g-chat": 60, "--g-chat1": 600, "--g-chat2": 500, "--g-feed": 40, "--g-fleet": 34, "--g-files": 40})
        self.assertTrue(a["dragging"], "body.drag + body.dragv while the pointer is held")
        self.assertEqual(a["listeners"], {"move": 1, "up": 1})
        # a drag moves a LANDING LINE (main, 2026-09): the pointer's +100px writes nothing until the release…
        self.assertEqual(a["afterMove"], a["afterDown"], "mousemove positions the ghost line; no pane re-lays out mid-drag")
        # …then chat1 +100, chat2 -100, the others exactly where the grab left them
        self.assertEqual(a["afterUp"]["--g-chat1"], 700)
        self.assertEqual(a["afterUp"]["--g-chat2"], 400)
        self.assertEqual(a["afterUp"]["--g-chat"], 60, "the chat area's share against the feed is untouched by a chat|chat drag")
        self.assertEqual(a["afterUp"]["--g-feed"], a["afterDown"]["--g-feed"], "feed is untouched by the drag")
        self.assertEqual(a["afterUp"]["--g-fleet"], a["afterDown"]["--g-fleet"], "fleet is untouched by the drag")
        self.assertFalse(a["dragAfterUp"], "body.drag / body.dragv are removed on mouseup")
        self.assertEqual(a["listenersAfterUp"], {"move": 0, "up": 0}, "the drag's window listeners are removed")
        self.assertEqual(a["store"], {"chat": 60, "chat1": 700, "fleet": 34, "feed": 40, "files": 40, "chat2": 400}, "the store holds the dragged widths")

    def test_5_gv_a_pairs_the_chat_area_with_fleet_and_leaves_the_row_s_inner_weights_alone(self):
        a = self.out["dragGvA"]
        # fleet is shown for this drag, so the OUTER row normalises: the chat area (600), fleet (300) and feed (400) to px;
        # the top row's chat1/chat2 stay where the last drag left them (another container)
        self.assertEqual(a["afterDown"], {"--g-chat": 600, "--g-chat1": 700, "--g-chat2": 400, "--g-fleet": 300, "--g-feed": 400, "--g-files": 40})
        # +60px: the pair that moves is chat|fleet — the whole chat area against the outline — and no column inside it moves
        self.assertEqual(a["afterUp"]["--g-chat"], 660)
        self.assertEqual(a["afterUp"]["--g-fleet"], 240)
        self.assertEqual(a["afterUp"]["--g-chat1"], 700, "the first pane's inner weight is not gv-a's business")
        self.assertEqual(a["afterUp"]["--g-chat2"], 400, "nor the column's")
        self.assertEqual(a["afterUp"]["--g-feed"], 400)
        self.assertEqual(a["store"], {"chat": 660, "chat1": 700, "fleet": 240, "feed": 400, "files": 40, "chat2": 400})

    def test_6_unregistering_a_closed_column_drops_it_everywhere(self):
        a = self.out["unregister"]
        self.assertNotIn("--g-chat2", a["grows"], "its --g-chat2 var leaves .row")
        self.assertNotIn("chat2", a["store"], "and its grow leaves the store")
        self.assertEqual(a["store"], {"chat": 660, "chat1": 700, "fleet": 240, "feed": 400, "files": 40})
        # a column made after the close averages its row: the first pane's 700 alone; had the closed pane still
        # counted (its element is still in the stub, still in the row), the average would be 550
        b = self.out["fairAfterClose"]
        self.assertEqual(b["grows"]["--g-chat3"], 700)
        self.assertNotIn("--g-chat2", b["grows"])
        self.assertEqual(b["store"], {"chat": 660, "chat1": 700, "fleet": 240, "feed": 400, "files": 40, "chat3": 700})

    def test_7_unregistered_ids_fall_back_to_the_fixed_keys(self):
        # no split, fleet hidden: gv-b's left neighbour is the chat area (lastChat() → 'chat-area'), and key() resolves
        # the fixed ids to chat / feed
        a = self.out["dragGvB"]
        self.assertEqual(a["afterDown"], {"--g-chat": 600, "--g-chat1": 700, "--g-fleet": 240, "--g-feed": 400, "--g-files": 40},
                         "the outer row's shown panes to px; the closed columns are gone from the grab, the first pane's inner weight is another container's")
        # -20px: chat -20, feed +20; fleet (hidden) untouched
        self.assertEqual(a["afterUp"]["--g-chat"], 580)
        self.assertEqual(a["afterUp"]["--g-feed"], 420)
        self.assertEqual(a["afterUp"]["--g-fleet"], 240)
        self.assertEqual(a["store"], {"chat": 580, "chat1": 700, "fleet": 240, "feed": 420, "files": 40})

    def test_8_a_new_column_takes_half_the_rightmost_column_after_every_shown_pane_is_normalised(self):
        # the chat split's honest half-width (2026-09-11): the top row's panes report chat-pane 600 and chat2 500, so the
        # grab-style normalisation writes those two first — never a mixed scale — and then chat2 and the new chat3 each take
        # 250; the outer row (chat 60, feed 40, the hidden outline and files panes) keeps its stored weights
        a = self.out["splitGrow"]
        self.assertTrue(a["wrote"])
        self.assertEqual(a["grows"], {"--g-chat": 60, "--g-chat1": 600, "--g-chat2": 250, "--g-chat3": 250, "--g-feed": 40, "--g-fleet": 34, "--g-files": 40})
        self.assertEqual(a["store"], {"chat": 60, "chat1": 600, "fleet": 34, "feed": 40, "files": 40, "chat2": 250, "chat3": 250}, "persisted, so __rompGrowFairIfNew keeps it when the column is made")
        self.assertFalse(a["hidden"], "a hidden left pane is never written")
        self.assertFalse(a["missing"], "nor a missing one")
        self.assertEqual(a["after"], a["grows"], "…and the refusals changed nothing")

    def test_9_a_closing_column_hands_its_width_to_the_column_on_its_left(self):
        # the halving's twin (review find 2026-09-11): with only the closing column's key deleted, flex gave its pixels to
        # EVERY pane by weight, so a tab dragged out and back narrowed the chat by a third per round trip. The top row's
        # panes report chat-pane 600 and chat2 500: the normalisation writes those two, then chat1 takes 600 + 500 + the
        # 7 px gutter that goes with the closing column; the outer row is not written
        a = self.out["splitShrink"]
        self.assertTrue(a["wrote"])
        self.assertEqual(a["grows"], {"--g-chat": 60, "--g-chat1": 1107, "--g-chat2": 500, "--g-feed": 40, "--g-fleet": 34, "--g-files": 40})
        self.assertEqual(a["store"], {"chat": 60, "chat1": 1107, "fleet": 34, "feed": 40, "files": 40, "chat2": 500}, "persisted: the width survives a reload")
        self.assertFalse(a["hiddenLeft"], "a hidden left pane is never written")
        self.assertFalse(a["missingGone"], "nor for a missing closing pane")
        self.assertEqual(a["after"], a["grows"], "…and the refusals changed nothing")
        u = a["unregistered"]
        self.assertEqual(u["grows"], {"--g-chat": 60, "--g-chat1": 1107, "--g-feed": 40, "--g-fleet": 34, "--g-files": 40}, "the unregister that follows drops the closing column's key alone")
        self.assertEqual(u["store"], {"chat": 60, "chat1": 1107, "fleet": 34, "feed": 40, "files": 40})

    def test_10_the_row_gutter_moves_a_horizontal_line_and_reports_the_top_row_s_share_writing_no_grow(self):
        # the chat rows (2026-09-15): the grab at y 400 (the divider: row 1 is 400 tall from 0) writes no grow — a row pair is a
        # share, not px — puts the drag classes on (dragh, not dragv) and shows #gv-ghost-h across the chat area at the divider;
        # the move repositions it; the release reports 500 / 800 to the hook, once, hides the line and drops the classes
        boot = self.out["boot"]["grows"]
        a = self.out["rows"]
        self.assertEqual(a["afterDown"]["grows"], boot, "no normalisation: nothing is written at the grab")
        self.assertTrue(a["afterDown"]["dragging"], "body.drag + body.dragh"); self.assertFalse(a["afterDown"]["dragv"])
        self.assertEqual(a["afterDown"]["ghost"], {"left": "0px", "width": "600px", "top": "400px", "display": "block"}, "the line spans the chat area, at the divider")
        self.assertEqual(a["afterMove"]["ghost"]["top"], "500px", "the line follows the pointer"); self.assertEqual(a["afterMove"]["grows"], boot); self.assertEqual(a["afterMove"]["applied"], [])
        self.assertEqual(a["afterUp"]["applied"], [0.625], "the top row's share of the pair: 500 of 800")
        self.assertEqual(a["afterUp"]["grows"], boot, "still no grow written: the share is the split script's to keep")
        self.assertIsNone(a["afterUp"]["store"]); self.assertEqual(a["afterUp"]["ghost"]["display"], "none"); self.assertFalse(a["afterUp"]["drag"])
        self.assertEqual(a["afterUp"]["listeners"], {"move": 0, "up": 0})
        f = self.out["rowsFar"]
        self.assertEqual(f["afterMove"]["ghost"]["top"], "680px", "clamped at the pair's minimum: min(120, 800 / 4) = 120 from the bottom")
        self.assertEqual(f["afterUp"]["applied"], [0.625, 0.85], "680 of 800")
        self.assertEqual(self.out["rowsAfter"], {"grows": boot, "store": None})

    def test_12_the_upgrade_of_a_pre_rows_store_renders_the_widths_the_pre_rows_shell_did(self):
        u = self.out["upgrade"]
        for name in ("one", "two", "three", "four", "stale", "missingOne", "missingTwo", "empty"):
            c = u[name]
            self.assertTrue(c["wrote"], name + ": a pre-rows store (no chat1) is upgraded")
            self.assertEqual(sorted(c["after"]), sorted(c["before"]), name + ": every pane the pre-rows row held is laid out")
            for k in c["before"]:
                self.assertLessEqual(abs(c["after"][k] - c["before"][k]), 1, "%s: %s renders at %.2f px, was %.2f" % (name, k, c["after"][k], c["before"][k]))
            self.assertIn("chat1", c["store"], name + ": the store carries chat1 from now on: the hook never runs again")
            self.assertFalse(c["again"], name + ": a second call is a no-op…"); self.assertEqual(c["growsAgain"], c["grows"], name + ": …and changes nothing")
        # the reviewer's case, in the hook's own numbers: 1007 - 14 shared 640 : 400 : 400, the area their two columns plus one gutter
        two = u["two"]
        self.assertAlmostEqual(two["grows"]["--g-chat1"], 993 * 640 / 1440, places=6); self.assertAlmostEqual(two["grows"]["--g-chat2"], 993 * 400 / 1440, places=6)
        self.assertAlmostEqual(two["grows"]["--g-feed"], 993 * 400 / 1440, places=6)
        self.assertAlmostEqual(two["grows"]["--g-chat"], 993 * 1040 / 1440 + 7, places=6, msg="the area: both columns' pixels plus the gutter between them")
        self.assertEqual(two["grows"]["--g-fleet"], 34, "a hidden pane is not in the pre-rows row: its weight is left alone")
        st = u["stale"]
        self.assertAlmostEqual(st["grows"]["--g-chat"], two["grows"]["--g-chat"], places=6, msg="a stale key (chat3, no column restored) is not summed into the area")
        self.assertEqual(st["grows"]["--g-chat3"], 999, "…and left where it was")
        one = u["one"]
        self.assertAlmostEqual(one["grows"]["--g-chat"], 1000 * 700 / 1000, places=6); self.assertAlmostEqual(one["grows"]["--g-chat1"], 700, places=6, msg="one column: the area is the pane")
        four = u["four"]
        self.assertAlmostEqual(four["grows"]["--g-fleet"], (1007 - 7 * 5) * 200 / 1500, places=6, msg="the outline shown: in the pre-rows row and normalised with it")
        # the missing-weight rule in the hook's own numbers (the reviewer's case at a 1007 px row): chat2 resolves to 520, so
        # 993 shared 640 : 520 : 400 — not the 640 : 640 : 400 make()'s sibling average had written before the upgrade
        m1 = u["missingOne"]
        self.assertAlmostEqual(m1["grows"]["--g-chat1"], 993 * 640 / 1560, places=6); self.assertAlmostEqual(m1["grows"]["--g-chat2"], 993 * 520 / 1560, places=6)
        self.assertAlmostEqual(m1["grows"]["--g-feed"], 993 * 400 / 1560, places=6)
        self.assertAlmostEqual(m1["grows"]["--g-chat"], 993 * 1160 / 1560 + 7, places=6)
        m2 = u["missingTwo"]
        self.assertAlmostEqual(m2["grows"]["--g-chat3"], (1007 - 21) * 520 / 2080, places=6, msg="the second missing weight averages the first's resolved 520 in: 520 again")
        self.assertAlmostEqual(m2["grows"]["--g-chat2"], m2["grows"]["--g-chat3"], places=6)
        em = u["empty"]
        self.assertAlmostEqual(em["grows"]["--g-chat1"], 986 * 60 / 200, places=6, msg="an empty legacy object: the pre-rows defaults, chat 60 and feed 40")
        self.assertAlmostEqual(em["grows"]["--g-chat2"], 986 * 50 / 200, places=6); self.assertAlmostEqual(em["grows"]["--g-chat3"], 986 * 50 / 200, places=6)
        self.assertAlmostEqual(em["grows"]["--g-feed"], 986 * 40 / 200, places=6)
        cur = u["current"]
        self.assertFalse(cur["wrote"], "a store that already carries chat1 is not a pre-rows store: untouched")
        self.assertEqual(cur["grows"]["--g-chat"], 900); self.assertEqual(cur["grows"]["--g-chat1"], 640)
        self.assertEqual(cur["store"], {"chat": 900, "chat1": 640, "chat2": 400, "fleet": 34, "feed": 400, "files": 40}, "and nothing is written")

    def test_13_a_legacy_restore_with_the_chat_pane_off_waits_for_the_pane_to_show_and_then_lays_out_as_the_old_shell_did(self):
        h = self.out["upgrade"]["hiddenChat"]
        self.assertEqual(h["madeGrow"], 400, "make()'s fair grow while the store is pre-rows-shaped IS the pre-rows rule: the feed alone is on screen, 400")
        self.assertNotIn("chat1", h["storeAfterMake"], "make()'s persist keeps the pre-rows shape while the upgrade is pending")
        self.assertFalse(h["deferred"], "the chat pane is off: the pixels wait")
        self.assertEqual(h["grows"]["--g-chat2"], 400, "the missing weight resolves over what is on screen — the feed alone — as the old shell's fair grow did")
        self.assertEqual(h["grows"]["--g-chat1"], 640, "the first pane's weight is not touched yet"); self.assertEqual(h["grows"]["--g-chat"], 640)
        self.assertEqual(h["store"], {"chat": 640, "fleet": 34, "feed": 400, "files": 40, "chat2": 400}, "persisted in the pre-rows shape: the resolved weight, no chat1")
        rh = h["reloadHidden"]
        self.assertFalse(rh["deferred"], "a reload while hidden: pending again"); self.assertEqual(rh["store"], h["store"], "…and nothing changes: the resolved weight is found in the store")
        self.assertEqual(rh["grows"]["--g-chat2"], 400)
        sh = h["shown"]
        self.assertEqual(sh["before"], {"chat1": 331, "chat2": 331, "feed": 331}, "the old shell: chat fair-grown to the feed's 400 at the show, three equal columns of (1007 - 14) / 3")
        for k in sh["before"]:
            self.assertLessEqual(abs(sh["after"][k] - sh["before"][k]), 1, "%s renders at %.2f px, the old shell gave %.2f" % (k, sh["after"][k], sh["before"][k]))
        self.assertEqual(sh["grows"]["--g-chat"], 669, "the area: the two columns' 331 each plus the gutter between them")
        self.assertIn("chat1", sh["store"], "finalised: the store carries chat1 from now on")
        self.assertEqual(sh["store"]["chat1"], 331); self.assertEqual(sh["store"]["chat2"], 331); self.assertEqual(sh["store"]["feed"], 331)
        rs = h["reloadShown"]
        self.assertFalse(rs["wrote"], "a reload after: not a pre-rows store any more")
        self.assertEqual(rs["after"], sh["after"], "…and the widths are kept"); self.assertEqual(rs["store"], sh["store"])

    def test_14_the_matrix_against_the_pre_rows_shell_lays_every_case_out_to_the_pixel(self):
        m = self.out["matrix"]
        self.assertEqual(m["cases"], 192, "1-4 columns x saved / missing / mixed weights x 8 outer combinations x the chat pane shown or hidden")
        self.assertEqual(m["mismatches"], [], "every case within 1e-6 px of the pre-rows shell; the first mismatches: %r" % m["mismatches"][:3])
        self.assertLessEqual(m["maxDiff"], 1e-6)

    def test_14b_the_embedded_pre_rows_helper_is_ab112d49_s_verbatim(self):
        # the oracle is the old _LANDING_JS's pane-weight helper, copied into this file; when the history is at hand (a full
        # clone), every executable line of the copy must be a line of that revision — a shallow checkout skips
        r = subprocess.run(["git", "show", "ab112d49:kernel/kernel.py"], cwd=os.path.dirname(HERE), capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("ab112d49 is not in this checkout's history (a shallow clone): the copy cannot be checked against it")
        old = r.stdout
        body = DRIVER[DRIVER.index("const OLD_SHELL = function () {") + len("const OLD_SHELL = function () {"):DRIVER.index("\n};\nconst OUTER_ALL")]
        lines = [ln for ln in body.strip().splitlines() if ln and not ln.startswith("var row=")]
        self.assertGreaterEqual(len(lines), 12)
        for ln in lines:
            self.assertIn(ln, old, "not a line of ab112d49's _LANDING_JS: %r" % ln)

    def test_15_a_mutation_during_the_wait_reaches_the_upgrade(self):
        a = self.out["mut"]["reveal"]
        self.assertEqual(a["before"], {"chat1": 246.5, "chat2": 246.5, "fleet": 246.5, "feed": 246.5}, "the old shell: the outline fair-grown to the feed's 400 at its reveal, then the first pane to (400 + 400) / 2 at the show — four equal panes of (1007 - 21) / 4")
        self.assertLessEqual(a["maxDiff"], 1e-6, "the rows shell lays the same four out: %r" % a["after"])
        self.assertEqual(a["newStore"]["fleet"], 246.5, "…and persisted the outline's live weight, not a boot snapshot's 34")
        b = self.out["mut"]["drag"]
        self.assertLessEqual(b["maxDiff"], 1e-6, "an outer gutter drag during the wait reaches the upgrade: %r vs %r" % (b["after"], b["before"]))
        self.assertEqual(b["dragged"]["--g-fleet"], 350); self.assertEqual(b["dragged"]["--g-feed"], 350)
        self.assertNotIn("chat1", b["storeAtDrag"], "the drag's persist kept the pre-rows shape")
        c = self.out["mut"]["close"]
        self.assertEqual(c["before"], {"chat1": 500, "feed": 500}, "the old shell: the column closed, the first pane fair-grown to the feed's 400 at the show — two halves")
        self.assertLessEqual(c["maxDiff"], 1e-6, "the rows shell too: %r" % c["after"])
        self.assertNotIn("chat2", c["newStore"], "the closed column's key does not come back into the store")
        self.assertIn("chat1", c["newStore"])
        d = self.out["mut"]["peer"]
        self.assertNotIn("chat1", d["pending"]["store"], "pending: the pre-rows shape")
        self.assertEqual(d["adopted"]["store"], {"chat": 669, "chat1": 331, "chat2": 331, "chat3": 331, "fleet": 34, "feed": 331, "files": 40}, "the peer's store is left exactly as it wrote it: no legacy-shape write over it")
        self.assertEqual(d["adopted"]["grows"]["--g-chat3"], 331, "the new column at the published weight, not a provisional 50")
        self.assertEqual(d["adopted"]["grows"]["--g-chat"], 669); self.assertEqual(d["adopted"]["grows"]["--g-chat1"], 331); self.assertEqual(d["adopted"]["grows"]["--g-feed"], 331)
        self.assertFalse(d["adopted"]["seed"], "adopted: this dashboard is current, nothing left to upgrade")
        self.assertEqual(d["shown"]["store"]["chat1"], 331, "the show later is a current dashboard's: the store keeps chat1")
        self.assertEqual(d["shown"]["grows"]["--g-chat1"], 331, "…and the first pane's inner weight is not re-fair-grown")

    def test_16_the_first_local_action_after_a_peer_s_upgrade_starts_from_it_and_reaches_the_store(self):
        peer = {"chat": 669, "chat1": 331, "chat2": 331, "chat3": 331, "fleet": 34, "feed": 331, "files": 40}
        d = self.out["peerFirst"]["drag"]
        self.assertFalse(d["legacy"], "the press ingested the peer's store")
        self.assertEqual(d["afterDown"]["--g-chat1"], 331, "…before the pair was normalised: the other weights are the peer's from the press on")
        self.assertEqual((d["grows"]["--g-fleet"], d["grows"]["--g-feed"]), (350, 350), "the two panes end where the drag put them")
        self.assertEqual(d["store"], dict(peer, fleet=350, feed=350), "one write carries the drag AND the peer's weights")
        r, c = self.out["peerFirst"]["reveal"], self.out["peerFirst"]["revealCurrent"]
        self.assertFalse(r["legacy"])
        self.assertEqual(r["fleet"], c["fleet"], "the outline revealed after the ingest fair-grows exactly as on a page booted current from the same store")
        self.assertEqual(r["store"], c["store"]); self.assertNotEqual(r["fleet"], 34, "…not the peer's stale weight for a pane the peer never showed")
        cl = self.out["peerFirst"]["close"]
        self.assertFalse(cl["legacy"]); self.assertNotIn("--g-chat2", cl["grows"], "the CSS property is gone")
        self.assertEqual(cl["store"], {k: v for k, v in peer.items() if k != "chat2"}, "the write carries the deletion over the peer's store: no chat2, chat1 kept")
        i = self.out["peerFirst"]["idle"]
        self.assertTrue(i["before"]["legacy"]); self.assertFalse(i["legacy"], "the storage event alone ingests")
        self.assertEqual(i["grows"]["--g-chat1"], 331); self.assertEqual(i["grows"]["--g-chat"], 669); self.assertEqual(i["grows"]["--g-chat3"], 331)
        self.assertEqual(i["store"], peer, "…and writes nothing"); self.assertFalse(i["seed"])
        m = self.out["peerFirst"]["midDrag"]
        self.assertTrue(m["down"]["legacy"]); self.assertEqual((m["down"]["grows"]["--g-fleet"], m["down"]["grows"]["--g-feed"]), (300, 300 + 100), "the press normalised the pair to px")
        self.assertTrue(m["mid"]["legacy"], "the peer's write mid-drag is only NOTED: nothing moves under the hand (the sixth pass)…")
        self.assertEqual(m["mid"]["grows"], m["down"]["grows"], "…no weight changes during the drag")
        self.assertEqual((m["grows"]["--g-fleet"], m["grows"]["--g-feed"]), (350, 350), "on release the dragged pair stands (no other pane in the container: the plain write)")
        self.assertEqual(m["store"], dict(peer, fleet=350, feed=350), "…and the write carries the merged view: the peer's store ingested at the release, the pair replaced")

    def test_17_a_drag_is_one_transaction_a_peer_s_write_waits_for_its_end_and_an_abandoned_drag_writes_nothing(self):
        peer3 = {"chat": 669, "chat1": 331, "chat2": 331, "fleet": 34, "feed": 331, "files": 900}
        p = self.out["tx"]["peerMid"]
        self.assertTrue(p["during"]["legacy"], "the peer's write mid-drag is noted, not applied"); self.assertTrue(p["during"]["sameAsDown"], "nothing moves under the hand")
        self.assertEqual(p["during"]["ghost"], "350px", "the line stands where the pointer put it")
        a = p["after"]
        self.assertFalse(a["legacy"], "the release ingested the peer's store…")
        self.assertLessEqual(abs(a["px"]["divider"] - 350), 1, "…and the divider lands where the line was: the pair rebased against the ingested files: %r" % a["px"])
        self.assertLessEqual(abs(a["px"]["feed"] - 350), 1); self.assertLessEqual(abs(a["px"]["files"] - 300), 1, "the files pane keeps its pixels: only the divider moved")
        self.assertEqual(a["store"]["files"], 900, "the store carries the peer's files weight"); self.assertEqual(a["store"]["chat1"], 331); self.assertEqual(a["store"]["chat"], 669)
        self.assertAlmostEqual(a["store"]["fleet"], 1050, places=6, msg="the pair: 900 · 700 / 300 = 2100, split 350 : 350"); self.assertAlmostEqual(a["store"]["feed"], 1050, places=6)
        self.assertEqual(a["listeners"], {"move": 0, "up": 0}); self.assertFalse(a["drag"])
        n = self.out["tx"]["noPeer"]
        self.assertEqual((n["grows"]["--g-fleet"], n["grows"]["--g-feed"], n["grows"]["--g-files"]), (350, 350, 300), "no peer: the plain write of the pair's pixels, 4e3c6215's result (the rebase is the identity)")
        self.assertEqual((n["store"]["fleet"], n["store"]["feed"]), (350, 350)); self.assertNotIn("chat1", n["store"], "still pending")
        for how, ab in self.out["tx"]["abandoned"].items():
            self.assertEqual((ab["pressed"]["--g-fleet"], ab["pressed"]["--g-feed"], ab["pressed"]["--g-files"]), (300, 400, 300), how + ": the press normalised the container to px")
            self.assertEqual(ab["cancelled"]["grows"], ab["before"]["grows"], how + ": the cancel put the press-time weights back (150 / 200 / 150)")
            self.assertEqual(ab["cancelled"]["bytes"], ab["before"]["bytes"], how + ": …and wrote nothing")
            self.assertEqual(ab["cancelled"]["listeners"], {"move": 0, "up": 0}, how + ": the gesture's listeners are gone"); self.assertFalse(ab["cancelled"]["drag"]); self.assertEqual(ab["cancelled"]["ghost"], "none")
            self.assertFalse(ab["ingested"]["legacy"], how + ": the peer's upgrade after it is taken in…")
            self.assertEqual((ab["ingested"]["grows"]["--g-fleet"], ab["ingested"]["grows"]["--g-feed"]), (34, 331), how + ": …the pair included")
            self.assertEqual((ab["later"]["store"]["fleet"], ab["later"]["store"]["feed"]), (34, 331), how + ": a later fair grow's write carries the PEER's pair, not the abandoned drag's")
        r = self.out["tx"]["replaced"]
        self.assertEqual(r["secondPress"]["grows"]["--g-fleet"], 300, "the second press cancelled the first (its 150 restored, then normalised again to 300): the 350 never landed")
        self.assertNotIn("chat1", r["secondPress"]["bytes"]); self.assertEqual(r["secondPress"]["listeners"], {"move": 1, "up": 1}, "one gesture's listeners, the second's")
        self.assertEqual((r["committed"]["store"]["fleet"], r["committed"]["store"]["feed"], r["committed"]["store"]["files"]), (300, 420, 280), "the second drag committed; the first never wrote")
        self.assertEqual(r["afterStale"], {"grows": r["committed"]["grows"], "store": r["committed"]["store"]}, "the first gesture's stale release is a no-op")
        rw = self.out["tx"]["row"]
        self.assertEqual(rw["mid"], {"ghost": "block", "dragh": True})
        self.assertEqual(rw["cancelled"]["ghost"], "none"); self.assertFalse(rw["cancelled"]["dragh"]); self.assertEqual(rw["cancelled"]["applied"], [], "abandoned by blur: no share reported")
        self.assertEqual(rw["cancelled"]["listeners"], {"move": 0, "up": 0}); self.assertNotIn("chat1", rw["cancelled"]["bytes"])
        c = rw["crossed"]
        self.assertEqual(c["applied"], [], "the row gutter's stale release reports nothing…"); self.assertEqual((c["grows"]["--g-fleet"], c["grows"]["--g-feed"]), (300, 400), "…and commits no pane gutter's drag")
        self.assertEqual(c["listeners"], {"move": 1, "up": 1})
        self.assertEqual((c["committed"]["store"]["fleet"], c["committed"]["store"]["feed"]), (350, 350), "the pane gutter's own release commits"); self.assertEqual(c["committed"]["applied"], [])

    def test_19_the_store_on_disk_is_the_one_source_a_late_page_adopts_the_newest_write_never_an_older_event_s_value(self):
        v2 = {"chat": 800, "chat1": 400, "chat2": 400, "fleet": 500, "feed": 200, "files": 100}
        q = self.out["source"]["queued"]
        self.assertFalse(q["afterFirst"]["legacy"], "the first event adopted…")
        self.assertEqual({k[4:]: v for k, v in q["afterFirst"]["grows"].items() if k[4:] in v2}, v2, "…the store as it stands, the NEWER write, not the older value the event carried")
        self.assertEqual(q["afterSecond"]["grows"], q["afterFirst"]["grows"], "the second event changes nothing")
        self.assertEqual(q["revealed"]["fleet"], 200, "the outline revealed after: the current rule over the feed's 200 (the newer store's), never 331")
        self.assertEqual(q["revealed"]["store"], dict(v2, fleet=200), "…and the write is the newer store with the outline fair-grown: the older numbers appear nowhere")
        b = self.out["source"]["byConstruction"]
        for path in ("fairGrow", "unregister", "dragRelease"):
            self.assertIn("chat1", b[path], path + ": a pending page's write path, with a peer's current store on disk and no event heard, leaves the store current-shaped")
        self.assertNotIn("chat2", b["unregister"]); self.assertEqual((b["dragRelease"]["fleet"], b["dragRelease"]["feed"]), (350, 350))
        self.assertEqual(b["seed"], {"wrote": False, "store": {"chat": 669, "chat1": 331, "chat2": 331, "fleet": 34, "feed": 331, "files": 40}, "legacy": False}, "the upgrade path adopts and writes nothing")
        d = self.out["source"]["diskWins"]
        self.assertTrue(d["legacy"], "an event carrying a current value over a pre-rows store on disk adopts nothing: the disk is the truth")
        self.assertTrue(d["sameBytes"]); self.assertEqual(d["grows"]["--g-chat1"], 640)

    def test_11_a_store_from_before_the_rows_seeds_the_first_pane_s_inner_weight_from_the_chat_weight(self):
        a = self.out["legacy"]
        self.assertEqual(a["grows"], {"--g-chat": 640, "--g-chat1": 640, "--g-fleet": 34, "--g-feed": 400, "--g-files": 40, "--g-chat2": 400},
                         "chat1 starts at the stored chat, so the stored column 2 keeps its 640:400 proportion against the first pane")
        self.assertEqual(a["store"], {"chat": 640, "fleet": 34, "feed": 400, "chat2": 400}, "seeded in memory: the store is written by the upgrade hook once the split has restored its columns (test 12), not here")


if __name__ == "__main__":
    unittest.main()
