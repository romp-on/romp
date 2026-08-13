// Drag a session to the trash dock to end it (the user 2026-08-13, TFT-style): a held
// press that MOVES picks the bean up, the dock slides in from the bottom, dropping on the
// armed dock posts the kernel's own endSession op — and a drop anywhere else springs them
// home, no harm done. The deliberate carry + the highlighted dock is the confirmation (an
// ended session still revives with its history). Source-pinned like the pane's other
// interaction contracts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const HIVE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "hive-pane.css"), "utf8");
const drop = HIVE.slice(HIVE.indexOf("private dropSessionDrag"), HIVE.indexOf("private static GHOST"));

test("a pick-up is gated on the GESTURE: a held press must move before it becomes a drag", () => {
  assert.match(HIVE, /this\.pressedPad && !this\.dragSession\s*\n\s*&& Math\.hypot\(e\.clientX - this\.pressedPad\.x, e\.clientY - this\.pressedPad\.y\) > 6/,
    "same px gate as the recruit click — never a timer");
  assert.match(HIVE, /if \(this\.dragSession\) \{ this\.moveSessionDrag\(e\); return; \}/,
    "a carry never orbits the camera");
  assert.match(HIVE, /this\.pressedPad = \{ sid, x: e\.clientX, y: e\.clientY, bean: hit\.bean \};/,
    "the press arms the pick-up; the tile's click resolves on the clean UP (the bean's is instant on the down)");
});

test("dropping on the armed dock ends the session via the kernel's own op", () => {
  assert.match(drop, /\{ type: "endSession", id: d\.sid \}/, "the same op the chat tab's × posts");
  assert.ok(KERNEL.includes('elif t == "endSession":'), "the kernel still handles it");
  assert.match(drop, /pad\.consumeBean\(\);/, "the bean vanishes into the dock");
  assert.match(drop, /pad\.dyingT = 0\.26;/, "straight to the sink — the hop is for natural exits");
  assert.match(drop, /particles\.burst/, "the drop is acknowledged where it happened");
});

test("dropping anywhere else springs them home — no kill outside the dock", () => {
  const misses = drop.slice(drop.indexOf("} else {"));
  assert.match(misses, /pad\.carryTo\(null\);/, "release → spring home");
  assert.ok(!misses.includes("endSession"), "no end op on a missed drop");
  assert.match(HIVE, /if \(this\.dragSession\) \{ this\.dropSessionDrag\(false\); return; \}/,
    "Esc aborts the carry the same way");
});

test("the dock arms only under the pointer, and shows WHO the drop would end", () => {
  assert.match(HIVE, /label\.textContent = "Drop to end " \+ pad\.sess\.name;/);
  assert.match(HIVE, /this\.trashEl\.classList\.toggle\("armed", over\);/);
  for (const frag of ["#hive-trash {", "#hive-trash.show {", "#hive-trash.armed {", "pointer-events: none;"])
    assert.ok(CSS.includes(frag), "hive-pane.css carries: " + frag);
});

test("the carry is pinned under the cursor EVERY FRAME, on a flat plane", () => {
  // Recomputed after the camera eases (frame()), never only per pointer event — a spring,
  // idle drift, or a still pointer can no longer pull the bean out from under the cursor
  // (the user 2026-08-13, who watched them drift apart). The flat CARRY_Y plane keeps the
  // cursor→bean mapping projectively exact anywhere on screen; fixed camera DEPTH did not.
  const frame = HIVE.slice(HIVE.indexOf("this.camera.lookAt(this.targetCur);"));
  assert.match(frame, /if \(this\.dragSession\) \{\s*\n\s*this\.idleT = 0;/,
    "holding someone is not idle — no board sway mid-carry");
  assert.match(frame, /const t = \(CARRY_Y - o\.y\) \/ v\.y;/, "plane intersection, not camera depth");
  assert.ok(!HIVE.includes("multiplyScalar(d.depth)"), "the depth-sphere carry is gone");
});

test("the carrier wraps the dweller, so a carry never fights the idle animation", () => {
  assert.match(HIVE, /this\.carrier\.add\(this\.guy\.group\);/);
  assert.match(HIVE, /this\.group\.add\(this\.carrier\);/);
  assert.match(HIVE, /this\.carrier\.position\.lerp\(this\.carryWant/, "eased, never teleported");
});
