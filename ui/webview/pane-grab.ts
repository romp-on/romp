// THE PANE GRAB DETECTOR (plans/pane-docking.md section 3, the empty space inside a pane): a small bundle the
// shell's docking engine (panedock-main.ts) injects into every pane document while the kit is on, the same road
// as its cursor stylesheet. The shell can see only its own chrome; a pane's content is an iframe whose pointer
// events never reach the parent. So the inner page detects a press on its OWN empty background (the feed's list
// and columns where no card is under the pointer, the sessions band's SVG outside every lane and mark, the
// outline's list below its rows, the Files pane's empty state) and forwards it: it CAPTURES the pointer on the
// pressed element, so the moves keep flowing to this document once the pointer leaves the iframe, and posts
// {romp:"paneGrab", clientX, clientY, pointerId} to the shell, which arms exactly the drag it arms on the ring; the
// release posts {romp:"paneGrabEnd", pointerId}, so a press the shell hears only after the pointer is already up
// (its message task can run after the pointerup) never leaves a press standing with no button held.
// The chat is the deliberate exception: a press-drag over the transcript is a text selection, so its grab surface
// stays the strip's empty run (the shell's own wiring). The open hand shows over exactly the empty targets: a body
// class toggled by the target under the pointer, so a card's text never wears a hand it cannot honour.
//
// Inert unless the page's body carries `pane-docking` (the shell sets it while the kit is on and removes it when
// the kit goes off; going off also removes this script's tag, its style and the window global, so the page's DOM is
// what it was, while the listeners stay bound once and answer only to the class). The press is NOT prevented: a
// prevented pointerdown suppresses the mousedown that moves focus (the band's wrap takes the keyboard on a press, a
// field blurs on a click beside it); a selection is stopped by user-select on the hovered empty background instead.
// The pure decision (`emptyPress`) is node-tested; the listeners are the DOM glue.

export const GRAB_HOVER_CLASS = "pd-grab-hover";
export const KIT_CLASS = "pane-docking";
export const STYLE_ID = "pd-grab-css";

/** A pane page's empty background: the press must land on one of THESE elements itself (a container, not a card
 *  or a row inside it). Keyed by the page's app name (`window.__rompApp`). The chat is absent on purpose. */
export const EMPTY_BY_APP: Record<string, string> = {
  feed: "body, #feed-list, #feed-cols, .feed-cols, .feed-col, .feed-col-list, #feed-foot",
  timeline: "body, #host, .romp-tl-wrap, svg",
  fleet: "body, #fleet-list, #fleet-foot",
  files: "body, #files-empty",
};

/** Anything a press yields to, wherever it sits: controls, links, fields, cards, rows, chips, marks. */
export const CONTROL_SEL = "a, button, input, textarea, select, label, summary, [role], [contenteditable], [draggable=true], [data-act], [data-sid], [data-id], .fitem, .ftask-group, .fcard, .card, .chip, .tag-chip, svg *";

/** The subset of an Element the decision reads, so a node test can hand in a fake. */
export interface TargetLike { matches(sel: string): boolean; closest(sel: string): unknown }

/** Whether a press on `target` in the page for `app` is a press on the page's empty background. */
export function emptyPress(app: string, target: TargetLike | null): boolean {
  const sel = EMPTY_BY_APP[app];
  if (!sel || !target) return false;
  try {
    if (target.closest(CONTROL_SEL)) return false;
    return target.matches(sel);
  } catch {
    return false;
  }
}

/** Whether a pointer press may be forwarded: the primary button, no modifier (Option is the shell's own path). */
export function forwardable(e: { button: number; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }): boolean {
  return e.button === 0 && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey;
}

/** The style the hovered empty background wears: the open hand, and no selection anchored under a press there. */
export function ensureStyle(doc: Document): void {
  if (doc.getElementById(STYLE_ID)) return;
  const st = doc.createElement("style"); st.id = STYLE_ID;
  st.textContent = `body.${KIT_CLASS}.${GRAB_HOVER_CLASS}{cursor:grab;user-select:none;-webkit-user-select:none}`;
  (doc.head || doc.documentElement).appendChild(st);
}

export function install(win: Window, app: string): void {
  const doc = win.document;
  if (!EMPTY_BY_APP[app]) return;
  const w = win as any;
  const expose = () => { w.__rompPaneGrab = { app, empty: (el: Element | null) => emptyPress(app, el) }; };   // read-only, for the served pins
  ensureStyle(doc);
  if (w.__rompPaneGrabWired) { expose(); return; }   // re-injected after the kit went off and on: the listeners are bound once
  w.__rompPaneGrabWired = true;
  const on = () => !!(doc.body && doc.body.classList.contains(KIT_CLASS));
  doc.addEventListener("pointermove", (e) => {
    const want = on() && emptyPress(app, e.target as TargetLike | null);
    if (doc.body && doc.body.classList.contains(GRAB_HOVER_CLASS) !== want) doc.body.classList.toggle(GRAB_HOVER_CLASS, want);
  }, { capture: true, passive: true });
  doc.addEventListener("pointerleave", () => { if (doc.body) doc.body.classList.remove(GRAB_HOVER_CLASS); }, true);
  const post = (m: Record<string, unknown>) => { try { win.parent.postMessage(m, "*"); } catch { /* no parent */ } };
  doc.addEventListener("pointerdown", (e) => {
    if (!on() || !forwardable(e) || !emptyPress(app, e.target as TargetLike | null)) return;
    const t = e.target as Element;
    try { t.setPointerCapture(e.pointerId); } catch { /* an old engine: the moves still arrive over the gaps */ }
    // the press is not prevented (focus follows the mousedown it would suppress); the hover style above anchors no
    // selection, and the shell stops the rest once the slop is crossed
    const id = e.pointerId;
    const end = (ev: Event) => {
      const pe = ev as PointerEvent;
      if (pe.pointerId !== undefined && pe.pointerId !== id) return;
      t.removeEventListener("pointerup", end, true); t.removeEventListener("pointercancel", end, true); t.removeEventListener("lostpointercapture", end, true);
      post({ romp: "paneGrabEnd", app, pointerId: id });
    };
    t.addEventListener("pointerup", end, true); t.addEventListener("pointercancel", end, true); t.addEventListener("lostpointercapture", end, true);
    post({ romp: "paneGrab", app, clientX: e.clientX, clientY: e.clientY, pointerId: id });
  }, true);
  expose();
}

// boot in a pane document (a child frame); an import in node stays inert
if (typeof window !== "undefined" && typeof document !== "undefined" && window.parent && window.parent !== window) {
  const app = String((window as any).__rompApp || "");
  if (document.body) install(window, app); else document.addEventListener("DOMContentLoaded", () => install(window, app));
}
