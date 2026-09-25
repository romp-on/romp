// "Artifacts": a session's files as its own column of the dashboard (plans/artifacts-pane.md, the user 2026-09-19; the second
// pass 2026-09-20, section 9; the contributors' reviews of PR 1925, 2026-09-21). A picker at the top names the session; under it
// every file that was put into that session's thread, as a list and, for images, a grid of BIG thumbnails, so a run of plots can
// be opened and cycled through large. An on-top read of what already happened, by the kernel's deterministic rules (the edit
// tools' inputs, the paths the chat linked and rendered from the prose, a drop's saved path); nothing is injected into any
// session and nothing is written.
//
// The picker lists exactly the sessions OPEN in the chat panes of this dashboard: the shell's {romp:'chatTabs', tabs} broadcast,
// the union of every chat column's strip (a remote tab under its host id, which is what routes the listing to the kernel that
// owns it). Its button and its rows wear the strip's own label (host-prefix.ts sessionLabelNodes) in a ctx-menu card. Beside it
// the lock (the Sessions pane's padlock): unlocked, the pane shows whatever came last, a pick or the chat's most recently
// selected tab ({romp:'activeChat'} from the shell, {type:'activeChat'} from the kernel on ready); locked, it stays on the
// pick; only the lock button changes the lock (the pure machine: artifacts-model.ts nextSelection). The kernel's frame is the
// relay of the same switch over the sockets, and it can land after a later shell relay: it is applied only when its id and nonce
// are not behind the last relay this pane heard (artifacts-model.ts echoAccepted).
//
// The kernel traffic is request and response on this socket, routed to the kernel that owns the session by federation.js
// (loaded by the page, never imported): listArtifacts for the listing, each answer carrying the reqId it was asked with, so a
// slow answer landing after a newer selection is dropped, never rendered; and watchArtifacts, the one session this pane
// shows, which the owning kernel's pusher cycle answers with artifactsChanged {sid, version} when that session's transcript
// grew: the pane re-asks (on screen) or marks the listing stale (off screen) and re-asks when shown. Most moves of a transcript
// change no artifact, so an answer whose rows read the same as the shown ones (listingSig) touches nothing, and a changed one is
// rebuilt in place, row by path, the reader's scroll position kept. No Refresh control, no polling. A session whose host is
// unreachable (federation's down set, host-prefix.ts hostIsDown) says so in place of the wait and lists once the host is back.
// Thumbnails and the large view read the token-authed /file route with the session's sid (preview.ts fileUrl, host-routed for a
// remote session), never a new file server. The large view is the chat's lightbox (openLightbox) with its arrows stepping this
// pane's image sequence (setLightboxNav), plus one control that sends the picture to the Files pane through the shell's viewFile
// relay; a row click walks the chat's route ladder (file-route.ts). A page opened with no shell has no tabs to list and falls
// back to the picker's list (requestSessions), the first landing's behaviour.
import { fileUrl, openLightbox, setLightboxNav, canPreview } from "./preview";
import { gridItems, cycleEntries, viaWord, rowRoute, ago, nextSelection, normalizeTabs, shownRow, listingSig, echoAccepted, type ArtifactItem, type TabRow, type Selection, type SelectionEvent, type RelayMark } from "./artifacts-model";
import { initFileView, openFileView } from "./file-view";
import { delegate } from "./actions";
import { applyTheme } from "./theme";
import { watchOverlayScrollbars } from "./overlay-scrollbars";
import { loadSettings, installSettingsSync, onExternalSettingsChange } from "./settings";
import { sessionLabelNodes, hostOf, hostIsDown, hostDownNote } from "./host-prefix";
import { menuCard, showMenuCard, closeContextMenu } from "./ctx-menu";

interface Listing { items: ArtifactItem[]; capped: boolean; max: number; error: string; sid: string; sig: string }

const SEL_KEY = "romp:artifacts:sid";
const LOCK_KEY = "romp:artifacts:lock";
const vscodeApi = typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;
const framed = window.parent && window.parent !== window;   // a pane of a dashboard (the shell tells it tabs and the active chat), or a page opened alone

let tabs: TabRow[] = [];                                    // the open tabs of the chat panes (the shell's union), the picker's rows
const known = new Map<string, TabRow>();                    // every tab ever listed, by id: a selection no tab shows keeps its last known name (round two, low d)
let sel: Selection = { sid: readSelected(), locked: readLock() };
let listing: Listing | null = null;
let loading = false;
let stale = false;                                          // a growth signal arrived while the pane was off screen or an ask was in flight: re-ask when shown, or once the answer lands
let reqSeq = 0;
let lastReq = 0;
let watched: string | null = null;                          // the session the kernel is told to watch (one per socket)
let panesOn: Record<string, boolean> = {};
let panesAvail: Record<string, boolean> = {};
let relayMark: RelayMark | null = null;                     // the last shell relay of the chat's active tab: the kernel's echo of an earlier switch is dropped against it
const downSeen = new Set<string>();                         // hosts ("" = the local kernel) whose link dropped since this pane last asked: the next open re-asks, a first open re-sends the watch alone
let pickerOpen = false;                                     // the picker's card is up (aria-expanded)
let answers = 0;                                            // accepted listings, stamped on the root for the labs (data-answers)

function readSelected(): string | null { try { return localStorage.getItem(SEL_KEY); } catch { return null; } }
function writeSelected(sid: string | null): void { try { if (sid) localStorage.setItem(SEL_KEY, sid); else localStorage.removeItem(SEL_KEY); } catch { /* storage may be denied */ } }
function readLock(): boolean { try { return localStorage.getItem(LOCK_KEY) === "1"; } catch { return false; } }
function writeLock(on: boolean): void { try { localStorage.setItem(LOCK_KEY, on ? "1" : "0"); } catch { /* storage may be denied */ } }
function el(tag: string, cls?: string): HTMLElement { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function onScreen(): boolean { return framed ? panesOn.artifacts === true : true; }
// the page's romp loader (kernel.py _pane_spin, ui/CLAUDE.md): up while a session's first listing is outstanding, down when it or an error lands
function spin(on: boolean): void { const o = document.getElementById("pane-spin"); if (o) o.classList.toggle("gone", !on); }

function ask(msg: Record<string, unknown>): void { vscodeApi?.postMessage(msg); }
function requestSessions(): void { ask({ type: "requestSessions" }); }
function requestListing(): void {
  stale = false;
  if (!sel.sid) { listing = null; loading = false; paint(); return; }
  if (hostIsDown(sel.sid)) {   // the owning host is unreachable: the note in place of a wait that nothing could end; the host's return re-asks (rearm)
    loading = false; downSeen.add(hostOf(sel.sid));
    listing = { items: [], capped: false, max: 0, error: hostDownNote(sel.sid), sid: sel.sid, sig: "" };
    paint(); return;
  }
  lastReq = ++reqSeq; loading = true;
  if (!listing) paint();       // the wait paints only while nothing is listed: a re-ask under a shown listing leaves it, and the reader's place, alone
  const h = hostOf(sel.sid);
  if (h && !fedHasConn(h)) { downSeen.add(h); return; }   // the host's relay not dialed yet (a boot ahead of federation's first host list): an ask now has nothing to ride and is dropped there; the relay's open asks (rearm)
  ask({ type: "listArtifacts", sid: sel.sid, reqId: lastReq });
}
function fedHasConn(host: string): boolean {
  try { const fed = (window as any).__rompFed; return !fed || typeof fed.hasConn !== "function" || !!fed.hasConn(host); } catch { return true; }
}
// the ONE watched session: the previous one unwatched on its own kernel (federation routes the frame by its sid), the new one watched
function watch(sid: string | null): void {
  if (sid === watched) return;
  if (watched) ask({ type: "watchArtifacts", sid: watched, unwatch: true });
  if (sid) ask({ type: "watchArtifacts", sid });
  watched = sid;
}
// A socket (re)open loses the watch: the kernel mints a fresh client for the new socket, so its watch is gone, and growth would stop
// silently with no Refresh control left to recover it (the verifier of PR 1925, medium). Re-arm on every (re)open, never on the sid
// changing: the local kernel's shim fires romp:wsup, a host's relay socket fires romp:hostRelayUp with its host. The listing is
// re-asked only after that link DROPPED on this page (romp:wsdown, or the host in federation's down set): a growth during the outage
// was never signalled, and an ask in flight died with the socket. The first open fires the same events after the shim flushed the
// boot's queued sends, and re-asking there asked twice (the reviewers of PR 1925): the watch alone is re-sent then.
function rearm(host: string): void {
  if (!sel.sid || hostOf(sel.sid) !== host) return;
  ask({ type: "watchArtifacts", sid: sel.sid }); watched = sel.sid;
  if (!downSeen.has(host)) return;
  downSeen.delete(host);
  if (onScreen()) requestListing(); else stale = true;
}
window.addEventListener("romp:wsup", () => rearm(""));
window.addEventListener("romp:wsdown", () => { downSeen.add(""); });
window.addEventListener("romp:hostRelayUp", (ev) => rearm(String((ev as CustomEvent).detail?.host || "")));
// federation's down set moved (the kernel's tunnel health, published on a change only, never a timer): the shown session's host
// gone down while its first listing was outstanding gets the note in place of the wait; a host back with its relay socket never
// closed (no romp:hostRelayUp to come) re-asks here
window.addEventListener("romp-hosts", () => {
  if (!sel.sid) return;
  const h = hostOf(sel.sid); if (!h) return;
  if (hostIsDown(sel.sid)) { downSeen.add(h); if (!listing) requestListing(); }
  else if (downSeen.has(h)) rearm(h);
});
// every change of the selection goes through the machine; a changed session drops the listing, moves the watch and asks anew; an
// unchanged one repaints the bar alone (the lock's state, the picker's label): the body's nodes and the reader's place stand
function apply(ev: SelectionEvent): void {
  const next = nextSelection(sel, ev);
  const changed = next.sid !== sel.sid;
  sel = next; writeSelected(sel.sid); writeLock(sel.locked);
  if (changed) { listing = null; loading = false; watch(sel.sid); requestListing(); return; }
  paintBar();
}

// ── the paint: the bar (the lock, the picker, the count) and the body (the grid, the list), each updated in place ──────
function lockSvg(on: boolean): SVGElement {
  // the Sessions pane's padlock, byte for byte (romp-timeline-view.js _drawLockToggle): the body, then the seated (locked) or swung-out (unlocked) shackle
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg"); svg.setAttribute("viewBox", "0 0 16 14"); svg.setAttribute("aria-hidden", "true");
  const body = document.createElementNS(NS, "rect"); body.setAttribute("x", "3"); body.setAttribute("y", "6.2"); body.setAttribute("width", "8"); body.setAttribute("height", "5.6"); body.setAttribute("rx", "1.4");
  const shackle = document.createElementNS(NS, "path"); shackle.setAttribute("d", on ? "M4.8 6.2 V4.4 a2.2 2.2 0 0 1 4.4 0 V6.2" : "M9.4 6.2 V5.3 A2.4 2.4 0 0 1 13.6 3.7");
  svg.append(body, shackle);
  return svg;
}
// the skeleton, built once: the bar's two buttons keep their identity across every repaint (the focus, an open picker card anchored on
// the button, the lab's element marks), the body is one scroller whose rows are rebuilt by path
function skeleton(): { bar: HTMLElement; body: HTMLElement } | null {
  const root = document.getElementById("artifacts-root");
  if (!root) return null;
  let bar = root.querySelector<HTMLElement>(".art-bar"); let body = root.querySelector<HTMLElement>(".art-body");
  if (bar && body) return { bar, body };
  root.replaceChildren();
  bar = el("div", "art-bar");
  const lock = el("button", "art-lock") as HTMLButtonElement; lock.dataset.act = "art-lock"; lock.id = "art-lock";
  const pick = el("button", "art-pick") as HTMLButtonElement; pick.dataset.act = "art-pick"; pick.id = "art-pick"; pick.setAttribute("aria-haspopup", "menu");
  const count = el("span", "art-count");
  const noteEl = el("span", "art-note"); noteEl.id = "art-note";   // what the last click could not do (a kind the viewer does not show); cleared by the next render
  bar.append(lock, pick, count, noteEl);
  body = el("div", "art-body");
  root.append(bar, body);
  return { bar, body };
}
function paint(): void { paintBar(); paintBody(); }
function paintBar(): void {
  const sk = skeleton(); if (!sk) return;
  const lock = sk.bar.querySelector<HTMLButtonElement>("#art-lock")!; const pick = sk.bar.querySelector<HTMLButtonElement>("#art-pick")!; const count = sk.bar.querySelector<HTMLElement>(".art-count")!;
  lock.classList.toggle("on", sel.locked);
  lock.setAttribute("aria-pressed", sel.locked ? "true" : "false");
  lock.setAttribute("aria-label", sel.locked ? "Locked to this session" : "Following the chat's active tab");
  lock.title = sel.locked ? "Locked to this session. Click to follow the chat's active tab again." : "Following the chat's active tab. Click to lock the pane to this session.";
  lock.replaceChildren(lockSvg(sel.locked));
  const shown = shownRow(tabs, sel.sid, known);
  pick.title = shown.row && !shown.open ? "the session whose files are listed; its chat tab is not open" : "the session whose files are listed";
  pick.setAttribute("aria-expanded", pickerOpen ? "true" : "false");
  const parts: Node[] = [];
  if (shown.row) {
    parts.push(...sessionLabelNodes(shown.row.name, shown.row.id, shown.row.color));
    if (!shown.open) { const no = el("span", "art-not-open"); no.textContent = "not open"; parts.push(no); }
  } else { const none = el("span", "art-none-label"); none.textContent = tabs.length ? "Choose a session…" : (framed ? "No chat tab is open" : "No sessions yet"); parts.push(none); }
  const chev = el("span", "art-chev"); chev.textContent = "▾"; parts.push(chev);
  pick.replaceChildren(...parts);
  count.textContent = listing && !listing.error ? (listing.capped ? "the newest " + listing.max + " files of more" : listing.items.length + (listing.items.length === 1 ? " file" : " files")) : "";
}
function emptyBody(body: HTMLElement, cls: string, text: string): void { const e = el("div", cls); e.textContent = text; body.replaceChildren(e); }
function paintBody(): void {
  const sk = skeleton(); if (!sk) return;
  const body = sk.body;
  const n = document.getElementById("art-note"); if (n) n.textContent = "";
  spin(!!sel.sid && loading && !listing);
  if (!sel.sid) { emptyBody(body, "art-empty", framed ? "Select a chat tab, or pick a session, to see the files its thread wrote, showed and received." : "Pick a session to see the files its thread wrote, showed and received."); return; }
  if (loading && !listing) { emptyBody(body, "art-empty", "Reading the thread…"); return; }
  if (!listing) { body.replaceChildren(); return; }
  if (listing.error) { emptyBody(body, "art-err", listing.error); return; }
  if (!listing.items.length) { emptyBody(body, "art-empty", "This thread has put no file in yet."); return; }
  // the rebuild in place: every row and tile keyed by its path, the existing node kept (its thumbnail stays decoded, its identity
  // stays for the reader and the labs), the order set by appending in the listing's order, and the reader's scroll position kept:
  // the listing is newest-first, so what was inserted stands above the reader and its height is added back (a reader at the top stays there)
  const st = body.scrollTop, sh0 = body.scrollHeight;
  const oldRows = new Map<string, HTMLElement>(); body.querySelectorAll<HTMLElement>(".art-row[data-path]").forEach((r) => oldRows.set(r.dataset.path!, r));
  const oldTiles = new Map<string, HTMLElement>(); body.querySelectorAll<HTMLElement>(".art-thumb[data-path]").forEach((c) => oldTiles.set(c.dataset.path!, c));
  const imgs = gridItems(listing.items);
  let grid = body.querySelector<HTMLElement>(".art-grid");
  if (imgs.length && canPreview()) {
    if (!grid) { grid = el("div", "art-grid"); body.prepend(grid); }
    imgs.forEach((it, i) => {
      let cell = oldTiles.get(it.path);
      if (!cell) {
        cell = el("div", "art-thumb"); cell.dataset.act = "art-open"; cell.dataset.path = it.path;
        const img = document.createElement("img"); img.loading = "lazy"; img.src = fileUrl(it.path, sel.sid); img.alt = it.name;
        cell.append(img, el("div", "art-cap"));
      }
      cell.dataset.i = String(i); cell.title = it.path; cell.querySelector(".art-cap")!.textContent = it.name;
      grid!.appendChild(cell);
    });
    Array.from(grid.children).slice(imgs.length).forEach((c) => c.remove());
  } else if (grid) grid.remove();
  let list = body.querySelector<HTMLElement>(".art-list");
  if (!list) { list = el("div", "art-list"); body.appendChild(list); }
  listing.items.forEach((it, i) => {
    let row = oldRows.get(it.path);
    if (!row) { row = el("div", "art-row"); row.dataset.act = "art-row"; row.dataset.path = it.path; row.append(el("span", "art-name"), el("span", "art-dir"), el("span", "art-via"), el("span", "art-age")); }
    row.className = "art-row" + (it.exists ? "" : " missing") + (it.refused ? " refused" : "");
    row.dataset.i = String(i); row.title = it.refused ? it.path + " (" + it.refused + ")" : it.path;
    row.querySelector(".art-name")!.textContent = it.name;
    row.querySelector(".art-dir")!.textContent = it.path.slice(0, it.path.length - it.name.length).replace(/\/$/, "");
    row.querySelector(".art-via")!.textContent = viaWord(it.via);
    row.querySelector(".art-age")!.textContent = it.t ? ago(Date.now() / 1000 - it.t) : "";
    list!.appendChild(row);
  });
  Array.from(list.children).slice(listing.items.length).forEach((c) => c.remove());
  Array.from(body.children).forEach((c) => { if (c !== grid && c !== list) c.remove(); });
  if (st > 0) body.scrollTop = st + (body.scrollHeight - sh0);
}

// ── the picker's card: the open tabs, one row per session in the strip's label, the shown one checked ───────────────
function openPicker(anchor: HTMLElement): void {
  closeContextMenu();
  const card = menuCard({ className: "art-picker", id: "art-picker" });
  if (!tabs.length) {
    const none = el("div", "ctx-item art-none"); none.setAttribute("role", "menuitem"); none.tabIndex = -1;
    const b = el("span", "ctx-item-body"); b.textContent = framed ? "No chat tab is open" : "No sessions yet"; none.appendChild(b); card.appendChild(none);
  }
  for (const t of tabs) {
    const cur = t.id === sel.sid;
    const row = el("div", "ctx-item" + (cur ? " current" : "")); row.setAttribute("role", "menuitemradio"); row.setAttribute("aria-checked", cur ? "true" : "false"); row.tabIndex = -1; row.dataset.sid = t.id; row.title = t.id;
    const b = el("span", "ctx-item-body"); b.append(...sessionLabelNodes(t.name, t.id, t.color)); row.appendChild(b);
    row.addEventListener("click", () => { const id = t.id; closeContextMenu(); document.getElementById("art-pick")?.focus(); apply({ type: "pick", id }); });   // a pick never changes the lock (9.5); the focus back on the button the card came from
    card.appendChild(row);
  }
  const r = anchor.getBoundingClientRect();
  pickerOpen = true; anchor.setAttribute("aria-expanded", "true");
  showMenuCard(card, r.left, r.bottom + 2, { onClose: () => { pickerOpen = false; document.getElementById("art-pick")?.setAttribute("aria-expanded", "false"); } });
}

// ── the clicks, delegated once on the root ───────────────────────────────────────────────────
function openLarge(i: number): void {
  if (!listing || !sel.sid) return;
  const imgs = gridItems(listing.items); const it = imgs[i]; if (!it) return;
  openLightbox(it.path, sel.sid);
  // the one extra control: send this picture to the Files pane's viewer (the shell's viewFile relay), hidden where no
  // Files control exists; the lightbox's bar is the file viewer's bar, so the button wears its dress
  if (framed && panesAvail.files !== false) {
    const bar = document.querySelector("#romp-lightbox .fileview-acts");
    if (bar && !bar.querySelector(".art-send")) {
      const b = el("button", "fileview-btn art-send") as HTMLButtonElement; b.textContent = "Files pane"; b.title = "open this picture in the Files pane";
      b.onclick = (ev) => { ev.stopPropagation(); const cur = document.querySelector<HTMLImageElement>("#romp-lightbox .romp-lightbox-img"); const p = cur && cur.alt ? cur.alt : it.path;
        window.parent.postMessage({ romp: "viewFile", path: p, sid: sel.sid, pane: "pane", frag: null }, "*"); };
      bar.insertBefore(b, bar.firstChild);
    }
  }
}
function note(text: string): void { const n = document.getElementById("art-note"); if (n) n.textContent = text; }
function openRow(i: number): void {
  if (!listing || !sel.sid) return;
  const it = listing.items[i]; if (!it || it.refused) return;
  if (it.kind === "other") { note("The viewer cannot show " + it.name + ": not a kind it renders."); return; }   // an ordinary kind of the listing, no viewer for it (the design's section 2)
  if (it.kind === "image" && it.exists && canPreview()) { openLarge(gridItems(listing.items).findIndex((g) => g.path === it.path)); return; }
  if (rowRoute(framed, panesOn, panesAvail) === "pane") window.parent.postMessage({ romp: "viewFile", path: it.path, sid: sel.sid, pane: "pane", frag: null }, "*");
  else openFileView(it.path, sel.sid, {});
}

// ── boot ──────────────────────────────────────────────────────────────────────────────────────
applyTheme(document, loadSettings()); installSettingsSync(); onExternalSettingsChange((st) => applyTheme(document, st));
watchOverlayScrollbars(document, window);   // styles.css's styled scrollbars stand down where the platform overlays its own
initFileView((m) => vscodeApi?.postMessage(m));   // the shared viewer's poster: its ops (fileGitLink, saveFile) ride this socket
setLightboxNav((sid) => { const cur = sel.sid; return listing && cur && sid === cur ? cycleEntries(listing.items, cur) : []; });
const root = document.getElementById("artifacts-root");
if (root) delegate(root, {
  "art-lock": () => apply({ type: "toggleLock" }),
  "art-pick": (e) => openPicker(e as HTMLElement),
  "art-open": (e) => openLarge(Number((e as HTMLElement).dataset.i)),
  "art-row": (e) => openRow(Number((e as HTMLElement).dataset.i)),
});
window.addEventListener("message", (ev) => {
  const m = ev.data;
  if (!m) return;
  if (m.romp === "panes") {
    panesOn = m.on || {}; panesAvail = m.avail || {};
    if (onScreen() && sel.sid && !loading && (stale || !listing)) requestListing();   // shown again: a stale listing (a growth while off screen) is re-asked here, never under an ask in flight
    return;
  }
  if (m.romp === "chatTabs") { tabs = normalizeTabs(m.tabs); for (const t of tabs) known.set(t.id, t); apply({ type: "tabsChanged", tabs }); return; }   // the shell's union of the chat columns' open tabs (9.2); every id seen is remembered
  if (m.romp === "activeChat") {   // the shell's relay of the chat's switch: the mark the kernel's echo of the same switch is judged against
    const id = typeof m.id === "string" && m.id ? m.id : null;
    relayMark = { sid: id, nonce: typeof m.nonce === "number" ? m.nonce : null };
    apply({ type: "activeChat", id }); return;
  }
  if (m.type === "activeChat") {   // the kernel's frame (on ready, or the relay over the sockets): applied only when not behind the last shell relay
    const id = typeof m.id === "string" && m.id ? m.id : null;
    if (!echoAccepted(relayMark, id, m.nonce)) return;
    apply({ type: "activeChat", id }); return;
  }
  if (m.type === "artifactsChanged") {
    if (m.sid !== sel.sid) return;   // a signal for a session no longer shown (a stale watch on another kernel): nothing to do
    if (!onScreen() || loading) { stale = true; return; }   // off screen, or an ask already in flight: one re-ask once shown, or once that answer lands
    requestListing();
    return;
  }
  if (m.type === "sessionList" && Array.isArray(m.items)) {
    if (framed) return;   // a dashboard pane lists the chat's open tabs, never the picker's thirty days
    tabs = normalizeTabs(m.items.map((s: any) => ({ id: s.id, name: s.name || s.id, color: s.color || null }))); for (const t of tabs) known.set(t.id, t);
    paintBar(); if (sel.sid && !listing && !loading) requestListing();
    return;
  }
  if (m.type === "artifactsListing") {
    if (m.reqId !== lastReq || m.sid !== sel.sid) return;   // a slow answer for an earlier selection: dropped, never rendered
    loading = false;
    const items: ArtifactItem[] = Array.isArray(m.items) ? m.items : [];
    const next: Listing = { items, capped: !!m.capped, max: Number(m.max) || 500, error: String(m.error || ""), sid: String(m.sid || ""), sig: listingSig(items) };
    const same = !!listing && !listing.error && !next.error && listing.sig === next.sig && listing.capped === next.capped;
    listing = next;
    if (root) root.dataset.answers = String(++answers);
    if (same) paintBar(); else paint();   // the same rows: the body's nodes and the reader's place untouched (the count alone re-read)
    // the accepted answer is an EVENT on the page (one per accepted listing, naming the session and the ask it answered): a lab reading
    // the rows after a selection change holds on it, never on a wall-clock wait or on the bar's name, which repaints before the answer
    window.dispatchEvent(new CustomEvent("romp:artifacts-listing", { detail: { sid: next.sid, reqId: m.reqId, n: answers, same } }));
    if (stale && onScreen()) requestListing();   // a growth signalled while this ask was in flight: one re-ask, now that its answer landed
  }
});
paint();
vscodeApi?.postMessage({ type: "ready" });   // the handshake every pane sends (files.ts): the kernel answers a pane of a dashboard window with the window's active chat (9.5), so a reloaded pane knows the tab before the shell's next switch
if (!framed) requestSessions();
watch(sel.sid);
if (sel.sid) requestListing();
