// The feed's SEARCH filter (the user 2026-08-23): a footer search box that live-filters the board to
// sessions whose NAME matches the typed text — host prefix included, so "snape" finds every session on
// that machine and "snape:api" narrows to one. Compact by default (a "Search" button, the footer's
// word-button vocabulary), expanding to an inline input on click — progressive disclosure, like every
// feed surface. Pure matching lives here so node --test executes the rule without a DOM.

/** Case-insensitive SUBSTRING match over the session's full display name (host prefix included).
 *  An empty/blank query matches everything — the filter off. */
export function searchMatches(query: string | null | undefined, name: string | null | undefined): boolean {
  const q = (query || "").trim().toLowerCase();
  if (!q) return true;
  return (name || "").toLowerCase().includes(q);
}

/** The sids whose session name matches — the set the render's `shown` pass filters by. Cards also
 *  carry their own per-card name; the caller ORs both so a card whose session fell out of the meta
 *  list (a just-died session's last cards) still matches by its own label. */
export function searchSids(query: string | null | undefined,
                           metas: { sid: string; name: string }[]): Set<string> | null {
  const q = (query || "").trim();
  if (!q) return null;                               // null = no filter (distinct from "matched none")
  return new Set(metas.filter((m) => searchMatches(q, m.name)).map((m) => m.sid));
}
