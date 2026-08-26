// Session views (the user 2026-08-18; TAG model 2026-08-23; ALL default 2026-08-24; the hidden set
// RETIRED outright 2026-08-24 — the user: the tag system covers backgrounding, and the kernel
// migrated existing hidden entries into an "archived" tag once): the kernel's timeline-views blob,
// echoed on every tabOrder push — which sessions the chat TAB STRIP (and the timeline lanes) show.
// TWO built-in sentinels, not tags: "all" — the DEFAULT — shows LITERALLY EVERYTHING (that is
// All's meaning now; nothing can hide from it); "untagged" keeps the old default's meaning under
// its own honest name — a TAG marks a SPECIALIZED session, excluded from the untagged view and
// shown under its tag views. A tag view shows exactly its members. The kernel's _view_visible is
// the decision of record; this is its client mirror (the timeline carries its own copy — it cannot
// import TS).
// Pure, split out of render.ts for tests (the time-marker.ts pattern). The kernel emits `tags`;
// `groups` survives in the type as the pre-rename key an un-updated kernel still pushes.
export interface SessionTag { id: string; name?: string; color?: string; members?: string[]; host?: string }
export interface SessionViews {
  active?: string; tags?: SessionTag[]; groups?: SessionTag[];
  hidden?: string[];   // RETIRED 2026-08-24 — read-tolerated on old blobs, ignored everywhere, kernel-dropped
  // tag federation v0 (the user 2026-08-24): each ATTACHED kernel's own tags, read-only, joined by
  // the kernel per host (id = "host:tagid", members already respelled viewer-relative). Derived —
  // never an edit target, excluded from the echo key, dropped by the kernel if echoed back.
  remoteTags?: SessionTag[];
  /** per-surface multi-select lenses (the user 2026-08-25) — the shared TagLens shape; the legacy
   *  scalar `active` stays for old clients and seeds these on migration (kernel-normalized) */
  actives?: { [surface: string]: { all?: boolean; none?: boolean; tags?: string[] } };
  /** the union DISPLAY order (the user 2026-08-25): tag NAMES in the user's dragged order,
   *  viewer-side so remote-homed names hold position without cross-kernel writes; locals also
   *  keep array order (the drag rewrites both). Unlisted names follow, in natural order. */
  tagOrder?: string[];
}
// One union group = one tag NAME across every kernel defining it (user ruling 2026-08-24: kernels
// are plumbing — no host prefixes in any tag presentation). The typed mirror of the timeline's
// viewTagUnion over the same client blob (local tags + remoteTags); the local store's colour wins.
// The chat's tab-menu Tags section reads and edits through this exact shape.
export interface TagUnion { name: string; color: string; members: string[]; ids: string[]; localId: string | null; remotes: SessionTag[] }
export function viewTagUnion(views: SessionViews | null | undefined): TagUnion[] {
  // byName is null-prototype: a user-typed tag NAME can be "constructor"/"toString", which a
  // plain {} resolves through the prototype chain (the lookup returned a Function and the
  // builder crashed on it — adversarial review 2026-08-25)
  const out: TagUnion[] = [], byName: Record<string, TagUnion> = Object.create(null);
  for (const t of viewTags(views)) {
    const key = t.name || "tag";
    const g = byName[key] || (byName[key] = { name: key, color: "", members: [], ids: [], localId: null, remotes: [] });
    if (!g.localId) { g.localId = t.id; g.color = t.color || g.color; }
    g.ids.push(t.id);
    for (const m of (t.members || [])) if (!g.members.includes(m)) g.members.push(m);
    if (!out.includes(g)) out.push(g);
  }
  for (const rt of (views?.remoteTags || [])) {
    const key = rt.name || "tag";
    const g = byName[key] || (byName[key] = { name: key, color: "", members: [], ids: [], localId: null, remotes: [] });
    if (!g.localId && !g.color) g.color = rt.color || "";
    g.ids.push(rt.id);
    g.remotes.push(rt);
    for (const m of (rt.members || [])) if (!g.members.includes(m)) g.members.push(m);
    if (!out.includes(g)) out.push(g);
  }
  return orderUnions(out, views);
}

/** THE ordering rule, stated once (the user 2026-08-25): unions render in the user's dragged
 *  order (views.tagOrder, name-keyed — remote-homed names hold position too); names not yet
 *  ordered follow in their natural (array/arrival) order — sort() is stable, so a new tag lands
 *  at the end without disturbing anything. Every chip list, menu row list, and dialog row list
 *  renders through viewTagUnion, so the order flows everywhere from here. */
function orderUnions(out: TagUnion[], views: SessionViews | null | undefined): TagUnion[] {
  const ord = views?.tagOrder || [];
  if (!ord.length) return out;
  const ix = new Map(ord.map((n, i) => [n, i]));
  return out.sort((a, b) => (ix.has(a.name) ? ix.get(a.name)! : ord.length) - (ix.has(b.name) ? ix.get(b.name)! : ord.length));
}

// the one place the legacy key is honored, so every rule below reads through it
export function viewTags(views: SessionViews | null | undefined): SessionTag[] {
  return (views && (views.tags || views.groups)) || [];
}

export function viewVisible(views: SessionViews | null | undefined, id: string): boolean {
  if (!views || !views.active || views.active === "all") {
    return true;                                     // All = literally everything (hidden retired 2026-08-24)
  }
  if (views.active === "untagged") {
    // the UNION excludes (the user 2026-08-24): a session held by ANY kernel's tag is tagged —
    // a remote-homed tag pulls it out of untagged exactly like a local one
    return !viewTags(views).concat(views.remoteTags || []).some((t) => (t.members || []).includes(id));
  }
  // a tag view shows the NAME-KEYED UNION (user ruling 2026-08-24: a tag is its NAME; kernels are
  // plumbing) — whichever store's id is active, membership joins every same-name tag's
  const act = viewTags(views).find((x) => x.id === views.active)
    || (views.remoteTags || []).find((x) => x.id === views.active);
  if (!act) return true;
  const same = viewTags(views).concat(views.remoteTags || []).filter((x) => x.name === act.name);
  return same.some((t) => (t.members || []).includes(id));
}

// one canonical serialization for echo comparison — the kernel normalizer re-sorts lists and may
// clamp names, so optimistic edits compare by shape, never by identity
export function viewsKey(v: SessionViews | null | undefined): string {
  if (!v) return "";
  return JSON.stringify({ active: v.active || "all",
    actives: v.actives || {},   // per-surface lenses are client-posted state — the echo compares them (2026-08-25)
    order: v.tagOrder || [],    // the dragged union order is client-posted state too (2026-08-25)
    tags: viewTags(v).map((t) => ({ id: t.id, name: t.name, color: t.color,
                                    members: (t.members || []).slice().sort() })) });
}

// (hideIn RETIRED with the hidden set, the user 2026-08-24 — the tag system covers backgrounding.)
// the reveal gesture, post-retirement: explicitly opening a session lands on a visible view with
// the MINIMAL move and never mutates membership — a tagged session's home is its first holder tag;
// anything else is already visible under All, so switch there only when actually invisible.
export function revealIn(views: SessionViews | null | undefined, id: string): SessionViews {
  const v: SessionViews = JSON.parse(JSON.stringify(views || {}));
  // the reveal keys on the CHAT surface's lens (per-surface selections, the user 2026-08-25): the
  // MINIMAL move is ADDING the session's first holder tag to the selection — additive, never a
  // switch that hides what the user was looking at; a session in no tag home adds the none bucket.
  const unions = viewTagUnion(v);
  const lens = (v.actives || {})["chat"] || (v.active === "untagged" ? { none: true }
    : v.active && v.active !== "all" ? (() => { const g = unions.find((x) => x.ids.includes(v.active!)); return g ? { tags: [g.name] } : { all: true }; })()
    : { all: true });
  const visible = !lens || lens.all ? true
    : (lens.none && !unions.some((u) => u.members.includes(id)))
      || unions.some((u) => (lens.tags || []).includes(u.name) && u.members.includes(id));
  if (visible) return v;
  const holder = unions.find((u) => u.members.includes(id));
  const next: { none?: boolean; tags?: string[] } = {};
  if (holder) {
    if (lens.none) next.none = true;
    next.tags = [...(lens.tags || []), holder.name];
  } else {
    next.none = true;
    if (lens.tags && lens.tags.length) next.tags = lens.tags;
  }
  v.actives = Object.assign({}, v.actives, { chat: next });
  return v;
}
