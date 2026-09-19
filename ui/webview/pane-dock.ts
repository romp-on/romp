// THE PANE DOCKING ENGINE's pure half (plans/pane-docking.md, phase two): everything the shell engine
// (panedock-main.ts) decides that needs no DOM. The drop ZONES (four half-zones per pane, chosen by the nearest
// edge, plus a chat pane's tab strip as the join zone for a tab payload), the LANDING rectangle the live accent
// outline shows for a zone, the grab-surface gate and the slop, the SEED of a layout from the three shipped
// stores (romp-panes for which leaves exist, romp-pane-grow for the row's ratios, the chat columns in row
// order, the timeline band as a fixed-px kid) and the RECONCILE of a layout with the panes the shell shows
// now (a pane turned off parks, a pane turned on opens at its default dock). Like pane-tree.ts: no window, no
// document; the engine hands it rects, ids and points. Node-tested in pane-dock.test.ts.
import {
  type Edge, type Layout, type Node, type PaneId, type Rect,
  closePane, dockRoot, has, leaves, openPane, seedRowOverFixedBand, setFixed,
} from "./pane-tree";

/** The gap between sibling panes, the shipped gutter's 7 px. */
export const GUTTER = 7;
/** The grab RING inside every pane's rectangle (px): the pane's own padding, the iframe inset by this much,
 *  so a press on the ring is a press on the pane element itself, a grab surface without chrome. Six px since
 *  2026-09-19: the user's first press on a 3 px ring showed the hand but landed nothing at a normal pointer speed;
 *  six is a target a pointer lands on without aiming, and still reads as the pane's margin, not as chrome. */
export const RING = 6;
/** A press becomes a drag after this much travel (px); under it a press is a click, a text selection, a scroll. */
export const SLOP = 4;
/** The band's height when nothing has set `--tl` yet (the stylesheet's `var(--tl,200px)`). */
export const DEFAULT_BAND_PX = 200;
/** The layout store's key (plans/pane-docking.md section 6): written only while the kit is on. */
export const LAYOUT_KEY = "romp-layout";

export const CHAT = "chat-pane", FLEET = "fleet-pane", FEED = "feed-pane", FILES = "files-pane", BAND = "tl-pane";
/** Today's row order for the dashboard panes (kernel.py's fixed DOM order); chat columns slot after the chat. */
export const ROW_ORDER: PaneId[] = [CHAT, FLEET, FEED, FILES];

export interface Pt { x: number; y: number }
export type Payload = "pane" | "tab";
export type Zone = { target: PaneId; edge: Edge; strip?: false } | { target: PaneId; strip: true; edge?: undefined };

export function isChatPane(id: PaneId): boolean { return id === CHAT || /^chat-pane-\d+$/.test(id); }
export function isBand(id: PaneId): boolean { return id === BAND; }

/** The romp-pane-grow key of a pane id (`chat`, `fleet`, `feed`, `files`, `chat<n>` for a column). */
export function growKey(id: PaneId): string {
  if (id === CHAT) return "chat";
  if (id === FLEET) return "fleet";
  if (id === FEED) return "feed";
  if (id === FILES) return "files";
  const m = /^chat-pane-(\d+)$/.exec(id);
  return m ? "chat" + m[1] : id;
}

function inside(r: Rect, p: Pt): boolean { return p.x >= r.x && p.x < r.x + r.w && p.y >= r.y && p.y < r.y + r.h; }

/** The HALF-ZONE of a point inside a rect: the edge the point is nearest to, distances measured as a fraction
 *  of the rect's own dimension so a wide, short pane still offers its top and bottom halves. null outside. */
export function edgeZone(rect: Rect, p: Pt): Edge | null {
  if (!inside(rect, p) || rect.w <= 0 || rect.h <= 0) return null;
  const dl = (p.x - rect.x) / rect.w, dr = (rect.x + rect.w - p.x) / rect.w;
  const dt = (p.y - rect.y) / rect.h, db = (rect.y + rect.h - p.y) / rect.h;
  const m = Math.min(dl, dr, dt, db);
  return m === dl ? "left" : m === dr ? "right" : m === dt ? "top" : "bottom";
}

export interface ZoneOpts {
  /** the dragged pane (never a target of its own drop), or null for a tab payload */
  self: PaneId | null;
  /** the payload: a whole pane (edges only; a strip refuses it) or a single tab (a chat strip joins it) */
  payload: Payload;
  /** each chat pane's tab-strip height in px from its rect's top (the strip join zone), by pane id */
  strips?: Record<PaneId, number>;
}

/** The drop zone under a point, over the panes' current rectangles (viewport px, read right before the call).
 *  A point over the dragged pane itself is no zone (a drop there is a cancel); over a chat pane's strip band it
 *  is the STRIP join zone (a tab joins the group; a whole pane is refused there, which the engine shows as a
 *  refused outline); anywhere else in a pane it is that pane's nearest half. null over no pane. */
export function zoneAt(rects: ReadonlyArray<{ pane: PaneId; rect: Rect }>, p: Pt, opts: ZoneOpts): Zone | null {
  for (const { pane, rect } of rects) {
    if (!inside(rect, p)) continue;
    if (opts.self !== null && pane === opts.self) return null;
    const stripH = opts.strips && isChatPane(pane) ? opts.strips[pane] || 0 : 0;
    if (stripH > 0 && p.y < rect.y + stripH) return { target: pane, strip: true };
    const edge = edgeZone(rect, p);
    return edge ? { target: pane, edge } : null;
  }
  return null;
}

/** The rectangle the live outline shows for a zone: the target's half on that edge (what a dock there
 *  produces), or the strip band for a join. null when the target has no rect. */
export function landingRect(rects: ReadonlyArray<{ pane: PaneId; rect: Rect }>, zone: Zone, strips?: Record<PaneId, number>): Rect | null {
  const hit = rects.find((r) => r.pane === zone.target);
  if (!hit) return null;
  const r = hit.rect;
  if (zone.strip) return { x: r.x, y: r.y, w: r.w, h: Math.max(1, (strips && strips[zone.target]) || 0) };
  const hw = (r.w - GUTTER) / 2, hh = (r.h - GUTTER) / 2;
  switch (zone.edge) {
    case "left": return { x: r.x, y: r.y, w: Math.max(0, hw), h: r.h };
    case "right": return { x: r.x + r.w - Math.max(0, hw), y: r.y, w: Math.max(0, hw), h: r.h };
    case "top": return { x: r.x, y: r.y, w: r.w, h: Math.max(0, hh) };
    default: return { x: r.x, y: r.y + r.h - Math.max(0, hh), w: r.w, h: Math.max(0, hh) };
  }
}

/** A press, described for the grab gate: the primary button, the modifier, and what it landed on. */
export interface Press {
  button: number;
  alt: boolean;
  /** the pane element itself (its padding ring), not its iframe */
  onRing: boolean;
  /** the empty run of a pane's existing top row (the chat's tab strip between the + tab and its end; the Files bar) */
  onTopRun: boolean;
  /** a control, a link, a text field, or a text selection in progress: never a grab */
  onControl: boolean;
}

/** Whether a press may ARM a pane move: the primary button, not on a control, from a grab surface (the ring,
 *  a top-row empty run) or with Option/Alt held anywhere (plans/pane-docking.md section 3). The slop below
 *  still has to be crossed before anything lifts. */
export function grabbable(p: Press): boolean {
  return p.button === 0 && !p.onControl && (p.alt || p.onRing || p.onTopRun);
}

/** Whether a pointer has travelled past the slop from its press. */
export function crossedSlop(dx: number, dy: number, slop: number = SLOP): boolean {
  return Math.abs(dx) >= slop || Math.abs(dy) >= slop;
}

/** What the shell shows, read by the engine from the DOM: the panes on screen in row order (the chat, its
 *  side columns, then the outline, feed and files as toggled on), whether the band is on, the band's px. */
export interface Shown {
  row: PaneId[]; band: boolean; bandPx: number; grow: Record<string, number>;
  /** the pane ids whose ELEMENTS exist (shown or hidden); absent, every parked id is kept. A park keeps a MOUNTED
   *  iframe (section 5); a closed chat column has none, so its park is pruned rather than kept forever. */
  present?: PaneId[];
}

/** Seed a layout from the shipped stores, plans/pane-docking.md section 6: the row of shown panes weighted by
 *  their romp-pane-grow numbers over the band as a FIXED kid. This reproduces today's row when the kit turns on;
 *  the old keys are read, never written. */
export function seedLayout(sh: Shown): Layout {
  const row = sh.row.length ? sh.row : [CHAT];
  const grow: Record<PaneId, number> = {};
  for (const id of row) { const g = sh.grow[growKey(id)]; grow[id] = typeof g === "number" && g > 0 ? g : 1; }
  const tree = seedRowOverFixedBand(row, grow, sh.band ? BAND : null, sh.bandPx > 0 ? sh.bandPx : DEFAULT_BAND_PX);
  return { v: 1, tree, parked: [] };
}

/** A newly shown pane's DEFAULT DOCK: where the rail's "open" puts it (section 5). A chat column goes right of
 *  the last chat leaf; the outline right of the chat side; the feed right of the outline (else the chat side);
 *  the files right of the rightmost non-band leaf; the chat itself left of everything; the band at the bottom
 *  of the whole tree as a fixed kid. Returns the target leaf and edge, or null to dock against the root. */
export function defaultDock(tree: Node, pane: PaneId): { target: PaneId; edge: Edge } | null {
  const ls = leaves(tree).filter((p) => !isBand(p));
  if (!ls.length) return null;
  const chats = ls.filter(isChatPane);
  const lastChat = chats.length ? chats[chats.length - 1] : null;
  if (isBand(pane)) return null;
  if (pane === CHAT) return { target: ls[0], edge: "left" };
  if (isChatPane(pane)) return lastChat ? { target: lastChat, edge: "right" } : { target: ls[0], edge: "left" };
  if (pane === FLEET) return lastChat ? { target: lastChat, edge: "right" } : { target: ls[0], edge: "left" };
  if (pane === FEED) {
    if (ls.includes(FLEET)) return { target: FLEET, edge: "right" };
    if (lastChat) return { target: lastChat, edge: "right" };
    return { target: ls[0], edge: "left" };
  }
  return { target: ls[ls.length - 1], edge: "right" };   // files, and any pane this list does not know: the right end
}

/** Reconcile a layout with what the shell SHOWS now: every leaf no longer shown is PARKED (its iframe stays
 *  mounted and hidden, today's togglePane feel), every shown pane not in the tree opens at its default dock,
 *  and the band's fixed px follows `bandPx`. The shown set is the rail's truth (body.po-* plus the columns
 *  present), so the tree can never allot a rectangle to a hidden pane or forget a visible one. Pure. */
export function reconcileShown(cur: Layout, sh: Shown): Layout {
  const want = new Set<PaneId>(sh.row.concat(sh.band ? [BAND] : []));
  let lay: Layout = cur;
  // park what is gone (the only-pane refusal is fine: a tree of one hidden pane is replaced below)
  for (const p of leaves(lay.tree)) {
    if (want.has(p)) continue;
    const r = closePane(lay, p);
    if (r.ok) lay = r.layout;
  }
  // open what is new, in row order (so the outline lands right of the chat before the feed asks for the outline)
  const order = ROW_ORDER.concat(sh.row.filter((p) => !ROW_ORDER.includes(p)));
  const missing = order.filter((p) => want.has(p) && !has(lay.tree, p));
  for (const p of missing) {
    if (leaves(lay.tree).every((q) => !want.has(q))) {
      // the tree holds only panes that should be hidden (every shown pane was parked): start over from this pane,
      // and PARK the hidden ones the tree held (their iframes stay mounted and hidden, as a rail close leaves them)
      const dropped = leaves(lay.tree).filter((q) => !lay.parked.includes(q));
      lay = { v: 1, tree: { pane: p }, parked: lay.parked.concat(dropped).filter((q) => q !== p) };
      continue;
    }
    const d = defaultDock(lay.tree, p);
    if (d && has(lay.tree, d.target)) {
      const r = openPane(lay, p, d.target, d.edge);
      if (r.ok) lay = r.layout;
    } else {
      lay = { v: 1, tree: dockRoot(lay.tree, p, "right"), parked: lay.parked.filter((q) => q !== p) };
    }
  }
  if (sh.band && !has(lay.tree, BAND)) {
    lay = { v: 1, tree: dockRoot(lay.tree, BAND, "bottom", sh.bandPx > 0 ? sh.bandPx : DEFAULT_BAND_PX), parked: lay.parked.filter((q) => q !== BAND) };
  }
  if (sh.band) lay = { ...lay, tree: setFixed(lay.tree, BAND, sh.bandPx > 0 ? sh.bandPx : DEFAULT_BAND_PX) };
  // a stale park of anything shown is dropped (the parked set never holds a docked pane), and so is the park of a
  // pane whose element is gone (a closed chat column: nothing is mounted to re-open)
  const parked = lay.parked.filter((q) => !has(lay.tree, q) && (!sh.present || sh.present.includes(q)));
  return parked.length === lay.parked.length ? lay : { ...lay, parked };
}

/** The `--tl` band height in px from the shell's `.col` style value (`"312px"`), else the default. */
export function bandPxOf(tlValue: string | null | undefined): number {
  const n = parseFloat(String(tlValue || ""));
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_BAND_PX;
}

/** Round a rect to whole px for the DOM (the module's fractional rects are intended; the shell rounds at the
 *  edge), keeping the far edge exact so neighbours never overlap by a rounding step. */
export function roundRect(r: Rect): Rect {
  const x = Math.round(r.x), y = Math.round(r.y);
  return { x, y, w: Math.max(0, Math.round(r.x + r.w) - x), h: Math.max(0, Math.round(r.y + r.h) - y) };
}
