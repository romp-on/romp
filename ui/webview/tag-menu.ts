// The SHARED tag-lens menu (the user 2026-08-25): one component every webview surface mounts —
// the outline pane, the chat tab strip, the feed's local filter. (The timeline inlines the same
// behavior in MENU_STYLE: it may live in Obsidian's document and loads no modules.) The menu is
// multi-select toggles on ONE surface's lens: All a plain exclusive pick, (no tags) and every
// name-keyed union tag toggling with the ✓ per selected row, the menu staying open across toggles
// (a settings panel, not a command). A CAPTIONED DIVIDER says which surface the selection governs
// ("filters these tabs") — the shared idiom, sub-line scale. One management entry, "Configure
// tags…", when the surface offers a route to the dialog.
//
// CROSS-PANE DISMISSAL is built in (the user's bug): sibling panes' pointer events never reach
// this document, so every pane WRITES a pointerdown echo (romp:menu-echo — the color-echo idiom)
// and every open menu LISTENS via the storage event, which fires only in OTHER same-origin panes:
// exactly the gap the local document closers can't cover. Mounting surfaces inherit the fix free.
//
// SUBMENU-LESS by design today; the caret/side rule for menus that DO expand lives with the
// model-version submenus: carets always face right (▸), expansion prefers the right side and
// falls left only when the right edge would clip (measured, never assumed).
import { TagUnion } from "./session-views";
import { TagLens, lensAll, toggleLens, lensChips } from "./tag-lens";

export interface TagMenuOpts {
  lens: () => TagLens;                       // the surface's current selection (re-read per repaint)
  unions: () => TagUnion[];                  // the name-keyed union rows (re-read per repaint)
  onApply: (l: TagLens, done: boolean) => void;  // done=true → the pick closes the menu (All)
  onConfigure?: () => void;                  // the one management entry, when the surface has a route
}

let echoInstalled = false;
/** Every pane writes the pointerdown echo ONCE per document — sibling panes' open menus close on it.
 *  Installed AT MODULE LOAD by the guard below, never lazily (the user 2026-08-26: a menu opened
 *  from the sessions panel stood through clicks in the chat — the chat imports this module but had
 *  never opened a menu, so its document held no writer). Exported for documents that mount no tag
 *  menu at all (the shell page's palette-main) to compose the same writer explicitly. */
export function installMenuEcho(): void {
  if (echoInstalled) return;
  echoInstalled = true;
  document.addEventListener("pointerdown", () => {
    try { localStorage.setItem("romp:menu-echo", JSON.stringify({ t: Date.now() })); } catch { /* storage blocked */ }
  }, true);
}

let openMenu: HTMLElement | null = null;
export function closeTagMenu(): void {
  openMenu?.remove();
  openMenu = null;
}
// module-level closers, guarded: the model half of this module (and its constants) is importable
// from non-DOM contexts (the node test runner) — only a real document wires the listeners
if (typeof document !== "undefined") {
  installMenuEcho();   // the WRITER rides every bundle at load — a pane must broadcast before it ever opens a menu
  document.addEventListener("click", () => closeTagMenu());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeTagMenu(); });
  try {
    window.addEventListener("storage", (e) => { if (e.key === "romp:menu-echo" && e.newValue) closeTagMenu(); });
  } catch { /* no storage events (foreign host) — local closers still apply */ }
}

/** Open (or toggle shut) the lens menu anchored under `anchor`. The ctx-family skin — the chat
 *  pane's .ctx-menu is the reference spec (CLAUDE.md menu vocabulary). */
export function openTagMenu(anchor: HTMLElement, opts: TagMenuOpts): void {
  const reopen = !!openMenu && openMenu.dataset.tagMenu === "1";
  closeTagMenu();
  if (reopen) return;
  const menu = document.createElement("div");
  menu.dataset.tagMenu = "1";
  menu.setAttribute("style",
    "position:fixed;z-index:1001;min-width:180px;padding:4px;background:#252526;" +
    "border:1px solid rgba(255,255,255,0.12);border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,0.35);" +
    "font-size:12px;line-height:1.4;color:#cccccc;user-select:none;");
  menu.addEventListener("click", (e) => e.stopPropagation());
  const build = () => {
    menu.textContent = "";
    const lens = opts.lens();
    // (the scope caption retired 2026-08-25 — the user: the button tooltip already names the
    // surface, so it is the ONE scope carrier and the menu opens straight onto its rows)
    const row = (label: string, current: boolean, dot?: string | null, dim?: boolean) => {
      const r = document.createElement("div");
      r.setAttribute("style", "padding:4px 22px 4px 8px;border-radius:4px;cursor:pointer;position:relative;white-space:nowrap;"
        + (dim ? "opacity:0.85;" : ""));
      if (dot) {
        const d = document.createElement("span");
        d.setAttribute("style", "display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:" + dot + ";");
        r.appendChild(d);
      }
      r.appendChild(document.createTextNode(label));
      if (current) {
        const c = document.createElement("span");
        c.textContent = "✓";
        c.setAttribute("style", "position:absolute;right:6px;top:50%;transform:translateY(-50%);"
          + "background:#1EA1EB;color:#fff;border-radius:50%;width:13px;height:13px;font-size:9px;"
          + "font-weight:900;display:inline-flex;align-items:center;justify-content:center;line-height:1;");
      r.appendChild(c);
      }
      r.addEventListener("mouseenter", () => { r.style.background = "rgba(255,255,255,0.09)"; });
      r.addEventListener("mouseleave", () => { r.style.background = "transparent"; });
      menu.appendChild(r);
      return r;
    };
    row("All", lensAll(lens)).addEventListener("click", () => opts.onApply({ all: true }, true));
    row("(no tags)", !lensAll(lens) && !!lens.none)
      .addEventListener("click", () => { opts.onApply(toggleLens(lens, "none"), false); build(); });
    for (const u of opts.unions())
      row(u.name, !lensAll(lens) && (lens.tags || []).includes(u.name), u.color || "#9aa0a6")
        .addEventListener("click", () => { opts.onApply(toggleLens(lens, { tag: u.name }), false); build(); });
    if (opts.onConfigure) {
      const s = document.createElement("div");
      s.setAttribute("style", "height:1px;margin:4px 6px;background:rgba(255,255,255,0.12);");
      menu.appendChild(s);
      row("Configure tags…", false, null, true).addEventListener("click", () => { closeTagMenu(); opts.onConfigure!(); });
    }
  };
  build();
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth || 200;
  menu.style.left = Math.max(6, Math.min(Math.round(r.left), window.innerWidth - mw - 8)) + "px";
  const mh = menu.offsetHeight || 0;
  menu.style.top = (r.bottom + 4 + mh > window.innerHeight - 8 ? Math.max(8, Math.round(r.top) - mh - 4) : Math.round(r.bottom + 4)) + "px";
  openMenu = menu;
}

/** The shared tag-icon button (the user chose a tag glyph): identical across surfaces. It wears
 * THE BUTTON OUTLINE (the user 2026-08-25, round two: the bare glyph read weird next to the feed's
 * dressed buttons) — the feed word-button's box, stated in the same literals the feed's classes
 * resolve to (--card-border, 6px radius, the #feed-foot 1px 9px padding), so a chat/outline mount
 * computes equal to the feed instance by value, not by shared class (the 678 lesson). */
export function tagMenuButton(title: string, open: (btn: HTMLElement) => void): HTMLElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("style", "background:transparent;border:1px solid " + TAG_BTN_BORDER + ";"
    + "border-radius:6px;padding:4px 6px;cursor:pointer;color:#9aa0a6;display:inline-flex;align-items:center;");
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none">'
    + '<path d="M2 7.5 L7.5 2.5 H14 V9 L8.5 14 Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>'
    + '<circle cx="11" cy="5.5" r="1.2" fill="currentColor"/></svg>';
  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); open(btn); });
  btn.addEventListener("click", (e) => e.stopPropagation());   // the click-and-hold rule: swallow the opener's own click
  return btn;
}

// THE BUTTON CONVENTION (the user 2026-08-25): at rest (All) the tag icon is GRAY and stands
// alone; narrowed, it wears the ACCENT and the chips of everything selected render beside it —
// each tag in its color, the no-tags bucket as its own chip, a dim ✕ per chip unselecting that
// one pick. One renderer, so every mount is identical by construction; the feed's footer keeps
// its class mechanics (mode: "class" — its .on/.--dim values are pinned equal to these literals).
export const TAG_BTN_GRAY = "#9aa0a6";
export const TAG_BTN_ACCENT = "#9cd2ff";   // the romp accent (--accent) — pinned equal in feed.css/styles.css
export const TAG_BTN_BORDER = "rgba(255,255,255,0.10)";   // the feed's --card-border, stated by value
export const TAG_BTN_WASH = "rgba(156,210,255,0.12)";     // the feed .on's faint accent wash, ditto

export function syncTagFilter(btn: HTMLElement, chipsHost: HTMLElement,
                              lens: TagLens, unions: { name: string; color?: string | null; members: string[]; }[],
                              onApply: (l: TagLens) => void,
                              mode: "inline" | "class" = "inline"): void {
  const narrowed = !lensAll(lens);
  if (mode === "class") btn.classList.toggle("on", narrowed);
  else {
    // inline mode mirrors the feed's .on by VALUE: accent glyph + accent border + the faint wash
    btn.style.color = narrowed ? TAG_BTN_ACCENT : TAG_BTN_GRAY;
    btn.style.borderColor = narrowed ? TAG_BTN_ACCENT : TAG_BTN_BORDER;
    btn.style.background = narrowed ? TAG_BTN_WASH : "transparent";
  }
  btn.setAttribute("aria-pressed", narrowed ? "true" : "false");
  chipsHost.textContent = "";
  for (const c of lensChips(lens, unions as never)) {
    const col = c.color || TAG_BTN_GRAY;
    const chip = document.createElement("span");
    chip.setAttribute("style", "display:inline-flex;align-items:center;gap:5px;padding:2px 7px;"
      + "border-radius:9px;font-size:0.82em;border:1px solid " + col + ";color:" + col + ";"
      + "background:transparent;white-space:nowrap;");
    chip.appendChild(document.createTextNode(c.label));
    const x = document.createElement("span");
    x.textContent = "✕";
    x.setAttribute("style", "cursor:pointer;opacity:0.75;color:" + TAG_BTN_GRAY + ";font-size:0.9em;");
    x.title = "remove this from the filter";
    x.addEventListener("click", (e) => { e.stopPropagation(); onApply(toggleLens(lens, c.pick)); });
    chip.appendChild(x);
    chipsHost.appendChild(chip);
  }
}

