// The SHARED tag lens (the user 2026-08-25): one multi-select model for every surface's view
// selection — chat tabs, timeline lanes, the outline pane, and the feed's local filter all mount
// this. All is exclusive; otherwise arbitrary combinations of no-tags + tags toggle, and
// visibility is the UNION over the selected buckets. Tags are addressed by NAME (the union is
// name-keyed — kernels are plumbing, the user's 2026-08-24 ruling). Pure on purpose: surfaces
// mount it, the kernel mirrors it. Model authored on the feed's branch (its feed-tags module,
// manager-sanctioned as the shared shape 2026-08-25) and promoted here verbatim.
import { SessionViews, TagUnion, viewTagUnion } from "./session-views";

export interface TagLens {
  all?: boolean;      // the exclusive default: every session's cards
  none?: boolean;     // the no-tags bucket (sessions in no tag home)
  tags?: string[];    // selected tag NAMES (union-keyed)
}

/** Nothing picked = All: the empty selection never means "matched none" (the search-filter rule). */
export function lensAll(l: TagLens | null | undefined): boolean {
  if (!l || l.all) return true;
  return !l.none && !(l.tags && l.tags.length);
}

/** Toggle one pick. All is EXCLUSIVE: picking it clears the rest; picking anything else leaves All.
 *  Toggling the last selection off returns to All — the lens never strands an empty selection. */
export function toggleLens(l: TagLens | null | undefined, pick: "all" | "none" | { tag: string }): TagLens {
  if (pick === "all") return { all: true };
  const cur: TagLens = lensAll(l) ? {} : { none: l!.none, tags: (l!.tags || []).slice() };
  if (pick === "none") cur.none = !cur.none;
  else {
    const t = cur.tags || (cur.tags = []);
    const i = t.indexOf(pick.tag);
    if (i >= 0) t.splice(i, 1); else t.push(pick.tag);
  }
  if (!cur.none && !(cur.tags && cur.tags.length)) return { all: true };
  return { none: cur.none || undefined, tags: cur.tags && cur.tags.length ? cur.tags : undefined };
}

/** Union visibility over the selected buckets: "none" admits a session in NO tag home; a selected
 *  tag admits its name-keyed union's members. All admits everything. */
export function lensVisible(l: TagLens | null | undefined, unions: TagUnion[], sid: string): boolean {
  if (lensAll(l)) return true;
  const inAnyHome = unions.some((u) => u.members.includes(sid));
  if (l!.none && !inAnyHome) return true;
  const picked = l!.tags || [];
  return unions.some((u) => picked.includes(u.name) && u.members.includes(sid));
}

/** The lens described in words, for the disclosure line: "no tags", "infra", "infra + no tags". */
export function lensLabel(l: TagLens | null | undefined): string {
  if (lensAll(l)) return "All";
  const parts = (l!.tags || []).slice();
  if (l!.none) parts.push("no tags");
  return parts.join(" + ") || "All";
}

/** Canonical serialization for persistence and change compares. */
export function lensKey(l: TagLens | null | undefined): string {
  if (lensAll(l)) return "all";
  return JSON.stringify({ none: !!l!.none, tags: (l!.tags || []).slice().sort() });
}

/** The tag rows the menu offers: the name-keyed unions of the payload's views blob. */
export function lensUnions(views: SessionViews | null | undefined): TagUnion[] {
  return viewTagUnion(views);
}

/** One surface's lens off the views blob. A pre-lens blob (an older kernel, a client-held legacy
 *  shape) derives its lens from the legacy scalar EXACTLY as the kernel normalizer seeds it —
 *  behavior migrates, not just shape (an untagged view keeps excluding rather than falling open). */
export function surfaceLens(views: SessionViews | null | undefined, surface: string): TagLens {
  const l = views?.actives?.[surface];
  if (l) return l;
  const a = views?.active;
  if (!a || a === "all") return { all: true };
  if (a === "untagged") return { none: true };
  const g = viewTagUnion(views).find((x) => x.ids.includes(a));
  return g ? { tags: [g.name] } : { all: true };
}

/** The selection as display chips (the user 2026-08-25 convention: a narrowed button shows the
 *  chips of everything selected — each tag in its color, the no-tags bucket as its own chip).
 *  All → empty (the resting button stands alone). Per-selection chips, superseding the earlier
 *  combined-label call (that documented design choice retargeted by this ruling). */
export function lensChips(l: TagLens | null | undefined, unions: TagUnion[]): { label: string; color: string | null; pick: "none" | { tag: string } }[] {
  if (lensAll(l)) return [];
  const out: { label: string; color: string | null; pick: "none" | { tag: string } }[] = [];
  // "no tags" sits LEFTMOST in every selection render, the tags following in the USER'S order —
  // the unions arrive ordered (viewTagUnion's one ordering rule), so emitting selected names by
  // walking the unions IS the order (the user 2026-08-25, superseding the chips-then-none-last
  // form). A selected name missing from the unions (a stale lens) appends last, dressless.
  if (l!.none) out.push({ label: "no tags", color: null, pick: "none" });
  const picked = new Set(l!.tags || []);
  for (const u of unions) {
    if (!picked.has(u.name)) continue;
    picked.delete(u.name);
    out.push({ label: u.name, color: u.color || null, pick: { tag: u.name } });
  }
  for (const name of (l!.tags || [])) if (picked.has(name)) out.push({ label: name, color: null, pick: { tag: name } });
  return out;
}

