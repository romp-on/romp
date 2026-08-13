// Hive layout invariants (plans/hive.md): the spiral covers the plane ring by ring with no
// holes and no reshuffles — a session's hex is stable for its whole life, so the user's
// spatial memory of the board keeps working across pushes and reloads.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { assignSlots, axialToXZ, frameDt, frameRadius, HEX_SIZE, hexCorner, hexDistance, latticeSegments, PAD_R, PAD_THETA, RIM_THETA, ringOf, spiralSlot } from "./hive-layout";

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

test("pads snap flush: twice the pad apothem exactly spans the gap to every neighbour", () => {
  // The prism turns an edge toward each neighbour, so flush = apothem·2 === center distance.
  // Shrinking PAD_R below HEX_SIZE reopens the moat between cells; growing it overlaps them.
  const o = axialToXZ({ q: 0, r: 0 }, HEX_SIZE);
  const apothem = (PAD_R * Math.sqrt(3)) / 2;
  for (let i = 1; i <= 6; i++) {
    const p = axialToXZ(spiralSlot(i), HEX_SIZE);
    const d = Math.hypot(p.x - o.x, p.z - o.z);
    assert.ok(Math.abs(2 * apothem - d) < 1e-9, `neighbour ${i} leaves a ${d - 2 * apothem} gap`);
  }
});

// The prism's corners, as CylinderGeometry actually places them: x = r·sinθ, z = r·cosθ.
function padCorners(c: { x: number; z: number }): { x: number; z: number }[] {
  return Array.from({ length: 6 }, (_, k) => {
    const th = PAD_THETA + (k * Math.PI) / 3;
    return { x: c.x + PAD_R * Math.sin(th), z: c.z + PAD_R * Math.cos(th) };
  });
}

test("pads FIT, not overlap: adjacent hexes share exactly two corners (a whole edge)", () => {
  // Two equal regular hexagons sharing two adjacent corners share that edge and cannot
  // interpenetrate. thetaStart π/6 (the 2026-08-13 overlap) shares ZERO corners here —
  // it aims a corner at each neighbour, past the apothem line into the neighbouring cell.
  const o = padCorners(axialToXZ({ q: 0, r: 0 }, HEX_SIZE));
  for (let i = 1; i <= 6; i++) {
    const n = padCorners(axialToXZ(spiralSlot(i), HEX_SIZE));
    let shared = 0;
    for (const a of o) for (const b of n) if (Math.hypot(a.x - b.x, a.z - b.z) < 1e-9) shared++;
    assert.equal(shared, 2, `neighbour ${i} shares ${shared} corners`);
  }
});

test("hexCorner IS the cylinder parametrization (the corners everything shares)", () => {
  for (let k = 0; k < 6; k++) {
    const th = PAD_THETA + (k * Math.PI) / 3;
    const c = hexCorner(3, -2, PAD_R, k);
    assert.ok(Math.abs(c.x - (3 + PAD_R * Math.sin(th))) < 1e-12);
    assert.ok(Math.abs(c.z - (-2 + PAD_R * Math.cos(th))) < 1e-12);
  }
});

test("ringOf inverts the spiral: it agrees with hex distance from the origin", () => {
  const O = { q: 0, r: 0 };
  for (let i = 0; i < 169; i++) assert.equal(ringOf(i), hexDistance(spiralSlot(i), O), `slot ${i}`);
});

test("lattice: shared edges are emitted once, so every line weighs the same", () => {
  // ring 0 is one hex: 6 edges. rings 0..1 are 7 cells: 42 edge incidences, of which the
  // 6 center–ring and 6 ring–ring adjacencies are shared pairs → 42 − 12 = 30 unique.
  assert.equal(latticeSegments(0, HEX_SIZE).length / 4, 6);
  assert.equal(latticeSegments(1, HEX_SIZE).length / 4, 30);
});

test("lattice: every segment is exactly one hex side long (side = circumradius)", () => {
  const seg = latticeSegments(2, HEX_SIZE);
  for (let i = 0; i < seg.length; i += 4) {
    const d = Math.hypot(seg[i + 2] - seg[i], seg[i + 3] - seg[i + 1]);
    assert.ok(Math.abs(d - HEX_SIZE) < 1e-9, `segment ${i / 4} has length ${d}`);
  }
});

test("lattice: no duplicate segments in either direction", () => {
  const seg = latticeSegments(3, HEX_SIZE);
  const seen = new Set<string>();
  const q = (v: number) => Math.round(v * 1e5);
  for (let i = 0; i < seg.length; i += 4) {
    const a = q(seg[i]) + "," + q(seg[i + 1]), b = q(seg[i + 2]) + "," + q(seg[i + 3]);
    const key = a < b ? a + "|" + b : b + "|" + a;
    assert.ok(!seen.has(key), `segment ${i / 4} repeats ${key}`);
    seen.add(key);
  }
});

test("rim corners land on prism corners (Ring and Cylinder walk θ from different axes)", () => {
  // RingGeometry places x = r·cosθ, y = r·sinθ; rotation.x = −π/2 maps (x, y) → (x, −z).
  const prism = padCorners({ x: 0, z: 0 });
  for (let k = 0; k < 6; k++) {
    const th = RIM_THETA + (k * Math.PI) / 3;
    const rim = { x: PAD_R * Math.cos(th), z: -PAD_R * Math.sin(th) };
    const hit = prism.some((c) => Math.hypot(c.x - rim.x, c.z - rim.z) < 1e-9);
    assert.ok(hit, `rim corner ${k} sits off the prism's corner grid`);
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
