// Scrollbars follow the platform (the user 2026-09-25, on macOS in the light theme, who could not see the chat's scrollbar
// even while scrolling and wants the platform's show-while-scrolling bars). Any ::-webkit-scrollbar styling makes WebKit and
// Blink drop the platform's overlay scrollbars for classic, always-on ones, so romp's styled bars overrode a macOS "show
// scroll bars when scrolling" preference on every scroller of the page. This module measures what the platform draws and
// puts `overlay-scrollbars` on the root where it overlays; every custom scrollbar rule in the sheets is scoped under
// html:not(.overlay-scrollbars), so the class hands the page back to the platform's own bars (scrollbar-overlay.test.ts).
// Where the platform draws classic bars (Linux, Windows, macOS set to always show them) the class stays off and the styled
// bars stand, in theme tokens.

export const OVERLAY_SCROLLBARS = "overlay-scrollbars";

/** The width the platform's own vertical scrollbar takes in a box: 0 where it overlays the content. Measured on a probe no
 *  sheet reaches, a scroller inside a shadow root (the document's ::-webkit-scrollbar rules do not match into it, and the one
 *  inherited scrollbar property is reset on it). null when the probe got no box, which is what a document that is not laid
 *  out gives (a pane inside a display:none iframe measures 0 by 0 and would otherwise read as overlay). */
export function nativeScrollbarWidth(doc: Document): number | null {
  const host = doc.createElement("div");
  host.style.cssText = "position:absolute;top:-9999px;left:-9999px;visibility:hidden;pointer-events:none";
  const root: Node = typeof host.attachShadow === "function" ? host.attachShadow({ mode: "open" }) : host;
  const probe = doc.createElement("div");
  probe.style.cssText = "display:block;width:100px;height:100px;overflow:scroll;scrollbar-color:auto;scrollbar-width:auto";
  root.appendChild(probe);
  doc.documentElement.appendChild(host);   // the root, not the body: a page may observe the body's children (files.ts)
  const outer = probe.offsetWidth, inner = probe.clientWidth;
  host.remove();
  return outer > 0 ? outer - inner : null;
}

/** Set or clear the root class from one measurement: true for overlay, false for classic, null (the class untouched) when
 *  the measurement could say nothing. */
export function applyOverlayScrollbars(doc: Document, width: number | null): boolean | null {
  if (width === null) return null;
  const overlay = width === 0;
  doc.documentElement.classList.toggle(OVERLAY_SCROLLBARS, overlay);
  return overlay;
}

const watched = new WeakSet<object>();

/** Measure now, and again at each moment the platform's preference may have changed under the page: the window regaining
 *  focus (coming back from System Settings) and the page coming back into view. While no measurement has said anything yet
 *  (a pane hidden at boot), the window's resize measures too: it is the event a hidden pane gets when it is first laid out.
 *  Events only, no polling. Idempotent per document. */
export function watchOverlayScrollbars(doc: Document, win: Window, measure: (d: Document) => number | null = nativeScrollbarWidth): void {
  if (watched.has(doc)) return;
  watched.add(doc);
  let known = false;
  const run = () => { if (applyOverlayScrollbars(doc, measure(doc)) !== null) known = true; };
  run();
  win.addEventListener("focus", run);
  doc.addEventListener("visibilitychange", () => { if (doc.visibilityState === "visible") run(); });
  win.addEventListener("resize", () => { if (!known) run(); });
}
