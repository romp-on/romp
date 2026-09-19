// THE PANE DOCKING ENGINE (plans/pane-docking.md, phase two), a dashboard-shell bundle loaded like
// palette-main.ts (a `<script src=/dist/panedock-main.js>` in the shell head, kernel.py). It runs ONLY
// when the per-browser gear switch `paneDocking` is on (the user's one constraint: DEFAULT OFF). With the
// switch off this bundle changes nothing: no class, no stylesheet, no listener that acts, no store written,
// so the shipped pane layout (the _LANDING_* inline JS and its three stores) is byte for byte what it is
// today. On, it:
//
// - positions every pane iframe's `.pane` by GEOMETRY from the layout tree (pane-tree.ts): absolute rects
//   inside the shell's `.col`, the row's gutters and the band's `#gh` hidden, the band a fixed-px kid whose
//   px follows the shipped `--tl` (so the inline autosize keeps working). Nothing is ever re-parented: an
//   iframe moved in the DOM reloads and drops its socket (section 2).
// - seeds the layout ONCE from the three shipped stores (romp-panes, romp-pane-grow, the chat columns) and
//   keeps it in `romp-layout` ({v:1, tree, parked}); the old keys are read, never written (section 6). The
//   rail's toggles keep their meaning: a pane turned off PARKS (its iframe stays mounted and hidden, the
//   po-* class exactly as today), a pane turned on opens at its default dock (section 5).
// - arms a pane MOVE from the pane's empty space, never a title bar (section 3): the padding ring around
//   its iframe, the empty run of its existing top row (the chat's tab strip, the Files bar), or Option/Alt
//   held anywhere over it. An open-hand cursor over a grab surface, a closed hand while held; a press lifts
//   only after the slop, so a click, a text selection or a scroll is never a drag; Escape cancels.
// - shows the LIVE ACCENT OUTLINE while a drag is in flight: the rectangle the pane will land in (the
//   target's half on the nearest edge, section 4), a thin ring following the pointer over no zone, a
//   refused ring over a chat strip (a pane is not a tab). The drop is a pure tree move plus a recompute.
// - mounts one divider per internal edge: a horizontal edge drags a deferred landing line and commits once
//   at release (the row's cost rule, section 2), a vertical edge writes live, the band's edge writes `--tl`.
//
// Tabs as drop payloads (a tab dropped into a zone becomes a pane there; the strip as its join zone) are
// the plan's second seam and ship in the next pull request; until then the shipped tab-drag zones keep
// working and a column they open or close is mirrored into the tree (the romp-chat-cols event).
//
// Pure decisions live in pane-dock.ts and pane-tree.ts (node-tested); this file is the DOM glue. The flag
// read stays the shell's own raw-read idiom, split into a PURE `isPaneDockingOn` for the node test.
import {
  type Edge, type EdgeRect, type Layout, type PaneId, type Rect,
  edges, has, layout as layoutRects, leaves, move, parse, resize, serialise,
} from "./pane-tree";
import {
  BAND, CHAT, FEED, FILES, FLEET, GUTTER, LAYOUT_KEY, RING, type Payload, type Shown, type Zone,
  bandPxOf, crossedSlop, grabbable, growKey, isChatPane, landingRect, reconcileShown, roundRect, seedLayout, zoneAt,
} from "./pane-dock";

export const PANE_DOCKING_CLASS = "pane-docking";
const SETTINGS_KEY = "romp:settings";
const GROW_KEY = "romp-pane-grow";
const DRAG_CLASS = "pd-drag", RESIZE_CLASS = "pd-resize", ALT_CLASS = "pd-alt";
const STYLE_ID = "pd-css";
const GRAB_SCRIPT_ID = "pd-grab";
const MIN_PX = 120;      // a pane never resizes below this or a quarter of its pair (the shipped clamp)
const BAND_MIN = 48;     // the band's floor (the shipped #gh clamp)

/** Whether the gear's per-browser `paneDocking` switch is on, from the raw `romp:settings` JSON. Only the
 *  literal `true` turns it on: a store from before the key, a missing value, or any other type reads OFF
 *  (the fail-safe default for an opt-in that gates a whole layout engine). Pure; never throws. */
export function isPaneDockingOn(rawSettings: string | null): boolean {
  try {
    const o = JSON.parse(rawSettings || "{}");
    return !!o && typeof o === "object" && (o as { paneDocking?: unknown }).paneDocking === true;
  } catch {
    return false;
  }
}

/** The shell's title for a pane, for the free-floating outline (the keyboard palette's words, never chrome). */
export function paneTitle(id: PaneId): string {
  if (id === CHAT) return "Chat";
  if (id === FLEET) return "Outline";
  if (id === FEED) return "Feed";
  if (id === FILES) return "Files";
  if (id === BAND) return "Sessions";
  const m = /^chat-pane-(\d+)$/.exec(id);
  return m ? "Chat " + m[1] : id;
}

// The SHELL stylesheet, injected only while the kit is on (so the off DOM carries no node of the kit's).
const SHELL_CSS = [
  `body.${PANE_DOCKING_CLASS} .col{position:relative}`,
  // the row stays the flex kid that fills the space above the rail, but lays out nothing itself: every pane is
  // positioned against .col, the shipped gutters and the band's gutter are hidden (the kit mounts its own dividers)
  `body.${PANE_DOCKING_CLASS} .row{display:block;position:static;flex:1 1 auto;min-height:0}`,
  `body.${PANE_DOCKING_CLASS} .row>.gv,body.${PANE_DOCKING_CLASS} #gh{display:none}`,
  `body.${PANE_DOCKING_CLASS} .pane{position:absolute;margin:0;cursor:grab}`,
  `body.${PANE_DOCKING_CLASS} #tl-pane{position:absolute}`,
  // the grab RING: the pane's own padding, the iframe inset by it (a press on the ring is a press on the pane element).
  // The size is EXPLICIT: an absolutely positioned replaced element with width and height auto takes its intrinsic
  // 300 by 150 px and ignores its far offsets (CSS 2.1 10.3.8 and 10.6.5), so inset alone never stretches an iframe
  // (the round-one read: every pane's content sat in a 300 by 150 box at its top-left)
  `body.${PANE_DOCKING_CLASS} .pane>iframe{inset:${RING}px;width:calc(100% - ${2 * RING}px);height:calc(100% - ${2 * RING}px)}`,
  `body.${PANE_DOCKING_CLASS} .pane.split-v{padding:${RING}px;box-sizing:border-box}`,
  `body.${PANE_DOCKING_CLASS} .pane.split-v>iframe{inset:auto;width:100%;height:auto}`,
  // the dividers: the shipped gutter dress (a 1 px line in a 7 px strip), col-resize between columns, row-resize between rows
  `body.${PANE_DOCKING_CLASS} .pd-div{position:absolute;z-index:7;background:linear-gradient(90deg,transparent 3px,#333 3px,#333 4px,transparent 4px);cursor:col-resize}`,
  `body.${PANE_DOCKING_CLASS} .pd-div[data-dir=col]{background:linear-gradient(180deg,transparent 3px,#333 3px,#333 4px,transparent 4px);cursor:row-resize}`,
  // the closed hand from the PRESS on (:active, before any travel: the press registered, the pane is yours), and while
  // a pane is held; the iframes go pointer-transparent so the shell hears every move
  `body.${PANE_DOCKING_CLASS} .pane:active{cursor:grabbing}`,
  `body.${PANE_DOCKING_CLASS}.${DRAG_CLASS},body.${PANE_DOCKING_CLASS}.${DRAG_CLASS} .pane,body.${PANE_DOCKING_CLASS}.${DRAG_CLASS} .pd-div{cursor:grabbing}`,
  `body.${PANE_DOCKING_CLASS}.${DRAG_CLASS} iframe,body.${PANE_DOCKING_CLASS}.${RESIZE_CLASS} iframe{pointer-events:none}`,
  `body.${PANE_DOCKING_CLASS}.${ALT_CLASS} .pane,body.${PANE_DOCKING_CLASS}.${ALT_CLASS} iframe{cursor:grab}`,
  // the live outline: the accent wash inside a 2 px accent ring (the shipped #col-ghost dress), never a hit target,
  // above the focus ring; `free` while over no zone (a thin ring following the pointer); `refused` over a strip
  `#pd-outline{display:none;position:fixed;pointer-events:none;z-index:41;background:rgba(156,210,255,0.12);box-shadow:inset 0 0 0 2px var(--accent,#9cd2ff);align-items:center;justify-content:center;font:600 11px 'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#8a8a8a;letter-spacing:.04em}`,
  `#pd-outline.on{display:flex}`,
  `#pd-outline.free{background:transparent;box-shadow:inset 0 0 0 1px var(--accent,#9cd2ff)}`,
  `#pd-outline.refused{background:transparent;box-shadow:inset 0 0 0 1px var(--accent,#9cd2ff)}`,
  // the divider drag's deferred landing line, the #gv-ghost dress
  `#pd-ghost{display:none;position:fixed;pointer-events:none;z-index:40;background:linear-gradient(90deg,transparent 3px,var(--accent,#9cd2ff) 3px,var(--accent,#9cd2ff) 4px,transparent 4px)}`,
].join("\n");

// Injected into the CHAT and FILES documents while the kit is on: the open hand over the top row's empty run
// (the strip itself and its end spacer; the tabs and group heads keep the arrow they have today), and the
// Files bar. Removed with the kit.
const PANE_CSS = "#tabbar,#tabs,.tab-strip-end,.fileview-bar{cursor:grab}\n.tab,.tab-group-head,.tab-row-line{cursor:auto}";
const CONTROL_SEL = "button,a,input,textarea,select,[role=button],[contenteditable],.tab,.tab-group-head,.tab-tagchips,.tab-widgets-gear,.col-x";
const TOP_RUN_SEL = "#tabbar,#tabs,.tab-strip-end,.fileview-bar";

interface Press { pane: PaneId; frame: HTMLIFrameElement | null; win: Window; x0: number; y0: number; armed: boolean; zone: Zone | null }
interface DivDrag { edge: EdgeRect; x0: number; y0: number; live: boolean; last: number }

function byId(id: string): HTMLElement | null { return document.getElementById(id); }
function frameOfPane(id: PaneId): HTMLIFrameElement | null {
  const p = byId(id);
  return p ? (p.querySelector(":scope > iframe") as HTMLIFrameElement | null) : null;
}

class Engine {
  on = false;
  private col: HTMLElement | null = null;
  private row: HTMLElement | null = null;
  private lay: Layout | null = null;
  private style: HTMLStyleElement | null = null;
  private outline: HTMLElement | null = null;
  private ghost: HTMLElement | null = null;
  private dividers: HTMLElement[] = [];
  private divEdges = new Map<HTMLElement, EdgeRect>();
  private press: Press | null = null;
  private pressOff: (() => void) | null = null;
  private div: DivDrag | null = null;
  private divOff: (() => void) | null = null;
  private obs: MutationObserver | null = null;
  private wired = new WeakSet<Document>();
  private offs: Array<() => void> = [];
  private altOn = false;
  private savedWriters: Record<string, unknown> | null = null;

  constructor() {
    const w = window as any;
    // read-only hooks for the served pins: the switch, the layout, the panes' viewport rects, the drag state
    w.__rompPaneDock = {
      on: () => this.on,
      layout: () => (this.lay ? parse(serialise(this.lay)) : null),
      rects: () => this.viewportRects(),
      dragging: () => !!(this.press && this.press.armed),
      pressed: () => !!this.press,
      zone: () => (this.press ? this.press.zone : null),
    };
  }

  private mobile(): boolean {
    const w = window as any;
    try { return !!(w.__rompMobileOn && w.__rompMobileOn()); } catch { return false; }
  }

  /** Reflect the switch: on (and not the phone layout) starts the engine, anything else stops it. */
  apply(): void {
    if (!document.body) return;
    let raw: string | null = null;
    try { raw = localStorage.getItem(SETTINGS_KEY); } catch { raw = null; }
    const want = isPaneDockingOn(raw) && !this.mobile();
    if (want && !this.on) this.start();
    else if (!want && this.on) this.stop();
    else if (this.on) this.reconcile();
  }

  // ── lifecycle ────────────────────────────────────────────────────────────────────────────────────────
  private start(): void {
    this.col = document.querySelector(".col") as HTMLElement | null;
    this.row = document.querySelector(".row") as HTMLElement | null;
    if (!this.col || !this.row) return;
    this.on = true;
    document.body.classList.add(PANE_DOCKING_CLASS);
    this.style = document.createElement("style"); this.style.id = STYLE_ID; this.style.textContent = SHELL_CSS;
    document.head.appendChild(this.style);
    this.outline = document.createElement("div"); this.outline.id = "pd-outline"; document.body.appendChild(this.outline);
    this.ghost = document.createElement("div"); this.ghost.id = "pd-ghost"; document.body.appendChild(this.ghost);
    // the store, seeded once from the shipped keys when absent, then reconciled with what the rail shows
    let stored: Layout | null = null;
    try { stored = parse(localStorage.getItem(LAYOUT_KEY) || ""); } catch { stored = null; }
    const sh = this.shown();
    this.lay = reconcileShown(stored || seedLayout(sh), sh);
    this.persist();
    this.render();
    // the events the layout follows: the rail's toggles, the chat columns, the band's --tl, the window
    const on = (t: EventTarget, k: string, h: EventListenerOrEventListenerObject, o?: boolean | AddEventListenerOptions) => { t.addEventListener(k, h, o); this.offs.push(() => t.removeEventListener(k, h, o)); };
    on(window, "romp-panes", () => this.reconcile());
    on(window, "romp-chat-cols", (e) => { const d = ((e as CustomEvent).detail || {}) as { frame?: HTMLIFrameElement }; if (d.frame) this.wire(d.frame); this.reconcile(); });
    on(window, "resize", () => { if (this.mobile()) this.apply(); else this.render(); });
    on(this.col, "pointerdown", (e) => this.onShellPress(e as PointerEvent), true);
    on(document, "keydown", (e) => this.onKey(e as KeyboardEvent), true);
    on(document, "keyup", (e) => this.onKey(e as KeyboardEvent), true);
    on(window, "blur", () => this.setAlt(false));
    on(window, "message", (e) => this.onGrabMessage(e as MessageEvent));
    this.obs = new MutationObserver(() => this.reconcile());
    this.obs.observe(this.col, { attributes: true, attributeFilter: ["style"] });
    this.allFrames().forEach((f) => this.wire(f));
    this.standDownGrowWriters();
  }

  // The shipped geometry writers (_LANDING_JS: a new column halves the rightmost pane's grow, a closing one hands its
  // width left, a re-shown pane takes a fair grow, an unregistered pane drops its key) each write romp-pane-grow. Under
  // the kit the tree owns the geometry and the old keys are the OFF path's source of truth (plans/pane-docking.md
  // section 10), so while the kit is on they are stood down: the same globals answer as no-ops, and the originals
  // return when the kit goes off. The inline pane list they maintain is harmless stale while off (a missing element
  // is filtered by its own shown() check).
  private static WRITERS = ["__rompSplitGrow", "__rompSplitShrink", "__rompGrowFair", "__rompGrowFairIfNew", "__rompUnregisterPane"];
  private standDownGrowWriters(): void {
    const w = window as any;
    if (this.savedWriters) return;
    this.savedWriters = {};
    for (const k of Engine.WRITERS) { this.savedWriters[k] = w[k]; w[k] = () => false; }
  }
  private restoreGrowWriters(): void {
    const w = window as any;
    if (!this.savedWriters) return;
    for (const k of Engine.WRITERS) { if (this.savedWriters[k] !== undefined) w[k] = this.savedWriters[k]; }
    this.savedWriters = null;
  }

  private stop(): void {
    this.on = false;
    this.cancelPress(); this.endDiv(false);
    document.body.classList.remove(PANE_DOCKING_CLASS, DRAG_CLASS, RESIZE_CLASS, ALT_CLASS);
    this.offs.forEach((f) => f()); this.offs = [];
    if (this.obs) { this.obs.disconnect(); this.obs = null; }
    this.dividers.forEach((d) => d.remove()); this.dividers = []; this.divEdges.clear();
    if (this.style) { this.style.remove(); this.style = null; }
    if (this.outline) { this.outline.remove(); this.outline = null; }
    if (this.ghost) { this.ghost.remove(); this.ghost = null; }
    // the panes return to the flex row: every inline geometry the kit wrote goes
    this.allPaneEls().forEach((el) => { el.style.left = el.style.top = el.style.width = el.style.height = ""; });
    this.allFrames().forEach((f) => this.unwire(f));
    this.setAlt(false);
    this.restoreGrowWriters();
  }

  // ── what the shell shows ─────────────────────────────────────────────────────────────────────────────
  private allPaneEls(): HTMLElement[] {
    return Array.from(document.querySelectorAll(".col .pane")).filter((el) => !el.closest(".chat-sub")) as HTMLElement[];
  }
  private allFrames(): HTMLIFrameElement[] {
    return this.allPaneEls().map((p) => p.querySelector(":scope > iframe") as HTMLIFrameElement | null).filter((f): f is HTMLIFrameElement => !!f);
  }
  private poOn(key: string): boolean { return document.body.classList.contains("po-" + key); }
  private shown(): Shown {
    const row: PaneId[] = [];
    if (this.poOn("chat") && byId(CHAT)) {
      row.push(CHAT);
      // the side columns the chat split made, in DOM order (a bottom pane nests inside its parent and is no pane here)
      Array.from((this.row || document).querySelectorAll(".pane.chat-col")).forEach((el) => { if (el.id) row.push(el.id); });
    }
    for (const id of [FLEET, FEED, FILES]) if (this.poOn(growKey(id)) && byId(id)) row.push(id);
    let grow: Record<string, number> = {};
    try { const g = JSON.parse(localStorage.getItem(GROW_KEY) || "null"); if (g && typeof g === "object") grow = g; } catch { grow = {}; }
    const band = this.poOn("timeline") && !!byId(BAND);
    const present = this.allPaneEls().map((el) => el.id).filter(Boolean);   // a closed column's element is gone: its park goes with it
    return { row, band, bandPx: this.bandPx(), grow, present };
  }
  private bandPx(): number {
    const c = this.col;
    let v = c ? c.style.getPropertyValue("--tl") : "";
    if (!v && c) { try { v = getComputedStyle(c).getPropertyValue("--tl"); } catch { v = ""; } }
    return bandPxOf(v);
  }

  private reconcile(): void {
    if (!this.on || !this.lay) return;
    const next = reconcileShown(this.lay, this.shown());
    const changed = serialise(next) !== serialise(this.lay);
    this.lay = next;
    if (changed) this.persist();
    this.render();
  }

  private persist(): void {
    if (!this.lay) return;
    try { localStorage.setItem(LAYOUT_KEY, serialise(this.lay)); } catch { /* a full store: the layout still shows */ }
  }

  // ── geometry ─────────────────────────────────────────────────────────────────────────────────────────
  private box(): Rect | null {
    const r = this.row;
    if (!r) return null;
    return { x: r.offsetLeft, y: r.offsetTop, w: r.offsetWidth, h: r.offsetHeight };
  }

  private render(): void {
    if (!this.on || !this.lay) return;
    const box = this.box();
    if (!box) return;
    const rects = layoutRects(this.lay.tree, box, GUTTER);
    for (const { pane, rect } of rects) {
      const el = byId(pane);
      if (!el) continue;
      const r = roundRect(rect);
      el.style.left = r.x + "px"; el.style.top = r.y + "px"; el.style.width = r.w + "px"; el.style.height = r.h + "px";
    }
    this.mountDividers(edges(this.lay.tree, box, GUTTER));
  }

  private mountDividers(es: EdgeRect[]): void {
    while (this.dividers.length > es.length) { const d = this.dividers.pop()!; this.divEdges.delete(d); d.remove(); }
    while (this.dividers.length < es.length) {
      const d = document.createElement("div"); d.className = "pd-div";
      d.addEventListener("pointerdown", (e) => this.onDivPress(e, d));
      (this.row || this.col!).appendChild(d); this.dividers.push(d);
    }
    es.forEach((e, i) => {
      const d = this.dividers[i], r = roundRect(e.rect);
      d.setAttribute("data-dir", e.dir);
      d.style.left = r.x + "px"; d.style.top = r.y + "px"; d.style.width = r.w + "px"; d.style.height = r.h + "px";
      this.divEdges.set(d, e);
    });
  }

  /** Every docked pane's rectangle in VIEWPORT px, read from the DOM right now (the zones read these, never a
   *  cached copy: the lesson of every drag lab, re-read the rects right before the release). */
  private viewportRects(): Array<{ pane: PaneId; rect: Rect }> {
    if (!this.lay) return [];
    const out: Array<{ pane: PaneId; rect: Rect }> = [];
    for (const pane of leaves(this.lay.tree)) {
      const el = byId(pane);
      if (!el) continue;
      const b = el.getBoundingClientRect();
      out.push({ pane, rect: { x: b.left, y: b.top, w: b.width, h: b.height } });
    }
    return out;
  }

  /** Each chat pane's tab-strip height (px from the pane's top): its document's #tabbar bottom edge plus the ring. */
  private strips(): Record<PaneId, number> {
    const out: Record<PaneId, number> = {};
    if (!this.lay) return out;
    for (const pane of leaves(this.lay.tree)) {
      if (!isChatPane(pane)) continue;
      const f = frameOfPane(pane);
      try {
        const bar = f && f.contentDocument && f.contentDocument.getElementById("tabbar");
        if (bar) out[pane] = bar.getBoundingClientRect().bottom + RING;
      } catch { /* not ready: no strip zone yet */ }
    }
    return out;
  }

  // ── pane documents: the top-row runs, Option-drag, the keys ──────────────────────────────────────────
  private wire(f: HTMLIFrameElement): void {
    const doWire = () => {
      let d: Document | null = null;
      try { d = f.contentDocument; } catch { d = null; }
      if (!d || d.readyState === "loading") return;
      if (!this.wired.has(d)) {
        this.wired.add(d);
        d.addEventListener("pointerdown", (e) => this.onFramePress(e, f), true);
        d.addEventListener("keydown", (e) => this.onKey(e), true);
        d.addEventListener("keyup", (e) => this.onKey(e), true);
      }
      if (this.on && (f.id === "f-chat" || f.id.indexOf("f-chat-") === 0 || f.id === "f-files") && !d.getElementById(STYLE_ID)) {
        const st = d.createElement("style"); st.id = STYLE_ID; st.textContent = PANE_CSS; (d.head || d.documentElement).appendChild(st);
      }
      if (this.on) this.markDoc(d, f);
    };
    f.addEventListener("load", doWire);
    doWire();
  }
  private unwire(f: HTMLIFrameElement): void {
    try {
      const d = f.contentDocument;
      const st = d && d.getElementById(STYLE_ID);
      if (st) st.remove();
      if (d) d.documentElement.style.cursor = "";
      if (d && d.body) d.body.classList.remove(PANE_DOCKING_CLASS, "pd-grab-hover");   // the grab detector reads this: off, it is inert
      // the kit's nodes in the pane document go with it (the plan: byte-identical off pages): the detector's tag, its
      // style and its window global; the listeners it bound stay, answering only to the class, and re-injection never
      // binds them twice (the detector's own wired flag)
      for (const id of [GRAB_SCRIPT_ID, "pd-grab-css"]) { const n = d && d.getElementById(id); if (n) n.remove(); }
      if (d && d.defaultView) { try { delete (d.defaultView as any).__rompPaneGrab; } catch { /* fine */ } }
    } catch { /* gone */ }
  }

  /** The kit's mark on a pane document while on: the body class the grab detector keys on, and the detector itself
   *  (dist/pane-grab.js, the plan's section 3: the inner page detects a press on its own empty background and forwards
   *  it here), injected once per document; the chat is skipped (its grab surface stays the strip's empty run). */
  private markDoc(d: Document, f: HTMLIFrameElement): void {
    if (!d.body) return;
    d.body.classList.add(PANE_DOCKING_CLASS);
    if (f.id === "f-chat" || f.id.indexOf("f-chat-") === 0 || d.getElementById(GRAB_SCRIPT_ID)) return;
    const sc = d.createElement("script"); sc.id = GRAB_SCRIPT_ID; sc.src = "/dist/pane-grab.js" + this.distVer();
    (d.head || d.documentElement).appendChild(sc);
  }
  /** The shell's own bundle tag carries the dist version (`?v=N`); the injected detector rides the same, so a rebuild busts both. */
  private distVer(): string {
    try {
      const own = document.querySelector('script[src*="panedock-main.js"]') as HTMLScriptElement | null;
      const v = own ? new URL(own.src, location.href).searchParams.get("v") : null;
      return v ? "?v=" + encodeURIComponent(v) : "";
    } catch { return ""; }
  }

  /** A pane page's forwarded press ({romp:"paneGrab"}, pane-grab.ts): the page captured the pointer on its own empty
   *  background and hands the press here; the shell arms exactly the drag the ring arms, hearing the frame's captured
   *  moves through its window as it does for Option-drag. */
  private onGrabMessage(e: MessageEvent): void {
    const m = e.data;
    if (!m || !this.on) return;
    if (m.romp === "paneGrabEnd") {
      // the page's release: a press the shell heard only after the pointer was already up (its message task ran after
      // the pointerup, before this engine's own listeners existed) must not stand with no button held
      if (this.press && !this.press.armed) this.cancelPress();
      return;
    }
    if (m.romp !== "paneGrab" || this.press || this.div) return;
    const f = this.allFrames().find((x) => x.contentWindow === e.source);
    if (!f || !f.contentWindow) return;
    const paneNode = f.closest(".pane") as HTMLElement | null;
    if (!paneNode || !this.lay || !has(this.lay.tree, paneNode.id)) return;
    const b = f.getBoundingClientRect();
    this.beginPress(paneNode.id, f, f.contentWindow, b.left + f.clientLeft + Number(m.clientX || 0), b.top + f.clientTop + Number(m.clientY || 0));
  }

  private onKey(e: KeyboardEvent): void {
    if (!this.on) return;
    if (e.key === "Escape" && this.press && this.press.armed) { e.preventDefault(); e.stopPropagation(); this.cancelPress(); return; }
    if (e.key === "Alt") this.setAlt(e.type === "keydown");
  }
  /** Option/Alt held: the open hand over every pane, content included (the cursor is inherited, so each pane
   *  document's root carries it and controls with a cursor of their own keep theirs). */
  private setAlt(on: boolean): void {
    if (this.altOn === on) return;
    this.altOn = on;
    document.body.classList.toggle(ALT_CLASS, on && this.on);
    for (const f of this.allFrames()) {
      try { const d = f.contentDocument; if (d) d.documentElement.style.cursor = on && this.on ? "grab" : ""; } catch { /* gone */ }
    }
  }

  // ── the drag arm ─────────────────────────────────────────────────────────────────────────────────────
  private onShellPress(e: PointerEvent): void {
    if (!this.on || this.press || this.div) return;
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    if (t.classList.contains("pd-div")) return;   // its own handler
    const pane = t.classList.contains("pane") ? t : null;   // the ring: the pane element itself, never its iframe
    if (!pane || !pane.id || !this.lay || !has(this.lay.tree, pane.id)) return;
    if (!grabbable({ button: e.button, alt: e.altKey, onRing: true, onTopRun: false, onControl: false })) return;
    e.preventDefault();
    // CAPTURE the pointer on the pane: without it the moves over an iframe (a hair below the ring) go to that
    // iframe's document and the shell hears nothing until the pointer crosses a gap (a served find, 2026-09-19)
    try { pane.setPointerCapture(e.pointerId); } catch { /* an old engine: the moves still arrive over the gaps */ }
    this.beginPress(pane.id, null, window, e.clientX, e.clientY);
  }

  private onFramePress(e: PointerEvent, f: HTMLIFrameElement): void {
    if (!this.on || this.press || this.div) return;
    const paneNode = f.closest(".pane") as HTMLElement | null;
    const pane = paneNode && this.lay && has(this.lay.tree, paneNode.id) ? paneNode.id : null;
    if (!pane) return;
    const t = e.target;
    const onControl = !!(t instanceof Element && t.closest(CONTROL_SEL));
    const onTopRun = !!(t instanceof Element && t.closest(TOP_RUN_SEL));
    if (!grabbable({ button: e.button, alt: e.altKey, onRing: false, onTopRun, onControl })) return;
    const b = f.getBoundingClientRect();
    if (e.altKey) e.preventDefault();   // Option-drag: no text selection starts under the press
    const win = f.contentWindow;
    if (!win) return;
    try { if (t instanceof Element) t.setPointerCapture(e.pointerId); } catch { /* as above */ }   // the moves stay with this document
    this.beginPress(pane, f, win, b.left + f.clientLeft + e.clientX, b.top + f.clientTop + e.clientY);
  }

  private beginPress(pane: PaneId, frame: HTMLIFrameElement | null, win: Window, x: number, y: number): void {
    this.press = { pane, frame, win, x0: x, y0: y, armed: false, zone: null };
    const mv = (ev: Event) => this.onPressMove(ev as PointerEvent, win, frame);
    const up = (ev: Event) => this.onPressUp(ev as PointerEvent, win, frame);
    const cancel = () => this.cancelPress();
    win.addEventListener("pointermove", mv, true); win.addEventListener("pointerup", up, true); win.addEventListener("pointercancel", cancel, true);
    if (win !== window) { window.addEventListener("pointermove", mv, true); window.addEventListener("pointerup", up, true); }
    this.pressOff = () => {
      win.removeEventListener("pointermove", mv, true); win.removeEventListener("pointerup", up, true); win.removeEventListener("pointercancel", cancel, true);
      if (win !== window) { window.removeEventListener("pointermove", mv, true); window.removeEventListener("pointerup", up, true); }
    };
  }

  /** The pointer in SHELL viewport px: an event from a pane document is offset by its frame's box. */
  private shellPoint(e: PointerEvent, win: Window, frame: HTMLIFrameElement | null): { x: number; y: number } {
    if (win === window || !frame || e.view === window) return { x: e.clientX, y: e.clientY };
    const b = frame.getBoundingClientRect();
    return { x: b.left + frame.clientLeft + e.clientX, y: b.top + frame.clientTop + e.clientY };
  }

  private onPressMove(e: PointerEvent, win: Window, frame: HTMLIFrameElement | null): void {
    const p = this.press;
    if (!p) return;
    if (e.buttons === 0) { this.cancelPress(); return; }   // no button held: a release this engine never heard; nothing may arm or drop on it
    const pt = this.shellPoint(e, win, frame);
    if (!p.armed) {
      if (!crossedSlop(pt.x - p.x0, pt.y - p.y0)) return;
      p.armed = true;
      document.body.classList.add(DRAG_CLASS);
      try { (frame && frame.contentDocument ? frame.contentDocument : document).getSelection()?.removeAllRanges(); } catch { /* fine */ }
      try { if (frame && frame.contentDocument) frame.contentDocument.documentElement.style.userSelect = "none"; } catch { /* fine */ }
    }
    e.preventDefault();
    this.trackZone(pt, p);
  }

  /** The zone under the pointer and the outline for it, from rects read NOW. */
  private trackZone(pt: { x: number; y: number }, p: Press): void {
    const rects = this.viewportRects();
    const strips = this.strips();
    const zone = zoneAt(rects, pt, { self: p.pane, payload: "pane" as Payload, strips });
    p.zone = zone;
    const o = this.outline;
    if (!o) return;
    o.classList.add("on"); o.classList.remove("free", "refused");
    let r: Rect | null = zone ? landingRect(rects, zone, strips) : null;
    if (zone && zone.strip) { o.classList.add("refused"); o.textContent = "A pane is not a tab"; }
    else if (zone) o.textContent = paneTitle(p.pane);
    else { o.classList.add("free"); o.textContent = paneTitle(p.pane); r = { x: pt.x - 80, y: pt.y - 40, w: 160, h: 80 }; }
    if (r) { const rr = roundRect(r); o.style.left = rr.x + "px"; o.style.top = rr.y + "px"; o.style.width = rr.w + "px"; o.style.height = rr.h + "px"; }
  }

  private onPressUp(e: PointerEvent, win: Window, frame: HTMLIFrameElement | null): void {
    const p = this.press;
    if (!p) return;
    if (!p.armed) { this.cancelPress(); return; }   // under the slop: a click, a selection; nothing lifted, nothing changes
    e.preventDefault();
    const pt = this.shellPoint(e, win, frame);
    this.trackZone(pt, p);   // re-read the rects right before the release: the zone is decided on what is on screen now
    const zone = p.zone;
    this.cancelPress();
    if (!zone || zone.strip || !this.lay) return;   // no zone, or a strip (a pane is not a tab): a cancel
    this.drop(p.pane, zone.target, zone.edge as Edge);
  }

  private cancelPress(): void {
    const p = this.press;
    if (this.pressOff) { this.pressOff(); this.pressOff = null; }
    this.press = null;
    document.body.classList.remove(DRAG_CLASS);
    if (this.outline) { this.outline.classList.remove("on", "free", "refused"); this.outline.textContent = ""; }
    try { if (p && p.frame && p.frame.contentDocument) p.frame.contentDocument.documentElement.style.userSelect = ""; } catch { /* fine */ }
  }

  /** The drop: a pure tree move, persisted, then one recompute. A refusal (the only pane, a target gone in the
   *  meantime) says why through the shell's notice and changes nothing. */
  private drop(pane: PaneId, target: PaneId, edge: Edge): void {
    if (!this.lay) return;
    let tree;
    try { tree = move(this.lay.tree, pane, target, edge); }
    catch (err) { this.notify(String((err as Error).message || err)); return; }
    this.lay = { v: 1, tree, parked: this.lay.parked };
    this.persist();
    this.render();
  }

  private notify(text: string): void {
    const w = window as any;
    try { if (w.__rompNotify) w.__rompNotify("warn", text); } catch { /* no notice surface */ }
  }

  // ── the dividers ─────────────────────────────────────────────────────────────────────────────────────
  private onDivPress(e: PointerEvent, d: HTMLElement): void {
    if (!this.on || this.press || this.div || e.button !== 0) return;
    const edge = this.divEdges.get(d);
    if (!edge) return;
    e.preventDefault();
    // a horizontal edge moves a deferred landing line (a grow write reflows every pane document, so it lands once,
    // at release); a vertical edge, and the band's edge, write live (a height trade inside one column is cheap)
    const live = edge.dir === "col";
    this.div = { edge, x0: e.clientX, y0: e.clientY, live, last: 0 };
    document.body.classList.add(RESIZE_CLASS);
    const mv = (ev: Event) => this.onDivMove(ev as PointerEvent);
    const up = () => this.endDiv(true);
    window.addEventListener("pointermove", mv, true); window.addEventListener("pointerup", up, true);
    this.divOff = () => { window.removeEventListener("pointermove", mv, true); window.removeEventListener("pointerup", up, true); };
    if (!live && this.ghost && this.col) {
      const rb = this.col.getBoundingClientRect(), r = edge.rect;
      this.ghost.style.top = rb.top + r.y + "px"; this.ghost.style.height = r.h + "px"; this.ghost.style.width = GUTTER + "px";
      this.ghost.style.left = rb.left + r.x + "px"; this.ghost.style.display = "block";
    }
  }

  private splitAt(path: number[]): { ratios: number[] } | null {
    if (!this.lay) return null;
    let n: any = this.lay.tree;
    for (const i of path) { if (!n || !n.kids || !n.kids[i]) return null; n = n.kids[i]; }
    return n && n.kids ? (n as { ratios: number[] }) : null;
  }

  private clampDelta(edge: EdgeRect, px: number): number {
    // neither side of the edge drops under min(120 px, a quarter of the pair): the shipped clamp, in px
    const split = this.splitAt(edge.path);
    if (!split) return 0;
    const a = edge.avail * split.ratios[edge.i], b = edge.avail * split.ratios[edge.i + 1];
    const mn = Math.min(MIN_PX, (a + b) * 0.25);
    const lo = mn - a, hi = b - mn;
    if (lo > hi) return 0;
    return px < lo ? lo : px > hi ? hi : px;
  }

  private minFrac(edge: EdgeRect): number { return Math.min(0.25, MIN_PX / Math.max(1, edge.avail)); }

  private onDivMove(e: PointerEvent): void {
    const d = this.div;
    if (!d) return;
    e.preventDefault();
    const raw = d.edge.dir === "row" ? e.clientX - d.x0 : e.clientY - d.y0;
    if (d.edge.fixed) {
      // the band's edge: its height in px follows the pointer, through the shipped --tl (the observer re-renders)
      if (!this.col) return;
      const cb = this.col.getBoundingClientRect(), box = this.box();
      if (!box) return;
      const bottom = cb.top + box.y + box.h;
      const px = Math.max(BAND_MIN, Math.min(Math.round(window.innerHeight * 0.7), Math.round(bottom - e.clientY)));
      this.col.style.setProperty("--tl", px + "px");
      return;
    }
    const px = this.clampDelta(d.edge, raw);
    if (d.live) {
      if (!this.lay || px === d.last) return;
      const step = px - d.last; d.last = px;
      const tree = resize(this.lay.tree, d.edge.path, d.edge.i, step / d.edge.avail, this.minFrac(d.edge));
      this.lay = { ...this.lay, tree }; this.render();
      // the edge list was rebuilt by render: keep this drag on the same edge (same path and index)
      const same = Array.from(this.divEdges.values()).find((x) => x.dir === d.edge.dir && x.i === d.edge.i && x.path.join(",") === d.edge.path.join(","));
      if (same) d.edge = same;
    } else {
      d.last = px;
      if (this.ghost && this.col) { const cb = this.col.getBoundingClientRect(); this.ghost.style.left = cb.left + d.edge.rect.x + px + "px"; }
    }
  }

  private endDiv(commit: boolean): void {
    const d = this.div;
    if (this.divOff) { this.divOff(); this.divOff = null; }
    this.div = null;
    document.body.classList.remove(RESIZE_CLASS);
    if (this.ghost) this.ghost.style.display = "none";
    if (!d) return;
    if (commit && !d.live && !d.edge.fixed && this.lay && d.last) {
      const tree = resize(this.lay.tree, d.edge.path, d.edge.i, d.last / d.edge.avail, this.minFrac(d.edge));
      this.lay = { ...this.lay, tree };
    }
    if (commit) this.persist();
    this.render();
  }
}

// boot only in a browser TOP document (guards keep an import in node inert, so the pure exports are testable)
if (typeof window !== "undefined" && typeof document !== "undefined" && (!window.parent || window.parent === window)) {
  const engine = new Engine();
  const apply = () => engine.apply();
  if (document.body) apply(); else document.addEventListener("DOMContentLoaded", apply);
  // re-apply on a gear save in this document, and on a write from another tab (the shell's settings idiom)
  window.addEventListener("romp:settings", apply);
  window.addEventListener("storage", (e) => { if (!e || !e.key || e.key === SETTINGS_KEY) apply(); });
}
