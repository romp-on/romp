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
import { TABBAR_H_KEY, TABBAR_H_DEFAULT, clampTabbarH, parseTabbarH } from "./tabbar-resize";
import { ctxFallbackColor, pickTone, readableRgb } from "./ctx-color";
import { applyTheme } from "./theme";
import { SessionViews, viewVisible, viewsKey, revealIn, viewTagUnion, viewTags, type TagUnion, type SessionTag } from "./session-views";
import { lensVisible, surfaceLens } from "./tag-lens";
import { openTagMenu, tagMenuButton, syncTagFilter } from "./tag-menu";
import { syncSessionsFromTabMeta, applyMetaToSession, notePendingMeta, PendingTabMeta } from "./tab-meta";
import { markerLabel, dayContext } from "./time-marker";
import { compactDisplay, toolCounts, type DisplayItem } from "./compact";
import { senderKind } from "./sender-identity";
import { loadSettings, onExternalSettingsChange, installSettingsSync, type RompSettings } from "./settings";
import { delegate } from "./actions";
import { KIND_WORD, kindWord } from "./spin-caption";
import { isClearCmd, openTopTitles, clearConfirmDetail, endConfirmDetail } from "./clear-confirm";
import { prebuildPlan, type ViewState } from "./prebuild";
import { reconcileTabOrder } from "./tab-order";
import { writeViewOrder } from "./view-order";
import { titleWithKey, chordOf, effectiveChord, loadOverrides } from "./keybindings";
import { DEFAULT_CHORDS } from "./commands";
import { NavHistory } from "./nav-history";
import { StagedStack } from "./staged-messages";
import { mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional } from "./provisional";
import { onlyTag, matchesOnly } from "./only-filter";
import { numberDiff, type DiffRow } from "./diff-lines";
import { parseAgentNotif, type AgentNotif } from "./agent-notif";
import { subTabId, isSubId, subParts, subLabel, gistLines, subHeadParts, openIconSvg, pinIconSvg, type SubMeta, type AgentGist } from "./subagent-view";
import { previewKind, previewFull, canPreview, fileUrl, retryFailedPreviews, refreshSettledPreviews, installMdImgHeal, setLightboxNav, type LightboxNavEntry } from "./preview";
import { openFileView } from "./file-view";
// initFileView rides its OWN line: the import above is pinned verbatim by file-view.test.ts
import { initFileView } from "./file-view";
import { initFileBrowse, openFileBrowse } from "./file-browse";   // the browser is pane-local here now (the user 2026-08-24)
import { pastedFilePath } from "./paste-path";
import { hostNameNodes, hostPartsNodes, hostPrefix, hostOf, hostIsDown, hostDownNote } from "./host-prefix";
import { dirStatusHint, nextDirActive, createDirPrompt, type DirStatus } from "./dir-complete";
import { mediaSrc, kernelUrl } from "./media";
import { initStrip, fmtReset } from "./strip";
import { apiErrorReason } from "./api-error-reason";
import { mathBlock, mathInline } from "./math";
import { setTip, pruneTip } from "./tip";
import { agentCount, replyOwed, threadsByAnchor, threadBusy, threadStuck, findAnchorRange, sliceRanges, prunePending, type CommentThread } from "./comments";
import { dragSlotIndex } from "./dragslot";

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
// ("Other"), and is empty while the question is still pending (multi-select answers arrive pre-split:
// the kernel fills chosen from the record's structured answers map — a LIST of picked labels when
// multiSelect, which renderAsk highlights per value; the flag itself rides along for reference).
type AskAnswerBlock = { question: string; header?: string; options: { label: string; description?: string }[]; chosen: string[]; multiSelect?: boolean };

// A completed background command's detail, keyed by its tool-use-id — the shell it ran + its output tail,
// joined in by the kernel (build_session's taskOutputs) so the inline completion card can expand into it.
type TaskOutputs = Record<string, { command: string; output: string }>;

type ChatEvent = (
  // mid/mids: postal message ids the kernel could NOT resolve into cards, carried on the raw turn so a
  // timeline arc into it still lands (see _hydrate_postal's unresolved path)
  | { kind: "user"; md: string; uuid?: string; ts?: string; reminders?: string[]; taskOutputs?: TaskOutputs; human?: boolean; romp?: boolean; rompAuto?: boolean; rompSystem?: boolean; followUp?: boolean; goal?: string; fuCtx?: string; canned?: string; tag?: string; mid?: string; mids?: string[]; images?: { src: string; path?: string }[]; undelivered?: boolean; echoT?: number; spacePaths?: string[]; pathLinks?: Record<string, string>; pathPins?: Record<string, string> }
  | { kind: "assistant"; md: string; uuid?: string; ts?: string; spacePaths?: string[]; pathLinks?: Record<string, string>; pathPins?: Record<string, string> }   // spacePaths: backticked filenames WITH spaces the kernel verified exist (build_session _space_paths) → whole-span links. pathLinks: path-shaped tokens the kernel verified against the filesystem, token → real open target (build_session _path_links) — the linkifier's gate
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
      // Subagent transcripts (plans/subagent-transcripts.md): the tool_use BLOCK id (uuid is the record's);
      // for Agent/Task the agent the launch wrote (null when no sidecar/ack names one), whether the launch
      // was a background one, whether it is still running, and — only while running — the live preview
      // of its last few tool calls. The kernel clears `output` while a background agent runs (the launch
      // ack is not a report) and fills it with the notification's <result> once it lands.
      toolUseId?: string;
      agentId?: string | null;
      agentAsync?: boolean;
      agentRunning?: boolean;
      agentGist?: AgentGist;
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
  | { kind: "queued"; texts: { md: string; followUp?: boolean; goal?: string; fuCtx?: string; idx?: number; park?: number; cancelable?: boolean; optimistic?: boolean; imgPaths?: string[] }[]; ts?: string; uuid?: string; bare?: boolean; held?: { reason: string; resetsAt?: number | null; what: string; detail?: string } }   // imgPaths: an optimistic echo's dragged-image attachments → thumbnails, the landed form's own renderer (the user 2026-08-25)
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
  // the BRANCH divider (the user 2026-08-13): this session forked off another at `cut` — everything
  // above the divider is history shared with the parent. Clicking jumps to the parent at that spot.
  | { kind: "branch"; fromSid?: string; fromName?: string; cut?: string; ts?: string; uuid?: string }
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
  // Durable command GESTURE (the user 2026-08-14): a /model-/effort-/auth-style pick used to survive only as
  // the synthesized live chip, which stale_cmd prunes on the next human turn — the user's own gesture then
  // vanished from their side of the history while the applied note stayed. The kernel writes a
  // {"t","cmdGesture"} marker at the request moment and interleaves this once the live chip retires, so the
  // right side keeps "what you did" and the left rail keeps "it took effect". cmd is the full "/effort high"
  // text. SDK-only, like effortApplied.
  | { kind: "cmdGesture"; cmd: string; ts?: string; uuid?: string }
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

type ChipState = "working" | "ready" | "needsInput" | "awaiting" | "awaitingBg" | "idle" | "closed" | "compacting" | "clearing" | "blocked" | "retrying" | "interrupting" | "opening";   // needsInput = a live permission/picker prompt (on YOU) — renamed from the legacy "awaiting" (2026-08-15), which stays accepted for OLDER REMOTE KERNELS across federation; awaitingBg = idle main thread waiting on background work it dispatched (the user 2026-07-13)
type PeerIdent = { name: string; host?: string; sid?: string; color?: { bg: string; fg: string } | null };   // a named peer behind a peer-kind wait (kernel _peer_identity, 2026-08-26)
interface Status { state: ChipState; sinceEpoch: number | null; awaitingWhy?: string | null; awaitingKind?: string | null; awaitingPeers?: PeerIdent[] | null; awaitingTasks?: string[]; awaitingTaskIds?: string[]; awaitingCount?: number | null; effort?: string; model?: string; modelPending?: boolean; effortPending?: boolean; mode?: string; fast?: string; auth?: string; authLive?: string; authPending?: boolean; authBoth?: boolean; authAcct?: string; ctx?: string; ctxOver?: boolean; ctxColor?: number[]; modelColor?: number[]; effortColor?: number[]; modelTone?: number[]; effortTone?: number[]; ctxTone?: number[]; faded?: boolean; backend?: string; apiTooLong?: boolean; apiSpendLimit?: boolean; apiModelLimit?: boolean; apiAuthErr?: boolean; apiRefusal?: boolean; retrySuppressed?: boolean; retryNextAt?: number | null; retryTries?: number | null; }   // awaitingWhy/awaitingTasks = what an awaitingBg session is waiting on (kernel _session_awaiting's phrasing + the live awaited task descriptions) — the #bg-tasks box renders it when no tracked tasks claim the box (renderAwaitWhy; the user 2026-08-13, who moved it out of the statusline the same day PR #350 put it there)   // retrySuppressed = the user interrupted this thread's API-error storm → romp's auto-retry stays OFF for it until a successful turn re-arms (the user 2026-07-06). backend = "tmux" | "sdk"; apiTooLong = the "blocked" is a "prompt is too long" error (on you → red tab) vs a transient API error (amber/retrying); apiSpendLimit = a monthly spend cap (on you → raise it; NEVER auto-retried — retrying can't fix it, the user 2026-07-14); apiModelLimit = this session's MODEL is out of allowance (on you → switch model or add credits; not auto-retried either, the user 2026-08-01); apiRefusal = the model's safeguards refused the prompt itself (on you → rewrite it or drop the thread; never auto-retried — a refusal is deterministic on the same input, so a retry just manufactures the same refusal, the user 2026-08-15); ctxColor = the GLOBAL colormap's RGB for the context%, computed server-side; modelColor/effortColor = the same map's RGB tint for the model name + effort (by capability/effort rank), server-computed; modelPending = a /model switch is resolving → the badge shows switching-dots until the new name lands (server-driven, event-based, the user 2026-07-03); fast = the CLI's fast-mode state ("on"/"off"/"cooldown", from the SDK init's fast_mode_state; absent = unknown/unavailable → no fast badge)
interface Color { bg: string; fg: string; }
// A run_in_background task surfaced in the #bg-tasks box (the kernel's _bg_tasks): a one-line summary +
// status, expandable to the command + its output. status = running | completed | failed. For a dispatched
// agent/workflow, `summary` is the dispatch's description (or the workflow meta's summary) and `command`
// carries the full ask — the Agent prompt / the Workflow script — so the row's detail level says what the
// work IS, not a generic label over an empty block (the user 2026-08-15).
interface BgTask { id: string; status: string; summary: string; command?: string; output?: string; agentId?: string; }   // agentId: an AGENT row (its own transcript is openable — plans/subagent-transcripts.md); absent on shell tasks
// The box payload: count (total to surface → the "N background tasks" header) + up to 16 tasks (the list).
interface BgTasks { count: number; tasks: BgTask[]; }
// events is a contiguous TAIL of the transcript: global indices [headFrom, headTotal). On a fresh load the
// kernel ships only the last WIRE_TAIL events (headFrom > 0) to keep startup light; older history streams in
// on scroll-back (loadOlder → chatHead prepends, lowering headFrom). headFrom 0 = the whole transcript is
// resident. chatTail's `from` is GLOBAL and mapped through headFrom.
interface Session { id: string; name: string; color: Color | null; events: ChatEvent[]; status: Status; firstSeen?: number; cwd?: string; gitBranch?: string; workTree?: { dir: string; branch: string } | null; headFrom?: number; headTotal?: number; bgTasks?: BgTasks; hideFromFeed?: boolean; postalServiceOff?: boolean; notify?: boolean; branch?: { fromSid: string; fromName: string; cut: string; t: number } | null; branches?: { sid: string; name: string; cut: string; t: number }[] | null; sub?: SubInfo; }
// A SUBAGENT VIEWER pseudo-session (plans/subagent-transcripts.md): a read-only tab whose events are one
// agent's own transcript, fed by {type:"subagent"} frames. Client-only — the kernel never lists it in
// tabOrder (reconcileTabOrder keeps a known, never-kernel-seen id), so it lives exactly as long as the
// viewer. anchorUuid = the parent's Agent tool head, for the header's link back.
interface SubInfo { parentId: string; agentId: string; meta: SubMeta | null; running: boolean; truncated: boolean; error: string | null; loaded: boolean; anchorUuid: string | null; }

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
const pendingSent = new Map<string, { text: string; ts: number; base: number; imgPaths?: string[] }[]>();
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
// What each session's tail last SHOWED optimistically (texts, joined) — reconcileOptimistic compares
// against it so the view repaints exactly when the visible echo set CHANGES, even though the event
// COUNT may not: a landing frame that replaces the echo 1:1 (its user atom in, our bubble out) left
// syncView's rendered===len fast path skipping the swap, so the dashed bubble lingered past its own
// landing until some later push (found by the 2026-08-25 continuity harness). A map, not in-array
// bookkeeping: upsert hands this function a FRESH events array, so the previous pass's injections
// are only knowable from state that survives the frame.
const echoShownSig = new Map<string, string>();

function reconcileOptimistic(s: Session): void {
  const settle = (after: string[]) => {
    const sig = after.join("\u0000");
    if ((echoShownSig.get(s.id) || "") !== sig) {
      if (sig) echoShownSig.set(s.id, sig); else echoShownSig.delete(s.id);
      const v = views.get(s.id);
      if (v) v.stale = true;
    }
  };
  // undo our own injections. A standalone bare group is tail-appended (pop it); a kernel group we EXTENDED is
  // restored by dropping the optimistic texts off our clone — so `landed` below only ever sees kernel truth.
  while (s.events.length && isOptimistic(s.events[s.events.length - 1])) s.events.pop();
  const qi = tailQueuedIdx(s.events);
  if (qi >= 0) {
    const q = s.events[qi] as Extract<ChatEvent, { kind: "queued" }>;
    if (q.texts.some((t) => t.optimistic)) s.events[qi] = { ...q, texts: q.texts.filter((t) => !t.optimistic) };
  }
  const list = pendingSent.get(s.id);
  if (!list || !list.length) { settle([]); return; }
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
  if (!inject.length) { settle([]); return; }
  // cancelable from the PRESS (the user 2026-08-30, who sent mid-compaction and sat in an unlabeled,
  // uncancellable beat until the kernel's park round-tripped): the ✕ on an optimistic bubble drops our
  // re-injection and asks the kernel to cancel by body wherever the send landed (see the qx handler).
  const mk = (p: { text: string; imgPaths?: string[] }) => ({ md: p.text, optimistic: true, cancelable: true, imgPaths: p.imgPaths });
  const qj = tailQueuedIdx(s.events);
  if (qj >= 0) {
    // something IS queued here → ours queues behind it: show it in that group, under its header, counted
    const q = s.events[qj] as Extract<ChatEvent, { kind: "queued" }>;
    s.events[qj] = { ...q, texts: [...q.texts, ...inject.map(mk)] };
  } else {
    // nothing known-queued → a BARE dashed bubble: no "N queued messages" header to claim what we can't back
    s.events.push({ kind: "queued", bare: true, texts: inject.map(mk), uuid: OPT_PREFIX + inject[0].ts });
  }
  settle(inject.map((p) => p.text));
}

// Record a composer send as in-flight and show its optimistic bubble NOW (before any kernel push).
function registerOptimistic(id: string, text: string, imgPaths?: string[]): void {
  const arr = pendingSent.get(id) || [];
  arr.push({ text, ts: Date.now(), base: -1, imgPaths });   // base is stamped by the reconcile just below
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
    // Your own send reveals itself only from the TAIL, measured BEFORE the bubble lands (the append
    // grows scrollHeight, which would misread a tail-sitter as scrolled-up). At — or within the
    // stick rule's 80px of — the bottom, hitting Enter scrolls to the new bubble, exactly once, at
    // send time (the user 2026-08-09, whose send painted below the fold and looked lost). Scrolled
    // UP reading history, the viewport stays exactly where it is and the bubble waits below (the
    // user 2026-08-30, yanked mid-read by the unconditional snap that used to live here).
    const content = document.getElementById("content");
    const wasAtBottom = !!content && nearBottom(content);
    appendActive();
    if (content && wasAtBottom) content.scrollTop = content.scrollHeight;
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
// Optimistic label/color edits awaiting their kernel echo — holds a stale in-flight push from
// reverting the strip (see tab-meta.ts; the sessionViews pending machinery's reasoning).
const pendingTabMeta = new Map<string, PendingTabMeta>();
// The romp identity palette for the tab right-click color picker (the user 2026-06-29). Fetched once from the
// kernel's /palette so the client holds no color literals; empty until it lands (the menu just omits the row).
// The palette is SELECTABLE now (the user 2026-07-12): a {type:"palette"} push lands the new set on switch.
// ── session views (the user 2026-08-18): the kernel's views blob gates which sessions get TABS.
// A hidden session is a BACKGROUND session — still running, judged and carded; the + picker lists it
// under "Hidden" and the timeline's corner panel counts it, so it is always one glance away.
// Captured from every tabOrder push; a local gesture (hide from the tab menu, reveal from the
// picker) applies optimistically and holds sticky until a push echoes it — yielding to the kernel
// after three silent pushes, the same machinery the timeline's copy runs.
let sessionViews: SessionViews | null = null;
let pendingSessionViews: SessionViews | null = null;
let pendingViewsAge = 0;
let allHiddenBlanked = false;   // the active transcript was blanked because EVERY session is view-hidden
function effViews(): SessionViews | null { return pendingSessionViews ?? sessionViews; }
function captureViews(v: SessionViews | null) {
  if (v) sessionViews = v;
  // v null = a tabOrder frame WITHOUT the blob (an older kernel in a mixed-version mesh): it still
  // ages a pending edit, or the optimistic state would fake success forever against a kernel that
  // will never confirm it
  if (pendingSessionViews && ((v && viewsKey(v) === viewsKey(pendingSessionViews)) || ++pendingViewsAge >= 3)) {
    pendingSessionViews = null; pendingViewsAge = 0;
  }
  // A VIEW CHANGE that excludes the ACTIVE session converts it into the peek instead of bouncing
  // (the user 2026-08-24: open All, pick a session, re-apply the tag filter — keep reading it in
  // the ghost dress, and it evaporates on the next tab switch). The same derivation setActive runs
  // on activation, run here on every views arrival; symmetric, so a view that now INCLUDES the
  // active peek sheds the dress for free. With the active session counted by tabInView as the peek,
  // the deferred first-tab bounce never fires (its fire-time revalidation re-checks tabInView).
  if (activeId) assertPeekFor(activeId);
}
// (postViews below runs the same re-derivation for the LOCAL optimistic edit — both views-arrival
// paths keep the active session's peek state current.)
function postViews(v: SessionViews) {
  pendingSessionViews = v; pendingViewsAge = 0;
  if (activeId) assertPeekFor(activeId);   // the optimistic edit re-derives the active session's peek too
  if (vscodeApi) vscodeApi.postMessage({ type: "setTimelineViews", views: v });
  renderTabs();
}
// ── EPHEMERAL PEEK TAB (the user 2026-08-24, superseding the kernel's reveal-rule view mutation):
// activating a session the current view HIDES opens it as a TEMPORARY tab — real and scrollable,
// dressed .tab-peek ("breaks the view's rules") — and auto-closing: activating any other tab drops
// it, no explicit close. Per-dashboard client state like the pending-views copy above: never
// persisted, never federated, never written to timeline-views.json — a click is a peek, not a view
// edit (the picker's other-view row jumps views instead — the hidden set retired 2026-08-24). Sending a message from a peeked
// session does NOT pin it: the peek still drops on click-away, and the session stays reachable via
// the feed and the nav trail. Nav history stores only the sid — back/forward lands in setActive
// like every activation, which re-derives peek-vs-normal from the CURRENT views (a since-revealed
// session re-pops as a normal tab, a since-hidden one as a peek).
let peekId: string | null = null;
// the CHAT surface keys on its own lens (per-surface selections, the user 2026-08-25) — the scalar
// viewVisible stays for legacy callers; tabs and peeks decide through actives.chat
function chatVisible(id: string): boolean {
  // a subagent viewer is in the chat lens only once PINNED (its header's pin control): unpinned it is
  // the peek — assertPeekFor's own derivation then dresses it .tab-peek and drops it on the next
  // activation, with no second peek mechanism (plans/subagent-transcripts.md)
  if (isSubId(id)) return pinnedSubs.has(id);
  const v = effViews();
  return lensVisible(surfaceLens(v, "chat"), viewTagUnion(v), id);
}
// Pinned subagent viewers ("keep this tab"). Client state like peekId: a pinned viewer does NOT survive
// a reload in this slice (deliberate — the kernel has no tab-order entry to restore it from; see plan).
const pinnedSubs = new Set<string>();
function assertPeekFor(id: string): void {
  const next = chatVisible(id) ? null : id;
  if (next !== peekId) { peekId = next; renderTabs(); }
}
function tabInView(id: string): boolean { return id === peekId || chatVisible(id); }
function visibleOrder(): string[] { return order.filter(tabInView); }
function revealSession(id: string) { postViews(revealIn(effViews(), id)); }

let paletteColors: string[] = [];
fetch(kernelUrl("/palette"), { cache: "no-store" }).then((r) => r.json())
  .then((d) => { if (Array.isArray(d.colors)) paletteColors = d.colors; }).catch(() => { /* menu omits the swatch row */ });
const mru: string[] = [];             // recency stack, front = most-recently-active (close → return to previous)
let activeId: string | null = null;
let renderingSid: string | null = null;   // the session id syncView is currently building (for per-session fold keys)
// The SESSION whose transcript DOM is being built — the id preview/image URLs must bake in, host prefix
// included. Distinct from renderingSid, which is a fold KEY the comment popover retargets to its thread id.
// Baking activeId instead routed a background build's file fetches at whatever session the user was READING:
// the idle prebuild renders hidden tabs' new turns, so a federated session's figures asked the WRONG host's
// kernel, whose truthful 404 looped "fetching → not found" on files that exist — and every retry/heal
// re-fetches the closure's captured URL, so only the next send's tail re-render ever fixed it (the user
// 2026-08-24, the recurring inline-preview failure). Same class for relative paths across LOCAL sessions:
// they resolved against the active session's cwd, not the owning session's.
let renderingOwnerSid: string | null = null;
// TRUE while fillCommentMsgs renders into the comment popover (the user 2026-08-23): the thread uses
// the chat's own renderer deliberately — but the transcript-COUPLED hover machinery must stay out.
// wireTurnHover's glow band appends to turn.parentElement (the .cmt-msgs list, positioned nowhere the
// band math expects), its dotHover/dotOpen posts carry the MAIN session's id with the THREAD's uuids
// (cross-lighting the timeline wrongly, and a dot click "jumps" somewhere false), and the rail
// time-markers paint 45px left of a turn that has no gutter — a clipped sliver at the popover edge.
let renderingIntoThread = false;
// restore the last-active tab on refresh (persisted via setState); one-shot, applied when its session arrives
let wantActive: string | null = (() => { try { return ((vscodeApi?.getState?.() || {}) as any).activeId || null; } catch { return null; } })();
let pendingAnchor: string | null = null; // deep-link target waiting to be scrolled to
let pendingAnchorIntent: string | null = null; // kind the uuid anchor must honor — sticks with pendingAnchor across render-pass retries (pendingAnchorKind is cleared each pass, this isn't)
let pendingAnchorT: number | null = null; // time fallback (epoch s) when the uuid can't resolve
let pendingAnchorKind: string | null = null; // intent for the time fallback: "user" = land on the user's own turn
let anchorPendingOlder = false; // scrollToAnchor kicked off a loadOlder fetch for an anchor past the resident tail → don't toast "couldn't locate"; chatHead re-lands when the chunk arrives (the user 2026-06-27)
// ── the SEEK (the user 2026-08-25): a card/summary click sometimes needed a second press ──
// The give-up underneath: landActive runs ONE scrollToAnchor attempt per pass and then nulls
// pendingAnchor unconditionally — the "stash for the next render pass" scrollToAnchor writes was
// wiped in the same breath, so any transient miss (a re-query racing the window re-render it just
// asked for, a push rebuilding mid-seek) one-shotted the navigation into the couldn't-locate
// toast. The second click "worked" because the first click's re-render had made the target
// resident. The seek is now DURABLE state: it re-arms the attempt on every render pass (pushes,
// chatHead arrivals — events, not timers) until it LANDS, the user CANCELS via the indicator's ✕,
// or the standing can't-trap backstop expires into the honest failure. While it outlives the
// immediate landing, a small pane-local notice says so ("finding the passage…") with the ✕ —
// cancel leaves the reader exactly where they are, scroll fully theirs.
let seek: { sid: string; uuid: string; kind: string | null } | null = null;
let seekBackstop: number | undefined;
const SEEK_BACKSTOP_MS = 30_000;

function armSeek(sid: string, uuid: string, kind: string | null): void {
  if (seek && seek.sid === sid && seek.uuid === uuid) return;   // same target mid-seek: idempotent, never a restart
  clearSeek();                                                  // a different target supersedes cleanly
  seek = { sid, uuid, kind };
  seekBackstop = window.setTimeout(() => failSeek(), SEEK_BACKSTOP_MS);
}

function clearSeek(): void {
  seek = null;
  if (seekBackstop !== undefined) { clearTimeout(seekBackstop); seekBackstop = undefined; }
  document.getElementById("seek-note")?.remove();
}

/** Drop the seek's claim on any in-flight older fetch: the chunk (if one is on the wire) arrives as
 *  a PURE prepend re-anchored on the reader's own row + offset — never a yank to the abandoned target. */
function releaseSeekFetch(sid: string): void {
  anchorPendingOlder = false;
  if (loadingOlder.has(sid)) {
    const content = document.getElementById("content");
    const v = views.get(sid);
    const keep = content && v && sid === activeId ? captureScrollAnchor(content, v) : null;
    if (keep) { pendingOlderAnchor.set(sid, keep.uuid); pendingOlderKeepY.set(sid, keep.y); return; }
  }
  pendingOlderAnchor.delete(sid);
  pendingOlderKeepY.delete(sid);
}

function cancelSeek(): void {   // the indicator's ✕ — the reader keeps their spot
  if (!seek) return;
  const sid = seek.sid;
  pendingAnchor = null; pendingAnchorIntent = null; pendingAnchorT = null; pendingAnchorKind = null;
  releaseSeekFetch(sid);
  clearSeek();
}

function failSeek(): void {     // the backstop: the seek could not land — say so, honestly, once
  if (!seek) return;
  const sid = seek.sid;
  pendingAnchor = null; pendingAnchorIntent = null; pendingAnchorT = null; pendingAnchorKind = null;
  releaseSeekFetch(sid);
  clearSeek();
  landToast("couldn't locate this in the transcript");
  notifyShell("locate", "Couldn't jump to this in " + (sessions.get(sid)?.name || "the transcript")
              + ". The chat is missing that part of its history.", sid);
}

/** The pane-local seek notice: pulsing dots + "finding the passage…" + the cancel ✕. Created once
 *  per seek (never rebuilt by renders — click-safe by construction; removal is the ✕'s immediate
 *  acknowledgement), shown only while the seeking session is the active tab. */
function showSeekNote(): void {
  if (!seek) return;
  const existing = document.getElementById("seek-note");
  if (seek.sid !== activeId) { existing?.remove(); return; }
  if (existing) return;
  const n = el("div", "");
  n.id = "seek-note";
  n.appendChild(metaDots());
  const label = el("span", "seek-note-label");
  label.textContent = "finding the passage…";
  n.appendChild(label);
  const x = el("button", "seek-note-x");
  x.setAttribute("aria-label", "Stop looking");
  x.title = "stop looking — stay right here";
  x.textContent = "✕";
  x.addEventListener("click", (e) => { e.stopPropagation(); cancelSeek(); });
  n.appendChild(x);
  document.body.appendChild(n);
}
// KEEP-OFFSET landing (the user 2026-08-02). A scroll-back loadOlder re-anchors on the row the reader was
// on — that is POSITION PRESERVATION, not a deep-link: the row must come back at the SAME on-screen offset,
// with no top-align and no flash. Non-null ⇒ resolve pendingAnchor by id as usual (which renders the window
// around it), then restore it to this y instead of calling landOn. Sticks with pendingAnchor across
// render-pass retries, like pendingAnchorIntent.
let pendingAnchorKeepY: number | null = null;
let flashedAnchor: string | null = null; // the anchor already flashed THIS navigation — a deep anchor
// re-lands once per older-history fetch round, and each re-land used to pulse again (the user
// 2026-08-15: "pulsating way too many times"). A NEW navigation (setActive with an anchor) re-arms.
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
    // svg profile too (the user 2026-08-19): KaTeX's html output still draws STRETCHY glyphs —
    // \sqrt radicals, wide accents, extensible arrows — as inline <svg><path>, and the html-only
    // profile silently ate them: $\sqrt{d}$ rendered as a bare serif "d", the radical gone.
    // DOMPurify's svg profile is still sanitized (no scripts, handlers, or foreignObject).
    return DOMPurify.sanitize(dirty, { USE_PROFILES: { html: true, svg: true }, ADD_DATA_URI_TAGS: ["img"] });
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

function dot(kind: "green" | "ring" | "user" | "red" | "romp" | "working" | "tag"): HTMLElement { return el("span", "dot " + kind); }

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

// A .fold-pre's inner scroll position must survive the same rebuilds openFolds survives (the user
// 2026-08-14: reading a scrolled CLAUDE.md doc in the System context card, the box snapped to its
// top on every kernel push — the rebuilt node is a fresh element at scrollTop 0, and pushes land
// every 0.5–3s). Saved per stable key as the user scrolls; reapplied a frame after the rebuilt node
// lands, because a node that hasn't laid out yet clamps any scrollTop write back to 0. Keyless
// callers (notices, reminder bodies) stay transient, exactly like keyless folds.
const foldScroll = new Map<string, number>();
function keepScroll(box: HTMLElement, key?: string): HTMLElement {
  if (!key) return box;
  box.addEventListener("scroll", () => { foldScroll.set(key, box.scrollTop); }, { passive: true });
  const saved = foldScroll.get(key);
  if (saved) requestAnimationFrame(() => { box.scrollTop = saved; });
  return box;
}

function preEl(text: string, scrollKey?: string): HTMLElement {
  const pre = el("pre", "io-pre fold-pre");
  pre.textContent = text;
  return keepScroll(pre, scrollKey);
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

// Where a clicked file path should actually open, which depends entirely on which host you are in.
//
//   • VS Code → the host extension's openFile handler, i.e. the editor two inches away. Unbeatable.
//   • Web dashboard → the viewer, as a modal over THIS pane (file-view.ts; the user 2026-08-15 — the
//     first cut filled the feed pane, and reading a file cost the cards). The bytes come to the
//     browser over /file, which is the fix for the original break (the user 2026-08-08): the kernel
//     used to run an opener on ITS machine, the wrong screen entirely from another device.
//
// Same document as the click, so there is no shell relay and no fallback ladder: standalone /chat
// and the framed pane behave identically.
function openPath(path: string, sid?: string | null): void {
  if (!vscodeApi) return;
  if (location.protocol === "http:" || location.protocol === "https:") {
    openFileView(path, sid || activeId || null);
    return;
  }
  vscodeApi.postMessage(sid ? { type: "openFile", path, id: sid } : { type: "openFile", path });
}

// Surface the FILE BROWSER at `path` for the session: the shell brings the feed pane forward and the
// browser overlay opens there (unlike openPath's in-pane viewer modal, the browser overlay lives in
// the feed document). Web-only, and only when a shell exists to relay to; VS Code's affordances are
// gated off at their call sites (the editor has its own explorer, and the webview can't reach the
// kernel origin anyway).
function openBrowse(path: string, sid?: string | null): void {
  // PANE-LOCAL since 2026-08-24 (the user: it opened over the FEED cards — the wrong pane): the
  // browser is a modal over the chat that launched it, the same document the viewer already uses —
  // no shell lift, no pane juggling (the shell's browseClosed restore is a no-op here: it only
  // fires when the shell itself lifted the feed). Web-only stands — the VS Code webview cannot
  // reach the kernel origin, and the editor has its own explorer.
  const web = location.protocol === "http:" || location.protocol === "https:";
  if (!web) return;
  openFileBrowse(path || ".", sid || activeId || null);
}
// (The old forwarder that relayed the viewer's directory-half {romp:'browseFiles'} ask to the shell
// is gone with the move: initFileBrowse's own listener answers it in THIS document now.)
initFileBrowse((m) => vscodeApi?.postMessage(m));

// A clickable file name that opens the real file — in the editor (VS Code) or the in-pane viewer
// modal (web). Shared open/navigate surface; see extension.ts's openFile handler and file-view.ts.
function fileLink(path: string): HTMLElement {
  const a = el("span", "tool-file");
  a.textContent = shortPath(path);
  a.title = "Open " + path;
  a.addEventListener("click", (e) => {
    e.stopPropagation();
    openPath(path);
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
// EVERYTHING on this ride is keyed by (session, path), never the bare path: the same RELATIVE path
// string in two sessions names two different files (each cwd its own), and a bare-path cache let the
// FIRST asker's answer fill every session's chips — wrong pixels shown silently, and a first-ask
// failure parked every session's chip (found adversarially reviewing the owner-sid fix, 2026-08-24).
// The chip carries its sid in data-imgsid so refills and heals match on both halves.
const imgKey = (sid: string | null, p: string): string => (sid || "") + "\u0000" + p;
const imgUrlCache = new Map<string, string>();   // (sid,path) → dataURL (loaded)
const imgFailed = new Set<string>();             // (sid,path) → keep the chip until a reconnect heal
const imgRequested = new Set<string>();          // (sid,path) → request in flight
function fillPathImg(wrap: HTMLElement, p: string, sid: string | null): void {
  wrap.textContent = "";
  const url = imgUrlCache.get(imgKey(sid, p));
  if (url) {
    const img = document.createElement("img"); img.className = "user-img"; img.src = url; img.loading = "lazy"; img.title = p;
    wrap.appendChild(img);
  } else {
    // still waiting on the host round-trip → pulsing-dots loading cue (pure CSS — the sandbox can't
    // fetch the swirl asset); a FAILED path drops the pulse and reads as the plain chip it is
    const chip = el("div", "user-img-path" + (imgFailed.has(imgKey(sid, p)) ? "" : " img-pending"));
    chip.textContent = "🖼 " + (p.split("/").pop() || p); chip.title = p;
    wrap.appendChild(chip);
  }
}
function buildPathImg(p: string, sid: string | null): HTMLElement {
  const wrap = el("span", "js-pathimg"); wrap.dataset.imgpath = p;
  wrap.dataset.imgsid = sid || "";
  fillPathImg(wrap, p, sid);
  const k = imgKey(sid, p);
  if (!imgUrlCache.has(k) && !imgFailed.has(k) && !imgRequested.has(k)) {
    imgRequested.add(k);
    // id → the kernel resolves a RELATIVE path against this session's cwd (assistant-mentioned
    // "plots/out.png" renders too, not just absolute user-attachment paths — the user 2026-07-20).
    // The OWNING session's id, passed by the caller: a background build must never send activeId here.
    if (vscodeApi) vscodeApi.postMessage({ type: "imgRequest", path: p, id: sid });
  }
  return wrap;
}
function onImgData(p: string, url: string | null, sid: string | null): void {
  // A kernel that echoes the sid answers exactly the chips that asked; an OLDER kernel's reply
  // (no sid echo) falls back to path-only matching — at worst the old sharing, never a dead chip.
  const bySid = typeof sid === "string";
  const keys = bySid ? [imgKey(sid, p)]
    : Array.from(document.querySelectorAll(".js-pathimg"))
        .filter((n) => (n as HTMLElement).dataset.imgpath === p)
        .map((n) => imgKey((n as HTMLElement).dataset.imgsid || null, p));
  for (const k of keys) {
    imgRequested.delete(k);
    if (url) imgUrlCache.set(k, url); else imgFailed.add(k);
  }
  document.querySelectorAll(".js-pathimg").forEach((n) => {
    const e = n as HTMLElement;
    if (e.dataset.imgpath !== p) return;
    if (bySid && (e.dataset.imgsid || "") !== (sid || "")) return;
    fillPathImg(e, p, e.dataset.imgsid || null);
  });
}
// RECONNECT-class heal for the path-image chips (the user 2026-08-24): imgFailed was "never retry
// for the page lifetime", so a figure that failed while the kernel/tunnel was away stayed a dead
// chip forever. A reconnect un-parks every failed path for ONE fresh host round-trip — the same
// request flow buildPathImg runs on mint (imgRequested still dedups in-flight asks).
function healPathImgs(): void {
  if (!imgFailed.size) return;
  const failed = new Set(imgFailed);
  imgFailed.clear();
  document.querySelectorAll(".js-pathimg").forEach((n) => {
    const e = n as HTMLElement;
    const p = e.dataset.imgpath;
    // the chip's own minted sid — a heal fired while another tab is active must re-ask for the
    // OWNING session, not the one being looked at (and the popover's chips carry theirs too)
    const own = e.dataset.imgsid || activeId;
    const k = p ? imgKey(own, p) : "";
    if (!p || !failed.has(k)) return;
    fillPathImg(e, p, own);                          // back to the pending pulse while the ask is out
    if (!imgRequested.has(k)) {
      imgRequested.add(k);
      if (vscodeApi) vscodeApi.postMessage({ type: "imgRequest", path: p, id: own });
    }
  });
}
// the page shim fires romp:wsup when THIS pane's kernel socket reconnects (kernel.py ws.onopen) —
// the same kernel-is-back event a hostUp is for a federated tunnel; heal everything on it
window.addEventListener("romp:wsup", () => { retryFailedPreviews(); refreshSettledPreviews(); healPathImgs(); });
installMdImgHeal();   // markdown-inline <img> failures register for the per-message heal (capture-phase, once)

// One image of a user turn: the picture (or its hydration chip) plus, when the
// on-disk path is known, a caption line — the full absolute path (click → open),
// ⧉ copies it. So both the rendered image AND its path stay accessible no matter
// how the image arrived (pasted inline, referenced by path, typed as text).
// pathInText: the path is ALREADY visible (linkified) in the message text — a
// dropped/pasted screenshot inserts it there — so a caption would just repeat it
// (the user 2026-07-15). Skip the caption then; the in-text link already opens it.
// The lightbox's arrow-navigation sequence (2026-08-29): every image embed in this chat, oldest
// to newest, from the session's EVENTS — the DOM misses virtualization-windowed turns. Each entry
// carries the same (path, sid, pin) triple the embed's own click passes, so a step renders that
// message's pinned bytes (never the live file — the history-rewrite guard extends to navigation).
// One entry per (event, target): a message mentioning one image twice is one stop.
function chatImagesFor(sid: string | null | undefined): LightboxNavEntry[] {
  const s = sid ? sessions.get(sid) : null;
  if (!s) return [];
  const out: LightboxNavEntry[] = [];
  for (const ev of s.events as (ChatEvent & { images?: { src: string; path?: string }[]; pathLinks?: Record<string, string>; spacePaths?: string[]; pathPins?: Record<string, string> })[]) {
    const pins = ev.pathPins || {};
    const seen = new Set<string>();
    const add = (target: string) => {
      if (!target || seen.has(target) || previewKind(target) !== "img") return;
      seen.add(target);
      out.push({ path: target, sid, pin: pins[target] });
    };
    for (const im of ev.images || []) if (im.path) add(im.path);
    for (const tok of Object.keys(ev.pathLinks || {})) add((ev.pathLinks as Record<string, string>)[tok]);
    for (const sp of ev.spacePaths || []) add(sp);
  }
  return out;
}
setLightboxNav(chatImagesFor);

function userImage(im: { src: string; path?: string }, pathInText = false): HTMLElement {
  const fig = el("span", "user-img-wrap");
  if (im.src.startsWith("path:")) {
    fig.appendChild(buildPathImg(im.src.slice(5), renderingOwnerSid ?? activeId));   // host reads it → real thumbnail; chip until then / on failure
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
    openPath(path);
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
// A clickable, VERBATIM file link — the SAME open-the-file path the caption/image links use (openPath:
// the editor in VS Code, the feed pane's viewer on the web). `raw` is shown as written; `open` is what
// gets opened. A bare file:// can't be followed by the browser from the http dashboard (blocked scheme)
// and a VS Code editor won't render a PDF, so it's routed rather than navigated. `relative` bare paths
// carry the active session id so whoever resolves them uses THAT session's cwd — a relative
// `design/foo.md` is relative to the repo the agent runs in, not the kernel's cwd (the user 2026-07-06).
function openPathLink(raw: string, open: string, relative = false): HTMLElement {
  const a = el("span", "file-uri-link");
  a.textContent = raw;                       // shown exactly as written, selectable/copyable in place
  a.title = "Open " + open;
  a.addEventListener("click", (e) => {
    e.stopPropagation();
    openPath(open, relative ? activeId : null);
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
    pathLinks?: Record<string, string>, pathPins?: Record<string, string>): void {
  // A whole-backtick http(s) URL becomes a TAPPABLE link that still looks like code (the user
  // 2026-08-16, on mobile, wanting to tap through to a dashboard link a session sent). Bare URLs
  // and [text](url) already link via marked's gfm autolink + the global anchor click delegate;
  // the code-span form was the one dead shape. Inline spans only (never inside <pre> blocks or an
  // existing anchor), and only when the span's ENTIRE text is one URL — a URL quoted inside prose
  // code stays code. The scheme is validated here, so the anchor is as safe as md()'s sanitized ones.
  for (const code of Array.from(root.querySelectorAll("code"))) {
    const t = (code.textContent || "").trim();
    if (!/^https?:\/\/\S+$/.test(t)) continue;
    if (code.closest("pre") || code.closest("a")) continue;
    const a = document.createElement("a");
    a.href = t;
    a.className = "url-code-link";
    a.title = t + " — opens in a new tab";
    code.replaceWith(a);
    a.appendChild(code);
  }
  const previewable: string[] = [];   // renderable paths found in this message → full renders at their mentions
  const mentionAt = new Map<string, HTMLElement>();   // path → its FIRST mention's element (figure anchor)
  const kernelVerified = new Set<string>();           // paths the kernel stat'd — their previews fail loudly, never silently
  if (spacePaths && spacePaths.length) {
    const verified = new Set(spacePaths);
    for (const code of Array.from(root.querySelectorAll("code"))) {
      if (code.closest("a, .file-uri-link, pre")) continue;    // already linked, or a fenced block
      const tok = (code.textContent || "").trim();
      if (!verified.has(tok)) continue;
      const link = openPathLink(tok, tok, true);
      code.replaceChildren(link);                              // the <code> chrome stays; its content is the link
      kernelVerified.add(tok);
      if (previewKind(tok) && !previewable.includes(tok) && !(skipThumbs && skipThumbs.includes(tok))) {
        previewable.push(tok);
        mentionAt.set(tok, code);
      }
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
      const link = isUri ? fileUriLink(tok) : openPathLink(tok, open, true);
      frag.appendChild(link);
      if (!isUri && typeof fixed === "string") kernelVerified.add(open);   // the kernel stat'd it this build
      if (previewKind(open) && !previewable.includes(open) && !(skipThumbs && skipThumbs.includes(open))) {
        previewable.push(open);
        mentionAt.set(open, link);
      }
      last = m.index + tok.length;
      re.lastIndex = last;
      any = true;
    }
    if (!any) continue;
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    tn.replaceWith(frag);
  }
  // A mentioned image/PDF renders FULL-SIZE at its MENTION — the figure lands right after the
  // paragraph/list item that names it, like figures in a document (the user 2026-08-15, whose four
  // captioned plots all collected at the message's tail, far from the prose describing each; the
  // 2026-07-20 rule — a rendered image, not a thumbnail — stands, this moves WHERE it renders).
  // Figures mentioned in the same block share one strip; a mention with no block anchor (bare text
  // at the root) keeps the old below-message placement. Absolute AND relative paths work — the
  // kernel resolves a relative one against this session's cwd, same as click-to-open. Per surface:
  //   web       — previewFull: the kernel serves the bytes straight into an <img> at the user-image
  //               scale / a PDF card. A KERNEL-VERIFIED path that fails to load shows a retry chip
  //               (transient failure — restart, tunnel blip); only an unverified one self-removes.
  //   VS Code   — the webview sandbox can't reach the kernel origin from an <img>, so an IMAGE rides
  //               the same host data-URL flow the user-message pictures use (buildPathImg, imgRequest
  //               now carrying the session id for relative resolution); a PDF keeps its click-to-open
  //               link (no inline viewer in the sandbox).
  // EVERY verified figure mention renders eagerly at its mention anchor (the user 2026-08-30,
  // overruling the 4-eager+chip fold shipped a day earlier, paraphrased: they should be able to
  // preview as many images as they want in the thread). No cap and no chip: previewFull's <img>s
  // are browser-lazy (loading="lazy"), so an off-screen figure costs one DOM node until scrolled
  // near — a many-figure message can't hurt scroll or startup — and the kernel's mention-pin store
  // is already size-bounded with eviction. Every path stays clickable regardless.
  if (previewable.length) {
    const BLOCK_SEL = "p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th";
    const strips = new Map<HTMLElement, HTMLElement>();   // figure anchor → its strip (same block shares one)
    const renderFig = (p: string): HTMLElement | null => {
      const full = canPreview() ? previewFull(p, renderingOwnerSid ?? activeId, kernelVerified.has(p), (pathPins || {})[p])
        : previewKind(p) === "img" ? buildPathImg(p, renderingOwnerSid ?? activeId) : null;
      if (!full) return null;
      const block = mentionAt.get(p)?.closest(BLOCK_SEL) as HTMLElement | null;
      const anchor = block && root.contains(block) && block !== root ? block : root;
      let strip = strips.get(anchor);
      if (!strip) {
        strip = el("div", "path-thumbs");
        // an li/td keeps its figure INSIDE (stays under its bullet/cell); a p/heading takes it after
        if (anchor === root || /^(LI|TD|TH)$/.test(anchor.tagName)) anchor.appendChild(strip);
        else anchor.insertAdjacentElement("afterend", strip);
        strips.set(anchor, strip);
      }
      strip.appendChild(full);
      return strip;
    };
    for (const p of previewable) renderFig(p);
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
  // A machine-cut turn's settle record is dropped server-side, but anchors minted AT that settle's
  // uuid (a verdict filed on the cut turn) must still land — the seam that replaced it answers to
  // them (kernel settleUuids → data-uuids, a token list like the postal data-mids).
  const su = (ev as any).settleUuids as string[] | undefined;
  if (su && su.length) turn.dataset.uuids = su.join(" ");
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
  if (epoch != null && !renderingIntoThread && turn.querySelector(".dot")) turn.insertBefore(timeMarker(epoch, prevEpoch ?? null), turn.firstChild);
  // rail-dot fleet links: hover anywhere on the turn → white-highlight this turn's
  // event on the timeline AND outline its feed card(s); click the DOT → open that
  // card's modal in the feed (the host resolves turn → event → cards). The whole
  // turn is the hover target (the user 2026-06-12) — hovering the MESSAGE bubble or
  // the WORK/reply body must light the timeline, not only the rail dot.
  const railDot = turn.querySelector(".dot") as HTMLElement | null;
  if ((anchorUuid || epoch != null) && !renderingIntoThread) wireTurnHover(turn, railDot, anchorUuid ?? null, epoch ?? 0, ev.tlId ?? null);
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
    // a tinted (unread-comment) rail opens THAT thread (the user 2026-08-23) — checked at click
    // time, so the same strip goes back to timeline navigation the moment the thread is read
    const um = turn.querySelector("mark.cmt-hl.unread") as HTMLElement | null;
    if (um?.dataset.tid && activeId) { openCommentPopover(activeId, um.dataset.tid, e.clientX, e.clientY); return; }
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
  m.dataset.epoch = String(epoch);   // the top-of-view day-context label reads this (paintRailSticky)
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

// The DAY CONTEXT label (the user 2026-08-17): "Yesterday" / "3 days ago" / "Last week" riding just
// above the rail's TOP stamp whenever that stamp is not from today — so mid-scroll through history
// the day is always in view even with the day divider scrolled away. One fixed element, painted by
// the same pass that owns the sticky stamp; its text swaps only at day boundaries, and its position
// tracks whichever stamp owns the top slot (the sticky at the line, or the leading real stamp as it
// scrolls toward the line) — the handoff lands at the same pixel, so there is no jump.
let railDay: HTMLElement | null = null;
function ensureRailDay(): HTMLElement {
  if (railDay && railDay.isConnected) return railDay;
  railDay = el("div", "rail-day");
  railDay.style.display = "none";
  document.body.appendChild(railDay);
  return railDay;
}

// SCROLL MARKS (the user 2026-08-17): a thin blue notch on the chat's right scroll edge for every
// USER message — the conversation's shape at a glance, VS Code overview-ruler style. Positions are
// proportional (turn offset over scroll height), so they are scroll-INVARIANT: the paint rides the
// rail-sticky scheduler for free, but a signature check skips the DOM work on pure scrolls and only
// rebuilds when the geometry or the set of user turns actually changed. Passive fixed chrome, like
// the sticky: it never intercepts the native scrollbar underneath. The blue is the outgoing-bubble
// blue — the color that already means "yours" — never the romp accent.
let scrollMarks: HTMLElement | null = null;
let scrollMarksSig = "";
// Measured unit heights, remembered once rendered (the user 2026-08-17, whose video showed notches
// wiggling non-uniformly while scrolling: a message crossing the render-window boundary corrected
// from the uniform average-height estimate to its true pixels). One number per event, per session —
// trivial memory — and spacer-slot positions built from these cumulative heights (normalized to the
// spacer's actual height) match truth so closely that boundary crossings correct by ~nothing.
const unitHeights = new Map<string, Map<number, number>>();

// EVENT index → DISPLAY-UNIT index. Unit === event only in NORMAL mode; in compact mode tool runs
// fold into toolgroup units, so the two spaces diverge — and contentOffsetFrame speaks UNITS
// (data-unit tags, spacer counts, cached heights are all unit-keyed) while marks anchor to EVENTS.
// Passing event indices straight through was the missing-notch bug (the user 2026-08-18: "some are
// displayed, others aren't" — replies land beside big tool runs, exactly where the spaces diverge
// most, and a wrong unit index found no node, so the mark silently vanished).
function eventUnitIndex(s: Session): Int32Array {
  const map = new Int32Array(s.events.length).fill(-1);
  const items = displayItems(s);
  for (let u = 0; u < items.length; u++) {
    const it = items[u];
    if (it.kind === "toolgroup" || it.kind === "retrygroup") { for (const i of it.indices) map[i] = u; }
    else map[it.index] = u;
  }
  return map;
}

// ONE content-space frame for every scrollbar overlay (the user 2026-08-17, watching comment ticks
// drift past message notches as history loaded): marks anchored to transcript positions share an
// inherent monotonic order, so every overlay must place them with the SAME event-index → pixel
// mapping. Since T129 (the user 2026-08-27, filming marks moving RELATIVE TO EACH OTHER while
// scrolling — geometrically impossible for a linear map) the frame is fully VIRTUAL: one
// prefix-sum over ALL units, the cached measured height where a unit was ever rendered, the
// renderer's average else. The old frame was PIECEWISE — live getBoundingClientRect offsets
// inside the virtualization window, cache sums normalized to each spacer's rendered height
// outside it — so pure scrolling changed the map every frame the window slid: marks flipped
// basis crossing the window edge, the spacer denominators flapped, and first-render measurements
// reshuffled the slots mid-scroll. In the virtual frame a mark's position does not depend on
// scrollTop or the window AT ALL: under pure scrolling nothing moves, and a mark moves only when
// information arrives — a height first measured (or remeasured: an image sizing in, a fold
// toggling), events appending — the event-keyed remap the design rule asks for. The rendered
// window still feeds the cache every paint, so the frame converges to truth as units are seen;
// the scrollbar thumb lives in the real-pixel frame, whose totals the cache tracks to within the
// average-height estimate for never-rendered units — same accuracy class as the spacers that
// size that scrollbar in the first place. Returns null while the pane is hidden or degenerate;
// offsetOf returns null out of range — callers skip that mark, the next paint lands it.
function contentOffsetFrame(content: HTMLElement, v: View, s: Session):
    { sh: number; offsetOf: (i: number) => number | null } | null {
  const cRect = content.getBoundingClientRect();
  if (!content.scrollHeight || cRect.height <= 40) return null;
  const unitTotal = v.unitTotal ?? s.events.length;
  if (!(unitTotal > 0)) return null;
  // remember every rendered unit's measured height — the virtual frame feeds on them
  let uh = unitHeights.get(activeId!);
  if (!uh) { uh = new Map(); unitHeights.set(activeId!, uh); }
  for (const node of Array.from(v.el.querySelectorAll<HTMLElement>(".turn[data-unit]"))) {
    const u = Number(node.dataset.unit);
    const h = node.offsetHeight;
    if (Number.isFinite(u) && h > 0) uh.set(u, h);
  }
  const avg = v.avgTurnH ?? 60;
  // one prefix-sum pass per paint (O(n)), then O(1) per mark
  const pre: number[] = [0];
  let t = 0;
  for (let u = 0; u < unitTotal; u++) { t += uh.get(u) ?? avg; pre.push(t); }
  if (!(t > 0)) return null;
  const offsetOf = (i: number): number | null =>
    (i >= 0 && i < unitTotal) ? pre[i] + (pre[i + 1] - pre[i]) / 2 : null;   // the unit's slot MIDDLE, uniform for every unit
  return { sh: t, offsetOf };
}

function ensureScrollMarks(): HTMLElement {
  if (scrollMarks && scrollMarks.isConnected) return scrollMarks;
  scrollMarks = el("div", "scroll-marks");
  scrollMarks.style.display = "none";
  document.body.appendChild(scrollMarks);
  return scrollMarks;
}

function paintScrollMarks(): void {
  const box = ensureScrollMarks();
  const content = document.getElementById("content");
  const v = activeId ? views.get(activeId) : null;
  const s = activeId ? sessions.get(activeId) : null;
  if (!content || !v || !s || v.el.style.display === "none") { box.style.display = "none"; scrollMarksSig = ""; return; }
  const cRect = content.getBoundingClientRect();
  // The WHOLE loaded conversation gets notches, not just the rendered DOM window (the user
  // 2026-08-17, who scrolled back and watched the newer messages' marks vanish): the chat
  // virtualizes — a window of real turns between two spacers sized by the renderer's avg-height
  // estimate, and the scrollbar spans that estimated whole. contentOffsetFrame owns that mapping —
  // exactly as accurate as the scrollbar the user is reading the notches against, and shared with
  // the comment rail so the two mark kinds can never disagree about order.
  const frame = contentOffsetFrame(content, v, s);
  if (!frame) { box.style.display = "none"; scrollMarksSig = ""; return; }
  const sh = frame.sh;
  const offs: Array<{ top: number; m: string }> = [];
  const evUnit = eventUnitIndex(s);                       // marks anchor to EVENTS; the frame speaks UNITS
  for (let i = 0; i < s.events.length; i++) {
    const ev = s.events[i] as ChatEvent & { human?: boolean; romp?: boolean; rompAuto?: boolean; canned?: string; md?: string; tag?: string };
    if (ev.kind !== "user") continue;
    // the SAME classifier the bubble and rail dot read (sender-identity.ts, the user 2026-08-18:
    // "reuse the same functions that compute how they render in the chat, so there's never any
    // chance of desynchronization"). user → the blue that means yours; romp/tagged → a light gray
    // notch (machine-sent activity, visible on the map but never posing as your words); harness
    // noise ("injected") → no notch at all.
    const kind = senderKind(ev);
    if (kind === "injected") continue;
    const md = (ev.md || "").trim();
    // gestures are the user's DOINGS, not their words — no notch (the Continue row, a /command)
    if (!md || ev.canned === "continue" || SLASH_CMD_RE.test(md)) continue;
    const u = evUnit[i];
    if (u < 0) continue;                                  // not in the display stream (never for user events)
    const off = frame.offsetOf(u);
    if (off == null) continue;
    offs.push({ top: off, m: kind === "user" ? "" : "machine" });
  }
  const ys = offs.map((o) => ({ y: Math.round((o.top / sh) * (cRect.height - 4)), m: o.m }));
  const sig = activeId + "|" + Math.round(cRect.top) + "," + Math.round(cRect.right) + ","
    + Math.round(cRect.height) + "|" + ys.map((o) => o.y + (o.m ? "m" : "")).join(",");
  if (sig !== scrollMarksSig) {
    scrollMarksSig = sig;
    // UPDATE IN PLACE when the notch count is unchanged (the user 2026-08-17: scrolling back streams
    // older history in, the scroller's world grows, and every proportional position legitimately
    // compresses — the native thumb does the same — but rebuilt nodes TELEPORTED there). Moving the
    // existing nodes lets the CSS transition carry them, so a history load reads as the map
    // rescaling rather than notches jumping to wrong places. Count changes (new messages, a fresh
    // tab) still rebuild outright — those are new marks, not moved ones.
    const kids = Array.from(box.children) as HTMLElement[];
    if (kids.length === ys.length) {
      ys.forEach((o, i) => { kids[i].style.top = o.y + "px"; kids[i].className = "scroll-mark" + (o.m ? " " + o.m : ""); });
    } else {
      box.replaceChildren(...ys.map((o) => {
        const m = el("div", "scroll-mark" + (o.m ? " " + o.m : ""));
        m.style.top = o.y + "px";
        return m;
      }));
    }
    box.style.left = (cRect.right - 12) + "px";
    box.style.top = cRect.top + "px";
    box.style.height = cRect.height + "px";
  }
  box.style.display = ys.length ? "" : "none";
}

function paintRailSticky(): void {
  const stamp = ensureRailSticky();
  const content = document.getElementById("content");
  const v = activeId ? views.get(activeId) : null;
  if (!content || !v || v.el.style.display === "none") {
    stamp.style.display = "none";
    if (railDay) railDay.style.display = "none";
    return;
  }
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
  // DAY CONTEXT (the user 2026-08-17): whichever stamp owns the top slot also names its DAY, in a
  // small label riding just ABOVE it, whenever that day is not today — so scrolling through history
  // always says where you are even with the day divider off-screen. The anchor is the tracked turn's
  // marker when there is one, else the first stamp below the line ("the next one"); the label moves
  // WITH the leading stamp, and at handoff the sticky takes the very pixels the stamp and label held,
  // so there is no jump at the transition. The text swaps only at day boundaries.
  const day = ensureRailDay();
  const firstBelow = all.find(([, top]) => top >= line);
  const anchorM = marker || (firstBelow ? firstBelow[0] : null);
  const gRect = anyMarker ? anyMarker.getBoundingClientRect() : null;
  // ABOVE the stamp, not below (the user 2026-08-18, with a video): below it, the next incoming
  // stamp scrolled straight through the label's spot and the label had to leap over it — a visible
  // collision and jump at every handoff. Above it, nothing ever crosses the label's path. A leading
  // real stamp always has room for its label inside the pane: markers sit 10–20px below their
  // turn's top, and the label band (dayH + 1 ≈ 9.8px at the default 13px chat font) fits even the
  // smallest 10px offset — barely, so the tab-bar clamp below backstops it. The sticky alone rests
  // AT the line, so when a
  // label shows, the slot line drops by the label's height to make that room (the 2026-08-17 first
  // cut floated the label above the sticky without shifting it, and bled into the tab bar).
  const ep = anchorM ? Number(anchorM.dataset.epoch || 0) : 0;
  const label = ep && gRect ? dayContext(ep, Date.now()) : "";
  let dayW = 0, dayH = 0;
  if (label) {
    if (day.textContent !== label) day.textContent = label;
    day.style.display = "";                        // must be visible to measure
    const r = day.getBoundingClientRect(); dayW = r.width; dayH = r.height;
  } else day.style.display = "none";
  const slotLine = line + (label ? dayH + 1 : 0);
  const paintDay = (slotTop: number) => {
    if (!label) return;
    if (slotTop > cBottom) { day.style.display = "none"; return; }   // anchor is off the bottom
    // Natural width, right edge on the gutter's right edge (the stamp's own) — but never past the
    // pane's left edge: "2 days ago" at 0.68em is wider than the 47px gutter, and a box pinned to
    // the gutter clipped its leading digit at the pane edge (the user 2026-08-18, with a
    // screenshot). When it doesn't fit, the label slides right just enough to stay whole.
    // +1 right / -3 up from the geometric position (the user 2026-08-22): a hair of breathing room
    // between the label, the pane edge, and the stamp below it
    day.style.left = Math.max(3, gRect!.right - dayW + 1) + "px";
    day.style.top = Math.max(cTop + 1, slotTop - dayH - 4) + "px";
    day.style.display = "";
  };
  // The tracked turn's OWN stamp leads the top slot while it is at or below the slot line — it scrolls up
  // freely until it reaches it, and the instant it crosses ABOVE (markerTop < slotLine) the sticky takes the
  // same slot showing the same time, so the swap is invisible: no gap, no clipped sliver (the user 2026-07-23).
  // Deliberately keyed on the TRACKED turn's marker, not on any stamp anywhere: a LATER time change further
  // down the view is a different time, so it must not blank the top — that would leave the slot empty, which
  // is the whole thing the sticky exists to prevent.
  const realLeads = markerShown && markerTop >= slotLine;
  if (!hm || realLeads) {
    stamp.style.display = "none";
    for (const [m] of all) m.style.visibility = "";   // real stamp leads → nothing suppressed
    if (anyMarker) paintDay(realLeads ? markerTop : (firstBelow ? firstBelow[1] : cBottom + 1));
    else day.style.display = "none";
    return;
  }
  // The sticky leads: pin it at the slot line and hide every marker that has crossed ABOVE it — or INTO
  // its box: with the slot dropped for the day label, a not-yet-tracked turn's stamp (marker 10–20px below
  // a turn top that has not reached the line) can enter the sticky's own band while still "below the slot
  // line", and two HH:MM texts superimpose. The threshold is therefore the sticky's BOTTOM edge, so an
  // incoming stamp slides under the sticky hidden and re-emerges only when it leads. With no label this
  // changes nothing: slotLine === line, and a stamp inside [line, line+stampH) forces its own turn tracked
  // (offsets ≥ stamp height), which is realLeads — this branch never runs. Markers at or below the sticky's
  // bottom stay visible — they are the genuine lower stamps, not doubles.
  const g = (anyMarker || marker!).getBoundingClientRect();
  for (const [m, top] of all) m.style.visibility = top < slotLine + g.height ? "hidden" : "";
  stamp.textContent = hm;
  stamp.style.left = g.left + "px";
  stamp.style.width = g.width + "px";
  stamp.style.top = slotLine + "px";
  stamp.style.display = "";
  paintDay(slotLine);
}

// Scroll is the sticky stamp's primary driver, and a re-render moves the geometry under it — both funnel
// here, rAF-coalesced so a fast flick and a busy tail each paint once per frame. (This used to be two
// wrappers: one re-ran the spacing pass on re-render, one repainted the sticky on scroll. With the spacing
// pass gone there is only one thing left to do, so there is only one scheduler.)
let railStickyPending = false;
function scheduleRailSticky(): void {
  if (railStickyPending) return;
  railStickyPending = true;
  requestAnimationFrame(() => { railStickyPending = false; paintRailSticky(); paintScrollMarks(); updateCommentRail(); });
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
    // ONE classifier (sender-identity.ts, the user 2026-08-18): the bubble dress here, the rail
    // dot below, and paintScrollMarks' notch color all read the SAME senderKind verdict, so the
    // surfaces can never desynchronize. The sender-declared render hint (kernel MSG_TAG_RE lift,
    // the user 2026-08-15) classifies as "tagged": machine-sent on the user's behalf, shedding the
    // typed-words blue for the gray family under the SENDER's own ⚙ label.
    const kind = senderKind(ev);
    const romp = kind === "romp";
    const injected = kind === "injected";
    const tagged = kind === "tagged";
    const turn = el("div", "turn turn-user" + (romp ? " romp" : injected ? " injected" : ""));
    // Unresolved postal ids ride the raw turn so a timeline message arc can still land on it. Without
    // this the arc pointed at a turn with nothing to match and the click died silently (the user
    // 2026-07-23). A hydrated card sets data-mid in renderPostalService instead.
    if (ev.mid) turn.dataset.mid = ev.mid;
    if (ev.mids && ev.mids.length) turn.dataset.mids = ev.mids.join(" ");
    // Prompts ride the rail like every other turn: their own dot + a left-gutter HH:MM marker (added in
    // renderEvent). Genuine prompts get the solid blue dot; a romp injection a gray dot; harness notes the
    // hollow ring used by assistant turns.
    turn.appendChild(dot(romp ? "romp" : tagged ? "tag" : injected ? "ring" : "user"));
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
      if (tagged) {
        const tchip = el("div", "romp-tag");
        tchip.appendChild(document.createTextNode("⚙ " + ev.tag));
        turn.appendChild(tchip);
      }
      const bubble = el("div", (romp ? "romp-bubble" : tagged ? "romp-bubble tag-bubble" : injected ? "user-note" : "user-bubble") + " md");
      // A slash COMMAND you sent reads as a special keyword, not prose (the user 2026-06-29): render the leading
      // "/cmd" token as a monospace chip. Genuine human bubbles only (a romp/injected note is never a command).
      // paths this turn already renders as full in-bubble images (both the caption path and a
      // "path:"-sourced src) — the linkifier must not ALSO thumb them, or the picture shows twice
      const imgPaths = (ev.images || [])
        .flatMap((im) => [im.path, im.src.startsWith("path:") ? im.src.slice(5) : ""])
        .filter((p): p is string => !!p);
      if (!romp && !injected && !tagged && ev.md && renderSlashCmd(bubble, ev.md)) {
        // a COMMAND is a user GESTURE, not a user message (the user 2026-08-13): it changes something
        // rather than saying something, so it sheds the blue said-thing bubble and reads in the
        // system-event family (the ✦ dividers) — a dim left-aligned row: ✦ mark, mono chip, args
        turn.classList.add("turn-cmd");
        bubble.classList.add("cmd-row");
      } else if (!romp && !injected && !tagged && ev.md && ev.canned === "continue") {
        // The card's Continue button: a user GESTURE, not typed prose. Third cut (the user
        // 2026-08-17, superseding the 2026-08-15 slash-command dress — its ✦ mark said nothing, and
        // the boxed chip + trailing caret made a second gesture grammar next to ↩ Follow-up's):
        // Continue now wears the SAME grammar as the Follow-up header — caret, "→ Continue" in the
        // accent, the SENT text's own first line dimmed beside it, the rest one click deeper, so
        // expanding only ever reveals MORE of the same words. Uniqueness rides the word + the →
        // glyph exactly as Follow-up's rides ↩. The judges still file it as your reply, and the ↩
        // follow-up header above still names the goal it answers.
        bubble.classList.add("cont-row");
        const raw = ev.md.replace(/<!--[\s\S]*?-->/g, "").trim();
        const lines = raw.split("\n").map((l) => l.trim());
        const first = lines.find((l) => l && !l.startsWith(">")) || lines.find((l) => l) || raw;   // skip a goal-context "> …" quote
        const clipped = first.length > 90 ? first.slice(0, 88).replace(/\s+\S*$/, "") + "…" : first;
        const head = el("div", "followup-tag cont-tag");
        const tri = el("span", "followup-tri cont-tri");         // glyph via CSS, so expansion flips it
        const lbl = el("span", "followup-lbl"); lbl.textContent = "→ Continue";
        const g = el("span", "followup-goal"); g.textContent = clipped;
        head.append(tri, lbl, g);
        bubble.appendChild(head);
        const full = el("div", "nudge-full md");
        full.innerHTML = md(ev.md);
        bubble.appendChild(full);
        bubble.classList.add("nudge-collapsible");
        bubble.dataset.act = "nudgetoggle";   // the stable body delegate, never a per-render listener (CLAUDE.md)
        const ckey = ev.uuid ? "cont:" + ev.uuid : undefined;
        if (ckey) bubble.dataset.nkey = ckey;
        applyFold(bubble, "expanded", ckey);
        bubble.title = bubble.classList.contains("expanded") ? "click to collapse" : "click to expand";
      } else if (tagged) {
        // long templates fold like nudges: the gist is the message's OWN first non-quote line —
        // never a paraphrase — with the full text one click deeper, keyed to survive re-renders
        const raw = ev.md.replace(/<!--[\s\S]*?-->/g, "").trim();
        const lines = raw.split("\n").map((l) => l.trim());
        const first = lines.find((l) => l && !l.startsWith(">")) || lines.find((l) => l) || raw;
        const gist = first.length > 90 ? first.slice(0, 88).replace(/\s+\S*$/, "") + "…" : first;
        const more = collapseWs(raw) !== collapseWs(gist);
        const gistEl = el("div", "nudge-gist");
        if (more) { const c = el("span", "nudge-caret"); c.textContent = "▸"; gistEl.appendChild(c); }
        gistEl.appendChild(document.createTextNode(gist));
        bubble.appendChild(gistEl);
        if (more) {
          const full = el("div", "nudge-full md");
          full.innerHTML = md(ev.md);
          bubble.appendChild(full);
          bubble.classList.add("nudge-collapsible");
          bubble.dataset.act = "nudgetoggle";   // the stable body delegate, never a per-render listener
          const tkey = ev.uuid ? "tag:" + ev.uuid : undefined;
          if (tkey) bubble.dataset.nkey = tkey;
          applyFold(bubble, "expanded", tkey);
          bubble.title = bubble.classList.contains("expanded") ? "click to collapse" : "click to expand";
        }
      } else if (romp && ev.md) {
        // A romp-injected NUDGE (auto status-check, Nudge button, injected follow-up) is mechanical
        // bookkeeping — progressive disclosure (the user 2026-07-17): default is a ONE-LINE gist with a
        // caret; click the bubble for the full text. Keyed, so an expanded nudge survives re-renders.
        // The gist SAYS WHAT ROMP DID, not the message's first line (the user 2026-07-17 ×2: a follow-up
        // opens with the goal-context "> …" quote, so the text gist read as the user's own words — pure
        // confusion). Known flavors get a semantic label; the text fallback skips quoted "> " lines.
        // display-only strip of the literal "[romp] " source prefix (T130, the user 2026-08-27: a
        // nudge says romp above it, but a mechanics notice ALSO printed "[romp]" inside the bubble —
        // two shapes for one sender). The prefix exists for the AGENT (the housekeeping-note design:
        // it tells a model that has never heard of the dashboard where the message came from) and
        // stays in the transcript record untouched; the romp attribution mark above the bubble
        // already says the same thing to the READER, so showing both was double-labelling.
        const raw = ev.md.replace(/<!--[\s\S]*?-->/g, "").replace(/^\s*\[romp\]\s*/, "").trim();
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
          linkifyFileUris(full, imgPaths, ev.spacePaths, ev.pathLinks, ev.pathPins);
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
        linkifyFileUris(bubble, imgPaths, ev.spacePaths, ev.pathLinks, ev.pathPins);   // bare file:// URLs in a message → clickable (open in the host's default app)
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
        // covers BOTH dropped-echo populations (the copy predated the 2026-08-26 widening and claimed a
        // process death for every loss): an SDK echo really is a send its CLI died holding, but a tmux
        // echo settles dropped when the session simply moved past it — a keystroke the pane dropped, or
        // a delivery the transcript recorded under different text. Say what is KNOWN (it never made the
        // conversation), not a cause that is only sometimes true.
        note.title = "This message never made it into the conversation: the session moved on without recording it (a dropped keystroke, a process that died holding it, or a delivery recorded under different text).";
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
        // (The FORK affordance no longer rides this row: forking conceptually cuts BELOW the previous
        // response, so its button lives there now — applyForkSpots — while the rewind family above
        // stays here, acting on THIS message. The user 2026-08-19.)
        const acts = el("div", "msg-acts");   // one row under the bubble (the turn is a column flex)
        acts.appendChild(edit);
        acts.appendChild(del);
        acts.appendChild(rf);
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
    linkifyFileUris(body, undefined, ev.spacePaths, ev.pathLinks, ev.pathPins);   // bare file:// URLs + verified spaced filenames → clickable
    turn.appendChild(body);
    return turn;
  }
  if (ev.kind === "thinking") {
    const turn = el("div", "turn turn-thinking");
    turn.appendChild(dot("ring"));
    // Opaque = signed AND textless: only then is "Thinking…" all there is to show. A block with a
    // signature and text is a reasoning SUMMARY (the kernel asks for them when its thinking-summaries
    // toggle is on) and renders like any thinking text. The kernel's flag means opaque already; the
    // text is re-checked here so a bundle fed by an older kernel (flag = signed) still shows what it
    // was handed (2026-09-01 — before this, every summary hid behind the placeholder).
    const opaque = ev.encrypted && !(ev.text || "").trim();
    const t = el("div", "thinking" + (opaque ? " encrypted" : ""));
    t.textContent = opaque ? "Thinking…" : ev.text;
    if (opaque) { turn.appendChild(t); return turn; }   // already a one-liner
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
  if (ev.kind === "branch") {
    // the branch divider: everything above is history shared with the parent session; the label
    // deep-links to the parent AT the branch point (data-uuid there is the cut record itself)
    const turn = el("div", "turn turn-branchmark");
    const line = el("div", "branch-divider");
    const label = el("button", "branch-label") as HTMLButtonElement;
    label.type = "button";
    label.dataset.act = "branchjump";
    if (ev.fromSid) label.dataset.sid = ev.fromSid;
    if (ev.cut) label.dataset.cut = ev.cut;
    label.textContent = "Branched from " + (ev.fromName || "another session")
      + " · the conversation above is shared";
    label.title = "Open " + (ev.fromName || "the parent session") + " at the branch point";
    line.appendChild(label);
    turn.appendChild(line);
    return turn;
  }
  if (ev.kind === "reconnecting") return renderReconnecting(ev);
  if (ev.kind === "retrying") return renderRetrying(ev);
  if (ev.kind === "retried") return renderRetried(ev);
  if (ev.kind === "retryGaveUp") return renderRetryGaveUp(ev);
  if (ev.kind === "apiErrorNote") return renderApiErrorNote(ev);
  if (ev.kind === "effortApplied") return renderEffortApplied(ev);
  if (ev.kind === "cmdGesture") return renderCmdGesture(ev);
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
// Make `elem` act on the folder `cwd` on click (the user 2026-06-27; rerouted 2026-08-14): used
// EVERYWHERE a folder location is shown (statusline, the System-context Directory row, …) — on the web it
// BROWSES the folder in the dashboard, in VS Code it keeps the configured opener. Click-safe — the
// action rides a data-act caught by a document-level delegate (browseFiles/openFolder), so it works under
// any re-rendering surface without per-node handlers. `sid` (the owning session's, possibly host-prefixed, id — the user
// 2026-07-03) rides along as data-id: for a REMOTE session this is how the kernel knows to SSH out instead
// of treating a remote path as local (a silent no-op, since that path doesn't exist here). This pane stays
// host-BLIND as designed (see federation.ts) — sid is just echoed back opaquely, never parsed here.
function asFolderLink(elem: HTMLElement, cwd: string, sid?: string): void {
  if (!cwd) return;
  // On the web a click BROWSES the folder in the dashboard (the user 2026-08-14) — the affordance
  // that works from every device, where OS-open acted on the KERNEL's machine (the wrong-machine
  // class the 📎 picker and file links were cured of). OS-open survives on
  // the row's right-click menu for the genuinely-local case (the contextmenu delegate below). In
  // VS Code the browser overlay doesn't exist, so the click keeps opening the folder host-side.
  const web = location.protocol === "http:" || location.protocol === "https:";
  elem.dataset.act = web ? "browseFiles" : "openFolder";   // pane-local browse needs no shell (2026-08-24)
  elem.dataset.cwd = cwd;
  if (sid) elem.dataset.id = sid;
  elem.classList.add("folder-link");
  elem.title = cwd + (elem.dataset.act === "browseFiles"
    ? "  ·  click to browse this folder" : "  ·  click to open this folder");
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
  (ev.claudemd || []).forEach((doc, i) => {
    const sec = el("div", "sys-doc");
    const dh = el("div", "sys-doc-head");
    const scope = el("span", "sys-doc-scope " + (doc.scope === "global" ? "global" : "project"));
    scope.textContent = doc.scope === "global" ? "global" : "project";
    const pth = el("span", "sys-doc-path"); pth.textContent = doc.path;
    dh.appendChild(scope); dh.appendChild(pth);
    sec.appendChild(dh);
    // raw text in a bordered, scrollable sub-box (.fold-pre) — scroll position keyed per doc so a
    // reader's place survives the per-push rebuild of this turn (keepScroll)
    sec.appendChild(preEl(doc.text, key ? key + ":doc" + i : undefined));
    body.appendChild(sec);
  });
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
// NOTE: effectively unreachable — the kernel always attaches askAnswer to AskUserQuestion events, filled
// from the record's structured answers map (authoritative; this scrape garbles quote-bearing questions).
// Kept as the belt-and-suspenders raw parse only; don't extend it.
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
  const row = (t: (typeof ev.tasks)[number]) => {
    const r = el("div", "todo-item todo-" + t.status);
    const mark = el("span", "todo-mark");
    mark.textContent = t.status === "completed" ? "✓" : t.status === "in_progress" ? "◐" : "○";
    r.appendChild(mark);
    const txt = el("span", "todo-text");
    txt.textContent = t.status === "in_progress" && t.activeForm ? t.activeForm : t.subject;
    r.appendChild(txt);
    return r;
  };
  // PROGRESSIVE DISCLOSURE (the user 2026-08-24): a continuously-dispatched session — a manager
  // especially — accumulates dozens of finished tasks and the card showed them all. Default = the
  // ON-DECK work (in_progress + pending); the finished bulk (completed — and cancelled/deleted,
  // should the store carry them: anything not on deck) folds into one row, "+N more completed",
  // click to expand and click again to re-fold, nothing lost. The fold sits WHERE the bulk lives
  // (finished work precedes current work in the list), keyed per session so the state survives the
  // per-push re-renders (the openFolds idiom); a list with ≤ 2 finished rows stays inline — a fold
  // hiding two rows costs a click for nothing. The label flip + rows appearing ARE the click's
  // acknowledgement, immediate and local (no round-trip).
  const onDeck = ev.tasks.filter((t) => t.status === "in_progress" || t.status === "pending");
  const finished = ev.tasks.filter((t) => t.status !== "in_progress" && t.status !== "pending");
  if (finished.length >= 3) {
    const foldKey = "todo-done:" + (renderingSid || "");
    const doneBox = el("div", "todo-done");
    for (const t of finished) doneBox.appendChild(row(t));
    applyFold(doneBox, "todo-open", foldKey);
    const tog = el("button", "todo-fold") as HTMLButtonElement;
    tog.type = "button";
    const label = () => {
      const open = doneBox.classList.contains("todo-open");
      tog.textContent = open ? `hide ${finished.length} completed` : `+ ${finished.length} more completed`;
      tog.title = open ? "collapse the completed items" : "show the completed items";
    };
    label();
    tog.addEventListener("click", (e) => {
      e.stopPropagation();
      rememberFold(doneBox, "todo-open", foldKey);
      label();
    });
    card.appendChild(tog);
    card.appendChild(doneBox);
  } else for (const t of finished) card.appendChild(row(t));
  for (const t of onDeck) card.appendChild(row(t));
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
  // This fill runs in the MESSAGE handler, outside any syncView — renderingSid/renderingOwnerSid still
  // hold whatever was built last. Pin both to the episode's own session so fold keys and file/preview
  // URLs belong to it, then restore.
  const savedKey = renderingSid;
  const savedOwner = renderingOwnerSid;
  renderingSid = sid;
  renderingOwnerSid = sid;
  try {
    document.querySelectorAll<HTMLElement>(".clear-body").forEach((b) => {
      if (b.dataset.clearKey === key) fillClearBody(b, got);
    });
  } finally {
    renderingSid = savedKey;
    renderingOwnerSid = savedOwner;
  }
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

// The durable COMMAND-GESTURE row (the user 2026-08-14): wears the SAME dress as a live command turn —
// turn-user's flex-end puts it on the user's side, ✦ + the mono chip via the shared renderSlashCmd — so the
// moment prune_live retires the synthesized chip and this interleaved event takes over is invisible. No
// edit/delete/fork affordances on purpose: the uuid is synthetic ("cmdg:<t>"), not a rewindable transcript
// atom, and a gesture is not a message to edit.
function renderCmdGesture(ev: Extract<ChatEvent, { kind: "cmdGesture" }>): HTMLElement {
  const turn = el("div", "turn turn-user turn-cmd");
  turn.appendChild(dot("user"));
  const bubble = el("div", "user-bubble md cmd-row");
  if (!renderSlashCmd(bubble, ev.cmd)) bubble.textContent = ev.cmd;
  turn.appendChild(bubble);
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
  if (!label) return;
  if (label.dataset.bare === "1") {                       // the pre-confirmation group recounts in its own words
    label.textContent = bubbles.length === 1 ? "sending…" : `sending ${bubbles.length}…`;
    return;
  }
  const nCmd = bubbles.filter((b) => b.querySelector(".slash-cmd-chip")).length;
  label.textContent = queuedCountText(bubbles.length, nCmd) + (label.dataset.why || "");
}

function renderQueued(ev: Extract<ChatEvent, { kind: "queued" }>): HTMLElement {
  const turn = el("div", "turn turn-queued");
  // A BARE group is romp's own optimistic echo with nothing else known-queued: "N queued messages" is a
  // claim we can't back for a send the session hasn't confirmed (the user 2026-07-16) — so it wears its
  // OWN honest label instead: "sending…" states exactly what is known, the press happened and nothing is
  // confirmed yet (the user 2026-08-30, who sat in that stage with no state label and no way to cut the
  // message). Merged into a real queued group, the standard header returns and counts ours in — there the
  // queueing IS established, so assuming this one joins it is honest.
  if (ev.bare) {
    const head = el("div", "queued-head");
    head.appendChild(hourglassIcon());
    const label = el("span", "queued-count");
    label.dataset.bare = "1";     // reflow rewrites this label in its own vocabulary, never "N queued"
    label.textContent = ev.texts.length === 1 ? "sending…" : `sending ${ev.texts.length}…`;
    label.title = "on its way to the session — cancellable until the session takes it";
    head.appendChild(label);
    turn.appendChild(head);
  }
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
    // An optimistic echo's dragged images render as THUMBNAILS, not just their trailing paths (the
    // user 2026-08-25: composer preview → path-only provisional → thumbnail landing flashed). Same
    // machinery end to end: userImage with the landed form's exact "path:" shape — buildPathImg's
    // (sid,path)-keyed cache then serves the LANDED bubble the same bytes, so the reconcile swap
    // never re-fetches or flickers. pathInText: the send appends the paths to the text above.
    if (t.imgPaths && t.imgPaths.length) {
      for (const ip of t.imgPaths) bubble.appendChild(userImage({ src: "path:" + ip, path: ip }, true));
    }
    // CANCELABLE — an explicit ✕ on the bubble (the user 2026-07-08; the old whole-bubble click was
    // undiscoverable AND hung on a node every push rebuilds, so mid-press rebuilds silently ate the
    // click). The ✕ carries data-act="qx" → the ONE document.body delegate (click-safe per CLAUDE.md);
    // a MESSAGE returns to the composer to re-edit, a slash COMMAND just cancels. Covers both queues:
    // the backend's own (idx; SDK only — tmux's queue lives inside Claude Code, no recall) and ops
    // PARKED during compaction/model switches (park; romp-owned on every backend).
    if (t.cancelable && (t.idx !== undefined || t.park !== undefined || t.optimistic)) {
      const x = el("button", "queued-x");
      x.textContent = "✕";
      x.title = isCmd ? "cancel this queued command" : "cancel this queued message and move it back to the composer";
      x.dataset.act = "qx";
      if (t.idx !== undefined) x.dataset.qidx = String(t.idx);
      if (t.park !== undefined) x.dataset.qpark = String(t.park);
      if (t.optimistic) x.dataset.qopt = "1";   // ✕ before confirmation → cancel-by-body (no park/idx yet)
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

// The refusal card's remedy line, ONE string: renderApiError's initial write and apiRetryTick's
// per-second re-assert both read it, so the card and the tick can never drift into different words.
const REFUSAL_REMEDY = "the model's safeguards refused this prompt — rewrite it or drop this thread";

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
  // A safeguards REFUSAL too (the user 2026-08-15): a refusal is deterministic on the same input — a
  // retry re-sends the same prompt and manufactures the same refusal — so no button; the line below
  // names the real fix (rewrite the ask, or drop this thread).
  const refusal = !!st?.apiRefusal;
  const spendCap = !!st?.apiSpendLimit || !!st?.apiModelLimit || !!st?.apiAuthErr || refusal;
  if (!spendCap) {
    const retry = el("button", "apierror-retry") as HTMLButtonElement;
    retry.textContent = "Retry now";
    retry.title = "send “retry” into this session right now (also resets the auto-retry countdown)";
    retry.addEventListener("click", () => {
      // manual:true → an explicit override that fires even when auto-retry is paused/suppressed for this thread
      // (the kernel gate is for the auto-loop only); without it "Retry now" was a dead no-op on a suppressed
      // session (the user 2026-07-06). Acknowledge the click AT ONCE — disable + "Retrying…" — so it never
      // reads as unresponsive; the next render (a fresh error card, or the turn resuming) restores it.
      const own = owningSidOf(retry);
      if (vscodeApi) vscodeApi.postMessage({ type: "apiRetry", id: own, manual: true });
      if (own) apiRetryNext.set(own, Date.now() + API_RETRY_MS);   // restart the countdown
      retry.disabled = true;
      retry.textContent = "Retrying…";
      setTimeout(() => { if (retry.isConnected) { retry.disabled = false; retry.textContent = "Retry now"; } }, 2500);
    });
    head.appendChild(retry);
  } else if (st?.backend === "tmux" && !refusal) {
    // (a refusal parks no menu — the Esc-sender below is for the CLI's spend-limit dialog only)
    const dismiss = el("button", "apierror-retry") as HTMLButtonElement;   // same button chrome, different verb
    dismiss.textContent = "Dismiss dialog";
    dismiss.title = "the terminal is showing the spend-limit menu — send Esc to close it (cancels; changes no billing setting)";
    dismiss.addEventListener("click", () => {
      if (vscodeApi) vscodeApi.postMessage({ type: "dismissDialog", id: owningSidOf(dismiss) });
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
  if (refusal) countdown.textContent = REFUSAL_REMEDY;   // never "retrying soon…": a refusal is deterministic, and the tick RE-ASSERTS this line every second
  else if (spendCap) countdown.textContent = "spend limit reached — raise it at claude.ai/settings/usage";   // never "retrying soon…": the tick skips spend-capped threads
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
    // A safeguards REFUSAL (apiRefusal) is out too (the user 2026-08-15): a refusal is deterministic on
    // the same input, so every retry re-sends the same prompt and manufactures the same refusal.
    sessions.forEach((s, id) => { if (s.status.state === "blocked" && !s.status.retrySuppressed && !s.status.apiSpendLimit && !s.status.apiModelLimit && !s.status.apiAuthErr && !s.status.apiRefusal) blocked.add(id); });
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
    if (active?.status.apiRefusal) {
      // A refusal is never auto-retried, so there is no countdown to show — hold the remedy line.
      // FIRST in the ladder: the global pause is about auto-retry, which a refusal never gets. Writing
      // (not skipping) each tick also heals the Stop/Resume-all handler's blanket countdown rewrite
      // within a second, without that handler needing per-session context.
      cd.textContent = REFUSAL_REMEDY;
    } else if (globalRetryPaused) {
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
    if (ev.output) inlineFold(head, turn, `${countLines(ev.output)} lines`, preEl(ev.output, fkey && fkey + ":out"), fkey);
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
      inlineFold(head, turn, `${countLines(ev.output)} line${countLines(ev.output) === 1 ? "" : "s"}`, preEl(ev.output, fkey && fkey + ":out"), fkey);
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
  if ((ev.name === "Task" || ev.name === "Agent") && ev.agentId) {
    // Subagent transcripts (plans/subagent-transcripts.md): the arrow opens the agent's WHOLE
    // conversation as a peek tab (level 1); while the agent runs, the preview under the head shows
    // its last few tool calls (level 0). Both live inside this turn, so a collapsed compact run hides
    // them with the head and an expanded run shows them.
    head.appendChild(agentOpenButton(ev.agentId, ev.uuid || null, renderingOwnerSid || renderingSid || null));
    if (ev.agentGist) head.insertAdjacentElement("afterend", renderAgentGist(ev.agentGist));
  }
  return turn;
}

// The house line-icon button that opens a subagent's transcript. Delegated (data-act on the stable
// document.body delegate below), never a per-node listener: the tool head rebuilds on every push.
function agentOpenButton(agentId: string, anchorUuid: string | null, ownerSid: string | null): HTMLElement {
  const b = el("span", "tool-open-agent");
  b.dataset.act = "openSubagent";
  b.dataset.agent = agentId;
  if (ownerSid) b.dataset.sid = ownerSid;
  if (anchorUuid) b.dataset.uuid = anchorUuid;
  b.innerHTML = openIconSvg();
  b.setAttribute("role", "button"); b.tabIndex = 0;
  setTip(b, "open transcript");
  return b;
}

// The running agent's preview: up to three dim rows in the head vocabulary (`<tool> <desc>`), newest
// last, the last row trailing "· N tool calls · elapsed". Wears the tool-fold-toggle's size (0.86em) —
// no new font-size on the tool head. Gone once the kernel stops shipping the gist (the agent finished).
function renderAgentGist(g: AgentGist): HTMLElement {
  const box = el("div", "agent-gist");
  for (const line of gistLines(g, Date.now())) {
    const row = el("div", "agent-gist-row");
    const t = el("span", "agent-gist-tool"); t.textContent = line.tool; row.appendChild(t);
    if (line.desc) { const d = el("span", "agent-gist-desc"); d.textContent = line.desc; row.appendChild(d); }
    if (line.meta) { const m = el("span", "agent-gist-meta"); m.textContent = line.meta; row.appendChild(m); }
    box.appendChild(row);
  }
  return box;
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
const CLASSIC_FADE_SCALE = 0.9;   // T118 (the user 2026-08-27): +10% brighter faded tab labels under Classic — one tunable knob (half the parked T113 number)
function fadedColor(hex: string): string {
  const m = /^#?([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const [br, bgc, bb] = bgRgb();
  const lum = (x: number, y: number, z: number) => 0.2126 * x + 0.7152 * y + 0.0722 * z;
  const Lc = lum(r, g, b), Lb = lum(br, bgc, bb), Lt = Lb + 38;
  if (Lc <= Lt) return hex; // already dim — leave it
  // Classic fades 10% less far toward the background (T118); Yatharth keeps his full fade.
  const scale = settings.chatTabTheme === "yatharth" ? 1 : CLASSIC_FADE_SCALE;
  const t = Math.min(0.85, (Lc - Lt) / (Lc - Lb)) * scale;
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
// `report` is the frame's provenance under federation (federation.ts emitMergedOrder): `reemit` marks a
// synthetic re-emission served from the manager's STORED per-host slices (a view-order storage event, a
// host attach or drop), `freshHost` names the one host whose own push drove a fresh emission. A frame the
// kernel sent directly (VS Code, a single-kernel page) carries neither.
type OrderReport = { reemit?: boolean; freshHost?: string } | undefined;
function ackClosingTabs(kernelOrder: readonly string[], report?: OrderReport): void {
  if (!closingTabs.size) return;
  const live = new Set(kernelOrder);
  const now = Date.now();
  for (const [id, ts] of Array.from(closingTabs)) {
    if (!live.has(id)) { closingTabs.delete(id); continue; }       // the kernel dropped it → confirmed
    if (now - ts < CLOSE_ACK_MS) continue;                         // still in flight; the shutdown runs behind us
    // Past the backstop, only a FRESH report from the OWNING kernel may call the close refused (T233, the
    // user 2026-09-03: a session the kernel had killed within the same second toasted "Couldn't close"
    // because a federation re-emit re-served a stored slice still carrying the id 15s later). A re-emit
    // is never new evidence, and another host's push says nothing about this id's kernel — both keep the
    // suppression and wait for the owner's own word; the backstop stays the honest path for a close that
    // genuinely did not take.
    if (report && (report.reemit || (typeof report.freshHost === "string" && hostOf(id) !== report.freshHost))) continue;
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
function applyTabOrder(o: any, tabs?: any, report?: OrderReport) {
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
    // …and apply the same blob to EXISTING sessions (the user 2026-08-24): the label/color used to
    // stick until a per-session frame happened to rebuild — which, after a headless POST
    // /rename | /color, is never for a background tab (the chat build cache's sig watches
    // transcript+states only, deliberately). The recurring push is the authoritative carrier, so
    // ANY writer — CLI POST, WS op, another dashboard, a remote kernel — renders here within a
    // cycle (see tab-meta.ts); the renderTabs() below repaints with it.
    syncSessionsFromTabMeta(tabs, (id) => sessions.get(id), pendingTabMeta);
  }
  // Adopt the kernel order verbatim, keeping any just-arrived tab the push doesn't carry yet (see tab-order.ts).
  const kernelOrder = Array.isArray(o) ? o.filter((x: any) => typeof x === "string") : [];
  ackClosingTabs(kernelOrder, report);
  // A kernel-owned tab the push no longer carries gets the SAME teardown the `closed` event runs — the
  // session map, its view, drafts and the active-tab reselect all go, not just the strip entry. Under
  // federation the merged order only omits an id when its OWNING host affirmatively reported it gone
  // (per-host slices persist across down/detached hosts), so this never fires on a tunnel blip.
  const inKernel = new Set<string>(kernelOrder);
  const omitted = new Set(order.filter((id) => kernelListed.has(id) && !inKernel.has(id)));   // all of them first: no fallback onto one going in the same breath
  for (const id of order.slice()) {
    if (omitted.has(id)) dismissSession(id, "omitted", omitted);
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
let tabDragCommitted = false;   // set by the strip's drop handler; dragend without it = a cancel (Escape / dropped outside)
// The drag hit-test's stable inputs (the drag-flap fix, 2026-08-28): every tab's outer width and
// the strip's geometry, measured ONCE at dragstart. The pointer is then hit-tested against a
// VIRTUAL wrap layout of the non-dragged tabs (dragslot.ts) — the live rects shift when the
// provisional tab inserts (wrap cascade included), which is exactly the feedback loop that made
// the slot oscillate between distant positions in the user's recording.
let dragGeom: { widths: Map<string, number>; containerW: number; gapX: number; rowH: number } | null = null;
function snapshotDragGeometry(sample: HTMLElement): void {
  const bar = document.getElementById("tabs");
  if (!bar) { dragGeom = null; return; }
  const widths = new Map<string, number>();
  bar.querySelectorAll<HTMLElement>(".tab[data-id]").forEach((t) => { if (t.dataset.id) widths.set(t.dataset.id, t.getBoundingClientRect().width); });
  const cs = getComputedStyle(bar);
  dragGeom = { widths, containerW: bar.clientWidth,
               gapX: parseFloat(cs.columnGap || "0") || 0,
               rowH: sample.getBoundingClientRect().height || 1 };
}
let dragBlankEl: HTMLElement | null = null;
function dragImageBlank(): HTMLElement {
  if (!dragBlankEl || !dragBlankEl.isConnected) {
    dragBlankEl = el("div", "");
    dragBlankEl.style.cssText = "position:fixed;top:-10px;left:-10px;width:1px;height:1px;opacity:0;pointer-events:none";
    document.body.appendChild(dragBlankEl);
  }
  return dragBlankEl;
}
// FLIP the strip around a DOM mutation (T127 live reorder): snapshot every tab's rect by id, run
// the mutation, then play each moved tab from its old rect to its new one — an inverted transform
// released a frame later. A row jump is just a bigger delta: the wrap layout reflows and the
// animation follows, which is what makes true cross-row live reorder work. Under
// prefers-reduced-motion the mutation still happens (the reorder IS the information, per the
// spec) — only the transition is skipped, so positions update instantly. Duration is
// presentation, not logic: nothing waits on it.
function flipTabs(mutate: () => void): void {
  const bar = document.getElementById("tabs");
  if (!bar) { mutate(); return; }
  const before = new Map<string, DOMRect>();
  bar.querySelectorAll<HTMLElement>(".tab[data-id]").forEach((t) => { if (t.dataset.id) before.set(t.dataset.id, t.getBoundingClientRect()); });
  mutate();
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  bar.querySelectorAll<HTMLElement>(".tab[data-id]").forEach((t) => {
    const a = t.dataset.id ? before.get(t.dataset.id) : undefined;
    if (!a) return;
    const b = t.getBoundingClientRect();
    const dx = a.left - b.left, dy = a.top - b.top;
    if (!dx && !dy) return;
    t.style.transition = "none";
    t.style.transform = `translate(${dx}px, ${dy}px)`;
    requestAnimationFrame(() => {
      t.style.transition = "transform 0.12s ease";
      t.style.transform = "";
      const done = () => { t.style.transition = ""; t.removeEventListener("transitionend", done); };
      t.addEventListener("transitionend", done);
    });
  });
}
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
  if (draggedId) return;   // a drag owns the strip — the info popover pinned open through the whole gesture, occluding the row below (the user's recording, 2026-08-28)
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
  if (be === "sdk" || be === "tmux" || be === "codex") rows.push(["Backend", be === "sdk" ? "SDK" : be === "codex" ? "Codex" : "tmux"]);
  // Billing: whether this tab bills the API key or the Claude login — and WHICH login account (the
  // user 2026-08-09: shown whenever the backend reports it, one-auth machines included; only a tmux
  // session, whose CLI env romp does not control, reports nothing). No key material, ever.
  // When the CLI's own init landed on the OTHER side (authLive — say, a key found via apiKeyHelper
  // on a session launched for the login), the row carries the live truth beside the intent instead
  // of wearing the lie (the user 2026-08-15). The account name yields its parenthetical then: it is
  // not the account being billed.
  // the row tells the TRUTH in every landing shape (T124: after a switch it showed the pick as
  // applied fact through the whole reconnect window, and a wrong-side landing read as a quiet
  // parenthetical): a pending pick says so, a confirmed contradiction leads with the warning.
  if (s.status.auth) rows.push(["Billing",
    s.status.authPending
      ? (s.status.auth === "key" ? "API key" : "Login") + " (applying — not confirmed yet)"
      : s.status.authLive && s.status.authLive !== s.status.auth
        ? `⚠ ${s.status.auth === "key" ? "API key" : "Login"} picked, but the CLI reports `
          + `${s.status.authLive === "key" ? "the API key" : "the login"} — this session bills that`
        : s.status.auth === "key" ? "API key"
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
    const bar = ctxBar(); setCtxBar(bar, s.status.ctx, s.status.state === "compacting", pickTone(s.status.ctxColor, s.status.ctxTone), s.status.ctxOver);
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
// Release the press-hold and flush any deferred rebuild. Hoisted so the DRAG handlers can call it
// too: a native drag swallows the pointerup, so without this a finished drag would leave the strip
// frozen against pushes until the next unrelated press (see the dragend handler).
function releaseTabStrip(): void {
  if (!tabPointerHeld) return;
  tabPointerHeld = false;
  if (renderPendingWhilePressed) { renderPendingWhilePressed = false; setTimeout(() => renderTabs(), 0); }
}
// A loading PLACEHOLDER tab (the user 2026-06-26): name + identity color from the kernel's tabOrder push,
// shown while the session's build_session is still in flight so the strip's full width is reserved up front
// (no one-by-one pop-in). CLICKABLE (the user 2026-08-25: "I'd like to click it so when the session
// opens I'm already there") — it joins the tab set fully (select via the same #tabs delegate, keyboard,
// activation → MRU + peek derivation), the thread area holding the pane-local romp loader until the
// first session frame lands in place (showActive's loading branch). Still no close/drag: there is no
// session to end yet, and the order is the kernel's until the payload arrives. It wears the MINI romp
// swirl (spinning glyph) as its "generating" cue (the user 2026-07-03) — the same romp-loader motif as
// the panes, so a tab still building reads as "romp is working on this," consistent everywhere.
function makePlaceholderTab(id: string): HTMLElement {
  const meta = tabMeta.get(id);
  const tab = el("div", "tab tab-placeholder" + (id === activeId ? " active" : ""));
  tab.dataset.id = id;
  tab.dataset.act = "select";   // the SAME stable #tabs delegate every real tab rides (click-safe)
  tab.tabIndex = 0;
  tab.addEventListener("keydown", onTabKey);
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
function syncNoSessionsPlaceholder(visibleCount: number, totalCount = 0) {
  const content = document.getElementById("content");
  if (!content) return;
  const existing = document.getElementById("no-sessions");
  if (visibleCount > 0) {
    existing?.remove();               // a session arrived → the real view takes over
    return;
  }
  // sessions exist but the active view hides them all — say THAT, not "no sessions yet"
  const txt = totalCount > 0
    ? "Every session is hidden from this view. Reveal one from the + picker, or switch views on the timeline's Show menu."
    : "No sessions yet. Start one with  romp new <name>  or the + above.";
  if (existing) { existing.textContent = txt; return; }   // idempotent: renderTabs runs on every push
  const ph = el("div", "tx-empty");
  ph.id = "no-sessions";
  ph.textContent = txt;
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
    : ctxFallbackColor(pct);   // theme-aware pair (ctx-color.ts): classic keeps main's 60/85 verbatim.
  // FILLS wear the tone as-is in every theme — readableRgb is for TEXT (re-encoding the warn amber
  // fill made it a muddy brown on light; the user 2026-08-31, off the live preview)
  g.appendChild(fill);
  g.title = `context ${pct}% used`;
  return g;
}

// A hairline under EVERY row of tabs (T134, the user 2026-08-27, overturning the survey's
// one-outer-line design — their call, flagged when it shipped: with three rows and a short third,
// row 2's tabs "look like they're sitting there floating"). CSS cannot select flex-wrap rows, so
// the painter groups the rendered tabs by offsetTop and lays one absolute full-bleed hairline
// under each row but the last — the strip's existing bottom border already finishes the final row. Runs on
// every strip rebuild and on wrap changes (a ResizeObserver on #tabs: width changes re-wrap rows
// without a rebuild — event-keyed, no polling). Classic-scoped in CSS (the Yatharth theme hides
// .tab-row-line), like every strip tuning.
function paintTabRowLines(bar: HTMLElement): void {
  for (const old of Array.from(bar.querySelectorAll(":scope > .tab-row-line"))) old.remove();
  const rows = new Map<number, number>();   // rowTop → rowBottom (max tab bottom in that row)
  for (const t of Array.from(bar.children) as HTMLElement[]) {
    if (!t.classList.contains("tab")) continue;
    const top = t.offsetTop, bot = t.offsetTop + t.offsetHeight;
    rows.set(top, Math.max(rows.get(top) ?? 0, bot));
  }
  const bottoms = [...rows.values()].sort((a, b) => a - b);
  bottoms.pop();   // the LAST row already has #tabbar's own border-bottom beneath it — no double line
  for (const y of bottoms) {
    const line = el("div", "tab-row-line");
    line.style.top = y + "px";
    bar.appendChild(line);
  }
}
let tabRowObserver: ResizeObserver | null = null;
function ensureTabRowObserver(bar: HTMLElement): void {
  if (tabRowObserver) return;
  tabRowObserver = new ResizeObserver(() => paintTabRowLines(bar));
  tabRowObserver.observe(bar);
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
  // the session VIEWS filter composes here too (the user 2026-08-18): a view-hidden session keeps
  // its state, drafts and cached transcript — it just loses its tab until revealed
  const inViewIds = ids.filter(tabInView);
  const visibleIds = only ? inViewIds.filter((id) => matchesOnly(nameOf(id), only)) : inViewIds;
  // ...and it must govern the CHAT BODY too, not just the bar (the user 2026-07-16). Hiding a
  // non-matching TAB while its transcript keeps rendering leaks precisely what the filter exists to
  // hide: a real session's chat sitting on screen under `#only=api,tests,web`, statusline and all —
  // found while shooting the demo, with nimbus's transcript filling a "filtered" frame. Re-point the
  // selection at the first visible session. Deferred so we never re-enter the render we're inside;
  // setActive is a no-op once activeId is visible, so this settles in one pass.
  if (activeId && ids.includes(activeId) && !visibleIds.includes(activeId) && visibleIds.length) {
    const next = visibleIds[0];
    // re-validate at FIRE time, not schedule time: an activation between the two (a feed click
    // opening an ephemeral peek, a reveal landing) can have made the active tab visible — bouncing
    // then would kick the user off the very tab they just opened (the no-flap rule)
    setTimeout(() => { if (activeId !== next && activeId && !tabInView(activeId)) setActive(next); }, 0);
  }
  for (const id of visibleIds) {
    const s = sessions.get(id);
    if (!s) { bar.appendChild(makePlaceholderTab(id)); continue; }
    const tab = el("div", "tab" + (id === activeId ? " active" : ""));
    tab.tabIndex = 0;            // focusable for keyboard nav
    tab.dataset.id = id;
    tab.dataset.act = "select";  // click → setActive, via the stable #tabs delegate (./actions), not a per-node handler
    tab.addEventListener("keydown", onTabKey);
    // drag-to-reorder (synced with the timeline via the shared session-order file). A subagent viewer
    // stays put: it is client-only, and a reorder would post its id into the kernel's order.
    tab.draggable = !s.sub;
    // Exactly ONE thing on screen may look like the dragged tab (T133, the user 2026-08-27: the
    // native drag image following the pointer PLUS the dimmed in-flow tab read as a ghost
    // duplicate — "not how most softwares show it"). The native image is blanked, and the dimmed
    // in-flow element — the one that live-reorders through the strip — is the single provisional
    // visual, browser-style. dragImageBlank must be a rendered DOM node at dragstart (Chromium
    // snapshots it), hence the fixed off-viewport 1px div installed once below.
    tab.addEventListener("dragstart", (e) => {
      draggedId = id; tabDragCommitted = false;
      if (e.dataTransfer) { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setDragImage(dragImageBlank(), 0, 0); }
      tab.classList.add("dragging");
      hideTabTip();                        // defect 2 (2026-08-28): the hover popover pinned open through the gesture
      snapshotDragGeometry(tab);           // widths once at dragstart — the virtual hit-test's stable input (dragslot.ts)
    });
    // dragend closes EVERY drag (drop, Escape, released outside). The pointerdown that started the
    // drag latched tabPointerHeld, and the drag swallowed the matching pointerup — so the hold is
    // released here by hand, covering the whole gesture against pushes (the click-safe rule). A
    // CANCELLED drag re-renders from the untouched order and everything FLIP-animates home — that
    // render also folds in any push that arrived, deferred, mid-drag. A committed drop's reorderTo
    // already asked for its render; it ran deferred, so flush it.
    tab.addEventListener("dragend", () => {
      const cancelled = !tabDragCommitted;
      draggedId = null; tabDragCommitted = false;
      tab.classList.remove("dragging");
      tabPointerHeld = false;
      const pending = renderPendingWhilePressed;
      renderPendingWhilePressed = false;
      if (cancelled) flipTabs(() => renderTabs());
      else if (pending) setTimeout(() => renderTabs(), 0);
    });
    if (s.color) {
      tab.style.setProperty("--chip-bg", s.color.bg);
      tab.style.setProperty("--chip-fg", s.color.fg);
      tab.classList.add("colored");
    }
    if (id === peekId) tab.classList.add("tab-peek");   // ephemeral peek — ghost/dashed dress (styles.css)
    const st = s.status.state;
    if (st === "working") tab.classList.add("tab-working");
    // "blocked" is an API error. An on-YOU one — "prompt is too long" (compact), a monthly spend cap (raise it,
    // the user 2026-07-14), a spent model allowance (switch model, the user 2026-08-01), or a safeguards
    // refusal (rewrite the ask, the user 2026-08-15) — is alarm-red dashed; a TRANSIENT API error is auto-retrying and needs no attention → the
    // amber retrying treatment, not red (the user 2026-06-29).
    else if (st === "blocked") tab.classList.add((s.status.apiTooLong || s.status.apiSpendLimit || s.status.apiModelLimit || s.status.apiAuthErr || s.status.apiRefusal) ? "tab-blocked" : "tab-retrying");
    else if (st === "needsInput" || st === "awaiting") tab.classList.add("tab-awaiting");   // legacy name = an older remote kernel
    else if (st === "retrying") tab.classList.add("tab-retrying");       // amber: soft-blocked on an API auto-retry
    else if (st === "compacting" || st === "clearing") tab.classList.add("tab-compacting");   // both: a context op in flight
    else if (st === "closed") tab.classList.add("tab-closed");       // dead session: read-only, struck-through label
    if (s.status.faded) tab.classList.add("at-rest");
    // WORKING shows a yellow dot; AWAITING-BG the same dot in await-green — matching the chip's color, so the
    // tab reads the split at a glance (the user 2026-07-13); BLOCKED (API error) gets NO dot — the dashed
    // red tab highlight instead (the user 2026-06-16).
    if (st === "working") tab.appendChild(el("span", "tab-dot"));
    else if (st === "awaitingBg") tab.appendChild(el("span", "tab-dot await"));
    // MISSING state — the kernel listed this session but could not read what it is doing. An
    // explicit gray ring, so a bare tab can only mean a state with its own tab treatment (dashed
    // blocked ring, compacting bar, struck-through closed) or a healthy idle one, never a hole.
    else if (!st) tab.appendChild(el("span", "tab-dot unknown"));
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
    // a glance rather than only on inspection (the user 2026-07-29). The marked "host:" carries the why.
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
      if (settings.tabCtx === "always" || pct >= 50) tab.appendChild(tabCtxGauge(s.status.ctx, pickTone(s.status.ctxColor, s.status.ctxTone)));
    }
    // Rich hover tooltip (custom DOM — a native title can't colour/bold): backend in its own colour, the
    // full dir path, and mode/model/effort/context each on a line (the user 2026-06-23). See showTabTip.
    if (!s.sub) {   // the rich tip reads a real session's dir/branch/model; a viewer has none of them
      tab.addEventListener("mouseenter", () => showTabTip(tab, s));
      tab.addEventListener("mouseleave", hideTabTip);
    }
    const close = el("span", "tab-close");
    close.textContent = "×";
    // A dead (closed) session has nothing to end, so its ✕ just removes the read-only tab — no
    // "End session?" confirm (the user 2026-06-16). A live session still routes through the host's
    // Close-tab / End-session confirm (closeSession → confirmClose). A subagent viewer likewise
    // just closes (the tabs delegate's close handler routes it by isSubId).
    const dead = st === "closed";
    close.title = dead || s.sub ? "Close tab" : "End session";
    // Click-safe (see ./actions): renderTabs() does `#tabs`.replaceChildren() on every kernel push, so a
    // handler hung on this ✕ is destroyed mid-click and the click is dropped (the "had to click End session
    // several times" bug). The action lives on the stable #tabs delegate instead; this node just declares it.
    close.dataset.act = "close";
    close.dataset.id = id;
    if (dead) close.dataset.dead = "1";
    tab.appendChild(close);
    // double-click a tab to show/hide the ledger overview — same as the strip's caret
    tab.addEventListener("dblclick", (e) => { e.preventDefault(); toggleLedgerCollapsed(); });
    // right-click → context menu; "Rename" edits the title in place (not for a viewer: nothing to rename/hide/end)
    if (!s.sub) tab.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); showTabMenu(e, id); });
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
  // the shared TAG-ICON filter (the user 2026-08-25): identical across surfaces, opening the one
  // multi-select lens menu — this instance governs the TAB STRIP (actives.chat)
  const tagBtn = tagMenuButton("filter these tabs by tag", (btn) => {
    openTagMenu(btn, {
      lens: () => surfaceLens(effViews(), "chat"),
      unions: () => viewTagUnion(effViews()),
      onApply: (l) => {
        const v = JSON.parse(JSON.stringify(effViews() || { active: "all", tags: [] }));
        v.actives = Object.assign({}, v.actives, { chat: l });
        postViews(v);
      },
      onConfigure: () => { vscodeApi?.postMessage({ type: "openTagsDialog" }); },
    });
  });
  tagBtn.classList.add("tab-tagfilter");
  // button + chips ride ONE box that reserves the + tab's exact height (.tab-tagbox, 31px) and
  // centers them in it — a wrapped controls-only line used to stand only pill-tall and sat nearly
  // flush under the row above (the user 2026-08-25); the box makes every line the controls form
  // as tall as a line the + is on, wherever the strip wraps them
  const tagBox = el("span", "tab-tagbox");
  tagBox.appendChild(tagBtn);
  // THE BUTTON CONVENTION (the user 2026-08-25): gray alone at rest; accent + the chips of
  // everything selected when narrowed — the shared renderer, identical on every mount
  const tagChipsHost = el("span", "tab-tagchips");
  tagChipsHost.setAttribute("style", "display:inline-flex;gap:5px;align-items:center;margin-left:2px;");
  tagBox.appendChild(tagChipsHost);
  bar.appendChild(tagBox);
  {
    const v = effViews();
    syncTagFilter(tagBtn, tagChipsHost, surfaceLens(v, "chat"), viewTagUnion(v), (l) => {
      const nv = JSON.parse(JSON.stringify(v || { active: "all", tags: [] }));
      nv.actives = Object.assign({}, nv.actives, { chat: l });
      postViews(nv);
    });
  }
  // T161 (the user 2026-08-28, Android: no tag control on mobile): the phone chat page hides the whole
  // #tabs strip — and the mount above with it. The kernel's mobile header carries an empty #mtag-slot
  // (left of +); mount the SAME shared button + chips into it ONCE — the slot is kernel-built and never
  // rebuilt, so the ensure-once mount is click-safe by construction — and re-sync it every render like
  // the desktop pair. Same chat lens, same house menu, same echo dismissal: one vocabulary, no copy.
  // Absent slot (VS Code webview, desktop-only pages) → no-op; on the desktop kernel page the slot
  // hides with #mhdr. The touch padding is set inline because the shared button styles inline by design.
  const mslot = document.getElementById("mtag-slot");
  if (mslot) {
    if (!mslot.firstChild) {
      const mBtn = tagMenuButton("filter sessions by tag", (btn) => {
        openTagMenu(btn, {
          lens: () => surfaceLens(effViews(), "chat"),
          unions: () => viewTagUnion(effViews()),
          onApply: (l) => {
            const mv = JSON.parse(JSON.stringify(effViews() || { active: "all", tags: [] }));
            mv.actives = Object.assign({}, mv.actives, { chat: l });
            postViews(mv);
          },
          onConfigure: () => { vscodeApi?.postMessage({ type: "openTagsDialog" }); },
        });
      });
      mBtn.classList.add("tab-tagfilter");
      mBtn.style.padding = "8px 9px";   // the coarse-pointer target: ~32px tall, the header row's own scale
      const mChips = el("span", "tab-tagchips");
      mChips.setAttribute("style", "display:inline-flex;gap:5px;align-items:center;margin-left:2px;");
      mslot.append(mBtn, mChips);
    }
    const mv2 = effViews();
    syncTagFilter(mslot.children[0] as HTMLElement, mslot.children[1] as HTMLElement,
      surfaceLens(mv2, "chat"), viewTagUnion(mv2), (l) => {
        const nv = JSON.parse(JSON.stringify(mv2 || { active: "all", tags: [] }));
        nv.actives = Object.assign({}, nv.actives, { chat: l });
        postViews(nv);
      });
  }
  paintTabRowLines(bar);
  ensureTabRowObserver(bar);
  // Restore tab-mode focus if a tab held it before this rebuild (see the top of renderTabs).
  if (refocusTab) focusActiveTab();
  syncNoSessionsPlaceholder(visibleIds.length, ids.length);
  // Hiding the LAST visible session must also blank its transcript: a strip with no tabs cannot sit
  // over a hidden session's live chat (the ghost would show exactly what the hide asked to put away).
  // Restored the moment anything is visible again — the placeholder owns the empty state meanwhile.
  if (activeId) {
    const av = views.get(activeId);
    const blank = !visibleIds.length && ids.length > 0 && !tabInView(activeId);
    if (av && blank && av.el.style.display !== "none") { av.el.style.display = "none"; allHiddenBlanked = true; }
    else if (av && !blank && allHiddenBlanked) { av.el.style.display = ""; allHiddenBlanked = false; }
  }
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
  // Comment first, Quote second (the user 2026-08-23): Comment is the primary act — a side thread
  // about the passage — and Quote is the lighter one. Comment only when the selection sits in a real
  // transcript turn (transcriptSelection's uuid) on a real session.
  const q = transcriptSelection();
  if (q?.uuid && activeId && !isProvisionalId(activeId) && sessions.get(activeId)) {
    const sid = activeId, uuid = q.uuid, qtext = q.text;
    mk("Comment", () => openCommentComposer(sid, uuid, qtext, e.clientX, e.clientY));
  }
  // "Quote" is the CHIP, and only the chip (the user 2026-08-23, consolidating the three verbs —
  // Comment / Quote / Stage — by removal): the selection already seeded it (selectionchange), so
  // the item just puts the caret where the reply goes. The in-box editable-blockquote form is gone.
  mk("Quote", () => { (document.getElementById("composer-input") as HTMLTextAreaElement | null)?.focus(); });
  mk("Copy", () => copyToClipboard(text));
  document.body.appendChild(menu);
  ctxMenuEl = menu;
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(0, Math.min(e.clientX, window.innerWidth - r.width - 4)) + "px";
  menu.style.top = Math.max(0, Math.min(e.clientY, window.innerHeight - r.height - 4)) + "px";
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
  notePendingMeta(pendingTabMeta, id, { colorBg: bg });   // a push built before the kernel applied this cannot revert the swatch
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
function ctxIcon(kind: "feed" | "mail" | "bell" | "bill" | "folder" | "tag" | "pencil", off: boolean): HTMLElement {
  const span = el("span", "ctx-icon" + (off ? " off" : ""));
  const slash = off ? '<line x1="1.6" y1="14.4" x2="14.4" y2="1.6"/>' : "";
  const body = kind === "feed"
    ? '<circle cx="8" cy="8" r="6"/><path d="M5 8.3 L7.2 10.7 L11.4 5.3"/>'              // circle + check (on the feed)
    : kind === "mail"
      ? '<rect x="2" y="4" width="12" height="8" rx="1.5"/><path d="M2.5 5 L8 9 L13.5 5"/>'  // envelope (on the postal service)
      : kind === "bill"
        ? '<rect x="2" y="4" width="12" height="8" rx="1.5"/><line x1="2" y1="6.8" x2="14" y2="6.8"/><line x1="4.2" y1="9.6" x2="7.4" y2="9.6"/>'  // payment card (billing)
        : kind === "folder"
          ? '<path d="M2 4.5 A1.2 1.2 0 0 1 3.2 3.3 L6.2 3.3 L7.6 4.9 L12.8 4.9 A1.2 1.2 0 0 1 14 6.1 L14 11.5 A1.2 1.2 0 0 1 12.8 12.7 L3.2 12.7 A1.2 1.2 0 0 1 2 11.5 Z"/>'  // folder (browse files)
        : kind === "tag"
          ? '<path d="M2 3.4 A1.4 1.4 0 0 1 3.4 2 L7.6 2 A1.4 1.4 0 0 1 8.6 2.4 L13.6 7.4 A1.4 1.4 0 0 1 13.6 9.4 L9.4 13.6 A1.4 1.4 0 0 1 7.4 13.6 L2.4 8.6 A1.4 1.4 0 0 1 2 7.6 Z"/><circle cx="5.4" cy="5.4" r="1.1"/>'  // luggage tag (session tags)
        : kind === "pencil"
          ? '<path d="M3 13 L3.6 10.4 L10.8 3.2 A1.3 1.3 0 0 1 12.8 5.2 L5.6 12.4 Z"/><line x1="9.8" y1="4.2" x2="11.8" y2="6.2"/>'  // pencil (rename)
          : '<path d="M8 2 C5.9 2.2 4.7 3.8 4.7 5.8 L4.7 8 L3.4 9.9 L12.6 9.9 L11.3 8 L11.3 5.8 C11.3 3.8 10.1 2.2 8 2 Z"/><path d="M6.6 11.6 A1.5 1.5 0 0 0 9.4 11.6"/>';  // bell (system notifications)
  span.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + body + slash + "</svg>";
  return span;
}

function showTabMenu(e: MouseEvent, id: string) {
  dismissTabMenu();
  const menu = el("div", "ctx-menu");
  // Rename leads ONE top section with the session controls (the user 2026-08-24: it sat alone and
  // bare above its own divider) — the standard dress like its siblings: icon + the sub-line, which
  // says what a rename PRESERVES (sessions are uuid-keyed, the name is a label — mailboxes, goals
  // and history follow the session, per the /rename route's contract).
  // id only, never the tab node under the cursor: the menu (on document.body) outlives kernel pushes,
  // but the tab it was opened from does not — renderTabs() swaps the strip on every push, so a node
  // captured here is usually DETACHED by the time Rename is clicked (the click-safety rule).
  {
    const rename = el("div", "ctx-item ctx-item-toggle");
    rename.appendChild(ctxIcon("pencil", false));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = "Rename"; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub"); sb.textContent = "the name is a label — mail, goals and history follow the session"; bodyEl.appendChild(sb);
    rename.appendChild(bodyEl);
    rename.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); startTabRename(id); });
    menu.appendChild(rename);
  }
  // Move to folder… sits with Rename (the user 2026-09-01: a subproject became its own repo and the
  // session should follow it) — the same dress, the sub-line saying what a move KEEPS. The dialog does
  // the rest (showMovePrompt); the kernel wraps the CLI's own relocation. SDK sessions only: a terminal
  // session has no relocation primitive, so its row says so rather than failing after a click.
  {
    const sTm = sessions.get(id);
    const isTmux = !!(sTm && sTm.status && sTm.status.backend === "tmux");
    const mv = el("div", "ctx-item ctx-item-toggle" + (isTmux ? " ctx-item-off" : ""));
    mv.appendChild(ctxIcon("folder", isTmux));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = "Move to folder…"; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub");
    sb.textContent = isTmux ? "terminal sessions can't move — start a new one in that folder"
                            : "the conversation, mail, goals and history stay with the session";
    bodyEl.appendChild(sb);
    mv.appendChild(bodyEl);
    if (!isTmux) mv.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); showMovePrompt(id); });
    menu.appendChild(mv);
  }
  // Colors join Rename in the AESTHETIC section (the user 2026-08-24, the final by-kind grouping:
  // [Rename + colors] / [feed, mail, bell, billing, Tags] / [Browse]). The swatch row itself is
  // unchanged (the user 2026-06-29): the identity palette as circles, the current one ringed,
  // omitted until /palette has loaded.
  if (paletteColors.length) {
    const sNow = sessions.get(id);
    const cur = (sNow && sNow.color ? sNow.color.bg : "").toLowerCase();
    const row = el("div", "ctx-colors");
    // balanced swatch rows (the user 2026-08-28, T164): for n swatches, the fewest rows that keep
    // each row within the menu-friendly cap, split ceil-evenly — 12 reads 6+6, 9 reads 5+4, a
    // future 13 reads 7+6; the CSS keeps repeat(5) as the no-JS fallback
    const swRows = Math.ceil(paletteColors.length / 6);
    row.style.gridTemplateColumns = "repeat(" + Math.ceil(paletteColors.length / swRows) + ", 18px)";
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
  menu.appendChild(el("div", "ctx-sep"));
  // Feed + Mail per-session toggles (the user 2026-06-26) — the same controls as the timeline lane's feed
  // checkbox + postal mailbox, here as icon + label + a faint "what it does" sub-line. State from the session.
  const s = sessions.get(id);
  const offFeed = !!(s && s.hideFromFeed);
  const offMail = !!(s && s.postalServiceOff);
  const onBell = !!(s && s.notify);
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
  // (The hide-session mechanism is fully RETIRED, the user 2026-08-24 — the tag system covers
  // backgrounding; the kernel migrated existing hidden entries into the "archived" tag. revealIn
  // survives for the picker's tagged-session jump.)
  // Billing submenu (the user 2026-08-09, who wants the login/API-key switch here rather than as a
  // statusline badge). Only when the machine offers BOTH choices (st.authBoth) — a one-auth machine
  // keeps the fact on the tab hover, never a dead selector — and the key stays labelled plainly
  // 'API key', no fragment of it anywhere. Clicking opens a flyout with the two choices, the
  // session's current one check-marked; a pick posts the same setAuth the badge used (the session
  // reconnects to apply, so the sub-line says "applying…" while st.authPending rides the status).
  const st = s ? s.status : null;
  if (st && st.auth && st.authBoth) {
    // (no divider: billing sits in the behavior section with the toggles — the by-kind grouping)
    const item = el("div", "ctx-item ctx-item-toggle ctx-item-billing");
    item.appendChild(ctxIcon("bill", false));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = "Billing"; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub");
    sb.textContent = st.authPending ? "applying…"
      : st.authLive && st.authLive !== st.auth
        ? `⚠ CLI reports ${st.authLive === "key" ? "API key" : "login"}`   // the pick did not take — say so where the switch lives (T124)
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
  // TAGS (the user 2026-08-24, overruling the earlier skip: tag editing belongs everywhere a
  // session is in front of you — you might not have the timeline open and still want to organize
  // or dispatch). A compact one-line row — the current tag names as the sub-line — with the
  // mechanics one click away in a flyout (progressive disclosure; the Billing submenu's chrome).
  // SAME semantics as the timeline dialog, name-keyed union rules throughout (kernels are plumbing,
  // no host prefixes): an ADD lands on the local store when the name exists locally, else the
  // tag's single home over the editTag wire; a REMOVE removes the (name, member) pair from EVERY
  // store holding it; New tag… creates locally with the next unused palette colour. Local writes
  // post the whole blob (postViews — pendingSessionViews echoes instantly); remote writes ride the
  // editTag op and settle on the next push (a refused edit re-appears — the kernel's loud
  // tagEditFailed lands on the timeline dialog, 628's surface).
  {
    const unionFor = () => viewTagUnion(effViews());
    const holding = () => unionFor().filter((g) => g.members.includes(id));
    const tagsItem = el("div", "ctx-item ctx-item-toggle ctx-item-tags");
    tagsItem.appendChild(ctxIcon("tag", false));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = "Tags"; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub");
    const subText = () => { const names = holding().map((g) => g.name); return names.length ? names.join(" · ") : "none yet — tag it to organize and dispatch"; };
    sb.textContent = subText();
    bodyEl.appendChild(sb);
    tagsItem.appendChild(bodyEl);
    const caret = el("span", "ctx-caret"); caret.textContent = "▸"; tagsItem.appendChild(caret);
    const editUnion = (g: TagUnion, edit: { add?: string[]; remove?: string[] }) => {
      // ONE optimistic blob per gesture: the local store's edit AND the remote entries' mirror both
      // land in pendingSessionViews so the flyout reads true instantly. Echoed remoteTags are
      // DERIVED — the kernel drops them from the echo — so mutating the copy is presentation-only;
      // the remote's own next push is the durable truth (a refused edit re-appears there).
      const nv = JSON.parse(JSON.stringify(effViews() || {})) as SessionViews;
      let dirty = false;
      const nvRemote = (rt: SessionTag) => (nv.remoteTags || []).find((x) => x.id === rt.id);
      if (edit.add?.length) {
        if (g.localId) {
          const t = viewTags(nv).find((x) => x.id === g.localId);
          if (t) { t.members = Array.from(new Set((t.members || []).concat(edit.add))); dirty = true; }
        } else if (g.remotes.length) {
          vscodeApi?.postMessage({ type: "editTag", edit: { host: g.remotes[0].host || "", name: g.name, add: edit.add.slice() } });
          const mine = nvRemote(g.remotes[0]);
          if (mine) { mine.members = Array.from(new Set((mine.members || []).concat(edit.add))); dirty = true; }
        }
      }
      if (edit.remove?.length) {
        if (g.localId) {
          const t = viewTags(nv).find((x) => x.id === g.localId);
          if (t && (t.members || []).some((m) => edit.remove!.includes(m))) {
            t.members = (t.members || []).filter((m) => !edit.remove!.includes(m));
            dirty = true;
          }
        }
        for (const rt of g.remotes) {
          if (!(rt.members || []).some((m) => edit.remove!.includes(m))) continue;
          vscodeApi?.postMessage({ type: "editTag", edit: { host: rt.host || "", name: g.name, remove: edit.remove!.slice() } });
          const mine = nvRemote(rt);
          if (mine) { mine.members = (mine.members || []).filter((m) => !edit.remove!.includes(m)); dirty = true; }
        }
      }
      if (dirty) postViews(nv);
    };
    // HOVER-INTENT open (T163, the user 2026-08-28: hovering down to Tags should open the submenu
    // without another click): the feed's 120ms intent debounce — enough to skip a graze, never a
    // wait. Click still opens instantly (and focuses the input; a hover-open must NOT steal the
    // keyboard). Leaving is tolerant the way native menus are: the same 120ms lets the pointer
    // cross the gap into the submenu; entering either surface cancels the close, leaving BOTH
    // closes. Timers here are gesture DEFINITIONS (hover intent), not state proxies.
    const HOVER_INTENT_MS = 120;
    let hoverOpenT: number | null = null;
    let hoverCloseT: number | null = null;
    const cancelHoverTimers = () => {
      if (hoverOpenT != null) { clearTimeout(hoverOpenT); hoverOpenT = null; }
      if (hoverCloseT != null) { clearTimeout(hoverCloseT); hoverCloseT = null; }
    };
    const openTagsFly = (focusInput: boolean) => {
      const openFly = menu.querySelector(".ctx-sub-tags");
      if (openFly) return openFly as HTMLElement;
      menu.querySelector(".ctx-sub")?.remove();                  // one flyout at a time (Billing's rule)
      const sub = el("div", "ctx-menu ctx-sub ctx-sub-tags");
      const build = () => {
        sub.replaceChildren();
        for (const g of holding()) {                             // one chip per NAME — never a host prefix
          const row = el("div", "ctx-item ctx-item-toggle");
          const chip = el("span", "ctx-tag-dot"); chip.style.background = g.color || "var(--dim)"; row.appendChild(chip);
          const bodyE = el("span", "ctx-item-body");
          const lb = el("span", "ctx-item-label"); lb.textContent = g.name; bodyE.appendChild(lb);
          row.appendChild(bodyE);
          const x = el("button", "ctx-tag-x") as HTMLButtonElement;
          x.type = "button"; x.textContent = "✕"; x.title = "remove this tag from the session — everywhere it holds it";
          x.addEventListener("click", (e2) => { e2.stopPropagation(); editUnion(g, { remove: [id] }); build(); sb.textContent = subText(); });
          row.appendChild(x);
          sub.appendChild(row);
        }
        const others = unionFor().filter((g) => !g.members.includes(id));
        if (holding().length && others.length) sub.appendChild(el("div", "ctx-sep"));
        for (const g of others) {
          const row = el("div", "ctx-item ctx-item-toggle");
          const chip = el("span", "ctx-tag-dot"); chip.style.background = g.color || "var(--dim)"; row.appendChild(chip);
          const bodyE = el("span", "ctx-item-body");
          const lb = el("span", "ctx-item-label"); lb.textContent = "+ " + g.name; bodyE.appendChild(lb);
          row.appendChild(bodyE);
          row.addEventListener("click", (e2) => { e2.stopPropagation(); editUnion(g, { add: [id] }); build(); sb.textContent = subText(); });
          sub.appendChild(row);
        }
        if (holding().length || others.length) sub.appendChild(el("div", "ctx-sep"));
        // New tag… — an inline input, never a native prompt (the menus vocabulary)
        const nrow = el("div", "ctx-item ctx-item-newtag");
        const inp = el("input", "ctx-tag-input") as HTMLInputElement;
        inp.placeholder = "New tag…"; inp.maxLength = 40;
        inp.addEventListener("click", (e2) => e2.stopPropagation());
        inp.addEventListener("keydown", (e2) => {
          if (e2.key !== "Enter") return;
          const name = inp.value.trim();
          if (!name) return;
          const existing = unionFor().find((g) => g.name === name);
          if (existing) { editUnion(existing, { add: [id] }); build(); sb.textContent = subText(); return; }
          const nv = JSON.parse(JSON.stringify(effViews() || {})) as SessionViews;
          const used = new Set(viewTags(nv).map((t) => t.color));
          const color = paletteColors.find((c) => !used.has(c)) || paletteColors[0] || "#1EA1EB";
          nv.tags = viewTags(nv).concat([{ id: "g" + Date.now().toString(36), name, color, members: [id] }]);
          delete nv.groups;
          postViews(nv);
          build(); sb.textContent = subText();
        });
        nrow.appendChild(inp);
        sub.appendChild(nrow);
      };
      build();
      // Configure tags… at the foot, behind the divider (T163): the ONE route the tag-lens menus
      // use — openTagsDialog — never a copy of the dialog wiring.
      sub.appendChild(el("div", "ctx-sep"));
      const cfg = el("div", "ctx-item ctx-item-configtags");
      const cfgBody = el("span", "ctx-item-body");
      const cfgL = el("span", "ctx-item-label"); cfgL.textContent = "Configure tags…"; cfgBody.appendChild(cfgL);
      cfg.appendChild(cfgBody);
      cfg.addEventListener("click", (e2) => {
        e2.stopPropagation(); dismissTabMenu();
        vscodeApi?.postMessage({ type: "openTagsDialog" });
      });
      sub.appendChild(cfg);
      menu.appendChild(sub);
      const ir = tagsItem.getBoundingClientRect();
      const sr = sub.getBoundingClientRect();
      // the side rule (the model-version submenus): PREFER right; fall LEFT only when the right
      // edge would clip — never slide over the row
      if (ir.right + 2 + sr.width <= window.innerWidth - 8) sub.style.left = Math.round(ir.right + 2) + "px";
      else sub.style.left = Math.max(8, Math.round(ir.left) - sr.width - 2) + "px";
      sub.style.top = Math.max(0, Math.min(ir.top, window.innerHeight - sr.height - 4)) + "px";
      if (focusInput) (sub.querySelector(".ctx-tag-input") as HTMLInputElement | null)?.focus();
      // leave-tolerance: entering either surface cancels the pending close; leaving both arms it
      sub.addEventListener("pointerenter", cancelHoverTimers);
      sub.addEventListener("pointerleave", armHoverClose);
      return sub;
    };
    const armHoverClose = () => {
      cancelHoverTimers();
      hoverCloseT = window.setTimeout(() => {
        hoverCloseT = null;
        menu.querySelector(".ctx-sub-tags")?.remove();
      }, HOVER_INTENT_MS);
    };
    tagsItem.addEventListener("pointerenter", () => {
      cancelHoverTimers();
      if (menu.querySelector(".ctx-sub-tags")) return;           // already open — nothing to intend
      hoverOpenT = window.setTimeout(() => { hoverOpenT = null; openTagsFly(false); }, HOVER_INTENT_MS);
    });
    tagsItem.addEventListener("pointerleave", armHoverClose);
    tagsItem.addEventListener("click", (ev) => {
      ev.stopPropagation();
      cancelHoverTimers();
      const openFly = menu.querySelector(".ctx-sub-tags");
      if (openFly) { openFly.remove(); return; }                 // second click folds the flyout
      openTagsFly(true);
    });
    menu.appendChild(tagsItem);
  }
  // BROWSE FILES — at the BOTTOM behind its own divider (the user 2026-08-24: it is a different
  // kind of thing from the toggles above), wearing the standard icon + sub-description dress, and
  // opening PANE-LOCAL over this chat (openBrowse). Web-only: the VS Code webview cannot reach the
  // kernel origin, and the editor has its own explorer.
  if (location.protocol === "http:" || location.protocol === "https:") {
    menu.appendChild(el("div", "ctx-sep"));
    const browse = el("div", "ctx-item ctx-item-toggle");
    browse.appendChild(ctxIcon("folder", false));
    const bodyEl = el("span", "ctx-item-body");
    const l = el("span", "ctx-item-label"); l.textContent = "Browse files"; bodyEl.appendChild(l);
    const sb = el("span", "ctx-item-sub"); sb.textContent = "the session's working tree, in a viewer over this chat"; bodyEl.appendChild(sb);
    browse.appendChild(bodyEl);
    browse.addEventListener("click", (ev) => {
      ev.stopPropagation(); dismissTabMenu();
      openBrowse(s?.cwd || ".", id);
    });
    menu.appendChild(browse);
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
    const ord = visibleOrder();                 // never cycle onto a view-hidden session
    const i = ord.indexOf(activeId);
    if (i < 0) return;
    const dir = e.key === "ArrowRight" ? 1 : -1;
    setActive(ord[(i + dir + ord.length) % ord.length]);
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
  return elm.tagName === "TEXTAREA" || elm.tagName === "INPUT" || elm.tagName === "SELECT" || elm.isContentEditable === true;   // SELECT: type-ahead in a dropdown is typing too
}
window.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  if (isTypingTarget(e.target)) return;
  if (document.querySelector(".picker-overlay")) return;   // #picker / #confirm open
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    if (!activeId || order.length < 2) return;
    const ord = visibleOrder();                 // never cycle onto a view-hidden session
    const i = ord.indexOf(activeId);
    if (i < 0) return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    setActive(ord[(i + dir + ord.length) % ord.length]);
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
    if (composerNoteHolds()) return;               // the box just changed hands under the user — a click re-binds it, not a key (T236)
    if (focusComposerOrAsk()) e.preventDefault();   // the picker card if one's up, else the message box
  }
});
// SELECT → TYPE → ⌘⏎ (the user 2026-09-02): a transcript selection already seeded the reply chip
// (selectionchange), so the natural next act is just TYPING — the first printable keystroke drops
// the cursor into the message box with the chip attached, no mouse round-trip, and ⌘⏎ stages as
// ever. (Not selection-gated: a printable keystroke nobody claimed means "type" from anywhere —
// the same two-state default as bare-area Enter above.) Bubble phase on window = every capture
// handler (shell chords, overlays) and element handler (live-ask card, focused tab, slash menu)
// has already spoken; we take only keystrokes nobody claimed. NEVER preventDefault: focusing
// during keydown lets the NATIVE keystroke insert into the newly focused box, so the composer's
// own input bookkeeping (draft, slash menu) sees ordinary typing.
window.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;   // chords are not typing (shift stays: capitals)
  if (e.isComposing || e.keyCode === 229) return;   // IME mid-composition — a focus steal aborts the composition
  if (e.key.length !== 1 || e.key === " ") return;  // printable only; Space stays a toggle/scroll key
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta || ta.disabled || document.activeElement === ta) return;   // no box / read-only session / already typing (covers key repeat)
  if (isTypingTarget(e.target) || isTypingTarget(document.activeElement)) return;
  if (activeId && liveAsks.has(activeId)) return;   // digits belong to the live-ask card's number keys
  if (ctxMenuEl || document.querySelector(".picker-overlay")) return;   // an open menu / #picker / #confirm owns the keys
  if (document.getElementById("romp-fileview") || document.getElementById("romp-filebrowse")
      || document.getElementById("romp-lightbox")) return;   // full-pane surfaces own their keys
  if (document.querySelector("#rsettings:not([hidden]), #ra-back:not([hidden]), #rkeys-back, .meta-menu")) return;   // the pane's own modals + meta menus own their keys (a letter typed there must never land in the draft)
  if (composerNoteHolds()) return;   // the box just changed hands under the user — no focus steal, the note flashes; a click re-binds (T236). Nothing to cancel either: a key on the bare body has nothing to insert into.
  ta.focus({ preventScroll: true });   // the native keystroke lands in the box; the chip survives (a collapse never clears it)
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
// Ctrl+M / Ctrl+, — chat history back/forward, the user's own Obsidian nav keys (their vault's
// hotkeys.json, verified 2026-08-14: Ctrl, not Option). The trail lives in THIS pane (navHist), so
// the keys are handled here whenever focus is inside the chat — capture-phase like Cmd+O above, so
// they work from the composer too. Registered as chat.navBack/chat.navForward in the shell's command
// registry (palette + Customize shortcuts…); this handler reads the SAME overrides store per press,
// so a rebind in the dialog moves both dispatch paths at once. Chat-only for now (the user's scoping).
window.addEventListener("keydown", (e) => {
  if (!e.ctrlKey && !e.metaKey) return;                 // both defaults carry Ctrl; a rebind may use Meta
  const ch = chordOf(e);
  if (!ch || !ch.includes("+")) return;
  const mac = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
  const ov = loadOverrides();
  if (ch === effectiveChord("chat.navBack", DEFAULT_CHORDS["chat.navBack"], ov, mac)) {
    e.preventDefault(); e.stopPropagation();
    navHist.go(-1);
  } else if (ch === effectiveChord("chat.navForward", DEFAULT_CHORDS["chat.navForward"], ov, mac)) {
    e.preventDefault(); e.stopPropagation();
    navHist.go(1);
  }
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
    dismissSession(id, "close");           // drops it from sessions/order/views and reselects
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
                  mkBe("tmux", "tmux", "Drives a real terminal pane (tmux)."),   // SDK first — the de-facto default (the user 2026-07-02)
                  mkBe("codex", "Codex", "Runs an OpenAI Codex agent (the host needs romp-codex-setup + codex login)."));
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

// The standard romp loader's inner anatomy — wordmark + spinning swirl + pulsing dots + caption
// (the boot/pane .rl-* styles already on this page) — shared by every in-page wait that wears it
// (the revive loader, the loading-tab wait, the comment popover's boot), per the loading-states
// rule: one treatment, parameterized rather than forked. wordmark:false (the user 2026-08-25,
// POPOVER-ONLY: "don't write the whole text of romp — just the spinning logo") keeps the spinning
// swirl + dots + caption and drops the R-o-m-p letters; everywhere else keeps the full treatment.
function rompLoaderInner(caption: string, opts?: { wordmark?: boolean }): HTMLElement {
  const inner = el("div", "rl-in");
  const word = el("div", "rl-word");
  const swirl = el("img", "rl-o") as HTMLImageElement;
  swirl.src = mediaSrc("romp-swirl-o.svg"); swirl.alt = ""; swirl.onerror = () => swirl.remove();
  if (opts?.wordmark === false) {
    word.appendChild(swirl);
  } else {
    const r = el("span", ""); (r as HTMLElement).style.color = "#1EA1EB"; r.textContent = "R";
    swirl.alt = "o";
    const mm = el("span", ""); (mm as HTMLElement).style.color = "#54B204"; mm.textContent = "m";
    const p = el("span", ""); (p as HTMLElement).style.color = "#4EA8A9"; p.textContent = "p";
    word.append(r, swirl, mm, p);
  }
  const dots = el("div", "rl-dots");
  dots.append(el("i", ""), el("i", ""), el("i", ""));
  const cap = el("div", "revive-cap");
  cap.textContent = caption;
  inner.append(word, dots, cap);
  return inner;
}

// ---- revive loader (the user 2026-07-05; PANE-LOCAL 2026-08-25) ----
// Reviving a dead session takes seconds (relaunch + resume), and the Revive click used to give ZERO
// feedback. Per the repo's loading rule the FIRST thing up is the romp loader — spinning swirl +
// wordmark + three pulsing dots, the same .rl-* treatment as the boot/pane loaders (their styles are
// already on this page) — with a "reviving <name>…" caption. EVENT-cleared: the kernel's focus for
// that sid (revive succeeded) or reviveFailed. A 60s backstop can never trap the user.
// PANE-LOCAL (the user 2026-08-25): the old overlay covered the WHOLE window and blocked everything
// while the revive ran. Now the revive gesture mints/foregrounds the session's TAB immediately and
// the loader covers only THAT session's thread area (#content's box, measured — the tab strip and
// composer stay live), shown only while the reviving session is the active one: switch away and every
// other tab is fully interactive, switch back and the loader (or the revived thread) is there.
// placeReviveLoader() re-runs from showActive (every switch/push) + a ResizeObserver on #content.
let revivePending: string | null = null;
let reviveBackstop: number | undefined;
let reviveRo: ResizeObserver | null = null;
// sid → the kernel's named revive failure: shown INSIDE that session's pane (the empty-transcript
// placeholder), plus the dismissible warn-toast family — never a window-blocking overlay.
const failedRevives = new Map<string, string>();

function clearReviveLoader() {
  revivePending = null;
  if (reviveBackstop !== undefined) { clearTimeout(reviveBackstop); reviveBackstop = undefined; }
  reviveRo?.disconnect(); reviveRo = null;
  document.getElementById("revive-loader")?.remove();
}

// Session-local visibility + geometry: the loader shows only while the reviving session is ACTIVE,
// pinned over the thread area's current box. Runs on every showActive (tab switches, push re-renders)
// and on #content resizes, so the tab bar wrapping or the ledger appearing never leaves it adrift.
function placeReviveLoader() {
  const o = document.getElementById("revive-loader");
  if (!o) return;
  const c = document.getElementById("content");
  if (!c || activeId !== revivePending) { o.style.display = "none"; return; }
  const r = c.getBoundingClientRect();
  o.style.display = "flex";
  o.style.top = r.top + "px"; o.style.left = r.left + "px";
  o.style.width = r.width + "px"; o.style.height = r.height + "px";
}

function showReviveLoader(id: string, name: string) {
  clearReviveLoader();
  failedRevives.delete(id);
  revivePending = id;
  // The TAB mints immediately (the openProvisional idiom): the session usually already has its closed
  // tab; a revive reaching a session the chat doesn't hold gets a stub — "opening" is the designed
  // vocabulary for a tab whose real payload is on its way, and the kernel's first payload for the
  // revived session continues it seamlessly.
  if (!sessions.has(id)) {
    sessions.set(id, { id, name, color: null, events: [], status: { state: "opening", sinceEpoch: Date.now() } });
    order.push(id);
  }
  renderTabs();
  setActive(id);
  const o = el("div", ""); o.id = "revive-loader";
  o.appendChild(rompLoaderInner(`reviving “${name}”…`));
  document.body.appendChild(o);
  placeReviveLoader();
  const c = document.getElementById("content");
  if (c && typeof ResizeObserver === "function") {
    reviveRo = new ResizeObserver(placeReviveLoader);
    reviveRo.observe(c);
  }
  reviveBackstop = window.setTimeout(
    () => reviveFailedLocal(id, name, "still waiting — the resume may be stuck; check the kernel log"), 60000);
}

// Failure lands in the SESSION'S OWN PANE (fail loudly, never a window-blocking overlay): the named
// error fills that session's empty-transcript placeholder, and the dismissible warn-toast carries it
// to a user who already switched away. A user gesture (tab ✕, another revive) always beats it.
function reviveFailedLocal(id: string, name: string, text: string) {
  clearReviveLoader();
  const msg = `Couldn’t revive “${name}”: ${text}`;
  failedRevives.set(id, msg);
  const v = views.get(id);
  if (v) { v.rendered = 0; v.stale = true; }   // the placeholder re-renders with the failure text
  if (activeId === id) { syncView(id); showActive(); }
  warnToast(msg);
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
// ---- move session (the user 2026-09-01) ----
// "Move to folder…" on the tab menu: the session's working directory follows a subproject that became
// its own repo. The kernel wraps the CLI's own relocation (its set_cwd control request): conversation,
// name, mailbox and history stay with the session; its tools, CLAUDE.md and project settings come from
// the new folder from the next turn on. One small dialog on the confirm chrome: the path box prefilled
// with the current folder, the owning kernel's verdict for what is typed (the create picker's dirComplete
// op, asked with NEGATIVE reqIds so the picker's own completer never claims these answers), Move/Cancel.
// Acknowledged at once (the button reads "Moving…"), CLOSED by the kernel's typed `moved`, and on a typed
// `moveFailed` left open with the reason where the path is — never a silent nothing. A 90s backstop covers
// a dormant session, which the kernel revives first (it waits up to 60s for that CLI to come up).
let movePrompt: { sid: string; overlay: HTMLElement; input: HTMLInputElement; hint: HTMLElement;
                  go: HTMLButtonElement; close: () => void; backstop?: number } | null = null;
let moveDirReq = 0;

function closeMovePrompt(): void {
  if (!movePrompt) return;
  if (movePrompt.backstop !== undefined) clearTimeout(movePrompt.backstop);
  const p = movePrompt; movePrompt = null;
  p.close();
}

function showMovePrompt(sid: string): void {
  closeMovePrompt();
  const sess = sessions.get(sid);
  const overlay = el("div", "picker-overlay confirm-overlay"); overlay.id = "move-prompt";
  const box = el("div", "picker-box confirm-box");
  const h = el("div", "confirm-title"); h.textContent = `Move “${sess?.name || "session"}” to a folder`;
  const d = el("div", "confirm-detail");
  d.textContent = "The conversation, name, mail and history stay with the session. From its next turn on, its tools, CLAUDE.md and project settings come from the new folder.";
  const input = document.createElement("input");
  input.type = "text"; input.className = "fork-name move-dir"; input.value = sess?.cwd || "";
  input.placeholder = "folder path (~ and $VARs expand)";
  input.setAttribute("autocapitalize", "off"); input.setAttribute("autocomplete", "off");
  input.setAttribute("autocorrect", "off"); input.setAttribute("spellcheck", "false");
  const hint = el("div", "move-dir-hint");   // the kernel's one-line verdict for the typed path
  const actions = el("div", "confirm-actions");
  const cancel = el("button", "picker-action confirm-btn"); cancel.textContent = "Cancel";
  const go = el("button", "picker-action confirm-btn") as HTMLButtonElement; go.textContent = "Move";
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); closeMovePrompt(); } };
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey, true); };
  movePrompt = { sid, overlay, input, hint, go, close };
  const ask = () => {
    if (!vscodeApi) return;
    vscodeApi.postMessage({ type: "dirComplete", value: input.value.trim(), reqId: -(++moveDirReq), host: hostOf(sid) });
  };
  const start = () => {
    const dir = input.value.trim();
    if (!dir) { input.classList.add("bad"); input.focus(); return; }
    // acknowledge the click before the round trip (the repo's button rule); the kernel's typed reply
    // — moved / moveFailed — is what changes this dialog next
    go.disabled = true; go.textContent = "Moving…"; input.disabled = true;
    vscodeApi?.postMessage({ type: "moveSession", id: sid, dir });
    if (movePrompt) movePrompt.backstop = window.setTimeout(
      () => moveFailedLocal(sid, sess?.name || sid, "still waiting — the kernel has not answered; check the kernel log"), 90000);
  };
  cancel.addEventListener("click", closeMovePrompt);
  go.addEventListener("click", start);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); start(); } });
  input.addEventListener("input", () => { input.classList.remove("bad"); ask(); });
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeMovePrompt(); });
  actions.appendChild(cancel); actions.appendChild(go);
  box.appendChild(h); box.appendChild(d); box.appendChild(input); box.appendChild(hint); box.appendChild(actions);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  document.addEventListener("keydown", onKey, true);
  input.focus(); input.select();
  ask();   // vet the prefilled path straight away, like the picker does
}

// the owning kernel's verdict for the move dialog's path (a dirCompletions reply with our negative reqId)
function onMoveDirCompletions(m: any): void {
  if (!movePrompt || m.reqId !== -moveDirReq) return;   // a newer keystroke owns the field, or no dialog
  const s = m.status || null;
  // dirStatusHint's "can be created" verdict is the picker's offer; a move never creates, so a missing
  // folder is simply not there
  const said = s && !s.isDir && s.canCreate ? { text: "not found", cls: "bad", title: "A move goes to a folder that already exists." }
                                             : dirStatusHint(s);
  movePrompt.hint.textContent = said.text;
  movePrompt.hint.title = said.title;
  movePrompt.hint.className = "move-dir-hint" + (said.cls ? " " + said.cls : "");
  movePrompt.input.classList.toggle("bad", said.cls === "bad");
}

// the kernel's typed outcome: `moved` closes the dialog (a parked move that lands later says so in a
// toast, since the dialog may be long gone); `moveFailed` puts the reason where the path is
function moveLanded(sid: string, name: string, cwd: string): void {
  const s = sessions.get(sid);
  if (s && cwd) s.cwd = cwd;   // the kernel's push carries the same; this keeps the statusline honest meanwhile
  if (movePrompt && movePrompt.sid === sid) { closeMovePrompt(); return; }
  warnToast(`“${name}” now works in ${cwd || "its new folder"}`);
}

function moveFailedLocal(sid: string, name: string, text: string): void {
  const p = movePrompt;
  if (p && p.sid === sid) {
    if (p.backstop !== undefined) { clearTimeout(p.backstop); p.backstop = undefined; }
    p.go.disabled = false; p.go.textContent = "Move"; p.input.disabled = false;
    p.hint.textContent = text; p.hint.title = text; p.hint.className = "move-dir-hint bad";
    p.input.classList.add("bad"); p.input.focus();
    return;
  }
  warnToast(`Couldn’t move “${name}”: ${text}`);
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
    ? "A new session continues the conversation to just below this response; this one is untouched."
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

// ── COMMENT THREADS (the user 2026-08-13) ───────────────────────────────────────────────────────────
// Highlight a passage → "Comment" on the selection menu → a side conversation opens right there, in a
// pane-local popover anchored to the highlighted text. The kernel forks the session at the anchored
// message (the thread's agent holds exactly the context that produced the passage) and keeps the fork
// off the board; this side owns the anchoring: a <mark> over the exact text (re-found per render,
// whitespace-tolerantly — comments.ts) plus a per-turn badge that survives even when the rendered text
// drifts, so a thread is never unreachable. State lives in module maps keyed by sid/tid, so every
// re-render (the transcript rebuilds constantly) reapplies it — the openFolds pattern.
const commentThreads = new Map<string, CommentThread[]>();          // parent sid → last frame's threads
const commentPending = new Map<string, { text: string; t: number; imgPaths?: string[] }[]>(); // tid → optimistic sends (+ shipped image paths → echo thumbnails, the parity bundle 2026-08-26)
// image paths shipped into the OPEN popover's box (the droppedPath ack) and not yet sent — the next
// send attaches them to its pending entry so the echo renders the same thumbnails the chat's does
let cmtShippedImgs: string[] = [];
// THE SEND LATCH (T102, the user 2026-08-26; rescoped T237): tid → what the thread's projection held at
// the newest SEND gesture — its message count, its newest message's time, its agent-message count. Set
// at the gesture (create seeds zeros under the synth tid, transferred on adopt; a follow-up stamps its
// thread's counts), BEFORE any kernel round-trip. Against a kernel that ships replyOwed (T237) the latch
// covers ONLY the pre-round-trip instant: it clears once a frame's projection carries the send (a message
// newer than the click's newest, or more messages than then — the count alone misses a thread already at
// the projection's 40-message cap) and the kernel's owed bit owns the wash from there. Against an OLDER
// kernel (no bit) it keeps the T102 contract: cleared by the reply-arrived event — the agent's reply record
// landing (agentCount rising past the base) with the thread settled. No push counting, no timers.
type CmtLatch = { you: number; youT: number; agents: number; queued: number; last: string };
const cmtAwaitBase = new Map<string, CmtLatch>();
const cmtYouRows = (th: CommentThread) => (th.msgs || []).filter((m) => m.who === "you");
const cmtLatchOf = (th: CommentThread): CmtLatch => {
  const you = cmtYouRows(th);
  return { you: you.length, youT: you.length ? (you[you.length - 1].t || 0) : 0, agents: agentCount(th),
           queued: th.queued || 0, last: th.lastUuid || "" };
};
const CMT_LATCH_ZERO: CmtLatch = { you: 0, youT: 0, agents: 0, queued: 0, last: "" };
/** Does this frame's thread release the send latch? Against a kernel that ships replyOwed (T237), the
 *  KERNEL'S ACKNOWLEDGEMENT of the send does — any of: the user's own "you" row newer than the click's
 *  newest (or more "you" rows than then; the count alone never grows on a thread at the projection's
 *  40-message cap); the backend now holding or having fed the send (queued grew — its input echo lives from
 *  the send until the record lands, so this covers the mid-turn follow-up the backend queues AND the send
 *  the CLI has but has not written); the send consumed as a slash command (the newest record moved —
 *  lastUuid, cap-proof — while the kernel says nothing is owed); or the kernel marking the thread
 *  unreachable (a broken thread owes nothing). Never an agent row alone, and never "the transcript grew"
 *  alone: the agent's partials and the reply itself land BEFORE a held send is written, and clearing on
 *  them dropped the green while the send was still owed (round-2/4 review). Against an older kernel (no
 *  bit) the T102 reply-arrived clear stands. A thread leaving "open" or erroring releases either way. */
function cmtLatchReleased(t: CommentThread, base: CmtLatch): boolean {
  if (t.status !== "open" || !!t.error) return true;
  if (typeof t.replyOwed === "boolean") {
    const you = cmtYouRows(t);
    const newestYouT = you.length ? (you[you.length - 1].t || 0) : 0;
    if (you.length > base.you || newestYouT > base.youT) return true;   // written
    if ((t.queued || 0) > base.queued) return true;                       // held or fed by the backend — the kernel owes it now
    if (t.unreachable) return true;                                       // nothing can land — no green promise
    return !!t.lastUuid && t.lastUuid !== base.last && t.replyOwed === false;   // consumed as a command: moved, nothing owed
  }
  return agentCount(t) > base.agents && !threadBusy(t.state);
}
// Creates in flight (T106 lab find, 2026-08-26): a comment made seconds after a reply lands can be
// refused by the kernel's parse lag ("isn't in the transcript yet"). The payload holds here from the
// send; a TRANSIENT nack keeps the optimistic mark + latch alive and the create RE-POSTS when the
// next session frame for the sid arrives (frames are built from the kernel's parse — a new frame IS
// the parse catching up). Bounded by attempts, not time; a real refusal or the ack drops the hold.
const cmtCreateInFlight = new Map<string, { sid: string; uuid: string; exact: string; text: string;
  name: string; model: string; effort: string; fast: string; color: string; tries: number }>();
const CMT_CREATE_MAX_TRIES = 12;

function retryCmtCreates(sid: string): void {
  for (const [u, c] of Array.from(cmtCreateInFlight.entries())) {
    if (c.sid !== sid || c.tries < 1) continue;            // tries starts counting after the first transient nack
    if (c.tries > CMT_CREATE_MAX_TRIES) {                  // the can't-trap bound: give up honestly
      cmtCreateInFlight.delete(u);
      dropSynthThread(c.sid, u);
      warnToast("couldn't anchor the comment — the message never appeared in the kernel's transcript.");
      continue;
    }
    c.tries++;
    vscodeApi?.postMessage({ type: "commentCreate", id: c.sid, uuid: c.uuid, exact: c.exact,
      text: c.text, name: c.name, model: c.model, effort: c.effort, fast: c.fast, color: c.color });
  }
}

/** Drop a refused create's optimistic synth thread + latch + mark — the honest retreat. */
function dropSynthThread(sid: string, uuid: string): void {
  const tid = "pending:" + uuid;
  const cur = commentThreads.get(sid) || [];
  if (cur.some((t) => t.tid === tid)) commentThreads.set(sid, cur.filter((t) => t.tid !== tid));
  cmtAwaitBase.delete(tid);
  applyCommentMarks(sid);
}
// the one busy answer for the mark + rail tick: an in-flight EXCHANGE (the gesture latch above), or
// — after a reload lost the client latch — the exchange's own records still saying a reply is owed
// (msgs ending with the user's message). A stuck/errored/closed thread never pulses: green would lie.
// A thread whose trailing user message was ANSWERED BY A STOP (T138, found by lab phase 4e): the
// user interrupted the turn, so no reply record is coming for that send — replyOwed's
// trailing-user shape would otherwise hold the mark green forever. A plain until-the-next-send
// tombstone: the only event that can OWE a new reply is the user's next send gesture, which
// retires it (and latches the real base). A count-keyed first cut died in the lab: the CLI files
// the interrupt itself as a trailing user-kind record, so any record-shape re-derivation re-fires
// exactly like the flapping proxies the T102 rule bans — the gesture pair (stop sets, send
// clears) is the event-true form.
const cmtInterrupted = new Set<string>();
const commentInFlight = (th: CommentThread): boolean => {
  if (th.status !== "open" || !!th.error || threadStuck(th.state)) return false;
  if (cmtAwaitBase.has(th.tid)) return true;    // the pre-round-trip instant only: from the send click until a frame carries it
  // the kernel is the one truth for "a reply is still owed" (T237): it reads the thread's transcript with
  // the chat's own turn-end, so an intermediate record of a multi-record turn, a state flap between
  // records, or a frame that lost a client latch can no longer drop the green wash to yellow while the
  // thread is still responding. An older kernel ships no bit → the msgs-derived fallback.
  const owed = typeof th.replyOwed === "boolean" ? th.replyOwed : replyOwed(th);
  return owed && !cmtInterrupted.has(th.tid);
};
const commentDrafts = new Map<string, string>();                    // draft key → unsent popover text
// The popover-boot hold (fillCommentMsgs): tid → when the loader first held the list. Held until the
// thread's events land (event-based) or the backstop expires (never trap); a backstop timer refills
// the open popover so the fall-through actually paints.
const cmtBootSince = new Map<string, number>();
const CMT_BOOT_BACKSTOP_MS = 8000;
function cmtBootHolds(tid: string): boolean {
  const t0 = cmtBootSince.get(tid) ?? Date.now();
  if (!cmtBootSince.has(tid)) {
    cmtBootSince.set(tid, t0);
    window.setTimeout(() => { if (openCommentKey?.tid === tid) refillOpenCommentPop(); }, CMT_BOOT_BACKSTOP_MS + 50);
  }
  return Date.now() - t0 < CMT_BOOT_BACKSTOP_MS;
}
let openCommentKey: { sid: string; tid: string } | null = null;     // the open thread popover
let pendingCommentAnchor: { sid: string; uuid: string; exact: string;
  model?: string; effort?: string; fast?: string; color?: string } | null = null; // create mode (+ the thread's own picks)
let pendingAdoptTid: string | null = null;                          // commentCreated ack that beat its frame
let commentPopPos: { x: number; y: number } | null = null;

// the popover's own file picker (the user 2026-08-17: the attach clip, like the chat's) — files
// ship through the SAME dropFile flow; the droppedPath ack sees the open popover and lands there
const cmtFilePicker = document.createElement("input");
cmtFilePicker.type = "file";
cmtFilePicker.multiple = true;
cmtFilePicker.style.display = "none";
cmtFilePicker.addEventListener("change", () => {
  const sid = pendingCommentAnchor?.sid || openCommentKey?.sid || activeId;
  Array.from(cmtFilePicker.files || []).forEach((f) => shipFileToHost(f, sid));
  cmtFilePicker.value = "";
});
document.body.appendChild(cmtFilePicker);

function closeCommentPop(): void {
  document.getElementById("cmt-pop")?.remove();
  openCommentKey = null;
  pendingCommentAnchor = null;
}

// close on any press outside the popover (drafts persist in commentDrafts; reopening restores them).
// The model/effort dropdowns and the break-out dialog ride document.body, so popover containment
// can't see them: without the exemption, pressing a menu item closed the box on mousedown and nulled
// pendingCommentAnchor before the item's click could land the pick (the user 2026-08-18), and a
// press inside the break-out dialog stranded its Cancel the same way (Escape-cancel kept the box).
document.addEventListener("mousedown", (ev) => {
  const pop = document.getElementById("cmt-pop");
  if (!pop || pop.contains(ev.target as Node)) return;
  if ((ev.target as HTMLElement).closest?.(".meta-menu, #fork-prompt")) return;
  closeCommentPop();
}, true);

/** Re-anchor every thread of `sid` onto its rendered turn: the exact-text <mark> plus the turn badge.
 *  Idempotent and cheap (early-out when the session has no threads) — called after every payload that
 *  can rebuild transcript DOM, and after each comments frame. */
function applyCommentMarks(sid: string): void {
  const v = views.get(sid);
  if (!v) return;
  applyBranchChips(sid, v);   // same driver, same hooks: branch chips re-anchor with the marks
  applyForkSpots(sid, v);     // and the below-response fork spots (same reason: this DOM rebuilds constantly)
  if (sid === activeId) updateCommentRail();   // and the scroll-rail ticks follow the active view
  const threads = commentThreads.get(sid) || [];
  const have = new Set(threads.map((t) => t.tid));
  for (const old of Array.from(v.el.querySelectorAll("mark.cmt-hl"))) {
    if (!have.has((old as HTMLElement).dataset.tid || "")) unwrapCommentMark(old as HTMLElement);
  }
  // the rail cue clears first (idempotent re-apply): a thread viewed, resolved, or removed must
  // drop its turn's tint on this very pass, not linger until the next anchor match
  for (const t of Array.from(v.el.querySelectorAll(".turn.cmt-rail-unread"))) t.classList.remove("cmt-rail-unread");
  if (!threads.length) return;
  for (const [uuid, list] of threadsByAnchor(threads)) {
    const turn = v.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`) as HTMLElement | null;
    if (!turn) continue;                       // windowed out — the mark returns when the turn does
    for (const th of list) ensureCommentMark(turn, th);
    // the turn's RAIL segment shouts the new-here state too (the user 2026-08-23: the corner dot is
    // easy to miss — the identity line's own segment tints comment-yellow while any thread on this
    // turn is unread, and clicking the rail opens it). Cleared above the moment it's viewed —
    // openCommentPopover drops the unread flag and re-runs this pass.
    turn.classList.toggle("cmt-rail-unread", list.some((t) => !!t.unread && t.status === "open"));
  }
}

/** The parent side of a branch (the user 2026-08-13): a small "↳ <name>" chip on the turn a fork
 *  departed from, deep-linking into the branched session at its divider. Idempotent, applied on the
 *  same hooks as the comment marks (the DOM it lives in rebuilds constantly). */
function applyBranchChips(sid: string, v: View): void {
  const kids = (sessions.get(sid)?.branches || []) as { sid: string; name: string; cut: string }[];
  for (const box of Array.from(v.el.querySelectorAll(".branch-chips"))) {
    for (const c of Array.from(box.children)) {
      if (!kids.some((k) => k.sid === (c as HTMLElement).dataset.sid)) c.remove();
    }
    if (!box.children.length) box.remove();
  }
  for (const k of kids) {
    if (!k.cut) continue;
    const turn = v.el.querySelector(`.turn[data-uuid="${cssEscape(k.cut)}"]`) as HTMLElement | null;
    if (!turn || turn.querySelector(`.branch-chip[data-sid="${cssEscape(k.sid)}"]`)) continue;
    turn.classList.add("has-cmt");             // the comment badge's positioning contract
    let box = turn.querySelector(":scope > .branch-chips") as HTMLElement | null;
    if (!box) { box = el("div", "branch-chips"); turn.appendChild(box); }
    const chip = el("button", "branch-chip") as HTMLButtonElement;
    chip.type = "button";
    chip.dataset.act = "branchjump";
    chip.dataset.sid = k.sid;
    chip.dataset.cut = "branch:" + k.cut;      // the child's divider carries this uuid — land on it
    chip.textContent = "↳ " + (k.name || "fork");
    chip.title = "A session branched from this message: " + (k.name || k.sid) + ". Click to open it there.";
    box.appendChild(chip);
  }
}

/** The FORK affordance lives BELOW the response it branches from (the user 2026-08-19: forking
 *  conceptually cuts under the response — the old button rode the NEXT prompt's msg-acts row, where
 *  it read as acting on that message). A hover-revealed "fork" under the LAST assistant bubble of
 *  each response run. The CUT is unchanged: the first genuine editable prompt after the run — exactly
 *  the uuid the old bubble button passed, so _rewind_target resolves it the same way — and the tip
 *  run, with no prompt after it, forks the whole conversation (uuid "", the palette's fork-from-tip).
 *  Idempotent and applied on the marks' hooks, like the branch chips (this DOM rebuilds constantly);
 *  a windowed-out anchor turn simply has no spot until it returns. */
function applyForkSpots(sid: string, v: View): void {
  const s = sessions.get(sid);
  const evs = (s?.events || []) as ChatEvent[];
  const editable = (s as any)?._editable as Set<string> | undefined;
  const spots = new Map<string, string>();   // last-assistant-of-run uuid -> cut uuid ("" = whole conversation)
  let run: string | null = null;             // the newest assistant uuid whose run has no cut yet
  for (const ev of evs) {
    if (ev.kind === "assistant" && ev.uuid) run = ev.uuid;
    else if (ev.kind === "user" && ev.uuid && run && !spots.has(run)
             && senderKind(ev) === "user" && editable?.has(ev.uuid)) {
      spots.set(run, ev.uuid);   // the FIRST prompt after the run = the cut just below its response
    }
  }
  if (run && !spots.has(run)) spots.set(run, "");   // the tip run: nothing follows -> whole conversation
  for (const old of Array.from(v.el.querySelectorAll(".fork-spot")) as HTMLElement[]) {
    // closest, not parentElement: the spot may nest inside the turn's elapsed row (below)
    const anchor = (old.closest(".turn-assistant") as HTMLElement | null)?.dataset.uuid || "";
    if (spots.get(anchor) !== old.dataset.cut) old.remove();   // gone, or its cut moved
  }
  for (const [anchor, cut] of spots) {
    const turn = v.el.querySelector(`.turn-assistant[data-uuid="${cssEscape(anchor)}"]`) as HTMLElement | null;
    if (!turn || turn.querySelector(".fork-spot")) continue;
    const row = el("div", "fork-spot");
    row.dataset.cut = cut;
    const fk = el("button", "msg-fork") as HTMLButtonElement;
    fk.type = "button";
    fk.textContent = "fork";
    fk.dataset.act = "forkspot";   // delegated (click-safe): the transcript rebuilds on every push
    fk.title = cut
      ? "Fork the session from just below this response — a new parallel session carries the conversation to here; this one is untouched"
      : "Fork the session — a new parallel session continues this whole conversation; this one is untouched";
    row.appendChild(fk);
    // INLINE with the "worked …" seconds label when the turn has one (the user 2026-08-25: not on a
    // new row — just to the right of it). The elapsed row is the flex host; a turn with no footer
    // (the live tip) keeps the button on its own row exactly as before.
    const elapsed = turn.querySelector(":scope > .turn-elapsed") as HTMLElement | null;
    if (elapsed) elapsed.appendChild(row);
    else turn.appendChild(row);
  }
}

/** Yellow ticks on the chat's scroll rail marking commented spots (the user 2026-08-15) — one per
 *  open/resolved thread of the ACTIVE session, clicking jumps there and opens the thread. Placed in
 *  the SAME content-space frame as the message notches (contentOffsetFrame): the old uniform
 *  index-fraction percents were a second frame that drifted past the notches as history loaded and
 *  real turn heights diverged from uniform (the user 2026-08-17) — two marks on one scrollbar may
 *  never disagree about order. Rides the notches' rAF scheduler so both repaint from one world; a
 *  signature skips untouched paints, and an unchanged tick set moves IN PLACE (same tids, same
 *  order) so a mid-press rebuild can't eat the click (these ticks are buttons). */
let cmtRailSig = "";
function updateCommentRail(): void {
  let rail = document.getElementById("cmt-rail");
  const content = document.getElementById("content");
  const v = activeId ? views.get(activeId) : null;
  const s = activeId ? sessions.get(activeId) : null;
  const threads = ((activeId && commentThreads.get(activeId)) || [])
    .filter((t) => t.status === "open" || t.status === "resolved" || t.status === "merged");
  if (!content || !v || !s || !threads.length || v.el.style.display === "none") { rail?.remove(); cmtRailSig = ""; return; }
  const r = content.getBoundingClientRect();
  if (!r.height) { rail?.remove(); cmtRailSig = ""; return; }              // hidden pane
  const frame = contentOffsetFrame(content, v, s);
  if (!frame) { rail?.remove(); cmtRailSig = ""; return; }
  const ticks: Array<{ th: CommentThread; y: number }> = [];
  const evUnit = eventUnitIndex(s);                       // anchors are EVENTS; the frame speaks UNITS
  for (const th of threads) {
    const idx = s.events.findIndex((e) => e.uuid === th.anchorUuid);
    if (idx < 0 || evUnit[idx] < 0) continue;
    const off = frame.offsetOf(evUnit[idx]);
    if (off == null) continue;
    // the same proportional map the notches use, clamped so the last tick stays on the strip
    ticks.push({ th, y: Math.min(r.height - 6, Math.round((off / frame.sh) * (r.height - 4))) });
  }
  const sig = activeId + "|" + Math.round(r.top) + "," + Math.round(r.right) + "," + Math.round(r.height)
    + "|" + ticks.map((t) => t.th.tid + ":" + t.y + ":" + t.th.status + ":" + (t.th.unread ? 1 : 0)
                            + ":" + (commentInFlight(t.th) ? 1 : 0)).join(",");
  if (sig === cmtRailSig && rail) return;
  cmtRailSig = sig;
  if (!rail) { rail = el("div", "cmt-rail"); rail.id = "cmt-rail"; document.body.appendChild(rail); }
  rail.style.left = (r.right - 10) + "px";
  rail.style.top = r.top + "px";
  rail.style.height = r.height + "px";
  const cls = (th: CommentThread) => "cmt-tick" + (th.status === "resolved" || th.status === "merged" ? " resolved" : "")
    + (th.unread && th.status === "open" ? " unread" : "")
    + (commentInFlight(th) ? " busy" : "");   // green while working OR a reply is owed (2026-08-24)
  const kids = Array.from(rail.children) as HTMLElement[];
  if (kids.length === ticks.length && kids.every((k, i) => k.dataset.tid === ticks[i].th.tid)) {
    ticks.forEach((t, i) => { kids[i].style.top = t.y + "px"; kids[i].className = cls(t.th); });
    return;
  }
  rail.replaceChildren(...ticks.map((t) => {
    const tick = el("button", cls(t.th)) as HTMLButtonElement;
    tick.type = "button";
    tick.dataset.act = "cmtjump";
    tick.dataset.tid = t.th.tid;
    tick.dataset.uuid = t.th.anchorUuid;
    tick.style.top = t.y + "px";
    tick.title = (t.th.name || "comment") + ": click to jump to it";
    return tick;
  }));
}
window.addEventListener("resize", () => updateCommentRail());

// (The per-turn count badge is GONE — the user 2026-08-17: the highlight does the speaking, and
// the scroll-rail tick already covers a thread whose rendered text drifted beyond re-matching.)

function unwrapCommentMark(markEl: HTMLElement): void {
  const p = markEl.parentNode;
  if (!p) return;
  if (p instanceof Element) {
    p.classList.remove("cmt-hl-host");                             // an inline-code span it tinted
    (p.closest(".katex.cmt-hl-host") as HTMLElement | null)?.classList.remove("cmt-hl-host");   // or the math block
  }
  while (markEl.firstChild) p.insertBefore(markEl.firstChild, markEl);
  markEl.remove();
  p.normalize();
}

function ensureCommentMark(turn: HTMLElement, th: CommentThread): void {
  const sel = `mark.cmt-hl[data-tid="${cssEscape(th.tid)}"]`;
  if (!turn.querySelector(sel)) {
    const body = turn.querySelector(".md") as HTMLElement | null;
    if (!body) return;                          // no prose body (a tool row) — the badge carries it
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let n: Node | null;
    while ((n = walker.nextNode())) nodes.push(n as Text);
    // full match, or the longest prefix that lives in THIS turn (a cross-message selection anchors
    // to its first turn) — so the comment is visible in context, not just on the tiny badge
    const r = findAnchorRange(nodes.map((t) => t.data).join(""), th.exact);
    if (!r) return;                             // rendered text drifted — the badge still reaches it
    for (const sl of sliceRanges(nodes.map((t) => t.data.length), r.start, r.end)) {
      const t = nodes[sl.idx];
      const mid = sl.s > 0 ? t.splitText(sl.s) : t;
      if (sl.e - sl.s < mid.data.length) mid.splitText(sl.e - sl.s);
      const m = document.createElement("mark");
      m.className = "cmt-hl";
      m.dataset.tid = th.tid;
      m.dataset.act = "cmtopen";
      mid.parentNode?.insertBefore(m, mid);
      m.appendChild(mid);
    }
  }
  // One unbroken stroke. The wrap pass is hole-free by construction (sliceRanges covers every text
  // node of the contiguous match range, interior whitespace included — verified against a DOM port,
  // 2026-08-13), so the word-island look had two OTHER causes: per-segment corner rounding (the
  // radius now sits only on the run's outer ends) and inline-code PADDING — a mark inside a <code>
  // span cannot paint the element's own padded background, leaving an untinted sliver around every
  // code word. When a segment covers a code span's whole text, tint the span itself (cmt-hl-host).
  const segs = Array.from(turn.querySelectorAll(sel)) as HTMLElement[];
  for (let i = 0; i < segs.length; i++) {
    styleCommentMark(segs[i], th);
    segs[i].classList.toggle("hl-first", i === 0);
    segs[i].classList.toggle("hl-last", i === segs.length - 1);
    // hosts a mark can't paint from inside: an inline-code span's padding, and KaTeX's inter-glyph
    // spacing (the user 2026-08-15, screenshot: math highlighted with gaps punched at every glyph
    // box) — tint the ELEMENT instead; resolved threads drop the tint like the marks dim
    const host = segs[i].parentElement;
    if (host && host.tagName === "CODE" && host.parentElement?.tagName !== "PRE"
        && host.childNodes.length === 1) {
      host.classList.toggle("cmt-hl-host", th.status !== "resolved" && th.status !== "merged");
    }
    const kat = segs[i].parentElement?.closest(".katex") as HTMLElement | null;
    if (kat) kat.classList.toggle("cmt-hl-host", th.status !== "resolved" && th.status !== "merged");
  }
}

function styleCommentMark(m: HTMLElement, th: CommentThread): void {
  m.classList.toggle("resolved", th.status === "resolved" || th.status === "merged");
  m.classList.toggle("unread", !!th.unread && th.status === "open");
  m.classList.toggle("busy", commentInFlight(th));
  m.title = th.status === "promoted" ? "thread, now the session '" + th.promotedName + "'"
    : th.status === "merged" ? "relayed thread: its discussion was sent back into the session"
    : th.status === "resolved" ? "resolved thread: closed — click to read; a new comment continues the conversation"
    : "thread: click to open";
}

/** The comment's identity color (the user 2026-08-17): one of the session palette's colors,
 *  DISTINCT from the parent session's and, where the palette allows, from every open tab's —
 *  the same standing-out rule _pick_identity_color applies to new sessions, decided client-side
 *  so the dialog can wear it before the thread exists. */
function pickThreadColor(sid: string): string {
  const parent = (sessions.get(sid)?.color?.bg || "").toLowerCase();
  const worn = new Set<string>();
  sessions.forEach((se) => { const c = (se.color?.bg || "").toLowerCase(); if (c) worn.add(c); });
  (commentThreads.get(sid) || []).forEach((t) => { if (t.color) worn.add(t.color.toLowerCase()); });
  const free = paletteColors.find((c) => !worn.has(c.toLowerCase()) && c.toLowerCase() !== parent);
  return free || paletteColors.find((c) => c.toLowerCase() !== parent) || "#e8b220";
}

function openCommentComposer(sid: string, uuid: string, exact: string, x: number, y: number): void {
  pendingCommentAnchor = { sid, uuid, exact, color: pickThreadColor(sid) };
  openCommentKey = null;
  commentPopPos = { x, y };
  renderCommentPopover();
}

function openCommentPopover(sid: string, tid: string, _x?: number, _y?: number): void {
  openCommentKey = { sid, tid };
  pendingCommentAnchor = null;
  // click coords no longer seed the position (the user 2026-08-25): a THREAD popover opens at the
  // fixed right-aligned geometry below; only a real drag (commentPopPos) parks it elsewhere. The
  // CREATE composer (openCommentComposer) still opens at the selection point — a different gesture.
  vscodeApi?.postMessage({ type: "commentSeen", id: sid, tid });
  const th = (commentThreads.get(sid) || []).find((t) => t.tid === tid);
  if (th) th.unread = false;                    // optimistic; the kernel's watermark reconciles
  renderCommentPopover();
  applyCommentMarks(sid);
}

/** commentCreated's adoption: swap the create popover for the named thread's (never a guess — the
 *  kernel sends the frame first, then the ack naming the tid). When the frame hasn't landed yet
 *  (a dropped/reordered leg), the tid parks in pendingAdoptTid and the next frame adopts it. */
function adoptCommentThread(sid: string, tid: string): void {
  // the create's gesture latch carries onto the real thread (the synth tid retires with the anchor)
  for (const k of Array.from(cmtAwaitBase.keys())) {
    if (k.startsWith("pending:")) { cmtAwaitBase.set(tid, cmtAwaitBase.get(k)!); cmtAwaitBase.delete(k); }
  }
  if (pendingCommentAnchor && pendingCommentAnchor.sid === sid) {
    commentDrafts.delete("new:" + pendingCommentAnchor.uuid);
    commentDrafts.delete("newname:" + pendingCommentAnchor.uuid);
    pendingCommentAnchor = null;
  }
  pendingAdoptTid = null;
  openCommentKey = { sid, tid };
  vscodeApi?.postMessage({ type: "commentSeen", id: sid, tid });
  renderCommentPopover();
  applyCommentMarks(sid);
}

/** The popover's unconfirmed sends, rendered through the CHAT'S OWN queued idiom — renderQueued's
 *  bare optimistic group, the exact component an unconfirmed chat send wears (T104, the user
 *  2026-08-26: the thread-local echo was a washed-gray one-off pill, the "third look" the chat
 *  killed 2026-07-16 reborn in the popover; "I really want to be inheriting all the stuff for how
 *  the chat normally renders"). Inherited, never restyled: the dashed bubble, the sent-just-now
 *  title, the no-header bare form all come from the one code path. */
function cmtPendingQueued(pend: { text: string; t: number; imgPaths?: string[] }[]): HTMLElement {
  return renderQueued({ kind: "queued", bare: true,
    texts: pend.map((p) => ({ md: p.text, optimistic: true, cancelable: false, imgPaths: p.imgPaths })),
    uuid: OPT_PREFIX + pend[0].t } as Extract<ChatEvent, { kind: "queued" }>);
}

function commentMsgEl(who: "you" | "agent", text: string): HTMLElement {
  const n = el("div", "cmt-msg " + who);
  if (who === "agent") n.innerHTML = md(text);
  else n.textContent = text;
  return n;
}

/** The open thread + parent sid behind the popover, resolved fresh (delegated handlers must never
 *  close over a stale thread object). */
function openCommentThread(): { sid: string; th: CommentThread } | null {
  if (!openCommentKey) return null;
  const th = (commentThreads.get(openCommentKey.sid) || []).find((t) => t.tid === openCommentKey!.tid);
  return th ? { sid: openCommentKey.sid, th } : null;
}

/** The popover's conversation area, (re)filled in place — shared by the full build and the
 *  frame-driven refresh so an update never rebuilds the composer under the user's caret. */
/** The OWNING session of a control's DOM at click time: the thread root (chat views) or the
 *  popover's list (stamped with the THREAD sid below) — so in-turn controls rendered through the
 *  shared path (queued ✕, api-error Retry) act on the session that owns them, never on whichever
 *  tab is active (the parity bundle, 2026-08-26; the render-time twin is renderingOwnerSid). */
function owningSidOf(el0: HTMLElement | null): string | null {
  return (el0?.closest("[data-session]") as HTMLElement | null)?.dataset.session || activeId;
}

// The persistent sent-back marker (T145): where the last relay CUT the thread — everything above
// it went back to the main conversation; everything below is the new tail a later relay would
// send. From the store (relayedT rides the comments frame), so it survives reopens; evidence-time
// stamp, rendered in the rail's own HH:MM.
function cmtRelayedNote(t: number): HTMLElement {
  const n = el("div", "cmt-relayed-note");
  n.textContent = `↩ sent back to the main conversation · ${markerLabel(t, null, Date.now()).hm}`;
  n.title = "the discussion above was relayed into the session; anything below goes with the next relay";
  return n;
}

function fillCommentMsgs(list: HTMLElement, th: CommentThread, sid: string): void {
  list.dataset.session = th.tid;   // the THREAD owns its queue/errors — in-turn controls resolve to it
  const prevScroll = list.scrollTop;
  // the slack rule (the user 2026-08-25, same word as the chat's appendActive): while the thread's
  // content hasn't overflowed the fixed box, streaming writes in place — no follow, no jump; once
  // overflowing, the at-tail stick behaves as before
  const overflowed = list.scrollHeight > list.clientHeight + 2;
  const atTail = overflowed && list.scrollTop >= list.scrollHeight - list.clientHeight - 8;
  list.replaceChildren();
  // pruned against BOTH projections (T106 lab, screenshot: the follow-up double-showed — its
  // landed user TURN rendered from th.events while the echo, pruned only against th.msgs, waited
  // for the slower projection to catch up). A user event's md is the same landed fact.
  const evUserMsgs = ((th.events || []) as ChatEvent[])
    .filter((e): e is Extract<ChatEvent, { kind: "user" }> => e.kind === "user" && typeof (e as { md?: string }).md === "string")
    .map((e) => ({ who: "you" as const, text: e.md, t: 0 }));
  const pend = prunePending(commentPending.get(th.tid) || [], [...th.msgs, ...evUserMsgs]);
  commentPending.set(th.tid, pend);
  const evs = (th.events || []) as ChatEvent[];
  // ONE final format (the user 2026-08-25: a fresh thread flashed the plain msgs projection for a
  // couple of seconds before jumping into the chat rendering): until the thread's REAL render — its
  // events — is ready, the conversation area holds the standard romp loader, and the chat-parity
  // render appears once. Event-based fade (the comments frame carrying events refills this list);
  // the backstop falls through to the msgs projection so a thread whose events never come (an old
  // kernel) can't trap the loader.
  // A CONTENTLESS open thread NEVER renders blank (T152, the user 2026-08-28, live specimen: a
  // fork of a 40MB parent took 200+ seconds to spend across kernel restarts, the 8s boot backstop
  // expired, and the popover showed the quote floating over nothing — 'nothing shows up when I
  // click on it'). While BOTH projections are empty on an open, error-free thread, the loader
  // stays; past the backstop it swaps to an honest slower message (the backstop changes the LABEL,
  // never to blank — the error and stuck branches below still take over the moment the frame says
  // so, and any content arriving replaces this whole render, all event-keyed).
  if (!evs.length && !th.msgs.length && th.status === "open" && !th.error) {
    const slow = !cmtBootHolds(th.tid);
    const boot = el("div", "cmt-boot");
    boot.appendChild(rompLoaderInner(
      slow ? "still opening — the thread's session is taking longer than usual…" : "opening the thread…",
      { wordmark: false }));
    list.appendChild(boot);
    if (pend.length) list.appendChild(cmtPendingQueued(pend));
    list.scrollTop = list.scrollHeight;
    return;
  }
  if (evs.length) cmtBootSince.delete(th.tid);
  if (evs.length) {
    // the CHAT's own renderer, from the branch point on (the user 2026-08-17: the same component —
    // rail dots, markdown, tool folds, notice cards — never a simplified twin). renderingSid keys
    // the folds per thread, exactly as a chat tab would.
    const saved = renderingSid;
    const savedOwner = renderingOwnerSid;
    renderingSid = th.tid;
    renderingOwnerSid = sid;   // fold keys are per-thread; file/preview URLs belong to the thread's SESSION
    renderingIntoThread = true;   // same renderer, minus the transcript-coupled hover chrome (see the flag)
    let prev: number | null = null;
    let quoteHost: HTMLElement | null = null;   // the thread's OPENING message — the quote's home
    // the SAME display units the chat renders (the user 2026-08-24, leg C: the popover ignored the
    // compact/hide-thinking setting — thinking blocks and raw tool runs showed regardless of the
    // gear): thinking dropped, consecutive tools folded to the chat's own renderToolGroup line,
    // expansion keyed by the SHARED expandedGroups store so a toggle survives refills. The group
    // toggle and a settings flip both refill the open popover live (toggleToolGroup / setupSettings
    // → refillOpenCommentPop).
    const items: DisplayItem[] = settings.compact
      ? compactDisplay(evs.map((e) => e.kind), evs.map((e) => e.kind === "tool" ? e.name : undefined))
      : evs.map((_, i) => ({ kind: "event", index: i } as DisplayItem));
    const thWorking = threadBusy(th.state);
    let relayNoted = !th.relayedT;   // T145: drop the sent-back marker at its place in time, once
    for (const it of items) {
      // a new day opens with the chat's own divider (the parity bundle, 2026-08-26) — same helper,
      // same placement idiom as appendItem
      const dayOpen = eventEpoch(evs[itemFirstEvent(it)]);
      if (!relayNoted && dayOpen != null && dayOpen > (th.relayedT || 0)) {
        list.appendChild(cmtRelayedNote(th.relayedT || 0));
        relayNoted = true;
      }
      if (dayOpen != null) {
        const dv = dayDividerFor(dayOpen, prev);
        if (dv) list.appendChild(dv);
      }
      if (it.kind === "toolgroup" || it.kind === "retrygroup") {
        const run = it.indices.map((ix) => evs[ix]);
        const key = it.kind === "toolgroup" ? toolGroupKey(run[0]) : retryGroupKey(run[0]);
        const open = expandedGroups.has(key);
        list.appendChild(it.kind === "toolgroup"
          ? renderToolGroup(run as Extract<ChatEvent, { kind: "tool" }>[], prev, key, open)
          : renderRetryGroup(run as Extract<ChatEvent, { kind: "retried" }>[], prev, key, open));
        if (open) {
          it.indices.forEach((ix, j) => {   // the run's own members — it.indices already excludes thinking
            const child = renderEvent(evs[ix], prev, turnWorkedSecs(evs, ix, thWorking));
            child.classList.add("tg-child"); if (j === it.indices.length - 1) child.classList.add("tg-last");
            list.appendChild(child);
            const ep = eventEpoch(evs[ix]); if (ep != null) prev = ep;
          });
        } else {
          const ep = eventEpoch(run[run.length - 1]); if (ep != null) prev = ep;
        }
        continue;
      }
      const ev = evs[it.index];
      // the "worked Ns" footer rides exactly as in the chat (the parity bundle) — the same
      // turnWorkedSecs, with the thread session's own busy reading standing in for `working`
      const node = renderEvent(ev, prev, turnWorkedSecs(evs, it.index, thWorking));
      list.appendChild(node);
      if (!quoteHost && ev.kind === "user") quoteHost = node;
      const ep = eventEpoch(ev);
      if (ep != null) prev = ep;
    }
    if (!relayNoted) list.appendChild(cmtRelayedNote(th.relayedT || 0));   // relay at the tail — nothing new after it yet
    renderingIntoThread = false;
    renderingSid = saved;
    renderingOwnerSid = savedOwner;
    // The quoted passage renders as CONTEXT attached to the thread's opening message (the user
    // 2026-08-24): it used to sit as a standalone block ABOVE the whole list — above the branch
    // divider too, misreading chronology, since the branch happened before the quote. Same idiom
    // as the chat's citation-as-context. The standalone block only mints while the events haven't
    // landed yet (openCommentPop), so it is swept here the moment they have — otherwise the frame's
    // arrival left BOTH on screen, quote on top, "branched" below it.
    if (th.exact && quoteHost) {
      const ctx = el("div", "cmt-quote cmt-quote-ctx");
      ctx.textContent = th.exact;
      ctx.title = "the highlighted passage this thread is about";
      quoteHost.insertBefore(ctx, quoteHost.firstChild);
      list.closest(".cmt-pop")?.querySelector(":scope > .cmt-quote")?.remove();
    }
  } else for (const m of th.msgs) list.appendChild(commentMsgEl(m.who, m.text));
  if (pend.length) list.appendChild(cmtPendingQueued(pend));
  // (the typing dots that rendered here while the thread was busy are RETIRED — the user 2026-08-24:
  // the await-green highlight carries the in-flight signal, and the reply's arrival is announced by
  // the green→yellow settle; the pending bubble still acknowledges the user's own send)
  if (th.status === "open" && threadStuck(th.state)) {
    const note = el("div", "cmt-note");
    note.textContent = "This thread hit a prompt it can't answer from here. Break it out to continue.";
    list.appendChild(note);
  }
  if (th.status === "open" && th.error) {
    // the CLI behind this thread could not start — dots pulsing forever would be a lie
    const note = el("div", "cmt-note cmt-err");
    note.textContent = th.error;
    list.appendChild(note);
  }
  list.scrollTop = atTail ? list.scrollHeight : prevScroll;
}

// Refill the OPEN popover's conversation in place (leg C, the user 2026-08-24): the popover renders
// the chat's display units, so everything that re-renders the chat's units — a tool-group toggle, a
// compact/settings flip — must refill the popover too, or it holds the stale shape until the next
// comments frame.
function refillOpenCommentPop(): void {
  if (!openCommentKey) return;
  const th = (commentThreads.get(openCommentKey.sid) || []).find((t) => t.tid === openCommentKey!.tid);
  const list = document.querySelector(".cmt-pop .cmt-msgs") as HTMLElement | null;
  if (th && list) fillCommentMsgs(list, th, openCommentKey.sid);
}

/** The open thread's status, in the chat's own Status shape — what the SHARED statusline builders
 *  (syncMetaControls / toggleMetaMenu) consume, so the popover renders the chat statusline's full
 *  element set (mode · model · effort · fast; the user 2026-08-25) through the one code path.
 *  metaPending's switching-dots ride the same keys, sid-scoped. */
function threadMetaStatus(th: CommentThread): Status {
  const stuck = threadStuck(th.state);
  return { state: stuck ? "needsInput" : (threadBusy(th.state) ? "working" : "ready"),
           sinceEpoch: th.sinceEpoch || null, mode: th.mode || "", model: th.model || "",
           effort: th.effort || "default", fast: th.fast || "", backend: "sdk",
           modelColor: th.modelColor, effortColor: th.effortColor,
           modelTone: (th as any).modelTone, effortTone: (th as any).effortTone } as Status;
}

/** The popover statusline's LEFT half — the thread's state chip, wearing exactly the chat's chip
 *  anatomy (chip-working + pulse + counting timer, retrying, compacting-line, Blocked, Ready), so
 *  the popover says it's working the way the main chat does (the user 2026-08-25: no indication it
 *  was working, no counting timer). Rebuilt in place per comments frame — chips carry no listeners,
 *  so the swap is click-safe. */
function cmtStateChip(th: CommentThread): HTMLElement {
  const wrap = el("span", "cmt-state");
  wrap.id = "cmt-state";
  if (th.status !== "open") return wrap;                    // closed threads carry their status in the title
  const st = th.state || "";
  // the stop square, exactly the chat statusline's affordance owner-scoped to the THREAD (T138,
  // the user 2026-08-27: threads couldn't be interrupted from the UI at all — the popover chip
  // rendered working/retrying with no stop). data-act rides the stable document.body delegate
  // (this statusline rebuilds per comments frame — a direct listener would be press-unsafe), and
  // data-sid pins the target to the thread's own session, never the active tab.
  const stopBtn = () => {
    const b = el("button", "stop-btn cmt-stop");
    (b as HTMLButtonElement).type = "button";
    b.title = "Stop — interrupt this thread's turn";
    b.setAttribute("aria-label", "Interrupt this thread");
    b.dataset.act = "cmtinterrupt";
    b.dataset.sid = th.tid;
    b.appendChild(el("span", "stop-icon"));
    return b;
  };
  if (st === "working") {
    const chip = el("span", "chip chip-working");
    const label = el("span", "chip-pulse");
    label.textContent = CHIP_LABEL.working;
    chip.appendChild(label);
    const timer = el("span", "status-timer");
    timer.id = "cmt-work-timer";
    timer.textContent = elapsedMs(th.sinceEpoch || null);
    wrap.append(chip, timer, stopBtn());
  } else if (st === "retrying") {
    const chip = el("span", "chip chip-retrying");
    chip.textContent = CHIP_LABEL.retrying;
    wrap.append(chip, stopBtn());
  } else if (st === "compacting") {
    const c = el("span", "compacting-line");
    c.textContent = "⟳ Compacting context…";
    wrap.appendChild(c);
  } else if (threadStuck(st)) {
    const chip = el("span", "chip chip-needsInput");
    chip.textContent = CHIP_LABEL.needsInput;
    wrap.appendChild(chip);
  } else {
    const chip = el("span", "chip chip-ready");
    chip.textContent = CHIP_LABEL.ready;
    wrap.appendChild(chip);
  }
  return wrap;
}

/** Resize from ANY edge or corner, macOS-style (the user 2026-08-25: "only from the little thing
 *  at the bottom right — I should be able to grab any edge and pull"). An 8px band on each side is
 *  a live handle with the platform cursor (ew/ns/nwse/nesw); each pull moves its own edge with the
 *  opposite edge anchored, clamped to the CSS min-size. The bottom-right corner is deliberately
 *  left to the native resize grip (it keeps working exactly as before). Pointer capture keeps fast
 *  pulls on the edge; a north/west pull writes the new position through to commentPopPos, the same
 *  persistence the drag has. Installed once per popover element (the element survives in-place
 *  refreshes — the click-safety discipline). */
function wireEdgeResize(pop: HTMLElement): void {
  const EDGE = 8;
  const MIN_W = 300, MIN_H = 120;   // the .cmt-pop CSS mins, restated for the math
  const zoneAt = (ev: PointerEvent): string => {
    const r = pop.getBoundingClientRect();
    if (ev.clientX > r.right - 18 && ev.clientY > r.bottom - 18) return "";   // the native grip's corner
    const n = ev.clientY - r.top < EDGE, so = r.bottom - ev.clientY < EDGE;
    const w = ev.clientX - r.left < EDGE, e = r.right - ev.clientX < EDGE;
    return (n ? "n" : so ? "s" : "") + (w ? "w" : e ? "e" : "");
  };
  const CUR: Record<string, string> = { n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
                                        ne: "nesw-resize", sw: "nesw-resize", nw: "nwse-resize", se: "nwse-resize" };
  pop.addEventListener("pointermove", (ev: PointerEvent) => {
    if (ev.buttons) return;                          // mid-press: the active gesture owns the cursor
    pop.style.cursor = CUR[zoneAt(ev)] || "";
  });
  pop.addEventListener("pointerdown", (ev: PointerEvent) => {
    const zone = zoneAt(ev);
    if (!zone) return;
    ev.preventDefault();
    ev.stopPropagation();                            // the whole-box drag must not also engage
    const r0 = pop.getBoundingClientRect();
    const x0 = ev.clientX, y0 = ev.clientY;
    pop.setPointerCapture(ev.pointerId);
    const move = (mv: PointerEvent) => {
      const dx = mv.clientX - x0, dy = mv.clientY - y0;
      let left = r0.left, top = r0.top, wpx = r0.width, hpx = r0.height;
      if (zone.includes("e")) wpx = r0.width + dx;
      if (zone.includes("s")) hpx = r0.height + dy;
      if (zone.includes("w")) { wpx = r0.width - dx; left = r0.left + dx; }
      if (zone.includes("n")) { hpx = r0.height - dy; top = r0.top + dy; }
      if (wpx < MIN_W) { if (zone.includes("w")) left -= MIN_W - wpx; wpx = MIN_W; }
      if (hpx < MIN_H) { if (zone.includes("n")) top -= MIN_H - hpx; hpx = MIN_H; }
      left = Math.max(4, left); top = Math.max(4, top);
      pop.style.width = wpx + "px";
      pop.style.height = hpx + "px";
      pop.style.left = left + "px";
      pop.style.top = top + "px";
      commentPopPos = { x: left, y: top };           // the drag's own persistence, kept in step
    };
    const up = () => {
      pop.removeEventListener("pointermove", move);
      pop.removeEventListener("pointerup", up);
      pop.removeEventListener("pointercancel", up);
      pop.classList.add("sized");                    // a real user resize unlocks the quote clamp, like the grip
    };
    pop.addEventListener("pointermove", move);
    pop.addEventListener("pointerup", up);
    pop.addEventListener("pointercancel", up);
  });
}

function commentPopTitle(create: boolean, th: CommentThread | null | undefined): string {
  const nm = th?.name || "Thread";
  return create ? "New comment:"
    : th!.status === "promoted" ? "Now its own session: " + th!.promotedName
    : th!.status === "promoting" ? "Breaking out…"
    : th!.status === "merging" ? "Relaying back to the session…"
    : th!.status === "merged" ? nm + " (relayed to the session)"
    : th!.status === "resolved" ? nm + " (resolved)" : nm;
}

/** Send the popover composer's text — the Enter key and the delegated Send button share this. The
 *  button acknowledges instantly; a thread reply also renders its optimistic pending bubble. */
function commentSendFromPop(pop: HTMLElement): void {
  const box = pop.querySelector(".cmt-input") as HTMLTextAreaElement | null;
  const send = pop.querySelector('[data-act="cmtsend"]') as HTMLButtonElement | null;
  const text = box?.value.trim();
  if (!box || !text || !vscodeApi) return;
  const create = pendingCommentAnchor;
  if (create) {
    const nameBox = pop.querySelector(".cmt-name") as HTMLInputElement | null;
    const nm = (nameBox?.value || "").trim();
    if (nm && !/^[A-Za-z0-9._-]+$/.test(nm)) { nameBox?.classList.add("bad"); nameBox?.focus(); return; }
    if (send) { send.disabled = true; send.classList.add("busy"); }   // ack before the round-trip (the ➤
    //                                       dims); the draft survives until commentCreated adopts the thread
    // the ants start on the GESTURE (the user 2026-08-17: the cue lagged the kernel round-trip):
    // a synthetic working thread marks the passage NOW; the kernel's frame replaces the whole list,
    // so its never-listed tid unwraps through the standard sweep the moment the real thread lands
    const synth: CommentThread = { tid: "pending:" + create.uuid, anchorUuid: create.uuid,
      exact: create.exact, status: "open", createdT: Date.now() / 1000, state: "working",
      unread: false, replyOwed: true, promotedName: "", msgs: [], name: nm || "comment", color: create.color || "" };
    const cur0 = commentThreads.get(create.sid) || [];
    commentThreads.set(create.sid, [...cur0.filter((t) => t.tid !== synth.tid), synth]);
    cmtAwaitBase.set(synth.tid, { ...CMT_LATCH_ZERO });   // the SEND gesture latches the pulse — before any kernel round-trip (T102); released once a frame acknowledges the send (T237)
    applyCommentMarks(create.sid);
    vscodeApi.postMessage({ type: "commentCreate", id: create.sid, uuid: create.uuid, exact: create.exact,
      text, name: nm, model: create.model || "", effort: create.effort || "",
      fast: create.fast || "", color: create.color || "" });
    cmtCreateInFlight.set(create.uuid, { sid: create.sid, uuid: create.uuid, exact: create.exact,
      text, name: nm, model: create.model || "", effort: create.effort || "",
      fast: create.fast || "", color: create.color || "", tries: 0 });
    return;
  }
  const cur = openCommentThread();
  if (!cur) return;
  vscodeApi.postMessage({ type: "commentReply", id: cur.sid, tid: cur.th.tid, text });
  cmtAwaitBase.set(cur.th.tid, cmtLatchOf(cur.th));   // a follow-up RE-LATCHES at its own send — until a frame carries that send; then the kernel's replyOwed owns it (T102 → T237)
  cmtInterrupted.delete(cur.th.tid);                  // a fresh send re-owes a reply — the stop tombstone retires (T138)
  cur.th.state = "working";                     // optimistic: the pulse rides the SEND, not the
  applyCommentMarks(cur.sid);                   // round-trip (the kernel's next frame confirms)
  const pl = commentPending.get(cur.th.tid) || [];
  pl.push({ text, t: Date.now() / 1000,
            imgPaths: cmtShippedImgs.filter((p2) => text.includes(p2)) });   // only paths still in the sent text
  cmtShippedImgs = [];
  commentPending.set(cur.th.tid, pl);
  commentDrafts.delete(cur.th.tid);
  box.value = "";
  const list = pop.querySelector(".cmt-msgs") as HTMLElement | null;
  if (list) fillCommentMsgs(list, cur.th, cur.sid);      // the pending bubble IS the acknowledgement
}

/** The popover — ONE pane-local card (no backdrop: the conversation stays readable beside it).
 *  Same thread still open → the conversation refreshes IN PLACE: the composer, its caret and the
 *  action row survive every comments frame (a full rebuild per push ate mid-press clicks and
 *  jumped the caret — the click-safety rule). Identity or status changed → full rebuild. Buttons
 *  carry data-act only; their actions live on the document.body delegate. */
function renderCommentPopover(): void {
  const create = pendingCommentAnchor;
  const key = openCommentKey;
  const prev = document.getElementById("cmt-pop");
  if (!create && !key) { prev?.remove(); return; }
  const sid = create ? create.sid : key!.sid;
  const th = key ? (commentThreads.get(sid) || []).find((t) => t.tid === key.tid) : null;
  if (key && !th) { closeCommentPop(); return; }
  const mode = create ? "create" : "thread";
  const status = th ? th.status : "";
  if (prev && prev.dataset.mode === mode && prev.dataset.tid === (th ? th.tid : create!.uuid)
      && prev.dataset.status === status) {
    // in-place refresh: conversation, title, and the live model/effort labels
    const t = prev.querySelector(".cmt-title") as HTMLElement | null;
    if (t) t.textContent = commentPopTitle(!!create, th);
    const list = prev.querySelector(".cmt-msgs") as HTMLElement | null;
    if (th && list) fillCommentMsgs(list, th, sid);
    const cs = prev.querySelector("#cmt-state");
    if (th && cs) cs.replaceWith(cmtStateChip(th));   // state chip + timer track every frame, in place
    const cm = prev.querySelector("#cmt-meta") as HTMLElement | null;
    if (th && cm) syncMetaControls(cm, threadMetaStatus(th), th.tid);   // same in-place refresh as the chat's tick
    return;
  }
  const hadFocus = !!prev?.querySelector(".cmt-input:focus-within, .cmt-input:focus");
  prev?.remove();
  const pop = el("div", "cmt-pop");
  pop.id = "cmt-pop";
  pop.dataset.mode = mode;
  pop.dataset.tid = th ? th.tid : create!.uuid;
  pop.dataset.status = status;
  const head = el("div", "cmt-head");
  const title = el("span", "cmt-title");
  title.textContent = commentPopTitle(!!create, th);
  if (th?.color) title.style.color = th.color;   // an existing thread's title wears its identity color
  let nameBox: HTMLInputElement | null = null;
  if (create) {
    // the name lives IN the header — "New comment: <name>" — bold and editable right there (the
    // user 2026-08-17); prefilled <session>-comment-<N>, drafted under its own key so a refused
    // create hands an edited name back too
    const nk = "newname:" + create.uuid;
    nameBox = document.createElement("input");
    nameBox.type = "text";
    nameBox.className = "cmt-name";
    nameBox.setAttribute("autocapitalize", "off");
    nameBox.setAttribute("autocomplete", "off");
    nameBox.setAttribute("spellcheck", "false");
    const sess0 = sessions.get(sid);
    nameBox.value = commentDrafts.get(nk)
      || ((sess0?.name || "session").replace(/[^A-Za-z0-9._-]/g, "-")
          + "-comment-" + ((commentThreads.get(sid) || []).length + 1));
    nameBox.title = "The comment's name — edit it right here";
    if (create.color) nameBox.style.color = create.color;   // its identity color, distinct from the parent's
    const nb = nameBox;
    nb.addEventListener("input", () => { nb.classList.remove("bad"); commentDrafts.set(nk, nb.value); });
  }
  const closeBtn = el("button", "cmt-x") as HTMLButtonElement;
  closeBtn.type = "button";
  closeBtn.textContent = "×";
  closeBtn.title = "Close (the thread stays on its highlight)";
  closeBtn.dataset.act = "cmtclose";
  if (nameBox) head.append(title, nameBox, closeBtn);
  else head.append(title, closeBtn);
  // DRAG by the header (the user 2026-08-13, who found the popover's spot inconvenient): pointer
  // capture, viewport-clamped, and the position writes through to commentPopPos so a later full
  // rebuild (a status flip) reopens where the user parked it. The header survives in-place
  // refreshes, so a drag is never cut by a comments frame; a full rebuild mid-drag just ends it.
  head.title = "Drag to move";
  // the WHOLE box drags (the user 2026-08-17), not just the header — any grip that isn't an
  // interactive control or selectable text, and never the bottom-right resize corner
  pop.addEventListener("pointerdown", (ev: PointerEvent) => {
    const t = ev.target as HTMLElement;
    if (t.closest(".cmt-x, .cmt-name, .cmt-input, .cmt-msgs, .cmt-quote, button, input, textarea, .meta-btn, .meta-menu")) return;
    const pr = pop.getBoundingClientRect();
    if (ev.clientX > pr.right - 18 && ev.clientY > pr.bottom - 18) return;   // the resize handle's corner
    ev.preventDefault();
    const dx = ev.clientX - pop.offsetLeft, dy = ev.clientY - pop.offsetTop;
    pop.setPointerCapture(ev.pointerId);
    pop.classList.add("dragging");
    const move = (mv: PointerEvent) => {
      const x = Math.max(4, Math.min(mv.clientX - dx, window.innerWidth - pop.offsetWidth - 4));
      const y = Math.max(4, Math.min(mv.clientY - dy, window.innerHeight - 40));
      pop.style.left = x + "px";
      pop.style.top = y + "px";
      commentPopPos = { x, y };
    };
    const up = () => {
      pop.classList.remove("dragging");
      pop.removeEventListener("pointermove", move);
      pop.removeEventListener("pointerup", up);
      pop.removeEventListener("pointercancel", up);
    };
    pop.addEventListener("pointermove", move);
    pop.addEventListener("pointerup", up);
    pop.addEventListener("pointercancel", up);
  });
  pop.appendChild(head);
  wireEdgeResize(pop);
  if (create || !(th!.events || []).length) {
    // the chat-parity view opens ON the quoting message, so a second quote block would say it twice
    const quote = el("div", "cmt-quote");
    quote.textContent = create ? create.exact : th!.exact;
    quote.title = "the highlighted passage this thread is about";
    pop.appendChild(quote);
  }
  if (th) {
    const list = el("div", "cmt-msgs");
    fillCommentMsgs(list, th, sid);
    pop.appendChild(list);
  }
  let metaRowPending: HTMLElement | null = null;   // appended under the composer row — the statusline position
  if (create) {
    // the thread's OWN model + effort + fast (the user 2026-08-17; fast and the kernel-side
    // defaults the user 2026-08-29): the same /models-fed choices the chat's statusline selectors
    // use. Each chip shows the EFFECTIVE default — the dialog's own pick, else the kernel's
    // default-comment setting (commentDefaults), else what this session runs now — so a pick is
    // always a visible deviation; picking affects only the thread, never the conversation it
    // branches from. The kernel re-resolves the same order at create (_comment_launch_prefs).
    const st = sessions.get(sid)?.status;
    // the kernel default for one chip, resolved ("session" → none → inherit this session)
    const setDef = (kind: "model" | "effort" | "fast") => {
      const v = kind === "model" ? commentDefaults.model : kind === "effort" ? commentDefaults.effort
        : commentDefaults.fast;
      return v === "session" ? "" : v;
    };
    // fast is offered only where the effective model could run it (fastAvailable's rule: unknown
    // and opus stay offered; an explicit non-Opus pick drops the chip — no control that only toasts)
    const canFast = (m: string) => {
      const v = (m || "").toLowerCase();
      return !v || v === "default" || v.includes("opus");
    };
    const buildMetaRow = (): HTMLElement => {
      const metaRow = el("div", "statusline cmt-meta-row");
      const mkSel = (kind: "model" | "effort" | "fast") => {
        // the statusline's own badge chrome (.meta-btn/.meta-label/.meta-caret) so the two stay in
        // sync by construction (the user 2026-08-17), tinted from the /models list's shared colors
        const btn = el("span", "meta-btn") as HTMLElement;
        const chosen = (kind === "model" ? create.model : kind === "effort" ? create.effort : create.fast) || "";
        const effVal = chosen || setDef(kind);
        const label = el("span", "meta-label");
        if (kind === "fast") {
          const on = (effVal || st?.fast || "off").toLowerCase() === "on";
          label.textContent = prettyFast(on ? "on" : "off");
          if (on) label.style.color = "var(--fast)";
        } else {
          const choice = kind === "model" ? (effVal ? modelChoiceLabel(effVal) : null)
            : (effVal ? EFFORT_CHOICES.find((c) => c.value === effVal) : null);
          label.textContent = choice ? choice.label : (kind === "model" ? (st?.model || "Default") : (st?.effort || "default"));
          const tint0 = kind === "model" ? pickTone(st?.modelColor, st?.modelTone) : pickTone(st?.effortColor, st?.effortTone);
          const tint = (nonClassicChoiceTone(choice) as number[] | undefined) || (tint0 && tint0.length === 3 ? readableRgb(tint0) : tint0);   // TEXT tint: light re-encodes on BOTH branches (the session-status fallback skipped it)
          if (tint && tint.length === 3) label.style.color = `rgb(${tint[0]},${tint[1]},${tint[2]})`;
        }
        const caret = el("span", "meta-caret");
        caret.textContent = "▾";
        btn.append(label, caret);
        btn.title = chosen ? "the comment runs on its own " + kind + "; this session keeps its"
          : setDef(kind) ? "the default for new comments (set in Settings) — click to pick another for this one"
          : "inherits this session's " + kind + " — click to pick another for the comment only";
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          closeMetaMenu();
          const menu = el("div", "meta-menu");
          for (const c of META_CHOICES[kind]) {
            // a PINNED model family (its /models default is a version, not the alias) — see the Latest row below
            const pinnedTo = kind === "model" ? (c.default || "") : "";
            const pinned = !!pinnedTo && pinnedTo !== c.value;
            const cur = kind === "fast"
              ? c.value === ((effVal || st?.fast || "off").toLowerCase() === "on" ? "on" : "off")
              : effVal ? (pinned ? effVal === pinnedTo : (c.value === effVal || !!c.versions?.some((v) => v.value === effVal)))
              : (st ? isCurrentMeta(kind, st, c.value) : false);
            const item = el("div", "meta-item" + (cur ? " current" : ""));
            item.textContent = c.label;
            item.addEventListener("click", (ev) => {
              ev.stopPropagation();
              // a model family sends its remembered DEFAULT — the pinned version, else the alias —
              // exactly as the chat statusline and the timeline lane do (this dialog sent the bare
              // alias, so the same click floated here and pinned there)
              if (pendingCommentAnchor) pendingCommentAnchor[kind] = kind === "model" ? (c.default || c.value) : c.value;
              closeMetaMenu();
              document.getElementById("cmt-pop")?.remove();   // full rebuild shows the pick
              renderCommentPopover();
            });
            menu.appendChild(item);
            if (pinned) {
              // This menu is FLAT — no submenu, so no Latest row — and once a family carried a pin the
              // family row launched on the pin and nothing here launched on the alias. The family row
              // names the pin it launches on, and a "<Family> · Latest" row beside it sends the bare
              // alias: a per-thread launch pref, never the family's memory (an alias records nothing at
              // the kernel's choke point, so no floating flag rides it).
              const pinSub = el("div", "meta-item-sub");
              pinSub.textContent = modelChoiceLabel(pinnedTo).label;
              item.appendChild(pinSub);
              const latest = el("div", "meta-item" + (effVal === c.value ? " current" : ""));
              const lh = el("div");
              lh.textContent = c.label + " · Latest";
              const ls = el("div", "meta-item-sub");
              ls.textContent = "follows the newest " + c.label;
              latest.append(lh, ls);
              latest.addEventListener("click", (ev) => {
                ev.stopPropagation();
                if (pendingCommentAnchor) pendingCommentAnchor[kind] = c.value;
                closeMetaMenu();
                document.getElementById("cmt-pop")?.remove();
                renderCommentPopover();
              });
              menu.appendChild(latest);
            }
          }
          document.body.appendChild(menu);
          const r2 = btn.getBoundingClientRect();
          menu.style.left = r2.left + "px";
          menu.style.top = (r2.bottom + 4) + "px";
          metaMenuEl = menu;
        });
        return btn;
      };
      const metaRight = el("span", "sl-right");
      metaRight.append(mkSel("model"), mkSel("effort"));
      if (canFast(create.model || setDef("model") || st?.model || "")) metaRight.append(mkSel("fast"));
      metaRow.appendChild(metaRight);
      return metaRow;
    };
    metaRowPending = buildMetaRow();
    // the gear may have moved the defaults since page load — re-read at open and repaint the row
    // IN PLACE (never the whole popover: the composer holds focus and an unsent draft)
    refreshCommentDefaults(() => {
      if (pendingCommentAnchor !== create) return;   // the dialog already sent or closed
      const live = pop.querySelector(".cmt-meta-row");
      if (live) live.replaceWith(buildMetaRow());
    });
  }
  if (create || th!.status !== "promoted") {
    const dk = create ? "new:" + create.uuid : th!.tid;
    const box = document.createElement("textarea");
    box.className = "cmt-input";
    box.rows = 2;
    box.placeholder = create ? "Comment on this passage…"
      : th!.status === "merged" ? "Reply to continue — the discussion so far was relayed to the session…"
      : th!.status === "resolved" ? "Reply to reopen…" : "Reply…";
    box.value = commentDrafts.get(dk) || "";
    box.addEventListener("input", () => commentDrafts.set(dk, box.value));
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); commentSendFromPop(pop); }
      else if (ev.key === "Escape") { ev.stopPropagation(); closeCommentPop(); }
    });
    const crow = el("div", "cmt-composer");   // the chat composer's shape: clip left, ➤ right
    const attach = el("button", "cmt-attach") as HTMLButtonElement;
    attach.type = "button";
    attach.title = "Attach a file";
    attach.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
      + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
      + '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66'
      + 'l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
    attach.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); cmtFilePicker.click(); });
    const send = el("button", "cmt-send") as HTMLButtonElement;
    send.type = "button";
    send.textContent = "➤";
    send.title = create ? "Comment (Enter)" : "Send (Enter)";
    send.setAttribute("aria-label", create ? "Comment" : "Send");
    send.dataset.act = "cmtsend";
    crow.append(attach, box, send);
    pop.appendChild(crow);
    if (metaRowPending) pop.appendChild(metaRowPending);   // model/effort under the box, like the chat
    if (th && th.status === "open") {
      // the thread is a real session under the hood — its model/effort switch LIVE through the
      // chat's own ops (setModel/setEffort route by sid; be.owns makes the thread reachable).
      // The row IS a statusline (the user 2026-08-25): the chat's own .statusline dress — state
      // chip with the counting timer on the left, the meta badges clustered right in .sl-right —
      // so the popover and the chat stay one vocabulary by construction.
      const mrow = el("div", "statusline cmt-meta-row");
      mrow.appendChild(cmtStateChip(th));
      // the chat's OWN meta controls — mode · model · effort · fast, the full statusline element
      // set (the user 2026-08-25: fast and the mode badge were missing; a popover-local copy had
      // drifted in dress too). syncMetaControls builds/refreshes them; forSid routes the ops and
      // the pending keys at the THREAD, so a pick touches only it.
      const right = el("span", "sl-right");
      const metaBox = el("span", "spinner-meta");   // the SAME wrapper the chat's badges wear — its
      metaBox.id = "cmt-meta";                      // mono/0.92em/dim dress is where the font lives
      syncMetaControls(metaBox, threadMetaStatus(th), th.tid);
      right.appendChild(metaBox);
      mrow.appendChild(right);
      pop.appendChild(mrow);
    }
    const row = el("div", "cmt-actions");
    if (th) {
      const br = el("button", "cmt-act") as HTMLButtonElement;
      br.type = "button";
      br.textContent = "Break out";
      br.title = "Continue this thread as its own session; it keeps everything it knows";
      br.dataset.act = "cmtbreak";
      row.appendChild(br);
      // MERGE (the user 2026-08-23): the third exit — fold the thread's outcome back into the
      // session. The kernel sends the parent the discussion as the person's own handoff and the
      // thread settles to 'merged'; only a thread with something to say can merge (kernel-refused
      // otherwise, loudly).
      if (th.status === "open" || th.status === "resolved") {
        const mg = el("button", "cmt-act") as HTMLButtonElement;
        mg.type = "button";
        mg.textContent = "Relay";   // T145: the verb is 'sending it back into the main thread with context' — Merge implied the thread closes, and it doesn't
        mg.title = "Send this discussion back into the main conversation as context going forward; the thread stays open for more talk";
        mg.dataset.act = "cmtmerge";
        row.appendChild(mg);
      }
      // Resolve is GONE (the user 2026-08-17): an idle thread draws no usage and its transcript
      // is on disk either way, so the real verbs are Break out and Delete. Legacy resolved rows
      // still render dimmed and reopen on reply; they just can't be minted anymore. Never for
      // 'promoting' — the kernel would refuse anyway.
      if (th.status === "open" || th.status === "resolved") {
        const dl = el("button", "cmt-act cmt-del") as HTMLButtonElement;
        dl.type = "button";
        dl.textContent = "Delete";
        dl.title = "Remove this thread and its highlight (the conversation file stays on disk)";
        dl.dataset.act = "cmtdelete";
        dl.addEventListener("pointerleave", () => { dl.classList.remove("armed"); dl.textContent = "Delete"; });
        row.appendChild(dl);
      }
    }
    pop.appendChild(row);
  } else if (th) {
    const row = el("div", "cmt-actions");
    const open = el("button", "cmt-send") as HTMLButtonElement;
    open.type = "button";
    open.textContent = "Open the session";
    open.dataset.act = "cmtopensession";
    open.dataset.tid = th.tid;
    row.appendChild(open);
    pop.appendChild(row);
  }
  document.body.appendChild(pop);
  if (typeof ResizeObserver === "function") {
    // resizing the BOX (resize: both) hands the extra room to the quoted context: the .sized class
    // unlocks the quote's clamp; armed only after a real user resize, so the natural size stays tight
    const w0 = pop.offsetWidth, h0 = pop.offsetHeight;
    const ro = new ResizeObserver(() => {
      if (Math.abs(pop.offsetWidth - w0) > 6 || Math.abs(pop.offsetHeight - h0) > 6) pop.classList.add("sized");
    });
    ro.observe(pop);
  }
  // OPEN GEOMETRY (the user 2026-08-25), THREAD mode: 70% of the chat pane's width with the RIGHT
  // edge on the chat's right edge, 60% of the pane's height — and the size is FIXED from open:
  // streaming text fills within (.cmt-msgs flexes and scrolls; the box never reflows on content
  // events — the auto-expand was the jarring part). Manual resize (resize: both) still hands the
  // user control, and a dragged position (commentPopPos) still wins over the default placement.
  // The CREATE composer keeps its natural size at the selection point — a different gesture.
  if (th && !pop.style.width) { pop.style.width = Math.round(window.innerWidth * 0.7) + "px"; }
  if (th && !pop.style.height) { pop.style.height = Math.round(window.innerHeight * 0.6) + "px"; }
  const r = pop.getBoundingClientRect();
  const defaultX = th ? (window.innerWidth - r.width - 8) : (window.innerWidth - r.width) / 2;
  const px = Math.max(8, Math.min(commentPopPos?.x ?? defaultX, window.innerWidth - r.width - 8));
  const py = Math.max(8, Math.min(commentPopPos?.y ?? 120, window.innerHeight - r.height - 8));
  pop.style.left = px + "px";
  pop.style.top = py + "px";
  const msgs = pop.querySelector(".cmt-msgs") as HTMLElement | null;
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
  if (create || hadFocus || !th || !th.msgs.length) (pop.querySelector(".cmt-input") as HTMLTextAreaElement | null)?.focus();
}

// BREAK OUT (the user 2026-08-13: "a button that breaks it out into its own session") — the fork
// modal's shape: a name box, Enter/Break out, provisional tab as the instant acknowledgement. The
// kernel seeds the judge stores and registers the session (commentPromote); the popover's thread
// flips to "promoted" on the next comments frame.
function showBreakoutPrompt(sid: string, tid: string): void {
  const sess = sessions.get(sid);
  const thName = (commentThreads.get(sid) || []).find((t) => t.tid === tid)?.name || "";
  const base = thName || ((sess?.name || "session").replace(/[^A-Za-z0-9._-]/g, "-") + "-thread");
  document.getElementById("fork-prompt")?.remove();
  const overlay = el("div", "picker-overlay confirm-overlay");
  overlay.id = "fork-prompt";
  const box = el("div", "picker-box confirm-box");
  const h = el("div", "confirm-title");
  h.textContent = "Break out the thread";
  const d = el("div", "confirm-detail");
  d.textContent = "The thread becomes its own session, keeping the conversation up to its highlight plus everything discussed since.";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "fork-name";
  input.value = base;
  input.setAttribute("autocapitalize", "off");
  input.setAttribute("autocomplete", "off");
  input.setAttribute("autocorrect", "off");
  input.setAttribute("spellcheck", "false");
  const actions = el("div", "confirm-actions");
  const cancel = el("button", "picker-action confirm-btn");
  cancel.textContent = "Cancel";
  const create = el("button", "picker-action confirm-btn");
  create.textContent = "Break out";
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey, true); };
  const go = () => {
    const name = input.value.trim();
    if (!/^[A-Za-z0-9._-]+$/.test(name)) { input.classList.add("bad"); input.focus(); return; }
    vscodeApi?.postMessage({ type: "commentPromote", id: sid, tid, name });
    close();
    closeCommentPop();
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
  input.focus();
  input.setSelectionRange(0, input.value.length);
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
    if (it.hiddenTab) {   // open as a tab but filtered out of the CURRENT view (tagged) → picking jumps to its view
      time.textContent = "other view";
      time.style.opacity = "0.7";
    } else if (it.running) {   // a live session (SDK/tmux backend) whose tab is closed → a green "running" badge
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
      } else if (it.hiddenTab) {
        revealSession(it.id);   // its tab already exists — switch to a view that shows it (revealIn, post-retirement)
        setActive(it.id);
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
    // open-as-tab but filtered out of the CURRENT view (tagged; the hidden set retired 2026-08-24)
    // still list (the user 2026-08-18): a background session's one visible home on the chat side —
    // omitting them here plus the tab being out of view would recreate the secret-running-session
    // state abolished 2026-08-11
    const hidden = items.filter((it) => isOpenTab(it.id) && !tabInView(it.id))
                        .map((it) => Object.assign({}, it, { hiddenTab: true }));
    const running = avail.filter((it) => it.running);
    const rest = avail.filter((it) => !it.running);
    if (running.length) { list.appendChild(label("Running — reopen")); for (const it of running) list.appendChild(mkRow(it)); }
    if (hidden.length) { list.appendChild(label("In another view — open")); for (const it of hidden) list.appendChild(mkRow(it)); }
    if (rest.length) { if (running.length || hidden.length) list.appendChild(label("Recent")); for (const it of rest) list.appendChild(mkRow(it)); }
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
                || v?.el.querySelector(`.turn[data-mids~="${cssEscape(uuid)}"]`)
                || v?.el.querySelector(`.turn[data-uuids~="${cssEscape(uuid)}"]`)) as HTMLElement | null;
  // Deep-link into history the window doesn't currently cover (the head/tail folded into a spacer): find the
  // event, render a fresh window AROUND its unit, then re-query — the "load it when you jump there" behaviour.
  // (No match anywhere → genuinely off the active path; stash for the next render pass.)
  if (!target && v && activeId) {
    const s = sessions.get(activeId);
    // resultUuid too: an ANSWERED AskUserQuestion turn is anchored by its answer line's uuid
    // (renderEvent's data-uuid — the uuid the timeline emits for the decision), which no event
    // carries as its OWN uuid, so a uuid/mid-only lookup missed it and this recovery never ran.
    const idx = s ? s.events.findIndex((e) => e.uuid === uuid || (e as { mid?: string }).mid === uuid
                                       || (e as { resultUuid?: string }).resultUuid === uuid
                                       || (((e as { settleUuids?: string[] }).settleUuids || []).includes(uuid))) : -1;
    if (s && idx >= 0) {
      const items = displayItems(s);
      let u = items.findIndex((it) => it.kind === "toolgroup" || it.kind === "retrygroup" ? it.indices.includes(idx) : it.index === idx);
      if (u < 0) u = Math.max(0, items.findIndex((it) => itemFirstEvent(it) >= idx));
      // The anchor can live INSIDE a collapsed tool run: the folded line carries only the run's FIRST
      // uuid, so the re-render below could never surface a mid-run member — the click honest-failed
      // "pointer-not-rendered" with the message sitting right behind the fold (the user 2026-07-16: a
      // Blocked card anchored to its session's pending AskUserQuestion tool atom). The click asked to
      // SEE that message: expand the run, so the re-render gives the member its own turn to land on.
      const hit = items[u];
      if (hit && hit.kind === "toolgroup" && hit.indices.includes(idx))
        expandedGroups.add(toolGroupKey(s.events[hit.indices[0]]));
      if (hit && hit.kind === "retrygroup" && hit.indices.includes(idx))
        expandedGroups.add(retryGroupKey(s.events[hit.indices[0]]));
      const working = s.status.state === "working" || s.status.state === "compacting";
      renderWindowItems(v, s, items, Math.max(0, u - WINDOW_RADIUS), Math.min(items.length, u + WINDOW_RADIUS), working);
      // Re-query with the SAME three selectors the first lookup used. data-mids was missing here, so an
      // unhydrated postal turn (whose message ids live only in data-mids) could be found in the events,
      // have its window rendered — and then still honest-fail "pointer-not-rendered" on the re-query.
      target = (v.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`)
                || v.el.querySelector(`.turn[data-mid="${cssEscape(uuid)}"]`)
                || v.el.querySelector(`.turn[data-mids~="${cssEscape(uuid)}"]`)
                || v.el.querySelector(`.turn[data-uuids~="${cssEscape(uuid)}"]`)) as HTMLElement | null;
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
  landOn(target, uuid);
  if (pendingAnchorQuote) { highlightCiteSpan(target, pendingAnchorQuote); pendingAnchorQuote = null; }
  return true;
}

/** The time-only landing (see landActive): the event whose epoch sits nearest `t` among the
 *  RESIDENT events, landed with the honest note. Returns true when it landed. */
function landNearestMoment(t: number): boolean {
  const v = activeId ? views.get(activeId) : null;
  const s = activeId ? sessions.get(activeId) : null;
  if (!v || !s || !s.events.length) return false;
  let best = -1, bestD = Infinity, headEp: number | null = null;
  for (let i = 0; i < s.events.length; i++) {
    const ep = eventEpoch(s.events[i]);
    if (ep == null) continue;
    if (headEp == null) headEp = ep;
    const d = Math.abs(ep - t);
    if (d < bestD) { bestD = d; best = i; }
  }
  if (best < 0) return false;
  const uuid = (s.events[best] as { uuid?: string }).uuid || "";
  const items = displayItems(s);
  let u = items.findIndex((it) => it.kind === "toolgroup" || it.kind === "retrygroup" ? it.indices.includes(best) : it.index === best);
  if (u < 0) u = Math.max(0, items.findIndex((it) => itemFirstEvent(it) >= best));
  const working = s.status.state === "working" || s.status.state === "compacting";
  renderWindowItems(v, s, items, Math.max(0, u - WINDOW_RADIUS), Math.min(items.length, u + WINDOW_RADIUS), working);
  const target = (uuid ? v.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`) : null)
    || (v.el.querySelector(`[data-unit="${u}"]`) as HTMLElement | null);
  if (!target) { landTrail.push("time-nearest-miss"); return false; }
  landTrail.push("time-nearest");
  landOn(target as HTMLElement, uuid || undefined);
  const beforeHead = headEp != null && t < headEp && (s.headFrom ?? 0) > 0;
  landToast(beforeHead
    ? "that link points at a moment before the loaded history — landed at the oldest loaded message"
    : "that link points at a time, not a message — landed at the closest one");
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
// The supporting SPAN (T218): the distiller quoted the sentence its takeaway rests on, the kernel
// located it in the cited atom, and the landing highlights it INSIDE the (often long, multi-topic)
// message — the study's most common partial was the right message with the claim buried deep. The
// CSS Custom Highlight API paints it with ZERO DOM surgery, so the ever-re-rendering turn list is
// never mutated (the click-safety family); a browser without it, or an unfindable quote, keeps
// today's whole-message landing exactly (the honest-fallback rule).
let pendingAnchorQuote: string | null = null;
function highlightCiteSpan(target: HTMLElement, quote: string): void {
  try {
    const H = (CSS as unknown as { highlights?: Map<string, unknown> }).highlights;
    if (!H || typeof Highlight === "undefined") return;
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = []; let full = "";
    for (let n = walker.nextNode(); n; n = walker.nextNode()) { nodes.push(n as Text); full += (n as Text).data; }
    let at = full.indexOf(quote);
    let len = quote.length;
    if (at < 0) {
      const pat = new RegExp(quote.trim().split(/\s+/).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), "i");
      const m = pat.exec(full);
      if (!m) return;                                    // unfindable in the rendered text → no highlight, no guess
      at = m.index; len = m[0].length;
    }
    const range = document.createRange();
    let pos = 0, started = false;
    for (const tn of nodes) {
      const end = pos + tn.data.length;
      if (!started && at < end) { range.setStart(tn, at - pos); started = true; }
      if (started && at + len <= end) { range.setEnd(tn, at + len - pos); break; }
      pos = end;
    }
    if (!started) return;
    H.set("cite-span", new (Highlight as unknown as { new(...r: Range[]): unknown })(range));
    window.setTimeout(() => { try { H.delete("cite-span"); } catch { /* gone with a nav */ } }, 6000);
    const el0 = range.startContainer.parentElement;
    if (el0) el0.scrollIntoView({ block: "center", behavior: "auto" });   // land ON the sentence, not the message top
  } catch { /* highlight is chrome, never load-bearing */ }
}

function landOn(target: HTMLElement, flashKey?: string) {
  const realign = () => target.scrollIntoView({ block: "start", behavior: "auto" });
  realign();
  if (flashKey == null || flashKey !== flashedAnchor) {   // one flash per navigation (see flashedAnchor)
    if (flashKey != null) flashedAnchor = flashKey;
    target.classList.add("anchor-flash");
    setTimeout(() => target.classList.remove("anchor-flash"), 1700);
  }
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
// had no handler and the dropped route had no witness). Toasts stack in #warn-toasts
// so bursts stay readable, and fade on their own.
// DISMISSAL is the family treatment (the user 2026-08-25: the notice is useful but it
// floats over the tab strip with no visible way out — "it gets in the way of stuff"):
// a visible ✕ (the chip-✕ dress: dim, separate, hover-brightens), the whole toast still
// click-dismisses, and Escape clears the stack. One DELEGATED handler on the stable
// #warn-toasts container (created once, toasts appended into it — click-safe across
// re-renders by construction, per the standing button rules; removal IS the immediate
// acknowledgement). The Escape listener never stops propagation: clearing a toast is
// additive noise-removal, not a key the rest of the UI loses — and overlay consumers
// that capture Escape (the lightbox, the viewer) still peel first by construction.
function warnToast(msg: string) {
  let box = document.getElementById("warn-toasts");
  if (!box) {
    box = el("div", "");
    box.id = "warn-toasts";
    document.body.appendChild(box);
    box.addEventListener("click", (e) => {
      (e.target as HTMLElement | null)?.closest(".warn-toast")?.remove();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") for (const w of Array.from(box!.children)) w.remove();
    });
  }
  const t = el("div", "warn-toast");
  const txt = el("span", "warn-toast-msg");
  txt.textContent = msg;
  const x = el("button", "warn-toast-x");
  x.setAttribute("aria-label", "Dismiss");
  x.title = "dismiss (Esc)";
  x.textContent = "✕";
  t.append(txt, x);
  t.title = "click to dismiss";
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
// The wrapper re-anchors comment highlights after EVERY sync (the user 2026-08-13): marks live in
// the rebuilt DOM, and hanging the re-apply only on inbound messages missed the renders that run
// off them (a tab switch, a prebuild) — idempotent and ~free for sessions with no threads.
function syncView(id: string, atBottom?: boolean): View {
  const v = syncViewInner(id, atBottom);
  applyCommentMarks(id);
  return v;
}

function syncViewInner(id: string, atBottom?: boolean): View {
  // atBottom (passed by appendActive): false ⇒ the user is scrolled UP reading. A compact append must then
  // NOT evict the window top — evicting shifts the content above the viewport, and since the compact path
  // FULL-REBUILDS (clears the DOM, resetting scrollTop), the caller can only restore the position if the
  // content above is unchanged. true/undefined ⇒ free to evict the top (we're at the bottom, or it's a
  // non-append sync).
  renderingSid = id;          // so renderSystem can key the pinned card's persisted open-state by session
  renderingOwnerSid = id;     // preview/image URLs bake THIS session's id (host prefix included), never activeId's
  const subOf = subParts(id);
  if (subOf) renderingOwnerSid = subOf.parentId;   // …except a subagent VIEWER, whose files belong to its PARENT session
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
      if (failedRevives.has(id)) {
        ph.textContent = failedRevives.get(id) || "";
        ph.classList.add("tx-revive-failed");
      } else if (s.sub && s.sub.error) {
        // the kernel could not open the agent's file: its sentence, loud, in the pane (never a blank)
        ph.textContent = s.sub.error;
        ph.classList.add("tx-revive-failed");
      } else if (s.sub && !s.sub.loaded) {
        // the viewer's first frame is in flight → the romp loader holds the pane (the wait-state rule)
        ph.classList.add("tx-starting");
        ph.appendChild(rompLoaderInner("opening the agent's transcript…"));
      } else if (s.sub) {
        ph.textContent = "This agent has written nothing yet.";
      } else if (isProvisionalId(id) && failedProvisionals.has(id)) {
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
function itemFirstEvent(it: DisplayItem): number { return it.kind === "toolgroup" || it.kind === "retrygroup" ? it.indices[0] : it.index; }

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
  } else if (it.kind === "retrygroup") {
    const notes = it.indices.map((i) => s.events[i]) as Extract<ChatEvent, { kind: "retried" }>[];
    const key = retryGroupKey(notes[0]);
    const open = expandedGroups.has(key);
    v.el.appendChild(tag(renderRetryGroup(notes, prevEpoch, key, open)));
    adv(it.indices[0]);
    if (open) {
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

// A collapsed run of consecutive retry-recovery notes (T131 follow-up; the user 2026-08-27,
// seventeen consecutive rows) → one rail line in the same fold grammar as the tool runs: caret +
// "Recovered after retries ×N", expanding to the individual notes. Key rides expandedGroups like
// a toolgroup, so the same toggle, persistence-across-rebuilds, and popover-flip machinery apply.
function retryGroupKey(first: ChatEvent): string { return "rg:" + (first.uuid || String(eventEpoch(first) ?? "")); }
function renderRetryGroup(notes: Extract<ChatEvent, { kind: "retried" }>[], prevEpoch: number | null, key: string, open: boolean): HTMLElement {
  const turn = el("div", "turn turn-toolgroup turn-retrygroup" + (open ? " expanded" : ""));
  turn.appendChild(dot("ring"));
  const line = el("div", "toolgroup-line");
  line.title = open ? "click to collapse" : "click to expand";
  const caret = el("span", "toolgroup-caret"); caret.textContent = open ? "▾" : "▸"; line.appendChild(caret);
  if (!open) {
    const w = el("span", "retried-text");
    w.textContent = ` Recovered after retries ×${notes.length}`;
    line.appendChild(w);
  }
  line.addEventListener("click", (e) => { e.stopPropagation(); toggleToolGroup(key); });
  turn.appendChild(line);
  const epoch = eventEpoch(notes[0]);
  const anchorUuid = notes[0].uuid ?? null;
  if (anchorUuid) turn.dataset.uuid = anchorUuid;
  if (epoch != null) turn.dataset.t = String(epoch);
  if (epoch != null) turn.insertBefore(timeMarker(epoch, prevEpoch ?? null), turn.firstChild);
  const railDot = turn.querySelector(".dot") as HTMLElement | null;
  if (anchorUuid || epoch != null) wireTurnHover(turn, railDot, anchorUuid, epoch ?? 0, notes[0].tlId ?? null);
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
  refillOpenCommentPop();   // the popover renders the same units — its copy of this run must flip too
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
  const savedOwnerSid = renderingOwnerSid;
  for (const id of prebuildPlan(activeId, mru, order, viewState)) {
    if (!sessions.has(id)) continue;
    try {
      ensureView(id);
      syncView(id); // build the hidden view now, off the critical path
    } catch { /* one malformed tab must not break idle pre-building of the rest */ }
    if (deadline.timeRemaining() < 3) { schedulePrebuild(); break; } // out of idle budget → resume next idle
  }
  renderingSid = savedRenderingSid;
  renderingOwnerSid = savedOwnerSid;
}

function showActive() {
  const content = document.getElementById("content");
  if (!content) return;
  placeReviveLoader();   // session-local: shows over THIS pane only while the reviving tab is active
  notifyActive();
  renderLedger();  // swap in the active session's digest box (or hide if none)
  renderLiveAsk(); // swap in the active session's pending picker (or hide if none)
  renderBgTasks(); // swap in the active session's background-task box (or hide if none)
  let empty = document.getElementById("empty-state");
  const s = activeId ? sessions.get(activeId) : null;
  if (!s) {
    for (const v of views.values()) v.el.style.display = "none";
    renderSubHead();   // no active viewer → the header goes
    // A KNOWN-LOADING tab (its meta arrived, its payload hasn't — the clicked placeholder): the
    // thread area holds the pane-local romp loader, and the first session frame renders in place —
    // you're already there (the user 2026-08-25). Everything else keeps the no-sessions copy.
    document.getElementById("tab-loading")?.remove();
    if (activeId && tabMeta.has(activeId)) {
      const wait = el("div", "tab-loading-wait");
      wait.id = "tab-loading";
      wait.appendChild(rompLoaderInner("opening “" + (tabMeta.get(activeId)?.name || "session") + "”…"));
      content.appendChild(wait);
      if (empty) empty.style.display = "none";
    } else if (!empty) {
      empty = el("div", "empty-state"); empty.id = "empty-state";
      empty.textContent = "No session open — click + to add one.";
      content.appendChild(empty);
    } else { empty.style.display = ""; }
    document.body.style.removeProperty("--active-accent"); // no session → neutral window border
    updateStatusline();
    return;
  }
  document.getElementById("tab-loading")?.remove();   // the payload landed — the real view takes over in place
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
    const viewer = !!s.sub;   // a SUBAGENT VIEWER is read-only by nature: there is no session behind it to message
    composer.disabled = closed || viewer;
    composer.placeholder = closed ? "Session closed — read-only" : composerRestingPlaceholder();
    const sendBtn = document.getElementById("composer-send") as HTMLButtonElement | null;
    if (sendBtn) sendBtn.disabled = closed || viewer;   // read-only session/viewer → the explicit send button is dead too
    // ONE read-only cue for the viewer: the statusline's dim line. The whole message box (input + send)
    // goes, so the pane never says it twice and the transcript gets the vertical space back.
    const composerBox = document.getElementById("composer");
    if (composerBox) composerBox.style.display = viewer ? "none" : "";
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
  renderSubHead();   // the viewer's header above its transcript (hidden for every real session)
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
  if (v.el.childNodes.length === 0) {   // truly empty → the ROMP LOADER holds the spot (the standing
    // wait-state rule: swirl + wordmark + pulsing accent dots — never a bare hint). Removed by the
    // deferred build replacing this view's children — the content event — and that build always
    // runs (or the next syncView rebuilds), so the loader cannot trap.
    const ld = el("div", "tx-loading");
    const sw = document.createElement("img"); sw.className = "tx-loading-swirl";
    sw.src = mediaSrc("romp-swirl-glyph.svg"); sw.alt = ""; sw.onerror = () => sw.remove();
    const wm = el("span", "tx-loading-wordmark"); wm.textContent = "romp";
    const dots = el("span", "tx-loading-dots"); dots.append(el("i"), el("i"), el("i"));
    ld.append(sw, wm, dots);
    v.el.appendChild(ld);
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
  // The durable seek re-arms the per-pass attempt: every render pass retries until it lands, the
  // user cancels, or the backstop fires — never hijacking a scroll-back keep-offset restore.
  if (!pendingAnchor && pendingAnchorT == null && pendingAnchorKeepY == null && seek && seek.sid === activeId) {
    pendingAnchor = seek.uuid;
    pendingAnchorIntent = seek.kind;
  }
  const att = { anchor: pendingAnchor, t: pendingAnchorT, kind: pendingAnchorKind, keep: pendingAnchorKeepY != null };   // this pass's landing attempt, for diagnostics
  if (att.anchor || att.t != null) landTrail = [];
  let scrolled = pendingAnchor ? scrollToAnchor(pendingAnchor) : false;
  // TIME-ONLY navigation (the user 2026-08-25, the fifth can't-locate shape): some producers — the
  // timeline's lane clicks, deep links, cards minted from segments with no anchorable atom — send
  // anchorT with NO uuid, and the by-id-only landing left the whole class dead-ending in the bare
  // toast (audit rows: anchor null, empty trail). When the TIME is the anchor's only datum it is
  // the datum, not a proxy (the 2026-06-20 removal was about silently substituting time for a
  // KNOWN id): land at the event nearest that moment and SAY SO, never impersonating an exact jump
  // and never dead-ending. If the moment predates the loaded history, the oldest loaded message is
  // the nearest reachable point — the note names that too (fail loudly, land nearest).
  if (!scrolled && !att.anchor && att.t != null) scrolled = landNearestMoment(att.t);
  if (seek && att.anchor === seek.uuid) {
    if (scrolled) clearSeek();             // the landing event — the indicator dies with the seek
    else showSeekNote();                   // outlived the immediate landing → say the search is on
  }
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
    if (!scrolled && !anchorPendingOlder && !att.keep && !(seek && att.anchor === seek.uuid)) {   // a live SEEK keeps working instead — its backstop owns the failure
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
  updateJumpBtn();   // per-tab truth: the entering tab's restored position decides the chip, not the left one's
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
  // Follow-the-tail engages ONLY once content actually overflows (the user 2026-08-25: with slack
  // below, a streaming reply should write IN PLACE and grow a scrollbar, not jump the view to the
  // bottom). nearBottom is trivially true while nothing overflows, so without this gate the very
  // append that crosses the overflow boundary yanked the view. Overflowing + genuinely at the
  // bottom keeps the existing stick; scrolled-up keeps its never-yank rule.
  const stick = content.scrollHeight > content.clientHeight + 2 && nearBottom(content);
  const before = content.scrollTop;
  const anchor = !stick && v ? captureScrollAnchor(content, v) : null;
  syncView(activeId, stick);
  syncHostOfflineFoot();                 // before the scroll maths: it changes scrollHeight
  updateStatusline();
  if (stick) content.scrollTop = content.scrollHeight;
  else if (!(v && restoreScrollAnchor(content, v, anchor))) content.scrollTop = before;
  scheduleRailSticky();
  updateJumpBtn();   // appends can cross the overflow boundary either way — re-read the chip's truth
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
// ── jump to newest (the user 2026-08-31) ─────────────────────────────────────────────────────────
// Scrolled-up reading leaves follow mode, and the send gate keeps it that way — this chip is the
// deliberate way BACK. Visible only while the transcript overflows AND the view is off the bottom,
// read through the SAME nearBottom threshold appendActive's stick and the send gate use (one
// definition, never a second). Click = snap to the bottom + set the view's stick: follow mode
// re-engaged exactly as today's at-bottom behavior — every subsequent append recomputes stick from
// the at-bottom position and keeps descending until the user scrolls up, which re-shows the chip
// from the same listener. It lives on BODY, never inside #content, so window re-renders cannot
// rebuild it mid-click (the click-safety rule satisfied structurally); the snap itself is the
// acknowledgment. Anchored to #content's bottom edge by measurement, re-run by the content
// ResizeObserver below (the composer growing moves that edge) — event-based, no polling.
const jumpBtn = document.createElement("button");
jumpBtn.id = "jump-bottom";
jumpBtn.title = "jump to newest — then follow new content";
jumpBtn.setAttribute("aria-label", "jump to newest");
jumpBtn.hidden = true;
jumpBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"'
  + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<polyline points="6 9.5 12 15.5 18 9.5"/></svg>';   // stemless chevron — the full arrow fought the short pill (the user 2026-08-31)
function updateJumpBtn(): void {
  const c = document.getElementById("content");
  if (!c || c.clientHeight <= 0) { jumpBtn.hidden = true; return; }   // hidden pane measures 0 — no chip
  const off = c.scrollHeight > c.clientHeight + 2 && !nearBottom(c);
  jumpBtn.hidden = !off;
  if (off) jumpBtn.style.bottom = (Math.max(0, window.innerHeight - c.getBoundingClientRect().bottom) + 8) + "px";
}
jumpBtn.onclick = () => {
  const c = document.getElementById("content");
  if (!c) return;
  c.scrollTop = c.scrollHeight;                       // the snap IS the acknowledgment
  const v = activeId ? views.get(activeId) : undefined;
  if (v) { v.stick = true; v.scrollTop = c.scrollTop; }   // the explicit re-entry into follow mode
  updateJumpBtn();                                    // at the bottom now — the chip hides itself
};
{
  const c = document.getElementById("content");
  if (c) {
    document.body.appendChild(jumpBtn);
    c.addEventListener("scroll", updateJumpBtn, { passive: true });   // it only measures
    if (typeof ResizeObserver === "function") new ResizeObserver(updateJumpBtn).observe(c);
  }
}
window.addEventListener("resize", updateJumpBtn);
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

// ── drag-to-resize the tab strip (the user 2026-08-18) ── #tabbar wraps its tabs into rows and scrolls
// past its max-height cap (150px ≈ four rows), which clipped the fifth row of a many-session strip with
// no way to see more. The #tabbar-resize grip straddles the strip's bottom border: drag DOWN for more
// rows, UP for fewer; it stays a scroll pane at every size. The dragged cap is per-viewer arrangement
// like the tab ORDER (romp:vieworder), so it lives in localStorage — applied at boot, written on release
// (not per-move). A double-click resets to the CSS default. Same pattern as #composer-resize; the grip
// is a SIBLING below the bar (a child would scroll away with the rows), so it also survives every #tabs
// re-render. The content-anchor ResizeObserver above cancels the scroll jump the resize would cause.
{
  const bar = document.getElementById("tabbar");
  const grip = document.getElementById("tabbar-resize");
  if (bar && grip) {
    // `cap` is the preference of record. The APPLIED value re-clamps to the live window on every
    // resize (an oversized cap must not crush the transcript when the window shrinks), but a
    // briefly-small window never rewrites the preference — the applied style heals back when the
    // window grows. Applied through the --tabbar-cap VAR, never the max-height property: the mobile
    // page's `#tabbar{max-height:none}` must keep winning (an inline max-height would override that
    // media rule and clip the mobile header, e.g. after a tablet rotation).
    let cap = parseTabbarH(localStorage.getItem(TABBAR_H_KEY));
    const applyCap = () => {
      if (cap == null) bar.style.removeProperty("--tabbar-cap");
      else bar.style.setProperty("--tabbar-cap", clampTabbarH(cap, window.innerHeight) + "px");
    };
    applyCap();
    window.addEventListener("resize", applyCap);
    let startY = 0, startH = 0, pid = 0, dragging = false, stick = false;
    const onMove = (e: PointerEvent) => {
      if (!dragging) return;
      if (!(e.buttons & 1)) { onUp(); return; }    // release swallowed (context menu, off-window) → end, never strand a phantom drag
      cap = clampTabbarH(startH + (e.clientY - startY), window.innerHeight);
      applyCap();
      // growing the bar shrinks #content, which would push a tail-following view off the tail one
      // sub-threshold step at a time — if it was at the tail when the drag began, keep it there
      const content = document.getElementById("content");
      if (stick && content) content.scrollTop = content.scrollHeight;
    };
    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      try { grip.releasePointerCapture(pid); } catch { /* already released */ }
      grip.classList.remove("dragging");
      document.body.classList.remove("tabbar-resizing");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      if (cap != null) localStorage.setItem(TABBAR_H_KEY, String(cap));
    };
    grip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;                  // a right-click opens menus, never a drag
      e.preventDefault();
      dragging = true;
      startY = e.clientY;
      // Anchor to the EFFECTIVE cap, never the rendered height: with fewer rows than the cap the
      // rect is content-sized, and anchoring there silently collapsed a larger stored cap to about
      // the content height on any drag — invisible at the time, and the clipped strip came back
      // weeks later with no visible cause.
      startH = cap != null ? clampTabbarH(cap, window.innerHeight) : TABBAR_H_DEFAULT;
      const content = document.getElementById("content");
      stick = !!content && nearBottom(content);
      pid = e.pointerId;
      // capture so the drag survives leaving the pane; a pointer already gone throws — the window
      // listeners below carry the drag either way, so a failed capture must not abort the setup
      try { grip.setPointerCapture(pid); } catch { /* pointer already released */ }
      grip.classList.add("dragging");
      document.body.classList.add("tabbar-resizing");
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    });
    // a double-click resets to the CSS default cap — the quick escape hatch, like the composer's
    grip.addEventListener("dblclick", () => { cap = null; applyCap(); localStorage.removeItem(TABBAR_H_KEY); });
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
      if (activeId) applyCommentMarks(activeId);   // the re-window rebuilt turns — re-anchor highlights
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
// ── SUBAGENT VIEWER (plans/subagent-transcripts.md, 2026-09-05) ─────────────────────────────────
// The arrow on an Agent head (or an agent bg-task row) opens the agent's whole transcript as a PEEK tab:
// a client-only pseudo-session in `sessions`/`order` with id `<parentId>/agent/<agentId>`, fed by the
// kernel's {type:"subagent"} frames (openSubagent → first frame now, then a re-push whenever the agent's
// file or liveness moves; closeSubagent stops them). Rendered through the SAME syncView/displayItems/
// renderEvent path as the chat — Compact transcript, folds, day dividers, all of it — read-only. Peek
// mechanics are the chat's own: chatVisible() says a viewer is in the lens only when pinned, so
// assertPeekFor dresses it .tab-peek and pruneSubViews (from setActive) closes it on the next activation.
function openSubagentView(parentId: string, agentId: string, anchorUuid: string | null): void {
  const id = subTabId(parentId, agentId);
  const parent = sessions.get(parentId);
  const cur = sessions.get(id);
  if (!cur) {
    sessions.set(id, {
      id, name: subLabel(null), color: parent?.color || null, events: [],
      status: { state: "idle", sinceEpoch: null, backend: "sub" },
      cwd: parent?.cwd,
      sub: { parentId, agentId, meta: null, running: false, truncated: false, error: null, loaded: false, anchorUuid },
    });
    if (!order.includes(id)) order.push(id);
    vscodeApi?.postMessage({ type: "openSubagent", id: parentId, agentId });
  } else if (anchorUuid && cur.sub) cur.sub.anchorUuid = anchorUuid;
  setActive(id);
}

function closeSubagentView(id: string): void {
  const p = subParts(id);
  if (p) vscodeApi?.postMessage({ type: "closeSubagent", id: p.parentId, agentId: p.agentId });
  pinnedSubs.delete(id);
  dismissSession(id, "close");
}

// Every UNPINNED viewer other than `keep` closes — the peek rule, applied at the one event that ends
// a peek (an activation), never on a timer. A pinned viewer stays until its ✕.
function pruneSubViews(keep: string): void {
  for (const id of Array.from(sessions.keys())) {
    if (id !== keep && isSubId(id) && !pinnedSubs.has(id)) closeSubagentView(id);
  }
}

// A {type:"subagent"} frame: replace the viewer's events in place (the chat's own append/scroll rule
// via appendActive when it is the active tab — follow the bottom only when already there), refresh the
// header and the tab label. A frame for a viewer that is no longer open tells the kernel to stop.
function applySubagentFrame(m: any): void {
  const parentId = String(m.id || ""), agentId = String(m.agentId || "");
  if (!parentId || !agentId) return;
  const id = subTabId(parentId, agentId);
  const s = sessions.get(id);
  if (!s || !s.sub) { vscodeApi?.postMessage({ type: "closeSubagent", id: parentId, agentId }); return; }
  s.sub.loaded = true;
  s.sub.error = m.error ? String(m.error) : null;
  s.sub.running = !!m.running;
  s.sub.truncated = !!m.truncated;
  if (m.meta && typeof m.meta === "object") s.sub.meta = m.meta as SubMeta;
  s.name = subLabel(s.sub.meta);
  if (!s.sub.error) s.events = Array.isArray(m.events) ? (m.events as ChatEvent[]) : [];
  else s.events = [];
  renderTabs();
  if (activeId === id) {
    const v = views.get(id);
    const first = !v || v.rendered === 0;   // the pane held the loader/placeholder — this is the FIRST content
    if (v && s.events.length === 0) { v.rendered = 0; v.stale = true; }   // placeholder → loader/error/empty re-derives
    // the first content lands at the NEWEST end like a fresh tab (showActive → landActive); every later
    // frame is an append that keeps the reader's spot (appendActive's follow-only-when-at-bottom rule)
    if (first) { if (v) { v.stick = true; v.rendered = 0; } showActive(); }
    else appendActive();
    renderSubHead();
  } else {
    const v = views.get(id);
    if (v) v.stale = true;
  }
}

// The viewer's header, a sticky line above its transcript inside #content: "subagent of <parent> ·
// <type> · running|finished", the parent name a link back to the Agent tool head (setActive with the
// head's uuid as the anchor — the chat's own scroll-to-uuid), the pin control, and the "earlier part
// not shown" note when the kernel cut the tail. Hidden whenever the active tab is a real session.
function renderSubHead(): void {
  const content = document.getElementById("content");
  if (!content) return;
  let host = document.getElementById("sub-head");
  const s = activeId ? sessions.get(activeId) : null;
  if (!s || !s.sub) { if (host) host.remove(); return; }
  if (!host) { host = el("div", "sub-head"); host.id = "sub-head"; content.insertBefore(host, content.firstChild); }
  host.replaceChildren();
  const line = el("div", "sub-head-line");
  const kicker = el("span", "sub-head-kicker"); kicker.textContent = "subagent of"; line.appendChild(kicker);
  const parentName = sessions.get(s.sub.parentId)?.name || tabMeta.get(s.sub.parentId)?.name || "its session";
  const link = el("span", "sub-head-parent");
  link.dataset.act = "subParent"; link.dataset.sid = s.sub.parentId;
  if (s.sub.anchorUuid) link.dataset.uuid = s.sub.anchorUuid;
  link.replaceChildren(...hostNameNodes(parentName, s.sub.parentId));
  setTip(link, "back to the launch in the parent session");
  line.appendChild(link);
  const parts = subHeadParts(s.sub.meta, s.sub.running);
  const typ = el("span", "sub-head-type"); typ.textContent = "· " + parts.type; line.appendChild(typ);
  const state = el("span", "sub-head-state " + parts.state); state.textContent = "· " + parts.state; line.appendChild(state);
  const pin = el("span", "sub-head-pin" + (pinnedSubs.has(s.id) ? " pinned" : ""));
  pin.dataset.act = "pinSubagent"; pin.dataset.id = s.id;
  pin.innerHTML = pinIconSvg();
  pin.setAttribute("role", "button"); pin.tabIndex = 0;
  setTip(pin, pinnedSubs.has(s.id) ? "kept — click to let this tab close on its own" : "keep this tab");
  line.appendChild(pin);
  host.appendChild(line);
  if (s.sub.truncated && !s.sub.error) {
    const note = el("div", "sub-head-note");
    note.textContent = "earlier part not shown";
    host.appendChild(note);
  }
}

function renderBgTasks() {
  const host = document.getElementById("bg-tasks");
  if (!host) return;
  host.replaceChildren();
  const s = activeId ? sessions.get(activeId) : null;
  const box = s && s.bgTasks;
  const tasks = (box && box.tasks) || [];
  const count = box ? box.count : 0;
  host.classList.remove("bg-awaited");   // re-derived below from THIS payload (renderAwaitWhy adds its own)
  if (!count || !tasks.length) { renderAwaitWhy(host, s || null); return; }
  host.style.display = "";
  // the AWAITED rows (the user 2026-08-19): when the chip waits on specific tasks, those rows — and
  // the box holding them — wear a thin outline in the chip's green (awaitingTaskIds, the kernel's
  // exact launch-id match); the status DOT keeps its meaning (yellow = the task is running). Keyed on
  // the ids' PRESENCE, never the chip state (the user 2026-08-30: awaited things show even while the
  // session is working — the kernel only ships ids when something genuinely awaits them).
  const awaited = new Set<string>(s!.status.awaitingTaskIds || []);
  host.classList.toggle("bg-awaited", tasks.some((t) => awaited.has(t.id)));
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
    const row = el("div", "bg-task bg-" + (t.status || "running") + (awaited.has(t.id) ? " bg-awaited" : "") + (tOpen ? " open" : ""));
    const rh = el("div", "bg-head");
    rh.dataset.act = "bg-toggle"; rh.dataset.id = t.id;   // the row header toggles; clicks in the detail body don't collapse it
    rh.appendChild(el("span", "bg-dot"));
    const sum = el("span", "bg-sum"); sum.textContent = t.summary || "Background task"; rh.appendChild(sum);
    if (t.agentId) {
      // an AGENT row: the same open-transcript arrow the Agent tool head wears (plans/subagent-transcripts.md).
      // Nested inside the bg-toggle row; the body delegate's closest-[data-act] lookup finds the arrow first,
      // so a click opens the viewer without toggling the row.
      const open = agentOpenButton(t.agentId, null, sid);
      open.classList.add("bg-open-agent");
      rh.appendChild(open);
    }
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

// The Awaiting session's WHY, in the same box when NO tracked tasks claim it (the user 2026-08-13:
// the reason spent a few hours beside the statusline chip — PR #350 — and crowded the composer area;
// this box between transcript and composer is where dispatched work has always surfaced). Same fold
// treatment as the task header: one await-green-dotted line, click → the full why, each awaited item when
// there are several, and a plain-words note on what the state means. No Stop here — an untracked wait
// (a peer's PR, a build) has no process to kill; tracked run_in_background tasks take the list path
// above, which carries one Stop per running row.
function renderAwaitWhy(host: HTMLElement, s: Session | null) {
  // Keyed on awaited CONTENT, never the chip state (the user 2026-08-30, their words paraphrased:
  // even while working, anything the session awaits shows at the chat bottom in the green box). The
  // kernel ships awaitingWhy whenever something is genuinely awaited — armed kernel watches included,
  // mid-turn included — so the fields' presence IS the render condition; the chip keeps its meaning.
  const why = (s && (s.status.awaitingWhy || "").trim()) || "";
  if (!why || !activeId) { host.style.display = "none"; return; }
  host.style.display = "";
  host.classList.add("bg-awaited");   // this whole box IS the awaited thing — the chip's green border
  const sid = activeId;
  const open = bgFoldOpen.has(sid);
  const head = el("div", "bg-fold-head bg-await" + (open ? " open" : ""));
  head.dataset.act = "bg-fold"; head.dataset.id = sid;
  const car = el("span", "bg-caret"); car.textContent = open ? "▾" : "▸"; head.appendChild(car);
  head.appendChild(el("span", "bg-dot"));
  const lab = el("span", "bg-fold-label");
  // the kernel's why leads with the verb ("waiting on a background task: …") — strip it so the
  // labeled header doesn't stutter; the expanded body keeps the full sentence
  const kw = KIND_WORD[(s!.status.awaitingKind || "")] || "";
  const awPeers = s!.status.awaitingPeers || [];
  if (awPeers.length) {
    // a peer-kind wait NAMES the actual session (the user 2026-08-26) — identity colour, quiet
    // host: prefix, the feed box's own treatment; the why tail keeps the wait's verb without
    // restating the names ("delegated to X; " is the names, already rendered)
    lab.append("Awaiting ");
    awPeers.forEach((pr, i) => {
      if (i) lab.append(", ");
      const nm = el("span", "bg-await-peer");
      nm.textContent = (pr.host ? pr.host + ":" : "") + pr.name;
      if (pr.color && pr.color.bg) nm.style.color = pr.color.bg;
      lab.appendChild(nm);
    });
    lab.append(" · " + why.replace(/^delegated to [^;]*;\s*/i, "").replace(/^(waiting on|awaiting)\s+/i, ""));
  } else {
    lab.textContent = "Awaiting" + (kw ? " " + kindWord(s!.status.awaitingKind, s!.status.awaitingCount) : "") + " · " + why.replace(/^(waiting on|awaiting)\s+/i, "");
  }
  head.appendChild(lab);
  host.appendChild(head);
  if (!open) return;
  const det = el("div", "bg-detail bg-await-detail");
  const w = el("div", "bg-await-why"); w.textContent = why; det.appendChild(w);
  const items = s!.status.awaitingTasks || [];
  if (items.length > 1) {   // a single description is already the why — list only a real plurality
    for (const t of items) { const r = el("div", "bg-await-task"); r.textContent = "· " + t; det.appendChild(r); }
  }
  const note = el("div", "bg-await-note");
  note.textContent = s!.status.state === "awaitingBg"
    ? "The session is idle until this finishes; it picks back up on its own when the result lands."
    : "The session keeps working meanwhile; it's told when this lands.";
  det.appendChild(note);
  host.appendChild(det);
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
  // the one canonical hint line (the user 2026-08-15): send, newline, stage, commands — and just
  // "/ for commands", not "type / for commands". The static skeletons carry the same string.
  // WIDTH-ADAPTIVE (the user 2026-08-26, whose narrow pane wrapped the full hint onto a clipped
  // second line): below ~620px of box the hint drops to the core prompt + the one undiscoverable
  // key ("/"); the full key chart stays a wide-desktop hint. Re-fitted event-based on pane resize
  // (the ResizeObserver beside the composer's other wiring), never re-measured per keystroke.
  if (isCoarsePointer()) return "Message this session…";
  const ta = document.getElementById("composer-input");
  if (ta && ta.clientWidth > 0 && ta.clientWidth < 620) return "Message this session…  (/ for commands)";
  return "Message this session…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)";
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
// One dropdown entry. `sub` is the second line for a choice whose consequence is not obvious from its
// label; `sdkOnly` drops the entry on a tmux session, whose backend cannot apply it.
interface MetaChoice { label: string; value: string; sub?: string; sdkOnly?: boolean; color?: number[] | null;
  versions?: { label: string; value: string; learned?: boolean }[]; default?: string }   // model families only (the
  // user 2026-08-25). `default` is the family's remembered version pin, else the family ALIAS; `learned`
  // marks a version the catalog lacks — a running session's CLI reported it (kernel /models).
// Model + effort choices come from the kernel's /models — the ONE list shared with the timeline lanes and the
// judge-tier settings (the user 2026-07-02, who wanted one shared code path, not hardcoded in multiple places), so
// the client holds no model literals (mirrors paletteColors above). Populated in place on load so META_CHOICES
// keeps its reference; the session picker appends its own "Default" (use-the-CLI-default) sentinel — not a model.
const MODEL_CHOICES: { label: string; value: string; color?: number[] | null }[] = [];
const EFFORT_CHOICES: { label: string; value: string; color?: number[] | null }[] = [];
// A CODEX session's pickers speak Codex's vocabulary (the payload's codex section — models from
// the app-server's own list, efforts the four Codex accepts). Empty until the codex backend has
// run: an empty model menu beats offering another vendor's models (docs/codex.md).
const CODEX_MODEL_CHOICES: { label: string; value: string; color?: number[] | null }[] = [];
const CODEX_EFFORT_CHOICES: { label: string; value: string; color?: number[] | null }[] = [];
// Loaded at page load and RE-LOADED on the kernel's {type:"models"} frame — the pick memory moved (a
// version pinned, a family un-pinned by Latest, a refused pin dropped; from this tab, another dashboard,
// or the kernel itself) or the catalog grew. A family's `default` is what its row SENDS, so a list
// fetched once went stale the moment anything changed it: after Latest un-pinned a family, this tab's
// next family click sent the old pinned id and silently re-pinned. Refilled IN PLACE so META_CHOICES
// keeps its reference; event-keyed on the frame, never a poll.
// A response is applied only if it is not OLDER than one already applied: its `rev` is the pick memory's
// revision — the same counter the models frame carries — and two fetches can overlap (a frame during the
// page-load fetch; two quick frames) and resolve out of order, so without the check the STALE list won
// until the next change. A payload without a rev (an older kernel) always applies.
let modelChoicesRev = -1;
function loadModelChoices(): void {
  fetch(kernelUrl("/models"), { cache: "no-store" }).then((r) => r.json()).then((d) => {
    if (typeof d.rev === "number") { if (d.rev < modelChoicesRev) return; modelChoicesRev = d.rev; }
    if (Array.isArray(d.models)) { MODEL_CHOICES.length = 0; MODEL_CHOICES.push(...d.models, { label: "Default", value: "default" }); }
    if (Array.isArray(d.efforts)) { EFFORT_CHOICES.length = 0; EFFORT_CHOICES.push(...d.efforts); }
    if (d.codex && Array.isArray(d.codex.models)) { CODEX_MODEL_CHOICES.length = 0; CODEX_MODEL_CHOICES.push(...d.codex.models); }
    if (d.codex && Array.isArray(d.codex.efforts)) { CODEX_EFFORT_CHOICES.length = 0; CODEX_EFFORT_CHOICES.push(...d.codex.efforts); }
    if (d.commentDefaults) adoptCommentDefaults(d.commentDefaults);
  }).catch(() => { /* picker stays as it was until it lands */ });
}
loadModelChoices();
// The kernel's default-comment settings, RAW ("session" = same as the session — the user 2026-08-29):
// what a new comment thread launches on when the dialog is left untouched. Pre-read so the create
// dialog SHOWS the effective default and a pick stays a deviation; the kernel re-resolves at create,
// so a stale pre-read can mislabel a chip but never mislaunch a thread. Re-fetched at each dialog
// open (the gear may have changed it since page load).
let commentDefaults = { model: "session", effort: "session", fast: "session" };
function adoptCommentDefaults(d: any): void {
  commentDefaults = { model: String(d.model || "session"), effort: String(d.effort || "session"),
                      fast: String(d.fast || "session") };
}
function refreshCommentDefaults(then: () => void): void {
  fetch(kernelUrl("/models"), { cache: "no-store" }).then((r) => r.json()).then((d) => {
    if (d && d.commentDefaults) { adoptCommentDefaults(d.commentDefaults); then(); }
  }).catch(() => { /* keep the page-load pre-read — the kernel still resolves at create */ });
}
// A model VALUE's display label + family tint, version ids included ("claude-opus-5" → its version
// label under the opus family) — the create dialog's default chip must name whatever the setting
// holds, not just top-level families.
function modelChoiceLabel(value: string): { label: string; color?: number[] | null } {
  for (const c of MODEL_CHOICES as MetaChoice[]) {
    if (c.value === value) return c;
    const v = c.versions?.find((x) => x.value === value);
    if (v) return { label: v.label, color: c.color };
  }
  return { label: value };
}
// Permission mode. A tmux session has no slash command for it — the host cycles shift+tab the right
// number of times (the user 2026-06-16) — so the four CYCLE modes are all that backend can reach.
// An SDK session sets it outright over the control channel (set_permission_mode), which is what makes
// Bypass offerable there and only there: on tmux the click would land on a mode the cycle cannot
// express, and _cycle_mode would drop it. `sdkOnly` is the filter, applied in toggleMetaMenu.
// Permission-mode GLYPHS (the user 2026-08-28): each mode gets a small line icon beside its text —
// the statusline badge and the picker rows carry it, always WITH the label (an icon alone is a
// riddle). House icon style (the tag-glyph convention): 16-unit viewBox, stroke currentColor 1.4,
// round caps/joins. The vocabulary: the GATE is a shield — Normal is the shield as-is, Bypass is
// the shield slashed (the gate removed); Accept edits is the pencil (edits pre-approved); Auto is
// the bolt (it decides at speed); Plan is the route pin-to-pin (look before touching); Don't ask
// (renderable, not offerable) is the crossed speech bubble (it will never raise a question).
const MODE_ICONS: Record<string, string> = {
  default: '<path d="M8 2 L13 4 V8 C13 11.4 10.8 13.2 8 14 C5.2 13.2 3 11.4 3 8 V4 Z"/>',
  acceptedits: '<path d="M3.5 12.5 L4.1 10.1 L10.9 3.3 A1.35 1.35 0 0 1 12.8 5.2 L6 12 L3.5 12.5 Z"/><path d="M9.9 4.3 L11.8 6.2"/>',
  auto: '<path d="M8.8 2 L4.2 9 H7.4 L6.9 14 L11.8 6.8 H8.3 Z"/>',
  plan: '<circle cx="4" cy="12" r="1.5"/><circle cx="12" cy="4" r="1.5"/><path d="M5.2 10.8 C7.5 9.5 8.5 6.5 10.8 5.2" stroke-dasharray="2 1.6"/>',
  bypasspermissions: '<path d="M8 2 L13 4 V8 C13 11.4 10.8 13.2 8 14 C5.2 13.2 3 11.4 3 8 V4 Z"/><path d="M3.2 13 L12.8 3"/>',
  dontask: '<path d="M3 3.5 H13 V10 H8.5 L5.5 12.8 V10 H3 Z"/><path d="M3.2 12.6 L12.8 2.6"/>',
};
// the modes that REMOVE the gate rather than move it read in a red hue on the yatharth themes
// (the user 2026-08-31) — CSS-scoped to .chat-theme-yatharth so classic renders untouched
function riskyMode(mode: string | undefined): boolean {
  const k = (mode || "").toLowerCase().replace(/[\u2019' -]/g, "");
  return k === "bypasspermissions" || k === "bypass" || k === "dontask";
}

function modeIconSvg(mode: string | undefined): string {
  // accepts wire values AND display labels (metaButton receives prettyMode's text)
  const raw = (mode || "default").toLowerCase().replace(/[\u2019' -]/g, "");
  const k = raw === "normal" || raw === "" ? "default" : raw;
  const body = MODE_ICONS[k] ?? MODE_ICONS.default;
  return '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
}

// Every mode wears a one-phrase sub-line (T140, the user 2026-08-28: where taglines exist under
// some entries, add analogous ones saying what the other modes are — and 'Accept edits' reads
// just 'Accept'; the mode ids stay the wire's). Each line states what the mode ENFORCES, worded
// from the plumbing, not folklore: the SDK's permission engine owns the gate (romp's can_use_tool
// only renders the asks it fires), auto's gate is the safety classifier (observed enforcement:
// its outage refuses with "cannot determine the safety of"), and bypass's line keeps its 2026-08-15
// cost warning — it never fires can_use_tool, so the approval RECORD goes too, not just the asking.
const MODE_CHOICES: MetaChoice[] = [
  { label: "Normal", value: "default", sub: "asks before edits and commands" },
  { label: "Accept", value: "acceptEdits", sub: "file edits apply without asking; commands still ask" },
  { label: "Auto", value: "auto", sub: "safe actions run unasked; risky ones still ask" },
  { label: "Plan", value: "plan", sub: "reads and proposes only — changes nothing" },
  { label: "Bypass permissions", value: "bypassPermissions", sdkOnly: true,
    sub: "every tool runs unasked, and romp stops showing approvals" },
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
    case "acceptedits": return "Accept";   // one word everywhere the mode renders (T140)
    case "auto": return "Auto";
    case "dontask": return "Don’t ask";
    case "bypasspermissions": return "Bypass";
    case "sandboxed": return "Sandboxed";   // a Codex session's fixed posture (workspace-write)
    default: return "Normal";   // default / normal / unknown
  }
}
const META_CHOICES: Record<MetaKind, MetaChoice[]> = {
  mode: MODE_CHOICES, model: MODEL_CHOICES, effort: EFFORT_CHOICES, fast: FAST_CHOICES,
};
// The choices a menu offers depend on the session's BACKEND: a Codex session speaks Codex's
// vocabulary (its own model list, the four efforts it accepts) — never Claude's, whose aliases
// the codex backend refuses (docs/codex.md). Mode/fast never reach here for codex (see the
// toggleMetaMenu guard / the fast badge's report gate).
function metaChoices(kind: MetaKind, st: Status): MetaChoice[] {
  if (st.backend === "codex") {
    if (kind === "model") return CODEX_MODEL_CHOICES;
    if (kind === "effort") return CODEX_EFFORT_CHOICES;
  }
  return META_CHOICES[kind];
}
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

function metaButton(kind: MetaKind, text: string, forSid?: string | null): HTMLElement {
  const btn = el("span", "meta-btn");
  btn.dataset.kind = kind;
  if (kind === "mode") {   // the permission glyph, always beside its text (never instead of it)
    const ico = el("span", "meta-ico mode-ico");
    ico.innerHTML = modeIconSvg(text);   // refreshed by the sync loop below from st.mode
    btn.appendChild(ico);
    btn.classList.toggle("mode-risky", riskyMode(text));   // kept live by the sync loop
  }
  const label = el("span", "meta-label");
  label.textContent = text;
  btn.appendChild(label);
  const caret = el("span", "meta-caret");
  caret.textContent = "▾";
  btn.appendChild(caret);
  // the styled tip (tip.ts), not a native title — every tooltip wears the one .romp-tip dress
  setTip(btn, kind === "model" ? "change model (sends /model)"
    : kind === "effort" ? "change thinking effort (sends /effort)"
    : kind === "fast" ? "toggle fast mode (sends /fast)"
    : "change permission mode (shift+tab cycle)");
  btn.addEventListener("click", (e) => { e.stopPropagation(); toggleMetaMenu(kind, btn, forSid ?? null); });
  return btn;
}

// The model/effort label tint, from the server-computed colormap RGB (by capability/effort rank, the user
// 2026-07-02) — "" for mode (untinted) or an unknown model/effort, which resets to the default gray.
// a /models-route choice carries classic `color` + yatharth `tone` — pick by theme like the badges
function nonClassicChoiceTone(choice: { color?: number[] | null; tone?: number[] | null } | null | undefined): number[] | undefined {
  if (!choice) return undefined;
  const picked = pickTone(choice.color, choice.tone);
  return picked && picked.length === 3 ? readableRgb(picked) : (picked as number[] | undefined);
}

function metaColor(kind: MetaKind, st: Status): string {
  // fast ON wears the CLI's own fast-mode orange (--fast, a status color) so the badge reads the same
  // here as in the Claude Code TUI; off/cooldown stay the default gray.
  if (kind === "fast") return (st.fast || "").toLowerCase() === "on" ? "var(--fast)" : "";
  const c0 = kind === "model" ? pickTone(st.modelColor, st.modelTone)
    : kind === "effort" ? pickTone(st.effortColor, st.effortTone) : undefined;
  const c = c0 && c0.length === 3 ? readableRgb(c0) : c0;
  return (c && c.length === 3) ? `rgb(${c[0]},${c[1]},${c[2]})` : "";
}

// Build or refresh the model/effort buttons inside #spinner-meta. Called from
// updateStatusline (fresh container) and the 1s ticker (label refresh in place).
function syncMetaControls(meta: HTMLElement, st: Status, forSid?: string | null) {
  // order left→right: mode · model · effort · fast — the mode selector sits LEFT of the model name
  // (the user 2026-06-16); fast exists only when the session reports it (SDK init) AND the model can
  // run it (fastAvailable). Billing moved to the tab's right-click menu (the user 2026-08-09) — no
  // badge here.
  const fast = st.fast && fastAvailable(st) ? st.fast : "";   // reported AND the model can run it — else no dead control
  const want = [st.mode ? "mode" : "", st.model ? "model" : "", st.effort ? "effort" : "", fast ? "fast" : ""].filter(Boolean).join();
  const btns = Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[];
  if (btns.map((b) => b.dataset.kind).join() !== want) {
    meta.replaceChildren();
    if (st.mode) meta.appendChild(metaButton("mode", prettyMode(st.mode), forSid));
    if (st.model) meta.appendChild(metaButton("model", st.model, forSid));
    if (st.effort) meta.appendChild(metaButton("effort", st.effort, forSid));
    if (fast) meta.appendChild(metaButton("fast", prettyFast(fast), forSid));
  }
  for (const b of Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[]) {
    const kind = b.dataset.kind as MetaKind;
    const disp = kind === "mode" ? prettyMode(st.mode) : kind === "fast" ? prettyFast(st.fast)
      : metaCurrent(kind, st);
    const label = b.querySelector(".meta-label") as HTMLElement | null;
    if (kind === "mode") {
      const ico = b.querySelector(".mode-ico") as HTMLElement | null;
      if (ico) ico.innerHTML = modeIconSvg(st.mode);
      b.classList.toggle("mode-risky", riskyMode(st.mode));
    }
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
  document.querySelectorAll(".meta-sub").forEach((n) => n.remove());   // an open version submenu goes with its menu
  metaMenuEl?.remove();
  metaMenuEl = null;
}
function toggleMetaMenu(kind: MetaKind, btn: HTMLElement, forSid?: string | null) {
  const wasOpen = metaMenuEl?.dataset.kind === kind;
  closeMetaMenu();
  if (wasOpen) return;
  // forSid ≠ the active session: a COMMENT THREAD's own statusline (the user 2026-08-25 — one
  // builder for both surfaces, so the popover can never drift off the chat's anatomy again). The
  // thread's status-shape comes from the open popover's frame fields; the ops route by its sid.
  const forThread = !!forSid && forSid !== activeId;
  const th0 = forThread ? openCommentThread()?.th : null;
  if (forThread && !th0) return;
  const status: Status | null = forThread ? threadMetaStatus(th0!)
    : (activeId ? sessions.get(activeId)?.status ?? null : null);
  const opSid = forThread ? forSid! : activeId;
  if (!status || !opSid) return;
  // a pending permission/picker prompt owns the pane's keyboard — injecting a
  // slash command there would answer the prompt instead (host guards this too)
  if (status.state === "needsInput" || status.state === "awaiting") return;
  const s = { status };
  // a Codex session's mode is fixed (sandboxed, plans/codex-backend.md phase 1) — the badge is
  // informational, and opening Claude's permission-mode cycle under it would offer four no-ops
  if (kind === "mode" && s.status.backend === "codex") return;
  const menu = el("div", "meta-menu");
  menu.dataset.kind = kind;
  const pickValue = (value: string, floating = false) => {
    if (vscodeApi) {
      const op: Record<string, unknown> = { type: kind === "model" ? "setModel" : kind === "effort" ? "setEffort" : kind === "fast" ? "setFast" : "setMode", id: opSid, value };
      if (floating) op.floating = true;   // the submenu's Latest row: the kernel forgets the family's pin
      vscodeApi.postMessage(op);
      const was = metaCurrent(kind, s.status);
      metaPending.set(`${opSid}:${kind}`, { was, until: Date.now() + 20_000 });
      btn.classList.add("meta-pending");
    }
    closeMetaMenu();
  };
  let subEl: HTMLElement | null = null;
  const closeSub = () => { subEl?.remove(); subEl = null; };
  // An sdkOnly entry is dropped on tmux rather than shown-and-refused: the backend cannot apply it,
  // and a menu that lists a mode you can't have is worse than one that doesn't. Codex sessions read
  // their own vocabulary via metaChoices (docs/codex.md) before the same filter.
  for (const c of metaChoices(kind, s.status).filter((c) => !c.sdkOnly || s.status.backend === "sdk")) {
    const item = el("div", "meta-item" + (isCurrentMeta(kind, s.status, c.value) ? " current" : ""));
    item.tabIndex = 0;
    const rowIco = kind === "mode" ? el("span", "meta-ico mode-ico") : null;
    if (rowIco) rowIco.innerHTML = modeIconSvg(c.value);
    if (kind === "mode" && riskyMode(c.value)) item.classList.add("mode-risky");
    // model/effort rows wear THEIR OWN rank color (the user 2026-08-31: a picker whose rows are
    // all default-gray codes nothing) — the same /models-fed color+tone the badges use
    if (kind === "model" || kind === "effort") {
      const rowTint = nonClassicChoiceTone(c as { color?: number[] | null; tone?: number[] | null });
      if (rowTint) item.style.color = `rgb(${rowTint.join(",")})`;
    }
    if (c.sub) {
      const head = el("div");
      if (rowIco) head.appendChild(rowIco);
      head.appendChild(document.createTextNode(c.label));
      const sub = el("div", "meta-item-sub");
      sub.textContent = c.sub;
      item.appendChild(head);
      item.appendChild(sub);
    } else if (rowIco) {
      item.appendChild(rowIco);
      item.appendChild(document.createTextNode(c.label));
    } else {
      item.textContent = c.label;
    }
    // A model family with more than one live version wears the side-submenu affordance (the user
    // 2026-08-25): hover or an arrow key reveals every version, each directly pickable with the ✓
    // on the session's current one; clicking the family picks its DEFAULT — the version the user
    // last chose for it, else the newest (the kernel /models `default` field). The submenu opens
    // LEFTWARD: this menu is anchored at the panel's bottom-right, so left is where the room is.
    // One builder serves the chat statusline AND the comment-popover statusline (post-676), so
    // both surfaces grow the affordance together.
    const versions = kind === "model" ? (c.versions || []) : [];
    const openSub = versions.length > 1 ? () => {
      closeSub();
      const sub = el("div", "meta-menu meta-sub");
      // "Latest" heads the submenu: the one gesture back to floating once a family carries a pin — the
      // family row sends the pin, the rows below pin, and a typed "/model fable" leaves the pick memory
      // alone by design. It sends the ALIAS with the `floating` flag, which the kernel's setModel arm
      // hands to _set_model_or_park to forget the family's remembered pin, so the family follows the
      // CLI's newest release again. An explicit user gesture, so it may move state. ✓ when the family
      // is unpinned and the session runs it.
      const pinned = !!c.default && c.default !== c.value;
      const latest = el("div", "meta-item" + (!pinned && isCurrentMeta(kind, s.status, c.value) ? " current" : ""));
      latest.tabIndex = 0;
      const lhead = el("div");
      lhead.textContent = "Latest";
      const lsub = el("div", "meta-item-sub");
      lsub.textContent = pinned ? "unpins — follows the newest " + c.label : "follows the newest " + c.label;
      latest.append(lhead, lsub);
      latest.addEventListener("click", (e) => { e.stopPropagation(); pickValue(c.value, true); });
      latest.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); pickValue(c.value, true); }
        else if (e.key === "ArrowLeft") { e.preventDefault(); closeSub(); item.focus(); }
      });
      sub.appendChild(latest);
      for (const v of versions) {
        const cur = (s.status.model || "").toLowerCase() === v.label.toLowerCase();
        const row = el("div", "meta-item" + (cur ? " current" : ""));
        row.tabIndex = 0;
        row.textContent = v.label;
        if (v.learned) {
          // LOUD, per the fail-loudly rule: this version is in no catalog list — a running session's CLI
          // reported it (kernel /models `learned`) — so the row says so instead of a stale menu hiding
          // a live model. The marker wears the menu vocabulary's sub-line size and opacity.
          const tag = el("span", "meta-item-sub");
          tag.textContent = " new";
          row.appendChild(tag);
          row.title = "Reported by a running session's Claude Code; not yet in romp's version list";
        }
        row.addEventListener("click", (e) => { e.stopPropagation(); pickValue(v.value); });
        row.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); pickValue(v.value); }
          else if (e.key === "ArrowLeft") { e.preventDefault(); closeSub(); item.focus(); }
        });
        sub.appendChild(row);
      }
      document.body.appendChild(sub);
      const rr = item.getBoundingClientRect();
      // side rule (the user 2026-08-25): PREFER opening RIGHT; fall left only when the right edge
      // would clip — measured, never assumed (this menu anchors bottom-right, so left often wins,
      // but a narrow window or a wide panel can leave right-room; the measurement decides)
      const sw = sub.offsetWidth || 140;
      if (rr.right + 4 + sw <= window.innerWidth - 8) sub.style.left = Math.round(rr.right + 4) + "px";
      else sub.style.right = Math.max(8, window.innerWidth - rr.left + 4) + "px";
      sub.style.bottom = Math.max(8, window.innerHeight - rr.bottom) + "px";
      subEl = sub;
      return sub;
    } : null;
    if (openSub) {
      // the caret ALWAYS faces right (the user 2026-08-25) — it marks "expandable", not the side
      item.appendChild(el("span", "meta-caret")).textContent = "\u25B8";
      item.addEventListener("mouseenter", () => openSub());
    } else {
      item.addEventListener("mouseenter", () => closeSub());
    }
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      pickValue(kind === "model" ? (c.default || c.value) : c.value);
    });
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); pickValue(kind === "model" ? (c.default || c.value) : c.value); }
      else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && openSub) {
        // both arrows expand (the side is measured, so either may be where it opens) —
        // ArrowLeft inside the submenu collapses back
        e.preventDefault();
        const sub = openSub();
        (sub.querySelector("[tabindex]") as HTMLElement | null)?.focus();
      }
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
    if (s.status.state === "needsInput" || s.status.state === "awaiting" || s.status.state === "compacting" || s.status.state === "closed") return;
    vscodeApi.postMessage({ type: "compactSession", id: activeId });
    bar.classList.add("ctx-clicked");   // immediate cue; the real compacting state takes over via the poll
  });
  return bar;
}
function setCtxBar(bar: HTMLElement, ctxStr: string | undefined, compacting = false, ctxColor?: number[], ctxOver = false) {
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
    : ctxFallbackColor(pct);   // theme-aware pair; fills stay un-re-encoded (see tabCtxGauge's note)
  if (fill) { fill.style.width = pct + "%"; fill.style.background = fillBg; }
  // ctxOver: the kernel clamps the CLI's "0-100+" percentage at 100 — past it the tokens exceed the
  // CURRENT model's window (a 1M→200k model switch does this instantly). Say so: a silent 100% right
  // after picking a smaller model reads as a broken gauge (the user 2026-09-02).
  if (txt) txt.textContent = ctxOver ? "100%+" : pct + "%";
  bar.title = ctxOver
    ? "context exceeds this model's window — the next turn compacts or trims; click to /compact now"
    : `context ${pct}% used — click to /compact`;
}

const CHIP_LABEL: Record<ChipState, string> = {
  working: "Working", ready: "Ready", needsInput: "Blocked",
  awaiting: "Blocked",   // the legacy name for needsInput — an older remote kernel still sends it
  awaitingBg: "Awaiting",   // idle, waiting on background work it dispatched — the romp await-green, not working-yellow (the user 2026-07-13; recolored from straw 2026-07-22)
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
  // styled tip (tip.ts): a label line + its explanation, so the icon-only square explains itself
  setTip(btn, stuck
    ? "Stop retrying\ninterrupt this thread and hold its auto-retry off until you send it a message"
    : "Stop\ninterrupt this session (same as Ctrl+C)");
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
  if (s.sub) {
    // a subagent viewer has no session state, model or context to show — the header above the
    // transcript says what it is; the statusline just says the pane is read-only
    const ro = el("span", "sub-status-line");
    ro.textContent = "read-only · a subagent's transcript";
    sl.appendChild(ro);
    return;
  }
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
    // idle main thread, waiting on background work it dispatched (the user 2026-07-13): its own await-green
    // chip — no pulse (nothing is computing HERE), but the elapsed timer stays so the wait has a clock
    const chip = el("span", "chip chip-awaitingBg");
    // the KIND rides the label so a glance says WHAT is awaited (the user 2026-08-15) — tooltips are
    // dead on the touch PWA, so the word must be visible; the subject stays in the #bg-tasks box
    const kw = KIND_WORD[s.status.awaitingKind || ""] || "";
    chip.classList.add("chip-awaiting-" + (s.status.awaitingKind || "untyped"));   // per-kind hook, one hue today
    const chipPeers = s.status.awaitingPeers || [];
    if (chipPeers.length) {
      // the pill names the actual session (the user 2026-08-26): "Awaiting <name>", the NAME itself
      // in the peer's identity colour — the dot it launched with retired the same day (round two:
      // it read stupid). The name sits on an always-on ~85% black backing (.chip-peer-name), mostly
      // opaque so ANY identity colour reads against any chip hue (their green-on-green example),
      // translucent enough that the chip's own colour still glows through around it. Several peers
      // keep the one-line rule as a count, names on the tooltip.
      chip.append(CHIP_LABEL.awaitingBg + " ");
      if (chipPeers.length === 1) {
        const nm = el("span", "chip-peer-name");
        // the HOUSE session-reference idiom (the user 2026-08-26, round three — one undifferentiated
        // string read wrong): the shared renderer, so the host prefix wears .host-prefix (italic
        // gray) and the NAME text takes the identity colour — the card headers' own treatment,
        // never a restyled copy
        nm.replaceChildren(...hostPartsNodes(chipPeers[0].host, chipPeers[0].name));
        if (chipPeers[0].color && chipPeers[0].color.bg) nm.style.color = chipPeers[0].color.bg;
        chip.appendChild(nm);
      } else chip.append(chipPeers.length + " peers");
    } else chip.textContent = CHIP_LABEL.awaitingBg + (kw ? " " + kindWord(s.status.awaitingKind, s.status.awaitingCount) : "");   // "Awaiting agent" for one, "agents" for more (T225)
    chip.title = (s.status.awaitingWhy || "idle, waiting on background work it dispatched")
               + " — clears when the result lands";
    sl.appendChild(chip);
    const timer = el("span", "status-timer");
    timer.id = "work-timer";
    timer.textContent = elapsedMs(s.status.sinceEpoch);
    sl.appendChild(timer);
    // The WHY renders in the #bg-tasks box between transcript and composer (renderAwaitWhy), not
    // here — a reason line beside the chip crowded the composer area (the user 2026-08-13, on the
    // same day's PR #350 that first surfaced it here).
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

  // The right-side cluster — dir · branch · mode/model/effort/fast badges · ctx battery — grouped in ONE
  // container (.sl-right) that carries the right-justify margin and wraps INTERNALLY with right-aligned
  // rows. Grouped, not flat: when a narrow pane wraps the statusline, flat children restart each extra row
  // at the LEFT edge (justify only reaches the row holding the auto margin) — the user 2026-08-10, on a
  // phone, wanted the wrapped controls to stay clustered on the right.
  const right = el("span", "sl-right");
  // The session's working directory (the current one — a tab-menu move changes it), leading the right-side
  // cluster — just left of the mode/model/effort controls (the user 2026-06-23). Basename only; full path
  // on hover. Empty (rare, no cwd) it's a zero-width spacer.
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
  setCtxBar(bar, s.status.ctx, s.status.state === "compacting", pickTone(s.status.ctxColor, s.status.ctxTone), s.status.ctxOver);
  right.appendChild(bar);
  // stop/interrupt button — at the FAR RIGHT of the statusline (the user 2026-08-28; it sat
  // beside the state chip on the left before), riding inside the right cluster so a wrapped
  // narrow statusline keeps it with the controls. Shown while busy (working/compacting) AND while
  // stuck retrying / blocked, where it doubles as the per-thread auto-retry off-switch (the user
  // 2026-07-06). Omitted in idle states (nothing to interrupt — the user 2026-06-19) and while
  // INTERRUPTING (the stop is already in flight; re-pressing it is a lie — the user 2026-07-02).
  if (s.status.state === "working" || s.status.state === "compacting"
      || s.status.state === "retrying" || s.status.state === "blocked") right.appendChild(stopButton(s.status.state));
  sl.appendChild(right);
  pruneTip();   // a rebuilt statusline tears tip anchors (the stop button) out mid-hover — drop the orphan (PR #763 item 8; the feed's render does the same)
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
let fireStage: () => void = () => { /* assigned by the composer closure */ };

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
// NOT persisted with drafts (a reload cannot resurrect the bytes) — but each entry RETAINS its encoded
// payload until the ack retires it, because the ack rides the very socket the dropFile went out on: a
// kernel restart between ship and ack means that ack can never arrive, and the chip pulsed forever
// while a held send never fired (T215). The reconnect event (romp:wsup) re-ships every retained
// payload; shipId lets an ack retire exactly the chip that asked, so a duplicate ack from a re-ship
// race is dropped instead of attached to whatever tab is active. Names of ships lost to a full page
// RELOAD persist beside the drafts and surface as a loud re-attach toast at startup (the VS Code pipe
// reloads its webview on reconnect, so the wedge there is a vanished chip, not an eternal one).
interface PendingShip { name: string; shipId: string; b64?: string }
let shipSeq = 0;   // per-page mint — a shipId only ever meets acks for this page's own ships
const pendingShips = new Map<string, PendingShip[]>();   // sid -> ships awaiting droppedPath

// The kernel saves a shipped file as drops/<ms>-<sanitized name> (_save_dropped_file). Mirror its
// sanitizer so an ack can be matched back to the pending chip it retires by basename suffix; the
// FIFO fallback in resolvePendingShip covers any mismatch (e.g. non-ASCII, where Python's \w and
// JS's \w disagree).
function shipSafeName(name: string): string {
  return (name.replace(/[^\w.-]+/g, "_").slice(-80)) || "drop";
}

function addPendingShip(id: string | null, name: string, shipId: string): void {
  if (!id) return;
  const list = pendingShips.get(id) || [];
  list.push({ name, shipId });
  pendingShips.set(id, list);
  persistDrafts();   // the NAMES ride the draft store so a reload can say what it lost (T215)
  if (id === activeId) renderComposerFiles(id);
}

// The sid holding a given shipId (null if none): the ack handler's stray-duplicate gate — an ack
// carrying a shipId no pending entry holds answers a ship already retired (a re-ship raced the
// original ack across a reconnect), and must be dropped, not attached to the active composer.
function shipOwner(shipId: string): string | null {
  for (const [id, list] of pendingShips) if (list.some((p) => p.shipId === shipId)) return id;
  return null;
}

// An ack (or nack) retires ONE pending chip: the entry whose shipId the ack echoes (exact — new
// kernels echo it); else the entry whose sanitized name `key` ends with — `key` is the saved path on
// ack (basename <ms>-<safe name>) or the raw name on nack, and both end with the sanitized original —
// else the oldest (the kernel answers a connection's dropFiles in order). Searched active-tab-first
// across all sessions because a legacy ack carries no session id. Returns the sid whose chip it
// retired (null if none matched), so the ack can attach the file to the composer that SHIPPED it —
// attaching to whatever tab was active at ack time put a slow upload's file on the wrong session's
// strip after a mid-flight tab switch (the user 2026-08-16, the send-while-uploading report's
// second face).
function retirePendingShip(key: string, shipId?: string): string | null {
  const k = "-" + shipSafeName(key.split("/").pop() || key);
  const ids = activeId ? [activeId, ...pendingShips.keys()] : [...pendingShips.keys()];
  for (const id of ids) {
    const list = pendingShips.get(id);
    if (!list || !list.length) continue;
    let i = shipId ? list.findIndex((p) => p.shipId === shipId) : -1;
    if (shipId && i < 0) continue;   // an id-carrying ack retires ONLY its own entry, wherever it lives
    if (i < 0) i = list.findIndex((p) => k.endsWith("-" + shipSafeName(p.name)));
    list.splice(i >= 0 ? i : 0, 1);
    if (!list.length) pendingShips.delete(id);
    persistDrafts();
    if (id === activeId) renderComposerFiles(id);
    return id;
  }
  return null;
}

// Re-ship the retained payloads whose ack socket just came back. That socket died with the acks
// still owed, so re-sending the bytes is the ONLY way the chip's retiring event can still arrive;
// the kernel just saves a fresh copy (a duplicate file in drops/ is an orphan, never attached — the
// shipId-matched ack retires this chip and any stray twin ack is dropped by shipOwner's gate). An
// entry with no payload yet is one whose FileReader is still encoding — its own onload ships through
// the fresh socket, so it is deliberately skipped here, never doubled.
// SCOPED to the socket that reconnected: a remote session's ack rides that host's RELAY, not the
// pane socket, so romp:wsup re-ships local entries only, and the relay's own (re)open event below
// carries its host (review finding 2026-09-01: v1 listened to romp:wsup alone, whose entries-of-
// every-host re-ship both missed the remote relay's own redial — which fires no romp:wsup — and
// re-sent remote payloads into a relay that was still down; the kernel-reported hostUp is NOT a
// re-ship event either — see the handler — because it precedes the relay's open by a round trip).
function reshipPendingUploads(hosts?: readonly string[]): void {
  if (!vscodeApi) return;
  for (const [id, list] of pendingShips) {
    const h = hostOf(id);
    if (hosts ? hosts.indexOf(h) < 0 : h) continue;   // no scope → the local kernel's own entries
    for (const p of list) {
      if (!p.b64) continue;
      const msg: { type: string; name: string; b64: string; shipId: string; id?: string } =
        { type: "dropFile", name: p.name, b64: p.b64, shipId: p.shipId };
      if (id) msg.id = id;
      vscodeApi.postMessage(msg);
    }
  }
}
window.addEventListener("romp:wsup", () => reshipPendingUploads());
// federation dispatches this on a host relay socket (re)connect — the exact event that makes that
// host's owed acks reachable again; the detail names the host, so only its entries re-ship
window.addEventListener("romp:hostRelayUp", (e) => {
  const h = String((((e as CustomEvent).detail || {}) as any).host || "");
  if (h) reshipPendingUploads([h]);
});

// Sids whose SEND is HELD until every pending ship acks (the user 2026-08-16: sending mid-upload
// silently dropped the attachment — the send read only the acked list). Armed by the confirm's
// "wait" pick in sendComposer's gate; fired by the LAST droppedPath ack (event-based), cancelled by
// a dropSaveFailed nack or by any successful send for the sid.
const sendOnShip = new Set<string>();
// The ship-gate confirm currently open, by owning sid (the user 2026-08-19): while the "still
// uploading" dialog is up, the upload FINISHING answers the question itself — the dialog closes and
// the send fires, no click needed. The deciding event is the same last-ship ack a held send waits on.
let shipGateSid: string | null = null;
// Assigned by setupComposer (sendComposer lives in its closure); the WS ack handler fires a held
// send through it when the last pending ship lands.
let fireHeldSend: () => void = () => {};

// Persist drafts across a full RELOAD (the user 2026-06-25: a half-typed message must survive a refresh, not
// only a tab switch). The Map is in-memory, so mirror it into the webview's persisted state — the same store
// that remembers the active tab — and reload it at startup. restoreActiveDraftOnce() drops the active tab's
// draft back into the box ONE time after load, and only when the box is empty, so it never clobbers live typing.
// Citations persist alongside drafts (same lifecycle: survive reload + tab switch, cleared on send/dismiss).
function persistDrafts(): void {
  try {
    vscodeApi?.setState?.({ ...(vscodeApi.getState?.() || {}), drafts: Object.fromEntries(drafts),
                            citations: Object.fromEntries(composerCitations),
                            files: Object.fromEntries(composerFiles),
                            staged: stagedMsgs.entries(),
                            // NAMES of ships still awaiting their ack — never the bytes. A reload
                            // cannot resurrect the upload (the payload dies with the page), so the
                            // next load reads these to say LOUDLY what was lost (T215; the VS Code
                            // pipe reloads its webview on kernel reconnect, taking mid-flight ships
                            // with it — the silent-vanish face of the same wedge).
                            shipsInFlight: [...pendingShips.values()].flat().map((p) => p.name) });
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
  // Ships that were still awaiting their ack when the page died (persistDrafts's shipsInFlight): the
  // payload died with the page, so the upload is LOST — say so loudly with the re-attach affordance
  // spelled out, and clear the record so the toast fires once, not on every future load (T215). This
  // is the reload face of the ship/ack wedge; the same-page reconnect face re-ships via romp:wsup.
  const lostShips = ((vscodeApi?.getState?.() || {}) as any).shipsInFlight;
  if (Array.isArray(lostShips)) {
    const names = lostShips.filter((x): x is string => typeof x === "string" && !!x);
    if (names.length) {
      warnToast(names.join(", ") + (names.length === 1
                ? " was still uploading when this page reloaded, so it was NOT attached — attach it again."
                : " were still uploading when this page reloaded, so they were NOT attached — attach them again."));
      // clear the record DIRECTLY — v1 called persistDrafts() here, which reads stagedMsgs, declared
      // BELOW this block: the TDZ throw landed in persistDrafts' own catch and the clear silently
      // never ran, so this toast re-fired on every reload until an unrelated composer gesture
      // (review finding 2026-09-01, verified on the emitted bundle). No eval-order dependency now.
      vscodeApi?.setState?.({ ...(vscodeApi.getState?.() || {}), shipsInFlight: [] });
    }
  }
} catch { /* ignore */ }

// Composer EDIT mode (per session): set when the user clicks a bubble's edit affordance — the composer
// then sends a rewindSend (branch from just before that message) instead of a plain message. The chip
// strip shows an "Editing message" pill whose ✕ (or Esc in the box) cancels back to normal sending.
const composerEdits = new Map<string, { uuid: string; orig: string }>();

// STAGED messages (the user 2026-08-15): compose against a highlight, ⌘/Ctrl+⏎ to HOLD it — citation
// chips and all — clear the box and keep reading; repeat. Nothing sends until the next plain send
// (the stack flushes first, in stage order, the typed message last) or the strip's Send now.
// Deliberately NOT the queue and never called "queued": queued is romp's injection-side wait (sent,
// pending injection); staged is user-side — unsent by choice, each message keeping the context it was
// written against so the batch reads as quote → comment, quote → comment. Per-tab, persisted with the
// drafts (a reload or tab switch never drops a stack). The pure stack rules live in staged-messages.ts,
// executed by its test; this file owns the strip DOM and the send routing.
const stagedMsgs = new StagedStack();
const stagedOpen = new Set<string>();   // sid:index of staged chips expanded to their full text
try { stagedMsgs.restore(((vscodeApi?.getState?.() || {}) as any).staged); } catch { /* ignore */ }

// One routing owner for a user message (the deliver path and the staged flush both speak it): a goal
// chip rides askFollowUp, quote chips wrap client-side, a bare message is a plain send with the
// optimistic bubble (chip sends have their own kernel-side echo).
function routeUserMessage(sid: string, text: string, cites: Citation[] | undefined, imgPaths?: string[]): void {
  if (!vscodeApi) return;
  const goalCite = cites?.find((c) => c.itemId);
  const quoteCites = cites ? cites.filter((c) => c.quote) : [];
  // EVERY branch echoes optimistically (the user 2026-08-23: quoted and follow-up sends showed
  // nothing until the kernel round-tripped, while plain sends painted instantly — the exact
  // inconsistency reported). The quote branch echoes the COMPOSED body, which is byte-identical to
  // what lands (quoteReplyBody IS the send path), so the reconcile's includes() match is exact; the
  // follow-up echoes the typed words, a substring of the goal-wrapped landing.
  if (goalCite?.itemId) { vscodeApi.postMessage({ type: "askFollowUp", itemId: goalCite.itemId, text, sid }); registerOptimistic(sid, text, imgPaths); }
  else if (quoteCites.length) { const body = quoteReplyBody(quoteCites, text); vscodeApi.postMessage({ type: "sendMessage", id: sid, text: body }); registerOptimistic(sid, body, imgPaths); }
  else { vscodeApi.postMessage({ type: "sendMessage", id: sid, text }); registerOptimistic(sid, text, imgPaths); }
}

/** Release the tab's staged stack (deliver's guards — host down, provisional — run before this in the
 *  send path; Send now re-checks reachability itself). Returns how many went. */
function flushStaged(sid: string): number {
  const batch = stagedMsgs.takeAll(sid);
  for (const s of batch) routeUserMessage(sid, s.text, s.cites as Citation[]);
  if (batch.length) { persistDrafts(); renderStagedStrip(sid); }
  return batch.length;
}

function renderStagedStrip(id: string | null): void {
  const strip = document.getElementById("composer-staged");
  if (!strip) return;
  strip.replaceChildren();
  const list = id ? stagedMsgs.list(id) : [];
  if (!id || !list.length) { strip.style.display = "none"; return; }
  strip.style.display = "flex";
  const head = el("div", "staged-head");
  const lbl = el("span");
  lbl.textContent = list.length + " staged — sends with your next message";
  lbl.title = "⌘⏎ (or Ctrl+⏎) stages what you've typed, quote chips and all, without sending. "
    + "A plain send releases them in order with your new message last — or Send now releases them alone.";
  const go = el("button", "staged-go");
  go.textContent = "Send now";
  go.addEventListener("click", () => {
    if (!id) return;
    if (hostIsDown(id) || isProvisionalId(id)) {
      warnToast("Can't send yet — the session isn't reachable. They stay staged.");
      return;
    }
    flushStaged(id);
  });
  head.append(lbl, go);
  strip.appendChild(head);
  list.forEach((s, i) => {
    const chip = el("div", "staged-chip");
    // each staged reply keeps the CONTEXT it was written against visible inside its own dotted box
    // (the user 2026-08-15): one small context chip per quote, independently expandable — clicking
    // the quote opens the quote, clicking anywhere else in the box opens the reply text. Both folds
    // are keyed so they survive the strip re-render.
    const quotes = (s.cites as Citation[]).filter((c) => c && c.quote);
    for (let j = 0; j < quotes.length; j++) {
      const ck = id + ":" + i + ":" + j;
      const cOpen = stagedOpen.has(ck);
      // the SAME blue citation pill the composer wears (the user 2026-08-15: the context must keep
      // "its little blue background chip", not restyle inside the staged box)
      const cite = el("div", "composer-chip staged-cite" + (cOpen ? " open" : ""));
      const cm = el("span", "composer-chip-mark"); cm.textContent = "“";
      const cl = el("span", "composer-chip-label"); cl.textContent = quotes[j].quote || "";
      const ch = el("span", "staged-expand"); ch.textContent = cOpen ? "(collapse)" : "(expand)";
      cite.append(cm, cl, ch);
      cite.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (stagedOpen.has(ck)) stagedOpen.delete(ck); else stagedOpen.add(ck);
        renderStagedStrip(id);
      });
      chip.appendChild(cite);
    }
    const row = el("div", "staged-row");
    const mark = el("span", "composer-chip-mark");
    mark.textContent = "•";
    const label = el("span", "composer-chip-label");
    label.textContent = s.text || "(context only — sends with your message)";
    // one line, ellipsized IN BOUNDS, with a colored "(expand)" that is visibly chrome, not message
    // text; click toggles the full text — the context-fold idiom (the user 2026-08-15, whose staged
    // line ran off the right edge with no ellipsis and no way to read the rest)
    const open = stagedOpen.has(id + ":" + i);
    if (open) chip.classList.add("open");
    const hint = el("span", "staged-expand");
    hint.textContent = open ? "(collapse)" : "(click to expand)";
    const toggle = () => {
      const k = id + ":" + i;
      if (stagedOpen.has(k)) stagedOpen.delete(k); else stagedOpen.add(k);
      renderStagedStrip(id);
    };
    chip.addEventListener("click", toggle);
    const x = el("button", "composer-chip-x");
    x.setAttribute("aria-label", "Discard staged message");
    x.textContent = "✕";
    x.addEventListener("click", (ev) => { ev.stopPropagation(); stagedMsgs.removeAt(id, i); persistDrafts(); renderStagedStrip(id); });
    row.append(mark, label, hint, x);
    chip.appendChild(row);
    strip.appendChild(chip);
  });
}

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
  // one chip per held context, in the order they were added — the strip wraps them in rows, and the
  // Stage button appended after the loop rides the end of the LAST row (the chip's max-width cedes it room)
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
  // The STAGE button (the user 2026-08-23; moved 2026-08-24): nobody discovers ⌘⏎ on their own, so
  // while quote context is held the strip carries its visible face — IMMEDIATELY AFTER the chips it
  // acts on (right-justified it floated detached, and its meaning didn't read), gone with them.
  // Same action as the shortcut: stage the context (and any typed text) and keep going.
  {
    const st = el("button", "composer-stage-btn") as HTMLButtonElement;
    st.type = "button";
    st.textContent = "Stage";
    st.title = "hold this context (and anything typed) for one combined send later — ⌘⏎ does the same";
    st.addEventListener("click", (e) => { e.stopPropagation(); fireStage(); });
    strip.appendChild(st);
  }
}

// The attachment strip: one little thumbnail per dropped/pasted/picked file, above the textarea (the
// user 2026-08-04). An image shows its pixels — same-origin /file bytes on the web dashboard, the host
// imgRequest data-URL flow in the VS Code webview (the sandbox can't reach the kernel origin) — and any
// other file wears a compact ext + name chip. Click opens the file (the same openFile the path links
// use); the ✕ removes just that attachment. Rendered per session, like the citation chips beside it.
function renderComposerFiles(id: string | null): void {
  const strip = document.getElementById("composer-files");
  if (!strip) return;
  // the held-send state rides the send button (see sendOnShip): dimmed + titled while a "wait for
  // the upload" pick is armed, restored the moment the hold fires or cancels — this renderer runs
  // on every strip change AND every tab switch, so the button always reflects the ACTIVE tab
  const sendBtn = document.getElementById("composer-send");
  if (sendBtn) {
    const held = !!id && sendOnShip.has(id);
    sendBtn.classList.toggle("send-held", held);
    // through the styled tip, not a native title: the button already wears setTip("Send (Enter)"),
    // and a native title beside it showed BOTH boxes while a hold was armed (2026-09-02)
    setTip(sendBtn, held ? "Send (Enter)\nsends when the upload finishes" : "Send (Enter)");
  }
  strip.replaceChildren();
  const paths = (id ? composerFiles.get(id) : undefined) || [];
  const pending = (id ? pendingShips.get(id) : undefined) || [];
  if (!paths.length && !pending.length) { strip.style.display = "none"; return; }
  strip.style.display = "flex";
  // A held send IS a staged message and must LOOK like one (the user 2026-08-22): the same head line
  // the staged strip wears — what will happen, live count, and the way out. Before this, the only
  // cue after "Wait for the upload" was the dimmed send button, which read as nothing happening.
  // Re-rendered here because this renderer already runs on the wait click, every ack, and every tab
  // switch — the exact events the line must track.
  if (id && sendOnShip.has(id)) {
    const head = el("div", "staged-head held-head");
    const lbl = el("span");
    lbl.textContent = "staged — sends when the upload finishes"
      + (pending.length > 1 ? " (" + pending.length + " still uploading)" : "");
    lbl.title = "You chose to wait. The message sends itself the moment the last attachment lands.";
    const cancel = el("button", "staged-go");
    cancel.textContent = "Cancel";
    cancel.title = "keep the message and attachments — just stop the automatic send";
    cancel.addEventListener("click", () => { sendOnShip.delete(id); renderComposerFiles(id); });
    head.append(lbl, cancel);
    strip.appendChild(head);
  }
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
        const w = buildPathImg(p, id);             // VS Code: host-read data URL fills in; chip until then
        w.classList.add("composer-file-hostimg");
        box.appendChild(w);
      }
    } else {
      box.appendChild(composerFileDoc(p));
    }
    box.addEventListener("click", () => { openPath(p, id || null); });
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
  pending.forEach((p, i) => {
    const box = el("span", "composer-file composer-file-pending");
    box.title = p.name + " — uploading";
    const nm = el("span", "composer-file-name");
    nm.textContent = p.name;
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
      persistDrafts();   // the dismissed chip's name must not resurface as a reload-loss toast
      // dismissing the LAST chip settles an armed hold the same way a nack does — cancelled
      // LOUDLY, never auto-sent: the ✕ removed the very entry whose ack the hold was waiting
      // for, so left armed it would wait forever (review finding 2026-09-01)
      if (id && !(pendingShips.get(id) || []).length) {
        const held = sendOnShip.delete(id);
        const gateWasOpen = shipGateSid === id;
        if (gateWasOpen) { shipGateSid = null; closeConfirm(null); }
        if (held || gateWasOpen) warnToast("The pending upload was dismissed — your held message was NOT sent.");
      }
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
  return text ? sections.join("\n\n") + "\n\n" + text : sections.join("\n\n");
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

// The chat's back/forward trail (the user 2026-08-14) — the rules live in nav-history.ts, executed
// by its test. The deps close over this pane's real state: the active tab + #content scroll for
// "now", the sessions map for liveness, and a land that pre-seeds the per-tab scroll restore (views)
// BEFORE setActive — so the entering tab's own restore applies the remembered spot — then re-asserts
// it once the pane is visible (a same-tab jump returns early from setActive and needs the direct set).
const navHist = new NavHistory({
  now: () => {
    const c = document.getElementById("content");
    return activeId && c ? { sid: activeId, top: c.scrollTop } : null;
  },
  alive: (sid) => sessions.has(sid),
  apply: (spot) => {
    const v = views.get(spot.sid);
    if (v) { v.scrollTop = spot.top; v.stick = false; }   // land on the remembered spot, never the live bottom
    setActive(spot.sid);
    const c = document.getElementById("content");
    if (c) whenChatVisible(() => { if (activeId === spot.sid) c.scrollTop = spot.top; });
  },
});

function setActive(id: string, anchor?: string, anchorT?: number, anchorKind?: string) {
  noteMru(id);
  // EPHEMERAL PEEK (see peekId): an out-of-view target opens as the peek; activating anything else
  // drops it. Before the already-active early-return, so a re-focus of a hidden session re-asserts
  // its peek even when nothing else changes.
  assertPeekFor(id);
  pruneSubViews(id);   // an unpinned subagent viewer is a peek: any other activation closes it (and its kernel pushes)
  if (isSubId(id) && !sessions.has(id)) {
    // a viewer id from the nav trail / a stale state whose tab is gone → reopen it (openSubagentView
    // lands back here with the pseudo-session in place)
    const p = subParts(id);
    if (p) { openSubagentView(p.parentId, p.agentId, null); return; }
  }
  if (activeId === id && anchor == null && anchorT == null) return; // already active, nothing to do
  navHist.record();   // every real navigation records the spot being LEFT (the user 2026-08-14: Ctrl+M / Ctrl+, walk the trail)
  closeMetaMenu(); // an open model/effort menu targets the tab we're leaving
  // a comment popover belongs to its parent session's view — leaving that session closes it (the
  // user 2026-08-13: it lingered over the next tab's chat); the highlight reopens it any time
  if (openCommentKey && openCommentKey.sid !== id) closeCommentPop();
  if (pendingCommentAnchor && pendingCommentAnchor.sid !== id) closeCommentPop();
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
    clearComposerNote();   // a real switch binds the box to `id` — a note about the last hand-over is stale (T236)
    if (activeId) {
      if (ta.value) drafts.set(activeId, ta.value); else drafts.delete(activeId);
      // A citation chip is a "reply to this card right now" intent — switching tabs abandons it, so drop the
      // leaving tab's chip (the user 2026-07-01). A feed click that seeds a chip sets it AFTER this switch.
      composerCitations.delete(activeId);
    }
    ta.value = drafts.get(id) ?? "";
    growComposer(ta);
    renderComposerChips(id);   // the entering tab's own citation chip (if any)
    renderStagedStrip(id);     // …and its staged stack (per-tab; the strip follows the switch)
    renderComposerFiles(id);   // …and its attachment thumbnails (draft lifecycle: they survive the switch)
    persistDrafts();   // the leaving tab's draft was just stashed → keep the persisted copy in sync
  }
  pendingAnchor = anchor ?? null;
  if (anchor) flashedAnchor = null;        // a fresh navigation re-arms the one-per-navigation flash
  pendingAnchorIntent = anchor ? (anchorKind ?? null) : null;
  if (anchor) armSeek(id, anchor, anchorKind ?? null);   // durable until land / ✕ / backstop (see armSeek)
  activeId = id;
  try { vscodeApi?.setState?.({ ...(vscodeApi.getState?.() || {}), activeId: id }); } catch { /* ignore */ }
  renderTabs();
  showActive();
  schedulePrebuild(); // warm the OTHER tabs in idle (MRU-first) so the next switch is instant
}

function cycleTab(dir: number) {
  const ord = visibleOrder();                   // never cycle onto a view-hidden session
  if (ord.length < 2 || !activeId) return;
  const i = ord.indexOf(activeId);
  if (i < 0) return;
  setActive(ord[(i + dir + ord.length) % ord.length]);
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
  retryCmtCreates(String(msg.id || ""));   // a session frame = the kernel re-parsed → retry a lag-refused create (T106)
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
  // a session frame can ride the kernel's chat build cache with a stale name/color embedded (its sig
  // watches transcript+states only) — the freshest tabOrder meta wins over it, pending guard included
  const tm = tabMeta.get(msg.id);
  if (tm) applyMetaToSession(s, tm, pendingTabMeta.get(msg.id));
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
  // A view that never held an event is a PLACEHOLDER (a fork's provisional tab, a revive's stub):
  // the payload that fills it is the tab's FIRST content-bearing build, not an append onto a
  // transcript someone is reading. The append path measured "was the reader at the bottom?"
  // against a one-line placeholder that cannot overflow, landed the whole arriving history at
  // scrollTop 0, and the never-yank rule then held the top forever (the user 2026-09-02: an
  // opened or forked session sat at the top after its context loaded). A first build takes the
  // showActive branch below, where landActive pins the bottom exactly like a brand-new tab.
  const firstBuild = !!(existed && prev && !prev.events.length && msg.events && msg.events.length);
  // Preserve the reader's position across ANY active-tab rebuild (fork OR slid tail-window): capture whether
  // they were at the bottom + their anchor turn BEFORE we drop/rebuild the DOM, so a push never SNAPS a
  // scrolled-up reader down (the user 2026-07-06). Only a genuinely-at-bottom reader follows new content.
  let _scrollContent: HTMLElement | null = null, _scrollAnchor: { uuid: string; y: number } | null = null, _wasNear = true;
  if (msg.id === activeId && !(existed && !forked && !firstBuild)) {   // only the rebuild branch (appendActive preserves on its own)
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
  // The torn-down session is BACK while its hand-over note still holds the box: the user has done nothing
  // since (a click into the box, a tab switch or the ✕ would have retired it), so put them back exactly
  // where they were — its tab active, its kept draft in the box. Merely retiring the note here left the
  // fallback tab active with the box unheld, and the next blind keystroke landed there after all (the
  // T236 harness, omission path: the tab is re-listed within seconds). setActive clears the note.
  if (composerNoteSid === msg.id) setActive(msg.id);
  const adopted = !activeId;
  if (adopted) { activeId = msg.id; loadComposerFor(msg.id, true); }   // adopted as the only tab → its draft too (T236: the once-per-page restore below never covers a session that LEFT and came back)
  if (wantActive && msg.id === wantActive) { wantActive = null; setActive(msg.id); }   // restore persisted tab on arrival
  renderTabs();                                   // a new id appended to `order` above → strip repaints in kernel order
  // Active tab: a content refresh appends + preserves scroll (appendActive); a new tab or a fork
  // lands at the bottom/anchor (showActive). This is what keeps new pushes from snapping to bottom.
  if (msg.id === activeId) {
    // an ADOPTION is a first show even for a payload this page already held: the no-active-tab state hid
    // every view, and appendActive never re-reveals one (T236 harness: a tab active over "No session open")
    if (existed && !forked && !firstBuild && !adopted) {
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
  retryCmtCreates(String(msg.id || ""));   // ditto for the delta path (T106)
  const s = sessions.get(msg.id);
  if (!s) { requestFullSession(msg.id); return; }   // a delta with no base is PROOF of desync (see chatTail)
  s.events = msg.events || s.events;
  const before = awaitKey(s.status);
  s.status = msg.status || s.status;
  reconcileRewind(s);                    // pending-rewind overlay + the editable-bubble set, from the fresh payload
  reconcileOptimistic(s);                // re-assert (or retire) any in-flight optimistic sends on this push
  renderTabs();                          // status/chip change only — repaint, never re-order (the user 2026-06-27)
  if (msg.id === activeId) {
    appendActive();
    renderLedger(); // refresh the summary box (ages + any new items) as the active session works
    if (awaitKey(s.status) !== before) renderBgTasks();   // the box rides the chip's own frame (T225; see chatTail)
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
// A reconnect mints a FRESH kernel-side client (its echat starts empty, so full frames are already
// guaranteed) — but an ask parked against the dead socket would gag the new socket's repair path
// forever (awaitingFull only clears when the reply lands, and the dead socket's never will).
window.addEventListener("romp:wsup", () => awaitingFull.clear());

function chatTail(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) {
    // A delta for a session we hold NO base for is PROOF of desync, not noise to ignore: the full
    // frame was sent while this document had no message listener yet (the pusher fires from the
    // moment the socket opens; the 1.4MB bundle can still be evaluating), and the kernel's echat
    // advances on SEND — so deltas are all it will ever volunteer, and the tab sat on the
    // « opening … » placeholder forever (the user 2026-09-02; a duplicated browser tab won the
    // race via cached bundles, a reload only sometimes). Ask for the base instead of waiting.
    requestFullSession(msg.id);
    return;
  }
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
  const before = awaitKey(s.status);
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
    // THIS is the frame that flips the chip (T225): a status-only change reaches a caught-up client as a
    // chatTail with an empty suffix and the full status — awaitingWhy/Kind/Count/Tasks included. The box
    // rendered only from the full-session path, so the chip read "Awaiting agents" with no box until a
    // transcript change happened to send a full frame (31s+ in the user's shot; never, in the quiet lab).
    // Render the box from the SAME frame, keyed on the awaited fields CHANGING — never on the per-second ticks.
    if (awaitKey(s.status) !== before) renderBgTasks();
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
    flashedAnchor = null;                  // ditto: this path is also a user navigation
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

// The awaiting fields the #bg-tasks box renders from (renderAwaitWhy / the awaited-row outline) — one
// key per status, so a status-only frame re-renders the box exactly when THESE change (the chip's own
// flip is one of them) and never on the per-second ticks that touch nothing the box shows.
function awaitKey(st: Status | undefined): string {
  if (!st) return "";
  return JSON.stringify([st.state, st.awaitingWhy || "", st.awaitingKind || "", st.awaitingCount ?? null,
                         st.awaitingTasks || [], st.awaitingTaskIds || [],
                         (st.awaitingPeers || []).map((p) => [p.host || "", p.name || ""])]);
}

function statusOnly(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) { requestFullSession(msg.id); return; }   // a delta with no base is PROOF of desync (see chatTail)
  const before = awaitKey(s.status);
  s.status = msg.status || s.status;
  renderTabs();                          // status-only push → repaint the chip; order is untouched
  if (msg.id === activeId) {
    updateStatusline();
    // T225 (the user 2026-09-02): the awaiting BOX must be there the moment the chip says so. Both read
    // this one payload, but the box was rendered only by the full-session frame path, so a status-only
    // flip to Awaiting left the chip on and the box absent until the next transcript change or tab
    // switch (31s+ observed). Same frame, same data — re-render the box when its fields changed.
    if (awaitKey(s.status) !== before) renderBgTasks();
  }
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
    else { failedProvisionals.delete(id); dismissSession(id, "close"); }
    return;
  }
  dismissSession(id, "close");
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
// WHY a session is leaving the panel — the one fact that decides what happens to its composer state
// (T236, the user 2026-09-03: an unsent draft typed into one session's box turned up in another's after
// a remote host dropped off). "close" is the user's own ✕ / End; "end" is the owning kernel's `closed`
// frame — the session actually ended. Both are GENUINE ends and clear the draft (the user 2026-08-04).
// "hostDrop" is federation's synthetic `closed` for every session of a host that left the mesh — the
// session still exists on its kernel; "omitted" is a kernel tabOrder push that stopped carrying an id it
// used to (applyTabOrder's teardown) — an absence, not a report of an end. Neither is the user's close,
// so the draft (and citations, edit pill, attachments) is STASHED under the session's stable id and comes
// back with the session. Deleting it here, as every teardown once did, is what lost the user's text.
type DismissWhy = "close" | "end" | "hostDrop" | "omitted";

// The active box's live text, filed under ITS OWN id. The input handler keeps `drafts` current on every
// keystroke, but a box filled programmatically (a slash pick, a history recall) can be ahead of it — so
// a teardown stashes explicitly before anything is cleared or the box changes hands.
function stashActiveDraft(id: string): void {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta || activeId !== id) return;
  if (ta.value) drafts.set(id, ta.value); else drafts.delete(id);
}

// Bind the composer to `id`: its draft into the box (unless `keepTyped` and the box already holds live
// typing — the never-clobber rule restoreActiveDraftOnce follows), then its citation chips, attachment
// thumbnails and staged stack. setActive paints the same set inline (pinned there); this is the loader
// for the two OTHER ways the box changes hands — the active tab's teardown and the `!activeId` adoption
// of an arriving session — which used to load less (on adoption, no draft at all: a session that LEFT
// and came back as the only tab showed an empty box over a kept draft).
function loadComposerFor(id: string | null, keepTyped = false): void {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (ta) {
    if (!(keepTyped && ta.value)) ta.value = (id && drafts.get(id)) || "";
    growComposer(ta);
  }
  renderComposerChips(id);
  renderComposerFiles(id);
  renderStagedStrip(id);
}

// The line above the box after the ACTIVE tab was torn down under the user (T236): whose box went away,
// why, and — for a host drop or an omission — that the unsent draft is kept. The tab strip alone did not
// carry this (the report proves it: the user kept typing). One note at a time; it retires on the exact
// events that make it stale — an explicit tab switch (setActive re-binds the box), the session's own
// return (its next session frame), or its ✕ — never a timer.
let composerNoteSid: string | null = null;
function renderComposerNote(sid: string, why: DismissWhy, name: string): void {
  const box = document.getElementById("composer");
  if (!box) return;
  clearComposerNote();
  const note = el("div", "composer-note");
  note.id = "composer-note";
  const msg = el("span", "composer-note-msg");
  const who = el("span", "composer-note-name");
  who.replaceChildren(...hostNameNodes(name, sid));   // the same "host:" + name the tab wore
  let tail: string;
  if (why === "hostDrop") tail = "’s host disconnected — your unsent draft is kept and comes back with it.";
  else if (why === "omitted") tail = " is no longer listed by romp — your unsent draft is kept and comes back with it.";
  else {
    const next = activeId ? (sessions.get(activeId)?.name || tabMeta.get(activeId)?.name || "") : "";
    tail = next ? ` ended — the box below is “${next}”’s now.` : " ended.";
  }
  msg.append(who, document.createTextNode(tail));
  if (activeId) {   // a survivor owns the box now — say what re-binds typing to it (the keys alone will not, see composerNoteHolds)
    const hint = el("span", "composer-note-hint");
    hint.textContent = " Click the box to type here.";
    msg.appendChild(hint);
  }
  const x = el("button", "composer-chip-x");
  x.setAttribute("aria-label", "Dismiss");
  x.title = "dismiss";
  x.textContent = "✕";
  x.addEventListener("click", () => clearComposerNote());
  note.append(msg, x);
  box.insertBefore(note, box.firstChild);
  composerNoteSid = sid;
}
function clearComposerNote(): void {
  document.getElementById("composer-note")?.remove();
  composerNoteSid = null;
}
// While the note is up, the box has NO keyboard owner: the two "type from anywhere" defaults below (a
// printable keystroke nobody claimed, Enter from the bare area) stand down instead of dropping the cursor
// into the survivor's box — the harness showed the blur alone was not enough: the first printable key
// re-focused the box and the continuation of A's draft landed in B's anyway (T236). The keystroke is
// swallowed and the note flashes: the eye goes to the line that explains it. The box gaining focus (a click,
// Tab, any other route the user takes into it), a tab switch or the note's ✕ ends the hold — every one a
// deliberate act.
function composerNoteHolds(): boolean {
  if (!composerNoteSid) return false;
  flashComposerNote();
  return true;
}
function flashComposerNote(): void {
  const n = document.getElementById("composer-note");
  if (!n) return;
  n.classList.remove("composer-note-flash");
  void n.offsetWidth;   // restart the one-shot animation
  n.classList.add("composer-note-flash");
  n.addEventListener("animationend", () => n.classList.remove("composer-note-flash"), { once: true });   // one-shot: the class leaves on the animation's own end
}

// `doomed`: the other ids the same teardown is about to run through (applyTabOrder hands over every id its
// push omitted); a host drop needs no list — every session on that host is going. The fallback must skip
// them: landing on a sibling that is dismissed a moment later re-binds the box twice and leaves the note
// naming the sibling, not the box the user was typing in.
function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>): void {
  if (peekId === id) peekId = null;   // its tab is going — the peek goes with it
  const wasActive = activeId === id;
  const name = sessions.get(id)?.name || tabMeta.get(id)?.name || id;   // read before the maps forget it
  if (wasActive) stashActiveDraft(id);   // FIRST: what is on screen belongs to this id, whatever happens next
  sessions.delete(id);
  liveAsks.delete(id);
  ledgers.delete(id);
  if (why === "close" || why === "end") {
    // ALL of the closed session's composer context goes with it — the draft, the reply-context citation
    // chip, and any pending edit pill (the user 2026-08-04). Deleting from the maps is not enough when the
    // closed session was ACTIVE: the shared chip strip above the composer still shows its chip until
    // someone repaints it, and that stale chip's ✕ targets the dead id (whose map entry is gone), so the
    // click early-returns and the chip can't even be dismissed — hence the repaint below.
    drafts.delete(id); composerCitations.delete(id); composerEdits.delete(id); composerFiles.delete(id); persistDrafts();
  } else {
    persistDrafts();   // a host drop / omission KEEPS it all (see DismissWhy) — the stash above may have updated the copy
  }
  const v = views.get(id);
  if (v) { v.el.remove(); views.delete(id); }
  const oi = order.indexOf(id); if (oi >= 0) order.splice(oi, 1);
  const mi = mru.indexOf(id); if (mi >= 0) mru.splice(mi, 1);   // before the fallback read below — never the dead id
  renderTabs();                          // tab removed from `order` above → repaint without it
  if (wasActive) {
    // MRU: return to the previously-active tab, not the positional neighbor — one still on the strip (the
    // dead id left `mru` above; an id `order` no longer carries would bind the box to a tab nobody can see,
    // and the next keystroke would file under it) and not one this same teardown takes next.
    const home = hostOf(id);   // "" for a local id, and then no sibling rule: every local tab would "share" it
    const goingToo = (x: string) => (doomed?.has(x) ?? false) || (why === "hostDrop" && !!home && hostOf(x) === home);
    // …and with no recency left (the user only ever looked at this one tab), the first tab still on the
    // strip: leaving activeId null with tabs showing read "No session open" under a visible strip, and
    // handed the box to whichever session frame happened to arrive next (the harness caught both, T236).
    activeId = mru.find((x) => order.includes(x) && !goingToo(x)) || order.find((x) => !goingToo(x)) || null;
    loadComposerFor(activeId);   // the strip was showing the CLOSED session's chip/thumbnails/draft — swap in the new active tab's (usually none)
    // The box just changed hands under the user. Their own ✕ is the one case they already know; for every
    // other reason BLUR it — a keystroke a moment later must not land in the survivor's session unnoticed
    // (the T236 report: the continuation of A's draft surfaced in B's box) — and say above it whose box
    // went away. An explicit tab click is what binds the box to a session again.
    if (why !== "close") {
      const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
      if (ta) ta.blur();
      renderComposerNote(id, why, name);
    }
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
  // the shell's palette / shell-focus chords: the chat owns the nav trail, the shell just asks
  if (m.romp === "chatNav") { navHist.go(m.dir === 1 ? 1 : -1); return; }
  if (m.type === "pipeState") { pipeBanner(!!m.up, Number(m.queued) || 0); return; }
  // any kernel message proves the kernel is reachable again — heal previews whose fetch died in a
  // restart window (preview.ts retryFailedPreviews; a no-op when nothing failed). federation's
  // tunnel poll rides this same path: it dispatches {type:"hostUp"} on a host's down→up transition,
  // so relay-failed figures heal the moment the tunnel returns even when no chat traffic flows
  // (idle sessions generated no messages, so figures sat until the user's next send — 2026-08-17)
  retryFailedPreviews();
  // a RECONNECT-class event goes further: settled chips (budget spent) re-attempt regardless of
  // their error text, and the path-image chips parked in imgFailed get one fresh host round-trip
  // (bounded: reconnects are rare events, never a per-push loop)
  // hostUp deliberately does NOT re-ship pending uploads (review finding 2026-09-01): federation
  // dispatches it in the same synchronous tick it re-dials a recovered host's relay, so the socket is
  // still CONNECTING — a re-ship here hit outbound's readyState gate and raised a false "unreachable —
  // dropFile was not delivered" toast (and tore down an in-flight provisional create) an RTT before
  // the relay's own onopen re-shipped correctly. romp:hostRelayUp IS that onopen — the one exact event.
  if (m.type === "hostUp") { refreshSettledPreviews(); healPathImgs(); }
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
  else if (m.type === "subagent") applySubagentFrame(m);
  else if (m.type === "update") update(m);
  else if (m.type === "status") statusOnly(m);
  else if (m.type === "focus") {
    revealSelfPane();   // every focus is someone jumping HERE — on mobile, come forward (incl. from a remote kernel)
    closingTabs.delete(m.id);   // an explicit reveal outranks a pending close-suppression: closing a tab and
    //                             reopening it from the picker inside the ack window must show it at once
    if (revivePending && m.id === revivePending) clearReviveLoader();   // the revive landed — the loader's success event
    assertPeekFor(m.id);   // an out-of-view focus peeks even on the already-active fast path below (setActive is skipped there)
    // `live` (the user 2026-07-08): land on the LIVE TAIL. A blocked card's picker/permission prompt IS the
    // live bottom of the chat, so its feed chip drops the user right on it. Stick the target view to bottom so
    // showActive scrolls there; and cover the ALREADY-ACTIVE case, where setActive early-returns (activeId ===
    // id, no anchor) and would otherwise leave a scrolled-up chat parked in history, not at the prompt.
    if (m.live) { const v = views.get(m.id); if (v) v.stick = true; }
    if (m.live && activeId === m.id) {
      // one frame LATER, not now: when this focus is what un-hid the pane (the shell's reveal lands a
      // task after revealSelfPane's postMessage), the pane is still display:none here and scrollHeight
      // is 0 — the jump read as a no-op (the user 2026-08-13). Next frame the layout is real; when the
      // pane was already visible the deferral is invisible. Anchored jumps need nothing: landOn's
      // ResizeObserver realign already re-lands them when the pane sizes in.
      window.requestAnimationFrame(() => {
        const c = document.getElementById("content"); if (c) c.scrollTop = c.scrollHeight;
      });
    } else {
      pendingAnchorQuote = typeof (m as { anchorQuote?: string }).anchorQuote === "string" ? (m as { anchorQuote?: string }).anchorQuote! : null;   // the supporting span (T218) — consumed by the landing
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
  else if (m.type === "dirCompletions") {                            // the owning kernel's path completions
    // a NEGATIVE reqId is the move dialog's ask (showMovePrompt) — routed before the picker's handler,
    // whose in-flight bookkeeping must not be flipped by an answer it never asked for
    if (typeof m.reqId === "number" && m.reqId < 0) onMoveDirCompletions(m); else onDirCompletions(m);
  }
  else if (m.type === "createDirMissing" && m.name) onCreateDirMissing(m);   // create it, or edit the path
  // The AUTHORITATIVE answer to a ✕ on a queued bubble (the user 2026-07-20). ok:false = the message
  // had already left romp's queue (handed to the CLI — no recall exists): toast the kernel's 'too late'
  // and UNDO the optimistic composer restore if the draft is untouched — leaving the copy there invited
  // re-sending a message that is already being answered. ok:true just drops the stash (restore stands).
  // T214: an answer that found no waiting question (the ask died with a kernel restart) — the
  // kernel flipped nothing and says so; the toast carries the whole story, including that the
  // session was already asked to raise its question again.
  else if (m.type === "askLost" && typeof m.text === "string") warnToast(m.text);
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
  // The kernel's pick memory moved (a pin, a Latest un-pin, a refused pin dropped) or its catalog grew:
  // re-read /models so the family rows send the fresh default — the models-list twin of the palette frame.
  else if (m.type === "models") loadModelChoices();
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
    // the kernel's loud revive failure → named, in that session's own pane + the dismissible toast
    reviveFailedLocal(String(m.id), String(m.name || m.id), String(m.text || "unknown error"));
  }
  else if (m.type === "moved" && m.id) moveLanded(String(m.id), String(m.name || m.id), String(m.cwd || ""));
  else if (m.type === "moveFailed" && m.id) {
    // the kernel's typed move refusal → the reason where the path was typed, or a toast if the dialog is gone
    moveFailedLocal(String(m.id), String(m.name || m.id), String(m.text || "unknown error"));
  }
  else if (m.type === "focusComposer") { const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null; ta?.focus(); }
  else if (m.type === "glowTurns") applyGlow(Array.isArray(m.groups) ? m.groups : [], Array.isArray(m.mids) ? m.mids : []);
  else if (m.type === "askLive") setLiveAsk(m.id, m.ask ?? null);
  else if (m.type === "askLiveClear") clearLiveAsk(m.id);
  else if (m.type === "clipboardText") insertClipboardText(String(m.text ?? ""));
  else if (m.type === "ledger") setLedger(m.id, m.ledger ?? null);
  else if (m.type === "working") { workingSet = new Set(Array.isArray(m.names) ? m.names : []); refreshPostalDots(); }
  else if (m.type === "imgData" && typeof m.path === "string") onImgData(m.path, typeof m.url === "string" ? m.url : null, typeof m.sid === "string" ? m.sid : null);
  else if (m.type === "tabOrder") {
    captureViews(m.views || null);
    applyTabOrder(m.order, m.tabs, { reemit: m.reemit === true, freshHost: typeof m.freshHost === "string" ? m.freshHost : undefined });
  }
  else if (m.type === "renamed" && m.id && typeof m.name === "string") {
    notePendingMeta(pendingTabMeta, m.id, { name: m.name });   // kernel truth — hold it against a push built pre-rename
    const s = sessions.get(m.id);
    if (s && s.name !== m.name) { s.name = m.name; renderTabs(); }
  }
  else if (m.type === "droppedPath" && typeof m.path === "string") {   // host-saved drop/paste/pick → a thumbnail, not path text (the user 2026-08-04)
    const ackShip = typeof m.shipId === "string" && m.shipId ? m.shipId : undefined;
    if (ackShip && !shipOwner(ackShip)) return;   // a duplicate of a ship already retired (a reconnect
    //                                               re-ship raced the original ack) — attaching it again
    //                                               would double the file on whatever tab is active (T215)
    const cbox = document.getElementById("cmt-pop")?.querySelector(".cmt-input") as HTMLTextAreaElement | null;
    if (cbox) {
      // a comment popover is open — its own clip shipped this file, so the path lands in ITS box
      retirePendingShip(m.path, ackShip);
      cbox.value = (cbox.value ? cbox.value.trimEnd() + " " : "") + m.path + " ";
      cbox.dispatchEvent(new Event("input"));   // the draft listener persists it
      if (previewKind(m.path) === "img") cmtShippedImgs.push(m.path);   // the echo's thumbnail ride
      cbox.focus();
      return;
    }
    const owner = retirePendingShip(m.path, ackShip) || activeId;      // the chip this ack answers names the OWNING composer (no-op for pickFile, which never ships)
    addComposerFile(owner, m.path);
    // an OPEN ship-gate dialog counts as a held send (the user 2026-08-19): the upload finishing is
    // the answer to the question it asks, so it closes itself and the send fires — no click needed
    const gateOpen = shipGateSid === owner;
    if (owner && (sendOnShip.has(owner) || gateOpen) && !(pendingShips.get(owner) || []).length) {
      // the LAST ship landed — the event the held send was waiting for (the user 2026-08-16)
      sendOnShip.delete(owner);
      if (gateOpen) { shipGateSid = null; closeConfirm(null); }
      if (owner === activeId) fireHeldSend();
      else warnToast("attachments finished uploading on another tab — the held message was not sent; review it there.");
    }
  } else if (m.type === "dropSaveFailed" && typeof m.name === "string") {
    // the kernel could not SAVE the shipped bytes — clear the pending chip and say so loudly,
    // never leave dots pulsing over a file that is not coming (fail loudly, don't degrade silently)
    const nackShip = typeof m.shipId === "string" && m.shipId ? m.shipId : undefined;
    if (nackShip && !shipOwner(nackShip)) return;   // duplicate nack for a chip already settled — the
    //                                                 first one warned; a re-warn would double the toast
    const owner = retirePendingShip(m.name, nackShip) || activeId;
    const held = !!owner && sendOnShip.delete(owner);    // a held send must not fire without the file it waited for
    const gateWasOpen = shipGateSid === owner;
    if (gateWasOpen) { shipGateSid = null; closeConfirm(null); }   // the question is moot — but a failed save never auto-sends
    warnToast(m.name + " couldn't be saved on the kernel, so it was not attached — try again."
              + (held || gateWasOpen ? " Your message was NOT sent." : ""));
    if (owner && owner === activeId) renderComposerFiles(owner);   // the held-send button state clears with the hold
  }
  // an EDITOR highlight (VS Code host, onDidChangeTextEditorSelection — the user 2026-07-13) seeds the
  // same quote chip a transcript highlight does, labeled + wrapped with its file:lines origin (m.src).
  // The FILE VIEWER posts this same shape WITH a sid — the session it was opened for — and that wins
  // over activeId-at-gesture: the modal stays up across a tab switch, and the chip belongs to the
  // session whose file it quotes (the 2026-08-19 routing rule). Host posts carry no sid.
  else if (m.type === "editorSelection" && typeof m.text === "string" && m.text.trim()) {
    const to = typeof m.sid === "string" && m.sid ? m.sid : activeId;
    if (to) seedEditorQuote(to, m.text, typeof m.src === "string" ? m.src : undefined);
  }
  // the editor selection collapsed (deselect / click away) — drop the chip that highlight seeded
  else if (m.type === "editorSelectionCleared") clearEditorCitation(activeId);
  // comment threads (the user 2026-08-13): the per-session thread frame — store, prune dead
  // client-side state, re-anchor the highlights, adopt a parked create ack, refresh the popover
  else if (m.type === "comments" && m.id) {
    const sid = String(m.id);
    const threads = (m.threads || []) as CommentThread[];
    // an OPTIMISTIC synth thread (a create still in flight) survives the frame rebuild until its
    // real thread supersedes it (same anchor) or its create fails — the frame used to wipe it, so
    // every create's mark BLINKED between the gesture and the thread's first frame, and a
    // parse-lag refusal erased the comment entirely (the T106 lab's first catch, 2026-08-26)
    const synths = (commentThreads.get(sid) || []).filter((t) =>
      t.tid.startsWith("pending:") && cmtCreateInFlight.has(t.tid.slice("pending:".length))
      && !threads.some((r) => r.anchorUuid === t.anchorUuid));
    commentThreads.set(sid, synths.length ? [...threads, ...synths] : threads);
    // THE REPLY-COMPLETED EVENT (T102, sharpened by T112): a frame whose msgs hold MORE agent
    // records than the send's base AND whose thread reads settled — the turn that produced the
    // reply has ENDED, so what the reader sees is the answer, not a mid-turn interim. Counting
    // records alone cleared 40s early on the specimen: the model wrote "checking…" first, ran
    // tools, then answered — the interim record raised the count while the turn was still working
    // and the pulse went yellow before the answer existed. threadBusy here can only DELAY the
    // clear (the latch side never re-derives from state), so the boot-flap class T102 removed
    // cannot re-green anything. Leaving "open" (or erroring) still clears immediately.
    for (const t of threads) {
      // T237: the latch covers ONLY the pre-round-trip instant — released once a frame's projection carries
      // the user's own send (see cmtLatchReleased); the kernel's owed bit owns the wash from there
      const base = cmtAwaitBase.get(t.tid);
      if (base !== undefined && cmtLatchReleased(t, base)) cmtAwaitBase.delete(t.tid);
    }
    // prune latches only for tids NO session's thread list knows (T237): this frame lists ONE session's
    // threads, and pruning against it dropped every other session's latch — and any tid a frame
    // momentarily lacked — leaving a fresh comment's mark plain yellow while it was still "opening"
    const knownTids = new Set<string>();
    commentThreads.forEach((list) => list.forEach((t) => knownTids.add(t.tid)));
    for (const k of Array.from(cmtAwaitBase.keys()))
      if (!k.startsWith("pending:") && !knownTids.has(k)) cmtAwaitBase.delete(k);
    const live = new Set(threads.filter((t) => t.status !== "promoted").map((t) => t.tid));
    for (const k of Array.from(commentPending.keys())) if (!live.has(k)) commentPending.delete(k);
    for (const k of Array.from(commentDrafts.keys())) if (!k.startsWith("new:") && !live.has(k)) commentDrafts.delete(k);
    if (pendingAdoptTid && threads.some((t) => t.tid === pendingAdoptTid)) adoptCommentThread(sid, pendingAdoptTid);
    if (openCommentKey && openCommentKey.sid === sid) {
      // reading IS seeing: a reply that lands while its popover is open must not dot the mark — the
      // watermark advances BEFORE the marks paint (T237), so it never wears yellow for it, not even one tick
      const th = threads.find((t) => t.tid === openCommentKey!.tid);
      if (th && th.unread) {
        th.unread = false;
        vscodeApi?.postMessage({ type: "commentSeen", id: sid, tid: th.tid });
      }
    }
    applyCommentMarks(sid);
    if (openCommentKey && openCommentKey.sid === sid) renderCommentPopover();
  }
  // the create ack names the new thread: adopt exactly it (never a guess). The kernel sends the
  // frame first; if this ack somehow beat it, park the tid and the next frame adopts. The draft is
  // spent UNCONDITIONALLY off the echoed anchor uuid — a popover closed before the ack otherwise
  // leaves its sent words to resurface in the next composer on the same passage.
  else if (m.type === "commentCreateFailed" && m.id && m.uuid) {
    // the typed nack (T106): a TRANSIENT refusal (the kernel's parse hasn't caught the anchor
    // record up yet) keeps the optimistic mark + latch and arms the frame-keyed retry — the next
    // session frame for the sid IS the parse catching up (retryCmtCreates). A real refusal drops
    // the synth honestly; the kernel's warn already said why.
    const held = cmtCreateInFlight.get(String(m.uuid));
    if (held) {
      if (m.transient) { if (held.tries === 0) held.tries = 1; }
      else { cmtCreateInFlight.delete(String(m.uuid)); dropSynthThread(held.sid, held.uuid); }
    }
  }
  else if (m.type === "commentCreated" && m.id && m.tid) {
    if (m.uuid) cmtCreateInFlight.delete(String(m.uuid));   // the ack retires the retry hold
    if (m.uuid) commentDrafts.delete("new:" + String(m.uuid));
    if (pendingCommentAnchor && pendingCommentAnchor.sid === m.id) {
      const tid = String(m.tid);
      if ((commentThreads.get(String(m.id)) || []).some((t) => t.tid === tid)) adoptCommentThread(String(m.id), tid);
      else pendingAdoptTid = tid;
    }
  }
  // a reply the kernel refused: drop its optimistic bubble (it must not read as 'still thinking'
  // forever) and hand the words back for review-and-resend — the toast says why it failed
  else if (m.type === "commentSendFailed" && m.tid) {
    cmtAwaitBase.delete(String(m.tid));           // a refused send owes nothing — the latch it armed goes with it (T237 review)
    const pl = commentPending.get(String(m.tid));
    if (pl && pl.length) {
      const lost = pl.pop()!;
      commentDrafts.set(String(m.tid), lost.text);
      if (openCommentKey && openCommentKey.tid === m.tid) {
        const box = document.getElementById("cmt-pop")?.querySelector(".cmt-input") as HTMLTextAreaElement | null;
        if (box && !box.value.trim()) box.value = lost.text;
        renderCommentPopover();
      }
    }
  }
  else if (m.type === "closed") dismissSession(m.id, m.hostDrop === true ? "hostDrop" : "end");   // a session died on its own (or the kernel confirms our close) — or its HOST dropped (federation's stand-in, stamped: not an end)
  // any payload that rebuilt transcript DOM must get its highlights re-applied (marks live IN that DOM)
  if (m && m.id && (m.type === "session" || m.type === "chatTail" || m.type === "chatHead" || m.type === "chatEpisode"))
    applyCommentMarks(String(m.id));
  // a refused create (warn) must hand the popover back — the draft is intact, the button un-sticks.
  // FULL rebuild: the in-place refresh path deliberately never touches the composer, so it would
  // leave the disabled "Starting…" button stuck (the user 2026-08-15, screenshot of exactly that)
  if (m && m.type === "warn" && pendingCommentAnchor) {
    // the synthetic working mark must die with the refusal, or it marches forever
    const pa = pendingCommentAnchor;
    commentThreads.set(pa.sid, (commentThreads.get(pa.sid) || []).filter((t) => t.tid !== "pending:" + pa.uuid));
    applyCommentMarks(pa.sid);
    document.getElementById("cmt-pop")?.remove();
    renderCommentPopover();
  }
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
  const ct = document.getElementById("cmt-work-timer");
  if (ct) {
    const cur = openCommentThread();
    if (cur && threadBusy(cur.th.state)) ct.textContent = elapsedMs(cur.th.sinceEpoch || null);
  }
  const meta = document.getElementById("spinner-meta");
  if (meta) syncMetaControls(meta, s.status);
  const bar = document.getElementById("ctx-bar");
  if (bar) setCtxBar(bar, s.status.ctx, s.status.state === "compacting", pickTone(s.status.ctxColor, s.status.ctxTone), s.status.ctxOver);
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
  // ⌘/Ctrl+⏎ — STAGE the box instead of sending (the user 2026-08-15): the text and its citation
  // chips move to the staged strip, the box clears, focus stays for the next highlight-and-comment.
  // The states that already own the box refuse loudly rather than staging a lie: a picker answer
  // answers NOW or sends normally; an edit replaces a past message; attachments ride a normal send.
  const stageComposer = () => {
    if (!activeId) return;
    const typed = ta.value.trim();
    // context stages ALONE (the user 2026-08-23): select a passage, ⌘⏎ with an empty box, repeat —
    // then one typed message flushes the whole run. Nothing at all → nothing to stage.
    if (!typed && !(composerCitations.get(activeId) || []).some((c) => c.quote)) return;
    if (composerAnswersAsk()) { warnToast("A picker is waiting on this box — answer it, or send normally."); return; }
    if (composerEdits.has(activeId)) { warnToast("An edit replaces a past message — send it normally."); return; }
    if ((composerFiles.get(activeId) || []).length) { warnToast("Attachments can't be staged — send them with a normal message."); return; }
    stagedMsgs.push(activeId, { text: typed, cites: (composerCitations.get(activeId) || []).slice() });
    composerCitations.delete(activeId); renderComposerChips(activeId);   // the chips now live on the staged item
    drafts.delete(activeId); draftStartedAt.delete(activeId);
    ta.value = ""; composerManualH = null; ta.style.height = "";
    persistDrafts();
    renderStagedStrip(activeId);
  };
  const sendComposer = (opts?: { pastShipGate?: boolean }) => {
    const typed = ta.value.trim();
    if (!activeId) return;
    // an empty plain send with a staged stack = "go": release what's held, nothing new to add
    if (!typed && !(composerFiles.get(activeId) || []).length && stagedMsgs.count(activeId)) {
      if (hostIsDown(activeId) || isProvisionalId(activeId)) {
        warnToast("Can't send yet — the session isn't reachable. They stay staged.");
        return;
      }
      flushStaged(activeId);
      return;
    }
    const attached = composerFiles.get(activeId) || [];
    // SHIP GATE (the user 2026-08-16): an upload still in flight is NOT in `attached` (the send reads
    // only acked paths), so sending now silently drops it — the exact report. Intercept with the same
    // pane-local confirm the /clear guard uses: send WITHOUT it explicitly, or hold the send and let
    // the last droppedPath ack fire it (event-based; a save nack cancels the hold loudly instead).
    const shipping = (pendingShips.get(activeId) || []).length;
    if (shipping && !opts?.pastShipGate) {
      const sid = activeId;
      const what = shipping === 1 ? "An attachment is" : shipping + " attachments are";
      const them = shipping === 1 ? "it" : "them";
      shipGateSid = sid;                       // the last-ship ack resolves the open dialog itself
      showConfirm(what + " still uploading",
                  "Send now and your message goes without " + them + ". Or just wait — it sends "
                  + "itself the moment the upload finishes.",
                  [{ label: "Wait for the upload", value: "wait" },
                   { label: "Send without " + them, value: "now", danger: true }],
                  (v) => {
                    shipGateSid = null;
                    if (v === "now") sendComposer({ pastShipGate: true });
                    else if (v === "wait") { sendOnShip.add(sid); renderComposerFiles(sid); }
                  });
      return;
    }
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
        // the refusal itself is DEMAND: ask the kernel to re-dial that host's tunnel right now,
        // so "romp is re-dialing" below is literally true at the moment it is read (2026-08-16)
        vscodeApi?.postMessage({ type: "redial", host });
        warnToast(host + " is disconnected, so this wasn't sent. It's still in the box — romp is "
          + "re-dialing the link now; send again when it's back.");
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
        registerOptimistic(sid, text, attached.filter((p) => previewKind(p) === "img"));
        sendOnShip.delete(sid);                       // a send happened — any held one is superseded
        histWalk.delete(sid);                         // …and the history walk starts fresh
        if (attached.length) { composerFiles.delete(sid); if (sid === activeId) renderComposerFiles(sid); }
        drafts.delete(sid); draftStartedAt.delete(sid); persistDrafts();
        ta.value = ""; composerManualH = null; ta.style.height = "";
        return;
      }
      lastSent.set(activeId, text);   // remembered for a possible Ctrl+C restore
      // STAGED first (the user 2026-08-15): the held run releases in stage order, each message with the
      // context it was written against, and the message being typed right now lands LAST — the reading
      // the user composed: quote → comment, quote → comment, then the wrap-up. The guards above (host
      // down, provisional) have already passed, so nothing staged can be half-lost here.
      flushStaged(sid);
      // A pending citation chip → send as a FOLLOW-UP on that goal (the user 2026-07-01): askFollowUp wraps the
      // text with the goal's context + the romp-goal-id marker (kernel side), so the goal reopens (done→working,
      // unless cleared) and the chat renders the ↩ Follow-up header — the same path the Follow-up button uses,
      // just seeded by the click. A QUOTE chip (highlighted transcript text, the user 2026-07-13) has no goal:
      // it wraps client-side (quoteReplyBody) into a plain message. No chip → plain sendMessage. The three
      // branches live in routeUserMessage — ONE routing owner, shared with the staged flush above.
      // Inside it, `sid` is what ROUTES this to the owning kernel in a federated dashboard (the user
      // 2026-07-29): federation keys routing off `id`/`sid` only, and an `itemId` ("‹sid›:‹goal›") can't
      // be one — its own colon would read the session uuid as a host. Without the sid a follow-up on a
      // REMOTE card went to the LOCAL kernel, which owns no such session and dropped it into tmux by
      // uuid — nothing sent, no error, the card flashing to Working and back. The kernel keeps deriving
      // its sid from itemId, so this is inert locally; every other card op carries the sid the same way.
      const cites = composerCitations.get(activeId);
      routeUserMessage(activeId, text, cites, attached.filter((p) => previewKind(p) === "img"));
      // (a citation follow-up/quote has its own kernel-side echo path; the optimistic bubble covers the plain send)
      if (cites) { composerCitations.delete(activeId); renderComposerChips(activeId); }   // consumed on send
      sendOnShip.delete(sid);                       // a send happened — any held one is superseded
      histWalk.delete(sid);                         // …and the history walk starts fresh
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
  if (sendBtn) setTip(sendBtn, "Send (Enter)");   // styled tip replaces the skeleton's native title (aria-label stays)
  sendBtn?.addEventListener("mousedown", (e) => { e.preventDefault(); sendComposer(); if (isCoarsePointer()) ta.blur(); else ta.focus(); });
  fireHeldSend = () => sendComposer();   // the ack handler's door into this closure (see sendOnShip)
  fireStage = () => stageComposer();     // the chips strip's Stage button's door (renderComposerChips)

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

  // ── width-adaptive resting placeholder (the user 2026-08-26) ── the pane resized across the
  // short/long hint threshold → re-fit the placeholder, but ONLY while it is showing a resting
  // form: a picker's "add your own answer…" or the closed-session notice must never be clobbered.
  try {
    new ResizeObserver(() => {
      if (ta.placeholder.startsWith("Message this session…")) ta.placeholder = composerRestingPlaceholder();
    }).observe(ta);
  } catch (e) { /* tests: no ResizeObserver in the DOM shim */ }

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
  // The box GAINING FOCUS is the deliberate re-bind the note asked for (T236): the teardown blurred it, showActive
  // never focuses it, and the two type-from-anywhere defaults stand down while the note holds — so any focus now
  // is the user's own act (a click, Tab, Enter over a selection, Quote, a citation seed, a slash pick, an edit
  // recall). Retiring on pointerdown alone left the note over a box they were typing in by any other route.
  ta.addEventListener("focus", () => { if (composerNoteSid) clearComposerNote(); });
  ta.addEventListener("blur", () => window.setTimeout(closeSlash, 120));   // close when leaving (a row's mousedown keeps focus, so it fires only on a real leave)
  window.addEventListener("resize", positionSlash);

  // ── PROMPT HISTORY (the user 2026-08-16): ↑ with the caret on the box's FIRST line recalls the
  // session's previously SENT prompts, shell-style; ↓ on the last line walks forward again, and
  // walking past the newest restores the draft you were typing (stashed on the first ↑). History is
  // the session payload's own human-sent messages — authoritative, survives reloads — with romp's
  // injected turns excluded and adjacent repeats collapsed. The walk drops on send.
  const histWalk = new Map<string, { idx: number; stash: string }>();   // sid → walk position + stashed draft
  let recalling = false;                               // fences the recall's own synthetic input event
  const composerHistory = (sid: string): string[] => {
    const out: string[] = [];
    for (const ev of sessions.get(sid)?.events || []) {
      if (ev.kind !== "user" || !ev.human || ev.romp || ev.rompAuto) continue;
      const t = (ev.md || "").trim();
      if (t && out[out.length - 1] !== t) out.push(t);
    }
    return out;                                        // oldest → newest
  };
  ta.addEventListener("keydown", (e) => {
    if (slashKey(e)) return;   // the slash menu owns ↑/↓/⏎/Tab/Esc while it's open
    // ↑/↓ recall history ONLY from an EMPTY box (the user 2026-08-17, tightening the first cut's
    // first-line rule: any text already in the box — even one character — means a draft in progress,
    // and arrows must never hijack it). Once a walk is ACTIVE the recalled text is the walk's own, so
    // ↑/↓ keep navigating from its boundary lines; the first manual edit ends the walk (see the input
    // listener) and the text becomes an ordinary draft that recall won't touch.
    if ((e.key === "ArrowUp" || e.key === "ArrowDown") && activeId
        && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey
        && ta.selectionStart === ta.selectionEnd) {
      const onFirst = !ta.value.slice(0, ta.selectionStart).includes("\n");
      const onLast = !ta.value.slice(ta.selectionStart).includes("\n");
      const w = histWalk.get(activeId);
      if (e.key === "ArrowUp" ? (w ? onFirst : ta.value === "") : (onLast && w)) {
        const hist = composerHistory(activeId);
        if (e.key === "ArrowUp") {
          const idx = w ? w.idx - 1 : hist.length - 1;
          if (idx >= 0 && hist.length) {
            e.preventDefault();
            histWalk.set(activeId, { idx, stash: w ? w.stash : ta.value });
            recalling = true;
            ta.value = hist[idx];
            ta.setSelectionRange(ta.value.length, ta.value.length);
            growComposer(ta);
            ta.dispatchEvent(new Event("input"));      // draft/slash/ask-mode bookkeeping stays true
            recalling = false;
          }
          return;                                      // nothing older → native caret-to-start is fine
        }
        e.preventDefault();
        const idx = w!.idx + 1;
        recalling = true;
        if (idx >= hist.length) { ta.value = w!.stash; histWalk.delete(activeId); }
        else { w!.idx = idx; ta.value = hist[idx]; }
        ta.setSelectionRange(ta.value.length, ta.value.length);
        growComposer(ta);
        ta.dispatchEvent(new Event("input"));
        recalling = false;
        return;
      }
    }
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
    // ⌘⏎ / Ctrl+⏎ stages (the user 2026-08-15) — and focus STAYS in the box, because the whole point
    // is highlighting the next spot and typing again. Checked before the plain-Enter send below.
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.shiftKey) {
      e.preventDefault();
      stageComposer();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !isCoarsePointer()) {
      e.preventDefault();
      sendComposer();
      focusActiveTab();   // jump focus to the tab bar after sending (the user 2026-06-25) so ←/→ switch
                          // sessions right away — the composer (a textarea) would otherwise keep the arrows
                          // for its caret. (The explicit send BUTTON keeps composer focus for continued typing.)
    }
  });
  ta.addEventListener("input", () => {
    // a MANUAL edit ends any history walk (the user 2026-08-17): the text becomes an ordinary
    // draft, and recall stays away from drafts. The recall's own synthetic dispatch is fenced.
    if (!recalling && activeId) histWalk.delete(activeId);
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
  // upgrade the skeleton's native title to the styled tip (tip.ts removes the title attribute);
  // the icon-only button keeps an accessible name via aria-label
  if (attach) { setTip(attach, "Attach a file"); attach.setAttribute("aria-label", "Attach a file"); }
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
  const shipId = "s" + Date.now().toString(36) + "." + (++shipSeq);   // page-local; the ack echoes it
  addPendingShip(sid, name, shipId);   // mid-encode (or mid-verify, for a pasted path) must not reroute
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = String(reader.result || "").split(",")[1] || "";
    if (!b64 || !vscodeApi) { retirePendingShip(name, shipId); return; }
    const entry = sid ? (pendingShips.get(sid) || []).find((p) => p.shipId === shipId) : undefined;
    if (entry) entry.b64 = b64;   // retained until the ack — the reconnect re-ship needs the bytes (T215)
    const msg: { type: string; name: string; b64: string; shipId: string; id?: string } =
      { type: "dropFile", name, b64, shipId };
    if (sid) msg.id = sid;   // the owning session → the owning kernel
    vscodeApi.postMessage(msg);
  };
  reader.onerror = () => retirePendingShip(name, shipId);   // an unreadable file must not leave a stuck chip
  reader.readAsDataURL(f);
}

// ---- settings: the gear + modal live on the TIMELINE now (the user 2026-06-14). The chat just
// CONSUMES the shared 'romp:settings' (compact mode) — applying a change made there, in a same-origin
// tab, live via the storage event; and reading it at startup. ----
// The chat TEXT scheme (the user 2026-08-24) is a body class the tier variables key on
// (styles.css body.scheme-*) — toggled here so a gear pick recolors live, event-based.
function applyChatScheme(s: RompSettings): void {
  document.body.classList.toggle("scheme-high-contrast", s.chatScheme === "high-contrast");
  document.body.classList.toggle("scheme-solarized-dark", s.chatScheme === "solarized-dark");
  // the overall theme (T113 promoted 2026-08-28): the shared applier toggles the strip-aesthetic
  // and light-theme classes from s.theme. Applies live — onExternalSettingsChange re-runs this.
  applyTheme(document, s);
}
function setupSettings(): void {
  applyChatScheme(settings);   // the persisted pick applies at startup — it survives reloads
  // renderTabs too: the tab strip reads settings (the context gauge toggle) but rerenderAll only
  // rebuilds the transcript views, so without it a gear change waited for the next kernel push.
  onExternalSettingsChange((s) => { settings = s; applyChatScheme(s); renderTabs(); rerenderAll(); refillOpenCommentPop(); });
}

// The feed's click echo (feed.ts focusEcho — the user 2026-08-24, "clicking into a not-shown
// session is slow"): activate NOW, before any kernel round-trip — the tab/peek + loader are the
// instant acknowledgment, and the kernel's focus frame follows with the anchor, re-deriving the
// peek idempotently. Unknown sids fall through to the kernel (it may route a revive/confirm).
window.addEventListener("storage", (e) => {
  if (e.key !== "romp:focus-echo" || !e.newValue) return;
  try {
    const v = JSON.parse(e.newValue);
    const sid = typeof v.sid === "string" ? v.sid : "";
    if (!sid || (!sessions.has(sid) && !tabMeta.has(sid))) return;
    revealSelfPane();
    closingTabs.delete(sid);
    assertPeekFor(sid);
    setActive(sid);
  } catch { /* malformed echo — the kernel frame corrects momentarily */ }
});
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
  // OS-open demoted to the folder-link's right-click (the user 2026-08-14): the click now browses in
  // the dashboard, and this menu keeps "open a folder window" reachable for whoever IS at that
  // machine's screen. One document-level listener — folder links live in re-rendered surfaces.
  document.addEventListener("contextmenu", (ev) => {
    const link = (ev.target as HTMLElement).closest?.(".folder-link[data-cwd]") as HTMLElement | null;
    if (!link || link.dataset.act !== "browseFiles") return;   // openFolder clicks need no second door
    ev.preventDefault();
    document.getElementById("folder-ctx")?.remove();
    const menu = el("div", "ctx-menu");
    menu.id = "folder-ctx";
    const item = el("div", "ctx-item");
    item.textContent = "Open folder window";
    const sub = el("span", "ctx-item-sub");
    sub.textContent = "on the machine the session runs on";
    item.appendChild(sub);
    const cwd = link.dataset.cwd || "";
    const id = link.dataset.id;
    item.addEventListener("click", (e2) => {
      e2.stopPropagation();
      menu.remove();
      vscodeApi?.postMessage(id ? { type: "openFolder", cwd, id } : { type: "openFolder", cwd });
    });
    menu.appendChild(item);
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.max(0, Math.min(ev.clientX, window.innerWidth - r.width - 4)) + "px";
    menu.style.top = Math.max(0, Math.min(ev.clientY, window.innerHeight - r.height - 4)) + "px";
    const dismiss = () => { menu.remove(); document.removeEventListener("click", dismiss); };
    document.addEventListener("click", dismiss);
  });
  delegate(document.body, {
    // Subagent transcripts (plans/subagent-transcripts.md): the arrow on an Agent head / agent bg row
    // opens the viewer; the viewer header's parent link jumps back to the tool head; its pin keeps the tab.
    openSubagent: (el) => {
      const agentId = el.dataset.agent; if (!agentId) return;
      const owner = el.dataset.sid || activeId; if (!owner) return;
      openSubagentView(owner, agentId, el.dataset.uuid || null);
    },
    subParent: (el) => {
      const sid = el.dataset.sid; if (!sid) return;
      setActive(sid, el.dataset.uuid || undefined);
    },
    pinSubagent: (el) => {
      const id = el.dataset.id; if (!id) return;
      if (pinnedSubs.has(id)) pinnedSubs.delete(id); else pinnedSubs.add(id);
      assertPeekFor(id);   // pinned → in the chat lens → sheds the peek dress; unpinned → back to a peek
      renderSubHead();
    },
    openFolder: (el) => {
      const cwd = el.dataset.cwd; if (!cwd || !vscodeApi) return;
      const id = el.dataset.id;
      vscodeApi.postMessage(id ? { type: "openFolder", cwd, id } : { type: "openFolder", cwd });
    },
    // the web twin of openFolder: surface the file browser at this folder
    browseFiles: (el) => {
      const cwd = el.dataset.cwd; if (!cwd) return;
      openBrowse(cwd, el.dataset.id);
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
      const sidQ = owningSidOf(el) || activeId;
      if (qmd) {
        // EVERY ✕ drops our own optimistic entry for the text first (the user 2026-08-30). At the
        // optimistic stage (qopt) that is the whole client half — the kernel may not have pushed its
        // park yet, and the reconcile would otherwise repaint the bubble the user just cut. And on a
        // PARKED/backend ✕ it is just as load-bearing: the kernel bubble had been SUPPRESSING our
        // still-live entry (shownProvisional), so cancelling only the kernel op resurrected the
        // cancelled message as a dashed bubble until the TTL (caught by this fix's served-page probe).
        const list = pendingSent.get(sidQ) || [];
        const i = list.findIndex((p) => p.text === qmd);
        if (i >= 0) { list.splice(i, 1); if (list.length) pendingSent.set(sidQ, list); else pendingSent.delete(sidQ); }
        echoShownSig.delete(sidQ);
      }
      const msg: Record<string, unknown> = { type: "cancelQueued", id: sidQ, md: qmd };
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
    // a comment highlight or its turn badge (the user 2026-08-13): open the thread's popover at the
    // click. Delegated — marks and badges are re-created on every transcript rebuild — and so is
    // every popover BUTTON below: the popover's conversation refreshes on comments frames, and a
    // per-render listener would eat the mid-press click (the click-safety rule).
    cmtopen: (elx) => {
      const tid = elx.dataset.tid;
      if (!tid || !activeId) return;
      const r = elx.getBoundingClientRect();
      openCommentPopover(activeId, tid, Math.min(r.left, window.innerWidth - 380), r.bottom + 6);
    },
    cmtclose: () => closeCommentPop(),
    // Interrupt the THREAD's own turn (T138): the sid rides the button (the thread's session),
    // never activeId — the exact owner-scoping class queued-x/Retry were fixed for. The gesture
    // itself ENDS the exchange with no reply record coming, so the T102 send-latch clears on THIS
    // event (a user gesture is a sanctioned mover; waiting for a reply-completed record would hang
    // the pulse green forever). Acknowledge instantly per the buttons rule: the chip flips to
    // Interrupting… and the timer + button go; the next comments frame rebuilds this statusline.
    cmtinterrupt: (elx) => {
      const sid = (elx as HTMLElement).dataset.sid;
      if (!sid || !vscodeApi) return;
      vscodeApi.postMessage({ type: "interrupt", id: sid });
      cmtAwaitBase.delete(sid);
      cmtInterrupted.add(sid);
      const wrap = document.getElementById("cmt-state");
      if (wrap) {
        const chip = el("span", "chip chip-interrupting");
        chip.textContent = CHIP_LABEL.interrupting;
        wrap.replaceChildren(chip);
      }
    },
    // a scroll-rail tick: jump the chat to the commented message (fresh navigation → one flash)
    // and open its thread beside it
    cmtjump: (elx) => {
      const tid = elx.dataset.tid, uuid = elx.dataset.uuid;
      if (!tid || !uuid || !activeId) return;
      flashedAnchor = null;
      scrollToAnchor(uuid);
      const r = elx.getBoundingClientRect();
      openCommentPopover(activeId, tid, Math.max(8, r.left - 380), Math.max(60, r.top - 40));
    },
    cmtsend: (elx) => {
      const pop = elx.closest(".cmt-pop") as HTMLElement | null;
      if (pop) commentSendFromPop(pop);
    },
    cmtbreak: () => {
      const cur = openCommentThread();
      if (cur) showBreakoutPrompt(cur.sid, cur.th.tid);
    },
    cmtmerge: (elx) => {
      const cur = openCommentThread();
      if (!cur) return;
      (elx as HTMLButtonElement).disabled = true;   // acknowledge before the round-trip
      elx.textContent = "Relaying…";
      vscodeApi?.postMessage({ type: "commentMerge", id: cur.sid, tid: cur.th.tid });
    },
    cmtresolve: (elx) => {
      const cur = openCommentThread();
      if (!cur) return;
      (elx as HTMLButtonElement).disabled = true;
      elx.textContent = "Resolving…";
      vscodeApi?.postMessage({ type: "commentResolve", id: cur.sid, tid: cur.th.tid });
    },
    cmtdelete: (elx) => {
      const cur = openCommentThread();
      if (!cur) return;
      if (!elx.classList.contains("armed")) { elx.classList.add("armed"); elx.textContent = "Really delete?"; return; }
      vscodeApi?.postMessage({ type: "commentDelete", id: cur.sid, tid: cur.th.tid });
      // optimistic removal (the user 2026-08-17: the highlight must go NOW, mid-animation included);
      // the kernel's next frame confirms, and kernel-side the delete also interrupts the in-flight
      // reply so the work stops with its cue
      commentThreads.set(cur.sid, (commentThreads.get(cur.sid) || []).filter((t) => t.tid !== cur.th.tid));
      applyCommentMarks(cur.sid);
      closeCommentPop();
    },
    cmtopensession: (elx) => {
      const tid = elx.dataset.tid;
      closeCommentPop();
      if (tid) setActive(tid);
    },
    // a branch divider (child side) or branch chip (parent side): jump to the other end of the
    // branch, landing on the branch-point turn via the deep-link anchor machinery
    branchjump: (elx) => {
      const sid = elx.dataset.sid;
      if (!sid) return;
      if (!sessions.get(sid)) { warnToast("That session isn't on this dashboard right now."); return; }
      setActive(sid, elx.dataset.cut || undefined);
    },
    // a below-response fork spot: the row carries its own cut ("" = whole conversation)
    forkspot: (elx) => {
      const cut = (elx.closest(".fork-spot") as HTMLElement | null)?.dataset.cut || "";
      if (activeId && !isProvisionalId(activeId) && sessions.get(activeId)) showForkPrompt(activeId, cut);
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
      if (id && isSubId(id)) { closeSubagentView(id); return; }   // a subagent viewer: nothing to end, just close
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
      // …and the confirm NAMES what is still open on its board (the user 2026-08-15, who ended a
      // session holding an open task with no warning — the /clear gate has warned this way since
      // 2026-07-27, and ending drops the cards from the working surfaces the same way)
      showConfirm(`End “${nm}”?`,
        endConfirmDetail(openTopTitles(ledgers.get(id)?.tree),
          "The session shuts down. Its history stays on disk — revive it any time from the picker or timeline."),
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
  window.addEventListener("pointerup", releaseTabStrip);
  window.addEventListener("pointercancel", releaseTabStrip);
  window.addEventListener("blur", releaseTabStrip);
  // LIVE REORDER (T127, the user 2026-08-27: dragging should push the other tabs into their new
  // locations as you drag, the way browsers do): while a tab drags, its in-flow element moves
  // through the strip's DOM as the pointer crosses the MIDPOINT of the tab under it — the
  // slot-boundary event, never a timer — and the wrap layout reflows rows natively, so a tab
  // pushed past a row's end wraps to the next row mid-drag; siblings FLIP to their new rects
  // (flipTabs). The no-op guard (already sitting immediately before the reference node) is what
  // keeps a pointer resting inside one slot from churning the DOM on every dragover tick.
  tabs.addEventListener("dragover", (e) => {
    if (!draggedId || !dragGeom) return;
    e.preventDefault();   // the whole strip is a valid drop target
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    const dragged = tabs.querySelector<HTMLElement>(`.tab[data-id="${CSS.escape(draggedId)}"]`);
    if (!dragged) return;
    // native feel: a pointer still inside the dragged tab's own box moves nothing (without this,
    // the virtual mapping — which removes the dragged tab — can read the untouched start position
    // as one slot over and hop the tab before the user has left it)
    const dr = dragged.getBoundingClientRect();
    if (e.clientX >= dr.left && e.clientX <= dr.right && e.clientY >= dr.top && e.clientY <= dr.bottom) return;
    // the virtual layout: the OTHER tabs in current DOM order, widths from the dragstart snapshot —
    // boundaries that cannot move in response to the insert they cause (dragslot.ts owns the math)
    const others = Array.from(tabs.querySelectorAll<HTMLElement>(".tab[data-id]")).filter((t) => t !== dragged);
    const boxes = others.map((t) => ({ id: t.dataset.id!, w: dragGeom!.widths.get(t.dataset.id!) ?? t.getBoundingClientRect().width }));
    const br = tabs.getBoundingClientRect();
    const idx = dragSlotIndex(boxes, dragGeom.containerW, dragGeom.gapX, dragGeom.rowH,
                              e.clientX - br.left, e.clientY - br.top);
    const ref = idx < others.length ? others[idx] : dragged.parentElement === tabs ? tabs.querySelector(".tab-add") : null;
    if (ref !== dragged && dragged.nextElementSibling !== ref)
      flipTabs(() => tabs.insertBefore(dragged, ref));
  });
  // Drop commits the LIVE DOM position through the same reorderTo the strip has always used —
  // neighbor id + side — so persistence and the kernel write are byte-identical, and ids that a
  // filtered view HIDES (present in `order`, absent from the DOM) keep their places: a wholesale
  // order-from-DOM write would silently drop them.
  tabs.addEventListener("drop", (e) => {
    if (!draggedId) return;
    e.preventDefault();
    const dragged = tabs.querySelector<HTMLElement>(`.tab[data-id="${CSS.escape(draggedId)}"]`);
    if (!dragged) return;
    const prev = dragged.previousElementSibling as HTMLElement | null;
    const next = dragged.nextElementSibling as HTMLElement | null;
    if (prev?.dataset?.id) reorderTo(draggedId, prev.dataset.id, true);
    else if (next?.dataset?.id) reorderTo(draggedId, next.dataset.id, false);
    tabDragCommitted = true;   // dragend must not treat this as a cancel (it fires next)
  });
})();
// right-click a selection in the transcript → Reply (quote it) / Copy
document.getElementById("content")?.addEventListener("contextmenu", showSelectionMenu);
// The chat document hosts the viewer itself (openPath), so it boots the viewer's listener with the
// same WS poster the feed hands it: Edit/Save round-trips and the GitHub-link ask ride post(), and
// the kernel's replies come back as window MessageEvents via the pane shim — either document, one
// mechanism (file-view.ts initFileView).
initFileView((m) => vscodeApi?.postMessage(m));
if (vscodeApi) vscodeApi.postMessage({ type: "ready" });
