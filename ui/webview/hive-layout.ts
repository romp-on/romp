// Hive layout — the pure hex math (no DOM, no three imports; tested by hive-layout.test.ts).
// Pointy-top axial coordinates. Slot i counts a center-out spiral: slot 0 is the origin and
// ring k holds 6k slots, so the board grows evenly in every direction and a slot index alone
// fixes a pad's place. Sessions KEEP their slot for life (assignSlots) — the board never
// reshuffles on a push, only on a real arrival or departure (CLAUDE.md ## Design: a pad may
// move only on new information).

export interface Axial { q: number; r: number }

// Board scale. Neighbour centers sit √3·HEX_SIZE apart (axialToXZ); each pad's top is a hex
// prism of circumradius PAD_R with an EDGE turned toward every neighbour (hive.ts thetaStart
// π/6), so its apothem is PAD_R·√3/2. PAD_R = HEX_SIZE makes twice the apothem exactly the
// neighbour gap: adjacent pads snap flush and share their edge, one connected honeycomb
// rather than islands (the user 2026-08-13, who wanted the hexagons snapped together). The
// pads' slight base flare (hive.ts ×1.04) tucks under the shared rim, hiding any seam.
export const HEX_SIZE = 2.05;
export const PAD_R = HEX_SIZE;

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
