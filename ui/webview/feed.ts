// romp feed — a stream of rounded "deliverable cards" on a backdrop. Each card is
// ONE deliverable (a turn's "did" phrase) from some session, newest on top. The
// session name links to that session's tab; the checkbox dismisses the card; the
// message expands to a (pre-generated) action paragraph.
//
// Rendering is KEYED + INCREMENTAL: cards are kept alive across the host's live
// pushes and updated in place — never torn down — so hovering one doesn't flicker
// when the fleet streams new deliverables in.
import { distillText, applyDistillLine, distillPending } from "./distiller-line";

interface FeedItem {
  itemId: string;
  sid: string;
  name: string;
  color: { bg: string; fg: string } | null;
  did: string;
  ask: string;
  t: number;            // epoch seconds
  live: boolean;
  trgb: [number, number, number];   // hawaii recency color — tints the CARD background
  relevance: "DONE" | "DECISION" | "DETAILS" | "UNTAGGED";
  origin?: "user" | "agent";        // who prompted the turn: the user typed vs a peer's message
  inAsk?: boolean;                  // this reply is linked into some ask → renders inside it, not standalone
  standalone?: boolean;             // host-computed standalone-card eligibility (user-origin + DONE/DECISION + unlinked + past REQUESTS_FLOOR)
}

// Relevance: a per-card colored label (DONE/DECISION/DETAILS; UNTAGGED gets none)
// PLUS header toggle "badges" that turn each class off/on. All on by default;
// UNTAGGED is always visible (no toggle). Per-card dismiss is separate curation.
const RELEVANCES = ["DONE", "DECISION", "DETAILS"] as const;
const RELEVANCE_LABEL: Record<string, string> = { DONE: "done", DECISION: "decision", DETAILS: "details" };
const relevanceFilter = new Set<string>(RELEVANCES);

// Origin: agent-prompted turns (peer postal messages, not the user's asks) are
// HIDDEN by default — the feed is the user's inbox, not the fleet's internal chatter.
// The header "internal N" chip toggles them back on.
let showInternal = false;

// Asks inbox (the DEFAULT view; request registry REQUESTS.md): one persistent
// card per OPEN ask of the user's, sorted into THREE state columns derived from each
// ask's newest link. Clear (the user-only, binary) removes it — inbox-zero. The
// deliverables stream is the safety-net behind the header toggle.
interface AskLinked { did: string; relevance: string; t: number; reply_id: string; status: "done" | "question" | "update"; sid: string; name: string; color?: { bg: string; fg: string } | null; trgb?: [number, number, number]; answer?: boolean }
interface DecisionBrief { context: string; question: string; options: string[] | null; sid: string; t: number }
interface AskQuestion { reply_id: string; sid: string; name: string; t: number; brief: DecisionBrief | null; qtype?: "decision" | "action" | "idea"; nodeId?: string }
interface AskPath { name: string; sid: string; color: { bg: string; fg: string } | null; since: number; lastPhrase: string }
// One node of the ask's request DAG (flat list, root first; nest via children ids;
// a node under two parents appears in both → render twice, dim the repeat).
interface AskTreeNode {
  id: string; kind: "ask" | "handoff"; text: string; who: string;
  whoSid: string; whoColor: { bg: string; fg: string } | null;   // agent → colored session link
  whoWorking?: boolean;                                          // that agent is currently WORKING → yellow dot before its name
  status: "done" | "question" | "open"; t: number; last: number;
  mt?: number;                                                   // last-modified (done/block segment) → blocked/done nodes deep-link to where they RESOLVED, not where they were minted
  anchorUuid?: string | null;                                    // EXACT turn uuid for this node's WORK target (where it resolved — an assistant turn); mark/time zones jump here. null when unresolvable
  promptAnchorUuid?: string | null;                              // EXACT turn uuid for this node's PROMPT target = the user's minting message (a user turn) → prompt-intent jumps (title, text) resolve BY ID (kernel 92e23ff)
  why?: string; blockWhy?: string; doneWhy?: string;             // planner's one-sentence rationales — revealed on hover in the modal
  derived?: boolean;                                             // done by roll-up/roll-down (kernel), not explicit → DIMMED ✓ disc
  followupPending?: boolean;                                     // this sub was optimistically reopened by a per-sub follow-up → "↻ Followed up" chip (kernel flatten, judges 047264f)
  summary?: string | null;                                       // the DISTILLER's key takeaway for a completed goal (artifact or 1-3 sentences) → the modal's auto-line for a DONE node (kernel flatten 78fc97b)
  blockSummary?: string | null;                                  // the BLOCK-distiller's decision brief for a blocked goal → the modal's auto-line for a BLOCKED node (kernel 466393c); null until produced
  trgb?: [number, number, number];                               // last-activity recency tint (timestamp)
  children: string[]; rows: AskLinked[];
}
interface AskItem {
  itemId: string; sid: string; name: string; color: { bg: string; fg: string } | null;
  text: string; t: number; created: number; live: boolean;
  done: number; needsYou: number; linked: AskLinked[]; turnId: string;
  trgb: [number, number, number];
  column: "working" | "needs_input" | "completed";   // RAW kernel value (build_feed): working/needs_input/completed. askColumn() maps it to the local Column. NOT "asks" — that was a stale lie that silently broke `it.column === "asks"` checks.
  openQuestions: AskQuestion[];                    // live unanswered DECISIONs → decision sub-cards
  openPaths: AskPath[];                            // open leaves → "waiting on X" drop-point lines
  reopened?: boolean;                              // resurrected: a question arrived AFTER the user cleared it
  followupPending?: boolean;                       // you followed up on a settled card → optimistically reopened, awaiting the judge's re-file (kernel)
  recheck?: boolean;                               // soft-block you've already replied to (targeted card-reply OR a plain thread reply after it) → de-urgented (dotted), dropped from the "need input" count, until the judge resolves or re-blocks it (kernel build_feed; the user 2026-06-27)
  autoFiled?: boolean;                             // settled → moved to COMPLETED by the auto-filing rule (keeps the green ring)
  explicitDone?: boolean;                          // every path explicitly DONE-stamped → blue ring (blue+green when settled agrees)
  turnIds?: string[];                              // typed turns that minted/amended this card
  // the owning session is live-blocked (permission/picker, or stopped on an API error) ON this card's
  // work → the card itself files under BLOCKED (the user's ruling 2026-06-11; apiError 2026-06-16).
  blocked?: { state: string; since?: number; what: string; status?: number; category?: string; text?: string;
              tooLong?: boolean;   // apiError: a "prompt is too long" error (on you → compact) vs a transient API error
              toName?: string; toSid?: string; fromName?: string; msgId?: string; body?: string };   // parkedHandoff adds to*/from*
  blockWhy?: string;                               // planner's one-sentence "why blocked" → now the HOVER tooltip on the blocked card's auto-line (the user 2026-06-18)
  doneWhy?: string;                                // planner's one-sentence "why done" → now the HOVER tooltip on the completed card's auto-line (the user 2026-06-18)
  summary?: string | null;                         // distiller's key takeaway for a COMPLETED goal → the done card's one auto-written line (kernel asks.append); null until produced
  blockSummary?: string | null;                    // block-distiller's decision brief for a BLOCKED goal → the blocked card's one auto-written line (kernel 466393c); null until produced
  summaryAnchorUuid?: string | null;               // click the summary line → the biggest contiguous assistant-text block in the work span (kernel _seg_best_text; the user 2026-06-22)
  origin?: { peer: string; peerSid: string; color: { bg: string; fg: string } | null } | null;  // courier handoff: planted by a peer's message → "↪ from <peer>"
  waitingOn?: { peerSid: string; name: string; color: { bg: string; fg: string } | null; inCycle: boolean } | null;  // unanswered msg out to a live peer → "Awaiting <peer>" chip (peer name in native colour, no emoji; kernel _wait_for_graph; the user 2026-06-22)
  awaiting?: { why?: string | null } | null;       // AWAITING flavor: held in Working, ⏳ awaiting badge — waiting on dispatched/delegated work (agents/subagents/a build), NOT on you (kernel build_feed; the user 2026-06-22). The peer case rides waitingOn; this carries the generic "why".
  groupTitle?: string;                             // host: this ask shares a typed turn with siblings → the group's title
  groupN?: number;                                 // host: sibling count for that turn (>1 ⇒ fold into one group card)
  provisional?: boolean;                           // a LIVE-PROMPT placeholder (kernel _provisional_card): the session is working an in-progress turn the planner hasn't classified yet. No goal node (empty tree) — dim, non-interactive, no clear/nudge/modal; replaced by the real card once the planner places the segment.
  tree: AskTreeNode[];                             // the ask's DAG, rendered as a tree in the expanded body
}
// A GROUP = N sibling asks minted by ONE typed turn (shared turnId), folded into a
// single card. DERIVED at render time from the current asks, so membership shrinks
// as the user clears members (a lone survivor falls back to a normal single card).
interface AskGroup {
  turnId: string; title: string; members: AskItem[];   // members sorted chronologically
  name: string; color: { bg: string; fg: string } | null; sid: string;   // shared asking session
  t: number; trgb: [number, number, number]; column: Column; live: boolean;
}
let asks: AskItem[] = [];
let viewAsks = true;           // asks inbox is the default; deliverables behind the toggle
const expandedAsks = new Set<string>();
// Per-node collapse state, key = askId + ":" + nodeId. INVERTED sense: a node is
// EXPANDED (its history rows AND its descendant subtree visible) by default;
// collapsing it adds the key here and hides its WHOLE subtree. Empty set = the
// tree is fully open, which matches the always-expanded look it had before
// collapse was deepened to cover children, not just rows.
const collapsedNodes = new Set<string>();
let fullscreenAskId: string | null = null; // ask itemId OR group key "g:<turnId>" shown in the modal (single-click)
let modalRenderedId: string | null = null; // last target the modal body was built for → reset body cache on change
let openFollowUpOnRender = false;           // a card's "Follow up" opened the modal → pop+focus its composer on the next renderModal (the user 2026-06-22)
// Per-sub FOLLOW-UP target (the user 2026-06-17): a blocked sub-node's "↳ follow up" re-points the (robust,
// outside-the-tree-body) footer composer at THAT sub instead of the whole card, so the answer files under it
// and unblocks just that branch. null = the composer follows up on the whole card (the default).
let followupSub: { itemId: string; title: string } | null = null;
// Set by renderModal (captures the footer composer); called from a tree node's "↳ follow up" — kept as a
// module ref (not threaded through renderTreeBody) so the per-node button reads the CURRENT opener at click time.
let openSubFollowUp: ((itemId: string, title: string) => void) | null = null;
let hoverAskId: string | null = null;      // transient hover focus (white border + previewed journey)
let pinnedAskId: string | null = null;     // double-click PIN (persists after hover-leave)
// effective focus = hover ?? pinned; the white border + lit timeline journey follow it.
function applyFocus() {
  const eff = hoverAskId ?? pinnedAskId;
  for (const [id, card] of askEls) card.classList.toggle("focused", id === eff);
  for (const [tid, card] of groupEls) card.classList.toggle("focused", "g:" + tid === eff);
}
const askEls = new Map<string, HTMLElement>();
// Optimistically-cleared item ids: Clear animates a card out + posts askClear, but a feed push that
// arrives BEFORE the kernel processes the clear still lists the card — re-rendering it strips the
// `.dismissing` class (updateAskCard resets className) so it pops back, then a later push drops it. We
// suppress those ids from incoming payloads until the kernel's payload confirms the clear (no longer
// lists them), so a stale push can't resurrect a card mid-dismiss (the user 2026-06-19).
const pendingCleared = new Set<string>();
// A LIFO of recently-cleared card batches, holding the AskItem data itself (a single Clear pushes [it]; a
// Clear-all pushes the whole batch). "Undo clear" pops the latest and re-inserts those cards IMMEDIATELY —
// optimistic restore — so the card reappears on click instead of waiting on the kernel round-trip + next feed
// build. Mirrors the kernel's _undo_clear (restores the most-recent clear batch). (the user 2026-06-27.)
const clearedStack: AskItem[][] = [];
// The inverse of pendingCleared: ids we've optimistically RESTORED, kept (with their cached card) until a
// kernel push actually carries them again — otherwise the very next push (before the kernel un-archived) would
// replace `asks` and drop the just-restored card, a flicker. Dropped once the kernel lists the id.
const pendingRestored = new Map<string, AskItem>();
// Group cards keyed by turnId, stored under "g:"+turnId. The focus state
// (hoverAskId/pinnedAskId) holds EITHER a raw ask itemId OR a group key
// "g:"+turnId; applyFocus + focusAnchorId understand both.
const groupEls = new Map<string, HTMLElement>();


// The three columns. The HOST decides each ask's column by DAG path accounting
// (completed only when every subgraph node is DONE); we just map its snake_case.
type Column = "asks" | "needsInput" | "completed";
function askColumn(it: AskItem): Column {
  // it.column is AUTHORITATIVE — the kernel already floors a live permission/picker block to needs_input (and
  // parked handoffs / placeholders set it too), so the client just maps its snake_case. We no longer re-route
  // by it.blocked: that crafty override existed only because the kernel used to report a picker-blocked card as
  // "working" while showing it under Blocked — it now reports needs_input directly (the user 2026-06-29). An
  // API-error card stays in its natural column (working): the kernel keeps column=working for it (a transient
  // stall, not a block), so it lands in "asks" with just the "⚠ API error" chip + Retry.
  return it.column === "needs_input" ? "needsInput" : it.column === "completed" ? "completed" : "asks";
}

// How opaque the recency tint is over the (black) page — low = a faint, very
// see-through wash of the hawaii color; the colormap itself darkens with age.
const TINT_ALPHA = 0.22;
interface Detail { id: string; t?: number; paragraph: string; next_steps?: string[]; src?: string; }
type DetailState = { state: "loading" | "ready" | "failed"; reason?: string; data?: Detail };

const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;

let items: FeedItem[] = [];
// Card-display prefs read straight from the shared 'romp:settings' (the kernel's ⛭ gear writes it; same
// document as this feed bundle). Default ON. These gate the CARDS only — the modal always shows everything
// (the user 2026-06-17). `!== false` so a missing key defaults to shown.
function feedPrefs(): { subgoals: boolean } {
  try {
    const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
    return { subgoals: s.subgoals !== false };
  } catch { return { subgoals: true }; }
}
// names of sessions currently WORKING → a working dot before that name everywhere
// it renders (card titles, modal title, group name). Pushed in each feed message.
let workingSet = new Set<string>();
// Ensure a `.fwork-dot` sits immediately before `nameEl` iff `on` (idempotent on
// re-render). The name's own text/color are untouched.
function setWorkDot(nameEl: HTMLElement | null, on: boolean) {
  if (!nameEl) return;
  const prev = nameEl.previousElementSibling;
  const has = !!prev && prev.classList.contains("fwork-dot");
  if (on && !has) nameEl.parentElement?.insertBefore(el("span", "fwork-dot"), nameEl);
  else if (!on && has) prev!.remove();
}

let hostNow = Math.floor(Date.now() / 1000);
let showDismissed = false;
let dismissedCount = 0;
let canUndoClear = false;   // host: cleared.jsonl has rows → the UndoClear button shows
// Expand-for-detail: which items are open, plus the per-item detail we've fetched.
// Both survive a host re-render (render() reconstructs the open blocks from these).
const expanded = new Set<string>();
const details = new Map<string, DetailState>();
const cardEls = new Map<string, HTMLElement>();   // itemId -> live card element (reused)
// FLIP-across-identity (the user 2026-06-29): which render KEY covered each goal itemId on the LAST render.
// A goal's card can change identity — a group ("g:"+turnId) dissolving to a solo ask ("a:"+itemId), a goal
// absorbed under an umbrella ("a:"+umbrellaId) — which is a DIFFERENT DOM node, so the normal FLIP (reuse one
// node) can't slide it and it would pop. We map the new card back to its predecessor's old rect so it slides
// from there instead of appearing from nowhere. Rebuilt every render.
let prevItemKey = new Map<string, string>();

function el(tag: string, cls?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

// A lightweight yes/no overlay for the feed. Separate from the ask #feed-modal
// (a different state machine); Esc or a backdrop click cancels.
function feedConfirm(message: string, confirmLabel: string, onConfirm: () => void): void {
  const back = el("div", "fconfirm-back");
  const box = el("div", "fconfirm-box");
  const msg = el("div", "fconfirm-msg"); msg.textContent = message;
  const btns = el("div", "fconfirm-btns");
  const cancel = el("button", "fconfirm-btn"); cancel.textContent = "Cancel";
  const ok = el("button", "fconfirm-btn primary"); ok.textContent = confirmLabel;
  btns.append(cancel, ok);
  box.append(msg, btns);
  back.append(box);
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  cancel.onclick = (e) => { e.stopPropagation(); close(); };
  ok.onclick = (e) => { e.stopPropagation(); close(); onConfirm(); };
  back.onclick = (e) => { if (e.target === back) close(); };
  document.addEventListener("keydown", onKey);
  document.body.appendChild(back);
  ok.focus();
}

// A feed click on a session link. Live → open its tab. Closed (tab gone) → offer
// to revive it: the kernel's own confirmRevive dialog routes only to chat
// clients, so from a feed-only view an "open" would silently no-op; ask here and
// post reviveSession directly (the kernel reopens the most-recent incarnation).
function openOrReviveSession(sid: string, live: boolean, name: string): void {
  if (live) { vscodeApi?.postMessage({ type: "openSession", id: sid }); return; }
  feedConfirm(`“${name}” is closed — revive it?`, "Revive",
    () => vscodeApi?.postMessage({ type: "reviveSession", id: sid }));
}

function relAge(sec: number): string {
  const s = Math.max(0, sec);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function dayLabel(t: number, now: number): string {
  const d = new Date(t * 1000);
  const ymd = (x: Date) => `${x.getFullYear()}-${x.getMonth()}-${x.getDate()}`;
  if (ymd(d) === ymd(new Date(now * 1000))) return "Today";
  if (ymd(d) === ymd(new Date((now - 86400) * 1000))) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

// ---- header (built once, then updated in place): title + "N need input" + live ----
let headerBuilt = false;
let elTitle: HTMLElement, elMeta: HTMLElement;

function ensureHeader() {
  if (headerBuilt) return;
  const head = document.getElementById("feed-head")!;
  head.innerHTML = "";
  elTitle = el("span", "fh-title"); elTitle.textContent = "romp";
  elMeta = el("span", "fh-meta");
  head.append(elTitle, elMeta);    // no toggles, chips, or buttons — one view, nothing clickable
  headerBuilt = true;
}

// Standalone deliverable cards: a user-origin reply NOT linked to any ask, tagged
// DONE or DECISION (the granular/unexpected work the user never explicitly asked
// for). Linked replies, agent-internal turns, and routine (details) turns never
// get their own card.
function standaloneItems(): FeedItem[] {
  return items.filter((i) => i.standalone);   // host-computed (gated by REQUESTS_FLOOR) — avoids flooding the columns with pre-registry backlog
}

function updateHeader() {
  ensureHeader();
  // "N need input" = the actionable count: asks whose newest link is a DECISION
  // plus standalone DECISION deliverables (everything sitting in column 2).
  const needInput = asks.filter((a) => askColumn(a) === "needsInput" && !a.recheck).length   // re-check cards aren't on you anymore → out of the count
    + standaloneItems().filter((i) => i.relevance === "DECISION").length;
  const liveN = new Set([
    ...asks.filter((a) => a.live).map((a) => a.sid),
    ...items.filter((i) => i.live).map((i) => i.sid),
  ]).size;
  elMeta.innerHTML = `<span class="fh-need">${needInput} need input</span>`
    + (liveN ? ` · <span class="fh-live">${liveN} live</span>` : ``);
}

// ---- standalone deliverable card (same v3 anatomy as an ask card) ----
// row 1 = deliverable text (full width); row 2 = owner name; row 3 = age (bottom-left)
// + [Clear] (bottom-right). (No tally — a standalone deliverable has no subgraph.) Clear
// shares the asks' cleared.jsonl (reply ids work in askClear). Whole-card click locates the turn.
function makeCard(it: FeedItem): HTMLElement {
  const card = el("div", "fitem");
  card.dataset.key = "i:" + it.itemId;
  card.dataset.id = it.itemId;
  card.title = "click for detail";

  const main = el("div", "fitem-main");
  const row1 = el("div", "fask-row1");
  const title = el("div", "fcard-title nav");      // the deliverable phrase (headline)
  title.title = "jump to this on the timeline";
  const time = el("span", "ftime");
  row1.append(title);
  const row2 = el("div", "fask-row2");
  const idwrap = el("div", "fask-id");
  const name = el("a", "fname"); name.title = "open this session";
  idwrap.append(name);
  row2.append(idwrap);
  const actions = el("div", "fask-actions");
  const clr = el("button", "fdismiss"); clr.textContent = "Clear"; clr.title = "clear this item (inbox-zero)";
  actions.append(clr);
  const row3 = el("div", "fask-row3"); row3.append(time, actions);   // time bottom-left · Clear bottom-right
  main.append(row1, row2, row3);
  card.append(main);

  // same click model as ask cards: body → modal, title → locate the originating
  // REQUEST (anchor:'prompt'), i.e. where the user wrote it — not the agent's work line.
  // The deliverable's itemId IS its typed turn (request + reply share the id), so the
  // anchor just selects the prompt glyph over the work bar.
  card.onclick = () => { fullscreenAskId = "i:" + it.itemId; renderModal(); };
  title.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: it.itemId, sid: it.sid, t: it.t, anchor: "prompt" }); };
  name.onclick = (ev) => { ev.stopPropagation(); openOrReviveSession(it.sid, it.live, it.name); };
  clr.onclick = (ev) => {
    ev.stopPropagation();
    pendingCleared.add(it.itemId);   // suppress until the kernel confirms — no mid-dismiss pop-back
    clearedStack.push([it as unknown as AskItem]);
    card.classList.add("dismissing");
    vscodeApi?.postMessage({ type: "askClear", itemId: it.itemId });   // reply ids share cleared.jsonl
    setTimeout(() => { if (cardEls.get(it.itemId) === card && card.classList.contains("dismissing")) { card.remove(); cardEls.delete(it.itemId); } }, 180);
  };

  const a = card as any;
  a._time = time; a._name = name; a._title = title;
  return card;
}

function updateCard(card: HTMLElement, it: FeedItem) {
  const a = card as any;
  card.className = "fitem" + (it.live ? " live" : " dead");
  const [r, g, b] = it.trgb;
  card.style.background = `rgba(${r}, ${g}, ${b}, ${TINT_ALPHA})`;
  card.style.borderColor = `rgba(${r}, ${g}, ${b}, ${Math.min(TINT_ALPHA + 0.2, 0.9)})`;
  a._title.textContent = it.did;
  a._name.textContent = it.name;
  if (it.color) a._name.style.color = it.color.bg;
  setWorkDot(a._name, workingSet.has(it.name));   // working dot before the session name
  a._time.textContent = relAge(hostNow - it.t);
}

// ---- expand → detail ----
function toggleExpand(id: string) {
  if (expanded.has(id)) { expanded.delete(id); render(); return; }
  expanded.add(id);
  const d = details.get(id);
  if (!d || d.state === "failed") {       // not fetched yet, or retry after a failure
    const it = items.find((i) => i.itemId === id);
    // Only DONE/DECISION get a generated paragraph; DETAILS/UNTAGGED don't, so the
    // host reads cache-only for them and never spawns (no "generating…" wait).
    const generate = it ? (it.relevance === "DONE" || it.relevance === "DECISION") : false;
    details.set(id, { state: "loading" });
    vscodeApi?.postMessage({ type: "expand", itemId: id, generate });
  }
  render();
}

// Expanded view: the ASK (original request) on top, the RESPONSE below it.
// Response = the generated detail paragraph for DONE/DECISION; for routine items
// (no paragraph) it falls back to the deliverable summary. Signature-guarded so an
// open, hovered card never flickers on a host repush.
function renderExpandInto(slot: HTMLElement, it: FeedItem) {
  const d = details.get(it.itemId);
  const respSig = !d || d.state === "loading" ? "loading"
    : d.state === "failed" ? "f:" + (d.reason || "")
    : "r:" + (d.data?.paragraph || "") + "¦" + (d.data?.next_steps || []).join("¦");
  const sig = "a:" + it.ask + "||" + respSig;
  if ((slot as any)._sig === sig) return;
  (slot as any)._sig = sig;
  slot.innerHTML = "";
  const box = el("div", "fexpand");

  // ASK (top)
  const askWrap = el("div", "fx-ask");
  const askLab = el("div", "fx-label"); askLab.textContent = "Ask";
  const askBody = el("div", "fx-body"); askBody.textContent = it.ask || "(no recorded request)";
  askWrap.append(askLab, askBody);
  box.appendChild(askWrap);

  // RESPONSE (below)
  const respWrap = el("div", "fx-resp");
  const respLab = el("div", "fx-label"); respLab.textContent = "Response";
  respWrap.appendChild(respLab);
  if (!d || d.state === "loading") {
    const b = el("div", "fx-body loading"); b.textContent = "Generating…"; respWrap.appendChild(b);
  } else if (d.state === "failed") {
    // routine/legacy → no paragraph; the deliverable summary IS the response
    const b = el("div", "fx-body"); b.textContent = it.did; respWrap.appendChild(b);
  } else {
    const para = el("div", "fx-body"); para.textContent = d.data!.paragraph; respWrap.appendChild(para);
    if (Array.isArray(d.data!.next_steps) && d.data!.next_steps.length) {
      const ul = el("ul", "fdetail-steps");
      for (const s of d.data!.next_steps) { const li = document.createElement("li"); li.textContent = s; ul.appendChild(li); }
      respWrap.appendChild(ul);
    }
  }
  box.appendChild(respWrap);
  slot.appendChild(box);
}

// ---- ask card (the inbox unit) ----
// Anatomy (the user 2026-06-14): row1 = ask text, full width across the top; row2 = worker
// name (identity color, clickable) on its own row below it; row3 = age bottom-left, status
// badges + Clear bottom-right. Stacking the age/actions onto their own row frees the title
// and the (often long) session name to use the full card width instead of competing for it.
// Click the CARD → expand + light the DAG path on the timeline.
// Expanded body = the request DAG as a tree of NODES (state machine only); each
// node clicks to reveal its OWN reply history; ? nodes carry a decision sub-card.
function makeAskCard(it: AskItem): HTMLElement {
  const card = el("div", "fitem ask");
  card.dataset.key = "a:" + it.itemId;

  const main = el("div", "fitem-main");
  // ROW 1 — ask title, full width across the top (the user 2026-06-14); hit-area still
  // fits its text (it must NOT flex-grow, or blank space right of it triggers locate)
  const row1 = el("div", "fask-row1");
  const title = el("div", "fcard-title nav"); title.title = "locate this in the text";
  const time = el("span", "ftime");
  row1.append(title);
  // ROW 2 — the session name on its own row, directly below the title
  const row2 = el("div", "fask-row2");
  const idwrap = el("div", "fask-id");
  const name = el("a", "fname"); name.title = "open this session";
  // ↪ courier handoff provenance: this goal was planted by a peer's message — shows
  // "↪ from <sender>" beside the owning session, click opens the sender. Hidden unless origin.
  // It's a DIRECT child of row2 (not nested in idwrap) so that when the name + provenance + the
  // reopened/Followed-up chips can't all fit, row2 WRAPS it to a new line instead of the provenance
  // overflowing on top of the chips (the user 2026-06-20). idwrap's flex-grow still right-aligns it
  // against the chips whenever it does fit on the one line.
  const origin = el("a", "fask-origin"); origin.style.display = "none";
  origin.title = "this work was delegated from another session — click to open it";
  idwrap.append(name);
  const actions = el("div", "fask-actions");
  const reBadge = el("span", "fask-reopened"); reBadge.textContent = "reopened"; reBadge.title = "a question arrived after you cleared this"; reBadge.style.display = "none";
  const fupBadge = el("span", "fask-followedup"); fupBadge.textContent = "↻ Followed up"; fupBadge.title = "you followed up — reopened to Working; the planner will re-file it on the next pass"; fupBadge.style.display = "none";
  const waitOnBadge = el("span", "fask-waiton"); waitOnBadge.style.display = "none";   // "Awaiting <peer>" / "Deadlock <peer>", peer name in native colour (the user 2026-06-22)
  const blkBadge = el("a", "fask-blocked"); blkBadge.style.display = "none";   // ⏸ live permission/picker block → click opens the session
  const apiBadge = el("span", "fask-apierror"); apiBadge.textContent = "⚠ API error"; apiBadge.style.display = "none";   // red: session stopped on an API error
  const apiRetry = el("button", "fdismiss fretry"); apiRetry.textContent = "Retry"; apiRetry.title = "send “retry” into this session to resume"; apiRetry.style.display = "none";
  const revive = el("button", "fdismiss frevive"); revive.textContent = "Revive"; revive.title = "bring this offline session back so the parked hand-off is delivered"; revive.style.display = "none";
  // monochrome wireframe hourglass (the SAME line-icon drawn for the queued-messages header — render.ts
  // hourglassIcon) instead of the ⏳ emoji, which clashed with the app's stroked-icon look (the user 2026-06-29).
  // stroke=currentColor → it picks up the badge's teal; .fask-wait is inline-flex so the icon + word align.
  const waitBadge = el("span", "fask-wait"); waitBadge.style.display = "none";
  waitBadge.innerHTML = '<svg class="fask-wait-glyph" viewBox="0 0 16 16" width="11" height="11" fill="none" '
    + 'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M4 3 H12 L8 8 L12 13 H4 L8 8 Z"/></svg><span>awaiting</span>';
  waitBadge.title = "Waiting on work it started, not on you. Clears when the result lands.";
  const clr = el("button", "fdismiss"); clr.textContent = "Clear"; clr.title = "clear this ask (inbox-zero; the one human-asserted fact)";
  // "Nudge" on the card itself (the user 2026-06-18): a one-click status follow-up for a WORKING card,
  // beside Clear, so you don't have to open the modal. Sends the canned status question down the SAME
  // follow-up path (askFollowUp → the kernel quotes the goal as context). Shown only for working cards.
  const nudge = el("button", "fdismiss ffollow fask-nudge") as HTMLButtonElement; nudge.textContent = "Nudge"; nudge.title = "nudge this session for a status update on this goal"; nudge.style.display = "none";
  // "Follow up" on a BLOCKED or COMPLETED card (the user 2026-06-22): one click opens THIS goal's modal and
  // jumps straight into its follow-up composer (via openFollowUpOnRender), saving the open-modal-then-click
  // step. The modal's own Follow up button stays. Working cards get Nudge instead — the two are mutually
  // exclusive by column, so the footer never shows both.
  const cardFup = el("button", "fdismiss ffollow fask-fup"); cardFup.textContent = "Follow up"; cardFup.title = "open this goal and jump straight into a follow-up"; cardFup.style.display = "none";
  cardFup.onclick = (ev) => { ev.stopPropagation(); fullscreenAskId = it.itemId; openFollowUpOnRender = true; renderModal(); };
  // Session-STATE badges (⏸ approval / ⚠ API error / ⏳ waiting) ride the SESSION-NAME row, right after the
  // name — they describe the session's live state, and keeping them OFF the action row stops them shoving
  // the buttons past the card's right edge on a narrow card (the user 2026-06-19; mirrors the ↻ Followed-up
  // chip moved up 2026-06-18). idwrap is flex:1 so the name ellipsizes before the badge is ever clipped.
  idwrap.append(waitBadge, apiBadge, blkBadge);
  // The action row holds ONLY buttons now (Retry / Nudge / Follow up / Clear).
  actions.append(apiRetry, revive, nudge, cardFup, clr);
  // "↪ from <peer>" provenance + the "reopened"/"↻ Followed up" chips ride the name row's right side;
  // row2 wraps them onto a new line when there isn't room, so the provenance never overlaps a chip
  // (the user 2026-06-20). origin sits left of the chips, matching the "from … · Followed up" reading order.
  row2.append(idwrap, origin, reBadge, fupBadge, waitOnBadge);
  // ROW 3 — timestamp bottom-left · action buttons bottom-right
  const row3 = el("div", "fask-row3"); row3.append(time, actions);
  // the user's handoff spec (2026-06-10): below the main session, list the OTHER
  // sessions this ask was handed to — but only while they are LIVE-WORKING on
  // an unfinished branch. Idle or finished recipients disappear; presence on
  // the list therefore always means active, so the dot is always on.
  const checklist = el("div", "fask-checklist");   // inline sub-goal list (top 2 levels); filled in updateAskCard
  const delegations = el("div", "fask-delegations");
  // The DISTILLER's own line, restored 2026-06-29 (the user: show everything the distiller produces — just NOT
  // the planner's why-created/why-blocked/why-done rationales). One line per card: a completed card shows the
  // takeaway (summary), a blocked card the decision brief (blockSummary). Shown ONLY once it exists — no
  // generating-state placeholder (which used to stick) and no why tooltip. Filled in updateAskCard.
  const distill = el("div", "fask-distill");
  // ⏳ AWAITING cue (the user 2026-06-29): a small romp swirl spinning in the SAME body spot the distiller line
  // will eventually fill — a completed/blocked card shows its takeaway there; a WORKING card that's awaiting
  // dispatched/delegated work shows the spinning swirl instead, a glanceable "in flight, not stalled" sign.
  // The "why" rides beside it (it was tooltip-only on the ⏳ badge). Shown only while awaiting; see updateAskCard.
  const awaitSpin = el("div", "fask-awaiting"); awaitSpin.style.display = "none";
  const awaitGlyph = el("span", "fask-awaiting-swirl"); awaitGlyph.setAttribute("aria-hidden", "true");
  const awaitWhy = el("span", "fask-awaiting-why");
  awaitSpin.append(awaitGlyph, awaitWhy);
  main.append(row1, row2, row3, distill, awaitSpin, checklist, delegations);   // no expand button — body click opens the modal
  card.append(main);
  // Follow-up lives in the modal now (the user 2026-06-10), not on the card.

  // title → locate the turn the card stands for. A normal card anchors on "prompt" (the originating
  // user message). A DELEGATION card (it.origin) has NO originating user prompt — it was planted by a
  // peer's postal message — so "prompt" lands on whatever user turn is nearest in time (an unrelated
  // message — the user hit this). For origin cards anchor on "work" instead, landing where the
  // delegation was processed, mirroring the modal tree-node nav (rompinfra, the user 2026-06-16).
  // agent → open session; Clear → inbox-zero. stopPropagation so the card-body handlers don't also fire.
  const titleAnchor = it.origin ? "work" : "prompt";
  // PREFERRED: the card's root node carries the EXACT turn uuid (kernel 996ebd7) → id-based deep-link,
  // killing the nearest-time miss (delegation cards land on where the work happened, not an unrelated
  // user message). The card's root node is the one whose id IS the card's itemId. Null → time fallback.
  // (The chat's kind guard still refuses a non-user uuid for "prompt"-intent, so a normal card with only
  // a reply uuid falls back to time as before — no regression; delegation "work" cards deep-link.)
  // The root node carries TWO uuids (bugs 92e23ff): anchorUuid = the WORK turn (where it resolved), and
  // promptAnchorUuid = the user's MINTING message (a user turn). A "prompt"-intent title jumps by the prompt
  // uuid (resolves by id on the user turn — no kind-guard refusal, no time-landing heuristic); a "work"
  // (origin) title keeps the work uuid. cardAnchorUuid stays the WORK uuid — goNoted (the why-line) reuses it.
  const rootNode = it.tree?.find((n) => n.id === it.itemId);
  const cardAnchorUuid = rootNode?.anchorUuid ?? null;
  const titleUuid = titleAnchor === "prompt" ? (rootNode?.promptAnchorUuid ?? null) : cardAnchorUuid;
  // A PROVISIONAL placeholder has no goal node / timeline anchor — clicking anywhere just opens the live
  // session (go see what it's working on); the modal, timeline deep-link, and path-hover are all skipped.
  title.onclick = (ev) => { ev.stopPropagation(); if (it.provisional) { openOrReviveSession(it.sid, it.live, it.name); return; } vscodeApi?.postMessage({ type: "showOnTimeline", itemId: it.itemId, sid: it.sid, t: it.t, anchor: titleAnchor, anchorUuid: titleUuid }); };
  // (The auto-line is plain text now — no deep-link — so no onclick here; its hover tooltip = the planner's
  // why, set in updateAskCard. The inline sub-goal checkmarks remain clickable via wireNodeZones.)
  name.onclick = (ev) => { ev.stopPropagation(); openOrReviveSession(it.sid, it.live, it.name); };
  clr.onclick = (ev) => {
    ev.stopPropagation();
    pendingCleared.add(it.itemId);   // suppress from incoming pushes until the kernel confirms the clear
    clearedStack.push([it]);         // cache for an instant optimistic Undo clear
    card.classList.add("dismissing");
    vscodeApi?.postMessage({ type: "askClear", itemId: it.itemId });
    setTimeout(() => { if (askEls.get(it.itemId) === card && card.classList.contains("dismissing")) { card.remove(); askEls.delete(it.itemId); } }, 180);
  };
  nudge.onclick = (ev) => {
    ev.stopPropagation();
    if (nudge.disabled) return;        // guard the double-fire: a nudge with no visible change invites a re-click
    nudge.disabled = true;             // immediate feedback (the user 2026-06-24): the click ALWAYS shows it took,
    nudge.textContent = "Nudged";      // before the kernel round-trip — then it self-restores. See ./actions / CLAUDE.md.
    nudge.classList.add("romp-acted");
    setTimeout(() => nudge.classList.remove("romp-acted"), 280);
    vscodeApi?.postMessage({ type: "askFollowUp", itemId: it.itemId, nudge: true, text: "Status on the goal above: what's done, what's left, and is anything blocked waiting on a decision from me?" });   // nudge:true → romp authored it → gray bubble. text mirrors AUTO_NUDGE_TEXT (bin/romp-kernel)
    setTimeout(() => { if (nudge.isConnected) { nudge.disabled = false; nudge.textContent = "Nudge"; } }, 1500);
  };
  // HOVER (120ms intent debounce so sweeps don't spam) → white border + preview
  // this card's timeline journey. LEAVE → restore the pinned card's journey, or
  // clear if none pinned.
  let hoverTimer: number | undefined;
  card.addEventListener("mouseenter", () => {
    if (it.provisional) return;                        // no timeline path for a placeholder
    hoverTimer = window.setTimeout(() => {
      hoverTimer = undefined;
      hoverAskId = it.itemId; applyFocus();
      vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, locate: false });
    }, 120);
  });
  card.addEventListener("mouseleave", () => {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = undefined; }
    if (hoverAskId === it.itemId) {
      hoverAskId = null; applyFocus();
      const pin = focusAnchorId(pinnedAskId);
      if (pin) vscodeApi?.postMessage({ type: "showAskPath", itemId: pin, locate: false });   // back to the pin (ask or group)
      else vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, off: true });      // clear
    }
  });
  // card BODY single click → open the modal; double click → pin/unpin (locks the
  // journey in place; hovering others still previews, leave returns to the pin).
  // Debounced ~220ms so a double never opens the modal first.
  let pending: number | undefined;
  card.addEventListener("click", () => {
    if (it.provisional) { openOrReviveSession(it.sid, it.live, it.name); return; }   // placeholder → open the session
    if (pending) { clearTimeout(pending); pending = undefined; return; }   // 2nd click — let dblclick handle it
    pending = window.setTimeout(() => {
      pending = undefined;
      fullscreenAskId = it.itemId;
      vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, locate: false });
      render();
    }, 220);
  });
  card.addEventListener("dblclick", () => {
    if (it.provisional) return;                        // a placeholder can't be pinned (no timeline path)
    if (pending) { clearTimeout(pending); pending = undefined; }
    pinnedAskId = pinnedAskId === it.itemId ? null : it.itemId;
    applyFocus();
    // double-click = PIN + jump the TIMELINE to the painted DAG (the user's
    // ruling: hover/single-click only highlight; only a double pans)
    if (pinnedAskId === it.itemId) vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, locate: false, jump: true });
    else if (!pinnedAskId && hoverAskId !== it.itemId) vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, off: true });
  });

  const a = card as any;
  a._title = title; a._name = name; a._time = time; a._reopened = reBadge; a._followedup = fupBadge;
  a._waitOn = waitOnBadge;
  a._blocked = blkBadge; a._wait = waitBadge;
  a._apiBadge = apiBadge; a._apiRetry = apiRetry; a._revive = revive; a._nudge = nudge; a._cardFup = cardFup; a._clr = clr;
  a._delegations = delegations;
  a._checklist = checklist;
  a._distill = distill;
  a._awaitSpin = awaitSpin; a._awaitWhy = awaitWhy;
  a._origin = origin;
  return card;
}

function updateAskCard(card: HTMLElement, it: AskItem) {
  const a = card as any;
  card.className = "fitem ask" + (it.live ? " live" : " dead") + (it.itemId === (hoverAskId ?? pinnedAskId) ? " focused" : "") + (it.itemId === pinnedAskId ? " pinned" : "") + (it.provisional ? " provisional" : "");
  // PROVISIONAL placeholder: a dim, italic, non-interactive card from the live prompt while the planner
  // hasn't classified the in-progress turn yet. No Clear/Nudge (nothing to curate), no auto-line, no tree.
  card.style.opacity = it.provisional ? ".62" : "";
  a._title.style.fontStyle = it.provisional ? "italic" : "";
  const [r, g, b] = it.trgb;
  card.style.background = `rgba(${r}, ${g}, ${b}, ${TINT_ALPHA})`;
  // GHOST prompt: a provisional placeholder gets a dashed outline so it reads as not-yet-real (the user
  // 2026-06-19), distinct from the solid recency-tinted border of a real card. Reset to solid when the
  // planner replaces it with the classified card.
  if (it.provisional) {
    card.style.borderStyle = "dashed";
    card.style.borderWidth = "1.5px";
    card.style.borderColor = "rgba(255, 255, 255, 0.45)";
  } else if (it.recheck) {
    // RE-CHECK: a soft-block you've already replied to — de-urgented with the same dotted treatment as a
    // provisional card (you're no longer the bottleneck), but readable, until the judge resolves or
    // re-blocks it (the user 2026-06-27).
    card.style.borderStyle = "dashed";
    card.style.borderWidth = "1.5px";
    card.style.borderColor = "rgba(255, 255, 255, 0.32)";
  } else {
    card.style.borderStyle = "";
    card.style.borderWidth = "";
    card.style.borderColor = `rgba(${r}, ${g}, ${b}, ${Math.min(TINT_ALPHA + 0.2, 0.9)})`;
  }
  // a re-check card dims slightly (between a normal card and a provisional ghost) so it reads as "handled, pending"
  if (!it.provisional) card.style.opacity = it.recheck ? ".8" : "";
  a._title.textContent = it.text;
  a._name.textContent = it.name;
  if (it.color) a._name.style.color = it.color.bg;
  setWorkDot(a._name, workingSet.has(it.name));   // working dot before the session name
  // ↪ courier handoff: planted by a peer's message → "↪ from <sender>", click opens the sender
  const og = a._origin as HTMLElement;
  if (it.origin && it.origin.peer) {
    og.style.display = "";
    // "↪ from" in dim gray (the Clear-button gray), the peer name in the bold session-name style next to
    // it — its own identity colour, like every other session name in this row (the user 2026-06-16).
    og.replaceChildren();
    og.style.color = "";
    const pre = el("span", "fask-origin-pre"); pre.textContent = "↪ from ";
    const peer = el("span", "fask-origin-peer"); peer.textContent = it.origin.peer;
    if (it.origin.color) peer.style.color = it.origin.color.bg;
    og.append(pre, peer);
    og.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: it.origin!.peerSid }); };
  } else {
    og.style.display = "none";
  }
  a._time.textContent = relAge(hostNow - it.t);
  a._reopened.style.display = it.reopened ? "" : "none";
  // "↻ Followed up" while the kernel has optimistically reopened a settled card you followed up on, before
  // the judge re-files it (it.followupPending self-clears on the next pass). (judges delegation, 2026-06-17.)
  // RE-CHECK chip (the user 2026-06-27): a soft-block you've replied to — targeted (followupPending) OR a
  // plain thread reply (kernel `recheck`, the superset). Reads "↩ re-checking" so you know it registered and
  // isn't on you, pending the judge's verdict. Falls back to the legacy "↻ Followed up" text otherwise.
  if (it.recheck) {
    a._followedup.style.display = "";
    a._followedup.textContent = "↩ re-checking";
    a._followedup.title = "you've replied — no longer waiting on you; the judge will resolve it or re-block it on the next pass";
  } else if (it.followupPending) {
    a._followedup.style.display = "";
    a._followedup.textContent = "↻ Followed up";
    a._followedup.title = "you followed up — reopened to Working; the planner will re-file it on the next pass";
  } else {
    a._followedup.style.display = "none";
  }
  // "Awaiting <peer>" — this session has an unanswered message out to a live peer (kernel _wait_for_graph):
  // held in Working, not stalled, so auto-nudge skips it. The peer NAME renders in its NATIVE identity colour
  // (like the "↪ from" provenance), no emoji prefix (the user 2026-06-22). A mutual-wait CYCLE keeps the red
  // styling + a "Deadlock" label instead of "Awaiting".
  const wo = it.waitingOn;
  if (wo) {
    a._waitOn.replaceChildren();
    const woPre = el("span", "fask-waiton-pre"); woPre.textContent = wo.inCycle ? "Deadlock " : "Awaiting ";
    const woName = el("span", "fask-waiton-name"); woName.textContent = wo.name;
    if (wo.color && wo.color.bg) woName.style.color = wo.color.bg;   // the peer's own identity colour
    a._waitOn.append(woPre, woName);
    a._waitOn.title = wo.inCycle
      ? "MUTUAL WAIT — this session and " + wo.name + " are each waiting on the other (a deadlock); auto-nudge surfaces it instead of nudging"
      : "this session has an unanswered message out to " + wo.name + " — waiting on its reply, not stalled, so auto-nudge skips it";
    a._waitOn.className = "fask-waiton" + (wo.inCycle ? " fask-waiton-cycle" : "");
    a._waitOn.style.display = "";
  } else {
    a._waitOn.replaceChildren();
    a._waitOn.style.display = "none";
  }
  a._nudge.style.display = (it.column === "working" && !it.provisional && !it.recheck && it.blocked?.state !== "apiError") ? "" : "none";   // Nudge only on a real working card — not a re-checking one, and not an API-error one (Retry is its action) (the user 2026-06-18/27/29)
  a._cardFup.style.display = ((it.column === "needs_input" || it.column === "completed") && !it.provisional) ? "" : "none";   // Follow up on blocked/completed cards (the user 2026-06-22)
  a._clr.style.display = it.provisional ? "none" : "";   // a placeholder has nothing to curate — no Clear
  // ⏳ awaiting: held in Working, waiting on work it dispatched/delegated (agents, a subagent, a build).
  // The peer case already shows the "Awaiting <peer>" chip (waitingOn), so suppress the generic badge then.
  const aw = it.awaiting;
  a._wait.style.display = (aw && !it.waitingOn) ? "" : "none";
  if (aw && !it.waitingOn) {
    a._wait.title = aw.why || "Waiting on work it started, not on you. Clears when the result lands.";
  }
  // SPINNING SWIRL + a short caption in the card body (the user 2026-06-29): any card that's "in motion but
  // not on you" — the ones that read as dashed/ghosted in Working — shows the spinning romp swirl where the
  // distiller line will eventually land, with a couple words saying what's happening. Three cases:
  //  • AWAITING — held in Working, waiting on dispatched/delegated work (the kernel's why, else a generic line).
  //  • PROVISIONAL — a dashed live-prompt placeholder: the session is working a BRAND-NEW ask the planner
  //    hasn't classified into a goal yet (THAT's why it's dashed — it's not a real card yet) → "Working…".
  //  • RE-CHECK — a soft-block you've ALREADY replied to, de-urgented (dashed) until the judge re-judges → "Re-checking…".
  // The blocked placeholder (provisional but needs-input: "Awaiting your input") is on YOU, not in motion, so
  // it's excluded — only the working-column dashed cards spin.
  // Each case pairs a short body caption with a FULLER tooltip (hover the swirl/caption) that explains what's
  // actually happening — including, for a provisional card, WHY it's dashed.
  let spinCaption: string | null = null, spinTip = "";
  if (aw && !it.waitingOn) {
    spinCaption = aw.why || "Waiting on work it dispatched…";
    spinTip = aw.why
      ? aw.why + ". Not on you."
      : "Waiting on work it started, not on you. Clears when the result lands.";
  } else if (it.provisional && it.column === "working") {
    spinCaption = "Working…";
    spinTip = "A new prompt, not yet sorted into a goal. Placeholder until it is.";
  } else if (it.recheck) {
    spinCaption = "Re-checking…";
    spinTip = "You've replied. The judge will resolve or re-block it.";
  } else if (distillPending(it.column === "completed", it.column === "needs_input", it.summary, it.blockSummary, !!it.blocked)) {
    //  • DISTILLING (the user 2026-06-29) — a resolved card whose distiller hasn't produced its line yet:
    //    a completed goal awaiting its takeaway (summary), or a blocked goal awaiting its decision brief
    //    (blockSummary). The same swirl spins in the distiller-line spot until the line lands, so a card that
    //    "is in motion" (the distiller LLM is running) reads as busy rather than blank. Excludes a live
    //    permission/picker block (on YOU). distillPending lives in ./distiller-line so the test EXECUTES it.
    spinCaption = "Distilling…";
    spinTip = it.column === "completed"
      ? "Writing the key takeaway…"
      : "Writing the decision brief…";
  }
  a._awaitSpin.style.display = spinCaption ? "" : "none";
  if (spinCaption) { a._awaitWhy.textContent = spinCaption; a._awaitSpin.title = spinTip || spinCaption; }
  // The swirl's "Re-checking…" caption + tooltip REPLACES the separate "↩ re-checking" chip (the user
  // 2026-06-29: don't show both) — drop the chip the recheck branch set above when the swirl is saying it.
  if (spinCaption === "Re-checking…") a._followedup.style.display = "none";
  // ⏸ live block badge: the session is stopped mid-turn on a permission prompt /
  // picker FOR THIS CARD's work — the card files under BLOCKED while it lasts
  const isApiErr = it.blocked?.state === "apiError";
  a._blocked.style.display = (it.blocked && !isApiErr) ? "" : "none";
  if (it.blocked && !isApiErr) {
    a._blocked.textContent = it.blocked.state === "permission" ? "⏸ approval" : "⏸ picker";
    a._blocked.title = it.blocked.what + " — click to open the session";
    a._blocked.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: it.sid }); };
  }
  // The DISTILLER's line (restored 2026-06-29): completed card → takeaway (it.summary), blocked card → decision
  // brief (it.blockSummary), shown ONLY when produced; never a generating placeholder, never the planner's why.
  // The rule lives in ./distiller-line so distiller-line.test.ts can EXECUTE it (a regex pin let it silently
  // turn off once — the user 2026-06-29). updateAskCard runs every push, so this re-applies on every refresh.
  const distillShown = applyDistillLine(a._distill as HTMLElement, it.column === "completed", it.column === "needs_input",
                   it.summary, it.blockSummary);
  // The distiller line is a LINK: clicking it jumps to where the takeaway/brief was actually written — the
  // biggest contiguous assistant-text block in the goal's work span (it.summaryAnchorUuid; kernel
  // _seg_best_text). This was lost when the line was restored via applyDistillLine (which only sets text), so
  // the summary read like plain text with no affordance (the user 2026-06-29). stopPropagation so it doesn't
  // also open the modal (the card-body click). Falls back to non-clickable when there's no anchor.
  const dl = a._distill as HTMLElement;
  if (distillShown && it.summaryAnchorUuid) {
    dl.classList.add("fask-distill-link");
    dl.title = "jump to where this was written";
    dl.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: it.itemId, sid: it.sid, t: it.t, anchor: "work", anchorUuid: it.summaryAnchorUuid }); };
  } else {
    dl.classList.remove("fask-distill-link");
    dl.onclick = null;
    dl.removeAttribute("title");
  }
  // API error → a red "API error" badge + a Retry button that pastes "retry" into the session to resume
  // the stalled turn (the user 2026-06-16). The card STAYS in Working (the user 2026-06-29) — an API error is
  // a transient stall, not a block — so this badge + Retry are the only API-error cue; no column move.
  a._apiBadge.style.display = isApiErr ? "" : "none";
  a._apiRetry.style.display = isApiErr ? "" : "none";
  if (isApiErr && it.blocked) {
    // "prompt is too long" is on you (compact); other API errors are transient (auto-retrying) — the user 2026-06-29
    a._apiBadge.textContent = it.blocked.tooLong ? "⚠ Prompt too long"
      : it.blocked.status ? `⚠ API error · ${it.blocked.status}` : "⚠ API error";
    a._apiBadge.title = it.blocked.text || it.blocked.what;
    a._apiRetry.disabled = false; a._apiRetry.textContent = "Retry";
    a._apiRetry.onclick = (ev: Event) => {
      ev.stopPropagation();
      vscodeApi?.postMessage({ type: "apiRetry", id: it.sid });
      a._apiRetry.disabled = true; a._apiRetry.textContent = "Retrying…";
    };
  }
  // PARKED HANDOFF → a "Revive" button that brings the offline recipient back so the parked message is
  // delivered; the existing Clear button dismisses it (rides cleared.jsonl). (the user 2026-06-22.)
  const isParked = it.blocked?.state === "parkedHandoff";
  a._revive.style.display = isParked ? "" : "none";
  if (isParked && it.blocked) {
    const toSid = it.blocked.toSid || it.sid;
    a._revive.disabled = false; a._revive.textContent = `Revive ${it.blocked.toName || it.name}`;
    a._revive.onclick = (ev: Event) => {
      ev.stopPropagation();
      vscodeApi?.postMessage({ type: "reviveSession", id: toSid });
      a._revive.disabled = true; a._revive.textContent = "Reviving…";
    };
  }
  // (Follow-up is modal-only now — no card button; the body click opens the modal. the user 2026-06-16.)
  // the user's handoff spec (2026-06-10): every session this ask was handed to,
  // ANYWHERE in its tree (not just the last hop), shown below the main session
  // — bold, identity color, always with the working dot — but ONLY while that
  // session is live-working and its branch is unfinished. Idle or finished →
  // the line disappears. The main session stays on its own row above.
  const ho = a._delegations as HTMLElement;
  ho.innerHTML = "";
  const hseen = new Set<string>();
  for (const n of it.tree || []) {
    if (n.kind !== "handoff" || n.status === "done") continue;       // finished branch → gone
    if (!n.whoSid || n.who === it.name || hseen.has(n.whoSid)) continue;
    if (!workingSet.has(n.who)) continue;                            // idle → gone
    hseen.add(n.whoSid);
    const line = el("div", "fask-delegation-line");
    const nm = el("a", "fask-delegation"); nm.textContent = n.who;
    if (n.whoColor) nm.style.color = n.whoColor.bg;
    nm.title = n.text || "open this session";
    nm.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: n.whoSid }); };
    line.appendChild(nm);
    setWorkDot(nm, true);                  // presence == actively working, dot always on
    ho.appendChild(line);
  }
  ho.style.display = ho.children.length ? "" : "none";

  // Inline sub-goal checklist (the user 2026-06-16): the top-level goal IS the card title, so show its
  // DIRECT sub-goals (the top 2 levels) as a ✓ done / ? question / ▢ open list — the deeper steps stay
  // in the modal. Skips delegation nodes (kind "handoff"; those render in the delegations section above).
  const cl = a._checklist as HTMLElement;
  cl.innerHTML = "";
  const tree = it.tree || [];
  const root = tree.find((n) => n.id === it.itemId) || tree[0];
  // the "Sub goals" toggle gates the inline checklist on CARDS (the modal always shows the full tree)
  const subs = (root && feedPrefs().subgoals)
    ? root.children.map((id) => tree.find((n) => n.id === id)).filter((n): n is AskTreeNode => !!n && n.kind !== "handoff")
    : [];
  for (const s of subs.slice(0, 8)) {
    const row = el("div", "fcheck " + nodeStatusClass(s));
    const mark = el("span", "fcheck-mark");
    // ✓ blue disc (done) / ⏸ red pause (question = blocked) / hollow ○ (not done) — the SAME notation as the
    // ledger checklist + Fleet (the user 2026-06-24: the red ⏸ replaces the amber ? everywhere, for consistency).
    mark.textContent = s.status === "done" ? "✓" : s.status === "question" ? "⏸" : "○";
    const txt = el("span", "fcheck-text"); txt.textContent = s.text;
    row.append(mark, txt);
    // a card sub-goal clicks EXACTLY like the modal tree node (the user 2026-06-17): text → the message,
    // checkbox → where it got checked off — separate links — via the SAME wireNodeZones. No time cell here.
    // (stopPropagation in the handlers keeps a sub-goal click from also opening the card's modal.)
    wireNodeZones(it, s, mark, txt, null, true);
    cl.appendChild(row);
  }
  cl.style.display = cl.children.length ? "" : "none";
}

// Resolve a focus key (set on hoverAskId/pinnedAskId) to the itemId the timeline
// path-preview understands: a raw ask id maps to itself; a group key "g:<turnId>"
// maps to its FIRST (chronological) live member (v1 — siblings share the turn).
// Returns null if the key is empty or the group has no live members left.
function focusAnchorId(key: string | null): string | null {
  if (!key) return null;
  if (key.startsWith("g:")) {
    const tid = key.slice(2);
    // by turnId only — finds the survivor even after a dissolving group's members lose groupTitle
    const members = asks.filter((a) => a.turnId === tid).sort((a, b) => a.t - b.t);
    return members.length ? members[0].itemId : null;
  }
  return key;
}

// Liveness ANOMALY (the user's simplification, 2026-06-11): ring a card only when
// its computed liveness DISAGREES with the column it's filed in — agreement is
// the normal case and carries no information.
//   ASKS + settled    → green ring: nothing can move this without you; it really
//                       belongs in COMPLETED (or AWAITING) — likely a missed DONE.
//   ASKS + stalled    → dashed orange ring: an unfinished handoff nobody is
//                       working — a dropped delegation that will never finish.
//   COMPLETED + active → gold ring: filed done, but the owning session is mid-turn
//                       — it may still be chewing on this.
// Everything else (active/delegated in ASKS, anything in AWAITING) is expected:
// no ring. delegated/stalled are impossible in COMPLETED by construction (an
// unfinished handoff keeps the fold from completing). Tooltips stay on every card.
// Rings (the user's simplification, 2026-06-12-eve): rings exist ONLY in
// COMPLETED, where every card arrived one of two ways — the judge model stamped
// it done, or the auto-filer noticed nothing was moving it. Agreement is the
// expected state and gets NO ink:
//   no ring          = judged done AND settled — both agree; Clear with confidence
//   green            = auto-filed only — the judge never stamped it; verify, then
//                      Clear (right) or Follow up (wrong); either click is a label
//   blue             = judged done only — not settled yet (often still mid-turn
//                      on it, or waiting); glance before clearing
// A member's rolled-up status → the chat-timeline mark vocabulary (● done /
// ? needs the user / ○ not finished), derived from the host's per-ask column.
function memberStatus(m: AskItem): "done" | "question" | "open" {
  const c = askColumn(m);
  return c === "completed" ? "done" : c === "needsInput" ? "question" : "open";
}
function memberMark(m: AskItem): string {
  const s = memberStatus(m);
  return s === "done" ? "●" : s === "question" ? "⏸" : "○";
}

// Fold N sibling asks (shared turnId) into one AskGroup. Column = WORST member
// (any needs-input → needsInput; else any open → asks; else completed). Identity
// (name/color/sid) is the shared asking session; age/tint follow the newest member.
function buildGroup(turnId: string, members: AskItem[]): AskGroup {
  const ms = members.slice().sort((a, b) => a.t - b.t);                       // chronological
  const repr = ms.reduce((x, y) => (y.t > x.t ? y : x), ms[0]);               // most-recent → freshest age/tint
  const column: Column = ms.some((m) => askColumn(m) === "needsInput") ? "needsInput"
    : ms.some((m) => askColumn(m) === "asks") ? "asks" : "completed";
  return {
    turnId, title: ms[0].groupTitle || ms[0].text, members: ms,
    name: ms[0].name, color: ms[0].color, sid: ms[0].sid,
    t: repr.t, trgb: repr.trgb, column, live: ms.some((m) => m.live),
  };
}

// ---- grouped sibling-asks card ----
// One card for a whole typed turn that minted several asks: title = the turn
// summary; one line per member (status circle + member text), the circles filling
// in (○ → ●) as each sub-part completes. Body click → modal (members stacked).
// Clear clears EVERY member. Hover/pin/focus mirror the ask card, keyed by group.
function makeGroupCard(g: AskGroup): HTMLElement {
  const card = el("div", "fitem ask fgroup");
  card.dataset.key = "g:" + g.turnId;
  const fkey = "g:" + g.turnId;

  const main = el("div", "fitem-main");
  const row1 = el("div", "fask-row1");
  const title = el("div", "fcard-title nav"); title.title = "locate this in the text";
  const time = el("span", "ftime");
  row1.append(title);
  const row2 = el("div", "fask-row2");
  const idwrap = el("div", "fask-id");
  const name = el("a", "fname"); name.title = "open this session";
  idwrap.append(name);   // no "· N parts" label — the member checklist below already shows the count
  row2.append(idwrap);
  const actions = el("div", "fask-actions");
  const clr = el("button", "fdismiss"); clr.textContent = "Clear"; clr.title = "clear ALL sub-asks of this request (inbox-zero)";
  actions.append(clr);
  const row3 = el("div", "fask-row3"); row3.append(time, actions);   // time bottom-left · Clear bottom-right
  const memberList = el("div", "fgroup-members");
  main.append(row1, row2, row3, memberList);
  card.append(main);

  const m0 = () => ((card as any)._g as AskGroup | undefined)?.members?.[0];
  title.onclick = (ev) => { ev.stopPropagation(); const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId }); };
  name.onclick = (ev) => { ev.stopPropagation(); const cur = (card as any)._g as AskGroup; if (cur?.sid) openOrReviveSession(cur.sid, cur.live, cur.name); };
  clr.onclick = (ev) => {
    ev.stopPropagation();
    const cur = (card as any)._g as AskGroup;
    card.classList.add("dismissing");
    clearedStack.push(cur.members.slice());   // cache the whole batch for an instant optimistic Undo clear
    for (const m of cur.members) { pendingCleared.add(m.itemId); vscodeApi?.postMessage({ type: "askClear", itemId: m.itemId }); }   // clear every member
    // only finalize if a render in the 180ms window didn't revive (re-render clears
    // .dismissing) or replace this card — else a stale timeout yanks the wrong one
    setTimeout(() => { if (groupEls.get(cur.turnId) === card && card.classList.contains("dismissing")) { card.remove(); groupEls.delete(cur.turnId); } }, 180);
  };
  // hover (120ms intent) → white border + preview the group's timeline journey
  // (first member). leave → restore the pin (ask OR group) or clear.
  let hoverTimer: number | undefined;
  card.addEventListener("mouseenter", () => {
    hoverTimer = window.setTimeout(() => {
      hoverTimer = undefined;
      hoverAskId = fkey; applyFocus();
      const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, locate: false });
    }, 120);
  });
  card.addEventListener("mouseleave", () => {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = undefined; }
    if (hoverAskId === fkey) {
      hoverAskId = null; applyFocus();
      const pin = focusAnchorId(pinnedAskId);
      if (pin) vscodeApi?.postMessage({ type: "showAskPath", itemId: pin, locate: false });
      else { const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, off: true }); }
    }
  });
  // body single-click → modal; double-click → pin/unpin (debounced ~220ms).
  let pending: number | undefined;
  card.addEventListener("click", () => {
    if (pending) { clearTimeout(pending); pending = undefined; return; }
    pending = window.setTimeout(() => {
      pending = undefined;
      fullscreenAskId = fkey;
      const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, locate: false });
      render();
    }, 220);
  });
  card.addEventListener("dblclick", () => {
    if (pending) { clearTimeout(pending); pending = undefined; }
    pinnedAskId = pinnedAskId === fkey ? null : fkey;
    applyFocus();
    const m = m0();
    // double-click = PIN + jump the TIMELINE (same contract as single cards)
    if (pinnedAskId === fkey && m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, locate: false, jump: true });
    else if (!pinnedAskId && hoverAskId !== fkey && m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, off: true });
  });

  const a = card as any;
  a._title = title; a._name = name; a._time = time; a._members = memberList;
  return card;
}

function updateGroupCard(card: HTMLElement, g: AskGroup) {
  const a = card as any;
  a._g = g;                                          // current group for the (reused) handlers
  const fkey = "g:" + g.turnId;
  const eff = hoverAskId ?? pinnedAskId;
  card.className = "fitem ask fgroup" + (g.live ? " live" : " dead")
    + (fkey === eff ? " focused" : "") + (fkey === pinnedAskId ? " pinned" : "");
  const [r, gg, b] = g.trgb;
  card.style.background = `rgba(${r}, ${gg}, ${b}, ${TINT_ALPHA})`;
  card.style.borderColor = `rgba(${r}, ${gg}, ${b}, ${Math.min(TINT_ALPHA + 0.2, 0.9)})`;
  a._title.textContent = g.title;
  a._name.textContent = g.name;
  if (g.color) a._name.style.color = g.color.bg;
  setWorkDot(a._name, workingSet.has(g.name));   // working dot before the session name
  a._time.textContent = relAge(hostNow - g.t);
  // member lines — rebuilt only when the member set or any member's status changes
  const memSig = g.members.map((m) => m.itemId + ":" + memberStatus(m)).join("|");
  if (a._memSig !== memSig) {
    a._memSig = memSig;
    const host = a._members as HTMLElement; host.innerHTML = "";
    for (const m of g.members) {
      const line = el("div", "fgroup-member st-" + memberStatus(m));
      const dot = el("span", "fgroup-dot"); dot.textContent = memberMark(m); line.appendChild(dot);
      const txt = el("span", "fgroup-mtext"); txt.textContent = m.text; line.appendChild(txt);
      host.appendChild(line);
    }
  }
}

// Transient hover-highlight signal: hovering a modal line emits its event id(s);
// the host writes timeline-hover.json {id, ids, nonce} (debounced) and db_timeline
// draws a light transient outline on the matching timeline events. null = clear.
// Hovering a PARENT line sends the union of everything underneath it.
function hoverEmit(ids: string | string[] | null) {
  if (Array.isArray(ids)) vscodeApi?.postMessage({ type: "hoverHighlight", ids });
  else vscodeApi?.postMessage({ type: "hoverHighlight", id: ids });
}

// Node STATE → mark, exactly three (the user's model: completed / needing input /
// not finished), in the chat timeline's visual language: ● filled green = done,
// ○ hollow = not finished, ? = needs the user. Status is ROLLED-UP host-side (a
// node is ● when every path below it ends done; a ○ or ? anywhere below
// propagates up), so a completed ask reads as a column of filled dots. The
// disclosure triangle is the only arrow — no glyph shares its shape.
function nodeMark(n: AskTreeNode): string {
  if (n.status === "done") return "●";
  if (n.status === "question") return "⏸";   // blocked → the red pause (was an amber ?), consistent w/ the ledger
  return "○";
}
function nodeStatusClass(n: AskTreeNode): string {
  if (n.status === "done") return "done";
  if (n.status === "question") return "question";
  return "open";
}
// True if any DESCENDANT of `node` is itself a question. Node status is ROLLED UP
// (a ? anywhere below makes every ancestor ?), so this distinguishes the ACTUAL
// pending question (the LOWEST ? in a branch) from its rolled-up ancestors — only
// the lowest one renders a reply box (the user 2026-06-10). Cycle-safe (DAG).
function hasQuestionDescendant(node: AskTreeNode, byId: Map<string, AskTreeNode>): boolean {
  const stack = [...(node.children || [])];
  const seen = new Set<string>();
  while (stack.length) {
    const cid = stack.pop()!;
    if (seen.has(cid)) continue; seen.add(cid);
    const c = byId.get(cid); if (!c) continue;
    if (c.status === "question") return true;
    for (const gc of c.children || []) stack.push(gc);
  }
  return false;
}

// Re-render trigger: per-node expansion + node states + which questions have briefs.
function treeSig(it: AskItem): string {
  return it.tree.map((n) =>
    n.id + n.status + n.rows.length + (n.whoWorking ? "W" : "") + (collapsedNodes.has(it.itemId + ":" + n.id) ? "c" : "")).join("|")
    + "‖" + it.openQuestions.map((q) => q.reply_id + (q.brief ? "b" : "")).join(",");
}

// Render the DAG as a Linux-style node tree (modal body only). Sig-guarded so a
// host re-push never collapses a node the user just opened or clobbers a
// half-typed answer.
function renderTreeBody(host: HTMLElement, it: AskItem, skipRoot = false) {
  const sig = (skipRoot ? "s|" : "") + treeSig(it);
  if ((host as any)._sig === sig) return;
  (host as any)._sig = sig;
  host.innerHTML = "";
  if (!it.tree.length) { const b = el("div", "fx-body"); b.textContent = "No work yet."; host.appendChild(b); return; }
  const box = el("div", "ftree");
  const byId = new Map(it.tree.map((n) => [n.id, n] as const));
  const briefs = new Map(it.openQuestions.map((q) => [q.reply_id, q] as const));
  const seen = new Set<string>();
  const root = it.tree[0];
  if (skipRoot) {
    // The single-ask modal HEADER already shows the root goal's text (and credits it), so
    // rendering the root's own line here duplicated the title (the user 2026-06-15). Render the
    // root's CHILDREN at depth 0 instead; mark the root seen so a child linking back won't redraw
    // it. build_feed gives every goal node rows:[], so the root has no history rows to preserve.
    seen.add(root.id);
    const kids = (root.children || []).map((c) => byId.get(c)).filter(Boolean) as AskTreeNode[];
    if (!kids.length) { const b = el("div", "fx-body"); b.textContent = "No sub-work yet."; host.appendChild(b); return; }
    for (const k of kids) renderTreeNode(box, it, k, byId, briefs, seen, 0, root.who);
  } else {
    // group modal: each member's tree is stacked with the member's text AS its root line (no
    // per-member header), so the root line stays. pass root.who → the root isn't re-attributed.
    renderTreeNode(box, it, root, byId, briefs, seen, 0, root.who);
  }
  host.appendChild(box);
}

// Hierarchy is shown by INDENTATION alone (no ASCII tree connectors — the
// disclosure triangles + indent levels already carry the structure; the user's
// de-clutter ruling 2026-06-10).
const TREE_INDENT_EM = 1.4;

// Wire a goal node's mark / text / (optional time) into click+hover ZONES — shared by the modal tree AND
// the card's inline sub-goal checklist so they navigate IDENTICALLY (the user 2026-06-17): the TEXT jumps
// to the MESSAGE that minted it (anchor "prompt"); the CHECKBOX (+ the time, when there is one) to where it
// got CHECKED OFF / marked BLOCKED (anchor "work", by id via anchorUuid when resolved). A not-yet-resolved
// node's checkbox + text light together as one unit. `meta` is null on the card (no time cell). `wire`
// false (a dim repeat node) skips the wiring but still returns goWork — the modal's rationale links to it.
function wireNodeZones(it: AskItem, node: AskTreeNode, mark: HTMLElement, txt: HTMLElement, meta: HTMLElement | null, wire: boolean): (ev: Event) => void {
  const navId = node.kind === "handoff" ? node.id : it.turnId;
  const navSid = node.whoSid || (node.kind === "handoff" ? node.id.split(":")[0] : it.sid);
  const resolved = node.status === "done" || node.status === "question";   // done / blocked → has a resolution
  const resolveT = (resolved && node.mt) ? node.mt : node.t;
  const goMsg = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: navId, sid: navSid, t: node.t, anchor: "prompt", anchorUuid: node.promptAnchorUuid ?? null }); };
  const goWork = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: navId, sid: navSid, t: resolveT, anchor: "work", anchorUuid: node.anchorUuid ?? null }); };
  if (!wire) return goWork;
  // tooltip names the destination by status: a blocked node was "marked blocked", a done node "checked off"
  const workTitle = node.status === "question" ? "jump to where this got marked blocked"
                  : resolved ? "jump to where this got checked off" : "jump to this work";
  txt.classList.add("lz-nav"); txt.title = "jump to the message that asked for this"; txt.onclick = goMsg;
  const linkHover = (group: HTMLElement[]) => {
    const on = () => group.forEach((g) => g.classList.add("lz-hl"));
    const off = () => group.forEach((g) => g.classList.remove("lz-hl"));
    group.forEach((g) => { g.addEventListener("mouseenter", on); g.addEventListener("mouseleave", off); });
  };
  if (resolved) {
    // RESOLVED (checked off / blocked): MARK + META jump to where it resolved (goWork), as one pair; TEXT
    // → the minting message, on its own. Hovering the mark or the time lights BOTH (shared target).
    mark.classList.add("lz-nav"); mark.title = workTitle; mark.onclick = goWork;
    if (meta) { meta.classList.add("lz-nav"); meta.title = workTitle; meta.onclick = goWork; }
    linkHover([txt]);
    linkHover(meta ? [mark, meta] : [mark]);
  } else {
    // NOT yet checked off / blocked → no completion: the checkbox + text are ONE block (both the goal
    // itself), navigating to the message together and lighting together. The meta time (when present) stays
    // its own zone → the node's latest work. (the user 2026-06-17.)
    mark.classList.add("lz-nav"); mark.title = "jump to the message that asked for this"; mark.onclick = goMsg;
    if (meta) { meta.classList.add("lz-nav"); meta.title = "jump to the latest work here"; meta.onclick = goWork; }
    linkHover([mark, txt]);   // checkbox + text light together, each keeping its own shape
    if (meta) linkHover([meta]);
  }
  return goWork;
}

function renderTreeNode(box: HTMLElement, it: AskItem, node: AskTreeNode, byId: Map<string, AskTreeNode>, briefs: Map<string, AskQuestion>, seen: Set<string>, depth: number, parentWho: string) {
  const repeat = seen.has(node.id);
  const nodeKey = it.itemId + ":" + node.id;
  const expandable = !repeat && (node.rows.length > 0 || (node.children || []).length > 0);
  const line = el("div", "ftree-node st-" + nodeStatusClass(node) + (repeat ? " repeat" : "") + (depth === 0 ? " ftree-root" : "") + (node.derived ? " derived" : ""));
  // the event this line stands for (handoff → its postal msg id; root → the typed
  // turn) — lets a chat rail-dot hover ring this line back (applyExtHover)
  line.dataset.eid = node.kind === "handoff" ? node.id : it.turnId;
  if (depth) line.style.paddingLeft = (depth * TREE_INDENT_EM) + "em";
  // disclosure triangle: ▶ collapsed / ▼ expanded; non-expandable nodes get a blank spacer (no pointer)
  const tri = el("span", "ftree-tri" + (expandable ? " nav" : " empty"));
  tri.textContent = expandable ? (collapsedNodes.has(nodeKey) ? "▶" : "▼") : "";
  // ONLY the triangle toggles expand/collapse (the user 2026-06-10). stopPropagation
  // so the click doesn't bubble to the line, whose click navigates instead.
  if (expandable) tri.onclick = (ev) => { ev.stopPropagation(); if (collapsedNodes.has(nodeKey)) collapsedNodes.delete(nodeKey); else collapsedNodes.add(nodeKey); render(); };
  line.appendChild(tri);
  const mark = el("span", "ftree-mark"); mark.textContent = nodeMark(node); line.appendChild(mark);
  const txt = el("span", "ftree-text"); txt.textContent = node.text || "(node)"; line.appendChild(txt);
  // (The node's why/blocked/done rationale hover tooltip was removed 2026-06-27 — just the goal text now.)
  if (node.who && node.who !== parentWho) {
    const who = el("a", "ftree-who"); who.title = node.whoWorking ? "open this session (working now)" : "open this session";
    who.appendChild(document.createTextNode("→ "));
    // working dot LEFT of the name (after the arrow): the agent this is handed to is
    // live-working, so the user knows the item is likely to get finished
    if (node.whoWorking) who.appendChild(el("span", "ftree-who-dot"));
    who.appendChild(document.createTextNode(node.who));
    if (node.whoColor) who.style.color = node.whoColor.bg;
    who.onclick = (ev) => { ev.stopPropagation(); if (node.whoSid) vscodeApi?.postMessage({ type: "openSession", id: node.whoSid }); };
    line.appendChild(who);
  }
  const meta = el("span", "ftree-meta");
  // a node needing the user reads as "BLOCKED" (red, all-caps) — the marker + this label are the block
  // signal, distinct from a recency-tinted age (the user 2026-06-17). Other states show "(Xm ago)".
  meta.textContent = node.status === "question" ? "BLOCKED" : "(" + relAge(hostNow - node.last) + ")";
  if (node.status !== "question" && node.trgb) meta.style.color = "rgb(" + node.trgb.join(",") + ")";   // Hawaii recency tint
  line.appendChild(meta);
  // Whole-line click NAVIGATES into the chat. PREFERRED: node.anchorUuid (kernel 996ebd7) deep-links to
  // the EXACT turn by id — where the node resolved (done/blocked) or was minted (open) — killing the
  // nearest-time mismatch. FALLBACK (anchorUuid null/off-path): the time path below. anchor:"work"
  // lands on the ASSISTANT turn, never the user prompt (the user 2026-06-16: "for blocked and
  // completed things jump to places in the chat that are NOT the user's message"). A blocked/done
  // node sends node.mt — the segment where the planner applied the block/done op, i.e. where the work
  // actually got blocked or finished — so the click lands on THAT assistant action, not where the node
  // was first minted. An open node sends node.t (its own start). navSid is the node's session
  // (a handoff node lives in the recipient's transcript).
  // Click/hover zones for this node, via the SHARED wireNodeZones (so the card's sub-goal checklist clicks
  // identically). Returns goWork; the inline rationale below links to the same place. (the user 2026-06-17.)
  const goWork = wireNodeZones(it, node, mark, txt, meta, !repeat);
  // BLOCKED-node surgical actions in the MODAL tree (the user 2026-06-17): resolve or follow up on a
  // SPECIFIC blocked sub-goal, not just the whole card. Wired AROUND the shared wireNodeZones (which the
  // card checklist also uses) so only the modal flips — the card checklist + ledger marks stay pure-nav.
  // Skips repeats (dim back-links) and handoff nodes (those resolve in another session's store).
  if (!repeat && node.status === "question" && node.kind !== "handoff") {
    // The MARK stays pure NAV here (it keeps wireNodeZones → jump to where it got blocked), EXACTLY like the
    // main card — clicking a node in the modal no longer silently crosses it off, which was confusing (the
    // user 2026-06-29). Instead two explicit BUTTONS sit on the line: "Done" crosses it off, "Follow up"
    // answers just this sub-goal.
    const acts = el("span", "ftree-node-acts");
    // "Done": post nodeOverride op:resolve — the kernel marks the node resolved + clears the block + re-rolls
    // inline (no judge pass; bugs owns the handler 3dded52). Immediate-apply (no draft to lose on a re-render).
    const done = el("button", "ftree-act-btn ftree-act-done"); done.textContent = "Done";
    done.title = "mark this done — it stops blocking and the thread's other work continues";
    done.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "nodeOverride", sid: it.sid, nodeId: node.id, op: "resolve" }); };
    // "Follow up": re-target the footer composer at THIS sub so the answer files under it and unblocks just
    // this branch (the judge reopens + force-files under any node id — no kernel change).
    const fu = el("button", "ftree-act-btn ftree-act-fup"); fu.textContent = "Follow up";
    fu.title = "follow up on this specific blocked sub-goal";
    fu.onclick = (ev) => { ev.stopPropagation(); openSubFollowUp?.(node.id, node.text || "(sub-goal)"); };
    acts.append(done, fu);
    line.appendChild(acts);
  }
  // a per-node "↻ Followed up" chip while THIS sub is optimistically reopened by a follow-up, until the judge
  // re-files it (node.followupPending, emitted per-node by build_feed's flatten — judges 047264f).
  if (!repeat && node.followupPending) {
    const chip = el("span", "ftree-followedup"); chip.textContent = "↻ Followed up";
    chip.title = "you followed up on this sub-goal — reopened to working; the planner will re-file it on the next pass";
    line.appendChild(chip);
  }
  // Hovering a node lights ITS OWN work-bars on the timeline — the union of this node's segment trail
  // and everything under it — via the SAME showAskPath the card uses, just scoped to this node (the
  // host resolves the node's subtree segments). Leaving restores the card's full path. (Before: it
  // emitted the goal-node id through hoverHighlight, which the timeline matches against SEGMENT ids,
  // so a sub-node hover never lit anything — the user 2026-06-16.)
  if (!repeat) {
    line.addEventListener("mouseenter", () => vscodeApi?.postMessage({ type: "showAskPath", itemId: node.id, locate: false }));
    line.addEventListener("mouseleave", () => vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, locate: false }));
  }
  box.appendChild(line);
  if (repeat) return;                                   // dim repeat: line only, no descent
  seen.add(node.id);
  // The DISTILLER's per-node line (restored 2026-06-29, the user: show everything the distiller produces, just
  // NOT the planner's why-created/why-blocked/why-done rationales). A done node shows its takeaway
  // (node.summary), a blocked node its decision brief (node.blockSummary) — ONLY when produced (non-empty),
  // never a generating-state placeholder. pre-wrap (CSS) keeps a copy-pasteable artifact intact across lines.
  const nodeDistill = distillText(node.status === "done", node.status === "question",
                                  node.summary, node.blockSummary);
  if (nodeDistill) {
    const sum = el("div", "ftree-summary");
    sum.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
    sum.textContent = nodeDistill;
    // parity with the card's distiller line (the user 2026-06-29): the modal summary is also a LINK — clicking
    // it follows to where the node resolved (its work anchor, the SAME target as the node's mark/time zones).
    if (!repeat && node.anchorUuid) {
      sum.classList.add("ftree-summary-link");
      sum.title = "jump to where this was written";
      sum.onclick = goWork;
    }
    box.appendChild(sum);
  }
  // (the in-feed decision sub-card was removed — a blocked node shows its red BLOCKED marker and
  // links to the session; answering happens in the session, not in the feed. the user 2026-06-15.)
  // node history (rows) — progressive, only when this node was clicked open.
  // the user's ruling: every report that arrived IS a completed sub-thing — it
  // gets a filled green dot. (State still lives on the node line: the dot on a
  // report never holds anything open.) The one exception is the report that IS
  // the currently-open question — that keeps its ?.
  if (!collapsedNodes.has(nodeKey)) {
    // ONE chronological stream per level (the user's ruling): reports and
    // delegations interleave by the time their line DISPLAYS — a report's own
    // time, a delegation's rolled-up last-activity. Each visible sibling list
    // reads oldest → newest; deeper levels re-sort within their own parent
    // (cross-branch disorder when expanded is accepted).
    const entries: { t: number; render: () => void }[] = [];
    for (const r of node.rows) {
      entries.push({ t: r.t, render: () => {
        const row = el("div", "frow nav st-" + r.status + (r.answer ? " st-answer" : ""));
        row.dataset.eid = r.reply_id;   // chat rail-dot hover rings this row back
        row.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM + 1) + "em";
        // ↩ = the user's recorded ANSWER (an explicit child event, not agent work)
        const rm = el("span", "frow-mark"); rm.textContent = r.answer ? "↩" : r.status === "question" ? "⏸" : "●"; row.appendChild(rm);
        const rt = el("span", "flinked-did"); rt.textContent = r.did; row.appendChild(rt);
        const ra = el("span", "ftime"); ra.textContent = relAge(hostNow - r.t);
        if (r.trgb) ra.style.color = "rgb(" + r.trgb.join(",") + ")";   // Hawaii recency tint
        row.appendChild(ra);
        row.title = "jump to this on the timeline";
        row.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: r.reply_id, sid: r.sid || r.reply_id.split(":")[0], t: r.t, anchorUuid: r.reply_id, anchor: "work" }); };   // reply_id IS the assistant reply turn's data-uuid → land BY ID (chat nav is id-only now; without anchorUuid the row honest-failed)
        row.addEventListener("mouseenter", () => hoverEmit(r.reply_id)); row.addEventListener("mouseleave", () => hoverEmit(null));   // transient timeline highlight
        box.appendChild(row);
      } });
    }
    // collapsed node hides its WHOLE subtree — descendants render only when this
    // node is expanded. This is what makes the collapse "deep".
    const kids = (node.children || []).map((c) => byId.get(c)).filter(Boolean) as AskTreeNode[];
    for (const k of kids) {
      entries.push({ t: k.last, render: () => renderTreeNode(box, it, k, byId, briefs, seen, depth + 1, node.who) });
    }
    entries.sort((a, b) => a.t - b.t);
    for (const e of entries) e.render();
  }
}

// auto-grow the follow-up composer like the main message box (capped a few lines)
function growFollowUp(ta: HTMLTextAreaElement) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
}

// ⛶ full-screen tree modal over the whole feed (deep hierarchies, full width).
// Header credits the ask (title · agent · age · Clear); body = the state tree with
// progressive per-node history. Driven by fullscreenAskId; re-rendered each push.
function renderModal() {
  let m = document.getElementById("feed-modal");
  // The open target is EITHER a single ask (itemId) OR a group ("g:<turnId>" key).
  const isGroup = !!fullscreenAskId && fullscreenAskId.startsWith("g:");
  const tid = isGroup ? fullscreenAskId!.slice(2) : "";
  // Collect by turnId only (NOT groupTitle) so a dissolving group is detected even
  // if its survivors lost the flag; a group down to ONE member converts to that
  // survivor's normal single-ask modal rather than closing.
  const gMembers = isGroup ? asks.filter((a) => a.turnId === tid).sort((a, b) => a.t - b.t) : [];
  if (isGroup && gMembers.length === 1) fullscreenAskId = gMembers[0].itemId;
  const grp = isGroup && gMembers.length >= 2 ? buildGroup(tid, gMembers) : null;
  // …OR a standalone deliverable ("i:<itemId>") — same modal chrome, body = the
  // ask/response detail that used to inline under the old [+] button.
  const fitem = (fullscreenAskId && fullscreenAskId.startsWith("i:"))
    ? items.find((i) => i.itemId === fullscreenAskId!.slice(2)) : null;
  // …OR a synthetic blocked-session card ("b:<sid>") — its modal is the ERROR
  // explanation (what's suspicious + what to note for a correction), nothing else.
  const it = (fullscreenAskId && !fullscreenAskId.startsWith("g:") && !fullscreenAskId.startsWith("i:"))
    ? asks.find((a) => a.itemId === fullscreenAskId) : null;
  if (!it && !grp && !fitem) { if (m) { m.remove(); hoverEmit(null); } modalRenderedId = null; return; }   // closed / dissolved → clear hover highlight
  if (!m) {
    m = el("div", ""); m.id = "feed-modal";
    const inner = el("div", "feed-modal-inner");
    const head = el("div", "feed-modal-head");
    const ttl = el("div", "feed-modal-title"); ttl.id = "feed-modal-title";
    const agent = el("a", "fname feed-modal-agent"); agent.id = "feed-modal-agent";
    const close = el("button", "feed-modal-close"); close.textContent = "✕"; close.title = "close (Esc)";
    close.onclick = () => { fullscreenAskId = null; renderModal(); };
    head.append(ttl, agent, close);   // TOP bar: session name (+ a title for group/standalone) at the left, ✕ at the right
    // BOTTOM bar (the user 2026-06-16): the checklist sits at the top; age + Follow up + Clear live below it
    // in one row, and the Follow-up composer drops in under that row when the button is toggled.
    const age = el("span", "ftime feed-modal-age"); age.id = "feed-modal-age";
    const fup = el("button", "fdismiss ffollow feed-modal-follow"); fup.id = "feed-modal-follow"; fup.textContent = "Follow up"; fup.title = "send a follow-up to this session — the card returns to ASKS"; fup.style.display = "none";
    // (Nudge moved OUT of the modal footer onto the working CARD itself — the user 2026-06-18.)
    const clr = el("button", "fdismiss feed-modal-clear"); clr.id = "feed-modal-clear"; clr.textContent = "Clear";
    const footRow = el("div", "feed-modal-foot-row"); footRow.append(age, fup, clr);
    const fubox = el("div", "ffollow-box feed-modal-follow-box"); fubox.id = "feed-modal-follow-box"; fubox.style.display = "none";
    const fuin = el("textarea", "fq-input feed-modal-follow-input") as HTMLTextAreaElement; fuin.id = "feed-modal-follow-input"; fuin.placeholder = "follow up on this…"; fuin.rows = 1;
    fuin.addEventListener("input", () => growFollowUp(fuin));
    const fusend = el("button", "fq-send feed-modal-follow-send"); fusend.id = "feed-modal-follow-send"; fusend.textContent = "Send";
    fubox.append(fuin, fusend);
    // when a blocked sub is the follow-up target, this label says so (click → revert to the whole card)
    const futgt = el("div", "feed-modal-follow-target"); futgt.id = "feed-modal-follow-target"; futgt.style.display = "none";
    const foot = el("div", "feed-modal-foot"); foot.id = "feed-modal-foot"; foot.append(footRow, futgt, fubox);
    const body = el("div", "feed-modal-body"); body.id = "feed-modal-body";
    inner.append(head, body, foot);
    m.appendChild(inner);
    m.onclick = (ev) => { if (ev.target === m) { fullscreenAskId = null; renderModal(); } };  // backdrop closes
    document.body.appendChild(m);
  }
  const body = document.getElementById("feed-modal-body") as HTMLElement;
  // reset the body cache when the open target changes (ask↔group, or a different one)
  // Shared follow-up composer wiring (single-ask AND group modals): the button
  // toggles the box, Enter sends (Shift+Enter = newline), Escape closes. Only
  // the keys we act on are swallowed — everything else (Cmd/Ctrl+V paste, copy,
  // select-all) propagates so VS Code's webview clipboard handler can run.
  function wireFollowUp(fupEl: HTMLButtonElement, fuboxEl: HTMLElement, fuinEl: HTMLTextAreaElement, fusendEl: HTMLButtonElement, send: (txt: string) => void) {
    fupEl.style.display = "";
    // Send/⏎ posts the follow-up and CLOSES the modal — once it's gone through there's nothing left to do
    // here, so drop back to the feed (the user 2026-06-19). (The kernel optimistically reopens the card with
    // a "Followed up" chip, which you then see in the list.)
    const submit = () => { const txt = fuinEl.value.trim(); if (!txt) return; send(txt); fuinEl.value = ""; fuinEl.style.height = ""; fuboxEl.style.display = "none"; fullscreenAskId = null; renderModal(); };
    fupEl.onclick = () => { const show = fuboxEl.style.display === "none"; fuboxEl.style.display = show ? "" : "none"; if (show) fuinEl.focus(); };
    fusendEl.onclick = submit;
    fuinEl.onkeydown = (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); ev.stopPropagation(); submit(); }
      else if (ev.key === "Escape") { ev.stopPropagation(); fuboxEl.style.display = "none"; }
    };
  }

  if (modalRenderedId !== fullscreenAskId) {
    body.innerHTML = ""; (body as any)._sig = "";
    // a fresh target gets a fresh, collapsed follow-up composer
    const fb0 = document.getElementById("feed-modal-follow-box") as HTMLElement | null;
    if (fb0) { fb0.style.display = "none"; const i0 = document.getElementById("feed-modal-follow-input") as HTMLTextAreaElement | null; if (i0) { i0.value = ""; i0.style.height = ""; } }
    followupSub = null;   // a fresh target → the composer follows up on the whole card until a sub is picked
    // fresh open shows ONE level (the user's ruling): the root's reports + its
    // direct children as lines, everything beneath those children folded until
    // clicked. Seeding overrides any unfolds left from a previous open.
    for (const mem of (it ? [it] : grp ? grp.members : [])) {
      const rootId = mem.tree.length ? mem.tree[0].id : null;
      for (const n of mem.tree) {
        const key = mem.itemId + ":" + n.id;
        if (n.id === rootId) collapsedNodes.delete(key);
        else if (n.rows.length || (n.children || []).length) collapsedNodes.add(key);
      }
      // EXCEPTION to one-level (the user): expand the branch DOWN to each pending
      // question so its reply box is reachable without manual unfolding — only that
      // branch's ancestors, never the whole tree.
      const byId = new Map(mem.tree.map((n) => [n.id, n] as const));
      const root = mem.tree[0];
      if (root) {
        const walk = (n: AskTreeNode, ancestors: string[]) => {
          if (n.status === "question" && !hasQuestionDescendant(n, byId))
            for (const aid of ancestors) collapsedNodes.delete(mem.itemId + ":" + aid);   // open the path to the box
          const next = [...ancestors, n.id];
          for (const cid of n.children || []) { if (next.includes(cid)) continue; const c = byId.get(cid); if (c) walk(c, next); }
        };
        walk(root, []);
      }
    }
  }
  modalRenderedId = fullscreenAskId;
  const ttlEl = document.getElementById("feed-modal-title") as HTMLElement;
  const agent = document.getElementById("feed-modal-agent") as HTMLElement;
  const ageEl = document.getElementById("feed-modal-age") as HTMLElement;
  const clrEl = document.getElementById("feed-modal-clear") as HTMLElement;
  const fupEl = document.getElementById("feed-modal-follow") as HTMLButtonElement;
  const fuboxEl = document.getElementById("feed-modal-follow-box") as HTMLElement;
  const fuinEl = document.getElementById("feed-modal-follow-input") as HTMLTextAreaElement;
  const fusendEl = document.getElementById("feed-modal-follow-send") as HTMLButtonElement;
  // Per-sub follow-up re-targeting (the user 2026-06-17): a blocked sub's "↳ follow up" points the footer
  // composer at that sub; the label says which (click it to revert to the whole card); sending reverts too.
  const futgtEl = document.getElementById("feed-modal-follow-target") as HTMLElement;
  const setFollowTarget = (sub: { itemId: string; title: string } | null) => {
    followupSub = sub;
    if (!futgtEl) return;
    if (sub) { futgtEl.textContent = "↳ following up on: " + sub.title; futgtEl.style.display = ""; }
    else { futgtEl.style.display = "none"; }
  };
  if (futgtEl) futgtEl.onclick = () => setFollowTarget(null);   // revert to the whole card
  openSubFollowUp = (itemId, title) => { setFollowTarget({ itemId, title }); fuboxEl.style.display = ""; fuinEl.focus(); };
  // POST a follow-up to the picked sub if one is set, else the card/group fallback; then revert to the card.
  const postFollowUp = (txt: string, fbId: string, fbTitle?: string) => {
    const tgt = followupSub;
    vscodeApi?.postMessage({ type: "askFollowUp", itemId: tgt ? tgt.itemId : fbId, title: tgt ? tgt.title : fbTitle, text: txt });
    setFollowTarget(null);
  };
  setFollowTarget(followupSub);   // sync the label to the current target on every (re)render
  // modal title = a locate link, same as the collapsed card's title (the user
  // 2026-06-10: every title should jump to the thing in the text/timeline)
  ttlEl.classList.add("nav");
  ttlEl.title = "locate this in the text";
  ttlEl.style.display = "";    // default shown (group / standalone); the single-ask branch hides it — its
                               // top-level goal IS the first line of the tree, not a separate header title
  clrEl.style.display = "";   // re-shown here because the blocked branch below hides it
  let titleHoverId: string | null = null;   // the originating typed turn → chat/timeline hover highlight
  if (grp) {
    ttlEl.textContent = grp.title;
    titleHoverId = grp.turnId;
    const gm0 = grp.members[0];   // prompt-intent title → the first member's MINTING message (resolves by id, kernel 92e23ff)
    const gm0Prompt = gm0.tree?.find((n) => n.id === gm0.itemId)?.promptAnchorUuid ?? null;
    ttlEl.onclick = () => vscodeApi?.postMessage({ type: "showOnTimeline", itemId: gm0.itemId, sid: grp.sid, t: grp.t, anchor: "prompt", anchorUuid: gm0Prompt });
    agent.textContent = grp.name; if (grp.color) agent.style.color = grp.color.bg; setWorkDot(agent, workingSet.has(grp.name)); agent.classList.toggle("dead", !grp.live);
    agent.onclick = () => vscodeApi?.postMessage({ type: "openSession", id: grp.sid });
    ageEl.textContent = relAge(hostNow - grp.t);
    ageEl.style.color = "rgb(" + grp.trgb.join(",") + ")";   // tint the age by recency (the time colour scheme)
    clrEl.onclick = () => { for (const mem of grp.members) vscodeApi?.postMessage({ type: "askClear", itemId: mem.itemId }); fullscreenAskId = null; renderModal(); };
    // follow-up on a group goes to the session that took the typed prompt — one
    // message prefixed with the GROUP title, filed under the first member's ask
    wireFollowUp(fupEl, fuboxEl, fuinEl, fusendEl, (txt) => postFollowUp(txt, grp.members[0].itemId, grp.title));
    renderGroupModalBody(body, grp.members);
  } else if (it) {
    // The top-level goal IS the modal: render it as the ROOT of the tree list (not a separate header
    // title), so a goal with no sub-work is just one list line carrying its own done/blocked state, and
    // any sub-goals render beneath it as the rest of the list (the user 2026-06-16). The header above the
    // tree is only the session name + a recency-tinted age; Follow up moved to the footer below the tree.
    ttlEl.style.display = "none";
    titleHoverId = it.turnId;
    agent.textContent = it.name; if (it.color) agent.style.color = it.color.bg; setWorkDot(agent, workingSet.has(it.name)); agent.classList.toggle("dead", !it.live);
    agent.onclick = () => vscodeApi?.postMessage({ type: "openSession", id: it.sid });
    ageEl.textContent = relAge(hostNow - it.t);
    ageEl.style.color = "rgb(" + it.trgb.join(",") + ")";   // tint the age by recency (the time colour scheme)
    clrEl.onclick = () => { vscodeApi?.postMessage({ type: "askClear", itemId: it.itemId }); fullscreenAskId = null; renderModal(); };
    // follow-up works in ANY state (the user 2026-06-10) — asks, awaiting, or completed;
    // toggling the button reveals the composer.
    wireFollowUp(fupEl, fuboxEl, fuinEl, fusendEl, (txt) => postFollowUp(txt, it.itemId));
    renderTreeBody(body, it, false);   // root goal IS the first list line; sub-goals render beneath it
  } else if (fitem) {
    ttlEl.textContent = fitem.did;
    ttlEl.onclick = () => vscodeApi?.postMessage({ type: "showOnTimeline", itemId: fitem.itemId, sid: fitem.sid, t: fitem.t, anchor: "prompt" });
    agent.textContent = fitem.name; if (fitem.color) agent.style.color = fitem.color.bg; setWorkDot(agent, workingSet.has(fitem.name)); agent.classList.toggle("dead", !fitem.live);
    agent.onclick = () => vscodeApi?.postMessage({ type: "openSession", id: fitem.sid });
    ageEl.textContent = relAge(hostNow - fitem.t);
    ageEl.style.color = "rgb(" + fitem.trgb.join(",") + ")";   // tint the age by recency (the time colour scheme)
    clrEl.onclick = () => { vscodeApi?.postMessage({ type: "askClear", itemId: fitem.itemId }); fullscreenAskId = null; renderModal(); };
    fupEl.style.display = "none"; fuboxEl.style.display = "none";   // standalone deliverable: no follow-up
    // fetch the detail once (same machinery the old inline [+] used)
    const d = details.get(fitem.itemId);
    if (!d || d.state === "failed") {
      details.set(fitem.itemId, { state: "loading" });
      vscodeApi?.postMessage({ type: "expand", itemId: fitem.itemId,
        generate: fitem.relevance === "DONE" || fitem.relevance === "DECISION" });
    }
    renderStandaloneTreeInto(body, fitem);
  }
  // The bottom bar always shows (every modal has an age + Clear); the Follow-up button inside it hides
  // itself for standalone deliverables (no follow-up), and the composer stays collapsed until toggled.
  // modal title hover → light the originating message in the chat (+ its timeline
  // glyph), the same join the title CLICK locates to (the user 2026-06-12). Asks
  // and groups carry the typed-turn id; deliverable/blocked modals have no chat
  // message to point at, so they clear. onmouseenter/leave (assignable props, not
  // addEventListener) so each re-render overwrites instead of stacking handlers.
  ttlEl.onmouseenter = titleHoverId ? () => hoverEmit(titleHoverId) : null;
  ttlEl.onmouseleave = titleHoverId ? () => hoverEmit(null) : null;
  // One-click "Follow up" from a blocked/completed feed card (the user 2026-06-22): the card opened THIS
  // modal AND asked to jump straight into the composer — pop it open + focus, exactly like clicking the
  // modal's own Follow up. Runs after the branches set fupEl's visibility, so it no-ops for a standalone
  // deliverable (whose Follow up is hidden). One-shot: the flag is consumed here.
  if (openFollowUpOnRender) {
    openFollowUpOnRender = false;
    if (fupEl && fupEl.style.display !== "none" && fuboxEl && fuinEl) {
      fuboxEl.style.display = ""; growFollowUp(fuinEl); fuinEl.focus();
    }
  }
}


// Standalone completion rendered in the SAME visual language as ask cards
// (the user 2026-06-10: "I would prefer if that particular simple card had a
// consistent formatting to all the other ones"): a one-node tree — root line
// = the prompt that caused it, one green ● report row = the deliverable,
// pending next-steps as hollow ○ rows, and the written paragraph beneath as
// secondary detail instead of the old ASK/RESPONSE block.
function renderStandaloneTreeInto(host: HTMLElement, fitem: FeedItem) {
  const d = details.get(fitem.itemId);
  const det = d && d.data ? d.data : undefined;
  const sig = "st:" + fitem.itemId + ":" + fitem.relevance + ":" + (det ? (det.paragraph || "") + "¦" + (det.next_steps || []).join("¦") : "…");
  if ((host as any)._sig === sig) return;
  (host as any)._sig = sig;
  host.innerHTML = "";
  const box = el("div", "ftree");
  // root = the typed prompt this work answered (● — the work is finished)
  const root = el("div", "ftree-node st-done nav");
  root.appendChild(el("span", "ftree-tri empty"));
  const mark = el("span", "ftree-mark"); mark.textContent = "●"; root.appendChild(mark);
  const txt = el("span", "ftree-text"); txt.textContent = fitem.ask || fitem.did; root.appendChild(txt);
  const meta = el("span", "ftree-meta"); meta.textContent = relAge(hostNow - fitem.t);
  meta.style.color = "rgb(" + fitem.trgb.join(",") + ")";
  root.appendChild(meta);
  root.onclick = () => vscodeApi?.postMessage({ type: "showOnTimeline", itemId: fitem.itemId, sid: fitem.sid, t: fitem.t, anchor: "prompt" });
  box.appendChild(root);
  // the deliverable = one green report row (same vocabulary as ask trees)
  const row = el("div", "frow nav st-done");
  row.style.paddingLeft = (TREE_INDENT_EM + 1) + "em";
  const rm = el("span", "frow-mark"); rm.textContent = "●"; row.appendChild(rm);
  const rt = el("span", "flinked-did"); rt.textContent = fitem.did; row.appendChild(rt);
  const ra = el("span", "ftime"); ra.textContent = relAge(hostNow - fitem.t); row.appendChild(ra);
  row.title = "jump to this on the timeline";
  row.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: fitem.itemId, sid: fitem.sid, t: fitem.t }); };
  box.appendChild(row);
  // what's still pending (next-steps) = hollow circles, like any open path
  if (det && Array.isArray(det.next_steps)) {
    for (const s of det.next_steps) {
      const nr = el("div", "frow st-open");
      nr.style.paddingLeft = (TREE_INDENT_EM + 1) + "em";
      const nm = el("span", "frow-mark"); nm.textContent = "○"; nr.appendChild(nm);
      const nt = el("span", "flinked-did"); nt.textContent = s; nr.appendChild(nt);
      box.appendChild(nr);
    }
  }
  host.appendChild(box);
  // the JLD paragraph stays, dimmer, as secondary detail under the tree
  const par = el("div", "fx-body fstandalone-par");
  par.textContent = det && det.paragraph ? det.paragraph : d && d.state === "loading" ? "…" : "";
  if (par.textContent) host.appendChild(par);
}

// Group modal body: each member's own flat tree stacked, member text as its root
// line, chronological. Sig-guarded (member set + per-member tree sigs) so a host
// repush doesn't tear down an open subtree. Collapse state lives in collapsedNodes
// (keyed by itemId:nodeId), so it survives even a full rebuild here.
function renderGroupModalBody(host: HTMLElement, members: AskItem[]) {
  const sig = members.map((m) => m.itemId + "@" + treeSig(m)).join("‖");
  if ((host as any)._sig === sig) return;
  (host as any)._sig = sig;
  host.innerHTML = "";
  for (const m of members) {
    const sec = el("div", "fgroup-modal-member");
    renderTreeBody(sec, m);   // renders m.tree rooted at m's ask node → member text IS the root line
    host.appendChild(sec);
  }
}

// A column entry is an ask card or a standalone deliverable card; the reconcile
// picks the right builder + cache map by kind.
type Entry =
  | { kind: "ask"; t: number; ask: AskItem }
  | { kind: "group"; t: number; group: AskGroup }
  | { kind: "item"; t: number; item: FeedItem };

// UndoClear (top right): restore the most recently cleared card — the host pops
// the newest cleared.jsonl row. Built fresh wherever the top strip renders: on
// the legend row when columns exist, and on the empty state too (clearing the
// LAST card is exactly when undo is wanted).
function makeUndoClearBtn(): HTMLElement {
  const b = el("button", "fdismiss ffollow");   // restorative → blue hover (.ffollow), not Clear's red
  b.id = "feed-undoclear";
  b.textContent = "Undo clear";
  b.title = "restore the most recently cleared card";
  b.onclick = (ev) => {
    ev.stopPropagation();
    b.classList.add("romp-acted");   // instant press acknowledgment (CLAUDE.md), before any round-trip
    // OPTIMISTIC restore (the user 2026-06-27): re-insert the most-recently-cleared batch RIGHT NOW from the
    // client cache, instead of waiting for the kernel to un-archive + rebuild + re-push the feed (the lag the
    // user felt). The kernel's undoClear reconciles on its next push; pendingCleared is dropped for these ids
    // so that push can't re-suppress them.
    const batch = clearedStack.pop();
    if (batch && batch.length) {
      for (const it of batch) {
        pendingCleared.delete(it.itemId);
        pendingRestored.set(it.itemId, it);                                  // stay sticky until the kernel push carries it
        if (!asks.some((a) => a.itemId === it.itemId)) asks.push(it);        // show it NOW
      }
      render();
    } else {
      pendingCleared.clear();   // nothing cached (e.g. cleared in another session) → fall back to the round-trip
    }
    vscodeApi?.postMessage({ type: "undoClear" });
  };
  return b;
}

// Clear-all + UndoClear live in #feed-foot — a footer bar in normal flow BELOW the scrolling card
// list, so they can never overlap a card (the user 2026-06-15). Appended once; render() toggles
// each one's display. Clear all is appended first (left); UndoClear second (far right).
function ensureUndoClear(): HTMLElement {
  let b = document.getElementById("feed-undoclear");
  if (!b) { b = makeUndoClearBtn(); (document.getElementById("feed-foot") || document.body).appendChild(b); }
  return b;
}

// Clear all: inbox-zero every open card at once. Destructive, so it hovers RED (.fdismiss); the single
// UndoClear restores the whole batch (the host clears them as one cleared.jsonl batch).
function makeClearAllBtn(): HTMLElement {
  const b = el("button", "fdismiss");
  b.id = "feed-clearall";
  b.textContent = "Clear all";
  b.title = "clear every open card (inbox-zero) — UndoClear restores them";
  b.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "clearAll" }); };
  return b;
}

function ensureClearAll(): HTMLElement {
  let b = document.getElementById("feed-clearall");
  if (!b) { b = makeClearAllBtn(); (document.getElementById("feed-foot") || document.body).appendChild(b); }
  return b;
}

// Sub-goals toggle (the user 2026-06-18): moved OUT of the ⛭ gear and INTO the feed footer, beside Clear
// all / Undo clear. Gates each card's inline sub-goal checklist. Writes the SHARED romp:settings.subgoals
// and fires the same 'romp:settings' event the gear does, so flipping it re-gates the cards live (and
// live-syncs to any open gear via the storage event). Sits far-left in the footer pane (prepended).
function makeSubgoalsToggle(): HTMLElement {
  const lab = el("label", "feed-subtoggle");
  lab.style.cssText = "display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:10.5px;opacity:.85;user-select:none";
  lab.title = "show each card's inline sub-goal checklist (the modal always shows the full tree)";
  const cb = el("input") as HTMLInputElement; cb.type = "checkbox"; cb.id = "feed-subgoals-cb"; cb.checked = feedPrefs().subgoals;
  const span = el("span"); span.textContent = "Sub-goals";
  cb.addEventListener("change", (ev) => {
    ev.stopPropagation();
    try {
      const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
      s.subgoals = cb.checked;
      localStorage.setItem("romp:settings", JSON.stringify(s));
      window.dispatchEvent(new Event("romp:settings"));   // same-doc signal → re-gate cards now
    } catch { /* ignore */ }
  });
  lab.append(cb, span);
  return lab;
}
function ensureSubgoalsToggle(): HTMLElement {
  let l = document.getElementById("feed-subgoals");
  if (!l) { l = makeSubgoalsToggle(); l.id = "feed-subgoals"; (document.getElementById("feed-foot") || document.body).prepend(l); }
  const cb = document.getElementById("feed-subgoals-cb") as HTMLInputElement | null;
  if (cb) cb.checked = feedPrefs().subgoals;   // re-sync (the gear or another tab may have changed it)
  return l;
}

// Build the three columns (Asks | Awaiting | Completed) inside #feed-list once;
// rebuild if torn down (empty state). "Awaiting" (the user's ruling 2026-06-10):
// matches the session-chip vocabulary — anything here awaits HIM (a question,
// an action like reload, an idea), red like the awaiting chip.
function ensureCols(list: HTMLElement) {
  if (!document.getElementById("feed-cols")) {
    list.innerHTML = "";
    const cols = el("div", "feed-cols"); cols.id = "feed-cols";
    // "Working" (the user's rename, 2026-06-11): every card is an ASK; the left
    // column holds the ones being worked — internal keys keep the old names
    // each header is a filled state chip reproducing the chat status chips
    // (styles.css .chip): working=yellow, blocked=awaiting-red, completed=ready-blue.
    for (const [key, label, chip] of [["asks", "Working", "working"], ["needsInput", "Blocked", "blocked"], ["completed", "Completed", "completed"]] as const) {
      const col = el("div", "feed-col col-" + key);
      const head = el("div", "feed-col-head");
      const name = el("span", "feed-col-name fcol-chip fcol-chip-" + chip); name.textContent = label;
      const count = el("span", "feed-col-count"); count.id = "col-" + key + "-count";
      head.append(name, count);
      const body = el("div", "feed-col-list"); body.id = "col-" + key + "-list";
      col.append(head, body);
      cols.appendChild(col);
    }
    list.appendChild(cols);
  }
  return {
    asks: document.getElementById("col-asks-list")!,
    needsInput: document.getElementById("col-needsInput-list")!,
    completed: document.getElementById("col-completed-list")!,
    asksCount: document.getElementById("col-asks-count")!,
    needsInputCount: document.getElementById("col-needsInput-count")!,
    completedCount: document.getElementById("col-completed-count")!,
  };
}

// Keyed in-place reconcile of ONE column (mixes ask + standalone cards; a card
// whose column changed is MOVED, not rebuilt — no hover flicker). Records each key
// in `globalDesired` for the cross-column cache cleanup the caller runs after.
function reconcileCol(listEl: HTMLElement, entries: Entry[], globalDesired: Set<string>) {
  const existing = new Map<string, HTMLElement>();
  for (const c of Array.from(listEl.children) as HTMLElement[]) {
    const k = c.dataset.key;
    if (k) existing.set(k, c); else c.remove();
  }
  const ordered: HTMLElement[] = [];
  const colDesired = new Set<string>();
  for (const e of entries) {
    let key: string, card: HTMLElement;
    if (e.kind === "ask") {
      key = "a:" + e.ask.itemId;
      card = askEls.get(e.ask.itemId) || makeAskCard(e.ask);
      askEls.set(e.ask.itemId, card);
      updateAskCard(card, e.ask);
    } else if (e.kind === "group") {
      key = "g:" + e.group.turnId;
      card = groupEls.get(e.group.turnId) || makeGroupCard(e.group);
      groupEls.set(e.group.turnId, card);
      updateGroupCard(card, e.group);
    } else {
      key = "i:" + e.item.itemId;
      card = cardEls.get(e.item.itemId) || makeCard(e.item);
      cardEls.set(e.item.itemId, card);
      updateCard(card, e.item);
    }
    globalDesired.add(key); colDesired.add(key);
    ordered.push(card);
  }
  for (const [k, c] of existing) if (!colDesired.has(k)) c.remove();
  let cur: ChildNode | null = listEl.firstChild;
  for (const node of ordered) {
    if (cur === node) { cur = cur.nextSibling; continue; }
    listEl.insertBefore(node, cur);
  }
  // an empty column shows NOTHING (the user 2026-06-25) — no "—" placeholder. (Any stray non-keyed child,
  // including an old placeholder, is already removed at the top of this reconcile.)
}

// ── FLIP: animate a card FLYING to its new column when its status changes (the user 2026-06-27) ──
// Cards are reused DOM nodes that reconcileCol MOVES between columns, so a status change relocates the same
// element — perfect for FLIP (First-Last-Invert-Play): record each card's screen rect + column BEFORE the
// move, then after it, offset the card back to where it was and transition that offset to zero so it glides to
// its new home. The flying card sits in the BACK layer (position:relative; z-index:-1 → behind sibling cards
// but above the column background) so it never flies OVER other content. Respects prefers-reduced-motion.
type FlipState = { rect: DOMRect; col: string };
const FLY_COLS: ("asks" | "needsInput" | "completed")[] = ["asks", "needsInput", "completed"];
function captureCardRects(cols: ReturnType<typeof ensureCols>): Map<string, FlipState> {
  const m = new Map<string, FlipState>();
  for (const key of FLY_COLS) {
    const colEl = cols[key];
    for (const c of Array.from(colEl.children) as HTMLElement[]) {
      if (c.dataset.key) m.set(c.dataset.key, { rect: c.getBoundingClientRect(), col: colEl.id });
    }
  }
  return m;
}
function flyColumnChanges(first: Map<string, FlipState>, cols: ReturnType<typeof ensureCols>): void {
  if (!first.size) return;
  try { if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return; } catch { /* no matchMedia */ }
  for (const key of FLY_COLS) {
    const colEl = cols[key];
    for (const c of Array.from(colEl.children) as HTMLElement[]) {
      const k = c.dataset.key; if (!k) continue;
      const prev = first.get(k);
      if (!prev) continue;                                 // brand-new card → no FLIP (nothing to glide from)
      const now = c.getBoundingClientRect();
      const dx = prev.rect.left - now.left, dy = prev.rect.top - now.top;
      if (!dx && !dy) continue;                            // didn't move → leave it alone
      // Two flavors of move, ONE FLIP (the user 2026-06-29): a card that CHANGED COLUMN flies in the BACK
      // layer (z-index:-1 → behind the other cards, so it never sails over them); a card that STAYED in its
      // column but shifted — because the card that left it vacated a slot — glides IN PLACE in normal flow, so
      // the remaining cards reflow smoothly to their new spots instead of snapping there in a discrete jump.
      const crossed = prev.col !== colEl.id;
      if (crossed) c.classList.add("fitem-flying");
      // Invert: jump the card back to its old spot, instantly.
      c.style.transition = "none";
      c.style.transform = `translate(${dx}px, ${dy}px)`;
      // Play: next frame, release the offset with a transition → it glides to its new home.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        c.style.transition = "transform .42s cubic-bezier(.22, .61, .36, 1)";
        c.style.transform = "translate(0, 0)";
      }));
      const done = (ev: TransitionEvent) => {
        if (ev.propertyName !== "transform") return;
        c.removeEventListener("transitionend", done);
        if (crossed) c.classList.remove("fitem-flying");
        c.style.transition = ""; c.style.transform = "";   // back to normal flow + stacking
      };
      c.addEventListener("transitionend", done);
    }
  }
}

// ── Absorb: when a top-level ask card becomes a SUB-GOAL of another card, it shrinks + flies INTO the parent
// instead of just vanishing (the user 2026-06-29). Detected at reconcile time: a card that's leaving the board
// AND whose itemId now appears as a node inside some still-visible ask's tree. We detach the leaving node to a
// fixed overlay at its old spot, then transition it scaling down + translating to the parent card's center.
function absorbIntoParent(card: HTMLElement, fromRect: DOMRect, parent: HTMLElement): void {
  const to = parent.getBoundingClientRect();
  if (!to.width || !fromRect.width) { card.remove(); return; }   // parent off-screen → just drop it
  card.remove();                                                 // out of column flow first
  card.classList.add("fitem-absorbing");
  Object.assign(card.style, {
    position: "fixed", left: `${fromRect.left}px`, top: `${fromRect.top}px`,
    width: `${fromRect.width}px`, height: `${fromRect.height}px`, margin: "0",
  });
  document.body.appendChild(card);
  const dx = (to.left + to.width / 2) - (fromRect.left + fromRect.width / 2);
  const dy = (to.top + to.height / 2) - (fromRect.top + fromRect.height / 2);
  let gone = false;
  const done = () => { if (gone) return; gone = true; card.removeEventListener("transitionend", done); card.remove(); };
  requestAnimationFrame(() => requestAnimationFrame(() => {
    card.style.transition = "transform .4s cubic-bezier(.4, 0, .2, 1), opacity .4s ease";
    card.style.transformOrigin = "center center";
    card.style.transform = `translate(${dx}px, ${dy}px) scale(0.14)`;
    card.style.opacity = "0";
  }));
  card.addEventListener("transitionend", done);
  setTimeout(done, 650);                                         // backstop if transitionend never fires
}

// THE view: one screen, three columns merging open asks with standalone
// completions; cards move between columns as links arrive.
function render() {
  const list = document.getElementById("feed-list")!;
  const prevScroll = list.scrollTop;
  const standalone = standaloneItems();
  // footer pane (below the cards, no overlap): Sub-goals toggle (left) · Clear all · UndoClear (right)
  const showCA = !!(asks.length || standalone.length);
  ensureSubgoalsToggle();   // the toggle lives in the footer now; visible whenever the footer is
  ensureClearAll().style.display = showCA ? "" : "none";
  ensureUndoClear().style.display = canUndoClear ? "" : "none";
  const foot = document.getElementById("feed-foot");
  // show the footer whenever there are cards (so the Sub-goals toggle is reachable) or an undo is available
  if (foot) foot.style.display = (showCA || canUndoClear) ? "" : "none";

  if (!asks.length && !standalone.length) {
    askEls.clear(); groupEls.clear(); cardEls.clear();
    // inbox zero → the romp wordmark (a CSS background). role/aria-label + title keep the meaning for hover /
    // screen readers, since a background image carries no accessible text. Created ONCE (idempotent): on the
    // transition from cards→empty we mint it (its CSS fade-in plays once, the user 2026-06-25), and every
    // subsequent empty push leaves it in place so the logo doesn't re-fade on the 0.5s cadence.
    if (!list.querySelector(".feed-empty")) {
      list.innerHTML = "";
      const e = el("div", "feed-empty"); e.title = "All tasks complete";
      e.setAttribute("role", "img"); e.setAttribute("aria-label", "All tasks complete");
      list.appendChild(e);
    }
    return;
  }

  const cols = ensureCols(list);
  const buckets: Record<Column, Entry[]> = { asks: [], needsInput: [], completed: [] };
  // Derive sibling GROUPS at render time, keyed by the shared typed turn (turnId).
  // Only host-flagged asks (groupTitle) participate, and a turn needs ≥2 current
  // members to fold — a lone survivor (siblings cleared) renders as a single card.
  const byTurn = new Map<string, AskItem[]>();
  for (const a of asks) {
    if (!a.groupTitle || !a.turnId) continue;
    const arr = byTurn.get(a.turnId) || []; arr.push(a); byTurn.set(a.turnId, arr);
  }
  const grouped = new Set<string>();   // itemIds folded into a group → excluded from single ask cards
  for (const [tid, members] of byTurn) {
    if (members.length < 2) continue;
    members.forEach((m) => grouped.add(m.itemId));
    const g = buildGroup(tid, members);
    buckets[g.column].push({ kind: "group", t: g.t, group: g });
  }
  for (const a of asks) { if (grouped.has(a.itemId)) continue; buckets[askColumn(a)].push({ kind: "ask", t: a.t, ask: a }); }
  for (const it of standalone) buckets[it.relevance === "DONE" ? "completed" : "needsInput"].push({ kind: "item", t: it.t, item: it });
  // ALWAYS oldest-at-top (the user 2026-06-27): the newest work sits at the BOTTOM of each column, nearest the
  // eye, and new/moved cards stack onto the bottom (matches the fly animation). No toggle — this is the behavior.
  for (const k of Object.keys(buckets) as Column[]) buckets[k].sort((x, y) => x.t - y.t);

  // FLIP-across-identity bookkeeping (the user 2026-06-29): map every goal itemId this render → the card KEY
  // that renders it, so the next render can slide a card whose IDENTITY changed (group↔solo, umbrella absorb)
  // from its predecessor's old spot. An ask/group "covers" its own id AND its tree node ids, so an umbrella
  // card covers the goals it just absorbed (their solo cards were the predecessors).
  const curItemKey = new Map<string, string>();
  const coverInto = (key: string, ids: string[]) => { for (const id of ids) if (!curItemKey.has(id)) curItemKey.set(id, key); };
  for (const k of Object.keys(buckets) as Column[]) for (const e of buckets[k]) {
    if (e.kind === "ask") coverInto("a:" + e.ask.itemId, [e.ask.itemId, ...(e.ask.tree || []).map((n) => n.id)]);
    else if (e.kind === "group") coverInto("g:" + e.group.turnId, e.group.members.flatMap((m) => [m.itemId, ...(m.tree || []).map((n) => n.id)]));
    else coverInto("i:" + e.item.itemId, [e.item.itemId]);
  }

  // FLIP step 1 (the user 2026-06-27): record every visible card's position + column BEFORE the reconcile, so
  // a card that changes column can FLY from its old spot to the new one instead of teleporting.
  const flipFirst = captureCardRects(cols);

  const desired = new Set<string>();
  reconcileCol(cols.asks, buckets.asks, desired);
  reconcileCol(cols.needsInput, buckets.needsInput, desired);
  reconcileCol(cols.completed, buckets.completed, desired);
  // the count chip shows the number only when there ARE cards; an empty column shows nothing — not "0"
  // (the user 2026-06-25). Empty string collapses the chip (it has no padding/background of its own).
  const setCount = (elc: HTMLElement, n: number) => { elc.textContent = n ? String(n) : ""; elc.style.display = n ? "" : "none"; };
  setCount(cols.asksCount, buckets.asks.length);
  setCount(cols.needsInputCount, buckets.needsInput.length);
  setCount(cols.completedCount, buckets.completed.length);

  // Remove cards no longer in the payload — EXCEPT one mid-dismiss (.dismissing): let its own 180ms timer
  // finish the collapse animation instead of yanking it instantly on a push (the user 2026-06-19).
  const undismissed = (el?: HTMLElement) => !!el && !el.classList.contains("dismissing");
  // A card that's leaving because it became a SUB-GOAL of another card absorbs INTO that parent (the user
  // 2026-06-29). Map each visible ask's NON-root tree-node ids → that ask's card, so a leaving id can find its
  // new home. (Falls back to an instant remove if there's no parent or motion is reduced.)
  let reduceMotion = false;
  try { reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { /* no matchMedia */ }
  const subgoalParent = new Map<string, HTMLElement>();
  if (!reduceMotion) for (const a of asks) {
    const pcard = askEls.get(a.itemId); if (!pcard) continue;
    for (const node of a.tree) { if (node.id !== a.itemId && !subgoalParent.has(node.id)) subgoalParent.set(node.id, pcard); }
  }
  for (const id of Array.from(askEls.keys())) {
    if (desired.has("a:" + id) || !undismissed(askEls.get(id))) continue;
    const leaving = askEls.get(id)!;
    const parent = subgoalParent.get(id), first = flipFirst.get("a:" + id);
    if (parent && first && parent !== leaving) absorbIntoParent(leaving, first.rect, parent);
    else leaving.remove();
    askEls.delete(id);
  }
  for (const tid of Array.from(groupEls.keys())) if (!desired.has("g:" + tid) && undismissed(groupEls.get(tid))) { groupEls.get(tid)?.remove(); groupEls.delete(tid); }
  for (const id of Array.from(cardEls.keys())) if (!desired.has("i:" + id) && undismissed(cardEls.get(id))) { cardEls.get(id)?.remove(); cardEls.delete(id); }

  list.scrollTop = prevScroll;
  // FLIP-across-identity: a card whose KEY is new this render (group→solo, solo→group, umbrella absorb) has no
  // First rect of its own, so the normal FLIP can't slide it. Alias it to its PREDECESSOR's rect — the card
  // key that covered one of its goals LAST render — so it glides in from where that predecessor sat instead of
  // popping (the user 2026-06-29). First predecessor found wins; never overwrite a card's own real First rect.
  if (!reduceMotion) for (const [itemId, curKey] of curItemKey) {
    if (flipFirst.has(curKey)) continue;                 // this card has its own First rect → normal FLIP path
    const prevKey = prevItemKey.get(itemId);
    if (prevKey && prevKey !== curKey && flipFirst.has(prevKey)) flipFirst.set(curKey, flipFirst.get(prevKey)!);
  }
  // FLIP step 2: any card whose column changed flies from its recorded spot to the new one (in the back layer).
  flyColumnChanges(flipFirst, cols);
  prevItemKey = curItemKey;   // remember this render's identity map for the next FLIP-across-identity
  renderModal();   // keep the ⛶ full-screen tree (if open) in sync with this push
  applyExtHover(); // reconcile/renderModal may have rebuilt nodes — re-apply the rail-dot outlines (cards AND modal rows)
}

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && fullscreenAskId) { fullscreenAskId = null; renderModal(); }
});

// Focus policy (the user 2026-06-13): the feed is MOUSE-driven and almost never
// needs the keyboard — but clicking a card stole focus from the CHAT iframe,
// killing the chat's keyboard nav until you clicked back into it. So after any
// click in the feed, hand focus straight BACK to the chat — UNLESS the feed
// genuinely wants keys right now: a modal/help overlay is open (Esc closes it, its
// follow-up/report fields type) or the click landed in a text field. The check
// runs AFTER the click's own handler (deferred a tick), so a card click that just
// OPENED a modal keeps focus, and clicking ✕/backdrop to CLOSE one returns focus
// to chat. Same-origin combined page only — a no-op on the standalone /feed page
// or inside VS Code, where there's no sibling chat-frame to reach.
function feedWantsKeys(t: EventTarget | null): boolean {
  if (document.getElementById("feed-modal")) return true;
  const el = t as HTMLElement | null;
  return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
}
function returnFocusToChat(): void {
  try {
    if (!window.parent || window.parent === window) return;
    const chat = window.parent.document.getElementById("chat-frame") as HTMLIFrameElement | null;
    chat?.contentWindow?.focus();   // restores the chat document's last-focused element (tab/composer)
  } catch { /* cross-origin / not embedded → leave focus where it is */ }
}
window.addEventListener("click", (e) => {
  const t = e.target;
  setTimeout(() => { if (!feedWantsKeys(t)) returnFocusToChat(); }, 0);
});

// Re-render when the card-display prefs change: a 'storage' event fires for a change made in ANOTHER
// same-origin pane/tab, and the ⛭ gear (same document) dispatches a "romp:settings" event after it writes
// (a same-doc write fires no storage event). Either way the cards re-gate to the new Explanations/Sub-goals.
window.addEventListener("storage", (e) => { if (e.key === "romp:settings") render(); });
window.addEventListener("romp:settings", () => render());

window.addEventListener("message", (e: MessageEvent) => {
  const m = e.data;
  if (!m) return;
  if (m.type === "feed") {
    items = Array.isArray(m.items) ? m.items : [];
    const incomingAsks: AskItem[] = Array.isArray(m.asks) ? m.asks : [];
    // A clear is CONFIRMED once the kernel's payload no longer lists it → stop suppressing it. Then drop
    // any still-pending (kernel hasn't caught up) from this payload so a stale push can't resurrect them.
    for (const id of Array.from(pendingCleared)) if (!incomingAsks.some((a) => a.itemId === id)) pendingCleared.delete(id);
    asks = pendingCleared.size ? incomingAsks.filter((a) => !pendingCleared.has(a.itemId)) : incomingAsks;
    // An optimistic Undo clear is CONFIRMED once the kernel's payload carries the id again → stop forcing it.
    // Until then, keep the cached card in `asks` so the replace above can't drop the just-restored card (flicker).
    if (pendingRestored.size) {
      for (const id of Array.from(pendingRestored.keys())) if (incomingAsks.some((a) => a.itemId === id)) pendingRestored.delete(id);
      const present = new Set(asks.map((a) => a.itemId));
      for (const it of pendingRestored.values()) if (!present.has(it.itemId)) asks.push(it);
    }
    workingSet = new Set(Array.isArray(m.working) ? m.working : []);
    hostNow = typeof m.now === "number" ? m.now : Math.floor(Date.now() / 1000);
    if (typeof m.dismissedCount === "number") dismissedCount = m.dismissedCount;
    if (typeof m.showDismissed === "boolean") showDismissed = m.showDismissed;
    if (typeof m.canUndoClear === "boolean") canUndoClear = m.canUndoClear;
    render();
  } else if (m.type === "detail" && m.itemId) {
    details.set(m.itemId, { state: "ready", data: m.detail });
    if (expanded.has(m.itemId) || fullscreenAskId === "i:" + m.itemId) render();
  } else if (m.type === "detailPending" && m.itemId) {
    if (details.get(m.itemId)?.state !== "ready") details.set(m.itemId, { state: "loading" });
    if (expanded.has(m.itemId) || fullscreenAskId === "i:" + m.itemId) render();
  } else if (m.type === "detailFailed" && m.itemId) {
    details.set(m.itemId, { state: "failed", reason: m.reason });
    if (expanded.has(m.itemId) || fullscreenAskId === "i:" + m.itemId) render();
  } else if (m.type === "hoverCards") {
    // rail-dot hover in the CHAT panel → white-outline the card(s) built from
    // that turn, plus the matching ROWS inside an open modal (eid). The host
    // fans the same hover out to the timeline.
    extHoverKeys = new Set(Array.isArray(m.keys) ? m.keys.map(String) : []);
    extHoverEid = typeof m.eid === "string" && m.eid ? m.eid : null;
    applyExtHover();
  } else if (m.type === "openCard" && typeof m.key === "string") {
    // rail-dot click → open this card's modal (key is fullscreenAskId-shaped:
    // ask itemId, "i:<itemId>" standalone, "g:<turnId>" group). hl = the clicked
    // turn's event id: ring its row(s) and scroll the first one into view.
    fullscreenAskId = m.key;
    if (typeof m.hl === "string" && m.hl) extHoverEid = m.hl;
    renderModal();
    applyExtHover();
    if (extHoverEid) document.querySelector(".dot-hl[data-eid]")?.scrollIntoView({ block: "center" });
  } else if (m.type === "pickerOptions" && typeof m.name === "string") {
    // the host read the blocked session's live resume-picker screen — show the
    // same options in-page; a choice goes back as keystrokes (transport only,
    // the user decides — the never-auto-answer rule holds).
    showPickerDialog(String(m.name), Array.isArray(m.options) ? m.options.map(String) : []);
  }
});

// ---- in-page resume-picker dialog (the answerPicker flow, no native QuickPick) ----
function showPickerDialog(name: string, options: string[]) {
  document.getElementById("picker-dialog")?.remove();
  if (!options.length) return;   // host already toasted "no longer on screen"
  const overlay = el("div", "pickdlg-overlay"); overlay.id = "picker-dialog";
  const box = el("div", "pickdlg-box");
  const title = el("div", "pickdlg-title");
  title.textContent = `${name} is waiting on the resume picker — choose to answer it`;
  box.append(title);
  options.forEach((opt, i) => {
    const btn = el("button", "pickdlg-opt");
    btn.textContent = opt;
    btn.onclick = () => {
      vscodeApi?.postMessage({ type: "answerPickerChoice", name, n: i });
      overlay.remove();
    };
    box.append(btn);
  });
  const cancel = el("button", "pickdlg-opt pickdlg-cancel");
  cancel.textContent = "Cancel";
  cancel.onclick = () => overlay.remove();
  box.append(cancel);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
  (box.querySelector("button") as HTMLElement | null)?.focus();
}

// White outline for cards named by a chat rail-dot hover — and, when a modal is
// open, for the modal rows/node-lines whose event matches (data-eid, set in
// renderTreeNode). Re-applied after each render() since reconcile/renderModal
// may rebuild nodes mid-hover.
let extHoverKeys = new Set<string>();
let extHoverEid: string | null = null;
function applyExtHover() {
  document.querySelectorAll<HTMLElement>("[data-key]").forEach((c) => {
    c.classList.toggle("dot-hl", extHoverKeys.has(c.dataset.key || ""));
  });
  document.querySelectorAll<HTMLElement>("[data-eid]").forEach((c) => {
    c.classList.toggle("dot-hl", !!extHoverEid && c.dataset.eid === extHoverEid);
  });
}

// Keep "Xm ago" honest between host pushes (host reposts ~1×/min for color fade).
setInterval(() => {
  const now = Math.floor(Date.now() / 1000);
  for (const [id, card] of cardEls) {
    const it = items.find((i) => i.itemId === id);
    const t = (card as any)._time as HTMLElement | undefined;
    if (it && t) t.textContent = relAge(now - it.t);
  }
  for (const [id, card] of askEls) {
    const it = asks.find((a) => a.itemId === id);
    const t = (card as any)._time as HTMLElement | undefined;
    if (it && t) t.textContent = relAge(now - it.t);
  }
}, 15000);

vscodeApi?.postMessage({ type: "ready" });
