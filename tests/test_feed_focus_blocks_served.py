#!/usr/bin/env python3
"""T410 (the user 2026-09-13, who wanted the focused session's blocks movable, resizable and collapsible on their own, and,
in the amendment, a label reading "Current session: <name>" that folds the whole section): THE SECTION'S OWN LAYOUT.

The focused-session section (T347, tests/test_feed_focus_served.py) mirrored the board. Now it keeps its own block
order (`focusOrder`, empty = follow the board until the first drag in the section), its own block widths (`focusW`,
flex weights traded at a gutter, floor 0.35 of a share), its own per-block collapse (`focusCols`, held for whichever
session is focused) and a fold of the whole section to its label (`focusFolded`); all four are view state, prune-exempt,
and a blob saved before them reads as the defaults. The label "Current session: <name>" is the section's collapse
control; the name inside it keeps its own click (open the session) and hover title; the caret at the end of the line
reads the state; folded, the focused session's card total follows the name.

The served lab drives the real /feed page from a hermetic kernel with a SYNTHETIC payload (the notes-api demo world:
`web` with two Working, one Blocked and two Completed cards; `api` with one Working card; `tests` with none), through
the page's own frame path, with page.mouse for every drag, and walks these roads in one browser run:
  (f) a store from BEFORE the change (the T347 fields only: focused true, order [], cols []) seeded before the page's
      script hydrates: the section renders in the board's order with equal widths, nothing folded, the label unfolded;
  (h) the label's computed font-size equals a board column head's (.feed-col-head) and its weight the section heads'
      (600), in both themes; the name wears the session's identity colour at the session headers' size; the chip is the
      drag handle (grab cursor, focusable, the arrow keys promised), as on the board: no grip (the user 2026-09-14);
  (e) the LABEL reads "Current session: web"; a click on its text folds the whole section (the blocks hidden, the quiet
      line hidden, the divider kept, the caret ▸, the total card count shown), the blob carries focusFolded true, a
      reload keeps it folded, a click on the caret unfolds it, Enter and Space on the focused label fold and unfold,
      and the name's click still posts the open-session message without folding;
  (d) the Completed block's caret folds the block (col-collapsed, its list hidden, its chip and count shown); an
      activeChat frame for `api` keeps Completed folded (focusCols is per column key, not per session); a reload keeps
      it; the caret again unfolds it;
  (c) a gutter drag widens Working against Blocked: the blob's focusW carries both weights summing to the previous sum
      (within 0.001) and the pixels move with the weights; later, a drag far past the neighbour stops at the 0.35 floor;
  (a) a chip drag of Blocked to the first slot reorders the section's blocks (visual order by getBoundingClientRect
      left) while the board's columns keep their order; the blob carries focusOrder;
  (b) a board chip drag (Completed to the first slot) moves the board and, the section now having its own order,
      leaves the section as it was; in a FRESH state (focusOrder empty) the same board drag moves the section too;
  (g) the freeze low: with the pointer on the section's copy of a card, a payload that moves another card queues; a
      synthetic mouseleave on the BOARD twin of the hovered card does not release the hold (the moved card is still in
      its old column); the pointer leaving the copy applies it;
  (i) a focused session with NO cards (tests): the label stands, no quiet line, the three blocks with their heads and
      empty lists (nothing said under a head, the user 2026-09-14);
  (j) the single-column layout (a 520 px viewport stacks the columns): a focused block whose category has no cards hides
      whole, chip and all (api has one Working card: Blocked and Completed vanish), and a card arriving for Blocked
      brings the block back; the row layout keeps every head;
  (k) (l) (m) the slot math over HIDDEN blocks (review round two): a jiggle on the one visible chip (Completed, first in the
      stacked order, the hidden blocks after it) stores nothing; a one-slot
      drag of Completed past Blocked with Working hidden lands right behind Blocked, Working keeping its place; ArrowDown
      on the last visible block (Completed, the hidden Working below it) and ArrowUp on the first move nothing;
  (n) the label's clamp, MEASURED at 520 and 420 px with an 80-character session name: the caret on the name's line to its
      right, the name cut inside the head, the head one line;
  (o) (p) a there-and-back key move from a following state stores nothing; a click focuses the chip so the arrows fire;
  (q) (r) (s) (t) focus is NATIVE (review round two): a mouse click rings nothing (:focus-visible false, outline none), a
      drag ends with the chip blurred and the arrows back with the card cursor, Tab rings, a pointerdown on a clipped chip
      scrolls the list by nothing;
  (u) (v) (w) provenance: an order pinned by DRAG survives a key that lands on the fallback (row; row then single column;
      single column then row), and a board drag leaves the pinned section alone;
  (x) (y) (z) round three: key out, a click, key back stores nothing; a one-pixel slip is a click (focus kept, the arrow
      then moves the block); key out, a board there-and-back drag, key back stores nothing; a right press arms no drag.
Screenshots with FEED_FOCUS_BLOCKS_SHOTS=<path-prefix>: -1-label-dark/-light (unfolded, the label above the blocks),
-2-folded-dark/-light, -3-completed-collapsed-dark/-light, -4-working-widened-dark/-light, -5-reordered-dark/-light,
-6-label-vs-colhead-dark (a clip with the label and a board column head together), -7-empty-row-dark/-light (a focused
session with no cards, side by side), -8-stacked-empty-dark/-light (single column, the empty blocks hidden). Optional: never a skip.
Skips LOUDLY without the extension deps or a Playwright browser, and for nothing else (ROMP_SERVED_TESTS_REQUIRE=1 turns
those two red where the browser is installed); a build or kernel failure is a failure. Source pins ride
ui/webview/feed-focus-section.test.ts, the persisted fields ui/webview/feed-view-state.test.ts. All fixtures synthetic.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)

SID_WEB = "aaaaaaaa-1111-2222-3333-777777777777"
SID_API = "aaaaaaaa-1111-2222-3333-888888888888"
SID_TESTS = "aaaaaaaa-1111-2222-3333-999999999999"
SID_LONG = "aaaaaaaa-1111-2222-3333-aaaaaaaaaaaa"
LONG_NAME = ("notes-api-" * 8)[:80]   # an 80-character session name: the label's clamp (review round two)
IDS = {
    "webWork1": "cccccccc-1111-2222-3333-000000000001",
    "webWork2": "cccccccc-1111-2222-3333-000000000002",
    "webBlocked": "cccccccc-1111-2222-3333-000000000003",
    "webDone1": "cccccccc-1111-2222-3333-000000000004",
    "webDone2": "cccccccc-1111-2222-3333-000000000005",
    "apiWork": "cccccccc-1111-2222-3333-000000000011",
    "apiBlocked": "cccccccc-1111-2222-3333-000000000012",   # road (j): the card whose arrival brings a hidden block back
}
WEB_COLOR = "rgb(30, 161, 235)"   # #1EA1EB, the colour the payload gives web
COLS = ["asks", "needsInput", "completed"]
ROW_DEFAULT = ["asks", "needsInput", "completed"]
MIN_W = 0.35


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
// tall enough for the section AND the board's heads under it: the screenshots show both
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
const errors = [];   // an exception mid-render aborts the view-state write: every page error is evidence
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text().slice(0, 300)); });
// Two document-start hooks, on every navigation. (1) The view-state SEED: a blob parked in window.name by the evaluate
// before a reload is written to localStorage before the page's script hydrates (window.name survives the navigation;
// an evaluate-then-reload would race the old document's end-of-render write). (2) The postMessage capture: the shim
// ASSIGNS window.acquireVsCodeApi, so an accessor wraps every api object it hands out and records what feed.ts posts.
await page.addInitScript(() => {
  if (window.name.startsWith("seed:")) { localStorage.setItem("romp:feedview", window.name.slice(5)); window.name = ""; }
  window.__posted = [];
  let real;
  Object.defineProperty(window, "acquireVsCodeApi", {
    configurable: true,
    get() {
      if (!real) return undefined;
      return function () {
        const api = real.apply(this, arguments);
        const pm = api.postMessage;
        api.postMessage = function (m) { window.__posted.push(m); return pm.apply(api, arguments); };
        return api;
      };
    },
    set(fn) { real = fn; },
  });
});
const ready = async () => {
  await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
  await page.waitForTimeout(600);
};
// the synthetic world: web with two Working, one Blocked, two Completed; api with one Working (its column is the
// payload's parameter: road (g) moves it); tests listed with no cards
const now = Math.floor(Date.now() / 1000);
const ask = (itemId, sid, name, column, text, t) => ({ itemId, sid, name, color: cfg.colors[name], text, t, live: true,
  turnId: "turn-" + itemId.slice(-2), trgb: [30, 161, 235], column, tree: [] });
const payloadOf = (apiColumn) => ({ type: "feed", asks: [
    ask(cfg.ids.webWork1, cfg.web, "web", "working", "notes-api: draft the search index", now - 60),
    ask(cfg.ids.webWork2, cfg.web, "web", "working", "notes-api: paginate the notes list", now - 400),
    ask(cfg.ids.webBlocked, cfg.web, "web", "needs_input", "notes-api: pick the retry policy", now - 300),
    ask(cfg.ids.webDone1, cfg.web, "web", "completed", "notes-api: write the index schema", now - 240),
    ask(cfg.ids.webDone2, cfg.web, "web", "completed", "notes-api: add the tag table migration", now - 900),
    ask(cfg.ids.apiWork, cfg.api, "api", apiColumn, "notes-api: wire the tag filter", now - 120)],
  sessions: [{ sid: cfg.web, name: "web" }, { sid: cfg.api, name: "api" }, { sid: cfg.tests, name: "tests" }, { sid: cfg.long, name: cfg.longName }],
  order: [cfg.web, cfg.api, cfg.tests, cfg.long] });
// the label's clamp, measured: the name's and the caret's rects on the label's line
const labelGeom = () => page.evaluate(() => {
  const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height }; };
  return { head: r("#feed-focus .feed-focus-head"), name: r("#feed-focus .feed-focus-head .fname"), caret: r("#feed-focus .feed-focus-caret"),
           nameText: document.querySelector("#feed-focus .feed-focus-head .fname")?.textContent ?? null };
});
const payload = payloadOf("working");
// the page's own frame path; then ONE animation frame (the handler renders synchronously, there is no timer to wait out)
const deliver = (m) => page.evaluate((m) => new Promise((res) => {
  window.dispatchEvent(new MessageEvent("message", { data: m }));
  requestAnimationFrame(() => res(null));
}), m);
const frame = () => page.evaluate(() => new Promise((res) => requestAnimationFrame(() => res(null))));
// off every card: a hovered card holds the board (hover-freeze) and would defer the section's repaint to the release
const park = async () => { await page.mouse.move(2, 2); await page.waitForTimeout(150); };
// (re)load the page, `seed` = a view-state blob written before the script hydrates; then the world and web's focus
const boot = async (seed) => {
  if (seed !== undefined) await page.evaluate((s) => { window.name = "seed:" + s; }, seed);
  await page.reload();
  await ready();
  await deliver(payload);
  await page.waitForSelector(`#feed-cols [data-key="a:${cfg.ids.webWork1}"]`, { timeout: 10000 });
  await deliver({ type: "activeChat", id: cfg.web });
  await page.waitForSelector(`#feed-focus [data-key="f:a:${cfg.ids.webWork1}"]`, { state: "attached", timeout: 10000 });   // attached: a folded section hides it
  await frame();
  await park();
};
const survey = () => page.evaluate(() => {
  const sec = document.getElementById("feed-focus");
  const board = document.getElementById("feed-cols");
  const keyOf = (c) => c ? ((c.className.match(/\bcol-(asks|needsInput|completed)\b/) || [])[1] || null) : null;
  const byLeft = (els) => els.slice().sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left).map(keyOf);
  const shown = (e) => !!e && e.style.display !== "none" && getComputedStyle(e).display !== "none";
  const q = (s) => sec ? sec.querySelector(s) : null;
  const cols = sec ? Array.from(sec.querySelectorAll(".feed-focus-cols .feed-col")) : [];
  const head = q(".feed-focus-head"), lbl = q(".feed-focus-head .feed-focus-fold"), nm = q(".feed-focus-head .fname");
  const cards = (root) => root ? Array.from(root.querySelectorAll(".fitem[data-key]")).map((c) => ({ key: c.dataset.key, col: keyOf(c.closest(".feed-col")) })) : [];
  const rect = (e) => { const r = e.getBoundingClientRect(); return { left: r.left, width: r.width }; };
  return {
    section: !!sec, folded: sec ? sec.classList.contains("folded") : null,
    headShown: shown(head), label: lbl ? lbl.textContent : null, labelAria: lbl ? lbl.getAttribute("aria-label") : null,
    labelExpanded: lbl ? lbl.getAttribute("aria-expanded") : null, labelTag: lbl ? lbl.tagName : null,
    headOrder: head ? Array.from(head.children).map((c) => c.className.split(" ")[0]) : [],
    name: nm ? nm.textContent : null, nameColor: nm ? getComputedStyle(nm).color : null, nameTitle: nm ? nm.title : null,
    caret: q(".feed-focus-caret")?.textContent ?? null,
    total: shown(q(".feed-focus-count")) ? q(".feed-focus-count").textContent : null,
    emptyShown: shown(q(".feed-focus-empty")), colsShown: shown(q(".feed-focus-cols")), divider: shown(sec && sec.nextElementSibling && sec.nextElementSibling.matches("hr.feed-focus-divider") ? sec.nextElementSibling : null),
    secOrder: byLeft(cols), boardOrder: byLeft(Array.from(document.querySelectorAll("#feed-cols .feed-col"))),
    // the single-column layout's order: the VISIBLE blocks top to bottom (a hidden block's rect is all zeros)
    secOrderY: cols.filter((c) => shown(c)).sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top).map(keyOf),
    rects: Object.fromEntries(cols.map((c) => [keyOf(c), rect(c)])),
    collapsed: cols.filter((c) => c.classList.contains("col-collapsed")).map(keyOf),
    listShown: Object.fromEntries(cols.map((c) => [keyOf(c), shown(c.querySelector(".feed-col-list"))])),
    chipShown: Object.fromEntries(cols.map((c) => [keyOf(c), shown(c.querySelector(".fcol-chip"))])),
    counts: Object.fromEntries(cols.map((c) => [keyOf(c), c.querySelector(".feed-col-count").textContent])),
    blockFolds: Object.fromEntries(cols.map((c) => { const f = c.querySelector(".fcol-fold"); return [keyOf(c), f ? f.textContent + "|" + f.getAttribute("aria-expanded") : null]; })),
    gutters: Object.fromEntries(cols.map((c) => [keyOf(c), shown(c.querySelector(".focus-gutter"))])),
    grips: sec ? sec.querySelectorAll(".drag-grip").length : 0,   // none: the chip is the handle (the user 2026-09-14)
    chips: Object.fromEntries(cols.map((c) => { const g = c.querySelector(".fcol-chip"); return [keyOf(c), g ? { tag: g.tagName, tabindex: g.getAttribute("tabindex"), text: g.textContent,
      keys: g.getAttribute("aria-keyshortcuts"), title: g.title, cursor: getComputedStyle(g).cursor, touch: getComputedStyle(g).touchAction, shown: shown(c) } : null]; })),
    secCards: cards(sec), boardCards: cards(board),
    stored: JSON.parse(localStorage.getItem("romp:feedview") || "null"),
    posted: (window.__posted || []).filter((m) => m && m.type === "openSession"),
    hovered: !!document.querySelector(".fitem:hover"),
  };
});
const metrics = () => page.evaluate(() => {
  const cs = (s) => { const e = document.querySelector(s); return e ? getComputedStyle(e) : null; };
  const lbl = cs("#feed-focus .feed-focus-head .feed-focus-fold"), head = cs("#feed-focus .feed-focus-head"), nm = cs("#feed-focus .feed-focus-head .fname");
  const col = cs("#feed-cols .feed-col-head"), sess = cs("#feed-cols .feed-sess-head"), hr = cs("#feed-focus + hr.feed-focus-divider");
  const caret = cs("#feed-focus .feed-focus-caret"), blockCaret = cs("#feed-focus .col-asks .fcol-fold"), chip = cs("#feed-focus .col-asks .fcol-chip");
  const sessName = cs("#feed-cols .feed-sess-head .fname");
  const pick = (c, k) => (c ? c[k] : null);
  return { labelSize: pick(lbl, "fontSize"), labelWeight: pick(lbl, "fontWeight"), labelColor: pick(lbl, "color"),
           headSize: pick(head, "fontSize"), headWeight: pick(head, "fontWeight"),
           nameSize: pick(nm, "fontSize"), nameWeight: pick(nm, "fontWeight"), nameColor: pick(nm, "color"),
           colHeadSize: pick(col, "fontSize"), sessHeadWeight: pick(sess, "fontWeight"),
           dividerWidth: pick(hr, "borderTopWidth"), dividerColor: pick(hr, "borderTopColor"),
           caretSize: pick(caret, "fontSize"), blockCaretSize: pick(blockCaret, "fontSize"),
           sessNameSize: pick(sessName, "fontSize"), sessNameWeight: pick(sessName, "fontWeight"),
           chipCursor: pick(chip, "cursor"), chipTouch: pick(chip, "touchAction") };
});
const light = async (on) => {   // LIGHT theme: the classes the feed's theme switch sets
  await page.evaluate((on) => { document.body.classList[on ? "add" : "remove"]("chat-theme-yatharth", "theme-light"); }, on);
  await page.waitForTimeout(250);
};
const shotBoth = async (name) => {
  if (!cfg.shots) return;
  await page.screenshot({ path: `${cfg.shots}-${name}-dark.png` });
  await light(true);
  await page.screenshot({ path: `${cfg.shots}-${name}-light.png` });
  await light(false);
};
// a block's chip (the handle, as on the board): hold it, carry it along the row to x, release, let the 150 ms glide and the settle pass
const dragBlockChip = async (key, x) => {
  const g = await page.locator(`#feed-focus .col-${key} .feed-col-head .fcol-chip`).boundingBox();
  const y = g.y + g.height / 2;
  await page.mouse.move(g.x + g.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 16 });
  await page.waitForTimeout(200);
  await page.mouse.up();
  await page.waitForTimeout(300);
  await park();
};
// a board column's chip: the board's own drag (wireColDrag over #feed-cols)
const dragChip = async (key, x) => {
  const c = await page.locator(`#feed-cols .col-${key} .feed-col-head .fcol-chip`).boundingBox();
  const y = c.y + c.height / 2;
  await page.mouse.move(c.x + c.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 16 });
  await page.waitForTimeout(200);
  await page.mouse.up();
  await page.waitForTimeout(300);
  await park();
};
// a block's gutter, dragged dx pixels along the row
const dragGutter = async (key, dx) => {
  const g = await page.locator(`#feed-focus .col-${key} .focus-gutter`).boundingBox();
  const x = g.x + g.width / 2, y = g.y + Math.min(40, g.height / 2);
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y, { steps: 12 });
  await page.mouse.up();
  await frame();
  await park();
};
const T347_BLOB = JSON.stringify({ v: 1, sec: {}, tree: [], nodes: [], logs: [], asks: [], threads: [], cols: [], order: [], focused: true });
const out = {};
await page.goto(cfg.feed);
await ready();
// (f) a store from BEFORE the change: the T347 fields only, the switch on
await boot(T347_BLOB);
out.fresh = await survey();
// (h) the label against the board's column head and the section heads, both themes
out.metricsDark = await metrics();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-1-label-dark.png" });
await light(true);
out.metricsLight = await metrics();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-1-label-light.png" });
await light(false);
// (e) the LABEL folds the whole section
await page.click("#feed-focus .feed-focus-head .feed-focus-fold");   // the label's text
await frame(); await park();
out.folded = await survey();
if (cfg.shots) {
  await page.screenshot({ path: cfg.shots + "-2-folded-dark.png" });
  const lb = await page.locator("#feed-focus .feed-focus-head").boundingBox();
  const ch = await page.locator("#feed-cols .feed-col-head").first().boundingBox();
  const top = Math.max(0, lb.y - 6);
  await page.screenshot({ path: cfg.shots + "-6-label-vs-colhead-dark.png", clip: { x: 0, y: top, width: 1100, height: ch.y + ch.height + 6 - top } });
  await light(true);
  await page.screenshot({ path: cfg.shots + "-2-folded-light.png" });
  await light(false);
}
await boot();   // a reload keeps it folded (the blob)
out.foldedReloaded = await survey();
await page.click("#feed-focus .feed-focus-caret");   // the caret at the end of the line unfolds it
await frame(); await park();
out.unfolded = await survey();
await page.focus("#feed-focus .feed-focus-head .feed-focus-fold");
await page.keyboard.press("Enter");
await frame();
out.keyFolded = await survey();
await page.keyboard.press(" ");
await frame();
out.keyUnfolded = await survey();
await page.click("#feed-focus .feed-focus-head .fname");   // the name keeps its own click: open the session, no fold
await frame(); await park();
out.nameClicked = await survey();
// (d) the Completed block's caret folds the block; the fold holds across the focused session and a reload
await page.click("#feed-focus .col-completed .fcol-fold");
await frame(); await park();
out.completedFolded = await survey();
await shotBoth("3-completed-collapsed");
await deliver({ type: "activeChat", id: cfg.api });
out.completedFoldedApi = await survey();
await deliver({ type: "activeChat", id: cfg.web });
await boot();
out.completedFoldedReloaded = await survey();
await page.click("#feed-focus .col-completed .fcol-fold");
await frame(); await park();
out.completedOpen = await survey();
// (c) the gutter between Working and Blocked, 180 px right
await dragGutter("asks", 180);
out.widened = await survey();
await shotBoth("4-working-widened");
// (a) the Blocked chip to the first slot: past the Working block's midpoint, leftwards
const work = await page.locator("#feed-focus .col-asks").boundingBox();
await dragBlockChip("needsInput", work.x + 30);
out.reordered = await survey();
await shotBoth("5-reordered");
// (b) the board's Completed chip to the first slot: the board moves, the section (its own order now) stays
const boardWork = await page.locator("#feed-cols .col-asks").boundingBox();
await dragChip("completed", boardWork.x + 30);
out.boardDragged = await survey();
// (g) the freeze low: the pointer on the section's copy; a payload that moves api's card queues; a synthetic
// mouseleave on the BOARD twin must not release the hold; leaving the copy applies the payload
await page.hover(`#feed-focus [data-key="f:a:${cfg.ids.webWork1}"]`);
await page.waitForTimeout(150);
await deliver(payloadOf("completed"));
await page.evaluate((id) => { document.querySelector(`#feed-cols [data-key="a:${id}"]`).dispatchEvent(new MouseEvent("mouseleave")); }, cfg.ids.webWork1);
await frame();
out.stillHeld = await survey();
await park();
await frame();
out.released = await survey();
// (c, the floor) the Working gutter far past its neighbour (Completed, in the section's order now)
await dragGutter("asks", 1500);
out.floor = await survey();
// (b, fresh) a board chip drag with no section order of its own moves the section too
await boot(T347_BLOB);
out.fresh2 = await survey();
const boardWork2 = await page.locator("#feed-cols .col-asks").boundingBox();
await dragChip("completed", boardWork2.x + 30);
out.freshBoardDragged = await survey();
// (i) a focused session with NO cards, side by side: the label stands, no quiet line, three heads over empty lists
await deliver({ type: "activeChat", id: cfg.tests });
await frame(); await park();
out.emptyRow = await survey();
await shotBoth("7-empty-row");
// (j) SINGLE COLUMN (a narrow viewport stacks the columns): api has one Working card, so Blocked and Completed hide
// whole, chip and all; a Blocked card arriving for api brings that block back; then the row layout again
await page.setViewportSize({ width: 520, height: 760 });
await deliver({ type: "activeChat", id: cfg.api });
await frame(); await park();
out.stackedEmpty = await survey();
await shotBoth("8-stacked-empty");
const withBlocked = payloadOf("working");
withBlocked.asks.push(ask(cfg.ids.apiBlocked, cfg.api, "api", "needs_input", "notes-api: choose the tag limit", now - 30));
await deliver(withBlocked);
await frame(); await park();
out.stackedArrived = await survey();
// (k) stacked, the section following the board (no order of its own): api's one card sits in Completed, the FIRST block
// of the stacked order, so Completed is the only visible block with the two hidden ones after it; a 14 px jiggle on its
// chip must store NOTHING (the hidden blocks' zero rects used to read as slots past the pointer, and the old math stored
// needsInput, asks, completed: an order the user never chose)
await boot(T347_BLOB);
await page.setViewportSize({ width: 520, height: 760 });
await deliver(payloadOf("completed"));
await deliver({ type: "activeChat", id: cfg.api });
await frame(); await park();
const jw = await page.locator("#feed-focus .col-completed .feed-col-head .fcol-chip").boundingBox();
await page.mouse.move(jw.x + jw.width / 2, jw.y + jw.height / 2);
await page.mouse.down();
await page.mouse.move(jw.x + jw.width / 2, jw.y + jw.height / 2 + 14, { steps: 4 });
await page.waitForTimeout(150);
await page.mouse.up();
await page.waitForTimeout(300); await park();
out.stackedJiggle = await survey();
// (l) stacked, api with a Blocked and a Completed card and NO Working card (Working hidden): a one-slot drag of Completed
// down past Blocked must land it right behind Blocked, the hidden Working keeping its place last
const twoBlocks = payloadOf("completed");
twoBlocks.asks.push(ask(cfg.ids.apiBlocked, cfg.api, "api", "needs_input", "notes-api: choose the tag limit", now - 30));
await deliver(twoBlocks);
await frame(); await park();
out.stackedTwo = await survey();
const bl = await page.locator("#feed-focus .col-needsInput").boundingBox();
const cc = await page.locator("#feed-focus .col-completed .feed-col-head .fcol-chip").boundingBox();
await page.mouse.move(cc.x + cc.width / 2, cc.y + cc.height / 2);
await page.mouse.down();
await page.mouse.move(cc.x + cc.width / 2, bl.y + bl.height * 0.75, { steps: 16 });
await page.waitForTimeout(200);
await page.mouse.up();
await page.waitForTimeout(300); await park();
out.stackedOneSlot = await survey();
// (m) the arrow keys skip a hidden neighbour: after (l) Completed is the last VISIBLE block (the hidden Working below
// it), so ArrowDown on its chip moves nothing, and ArrowUp on Blocked (the first) moves nothing either
await page.focus("#feed-focus .col-completed .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowDown");
await frame(); await park();
out.stackedKeyDown = await survey();
await page.focus("#feed-focus .col-needsInput .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowUp");
await frame(); await park();
out.stackedKeyUp = await survey();
// (n) the label's clamp, measured at 520 and 420 px with an 80-character session name: the caret sits on the name's line,
// to its right; the name is cut with an ellipsis, never wrapped
await deliver({ type: "activeChat", id: cfg.long });
await frame(); await park();
out.long520 = await labelGeom();
await shotBoth("9-long-name-520");
await page.setViewportSize({ width: 420, height: 760 });
await frame(); await park();
out.long420 = await labelGeom();
await page.setViewportSize({ width: 1100, height: 760 });
await deliver(payload);
await deliver({ type: "activeChat", id: cfg.web });
await frame(); await park();
out.rowAgain = await survey();
// (o) a there-and-back keyboard move from a FOLLOWING state (no order of its own) stores nothing (review, low 1)
await boot(T347_BLOB);
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowRight");
await frame(); await park();
out.keyThere = await survey();
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowLeft");
await frame(); await park();
out.keyBack = await survey();
// (p) a CLICK on the chip focuses it, so the arrow keys its tooltip promises need no Tab first (review, low 2)
await page.click("#feed-focus .col-needsInput .feed-col-head .fcol-chip");
await frame();
out.clickFocused = await page.evaluate(() => { const a = document.activeElement; return a ? (a.className.split(" ").slice(0, 2).join(" ") + "|" + a.textContent) : null; });
await page.keyboard.press("ArrowRight");
await frame(); await park();
out.clickArrow = await survey();
// the focus probe: which element holds focus, whether the keyboard ring shows (:focus-visible and the computed outline)
const focusProbe = () => page.evaluate(() => {
  const a = document.activeElement;
  const chip = a && a.classList.contains("fcol-chip") && a.closest("#feed-focus") ? a : null;
  return { active: a ? (a.className.split(" ").slice(0, 2).join(" ") + "|" + (a.textContent || "").slice(0, 12)) : null,
           visible: chip ? chip.matches(":focus-visible") : null, outline: chip ? getComputedStyle(chip).outlineStyle : null };
});
// (q) a mouse click focuses the chip natively: no keyboard ring (review round two, medium 2)
await boot(T347_BLOB);
await page.click("#feed-focus .col-needsInput .feed-col-head .fcol-chip");
await frame();
out.clickRing = await focusProbe();
// (r) a DRAG ends with the chip blurred, no ring, and ArrowDown then moves nothing
const w2 = await page.locator("#feed-focus .col-asks").boundingBox();
await dragBlockChip("needsInput", w2.x + 30);
out.dragRing = await focusProbe();
await page.keyboard.press("ArrowRight");
await frame(); await park();
out.dragArrow = await survey();
// (s) Tab still rings: from the label button, Tab lands on the first chip with :focus-visible
await page.focus("#feed-focus .feed-focus-head .feed-focus-fold");
await page.keyboard.press("Tab");
await frame();
out.tabRing = await focusProbe();
// (t) a pointerdown on a partly clipped chip does not scroll the feed (medium 3): a short viewport so the list scrolls,
// the list scrolled so a section head sits half under its top edge, press, read the scroll again
await page.setViewportSize({ width: 1100, height: 420 });
await frame(); await park();
const clipped = await page.evaluate(() => {
  const list = document.getElementById("feed-list");
  const head = document.querySelector("#feed-focus .col-completed .feed-col-head");
  const r = head.getBoundingClientRect(), lr = list.getBoundingClientRect();
  list.scrollTop = Math.max(0, r.top - lr.top + Math.round(r.height / 2));   // the head half under the list's top edge
  const c = document.querySelector("#feed-focus .col-completed .feed-col-head .fcol-chip").getBoundingClientRect();
  return { before: list.scrollTop, chipX: c.left + c.width / 2, chipY: Math.min(lr.bottom - 2, Math.max(lr.top + 2, c.top + c.height * 0.8)) };
});
await page.mouse.move(clipped.chipX, clipped.chipY);
await page.mouse.down();
await page.waitForTimeout(120);
const scrolledTo = await page.evaluate(() => document.getElementById("feed-list").scrollTop);
await page.mouse.up();
await frame(); await park();
out.clipScroll = { before: clipped.before, after: scrolledTo };
await page.evaluate(() => { document.getElementById("feed-list").scrollTop = 0; });
await page.setViewportSize({ width: 1100, height: 760 });
await frame(); await park();
// (u) PROVENANCE (medium 1): an order pinned by DRAG is never cleared by a key that lands on the fallback, and the next
// board drag leaves the pinned section alone
await boot(T347_BLOB);
const w3 = await page.locator("#feed-focus .col-asks").boundingBox();
await dragBlockChip("needsInput", w3.x + 30);   // Blocked first: focusOrder [needsInput, asks, completed], pinned by drag
out.pinnedByDrag = await survey();
await page.focus("#feed-focus .col-needsInput .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowRight");      // back to the board's arrangement: stays STORED (a drag pinned it)
await frame(); await park();
out.pinnedKeyBack = await survey();
const bw = await page.locator("#feed-cols .col-asks").boundingBox();
await dragChip("completed", bw.x + 30);        // a board drag: the pinned section does not follow
out.pinnedBoardDrag = await survey();
// (v) row to single column: a row drag pins; at 520 px an ArrowDown landing on the stacked default clears nothing
await boot(T347_BLOB);
const w4 = await page.locator("#feed-focus .col-asks").boundingBox();
await dragBlockChip("completed", w4.x + 30);   // Completed first: [completed, asks, needsInput], pinned by drag
await page.setViewportSize({ width: 520, height: 760 });
await frame(); await park();
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowDown");        // [completed, needsInput, asks] = the stacked default: must stay stored
await frame(); await park();
out.pinnedStackedKey = await survey();
await page.setViewportSize({ width: 1100, height: 760 });
await frame(); await park();
out.pinnedBackToRow = await survey();
// (w) the reverse: a single-column drag pins; back in the row layout an ArrowRight landing on the row default clears nothing
await boot(T347_BLOB);
await page.setViewportSize({ width: 520, height: 760 });
await frame(); await park();
const cbl = await page.locator("#feed-focus .col-needsInput").boundingBox();
const ccc = await page.locator("#feed-focus .col-completed .feed-col-head .fcol-chip").boundingBox();
await page.mouse.move(ccc.x + ccc.width / 2, ccc.y + ccc.height / 2);
await page.mouse.down();
await page.mouse.move(ccc.x + ccc.width / 2, cbl.y + cbl.height * 0.75, { steps: 16 });   // Completed down past Blocked
await page.waitForTimeout(200);
await page.mouse.up();
await page.waitForTimeout(300); await park();
out.stackedPinned = await survey();            // [needsInput, completed, asks], pinned by drag
await page.setViewportSize({ width: 1100, height: 760 });
await frame(); await park();
await page.focus("#feed-focus .col-needsInput .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowRight");       // [completed, needsInput, asks]? no: Blocked one slot right = [completed, needsInput, asks]
await frame(); await park();
out.rowKeyAfterStackedPin = await survey();
// (x) key out, a plain CLICK on a chip, key back: the click must not drop the keys' provenance (round three, medium 1)
await boot(T347_BLOB);
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowRight");
await frame(); await park();
await page.click("#feed-focus .col-completed .feed-col-head .fcol-chip");
await frame(); await park();
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowLeft");
await frame(); await park();
out.keyClickKey = await survey();
// (y) a one-pixel slip on a press is a click, not a drag: the chip keeps focus and ArrowRight then moves the block (medium 2)
await boot(T347_BLOB);
const sc = await page.locator("#feed-focus .col-asks .feed-col-head .fcol-chip").boundingBox();
await page.mouse.move(sc.x + sc.width / 2, sc.y + sc.height / 2);
await page.mouse.down();
await page.mouse.move(sc.x + sc.width / 2 + 1, sc.y + sc.height / 2, { steps: 1 });
await page.mouse.up();
await frame();
out.slipFocus = await focusProbe();
await page.keyboard.press("ArrowRight");
await frame(); await park();
out.slipArrow = await survey();
// (z) key out, a BOARD there-and-back drag (Completed to the first slot and back to its own), key back: stores nothing
await boot(T347_BLOB);
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
await page.keyboard.press("ArrowRight");
await frame(); await park();
out.zMinted = await survey();
const bw2 = await page.locator("#feed-cols .col-asks").boundingBox();
const bcols = await page.locator("#feed-cols").boundingBox();
const bcc = await page.locator("#feed-cols .col-completed .feed-col-head .fcol-chip").boundingBox();
const bcx = bcc.x + bcc.width / 2, bcy = bcc.y + bcc.height / 2;
await page.mouse.move(bcx, bcy);
await page.mouse.down();
await page.mouse.move(bw2.x + 30, bcy, { steps: 16 });    // past Working's midpoint: Completed first
await page.waitForTimeout(200);
await page.mouse.move(bcols.x + bcols.width - 30, bcy, { steps: 16 });   // and back past the last column's midpoint: its own slot again
await page.waitForTimeout(200);
await page.mouse.up();
await page.waitForTimeout(300); await park();
out.boardThereAndBack = await survey();
await page.focus("#feed-focus .col-asks .feed-col-head .fcol-chip");
out.zBeforeKey = await focusProbe();
await page.keyboard.press("ArrowLeft");
out.zRightAfter = await page.evaluate(() => ({ stored: JSON.parse(localStorage.getItem("romp:feedview") || "null"), active: document.activeElement ? document.activeElement.className + "|" + document.activeElement.textContent : null,
  vars: Array.from(document.querySelectorAll("#feed-focus .feed-col")).map((c) => c.className.match(/col-(asks|needsInput|completed)/)[1] + ":" + c.style.getPropertyValue("--col-order")) }));
await frame(); await park();
out.keyAfterBoardBack = await survey();
// a RIGHT press arms no drag: the block stays where it is
await boot(T347_BLOB);
const rc = await page.locator("#feed-focus .col-needsInput .feed-col-head .fcol-chip").boundingBox();
const rw = await page.locator("#feed-focus .col-asks").boundingBox();
await page.mouse.move(rc.x + rc.width / 2, rc.y + rc.height / 2);
await page.mouse.down({ button: "right" });
await page.mouse.move(rw.x + 30, rc.y + rc.height / 2, { steps: 12 });
await page.mouse.up({ button: "right" });
await page.keyboard.press("Escape");   // any context menu
await frame(); await park();
out.rightPress = await survey();
out.errors = errors;
fs.writeFileSync(cfg.out, JSON.stringify(out));   // a file, not stdout: the survey record is past the size one pipe write carries whole
await browser.close();
process.exit(0);
"""


class ServedFocusedSectionBlocks(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="feedfocusblocks-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise AssertionError("esbuild failed here: " + (b.stderr or b.stdout)[-400:])   # a build failure is a failure, never a skip
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "session-hosts"), "w") as fh:   # a lab root of its own pins the hosts OFF (CLAUDE.md 2026-09-11)
            fh.write("off\n")
        cls.port = _free_port()
        cls.token = "testtok-feedfocusblocks"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):   # bounded: 60 s of half-second probes, the served labs' own wait
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            cls.kernel.wait()
            raise AssertionError("hermetic kernel never served /healthz here; log tail:\n" + open(cls.klog).read()[-1500:])

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @staticmethod
    def _col_of(cards, key):
        return next((c["col"] for c in cards if c["key"] == key), None)

    def test_the_sections_blocks_move_resize_and_fold_on_their_own_and_the_label_folds_the_section(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token),
                       "web": SID_WEB, "api": SID_API, "tests": SID_TESTS, "ids": IDS,
                       "long": SID_LONG, "longName": LONG_NAME, "out": os.path.join(self.lab, "result.json"),
                       "colors": {"web": {"bg": "#1EA1EB", "fg": "#ffffff"}, "api": {"bg": "#E0A526", "fg": "#000000"}},
                       "shots": os.environ.get("FEED_FOCUS_BLOCKS_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        out_path = os.path.join(self.lab, "result.json")
        self.assertTrue(os.path.exists(out_path), "driver wrote no result file:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        with open(out_path) as fh:
            r = json.load(fh)
        self.assertEqual(r.get("errors"), [], "the page threw nothing (an exception mid-render would skip the view-state write): %r" % r.get("errors"))
        web_keys = {"f:a:" + IDS[k] for k in ("webWork1", "webWork2", "webBlocked", "webDone1", "webDone2")}

        # ── (f) a store from before the change: the board's order, equal widths, nothing folded, the label unfolded ──
        fr = r["fresh"]
        self.assertTrue(fr["section"] and fr["headShown"], "the seeded switch builds the section with its label: %r" % {k: fr[k] for k in ("section", "headShown", "stored")})
        self.assertEqual(fr["stored"].get("focused"), True, "the T347 blob was what the page hydrated: %r" % fr["stored"])
        for k in ("focusOrder", "focusW", "focusCols", "focusFolded"):
            self.assertEqual(fr["stored"].get(k), {"focusW": {}}.get(k, [] if k != "focusFolded" else False), "the pre-change blob reads as the default for %s: %r" % (k, fr["stored"]))
        self.assertEqual(fr["secOrder"], ROW_DEFAULT, "the section follows the board's (default) order: %r" % fr["secOrder"])
        self.assertEqual(fr["boardOrder"], ROW_DEFAULT)
        widths = [fr["rects"][k]["width"] for k in COLS]
        self.assertLess(max(widths) - min(widths), 2, "equal widths (an empty focusW): %r" % widths)
        self.assertEqual((fr["collapsed"], fr["folded"], fr["labelExpanded"], fr["caret"], fr["total"]), ([], False, "true", "▾", None),
                         "nothing folded, the label unfolded with the open caret and no count: %r" % fr)
        self.assertEqual(sorted(c["key"] for c in fr["secCards"]), sorted(web_keys), "web's five copies: %r" % fr["secCards"])
        self.assertEqual(fr["counts"], {"asks": "2", "needsInput": "1", "completed": "2"})
        self.assertEqual(fr["gutters"], {"asks": True, "needsInput": True, "completed": False}, "a gutter on every block but the last: %r" % fr["gutters"])
        # the label: "Current session:" then the name, in the session's identity colour, the name's title kept
        self.assertEqual((fr["label"], fr["labelAria"], fr["name"], fr["nameColor"], fr["nameTitle"]),
                         ("Current session:", "Current session: web", "web", WEB_COLOR, "open this session"), "the label reads Current session: web: %r" % fr)
        self.assertEqual(fr["labelTag"], "BUTTON", "the label is an accessible control")
        # no grip (the user 2026-09-14): the chip is the handle, as on the board — focusable, the arrow keys promised,
        # grab cursor, touch-action none
        self.assertEqual(fr["grips"], 0, "no grip on the section's heads")
        for k, label in (("asks", "Working"), ("needsInput", "Blocked"), ("completed", "Completed")):
            g = fr["chips"][k]
            self.assertEqual((g["tag"], g["tabindex"], g["text"], g["keys"], g["title"], g["cursor"], g["touch"]),
                             ("SPAN", "0", label, "ArrowLeft ArrowRight", "Drag to reorder, or press the arrow keys", "grab", "none"), "the %s chip: %r" % (label, g))

        # ── (h) the label's size is the board column head's and its weight the section heads'; both themes ──
        for theme, m in (("dark", r["metricsDark"]), ("light", r["metricsLight"])):
            self.assertEqual(m["labelSize"], m["colHeadSize"], "%s: the label at the column head's size: %r" % (theme, m))
            # the name is styled as a session name below it (the user 2026-09-14): the session headers' size and weight
            self.assertAlmostEqual(float(m["nameSize"][:-2]), float(m["sessNameSize"][:-2]), delta=0.01, msg="%s: the name at the session names' size below: %r" % (theme, m))
            self.assertEqual(m["nameWeight"], m["sessNameWeight"], "%s: …and their weight: %r" % (theme, m))
            self.assertNotEqual(m["nameSize"], m["colHeadSize"], "%s: the name is larger than the label text (the headers' size, not the chips'): %r" % (theme, m))
            self.assertEqual((m["labelWeight"], m["nameWeight"]), ("600", "600"), "%s: the section heads' weight (600) for label and name: %r" % (theme, m))
            if m["sessHeadWeight"] is not None:
                self.assertEqual(m["sessHeadWeight"], "600", "the section heads' weight is 600: %r" % m)
            self.assertEqual(m["nameColor"], WEB_COLOR, "%s: the name in web's identity colour: %r" % (theme, m))
            self.assertAlmostEqual(float(m["caretSize"][:-2]), float(m["blockCaretSize"][:-2]), delta=0.01, msg="%s: the label's caret at the block carets' size: %r" % (theme, m))
            self.assertEqual(m["dividerWidth"], "2px")
            self.assertEqual((m["chipCursor"], m["chipTouch"]), ("grab", "none"), "%s: the chip drags (the board's affordance): %r" % (theme, m))
        self.assertEqual((r["metricsDark"]["dividerColor"], r["metricsLight"]["dividerColor"]), ("rgba(255, 255, 255, 0.22)", "rgba(0, 0, 0, 0.22)"), "--rule-strong in both themes")
        self.assertNotEqual(r["metricsDark"]["labelColor"], r["metricsLight"]["labelColor"], "the label text's colour follows the theme")

        # ── (e) the label folds the whole section ──
        fo = r["folded"]
        self.assertEqual((fo["folded"], fo["headShown"], fo["colsShown"], fo["emptyShown"], fo["divider"]), (True, True, False, False, True),
                         "folded: the label alone, the blocks and the quiet line hidden, the divider kept: %r" % {k: fo[k] for k in ("folded", "headShown", "colsShown", "emptyShown", "divider")})
        self.assertEqual((fo["caret"], fo["labelExpanded"], fo["total"]), ("▸", "false", "5"), "the caret turned, aria-expanded false, web's five cards counted: %r" % fo)
        self.assertEqual(fo["stored"].get("focusFolded"), True, "the fold is persisted: %r" % fo["stored"])
        self.assertEqual(fo["headOrder"][-1], "feed-focus-caret", "the caret sits at the END of the label line: %r" % fo["headOrder"])
        self.assertEqual(sorted(c["key"] for c in fo["boardCards"]), sorted(c["key"] for c in fr["boardCards"]), "the board below is untouched by the fold")
        rl = r["foldedReloaded"]
        self.assertEqual((rl["folded"], rl["colsShown"], rl["caret"], rl["total"]), (True, False, "▸", "5"), "a reload keeps it folded: %r" % rl)
        un = r["unfolded"]
        self.assertEqual((un["folded"], un["colsShown"], un["caret"], un["labelExpanded"], un["total"], un["stored"].get("focusFolded")), (False, True, "▾", "true", None, False),
                         "a click on the caret unfolds it: %r" % un)
        self.assertEqual((r["keyFolded"]["folded"], r["keyUnfolded"]["folded"]), (True, False), "Enter folds, Space unfolds, with the label focused")
        nc = r["nameClicked"]
        self.assertEqual(nc["folded"], False, "the name's click does not fold the section: %r" % nc)
        self.assertEqual([m.get("id") for m in nc["posted"]], [SID_WEB], "the name's click posts the open-session message for web, once: %r" % nc["posted"])
        self.assertEqual(r["keyUnfolded"]["posted"], [], "…and nothing else posted it earlier")

        # ── (d) the Completed caret folds the block; the fold holds across the focused session and a reload ──
        cf = r["completedFolded"]
        self.assertEqual(cf["collapsed"], ["completed"], "Completed folded: %r" % cf["collapsed"])
        self.assertEqual((cf["listShown"]["completed"], cf["chipShown"]["completed"], cf["counts"]["completed"], cf["blockFolds"]["completed"]), (False, True, "2", "▸|false"),
                         "its list hidden, its chip and count shown, its caret turned: %r" % cf)
        self.assertEqual(cf["gutters"], {"asks": True, "needsInput": False, "completed": False}, "no gutter on the folded block nor on the last expanded one: %r" % cf["gutters"])
        self.assertEqual(cf["stored"].get("focusCols"), ["completed"], "persisted as focusCols: %r" % cf["stored"])
        ca = r["completedFoldedApi"]
        self.assertEqual((ca["name"], ca["collapsed"]), ("api", ["completed"]), "focusing api keeps Completed folded (per column key, not per session): %r" % {k: ca[k] for k in ("name", "collapsed")})
        self.assertEqual(r["completedFoldedReloaded"]["collapsed"], ["completed"], "a reload keeps it: %r" % r["completedFoldedReloaded"]["collapsed"])
        self.assertEqual(r["completedOpen"]["collapsed"], [], "the caret again unfolds it")

        # ── (c) the gutter widens Working against Blocked; the weights keep their sum ──
        before, wd = r["completedOpen"], r["widened"]
        self.assertEqual(wd["secOrder"], ROW_DEFAULT, "the gutter moved no block")
        w = wd["stored"].get("focusW") or {}
        self.assertEqual(sorted(w), ["asks", "needsInput"], "the two traded weights are persisted: %r" % w)
        self.assertAlmostEqual(w["asks"] + w["needsInput"], 2.0, delta=0.0011, msg="the pair's sum is the previous sum (1 + 1): %r" % w)
        self.assertGreater(w["asks"], 1.2, "Working took the width: %r" % w)
        dw = wd["rects"]["asks"]["width"] - before["rects"]["asks"]["width"]
        self.assertAlmostEqual(dw, 180, delta=6, msg="Working grew by the drag's 180 px: %r" % dw)
        self.assertAlmostEqual(wd["rects"]["needsInput"]["width"] - before["rects"]["needsInput"]["width"], -180, delta=6, msg="…and Blocked gave it: %r" % wd["rects"])
        self.assertAlmostEqual(wd["rects"]["completed"]["width"], before["rects"]["completed"]["width"], delta=2, msg="Completed untouched: %r" % wd["rects"])

        # ── (a) the Blocked chip dragged to the first slot: the section reorders, the board keeps its order ──
        ro = r["reordered"]
        self.assertEqual(ro["secOrder"], ["needsInput", "asks", "completed"], "Blocked first in the section: %r" % ro["secOrder"])
        self.assertEqual(ro["boardOrder"], ROW_DEFAULT, "the board's columns keep their order: %r" % ro["boardOrder"])
        self.assertEqual(ro["stored"].get("focusOrder"), ["needsInput", "asks", "completed"], "the blob carries the section's own order: %r" % ro["stored"])
        self.assertEqual(ro["stored"].get("order"), [], "…and not the board's: %r" % ro["stored"])
        self.assertEqual(sorted(c["key"] for c in ro["secCards"]), sorted(web_keys), "the cards rode with their blocks")
        self.assertEqual(ro["gutters"], {"needsInput": True, "asks": True, "completed": False}, "the last block in VISUAL order has no gutter: %r" % ro["gutters"])

        # ── (b) a board chip drag moves the board and leaves the section, which has its own order now ──
        bd = r["boardDragged"]
        self.assertEqual(bd["boardOrder"], ["completed", "asks", "needsInput"], "the board's Completed first: %r" % bd["boardOrder"])
        self.assertEqual(bd["secOrder"], ["needsInput", "asks", "completed"], "the section as it was: %r" % bd["secOrder"])
        self.assertEqual((bd["stored"].get("order"), bd["stored"].get("focusOrder")), (["completed", "asks", "needsInput"], ["needsInput", "asks", "completed"]), "two orders, two fields: %r" % bd["stored"])

        # ── (g) the freeze low ──
        sh, re_ = r["stillHeld"], r["released"]
        self.assertTrue(sh["hovered"], "the pointer rests on the copy")
        self.assertEqual(self._col_of(sh["boardCards"], "a:" + IDS["apiWork"]), "asks", "the payload is still queued after a mouseleave on the BOARD twin: api's card has not moved: %r" % sh["boardCards"])
        self.assertEqual(self._col_of(re_["boardCards"], "a:" + IDS["apiWork"]), "completed", "the pointer leaving the copy applied it: %r" % re_["boardCards"])
        self.assertFalse(re_["hovered"])

        # ── (c, the floor) a drag far past the neighbour stops at 0.35 of a share ──
        fl = r["floor"]
        wf = fl["stored"]["focusW"]
        self.assertAlmostEqual(wf["completed"], MIN_W, delta=0.0011, msg="the neighbour (Completed, in the section's order) at the floor: %r" % wf)
        self.assertAlmostEqual(wf["asks"] + wf["completed"], w["asks"] + 1.0, delta=0.0011, msg="the pair's sum preserved (Working's weight plus Completed's 1): %r" % wf)
        pair = fl["rects"]["asks"]["width"] + fl["rects"]["completed"]["width"]
        self.assertAlmostEqual(fl["rects"]["completed"]["width"], pair * MIN_W / (wf["asks"] + wf["completed"]), delta=8, msg="the pixels follow the weights: %r" % fl["rects"])

        # ── (b, fresh) with no section order of its own, a board drag moves the section too ──
        # ── (i) a focused session with NO cards, side by side: the label and the blocks' heads stand, nothing is said ──
        er = r["emptyRow"]
        self.assertEqual((er["headShown"], er["name"], er["emptyShown"], er["colsShown"], er["secCards"], er["grips"]), (True, "tests", False, True, [], 0),
                         "tests focused, no cards: the label, no quiet line, the blocks shown, no grip: %r" % er)
        self.assertEqual({k: v["shown"] for k, v in er["chips"].items()}, {"asks": True, "needsInput": True, "completed": True}, "side by side every head stands over its empty list: %r" % er["chips"])
        self.assertEqual(er["counts"], {"asks": "", "needsInput": "", "completed": ""}, "no counts under empty heads")
        # ── (j) single column: a block with no cards hides whole, chip and all, and returns with its first card ──
        se = r["stackedEmpty"]
        self.assertEqual((se["name"], se["headShown"], se["colsShown"]), ("api", True, True), "api focused in the single-column layout: %r" % se)
        self.assertEqual({k: v["shown"] for k, v in se["chips"].items()}, {"asks": True, "needsInput": False, "completed": False},
                         "api has one Working card: Blocked and Completed hide whole, head and all (the user 2026-09-14): %r" % se["chips"])
        sa = r["stackedArrived"]
        self.assertEqual({k: v["shown"] for k, v in sa["chips"].items()}, {"asks": True, "needsInput": True, "completed": False},
                         "a Blocked card arrived for api: its block is back; Completed still hidden: %r" % sa["chips"])
        self.assertEqual(self._col_of(sa["secCards"], "f:a:" + IDS["apiBlocked"]), "needsInput", "the arrived card sits in the returned block: %r" % sa["secCards"])
        ra = r["rowAgain"]
        self.assertEqual({k: v["shown"] for k, v in ra["chips"].items()}, {"asks": True, "needsInput": True, "completed": True}, "side by side again: every head stands: %r" % ra["chips"])
        # ── (k) stacked, one visible block: a jiggle stores nothing (review round two, medium 1) ──
        sj = r["stackedJiggle"]
        self.assertEqual(sj["stored"]["focusOrder"], [], "a 14 px jiggle on the one visible chip stores no order (the hidden blocks are no slots): %r" % sj["stored"])
        self.assertEqual({k: v["shown"] for k, v in sj["chips"].items()}, {"asks": False, "needsInput": False, "completed": True}, "Completed the only visible block: %r" % sj["chips"])
        # ── (l) stacked, Working hidden: a one-slot drag of Completed past Blocked lands right behind Blocked ──
        st2 = r["stackedTwo"]
        self.assertEqual({k: v["shown"] for k, v in st2["chips"].items()}, {"asks": False, "needsInput": True, "completed": True}, "Working hidden, Blocked and Completed shown: %r" % st2["chips"])
        so = r["stackedOneSlot"]
        self.assertEqual(so["stored"]["focusOrder"], ["needsInput", "completed", "asks"],
                         "Completed one slot down, behind Blocked; the hidden Working keeps its place last (the base stored it behind Working): stored %r, on screen %r, rects %r, two-block state %r"
                         % (so["stored"], so["secOrder"], so["rects"], {k: (v["shown"], v["cursor"]) for k, v in st2["chips"].items()}))
        # ── (m) the arrow keys skip a hidden neighbour ──
        sk = r["stackedKeyDown"]
        self.assertEqual(sk["stored"]["focusOrder"], ["needsInput", "completed", "asks"], "ArrowDown on the last visible block (Completed) moves nothing past the hidden Working: %r" % sk["stored"])
        self.assertEqual(sk["secOrderY"], ["needsInput", "completed"], "on screen, Blocked then Completed, unchanged: %r" % sk["secOrderY"])
        self.assertEqual(so["secOrderY"], ["needsInput", "completed"], "after the one-slot drag, Blocked then Completed on screen: %r" % so["secOrderY"])
        self.assertEqual(r["stackedKeyUp"]["stored"]["focusOrder"], ["needsInput", "completed", "asks"], "ArrowUp on the first block moves nothing: %r" % r["stackedKeyUp"]["stored"])
        # ── (n) the label's clamp, measured (review round two, medium 2): one line, the caret to the right of the name ──
        for w, g in (("520", r["long520"]), ("420", r["long420"])):
            nm, ca, hd = g["name"], g["caret"], g["head"]
            self.assertEqual(g["nameText"], LONG_NAME)
            self.assertGreater(ca["left"], nm["right"] - 0.5, "%s px: the caret sits to the right of the name: %r" % (w, g))
            self.assertLess(abs((ca["top"] + ca["bottom"]) / 2 - (nm["top"] + nm["bottom"]) / 2), nm["height"] / 2, "%s px: the caret on the name's line: %r" % (w, g))
            self.assertLess(nm["right"], hd["right"] + 0.5, "%s px: the name is clamped inside the head: %r" % (w, g))
            self.assertLess(hd["height"], nm["height"] * 1.6, "%s px: the head is one line: %r" % (w, g))
        # ── (o) a there-and-back keyboard move from a following state stores nothing (review, low 1) ──
        self.assertEqual(r["keyThere"]["stored"]["focusOrder"], ["needsInput", "asks", "completed"], "ArrowRight on Working: one slot on: %r" % r["keyThere"]["stored"])
        self.assertEqual(r["keyBack"]["stored"]["focusOrder"], [], "ArrowLeft back: the section FOLLOWS the board again, nothing stored: %r" % r["keyBack"]["stored"])
        self.assertEqual(r["keyBack"]["secOrder"], ["asks", "needsInput", "completed"], "…and on screen the board's arrangement: %r" % r["keyBack"]["secOrder"])
        # ── (p) a click on the chip focuses it; the arrow keys then work without a Tab (review, low 2) ──
        self.assertEqual(r["clickFocused"], "feed-col-name fcol-chip|Blocked", "the clicked chip holds focus: %r" % r["clickFocused"])
        self.assertEqual(r["clickArrow"]["stored"]["focusOrder"], ["asks", "completed", "needsInput"], "ArrowRight right after the click moves Blocked one slot on: %r" % r["clickArrow"]["stored"])
        # ── (x) (y) (z) round three: a click and a there-and-back drag keep a key-minted order walkable; a slip is a click ──
        self.assertEqual(r["keyClickKey"]["stored"]["focusOrder"], [], "key out, click, key back: the section FOLLOWS again, nothing stored (a click changed no order, so the keys' provenance stands): %r" % r["keyClickKey"]["stored"])
        self.assertEqual(r["slipFocus"]["active"], "feed-col-name fcol-chip|Working", "a one-pixel slip is a click: the chip keeps its focus: %r" % r["slipFocus"])
        self.assertEqual(r["slipArrow"]["stored"]["focusOrder"], ["needsInput", "asks", "completed"], "…and ArrowRight then moves the block: %r" % r["slipArrow"]["stored"])
        self.assertEqual(r["zMinted"]["stored"]["focusOrder"], ["needsInput", "asks", "completed"], "(z) the key out minted: %r" % r["zMinted"]["stored"])
        self.assertEqual(r["boardThereAndBack"]["stored"]["order"], [], "the board drag ended where it started: no board order stored: %r" % r["boardThereAndBack"]["stored"])
        self.assertEqual(r["boardThereAndBack"]["stored"]["focusOrder"], ["needsInput", "asks", "completed"], "…and the section's own order stood through it: %r / on screen %r / board %r" % (r["boardThereAndBack"]["stored"], r["boardThereAndBack"]["secOrder"], r["boardThereAndBack"]["boardOrder"]))
        self.assertEqual(r["keyAfterBoardBack"]["stored"]["focusOrder"], [], "key out, a board there-and-back, key back: nothing stored (the board changed no order): %r; before the key %r; right after %r" % (r["keyAfterBoardBack"]["stored"], r["zBeforeKey"], r["zRightAfter"]))
        self.assertEqual((r["rightPress"]["stored"]["focusOrder"], r["rightPress"]["secOrder"]), ([], ["asks", "needsInput", "completed"]), "a right press arms no drag: %r" % r["rightPress"]["stored"])
        # ── (u) (v) (w) provenance (review round two, medium 1): a drag-pinned order survives a key that lands on the fallback ──
        pb = r["pinnedByDrag"]
        self.assertEqual(pb["stored"]["focusOrder"], ["needsInput", "asks", "completed"], "pinned by drag: %r" % pb["stored"])
        pk = r["pinnedKeyBack"]
        self.assertEqual(pk["stored"]["focusOrder"], ["asks", "needsInput", "completed"], "ArrowRight lands on the board's arrangement and the order STAYS stored (pinned by drag, not cleared): %r" % pk["stored"])
        pbd = r["pinnedBoardDrag"]
        self.assertEqual(pbd["boardOrder"], ["completed", "asks", "needsInput"], "the board moved: %r" % pbd["boardOrder"])
        self.assertEqual(pbd["secOrder"], ["asks", "needsInput", "completed"], "…and the pinned section did not follow it: %r" % pbd["secOrder"])
        ps = r["pinnedStackedKey"]
        self.assertEqual(ps["stored"]["focusOrder"], ["completed", "needsInput", "asks"], "single column: ArrowDown lands on the stacked default and the drag-pinned order STAYS stored: %r" % ps["stored"])
        self.assertEqual(r["pinnedBackToRow"]["secOrder"], ["completed", "needsInput", "asks"], "back in the row layout the pinned arrangement stands: %r" % r["pinnedBackToRow"]["secOrder"])
        sp = r["stackedPinned"]
        self.assertEqual(sp["stored"]["focusOrder"], ["needsInput", "completed", "asks"], "a single-column drag pins: %r" % sp["stored"])
        rk = r["rowKeyAfterStackedPin"]
        self.assertEqual(rk["stored"]["focusOrder"], ["completed", "needsInput", "asks"], "row layout: ArrowRight on Blocked moves it one slot; the pin stands as an explicit order: %r" % rk["stored"])
        # ── (q) (r) (s) (t) native focus (review round two, mediums 2 and 3) ──
        self.assertEqual((r["clickRing"]["active"], r["clickRing"]["visible"], r["clickRing"]["outline"]), ("feed-col-name fcol-chip|Blocked", False, "none"),
                         "a mouse click focuses the chip without the keyboard ring: %r" % r["clickRing"])
        self.assertNotEqual((r["dragRing"]["active"] or "").split("|")[0], "feed-col-name fcol-chip", "after a drag the chip no longer holds focus: %r" % r["dragRing"])
        self.assertEqual(r["dragArrow"]["stored"]["focusOrder"], ["needsInput", "asks", "completed"], "ArrowRight after a drag moves nothing (the keys went back to the card cursor): %r" % r["dragArrow"]["stored"])
        self.assertEqual((r["tabRing"]["active"], r["tabRing"]["visible"]), ("feed-col-name fcol-chip|Working", True),
                         "Tab from the label lands on the first chip in DOM order (Working; CSS order paints the visual one) WITH the ring: %r" % r["tabRing"])
        self.assertNotEqual(r["tabRing"]["outline"], "none", "…the accent outline painted: %r" % r["tabRing"])
        self.assertGreater(r["clipScroll"]["before"], 0, "the list was scrolled so the head sat under its top edge: %r" % r["clipScroll"])
        self.assertEqual(r["clipScroll"]["after"], r["clipScroll"]["before"], "a pointerdown on the clipped chip scrolls nothing (medium 3): %r" % r["clipScroll"])
        f2, fb = r["fresh2"], r["freshBoardDragged"]
        self.assertEqual((f2["secOrder"], f2["stored"].get("focusOrder")), (ROW_DEFAULT, []), "the reseeded state follows the board: %r" % f2["stored"])
        self.assertEqual((fb["boardOrder"], fb["secOrder"]), (["completed", "asks", "needsInput"], ["completed", "asks", "needsInput"]), "the section followed the board's drag: %r" % {k: fb[k] for k in ("boardOrder", "secOrder")})
        self.assertEqual(fb["stored"].get("focusOrder"), [], "…without minting an order of its own: %r" % fb["stored"])


if __name__ == "__main__":
    unittest.main()
