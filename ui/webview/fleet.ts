// Fleet — a by-SESSION view that mirrors the chat's LEDGER BOX (the user 2026-06-23): each live session, then
// its goal tree beneath it — collapsible checkmark nodes, recency-coloured "(Xm ago)" times, the same .ledger-*
// look. It rides the FEED payload (connects app=feed, reads its `ledgers` slice — one per-session build_session
// ledger, the SAME tree the ledger box draws) — the proven feed channel. Completed top goals hide behind a
// bottom "Show completed" checkbox (default off). The recency colour helpers are copied verbatim from render.ts
// so the colours are IDENTICAL to the ledger box.
import { delegate, flash } from "./actions";
import { paintHeld, paintReleased, publishPaneHidden } from "./paint-gate";
import { applyTheme } from "./theme";
import { loadSettings, installSettingsSync, onExternalSettingsChange } from "./settings";
import { SessionViews, viewTagUnion } from "./session-views";
import { lensVisible, surfaceLens } from "./tag-lens";
import { openTagMenu, tagMenuButton, syncTagFilter } from "./tag-menu";
import { mintWriteId } from "./views-writes";
import { fleetVisibleRoots } from "./fleet-roots";
import { onlyTag, matchesOnly } from "./only-filter";
import { hostPrefix } from "./host-prefix";
import { ageColorReadable } from "./age-color";
import { liveNow } from "./feed-age";
import { TIP_GRACE_MS } from "./tip";
import { perfFrameHandler } from "./perf-telemetry";
import { linkifyPrRefs, installPrLinkOpener } from "./pr-links";
import { listenForFrames } from "./frame-listener";
import { openGear } from "./gear-host";
import { menuCard, addMenuItem, showMenuCard, openConfirmBox } from "./ctx-menu";
import { openTopTitles, endConfirmDetail, RENAME_SUBLINE, END_SESSION_STANDING } from "./clear-confirm";
import { addRestartRow, restartInterrupts, settleRestart } from "./restart-row";

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
interface FleetSession { sid: string; name: string; color: Color; status?: { state?: string } | null; ledger?: Ledger | null;
                         postalServiceOff?: boolean; mailOffWhy?: string;   // the session's mail is off, and why (isolation, a comment thread's default, an unreadable record; T356)
                         provisional?: boolean; }   // the cold-tab gate skipped this tab: the row's ledger is the store's, its jumps wait for the tab (plans/outline-pane-provisional-row.md)

const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;
// A `#123` in a goal row links to the PR page of the repository its session works in (pr-links.ts; the
// user 2026-09-06). The repo per session rides the feed frame's `sessions` rows (owner/repo, or null).
let repoBySid = new Map<string, string | null>();
// The link opens the PR and nothing else: capture-phase on the document, so the row's delegated jump
// under it never fires. Web → the viewer's browser; VS Code → the host's openExternal (view-routing.ts).
installPrLinkOpener(document, vscodeApi ? (m) => vscodeApi.postMessage(m) : undefined);

let sessions: FleetSession[] = [];
// Whether the FIRST feed payload has arrived (the user 2026-06-29): before it has, the fleet must NOT claim
// "no work" — that's the loading gap, where the data simply hasn't landed yet. We leave #fleet-list empty so
// the page's romp loader (_pane_spin) stays up, exactly like the other panes, until real data arrives.
let loaded = false;
let offNotice = false;   // the Task tracking switch's off frame is on screen as the notice (T404): the loader stands down without the page claiming loaded
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
let outlineViewsWriteSeq = 0;                   // per-page counter behind this pane's lens-write ids (mintWriteId)
// This pane's lens write: the frame copy it holds with only the outline lens changed, posted with a
// writeId and `edited: []` (the 2026-09-05 review) — the empty list is the kernel's word
// that the write changes NO tag, so the tags the copy carries are never applied over a newer store;
// only the lens lands. The pane ignores the viewsAck and settles from the next feed frame, as it
// always has (docs/read-side.md, the views contract).
function postOutlineLens(v: SessionViews) {
  vscodeApi?.postMessage({ type: "setTimelineViews", views: v, writeId: mintWriteId(++outlineViewsWriteSeq), edited: [] });
}
let syncFleetTagBtn: (() => void) | null = null;   // re-dress the tag button per the shared convention on each render
// Provisional cards (the user 2026-06-29): a session working a brand-new prompt the planner hasn't classified
// into a goal yet has NO ledger node, so it's invisible in the fleet — exactly the "things about to appear" the
// user wants to track. They ride the SAME feed payload (feed.asks, provisional:true), so surface a dotted
// signature row per such session here. Stored from each push.
interface ProvCard { sid: string; name: string; color: { bg: string; fg: string } | null; text: string }
let provCards: ProvCard[] = [];
// A PROVISIONAL ROW's nodes (plans/outline-pane-provisional-row.md): the store holds each goal's position, but a cold tab has no
// landing (the chat page holds no history for it, and the focus road has no time fallback), so the jump actions are withheld and
// the mark and the text say what the click cannot do; the row's own open still jumps into the session, whose build lands the row.
const WITHHELD = "nothing to land on until this tab is built: open the session first";
// Full feed-card lookup by goal id (the SAME asks slice provCards reads): the hover card joins a top goal's
// row to its feed card for the distiller BACKGROUND (cards carry it; ledger nodes don't — the user 2026-07-13).
let asksById = new Map<string, { background?: string | null; summary?: string | null; blockSummary?: string | null }>();
// The kernel's clock: `now` on the last frame, and WHEN that frame arrived (local ms). Every age on this pane
// reads nowSec(), which adds the local time elapsed since (feed-age.ts liveNow), so the browser's own clock
// never enters — read against Date.now(), every "(Xm ago)", the current goal's elapsed time and the recency
// cutoff were off by whatever skew sat between the two clocks, and moved only when a frame arrived (a quiet
// board on the delta path sends one every 60 s). `nowAt` is the WIRE arrival federation stamps on the merged
// frame it re-emits (a re-emit after a quiet hour must not move an age); a frame without one (the VS Code
// pipe hands frames straight to this pane) is arriving now.
let hostNow = Math.floor(Date.now() / 1000), hostNowAt = Date.now();
function nowSec(): number { return liveNow(hostNow, hostNowAt, Date.now()); }
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
  const prov = !!ctx.s.provisional;
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
  const mark = el("span", "ledger-tmark" + (prov ? "" : " lz-nav"));
  if (!prov) { mark.dataset.sid = s.sid; mark.dataset.nid = n.id; mark.dataset.act = resolved ? "gowork" : "goprompt"; }
  mark.textContent = n.done ? "✓" : n.blocked ? "⏸" : "";   // open = a hollow CSS ring (no glyph)
  // The mark's WHY tooltip + the text's full-goal tooltip both moved INTO the hover card (the user
  // 2026-07-13): it leads with markReason() as its state line and the untruncated title, so the native
  // titles would only pop redundantly on top of it. (markReason is hoisted below the render — one rule.)
  const txt = el("span", "ledger-ttext" + (prov ? "" : " lz-nav"));
  if (!prov) { txt.dataset.sid = s.sid; txt.dataset.nid = n.id; txt.dataset.act = "goprompt"; }   // text → the asking message; withheld on a provisional row (the hover card says why: no native titles here, 2026-07-13)
  highlightInto(txt, n.text, curSearch);   // search: highlight the matched substring (plain text otherwise)
  linkifyPrRefs(txt, repoBySid.get(s.sid) || null);   // `#123` in the goal → its PR page; a search hit span is walked, not skipped
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
  if (time.textContent && !prov) { time.classList.add("lz-nav"); time.dataset.sid = s.sid; time.dataset.nid = n.id; time.dataset.act = "gowork"; }   // time → where the work happened/resolved; withheld on a provisional row
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
// TWO measures of "not on screen" (the user 2026-09-07, whose dashboard froze on the return to its tab):
// the observer sees a display:none pane but never fires while the TAB is hidden — so an on-screen pane in
// a background tab kept rebuilding on every push, and nothing fired on the return — and document.hidden
// sees the tab but never a display:none pane. Both gate, both events release, and the payload (sessions,
// asksById, the pending hosts) is applied either way; only the rebuild waits (paint-gate.ts).
// The same two measures are this pane's hidden word for the kernel's pane shim (paint-gate.ts publishPaneHidden):
// the shim's zero-viewport probe misses a pane hidden after a first show in Chromium, so the release path and the
// hidden arm of visibilitychange publish document.hidden OR the observer's last word as window.__rompPaneHidden,
// on the same events, and nothing until the observer has spoken.
let paneVisible: boolean | null = null;   // the observer's last word; null until it speaks (the gate reads null as on screen; nothing is published for it)
let paneDirty = false;
let renameHold = false;    // an in-place rename is open on a row: render() waits (renameDirty remembers a push that arrived meanwhile); declared
let renameDirty = false;   //  beside the paint gate's state, inside the slice outline-visibility.test.ts lifts, so that harness needs no stub
let focusedHead: HTMLElement | null = null;  // the session head holding keyboard focus, the node itself: render() rebuilds the list on every push, and
//                                              only the rebuild that removes THIS node while it holds the focus puts the focus on the same session's new
//                                              head (round three: a restore keyed on the sid alone fired on every push once a head had been focused, and
//                                              pulled the window's focus out of the chat composer into the pane)
let rebuilding = false;    // render() is replacing the list: the focusout its removals fire (Chromium) is the rebuild's, not the user's focus leaving
function watchPaneVisibility(list: HTMLElement): void {
  if (typeof IntersectionObserver === "undefined") return;   // no observer → the tab's visibility alone gates
  new IntersectionObserver((entries) => {
    paneVisible = entries.some((e) => e.isIntersecting);
    releasePaint();
  }).observe(list);
}
// Synchronous on purpose: a paint inside the event handler is the earliest fresh frame after the
// compositor's cached one; a requestAnimationFrame hop is later at best and never fires in a hidden frame.
function releasePaint(): void {
  publishPaneHidden(document.hidden, paneVisible);
  if (!paintReleased(paneDirty, document.hidden, paneVisible)) return;
  paneDirty = false;
  render();
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) releasePaint(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) publishPaneHidden(true, paneVisible); });   // the hidden arm releases nothing, so the release path never publishes it
let paneWatching = false;
function render() {
  syncFleetTagBtn?.();
  const list = document.getElementById("fleet-list");
  if (!list) return;
  if (!paneWatching) { paneWatching = true; watchPaneVisibility(list); }
  // Nobody can see it: paint when it is shown — except the FIRST content, which paints through so the reveal
  // shows the list at once rather than the pane loader fading out over an empty pane (an empty list IS the
  // loader-up state; one hidden render buys an instant reveal).
  if (paintHeld(document.hidden, paneVisible, list.childElementCount > 0)) { paneDirty = true; return; }
  if (renameHold) { renameDirty = true; return; }   // a name is being edited in place: the push waits for Enter or Escape (the strip freezes the same way)
  const held = focusedHead;                                                                 // read before the rebuild: the one event that moves the focus
  const heldActive = !!held && held.isConnected && held.contains(document.activeElement);   //  back is this render removing the head that holds it
  rebuilding = true;
  list.replaceChildren();
  rebuilding = false;
  // BEFORE the first payload: leave the list EMPTY so the page's romp loader (_pane_spin over #fleet-list)
  // stays up — no child means it never hides — instead of flashing a false "no work" message (the user
  // 2026-06-29). A WS drop / kernel restart re-shows that same loader (romp:wsdown), so a restart shows the
  // swirl, not "no tasks".
  if (!loaded) { emptyShown = false; return; }
  if (pendingHosts.length) list.appendChild(hostLoadStrip());   // leads the list: what is still coming
  const masterNote = masterMailNote(sessions);
  if (masterNote) list.appendChild(masterNote);
  const sd = showDone();
  const grouped = isGrouped();
  curFoldMode = foldMode();   // snapshot the sticky Collapse/Expand mode once for this render
  paintFoldButtons();
  const now = nowSec();   // the kernel's clock, moving between frames (see hostNow)
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
      if (s.provisional) { sec.classList.add("fl-prov-sess"); }   // the light mark: this tab's row is the store's until the tab is built
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
      if (s.postalServiceOff && s.mailOffWhy !== "master") {
        // T356: a session whose mail is off says so on its row, quietly; the master's sessions share one pane note
        const mo = el("span", "fl-mail-off");
        mo.textContent = (s.mailOffWhy === "unreadable" || s.mailOffWhy === "flags") ? "mail held" : "mail off";
        mo.title = s.mailOffWhy === "unreadable" ? "this session's record cannot be read: mail waits until it is repaired"
          : s.mailOffWhy === "flags" ? "the session settings file cannot be read: mail waits until it is written again"
          : s.mailOffWhy === "thread" ? "a comment thread's mail is off until it is broken out"
          : "this session neither sends nor receives peer mail";
        head.appendChild(mo);
      }
      head.title = s.provisional ? "Open this session: its transcript has not been loaded since the restart, so its goals are read from the store and their jumps wait for the tab" : "Open this session";
      head.dataset.act = "open"; head.dataset.sid = s.sid;   // click-safe: action lives on the #fleet-list delegate
      head.tabIndex = 0; head.setAttribute("role", "button");   // focusable: Enter opens, the menu key or Shift+F10 opens the row's menu (2026-09-16)
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
      head.tabIndex = 0; head.setAttribute("role", "button");   // the same reach as a session with a tree
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
  // this rebuild removed the head that held the focus: the same session's new head takes it back, so a row reached by keyboard
  // (and the row the menu returns focus to) is not lost to the next push (round two, low a). Bounded to that event and to a pane
  // whose document has the focus: a head focused earlier and left for a goal row or the chat composer is never refocused by a
  // push (round three, high: each push pulled the focus into the pane and the composer lost the rest of the sentence)
  if (held && !held.isConnected) {
    const next = heldActive && document.hasFocus() && held.dataset.sid ? headOf(held.dataset.sid) : null;
    focusedHead = next;
    next?.focus({ preventScroll: true });
  }
}

// One note above the list for the sessions the master default isolates, instead of a chip on every row: under
// an isolating master most rows would carry the same chip, and the rows a session opted in stand out by lacking it.
export const MASTER_MAIL_TIP = "its mail is off by the master default (the * key in session-flags.json): the lane's mailbox toggle opts it back in, or clear the master to open every session";
function masterMailNote(all: { mailOffWhy?: string }[]): HTMLElement | null {
  const n = all.filter((s) => s.mailOffWhy === "master").length;
  if (!n) return null;
  const note = el("div", "fl-master-mail");
  note.textContent = "Mail off by the master default for " + n + (n === 1 ? " session" : " sessions");
  note.title = MASTER_MAIL_TIP;
  return note;
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

// every frame's synchronous handling time is measured (perf-telemetry.ts: one clientDiag row a
// minute, read by `romp perf client`); the handler itself is unchanged
// …and handed the merged frames by direct call from federation.js when this page has it (frame-listener.ts)
listenForFrames(perfFrameHandler("fleet", (m) => vscodeApi?.postMessage(m), (e: MessageEvent) => {
  const m = e.data;
  if (!m) return;
  if (m.type === "delta") {
    // The shim reassembles every {type:"delta"} frame into the whole message before a bundle sees it, and
    // federation's remote sockets never dial for deltas — so one reaching this handler means a host handed
    // the pane a kernel frame unreassembled. Say so and ask for the whole slot (needSlot: what the shim
    // itself sends for a delta it cannot apply) rather than sit on the last frame while every update is
    // dropped on the floor (fail loudly, never degrade). Run for real in fleet-live-clock.test.ts.
    console.error("outline: a delta frame reached the pane unreassembled — asking the kernel for the whole slot");
    vscodeApi?.postMessage({ type: "clientDiag", surface: "outline", what: "delta-unapplied", data: { slot: m.slot, rev: m.rev } });
    vscodeApi?.postMessage({ type: "needSlot", slot: m.slot });
    return;
  }
  // The kernel's answer to this pane's Restart session (2026-09-23), aimed at the pane that asked: the row's
  // latch lifts on the event, never on a clock. A refusal is LOUD with it — the bell entry the shell keeps,
  // carrying the session so the entry jumps there ({romp:'notify'}, the feed's road for a refused gesture);
  // this pane has no toast of its own and a restart that did not happen must not pass in silence.
  if (m.type === "restarted" || m.type === "restartFailed") {
    if (typeof m.id === "string") settleRestart(m.id);
    if (m.type === "restartFailed")
      window.parent?.postMessage({ romp: "notify", kind: "refused",
        text: "Couldn’t restart “" + String(m.name || m.id) + "” — " + String(m.text || "unknown error"), sid: String(m.id || "") }, "*");
    return;
  }
  // a kernel older than this page does not know the op (it advertises restartSession in its caps): the row
  // re-arms on the refusal instead of latching for good, and says why
  if (m.type === "unknownOp" && m.op === "restartSession") {
    for (const s of sessions) settleRestart(s.sid);
    window.parent?.postMessage({ romp: "notify", kind: "refused",
      text: "This romp kernel is older than the dashboard and has no Restart session — end the session and revive it instead." }, "*");
    return;
  }
  if (m.type !== "feed") return;                     // the Outline rides the FEED payload (proven channel); reads its `ledgers`
  // the Task tracking switch off (T404): the frame carries `off` and no ledgers; the notice the kernel rendered shows in
  // place of the list, and nothing below applies; the next real frame swaps back
  const ttOff = document.getElementById("tt-off"), ttList = document.getElementById("fleet-list");
  if (ttOff) ttOff.hidden = !m.off;
  if (ttList) ttList.hidden = !!m.off;
  if (m.off) {
    // the notice IS this frame's content (T404 round two, medium 1): _keepLoader below stands down while it shows, and the
    // loader itself goes now (its observer watches the list, which the off frame leaves empty). The page does not claim
    // loaded (round three, low 2): a later frame with no ledgers array (federation before any host built its ledgers)
    // then brings the loader back instead of leaving an empty list with no loader and no notice
    offNotice = true;
    document.getElementById("pane-spin")?.classList.add("gone");
    return;
  }
  offNotice = false;
  // "loaded" means the kernel actually BUILT the fleet's ledgers (the key is present, even if []) — NOT merely
  // that some feed message arrived. A feed push can reach us before the (cold) ledger build finishes; treating
  // that as loaded would drop the loader onto an empty pane (the user 2026-06-29). Until ledgers land, keep the
  // loader up (render() bails, leaving the list empty so _pane_spin holds).
  if (m.views && typeof m.views === "object") fleetViews = m.views as SessionViews;   // rides the feed payload (2026-08-25)
  // the attached-but-not-yet-merged hosts, from the merge itself (the ONLY writer; absent = none pending)
  pendingHosts = Array.isArray(m.pendingHosts) ? m.pendingHosts.filter((h: any) => typeof h === "string") : [];
  pendingDead = Array.isArray(m.pendingDead) ? m.pendingDead.filter((h: any) => typeof h === "string") : [];
  if (typeof m.now === "number") {
    hostNow = m.now;
    hostNowAt = typeof m.nowAt === "number" ? m.nowAt : Date.now();   // the pair travels together: the frame's clock, and when THAT frame arrived
  }
  if (Array.isArray(m.sessions))
    repoBySid = new Map(m.sessions.filter((s: any) => s && typeof s.sid === "string")
      .map((s: any) => [s.sid as string, typeof s.githubRepo === "string" ? s.githubRepo : null] as const));
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
}));
window.addEventListener("storage", (e: StorageEvent) => { if (e.key === "romp:settings") { applyTheme(document, loadSettings()); render(); } });   // theme/colormap change → reskin + recolour
applyTheme(document, loadSettings());   // the persisted theme applies at boot (2026-08-28)
// VS Code webviews have per-origin storage and never see another pane's `storage` events — gear
// saves arrive as settingsSync host messages (PR #763 item 3; the raiser re-fires romp:settings,
// which onExternalSettingsChange below already handles in the browser too)
installSettingsSync();
onExternalSettingsChange((s) => { applyTheme(document, s); render(); });

// THE ROW'S MENU (the user 2026-09-16, who wanted to right-click a session's name here to rename or delete it):
// a right-click on a session's head, or the ContextMenu key or Shift+F10 on the focused head, opens Rename and Delete
// through the shared builder (ctx-menu.ts: the chat's menu dress through the theme tokens, dismissal, keyboard reach).
// Both verbs take the tab strip's own roads, never a second one: Rename edits the name in place (the strip's
// startTabRename shape: Enter commits, Escape cancels, a blur commits) and posts renameSession, nothing renamed locally
// ahead of the kernel, whose push brings the new name to every surface; Delete is the strip's close button: the End
// confirm (its title, the open goals named in the detail, its buttons) and then endSession and closeTab in its order.
// The row leaves on the kernel's push (the kill is the event), never locally ahead of it. plans/sessions-pane-session-menu.md.
function sessionRow(sid: string): FleetSession | undefined { return sessions.find((s) => s.sid === sid); }
// the row's head as it stands NOW: render() rebuilds the list on every push (every half second to three seconds), so a node
// captured when the menu opened may be detached by the time an item is picked; every pick resolves by sid (the strip's own
// rule for its menu: the id only, never the node under the cursor)
function headOf(sid: string): HTMLElement | null {
  return document.querySelector('#fleet-list .fl-head[data-sid="' + CSS.escape(sid) + '"]') as HTMLElement | null;
}
function displayName(sid: string): string {
  return sessionRow(sid)?.name || (headOf(sid)?.querySelector(".fl-name") as HTMLElement | null)?.textContent || "";
}

function showSessionMenu(x: number, y: number, sid: string, viaKeyboard: boolean): void {
  if (!sid) return;
  // on close the focus returns to the row's head by sid (a push may have rebuilt the list under the open menu, and the builder's
  // own return refocuses only a still-connected opener), unless the close followed the focus out of this document: the window's
  // blur fires when a click lands in another pane, and a refocus then pulled the focus back to the head (Firefox lost the composer)
  // built by hand (menuCard → rows → showMenuCard, the builder's documented road for a card with a row the
  // standard shape cannot express): Restart session latches its own label in place, so it needs its node
  const menu = menuCard({ className: "fl-sess-menu" });
  addMenuItem(menu, { label: "Rename", sub: RENAME_SUBLINE, pick: () => startRowRename(sid) });
  // Restart session between the two (the user 2026-09-23): it changes the session least of the three — the
  // row leaves nothing behind, where Delete ends it. Everything it needs is resolved by SID at build time,
  // never off the node under the cursor, which the next push rebuilds (this pane's own rule, above).
  addRestartRow(menu, sid, {
    name: displayName(sid),
    working: restartInterrupts(sessionRow(sid)?.status?.state),
    titles: openTopTitles((sessionRow(sid)?.ledger?.tree || []) as any),   // the live ledger at click time, as Delete reads it
    confirm: (title, detail, buttons, cb) => openConfirmBox(title, detail, buttons, cb),
    post: () => vscodeApi?.postMessage({ type: "restartSession", id: sid }),
  });
  addMenuItem(menu, { label: "Delete", sub: "ends the session; its history stays on disk", danger: true, pick: () => confirmEndSession(sid) });
  showMenuCard(menu, x, y, { viaKeyboard, onClose: () => { if (!document.hasFocus()) return; const h = headOf(sid); if (h) h.focus({ preventScroll: true }); } });
}

function startRowRename(sid: string): void {
  const head = headOf(sid);                                     // resolved now: a push since the menu opened rebuilt the row
  const nm = head?.querySelector(".fl-name") as HTMLElement | null;
  if (!head || !nm || renameHold) return;                       // the row is gone (the session ended): nothing to edit, nothing held
  const row: HTMLElement = head;
  const full = displayName(sid);
  const p = hostPrefix(full, sid);                              // a federated row shows "host:name": the host is this viewer's metadata and the far kernel
  const base = p ? p.rest : full;                               //  knows the bare name, so the name alone is edited and posted (the strip's rule)
  const input = document.createElement("input");
  input.className = "fl-rename";
  input.value = base;
  input.spellcheck = false;
  input.size = Math.max(base.length, 4);
  const fixed = p ? document.createElement("span") : null;      // the host stays put beside the input, rendered as the row renders it and not editable
  if (fixed) { fixed.className = "host-prefix"; fixed.textContent = p!.host; }   //  (the strip's shape; round three, low)
  let settled = false;
  const finish = (commit: boolean) => {
    if (settled) return;
    settled = true;
    const v = input.value.trim();
    const hadFocus = document.activeElement === input;          // Enter or Escape: the row takes the focus back; a blur has already moved it elsewhere
    if (input.isConnected) input.replaceWith(nm);
    fixed?.remove();
    if (hadFocus && row.isConnected) row.focus({ preventScroll: true });
    renameHold = false;
    if (renameDirty) { renameDirty = false; render(); }
    if (commit && v && v !== base) vscodeApi?.postMessage({ type: "renameSession", id: sid, name: v });   // the strip's message; the kernel's push renames the row
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();   // the list's keys (Enter opens, the menu key) are not the input's
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  for (const ev of ["click", "mousedown", "dblclick", "contextmenu"]) input.addEventListener(ev, (e) => e.stopPropagation());   // never the head's open
  nm.replaceWith(input);
  if (fixed) input.before(fixed);
  if (!input.isConnected) return;                               // never hold render() for an input that is not in the document
  renameHold = true;
  input.focus();
  input.select();
}

function confirmEndSession(sid: string): void {
  const name = displayName(sid);
  const titles = openTopTitles((sessionRow(sid)?.ledger?.tree || []) as any);   // the live ledger at click time, as the strip reads it
  openConfirmBox("End \u201c" + name + "\u201d?",
    endConfirmDetail(titles, END_SESSION_STANDING),
    [{ label: "End session", value: "end", danger: true }, { label: "Cancel", value: "" }],
    (v) => {
      if (v !== "end") return;   // Cancel, Escape, the backdrop: nothing
      vscodeApi?.postMessage({ type: "endSession", id: sid });   // the strip's two messages, in its order
      vscodeApi?.postMessage({ type: "closeTab", id: sid });
    });
}

(() => {
  const list = document.getElementById("fleet-list");
  if (!list) return;
  list.addEventListener("contextmenu", (e) => {
    const head = (e.target as Element).closest?.(".fl-head") as HTMLElement | null;
    if (!head || !head.dataset.sid) return;   // the goal rows below keep their own clicks; a right-click there does nothing new
    e.preventDefault(); e.stopPropagation();
    showSessionMenu(e.clientX, e.clientY, head.dataset.sid, false);
  });
  document.addEventListener("focusin", (e) => {   // which session head has the focus, for the restore after a rebuild
    focusedHead = (e.target as Element).closest?.(".fl-head") as HTMLElement | null;
  });
  list.addEventListener("focusout", (e) => {      // the focus left the list (a goal row, another frame, the menu): no head holds it. A head the rebuild
    if (rebuilding) return;                        //  removes fires this too (Chromium), and that one is the rebuild's to settle
    const to = e.relatedTarget as Node | null;
    if (!to || !list.contains(to)) focusedHead = null;
  });
  list.addEventListener("keydown", (e) => {
    const head = (e.target as Element).closest?.(".fl-head") as HTMLElement | null;
    if (!head || !head.dataset.sid || e.target !== head) return;
    if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
      e.preventDefault();
      const r = head.getBoundingClientRect();
      showSessionMenu(r.left + 12, r.bottom, head.dataset.sid, true);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openSession(head.dataset.sid);
    }
  });
})();

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
    return n.blocked ? "needs you" : "not yet done";
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
  card.append(state);
  // a provisional row's jumps are withheld (plans/outline-pane-provisional-row.md); the card, the row's one tooltip, says why
  if (s.provisional) { const held = el("div", "fl-hover-state"); held.textContent = WITHHELD; card.append(held); }
  card.append(title);
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
  const card = buildHoverCard(s, n, byId, nowSec());
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
          postOutlineLens(v);
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
      postOutlineLens(v);
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

// Keep every "(Xm ago)", the current goal's elapsed time, the recency cutoff and the slider's range honest
// between frames: the clock-derived parts move on the local clock's deltas (nowSec) every 15 s, whatever the
// wire is doing. A whole render(), not a per-element repaint: it is what every frame already runs, the ledgers
// are small, and the cutoff filter has to be able to DROP a row as it ages out of the window — which no
// repaint of the row's own text could do. Not for a pane nobody can see: a hidden tab (document.hidden) or a
// pane the shell has hidden (display:none gives the iframe a ZERO viewport, the shim's zero-viewport probe;
// document.hidden stays false for it) skips the tick, and the first visible moment catches up once
// (visibilitychange, or the resize the iframe gets when it is shown again) — not on every flip, only after a
// tick was skipped. Frames still render as they arrive. The probe alone, not the pane's published word the shim's
// paneHidden() also reads: a pane hidden after a first show keeps its size in Chromium, so this tick runs for it,
// and the cost is one 15 s pass nobody sees.
const paneHidden = () => document.hidden || window.innerWidth === 0 || window.innerHeight === 0;
let tickSkipped = false;   // a refresh fell while hidden: the pane owes one catch-up render
const refreshIfVisible = () => {
  if (!loaded) return;
  if (paneHidden()) { tickSkipped = true; return; }
  tickSkipped = false;
  render();
};
setInterval(refreshIfVisible, 15000);
const catchUp = () => { if (tickSkipped) refreshIfVisible(); };
document.addEventListener("visibilitychange", catchUp);
window.addEventListener("resize", catchUp);

// Hold the romp loader up until the ledgers actually land (the user 2026-06-29, who wanted the loading thing shown until
// the tasks are ready to render). The shared _pane_spin loader has an 8s backstop that would otherwise hide
// it over an EMPTY pane while a cold kernel is still building every session's ledger (which can take longer
// than 8s for a big fleet) — leaving a blank gap before the tasks paint. So while we're not loaded yet, keep
// re-asserting the loader, beating that backstop; stop the instant the data arrives (event-based via `loaded`).
const _keepLoader = setInterval(() => {
  if (loaded) { clearInterval(_keepLoader); return; }
  if (offNotice) return;   // the Task tracking switch's notice is the content: no loader over it (T404)
  const spin = document.getElementById("pane-spin");
  if (spin) spin.classList.remove("gone");
}, 1000);

export {};   // module scope — keep its globals off feed.ts's (a global script)

// the notice's button while the Task tracking switch is off (T404): the settings on Task tracking
document.getElementById("tt-off-btn")?.addEventListener("click", () => {
  // through gear-host's one road (the shell forwards it into the settings iframe at the Task tracking tab); a standalone
  // /feed or /fleet page has no shell to ask and hosts no gear, so it goes to the dashboard with the tab named in the hash,
  // which the landing opens (T404 round two, medium 2: the bare post reached nothing on the only page where the notice shows)
  if (!openGear(window, { tab: "tasks" })) window.location.assign("/" + window.location.search + "#settings=tasks");
});
