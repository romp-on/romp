// THE PANE LAYOUT TREE, executed (the pane docking kit, plans/pane-docking.md phase one). The pure tree:
// geometry (layout rects from a viewport), the edits (split, detach/collapse, move, close/park, resize),
// the seed that reproduces today's flex row, and the fail-closed store round-trip. No DOM; the module takes
// ids, a viewport and the tree. Pane ids are generic here; the seed-parity test uses the shipped ids and
// grow weights (chat/fleet/feed/files, `fleet` the outline pane's shipped store key) because reproducing
// today's row is the whole point of that test. Synthetic session ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import {
  isLeaf, isSplit, leaves, has, leafOf, layout, splitAt, detach, move, closePane, openPane, resize,
  seedRowOverBand, serialise, parse, type Node, type Split, type Layout,
} from "./pane-tree";

const near = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) <= eps;
const widthsOf = (n: Node, w: number, h: number, g: number) => {
  const m: Record<string, number> = {};
  for (const { pane, rect } of layout(n, { x: 0, y: 0, w, h }, g)) m[pane] = rect.w;
  return m;
};

test("leaves and has: depth-first ids, membership", () => {
  const t: Split = { dir: "row", kids: [{ pane: "a" }, { dir: "col", kids: [{ pane: "b" }, { pane: "c" }], ratios: [0.5, 0.5] }], ratios: [0.5, 0.5] };
  assert.deepEqual(leaves(t), ["a", "b", "c"]);
  assert.equal(has(t, "b"), true);
  assert.equal(has(t, "z"), false);
  assert.equal(isSplit(t), true);
  assert.equal(isLeaf(t.kids[0]), true);
  assert.equal(leafOf(t, "c")?.pane, "c");
  assert.equal(leafOf(t, "z"), null);
});

test("layout: a leaf fills the box", () => {
  const r = layout({ pane: "a" }, { x: 3, y: 4, w: 100, h: 80 }, 7);
  assert.deepEqual(r, [{ pane: "a", rect: { x: 3, y: 4, w: 100, h: 80 } }]);
});

test("layout: a row divides width by ratio, less the gutters between kids", () => {
  const t: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }, { pane: "c" }], ratios: [0.5, 0.25, 0.25] };
  const rects = layout(t, { x: 0, y: 0, w: 1000, h: 200 }, 10);
  // span = 1000 - 2*10 = 980; a=490, b=245, c=245; y/h pass through; x accumulates size+gutter
  const by: Record<string, { x: number; w: number }> = {};
  for (const { pane, rect } of rects) by[pane] = { x: rect.x, w: rect.w };
  assert.ok(near(by.a.w, 490) && near(by.b.w, 245) && near(by.c.w, 245), JSON.stringify(by));
  assert.ok(near(by.a.x, 0) && near(by.b.x, 500) && near(by.c.x, 755), JSON.stringify(by));
  for (const { rect } of rects) assert.ok(near(rect.h, 200), "row kids keep the full height");
});

test("layout: a column divides height", () => {
  const t: Split = { dir: "col", kids: [{ pane: "top" }, { pane: "bot" }], ratios: [0.8, 0.2] };
  const rects = layout(t, { x: 0, y: 0, w: 300, h: 800 }, 6);
  const by: Record<string, { y: number; h: number }> = {};
  for (const { pane, rect } of rects) by[pane] = { y: rect.y, h: rect.h };
  // span = 800 - 6 = 794; top=635.2, bot=158.8
  assert.ok(near(by.top.h, 794 * 0.8) && near(by.bot.h, 794 * 0.2), JSON.stringify(by));
  assert.ok(near(by.top.y, 0) && near(by.bot.y, 794 * 0.8 + 6), JSON.stringify(by));
});

test("seed reproduces today's flex row: widths proportional to the grow weights", () => {
  // today: #chat/#fleet/#feed/#files{flex:var(--g-x) 1 0}; a flex row sizes each pane in proportion to its
  // grow weight. The seeded row must give the same proportions, so a switch-ON default render is unchanged.
  const order = ["chat", "fleet", "feed", "files"];
  const grow = { chat: 60, fleet: 34, feed: 40, files: 40 };   // the shipped defaults (romp-pane-grow)
  const row = seedRowOverBand(order, grow, null, 0);
  const w = widthsOf(row, 1000, 600, 7);
  const span = 1000 - 3 * 7;   // three gutters between four panes
  const sum = 60 + 34 + 40 + 40;
  for (const p of order) assert.ok(near(w[p], span * (grow[p as keyof typeof grow] / sum)), p + " width " + w[p]);
});

test("seedRowOverBand: a column of the row over the bottom band", () => {
  const t = seedRowOverBand(["chat"], { chat: 60 }, "timeline", 0.25) as Split;
  assert.equal(t.dir, "col");
  assert.deepEqual(leaves(t), ["chat", "timeline"]);
  assert.ok(near(t.ratios[0], 0.75) && near(t.ratios[1], 0.25));
  const rects = layout(t, { x: 0, y: 0, w: 500, h: 1000 }, 0);
  const band = rects.find((r) => r.pane === "timeline")!;
  assert.ok(near(band.rect.h, 250), "the band is a quarter of the height");
});

test("splitAt: flatten a sibling into a same-direction parent, halving the target's share", () => {
  const base: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.5, 0.5] };
  const right = splitAt(base, "a", "c", "right") as Split;
  assert.deepEqual(leaves(right), ["a", "c", "b"], "c goes after a, still one flat row");
  assert.ok(near(right.ratios[0], 0.25) && near(right.ratios[1], 0.25) && near(right.ratios[2], 0.5), JSON.stringify(right.ratios));
  const left = splitAt(base, "a", "c", "left") as Split;
  assert.deepEqual(leaves(left), ["c", "a", "b"], "c goes before a");
});

test("splitAt: nest a new split when the edge direction differs from the parent", () => {
  const base: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.5, 0.5] };
  const t = splitAt(base, "a", "c", "bottom") as Split;
  assert.equal(t.dir, "row");
  assert.equal(isSplit(t.kids[0]), true, "a's slot became a col split");
  const nested = t.kids[0] as Split;
  assert.equal(nested.dir, "col");
  assert.deepEqual(leaves(nested), ["a", "c"], "bottom => a over c");
  assert.ok(near(t.ratios[0], 0.5), "the row slot ratio is unchanged; the split subdivides within it");
});

test("splitAt: wrap the root leaf", () => {
  const t = splitAt({ pane: "a" }, "a", "b", "right") as Split;
  assert.equal(t.dir, "row");
  assert.deepEqual(leaves(t), ["a", "b"]);
  assert.throws(() => splitAt(t, "a", "b", "right"), /already in the tree/);
  assert.throws(() => splitAt(t, "z", "c", "right"), /target not in the tree/);
});

test("detach: remove a leaf and collapse the single-kid split into that kid, keeping its slot ratio", () => {
  const inner: Split = { dir: "col", kids: [{ pane: "b" }, { pane: "c" }], ratios: [0.5, 0.5] };
  const tree: Split = { dir: "row", kids: [{ pane: "a" }, inner], ratios: [0.3, 0.7] };
  const { tree: after, leaf } = detach(tree, "b");
  assert.equal(leaf?.pane, "b");
  const s = after as Split;
  assert.deepEqual(leaves(s), ["a", "c"]);
  assert.ok(near(s.ratios[1], 0.7), "c took the collapsed split's slot ratio 0.7");
  assert.equal(detach({ pane: "a" }, "a").tree, null, "detaching the whole tree yields null");
  assert.equal(detach(tree, "z").leaf, null, "absent pane: no leaf");
});

test("move: detach then dock, carrying a chat group across the move", () => {
  const tree: Split = { dir: "row", kids: [{ pane: "chat", group: ["s1", "s2"], active: "s1" }, { pane: "feed" }], ratios: [0.5, 0.5] };
  const moved = move(tree, "chat", "feed", "bottom");
  assert.deepEqual(leaves(moved).sort(), ["chat", "feed"]);
  const chat = leafOf(moved, "chat")!;
  assert.deepEqual(chat.group, ["s1", "s2"], "the sessions rode the move");
  assert.equal(chat.active, "s1");
  assert.throws(() => move(tree, "chat", "chat", "left"), /dock to itself/);
  assert.throws(() => move({ pane: "chat" }, "chat", "feed", "left"), /only pane|not in the tree/);
  // an empty group is a faithful carry (the marker rides the move), not silently dropped to a bare leaf
  const withEmpty: Split = { dir: "row", kids: [{ pane: "chat", group: [], active: undefined }, { pane: "feed" }], ratios: [0.5, 0.5] };
  const carried = leafOf(move(withEmpty, "chat", "feed", "left"), "chat")!;
  assert.deepEqual(carried.group, [], "an empty group rides the move as an empty group");
});

test("closePane: PARK a plain pane; refuse a chat leaf holding sessions and the only pane", () => {
  const cur: Layout = { v: 1, tree: { dir: "row", kids: [{ pane: "chat", group: ["s1"] }, { pane: "feed" }], ratios: [0.5, 0.5] }, parked: [] };
  const closeFeed = closePane(cur, "feed");
  assert.equal(closeFeed.ok, true);
  assert.deepEqual(leaves(closeFeed.layout.tree), ["chat"], "feed left the tree");
  assert.deepEqual(closeFeed.layout.parked, ["feed"], "feed is parked, not gone");
  const closeChat = closePane(cur, "chat");
  assert.equal(closeChat.ok, false);
  assert.match(closeChat.reason!, /emptied, not closed/);
  const only = closePane({ v: 1, tree: { pane: "chat" }, parked: [] }, "chat");
  assert.equal(only.ok, false);
  assert.match(only.reason!, /only pane/);
});

test("openPane: dock a parked pane (CloseResult shape), drop a stale park, refuse with a reason not a throw", () => {
  const cur: Layout = { v: 1, tree: { pane: "chat" }, parked: ["feed"] };
  const opened = openPane(cur, "feed", "chat", "right");
  assert.equal(opened.ok, true);
  assert.deepEqual(leaves(opened.layout.tree).sort(), ["chat", "feed"]);
  assert.deepEqual(opened.layout.parked, [], "feed unparked");
  // MEDIUM 2: a pane already docked but still (stale) in parked has the park DROPPED, not the layout returned untouched
  const stale: Layout = { v: 1, tree: { dir: "row", kids: [{ pane: "chat" }, { pane: "feed" }], ratios: [0.5, 0.5] }, parked: ["feed"] };
  const cleaned = openPane(stale, "feed", "chat", "right");
  assert.equal(cleaned.ok, true);
  assert.deepEqual(cleaned.layout.parked, [], "the stale park of an already-docked pane is dropped");
  assert.deepEqual(leaves(cleaned.layout.tree).sort(), ["chat", "feed"], "no structural change when already docked");
  // LOW c: a reason, never a throw, for an absent target or opening a pane against itself
  const absent = openPane(cur, "files", "nope", "left");
  assert.equal(absent.ok, false); assert.match(absent.reason!, /target not in the tree/);
  const selfOpen = openPane(cur, "files", "files", "left");
  assert.equal(selfOpen.ok, false); assert.match(selfOpen.reason!, /against itself/);
});

test("resize: move an edge by a fraction, clamped so neither side drops below the min", () => {
  const tree: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }, { pane: "c" }], ratios: [0.4, 0.4, 0.2] };
  const grown = resize(tree, [], 0, 0.1, 0.1) as Split;   // move the a|b edge, a grows by 0.1
  assert.ok(near(grown.ratios[0], 0.5) && near(grown.ratios[1], 0.3) && near(grown.ratios[2], 0.2), JSON.stringify(grown.ratios));
  const clamped = resize(tree, [], 0, 1.0, 0.1) as Split;   // a huge delta clamps b to the min 0.1
  assert.ok(near(clamped.ratios[0], 0.7) && near(clamped.ratios[1], 0.1), JSON.stringify(clamped.ratios));
  const nested: Split = { dir: "row", kids: [{ pane: "x" }, { dir: "col", kids: [{ pane: "y" }, { pane: "z" }], ratios: [0.5, 0.5] }], ratios: [0.5, 0.5] };
  const inner = resize(nested, [1], 0, 0.2, 0.1) as Split;   // path into the col split
  const col = inner.kids[1] as Split;
  assert.ok(near(col.ratios[0], 0.7) && near(col.ratios[1], 0.3), JSON.stringify(col.ratios));
  let badPath: Node | undefined;
  assert.doesNotThrow(() => { badPath = resize(tree, [9], 0, 0.1, 0.1); }, "a bad path does not throw");
  assert.deepEqual((badPath as Split).ratios, [0.4, 0.4, 0.2], "a bad path leaves the ratios unchanged");
  assert.deepEqual(resize(tree, [], 9, 0.1, 0.1), tree, "a bad edge index is a no-op");
});

test("resize: when a pair is already narrower than two mins, the edge is a NO-OP, never a negative ratio", () => {
  // both sides cannot meet the min at once; the old clamp minted a negative ratio that norm() then spread
  // across every sibling, losing a pane and moving untouched ones. The edge must simply not move.
  const tight: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }, { pane: "c" }, { pane: "d" }], ratios: [0.04, 0.04, 0.46, 0.46] };
  assert.deepEqual((resize(tight, [], 0, 0.02, 0.05) as Split).ratios, [0.04, 0.04, 0.46, 0.46], "grow request on a sub-min pair: no change");
  const neg: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }, { pane: "c" }, { pane: "d" }], ratios: [0.1, 0.1, 0.4, 0.4] };
  assert.deepEqual((resize(neg, [], 0, 0, 0.3) as Split).ratios, [0.1, 0.1, 0.4, 0.4], "an unsatisfiable min leaves c and d untouched, no pane lost");
  // a satisfiable clamp never drops either side below the min
  const ok = resize({ dir: "row", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.5, 0.5] }, [], 0, 0.6, 0.1) as Split;
  assert.ok(ok.ratios[0] <= 0.9 + 1e-9 && ok.ratios[1] >= 0.1 - 1e-9, JSON.stringify(ok.ratios));
});

test("serialise/parse: round-trip, and fail-closed on any bad shape", () => {
  const cur: Layout = { v: 1, tree: { dir: "col", kids: [{ dir: "row", kids: [{ pane: "chat" }, { pane: "feed" }], ratios: [0.6, 0.4] }, { pane: "timeline" }], ratios: [0.8, 0.2] }, parked: ["files"] };
  const round = parse(serialise(cur));
  assert.deepEqual(round, cur, "a valid store round-trips exactly");
  assert.equal(parse("not json"), null, "garbage");
  assert.equal(parse(JSON.stringify({ v: 2, tree: { pane: "chat" }, parked: [] })), null, "wrong version");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { dir: "row", kids: [{ pane: "a" }], ratios: [1] }, parked: [] })), null, "a split with one kid is invalid");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { dir: "row", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.5] }, parked: [] })), null, "ratios length must match kids");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { dir: "row", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.3, 0.3] }, parked: [] })), null, "ratios must sum to 1");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { dir: "diag", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.5, 0.5] }, parked: [] })), null, "dir must be row or col");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { pane: 7 }, parked: [] })), null, "a pane id must be a string");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { pane: "chat" }, parked: [3] })), null, "parked must be strings");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { pane: "a", group: [1, 2] }, parked: [] })), null, "a group must be strings");
  assert.equal(parse(JSON.stringify({ v: 1, tree: { pane: "a", active: {} }, parked: [] })), null, "active must be a string");
  const withGroup: Layout = { v: 1, tree: { pane: "chat", group: ["s1", "s2"], active: "s1" }, parked: [] };
  assert.deepEqual(parse(serialise(withGroup)), withGroup, "a valid group/active round-trips");
});

// ---- round two: the read's two mediums and the lows (each red against the pre-fix module) ----

test("parse refuses a duplicate pane id anywhere in the tree (MEDIUM 1: it would strip to a kid-less split and blank the dashboard)", () => {
  const dup = JSON.stringify({ v: 1, tree: { dir: "row", kids: [{ pane: "chat" }, { pane: "chat" }], ratios: [0.5, 0.5] }, parked: [] });
  assert.equal(parse(dup), null, "two 'chat' leaves in one split is refused");
  const deep = JSON.stringify({ v: 1, tree: { dir: "col", kids: [{ pane: "chat" }, { dir: "row", kids: [{ pane: "feed" }, { pane: "chat" }], ratios: [0.5, 0.5] }], ratios: [0.5, 0.5] }, parked: [] });
  assert.equal(parse(deep), null, "a duplicate nested deeper is refused too");
});

test("closePane on a duplicate-id tree is refused, never a blank dashboard (MEDIUM 1, the strip end)", () => {
  const dup: Layout = { v: 1, tree: { dir: "row", kids: [{ pane: "chat" }, { pane: "chat" }], ratios: [0.5, 0.5] }, parked: [] };
  const r = closePane(dup, "chat");
  assert.equal(r.ok, false, "stripping both duplicate leaves must refuse, not leave a kid-less split");
  assert.deepEqual(leaves(r.layout.tree), ["chat", "chat"], "the layout is untouched on the refusal");
});

test("parse refuses a pane that is both docked and parked (MEDIUM 2)", () => {
  const both = JSON.stringify({ v: 1, tree: { pane: "chat" }, parked: ["chat"] });
  assert.equal(parse(both), null, "chat cannot be in the tree and in parked");
});

test("resize with a non-finite delta is a no-op (LOW b: NaN passed both clamps and zeroed two panes)", () => {
  const t: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }, { pane: "c" }], ratios: [0.5, 0.3, 0.2] };
  assert.deepEqual((resize(t, [], 0, NaN, 0.1) as Split).ratios, [0.5, 0.3, 0.2], "a NaN delta leaves the ratios unchanged");
  assert.deepEqual((resize(t, [], 0, 0.1, NaN) as Split).ratios, [0.5, 0.3, 0.2], "a NaN min leaves them unchanged too");
});

test("layout clamps rects to the box when a split is narrower than its gutters (LOW a)", () => {
  const t: Split = { dir: "row", kids: [{ pane: "a" }, { pane: "b" }, { pane: "c" }], ratios: [1 / 3, 1 / 3, 1 / 3] };
  const rects = layout(t, { x: 0, y: 0, w: 10, h: 20 }, 7);   // span 10-14 = negative; the old code put right edges at 0,7,14
  for (const { pane, rect } of rects) {
    assert.ok(rect.x >= -1e-9 && rect.x + rect.w <= 10 + 1e-9, pane + " stays within the 10px box: " + JSON.stringify(rect));
    assert.ok(rect.w >= -1e-9, pane + " has no negative width");
  }
});

test("parse normalises a stored ratio that sums under 1 so layout fills the box (LOW d: loose tolerance vs no re-normalise)", () => {
  const s = JSON.stringify({ v: 1, tree: { dir: "row", kids: [{ pane: "a" }, { pane: "b" }], ratios: [0.4995, 0.4996] }, parked: [] });
  const p = parse(s);
  assert.ok(p, "a ratio a sliver under 1 (within tolerance) still parses");
  const w = widthsOf(p!.tree, 1000, 100, 0);
  assert.ok(near(w.a + w.b, 1000, 1e-6), "the two panes fill the 1000px box exactly: " + JSON.stringify(w));
});
