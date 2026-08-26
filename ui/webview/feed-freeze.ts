// HOVER-FREEZE (the user 2026-08-24): while the pointer rests on a card, the board must not move
// under it — incoming feed payloads queue in feed.ts instead of rendering, and the deferred churn
// surfaces as a subtle "+N/-N" hint beside the column pills (and the session headers in grouped
// mode). The pure counting lives here so node --test executes the rule without a DOM: given the
// DISPLAYED view and the QUEUED payload's would-be view (both already view-filtered by the caller),
// count per-column and per-session arrivals and departures. A card present in both views in the
// same column counts nothing — the badge hints at deferred MOVEMENT, not content edits.

export type FreezeItem = { id: string; col: string; sid: string };
export type FreezeCount = { add: number; del: number };
export type FreezeCounts = {
  cols: Record<string, FreezeCount>;
  sess: Record<string, FreezeCount>;
  any: boolean;
};

// The hovered card's "did IT change?" compare projects CONTENT-BEARING fields only (the user
// 2026-08-25: the whole-item compare flagged the recency tint — trgb recomputes every build as
// cards age, at the top level AND inside every sub-goal node — as "this card updated" on nearly
// every card). An EXPLICIT list, so a new volatile field can never silently rejoin the diff; the
// sub-goal tree contributes its nodes' identity/text/status only, never their own aging channels.
const SELF_CONTENT = ["text", "column", "summary", "blockSummary", "blocked", "warns", "retrying", "nudgeFailed"] as const;
export function contentSig(a: Record<string, unknown> | undefined | null): string {
  if (!a) return "";
  const o: Record<string, unknown> = {};
  for (const k of SELF_CONTENT) o[k] = a[k];
  const tree = Array.isArray(a.tree) ? (a.tree as Record<string, unknown>[]) : [];
  o.tree = tree.map((n) => [n.id, n.text, n.status, n.cleared]);
  return JSON.stringify(o);
}

export function freezeDiff(displayed: FreezeItem[], pending: FreezeItem[]): FreezeCounts {
  const cols: Record<string, FreezeCount> = {};
  const sess: Record<string, FreezeCount> = {};
  const at = (m: Record<string, FreezeCount>, k: string) => (m[k] ??= { add: 0, del: 0 });
  const dById = new Map(displayed.map((d) => [d.id, d] as const));
  const pById = new Map(pending.map((p) => [p.id, p] as const));
  let any = false;
  for (const p of pending) {
    const d = dById.get(p.id);
    if (!d) {
      at(cols, p.col).add++; at(sess, p.sid).add++; any = true;
    } else if (d.col !== p.col) {
      // a column MOVE leaves one header and arrives at another — one del + one add, and the same
      // for its session (grouped mode groups per column, so the run moves too)
      at(cols, d.col).del++; at(cols, p.col).add++;
      at(sess, d.sid).del++; at(sess, p.sid).add++;
      any = true;
    }
  }
  for (const d of displayed) {
    if (!pById.has(d.id)) {
      at(cols, d.col).del++; at(sess, d.sid).del++; any = true;
    }
  }
  return { cols, sess, any };
}
