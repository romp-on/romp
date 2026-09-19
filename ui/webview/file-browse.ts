// The file BROWSER (the user 2026-08-14): a breadcrumb bar over one directory's entries: click a directory
// to descend, a file to open in the existing viewer, an ancestor crumb to walk up. It exists because the
// viewer could only ever show a path someone else surfaced; this is the "just look around the repo" half.
// Three documents host it, each through initFileBrowse with its own contract (BrowseHost): the FEED pane,
// the shell's relay target for a browse ask naming no pane and the one document whose close restores a
// pane (the default contract); the FILES pane (files.ts), the listing as a column of its own while that
// pane is on screen (no setting names a closed pane since T404); and the chat, where a folder's listing
// opens over the transcript otherwise (render.ts openBrowse decides among them at the click, by the file
// link's ladder in file-route.ts browseRoute).
//
// It is the viewer's SIBLING overlay and sits BENEATH it (z-index), and the stack is kept
// ONE-DIRECTIONAL: opening a file from a listing overlays the viewer on top with the listing intact
// underneath — while opening the BROWSER always closes a viewer that is up (openFileBrowse below),
// because "browse" means the user wants the listing now, and a browser painted under an opaque
// viewer is a dead click (found in review, 2026-08-14). When unsaved edits keep that viewer up (its
// discard confirm, answered with cancel), the browse stands down whole rather than paint the dead
// listing beneath it (the stand-down in openFileBrowse). One direction also makes the keydown story
// honest: the browser's handler always registers before the viewer's, so Escape's topmost-only rule
// holds by construction. The close contract is ownership-aware — the viewer is a modal over this
// document (2026-08-15) and never touches the pane, so the browser's own browseClosed is the ONLY
// pane restore — the shell puts the feed pane back exactly once.
// The FEED alone owes that notice (BrowseHost.shellRestore): the Files pane stays up, and the chat never
// asks the shell to lift a pane for its browser, so their closes say nothing.
//
// The listing rides a WebSocket op (listDir → dirListing), NOT a new HTTP route: the sid field routes
// it to the session-OWNING kernel over the existing federation splice, so browsing a remote session's
// disk needs zero relay code. Staleness is the dirComplete protocol — a client-minted reqId echoed
// back, replies dropped on mismatch, one in-flight ask with newest-value coalescing (the pacing is the
// round-trip itself — an event, not a timer). File BYTES stay on HTTP /file via the existing viewer.
import { closeFileView, openFileClick } from "./file-view";
import { fileUrl } from "./preview";
import { openContextMenu, closeContextMenu, CtxItem } from "./ctx-menu";   // the one menu builder (the v0.16.0 tidy): the row menu's card, dismissal and keys

type DirEntry = {
  name: string; isDir: boolean; isLink: boolean;
  size: number; mtime: number; viewable?: boolean;
};
type DirListing = {
  type: "dirListing"; reqId?: number; host?: string; sid?: string;
  base?: string; parent?: string | null; entries?: DirEntry[];
  total?: number; truncated?: boolean; error?: string;
};

let post: (m: Record<string, unknown>) => void = () => { /* bound by initFileBrowse */ };
let reqSeq = 0;
let inflight = false;
let queued: string | null = null;      // newest navigation asked while one listDir was in flight
let needResync = false;                // a listing was lost to a socket drop — re-ask on romp:wsup
let curPath = "";                      // the listing being shown (or asked for)
let curParent: string | null = null;   // the kernel's parent of the CURRENT base — the way above "~"
let curSid: string | null = null;
let onKeyRef: ((e: KeyboardEvent) => void) | null = null;   // the live keydown handler, so close can unbind it
let showHidden = false;
let shellRestore = true;               // this document's close owes the shell a browseClosed (the feed's contract)
let openPick: ((path: string, sid: string | null) => void) | null = null;   // the host's own open for a picked file (BrowseHost.openFile), else the viewer here

/** A browse ask that reached this window: the shell's relay, or a viewer's directory link posting to its own
 *  window ({romp:"browseFiles", path, sid, identity}). */
export type BrowseAsk = { path: string; sid?: unknown; identity?: unknown };
/** How the hosting document takes part (initFileBrowse's second argument). Every field is optional; the
 *  feed's contract is the default. */
export type BrowseHost = {
  /** Take the ask whole instead of opening here: the chat routes it through its file-link ladder (render.ts
   *  openBrowse); the Files pane caches the identity it carries, then opens (files.ts). */
  onRelay?: (m: BrowseAsk) => void;
  /** Open a file picked from the listing (a row click, Enter on the active row) in place of the viewer here,
   *  UNDER the gesture reader: a Cmd/Ctrl- or middle-clicked PDF row still takes the browser's own tab first.
   *  The Files pane routes a pick through its own open (files.ts openHere), so the file enters its Recent list. */
  openFile?: (path: string, sid: string | null) => void;
  /** Whether a close here tells the shell browseClosed. TRUE only for the FEED, the pane the shell lifts for a
   *  relayed browse and puts back on that message. The Files pane stays up and the chat never asks for a lift,
   *  so their closes say nothing: a browseClosed from either would consume a flag the feed's relay armed and
   *  hide the feed under its own browser. */
  shellRestore?: boolean;
};

function el(tag: string, cls?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

// The browser is the ONLY overlay that juggles the feed pane (the viewer is a modal over whatever
// document opened it since 2026-08-15, and never touches the panes), so browseClosed alone restores
// a pane the shell turned on for us. Fires on EVERY close path, from the document that owes it
// (BrowseHost.shellRestore: the feed).
function tellShellClosed(): void {
  if (!shellRestore) return;
  try {
    if (window.parent !== window) window.parent.postMessage({ romp: "browseClosed" }, "*");
  } catch { /* no shell (standalone /feed) — nothing to restore */ }
}

export function closeFileBrowse(): void {
  const box = document.getElementById("romp-filebrowse");
  if (!box) return;
  box.remove();
  if (document.getElementById("fb-ctx")) closeContextMenu();     // a row menu must not outlive its listing
  document.body.classList.remove("filebrowse-open");
  // Unbind + reset EXPLICITLY: a ✕-close sees no keydown, so a lazy self-removing handler would
  // survive into the next open and double every keystroke; and a module-level inflight surviving a
  // close would wedge the reopened browser behind a reply that may never come (both found in review).
  if (onKeyRef) { document.removeEventListener("keydown", onKeyRef); onKeyRef = null; }
  inflight = false;
  queued = null;
  needResync = false;
  tellShellClosed();
}

function human(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + " KB";
  return n + " B";
}

// The download idiom the viewer uses: a transient cookie-authed <a download> the BROWSER owns; the
// kernel's attachment disposition keeps the page from navigating.
function startDownload(path: string): void {
  const a = document.createElement("a");
  a.href = fileUrl(path, curSid) + "&download=1";
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function joinPath(base: string, name: string): string {
  return (base === "/" ? "" : base) + "/" + name;
}

function dirnameOf(p: string): string {
  const cut = p.lastIndexOf("/");
  return cut > 0 ? p.slice(0, cut) : "/";
}

/** Open the browser at `path` (as the sid's kernel resolves it — "." means that session's cwd). */
export function openFileBrowse(path: string, sid?: string | null): void {
  const had = document.getElementById("romp-filebrowse");
  if (!had) {
    // the id rides the BACKDROP — every open/close/topmost check looks up #romp-filebrowse, and
    // the outermost element is what closeFileBrowse removes. The card inside is the viewer's
    // treatment (the user 2026-09-04, superseding the 2026-08-24 pane takeover): centered over
    // the dim, backdrop click closes (the lightbox contract — content clicks never do).
    const wrap = el("div", "");
    wrap.id = "romp-filebrowse";
    // Arm on pointerdown: a `click` is dispatched on the nearest common ancestor when the press and the
    // release land on different elements, so a drag that STARTED inside the card and ended over the dim
    // read as a backdrop click and closed the browser mid-gesture (review find on #924, 2026-09-07).
    // Close only when both ends were on the dim.
    let downOnDim = false;
    wrap.addEventListener("pointerdown", (e) => { downOnDim = e.target === wrap; });
    wrap.onclick = (ev) => { const close = downOnDim && ev.target === wrap; downOnDim = false; if (close) closeFileBrowse(); };
    const box = el("div", "filebrowse");
    document.body.classList.add("filebrowse-open");

    const bar = el("div", "fb-bar");
    const crumbs = el("div", "fb-crumbs");
    crumbs.id = "fb-crumbs";
    const acts = el("div", "fileview-acts");
    const hid = el("button", "fileview-btn") as HTMLButtonElement;
    hid.type = "button"; hid.id = "fb-hidden"; hid.textContent = "Hidden";
    hid.title = "Show dotfiles too";
    hid.setAttribute("aria-pressed", "false");
    hid.addEventListener("click", () => {           // static overlay chrome — direct listeners are
      showHidden = !showHidden;                     // click-safe here, same as the viewer's buttons
      hid.classList.toggle("on", showHidden);
      hid.setAttribute("aria-pressed", String(showHidden));
      ask(curPath);
    });
    const close = el("button", "fileview-btn fileview-close") as HTMLButtonElement;
    close.type = "button"; close.textContent = "✕"; close.title = "Close (Esc)";
    close.setAttribute("aria-label", "Close the file browser");
    close.addEventListener("click", closeFileBrowse);
    acts.appendChild(hid); acts.appendChild(close);
    bar.appendChild(crumbs); bar.appendChild(acts);

    const list = el("div", "fb-list");
    list.id = "fb-list";
    box.appendChild(bar); box.appendChild(list);
    wrap.appendChild(box);
    document.body.appendChild(wrap);

    // ONE click listener on the stable list root — rows are rebuilt per navigation, so per-row
    // listeners are exactly the destroyed-mid-click bug (ui/CLAUDE.md); the crumbs delegate the
    // same way on their own stable bar node.
    list.addEventListener("click", (ev) => {
      const row = (ev.target as HTMLElement).closest("[data-act]") as HTMLElement | null;
      if (!row || !list.contains(row)) return;
      onAct(row, ev);
    });
    const fileRowOf = (ev: MouseEvent) => {
      const row = (ev.target as HTMLElement).closest("[data-act]") as HTMLElement | null;
      return row && list.contains(row) && row.dataset.act === "file" ? row : null;   // a FILE row only: a middle-click
    };                                                                                // never navigates or downloads
    list.addEventListener("mousedown", (ev) => {         // the middle PRESS on a file row: no autoscroll, which
      if (ev.button === 1 && fileRowOf(ev)) ev.preventDefault();   // starts on the press and would swallow the auxclick
    });
    list.addEventListener("auxclick", (ev) => {          // a middle-click: a PDF row in a browser tab of its own
      if (ev.button !== 1) return;                        // (`click` never fires for the middle button)
      const row = fileRowOf(ev);
      if (!row) return;
      onAct(row, ev);
    });
    crumbs.addEventListener("click", (ev) => {
      const c = (ev.target as HTMLElement).closest("[data-path]") as HTMLElement | null;
      if (!c || !crumbs.contains(c)) return;
      ask(c.dataset.path || "/");
    });
    // Per-entry mechanics one level deeper (the one ctx-menu vocabulary): Copy path / Download /
    // Open folder — the last via the chat's own openFolder op, which always stays on the LOCAL
    // kernel (it SSHes out for a host-prefixed sid; federation.ts routeOutbound's openFolder rule).
    list.addEventListener("contextmenu", (ev) => {
      const row = (ev.target as HTMLElement).closest("[data-path]") as HTMLElement | null;
      if (!row || !list.contains(row)) return;
      ev.preventDefault();
      showRowMenu(ev as MouseEvent, row.dataset.path || "", row.dataset.act === "dir");
    });

    // Escape / arrows / Enter / Backspace. TOPMOST-only, layer by layer: an open row menu first,
    // then the viewer (which can only sit ABOVE us — opening the browser closes any viewer, so our
    // handler always registered before the viewer's), then the browser itself.
    const onKey = (e: KeyboardEvent) => {
      const box2 = document.getElementById("romp-filebrowse");
      if (!box2) return;                                      // closed: closeFileBrowse unbinds us
      // the row menu is the topmost surface and EVERY key is its while it is open (the shared builder's: arrows, Home, End,
      // Enter, Space, Tab); its Escape is taken first, at the capture phase, and marked on the event, so a marked Escape
      // peels nothing more here either (round two of the tidy: the arrows once walked the listing under the open card)
      if (document.getElementById("fb-ctx") || (e.key === "Escape" && e.defaultPrevented)) return;
      if (document.getElementById("romp-fileview")) return;   // the viewer is topmost — its key
      if (e.key === "Escape") { e.preventDefault(); closeFileBrowse(); return; }
      if (e.key === "Backspace" || e.key === "ArrowLeft") {
        const cs = box2.querySelectorAll<HTMLElement>("#fb-crumbs [data-path]");
        // the crumb before the current one; at a "~"-rooted trail's top the kernel-sent parent is
        // the way above home (the up-crumb below carries it too)
        const up = cs.length >= 2 ? cs[cs.length - 2].dataset.path : curParent;
        if (up) { e.preventDefault(); ask(up); }
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const rows = [...box2.querySelectorAll<HTMLElement>(".fb-row[data-act]")];
        if (!rows.length) return;
        const at = rows.findIndex((r) => r.classList.contains("active"));
        const next = e.key === "ArrowDown" ? Math.min(rows.length - 1, at + 1) : Math.max(0, at - 1);
        rows.forEach((r, i) => r.classList.toggle("active", i === next));
        rows[next].scrollIntoView({ block: "nearest" });
        return;
      }
      if (e.key === "Enter") {
        const active = box2.querySelector<HTMLElement>(".fb-row.active");
        if (active) { e.preventDefault(); onAct(active, e); }   // Cmd/Ctrl+Enter on a PDF row: its own tab, like the click
      }
    };
    document.addEventListener("keydown", onKey);
    onKeyRef = onKey;
  }
  // "Browse" means the user wants the LISTING now: a viewer left up would sit over the browser (its
  // modal backdrop draws above — the review's dead-click finding). Closing it costs nothing beyond
  // the modal itself: the viewer never touches the pane, so there is no restore to worry about and
  // the pane stays up for the listing.
  if (document.getElementById("romp-fileview")) closeFileView();
  // The viewer's dirty-edit guard can keep it (closeFileView's closeGuard: the person answered the
  // discard confirm with cancel). Then the click stands down WHOLE: no listing is asked for (it would
  // sit beneath a viewer that covers it, a dead click, and the next Escape after the viewer would fall
  // through to it); no notice follows (the person just answered the confirm); an overlay built above
  // for this click is taken down again; and a listing already beneath the viewer is left exactly as it
  // was, its session and its Hidden state included, since nothing below changed.
  if (document.getElementById("romp-fileview")) { if (!had) unbuild(); return; }
  curSid = sid || null;
  showHidden = false;
  // A re-invoke while open must resync the persistent Hidden control with the state it claims to
  // show — resetting the variable alone left the button lit over a dotfile-hidden listing (review).
  const hb = document.getElementById("fb-hidden");
  if (hb) { hb.classList.remove("on"); hb.setAttribute("aria-pressed", "false"); }
  ask(path);
}

// An overlay built for a click that then stood down (the viewer's veto above): gone again, outside the
// close protocol. Nothing opened, so nothing is owed: no browseClosed (the notice a browser that was up
// sends on closing; the shell's pane restore hangs on it), and no latch to reset, since no ask went out.
function unbuild(): void {
  document.getElementById("romp-filebrowse")?.remove();
  document.body.classList.remove("filebrowse-open");
  if (onKeyRef) { document.removeEventListener("keydown", onKeyRef); onKeyRef = null; }
}

function onAct(row: HTMLElement, ev?: MouseEvent | KeyboardEvent): void {
  const p = row.dataset.path || "";
  if (row.dataset.act === "dir") { ask(p); return; }
  if (row.dataset.act === "file") { openFileClick(ev, p, curSid, openPick ?? undefined); return; }   // a modified click on a PDF → its own tab; else the host's open, or the viewer here
  if (row.dataset.act === "dl") startDownload(p);       // download-only rows download directly —
}                                                       // a viewer that could only apologize helps nobody

function showRowMenu(e: MouseEvent, path: string, isDir: boolean): void {
  const items: CtxItem[] = [];
  const add = (label: string, fn: () => void, sub?: string) => { items.push({ label, sub, pick: fn }); };
  add("Copy path", () => { navigator.clipboard?.writeText(path); });
  if (!isDir) add("Download", () => startDownload(path));
  // the demoted OS-open (the user 2026-08-14): openFolder always runs via the LOCAL kernel, which
  // SSHes out when the sid is host-prefixed — so it lands on the machine the session runs on
  add("Open folder window", () => {
    const cwd = isDir ? path : dirnameOf(path);
    post(curSid ? { type: "openFolder", cwd, id: curSid } : { type: "openFolder", cwd });
  }, "on the machine the session runs on");
  // the shared card, keeping the #fb-ctx id the sheet draws over both overlays (feed.css): placed inside the pane, dismissed
  // on a press outside, Escape, a scroll or the window's blur, the rows reachable by the arrows
  openContextMenu(e.clientX, e.clientY, items, { id: "fb-ctx" });
}

// One in-flight ask; a navigation typed meanwhile waits as `queued` and fires when the reply lands —
// the dirComplete pacing (no debounce; the round-trip is the event).
function ask(path: string): void {
  curPath = path;
  if (inflight) { queued = path; return; }
  inflight = true;
  const list = document.getElementById("fb-list");
  if (list) {
    // the loading rule: the romp loader first, never a blank or a frozen listing
    const load = el("div", "fileview-load");
    load.innerHTML = '<img src="/media/romp-swirl-glyph.svg" alt=""><span>romp</span>'
      + '<i class="fileview-dot"></i><i class="fileview-dot"></i><i class="fileview-dot"></i>';
    list.replaceChildren(load);
  }
  post({ type: "listDir", path, sid: curSid || undefined, reqId: ++reqSeq, hidden: showHidden });
}

// The breadcrumb trail for `base` (~-collapsed): every ancestor is a click. A "~"-rooted trail also
// gets a leading up-crumb carrying the kernel-sent parent — without it home was a ceiling: the trail
// bottoms out at "~" while /tmp or another checkout sits one level above, reachable only if the
// kernel's parent field is actually read (the review's unread-field finding).
function buildCrumbs(base: string, parent: string | null): void {
  const crumbs = document.getElementById("fb-crumbs");
  if (!crumbs) return;
  crumbs.replaceChildren();
  const home = base === "~" || base.startsWith("~/");
  if (home && parent) {
    const up = el("span", "fb-crumb fb-crumb-up");
    up.dataset.path = parent;
    up.textContent = "⋯";
    up.title = "up to " + parent;
    crumbs.appendChild(up);
    const sep0 = el("span", "fb-crumb-sep"); sep0.textContent = "/";
    crumbs.appendChild(sep0);
  }
  const segs = base.split("/").filter((s) => s !== "");
  const rootCrumb = el("span", "fb-crumb");
  rootCrumb.dataset.path = home ? "~" : "/";
  rootCrumb.textContent = home ? "~" : "/";
  if (home) segs.shift();
  crumbs.appendChild(rootCrumb);
  let acc = home ? "~" : "/";
  for (const s of segs) {
    const sep = el("span", "fb-crumb-sep"); sep.textContent = "/";
    crumbs.appendChild(sep);
    acc = (acc === "/" ? "" : acc) + "/" + s;
    const c = el("span", "fb-crumb");
    c.dataset.path = acc;
    c.textContent = s;
    crumbs.appendChild(c);
  }
  crumbs.title = base;
}

// Render a listing failure IN the overlay, loudly, with the crumbs as the way out. The kernel's
// error replies carry base/parent when the path resolved, so even a FIRST open that fails builds a
// walkable trail — an error over an empty crumb bar was a dead end (the review's first-open finding).
function renderError(text: string, base?: string, parent?: string | null): void {
  const list = document.getElementById("fb-list");
  if (!list) return;
  if (base) buildCrumbs(base, parent ?? null);
  const why = el("div", "fileview-err");
  why.textContent = text;
  list.replaceChildren(why);
}

function onListing(m: DirListing): void {
  // Cleared UNCONDITIONALLY, before the stale check — the completer's own precedent (render.ts
  // dirInFlight): a reply is the un-block event whatever it carries, and gating the clear on the
  // reqId match wedged the overlay forever on any lost or duplicate frame (the review's latch bug).
  inflight = false;
  if (queued !== null) { const q = queued; queued = null; ask(q); return; }
  if (m.reqId !== reqSeq) return;                 // a stale reply — a newer navigation superseded it
  if (!document.getElementById("romp-filebrowse")) return;   // closed while the ask was in flight

  if (m.error) {
    curParent = m.parent ?? curParent;
    renderError(m.error, m.base, m.parent);
    return;
  }

  const base = m.base || "/";
  curPath = base;                                  // the kernel's resolved, ~-collapsed truth
  curParent = m.parent ?? null;
  buildCrumbs(base, curParent);

  const list = document.getElementById("fb-list");
  if (!list) return;
  const rows: HTMLElement[] = [];
  for (const en of m.entries || []) {
    const p = joinPath(base, en.name);
    const row = el("div", "fb-row");
    row.dataset.path = p;
    const nm = el("span", "fb-name");
    if (en.isDir) {
      row.dataset.act = "dir";
      nm.textContent = en.name + "/";
      row.classList.add("fb-dir");
      row.title = p + (en.isLink ? "  ·  symlink" : "");
    } else {
      const dlOnly = en.viewable === false;
      row.dataset.act = dlOnly ? "dl" : "file";
      nm.textContent = en.name;
      if (dlOnly) row.classList.add("fb-dlonly");
      row.title = p + (en.isLink ? "  ·  symlink" : "")
        + "  ·  " + new Date(en.mtime * 1000).toLocaleString()
        + (dlOnly ? "  ·  opens as a download (not viewable in the browser, or too large to show)" : "");
      const sz = el("span", "fb-size");
      sz.textContent = (dlOnly ? "⤓ " : "") + human(en.size);
      row.appendChild(nm); row.appendChild(sz);
      rows.push(row);
      continue;
    }
    row.appendChild(nm);
    rows.push(row);
  }
  if (!rows.length) {
    const empty = el("div", "fb-more");
    empty.textContent = "empty directory";
    rows.push(empty);
  }
  if (m.truncated) {
    // no silent caps: say exactly what was left out
    const more = el("div", "fb-more");
    more.textContent = (m.entries || []).length + " of " + (m.total || 0)
      + " entries — the rest aren't shown";
    rows.push(more);
  }
  list.replaceChildren(...rows);
  list.scrollTop = 0;
}

/** Bind the kernel poster and listen for the shell's relay + the kernel's listing replies.
 *  Called once per hosting document (the feed's, the Files pane's and the chat's boot, beside initFileView);
 *  `host` is that document's contract (BrowseHost), the feed's by default. */
export function initFileBrowse(poster: (m: Record<string, unknown>) => void, host: BrowseHost = {}): void {
  post = poster;
  shellRestore = host.shellRestore !== false;
  openPick = host.openFile ?? null;
  window.addEventListener("message", (e: MessageEvent) => {
    const m = e.data;
    if (!m) return;
    if (m.romp === "browseFiles" && typeof m.path === "string") {
      if (host.onRelay) { host.onRelay({ path: m.path, sid: m.sid, identity: m.identity }); return; }   // this document's own contract takes the ask whole
      openFileBrowse(m.path || ".", typeof m.sid === "string" ? m.sid : null);
    } else if (m.type === "dirListing") {
      onListing(m as DirListing);
    } else if (m.type === "warn" && typeof m.sid !== "string" && inflight && document.getElementById("romp-filebrowse")) {
      // A federation drop (the remote host's tunnel is down) answers with a warn INSTEAD of a
      // dirListing — the feed page renders no toasts, so without this branch the ask would hang on
      // a reply that was never sent. Loud, in place, crumbs intact (fail loudly, never a spinner).
      // A warn carrying a session id answers a send INTO that session (the kernel's refusal of a slash command a
      // Codex session cannot take, broadcast to every chat pane when no socket carried it; 2026-09-19), never this
      // listing: with a slow listing in flight it read as the listing's failure and replaced the crumbs with the
      // refusal's sentence. A listing's own failure names no session.
      inflight = false;
      queued = null;
      renderError(String(m.text || "the session's host is not answering"));
    }
  });
  // A socket drop mid-ask loses the reply — the frame was sent, nothing will re-send it. The drop
  // and the return are EVENTS the pane shim already dispatches; keying recovery on them (rather
  // than a timer) is the house rule. On wsdown the latch resets; on wsup the current listing is
  // re-asked, which also repaints over the stale loader.
  window.addEventListener("romp:wsdown", () => {
    if (!document.getElementById("romp-filebrowse")) return;
    if (inflight || queued !== null) { inflight = false; queued = null; needResync = true; }
  });
  window.addEventListener("romp:wsup", () => {
    if (!needResync || !document.getElementById("romp-filebrowse")) return;
    needResync = false;
    ask(curPath);
  });
}
