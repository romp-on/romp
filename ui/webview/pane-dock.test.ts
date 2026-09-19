// THE PANE DOCKING ENGINE's pure half, executed (plans/pane-docking.md phase two): the half-zones by nearest
// edge, the strip join zone for a tab payload only, the landing rectangle the accent outline shows, the grab
// gate and the slop, the seed from the shipped stores (today's row over a fixed-px band) and the reconcile
// with the shown set (park what is off, open what is on at its default dock, the band's px following --tl).
// No DOM. Shipped pane ids (chat-pane, fleet-pane the outline's legacy key, feed-pane, files-pane, tl-pane).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import {
  GUTTER, RING, SLOP, CHAT, FLEET, FEED, FILES, BAND,
  edgeZone, zoneAt, landingRect, grabbable, crossedSlop, growKey, seedLayout, defaultDock, reconcileShown,
  bandPxOf, roundRect, colNumberOf, ownerColumn, planTabDrop,
} from "./pane-dock";
import { layout, leaves, isSplit, has, type Split, type Layout } from "./pane-tree";

const R = (x: number, y: number, w: number, h: number) => ({ x, y, w, h });
const chat = { pane: CHAT, rect: R(0, 0, 600, 800) }, fleet = { pane: FLEET, rect: R(607, 0, 300, 800) }, feed = { pane: FEED, rect: R(914, 0, 400, 800) };
const rects = [chat, fleet, feed];

test("edgeZone: the nearest edge by fraction of the rect's own dimension; null outside", () => {
  const r = R(0, 0, 600, 800);
  assert.equal(edgeZone(r, { x: 10, y: 400 }), "left");
  assert.equal(edgeZone(r, { x: 590, y: 400 }), "right");
  assert.equal(edgeZone(r, { x: 300, y: 10 }), "top");
  assert.equal(edgeZone(r, { x: 300, y: 790 }), "bottom");
  // a wide short pane: a point 20% in from the left and 10% from the top is TOP (fractions, not px)
  assert.equal(edgeZone(R(0, 0, 1000, 100), { x: 200, y: 10 }), "top");
  assert.equal(edgeZone(r, { x: 601, y: 10 }), null, "outside the rect");
  assert.equal(edgeZone(R(0, 0, 0, 10), { x: 0, y: 5 }), null, "a degenerate rect offers nothing");
});

test("zoneAt: a pane drag sees four half-zones per OTHER pane, none over itself, none over no pane", () => {
  const o = { self: FEED, payload: "pane" as const };
  assert.deepEqual(zoneAt(rects, { x: 20, y: 400 }, o), { target: CHAT, edge: "left" });
  assert.deepEqual(zoneAt(rects, { x: 580, y: 400 }, o), { target: CHAT, edge: "right" });
  assert.deepEqual(zoneAt(rects, { x: 750, y: 20 }, o), { target: FLEET, edge: "top" });
  assert.deepEqual(zoneAt(rects, { x: 750, y: 780 }, o), { target: FLEET, edge: "bottom" });
  assert.equal(zoneAt(rects, { x: 1000, y: 400 }, o), null, "over the dragged pane itself: no zone (a drop cancels)");
  assert.equal(zoneAt(rects, { x: 603, y: 400 }, o), null, "over the gutter: no zone");
  assert.equal(zoneAt(rects, { x: 2000, y: 400 }, o), null, "off every pane");
});

test("zoneAt: the strip is a join zone for a TAB payload and stays a zone for a pane too (the engine refuses it)", () => {
  const strips = { [CHAT]: 32 };
  assert.deepEqual(zoneAt(rects, { x: 300, y: 10 }, { self: null, payload: "tab", strips }), { target: CHAT, strip: true });
  assert.deepEqual(zoneAt(rects, { x: 300, y: 40 }, { self: null, payload: "tab", strips }), { target: CHAT, edge: "top" }, "below the strip: the top half");
  assert.deepEqual(zoneAt(rects, { x: 300, y: 10 }, { self: FEED, payload: "pane", strips }), { target: CHAT, strip: true }, "a pane over the strip names the strip, so the engine can show it refused");
  assert.deepEqual(zoneAt(rects, { x: 750, y: 10 }, { self: null, payload: "tab", strips }), { target: FLEET, edge: "top" }, "a non-chat pane has no strip");
});

test("landingRect: the target's half on the edge (less the gutter's share), or the strip band", () => {
  assert.deepEqual(landingRect(rects, { target: CHAT, edge: "left" }), R(0, 0, (600 - GUTTER) / 2, 800));
  assert.deepEqual(landingRect(rects, { target: CHAT, edge: "right" }), R(600 - (600 - GUTTER) / 2, 0, (600 - GUTTER) / 2, 800));
  assert.deepEqual(landingRect(rects, { target: FLEET, edge: "top" }), R(607, 0, 300, (800 - GUTTER) / 2));
  assert.deepEqual(landingRect(rects, { target: FLEET, edge: "bottom" }), R(607, 800 - (800 - GUTTER) / 2, 300, (800 - GUTTER) / 2));
  assert.deepEqual(landingRect(rects, { target: CHAT, strip: true }, { [CHAT]: 32 }), R(0, 0, 600, 32));
  assert.equal(landingRect(rects, { target: BAND, edge: "top" }), null, "a target with no rect");
});

test("grabbable: primary button, not a control; the ring, a top-row run, or Option anywhere", () => {
  const base = { button: 0, alt: false, onRing: false, onTopRun: false, onControl: false };
  assert.equal(grabbable(base), false, "a plain press over content is never a grab");
  assert.equal(grabbable({ ...base, onRing: true }), true);
  assert.equal(grabbable({ ...base, onTopRun: true }), true);
  assert.equal(grabbable({ ...base, alt: true }), true, "Option-drag anywhere");
  assert.equal(grabbable({ ...base, alt: true, onControl: true }), false, "a control yields to itself even with Option");
  assert.equal(grabbable({ ...base, onRing: true, button: 2 }), false, "the primary button only");
  assert.equal(crossedSlop(SLOP - 1, 0), false); assert.equal(crossedSlop(SLOP, 0), true); assert.equal(crossedSlop(0, -SLOP), true);
  assert.equal(RING, 6, "the grab ring: six px since the user's first press on a three px ring landed nothing (2026-09-19)");
});

test("growKey: the shipped store keys, chat<n> for a column", () => {
  assert.deepEqual([CHAT, FLEET, FEED, FILES, "chat-pane-3"].map(growKey), ["chat", "fleet", "feed", "files", "chat3"]);
});

test("seedLayout: today's row by grow weights over the band as a FIXED kid; parity with the shipped flex row", () => {
  const lay = seedLayout({ row: [CHAT, FEED], band: true, bandPx: 200, grow: { chat: 60, feed: 40, fleet: 34, files: 40 } });
  assert.deepEqual(lay.parked, []);
  const root = lay.tree as Split;
  assert.ok(isSplit(root) && root.dir === "col" && root.fixed && root.fixed[1] === 200, "a column, the band fixed at 200 px");
  const rs = layout(lay.tree, R(0, 0, 1007, 1007), GUTTER);
  const by = Object.fromEntries(rs.map((r) => [r.pane, r.rect]));
  assert.equal(by[BAND].h, 200, "the band keeps its px");
  assert.equal(by[BAND].y, 1007 - 200);
  assert.equal(by[CHAT].h, 800, "the row takes the rest, less the gutter");
  assert.equal(Math.round(by[CHAT].w), 600); assert.equal(Math.round(by[FEED].w), 400);   // 60:40 of 1000
  const noBand = seedLayout({ row: [CHAT], band: false, bandPx: 0, grow: {} });
  assert.deepEqual(noBand.tree, { pane: CHAT }, "one pane, no band: a bare leaf");
});

test("defaultDock: a column right of the last chat, the outline right of the chat side, the feed right of the outline, files at the right end, the chat at the left, the band against the root", () => {
  const t = seedLayout({ row: [CHAT, "chat-pane-2", FLEET, FEED], band: false, bandPx: 0, grow: {} }).tree;
  assert.deepEqual(defaultDock(t, "chat-pane-3"), { target: "chat-pane-2", edge: "right" });
  assert.deepEqual(defaultDock(t, FILES), { target: FEED, edge: "right" });
  const t2 = seedLayout({ row: [CHAT, FEED], band: false, bandPx: 0, grow: {} }).tree;
  assert.deepEqual(defaultDock(t2, FLEET), { target: CHAT, edge: "right" });
  assert.deepEqual(defaultDock({ pane: FEED }, FEED), { target: FEED, edge: "left" }, "the feed with no chat and no outline: the left of what is there");
  assert.deepEqual(defaultDock({ pane: FLEET }, FEED), { target: FLEET, edge: "right" });
  assert.deepEqual(defaultDock(t2, CHAT), { target: CHAT, edge: "left" });
  assert.equal(defaultDock(t2, BAND), null, "the band docks against the whole tree");
  assert.equal(defaultDock({ pane: BAND }, FEED), null, "only the band present: nothing to dock against but the root");
});

test("reconcileShown: a pane turned off PARKS, one turned on opens at its default dock, the band follows --tl", () => {
  let lay: Layout = seedLayout({ row: [CHAT, FLEET, FEED], band: true, bandPx: 200, grow: { chat: 60, fleet: 34, feed: 40 } });
  // the rail hides the outline
  lay = reconcileShown(lay, { row: [CHAT, FEED], band: true, bandPx: 200, grow: {} });
  assert.deepEqual(leaves(lay.tree), [CHAT, FEED, BAND]);
  assert.deepEqual(lay.parked, [FLEET]);
  // ...and brings it back: right of the chat, un-parked
  lay = reconcileShown(lay, { row: [CHAT, FLEET, FEED], band: true, bandPx: 200, grow: {} });
  assert.deepEqual(leaves(lay.tree), [CHAT, FLEET, FEED, BAND]);
  assert.deepEqual(lay.parked, []);
  // the band's px follows the shell's --tl
  lay = reconcileShown(lay, { row: [CHAT, FLEET, FEED], band: true, bandPx: 312, grow: {} });
  const root = lay.tree as Split;
  assert.equal(root.fixed && root.fixed[1], 312);
  // the band turned off parks it and drops the fixed kid; turned on again it returns at the bottom, fixed
  lay = reconcileShown(lay, { row: [CHAT, FLEET, FEED], band: false, bandPx: 312, grow: {} });
  assert.equal(has(lay.tree, BAND), false); assert.deepEqual(lay.parked, [BAND]);
  assert.equal((lay.tree as Split).fixed, undefined, "no fixed kid left: the row's split carries none");
  lay = reconcileShown(lay, { row: [CHAT, FLEET, FEED], band: true, bandPx: 250, grow: {} });
  const rs = layout(lay.tree, R(0, 0, 1000, 1000), GUTTER);
  const band = rs.find((r) => r.pane === BAND)!.rect;
  assert.deepEqual([band.y, band.h, band.w], [750, 250, 1000], "back at the bottom, full width, its px");
  assert.deepEqual(lay.parked, []);
  // a column opened by the chat split lands right of the last chat leaf; closing it parks it and the next
  // reconcile without it in the row keeps it parked (the DOM element is gone, the park is bookkeeping)
  lay = reconcileShown(lay, { row: [CHAT, "chat-pane-2", FLEET, FEED], band: true, bandPx: 250, grow: {} });
  assert.deepEqual(leaves(lay.tree), [CHAT, "chat-pane-2", FLEET, FEED, BAND]);
  lay = reconcileShown(lay, { row: [CHAT, FLEET, FEED], band: true, bandPx: 250, grow: {} });
  assert.deepEqual(leaves(lay.tree), [CHAT, FLEET, FEED, BAND]);
  assert.deepEqual(lay.parked, ["chat-pane-2"]);
});

test("reconcileShown: a parked pane whose element is gone (a closed chat column) is pruned; a hidden dashboard pane's park stays", () => {
  let lay: Layout = seedLayout({ row: [CHAT, "chat-pane-2", FLEET], band: false, bandPx: 0, grow: {} });
  // the rail hides the outline and the column closes: both leave the row; only the outline's element remains
  lay = reconcileShown(lay, { row: [CHAT], band: false, bandPx: 0, grow: {}, present: [CHAT, FLEET, FEED, FILES, BAND] });
  assert.deepEqual(leaves(lay.tree), [CHAT]);
  assert.deepEqual(lay.parked, [FLEET], "the outline parks (its iframe is mounted and hidden); the column's park is pruned (nothing is mounted)");
  // without a present list every park is kept (an older caller)
  const kept = reconcileShown(seedLayout({ row: [CHAT, "chat-pane-2"], band: false, bandPx: 0, grow: {} }), { row: [CHAT], band: false, bandPx: 0, grow: {} });
  assert.deepEqual(kept.parked, ["chat-pane-2"]);
});

test("reconcileShown: every shown pane parked (a store from a browser whose panes were all off) restarts from the shown set", () => {
  const lay = reconcileShown({ v: 1, tree: { pane: FILES }, parked: [CHAT, FEED] }, { row: [CHAT, FEED], band: false, bandPx: 0, grow: {} });
  assert.deepEqual(leaves(lay.tree), [CHAT, FEED]);
  assert.deepEqual(lay.parked, [FILES], "the files pane, hidden by the rail, parks");
});

test("reconcileShown is idempotent on a layout that already matches the shown set", () => {
  const lay = seedLayout({ row: [CHAT, FLEET, FEED], band: true, bandPx: 200, grow: { chat: 60, fleet: 34, feed: 40 } });
  const again = reconcileShown(lay, { row: [CHAT, FLEET, FEED], band: true, bandPx: 200, grow: {} });
  assert.deepEqual(again, lay);
});

test("bandPxOf and roundRect", () => {
  assert.equal(bandPxOf("312px"), 312); assert.equal(bandPxOf(""), 200); assert.equal(bandPxOf(null), 200); assert.equal(bandPxOf("nope"), 200);
  assert.deepEqual(roundRect(R(0.4, 0.6, 99.7, 10.2)), R(0, 1, 100, 10), "far edges rounded as edges, so neighbours meet");
});

test("colNumberOf and ownerColumn: the first chat pane is column 1, chat-pane-<n> is n, any other pane none; a session's column from the sets", () => {
  assert.equal(colNumberOf(CHAT), 1); assert.equal(colNumberOf("chat-pane-3"), 3); assert.equal(colNumberOf(FEED), null); assert.equal(colNumberOf(BAND), null);
  const sets = { "2": ["s-a", "s-b"], "3": ["s-c"] };
  assert.equal(ownerColumn(sets, "s-b"), 2); assert.equal(ownerColumn(sets, "s-c"), 3);
  assert.equal(ownerColumn(sets, "s-z"), 1, "unlisted: the first column holds the rest"); assert.equal(ownerColumn(null, "s-a"), 1);
});

test("planTabDrop: a strip joins that column, a non-chat strip refuses, an edge opens a new column, a lone later column moves itself and refuses its own edge", () => {
  const sets = { "2": ["s-a", "s-b"], "3": ["s-c"] };
  assert.deepEqual(planTabDrop({ target: CHAT, strip: true }, "s-a", sets), { kind: "join", col: 1 });
  assert.deepEqual(planTabDrop({ target: "chat-pane-2", strip: true }, "s-z", sets), { kind: "join", col: 2 });
  assert.equal(planTabDrop({ target: FEED, strip: true }, "s-a", sets).kind, "refuse", "a non-chat pane has no strip to join");
  assert.deepEqual(planTabDrop({ target: FEED, edge: "bottom" }, "s-a", sets), { kind: "newColumn" }, "a tab from a column of two: a new column with it");
  assert.deepEqual(planTabDrop({ target: FEED, edge: "bottom" }, "s-z", sets), { kind: "newColumn" }, "a tab from the first column: a new column with it");
  assert.deepEqual(planTabDrop({ target: FEED, edge: "left" }, "s-c", sets), { kind: "moveColumn", pane: "chat-pane-3" }, "alone in column 3: the column's pane moves, nothing is minted");
  assert.equal(planTabDrop({ target: "chat-pane-3", edge: "left" }, "s-c", sets).kind, "refuse", "a lone column on its own edge");
  assert.deepEqual(planTabDrop({ target: FEED, edge: "top" }, "s-a", null), { kind: "newColumn" }, "no sets: the first column");
});

test("reconcileShown: a dock hint puts the next new chat column at the drop edge; without one it lands right of the last chat", () => {
  const base = seedLayout({ row: [CHAT, FLEET, FEED], band: false, bandPx: 0, grow: { chat: 50, fleet: 25, feed: 25 } });
  const hinted = reconcileShown(base, { row: [CHAT, "chat-pane-2", FLEET, FEED], band: false, bandPx: 0, grow: {}, newChatDock: { target: FEED, edge: "bottom" } });
  const row = hinted.tree as Split;
  assert.equal(row.dir, "row"); assert.deepEqual(leaves(hinted.tree), [CHAT, FLEET, FEED, "chat-pane-2"]);
  const last = row.kids[2] as Split;
  assert.ok(isSplit(last) && last.dir === "col", "the feed's slot became a column: the feed over the new pane");
  assert.deepEqual(row.ratios.map((r) => Math.round(r * 100) / 100), [0.5, 0.25, 0.25], "the other panes' shares are untouched");
  const plain = reconcileShown(base, { row: [CHAT, "chat-pane-2", FLEET, FEED], band: false, bandPx: 0, grow: {} });
  assert.deepEqual(leaves(plain.tree), [CHAT, "chat-pane-2", FLEET, FEED], "no hint: right of the last chat");
  // the hint names a target that is not in the tree, or the pane itself: the default dock
  const stray = reconcileShown(base, { row: [CHAT, "chat-pane-2", FLEET, FEED], band: false, bandPx: 0, grow: {}, newChatDock: { target: "ghost", edge: "left" } });
  assert.deepEqual(leaves(stray.tree), [CHAT, "chat-pane-2", FLEET, FEED]);
});
