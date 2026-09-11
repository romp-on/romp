// Pinned tabs (the user 2026-09-10): a pinned session's tab stays at its SLOT — the index it had on the strip when it
// was pinned — through everything that rewrites the order: a drag of another tab flows around it, and a kernel push,
// an arrangement merge or a reload that would have moved it is corrected before the strip paints (the user, later
// that day: whatever else reshuffles, a pinned tab must not move). It cannot be dragged itself, and it says so with a
// folded corner (the dog-ear render.ts paints). Per browser, like the tab order it holds a place in (view-order.ts):
// pinning is about how YOU have arranged your strip. Not the tab-groups "Show when folded" pin (tab-groups.ts
// PinnedRef), which keeps a member visible under its folded section and says nothing about position. Pure and
// DOM-free (storage passed in) so node executes the rules.

export const TABPINS_KEY = "romp:tabpins";
export const TABPINS_EVENT = "romp-tabpins";   // window event raised on every write (same-document; siblings get `storage`)

/** sid → the slot it holds (its index in the order). A negative slot means "wherever it is now": the first
 *  placement adopts the current index and writes it down (the store's first shape, a bare list, reads this way). */
export type TabPins = Map<string, number>;

type StorageLike = { getItem(k: string): string | null; setItem(k: string, v: string): void };

export function loadTabPins(storage: StorageLike): TabPins {
  const out: TabPins = new Map();
  try {
    const d = JSON.parse(storage.getItem(TABPINS_KEY) || "{}");
    if (Array.isArray(d)) { for (const x of d) if (typeof x === "string" && x) out.set(x, -1); }
    else if (d && typeof d === "object") for (const k of Object.keys(d)) { const v = d[k]; if (k && typeof v === "number" && Number.isFinite(v)) out.set(k, Math.max(-1, Math.floor(v))); }
  } catch (e) { /* corrupt: no pins, never a throw */ }
  return out;
}

export function writeTabPins(storage: StorageLike, pins: ReadonlyMap<string, number>): void {
  const blob: Record<string, number> = {};
  for (const [sid, at] of pins) blob[sid] = at;
  try { storage.setItem(TABPINS_KEY, JSON.stringify(blob)); } catch (e) { /* storage full/blocked */ }
  try { window.dispatchEvent(new Event(TABPINS_EVENT)); } catch (e) { /* non-DOM (tests) */ }
}

/** Pin one session at `at` (its current index on the strip), or unpin it; returns the set as stored. */
export function setTabPinned(storage: StorageLike, sid: string, on: boolean, at = -1): TabPins {
  const pins = loadTabPins(storage);
  if (on) pins.set(sid, at); else pins.delete(sid);
  writeTabPins(storage, pins);
  return pins;
}

/** The order with every pinned id at its slot: pinned ids the order carries are taken out and put back at their
 *  recorded index (a negative one: the index they have in `order` now), lowest slot first, the other ids keeping
 *  their relative order around them. A slot past the end trails at the end. A pinned id the order does not carry
 *  is not conjured back. */
export function placePinned(order: readonly string[], pins: ReadonlyMap<string, number>): string[] {
  const held = Array.from(pins)
    .filter(([sid]) => order.includes(sid))
    .map(([sid, at]) => [sid, at < 0 ? order.indexOf(sid) : at] as [string, number])
    .sort((a, b) => a[1] - b[1] || order.indexOf(a[0]) - order.indexOf(b[0]));
  if (!held.length) return order.slice();
  const heldIds = new Set(held.map(([sid]) => sid));
  const rest = order.filter((id) => !heldIds.has(id));
  const out: string[] = [];
  let h = 0, r = 0;
  while (h < held.length || r < rest.length) {
    if (h < held.length && (held[h][1] <= out.length || r >= rest.length)) out.push(held[h++][0]);
    else out.push(rest[r++]);
  }
  return out;
}

/** Pins whose slot is still "wherever it is now" (a negative slot), resolved against the order: the map to write,
 *  or null when every pin already has its slot. */
export function adoptPinSlots(pins: ReadonlyMap<string, number>, order: readonly string[]): TabPins | null {
  let changed = false;
  const out: TabPins = new Map();
  for (const [sid, at] of pins) {
    const i = order.indexOf(sid);
    if (at < 0 && i >= 0) { out.set(sid, i); changed = true; } else out.set(sid, at);
  }
  return changed ? out : null;
}

/** The pins whose session the strip still carries — none dropped while the strip is empty (a kernel not yet heard
 *  from is not a strip with nothing on it). Returns null when nothing changed. */
export function prunePins(pins: ReadonlyMap<string, number>, order: readonly string[]): TabPins | null {
  if (!order.length) return null;
  const kept: TabPins = new Map(Array.from(pins).filter(([sid]) => order.includes(sid)));
  return kept.size === pins.size ? null : kept;
}
