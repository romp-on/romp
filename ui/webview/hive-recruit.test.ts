// Click ANY empty hexagon to spawn a session there (the user 2026-08-13). The ghost is the
// standing invitation: parked on the first free slot, gliding under the pointer over any
// empty cell; a clean click reserves that cell and opens the new-session picker, and the
// next NEW arrival takes the reserved cell. Source-pinned in the tab-rename-host style —
// these behaviors only meet the pure math (hive-layout) and the shell relay at runtime.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const HIVE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive.ts"), "utf8");
const pick = HIVE.slice(HIVE.indexOf("private pick()"), HIVE.indexOf("select(sid: string)"));
const up = HIVE.slice(HIVE.indexOf("private onPointerUp"), HIVE.indexOf("private static GHOST"));
const sync = HIVE.slice(HIVE.indexOf("sync(sessions:"), HIVE.indexOf("private ensureLattice"));

test("pick() sees every empty cell: ground-plane fallthrough via the layout inverses", () => {
  assert.match(pick, /xzToAxial\(px, pz, HEX_SIZE\)/, "world point → cell");
  assert.match(pick, /slotOfAxial\(cell\)/, "cell → spiral slot");
  assert.match(pick, /!new Set\(this\.slots\.values\(\)\)\.has\(slot\)/, "only FREE cells invite");
  assert.match(pick, /hexDistance\(cell, \{ q: 0, r: 0 \}\) <= this\.latticeRings/,
    "only cells of the visible board — not the infinite plane");
});

test("recruit is a clean CLICK, decided on the up — the empty board still orbits", () => {
  assert.match(HIVE, /this\.pendingRecruit = \{ x: e\.clientX, y: e\.clientY, slot: this\.ghostSlot \};/);
  assert.match(HIVE, /this\.dragging = \{ mode: "orbit", x: e\.clientX, y: e\.clientY \};\s*\n\s*return;/,
    "the down starts the orbit too");
  assert.match(up, /Math\.hypot\(e\.clientX - pr\.x, e\.clientY - pr\.y\) > 5\) return;/,
    "the gate is the gesture (px travelled), never a timer");
  assert.match(up, /this\.reservedSlot = pr\.slot;/, "the click claims the cell");
  assert.match(up, /openPicker/, "…then asks the shell for the picker");
  assert.match(up, /particles\.burst/, "acknowledged immediately, before any round-trip");
});

test("sync() honors the reservation before pads are built; revived sessions outrank it", () => {
  assert.match(sync, /diff\.added\.find\(\(id\) => !stored\.has\(id\)\)/,
    "only a sid with NO remembered home takes the clicked cell — a revival returns to its hex");
  assert.match(sync, /this\.reservedSlot = null;/, "spent (or dropped) at the first arrival");
  assert.ok(sync.indexOf("reservedSlot") < sync.indexOf("new Pad("),
    "the override lands before any pad is constructed");
});

test("the ghost glides — follows the hovered empty cell, parks back on the first free slot", () => {
  assert.match(HIVE, /this\.ghost\.position\.lerp\(this\.ghostTarget/, "eased, never teleported");
  assert.match(HIVE, /if \(this\.hovered !== HiveWorld\.GHOST && this\.ghostSlot !== this\.ghostHome\) this\.ghostTo\(this\.ghostHome\);/,
    "pointer off the board → back to the park");
});
