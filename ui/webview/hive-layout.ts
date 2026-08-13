// Hive layout — the pure hex math (no DOM, no three imports; tested by hive-layout.test.ts).
// Pointy-top axial coordinates. Slot i counts a center-out spiral: slot 0 is the origin and
// ring k holds 6k slots, so the board grows evenly in every direction and a slot index alone
// fixes a pad's place. Sessions KEEP their slot for life (assignSlots) — the board never
// reshuffles on a push, only on a real arrival or departure (CLAUDE.md ## Design: a pad may
// move only on new information).

export interface Axial { q: number; r: number }

// Board scale. Neighbour centers sit √3·HEX_SIZE apart (axialToXZ); each pad's top is a hex
// prism of circumradius PAD_R with an EDGE turned toward every neighbour (PAD_THETA below),
// so its apothem is PAD_R·√3/2. PAD_R = HEX_SIZE makes twice the apothem exactly the
// neighbour gap: adjacent pads snap flush and share their edge, one connected honeycomb
// rather than islands (the user 2026-08-13, who wanted the hexagons snapped together). The
// pads' slight base flare (hive.ts ×1.04) tucks under the shared rim, hiding any seam.
export const HEX_SIZE = 2.05;
export const PAD_R = HEX_SIZE;

// Prism orientation — the turn that makes flush pads FIT instead of overlap (the user
// 2026-08-13: full-size pads first went in corner-first and interpenetrated). Three's
// CylinderGeometry walks θ as x = r·sinθ, z = r·cosθ, so a vertex lands at XZ angle 90°−θ:
// thetaStart 0 puts corners at 30°+k·60°, which puts the EDGES' midpoints at k·60° — the
// exact angles axialToXZ hands the six neighbours. (The old π/6 landed corners at k·60°,
// aiming a PAD_R-long corner straight at each neighbour, past the √3/2 apothem line.)
// Flat hexes (RingGeometry/CircleGeometry) walk θ as x = r·cosθ, y = r·sinθ and lie down
// via rotation.x = −π/2, landing a vertex at XZ angle −θ: −π/6 lines their corners up with
// the prism's. Both constants are pinned together by hive-layout.test.ts.
export const PAD_THETA = 0;
export const RIM_THETA = -Math.PI / 6;

// The six axial neighbour directions, in the ring-walk order the spiral uses.
const DIRS: readonly Axial[] = [
  { q: 1, r: 0 }, { q: 1, r: -1 }, { q: 0, r: -1 },
  { q: -1, r: 0 }, { q: -1, r: 1 }, { q: 0, r: 1 },
];

// Ring k spans spiral indices [3k(k-1)+1, 3k(k+1)]; walk starts at DIRS[4]·k (the south-west
// corner) and takes k steps along each of the six directions in order.
export function spiralSlot(i: number): Axial {
  if (!Number.isFinite(i) || i <= 0) return { q: 0, r: 0 };
  let k = 1;
  while (3 * k * (k + 1) < i) k++;
  const idx = i - (3 * k * (k - 1) + 1);
  let q = DIRS[4].q * k, r = DIRS[4].r * k;
  const side = Math.floor(idx / k), step = idx % k;
  for (let s = 0; s < side; s++) { q += DIRS[s].q * k; r += DIRS[s].r * k; }
  q += DIRS[side].q * step; r += DIRS[side].r * step;
  return { q, r };
}

// Pointy-top axial → world XZ. `size` is the hex circumradius (center to corner); adjacent
// pad centers land size·√3 apart.
export function axialToXZ(a: Axial, size: number): { x: number; z: number } {
  const s3 = Math.sqrt(3);
  return { x: size * (s3 * a.q + (s3 / 2) * a.r), z: size * 1.5 * a.r };
}

export function hexDistance(a: Axial, b: Axial): number {
  const dq = a.q - b.q, dr = a.r - b.r;
  return (Math.abs(dq) + Math.abs(dr) + Math.abs(dq + dr)) / 2;
}

// World XZ → the axial cell containing it: the inverse of axialToXZ, cube-rounded so a
// point anywhere inside a hex maps to that hex (nearest-center in hex metric).
export function xzToAxial(x: number, z: number, size: number): Axial {
  const qf = ((Math.sqrt(3) / 3) * x - z / 3) / size;
  const rf = (2 / 3) * z / size;
  const sf = -qf - rf;
  let q = Math.round(qf), r = Math.round(rf);
  const s = Math.round(sf);
  const dq = Math.abs(q - qf), dr = Math.abs(r - rf), ds = Math.abs(s - sf);
  if (dq > dr && dq > ds) q = -r - s;
  else if (dr > ds) r = -q - s;
  return { q: q + 0 === 0 ? 0 : q, r: r + 0 === 0 ? 0 : r };   // Math.round(-0.2) is -0
}

// Spiral index of an axial cell — the inverse of spiralSlot. Walks the cell's own ring
// (6k cells, k small on any real board), so no closed-form arithmetic to get subtly wrong.
export function slotOfAxial(a: Axial): number {
  const k = hexDistance(a, { q: 0, r: 0 });
  if (k === 0) return 0;
  for (let i = 3 * k * (k - 1) + 1; i <= 3 * k * (k + 1); i++) {
    const s = spiralSlot(i);
    if (s.q === a.q && s.r === a.r) return i;
  }
  return -1;                                   // unreachable for a well-formed axial
}

// Ring number of a spiral slot — the inverse of spiralSlot's ring arithmetic.
export function ringOf(i: number): number {
  if (!Number.isFinite(i) || i <= 0) return 0;
  let k = 1;
  while (3 * k * (k + 1) < i) k++;
  return k;
}

// Corner k (0..5) of the hex at center (cx, cz) with circumradius r — the ONE place corner
// positions are computed, in the same parametrization CylinderGeometry uses for the pad
// prism (x = r·sinθ, z = r·cosθ, θ from PAD_THETA): the lattice below, hive.ts's line
// loops, and the prism all agree on where a corner is by construction.
export function hexCorner(cx: number, cz: number, r: number, k: number): { x: number; z: number } {
  const th = PAD_THETA + (k * Math.PI) / 3;
  return { x: cx + r * Math.sin(th), z: cz + r * Math.cos(th) };
}

// The one-layer board: every UNIQUE edge of the honeycomb covering rings 0..`rings`, flat
// [ax, az, bx, bz, …] ready for a LineSegments buffer. The cells tessellate (PAD_R =
// HEX_SIZE), so an interior edge belongs to exactly two cells — it is emitted ONCE, keyed
// by its quantized endpoints, so every line in the lattice draws at the same weight
// (the user 2026-08-13: one thin line, mathematically even — never doubled where cells
// meet, never a second grid underneath).
export function latticeSegments(rings: number, size: number): number[] {
  const seen = new Set<string>();
  const out: number[] = [];
  const q = (v: number) => Math.round(v * 1e5);
  const last = 3 * Math.max(0, rings) * (Math.max(0, rings) + 1);
  for (let i = 0; i <= last; i++) {
    const c = axialToXZ(spiralSlot(i), size);
    for (let e = 0; e < 6; e++) {
      const a = hexCorner(c.x, c.z, size, e), b = hexCorner(c.x, c.z, size, (e + 1) % 6);
      const ka = q(a.x) + "," + q(a.z), kb = q(b.x) + "," + q(b.z);
      const key = ka < kb ? ka + "|" + kb : kb + "|" + ka;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(a.x, a.z, b.x, b.z);
    }
  }
  return out;
}

// Stable slot assignment. `prev` is the last known sid→slot map (persisted by the caller);
// `sids` the sessions present NOW, in arrival order. Every sid that had a slot keeps it
// (first claim wins on a corrupt duplicate); new sids fill the lowest free slots in order.
// Dropping a sid from `sids` frees its slot for FUTURE arrivals only — nothing already
// placed ever moves.
export function assignSlots(prev: ReadonlyMap<string, number>, sids: readonly string[]): Map<string, number> {
  const out = new Map<string, number>();
  const used = new Set<number>();
  for (const sid of sids) {
    const s = prev.get(sid);
    if (s !== undefined && Number.isInteger(s) && s >= 0 && !used.has(s)) { out.set(sid, s); used.add(s); }
  }
  let next = 0;
  for (const sid of sids) {
    if (out.has(sid)) continue;
    while (used.has(next)) next++;
    out.set(sid, next); used.add(next);
  }
  return out;
}

// Frame delta from two rAF-ish timestamps, in seconds — SAFE against every clock the
// browser can throw: a negative step (rAF timeline vs performance.now skew, VM clock
// adjustments, headless virtual time) falls back to one nominal frame instead of going
// negative — a negative dt would flip every exponential ease into a runaway AWAY from its
// target (the 2026-08-13 camera-divergence bug: distCur 10.5 → 426 in four seconds). A
// huge step (tab restored after minutes) caps at 50ms so nothing teleports.
export function frameDt(nowMs: number, lastMs: number): number {
  if (!Number.isFinite(nowMs) || !Number.isFinite(lastMs) || lastMs < 0) return 1 / 60;
  const dt = (nowMs - lastMs) / 1000;
  if (dt <= 0) return 1 / 60;
  return Math.min(0.05, dt);
}

// World-space radius of the occupied board (for camera framing): the farthest pad center
// from the origin, plus one pad of margin so the frame never crops a rim.
export function frameRadius(slots: readonly number[], size: number): number {
  let r = 0;
  for (const i of slots) {
    const { x, z } = axialToXZ(spiralSlot(i), size);
    r = Math.max(r, Math.hypot(x, z));
  }
  return r + size * 2;
}
