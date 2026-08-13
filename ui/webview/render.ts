import { marked } from "marked";
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import json from "highlight.js/lib/languages/json";
import xml from "highlight.js/lib/languages/xml";
import cssLang from "highlight.js/lib/languages/css";
import markdown from "highlight.js/lib/languages/markdown";
import diff from "highlight.js/lib/languages/diff";
import yaml from "highlight.js/lib/languages/yaml";
import type { ParsedAsk } from "../ask-types";
import { quoteReply } from "../quote";
import { markerLabel } from "./time-marker";
import { compactDisplay, toolCounts, type DisplayItem } from "./compact";
import { loadSettings, onExternalSettingsChange, installSettingsSync, type RompSettings } from "./settings";
import { delegate } from "./actions";
import { isClearCmd, openTopTitles, clearConfirmDetail } from "./clear-confirm";
import { prebuildPlan, type ViewState } from "./prebuild";
import { reconcileTabOrder } from "./tab-order";
import { writeViewOrder } from "./view-order";
import { titleWithKey } from "./keybindings";
import { mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional } from "./provisional";
import { onlyTag, matchesOnly } from "./only-filter";
import { numberDiff, type DiffRow } from "./diff-lines";
import { parseAgentNotif, type AgentNotif } from "./agent-notif";
import { previewKind, previewFull, canPreview, fileUrl } from "./preview";
import { pastedFilePath } from "./paste-path";
import { hostNameNodes, hostPrefix, hostOf, hostIsDown, hostDownNote } from "./host-prefix";
import { dirStatusHint, nextDirActive, createDirPrompt, type DirStatus } from "./dir-complete";
import { mediaSrc, kernelUrl } from "./media";
import { initStrip, fmtReset } from "./strip";
import { apiErrorReason } from "./api-error-reason";
import { mathBlock, mathInline } from "./math";

for (const [name, lang] of Object.entries({
  bash, sh: bash, shell: bash, python, py: python, javascript, js: javascript,
  typescript, ts: typescript, json, xml, html: xml, css: cssLang, markdown, md: markdown,
  diff, yaml, yml: yaml,
})) {
  try { hljs.registerLanguage(name, lang as any); } catch { /* dup alias */ }
}

marked.setOptions({ gfm: true, breaks: false });
// Strikethrough requires DOUBLE tildes (the user 2026-06-26). marked's built-in GFM `del` tokenizer also
// fires on a SINGLE tilde, so prose like "near the ~21 Wh/day budget … gives ~1.5–2 days" renders as one big
// <del> struck through from the first ~ to the second. GitHub itself only strikes ~~double~~, so match that:
// a lone ~ (commonly "approximately") stays literal. Returning undefined lets marked treat the ~ as text.
marked.use({
  tokenizer: {
    del(src: string) {
      const m = /^~~(?=\S)([\s\S]*?\S)~~/.exec(src);
      if (!m) return undefined;
      return { type: "del", raw: m[0], text: m[1], tokens: (this as { lexer: { inlineTokens(s: string): unknown[] } }).lexer.inlineTokens(m[1]) };
    },
  },
} as Parameters<typeof marked.use>[0]);

// TeX math ($..$, $$..$$, \(..\), \[..\]) rendered via KaTeX. All delimiter heuristics (the
// $-vs-shell/price disambiguation) live in math.ts; the output is plain spans + inline styles
// (output: "html"), which DOMPurify's html profile in md() passes through unchanged.
marked.use({ extensions: [mathBlock, mathInline] });

// One answered (or pending) question on an AskUserQuestion turn: the prompt + its options, plus the
// user's answer TEXT per question (`chosen`). Answer text may name an option label OR be free-text
// ("Other"), and is empty while the question is still pending (multi-select answers arrive pre-split).
type AskAnswerBlock = { question: string; header?: string; options: { label: string; description?: string }[]; chosen: string[] };

// A completed background command's detail, keyed by its tool-use-id — the shell it ran + its output tail,
// joined in by the kernel (build_session's taskOutputs) so the inline completion card can expand into it.
type TaskOutputs = Record<string, { command: string; output: string }>;

type ChatEvent = (
  // mid/mids: postal message ids the kernel could NOT resolve into cards, carried on the raw turn so a
  // timeline arc into it still lands (see _hydrate_postal's unresolved path)
  | { kind: "user"; md: string; uuid?: string; ts?: string; reminders?: string[]; taskOutputs?: TaskOutputs; human?: boolean; romp?: boolean; rompAuto?: boolean; rompSystem?: boolean; followUp?: boolean; goal?: string; fuCtx?: string; mid?: string; mids?: string[]; images?: { src: string; path?: string }[]; undelivered?: boolean; echoT?: number; spacePaths?: string[]; pathLinks?: Record<string, string> }
  | { kind: "assistant"; md: string; uuid?: string; ts?: string; spacePaths?: string[]; pathLinks?: Record<string, string> }   // spacePaths: backticked filenames WITH spaces the kernel verified exist (build_session _space_paths) → whole-span links. pathLinks: path-shaped tokens the kernel verified against the filesystem, token → real open target (build_session _path_links) — the linkifier's gate
  | { kind: "thinking"; text: string; encrypted: boolean; uuid?: string; ts?: string }
  | {
      kind: "tool";
      name: string;
      desc: string;
      input: string;
      output: string;
      isError: boolean;
      uuid?: string;
      resultUuid?: string;   // tool_result line uuid (AUQ answer) — the deep-link anchor the timeline emits
      ts?: string;
      file?: string;
      diff?: string;
      // Edit/MultiEdit: REAL-line-number diff rows from Claude Code's structuredPatch (the kernel's
      // _patch_rows). Preferred over `diff`; absent on older records → the client falls back to numberDiff.
      diffRows?: DiffRow[];
      // AskUserQuestion only: the kernel joins the posed questions/options to the recorded answer and
      // attaches them here (the user 2026-06-16). Empty `chosen` while pending; filled once answered →
      // renderAsk flips the turn to the blue "you answered Claude's question" box.
      askAnswer?: AskAnswerBlock[];
      // Skill only: the skill's full instructions markdown (the isMeta content record / its live-stream
      // twin, joined by the kernel) → the tool's collapsed-by-default fold body (the user 2026-07-08).
      skillMd?: string;
    }
  | {
      kind: "postal-service";
      direction: "in" | "out";
      peer: string;
      color: { bg: string; fg: string } | null;
      body: string;
      summary?: string;  // incoming Haiku caption (≤9 words) — shown instead of the verbose body; body on hover
      intent?: string;   // sender-declared kind (delegate|coordinate|question) → the interaction-type chip; body-token parse is the legacy fallback
      mid?: string;      // postal message id (joins feed-modal handoff hovers to this card)
      t?: number;        // epoch seconds (incoming)
      park?: boolean;
      status?: "delivered" | "parked"; // outgoing
      ts?: string;
      uuid?: string;
    }
  // Claude Code's NATIVE teammate/agent-message channel (one agent messaged this session) — distinct from
  // romp's postal service, so it gets its OWN neutral collapsed card, NOT the per-peer-colored postal card
  // and NOT a blue "you typed this" bubble. blocks = one per sending agent {id, summary?, body}.
  | { kind: "teammate"; blocks: { id: string; summary?: string; body: string }[]; ts?: string; uuid?: string }
  // Claude Code's Task to-do list, folded into one live checklist.
  | { kind: "todo"; tasks: TodoTask[]; error?: string; ts?: string; uuid?: string }
  // A CLIENT-side optimistic echo of a just-sent message is one of these (uuid OPT_PREFIX), injected at the
  // tail so it shows the instant you hit Enter and STAYS put across pushes — bridging the server-side
  // echo→landed gap where the kernel's own provisional briefly vanished (the user 2026-07-15). It rides the
  // queued idiom because that IS what it is to the reader: sent, nothing's happened yet (the user 2026-07-16).
  // `optimistic` marks a text romp has NOT confirmed the session received (→ its own tooltip, never an ✕ —
  // there's nothing confirmed to cancel); `bare` drops the "N queued messages" header, since with nothing
  // known-queued we can't claim that. Reconciled out once the kernel's payload carries the message (see
  // reconcileOptimistic). Neither field is ever sent to/from the kernel.
  // `held` DOES come from the kernel (_limit_hold): the queue is stuck on the ACCOUNT rather than on this
  // session — a usage limit or a monthly spend cap holds every send — so the head names what it is waiting
  // for, and how long is left when the API reported a reset (the user 2026-07-24).
  | { kind: "queued"; texts: { md: string; followUp?: boolean; goal?: string; fuCtx?: string; idx?: number; park?: number; cancelable?: boolean; optimistic?: boolean }[]; ts?: string; uuid?: string; bare?: boolean; held?: { reason: string; resetsAt?: number | null; what: string; detail?: string } }
  // The turn stopped on an API error (event-based: transcript isApiErrorMessage). The session is BLOCKED
  // until retried — a red-dot card at the bottom with a Retry button (the user 2026-06-16).
  | { kind: "apiError"; text: string; status?: number; ts?: string; uuid?: string }
  | { kind: "compact"; trigger?: string; preTokens?: number; postTokens?: number; summary?: string; ts?: string; uuid?: string }
  // The /clear boundary (the user 2026-07-27): this session's conversation was cleared and the fresh
  // episode starts here — a collapsed "Conversation cleared" notice card at the very top of the chat, the
  // chat twin of the timeline's film-splice seam. Its body lazy-loads the PRE-CLEAR conversation on first
  // expand (loadEpisode → chatEpisode, cached per boundary), so the cleared history stays one click away
  // instead of vanishing. `uuid` is "clear:<episode head>" — stable across pushes (the fold key).
  | { kind: "clear"; clearedAt?: number; episodes?: number; ts?: string; uuid?: string; dropped?: string[] }
  // LIVE /clear in progress (kernel-driven, event-based off the SDK backend's clearing bracket): an
  // animated "Clearing conversation…" element between the /clear delivery and the fresh transcript
  // landing — a stretch that otherwise has NO observable state and used to render as a dead gap, then
  // "No messages yet." (the user 2026-07-27). No ts → off the rail (transient).
  | { kind: "clearing"; ts?: string; uuid?: string }
  // LIVE compaction in progress (kernel-driven, event-based): an animated inline element while the session
  // compacts — sits above any queued/provisional message, and is replaced by the "compact" divider above
  // once the boundary lands and compacting clears (the user 2026-07-06). No ts → off the rail (transient).
  | { kind: "compacting"; ts?: string; uuid?: string }
  // LIVE session reconnect in progress (kernel-driven): an /effort switch reconnects the session to apply
  // (--effort is connect-time), so an animated "Reloading session…" element shows while it re-reads the
  // transcript, clearing when the new client connects (the user 2026-07-06). No ts → off the rail (transient).
  | { kind: "reconnecting"; effort?: string; ts?: string; uuid?: string }
  // LIVE api_retry in progress (kernel-driven, event-based, SDK-only): the API returned a retryable error
  // (rate-limit / overload) and the CLI is backing off + retrying, so the turn stalls. An animated "API
  // retrying…" element (the amber retrying status color) with the live attempt count; clears the instant
  // output resumes. No ts → off the rail (transient). Was visible ONLY as the amber tab border (the user
  // 2026-07-08: "the border says retrying but the chat shows no sign").
  // info (the user 2026-07-10): the latest attempt's own detail — attempt/max from the api_retry payload,
  // the error status+message behind the backoff, and the next-attempt moment (epoch s) — so the element
  // says WHAT is failing and when it retries, not just that a storm exists. Every field optional (only
  // status has been seen on the wire so far).
  | { kind: "retrying"; retries?: number; info?: { attempt?: number | null; max?: number | null; status?: number | string | null; error?: string | null; retryAt?: number | null; requestId?: string | null; networkDown?: boolean | null; rateLimitType?: string | null } | null; ts?: string; uuid?: string }
  // A stalled api_retry turn RECOVERED — a persistent, rail-anchored "Recovered after N retries" note left
  // where output resumed, the historical counterpart of the transient element above (the user 2026-07-08).
  | { kind: "retried"; retries: number; ts?: string; uuid?: string }
  // A stalled api_retry turn that did NOT recover — the CLI exhausted its attempts and settled the turn
  // with its error text (the user 2026-07-25: this used to record as "Recovered", the opposite). The red
  // durable twin of `retried`, left where the storm died; the error text itself follows as apiErrorNote.
  | { kind: "retryGaveUp"; retries: number; errorKind?: string; ts?: string; uuid?: string }
  // The DURABLE record of a turn that died on an API error: the transcript's isApiErrorMessage record,
  // rendered with the red api-error chrome instead of as an agent bubble, so a failed turn stays loudly
  // visible in history (the user 2026-07-25). No buttons — while the session is still blocked on this
  // very record, the kernel swaps it for the LIVE card below (kind "apiError"), which carries Retry.
  | { kind: "apiErrorNote"; md: string; status?: number; ts?: string; uuid?: string }
  // Durable "effort set to X" note (the user 2026-07-16): the /effort reconnect leaves no transcript atom,
  // and the synthesized /effort chip prunes on the next message — so this rail-anchored note marks WHEN the
  // new effort took effect, and stays. Kernel-interleaved by time, like `retried`. SDK-only.
  | { kind: "effortApplied"; effort: string; ts?: string; uuid?: string }
  // The model's safeguards flagged the prompt and the CLI retried the turn on a fallback model (the
  // transcript's system/model_refusal_fallback record). The reply that follows came from a DIFFERENT
  // model — conversation state that must be apparent in the chat, never silent (the user 2026-08-03).
  // from/to are raw model ids; md is the CLI's full explanation, one click away.
  | { kind: "modelFallback"; from?: string; to?: string; md?: string; ts?: string; uuid?: string }
  // Pinned, collapsed "system context" card at the top of the transcript (the user 2026-06-19): the
  // CLAUDE.md instructions in effect + session config. NOT the verbatim harness prompt — it's never
  // recorded, so it can't be shown (renderSystem says so). No ts/uuid → off the rail (no dot/hover).
  | { kind: "system"; model?: string; cwd?: string; gitBranch?: string; workTree?: { dir: string; branch: string } | null; version?: string; mode?: string;
      claudemd?: { path: string; scope: string; text: string }[]; uuid?: string; ts?: string }
) & { tlId?: string };   // tlId: the timeline atom this event's hover lights — a prompt → the DOT, work → the BAR

interface TodoTask { id: string; subject: string; activeForm?: string; status: string }

type ChipState = "working" | "ready" | "awaiting" | "awaitingBg" | "idle" | "closed" | "compacting" | "clearing" | "blocked" | "retrying" | "interrupting" | "opening";   // awaiting = a live permission/picker prompt (on YOU); awaitingBg = idle main thread waiting on background work it dispatched (straw, the user 2026-07-13)
interface Status { state: ChipState; sinceEpoch: number | null; effort?: string; model?: string; modelPending?: boolean; effortPending?: boolean; mode?: string; fast?: string; auth?: string; authPending?: boolean; authBoth?: boolean; authAcct?: string; ctx?: string; ctxColor?: number[]; modelColor?: number[]; effortColor?: number[]; faded?: boolean; backend?: string; apiTooLong?: boolean; apiSpendLimit?: boolean; apiModelLimit?: boolean; apiAuthErr?: boolean; retrySuppressed?: boolean; retryNextAt?: number | null; retryTries?: number | null; }   // retrySuppressed = the user interrupted this thread's API-error storm → romp's auto-retry stays OFF for it until a successful turn re-arms (the user 2026-07-06). backend = "tmux" | "sdk"; apiTooLong = the "blocked" is a "prompt is too long" error (on you → red tab) vs a transient API error (amber/retrying); apiSpendLimit = a monthly spend cap (on you → raise it; NEVER auto-retried — retrying can't fix it, the user 2026-07-14); apiModelLimit = this session's MODEL is out of allowance (on you → switch model or add credits; not auto-retried either, the user 2026-08-01); ctxColor = the GLOBAL colormap's RGB for the context%, computed server-side; modelColor/effortColor = the same map's RGB tint for the model name + effort (by capability/effort rank), server-computed; modelPending = a /model switch is resolving → the badge shows switching-dots until the new name lands (server-driven, event-based, the user 2026-07-03); fast = the CLI's fast-mode state ("on"/"off"/"cooldown", from the SDK init's fast_mode_state; absent = unknown/unavailable → no fast badge)
interface Color { bg: string; fg: string; }
// A run_in_background task surfaced in the #bg-tasks box (the kernel's _bg_tasks): a one-line summary +
// status, expandable to the command + its output. status = running | completed | failed.
interface BgTask { id: string; status: string; summary: string; command?: string; output?: string; }
// The box payload: count (total to surface → the "N background tasks" header) + up to 16 tasks (the list).
interface BgTasks { count: number; tasks: BgTask[]; }
// events is a contiguous TAIL of the transcript: global indices [headFrom, headTotal). On a fresh load the
// kernel ships only the last WIRE_TAIL events (headFrom > 0) to keep startup light; older history streams in
// on scroll-back (loadOlder → chatHead prepends, lowering headFrom). headFrom 0 = the whole transcript is
// resident. chatTail's `from` is GLOBAL and mapped through headFrom.
interface Session { id: string; name: string; color: Color | null; events: ChatEvent[]; status: Status; firstSeen?: number; cwd?: string; gitBranch?: string; workTree?: { dir: string; branch: string } | null; headFrom?: number; headTotal?: number; bgTasks?: BgTasks; hideFromFeed?: boolean; postalServiceOff?: boolean; notify?: boolean; }

const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;

// The romp strip (VS Code only — the host opts in via __rompShowStrip): usage
// bars, pane quick-opens, refresh, remotes, and the gear — docked below the
// composer. The chat hosts its OWN copy of the settings modal (gear.js +
// gear.css), so the gear opens right here, over the pane it was clicked in
// (the user 2026-07-13); the host still hides this strip while the feed panel
// is visible (feed wins).
if ((window as any).__rompShowStrip) {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { initGear } = require("./gear.js");
  initGear((m: Record<string, unknown>) => vscodeApi?.postMessage(m));
}
initStrip(() => window.postMessage({ romp: "openSettings" }, "*"),
  (m) => vscodeApi?.postMessage(m));
installSettingsSync();   // a gear save in ANOTHER VS Code pane lands here via the host

let settings: RompSettings = loadSettings();   // global webview settings (compact mode, …) — see settings.ts
const expandedGroups = new Set<string>();      // compact mode: tool-group keys the user clicked open

const sessions = new Map<string, Session>();
const order: string[] = [];           // positional tab order (for cycling)

// ── client-side optimistic echo (the user 2026-07-15) ── a composer send clears the box instantly, but the
// message only reappears in the chat once the kernel round-trips it back (its own provisional). Sending to a
// busy/slow thread, the kernel's provisional could briefly VANISH in the echo→landed gap — so a just-sent
// message looked lost for a beat. We drop a local optimistic bubble at the tail the moment you hit Enter and
// keep RE-injecting it on every push until the kernel's payload demonstrably carries the message, then let it
// go — the immediacy is client-owned, independent of every server-side timing subtlety.
// It rides the QUEUED idiom (the user 2026-07-16): to the reader an unconfirmed send and a queued one are the
// same state — sent, nothing's happened yet — so they wear the same dashed bubble. That also means the look
// only ever moves provisional→settled: dashed→solid when it lands, dashed→dashed (invisible) when it really
// was queued. It first shipped as a 0.6-opacity solid bubble, which invented a THIRD look and made a queued
// send flip solid→dashed — backwards, as if it had un-landed.
const OPT_PREFIX = "optimistic:";
const OPT_TTL_MS = 20_000;    // backstop: a real send always echoes within this; past it we stop asserting
const OPT_TAIL_SCAN = 30;     // the kernel's version (user atom / queued bubble) always lands at the tail
// sid → in-flight optimistic sends. `base` is how many LANDED user atoms carrying this text the tail
// already held at send time (stamped on the first reconcile, -1 until then): only a count BEYOND base
// means THIS send landed. The old retire test was a bare substring scan with no notion of which event
// carried the text, so a resend — or any short message that substrings an older bubble ("continue",
// "test") — retired its own entry in the very call that created it, and the send showed nothing at
// all (the user 2026-08-09, who watched sends vanish for a beat before appearing).
const pendingSent = new Map<string, { text: string; ts: number; base: number }[]>();
const isOptimistic = (e: ChatEvent): boolean => !!e.uuid && e.uuid.startsWith(OPT_PREFIX);

// The kernel's own queued group, if one is at the tail. Ours merges INTO it when present: the session is
// provably holding messages, so this send will queue behind them — no reason to show it as a separate lone
// bubble (the user 2026-07-16). Tail-scanned; a queued group only ever sits at the bottom.
function tailQueuedIdx(evs: ChatEvent[]): number {
  for (let i = evs.length - 1, n = 0; i >= 0 && n < 10; i--, n++) if (evs[i].kind === "queued") return i;
  return -1;
}

// Rebuild a session's optimistic tail: strip what we injected last push (kernel events are authoritative),
// then re-add one per still-in-flight send — dropping those the kernel has now surfaced (a landed user atom
// or a queued bubble carrying our text) or that have aged past the TTL backstop.
function reconcileOptimistic(s: Session): void {
  // undo our own injections. A standalone bare group is tail-appended (pop it); a kernel group we EXTENDED is
  // restored by dropping the optimistic texts off our clone — so `landed` below only ever sees kernel truth.
  while (s.events.length && isOptimistic(s.events[s.events.length - 1])) s.events.pop();
  const qi = tailQueuedIdx(s.events);
  if (qi >= 0) {
    const q = s.events[qi] as Extract<ChatEvent, { kind: "queued" }>;
    if (q.texts.some((t) => t.optimistic)) s.events[qi] = { ...q, texts: q.texts.filter((t) => !t.optimistic) };
  }
  const list = pendingSent.get(s.id);
  if (!list || !list.length) return;
  const now = Date.now();
  const tail = s.events.slice(-OPT_TAIL_SCAN);
  // A LANDED copy of the text is a real user atom — never the kernel's own provisional echo, whose
  // uuid keeps the backend's "echo:" prefix all the way into the payload. Retiring on the echo was
  // the flash-out: the echo deleted our entry for good, then blinked in its own echo→landed handoff
  // with nothing left to cover the gap (the user 2026-08-09). The header above always said "until
  // the payload DEMONSTRABLY carries the message" — an unlanded echo demonstrates nothing yet.
  const landedCount = (t: string) => tail.filter((e) =>
    e.kind === "user" && typeof e.md === "string" && e.md.includes(t)
    && !String((e as any).uuid || "").startsWith("echo:")).length;
  // The kernel's own PROVISIONAL copy — its queued bubble, or its echo atom — SUPPRESSES our bubble
  // for this push (injecting beside it would show the send twice) but never retires the entry: if
  // the provisional blinks out on the next push, ours steps straight back in.
  const shownProvisional = (t: string) => tail.some((e) =>
    (e.kind === "queued" && Array.isArray(e.texts) && e.texts.some((x) => typeof x.md === "string" && x.md.includes(t))) ||
    (e.kind === "user" && typeof e.md === "string" && String((e as any).uuid || "").startsWith("echo:") && e.md.includes(t)));
  // First reconcile after the send (registerOptimistic calls this synchronously): whatever matching
  // atoms the tail ALREADY holds are background — an older identical message, a bubble this text
  // substrings — not this send. Only growth past this count is a landing.
  for (const p of list) if (p.base < 0) p.base = landedCount(p.text);
  const keep = list.filter((p) => now - p.ts < OPT_TTL_MS && landedCount(p.text) <= p.base);
  if (keep.length) pendingSent.set(s.id, keep); else pendingSent.delete(s.id);
  const inject = keep.filter((p) => !shownProvisional(p.text));
  if (!inject.length) return;
  const mk = (p: { text: string }) => ({ md: p.text, optimistic: true, cancelable: false });
  const qj = tailQueuedIdx(s.events);
  if (qj >= 0) {
    // something IS queued here → ours queues behind it: show it in that group, under its header, counted
    const q = s.events[qj] as Extract<ChatEvent, { kind: "queued" }>;
    s.events[qj] = { ...q, texts: [...q.texts, ...inject.map(mk)] };
  } else {
    // nothing known-queued → a BARE dashed bubble: no "N queued messages" header to claim what we can't back
    s.events.push({ kind: "queued", bare: true, texts: inject.map(mk), uuid: OPT_PREFIX + inject[0].ts });
  }
}

// Record a composer send as in-flight and show its optimistic bubble NOW (before any kernel push).
function registerOptimistic(id: string, text: string): void {
  const arr = pendingSent.get(id) || [];
  arr.push({ text, ts: Date.now(), base: -1 });   // base is stamped by the reconcile just below
  pendingSent.set(id, arr);
  const s = sessions.get(id);
  if (!s) return;
  reconcileOptimistic(s);
  // The reconcile can mutate the tail IN PLACE — merging into an existing queued group, or pop+push
  // on a repeat send — which leaves s.events.length unchanged, and syncView's no-op fast path
  // (rendered === len) then skipped the repaint: the bubble waited for the next kernel push instead
  // of this keystroke (the user 2026-08-07, who saw the delay on sends into a busy session). Marking
  // the view stale takes the same window re-render a tool-group toggle uses, so the send paints NOW
  // in every case, not just the length-growing bare-bubble one.
  const v = views.get(id);
  if (v) v.stale = true;
  if (id === activeId) {
    appendActive();
    // Your OWN send always reveals itself: appendActive's stick rule keeps the viewport still when
    // you're read up >80px from the bottom, so a send made while scrolled up painted below the fold
    // and looked like it never appeared (the user 2026-08-09). Hitting Enter is the intent to see
    // the message — scroll to it, exactly once, at send time.
    const content = document.getElementById("content");
    if (content) content.scrollTop = content.scrollHeight;
  }
}

// ── conversation rewind (edit a past message, SDK sessions) ──────────────────────────────────────
// Editing a user bubble rewinds the conversation to just before it and sends the edited text as the
// branch's next turn (the kernel's rewindSend op → the SDK backend's --resume-session-at reconnect).
// The kernel's payload only reflects the branch once the rewound turn's records land (~seconds of CLI
// reconnect), so the CLICKING client overlays the outcome locally in the gap: the edited bubble wears
// the NEW text, everything after it dims as abandoned, and the backend's queued chip for the same
// text is suppressed (the overlay already shows it in place). Retired the moment the old bubble's
// uuid leaves the payload (the branch arrived) — plus a TTL backstop so a failed rewind (the kernel
// warn-toasts it) can't dim the tail forever.
const REWIND_TTL_MS = 30_000;
// `bare` marks a DELETE rollback (rewindDelete): no replacement text — the deleted bubble itself dims
// with the tail, and the kernel's pending-cut payload (not a landed turn) is what retires the overlay.
const pendingRewind = new Map<string, { uuid: string; text: string; ts: number; bare?: boolean }>();

// Re-apply (or retire) the pending-rewind overlay on a fresh payload, and recompute which user
// bubbles are EDITABLE: genuine human messages with a transcript uuid, AFTER the last compaction —
// the CLI only addresses post-boundary records, so older bubbles get no edit affordance (the kernel
// re-validates regardless). Runs beside reconcileOptimistic on every ingest path; the rewound flags
// are stripped first because a chatTail delta REUSES prefix event objects across pushes.
function reconcileRewind(s: Session): void {
  for (const e of s.events) if ((e as any).rewound) delete (e as any).rewound;
  let lastCompact = -1;
  for (let i = 0; i < s.events.length; i++) if (s.events[i].kind === "compact") lastCompact = i;
  const editable = new Set<string>();
  if (s.status?.backend === "sdk") {
    for (let i = lastCompact + 1; i < s.events.length; i++) {
      const e = s.events[i] as any;
      if (e.kind === "user" && e.human && e.uuid && !e.romp && !e.interruptMarker
          && !e.uuid.startsWith(OPT_PREFIX)) editable.add(e.uuid);
    }
  }
  (s as any)._editable = editable;
  const pr = pendingRewind.get(s.id);
  if (!pr) return;
  const idx = s.events.findIndex((e) => e.kind === "user" && (e as any).uuid === pr.uuid);
  if (idx < 0 || Date.now() - pr.ts > REWIND_TTL_MS) {
    pendingRewind.delete(s.id);          // the branch landed (old uuid gone) — or the backstop expired
    return;
  }
  if (pr.bare) {
    // DELETE rollback: the deleted bubble goes too — dim from it onward. No text replacement and no
    // queued-chip suppression (nothing was sent; the kernel's cut payload retires this in a beat).
    for (let j = idx; j < s.events.length; j++) (s.events[j] as any).rewound = true;
  } else {
    s.events[idx] = { ...(s.events[idx] as any), md: pr.text, pending: true, images: undefined };
    for (let j = idx + 1; j < s.events.length; j++) (s.events[j] as any).rewound = true;
    for (let j = s.events.length - 1; j > idx; j--) {
      const e = s.events[j] as any;
      if (e.kind === "queued" && Array.isArray(e.texts)) {
        e.texts = e.texts.filter((t: any) => t.md !== pr.text);
        if (!e.texts.length) s.events.splice(j, 1);
      }
    }
  }
  const v = views.get(s.id);
  if (v) v.stale = true;                 // the overlay touches MID-window turns — the append fast path won't repaint them
}
// Tab name+color from the kernel's tabOrder push (the user 2026-06-26): lets renderTabs paint the WHOLE
// strip as placeholders BEFORE each session's build_session arrives, so tabs don't pop in one-by-one.
const tabMeta = new Map<string, { name: string; color: Color | null }>();
// Tabs the user has just ✕'d, suppressed until the kernel's own tab set agrees. Declared up here beside
// tabMeta because renderTabs reads it, and renderTabs can run before the module finishes evaluating.
// The close was ALREADY optimistic (dismissSession runs on click) but nothing recorded that locally — so the
// next push, built before the kernel had processed the close, re-listed the id, and with its session already
// dropped here the strip repainted it as a LOADING PLACEHOLDER: the closed tab faded back in wearing the romp
// swirl, looking like a shutdown you had to wait out (the user 2026-07-24, who wanted the tab to just go and
// the shutdown to run behind it). Cleared on the kernel's ack — its push dropping the id — not on a timer.
const closingTabs = new Map<string, number>();
// …with a backstop for the ack that never comes. A refused/failed end leaves no failure EVENT to key on:
// the sole evidence a close didn't take is the kernel still listing the tab long after. Past this we say
// so and let the tab back, rather than hiding a session that's really still open.
const CLOSE_ACK_MS = 15_000;
// The romp identity palette for the tab right-click color picker (the user 2026-06-29). Fetched once from the
// kernel's /palette so the client holds no color literals; empty until it lands (the menu just omits the row).
// The palette is SELECTABLE now (the user 2026-07-12): a {type:"palette"} push lands the new set on switch.
let paletteColors: string[] = [];
fetch(kernelUrl("/palette"), { cache: "no-store" }).then((r) => r.json())
  .then((d) => { if (Array.isArray(d.colors)) paletteColors = d.colors; }).catch(() => { /* menu omits the swatch row */ });
const mru: string[] = [];             // recency stack, front = most-recently-active (close → return to previous)
let activeId: string | null = null;
let renderingSid: string | null = null;   // the session id syncView is currently building (for per-session fold keys)
// restore the last-active tab on refresh (persisted via setState); one-shot, applied when its session arrives
let wantActive: string | null = (() => { try { return ((vscodeApi?.getState?.() || {}) as any).activeId || null; } catch { return null; } })();
let pendingAnchor: string | null = null; // deep-link target waiting to be scrolled to
let pendingAnchorIntent: string | null = null; // kind the uuid anchor must honor — sticks with pendingAnchor across render-pass retries (pendingAnchorKind is cleared each pass, this isn't)
let pendingAnchorT: number | null = null; // time fallback (epoch s) when the uuid can't resolve
let pendingAnchorKind: string | null = null; // intent for the time fallback: "user" = land on the user's own turn
let anchorPendingOlder = false; // scrollToAnchor kicked off a loadOlder fetch for an anchor past the resident tail → don't toast "couldn't locate"; chatHead re-lands when the chunk arrives (the user 2026-06-27)
// KEEP-OFFSET landing (the user 2026-08-02). A scroll-back loadOlder re-anchors on the row the reader was
// on — that is POSITION PRESERVATION, not a deep-link: the row must come back at the SAME on-screen offset,
// with no top-align and no flash. Non-null ⇒ resolve pendingAnchor by id as usual (which renders the window
// around it), then restore it to this y instead of calling landOn. Sticks with pendingAnchor across
// render-pass retries, like pendingAnchorIntent.
let pendingAnchorKeepY: number | null = null;
// Landing diagnostics (the user's ask, 2026-06-10): record HOW each deep-link
// landing resolved — exact pointer / refused wrong-kind pointer / time-nearby
// / gave up. The trail is posted to the host (→ ~/.local/state/romp/
// locate-diag.jsonl) on every attempt, and DEGRADED landings show a transient
// toast, so a bad jump is visibly flagged instead of looking like a confident
// (but wrong) link. "That click landed weird" + the log = a diagnosable bug.
let landTrail: string[] = [];

// Per-session rendered DOM, kept alive so switching tabs doesn't rebuild the
// whole transcript — only the active view is shown, others are display:none.
// TAIL-WINDOWED: a long transcript renders thousands of .turn nodes, and the
// browser laying out + hit-testing that whole tree is what made focusing a big
// chat lag ~½s (the user 2026-06-25). So only the TAIL [winStart, len) renders as
// real .turn nodes; the older head [0, winStart) collapses into ONE measured
// `.tx-spacer` div at the top. Scrolling near the top lazily expands the window
// downward (renders older turns, shrinks the spacer) — the classic chat-app
// "load more as you scroll back". The spacer carries a REAL height (winStart ×
// avgTurnH), so content.scrollHeight stays honest and stick-to-bottom never snaps
// (the failure mode of the reverted content-visibility approach). winStart === 0
// ⇒ no spacer, whole transcript rendered (short sessions, compact mode).
// Invariant: view.rendered === len (every event accounted for). Note the DOM child
// count is NOT len − winStart + spacer: a unit may own more than one node (the day
// divider that opens a new day precedes its turn), so anything mapping DOM back to
// units reads data-unit off the node rather than counting children.
interface View { el: HTMLElement; rendered: number; scrollTop: number; stick: boolean; shown: boolean; stale: boolean; winStart: number; winEnd?: number; avgTurnH?: number; spacerCount?: number; spacerCountBot?: number; unitTotal?: number; }
const views = new Map<string, View>();

// Pending pickers (AskUserQuestion / tool-permission) keyed by session id. These
// live ONLY in the session's tmux pane (Claude Code doesn't write a pending
// prompt to the transcript until it's answered), so the host captures+parses the
// pane and pushes them here. Kept OUT of the transcript `events` list so syncView
// never clobbers them; rendered into the dedicated #live-ask region instead.
// The pending prompt per session (its `kind` selects the widget). Kept OUT of the
// transcript events so syncView can't clobber it. A stored null = awaiting an
// unstructured screen (e.g. the free-text "type something" field) → a text input;
// no entry at all = not awaiting → hidden.
const liveAsks = new Map<string, ParsedAsk | null>();
// When each session's live picker ARRIVED, and when the composer's current draft was STARTED (empty →
// non-empty). A draft you were already writing when the question appeared CANNOT be an answer to a question
// you hadn't seen yet, so Enter sends it as a normal MESSAGE — which a picker-blocked session necessarily
// queues behind the answer — instead of silently becoming the answer (the user 2026-07-16: a queued message
// got eaten as the reply to an AskUserQuestion). Two real event stamps compared, never a time heuristic.
// Either stamp missing → we don't know it predates, so the picker keeps the box (the old behaviour).
const askArrivedAt = new Map<string, number>();
const draftStartedAt = new Map<string, number>();

// Per-session rolling digest (headline + goal tree + Recent), feeding the tab tooltip and the
// Fleet view. (The in-chat #ledger box and its bullets list are retired — 2026-07-07 payload audit.)
// A node of the goal-graph overview tree: open paths are expanded, done nodes are pruned to leaves.
// `current` = the focus node being worked on (gets a pointer + the live elapsed); a `done` node shows
// its completion time, recency-coloured, on the right (the user 2026-06-16).
// `derived` = this node is done only because all its children are (the kernel propagates completion up
// the tree), as opposed to an explicitly-asserted done. Rendered as the blue ✓ disc dimmed (the user
// 2026-06-16). Empty/false → explicit done (full disc).
interface LedgerTreeNode { id: string; text: string; depth: number; done: boolean; blocked: boolean; t?: number; mt?: number; current: boolean; derived?: boolean; cleared?: boolean; onpath?: boolean; promptAnchorUuid?: string | null; anchorUuid?: string | null; children?: string[]; summary?: string | null; blockSummary?: string | null; _rec?: number; }   // summary/blockSummary = the distiller's takeaway / decision brief, revealed by the row's ⊕ expander; _rec = render-stamped subtree-rolled-up recency
interface LedgerRecent { text: string; t: number; }   // tab-hover "Recent": up-to-5 most-recent TOP tasks across live + archive, any status (the user 2026-06-30)
interface Ledger { summary: string; tree?: LedgerTreeNode[]; current?: { t?: number } | null; recent?: LedgerRecent[]; }
const ledgers = new Map<string, Ledger | null>();

function el(tag: string, cls?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function md(src: string): string {
  // Transcript text (user prompts, assistant output, subagent reports, postal
  // bodies) is UNTRUSTED and `marked` emits raw HTML verbatim, so its output
  // must be sanitized before it ever reaches .innerHTML — otherwise a payload
  // like `<img src=x onerror=...>` or `[x](javascript:...)` runs in the webview
  // (which can postMessage the host to open files / drive sessions). DOMPurify
  // strips event-handler attributes and dangerous URL schemes. Keep data: URIs
  // on <img> (the CSP allows them and inline transcript images rely on them).
  try {
    const dirty = marked.parse(src) as string;
    return DOMPurify.sanitize(dirty, { USE_PROFILES: { html: true }, ADD_DATA_URI_TAGS: ["img"] });
  } catch { const d = document.createElement("div"); d.textContent = src; return d.innerHTML; }
}

function highlight(container: HTMLElement, lineNos = true) {
  container.querySelectorAll("pre code").forEach((node) => {
    const code = node as HTMLElement;
    const raw = code.textContent || "";   // capture BEFORE we rewrite innerHTML: line-wrapping drops the \n joins, so the on-screen markup's textContent is NOT copy-safe
    const lang = (code.className.match(/language-([\w-]+)/) || [])[1];
    try {
      code.innerHTML = lang && hljs.getLanguage(lang)
        ? hljs.highlight(raw, { language: lang }).value
        : hljs.highlightAuto(raw).value;
      code.classList.add("hljs");
      if (lineNos) wrapCodeLines(code);   // per-line gutter so a soft-wrap reads distinctly from a real newline
    } catch { /* leave as-is */ }
    const pre = code.parentElement;
    if (pre && pre.tagName === "PRE") addCopyBtn(pre as HTMLElement, raw);   // an automatic "Copy" button per block
  });
}

// Copy text to the clipboard, falling back to a hidden-textarea execCommand when the async Clipboard API
// is unavailable (it needs a secure context — localhost counts, but stay safe). Returns whether it copied.
function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
  }
  return Promise.resolve(fallbackCopy(text));
}
function fallbackCopy(text: string): boolean {
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.top = "-9999px"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

// An automatic "Copy" button parked top-right of every rendered code block (the user 2026-06-22). The RAW
// source is captured at highlight time and closed over — the on-screen markup adds a line-number gutter and
// drops the newline joins, so copying its textContent would be wrong. Faint until the block is hovered;
// flips to a green "Copied" for ~1.2s on success. Idempotent (highlight can re-run on a re-render).
function addCopyBtn(pre: HTMLElement, raw: string) {
  if (pre.querySelector(":scope > .code-copy")) return;
  pre.classList.add("has-copy");
  const btn = el("button", "code-copy") as HTMLButtonElement;
  btn.type = "button"; btn.textContent = "Copy"; btn.title = "copy this code block";
  btn.addEventListener("click", (ev) => {
    ev.preventDefault(); ev.stopPropagation();
    copyText(raw).then((ok) => {
      btn.textContent = ok ? "Copied" : "Copy failed";
      btn.classList.toggle("copied", ok);
      window.setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1200);
    });
  });
  pre.appendChild(btn);
}

// Wrap each logical line of (hljs-highlighted) code in <span class=cl><span class=ct>…</span></span>,
// re-opening any hljs span that straddles a newline so the markup stays valid. A CSS counter on .cl
// draws the subtle line numbers; .ct holds the wrapping content (the user 2026-06-16).
function wrapCodeLines(code: HTMLElement) {
  const lines = code.innerHTML.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();   // a trailing newline isn't a blank line
  let open: string[] = [];
  code.innerHTML = lines.map((ln) => {
    const prefix = open.join("");
    const re = /<span[^>]*>|<\/span>/g; let m; const stack = open.slice();
    while ((m = re.exec(ln))) { if (m[0] === "</span>") stack.pop(); else stack.push(m[0]); }
    const suffix = "</span>".repeat(Math.max(0, stack.length));
    open = stack;
    return `<span class="cl"><span class="ct">${prefix}${ln}${suffix}</span></span>`;
  }).join("");
}

function dot(kind: "green" | "ring" | "user" | "red" | "romp" | "working"): HTMLElement { return el("span", "dot " + kind); }

function ioRow(label: "IN" | "OUT", text: string, isError: boolean): HTMLElement {
  const row = el("div", "io-row" + (label === "OUT" ? " io-out" : "") + (isError ? " io-error" : ""));
  const lab = el("span", "io-label"); lab.textContent = label;
  const pre = el("pre", "io-pre"); pre.textContent = text;
  row.appendChild(lab); row.appendChild(pre);
  return row;
}

// Tools whose result is pure boilerplate ("…updated successfully", "Task #N
// created") — on success the OUT box is suppressed (the green ✓ rail dot is the
// success signal). On error the real message is always shown. (The Agent/Task
// subagent tool is NOT here — its output is the agent's report, which is signal.)
const ACK_TOOLS = new Set([
  "Edit", "Write", "MultiEdit", "NotebookEdit",
  "TodoWrite", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
]);

function shortPath(p: string): string {
  const parts = p.split("/").filter(Boolean);
  return parts.length <= 2 ? p : ".../" + parts.slice(-2).join("/");
}

function countLines(s: string): number {
  if (!s) return 0;
  const n = s.split("\n").length;
  return s.endsWith("\n") ? n - 1 : n;
}

function preEl(text: string): HTMLElement {
  const pre = el("pre", "io-pre fold-pre");
  pre.textContent = text;
  return pre;
}

// Links in the chat (markdown [x](url) and GFM-autolinked bare URLs alike, all rendered as <a href>
// by md()) must actually follow on click. Two hosts, two paths:
//   • Web dashboard (http(s): origin): open it in the user's OWN browser, on the device they're viewing
//     from — a normal window.open in the click gesture (not popup-blocked). This is the common case and
//     it used to silently die: the old code only ever postMessage'd the host, and the kernel has no
//     openLink handler, so a link click did nothing on the web dashboard (the user 2026-06-25).
//   • VS Code webview (vscode-webview: origin): the sandbox blocks window.open, so route the anchor to
//     the host extension, which openExternal()s normal URLs and feeds vscode://romp.romp-chat-view deep
//     links into its own URI handler.
// DOMPurify already stripped dangerous schemes (javascript:, etc.) in md(), so a surviving href is safe.
document.addEventListener("click", (e) => {
  const a = (e.target as HTMLElement)?.closest?.("a[href]") as HTMLAnchorElement | null;
  if (!a) return;
  const href = a.getAttribute("href") || "";
  if (!/^[a-z][a-z0-9+.-]*:/i.test(href)) return; // fragment/relative — leave alone
  e.preventDefault();
  e.stopPropagation();
  if (location.protocol === "http:" || location.protocol === "https:") {
    window.open(href, "_blank", "noopener,noreferrer"); // web dashboard → open in the viewer's browser
  } else if (vscodeApi) {
    vscodeApi.postMessage({ type: "openLink", href });  // VS Code webview → host openExternal
  }
}, true);

// A clickable file name that opens the real file in the editor (shared
// open/navigate surface — see extension.ts openFile handler).
function fileLink(path: string): HTMLElement {
  const a = el("span", "tool-file");
  a.textContent = shortPath(path);
  a.title = "Open " + path;
  a.addEventListener("click", (e) => {
    e.stopPropagation();
    if (vscodeApi) vscodeApi.postMessage({ type: "openFile", path });
  });
  return a;
}


// Compact fold: the AFFORDANCE (caret + a summary like "12 lines" / "+5 −2") sits
// on the RIGHT of the tool's HEAD line; the expandable content hangs below the
// head, hidden until clicked — so each tool stays ONE row by default (the user:
// vertical-compact). `head` must already be appended to `turn`.
function inlineFold(head: HTMLElement, turn: HTMLElement, label: string, content: HTMLElement, key?: string) {
  const toggle = el("span", "tool-fold-toggle");
  toggle.textContent = label;   // just the clickable summary ("+14 −0" / "12 lines") — no caret/bullet
  toggle.title = "click to expand";
  applyFold(turn, "fold-open", key);
  toggle.addEventListener("click", (e) => { e.stopPropagation(); rememberFold(turn, "fold-open", key); });
  content.classList.add("tool-fold-body");
  head.appendChild(toggle);
  turn.appendChild(content);
}

// Expand/collapse state must SURVIVE the incremental re-render that every send/turn triggers (the user
// 2026-06-19): a short transcript rebuilds from index 0, a long one re-renders the trailing TAIL_RECHECK
// turns — either way a DOM-only `.open` silently resets whatever the user had opened (e.g. they expand
// the system-context card, type a message, hit ⏎, and it snaps shut). So we persist open-state in a Set
// keyed by a stable id (the turn uuid, or the session id for the pinned system card) and reapply it on
// rebuild — the same trick `expandedGroups` uses for collapsed tool runs. A keyless fold (no stable id)
// is just transient, exactly as before. Same-uuid sibling folds share a key → they expand together on
// rebuild; benign (shows more, never less) and rare.
const openFolds = new Set<string>();
function applyFold(target: HTMLElement, cls: string, key?: string): void {
  if (key && openFolds.has(key)) target.classList.add(cls);
}
function rememberFold(target: HTMLElement, cls: string, key?: string): void {
  const open = target.classList.toggle(cls);
  if (key) { if (open) openFolds.add(key); else openFolds.delete(key); }
}

// Hidden-until-clicked disclosure (caret + label) — for noise-by-default content like Read dumps and
// folded system reminders. Pass a stable `key` to make the open/closed state survive re-renders.
function foldable(label: string, content: HTMLElement, key?: string): HTMLElement {
  const wrap = el("div", "fold");
  const head = el("div", "fold-head");
  const caret = el("span", "fold-caret"); caret.textContent = "▸";
  const lab = el("span", "fold-label"); lab.textContent = label;
  head.appendChild(caret); head.appendChild(lab);
  applyFold(wrap, "open", key);
  head.addEventListener("click", () => rememberFold(wrap, "open", key));
  wrap.appendChild(head);
  wrap.appendChild(content);
  return wrap;
}

// THE shared "notice card" — informational transcript notices (a backgrounded agent's report, a romp
// system notice, folded system-reminders) each get their OWN boxed card with a type CHIP, a one-line gist
// head, and a keyed collapse→expand body. One family, inspired by the postal/teammate cards but distinct:
// each `variant` carries its own color (agent = accent blue, romp = the swirl + faded-accent, reminder =
// muted). Distinct from postal (per-peer color) and teammate (dashed neutral). Collapse is KEYED (survives
// the chat's re-renders via openFolds), unlike the postal/teammate hand-rolled toggle (the user 2026-07-06).
// `nested` (the user 2026-07-06): the AGENT/REMINDER notices are appended INSIDE the parent user turn that
// carried the <task-notification>/reminder, so they must NOT bring their own .turn (rail + dot) — a turn
// nested in a turn drew a SECOND rail + dot, indented another 24px, so the card floated off to the right
// detached from the timeline. Nested → return the bare card; it sits in the parent turn's rail column under
// its single dot (connected, like any in-turn card). A standalone notice (romp system) IS its own top-level
// turn, so it keeps the .turn wrapper + dot.
function noticeCard(o: { variant: "agent" | "romp" | "reminder" | "compact" | "clear"; chip: string; logo?: boolean;
                        head: string; body: HTMLElement; collapsible?: boolean; key?: string;
                        nested?: boolean }): HTMLElement {
  const card = el("div", "notice-card notice-card-" + o.variant + (o.nested ? " notice-nested" : ""));
  const collapsible = o.collapsible !== false;

  const headEl = el("div", "notice-head");
  const caret = collapsible ? el("span", "notice-caret") : null;
  if (caret) headEl.appendChild(caret);
  const chip = el("span", "notice-chip notice-chip-" + o.variant);
  if (o.logo) {   // the romp swirl marks a romp notice as "from romp", the way the postal card does
    const logo = el("img", "notice-chip-logo") as HTMLImageElement;
    logo.src = mediaSrc("romp-swirl-glyph.svg"); logo.alt = ""; logo.onerror = () => logo.remove();
    chip.appendChild(logo);
  }
  chip.appendChild(document.createTextNode(o.chip));
  headEl.appendChild(chip);
  if (o.head) { const h = el("span", "notice-head-text"); h.textContent = o.head; headEl.appendChild(h); }
  card.appendChild(headEl);

  const hasBody = o.body.childNodes.length > 0;   // gist-only notice → skip the wrapper, render flat
  if (hasBody) { const bodyEl = el("div", "notice-body"); bodyEl.appendChild(o.body); card.appendChild(bodyEl); }

  if (collapsible && hasBody) {
    card.classList.add("notice-collapsible");
    applyFold(card, "notice-open", o.key);                 // keyed: an expanded card stays open across pushes
    if (caret) caret.textContent = card.classList.contains("notice-open") ? "▾" : "▸";
    headEl.addEventListener("click", () => {
      rememberFold(card, "notice-open", o.key);
      if (caret) caret.textContent = card.classList.contains("notice-open") ? "▾" : "▸";
    });
  }
  if (o.nested) return card;                               // sits inside the parent turn — no own rail/dot
  const turn = el("div", "turn turn-notice notice-" + o.variant);
  const d = dot("ring"); d.classList.add("notice-dot", "notice-dot-" + o.variant);
  turn.appendChild(d);
  turn.appendChild(card);
  return turn;
}

// A backgrounded task's completion (<task-notification>) → an accent-blue notice card. The HEAD is a
// glanceable gist — the task's own name and a compact status ("desc · exit 0", "agent name · completed") —
// NOT the whole summary sentence. The collapsible BODY is the real detail, one click away (the user
// 2026-07-23, who saw a card that printed the same summary twice and couldn't open it for more):
//   - an AGENT: its final message (markdown);
//   - a background COMMAND: the shell command it ran + its output tail, joined in by the kernel via
//     tool-use-id (ev.taskOutputs — the client can't read the output file itself).
// When there is genuinely nothing more than the gist, the card renders FLAT (no caret, no repeated body) —
// honest, and no dead-end. The pure parse lives in agent-notif.ts (testable); this owns the DOM.
function renderAgentNotif(a: AgentNotif, outputs?: TaskOutputs, key?: string): HTMLElement {
  const chip = a.kind === "agent" ? "agent" : "task";       // a Bash command is a task, not an "agent"
  const head = a.detail ? `${a.label} · ${a.detail}` : a.label;
  const body = el("div", "notice-md md");
  const extra = a.toolUseId && outputs ? outputs[a.toolUseId] : undefined;
  let hasBody = false;
  if (a.result) {                                           // an agent's final message
    body.innerHTML = md(a.result); highlight(body); hasBody = true;
  } else if (extra && (extra.command || extra.output)) {    // a command's shell + output tail
    if (extra.command) { const lbl = el("div", "notice-sub"); lbl.textContent = "command"; body.appendChild(lbl); body.appendChild(preEl(extra.command)); }
    if (extra.output) { const lbl = el("div", "notice-sub"); lbl.textContent = "output"; body.appendChild(lbl); body.appendChild(preEl(extra.output)); }
    hasBody = true;
  }
  return noticeCard({ variant: "agent", chip, head, body, key,
                      collapsible: hasBody, nested: true });   // flat when the gist is all there is; rendered inside the carrying user turn
}

// ---- path-source pasted images ----
// A user turn may carry a "path:<abs path>" image (Claude Code's image-cache or a
// screenshot from disk). The webview can't read files, so we ask the host once per
// path; until/unless it returns a dataURL we show a "🖼 filename" chip, then swap in
// the real thumbnail when the host answers. Re-renders rebuild the element from these
// caches, so a thumbnail already fetched stays a thumbnail.
const imgUrlCache = new Map<string, string>();   // path → dataURL (loaded)
const imgFailed = new Set<string>();             // path → keep the chip, never retry
const imgRequested = new Set<string>();          // path → request in flight
function fillPathImg(wrap: HTMLElement, p: string): void {
  wrap.textContent = "";
  const url = imgUrlCache.get(p);
  if (url) {
    const img = document.createElement("img"); img.className = "user-img"; img.src = url; img.loading = "lazy"; img.title = p;
    wrap.appendChild(img);
  } else {
    const chip = el("div", "user-img-path"); chip.textContent = "🖼 " + (p.split("/").pop() || p); chip.title = p;
    wrap.appendChild(chip);
  }
}
function buildPathImg(p: string): HTMLElement {
  const wrap = el("span", "js-pathimg"); wrap.dataset.imgpath = p;
  fillPathImg(wrap, p);
  if (!imgUrlCache.has(p) && !imgFailed.has(p) && !imgRequested.has(p)) {
    imgRequested.add(p);
    // id → the kernel resolves a RELATIVE path against this session's cwd (assistant-mentioned
    // "plots/out.png" renders too, not just absolute user-attachment paths — the user 2026-07-20)
    if (vscodeApi) vscodeApi.postMessage({ type: "imgRequest", path: p, id: activeId });
  }
  return wrap;
}
function onImgData(p: string, url: string | null): void {
  imgRequested.delete(p);
  if (url) imgUrlCache.set(p, url); else imgFailed.add(p);
  document.querySelectorAll(".js-pathimg").forEach((n) => {
    const e = n as HTMLElement; if (e.dataset.imgpath === p) fillPathImg(e, p);
  });
}

// One image of a user turn: the picture (or its hydration chip) plus, when the
// on-disk path is known, a caption line — the full absolute path (click → open),
// ⧉ copies it. So both the rendered image AND its path stay accessible no matter
// how the image arrived (pasted inline, referenced by path, typed as text).
// pathInText: the path is ALREADY visible (linkified) in the message text — a
// dropped/pasted screenshot inserts it there — so a caption would just repeat it
// (the user 2026-07-15). Skip the caption then; the in-text link already opens it.
function userImage(im: { src: string; path?: string }, pathInText = false): HTMLElement {
  const fig = el("span", "user-img-wrap");
  if (im.src.startsWith("path:")) {
    fig.appendChild(buildPathImg(im.src.slice(5)));   // host reads it → real thumbnail; chip until then / on failure
  } else {
    const img = document.createElement("img"); img.className = "user-img"; img.src = im.src; img.loading = "lazy";
    fig.appendChild(img);
  }
  if (im.path && !pathInText) fig.appendChild(imgCaption(im.path));   // caption only when the path isn't already in the text
  return fig;
}
function imgCaption(path: string): HTMLElement {
  const cap = el("span", "img-caption");
  const icon = el("span", "img-icon");   // separate node, so selecting the path text doesn't grab the emoji
  icon.textContent = "🖼";
  cap.appendChild(icon);
  cap.appendChild(imgPathLink(path));
  const copy = el("span", "img-copy");
  copy.textContent = "⧉";
  copy.title = "Copy path: " + path;
  copy.addEventListener("click", (e) => {
    e.stopPropagation();
    navigator.clipboard?.writeText(path).then(() => {
      copy.textContent = "✓";
      setTimeout(() => { copy.textContent = "⧉"; }, 900);
    });
  });
  cap.appendChild(copy);
  return cap;
}
// The full absolute path, clickable — opens the image file in the editor. Shown
// verbatim (never shortened to a basename) so it can also be read and selected/
// copied right where it stands.
function imgPathLink(path: string): HTMLElement {
  const a = el("span", "img-link");
  a.textContent = path;
  a.title = "Open " + path;
  a.addEventListener("click", (e) => {
    e.stopPropagation();
    if (vscodeApi) vscodeApi.postMessage({ type: "openFile", path });
  });
  return a;
}
// Make literal occurrences of the images' paths inside the rendered message text
// clickable with the same open-the-file link the captions use — the typed path
// stays visible verbatim, it just gains the link behavior.
function linkifyImgPaths(root: HTMLElement, paths: string[]): void {
  if (!paths.length) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let n: Node | null;
  while ((n = walker.nextNode())) nodes.push(n as Text);
  for (const tn of nodes) {
    for (const p of paths) {
      const i = tn.data.indexOf(p);
      if (i < 0) continue;
      tn.splitText(i + p.length);
      const mid = tn.splitText(i);
      mid.replaceWith(imgPathLink(p));
      break;   // the split invalidated this node's tail — one link per original node
    }
  }
}

// A file:// URI → its local filesystem path: strip the scheme, percent-decode. file:///a/b → /a/b.
function fileUriToPath(uri: string): string {
  let p = uri.replace(/^file:\/\//i, "");   // file:///Users/… → /Users/… (host is empty for file:///)
  try { p = decodeURIComponent(p); } catch { /* malformed %-escape — use verbatim */ }
  return p;
}
// A clickable, VERBATIM file link that opens in the host's default app — the SAME open-the-file path the
// caption/image links use ({type:"openFile"} → the kernel runs `open <path>`, so e.g. a PDF opens in the
// viewer). `raw` is shown as written; `open` is what the host opens. A bare file:// can't be followed by the
// browser from the http dashboard (blocked scheme) and a VS Code editor won't render a PDF, so it's routed to
// the host opener instead of navigated. `relative` bare paths carry the active session id so the KERNEL
// resolves them against THAT session's cwd — a relative `design/foo.md` is relative to the repo the agent
// runs in, not the kernel's cwd (the user 2026-07-06).
function openPathLink(raw: string, open: string, relative = false): HTMLElement {
  const a = el("span", "file-uri-link");
  a.textContent = raw;                       // shown exactly as written, selectable/copyable in place
  a.title = "Open " + open;
  a.addEventListener("click", (e) => {
    e.stopPropagation();
    if (vscodeApi) vscodeApi.postMessage(relative
      ? { type: "openFile", path: open, id: activeId }   // kernel resolves against this session's cwd
      : { type: "openFile", path: open });
  });
  return a;
}
function fileUriLink(uri: string): HTMLElement { return openPathLink(uri, fileUriToPath(uri)); }
// Is this bare token (trailing punctuation already stripped) a file path worth linkifying? Requires a slash
// and EITHER an absolute/anchored start (/, ~/, ./, ../) OR a file extension on the final segment — so
// "and/or", "TCP/IP", "24/7", "read/write" stay as prose. URL-ish tokens (a ':' or '//') are rejected;
// http(s) links are already <a> (skipped) — this just guards a rare un-autolinked one.
function looksLikeFilePath(tok: string): boolean {
  if (tok.includes(":") || tok.includes("//") || !tok.includes("/")) return false;
  if (/^(?:~\/|\.{1,2}\/|\/)/.test(tok)) return true;                        // absolute or anchored (/, ~/, ./, ../)
  return /\.[A-Za-z0-9]{1,8}$/.test(tok.slice(tok.lastIndexOf("/") + 1));    // relative → the last segment has an extension
}
// A BARE filename (no slash — `power2_watts.pdf`) is linkified ONLY inside inline <code> (the user
// 2026-07-17: a reply listing its output files wasn't clickable). Backticks are where agents put
// filenames, and the KNOWN-extension gate keeps backticked dotted identifiers (`np.array`, `s.color`,
// `romp.kernelPort`) and version numbers (`0.4.293`) reading as prose — an unknown extension stays text.
const BARE_FILE_EXTS = new Set([
  "md", "txt", "rst", "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "jsonl", "csv", "tsv",
  "pdf", "png", "jpg", "jpeg", "gif", "svg", "webp", "html", "htm", "css", "scss", "sh", "bash", "zsh",
  "bats", "yaml", "yml", "toml", "ini", "cfg", "conf", "xml", "ipynb", "rs", "go", "java", "c", "h",
  "cpp", "hpp", "cc", "rb", "php", "sql", "log", "lock", "tex", "bib", "zip", "tar", "gz", "tgz",
  "mp4", "mov", "mp3", "wav", "vsix", "plist", "diff", "patch",
]);
function looksLikeBareFileName(tok: string): boolean {
  if (tok.includes("/") || tok.includes(":")) return false;
  const dot = tok.lastIndexOf(".");
  if (dot <= 0) return false;                                                // needs a name before the extension
  return BARE_FILE_EXTS.has(tok.slice(dot + 1).toLowerCase());
}
// Make bare file:// URLs AND bare file paths inside a rendered CHAT message clickable (assistant replies +
// your own bubbles) — a relative `design/foo.md` opens too, resolved against the session's cwd (the user
// 2026-07-06). marked doesn't autolink these and DOMPurify strips the file: scheme, so without this they read
// as dead text. Deliberately NOT applied to tool-use summaries. Linkifies inside INLINE <code> too — agents
// routinely wrap a path in backticks; only FENCED <pre> blocks and text already inside a link are skipped.
// Trailing sentence punctuation is left out, not swallowed.
const CLICKABLE_PATH_RE = /file:\/\/\/?[^\s<>"'`)]+|[~.\w\-]*\/[~.\w\-/]*[\w\-]|[\w\-][\w\-.]*\.[A-Za-z0-9]{1,8}/gi;
// `skipThumbs`: paths this turn ALREADY renders as full in-bubble images (a pasted screenshot's
// ev.images) — they stay clickable links but are excluded from the mentioned-path thumbnail strip,
// otherwise the same picture renders twice (the user 2026-07-10).
// `spacePaths` (the user 2026-08-04): backticked filenames WITH SPACES that the KERNEL verified exist
// (build_session's _space_paths — resolved like a click, existence-checked). The token regex below can
// never span a space — in prose that boundary is what keeps ordinary text unlinked — so a note titled
// `Moving from correlation to causal components.md` linkified only its last word. For exactly these
// verified spans, the whole inline-code content becomes ONE link; the filesystem is the authority, so a
// backticked command like `uv run pytest tests/x.py` (no such file) is never mislinked.
// `pathLinks`: the kernel's verdict on every path-shaped token in this message (build_session's
// _path_links — tier 1 exact stat, tiers 2/3 a unique repo-list match that FIXES a shortened mention
// to its real file). When the key is present, a token links ONLY if it's in the map, and it opens the
// map's value — so `render.js` in prose stops 404ing, and hover shows the real target. Every shape
// gate below still applies; the map only ever narrows. An event with NO pathLinks key at all (an old
// kernel, a cached payload) keeps today's shape-only linking rather than unlinking history.
// file:// URIs are explicit absolute paths — never gated on the map.
function linkifyFileUris(root: HTMLElement, skipThumbs?: string[], spacePaths?: string[],
                         pathLinks?: Record<string, string>): void {
  const previewable: string[] = [];   // renderable paths found in this message → a thumbnail strip below it
  if (spacePaths && spacePaths.length) {
    const verified = new Set(spacePaths);
    for (const code of Array.from(root.querySelectorAll("code"))) {
      if (code.closest("a, .file-uri-link, pre")) continue;    // already linked, or a fenced block
      const tok = (code.textContent || "").trim();
      if (!verified.has(tok)) continue;
      const link = openPathLink(tok, tok, true);
      code.replaceChildren(link);                              // the <code> chrome stays; its content is the link
      if (previewKind(tok) && !previewable.includes(tok) && !(skipThumbs && skipThumbs.includes(tok))) previewable.push(tok);
    }
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let n: Node | null;
  while ((n = walker.nextNode())) nodes.push(n as Text);
  for (const tn of nodes) {
    if (tn.parentElement?.closest("a, .file-uri-link, pre")) continue;   // already a link, or a fenced code block
    const inCode = !!tn.parentElement?.closest("code");                  // inline code — where bare filenames may link
    const text = tn.data;
    if (!text.includes("/") && !(inCode && text.includes("."))) continue;   // cheap pre-filter: no slash (and, in code, no dot) → nothing here
    const re = new RegExp(CLICKABLE_PATH_RE.source, "gi");
    const frag = document.createDocumentFragment();
    let last = 0, any = false, m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      let tok = m[0];
      const trail = tok.match(/[.,;:!?)\]}>"'`]+$/);   // don't grab a sentence's closing punctuation
      if (trail) tok = tok.slice(0, tok.length - trail[0].length);
      if (!tok) continue;
      const isUri = /^file:\/\//i.test(tok);
      if (!isUri && !looksLikeFilePath(tok) && !(inCode && looksLikeBareFileName(tok))) continue;   // "and/or", `np.array` etc. — leave as prose
      const fixed = !isUri && pathLinks ? pathLinks[tok] : undefined;   // the kernel's verdict, when it rendered one
      if (!isUri && pathLinks && typeof fixed !== "string") continue;   // checked against the filesystem: no such file (or several) → prose
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const open = isUri ? fileUriToPath(tok) : (fixed ?? tok);
      frag.appendChild(isUri ? fileUriLink(tok) : openPathLink(tok, open, true));
      if (previewKind(open) && !previewable.includes(open) && !(skipThumbs && skipThumbs.includes(open))) previewable.push(open);
      last = m.index + tok.length;
      re.lastIndex = last;
      any = true;
    }
    if (!any) continue;
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    tn.replaceWith(frag);
  }
  // A mentioned image/PDF renders FULL-SIZE under the message (the user 2026-07-20, who wanted not even a
  // thumbnail but a rendered image, like the user messages; supersedes the 2026-07-08 thumbnail strip,
  // which lives on in the feed's artifact strips). Absolute AND relative paths work — the kernel
  // resolves a relative one against this session's cwd, same as click-to-open. Per surface:
  //   web       — previewFull: the kernel serves the bytes straight into an <img> at the user-image
  //               scale / a PDF's native inline viewer; a path the kernel can't serve removes itself.
  //   VS Code   — the webview sandbox can't reach the kernel origin from an <img>, so an IMAGE rides
  //               the same host data-URL flow the user-message pictures use (buildPathImg, imgRequest
  //               now carrying the session id for relative resolution); a PDF keeps its click-to-open
  //               link (no inline viewer in the sandbox).
  // Capped so a message that enumerates a directory of images doesn't wallpaper the chat; every path
  // stays clickable regardless.
  if (previewable.length) {
    const strip = el("div", "path-thumbs");
    for (const p of previewable.slice(0, 4)) {
      const full = canPreview() ? previewFull(p, activeId)
        : previewKind(p) === "img" ? buildPathImg(p) : null;
      if (full) strip.appendChild(full);
    }
    if (strip.childElementCount) root.appendChild(strip);
  }
}

function renderEvent(ev: ChatEvent, prevEpoch?: number | null, worked?: number | null): HTMLElement {
  const turn = renderEventInner(ev);
  // pending-rewind overlay (reconcileRewind): this turn sits AFTER an edited message — it belongs to
  // the branch being abandoned, so it dims until the kernel's rewound payload replaces it
  if ((ev as any).rewound) turn.classList.add("rewound");
  // Deep-link anchor. An AskUserQuestion widget carries the ANSWER-line (tool_result
  // user line) uuid — that's the uuid the timeline emits for the decision, and the
  // answer line produces no standalone event/DOM node of its own. Everything else
  // anchors on its own uuid.
  const anchorUuid = (ev.kind === "tool" && ev.name === "AskUserQuestion" && ev.resultUuid) ? ev.resultUuid : ev.uuid;
  if (anchorUuid) turn.dataset.uuid = anchorUuid; // deep-link anchor (shared with vs_chat)
  // epoch-seconds stamp → time-based anchor fallback (when a uuid anchor is
  // stale/orphaned, the deep link can still land on the nearest moment)
  const epoch = eventEpoch(ev);
  if (epoch != null) turn.dataset.t = String(epoch);
  // rail time-stamp: HH:MM just to the LEFT of every dot (the user 2026-06-10) — a left
  // timestamp column so each event on the rail shows when it happened. On every dotted turn,
  // postal cards included (the user 2026-06-13: a postal message rides the rail like every other
  // event instead of stamping the time inside its own card). Prompts ride this rail too instead
  // of an in-bubble stamp (the human via debugger, 2026-06-12). The date shows only on the first
  // turn of a new (non-today) day.
  if (epoch != null && turn.querySelector(".dot")) turn.insertBefore(timeMarker(epoch, prevEpoch ?? null), turn.firstChild);
  // rail-dot fleet links: hover anywhere on the turn → white-highlight this turn's
  // event on the timeline AND outline its feed card(s); click the DOT → open that
  // card's modal in the feed (the host resolves turn → event → cards). The whole
  // turn is the hover target (the user 2026-06-12) — hovering the MESSAGE bubble or
  // the WORK/reply body must light the timeline, not only the rail dot.
  const railDot = turn.querySelector(".dot") as HTMLElement | null;
  if (anchorUuid || epoch != null) wireTurnHover(turn, railDot, anchorUuid ?? null, epoch ?? 0, ev.tlId ?? null);
  // a finished prompt's last reply carries a small "worked 2m 14s" tick in the rail
  // gutter (left, by the time-markers) — how long the session ran on that prompt.
  if (worked != null) turn.appendChild(elapsedFooter(worked));
  return turn;
}

// Format a worked-duration (seconds) the same way the live work-timer formats its
// elapsed (elapsedMs): "45s" / "2m 14s" / "1h 03m". Units distinguish it from the
// HH:MM rail time-markers.
function durLabel(secs: number): string {
  secs = Math.max(0, Math.floor(secs));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${secs % 60}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

function elapsedFooter(secs: number): HTMLElement {
  const f = el("div", "turn-elapsed");
  f.textContent = durLabel(secs);
  f.title = `worked ${durLabel(secs)} on this prompt`;
  return f;
}

// Hover uses the same 120ms intent debounce as ledger bullets / feed rows so
// scrolling the transcript doesn't strobe the timeline; leave clears. The sid is
// read at event time (only the ACTIVE session's view is hoverable). The host
// decides which timeline ATOM to light (the prompt DOT vs the work BAR) from the
// hovered line's uuid: a user-prompt turn carries the event's boundary uuid →
// dot; an assistant/tool/thinking turn carries a work-line uuid (or resolves by
// time into the period) → bar. So hovering the message lights the dot and
// hovering the work body lights the bar — the chat just reports its own uuid.
// HOVER is on the RAIL DOT only (the user 2026-06-15: hovering the message TEXT must not light the
// timeline — only the rail/"timeline" gutter does); the dot also keeps the click (open the feed card).
function wireTurnHover(turn: HTMLElement, dot: HTMLElement | null, uuid: string | null, t: number, tlId?: string | null) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const hoverTarget = dot || turn;   // only the dot triggers the timeline highlight (turn fallback if no dot)
  hoverTarget.addEventListener("mouseenter", () => {
    // ATOMIC local acknowledgement (the user 2026-07-17 ×2): the hovered turn's WHOLE segment lights
    // synchronously, in the same frame as the dot's own :hover growth. The kernel's authoritative
    // band replaces it on fan-back (usually identical geometry → no visible change).
    cancelHoverClear();   // a glyph→glyph handoff must not blank the glow in the gap
    instantLocalBand(turn);
    timer = setTimeout(() => { timer = undefined; if (activeId) vscodeApi?.postMessage({ type: "dotHover", sid: activeId, uuid, t, tlId }); }, 120);
  });
  hoverTarget.addEventListener("mouseleave", () => {
    clearLocalBand();
    if (timer) { clearTimeout(timer); timer = undefined; return; } // never fired — nothing to clear
    scheduleHoverClear();
  });
  if (dot) {
    dot.classList.add("dot-nav");
    dot.title = "click: jump to this on the timeline + feed · hover: highlight there";
    dot.addEventListener("click", (e) => {
      e.stopPropagation();
      // tlId rides along so the timeline can land on the EXACT glyph instead of the nearest thing at t —
      // the same id the hover already sends. Without it the kernel could only offer a bare time.
      if (activeId) vscodeApi?.postMessage({ type: "dotOpen", sid: activeId, uuid, t, tlId });
    });
  }
  // The rail LINE is a nav handle too (the user 2026-07-02): hovering EXACTLY on the line segment lights
  // it with the same expanding white ring the dots use, and drives the same timeline/feed cross-highlight
  // (same dotHover payload + 120ms intent debounce). The line itself is the turn's ::before pseudo — it
  // can't take pointer events — so a slim invisible hit strip overlays it; the dot's enlarged hit pad
  // stacks ABOVE the strip, so the dot always wins where they overlap.
  const rail = el("div", "rail-hit");
  rail.title = "click: jump to this on the timeline + feed · hover: highlight there";
  // The line CLICKS like its dots too (the user 2026-07-23). It already hovers like them, and a handle
  // that highlights on hover but does nothing on click reads as broken rather than as read-only. Same
  // dotOpen payload, so the line and the dot that begins its turn navigate to exactly the same place.
  rail.addEventListener("click", (e) => {
    e.stopPropagation();
    if (activeId) vscodeApi?.postMessage({ type: "dotOpen", sid: activeId, uuid, t, tlId });
  });
  let railTimer: ReturnType<typeof setTimeout> | undefined;
  rail.addEventListener("mouseenter", () => {
    cancelHoverClear();
    instantLocalBand(turn);
    railTimer = setTimeout(() => { railTimer = undefined; if (activeId) vscodeApi?.postMessage({ type: "dotHover", sid: activeId, uuid, t, tlId }); }, 120);
  });
  rail.addEventListener("mouseleave", () => {
    clearLocalBand();
    if (railTimer) { clearTimeout(railTimer); railTimer = undefined; return; }
    scheduleHoverClear();
  });
  turn.appendChild(rail);
}

// The cross-surface hover CLEAR is deferred a beat (the user 2026-07-17: moving along the rail blanked
// the whole segment highlight between adjacent glyphs, then rebuilt it — a large→small→large flicker).
// mouseleave schedules the clear; the very next mouseenter cancels it, so a handoff between glyphs of
// the same (or a new) segment replaces the glow without ever passing through empty. Same grace idiom as
// the timeline tooltip's deferred unfreeze. Left the rail entirely → the clear fires after the beat.
let hoverClearTimer: ReturnType<typeof setTimeout> | undefined;
function cancelHoverClear(): void {
  if (hoverClearTimer) { clearTimeout(hoverClearTimer); hoverClearTimer = undefined; }
}
function scheduleHoverClear(): void {
  cancelHoverClear();
  hoverClearTimer = setTimeout(() => { hoverClearTimer = undefined; vscodeApi?.postMessage({ type: "dotHover" }); }, 60);
}

// Instant, purely-local hover acknowledgement — shared by the dot and the rail strip (the user
// 2026-07-02 for the strip; 2026-07-17 extended to the dot, then widened from one inter-dot span to the
// hovered turn's WHOLE SEGMENT: the dot-to-dot subset lit first and the rest followed on fan-back —
// not atomic). The segment is approximated locally as the nearest PROMPT dot at-or-above (.dot.user /
// .dot.romp — the dots that start a turn) down to the NEXT prompt dot below, else the transcript's last
// dot (the kernel's own band clamps the same way). No prompt dot rendered above (a window cut) → the
// old nearest-dot span. The fan-back band replaces this with the authoritative segment — usually
// identical geometry, so nothing visibly changes. Dots only (the user 2026-07-03): no complete span →
// nothing local. Entering a target also drops any PREVIOUS fan-back band + rings in the same frame, so
// a segment→segment move swaps the highlight atomically instead of showing both.
function instantLocalBand(turn: HTMLElement): void {
  const host = turn.parentElement;
  if (!host) return;
  document.querySelectorAll(".rail-band").forEach((n) => n.remove());   // -local AND the previous fan-back
  clearRailRings();
  const hostR = host.getBoundingClientRect();
  const e = railBandEdges(turn, turn, hostR);
  if (e) drawRailBand(host, hostR, turn, e.top, e.bottom, true);
}

// ONE edge rule for BOTH bands — the instant local paint above and the kernel's fan-back in
// paintRailBand. They used to compute their edges differently: the local one walked to the bounding
// PROMPT dots, the fan-back to the nearest dot of ANY kind around the turns the kernel had glowed. The
// fan-back's answer is therefore never larger and is usually smaller, so hovering the rail lit a whole
// segment and then visibly gave part of it back a moment later (the user 2026-07-23, who asked that a
// highlight land once, atomically, and stay). Sharing the rule is what makes the second paint a no-op.
//
// Prompt dots are the right boundary because a segment IS a prompt-to-prompt unit: the walk lands on the
// same place the kernel's segment ends, whereas "nearest dot" lands on whatever glyph happens to be
// closest. A band still may never extend past the last dot into the stubbed lineless tail.
//
// EVERY fallback has to be invariant in `first`/`last` too, or the rule is shared in name only. The two
// callers pass different turns for the same hover — the local paint knows only the turn under the
// pointer, the fan-back knows the whole glowed run — so any walk measured RELATIVE to its argument gives
// the two different answers and the flicker comes back. That is why the bottom cannot fall through
// railDotBelow: on the live tail, where no prompt dot follows, "the next dot below" is the next glyph for
// the local paint and a later one for the fan-back, so the band landed short and then grew a tick later
// (the user 2026-07-23, second recording — the residue of the first fix). railLastDotFrom is the
// transcript's FINAL dot from any starting turn in the tail, so both callers land on it exactly.
// railPromptDot{Above,Below} and railDotAbove are already invariant this way: every turn in a segment
// walks back to the same prompt, and forward to the same next prompt.
function railBandEdges(first: HTMLElement, last: HTMLElement, hostR: DOMRect): { top: number; bottom: number } | null {
  // The top's fallback is the FIRST dot in the loaded window, not a walk back from `first`: when no prompt
  // is rendered above (a scroll window cut mid-segment) the segment began before what is on screen, so the
  // window's first dot is its visible start — and, being fixed, it is the same answer for both callers.
  const top = railPromptDotAbove(first, hostR) ?? railFirstDotIn(first, hostR);
  const bottom = railPromptDotBelow(last, hostR) ?? railLastDotFrom(last, hostR) ?? railDotAbove(last, hostR);
  if (top == null || bottom == null || bottom <= top) return null;
  return { top, bottom };
}
// The first dot in the turn's host, scanning from the top. Fixed for the whole window, so both callers
// resolve it identically no matter which turn each of them happens to hold.
function railFirstDotIn(turn: HTMLElement, hostR: DOMRect): number | null {
  const host = turn.parentElement;
  if (!host) return null;
  const d = host.querySelector<HTMLElement>(".turn .dot");
  if (!d) return null;
  const r = d.getBoundingClientRect();
  return r.top + r.height / 2 - hostR.top;
}
function clearLocalBand(): void {
  document.querySelectorAll(".rail-band-local").forEach((n) => n.remove());
  if (!document.querySelector(".rail-band")) clearRailRings();   // fan-back band may still own the rings
}
// Segment edges for the local band: prompts wear .dot.user (human, answered ask) or .dot.romp
// (injected) — the dots that BEGIN a turn in the event model.
function railPromptDotAbove(turn: HTMLElement, hostR: DOMRect): number | null {
  for (let n: Element | null = turn; n; n = n.previousElementSibling) {
    if (!(n instanceof HTMLElement) || !n.classList.contains("turn")) continue;
    const d = n.querySelector<HTMLElement>(".dot.user, .dot.romp");
    if (d) { const r = d.getBoundingClientRect(); return r.top + r.height / 2 - hostR.top; }
  }
  return null;
}
function railPromptDotBelow(turn: HTMLElement, hostR: DOMRect): number | null {
  for (let n: Element | null = turn.nextElementSibling; n; n = n.nextElementSibling) {
    if (!(n instanceof HTMLElement) || !n.classList.contains("turn")) continue;
    const d = n.querySelector<HTMLElement>(".dot.user, .dot.romp");
    if (d) { const r = d.getBoundingClientRect(); return r.top + r.height / 2 - hostR.top; }
  }
  return null;
}
// The live tail has no next prompt — the segment's band clamps to its own last dot, like the kernel's.
function railLastDotFrom(turn: HTMLElement, hostR: DOMRect): number | null {
  let y: number | null = null;
  for (let n: Element | null = turn; n; n = n.nextElementSibling) {
    if (!(n instanceof HTMLElement) || !n.classList.contains("turn")) continue;
    const d = n.querySelector<HTMLElement>(".dot");
    if (d) { const r = d.getBoundingClientRect(); y = r.top + r.height / 2 - hostR.top; }
  }
  return y;
}

// Transient cross-highlight FROM the timeline (host fans a bar hover here as
// glowTurns): white-ring the rail dot of every chat turn the hovered segments
// contain — matched BY UUID (the kernel sends each segment's atom uuids), not a
// time window — plus any postal card carrying a hovered message id. Empty
// groups+mids = clear. Glow is hover-transient, so a re-render that drops it
// mid-hover self-heals on the next hover tick (the user 2026-06-19).
function applyGlow(groups: Array<{ sid: string; uuids: string[] }>, mids: string[]) {
  document.querySelectorAll(".ext-glow").forEach((n) => n.classList.remove("ext-glow"));
  const midSet = new Set(mids);
  if (midSet.size) {
    document.querySelectorAll<HTMLElement>(".turn[data-mid]").forEach((n) => {
      if (midSet.has(n.dataset.mid || "")) n.classList.add("ext-glow");
    });
  }
  for (const g of groups) {
    const v = views.get(g.sid);
    if (!v) continue;
    const uset = new Set(g.uuids || []);
    if (!uset.size) continue;
    v.el.querySelectorAll<HTMLElement>(".turn[data-uuid]").forEach((n) => {
      if (uset.has(n.dataset.uuid || "")) n.classList.add("ext-glow");   // every row of a matched atom lights
    });
  }
  paintGlowRuler();   // mirror the glow as bands on the overview ruler (link_audit's #4)
  paintRailBand();    // one continuous measured band over the rail line (the user 2026-07-02)
}

// Rail bands ALWAYS end on dots (the user 2026-07-02 ×3, for whom nothing else could really have any meaning in
// this representation). The dots are the rail's only real y-coordinates, so every band edge is found by
// WALKING to a dot: up from a turn to the nearest dot at-or-above it, down to the nearest dot below.
// Turn-box edges are only the last-resort fallback at the transcript's ends, where no bounding dot exists.
function railDotAbove(turn: HTMLElement, hostR: DOMRect): number | null {
  for (let n: Element | null = turn; n; n = n.previousElementSibling) {
    if (!(n instanceof HTMLElement) || !n.classList.contains("turn")) continue;
    const d = n.querySelector<HTMLElement>(".dot");
    if (d) { const r = d.getBoundingClientRect(); return r.top + r.height / 2 - hostR.top; }
  }
  return null;
}
function railDotBelow(turn: HTMLElement, hostR: DOMRect): number | null {
  for (let n: Element | null = turn.nextElementSibling; n; n = n.nextElementSibling) {
    if (!(n instanceof HTMLElement) || !n.classList.contains("turn")) continue;
    const d = n.querySelector<HTMLElement>(".dot");
    if (d) { const r = d.getBoundingClientRect(); return r.top + r.height / 2 - hostR.top; }
  }
  return null;
}
// The band renders as a CAPSULE OUTLINE (the user 2026-07-03): straight ring-runs between dots that
// break tangentially at each dot's own white ring — the bracketing lines "turn into the lines around
// the circle and go around", never crossing through it. 7px ≈ where the band ring's edge (±3px off the
// line) meets the dot ring's outer circle (r 7.5) tangentially.
const RAIL_DOT_CLEAR = 7;
function railDotsBetween(host: HTMLElement, hostR: DOMRect, top: number, bottom: number): Array<{ el: HTMLElement; y: number; x: number }> {
  const out: Array<{ el: HTMLElement; y: number; x: number }> = [];
  host.querySelectorAll<HTMLElement>(".turn .dot").forEach((d) => {
    const r = d.getBoundingClientRect();
    const y = r.top + r.height / 2 - hostR.top;
    const x = r.left + r.width / 2 - hostR.left;   // dot CENTER x — an indented tg-child dot sits on the sub-rail
    if (y >= top - 1 && y <= bottom + 1) out.push({ el: d, y, x });
  });
  return out.sort((a, b) => a.y - b.y);
}
function drawRailBand(host: HTMLElement, hostR: DOMRect, xRef: HTMLElement, top: number, bottom: number, local: boolean): void {
  if (bottom <= top) return;
  // fallback CENTER x when a run has no bounding dot to hug: the reference turn's rail line
  // (.turn::before spans x 10.5–12.5 → center 11.5)
  const fallbackCx = xRef.getBoundingClientRect().left - hostR.left + 11.5;
  const cls = "rail-band" + (local ? " rail-band-local" : "");
  const dots = railDotsBetween(host, hostR, top, bottom);
  // every dot along the band EXPANDS in its own color (.rail-ring) — the thickened line runs to it and
  // the grown disc takes over (the user 2026-07-17: same thicken-in-color language as the timeline)
  for (const d of dots) d.el.classList.add("rail-ring");
  // one 4px-wide piece of the thickened rail (or 4px-tall, for a run along a corner arm)
  const put = (left: number, top_: number, w: number, h: number) => {
    if (w <= 0 || h <= 0) return;
    const band = el("div", cls);
    band.style.left = `${left}px`; band.style.top = `${top_}px`;
    band.style.width = `${w}px`; band.style.height = `${h}px`;
    host.appendChild(band);
  };
  const stops = [top, ...dots.map((d) => d.y), bottom];
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i] + RAIL_DOT_CLEAR, b = stops[i + 1] - RAIL_DOT_CLEAR;
    if (b <= a) continue;                       // dots closer than the clearance → the rings alone carry it
    // The band FOLLOWS the rail's detour through an expanded tool-group (the user 2026-07-17 ×2: the
    // rail goes in, down, and back — the band must trace the SAME path, never a straight run at one x
    // beside indented dots). A run whose bounding dots sit at DIFFERENT x's crosses a corner: the arm
    // hangs at the upper dot's turn-box bottom, so the run splits into an L — down the upper rail to
    // the corner, along the arm, down the lower rail. Same-x runs (and dotless clamps) stay straight.
    const upper = dots[i - 1] ?? null;          // stops[i] is dots[i-1].y whenever the upper edge is a dot
    const lower = dots[i] ?? dots[dots.length - 1] ?? null;
    const lx = lower ? lower.x : fallbackCx;
    if (upper && lower && Math.abs(upper.x - lower.x) > 1) {
      const turnBox = (upper.el.closest(".turn") as HTMLElement | null)?.getBoundingClientRect();
      const cornerY = turnBox ? turnBox.bottom - hostR.top - 1 : null;   // center of the 2px arm at the box bottom
      if (cornerY != null && cornerY > a && cornerY < b) {
        put(upper.x - 2, a, 4, cornerY - a);                                              // down the upper rail
        put(Math.min(upper.x, lower.x) - 2, cornerY - 2, Math.abs(upper.x - lower.x) + 4, 4);   // along the arm
        put(lower.x - 2, cornerY, 4, b - cornerY);                                        // down the lower rail
        continue;
      }
    }
    put(lx - 2, a, 4, b - a);
  }
}
function clearRailRings(): void {
  document.querySelectorAll(".dot.rail-ring").forEach((n) => n.classList.remove("rail-ring"));
}

// ONE continuous rail band per hovered segment: from the segment's own prompt dot down to the NEXT dot
// after its last turn. Repainted on every glow application (replacing any instant local band); a
// re-render wipes it and the next hover tick repaints — same transient contract as .ext-glow itself.
function paintRailBand(): void {
  document.querySelectorAll(".rail-band").forEach((n) => n.remove());
  clearRailRings();
  for (const v of views.values()) {
    const glowed = Array.from(v.el.querySelectorAll<HTMLElement>(".turn.ext-glow"));
    if (!glowed.length) continue;
    const hostR = v.el.getBoundingClientRect();
    const first = glowed[0], last = glowed[glowed.length - 1];
    // Same edge rule as the instant local band (railBandEdges) so this repaint changes nothing visible —
    // it used to walk to the nearest dot of any kind and hand back part of the highlight the local paint
    // had just drawn. Dots only either way (the user 2026-07-03: the rail line is stubbed after the last
    // event, so nothing may glow over that lineless space; an unterminated tail isn't a unit yet).
    const e = railBandEdges(first, last, hostR);
    if (!e) continue;
    drawRailBand(v.el, hostR, first, e.top, e.bottom, false);
  }
}

// ---- overview ruler (link_audit's #4, the user 2026-06-22) ----
// A thin strip over the #content scrollbar gutter that bands the FULL-transcript location of whatever turns
// currently carry .ext-glow — so a cross-surface hover (timeline / feed card / chat dot) shows WHERE in the
// scroll the hovered thing sits, even when it's scrolled off-screen. Hover-only: empty glow → hidden. Bands
// map CONTENT space → ruler space, so a plain scroll never moves them (they mark absolute transcript
// position); only a glow change or a #content relayout repaints. v1 is a pure indicator (pointer-events:none
// so the native scrollbar still works underneath) — the optional click-a-band-to-scroll is intentionally
// skipped so it can't swallow scrollbar clicks.
const RULER_W = 10;   // == the webkit scrollbar width (styles.css ::-webkit-scrollbar) so bands sit in its gutter
let glowRuler: HTMLElement | null = null;
function ensureGlowRuler(): HTMLElement {
  if (glowRuler && glowRuler.isConnected) return glowRuler;
  glowRuler = el("div", "glow-ruler");
  glowRuler.style.display = "none";
  document.body.appendChild(glowRuler);
  return glowRuler;
}
function paintGlowRuler(): void {
  const ruler = ensureGlowRuler();
  const content = document.getElementById("content");
  const v = activeId ? views.get(activeId) : null;
  // only the ACTIVE view's glows map onto its #content scroll (other views are display:none → zero rects)
  const glows = (content && v) ? Array.from(v.el.querySelectorAll<HTMLElement>(".turn.ext-glow")) : [];
  if (!content || !glows.length) { ruler.style.display = "none"; ruler.replaceChildren(); return; }
  const rect = content.getBoundingClientRect();
  const scrollH = content.scrollHeight || 1;
  const rulerH = content.clientHeight;          // the strip spans #content's VISIBLE height
  // each glowing turn → a [top, bot] span in CONTENT space (scroll-independent: + scrollTop, − content top)
  const segs = glows.map((turn) => {
    const top = turn.getBoundingClientRect().top - rect.top + content.scrollTop;
    return { top, bot: top + turn.offsetHeight };
  }).sort((a, b) => a.top - b.top);
  // coalesce contiguous / overlapping turns into ONE band; a multi-segment goal hover → a few disjoint bands
  const bands: Array<{ top: number; bot: number }> = [];
  for (const s of segs) {
    const last = bands[bands.length - 1];
    if (last && s.top <= last.bot + 3) last.bot = Math.max(last.bot, s.bot);
    else bands.push({ top: s.top, bot: s.bot });
  }
  // pin the strip over the scrollbar gutter at #content's right edge (getBoundingClientRect → robust to a
  // window-accent body border); height = the visible scroll height the bands map into
  ruler.style.top = rect.top + "px";
  ruler.style.height = rulerH + "px";
  ruler.style.left = (rect.right - RULER_W) + "px";
  ruler.replaceChildren();
  for (const b of bands) {
    const band = el("div", "glow-ruler-band");
    band.style.top = (b.top / scrollH * rulerH) + "px";
    band.style.height = Math.max(3, (b.bot - b.top) / scrollH * rulerH) + "px";   // min 3px so a short turn still reads
    ruler.appendChild(band);
  }
  ruler.style.display = "";
}

function eventEpoch(ev: ChatEvent): number | null {
  if (ev.ts) {
    const ms = Date.parse(ev.ts);
    if (!isNaN(ms)) return Math.floor(ms / 1000);
  }
  // postal "in" events carry their epoch in `t` (seconds) rather than an ISO `ts`,
  // so they still anchor a rail dot's hover wiring and deep-link fallback.
  if (ev.kind === "postal-service" && ev.t != null) return Math.floor(ev.t);
  return null;
}

// A rail time-stamp (HH:MM) for a turn. On the first turn of a new (non-today) day it
// also shows the date, with emphasis. A run of same-minute turns shows the stamp only
// on the first (the user 2026-06-12); see markerLabel() for the rules. A suppressed turn
// keeps an EMPTY marker (so the dot keeps its column alignment) but stashes its HH:MM in
// data-hm, which is what the sticky stamp reads to name the time at the top of the view.
// Nothing re-reveals a suppressed marker: a stamp marks a time CHANGE and nothing else
// (the user 2026-07-23) — see paintRailSticky for why the rail no longer needs repeats.
function timeMarker(epoch: number, prevEpoch: number | null): HTMLElement {
  const { text, day, hm } = markerLabel(epoch, prevEpoch, Date.now());
  const m = el("div", "time-marker");
  m.dataset.hm = hm;
  // The gutter shows the TIME and nothing else. The date rides a full-width day divider
  // instead (dayDividerFor below) — no date word has to fit 47px of rail any more.
  if (text) m.textContent = day ? hm : text;
  return m;
}

// The day boundary itself: a hairline rule across the prose column with the date on it
// ("Yesterday" / "Mon" / "Jun 3"), emitted as a SIBLING immediately before the first turn
// of a new (non-today) day. Returns null on every other turn.
//
// Why it is not in the rail (the user 2026-08-01): the date used to stack on its own row
// inside the .time-marker, but the gutter is only 47px wide and "Yesterday" measures 52.6px
// bold, so its "Y" was clipped by the pane's overflow. Nothing that has to FIT a fixed 47px
// is safe — `--fs` follows --vscode-chat-font-size, so a bigger chat font re-clips whatever
// just barely fit at 13px. Out here the label has the whole column and can never be cut off.
//
// It must be a SIBLING, never the turn's first child: .dot and .time-marker are absolutely
// positioned against the TURN's top edge, so a divider inside it would shove the message down
// and leave the dot stranded up beside the rule.
function dayDividerFor(epoch: number, prevEpoch: number | null): HTMLElement | null {
  const { day, date } = markerLabel(epoch, prevEpoch, Date.now());
  if (!day || !date) return null;
  const d = el("div", "day-divider");
  const lbl = el("span", "day-divider-label"); lbl.textContent = date;
  d.appendChild(lbl);
  return d;
}

// STICKY rail stamp (the user 2026-07-22). A stamp can only sit at a TURN boundary, so a single message
// taller than the viewport has nowhere to put one, and scrolling through it left the rail blank.
//
// This is why the rail no longer repeats a time it has already shown. There used to be a post-layout
// spacing pass that re-revealed a suppressed same-minute stamp every ~6 rows, purely so the gutter never
// went long without telling you the time. The sticky guarantees that outright — there is ALWAYS a stamp at
// the top of the view — so repeating "12:02" every few dots was noise (the user 2026-07-23). Now a stamp
// means exactly one thing: the time CHANGED here. Everything else is the sticky's job.
// This pins the current turn's HH:MM at the top of the gutter while you're inside it, and drops it the
// moment that turn's own marker is on screen showing the time — so there is always exactly one stamp
// visible, never two, and it hands off naturally as the next turn scrolls up.
//
// A fixed overlay on <body> (the glow-ruler pattern): #content holds the per-session view elements and is
// rebuilt/swapped on pushes and tab switches, so an element parented there would be destroyed; positioning
// off content.getBoundingClientRect() each paint keeps it aligned without owning any DOM inside the scroll.
let railSticky: HTMLElement | null = null;
function ensureRailSticky(): HTMLElement {
  if (railSticky && railSticky.isConnected) return railSticky;
  railSticky = el("div", "time-marker rail-sticky");
  railSticky.style.display = "none";
  document.body.appendChild(railSticky);
  return railSticky;
}

function paintRailSticky(): void {
  const stamp = ensureRailSticky();
  const content = document.getElementById("content");
  const v = activeId ? views.get(activeId) : null;
  if (!content || !v || v.el.style.display === "none") { stamp.style.display = "none"; return; }
  const cRect = content.getBoundingClientRect();
  const cTop = cRect.top, cBottom = cRect.bottom;
  const BUFFER = 6;                             // the gap kept above the sticky; ALSO the hand-off line
  const line = cTop + BUFFER;                   // where the sticky rests, and where a real stamp hands off to it
  // One pass, all reads (no interleaved writes): the turn spanning the LINE (its time is what sits at the top)
  // along with whether its OWN marker is showing and where, the gutter x, and every marker's top for the
  // visibility pass below.
  let marker: HTMLElement | null = null;
  let anyMarker: HTMLElement | null = null;
  let markerTop = 0, markerShown = false;      // the tracked turn's own marker: where it is, and is it stamped
  const all: Array<[HTMLElement, number]> = []; // [marker, top] for the visibility pass
  for (const t of Array.from(v.el.children) as HTMLElement[]) {
    const m = t.firstChild as HTMLElement | null;
    if (!m || m.nodeType !== 1 || !m.classList || !m.classList.contains("time-marker")) continue;
    if (!anyMarker) anyMarker = m;             // any marker gives the gutter's real x geometry
    const r = m.getBoundingClientRect();
    all.push([m, r.top]);
    if (t.getBoundingClientRect().top <= line) {          // the turn whose content sits at the line
      marker = m; markerTop = r.top; markerShown = !!m.textContent;
    }
  }
  const hm = marker ? (marker.dataset.hm || "") : "";
  // The tracked turn's OWN stamp leads the top slot while it is at or below the line — it scrolls up freely
  // until it reaches the line, and the instant it crosses ABOVE (markerTop < line) the sticky takes the same
  // slot showing the same time, so the swap is invisible: no gap, no clipped sliver (the user 2026-07-23).
  // Deliberately keyed on the TRACKED turn's marker, not on any stamp anywhere: a LATER time change further
  // down the view is a different time, so it must not blank the top — that would leave the slot empty, which
  // is the whole thing the sticky exists to prevent.
  const realLeads = markerShown && markerTop >= line;
  if (!hm || realLeads) {
    stamp.style.display = "none";
    for (const [m] of all) m.style.visibility = "";   // real stamp leads → nothing suppressed
    return;
  }
  // The sticky leads: pin it at the line and hide every marker that has crossed ABOVE it, so the real stamp
  // handing off never shows a clipped duplicate beside the sticky. Markers at or below the line stay visible —
  // they are the genuine lower stamps, not doubles.
  for (const [m, top] of all) m.style.visibility = top < line ? "hidden" : "";
  const g = (anyMarker || marker!).getBoundingClientRect();
  stamp.textContent = hm;
  stamp.style.left = g.left + "px";
  stamp.style.width = g.width + "px";
  stamp.style.top = line + "px";
  stamp.style.display = "";
}

// Scroll is the sticky stamp's primary driver, and a re-render moves the geometry under it — both funnel
// here, rAF-coalesced so a fast flick and a busy tail each paint once per frame. (This used to be two
// wrappers: one re-ran the spacing pass on re-render, one repainted the sticky on scroll. With the spacing
// pass gone there is only one thing left to do, so there is only one scheduler.)
let railStickyPending = false;
function scheduleRailSticky(): void {
  if (railStickyPending) return;
  railStickyPending = true;
  requestAnimationFrame(() => { railStickyPending = false; paintRailSticky(); });
}

function renderEventInner(ev: ChatEvent): HTMLElement {
  if (ev.kind === "system") return renderSystem(ev);
  if (ev.kind === "user") {
    // The CLI's stop record is an EVENT, not a message (the user 2026-07-02): render it as a slim rail
    // marker in the compact-divider's language — never a person-blue bubble that reads like typed input.
    if ((ev as any).interruptMarker) {
      const turn = el("div", "turn turn-interrupt");
      turn.appendChild(dot("ring"));
      const line = el("div", "interrupt-line");
      line.appendChild(el("span", "interrupt-square"));   // the stop button's own glyph, tying cause to effect
      // The seam says WHY when the transcript does (kernel interruptCause, from the resume notice romp
      // itself injected): a kernel-restart/crash cut is not the user pressing stop, and the old blanket
      // "you stopped this turn" title blamed them for every deploy (the user 2026-07-09).
      const cause = (ev as any).interruptCause;
      if (cause === "restart") {
        line.appendChild(document.createTextNode("interrupted — kernel restart"));
        line.title = "a romp kernel restart cut this turn; the session was resumed automatically";
      } else if (cause === "crash") {
        line.appendChild(document.createTextNode("interrupted — process died"));
        line.title = "this session's claude process died mid-turn; the session was resumed automatically";
      } else {
        line.appendChild(document.createTextNode("interrupted"));
        line.title = "you stopped this turn here (the stop button / Ctrl+C)";
      }
      turn.appendChild(line);
      return turn;
    }
    // A romp SYSTEM notice (kernel restart/resume, Retry) — flagged server-side (ev.rompSystem) so it's
    // separable from a feed NUDGE, which shares the same author (the user 2026-07-06). It gets its OWN romp
    // notice card (swirl chip + faded-accent box), NOT the gray nudge bubble: it's status, not a prompt.
    if ((ev as any).rompSystem && ev.md) {
      // strip the invisible <!-- romp-* --> markers, then the leading "[romp]" (the chip already says who it's from)
      const text = ev.md.replace(/<!--[\s\S]*?-->/g, "").replace(/^\s*\[romp\]\s*/i, "").trim();
      const firstLine = (text.split("\n").find((l) => l.trim()) || text).trim();
      const gist = firstLine.length > 90 ? firstLine.slice(0, 88).replace(/\s+\S*$/, "") + "…" : firstLine;
      const body = el("div", "notice-md md");
      body.innerHTML = md(text);
      return noticeCard({ variant: "romp", chip: "romp", logo: true, head: gist, body,
                          collapsible: collapseWs(text) !== collapseWs(gist),
                          key: ev.uuid ? "rsys:" + ev.uuid : undefined });
    }
    // Three flavors of a "user-role" turn: a GENUINE typed prompt → the blue right-aligned bubble; a
    // message romp INJECTED (a feed nudge / follow-up — ev.romp) → a GRAY right-aligned bubble with a
    // "romp" tag, so it's clear romp (not you) sent it (the user 2026-06-19); everything else harness-
    // injected (compact summary, /command stdout, system reminders) → a neutral left note box.
    const romp = !!ev.romp;
    const injected = !ev.human && !romp;
    const turn = el("div", "turn turn-user" + (romp ? " romp" : injected ? " injected" : ""));
    // Unresolved postal ids ride the raw turn so a timeline message arc can still land on it. Without
    // this the arc pointed at a turn with nothing to match and the click died silently (the user
    // 2026-07-23). A hydrated card sets data-mid in renderPostalService instead.
    if (ev.mid) turn.dataset.mid = ev.mid;
    if (ev.mids && ev.mids.length) turn.dataset.mids = ev.mids.join(" ");
    // Prompts ride the rail like every other turn: their own dot + a left-gutter HH:MM marker (added in
    // renderEvent). Genuine prompts get the solid blue dot; a romp injection a gray dot; harness notes the
    // hollow ring used by assistant turns.
    turn.appendChild(dot(romp ? "romp" : injected ? "ring" : "user"));
    // a TYPED follow-up (resumed a goal) → a compact "↩ Follow-up · <goal>" header, the romp goal-context
    // quote + markers already stripped server-side. Same header the pending queued render uses (consistency).
    if (ev.followUp && !romp) turn.appendChild(followUpHeader(ev.goal, ev.fuCtx, ev.uuid ? "u:" + ev.uuid : undefined));
    const hasImgs = !!(ev.images && ev.images.length);
    if (ev.md || hasImgs) {
      if (romp) {
        // every romp bubble carries the "romp" tag WITH the swirl LOGO next to it, so any message from romp reads
        // as romp at a glance — incl. system notices like the kernel-restart resume (the user 2026-07-05). This
        // supersedes the 2026-06-23 rule that drew the logo ONLY on auto-nudges (ev.rompAuto): at the data level
        // a romp system notice is indistinguishable from a Nudge-button click (both romp-injected, no romp-auto),
        // so the logo now marks the romp tag wherever it appears. Served at /media on the web dashboard; in a
        // sandbox without it the img self-removes (alt stays empty).
        const tag = el("div", "romp-tag");
        const logo = el("img", "romp-tag-logo") as HTMLImageElement;
        logo.src = mediaSrc("romp-swirl-glyph.svg"); logo.alt = ""; logo.onerror = () => logo.remove();
        tag.appendChild(logo);
        tag.appendChild(document.createTextNode("romp"));
        turn.appendChild(tag);
      }
      const bubble = el("div", (romp ? "romp-bubble" : injected ? "user-note" : "user-bubble") + " md");
      // A slash COMMAND you sent reads as a special keyword, not prose (the user 2026-06-29): render the leading
      // "/cmd" token as a monospace chip. Genuine human bubbles only (a romp/injected note is never a command).
      // paths this turn already renders as full in-bubble images (both the caption path and a
      // "path:"-sourced src) — the linkifier must not ALSO thumb them, or the picture shows twice
      const imgPaths = (ev.images || [])
        .flatMap((im) => [im.path, im.src.startsWith("path:") ? im.src.slice(5) : ""])
        .filter((p): p is string => !!p);
      if (!romp && !injected && ev.md && renderSlashCmd(bubble, ev.md)) {
        // a COMMAND is a user GESTURE, not a user message (the user 2026-08-13): it changes something
        // rather than saying something, so it sheds the blue said-thing bubble and reads in the
        // system-event family (the ✦ dividers) — a dim left-aligned row: ✦ mark, mono chip, args
        turn.classList.add("turn-cmd");
        bubble.classList.add("cmd-row");
      } else if (romp && ev.md) {
        // A romp-injected NUDGE (auto status-check, Nudge button, injected follow-up) is mechanical
        // bookkeeping — progressive disclosure (the user 2026-07-17): default is a ONE-LINE gist with a
        // caret; click the bubble for the full text. Keyed, so an expanded nudge survives re-renders.
        // The gist SAYS WHAT ROMP DID, not the message's first line (the user 2026-07-17 ×2: a follow-up
        // opens with the goal-context "> …" quote, so the text gist read as the user's own words — pure
        // confusion). Known flavors get a semantic label; the text fallback skips quoted "> " lines.
        const raw = ev.md.replace(/<!--[\s\S]*?-->/g, "").trim();
        const lines = raw.split("\n").map((l) => l.trim());
        const firstLine = lines.find((l) => l && !l.startsWith(">")) || lines.find((l) => l) || raw;
        const gist = ev.followUp ? "follow-up" + (ev.goal ? " · " + ev.goal : "")
          : ev.rompAuto ? "nudged for a status update" + (ev.goal ? " · " + ev.goal : "")
          : firstLine.length > 90 ? firstLine.slice(0, 88).replace(/\s+\S*$/, "") + "…" : firstLine;
        const more = collapseWs(raw) !== collapseWs(gist);
        const gistEl = el("div", "nudge-gist");
        if (more) { const c = el("span", "nudge-caret"); c.textContent = "▸"; gistEl.appendChild(c); }
        gistEl.appendChild(document.createTextNode(gist));
        bubble.appendChild(gistEl);
        if (more) {
          const full = el("div", "nudge-full md");
          full.innerHTML = md(raw);
          linkifyFileUris(full, imgPaths, ev.spacePaths, ev.pathLinks);
          bubble.appendChild(full);
          bubble.classList.add("nudge-collapsible");
          // toggle rides the stable document.body delegate (data-act), NOT a per-render listener —
          // the tail rebuilds every push and a rebuilt bubble eats a mid-press click (CLAUDE.md)
          bubble.dataset.act = "nudgetoggle";
          const nkey = ev.uuid ? "nudge:" + ev.uuid : undefined;
          if (nkey) bubble.dataset.nkey = nkey;
          applyFold(bubble, "expanded", nkey);
          bubble.title = bubble.classList.contains("expanded") ? "click to collapse" : "click to expand";
        }
      } else if (ev.md) {
        bubble.innerHTML = md(ev.md);
        linkifyFileUris(bubble, imgPaths, ev.spacePaths, ev.pathLinks);   // bare file:// URLs in a message → clickable (open in the host's default app)
      }
      // images, IN the bubble (part of his message): thumbnail + open/copy caption;
      // a literal path in the typed text becomes the same open-link inline.
      if (ev.images) {
        linkifyImgPaths(bubble, ev.images.map((im) => im.path).filter((p): p is string => !!p));
        const mdText = ev.md || "";   // a path present here is already a link in the bubble → drop the caption's repeat
        for (const im of ev.images) bubble.appendChild(userImage(im, !!(im.path && mdText.includes(im.path))));
      }
      turn.appendChild(bubble);
      // NEVER-DELIVERED send (kernel ev.undelivered, from the backend's dropped-echo marking): the
      // session's process died holding this message, so it was never seen — say so instead of letting
      // it pose as history (the user 2026-07-29: a two-day-old lost send kept resurfacing mid-chat as
      // an ordinary sent bubble, hopping turns, with its stale timestamp reading as a glitch). The
      // bubble stays — it is the only surviving copy of the text — and the note under it names the
      // loss and offers the two honest moves: put the text back in the composer, or dismiss the
      // record. Buttons ride the body delegate (data-act): the tail rebuilds every push, and a
      // per-render listener eats a mid-press click (CLAUDE.md).
      if (ev.undelivered) {
        turn.classList.add("undelivered");
        bubble.classList.add("undelivered-bubble");
        const note = el("div", "undelivered-note");
        note.title = "This message never reached the session: its process died holding it before it was written to the conversation.";
        const label = el("span", "undelivered-label");
        label.textContent = "never delivered";
        note.appendChild(label);
        if (!romp && !injected && ev.md) {   // restore only the user's own words, not a romp injection's
          const re = el("button", "undelivered-act") as HTMLButtonElement;
          re.type = "button";
          re.textContent = "copy to composer";
          re.title = "Put the text back in the composer to review and send again";
          re.dataset.act = "echorestore";
          (re as any)._etext = ev.md;
          note.appendChild(re);
        }
        const dx = el("button", "undelivered-act") as HTMLButtonElement;
        dx.type = "button";
        dx.textContent = "dismiss";
        dx.title = "Remove this never-delivered message";
        dx.dataset.act = "echodismiss";
        if (ev.uuid) dx.dataset.euuid = ev.uuid;
        if (ev.echoT) dx.dataset.et = String(ev.echoT);
        note.appendChild(dx);
        turn.appendChild(note);
      }
      // EDIT affordance (SDK sessions): rewind the conversation to just before this message and take a
      // new branch with an edited version — the cloud-UI edit semantics. Shown on genuine human bubbles
      // the backend can address (reconcileRewind's _editable set: has a transcript uuid, newer than the
      // last compaction); the kernel re-validates on click and warn-toasts a refusal (e.g. mid-turn).
      const editSid = renderingSid;
      if (!romp && !injected && ev.uuid && editSid
          && (sessions.get(editSid) as any)?._editable?.has(ev.uuid)) {
        const edit = el("button", "msg-edit") as HTMLButtonElement;
        edit.type = "button";
        edit.textContent = "edit";
        edit.title = "Edit this message — sending rewinds the conversation to this point and continues on a new branch (later turns are abandoned)";
        const uuid = ev.uuid, orig = ev.md || "";
        edit.addEventListener("click", (e) => { e.stopPropagation(); beginComposerEdit(editSid, uuid, orig); });
        // DELETE affordance: roll the conversation back to just before this message — nothing is sent;
        // this message and everything after become the abandoned branch. Two-click arm (the second
        // click confirms); leaving or de-focusing the button disarms it, and a re-render rebuilding
        // the node disarms too — every miss fails toward "not deleted".
        const del = el("button", "msg-del") as HTMLButtonElement;
        del.type = "button";
        del.textContent = "delete";
        del.title = "Delete this message — rolls the conversation back to just before it (this message and everything after are abandoned)";
        const disarm = () => { del.classList.remove("armed"); del.textContent = "delete"; };
        del.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!del.classList.contains("armed")) { del.classList.add("armed"); del.textContent = "roll back?"; return; }
          disarm();
          fireRewindDelete(editSid, uuid);
        });
        del.addEventListener("blur", disarm);
        del.addEventListener("pointerleave", disarm);
        // RESTORE-FILES affordance (the user 2026-08-04): put the WORKSPACE back the way it was just
        // before this message — the SDK's file-checkpoint rewind (rewind_files). The conversation is
        // untouched; edit/delete cover that. Same two-click arm as delete (destructive — every miss
        // fails toward "not restored"); the kernel warns on a refusal (tmux, disconnected).
        const rf = el("button", "msg-restorefiles") as HTMLButtonElement;
        rf.type = "button";
        rf.textContent = "restore files";
        rf.title = "Restore files to their state just before this message — the conversation is untouched";
        const rfDisarm = () => { rf.classList.remove("armed"); rf.textContent = "restore files"; };
        rf.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!rf.classList.contains("armed")) { rf.classList.add("armed"); rf.textContent = "revert files?"; return; }
          rfDisarm();
          rf.disabled = true; rf.textContent = "restoring…";   // acknowledged; a re-render resets the label
          vscodeApi?.postMessage({ type: "rewindFiles", id: editSid, uuid });
        });
        rf.addEventListener("blur", rfDisarm);
        rf.addEventListener("pointerleave", rfDisarm);
        // FORK affordance (the user 2026-08-13): branch a NEW parallel session from just before this
        // message — old and new then run as separate threads (the rewind family above edits THIS
        // session; fork leaves it untouched). Non-destructive, so no two-click arm: the name modal is
        // the confirmation.
        const fk = el("button", "msg-fork") as HTMLButtonElement;
        fk.type = "button";
        fk.textContent = "fork";
        fk.title = "Fork the session from just before this message — a new parallel session carries the conversation up to here; this one is untouched";
        fk.addEventListener("click", (e) => { e.stopPropagation(); showForkPrompt(editSid, uuid); });
        const acts = el("div", "msg-acts");   // one row under the bubble (the turn is a column flex)
        acts.appendChild(edit);
        acts.appendChild(del);
        acts.appendChild(rf);
        acts.appendChild(fk);
        turn.appendChild(acts);
      }
    }
    if (ev.reminders && ev.reminders.length) {
      // A backgrounded agent's <task-notification> gets its OWN informative card (name + status + result);
      // everything else stays a plain "ⓘ N system reminders" fold (the user 2026-06-30).
      const plain: string[] = [];
      ev.reminders.forEach((r, i) => {
        const a = parseAgentNotif(r);
        if (a) turn.appendChild(renderAgentNotif(a, ev.taskOutputs, ev.uuid ? "agn:" + ev.uuid + ":" + i : undefined));
        else plain.push(r);
      });
      if (plain.length) {
        const body = el("div", "reminder-body");
        for (const r of plain) body.appendChild(preEl(r));
        const n = plain.length;
        turn.appendChild(noticeCard({ variant: "reminder", chip: "system",
          head: `${n} reminder${n > 1 ? "s" : ""}`, body, nested: true,   // rendered inside the carrying user turn
          key: ev.uuid ? "rem:" + ev.uuid : undefined }));
      }
    }
    return turn;
  }
  if (ev.kind === "assistant") {
    // The null settle-reply closing an interrupted turn (kernel interruptSettle: "No response requested."
    // right after the interrupt record) is part of the SEAM, not the agent speaking — render it in the
    // interrupt marker's own language instead of a full assistant bubble (the user 2026-07-09: every
    // kernel-restart cut minted one of these bubbles per session).
    if ((ev as any).interruptSettle) {
      const turn = el("div", "turn turn-interrupt");
      turn.appendChild(dot("ring"));
      const line = el("div", "interrupt-line");
      line.appendChild(document.createTextNode("no response — turn settled"));
      line.title = "the model closed the interrupted turn with nothing to add; the real work resumes below";
      turn.appendChild(line);
      return turn;
    }
    const turn = el("div", "turn turn-assistant");
    turn.appendChild(dot("ring"));
    const body = el("div", "assistant md");
    body.innerHTML = md(ev.md);
    highlight(body);
    linkifyFileUris(body, undefined, ev.spacePaths, ev.pathLinks);   // bare file:// URLs + verified spaced filenames → clickable
    turn.appendChild(body);
    return turn;
  }
  if (ev.kind === "thinking") {
    const turn = el("div", "turn turn-thinking");
    turn.appendChild(dot("ring"));
    const t = el("div", "thinking" + (ev.encrypted ? " encrypted" : ""));
    t.textContent = ev.encrypted ? "Thinking…" : ev.text;
    if (ev.encrypted) { turn.appendChild(t); return turn; }   // already a one-liner
    // condense: clamp to ~2 lines with a fade; click to expand (the user: don't let
    // the interspersed thinking blocks dominate vertically).
    const clamp = el("div", "think-clamp");
    clamp.appendChild(t);
    clamp.title = "click to expand";
    const tkey = ev.uuid ? "think:" + ev.uuid : undefined;
    applyFold(clamp, "expanded", tkey);
    clamp.addEventListener("click", () => rememberFold(clamp, "expanded", tkey));
    turn.appendChild(clamp);
    return turn;
  }
  if (ev.kind === "postal-service") return renderPostalService(ev);
  if (ev.kind === "teammate") return renderTeammate(ev);
  if (ev.kind === "todo") return renderTodo(ev);
  if (ev.kind === "queued") return renderQueued(ev);
  if (ev.kind === "apiError") return renderApiError(ev);
  if (ev.kind === "compacting") return renderCompacting();
  if (ev.kind === "clearing") return renderClearing();
  if (ev.kind === "clear") return renderClear(ev);
  if (ev.kind === "reconnecting") return renderReconnecting(ev);
  if (ev.kind === "retrying") return renderRetrying(ev);
  if (ev.kind === "retried") return renderRetried(ev);
  if (ev.kind === "retryGaveUp") return renderRetryGaveUp(ev);
  if (ev.kind === "apiErrorNote") return renderApiErrorNote(ev);
  if (ev.kind === "effortApplied") return renderEffortApplied(ev);
  if (ev.kind === "modelFallback") return renderModelFallback(ev);
  if (ev.kind === "compact") return renderCompact(ev);
  return renderTool(ev);
}

// Prettify a model id for the collapsed summary line: "claude-opus-4-8" → "Opus 4.8". Unknown ids pass
// through unchanged so nothing is ever hidden behind a bad guess.
function prettyModel(id: string): string {
  const m = /(?:claude-)?(opus|sonnet|haiku|fable)(?:-(\d+))?(?:-(\d+))?/i.exec(id);
  if (!m) return id;
  const fam = m[1][0].toUpperCase() + m[1].slice(1).toLowerCase();
  const ver = [m[2], m[3]].filter(Boolean).join(".");
  return ver ? `${fam} ${ver}` : fam;
}

// The pinned "system context" card at the very top of the transcript (the user 2026-06-19): a proper
// bordered BOX that looks complete even when collapsed — a ⚙ header with a one-line summary (model ·
// N CLAUDE.md) and a caret. Expanding reveals the session's model / permission-mode / cwd / branch /
// Claude Code version, then the CLAUDE.md instruction files that were in effect, each as its own raw,
// scrollable SUB-box. It is explicitly NOT the verbatim Claude Code harness prompt — that text is never
// written to the transcript, so it can't be shown; the closing note says so. Carries no dot/timestamp
// (no ts/uuid) → renderEvent leaves it off the conversational rail. Its open/closed state is persisted
// per session (keyed by renderingSid) so a send/turn re-render never snaps it shut.
// Make `elem` open the folder `cwd` with the configured opener on click (the user 2026-06-27): used
// EVERYWHERE a folder location is shown (statusline, the System-context Directory row, …). Click-safe — the
// action rides a data-act caught by the document-level openFolder delegate, so it works under any re-rendering
// surface without per-node handlers. `sid` (the owning session's, possibly host-prefixed, id — the user
// 2026-07-03) rides along as data-id: for a REMOTE session this is how the kernel knows to SSH out instead
// of treating a remote path as local (a silent no-op, since that path doesn't exist here). This pane stays
// host-BLIND as designed (see federation.ts) — sid is just echoed back opaquely, never parsed here.
function asFolderLink(elem: HTMLElement, cwd: string, sid?: string): void {
  if (!cwd) return;
  elem.dataset.act = "openFolder";
  elem.dataset.cwd = cwd;
  if (sid) elem.dataset.id = sid;
  elem.classList.add("folder-link");
  elem.title = cwd + "  ·  click to open this folder";
}

// Small inline-SVG folder in the romp line-icon style (matches ctxIcon: 16-unit viewBox, currentColor, so it
// inherits the dim statusline tint / brightens on the folder-link hover) — the monochrome replacement for the
// 📁 emoji beside the statusline directory (the user 2026-07-15). Trusted constant markup.
function folderIcon(): HTMLElement {
  const span = el("span", "status-dir-icon");
  span.innerHTML = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" '
    + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M2 12.6 a1 1 0 0 1-1-1 V4.4 a1 1 0 0 1 1-1 H5.9 a1 1 0 0 1 0.7 0.3 L7.8 5 '
    + 'H13 a1 1 0 0 1 1 1 V11.6 a1 1 0 0 1-1 1 Z"/></svg>';
  return span;
}

function renderSystem(ev: Extract<ChatEvent, { kind: "system" }>): HTMLElement {
  const turn = el("div", "turn turn-system");
  const key = renderingSid ? "sysctx:" + renderingSid : undefined;
  const card = el("div", "sys-card");
  applyFold(card, "open", key);

  const head = el("div", "sys-card-head");
  const gear = el("span", "sys-gear"); gear.textContent = "⚙"; head.appendChild(gear);
  const title = el("span", "sys-title"); title.textContent = "System context"; head.appendChild(title);
  const n = (ev.claudemd || []).length;
  const bits: string[] = [];
  if (ev.model) bits.push(prettyModel(ev.model));
  if (ev.cwd) bits.push("📁 " + (ev.cwd.replace(/\/+$/, "").split("/").pop() || ev.cwd));   // dir basename, glanceable; full path in the row below
  if (n) bits.push(`${n} CLAUDE.md`);
  const sub = el("span", "sys-sub"); sub.textContent = bits.join(" · "); head.appendChild(sub);
  const caret = el("span", "sys-caret"); caret.textContent = "▸"; head.appendChild(caret);
  head.title = "the CLAUDE.md instructions + config this session is running under";
  head.addEventListener("click", () => rememberFold(card, "open", key));
  card.appendChild(head);

  const body = el("div", "sys-card-body");
  const rows: [string, string][] = [];
  if (ev.model) rows.push(["Model", ev.model]);
  if (ev.mode) rows.push(["Permission mode", ev.mode]);
  if (ev.cwd) rows.push(["Directory", ev.cwd]);
  if (ev.gitBranch) rows.push(["Git branch", ev.gitBranch]);
  // where the work ACTUALLY lands (the user 2026-08-13): the repo convention here does real work on
  // per-session worktrees beside the registered clone, so the Directory/branch rows alone read 'main'
  // forever; the kernel derives this from the newest edit event and sends it only when it differs.
  if (ev.workTree) rows.push(["Worktree", ev.workTree.dir + (ev.workTree.branch ? "  ⎇ " + ev.workTree.branch : "")]);
  if (ev.version) rows.push(["Claude Code", ev.version]);
  if (rows.length) {
    const grid = el("div", "sys-meta");
    for (const [k, val] of rows) {
      const ke = el("span", "sys-key"); ke.textContent = k; grid.appendChild(ke);
      const ve = el("span", "sys-val"); ve.textContent = val;
      if (k === "Directory") asFolderLink(ve, val, renderingSid || undefined);   // the cwd path → click to open the folder
      grid.appendChild(ve);
    }
    body.appendChild(grid);
  }
  for (const doc of ev.claudemd || []) {
    const sec = el("div", "sys-doc");
    const dh = el("div", "sys-doc-head");
    const scope = el("span", "sys-doc-scope " + (doc.scope === "global" ? "global" : "project"));
    scope.textContent = doc.scope === "global" ? "global" : "project";
    const pth = el("span", "sys-doc-path"); pth.textContent = doc.path;
    dh.appendChild(scope); dh.appendChild(pth);
    sec.appendChild(dh);
    sec.appendChild(preEl(doc.text));   // raw text in a bordered, scrollable sub-box (.fold-pre)
    body.appendChild(sec);
  }
  const note = el("div", "sys-note");
  note.textContent = "Claude Code’s base harness prompt isn’t recorded in the transcript, so it isn’t shown here — this is the CLAUDE.md instructions and session config that were in effect.";
  body.appendChild(note);
  card.appendChild(body);

  turn.appendChild(card);
  return turn;
}

// AskUserQuestion — render the posed question(s) + options. While the question is still pending it's a
// neutral "Question" card; once it's been ANSWERED it becomes the blue, right-aligned "you answered
// Claude's question" box so the scrollback shows it was a reply to a popup, not a typed message (the
// user 2026-06-16). Prefers the kernel's structured askAnswer (question/options/chosen already joined);
// falls back to parsing the raw tool input/output when it isn't attached yet, so the turn renders the
// same either way.
function renderAsk(ev: Extract<ChatEvent, { kind: "tool" }>): HTMLElement | null {
  const blocks = (ev.askAnswer && ev.askAnswer.length) ? ev.askAnswer : parseAskRaw(ev);
  if (!blocks || !blocks.length) return null;
  // answered = at least one question has a recorded answer (chosen text). Empty chosen = still pending.
  const answered = blocks.some((b) => b.chosen && b.chosen.length > 0);

  const turn = el("div", "turn turn-ask" + (answered ? " answered" : ""));
  turn.appendChild(dot(answered ? "user" : "ring"));   // answered → the blue user dot, matching the box
  const card = el("div", "ask-card" + (answered ? " ask-answered" : ""));
  if (answered) {
    const tag = el("div", "ask-answered-tag");
    tag.textContent = "↳ You answered Claude’s question";
    card.appendChild(tag);
  } else {
    const head = el("div", "ask-head");
    head.textContent = blocks.length > 1 ? `${blocks.length} questions` : "Question";
    card.appendChild(head);
  }
  for (const b of blocks) {
    const qel = el("div", "ask-q");
    const qt = el("div", "ask-qtext"); qt.textContent = b.question || b.header || ""; qel.appendChild(qt);
    // While PENDING the options live in the red picker below (renderLiveAsk), so the transcript turn stays
    // a compact "Question + title" card — no options — instead of duplicating the whole picker (the user
    // 2026-07-22). Once ANSWERED it fills in to the full question + chosen-option box.
    if (answered) {
      const opts = Array.isArray(b.options) ? b.options : [];
      const labels = opts.map((o) => String(o.label || "")).filter(Boolean);
      const chosen = (b.chosen || []).map((c) => String(c));
      const picked = new Set(chosen.filter((c) => labels.includes(c)));   // answers that name an option
      const others = chosen.filter((c) => !labels.includes(c));           // free-text "Other" answers
      for (const o of opts) {
        const isChosen = !!o.label && picked.has(String(o.label));
        const opt = el("div", "ask-opt" + (isChosen ? " chosen" : ""));
        const mark = el("span", "ask-mark"); mark.textContent = isChosen ? "●" : "○"; opt.appendChild(mark);
        const lab = el("span", "ask-optlabel"); lab.textContent = o.label || ""; opt.appendChild(lab);
        if (o.description) { const d = el("span", "ask-optdesc"); d.textContent = o.description; opt.appendChild(d); }
        qel.appendChild(opt);
      }
      // a free-text answer matching no option → a selected "Other" row + the verbatim words (quoted),
      // so a typed answer is never silently dropped to an empty-looking menu.
      for (const other of others) {
        const opt = el("div", "ask-opt chosen ask-other");
        const mark = el("span", "ask-mark"); mark.textContent = "●"; opt.appendChild(mark);
        const lab = el("span", "ask-optlabel"); lab.textContent = "Other"; opt.appendChild(lab);
        const d = el("span", "ask-answer-text"); d.textContent = "“" + other + "”"; opt.appendChild(d);
        qel.appendChild(opt);
      }
    }
    card.appendChild(qel);
  }
  turn.appendChild(card);
  return turn;
}

// Fallback when the kernel hasn't attached askAnswer yet: parse the posed questions from the tool input
// (JSON) and the recorded answer from the tool output (`"<q>"="<a>"` pairs) into the same block shape
// renderAsk consumes. The answer text may name an option label OR be free-text ("Other"); multi-select
// joins labels as "A, B, C". Comma-split only when a label actually matches, so a free-text answer that
// happens to contain commas isn't shredded.
function parseAskRaw(ev: Extract<ChatEvent, { kind: "tool" }>): AskAnswerBlock[] | null {
  let data: any;
  try { data = JSON.parse(ev.input); } catch { return null; }
  const qs = data && Array.isArray(data.questions) ? data.questions : null;
  if (!qs || !qs.length) return null;
  const out = ev.output || "";
  const pairs = new Map<string, string>();
  for (const m of out.matchAll(/[“"]([^”"]*)[”"]\s*=\s*[“"]([^”"]*)[”"]/g)) pairs.set(m[1].trim(), m[2]);
  const pairVals = Array.from(pairs.values());
  const answerFor = (q: any): string =>
    qs.length === 1 && pairVals.length ? pairVals[0]
      : pairs.get(String(q.question || "").trim()) ?? pairs.get(String(q.header || "").trim()) ?? "";
  return qs.map((q: any): AskAnswerBlock => {
    const opts = Array.isArray(q.options) ? q.options : [];
    const labels = opts.map((o: any) => String(o.label || "")).filter(Boolean);
    const ans = answerFor(q);
    let chosen: string[] = [];
    if (ans) {
      if (labels.includes(ans)) chosen = [ans];
      else {
        const parts = ans.split(/,\s*/).map((s) => s.trim()).filter(Boolean);
        chosen = parts.some((p) => labels.includes(p)) ? parts : [ans];   // matched labels highlight; rest → "Other"
      }
    }
    return {
      question: String(q.question || ""),
      header: q.header ? String(q.header) : undefined,
      options: opts.map((o: any) => ({ label: String(o.label || ""), description: o.description ? String(o.description) : undefined })),
      chosen,
    };
  });
}

// Claude Code's Task to-do list — a compact live checklist mirroring the terminal:
// ○ pending / ◐ in_progress / ✓ completed (done is struck through).
function renderTodo(ev: Extract<ChatEvent, { kind: "todo" }>): HTMLElement {
  const turn = el("div", "turn turn-todo");
  turn.appendChild(dot("ring"));
  const card = el("div", "todo-card");
  // FAIL LOUDLY (the user 2026-07-03): the kernel couldn't read Claude's authoritative task store, so it
  // surfaces THIS instead of quietly showing a lossy transcript-folded list that could be wrong.
  if (ev.error) {
    card.classList.add("todo-card-error");
    const head = el("div", "todo-head"); head.textContent = "To-do · unavailable";
    card.appendChild(head);
    const msg = el("div", "todo-error-msg"); msg.textContent = ev.error;
    card.appendChild(msg);
    turn.appendChild(card);
    return turn;
  }
  const done = ev.tasks.filter((t) => t.status === "completed").length;
  const head = el("div", "todo-head"); head.textContent = `To-do · ${done}/${ev.tasks.length}`;
  card.appendChild(head);
  for (const t of ev.tasks) {
    const row = el("div", "todo-item todo-" + t.status);
    const mark = el("span", "todo-mark");
    mark.textContent = t.status === "completed" ? "✓" : t.status === "in_progress" ? "◐" : "○";
    row.appendChild(mark);
    const txt = el("span", "todo-text");
    txt.textContent = t.status === "in_progress" && t.activeForm ? t.activeForm : t.subject;
    row.appendChild(txt);
    card.appendChild(row);
  }
  turn.appendChild(card);
  return turn;
}

// A context compaction → one clean teal rail marker (the user 2026-06-14): replaces the raw /compact
// stdout (leaked ANSI dim codes + hook-completion noise). renderEvent adds the rail time-marker +
// hover wiring (this turn has a .dot); in compact mode it passes through unchanged → the same marker.
// A context-compaction boundary → a NOTICE CARD in romp's system-event family (the user 2026-07-07): the same
// boxed, chip-headed, default-collapsed treatment as every other system event (agent/romp/reminder notices),
// so a compaction reads as one of them rather than a bespoke rail line. Its ONE distinction is the COMPACTION
// TEAL accent (--st-compacting-bg) — the status color it already shares with the live compacting bar and the
// statusline battery, never a new hue. The head says WHY at a glance (trigger + token win); the model's
// SUMMARY of what it kept is the collapsible body — DEFAULT COLLAPSED, expands to the whole thing (keyed fold,
// so it survives re-renders). Everything before the boundary is compacted out of the agent's context, so the
// default window opens AT this card (see lastCompactUnit); older history stays reachable on scroll-back.
function renderCompact(ev: Extract<ChatEvent, { kind: "compact" }>): HTMLElement {
  const summary = (ev.summary || "").trim();
  const uuid = ev.uuid || "";
  // A muted meta suffix on the head, when the boundary carried it: the trigger (auto vs. the user's /compact)
  // and the token win (before → after), so the head says WHY at a glance.
  const bits: string[] = [];
  if (ev.trigger === "auto") bits.push("auto");
  else if (ev.trigger === "manual") bits.push("manual");
  if (ev.preTokens) bits.push(ev.postTokens ? `${compactTokens(ev.preTokens)} → ${compactTokens(ev.postTokens)}` : `${compactTokens(ev.preTokens)} freed`);
  const head = "Context compacted" + (bits.length ? " · " + bits.join(" · ") : "");
  const body = el("div", "notice-md md");
  if (summary) { body.innerHTML = md(summary); highlight(body); }
  else { const p = el("div", "notice-plain"); p.textContent = "No summary was captured for this compaction."; body.appendChild(p); }
  // collapsible only when there's a summary to reveal; the "compacted" chip carries identity (no ✦ glyph)
  return noticeCard({ variant: "compact", chip: "compacted", head, body,
                      collapsible: !!summary, key: uuid ? "compact:" + uuid : undefined });
}

// The /clear boundary card (the user 2026-07-27): the fresh episode opens with a collapsed "Conversation
// cleared" notice — the chat twin of the timeline's film-splice seam, in the same system-event family as
// the compact divider. Progressive disclosure: the head is the one-line version; the PRE-CLEAR conversation
// itself lazy-loads into the body on FIRST expand (loadEpisode → chatEpisode), rendered read-only through
// the same per-event renderers as the live transcript, and cached per boundary uuid so the chat's per-push
// rebuilds never re-fetch or re-ask the kernel. Auto-collapsed by default, like the system card beneath it.
const episodeCache = new Map<string, { events: ChatEvent[]; truncated?: number; error?: string }>();
const episodePendingKey = new Map<string, string>();   // sid → the fold key whose fetch is in flight

function fillClearBody(body: HTMLElement, got: { events: ChatEvent[]; truncated?: number; error?: string }): void {
  while (body.firstChild) body.removeChild(body.firstChild);
  if (got.error) {
    const p = el("div", "notice-plain");
    p.textContent = got.error;
    body.appendChild(p);
    return;
  }
  if (got.truncated) {
    const n = el("div", "clear-truncated");
    n.textContent = `… ${got.truncated} earlier event${got.truncated === 1 ? "" : "s"} of the cleared conversation not shown`;
    body.appendChild(n);
  }
  const wrap = el("div", "clear-episode");
  let prevEp: number | null = null;
  for (const e of got.events) {
    try {
      const ep = eventEpoch(e);
      const prior = prevEp;   // the PREVIOUS event's epoch — chained, so the fold divides days
      if (ep != null) {       // rather than re-stamping the full date on every turn in it
        const dv = dayDividerFor(ep, prior);
        if (dv) wrap.appendChild(dv);
        prevEp = ep;
      }
      wrap.appendChild(renderEvent(e, prior));
    }
    catch { /* one malformed historical event must not blank the whole fold */ }
  }
  body.appendChild(wrap);
}

function renderClear(ev: Extract<ChatEvent, { kind: "clear" }>): HTMLElement {
  const sid = renderingSid || "";
  const key = "clear:" + (ev.uuid || sid);
  // the boundary settle's own record rides the event: the head counts the dropped cards, hover
  // names them — the drop is visible in the chat too, never only in the feed (the user 2026-07-27)
  const dropped = ev.dropped || [];
  const head = "Conversation cleared — a fresh one starts here"
    + (dropped.length ? " · " + dropped.length + " open card" + (dropped.length === 1 ? "" : "s") + " dropped with it" : "");
  const body = el("div", "clear-body");
  body.dataset.clearKey = key;
  const cached = episodeCache.get(key);
  if (cached) fillClearBody(body, cached);
  else {
    // placeholder until the first expand fetches — the loader motif (pulsing accent dots), per the
    // "show the romp loader first" rule; replaced in place when chatEpisode lands
    const p = el("div", "notice-plain clear-loading");
    p.appendChild(metaDots());
    p.appendChild(document.createTextNode(" Loading the cleared conversation…"));
    body.appendChild(p);
  }
  const turn = noticeCard({ variant: "clear", chip: "cleared", head, body, collapsible: true, key });
  if (dropped.length) turn.querySelector(".notice-head")?.setAttribute("title", "dropped: " + dropped.join(", "));
  // Fetch on FIRST expand (ride the same head click that toggles the fold — noticeCard owns the toggle):
  // one request per boundary, deduped by the pending map; re-renders hit the cache instead.
  const headEl = turn.querySelector(".notice-head");
  headEl?.addEventListener("click", () => {
    const card = turn.querySelector(".notice-card") || turn;
    if (!card.classList.contains("notice-open")) return;                  // just collapsed — nothing to load
    if (episodeCache.has(key) || episodePendingKey.get(sid) === key) return;
    episodePendingKey.set(sid, key);
    vscodeApi?.postMessage({ type: "loadEpisode", id: sid });
  });
  return turn;
}

// chatEpisode: the kernel's one-shot reply to loadEpisode — cache it under the key the expand recorded,
// then fill the (possibly still-open) card body in place, wherever it currently sits in the DOM.
function chatEpisode(m: any): void {
  const sid = String(m.id || "");
  const key = episodePendingKey.get(sid);
  if (!key) return;
  episodePendingKey.delete(sid);
  const got = { events: (m.events || []) as ChatEvent[], truncated: m.truncated || 0,
                error: m.error ? String(m.error) : undefined };
  episodeCache.set(key, got);
  document.querySelectorAll<HTMLElement>(".clear-body").forEach((b) => {
    if (b.dataset.clearKey === key) fillClearBody(b, got);
  });
}

// LIVE /clear in progress (the user 2026-07-27): an animated inline element between the /clear delivery
// and the fresh transcript landing — the loader dots motif, sibling of the reconnecting element. Without
// it that stretch showed a dead gap and then a bare "No messages yet.". Event-based off the SDK backend's
// clearing bracket; it vanishes the instant the fork lands, when the "Conversation cleared" boundary card
// (renderClear) takes over as the durable record.
function renderClearing(): HTMLElement {
  const turn = el("div", "turn turn-clearing");
  turn.appendChild(dot("ring"));
  const line = el("div", "clearing-line");
  line.appendChild(metaDots());   // the pulsing accent-blue dots — "it's romp, working"
  const txt = el("span", "clearing-text");
  txt.textContent = "Clearing conversation…";
  line.appendChild(txt);
  line.title = "starting a fresh conversation — the cleared one stays one click away, behind the divider that lands here";
  turn.appendChild(line);
  return turn;
}

// LIVE compaction in progress (the user 2026-07-06): an animated inline element in the chat flow while the
// session compacts — the SAME compressing-teal-bar motion as the statusline ctx-bar (@keyframes ctx-compress),
// styled to rhyme (via the compaction teal) with the "Context compacted" notice card (renderCompact) it
// becomes once the boundary lands.
// The kernel appends kind:"compacting" BEFORE kind:"queued", so a message sent mid-compaction stacks BELOW
// this instead of clobbering it. Event-based: it vanishes the instant the session stops compacting.
function renderCompacting(): HTMLElement {
  const turn = el("div", "turn turn-compacting");
  turn.appendChild(dot("ring"));
  const line = el("div", "compacting-inline");
  const bar = el("span", "compacting-bar");
  const fill = el("span", "compacting-bar-fill");
  // Sweep through the SAME context colormap the statusline/tab compacting bars use (the user 2026-07-07:
  // "the bar that's moving should change colour just like it does in other parts of the UI"), not a flat
  // teal — applyCompactSweep sets --cmp0..4 the @keyframes ctx-compress read, and phase-syncs the animation
  // to the wall clock so it resumes seamlessly across the chat's per-push rebuilds (fresh element each time).
  applyCompactSweep(fill, 3200);   // duration MUST match the .compacting-bar-fill @keyframes (ctx-compress 3.2s)
  bar.appendChild(fill);
  line.appendChild(bar);
  const txt = el("span", "compacting-text");
  txt.textContent = "Compacting context…";
  line.appendChild(txt);
  line.title = "compacting — compressing the conversation to free up context; any queued message sends once it finishes";
  turn.appendChild(line);
  return turn;
}

// LIVE session reconnect (the user 2026-07-06): an /effort switch has no SDK runtime control, so romp applies
// it by RECONNECTING the session (resume = the CLI re-reads the transcript) — otherwise invisible in the chat.
// While the reconnect is pending, an animated "Reloading session — applying <effort> effort…" element shows
// (the romp-accent pulsing dots, the loader motif), so the user sees the "rereading transcript" step the TUI
// narrates; it clears the instant the new client connects (kernel drops effortPending). Sibling of the
// compacting element; appended before the queued bubble.
function renderReconnecting(ev: Extract<ChatEvent, { kind: "reconnecting" }>): HTMLElement {
  const turn = el("div", "turn turn-reconnecting");
  turn.appendChild(dot("ring"));
  const line = el("div", "reconnecting-line");
  line.appendChild(metaDots());   // the same pulsing accent-blue dots as the switching-dots badge — "it's romp, working"
  const txt = el("span", "reconnecting-text");
  txt.textContent = ev.effort ? `Reloading session — applying ${ev.effort} effort…` : "Reloading session…";
  line.appendChild(txt);
  line.title = "applying the effort change — reloading the session (it re-reads the transcript); any message you send lands once it's back";
  turn.appendChild(line);
  return turn;
}

// LIVE api_retry (the user 2026-07-08): the API returned a retryable error (rate-limit / overload) and the
// CLI is backing off + retrying, so the turn stalls. This used to be visible ONLY as the amber tab border,
// with nothing in the chat ("the border says retrying but the chat shows no sign"). Now an animated element
// — the loader dots (it's mid-operation) + an AMBER "API retrying…" line (the retrying status color, so it
// reads as the SAME state the border shows) — with the live attempt count. Sibling of the compacting /
// reconnecting elements; event-based, it clears the instant output resumes (then the "Recovered after N
// retries" note lands above). Appended before the queued bubble.
function renderRetrying(ev: Extract<ChatEvent, { kind: "retrying" }>): HTMLElement {
  const turn = el("div", "turn turn-retrying");
  turn.appendChild(dot("ring"));
  const line = el("div", "retrying-line");
  line.appendChild(metaDots());
  const txt = el("span", "retrying-text");
  const info = ev.info || {};
  const n = info.attempt || ev.retries || 0;
  let head = n > 1 ? `API retrying — attempt ${n}` : "API retrying";
  if (n > 1 && info.max) head += ` of ${info.max}`;
  txt.textContent = head;
  line.appendChild(txt);
  // LIVE countdown to the next attempt (the user 2026-07-24). It used to re-derive only on re-render, so it
  // sat frozen at "next try in ~3s" for the whole backoff — a number that never moves reads as broken, and
  // says nothing about whether anything is still happening. Now the epoch rides a data attr and
  // retryingTick() rewrites this span every second, exactly like the API-error card's countdown. `~` is
  // dropped: a ticking number is precise enough to speak plainly.
  if (info.retryAt) {
    const cd = el("span", "retrying-countdown");
    cd.dataset.retryAt = String(info.retryAt);
    cd.textContent = retryingCountdownText(info.retryAt);
    line.appendChild(cd);
  }
  // Stop control (the user 2026-07-24, who wanted the API-error card's stop/resume reach here too). The
  // storm lives INSIDE the CLI's own backoff — the SDK exposes no API to abort or accelerate it (verified
  // against the installed claude_agent_sdk: no max_retries, no retry control, only interrupt()) — so the one
  // honest lever is to INTERRUPT the stalled turn. That is a real mechanism, not a placebo: it cuts the turn
  // AND marks the thread retry-suppressed, so romp's own auto-retry loop won't relapse into the storm
  // afterwards. Once stopped the session lands blocked/interrupted, where the existing card's "Retry now" +
  // resume controls take over — so stop → manual retry → resume is a closed loop across the two states.
  // Delegated via data-act (never a per-render listener): the transcript tail rebuilds on every kernel push,
  // and a rebuilt node eats a mid-press click — the "had to click it several times" bug (CLAUDE.md).
  const stop = el("button", "retrying-stop") as HTMLButtonElement;
  stop.dataset.act = "stopRetrying";
  stop.textContent = "Stop retrying";
  stop.title = "interrupt this stalled turn and stop the backoff — romp's auto-retry stays off for this session until you send a message";
  line.appendChild(stop);
  line.title = "the API returned a retryable error (rate-limit / overload); the CLI is backing off and retrying — any message you send lands once it recovers";
  turn.appendChild(line);
  // The error behind the backoff, when the payload names it — status code and/or message on its own muted
  // line (same size as the retrying text: one size per information type), full message in the tooltip.
  if (info.status || info.error || info.networkDown) {
    const err = el("div", "retrying-err");
    const status = info.status ? `HTTP ${info.status}` : "";
    const msg = (info.error || "").trim();
    err.textContent = [status, msg, apiErrorReason(info)].filter(Boolean).join(" — ");
    // The request id belongs in the tooltip, not the line: it is the one thing worth quoting to support and
    // the one thing nobody reads at a glance (progressive disclosure — gist on the line, mechanics a hover away).
    err.title = [msg, info.requestId ? `request ${info.requestId}` : ""].filter(Boolean).join("\n") || "";
    turn.appendChild(err);
  }
  return turn;
}


// The next-attempt countdown's text. Past due (the attempt is firing, or the CLI slipped its own estimate)
// reads "retrying now…" rather than a stuck "0s" or a negative — the wait is over either way, and the loader
// dots beside it already say something is in flight.
function retryingCountdownText(retryAt: number): string {
  const s = Math.ceil(retryAt - Date.now() / 1000);
  return s > 0 ? `— next try in ${s}s…` : "— retrying now…";
}

// Tick every .retrying-countdown once a second (driven by the same 1s interval as the API-error countdown —
// one timer for both, no second scheduler). Each span carries its own retryAt epoch, so this is a pure
// re-read of the authoritative number the CLI reported: no client-side drift, and a card whose event has
// gone stale still counts down to the moment it was told.
function retryingTick(): void {
  for (const n of Array.from(document.querySelectorAll(".retrying-countdown"))) {
    const cd = n as HTMLElement;
    const at = Number(cd.dataset.retryAt);
    if (at) cd.textContent = retryingCountdownText(at);
  }
}

// The persistent counterpart of renderRetrying (the user 2026-07-08): once a stalled api_retry turn resumes
// output, a muted, rail-anchored "Recovered after N retries" note is left where the recovery happened, so the
// history records that the turn weathered an API storm. Static (no animation, no expand) — a one-line marker.
function renderRetried(ev: Extract<ChatEvent, { kind: "retried" }>): HTMLElement {
  const turn = el("div", "turn turn-retried");
  turn.appendChild(dot("ring"));
  const line = el("div", "retried-line");
  const n = ev.retries || 0;
  const txt = el("span", "retried-text");
  txt.textContent = `Recovered after ${n} ${n === 1 ? "retry" : "retries"}`;
  line.appendChild(txt);
  line.title = "the API returned a retryable error (rate-limit / overload); the CLI backed off and retried, then output resumed";
  turn.appendChild(line);
  return turn;
}

// The FAILED counterpart of renderRetried (the user 2026-07-25): the CLI exhausted its retry attempts and
// the turn died. Same slim rail-anchored shape, but in the blocked/red voice — this storm produced nothing,
// and the note must never read like a recovery. The error text itself follows as the apiErrorNote card.
function renderRetryGaveUp(ev: Extract<ChatEvent, { kind: "retryGaveUp" }>): HTMLElement {
  const turn = el("div", "turn turn-retried turn-gaveup");
  turn.appendChild(dot("red"));
  const line = el("div", "retried-line");
  const n = ev.retries || 0;
  const txt = el("span", "retried-text gaveup-text");
  txt.textContent = `API errors — gave up after ${n} ${n === 1 ? "retry" : "retries"}`;
  line.appendChild(txt);
  line.title = "the API kept returning retryable errors" + (ev.errorKind ? ` (${ev.errorKind})` : "")
    + "; the CLI backed off and retried until its attempts ran out, then ended the turn with no output";
  turn.appendChild(line);
  return turn;
}

// The durable red card for a turn that DIED on an API error — the transcript's own error record, worn as
// error chrome instead of an agent bubble (the user 2026-07-25: "I'd like to see a very visible error").
// Reuses the live card's .apierror-* dress so the two read as the same event; carries no buttons — the
// LIVE card (renderApiError, swapped in by the kernel while this record still blocks the session) owns
// Retry/auto-retry, and once the session has moved on there is nothing left to retry here.
function renderApiErrorNote(ev: Extract<ChatEvent, { kind: "apiErrorNote" }>): HTMLElement {
  const turn = el("div", "turn turn-apierror");
  turn.appendChild(dot("red"));
  const card = el("div", "apierror-card apierror-note");
  const head = el("div", "apierror-head");
  const badge = el("span", "apierror-badge");
  badge.textContent = ev.status ? `API error · ${ev.status}` : "API error";
  head.appendChild(badge);
  card.appendChild(head);
  const body = el("div", "apierror-body");
  body.textContent = ev.md || "The turn stopped on an API error.";
  card.appendChild(body);
  turn.title = "this turn died on an API error — whatever it was going to say was never produced";
  turn.appendChild(card);
  return turn;
}

// A durable, rail-anchored "effort set to X" note at the moment an /effort change took effect (the user
// 2026-07-16). Same slim treatment as renderRetried — the reconnect leaves no transcript atom, so this is the
// only lasting record of when reasoning effort changed (the synthesized /effort chip self-destructs on the
// next message). Kernel writes it at the reconnect landing, so its timestamp IS the apply moment.
function renderEffortApplied(ev: Extract<ChatEvent, { kind: "effortApplied" }>): HTMLElement {
  const turn = el("div", "turn turn-effort");
  turn.appendChild(dot("ring"));
  const line = el("div", "effort-line");
  const txt = el("span", "effort-text");
  txt.textContent = `effort set to ${ev.effort}`;
  line.appendChild(txt);
  line.title = "reasoning effort is a connect-time setting; the session reconnected to apply it, and this marks when the new level took effect";
  turn.appendChild(line);
  return turn;
}

// The durable "safeguards flagged → switched model" note (the user 2026-08-03: a mid-turn model swap
// must be apparent in the chat, never silent). Slim rail line in the warning voice, placed where the
// retry started — i.e. just above the fallback model's reply. The CLI's full explanation (why the
// safeguards fired, the /feedback pointer) expands on click; fold state survives re-renders via the
// record's uuid key.
function renderModelFallback(ev: Extract<ChatEvent, { kind: "modelFallback" }>): HTMLElement {
  const turn = el("div", "turn turn-retried turn-modelswap");
  turn.appendChild(dot("ring"));
  const line = el("div", "retried-line modelswap-line");
  const txt = el("span", "retried-text modelswap-text");
  const from = ev.from ? prettyModel(ev.from) : "";
  const to = ev.to ? prettyModel(ev.to) : "a fallback model";
  txt.textContent = `${from || "The model"}'s safeguards flagged this message · switched to ${to}`;
  line.appendChild(txt);
  turn.appendChild(line);
  if (ev.md) {
    const body = el("div", "modelswap-body");
    body.textContent = ev.md;
    const key = ev.uuid ? "mswap:" + ev.uuid : undefined;
    applyFold(body, "expanded", key);
    line.title = "click for the full notice";
    line.addEventListener("click", () => rememberFold(body, "expanded", key));
    turn.appendChild(body);
  }
  return turn;
}

// Compact a token count for the compaction divider: 795232 → "795k", 6514 → "6.5k", 900 → "900".
function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return (k < 10 ? k.toFixed(1).replace(/\.0$/, "") : Math.round(k)) + "k";
}

// A compact "Follow-up" header above a message that resumed a goal (the user 2026-06-27): a ↩ glyph + the
// goal title (when known), muted, so a follow-up reads as such WITHOUT dumping romp's goal-context quote into
// the bubble. Shared by landed user turns + pending queued messages so the two render consistently.
// When the event carries the stripped quote (ctx), the header is CLICK-EXPANDABLE (the user 2026-07-01): a
// ▸ disclosure toggles a muted block showing exactly the goal context that rode along with the message — the
// strip is display-only, and this is where the hidden part can be audited. Expansion survives the chat's
// re-renders via fuExpanded (keyed by the turn's uuid / queue slot), NOT DOM state that a rebuild would lose.
const fuExpanded = new Set<string>();
function followUpHeader(goal?: string, ctx?: string, key?: string): HTMLElement {
  const h = el("div", "followup-tag");
  const k = key || "";
  if (ctx && k) {
    const tri = el("span", "followup-tri");
    tri.textContent = fuExpanded.has(k) ? "▾" : "▸";
    h.appendChild(tri);
  }
  const lbl = el("span", "followup-lbl"); lbl.textContent = "↩ Follow-up"; h.appendChild(lbl);
  if (goal) { const g = el("span", "followup-goal"); g.textContent = goal; h.appendChild(g); }
  if (!ctx || !k) return h;
  const wrap = el("div", "followup-wrap");
  wrap.appendChild(h);
  const box = el("div", "followup-ctx");
  box.textContent = ctx;
  box.style.display = fuExpanded.has(k) ? "" : "none";
  wrap.appendChild(box);
  h.classList.add("followup-expandable");
  h.title = "show the goal context romp sent along with this message";
  h.onclick = () => {
    const open = !fuExpanded.has(k);
    if (open) fuExpanded.add(k); else fuExpanded.delete(k);
    box.style.display = open ? "" : "none";
    const tri = h.querySelector(".followup-tri"); if (tri) tri.textContent = open ? "▾" : "▸";
  };
  return wrap;
}

// A wireframe hourglass in the romp accent blue, drawn in the SAME line-icon style as the feed/mail toggle
// icons (ctxIcon) — used on the queued-messages header instead of an hourglass emoji, which clashed with the
// app's stroked-icon look (the user 2026-06-27). One continuous path: top bar → right wall to the center pinch →
// bottom bar → left wall back up.
function hourglassIcon(): HTMLElement {
  const span = el("span", "queued-icon");
  span.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" '
    + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M4 3 H12 L8 8 L12 13 H4 L8 8 Z"/></svg>';
  return span;
}

// A leading "/cmd [args]" is a command keyword, not prose. "/cmd" must be a WHOLE leading token (space or end
// after it) so a "/Users/…" path is never chipped.
const SLASH_CMD_RE = /^(\/[A-Za-z][\w-]*)(?=\s|$)([\s\S]*)$/;

// Render `text` into `bubble` as a monospace command chip + plain args when it's a slash command; returns true
// if it did. Shared by landed user turns AND pending queued messages so a command reads the same either way —
// a queued /compact should look like a COMMAND, not a generic "message" (the user 2026-07-01).
function renderSlashCmd(bubble: HTMLElement, text: string): boolean {
  const m = text.match(SLASH_CMD_RE);
  if (!m) return false;
  const chip = el("span", "slash-cmd-chip"); chip.textContent = m[1];
  bubble.appendChild(chip);
  const rest = m[2].replace(/^\s+/, "");
  if (rest) { const args = el("span", "slash-cmd-args"); args.textContent = rest; bubble.appendChild(args); }
  return true;
}

// Pending queued messages — the user's inputs submitted while the session was still working, not yet
// processed. Rendered at the bottom (closest to the composer) as faint right-aligned "you" bubbles, the SAME
// way a landed message renders (markdown, follow-ups cleaned of the romp goal-context + markers, with the
// compact Follow-up header) so a pending message looks like what it'll become (the user 2026-06-27). A queued
// slash command renders as a command chip, and the header noun matches (a /compact is a queued "command", not
// a "message" — the user 2026-07-01).
// The "N queued …" count. Shared with the ✕'s immediate recount so the number on screen can never drift
// from the bubbles under it while the cancel is in flight.
function queuedCountText(n: number, nCmd: number): string {
  const noun = nCmd === n ? "command" : nCmd === 0 ? "message" : "item";
  return `${n} queued ${noun}${n === 1 ? "" : "s"}`;
}

// Keep a queued group honest the instant a ✕ takes one of its bubbles out: the LAST bubble going takes the
// whole group with it, otherwise the header recounts. Without this the header outlived its bubbles and sat
// there alone still claiming a message the user had just cancelled (the user 2026-07-24).
function reflowQueuedGroup(turn: HTMLElement): void {
  const bubbles = Array.from(turn.querySelectorAll(".queued-bubble"));
  if (!bubbles.length) { turn.remove(); return; }
  const label = turn.querySelector(".queued-count") as HTMLElement | null;
  if (!label) return;                                     // a bare (optimistic) group has no header to fix
  const nCmd = bubbles.filter((b) => b.querySelector(".slash-cmd-chip")).length;
  label.textContent = queuedCountText(bubbles.length, nCmd) + (label.dataset.why || "");
}

function renderQueued(ev: Extract<ChatEvent, { kind: "queued" }>): HTMLElement {
  const turn = el("div", "turn turn-queued");
  // A BARE group is romp's own optimistic echo with nothing else known-queued: it gets the dashed bubble but
  // NO header, because "N queued messages" is a claim we can't back for a send the session hasn't confirmed
  // (the user 2026-07-16). Merged into a real queued group, the header returns and counts ours in — there the
  // queueing IS established, so assuming this one joins it is honest.
  if (!ev.bare) {
    const n = ev.texts.length;
    const nCmd = ev.texts.filter((t) => SLASH_CMD_RE.test(t.md)).length;
    const head = el("div", "queued-head");
    head.appendChild(hourglassIcon());
    // While a question is pending, say WHAT it's waiting on: a message you'd already written when the picker
    // arrived is sent as a message, not eaten as the answer, and the session can't take it until the question
    // is resolved — so the queue is really "after you answer" (the user 2026-07-16).
    const pendingAsk = !!activeId && liveAsks.has(activeId);
    // A queue held by the ACCOUNT (usage limit / spend cap) says so, and outranks the pending-ask note:
    // that queue isn't moving whatever you answer. A stack of bubbles sitting for hours with no stated
    // cause reads as romp having eaten the messages (the user 2026-07-24). The countdown rides the API's
    // own reset stamp — when it didn't report one (a spend cap never does), say the reason and no more.
    const askNote = (pendingAsk ? " · sends after you answer" : "");
    const held = ev.held;
    const why = held
      ? ` · ${held.what}` + (held.resetsAt ? ` · in ${fmtReset(held.resetsAt, Math.floor(Date.now() / 1000))}` : "")
      : askNote;
    const label = el("span", "queued-count");
    label.dataset.why = why;      // the ✕'s recount rewrites the count and keeps this suffix as-is
    label.textContent = queuedCountText(n, nCmd) + why;
    // `detail` is the CLI's OWN sentence about the limit (it carries the reset time as a wall clock, which
    // is why that flavor has no epoch to count down to). One level deeper on hover, per the compact-by-
    // default rule — the head keeps its one-line reason.
    if (held?.detail) label.title = held.detail;
    head.appendChild(label);
    turn.appendChild(head);
  }
  for (const t of ev.texts) {
    if (t.followUp) turn.appendChild(followUpHeader(t.goal, t.fuCtx, t.idx !== undefined ? "q:" + t.idx : undefined));
    const bubble = el("div", "queued-bubble md" + (t.cancelable ? " cancelable" : ""));
    // one phrase separating OUR unconfirmed echo from a real queued message, which the session has accepted
    // and is holding (the user 2026-07-16)
    if (t.optimistic) bubble.title = "sent just now — romp hasn't confirmed the session has it yet";
    // a queued entry with NO ✕ (the user 2026-07-20): the queue lives inside the session's own CLI —
    // there is no recall — so instead of a cancel that would only ever say "too late", the tooltip says
    // where the message actually is. (SDK mid-turn forwards and every tmux queued message land here.)
    else if (!t.cancelable && t.idx !== undefined)
      bubble.title = "queued in the session — it can't be recalled, and joins the conversation at the session's next step";
    const isCmd = renderSlashCmd(bubble, t.md);
    if (!isCmd) bubble.innerHTML = md(t.md);
    // CANCELABLE — an explicit ✕ on the bubble (the user 2026-07-08; the old whole-bubble click was
    // undiscoverable AND hung on a node every push rebuilds, so mid-press rebuilds silently ate the
    // click). The ✕ carries data-act="qx" → the ONE document.body delegate (click-safe per CLAUDE.md);
    // a MESSAGE returns to the composer to re-edit, a slash COMMAND just cancels. Covers both queues:
    // the backend's own (idx; SDK only — tmux's queue lives inside Claude Code, no recall) and ops
    // PARKED during compaction/model switches (park; romp-owned on every backend).
    if (t.cancelable && (t.idx !== undefined || t.park !== undefined)) {
      const x = el("button", "queued-x");
      x.textContent = "✕";
      x.title = isCmd ? "cancel this queued command" : "cancel this queued message and move it back to the composer";
      x.dataset.act = "qx";
      if (t.idx !== undefined) x.dataset.qidx = String(t.idx);
      if (t.park !== undefined) x.dataset.qpark = String(t.park);
      if (isCmd) x.dataset.qcmd = "1";
      (x as any)._qmd = t.md;   // the bubble's body — the kernel's drift guard + the composer restore read it
      bubble.appendChild(x);
    }
    turn.appendChild(bubble);
  }
  return turn;
}

// Put a canceled queued message back into the composer so the user can edit/re-send it. Appends on a new
// line if there's already a draft, else fills it; focuses + caret to end + fires `input` so the autosize
// + send-button enable react (the user 2026-06-27).
function restoreToComposer(text: string) {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta) return;
  ta.value = ta.value.trim() ? ta.value.replace(/\s*$/, "") + "\n" + text : text;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
}

// The composer state around each ✕-click's optimistic restore, keyed `sid + " " + md`, so a FAILED
// cancel (kernel cancelResult ok:false — the message had already reached the session) can put the
// composer back exactly as it was IF the user hasn't touched it since (the user 2026-07-20: the
// restored copy of an un-recallable message is a double-send waiting to happen). An edited draft is
// never touched — the toast alone covers it.
const pendingCancelRestores = new Map<string, { before: string; after: string }>();

// The turn stopped on an API error — the session is BLOCKED until retried. A red-dot card at the bottom
// (so it stands out, the user 2026-06-16) carrying the error text + a red "API error" badge and a Retry
// button that pastes "retry" into the session to resume the stalled turn.
function renderApiError(ev: Extract<ChatEvent, { kind: "apiError" }>): HTMLElement {
  const turn = el("div", "turn turn-apierror");
  turn.appendChild(dot("red"));
  const card = el("div", "apierror-card");
  const head = el("div", "apierror-head");
  const badge = el("span", "apierror-badge");
  badge.textContent = ev.status ? `API error · ${ev.status}` : "API error";
  head.appendChild(badge);
  // Live countdown to the next AUTO-retry — apiRetryTick() (below) updates this text every second.
  const countdown = el("span", "apierror-countdown");
  countdown.textContent = "retrying soon…";
  head.appendChild(countdown);
  // A SPEND-CAP block gets no Retry (the user 2026-07-16, mirroring the feed card's 2026-07-14 call):
  // retrying can't lift a billing cap, and on a tmux session it's worse than useless — the CLI is parked
  // on an interactive menu that eats the injected "retry" as navigation keystrokes. There the real
  // unblock is dismissing that dialog, so the tmux card offers exactly that (the kernel verifies the
  // menu is up, then sends Esc — cancel, never a billing change). An SDK spend-cap card names the fix
  // (raise the cap) with no dead button at all.
  const st = activeId ? sessions.get(activeId)?.status : undefined;
  // A spent MODEL allowance gets no Retry either (the user 2026-08-01): "retry" re-fails until the model
  // changes or its own window resets, so the card names the fix instead of offering a button that cannot work.
  // A dead CREDENTIAL is the same shape (the user 2026-08-08, per-session auth): retrying re-presents the
  // broken login/key forever — the fix is /login, the key, or switching which one the session bills.
  const spendCap = !!st?.apiSpendLimit || !!st?.apiModelLimit || !!st?.apiAuthErr;
  if (!spendCap) {
    const retry = el("button", "apierror-retry") as HTMLButtonElement;
    retry.textContent = "Retry now";
    retry.title = "send “retry” into this session right now (also resets the auto-retry countdown)";
    retry.addEventListener("click", () => {
      // manual:true → an explicit override that fires even when auto-retry is paused/suppressed for this thread
      // (the kernel gate is for the auto-loop only); without it "Retry now" was a dead no-op on a suppressed
      // session (the user 2026-07-06). Acknowledge the click AT ONCE — disable + "Retrying…" — so it never
      // reads as unresponsive; the next render (a fresh error card, or the turn resuming) restores it.
      if (vscodeApi) vscodeApi.postMessage({ type: "apiRetry", id: activeId, manual: true });
      if (activeId) apiRetryNext.set(activeId, Date.now() + API_RETRY_MS);   // restart the countdown
      retry.disabled = true;
      retry.textContent = "Retrying…";
      setTimeout(() => { if (retry.isConnected) { retry.disabled = false; retry.textContent = "Retry now"; } }, 2500);
    });
    head.appendChild(retry);
  } else if (st?.backend === "tmux") {
    const dismiss = el("button", "apierror-retry") as HTMLButtonElement;   // same button chrome, different verb
    dismiss.textContent = "Dismiss dialog";
    dismiss.title = "the terminal is showing the spend-limit menu — send Esc to close it (cancels; changes no billing setting)";
    dismiss.addEventListener("click", () => {
      if (vscodeApi) vscodeApi.postMessage({ type: "dismissDialog", id: activeId });
      dismiss.disabled = true;
      dismiss.textContent = "Dismissing…";
      setTimeout(() => { if (dismiss.isConnected) { dismiss.disabled = false; dismiss.textContent = "Dismiss dialog"; } }, 2500);
    });
    head.appendChild(dismiss);
  }
  // Global auto-retry pause (the user 2026-06-30) — no per-session off-switch. "Retry now" + sending a message still work.
  const paused = globalRetryPaused;
  // Per-thread suppression (the user 2026-07-06): the user interrupted THIS thread's storm → its auto-retry is
  // held off until a successful turn re-arms it. Distinct from the global pause; "Retry now" + a message still work.
  const suppressed = activeId ? !!sessions.get(activeId)?.status.retrySuppressed : false;
  if (spendCap) countdown.textContent = "spend limit reached — raise it at claude.ai/settings/usage";   // never "retrying soon…": the tick skips spend-capped threads
  else if (paused) countdown.textContent = retryPausedText();   // a usage-limit pause counts down to the window reset
  else if (suppressed) countdown.textContent = "auto-retry stopped for this session — send a message to resume";
  const stop = el("button", "apierror-stop") as HTMLButtonElement;
  stop.textContent = paused ? "Resume all auto-retries" : "Stop all auto-retries";
  stop.title = paused ? "resume auto-retrying globally" : "stop the auto-retry loop for all errors globally";
  stop.addEventListener("click", () => {
    globalRetryPaused = !globalRetryPaused;
    if (vscodeApi) vscodeApi.postMessage({ type: "setGlobalRetryPaused", value: globalRetryPaused });
    
    // Update visuals immediately on all errors
    for (const btn of Array.from(document.querySelectorAll(".apierror-stop"))) {
      btn.textContent = globalRetryPaused ? "Resume all auto-retries" : "Stop all auto-retries";
      (btn as HTMLElement).title = globalRetryPaused ? "resume auto-retrying globally" : "stop the auto-retry loop for all errors globally";
    }
    for (const cd of Array.from(document.querySelectorAll(".apierror-countdown"))) {
      cd.textContent = globalRetryPaused ? retryPausedText() : "retrying soon…";
    }
  });
  head.appendChild(stop);
  card.appendChild(head);
  const body = el("div", "apierror-body");
  body.textContent = ev.text || "The session stopped on an API error.";
  card.appendChild(body);
  turn.appendChild(card);
  return turn;
}

// ── API-error auto-retry ──────────────────────────────────────────────────────────────────────────
// While a session sits BLOCKED on an API error (status.state === "blocked"), retry it every 10s until it
// recovers (the kernel stops marking it blocked → its timer is dropped). Client-side, self-cancelling, and
// covers EVERY blocked session (not just the visible tab); the active session's card shows a live
// countdown. The API may be down, so this deliberately doesn't depend on the summary/caption pipeline.
const API_RETRY_MS = 10_000;
const apiRetryNext = new Map<string, number>();   // sid -> epoch ms of its next auto-retry
let globalRetryPaused = false;                    // persisted via host globalRetryPaused push
let globalRetryResumeAt: number | null = null;    // epoch SECONDS the limiting usage window resets (kernel resumeAt) — null for a manual pause
let globalRetryReason = "";                       // "spend" when a monthly spend cap engaged the pause — no reset to count down to (the user 2026-07-14)

// The paused line names WHY/WHEN retrying resumes: a RATE-window pause carries the window's reset time so the
// card counts down (the user 2026-07-13); a monthly SPEND cap has no reset — it lifts only when you raise it, so
// the card says so instead of a countdown (the user 2026-07-14); a manual Stop is the plain "off" label.
function retryPausedText(): string {
  if (globalRetryReason === "spend") return "monthly spend limit reached — raise it at claude.ai/settings/usage";
  if (globalRetryResumeAt) {
    const dt = Math.max(0, Math.ceil(globalRetryResumeAt - Date.now() / 1000));
    const hm = new Date(globalRetryResumeAt * 1000).toTimeString().slice(0, 5);
    return `usage limit — retrying at ${hm} (in ${durLabel(dt)})`;
  }
  return "auto-retry off (global)";
}

function apiRetryTick(): void {
  const now = Date.now();
  if (!globalRetryPaused) {                       // user/limit stopped retrying globally → schedule nothing
    const blocked = new Set<string>();
    // A thread the user interrupted (status.retrySuppressed) is left OUT of the retry set — same as a recovered
    // one: romp won't re-fire "retry" into it until a successful turn re-arms it (the user 2026-07-06). A monthly
    // spend cap (apiSpendLimit) is left out too (the user 2026-07-14): retrying can't fix a billing cap, so it's
    // never auto-retried — the card asks you to raise it instead (the global pause it engages also gates this).
    // A spent MODEL allowance (apiModelLimit) is out for the same reason: every retry re-fails until the
    // user switches model or tops up, and the card says exactly that (the user 2026-08-01).
    sessions.forEach((s, id) => { if (s.status.state === "blocked" && !s.status.retrySuppressed && !s.status.apiSpendLimit && !s.status.apiModelLimit && !s.status.apiAuthErr) blocked.add(id); });
    apiRetryNext.forEach((_, id) => { if (!blocked.has(id)) apiRetryNext.delete(id); });   // recovered / suppressed → stop
    blocked.forEach((id) => {
      // The kernel owns the cadence now (the user 2026-07-29): it backs each outage's attempts off up to
      // half an hour, and publishes when the next may fire. While that deadline stands, this tick does
      // not ask — asking anyway would be harmless (the kernel refuses) but it would also mean every open
      // dashboard hammering the gate, and the countdown below would be describing a schedule that isn't
      // the real one. With no deadline published (a fresh block, an older kernel) the local 10s tick
      // stands in, so the first attempt is still prompt.
      const kernelNext = sessions.get(id)?.status.retryNextAt;
      if (kernelNext) {
        apiRetryNext.set(id, kernelNext * 1000);
        if (now < kernelNext * 1000) return;
      } else if (!apiRetryNext.has(id)) {
        apiRetryNext.set(id, now + API_RETRY_MS);
      }
      if (now >= (apiRetryNext.get(id) as number)) {
        if (vscodeApi) vscodeApi.postMessage({ type: "apiRetry", id });
        apiRetryNext.set(id, now + API_RETRY_MS);                                          // reset the countdown
      }
    });
  }
  // live countdown on the NEWEST error card (the live one — older cards in the transcript are settled
  // history and stay static): "retrying in Ns", or during a usage-limit pause the reset-time countdown
  // (the user 2026-07-13). Ticks every second even while paused, so the paused line stays live.
  const cds = document.querySelectorAll(".apierror-countdown");
  const cd = cds.length ? (cds[cds.length - 1] as HTMLElement) : null;
  if (cd) {
    const active = activeId ? sessions.get(activeId) : null;
    if (globalRetryPaused) {
      cd.textContent = retryPausedText();
    } else if (active?.status.retrySuppressed) {
      cd.textContent = "auto-retry stopped for this session — send a message to resume";
    } else {
      const at = activeId ? apiRetryNext.get(activeId) : undefined;
      const tries = active?.status.retryTries || 0;
      if (!at) { cd.textContent = "retrying soon…"; } else {
        const left = Math.max(0, Math.ceil((at - now) / 1000));
        // "in 1750s" is not a readable wait; past a minute it reads in minutes, and the attempt count
        // explains why the gap has grown (each failure steps the backoff up) rather than looking stuck
        const when = left >= 90 ? `${Math.round(left / 60)}m` : `${left}s`;
        cd.textContent = `retrying in ${when}` + (tries > 1 ? ` · ${tries} tries so far` : "");
      }
    }
  }
}
setInterval(() => { apiRetryTick(); retryingTick(); }, 1000);   // ONE 1s timer drives both countdowns

function renderTool(ev: Extract<ChatEvent, { kind: "tool" }>): HTMLElement {
  if (ev.name === "AskUserQuestion") { const a = renderAsk(ev); if (a) return a; }
  const turn = el("div", "turn turn-tool" + (ev.isError ? " tool-err" : ""));
  // A still-RUNNING subagent (Task/Agent dispatched, no report back yet) gets a solid amber WORKING dot instead
  // of the green ✓ — so a dispatched-but-unfinished agent reads as "still going", not done (the user 2026-06-24,
  // mirroring the TUI's clearer running/done split). Other in-flight tools resolve too fast to bother.
  const agentRunning = (ev.name === "Task" || ev.name === "Agent") && !ev.output && !ev.isError;
  const d = dot(ev.isError ? "ring" : agentRunning ? "working" : "green");
  if (ev.isError) d.classList.add("err");
  turn.appendChild(d);

  const head = el("div", "tool-head");
  const name = el("span", "tool-name"); name.textContent = ev.name;
  head.appendChild(name);
  if (ev.file) head.appendChild(fileLink(ev.file));
  else if (ev.desc) { const c = el("span", "tool-desc"); c.textContent = ev.desc; head.appendChild(c); }

  const ack = ACK_TOOLS.has(ev.name);
  turn.appendChild(head);

  const fkey = ev.uuid ? "tool:" + ev.uuid : undefined;   // persist this tool's fold across re-renders
  if (ev.isError) {
    // FAILED tool → collapse to ONE line like the successful ones, kept RED; click to expand the error (the
    // user 2026-06-22). Was an always-shown ~300px io-clamp block; now it folds onto the head behind a red
    // "error" toggle, the IN/OUT hanging below. The red ✗ rail dot + red tool name (.tool-err) keep it loud.
    if (ev.input || ev.output) {
      const io = el("div", "tool-io tool-io-fold");
      if (ev.input) io.appendChild(ioRow("IN", ev.input, true));
      if (ev.output) io.appendChild(ioRow("OUT", ev.output, true));
      const n = ev.output ? countLines(ev.output) : 0;
      inlineFold(head, turn, n ? `error · ${n} line${n === 1 ? "" : "s"}` : "error", io, fkey);
    }
  } else if (ev.diffRows?.length || ev.diff) {
    // Edit/MultiEdit: the red/green diff hangs below the head, hidden, with a line-number gutter (the user
    // 2026-06-29). PREFER ev.diffRows — REAL file line numbers + context, from Claude Code's structuredPatch
    // (kernel _patch_rows). Fall back to numberDiff(ev.diff), a relative gutter, for older records with no
    // structured patch. Styled like a diff viewer: old#/new# columns, +/- coloring, faint @@ hunk headers.
    const rows: DiffRow[] = ev.diffRows?.length ? ev.diffRows : numberDiff(ev.diff || "");
    const add = rows.filter((r) => r.sign === "+").length;
    const del = rows.filter((r) => r.sign === "-").length;
    const pre = el("pre", "io-pre fold-pre diff-fold");
    for (const r of rows) {
      const cls = r.sign === "+" ? "diff-add" : r.sign === "-" ? "diff-del" : r.sign === "@" ? "diff-hunk" : "diff-ctx";
      const row = el("div", "diff-row " + cls);
      const og = el("span", "diff-gut diff-gut-old"); og.textContent = r.oldNo == null ? "" : String(r.oldNo);
      const ng = el("span", "diff-gut diff-gut-new"); ng.textContent = r.newNo == null ? "" : String(r.newNo);
      const sign = el("span", "diff-sign"); sign.textContent = r.sign === " " || r.sign === "@" ? "" : r.sign;
      const txt = el("span", "diff-code"); txt.textContent = r.text;
      row.append(og, ng, sign, txt);
      pre.appendChild(row);
    }
    inlineFold(head, turn, `+${add} −${del}`, pre, fkey);
  } else if (ev.name === "Read") {
    if (ev.output) inlineFold(head, turn, `${countLines(ev.output)} lines`, preEl(ev.output), fkey);
  } else if (ev.name === "Skill") {
    // A Skill invocation (the user 2026-07-08): the head names the skill, and the skill's INSTRUCTIONS
    // (ev.skillMd, kernel-joined) are the fold body — DEFAULT COLLAPSED like every tool body. They used
    // to render as a separate fully-expanded note box for the whole live turn, then vanish on landing
    // (the fold only ever showed the one-line "Launching skill: X" result).
    let skillName = "";
    try { const o = JSON.parse(ev.input); if (o && typeof o.skill === "string") skillName = o.skill + (o.args ? " " + o.args : ""); } catch { /* truncated JSON → head stays bare */ }
    if (skillName && !ev.desc) { const c = el("span", "tool-desc"); c.textContent = skillName; head.appendChild(c); }
    if (ev.skillMd) {
      const box = el("div", "agent-report md"); box.innerHTML = md(ev.skillMd); highlight(box);
      inlineFold(head, turn, `skill · ${countLines(ev.skillMd)} lines`, box, fkey);
    } else if (ev.output) {
      // an older record with no joined content — keep the result reachable as before
      inlineFold(head, turn, `${countLines(ev.output)} line${countLines(ev.output) === 1 ? "" : "s"}`, preEl(ev.output), fkey);
    }
  } else if (!ack && (ev.input || ev.output)) {
    const signal = ev.name === "Task" || ev.name === "Agent";
    if (signal) {
      // Subagent (Task/Agent) = a delegated mini-conversation, disclosed PROGRESSIVELY (the user
      // 2026-07-17: default compact, click to go deeper — everywhere). Level 0 is ONE head row (Task +
      // its description, the amber/green rail dot carrying run-state); level 1 (the head's inline fold)
      // reveals the PROMPT and REPORT as their own collapsed caret boxes; level 2 opens either box —
      // each markdown-rendered (the user 2026-07-08; the prompt is the prompt field, not the tool JSON).
      // Unlike the pre-07-08 head toggle this reveals fold LABELS, not the prompt itself, so nothing
      // renders twice.
      const akey = fkey ? fkey + ":agent" : undefined;
      const halves = el("div", "agent-folds");
      if (ev.input) {
        let promptText = ev.input;   // ev.input is the tool's full JSON; show just the prompt the agent was given
        try { const o = JSON.parse(ev.input); if (o && typeof o.prompt === "string") promptText = o.prompt; } catch { /* truncated JSON → show raw */ }
        const box = el("div", "agent-report md"); box.innerHTML = md(promptText); highlight(box);
        halves.appendChild(foldable("prompt", box, akey ? akey + ":prompt" : undefined));
      }
      if (ev.output) {
        // the report is the meatier half → its line count rides the fold label (else just "report")
        const box = el("div", "agent-report md"); box.innerHTML = md(ev.output); highlight(box);
        halves.appendChild(foldable(`report · ${countLines(ev.output)} lines`, box, akey ? akey + ":report" : undefined));
      }
      if (halves.childElementCount) {
        const label = ev.output ? `prompt + report · ${countLines(ev.output)} line${countLines(ev.output) === 1 ? "" : "s"}` : "prompt";
        inlineFold(head, turn, label, halves, fkey);
      }
    } else if (!ev.output) {
      // No result text yet, OR a command that finished with no output (mkdir, git add, …). Keep the command
      // COLLAPSED behind the head fold from the VERY FIRST render (the user 2026-07-21) — it used to render
      // the IN row expanded and only snap shut once a result landed, so a running command flashed its full
      // text then collapsed. resultUuid (set by the kernel only when the tool_result arrives) tells the two
      // apart: absent = still running; present = done-but-empty. Same fkey as the completed branch, so a
      // user expand survives the running→done re-render.
      if (ev.input) {
        const io = el("div", "tool-io tool-io-fold");
        io.appendChild(ioRow("IN", ev.input, false));
        inlineFold(head, turn, ev.resultUuid ? "no output" : "running…", io, fkey);
      }
    } else {
      // Bash/Grep/Glob/…: output line-count on the head line (right of the command);
      // the command + full output hang below, hidden until clicked.
      const io = el("div", "tool-io tool-io-fold");
      if (ev.input) io.appendChild(ioRow("IN", ev.input, false));
      io.appendChild(ioRow("OUT", ev.output, false));
      const n = countLines(ev.output);
      inlineFold(head, turn, `${n} line${n === 1 ? "" : "s"}`, io, fkey);
    }
  }
  // No right-side status glyph: the LEFT rail dot already carries the outcome — a green ✓
  // disc on success, a red ✗ disc on error (the user 2026-06-13). The old in-head ✓/✗ sat
  // right beside an identical dot, so it was pure duplication.
  return turn;
}


// Navigate to a session by its romp NAME. If it's an open tab, just select it
// (no host round-trip); otherwise ask the host to resolve the name → transcript
// and open/revive it.
function navToSession(name: string) {
  const open = order.find((id) => sessions.get(id)?.name === name);
  if (open) { setActive(open); return; }
  if (vscodeApi) vscodeApi.postMessage({ type: "openByName", name });
}

// Turn an element into a clickable session-name chip (cursor + hover underline,
// click navigates). Used for the sender/recipient chip on a postal card.
function makeSessionChip(elm: HTMLElement, name: string) {
  elm.classList.add("chip-nav");
  elm.title = `Go to ${name}`;
  elm.addEventListener("click", (e) => { e.stopPropagation(); navToSession(name); });
}

// Names of sessions currently WORKING (broadcast by the host) → a working dot
// before that name wherever it renders (postal sender/recipient chips).
let workingSet = new Set<string>();
// Ensure a working dot (the same `.tab-dot` used on working tabs) sits before a
// postal peer name iff that session is working. Idempotent.
function setPeerDot(peerEl: HTMLElement, on: boolean) {
  const prev = peerEl.previousElementSibling;
  const has = !!prev && prev.classList.contains("peer-dot");
  if (on && !has) peerEl.parentElement?.insertBefore(el("span", "tab-dot peer-dot"), peerEl);
  else if (!on && has) prev!.remove();
}
function refreshPostalDots() {
  document.querySelectorAll(".postal-service-peer").forEach((p) => setPeerDot(p as HTMLElement, workingSet.has((p.textContent || "").trim())));
}

// A Romp Postal Service message, as a compact identity-coloured card.
// One-line summary for a postal card: the judge's gist when one exists (the recipient session's caption
// of this message, joined by msg id — the kernel now fills it for OUTGOING mail too), else the first
// non-empty line of the body. The fallback is NOT hard-truncated here: a 100-char slice parked its "…"
// mid-line and wasted the rest (the user 2026-07-25) — CSS clamps the summary to two full lines instead,
// and the gist replaces it on a later render once the recipient's judge has captioned the message.
function postalServiceSummary(ev: Extract<ChatEvent, { kind: "postal-service" }>): string {
  const cap = ev.summary && ev.summary.trim();
  if (cap) return cap;
  return (ev.body || "").split("\n").map((s) => s.trim()).find(Boolean) || "";
}
const collapseWs = (s: string) => s.replace(/\s+/g, " ").trim();

// The interaction TYPE of a postal message, parsed from its leading intent token → a small chip on the
// card head, shown in both the compact and expanded views (the user 2026-06-16). There are THREE
// top-level categories now (the user 2026-06-17): delegation / coordination / question. FYI is NOT its
// own class anymore — it folds into coordination (a heads-up with no work or answer owed), matching the
// courier's delegating-vs-coordinating split (romp-judge). Legacy lead-words fold in: HANDOFF + ASK
// ("do this" = work) → delegation, FYI → coordination, Q → question. Unknown/absent token → no chip.
const POSTAL_INTENTS: Record<string, { label: string; cls: string }> = {
  DELEGATE: { label: "delegation", cls: "delegate" },
  HANDOFF: { label: "delegation", cls: "delegate" },        // legacy term → delegation
  ASK: { label: "delegation", cls: "delegate" },            // legacy "do this" (a work request) → delegation
  COORDINATE: { label: "coordination", cls: "coordinate" },
  FYI: { label: "coordination", cls: "coordinate" },        // legacy heads-up → coordination (no longer its own chip)
  QUESTION: { label: "question", cls: "question" },
  Q: { label: "question", cls: "question" },                // legacy → question
};
function postalServiceIntent(body: string | undefined): { label: string; cls: string } | null {
  const m = /^\s*\*{0,2}([A-Za-z]{1,12})\*{0,2}\s*:/.exec(body || "");
  return m ? (POSTAL_INTENTS[m[1].toUpperCase()] || null) : null;
}

function renderPostalService(ev: Extract<ChatEvent, { kind: "postal-service" }>): HTMLElement {
  const turn = el("div", "turn turn-postal-service postal-service-" + ev.direction);
  if (ev.mid) turn.dataset.mid = ev.mid;   // joins feed-modal handoff hovers to this card
  const d = dot("ring");
  d.classList.add("mail");
  if (ev.color) d.style.background = ev.color.bg;
  turn.appendChild(d);

  const card = el("div", "postal-service-card");
  if (ev.color) {
    card.style.setProperty("--peer-bg", ev.color.bg);
    card.style.setProperty("--peer-fg", ev.color.fg);
  }

  const head = el("div", "postal-service-head");
  const arrow = el("span", "postal-service-arrow");
  arrow.textContent = ev.direction === "in" ? "↙" : "↗";
  const verb = el("span", "postal-service-dir");
  verb.textContent = ev.direction === "in" ? "from" : "to";
  const peer = el("span", "postal-service-peer");
  peer.textContent = ev.peer;
  makeSessionChip(peer, ev.peer); // click the sender/recipient name → go to that session's tab
  // the romp swirl marks this as a romp-postal-service message (the user 2026-06-23: postal is from romp too)
  const rlogo = el("img", "postal-service-romp-logo") as HTMLImageElement;
  rlogo.src = mediaSrc("romp-swirl-glyph.svg"); rlogo.alt = ""; rlogo.title = "Romp Postal Service message"; rlogo.onerror = () => rlogo.remove();
  head.appendChild(rlogo);
  head.appendChild(arrow);
  head.appendChild(verb);
  head.appendChild(peer);
  setPeerDot(peer, workingSet.has(ev.peer));   // working dot before the peer name if that session is working

  // interaction-type chip (delegation / coordination / question). Prefer the sender's DECLARED kind
  // (send_message's `kind` param, surfaced by the kernel) — the old leading-token parse of the body is
  // only a legacy fallback now that the kind rides as an explicit field, not a "DELEGATE:" prefix.
  const intent = (ev.intent && POSTAL_INTENTS[ev.intent.toUpperCase()]) || postalServiceIntent(ev.body);
  if (intent) {
    const ib = el("span", "postal-service-intent postal-service-intent-" + intent.cls);
    ib.textContent = intent.label;
    ib.title = "interaction type";
    head.appendChild(ib);
  }

  if (ev.park || ev.status === "parked") {
    const b = el("span", "postal-service-badge parked");
    b.textContent = "⏸ parked";
    head.appendChild(b);
  } else if (ev.status === "delivered") {
    const b = el("span", "postal-service-badge delivered");
    b.textContent = "✓ delivered";
    head.appendChild(b);
  }

  // (no in-card time — the rail time-marker to the left of the dot carries it, like every
  // other event; see renderEvent's rail time-stamp.)
  card.appendChild(head);

  // Body: ALWAYS lead with a one-line summary — the incoming Haiku caption, or (sent mail / no caption)
  // the first line of the message — and let a click expand the box to the full message inline (the user
  // 2026-06-16). Both directions read the same now: a summary that opens on demand, instead of a hover
  // tooltip (incoming) vs. the whole body always (outgoing).
  const body = el("div", "postal-service-body md");
  const fullText = (ev.body || "").trim();
  const summaryText = postalServiceSummary(ev);
  const expandable = !!summaryText && !!fullText && collapseWs(fullText) !== collapseWs(summaryText);
  if (expandable) {
    const sum = el("div", "postal-service-summary");
    const caret = el("span", "postal-service-expand-caret"); caret.textContent = "▸"; sum.appendChild(caret);
    const sumText = el("span", "postal-service-summary-text"); sumText.textContent = summaryText; sum.appendChild(sumText);
    const full = el("div", "postal-service-full md"); full.innerHTML = md(ev.body); highlight(full);
    // KEYED expand (the user 2026-07-25: "it expands for like a second and then collapses again") —
    // the old hand-rolled classList.toggle lived only on this DOM node, and the next kernel push
    // rebuilds the card, silently closing it. Same openFolds mechanism as every other keyed fold;
    // keyed by message id when the log resolved one, else the carrying atom.
    const pkey = "postal:" + (ev.mid || ev.uuid || "");
    const dress = () => {
      const open = body.classList.contains("expanded");
      caret.textContent = open ? "▾" : "▸";
      sum.title = open ? "click to collapse" : "click to expand the full message";
    };
    applyFold(body, "expanded", pkey);
    dress();
    sum.addEventListener("click", () => { rememberFold(body, "expanded", pkey); dress(); });
    body.classList.add("postal-service-expandable");
    body.appendChild(sum);
    body.appendChild(full);
  } else {
    body.innerHTML = md(ev.body);
    highlight(body);
  }
  card.appendChild(body);

  turn.appendChild(card);
  return turn;
}

// A NATIVE Claude Code teammate message (another agent messaged this session) — deliberately its OWN look,
// NOT the romp postal card: no per-peer color, no romp swirl, no from/to arrow, no session-color chip. A
// plain neutral card with a "teammate" tag + the sending agent name(s) + a collapsed→expand body, so it's
// tellable apart from a romp-postal message at a glance while sharing the same collapse affordance (the
// user 2026-07-05). Before this, these rendered as a blue "you typed this" bubble full of coordination JSON.
function renderTeammate(ev: Extract<ChatEvent, { kind: "teammate" }>): HTMLElement {
  const turn = el("div", "turn turn-teammate");
  const d = dot("ring");
  d.classList.add("teammate");
  turn.appendChild(d);

  const card = el("div", "teammate-card");

  const head = el("div", "teammate-head");
  const tag = el("span", "teammate-tag");
  tag.textContent = "teammate";
  tag.title = "a message from another Claude agent — not from you, not the romp postal service";
  head.appendChild(tag);
  // the sending agent name(s) as PLAIN text — no colored session chip (that's the postal card's language)
  const ids = (ev.blocks || []).map((b) => b.id).filter(Boolean);
  if (ids.length) {
    const names = el("span", "teammate-names");
    names.textContent = ids.length <= 3 ? ids.join(", ") : ids.slice(0, 2).join(", ") + ", +" + (ids.length - 2);
    names.title = ids.join(", ");
    head.appendChild(names);
  }
  card.appendChild(head);

  // Collapsed summary line → click to expand the full body/bodies (same affordance as the postal card).
  // Summary = the first block's summary attr, else "N messages" for a multi-agent batch, else the first
  // non-empty line of the single body.
  const body = el("div", "teammate-body md");
  const blocks = ev.blocks || [];
  const fullText = blocks.map((b) => b.body || "").join("\n\n").trim();
  const firstSummary = (blocks.find((b) => b.summary && b.summary.trim()) || {}).summary || "";
  let summaryText = firstSummary.trim();
  if (!summaryText) {
    summaryText = blocks.length > 1 ? blocks.length + " messages" : (fullText.split("\n").find((l) => l.trim()) || "").slice(0, 140);
  }
  const fullMd = blocks.map((b) => (b.id ? "**" + b.id + "**\n\n" : "") + (b.body || "")).join("\n\n---\n\n");
  const expandable = !!summaryText && !!fullText && collapseWs(fullText) !== collapseWs(summaryText);
  if (expandable) {
    const sum = el("div", "teammate-summary");
    const caret = el("span", "teammate-expand-caret"); caret.textContent = "▸"; sum.appendChild(caret);
    const sumText = el("span", "teammate-summary-text"); sumText.textContent = summaryText; sum.appendChild(sumText);
    const full = el("div", "teammate-full md"); full.innerHTML = md(fullMd); highlight(full);
    sum.title = "click to expand the full message";
    sum.addEventListener("click", () => {
      const open = body.classList.toggle("expanded");
      caret.textContent = open ? "▾" : "▸";
    });
    body.classList.add("teammate-expandable");
    body.appendChild(sum);
    body.appendChild(full);
  } else {
    body.innerHTML = md(fullText || summaryText);
    highlight(body);
  }
  card.appendChild(body);

  turn.appendChild(card);
  return turn;
}

// (statusline chips removed — working state shows the spinner, other states show
//  on the tab outline; CHIP_LABEL is no longer needed.)

function bgRgb(): [number, number, number] {
  try {
    const m = /(\d+)\D+(\d+)\D+(\d+)/.exec(getComputedStyle(document.body).backgroundColor || "");
    if (m) return [+m[1], +m[2], +m[3]];
  } catch { /* ignore */ }
  return [30, 30, 30];
}

// Perceptual fade for an idle session's color: blend toward the background until
// its luminance hits a uniform low target, so a bright hue (yellow) fades as much
// as a dim one (blue) — consistent "faded-ness" regardless of color. Never
// touches the bright color (only used for at-rest tabs).
function fadedColor(hex: string): string {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const [br, bgc, bb] = bgRgb();
  const lum = (x: number, y: number, z: number) => 0.2126 * x + 0.7152 * y + 0.0722 * z;
  const Lc = lum(r, g, b), Lb = lum(br, bgc, bb), Lt = Lb + 38;
  if (Lc <= Lt) return hex; // already dim — leave it
  const t = Math.min(0.85, (Lc - Lt) / (Lc - Lb));
  const hx = (a: number, c: number) => Math.round(a * (1 - t) + c * t).toString(16).padStart(2, "0");
  return `#${hx(r, br)}${hx(g, bgc)}${hx(b, bb)}`;
}

// Tab order is the KERNEL's order, verbatim (the user 2026-06-27). The kernel (bin/romp-kernel `_ordered`)
// is the single source of truth — a pure positional list with NO activity / mtime / idle / status input, so
// it never reshuffles on its own. The client does NOT re-derive it: a tab moves ONLY when the user drags it
// (rewrites the list), a new session arrives (`order.push`, at the end), or a tab closes (`order.splice`).
// NOTHING re-sorts on a status/activity push. The whole reconciliation is the pure `reconcileTabOrder`
// (./tab-order). This replaced a parallel client sort (effIdx + a firstSeen tiebreaker) that diverged from
// the kernel and made tabs jump on ordinary activity — invisible to the kernel's own (passing) order tests.

// Persist the current full tab order. Called only after a drag — the one client action that changes it.
//
// This writes to THIS BROWSER, not to the kernel (the user 2026-07-31): order is a property of how you are
// looking at the fleet, so arranging tabs on the laptop has no business moving them on the desktop — and
// only a browser-side list can interleave hosts at all, since no single kernel can record an order over
// sids it does not know about. Each kernel keeps its own list as the arrival-order SEED; ./view-order
// layers this over it, and federation re-emits the tab strip, the timeline lanes and the feed's groups
// together so all three surfaces read the same way.
function commitTabOrder() {
  writeViewOrder(order.slice());
}
// Settle the just-closed tabs against the kernel's authoritative list. Gone from it → the close landed, stop
// suppressing. STILL in it past the backstop → it didn't land; say so plainly (a session left open while its
// tab is hidden is exactly the silent-wrong-state we'd rather surface) and let the tab come back.
function ackClosingTabs(kernelOrder: readonly string[]): void {
  if (!closingTabs.size) return;
  const live = new Set(kernelOrder);
  const now = Date.now();
  for (const [id, ts] of Array.from(closingTabs)) {
    if (!live.has(id)) { closingTabs.delete(id); continue; }       // the kernel dropped it → confirmed
    if (now - ts < CLOSE_ACK_MS) continue;                         // still in flight; the shutdown runs behind us
    closingTabs.delete(id);
    warnToast(`Couldn't close “${tabMeta.get(id)?.name || id}” — romp still has it open.`);
  }
}

// Apply the kernel's authoritative tab order (its tabOrder push, also re-sent after a timeline drag).
// Ids any kernel tabOrder push has EVER carried, for the page's whole life. A tab on this list is
// kernel-owned: when a later push stops carrying it, that omission is the removal event and the tab is
// dismissed below — the continuous push is the authority, the one-shot `closed` frame just the fast path.
// Before this, `closed` was the ONLY remover: a client whose socket was down at the kill (a frozen webview
// force-dropped at the send-queue cap, a sleep, a network blip) missed that single frame forever, and the
// dead session's tab rode the reconcile keep on every later push — frozen on its last live status, fully
// clickable, reading as a running session (the 2026-08-11 ghost: an ended session stayed on the strip
// looking alive). Add-only, never pruned: dropping an entry would hand a late stale `session` frame the
// never-listed keep and re-mint the ghost. Client-minted ids (the create placeholder) never enter it.
const kernelListed = new Set<string>();
function applyTabOrder(o: any, tabs?: any) {
  // name+color per tab → renderTabs paints placeholders for tabs whose session hasn't arrived yet (tabs-first).
  // The payload is the kernel's AUTHORITATIVE current tab set, so REBUILD (not merge) — a closed tab drops out
  // and never lingers as a stale placeholder. Absent tabs (older kernel) → keep what we have.
  if (Array.isArray(tabs)) {
    tabMeta.clear();
    for (const t of tabs) {
      if (t && typeof t.id === "string") {
        tabMeta.set(t.id, { name: typeof t.name === "string" ? t.name : "",
                            color: (t.color && typeof t.color.bg === "string") ? t.color : null });
      }
    }
  }
  // Adopt the kernel order verbatim, keeping any just-arrived tab the push doesn't carry yet (see tab-order.ts).
  const kernelOrder = Array.isArray(o) ? o.filter((x: any) => typeof x === "string") : [];
  ackClosingTabs(kernelOrder);
  // A kernel-owned tab the push no longer carries gets the SAME teardown the `closed` event runs — the
  // session map, its view, drafts and the active-tab reselect all go, not just the strip entry. Under
  // federation the merged order only omits an id when its OWNING host affirmatively reported it gone
  // (per-host slices persist across down/detached hosts), so this never fires on a tunnel blip.
  const inKernel = new Set<string>(kernelOrder);
  for (const id of order.slice()) {
    if (kernelListed.has(id) && !inKernel.has(id)) dismissSession(id);
  }
  const next = reconcileTabOrder(kernelOrder, order, (id) => sessions.has(id) || tabMeta.has(id),
                                 (id) => kernelListed.has(id));
  order.length = 0;
  for (const id of next) order.push(id);
  for (const id of kernelOrder) kernelListed.add(id);
  renderTabs();
}
// Order-audit instrumentation (the user 2026-07-02): tabs STILL occasionally reorder themselves and code
// reading alone has never found why, so watch the RENDERED order itself. Whenever two tabs present in both
// the previous and the current render swap relative slots (a permutation — adds/drops are routine churn),
// capture the JS stack of whoever triggered this render and report it to the kernel's order-audit.jsonl,
// alongside the kernel's own persist/push records — one log tells which side moved first, and from where.
// A user drag permutes legitimately; it's tagged (drag:true) so the log separates it from the bug.
let lastTabIds: string[] = [];
let tabDragJustCommitted = false;
function auditTabOrder(ids: string[]) {
  const both = new Set(ids.filter((id) => lastTabIds.includes(id)));
  const prev = JSON.stringify(lastTabIds.filter((id) => both.has(id)));
  const next = JSON.stringify(ids.filter((id) => both.has(id)));
  if (prev !== next) {
    const stack = new Error("tab order permuted").stack || "";
    const rec = { type: "orderAudit", surface: "chat-tabs", old: lastTabIds.slice(), new: ids.slice(),
                  stack, drag: tabDragJustCommitted };
    console.warn("[romp] tab order permuted", rec);
    if (vscodeApi) vscodeApi.postMessage(rec);
  }
  tabDragJustCommitted = false;
  lastTabIds = ids.slice();
}
let draggedId: string | null = null;
function reorderTo(dragId: string, targetId: string, after: boolean) {
  const di = order.indexOf(dragId);
  if (di < 0) return;
  order.splice(di, 1);
  const ti = order.indexOf(targetId);
  if (ti < 0) order.push(dragId);
  else order.splice(after ? ti + 1 : ti, 0, dragId);
  tabDragJustCommitted = true;   // the next render's permutation is this drag, not the bug (order audit)
  commitTabOrder();
  renderTabs();
}

// Rich tab hover tooltip (the user 2026-06-23): a CUSTOM DOM tooltip (a native `title` can't colour/bold).
// Shows the full directory path, then labelled field rows — git branch, mode / model / effort, backend —
// the context battery, a labelled "Summary" row, and a labelled "Latest" row = the collapsed ledger's
// current-top-goal recency-coloured "(Xm ago)". One shared element, repositioned under the hovered tab and
// clamped on-screen.
let tabTipEl: HTMLElement | null = null;
function hideTabTip(): void { if (tabTipEl) tabTipEl.style.display = "none"; }
function showTabTip(tab: HTMLElement, s: Session): void {
  if (!tabTipEl) { tabTipEl = el("div", "tab-tip"); document.body.appendChild(tabTipEl); }
  const tip = tabTipEl;
  tip.replaceChildren();
  const now = Date.now() / 1000;
  const be = s.status.backend;
  // labelled rows, one visual grammar (the user 2026-08-13): the directory is a ROW like the others —
  // the 📁 glyph in the label slot, path right of it, aligned — not a naked line floating on top; the
  // branch row wears the ⎇ glyph in its label slot for the same consistency. Branch is the top-level
  // session field, resident even when the head system event is windowed out of the wire tail (the user
  // 2026-06-30), and the worktree row shows where the work actually lands when that differs.
  const rows: Array<[string, string]> = [];
  if (s.cwd) rows.push(["📁", s.cwd]);
  if (s.gitBranch) rows.push(["⎇", s.gitBranch]);
  if (s.workTree) rows.push(["Worktree", s.workTree.dir + (s.workTree.branch ? "  ⎇ " + s.workTree.branch : "")]);
  if (s.status.mode) rows.push(["Mode", prettyMode(s.status.mode)]);
  if (s.status.model) rows.push(["Model", s.status.model]);
  if (s.status.effort) rows.push(["Effort", s.status.effort]);
  // Backend is a plain labelled FIELD now, under the others (the user 2026-07-08 — no longer a coloured
  // "SDK backend" badge at the top of the tooltip; it reads as one of the session's config fields).
  if (be === "sdk" || be === "tmux") rows.push(["Backend", be === "sdk" ? "SDK" : "tmux"]);
  // Billing: whether this tab bills the API key or the Claude login — and WHICH login account (the
  // user 2026-08-09: shown whenever the backend reports it, one-auth machines included; only a tmux
  // session, whose CLI env romp does not control, reports nothing). No key material, ever.
  if (s.status.auth) rows.push(["Billing", s.status.auth === "key" ? "API key"
    : (s.status.authAcct ? `Login (${s.status.authAcct})` : "Login")]);
  for (const [k, v] of rows) {
    const r = el("div", "tab-tip-row");
    const ke = el("span", "tab-tip-k"); ke.textContent = k;
    const ve = el("span", "tab-tip-v"); ve.textContent = v;
    r.appendChild(ke); r.appendChild(ve); tip.appendChild(r);
  }
  // context BATTERY (the same widget as the bottom bar), not a text %
  if (s.status.ctx) {
    const cr = el("div", "tab-tip-row tab-tip-ctx");          // extra vertical room — the battery bar is tall
    const ck = el("span", "tab-tip-k"); ck.textContent = "Context"; cr.appendChild(ck);
    const bar = ctxBar(); setCtxBar(bar, s.status.ctx, s.status.state === "compacting", s.status.ctxColor);
    cr.appendChild(bar); tip.appendChild(cr);
  }
  // ledger rows, LABELLED + aligned with the rows above (the user 2026-06-23 v3): the summary, then Recent.
  const lg = ledgers.get(s.id);
  if (lg?.summary) {
    const r = el("div", "tab-tip-row");
    const k = el("span", "tab-tip-k"); k.textContent = "Summary";
    const v = el("span", "tab-tip-v"); v.textContent = lg.summary;
    r.appendChild(k); r.appendChild(v); tip.appendChild(r);
  }
  // "Recent" — the up-to-5 most recent TOP tasks this session did, ALWAYS shown when any exist, regardless of
  // completion status (done/blocked/cleared) and regardless of age (the user 2026-06-30). PREFER the server's
  // `recent` list: it merges the live store AND the archive, so a session whose tops were all crossed off still
  // lists what it last worked on (the live tree alone would be near-empty → "just a Summary, no Recent"). Fall
  // back to the live tree for an older kernel that doesn't ship `recent`.
  let recentItems: { text: string; t: number }[] = [];
  if (lg?.recent && lg.recent.length) {
    recentItems = lg.recent.map((r) => ({ text: r.text, t: r.t || 0 }));
  } else if (lg?.tree && lg.tree.length) {
    const named = lg.tree.filter((n) => (n.text || "").trim());
    const timed = named.map((n) => ({ text: n.text, t: (n.mt ?? n.t) || 0 })).filter((x) => x.t > 0).sort((a, b) => b.t - a.t);
    const untimed = named.filter((n) => !((n.mt ?? n.t) || 0)).map((n) => ({ text: n.text, t: 0 }));
    recentItems = [...timed, ...untimed].slice(0, 5);
  }
  if (recentItems.length) {
    const r = el("div", "tab-tip-row tab-tip-recent");
    const k = el("span", "tab-tip-k"); k.textContent = "Recent";
    const list = el("div", "tab-tip-recent-list");
    for (const { text, t } of recentItems) {
      const item = el("div", "tab-tip-recent-item");
      const txt = el("span"); txt.textContent = text;
      item.appendChild(txt);
      if (t > 0) {                                            // dated: show the recency-coloured "(Xd ago)"
        const ago = el("span", "tab-tip-ago"); ago.textContent = " (" + agehms(now - t) + " ago)";
        item.appendChild(ago);
        item.style.color = ageColorReadable(now - t);        // text + time both in the node's recency colour
      } else {
        item.style.color = ageColorReadable(345600);         // undated backfill → the oldest-bucket colour
      }
      list.appendChild(item);
    }
    r.appendChild(k); r.appendChild(list); tip.appendChild(r);
  }
  if (!tip.childElementCount) { tip.style.display = "none"; return; }
  tip.style.display = "block";
  tip.style.left = "0px"; tip.style.top = "-9999px";          // measure off-screen, then clamp on-screen
  const r = tab.getBoundingClientRect();
  const tw = tip.getBoundingClientRect().width;
  tip.style.left = Math.round(Math.min(Math.max(4, r.left), window.innerWidth - tw - 6)) + "px";
  tip.style.top = Math.round(r.bottom + 4) + "px";
}

// While a tab name is being edited in place, defer re-renders (a tick's status
// refresh would otherwise replace the tab bar and destroy the input mid-edit).
let renameActive = false;
let renderPendingAfterRename = false;
// While a pointer is PRESSED on the tab strip, defer re-renders (the user 2026-06-30). renderTabs runs on
// EVERY kernel push and does `#tabs`.replaceChildren(), so a push that lands between your mousedown and
// mouseup on a tab's ✕ DESTROYS the pressed node mid-press: the native `click` then never fires (or
// retargets to the data-act-less #tabs), so the delegate never runs and the "End session?" dialog never
// opens — the intermittent "the ✕ is sometimes unresponsive" bug (frequent while the fleet is busy = many
// pushes, rare when idle; NOT focus-related). Delegation alone can't save a click whose pressed node is
// gone. So we HOLD the rebuild while the strip is pressed and flush it AFTER release — exactly the
// timeline's _pointerHeld guard (romp-timeline-view.js). The flush is deferred a tick so the click, which
// dispatches right after pointerup, fires against the still-present node first.
let tabPointerHeld = false;
let renderPendingWhilePressed = false;
// A loading PLACEHOLDER tab (the user 2026-06-26): name + identity color from the kernel's tabOrder push,
// shown while the session's build_session is still in flight so the strip's full width is reserved up front
// (no one-by-one pop-in). Non-interactive — no select/close/drag — until the real session arrives and
// renderTabs swaps in the full tab. It wears the MINI romp swirl (spinning glyph) as its "generating" cue
// (the user 2026-07-03) instead of a whole-tab opacity pulse — the same romp-loader motif as the panes, so a
// tab still building reads as "romp is working on this," consistent everywhere.
function makePlaceholderTab(id: string): HTMLElement {
  const meta = tabMeta.get(id);
  const tab = el("div", "tab tab-placeholder");
  tab.dataset.id = id;
  if (meta?.color) {
    tab.style.setProperty("--chip-bg", meta.color.bg);
    tab.style.setProperty("--chip-fg", meta.color.fg);
    tab.classList.add("colored");
  }
  const swirl = el("img", "tab-ph-swirl") as HTMLImageElement;
  swirl.src = mediaSrc("romp-swirl-glyph.svg"); swirl.alt = ""; swirl.onerror = () => swirl.remove();
  tab.appendChild(swirl);
  const label = el("span", "tab-label");
  if (meta?.name) label.replaceChildren(...hostNameNodes(meta.name, id));
  else label.textContent = "…";
  tab.appendChild(label);
  return tab;
}

// With NO sessions at all, put a real element in #content — otherwise the chat pane sits under the
// romp loader for a full THIRTY SECONDS on every load.
//
// The loader (kernel _pane_spin) hides on an event: a MutationObserver fires when #content gains a child
// whose id isn't the always-present #live-ask host. That is exactly right while a session is loading, and
// unreachable when there is nothing to load — no session means nothing ever renders, the observer never
// fires, and the only escape is the 30s failsafe that exists for a dead kernel. A fresh install has zero
// sessions, so the very first thing a new user sees is a half-minute spinner over an empty pane (the user
// 2026-07-27, on a clean v0.1.1 install: "still takes 10s+ just to load an empty chat", identical on every
// refresh — because it was a timer, not work).
//
// The empty transcript case was already handled with a "No messages yet." placeholder; this is its
// missing sibling, one level up: no sessions rather than no messages. Saying so also beats a spinner —
// it tells a new user the pane is working and what to do next.
function syncNoSessionsPlaceholder(visibleCount: number) {
  const content = document.getElementById("content");
  if (!content) return;
  const existing = document.getElementById("no-sessions");
  if (visibleCount > 0) {
    existing?.remove();               // a session arrived → the real view takes over
    return;
  }
  if (existing) return;               // idempotent: renderTabs runs on every push
  const ph = el("div", "tx-empty");
  ph.id = "no-sessions";
  ph.textContent = "No sessions yet. Start one with  romp new <name>  or the + above.";
  content.appendChild(ph);
}

// The tab strip's vertical context gauge: fill height = context-used %, coloured by the SAME
// server-computed global-colormap RGB the statusline battery / timeline use (setCtxBar), with the
// same traffic-light fallback for an older kernel that doesn't ship ctxColor. Passive — a click
// falls through to the tab's own select; the statusline battery keeps the click-to-/compact.
function tabCtxGauge(ctxStr: string, ctxColor?: number[]): HTMLElement {
  const pct = Math.max(0, Math.min(100, parseInt(ctxStr, 10) || 0));
  const g = el("span", "tab-ctx");
  const fill = el("span", "tab-ctx-fill");
  fill.style.height = pct + "%";
  fill.style.background = (ctxColor && ctxColor.length === 3) ? `rgb(${ctxColor.join(",")})`
    : (pct >= 85 ? "#c0392b" : pct >= 60 ? "#e0b020" : "#54B204");
  g.appendChild(fill);
  g.title = `context ${pct}% used`;
  return g;
}

function renderTabs() {
  if (renameActive) { renderPendingAfterRename = true; return; }
  if (tabPointerHeld) { renderPendingWhilePressed = true; return; }   // don't destroy a tab mid-click (see tabPointerHeld)
  const bar = document.getElementById("tabs");
  if (!bar) return;
  // Preserve TAB-MODE keyboard focus across the rebuild (the user 2026-06-29). renderTabs runs on EVERY kernel
  // push (0.5–3s), and replaceChildren() destroys the focused tab — dropping focus out of the strip (often out
  // of the chat iframe entirely), which silently killed ←/→/Enter nav after a send or any push: you were left
  // focused on nothing, so the keyboard model was dead until you clicked again. If a tab held focus, re-focus
  // the active tab after the rebuild so "tab mode" survives the repaint.
  const refocusTab = bar.contains(document.activeElement);
  bar.replaceChildren();
  // TABS-FIRST (the user 2026-06-26): render the WHOLE strip up front, in `order` — the kernel's order
  // verbatim (applyTabOrder), plus any just-arrived tab not yet pushed. An id whose session hasn't landed yet
  // draws as a placeholder (name+color, non-interactive) that fills in when build_session arrives — so tabs
  // don't pop in one-by-one. NO re-sort here: the order is whatever `order` holds (the user 2026-06-27).
  // A tab the user just closed is skipped on BOTH passes (closingTabs): the kernel goes on listing it for a
  // push or two after the ✕, and drawing it from tabMeta with no session behind it is what put the swirling
  // placeholder back on screen. Cleared the moment the kernel's list agrees — see ackClosingTabs.
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const id of order) { if (!seen.has(id) && !closingTabs.has(id)) { seen.add(id); ids.push(id); } }
  for (const id of tabMeta.keys()) { if (!seen.has(id) && !closingTabs.has(id)) { seen.add(id); ids.push(id); } }   // any pushed tab not yet in `order` (placeholder)
  auditTabOrder(ids);
  // demo/recording view filter (the user 2026-07-14): `#only=<tag>` shows only matching-name tabs; the
  // real sessions keep running, just hidden from this view. No tag → visibleIds === ids (unchanged).
  const only = onlyTag();
  const nameOf = (id: string) => sessions.get(id)?.name ?? tabMeta.get(id)?.name ?? "";
  const visibleIds = only ? ids.filter((id) => matchesOnly(nameOf(id), only)) : ids;
  // ...and it must govern the CHAT BODY too, not just the bar (the user 2026-07-16). Hiding a
  // non-matching TAB while its transcript keeps rendering leaks precisely what the filter exists to
  // hide: a real session's chat sitting on screen under `#only=api,tests,web`, statusline and all —
  // found while shooting the demo, with nimbus's transcript filling a "filtered" frame. Re-point the
  // selection at the first visible session. Deferred so we never re-enter the render we're inside;
  // setActive is a no-op once activeId is visible, so this settles in one pass.
  if (only && activeId && !visibleIds.includes(activeId) && visibleIds.length) {
    const next = visibleIds[0];
    setTimeout(() => { if (activeId !== next) setActive(next); }, 0);
  }
  for (const id of visibleIds) {
    const s = sessions.get(id);
    if (!s) { bar.appendChild(makePlaceholderTab(id)); continue; }
    const tab = el("div", "tab" + (id === activeId ? " active" : ""));
    tab.tabIndex = 0;            // focusable for keyboard nav
    tab.dataset.id = id;
    tab.dataset.act = "select";  // click → setActive, via the stable #tabs delegate (./actions), not a per-node handler
    tab.addEventListener("keydown", onTabKey);
    // drag-to-reorder (synced with the timeline via the shared session-order file)
    tab.draggable = true;
    tab.addEventListener("dragstart", (e) => { draggedId = id; if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"; tab.classList.add("dragging"); });
    tab.addEventListener("dragend", () => { draggedId = null; document.querySelectorAll(".tab.dragging,.tab.drop-before,.tab.drop-after").forEach((t) => t.classList.remove("dragging", "drop-before", "drop-after")); });
    tab.addEventListener("dragover", (e) => {
      if (!draggedId || draggedId === id) return;
      e.preventDefault();
      const r = tab.getBoundingClientRect();
      const after = e.clientX > r.left + r.width / 2;
      tab.classList.toggle("drop-after", after);
      tab.classList.toggle("drop-before", !after);
    });
    tab.addEventListener("dragleave", () => tab.classList.remove("drop-before", "drop-after"));
    tab.addEventListener("drop", (e) => {
      tab.classList.remove("drop-before", "drop-after");
      if (!draggedId || draggedId === id) return;
      e.preventDefault();
      const r = tab.getBoundingClientRect();
      reorderTo(draggedId, id, e.clientX > r.left + r.width / 2);
    });
    if (s.color) {
      tab.style.setProperty("--chip-bg", s.color.bg);
      tab.style.setProperty("--chip-fg", s.color.fg);
      tab.classList.add("colored");
    }
    const st = s.status.state;
    if (st === "working") tab.classList.add("tab-working");
    // "blocked" is an API error. An on-YOU one — "prompt is too long" (compact), a monthly spend cap (raise it,
    // the user 2026-07-14), or a spent model allowance (switch model, the user 2026-08-01) — is alarm-red dashed; a TRANSIENT API error is auto-retrying and needs no attention → the
    // amber retrying treatment, not red (the user 2026-06-29).
    else if (st === "blocked") tab.classList.add((s.status.apiTooLong || s.status.apiSpendLimit || s.status.apiModelLimit || s.status.apiAuthErr) ? "tab-blocked" : "tab-retrying");
    else if (st === "awaiting") tab.classList.add("tab-awaiting");
    else if (st === "retrying") tab.classList.add("tab-retrying");       // amber: soft-blocked on an API auto-retry
    else if (st === "compacting" || st === "clearing") tab.classList.add("tab-compacting");   // both: a context op in flight
    else if (st === "closed") tab.classList.add("tab-closed");       // dead session: read-only, struck-through label
    if (s.status.faded) tab.classList.add("at-rest");
    // WORKING shows a yellow dot; AWAITING-BG the same dot in straw — matching the chip's color, so the
    // tab reads the split at a glance (the user 2026-07-13); BLOCKED (API error) gets NO dot — the dashed
    // red tab highlight instead (the user 2026-06-16).
    if (st === "working") tab.appendChild(el("span", "tab-dot"));
    else if (st === "awaitingBg") tab.appendChild(el("span", "tab-dot await"));
    // OPENING (a provisional tab, or the kernel's own opening chip): the accent loader dot — the session
    // is starting, and a tab with no cue at all read as dead (the user 2026-08-10). Same pulse as the
    // statusline's opening dots; never the solid working yellow, which claims work that isn't happening.
    else if (st === "opening") tab.appendChild(el("span", "tab-dot opening"));
    // compacting → a tiny animated compaction bar before the name (the tab gets no outline for this state,
    // so the bar IS the cue). A teal fill whose right edge slides left and loops — the same "compression"
    // motion as the statusline ctx-scan bar (.ctx-compress), miniaturised. Replaces the static ⇲ glyph the
    // user disliked (2026-06-24): motion reads as a transient PROCESS, not a status colour. Compacting can't
    // coincide with working, so no dot clash.
    if (st === "compacting") {
      const ci = el("span", "tab-compacting-bar");
      const cfill = el("span", "tab-compacting-fill");
      applyCompactSweep(cfill);   // phase-sync across re-renders (the anim no longer restarts) + colormap gradient
      ci.appendChild(cfill);
      ci.title = "compacting — compressing the conversation to free up context";
      tab.appendChild(ci);
    }
    const label = el("span", "tab-label");
    label.replaceChildren(...hostNameNodes(s.name, id));   // remote "host:" prefix renders as quiet metadata
    // ...and the whole tab dims when that host is unreachable, so a disconnected session reads as one at
    // a glance rather than only on inspection (the user 2026-07-29). The struck "host:" carries the why.
    if (hostIsDown(id)) { tab.classList.add("host-off"); tab.title = hostDownNote(id); }
    if (s.status.faded && id !== activeId && s.color) {
      const full = s.color.bg;
      label.style.color = fadedColor(full);
      // The "host:" prefix declares its OWN color (quiet gray), so the parent's faded color can't inherit
      // into it — left alone it stays at full gray and outshines the faded name it precedes. Fade it in
      // tandem via a class, and un-fade it with the name on hover (the user 2026-07-22).
      label.classList.add("name-faded");
      // hover un-fades the name to its full (readable) identity color, reverting on leave
      tab.addEventListener("mouseenter", () => { label.style.color = full; label.classList.remove("name-faded"); });
      tab.addEventListener("mouseleave", () => { label.style.color = fadedColor(full); label.classList.add("name-faded"); });
    }
    tab.appendChild(label);
    // Slim vertical context gauge right of the name (the user 2026-08-08): the statusline battery's
    // fill % + colormap colour, rotated upright and with no % text — so "this session is filling up"
    // reads at a glance across the whole strip. Skipped while compacting (the compacting bar owns that
    // moment, and the % is about to be wrong) and on dead tabs. gear → Chat picks WHEN it shows:
    // only once ≥50% full (the default — a gauge on every quiet tab is clutter; it appears when it
    // has news), always, or never (the user 2026-08-08 v2, replacing the on/off toggle).
    if (settings.tabCtx !== "never" && s.status.ctx && st !== "compacting" && st !== "closed") {
      const pct = Math.max(0, Math.min(100, parseInt(s.status.ctx, 10) || 0));
      if (settings.tabCtx === "always" || pct >= 50) tab.appendChild(tabCtxGauge(s.status.ctx, s.status.ctxColor));
    }
    // Rich hover tooltip (custom DOM — a native title can't colour/bold): backend in its own colour, the
    // full dir path, and mode/model/effort/context each on a line (the user 2026-06-23). See showTabTip.
    tab.addEventListener("mouseenter", () => showTabTip(tab, s));
    tab.addEventListener("mouseleave", hideTabTip);
    const close = el("span", "tab-close");
    close.textContent = "×";
    // A dead (closed) session has nothing to end, so its ✕ just removes the read-only tab — no
    // "End session?" confirm (the user 2026-06-16). A live session still routes through the host's
    // Close-tab / End-session confirm (closeSession → confirmClose).
    const dead = st === "closed";
    close.title = dead ? "Close tab" : "End session";
    // Click-safe (see ./actions): renderTabs() does `#tabs`.replaceChildren() on every kernel push, so a
    // handler hung on this ✕ is destroyed mid-click and the click is dropped (the "had to click End session
    // several times" bug). The action lives on the stable #tabs delegate instead; this node just declares it.
    close.dataset.act = "close";
    close.dataset.id = id;
    if (dead) close.dataset.dead = "1";
    tab.appendChild(close);
    // double-click a tab to show/hide the ledger overview — same as the strip's caret
    tab.addEventListener("dblclick", (e) => { e.preventDefault(); toggleLedgerCollapsed(); });
    // right-click → context menu; "Rename" edits the title in place
    tab.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); showTabMenu(e, id); });
    bar.appendChild(tab);
  }
  const add = el("div", "tab tab-add");
  add.textContent = "+";
  // tooltip carries the CURRENT binding (the user 2026-08-10: shortcuts discoverable by hover). True on
  // every surface: the shell dispatches the effective chord from the same store this reads, and outside
  // the shell (VS Code / standalone, their own localStorage → the default) the in-page Cmd+O fallback
  // below answers it. Rebuilt with the strip each push, so a rebind shows on the next render.
  add.title = titleWithKey("Open a session", "session.new");
  add.addEventListener("click", () => openPicker());
  bar.appendChild(add);
  // Restore tab-mode focus if a tab held it before this rebuild (see the top of renderTabs).
  if (refocusTab) focusActiveTab();
  syncNoSessionsPlaceholder(visibleIds.length);
  // (The Fleet toggle that briefly lived here as a tab-bar pill was removed 2026-06-24: Fleet/Chat are now
  // the rotated toggles in the chat pane's vertical strip — see _LANDING_FLEET_JS — so the pill was redundant.)
  // (The collapse caret moved OFF the tab bar into the #ledger strip's title row — the strip now always
  // shows the session title + caret, expanding to goals / working-on / done. See renderLedger. 2026-06-16)
}

// Right-click context menu on a tab. Webviews can't use VS Code's native menus,
// so this is a small themed floating menu; one open at a time, dismissed by any
// outside click, Escape, scroll, or losing window focus.
let ctxMenuEl: HTMLElement | null = null;
function dismissTabMenu() {
  ctxMenuEl?.remove();
  ctxMenuEl = null;
}

// Right-clicking a SELECTION in the transcript pops a small menu with Reply (quote
// the selection into the composer as a "> …" blockquote) and Copy. With no selection
// inside the chat we leave the native/default menu alone. Reuses the tab menu's
// ctx-menu chrome + its global dismissal (outside-click / Esc / scroll / blur).
function showSelectionMenu(e: MouseEvent) {
  const content = document.getElementById("content");
  const sel = window.getSelection();
  const text = sel ? sel.toString() : "";
  if (!content || !sel || !sel.anchorNode || !content.contains(sel.anchorNode) || !text.trim()) return;
  e.preventDefault();
  dismissTabMenu();
  const menu = el("div", "ctx-menu");
  const mk = (labelText: string, fn: () => void) => {
    const item = el("div", "ctx-item");
    item.textContent = labelText;
    item.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); fn(); });
    menu.appendChild(item);
  };
  mk("Reply", () => quoteSelectionIntoComposer(text));
  mk("Copy", () => copyToClipboard(text));
  document.body.appendChild(menu);
  ctxMenuEl = menu;
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(0, Math.min(e.clientX, window.innerWidth - r.width - 4)) + "px";
  menu.style.top = Math.max(0, Math.min(e.clientY, window.innerHeight - r.height - 4)) + "px";
}

// Drop the selection into the composer as a markdown blockquote (quote.ts does the
// formatting), cursor on the blank line below it, and remember it as the draft.
function quoteSelectionIntoComposer(text: string) {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta) return;
  const { value, caret } = quoteReply(text, ta.value);
  ta.value = value;
  ta.selectionStart = ta.selectionEnd = caret;
  growComposer(ta);
  ta.focus();
  if (activeId) {
    drafts.set(activeId, ta.value);
    // the selection that opened this menu ALSO seeded the auto quote-chip (selectionchange, the user
    // 2026-07-13) — the quote now lives IN the composer text, so drop that chip or the send would quote it
    // twice. Only the NEWEST chip (the one this selection's gesture made) — earlier stacked contexts stay.
    const list = composerCitations.get(activeId);
    if (list?.length && list[list.length - 1].quote) {
      list.pop();
      if (!list.length) composerCitations.delete(activeId);
      renderComposerChips(activeId);
    }
    persistDrafts();
  }
}

function copyToClipboard(text: string) {
  navigator.clipboard?.writeText(text).catch(() => { try { document.execCommand("copy"); } catch { /* best effort */ } });
}
// Toggle a per-session view flag (feed mute / postal isolation) — the SAME message the timeline lane toggles
// send, persisted + re-broadcast by the kernel. Optimistically update the local copy so reopening the menu
// reflects it before the next push (the kernel reconciles).
function setSessionFlag(id: string, flag: "hideFromFeed" | "postalServiceOff" | "notify", value: boolean) {
  const s = sessions.get(id);
  if (s) s[flag] = value;   // both flags are declared optional booleans on Session — no cast needed
  if (vscodeApi) vscodeApi.postMessage({ type: "setSessionFlag", id, flag, value });
}
// Override a session's identity color from the tab menu's swatches (the user 2026-06-29). Optimistically paint
// the new color now (session + placeholder copies), then post to the kernel, which persists it to the names
// registry and re-broadcasts so every surface (tabs, lanes, feed cards) agrees. fg stays white, matching
// _name_color. The kernel accepts only a real palette value, so a stale id is a harmless no-op there.
function setSessionColor(id: string, bg: string) {
  const color: Color = { bg, fg: "#ffffff" };
  const s = sessions.get(id);
  if (s) s.color = color;
  const meta = tabMeta.get(id);
  if (meta) meta.color = color;
  renderTabs();
  // OPTIMISTIC cross-pane echo (the user 2026-08-08): the tabs repaint on this very click, but the
  // FEED kept the old colour until the kernel's next feed rebuild pushed — a second or two. Tell the
  // other panes kernel-free, on the same host-matched pair settings sync rides: the browser's
  // same-origin iframes hear the localStorage write (`storage` fires cross-document; `t` makes
  // re-picking the same colour still fire it), and in VS Code — where each webview's localStorage is
  // its own — the host fans {colorSync} to its other panels (__rompShowStrip marks that host).
  try { localStorage.setItem("romp:color-echo", JSON.stringify({ sid: id, bg, t: Date.now() })); } catch { /* storage blocked */ }
  if ((window as any).__rompShowStrip) vscodeApi?.postMessage({ type: "colorSync", sid: id, bg });
  if (vscodeApi) vscodeApi.postMessage({ type: "setSessionColor", id, bg });
}

// Small inline-SVG icon for the tab menu's toggle items (trusted constant markup; `off` slashes + dims it,
// matching the timeline lane toggles). 16-unit viewBox; currentColor so .ctx-icon/.off set the tint.
function ctxIcon(kind: "feed" | "mail" | "bell" | "bill", off: boolean): HTMLElement {
  const span = el("span", "ctx-icon" + (off ? " off" : ""));
  const slash = off ? '<line x1="1.6" y1="14.4" x2="14.4" y2="1.6"/>' : "";
  const body = kind === "feed"
    ? '<circle cx="8" cy="8" r="6"/><path d="M5 8.3 L7.2 10.7 L11.4 5.3"/>'              // circle + check (on the feed)
    : kind === "mail"
      ? '<rect x="2" y="4" width="12" height="8" rx="1.5"/><path d="M2.5 5 L8 9 L13.5 5"/>'  // envelope (on the postal service)
      : kind === "bill"
        ? '<rect x="2" y="4" width="12" height="8" rx="1.5"/><line x1="2" y1="6.8" x2="14" y2="6.8"/><line x1="4.2" y1="9.6" x2="7.4" y2="9.6"/>'  // payment card (billing)
        : '<path d="M8 2 C5.9 2.2 4.7 3.8 4.7 5.8 L4.7 8 L3.4 9.9 L12.6 9.9 L11.3 8 L11.3 5.8 C11.3 3.8 10.1 2.2 8 2 Z"/><path d="M6.6 11.6 A1.5 1.5 0 0 0 9.4 11.6"/>';  // bell (system notifications)
  span.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + body + slash + "</svg>";
  return span;
}

function showTabMenu(e: MouseEvent, id: string) {
  dismissTabMenu();
  const menu = el("div", "ctx-menu");
  const rename = el("div", "ctx-item");
  rename.textContent = "Rename";
  // id only, never the tab node under the cursor: the menu (on document.body) outlives kernel pushes,
  // but the tab it was opened from does not — renderTabs() swaps the strip on every push, so a node
  // captured here is usually DETACHED by the time Rename is clicked (the click-safety rule).
  rename.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); startTabRename(id); });
  menu.appendChild(rename);
  // Feed + Mail per-session toggles (the user 2026-06-26) — the same controls as the timeline lane's feed
  // checkbox + postal mailbox, here as icon + label + a faint "what it does" sub-line. State from the session.
  const s = sessions.get(id);
  const offFeed = !!(s && s.hideFromFeed);
  const offMail = !!(s && s.postalServiceOff);
  const onBell = !!(s && s.notify);
  menu.appendChild(el("div", "ctx-sep"));
  const toggle = (kind: "feed" | "mail" | "bell", off: boolean, lab: string, sub: string, fn: () => void) => {
    const item = el("div", "ctx-item ctx-item-toggle");
    item.appendChild(ctxIcon(kind, off));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = lab; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub"); sb.textContent = sub; bodyEl.appendChild(sb);
    item.appendChild(bodyEl);
    item.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); fn(); });
    menu.appendChild(item);
  };
  toggle("feed", offFeed,
    offFeed ? "Show in feed" : "Hide from feed",
    offFeed ? "let its prompts make feed cards again" : "stop its prompts making feed cards",
    () => setSessionFlag(id, "hideFromFeed", !offFeed));
  toggle("mail", offMail,
    offMail ? "Rejoin mail" : "Mute mail",
    offMail ? "reconnect it to the postal service" : "hide from peers — no messages in or out",
    () => setSessionFlag(id, "postalServiceOff", !offMail));
  // system-notification bell (the user 2026-07-28) — same flag the timeline lane bell toggles. NOTE the
  // inverted polarity vs the two above: `notify` true is the ENABLED state, so the icon slashes on !onBell.
  toggle("bell", !onBell,
    onBell ? "Stop notifying" : "Notify me",
    onBell ? "no more system notifications for this session" : "system notification when its work blocks on you or completes",
    () => setSessionFlag(id, "notify", !onBell));
  // Billing submenu (the user 2026-08-09, who wants the login/API-key switch here rather than as a
  // statusline badge). Only when the machine offers BOTH choices (st.authBoth) — a one-auth machine
  // keeps the fact on the tab hover, never a dead selector — and the key stays labelled plainly
  // 'API key', no fragment of it anywhere. Clicking opens a flyout with the two choices, the
  // session's current one check-marked; a pick posts the same setAuth the badge used (the session
  // reconnects to apply, so the sub-line says "applying…" while st.authPending rides the status).
  const st = s ? s.status : null;
  if (st && st.auth && st.authBoth) {
    menu.appendChild(el("div", "ctx-sep"));
    const item = el("div", "ctx-item ctx-item-toggle ctx-item-billing");
    item.appendChild(ctxIcon("bill", false));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = "Billing"; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub");
    sb.textContent = st.authPending ? "applying…"
      : (st.auth === "key" ? "API key" : (st.authAcct ? `Login (${st.authAcct})` : "Login"));
    bodyEl.appendChild(sb);
    item.appendChild(bodyEl);
    const caret = el("span", "ctx-caret"); caret.textContent = "▸"; item.appendChild(caret);
    item.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const open = menu.querySelector(".ctx-sub");
      if (open) { open.remove(); return; }                       // second click folds the flyout
      const sub = el("div", "ctx-menu ctx-sub");
      for (const c of [{ label: st.authAcct ? `Login (${st.authAcct})` : "Login", value: "login" },
                       { label: "API key", value: "key" }]) {
        const opt = el("div", "ctx-item" + (st.auth === c.value ? " current" : ""));
        opt.textContent = c.label;
        opt.addEventListener("click", (ev2) => {
          ev2.stopPropagation();
          dismissTabMenu();
          if (st.auth !== c.value && vscodeApi) vscodeApi.postMessage({ type: "setAuth", id, value: c.value });
        });
        sub.appendChild(opt);
      }
      // INSIDE the menu node (so dismissTabMenu and the outside-mousedown check cover it), placed
      // beside the item — .ctx-menu is position:fixed, so the coords are viewport-space, clamped
      menu.appendChild(sub);
      const ir = item.getBoundingClientRect();
      const sr = sub.getBoundingClientRect();
      sub.style.left = Math.max(0, Math.min(ir.right + 2, window.innerWidth - sr.width - 4)) + "px";
      sub.style.top = Math.max(0, Math.min(ir.top, window.innerHeight - sr.height - 4)) + "px";
    });
    menu.appendChild(item);
  }
  // Color swatches (the user 2026-06-29): the romp identity palette as circles, the session's current one
  // ringed. Click one to recolor the session. Omitted until /palette has loaded (paletteColors empty).
  if (paletteColors.length) {
    menu.appendChild(el("div", "ctx-sep"));
    const cur = (s && s.color ? s.color.bg : "").toLowerCase();
    const row = el("div", "ctx-colors");
    for (const bg of paletteColors) {
      const sw = el("button", "ctx-swatch" + (bg.toLowerCase() === cur ? " sel" : ""));
      sw.style.background = bg;
      sw.title = bg;
      sw.setAttribute("aria-label", "Set color " + bg);
      sw.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); setSessionColor(id, bg); });
      row.appendChild(sw);
    }
    menu.appendChild(row);
  }
  document.body.appendChild(menu);
  ctxMenuEl = menu;
  // at the cursor, clamped so it never overflows the pane
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(0, Math.min(e.clientX, window.innerWidth - r.width - 4)) + "px";
  menu.style.top = Math.max(0, Math.min(e.clientY, window.innerHeight - r.height - 4)) + "px";
}
// A remote host coming or going flips the disconnected marks on its tabs. The federation manager fires
// this only when the reachable set actually CHANGES (its own /tunnels poll is the event), so this is a
// repaint per connect/drop, not per poll (the user 2026-07-29).
window.addEventListener("romp-hosts", () => { renderTabs(); });
window.addEventListener("mousedown", (e) => { if (ctxMenuEl && !ctxMenuEl.contains(e.target as Node)) dismissTabMenu(); }, true);
window.addEventListener("keydown", (e) => { if (e.key === "Escape") dismissTabMenu(); }, true);
window.addEventListener("scroll", dismissTabMenu, true);
window.addEventListener("blur", () => dismissTabMenu());

// "Rename" (tab context menu): swap the tab's label for an inline input. Enter
// or clicking away commits (the host renames the tmux session and confirms with
// a "renamed" message — the label only changes once that lands), Esc cancels.
function startTabRename(id: string) {
  const s = sessions.get(id);
  if (!s) return;
  // Resolve the tab NOW, by id. The old signature took the nodes captured at menu-open time, and a
  // kernel push while the menu sat open replaced the strip under it — so the first Rename click did all
  // its work on a detached orphan (invisible input, focus() a no-op) and left renameActive stuck true,
  // since only that orphan input's Enter/Esc/blur could clear it. The frozen strip is why a SECOND
  // attempt always worked, and why committing it healed everything (the user 2026-08-08: "rename only
  // takes on the second try"). A vanished tab (session closed mid-menu) bails out BEFORE the flag.
  const bar = document.getElementById("tabs");
  const tab = bar && Array.from(bar.children).find(
    (t): t is HTMLElement => t instanceof HTMLElement && t.dataset.id === id);
  const label = tab && tab.querySelector<HTMLElement>(".tab-label");
  if (!tab || !label || tab.querySelector(".tab-rename")) return;
  // A remote session displays as "host:name", where "host:" is METADATA this viewer added (see
  // ./host-prefix) — the far kernel knows the session by the bare name alone. Seeding the editor with
  // the whole display string handed the user the host to edit and sent it back on the other side of the
  // rename, where the colon is not a legal session name and the write was refused (the user 2026-08-02).
  // So the host stays put, rendered exactly as the label renders it and not editable, and the input
  // holds the one part that is theirs to change.
  const p = hostPrefix(s.name, id);
  const base = p ? p.rest : s.name;
  const input = document.createElement("input") as HTMLInputElement;
  input.className = "tab-rename";
  input.value = base;
  input.spellcheck = false;
  input.size = Math.max(base.length, 4);
  const fixed = p ? el("span", "host-prefix") : null;
  if (fixed) fixed.textContent = p!.host;
  renameActive = true;
  tab.draggable = false;            // dragging would eat the text selection
  label.style.display = "none";
  label.after(input);
  if (fixed) input.before(fixed);
  let finished = false;
  const done = (commit: boolean) => {
    if (finished) return;
    finished = true;
    const v = input.value.trim();
    input.remove();
    fixed?.remove();
    label.style.display = "";
    tab.draggable = true;
    renameActive = false;
    if (renderPendingAfterRename) { renderPendingAfterRename = false; renderTabs(); }
    // The bare name, never the display string: the host prefix is this viewer's, and the kernel that
    // owns the session is addressed by `id` (federation routes on the prefix there).
    if (commit && v && v !== base && vscodeApi) vscodeApi.postMessage({ type: "renameSession", id, name: v });
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); done(true); }
    else if (e.key === "Escape") { e.preventDefault(); done(false); }
  });
  input.addEventListener("blur", () => done(true));
  // keep clicks inside the input from selecting/dragging the tab underneath
  for (const ev of ["click", "mousedown", "dblclick", "contextmenu"]) input.addEventListener(ev, (e) => e.stopPropagation());
  input.focus();
  input.select();
}

// Keyboard nav on a focused tab: ←/→ step prev/next; ↑/↓ jump to the nearest tab
// in the row above/below (tabs wrap via flex-wrap).
function onTabKey(e: KeyboardEvent) {
  if (!activeId || !order.length) return;
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    const i = order.indexOf(activeId);
    if (i < 0) return;
    const dir = e.key === "ArrowRight" ? 1 : -1;
    setActive(order[(i + dir + order.length) % order.length]);
    focusActiveTab();
  } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    e.preventDefault();
    const t = tabInAdjacentRow(activeId, e.key === "ArrowDown" ? 1 : -1);
    if (t) { setActive(t); focusActiveTab(); }
  } else if (e.key === "Enter") {
    // in "tab mode", Enter drops back into the now-selected session below the transcript (the user
    // 2026-06-25), the mirror of Escape (composer → tabs). ←/→ pick the session, Enter starts. If a live
    // AskUserQuestion picker is up instead of the message box, Enter focuses the PICKER so ↑/↓ step the
    // options (the user 2026-06-27) — it used to focus the now-hidden composer, so the picker never got keys.
    e.preventDefault();
    focusComposerOrAsk();
  }
}
function focusActiveTab() {
  const bar = document.getElementById("tabs");
  (bar?.querySelector(`.tab[data-id="${activeId}"]`) as HTMLElement | null)?.focus();
}
// "Enter to start typing" lands on whatever's actually showing below the transcript: when a live
// AskUserQuestion picker is up the PICKER CARD owns the keyboard (↑/↓ step the options, Enter confirms), so
// focus that — the message box stays visible below (it's the picker's "add your own" field) and a click lands
// there to type a free-text answer; otherwise focus the message box (the user 2026-06-27). Returns whether it
// focused something (so the caller can preventDefault only then).
function focusComposerOrAsk(): boolean {
  if (activeId && liveAsks.has(activeId)) {
    const card = document.querySelector("#live-ask .ask-card") as HTMLElement | null;
    if (card) { card.focus({ preventScroll: true }); return true; }
  }
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (ta && !ta.disabled) { ta.focus(); return true; }
  return false;
}

// Window-level arrow nav for when the CHAT WINDOW (not the composer or a dialog)
// has focus: ←/→ step between tabs, ↑/↓ scroll the transcript. Deliberately
// yields to anything more specific —
//   • a typing target (textarea/input/contenteditable) keeps its native caret;
//   • an open picker/confirm overlay (.picker-overlay) owns its own keys;
//   • a handler that already acted (defaultPrevented) wins — a FOCUSED tab's
//     onTabKey (which also does ↑/↓ row-jumps) and the live-ask card both
//     preventDefault before this bubbles to window.
// On ←/→ we do NOT focus the tab, so focus stays in the window and ↑/↓ keep
// scrolling. Any modifier (so Cmd/Ctrl/Alt/Shift shortcuts and selection are
// untouched) bails out.
const NAV_SCROLL_STEP = 60;
function isTypingTarget(t: EventTarget | null): boolean {
  const elm = t as HTMLElement | null;
  if (!elm || typeof elm.tagName !== "string") return false;
  return elm.tagName === "TEXTAREA" || elm.tagName === "INPUT" || elm.isContentEditable === true;
}
window.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  if (isTypingTarget(e.target)) return;
  if (document.querySelector(".picker-overlay")) return;   // #picker / #confirm open
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    if (!activeId || order.length < 2) return;
    const i = order.indexOf(activeId);
    if (i < 0) return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    setActive(order[(i + dir + order.length) % order.length]);
  } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    const content = document.getElementById("content");
    if (!content) return;
    e.preventDefault();
    content.scrollBy({ top: e.key === "ArrowDown" ? NAV_SCROLL_STEP : -NAV_SCROLL_STEP });
  } else if (e.key === "Enter") {
    // A live transcript selection outranks everything below (the user 2026-08-04): the selection already
    // seeded the reply chip (selectionchange), and Enter is the natural "now type the reply" — so drop
    // straight into the message box with that context attached, even when the mousedown that made the
    // selection happened to land focus on a fold head or button (which the bare-area gate below would
    // refuse). Re-seeding here is what makes the chip exactly what's selected at the moment of Enter.
    // Focusing the box collapses the selection, and the chip survives — a collapse never clears it.
    // A focused tab and the live-ask card still win: both preventDefault before this bubbles to window.
    const q = transcriptSelection();
    if (q && activeId) {
      e.preventDefault();
      seedTranscriptQuote(activeId, q.text, q.uuid);
      focusComposer();
      return;
    }
    // Enter from the bare chat AREA → drop the cursor into the message box, so after clicking in the
    // transcript to read/select you can just hit Enter to type (the user 2026-06-26). Gated on
    // activeElement being the body (no focused control), so it never steals Enter from a focused tab
    // (onTabKey), the live-ask card, a code-copy button, etc. — and it touches NO clicks, so highlighting
    // and expanding folds are completely unaffected. The two-state model (tabs ↔ box) thus has a sensible
    // default: Enter always lands you in the box unless you're already on a tab or in the box.
    const ae = document.activeElement;
    if (ae && ae !== document.body) return;
    if (focusComposerOrAsk()) e.preventDefault();   // the picker card if one's up, else the message box
  }
});
// Cmd/Ctrl+O and Cmd/Ctrl+Shift+O — the in-PAGE fallback, from anywhere including the composer, the
// way Obsidian's quick switcher opens over the editor (the user 2026-08-08). Inside the romp shell
// this handler STANDS DOWN: the shell owns both combos (palette-main.ts — plain O is the session jump
// switcher up in the shell document, Shift+O this picker), and its per-pane capture handler sees the
// keystroke once this window-capture one declines to stop it. Standalone and in VS Code (no shell
// handler on this document) both combos fall back to the picker here. preventDefault cancels the
// browser's own Cmd+O (open file) while this tab has focus, and only there.
function inRompShell(): boolean {
  try { return !!(window.parent && window.parent !== window && window.parent.document.getElementById("chat-pane")); }
  catch (e) { return false; }   // cross-origin parent (VS Code) — not the romp shell
}
window.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || (e.key || "").toLowerCase() !== "o") return;
  if (inRompShell()) return;   // the shell's handler on this document takes it from here
  e.preventDefault(); e.stopPropagation();
  if (pickerVisible()) closePicker(); else openPicker();
}, true);
// Nearest tab in the row above (dir<0) or below (dir>0) the given tab, by column.
function tabInAdjacentRow(id: string, dir: number): string | null {
  const bar = document.getElementById("tabs");
  const cur = bar?.querySelector(`.tab[data-id="${id}"]`) as HTMLElement | null;
  if (!bar || !cur) return null;
  const cr = cur.getBoundingClientRect();
  const cx = cr.left + cr.width / 2;
  let best: { id: string; score: number } | null = null;
  for (const t of Array.from(bar.querySelectorAll(".tab[data-id]")) as HTMLElement[]) {
    const r = t.getBoundingClientRect();
    const vGap = dir < 0 ? cr.top - r.bottom : r.top - cr.bottom; // >0 only if on a row in that direction
    if (vGap < -1) continue;
    if ((dir < 0 && r.bottom > cr.top + 1) || (dir > 0 && r.top < cr.bottom - 1)) continue;
    const score = Math.max(0, vGap) * 1000 + Math.abs(r.left + r.width / 2 - cx); // nearest row, then nearest column
    if (!best || score < best.score) best = { id: t.dataset.id!, score };
  }
  return best?.id ?? null;
}

// ---- session picker overlay (colored, Claude-Code-history style) ----

// When true, picking a row returns the selection to the extension (cross-ext
// pickSession) instead of opening a tab. pickAllowNew adds a "New session…" row.
let pickMode = false;
let pickAllowNew = false;

// The session you just created gets its TAB AND COMPOSER IMMEDIATELY, and starts behind them (the user
// 2026-07-30). This replaced an "Opening session…" modal that covered the pane while the kernel resolved
// the directory, spawned tmux or connected the SDK, and the first transcript poll came back — seconds you
// could do nothing with, watching three dots. See ./provisional.ts for why the id carries no colon.
//
// `pendingNewSession` is the NAME the created session will arrive under; it is the only join available,
// since the kernel mints the id. The provisional tab carries the OPENING state (the same "Opening
// session" dots the kernel's own opening chip renders — it seeded "working" once, which put a Working
// chip over an epoch-sized clock in the statusline until the first real payload arrived; the user
// 2026-08-10), a live composer, and anything typed into it, held until the real session lands.
let pendingNewSession: string | null = null;
let provisionalId: string | null = null;
const provisionalQueue: string[] = [];
let provisionalTimer: ReturnType<typeof setTimeout> | undefined;
// Text typed into a provisional tab whose create had to ask something first, waiting for the retry's tab.
let pendingCarry = "";
// Provisional tabs whose create FAILED (the user 2026-08-08): the tab — and whatever was typed into
// it — STAYS, foregrounded, with the failure dialog on top. It used to be torn down and the held text
// dumped into whichever tab happened to be active, polluting an unrelated thread's draft. The set keys
// the failed transcript placeholder, the send refusal, and the composer's read-only exemption; the
// tab's ✕ discards tab and text together (an explicit choice, local-only — the kernel never knew it).
const failedProvisionals = new Set<string>();
// The backstop, for a create that fails with nothing said. Every failure the kernel CAN name arrives as a
// warn / createDirMissing and lands the dialog at once; this covers a spawn that dies silently. It is
// deliberately long — the point is that it is no longer what you wait on, the way the old 30s cue was.
const PROVISIONAL_WAIT_MS = 90_000;

function openProvisional(req: CreateReq): void {
  dropProvisional();                       // never two at once: a second create supersedes the first
  const display = provisionalName(req.host, req.name);
  pendingNewSession = display;
  const id = mintProvisionalId(Date.now().toString(36) + Math.random().toString(36).slice(2));
  provisionalId = id;
  // state "opening", NOT "working": updateStatusline renders the working chip with an elapsed timer off
  // sinceEpoch, and a provisional tab has no honest work clock — the seed showed "Working" + a giant
  // number for however long the first kernel payload took (the user 2026-08-10, who read it as "a random
  // string of numbers"). "opening" is the designed vocabulary for exactly this phase, and the kernel's
  // first payload for the real session continues it seamlessly. sinceEpoch is MILLISECONDS everywhere.
  sessions.set(id, { id, name: display, color: null, events: [],
                     status: { state: "opening", sinceEpoch: Date.now() } });
  order.push(id);                          // a tab the kernel does not know yet survives reconcileTabOrder
  renderTabs();
  setActive(id);
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  // text carried over from a create that had to ask something first (the folder question): it was typed
  // for THIS session, so it comes back into the box the retry opens rather than being stranded on
  // whichever tab happened to be underneath.
  if (ta && pendingCarry) { ta.value = pendingCarry; growComposer(ta); }
  pendingCarry = "";
  ta?.focus();                             // the whole point: you can start typing NOW
  provisionalTimer = setTimeout(
    () => failProvisional("romp asked to start it, but nothing came back."), PROVISIONAL_WAIT_MS);
}

/** Retire the provisional tab and hand back whatever was typed into it, so nothing is ever just dropped. */
function dropProvisional(): { queued: string[]; draft: string } {
  const id = provisionalId;
  provisionalId = null;
  pendingNewSession = null;
  if (provisionalTimer) { clearTimeout(provisionalTimer); provisionalTimer = undefined; }
  const queued = provisionalQueue.slice();
  provisionalQueue.length = 0;
  let draft = "";
  if (id) {
    const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
    draft = (activeId === id && ta) ? ta.value : (drafts.get(id) ?? "");
    pendingSent.delete(id);                // the optimistic bubbles belong to a tab that is going away
    dismissSession(id);                    // drops it from sessions/order/views and reselects
  }
  return { queued, draft };
}

// The real session arrived: move everything the provisional tab was holding onto it and focus it. The
// queued messages send FOR REAL here — they were never sent before, because there was no session to send
// them to; the dashed bubbles you saw were this client saying "received", not the kernel.
function adoptProvisional(realId: string): void {
  const { queued, draft } = dropProvisional();
  if (draft) drafts.set(realId, draft);    // set BEFORE the switch — setActive fills the box from drafts
  setActive(realId);
  for (const text of queued) {
    vscodeApi?.postMessage({ type: "sendMessage", id: realId, text });
    registerOptimistic(realId, text);      // …and the bubble carries over to the tab that now owns it
  }
  if (draft) { persistDrafts(); const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null; if (ta) growComposer(ta); }
}

// A create that FAILED. The kernel's own words are the message wherever it gave any (a bad name, an
// unreadable parent, the SDK setup hint) — a dialog naming the reason beats the old cue, which simply
// stopped after thirty seconds and left you to work out that nothing had happened. The TAB STAYS and is
// foregrounded first, with whatever was typed back in ITS box (the user 2026-08-08): tearing it down
// restored the text into whichever tab happened to be active, so a failure that landed while you were
// reading another thread silently rewrote that thread's draft.
function failProvisional(why: string): void {
  if (!provisionalId) return;
  const id = provisionalId;
  const name = pendingNewSession || "that session";
  // retire the create MACHINERY only — dropProvisional() would dismiss the tab too
  provisionalId = null;
  pendingNewSession = null;
  if (provisionalTimer) { clearTimeout(provisionalTimer); provisionalTimer = undefined; }
  const queued = provisionalQueue.slice();
  provisionalQueue.length = 0;
  const ta0 = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  const draft = (activeId === id && ta0) ? ta0.value : (drafts.get(id) ?? "");
  const held = [...queued, draft].filter(Boolean).join("\n\n");
  pendingSent.delete(id);            // the dashed "received" bubbles fold into the held text instead
  failedProvisionals.add(id);
  const s = sessions.get(id);
  // "closed" gives the tab the dead treatment (struck label, plain ✕) — but the composer stays LIVE
  // for a failed provisional (the read-only exemption below), since the held text must stay editable
  if (s) s.status = { state: "closed", sinceEpoch: Date.now() };   // ms, like every kernel payload
  setActive(id);                     // jump back to the failed thread BEFORE saying anything
  if (held) {
    drafts.set(id, held); persistDrafts();
    const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
    if (ta) { ta.value = held; growComposer(ta); }
  }
  renderTabs();
  showConfirm("Couldn't start " + name,
    why + (held ? "\n\nWhat you typed is in this tab's message box." : ""),
    [{ label: "OK", value: "ok" }], () => { /* the tab keeps the text until its ✕ discards both */ });
}

// The ✕ on a provisional tab: tell the kernel to abort the pending spawn too, so a slow-but-successful
// create doesn't leave behind an orphan session the user meant to cancel.
function cancelProvisional(): void {
  const name = pendingNewSession;
  dropProvisional();
  if (name && vscodeApi) vscodeApi.postMessage({ type: "cancelCreate", name });
}

// Bring this pane FORWARD in the shell. On mobile only one pane is on screen at a time, so a jump the
// chat just took is invisible unless the shell switches to the chat tab; on desktop all panes are up at
// once and the shell ignores it. The kernel asks for that switch itself — but only of ITS OWN shell
// clients, and the shell socket is local-only, so a click on a REMOTE host's card routes to that host's
// kernel and its reveal reaches nobody: on a phone, a remote session's distilled summary looked like dead
// text (the user 2026-07-30). The pane knows it took the focus whichever kernel sent it, so it asks for
// itself, and the local path keeps its (now duplicate, idempotent) kernel-side reveal.
function revealSelfPane(): void {
  try {
    if (window.parent && window.parent !== window) window.parent.postMessage({ romp: "reveal", pane: "chat" }, "*");
  } catch (e) { /* standalone page — no shell to ask */ }
}

// Full-screen bridge (the user 2026-07-05): the picker is rendered inside the /chat iframe, so its
// position:fixed;inset:0 only covered the chat PANE — on a short pane the session list couldn't scroll.
// Mirror the settings bridge: tell the shell to lift #f-chat over the whole window (body.picker-open) while
// the picker is up, so the overlay fills the screen and the list gets the full viewport height. Standalone
// (no parent) is a no-op.
//
// While lifted, this page keeps PAINTING its content at the chat pane's old screen rect (--pane-* vars,
// measured from the shell's pane div): the transcript stays exactly where it was, live and visible under
// the overlay's dim like every other pane. The first cut hid the page's content instead, which left a
// BLACK HOLE where the chat pane had been — the one region of the dashboard that changed behind the
// centered modal (the user 2026-08-08). A pane we can't measure (hidden, or a cross-origin parent like
// VS Code) falls back to that hiding via .pane-gone.
function liftPaneRect(): DOMRect | null {
  try {
    const p = window.parent?.document?.getElementById("chat-pane");
    return p ? p.getBoundingClientRect() : null;
  } catch (e) { return null; }   // cross-origin parent (VS Code) — no shell pane to measure
}
function placeLifted(): void {
  const r = liftPaneRect();
  const gone = !r || r.width < 40 || r.height < 40;
  document.body.classList.toggle("pane-gone", gone);
  if (gone) return;
  const st = document.documentElement.style;
  st.setProperty("--pane-x", r!.left + "px");
  st.setProperty("--pane-y", r!.top + "px");
  st.setProperty("--pane-w", r!.width + "px");
  st.setProperty("--pane-h", r!.height + "px");
}
function onLiftResize(): void { if (pickerVisible()) placeLifted(); }   // panes track the window; follow them
function signalPickerOverlay(on: boolean) {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ romp: "picker", on }, "*");
      if (on) { placeLifted(); window.addEventListener("resize", onLiftResize); }
      else window.removeEventListener("resize", onLiftResize);
      // The class rides documentElement AND body (like the gear's rs-modal-open): THEME_CSS paints
      // html and body separately, and an opaque html kept the lift a full black-out (the user 2026-08-08).
      document.documentElement.classList.toggle("picker-lifted", on);
      document.body.classList.toggle("picker-lifted", on);
    }
  } catch (e) { /* standalone: no shell to lift */ }
}

// ── new-session directory: inline completer ────────────────────────────────────────────────────────
// Typing a path used to be blind — a datalist of previously-used dirs and a native Browse… dialog that
// only ever showed the LOCAL machine, so a session on a remote host was typed from memory and only
// found out it was wrong after the create (the user 2026-07-28). The kernel that will own the session
// answers instead (dirComplete → dirCompletions), so the same field completes remote paths over the
// host's tunnel, and the status line under it says what the path IS before anything is committed.
//
// No debounce: a request goes out on the keystroke, and while one is in flight the newest value is
// held and fired when the reply lands. The pacing is the round-trip itself — an event, not a timer —
// so a fast local kernel completes per keystroke and a slow SSH hop coalesces on its own.
interface DirItem { name: string; path: string }
let dirReq = 0;                    // newest request id; a reply for an older one is stale
let dirInFlight = false;
let dirQueued: string | null = null;  // typed while a request was out
let dirItems: DirItem[] = [];
let dirActive = -1;                   // highlighted completion row (-1 = none; Enter then creates)
let dirStatus: DirStatus | null = null;
let dirAskedHost = "";          // the host the in-flight question was about; a reply for any other is stale

// The last directory a session was actually started in, per host (the user 2026-07-29). The gear's
// "Default directory" is ONE path on THIS machine, so it is the wrong answer for a remote: prefilling a
// Mac's ~/GitRepos into a Linux box's field is a path that cannot exist there. What you used last on
// that machine is the answer that is right by construction, and it overrides the gear default for that
// host. With nothing remembered, a remote is left BLANK, which asks that kernel for its own default —
// the honest fallback, since only that machine knows where its romp lives.
const DIR_BY_HOST_KEY = "romp:dirByHost";

function dirByHost(): Record<string, string> {
  try {
    const v = JSON.parse(localStorage.getItem(DIR_BY_HOST_KEY) || "{}");
    return v && typeof v === "object" ? v : {};
  } catch { return {}; }
}

function rememberDir(host: string, dir: string): void {
  const d = (dir || "").trim();
  if (!d) return;
  const all = dirByHost();
  all[host || ""] = d;
  try { localStorage.setItem(DIR_BY_HOST_KEY, JSON.stringify(all)); } catch { /* storage full */ }
}

/** What the directory field should start with for `host`: what you last used there, else (local only)
 *  the gear/kernel default, else blank so that kernel answers with its own. */
function dirPrefill(host: string): string {
  const remembered = dirByHost()[host || ""];
  if (remembered) return remembered;
  return host ? "" : (kernelDefaultDir || loadSettings().defaultDir || "");
}

// Browse… is served by the kernel, and only when THAT machine can actually draw a dialog. Two ways it
// can't: a REMOTE host, whose dialog would open on the local screen and list the wrong disk; and a kernel
// with no desktop session at all — a server, a cloud VM — where the click used to reach a macOS-only
// osascript and return nothing whatsoever. Neither is a reason to leave a button that looks live, and
// neither costs anything to lose: the field beside it already does the job — the completer asks the
// OWNING kernel, so a path on any host is typed with real folders offered as you go.
// The capability rides in on the local sessionList; assume yes until a kernel says otherwise, so an older
// kernel that doesn't send it keeps the button it always had.
let kernelNativeDialogs = true;

// The two cases are shown differently, because one of them can change and the other cannot. A REMOTE host
// is one click back to local, so the button stays in place, disabled, saying so. A kernel with no desktop
// can NEVER open a dialog, so the button is not rendered at all: a permanently grey control explained only
// by a hover title is no explanation on a phone, and the field beside it is the whole affordance anyway.
function applyBrowseState(host: string): void {
  const b = document.querySelector("#picker .picker-browse") as HTMLButtonElement | null;
  if (!b) return;
  b.style.display = kernelNativeDialogs ? "" : "none";
  b.disabled = !!host;
  b.title = host
    ? `The native dialog is local-only. Type the path on ${host} instead; it completes as you type.`
    : "Pick a folder with the native dialog (opens on the kernel's machine — host-local)";
}

// Which host's sessions the picker list is currently showing ("" = this machine). The Host row picks the
// machine a NEW session would be created on; it now also picks whose EXISTING sessions are listed, so a
// remote session can be reopened or revived without going to that machine's own dashboard.
let pickerListHost = "";

function requestSessionList(host: string): void {
  pickerListHost = host;
  // the billing choices on screen belong to the host that just stopped being selected — hide the auth
  // row until ITS kernel's sessionList answers with its own availability (the same staleness rule the
  // dir status follows; an older kernel never answers with one and the row simply stays away)
  pickerAuthAvail = null;
  syncPickerAuth();
  if (vscodeApi) vscodeApi.postMessage({ type: "requestSessions", host });
}

// Per-session BILLING for a NEW session (the user 2026-08-08): login vs the API key the selected host's
// manager environment carries. The row is ALWAYS there for an SDK session (the user 2026-08-09):
// segmented BUTTONS when the selected host offers both, and when it offers ONE, the same spot just
// writes out which it is — informative, never a one-option selector (what the earlier disappearing
// rule was really against). The row still disappears when the backend toggle says tmux (that CLI
// lives in the tmux server's environment, which the kernel does not control) and until the host's
// sessionList reply carries authAvail (an older kernel never answers with one).
let pickerAuthAvail: { login?: boolean; key?: boolean; acct?: string; default?: string } | null = null;

function syncPickerAuth(): void {
  const wrap = document.querySelector("#picker .picker-auth") as HTMLElement | null;
  if (!wrap) return;
  const beSel = document.querySelector("#picker .picker-backend:not(.picker-host):not(.picker-auth) .picker-be-opt.sel") as HTMLElement | null;
  const a = pickerAuthAvail;
  const show = !pickMode && !!(a && (a.login || a.key)) && (beSel?.dataset.be || loadSettings().backend) === "sdk";
  wrap.style.display = show ? "" : "none";
  if (!show) return;
  const both = !!(a!.login && a!.key);
  wrap.querySelectorAll(".picker-be-opt").forEach((x) => ((x as HTMLElement).style.display = both ? "" : "none"));
  const fixed = wrap.querySelector(".picker-auth-fixed") as HTMLElement | null;
  if (fixed) {
    fixed.style.display = both ? "none" : "";
    // one real choice → written out in the buttons' place, naming the login account when known
    fixed.textContent = both ? "" : (a!.key ? "API key" : (a!.acct ? `Login (${a!.acct})` : "Login"));
  }
  if (!both) return;   // the fixed text is the whole row — nothing to seed
  // the Login button's hover names WHICH account (the user 2026-08-09)
  const loginBtn = wrap.querySelector('.picker-be-opt[data-auth="login"]') as HTMLElement | null;
  if (loginBtn && a!.acct) loginBtn.title = `Bill this session to the machine's Claude login (${a!.acct}).`;
  // seed the selection from the host's default only when nothing is selected yet this open
  if (!wrap.querySelector(".picker-be-opt.sel")) {
    const def = a!.default === "key" ? "key" : "login";
    wrap.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.toggle("sel", (x as HTMLElement).dataset.auth === def));
  }
}

// the picked billing for the create payload — "" when the row is hidden or written-out (one real
// choice: the kernel default IS that choice, and a stale .sel from a previously-selected both-offering
// host must not ride along)
function pickerAuthChoice(): string {
  const wrap = document.querySelector("#picker .picker-auth") as HTMLElement | null;
  if (!wrap || wrap.style.display === "none") return "";
  const sel = wrap.querySelector(".picker-be-opt.sel") as HTMLElement | null;
  if (!sel || sel.style.display === "none") return "";
  return sel.dataset.auth || "";
}

function pickerHost(): string {
  const sel = document.querySelector("#picker .picker-host .picker-be-opt.sel") as HTMLElement | null;
  return sel?.dataset.host || "";
}

function askDirComplete(value: string): void {
  if (!vscodeApi) return;
  if (dirInFlight) { dirQueued = value; return; }
  dirInFlight = true;
  dirAskedHost = pickerHost();
  // The old verdict belongs to whatever was asked BEFORE this (the user 2026-07-29, who switched host and
  // watched the field keep insisting the path was fine). It is about a different machine, or a different
  // path, so it stops standing the instant a new question goes out: the line says it is checking, and a
  // host whose kernel never answers (an older build with no such op) leaves it saying that rather than
  // showing a verdict from somewhere else.
  dirStatus = null;
  dirItems = [];
  renderDirMenu(false);
  vscodeApi.postMessage({ type: "dirComplete", value, reqId: ++dirReq, host: dirAskedHost });
}

function onDirCompletions(m: any): void {
  dirInFlight = false;
  const stale = m.reqId !== dirReq || (typeof m.host === "string" ? m.host : "") !== pickerHost();
  if (dirQueued !== null) { const v = dirQueued; dirQueued = null; askDirComplete(v); }
  if (stale) return;                                   // a newer keystroke already owns the field
  dirItems = Array.isArray(m.items) ? m.items : [];
  dirStatus = m.status || null;
  dirActive = -1;
  renderDirMenu(!!m.truncated);
}

function dirMenuOpen(): boolean {
  const menu = document.getElementById("picker-dir-menu");
  return !!menu && menu.style.display !== "none";
}

function closeDirMenu(): void {
  const menu = document.getElementById("picker-dir-menu");
  if (menu) { menu.style.display = "none"; menu.replaceChildren(); }
  dirActive = -1;
}

// The verdict is the one-glance version: what this path is right now, folded INTO the field's right
// end (the user 2026-08-11, trading the second row for an in-box hint) — the full sentence rides on
// hover. The menu underneath is the deeper level, and it only exists while there is something to choose.
function renderDirMenu(truncated: boolean): void {
  const hint = document.getElementById("picker-dir-hint");
  const said = dirStatus || !dirInFlight ? dirStatusHint(dirStatus)
    : { text: dirAskedHost ? `checking on ${dirAskedHost}…` : "checking…", cls: "", title: "" };
  const input = document.getElementById("picker-dir") as HTMLInputElement | null;
  if (hint) {
    hint.className = "picker-dir-hint" + (said.cls ? " " + said.cls : "");
    hint.textContent = said.text;
    hint.title = said.title;
    // the hint borrows the box's right end, so the typed text must stop where it starts — measured,
    // not guessed (the width varies by verdict and ellipsis cap; 0 = the field is folded away)
    const w = hint.offsetWidth;
    if (input) input.style.paddingRight = said.text && w ? w + 16 + "px" : "";
  }
  // and the FIELD itself carries it (the user 2026-07-29): a path that cannot work goes red where the
  // path is, not only in a hint at its edge, so the problem is visible without reading anything.
  if (input) {
    input.classList.toggle("bad", said.cls === "bad");
    input.classList.toggle("warn", said.cls === "warn");
  }
  const menu = document.getElementById("picker-dir-menu");
  if (!menu) return;
  menu.replaceChildren();
  // Only when you are IN the field (the user 2026-07-29): opening the + picker asks the kernel about the
  // prefilled path so the hint can vet it straight away, and that answer used to drop a folder
  // list over the dialog before anyone had touched it. The hint is the passive half; the menu is the
  // half you asked for by putting the cursor there.
  if (!dirItems.length || document.activeElement !== input) { menu.style.display = "none"; return; }
  dirItems.forEach((it, i) => {
    const row = el("div", "picker-dir-row" + (i === dirActive ? " active" : ""));
    row.textContent = it.name;
    row.dataset.path = it.path;
    row.addEventListener("mousedown", (e) => { e.preventDefault(); acceptDir(i); });   // mousedown: the input keeps focus
    menu.appendChild(row);
  });
  if (truncated) {
    const more = el("div", "picker-dir-more");
    more.textContent = "more: keep typing to narrow";
    menu.appendChild(more);
  }
  menu.style.display = "";
}

// Accept a completion: the field becomes that path with a trailing slash, which immediately asks for
// its children. Tab-tab-tab walks down a tree without a modal, and works the same on a remote host.
function acceptDir(i: number): void {
  const it = dirItems[i];
  const input = document.getElementById("picker-dir") as HTMLInputElement | null;
  if (!it || !input) return;
  input.value = it.path + "/";
  input.focus();
  askDirComplete(input.value);
}

function moveDirActive(delta: number): void {
  if (!dirItems.length) return;
  dirActive = nextDirActive(dirActive, delta, dirItems.length);
  renderDirMenu(false);
  const active = document.querySelector("#picker-dir-menu .picker-dir-row.active") as HTMLElement | null;
  active?.scrollIntoView({ block: "nearest" });
}

// Keys belong to the completer only while the dir field has focus — everywhere else in the picker
// they still walk the session list. Returns true when it handled the key.
function dirKey(e: KeyboardEvent): boolean {
  const input = document.getElementById("picker-dir");
  if (!input || document.activeElement !== input) return false;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!dirMenuOpen()) return false;
    e.preventDefault(); e.stopPropagation();
    moveDirActive(e.key === "ArrowDown" ? 1 : -1);
    return true;
  }
  if (e.key === "Tab" && dirItems.length) {
    e.preventDefault(); e.stopPropagation();
    acceptDir(dirActive >= 0 ? dirActive : 0);          // Tab with nothing chosen takes the first match
    return true;
  }
  if (e.key === "Enter" && dirActive >= 0) {
    e.preventDefault(); e.stopPropagation();
    acceptDir(dirActive);                                // a chosen row completes; it does NOT create
    return true;
  }
  if (e.key === "Escape" && dirMenuOpen()) {
    e.preventDefault(); e.stopPropagation();
    closeDirMenu();                                      // dismiss the menu, keep the picker open
    return true;
  }
  return false;
}

// ── creating a session, including the "that folder isn't there" fork ───────────────────────────────
// The create is one round trip: the OWNING kernel checks its own disk and either starts the session or
// says the directory is missing. A missing one is a question, not a failure — before this the kernel
// warned into a toast the "Opening…" cue was covering, so the create looked like it silently did
// nothing for 30 seconds (the user 2026-07-28). The request is remembered so "Create it" can re-send
// exactly the same create with mkdir set, host and backend included.
interface CreateReq { name: string; backend: string; dir: string; host: string; auth?: string }
let lastCreate: CreateReq | null = null;

function startCreate(req: CreateReq, mkdir = false): void {
  lastCreate = req;
  rememberDir(req.host, req.dir);   // what you used on that machine is the right prefill for it next time
  if (vscodeApi) vscodeApi.postMessage({ type: "createSession", ...req, ...(mkdir ? { mkdir: true } : {}) });
  closePicker();
  // …and the tab is THERE, with a live composer, before the kernel has answered. A remote session's tab
  // arrives host-prefixed, so that is the name the provisional one is matched against on arrival.
  openProvisional(req);
}

function onCreateDirMissing(m: any): void {
  // The folder question supersedes the provisional tab: this create is not going to land as asked. Keep
  // what was typed — "Create it and start" re-sends the very same create, so the text belongs to the
  // session that is about to exist, not to whatever tab we happen to fall back to.
  const held = dropProvisional();
  pendingCarry = [...held.queued, held.draft].filter(Boolean).join("\n\n");
  const req = lastCreate;
  showConfirm("That folder isn't there",
    createDirPrompt(String(m.name), (m.status || null) as DirStatus | null, String(m.dir || "")),
    [{ label: "Create it and start", value: "create" }, { label: "Edit the path", value: "edit" }],
    (v) => {
      if (!req) return;
      if (v === "create") { startCreate(req, true); return; }
      if (v === "edit") {
        openPicker();                    // reopen with the same name and path, cursor in the dir field
        const search = document.getElementById("picker-search") as HTMLInputElement | null;
        if (search) search.value = req.name;
        const dir = document.getElementById("picker-dir") as HTMLInputElement | null;
        if (dir) { dir.value = req.dir; dir.focus(); dir.select(); askDirComplete(dir.value); }
      }
    });
}

function openPicker(pick = false, prompt?: string, allowNew = false) {
  pickMode = pick;
  pickAllowNew = pick && allowNew;
  let overlay = document.getElementById("picker");
  if (!overlay) {
    overlay = el("div", "picker-overlay"); overlay.id = "picker";
    const box = el("div", "picker-box");
    const search = el("input", "picker-search") as HTMLInputElement;
    search.id = "picker-search";
    search.placeholder = "Search sessions…";
    search.spellcheck = false;
    // The phone keyboard's prediction bar had learned the user's session names and offered them over
    // this box — redundant next to the real session list right below it, and mistakable for romp UI
    // (the user 2026-08-12, Samsung keyboard). These are the standard opt-out HINTS: autocomplete
    // also keeps the browser's own form-history dropdown off (the dir field wears the same belt),
    // autocapitalize suits session names anyway ("dev", not "Dev"), autocorrect is iOS's spelling of
    // the same ask. A keyboard may still ignore them — its predictive-text setting is the only sure
    // switch, so nothing here may claim the bar is gone, only unrequested.
    search.setAttribute("autocomplete", "off");
    search.autocapitalize = "none";
    search.setAttribute("autocorrect", "off");
    search.addEventListener("input", () => { filterPicker(search.value); pickerError(null); });
    const errLine = el("div", "picker-error"); errLine.id = "picker-error";
    const list = el("div", "picker-list"); list.id = "picker-list";
    // hover and keyboard share one "active" row
    list.addEventListener("mouseover", (e) => {
      const row = (e.target as HTMLElement).closest(".picker-row");
      if (row) setActiveRow(row as HTMLElement);
    });
    // Directory for a NEW session — fixed once the session starts, so it's chosen here. Prefilled with the
    // gear's "Default directory" on open; the kernel-fed completer below offers real folders as you type
    // and the kernel expands ~ / $VARs and validates it exists. Hidden in pick-mode (choosing an existing
    // session). ONE suggestion surface, deliberately: this field once ALSO carried a native datalist of
    // every listed session's recorded dir, superseded by the completer (2026-07-28) but left wired — and
    // autocomplete="off" does NOT suppress a list-attribute dropdown in Chromium, so TWO boxes popped over
    // the field, the native one offering dirs that no longer exist (a session's dir outlives a rename; the
    // user 2026-08-11, offered a long-gone folder next to the real one). The completer asks the OWNING
    // kernel — the authoritative source — so the stale-capable history list is gone, not merely hidden.
    const dirWrap = el("div", "picker-dir");
    const dirField = el("span", "picker-dir-field");   // input + its in-box verdict share one box
    const dirInput = el("input", "picker-dir-input") as HTMLInputElement;
    dirInput.id = "picker-dir";
    dirInput.spellcheck = false;
    dirInput.placeholder = "New-session directory (blank = default)";
    dirInput.title = "Working directory for a NEW session — fixed once it starts. Blank uses the kernel's default. ~ and $VARs expand; type to complete folders (Tab walks into one), on this machine or the selected host.";
    dirInput.setAttribute("autocomplete", "off");   // belt: no browser dropdown of past form values either
    const browseBtn = el("button", "picker-browse") as HTMLButtonElement;
    browseBtn.type = "button"; browseBtn.textContent = "Browse…";
    browseBtn.title = "Pick a folder with the native dialog (opens on the kernel's machine — host-local)";
    browseBtn.addEventListener("click", () => { if (vscodeApi) vscodeApi.postMessage({ type: "browseDir" }); });
    // the completer's dropdown + the typed path's verdict, both fed by the owning kernel. The verdict
    // sits IN the box, right-aligned and non-editable (the user 2026-08-11); a press on it belongs to
    // the input underneath, so it hands the focus (and the caret's usual click-the-empty-end spot) over.
    const dirMenu = el("div", "picker-dir-menu"); dirMenu.id = "picker-dir-menu"; dirMenu.style.display = "none";
    const dirHint = el("span", "picker-dir-hint"); dirHint.id = "picker-dir-hint";
    dirHint.addEventListener("mousedown", (e) => {
      e.preventDefault(); dirInput.focus();
      dirInput.setSelectionRange(dirInput.value.length, dirInput.value.length);
    });
    dirInput.addEventListener("input", () => askDirComplete(dirInput.value));
    dirInput.addEventListener("focus", () => askDirComplete(dirInput.value));
    dirInput.addEventListener("blur", () => closeDirMenu());   // a row's mousedown preventDefaults, so it never blurs
    dirField.appendChild(dirInput); dirField.appendChild(dirHint);
    dirWrap.appendChild(dirField); dirWrap.appendChild(browseBtn);
    dirWrap.appendChild(dirMenu);
    // per-session BACKEND picker (the user 2026-06-23): a tmux | SDK segmented toggle, defaulting to the
    // gear's Default backend but overridable for THIS new session. Hidden in pick-mode (like dirWrap).
    const beWrap = el("div", "picker-backend");
    const beLabel = el("span", "picker-backend-label"); beLabel.textContent = "Backend";
    const mkBe = (val: string, txt: string, tip: string) => {
      const b = el("button", "picker-be-opt") as HTMLButtonElement;
      b.type = "button"; b.textContent = txt; b.title = tip; b.dataset.be = val;
      b.addEventListener("click", () => beWrap.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.toggle("sel", x === b)));
      return b;
    };
    beWrap.append(beLabel, mkBe("sdk", "SDK", "Runs via the Claude Agent SDK."),   // not "headless" — same full chat UI (the user 2026-07-12)
                  mkBe("tmux", "tmux", "Drives a real terminal pane (tmux)."));   // SDK first — the de-facto default (the user 2026-07-02)
    // the billing row exists only for SDK sessions — re-decide on every backend toggle
    beWrap.addEventListener("click", () => syncPickerAuth());
    // per-session BILLING row (the user 2026-08-08): Login | API key buttons when the selected host
    // offers both; with ONE real choice the same spot writes it out as plain text (the user
    // 2026-08-09) — see syncPickerAuth. Same segmented-toggle grammar as Backend above.
    const auWrap = el("div", "picker-backend picker-auth");
    auWrap.style.display = "none";   // hidden until a sessionList reply carries authAvail
    const auLabel = el("span", "picker-backend-label"); auLabel.textContent = "Billing";
    const mkAu = (val: string, txt: string, tip: string) => {
      const b = el("button", "picker-be-opt") as HTMLButtonElement;
      b.type = "button"; b.textContent = txt; b.title = tip; b.dataset.auth = val;
      b.addEventListener("click", () => auWrap.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.toggle("sel", x === b)));
      return b;
    };
    const auFixed = el("span", "picker-auth-fixed");   // the written-out single choice
    auFixed.style.display = "none";
    auWrap.append(auLabel, mkAu("login", "Login", "Bill this session to the machine's Claude login (subscription usage)."),
                  mkAu("key", "API key", "Bill this session to the API key the manager's environment carries (per-token)."),
                  auFixed);
    // per-session HOST picker (federation, the user 2026-07-02): local | each attached SSH host — the new
    // session is created BY that host's kernel (over its tunnel) and appears prefixed `host:name`. The
    // options are rebuilt on every open (hosts attach/detach live); the row hides with no hosts attached.
    const hostWrap = el("div", "picker-backend picker-host");
    const hostLabel = el("span", "picker-backend-label"); hostLabel.textContent = "Host";
    hostWrap.appendChild(hostLabel);
    const actions = el("div", "picker-actions");
    const newSess = el("button", "picker-action");
    newSess.id = "picker-new-btn";
    // "Create session", not "New session" (the user 2026-08-12): you already pressed New session to
    // open this dialog — the button here is the ACT, not the door. (The palette command that opens
    // the picker keeps the New session name; that one IS the door.)
    newSess.textContent = "✛ Create session";
    newSess.title = "create a fresh romp session, named by the search box, and open it as a tab";
    newSess.addEventListener("click", () => {
      // The search box doubles as the name field — no native dialog.
      const name = search.value.trim();
      if (!name) { pickerError("Type the new session's name in the box above first."); search.focus(); return; }
      if (!/^[A-Za-z0-9._-]+$/.test(name)) { pickerError("Session names: letters, digits, . _ - only."); search.focus(); return; }
      // A path that CANNOT work stops the create here, where the field is, instead of after a round trip
      // (the user 2026-07-29). Only when the kernel's answer is about what is typed RIGHT NOW: a reply
      // for older text is not evidence about this one, and the kernel re-validates anyway. A missing but
      // creatable directory is not refused — that is the "create it or edit it" offer, deliberately.
      const typed = dirInput.value.trim();
      if (dirStatus && dirStatus.value === typed && !dirStatus.isDir && !dirStatus.canCreate && typed) {
        pickerError(dirStatus.isFile ? "That path is a file, not a folder. Pick a folder for the session."
                                     : "That folder can't be reached on the selected host. Pick another path.");
        dirInput.focus(); dirInput.select();
        return;
      }
      // backend: this picker's toggle (defaults to the gear's "Default backend", overridable per session)
      const beSel = beWrap.querySelector(".picker-be-opt.sel") as HTMLElement | null;
      // host: local ("") or an attached SSH host — the federation manager routes createSession there.
      const hostSel = (hostWrap.querySelector(".picker-be-opt.sel") as HTMLElement | null)?.dataset.host || "";
      // billing: the picker's Billing row when it is showing (both choices real on that host); ""
      // omits the field and the kernel's own default stands (the user 2026-08-08)
      const auth = pickerAuthChoice();
      startCreate({ name, backend: beSel?.dataset.be || loadSettings().backend,
                    dir: dirInput.value.trim(), host: hostSel, ...(auth ? { auth } : {}) });
    });
    actions.appendChild(newSess);
    // CREATE controls first, the resume list LAST (the user 2026-08-12): typing in the name box
    // re-filters the list, whose height changes with every keystroke — with the list mid-dialog the
    // controls below it jumped around exactly when you were reaching for them. The stable, most-used
    // half (name → directory → backend → billing → host → New session) now holds still at the top,
    // and the list — the occasional alternative, not the main act — grows and shrinks harmlessly at
    // the bottom, under a label that says what it is. In pick-mode the list IS the dialog, so the
    // label hides with the create rows (openPicker below).
    const altHead = el("div", "picker-alt-head");
    altHead.id = "picker-alt-head";
    // "(last 30 days)" is the kernel's real reach — PICKER_WINDOW in kernel.py; picker-order.test.ts
    // holds the two in step so a widened window can't leave this label lying.
    altHead.textContent = "Or reopen an existing session (last 30 days)";
    box.appendChild(search);
    box.appendChild(errLine);
    box.appendChild(dirWrap);
    box.appendChild(beWrap);
    box.appendChild(auWrap);
    box.appendChild(hostWrap);
    box.appendChild(actions);
    box.appendChild(altHead);
    box.appendChild(list);
    overlay.appendChild(box);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closePicker(); });
    document.body.appendChild(overlay);
    document.addEventListener("keydown", pickerKey);
    // SHORT-WINDOW FOLD (the user 2026-08-10, Chrome on a phone): with the on-screen keyboard up, the
    // picker's lower rows sat behind it and nothing gave. The shell sizes this lifted iframe to the
    // VISIBLE height (--app-h ← the top-level visualViewport, which the keyboard shrinks), so the
    // keyboard opening/closing lands here as this window's own resize — the exact event to key on, no
    // timers, no UA sniffing. Short window → kb-tight tightens the frame and lets the RESUME LIST
    // give way (styles.css; the user 2026-08-12, after two folds that each hid the wrong half): the
    // keyboard is up because a new session's name is being typed, so every create control stays on
    // screen and the list collapses to the leftover height. The same resize expands the list back the
    // moment there is room again (a genuinely small screen folds too, which is the right call there
    // as well).
    const kbFit = () => document.getElementById("picker")?.classList.toggle("kb-tight", window.innerHeight < 480);
    window.addEventListener("resize", kbFit);
    kbFit();
  }
  overlay.style.display = "flex";
  signalPickerOverlay(true);   // lift the chat iframe full-window so the picker covers the whole screen
  const actions = overlay.querySelector(".picker-actions") as HTMLElement | null;
  if (actions) actions.style.display = pick ? "none" : "";
  const altHeadEl = document.getElementById("picker-alt-head");
  if (altHeadEl) altHeadEl.style.display = pick ? "none" : "";   // pick-mode: the list IS the dialog, not an alternative
  const dirWrap = overlay.querySelector(".picker-dir") as HTMLElement | null;
  if (dirWrap) dirWrap.style.display = pick ? "none" : "";   // dir only matters when creating, not picking
  const beWrapEl = overlay.querySelector(".picker-backend:not(.picker-host):not(.picker-auth)") as HTMLElement | null;
  if (beWrapEl) {   // reset the backend toggle to the gear default each open (overridable for this session)
    beWrapEl.style.display = pick ? "none" : "";
    const def = loadSettings().backend || "tmux";
    beWrapEl.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.toggle("sel", (x as HTMLElement).dataset.be === def));
  }
  const auWrapEl = overlay.querySelector(".picker-auth") as HTMLElement | null;
  if (auWrapEl) {   // fresh open: forget last time's pick + availability; the local sessionList reply re-arms it
    auWrapEl.style.display = "none";
    auWrapEl.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.remove("sel"));
  }
  const hostWrapEl = overlay.querySelector(".picker-host") as HTMLElement | null;
  if (hostWrapEl) {   // rebuild the host options each open (attach/detach is live); hide with no remotes
    const hosts: string[] = ((window as any).__rompFed?.hosts?.() || []) as string[];
    hostWrapEl.style.display = pick || !hosts.length ? "none" : "";
    hostWrapEl.querySelectorAll(".picker-be-opt").forEach((x) => x.remove());
    for (const h of ["", ...hosts]) {
      const b = el("button", "picker-be-opt" + (h === "" ? " sel" : "")) as HTMLButtonElement;
      // This machine wears its REAL name, like the remote options wear theirs (the user 2026-08-12):
      // "local" made the row read as one named machine plus an unnamed one. The name rides the local
      // sessionList reply (selfHost), so the very first open may briefly say "local" until it lands —
      // the handler below relabels the button in place.
      b.type = "button"; b.textContent = h || localSelfHost || "local"; b.dataset.host = h;
      b.title = h ? `Create the session on ${h} (its kernel spawns it; the tab shows as ${h}:name).`
                  : "Create the session on this machine.";
      b.addEventListener("click", () => {
        hostWrapEl.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.toggle("sel", x === b));
        // the native Browse… dialog opens on the LOCAL kernel's screen, so it can't stand for a remote
        // machine's disk; the inline completer can — it asks that host's own kernel (the user 2026-07-28)
        applyBrowseState(h);
        const dirIn = document.getElementById("picker-dir") as HTMLInputElement | null;
        if (dirIn) dirIn.placeholder = h ? `New-session directory on ${h} (blank = its default)` : "New-session directory (blank = default)";
        // …and the SESSIONS listed above belong to that machine now too (the user 2026-07-29): the list
        // becomes that host's, so a remote session can be reopened or revived from here. Its rows arrive
        // host-prefixed, so a click routes straight back to the kernel that owns them.
        requestSessionList(h);
        const lst = document.getElementById("picker-list");
        if (lst) lst.replaceChildren(Object.assign(el("div", "picker-more"),
          { textContent: h ? `loading ${h}'s sessions…` : "loading sessions…" }));
        // the completions on screen belong to the host that just stopped being selected
        closeDirMenu();
        // …and so does the PATH: this machine's default is meaningless on another box. Swap in what was
        // last used there (blank asks that kernel for its own default) and re-vet it against that host,
        // so a path that cannot exist there says so before anything is created (the user 2026-07-29).
        if (dirIn) { dirIn.value = dirPrefill(h); askDirComplete(dirIn.value); }
      });
      hostWrapEl.appendChild(b);
    }
  }
  applyBrowseState("");   // fresh open defaults back to local — enabled unless this kernel has no desktop
  const di = document.getElementById("picker-dir") as HTMLInputElement | null;
  // the host row resets to local on every open, so this is the local prefill: what you last used here,
  // else the kernel's persisted default (file→env; localStorage is a same-tab cache)
  if (di) di.value = dirPrefill("");
  closeDirMenu();                       // a previous open's folder list is not this one's
  if (di && !pick) askDirComplete(di.value);   // the status line says what the prefilled path is, before anything is typed
  // In a filtered view (#only=<tag>), a new session created here would vanish from the view unless its name
  // matches. Prefill the name box with the tag so what you launch stays in view (the user 2026-07-15) —
  // editable: clear it to launch outside the filter on purpose. Only when creating is possible here (the
  // New-session button in create mode, or the New-session row when pickAllowNew), never in pure resume.
  const only = (!pick || pickAllowNew) ? onlyTag() : null;
  // Seed the name box ONLY for a single-prefix filter: with a list (`#only=api,tests`) there is no
  // one prefix a new session must wear, and "api,tests-" would be nonsense (the user 2026-07-16).
  const seed = only && !only.includes(",") ? only + "-" : "";
  const s = document.getElementById("picker-search") as HTMLInputElement | null;
  if (s) {
    s.value = seed;
    s.placeholder = prompt || "Search sessions, or type a new session's name…";
    s.focus();
    if (seed) s.setSelectionRange(seed.length, seed.length);   // cursor after the tag prefix, ready to type
  }
  filterPicker(seed); // reset row visibility; arm the New-session button for the (possibly seeded) value
  pickerError(null);
  requestSessionList("");   // the Host row resets to local on open, so the list starts local too
}

// ---- revive loader (the user 2026-07-05) ----
// Reviving a dead session takes seconds (relaunch + resume), and the Revive click used to give ZERO
// feedback. Per the repo's loading rule the FIRST thing up is the romp loader — spinning swirl +
// wordmark + three pulsing dots, the same .rl-* treatment as the boot/pane loaders (their styles are
// already on this page) — over the chat, with a "reviving <name>…" caption. EVENT-cleared: the
// kernel's focus for that sid (revive succeeded) or reviveFailed (the loader morphs into the error
// notice — fail loudly, never silently back to nothing). A 60s backstop can never trap the user.
let revivePending: string | null = null;
let reviveBackstop: number | undefined;

function clearReviveLoader() {
  revivePending = null;
  if (reviveBackstop !== undefined) { clearTimeout(reviveBackstop); reviveBackstop = undefined; }
  document.getElementById("revive-loader")?.remove();
}

function showReviveLoader(id: string, name: string) {
  clearReviveLoader();
  revivePending = id;
  const o = el("div", ""); o.id = "revive-loader";
  const inner = el("div", "rl-in");
  const word = el("div", "rl-word");
  const r = el("span", ""); (r as HTMLElement).style.color = "#1EA1EB"; r.textContent = "R";
  const swirl = el("img", "rl-o") as HTMLImageElement;
  swirl.src = mediaSrc("romp-swirl-o.svg"); swirl.alt = "o"; swirl.onerror = () => swirl.remove();
  const mm = el("span", ""); (mm as HTMLElement).style.color = "#54B204"; mm.textContent = "m";
  const p = el("span", ""); (p as HTMLElement).style.color = "#4EA8A9"; p.textContent = "p";
  word.append(r, swirl, mm, p);
  const dots = el("div", "rl-dots");
  dots.append(el("i", ""), el("i", ""), el("i", ""));
  const cap = el("div", "revive-cap");
  cap.textContent = `reviving “${name}”…`;
  inner.append(word, dots, cap);
  o.appendChild(inner);
  document.body.appendChild(o);
  reviveBackstop = window.setTimeout(
    () => showReviveError(name, "still waiting — the resume may be stuck; check the kernel log"), 60000);
}

function showReviveError(name: string, text: string) {
  // Morph the loader into the failure notice (fail loudly); build the overlay if it's already gone.
  revivePending = null;
  if (reviveBackstop !== undefined) { clearTimeout(reviveBackstop); reviveBackstop = undefined; }
  let o = document.getElementById("revive-loader");
  if (!o) { o = el("div", ""); o.id = "revive-loader"; document.body.appendChild(o); }
  o.replaceChildren();
  const box = el("div", "revive-err");
  const msg = el("div", "revive-err-text");
  msg.textContent = `Couldn’t revive “${name}”: ${text}`;
  const btn = el("button", "revive-err-dismiss");
  btn.textContent = "Dismiss";
  btn.addEventListener("click", () => clearReviveLoader());
  box.append(msg, btn);
  o.appendChild(box);
}

// ---- in-webview confirm dialog (replaces the host's native modals) ----
// One overlay at a time; Esc / backdrop click cancels (cb(null)). Buttons carry
// a value handed to cb. Reuses the picker overlay's backdrop styling.
let confirmCb: ((v: string | null) => void) | null = null;
function showConfirm(title: string, detail: string, buttons: Array<{ label: string; value: string; danger?: boolean }>, cb: (v: string | null) => void) {
  closeConfirm(null);   // a newer dialog replaces (and cancels) an older one
  confirmCb = cb;
  const overlay = el("div", "picker-overlay confirm-overlay"); overlay.id = "confirm";
  const box = el("div", "picker-box confirm-box");
  const h = el("div", "confirm-title"); h.textContent = title;
  const d = el("div", "confirm-detail"); d.textContent = detail;
  const actions = el("div", "confirm-actions");
  for (const b of buttons) {
    const btn = el("button", "picker-action confirm-btn" + (b.danger ? " danger" : ""));
    btn.textContent = b.label;
    btn.addEventListener("click", () => closeConfirm(b.value));
    actions.appendChild(btn);
  }
  box.appendChild(h); box.appendChild(d); box.appendChild(actions);
  overlay.appendChild(box);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeConfirm(null); });
  document.body.appendChild(overlay);
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); closeConfirm(null); } };
  (overlay as any)._key = onKey;
  document.addEventListener("keydown", onKey, true);
  (actions.firstElementChild as HTMLElement | null)?.focus();
}
// THE FORK MODAL (the user 2026-08-13): fork this conversation into a NEW parallel session — from just
// before a given user message (the bubble's fork button) or from the tip (the palette command, uuid "").
// One small dialog on the confirm chrome: a name box prefilled "<session>-fork" (editable), Fork/Cancel.
// The kernel owns the mechanics (forkSession op); the provisional tab is the instant acknowledgement,
// joined by NAME when the real session lands — exactly the picker-create flow.
function showForkPrompt(sid: string, uuid: string): void {
  const sess = sessions.get(sid);
  const base = (sess?.name || "session").replace(/[^A-Za-z0-9._-]/g, "-");
  document.getElementById("fork-prompt")?.remove();
  const overlay = el("div", "picker-overlay confirm-overlay"); overlay.id = "fork-prompt";
  const box = el("div", "picker-box confirm-box");
  const h = el("div", "confirm-title"); h.textContent = "Fork session";
  const d = el("div", "confirm-detail");
  d.textContent = uuid
    ? "A new session continues from just before this message; this one is untouched."
    : "A new session continues this whole conversation; this one is untouched.";
  const input = document.createElement("input");
  input.type = "text"; input.className = "fork-name"; input.value = base + "-fork";
  input.setAttribute("autocapitalize", "off"); input.setAttribute("autocomplete", "off");
  input.setAttribute("autocorrect", "off"); input.setAttribute("spellcheck", "false");
  const actions = el("div", "confirm-actions");
  const cancel = el("button", "picker-action confirm-btn"); cancel.textContent = "Cancel";
  const create = el("button", "picker-action confirm-btn"); create.textContent = "Fork";
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey, true); };
  const go = () => {
    const name = input.value.trim();
    if (!/^[A-Za-z0-9._-]+$/.test(name)) { input.classList.add("bad"); input.focus(); return; }
    vscodeApi?.postMessage({ type: "forkSession", id: sid, uuid, name });
    close();
    openProvisional({ name, backend: "sdk", dir: "", host: hostOf(sid) });
  };
  cancel.addEventListener("click", close);
  create.addEventListener("click", go);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  input.addEventListener("input", () => input.classList.remove("bad"));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  box.append(h, d, input, actions);
  actions.append(cancel, create);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  document.addEventListener("keydown", onKey, true);
  input.focus(); input.setSelectionRange(0, input.value.length);
}

// THE MCP PANEL (the user 2026-08-05). `/mcp` in a romp session used to be a dead end: the CLI's own
// panel is an interactive TUI an SDK-driven session cannot render, so it replied "use a terminal". The
// SDK exposes the same facts and repairs as control requests (get_mcp_status / toggle_mcp_server /
// reconnect_mcp_server), so this renders them: one row per server with its status dot, tool count, the
// error when it failed, and the two actions. Every action refetches — the panel only ever shows the
// CLI's own status, never an optimistic guess. Reuses the confirm overlay's chrome (no new styles).
let mcpPanelSid: string | null = null;
function openMcpPanel(sid: string): void {
  mcpPanelSid = sid;
  document.getElementById("mcp-panel")?.remove();
  const overlay = el("div", "picker-overlay confirm-overlay"); overlay.id = "mcp-panel";
  const box = el("div", "picker-box confirm-box mcp-box");
  const h = el("div", "confirm-title"); h.textContent = "MCP servers";
  const body = el("div", "confirm-detail mcp-list");
  body.textContent = "Loading…";
  const actions = el("div", "confirm-actions");
  const closeBtn = el("button", "picker-action confirm-btn"); closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => { mcpPanelSid = null; overlay.remove(); document.removeEventListener("keydown", onKey, true); });
  actions.appendChild(closeBtn);
  box.append(h, body, actions);
  overlay.appendChild(box);
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); closeBtn.click(); } };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeBtn.click(); });
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(overlay);
  loadMcpPanel(sid, body);
}

function loadMcpPanel(sid: string, body: HTMLElement): void {
  fetch(kernelUrl("/mcp?sid=" + encodeURIComponent(sid)), { cache: "no-store" })
    // a kernel from before this panel 404s here, and .json() on the error page read as a bare "parse
    // error" (the user 2026-08-05) — name the actual situation instead
    .then((r) => { if (!r.ok) throw new Error("this romp kernel predates the MCP panel — restart romp to update it"); return r.json(); })
    .then((d) => {
      if (mcpPanelSid !== sid) return;   // panel closed (or reopened for another tab) while loading
      body.textContent = "";
      const servers: any[] = Array.isArray(d?.servers) ? d.servers : [];
      // FAIL LOUDLY: a refusal (tmux session, disconnected CLI) is named, never an empty list that
      // reads as "no servers configured".
      if (d?.error) {
        const e = el("div", "mcp-err"); e.textContent = String(d.error); body.appendChild(e);
      }
      if (!servers.length) {
        if (!d?.error) { const n = el("div", "mcp-none"); n.textContent = "No MCP servers configured for this session."; body.appendChild(n); }
        return;
      }
      for (const srv of servers) {
        const row = el("div", "mcp-row");
        const dot = el("span", "mcp-dot mcp-" + String(srv.status || "unknown"));
        const name = el("span", "mcp-name"); name.textContent = String(srv.name || "?");
        const st = el("span", "mcp-status"); st.textContent = String(srv.status || "unknown");
        const tools = Array.isArray(srv.tools) ? srv.tools.length : null;
        const meta = el("span", "mcp-meta");
        meta.textContent = [tools != null ? tools + " tool" + (tools === 1 ? "" : "s") : "",
                            srv.scope ? String(srv.scope) : ""].filter(Boolean).join(" · ");
        row.append(dot, name, st, meta);
        const disabled = String(srv.status) === "disabled";
        const act = (action: string, enabled: boolean, label: string, busy: string) => {
          const b = el("button", "mcp-act") as HTMLButtonElement;
          b.textContent = label;
          b.addEventListener("click", () => {
            b.disabled = true; b.textContent = busy;   // acknowledge before the round-trip
            vscodeApi?.postMessage({ type: "mcpAction", id: sid, server: srv.name, action, enabled });
          });
          row.appendChild(b);
        };
        if (!disabled) act("reconnect", true, "Reconnect", "Reconnecting…");
        act("toggle", disabled, disabled ? "Enable" : "Disable", disabled ? "Enabling…" : "Disabling…");
        if (srv.error) { const er = el("div", "mcp-err"); er.textContent = String(srv.error); row.appendChild(er); }
        body.appendChild(row);
      }
    })
    .catch((e) => { if (mcpPanelSid === sid) body.textContent = "Couldn't load MCP status: " + ((e && e.message) || e); });
}

function closeConfirm(value: string | null) {
  const o = document.getElementById("confirm");
  if (o) {
    const k = (o as any)._key;
    if (k) document.removeEventListener("keydown", k, true);
    o.remove();
  }
  const cb = confirmCb;
  confirmCb = null;
  if (cb) cb(value);
}

// Inline validation message under the search box (null hides it).
function pickerError(msg: string | null) {
  const e = document.getElementById("picker-error");
  if (!e) return;
  e.textContent = msg || "";
  e.classList.toggle("show", !!msg);
}

function pickerVisible(): boolean {
  const o = document.getElementById("picker");
  return !!o && o.style.display !== "none";
}

function closePicker() {
  const o = document.getElementById("picker");
  if (o) o.style.display = "none";
  signalPickerOverlay(false);   // release the full-window lift — the chat iframe returns to its pane
  if (pickMode) {
    if (vscodeApi) vscodeApi.postMessage({ type: "pickResult", id: null });
    pickMode = false;
  }
}

function pickerRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll("#picker-list .picker-row:not(.hidden)")) as HTMLElement[];
}

function setActiveRow(row: HTMLElement | null) {
  document.querySelectorAll("#picker-list .picker-row.active").forEach((r) => r.classList.remove("active"));
  if (row) { row.classList.add("active"); row.scrollIntoView({ block: "nearest" }); }
  syncNewButton();   // an active row and an armed New-session button are mutually exclusive Enter targets
}

// Arm the "✛ Create session" button exactly when Enter should CREATE: create mode (the + flow), a name
// typed, and no row explicitly active. A typed name belongs to "create" (the user 2026-07-28) — it
// must never be re-routed onto a fuzzy match — so matches don't steal the arm; stepping onto a row
// with ArrowDown (or hovering one) is the explicit act that hands Enter to that row instead.
function syncNewButton() {
  const btn = document.getElementById("picker-new-btn");
  if (!btn) return;
  const creating = (btn.closest(".picker-actions") as HTMLElement | null)?.style.display !== "none";
  const q = (document.getElementById("picker-search") as HTMLInputElement | null)?.value.trim() || "";
  const rowActive = !!document.querySelector("#picker-list .picker-row.active:not(.hidden)");
  btn.classList.toggle("active", creating && !!q && !rowActive);
}

function moveActive(delta: number) {
  const rows = pickerRows();
  if (!rows.length) return;
  const cur = rows.findIndex((r) => r.classList.contains("active"));
  // ArrowUp from the TOP row steps back OUT of the match list: with a typed name in create mode that
  // re-arms the New-session button (the way back to "Enter creates"), instead of wrapping to the
  // bottom row (the user 2026-07-28).
  if (cur === 0 && delta < 0) {
    const btn = document.getElementById("picker-new-btn");
    const creating = !!btn && (btn.closest(".picker-actions") as HTMLElement | null)?.style.display !== "none";
    const q = (document.getElementById("picker-search") as HTMLInputElement | null)?.value.trim() || "";
    if (creating && q) { setActiveRow(null); return; }
  }
  const next = cur < 0 ? (delta > 0 ? 0 : rows.length - 1) : (cur + delta + rows.length) % rows.length;
  setActiveRow(rows[next]);
}

function pickerKey(e: KeyboardEvent) {
  const o = document.getElementById("picker");
  if (!o || o.style.display === "none") return;
  // the directory field's completer owns the arrows / Tab / Enter while it is focused, so walking the
  // folder list can't also walk the session list underneath it
  if (dirKey(e)) return;
  if (e.key === "Escape") closePicker();
  else if (e.key === "ArrowDown") { e.preventDefault(); moveActive(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveActive(-1); }
  else if (e.key === "Enter") {
    e.preventDefault();
    // Precedence (the user 2026-07-28): an EXPLICITLY active row (ArrowDown/hover) wins; else an armed
    // New-session button (create mode + typed name) creates EXACTLY what was typed — never a fuzzy
    // match; else (empty box) fall back to the first listed row.
    const active = document.querySelector("#picker-list .picker-row.active:not(.hidden)") as HTMLElement | null;
    if (active) { active.click(); return; }
    const btn = document.getElementById("picker-new-btn");
    if (btn?.classList.contains("active")) { btn.click(); return; }
    const first = pickerRows()[0];
    if (first) first.click();
  }
}

// The kernel's real default new-session directory (its serve cwd, ~-ified), from the sessionList payload —
// prefilled into the dir field when there's no gear default, so "the default path is written in there".
let kernelDefaultDir = "";
// This machine's name as the kernel's peers know it (_self_host — short hostname, ROMP_HOST_NAME
// override), from the same payload. The + picker's Host row labels its first option with it, so the
// row reads as a list of machines by name rather than named hosts plus a "local" (the user 2026-08-12).
let localSelfHost = "";
// Is this session already an open tab in THIS dashboard? (loaded session, or a not-yet-loaded placeholder tab
// the kernel's order carries.) The + picker uses it to hide sessions you can already reach by a tab-click.
function isOpenTab(id: string): boolean {
  return sessions.has(id) || order.includes(id) || tabMeta.has(id);
}

// Foot of the list: how far back the picker reaches, so an older session's absence has a stated reason
// rather than looking like the search failed. Not a .picker-row, so filterPicker and the keyboard row-walk
// both skip it. There is no loading state to show — the kernel sends the whole 30 days in one reply, which
// measured ~78ms cold and ~4ms cached once fork detection is off (the user 2026-07-24, who asked for the
// list to just be there rather than paged in as you scroll).
function renderPickerFootRow() {
  const list = document.getElementById("picker-list");
  if (!list) return;
  list.querySelector(".picker-more")?.remove();
  const more = el("div", "picker-more");
  more.textContent = "showing the last 30 days";
  list.appendChild(more);
}

function renderPicker(items: any[]) {
  const list = document.getElementById("picker-list");
  if (!list) return;
  const keepScroll = list.scrollTop;    // a deep list swaps the rows under the user mid-scroll
  list.replaceChildren();
  const mkRow = (it: any): HTMLElement => {
    const row = el("div", "picker-row" + (it.running ? " running" : ""));
    row.dataset.search = (it.name + " " + (it.summary || "")).toLowerCase();
    const top = el("div", "picker-row-top");
    const name = el("span", "picker-name");
    name.replaceChildren(...hostNameNodes(it.name, it.id));
    if (it.color && it.color.bg) name.style.color = it.color.bg;
    const time = el("span", "picker-time");
    if (it.running) {   // a live session (SDK/tmux backend) whose tab is closed → a green "running" badge
      time.classList.add("picker-running-badge");
      time.append(el("span", "picker-run-dot"), document.createTextNode("running"));
    } else {
      time.textContent = it.time;
    }
    top.appendChild(name);
    top.appendChild(time);
    row.appendChild(top);
    if (it.summary) {
      const sum = el("div", "picker-summary");
      sum.textContent = it.summary;
      row.appendChild(sum);
    }
    row.addEventListener("click", () => {
      if (pickMode) {
        if (vscodeApi) vscodeApi.postMessage({ type: "pickResult", id: it.id, name: it.name });
        pickMode = false; // so closePicker doesn't also post a cancel
      } else if (vscodeApi) {
        vscodeApi.postMessage({ type: "openSession", id: it.id });
      }
      closePicker();
    });
    return row;
  };
  const label = (txt: string): HTMLElement => { const l = el("div", "picker-group-label"); l.textContent = txt; return l; };
  // In the + (open) flow, HIDE sessions you already have open as a tab — they're a tab-click away, so listing
  // them is just noise (the user 2026-07-15). What's left splits into RUNNING (a live backend whose tab you
  // closed — reopen it, shown first with a badge) then the closed/aged ones you can revive. In PICK mode
  // (choosing a target session) nothing is hidden — you may well want an already-open one.
  if (pickMode) {
    for (const it of items) list.appendChild(mkRow(it));
  } else {
    const avail = items.filter((it) => !isOpenTab(it.id));
    const running = avail.filter((it) => it.running);
    const rest = avail.filter((it) => !it.running);
    if (running.length) { list.appendChild(label("Running — reopen")); for (const it of running) list.appendChild(mkRow(it)); }
    if (rest.length) { if (running.length) list.appendChild(label("Recent")); for (const it of rest) list.appendChild(mkRow(it)); }
  }
  if (!list.children.length && pickerListHost) {
    // a machine romp can reach but which has no sessions in the window: say that, or an empty list reads
    // as a search that failed or a request that never answered
    const none = el("div", "picker-more");
    none.textContent = `no sessions on ${pickerListHost} in the last 30 days`;
    list.appendChild(none);
  }
  if (pickAllowNew) {
    const row = el("div", "picker-row picker-new");
    row.dataset.search = "new session";
    const top = el("div", "picker-row-top");
    const label = el("span", "picker-name"); label.textContent = "+ New session…";
    top.appendChild(label);
    row.appendChild(top);
    row.addEventListener("click", () => {
      if (vscodeApi) vscodeApi.postMessage({ type: "pickResult", createNew: true });
      pickMode = false;
      closePicker();
    });
    list.appendChild(row);
  }
  // (The recent-dirs datalist that was refilled here is gone — the kernel-fed completer is the ONE
  // suggestion surface for the dir field; see the field's construction for the two-boxes story.)
  // Prefill the dir field with the kernel's real default path once it arrives (only if untouched + no gear
  // default) — so the actual default is written in there as an editable starting point (the user 2026-06-23).
  const di = document.getElementById("picker-dir") as HTMLInputElement | null;
  if (di && !di.value) di.value = kernelDefaultDir || loadSettings().defaultDir || "";
  renderPickerFootRow();          // the "last 30 days" note below the rows
  list.scrollTop = keepScroll;    // a kernel push re-renders the list; don't yank a scrolled-back user to the top
  // Re-apply the current filter (the list may refresh while the user is mid-
  // type) — it also sets the active row / arms the New-session button.
  const s = document.getElementById("picker-search") as HTMLInputElement | null;
  filterPicker(s?.value || "");
}

function filterPicker(q: string) {
  const query = q.toLowerCase();
  // a live search collapses the Running/Recent split into one flat result list — hide the group labels (CSS)
  document.getElementById("picker-list")?.classList.toggle("searching", !!query);
  document.querySelectorAll("#picker-list .picker-row").forEach((r) => {
    const row = r as HTMLElement;
    const hit = !query || (row.dataset.search || "").includes(query);
    row.classList.toggle("hidden", !hit);
  });
  // In the + (create) flow a TYPED name means "create this" (the user 2026-07-28): no row
  // auto-activates, so a bare Enter lands on the armed New-session button and creates EXACTLY the
  // typed name — reopening one of the matches shown below takes an explicit ArrowDown (or hover/click).
  // With an empty box (or in pick mode, where creating isn't on offer) the first row still
  // auto-highlights so Enter opens it, as before. setActiveRow syncs the button's armed state.
  const btn = document.getElementById("picker-new-btn");
  const creating = !!btn && (btn.closest(".picker-actions") as HTMLElement | null)?.style.display !== "none";
  setActiveRow(creating && q.trim() ? null : pickerRows()[0] ?? null);
}

function nearBottom(c: HTMLElement): boolean {
  return c.scrollHeight - c.scrollTop - c.clientHeight < 80;
}

function cssEscape(s: string): string {
  return typeof (window as any).CSS?.escape === "function" ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
}

// Scroll the thread to the event carrying this source JSONL uuid (the deep-link
// anchor) and flash it. Multiple events can share one line's uuid (a multi-block
// assistant turn) — target the first. If it's not rendered yet, stash it as
// pendingAnchor for the next render pass to retry.
function scrollToAnchor(uuid: string): boolean {
  anchorPendingOlder = false;            // fresh attempt; set true below only if we kick off an older-history fetch
  if (!uuid) return false;
  const v = activeId ? views.get(activeId) : null;
  // Resolve BY ID against the rendered turns: the atom uuid (every turn) OR the postal message id (postal
  // cards also carry data-mid). A postal deep-link — the timeline connector / feed delegation — passes the
  // message id; matching it to the card's data-mid lands on the EXACT message, not a nearest-time guess that
  // can drift onto an unrelated turn that happens to be near in time (the user 2026-06-20).
  // data-mids is the UNHYDRATED case: a turn whose message ids the kernel could not resolve into cards
  // still carries them, so the arc into it stays landable rather than pointing at a turn that cannot
  // answer to it (the user 2026-07-23). `~=` matches one whitespace-separated token, and a message id
  // never contains whitespace.
  let target = (v?.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`)
                || v?.el.querySelector(`.turn[data-mid="${cssEscape(uuid)}"]`)
                || v?.el.querySelector(`.turn[data-mids~="${cssEscape(uuid)}"]`)) as HTMLElement | null;
  // Deep-link into history the window doesn't currently cover (the head/tail folded into a spacer): find the
  // event, render a fresh window AROUND its unit, then re-query — the "load it when you jump there" behaviour.
  // (No match anywhere → genuinely off the active path; stash for the next render pass.)
  if (!target && v && activeId) {
    const s = sessions.get(activeId);
    // resultUuid too: an ANSWERED AskUserQuestion turn is anchored by its answer line's uuid
    // (renderEvent's data-uuid — the uuid the timeline emits for the decision), which no event
    // carries as its OWN uuid, so a uuid/mid-only lookup missed it and this recovery never ran.
    const idx = s ? s.events.findIndex((e) => e.uuid === uuid || (e as { mid?: string }).mid === uuid
                                       || (e as { resultUuid?: string }).resultUuid === uuid) : -1;
    if (s && idx >= 0) {
      const items = displayItems(s);
      let u = items.findIndex((it) => it.kind === "toolgroup" ? it.indices.includes(idx) : it.index === idx);
      if (u < 0) u = Math.max(0, items.findIndex((it) => itemFirstEvent(it) >= idx));
      // The anchor can live INSIDE a collapsed tool run: the folded line carries only the run's FIRST
      // uuid, so the re-render below could never surface a mid-run member — the click honest-failed
      // "pointer-not-rendered" with the message sitting right behind the fold (the user 2026-07-16: a
      // Blocked card anchored to its session's pending AskUserQuestion tool atom). The click asked to
      // SEE that message: expand the run, so the re-render gives the member its own turn to land on.
      const hit = items[u];
      if (hit && hit.kind === "toolgroup" && hit.indices.includes(idx))
        expandedGroups.add(toolGroupKey(s.events[hit.indices[0]]));
      const working = s.status.state === "working" || s.status.state === "compacting";
      renderWindowItems(v, s, items, Math.max(0, u - WINDOW_RADIUS), Math.min(items.length, u + WINDOW_RADIUS), working);
      // Re-query with the SAME three selectors the first lookup used. data-mids was missing here, so an
      // unhydrated postal turn (whose message ids live only in data-mids) could be found in the events,
      // have its window rendered — and then still honest-fail "pointer-not-rendered" on the re-query.
      target = (v.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`)
                || v.el.querySelector(`.turn[data-mid="${cssEscape(uuid)}"]`)
                || v.el.querySelector(`.turn[data-mids~="${cssEscape(uuid)}"]`)) as HTMLElement | null;
    } else if (s && (s.headFrom ?? 0) > 0) {
      // The anchor is OLDER than the resident tail — the chat ships only WIRE_TAIL events and streams older
      // history in on demand, so a deep-link to a message past the tail had nothing to match and honest-failed
      // with "couldn't locate" even though it's in the transcript (the user 2026-06-27). Fetch the next older
      // chunk re-anchored on THIS uuid; chatHead lands on it when it arrives, and if it's STILL further back
      // this same branch fires again — a fetch-until-resident loop that terminates when headFrom reaches 0.
      // A fetch ALREADY IN FLIGHT counts as pending too (the user 2026-07-20): pendingAnchor re-attempts
      // run on every push re-render (0.5–3s), so a mid-fetch attempt used to fall through to
      // "pointer-not-rendered" and toast a false "couldn't locate" while the chunk that would land it was
      // still on the wire. Re-point the arrival re-land at THIS uuid and keep waiting.
      // …and the arrival becomes a DEEP-LINK land, so drop any keep-offset a scroll-back fetch left behind:
      // in the short-circuit branch the in-flight fetch may well BE a requestOlder, whose stale offset would
      // otherwise silently demote this click to a position restore.
      if (fetchOlderForAnchor(activeId, uuid) || loadingOlder.has(activeId)) {
        pendingOlderAnchor.set(activeId, uuid);
        pendingOlderKeepY.delete(activeId);
        pendingAnchor = uuid; anchorPendingOlder = true; landTrail.push("pointer-fetch-older"); return false;
      }
    }
  }
  if (!target) { pendingAnchor = uuid; landTrail.push("pointer-not-rendered"); return false; }
  // KIND GUARD — the robust half of "title clicks always land on the originating
  // message". Upstream producers substitute a reply uuid when the prompt line is
  // off the active path (compaction orphans it), so a prompt-intent anchor can
  // arrive pointing at an ASSISTANT turn. Checked against the rendered DOM here,
  // the one place that can't be fooled: a prompt-intent ("user") anchor must land
  // on the originating MESSAGE — a user turn OR a peer's postal card (a peer-opened
  // node's prompt IS the incoming message) — never an assistant turn. A peer opener
  // used to be refused here (.turn-postal-service isn't .turn-user) and fall through to the
  // time fallback; accepting postal lets it resolve BY ID instead (the user 2026-06-20).
  if (pendingAnchorIntent === "user"
      && !target.classList.contains("turn-user") && !target.classList.contains("turn-postal-service")) {
    pendingAnchor = null; pendingAnchorIntent = null; landTrail.push("pointer-wrong-kind"); return false;
  }
  pendingAnchor = null; pendingAnchorIntent = null;
  // POSITION PRESERVATION, not a jump (the user 2026-08-02): a scroll-back loadOlder re-anchors on the row
  // the reader was already on, so it must land back at its captured offset — never top-aligned and flashed
  // like a deep-link. Routing it through landOn was what yanked a reader off the summary they had just
  // jumped to and onto the head of the resident tail (an old Bash card); see chatHead.
  if (pendingAnchorKeepY != null) {
    const keepY = pendingAnchorKeepY;
    pendingAnchorKeepY = null;
    landTrail.push("pointer-keep-offset");
    const content = document.getElementById("content");
    if (content) {
      const yNow = target.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop;
      content.scrollTop = yNow - keepY;
    }
    return true;
  }
  landTrail.push("pointer-exact");
  landOn(target);
  return true;
}

// Land on a turn at the TOP of the viewport and KEEP it landed while the chrome
// above the scroll container settles. Top-align (not center) so a jump lands on
// the START of the thing and you read DOWN into it — a long work period isn't
// half-scrolled-past on arrival (the user 2026-06-12). scrollIntoView is a
// one-shot: when a jump also switches tabs, the tab bar re-renders (possibly
// wrapping to a SECOND row) and the ledger box for the new session appears — both
// AFTER the scroll ran. #content shrinks by that growth and the landed turn drifts
// off its mark. So: re-align whenever the bar/ledger actually resizes, plus two
// timed retries for late layout (images, markdown), for ~1.2s — canceled the
// moment the user wheel-scrolls so we never fight a real gesture.
function landOn(target: HTMLElement) {
  const realign = () => target.scrollIntoView({ block: "start", behavior: "auto" });
  realign();
  target.classList.add("anchor-flash");
  setTimeout(() => target.classList.remove("anchor-flash"), 1700);
  const until = Date.now() + 1200;
  let ro: ResizeObserver | null = null;
  const stop = () => { ro?.disconnect(); ro = null; window.removeEventListener("wheel", stop); };
  if (typeof ResizeObserver === "function") {
    ro = new ResizeObserver(() => { if (Date.now() < until) realign(); else stop(); });
    for (const id of ["tabbar", "ledger"]) { const c = document.getElementById(id); if (c) ro.observe(c); }
  }
  window.addEventListener("wheel", stop, { passive: true });
  setTimeout(() => { if (ro && Date.now() < until + 100) realign(); }, 250);
  setTimeout(() => { if (ro) realign(); stop(); }, 1200);
}


// Transient bottom-center notice for DEGRADED deep-link landings only (see
// the diagnostics block in showActive) — a bad jump announces itself instead
// of impersonating a successful one.
function landToast(msg: string) {
  const t = el("div", "locate-toast");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add("fade"), 5200);
  setTimeout(() => t.remove(), 6000);
}

// Right-side warning toast: kernel `warn` messages and federation delivery failures
// land here, so a failed action never dies silently (the user 2026-07-10: creating a
// session on an unreachable remote host gave no feedback at all — the kernel's warn
// had no handler and the dropped route had no witness). Click to dismiss; fades on
// its own otherwise. Toasts stack in #warn-toasts so bursts stay readable.
function warnToast(msg: string) {
  let box = document.getElementById("warn-toasts");
  if (!box) {
    box = el("div", "");
    box.id = "warn-toasts";
    document.body.appendChild(box);
  }
  const t = el("div", "warn-toast");
  t.textContent = msg;
  t.title = "click to dismiss";
  t.addEventListener("click", () => t.remove());
  box.appendChild(t);
  setTimeout(() => t.classList.add("fade"), 11000);
  setTimeout(() => t.remove(), 12000);
}

// Trailing events to re-render on each sync, in case they mutated in place
// (e.g. a tool's output arriving after its tool_use was first shown). Earlier
// events are immutable in an append-only transcript, so they stay cached.
const TAIL_RECHECK = 25;

// Tail-windowing (see the View comment): a fresh/rewound view renders only the
// last WINDOW_TAIL events; scrolling within EXPAND_TRIGGER_PX of the top reveals
// the next EXPAND_CHUNK older ones. WINDOW_TAIL > TAIL_RECHECK so the trailing
// re-check window is always fully rendered.
const WINDOW_TAIL = 80;
const EXPAND_CHUNK = 80;
const EXPAND_TRIGGER_PX = 600;
// Bidirectional virtualization: a scroll/jump renders a window of WINDOW_RADIUS units above & below the
// viewport focus; re-window when the viewport gets within REVIRT_MARGIN units of a rendered edge.
const WINDOW_RADIUS = 70;
const REVIRT_MARGIN = 20;
// A view whose window grew past this (scrolled to the top → lazy-expand crept winStart to 0, or a long
// session watched as it appended) is re-collapsed to the tail window when you switch BACK to it, so a
// switch never has to reveal thousands of nodes. Above WINDOW_TAIL so a normally-grown tab isn't churned.
const WINDOW_CAP = 300;

function ensureView(id: string): View {
  let v = views.get(id);
  if (!v) {
    const content = document.getElementById("content");
    const elv = el("div", "thread");
    elv.dataset.session = id;
    elv.style.display = "none";
    // The live-ask picker is the LAST child of #content (it flows at the bottom of the transcript and scrolls
    // with it). Insert new threads BEFORE it so the picker always stays beneath the active thread; insertBefore
    // with a null/absent ref node just appends, so this is safe whether or not the picker node exists yet.
    content?.insertBefore(elv, document.getElementById("live-ask"));
    v = { el: elv, rendered: 0, scrollTop: 0, stick: true, shown: false, stale: false, winStart: 0, winEnd: 0 };
    views.set(id, v);
  }
  return v;
}

// Bring this view's DOM up to date with its session's events: append new ones
// and re-render a bounded trailing window (cheap), or rebuild fully on a shrink
// (rewind). Does NOT touch scroll. No-op cost when nothing changed is ~O(TAIL).
function syncView(id: string, atBottom?: boolean): View {
  // atBottom (passed by appendActive): false ⇒ the user is scrolled UP reading. A compact append must then
  // NOT evict the window top — evicting shifts the content above the viewport, and since the compact path
  // FULL-REBUILDS (clears the DOM, resetting scrollTop), the caller can only restore the position if the
  // content above is unchanged. true/undefined ⇒ free to evict the top (we're at the bottom, or it's a
  // non-append sync).
  renderingSid = id;          // so renderSystem can key the pinned card's persisted open-state by session
  const v = ensureView(id);
  const s = sessions.get(id);
  if (!s) return v;
  // An empty transcript has nothing to build → a "No messages yet." placeholder, NEVER the deferred
  // "Loading transcript…" hint. The kernel re-sends the FULL events payload on every push, so a zero-event
  // session is genuinely empty — nothing is streaming in to wait for, so a perpetual "Loading…" was a lie
  // (the user 2026-06-19). Idempotent: leaves an existing placeholder in place; the first real event clears it.
  if (s.events.length === 0) {
    const only = v.el.childNodes.length === 1 ? (v.el.firstChild as HTMLElement) : null;
    // …and rebuild a placeholder whose STARTING loader outlived its create (the failure flips it to
    // the couldn't-start notice below — the spinning loader would be a lie on a failed tab)
    const staleStart = !!only && only.classList?.contains("tx-starting") && failedProvisionals.has(id);
    if (!only || !only.classList?.contains("tx-empty") || staleStart) {
      while (v.el.firstChild) v.el.removeChild(v.el.firstChild);
      const ph = el("div", "tx-empty"); v.el.appendChild(ph);
      // A PROVISIONAL tab is not empty, it is STARTING — so it wears the romp loader (the repo's rule for
      // any wait), not the placeholder that tells you to send something. The composer below it is live
      // either way: anything typed here is held and flushed when the session lands. A FAILED create's
      // tab says what happened instead (the user 2026-08-08) — the loader would be a lie.
      if (isProvisionalId(id) && failedProvisionals.has(id)) {
        ph.textContent = "This session couldn't start. What you typed is kept in the box below; "
          + "✕ on the tab discards both.";
      } else if (isProvisionalId(id)) {
        ph.classList.add("tx-starting");
        const sw = el("img", "tx-starting-swirl") as HTMLImageElement;
        sw.src = mediaSrc("romp-swirl-glyph.svg"); sw.alt = ""; sw.onerror = () => sw.remove();
        const wm = el("div", "tx-starting-msg");
        wm.textContent = "Starting " + s.name + "… you can type now; romp sends it when it's up.";
        ph.append(sw, wm);
      } else ph.textContent = "No messages yet.";
    }
    v.rendered = 0; v.stale = false; v.winStart = 0; v.winEnd = 0;
    return v;
  }
  const working = s.status.state === "working" || s.status.state === "compacting";
  const items = displayItems(s);   // units: one per event (normal) or one folded compactDisplay item (compact)
  const total = items.length;
  const len = s.events.length;
  const firstBuild = v.rendered === 0 || v.el.childNodes.length === 0;
  const rewind = len < v.rendered;
  // A fresh build / rewind shows just the TAIL window (bounded → instant switch + small DOM) — but never
  // opens BELOW the newest compaction boundary: pre-compaction history is out of the agent's context, so the
  // default view starts AT the "✦ Context compacted" box (the user 2026-07-07). Older events stream in on
  // scroll-back. When post-compaction work already exceeds the tail window, the tail wins (compaction is above).
  if (firstBuild || rewind) {
    const start = Math.max(0, total - WINDOW_TAIL, lastCompactUnit(s, items));
    renderWindowItems(v, s, items, start, total, working); v.stale = false; return v;
  }
  // No-op fast path — a tab SWITCH / repaint with no event change: reveal the cached DOM, re-render nothing.
  // WITHOUT this, every showActive() re-built the trailing window (markdown + highlight.js) — the big-session
  // switch lag (the user 2026-06-25). A REAL change lowers v.rendered (delta-send sets it to the change index;
  // an append grows len past it) or sets v.stale, so this never skips an actual update.
  if (v.rendered === len && !v.stale && v.el.childNodes.length > 0) return v;
  const wasAtTail = (v.winEnd ?? total) >= (v.unitTotal ?? total);   // window was covering the OLD end
  // An in-place change (tool-group toggle, off-screen update) OR compact mode → re-render the CURRENT window
  // (so the change shows wherever the user is), extending to the new tail if it was at the tail.
  if (settings.compact || v.stale) {
    const span = Math.max(WINDOW_TAIL, (v.winEnd ?? total) - (v.winStart ?? 0));
    // Scrolled-up append (atBottom === false): KEEP winStart so the content above the viewport is unchanged
    // and the caller's scrollTop restore lands exactly. Otherwise extend the tail window, evicting the top.
    const keepTop = wasAtTail && atBottom === false;
    const ws = keepTop ? (v.winStart ?? 0)
                       : wasAtTail ? Math.max(0, total - span) : Math.min(v.winStart ?? 0, Math.max(0, total - 1));
    const we = (wasAtTail || keepTop) ? total : Math.min(v.winEnd ?? total, total);
    renderWindowItems(v, s, items, ws, we, working); v.stale = false; return v;
  }
  // Normal mode, pure append. While BROWSING history (window not at the tail), the new events land below the
  // rendered window → just grow the bottom spacer (no DOM churn); the user sees them on scroll-down.
  if (!wasAtTail) {
    v.spacerCountBot = total - (v.winEnd ?? total); v.unitTotal = total; v.rendered = len; sizeSpacers(v); return v;
  }
  // Normal mode, append AT the tail (unit === event, top spacer only): the cheap incremental hot path —
  // append the new turns + re-check a trailing window, tagging data-unit so the scroll↔unit map stays valid.
  let from = Math.min(v.rendered, Math.max(0, len - TAIL_RECHECK));
  from = Math.max(from, v.winStart ?? 0);
  // Drop every node from unit `from` onward, then re-render that span. Trim by DATA-UNIT, never by
  // child COUNT: a unit can put more than one node in the thread (a day divider precedes the turn
  // that opens a new day), so `keep = spacer + (from - winStart)` counted one node per unit and the
  // extra dividers made it delete that many live turns off the tail, which then never came back.
  // Reading the unit off the node is exact however many nodes a unit owns; the top spacer carries no
  // data-unit, so it stops the walk on its own.
  const unitOf = (n: ChildNode): number =>
    n instanceof HTMLElement && n.dataset.unit != null ? Number(n.dataset.unit) : -1;
  while (v.el.lastChild && unitOf(v.el.lastChild) >= from) v.el.removeChild(v.el.lastChild);
  for (let i = from; i < len; i++) {
    const prev = prevTimedEpoch(s.events, i);
    const ep = eventEpoch(s.events[i]);
    if (ep != null) {   // a day boundary opens with its divider here too, or the tail append would drop it
      const dv = dayDividerFor(ep, prev);
      if (dv) { dv.dataset.unit = String(i); v.el.appendChild(dv); }
    }
    const node = renderEvent(s.events[i], prev, turnWorkedSecs(s.events, i, working));
    node.dataset.unit = String(i);   // unit === event in normal mode
    v.el.appendChild(node);
  }
  v.winEnd = total; v.spacerCount = v.winStart ?? 0; v.spacerCountBot = 0; v.unitTotal = total; v.rendered = len;
  return v;
}

// prevEpoch for event i = the most recent EARLIER timed event's epoch (untimed todo/queued skipped so the
// time-marker chain holds). The back-scan walks the full s.events, NOT just the rendered window, so the
// first turn below the spacer still shows a marker relative to the real prior event.
function prevTimedEpoch(events: ChatEvent[], i: number): number | null {
  for (let j = i - 1; j >= 0; j--) { const e = eventEpoch(events[j]); if (e != null) return e; }
  return null;
}

// ── Unified bidirectional virtualization (the user 2026-06-25) ─────────────────────────────────────────
// Both modes render a window of UNITS [winStart, winEnd): a unit is one event (normal) or one folded
// compactDisplay item (compact). The hidden head [0, winStart) collapses into a TOP spacer and the hidden
// tail [winEnd, total) into a BOTTOM spacer, each sized by (hidden-unit count × avg row height) so the
// scrollbar spans the whole transcript. On scroll, virtualizeToViewport() re-renders AROUND wherever the
// viewport lands — a steady scroll-back OR a scrollbar jump — so random access works, not just contiguous
// scroll. Every rendered node is tagged data-unit so the scroll↔unit mapping can locate it.

// The display units for the current mode: every event as its own pass-through (normal), or the folded
// compactDisplay stream (compact). O(events), cheap (array ops, no DOM).
function displayItems(s: Session): DisplayItem[] {
  if (!settings.compact) {
    const out: DisplayItem[] = [];
    for (let i = 0; i < s.events.length; i++) out.push({ kind: "event", index: i });
    return out;
  }
  return compactDisplay(s.events.map((e) => e.kind), s.events.map((e) => e.kind === "tool" ? e.name : undefined));
}
function itemFirstEvent(it: DisplayItem): number { return it.kind === "toolgroup" ? it.indices[0] : it.index; }

// The display-unit index of the most recent compaction boundary in the loaded events, or 0 if none. The
// default render window opens at (never below) this unit so pre-compaction history is scrubbed from the
// default view (the user 2026-07-07); older events remain reachable on scroll-back via the top spacer.
function lastCompactUnit(s: Session, items: DisplayItem[]): number {
  let evIdx = -1;
  for (let i = s.events.length - 1; i >= 0; i--) { if (s.events[i].kind === "compact") { evIdx = i; break; } }
  if (evIdx < 0) return 0;
  for (let u = 0; u < items.length; u++) {
    if (itemFirstEvent(items[u]) <= evIdx && (u + 1 >= items.length || itemFirstEvent(items[u + 1]) > evIdx)) return u;
  }
  return 0;
}

// Append one display unit's DOM to v.el (a turn, or a folded toolgroup + its expansion), tagging every node
// with data-unit = u for the scroll↔unit map. Returns the advanced prevEpoch.
function appendItem(v: View, s: Session, items: DisplayItem[], u: number, prevEpoch: number | null, working: boolean): number | null {
  const it = items[u];
  const tag = (node: HTMLElement): HTMLElement => { node.dataset.unit = String(u); return node; };
  const adv = (i: number) => { const ep = eventEpoch(s.events[i]); if (ep != null) prevEpoch = ep; };
  // A new day opens with its divider, above whatever unit starts that day (tagged with the same
  // data-unit so the scroll↔unit map still resolves every node it walks).
  const dayOpen = eventEpoch(s.events[itemFirstEvent(it)]);
  if (dayOpen != null) {
    const dv = dayDividerFor(dayOpen, prevEpoch);
    if (dv) v.el.appendChild(tag(dv));
  }
  if (it.kind === "toolgroup") {
    const first = s.events[it.indices[0]];
    const key = toolGroupKey(first);
    const tools = it.indices.map((i) => s.events[i]) as Extract<ChatEvent, { kind: "tool" }>[];
    const open = expandedGroups.has(key);
    v.el.appendChild(tag(renderToolGroup(tools, prevEpoch, key, open)));
    adv(it.indices[0]);
    if (open) {   // expanded → the GROUPED TOOLS, each as its normal turn. Compact mode hides thinking
      // everywhere, so the expansion must too: iterate it.indices (the tools only), NOT the contiguous
      // start..end span, which would surface the thinking that sat between the tools (the user 2026-06-29).
      // it.indices already excludes thinking — compactDisplay skipped it while building the run.
      it.indices.forEach((i, j) => {
        const child = renderEvent(s.events[i], prevEpoch, turnWorkedSecs(s.events, i, working));
        child.classList.add("tg-child"); if (j === it.indices.length - 1) child.classList.add("tg-last");
        v.el.appendChild(tag(child)); adv(i);
      });
    }
  } else {
    v.el.appendChild(tag(renderEvent(s.events[it.index], prevEpoch, turnWorkedSecs(s.events, it.index, working))));
    adv(it.index);
  }
  return prevEpoch;
}

// Full (re)build of the window [unitStart, unitEnd) with head/tail spacers. Does NOT touch scroll (callers
// anchor). prevEpoch for the first rendered unit chains off the real prior event so its time-marker is right.
function renderWindowItems(v: View, s: Session, items: DisplayItem[], unitStart: number, unitEnd: number, working: boolean): void {
  const total = items.length;
  unitStart = Math.max(0, Math.min(unitStart, total));
  unitEnd = Math.max(unitStart, Math.min(unitEnd, total));
  while (v.el.firstChild) v.el.removeChild(v.el.firstChild);
  if (unitStart > 0) v.el.appendChild(el("div", "tx-spacer tx-spacer-top"));
  let prevEpoch = unitStart > 0 && unitStart < total ? prevTimedEpoch(s.events, itemFirstEvent(items[unitStart])) : null;
  for (let u = unitStart; u < unitEnd; u++) prevEpoch = appendItem(v, s, items, u, prevEpoch, working);
  if (unitEnd < total) v.el.appendChild(el("div", "tx-spacer tx-spacer-bot"));
  v.winStart = unitStart; v.winEnd = unitEnd;
  v.spacerCount = unitStart; v.spacerCountBot = total - unitEnd; v.unitTotal = total;
  v.rendered = s.events.length;
  sizeSpacers(v);
}

// Size the head/tail spacers to (hidden-unit count × avg rendered row height) so the scrollbar spans the
// whole transcript. avgTurnH is measured once off the rendered rows (only when VISIBLE — a display:none
// pre-built view reports offsetHeight 0, so don't cache a 0) and reused; the spacers sit off-viewport, so a
// per-row estimate is invisible.
function sizeSpacers(v: View): void {
  const top = v.el.querySelector(".tx-spacer-top") as HTMLElement | null;
  const bot = v.el.querySelector(".tx-spacer-bot") as HTMLElement | null;
  if (!top && !bot) return;
  if (v.avgTurnH == null) {
    let h = 0, n = 0;
    for (const c of Array.from(v.el.children) as HTMLElement[]) {
      if (c.classList.contains("tx-spacer")) continue;
      h += c.offsetHeight; n++;
    }
    if (h > 0 && n > 0) v.avgTurnH = h / n;
  }
  const avg = v.avgTurnH ?? 60;
  if (top) top.style.height = Math.max(0, Math.round((v.spacerCount ?? 0) * avg)) + "px";
  if (bot) bot.style.height = Math.max(0, Math.round((v.spacerCountBot ?? 0) * avg)) + "px";
}

// Estimate the UNIT index at the viewport top: a spacer maps by avg height; a rendered row by its data-unit.
function unitAtScroll(v: View, content: HTMLElement): number {
  const avg = v.avgTurnH ?? 60;
  const st = content.scrollTop;
  const cTop = content.getBoundingClientRect().top;
  const yOf = (e: HTMLElement) => e.getBoundingClientRect().top - cTop + st;   // position in scroll space
  const top = v.el.querySelector(".tx-spacer-top") as HTMLElement | null;
  const topH = top ? top.offsetHeight : 0;
  if (st < topH) return Math.max(0, Math.floor(st / avg));   // in the top spacer
  let lastUnit = v.winStart ?? 0;
  for (const c of Array.from(v.el.children) as HTMLElement[]) {
    if (c.classList.contains("tx-spacer")) continue;
    const t0 = yOf(c);
    if (st < t0) break;
    if (c.dataset.unit != null) lastUnit = Number(c.dataset.unit);
    if (st < t0 + c.offsetHeight) return lastUnit;
  }
  const bot = v.el.querySelector(".tx-spacer-bot") as HTMLElement | null;
  if (bot) { const bTop = yOf(bot); if (st >= bTop) return (v.winEnd ?? 0) + Math.floor((st - bTop) / avg); }
  return lastUnit;
}

// (Compact rendering is no longer a separate path: syncView routes BOTH modes through renderWindowItems,
// which renders compactDisplay units the same way it renders per-event units — see appendItem.)

// Stable identity for a collapsed tool run (survives rebuilds) = the first tool's uuid (else its epoch).
function toolGroupKey(first: ChatEvent): string { return "tg:" + (first.uuid || String(eventEpoch(first) ?? "")); }

// A collapsed run of consecutive tool uses → one rail line: a caret + "3 Edits, 2 Reads" with each
// tool word bold (matching the non-compact .tool-name, so it reads AS tools). Clicking the line toggles
// expand → the full non-compact cards (the user 2026-06-14). Carries the rail dot + time-marker + hover
// wiring like any event so it anchors on the timeline; the dot is a green ✓ disc, red ✗ if any errored.
function renderToolGroup(tools: Extract<ChatEvent, { kind: "tool" }>[], prevEpoch: number | null, key: string, open: boolean): HTMLElement {
  const turn = el("div", "turn turn-toolgroup" + (open ? " expanded" : ""));
  const anyErr = tools.some((t) => t.isError);
  const d = dot(anyErr ? "ring" : "green");
  if (anyErr) d.classList.add("err");
  turn.appendChild(d);
  const line = el("div", "toolgroup-line");
  line.title = open ? "click to collapse" : "click to expand";
  const caret = el("span", "toolgroup-caret"); caret.textContent = open ? "▾" : "▸"; line.appendChild(caret);
  if (!open) {   // collapsed → the "3 Edits, 2 Reads" summary; expanded → just the open arrow (the cards say it)
    toolCounts(tools.map((t) => t.name)).forEach((c, i) => {
      line.appendChild(document.createTextNode((i ? ", " : " ") + c.count + " "));
      const w = el("span", "toolgroup-tool"); w.textContent = c.label; line.appendChild(w);   // bold, like .tool-name
    });
  }
  line.addEventListener("click", (e) => { e.stopPropagation(); toggleToolGroup(key); });
  turn.appendChild(line);
  const epoch = eventEpoch(tools[0]);
  const anchorUuid = tools[0].uuid ?? null;
  if (anchorUuid) turn.dataset.uuid = anchorUuid;
  if (epoch != null) turn.dataset.t = String(epoch);
  if (epoch != null) turn.insertBefore(timeMarker(epoch, prevEpoch ?? null), turn.firstChild);
  const railDot = turn.querySelector(".dot") as HTMLElement | null;
  if (anchorUuid || epoch != null) wireTurnHover(turn, railDot, anchorUuid, epoch ?? 0, tools[0].tlId ?? null);
  return turn;
}

// Toggle a collapsed tool run open/closed and repaint the active view in place (scroll preserved).
function toggleToolGroup(key: string): void {
  if (expandedGroups.has(key)) expandedGroups.delete(key); else expandedGroups.add(key);
  const content = document.getElementById("content");
  const top = content ? content.scrollTop : 0;
  // the expand/collapse changes the DOM without changing the event set, so mark the view stale to force
  // the compact rebuild past the cache guard (a plain tab switch leaves stale false → reuses the cache).
  if (activeId) { const v = views.get(activeId); if (v) v.stale = true; syncView(activeId); }
  if (content) content.scrollTop = top;
  scheduleRailSticky();
}

// Re-render every view from scratch (used when a setting like compact flips): reset each view so the
// next syncView rebuilds it via the right path, then repaint the active one.
function rerenderAll(): void {
  cancelPrebuild(); // the queued plan is now stale (every view reset below) — re-warm after showActive
  for (const v of views.values()) { while (v.el.firstChild) v.el.removeChild(v.el.firstChild); v.rendered = 0; v.stale = false; v.winStart = 0; v.winEnd = 0; v.avgTurnH = undefined; v.spacerCount = undefined; v.spacerCountBot = undefined; v.unitTotal = undefined; }
  showActive();
  schedulePrebuild(); // rebuild every off-screen view in idle under the new setting, so switches stay instant
}

// Index of the last human-prompt event = start of the current turn, where any
// in-place mutations (a tool's output arriving, etc.) live. 0 if none.
function lastTurnStart(events: ChatEvent[]): number {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].kind === "user") return i;
  return 0;
}

// If event i is the LAST reply of a COMPLETED prompt-turn, return the seconds the
// session worked on it (the IMMEDIATE trigger → this reply); else null. A turn is
// "completed" when a new GENUINE prompt follows it (injected user-role lines — postal
// pushes, /command stdout — are skipped, NOT treated as the next prompt), or it's the
// final turn and the session is no longer working (the live spinner owns it).
// The elapsed is measured from the most recent user-role line of ANY author — the
// thing that ACTUALLY triggered this reply — NOT the older human prompt: a nudge or
// postal push that prompted the work is the start, so a nudge-triggered reply doesn't
// inherit the original prompt's elapsed (the user 2026-06-22, who saw worked 23m for a
// 2-min-old nudge — the clock had run from a much older human prompt). Drives the
// "worked …" rail footer.
function turnWorkedSecs(events: ChatEvent[], i: number, working: boolean): number | null {
  const ev = events[i];
  if (ev.kind === "user") return null;                 // a prompt, not a reply
  let completed = false;
  for (let j = i + 1; j < events.length; j++) {
    const e = events[j];
    if (e.kind !== "user") return null;                // another reply in this turn → i isn't its last
    if (e.human) { completed = true; break; }          // next genuine prompt → the turn ended at i
    // injected user line (postal push, /command stdout, …) → same turn, keep scanning
  }
  if (!completed && working) return null;              // final turn still in progress → spinner owns it
  const end = eventEpoch(ev);
  if (end == null) return null;
  let start: number | null = null;                     // the IMMEDIATE trigger: the most recent user line, ANY
  for (let j = i; j >= 0; j--) { const e = events[j]; if (e.kind === "user") { start = eventEpoch(e); break; } }   // author (human / nudge / postal) — not the older human prompt
  if (start == null) return null;
  const secs = end - start;
  return secs > 0 ? secs : null;
}

// Show only the active session's (lazily built) view and set its scroll: a
// deep-link anchor wins; else stick to bottom on first show / if it was left at
// the bottom; else restore the saved position. Switching tabs never rebuilds —
// the cached DOM is just revealed.
// Tell the extension which tab is active, so it can publish it to the romp
// timeline (which outlines the open lane). activeId may be null (no session).
function notifyActive() {
  if (vscodeApi) vscodeApi.postMessage({ type: "activeTab", id: activeId });
}

// Move id to the front of the recency stack (most-recently-active).
function touchMru(id: string) {
  const i = mru.indexOf(id);
  if (i >= 0) mru.splice(i, 1);
  mru.unshift(id);
}

// rAF handle for a DEFERRED heavy transcript build (a first-visit / changed-compact view). Switching away
// before it fires cancels it, so rapid tab-cycling never waits on a transcript it's leaving. (the user 2026-06-17.)
let pendingBuildRaf: number | null = null;

// ---- background pre-build of off-screen tabs (the user 2026-06-25) ----
// Switching to a tab used to pay its WHOLE O(events) DOM build on first visit (showActive's heavy gate), so a
// big transcript opened with a visible lag and, on startup, tabs seemed to "open one at a time".
// content-visibility:auto already skips an OFF-screen turn's layout/paint, but the nodes still have to be
// CREATED once — that's the cost. So build every off-screen tab's DOM AHEAD of time during browser IDLE
// (lowest priority, chunked to the idle deadline): by the time you switch, the view is already built and the
// switch takes showActive's instant (non-heavy) path. Pre-built views stay display:none — pure node creation
// moved off the critical path ("loading stuff in the background"). The POLICY (which tabs, what order) lives
// in prebuild.ts so a test pins it; here we just walk the plan. schedulePrebuild() is fired from the lifecycle
// hooks (a session arriving/updating, a tab switch) and coalesces — a queued pass rescans everything.
type IdleDeadline = { timeRemaining(): number; didTimeout?: boolean };
const _ric = (typeof window !== "undefined" ? (window as any).requestIdleCallback : undefined) as
  | ((cb: (d: IdleDeadline) => void, o?: { timeout: number }) => number)
  | undefined;
const _cic = (typeof window !== "undefined" ? (window as any).cancelIdleCallback : undefined) as
  | ((h: number) => void)
  | undefined;
// requestIdleCallback when available; else a short setTimeout with a small synthetic frame budget, so the
// behaviour degrades gracefully where it's absent.
const requestIdle = (cb: (d: IdleDeadline) => void): number =>
  _ric ? _ric(cb, { timeout: 1500 }) : (window.setTimeout(() => cb({ timeRemaining: () => 12 }), 16) as unknown as number);
const cancelIdle = (h: number): void => { if (_cic) _cic(h); else clearTimeout(h); };

let prebuildHandle: number | null = null;

// Schedule one idle pass (idempotent — a queued pass rescans ALL tabs when it runs, so coalescing the repeat
// calls fired during a startup burst is both correct and cheap).
function schedulePrebuild(): void {
  if (prebuildHandle != null) return;
  prebuildHandle = requestIdle(runPrebuild);
}

// Cancel any queued pass (e.g. a setting flip that rebuilds everything wholesale, so the stale plan is moot).
function cancelPrebuild(): void {
  if (prebuildHandle != null) { cancelIdle(prebuildHandle); prebuildHandle = null; }
}

function runPrebuild(deadline: IdleDeadline): void {
  prebuildHandle = null;
  if (pendingBuildRaf != null) { schedulePrebuild(); return; } // active tab mid-build → yield, retry next idle
  const viewState = (id: string): ViewState | null => {
    const s = sessions.get(id);
    if (!s) return null;
    const v = views.get(id);
    return {
      events: s.events.length,
      hasDom: !!v && v.el.childNodes.length > 0,
      stale: !!v && v.stale,
      rendered: v ? v.rendered : 0,
    };
  };
  const savedRenderingSid = renderingSid; // syncView sets this; restore it so nothing keys off a pre-built tab
  for (const id of prebuildPlan(activeId, mru, order, viewState)) {
    if (!sessions.has(id)) continue;
    try {
      ensureView(id);
      syncView(id); // build the hidden view now, off the critical path
    } catch { /* one malformed tab must not break idle pre-building of the rest */ }
    if (deadline.timeRemaining() < 3) { schedulePrebuild(); break; } // out of idle budget → resume next idle
  }
  renderingSid = savedRenderingSid;
}

function showActive() {
  const content = document.getElementById("content");
  if (!content) return;
  notifyActive();
  renderLedger();  // swap in the active session's digest box (or hide if none)
  renderLiveAsk(); // swap in the active session's pending picker (or hide if none)
  renderBgTasks(); // swap in the active session's background-task box (or hide if none)
  let empty = document.getElementById("empty-state");
  const s = activeId ? sessions.get(activeId) : null;
  if (!s) {
    for (const v of views.values()) v.el.style.display = "none";
    if (!empty) {
      empty = el("div", "empty-state"); empty.id = "empty-state";
      empty.textContent = "No session open — click + to add one.";
      content.appendChild(empty);
    } else { empty.style.display = ""; }
    document.body.style.removeProperty("--active-accent"); // no session → neutral window border
    updateStatusline();
    return;
  }
  if (empty) empty.style.display = "none";
  restoreActiveDraftOnce();   // after a reload, drop the active tab's persisted draft back into the box (once)
  // A closed (dead) session is READ-ONLY: disable the composer so a message can't be black-holed into
  // a session that no longer exists (the user 2026-06-16). Re-runs each push, so a session that dies
  // while you're viewing it disables the box live; switching back to a live tab re-enables it.
  const composer = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (composer) {
    // a FAILED provisional wears the closed treatment on its tab, but its composer stays LIVE: it holds
    // the only copy of what was typed, which must stay editable/copyable (the send path refuses loudly)
    const closed = s.status.state === "closed" && !failedProvisionals.has(activeId!);
    composer.disabled = closed;
    composer.placeholder = closed ? "Session closed — read-only" : composerRestingPlaceholder();
    const sendBtn = document.getElementById("composer-send") as HTMLButtonElement | null;
    if (sendBtn) sendBtn.disabled = closed;   // read-only session → the explicit send button is dead too
  }
  // tint the whole-window border with the active session's identity color
  if (s.color && s.color.bg) document.body.style.setProperty("--active-accent", s.color.bg);
  else document.body.style.removeProperty("--active-accent");
  syncHostOfflineFoot();   // the tab we just switched to may sit on an unreachable host
  touchMru(activeId!); // record activation order so close returns to the previous tab
  const v = ensureView(activeId!);
  // Bound the switch. A view the user scrolled to the top of has had its window expanded to the WHOLE
  // transcript (winStart crept to 0 via lazy-expand), and compact mode renders the whole folded stream —
  // either way, revealing thousands of nodes is the big-session switch lag (the user 2026-06-25: 4144 turns
  // / 43k DOM nodes on a 7157-event session in compact mode). Switching TO such a view with no deep-link
  // pending, re-collapse to the tail window and land at the bottom (the usual intent on switch-back);
  // scrolling up lazily reloads. Both render paths honour winStart, so this works in either mode. Skip when
  // a deep-link is pending (its target may be in the collapsed head). A small view (≤ cap) is left untouched
  // → the no-op fast path reveals it instantly.
  if (!pendingAnchor && pendingAnchorT == null
      && v.el.querySelectorAll(".turn").length > WINDOW_CAP) {
    v.rendered = 0; v.winStart = 0; v.avgTurnH = undefined; v.stick = true;   // → firstBuild rebuilds the tail, lands at bottom
  }
  for (const [vid, vv] of views) vv.el.style.display = vid === activeId ? "" : "none";
  updateStatusline();
  // The transcript BUILD is the only expensive part of a switch. A view already built for the current
  // events renders instantly (cache / incremental); an UNBUILT one (first visit), or a compact view whose
  // events changed, is an O(events) rebuild — so DEFER it to the next frame and SKIP it if we switch away
  // first. Rapid tab-cycling then stays snappy: only the tab you LAND on actually builds. (the user 2026-06-17.)
  // An empty transcript is NEVER heavy: building zero events is instant and must show a placeholder, not
  // the "Loading transcript…" hint — so it renders synchronously below via syncView, never deferred. The
  // `length > 0` guard is what stops a zero-event session from flashing (or sticking on) "Loading…".
  const heavy = s.events.length > 0 && (v.el.childNodes.length === 0 || (settings.compact && (v.rendered !== s.events.length || v.stale)));
  if (!heavy) { syncView(activeId!); landActive(content, v); return; }
  if (v.el.childNodes.length === 0) {   // truly empty → a light loading hint (a stale view keeps its old content visible)
    const ld = el("div", "tx-loading"); ld.textContent = "Loading transcript…"; v.el.appendChild(ld);
  }
  if (pendingBuildRaf != null) cancelAnimationFrame(pendingBuildRaf);
  const target = activeId!;
  pendingBuildRaf = requestAnimationFrame(() => {
    pendingBuildRaf = null;
    if (activeId !== target) return;    // switched away before the build ran → don't build the tab we left
    const vv = views.get(target);
    if (!vv || !sessions.has(target)) return;
    syncView(target);                   // the heavy build now (clears the loading hint)
    landActive(document.getElementById("content"), vv);
  });
}

// Scroll/anchor landing + deep-link diagnostics + restamp, AFTER the active view's DOM is up to date —
// run synchronously for an already-built view, or deferred (next frame) after a heavy build. Factored so
// both paths land identically (the user 2026-06-17).
// Deferred deep-link land: when a jump arrives while the chat pane is hidden (#content clientHeight 0), we keep
// the anchor and re-run the land the moment the pane is next visible. The pane is toggled in the SHELL (this
// iframe just gets un-hidden), so we watch #content's own size: a ResizeObserver catches the 0→height reflow,
// plus a window-resize listener as belt-and-braces for the display:none-iframe edge case. Installed once; a
// no-op until something arms `armedDeferredLand`. (the user 2026-06-30.)
let armedDeferredLand: (() => void) | null = null;
let visibilityWatchOn = false;
function whenChatVisible(cb: () => void): void {
  const c = document.getElementById("content");
  if (c && c.clientHeight > 0) { cb(); return; }
  armedDeferredLand = cb;
  if (visibilityWatchOn) return;
  visibilityWatchOn = true;
  const fire = () => {
    const cc = document.getElementById("content");
    if (cc && cc.clientHeight > 0 && armedDeferredLand) { const f = armedDeferredLand; armedDeferredLand = null; f(); }
  };
  if (c && typeof ResizeObserver !== "undefined") new ResizeObserver(fire).observe(c);
  window.addEventListener("resize", fire);
}

function landActive(content: HTMLElement | null, v: View): void {
  if (!content) return;
  // DEEP-LINK INTO A HIDDEN CHAT PANE (the user 2026-06-30): a jump from the Outline/feed/timeline can arrive
  // while the chat pane is toggled OFF (display:none → #content clientHeight 0). A scroll can't land in a
  // zero-height view, and the code below would consume pendingAnchor anyway — so the jump silently no-ops and
  // never re-lands when the pane comes back (the "Outline links don't work" report). Instead: KEEP the anchor
  // and defer this land until the pane is next visible. Confirmed live: with the pane visible the same anchors
  // land pointer-exact; only the hidden case dropped them.
  if ((pendingAnchor || pendingAnchorT != null) && content.clientHeight === 0) {
    whenChatVisible(() => { const c = document.getElementById("content"); const vv = activeId ? views.get(activeId) : null; if (c && vv) landActive(c, vv); });
    return;
  }
  sizeSpacers(v);  // the view is now VISIBLE (display set in showActive), so the spacers get a real height
                   // measurement — a tab pre-built while display:none could only fall back until now
  const att = { anchor: pendingAnchor, t: pendingAnchorT, kind: pendingAnchorKind, keep: pendingAnchorKeepY != null };   // this pass's landing attempt, for diagnostics
  if (att.anchor || att.t != null) landTrail = [];
  let scrolled = pendingAnchor ? scrollToAnchor(pendingAnchor) : false;
  // BY-ID landing ONLY (the user 2026-06-20, who wanted to shrink the 29%, then remove the time fallback). TIER 1, by id:
  // a card TITLE / node text sends promptAnchorUuid, which lands the originating MESSAGE — a user turn OR a
  // peer's postal card (scrollToAnchor's kind guard now accepts both). That covers the ~71% of cards that
  // resolve PLUS the peer-opener slice the guard used to refuse into the time fallback. The rest mint from an
  // autonomous/continuation segment (no opener) or a turn pruned/compacted off the active path — genuinely
  // unanchorable — so they honest-fail with a toast rather than a clock-nearest guess (which often landed on
  // an unrelated turn anyway — the 'retry'-message bug). The old time tier-2 (scrollToNearestT) is GONE: the
  // last time-based navigation removed, per "no time heuristics". WORK/REPLY intent never had a tier-2 either.
  pendingAnchor = null; pendingAnchorIntent = null; pendingAnchorT = null; pendingAnchorKind = null; pendingAnchorKeepY = null;
  // Diagnostics: log every landing attempt; a deep-link that couldn't resolve announces itself loudly
  // instead of impersonating a successful jump.
  if (att.anchor || att.t != null) {
    vscodeApi?.postMessage({
      type: "locateDiag", id: activeId, ok: scrolled, trail: landTrail.slice(),
      anchor: att.anchor ?? undefined, anchorT: att.t ?? undefined, kind: att.kind ?? undefined,
      keep: att.keep || undefined,
    });
    // A keep-offset restore is NOT a user navigation — nobody asked to locate anything, so a failed one must
    // not raise "couldn't locate this in the transcript" at a reader who only scrolled. It still gets its
    // audit row above (trail + keep), which is where a lost position is diagnosed from.
    if (!scrolled && !anchorPendingOlder && !att.keep) {
      landToast("couldn't locate this in the transcript");  // fetching older history → chatHead re-lands, no false "couldn't locate"
      // …and file it in the error center. The toast is transient and the locate-audit.jsonl row is invisible
      // from the UI, so a failed jump left NOTHING the user could point at afterwards — it read as the click
      // doing nothing (the user 2026-07-28, who wanted this represented as an error, not just a pop-up).
      // The entry carries the sid so clicking it jumps to the card.
      notifyShell("locate", "Couldn't jump to this in " + (sessions.get(activeId || "")?.name || "the transcript")
                  + ". The chat is missing that part of its history.", activeId || "");
    }
  }
  if (!scrolled) {
    if (!v.shown || v.stick) content.scrollTop = content.scrollHeight;
    else content.scrollTop = v.scrollTop;
  }
  v.shown = true;
  scheduleRailSticky();
}

// Scroll ANCHORING for scrolled-up re-renders (the user 2026-07-05). "Appended content is below the
// viewport, so the raw scrollTop still means the same place" stopped being true once chatTail deep-fills
// started rewriting EARLIER cards in place: a running subagent's Task report card ABOVE the viewport grows
// on every update, so a raw pixel restore let the text being read drift down the screen (worst with many
// subagents — every one of their updates re-renders the transcript). Anchor instead on the first rendered
// turn still visible at the viewport top, keyed by its STABLE data-uuid, and after the rebuild put THAT
// element back at its exact offset — then content changing anywhere else, above or below, cannot move what
// the user is reading. The raw scrollTop stays as the fallback for an anchor the render window evicted.
function captureScrollAnchor(content: HTMLElement, v: View): { uuid: string; y: number } | null {
  const cTop = content.getBoundingClientRect().top;
  const turns = v.el.querySelectorAll("[data-uuid]");
  for (let i = 0; i < turns.length; i++) {
    const t = turns[i] as HTMLElement;
    const r = t.getBoundingClientRect();
    if (r.bottom > cTop + 1) {                 // the first turn still (partly) visible at/below the viewport top
      const uuid = t.dataset.uuid || "";
      return uuid ? { uuid, y: r.top - cTop } : null;
    }
  }
  return null;
}

function restoreScrollAnchor(content: HTMLElement, v: View, a: { uuid: string; y: number } | null): boolean {
  if (!a) return false;
  const el = v.el.querySelector(`[data-uuid="${cssEscape(a.uuid)}"]`) as HTMLElement | null;
  if (!el) return false;
  const yNow = el.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop;
  content.scrollTop = yNow - a.y;              // the anchor turn keeps its exact on-screen offset
  return true;
}

// Live tail-append to the ACTIVE view. At the bottom → follow it. Scrolled UP reading → keep the viewport
// exactly where it is: a new message must NOT move what you're looking at (the user 2026-06-25 — incoming
// messages were jumping the view "backwards"; the compact path FULL-REBUILDS on append, clearing the DOM and
// resetting scrollTop). Pass atBottom=false so the rebuild keeps winStart, then restore ANCHOR-relative
// (captureScrollAnchor) — the raw scrollTop only as the eviction fallback.
// A disconnected host's transcript SAYS SO where it ends (the user 2026-07-30). The tab mark is
// peripheral once you are reading — you notice it after the fact, if at all — so the note goes at the
// bottom of the conversation, which is where the eye already lands and where "nothing more is coming"
// belongs. Deliberately NOT a top banner: one lived there for a few hours on 2026-07-29 and covered the
// session tab strip, hiding the sessions in order to announce that a machine had gone away.
//
// A sibling of the view element, never a child: syncView counts v.el's children to track what it has
// rendered, so a foot node inside it would read as transcript.
function syncHostOfflineFoot(): void {
  const content = document.getElementById("content");
  if (!content) return;
  const existing = document.getElementById("host-offline-foot");
  if (!activeId || !hostIsDown(activeId)) { existing?.remove(); return; }
  const host = String(activeId).slice(0, String(activeId).indexOf(":"));
  const text = host + " is disconnected — this is the last romp got from it. Reconnecting.";
  if (existing) { if (existing.textContent !== text) existing.textContent = text; return; }
  const note = el("div", "tx-hostoff");
  note.id = "host-offline-foot";
  note.textContent = text;
  note.title = hostDownNote(activeId);   // the one place that note is worded — host-prefix.ts
  content.appendChild(note);
}

function appendActive() {
  const content = document.getElementById("content");
  if (!content || !activeId) { showActive(); return; }
  const v = views.get(activeId);
  const stick = nearBottom(content);
  const before = content.scrollTop;
  const anchor = !stick && v ? captureScrollAnchor(content, v) : null;
  syncView(activeId, stick);
  syncHostOfflineFoot();                 // before the scroll maths: it changes scrollHeight
  updateStatusline();
  if (stick) content.scrollTop = content.scrollHeight;
  else if (!(v && restoreScrollAnchor(content, v, anchor))) content.scrollTop = before;
  scheduleRailSticky();
}

// Row heights change when the pane is resized (text re-wraps), so the spacing-based
// stamps must be recomputed against the new layout.
window.addEventListener("resize", scheduleRailSticky);
// Reposition/repaint the overview ruler whenever the window OR the #content box changes (the ledger or
// live-ask strip showing, the composer growing, a resize) — the ruler maps content-space → ruler-space, so
// a plain scroll needs no repaint, but its viewport box and scrollHeight can move under it (link_audit's #4).
window.addEventListener("resize", paintGlowRuler);
window.addEventListener("resize", scheduleRailSticky);
if (typeof ResizeObserver === "function") {
  const ro = new ResizeObserver(() => paintGlowRuler());
  const c = document.getElementById("content");
  if (c) ro.observe(c);
}
// the sticky rail stamp tracks the scroll it annotates (passive: it only measures, never blocks the scroll)
{
  const c = document.getElementById("content");
  if (c) c.addEventListener("scroll", scheduleRailSticky, { passive: true });
}
// Boxes ABOVE the transcript grow/shrink → keep the chat text visually anchored (the user 2026-06-30 for
// #tabbar; extended to #ledger 2026-07-05). Both are `flex: 0 0 auto` directly above the `flex: 1 1 auto`
// #content scroll area, so when one grows — a working dot wraps the tab strip to a second row, a ledger
// item lands and deepens the summary box — it shoves #content down by that Δ and every line under it
// jumps. Compensate by shifting #content.scrollTop by the SAME Δ (box moved down by Δ → scroll the content
// up by Δ to cancel it), so the line being read stays fixed on screen. Symmetric on shrink (Δ negative).
// Skipped when stuck to the bottom (that view follows the tail anyway) or when the pane is hidden
// (clientHeight 0).
if (typeof ResizeObserver === "function") {
  for (const boxId of ["tabbar", "ledger"]) {
    const box = document.getElementById(boxId);
    if (!box) continue;
    let lastH = -1;                                           // -1 = not yet measured (observe fires once on attach)
    const tro = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect?.height ?? 0;
      const content = document.getElementById("content");
      if (content && lastH >= 0 && h !== lastH && content.clientHeight > 0 && !nearBottom(content)) {
        content.scrollTop += h - lastH;
        const v = activeId ? views.get(activeId) : null;
        if (v) v.scrollTop = content.scrollTop;               // keep the per-view saved position in sync
      }
      lastH = h;
    });
    tro.observe(box);
  }
}

// Keep the rendered window over the viewport as the user scrolls — a steady scroll-back OR a scrollbar JUMP
// to anywhere (random access). On every scroll we estimate the unit at the viewport top; if it has drifted
// within REVIRT_MARGIN units of a rendered edge (or sits in a blank spacer after a jump), we re-render a
// fresh window AROUND it and re-anchor so the focused unit stays put. Event-based (keyed on scroll position,
// not a timer); coalesced to one rebuild per frame. A "Loading…" pill paints first so a jump into
// un-rendered history reads as loading, not frozen.
let revirtBusy = false;
function virtualizeToViewport(): void {
  if (revirtBusy || !activeId) return;
  const v = views.get(activeId);
  const content = document.getElementById("content");
  const s = sessions.get(activeId);
  if (!v || !content || !s) return;
  const total = v.unitTotal ?? 0;
  const moreOnServer = (s.headFrom ?? 0) > 0;   // older history not yet resident (wire tail-windowing)
  if (total === 0 || ((v.winStart ?? 0) === 0 && (v.winEnd ?? total) >= total && !moreOnServer)) return; // everything rendered + resident
  // CHEAP pre-check first (this runs on EVERY scroll): is the viewport comfortably inside the rendered band?
  // Only when it nears a rendered edge do we pay the precise unit walk + re-render. Without this, every scroll
  // event would getBoundingClientRect every rendered row → jank.
  const avg = v.avgTurnH ?? 60;
  const edgePx = REVIRT_MARGIN * avg;
  const topEl = v.el.querySelector(".tx-spacer-top") as HTMLElement | null;
  const botEl = v.el.querySelector(".tx-spacer-bot") as HTMLElement | null;
  const cRectTop = content.getBoundingClientRect().top;
  const topH = topEl ? topEl.offsetHeight : 0;
  const renderedBottom = botEl ? botEl.getBoundingClientRect().top - cRectTop + content.scrollTop : content.scrollHeight;
  const st = content.scrollTop, vh = content.clientHeight;
  // At the top of the RESIDENT events with older history still on the server → fetch the previous chunk
  // (loadOlder → chatHead). winStart 0 ⇒ no top spacer left to expand into; topH is 0 so this is "near 0".
  if (moreOnServer && (v.winStart ?? 0) === 0 && st < topH + edgePx) { requestOlder(activeId, v, content); return; }
  const nearTopEdge = (v.winStart ?? 0) > 0 && st < topH + edgePx;
  const nearBotEdge = (v.winEnd ?? total) < total && st + vh > renderedBottom - edgePx;
  if (!nearTopEdge && !nearBotEdge) return;   // window comfortably covers the viewport
  revirtBusy = true;
  showLoadingPill();
  // Defer one frame so the pill paints before the (possibly heavy) render, then re-anchor on the focus unit.
  requestAnimationFrame(() => {
    try {
      // Read the focus unit at RENDER time, not scroll-event time: a fast scrollbar DRAG moves on between the
      // scroll event and this frame, so capturing it earlier anchored to a stale spot → the "snap back" on a
      // fast random jump (the user 2026-06-25).
      const idx = unitAtScroll(v, content);
      const working = s.status.state === "working" || s.status.state === "compacting";
      const items = displayItems(s);
      const c = Math.max(0, Math.min(idx, items.length - 1));
      // where unit c sits relative to the viewport top NOW (so it doesn't jump); null ⇒ it's in a spacer
      // (a jump) → land it at the viewport top.
      const before = v.el.querySelector(`[data-unit="${c}"]`) as HTMLElement | null;
      const beforeY = before ? before.getBoundingClientRect().top - content.getBoundingClientRect().top : 0;
      renderWindowItems(v, s, items, Math.max(0, c - WINDOW_RADIUS), Math.min(items.length, c + WINDOW_RADIUS), working);
      const anchor = v.el.querySelector(`[data-unit="${c}"]`) as HTMLElement | null;
      if (anchor) {
        const yNow = anchor.getBoundingClientRect().top - content.getBoundingClientRect().top + content.scrollTop;
        content.scrollTop = yNow - beforeY;
      }
      scheduleRailSticky();
    } finally {
      hideLoadingPill();
      revirtBusy = false;   // always release, even if a render threw — a wedged flag = no more loading
    }
  });
}
{
  const c = document.getElementById("content");
  if (c) c.addEventListener("scroll", virtualizeToViewport, { passive: true });
}

// A small "Loading earlier messages…" pill at the top-center of the chat pane, shown while a window
// expand/jump is rendering so a scroll into un-rendered history reads as loading-in-progress, not frozen
// (the user 2026-06-25). Lives in the chat iframe's body; idempotent.
let loadingPillEl: HTMLElement | null = null;
function showLoadingPill(): void {
  if (!loadingPillEl) {
    loadingPillEl = document.createElement("div");
    loadingPillEl.className = "tx-loading-pill";
    loadingPillEl.textContent = "Loading earlier messages…";
    document.body.appendChild(loadingPillEl);
  }
  loadingPillEl.style.display = "";
}
function hideLoadingPill(): void { if (loadingPillEl) loadingPillEl.style.display = "none"; }

// ---- ledger box (rolling per-session digest, just below the tabs) ----

function setLedger(id: string, ledger: Ledger | null) {
  ledgers.set(id, ledger);
  if (id === activeId) renderLedger();
}

function agehms(secs: number): string {
  secs = Math.max(0, Math.floor(secs));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

// The "(age)" label is colored by recency on the SHARED romp colormap, log age scale, so the ledger
// matches the feed. The bullet TEXT stays at default brightness; only the
// age label is colored.
// The recency colormaps — KEEP IN SYNC with bin/romp_colormap.py (the kernel computes the feed's trgb
// from the same stops). ONE global time colormap, picked in settings (the user 2026-06-17): the ledger
// reads the chosen map here so it matches the feed (which gets the map via the kernel's trgb). Each is
// dark→light (recent → the last/bright stop).
const COLORMAPS: Record<string, Array<[number, number, number]>> = {
  aurora: [[84, 178, 4], [0, 180, 115], [35, 175, 156], [66, 169, 176], [25, 168, 201], [14, 164, 227], [74, 155, 241], [113, 145, 244], [144, 136, 240]],   // romp green→teal→blue→purple at CONSTANT lightness — the default
  hawaii: [[140, 2, 115], [146, 46, 85], [151, 78, 62], [155, 111, 40], [156, 150, 28], [137, 189, 74], [107, 212, 142], [103, 233, 213], [179, 242, 253]],
  viridis: [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]],
  magma: [[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]],
  inferno: [[0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99], [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164]],
  plasma: [[13, 8, 135], [75, 3, 161], [125, 3, 168], [168, 34, 150], [203, 70, 121], [229, 107, 93], [248, 148, 65], [253, 195, 40], [240, 249, 33]],
  cividis: [[0, 34, 78], [33, 59, 110], [76, 85, 108], [108, 110, 114], [142, 137, 120], [177, 165, 112], [217, 197, 92], [254, 232, 56]],
};
function selectedStops(): Array<[number, number, number]> {
  return COLORMAPS[(settings.colormap || "").toLowerCase()] || COLORMAPS.aurora;   // settings updated + rerenderAll on change
}
function ramp(v: number): [number, number, number] {
  const STOPS = selectedStops();
  v = Math.max(0, Math.min(1, v));
  const x = v * (STOPS.length - 1), i = Math.floor(x), fr = x - i;
  if (i >= STOPS.length - 1) return STOPS[STOPS.length - 1];
  const a = STOPS[i], b = STOPS[i + 1];
  return [Math.round(a[0] + (b[0] - a[0]) * fr), Math.round(a[1] + (b[1] - a[1]) * fr), Math.round(a[2] + (b[2] - a[2]) * fr)];
}
// Arm a compaction "sweep" fill (the tab bar bar + the statusline battery scan) so it (a) does NOT restart
// every render and (b) mirrors the context colormap as it compresses.
//   (a) renderTabs()/updateStatusline() recreate the element on every kernel push (0.5–3s backstop + one per
//       stream event); a plain CSS animation resets to frame 0 each time, so it visibly hiccups/jumps (the
//       user 2026-07-02: the tab bar restarted while the timeline's — a persistent repositioned overlay —
//       stayed smooth). A NEGATIVE animation-delay of -(now mod duration) makes the phase a pure function of
//       the wall clock, so a freshly-built element resumes exactly where the destroyed one was — seamless
//       across rebuilds, event-based, no shared timer. Duration MUST match the element's @keyframes duration.
//   (b) sample ramp() at the sweep's scaleX stops (see @keyframes tab-compact / ctx-compress in styles.css)
//       and hand them to CSS as vars: --cmp4 (widest = the map's "full"/100% colour) … --cmp0 (narrowest =
//       the map's 0% colour). The bar then slides through the SAME hues the battery fill uses as it narrows.
// phaseSync: seed the wall-clock phase ONLY on a FRESHLY-created element. For a REUSED element the animation
// is already running on the compositor, and re-seeding animationDelay RESTARTS it — which is exactly the jump
// the user saw on the statusline bar (its in-place refresh reuses #ctx-bar), 2026-07-02. The gradient vars are
// always safe to (re)apply: changing a custom prop the keyframes reference recolors WITHOUT restarting.
function applyCompactSweep(fillEl: HTMLElement, durationMs = 1600, phaseSync = true): void {
  if (phaseSync) fillEl.style.animationDelay = `-${Date.now() % durationMs}ms`;
  const rgb = (v: number): string => { const c = ramp(v); return `rgb(${c[0]},${c[1]},${c[2]})`; };
  fillEl.style.setProperty("--cmp0", rgb(0.12));   // narrowest width in the sweep → the map's low end
  fillEl.style.setProperty("--cmp1", rgb(0.34));
  fillEl.style.setProperty("--cmp2", rgb(0.56));
  fillEl.style.setProperty("--cmp3", rgb(0.78));
  fillEl.style.setProperty("--cmp4", rgb(1.0));    // full width → the map's high end (matches a ~100% fill)
}
// recency → ramp position [0..1] (recent → 1, old → 0), shared log age scale.
function recencyV(ageSecs: number): number {
  const LO = 120, HI = 345600; // 2 min (brightest) .. 96 h (darkest) — matches romp_colormap.py FADE_HI
  const a = Math.max(LO, Math.min(HI, ageSecs));
  return 1.0 - (Math.log(a) - Math.log(LO)) / (Math.log(HI) - Math.log(LO));
}
function ageColor(ageSecs: number): string {
  const c = ramp(recencyV(ageSecs));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}
function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0; const l = (mx + mn) / 2;
  const s = d === 0 ? 0 : l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
  if (d !== 0) {
    if (mx === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h /= 6;
  }
  return [h, s, l];
}
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) { const v = Math.round(l * 255); return [v, v, v]; }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const hk = (t: number) => {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [Math.round(hk(h + 1 / 3) * 255), Math.round(hk(h) * 255), Math.round(hk(h - 1 / 3) * 255)];
}
// Recency for the ledger: take the shared hawaii ramp color (the same hue progression
// the feed uses) but remap its LIGHTNESS into a legible band so
// no bullet drops into the unreadable dark-magenta the raw ramp produces at the
// old end. Brightness still carries recency as a secondary cue — recent bullets
// are bright, oldest ones fade darker/muted — but the fade is floored (~L0.50) so
// even the oldest stays readable on the dark panel. Hue carries recency too
// (magenta = old → cyan = recent).
function ageColorReadable(ageSecs: number): string {
  const v = recencyV(ageSecs);             // 0 = oldest, 1 = most recent
  const c = ramp(v);
  const [h, s] = rgbToHsl(c[0], c[1], c[2]);
  const L = 0.50 + 0.22 * v;               // oldest → 0.50 (faded), recent → 0.72 (bright)
  const S = Math.max(0.4, s) * (0.65 + 0.35 * v); // mute the old end a touch; full vividness when recent
  const o = hslToRgb(h, Math.min(1, S), L);
  return `rgb(${o[0]}, ${o[1]}, ${o[2]})`;
}

// How many bullets the ledger box renders. The box is a scroll-pane (~6 rows tall,
// see .ledger-bullets in styles.css) so the rest scroll into view (the user 2026-06-12).
// Matches the host's recentReplyBullets cap in state.ts.
const LEDGER_BULLET_CAP = 30;

// Collapse state for the ledger summary box (toggled from the tab bar, persisted
// per-panel via the webview state so it survives reloads).
let ledgerCollapsed = false;
try { ledgerCollapsed = !!((vscodeApi && vscodeApi.getState && vscodeApi.getState()) || {}).ledgerCollapsed; } catch { /* ignore */ }
// Per-node fold state for the ledger tree (the user 2026-06-16): a node folds by DEFAULT once it's done
// (a "previous" task) unless it's on the recent path; the user can override either way. Keyed by node id
// (ids are session-scoped, so the sets are safe to keep global across session switches).
const ledgerFolded = new Set<string>();    // explicitly folded by the user (overrides a default-open)
const ledgerExpanded = new Set<string>();  // explicitly expanded by the user (overrides a default-fold)
// One-shot FLIP source (the user 2026-06-18): capture WHERE the goal text sits now, so the next render can
// MORPH the destination element from this spot — BOTH ways: expanding glides the collapsed line into its
// pinned row (then the rest fades in); collapsing glides the row text back up into the compact line.
// Consumed (cleared) by that render; a routine data-update leaves it null, so it never re-animates.
let ledgerMorphFrom: { left: number; top: number } | null = null;
function toggleLedgerCollapsed() {
  const expanding = ledgerCollapsed;   // currently collapsed → this click EXPANDS
  ledgerMorphFrom = null;
  // FROM = wherever the goal text lives RIGHT NOW: collapsed → the summary line; expanded → the curTop row's text.
  const fromEl = (expanding
    ? document.getElementById("ledger")?.querySelector(".ledger-summary")
    : document.getElementById("ledger")?.querySelector(".ledger-tnode.ledger-curtop .ledger-ttext")) as HTMLElement | null;
  if (fromEl && fromEl.getBoundingClientRect) { const r = fromEl.getBoundingClientRect(); ledgerMorphFrom = { left: r.left, top: r.top }; }
  ledgerCollapsed = !ledgerCollapsed;
  try { if (vscodeApi && vscodeApi.setState) vscodeApi.setState({ ...(vscodeApi.getState() || {}), ledgerCollapsed }); } catch { /* ignore */ }
  renderLedger();
  renderTabs(); // refresh the ▾/▸ glyph
}

function renderLedger() {
  // The in-chat ledger box was REMOVED (the user 2026-06-24): the per-session work digest now lives in the
  // tab hover tooltip (Summary + last-5 worked-on items) and the Fleet view, so the box was redundant. Keep
  // #ledger empty + hidden. The ledger DATA (ledgers map) and .ledger-* styling stay — the tooltip + Fleet use them.
  const host = document.getElementById("ledger");
  if (host) { host.replaceChildren(); host.style.display = "none"; }
}

// Background-task box (#bg-tasks) between the transcript and the composer (the user 2026-06-26). Three-level
// disclosure, like a tool-use fold — and COLLAPSED by default so a busy session (e.g. many running training
// tasks) doesn't fill the box:
//   1. a count HEADER — "Background task · <name>" for one, "N background tasks" for many (+ a worst-status
//      dot so a failure is glanceable while collapsed). Click → toggle the list.
//   2. the LIST — one row per task (status dot + summary), scrollable (~5 visible). Click a row → details.
//   3. per-task DETAILS — the command + its output, each in its own scrollable block.
// Both fold levels persist across the per-push re-render (keyed by session id / task id); every toggle is
// DELEGATED to the stable #bg-tasks container so a rebuild mid-click never drops it. textContent only
// (command/output are untrusted).
const bgExpanded = new Set<string>();   // task ids whose details are open
const bgFoldOpen = new Set<string>();   // session ids whose list is expanded
const BG_RANK: Record<string, number> = { failed: 3, running: 2, completed: 1 };
function renderBgTasks() {
  const host = document.getElementById("bg-tasks");
  if (!host) return;
  host.replaceChildren();
  const s = activeId ? sessions.get(activeId) : null;
  const box = s && s.bgTasks;
  const tasks = (box && box.tasks) || [];
  const count = box ? box.count : 0;
  if (!count || !tasks.length) { host.style.display = "none"; return; }
  host.style.display = "";
  const sid = activeId as string;
  const open = bgFoldOpen.has(sid);
  // worst status among the shown tasks → the header dot color (so a failure shows even while collapsed)
  const worst = tasks.reduce((w, t) => (BG_RANK[t.status] || 0) > (BG_RANK[w] || 0) ? t.status : w, "completed");
  const head = el("div", "bg-fold-head bg-" + worst + (open ? " open" : ""));
  head.dataset.act = "bg-fold"; head.dataset.id = sid;
  const car = el("span", "bg-caret"); car.textContent = open ? "▾" : "▸"; head.appendChild(car);   // ▸ closed → ▾ open (expands DOWNWARD beneath the header)
  head.appendChild(el("span", "bg-dot"));
  const lab = el("span", "bg-fold-label");
  lab.textContent = count === 1 ? "Background task · " + (tasks[0].summary || "running")
    : count + " background tasks";
  head.appendChild(lab);
  host.appendChild(head);
  if (!open) return;
  const list = el("div", "bg-list");
  for (const t of tasks) {
    const tOpen = bgExpanded.has(t.id);
    const row = el("div", "bg-task bg-" + (t.status || "running") + (tOpen ? " open" : ""));
    const rh = el("div", "bg-head");
    rh.dataset.act = "bg-toggle"; rh.dataset.id = t.id;   // the row header toggles; clicks in the detail body don't collapse it
    rh.appendChild(el("span", "bg-dot"));
    const sum = el("span", "bg-sum"); sum.textContent = t.summary || "Background task"; rh.appendChild(sum);
    const st = el("span", "bg-status"); st.textContent = t.status || "running"; rh.appendChild(st);
    if ((t.status || "running") === "running") {
      // Stop this ONE task (the SDK's stop_task control request — the user 2026-08-04). Rides the same
      // stable delegate as the fold toggles (click-safe across re-renders); the click acknowledges by
      // disabling + relabeling ITSELF, and the row's disappearance (the task's own terminal lifecycle
      // event) is the real confirmation — a task still running at the next render gets a fresh button.
      const stop = el("button", "bg-stop");
      stop.dataset.act = "bg-stop"; stop.dataset.id = t.id;
      stop.textContent = "Stop"; stop.title = "stop this background task";
      rh.appendChild(stop);
    }
    const rc = el("span", "bg-caret"); rc.textContent = tOpen ? "▾" : "▸"; rh.appendChild(rc);
    row.appendChild(rh);
    if (tOpen) {
      const det = el("div", "bg-detail");
      if (t.command) { const cmd = el("pre", "bg-cmd"); cmd.textContent = t.command; det.appendChild(cmd); }
      const out = el("pre", "bg-out"); out.textContent = t.output || "(no output captured)"; det.appendChild(out);
      row.appendChild(det);
    }
    list.appendChild(row);
  }
  host.appendChild(list);
}

// ---- live "awaiting your input" widgets (structured: radio / checkbox / submit / text) ----

function setLiveAsk(id: string, ask: ParsedAsk | null) {
  if (!liveAsks.has(id)) askArrivedAt.set(id, Date.now());   // ARRIVED now — not a re-render of the same question
  liveAsks.set(id, ask);
  if (id === activeId) renderLiveAsk();
}
function clearLiveAsk(id: string) {
  askArrivedAt.delete(id);
  if (liveAsks.delete(id) && id === activeId) renderLiveAsk();
}

let sendingTimer: ReturnType<typeof setTimeout> | undefined;
// Local UI highlight for the single-select card (↑/↓); the actual selection is the
// delta send on confirm. Keyed so incidental re-posts of the same prompt keep it.
let liveAskFocus = 0;
let liveAskFocusKey = "";

// True on a phone/tablet (a coarse pointer) — the mobile signal we gate touch affordances on. NOT viewport
// width (desktop chat panes are narrow too). Reused by the composer's Enter behavior, its resting
// placeholder, and the attach picker below.
function isCoarsePointer(): boolean {
  try { return window.matchMedia("(pointer:coarse)").matches; } catch { return false; }
}

// The composer's resting placeholder — mirrors chatBody()'s skeleton. Restored whenever no free-text
// picker is active. (Kept here, not read from the DOM, so an "answering" placeholder never leaks back
// as the default after a picker resolves.) On a phone (coarse pointer) Enter makes a NEWLINE and the Send
// button sends, so the ⏎/⇧⏎ hint is wrong there — and the box is a ONE-line Signal-style pill flanked by
// the two buttons (the user 2026-07-30), so even the "/" hint wrapped and got clipped: mobile keeps just
// the core prompt, and slash-command discovery stays a desktop hint.
function composerRestingPlaceholder(): string {
  return isCoarsePointer()
    ? "Message this session…"
    : "Message this session…  (⏎ send · ⇧⏎ newline · type / for commands)";
}

// How a message typed into the NORMAL composer should be routed while a live picker is up — the picker's
// dropped inline "add your own" field, now served by the composer (the user 2026-07-09). null → no active
// free-text path, so the composer sends a normal message as usual (a permission Allow/Deny prompt, or an
// ExitPlanMode review, offers no free text).
//   "custom" → an AskUserQuestion option list with a "Type something" slot: single-select submits the typed
//              answer immediately; multi-select adds it as a checked custom row (then the card's Submit sends).
//   "text"   → a raw free-text prompt the panel couldn't structure (askText).
function composerAnswersAsk(): "custom" | "text" | null {
  if (!activeId || !liveAsks.has(activeId)) return null;
  if (draftPredatesAsk(activeId)) return null;   // already writing it when the question landed → it's a message
  const ask = liveAsks.get(activeId) ?? null;
  if (!ask) return "text";
  if ((ask.kind === "single" || ask.kind === "multi")
      && ask.options.some((o) => isTypeSomething(o.label))) return "custom";
  return null;
}

// True when the composer's draft was already under way before this session's question arrived — so it can't
// be an answer to it. Both stamps are required: without them we can't claim it predates, and the picker keeps
// the box (see askArrivedAt/draftStartedAt).
function draftPredatesAsk(id: string): boolean {
  const started = draftStartedAt.get(id), arrived = askArrivedAt.get(id);
  return started != null && arrived != null && started < arrived;
}

// Put the composer into (or out of) "answer this picker" mode: a picker with a free-text path relabels the
// box "add your own answer…" and tints it, so it's discoverable that typing here answers the prompt.
// Called on every renderLiveAsk (the single owner of picker↔composer coupling), so it self-heals.
function setComposerAskMode() {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta) return;
  if (composerAnswersAsk()) {
    ta.placeholder = "add your own answer…  (⏎ submit)";
    ta.classList.add("answering");
  } else {
    ta.placeholder = composerRestingPlaceholder();
    ta.classList.remove("answering");
  }
}

// Render the widget matching the active session's pending prompt. It lives at the BOTTOM of the transcript
// (the last child of #content) and scrolls WITH the chat history (the user 2026-06-27) — so a tall picker
// never buries the context above it; scroll up and the question's context is still right there. The footer
// (message box + controls) stays VISIBLE beneath it; the composer doubles as the "add your own" field.
// single → radio rows, multi → checkboxes + Submit/Cancel, submit → review + action buttons, null → warning.
function renderLiveAsk() {
  const host = document.getElementById("live-ask");
  const footer = document.getElementById("footer");
  const content = document.getElementById("content");
  if (!host) return;
  // Keep the picker the LAST child of #content so it sits beneath the active thread even if a thread was
  // appended after it (e.g. switching to a never-seen session while a picker is up).
  if (content && host.parentNode === content && host !== content.lastChild) content.appendChild(host);
  host.replaceChildren();
  host.classList.remove("sending");
  if (sendingTimer) { clearTimeout(sendingTimer); sendingTimer = undefined; }
  sendingGuard = false; // a fresh render = the previous action resolved; re-enable
  const cur = activeId ? liveAsks.get(activeId) : undefined;
  if (cur) liveTextValue = ""; // leaving the free-text field (a structured screen is up)
  // The footer (statusline: working chip / interrupt / model selector + the message box) stays VISIBLE
  // while a picker is up (the user 2026-07-09): the picker no longer TAKES OVER the box — it drops its own
  // inline "add your own" field, and the NORMAL composer becomes that field (see composerAnswersAsk /
  // sendComposer). So you keep every control in view and can still type a free-text answer.
  if (footer) footer.style.display = "";
  if (!activeId || !liveAsks.has(activeId)) {
    host.style.display = "none";
    liveTextValue = "";
    setComposerAskMode();   // no picker → the composer's normal placeholder + behavior
    return;
  }
  host.style.display = "";
  const ask = liveAsks.get(activeId) ?? null;
  setComposerAskMode();   // picker with a free-text path → the composer becomes "add your own answer…"
  if (!ask) renderUnknownCard();
  else if (ask.kind === "multi") renderMultiCard(ask);
  else if (ask.kind === "submit") renderSubmitCard(ask);
  else renderSingleCard(ask);
  renderAskPreview();   // focus-aware: the FOCUSED option's own preview (SDK) or the single scraped one (tmux)
  // Reveal the picker if the user is parked at the bottom — it's part of the scroll flow now, so new/taller
  // pickers would otherwise land below the fold. Never yank a user who has scrolled UP to read context.
  const v = activeId ? views.get(activeId) : undefined;
  if (content && (!v || v.stick)) content.scrollTop = content.scrollHeight;
}

// The focused option's side-by-side preview box, reproduced VERBATIM in a monospace block (the user
// 2026-06-13). The TUI draws it to the RIGHT of the options; the chat rail is narrow, so it sits BELOW the
// card and scrolls sideways if wider than the rail. FOCUS-AWARE (the user 2026-06-22): on a single-select
// card it shows the CURRENTLY-FOCUSED option's preview, so ↑/↓ swaps the picture like the terminal —
// instantly when the option carries its OWN preview (the SDK backend sends one per option), else from
// ParsedAsk.preview (the one the tmux scrape captured for the focused row, which paintLiveAskFocus keeps
// current by nudging the terminal cursor). REPLACES rather than appends, so stepping never stacks
// duplicates. textContent, never innerHTML: the pane text is untrusted terminal output.
function renderAskPreview() {
  const host = document.getElementById("live-ask"); if (!host) return;
  const card = host.querySelector(".ask-card") as HTMLElement | null; if (!card) return;
  const ask = activeId ? liveAsks.get(activeId) : null;
  let preview: string | undefined;
  if (ask && ask.kind === "single") {
    const opts = singleOptions(ask);
    const o = opts[Math.max(0, Math.min(liveAskFocus, opts.length - 1))];
    preview = (o && o.preview) || ask.preview || undefined;
  } else {
    preview = ask?.preview || undefined;
  }
  let pre = card.querySelector(".ask-preview") as HTMLElement | null;
  if (!preview) { if (pre) pre.remove(); return; }
  if (!pre) { pre = el("pre", "ask-preview"); card.appendChild(pre); }
  // A "diff" preview (Edit/Write permission on the SDK backend) gets per-line +/- coloring; everything
  // else stays verbatim monospace. Still text-only — each line's text is set via textContent (untrusted
  // tool output), only the row's CLASS is derived from its leading char (the user 2026-06-27).
  if (ask && ask.previewKind === "diff") {
    pre.classList.add("ask-preview-diff");
    pre.replaceChildren(...preview.split("\n").map(diffLineEl));
  } else {
    pre.classList.remove("ask-preview-diff");
    pre.textContent = preview;
  }
}

// One diff row: green for an added (+) line, red for a removed (-) line, dim for a hunk (@@) header,
// neutral otherwise. unified-diff prefixes context lines with a space, so real code starting with +/-
// isn't miscolored. textContent only.
function diffLineEl(line: string): HTMLElement {
  const cls = line.startsWith("@@") ? "diff-hunk"
    : line.startsWith("+") ? "diff-add"
    : line.startsWith("-") ? "diff-del" : "diff-ctx";
  const row = el("div", cls);
  row.textContent = line.length ? line : "​";   // keep blank rows at line height
  return row;
}

function askCard(extraClass = ""): HTMLElement {
  const card = el("div", "ask-card ask-live" + (extraClass ? " " + extraClass : ""));
  document.getElementById("live-ask")!.appendChild(card);
  return card;
}
function qline(card: HTMLElement, text?: string) {
  if (text) { const qt = el("div", "ask-qtext"); qt.textContent = text; card.appendChild(qt); }
}

// The pickable rows of a single-select card. "Type something." is driven by the inline custom field (not a
// row); "Chat about this" IS a real selectable answer (the user 2026-06-27) — picking it tells the agent you
// want to discuss instead of choosing, exactly like the terminal — so it's kept. Falls back to everything
// rather than render zero rows.
function singleOptions(ask: ParsedAsk) {
  const real = ask.options.filter((o) => !isMetaOption(o.label));
  return real.length ? real : ask.options;
}

// SINGLE-select: clickable radio rows; ↑/↓ highlight, Enter/number confirm.
// Also each question tab of the multi-QUESTION wizard (Enter picks + advances);
// its "Type something." slot is driven by the inline custom-answer field.
function renderSingleCard(ask: ParsedAsk) {
  const card = askCard();
  qline(card, ask.question || ask.header);
  const opts = singleOptions(ask);
  const key = (activeId || "") + "§" + opts.map((o) => `${o.n}:${o.label}`).join("|");
  if (key !== liveAskFocusKey) { liveAskFocusKey = key; const sel = opts.findIndex((o) => o.selected); liveAskFocus = sel >= 0 ? sel : 0; }
  liveAskFocus = Math.max(0, Math.min(liveAskFocus, opts.length - 1));
  opts.forEach((o, i) => {
    const row = el("div", "ask-live-opt" + (i === liveAskFocus ? " focus" : ""));
    const lab = el("span", "ask-optlabel"); lab.textContent = `${o.n}. ${o.label}`; row.appendChild(lab);
    if (o.desc) { const d = el("span", "ask-optdesc"); d.textContent = o.desc; row.appendChild(d); }
    row.addEventListener("click", () => answerLiveAsk(o.n));
    row.addEventListener("mousemove", () => { if (liveAskFocus !== i) { liveAskFocus = i; paintLiveAskFocus(); } });
    card.appendChild(row);
  });
  if (ask.options.some((o) => isTypeSomething(o.label))) card.appendChild(customHintRow());
  card.tabIndex = 0;
  card.addEventListener("keydown", onSingleKey);
  focusCardUnlessTyping(card);
}

// The "+ add your own" affordance is no longer an inline input — it points at the NORMAL message box below,
// which now doubles as the free-text answer field (the user 2026-07-09). A static, non-interactive hint so
// the option is still discoverable on the card; the actual typing happens in the composer.
function customHintRow(): HTMLElement {
  const row = el("div", "ask-custom ask-custom-hint");
  const plus = el("span", "ask-custom-plus"); plus.textContent = "+"; row.appendChild(plus);
  const lab = el("span", "ask-custom-hint-text");
  lab.textContent = "add your own — type it in the message box below";
  row.appendChild(lab);
  return row;
}

// Keep the picker card's keyboard alive across re-renders (each render rebuilds the card), but NEVER yank
// focus from a user typing in the composer or any input — the footer stays visible now, so a re-mirror must
// not steal the caret mid-word (the user 2026-07-09).
function focusCardUnlessTyping(card: HTMLElement) {
  if (!isTypingTarget(document.activeElement)) card.focus({ preventScroll: true });
}

// "Type something" is the inline free-text slot (handled by the +custom field) and "Submit" is the dedicated
// button, so both are filtered out of the option rows. "Chat about this" is NOT filtered — it's a genuine
// answer the user can pick (the user 2026-06-27).
function isTypeSomething(label: string): boolean { return /^\s*type something/i.test(label); }
function isMetaOption(label: string): boolean { return /^\s*(type something|submit$)/i.test(label.trim()); }

// MULTI-select: a real checkbox per real option, an inline "add your own" field
// (drives the TUI's Type-something slot), and Submit / Cancel buttons.
// the checkable options of a multi card (skip meta rows like Type-something / Submit) — the rows the
// arrow keys step through and Space toggles.
function multiOptions(ask: ParsedAsk) {
  return ask.options.filter((o) => o.checked !== undefined && !isMetaOption(o.label));
}
function renderMultiCard(ask: ParsedAsk) {
  const card = askCard("ask-live-multi");
  qline(card, ask.question || ask.header);
  // A custom answer that's already been typed shows up as a normal checked option
  // (its label is the text, no longer "Type something"), so it renders as a checkbox.
  const checkOpts = multiOptions(ask);
  // keyboard focus (the user 2026-06-22, revised 2026-06-27): ↑/↓ move a highlight across the checkboxes AND
  // the Submit/Cancel buttons (one combined list); Enter TOGGLES the focused checkbox (or activates Submit/
  // Cancel when the highlight is on a button) — you arrow DOWN past the checkboxes to Submit, then Enter to
  // submit. Reset the highlight only when the screen actually changes, so a re-mirror keeps your place.
  const navCount = checkOpts.length + 2;   // checkboxes + Submit + Cancel
  const key = (activeId || "") + "§multi§" + checkOpts.map((o) => `${o.n}:${o.label}`).join("|");
  if (key !== liveAskFocusKey) { liveAskFocusKey = key; const sel = checkOpts.findIndex((o) => o.selected); liveAskFocus = sel >= 0 ? sel : 0; }
  liveAskFocus = Math.max(0, Math.min(liveAskFocus, navCount - 1));
  checkOpts.forEach((o, i) => {
    const row = el("label", "ask-check" + (i === liveAskFocus ? " focus" : ""));
    const box = document.createElement("input"); box.type = "checkbox"; box.checked = !!o.checked;
    box.addEventListener("change", () => toggleLiveAsk(o.n));
    row.appendChild(box);
    const lab = el("span", "ask-optlabel"); lab.textContent = o.label; row.appendChild(lab);
    if (o.desc && o.desc.toLowerCase() !== "submit") { const d = el("span", "ask-optdesc"); d.textContent = o.desc; row.appendChild(d); }
    row.addEventListener("mousemove", () => { if (liveAskFocus !== i) { liveAskFocus = i; paintMultiFocus(); } });
    card.appendChild(row);
  });
  // "add your own" is served by the message box below (see customHintRow) — type there + ⏎ to add a checked
  // custom row, then Submit. Only while the TUI still offers a Type-something slot.
  if (ask.options.some((o) => isTypeSomething(o.label))) card.appendChild(customHintRow());
  const actions = el("div", "ask-actions");
  const sIdx = checkOpts.length, cIdx = checkOpts.length + 1;   // Submit / Cancel positions in the combined nav list
  const submit = el("button", "ask-btn ask-btn-primary" + (liveAskFocus === sIdx ? " focus" : "")); submit.textContent = "Submit";
  submit.addEventListener("click", () => submitLiveAsk());
  submit.addEventListener("mousemove", () => { if (liveAskFocus !== sIdx) { liveAskFocus = sIdx; paintMultiFocus(); } });
  actions.appendChild(submit);
  const cancel = el("button", "ask-btn" + (liveAskFocus === cIdx ? " focus" : "")); cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => cancelLiveAsk());
  cancel.addEventListener("mousemove", () => { if (liveAskFocus !== cIdx) { liveAskFocus = cIdx; paintMultiFocus(); } });
  actions.appendChild(cancel);
  card.appendChild(actions);
  card.tabIndex = 0;
  card.addEventListener("keydown", onMultiKey);
  focusCardUnlessTyping(card);
}
// MULTI-select keyboard: ↑/↓ move the highlight, Space toggles the focused checkbox, Enter submits, a digit
// jumps to + toggles its row. The actual toggle is still the optimistic toggleLiveAsk (host re-mirrors).
function paintMultiFocus() {
  const checks = document.querySelectorAll("#live-ask .ask-live-multi .ask-check");
  const btns = document.querySelectorAll("#live-ask .ask-live-multi .ask-actions .ask-btn");
  const nC = checks.length;
  checks.forEach((r, i) => r.classList.toggle("focus", i === liveAskFocus));   // checkboxes are 0..nC-1
  btns.forEach((b, j) => b.classList.toggle("focus", nC + j === liveAskFocus)); // Submit = nC, Cancel = nC+1
}
function onMultiKey(e: KeyboardEvent) {
  const ask = activeId ? liveAsks.get(activeId) : null;
  if (!ask || ask.kind !== "multi") return;
  const opts = multiOptions(ask);
  const n = opts.length; if (!n) return;
  const navCount = n + 2;   // checkboxes + Submit + Cancel — arrow keys walk the whole list
  if (e.key === "ArrowDown") { e.preventDefault(); liveAskFocus = (liveAskFocus + 1) % navCount; paintMultiFocus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); liveAskFocus = (liveAskFocus - 1 + navCount) % navCount; paintMultiFocus(); }
  else if (e.key === " " || e.key === "Spacebar") { e.preventDefault(); if (liveAskFocus < n) toggleLiveAsk(opts[liveAskFocus].n); }
  else if (e.key === "Enter") {
    e.preventDefault();
    // Enter TOGGLES the focused checkbox (the user 2026-06-27) — NOT submit. Arrow down to the Submit button
    // and Enter there to submit; on Cancel, Enter cancels.
    if (liveAskFocus < n) toggleLiveAsk(opts[liveAskFocus].n);
    else if (liveAskFocus === n) submitLiveAsk();
    else cancelLiveAsk();
  }
  else if (/^[1-9]$/.test(e.key)) { const idx = opts.findIndex((o) => o.n === parseInt(e.key, 10)); if (idx >= 0) { liveAskFocus = idx; paintMultiFocus(); toggleLiveAsk(opts[idx].n); } }
}

// Review screen: show chosen answers + the Submit answers / Cancel options as
// buttons (each is just an option pick, reusing answerLiveAsk). A multi-question
// wizard reviews every question→answer pair here; a single question keeps the
// flat "Selected: …" line.
function renderSubmitCard(ask: ParsedAsk) {
  const card = askCard("ask-live-submit");
  if (ask.pairs && ask.pairs.length > 1) {
    qline(card, "Review your answers");
    for (const p of ask.pairs) {
      const row = el("div", "ask-pair");
      if (p.q) { const q = el("div", "ask-pair-q"); q.textContent = p.q; row.appendChild(q); }
      const a = el("div", "ask-pair-a"); a.textContent = "→ " + (p.a || "(no answer)"); row.appendChild(a);
      card.appendChild(row);
    }
  } else {
    qline(card, ask.question);
    const chosen = el("div", "ask-chosen");
    chosen.textContent = ask.chosen && ask.chosen.length ? "Selected: " + ask.chosen.join(", ") : "(nothing selected)";
    card.appendChild(chosen);
  }
  const actions = el("div", "ask-actions");
  for (const o of ask.options) {
    const b = el("button", "ask-btn" + (/submit/i.test(o.label) ? " ask-btn-primary" : ""));
    b.textContent = o.label;
    b.addEventListener("click", () => answerLiveAsk(o.n));
    actions.appendChild(b);
  }
  card.appendChild(actions);
}

// Free-text (any unstructured awaiting screen): a text input. The value is held
// in liveTextValue so a re-render (re-mirror) doesn't wipe what's been typed.
let liveTextValue = "";

// Paste fallback. In the VS Code webview, native Cmd+V reliably reaches the
// composer textarea but NOT these dynamically-created fields — typing works,
// the paste event simply never fires (the user's report, 2026-06-11). On
// Cmd/Ctrl+V: give native paste ~150ms to land (a paste event disarms the
// fallback — e.g. in the browser, where it just works), then ask the HOST for
// vscode.env.clipboard text ("readClipboard" → "clipboardText") and insert it
// at the cursor ourselves.
let pasteTarget: HTMLInputElement | HTMLTextAreaElement | null = null;
let pasteArm = 0;
function wirePasteFallback(inp: HTMLInputElement | HTMLTextAreaElement) {
  inp.addEventListener("paste", () => { pasteArm++; }); // native worked — disarm any pending fallback
  inp.addEventListener("keydown", (ev) => {
    const e = ev as KeyboardEvent; // union element type degrades the overload to plain Event
    if (!(e.metaKey || e.ctrlKey) || e.altKey || e.key.toLowerCase() !== "v") return;
    const arm = ++pasteArm;
    setTimeout(() => {
      if (pasteArm !== arm) return; // a real paste event landed in the meantime
      pasteTarget = inp;
      vscodeApi?.postMessage({ type: "readClipboard" });
    }, 150);
  });
}
function insertClipboardText(text: string) {
  const inp = pasteTarget;
  pasteTarget = null;
  if (!inp || !text || !document.contains(inp)) return;
  const s = inp.selectionStart ?? inp.value.length;
  const t = inp.selectionEnd ?? s;
  inp.value = inp.value.slice(0, s) + text + inp.value.slice(t);
  const pos = s + text.length;
  try { inp.setSelectionRange(pos, pos); } catch { /* ignore */ }
  inp.dispatchEvent(new Event("input", { bubbles: true })); // keep liveTextValue/draft sync
  inp.focus();
}
// Safeguard: the session is awaiting input but the parser can't map the screen to
// a known widget (an unrecognized prompt, a free-text editor, etc.). Warn loudly
// so a prompt is never silently missed — and offer a best-effort text input in
// case it IS a plain text prompt.
function renderUnknownCard() {
  const card = askCard("ask-live-unknown");
  const warn = el("div", "ask-warn");
  warn.textContent = "⚠ Waiting on a prompt the panel can’t read — answer it in the terminal.";
  card.appendChild(warn);
  // Free text goes through the NORMAL message box below now (composerAnswersAsk → "text" → askText); no
  // separate inline input (the user 2026-07-09).
  const hint = el("div", "ask-custom-hint-text");
  hint.textContent = "…or, if it’s a text prompt, type it in the message box below + ⏎.";
  card.appendChild(hint);
}

function paintLiveAskFocus() {
  const rows = document.querySelectorAll("#live-ask .ask-live-opt");
  rows.forEach((r, i) => r.classList.toggle("focus", i === liveAskFocus));
  renderAskPreview();   // step the preview to the newly-focused option (instant for per-option SDK previews)
  // tmux scrape path: the focused option has no preview of its own, but the ask carries the one scraped for
  // the cursor row — so nudge the TERMINAL cursor onto this option, and the next scrape captures ITS preview.
  // That's the only way to "see the other ones" without selecting (the user 2026-06-22). Debounced in
  // navLiveAsk so a fast ↑↑↑ only drives the final option. SDK options carry their own preview → no nudge.
  const ask = activeId ? liveAsks.get(activeId) : null;
  if (ask && ask.kind === "single") {
    const opts = singleOptions(ask);
    const o = opts[Math.max(0, Math.min(liveAskFocus, opts.length - 1))];
    if (o && !o.preview && ask.preview) navLiveAsk(o.n);
  }
}

// Move the TUI cursor to `target` WITHOUT selecting, so the tmux-scraped preview follows ↑/↓. Debounced so
// a fast keyboard sweep drives only the final option; NOT sendingGuard'd (it's navigation, not a commit).
let navTimer: ReturnType<typeof setTimeout> | undefined;
function navLiveAsk(target: number) {
  if (!activeId || !vscodeApi) return;
  const id = activeId;
  if (navTimer) clearTimeout(navTimer);
  navTimer = setTimeout(() => { navTimer = undefined; vscodeApi?.postMessage({ type: "navAsk", id, target }); }, 110);
}

// Single-select keyboard: ↑/↓ highlight (preview follows), Enter confirms, number jumps to + confirms.
function onSingleKey(e: KeyboardEvent) {
  const ask = activeId ? liveAsks.get(activeId) : null;
  if (!ask || ask.kind !== "single") return;
  const opts = singleOptions(ask); // same rows the card renders
  const n = opts.length;
  if (e.key === "ArrowDown") { e.preventDefault(); liveAskFocus = (liveAskFocus + 1) % n; paintLiveAskFocus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); liveAskFocus = (liveAskFocus - 1 + n) % n; paintLiveAskFocus(); }
  else if (e.key === "Enter") { e.preventDefault(); answerLiveAsk(opts[liveAskFocus].n); }
  else if (/^[1-9]$/.test(e.key)) {
    const idx = opts.findIndex((o) => o.n === parseInt(e.key, 10));
    if (idx >= 0) { liveAskFocus = idx; answerLiveAsk(opts[idx].n); }
  }
}

// One terminal-bound action in flight at a time (prevents double-submit); a short
// safety un-dim re-enables the card if a click aborted host-side (no-op) instead
// of hanging dimmed. Reset on every re-render (a new screen = the action resolved).
let sendingGuard = false;
function dimSending() {
  const host = document.getElementById("live-ask");
  if (!host) return;
  sendingGuard = true;
  host.classList.add("sending");
  if (sendingTimer) clearTimeout(sendingTimer);
  sendingTimer = setTimeout(() => { host.classList.remove("sending"); sendingTimer = undefined; sendingGuard = false; }, 700);
}
function answerLiveAsk(target: number) {
  if (!activeId || sendingGuard) return;
  if (vscodeApi) vscodeApi.postMessage({ type: "answerAsk", id: activeId, target });
  dimSending();
}
function toggleLiveAsk(target: number) { // optimistic; host toggles + re-mirrors. NOT guarded — rapid toggles allowed.
  if (activeId && vscodeApi) vscodeApi.postMessage({ type: "toggleAsk", id: activeId, target });
}
function addCustomLiveAsk(text: string) { // fills the TUI's Type-something slot inline; re-mirror shows it as a checked option
  if (activeId && vscodeApi) vscodeApi.postMessage({ type: "addCustomAsk", id: activeId, text });
  liveTextValue = "";
}
function submitLiveAsk() {
  if (!activeId || sendingGuard) return;
  if (vscodeApi) vscodeApi.postMessage({ type: "submitAsk", id: activeId });
  dimSending();
}
function cancelLiveAsk() {
  if (!activeId || sendingGuard) return;
  if (vscodeApi) vscodeApi.postMessage({ type: "cancelAsk", id: activeId });
  dimSending();
}
function sendTextLiveAsk(text: string) {
  if (!activeId || sendingGuard) return;
  if (vscodeApi) vscodeApi.postMessage({ type: "askText", id: activeId, text });
  liveTextValue = "";
  dimSending();
}

// Animated Claude-Code-style "working" line (sparkle + rotating gerund).
function elapsedMs(sinceMs: number | null): string {
  if (!sinceMs) return "";
  const s = Math.max(0, Math.floor((Date.now() - sinceMs) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// Right side of the status line: "Opus 4.8 xhigh" (model + effort) — the context %
// is shown separately as a battery bar (ctxBar). Sourced from the @claude-model /
// @claude-effort / @claude-context tmux vars. Shown in EVERY state, not just working.
// Each value is a little dropdown: picking an entry has the host inject the matching
// /model or /effort slash command into the session's pane; the label then updates
// when the TUI's statusline republishes the tmux vars (meta-pending bridges the gap).
type MetaKind = "mode" | "model" | "effort" | "fast";
// Model + effort choices come from the kernel's /models — the ONE list shared with the timeline lanes and the
// judge-tier settings (the user 2026-07-02, who wanted one shared code path, not hardcoded in multiple places), so
// the client holds no model literals (mirrors paletteColors above). Populated in place on load so META_CHOICES
// keeps its reference; the session picker appends its own "Default" (use-the-CLI-default) sentinel — not a model.
const MODEL_CHOICES: { label: string; value: string }[] = [];
const EFFORT_CHOICES: { label: string; value: string }[] = [];
fetch(kernelUrl("/models"), { cache: "no-store" }).then((r) => r.json()).then((d) => {
  if (Array.isArray(d.models)) { MODEL_CHOICES.length = 0; MODEL_CHOICES.push(...d.models, { label: "Default", value: "default" }); }
  if (Array.isArray(d.efforts)) { EFFORT_CHOICES.length = 0; EFFORT_CHOICES.push(...d.efforts); }
}).catch(() => { /* picker stays empty until it lands */ });
// Permission mode: the shift+tab cycle (no slash command), so the picker offers the three cycle modes;
// the host sets them by sending shift+tab the right number of times (the user 2026-06-16).
const MODE_CHOICES: { label: string; value: string }[] = [
  { label: "Normal", value: "default" },
  { label: "Accept edits", value: "acceptEdits" },
  { label: "Auto", value: "auto" },
  { label: "Plan", value: "plan" },
];
// Fast mode (the CLI's /fast — Opus-only research preview): a two-state toggle offered as the same
// dropdown shape as the other badges. The badge exists only when the session REPORTS a fast state
// (st.fast, from the SDK init's fast_mode_state) — a session that can't run it, or a tmux session
// whose statusline doesn't publish it yet, shows no dead control. The options speak the BADGE's two
// words — Fast/Slow, never On/Off (the user 2026-08-11: a badge reading "Slow" opened a menu of
// "On"/"Off", two vocabularies for one toggle; prettyFast is the one wording, the values stay the
// wire's on/off).
const FAST_CHOICES: { label: string; value: string }[] = [
  { label: "Fast", value: "on" },
  { label: "Slow", value: "off" },
];
// Per-session billing (the user 2026-08-08) — the Claude login vs the API key the manager's
// environment carries — is no longer a statusline badge: the SWITCHING control lives in the tab's
// right-click menu (showTabMenu's Billing flyout, the user 2026-08-09), still gated on st.authBoth
// so a one-auth machine shows no dead selector, and still labelled plainly 'API key' — no fragment
// of the key, not even a last-4 tail, is shipped or shown (2026-08-08, evening). The tab hover's
// Billing row keeps carrying the fact everywhere.
// the fast-mode state ("on"/"off"/"cooldown") → the badge label. ONE WORD (the user 2026-08-10, on a
// phone-width statusline), but the WORD carries the state: off reads "Slow", not a second "Fast" —
// tint alone (orange on, dim off) didn't say which side the toggle was on (the user 2026-08-11).
// ON keeps the CLI's fast orange (metaColor); the picker's ✓ names the state on click.
function prettyFast(f: string | undefined): string {
  const s = (f || "").toLowerCase();
  return s === "cooldown" ? "Cooldown"   // rate-limited: the CLI resumes fast mode when the limit resets
    : s === "on" ? "Fast" : "Slow";
}
// Whether the session's MODEL can run fast mode at all (the CLI's /fast is an Opus-only research
// preview). Gated HERE, on the model, because the CLI is no help: fast_mode_state arrives "off" with
// an EMPTY fast_mode_disabled_reason on a non-Opus session (verified 2026-08-10 against 2.1.226 on a
// fable session), so state alone would leave a dead toggle on every model /fast refuses (the user
// 2026-08-10). Unknown/default stays visible: the account default may be Opus, and hiding a live
// control is worse than a rare dead one.
function fastAvailable(st: Status): boolean {
  const m = (st.model || "").toLowerCase();
  return !m || m === "default" || m.includes("opus");
}
// the @claude-permission-mode var → a short readable badge label
function prettyMode(m: string | undefined): string {
  switch ((m || "").toLowerCase()) {
    case "plan": return "Plan";
    case "acceptedits": return "Accept edits";
    case "auto": return "Auto";
    case "dontask": return "Don’t ask";
    case "bypasspermissions": return "Bypass";
    default: return "Normal";   // default / normal / unknown
  }
}
const META_CHOICES: Record<MetaKind, { label: string; value: string }[]> = {
  mode: MODE_CHOICES, model: MODEL_CHOICES, effort: EFFORT_CHOICES, fast: FAST_CHOICES,
};
// the live value of a meta kind for the active session
function metaCurrent(kind: MetaKind, st: Status): string {
  return (kind === "model" ? st.model : kind === "effort" ? st.effort : kind === "fast" ? st.fast
    : st.mode) || "";
}

// Is this menu entry the session's current value? Effort matches exactly; the
// model var holds a display name ("Opus 4.8"), so match on the leading word.
function isCurrentMeta(kind: MetaKind, st: Status, value: string): boolean {
  if (kind === "effort") return (st.effort || "").toLowerCase() === value;
  if (kind === "fast") return (st.fast || "").toLowerCase() === value;   // "cooldown" marks neither entry
  if (kind === "mode") {
    const m = (st.mode || "").toLowerCase();
    if (value === "default") return m === "" || m === "default" || m === "normal";
    return m === value.toLowerCase();                                          // auto / acceptEdits / plan match exactly
  }
  return (st.model || "").toLowerCase().startsWith(value);
}

// "<sessionId>:<kind>" → set when the user picks a value, cleared when the tmux
// var actually changes (or after 20s, if the TUI rejected/ignored the command).
const metaPending = new Map<string, { was: string; until: number }>();
function isMetaPending(kind: MetaKind, st: Status): boolean {
  if (!activeId) return false;
  const key = `${activeId}:${kind}`;
  const p = metaPending.get(key);
  if (!p) return false;
  const cur = metaCurrent(kind, st);
  if (cur !== p.was || Date.now() > p.until) { metaPending.delete(key); return false; }
  return true;
}

// Three pulsing accent-blue dots shown IN the model badge while a /model switch resolves (the user
// 2026-07-03) — the romp loader's dot motif, so a wait always reads as "something's happening, it's
// romp". Cleared the instant syncMetaControls sees modelPending drop and the real name lands.
function metaDots(): HTMLElement {
  const d = el("span", "meta-dots");
  d.appendChild(el("i"));
  d.appendChild(el("i"));
  d.appendChild(el("i"));
  return d;
}

function metaButton(kind: MetaKind, text: string): HTMLElement {
  const btn = el("span", "meta-btn");
  btn.dataset.kind = kind;
  const label = el("span", "meta-label");
  label.textContent = text;
  btn.appendChild(label);
  const caret = el("span", "meta-caret");
  caret.textContent = "▾";
  btn.appendChild(caret);
  btn.title = kind === "model" ? "change model (sends /model)"
    : kind === "effort" ? "change thinking effort (sends /effort)"
    : kind === "fast" ? "toggle fast mode (sends /fast)"
    : "change permission mode (shift+tab cycle)";
  btn.addEventListener("click", (e) => { e.stopPropagation(); toggleMetaMenu(kind, btn); });
  return btn;
}

// The model/effort label tint, from the server-computed colormap RGB (by capability/effort rank, the user
// 2026-07-02) — "" for mode (untinted) or an unknown model/effort, which resets to the default gray.
function metaColor(kind: MetaKind, st: Status): string {
  // fast ON wears the CLI's own fast-mode orange (--fast, a status color) so the badge reads the same
  // here as in the Claude Code TUI; off/cooldown stay the default gray.
  if (kind === "fast") return (st.fast || "").toLowerCase() === "on" ? "var(--fast)" : "";
  const c = kind === "model" ? st.modelColor : kind === "effort" ? st.effortColor : undefined;
  return (c && c.length === 3) ? `rgb(${c[0]},${c[1]},${c[2]})` : "";
}

// Build or refresh the model/effort buttons inside #spinner-meta. Called from
// updateStatusline (fresh container) and the 1s ticker (label refresh in place).
function syncMetaControls(meta: HTMLElement, st: Status) {
  // order left→right: mode · model · effort · fast — the mode selector sits LEFT of the model name
  // (the user 2026-06-16); fast exists only when the session reports it (SDK init) AND the model can
  // run it (fastAvailable). Billing moved to the tab's right-click menu (the user 2026-08-09) — no
  // badge here.
  const fast = st.fast && fastAvailable(st) ? st.fast : "";   // reported AND the model can run it — else no dead control
  const want = [st.mode ? "mode" : "", st.model ? "model" : "", st.effort ? "effort" : "", fast ? "fast" : ""].filter(Boolean).join();
  const btns = Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[];
  if (btns.map((b) => b.dataset.kind).join() !== want) {
    meta.replaceChildren();
    if (st.mode) meta.appendChild(metaButton("mode", prettyMode(st.mode)));
    if (st.model) meta.appendChild(metaButton("model", st.model));
    if (st.effort) meta.appendChild(metaButton("effort", st.effort));
    if (fast) meta.appendChild(metaButton("fast", prettyFast(fast)));
  }
  for (const b of Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[]) {
    const kind = b.dataset.kind as MetaKind;
    const disp = kind === "mode" ? prettyMode(st.mode) : kind === "fast" ? prettyFast(st.fast)
      : metaCurrent(kind, st);
    const label = b.querySelector(".meta-label") as HTMLElement | null;
    // A switching MODEL shows animated dots, not the stale/premature name (the user 2026-07-03): the
    // server drives it (st.modelPending) — event-based, cleared the instant the new model actually lands —
    // and the local click heuristic (isMetaPending) covers the sub-second before the first server push.
    // model resolves live; effort reconnects to apply (--effort is connect-time) — both drive the switching-
    // dots from the server (st.modelPending / st.effortPending), with isMetaPending covering the sub-second
    // before the first server push (the user 2026-07-06).
    const pending = (kind === "model" && !!st.modelPending) || (kind === "effort" && !!st.effortPending)
      || isMetaPending(kind, st);
    const showDots = pending && (kind === "model" || kind === "effort");   // both apply via a resolve/reconnect the server tracks
    if (label) {
      if (showDots) {
        if (!label.querySelector(".meta-dots")) label.replaceChildren(metaDots());
      } else if (label.textContent !== disp || label.firstElementChild) {
        label.textContent = disp;
      }
      label.style.color = showDots ? "" : metaColor(kind, st);   // tint the model name / effort by the colormap rank
    }
    b.classList.toggle("meta-pending", pending);
  }
}

let metaMenuEl: HTMLElement | null = null;
function closeMetaMenu() {
  metaMenuEl?.remove();
  metaMenuEl = null;
}
function toggleMetaMenu(kind: MetaKind, btn: HTMLElement) {
  const wasOpen = metaMenuEl?.dataset.kind === kind;
  closeMetaMenu();
  if (wasOpen) return;
  const s = activeId ? sessions.get(activeId) : null;
  if (!s) return;
  // a pending permission/picker prompt owns the pane's keyboard — injecting a
  // slash command there would answer the prompt instead (host guards this too)
  if (s.status.state === "awaiting") return;
  const menu = el("div", "meta-menu");
  menu.dataset.kind = kind;
  for (const c of META_CHOICES[kind]) {
    const item = el("div", "meta-item" + (isCurrentMeta(kind, s.status, c.value) ? " current" : ""));
    item.textContent = c.label;
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      if (activeId && vscodeApi) {
        vscodeApi.postMessage({ type: kind === "model" ? "setModel" : kind === "effort" ? "setEffort" : kind === "fast" ? "setFast" : "setMode", id: activeId, value: c.value });
        const was = metaCurrent(kind, s.status);
        metaPending.set(`${activeId}:${kind}`, { was, until: Date.now() + 20_000 });
        btn.classList.add("meta-pending");
      }
      closeMetaMenu();
    });
    menu.appendChild(item);
  }
  document.body.appendChild(menu);
  // anchored ABOVE the button (the statusline sits at the bottom of the panel)
  const r = btn.getBoundingClientRect();
  menu.style.right = Math.max(8, window.innerWidth - r.right) + "px";
  menu.style.bottom = (window.innerHeight - r.top + 6) + "px";
  metaMenuEl = menu;
}
document.addEventListener("click", () => closeMetaMenu());
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMetaMenu(); });

// Context "battery": a small bar that FILLS with the context-used %, recolors as it
// fills (green → amber → red), with the % written inside. Replaces the plain "40%".
// CLICK → /compact the session, same as the timeline's battery click.
function ctxBar(): HTMLElement {
  const bar = el("span", "ctx-bar"); bar.id = "ctx-bar";
  bar.appendChild(el("span", "ctx-fill"));
  bar.appendChild(el("span", "ctx-text"));
  bar.appendChild(el("span", "ctx-scan"));   // compacting: teal rectangle whose right edge compresses leftward (as on the timeline)
  bar.addEventListener("click", () => {
    const s = activeId ? sessions.get(activeId) : null;
    if (!s || !vscodeApi) return;
    // awaiting: the pane's keyboard belongs to the prompt; compacting/closed: nothing to do
    if (s.status.state === "awaiting" || s.status.state === "compacting" || s.status.state === "closed") return;
    vscodeApi.postMessage({ type: "compactSession", id: activeId });
    bar.classList.add("ctx-clicked");   // immediate cue; the real compacting state takes over via the poll
  });
  return bar;
}
function setCtxBar(bar: HTMLElement, ctxStr: string | undefined, compacting = false, ctxColor?: number[]) {
  // Compacting: hide the fill/% (the number is about to be wrong anyway) and run
  // the scanning bar instead, mirroring the timeline's battery. No ctx% needed.
  bar.classList.toggle("ctx-compacting", compacting);
  if (compacting) {
    bar.classList.remove("ctx-clicked");   // the click's pulse cue did its job
    bar.style.display = "";
    bar.title = "compacting context…";
    const scan = bar.querySelector(".ctx-scan") as HTMLElement | null;
    if (scan) {
      // setCtxBar runs on BOTH the fresh bar updateStatusline builds AND the reused #ctx-bar the lighter
      // in-place refresh keeps — so phase-sync ONLY a fresh scan (no `swept` flag yet); re-seeding a reused
      // one every refresh restarted its animation, the jump the user saw (2026-07-02). The gradient still
      // (re)applies either way (recolors without restarting).
      const fresh = !scan.dataset.swept;
      if (fresh) scan.dataset.swept = "1";
      applyCompactSweep(scan, 3200, fresh);   // ctx-compress runs 3.2s
    }
    return;
  }
  // left compacting → clear the arm flag so the NEXT episode re-seeds the phase on this (possibly reused) bar
  const scanOff = bar.querySelector(".ctx-scan") as HTMLElement | null;
  if (scanOff) delete scanOff.dataset.swept;
  if (!ctxStr) { bar.style.display = "none"; return; }
  bar.style.display = "";
  const pct = Math.max(0, Math.min(100, parseInt(ctxStr, 10) || 0));
  const fill = bar.querySelector(".ctx-fill") as HTMLElement | null;
  const txt = bar.querySelector(".ctx-text") as HTMLElement | null;
  // The GLOBAL colormap (the user 2026-06-26): the kernel computes the fill color server-side (ctxColor =
  // ramp(context%) on the selected map, bright = full) so the chat battery matches the timeline + usage bars.
  // Fall back to the old traffic-light if an older kernel didn't ship a color.
  const fillBg = (ctxColor && ctxColor.length === 3) ? `rgb(${ctxColor.join(",")})`
    : (pct >= 85 ? "#c0392b" : pct >= 60 ? "#e0b020" : "#54B204");
  if (fill) { fill.style.width = pct + "%"; fill.style.background = fillBg; }
  if (txt) txt.textContent = pct + "%";
  bar.title = `context ${pct}% used — click to /compact`;
}

const CHIP_LABEL: Record<ChipState, string> = {
  working: "Working", ready: "Ready", awaiting: "Blocked",
  awaitingBg: "Awaiting",   // idle, waiting on background work it dispatched — straw, not working-yellow (the user 2026-07-13)
  idle: "Idle", closed: "Closed", compacting: "Compacting", clearing: "Clearing", blocked: "API error",
  retrying: "API retrying…",   // a live session stalled on an API rate-limit/overload auto-retry (api 2026-06-23)
  interrupting: "Interrupting…",   // stop sent, turn not yet settled (the user 2026-07-02) — clears to READY on its own
  opening: "Opening…",             // spawned, transcript not on disk yet — the first record clears it (the user 2026-08-05)
};

// A stop/interrupt button that lives beside the state badge in the statusline (the user 2026-06-19):
// it sends the SAME interrupt the composer's Ctrl+C does (host → Esc into the pane) — a less fiddly way
// to halt a run than Ctrl+C in this surface. It renders while the session is busy (working/compacting) AND
// while it's stuck retrying / blocked on an API error (the user 2026-07-06): there the interrupt both aborts
// the CLI's in-flight retry AND pauses romp's auto-retry into this ONE thread until the user lands a
// successful turn again (kernel _suppress_session_retry). Omitted in the truly idle states (ready/idle/
// awaiting — nothing to stop). A neutral white square; hovering reveals the red stop tint.
function stopButton(state?: ChipState): HTMLElement {
  const btn = el("button", "stop-btn");
  (btn as HTMLButtonElement).type = "button";
  const stuck = state === "retrying" || state === "blocked";
  btn.title = stuck
    ? "Stop retrying — interrupt this thread and hold its auto-retry off until you send it a message"
    : "Stop — interrupt this session (same as Ctrl+C)";
  btn.setAttribute("aria-label", stuck ? "Stop retrying this session" : "Interrupt session");
  btn.appendChild(el("span", "stop-icon"));   // a filled square (CSS), the universal stop glyph
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!activeId || !vscodeApi) return;
    vscodeApi.postMessage({ type: "interrupt", id: activeId });
    // acknowledge INSTANTLY, then get out of the way (the user 2026-07-05): the old ack swapped the square
    // for the word "interrupting…", which overflowed the fixed-width button onto the elapsed timer. The
    // CHIP owns this state — flip it to Interrupting… optimistically and REMOVE the button + timer; the
    // kernel's push rebuilds the statusline in this same shape (chip only, no button) a beat later.
    const sl = document.getElementById("statusline");
    const chip = sl ? (sl.querySelector(".chip") as HTMLElement | null) : null;
    if (chip) { chip.className = "chip chip-interrupting"; chip.textContent = CHIP_LABEL.interrupting; }
    document.getElementById("work-timer")?.remove();
    btn.remove();
  });
  return btn;
}

// The "Opening session" line + three staggered accent dots (the loading-state rule's small form): shown
// while a tab has NO session payload yet AND while the kernel itself reports state "opening" (spawned,
// transcript not on disk). Both clear on real events — the first payload, the first record.
function openingLine(): HTMLElement {
  const c = el("span", "compacting-line opening-line");
  c.appendChild(document.createTextNode("Opening session"));
  const dots = el("span", "opening-line-dots");
  for (let i = 0; i < 3; i++) dots.appendChild(el("span"));
  c.appendChild(dots);
  return c;
}

function updateStatusline() {
  const sl = document.getElementById("statusline");
  const s = activeId ? sessions.get(activeId) : null;
  if (!sl) return;
  if (activeId && !s) {
    // the tab is a loading placeholder (its session payload hasn't arrived) — the statusline said
    // whatever the PREVIOUS tab said, or a spawn stub's "Working" over a broken clock (the user
    // 2026-08-05, who wanted "opening" and animated dots until it's ready)
    sl.replaceChildren(openingLine());
    return;
  }
  if (!s) return;
  sl.replaceChildren();
  // Left: the state chip — WORKING gets a sine color-pulse + elapsed timer; idle
  // states get the plain chip (no timer). Right: model + effort · ctx%, always.
  if (s.status.state === "working") {
    // pill bg stays on the chip; the gradient text-clip lives on an inner span
    // (background-clip:text on the chip itself would erase the pill background)
    const chip = el("span", "chip chip-working");
    const label = el("span", "chip-pulse");
    label.textContent = CHIP_LABEL.working;
    chip.appendChild(label);
    sl.appendChild(chip);
    const timer = el("span", "status-timer");
    timer.id = "work-timer";
    timer.textContent = elapsedMs(s.status.sinceEpoch);
    sl.appendChild(timer);
  } else if (s.status.state === "awaitingBg") {
    // idle main thread, waiting on background work it dispatched (the user 2026-07-13): its own straw
    // chip — no pulse (nothing is computing HERE), but the elapsed timer stays so the wait has a clock
    const chip = el("span", "chip chip-awaitingBg");
    chip.textContent = CHIP_LABEL.awaitingBg;
    chip.title = "idle, waiting on background work it dispatched — clears when the result lands";
    sl.appendChild(chip);
    const timer = el("span", "status-timer");
    timer.id = "work-timer";
    timer.textContent = elapsedMs(s.status.sinceEpoch);
    sl.appendChild(timer);
  } else if (s.status.state === "compacting") {
    const c = el("span", "compacting-line");
    c.textContent = "⟳ Compacting context…";
    sl.appendChild(c);
  } else if (s.status.state === "clearing") {
    const c = el("span", "compacting-line");   // same in-progress line treatment as compacting (one style per info type)
    c.textContent = "⟳ Clearing conversation…";
    sl.appendChild(c);
  } else if (s.status.state === "opening") {
    sl.appendChild(openingLine());             // spawned, transcript not on disk yet — dots until the first record
  } else {
    const chip = el("span", `chip chip-${s.status.state}`);
    chip.textContent = CHIP_LABEL[s.status.state] ?? (s.status.state[0].toUpperCase() + s.status.state.slice(1).toLowerCase());
    sl.appendChild(chip);
  }
  // stop/interrupt button, right beside the state badge — while busy (working/compacting) AND while stuck
  // retrying / blocked on an API error, where it doubles as the per-thread auto-retry off-switch (the user
  // 2026-07-06). Omitted in idle states (nothing to interrupt) — the user 2026-06-19 — and while
  // INTERRUPTING (the stop is already in flight; re-pressing it is a lie — the user 2026-07-02).
  if (s.status.state === "working" || s.status.state === "compacting"
      || s.status.state === "retrying" || s.status.state === "blocked") sl.appendChild(stopButton(s.status.state));
  // The right-side cluster — dir · branch · mode/model/effort/fast badges · ctx battery — grouped in ONE
  // container (.sl-right) that carries the right-justify margin and wraps INTERNALLY with right-aligned
  // rows. Grouped, not flat: when a narrow pane wraps the statusline, flat children restart each extra row
  // at the LEFT edge (justify only reaches the row holding the auto margin) — the user 2026-08-10, on a
  // phone, wanted the wrapped controls to stay clustered on the right.
  const right = el("span", "sl-right");
  // The session's working directory (fixed at creation), leading the right-side cluster — just left of the
  // mode/model/effort controls (the user 2026-06-23). Basename only; full path on hover. Empty (rare, no
  // cwd) it's a zero-width spacer.
  const dir = el("span", "status-dir");
  if (s.cwd) {
    dir.appendChild(folderIcon());
    dir.appendChild(document.createTextNode(" " + (s.cwd.replace(/\/+$/, "").split("/").pop() || s.cwd)));
    // Click → run the configured folder opener for this dir (default: the OS opener — Finder / xdg-open —
    // overridable via ROMP_OPEN_FOLDER or ~/.config/romp/open-folder, e.g. open in Ghostty). asFolderLink wires
    // the data-act caught by the document-level openFolder delegate, so the per-push rebuild can't drop it.
    // activeId rides along so a REMOTE session's click SSHes out instead of no-op'ing on a local path (2026-07-03).
    asFolderLink(dir, s.cwd, activeId || undefined);
  }
  right.appendChild(dir);
  // The session's git branch, just right of the dir — only when known and only if the user has OPTED IN
  // (Settings → Chat → "Show git branch"; off by default — the user 2026-08-10, trimming the statusline
  // for narrow panes; it shipped on by default 2026-06-23). Read from the TOP-LEVEL session field,
  // never the head system event: that event is windowed out of the wire tail on any >250-event session, which
  // used to blank the branch on most sessions (the user 2026-06-30).
  if (loadSettings().showBranch === true && ((s.workTree && s.workTree.branch) || s.gitBranch)) {
    const br = el("span", "status-branch");
    const liveBr = (s.workTree && s.workTree.branch) || s.gitBranch;
    br.textContent = "⎇ " + liveBr;
    br.title = s.workTree ? `worktree ${s.workTree.dir} — git branch: ${liveBr}` : "git branch: " + liveBr;
    right.appendChild(br);
  }
  const meta = el("span", "spinner-meta");
  meta.id = "spinner-meta";
  syncMetaControls(meta, s.status);
  right.appendChild(meta);
  const bar = ctxBar();
  setCtxBar(bar, s.status.ctx, s.status.state === "compacting", s.status.ctxColor);
  right.appendChild(bar);
  sl.appendChild(right);
}

// Unsent composer text, per session — a draft belongs to the tab it was typed
// in: switching away stashes it (the box empties for the new tab's own draft),
// switching back restores it.
const drafts = new Map<string, string>();

// A CITATION seeded into the composer when you click a feed card's summary or a sub-goal into the chat (the
// user 2026-07-01): a dismissible chip that says "you're following up on THIS". It rides the message out as a
// romp follow-up (via askFollowUp on send), so the goal's context travels along and the goal reopens
// (done→working, unless cleared). Keyed by session id like drafts, so it belongs to its tab.
// TWO flavors (the user 2026-07-13): a GOAL citation (itemId — the card-click case above) and a QUOTE
// citation (quote [+ the containing turn's uuid] — highlighting transcript text seeds it). A quote chip has
// no goal to reopen: the send wraps the highlighted text into a plain message (quoteReplyBody) so the agent
// knows exactly which part is being replied to.
// The value is a LIST (the user 2026-08-04): quote chips can STACK. ⌘-selecting (Ctrl off-mac) another piece
// of text ADDS a context below the ones already held — ⌘ because that is the platform's "add a separate item
// to a selection", while Shift keeps its native browser meaning (extend the live selection, whose chip just
// follows). A plain select still replaces, as before. Flavors never mix: the send routes goal XOR quotes, so
// a goal chip always rides alone — any quote seed drops it, and a goal seed (feed card click) drops the
// quotes. Each chip keeps its own ✕; Backspace-at-start eats the newest first.
interface Citation { itemId?: string; title: string; quote?: string; uuid?: string | null; src?: string }   // src = a VS Code editor highlight's origin, workspace-relative file:lines (the user 2026-07-13)
const composerCitations = new Map<string, Citation[]>();

// FILE ATTACHMENTS for the composer (the user 2026-08-04): a file dragged, pasted, or picked into the
// chat box becomes a little THUMBNAIL in a strip above the textarea — not a raw path string dumped into
// the text. Per session like drafts, and with the DRAFT lifecycle (survive tab switch + reload, cleared
// on send), unlike citations (a "reply right now" intent that a tab switch abandons). On send the paths
// ride the outgoing text as a trailing line, quoted when they contain spaces — the same thing the old
// insert-at-cursor produced, now legible while you compose.
const composerFiles = new Map<string, string[]>();   // sid -> attachment paths, in drop order

// Files whose BYTES are still in flight to the kernel (shipFileToHost → dropFile → droppedPath ack).
// On a phone that round trip is seconds long — base64 + a fragmented WS send + the kernel write — and
// with nothing on screen it reads as a dead click (the user 2026-08-11). So the strip shows a pending
// chip (name + pulsing dots) from the instant the file is picked, replaced by the real thumbnail when
// the ack lands, or removed with a loud toast when the kernel nacks (dropSaveFailed). In-memory only,
// NOT persisted with drafts: a reload kills the page whose socket the ack would ride.
const pendingShips = new Map<string, string[]>();    // sid -> shipped names awaiting droppedPath

// The kernel saves a shipped file as drops/<ms>-<sanitized name> (_save_dropped_file). Mirror its
// sanitizer so an ack can be matched back to the pending chip it retires by basename suffix; the
// FIFO fallback in resolvePendingShip covers any mismatch (e.g. non-ASCII, where Python's \w and
// JS's \w disagree).
function shipSafeName(name: string): string {
  return (name.replace(/[^\w.-]+/g, "_").slice(-80)) || "drop";
}

function addPendingShip(id: string | null, name: string): void {
  if (!id) return;
  const list = pendingShips.get(id) || [];
  list.push(name);
  pendingShips.set(id, list);
  if (id === activeId) renderComposerFiles(id);
}

// An ack (or nack) retires ONE pending chip: the entry whose sanitized name `key` ends with — `key`
// is the saved path on ack (basename <ms>-<safe name>) or the raw name on nack, and both end with
// the sanitized original — else the oldest (the kernel answers a connection's dropFiles in order).
// Searched active-tab-first across all sessions because the ack carries no session id — like the
// attachment itself, it lands wherever the user now is.
function retirePendingShip(key: string): void {
  const k = "-" + shipSafeName(key.split("/").pop() || key);
  const ids = activeId ? [activeId, ...pendingShips.keys()] : [...pendingShips.keys()];
  for (const id of ids) {
    const list = pendingShips.get(id);
    if (!list || !list.length) continue;
    const i = list.findIndex((n) => k.endsWith("-" + shipSafeName(n)));
    list.splice(i >= 0 ? i : 0, 1);
    if (!list.length) pendingShips.delete(id);
    if (id === activeId) renderComposerFiles(id);
    return;
  }
}

// Persist drafts across a full RELOAD (the user 2026-06-25: a half-typed message must survive a refresh, not
// only a tab switch). The Map is in-memory, so mirror it into the webview's persisted state — the same store
// that remembers the active tab — and reload it at startup. restoreActiveDraftOnce() drops the active tab's
// draft back into the box ONE time after load, and only when the box is empty, so it never clobbers live typing.
// Citations persist alongside drafts (same lifecycle: survive reload + tab switch, cleared on send/dismiss).
function persistDrafts(): void {
  try {
    vscodeApi?.setState?.({ ...(vscodeApi.getState?.() || {}), drafts: Object.fromEntries(drafts),
                            citations: Object.fromEntries(composerCitations),
                            files: Object.fromEntries(composerFiles) });
  } catch { /* ignore */ }
}
try {
  const saved = ((vscodeApi?.getState?.() || {}) as any).drafts;
  if (saved && typeof saved === "object") for (const [k, v] of Object.entries(saved)) if (typeof v === "string") drafts.set(k, v);
  const savedFiles = ((vscodeApi?.getState?.() || {}) as any).files;
  if (savedFiles && typeof savedFiles === "object")
    for (const [k, v] of Object.entries(savedFiles)) {
      const paths = (Array.isArray(v) ? v : []).filter((x): x is string => typeof x === "string" && !!x);
      if (paths.length) composerFiles.set(k, paths);
    }
  const savedCites = ((vscodeApi?.getState?.() || {}) as any).citations;
  if (savedCites && typeof savedCites === "object")
    for (const [k, v] of Object.entries(savedCites)) {
      // each value is a LIST of chips; a pre-stack state (before 2026-08-04) stored a single object — wrap it
      const list: Citation[] = [];
      for (const c of (Array.isArray(v) ? v : [v]) as any[]) {   // either flavor restores: goal (itemId) or quote (quote [+ uuid])
        if (c && typeof c.title === "string" && (typeof c.itemId === "string" || typeof c.quote === "string"))
          list.push({ itemId: typeof c.itemId === "string" ? c.itemId : undefined, title: c.title,
                      quote: typeof c.quote === "string" ? c.quote : undefined,
                      uuid: typeof c.uuid === "string" ? c.uuid : null,
                      src: typeof c.src === "string" ? c.src : undefined });
      }
      if (list.length) composerCitations.set(k, list);
    }
} catch { /* ignore */ }

// Composer EDIT mode (per session): set when the user clicks a bubble's edit affordance — the composer
// then sends a rewindSend (branch from just before that message) instead of a plain message. The chip
// strip shows an "Editing message" pill whose ✕ (or Esc in the box) cancels back to normal sending.
const composerEdits = new Map<string, { uuid: string; orig: string }>();

function beginComposerEdit(sid: string, uuid: string, orig: string): void {
  composerEdits.set(sid, { uuid, orig });
  composerCitations.delete(sid);   // an edit replaces the message wholesale — mixed goal/quote context would mislead
  if (sid !== activeId) return;
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (ta) { ta.value = orig; growComposer(ta); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
  renderComposerChips(sid);
}

// Fire a DELETE rollback (the bubble's armed second click): post rewindDelete and overlay the outcome
// locally — the deleted bubble + tail dim as abandoned — until the kernel's cut payload arrives (it
// truncates the parse the moment the backend flag is set, so this bridges roughly one push).
function fireRewindDelete(sid: string, uuid: string): void {
  vscodeApi?.postMessage({ type: "rewindDelete", id: sid, uuid });
  pendingRewind.set(sid, { uuid, text: "", ts: Date.now(), bare: true });
  const ce = composerEdits.get(sid);
  if (ce && ce.uuid === uuid) cancelComposerEdit(sid);   // the message being edited was just deleted
  const s = sessions.get(sid);
  if (s) { reconcileRewind(s); appendActive(); }   // paint the overlay NOW (stale → window re-render)
}

function cancelComposerEdit(sid: string): void {
  if (!composerEdits.delete(sid)) return;
  if (sid !== activeId) return;
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (ta) { ta.value = ""; composerManualH = null; ta.style.height = ""; }
  drafts.delete(sid); persistDrafts();
  renderComposerChips(sid);
}

// Render (or clear) the citation chip strip for a session's composer. The chip is a pill in the romp accent
// with the cited title + an ✕; clicking the ✕ dismisses it, clicking the pill itself opens an AUDIT preview
// of the exact prompt romp will send (the user 2026-07-01). It lives ABOVE the textarea (a textarea can't
// host inline DOM), so it reads as attached-but-separate context, not typed text.
function renderComposerChips(id: string | null): void {
  const strip = document.getElementById("composer-chips");
  if (!strip) return;
  closeCitePreview();   // the chip is being rebuilt (or removed) → drop any open audit popover for the old chip
  strip.replaceChildren();
  // an EDIT pill outranks a citation chip (beginComposerEdit clears citations; this is the belt-and-braces)
  const edit = id ? composerEdits.get(id) : undefined;
  if (edit && id) {
    strip.style.display = "flex";
    const chip = el("div", "composer-chip composer-chip-edit");
    chip.title = "sending rewinds the conversation to this message and continues on a new branch — ✕ (or Esc) cancels";
    const mark = el("span", "composer-chip-mark"); mark.textContent = "✎"; chip.appendChild(mark);
    const label = el("span", "composer-chip-label");
    label.textContent = "Editing message — send rewinds the conversation here";
    chip.appendChild(label);
    const x = el("button", "composer-chip-x"); x.setAttribute("aria-label", "Cancel edit"); x.textContent = "✕";
    x.addEventListener("click", (e) => { e.stopPropagation(); cancelComposerEdit(id); });
    chip.appendChild(x);
    strip.appendChild(chip);
    return;
  }
  const cites = id ? composerCitations.get(id) : undefined;
  if (!cites || !cites.length) { strip.style.display = "none"; return; }
  strip.style.display = "flex";
  // one chip per held context, in the order they were added — the strip stacks them (flex column)
  cites.forEach((cite, i) => {
    const chip = el("div", "composer-chip");
    chip.title = cite.quote
      ? "replying to the highlighted text — click to preview the message · ✕ to remove"
      : "click to see exactly what romp will send the model · ✕ to remove";
    chip.style.cursor = "pointer";
    chip.addEventListener("click", () => { if (id) openCitePreview(id, chip); });
    // a quote chip wears the typographic quote mark; a goal chip keeps the follow-up arrow
    const mark = el("span", "composer-chip-mark"); mark.textContent = cite.quote ? "“" : "↩"; chip.appendChild(mark);
    const label = el("span", "composer-chip-label"); label.textContent = cite.title; chip.appendChild(label);
    const x = el("button", "composer-chip-x"); x.setAttribute("aria-label", "Remove citation"); x.textContent = "✕";
    x.addEventListener("click", (e) => { e.stopPropagation(); if (id) removeCitation(id, i); });   // stop → don't open the preview
    chip.appendChild(x);
    strip.appendChild(chip);
  });
}

// The attachment strip: one little thumbnail per dropped/pasted/picked file, above the textarea (the
// user 2026-08-04). An image shows its pixels — same-origin /file bytes on the web dashboard, the host
// imgRequest data-URL flow in the VS Code webview (the sandbox can't reach the kernel origin) — and any
// other file wears a compact ext + name chip. Click opens the file (the same openFile the path links
// use); the ✕ removes just that attachment. Rendered per session, like the citation chips beside it.
function renderComposerFiles(id: string | null): void {
  const strip = document.getElementById("composer-files");
  if (!strip) return;
  strip.replaceChildren();
  const paths = (id ? composerFiles.get(id) : undefined) || [];
  const pending = (id ? pendingShips.get(id) : undefined) || [];
  if (!paths.length && !pending.length) { strip.style.display = "none"; return; }
  strip.style.display = "flex";
  paths.forEach((p, i) => {
    const box = el("span", "composer-file");
    box.title = p + " — click opens it · ✕ removes";
    if (previewKind(p) === "img") {
      if (canPreview()) {
        // Name first, pixels when ready (the user 2026-08-04): the ext + name chip goes up IMMEDIATELY
        // and the image swaps in on its own load event — a slow /file fetch never leaves a blank box,
        // and a 404 simply keeps the chip (event-based on load/error, nothing timed).
        const doc = composerFileDoc(p);
        box.appendChild(doc);
        const img = document.createElement("img");
        img.className = "composer-file-img";
        img.alt = p;
        img.addEventListener("load", () => doc.replaceWith(img));
        img.src = fileUrl(p, id);
      } else {
        const w = buildPathImg(p);                 // VS Code: host-read data URL fills in; chip until then
        w.classList.add("composer-file-hostimg");
        box.appendChild(w);
      }
    } else {
      box.appendChild(composerFileDoc(p));
    }
    box.addEventListener("click", () => { vscodeApi?.postMessage({ type: "openFile", path: p, id: id || undefined }); });
    const x = el("button", "composer-file-x");
    x.setAttribute("aria-label", "Remove attachment");
    x.textContent = "\u2715";
    x.addEventListener("click", (e) => { e.stopPropagation(); if (id) removeComposerFile(id, i); });
    box.appendChild(x);
    strip.appendChild(box);
  });
  // In-flight ships, after the real thumbnails: name + pulsing dots until the droppedPath ack swaps
  // in the thumbnail above. The ✕ removes just the CHIP (there is no cancelling a send in flight) —
  // the escape hatch for an ack lost to a mid-ship disconnect, so a stuck chip is never trapped.
  pending.forEach((n, i) => {
    const box = el("span", "composer-file composer-file-pending");
    box.title = n + " — uploading";
    const nm = el("span", "composer-file-name");
    nm.textContent = n;
    const dots = el("span", "composer-ship-dots");
    dots.append(el("i"), el("i"), el("i"));
    box.append(nm, dots);
    const x = el("button", "composer-file-x");
    x.setAttribute("aria-label", "Dismiss pending attachment");
    x.textContent = "✕";
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      const list = id ? pendingShips.get(id) : undefined;
      if (!list) return;
      list.splice(i, 1);
      if (!list.length && id) pendingShips.delete(id);
      renderComposerFiles(id);
    });
    box.appendChild(x);
    strip.appendChild(box);
  });
}

// A non-image attachment's face: the extension in a small badge + the basename, both text (no glyph).
function composerFileDoc(p: string): HTMLElement {
  const chip = el("span", "composer-file-doc");
  const dot = p.lastIndexOf(".");
  const ext = el("span", "composer-file-ext");
  ext.textContent = (dot > 0 ? p.slice(dot + 1) : "file").slice(0, 5).toUpperCase();
  const nm = el("span", "composer-file-name");
  nm.textContent = p.split("/").pop() || p;
  chip.append(ext, nm);
  return chip;
}

function addComposerFile(id: string | null, path: string): void {
  if (!id || !path) return;
  const list = composerFiles.get(id) || [];
  if (!list.includes(path)) list.push(path);       // the same file dropped twice attaches once
  composerFiles.set(id, list);
  persistDrafts();
  if (id === activeId) renderComposerFiles(id);
}

function removeComposerFile(id: string, idx: number): void {
  const list = composerFiles.get(id);
  if (!list || idx < 0 || idx >= list.length) return;
  list.splice(idx, 1);
  if (!list.length) composerFiles.delete(id);
  persistDrafts();
  if (id === activeId) renderComposerFiles(id);
}

// Audit popover — the EXACT wrapped body romp will send the model for this citation, fetched from the kernel's
// /followup-preview (the SAME _followup_body the send path uses, so it can't drift). Shows the injected
// goal-context quote + your current draft (or a placeholder) + the hidden romp-goal-id marker, in a scrollable
// monospace box anchored above the chip. Click outside or Esc closes. (the user 2026-07-01)
let citePreviewEl: HTMLElement | null = null;
function closeCitePreview(): void {
  if (citePreviewEl) { citePreviewEl.remove(); citePreviewEl = null; }
  document.removeEventListener("keydown", citePreviewKey, true);
  document.removeEventListener("mousedown", citePreviewOutside, true);
}
function citePreviewKey(e: KeyboardEvent): void { if (e.key === "Escape") { e.preventDefault(); closeCitePreview(); } }
function citePreviewOutside(e: MouseEvent): void {
  if (citePreviewEl && !citePreviewEl.contains(e.target as Node)) closeCitePreview();
}
function openCitePreview(id: string, anchor: HTMLElement): void {
  const cites = composerCitations.get(id);
  if (!cites || !cites.length) return;
  if (citePreviewEl) { closeCitePreview(); return; }   // second click on the chip toggles it closed
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  const draft = (ta?.value || "").trim();
  const pop = el("div", "cite-preview");
  const head = el("div", "cite-preview-head"); head.textContent = "What romp will send the model";
  const body = el("pre", "cite-preview-body"); body.textContent = "loading…";
  pop.append(head, body);
  document.body.appendChild(pop);
  citePreviewEl = pop;
  // position: above the chip, left-aligned to it, clamped into the viewport
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8)) + "px";
  pop.style.top = Math.max(8, r.top - pop.offsetHeight - 8) + "px";
  document.addEventListener("keydown", citePreviewKey, true);
  document.addEventListener("mousedown", citePreviewOutside, true);
  const goalId = cites.find((c) => c.itemId)?.itemId;
  if (!goalId) {
    // QUOTE chips compose CLIENT-side (quoteReplyBody IS the send path — no kernel wrap, so nothing to
    // fetch and nothing to drift). The preview is the WHOLE outgoing message — every stacked quote in
    // order, whichever chip was clicked. Same popover, same clamp-after-content.
    body.textContent = quoteReplyBody(cites.filter((c) => c.quote), draft || "(your message)");
    pop.style.top = Math.max(8, r.top - pop.offsetHeight - 8) + "px";
    return;
  }
  const url = kernelUrl("/followup-preview?itemId=" + encodeURIComponent(goalId) + "&text=" + encodeURIComponent(draft));
  fetch(url, { cache: "no-store" }).then((r) => r.json()).then((d) => {
    if (citePreviewEl !== pop) return;   // closed while loading
    body.textContent = (d && typeof d.body === "string" && d.body) ? d.body : "(no context — this goal may have been cleared)";
    // re-clamp now that the real content set the height
    pop.style.top = Math.max(8, r.top - pop.offsetHeight - 8) + "px";
  }).catch(() => { if (citePreviewEl === pop) body.textContent = "(couldn't load the preview)"; });
}

// Seed the citation for a session (from a feed card click that landed in the chat), replacing everything
// held — a goal chip rides alone, quote chips included (flavors never mix; the send routes goal XOR quotes).
function setCitation(id: string, cite: Citation): void {
  composerCitations.set(id, [cite]);
  persistDrafts();
  if (id === activeId) { renderComposerChips(id); focusComposer(); }
}

// The outgoing body for QUOTE citations (the user 2026-07-13): the highlighted text rides ahead of the
// typed message as a markdown quote block, so the agent knows exactly which part is being replied to.
// Also what the chip's audit preview shows — one function, no drift. Stacked chips (the user 2026-08-04)
// become one section each, in the order they sit in the strip. `src` (the VS Code editor flavor,
// 2026-07-13) names where a highlight came from — a workspace-relative file:lines — so that section's
// lead-in points the agent at the code, not the conversation.
function quoteReplyBody(cites: { quote?: string; src?: string | null }[], text: string): string {
  const sections = cites.map((c) => {
    const q = (c.quote || "").split("\n").map((l) => "> " + l).join("\n");
    const lead = c.src ? "Replying to this highlighted code (" + c.src + "):" : "Replying to this part of the conversation:";
    return lead + "\n" + q;
  });
  return sections.join("\n\n") + "\n\n" + text;
}

// HIGHLIGHT-TO-REPLY (the user 2026-07-13): selecting text in the chat transcript seeds the composer chip
// as reply context, exactly like a distilled-summary click — but quote-flavored. Event-based on
// selectionchange; a selection qualifies only when BOTH endpoints sit inside transcript turns (.turn), so
// composer/tab-bar/modal selections never seed. A COLLAPSE never clears the chip: clicking into the
// composer to type collapses the selection, and must not eat the chip it just made — dismissal stays the
// ✕ / Backspace-at-start. Unlike setCitation this never focuses the composer: stealing focus mid-drag
// would collapse the very selection being made (the focusCardUnlessTyping lesson).
const QUOTE_CAP = 4000;   // a selection can be huge; the send stays bounded
function mkQuoteCitation(quote: string, uuid: string | null, src?: string): Citation {
  const snip = quote.replace(/\s+/g, " ").trim();
  // an editor highlight leads its chip with where it came from (file:lines), then the snippet
  const title = (src ? src + " — " + snip : snip).slice(0, 140);
  return { title, quote: quote.slice(0, QUOTE_CAP), uuid, src: src || undefined };
}

// One selection GESTURE owns one chip (the user 2026-08-04). selectionchange fires dozens of times as a
// drag grows, so appending per event would spray chips — instead the live gesture WRITES THROUGH to the
// chip it owns (quoteSeedIdx), and only a NEW gesture decides replace-vs-add. The deciding event is the
// mousedown that starts the gesture: plain → this selection becomes the whole context (the pre-stack
// behavior); ⌘ (Ctrl off-mac) held → the chips already held stay and the new one lands below them. A
// shift-mousedown EXTENDS the live selection (the browser's own semantics), so it neither resets the
// gesture nor re-reads the modifier — the existing chip just follows the bigger selection. Keyboard
// extension (shift+arrows) fires no mousedown and rides the same write-through.
// ONLY a mousedown ends a gesture. A mid-drag tick can momentarily fail to qualify (the cursor crossing
// the gap between two turns puts an endpoint outside any .turn) — treating that as a gesture end made
// the very next qualifying tick APPEND AGAIN, so one ⌘-drag listed the same context twice (the user
// 2026-08-04). Plain select masked the same flicker, because its re-seed replaces.
let quoteAddHeld = false;              // ⌘/Ctrl was down at the gesture-starting mousedown
let quoteSeedIdx: number | null = null;   // index of the chip the live gesture owns — its write-through target
document.addEventListener("mousedown", (e) => {
  if (e.shiftKey) return;                     // shift = extend the live selection: same gesture, same chip
  quoteAddHeld = e.metaKey || e.ctrlKey;
  quoteSeedIdx = null;
}, true);

// Gesture-aware seed for TRANSCRIPT selections (the selectionchange listener + the Enter-to-reply shortcut).
function seedTranscriptQuote(id: string, quote: string, uuid: string | null): void {
  const chip = mkQuoteCitation(quote, uuid);
  // a quote seed drops a goal chip (flavors never mix — the send routes goal XOR quotes)
  const list = (composerCitations.get(id) || []).filter((c) => !c.itemId);
  let idx = quoteSeedIdx != null && quoteSeedIdx < list.length ? quoteSeedIdx : null;   // the live gesture's chip
  if (idx == null) {
    if (quoteAddHeld && list.length) idx = list.length;   // ⌘-select: add a context below the held ones
    else { list.length = 0; idx = 0; }                    // plain select: this is the context now
  }
  list[idx] = chip;
  // Identical text never lists twice (the user 2026-08-04): whatever path re-cites text already held —
  // re-selecting the same sentence, a double-click repeated, a drag re-traced after a transcript rebuild
  // killed the selection — the OTHER copy collapses and the gesture's own chip survives.
  for (let i = list.length - 1; i >= 0; i--) {
    if (i !== idx && list[i].quote === chip.quote) {
      list.splice(i, 1);
      if (i < idx) idx--;
    }
  }
  quoteSeedIdx = idx;
  composerCitations.set(id, list);
  persistDrafts();
  if (id === activeId) renderComposerChips(id);
}

// The EDITOR's highlight owns ONE chip — update it in place, append below if absent. A cursor move in the
// editor must adjust its own context, never wipe transcript quotes stacked beside it (the user 2026-08-04).
function seedEditorQuote(id: string, quote: string, src?: string): void {
  const chip = mkQuoteCitation(quote, null, src);
  const list = (composerCitations.get(id) || []).filter((c) => !c.itemId);   // flavors never mix
  const i = list.findIndex((c) => !!c.src);
  if (i >= 0) list[i] = chip; else list.push(chip);
  composerCitations.set(id, list);
  persistDrafts();
  if (id === activeId) renderComposerChips(id);
}
// The current selection when (and only when) it qualifies as transcript text — both endpoints inside
// `.turn` elements, non-collapsed, non-empty. Shared by the selectionchange seeding below and the
// Enter-to-reply shortcut (window keydown), so the two can never disagree on what counts.
// A ⌘-multi-select holds SEVERAL ranges — the browser's own discontiguous selection keeps the earlier
// highlight live while a new one is dragged. The chip being built belongs to the range the user is
// ACTIVELY dragging — the newest — so this reads THAT range alone, never sel.toString(), which
// concatenates every range and merged two discontiguous sections into one chip (the user 2026-08-04).
// The earlier ranges already own their chips from their own gestures. Endpoints come from the range's
// own containers (anchor/focus describe only the last-modified range, and flip on a backwards drag).
function transcriptSelection(): { text: string; uuid: string | null } | null {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return null;
  const r = sel.getRangeAt(sel.rangeCount - 1);
  if (r.collapsed) return null;                             // the ACTIVE range must be a real span
  const turnOf = (n: Node | null) => {
    const e = n instanceof Element ? n : n?.parentElement;
    return e?.closest?.(".turn") ?? null;
  };
  const a = turnOf(r.startContainer), f = turnOf(r.endContainer);
  if (!a || !f) return null;                                // both endpoints must be transcript turns
  const text = r.toString().trim();
  if (!text) return null;
  return { text, uuid: a.getAttribute("data-uuid") };
}
document.addEventListener("selectionchange", () => {
  if (!activeId) return;
  const q = transcriptSelection();
  // Never clear chips on a collapse — and never touch the GESTURE either: a mid-drag tick can flicker
  // non-qualifying (endpoint in the gap between turns), and ending the gesture there made the next tick
  // append a second copy of the same context (the user 2026-08-04). Gestures end at the next mousedown.
  if (!q) return;
  seedTranscriptQuote(activeId, q.text, q.uuid);
});

// Dismiss a citation — via its chip's ✕ (that exact chip, by index) or Backspace at the very start of an
// empty composer (no index → the NEWEST chip goes first, so repeated presses eat the stack bottom-up,
// each "like a character" as the user asked). Re-focuses the box so typing continues uninterrupted.
function removeCitation(id: string, idx?: number): void {
  const list = composerCitations.get(id);
  if (!list || !list.length) return;
  list.splice(idx == null ? list.length - 1 : idx, 1);
  if (!list.length) composerCitations.delete(id);
  persistDrafts();
  if (id === activeId) { renderComposerChips(id); focusComposer(); }
}

function focusComposer(): void {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  ta?.focus();
}

// A VS Code editor selection collapsed (the user deselected / clicked away) — drop the chip that
// highlight seeded so an abandoned selection doesn't leave stale context (the user 2026-07-14). Scoped
// tight: ONLY the editor-seeded chip (it alone carries `src`; a transcript-highlight quote chip has a
// uuid and no src, a goal chip has an itemId — both are left alone), and ONLY while the composer is
// empty, so a reply already being typed against the code keeps its quote. Unlike removeCitation this
// never focuses the composer — the user is in the editor, and yanking focus to the chat would be wrong.
function clearEditorCitation(id: string | null): void {
  if (!id) return;
  const list = composerCitations.get(id);
  const kept = list ? list.filter((c) => !c.src) : [];
  if (!list || kept.length === list.length) return;        // no editor-highlight chip → leave the rest alone
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (id === activeId && ta && ta.value.trim()) return;    // a reply is in progress → keep the context
  if (kept.length) composerCitations.set(id, kept); else composerCitations.delete(id);
  persistDrafts();
  if (id === activeId) renderComposerChips(id);            // rebuild the strip WITHOUT stealing focus
}

// Drop any session's citation that points at a now-cleared card (itemId = the goal node id, sid-prefixed so
// it belongs to exactly one session's composer). `itemIds` (when the kernel sends it) is the cleared card's
// whole SUBTREE: a chip can cite a SUB-goal of the card (wireNodeZones sends the clicked node's id), and
// clearing the card must drop that chip too, not just a top-goal one (the user 2026-07-01). Re-renders the
// strip if it was the active tab's chip.
function dropCitationByItem(itemId: string, itemIds?: string[]): void {
  const gone = new Set(itemIds && itemIds.length ? itemIds : [itemId]);
  gone.add(itemId);
  let changed = false;
  for (const [sid, list] of composerCitations) {
    const kept = list.filter((c) => !(c.itemId && gone.has(c.itemId)));   // quote chips cite no goal — a card clear never drops them
    if (kept.length === list.length) continue;
    if (kept.length) composerCitations.set(sid, kept); else composerCitations.delete(sid);
    changed = true;
    if (sid === activeId) renderComposerChips(sid);
  }
  if (changed) persistDrafts();
}
let draftsRestored = false;
function restoreActiveDraftOnce(): void {
  if (draftsRestored) return;
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta || !activeId) return;            // wait until the active tab is established after load
  draftsRestored = true;
  if (!ta.value) { const d = drafts.get(activeId); if (d) { ta.value = d; growComposer(ta); } }
  renderComposerChips(activeId);   // a citation persisted across the reload
  renderComposerFiles(activeId);   // attachments persisted across the reload → thumbnails again → show its chip again
}

// Most-recently-ACTIVATED session ids, current first — the recency the shell's session jump
// switcher (Cmd/Ctrl+O, palette-main.ts) sorts by, read directly off this window (same-origin).
// Session order elsewhere stays kernel-authoritative; this is only "what did I look at last".
const sessionMru: string[] = [];
(window as any).__rompMru = sessionMru;
// …and the sessions this page knows, for the same switcher: every tab in tab order — local AND
// the federation-merged remote ones (host-prefixed ids), with the tab's identity colors — so the
// switcher's rows carry the exact identity language the tabs do (the user 2026-08-08: bold name
// in the session color, host: prefix in the dim italic). A snapshot accessor, not live state.
(window as any).__rompSessionList = () =>
  order.map((id) => {
    const m = tabMeta.get(id);
    return { id, name: m?.name || id, bg: m?.color?.bg || "", fg: m?.color?.fg || "" };
  });
function noteMru(id: string): void {
  const i = sessionMru.indexOf(id);
  if (i >= 0) sessionMru.splice(i, 1);
  sessionMru.unshift(id);
  if (sessionMru.length > 50) sessionMru.pop();
}

function setActive(id: string, anchor?: string, anchorT?: number, anchorKind?: string) {
  noteMru(id);
  if (activeId === id && anchor == null && anchorT == null) return; // already active, nothing to do
  closeMetaMenu(); // an open model/effort menu targets the tab we're leaving
  pendingAnchorT = anchorT ?? null;
  pendingAnchorKind = anchorKind ?? null;
  // Remember where we were in the tab we're leaving, so we can restore it.
  const content = document.getElementById("content");
  if (content && activeId && activeId !== id) {
    const cur = views.get(activeId);
    if (cur) { cur.scrollTop = content.scrollTop; cur.stick = nearBottom(content); }
  }
  // Stash the leaving tab's draft; show the entering tab's own (usually empty).
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (ta && activeId !== id) {
    if (activeId) {
      if (ta.value) drafts.set(activeId, ta.value); else drafts.delete(activeId);
      // A citation chip is a "reply to this card right now" intent — switching tabs abandons it, so drop the
      // leaving tab's chip (the user 2026-07-01). A feed click that seeds a chip sets it AFTER this switch.
      composerCitations.delete(activeId);
    }
    ta.value = drafts.get(id) ?? "";
    growComposer(ta);
    renderComposerChips(id);   // the entering tab's own citation chip (if any)
    renderComposerFiles(id);   // …and its attachment thumbnails (draft lifecycle: they survive the switch)
    persistDrafts();   // the leaving tab's draft was just stashed → keep the persisted copy in sync
  }
  pendingAnchor = anchor ?? null;
  pendingAnchorIntent = anchor ? (anchorKind ?? null) : null;
  activeId = id;
  try { vscodeApi?.setState?.({ ...(vscodeApi.getState?.() || {}), activeId: id }); } catch { /* ignore */ }
  renderTabs();
  showActive();
  schedulePrebuild(); // warm the OTHER tabs in idle (MRU-first) so the next switch is instant
}

function cycleTab(dir: number) {
  if (order.length < 2 || !activeId) return;
  const i = order.indexOf(activeId);
  if (i < 0) return;
  setActive(order[(i + dir + order.length) % order.length]);
}

// First event carrying a uuid — a stable identity for "which transcript is this".
// A fork (the tab re-pointed onto a new transcript) changes it; more turns on the
// same transcript keep it.
function firstUuid(events: ChatEvent[]): string | null {
  for (const e of events) if (e.uuid) return e.uuid;
  return null;
}
// Do the two event lists share at least one uuid? Then one is a windowed/continued view of the SAME
// transcript (an append, or a tail-window slide that dropped older events off the front) — NOT a wholesale
// replacement (a /clear-style fork, which reuses no uuid). Used to tell a fork from a mere window slide.
function sharesAnyUuid(a: ChatEvent[], b: ChatEvent[]): boolean {
  const seen = new Set<string>();
  for (const e of b) if (e.uuid) seen.add(e.uuid);
  for (const e of a) if (e.uuid && seen.has(e.uuid)) return true;
  return false;
}

function upsert(msg: any) {
  const existed = sessions.has(msg.id);
  const prev = sessions.get(msg.id);
  awaitingFull.delete(msg.id);   // a full session landed → this session is re-based; a later gap may ask again
  const s: Session = {
    id: msg.id,
    name: msg.name,
    color: msg.color || null,
    events: msg.events || (prev ? prev.events : []),
    status: msg.status || (prev ? prev.status : { state: "idle", sinceEpoch: null }),
    firstSeen: msg.firstSeen ?? (prev ? prev.firstSeen : undefined),
    cwd: msg.cwd ?? (prev ? prev.cwd : ""),
    // top-level git branch (the user 2026-06-30): the status-bar branch + tab tooltip read this, NOT the head
    // system event — that event lives at events[0] and the WIRE_TAIL window drops it on any >250-event session,
    // so the branch used to vanish there. A chatTail delta omits it → keep the last-known via prev.
    gitBranch: msg.gitBranch ?? (prev ? prev.gitBranch : ""),
    workTree: msg.workTree ?? (prev ? prev.workTree : null),
    // A trimmed full send carries headFrom/headTotal; a whole-transcript send omits them (headFrom 0).
    headFrom: msg.headFrom ?? 0,
    headTotal: msg.headTotal ?? ((msg.events || (prev ? prev.events : [])).length),
    bgTasks: ("bgTasks" in msg) ? msg.bgTasks : (prev ? prev.bgTasks : undefined),
    hideFromFeed: ("hideFromFeed" in msg) ? !!msg.hideFromFeed : (prev ? prev.hideFromFeed : undefined),
    postalServiceOff: ("postalServiceOff" in msg) ? !!msg.postalServiceOff : (prev ? prev.postalServiceOff : undefined),
    notify: ("notify" in msg) ? !!msg.notify : (prev ? prev.notify : undefined),
  };
  sessions.set(msg.id, s);
  reconcileRewind(s);       // pending-rewind overlay + the editable-bubble set, from the fresh payload
  reconcileOptimistic(s);   // re-assert (or retire) any in-flight optimistic sends across the rebuild
  // The kernel re-sends the FULL "session" payload on every push. Distinguish an APPEND (more turns
  // on the SAME transcript — the common case) from a FORK (the tab re-pointed onto a NEW transcript,
  // events replaced wholesale, e.g. a /clear-style fork). Only a FORK drops the cached DOM and
  // rebuilds; an append lets syncView add just the new turns AND keeps the user's scroll position —
  // so new content no longer snaps the view to the bottom (the user 2026-06-15). Fork = the
  // transcript identity (first event's uuid) changed; an append keeps it.
  // A true FORK replaces the transcript wholesale (a /clear-style re-point) → the new events share NO uuid
  // with what we had. Comparing only the FIRST uuid mis-fired on a mere WINDOW SLIDE: once a session passes
  // WIRE_TAIL events, a full-session push re-windows to the last N, so the first uuid changes though it's the
  // same transcript — which dropped the DOM and snapped the view to the bottom (the user 2026-07-06). Detect a
  // fork by the ABSENCE of any shared event uuid instead.
  const forked = !!(existed && msg.events && msg.events.length && prev && prev.events.length
                    && !sharesAnyUuid(msg.events, prev.events));
  // Preserve the reader's position across ANY active-tab rebuild (fork OR slid tail-window): capture whether
  // they were at the bottom + their anchor turn BEFORE we drop/rebuild the DOM, so a push never SNAPS a
  // scrolled-up reader down (the user 2026-07-06). Only a genuinely-at-bottom reader follows new content.
  let _scrollContent: HTMLElement | null = null, _scrollAnchor: { uuid: string; y: number } | null = null, _wasNear = true;
  if (msg.id === activeId && !(existed && !forked)) {   // only the rebuild branch (appendActive preserves on its own)
    _scrollContent = document.getElementById("content");
    const _v0 = views.get(msg.id);
    _wasNear = !_scrollContent || !_v0 || !_v0.shown || nearBottom(_scrollContent);
    _scrollAnchor = (!_wasNear && _scrollContent && _v0) ? captureScrollAnchor(_scrollContent, _v0) : null;
  }
  if (forked) {
    const v = views.get(msg.id);
    if (v) { v.el.remove(); views.delete(msg.id); }
  }
  if ("ledger" in msg) ledgers.set(msg.id, msg.ledger ?? null);
  if (!existed) order.push(msg.id);
  if (!activeId) activeId = msg.id;
  if (wantActive && msg.id === wantActive) { wantActive = null; setActive(msg.id); }   // restore persisted tab on arrival
  renderTabs();                                   // a new id appended to `order` above → strip repaints in kernel order
  // Active tab: a content refresh appends + preserves scroll (appendActive); a new tab or a fork
  // lands at the bottom/anchor (showActive). This is what keeps new pushes from snapping to bottom.
  if (msg.id === activeId) {
    if (existed && !forked) {
      appendActive();
    } else {
      showActive();
      // a scrolled-up reader keeps their spot across the rebuild; a true fork's anchor uuid isn't in the new
      // transcript, so restoreScrollAnchor no-ops there and the fresh view stays at the bottom (correct)
      if (!_wasNear && _scrollAnchor && _scrollContent) {
        const v1 = views.get(msg.id);
        if (v1) restoreScrollAnchor(_scrollContent, v1, _scrollAnchor);
      }
    }
    renderBgTasks();
  }
  // A non-active session's view is left to sync lazily when it's next shown.
  // The session the user just created has ARRIVED: the provisional tab hands over its queued messages
  // and its draft to the real one, and this is where those messages actually reach the kernel — until
  // now there was no session to send them to.
  if (adoptsProvisional(existed, msg.name, pendingNewSession)) {
    adoptProvisional(msg.id);
  }
  schedulePrebuild(); // startup + new content: build the off-screen tabs in idle so they open instantly
}

function update(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) return;
  s.events = msg.events || s.events;
  s.status = msg.status || s.status;
  reconcileRewind(s);                    // pending-rewind overlay + the editable-bubble set, from the fresh payload
  reconcileOptimistic(s);                // re-assert (or retire) any in-flight optimistic sends on this push
  renderTabs();                          // status/chip change only — repaint, never re-order (the user 2026-06-27)
  if (msg.id === activeId) {
    appendActive();
    renderLedger(); // refresh the summary box (ages + any new items) as the active session works
  } else {
    const v = views.get(msg.id);
    if (v) v.stale = true; // re-render its current turn when it's next shown
    schedulePrebuild(); // rebuild the now-stale off-screen view in idle, before the user switches to it
  }
}

// DELTA-send (the user 2026-06-25): the kernel keeps the whole transcript resident in the browser but, once
// a tab is caught up, sends only the changed SUFFIX as {type:"chatTail", from, events}. The prefix [0, from)
// is unchanged (an append → from = old length; a tool output filling an earlier card → from = that card's
// index), so we truncate s.events to `from` and append the suffix, then re-render from exactly `from` (set
// v.rendered = from) so a deep fill repaints without rebuilding the whole transcript. A new connect / fork /
// behind-the-change client gets a full {type:"session"} instead (kernel decides), so we always have a base.
// Post one entry to the shell's error center (the rail's warning triangle). Same {romp:'notify'} bridge the
// feed's card-badge mirror uses; no-ops in the VS Code view, which has no shell frame around it.
function notifyShell(kind: string, text: string, sid?: string): void {
  try { window.parent?.postMessage({ romp: "notify", kind, text, sid: sid || "" }, "*"); } catch { /* no shell */ }
}

// Sessions we've asked the kernel to re-send in full after a delta gap. ONE ask per desync: the pusher runs
// every 0.5-3s and would otherwise re-ask on every rejected delta until the reply lands. Cleared in upsert(),
// so the next gap can ask again.
const awaitingFull = new Set<string>();
function requestFullSession(id: string): void {
  if (!id || awaitingFull.has(id)) return;
  awaitingFull.add(id);
  vscodeApi?.postMessage({ type: "needFull", id });
}

function chatTail(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) return;                                  // no base yet → ignore; a full session must arrive first
  // msg.from is a GLOBAL transcript index; the resident events are the tail [headFrom, …) → map to local.
  const from = (msg.from | 0) - (s.headFrom || 0);
  // The kernel's coordinate space ends at ITS OWN events — our injected optimistic tail is not in it.
  // Comparing `from` against the inflated length masked a genuine 1-event gap (the repair below never
  // fired, PR #107's desync class), and a delta starting exactly one past kernel truth landed BEYOND
  // the injected bubble, freezing it into the resident events as fake history the reconcile's strip
  // loop could never pop (the user 2026-08-09).
  let kernelLen = s.events.length;
  while (kernelLen > 0 && isOptimistic(s.events[kernelLen - 1])) kernelLen--;
  if (from > kernelLen) {
    // GAP: the delta starts PAST what we hold, so the events in between never reached us. Applying it would
    // fabricate a transcript that silently skips them. This used to just `return` and "wait for the next
    // full" — but no full was ever coming: the kernel's per-client bookkeeping (_send_chat's echat) advances
    // on SEND, not on ACK, so it goes on believing we're caught up and keeps sending deltas we keep dropping.
    // The tab then froze at this index — a stale "working" chip, no new messages, and every feed/timeline
    // deep-link into the missing range honest-failing "couldn't locate this in the transcript" — until the
    // socket happened to drop and a fresh connect re-sent the whole session (the user 2026-07-28, whose tab
    // went stale twice in one afternoon: locate-audit.jsonl recorded six pointer-not-rendered misses, then
    // pointer-exact on the SAME anchor the moment a kernel restart forced a reconnect).
    // So ASK for the full session — the one message that closes this desync class whatever opened it.
    requestFullSession(msg.id);
    return;
  }
  if (from < 0) return;                            // below the loaded head → our resident tail is still valid
  const wasLen = s.events.length;
  s.events.length = from;                          // drop the (now superseded) tail...
  for (const e of (msg.events || [])) s.events.push(e);   // ...and append the freshly-changed suffix
  reconcileRewind(s);                              // pending-rewind overlay + the editable-bubble set
  reconcileOptimistic(s);                          // re-assert (or retire) any in-flight optimistic sends
  // A delta that SHRINKS the tail (an event retired with nothing replacing it — cancelling the last queued
  // message is the everyday case) lands on `from === new length`, so lowering v.rendered to `from` leaves it
  // EQUAL to the length and syncView's no-op fast path skips the repaint — the retired turn stayed on screen
  // for good (the user 2026-07-24: a ✕'d queued message left its "1 queued message" element behind). Mark the
  // view stale so the window is rebuilt from the events that actually remain.
  const shrank = s.events.length < wasLen;
  if (typeof msg.total === "number") s.headTotal = msg.total;
  if (msg.status) s.status = msg.status;
  if ("ledger" in msg) ledgers.set(msg.id, msg.ledger ?? null);
  renderTabs();
  if (msg.id === activeId) {
    const v = views.get(msg.id);
    if (v) {
      v.rendered = Math.min(v.rendered, from);        // repaint from the exact changed point (catches a tool fill)
      if (shrank) v.stale = true;                     // …but a pure truncation needs the window rebuilt, see above
    }
    appendActive();
    renderLedger();
  } else {
    const v = views.get(msg.id);
    if (v) v.stale = true;
    schedulePrebuild(); // rebuild the now-stale off-screen view in idle, before the user switches to it
  }
}

// Older history streaming in from a loadOlder request (scroll-back past the loaded tail, the user 2026-06-25).
// The kernel replies with the previous chunk; PREPEND it to the resident tail (lowering headFrom) and re-
// anchor on the row the user was at, so the older content appears ABOVE without the view jumping.
const loadingOlder = new Set<string>();                 // sessions with a loadOlder in flight
const pendingOlderAnchor = new Map<string, string>();   // sid → the uuid to re-anchor on when the chunk lands
// sid → the on-screen y that uuid must come back to. PRESENT ⇒ the fetch was a scroll-back (requestOlder) and
// the arrival is POSITION PRESERVATION; ABSENT ⇒ it was a deep-link (fetchOlderForAnchor) and the arrival is a
// real jump that top-aligns + flashes. The two were indistinguishable before, so every scroll-back arrival
// jumped (the user 2026-08-02).
const pendingOlderKeepY = new Map<string, number>();
function chatHead(msg: any) {
  loadingOlder.delete(msg.id);
  hideLoadingPill();
  const forget = (sid: string) => { pendingOlderAnchor.delete(sid); pendingOlderKeepY.delete(sid); };
  const s = sessions.get(msg.id);
  if (!s) { forget(msg.id); return; }
  const before = msg.before | 0, from = msg.from | 0;
  if (before !== (s.headFrom ?? 0)) { forget(msg.id); return; }   // stale / overlapping → ignore
  const older = (msg.events || []) as ChatEvent[];
  if (older.length) s.events = older.concat(s.events);
  s.headFrom = from;
  const v = views.get(msg.id);
  if (msg.id !== activeId) { forget(msg.id); if (v) v.stale = true; return; }
  // re-anchor: reset the active view so it re-windows around the saved row (now further down s.events), and
  // put that row back where it was — the prepended older content sits above, off-screen, ready to scroll into.
  if (v) { v.rendered = 0; v.winStart = 0; v.winEnd = 0; v.avgTurnH = undefined; v.spacerCount = undefined; v.spacerCountBot = undefined; v.unitTotal = undefined; }
  const anchorUuid = pendingOlderAnchor.get(msg.id);
  const keepY = pendingOlderKeepY.get(msg.id);
  forget(msg.id);
  // A DEEP-LINK STILL WAITING TO LAND WINS (the user 2026-08-02). A scroll-back re-anchor is about where the
  // reader was; a pending deep-link is about where they asked to GO. Letting the arrival overwrite it sent
  // the click somewhere the user never named.
  if (anchorUuid && !pendingAnchor) {
    pendingAnchor = anchorUuid; pendingAnchorIntent = null; pendingAnchorT = null; pendingAnchorKind = null;
    pendingAnchorKeepY = keepY ?? null;
  }
  showActive();
}

// Fetch the next older history chunk re-anchored on `uuid` (a deep-link target past the resident tail), so
// chatHead lands on it when the chunk arrives — instead of "couldn't locate" (the user 2026-06-27). Same
// loadOlder request requestOlder uses, but it stashes the TARGET uuid rather than the current top row. False
// when there's nothing older to fetch (headFrom 0) or a fetch is already in flight.
function fetchOlderForAnchor(sid: string, uuid: string): boolean {
  const s = sessions.get(sid);
  if (!s || (s.headFrom ?? 0) <= 0 || loadingOlder.has(sid)) return false;
  pendingOlderAnchor.set(sid, uuid);
  pendingOlderKeepY.delete(sid);   // a DEEP-LINK: land it properly (top-align + flash), not offset-preserved
  loadingOlder.add(sid);
  showLoadingPill();
  vscodeApi?.postMessage({ type: "loadOlder", id: sid, before: s.headFrom });
  return true;
}

// Ask the kernel for the chunk of history just before the resident tail. Anchors on the row the reader is
// actually LOOKING AT — the first turn still visible at the viewport top, with its on-screen offset — so
// chatHead can put it back exactly there once the older chunk is prepended above it.
//
// It used to anchor on `v.el.querySelector(".turn[data-uuid]")`: the first turn in the DOM, which after a
// deep-link is a whole window-radius above what the reader is reading. Paired with chatHead landing it as a
// deep-link (top-align + flash), that produced the reported bug (the user 2026-08-02): click a card's
// distilled summary → it lands pointer-exact → landOn top-aligns it, which (when the summary sits within
// WINDOW_RADIUS of the resident head and older history is still on the server) trips this very fetch → the
// arrival yanks the reader off the summary and onto the head of the resident tail, an unrelated old Bash
// card. Clicking the summary a second time then "worked" because the chunk was resident by then. Same
// mechanism when the reader merely scrolls up after the click. Anchor on the reader's own row, restore it to
// its own offset, and the fetch becomes invisible again — which is all it was ever supposed to be.
function requestOlder(sid: string, v: View, content: HTMLElement): void {
  const s = sessions.get(sid);
  if (!s || (s.headFrom ?? 0) <= 0 || loadingOlder.has(sid)) return;
  const keep = captureScrollAnchor(content, v);
  const anchor = keep?.uuid
    || (v.el.querySelector(".turn[data-uuid]") as HTMLElement | null)?.dataset.uuid
    || (s.events[0] as { uuid?: string } | undefined)?.uuid;
  // keepY on EITHER branch: this arrival must never jump. With no capture (nothing visible at the viewport
  // top) 0 restores the fallback row to the top, which is where it already is.
  if (anchor) { pendingOlderAnchor.set(sid, anchor); pendingOlderKeepY.set(sid, keep?.y ?? 0); }
  loadingOlder.add(sid);
  showLoadingPill();
  vscodeApi?.postMessage({ type: "loadOlder", id: sid, before: s.headFrom });
}

function statusOnly(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) return;
  s.status = msg.status || s.status;
  renderTabs();                          // status-only push → repaint the chip; order is untouched
  if (msg.id === activeId) updateStatusline();
}

// The ✕'s own path: drop the tab now, THEN remember the close (see closingTabs). The order matters and
// regressed the whole mechanism once (the user 2026-08-02, closed tabs lingering as the spinning swirl):
// dismissSession used to open with closingTabs.delete(id), so set-then-dismiss erased the record the
// instant it was written — the suppression the optimistic close depends on never survived the click, the
// next push re-added the id, and with the session already dropped it rendered as the loader placeholder
// until the kernel caught up. (dismissSession is ALSO how a session dying on its own arrives — that one
// must not be recorded as a close of ours, which is why the record is written HERE, not in there.)
function closeTabLocally(id: string): void {
  // A provisional tab has no session to close — closing it means "never mind", so it cancels the spawn
  // the kernel may still be running, the way the old cue's ✕ did. A FAILED one has no spawn left either
  // (and the kernel never knew the id): its ✕ is a plain local discard — tab, draft, and all.
  if (isProvisionalId(id)) {
    if (id === provisionalId) cancelProvisional();
    else { failedProvisionals.delete(id); dismissSession(id); }
    return;
  }
  dismissSession(id);
  closingTabs.set(id, Date.now());
}

// Drop a session from the panel + reselect another tab, NOW (the user 2026-06-24): used both by the kernel's
// `closed` event AND optimistically the instant you Close tab / End session — otherwise the reselect waited on
// that round-trip while the tab bar already updated, leaving you on the CLOSED session's stale content.
//
// This deliberately does NOT touch closingTabs. Retiring the suppression belongs to its own events:
// ackClosingTabs (a tabOrder push without the id = the kernel's confirm; the backstop past CLOSE_ACK_MS =
// the loud failure) and an explicit focus (reopen). Retiring it here — including on the kernel's `closed`
// event — is both the regression above (the ✕ path runs through here microseconds after recording the
// close) and wrong under federation, where a `closed` ack can predate stale merged frames that still list
// the id: the moment nothing suppresses it, the strip re-draws the swirl placeholder.
function dismissSession(id: string): void {
  sessions.delete(id);
  liveAsks.delete(id);
  ledgers.delete(id);
  // ALL of the closed session's composer context goes with it — the draft, the reply-context citation
  // chip, and any pending edit pill (the user 2026-08-04). Deleting from the maps is not enough when the
  // closed session was ACTIVE: the shared chip strip above the composer still shows its chip until
  // someone repaints it, and that stale chip's ✕ targets the dead id (whose map entry is gone), so the
  // click early-returns and the chip can't even be dismissed — hence the repaint below.
  drafts.delete(id); composerCitations.delete(id); composerEdits.delete(id); composerFiles.delete(id); persistDrafts();
  const v = views.get(id);
  if (v) { v.el.remove(); views.delete(id); }
  const oi = order.indexOf(id); if (oi >= 0) order.splice(oi, 1);
  const mi = mru.indexOf(id); if (mi >= 0) mru.splice(mi, 1);
  renderTabs();                          // tab removed from `order` above → repaint without it
  if (activeId === id) {
    activeId = mru[0] || null; // MRU: return to the previously-active tab, not the positional neighbor
    const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
    if (ta) { ta.value = (activeId && drafts.get(activeId)) || ""; growComposer(ta); }
    renderComposerChips(activeId);   // the strip was showing the CLOSED session's chip — swap in the new active tab's (usually none)
    renderComposerFiles(activeId);   // same for its attachment thumbnails
    showActive();
  }
}

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

// (A banner used to drop across the top of this pane when the open tab's host went away. It covered the
// session tab strip — the thing you steer by — to report what the tabs already say themselves: the tab on
// an unreachable host dims, its "host:" token is struck, and the note lives on hover. A host dropping now
// flashes the rail's network glyph red three times instead (the user 2026-07-29, whose sessions the banner
// hid). An event gets a transient cue; the steady state stays on the surfaces already carrying it; no pixel
// of transcript is spent. hostDownNote is still the one place that note is worded — see host-prefix.ts.)

window.addEventListener("message", (e: MessageEvent) => {
  const m = e.data;
  if (!m) return;
  // the shell's palette: "Fork this session…" → the fork modal for the ACTIVE session, from the tip
  if (m.romp === "forkSession") {
    if (activeId && !isProvisionalId(activeId) && sessions.get(activeId)) showForkPrompt(activeId, "");
    return;
  }
  // the hive's ghost hex (relayed by the shell): open the same new-session picker the + tab does
  if (m.romp === "openPicker") { openPicker(); return; }
  if (m.type === "pipeState") { pipeBanner(!!m.up, Number(m.queued) || 0); return; }
  if (m.type === "session") upsert(m);
  else if (m.type === "globalRetryPaused") {
    globalRetryPaused = !!m.value;
    // limit-driven pause → the usage window's reset epoch (seconds); manual pause / unknown → null
    globalRetryResumeAt = typeof m.resumeAt === "number" ? m.resumeAt : null;
    globalRetryReason = typeof m.reason === "string" ? m.reason : "";   // "spend" → raise-your-cap text, no countdown
    for (const btn of Array.from(document.querySelectorAll(".apierror-stop"))) {
      btn.textContent = globalRetryPaused ? "Resume all auto-retries" : "Stop all auto-retries";
      (btn as HTMLElement).title = globalRetryPaused ? "resume auto-retrying globally" : "stop the auto-retry loop for all errors globally";
    }
    for (const cd of Array.from(document.querySelectorAll(".apierror-countdown"))) {
      cd.textContent = globalRetryPaused ? retryPausedText() : "retrying soon…";
    }
  }
  else if (m.type === "chatTail") chatTail(m);
  else if (m.type === "chatHead") chatHead(m);
  else if (m.type === "chatEpisode") chatEpisode(m);
  else if (m.type === "update") update(m);
  else if (m.type === "status") statusOnly(m);
  else if (m.type === "focus") {
    revealSelfPane();   // every focus is someone jumping HERE — on mobile, come forward (incl. from a remote kernel)
    closingTabs.delete(m.id);   // an explicit reveal outranks a pending close-suppression: closing a tab and
    //                             reopening it from the picker inside the ack window must show it at once
    if (revivePending && m.id === revivePending) clearReviveLoader();   // the revive landed — the loader's success event
    // `live` (the user 2026-07-08): land on the LIVE TAIL. A blocked card's picker/permission prompt IS the
    // live bottom of the chat, so its feed chip drops the user right on it. Stick the target view to bottom so
    // showActive scrolls there; and cover the ALREADY-ACTIVE case, where setActive early-returns (activeId ===
    // id, no anchor) and would otherwise leave a scrolled-up chat parked in history, not at the prompt.
    if (m.live) { const v = views.get(m.id); if (v) v.stick = true; }
    if (m.live && activeId === m.id) {
      const c = document.getElementById("content"); if (c) c.scrollTop = c.scrollHeight;
    } else {
      setActive(m.id, m.anchor, typeof m.anchorT === "number" ? m.anchorT : undefined, typeof m.anchorKind === "string" ? m.anchorKind : undefined);
    }
    // A feed card click that resolved to a live goal → seed the composer citation chip (the user 2026-07-01).
    if (m.cite && typeof m.cite.itemId === "string" && typeof m.cite.title === "string") setCitation(m.id, { itemId: m.cite.itemId, title: m.cite.title });
  }
  // A card was CLEARED → drop any composer citation chip pointing at it (the user 2026-07-01): the goal is
  // gone, so following up on it makes no sense. dropCitationsAll (Clear-all) drops every chip.
  else if (m.type === "dropCitation" && typeof m.itemId === "string") dropCitationByItem(m.itemId, Array.isArray(m.itemIds) ? m.itemIds.filter((x: unknown) => typeof x === "string") : undefined);
  else if (m.type === "dropCitationsAll") {
    if (composerCitations.size) { composerCitations.clear(); persistDrafts(); renderComposerChips(activeId); }
  }
  else if (m.type === "mcpResult") {
    if (m.error) warnToast("MCP " + (m.server || "server") + ": " + m.error);
    const body = document.querySelector("#mcp-panel .mcp-list") as HTMLElement | null;
    if (body && mcpPanelSid) loadMcpPanel(mcpPanelSid, body);   // refetch — never an optimistic row
  }
  else if (m.type === "nextTab") cycleTab(1);
  else if (m.type === "prevTab") cycleTab(-1);
  else if (m.type === "warn" && typeof m.text === "string" && m.text) {
    // A warn arriving while a create is in flight IS that create's verdict (a name the kernel won't take,
    // an unreadable parent, the SDK setup hint). It gets a dialog naming the reason and takes the
    // provisional tab down with it; a toast would slide past the one moment it needed to be read.
    if (provisionalId) failProvisional(m.text); else warnToast(m.text);
  }
  // `err` is the LOUD channel, deliberately distinct from `warn` (the user 2026-07-29): a warn toast fades
  // after 12s, which is right for "that name has a bad character" and wrong for "the message you just typed
  // was never sent." This one takes the confirm modal — it has to be dismissed — and hands the text back,
  // since the composer cleared on Enter and the kernel's record is the only place it survives.
  // …and it ALSO files an entry in the shell's error center, the bell in the bottom bar (the user
  // 2026-07-29). A modal is the interrupt; the bell is the durable record you can come back to — the same
  // split the card-badge mirror already makes. Dismissing the dialog must not erase the fact that a message
  // of yours never landed.
  else if (m.type === "err" && typeof m.text === "string" && m.text) {
    const copy = typeof m.copy === "string" ? m.copy : "";
    const title = typeof m.title === "string" && m.title ? m.title : "That action was not delivered";
    notifyShell("undelivered", copy ? title + ": " + copy : title, typeof m.sid === "string" ? m.sid : "");
    showConfirm(title, m.text,
                copy ? [{ label: "Copy my text", value: "copy" }, { label: "Dismiss", value: "ok" }]
                     : [{ label: "Dismiss", value: "ok" }],
                (v) => { if (v === "copy") navigator.clipboard?.writeText(copy); });
  }
  else if (m.type === "dirCompletions") onDirCompletions(m);        // the owning kernel's path completions
  else if (m.type === "createDirMissing" && m.name) onCreateDirMissing(m);   // create it, or edit the path
  // The AUTHORITATIVE answer to a ✕ on a queued bubble (the user 2026-07-20). ok:false = the message
  // had already left romp's queue (handed to the CLI — no recall exists): toast the kernel's 'too late'
  // and UNDO the optimistic composer restore if the draft is untouched — leaving the copy there invited
  // re-sending a message that is already being answered. ok:true just drops the stash (restore stands).
  else if (m.type === "cancelResult" && typeof m.id === "string") {
    const key = m.id + " " + (typeof m.md === "string" ? m.md : "");
    const stash = pendingCancelRestores.get(key);
    pendingCancelRestores.delete(key);
    if (!m.ok) {
      if (typeof m.text === "string" && m.text) warnToast(m.text);
      if (stash && m.id === activeId) {
        const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
        if (ta && ta.value === stash.after) {
          ta.value = stash.before;
          ta.dispatchEvent(new Event("input", { bubbles: true }));
        }
      }
      // …and put the BUBBLE back (the user 2026-07-24). The ✕ deletes it optimistically, but a miss means the
      // message is still going through — and the kernel's build never changed, so its next delta carries no
      // repaint and the optimistic delete would stand. That reads as "cancelled" while the session answers it
      // anyway, contradicting the toast we just raised. Repaint from the kernel's events, which still hold it.
      const rv = m.id === activeId && activeId ? views.get(activeId) : null;
      if (rv) { rv.stale = true; appendActive(); }
    }
  }
  // The identity palette changed (gear → Session colors): refresh the right-click menu's swatch set so a
  // menu opened after the switch offers the NEW palette (the kernel remaps + repaints sessions itself).
  else if (m.type === "palette" && Array.isArray(m.colors)) paletteColors = m.colors;
  else if (m.type === "sessionList") {
    // Whose list is this? federation stamps the source host; a local reply carries none. A reply for a
    // host the picker has since switched away from is dropped rather than painted over the current one
    // (the user 2026-07-29) — two kernels answer at their own speeds, so order is not a given.
    const from = typeof m.host === "string" ? m.host : "";
    // The LOCAL kernel's own machine name — adopted BEFORE the stale-list drop below, on purpose:
    // the name is this machine's identity, not list data, so it must not die with a reply whose
    // LIST is stale (picker already switched to a remote host — the drop's one job). The Host row
    // is built before this reply lands (its options rebuild on every open), so also relabel the
    // this-machine button in place — the row's first-ever open is the only one that shows the
    // "local" placeholder.
    if (typeof m.selfHost === "string" && m.selfHost && !from) {
      localSelfHost = m.selfHost;
      const lb = document.querySelector('#picker .picker-host .picker-be-opt[data-host=""]') as HTMLElement | null;
      if (lb) lb.textContent = localSelfHost;
    }
    if (from !== pickerListHost) return;
    if (typeof m.defaultDir === "string" && !from) kernelDefaultDir = m.defaultDir;   // the LOCAL default dir
    // …and whether that kernel can open a folder dialog at all. It arrives after the picker is already on
    // screen, so re-settle the button now rather than leaving it live until the next open.
    if (typeof m.nativeDialogs === "boolean" && !from) {
      kernelNativeDialogs = m.nativeDialogs;
      applyBrowseState(pickerHost());
    }
    // the selected host's billing choices ride its own list reply — this is what arms (or hides) the
    // picker's Billing row (the user 2026-08-08); an older kernel sends none and the row stays away
    pickerAuthAvail = (m.authAvail && typeof m.authAvail === "object") ? m.authAvail : null;
    syncPickerAuth();
    renderPicker(m.items || []);
  }
  else if (m.type === "browseResult" && typeof m.path === "string") {   // native Browse dialog returned a folder
    if (m.target === "gear") {                                          // the gear's "Default directory" Browse
      const gd = document.getElementById("rs-defaultdir") as HTMLInputElement | null;
      if (gd) { gd.value = m.path; gd.dispatchEvent(new Event("change")); }   // fire the gear's change → persist kernel-side
    } else {
      const di = document.getElementById("picker-dir") as HTMLInputElement | null;
      if (di) { di.value = m.path; di.focus(); }
    }
  }
  // toggle:true is the hotkey form (Cmd/Ctrl+Shift+O relayed by the shell): a second press closes an
  // open picker instead of re-opening it.
  else if (m.type === "openPicker") {
    if (m.toggle && pickerVisible()) closePicker();
    else openPicker(!!m.pick, m.prompt, !!m.allowNew);
  }
  // The shell's session jump switcher (Cmd/Ctrl+O) picked a session: an open tab activates like a
  // feed jump (the `focus` path above); one without a tab opens through the host, the exact message
  // a picker row sends.
  else if (m.type === "jumpSession" && typeof m.id === "string") {
    if (order.includes(m.id)) { revealSelfPane(); closingTabs.delete(m.id); setActive(m.id); }
    else if (vscodeApi) vscodeApi.postMessage({ type: "openSession", id: m.id });
  }
  // The host asks US to confirm (in-page, no native dialogs): ending a live
  // session on tab-close, and reviving a dead one on open.
  else if (m.type === "confirmClose" && m.id) {
    // × = End session, full stop (the user 2026-08-11): the old "Close tab" branch hid a RUNNING session
    // — no tab, no Fleet row, still judged and billed — a secret running session with no SDK-era use
    // case. Close-and-reopen is End + Revive, which keeps the whole history.
    const nm = String(m.name || "");
    showConfirm(`End “${nm}”?`,
      "The session shuts down. Its history stays on disk — revive it any time from the picker or timeline.",
      [{ label: "End session", value: "end", danger: true }, { label: "Cancel", value: "" }],
      (v) => {
        if (v !== "end") return;   // Cancel → nothing
        // End session = shut it down AND remove the tab (the user 2026-06-16: an explicitly-ended session
        // shouldn't linger as a struck-through read-only tab — that's only for sessions that die on their
        // own). closeTab durably forgets a kept read-only tab so the death event doesn't re-add it.
        vscodeApi?.postMessage({ type: "endSession", id: m.id });
        vscodeApi?.postMessage({ type: "closeTab", id: m.id });
        closeTabLocally(m.id);   // same optimistic drop as the in-page ✕ — this path used to sit and wait
      });
  }
  else if (m.type === "confirmRevive" && m.id) {
    revealSelfPane();   // the dead-session prompt is drawn in THIS pane — useless if the pane isn't showing
    const nm = String(m.name || "");
    showConfirm(`“${nm}” is closed — revive it?`,
      "Revive restarts the session and resumes its conversation. Read-only just shows the transcript.",
      [{ label: "Revive", value: "revive" }, { label: "View read-only", value: "ro" }, { label: "Cancel", value: "" }],
      (v) => {
        // acknowledge the click at once (the repo's loading rule): the romp loader goes up BEFORE the
        // seconds-long resume, and clears event-based on the kernel's focus/reviveFailed reply.
        if (v === "revive") { vscodeApi?.postMessage({ type: "reviveSession", id: m.id }); showReviveLoader(m.id, nm); }
        else if (v === "ro") vscodeApi?.postMessage({ type: "viewReadOnly", id: m.id });
      });
  }
  else if (m.type === "reviveFailed" && m.id) {
    // the kernel's loud revive failure → the loader morphs into the reason (never a silent no-op)
    showReviveError(String(m.name || m.id), String(m.text || "unknown error"));
  }
  else if (m.type === "focusComposer") { const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null; ta?.focus(); }
  else if (m.type === "glowTurns") applyGlow(Array.isArray(m.groups) ? m.groups : [], Array.isArray(m.mids) ? m.mids : []);
  else if (m.type === "askLive") setLiveAsk(m.id, m.ask ?? null);
  else if (m.type === "askLiveClear") clearLiveAsk(m.id);
  else if (m.type === "clipboardText") insertClipboardText(String(m.text ?? ""));
  else if (m.type === "ledger") setLedger(m.id, m.ledger ?? null);
  else if (m.type === "working") { workingSet = new Set(Array.isArray(m.names) ? m.names : []); refreshPostalDots(); }
  else if (m.type === "imgData" && typeof m.path === "string") onImgData(m.path, typeof m.url === "string" ? m.url : null);
  else if (m.type === "tabOrder") applyTabOrder(m.order, m.tabs);
  else if (m.type === "renamed" && m.id && typeof m.name === "string") {
    const s = sessions.get(m.id);
    if (s && s.name !== m.name) { s.name = m.name; renderTabs(); }
  }
  else if (m.type === "droppedPath" && typeof m.path === "string") {   // host-saved drop/paste/pick → a thumbnail, not path text (the user 2026-08-04)
    retirePendingShip(m.path);                                         // the in-flight chip this ack answers (no-op for pickFile, which never ships)
    addComposerFile(activeId, m.path);
  } else if (m.type === "dropSaveFailed" && typeof m.name === "string") {
    // the kernel could not SAVE the shipped bytes — clear the pending chip and say so loudly,
    // never leave dots pulsing over a file that is not coming (fail loudly, don't degrade silently)
    retirePendingShip(m.name);
    warnToast(m.name + " couldn't be saved on the kernel, so it was not attached — try again.");
  }
  // an EDITOR highlight (VS Code host, onDidChangeTextEditorSelection — the user 2026-07-13) seeds the
  // same quote chip a transcript highlight does, labeled + wrapped with its file:lines origin (m.src)
  else if (m.type === "editorSelection" && typeof m.text === "string" && m.text.trim() && activeId)
    seedEditorQuote(activeId, m.text, typeof m.src === "string" ? m.src : undefined);
  // the editor selection collapsed (deselect / click away) — drop the chip that highlight seeded
  else if (m.type === "editorSelectionCleared") clearEditorCitation(activeId);
  else if (m.type === "closed") dismissSession(m.id);   // a session died on its own (or the kernel confirms our close)
});

// Tick the working timer (the chip color-pulse is pure CSS) and keep the model/ctx
// meta fresh as status updates land.
setInterval(() => {
  const s = activeId ? sessions.get(activeId) : null;
  if (!s) return;
  if (s.status.state === "working" || s.status.state === "awaitingBg") {
    const timer = document.getElementById("work-timer");
    if (timer) timer.textContent = elapsedMs(s.status.sinceEpoch);
    else updateStatusline();
  }
  const meta = document.getElementById("spinner-meta");
  if (meta) syncMetaControls(meta, s.status);
  const bar = document.getElementById("ctx-bar");
  if (bar) setCtxBar(bar, s.status.ctx, s.status.state === "compacting", s.status.ctxColor);
}, 1000);

// the last message we delivered per session — so a Ctrl+C interrupt can put it back
// in the box, mirroring Claude Code (which restores the in-flight prompt on Esc).
const lastSent = new Map<string, string>();
let interruptFlashT: number | undefined;
function flashInterrupted(ta: HTMLTextAreaElement) {
  ta.classList.add("interrupted-flash");
  if (interruptFlashT) window.clearTimeout(interruptFlashT);
  interruptFlashT = window.setTimeout(() => ta.classList.remove("interrupted-flash"), 650);
}
// px height the user set by dragging #composer-resize; null = auto. It raises the auto-grow cap so a long
// message shows in full, and is cleared on send so the box snaps back to one line (the user 2026-07-07).
let composerManualH: number | null = null;
const COMPOSER_MIN_H = 38;                                  // ~one line + padding (matches the CSS min-height floor)
const composerMaxH = () => Math.max(120, Math.round(window.innerHeight * 0.6));   // never eat the whole chat
function growComposer(ta: HTMLTextAreaElement) {
  ta.style.height = "auto";
  const cap = composerManualH ?? 120;                      // dragged cap, else the default ~6-line cap
  ta.style.height = Math.min(ta.scrollHeight, cap) + "px";
}

// One slash command from the kernel's /commands (the Agent SDK's get_server_info): the name (no leading "/"),
// a one-line description, an optional argument hint, and any aliases.
interface SlashCmd { name: string; description?: string; argumentHint?: string; aliases?: string[]; }

// Composer: Enter sends the message to the active session as its next prompt,
// Shift+Enter inserts a newline; the box auto-grows a few lines.
function setupComposer() {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta) return;
  // Send the composer's text to the active session — the single path shared by ⏎ and the explicit send
  // button (the user 2026-06-17). Trims, remembers for a Ctrl+C restore, clears the box.
  const sendComposer = () => {
    const typed = ta.value.trim();
    if (!activeId) return;
    const attached = composerFiles.get(activeId) || [];
    if (!typed && !attached.length) return;
    // Attachment thumbnails ride the send as a trailing line of paths — quoted when they contain spaces,
    // the way a person would type them (the user 2026-08-04). Not for a picker answer or an edit: a
    // picker wants exactly the typed words, and an edit replaces a PAST message — the strip keeps its
    // files for the next normal send in both cases.
    const text = attached.length
      ? (typed ? typed + "\n" : "") + attached.map((p) => (/\s/.test(p) ? '"' + p + '"' : p)).join(" ")
      : typed;
    // A live picker with a free-text path is up → the composer IS its "add your own" field now (the user
    // 2026-07-09): route the typed text to the picker instead of sending a normal message. "custom" fills the
    // AskUserQuestion Type-something slot (single submits, multi adds a checked row + you Submit); "text"
    // answers a raw free-text prompt. A picker WITHOUT free text (permission Allow/Deny, plan review) returns
    // null here, so the box keeps its normal send — the controls just stay in view alongside it.
    const askRoute = typed ? composerAnswersAsk() : null;
    if (askRoute) {
      if (askRoute === "custom") addCustomLiveAsk(typed); else sendTextLiveAsk(typed);
      drafts.delete(activeId); draftStartedAt.delete(activeId); persistDrafts();
      ta.value = ""; composerManualH = null; ta.style.height = "";
      return;
    }
    // EDIT mode → a rewindSend: branch the conversation from just before the edited message. No
    // registerOptimistic (the edit lands MID-chat at the branch point, not at the tail) — the
    // pending-rewind overlay shows the outcome in place until the kernel's rewound payload arrives.
    const editing = composerEdits.get(activeId);
    if (editing) {
      if (!typed) return;   // an edit sends the typed words; attachments wait for the next normal send
      vscodeApi?.postMessage({ type: "rewindSend", id: activeId, uuid: editing.uuid, text: typed });
      pendingRewind.set(activeId, { uuid: editing.uuid, text: typed, ts: Date.now() });
      composerEdits.delete(activeId);
      renderComposerChips(activeId);
      const s = sessions.get(activeId);
      if (s) { reconcileRewind(s); appendActive(); }   // paint the overlay NOW (stale → window re-render)
      drafts.delete(activeId); draftStartedAt.delete(activeId); persistDrafts();
      ta.value = ""; composerManualH = null; ta.style.height = "";
      return;
    }
    const sid = activeId;   // the session this send (and any confirm below) was armed for
    const deliver = () => {
      if (activeId !== sid) return;   // a confirm outlived a tab switch — never send into the wrong session
      // A PROVISIONAL tab has no session behind it yet, so there is nothing to send to: hold the message
      // and flush it the instant the real one lands (adoptProvisional). The dashed optimistic bubble goes
      // up now, which is the honest reading — romp has your message, it has not been delivered — and it
      // carries over to the real tab rather than being redrawn there.
      // A session on an unreachable host cannot receive this: the kernel that owns it is the far end of a
      // link that is down. Refuse BEFORE the box is cleared, so the message stays exactly where you typed
      // it (the user 2026-07-30) — silently accepting it would clear the composer and deliver nothing.
      if (hostIsDown(sid)) {
        const host = String(sid).slice(0, String(sid).indexOf(":"));
        warnToast(host + " is disconnected, so this wasn't sent. It's still in the box — romp is "
          + "reconnecting, and you can send it then.");
        return;
      }
      if (isProvisionalId(sid)) {
        // a FAILED create's tab: there is no pending spawn to queue onto, and never a session to send
        // to — refuse loudly and leave the text exactly where it is (the box is the only copy)
        if (sid !== provisionalId) {
          warnToast("“" + (sessions.get(sid)?.name || "this session") + "” never started, so there's "
            + "nowhere to send this. It stays in the box — create the session again to use it.");
          return;
        }
        provisionalQueue.push(text);
        registerOptimistic(sid, text);
        if (attached.length) { composerFiles.delete(sid); if (sid === activeId) renderComposerFiles(sid); }
        drafts.delete(sid); draftStartedAt.delete(sid); persistDrafts();
        ta.value = ""; composerManualH = null; ta.style.height = "";
        return;
      }
      lastSent.set(activeId, text);   // remembered for a possible Ctrl+C restore
      // A pending citation chip → send as a FOLLOW-UP on that goal (the user 2026-07-01): askFollowUp wraps the
      // text with the goal's context + the romp-goal-id marker (kernel side), so the goal reopens (done→working,
      // unless cleared) and the chat renders the ↩ Follow-up header — the same path the Follow-up button uses,
      // just seeded by the click. A QUOTE chip (highlighted transcript text, the user 2026-07-13) has no goal:
      // it wraps client-side (quoteReplyBody) into a plain message. No chip → plain sendMessage.
      // `sid: activeId` is what ROUTES this to the owning kernel in a federated dashboard (the user 2026-07-29):
      // federation keys routing off `id`/`sid` only, and an `itemId` ("‹sid›:‹goal›") can't be one — its own
      // colon would read the session uuid as a host. Without the sid a follow-up on a REMOTE card went to the
      // LOCAL kernel, which owns no such session and dropped it into tmux by uuid — nothing sent, no error,
      // the card flashing to Working and back. The kernel keeps deriving its sid from itemId, so this is inert
      // locally; every other card op (askClear/cardNotify/showOnTimeline) already carries the sid the same way.
      const cites = composerCitations.get(activeId);
      const goalCite = cites?.find((c) => c.itemId);   // a goal chip rides alone (flavors never mix)
      const quoteCites = cites ? cites.filter((c) => c.quote) : [];
      if (vscodeApi) {
        if (goalCite?.itemId) vscodeApi.postMessage({ type: "askFollowUp", itemId: goalCite.itemId, text, sid: activeId });
        else if (quoteCites.length) vscodeApi.postMessage({ type: "sendMessage", id: activeId, text: quoteReplyBody(quoteCites, text) });
        else { vscodeApi.postMessage({ type: "sendMessage", id: activeId, text }); registerOptimistic(activeId, text); }
        // (a citation follow-up/quote has its own kernel-side echo path; the optimistic bubble covers the plain send)
      }
      if (cites) { composerCitations.delete(activeId); renderComposerChips(activeId); }   // consumed on send
      if (attached.length) { composerFiles.delete(sid); if (sid === activeId) renderComposerFiles(sid); }   // the strip emptied into this message
      drafts.delete(activeId); draftStartedAt.delete(activeId); persistDrafts();   // sent — no draft to restore on a later switch-back
      ta.value = "";
      composerManualH = null;   // a drag-expanded box snaps back to one line after a send (the user 2026-07-07)
      ta.style.height = "";
      // The box is empty again, so a live picker re-takes it: send your pre-question draft, then just type the
      // answer (the user 2026-07-16). Repaints the "answering" tint that draftPredatesAsk had suppressed.
      setComposerAskMode();
    };
    // A typed /clear ends the conversation, and the kernel's episode boundary then settles the
    // session's open cards with it. The composer sees the command BEFORE it runs — the one
    // interception point — so open cards put an explicit confirm between Enter and the drop
    // (the user 2026-07-27). Cancel keeps the text in the box; no open cards → no modal.
    // `/mcp` NEVER reaches the CLI (the user 2026-08-05): its own panel is an interactive TUI an
    // SDK session can't render, so the CLI answers "use a terminal". romp shows the same facts from
    // the SDK's control requests instead — status, enable/disable, reconnect. Intercepted here, the
    // one point that sees a command before it runs (the /clear precedent below).
    if (/^\/mcp\s*$/.test(text)) {
      ta.value = ""; composerManualH = null; ta.style.height = "";
      drafts.delete(sid); persistDrafts();
      openMcpPanel(sid);
      return;
    }
    const dropDetail = isClearCmd(text) ? clearConfirmDetail(openTopTitles(ledgers.get(sid)?.tree)) : null;
    if (dropDetail) {
      showConfirm("Clear this conversation?", dropDetail,
        [{ label: "Cancel", value: "cancel" }, { label: "Clear anyway", value: "clear", danger: true }],
        (v) => { if (v === "clear") deliver(); });
      return;
    }
    deliver();
  };
  // an explicit send button on the right of the box (touch devices have no easy ⏎; desktop gets a click
  // affordance too). mousedown, not click, so the textarea keeps focus and a follow-up keeps typing.
  // On a phone, though, keeping focus keeps the on-screen keyboard up and the composer pinned above it;
  // blur instead so the keyboard collapses and the box drops back to the bottom (the user 2026-07-22).
  const sendBtn = document.getElementById("composer-send") as HTMLButtonElement | null;
  sendBtn?.addEventListener("mousedown", (e) => { e.preventDefault(); sendComposer(); if (isCoarsePointer()) ta.blur(); else ta.focus(); });

  // ── drag-to-resize the message box (the user 2026-07-07) ── the #composer-resize handle straddles the
  // top-edge divider; dragging it UP grows the composer (to see a long message in full), DOWN shrinks it.
  // Sets composerManualH (the auto-grow cap) and the height directly for immediate feedback; a send clears
  // it (above) so the box snaps back to one line. Pointer capture → the drag survives leaving the handle.
  const grip = document.getElementById("composer-resize");
  if (grip) {
    let startY = 0, startH = 0, dragging = false;
    const onMove = (e: PointerEvent) => {
      if (!dragging) return;
      const h = Math.max(COMPOSER_MIN_H, Math.min(composerMaxH(), startH + (startY - e.clientY)));
      composerManualH = h;
      ta.style.height = h + "px";
    };
    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      grip.classList.remove("dragging");
      document.body.classList.remove("composer-resizing");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      dragging = true;
      startY = e.clientY;
      startH = ta.getBoundingClientRect().height;
      grip.classList.add("dragging");
      document.body.classList.add("composer-resizing");
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    });
    // a double-click on the handle resets to auto (one line) — a quick escape hatch without sending
    grip.addEventListener("dblclick", () => { composerManualH = null; growComposer(ta); });
  }

  // ── slash-command autocomplete (the user 2026-06-29) ── a "/" at the START of the box opens a filterable,
  // arrow-navigable menu of THIS session's slash commands (name + description + arg hint), sourced from the
  // kernel's /commands (the Agent SDK's get_server_info, per-cwd — works for tmux + SDK alike). Enter/Tab/click
  // FILLS "/name " so you add arguments then send yourself; Esc closes. The list is fetched per active session
  // and cached; while the kernel warms its (slow) probe the menu shows the romp loader. Modeled on the VS Code
  // client's command palette + the terminal UI.
  let slashCmds: SlashCmd[] = [];
  let slashSid: string | null = null;   // which session's list we hold; null = never loaded (distinct from the "" cwd-fallback sid)
  let slashWarming = false;
  let slashPoll: number | undefined;
  let pop: HTMLElement | null = null;
  let items: SlashCmd[] = [];
  let sel = 0;
  // Escape DISMISSES the menu until you clear the "/" and start over (the user 2026-06-29): once you've Esc'd
  // out, typing more of the same "/token" must NOT re-pop it — only deleting back past the "/" (slashQuery →
  // null) re-arms it. Latched here; set on Esc, cleared the moment the "/token" context is gone.
  let slashDismissed = false;
  // The list is strictly one line per command; → on the selected row opens its FULL name + arg hint +
  // description, wrapped, and ←/Esc return to the list (the user 2026-08-13, after /code-review's long arg
  // hint squeezed a wrapping description into a one-letter-wide column). A mode, not a per-row bit: ↑/↓
  // while expanded browse the full texts.
  let slashExpanded = false;
  const loadCmds = (sid: string, then?: () => void) => {
    fetch(kernelUrl("/commands?sid=" + encodeURIComponent(sid)), { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        slashCmds = Array.isArray(d.commands) ? d.commands : [];
        slashWarming = !!d.warming; slashSid = sid;
        if (slashWarming) { clearTimeout(slashPoll); slashPoll = window.setTimeout(() => loadCmds(sid, then), 1500); }
        then && then();
      })
      .catch(() => { slashCmds = []; slashWarming = false; slashSid = sid; then && then(); });
  };
  const slashQuery = (): string | null => (/^\/\S*$/.test(ta.value) ? ta.value.slice(1) : null);   // active only while the box is one "/token"
  const filterCmds = (q: string): SlashCmd[] => {
    const ql = q.toLowerCase();
    return slashCmds
      .map((c) => {
        let best = -1;
        for (const n of [c.name, ...(c.aliases || [])]) {
          const i = n.toLowerCase().indexOf(ql);
          best = Math.max(best, ql === "" ? 0 : i === 0 ? 2 : i > 0 ? 1 : -1);   // prefix > substring > miss
        }
        return { c, best };
      })
      .filter((x) => x.best >= 0)
      .sort((a, b) => b.best - a.best || a.c.name.localeCompare(b.c.name))
      .map((x) => x.c).slice(0, 60);
  };
  // The expanded view lists a multi-group arg hint one bracketed group per line ("[--fix] [--comment]" →
  // two lines) — but ONLY when the whole hint is bracketed groups; any free-form hint stays one line
  // rather than being split by a guess.
  const argLines = (hint: string): string[] => {
    const groups = hint.match(/\[[^\]]*\]/g) || [];
    const residue = hint.replace(/\[[^\]]*\]/g, "").trim();
    return groups.length >= 2 && !residue ? groups : [hint.trim()];
  };
  const closeSlash = () => { if (pop) { pop.remove(); pop = null; } if (slashPoll) { clearTimeout(slashPoll); slashPoll = undefined; } slashExpanded = false; };
  const positionSlash = () => {
    if (!pop) return;
    const r = ta.getBoundingClientRect();
    pop.style.left = r.left + "px";
    pop.style.width = Math.max(r.width, 300) + "px";
    pop.style.bottom = (window.innerHeight - r.top + 6) + "px";   // sit just ABOVE the composer
  };
  const pickSlash = (c: SlashCmd) => {
    ta.value = "/" + c.name + " ";                               // FILL (not send) — add args, then ⏎
    closeSlash();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    growComposer(ta);
    if (activeId) { drafts.set(activeId, ta.value); persistDrafts(); }
  };
  const paintSlash = () => {
    if (!pop) return;
    pop.replaceChildren();
    if (slashWarming && !slashCmds.length) {
      const l = document.createElement("div"); l.className = "slash-loading";
      const s = document.createElement("span"); s.className = "slash-spin"; s.setAttribute("aria-hidden", "true");
      l.append(s, document.createTextNode("loading commands…"));
      pop.appendChild(l); positionSlash(); return;
    }
    if (!items.length) {
      const e = document.createElement("div"); e.className = "slash-empty";
      e.textContent = slashCmds.length ? "no matching commands" : "no commands available";
      pop.appendChild(e); positionSlash(); return;
    }
    items.forEach((c, i) => {
      const expanded = i === sel && slashExpanded;
      const row = document.createElement("div");
      row.className = "slash-row" + (i === sel ? " sel" : "") + (expanded ? " expanded" : "");
      if (expanded) {
        // full text, stacked for reading: /name (+ the ← key hint), then each bracketed arg group on its
        // own line, then the whole description (the user 2026-08-13, round 2: one wrapped soup was legal
        // but not readable)
        const head = document.createElement("div"); head.className = "slash-x-head";
        const nm = document.createElement("span"); nm.className = "slash-name"; nm.textContent = "/" + c.name;
        const k = document.createElement("span"); k.className = "slash-key-hint"; k.textContent = "← all commands";
        head.append(nm, k);
        row.appendChild(head);
        if (c.argumentHint) {
          const args = document.createElement("div"); args.className = "slash-x-args";
          for (const g of argLines(c.argumentHint)) {
            const a = document.createElement("div"); a.className = "slash-arg"; a.textContent = g; args.appendChild(a);
          }
          row.appendChild(args);
        }
        if (c.description) { const ds = document.createElement("div"); ds.className = "slash-desc"; ds.textContent = c.description; row.appendChild(ds); }
      } else {
        const nm = document.createElement("span"); nm.className = "slash-name"; nm.textContent = "/" + c.name;
        if (c.argumentHint) { const a = document.createElement("span"); a.className = "slash-arg"; a.textContent = " " + c.argumentHint; nm.appendChild(a); }
        const ds = document.createElement("span"); ds.className = "slash-desc"; ds.textContent = c.description || "";
        row.append(nm, ds);
        // the → key hint rides the SELECTED row itself — a popup-bottom footer sat below the fold of the
        // scrolling list (the user 2026-08-13, round 2); the selected row is always scrolled into view
        if (i === sel) { const k = document.createElement("span"); k.className = "slash-key-hint"; k.textContent = "→ expand"; row.appendChild(k); }
      }
      row.addEventListener("mousedown", (ev) => { ev.preventDefault(); pickSlash(c); });   // mousedown keeps focus
      // hover-select is frozen while expanded: the tall row re-flows heights under the cursor, and a repaint
      // per crossed row would flap the expansion around; ↑/↓ still browse
      row.addEventListener("mousemove", () => { if (!slashExpanded && sel !== i) { sel = i; paintSlash(); } });
      pop!.appendChild(row);
    });
    positionSlash();
    (pop.querySelector(".slash-row.sel") as HTMLElement | null)?.scrollIntoView({ block: "nearest" });
  };
  const updateSlash = () => {
    const q = slashQuery();
    if (q === null) { slashDismissed = false; closeSlash(); return; }   // "/" gone → re-arm for the next one
    if (slashDismissed) return;   // Esc'd out: stay closed until the "/" is cleared (q===null above re-arms)
    const sid = activeId || "";
    if (slashSid !== sid) loadCmds(sid, updateSlash);   // (re)load for the active session (""→ kernel cwd fallback)
    items = filterCmds(q);
    if (sel >= items.length) sel = 0;
    if (!pop) { pop = document.createElement("div"); pop.className = "slash-pop"; pop.id = "slash-pop"; document.body.appendChild(pop); }
    paintSlash();
  };
  // arrow/enter/tab/esc while the menu is OPEN; returns true when it consumed the key (so the composer's own
  // Enter-to-send / Esc-to-tab handlers below don't also fire).
  const slashKey = (e: KeyboardEvent): boolean => {
    if (!pop) return false;
    if (e.key === "ArrowDown") { e.preventDefault(); if (items.length) { sel = (sel + 1) % items.length; paintSlash(); } return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); if (items.length) { sel = (sel - 1 + items.length) % items.length; paintSlash(); } return true; }
    if ((e.key === "Enter" || e.key === "Tab") && items.length) { e.preventDefault(); pickSlash(items[sel]); return true; }
    // → expands the selected row — but only with the caret at the END of the query, where the key has no
    // text left to cross; anywhere else it stays an ordinary caret move. ← is consumed only while expanded.
    if (e.key === "ArrowRight" && items.length && !slashExpanded
        && ta.selectionStart === ta.value.length && ta.selectionEnd === ta.value.length) {
      e.preventDefault(); slashExpanded = true; paintSlash(); return true;
    }
    if (e.key === "ArrowLeft" && slashExpanded) { e.preventDefault(); slashExpanded = false; paintSlash(); return true; }
    if (e.key === "Escape") {
      // Esc peels one layer: the full text first, then the menu (which stays dismissed until the "/" is cleared)
      if (slashExpanded) { e.preventDefault(); slashExpanded = false; paintSlash(); return true; }
      e.preventDefault(); slashDismissed = true; closeSlash(); return true;
    }
    return false;
  };
  ta.addEventListener("focus", () => { if (slashSid !== (activeId || "")) loadCmds(activeId || ""); });   // pre-warm the cache before "/"
  ta.addEventListener("blur", () => window.setTimeout(closeSlash, 120));   // close when leaving (a row's mousedown keeps focus, so it fires only on a real leave)
  window.addEventListener("resize", positionSlash);

  ta.addEventListener("keydown", (e) => {
    if (slashKey(e)) return;   // the slash menu owns ↑/↓/⏎/Tab/Esc while it's open
    // Backspace at the very START of the box deletes the citation chip "like a character" (the user
    // 2026-07-01) — the chip sits just before the caret, so this is the natural way to remove it by keyboard.
    if (e.key === "Backspace" && !e.metaKey && !e.ctrlKey && ta.selectionStart === 0 && ta.selectionEnd === 0
        && activeId && composerCitations.has(activeId)) {
      e.preventDefault();
      removeCitation(activeId);
      return;
    }
    // Ctrl+C = terminal-style interrupt of the active session (Control, not Cmd — on
    // macOS copy is Cmd+C, so this never collides with copy). The host sends Esc to
    // the pane; here we mirror Claude Code's UI: flash a cue, and drop the just-sent
    // prompt back into the (empty) box so you can edit and resend.
    if (e.ctrlKey && !e.metaKey && (e.key === "c" || e.key === "C")) {
      e.preventDefault();
      if (!activeId || !vscodeApi) return;
      vscodeApi.postMessage({ type: "interrupt", id: activeId });
      const restore = lastSent.get(activeId);
      if (restore && !ta.value.trim()) { ta.value = restore; growComposer(ta); }
      lastSent.delete(activeId);
      flashInterrupted(ta);
      return;
    }
    if (e.key === "Escape") {
      // An active EDIT chip cancels first (back to normal sending); otherwise Escape leaves the chat box
      // for "tab mode" — focus the active tab so ←/→ switch sessions (the user 2026-06-25). Enter on a
      // tab drops back in (onTabKey). Any draft text stays in the box, untouched.
      e.preventDefault();
      if (activeId && composerEdits.has(activeId)) { cancelComposerEdit(activeId); return; }
      focusActiveTab();
      return;
    }
    // On a phone (coarse pointer) Enter is a NEWLINE, not send: mobile keyboards often can't produce
    // Shift+Enter, and the software return key should just return. Mobile sends with the explicit Send
    // button only (the user 2026-07-15). Desktop keeps ⏎ send / ⇧⏎ newline. The `!isCoarsePointer()` guard
    // lets Enter fall through to the textarea's native newline on touch.
    if (e.key === "Enter" && !e.shiftKey && !isCoarsePointer()) {
      e.preventDefault();
      sendComposer();
      focusActiveTab();   // jump focus to the tab bar after sending (the user 2026-06-25) so ←/→ switch
                          // sessions right away — the composer (a textarea) would otherwise keep the arrows
                          // for its caret. (The explicit send BUTTON keeps composer focus for continued typing.)
    }
  });
  ta.addEventListener("input", () => {
    growComposer(ta);
    updateSlash();   // open/refresh/close the slash-command menu as the leading "/token" changes
    // keep the per-tab draft (and its persisted copy) current as you type, so a reload restores it
    if (activeId) {
      const had = draftStartedAt.has(activeId);
      // stamp the moment THIS draft began (empty → non-empty); emptying the box ends it, so the next
      // keystroke starts a fresh one — that's what decides answer-vs-message against a live picker
      if (ta.value) { if (!had) draftStartedAt.set(activeId, Date.now()); drafts.set(activeId, ta.value); }
      else { draftStartedAt.delete(activeId); drafts.delete(activeId); }
      persistDrafts();
      // going empty↔non-empty can flip which way ⏎ goes → repaint the box's own cue (the "answering" tint)
      if (had !== draftStartedAt.has(activeId)) setComposerAskMode();
    }
  });

  // Drag a file onto the box → insert its PATH at the cursor. NOTE: VS Code's
  // workbench drop overlay captures plain external file drags over any editor
  // group ("drop to open", which is why a bare drop opened the PNG) before the
  // webview sees them — hold SHIFT while dropping to suppress the overlay and
  // hand the drop here. Pasting (below) is overlay-free and covers the same
  // need. Best path source first — but ONLY for a session this machine owns:
  // File.path (Electron, when exposed), then text/uri-list file:// entries
  // (explorer drags), else the bytes go to the owning kernel, which saves them
  // and posts the saved path back ("droppedPath") — sandboxed webviews expose
  // NO filesystem path for OS drags, only content. A REMOTE host's session
  // (hostOf) never takes the path branches: a path on this machine means
  // nothing on that kernel's disk — the user 2026-08-11 dragged a laptop
  // screenshot into a server session and the agent there got a path it could
  // not open. Its drops/pastes ship the BYTES instead, the same dropFile route
  // federation already carries for the phone, and the saved path comes back
  // valid on the machine the agent actually reads.
  ta.addEventListener("dragover", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    ta.classList.add("drop-target");
  });
  ta.addEventListener("dragleave", () => ta.classList.remove("drop-target"));
  ta.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation();
    ta.classList.remove("drop-target");
    const dt = e.dataTransfer;
    if (!dt) return;
    const remote = hostOf(activeId || "");
    const uris = (dt.getData("text/uri-list") || "").split(/\r?\n/).filter((u) => u && !u.startsWith("#"));
    const fromUri = (u: string) => addComposerFile(activeId, decodeURIComponent(u.replace(/^file:\/\//, "")));
    const files = Array.from(dt.files || []);
    if (!files.length) {
      // a path-only drag (no File objects) can't be shipped — a browser can't read file:// bytes.
      // For a remote session that is a dead end, and it must be said, not silently mis-attached.
      for (const u of uris) if (u.startsWith("file://")) {
        if (remote) warnToast("That drag carried only this machine's path, which " + remote
          + " can't read — drop the file itself (or paste it) and the bytes will be shipped over.");
        else fromUri(u);
      }
      return;
    }
    files.forEach((f, i) => {
      if (!remote) {
        const p = (f as any).path as string | undefined;
        if (p) { addComposerFile(activeId, p); return; }
        if (uris[i] && uris[i].startsWith("file://")) { fromUri(uris[i]); return; }
      }
      shipFileToHost(f);
    });
  });

  // Cmd+V a copied file (Finder "Copy") or a clipboard screenshot → insert its
  // path, same pipeline as drops (including the remote rule: a local path only
  // for a locally-owned session). Plain text pastes keep the default behavior —
  // EXCEPT a paste that IS a local file's path (pastedFilePath: the whole paste
  // one line, absolute or ~, a kind /file serves). The user (2026-08-11) pasted
  // a screenshot's PATH into a remote session's box: the text rode the prompt
  // to a machine where that path doesn't exist, while dragging the same file
  // worked. A path-shaped paste is verified against the PAGE's own kernel
  // (/file — the machine the paste came from, auth-gated, existence-checked;
  // no sid, so it never routes to some other kernel's disk) and converted to
  // the same visible attachment chip a drop produces: the zero-copy path for a
  // locally-owned session, shipped bytes for a remote one. A miss puts the
  // EXACT text back at the cursor, so a path that isn't a local file pastes as
  // plain text exactly like today. Web dashboard only (canPreview): the
  // VS Code webview can't reach /file, and its Electron drops carry File.path.
  ta.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) {
      e.preventDefault();
      files.forEach((f) => {
        const p = (f as any).path as string | undefined;
        if (p && !hostOf(activeId || "")) addComposerFile(activeId, p);
        else shipFileToHost(f);
      });
      return;
    }
    const raw = e.clipboardData?.getData("text/plain") || "";
    const pasted = pastedFilePath(raw);
    if (!pasted || !canPreview()) return;              // ordinary text → default paste
    e.preventDefault();
    const sid = activeId;
    const selS = ta.selectionStart, selE = ta.selectionEnd;
    const putBack = () => {                            // miss → the default outcome, a beat late
      if (activeId === sid && document.contains(ta)) {
        ta.setRangeText(raw, selS, selE, "end");
        ta.dispatchEvent(new Event("input", { bubbles: true }));   // draft/grow/slash stay in sync
      } else if (sid) {                                // tab switched mid-verify → land in that draft
        drafts.set(sid, (drafts.get(sid) || "") + raw);
        persistDrafts();
      }
    };
    const remote = hostOf(sid || "");
    fetch(fileUrl(pasted.path), { method: remote ? "GET" : "HEAD" }).then(async (r) => {
      if (r.status === 413) {                          // refused LOUDLY, like every oversize arrival
        warnToast((pasted.path.split("/").pop() || "That file") + " is too large to attach from a "
          + "pasted path — it was pasted as text instead.");
        return putBack();
      }
      if (!r.ok) return putBack();
      if (!remote) { addComposerFile(sid, pasted.path); return; }  // zero-copy, like a local drop
      const blob = await r.blob();                     // ship the BYTES — the remote can't read our path
      shipFileToHost(new File([blob], pasted.path.split("/").pop() || "pasted", { type: blob.type }), sid);
    }).catch(putBack);
  });
  wirePasteFallback(ta); // belt-and-braces: native paste disarms it, so no double-insert

  // 📎 opens a file picker on the machine whose SCREEN you are looking at, routed
  // by host the same way Browse… splits:
  //
  //   • VS Code webview → the host extension's native open dialog (pickFile): the
  //     editor IS the local machine, so the dialog is on the right screen and the
  //     picked path comes back as droppedPath → an attachment thumbnail (the user
  //     2026-08-04; it used to insert the raw path at the cursor).
  //   • Web dashboard (http/https) → the BROWSER's own picker (the hidden
  //     <input type=file> below), and the chosen files' bytes ship to the kernel
  //     (shipFileToHost → dropFile → droppedPath), the flow drag/paste already
  //     rides. The old behavior posted pickFile to the kernel, whose native dialog
  //     opens on the KERNEL's machine — the wrong screen entirely from a remote
  //     browser, and on a headless kernel nothing but a warning.
  //
  // TOUCH devices keep the phone photo-picker UX (accept=image/*, the user
  // 2026-06-17: a screenshot reaches the session with no AirDrop/path gymnastics);
  // a desktop browser gets an unscoped, multi-select picker — attributes are set
  // per open, at the moment the pointer type is known.
  const attach = document.getElementById("composer-attach") as HTMLButtonElement | null;
  const isTouch = isCoarsePointer;
  const isWebPage = location.protocol === "http:" || location.protocol === "https:";
  const filePicker = document.createElement("input");
  filePicker.type = "file";
  filePicker.style.display = "none";
  filePicker.addEventListener("change", () => {
    Array.from(filePicker.files || []).forEach((f) => shipFileToHost(f));
    filePicker.value = ""; // let the same file be picked again
  });
  document.body.appendChild(filePicker);
  // web (touch or desktop): open the browser's picker — must fire from a real click gesture (iOS)
  attach?.addEventListener("click", (e) => {
    if (!isTouch() && !isWebPage) return;   // VS Code desktop → the host-dialog path (mousedown below)
    e.preventDefault();
    if (isTouch()) { filePicker.accept = "image/*"; filePicker.multiple = false; }
    else { filePicker.removeAttribute("accept"); filePicker.multiple = true; }
    filePicker.click();
  });
  // VS Code desktop: native host dialog; mousedown keeps the textarea focused for cursor-position insert
  attach?.addEventListener("mousedown", (e) => {
    if (isTouch() || isWebPage) return;
    e.preventDefault();
    vscodeApi?.postMessage({ type: "pickFile" });
  });
}

// No filesystem path available for a picked/dropped/pasted file → ship the bytes
// to the kernel that OWNS the active session, which saves them under its state
// dir's drops/ and posts back {type:"droppedPath", path} — which lands as an
// attachment thumbnail. dropFile carries the session id so federation routes it
// (routeOutbound's SCALAR_ID): the saved path rides the prompt and is read by the
// agent on THAT machine, so bytes saved on any other kernel would hand the agent
// a path that does not exist there.
const SHIP_MAX_BYTES = 50 * 1024 * 1024;   // payload ceiling for shipped attachment bytes
function shipFileToHost(f: File, sidAt: string | null = activeId) {
  if (f.size > SHIP_MAX_BYTES) {
    // an oversize file must be REFUSED VISIBLY, never dropped silently — name the
    // file, its size and the cap, on the same loud surface a failed federation
    // delivery uses.
    warnToast((f.name || "This file") + " is " + (f.size / (1024 * 1024)).toFixed(1)
      + " MB — attachments over 50 MB can't be shipped, so it was not attached.");
    return;
  }
  // The pending chip goes up NOW, before the encode even starts — on a phone the encode + fragmented
  // WS send + kernel round trip is seconds of otherwise-blank time that read as a dead click (the
  // user 2026-08-11). Retired by the droppedPath ack / dropSaveFailed nack (see retirePendingShip).
  const name = f.name || "pasted.png";
  const sid = sidAt;   // captured at CALL (= ship) time via the default param — a tab switch
  addPendingShip(sid, name);   // mid-encode (or mid-verify, for a pasted path) must not reroute
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = String(reader.result || "").split(",")[1] || "";
    if (!b64 || !vscodeApi) { retirePendingShip(name); return; }
    const msg: { type: string; name: string; b64: string; id?: string } =
      { type: "dropFile", name, b64 };
    if (sid) msg.id = sid;   // the owning session → the owning kernel
    vscodeApi.postMessage(msg);
  };
  reader.onerror = () => retirePendingShip(name);   // an unreadable file must not leave a stuck chip
  reader.readAsDataURL(f);
}

// ---- settings: the gear + modal live on the TIMELINE now (the user 2026-06-14). The chat just
// CONSUMES the shared 'romp:settings' (compact mode) — applying a change made there, in a same-origin
// tab, live via the storage event; and reading it at startup. ----
function setupSettings(): void {
  // renderTabs too: the tab strip reads settings (the context gauge toggle) but rerenderAll only
  // rebuilds the transcript views, so without it a gear change waited for the next kernel push.
  onExternalSettingsChange((s) => { settings = s; renderTabs(); rerenderAll(); });
}

setupComposer();
setupSettings();
// Tab-bar clicks are DELEGATED to the stable #tabs container (installed once), not hung on the per-tab nodes
// that renderTabs() rebuilds on every push — so selecting a tab or clicking its ✕ (Close/End session) always
// lands, even mid-rebuild. Each tab/✕ carries its data-act + data-id (see renderTabs, ./actions).
// Background-task rows toggle open/closed — delegated to the stable #bg-tasks container (installed once),
// not the per-task rows that renderBgTasks() rebuilds on every push, so the click always lands.
(() => {
  const host = document.getElementById("bg-tasks");
  if (!host) return;
  delegate(host, {
    "bg-fold": (el) => {   // the count header → show/hide the task list
      const id = el.dataset.id; if (!id) return;
      if (bgFoldOpen.has(id)) bgFoldOpen.delete(id); else bgFoldOpen.add(id);
      renderBgTasks();
    },
    "bg-toggle": (el) => {   // a task row → show/hide its command + output
      const id = el.dataset.id; if (!id) return;
      if (bgExpanded.has(id)) bgExpanded.delete(id); else bgExpanded.add(id);
      renderBgTasks();
    },
    "bg-stop": (el) => {   // stop this ONE task; the row disappears on its terminal lifecycle event
      const id = el.dataset.id; if (!id || !activeId) return;
      const btn = el as HTMLButtonElement;
      btn.disabled = true; btn.textContent = "Stopping…";   // immediate acknowledgement, before the round-trip
      vscodeApi?.postMessage({ type: "stopTask", id: activeId, taskId: id });
    },
  });
})();
(() => {
  // ONE openFolder delegate for the WHOLE chat (the user 2026-06-27): installed on document.body so EVERY place
  // that shows a folder — the statusline 📁, the System-context "Directory" row, anywhere asFolderLink is
  // applied — opens that folder on click. Body is stable across every per-push rebuild, so a click is never
  // dropped mid-press. (Only elements carrying data-act="openFolder" are matched; nothing else is affected.)
  // `id` (data-id, the session's own possibly host-prefixed id) rides along opaquely — see asFolderLink.
  delegate(document.body, {
    openFolder: (el) => {
      const cwd = el.dataset.cwd; if (!cwd || !vscodeApi) return;
      const id = el.dataset.id;
      vscodeApi.postMessage(id ? { type: "openFolder", cwd, id } : { type: "openFolder", cwd });
    },
    // "Stop retrying" on the live api_retry element (the user 2026-07-24). The CLI owns the backoff and the
    // SDK exposes no handle on it, so the honest stop is the SAME interrupt the stop button and Ctrl+C send:
    // it cuts the stalled turn and marks the thread retry-suppressed, so romp's auto-retry won't relapse.
    // Delegated (not a per-render listener) because the transcript tail rebuilds on every push. Acknowledge
    // at once — disable + relabel — so it never reads as unresponsive while the interrupt is in flight; the
    // element vanishes on the next push when the storm ends, so no self-restore is needed.
    stopRetrying: (el) => {
      if (!activeId || !vscodeApi) return;
      vscodeApi.postMessage({ type: "interrupt", id: activeId });
      const b = el as HTMLButtonElement;
      b.disabled = true;
      b.textContent = "Stopping…";
    },
    // ✕ on a queued bubble (the user 2026-07-08): cancel the queued message/command. Delegated here —
    // NOT a per-render listener on the bubble — because the transcript tail rebuilds on every push and
    // a rebuilt node eats a mid-press click (the "had to click it several times" class; CLAUDE.md).
    // The md body rides along so the kernel can verify it's still cancelling the RIGHT entry even if
    // the queue shifted between the push and the click.
    qx: (el) => {
      if (!activeId || !vscodeApi) return;
      const qmd = (el as any)._qmd as string | undefined;
      const msg: Record<string, unknown> = { type: "cancelQueued", id: activeId, md: qmd };
      if (el.dataset.qidx !== undefined) msg.idx = Number(el.dataset.qidx);
      if (el.dataset.qpark !== undefined) msg.park = Number(el.dataset.qpark);
      vscodeApi.postMessage(msg);
      if (qmd && el.dataset.qcmd !== "1") {
        // a message returns to the composer; a command just cancels. The restore is optimistic — stash
        // the composer's before/after so the kernel's cancelResult ok:false can undo it (untouched only).
        const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
        const before = ta ? ta.value : "";
        restoreToComposer(qmd);
        pendingCancelRestores.set(activeId + " " + qmd, { before, after: ta ? ta.value : "" });
      }
      // Optimistic; the next push rebuilds the queue without it. The GROUP is reflowed in the same breath —
      // the bubble alone leaves its "1 queued message" header behind, still counting what just went.
      const bub = el.closest(".queued-bubble") as HTMLElement | null;
      const grp = bub?.closest(".turn-queued") as HTMLElement | null;
      bub?.remove();
      if (grp) reflowQueuedGroup(grp);
    },
    // "copy to composer" on a never-delivered bubble: the echo is the only surviving copy of the text —
    // hand it back for review-and-resend (the same restore the queued ✕ uses). Delegated like qx: the
    // tail rebuilds every push, and a per-render listener eats a mid-press click.
    echorestore: (el) => {
      const t = (el as any)._etext as string | undefined;
      if (t) restoreToComposer(t);
    },
    // "dismiss" on a never-delivered bubble: the user has seen the loss — retire the echo. The node
    // goes NOW (acknowledge the click); the kernel's dismiss_echo is idempotent, so a miss just means
    // it was already gone and the next push paints without it either way.
    echodismiss: (el) => {
      if (!activeId || !vscodeApi) return;
      const msg: Record<string, unknown> = { type: "dismissEcho", id: activeId };
      if (el.dataset.euuid) msg.uuid = el.dataset.euuid;
      if (el.dataset.et) msg.t = Number(el.dataset.et);
      vscodeApi.postMessage(msg);
      (el.closest(".turn-user") as HTMLElement | null)?.remove();
    },
    // gist↔full toggle on a compact nudge bubble (the user 2026-07-17: progressive disclosure) —
    // delegated for the same reason as qx: the tail rebuilds every push, and a per-render bubble
    // listener eats a mid-press click. State keys through openFolds so it survives the rebuild.
    nudgetoggle: (el) => {
      rememberFold(el, "expanded", el.dataset.nkey || undefined);
      (el as HTMLElement).title = el.classList.contains("expanded") ? "click to collapse" : "click to expand";
    },
  });
})();
(() => {
  const tabs = document.getElementById("tabs");
  if (!tabs) return;
  delegate(tabs, {
    // Clicking a tab leaves focus ON the tab (renderTabs rebuilds the tab during setActive, which dropped
    // focus to the body — so Enter afterward did nothing). Now focus the (rebuilt) active tab, so the model
    // is consistent: tab focused → Enter drops into the message box; Escape there returns to the tabs.
    select: (el) => { const id = el.dataset.id; if (id) { setActive(id); focusActiveTab(); } },
    close: (el) => {
      const id = el.dataset.id;
      if (!id || !vscodeApi) return;
      // dead → just drop the read-only tab (optimistically too: it's the same kernel round-trip to wait
      // on). A failed provisional is local-only — the kernel never knew its id, so nothing to post.
      if (el.dataset.dead === "1") {
        if (!isProvisionalId(id)) vscodeApi.postMessage({ type: "closeTab", id });
        closeTabLocally(id);
        return;
      }
      // LIVE session: show the End confirm IMMEDIATELY, client-side — NOT via a closeSession→confirmClose
      // kernel round-trip, which made the ✕ feel unresponsive (and sometimes never opened the modal when the
      // kernel was busy). The dialog is static; the kernel doesn't need to decide it (the user 2026-06-24).
      // × = End session, full stop (the user 2026-08-11): the old "Close tab" branch hid a RUNNING
      // session — still judged and billed, but invisible — a tmux-era affordance with no SDK-era use
      // case. Close-and-reopen is End + Revive, which keeps the whole history.
      const nm = sessions.get(id)?.name || "";
      showConfirm(`End “${nm}”?`,
        "The session shuts down. Its history stays on disk — revive it any time from the picker or timeline.",
        [{ label: "End session", value: "end", danger: true }, { label: "Cancel", value: "" }],
        (v) => {
          if (v !== "end") return;   // Cancel → nothing
          vscodeApi?.postMessage({ type: "endSession", id });
          vscodeApi?.postMessage({ type: "closeTab", id });
          closeTabLocally(id);   // drop the tab + reselect NOW, and keep it gone while the kernel catches up
        });
    },
  });
  // Click-safe pressing (the user 2026-06-30): hold renderTabs() while a pointer is pressed anywhere on the
  // strip, so a kernel push mid-press can't replaceChildren() out from under the ✕/tab you're clicking and
  // drop the click (see tabPointerHeld). #tabs is stable across every rebuild, so this listener is installed
  // ONCE. Release on pointerup/cancel (anywhere — a press often ends off the tiny ✕) and on blur (the press
  // may end in another frame / outside the window, where no pointerup reaches us). The flush is a setTimeout(0)
  // so the click — dispatched immediately after pointerup, before the timer — fires against the live node first.
  tabs.addEventListener("pointerdown", () => { tabPointerHeld = true; });
  const releaseTabs = () => {
    if (!tabPointerHeld) return;
    tabPointerHeld = false;
    if (renderPendingWhilePressed) { renderPendingWhilePressed = false; setTimeout(() => renderTabs(), 0); }
  };
  window.addEventListener("pointerup", releaseTabs);
  window.addEventListener("pointercancel", releaseTabs);
  window.addEventListener("blur", releaseTabs);
})();
// right-click a selection in the transcript → Reply (quote it) / Copy
document.getElementById("content")?.addEventListener("contextmenu", showSelectionMenu);
if (vscodeApi) vscodeApi.postMessage({ type: "ready" });
