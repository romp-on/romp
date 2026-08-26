// Chat tab label + color follow the kernel's recurring tabOrder push (the user 2026-08-24, who ran
// `romp rename` / `romp color` headless and watched the strip hold the old label until reload): the
// push's per-tab meta (name + identity color) is rebuilt by the kernel every cycle from the names
// registry — the AUTHORITATIVE store every writer hits (a WS op, a headless POST /rename | /color, a
// remote kernel) — while per-session frames can ride the chat build cache with a stale name/color
// embedded (that cache's sig watches transcript+states only, deliberately). So the strip applies THIS
// blob to existing sessions on every push, and any writer's change renders within a push cycle, no
// reload, no per-route confirm frames. Pure and DOM-free, split out of render.ts for tests (the
// session-views.ts pattern).
//
// The pending guard keeps the strip flap-free (the cards-move-on-new-information rule, applied to
// labels): an OPTIMISTIC local edit (the color-swatch click; the kernel's one-shot renamed confirm)
// records what it expects here, and a push built BEFORE the kernel applied that edit cannot revert
// the strip while the expectation stands. Cleared by the echo (the push agreeing), or yielded after
// three silent pushes — the sessionViews pendingViewsAge machinery's constants and reasoning.

export interface TabColor { bg: string; fg: string }
export interface TabSessionMeta { name: string; color: TabColor | null }
export interface PendingTabMeta { name?: string; colorBg?: string; age: number }

export const PENDING_META_MAX_AGE = 3;

/** Record a local optimistic edit so pushes built before it cannot revert the strip. */
export function notePendingMeta(pending: Map<string, PendingTabMeta>, id: string,
                                edit: { name?: string; colorBg?: string }): void {
  const p = pending.get(id) || { age: 0 };
  if (edit.name !== undefined) p.name = edit.name;
  if (edit.colorBg !== undefined) p.colorBg = edit.colorBg;
  p.age = 0;
  pending.set(id, p);
}

const validColor = (c: unknown): TabColor | null =>
  (c && typeof (c as any).bg === "string" && typeof (c as any).fg === "string")
    ? { bg: (c as any).bg, fg: (c as any).fg } : null;

/** Apply one pushed tab-meta entry to its session — a pending local edit holds its field until the
 *  push echoes it. Returns true when something visible changed (the caller repaints). */
export function applyMetaToSession(s: TabSessionMeta, t: { name?: unknown; color?: unknown },
                                   p?: PendingTabMeta): boolean {
  let changed = false;
  const name = typeof t.name === "string" && t.name ? t.name : null;
  const color = validColor(t.color);
  if (name !== null && (!p || p.name === undefined || p.name === name) && s.name !== name) {
    s.name = name; changed = true;
  }
  if (color && (!p || p.colorBg === undefined || p.colorBg.toLowerCase() === color.bg.toLowerCase())
      && (!s.color || s.color.bg !== color.bg || s.color.fg !== color.fg)) {
    s.color = color; changed = true;
  }
  return changed;
}

/** Sync every pushed tab's meta onto its existing session (placeholder tabs read the blob directly).
 *  Ages the pending guard: an edit this push echoes clears; an unechoed one yields after
 *  PENDING_META_MAX_AGE pushes so the kernel's view wins eventually (it is the store of record).
 *  Returns true when any session visibly changed. */
export function syncSessionsFromTabMeta(
  tabs: ReadonlyArray<{ id?: unknown; name?: unknown; color?: unknown }>,
  get: (id: string) => TabSessionMeta | undefined,
  pending: Map<string, PendingTabMeta>,
): boolean {
  let changed = false;
  const seen = new Set<string>();
  for (const t of tabs) {
    if (!t || typeof t.id !== "string") continue;
    seen.add(t.id);
    const p = pending.get(t.id);
    const s = get(t.id);
    if (s && applyMetaToSession(s, t, p)) changed = true;
    if (p) {
      const c = validColor(t.color);
      const nameEchoed = p.name === undefined || (typeof t.name === "string" && t.name === p.name);
      const colorEchoed = p.colorBg === undefined || (!!c && c.bg.toLowerCase() === p.colorBg.toLowerCase());
      if (nameEchoed && colorEchoed) pending.delete(t.id);
      else if (++p.age >= PENDING_META_MAX_AGE) pending.delete(t.id);
    }
  }
  // a pending edit for a tab the push no longer carries ages out the same way (closed mid-edit)
  for (const [id, p] of pending) {
    if (!seen.has(id) && ++p.age >= PENDING_META_MAX_AGE) pending.delete(id);
  }
  return changed;
}
