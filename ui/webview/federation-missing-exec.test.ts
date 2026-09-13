// EXECUTED pins for the guards a browser story cannot reach on its own: with the manager missing every tab refuses
// its drag at dragstart, so no real drop ever calls reorderTo, and commitTabOrder is only ever called from reorderTo —
// a served test that drags in that state exercises the refusal, not these two lines. Lifted from render.ts the way
// tab-strip-skip-exec.test.ts lifts renderTabs; the review's mutation pass (2026-09-10) found both guards uncovered.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

type Lifted = { commitTabOrder: () => void; reorderTo: (a: string, b: string, after: boolean) => boolean; order: () => string[]; writes: string[][]; renders: () => number };

function lift(fedMissing: boolean, locked = false): Lifted {
  const a = RENDER.indexOf("function commitTabOrder() {"), b = RENDER.indexOf("\n}\n", a) + 3;
  const c = RENDER.indexOf("function reorderTo(dragId: string, targetId: string, after: boolean): boolean {"), d = RENDER.indexOf("\n}\n", c) + 3;
  assert.ok(a > 0 && b > a && c > b && d > c, "anchors not found — commitTabOrder / reorderTo moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(a, b) + RENDER.slice(c, d), { loader: "ts" }).code;
  const prelude = `
    let order = ["a", "b", "c"]; let tabDragJustCommitted = false; let renders = 0;
    const writes = []; const writeViewOrder = (o) => { writes.push(o.slice()); };
    const renderTabs = () => { renders++; };
    const fedMissing = FED;
    const settings = { tabsLocked: LOCKED };   // the tab lock (T395): a drop after another window locked mid-drag commits nothing
  `;
  const epilogue = `return { commitTabOrder, reorderTo, order: () => order.slice(), writes, renders: () => renders };`;
  return new Function("FED", "LOCKED", prelude + js + epilogue)(fedMissing, locked) as Lifted;
}

test("with the manager present a drop reorders, writes the arrangement once and says it reordered (the lift is live)", () => {
  const p = lift(false);
  assert.equal(p.reorderTo("a", "b", true), true);
  assert.deepEqual(p.order(), ["b", "a", "c"]);
  assert.deepEqual(p.writes, [["b", "a", "c"]]);
  assert.equal(p.renders(), 1);
  assert.equal(p.reorderTo("zz", "b", true), false, "an id the order lacks: nothing to move, no commit");
  assert.deepEqual(p.writes.length, 1);
});

test("with the manager MISSING a drop moves nothing, writes nothing and says so — and the writer refuses even a direct call", () => {
  const p = lift(true);
  assert.equal(p.reorderTo("a", "b", true), false, "the drop is reported as NOT committed, so dragend takes the cancel path and FLIPs the strip home");
  assert.deepEqual(p.order(), ["a", "b", "c"], "reorderTo stood down: the strip's order is the kernel's seed, untouched");
  assert.equal(p.renders(), 0);
  p.commitTabOrder();
  assert.deepEqual(p.writes, [], "commitTabOrder stood down on its own: no arrangement written from a page without its manager");
});

test("with the tabs LOCKED (another window's press landed mid-drag) a drop moves nothing and writes nothing (T395 round one, LOW 3)", () => {
  const p = lift(false, true);
  assert.equal(p.reorderTo("a", "b", true), false, "not a committed drag");
  assert.deepEqual(p.order(), ["a", "b", "c"]);
  assert.deepEqual(p.writes, []);
  assert.equal(p.renders(), 0);
});
