// romp feed — a stream of rounded "deliverable cards" on a backdrop. Each card is
// ONE deliverable (a turn's "did" phrase) from some session, newest on top. The
// session name links to that session's tab; the checkbox dismisses the card; the
// message expands to a (pre-generated) action paragraph.
//
// Rendering is KEYED + INCREMENTAL: cards are kept alive across the host's live
// pushes and updated in place — never torn down — so hovering one doesn't flicker
// when the fleet streams new deliverables in.
import { distillText, distillInputs, applyDistillLine, distillPending, distillStaleNote } from "./distiller-line";
import { flipNeeded } from "./feed-flip";
import { spinFor, KIND_WORD, kindWord, waitedSuffix } from "./spin-caption";
import { onlyTag, matchesOnly } from "./only-filter";
import { searchMatches, searchSids } from "./feed-search";
import { TagLens, lensAll, lensLabel, lensVisible, lensUnions } from "./tag-lens";
import { openTagMenu, tagMenuButton, syncTagFilter } from "./tag-menu";
import { SessionViews } from "./session-views";
import { freezeDiff, contentSig } from "./feed-freeze";
import { hostNameNodes, hostPartsNodes, hostIsDown, hostDownNote, hostOf } from "./host-prefix";
import { extHoverMatches } from "./card-key";
import { provenanceRows, provenanceGroupRows, rootStart, type ProvFmt, type ProvRow } from "./provenance";
import { ageColorReadable } from "./age-color";
import { badgeNotices, clearBoundaryNotices, sdkProblemNotices, syncNotices,
  type ClearNoticeRow, type SdkNoticeRow, type SyncNoticeRow } from "./badge-mirror";
import { initStrip } from "./strip";
import { installSettingsSync, loadSettings, onExternalSettingsChange } from "./settings";
import { applyTheme } from "./theme";
import { canPreview } from "./preview";
import { initFileView } from "./file-view";
import { initFileBrowse, openFileBrowse } from "./file-browse";
import { VIEW_STATE_KEY, parseViewState, serializeViewState, pruneViewState, capViewState, type FeedViewState } from "./feed-view-state";
import { wireTip, setTip, pruneTip } from "./tip";

// (The standalone-deliverable "FeedItem" subsystem was REMOVED 2026-07-07: the kernel had emitted
// items: [] permanently — goal cards are the only feed unit — so its types, renderers, expand/detail
// machinery, and CSS were all unreachable. Payload-contract audit.)

// Asks inbox (the DEFAULT view; request registry REQUESTS.md): one persistent
// card per OPEN ask of the user's, sorted into THREE state columns derived from each
// ask's newest link. Clear (the user-only, binary) removes it — inbox-zero. The
// deliverables stream is the safety-net behind the header toggle.
interface DecisionBrief { context: string; question: string; options: string[] | null; sid: string; t: number }
interface AskQuestion { reply_id: string; sid: string; name: string; t: number; brief: DecisionBrief | null; qtype?: "decision" | "action" | "idea"; nodeId?: string }
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
  derived?: boolean;                                             // done by roll-up/roll-down (kernel), not explicit → DIMMED ✓ disc
  qderived?: boolean;                                            // "question" by roll-UP (the block lives in a descendant) → tooltip says so; the actual ask carries its own ⏸ below (kernel flatten, the user 2026-07-11)
  auth?: "open" | "done";                                        // AUTHORITATIVE tier: mirrors an item on the agent's OWN to-do list → solidity=authority disc (open = bold accent ring; done = heaviest check). Absent = plain judge-inferred node.
  followupPending?: boolean;                                     // this sub was optimistically reopened by a per-sub follow-up → "↻ Followed up" chip (kernel flatten, judges 047264f)
  summary?: string | null;                                       // the DISTILLER's key takeaway for a completed goal (artifact or 1-3 sentences) → the modal's auto-line for a DONE node (kernel flatten 78fc97b)
  blockSummary?: string | null;                                  // the BLOCK-distiller's decision brief for a blocked goal → the modal's auto-line for a BLOCKED node (kernel 466393c); null until produced
  trgb?: [number, number, number];                               // last-activity recency tint (timestamp)
  cleared?: boolean;                                             // user-cleared sub (nodeOverride op:clear) → struck-through faded row + "cleared" chip; the mark stays tied to status (box = done, the user 2026-07-26)
  reviewedEarlier?: boolean;                                     // this done sub predates the top's review boundary (kernel flatten ↔ jd.review_boundary, the distiller's own scoping) → collapsed behind one "N reviewed earlier" row (the user 2026-08-19)
  parked?: { n: number } | null;                                 // LEAPFROGGED open row (kernel _parked_rows, the user 2026-08-24): nothing filed under it while n younger siblings were dispatched past it → quiet "parked" tag + the card's dim sub-goals suffix; retires on its own delegation edge or any verdict
  log?: NodeLogRow[] | null;                                     // the node's newest verdict rows (kernel _node_log_rows, non-done only) → the modal's per-item story (the user 2026-07-20)
  children: string[];
}
// One verdict-log row (kernel _node_log_rows): who did what to this node, when, and — when the
// parse resolves it — the exact chat turn to jump to (evT time-nav fallback otherwise).
interface NodeLogRow {
  kind: string; src: string; why?: string | null;
  at?: number | null; evT?: number | null; anchorUuid?: string | null;
}
interface AskItem {
  itemId: string; sid: string; name: string; color: { bg: string; fg: string } | null;
  text: string; t: number; live: boolean;
  turnId: string;
  trgb: [number, number, number];
  column: "working" | "needs_input" | "completed";   // RAW kernel value (build_feed): working/needs_input/completed. askColumn() maps it to the local Column. NOT "asks" — that was a stale lie that silently broke `it.column === "asks"` checks.
  followupPending?: boolean;                       // you followed up on a settled card → optimistically reopened, awaiting the judge's re-file (kernel)
  followupAt?: number | null;                      // when that follow-up/continue went — the latched button's honest age (T150)
  doneConfirming?: boolean;                        // the done verdict is in, only the settle event is pending → steady "done, confirming" chip on the Working card; placement deliberately does NOT move early (no working↔done flicker) (kernel build_feed ← judge rollup confirming export; the user 2026-07-24)
  recheck?: boolean;                               // soft-block you answered with a TARGETED follow-up → de-urgented (dotted), moved to Working, dropped from the "need input" count, until the judge resolves or re-blocks it (kernel build_feed; the user 2026-06-27)
  rejudging?: boolean;                             // soft-block + a PLAIN thread reply after it → moves to WORKING while the reply is in flight (echo/open turn), with a "Re-judging…" swirl; returns to Needs-You on its own if the judge leaves it blocked (kernel build_feed; the user 2026-07-02, immediate)
  nudgeFailed?: boolean;                           // the ONE auto-nudge on this stalled goal didn't resolve it → red "follow-up failed" chip (renamed off "stalled" 2026-07-23 — that word is the yellow romp-holding section's now); the failure also records a BLOCK verdict, so the card reaches Needs-you via the normal ladder (kernel _mark_nudge_failed, 2026-07-07)
  stalled?: { why: string; since: number; note?: string | null; blocked?: boolean } | null;
  working?: { since?: number | null; toolUses?: number | null } | null;   // open-turn narration: tool count + start (kernel _open_turn_progress; the user 2026-08-13)   // romp's nudge gate is HOLDING this working card behind a reviver that isn't retiring, so nothing is moving it (kernel _stalled_goals; the user 2026-07-23). `why` = the kernel's mechanical reason, always present; `note` = the staller's plain-language version, null until the judge writes one. Drives the Stalled section button — its own colour, since this is romp being the bottleneck, not you
  interrupting?: boolean;                          // a user interrupt is IN FLIGHT (dispatched, not yet settled) → steady "interrupting…" badge from the click until it settles, THEN yields to `interrupted` — never flickering to "working" in between (kernel build_feed; the user 2026-07-07)
  interrupted?: boolean;                           // the user STOPPED this session mid-turn and hasn't messaged it since → "interrupted" badge; its quiet is user-chosen, auto-nudge holds off until their next message (kernel build_feed; the user 2026-07-05)
  retrying?: { since?: number | null; count?: number } | null;   // the OPEN turn is inside an api-retry storm (kernel _session_retrying, live backend state) → "⚠ retrying since HH:MM" chip on the working card; without it a storm reads as plain healthy Working (the user 2026-07-09)
  autoFiled?: boolean;                             // settled → moved to COMPLETED by the auto-filing rule (keeps the green ring)
  explicitDone?: boolean;                          // every path explicitly DONE-stamped → blue ring (blue+green when settled agrees)
  // the owning session is live-blocked (permission/picker, or stopped on an API error) ON this card's
  // work → the card itself files under BLOCKED (the user's ruling 2026-06-11; apiError 2026-06-16).
  blocked?: { state: string; what: string; status?: number; text?: string;
              tooLong?: boolean;   // apiError: a "prompt is too long" error (on you → compact) vs a transient API error
              spendLimit?: boolean;   // apiError: a monthly spend cap (on you → raise it, never auto-retried; the user 2026-07-14)
              modelLimit?: boolean;   // apiError: this session's MODEL is out of allowance (on you → switch model or add credits; the user 2026-08-01)
              refusal?: boolean;   // apiError: the model's safeguards refused the prompt itself (on you → rewrite it or drop the thread; never auto-retried — deterministic on the same input, the user 2026-08-15)
              mode?: string; since?: number;   // judgeAuth adds these: which billing its judges ride ('key'|'login') + the first refusal time — romp can't analyze the session until the credential is fixed (the user 2026-08-12)
              capOffer?: { resetsAt: number; window?: string };   // apiError: login-billed session dead on the account's cap + a key on hand → the explicit switch OFFER; the pick is yours alone, both directions (2026-08-30)
              toName?: string; toSid?: string;    // parkedHandoff adds to*
              mid?: string; frm?: string; to?: string; origin?: string; body?: string; gist?: string };   // quarantine (held peer mail) adds these; gist = the bus's 90-char collapse for the compact card line
  summary?: string | null;                         // distiller's key takeaway for a COMPLETED goal → the done card's one auto-written line (kernel asks.append); null until produced
  distillState?: "completed" | "blocked" | null;   // the GENUINE resolution state the distiller line keys on, so the brief/takeaway rides the real block instead of the transient `column` (which recheck/rejudging flicker to working) — the user 2026-07-21; absent from older/remote payloads → fall back to column
  blockSummary?: string | null;                    // block-distiller's decision brief for a BLOCKED goal → the blocked card's one auto-written line (kernel 466393c); null until produced
  briefParts?: { id?: string; since: number }[] | null;   // MULTI-item brief: one {id, since} per paragraph IN ORDER (judge briefParts) → per-paragraph "Nm ago" stamps; null/absent = single ask, the card header's age is the stamp (the user 2026-07-24)
  summaryParts?: { id?: string; since: number }[] | null;   // the DONE twin: per-paragraph done-event stamps for a takeaway the distiller split by <completed-items> (the user 2026-07-24)
  summaryStale?: boolean;                          // the user followed up AFTER the shown takeaway (kernel: followupAt > distilledMt) → the summary section carries the stale note until the re-distill lands (the user 2026-08-19)
  background?: string | null;                      // distiller's BACKGROUND section: re-orientation for a reader who forgot the thread → the card's collapsed-by-default section above the takeaway (the user 2026-07-02)
  summaryAnchorUuid?: string | null;               // click the summary line → the completion turn's wrap-up block (kernel build_feed completed pin; cited/latest-prose fallbacks — the user 2026-07-14)
  summaryAnchorQuote?: string | null;              // the distiller's verbatim supporting sentence, pre-located in the cited atom (T218) — the landing scrolls to and highlights it; null keeps the whole-message landing
  summaryAnchorsPara?: ({ u: string; q?: string } | null)[] | null;   // per-paragraph landings (T220, the user's ruling): aligned to the takeaway's paragraphs; null entry = that paragraph falls back to the whole-summary landing
  warns?: { kind: string; t: number; msg: string; detail: string }[] | null;   // judge-stamped anomalies (judge _node_warn → kernel build_feed): yellow "warning" chip; click opens the detail modal (the user 2026-07-02)
  failLog?: { t: number; line: string; model: string; note: string }[] | null;   // the summarizer's failed ATTEMPTS on this card (judge _fail_log): when, which line, which MODEL, the literal error — the chip's hover history + the modal's "What was tried" (the user 2026-08-18, who needed to SEE "tried opus — 529" ×3 to know switching the model would fix it)
  nudged?: { count: number; times: number[] } | null;   // auto-nudge HISTORY (kernel _nudge_times): how many times romp followed up + when — the stalled chip's evidence (tooltip + modal line, the user 2026-07-02)
  warnRows?: { t: number; judge: string; err: string; note?: string; debug?: { input?: string; reply?: string } }[] | null;   // DEBUG MODE only (romp debug on): every judge failure touching this card (kernel _card_warn_rows) → "Warnings (debug)" modal section; rows captured in debug carry the failing call's input + reply (the user 2026-07-09)
  origin?: { peer: string; peerSid: string; peerHost?: string; color: { bg: string; fg: string } | null; live?: boolean } | null;
  handoffTo?: { peer: string; peerSid: string; peerHost?: string; color?: { bg: string; fg: string } | null } | null;  // sender-side handoff provenance (the user 2026-08-24): this card IS a top-level "↪ delegated to <peer>" tracking node — the kernel titles it with the WORK and ships the delegation here, the mirror of origin's "↪ from"; click opens the recipient
  satellite?: boolean | null;                        // tracked delegation (the user 2026-08-24): this card is the recipient-side copy of a delegator-homed primary — off the default board; the session filter still reaches it (nothing runs in secret)
  delegTracked?: { name: string; host?: string; sid: string; color?: { bg: string; fg: string } | null }[] | null;  // tracked delegation PRIMARY: the recipient identities whose live status this one card carries  // courier handoff: planted by a peer's message → "↪ from <peer>"; peerHost = a FEDERATED sender's host, rendered as the quiet "host:" prefix (absent on older payloads / local senders). live = the sender's linked entry is still OPEN; false → the badge is PROVENANCE, dimmed (the completed-column merge, the user 2026-08-16)
  waitingOn?: { peerSid: string; name: string; color: { bg: string; fg: string } | null; inCycle: boolean; kind?: string; since?: number } | null;  // unanswered msg out to a live peer → "Awaiting <peer>" chip, or "Handed off to <peer>" when kind is "delegate" (peer name in native colour, no emoji; kernel _wait_for_graph; the user 2026-06-22 / 2026-07-25). since = when the unanswered ask was sent → the chip's elapsed readout (the user 2026-08-23)
  awaiting?: { why?: string | null; kind?: string | null; since?: number | null; tasks?: string[] | null;
               peers?: { name: string; host?: string; sid?: string; color?: { bg: string; fg: string } | null }[] | null } | null;   // peers: delegation wait → the box names them in identity colour (the user 2026-08-23)   // AWAITING flavor: held in Working, ⏳ awaiting badge — waiting on dispatched/delegated work (agents/subagents/a build), NOT on you (kernel build_feed; the user 2026-06-22). The peer case rides waitingOn; this carries the generic "why". `tasks` = live bg-task descriptions (the user 2026-07-13): present → the compact "Awaiting task" pill (expands the list, like Sub-goals) replaces the boxed why. since = the wait's own event time → the box/pill elapsed readout (the user 2026-08-23)
  groupTitle?: string;                             // host: this ask shares a typed turn with siblings → the group's title
  groupN?: number;                                 // host: sibling count for that turn (>1 ⇒ fold into one group card)
  provisional?: boolean;                           // a LIVE-PROMPT placeholder (kernel _provisional_card): the session is working an in-progress turn the planner hasn't classified yet. No goal node (empty tree) — dim, non-interactive, no clear/nudge/modal; replaced by the real card once the planner places the segment.
  judging?: boolean;                               // the turn has SETTLED and the judge's pass is due/in flight → the swirl chip says Analyzing… — on a provisional card while the planner's classify is pending (the user 2026-07-12), and on a REAL working card while the closer's verdict is (the settle→verdict gap, the user 2026-07-13); an open turn stays the honest Working…
  notify?: boolean | null;                         // per-card bell (the user 2026-07-28): the kernel fires an OS notification when THIS card enters needs_input/completed. EFFECTIVE state (card override > session override > the master bell's default, 2026-08-09) — with the master on, every card arrives armed unless muted. True/absent, never false
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
// Per-node LOG-story expand state (the user 2026-07-20), key = askId + ":" + nodeId. Default collapsed:
// an open sub shows a one-line "asked you · 2h ago" gist; membership here expands the full
// block/unblock history. Progressive disclosure — same key shape as collapsedNodes, survives re-renders.
const nodeLogOpen = new Set<string>();
let fullscreenAskId: string | null = null; // ask itemId OR group key "g:<turnId>" shown in the modal (single-click)
let modalRenderedId: string | null = null; // last target the modal body was built for → reset body cache on change
// Per-sub FOLLOW-UP target (the user 2026-06-17): a blocked sub-node's "↳ follow up" re-points the (robust,
// outside-the-tree-body) footer composer at THAT sub instead of the whole card, so the answer files under it
// and unblocks just that branch. null = the composer follows up on the whole card (the default).
let followupSub: { itemId: string; title: string } | null = null;
// Set by renderModal (captures the footer composer); called from a tree node's "↳ follow up" — kept as a
// module ref (not threaded through renderTreeBody) so the per-node button reads the CURRENT opener at click time.
let openSubFollowUp: ((itemId: string, title: string) => void) | null = null;
let hoverAskId: string | null = null;      // transient hover focus (white border + previewed journey)
let pinnedAskId: string | null = null;     // double-click PIN (persists after hover-leave)
// KEYBOARD-NAV cursor (the user 2026-07-01): "" = mouse mode; "cards" = an arrow cursor over cards; "card" =
// focus is inside one card, arrows step its clickable elements. Armed when the shell hands the feed keyboard
// focus (Alt+Arrow). Reuses the mouse hover + click code paths so behavior can't drift. See the kb* block below.
let kbMode: "" | "cards" | "card" = "";
let kbCardEl: HTMLElement | null = null;    // the card the cursor is on
let kbEls: HTMLElement[] = [];              // that card's clickable elements (in "card" mode)
let kbElIdx = -1;
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
// Clear-all pushes the whole batch). "Undo" pops the latest and re-inserts those cards IMMEDIATELY —
// optimistic restore — so the card reappears on click instead of waiting on the kernel round-trip + next feed
// build. Mirrors the kernel's _undo_clear (restores the most-recent clear batch). (the user 2026-06-27.)
const clearedStack: AskItem[][] = [];
// The inverse of pendingCleared: ids we've optimistically RESTORED, kept (with their cached card) until a
// kernel push actually carries them again — otherwise the very next push (before the kernel un-archived) would
// replace `asks` and drop the just-restored card, a flicker. Dropped once the kernel lists the id.
const pendingRestored = new Map<string, AskItem>();
// Finish an optimistic dismiss: the 180ms fade just removed the card element, so drop the item(s) from the
// LOCAL model and re-render NOW — in grouped mode a run whose last card left takes its session-name header
// with it, and the column count follows, instead of both lingering until the next kernel push (the user
// 2026-07-13: "the card disappears really fast… make [the session name] disappear immediately"). Only the
// local view advances; pendingCleared still guards incoming pushes until the kernel confirms the clear.
function dropDismissed(ids: string[]): void {
  const gone = new Set(ids);
  asks = asks.filter((a) => !gone.has(a.itemId));
  render();
}
// Optimistic follow-up MOVE (the user 2026-06-30): submitting a follow-up on a blocked card moves it to
// Working IMMEDIATELY, instead of waiting out the kernel round-trip (be.send + build_feed + push). The kernel
// stays AUTHORITATIVE — this is only a short-lived prediction: the kernel's own optimistic_followup flips the
// card to working, and the next push that confirms it clears the prediction (reconcileFollowMove).
//
// The prediction ends on an EVENT, never a stopwatch (the user 2026-07-21). It used to get 4 seconds to be
// confirmed by a payload and then reverted with "that follow-up didn't move the card to Working", but the
// feed serves a goal-store snapshot frozen for the length of a judge pass — routinely 30-80s — so a reply
// landing mid-pass could not be confirmed inside any window worth waiting, and the toast fired while the
// session was already working the reply. Both halves are fixed: the kernel now punches user writes through
// that snapshot (_feed_goals), and it ANSWERS the prediction instead of leaving the client to time it out.
// cardMoveAck carries ok — false is the one real failure, the goal gone from the store or sealed by a view
// clear, and the only thing worth a toast — plus the buildId of the newest feed build already underway, so
// the prediction can wait for a payload built AFTER the gesture rather than trusting one that could not
// possibly know about it yet. MOVE_ACK_MS is now only a backstop for an answer that never arrives.
const MOVE_ACK_MS = 15000;
const pendingFollowMove = new Map<string, number>();   // card itemId → backstop timer id; KEY = still predicting
// card itemId → the ack's buildId AND the kernel whose counter it is on. Every kernel numbers feed
// builds independently, so an ack is only comparable against the SAME host's frame of a merged payload
// (the user 2026-08-15: the local kernel's buildId, large after days of uptime, "outranked" a remote
// ack's small post-restart buildId on the first merged emission — see reconcileFollowMove).
const pendingMoveAck = new Map<string, { host: string; buildId: number }>();
// What KIND of reply put each prediction in flight (the user 2026-07-20: EVERY context-carrying reply flips
// its card to Working instantly, not just the feed composer's own follow-up):
//  - "followup": a message rides along → the prediction wears the re-check styling ("Followed up" chip),
//    and an unconfirmed prediction reverts WITH a toast (a silently-unheeded message must be apparent).
//  - "answer": a picker/permission answer resolved the session's live block — no chip, and the prediction
//    YIELDS to the first authoritative payload (see reconcileFollowMove). The revert is SILENT: an unheeded
//    answer re-shows the ⏸ blocked card, which IS the signal — unlike a message, nothing can be lost.
// Sub-goals the user crossed off in the modal whose kernel ack hasn't landed yet. Sticky rather than a
// one-shot DOM edit: the feed re-renders on every kernel push, and a push already in flight when the
// click happened still carries the OLD tree, so a one-shot paint would flip the tick straight back off
// (the user 2026-07-23). An entry survives until the authoritative tree agrees, or until the kernel says
// it disagrees — nodeOverrideResult, whose failure path reverts it and says why out loud.
const pendingDone = new Set<string>();
function reconcilePendingDone(asks: AskItem[]) {
  if (!pendingDone.size) return;
  const seen = new Map<string, string>();               // nodeId -> its status in this payload
  for (const a of asks) for (const n of a.tree || []) seen.set(n.id, n.status);
  for (const id of Array.from(pendingDone)) {
    const st = seen.get(id);
    // done → the kernel caught up, drop the optimistic flag. Absent → the node is gone from the tree
    // entirely, so there is nothing left to paint and holding the flag would leak it forever.
    if (st === "done" || st === undefined) pendingDone.delete(id);
  }
}
type MoveKind = "followup" | "answer";
const pendingMoveKind = new Map<string, MoveKind>();
// COLUMN-FLIP TRIPWIRE (the user 2026-07-28, who watched a just-replied card bounce Working →
// Completed → Working and could not be told afterwards WHICH layer bounced it — every candidate
// mechanism checked out sound post-hoc, and no surface records what the view actually SHOWED).
// Every rendered column change posts a clientDiag breadcrumb (client-diag.jsonl) carrying what
// most recently changed the render's inputs, so the next bounce is attributed from the recorded
// trail instead of unreproducible archaeology. Ids only, no card text.
const shownCol = new Map<string, string>();            // itemId → column as last RENDERED (post-prediction)
let lastFeedEvent = "init";                            // the input change the next render reflects
let lastPayloadBuildId = 0;
function auditShownColumns(list: AskItem[]) {
  const seen = new Set<string>();
  const firstRender = shownCol.size === 0;             // a fresh pane "appears" everything — not a delta
  const appeared: string[] = [];
  for (const a of list) {
    seen.add(a.itemId);
    const prev = shownCol.get(a.itemId);
    if (prev === undefined) appeared.push(a.itemId);
    if (prev !== undefined && prev !== a.column) {
      vscodeApi?.postMessage({ type: "clientDiag", surface: "feed", what: "colflip",
        data: { id: a.itemId, from: prev, to: a.column, ev: lastFeedEvent,
                buildId: lastPayloadBuildId, predicted: pendingFollowMove.has(a.itemId) } });
    }
    shownCol.set(a.itemId, a.column);
  }
  const gone: string[] = [];
  for (const id of Array.from(shownCol.keys())) if (!seen.has(id)) { gone.push(id); shownCol.delete(id); }
  // ITEM-SET TRIPWIRE (the user 2026-07-31, cards blinking in and out): the colflip trail above only
  // records column CHANGES, so a card leaving/re-entering the render entirely — the observed flicker —
  // was invisible to every log. Same breadcrumb channel; ids only, no card text. `ev` names the input
  // change this render reflects, so a blink is attributed to the layer that dropped the card (a host
  // payload, a filter, a fold) instead of re-guessed from pixels.
  if (!firstRender && (appeared.length || gone.length)) {
    vscodeApi?.postMessage({ type: "clientDiag", surface: "feed", what: "itemset",
      data: { appeared, gone, total: list.length, ev: lastFeedEvent, buildId: lastPayloadBuildId } });
  }
}
function clearFollowMove(itemId: string, why = "") {
  const t = pendingFollowMove.get(itemId); if (t) clearTimeout(t);
  pendingFollowMove.delete(itemId); pendingMoveKind.delete(itemId); pendingMoveAck.delete(itemId);
  if (why) lastFeedEvent = "clear:" + why;             // tripwire attribution only; behavior unchanged
}
function optimisticFollowMove(itemId: string, kind: MoveKind = "followup") {
  const prev = pendingFollowMove.get(itemId); if (prev) clearTimeout(prev);
  pendingMoveKind.set(itemId, kind);
  pendingMoveAck.delete(itemId);                       // a fresh gesture waits on its OWN answer, not the last one's
  lastFeedEvent = "predict:" + kind;
  const timer = window.setTimeout(() => {
    if (!pendingFollowMove.has(itemId)) return;        // a push already confirmed the move → nothing to do
    const k = pendingMoveKind.get(itemId);
    clearFollowMove(itemId, "backstop-noack");         // give the kernel authority: drop the prediction
    // Reaching here means the kernel never answered AT ALL (see ackFollowMove) — not that it declined the
    // move, which is what the old wording claimed on every mid-pass reply. An "answer" prediction has no
    // ack by design and always yields to the first payload, so it never gets this far.
    if (k !== "answer") feedToast("romp didn’t confirm that move, so the card is back to the state romp reports. Check it.");
    render();                                          // fall back to the kernel-authoritative state
  }, MOVE_ACK_MS);
  pendingFollowMove.set(itemId, timer);
}
// The kernel's ANSWER to a predicted move (cardMoveAck, the user 2026-07-21). ok=false is the only genuine
// "it didn't stick": the goal is no longer in the store (compacted or rotated away) or a view clear sealed
// it — worth interrupting for, and precise about what happened, including that a follow-up's message still
// went out. ok=true records the buildId the prediction must outlive and re-arms the window SILENTLY: the
// kernel has spoken, so nothing from here on is worth a toast, but a prediction must never outlive the
// answer either, so the backstop stays armed in case a payload goes missing.
function ackFollowMove(itemId: string, ok: boolean, buildId: number, host: string) {
  if (!pendingFollowMove.has(itemId)) return;
  if (!ok) {
    clearFollowMove(itemId, "ack-fail");
    feedToast("Your reply was sent, but that card isn’t on the board any more to move to Working.");
    render();
    return;
  }
  pendingMoveAck.set(itemId, { host, buildId });
  const prev = pendingFollowMove.get(itemId); if (prev) clearTimeout(prev);
  pendingFollowMove.set(itemId, window.setTimeout(() => {
    if (!pendingFollowMove.has(itemId)) return;
    clearFollowMove(itemId, "backstop-noconfirm"); render();   // silent wedge guard: no payload ever answered the ack
  }, MOVE_ACK_MS));
}
// On a fresh authoritative payload: a predicted card the kernel now lists as working (or no longer lists at
// all — cleared/absorbed) is CONFIRMED → drop the prediction + its timer. Else keep predicting (not caught up)
// — EXCEPT an "answer" prediction, which yields to the first payload either way: the kernel rebuilds the feed
// right after retiring the picker (answerAsk → _mark_views_dirty), so a payload that still shows the card out
// of Working is post-answer truth — a real remaining/renewed block (e.g. the next permission prompt of a
// burst). Holding the prediction there would MASK a genuine "needs you"; dropping it re-shows the ⏸ card.
function reconcileFollowMove(incoming: AskItem[], buildId: number, buildIds?: Record<string, number>) {
  for (const id of Array.from(pendingFollowMove.keys())) {
    const a = incoming.find((x) => x.itemId === id);
    if (!a || a.column === "working" || pendingMoveKind.get(id) === "answer") {
      clearFollowMove(id, !a ? "gone" : a.column === "working" ? "confirmed" : "answer-yield");
      continue;
    }
    // ACKED, yet this payload still shows the card elsewhere. Trust it ONLY if it was built after the kernel
    // took the gesture: an older payload (one already in flight when the click landed) cannot know about the
    // reopen, and taking it as the answer is exactly the bounce back to Completed this replaced. A NEWER one
    // is the kernel's own state, so yield to it silently — the reply landed, the card simply moved on (the
    // work finished, a fresh block arrived), which is honest to show and never a failed move.
    //
    // "After" only means anything on ONE counter (the user 2026-08-15, whose reply to a remote card
    // bounced Working → Completed → Working): every kernel numbers its own feed builds, and the merged
    // payload's top-level buildId is the LOCAL kernel's — days of uptime vs a just-restarted remote made
    // it "outrank" every remote ack instantly, dropping the prediction while the cached remote frame
    // still predated the reopen. So compare the ack against the CARD's own kernel: its host's entry in
    // the per-host buildIds map (mergeHostFeeds). A single-kernel payload has no map — there the
    // top-level buildId IS the card's kernel's counter, for local (un-acked-host "") cards. A payload
    // that can't be placed on the ack's counter simply doesn't outrank: the prediction waits for
    // confirmation by content, with the MOVE_ACK_MS backstop unchanged behind it.
    const acked = pendingMoveAck.get(id);
    if (acked === undefined) continue;
    const cardHost = hostOf(a.sid);
    if (cardHost !== acked.host) continue;   // an ack from some other kernel says nothing about this card
    const mark = buildIds ? buildIds[cardHost] : (cardHost === "" ? buildId : undefined);
    if (typeof mark === "number" && mark > acked.buildId) clearFollowMove(id, "outranked");
  }
}
// Render-time: keep each still-unconfirmed predicted card in Working, styled like the kernel's own re-checked
// follow-up (recheck + followupPending), so the optimistic card matches the authoritative one with no jump.
//
// COPY the card, never write into it (the user 2026-08-02, who watched a just-replied card bounce
// working → blocked → working): on the federated page the ask objects in a payload ARE the
// FederationManager's cached per-host frames, served by reference (mergeHostFeeds concatenates the cached
// arrays and the merged frame arrives via a same-realm MessageEvent — no structured clone). An in-place
// `a.column = "working"` therefore wrote the prediction INTO that cache, and the next merged re-emit —
// fired by ANY host's frame, seconds later — handed the pane its own edit back as kernel truth, which
// reconcileFollowMove took as the kernel's confirmation and dropped the prediction. The next local build
// already in flight when the reply landed (honestly pre-reply) then bounced the card back to Blocked with
// no prediction left to hold it. Replacing the list SLOT with a copy keeps the render identical while the
// cached frame stays exactly what the kernel sent, so the prediction ends only on the real events.
function applyFollowMove(list: AskItem[]) {
  if (!pendingFollowMove.size) return;
  // now, in the server's epoch-second unit (kernel is local). Bump the predicted card's sort key to now so
  // the INSTANT optimistic move lands at the BOTTOM of Working, matching where the kernel's authoritative
  // followupAt stamp keeps it once this prediction clears — no top-flash then lurch-down (the user 2026-07-03).
  const nowSec = Math.floor(Date.now() / 1000);
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (!pendingFollowMove.has(a.itemId) || a.column === "working") continue;
    const c: AskItem = { ...a, column: "working" };
    if ((pendingMoveKind.get(a.itemId) ?? "followup") === "followup") { c.recheck = true; c.followupPending = true; }   // plain move / answer: no chip
    if (c.t < nowSec) c.t = nowSec;   // sort to the bottom (newest); the group's repr follows via buildGroup
    list[i] = c;
  }
}
// (drag-to-Working and the modal's "Move to Working" button were REMOVED, the user 2026-07-25: a
// messageless recategorize voids verdicts while adding no information — zero recorded uses, and a
// reply to the card does everything it did plus gives the agent something to act on. The kernel/judge
// reopen machinery stays: the reply path is its remaining caller, and historical "move" journal
// events still replay.)
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

// Session identity colours are 6-digit hex; split one into [r,g,b] channels. The card border colour is
// CSS-driven from these channels (--card-r/g/b) so the outline is a PLAIN rgba (not color-mix(), which a
// reused card node can silently reject) AND the highlight can bold the SAME colour by just raising the alpha
// in CSS (the user 2026-07-15). Returns null for a non-hex value so the caller can fall back to the recency
// tint channels.
function hexToRgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-fA-F]{6})$/.exec((hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
// Write the card's outline channels so CSS paints rest (0.5α) + a bolded .focused/.pinned in the SAME colour.
function setCardChannels(card: HTMLElement, rgb: [number, number, number]) {
  card.style.setProperty("--card-r", String(rgb[0]));
  card.style.setProperty("--card-g", String(rgb[1]));
  card.style.setProperty("--card-b", String(rgb[2]));
  card.style.borderColor = "";   // CSS owns border-color now (rgba(var(--card-*), …)); clear any stale inline
}
const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;

// The settings gear (the ⛭ modal + analytics) is part of THIS bundle now —
// gear.js builds its DOM here and rides our one kernel channel, so both hosts
// (the kernel's /feed page and the VS Code feed panel) get the same modal.
// Opened by a {romp:'openSettings'} window message (web shell rail / VS Code menu).
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { initGear } = require("./gear.js");
initGear((m: Record<string, unknown>) => vscodeApi?.postMessage(m));

// The romp strip (VS Code only — the host opts in via __rompShowStrip): usage
// bars + the gear button, docked below #feed-foot. The gear raises the modal
// in THIS document (the gear listener above).
initStrip(() => window.postMessage({ romp: "openSettings" }, "*"),
  (m: Record<string, unknown>) => vscodeApi?.postMessage(m));
installSettingsSync();   // a gear save in ANOTHER VS Code pane lands here via the host
// the overall theme applies to THIS document too (2026-08-28): at boot and on every settings
// write — before this only the chat pane wore the body classes, so a light theme could never
// reach the feed
applyTheme(document, loadSettings());
onExternalSettingsChange((s) => { applyTheme(document, s); render(); });

// Card-display prefs read straight from the shared 'romp:settings' (the kernel's ⛭ gear writes it; same
// document as this feed bundle). Default ON. These gate the CARDS only — the modal always shows everything
// (the user 2026-06-17). `!== false` so a missing key defaults to shown.
function feedPrefs(): { newestFirst: boolean; collapsed: boolean; grouped: boolean; stacked: boolean } {
  try {
    const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
    // newestFirst + collapsed default OFF (=== true): the feed's natural order is oldest-first, and cards
    // arrive with their summary open (the user 2026-07-07). collapsed is the DEFAULT section state new cards
    // inherit — a per-card expand overrides just that card without turning the mode off. (Sub-goals is now one
    // of the mutually-exclusive sections 2026-07-08; the same `collapsed` default applies — see resolveSec.)
    // grouped (the user 2026-07-13): each column groups its cards by SESSION (tab/lane order), a session-name
    // header on the backdrop between runs, the per-card name dropped — the compact by-session read.
    // Default ON (!== false, same day): grouping is the feed's normal reading mode; the toggle opts OUT.
    return { newestFirst: s.newestFirst === true, collapsed: s.collapsed === true, grouped: s.grouped !== false,
             stacked: s.stacked === true };
  } catch { return { newestFirst: false, collapsed: false, grouped: true, stacked: false }; }
}
// The kernel's session order (session-order.json — the SAME order the chat tabs + timeline lanes hold; the
// user 2026-07-13: grouped-mode sessions must match it). Rides every feed push; federation concatenates
// per-host orders local-first, ids pre-prefixed.
let sessionOrder: string[] = [];
// The chat tab strip's sessions (sid+name+color), riding every feed push: the footer's session-filter
// menu lists exactly the tabs (the user 2026-08-08) — a session with no cards still appears, and
// filtering to it shows an empty board. Federation prefixes sid+name per host and concatenates.
let sessionsMeta: { sid: string; name: string; color: { bg: string; fg: string } | null }[] = [];
// Attached hosts whose CARD payload hasn't merged yet (the user 2026-08-25: sessions land via the
// faster channels, cards trail with no cue) — the federation merge names them (pendingHosts), the
// board hints per host, and the hint retires ONLY on the real events: that host's first contribution
// (an empty one included) or its detach. The 45s mark ESCALATES the copy ("still waiting…"), never
// hides — the first cut's backstop RETIRED the hint while the host genuinely still pended (the user
// 2026-08-25, round two: the dots vanished, the cards still hadn't come; the signal knew, the
// backstop overrode it — a backstop must never make the board lie about a true wait). A DEAD link
// (pendingDead, from the merge's socket truth) names itself instead of waiting open-endedly.
let pendingHosts: string[] = [];
let pendingDead: string[] = [];
const hostloadTimers = new Map<string, number>();
const hostloadLong = new Set<string>();
function syncHostloadBackstops(): void {
  for (const h of pendingHosts) {
    if (!hostloadTimers.has(h)) {
      hostloadTimers.set(h, window.setTimeout(() => { hostloadLong.add(h); render(); }, 45000));
    }
  }
  for (const [h, t] of Array.from(hostloadTimers)) {
    if (!pendingHosts.includes(h)) {   // the payload landed (or the host detached) — the ONLY removals
      window.clearTimeout(t);
      hostloadTimers.delete(h);
      hostloadLong.delete(h);
    }
  }
}
// The feed's LOCAL tag lens (the user 2026-08-25, T70): the shared TagLens model (tag-lens.ts, the
// multi-select every surface speaks) applied as THIS board's own deliberate narrowing — never the
// shared blob's `active` (the decoupling ruling stands); the payload's views blob feeds tag
// DEFINITIONS only (lensUnions). Persistence is sessionStorage, the feed's storage-split convention
// (romp:feedOnly's reasoning): reload-proof, but a fresh window starts on All — a lens persisting
// for days would read as silently missing cards, and the disclosure line only mitigates. The tags
// dialog's "set for all surfaces" writes localStorage romp:feedTags-set {lens,t}; the storage
// listener below adopts it into this pane's own lens (the PR-B adoption contract).
let feedTagViews: SessionViews | null = null;
let feedLens: TagLens = { all: true };
try { feedLens = JSON.parse(sessionStorage.getItem("romp:feedTags") || "") || { all: true }; } catch { /* default All */ }
function setFeedLens(l: TagLens): void {
  feedLens = l;
  try {
    if (lensAll(l)) sessionStorage.removeItem("romp:feedTags");
    else sessionStorage.setItem("romp:feedTags", JSON.stringify(l));
  } catch { /* storage blocked */ }
}
try {
  window.addEventListener("storage", (e) => {
    if (e.key !== "romp:feedTags-set" || !e.newValue) return;
    try {
      const v = JSON.parse(e.newValue);
      if (v && v.lens) { setFeedLens(v.lens as TagLens); render(); }
    } catch { /* malformed set-for-all — ignore */ }
  });
} catch { /* no storage events */ }
// The one session the board is filtered to, or null — the DEFAULT, nothing selected, everything shows.
// sessionStorage, deliberately: it survives this tab's reloads (webviews reload on updates) but a fresh
// window always starts unfiltered — a filter that persisted for days would read as silently lost cards.
let feedOnlySid: string | null = null;
try { feedOnlySid = sessionStorage.getItem("romp:feedOnly") || null; } catch { /* storage blocked */ }
function setFeedOnly(sid: string | null): void {
  feedOnlySid = sid;
  try { sid ? sessionStorage.setItem("romp:feedOnly", sid) : sessionStorage.removeItem("romp:feedOnly"); } catch { /* ignore */ }
}
// The SEARCH query (the user 2026-08-23): type-to-filter by session name, host prefix included —
// "snape" keeps every session on that machine. Same storage lifetime as the session filter: survives
// this tab's reloads, never a fresh window (a filter persisting for days reads as silently lost cards).
let feedSearchQ = "";
try { feedSearchQ = sessionStorage.getItem("romp:feedSearch") || ""; } catch { /* storage blocked */ }
function setFeedSearch(q: string): void {
  feedSearchQ = q;
  try { q ? sessionStorage.setItem("romp:feedSearch", q) : sessionStorage.removeItem("romp:feedSearch"); } catch { /* ignore */ }
}

// OPTIMISTIC colour echo from the chat pane's tab menu (the user 2026-08-08): the chat repaints its
// tabs the instant a swatch is picked, but this pane kept the old colour until the kernel's next feed
// REBUILD pushed — a second or two. The echo arrives kernel-free on two host-matched channels (the
// same pair settings sync rides): the browser's same-origin iframes hear the localStorage write
// (`storage` fires cross-document), and VS Code's extension fans {colorSync} to its other panels.
// Apply it to every copy this pane holds and re-render; the kernel's own re-broadcast reconciles.
function applyColorEcho(sid: string, bg: string): void {
  if (!sid || !bg) return;
  const color = { bg, fg: "#ffffff" };   // fg fixed white, matching the kernel's _name_color
  let hit = false;
  for (const a of asks) if (a.sid === sid) { a.color = color; hit = true; }
  for (const s of sessionsMeta) if (s.sid === sid) { s.color = color; hit = true; }
  const nm = sessionsMeta.find((s) => s.sid === sid)?.name || asks.find((a) => a.sid === sid)?.name;
  if (nm) sessionColors.set(nm, bg);     // held-mail cards look colours up by name
  if (hit) render();
}
window.addEventListener("storage", (e) => {
  if (e.key !== "romp:color-echo" || !e.newValue) return;
  try {
    const v = JSON.parse(e.newValue);
    applyColorEcho(typeof v.sid === "string" ? v.sid : "", typeof v.bg === "string" ? v.bg : "");
  } catch { /* malformed echo — the kernel push corrects momentarily */ }
});
// names of sessions currently WORKING → a working dot before that name everywhere
// it renders (card titles, modal title, group name). Pushed in each feed message.
// INSTANT chat acknowledgment for a card click (the user 2026-08-24: clicking into a not-shown
// session felt slow): the felt latency is the kernel ROUND-TRIP — click → WS → _reveal_chat_for →
// focus frame — and a busy kernel holds its WS handler behind push builds, while the chat already
// HOLDS every session's data (measured: the chat's own first render of a 6000-event hidden
// transcript is ~25ms). So the click also drops a same-origin echo the chat hears in milliseconds
// (the storage event; the romp:color-echo precedent), activating the tab/peek and the loader
// immediately; the kernel's focus frame follows and lands the anchor idempotently. VS Code webviews
// don't share localStorage — there the kernel path stands alone, unchanged.
function focusEcho(sid: string): void {
  try { localStorage.setItem("romp:focus-echo", JSON.stringify({ sid, t: Date.now() })); } catch { /* storage blocked */ }
}
let workingSet = new Set<string>();
// This machine's own name (kernel _self_host, on every feed payload) and the identity colour of every
// session the feed knows, keyed "host:name" for a remote one and plain for a local one. Held mail names
// BOTH ends of the exchange, and a session's colour is its identity everywhere else, so the card has to
// be able to look one up by name — the quarantine record carries names, not sids (the user 2026-07-29).
let feedSelfHost = "";
const sessionColors = new Map<string, string>();
// session name -> live background-process descriptions the JUDGE classified as services (kernel bgServices:
// a dev server the session keeps around — nobody waits on it, so it is session furniture, not a status).
// Rendered as a neutral chip on the grouped-mode session header; flat mode has no headers (the chat view's
// background-task box still lists the processes there).
let bgServicesMap: Record<string, string[]> = {};
const openBgSvc = new Set<string>();   // sids with the chip's process list expanded — survives re-renders
// COLLAPSED threads (the user 2026-07-31): grouped mode's session header carries a caret, and folding one
// leaves the header alone in every column that thread appears in, with a count of what is folded away.
// Keyed by SID, not by column-run, for the reason the request turns on: cards that do not exist yet must
// arrive collapsed too, and a card's column is not knowable when you fold the thread. Persisted across
// reloads with the rest of the disclosure state, and deliberately NOT pruned when the cards go away.
const collapsedThreads = new Set<string>();
// names of sessions idle-but-AWAITING background work (the user 2026-07-13): the same dot in await-green —
// matching the chat chip's Awaiting color — so a held session reads differently from a working one.
let awaitingSet = new Set<string>();
// Sessions the kernel LISTS but whose live state it could not read. These draw a gray ring, which
// is what lets a BLANK pip mean "alive and quiet" and nothing else — before it, an unreadable state
// and an idle one were the same nothing, so a rendering hole was indistinguishable from health.
let unknownSet = new Set<string>();
type DotState = "" | "work" | "await" | "unknown";
const dotFor = (name: string): DotState =>
  workingSet.has(name) ? "work" : awaitingSet.has(name) ? "await"
  : unknownSet.has(name) ? "unknown" : "";
// The pip explains itself on hover (the user 2026-07-22: learn the states from tooltips, not the
// CLI). It encodes TURN state — is anything running? — not attention; attention lives in the card.
const DOT_TIP: Record<Exclude<DotState, "">, string> = {
  work: "working — a turn is running right now",
  await: "awaiting — idle, but background work it dispatched is still running",
  unknown: "state unknown — romp couldn't read this session's live state",
};
// Ensure a `.fwork-dot` sits immediately before `nameEl` iff `state` is non-empty (idempotent on
// re-render; an existing dot RETINTS in place when the state flips). The name's text/color are untouched.
function setWorkDot(nameEl: HTMLElement | null, state: DotState | boolean) {
  if (!nameEl) return;
  const st: DotState = state === true ? "work" : state === false ? "" : state;
  const prev = nameEl.previousElementSibling;
  const has = !!prev && prev.classList.contains("fwork-dot");
  const paint = (d: Element) => {          // one kind class at a time; "work" is the bare base class
    for (const k of ["await", "unknown"]) d.classList.toggle(k, st === k);
    (d as HTMLElement).title = st ? DOT_TIP[st] : "";
  };
  if (st && !has) {
    const d = el("span", "fwork-dot"); paint(d);
    nameEl.parentElement?.insertBefore(d, nameEl);
  } else if (st && has) paint(prev!);
  else if (!st && has) prev!.remove();
}

let hostNow = Math.floor(Date.now() / 1000);
let showDismissed = false;
let dismissedCount = 0;
let canUndoClear = false;   // host: cleared.jsonl has rows → the UndoClear button shows
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

// The warn-detail overlay (the user 2026-07-02): clicking a card's yellow "warning" chip opens this —
// one entry per judge-stamped anomaly, each telling in detail what happened and why it's unexpected,
// so pipeline misbehavior is followable from the card instead of buried in judge-errors.jsonl.
// Same lightweight overlay pattern as feedConfirm (its own state machine; Esc / backdrop / Close).
// A warn kind stamped by a GIVEN-UP summarizer line (summary/brief/stall) — these get the "distill
// failed" chip label and the modal's Try again (the user 2026-08-13); other anomaly kinds stay "warning".
const DISTILL_FAIL_RE = /^(summary|brief|stall)-failed$/;

// Feedback for the modal's Try again (the user 2026-08-13, round 2: the first cut leaned on the card's
// Distilling… swirl, which only shows where the done-side line is the visible one — on a Working card
// the click looked like a silent no-op even as the retry SUCCEEDED). Success and refusal both toast,
// and silence itself is caught: a kernel that predates the redistill op drops it with no result at all,
// so a backstop timer names that case instead of leaving the click unanswered (the ack event is the
// signal; the timer only speaks when it never comes).
let redistillWatch: { itemId: string; timer: number } | null = null;
function armRedistillWatch(itemId: string): void {
  if (redistillWatch) window.clearTimeout(redistillWatch.timer);
  redistillWatch = {
    itemId,
    timer: window.setTimeout(() => {
      redistillWatch = null;
      feedToast("no answer from the kernel about the summary retry — it may predate this feature (restart romp to update it)");
    }, 6000),
  };
}

function feedWarnModal(cardTitle: string, warns: { kind: string; t: number; msg: string; detail: string }[],
                       ctx?: { itemId: string; sid: string },
                       failLog?: { t: number; line: string; model: string; note: string }[] | null): void {
  const back = el("div", "fconfirm-back fwarn-back");
  const box = el("div", "fconfirm-box fwarn-box");
  const head = el("div", "fwarn-head"); head.textContent = "Unexpected behavior";
  const sub = el("div", "fwarn-sub"); sub.textContent = cardTitle;
  box.append(head, sub);
  for (const w of warns) {
    const entry = el("div", "fwarn-entry");
    const meta = el("div", "fwarn-meta");
    meta.textContent = w.kind + " · " + relAge(Math.max(0, Date.now() / 1000 - w.t));
    const body = el("div", "fwarn-detail"); body.textContent = w.detail || w.msg;
    entry.append(meta, body);
    box.append(entry);
  }
  // The attempt log (the user 2026-08-18): each failed try as its own line — when, which model, the
  // literal error — so a one-model outage reads as "tried opus — 529, tried opus — 529, …" at a glance
  // instead of hiding inside prose. Chronological, capped at the kernel (judge _fail_log).
  if (failLog && failLog.length) {
    const entry = el("div", "fwarn-entry");
    const meta = el("div", "fwarn-meta"); meta.textContent = "What was tried";
    entry.append(meta);
    for (const f of failLog) {
      const row = el("div", "fwarn-detail");
      row.textContent = `${clockHM(f.t)} · tried ${f.model} for the ${f.line} — ${f.note}`;
      entry.append(row);
    }
    box.append(entry);
  }
  const btns = el("div", "fconfirm-btns");
  // "Try again" on a given-up summary line (the user 2026-08-13): the kernel journals the re-arm and the
  // next triage pass re-runs the distiller — the card's own "Distilling…" swirl is the live cue after
  // the modal closes; a kernel refusal comes back as a redistillResult toast (fail loudly).
  if (ctx && warns.some((w) => DISTILL_FAIL_RE.test(w.kind))) {
    const retry = el("button", "fconfirm-btn") as HTMLButtonElement;
    retry.textContent = "Try again";
    retry.onclick = (e) => {
      e.stopPropagation();
      retry.disabled = true; retry.textContent = "retrying…";   // acknowledged before any round-trip
      armRedistillWatch(ctx.itemId);                            // silence gets named, never swallowed
      vscodeApi?.postMessage({ type: "redistill", itemId: ctx.itemId, sid: ctx.sid });
      window.setTimeout(close, 300);   // cosmetic beat so the pressed label registers; the op is already posted
    };
    btns.append(retry);
  }
  const ok = el("button", "fconfirm-btn primary"); ok.textContent = "Close";
  btns.append(ok);
  box.append(btns);
  back.append(box);
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  ok.onclick = (e) => { e.stopPropagation(); close(); };
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

function clockHM(t: number): string {
  return new Date(t * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

// The latched Continue/Check-status button explains itself (T150, the user 2026-08-28: a card read
// 'Sent' and clicking did nothing — the latch was honest, the silence was not). One title writer for
// both states of both buttons: while held, it names WHAT was sent, WHEN, and the exact event that
// re-arms it; enabled, it says what a click does. The hover title is the minimum honest surface for
// a disabled control (the buttons rule) — the label itself stays one word.
function contTitle(latched: boolean, verb: string, at?: number | null): string {
  return latched
    ? verb + " sent" + (at ? " " + relAge(hostNow - at) : "") +
      " — waiting for the session's reply to be judged; this re-arms then, and the card moves on its own"
    : verb === "a continue"
      ? "nothing needed from you — asks the session to keep going"
      : "asks the session where each open item stands";
}

function relAge(sec: number): string {
  const s = Math.max(0, sec);
  // Sub-minute ages all read "<1m ago" (the user 2026-07-20): a card the user just acted on stamps t=now,
  // and a counting "0s ago"/"14s ago" label is churn without information — the tint already says "fresh".
  if (s < 60) return `<1m ago`;
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

// ---- ask card (the inbox unit) ----
// Anatomy (the user 2026-06-14): row1 = ask text, full width across the top; row2 = worker
// name (identity color, clickable) on its own row below it; row3 = age bottom-left, status
// badges + Clear bottom-right. Stacking the age/actions onto their own row frees the title
// and the (often long) session name to use the full card width instead of competing for it.
// Click the CARD → expand + light the DAG path on the timeline.
// Expanded body = the request DAG as a tree of NODES (state machine only); each
// node clicks to reveal its OWN reply history; ? nodes carry a decision sub-card.
// ── per-card notification bell (the user 2026-07-28) ─────────────────────────────────────────────
// Every card wears a small bell BUTTON in its bottom-right corner (the user 2026-07-28, round 2 —
// promoted from a right-click-only toggle): click it to arm/disarm an OS notification for when THIS
// card blocks on you or completes (kernel notify-cards.json; the session-wide version lives on the
// timeline lane / tab menu, and the bottom bar's master bell defaults every card ON at once — the
// kernel resolves the three most-specific-wins and sends the effective state, so a click here is a
// per-card override against the defaults). Armed = accent bell, always visible; off = slashed dim bell, revealed on
// card hover (the tab-close idiom — no clutter on a quiet feed). Right-clicking the card still opens
// the labelled menu for the same toggle. Optimism mirrors the lane toggles: pendingNotify holds the
// clicked value sticky across pushes until the kernel's payload agrees, so the bell never flickers
// back while the rebuild lands.
const pendingNotify = new Map<string, boolean>();   // itemId -> clicked value, until the payload confirms
let cardMenuEl: HTMLElement | null = null;

function dismissCardMenu(): void { if (cardMenuEl) { cardMenuEl.remove(); cardMenuEl = null; } }
window.addEventListener("mousedown", (e) => { if (cardMenuEl && !cardMenuEl.contains(e.target as Node)) dismissCardMenu(); }, true);
window.addEventListener("keydown", (e) => { if (e.key === "Escape") dismissCardMenu(); }, true);
window.addEventListener("scroll", dismissCardMenu, true);
window.addEventListener("blur", () => dismissCardMenu());

// the same drawn bell as the chat tab menu's toggle icon (16-unit viewBox, currentColor, slash = off)
function cardBellSvg(off: boolean): string {
  const slash = off ? '<line x1="1.6" y1="14.4" x2="14.4" y2="1.6"/>' : "";
  return '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M8 2 C5.9 2.2 4.7 3.8 4.7 5.8 L4.7 8 L3.4 9.9 L12.6 9.9 L11.3 8 L11.3 5.8 C11.3 3.8 10.1 2.2 8 2 Z"/>'
    + '<path d="M6.6 11.6 A1.5 1.5 0 0 0 9.4 11.6"/>' + slash + "</svg>";
}

function cardNotifyOn(it: AskItem): boolean {
  return pendingNotify.has(it.itemId) ? !!pendingNotify.get(it.itemId) : !!it.notify;
}

// paint the corner bell to a state: armed = accent + unslashed (always visible via .on), off = slashed
// dim (hover-revealed). The title explains the CLICK, so it always describes the opposite state.
function paintCardBell(card: HTMLElement, on: boolean): void {
  const btn = (card as any)._bell as HTMLElement | undefined;
  if (!btn) return;
  if ((btn as any)._bellOn === on) return;   // steady state: never churn the svg under a press (click-safety)
  (btn as any)._bellOn = on;
  btn.classList.toggle("on", on);
  btn.innerHTML = cardBellSvg(!on);
  const say = on ? "system notifications on for this task — click to stop"
    : "notify me when this task blocks on you or completes";
  setTip(btn, say);                                    // styled tip (tip.ts), not a native title
  btn.setAttribute("aria-label", say);                 // the icon-only button keeps an accessible name
}

// the ONE toggle path — the corner bell click and the right-click menu both land here
function setCardNotify(card: HTMLElement, it: AskItem, value: boolean): void {
  pendingNotify.set(it.itemId, value);               // sticky until the kernel's payload carries it
  paintCardBell(card, value);                        // acknowledge instantly, before the round-trip
  vscodeApi?.postMessage({ type: "cardNotify", itemId: it.itemId, sid: it.sid, value });
}

function showCardMenu(e: MouseEvent, card: HTMLElement): void {
  dismissCardMenu();
  const it = (card as any)._it as AskItem | undefined;   // the freshest payload copy (updateAskCard stashes it)
  if (!it) return;
  const on = cardNotifyOn(it);
  const menu = el("div", "ctx-menu");
  const item = el("div", "ctx-item ctx-item-toggle");
  const icon = el("span", "ctx-icon" + (on ? "" : " off"));
  icon.innerHTML = cardBellSvg(!on);
  const body = el("span", "ctx-item-body");
  const lab = el("span", "ctx-item-label"); lab.textContent = on ? "Stop notifying" : "Notify me";
  const sub = el("span", "ctx-item-sub");
  sub.textContent = on ? "no more system notifications for this card"
    : "system notification when this card blocks on you or completes";
  body.append(lab, sub);
  item.append(icon, body);
  item.addEventListener("click", (ev) => {
    ev.stopPropagation();
    dismissCardMenu();
    setCardNotify(card, it, !on);
  });
  menu.appendChild(item);
  // Browse the session's working tree. Only the sid rides: the feed payload doesn't carry cwd, and
  // "." lets the OWNING kernel resolve it authoritatively (_resolve_open_path) rather than this pane
  // scraping another pane's state. Gated on canPreview() (web only): the VS Code webview can't reach
  // the kernel origin, and the editor has its own explorer.
  if (canPreview()) {
    const browse = el("div", "ctx-item");
    browse.textContent = "Browse files";
    browse.addEventListener("click", (ev) => {
      ev.stopPropagation();
      dismissCardMenu();
      openFileBrowse(".", it.sid);
    });
    menu.appendChild(browse);
  }
  document.body.appendChild(menu);
  cardMenuEl = menu;
  const r = menu.getBoundingClientRect();   // at the cursor, clamped inside the pane
  menu.style.left = Math.max(0, Math.min(e.clientX, window.innerWidth - r.width - 4)) + "px";
  menu.style.top = Math.max(0, Math.min(e.clientY, window.innerHeight - r.height - 4)) + "px";
}

function makeAskCard(it: AskItem): HTMLElement {
  const card = el("div", "fitem ask");
  card.dataset.key = "a:" + it.itemId;
  const main = el("div", "fitem-main");
  // ROW 1 — ask title (left, wraps) with the TIME trailing it, right-justified on the title's LAST line
  // (the user 2026-07-07): small + faded so it reads as metadata, not part of the title. The title still
  // must NOT flex-grow (blank space right of it would trigger locate) — the time's margin-left:auto right-
  // aligns it instead. Freeing the time from row3 lets Background/Summary own that row (one on each side).
  const row1 = el("div", "fask-row1");
  const title = el("div", "fcard-title nav"); title.title = "locate this in the text";
  const time = el("span", "ftime");
  row1.append(title, time);
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
  // (the "reopened" chip was DELETED 2026-07-07: dead since cleared-is-sealed-forever made a follow-up
  // to a cleared card a FRESH goal (2026-06-22) — the kernel never produced the flag again.)
  // Now serves ONLY the "↩ re-judging" recheck state — the plain "↻ Followed up" (reopened-to-Working) badge
  // was removed (the user 2026-07-01: click-to-cite makes follow-up routine, so the ack is noise). updateAskCard
  // sets the text/title when it shows for recheck.
  const fupBadge = el("span", "fask-followedup"); fupBadge.textContent = "↩ re-judging"; fupBadge.title = "you followed up — no longer waiting on you; the judge will resolve it or re-block it on the next pass"; fupBadge.style.display = "none";
  // "done, confirming" (the user 2026-07-24): the done verdict is in; the card holds its Working spot
  // until the settle event (the session's attention moving on) files it under Completed — moving the
  // COLUMN at the verdict would flicker it back on any trailing touch, which is the exact flicker the
  // settle gate exists to prevent. The takeaway is already being distilled during this window.
  const dcBadge = el("span", "fask-doneconfirming"); dcBadge.textContent = "done, confirming";
  dcBadge.title = "ruled done — it files under Completed once the session has moved on; a follow-up before then reopens it in place";
  dcBadge.style.display = "none";
  // "follow-up failed" (plans/stalled-open-todos-nudge.md): romp asked this stalled goal ONCE and the
  // response didn't resolve it; per the anti-loop rule it is never re-asked, so the card says so instead.
  // RENAMED off "stalled" (the user 2026-07-23, superseding their 2026-07-02 label): that word now belongs
  // exclusively to the yellow Stalled section — romp holding a WORKING card — and this chip means the
  // opposite (romp already asked; the thread waits on YOU). One word per meaning, user-visible strings only.
  const nfBadge = el("span", "fask-nudgefailed"); nfBadge.textContent = "follow-up failed";
  nfBadge.title = "romp followed up once; the response didn't resolve it and it won't be re-asked — it's waiting on you";
  nfBadge.style.display = "none";
  // "interrupted" (the user 2026-07-05): the user stopped this session mid-turn and hasn't messaged it
  // since — its quiet is user-chosen, not a stall. Auto-nudge holds off until their next message, and
  // the card says why it's sitting still instead of reading like an orphaned working goal.
  const intBadge = el("span", "fask-interrupted"); intBadge.textContent = "interrupted";
  intBadge.title = "you stopped this session mid-turn; romp won't follow up on its own until you message it again";
  intBadge.style.display = "none";
  // "interrupting…" (the user 2026-07-07): a stop is IN FLIGHT — the CLI hasn't reached a stream boundary
  // yet — so the card holds this steady from the click until the interrupt settles, then swaps to the
  // past-tense "interrupted" badge. Working-yellow + faded (matches the chat chip's chip-interrupting), so
  // it reads as "still winding down" rather than a done state.
  const intingBadge = el("span", "fask-interrupting"); intingBadge.textContent = "interrupting…";
  setTip(intingBadge, "stop sent — waiting for this session to reach a stopping point");   // styled tip (tip.ts), not a native title
  intingBadge.style.display = "none";
  // yellow "warning" chip (the user 2026-07-02): a judge stamped an anomaly on this goal (kernel `warns`,
  // judge _node_warn — e.g. a distiller cite-miss). Click → the warn-detail overlay: what happened and why
  // it's unexpected, per warn. A BUTTON (not a span) so it's focusable; the element survives re-renders
  // (updateAskCard mutates it in place) and reads its data from the card at click time, so it's click-safe.
  const warnChip = el("button", "fask-warnchip"); warnChip.textContent = "warning";
  warnChip.style.display = "none";
  warnChip.onclick = (ev) => {
    ev.stopPropagation();
    const ws = (card as any)._warnsData as AskItem["warns"];
    const wit = (card as any)._it as AskItem | undefined;   // freshest payload → the ids Try again posts with
    if (ws && ws.length) feedWarnModal((card as any)._title?.textContent || "", ws,
                                       wit ? { itemId: wit.itemId, sid: wit.sid } : undefined,
                                       (card as any)._failLog as AskItem["failLog"]);
  };
  const waitOnBadge = el("span", "fask-waiton"); waitOnBadge.style.display = "none";   // "Awaiting <peer>" / "Deadlock <peer>", peer name in native colour (the user 2026-06-22)
  const blkBadge = el("a", "fask-blocked"); blkBadge.style.display = "none";   // ⏸ live permission/picker block → click opens the session
  const apiBadge = el("span", "fask-apierror"); apiBadge.textContent = "⚠ API error"; apiBadge.style.display = "none";   // red: session stopped on an API error
  // filled red (a new chip, deliberately distinct from the outlined api-trouble family): romp's OWN
  // analysis of this session is refused on its credential — the session may be fine; the judges are
  // down, and every card here is frozen until the user fixes the key/login (the user 2026-08-12)
  const jauthBadge = el("span", "fask-jauth"); jauthBadge.style.display = "none";
  // "⚠ retrying since HH:MM" (the user 2026-07-09): the session's OPEN turn is inside an api-retry storm —
  // still in motion, so the card stays in Working, but the storm must be visible: the API-error badge above
  // only fires once the session is idle-stalled, and nimbus's card read plain healthy "Working" through an
  // ~80-minute storm. Same red api-trouble family, faded (in motion, not stopped); no Retry button — the
  // auto-retry is already doing that.
  const retryBadge = el("span", "fask-retrying"); retryBadge.style.display = "none";
  const apiRetry = el("button", "fdismiss fretry"); apiRetry.textContent = "Retry"; apiRetry.title = "send “retry” into this session to resume"; apiRetry.style.display = "none";
  // the auth-expired card offers THE FIX, not just the problem (T157, the user: watch it, log in,
  // it works again): opens the gear's Billing login — the paste-code flow that works from the phone
  const apiLogin = el("button", "fdismiss ffollow"); apiLogin.textContent = "Log in…";
  apiLogin.title = "open Billing login — sign in from any browser (your phone works) and this session recovers on its next turn";
  apiLogin.style.display = "none";
  // CAP-SWITCH OFFER (the user's binding ruling, 2026-08-30: a session must NEVER silently switch
  // billing in either direction): a login-billed session dead on the account's usage cap gets an
  // explicit offer to bill the key for the rest of the window — the pick is the user's alone, and
  // switching back is equally manual. Declining is just Clear; the kernel stops minting the offer
  // the moment the window resets (the deciding event), and the post-reset auto-retry runs either way.
  const capLine = el("span", "fask-capoffer"); capLine.style.display = "none";
  const capBtn = el("button", "fdismiss fcapswitch") as HTMLButtonElement;
  capBtn.textContent = "Switch to the key"; capBtn.style.display = "none";
  const revive = el("button", "fdismiss frevive"); revive.textContent = "Revive"; revive.title = "bring this offline session back so the parked hand-off is delivered"; revive.style.display = "none";
  // RESUME-GATE buttons (the user 2026-07-21): a boot-deferred high-context session — Proceed reloads it now,
  // Compact on resume /compacts first so future turns shrink (still one reload now), Skip leaves it dormant.
  // QUARANTINE buttons (per-host trust): a held message from a DIRECTED peer — Approve delivers it,
  // Edit opens the modal to change the text before delivering, Deny drops it. Human-in-the-loop is the
  // whole point of directed trust, so nothing reaches the session until one of these is clicked.
  const qApprove = el("button", "fdismiss fq fq-ok") as HTMLButtonElement; qApprove.textContent = "Approve"; qApprove.title = "deliver this message to the recipient session"; qApprove.style.display = "none";
  const qDeny = el("button", "fdismiss fq fq-no") as HTMLButtonElement; qDeny.textContent = "Deny"; qDeny.title = "drop this message — with the option of a note back to the sender"; qDeny.style.display = "none";
  // The header "awaiting" chip was REMOVED (the user 2026-07-04): it duplicated the "Awaiting background
  // agents" box in the card body, which says the same thing with room for the full "why" — so the chip was
  // pure redundancy. The awaiting state now reads only from that body box (see the awaitSpin block below).
  const clr = el("button", "fdismiss"); clr.textContent = "Clear"; clr.title = "clear this task";   // plain-spoken (the user 2026-07-13, over the inbox-zero jargon)
  // "Continue" (the user 2026-08-08): the needs-you card's one-click "nothing needed from me, keep
  // going" — a REPLY with a kernel-canned body (askFollowUp cont:true), never a bare column move (the
  // removed cardMove is the cautionary tale: a move with no message adds no information). The card
  // history that earned it (2026-07-25..08-08): 58% of blocked episodes resolved with no input on the
  // card, and a fifth of Clears landed on sessions visibly mid-turn — a "stop" sent where the user
  // meant "keep going". Shown only on live needs-you cards without a live ask (updateAskCard).
  const cont = el("button", "fdismiss fcontinue") as HTMLButtonElement; cont.textContent = "Continue";
  cont.title = "nothing needed from you — tells this session to keep going and decide open questions itself";
  cont.style.display = "none";
  // The action corner (the user 2026-08-08): Continue+Clear ride the END of row1 in EVERY mode —
  // right-justified beside the timestamp when there's room, dropping below when the title/time need
  // the width (they keep first claim). wrap-reverse stacks the overflow line UNDER the first, so when
  // only one button fits per line Clear stays the higher one; on one line Continue sits left of Clear
  // (source order). Generalizes the grouped-mode float (2026-07-13) to all modes — the re-home dance
  // between rows is gone, which is the strongest form of the click-safety rule (the buttons never move).
  const btns = el("span", "fask-btns");
  btns.append(cont, clr);
  // The card-face "Status?" sweep was REMOVED (the user 2026-07-21): a card still Working doesn't need a
  // status poke from the card face, and once the decision brief carries a paragraph per blocked sub-goal
  // (the briefer's per-sub-goal takeaway) the summary already says where each thing stands, so the sweep was
  // redundant clutter on the face. The sweep still lives one click deeper as the modal footer's "Check
  // status" (feed-modal-status), and each sub-goal keeps its own "Check status" in the modal tree.
  // Manual "Nudge" REMOVED (the user 2026-06-30): once Auto Nudge is robust you never hand-nudge — the
  // background nudge follows up on a stalled working goal automatically, so the manual button (and the whole
  // concept of manually nudging) is gone. Working cards now have no footer action of their own.
  // The card's own "Follow up" button was REMOVED (the user 2026-07-01): click-to-cite covers it — clicking the
  // card (its summary or a sub-goal) drops a dismissible context chip in the chat composer, so a follow-up
  // needs no dedicated button. The MODAL keeps its Follow up (feed-modal-follow) for reading-then-replying, and
  // the modal tree keeps its per-sub-goal Follow up.
  // Session-STATE badges (⏸ approval / ⚠ API error / ⏳ waiting) ride the SESSION-NAME row, right after the
  // name — they describe the session's live state, and keeping them OFF the action row stops them shoving
  // the buttons past the card's right edge on a narrow card (the user 2026-06-19; mirrors the ↻ Followed-up
  // chip moved up 2026-06-18). idwrap is flex:1 so the name ellipsizes before the badge is ever clipped.
  // COMPACTNESS (the user 2026-07-07): Clear rides the NAME row (right side, after the chips) and the
  // Background/Summary toggles ride the TIME row — freeing a whole action row. So the action row holds only
  // Retry / Revive (rare states); both rows flex-WRAP so nothing overflows or overlaps on a narrow card.
  actions.append(revive, qApprove, qDeny);
  // "↪ from <peer>" provenance + the "reopened"/"↻ Followed up" chips ride the name row's right side;
  // row2 wraps them onto a new line when there isn't room, so the provenance never overlaps a chip
  // (the user 2026-06-20). origin sits left of the chips, matching the "from … · Followed up" reading order.
  // (Clear left this row 2026-08-08 — it rides row1's action corner in every mode now, see fask-btns.)
  // EVERY session-state badge rides row2 DIRECTLY — grouped mode hides idwrap wholesale (the card
  // drops its name into the session header), which silently blanked whatever lived inside it: first
  // found as the lone red Retry (the user 2026-08-24, screenshot — its ⚠ badge sat hidden in the
  // wrap), then the same mechanism for ⚠ retrying-since, judge-auth, and the ⏸ approval chip. As
  // direct children they render in BOTH modes, count toward row2's grouped-mode liveness, and the
  // API badge stays immediately before its Retry button — one visual unit. idwrap keeps only the
  // name. Placement only; every badge's mint/retire semantics are untouched.
  row2.append(idwrap, retryBadge, apiBadge, apiRetry, apiLogin, capLine, capBtn, jauthBadge, blkBadge, origin, fupBadge, dcBadge, nfBadge, intingBadge, intBadge, warnChip, waitOnBadge);
  // the bell BUTTON (the user 2026-07-28): INLINE in row1's metadata cluster, right after the
  // timestamp (the last line's tail), the one spot that never shoves the title — and in-flow, so it
  // cannot overlap the floated Clear. It hides with VISIBILITY, so its slot is reserved whether or
  // not the pointer is over the card (the user 2026-07-31): round 4's display:none made an off bell
  // cost zero space, but then hovering materialized it into that line and re-wrapped the title,
  // moving everything below it — layout must not shift because the mouse moved. Session-level arming
  // shows on the lane/tab instead, so an armed SESSION doesn't stamp every card.
  const bellBtn = el("button", "fask-bellbtn");
  bellBtn.onclick = (ev: Event) => {
    ev.stopPropagation();                            // never open the modal under the toggle
    const cur = (card as any)._it as AskItem | undefined;   // freshest payload copy, not this closure
    const live = cur || it;
    setCardNotify(card, live, !cardNotifyOn(live));
  };
  row1.append(bellBtn);   // inline after the time — the metadata cluster's tail
  row1.append(btns);      // the action corner floats from the END of the flow, so title+time keep first claim
  // ROW 3 — Background (left) · Summary (right), always one line, opposite sides. Populated below, once the
  // toggle buttons exist (they're declared with the distiller sections). The time now trails the title (row1).
  const row3 = el("div", "fask-row3");
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
  // TWO collapsible sections since 2026-07-02 (the user: "I come back after a long time and forget the
  // context"): BACKGROUND (re-orientation, collapsed by default) above the takeaway (expanded by default),
  // each toggled by a small +/− button. Collapse state lives in module sets keyed by itemId so the keyed
  // incremental re-render never snaps a section shut.
  // Collapsed sections are REAL buttons — "background" / "summary" in the Clear button's chrome — and
  // sit SIDE BY SIDE on one row when both are collapsed (the user 2026-07-02, round 5). The whole thing
  // is ONE flex-wrap container: the full-width bodies force their own lines, lone buttons share a row.
  // Open, the body runs the full card width and its collapse control is a block "less" button on its own
  // line under the text, always bottom-left (the old trailing-inline placement wrapped unpredictably).
  // TOGGLE buttons (the user 2026-07-02, round 7 — replaces the separate less control): the Background /
  // Summary buttons are always-visible press toggles, capitalized like Clear. Both collapsed → the two buttons
  // sit side by side on one row. Expanding background splits them apart — each button heads its own
  // content — with one clear line between the background part and the summary part; expanding only the
  // summary keeps the buttons side by side with its text below. A pressed toggle wears .on (bright +
  // filled) so what's showing is visible at a glance; clicking again collapses.
  const secs = el("div", "fask-secs"); secs.style.display = "none";
  const bgBtn = el("button", "fask-secbtn"); bgBtn.textContent = "Background";
  const bgBody = el("div", "fask-bg-body");
  const takeBtn = el("button", "fask-secbtn"); takeBtn.textContent = "Summary";
  const distill = el("div", "fask-distill");
  // "Sub-goals" — the THIRD mutually-exclusive section (the user 2026-07-08, moved off the footer): shows/hides
  // the inline sub-goal tree (the checklist below). Sits right of Summary; hidden when the card has no
  // sub-goals. Wired in applySections alongside Background/Summary (one open at a time, or none).
  const subBtn = el("button", "fask-secbtn"); subBtn.textContent = "Sub-goals"; subBtn.style.display = "none";
  // "Stalled" — the FIFTH mutually-exclusive section (the user 2026-07-23): romp is holding this card and
  // nothing is moving it. Same press-toggle interaction as Background/Summary, but it keeps the WORKING
  // colour in both states (see .fask-stallbtn) so it still draws the eye while open — the one section whose
  // point is that something is wrong. Filled in applySections.
  const stallBtn = el("button", "fask-secbtn fask-stallbtn"); stallBtn.textContent = "Stalled"; stallBtn.style.display = "none";
  const stallBody = el("div", "fask-stall-body");
  // "Awaiting task" — the FOURTH mutually-exclusive section (the user 2026-07-13): a compact pill (with
  // the mini spinning swirl inside) that replaces the old boxed awaiting caption when live bg TASKS exist;
  // click expands the task list in the checklist spot, same interaction as Sub-goals. Filled in applySections.
  const taskBtn = el("button", "fask-secbtn fask-taskbtn"); taskBtn.style.display = "none";
  const taskGlyph = el("span", "fask-awaiting-swirl"); taskGlyph.setAttribute("aria-hidden", "true");
  const taskLbl = el("span", "fask-taskbtn-lbl");
  taskBtn.append(taskGlyph, taskLbl);
  secs.append(bgBody, distill, stallBody);   // the BODIES only; the toggles ride row3 (below), one body shows at a time
  // now that the toggles exist, populate row3: Background · Summary · Sub-goals · Waiting-on-task — GROUPED
  // left, wrapping together as a block (the user 2026-07-08). Retry/Revive (rare) trail on the right (actions).
  row3.append(bgBtn, takeBtn, stallBtn, subBtn, taskBtn, actions);
  // ⏳ AWAITING cue (the user 2026-06-29): a small romp swirl spinning in the SAME body spot the distiller line
  // will eventually fill — a completed/blocked card shows its takeaway there; a WORKING card that's awaiting
  // dispatched/delegated work shows the spinning swirl instead, a glanceable "in flight, not stalled" sign.
  // The "why" rides beside it (it was tooltip-only on the ⏳ badge). Shown only while awaiting; see updateAskCard.
  const awaitSpin = el("div", "fask-awaiting"); awaitSpin.style.display = "none";
  const awaitGlyph = el("span", "fask-awaiting-swirl"); awaitGlyph.setAttribute("aria-hidden", "true");
  const awaitWhy = el("span", "fask-awaiting-why");
  awaitSpin.append(awaitGlyph, awaitWhy);
  // QUARANTINE body: a held message from a DIRECTED peer, shown IN FULL, read-only (peer content, never
  // auto-run; the human is deciding on it, so clipping it works against the decision — the user
  // 2026-07-26). Editing happens in the Edit modal, never inline. Only on a quarantine card.
  const qbody = el("div", "fask-qbody");
  qbody.style.display = "none";
  main.append(row1, row2, row3, secs, qbody, awaitSpin, checklist, delegations);   // no expand button — body click opens the modal
  card.append(main);
  // Follow-up lives in the modal now (the user 2026-06-10), not on the card.

  // title → locate the turn the card stands for. A normal card anchors on "prompt" (the originating
  // user message). A DELEGATION card (it.origin) has NO originating user prompt — it was planted by a
  // peer's postal message — so "prompt" lands on whatever user turn is nearest in time (an unrelated
  // message — the user hit this). For origin cards anchor on "work" instead, landing where the
  // delegation was processed, mirroring the modal tree-node nav (rompinfra, the user 2026-06-16).
  // agent → open session; Clear → inbox-zero. stopPropagation so the card-body handlers don't also fire.
  let titleAnchor = it.origin ? "work" : "prompt";
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
  let titleUuid = titleAnchor === "prompt" ? (rootNode?.promptAnchorUuid ?? null) : cardAnchorUuid;
  // No minting user message — an autonomous NOTE the agent wrote itself (no opener), or the opener got
  // compacted off the active path — so a "prompt" jump has nothing to land on and used to honest-fail with
  // the "couldn't locate this in the transcript" toast. Fall back to WHERE THE NOTE WAS WRITTEN: the work
  // turn (an assistant turn). That needs anchor "work" so the chat's kind guard accepts a non-user uuid
  // (a "prompt" intent refuses an assistant turn). (the user 2026-06-30.)
  if (titleAnchor === "prompt" && !titleUuid && cardAnchorUuid) { titleAnchor = "work"; titleUuid = cardAnchorUuid; }
  // A PROVISIONAL placeholder has no goal node / timeline anchor — clicking anywhere just opens the live
  // session (go see what it's working on); the modal, timeline deep-link, and path-hover are all skipped.
  title.onclick = (ev) => { ev.stopPropagation(); if (it.provisional) { openOrReviveSession(it.sid, it.live, it.name); return; } focusEcho(it.sid); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: it.itemId, sid: it.sid, t: it.t, anchor: titleAnchor, anchorUuid: titleUuid }); };
  // (The auto-line is plain text now — no deep-link — so no onclick here; its hover tooltip = the planner's
  // why, set in updateAskCard. The inline sub-goal checkmarks remain clickable via wireNodeZones.)
  name.onclick = (ev) => { ev.stopPropagation(); openOrReviveSession(it.sid, it.live, it.name); };
  clr.onclick = (ev) => {
    ev.stopPropagation();
    // Flush this card's cross-surface hover highlight NOW: clearing removes the card, so its own mouseleave
    // never fires and the timeline/chat highlight stuck until you moved the mouse (the user 2026-07-03). The
    // synthetic mouseleave runs the exact leave logic (clears the highlight, or restores a pinned card's).
    card.dispatchEvent(new MouseEvent("mouseleave"));
    dressHeaderIfLast(card, it.sid);   // the run's last card takes its header with it — one motion (2026-08-24)
    pendingCleared.add(it.itemId);   // suppress from incoming pushes until the kernel confirms the clear
    clearedStack.push([it]);         // cache for an instant optimistic Undo
    card.classList.add("dismissing");
    vscodeApi?.postMessage({ type: "askClear", itemId: it.itemId, sid: it.sid });
    setTimeout(() => { if (askEls.get(it.itemId) === card && card.classList.contains("dismissing")) { card.remove(); askEls.delete(it.itemId); dropDismissed([it.itemId]); } }, 180);
  };
  cont.onclick = (ev) => {
    ev.stopPropagation();
    // the kernel supplies the canned body (CONTINUE_TEXT) — the client sends only the gesture, so the
    // copy has one voice-tested home. Ack INSTANTLY (disable + relabel, the click-safety rule), then the
    // same optimistic move a typed reply gets; updateAskCard re-arms the label once the judge has ruled.
    vscodeApi?.postMessage({ type: "askFollowUp", itemId: it.itemId, sid: it.sid, cont: true });
    cont.disabled = true; cont.textContent = "Sent";
    cont.title = contTitle(true, "a continue", null);
    optimisticFollowMove(it.itemId);
    render();
  };
  // HOVER (120ms intent debounce so sweeps don't spam) → white border + preview
  // this card's timeline journey. LEAVE → restore the pinned card's journey, or
  // clear if none pinned.
  let hoverTimer: number | undefined;
  card.addEventListener("mouseenter", () => {
    freezeEnter(it.itemId);                            // hover-freeze: pointer truth, no debounce
    if (it.provisional) return;                        // no timeline path for a placeholder
    hoverTimer = window.setTimeout(() => {
      hoverTimer = undefined;
      hoverAskId = it.itemId; applyFocus();
      vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, sid: it.sid, locate: false });
    }, 120);
  });
  card.addEventListener("mouseleave", () => {
    freezeLeave(it.itemId);                            // hover-freeze: leaving the card flushes queued payloads
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = undefined; }
    if (hoverAskId === it.itemId) {
      hoverAskId = null; applyFocus();
      const pin = focusAnchorId(pinnedAskId);
      if (pin) vscodeApi?.postMessage({ type: "showAskPath", itemId: pin, sid: sidOfItem(pin), locate: false });   // back to the pin (ask or group)
      else vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, sid: it.sid, off: true });      // clear
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
      vscodeApi?.postMessage({ type: "cardOpened", itemId: it.itemId, sid: it.sid });   // the open-metric row (2026-08-25)
      vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, sid: it.sid, locate: false });
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
    if (pinnedAskId === it.itemId) vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, sid: it.sid, locate: false, jump: true });
    else if (!pinnedAskId && hoverAskId !== it.itemId) vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, sid: it.sid, off: true });
  });

  // right-click → the per-card bell menu (the user 2026-07-28). Reads the FRESH item off the card
  // (a._it, restashed every updateAskCard) — the closure's `it` goes stale after the first push.
  card.addEventListener("contextmenu", (ev) => {
    if (it.provisional) return;                    // a placeholder has no stable identity to arm
    ev.preventDefault(); ev.stopPropagation();
    showCardMenu(ev, card);
  });

  const a = card as any;
  a._title = title; a._name = name; a._time = time; a._followedup = fupBadge;
  a._bell = bellBtn;
  a._row1 = row1; a._row2 = row2;   // grouped mode re-homes Clear between these (the user 2026-07-13)
  a._doneConfirming = dcBadge;
  a._nudgeFailed = nfBadge;
  a._interrupting = intingBadge;
  a._interrupted = intBadge;
  a._warnChip = warnChip;
  a._waitOn = waitOnBadge;
  a._blocked = blkBadge;
  a._apiBadge = apiBadge; a._apiRetry = apiRetry; a._apiLogin = apiLogin; a._retryBadge = retryBadge; a._revive = revive; a._clr = clr;
  a._capLine = capLine; a._capBtn = capBtn;
  a._jauthBadge = jauthBadge;
  a._cont = cont;
  a._qApprove = qApprove; a._qDeny = qDeny; a._qBody = qbody;
  a._delegations = delegations;
  a._checklist = checklist;
  a._distill = distill;
  a._secs = secs; a._bgBtn = bgBtn; a._bgBody = bgBody; a._takeBtn = takeBtn; a._subBtn = subBtn;
  a._stallBtn = stallBtn; a._stallBody = stallBody;
  a._taskBtn = taskBtn; a._taskLbl = taskLbl;
  a._awaitSpin = awaitSpin; a._awaitWhy = awaitWhy;
  a._origin = origin;
  return card;
}

// Which card section a card shows — the three sections are MUTUALLY EXCLUSIVE (the user 2026-07-08, was
// bg/summary only): "bg" | "summary" | "subgoals" | "none". At most ONE open at a time, or none. Keyed by
// itemId in a module map so the keyed incremental re-render never snaps a section shut. Absent = the DEFAULT,
// which the footer "Collapsed" toggle sets: OFF → "summary" open (a completed card's takeaway shows); ON →
// "none" (collapsed), so NEW cards arrive collapsed too. Clicking a toggle sets an explicit per-card override
// (click the open one → off; click another → switch) that survives the mode.
const secChoice = new Map<string, "bg" | "summary" | "subgoals" | "tasks" | "stall" | "none">();
function resolveSec(id: string, hasAwaitTasks = false): "bg" | "summary" | "subgoals" | "tasks" | "stall" | "none" {
  // an awaiting-on-tasks card OPENS its task list by default (the user 2026-08-23: the wait is the
  // one thing to read on that card); an explicit user pick and collapsed mode still win
  return secChoice.get(id) ?? (feedPrefs().collapsed ? "none" : hasAwaitTasks ? "tasks" : "summary");
}
// The Stalled body's text: the staller's plain-language note when the judge has written one, else the
// kernel's own mechanical reason. Never a waiting-on-the-judge placeholder — a stalled card always has
// something true to say about why it is stuck, because the kernel knew the reason before the judge was
// ever asked. That is the whole point of grounding this surface in the mechanical why.
function stallText(st: { why: string; note?: string | null } | null | undefined): string {
  if (!st) return "";
  const note = (st.note || "").trim();
  return note || ("Nothing is moving this: romp is waiting on " + st.why + ".");
}
// Per-node EXPAND state for a CARD's inline sub-goal tree, keyed "itemId:nodeId" (the user 2026-07-08, who referred to the
// little triangle-y icons from the outline view). A node is COLLAPSED by default; membership here means the
// user clicked its triangle open. So the tree opens showing only the top level and expands on demand — like
// the modal's one-level view. Empty default = everything collapsed. (Its OWN state, not the modal's
// `collapsedNodes`, which uses the inverse sense + its own seeding.)
const cardTreeExpanded = new Set<string>();

// ── disclosure state across a reload (the user 2026-07-24) ────────────────────────────────────────────
// A kernel restart reloads this page, which used to wipe every section you had opened. The five collections
// above are exactly the state worth carrying: what the USER chose to open. Everything else module-level here
// is a DOM cache or an in-flight optimistic record, and restoring those would resurrect predictions made
// against a kernel that no longer exists — see feed-view-state.ts.
// Column-layout state (the user 2026-08-16; drag extended to BOTH layouts 2026-08-24): which
// categories are folded to their header, and the dragged column order — ONE order, two renderings
// (the user thinks of the two layouts as one arrangement): a drag in either layout re-sequences both. Layout
// state, not card state — prune-exempt, persisted with the rest (the key stays `order`, so an
// arrangement dragged before the merge survives it).
const collapsedCols = new Set<string>();
let colOrder: string[] = [];                         // [] = each layout's own CSS default

(function hydrateViewState() {
  let st;
  try { st = parseViewState(localStorage.getItem(VIEW_STATE_KEY)); } catch { return; }   // private mode / blocked storage → run without it
  for (const [k, v] of Object.entries(st.sec)) secChoice.set(k, v as ReturnType<typeof resolveSec>);
  for (const k of st.tree) cardTreeExpanded.add(k);
  for (const k of st.nodes) collapsedNodes.add(k);
  for (const k of st.logs) nodeLogOpen.add(k);
  for (const k of st.asks) expandedAsks.add(k);
  for (const k of st.threads) collapsedThreads.add(k);
  for (const k of st.cols) collapsedCols.add(k);
  colOrder = st.order.slice();
})();

function currentViewState(): FeedViewState {
  const sec: Record<string, string> = {};
  secChoice.forEach((v, k) => { sec[k] = v; });
  return { v: 1, sec, tree: [...cardTreeExpanded], nodes: [...collapsedNodes], logs: [...nodeLogOpen],
           asks: [...expandedAsks], threads: [...collapsedThreads], cols: [...collapsedCols],
           order: colOrder.slice() };
}

// Written at the END of every render rather than from each toggle handler: the feed re-renders on every
// interaction anyway, so this cannot miss a mutation site as hand-placed save() calls would. render() also
// runs on every kernel push, so the write is gated on the serialised value actually CHANGING — that makes it
// effectively event-driven on real state change, not a per-push localStorage write.
let lastViewWrite = "";
function persistViewState(): void {
  const json = serializeViewState(capViewState(currentViewState()));
  if (json === lastViewWrite) return;
  lastViewWrite = json;
  try { localStorage.setItem(VIEW_STATE_KEY, json); } catch { /* quota / private mode → the feed still works */ }
}

// Self-clean, event-based: the kernel's payload names the full live card set, so anything whose card is gone
// (cleared, archived) is dropped now. `live` must be the UNFILTERED payload — see pruneViewState.
function pruneViewStateTo(live: Set<string>): void {
  const kept = pruneViewState(currentViewState(), live);
  const keep = (s: Set<string>, xs: string[]) => { s.clear(); for (const k of xs) s.add(k); };
  secChoice.clear();
  for (const [k, v] of Object.entries(kept.sec)) secChoice.set(k, v as ReturnType<typeof resolveSec>);
  keep(cardTreeExpanded, kept.tree);
  keep(collapsedNodes, kept.nodes);
  keep(nodeLogOpen, kept.logs);
  keep(expandedAsks, kept.asks);
  keep(collapsedThreads, kept.threads);   // pass-through: a thread stays collapsed with no cards on the board
}

// Fill + wire the card's THREE mutually-exclusive sections — Background, Summary, Sub-goals (the user
// 2026-07-08). At most ONE open at a time (or none): clicking the open one closes it, clicking another
// switches. Each button shows only when it has content to reveal — bg present / a produced takeaway/brief /
// the goal has sub-goals — so an unavailable choice falls back to "none". The bg/summary BODIES live in
// `_secs`; the sub-goal TREE lives in `_checklist` below. stopPropagation on every toggle — the card-body
// click opens the modal.
function applySections(a: any, it: AskItem, distillShown: boolean): void {
  const id = it.itemId;
  const bg = distillShown && it.background ? it.background : null;
  // does the card have a sub-goal tree to show? (the root has a non-handoff child — handoffs live in the
  // delegations section). byId/root are reused by the tree builder below.
  const tree = it.tree || [];
  const byId = new Map(tree.map((n) => [n.id, n] as const));
  const root = tree.find((n) => n.id === it.itemId) || tree[0];
  // DIRECT sub-goals only — one level below (the user 2026-07-15): the button reads "3 sub-goals" for the
  // goal's immediate children, matching what the tree first shows when opened; deeper levels aren't folded
  // into this headline number — the user drills into them by expanding a child's ▶ triangle. Distinct
  // non-handoff direct children, deduped once. (Was the whole-subtree count, every depth.)
  let subCount = 0;
  if (root) {
    const seenC = new Set<string>([root.id]);
    for (const cid of (root.children || [])) {
      if (seenC.has(cid)) continue;
      seenC.add(cid);
      const n = byId.get(cid);
      if (!n || n.kind === "handoff") continue;
      subCount++;
    }
  }
  const hasSubs = subCount > 0;
  // live background tasks (the user 2026-07-13): when the card is AWAITING on tasks, the compact
  // "Awaiting task" pill joins the section toggles and expands this list (the old boxed caption is gone)
  const taskList = ((it.awaiting && it.awaiting.tasks) || []).filter(Boolean);
  const hasTasks = taskList.length > 0;
  // resolve the selection (default = summary open), falling back to "none" if the chosen section is empty
  // the stall note (the user 2026-07-23) — shown whenever the kernel says romp is holding this card, with
  // or without a judge-written note, since `why` alone already answers "why is nothing happening"
  const stall = it.stalled && it.stalled.why ? it.stalled : null;
  let choice = resolveSec(id, hasTasks);
  if (choice === "bg" && !bg) choice = "none";
  if (choice === "summary" && !distillShown) choice = "none";
  if (choice === "subgoals" && !hasSubs) choice = "none";
  if (choice === "tasks" && !hasTasks) choice = "none";
  if (choice === "stall" && !stall) choice = "none";
  const pick = (want: "bg" | "summary" | "subgoals" | "tasks" | "stall") => (ev: Event) => {
    ev.stopPropagation();
    secChoice.set(id, choice === want ? "none" : want);   // click the showing one → off; else switch to it
    applySections(a, it, distillShown);
  };
  // Background toggle — visible only when there IS background; pressed (.on) when its body is showing
  a._bgBtn.style.display = bg ? "" : "none";
  a._bgBtn.classList.toggle("on", choice === "bg");
  a._bgBtn.setAttribute("aria-pressed", choice === "bg" ? "true" : "false");
  a._bgBtn.title = choice === "bg" ? "hide the background" : "show the background";
  a._bgBody.style.display = choice === "bg" ? "" : "none";
  if (choice === "bg") a._bgBody.textContent = bg as string;
  a._bgBtn.onclick = pick("bg");
  // Summary toggle
  a._takeBtn.style.display = distillShown ? "" : "none";
  a._takeBtn.classList.toggle("on", choice === "summary");
  a._takeBtn.setAttribute("aria-pressed", choice === "summary" ? "true" : "false");
  a._takeBtn.title = choice === "summary" ? "hide the summary" : "show the summary";
  (a._distill as HTMLElement).style.display = choice === "summary" ? "" : "none";
  a._takeBtn.onclick = pick("summary");
  // Stalled toggle — same press-toggle as the others; its colour is the difference (see .fask-stallbtn)
  a._stallBtn.style.display = stall ? "" : "none";
  a._stallBtn.classList.toggle("on", choice === "stall");
  a._stallBtn.setAttribute("aria-pressed", choice === "stall" ? "true" : "false");
  a._stallBtn.title = stall
    ? (choice === "stall" ? "hide why this is stalled" : "romp is holding this — show why")
    : "";
  a._stallBody.style.display = choice === "stall" ? "" : "none";
  if (choice === "stall") a._stallBody.textContent = stallText(stall);
  a._stallBtn.onclick = pick("stall");
  // Sub-goals toggle — visible only when the goal HAS sub-goals; pressed when the tree is showing
  const subBtn = a._subBtn as HTMLElement;
  subBtn.style.display = hasSubs ? "" : "none";
  subBtn.textContent = subCount === 1 ? "1 sub-goal" : subCount + " sub-goals";
  // dim " · N parked" suffix (the user 2026-08-24): the card-level gist of the row tags. Counts ONLY
  // rows the checklist this button toggles can actually reach — the same walk, stopping at handoff
  // nodes (delegations render in their own section) and at the root (the card head, not a row) — so
  // the suffix never advertises rows no expansion reveals (review 2026-08-24; a parked ask under a
  // LIVE delegation is the modal tree's to show).
  let parkedCount = 0;
  if (root) {
    const pseen = new Set<string>([root.id]);
    const pwalk = (nid: string) => {
      const n = byId.get(nid);
      if (!n || n.kind === "handoff" || pseen.has(n.id)) return;
      pseen.add(n.id);
      if (n.parked && n.parked.n) parkedCount++;
      for (const c of n.children || []) pwalk(c);
    };
    for (const c of (root.children || [])) pwalk(c);
  }
  if (hasSubs && parkedCount) {
    const pk = el("span", "fask-subparked");
    pk.textContent = " · " + parkedCount + " parked";
    subBtn.appendChild(pk);
  }
  subBtn.classList.toggle("on", choice === "subgoals");
  subBtn.setAttribute("aria-pressed", choice === "subgoals" ? "true" : "false");
  subBtn.title = choice === "subgoals" ? "hide the sub-goals" : "show the sub-goals";
  subBtn.onclick = pick("subgoals");
  // "Awaiting task" pill (the user 2026-07-13) — visible only while live bg tasks exist; the mini swirl
  // inside keeps the "in flight" cue; pressed when the task list is showing. No preachy tooltip.
  // "Awaiting", not "Waiting on": the chat chip and timeline badge already label this exact state
  // Awaiting, and two words for one state read as two states (the user 2026-08-13).
  const taskBtn = a._taskBtn as HTMLElement;
  taskBtn.style.display = hasTasks ? "" : "none";
  // the KIND words the pill (the user 2026-08-15): "Awaiting job", "Awaiting 3 agents" — the wait's
  // class in the visible label (tooltips are dead on the touch PWA); kindless keeps the classic "task"
  const awKind = (it.awaiting && it.awaiting.kind) || "";
  const kw = KIND_WORD[awKind] || "task";
  // the wait's elapsed time rides the pill exactly as it rides the awaiting box and the working
  // narration — a stuck wait must be glanceable everywhere the state shows (the user 2026-08-23)
  const pillWaited = waitedSuffix(it.awaiting && it.awaiting.since, Date.now() / 1000);
  (a._taskLbl as HTMLElement).textContent =
    (taskList.length === 1 ? "Awaiting " + (awKind ? kindWord(awKind, 1) : kw)
                           : "Awaiting " + taskList.length + " " + (awKind ? kindWord(awKind, taskList.length) : kw + "s")) + pillWaited;   // one number-agreeing vocabulary (T225)
  taskBtn.classList.toggle("on", choice === "tasks");
  taskBtn.setAttribute("aria-pressed", choice === "tasks" ? "true" : "false");
  taskBtn.title = choice === "tasks" ? "hide the tasks" : "show the tasks";
  taskBtn.onclick = pick("tasks");
  // the bg/summary/stall BODIES container shows only when one of those is open (the tree is a separate
  // element). "stall" MUST be here: stallBody lives inside _secs, so without it the Stalled toggle pressed
  // .on while its body stayed inside a display:none parent — the button "selected but nothing happened"
  // (the user 2026-07-23, the very first click on the day-old section).
  a._secs.style.display = (choice === "bg" || choice === "summary" || choice === "stall") ? "" : "none";
  // the inline sub-goal TREE (in _checklist), shown only when choice === "subgoals". Whole subtree, indented
  // by depth, with the outline's ▶/▼ disclosure triangles to fold branches (the user 2026-07-08). Same
  // inclusion rules as the modal's renderTreeNode: skip handoffs, a node reached under two parents renders
  // ONCE (dim ".repeat", not re-descended). renderTree() re-runs itself on a triangle toggle (collapse state
  // changed) without touching the buttons.
  const cl = a._checklist as HTMLElement;
  const renderTree = () => {
    cl.innerHTML = "";
    // the TASK list (the user 2026-07-13): same view/spot as the sub-goal checklist — one row per live
    // background task, a small spinning swirl as its mark (in flight), the task's own description as text
    if (choice === "tasks") {
      for (const d of taskList) {
        const row = el("div", "fcheck ftask");
        const tri = el("span", "fcheck-tri empty");
        const mark = el("span", "fcheck-mark");
        mark.appendChild(el("span", "fask-awaiting-swirl ftask-swirl"));
        const txt = el("span", "fcheck-text"); txt.textContent = d;
        row.append(tri, mark, txt);
        cl.appendChild(row);
      }
      cl.style.display = cl.children.length ? "" : "none";
      return;
    }
    if (choice !== "subgoals" || !root) { cl.style.display = "none"; return; }
    const rows: { node: AskTreeNode; depth: number; repeat: boolean; expandable: boolean; collapsed: boolean }[] = [];
    const seen = new Set<string>([root.id]);   // a child linking back to the root counts as a repeat (as the modal)
    const walk = (nid: string, depth: number) => {
      const n = byId.get(nid);
      if (!n || n.kind === "handoff") return;   // delegations render in their own section, not the checklist
      const repeat = seen.has(n.id);
      const expandable = !repeat && (n.children || []).some((c) => { const cn = byId.get(c); return !!cn && cn.kind !== "handoff"; });
      // DEFAULT COLLAPSED (the user 2026-07-08): the tree opens showing only the top level; a branch is
      // expanded only once its triangle was clicked (in cardTreeExpanded), just like the modal's one-level view.
      const collapsed = expandable && !cardTreeExpanded.has(id + ":" + n.id);
      rows.push({ node: n, depth, repeat, expandable, collapsed });
      if (repeat || collapsed) return;           // a repeat is dim + NOT re-descended; a collapsed branch is hidden
      seen.add(n.id);
      for (const c of n.children || []) walk(c, depth + 1);
    };
    // REVIEWED-EARLIER fold (the user 2026-08-19): direct children whose outcomes the user already
    // reviewed (kernel reviewedEarlier, from the SAME boundary the distiller scopes the takeaway with)
    // collapse behind one row, so a re-completed card presents only the new work — the old material is
    // one click away, never gone. Fresh rows first; the fold row sits below them.
    const revKids = (root.children || []).filter((c) => !!byId.get(c)?.reviewedEarlier);
    const freshKids = (root.children || []).filter((c) => !byId.get(c)?.reviewedEarlier);
    const revOpen = cardTreeExpanded.has(id + ":reviewed");
    for (const c of freshKids) walk(c, 0);
    const freshEnd = rows.length;
    if (revOpen) for (const c of revKids) walk(c, 0);
    const paintRow = ({ node: s, depth, repeat, expandable, collapsed }: typeof rows[number]) => {
      const row = el("div", "fcheck " + nodeStatusClass(s) + (s.auth ? " auth-" + s.auth : "") + (repeat ? " repeat" : ""));
      if (depth) row.style.paddingLeft = (depth * TREE_INDENT_EM) + "em";   // same per-level indent as the modal outline
      // disclosure triangle: ▶ collapsed / ▼ expanded; a non-expandable node gets a blank same-width spacer so
      // marks stay aligned. Only the triangle toggles (stopPropagation so the row click still opens the modal).
      const tri = el("span", "fcheck-tri" + (expandable ? " nav" : " empty"));
      tri.textContent = expandable ? (collapsed ? "▶" : "▼") : "";
      if (expandable) tri.onclick = (ev: Event) => {
        ev.stopPropagation();
        const k = id + ":" + s.id;
        if (cardTreeExpanded.has(k)) cardTreeExpanded.delete(k); else cardTreeExpanded.add(k);
        renderTree();
      };
      const mark = el("span", "fcheck-mark");
      // ✓ blue disc (done) / ⏸ red pause (question = blocked) / empty ring (not done) — the SAME notation as the
      // ledger checklist + Fleet (the user 2026-06-24). The OPEN mark is an empty element the CSS draws as a
      // 13px hollow circle matching the done disc's size (the user 2026-07-08: the ○ glyph read too small);
      // AUTHORITATIVE keeps the glyph, .auth-* only rings it. Blocked ROLLS UP (kernel flatten, the user
      // 2026-07-11): an ancestor of a blocked sub wears the ⏸ too, so the block is visible even while the
      // branch is collapsed — its tooltip points DOWN to the real ask.
      mark.textContent = s.status === "done" ? "✓" : s.status === "question" ? "⏸" : "";
      if (s.status === "question") mark.title = s.qderived ? "a sub-goal inside is blocked — expand to find it" : "blocked — needs you";
      const txt = el("span", "fcheck-text"); txt.textContent = s.text;
      row.append(tri, mark, txt);
      if (s.cleared) row.appendChild(clearedTag());   // the strike alone doesn't say WHY — see CLEARED_TIP
      if (s.parked && s.parked.n && !s.cleared) row.appendChild(parkedTag(s.parked.n));   // leapfrogged — see parkedTag
      // clicks match the modal tree node exactly (text → the message, checkbox → where it resolved) via the
      // SAME wireNodeZones; a dim repeat is display-only (wire=false).
      wireNodeZones(it, s, mark, txt, null, !repeat);
      cl.appendChild(row);
    };
    rows.slice(0, freshEnd).forEach(paintRow);
    if (revKids.length) {
      // the fold row: same gesture grammar as a branch triangle — click toggles, state survives
      // re-renders via cardTreeExpanded (keyed per card), and the label carries the count
      const row = el("div", "fcheck freviewed" + (revOpen ? " open" : ""));
      const tri = el("span", "fcheck-tri nav"); tri.textContent = revOpen ? "▼" : "▶";
      const mark = el("span", "fcheck-mark"); mark.textContent = "✓";
      const txt = el("span", "fcheck-text");
      txt.textContent = revKids.length + " reviewed earlier";
      row.title = "sub-goals you reviewed before your follow-up — the update above doesn't re-present them";
      row.onclick = (ev: Event) => {
        ev.stopPropagation();
        const k = id + ":reviewed";
        if (cardTreeExpanded.has(k)) cardTreeExpanded.delete(k); else cardTreeExpanded.add(k);
        renderTree();
      };
      row.append(tri, mark, txt);
      cl.appendChild(row);
    }
    rows.slice(freshEnd).forEach(paintRow);
    cl.style.display = cl.children.length ? "" : "none";
  };
  renderTree();
}

// The payload copy a card was last painted from, serialized — a card whose data did not change since is not
// repainted. Every feed frame used to rewrite all ~155 cards (about a hundred DOM writes each) when the frame
// changed one of them (2026-09-04). The display-side state a paint also reads (hover/pin focus, the pending
// bell, the done ticks) is folded into the key, so a change to any of it repaints as before.
// Bumped whenever a display-side input EVERY card reads changes: the view prefs (grouped, collapsed), the
// working/awaiting/unknown status sets and the self host that the session dots and delegation lines read.
// A card's key carries it, so such a change repaints every card once, as before. The coarse clock repaints
// each card at most every 15 s so the durations it renders (waited, working for, paragraph ages) keep
// ticking — the old cadence was every kernel push, at most 60 s apart.
let paintEpoch = 0;
let statusSig = "";
function noteStatusInputs(): void {
  const sig = [...workingSet].sort().join(",") + "|" + [...awaitingSet].sort().join(",") + "|" + [...unknownSet].sort().join(",") + "|" + feedSelfHost;
  if (sig !== statusSig) { statusSig = sig; paintEpoch++; }
}
function cardPaintKey(it: AskItem): string {
  return JSON.stringify(it) + "|" + (it.itemId === (hoverAskId ?? pinnedAskId) ? "f" : "") + (it.itemId === pinnedAskId ? "p" : "")
    + "|" + (pendingNotify.has(it.itemId) ? String(pendingNotify.get(it.itemId)) : "") + "|" + [...pendingDone].join(",")
    + "|" + paintEpoch + "|" + Math.floor(Date.now() / 15000);
}

function updateAskCard(card: HTMLElement, it: AskItem) {
  const a = card as any;
  a._it = it;   // the freshest payload copy — the right-click bell menu reads this, never a stale closure
  const pk = cardPaintKey(it);
  // Nothing this card shows has changed → leave its DOM alone. Except a card with a LATCHED button (Approve →
  // Delivering…, Retry → Retrying…, Revive → Reviving…): those rely on the next paint to re-enable when the
  // refused action left the payload unchanged, so a card with a disabled button always repaints.
  if (a._paintKey === pk && !card.querySelector("button[disabled]")) return;
  a._paintKey = pk;
  // per-card bell: retire the optimistic value once the kernel's payload agrees (event-based, no timer),
  // then render whichever stands. Same sticky-optimism shape as the timeline lane's _pendingFlags.
  if (pendingNotify.has(it.itemId) && !!it.notify === pendingNotify.get(it.itemId)) pendingNotify.delete(it.itemId);
  if (a._bell) (a._bell as HTMLElement).style.display = it.provisional ? "none" : "";   // no stable identity to arm
  paintCardBell(card, cardNotifyOn(it));
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
    // outline the card in ITS session's identity colour (the user 2026-07-15) — CSS paints it at 0.5α at rest
    // and bolds the SAME colour on hover/pin. Fall back to the recency-tint channels for the rare colourless
    // session so the border never voids to transparent.
    setCardChannels(card, (it.color && hexToRgb(it.color.bg)) || [r, g, b]);
  }
  // a re-check card dims slightly (between a normal card and a provisional ghost) so it reads as "handled, pending"
  if (!it.provisional) card.style.opacity = it.recheck ? ".8" : "";
  a._title.textContent = it.text;
  a._name.replaceChildren(...hostNameNodes(it.name, it.sid));   // remote "host:" prefix = quiet metadata
  if (it.color) a._name.style.color = it.color.bg;
  setWorkDot(a._name, dotFor(it.name));   // working/awaiting dot before the session name
  // ↪ courier handoff: planted by a peer's message → "↪ from <sender>", click opens the sender
  const og = a._origin as HTMLElement;
  if (it.origin && it.origin.peer) {
    og.style.display = "";
    // "↪ from" in dim gray (the Clear-button gray), the peer name in the bold session-name style next to
    // it — its own identity colour, like every other session name in this row (the user 2026-06-16).
    og.replaceChildren();
    og.style.color = "";
    const pre = el("span", "fask-origin-pre"); pre.textContent = "↪ from ";
    // A federated sender wears the same quiet "host:" prefix as remote session names on the
    // timeline/tabs (.host-prefix keeps its own dim color under the peer's identity color).
    const peer = el("span", "fask-origin-peer");
    peer.replaceChildren(...hostPartsNodes(it.origin.peerHost, it.origin.peer));
    if (it.origin.color) peer.style.color = it.origin.color.bg;
    og.append(pre, peer);
    // absorbed (the sender's linked entry closed — usually because THIS card completed and the
    // link-back checked it off): same badge, dimmed — provenance, not an active handoff. The title
    // also warns that a clear takes the linked entry with it (the user 2026-08-16, who watched that
    // happen with no visible cause).
    og.classList.toggle("fask-origin-absorbed", it.origin.live === false);
    og.title = (it.origin.live === false
      ? "delegated by " + it.origin.peer + "; their linked entry closed with this card"
      : "delegated by " + it.origin.peer + " — clearing this card also clears their linked entry")
      + " · click opens the session";
    og.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: it.origin!.peerSid }); };
  } else {
    og.style.display = "none";
  }
  // ↪ sender-side handoff provenance (the user 2026-08-24): a TOP-LEVEL "↪ delegated to <peer>"
  // tracking node wore its provenance as the card TITLE, arrow and all. The kernel now titles the
  // card with the WORK and ships the delegation here — the mirror of ↪ from above: identity color,
  // quiet host: prefix for a federated recipient, click opens the recipient session. STACKS after
  // an ↪ from badge rather than replacing it (origin and this are different facts about one card —
  // the same rule the 2026-08-24 review pinned on this slot).
  if (it.handoffTo && it.handoffTo.peerSid) {
    const hadOrigin = !!(it.origin && it.origin.peer);
    og.style.display = "";
    if (!hadOrigin) {
      og.replaceChildren(); og.style.color = ""; og.classList.remove("fask-origin-absorbed");
      og.title = "delegated to " + it.handoffTo.peer + "; their result checks this card off · click opens the session";
      og.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: it.handoffTo!.peerSid }); };
    }
    const pre = el("span", "fask-origin-pre"); pre.textContent = (hadOrigin ? " · " : "") + "↪ delegated to ";
    const peer = el("span", "fask-origin-peer");
    peer.replaceChildren(...hostPartsNodes(it.handoffTo.peerHost, it.handoffTo.peer));
    if (it.handoffTo.color) peer.style.color = it.handoffTo.color.bg;
    peer.title = "delegated to " + it.handoffTo.peer + "; their result checks this card off · click opens the session";
    peer.style.cursor = "pointer";
    peer.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: it.handoffTo!.peerSid }); };
    og.append(pre, peer);
  }
  // tracked delegation PRIMARY (the user 2026-08-24): the ONE card, homed here under the delegator —
  // it names the recipient(s) in their identity colors with the board's own live dot
  // (working/awaiting/idle), so the manager reads the worker's state without leaving this card.
  // STACKS after an ↪ from badge rather than replacing it (review 2026-08-24: an else-if hid a
  // middleman's own tracked handoff — origin and delegTracked are different facts about one card);
  // each recipient span carries its own click, so the ↪ from click keeps opening the sender.
  if (it.delegTracked && it.delegTracked.length) {
    const hadOrigin = !!(it.origin && it.origin.peer);
    og.style.display = "";
    if (!hadOrigin) {
      og.replaceChildren(); og.style.color = ""; og.classList.remove("fask-origin-absorbed");
      og.title = "a tracked handoff: the work runs with " + it.delegTracked.map((d) => d.name).join(", ")
        + " and reports back to this card";
      og.onclick = null;
    }
    const pre = el("span", "fask-origin-pre"); pre.textContent = (hadOrigin ? " · " : "") + "↪ delegated to ";
    og.append(pre);
    it.delegTracked.forEach((d, i) => {
      if (i) og.append(", ");
      const peer = el("span", "fask-origin-peer");
      peer.replaceChildren(...hostPartsNodes(d.host, d.name));
      if (d.color && d.color.bg) peer.style.color = d.color.bg;
      setWorkDot(peer, dotFor(d.name));
      peer.title = "a tracked handoff: the work runs with " + d.name + " and reports back to this card · click opens the session";
      peer.style.cursor = "pointer";
      peer.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: d.sid }); };
      og.append(peer);
    });
  }
  a._time.textContent = relAge(hostNow - it.t);
  // hover the stamp for provenance (the user 2026-07-27): the age marks the NEWEST event (a done card's
  // age is its completion), so the popover tells where the thread came from — started when, each sub +
  // its time, what the stamp marks.
  wireAgeTip(a._time, () => provenanceRows(it, hostNow, PROV_FMT));
  // RE-CHECK chip (the user 2026-06-27): a soft-block you answered with a TARGETED follow-up (kernel `recheck`).
  // Reads "↩ re-judging" so you know it registered and isn't on you, pending the judge's verdict. (A PLAIN reply
  // is `rejudging`, not `recheck`, and gets no chip: since 2026-07-02 it ALSO moves to Working while the reply
  // is in flight, and its "Analyzing…" swirl carries the same message — the chip would just double it up. The
  // swirl is therefore the ONLY cue on a rejudging card, which is why it must never be suppressed.)
  // The plain "↻ Followed up" chip (followupPending → reopened to Working) was REMOVED (the user 2026-07-01):
  // click-to-cite makes following up routine, so acknowledging it on the card is now noise — the card just
  // silently returns to Working. (followupPending still drives that column move; only its badge is gone.)
  if (it.recheck) {
    a._followedup.style.display = "";
    a._followedup.textContent = "↩ re-judging";
    a._followedup.title = "you followed up — no longer waiting on you; the judge will resolve it or re-block it on the next pass";
  } else {
    a._followedup.style.display = "none";
  }
  // "done, confirming" — the done verdict is in, settle pending; the card stays put in Working with this
  // steady cue instead of moving columns early (the user 2026-07-24: an indicator, never a column flicker).
  (a._doneConfirming as HTMLElement).style.display = it.doneConfirming ? "" : "none";
  // "follow-up failed" chip (plans/stalled-open-todos-nudge.md): the one auto-nudge didn't resolve the
  // stall and it is never re-asked — the card says so. The failure also records a BLOCK verdict (2026-07-07),
  // so the card reaches Needs-you via the normal ladder; this chip rides along as the explanation.
  a._nudgeFailed.style.display = it.nudgeFailed ? "" : "none";
  // "interrupting…" — a stop is IN FLIGHT (the user 2026-07-07): steady from the click until the interrupt
  // settles, at which point the kernel drops `interrupting` and (if still quiet) sets `interrupted`. The
  // follow-up-failed chip still outranks it; the two interrupt badges are mutually exclusive by construction
  // (the kernel never sets both), and we hide the past-tense one while interrupting for belt-and-suspenders.
  (a._interrupting as HTMLElement).style.display =
    (it.interrupting && !it.nudgeFailed) ? "" : "none";
  // "interrupted" — the user stopped this session and hasn't re-engaged; quiet is user-chosen (the
  // user 2026-07-05). The follow-up-failed chip outranks it: it carries a romp-ask outcome, while
  // this only explains silence — never show both; nor alongside the in-flight "interrupting…" badge.
  (a._interrupted as HTMLElement).style.display =
    (it.interrupted && !it.interrupting && !it.nudgeFailed) ? "" : "none";
  // the chip label says "follow-up failed"; its tooltip carries the EVIDENCE — romp did follow up, and
  // when (the user 2026-07-02: the bare label read like a state romp observed, not a nudge outcome)
  a._nudgeFailed.title = it.nudged && it.nudged.times.length
    ? `romp followed up ${it.nudged.count}× (${it.nudged.times.map(clockHM).join(", ")}); the response didn't resolve it and it won't be re-asked — it's waiting on you`
    : "romp followed up once; the response didn't resolve it and it won't be re-asked — it's waiting on you";
  // "warning" chip: a judge stamped an anomaly on this goal — show the latest msg on hover, detail on click.
  // Data rides the card element so the click handler (wired once in build) always reads the current push.
  // When the warns are all GIVEN-UP summarizer lines, the chip SAYS so — "distill failed" (the user
  // 2026-08-13, who read the generic label as a mystery) — and its modal carries the Try again.
  a._warnsData = it.warns || null;
  a._failLog = it.failLog || null;
  if (it.warns && it.warns.length) {
    const allDistill = it.warns.every((w) => DISTILL_FAIL_RE.test(w.kind));
    const lbl = allDistill ? "distill failed" : "warning";
    a._warnChip.style.display = "";
    a._warnChip.textContent = it.warns.length > 1 ? `${lbl} ×${it.warns.length}` : lbl;
    // hover = the attempt history when one exists (the user 2026-08-18: "tried opus — 529" ×3 says
    // what the prose can't — that ONE model keeps failing and switching it would fix this)
    a._warnChip.title = (it.failLog && it.failLog.length
      ? it.failLog.map((f) => `${clockHM(f.t)} tried ${f.model} — ${f.note}`).join("\n")
      : it.warns[it.warns.length - 1].msg) + "\n— click for what happened and why";
  } else {
    a._warnChip.style.display = "none";
  }
  // "Awaiting <peer>" / "Handed off to <peer>" — this session has an unanswered message out to a live peer
  // (kernel _wait_for_graph): held in Working, not stalled, so auto-nudge skips it. A DELEGATE handoff wears
  // "Handed off to" (the peer owns the work now; the user 2026-07-25 — a handoff is not "awaiting background
  // agents"); a question keeps "Awaiting". The peer NAME renders in its NATIVE identity colour (like the
  // "↪ from" provenance), no emoji prefix (the user 2026-06-22). A mutual-wait CYCLE keeps the red styling +
  // a "Deadlock" label over both.
  const wo = it.waitingOn;
  if (wo) {
    a._waitOn.replaceChildren();
    const woPre = el("span", "fask-waiton-pre");
    woPre.textContent = wo.inCycle ? "Deadlock " : wo.kind === "delegate" ? "Handed off to " : "Awaiting ";
    const woName = el("span", "fask-waiton-name"); woName.textContent = wo.name;
    if (wo.color && wo.color.bg) woName.style.color = wo.color.bg;   // the peer's own identity colour
    a._waitOn.append(woPre, woName);
    // elapsed since the unanswered ask went out (kernel _wait_for_graph's since) — the same readout the
    // working narration and awaiting box wear, so a wait stuck for hours is glanceable (the user 2026-08-23)
    const woWaited = waitedSuffix(wo.since, Date.now() / 1000);
    if (woWaited) {
      const woDur = el("span", "fask-waiton-dur"); woDur.textContent = woWaited;
      a._waitOn.append(woDur);
    }
    a._waitOn.title = wo.inCycle
      ? "MUTUAL WAIT — this session and " + wo.name + " are each waiting on the other (a deadlock); auto-nudge surfaces it instead of nudging"
      : wo.kind === "delegate"
        ? "this session handed work to " + wo.name + " and acts when the result comes back — not stalled, so auto-nudge skips it"
        : "this session has an unanswered message out to " + wo.name + " — waiting on its reply, not stalled, so auto-nudge skips it";
    a._waitOn.className = "fask-waiton" + (wo.inCycle ? " fask-waiton-cycle" : "");
    a._waitOn.style.display = "";
  } else {
    a._waitOn.replaceChildren();
    a._waitOn.style.display = "none";
  }
  a._clr.style.display = it.provisional ? "none" : "";   // a placeholder has nothing to curate — no Clear
  // (The card-face "Status?" sweep was removed 2026-07-21 — see the comment where it used to be declared.)
  // ⏳ awaiting: held in Working, waiting on work it dispatched/delegated (agents, a subagent, a build). The
  // peer case already shows the "Awaiting <peer>" chip (waitingOn), so the generic awaiting box is suppressed
  // then. (The old header "awaiting" chip is gone — the body box below carries it; the user 2026-07-04.)
  // SPINNING SWIRL + a short caption in the card body (the user 2026-06-29): a card with a re-evaluation or
  // dispatched work in flight shows the spinning romp swirl saying what's happening. The whole ladder lives
  // in ./spin-caption so spin-caption.test.ts EXECUTES it — a source-regex pin is what let the recheck /
  // rejudging branches be silently wrong (the user 2026-07-21); see that file for the cases + the contract.
  // distillInputs (in ./distiller-line so the test EXECUTES the rule) maps state→(completed,blocked). A card
  // in the Working column yields neither: a summary describes work that has stopped, so it is withheld while
  // the card is in motion and re-renders untouched when it settles (the user 2026-07-22). See that file for
  // why this supersedes the 2026-07-21 pin-the-brief rule.
  const { completed: dCompleted, blocked: dBlocked } = distillInputs(it.distillState, it.column);
  // The card is not left mute in that window: the Working displacement only happens under recheck/rejudging,
  // and both raise the "Analyzing…" swirl below, which says the judge is looking at it again.
  const spin = spinFor(it, distillPending(dCompleted, dBlocked, it.summary, it.blockSummary, !!it.blocked),
                       dCompleted, Date.now() / 1000);
  const spinCaption = spin.caption, spinTip = spin.tip, awaitingBg = spin.awaitingBg;
  a._awaitSpin.style.display = spinCaption ? "" : "none";
  // The AWAITING case gets a rounded box (its distinct read); the swirl spins in every case now —
  // except the at-rest floor (`still`): quiet/unknown keep the glyph as the state anchor, stilled,
  // because spin reads as in-flight and nothing is (the user 2026-08-14).
  a._awaitSpin.classList.toggle("await-paused", awaitingBg);
  a._awaitSpin.classList.toggle("await-still", !!spin.still);
  if (spinCaption) {
    // a DELEGATION wait names its peers the way the "↪ from" line does (the user 2026-08-23): the
    // quiet host: prefix + the peer's identity colour, never a colourless "Awaiting peer". The
    // ladder's caption stays the fallback (older kernel payloads carry no peers).
    const awPeers = (awaitingBg && it.awaiting && it.awaiting.peers) || [];
    if (awPeers.length) {
      a._awaitWhy.replaceChildren();
      a._awaitWhy.append("Awaiting ");
      awPeers.forEach((p, i) => {
        if (i) a._awaitWhy.append(", ");
        const nm = el("span", "fask-waiton-name");
        nm.replaceChildren(...hostPartsNodes(p.host, p.name));
        if (p.color && p.color.bg) nm.style.color = p.color.bg;
        if (p.sid) {
          // the standard session-chip gesture (the handoffTo idiom above): click opens the session
          nm.title = "waiting on " + p.name + " — click opens the session";
          nm.style.cursor = "pointer";
          nm.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: p.sid }); };
        }
        a._awaitWhy.appendChild(nm);
      });
      a._awaitWhy.append(waitedSuffix(it.awaiting && it.awaiting.since, Date.now() / 1000));
    } else a._awaitWhy.textContent = spinCaption;
    a._awaitSpin.title = spinTip || spinCaption;
    // HONEST fallback (the user 2026-08-26): a peer-kind wait with no named session says WHY the
    // name is missing, instead of presenting "peer" as a style — identity is only truly unknowable
    // when the record predates identity capture or an older/offline kernel shipped the payload.
    if (awaitingBg && !awPeers.length && it.awaiting && it.awaiting.kind === "peer")
      a._awaitSpin.title += " (No session is named in this wait's record — it predates identity capture, or an older kernel shipped it.)";
  }
  // The swirl's "Analyzing…" caption + tooltip REPLACES the separate "↩ re-judging" chip (the user
  // 2026-06-29: don't show both) — drop the chip the recheck branch set above when the swirl is saying it.
  // ("Analyzing…" is the user-facing label for the re-judging spin, the user 2026-07-08.)
  if (spinCaption === "Analyzing…") a._followedup.style.display = "none";
  // ⏸ live block badge: the session is stopped mid-turn on a permission prompt /
  // picker FOR THIS CARD's work — the card files under BLOCKED while it lasts
  const isApiErr = it.blocked?.state === "apiError";
  const isJudgeAuth = it.blocked?.state === "judgeAuth";
  // the resume-gate card carries its own explanatory text + Proceed/Compact/Skip buttons, so the ⏸ chip
  // (which only speaks permission/picker) would just misread — suppress it there (the user 2026-07-21).
  const showBlk = !!it.blocked && !isApiErr && !isJudgeAuth && it.blocked.state !== "quarantine";
  a._blocked.style.display = showBlk ? "" : "none";
  if (showBlk && it.blocked) {
    // live prompts only — the paused-stall badge retired with the floor (2026-07-07; a failed nudge now
    // records a real block and the follow-up-failed CHIP carries that story)
    a._blocked.textContent = it.blocked.state === "permission" ? "⏸ approval"
      : "⏸ picker";
    setTip(a._blocked as HTMLElement, it.blocked.what + " — click to jump to the prompt in the chat");
    // the prompt (a picker / permission approval) is the session's LIVE bottom → `live` lands the chat right
    // on it, not wherever it was last scrolled (the user 2026-07-08).
    a._blocked.onclick = (ev: Event) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "openSession", id: it.sid, live: true }); };
  }
  // The DISTILLER's line (restored 2026-06-29): completed card → takeaway (it.summary), blocked card → decision
  // brief (it.blockSummary), shown ONLY when produced; never a generating placeholder, never the planner's why.
  // The rule lives in ./distiller-line so distiller-line.test.ts can EXECUTE it (a regex pin let it silently
  // turn off once — the user 2026-06-29). updateAskCard runs every push, so this re-applies on every refresh.
  const distillShown = applyDistillLine(a._distill as HTMLElement, dCompleted, dBlocked,
                   it.summary, it.blockSummary);
  // A judge-auth card explains itself ON THE CARD FACE (the user 2026-08-12: a message, not just a
  // chip): no decision brief can exist here — the distiller is one of the very judges that are down —
  // so the distill line carries blocked.what instead of sitting empty. applyDistillLine re-runs every
  // push, so the line restores itself the moment the latch clears and a real brief/takeaway returns.
  if (isJudgeAuth && it.blocked && !distillShown) {
    const dle = a._distill as HTMLElement;
    dle.textContent = it.blocked.what || "";
    dle.style.display = it.blocked.what ? "" : "none";
  }
  // PER-PARAGRAPH ages (the user 2026-07-24): a MULTI-item decision brief writes one paragraph per
  // owed item IN ORDER (judge BLOCK_BRIEF_SYS 2026-07-21 + briefParts), so each paragraph can wear
  // the age of ITS OWN ask — the exact block-event time — and a stale re-surfaced ask shows its real
  // age at a glance (the incident: a card re-displayed a brief whose go-ahead was given two hours
  // earlier). The DONE side mirrors it: a takeaway the distiller split by <completed-items> ships
  // summaryParts, each paragraph stamped with its own done-event time. Deliberate gates: the parts
  // must belong to the STATE being shown (briefParts ↔ blocked brief, summaryParts ↔ takeaway),
  // multi-item only (a single ask keeps just the card header's age — the user's rule), and the
  // paragraph count must MATCH briefParts (the model may merge items; a missing stamp beats a wrong
  // one — then the plain applyDistillLine text above stands untouched). Rebuilt every push, so the
  // ages tick live like every other relAge on the card.
  //
  // ONE EXTRA TRAILING PARAGRAPH is allowed and left UNSTAMPED (the user 2026-07-29): all three judge
  // prompts now put whatever is still open in a last paragraph of its own, and that paragraph belongs to
  // no item, so it carries no item's age. Without this the count-match gate saw items+1 paragraphs on
  // every multi-item card that had a leftover and dropped every stamp. Exactly one extra, always the
  // last: a bigger surplus means the model split some other way and the mapping can no longer be trusted,
  // so the gate falls through to the plain text as before.
  const bp = dCompleted ? it.summaryParts : dBlocked ? it.briefParts : null;
  // Paragraph SPLIT fires for a stamped parts-takeaway (T153) OR for per-paragraph citations (T220,
  // the user's ruling: each paragraph of a multi-topic summary is independently clickable, hover
  // highlighting exactly the paragraph under the pointer). Anchor precedence per paragraph: the
  // model's OWN citation (it names what the paragraph was written from) beats the T153 tree-row
  // mapping; a paragraph with neither keeps the whole-summary landing via the card-level link.
  const pAnchors = (distillShown && it.summaryAnchorsPara) || null;
  if (distillShown && ((bp && bp.length > 1) || (pAnchors && pAnchors.some(Boolean)))) {
    const paras = distillShown.split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
    const stampOk = !!(bp && bp.length > 1 && (paras.length === bp.length || paras.length === bp.length + 1));
    const anchOk = !!(pAnchors && paras.length === pAnchors.length);   // count drift → drop, never mis-map
    if (stampOk || anchOk) {
      const dle = a._distill as HTMLElement;
      dle.textContent = "";
      const nowS = Date.now() / 1000;
      paras.forEach((p, i) => {
        const para = el("div", "fask-para");
        para.textContent = p;
        if (stampOk && i < bp!.length) {
          const age = el("span", "fask-para-age");
          age.textContent = relAge(nowS - (bp![i].since || nowS));
          para.append(" ", age);
        }
        // T220 first: the paragraph's own citation, with its located span riding the landing
        const cited = anchOk ? pAnchors![i] : null;
        let au: string | null = null, aq: string | undefined;
        if (cited && cited.u) { au = cited.u; aq = cited.q; }
        else if (stampOk && i < bp!.length) {
          // T153: the item's tree row carries its WORK anchor
          const pid = bp![i].id;
          const prow = pid ? (it.tree || []).find((r) => r.id === pid) : undefined;
          if (prow && prow.anchorUuid) au = prow.anchorUuid;
        }
        if (au) {
          const u = au;
          para.classList.add("fask-para-link");
          para.title = "jump to where this piece resolved";
          para.onclick = (ev: Event) => {
            ev.stopPropagation(); focusEcho(it.sid);
            vscodeApi?.postMessage({ type: "showOnTimeline", itemId: it.itemId, sid: it.sid,
                                     t: it.t, anchor: "work", anchorUuid: u, quote: aq });
          };
        }
        dle.append(para);
      });
    }
  }
  // The distiller line is a LINK: clicking it jumps to where the takeaway/brief was actually written — the
  // biggest contiguous assistant-text block in the goal's work span (it.summaryAnchorUuid; kernel
  // _seg_best_text). This was lost when the line was restored via applyDistillLine (which only sets text), so
  // the summary read like plain text with no affordance (the user 2026-06-29). stopPropagation so it doesn't
  // also open the modal (the card-body click). Falls back to non-clickable when there's no anchor.
  // STALE-takeaway note (the user 2026-08-19): the rule lives in ./distiller-line so the test EXECUTES
  // it. Prepended after the parts-split (which rewrites the element), so it survives either rendering.
  const staleNote = distillStaleNote(!!it.summaryStale, dCompleted, distillShown);
  if (staleNote) {
    const sn = el("div", "fsum-stale");
    sn.textContent = staleNote;
    (a._distill as HTMLElement).prepend(sn);
  }
  const dl = a._distill as HTMLElement;
  if (distillShown && it.summaryAnchorUuid) {
    dl.classList.add("fask-distill-link");
    dl.title = "jump to where this was written";
    dl.onclick = (ev: Event) => { ev.stopPropagation(); focusEcho(it.sid); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: it.itemId, sid: it.sid, t: it.t, anchor: "work", anchorUuid: it.summaryAnchorUuid, quote: it.summaryAnchorQuote || undefined }); };
  } else if (distillShown) {
    // No anchor recorded (a card minted from a postal delegate, a completion turn not yet landed) — the
    // line must still ACKNOWLEDGE the click instead of rendering as silently dead text (the user
    // 2026-07-20: hovering the federation card offered nothing, with no error anywhere). Same
    // affordance, honest outcome: the click says why the jump can't happen.
    dl.classList.add("fask-distill-link");
    dl.title = "no anchor recorded for this card";
    // Toast AND file it in the error center (the user 2026-07-28): a transient pop-up left no trace of a
    // click that couldn't do anything, so the failure was unreportable a minute later. The entry carries
    // the card so clicking it jumps back here.
    dl.onclick = (ev: Event) => {
      ev.stopPropagation();
      feedToast("couldn't locate this in the transcript — no anchor was recorded for this card");
      try {
        window.parent?.postMessage({ romp: "notify", kind: "locate",
          text: "Couldn't jump to this summary: no anchor was recorded for this card",
          sid: it.sid, itemId: it.itemId }, "*");
      } catch { /* no shell (VS Code view) */ }
    };
  } else {
    dl.classList.remove("fask-distill-link");
    dl.onclick = null;
    dl.removeAttribute("title");
  }
  // TWO collapsible distiller sections (the user 2026-07-02): BACKGROUND (re-orientation for a reader who
  // forgot the thread, collapsed by default) above the takeaway (expanded by default), each with a +/−.
  applySections(a, it, !!distillShown);   // bg/summary/sub-goals (mutually exclusive) — applyDistillLine returns the line's TEXT (string), coerce to "has content"
  // API error → a red "API error" badge + a Retry button that pastes "retry" into the session to resume
  // the stalled turn (the user 2026-06-16). The card STAYS in Working (the user 2026-06-29) — an API error is
  // a transient stall, not a block — so this badge + Retry are the only API-error cue; no column move.
  const spendLimit = !!(it.blocked && it.blocked.spendLimit);
  const modelLimit = !!(it.blocked && it.blocked.modelLimit);
  const refusal = !!(it.blocked && it.blocked.refusal);
  const authErr = !!(it.blocked && (it.blocked as { authErr?: boolean }).authErr);
  // …and the whole unit RETIRES the moment the session recovers (the user 2026-08-24, screenshot: a
  // GREEN awaiting dot beside a lone red Retry — the control read as arbitrary): visibility keys on
  // the session's LIVE state from this very payload — a session working again, or awaiting the
  // background work it dispatched, has resumed, and there is nothing to resume by hand no matter
  // what the card's stored block record still says. The same sets the session dot reads
  // (workingSet/awaitingSet), refreshed on every push — event-keyed, never a timer.
  const apiRecovered = workingSet.has(it.name) || awaitingSet.has(it.name);
  const showApiErr = isApiErr && !apiRecovered;
  a._apiBadge.style.display = showApiErr ? "" : "none";
  // Retry pastes "retry" to resume a stalled turn — useless against a monthly spend cap (retrying can't lift a
  // billing limit), so hide it there and let the badge tell you to raise the cap (the user 2026-07-14).
  // A spent MODEL allowance is the same shape: Retry re-fails until you switch model or top up, and the
  // badge says so (the user 2026-08-01). Its window does reset on its own, which the badge title carries.
  // A safeguards REFUSAL is the same shape again (the user 2026-08-15): deterministic on the same input,
  // so Retry re-collects the same refusal — the badge names the real fix (rewrite it or drop the thread).
  a._apiRetry.style.display = (showApiErr && !spendLimit && !modelLimit && !refusal) ? "" : "none";
  // a dead credential's card carries THE FIX (T157): the gear's Billing login — phone-workable
  const loginBtn = a._apiLogin as HTMLButtonElement;
  loginBtn.style.display = (showApiErr && authErr) ? "" : "none";
  if (showApiErr && authErr) loginBtn.onclick = (ev: Event) => {
    ev.stopPropagation();
    window.postMessage({ romp: "openSettings" }, "*");   // the login flow lives in the gear's Billing block
  };
  // "Continue" shows on a LIVE needs-you card with no live ask attached: the gesture claims "you're not
  // waiting on me", which means nothing in Working/Completed, can't answer a real permission prompt or
  // picker (it.blocked — text sent there would just queue behind the ask), and has no one to tell on a
  // dead session. Re-arm the label only once its own reply has been judged (same contract as the modal's
  // Check status): while followupPending/recheck holds, the disabled "Sent" IS the acknowledgement.
  const contBtn = a._cont as HTMLButtonElement;
  contBtn.style.display = (askColumn(it) === "needsInput" && it.live && !it.provisional && !it.blocked)
    ? "" : "none";
  if (contBtn.disabled && !it.followupPending && !it.recheck && !it.rejudging) {
    contBtn.disabled = false; contBtn.textContent = "Continue";
  }
  contBtn.title = contTitle(contBtn.disabled, "a continue", it.followupAt);
  if (showApiErr && it.blocked) {
    // on-you errors name themselves: a spend cap (raise it), "prompt too long" (compact), or a spent model
    // allowance (switch model); other API errors are transient and auto-retrying (2026-06-29 / 07-14 / 08-01).
    a._apiBadge.textContent = spendLimit ? "⚠ Spend limit"
      : it.blocked.tooLong ? "⚠ Prompt too long"
      : modelLimit ? "⚠ Model limit"
      : refusal ? "⚠ Safeguards refused"
      : it.blocked.status ? `⚠ API error · ${it.blocked.status}` : "⚠ API error";
    // a refusal's raw CLI text buries the remedy — the kernel's `what` states it plainly, so tip with that
    setTip(a._apiBadge as HTMLElement, (spendLimit || refusal) ? it.blocked.what : (it.blocked.text || it.blocked.what));
    a._apiRetry.disabled = false; a._apiRetry.textContent = "Retry";
    a._apiRetry.onclick = (ev: Event) => {
      ev.stopPropagation();
      vscodeApi?.postMessage({ type: "apiRetry", id: it.sid });
      a._apiRetry.disabled = true; a._apiRetry.textContent = "Retrying…";
    };
  }
  // CAP-SWITCH OFFER (2026-08-30): the tradeoff stated plainly, the pick the user's alone. The
  // latched "Switching…" survives pushes (the T150 idiom — the payload still says login until the
  // reconnect applies the pick) and self-resolves: once authLive flips to key, the kernel stops
  // minting the offer and both elements hide.
  const cap = (showApiErr && it.blocked && it.blocked.capOffer) || null;
  (a._capLine as HTMLElement).style.display = cap ? "" : "none";
  (a._capBtn as HTMLButtonElement).style.display = cap ? "" : "none";
  if (cap) {
    a._capLine.textContent = "This session bills the subscription, whose usage window is full — it can "
      + "bill your API key instead until the window resets at " + clockHM(cap.resetsAt)
      + ". It switches back only if you switch it.";
    if (!(a._capBtn as HTMLButtonElement).disabled) {
      a._capBtn.title = "bills THIS session to the API key — only your pick switches billing, in either "
        + "direction, and the auto-retry at the reset runs whether or not you switch";
      a._capBtn.onclick = (ev: Event) => {
        ev.stopPropagation();
        vscodeApi?.postMessage({ type: "setAuth", id: it.sid, value: "key" });
        a._capBtn.disabled = true; a._capBtn.textContent = "Switching…";   // ack before the round-trip
        a._capBtn.title = "the switch is applying — it lands the way a gear billing pick does, and this offer clears itself";
      };
    }
  } else { (a._capBtn as HTMLButtonElement).disabled = false; a._capBtn.textContent = "Switch to the key"; }
  // JUDGE-AUTH (the user 2026-08-12): romp's own analysis of this session is refused on its credential —
  // the judges bill what the session bills (kernel _judge_auth), that credential is failing, and no card
  // here can move until the user fixes it. The chip names which credential; no Retry (nothing in-session
  // to resume — the latch clears itself on the judges' next successful call once the credential works).
  (a._jauthBadge as HTMLElement).style.display = isJudgeAuth ? "" : "none";
  if (isJudgeAuth && it.blocked) {
    a._jauthBadge.textContent = it.blocked.mode === "key" ? "⚠ Can't analyze · API key" : "⚠ Can't analyze · login";
    setTip(a._jauthBadge as HTMLElement, (it.blocked.what || "")
      + (it.blocked.text ? ` — the CLI said: ${it.blocked.text}` : ""));
  }
  // "⚠ retrying since HH:MM" — the OPEN turn is inside an api-retry storm (kernel `retrying`: the live
  // backend state, with the storm's start from the states log). The card stays in Working — the storm is
  // in motion, not a block — but says so instead of reading as plain healthy Working for the storm's whole
  // life (the user 2026-07-09: an ~80-minute storm was invisible on the card). The stopped-on-error badge
  // (isApiErr) outranks it; the kernel never sets both, this is belt-and-suspenders.
  const rt = it.retrying;
  (a._retryBadge as HTMLElement).style.display = (rt && !isApiErr) ? "" : "none";
  if (rt && !isApiErr) {
    a._retryBadge.textContent = rt.since ? `⚠ retrying since ${clockHM(rt.since)}` : "⚠ retrying";
    setTip(a._retryBadge as HTMLElement, "this session's turn keeps hitting API errors and is auto-retrying"
      + (rt.count ? ` (attempt ${rt.count})` : "")
      + " — still in motion, not stalled; it resumes on its own when the API recovers");
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
  // QUARANTINE (per-host trust) → a COMPACT "New message" card under the RECIPIENT session's name —
  // that delivery is what you're approving (the user 2026-07-26; the full-body card made the feed
  // scroll). One dim line names the sender (host:name) with the bus's 90-char gist; clicking it opens
  // the decision modal with the whole body, read-only (peer content, never auto-run). Approve
  // delivers; Deny opens the same modal's feedback step (optional note mailed back to the sender).
  // The decision CARRIES THE CARD'S sid so the federation manager routes it to the kernel that
  // actually holds the file — without it a remote hold's verdict landed on the local kernel and
  // Approve silently did nothing (same shape as the askClear routing fix, 2026-07-02). No optimistic
  // clear — the next kernel push removes the card on success, or re-renders it (buttons re-enabled)
  // if the bus refused (e.g. the recipient is no longer live; the warn toast says why).
  // Human-in-the-loop is the whole point of directed trust.
  const isQuar = it.blocked?.state === "quarantine";
  const qBody = a._qBody as HTMLElement;
  qBody.style.display = isQuar ? "" : "none";
  for (const b of [a._qApprove, a._qDeny] as HTMLButtonElement[]) b.style.display = isQuar ? "" : "none";
  if (isQuar && it.blocked) {
    const mid = it.blocked.mid || "";
    // WHO to WHO, then what it says (the user 2026-07-29). Held mail is a delivery between two named
    // sessions on two named machines, and the card used to render that as one grey run of text with the
    // recipient missing entirely — you could not tell which of your sessions was about to receive it.
    // Hosts stay quiet metadata (the same .host-prefix every surface uses), session names wear their
    // identity colours, and the gist gets its own line under the route.
    const toHost = (it.sid && it.sid.indexOf(":") > 0) ? it.sid.slice(0, it.sid.indexOf(":")) : feedSelfHost;
    qBody.replaceChildren(
      quarWho(it.blocked.origin || "", it.blocked.frm || "?"),
      Object.assign(el("span", "fq-arrow"), { textContent: "\u2192" }),
      quarWho(toHost, it.blocked.to || it.name || "?", it.color?.bg),
      Object.assign(el("div", "fq-gist"), { textContent: it.blocked.gist || it.blocked.body || "" }));
    qBody.title = "click to read the whole message and decide";
    a._qApprove.disabled = false; a._qApprove.textContent = "Approve";
    a._qDeny.disabled = false; a._qDeny.textContent = "Deny";
    const decide = (action: string, busy: string, text: string, feedback?: string) => {
      vscodeApi?.postMessage({ type: "quarantineDecision", mid, action, text, sid: it.sid, feedback });
      for (const b of [a._qApprove, a._qDeny] as HTMLButtonElement[]) b.disabled = true;
      (action === "deny" ? a._qDeny : a._qApprove).textContent = busy;
    };
    const ends = (): [QuarEnd, QuarEnd] => [
      { host: it.blocked!.origin || "", name: it.blocked!.frm || "?" },
      { host: toHost, name: it.blocked!.to || it.name || "?", color: it.color?.bg }];
    qBody.onclick = (ev: Event) => { ev.stopPropagation(); showQuarantineDialog(...ends(), it.blocked!.body || "", decide, false); };
    a._qApprove.onclick = (ev: Event) => { ev.stopPropagation(); decide("approve", "Delivering…", it.blocked!.body || ""); };
    a._qDeny.onclick = (ev: Event) => { ev.stopPropagation(); showQuarantineDialog(...ends(), it.blocked!.body || "", decide, true); };
  }
  (a._clr as HTMLElement).style.display = isQuar ? "none" : "";

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

  // GROUPED mode (the user 2026-07-13): the session header on the backdrop carries the identity + working
  // dot, so the card drops its own name; row2 hides entirely once nothing on it shows. (The Clear re-home
  // between rows is GONE, the user 2026-08-08: the action corner lives in row1 in every mode now — see
  // fask-btns at construction — so there is nothing to move between renders, which is the strongest form
  // of the click-safety rule.)
  const gmode = feedPrefs().grouped;
  ((a._name as HTMLElement).parentElement as HTMLElement).style.display = gmode ? "none" : "";
  const r2 = a._row2 as HTMLElement;
  const r2live = (Array.from(r2.children) as HTMLElement[]).some((c) => c.style.display !== "none");
  r2.style.display = gmode && !r2live ? "none" : "";

  // (bg / summary / sub-goals are wired above in applySections — one mutually-exclusive selection.)
}

// One end of a held message's route: "host:" as quiet metadata, the session name in its identity
// colour. The colour is looked up by the name a peer addresses (sessionColors, filled per payload) and
// simply absent when that session has no cards here — an invented colour would be a lie about identity.
// A host romp cannot currently reach wears the same struck mark its tabs and lanes do.
function quarWho(host: string, name: string, known?: string): HTMLElement {
  const who = el("span", "fq-who");
  if (host) {
    const h = el("span", "host-prefix");
    h.textContent = host + ":";
    if (hostIsDown(host + ":x")) { h.classList.add("off"); h.title = hostDownNote(host + ":x"); }
    who.appendChild(h);
  }
  const n = el("span", "fq-name");
  n.textContent = name;
  const color = known || sessionColors.get(host ? host + ":" + name : name) || sessionColors.get(name);
  if (color) n.style.color = color;
  who.appendChild(n);
  return who;
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

// The session that owns a feed itemId — every showAskPath must carry it: federation's routeOutbound
// keys on `sid` (an itemId alone can only go local), so without it a remote card's hover landed on
// the LOCAL kernel, which knows nothing of that goal, and the timeline/chat highlight never lit
// (the user 2026-08-03). Same contract as the askClear sid fix (2026-07-02).
function sidOfItem(itemId: string | null): string | undefined {
  if (!itemId) return undefined;
  return asks.find((a) => a.itemId === itemId)?.sid;
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
  row1.append(title, time);   // time trails the title, right-justified (the user 2026-07-07) — matches the ask card
  const row2 = el("div", "fask-row2");
  const idwrap = el("div", "fask-id");
  const name = el("a", "fname"); name.title = "open this session";
  idwrap.append(name);   // no "· N parts" label — the member checklist below already shows the count
  const clr = el("button", "fdismiss"); clr.textContent = "Clear"; clr.title = "clear ALL sub-asks of this request (inbox-zero)";
  // Clear rides row1's action corner in every mode (the user 2026-08-08) — matches the ask card. Same
  // fask-btns wrapper so the float/wrap CSS is shared; no Continue here (a group is a multi-ask turn —
  // its members carry their own).
  const btns = el("span", "fask-btns");
  btns.append(clr);
  row1.append(btns);
  row2.append(idwrap);
  const memberList = el("div", "fgroup-members");   // no row3: the group card has no time-row content left
  main.append(row1, row2, memberList);
  card.append(main);

  const m0 = () => ((card as any)._g as AskGroup | undefined)?.members?.[0];
  title.onclick = (ev) => { ev.stopPropagation(); const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, sid: m.sid }); };
  name.onclick = (ev) => { ev.stopPropagation(); const cur = (card as any)._g as AskGroup; if (cur?.sid) openOrReviveSession(cur.sid, cur.live, cur.name); };
  clr.onclick = (ev) => {
    ev.stopPropagation();
    card.dispatchEvent(new MouseEvent("mouseleave"));   // flush the group's stuck hover highlight (see the ask card's clear)
    const cur = (card as any)._g as AskGroup;
    dressHeaderIfLast(card, cur.sid);   // a group is one session's turn — same one-motion rule (2026-08-24)
    card.classList.add("dismissing");
    clearedStack.push(cur.members.slice());   // cache the whole batch for an instant optimistic Undo
    for (const m of cur.members) { pendingCleared.add(m.itemId); vscodeApi?.postMessage({ type: "askClear", itemId: m.itemId, sid: m.sid }); }   // clear every member
    // only finalize if a render in the 180ms window didn't revive (re-render clears
    // .dismissing) or replace this card — else a stale timeout yanks the wrong one
    setTimeout(() => { if (groupEls.get(cur.turnId) === card && card.classList.contains("dismissing")) { card.remove(); groupEls.delete(cur.turnId); dropDismissed(cur.members.map((m) => m.itemId)); } }, 180);
  };
  // hover (120ms intent) → white border + preview the group's timeline journey
  // (first member). leave → restore the pin (ask OR group) or clear.
  let hoverTimer: number | undefined;
  card.addEventListener("mouseenter", () => {
    freezeEnter(fkey);                                 // hover-freeze: pointer truth, no debounce
    hoverTimer = window.setTimeout(() => {
      hoverTimer = undefined;
      hoverAskId = fkey; applyFocus();
      const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, sid: m.sid, locate: false });
    }, 120);
  });
  card.addEventListener("mouseleave", () => {
    freezeLeave(fkey);                                 // hover-freeze: leaving the card flushes queued payloads
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = undefined; }
    if (hoverAskId === fkey) {
      hoverAskId = null; applyFocus();
      const pin = focusAnchorId(pinnedAskId);
      if (pin) vscodeApi?.postMessage({ type: "showAskPath", itemId: pin, sid: sidOfItem(pin), locate: false });
      else { const m = m0(); if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, sid: m.sid, off: true }); }
    }
  });
  // body single-click → modal; double-click → pin/unpin (debounced ~220ms).
  let pending: number | undefined;
  card.addEventListener("click", () => {
    if (pending) { clearTimeout(pending); pending = undefined; return; }
    pending = window.setTimeout(() => {
      pending = undefined;
      fullscreenAskId = fkey;
      const m = m0(); if (m) vscodeApi?.postMessage({ type: "cardOpened", itemId: m.itemId, sid: m.sid });   // the open-metric row (2026-08-25)
      if (m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, sid: m.sid, locate: false });
      render();
    }, 220);
  });
  card.addEventListener("dblclick", () => {
    if (pending) { clearTimeout(pending); pending = undefined; }
    pinnedAskId = pinnedAskId === fkey ? null : fkey;
    applyFocus();
    const m = m0();
    // double-click = PIN + jump the TIMELINE (same contract as single cards)
    if (pinnedAskId === fkey && m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, sid: m.sid, locate: false, jump: true });
    else if (!pinnedAskId && hoverAskId !== fkey && m) vscodeApi?.postMessage({ type: "showAskPath", itemId: m.itemId, sid: m.sid, off: true });
  });

  const a = card as any;
  a._title = title; a._name = name; a._time = time; a._members = memberList;
  a._row1 = row1; a._row2 = row2; a._clr = clr;   // Clear lives in row1's action corner (2026-08-08)
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
  // outline in the group's session identity colour (the user 2026-07-15) — CSS: 0.5α rest, bolded on hover/pin
  setCardChannels(card, (g.color && hexToRgb(g.color.bg)) || [r, gg, b]);
  a._title.textContent = g.title;
  a._name.replaceChildren(...hostNameNodes(g.name, g.sid));
  if (g.color) a._name.style.color = g.color.bg;
  setWorkDot(a._name, dotFor(g.name));   // working/awaiting dot before the session name
  a._time.textContent = relAge(hostNow - g.t);
  wireAgeTip(a._time, () => provenanceGroupRows(g.members.map(rootStart), g.t, hostNow, PROV_FMT));
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
  // GROUPED mode (the user 2026-07-13) — same treatment as the ask card: the backdrop header carries the
  // name + dot, the emptied name row hides. (Clear rides row1's action corner in every mode now, the
  // user 2026-08-08 — no re-home between renders.)
  const gmode = feedPrefs().grouped;
  ((a._name as HTMLElement).parentElement as HTMLElement).style.display = gmode ? "none" : "";
  (a._row2 as HTMLElement).style.display = gmode ? "none" : "";   // the group's row2 is only the name now
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
function nodeMark(n: AskTreeNode): string {   // AUTHORITATIVE nodes keep the same glyph; the .auth-* class adds a white ring
  // CLEARED does not touch the mark (the user 2026-07-26: the box means DONE, and only done — a
  // cleared node wears the strike + "cleared" chip, and its mark keeps saying whether it finished).
  if (n.status === "done") return "●";
  if (n.status === "question") return "⏸";   // blocked → the red pause (was an amber ?), consistent w/ the ledger
  return "○";
}
// The cleared row's plain-language story (the user 2026-07-25: a struck-through sub-goal read as
// unexplained machinery, and nothing on screen said the strike means YOU cleared it). One word on the
// row, the sentence on hover, in both the card checklist and the modal. Named "cleared", not
// "dropped" — the same word the Clear button and the undo already use (the user 2026-07-26).
const CLEARED_TIP = "you cleared this off the board — no longer needed; the box still shows whether it was done";
function clearedTag(): HTMLElement {
  const tag = el("span", "fcleared-tag");
  tag.textContent = "cleared";
  tag.title = CLEARED_TIP;
  return tag;
}

// The parked row's plain-language story (the user 2026-08-24: a queued ask silently sat 40 minutes
// while the same card's younger items were dispatched one after another, and nothing said so). One
// quiet word on the row, the explanation on hover — a hint, never a needs-you alarm; the kernel
// retires it the instant the row gets its own delegation or any verdict (_parked_rows).
function parkedTag(n: number): HTMLElement {
  const tag = el("span", "fparked-tag");
  tag.textContent = "parked";
  tag.title = "nothing has happened here yet — " + n + " newer ask" + (n === 1 ? " was" : "s were")
    + " dispatched past this one; this tag clears on its own dispatch or any ruling";
  return tag;
}

function nodeStatusClass(n: AskTreeNode): string {
  if (n.cleared) return "cleared";
  if (n.status === "done") return "done";
  if (n.status === "question") return "question";
  return "open";
}

// One human phrase per verdict-log row — the modal's per-item story speaks in outcomes
// ("asked you", "you answered"), never judge internals (the user 2026-07-20).
function logPhrase(r: NodeLogRow): string {
  if (r.kind === "block") return r.src === "user" ? "you flagged a block" : "asked you";
  if (r.kind === "unblock") return r.src === "user" ? "you answered" : "unblocked";
  if (r.kind === "done") return r.src === "user" ? "you checked it off" : "marked done";
  if (r.kind === "reopen") return "reopened";
  // a clear is only "you" when the user did it — the episode boundary clears with src "romp"
  // when a /clear drops the conversation's open cards (the user 2026-07-27)
  if (r.kind === "clear") return r.src === "romp" ? "dropped with the cleared conversation" : "you cleared it";
  if (r.kind === "dismiss") return "dismissed";
  if (r.kind === "settle") return "settled";
  return r.kind || "updated";
}
const logRowT = (r: NodeLogRow): number => r.at || r.evT || 0;
// the provenance popover (hover the card's age stamp) borrows the same vocabulary the card renders with
const PROV_FMT: ProvFmt = { rel: relAge, clock: clockHM, phrase: logPhrase };
// The shared styled tooltip (tip.ts) carries every age stamp's provenance (the user 2026-07-27: the
// native title tooltip was dense and unaligned). Times sit in their own right-aligned column, rows
// wear the modal history-row look (.age-tip-row in feed.css). Rebuilt per hover, so it is click-safe;
// a lazy rows thunk keeps the assembly off the render path entirely. Placed ABOVE the stamp (it sits
// at a card's bottom edge), flipping below when there is no headroom. Cards are keyed + updated IN
// PLACE across pushes, so the hovered stamp usually survives a re-render — unconditionally hiding on
// render made the tip vanish ~a second into every hover (the feed re-renders on every kernel push;
// the user 2026-07-27): pruneTip (called after each render) hides only when the anchor was actually
// torn out of the DOM, where its mouseleave can never fire.
function wireAgeTip(elm: HTMLElement, rows: () => ProvRow[]): void {
  wireTip(elm, (tip) => {
    for (const r of rows()) {
      const row = el("div", "age-tip-row" + (r.kind === "stamp" ? " stamp" : ""));
      const w = el("span", "age-tip-when"); w.textContent = r.when;
      const x = el("span", "age-tip-what"); x.textContent = r.what;
      // each line wears its own recency colour — time AND text, the chat tab-tip treatment (the user
      // 2026-07-27); the un-timed remainder row keeps the panel's dim default
      if (r.t > 0) { const c = ageColorReadable(hostNow - r.t); row.style.color = c; w.style.color = c; }
      row.append(w, x); tip.appendChild(row);
    }
  }, { place: "above" });
}
// the node's owning session for navigation (a handoff node lives in the recipient's transcript) —
// the same resolution wireNodeZones uses for its zones
function navSidOf(it: AskItem, node: AskTreeNode): string {
  return node.whoSid || (node.kind === "handoff" ? node.id.split(":")[0] : it.sid);
}

// Canned one-click status asks (the user 2026-07-20). The reply shapes are exactly the verdicts the
// judge's planner files off a nudge reply — done / in progress / blocked-on-you / obsolete — so the
// session's answer heals the card without any new ingestion machinery.
//
// VOICE (the user 2026-07-24): the session has no idea romp is tracking it, so these read as the person
// it works for asking, in their words. The old opener announced a status check on "this card", naming a
// romp OBJECT the recipient has never heard of, and the four labeled reply slots read as a form. The
// same four answers still come back — a person asking "what shipped, what's next, or what do you need
// from me" gets them without naming the taxonomy. Same rule as the clear-wrap message.
function statusAskOne(title: string): string {
  return "Where does \"" + title + "\" stand? One line: what shipped, what's next, or exactly what you "
       + "need from me if you're stuck. If it isn't worth doing anymore, say so.";
}
function statusSweepText(it: AskItem): { n: number; text: string } {
  const open = (it.tree || []).filter((n) => n.status !== "done" && !n.cleared && n.kind !== "handoff" && n.id !== it.itemId);
  const lines = open.slice(0, 15).map((n) => "- " + (n.text || "(sub-goal)"));
  const more = open.length > 15 ? "\n(+" + (open.length - 15) + " more)" : "";
  return { n: open.length,
           text: "Where does each of these stand? One line each: what shipped, what's next, or exactly "
               + "what you need from me if you're stuck. If one isn't worth doing anymore, say so:\n"
               + lines.join("\n") + more };
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
    n.id + n.status + (n.cleared ? "x" : "") + (n.whoWorking ? "W" : "")
    // parked can mint/retire off a DIFFERENT top's dispatch (tops are siblings), which changes
    // nothing else in THIS tree — without it in the sig an open group modal kept a stale tag
    + (n.parked && n.parked.n ? "p" + n.parked.n : "")
    + (collapsedNodes.has(it.itemId + ":" + n.id) ? "c" : "")
    + (nodeLogOpen.has(it.itemId + ":" + n.id) ? "L" : "") + ((n.log || []).length || "")).join("|");
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
  const briefs = new Map<string, AskQuestion>();   // decision sub-cards retired with openQuestions (2026-07-07)
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

// Debug-mode judge warnings (the user 2026-07-09): every judge failure touching this card, appended
// below the tree. The kernel only emits warnRows while `romp debug on`, so the section
// simply never exists in normal mode. One line per failure (time · judge · kind — evidence); a row
// captured in debug mode expands (native <details>) to the failing call's full input + reply, so a
// rejection is inspectable the moment it happens, without reproducing it.
function applyModalWarnings(host: HTMLElement, it: AskItem): void {
  const rows = it.warnRows || [];
  let sec = host.querySelector(":scope > .fmodal-warns") as HTMLElement | null;
  if (!rows.length) { sec?.remove(); return; }
  const sig = rows.map((r) => r.t + r.judge + r.err).join("|");
  if (sec && (sec as any)._sig === sig) return;   // unchanged → keep the DOM (an open <details> survives the push)
  sec?.remove();
  sec = el("div", "fmodal-warns");
  (sec as any)._sig = sig;
  const head = el("div", "fmodal-warns-head");
  head.textContent = `Warnings — ${rows.length} judge failure${rows.length === 1 ? "" : "s"} (debug mode)`;
  sec.appendChild(head);
  for (const r of rows.slice().reverse()) {       // newest first: the live story on top
    const row = el("div", "fmodal-warn-row");
    const line = el("div", "fmodal-warn-line");
    line.textContent = `${clockHM(r.t)} · ${r.judge} · ${r.err}${r.note ? " — " + r.note : ""}`;
    row.appendChild(line);
    if (r.debug && (r.debug.input || r.debug.reply)) {
      const det = document.createElement("details");
      det.className = "fmodal-warn-det";
      const sum = document.createElement("summary");
      sum.textContent = "input + reply";
      det.appendChild(sum);
      for (const [cap, txt] of [["input", r.debug.input], ["reply", r.debug.reply]] as const) {
        if (!txt) continue;
        const capEl = el("div", "fmodal-warn-cap"); capEl.textContent = cap;
        const pre = document.createElement("pre"); pre.className = "fmodal-warn-pre"; pre.textContent = txt;
        det.append(capEl, pre);
      }
      row.appendChild(det);
    }
    sec.appendChild(row);
  }
  host.appendChild(sec);
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
  // itemId = the CLICKED node's own id, not the card's top (the user 2026-07-01): the kernel's _cite_for
  // seeds the composer chip from it, so a sub-goal click cites THAT sub-goal — its own title and its own
  // injected context in the audit preview — instead of a generic top-goal chip. The kernel uses itemId
  // only for the citation; the chat landing is fully anchorUuid-based, so navigation is unchanged.
  const navId = node.id || it.turnId;
  const navSid = node.whoSid || (node.kind === "handoff" ? node.id.split(":")[0] : it.sid);
  // An agentTask-OPEN node is authoritatively unchecked — never "resolved", so the mark hover can't read
  // "jump to where this got checked off" on an item the agent hasn't crossed off (the user 2026-07-01).
  // Defense-in-depth for the kernel's _agent_open_set fix: correct even if a stale build serves status:"done".
  // ...and a rolled-UP question ancestor (qderived) is not itself resolved: the block landed on a
  // descendant, so its own anchor is its mint, and the hover must not claim "marked blocked" here.
  const resolved = (node.status === "done" || (node.status === "question" && !node.qderived)) && node.auth !== "open";
  // time-nav fallback for the work jump: where it resolved (mt) for resolved nodes, the newest
  // activity (last) for open ones — matching the newest-seg work anchor (the user 2026-07-20)
  const resolveT = (resolved && node.mt) ? node.mt : (node.last || node.t);
  // anchorUuid can arrive null for a beat (the kernel's cache-only parse goes cold on every transcript
  // write); fall to the stored promptAnchorUuid rather than dispatch a null the chat can only toast on
  // (the user 2026-07-20: three dead clicks on a blocked sub's ⏸ mark in one cold beat).
  const goWork = (ev: Event) => { ev.stopPropagation(); focusEcho(navSid); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: navId, sid: navSid, t: resolveT, anchor: "work", anchorUuid: node.anchorUuid ?? node.promptAnchorUuid ?? null }); };
  // prompt-intent: jump to the minting user message. But a node with no opener (an autonomous note, or an
  // opener compacted off-path) has no promptAnchorUuid, so the jump would honest-fail with "couldn't locate".
  // Fall back to goWork — where the work actually happened — rather than toast (the user 2026-06-30).
  const goMsg = (ev: Event) => {
    if (!node.promptAnchorUuid && node.anchorUuid) { goWork(ev); return; }
    ev.stopPropagation(); focusEcho(navSid); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: navId, sid: navSid, t: node.t, anchor: "prompt", anchorUuid: node.promptAnchorUuid ?? null });
  };
  if (!wire) return goWork;
  // tooltip names the destination by status: a blocked node was "marked blocked", a done node "checked off"
  const workTitle = node.status === "question" && !node.qderived ? "jump to where this got marked blocked"
                  : resolved ? "jump to where this got checked off" : "jump to the latest work on this";
  const linkHover = (group: HTMLElement[]) => {
    const on = () => group.forEach((g) => g.classList.add("lz-hl"));
    const off = () => group.forEach((g) => g.classList.remove("lz-hl"));
    group.forEach((g) => { g.addEventListener("mouseenter", on); g.addEventListener("mouseleave", off); });
  };
  txt.classList.add("lz-nav");
  if (node.status === "done") {
    // DONE: TEXT → the minting message (its own zone); MARK + META jump to where it resolved (goWork),
    // as one pair. Hovering the mark or the time lights BOTH (shared target).
    txt.title = "jump to the message that asked for this"; txt.onclick = goMsg;
    mark.classList.add("lz-nav"); mark.title = workTitle; mark.onclick = goWork;
    if (meta) { meta.classList.add("lz-nav"); meta.title = workTitle; meta.onclick = goWork; }
    linkHover([txt]);
    linkHover(meta ? [mark, meta] : [mark]);
  } else {
    // NOT done (open or blocked): every zone answers "where does this STAND?" — the newest event on the
    // node — not where it was born (the user 2026-07-20: clicking a stale sub's title landed on a bare
    // 'retry' mint prompt, useless for deciding what to do with it). goWork targets the node's newest
    // trail segment (kernel _node_anchor_uuids); the minting ask is still one hop away in the chat.
    txt.title = workTitle; txt.onclick = goWork;
    mark.classList.add("lz-nav"); mark.title = workTitle; mark.onclick = goWork;
    if (meta) { meta.classList.add("lz-nav"); meta.title = "jump to the latest work here"; meta.onclick = goWork; }
    linkHover(meta ? [mark, txt, meta] : [mark, txt]);   // one shared target → one shared highlight
  }
  if (node.cleared) {
    // a cleared node's hover must SAY cleared first — "jump to the message that asked for this" alone
    // read as mystery machinery (the user 2026-07-25); the nav still works, the story leads.
    txt.title = CLEARED_TIP + "; click to jump to the message that asked for it";
  }
  return goWork;
}

function renderTreeNode(box: HTMLElement, it: AskItem, node: AskTreeNode, byId: Map<string, AskTreeNode>, briefs: Map<string, AskQuestion>, seen: Set<string>, depth: number, parentWho: string) {
  const repeat = seen.has(node.id);
  // An optimistically-done sub-goal reads as done for this whole render, by rewriting the node rather
  // than patching the DOM after the fact: the mark, the strike-through class, the "Blocked" label and
  // the action buttons all derive from status, so one substitution keeps them agreeing instead of three
  // separate edits that can drift from how a genuinely-done node draws (see pendingDone).
  if (!repeat && pendingDone.has(node.id) && node.status !== "done") node = { ...node, status: "done" };
  const nodeKey = it.itemId + ":" + node.id;
  const expandable = !repeat && (node.children || []).length > 0;
  const line = el("div", "ftree-node st-" + nodeStatusClass(node) + (repeat ? " repeat" : "") + (depth === 0 ? " ftree-root" : "") + (node.derived ? " derived" : "") + (node.auth ? " auth-" + node.auth : ""));
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
  // blocked rolls UP (kernel flatten, the user 2026-07-11): a rolled-up ancestor's ⏸ says the block is below
  if (node.status === "question") mark.title = node.qderived ? "a sub-goal inside is blocked — the ⏸ below is the ask" : "blocked — needs you";
  const txt = el("span", "ftree-text"); txt.textContent = node.text || "(node)"; line.appendChild(txt);
  if (node.cleared) line.appendChild(clearedTag());   // same one-word story as the card checklist
  if (node.parked && node.parked.n && !node.cleared) line.appendChild(parkedTag(node.parked.n));   // and the parked hint
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
  // a node needing the user reads as "Blocked" (red) — the marker + this label are the block
  // signal, distinct from a recency-tinted age (the user 2026-06-17). Other states show "(Xm ago)".
  meta.textContent = node.status === "question" ? (node.qderived ? "Blocked inside" : "Blocked") : "(" + relAge(hostNow - node.last) + ")";
  if (node.status !== "question" && node.trgb) meta.style.color = "rgb(" + node.trgb.join(",") + ")";   // Hawaii recency tint
  line.appendChild(meta);
  // Whole-line click NAVIGATES into the chat. PREFERRED: node.anchorUuid (kernel 996ebd7) deep-links to
  // the EXACT turn by id — where the node resolved (done/blocked) or was minted (open) — killing the
  // nearest-time mismatch. FALLBACK (anchorUuid null/off-path): the time path below. anchor:"work"
  // lands on the ASSISTANT turn, never the user prompt (the user 2026-06-16, who wanted blocked and
  // completed things to jump to places in the chat that are NOT the user's message). A blocked/done
  // node sends node.mt — the segment where the planner applied the block/done op, i.e. where the work
  // actually got blocked or finished — so the click lands on THAT assistant action, not where the node
  // was first minted. An open node sends node.t (its own start). navSid is the node's session
  // (a handoff node lives in the recipient's transcript).
  // Click/hover zones for this node, via the SHARED wireNodeZones (so the card's sub-goal checklist clicks
  // identically). Returns goWork; the inline rationale below links to the same place. (the user 2026-06-17.)
  const goWork = wireNodeZones(it, node, mark, txt, meta, !repeat);
  // NON-DONE-node surgical actions in the MODAL tree (the user 2026-06-17, widened from blocked-only
  // 2026-07-20: stale OPEN subs were unactionable — 'I don't know what to do with them'): act on a
  // SPECIFIC sub-goal, not just the whole card. Wired AROUND the shared wireNodeZones (which the
  // card checklist also uses) so only the modal flips — the card checklist + ledger marks stay pure-nav.
  // Skips repeats (dim back-links) and handoff nodes (those resolve in another session's store). A rolled-UP
  // question ancestor (qderived — the block lives in a descendant) gets NO action buttons: "Done" there would
  // resolve the whole subtree and "Follow up" would file the answer off-target; the actual ask below has them.
  if (!repeat && node.status !== "done" && !node.cleared && !node.qderived && node.kind !== "handoff") {
    // The MARK stays pure NAV here (it keeps wireNodeZones → jump to the latest work), EXACTLY like the
    // main card — clicking a node in the modal no longer silently crosses it off, which was confusing (the
    // user 2026-06-29). Instead explicit BUTTONS sit on the line: "Done" crosses it off, "Drop" clears it,
    // "Status?" asks the session where it stands, "Follow up" answers just this sub-goal.
    const acts = el("span", "ftree-node-acts");
    // Done/Drop/Status? are SUB-TASK-ONLY (the user 2026-06-30): not on the TOP-LEVEL goal (the tree root) —
    // the card's own Clear resolves the whole goal, and the card-level "Check status" sweeps every open sub.
    // The root is it.tree[0]; in the skip-root single-ask modal it's never drawn here anyway, so every drawn
    // node IS a sub-task and keeps them. "Follow up" stays on every non-done node.
    const isRoot = node.id === it.tree?.[0]?.id;
    if (!isRoot) {
      // "Done": post nodeOverride op:resolve — the kernel marks the node resolved + clears the block + re-rolls
      // inline (no judge pass; bugs owns the handler 3dded52). Immediate-apply (no draft to lose on a re-render).
      const done = el("button", "ftree-act-btn ftree-act-done"); done.textContent = "Done";
      done.title = node.status === "question"
        ? "mark this sub-goal done — it stops blocking and the thread's other work continues"
        : "mark this sub-goal done — you're asserting it's finished";
      // Crosses off IMMEDIATELY, then reconciles (the user 2026-07-23, for whom this felt very slow): the
      // click used to post and paint nothing, so the tick waited on a store write, a rollup and a full
      // feed rebuild. Drop and Check status beside it already acknowledged instantly; this was the odd
      // one out. The kernel's ack is what clears or reverts it — see nodeOverrideResult.
      done.onclick = (ev) => {
        ev.stopPropagation();
        pendingDone.add(node.id);
        vscodeApi?.postMessage({ type: "nodeOverride", sid: it.sid, nodeId: node.id, op: "resolve" });
        renderModal();   // repaint through the same path a real done takes, so it looks identical
      };
      // "Drop": the item-level clear (the user 2026-07-20, who wanted it as an item-level clear) —
      // clears it as no-longer-needed via the same user-authority seam as a card Clear. Acknowledges
      // instantly (fades + strikes the line; the mark stays honest); the kernel push confirms.
      const drop = el("button", "ftree-act-btn ftree-act-drop"); drop.textContent = "Drop";
      drop.title = "clear this sub-goal off the board — no longer needed, without claiming it was done";
      drop.onclick = (ev) => {
        ev.stopPropagation();
        vscodeApi?.postMessage({ type: "nodeOverride", sid: it.sid, nodeId: node.id, op: "clear" });
        // the mark is left ALONE — the box means done, and dropping doesn't finish anything
        line.classList.remove("st-open", "st-question"); acts.remove();
        line.classList.add("st-cleared");
        line.appendChild(clearedTag());   // instant ack wears the same tag the re-render will draw
      };
      // "Check status": ONE-CLICK targeted ask — the session states where this item stands and the judge
      // files the reply on this node (the same askFollowUp + romp-goal-id path a typed per-sub follow-up
      // rides). Named "Check status" to match the modal footer's sweep, not the old "Status?" (the user
      // 2026-07-21: one verb for the same act, whether it sweeps the card or checks a single sub-goal).
      const stat = el("button", "ftree-act-btn ftree-act-status") as HTMLButtonElement; stat.textContent = "Check status";
      stat.title = "ask the session where this item stands right now — the reply files back onto this sub-goal";
      stat.onclick = (ev) => {
        ev.stopPropagation();
        vscodeApi?.postMessage({ type: "askFollowUp", itemId: node.id, title: node.text || "(sub-goal)",
                                 text: statusAskOne(node.text || "this sub-goal"), sid: it.sid });
        stat.disabled = true; stat.textContent = "Asked";   // instant ack; the ↻ Followed up chip takes over on the next push
        optimisticFollowMove(it.itemId);
      };
      acts.append(done, drop, stat);
    }
    // "Follow up": re-target the footer composer at THIS sub so the answer files under it and unblocks just
    // this branch (the judge reopens + force-files under any node id — no kernel change).
    const fu = el("button", "ftree-act-btn ftree-act-fup"); fu.textContent = "Follow up";
    fu.title = node.status === "question" ? "follow up on this specific blocked sub-goal" : "follow up on this specific sub-goal";
    fu.onclick = (ev) => { ev.stopPropagation(); openSubFollowUp?.(node.id, node.text || "(sub-goal)"); };
    acts.append(fu);
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
    line.addEventListener("mouseenter", () => vscodeApi?.postMessage({ type: "showAskPath", itemId: node.id, sid: it.sid, locate: false }));
    line.addEventListener("mouseleave", () => vscodeApi?.postMessage({ type: "showAskPath", itemId: it.itemId, sid: it.sid, locate: false }));
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
  // The card's BACKGROUND section always shows in the MODAL (the user 2026-07-02): the root node renders
  // both sections in full, labeled "background" / "summary" — no collapsing here, the modal is the
  // full-detail view. Only the root carries it (it.background is a top-goal field).
  const modalBg = node.id === it.itemId && nodeDistill && it.background ? it.background : null;
  if (modalBg) {
    const bl = el("div", "ftree-seclabel"); bl.textContent = "background";
    bl.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
    const bb = el("div", "ftree-summary"); bb.textContent = modalBg;
    bb.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
    const sl = el("div", "ftree-seclabel"); sl.textContent = "summary";
    sl.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
    box.append(bl, bb, sl);
  }
  if (nodeDistill) {
    const sum = el("div", "ftree-summary");
    sum.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
    sum.textContent = nodeDistill;
    // parity with the card's distiller line (the user 2026-06-29): the modal summary is also a LINK — clicking
    // it follows to where the node resolved (its work anchor, the SAME target as the node's mark/time zones).
    // Wired on EITHER anchor: goWork itself falls to the prompt anchor when the work one is cold-null, so
    // gating on anchorUuid alone dead-ended the line during a cold beat (the user 2026-07-20).
    if (!repeat && (node.anchorUuid || node.promptAnchorUuid)) {
      sum.classList.add("ftree-summary-link");
      sum.title = "jump to where this was written";
      sum.onclick = goWork;
    }
    box.appendChild(sum);
  }
  // The per-item STORY (the user 2026-07-20: 'I don't know if they're still active'): a non-done node
  // with verdict history shows a one-line gist — its newest event in outcome words + how long ago —
  // expandable (keyed, survives re-renders) to the full block/unblock log, each row jumping to its own
  // chat turn (exact anchor when warm, ev-time nearest otherwise). Progressive disclosure: gist →
  // history → transcript, each one click.
  if (!repeat && node.status !== "done" && !node.cleared && node.log && node.log.length) {
    const rows = node.log;
    const last = rows[rows.length - 1];
    const opened = nodeLogOpen.has(nodeKey);
    const gist = el("div", "ftree-log-gist" + (opened ? " open" : ""));
    gist.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
    gist.textContent = (opened ? "▾ " : "▸ ") + logPhrase(last) + " · " + relAge(hostNow - logRowT(last));
    gist.title = opened ? "collapse this item's history" : "expand this item's history";
    gist.onclick = (ev) => { ev.stopPropagation(); if (opened) nodeLogOpen.delete(nodeKey); else nodeLogOpen.add(nodeKey); render(); };
    box.appendChild(gist);
    if (opened) {
      for (const r of rows) {
        const rt = logRowT(r);
        const row = el("div", "ftree-log-row");
        row.style.paddingLeft = ((depth + 1) * TREE_INDENT_EM) + "em";
        const when = el("span", "ftree-log-when"); when.textContent = relAge(hostNow - rt);
        const what = el("span", "ftree-log-what"); what.textContent = logPhrase(r) + (r.why ? " — " + r.why : "");
        row.append(when, what);
        row.title = "jump to this moment in the chat";
        row.onclick = (ev) => {
          ev.stopPropagation();
          focusEcho(navSidOf(it, node));
          vscodeApi?.postMessage({ type: "showOnTimeline", itemId: node.id, sid: navSidOf(it, node),
                                   t: r.evT || rt, anchor: "work", anchorUuid: r.anchorUuid ?? null });
        };
        box.appendChild(row);
      }
    }
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
  // …OR a synthetic blocked-session card ("b:<sid>") — its modal is the ERROR
  // explanation (what's suspicious + what to note for a correction), nothing else.
  const it = (fullscreenAskId && !fullscreenAskId.startsWith("g:") && !fullscreenAskId.startsWith("i:"))
    ? asks.find((a) => a.itemId === fullscreenAskId) : null;
  if (!it && !grp) { if (m) { m.remove(); hoverEmit(null); } modalRenderedId = null; return; }   // closed / dissolved → clear hover highlight
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
    // (The "Move to Working" button was removed — the user 2026-07-25; reply or Clear cover its cases.)
    // "Check status" (the user 2026-07-20): the card-level sweep, in the modal too — one message asking
    // the session where every open/blocked item stands. The single-ask branch wires + shows it.
    const cs = el("button", "fdismiss feed-modal-status"); cs.id = "feed-modal-status"; cs.textContent = "Check status";
    cs.title = "ask this session where every open item on this card stands — replies file back onto the card";
    cs.style.display = "none";
    const clr = el("button", "fdismiss feed-modal-clear"); clr.id = "feed-modal-clear"; clr.textContent = "Clear";
    // "Continue" (the user 2026-08-08): the card's one-click "nothing needed from me, keep going", in the
    // modal too. Left of Clear, matching the card's action corner. The single-ask branch wires + shows it.
    const cont = el("button", "fdismiss feed-modal-continue"); cont.id = "feed-modal-continue"; cont.textContent = "Continue";
    cont.title = "nothing needed from you — tells this session to keep going and decide open questions itself";
    cont.style.display = "none";
    const footRow = el("div", "feed-modal-foot-row"); footRow.append(age, fup, cs, cont, clr);
    const fubox = el("div", "ffollow-box feed-modal-follow-box"); fubox.id = "feed-modal-follow-box"; fubox.style.display = "none";
    const fuin = el("textarea", "fq-input feed-modal-follow-input") as HTMLTextAreaElement; fuin.id = "feed-modal-follow-input"; fuin.placeholder = "follow up on this…"; fuin.rows = 1;
    fuin.addEventListener("input", () => growFollowUp(fuin));
    const fusend = el("button", "fq-send feed-modal-follow-send"); fusend.id = "feed-modal-follow-send"; fusend.textContent = "Send";
    fubox.append(fuin, fusend);
    // when a blocked sub is the follow-up target, this label says so (click → revert to the whole card)
    const futgt = el("div", "feed-modal-follow-target"); futgt.id = "feed-modal-follow-target"; futgt.style.display = "none";
    const nudges = el("div", "feed-modal-nudges"); nudges.id = "feed-modal-nudges"; nudges.style.display = "none";
    const foot = el("div", "feed-modal-foot"); foot.id = "feed-modal-foot"; foot.append(nudges, footRow, futgt, fubox);
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
        // fold any non-root node that HAS a subtree to hide (one-level default view). `n.rows` was removed
        // from AskTreeNode in the payload audit — reading `.length` off the now-undefined field threw and
        // ABORTED the whole modal render → a blank modal (the user 2026-07-08; the typecheck flagged it but
        // esbuild doesn't type-check, so it shipped). Children alone decide foldability now.
        else if ((n.children || []).length) collapsedNodes.add(key);
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
  // nudge HISTORY (the user 2026-07-02): the stalled chip's evidence, one click away — when romp followed
  // up on this goal. Hidden unless the single-ask target has recorded fires.
  const nudEl = document.getElementById("feed-modal-nudges") as HTMLElement | null;
  if (nudEl) {
    const nu = it?.nudged;
    if (nu && nu.times.length) {
      nudEl.textContent = `romp followed up ${nu.count}× — ${nu.times.map(clockHM).join(", ")}`;
      nudEl.title = "automatic follow-ups romp sent on this goal (they render in the chat as gray romp bubbles with the swirl)";
      nudEl.style.display = "";
    } else {
      nudEl.style.display = "none";
    }
  }
  const ttlEl = document.getElementById("feed-modal-title") as HTMLElement;
  const agent = document.getElementById("feed-modal-agent") as HTMLElement;
  const ageEl = document.getElementById("feed-modal-age") as HTMLElement;
  const clrEl = document.getElementById("feed-modal-clear") as HTMLElement;
  const csEl = document.getElementById("feed-modal-status") as HTMLButtonElement | null;
  const contEl = document.getElementById("feed-modal-continue") as HTMLButtonElement | null;
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
  const postFollowUp = (txt: string, fbId: string, fbSid?: string, fbTitle?: string) => {
    const tgt = followupSub;   // a sub-goal belongs to the same session → same sid routes it
    vscodeApi?.postMessage({ type: "askFollowUp", itemId: tgt ? tgt.itemId : fbId, title: tgt ? tgt.title : fbTitle, text: txt, sid: fbSid });
    // Optimistically move THIS card (fbId is the visible card/group, even when a sub-goal is the message target)
    // to Working now, then re-render the feed so it slides over immediately — the kernel reconciles on its push.
    optimisticFollowMove(fbId);
    render();
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
  // default-hidden + reset every render; only the single-ask branch shows it (group/standalone never do)
  if (csEl) { csEl.style.display = "none"; }   // disabled/label NOT reset here — "Asked" survives the per-push re-render
  if (contEl) { contEl.style.display = "none"; }   // same contract as Check status: "Sent" survives re-renders
  let titleHoverId: string | null = null;   // the originating typed turn → chat/timeline hover highlight
  if (grp) {
    ttlEl.textContent = grp.title;
    titleHoverId = grp.turnId;
    const gm0 = grp.members[0];   // prompt-intent title → the first member's MINTING message (resolves by id, kernel 92e23ff)
    const gm0Prompt = gm0.tree?.find((n) => n.id === gm0.itemId)?.promptAnchorUuid ?? null;
    ttlEl.onclick = () => focusEcho(grp.sid); vscodeApi?.postMessage({ type: "showOnTimeline", itemId: gm0.itemId, sid: grp.sid, t: grp.t, anchor: "prompt", anchorUuid: gm0Prompt });
    agent.replaceChildren(...hostNameNodes(grp.name, grp.sid)); if (grp.color) agent.style.color = grp.color.bg; setWorkDot(agent, dotFor(grp.name)); agent.classList.toggle("dead", !grp.live);
    agent.onclick = () => vscodeApi?.postMessage({ type: "openSession", id: grp.sid });
    ageEl.textContent = relAge(hostNow - grp.t);
    wireAgeTip(ageEl, () => provenanceGroupRows(grp.members.map(rootStart), grp.t, hostNow, PROV_FMT));
    ageEl.style.color = "rgb(" + grp.trgb.join(",") + ")";   // tint the age by recency (the time colour scheme)
    clrEl.onclick = () => { for (const mem of grp.members) vscodeApi?.postMessage({ type: "askClear", itemId: mem.itemId, sid: mem.sid }); fullscreenAskId = null; renderModal(); };
    // follow-up on a group goes to the session that took the typed prompt — one
    // message prefixed with the GROUP title, filed under the first member's ask
    wireFollowUp(fupEl, fuboxEl, fuinEl, fusendEl, (txt) => postFollowUp(txt, grp.members[0].itemId, grp.members[0].sid, grp.title));
    renderGroupModalBody(body, grp.members);
  } else if (it) {
    // The top-level goal IS the modal: render it as the ROOT of the tree list (not a separate header
    // title), so a goal with no sub-work is just one list line carrying its own done/blocked state, and
    // any sub-goals render beneath it as the rest of the list (the user 2026-06-16). The header above the
    // tree is only the session name + a recency-tinted age; Follow up moved to the footer below the tree.
    ttlEl.style.display = "none";
    titleHoverId = it.turnId;
    agent.replaceChildren(...hostNameNodes(it.name, it.sid)); if (it.color) agent.style.color = it.color.bg; setWorkDot(agent, dotFor(it.name)); agent.classList.toggle("dead", !it.live);
    agent.onclick = () => vscodeApi?.postMessage({ type: "openSession", id: it.sid });
    ageEl.textContent = relAge(hostNow - it.t);
    wireAgeTip(ageEl, () => provenanceRows(it, hostNow, PROV_FMT));
    ageEl.style.color = "rgb(" + it.trgb.join(",") + ")";   // tint the age by recency (the time colour scheme)
    clrEl.onclick = () => { vscodeApi?.postMessage({ type: "askClear", itemId: it.itemId, sid: it.sid }); fullscreenAskId = null; renderModal(); };
    // "Check status" (the user 2026-07-20): shown when the card has open/blocked subs to sweep and the
    // session is live to answer. Same ack + re-arm contract as the card button (event-based: the judge's
    // re-file clears the asked state via the fresh modal render).
    if (csEl && it.live && statusSweepText(it).n > 0) {
      csEl.style.display = "";
      if (csEl.disabled && !it.followupPending && !it.recheck && !it.rejudging) { csEl.disabled = false; csEl.textContent = "Check status"; }
      csEl.title = contTitle(csEl.disabled, "a status ask", it.followupAt);
      csEl.onclick = () => {
        const sweep = statusSweepText(it);
        if (!sweep.n) return;
        vscodeApi?.postMessage({ type: "askFollowUp", itemId: it.itemId, text: sweep.text, sid: it.sid });
        csEl.disabled = true; csEl.textContent = "Asked";
        csEl.title = contTitle(true, "a status ask", null);
        optimisticFollowMove(it.itemId);
        render();
      };
    }
    // "Continue" — same gating as the card's button (live needs-you, no live ask), same ack contract.
    if (contEl && askColumn(it) === "needsInput" && it.live && !it.provisional && !it.blocked) {
      contEl.style.display = "";
      if (contEl.disabled && !it.followupPending && !it.recheck && !it.rejudging) { contEl.disabled = false; contEl.textContent = "Continue"; }
      contEl.title = contTitle(contEl.disabled, "a continue", it.followupAt);
      contEl.onclick = () => {
        vscodeApi?.postMessage({ type: "askFollowUp", itemId: it.itemId, sid: it.sid, cont: true });
        contEl.disabled = true; contEl.textContent = "Sent";
        contEl.title = contTitle(true, "a continue", null);
        optimisticFollowMove(it.itemId);
        render();
      };
    }
    // follow-up works in ANY state (the user 2026-06-10) — asks, awaiting, or completed;
    // toggling the button reveals the composer.
    wireFollowUp(fupEl, fuboxEl, fuinEl, fusendEl, (txt) => postFollowUp(txt, it.itemId, it.sid));
    renderTreeBody(body, it, false);   // root goal IS the first list line; sub-goals render beneath it
    applyModalWarnings(body, it);      // debug mode: this card's judge failures, input+reply expandable (the user 2026-07-09)
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

// A column entry is an ask card or a group card; the reconcile picks the right
// builder + cache map by kind.
type Entry =
  | { kind: "ask"; t: number; ask: AskItem }
  | { kind: "group"; t: number; group: AskGroup }
  // a SESSION HEADER row in grouped mode (the user 2026-07-13): the session's name + working dot on the
  // column backdrop, heading that session's run of cards. Only emitted for runs that exist.
  // `folded` = how many of this run's cards the header is standing in for (0 when the thread is expanded)
  | { kind: "sess"; t: number; sid: string; name: string; color: { bg: string; fg: string } | null; live: boolean; folded: number };

// ONE counting rule (the user 2026-08-26): every number on the board counts CARDS, never rows — a
// turn-group entry is worth its members, a folded session header is worth the cards it stands in for.
// Both the section chips and the fold accumulator read THIS, so expanded and collapsed can never
// disagree (the traced bug: the chip counted a group as 1 row while the fold header said "cards").
function entryCards(e: Entry): number {
  return e.kind === "sess" ? e.folded : e.kind === "group" ? e.group.members.length : 1;
}

// Grouped-mode session headers, one reused element per (column, sid) — same keyed-incremental treatment as
// cards so re-renders never rebuild a header mid-press. Pruned when a reconcile drops them from the DOM.
const sessHeadEls = new Map<string, HTMLElement>();
function makeSessHead(): HTMLElement {
  const h = el("div", "feed-sess-head");
  const nm = el("a", "fname"); nm.title = "open this session";
  // Neutral background-process chip (the user 2026-07-24): processes this session keeps running that the
  // judge classified as SERVICES (kernel bgServices — a dev server nobody waits on). Session furniture, so
  // it rides the header, never a card, and wears NO waiting/urgency framing — the .fask-secbtn chip
  // vocabulary, dim by default. Click expands the process list (keyed expand, survives re-renders).
  const svc = el("button", "fask-secbtn feed-sess-svcbtn"); svc.style.display = "none";
  const svcList = el("div", "feed-sess-svclist"); svcList.style.display = "none";
  // FOLD control (the user 2026-07-31), immediately right of the name where they asked for it. A caret is
  // the universal collapse affordance and costs one glyph, where a word chip would repeat on every header;
  // it is the same typographic vocabulary as the sub-goal triangles, never an emoji. Always drawn rather
  // than shown on hover: a hover-only control is invisible on the phone, which is where the feed is read
  // most. The count beside it is what keeps the folded row from dead-ending — you can see what is under it.
  const fold = el("button", "feed-sess-fold");
  const cnt = el("span", "feed-sess-foldn"); cnt.style.display = "none";
  h.append(nm, fold, cnt, svc, svcList);
  (h as any)._name = nm; (h as any)._fold = fold; (h as any)._foldn = cnt;
  (h as any)._svc = svc; (h as any)._svcList = svcList;
  return h;
}
function updateSessHead(h: HTMLElement, e: Entry & { kind: "sess" }): void {
  h.setAttribute("data-fsid", e.sid);   // the hover-freeze badge painter finds headers by sid
  const nm = (h as any)._name as HTMLElement;
  nm.replaceChildren(...hostNameNodes(e.name, e.sid));
  if (e.color) nm.style.color = e.color.bg;
  nm.classList.toggle("dead", !e.live);
  nm.onclick = (ev) => { ev.stopPropagation(); openOrReviveSession(e.sid, e.live, e.name); };
  setWorkDot(nm, dotFor(e.name));   // the working/awaiting dot rides the header, not the cards
  // the fold caret + the "n cards" stand-in for what it hides
  const fold = (h as any)._fold as HTMLElement, foldn = (h as any)._foldn as HTMLElement;
  const shut = collapsedThreads.has(e.sid);
  h.classList.toggle("folded", shut);
  fold.textContent = shut ? "▸" : "▾";           // ▸ folded / ▾ open
  fold.title = shut ? "show this session's cards" : "collapse this session to its name — new cards stay folded too";
  fold.setAttribute("aria-expanded", shut ? "false" : "true");
  fold.setAttribute("aria-label", (shut ? "expand " : "collapse ") + e.name);
  foldn.style.display = shut && e.folded ? "" : "none";
  // just the number (the user 2026-08-26) — the section chips' own vocabulary; the words live on hover
  foldn.textContent = String(e.folded);
  foldn.title = e.folded === 1 ? "1 card folded under this session" : e.folded + " cards folded under this session";
  fold.onclick = (ev) => {
    ev.stopPropagation();   // the fold IS the acknowledgement: local state + an immediate re-render
    if (collapsedThreads.has(e.sid)) collapsedThreads.delete(e.sid); else collapsedThreads.add(e.sid);
    render();
  };
  const svc = (h as any)._svc as HTMLElement, svcList = (h as any)._svcList as HTMLElement;
  const procs = e.live ? bgServicesMap[e.name] || [] : [];
  const open = procs.length > 0 && openBgSvc.has(e.sid);
  svc.style.display = procs.length ? "" : "none";
  svc.textContent = procs.length === 1 ? "background process" : procs.length + " background processes";
  svc.title = open ? "hide the processes" : "processes this session keeps running — click to list";
  svc.classList.toggle("on", open);
  svc.setAttribute("aria-pressed", open ? "true" : "false");
  svc.onclick = (ev) => {
    ev.stopPropagation();   // the expand IS the acknowledgement: local state + immediate re-render
    if (openBgSvc.has(e.sid)) openBgSvc.delete(e.sid); else openBgSvc.add(e.sid);
    render();
  };
  svcList.style.display = open ? "" : "none";
  if (open) svcList.replaceChildren(...procs.map((d) => {
    const r = el("div", "feed-sess-svcrow"); r.textContent = d; return r;
  }));
}

// Undo (top right): restore the most recently cleared card — the host pops
// the newest cleared.jsonl row. Built fresh wherever the top strip renders: on
// the legend row when columns exist, and on the empty state too (clearing the
// LAST card is exactly when undo is wanted). One word (the user 2026-08-24,
// dropping "Undo clear"): it sits right beside Clear all — context enough.
function makeUndoClearBtn(): HTMLElement {
  const b = el("button", "fdismiss ffollow");   // restorative → blue hover (.ffollow), not Clear's red
  b.id = "feed-undoclear";
  b.textContent = "Undo";
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
      // WORKING cue (the user 2026-07-31): with nothing in this page's cache the restore is a full
      // kernel round-trip and the button read dead for a beat (the optimistic branch above needs no
      // cue — its card appears instantly). Three pulsing accent dots + an accent border say "on it"
      // WITHOUT disabling: each further click keeps popping older batches. Cleared by the NEXT feed
      // payload (the push undoClear triggers — the event this cue is waiting for), with a timeout
      // backstop so a lost push can never trap it.
      b.classList.add("undo-busy");
      if (!b.querySelector(".undo-dots")) {
        const d = el("span", "undo-dots");
        d.append(el("i"), el("i"), el("i"));
        b.appendChild(d);
      }
      window.clearTimeout(undoBusyBackstop);
      undoBusyBackstop = window.setTimeout(clearUndoBusy, 6000);
    }
    vscodeApi?.postMessage({ type: "undoClear" });
  };
  return b;
}

let undoBusyBackstop = 0;
function clearUndoBusy(): void {
  window.clearTimeout(undoBusyBackstop);
  const b = document.getElementById("feed-undoclear");
  if (b) { b.classList.remove("undo-busy"); b.querySelector(".undo-dots")?.remove(); }
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
// Undo restores the whole batch (the host clears them as one cleared.jsonl batch).
function makeClearAllBtn(): HTMLElement {
  const b = el("button", "fdismiss");
  b.id = "feed-clearall";
  b.textContent = "Clear all";
  b.title = "clear every open card (inbox-zero) — Undo restores them";
  b.onclick = (ev) => { ev.stopPropagation(); vscodeApi?.postMessage({ type: "clearAll" }); };
  return b;
}

function ensureClearAll(): HTMLElement {
  let b = document.getElementById("feed-clearall");
  if (!b) { b = makeClearAllBtn(); (document.getElementById("feed-foot") || document.body).appendChild(b); }
  return b;
}

// The feed's mount of the SHARED tag-lens menu (tag-menu.ts — one component every surface mounts;
// the user's generalized design). The button is the shared monochrome tag glyph; active (a narrowed
// lens) wears the accent like every footer .on state. The menu inherits cross-pane dismissal free
// (romp:menu-echo). Configure routes to the tags dialog through the kernel (openTagsDialog).
function ensureTagLensBtn(): HTMLElement {
  let b = document.getElementById("feed-taglens") as HTMLElement | null;
  if (!b) {
    b = tagMenuButton("filter this board by tag — combinations union; All shows everything", (btn) => {
      openTagMenu(btn, {
        lens: () => feedLens,
        unions: () => lensUnions(feedTagViews),
        onApply: (l) => { setFeedLens(l); render(); },
        onConfigure: () => vscodeApi?.postMessage({ type: "openTagsDialog" }),
      });
    });
    b.id = "feed-taglens";
    // FOOTER DRESS (the user 2026-08-25, round two: at rest the button outlined blue, and All faded
    // it darker than its neighbours): the shared component ships its own inline style, and inline
    // beats every class rule — strip it and wear the EXACT sibling vocabulary, so the resting state
    // is the neighbours' computed style by construction and can never drift dark again. Active is
    // the standard .on accent ONLY — the class the siblings use, never an inline colour.
    b.removeAttribute("style");
    b.className = "fdismiss ffollow feed-modetoggle";
    (document.getElementById("feed-foot") || document.body).appendChild(b);
  }
  // THE BUTTON CONVENTION (the user 2026-08-25), shared renderer — subsumes the 696 instance
  // toggle: same .on class mechanics (mode "class", so the footer's sibling dress stands), plus
  // the chips of everything selected beside the button, identical to every other mount
  let ch = document.getElementById("feed-tagchips") as HTMLElement | null;
  if (!ch) {
    ch = document.createElement("span");
    ch.id = "feed-tagchips";
    ch.setAttribute("style", "display:inline-flex;gap:5px;align-items:center;margin-left:2px;");
    b.after(ch);
  }
  syncTagFilter(b, ch, feedLens, lensUnions(feedTagViews) as never, (l) => { setFeedLens(l); render(); }, "class");
  return b;
}

// The footer VIEW MENU (the user 2026-08-24): the three view controls — sort direction, single-column
// layout, by-session grouping — live behind ONE monochrome icon button now, a popup wearing the shared
// .ctx-menu vocabulary, where three word-buttons crowded the footer and the labels can breathe.
// Prefs still write the shared romp:settings (the ⛭ gear's watcher and other panes read the same keys).
// The button is ensure-once; the menu lives on document.body outside render()'s reconcile, opening
// upward like the session menu (click-safety: a push can never rebuild it mid-press).
function setViewPref(key: string, on: boolean, after?: (on: boolean) => void): void {
  try {
    const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
    s[key] = on;
    localStorage.setItem("romp:settings", JSON.stringify(s));
  } catch { /* ignore */ }
  after?.(on);   // runs BEFORE the re-render (e.g. apply the stack style var), on the NEW value
  window.dispatchEvent(new Event("romp:settings"));   // same-doc signal -> re-render (order / layout / grouping)
}
// "Single column view" (the user 2026-08-18, the footer "Stack" button then): force the one-column
// layout at ANY width — the same stacked view the narrow container query produces, as a standing
// choice. The pref drives a style() container condition on #feed-list (see feed.css), so the CSS
// stays the single owner of what stacking means; the narrow query still stacks regardless.
function applyStacked(on: boolean) {
  document.getElementById("feed-list")?.style.setProperty("--romp-stack", on ? "on" : "off");
}
// FORCED stacking (the user 2026-08-19): at or under the container query's own 540px the layout
// stacks regardless of the pref, so the toggle is a no-op there — the menu row says so instead of
// lying: still ✓-checked (stacking IS on), faded, inert, tooltip naming the way out (more width).
// Width changes are the event — a ResizeObserver on #feed-list, installed once.
const STACK_FORCED_W = 540;   // MUST match feed.css's @container (max-width: 540px) stack query
let stackResizeWatch: ResizeObserver | null = null;
let stackForced = false;
function refreshStackForced(): void {
  const list = document.getElementById("feed-list");
  const forced = !!list && list.clientWidth > 0 && list.clientWidth <= STACK_FORCED_W;
  if (forced === stackForced) return;
  stackForced = forced;
  if (viewMenuEl) paintViewMenu(viewMenuEl);   // an open menu repaints on the deciding event
}
// (The "Collapsed" default-section toggle moved into the settings modal, 2026-08-18 — a set-and-forget
// preference, not a per-glance view action. The pref and its behavior are unchanged; the gear writes
// romp:settings.collapsed and the settings watcher below drops the per-card overrides on change.)

let viewMenuEl: HTMLElement | null = null;
function closeViewMenu(): void {
  if (viewMenuEl?.contains(document.activeElement)) document.getElementById("feed-viewbtn")?.focus();
  viewMenuEl?.remove(); viewMenuEl = null;
  document.removeEventListener("pointerdown", viewMenuAway, true);
  document.removeEventListener("keydown", viewMenuKey, true);
}
function viewMenuAway(ev: Event): void {
  const t = ev.target as Node;
  if (viewMenuEl && !viewMenuEl.contains(t) && !(document.getElementById("feed-viewbtn")?.contains(t))) closeViewMenu();
}
function viewMenuKey(ev: KeyboardEvent): void { if (ev.key === "Escape") closeViewMenu(); }
// The rows: "Sort by most recent ↓/↑" keeps the Modified button's rule (the user 2026-08-18) — both
// directions are valid sorts, so the arrow IS the state and the row never wears the ✓; "Single column
// view" and "Group by session" are ✓ rows over the same prefs the old Stack/Group buttons wrote
// ("Group" organizes each column BY SESSION, the user 2026-07-13 — kernel tab/lane order, a name+dot
// header opening each session's run; grouped still defaults ON via feedPrefs' !== false, so the ✓
// reads exactly the way the pressed button did). Rows are real <button>s — Tab-reachable and
// Enter/Space-activatable, the operability the replaced footer buttons had — built ONCE per open and
// then synced IN PLACE (paintViewMenu below), so a live repaint (a settings change from another pane,
// the width crossing 540px) never swaps a row out from under a pressed pointer (click-safety), and a
// click always acts on the prefs AS OF the click, not as of the last paint.
function buildViewMenu(menu: HTMLElement): void {
  const mk = (check: boolean, act: () => void): HTMLElement => {
    const r = el("button", "ctx-item");
    (r as HTMLButtonElement).type = "button";
    r.setAttribute("role", check ? "menuitemcheckbox" : "menuitem");
    r.onclick = (ev) => {
      ev.stopPropagation();
      if (r.classList.contains("forced")) return;   // stacking is automatic at this width — a no-op toggle lies
      act();
      closeViewMenu();
    };
    menu.appendChild(r);
    return r;
  };
  mk(false, () => setViewPref("newestFirst", !feedPrefs().newestFirst));
  mk(true, () => setViewPref("stacked", !feedPrefs().stacked, applyStacked));
  mk(true, () => setViewPref("grouped", !feedPrefs().grouped));
}
// Sync the three rows to the CURRENT prefs — labels, ✓s, the forced state — without rebuilding them.
function paintViewMenu(menu: HTMLElement): void {
  const p = feedPrefs();
  const rows = menu.querySelectorAll(".ctx-item");
  if (rows.length !== 3) return;
  const set = (i: number, label: string, opts: { current: boolean; forced?: boolean; title: string }) => {
    const r = rows[i] as HTMLElement;
    r.textContent = label;
    r.title = opts.title;
    r.classList.toggle("current", opts.current);
    r.classList.toggle("forced", !!opts.forced);
    if (r.getAttribute("role") === "menuitemcheckbox") r.setAttribute("aria-checked", opts.current ? "true" : "false");
    if (opts.forced) r.setAttribute("aria-disabled", "true"); else r.removeAttribute("aria-disabled");
  };
  set(0, "Sort by most recent " + (p.newestFirst ? "\u2193" : "\u2191"), {
    current: false,   // a DIRECTION row — never the ✓-current mark
    title: p.newestFirst ? "newest at the top — click for oldest first" : "oldest at the top — click for newest first",
  });
  set(1, "Single column view", {
    current: p.stacked || stackForced, forced: stackForced,
    title: stackForced ? "stacked automatically at this width — widen the feed to unstack into three columns"
      : p.stacked ? "one-column layout at any width — click for side-by-side columns when the feed is wide"
      : "stack the columns into one, whatever the width",
  });
  set(2, "Group by session", {
    current: p.grouped,
    title: p.grouped ? "grouped by session — click for the flat column order"
      : "group each column's cards by session (tab order), a session header between runs",
  });
}
function openViewMenu(btn: HTMLElement): void {
  closeSessList();   // one menu at a time: a keyboard open fires no pointerdown, so the away-closers never ran
  const menu = el("div", "ctx-menu feed-viewmenu");
  menu.setAttribute("role", "menu");
  refreshStackForced();   // decide the forced row from the CURRENT width, not the last observed one
  buildViewMenu(menu);
  paintViewMenu(menu);
  document.body.appendChild(menu);
  // above the footer, left-aligned to the button, clamped into the viewport (the session menu's placement)
  const r = btn.getBoundingClientRect();
  menu.style.bottom = Math.round(window.innerHeight - r.top + 6) + "px";
  menu.style.left = Math.round(Math.max(6, Math.min(r.left, window.innerWidth - menu.offsetWidth - 6))) + "px";
  viewMenuEl = menu;
  (menu.querySelector(".ctx-item") as HTMLElement | null)?.focus();   // a keyboard open lands on the first row
  document.addEventListener("pointerdown", viewMenuAway, true);
  document.addEventListener("keydown", viewMenuKey, true);
}
// A WORD, not an icon (the user 2026-08-24, round two: the ordering glyph beside "Sessions ▴" read
// as "not what I would expect for what to show"): "View ▴" says exactly what clicking does — the
// footer's own word-button vocabulary, the same upward caret its Sessions neighbour wears. The
// original glyph existed to be compact; one short word costs a few px and buys first-look legibility.
function ensureViewMenuBtn(): HTMLElement {
  let b = document.getElementById("feed-viewbtn") as HTMLElement | null;
  if (!b) {
    b = el("button", "fdismiss ffollow feed-modetoggle");
    b.id = "feed-viewbtn";
    b.textContent = "View \u25b4";
    b.title = "view options — sort direction, single column, group by session";
    b.setAttribute("aria-haspopup", "menu");
    b.onclick = (ev) => {   // opening the menu IS the acknowledgement (same as the session filter)
      ev.stopPropagation();
      if (viewMenuEl) closeViewMenu(); else openViewMenu(b!);
    };
    (document.getElementById("feed-foot") || document.body).appendChild(b);
  }
  const list = document.getElementById("feed-list");
  if (!stackResizeWatch && list && typeof ResizeObserver !== "undefined") {
    stackResizeWatch = new ResizeObserver(() => refreshStackForced());
    stackResizeWatch.observe(list);
  }
  return b;
}

// The SESSION COMBOBOX (the user 2026-08-24, merging the 2026-08-08 session picker and the
// 2026-08-23 search box): ONE footer control owns "which sessions am I looking at". Collapsed, a
// single "Sessions ▴" word-button. Open, the top-bar-style input with the session list beneath —
// every row written the way every other surface writes a session (bold name in its identity colour,
// "host:" prefix folded quiet, the shared working/awaiting dot; the current pick wears the accent
// wash, exactly today's menu). TYPING narrows the list AND applies the live substring filter
// (romp:feedSearch — semantics unchanged); PICKING a row applies the exact-session filter
// (romp:feedOnly — semantics unchanged), rendered as a chip in the bar whose ✕ hands the bar back
// to typing. The two filters compose downstream exactly as before (viewFiltered). Invariants kept
// from both parents: an ACTIVE filter of either kind force-opens the bar (a compact state can never
// hide a live filter); Escape folds, and folding with anything live CLEARS it; the wrap is
// ensure-once and the list lives on document.body outside render()'s reconcile (click-safety); the
// list is built ONCE per open and typing only toggles row display, so a keystroke can never rebuild
// a row out from under a press.
let sessListEl: HTMLElement | null = null;
function closeSessList(): void {
  sessListEl?.remove(); sessListEl = null;
  document.removeEventListener("pointerdown", sessListAway, true);
  document.removeEventListener("keydown", sessListKey, true);
}
function sessListAway(ev: Event): void {
  const t = ev.target as Node;
  if (!sessListEl || sessListEl.contains(t) || document.getElementById("feed-search")?.contains(t)) return;
  closeSessList();
  // an away-click closes the LIST only — never a filter-clearing fold: clicking a card must not
  // wipe a live filter (clear-on-fold is for EXPLICIT folds — Escape, the button). With nothing
  // live the bar collapses quietly; force-open keeps it up otherwise.
  if (!feedSearchQ.trim() && !feedOnlySid) document.getElementById("feed-search")?.classList.remove("open");
}
function sessListKey(ev: KeyboardEvent): void { if (ev.key === "Escape") { closeSessList(); foldSessBox(); } }
// A session's name in the list wears EXACTLY the identity treatment every other surface gives it (the
// user 2026-08-08): bold, in the session's own colour, host prefix folded quiet — the way the chat tabs
// and the grouped session headers write it. Never a colour swatch: a dot beside a name on this board
// already MEANS working/awaiting (the shared fwork-dot vocabulary), so that status dot rides here too.
function sessMenuName(s: { sid: string; name: string; color: { bg: string; fg: string } | null }): HTMLElement {
  const nm = el("span", "fsm-name");
  nm.replaceChildren(...hostNameNodes(s.name, s.sid));
  if (s.color) nm.style.color = s.color.bg;
  return nm;
}
// Typing narrows the OPEN list in place: display toggles only, never a rebuild (click-safety). The
// "All sessions" row always shows — the way back can never be filtered away (no-dead-end).
function filterSessList(q: string): void {
  if (!sessListEl) return;
  sessListEl.querySelectorAll<HTMLElement>(".fsm-row").forEach((r) => {
    const name = r.getAttribute("data-name");
    r.style.display = name === null || searchMatches(q, name) ? "" : "none";
  });
}
function repaintSessListCurrent(): void {
  if (!sessListEl) return;
  sessListEl.querySelectorAll<HTMLElement>(".fsm-row").forEach((r) => {
    const on = (r.getAttribute("data-sid") || null) === feedOnlySid;
    r.classList.toggle("on", on);
    r.setAttribute("aria-selected", on ? "true" : "false");   // option rows select; checked is the checkbox pattern
  });
}
function openSessList(): void {
  closeViewMenu();   // one menu at a time: a keyboard open fires no pointerdown, so the away-closers never ran
  const wrap = document.getElementById("feed-search");
  if (!wrap || sessListEl) return;
  const menu = el("div", "feed-sessmenu");
  menu.setAttribute("role", "listbox");
  const row = (pick: string | null, label: HTMLElement, name: string | null, dotName?: string) => {
    const r = el("div", "fsm-row");
    r.appendChild(label);
    if (dotName) setWorkDot(label, dotFor(dotName));   // inserts the status dot before the name, in place
    r.setAttribute("role", "option");
    if (pick !== null) r.setAttribute("data-sid", pick);
    if (name !== null) r.setAttribute("data-name", name);
    // TAG CHIPS inline (the user 2026-08-25 — the unification call's smaller variant): the session's
    // name-keyed union tags as compact outline chips, the dialog's own one-chip-per-name vocabulary,
    // NON-interactive (grouping made visible; the two controls stay separate and the pick is
    // untouched). The strip ellipsizes in the row's leftover space so names stay primary.
    // (Deliberately NOT built, decided: search matching tag names — the pick list stays sessions-only.)
    if (pick !== null) {
      const chips = el("span", "fsm-chips");
      for (const g of lensUnions(feedTagViews)) {
        if (!g.members.includes(pick)) continue;
        const c = el("i", "fsm-chip-tag");
        c.textContent = g.name;
        if (g.color) { c.style.color = g.color; c.style.borderColor = g.color; }
        chips.appendChild(c);
      }
      if (chips.childElementCount) r.appendChild(chips);
    }
    r.onclick = (ev) => {   // pick = the exact filter, worn as the bar's chip; the list closes, the bar stays
      ev.stopPropagation();
      const already = (r.getAttribute("data-sid") || null) === feedOnlySid;
      setFeedOnly(already ? null : pick);
      const inp = document.getElementById("feed-search-input") as HTMLInputElement | null;
      if (inp) { inp.value = ""; inp.focus(); }   // back to typing mode; the chip carries the pick
      setFeedSearch("");
      closeSessList();
      render();
    };
    menu.appendChild(r);
  };
  const all = el("span", "");
  all.textContent = "All sessions";
  row(null, all, null);
  // tab order: rank by the kernel's session-order list (the same rank grouped mode sorts by); a sid the
  // list doesn't know keeps its place in the kernel's tab list (stable sort), after the ranked ones
  const rank = new Map(sessionOrder.map((s, i) => [s, i] as const));
  const rows = sessionsMeta.slice().sort((a, b) => (rank.get(a.sid) ?? 1e9) - (rank.get(b.sid) ?? 1e9));
  for (const s of rows) row(s.sid, sessMenuName(s), s.name, s.name);
  document.body.appendChild(menu);
  // above the footer, aligned to the BAR and at least its width (combobox convention), clamped in
  const r = wrap.getBoundingClientRect();
  menu.style.minWidth = Math.max(150, Math.round(r.width)) + "px";
  menu.style.bottom = Math.round(window.innerHeight - r.top + 6) + "px";
  menu.style.left = Math.round(Math.max(6, Math.min(r.left, window.innerWidth - menu.offsetWidth - 6))) + "px";
  sessListEl = menu;
  repaintSessListCurrent();
  filterSessList(feedSearchQ);
  document.addEventListener("pointerdown", sessListAway, true);
  document.addEventListener("keydown", sessListKey, true);
}
// Folding with anything live CLEARS it — the bar never hides an active filter of either kind.
function foldSessBox(): void {
  const wrap = document.getElementById("feed-search");
  const inp = document.getElementById("feed-search-input") as HTMLInputElement | null;
  let changed = false;
  if (inp && inp.value.trim()) { inp.value = ""; changed = true; }
  if (feedSearchQ.trim()) { setFeedSearch(""); changed = true; }
  if (feedOnlySid) { setFeedOnly(null); changed = true; }
  wrap?.classList.remove("open");
  closeSessList();
  if (changed) render();
}
function ensureSessionBox(): HTMLElement {
  let wrap = document.getElementById("feed-search") as HTMLElement | null;
  if (!wrap) {
    wrap = el("span", "");
    wrap.id = "feed-search";
    const btn = el("button", "fdismiss ffollow feed-modetoggle");
    btn.id = "feed-search-btn";
    btn.textContent = "Sessions \u25b4";
    btn.title = "filter the board by session — type to narrow (host prefix counts), or pick one exactly";
    btn.setAttribute("aria-haspopup", "listbox");
    const chip = el("span", "fsm-chip");
    chip.id = "feed-sess-chip";
    chip.hidden = true;
    const inp = document.createElement("input");
    inp.id = "feed-search-input";
    inp.type = "search";
    inp.placeholder = "session or host…";
    inp.setAttribute("aria-label", "filter cards by session name (host prefix included), or pick a session from the list");
    const openBox = () => { wrap!.classList.add("open"); openSessList(); inp.focus(); };
    btn.onclick = (ev) => { ev.stopPropagation(); wrap!.classList.contains("open") ? foldSessBox() : openBox(); };
    inp.onfocus = () => { if (!sessListEl) openSessList(); };   // focus re-opens the list (a pick closed it)
    inp.oninput = () => { setFeedSearch(inp.value); filterSessList(inp.value); render(); };   // live: narrows list AND board
    inp.onkeydown = (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); foldSessBox(); } };
    inp.onblur = () => {   // an empty bar folds quietly when focus leaves AND the list is gone — a row
      //                      press keeps the list (blur fires first); the away-closer owns that case
      if (!sessListEl && !inp.value.trim() && !feedOnlySid) wrap!.classList.remove("open");
    };
    const clr = el("button", "");
    clr.id = "feed-search-clear";
    (clr as HTMLButtonElement).type = "button";
    clr.setAttribute("aria-label", "Clear search");
    clr.title = "Clear search";
    clr.hidden = true;   // shown only with text to clear (the top bar's semantics); render() below syncs it
    clr.textContent = "\u00d7";
    clr.onclick = (ev) => {   // clear + REFOCUS — the user is mid-search, never fold under them
      ev.stopPropagation();
      inp.value = ""; setFeedSearch(""); filterSessList(""); inp.focus(); render();
    };
    wrap.appendChild(btn); wrap.appendChild(chip); wrap.appendChild(inp); wrap.appendChild(clr);
    (document.getElementById("feed-foot") || document.body).appendChild(wrap);
  }
  const inp = wrap.querySelector("input") as HTMLInputElement;
  if (inp && inp.value !== feedSearchQ && document.activeElement !== inp) inp.value = feedSearchQ;
  const clrBtn = document.getElementById("feed-search-clear");
  if (clrBtn && inp) clrBtn.hidden = !inp.value.trim();   // trimmed, like the fold + the filter — whitespace is no query
  // the chip quotes the picked session verbatim, dot included — a narrowed board must never look whole
  const chip = document.getElementById("feed-sess-chip") as HTMLElement | null;
  const cur = feedOnlySid ? sessionsMeta.find((s) => s.sid === feedOnlySid) : undefined;
  if (chip) {
    chip.hidden = !cur;
    if (cur) {
      // the ✕ is built ONCE (a per-render rebuild would swap it under a press — click-safety);
      // only the NAME re-quotes each render, so colour echoes and the working/awaiting dot stay live
      let nm = (chip as any)._nm as HTMLElement | undefined;
      if (!nm) {
        nm = el("span", "fsm-name");
        const x = el("button", "fsm-chipx");
        (x as HTMLButtonElement).type = "button";
        x.textContent = "\u00d7";
        x.setAttribute("aria-label", "show all sessions");
        x.title = "show all sessions — back to typing";
        x.onclick = (ev) => {
          ev.stopPropagation();
          setFeedOnly(null);
          repaintSessListCurrent();
          (document.getElementById("feed-search-input") as HTMLInputElement | null)?.focus();
          render();
        };
        chip.replaceChildren(nm, x);
        (chip as any)._nm = nm;
      }
      nm.replaceChildren(...hostNameNodes(cur.name, cur.sid));
      nm.style.color = cur.color ? cur.color.bg : "";
      setWorkDot(nm, dotFor(cur.name));
    }
  }
  // an ACTIVE filter of either kind force-opens the bar — never hidden behind the collapsed button
  const active = !!feedSearchQ.trim() || !!feedOnlySid;
  if (active) wrap.classList.add("open");
  const btn = document.getElementById("feed-search-btn");
  if (btn) { btn.classList.toggle("on", active); btn.setAttribute("aria-pressed", active ? "true" : "false"); }
  return wrap;
}

// (The footer "Sub-goals" checkbox was removed 2026-07-08: sub-goals is now a per-card "Sub-goals" button
// beside Summary — wired in applySections as the third mutually-exclusive section.)

// Build the three columns (Asks | Awaiting | Completed) inside #feed-list once;
// rebuild if torn down (empty state). "Awaiting" (the user's ruling 2026-06-10):
// matches the session-chip vocabulary — anything here awaits HIM (a question,
// an action like reload, an idea), red like the awaiting chip.
const STACK_DEFAULT = ["completed", "needsInput", "asks"];   // the stacked CSS default, top-down (2026-07-30)
const ROW_DEFAULT = ["asks", "needsInput", "completed"];     // the side-by-side CSS default — the build order

// Paint the column-order state (ONE order, two renderings — the user 2026-08-24): each column's
// --col-order var feeds BOTH layouts' `order:` rules; with no custom order the var comes OFF, so
// each layout keeps its own default (stacked: Completed→Blocked→Working, the user 2026-07-30; side
// by side: Working→Blocked→Completed, the build order). Also each section's fold (stacked-only CSS).
// Idempotent; runs at build, per toggle, and per drag re-slot.
function applyColStack(): void {
  const custom = colOrder.length === 3 ? colOrder : null;
  for (const key of ["asks", "needsInput", "completed"]) {
    const col = document.querySelector<HTMLElement>(".feed-col.col-" + key);
    if (!col) continue;
    const folded = collapsedCols.has(key);
    col.classList.toggle("col-collapsed", folded);
    if (custom) col.style.setProperty("--col-order", String(custom.indexOf(key) + 1));
    else col.style.removeProperty("--col-order");
    const fold = col.querySelector<HTMLElement>(".fcol-fold");
    if (fold) {
      fold.textContent = folded ? "▸" : "▾";
      fold.setAttribute("aria-expanded", String(!folded));
    }
  }
}

// Drag a section by its CATEGORY CHIP — in BOTH layouts now (the user 2026-08-24, reversing the
// 2026-08-16 stacked-only exclusion with their own ask: the columns should drag around in
// three-column view just as the sections already do stacked): stacked, the drag re-slots the section vertically;
// side by side, it reorders the three columns horizontally. ONE persisted order feeds both
// renderings (colOrder). The grab cursor on the chip is the affordance. While dragging, the grabbed
// section FOLLOWS the pointer along the drag axis (a transform, so nothing reflows under the hand)
// and the displaced sections FLIP-animate into their provisional slots — the arrangement you see
// mid-drag is the arrangement you get on drop.
function wireColDrag(chip: HTMLElement, col: HTMLElement, key: string): void {
  chip.addEventListener("pointerdown", (down) => {
    const colsEl = document.getElementById("feed-cols");
    if (!colsEl) return;
    const vertical = getComputedStyle(colsEl).flexDirection === "column";   // the drag AXIS, per layout
    down.preventDefault();
    down.stopPropagation();
    chip.setPointerCapture(down.pointerId);
    col.classList.add("col-dragging");
    const pos = (ev: PointerEvent) => (vertical ? ev.clientY : ev.clientX);
    const edge = (r: DOMRect) => (vertical ? r.top : r.left);
    const midOf = (r: DOMRect) => (vertical ? r.top + r.height / 2 : r.left + r.width / 2);
    const translate = (d: number) => (vertical ? "translateY(" + d + "px)" : "translateX(" + d + "px)");
    const fallback = vertical ? STACK_DEFAULT : ROW_DEFAULT;
    const hadCustom = colOrder.length === 3;   // for the no-trace rule in up()
    const start = pos(down);
    let slotShift = 0;   // the dragged section's own accumulated slot movement — folded into its
    //                      follow-transform so a re-slot never yanks it out from under the pointer
    const applyOrderFlip = (order: string[]) => {
      const els: Array<[string, HTMLElement]> = [];
      for (const k of ["asks", "needsInput", "completed"]) {
        const e = document.querySelector<HTMLElement>(".feed-col.col-" + k);
        if (e) els.push([k, e]);
      }
      const before = new Map(els.map(([k, e]) => [k, edge(e.getBoundingClientRect())]));
      colOrder = order;
      applyColStack();
      for (const [k, e] of els) {
        const d = (before.get(k) || 0) - edge(e.getBoundingClientRect());
        if (!d) continue;
        if (k === key) { slotShift -= d; continue; }   // both rects carry the follow-transform, so d is pure slot delta
        e.animate([{ transform: translate(d) }, { transform: translate(0) }],
                  { duration: 150, easing: "ease" });
      }
    };
    const move = (ev: PointerEvent) => {
      const order = (colOrder.length === 3 ? colOrder : fallback).slice();
      const from = order.indexOf(key);
      // the slot whose axis midpoint the pointer is past — walk the OTHER two sections' rects
      let to = from;
      for (const other of order) {
        if (other === key) continue;
        const oc = document.querySelector<HTMLElement>(".feed-col.col-" + other);
        if (!oc) continue;
        const m = midOf(oc.getBoundingClientRect());
        const oi = order.indexOf(other);
        if (oi < from && pos(ev) < m) { to = Math.min(to, oi); }
        if (oi > from && pos(ev) > m) { to = Math.max(to, oi); }
      }
      if (to !== from) {
        order.splice(from, 1);
        order.splice(to, 0, key);
        applyOrderFlip(order);
      }
      col.style.transform = translate(pos(ev) - start - slotShift);
    };
    const up = () => {
      chip.removeEventListener("pointermove", move);
      chip.removeEventListener("pointerup", up);
      chip.removeEventListener("pointercancel", up);
      // settle: animate from wherever the hand left it into its slot, then drop the transform
      const hang = col.style.transform;
      col.style.transform = "";
      if (hang && hang !== translate(0)) {
        col.animate([{ transform: hang }, { transform: translate(0) }],
                    { duration: 150, easing: "ease" });
      }
      col.classList.remove("col-dragging");
      // a drag dropped back where it started leaves NO trace (review 2026-08-24): without a
      // pre-existing custom order, ending on this layout's own default must not mint an EXPLICIT
      // order — that would silently re-arrange the OTHER layout, which keeps a different default
      if (!hadCustom && colOrder.length === 3 && colOrder.join() === fallback.join()) {
        colOrder = [];
        applyColStack();
      }
      persistViewState();
    };
    chip.addEventListener("pointermove", move);
    chip.addEventListener("pointerup", up);
    chip.addEventListener("pointercancel", up);
  });
}

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
      // header furniture (the user 2026-08-16): a caret LEFT of the chip folds the whole category to
      // its header (stacked-only, hidden side by side by CSS), and the CHIP ITSELF drags the section
      // to a new slot — in BOTH layouts since 2026-08-24 (see wireColDrag). These live on the
      // build-once header, so they are click-safe across the feed's constant re-renders.
      const fold = el("button", "fcol-fold");
      fold.setAttribute("aria-label", "Collapse " + label);
      fold.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (collapsedCols.has(key)) collapsedCols.delete(key); else collapsedCols.add(key);
        applyColStack();
        persistViewState();
      });
      const name = el("span", "feed-col-name fcol-chip fcol-chip-" + chip); name.textContent = label;
      name.title = "drag to reorder";
      wireColDrag(name, col, key);            // the chip ITSELF drags (the user 2026-08-16) — the grab
      //                                         cursor it wears in the stacked layout is the affordance
      const count = el("span", "feed-col-count"); count.id = "col-" + key + "-count";
      head.append(name, fold, count);         // caret RIGHT of the chip — the same side as the
      //                                          session headers' fold (the user 2026-07-31 / 2026-08-16)
      const body = el("div", "feed-col-list"); body.id = "col-" + key + "-list";
      col.append(head, body);
      cols.appendChild(col);
    }
    list.appendChild(cols);
    applyColStack();
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
// A grouped session header leaves as ONE MOTION with its run's last card (the user 2026-08-24,
// whose recording showed the header popping out a frame after the card finished fading): the exit
// wears the card-dismiss family (fade + slight shrink + height collapse) instead of vanishing at
// the next render. The element is UN-KEYED first — dropped from sessHeadEls and re-keyed to a
// tombstone — so a reappearing run mints a FRESH header while the ghost finishes; DOM removal keys
// on animationend (the event), with a backstop timeout so a lost event can never trap the ghost,
// and reduced motion removes at once (no animation ever plays there).
let sessGhostSeq = 0;
function startSessHeadExit(key: string, head: HTMLElement): void {
  if (sessHeadEls.get(key) === head) sessHeadEls.delete(key);
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    head.remove();
    return;
  }
  head.dataset.key = "x:" + (++sessGhostSeq);   // keyed-but-never-desired: survives the unkeyed-child sweep
  head.classList.add("sess-exit");
  const done = () => head.remove();
  head.addEventListener("animationend", done, { once: true });
  window.setTimeout(done, 600);                 // backstop — a lost event can never trap the ghost
}
// The CONJUNCTION half: when a dismissing card is its run's LAST, the header starts its exit at the
// same moment (the same click), not at the post-dismiss render. Grouped mode only; a run with other
// live cards keeps its header rock-steady. The walk reads the CURRENT DOM: back to the run's own
// header, then across the run for any member that is neither the leaving card nor already dismissing.
function dressHeaderIfLast(card: HTMLElement, sid: string): void {
  if (!feedPrefs().grouped) return;
  let head: HTMLElement | null = null;
  for (let p = card.previousElementSibling as HTMLElement | null; p; p = p.previousElementSibling as HTMLElement | null) {
    if (p.classList.contains("feed-sess-head")) { head = p; break; }
  }
  if (!head || head.getAttribute("data-fsid") !== sid || head.classList.contains("sess-exit")) return;
  for (let n = head.nextElementSibling as HTMLElement | null; n && !n.classList.contains("feed-sess-head"); n = n.nextElementSibling as HTMLElement | null) {
    if (n !== card && n.classList.contains("fitem") && !n.classList.contains("dismissing")) return;   // the run lives on
  }
  const key = head.dataset.key || "";
  startSessHeadExit(key, head);
}

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
    } else if (e.kind === "sess") {
      // grouped-mode session header — keyed per (column, sid): one session can head a run in EVERY column
      key = "s:" + listEl.id + ":" + e.sid;
      card = sessHeadEls.get(key) || makeSessHead();
      card.dataset.key = key;
      sessHeadEls.set(key, card);
      updateSessHead(card, e);
    } else {
      key = "g:" + e.group.turnId;
      card = groupEls.get(e.group.turnId) || makeGroupCard(e.group);
      groupEls.set(e.group.turnId, card);
      updateGroupCard(card, e.group);
    }
    globalDesired.add(key); colDesired.add(key);
    ordered.push(card);
  }
  for (const [k, c] of existing) {
    if (colDesired.has(k)) continue;
    if (c.classList.contains("sess-exit")) continue;             // a ghost mid-exit — its end event removes it
    if (k.startsWith("s:")) startSessHeadExit(k, c);             // headers leave as one motion (2026-08-24)
    else c.remove();
  }
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
// column id per card key as of the LAST render — the flip gate compares the next render against it
let prevCols: Map<string, string> = new Map();
// the card keys of a render's buckets, mapped to the column each lands in (the same keys reconcileCol mints)
function columnsOf(buckets: Record<Column, Entry[]>): Map<string, string> {
  const m = new Map<string, string>();
  for (const col of Object.keys(buckets) as Column[]) {
    buckets[col].forEach((e, i) => {
      const key = e.kind === "ask" ? "a:" + e.ask.itemId : e.kind === "group" ? "g:" + e.group.turnId : "s:" + col + ":" + e.sid;
      m.set(key, col + ":" + i);   // column AND position: a card that moved within its column glides too (the user 2026-06-29)
    });
  }
  return m;
}
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
  // READ every card's new position first, then WRITE: a transform written between two rect reads dirties
  // layout, so the next read forces a fresh layout of the whole document — one per card, 155 times per
  // feed frame (measured 2026-09-04: the largest single cost on the main thread the chat pane's clicks share).
  const moves: { c: HTMLElement; dx: number; dy: number; crossed: boolean }[] = [];
  for (const key of FLY_COLS) {
    const colEl = cols[key];
    for (const c of Array.from(colEl.children) as HTMLElement[]) {
      const k = c.dataset.key; if (!k) continue;
      const prev = first.get(k);
      if (!prev) continue;                                 // brand-new card → no FLIP (nothing to glide from)
      const now = c.getBoundingClientRect();
      const dx = prev.rect.left - now.left, dy = prev.rect.top - now.top;
      if (!dx && !dy) continue;                            // didn't move → leave it alone
      moves.push({ c, dx, dy, crossed: prev.col !== colEl.id });
    }
  }
  for (const { c, dx, dy, crossed } of moves) {
    {
      // Two flavors of move, ONE FLIP (the user 2026-06-29): a card that CHANGED COLUMN flies in the BACK
      // layer (z-index:-1 → behind the other cards, so it never sails over them); a card that STAYED in its
      // column but shifted — because the card that left it vacated a slot — glides IN PLACE in normal flow, so
      // the remaining cards reflow smoothly to their new spots instead of snapping there in a discrete jump.
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
// A small transient notice at the bottom of the feed — used to surface an inconsistency (e.g. an optimistic
// follow-up move the kernel never confirmed), so a behavior change is visible rather than silent. Auto-dismisses.
let feedToastEl: HTMLElement | null = null;
let feedToastTimer: number | undefined;
function feedToast(text: string) {
  if (feedToastEl) feedToastEl.remove();
  const t = el("div", "feed-toast"); t.textContent = text; t.setAttribute("role", "status");
  document.body.appendChild(t); feedToastEl = t;
  requestAnimationFrame(() => t.classList.add("show"));
  clearTimeout(feedToastTimer);
  feedToastTimer = window.setTimeout(() => {
    t.classList.remove("show");
    window.setTimeout(() => { if (feedToastEl === t) feedToastEl = null; t.remove(); }, 300);
  }, 4200);
}

// ── usage-limit banner (the user 2026-08-18): a judge layer down on a USAGE LIMIT must say so ──
// loudly, never fail quietly into retries. The kernel ships the judge-limit latch on the feed
// payload (self-expiring at the window reset, cleared by the next successful call). Built ONCE and
// updated in place — the button must survive re-renders (the click-safety rule), and it
// acknowledges immediately, then the latch clearing hides the banner on a later payload.
type JlIdent = { name: string; host?: string; sid?: string; color?: { bg: string; fg: string } | null };
let judgeLimit: { bucket?: string; resets_at?: number; model?: string;
                  loginSessions?: JlIdent[]; billingUnknown?: JlIdent[] } | null = null;
// The dismiss latches to THIS episode's identity — a NEW episode (different bucket or reset time)
// is new information and re-shows the banner; nothing else does (event-based, no timers). Stored
// in localStorage so the dismissal survives re-renders and reloads for the episode's lifetime.
const jlEpisodeKey = (j: { bucket?: string; resets_at?: number } | null) =>
  (j?.bucket || "") + ":" + (j?.resets_at || 0);
let jlSessOpen = false;   // the session list's keyed expand — module state, survives re-renders
function ensureJudgeLimit(): HTMLElement {
  let b = document.getElementById("judge-limit-banner");
  if (b) return b;
  b = el("div", "judge-limit-banner");
  b.id = "judge-limit-banner";
  const txt = el("span", "jl-text"); b.appendChild(txt);
  const btn = el("button", "jl-switch") as HTMLButtonElement;
  btn.type = "button";
  btn.textContent = "Run analysis on Opus until then";
  btn.title = "switch the analysis model to Opus (cheaper per token than Fable) while the Fable window is full";
  btn.onclick = () => {
    btn.disabled = true;
    btn.textContent = "Switching…";                      // acknowledge before the round-trip
    // gt: a settings gesture like any gear pick — stamped at the click so the kernel can order it
    vscodeApi?.postMessage({ type: "setJudgeModel", model: "opus", gt: Date.now() });
  };
  b.appendChild(btn);
  const sess = el("span", "jl-sess"); b.appendChild(sess);   // who the window actually touches (2026-08-28)
  const x = el("button", "jl-dismiss") as HTMLButtonElement;
  x.type = "button";
  x.textContent = "✕";
  x.title = "dismiss this notice — it returns only if a new limit episode starts";
  x.onclick = () => {
    // the hide IS the acknowledgment, immediate and local; the latch key makes it stick (build-once
    // button — the listener survives every re-render, per the click-safety rule)
    try { localStorage.setItem("romp:jlDismiss", jlEpisodeKey(judgeLimit)); } catch { /* storage blocked */ }
    paintJudgeLimit();
  };
  b.appendChild(x);
  // the "+N more" toggle is REBUILT per paint, so its action rides the build-once banner root
  // (delegation to the stable ancestor — the same rule the tab strip follows)
  b.addEventListener("click", (ev) => {
    const t = ev.target as HTMLElement;
    if (t && t.dataset && t.dataset.act === "jl-more") { jlSessOpen = !jlSessOpen; paintJudgeLimit(); }
  });
  const list = document.getElementById("feed-list")!;
  list.parentElement!.insertBefore(b, list);
  return b;
}
function paintJudgeLimit(): void {
  const b = ensureJudgeLimit();
  if (!judgeLimit) { b.style.display = "none"; return; }
  let dismissed = "";
  try { dismissed = localStorage.getItem("romp:jlDismiss") || ""; } catch { /* storage blocked */ }
  if (dismissed === jlEpisodeKey(judgeLimit)) { b.style.display = "none"; return; }
  const ra = judgeLimit.resets_at;
  const when = typeof ra === "number" && ra > 0
    ? new Date(ra * 1000).toTimeString().slice(0, 5) : "";
  const fable = judgeLimit.bucket === "fable";
  const txt = b.querySelector(".jl-text")!;
  // the HONEST scope (the user 2026-08-28, correction round — the everything-is-paused framing was
  // wrong: a judge call bills the JUDGED session's account, so key-billed analysis keeps flowing):
  // the window is the login account's; only the sessions billing it pause.
  txt.textContent = fable
    ? "The account's Fable usage window is full" + (when ? " (resets " + when + ")." : ".")
    : "The account's usage window is full" + (when ? "; it resumes at " + when + "." : ".");
  // WHO it touches: the sessions billing this account lose BOTH their turns (rate-limited) and
  // their card analysis until the reset; everyone else's analysis continues on their own billing.
  // Names wear the standard session chip — bold, identity colour, quiet host: prefix (the user's
  // ask). Inline when few; a keyed "+N more" expand when many; unknown billing said, never omitted.
  const sessEl = b.querySelector(".jl-sess") as HTMLElement;
  sessEl.replaceChildren();
  const names = judgeLimit.loginSessions || [];
  const unk = judgeLimit.billingUnknown || [];
  const chip = (p: JlIdent) => {
    const c = el("b", "jl-chip");
    c.replaceChildren(...hostPartsNodes(p.host, p.name));
    if (p.color && p.color.bg) c.style.color = p.color.bg;
    return c;
  };
  const appendChips = (list: JlIdent[]) => list.forEach((p, i) => { if (i) sessEl.append(", "); sessEl.appendChild(chip(p)); });
  if (names.length) {
    const many = names.length > 3;
    const shown = many && !jlSessOpen ? names.slice(0, 2) : names;
    sessEl.append("Analysis and turns pause for the sessions billing it: ");
    appendChips(shown);
    if (many) {
      const more = el("span", "jl-more");
      more.dataset.act = "jl-more";
      more.textContent = jlSessOpen ? " · fewer" : " +" + (names.length - shown.length) + " more";
      more.title = jlSessOpen ? "collapse the list" : "show every affected session";
      sessEl.appendChild(more);
    }
    sessEl.append(". Other sessions' analysis continues on their own billing.");
  } else {
    sessEl.append("No live session bills this account — every session's analysis continues on its own billing.");
  }
  if (unk.length) {
    const u = el("span", "jl-unknown");
    u.textContent = " · billing unknown for ";
    sessEl.appendChild(u);
    appendChips(unk);
    const u2 = el("span", "jl-unknown");
    u2.textContent = " (their CLI doesn't report it)";
    sessEl.appendChild(u2);
  }
  const btn = b.querySelector(".jl-switch") as HTMLButtonElement;
  btn.style.display = fable ? "" : "none";
  if (!judgeLimit || !fable) { btn.disabled = false; btn.textContent = "Run analysis on Opus until then"; }
  b.style.display = "";
}

// The render's display-side view: the footer session filter (the user 2026-08-08 — display-side
// only, `asks` stays complete so flipping it needs no kernel round-trip) composed with the search
// query (the user 2026-08-23 — a card passes when its session's meta name matches OR its own
// per-card label does, so a just-died session's cards keep matching after the meta list drops it).
// Shared by render() and the hover-freeze badge painter, so the deferred-churn hint counts exactly
// what the user would see move.
function viewScope(list: AskItem[]): AskItem[] {
  // The board shows EVERY session's cards, whatever tag view the tabs/timeline hold (the user's
  // 2026-08-25 ruling, superseding the 2026-08-24 feed-follows-the-view coupling after living with
  // it): the feed is the attention/clearing surface — hidden-elsewhere work still lands and clears
  // here. Its ONLY narrowing is its own local scoping (this combobox exact filter + search) and
  // the feed-local TAG LENS (viewBase). A tracked
  // delegation's satellite lives under its delegator's PRIMARY card: the default board hides it,
  // and picking its session in the filter is the one-click path back.
  let shown = feedOnlySid ? list.filter((a) => a.sid === feedOnlySid) : list.filter((a) => !a.satellite);
  const sMatch = searchSids(feedSearchQ, sessionsMeta);
  if (sMatch) shown = shown.filter((a) => sMatch.has(a.sid) || searchMatches(feedSearchQ, (a as { name?: string }).name));
  return shown;
}

// The feed-local TAG LENS slot (the user 2026-08-25, T70; disclosure lineage: the 665 outside-view
// treatments, retired with the shared-view coupling, live again here under the user's OWN lens).
// Needs-you always passes — the same interrupt rule the satellite and internals lens wear: a
// view-hidden session that needs the human is this board's whole job.
function viewBase(list: AskItem[]): AskItem[] {
  const s = viewScope(list);
  if (lensAll(feedLens)) return s;   // default All = today's board, byte-identical
  const u = lensUnions(feedTagViews);
  return s.filter((a) => lensVisible(feedLens, u, a.sid) || a.column === "needs_input");
}

// The disclosure count: what the TAG LENS alone hides (breakthroughs already show; counting them
// would double-speak) — viewScope minus viewBase, by construction.
function outsideLensCount(list: AskItem[]): number {
  return lensAll(feedLens) ? 0 : viewScope(list).length - viewBase(list).length;
}

// The board's final display view. (The 2026-08-25 team-internals lens lived in this slot for a
// day and RETIRED the same day on the user's verdict: team-internal cards must not be CREATED —
// chain-rooted minting in the courier owns that now — rather than created-then-foldable. No
// display class, no footer toggle; the slot family stays so future lenses layer the same way.)
// INSIDE this helper on purpose — the hover-freeze churn badges count through it, so they see
// exactly what the board shows (a filter outside would paint +N for cards that never appear).
function viewFiltered(list: AskItem[]): AskItem[] {
  return viewBase(list);
}

// The per-host loading strip (the user 2026-08-25): while an attached host's cards are pending,
// one quiet line per host — the romp loader family scoped to a strip, never a board takeover; the
// cards already present stay fully live. Retires per host on the exact event of its first merged
// contribution (pendingHosts, federation.ts) or on the standing can't-trap backstop; the lines are
// non-interactive, so a per-render rebuild is click-safe by construction.
function ensureHostLoad(list: HTMLElement): void {
  let strip = document.getElementById("feed-hostload");
  if (!pendingHosts.length) { strip?.remove(); return; }   // the real events emptied the list — the only way off
  if (!strip) {
    strip = el("div", "");
    strip.id = "feed-hostload";
  }
  // FIRST child in either board state (the user 2026-08-30: the reconnect strip sits at the TOP of
  // the feed, above the first column chip — it announces what is COMING, so it leads). Re-inserted
  // only when displaced (a fresh empty-state paint replaces children); non-interactive, so the move
  // is click-safe by construction.
  if (list.firstChild !== strip) list.insertBefore(strip, list.firstChild);
  strip.replaceChildren(...pendingHosts.map((h) => {
    const line = el("div", "hostload-line");
    const swirl = el("span", "fask-awaiting-swirl");   // the shared reverse-spin glyph, LEFT of the text
    const txt = el("span", "");
    txt.textContent = pendingDead.includes(h)
      ? "can\u2019t reach " + h + " \u2014 its cards return when it reconnects"
      : hostloadLong.has(h)
        ? "still waiting on " + h + "\u2026"
        : "loading cards from " + h + "\u2026";
    line.append(swirl, txt);
    return line;
  }));
}

function render() {
  const list = document.getElementById("feed-list")!;
  pruneTip();   // drop the styled tip only if the render tore its hovered anchor out (tip.ts pruneTip)
  applyFollowMove(asks);   // keep optimistically-moved follow-up cards in Working until the kernel confirms (or reverts)
  paintJudgeLimit();   // the usage-limit banner above the columns (build-once; hidden when unlatched)
  auditShownColumns(asks); // tripwire: what this render SHOWS is the record a bounce report needs
  const prevScroll = list.scrollTop;
  // footer pane (below the cards, no overlap): view menu · Session filter · Search | Clear all · Undo
  const showCA = !!asks.length;
  ensureViewMenuBtn().style.display = showCA ? "" : "none";       // sort + layout menu (the user 2026-08-24)
  ensureTagLensBtn().style.display = showCA ? "" : "none";        // the feed-local tag lens (the user 2026-08-25, T70)
  ensureSessionBox().style.display = showCA ? "" : "none";        // session combobox: type-or-pick filter (the user 2026-08-24)
  ensureClearAll().style.display = showCA ? "" : "none";
  ensureUndoClear().style.display = canUndoClear ? "" : "none";
  const foot = document.getElementById("feed-foot");
  // show the footer whenever there are cards (so the Sub-goals toggle is reachable) or an undo is available
  if (foot) foot.style.display = (showCA || canUndoClear) ? "" : "none";

  if (!asks.length) {   // the removed FeedItem subsystem left a stale second operand here (a read of the gone
    //                     `standalone` array) that threw a ReferenceError on an EMPTY feed → the inbox-zero
    //                     wordmark never rendered (the user 2026-07-08; payload-audit fallout). Goal cards are
    //                     the only feed unit now, so an empty asks list IS an empty feed.
    askEls.clear(); groupEls.clear();
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
    ensureHostLoad(list);   // an attached host's cards may be the ONLY thing coming — say so here too
    return;
  }

  const cols = ensureCols(list);
  const buckets: Record<Column, Entry[]> = { asks: [], needsInput: [], completed: [] };
  // The display-side view filters (session filter + search), shared with the hover-freeze badge
  // painter so the deferred-churn hint counts exactly what the user would see move (viewFiltered).
  let shown = viewFiltered(asks);
  // Derive sibling GROUPS at render time, keyed by the shared typed turn (turnId).
  // Only host-flagged asks (groupTitle) participate, and a turn needs ≥2 current
  // members to fold — a lone survivor (siblings cleared) renders as a single card.
  const byTurn = new Map<string, AskItem[]>();
  for (const a of shown) {
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
  for (const a of shown) { if (grouped.has(a.itemId)) continue; buckets[askColumn(a)].push({ kind: "ask", t: a.t, ask: a }); }
  // Oldest-at-top by default (the user 2026-06-27): the newest work sits at the BOTTOM of each column, and
  // new/moved cards stack onto the bottom (matches the fly animation). The footer "Newest first" toggle
  // (default off, the user 2026-07-07) reverses each column to newest-at-top.
  const newestFirst = feedPrefs().newestFirst;
  for (const k of Object.keys(buckets) as Column[]) buckets[k].sort((x, y) => newestFirst ? y.t - x.t : x.t - y.t);
  // GROUPED mode (the user 2026-07-13): within each column, cards gather by SESSION — session order = the
  // kernel's session-order list (the same order the chat tabs + timeline lanes hold; sessions the list
  // doesn't know keep their time order after it) — with a name+dot header entry opening each run. The sort
  // is stable, so per-session cards keep the column's newest/oldest order. Headers only where a run exists.
  if (feedPrefs().grouped) {
    const rank = new Map(sessionOrder.map((s, i) => [s, i] as const));
    const eSid = (e: Entry) => e.kind === "ask" ? e.ask.sid : e.kind === "group" ? e.group.sid : e.sid;
    for (const k of Object.keys(buckets) as Column[]) {
      const extra = new Map<string, number>();   // sids the order list doesn't know → after it, first-seen order
      for (const e of buckets[k]) { const s = eSid(e); if (!rank.has(s) && !extra.has(s)) extra.set(s, extra.size); }
      const rk = (e: Entry) => { const s = eSid(e); return rank.has(s) ? rank.get(s)! : 1e9 + (extra.get(s) || 0); };
      buckets[k].sort((x, y) => rk(x) - rk(y));
      const withHeads: Entry[] = [];
      let cur: string | null = null;
      let head: (Entry & { kind: "sess" }) | null = null;
      for (const e of buckets[k]) {
        const s = eSid(e);
        if (s !== cur) {
          cur = s;
          const src: any = e.kind === "ask" ? e.ask : e.kind === "group" ? e.group : e;
          head = { kind: "sess", t: e.t, sid: s, name: src.name, color: src.color || null, live: !!src.live, folded: 0 };
          withHeads.push(head);
        }
        // A COLLAPSED thread contributes its header and nothing else — the run's cards are counted onto the
        // header instead of rendered, so the folded row still says how much is under it. CARDS, not rows
        // (entryCards): a turn-group folds as its member count, the same rule the section chip reads.
        if (collapsedThreads.has(s)) { if (head) head.folded += entryCards(e); continue; }
        withHeads.push(e);
      }
      buckets[k] = withHeads;
    }
  }

  // FLIP-across-identity bookkeeping (the user 2026-06-29): map every goal itemId this render → the card KEY
  // that renders it, so the next render can slide a card whose IDENTITY changed (group↔solo, umbrella absorb)
  // from its predecessor's old spot. An ask/group "covers" its own id AND its tree node ids, so an umbrella
  // card covers the goals it just absorbed (their solo cards were the predecessors).
  const curItemKey = new Map<string, string>();
  const coverInto = (key: string, ids: string[]) => { for (const id of ids) if (!curItemKey.has(id)) curItemKey.set(id, key); };
  for (const k of Object.keys(buckets) as Column[]) for (const e of buckets[k]) {
    if (e.kind === "ask") coverInto("a:" + e.ask.itemId, [e.ask.itemId, ...(e.ask.tree || []).map((n) => n.id)]);
    else if (e.kind === "group") coverInto("g:" + e.group.turnId, e.group.members.flatMap((m) => [m.itemId, ...(m.tree || []).map((n) => n.id)]));
    // (the "item"/standalone bucket kind was removed with the FeedItem subsystem — no third branch)
  }

  // FLIP step 1 (the user 2026-06-27): record every visible card's position + column BEFORE the reconcile, so
  // a card that changes column can FLY from its old spot to the new one instead of teleporting. Only when
  // something CAN move: the capture and the fly each force a layout of the whole document, and most frames
  // change a card in place (text, tint, status chip) with every card staying where it was (2026-09-04).
  const nextCols = columnsOf(buckets);
  const needFlip = flipNeeded(prevCols, nextCols);
  prevCols = nextCols;
  const flipFirst = needFlip ? captureCardRects(cols) : new Map<string, FlipState>();

  const desired = new Set<string>();
  reconcileCol(cols.asks, buckets.asks, desired);
  reconcileCol(cols.needsInput, buckets.needsInput, desired);
  reconcileCol(cols.completed, buckets.completed, desired);
  // the count chip shows the number only when there ARE cards; an empty column shows nothing — not "0"
  // (the user 2026-06-25). Empty string collapses the chip (it has no padding/background of its own).
  const setCount = (elc: HTMLElement, n: number) => { elc.textContent = n ? String(n) : ""; elc.style.display = n ? "" : "none"; };
  // Headers aren't cards — but a FOLDED header stands in for its run, so its cards count. The column chip
  // reports what is on the board, never what you happen to have open: folding a thread must not read as
  // work having left the column (the user 2026-07-31). ONE counting rule (the user 2026-08-26): a number
  // is always CARDS, never rows — a turn-group row is its members, a folded header is the cards it hides
  // (entryCards, which the fold accumulator uses too) — so a section's number cannot move on any fold or
  // grouping, only when cards actually enter or leave the column.
  const nCards = (es: Entry[]) => es.reduce((n, e) => n + entryCards(e), 0);
  setCount(cols.asksCount, nCards(buckets.asks));
  setCount(cols.needsInputCount, nCards(buckets.needsInput));
  setCount(cols.completedCount, nCards(buckets.completed));

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

  // The tag lens's disclosure line (the user 2026-08-25, T70; lineage: the 665 outside-view line +
  // promoted banner, retired with the shared-view coupling, revived under the user's OWN lens —
  // what a filter hides stays one glance from reach, never silent, the 2026-08-11 rule). The click
  // resets to All, purely local (no kernel round-trip: sessionStorage + render).
  const lensOutN = outsideLensCount(asks);
  let lmore = document.getElementById("feed-lensmore");
  if (!lmore) {
    lmore = el("div", "");
    lmore.id = "feed-lensmore";
    lmore.title = "cards the tag filter hides — click to show all";
    lmore.onclick = () => { setFeedLens({ all: true }); render(); };
    list.appendChild(lmore);
  }
  const lensShownN = viewFiltered(asks).length;
  lmore.classList.toggle("prominent", lensOutN > lensShownN);
  lmore.style.display = lensOutN ? "" : "none";
  if (lensOutN) {
    lmore.textContent = lensOutN > lensShownN
      ? "Showing \u201c" + lensLabel(feedLens) + "\u201d \u2014 " + lensOutN
        + (lensOutN === 1 ? " card is" : " cards are") + " outside this filter \u00b7 show all"
      : lensOutN + (lensOutN === 1 ? " card" : " cards") + " outside this tag filter \u2014 show all";
  }
  ensureHostLoad(list);
  list.scrollTop = prevScroll;
  // Stale-freeze heal (hover-freeze): a LOCAL render can detach or re-key the hovered element with
  // no mouseleave — a removed element never fires leave events (typing in search filters the card
  // out; toggling Group swaps the ask card for a group card in place). :hover is live pointer
  // truth, checked per render (event-based, never a timer): no card under the pointer → the freeze
  // is stale, clear it and flush the queue; a DIFFERENT card under the pointer (re-keyed in place,
  // so no enter event ever fired) → re-arm to the element actually being hovered.
  if (freezeKey) {
    const hov = document.querySelector<HTMLElement>(".feed-cols .fitem:hover");
    if (!hov) { freezeKey = null; flushFreeze(); }
    else { const k = kbHoverId(hov); if (k && k !== freezeKey) freezeKey = k; }
  }
  paintFreezeBadges();   // hover-freeze: local renders while frozen re-sync the +N/-N hints (no-op unfrozen)
  // stale-ring heal: releaseTabScope sweeps the DOCUMENT, but a card DETACHED at release (filtered
  // out by search, say) keeps its ring and the reconcile may reattach that cached element later —
  // with no scope active, any ring inside a card is stale; strip per render (kbMode owns its own)
  if (!tabScopeKey && !kbMode) document.querySelectorAll(".fitem .kbd-focus").forEach((n) => n.classList.remove("kbd-focus"));
  // keyboard-scope focus restore (the user 2026-08-24): a re-render may rebuild the focused
  // control's element, and a rebuild must not eat keyboard focus (click-safety, applied to the
  // keyboard). Find the control again by LOGICAL identity — class + label, then the old slot.
  if (tabScopeKey && tabScopeSig) {
    const card = cardElByKey(tabScopeKey);
    const ae = document.activeElement;
    if (!card) releaseTabScope();
    else if (!ae || ae === document.body || !card.contains(ae)) {
      const els = cardControls(card);
      let i = els.findIndex((e2) => ctrlSig(e2) === tabScopeSig!.sig);
      if (i < 0) i = Math.min(tabScopeSig.idx, els.length - 1);
      if (i >= 0) tabScopeFocus(card, els, i);
      else releaseTabScope();
    }
  }
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
  if (needFlip) flyColumnChanges(flipFirst, cols);
  prevItemKey = curItemKey;   // remember this render's identity map for the next FLIP-across-identity
  renderModal();   // keep the ⛶ full-screen tree (if open) in sync with this push
  applyExtHover(); // reconcile/renderModal may have rebuilt nodes — re-apply the rail-dot outlines (cards AND modal rows)
  persistViewState();   // whatever the user opened survives the reload a kernel restart brings (no-op unless it changed)
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
  if (kbMode) return true;   // keyboard-nav is active → keep focus in the feed so the arrows land here
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

// ── keyboard navigation of cards + their elements (the user 2026-07-01) ── the shell hands the feed keyboard
// focus via {romp:'paneFocus'} (Alt+Arrow); from there plain Arrow keys move a cursor over cards, Enter drops
// INTO a card and steps its clickable elements, Enter on one ACTIVATES it (a real click), Escape steps back
// out. Every highlight + action reuses the mouse path — card cursor = the same hoverAskId/applyFocus/showAskPath
// the hover uses; element cursor dispatches a real mouseenter (so zone highlights + timeline light exactly as on
// hover) and Enter calls the element's own click() — so the keyboard can never drift from the mouse.
const KB_EL_SEL = ".fcard-title.nav,.fask-distill-link,.fname,.fask-apiRetry,.fask-revive,.fdismiss,.fask-secbtn,.fask-bellbtn,.fcheck .lz-nav,.fask-delegation";
function kbCardEls(): HTMLElement[] {
  // VISUAL order, not DOM order (review 2026-08-24): the columns re-sequence via --col-order — in
  // both layouts since the drag extended — so the arrow cursor sorts cards by their column's
  // effective `order` (getComputedStyle resolves the var), DOM order within a column.
  const els = Array.from(document.querySelectorAll<HTMLElement>(".feed-cols .fitem:not(.dismissing)"));
  const slot = new Map<HTMLElement, number>();
  for (const e of els) {
    const col = e.closest<HTMLElement>(".feed-col");
    slot.set(e, col ? parseInt(getComputedStyle(col).order || "0", 10) || 0 : 0);
  }
  return els.map((e, i) => ({ e, i, s: slot.get(e) || 0 }))
    .sort((a, b) => a.s - b.s || a.i - b.i)
    .map((x) => x.e);
}
function kbHoverId(el: HTMLElement): string {
  const key = el.dataset.key || "";
  return key.startsWith("g:") ? key : key.slice(2);   // group card → "g:<tid>" (applyFocus key); ask/deliverable → itemId
}
function kbSelectCard(el: HTMLElement | null): void {
  kbCardEl = el;
  const id = el ? kbHoverId(el) : null;
  hoverAskId = id; applyFocus();                       // the SAME white .focused ring the mouse hover shows
  if (el) el.scrollIntoView({ block: "nearest" });
  if (id && !id.startsWith("g:")) vscodeApi?.postMessage({ type: "showAskPath", itemId: id, sid: sidOfItem(id), locate: false });   // same lit timeline journey
}
function kbEnterCards(): void {
  kbMode = "cards";
  const cards = kbCardEls();
  kbSelectCard((kbCardEl && cards.indexOf(kbCardEl) >= 0) ? kbCardEl : (cards[0] || null));
}
function kbClearEl(): void {
  document.querySelectorAll(".kbd-focus").forEach((e) => e.classList.remove("kbd-focus"));
  if (kbEls[kbElIdx]) kbEls[kbElIdx].dispatchEvent(new MouseEvent("mouseleave"));
}
function kbSelectEl(idx: number): void {
  kbClearEl();
  kbElIdx = idx;
  const el = kbEls[idx];
  if (!el) return;
  el.classList.add("kbd-focus");                       // accent ring so title/Clear/etc read as focused
  el.scrollIntoView({ block: "nearest" });
  el.dispatchEvent(new MouseEvent("mouseenter"));       // reuse the element's OWN hover (zone .lz-hl + timeline)
}
function kbEnterCard(): void {
  if (!kbCardEl) return;
  kbEls = Array.from(kbCardEl.querySelectorAll<HTMLElement>(KB_EL_SEL)).filter((e) => e.offsetParent !== null);
  if (!kbEls.length) return;                            // nothing focusable inside → stay on the card cursor
  kbMode = "card"; kbSelectEl(0);
}
function kbExitCard(): void { kbClearEl(); kbEls = []; kbElIdx = -1; kbMode = "cards"; }   // back to the card cursor
function kbExit(): void { kbClearEl(); kbEls = []; kbElIdx = -1; kbMode = ""; kbCardEl = null; hoverAskId = null; applyFocus(); }

window.addEventListener("keydown", (e) => {
  if (!kbMode) return;
  if (e.altKey || e.ctrlKey || e.metaKey) return;      // Alt+Arrow is the shell's pane move; leave other combos alone
  if (document.getElementById("feed-modal")) return;   // the modal owns keys while it's open
  const k = e.key, fwd = (k === "ArrowDown" || k === "ArrowRight"), back = (k === "ArrowUp" || k === "ArrowLeft");
  if (kbMode === "cards") {
    if (fwd || back) {
      const cards = kbCardEls();
      let i = kbCardEl ? cards.indexOf(kbCardEl) : -1;
      if (i < 0 && hoverAskId) i = cards.findIndex((c) => kbHoverId(c) === hoverAskId);   // survive a re-render by key
      i = (i < 0) ? 0 : Math.max(0, Math.min(cards.length - 1, i + (fwd ? 1 : -1)));
      e.preventDefault(); kbSelectCard(cards[i] || null);
    } else if (k === "Enter") { e.preventDefault(); kbEnterCard(); }
    else if (k === "Escape") { e.preventDefault(); kbExit(); }
  } else if (kbMode === "card") {
    if (fwd || back) { e.preventDefault(); kbSelectEl(Math.max(0, Math.min(kbEls.length - 1, kbElIdx + (fwd ? 1 : -1)))); }
    else if (k === "Enter") { e.preventDefault(); kbEls[kbElIdx]?.click(); }   // EXACTLY a mouse click on that element
    else if (k === "Escape") { e.preventDefault(); kbExitCard(); }
  }
});
window.addEventListener("blur", () => { if (kbMode) kbExit(); });   // shell moved focus to another pane → drop the cursor

// Re-render when the card-display prefs change: a 'storage' event fires for a change made in ANOTHER
// same-origin pane/tab, and the ⛭ gear (same document) dispatches a "romp:settings" event after it writes
// (a same-doc write fires no storage event). Either way the cards re-gate to the new Explanations/Sub-goals.
// The collapsed DEFAULT now changes from the settings modal (2026-08-18), so the override-drop that
// used to live in the footer toggle rides the settings-change event instead: when `collapsed` flips —
// whichever surface flipped it — the per-card section overrides drop, so every card visibly re-flows
// to the new default. And the stacked pref re-applies on every change (another window's gear or a
// same-page toggle both land here).
let lastCollapsedPref = feedPrefs().collapsed;
function onSettingsChanged(): void {
  paintEpoch++;   // grouped/collapsed/stacked reach into every card's paint (name row, row2, sections)
  const p = feedPrefs();
  if (p.collapsed !== lastCollapsedPref) { lastCollapsedPref = p.collapsed; secChoice.clear(); }
  applyStacked(p.stacked);
  if (viewMenuEl) paintViewMenu(viewMenuEl);   // an open view menu re-reads the prefs it shows
  render();
}
window.addEventListener("storage", (e) => { if (e.key === "romp:settings") onSettingsChanged(); });
window.addEventListener("romp:settings", () => onSettingsChanged());
applyStacked(feedPrefs().stacked);   // boot: a persisted Stack takes effect before the first render

// The VS Code pipe's down-banner (the user 2026-07-21): while the extension host's kernel
// socket is down, the pane says so instead of sitting silently frozen on its last frame —
// and counts the typed messages the pipe is holding for delivery on reconnect. Only the
// extension posts pipeState; in the browser the page shim's own stale/disconnected bars cover this.
function pipeBanner(up: boolean, queued: number): void {
  const b = document.getElementById("rpipe");
  if (up) { if (b) b.remove(); return; }
  const bar = b || document.body.appendChild(Object.assign(document.createElement("div"), { id: "rpipe" }));
  bar.textContent = queued > 0
    ? `romp is unreachable — reconnecting… ${queued} message${queued === 1 ? "" : "s"} held, sending when it's back`
    : "romp is unreachable — reconnecting…";
}

// Card trouble badges mirror into the shell's notification bell (the user 2026-07-27): the chip on the
// card stays exactly as it was; the bell gets ONE durable entry per episode. badge-mirror.ts owns the
// episode identity (signature-keyed on the badge's own since/t); this wrapper owns the plumbing — the
// persisted seen-set (shared across tabs via localStorage, so two open dashboards don't double-log) and
// the {romp:'notify'} post the shell's bell listens for. Storing only the ACTIVE set is what re-arms a
// cleared badge and keeps the store from growing: a card that left the payload takes its sigs with it.
const BADGE_SEEN_KEY = "romp:cardNotified";
function mirrorBadges(items: AskItem[], clears: ClearNoticeRow[], sdk: SdkNoticeRow[], sync: SyncNoticeRow[]): void {
  let seen: string[] = [];
  try { seen = JSON.parse(localStorage.getItem(BADGE_SEEN_KEY) || "[]"); } catch { /* fresh */ }
  const seenSet = new Set(seen);
  const badges = badgeNotices(items, seenSet);
  // /clear boundary settles share the same seen-set + bell (the user 2026-07-27): a clear that
  // dropped open cards logs one durable entry naming them, so the drop is never silent.
  const boundary = clearBoundaryNotices(clears, seenSet);
  // …and so do SDK-backend failures (the user 2026-07-28): a crashed session thread, a dropped stream
  // or a refused setting is a romp problem, and it belongs where the user's other problems are.
  const sdkProblems = sdkProblemNotices(sdk, seenSet);
  // …and so do the automatic fleet syncs (the user 2026-07-30): romp moving commits between machines
  // by itself left no trace once the network panel's phase line cleared, successes least of all.
  const syncs = syncNotices(sync, seenSet);
  for (const n of [...badges.notices, ...boundary.notices, ...sdkProblems.notices, ...syncs.notices]) {
    try {
      window.parent?.postMessage({ romp: "notify", kind: n.kind, text: n.text, sid: n.sid, itemId: n.itemId }, "*");
    } catch { /* no shell (VS Code view) */ }
  }
  const active = new Set([...badges.active, ...boundary.active, ...sdkProblems.active, ...syncs.active]);
  try { localStorage.setItem(BADGE_SEEN_KEY, JSON.stringify(Array.from(active))); } catch { /* storage full */ }
}

// ── HOVER-FREEZE (the user 2026-08-24) ──────────────────────────────────────────────────────────
// While the pointer rests on a card, the board must not move under it: incoming feed payloads QUEUE
// (newest wins — intermediate states were never on screen, so nothing owes them an animation)
// instead of rendering, and the deferred churn shows as a subtle +N/-N beside the column pills and,
// in grouped mode, the session headers. Only the PAYLOAD path defers: the hovered card's own
// controls and every local gesture still render live from the displayed model. Flush is event-based
// (repo rule, no timers): the hovered card's mouseleave applies everything at once — a card CLEARED
// under the pointer flushes too, via its synthetic mouseleave — and window blur is the backstop.
let freezeKey: string | null = null;     // the hovered card's focus key, or null — pointer truth, no debounce
let pendingFeedPayload: any = null;      // newest queued payload; older ones are superseded unseen
function freezeEnter(key: string): void { freezeKey = key; }
function freezeLeave(key: string): void {
  if (tabScopeKey === key) releaseTabScope();   // hover-away releases the keyboard scope too
  if (freezeKey !== key) return;
  freezeKey = null;
  flushFreeze();
}
let flushQueued = false;
function flushFreeze(): void {
  if (flushQueued) return;
  flushQueued = true;
  queueMicrotask(() => {   // after the CURRENT gesture's handlers finish — a synthetic mouseleave
    flushQueued = false;   // dispatched mid-click (the clear path) must not re-render the board under
    //                        the rest of its own handler (pendingCleared.add, .dismissing come after
    //                        the dispatch). Gesture-ordering, not a timer: no time window anywhere.
    // EITHER holder keeps the queue (post-merge audit 2026-08-24): with the keyboard scope on card
    // A and the pointer merely visiting card B, B's mouseleave queues this flush — running it would
    // apply the payload and move the card being keyed. The scope's own release paths all flush.
    if (freezeKey || tabScopeKey) return;
    const m = pendingFeedPayload;
    pendingFeedPayload = null;
    if (m) applyFeedPayload(m);          // render() repaints the badges away (nothing pending)
  });
}
window.addEventListener("blur", () => { releaseTabScope(); freezeKey = null; flushFreeze(); });   // backstop:
//   focus left the pane — BOTH gate holders release (the keyboard scope has no pointer to leave with)

// The queued payload's would-be card list — the same pre-filters applyFeedPayload will apply,
// WITHOUT its bookkeeping side effects (a hint must never mutate pendingCleared or the disclosure
// state; the flush re-runs the real thing).
function payloadView(m: any): AskItem[] {
  const incoming: AskItem[] = Array.isArray(m.asks) ? m.asks : [];
  const only = onlyTag();
  const vis = only ? incoming.filter((a) => matchesOnly(a.name, only)) : incoming;
  let out = pendingCleared.size ? vis.filter((a) => !pendingCleared.has(a.itemId)) : vis;
  // mirror the optimistic-restore overlay applyFeedPayload keeps (read-only): a restored card the
  // flush will push right back must not hint as a departure
  if (pendingRestored.size) {
    const present = new Set(out.map((a) => a.itemId));
    const inIncoming = new Set(incoming.map((a) => a.itemId));   // carried by the payload → the flush DROPS
    out = out.slice();                                            // its overlay entry; it rides (or is filtered
    //                                                               with) the normal path, so no re-add here
    for (const it of pendingRestored.values()) if (!present.has(it.itemId) && !inIncoming.has(it.itemId)) out.push(it);
  }
  return out;
}
// Paint (or clear) the deferred-churn badges: +N accent / -N block-red beside each column pill, and
// beside each on-board session header in grouped mode. Ensure-once spans on the build-once column
// heads; session badges ride the reconciled headers (found by the data-fsid stamp) and are re-synced
// at every render tail, so a local re-render while frozen can never strand a stale count.
function paintFreezeParts(b: HTMLElement, c: { add: number; del: number }): void {
  b.replaceChildren();
  if (c.add) { const i = el("i", "fz-add"); i.textContent = "+" + c.add; b.appendChild(i); }
  if (c.add && c.del) b.appendChild(document.createTextNode("/"));
  if (c.del) { const i = el("i", "fz-del"); i.textContent = "-" + c.del; b.appendChild(i); }
  // the explicit reading (the user 2026-08-25, liking the +N/−N but wanting it to say what it
  // means): a parenthetical in the accent dress, well under their verbosity ceiling
  const note = el("i", "fz-note");
  note.textContent = " (" + (c.add + c.del) + " changed — mouse away to apply)";
  b.appendChild(note);
  b.title = "updates waiting while you hover — they apply when the pointer leaves the card";
}
// Has the FROZEN card's own content changed in the withheld payload? Churn elsewhere and a stale
// card under the pointer are different facts (the user 2026-08-25) — this one gets its own line,
// shown WITH the churn badges when both are true. Group keys compare the turn's member set
// (itemId-sorted, so payload order can never fake a change); ask keys compare the one item.
function pendingSelfChanged(key: string): boolean {
  if (!pendingFeedPayload) return false;
  const pend = payloadView(pendingFeedPayload);
  const byId = (a: AskItem, b2: AskItem) => (a.itemId < b2.itemId ? -1 : 1);
  // CONTENT-projected compares only (contentSig — the user 2026-08-25): the whole-item compare
  // flagged the per-build recency-tint recompute as "this card updated" on nearly every card
  if (key.startsWith("g:")) {
    const tid = key.slice(2);
    const cur = asks.filter((a) => a.turnId === tid).slice().sort(byId).map((a) => contentSig(a as any)).join("|");
    const nxt = pend.filter((a) => a.turnId === tid).slice().sort(byId).map((a) => contentSig(a as any)).join("|");
    return cur !== nxt;
  }
  return contentSig(asks.find((a) => a.itemId === key) as any) !== contentSig(pend.find((a) => a.itemId === key) as any);
}
function paintFreezeBadges(): void {
  if (!pendingFeedPayload) {
    document.querySelectorAll(".freeze-badge").forEach((n) => n.remove());
    document.getElementById("freeze-selfnote")?.remove();
    return;
  }
  const toItems = (list: AskItem[]) => viewFiltered(list).map((a) => ({ id: a.itemId, col: askColumn(a) as string, sid: a.sid }));
  const d = freezeDiff(toItems(asks), toItems(payloadView(pendingFeedPayload)));
  const put = (host: Element | null, c: { add: number; del: number } | undefined) => {
    if (!host) return;
    let b = host.querySelector(":scope > .freeze-badge") as HTMLElement | null;
    if (!c || (!c.add && !c.del)) { b?.remove(); return; }
    if (!b) { b = el("span", "freeze-badge"); host.appendChild(b); }
    paintFreezeParts(b, c);
  };
  for (const key of ["asks", "needsInput", "completed"]) {
    put(document.querySelector(".feed-col.col-" + key + " .feed-col-head"), d.cols[key]);
  }
  const groupedNow = feedPrefs().grouped;
  document.querySelectorAll<HTMLElement>(".feed-sess-head").forEach((h) => {
    put(h, groupedNow ? d.sess[h.getAttribute("data-fsid") || ""] : undefined);
  });
  // the hovered/keyed card's OWN pending update — its own line, independent of the churn badges
  // (both show when both are true). Body-mounted and pointer-inert: it must never affect hover,
  // and the frozen card's rect is stable by construction (that is the freeze's whole contract).
  const selfKey = freezeKey || tabScopeKey;
  const selfCard = selfKey ? cardElByKey(selfKey) : null;
  let selfNote = document.getElementById("freeze-selfnote");
  if (!selfKey || !selfCard || !pendingSelfChanged(selfKey)) {
    selfNote?.remove();
  } else {
    if (!selfNote) {
      selfNote = el("div", "");
      selfNote.id = "freeze-selfnote";
      selfNote.textContent = "(this card updated — mouse away to refresh)";
      document.body.appendChild(selfNote);
    }
    const r = selfCard.getBoundingClientRect();
    selfNote.style.left = Math.round(r.left + 12) + "px";
    selfNote.style.top = Math.round(r.bottom - 24) + "px";
  }
}

// ── CARD KEYBOARD SCOPE (the user 2026-08-24) ──────────────────────────────────────────────────
// Tab, pressed while a card is hovered (a click puts the pointer there too) or held by the keyboard
// card cursor, scopes to THAT card: it cycles every visible control on it — section pills, Clear,
// retries, the bell, follow-up, sub-goal links — wrapping at the ends; Enter/Space activate; the
// accent .kbd-focus ring marks the stop. Escape or the pointer leaving the card releases back to
// normal page order. Focus survives the feed's constant re-renders by LOGICAL control identity
// (class + label, then the old slot) — the click-safety rule applied to keyboard focus. While focus
// is inside, the card holds the payload gate hover-freeze uses, so the board cannot move the card
// being keyed (same gate as the pointer).
let tabScopeKey: string | null = null;
let tabScopeSig: { sig: string; idx: number } | null = null;
function cardElByKey(key: string): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-key="' + (key.startsWith("g:") ? key : "a:" + key) + '"]');
}
function cardControls(card: HTMLElement): HTMLElement[] {
  // the card's own DOM order IS its visual reading order — title row, pills, tail controls
  return Array.from(card.querySelectorAll<HTMLElement>(KB_EL_SEL)).filter((e) => e.offsetParent !== null);
}
function ctrlSig(e: HTMLElement): string {
  return e.className + "|" + (e.getAttribute("aria-label") || e.textContent || "").trim().slice(0, 24);
}
function tabScopeFocus(card: HTMLElement, els: HTMLElement[], i: number): void {
  document.querySelectorAll(".kbd-focus").forEach((n) => n.classList.remove("kbd-focus"));
  const el2 = els[i];
  if (!el2) return;
  tabScopeKey = kbHoverId(card);
  tabScopeSig = { sig: ctrlSig(el2), idx: i };
  if (el2.tabIndex < 0 && !el2.matches("button, a, input")) el2.tabIndex = -1;   // focusable, outside page order
  el2.classList.add("kbd-focus");
  el2.focus();
}
function releaseTabScope(): void {
  if (!tabScopeKey) return;
  tabScopeKey = null;
  tabScopeSig = null;
  document.querySelectorAll(".kbd-focus").forEach((n) => n.classList.remove("kbd-focus"));
  const ae = document.activeElement as HTMLElement | null;
  if (ae && ae.closest(".fitem")) ae.blur();
  if (!freezeKey) flushFreeze();   // the scope held the payload gate — releasing applies the queue
}
window.addEventListener("keydown", (e) => {
  if (document.getElementById("feed-modal")) return;   // the modal owns keys while it is open
  if (e.key === "Escape" && tabScopeKey) {
    e.preventDefault(); e.stopPropagation();
    releaseTabScope();
    return;
  }
  if (e.key === "Tab") {
    let card = tabScopeKey ? cardElByKey(tabScopeKey) : null;
    if (!card) card = (freezeKey ? cardElByKey(freezeKey) : null) || kbCardEl;   // hover/click, else the kb cursor
    if (!card || !card.isConnected) return;
    const ae = document.activeElement as HTMLElement | null;
    if (ae && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName) && !card.contains(ae)) return;   // typing elsewhere
    const els = cardControls(card);
    if (!els.length) return;
    e.preventDefault(); e.stopPropagation();
    let i = ae ? els.indexOf(ae) : -1;
    i = e.shiftKey ? (i <= 0 ? els.length - 1 : i - 1) : (i >= els.length - 1 ? 0 : i + 1);   // WRAP at the ends
    //                                                     (Shift+Tab = the same cycle, reversed)
    tabScopeFocus(card, els, i);
    // SELECT-ON-FOCUS for the view pills (the user 2026-08-24): landing on a SELECTOR applies it at
    // once, no Enter — radio-group semantics. The split: .fask-secbtn (Background/Summary/Sub-goals,
    // which switch what the card SHOWS) are selectors; everything else — Clear, retries, the bell,
    // follow-up, session names, sub-goal links — is an ACTION and must never fire from mere focus
    // (action is the default for anything ambiguous). The click lives HERE, in the Tab gesture, and
    // NOT in tabScopeFocus: the render-tail focus restore calls tabScopeFocus too, and a click there
    // would re-apply per re-render — one application per user gesture. The already-selected pill is
    // a no-op (focus keeps, nothing toggles), so cycling through it never flips the card's view off.
    const landed = els[i];
    if (landed && landed.matches(".fask-secbtn") && !landed.classList.contains("on")) landed.click();
    return;
  }
  if ((e.key === "Enter" || e.key === " ") && tabScopeKey) {
    const ae = document.activeElement as HTMLElement | null;
    const card = cardElByKey(tabScopeKey);
    if (!ae || !card || !card.contains(ae)) return;
    if (ae.matches("button, a, input")) return;   // native activation already fires the click
    e.preventDefault();
    ae.click();                                   // EXACTLY a mouse click on that control
  }
}, true);

// The feed payload's full application — model swap, bookkeeping, render. One body, two callers:
// the message handler applies live when no card is hovered; flushFreeze applies the newest queued
// payload when the pointer leaves (hover-freeze above).
function applyFeedPayload(m: any): void {
  judgeLimit = m.judgeLimit && typeof m.judgeLimit === "object"
    ? m.judgeLimit as { bucket?: string; resets_at?: number; model?: string } : null;
  const incomingAsks: AskItem[] = Array.isArray(m.asks) ? m.asks : [];
  // A clear is CONFIRMED once the kernel's payload no longer lists it → stop suppressing it. Then drop
  // any still-pending (kernel hasn't caught up) from this payload so a stale push can't resurrect them.
  for (const id of Array.from(pendingCleared)) if (!incomingAsks.some((a) => a.itemId === id)) pendingCleared.delete(id);
  // demo/recording view filter (the user 2026-07-14): `#only=<tag>` shows only matching-name cards; the
  // clear/follow bookkeeping above still runs against the FULL payload, so hidden cards stay consistent.
  // Self-clean the persisted disclosure state against the AUTHORITATIVE live set (the user 2026-07-24).
  // incomingAsks, deliberately — not `visible` below: `#only=` hides cards without ending them, and
  // pruning against the filtered list would throw away the hidden cards' sections. Event-based: a card
  // leaving the payload (cleared, archived) IS the signal, so nothing ages out on a timer.
  pruneViewStateTo(new Set(incomingAsks.map((a) => a.itemId)));
  const only = onlyTag();
  const visible = only ? incomingAsks.filter((a) => matchesOnly(a.name, only)) : incomingAsks;
  asks = pendingCleared.size ? visible.filter((a) => !pendingCleared.has(a.itemId)) : visible;
  // confirm/clear optimistic follow-up moves against the authoritative payload. buildId says WHEN this
  // payload read the store, which is what makes "the kernel has answered my click" an event and not a
  // guess (see reconcileFollowMove); a kernel too old to send one reads as 0 and simply never confirms
  // an acked prediction early, leaving the backstop to retire it.
  lastFeedEvent = "payload";                         // tripwire: this render's inputs are the fresh payload
  lastPayloadBuildId = typeof m.buildId === "number" ? m.buildId : 0;
  // per-host counters when this is a merged multi-kernel payload (mergeHostFeeds.buildIds);
  // absent on a single-kernel payload, where the top-level buildId is the one counter there is
  const perHostBuildIds = m.buildIds && typeof m.buildIds === "object" && !Array.isArray(m.buildIds)
    ? m.buildIds as Record<string, number> : undefined;
  reconcileFollowMove(incomingAsks, lastPayloadBuildId, perHostBuildIds);
  reconcilePendingDone(incomingAsks);   // retire an optimistic tick once the real tree carries it
  // An optimistic Undo is CONFIRMED once the kernel's payload carries the id again → stop forcing it.
  // Until then, keep the cached card in `asks` so the replace above can't drop the just-restored card (flicker).
  if (pendingRestored.size) {
    for (const id of Array.from(pendingRestored.keys())) if (incomingAsks.some((a) => a.itemId === id)) pendingRestored.delete(id);
    const present = new Set(asks.map((a) => a.itemId));
    for (const it of pendingRestored.values()) if (!present.has(it.itemId)) asks.push(it);
  }
  workingSet = new Set(Array.isArray(m.working) ? m.working : []);
  if (typeof m.selfHost === "string" && m.selfHost) feedSelfHost = m.selfHost;
  // Index every session's colour by the name a peer would address it by. Federation merges the hosts'
  // payloads, so one pass over the merged asks covers the whole fleet; a card whose session has no
  // cards of its own simply gets no colour, which is honest rather than invented.
  for (const a of incomingAsks) {
    if (!a.color || !a.name) continue;
    sessionColors.set(a.name, a.color.bg);
    const c = a.sid ? a.sid.indexOf(":") : -1;             // remote sid → also index the bare name under its host
    if (c > 0 && !a.name.includes(":")) sessionColors.set(a.sid.slice(0, c) + ":" + a.name, a.color.bg);
  }
  awaitingSet = new Set(Array.isArray(m.awaiting) ? m.awaiting : []);   // await-green awaiting dots (the user 2026-07-13)
  unknownSet = new Set(Array.isArray(m.stateUnknown) ? m.stateUnknown : []);   // listed-but-unreadable → gray ring, never a blank
  noteStatusInputs();   // the dots and delegation lines every card paints read these → a change repaints every card
  bgServicesMap = m.bgServices && typeof m.bgServices === "object" ? m.bgServices : {};   // session name -> judge-classified service descs → the session-header chip (2026-07-24)
  if (Array.isArray(m.order)) sessionOrder = m.order.filter((x: any) => typeof x === "string");   // grouped-mode session rank (tab/lane order)
  pendingHosts = Array.isArray(m.pendingHosts) ? m.pendingHosts.filter((h: any) => typeof h === "string") : [];
  pendingDead = Array.isArray(m.pendingDead) ? m.pendingDead.filter((h: any) => typeof h === "string") : [];
  syncHostloadBackstops();
  if (m.views && typeof m.views === "object") feedTagViews = m.views as SessionViews;   // tag DEFINITIONS only — never `active`
  if (Array.isArray(m.sessions)) {
    sessionsMeta = m.sessions.filter((s: any) => s && typeof s.sid === "string" && typeof s.name === "string");
    // a filter aimed at a session the tab strip no longer shows is moot — clear it (the deciding
    // event: the session left the tab list), rather than leaving the board silently pinned to nothing
    if (feedOnlySid && !sessionsMeta.some((s) => s.sid === feedOnlySid)) setFeedOnly(null);
  }
  hostNow = typeof m.now === "number" ? m.now : Math.floor(Date.now() / 1000);
  mirrorBadges(incomingAsks, Array.isArray(m.clearNotices) ? m.clearNotices : [],
    Array.isArray(m.sdkNotices) ? m.sdkNotices : [],
    Array.isArray(m.syncNotices) ? m.syncNotices : []);   // card trouble chips + /clear drops + SDK failures + fleet syncs also log in the shell's bell (chips stay on the cards)
  if (typeof m.dismissedCount === "number") dismissedCount = m.dismissedCount;
  clearUndoBusy();   // the push the undo was waiting on has landed (or any fresher one) — cue off
  if (typeof m.showDismissed === "boolean") showDismissed = m.showDismissed;
  if (typeof m.canUndoClear === "boolean") canUndoClear = m.canUndoClear;
  render();
}


window.addEventListener("message", (e: MessageEvent) => {
  const m = e.data;
  if (!m) return;
  if (m.type === "pipeState") { pipeBanner(!!m.up, Number(m.queued) || 0); return; }
  if (m.romp === "paneFocus") { kbEnterCards(); return; }   // the shell handed us keyboard focus → arm card nav
  if (m.romp === "revealCard") {
    // a bell-entry click jumps back to the card it was minted from (the user 2026-07-28): scroll it
    // into view and pulse it accent so the eye lands on the right card. A card that no longer exists
    // under its own key (cleared, or folded into a group) falls back to opening the session.
    const target = document.querySelector(`[data-key="a:${String(m.itemId || "")}"]`) as HTMLElement | null;
    if (target) {
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      target.classList.remove("reveal-pulse"); void target.offsetWidth;   // restart the animation on a repeat jump
      target.classList.add("reveal-pulse");
      target.addEventListener("animationend", () => target.classList.remove("reveal-pulse"), { once: true });
    } else if (m.sid) {
      vscodeApi?.postMessage({ type: "openSession", id: String(m.sid) });
    }
    return;
  }
  if (m.type === "feed") {
    // HOVER-FREEZE: a hovered card must not move on screen — queue the payload (newest wins) and
    // hint the deferred churn on the headers instead; mouseleave/blur flush it (see freezeEnter).
    if (freezeKey || tabScopeKey) { pendingFeedPayload = m; paintFreezeBadges(); return; }
    applyFeedPayload(m);
  } else if (m.type === "hoverCards") {
    // rail-dot hover in the CHAT panel → white-outline the card(s) built from
    // that turn, plus the matching ROWS inside an open modal (eid). The host
    // fans the same hover out to the timeline.
    extHoverKeys = new Set(Array.isArray(m.keys) ? m.keys.map(String) : []);
    extHoverEid = typeof m.eid === "string" && m.eid ? m.eid : null;
    applyExtHover();
  } else if (m.type === "nodeOverrideResult" && typeof m.nodeId === "string") {
    // The kernel's verdict on a Done click. Agreement is silent — the optimistic tick simply stays until
    // the next payload carries it for real. Disagreement REVERTS the tick and says why, out loud: a
    // cross-off that quietly un-crossed itself a second later would be worse than the slow version it
    // replaced (fail loudly, CLAUDE.md).
    if (!m.ok) {
      pendingDone.delete(m.nodeId);
      renderModal();
      feedToast("couldn't mark that sub-goal done: " + (String(m.error || "") || "the kernel refused it"));
    }
  } else if (m.type === "redistillResult" && typeof m.itemId === "string") {
    // The kernel's verdict on the warn modal's Try again — BOTH answers toast (the user 2026-08-13,
    // round 2: success used to lean on the Distilling… swirl, which a Working card withholds, so a
    // WORKING retry read as a silent no-op). The success copy promises what is actually true in every
    // column: the line regenerates on the next judge pass over this card.
    if (redistillWatch && redistillWatch.itemId === m.itemId) {
      window.clearTimeout(redistillWatch.timer);
      redistillWatch = null;
    }
    if (m.ok) feedToast("summary retry armed — it regenerates on the next judge pass over this card");
    else feedToast("couldn't retry the summary: " + (String(m.error || "") || "the kernel refused it"));
  } else if (m.type === "revealCards") {
    // chat rail CLICK → scroll to the card(s) covering that turn and pulse them (the user 2026-07-23).
    // Distinct from hoverCards, which only outlines whatever is already on screen: this one MOVES the
    // feed. A turn can belong to several cards; all of them pulse, and the first is what we scroll to.
    revealCards(new Set(Array.isArray(m.keys) ? m.keys.map(String) : []));
  } else if (m.type === "openCard" && typeof m.key === "string") {
    // rail-dot click → open this card's modal (key is fullscreenAskId-shaped:
    // ask itemId, "i:<itemId>" standalone, "g:<turnId>" group). hl = the clicked
    // turn's event id: ring its row(s) and scroll the first one into view.
    fullscreenAskId = m.key;
    vscodeApi?.postMessage({ type: "cardOpened", itemId: m.key, sid: "" });   // rail-dot opens count too (2026-08-25)
    if (typeof m.hl === "string" && m.hl) extHoverEid = m.hl;
    renderModal();
    applyExtHover();
    if (extHoverEid) document.querySelector(".dot-hl[data-eid]")?.scrollIntoView({ block: "center" });
  } else if (m.type === "err" && typeof m.text === "string" && m.text) {
    // the dialog interrupts; the bell KEEPS it (the user 2026-07-29) — dismissing the modal must not erase
    // the fact that a message never landed. Same {romp:'notify'} bridge the card-badge mirror below uses.
    const title = typeof m.title === "string" && m.title ? m.title : "That action was not delivered";
    const copy = typeof m.copy === "string" ? m.copy : "";
    window.parent?.postMessage({ romp: "notify", kind: "undelivered",
                                 text: copy ? title + ": " + copy : title,
                                 sid: typeof m.sid === "string" ? m.sid : "" }, "*");
    showErrDialog(title, m.text, copy);
  } else if (m.type === "pickerOptions" && typeof m.name === "string") {
    // the host read the blocked session's live resume-picker screen — show the
    // same options in-page; a choice goes back as keystrokes (transport only,
    // the user decides — the never-auto-answer rule holds).
    showPickerDialog(String(m.name), Array.isArray(m.options) ? m.options.map(String) : []);
  } else if (m.type === "colorSync" && typeof m.sid === "string" && typeof m.bg === "string") {
    // VS Code leg of the optimistic colour echo (see applyColorEcho): the extension fans the chat
    // pane's swatch pick here, since each webview's localStorage is its own — no storage event.
    applyColorEcho(m.sid, m.bg);
  } else if (m.type === "cardPredict" && Array.isArray(m.ids)) {
    // kernel fan-back (the user 2026-07-20): a context-carrying reply just fired SOMEWHERE — the chat's
    // citation follow-up, a picker/permission answer typed in the chat, another feed view's button — so
    // flip the named card(s) to Working NOW instead of waiting out the rebuild+push round trip. Same
    // prediction machinery as the local buttons (idempotent when this view initiated it); the kernel's
    // next push stays authoritative. An id may name a SUB-goal (a per-sub follow-up target): resolve it
    // to the visible top card that carries it in its tree.
    const kind: MoveKind = m.flavor === "answer" ? "answer" : "followup";
    let moved = false;
    for (const raw of m.ids.map(String)) {
      const top = asks.find((a) => a.itemId === raw) ?? asks.find((a) => a.tree?.some((n) => n.id === raw));
      if (top && top.column !== "working") { optimisticFollowMove(top.itemId, kind); moved = true; }
    }
    if (moved) render();
  } else if (m.type === "cardMoveAck" && Array.isArray(m.ids)) {
    // the kernel's answer to the prediction above (_ack_card_move). Ids resolve exactly as cardPredict's do
    // — the kernel may name a SUB-goal that a visible top card carries — and an id this view never predicted
    // is a no-op, so the ack is safe to broadcast to every feed pane.
    const bid = typeof m.buildId === "number" ? m.buildId : 0;
    // which kernel's counter `bid` is on: federation stamps a remote ack with its host (prefixInbound);
    // an unstamped ack came from the local kernel — host "", the same value hostOf gives local cards
    const ackHost = typeof m.host === "string" ? m.host : "";
    for (const raw of m.ids.map(String)) {
      const top = asks.find((a) => a.itemId === raw) ?? asks.find((a) => a.tree?.some((n) => n.id === raw));
      ackFollowMove(top ? top.itemId : raw, !!m.ok, bid, ackHost);
    }
  }
});

// ---- quarantine decision dialog (the user 2026-07-26): the card is compact, so THIS is where the
// whole held message is read and decided. Step 1: the full body, read-only (peer content, never
// auto-run; editing was cut from the flow), with Approve / Deny / Cancel. Deny flips the SAME dialog
// to step 2: an optional note back to the sender ("Deny & send note" / "Deny" / Cancel) — the bus
// mails it to the origin host so the sender's agent learns why instead of waiting forever. Lives on
// document.body OUTSIDE the re-rendered feed root, so a kernel push mid-decision can't eat the note.
// `decide` is the owning card's decision closure — it carries the mid + sid and flips the card buttons.
interface QuarEnd { host: string; name: string; color?: string }

function showQuarantineDialog(from: QuarEnd, to: QuarEnd, body: string,
                              decide: (action: string, busy: string, text: string, feedback?: string) => void,
                              denyFirst: boolean) {
  document.getElementById("quar-dialog")?.remove();
  const overlay = el("div", "pickdlg-overlay"); overlay.id = "quar-dialog";
  const box = el("div", "pickdlg-box qdlg-box");
  const title = el("div", "pickdlg-title");
  // the SAME route the card shows, so opening the message doesn't re-word who it is between
  const sender = `${from.host}:${from.name}`;
  const route = () => title.replaceChildren(
    Object.assign(el("span", "qdlg-lead"), { textContent: "New message" }),
    quarWho(from.host, from.name, from.color),
    Object.assign(el("span", "fq-arrow"), { textContent: "\u2192" }),
    quarWho(to.host, to.name, to.color));
  route();
  const view = el("div", "qdlg-view");
  view.textContent = body;
  const row = el("div", "qdlg-actions");
  box.append(title, view, row);

  // No Cancel button (the user 2026-07-26: two choices, approve or deny — this gate is only there to
  // catch something malicious). Clicking the backdrop still closes without deciding; the message
  // stays held either way.
  const denyStep = () => {
    title.replaceChildren(document.createTextNode(`Deny the message from ${sender}. Send a note back?`));
    const ta = el("textarea", "qdlg-text qdlg-feedback") as HTMLTextAreaElement;
    ta.placeholder = "optional: tell the sender why (delivered to them as postal mail)";
    row.replaceChildren();
    const withNote = el("button", "fdismiss fq fq-no") as HTMLButtonElement; withNote.textContent = "Deny & send note";
    withNote.title = "drop the message and mail your note back to the sender";
    withNote.onclick = () => { decide("deny", "Denying…", body, ta.value.trim() || undefined); overlay.remove(); };
    const bare = el("button", "fdismiss fq") as HTMLButtonElement; bare.textContent = "Deny without note";
    bare.title = "drop the message — nothing is sent back";
    bare.onclick = () => { decide("deny", "Denying…", body); overlay.remove(); };
    row.append(withNote, bare);
    box.insertBefore(ta, row);
    ta.focus();
  };

  if (denyFirst) {
    denyStep();
  } else {
    const ok = el("button", "fdismiss fq fq-ok") as HTMLButtonElement; ok.textContent = "Approve";
    ok.title = "deliver this message to the recipient session";
    ok.onclick = () => { decide("approve", "Delivering…", body); overlay.remove(); };
    const no = el("button", "fdismiss fq fq-no") as HTMLButtonElement; no.textContent = "Deny";
    no.title = "drop this message — with the option of a note back to the sender";
    no.onclick = () => denyStep();
    row.append(ok, no);
  }
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}

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

// The kernel's LOUD channel (the user 2026-07-29). The feed had no error surface at all: every failure here
// went to feedToast, which fades, and a kernel `warn` had no handler on this page whatsoever — so an action
// fired from a card could fail with the page showing nothing. A refused reply is typed work that is gone, so
// it gets a dialog you must dismiss, reusing the resume-picker dialog's chrome rather than inventing another.
function showErrDialog(title: string, text: string, copy: string) {
  document.getElementById("err-dialog")?.remove();
  const overlay = el("div", "pickdlg-overlay"); overlay.id = "err-dialog";
  const box = el("div", "pickdlg-box");
  const h = el("div", "pickdlg-title"); h.textContent = title;
  const d = el("div", "pickdlg-detail"); d.textContent = text;
  box.append(h, d);
  if (copy) {
    const c = el("button", "pickdlg-opt");
    c.textContent = "Copy my text";
    c.onclick = () => { navigator.clipboard?.writeText(copy); c.textContent = "Copied"; };
    box.append(c);
  }
  const ok = el("button", "pickdlg-opt pickdlg-cancel");
  ok.textContent = "Dismiss";
  ok.onclick = () => overlay.remove();
  box.append(ok);
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
    // via extHoverMatches, NOT a raw set lookup: the host names cards by goal-node id and the DOM keys
    // them by render namespace ("a:<itemId>"), so comparing them directly never matched and the whole
    // timeline→feed / chat→feed highlight was dead (see ./card-key).
    c.classList.toggle("dot-hl", extHoverMatches(c.dataset.key, extHoverKeys));
  });
  document.querySelectorAll<HTMLElement>("[data-eid]").forEach((c) => {
    c.classList.toggle("dot-hl", !!extHoverEid && c.dataset.eid === extHoverEid);
  });
}

// Scroll to the named cards and pulse them. Same key bridge as the hover (./card-key), so it lands on
// exactly the cards a hover would have outlined. The class is removed and re-added across a forced
// reflow, or a second click on the same card would re-add a class it already has and CSS would replay
// nothing — the "clicked again and it didn't flash" bug this shape avoids.
function revealCards(keys: Set<string>) {
  // A target inside a FOLDED thread has no element to scroll to, and a jump that lands on nothing is the
  // silent no-op a collapse must never cause (the user 2026-07-31). Unfold the owning thread(s) and render
  // before looking: the navigation wins over the disclosure, and the thread stays open afterwards so you
  // can see where you were taken.
  if (collapsedThreads.size) {
    let opened = false;
    for (const a of asks) {
      if (collapsedThreads.has(a.sid) && extHoverMatches("a:" + a.itemId, keys)) {
        collapsedThreads.delete(a.sid); opened = true;
      }
    }
    if (opened) render();
  }
  const hits = Array.from(document.querySelectorAll<HTMLElement>("[data-key]"))
    .filter((c) => extHoverMatches(c.dataset.key, keys));
  if (!hits.length) return;
  hits[0].scrollIntoView({ block: "center", behavior: "smooth" });
  for (const c of hits) {
    c.classList.remove("card-pulse");
    void c.offsetWidth;
    c.classList.add("card-pulse");
  }
}

// Keep "Xm ago" honest between host pushes (host reposts ~1×/min for color fade).
setInterval(() => {
  const now = Math.floor(Date.now() / 1000);
  for (const [id, card] of askEls) {
    const it = asks.find((a) => a.itemId === id);
    const t = (card as any)._time as HTMLElement | undefined;
    if (it && t) t.textContent = relAge(now - it.t);
  }
}, 15000);

initFileView((m) => vscodeApi?.postMessage(m));   // the file browser opens the viewer in this pane (and saves ride the poster)
initFileBrowse((m) => vscodeApi?.postMessage(m));   // …and a Browse files ask lands its sibling overlay

vscodeApi?.postMessage({ type: "ready" });
