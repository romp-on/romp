// Hive layout invariants (plans/hive.md): the spiral covers the plane ring by ring with no
// holes and no reshuffles — a session's hex is stable for its whole life, so the user's
// spatial memory of the board keeps working across pushes and reloads.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { assignSlots, axialToXZ, frameDt, frameRadius, hexDistance, spiralSlot } from "./hive-layout";

test("frameDt never goes negative or huge — a bad clock can't diverge the eases", () => {
  assert.equal(frameDt(1016, 1000), 0.016, "a normal frame passes through");
  assert.equal(frameDt(1000, 5000), 1 / 60, "a BACKWARD step falls back to one frame, never negative");
  assert.equal(frameDt(1000, 1000), 1 / 60, "a zero step too (exp ease with dt=0 is a no-op otherwise)");
  assert.equal(frameDt(99999, 1000), 0.05, "a long stall caps — nothing teleports after a tab restore");
  assert.equal(frameDt(1016, -1), 1 / 60, "the loop-start sentinel takes the nominal frame");
  assert.equal(frameDt(NaN, 1000), 1 / 60, "non-finite clocks fall back safely");
});

test("slot 0 is the origin; ring k holds 6k slots at hex distance k", () => {
  assert.deepEqual(spiralSlot(0), { q: 0, r: 0 });
  const O = { q: 0, r: 0 };
  for (const [k, lo, hi] of [[1, 1, 6], [2, 7, 18], [3, 19, 36]] as const) {
    for (let i = lo; i <= hi; i++) {
      assert.equal(hexDistance(spiralSlot(i), O), k, `slot ${i} sits on ring ${k}`);
    }
  }
});

test("the first 169 slots are unique (rings 0..7 tile without collisions)", () => {
  const seen = new Set<string>();
  for (let i = 0; i < 169; i++) {
    const { q, r } = spiralSlot(i);
    const key = q + "," + r;
    assert.ok(!seen.has(key), `slot ${i} collides at ${key}`);
    seen.add(key);
  }
});

test("consecutive slots inside a ring are neighbours (the walk never jumps)", () => {
  for (const [lo, hi] of [[1, 6], [7, 18], [19, 36]] as const) {
    for (let i = lo; i < hi; i++) {
      assert.equal(hexDistance(spiralSlot(i), spiralSlot(i + 1)), 1, `slots ${i}→${i + 1} adjacent`);
    }
  }
});

test("pointy-top spacing: all six neighbours of the origin sit √3·size away", () => {
  const size = 2;
  const o = axialToXZ({ q: 0, r: 0 }, size);
  for (let i = 1; i <= 6; i++) {
    const p = axialToXZ(spiralSlot(i), size);
    const d = Math.hypot(p.x - o.x, p.z - o.z);
    assert.ok(Math.abs(d - Math.sqrt(3) * size) < 1e-9, `neighbour ${i} at ${d}`);
  }
});

test("assignSlots: everyone keeps their slot; new sids fill the lowest free holes", () => {
  const first = assignSlots(new Map(), ["a", "b", "c"]);
  assert.deepEqual([...first.values()], [0, 1, 2]);
  // b leaves, d+e arrive: a and c stay put, d takes b's freed slot 1, e takes 3
  const second = assignSlots(first, ["a", "c", "d", "e"]);
  assert.equal(second.get("a"), 0);
  assert.equal(second.get("c"), 2);
  assert.equal(second.get("d"), 1);
  assert.equal(second.get("e"), 3);
  // an identical roster re-push changes nothing at all (the no-reshuffle invariant)
  const third = assignSlots(second, ["a", "c", "d", "e"]);
  assert.deepEqual([...third.entries()], [...second.entries()]);
});

test("assignSlots: a corrupt duplicate slot resolves first-claim-wins, loser re-slots", () => {
  const prev = new Map([["a", 4], ["b", 4]]);
  const out = assignSlots(prev, ["a", "b"]);
  assert.equal(out.get("a"), 4, "first claimant keeps the slot");
  assert.equal(out.get("b"), 0, "the duplicate falls to the lowest free slot");
});

test("frameRadius grows with the occupied ring and never returns zero", () => {
  const size = 2;
  const r0 = frameRadius([0], size);
  const r1 = frameRadius([0, 1, 2, 3], size);
  const r2 = frameRadius([0, 7], size);
  assert.ok(r0 >= size * 2, "even a lone pad frames with margin");
  assert.ok(r1 > r0 && r2 > r1, "farther occupied slots widen the frame");
});
