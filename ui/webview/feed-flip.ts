// When does a feed render need its FLIP pass (the user 2026-06-27's flying cards)? Only when a card can have
// MOVED: one changed column, appeared, or left. A render where every card kept its column changes cards in
// place (text, tint, chips) and nothing glides — yet the FLIP pass cost two forced layouts of the whole
// document on every such frame (2026-09-04, measured on the shared main thread). Pure and DOM-free so the
// gate is unit-tested; feed.ts feeds it the column map of the previous and the next render.
export function flipNeeded(prev: ReadonlyMap<string, string>, next: ReadonlyMap<string, string>): boolean {
  if (prev.size === 0) return false;                 // first paint: nothing on screen to glide from
  if (prev.size !== next.size) return true;          // a card appeared or left → its neighbours shift
  for (const [k, col] of next) {
    const was = prev.get(k);
    if (was === undefined || was !== col) return true;
  }
  return false;
}
