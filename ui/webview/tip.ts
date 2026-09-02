// ONE tooltip treatment for every webview surface (2026-08-28). Six custom tooltip mechanisms had
// grown, no two sharing background/radius/font/shadow; this is the shared behavior half — the dress
// half is the `.romp-tip` rule, byte-mirrored in styles.css and feed.css (like .ctx-menu), tokens
// only so it themes for free.
//
// Behavioral spec (the contract that won past fights): INSTANT in, small grace out, anchor-relative
// with flip + clamp. Concretely:
//   - mouseenter shows the tip immediately (no intent delay — a hover IS the intent);
//   - mouseleave hides it after a TIP_GRACE_MS grace, canceled by re-enter, so sweeping across a
//     gap never flickers it;
//   - opts.hoverable keeps it alive while the pointer is INSIDE the tip (the sessions-pane hover
//     card pattern) — otherwise the tip is pointer-events:none and can never eat a click;
//   - positioned below the anchor (opts.place: "above" flips the preference — the feed age stamp
//     sits at a card's bottom edge, so its story reads above), flipping to the other side when the
//     preferred side lacks room, clamped 8px into the viewport;
//   - hidden on scroll / click / anchor-gone. Surfaces that re-render on every kernel push update
//     cards IN PLACE, so the hovered anchor usually survives — pruneTip() hides only when the
//     anchor was actually torn out of the DOM, where its mouseleave can never fire (the feed
//     age-tip's 1s-vanish fix, 2026-07-27 — see feed.css).
//
// One DOM node per document (class `romp-tip`), repopulated by the caller's render(el) on each
// show, so the tip is rebuilt per hover and click-safe by construction.

export const TIP_GRACE_MS = 160;   // mouseleave → hide grace; re-enter (anchor or hoverable tip) cancels it

export interface TipOpts {
  hoverable?: boolean;             // pointer inside the tip keeps it alive (scrollable/rich tips)
  place?: "below" | "above";       // preferred side of the anchor (default "below"); flips when out of room
}

let tipEl: HTMLElement | null = null;
let tipAnchor: HTMLElement | null = null;   // the anchor the tip is up for — pruneTip checks it survived a render
let hideT: number | undefined;

function ensureEl(): HTMLElement {
  if (!tipEl || !tipEl.isConnected) {
    tipEl = document.createElement("div");
    tipEl.className = "romp-tip";
    tipEl.style.display = "none";
    // a hoverable tip keeps itself alive: entering it cancels the grace, leaving re-arms it
    tipEl.addEventListener("mouseenter", () => { if (hideT) { clearTimeout(hideT); hideT = undefined; } });
    tipEl.addEventListener("mouseleave", scheduleHide);
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

function hideTip(): void {
  if (hideT) { clearTimeout(hideT); hideT = undefined; }
  tipAnchor = null;
  if (tipEl) tipEl.style.display = "none";
}

function scheduleHide(): void {
  if (hideT) clearTimeout(hideT);
  hideT = window.setTimeout(hideTip, TIP_GRACE_MS);
}

// Hide only when the hovered anchor was torn out of the DOM (its mouseleave can never fire).
// Surfaces that rebuild on every kernel push call this after each render.
export function pruneTip(): void { if (tipAnchor && !tipAnchor.isConnected) hideTip(); }

function showTip(anchor: HTMLElement, render: (el: HTMLElement) => void, opts?: TipOpts): void {
  if (hideT) { clearTimeout(hideT); hideT = undefined; }
  const tip = ensureEl();
  tipAnchor = anchor;
  tip.replaceChildren();
  render(tip);
  if (!tip.childNodes.length) { hideTip(); return; }
  tip.style.pointerEvents = opts?.hoverable ? "auto" : "none";
  tip.style.display = "block";
  // measure off-screen, then place anchor-relative with flip + an 8px viewport clamp
  tip.style.left = "0px"; tip.style.top = "-9999px";
  const rc = anchor.getBoundingClientRect();
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(rc.left, window.innerWidth - w - 8)) + "px";
  const below = rc.bottom + 6, above = rc.top - h - 6;
  const top = (opts?.place === "above")
    ? (above >= 8 ? above : below)
    : (below + h <= window.innerHeight - 8 ? below : Math.max(8, above));
  tip.style.top = top + "px";
}

// document-level dismissals, installed once: a click navigates / opens something and a scroll moves
// the anchor — both drop the tip at once (event-based, no timers)
let dismissWired = false;
function wireDismiss(): void {
  if (dismissWired) return;
  dismissWired = true;
  document.addEventListener("click", hideTip, true);
  document.addEventListener("scroll", hideTip, true);
}

// Wire a styled tooltip onto `anchor`: instant show on mouseenter (render(el) populates the shared
// node fresh each time), grace-out on mouseleave. IDEMPOTENT per anchor: surfaces that update cards
// in place re-wire on every kernel push, so a repeat call just swaps in the fresh render closure —
// the listeners are installed once.
export function wireTip(anchor: HTMLElement, render: (el: HTMLElement) => void, opts?: TipOpts): void {
  wireDismiss();
  const a = anchor as any;
  // a styled tip REPLACES the native one — never both. Stripped here (not just in setTip) so every
  // rich tip gets it, and on every re-wire, since surfaces re-title anchors on each push (2026-09-02).
  anchor.removeAttribute("title");
  a._rompTipRender = render; a._rompTipOpts = opts;
  if (a._rompTipWired) return;
  a._rompTipWired = true;
  anchor.addEventListener("mouseenter", () => showTip(anchor, a._rompTipRender, a._rompTipOpts));
  anchor.addEventListener("mouseleave", () => { if (tipAnchor === anchor) scheduleHide(); });
  // keyboard parity (PR #763 item 10): focus shows, blur hides — a styled tip is never mouse-only
  anchor.addEventListener("focus", () => showTip(anchor, a._rompTipRender, a._rompTipOpts));
  anchor.addEventListener("blur", () => { if (tipAnchor === anchor) scheduleHide(); });
}

// One-line convenience for the title=-replacement spots: the tip text can be updated per push
// without re-wiring (the feed's badges re-title on every update). Removes any native title so the
// browser tooltip never doubles the styled one.
export function setTip(anchor: HTMLElement, text: string): void {
  (anchor as any)._tipText = text;
  anchor.removeAttribute("title");
  wireTip(anchor, (tip) => {
    const t = String((anchor as any)._tipText || "");
    if (!t) return;   // nothing to say → showTip sees an empty tip and stands down
    for (const line of t.split("\n")) {
      const d = document.createElement("div");
      d.textContent = line;
      tip.appendChild(d);
    }
  });
}
