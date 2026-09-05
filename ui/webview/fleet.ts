// Fleet — a by-SESSION view that mirrors the chat's LEDGER BOX (the user 2026-06-23): each live session, then
// its goal tree beneath it — collapsible checkmark nodes, recency-coloured "(Xm ago)" times, the same .ledger-*
// look. It rides the FEED payload (connects app=feed, reads its `ledgers` slice — one per-session build_session
// ledger, the SAME tree the ledger box draws) — the proven feed channel. Completed top goals hide behind a
// bottom "Show completed" checkbox (default off). The recency colour helpers are copied verbatim from render.ts
// so the colours are IDENTICAL to the ledger box.
import { delegate, flash } from "./actions";
import { applyTheme } from "./theme";
import { loadSettings, installSettingsSync, onExternalSettingsChange } from "./settings";
import { SessionViews, viewTagUnion } from "./session-views";
import { lensVisible, surfaceLens } from "./tag-lens";
import { openTagMenu, tagMenuButton, syncTagFilter } from "./tag-menu";
import { fleetVisibleRoots } from "./fleet-roots";
import { onlyTag, matchesOnly } from "./only-filter";
import { hostPrefix } from "./host-prefix";
import { ageColorReadable } from "./age-color";
import { TIP_GRACE_MS } from "./tip";

type Color = { bg: string; fg: string } | null;
interface LedgerNode {
  id: string; text: string; depth: number; done: boolean; blocked: boolean;
  t: number; mt?: number; current: boolean; derived?: boolean;
  cleared?: boolean; onpath?: boolean; children?: string[];
  summary?: string | null; blockSummary?: string | null; _rec?: number;
  // EXACT turn uuids the kernel already sends per node (build_session tree) — let the fleet deep-link a node to
  // the SAME place the feed modal does (the user 2026-06-27): promptAnchorUuid = the user's minting message,
  // anchorUuid = where the node resolved (an assistant turn).
  promptAnchorUuid?: string | null; anchorUuid?: string | null;
}
interface Ledger { summary?: string; tree: LedgerNode[]; current?: { t?: number } | null; archivedTops?: LedgerNode[]; }
interface FleetSession { sid: string; name: string; color: Color; status?: { state?: string } | null; ledger?: Ledger | null; }

const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;

let sessions: FleetSession[] = [];
// Whether the FIRST feed payload has arrived (the user 2026-06-29): before it has, the fleet must NOT claim
// "no work" — that's the loading gap, where the data simply hasn't landed yet. We leave #fleet-list empty so
// the page's romp loader (_pane_spin) stays up, exactly like the other panes, until real data arrives.
let loaded = false;
let emptyShown = false;   // the romp wordmark is currently showing → don't replay its fade-in every push
// Attached hosts whose feed payload this pane has not merged yet (federation.ts pendingHosts, riding the
// same feed message the ledgers do), and which of those sit on a dead link right now (pendingDead). The
// user 2026-09-02: after a kernel restart or a phone re-foreground the remote hosts' sessions were
// simply ABSENT from this pane for two minutes — no row, no cue — and read as wiped state. One quiet
// line per pending host (the feed's own strip, mirrored) says they are coming; it leaves ONLY on that
// host's first payload or its detach, the events the merge keys on — never a timer.
let pendingHosts: string[] = [];
let pendingDead: string[] = [];
let searchQuery = "";     // #fleet-search filter (the user 2026-06-29): show only sessions whose NAME matches
let fleetViews: SessionViews | null = null;   // the rendered views blob off the feed payload — the outline lens reads it (2026-08-25)
let syncFleetTagBtn: (() => void) | null = null;   // re-dress the tag button per the shared convention on each render
// Provisional cards (the user 2026-06-29): a session working a brand-new prompt the planner hasn't classified
// into a goal yet has NO ledger node, so it's invisible in the fleet — exactly the "things about to appear" the
// user wants to track. They ride the SAME feed payload (feed.asks, provisional:true), so surface a dotted
// signature row per such session here. Stored from each push.
interface ProvCard { sid: string; name: string; color: { bg: string; fg: string } | null; text: string }
let provCards: ProvCard[] = [];
// Full feed-card lookup by goal id (the SAME asks slice provCards reads): the hover card joins a top goal's
// row to its feed card for the distiller BACKGROUND (cards carry it; ledger nodes don't — the user 2026-07-13).
let asksById = new Map<string, { background?: string | null; summary?: string | null; blockSummary?: string | null }>();
const DONE_KEY = "romp:fleetShowDone";
function showDone(): boolean { try { return localStorage.getItem(DONE_KEY) === "1"; } catch { return false; } }
function setShowDone(on: boolean) { try { localStorage.setItem(DONE_KEY, on ? "1" : "0"); } catch { /* ignore */ } }

// "Group by session" (the user 2026-06-29): ON by default = the original by-session sections. OFF = a FLAT
// chronological list of every session's goals merged together, newest first, each row tagged on the RIGHT
// with the session it belongs to (.fl-sesslabel). The fold state keys are session-scoped either way, so a
// node's collapse carries across both modes.
const GROUP_KEY = "romp:fleetGroupBySession";
function isGrouped(): boolean { try { return localStorage.getItem(GROUP_KEY) !== "0"; } catch { return true; } }
function setGrouped(on: boolean) { try { localStorage.setItem(GROUP_KEY, on ? "1" : "0"); } catch { /* ignore */ } }

// Recency cutoff (the user 2026-06-27): a LOGARITHMIC slider hides sessions whose freshest activity is older
// than the window. Stored as a 0..1000 slider position. The right end is ADAPTIVE (the user 2026-06-27): it
// tracks the OLDEST session currently in the fleet, so the slider's whole travel always spans the real fleet
// and every drag does something — a fixed 1-month max left the upper third a dead zone for a fleet that only
// spans hours. The 1-minute FLOOR is preserved and far-right still means "show everything". cutoffSecs() maps
// the position log-uniformly from 1 minute to that adaptive max (each pixel = a constant RATIO of time).
const CUTOFF_KEY = "romp:fleetCutoffPos";
const CUT_MIN = 60, CUT_MAX = 30 * 86400;            // 1 minute (floor) … 1 month (initial fallback before the first render)
let fleetMaxAge = CUT_MAX;                            // adaptive right end — the oldest in-fleet age, refreshed each render()
let refreshCutoffLabel: (() => void) | null = null;  // mountControls registers its label painter so render() can refresh it
function cutoffPos(): number {
  try { const v = parseInt(localStorage.getItem(CUTOFF_KEY) || "", 10); return Number.isFinite(v) ? Math.max(0, Math.min(1000, v)) : 1000; }
  catch { return 1000; }
}
function setCutoffPos(p: number) { try { localStorage.setItem(CUTOFF_KEY, String(p)); } catch { /* ignore */ } }
function cutoffSecs(): number { return CUT_MIN * Math.pow(Math.max(fleetMaxAge, CUT_MIN * 2) / CUT_MIN, cutoffPos() / 1000); }   // log-uniform 1m … oldest-in-fleet
function fmtAge(s: number): string {
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}
// The AGE (secs) of a session's OLDEST currently-eligible TOP goal (respecting Show-completed), or 0 if none.
// The slider's adaptive right end takes the max of this across the fleet, so tightening the window can reach an
// old COMPLETED top even in an otherwise-active session — the per-TOP basis the cutoff filter also uses (NOT the
// session's single newest activity, which stayed ≈ now for any live session and made the slider a no-op).
function sessionOldestTopAge(s: FleetSession, now: number): number {
  const tree = s.ledger?.tree || [];
  stampSubtreeRecency(tree, s.ledger?.current || null);
  const archRoots = (Array.isArray(s.ledger?.archivedTops) ? s.ledger!.archivedTops! : []).filter((n) => n.depth === 0);
  const roots = tree.filter((n) => n.depth === 0);
  let age = 0;
  for (const r of fleetVisibleRoots(roots, archRoots, showDone())) { const rec = nodeRecency(r); if (rec) age = Math.max(age, now - rec); }
  return age;
}
const folded = new Set<string>(), expanded = new Set<string>();   // fold state, keyed "sid\0nodeId"
const fkey = (sid: string, id: string) => sid + "\0" + id;

// Top-level goals last seen as DONE (keyed "sid\0nodeId") — the basis for auto-collapsing a super-category the
// instant it FINISHES (the user 2026-06-29). See the transition pass in render().
const seenDone = new Set<string>();

const sessFolded = new Set<string>();   // sessions whose WHOLE task tree is collapsed, keyed by sid (the user 2026-06-24)

// Collapse / Expand are STICKY TOGGLE MODES (the user 2026-06-29), persisted across kernel restarts + reopens.
// "collapse" → render() folds EVERYTHING (every session + node) and KEEPS it folded as new work streams in;
// "expand" → render() force-expands everything; null → the manual per-node state (folded/expanded sets +
// the finished-top default). The active button "stays clicked". A manual fold/sessfold click LEAVES the mode
// (bakeFoldMode writes the mode's current look into the sets first, so only the node you touched changes).
type FoldMode = "collapse" | "expand" | null;
const FOLD_MODE_KEY = "romp:fleetFoldMode";
function foldMode(): FoldMode { try { const v = localStorage.getItem(FOLD_MODE_KEY); return v === "collapse" || v === "expand" ? v : null; } catch { return null; } }
function setFoldMode(m: FoldMode) { try { if (m) localStorage.setItem(FOLD_MODE_KEY, m); else localStorage.removeItem(FOLD_MODE_KEY); } catch { /* ignore */ } }
let curFoldMode: FoldMode = null;   // snapshot read once per render() so renderFleetNode doesn't re-hit localStorage per node

// A Collapse/Expand BUTTON click: toggle that mode on/off (mutually exclusive). Clear the manual sets so the
// mode is clean and toggling it back OFF returns to the default view.
function toggleFoldMode(m: "collapse" | "expand") {
  const on = foldMode() === m;
  folded.clear(); expanded.clear(); sessFolded.clear();
  setFoldMode(on ? null : m);
  render();
}
// Bake the ACTIVE mode's current look into the manual sets, then leave the mode — called when the user folds
// something by hand, so the auto mode releases but the view it produced is preserved (only the hand-toggled
// node then differs).
function bakeFoldMode() {
  const m = foldMode();
  if (!m) return;
  if (m === "collapse") {
    for (const s of sessions) {
      sessFolded.add(s.sid);
      for (const n of s.ledger?.tree || []) if (n.children && n.children.length) { folded.add(fkey(s.sid, n.id)); expanded.delete(fkey(s.sid, n.id)); }
    }
  } else {
    sessFolded.clear();
    for (const s of sessions) for (const n of s.ledger?.tree || []) if (n.children && n.children.length) { expanded.add(fkey(s.sid, n.id)); folded.delete(fkey(s.sid, n.id)); }
  }
  setFoldMode(null);
}
// Paint the two toggle buttons' "on" state from the persisted mode (called from render + at mount).
function paintFoldButtons() {
  const m = foldMode();
  const c = document.getElementById("fl-collapse"), e = document.getElementById("fl-expand");
  if (c) c.classList.toggle("on", m === "collapse");
  if (e) e.classList.toggle("on", m === "expand");
}

function el(tag: string, cls?: string): HTMLElement { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

// The status pip before a session name — the same language the feed's .fwork-dot speaks: gold =
// working, await-green = awaiting dispatched background work, gray ring = the live state could not be
// read. A healthy idle session gets NO pip, so a blank means "alive and quiet" and nothing else.
// That only holds if every OTHER state renders, which is why the awaiting dot belongs here too: the
// feed has shown it since 2026-07-13, but this pane did not, so an awaiting session was blank here
// and read as idle. States with their own designed treatments elsewhere (blocked / retrying /
// compacting / closed) keep their undotted look.
function statusDot(s: FleetSession): HTMLElement | null {
  const st = s.status?.state;
  const kind = st === "working" ? "" : st === "awaitingBg" ? "await" : st ? null : "unknown";
  if (kind === null) return null;                 // a known state with its own treatment: no pip
  const d = el("span", "fl-workdot" + (kind ? " " + kind : ""));
  d.title = kind === "" ? "working — a turn is running right now"
    : kind === "await" ? "awaiting — idle, but background work it dispatched is still running"
    : "state unknown — romp couldn't read this session's live state";
  return d;
}

// Hover-highlight a GROUP of zones together (parity with the ledger box's linkHover, render.ts): the
// checkbox + time light as one unit when either is hovered, each keeping its own shape via .lz-hl, so a
// clickable row shows which parts go together (the user 2026-06-24).
function linkHover(group: HTMLElement[]): void {
  const on = () => group.forEach((g) => g.classList.add("lz-hl"));
  const off = () => group.forEach((g) => g.classList.remove("lz-hl"));
  group.forEach((g) => { g.addEventListener("mouseenter", on); g.addEventListener("mouseleave", off); });
}

// open a session's chat AND flip the pane back to the chat view (the user 2026-06-24): the Fleet toggle now
// lives in the chat tab bar, which is hidden while Fleet is shown — so picking a session must return there.
function backToChat() { try { if (window.parent !== window) window.parent.postMessage({ romp: "toggleFleet", to: "chat" }, "*"); } catch { /* not in the shell */ } }
function openSession(sid: string) { vscodeApi?.postMessage({ type: "openSession", id: sid }); backToChat(); }

// Deep-link a fleet node to the SAME place the feed modal's matching zone does (the user 2026-06-27): post the
// SAME `showOnTimeline` message (sid + anchorUuid + t), keyed off the node's kernel-supplied anchor uuids, then
// leave the full-screen Fleet view so the chat/timeline land is visible. kind="prompt" → the asking message
// (promptAnchorUuid); kind="work" → where it resolved (anchorUuid, using mt for a resolved node). A null anchor
// falls back to time-based nav kernel-side, exactly as the modal does.
function fleetNode(sid: string, nid: string): LedgerNode | null {
  const s = sessions.find((x) => x.sid === sid);
  // archived-completed nodes live in ledger.archivedTops, NOT ledger.tree — missing them here made an
  // archived row's text a dead click (fell back to a bare openSession, no deep link; the user 2026-07-11)
  return (s?.ledger?.tree || []).find((n) => n.id === nid)
      || (s?.ledger?.archivedTops || []).find((n) => n.id === nid) || null;
}
function fleetNavTo(el: HTMLElement, kind: "prompt" | "work") {
  const sid = el.dataset.sid, nid = el.dataset.nid;
  if (!sid) return;
  const n = nid ? fleetNode(sid, nid) : null;
  if (!n) { openSession(sid); return; }   // node gone from the payload → just open the session
  const resolved = !!(n.done || n.blocked);
  const t = kind === "work" ? ((resolved && n.mt) ? n.mt : n.t) : n.t;
  const anchorUuid = kind === "work" ? (n.anchorUuid ?? null) : (n.promptAnchorUuid ?? null);
  vscodeApi?.postMessage({ type: "showOnTimeline", itemId: nid, sid, t, anchor: kind, anchorUuid });
  backToChat();
}

function agehms(secs: number): string {
  secs = Math.max(0, Math.floor(secs));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

function nodeRecency(n: LedgerNode): number { return (n._rec ?? n.mt ?? n.t) || 0; }
// roll the freshest activity up to every node (mirrors render.ts stampSubtreeRecency)
function stampSubtreeRecency(tree: LedgerNode[], cur: { t?: number } | null): void {
  const byId = new Map(tree.map((n) => [n.id, n] as const));
  const eff = (n: LedgerNode) => (n.current && cur && cur.t) ? Math.max(cur.t, (n.mt ?? n.t) || 0) : ((n.mt ?? n.t) || 0);
  const inflight = new Set<string>();
  const calc = (n: LedgerNode): number => {
    if (n._rec != null) return n._rec;
    if (inflight.has(n.id)) return eff(n);
    inflight.add(n.id);
    let r = eff(n);
    for (const cid of n.children || []) { const c = byId.get(cid); if (c) r = Math.max(r, calc(c)); }
    n._rec = r;
    return r;
  };
  for (const n of tree) n._rec = undefined;
  for (const n of tree) calc(n);
}

// Per-session context a node needs to render (its node lookup + live-current time). One per session; in the
// FLAT view the same renderFleetNode is called with each root's own ctx so nodes from different sessions land
// in one shared container.
interface SessCtx { s: FleetSession; byId: Map<string, LedgerNode>; curT?: number;
  // SEARCH (the user 2026-06-29): subtreeHit(id) = this node OR any descendant matches the query → used to
  // FORCE-EXPAND collapsed branches that contain a match so the hit is revealed. null when not searching.
  subtreeHit?: (id: string) => boolean; }
let curSearch = "";   // the active query (lowercased), snapshot per render() for highlighting + fold override

// Paint `text` into `elm`, wrapping every case-insensitive occurrence of `q` in a .fl-hit highlight span (no
// match, or no query → plain text). Uses text nodes (no innerHTML) so goal text can never inject markup.
function highlightInto(elm: HTMLElement, text: string, q: string): void {
  elm.replaceChildren();
  if (!q) { elm.textContent = text; return; }
  const lc = text.toLowerCase();
  let i = 0, idx: number;
  while ((idx = lc.indexOf(q, i)) !== -1) {
    if (idx > i) elm.appendChild(document.createTextNode(text.slice(i, idx)));
    const m = el("span", "fl-hit"); m.textContent = text.slice(idx, idx + q.length);
    elm.appendChild(m);
    i = idx + q.length;
  }
  if (i < text.length) elm.appendChild(document.createTextNode(text.slice(i)));
}

// A session NAME with search highlighting, the remote "host:" prefix rendered as quiet metadata
// (.host-prefix — gray, italic, smaller; the user 2026-07-11). The search highlight applies to the
// NAME part; the prefix is metadata and never highlights.
function nameInto(elm: HTMLElement, name: string, sid: string, q: string): void {
  const p = hostPrefix(name, sid);
  if (!p) { highlightInto(elm, name, q); return; }
  elm.replaceChildren();
  const h = el("span", "host-prefix"); h.textContent = p.host;
  const rest = el("span", "");
  highlightInto(rest, p.rest, q);
  elm.append(h, rest);
}

// Render node `n` (and its open children) into `container`. Hoisted out of render() so the FLAT (ungrouped)
// view can merge nodes from many sessions into one list. `flat` adds the session-name tag on the RIGHT of a
// depth-0 row (the ungrouped view's "which session is this" marker).
function renderFleetNode(ctx: SessCtx, n: LedgerNode, depth: number, container: HTMLElement, now: number, flat: boolean) {
  const { s, byId, curT } = ctx;
  const expandable = !!(n.children && n.children.length);
  const defaultFold = !!(n.done || n.cleared) && (depth === 0 || !n.onpath);   // a finished OR dismissed top folds by default
  // SEARCH force-expand (the user 2026-06-29): if a collapsed branch CONTAINS a match, open it so the hit is
  // revealed — overriding the fold/mode state while a query is active.
  const hitChild = expandable && curSearch && !!ctx.subtreeHit
    && (n.children || []).some((cid) => ctx.subtreeHit!(cid));
  // a sticky Collapse/Expand mode overrides the per-node state (the user 2026-06-29); null → manual default
  const isFolded = expandable && !hitChild && (
    curFoldMode === "collapse" ? true
    : curFoldMode === "expand" ? false
    : (folded.has(fkey(s.sid, n.id)) || (defaultFold && !expanded.has(fkey(s.sid, n.id)))));
  const row = el("div", "ledger-tnode" + (depth === 0 ? " ledger-top" : "")
    + (n.current ? " current" : "") + (n.done ? " done" : "")
    + (n.blocked && !n.done ? " blocked" : "") + (n.derived ? " derived" : "")
    + (n.cleared ? " cleared" : ""));   // cleared = strike + fade only; the mark stays honest (box = done)
  row.style.paddingLeft = (4 + depth * 15) + "px";
  const tri = el("span", "ledger-tri" + (expandable ? " nav" : " empty"));
  tri.textContent = expandable ? (isFolded ? "▶" : "▼") : "";
  // click-safe: the fold toggle lives on the #fleet-list delegate; this caret just carries its state. The
  // caret is the innermost data-act, so a click on it folds without also firing the row's "open".
  if (expandable) { tri.dataset.act = "fold"; tri.dataset.sid = s.sid; tri.dataset.nid = n.id; tri.dataset.folded = isFolded ? "1" : "0"; }
  // .lz-nav → the pointer cursor (from styles.css), so the checkbox / text / time read as clickable. Each
  // zone DEEP-LINKS to the same place the feed modal's matching zone does (the user 2026-06-27): the TEXT
  // jumps to the message that asked for this (goprompt), and a resolved node's MARK + TIME jump to where it
  // resolved (gowork) — an open node's mark goes to the prompt, its time to the latest work. The zones carry
  // their own data-act (innermost wins), so a click lands the deep-link; the row's data-act="open" remains
  // the fallback for a click on the row's empty space. (Delegated via #fleet-list — see ./actions.)
  const resolved = !!(n.done || n.blocked);
  const mark = el("span", "ledger-tmark lz-nav");
  mark.dataset.sid = s.sid; mark.dataset.nid = n.id; mark.dataset.act = resolved ? "gowork" : "goprompt";
  mark.textContent = n.done ? "✓" : n.blocked ? "⏸" : "";   // open = a hollow CSS ring (no glyph)
  // The mark's WHY tooltip + the text's full-goal tooltip both moved INTO the hover card (the user
  // 2026-07-13): it leads with markReason() as its state line and the untruncated title, so the native
  // titles would only pop redundantly on top of it. (markReason is hoisted below the render — one rule.)
  const txt = el("span", "ledger-ttext lz-nav");
  txt.dataset.sid = s.sid; txt.dataset.nid = n.id; txt.dataset.act = "goprompt";   // text → the asking message
  highlightInto(txt, n.text, curSearch);   // search: highlight the matched substring (plain text otherwise)
  // (The ⊕ distiller-summary expander was removed 2026-06-27 — the user: show just the goals, not the
  //  distiller takeaway / decision brief.)
  const time = el("span", "ledger-ttime");
  if (n.current && curT) {
    time.textContent = `(${agehms(now - curT)})`; time.style.color = ageColorReadable(now - curT);
  } else if (n.done && nodeRecency(n)) {
    const dt = now - nodeRecency(n);
    time.textContent = `(${agehms(dt)} ago)`; time.style.color = ageColorReadable(dt);
    txt.style.color = ageColorReadable(dt);                 // done text matches its rolled-up recency colour
  }
  if (time.textContent) { time.classList.add("lz-nav"); time.dataset.sid = s.sid; time.dataset.nid = n.id; time.dataset.act = "gowork"; }   // time → where the work happened/resolved
  // group the hover highlight like the ledger: a resolved node's checkbox + time light together, the text
  // on its own; an open node's checkbox + text are one block, the time on its own (the user 2026-06-24).
  if (n.done || n.blocked) { linkHover([txt]); linkHover(time.textContent ? [mark, time] : [mark]); }
  else { linkHover([mark, txt]); if (time.textContent) linkHover([time]); }
  row.appendChild(tri); row.appendChild(mark); row.appendChild(txt);
  row.appendChild(time);
  // FLAT view: tag each top-level goal with the session it belongs to, on the row's RIGHT (the user 2026-06-29).
  // It's a label, not its own action — a click bubbles to the row's data-act="open" and jumps into the session.
  if (flat && depth === 0) {
    const tag = el("span", "fl-sesslabel");
    const sd = statusDot(s); if (sd) tag.appendChild(sd);   // working / awaiting / unreadable
    const tnm = el("span", "fl-sesslabel-name"); nameInto(tnm, s.name, s.sid, curSearch);
    if (s.color?.bg) tnm.style.color = s.color.bg;
    tag.appendChild(tnm);
    tag.title = "this goal belongs to “" + s.name + "” — click to open it";
    row.appendChild(tag);
  }
  row.dataset.act = "open"; row.dataset.sid = s.sid;   // click-safe: action lives on the #fleet-list delegate
  row.dataset.nid = n.id;                              // the hover card keys off the row (sid, nid)
  container.appendChild(row);
  if (expandable && !isFolded) for (const cid of n.children!) { const c = byId.get(cid); if (c) renderFleetNode(ctx, c, depth + 1, container, now, flat); }
}

// The per-host loading strip (the user 2026-09-02): the feed's #feed-hostload, worn here — one line per
// pending host, the shared reverse-spin swirl LEFT of the text, at the top so it announces what is
// COMING before the sessions already here. Non-interactive, so the per-render rebuild is click-safe.
// The copy names a dead link honestly (fail loudly) instead of an open-ended "loading".
function hostLoadStrip(): HTMLElement {
  const strip = el("div", "");
  strip.id = "fleet-hostload";
  for (const h of pendingHosts) {
    const line = el("div", "hostload-line");
    const swirl = el("span", "fask-awaiting-swirl");
    const txt = el("span", "");
    txt.textContent = pendingDead.includes(h)
      ? "reconnecting to " + h + "\u2026"
      : "loading sessions from " + h + "\u2026";
    line.append(swirl, txt);
    strip.appendChild(line);
  }
  return strip;
}

// The pane is hidden by default in the dashboard shell (a display:none iframe) yet it received every feed
// push and rebuilt its whole list for nobody, on the main thread every pane shares (2026-09-04). While the
// list is not on screen the payload is kept and the rebuild deferred to the moment it comes into view.
let paneVisible = true;
let paneDirty = false;
function watchPaneVisibility(list: HTMLElement): void {
  if (typeof IntersectionObserver === "undefined") return;   // no observer → always render, as before
  new IntersectionObserver((entries) => {
    paneVisible = entries.some((e) => e.isIntersecting);
    if (paneVisible && paneDirty) { paneDirty = false; render(); }
  }).observe(list);
}
let paneWatching = false;
function render() {
  syncFleetTagBtn?.();
  const list = document.getElementById("fleet-list");
  if (!list) return;
  if (!paneWatching) { paneWatching = true; watchPaneVisibility(list); }
  // Nobody can see it: paint when it is shown — except the FIRST content, which paints through so the reveal
  // shows the list at once rather than the pane loader fading out over an empty pane (an empty list IS the
  // loader-up state; one hidden render buys an instant reveal).
  if (!paneVisible && list.childElementCount > 0) { paneDirty = true; return; }
  list.replaceChildren();
  // BEFORE the first payload: leave the list EMPTY so the page's romp loader (_pane_spin over #fleet-list)
  // stays up — no child means it never hides — instead of flashing a false "no work" message (the user
  // 2026-06-29). A WS drop / kernel restart re-shows that same loader (romp:wsdown), so a restart shows the
  // swirl, not "no tasks".
  if (!loaded) { emptyShown = false; return; }
  if (pendingHosts.length) list.appendChild(hostLoadStrip());   // leads the list: what is still coming
  const sd = showDone();
  const grouped = isGrouped();
  curFoldMode = foldMode();   // snapshot the sticky Collapse/Expand mode once for this render
  paintFoldButtons();
  const now = Math.floor(Date.now() / 1000);
  let any = false;

  // Adaptive cutoff range: the slider's right end tracks the OLDEST currently-eligible TOP goal (the user
  // 2026-06-30), so its travel always spans the real work — including old COMPLETED tops when Show-completed is
  // on — with no dead zone. Compute it BEFORE filtering, then refresh the slider's "≤ <age>" label.
  let maxAge = CUT_MIN * 2;
  for (const s of sessions) maxAge = Math.max(maxAge, sessionOldestTopAge(s, now));
  fleetMaxAge = maxAge;
  refreshCutoffLabel?.();
  const cutoff = cutoffSecs();

  // Auto-collapse a super-category the instant it FINISHES (the user 2026-06-29): when a top-level goal flips
  // not-done → done (every sub-step checked off), drop any manual "expand" for it so it folds shut — even if
  // you'd expanded it while it was in progress. Event-based (keyed on the done TRANSITION, via seenDone), and
  // one-shot: you can re-expand it afterward and it stays open, since the transition won't fire again until it
  // reopens and re-completes. Runs over EVERY top goal (not just visible ones) so the collapse sticks even when
  // "Show completed" is off and the finished goal is momentarily filtered out. Once folded, defaultFold (a done
  // top with no manual expand) keeps it shut.
  for (const s of sessions) {
    for (const r of s.ledger?.tree || []) {
      if (r.depth !== 0) continue;
      const k = fkey(s.sid, r.id);
      if (r.done) { if (!seenDone.has(k)) { expanded.delete(k); seenDone.add(k); } }   // just finished → collapse
      else seenDone.delete(k);                                                          // (re)opened → re-arm
    }
  }

  // Provisional signature (the user 2026-06-29): a dotted "about to appear" row for a session working a
  // not-yet-classified prompt. Spinning swirl + the live gist; clicking opens the session. `flat` adds the
  // session-name tag on the right (matching renderFleetNode's flat tagging). Provisionals are always current,
  // so they ignore the recency cutoff.
  const provBySid = new Map<string, ProvCard>();
  for (const p of provCards) if (!provBySid.has(p.sid)) provBySid.set(p.sid, p);
  const makeProvRow = (p: ProvCard, flat: boolean) => {
    const row = el("div", "ledger-tnode ledger-top fl-prov");
    row.dataset.act = "open"; row.dataset.sid = p.sid;   // click-safe via the #fleet-list delegate
    row.title = "this session is working a brand-new prompt — the planner hasn't filed it as a task yet";
    row.appendChild(el("span", "fl-prov-swirl"));
    const txt = el("span", "ledger-ttext fl-prov-text"); txt.textContent = p.text;
    row.appendChild(txt);
    if (flat && p.name) {
      const tag = el("span", "fl-sesslabel");
      const tnm = el("span", "fl-sesslabel-name"); tnm.textContent = p.name;
      if (p.color?.bg) tnm.style.color = p.color.bg;
      tag.appendChild(tnm);
      row.appendChild(tag);
    }
    return row;
  };

  // First pass (shared by both views): per session, keep the visible TOP goals inside the slider window (the
  // recency cutoff is applied per-top below), each session paired with its render context + surviving roots.
  const survivors: { ctx: SessCtx; visibleRoots: LedgerNode[] }[] = [];
  const sq = searchQuery.trim().toLowerCase();           // search (the user 2026-06-29): session NAME or goal CONTENT
  curSearch = sq;                                        // snapshot for renderFleetNode (highlight + force-expand)
  const only = onlyTag();                                // demo/recording view filter (`#only=<tag>`, the user 2026-07-14)
  const outlineLens = surfaceLens(fleetViews, "outline");
  const outlineUnions = viewTagUnion(fleetViews);
  for (const s of sessions) {
    if (only && !matchesOnly(s.name, only)) continue;    // hidden from this view; the real session keeps running
    if (!lensVisible(outlineLens, outlineUnions, s.sid)) continue;   // the OUTLINE's own lens (per-surface selections, 2026-08-25)
    const tree = s.ledger?.tree || [];
    // "Show completed" surfaces the FULLY-COMPLETED tops the compaction sweep archived out of the live tree
    // (the user 2026-06-27) — otherwise a finished+archived session has an empty live tree and vanishes, and
    // "Show completed" has nothing to reveal. The archive now carries each top's WHOLE SUBTREE (the user
    // 2026-06-29), so an archived completed goal EXPANDS to its hierarchy like a live one. ONLY shown when the
    // toggle is on (fleetVisibleRoots gates the depth-0 roots). The top-row selection is the pure ./fleet-roots.
    const archivedTops = Array.isArray(s.ledger?.archivedTops) ? s.ledger!.archivedTops! : [];
    stampSubtreeRecency(tree, s.ledger?.current || null);
    // byId spans the live tree AND the archived subtrees, so renderFleetNode can walk an archived top's
    // descendants. Only depth-0 archived nodes are ROOTS; the rest are reachable via their parents' children.
    const byId = new Map([...tree, ...archivedTops].map((n) => [n.id, n] as const));
    const roots = tree.filter((n) => n.depth === 0);
    const archRoots = archivedTops.filter((n) => n.depth === 0);
    // SEARCH (the user 2026-06-29): subtreeHit(id) = node OR any descendant text contains the query — memoized
    // over this session's nodes (live + archived). Drives the keep decision + the force-expand of collapsed hits.
    // It walks a top's WHOLE subtree (so a match in a live top's already-DONE sub-step still reveals that top),
    // but the tops it's applied to are the in-window, completed-gated `base` (the user 2026-06-30) — so search
    // stays inside the "Show completed" toggle + recency slider rather than reaching past them.
    const hitMemo = new Map<string, boolean>();
    const subtreeHit = (id: string): boolean => {
      const cached = hitMemo.get(id);
      if (cached !== undefined) return cached;
      hitMemo.set(id, false);                            // cycle guard (trees are acyclic, but be safe)
      const node = byId.get(id);
      let h = !!node && node.text.toLowerCase().includes(sq);
      if (!h && node) for (const cid of node.children || []) if (subtreeHit(cid)) { h = true; break; }
      hitMemo.set(id, h);
      return h;
    };
    let visibleRoots: LedgerNode[];
    if (sq) {
      // Search filters WITHIN the current view, it does NOT bypass it (the user 2026-06-30): apply the SAME
      // "Show completed" gating + recency cutoff as the no-search case FIRST, then keep only the tops that hit.
      // So a query surfaces a completed/old goal only when the toggle/slider would already be showing it —
      // typing in the search box narrows what's visible, it doesn't reach past the window. (A NAME match keeps
      // the session's in-window tops; a CONTENT match keeps just the tops whose subtree hits.)
      const base = fleetVisibleRoots(roots, archRoots, sd).filter((r) => (now - nodeRecency(r)) <= cutoff);
      visibleRoots = s.name.toLowerCase().includes(sq) ? base : base.filter((r) => subtreeHit(r.id));
      if (!visibleRoots.length) continue;                // no in-window name/content match → drop the session
    } else {
      visibleRoots = fleetVisibleRoots(roots, archRoots, sd);
      if (!visibleRoots.length) continue;                // nothing to show for this session → skip
      // recency cutoff (the user 2026-06-30): filter INDIVIDUAL top goals by recency — not just whole sessions.
      // Before, a session was kept whole if its NEWEST activity was recent, so an active session's old COMPLETED
      // tops always rode along and the slider looked dead. Now each top is gated on its own subtree-rolled-up
      // recency (_rec, stamped above): a live/in-progress top stays (≈ now), an old completed one drops as you
      // tighten the window. If nothing's left in-window, the session header is skipped too.
      visibleRoots = visibleRoots.filter((r) => (now - nodeRecency(r)) <= cutoff);
      if (!visibleRoots.length) continue;
    }
    survivors.push({ ctx: { s, byId, curT: s.ledger?.current?.t, subtreeHit: sq ? subtreeHit : undefined }, visibleRoots });
  }
  // SEARCH also filters the provisional ("about to appear") rows: keep one only if its session name or its
  // live gist matches the query (the user 2026-06-29).
  if (sq) for (const [sid, p] of Array.from(provBySid))
    if (!p.name.toLowerCase().includes(sq) && !(p.text || "").toLowerCase().includes(sq)) provBySid.delete(sid);

  if (grouped) {
    // BY-SESSION view: each session, then its goal tree beneath it (the original layout).
    for (const { ctx, visibleRoots } of survivors) {
      any = true;
      const s = ctx.s;
      const sec = el("div", "fl-session");
      const head = el("div", "fl-head");
      // session-level collapse caret (the user 2026-06-24): folds this session's WHOLE task tree. Its OWN
      // data-act="sessfold" (the innermost data-act in the head) so clicking it folds WITHOUT opening the
      // session — only a click on the name/rest of the head (data-act="open") jumps in.
      const sfolded = curFoldMode === "collapse" ? true : curFoldMode === "expand" ? false : sessFolded.has(s.sid);
      const caret = el("span", "fl-caret");
      caret.textContent = sfolded ? "▶" : "▼";
      caret.title = sfolded ? "expand this session's tasks" : "collapse this session's tasks";
      caret.dataset.act = "sessfold"; caret.dataset.sid = s.sid;
      caret.style.cssText = "flex:0 0 auto;cursor:pointer;color:var(--vscode-descriptionForeground,#9a9a9a);"
        + "font-size:9px;width:13px;text-align:center;user-select:none";
      head.appendChild(caret);
      const hd = statusDot(s); if (hd) head.appendChild(hd);   // working / awaiting / unreadable
      const nm = el("span", "fl-name");
      nameInto(nm, s.name, s.sid, curSearch);   // highlight a name match (remote "host:" stays quiet metadata)
      if (s.color?.bg) nm.style.color = s.color.bg;
      head.appendChild(nm);
      head.title = "Open this session";
      head.dataset.act = "open"; head.dataset.sid = s.sid;   // click-safe: action lives on the #fleet-list delegate
      sec.appendChild(head);

      const treeBox = el("div", "ledger-tree");
      if (!sfolded) {
        for (const r of visibleRoots) renderFleetNode(ctx, r, 0, treeBox, now, false);
        const prov = provBySid.get(s.sid);               // a provisional row joins this session's tree
        if (prov) { treeBox.appendChild(makeProvRow(prov, false)); provBySid.delete(s.sid); }
        sec.appendChild(treeBox);
      }
      list.appendChild(sec);
    }
    // sessions that are ONLY provisional (no ledger tree → skipped above) still get a minimal section, so the
    // "about to appear" work is visible. Sorted by name for a stable order.
    for (const [, p] of Array.from(provBySid).sort((a, b) => (a[1].name || "").localeCompare(b[1].name || ""))) {
      any = true;
      const sec = el("div", "fl-session");
      const head = el("div", "fl-head");
      head.appendChild(el("span", "fl-workdot"));
      const nm = el("span", "fl-name"); nm.textContent = p.name; if (p.color?.bg) nm.style.color = p.color.bg;
      head.appendChild(nm);
      head.title = "Open this session"; head.dataset.act = "open"; head.dataset.sid = p.sid;
      sec.appendChild(head);
      const treeBox = el("div", "ledger-tree"); treeBox.appendChild(makeProvRow(p, false)); sec.appendChild(treeBox);
      list.appendChild(sec);
    }
  } else {
    // FLAT (ungrouped) view (the user 2026-06-29): every session's top goals merged into ONE chronological
    // list, newest first, each tagged on the right with its session. The whole subtree still expands inline,
    // and the per-node fold state (session-scoped keys) carries over from the grouped view.
    const flatRoots: { ctx: SessCtx; root: LedgerNode }[] = [];
    for (const { ctx, visibleRoots } of survivors) for (const r of visibleRoots) flatRoots.push({ ctx, root: r });
    flatRoots.sort((a, b) => nodeRecency(b.root) - nodeRecency(a.root));   // newest first
    if (flatRoots.length || provBySid.size) {
      any = true;
      const treeBox = el("div", "ledger-tree fl-flat");
      for (const { ctx, root } of flatRoots) renderFleetNode(ctx, root, 0, treeBox, now, true);
      for (const [, p] of Array.from(provBySid).sort((a, b) => (a[1].name || "").localeCompare(b[1].name || "")))
        treeBox.appendChild(makeProvRow(p, true));   // provisional rows ride the flat list too, tagged by session
      list.appendChild(treeBox);
    }
  }

  if (!any && sq) {
    // SEARCH with no match (the user 2026-06-29): say "No results" — NOT the romp wordmark, which reads as
    // "all clear" and hides that you're filtering.
    const nr = el("div", "fl-empty");
    nr.textContent = "No results for “" + searchQuery.trim() + "”";
    list.appendChild(nr);
    emptyShown = false;
  } else if (!any && !pendingHosts.length) {
    // GENUINELY empty (data loaded, no open work): the romp tri-color WORDMARK, centered + faded in — the
    // same calm inbox-zero treatment as the feed (the user 2026-06-29). The fade plays ONCE on the
    // not-empty→empty transition (emptyShown guard), not on every push, since render() rebuilds each time.
    const wm = el("div", "fl-wordmark" + (emptyShown ? " no-anim" : ""));
    wm.setAttribute("role", "img");
    wm.setAttribute("aria-label", sd ? "No work across the fleet yet" : "No open work — every session is clear");
    list.appendChild(wm);
    emptyShown = true;
  } else {
    emptyShown = false;
  }
}

// The Fleet controls live in a DOCKED bottom bar — its own dedicated rectangle in normal flow (#fleet-foot),
// NOT a floating overlay (the user 2026-06-29). Left side: the view controls — "Group by session" (off = the
// flat chronological list) + Collapse-all / Expand-all. Right side: the recency-cutoff slider ("≤ <age>",
// logarithmic 1 minute … 1 month) beside the "Show completed" checkbox. Mounted once into #fleet-foot.
function mountControls() {
  const foot = document.getElementById("fleet-foot");
  if (!foot || foot.dataset.mounted === "1") return;     // mount once into the docked footer
  foot.dataset.mounted = "1";
  foot.replaceChildren();

  // ── LEFT cluster: grouping + collapse/expand ──
  const left = el("div", "fl-foot-left");
  const grpLbl = el("label", "fl-foot-toggle") as HTMLLabelElement;
  const grp = document.createElement("input");
  grp.type = "checkbox"; grp.checked = isGrouped(); grp.style.cursor = "pointer";
  grp.title = "Group goals under their session. Off = one chronological list across every session, each tagged with its session.";
  grp.addEventListener("change", () => { setGrouped(grp.checked); render(); });
  grpLbl.appendChild(grp);
  grpLbl.appendChild(document.createTextNode("Group"));   // short label; the tooltip carries the full meaning
  // Collapse / Expand are STICKY toggle buttons: click to enter the mode (button "stays clicked"), click
  // again to leave, or fold something by hand to release it. id'd so paintFoldButtons can light the active one.
  const collapse = el("button", "fl-foot-btn"); collapse.id = "fl-collapse";
  collapse.textContent = "Collapse"; collapse.title = "Keep everything collapsed — folds every session + goal and stays that way as work streams in (click again, or fold something by hand, to release)";
  collapse.addEventListener("click", () => { flash(collapse); toggleFoldMode("collapse"); });
  const expand = el("button", "fl-foot-btn"); expand.id = "fl-expand";
  expand.textContent = "Expand"; expand.title = "Keep everything expanded — opens every goal and stays that way as work streams in (click again, or fold something by hand, to release)";
  expand.addEventListener("click", () => { flash(expand); toggleFoldMode("expand"); });
  left.append(grpLbl, collapse, expand);

  // ── RIGHT cluster: recency cutoff slider + Show completed ──
  // Grow to fill the space between the left cluster and the right edge (the user 2026-06-30) so the slider can
  // stretch when the control bar has room; it still wraps + shrinks to its min on a narrow pane.
  const right = el("div", "fl-foot-right"); right.style.flex = "1 1 auto"; right.style.minWidth = "0";
  const lab = el("span");
  lab.style.cssText = "min-width:32px;text-align:right;font-variant-numeric:tabular-nums;flex:0 0 auto";
  const sl = document.createElement("input");
  // REVERSED direction + blue fill on the RIGHT (the user 2026-06-29): dragging RIGHT shows only MORE-RECENT
  // sessions (tighter window), LEFT shows everything. Done with a horizontal flip (scaleX(-1)) of the native
  // slider rather than mirroring the VALUE — so the accent (blue) fill, which a native range paints on the
  // LOW side, lands on the RIGHT. cutoffPos keeps its meaning (1000 = show all) and the value maps directly.
  sl.type = "range"; sl.min = "0"; sl.max = "1000"; sl.step = "1"; sl.value = String(cutoffPos());
  sl.style.cssText = "flex:1 1 96px;min-width:48px;cursor:pointer;transform:scaleX(-1)";   // grows to fill the bar; shrinks to min on a narrow pane
  (sl.style as CSSStyleDeclaration & { accentColor: string }).accentColor = "var(--accent, #9cd2ff)";
  sl.title = "Drag RIGHT to show only more-recent sessions (down to the last minute); LEFT shows everything — logarithmic";
  const paint = () => { lab.textContent = "≤ " + fmtAge(cutoffSecs()); };
  refreshCutoffLabel = paint;   // render() refreshes the label when the adaptive max shifts with the fleet
  sl.addEventListener("input", () => { setCutoffPos(parseInt(sl.value, 10)); paint(); render(); });
  paint();
  right.appendChild(lab); right.appendChild(sl);
  // "Show completed" checkbox on the SAME row (no divider — the cluster gap separates them).
  const lbl = el("label", "fl-foot-toggle") as HTMLLabelElement;
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = showDone();
  cb.style.cursor = "pointer";
  cb.addEventListener("change", () => { setShowDone(cb.checked); render(); });
  lbl.appendChild(cb);
  lbl.appendChild(document.createTextNode("Show completed"));
  right.appendChild(lbl);

  foot.append(left, right);
}

window.addEventListener("message", (e: MessageEvent) => {
  const m = e.data;
  if (!m || m.type !== "feed") return;               // Fleet rides the FEED payload (proven channel); reads its `ledgers`
  // "loaded" means the kernel actually BUILT the fleet's ledgers (the key is present, even if []) — NOT merely
  // that some feed message arrived. A feed push can reach us before the (cold) ledger build finishes; treating
  // that as loaded would drop the loader onto an empty pane (the user 2026-06-29). Until ledgers land, keep the
  // loader up (render() bails, leaving the list empty so _pane_spin holds).
  if (m.views && typeof m.views === "object") fleetViews = m.views as SessionViews;   // rides the feed payload (2026-08-25)
  // the attached-but-not-yet-merged hosts, from the merge itself (the ONLY writer; absent = none pending)
  pendingHosts = Array.isArray(m.pendingHosts) ? m.pendingHosts.filter((h: any) => typeof h === "string") : [];
  pendingDead = Array.isArray(m.pendingDead) ? m.pendingDead.filter((h: any) => typeof h === "string") : [];
  if (!Array.isArray(m.ledgers)) return;
  loaded = true;
  sessions = m.ledgers as FleetSession[];
  provCards = (Array.isArray(m.asks) ? m.asks : [])
    .filter((a: any) => a && a.provisional && a.sid)
    .map((a: any) => ({ sid: a.sid, name: a.name || "", color: a.color || null, text: a.text || "Working…" }));
  asksById = new Map((Array.isArray(m.asks) ? m.asks : [])
    .filter((a: any) => a && a.itemId && !a.provisional)
    .map((a: any) => [a.itemId as string, a] as const));
  render();
});
window.addEventListener("storage", (e: StorageEvent) => { if (e.key === "romp:settings") { applyTheme(document, loadSettings()); render(); } });   // theme/colormap change → reskin + recolour
applyTheme(document, loadSettings());   // the persisted theme applies at boot (2026-08-28)
// VS Code webviews have per-origin storage and never see another pane's `storage` events — gear
// saves arrive as settingsSync host messages (PR #763 item 3; the raiser re-fires romp:settings,
// which onExternalSettingsChange below already handles in the browser too)
installSettingsSync();
onExternalSettingsChange((s) => { applyTheme(document, s); render(); });

// Fleet-list clicks are DELEGATED to the stable #fleet-list (installed once). render() does
// `#fleet-list`.replaceChildren() on every feed push, so a handler hung on a rebuilt row/header/caret is
// destroyed mid-click and the click is dropped — delegation on the container that survives the rebuild fixes
// it. Each node declares its data-act + data-sid/data-nid (see render()). See ./actions and CLAUDE.md ## Design.
(() => {
  const list = document.getElementById("fleet-list");
  if (!list) return;
  delegate(list, {
    open: (el) => { const sid = el.dataset.sid; if (sid) openSession(sid); },
    goprompt: (el) => fleetNavTo(el, "prompt"),   // text/open-mark zone → the asking message (like the modal)
    gowork: (el) => fleetNavTo(el, "work"),        // resolved mark/time zone → where it resolved (like the modal)
    sessfold: (el) => {                                  // ▶/▼ on the session head → collapse/expand its whole tree
      const sid = el.dataset.sid;
      if (!sid) return;
      bakeFoldMode();   // a hand-fold leaves the sticky Collapse/Expand mode, preserving the current look
      if (sessFolded.has(sid)) sessFolded.delete(sid); else sessFolded.add(sid);
      render();
    },
    fold: (el) => {
      const sid = el.dataset.sid, nid = el.dataset.nid;
      if (!sid || !nid) return;
      bakeFoldMode();   // a hand-fold leaves the sticky Collapse/Expand mode, preserving the current look
      const k = fkey(sid, nid);
      if (el.dataset.folded === "1") { expanded.add(k); folded.delete(k); } else { folded.add(k); expanded.delete(k); }
      render();
    },
  });
})();

// ── OUTLINE HOVER CARD (the user 2026-07-13) ─────────────────────────────────────────────────────
// Hovering a goal row shows the FULL story its feed modal tells — the state line (markReason), the
// untruncated goal text, the distiller's Background + Key takeaway (or the Decision brief), and the
// sub-goal checklist — without leaving the Outline. ONE persistent panel on document.body: render()
// wipes #fleet-list on every push (replaceChildren), so anything mounted inside it dies mid-hover
// (the timeline SVG-wipe lesson) — the panel lives outside the wipe and only hides on a real
// mouse-out / scroll / click. Wiring is DELEGATED to the stable #fleet-list; the card shows INSTANTLY
// on hover (the one tooltip treatment, 2026-08-28 — a hover IS the intent) and hides after the shared
// TIP_GRACE_MS transit grace so sweeping into the card never flickers it.

// WHY a node's checkbox reads the way it does — explicit vs inferred (roll-up = every sub-step done,
// roll-down = a resolved parent) vs dismissed vs blocked vs open — worked out from the children the
// render already has (no kernel round-trip). Was the mark's native tooltip (the user 2026-06-24); now
// the hover card's state line — the same words, a richer home.
function markReason(n: LedgerNode, byId: Map<string, LedgerNode>): string {
  // `done` is HONEST since 2026-07-26 (the box means done): a cleared node's flag says whether it
  // actually finished before the dismissal — no more guessing from summary-presence.
  if (!n.done) {
    if (n.cleared) return n.blocked ? "blocked, then cleared — dismissed unfinished" : "cleared — dismissed as no longer needed, never done";
    return n.blocked ? "blocked — needs you" : "not yet done";
  }
  if (n.cleared) return "completed, then cleared off the board";
  if (!n.derived) return "done — explicitly checked off";
  const kids = (n.children || []).map((id) => byId.get(id)).filter(Boolean) as LedgerNode[];
  return (kids.length > 0 && kids.every((k) => k.done))
    ? "done — inferred: every sub-step is complete"
    : "done — inferred: a parent goal was checked off";
}

const HOVER_SUB_CAP = 14;   // sub-goal rows shown before "…and N more" (the card stays a glance, not a scroll)
let hoverCardEl: HTMLElement | null = null;
let hoverKey = "";                      // "sid\0nid" currently shown
let hoverHideT: number | undefined;

function hideHoverCard(): void {
  if (hoverHideT) { clearTimeout(hoverHideT); hoverHideT = undefined; }
  hoverKey = "";
  if (hoverCardEl) { hoverCardEl.remove(); hoverCardEl = null; }
}
// Leaving a row schedules the hide with the shared tip grace (tip.ts TIP_GRACE_MS), so crossing the
// small gap into the card (or to the next row, which re-keys) doesn't flicker it; entering cancels.
function scheduleHideHover(): void {
  if (hoverHideT) clearTimeout(hoverHideT);
  hoverHideT = window.setTimeout(hideHoverCard, TIP_GRACE_MS);
}

// The card body — the modal's sections from data the pane already holds: the ledger node (state, text,
// summary/blockSummary, subtree) + its feed card when one exists (background rides only on cards).
function buildHoverCard(s: FleetSession, n: LedgerNode, byId: Map<string, LedgerNode>, now: number): HTMLElement {
  const card = el("div", "fl-hover");
  const state = el("div", "fl-hover-state");
  const rec = nodeRecency(n);
  state.textContent = markReason(n, byId) + (rec ? " · " + agehms(now - rec) + " ago" : "");
  const title = el("div", "fl-hover-title"); title.textContent = n.text;
  card.append(state, title);
  const section = (label: string, text: string) => {
    const sec = el("div", "fl-hover-sec");
    const lab = el("div", "fl-hover-lab"); lab.textContent = label;
    const body = el("div", "fl-hover-body"); body.textContent = text;
    sec.append(lab, body); card.appendChild(sec);
  };
  const ask = asksById.get(n.id);   // top goals with a live feed card carry the distiller BACKGROUND
  if (ask?.background && ask.background.trim()) section("Background", ask.background);
  const summary = (n.summary || ask?.summary || "").trim();
  const brief = (n.blockSummary || ask?.blockSummary || "").trim();
  if (summary) section("Key takeaway", summary);
  else if (brief) section("Decision brief", brief);
  // sub-goal checklist: the node's whole subtree, depth-indented, same ✓/⏸/ring marks as the rows
  const subs: { d: number; c: LedgerNode }[] = [];
  const walk = (id: string, d: number) => {
    const c = byId.get(id);
    if (!c) return;
    subs.push({ d, c });
    for (const cid of c.children || []) walk(cid, d + 1);
  };
  for (const cid of n.children || []) walk(cid, 0);
  if (subs.length) {
    const sec = el("div", "fl-hover-sec");
    const lab = el("div", "fl-hover-lab"); lab.textContent = "Sub-goals";
    sec.appendChild(lab);
    for (const { d, c } of subs.slice(0, HOVER_SUB_CAP)) {
      const row = el("div", "fl-hover-sub" + (c.done ? " done" : "") + (c.blocked && !c.done ? " blocked" : ""));
      row.style.paddingLeft = (d * 12) + "px";
      const m = el("span", "m" + (!c.done && !c.blocked ? " open" : ""));
      m.textContent = c.done ? "✓" : c.blocked ? "⏸" : "";   // open = a hollow CSS ring, like the rows
      const t = el("span", "t"); t.textContent = c.text;
      row.append(m, t); sec.appendChild(row);
    }
    if (subs.length > HOVER_SUB_CAP) {
      const more = el("div", "fl-hover-more"); more.textContent = "…and " + (subs.length - HOVER_SUB_CAP) + " more";
      sec.appendChild(more);
    }
    card.appendChild(sec);
  }
  return card;
}

function showHoverCard(row: HTMLElement, sid: string, nid: string): void {
  const s = sessions.find((x) => x.sid === sid);
  const n = fleetNode(sid, nid);
  if (!s || !n) return;
  const byId = new Map([...(s.ledger?.tree || []), ...(s.ledger?.archivedTops || [])].map((x) => [x.id, x] as const));
  if (hoverCardEl) hoverCardEl.remove();
  const card = buildHoverCard(s, n, byId, Math.floor(Date.now() / 1000));
  card.addEventListener("mouseenter", () => { if (hoverHideT) { clearTimeout(hoverHideT); hoverHideT = undefined; } });
  card.addEventListener("mouseleave", scheduleHideHover);
  document.body.appendChild(card);
  hoverCardEl = card;
  // position: below the row, clamped into the viewport; flip above when the bottom lacks room
  const r = row.getBoundingClientRect();
  const w = card.offsetWidth, h = card.offsetHeight;
  card.style.left = Math.max(6, Math.min(r.left + 12, window.innerWidth - w - 6)) + "px";
  card.style.top = (r.bottom + 4 + h <= window.innerHeight - 6 ? r.bottom + 4 : Math.max(6, r.top - h - 4)) + "px";
}

(() => {
  const list = document.getElementById("fleet-list");
  if (!list) return;
  list.addEventListener("mouseover", (e) => {
    const row = (e.target as Element).closest?.(".ledger-tnode") as HTMLElement | null;
    const sid = row?.dataset.sid, nid = row?.dataset.nid;
    if (!row || !sid || !nid) return;                 // provisional rows (no nid) keep their native title
    if (hoverHideT) { clearTimeout(hoverHideT); hoverHideT = undefined; }
    const key = sid + "\0" + nid;
    if (key === hoverKey) return;                     // already shown for this row
    hoverKey = key;
    // INSTANT show (the one tooltip treatment, 2026-08-28): a hover IS the intent — the old 120ms
    // debounce made every row feel laggy; the key check above keeps child-element mouseovers cheap.
    showHoverCard(row, sid, nid);
  });
  list.addEventListener("mouseout", (e) => {
    const to = e.relatedTarget as Element | null;
    if (to && (to.closest?.(".ledger-tnode") || (hoverCardEl && hoverCardEl.contains(to)))) return;
    scheduleHideHover();
  });
  // a click navigates away (open / deep-link) and a scroll moves the anchor — both drop the card at once
  list.addEventListener("click", hideHoverCard);
  list.addEventListener("scroll", hideHoverCard, true);
})();

// Wire the top search bar (the user 2026-06-29): typing filters the fleet to sessions whose NAME matches.
// The input lives in the page body (kernel _fleet_page); installed once, re-renders on each keystroke.
// The trailing ✕ clears it (shown only while there's text), like any search bar — refocuses the input so you
// can keep typing.
(() => {
  const search = document.getElementById("fleet-search") as HTMLInputElement | null;
  const clear = document.getElementById("fleet-search-clear") as HTMLButtonElement | null;
  if (!search) return;
  // the shared TAG-ICON filter, right of the search box (the user 2026-08-25) — governs the OUTLINE
  const barEl = document.getElementById("fleet-search-bar");
  if (barEl) {
    const tagBtn = tagMenuButton("filter this outline by tag", (btn) => {
      openTagMenu(btn, {
        lens: () => surfaceLens(fleetViews, "outline"),
        unions: () => viewTagUnion(fleetViews),
        onApply: (l) => {
          const v = JSON.parse(JSON.stringify(fleetViews || { active: "all", tags: [] }));
          v.actives = Object.assign({}, v.actives, { outline: l });
          fleetViews = v;                                        // optimistic: the next feed push echoes it
          vscodeApi?.postMessage({ type: "setTimelineViews", views: v });
          render();
        },
        onConfigure: () => { vscodeApi?.postMessage({ type: "openTagsDialog" }); },
      });
    });
    barEl.appendChild(tagBtn);
    const chipsHost = document.createElement("span");
    chipsHost.id = "fleet-tagchips";
    chipsHost.setAttribute("style", "display:inline-flex;gap:5px;align-items:center;margin-left:2px;");
    barEl.appendChild(chipsHost);
    // the shared convention: gray alone at rest, accent + selection chips when narrowed
    syncFleetTagBtn = () => syncTagFilter(tagBtn, chipsHost, surfaceLens(fleetViews, "outline"), viewTagUnion(fleetViews), (l) => {
      const v = JSON.parse(JSON.stringify(fleetViews || { active: "all", tags: [] }));
      v.actives = Object.assign({}, v.actives, { outline: l });
      fleetViews = v;
      vscodeApi?.postMessage({ type: "setTimelineViews", views: v });
      render();
    });
    syncFleetTagBtn();
  }
  const syncClear = () => { if (clear) clear.hidden = search.value === ""; };
  search.addEventListener("input", () => { searchQuery = search.value; syncClear(); render(); });
  clear?.addEventListener("click", () => { search.value = ""; searchQuery = ""; syncClear(); search.focus(); render(); });
  syncClear();
})();

mountControls();
render();
vscodeApi?.postMessage({ type: "ready" });   // ask the kernel to push the initial fleet state (like feed/timeline)

// Hold the romp loader up until the ledgers actually land (the user 2026-06-29, who wanted the loading thing shown until
// the tasks are ready to render). The shared _pane_spin loader has an 8s backstop that would otherwise hide
// it over an EMPTY pane while a cold kernel is still building every session's ledger (which can take longer
// than 8s for a big fleet) — leaving a blank gap before the tasks paint. So while we're not loaded yet, keep
// re-asserting the loader, beating that backstop; stop the instant the data arrives (event-based via `loaded`).
const _keepLoader = setInterval(() => {
  if (loaded) { clearInterval(_keepLoader); return; }
  const spin = document.getElementById("pane-spin");
  if (spin) spin.classList.remove("gone");
}, 1000);

export {};   // module scope — keep its globals off feed.ts's (a global script)