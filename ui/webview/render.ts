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
import { markerLabel, chooseStamps } from "./time-marker";
import { compactDisplay, toolCounts, type DisplayItem } from "./compact";
import { loadSettings, onExternalSettingsChange, type RompSettings } from "./settings";
import { delegate } from "./actions";
import { prebuildPlan, type ViewState } from "./prebuild";
import { reconcileTabOrder } from "./tab-order";

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

// One answered (or pending) question on an AskUserQuestion turn: the prompt + its options, plus the
// user's answer TEXT per question (`chosen`). Answer text may name an option label OR be free-text
// ("Other"), and is empty while the question is still pending. multiSelect → chosen has >1 entry.
type AskAnswerBlock = { question: string; header?: string; options: { label: string; description?: string }[]; chosen: string[] };

type ChatEvent = (
  | { kind: "user"; md: string; uuid?: string; ts?: string; reminders?: string[]; human?: boolean; romp?: boolean; rompAuto?: boolean; followUp?: boolean; goal?: string; images?: { src: string; path?: string }[] }
  | { kind: "assistant"; md: string; uuid?: string; ts?: string }
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
      // AskUserQuestion only: the kernel joins the posed questions/options to the recorded answer and
      // attaches them here (the user 2026-06-16). Empty `chosen` while pending; filled once answered →
      // renderAsk flips the turn to the blue "you answered Claude's question" box.
      askAnswer?: AskAnswerBlock[];
    }
  | {
      kind: "postal-service";
      direction: "in" | "out";
      peer: string;
      color: { bg: string; fg: string } | null;
      body: string;
      summary?: string;  // incoming Haiku caption (≤9 words) — shown instead of the verbose body; body on hover
      mid?: string;      // postal message id (joins feed-modal handoff hovers to this card)
      t?: number;        // epoch seconds (incoming)
      park?: boolean;
      status?: "delivered" | "parked"; // outgoing
      ts?: string;
      uuid?: string;
    }
  // Claude Code's Task to-do list, folded into one live checklist.
  | { kind: "todo"; tasks: TodoTask[]; ts?: string; uuid?: string }
  | { kind: "queued"; texts: { md: string; followUp?: boolean; goal?: string; idx?: number; cancelable?: boolean }[]; ts?: string; uuid?: string }
  // The turn stopped on an API error (event-based: transcript isApiErrorMessage). The session is BLOCKED
  // until retried — a red-dot card at the bottom with a Retry button (the user 2026-06-16).
  | { kind: "apiError"; text: string; status?: number; category?: string; ts?: string; uuid?: string }
  | { kind: "compact"; ts?: string; uuid?: string }
  // Pinned, collapsed "system context" card at the top of the transcript (the user 2026-06-19): the
  // CLAUDE.md instructions in effect + session config. NOT the verbatim harness prompt — it's never
  // recorded, so it can't be shown (renderSystem says so). No ts/uuid → off the rail (no dot/hover).
  | { kind: "system"; model?: string; cwd?: string; gitBranch?: string; version?: string; mode?: string;
      claudemd?: { path: string; scope: string; text: string }[]; uuid?: string; ts?: string }
) & { tlId?: string };   // tlId: the timeline atom this event's hover lights — a prompt → the DOT, work → the BAR

interface TodoTask { id: string; subject: string; activeForm?: string; status: string }

type ChipState = "working" | "ready" | "awaiting" | "idle" | "closed" | "compacting" | "blocked" | "retrying";
interface Status { state: ChipState; sinceEpoch: number | null; effort?: string; model?: string; mode?: string; ctx?: string; ctxColor?: number[]; faded?: boolean; backend?: string; apiTooLong?: boolean; }   // backend = "tmux" | "sdk"; apiTooLong = the "blocked" is a "prompt is too long" error (on you → red tab) vs a transient API error (amber/retrying); ctxColor = the GLOBAL colormap's RGB for the context%, computed server-side
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
interface Session { id: string; name: string; color: Color | null; events: ChatEvent[]; status: Status; firstSeen?: number; cwd?: string; headFrom?: number; headTotal?: number; bgTasks?: BgTasks; hideFromFeed?: boolean; postalServiceOff?: boolean; }

const vscodeApi =
  typeof (window as any).acquireVsCodeApi === "function" ? (window as any).acquireVsCodeApi() : undefined;

let settings: RompSettings = loadSettings();   // global webview settings (compact mode, …) — see settings.ts
const expandedGroups = new Set<string>();      // compact mode: tool-group keys the user clicked open

const sessions = new Map<string, Session>();
const order: string[] = [];           // positional tab order (for cycling)
// Tab name+color from the kernel's tabOrder push (the user 2026-06-26): lets renderTabs paint the WHOLE
// strip as placeholders BEFORE each session's build_session arrives, so tabs don't pop in one-by-one.
const tabMeta = new Map<string, { name: string; color: Color | null }>();
// The romp identity palette for the tab right-click color picker (the user 2026-06-29). Fetched once from the
// kernel's /palette so the client holds no color literals; empty until it lands (the menu just omits the row).
let paletteColors: string[] = [];
fetch("/palette", { cache: "no-store" }).then((r) => r.json())
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
// Invariant: turn children === (len − winStart); view.rendered === len (events
// accounted), so DOM childNodes === (winStart>0 ? 1 : 0) + (len − winStart).
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

// Per-session rolling digest (purpose + a few timestamped bullets), shown in the
// #ledger box just below the tabs. Swaps with the active tab; pushed by the host.
interface LedgerBullet { text: string; t?: number; id?: string; sid?: string; tlId?: string; }   // id/sid = locate anchor; tlId = the timeline atom (turn DOT) to light on hover
// A node of the goal-graph overview tree: open paths are expanded, done nodes are pruned to leaves.
// `current` = the focus node being worked on (gets a pointer + the live elapsed); a `done` node shows
// its completion time, recency-coloured, on the right (the user 2026-06-16).
// `derived` = this node is done only because all its children are (the kernel propagates completion up
// the tree), as opposed to an explicitly-asserted done. Rendered as the blue ✓ disc dimmed (the user
// 2026-06-16). Empty/false → explicit done (full disc).
interface LedgerTreeNode { id: string; text: string; depth: number; done: boolean; blocked: boolean; t?: number; mt?: number; current: boolean; derived?: boolean; recent?: boolean; cleared?: boolean; onpath?: boolean; promptAnchorUuid?: string | null; anchorUuid?: string | null; children?: string[]; summary?: string | null; blockSummary?: string | null; _rec?: number; }   // summary/blockSummary = the distiller's takeaway / decision brief, revealed by the row's ⊕ expander; _rec = render-stamped subtree-rolled-up recency
// tree = the goal overview (preferred view); bullets = captioned-turn fallback for goal-less sessions.
interface Ledger { summary: string; tree?: LedgerTreeNode[]; bullets: LedgerBullet[]; current?: LedgerBullet | null; }
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

/** Red/green diff renderer — each line gets its own div.diff-row with a gutter marker and colored band. */
function diffPre(diffText: string): HTMLElement {
  const box = el("div", "fold-pre diff-fold");
  for (const line of diffText.split("\n")) {
    const m = line[0];
    const kind = m === "+" ? "add" : m === "-" ? "del" : "ctx";
    const row = el("div", "diff-row diff-" + kind);
    const gutter = el("span", "diff-gutter"); gutter.textContent = m === "+" || m === "-" ? m : " ";
    const text = el("span", "diff-text"); text.textContent = m === "+" || m === "-" ? line.slice(1) : line.replace(/^ {2}/, "");
    row.appendChild(gutter); row.appendChild(text);
    box.appendChild(row);
  }
  return box;
}

/** File header for Edit/Write diffs — paths get mono styling, notes get dimmed. */
function fileHead(text: string): HTMLElement {
  const wrap = el("div", "ask-filehead");
  for (const line of text.split("\n")) {
    const isPath = /^\S+$/.test(line) && /[/.]/.test(line);
    const row = el("div", isPath ? "ask-file-path" : "ask-file-note");
    row.textContent = line;
    wrap.appendChild(row);
  }
  return wrap;
}

/** Tool-permission detail block (Bash command, WebFetch url, MCP call). */
function askBody(body: string): HTMLElement {
  const wrap = el("div", "ask-body");
  const lines = body.split("\n");
  let descStart = -1;
  for (let i = lines.length - 1; i >= 1; i--) {
    const t = lines[i].trim();
    if (/[=|&$<>{}`\\]/.test(t)) break;
    if (/^[A-Z]/.test(t) && /\s/.test(t)) descStart = i;
  }
  const target = descStart >= 1 ? lines.slice(0, descStart) : lines;
  const desc = descStart >= 1 ? lines.slice(descStart).join(" ").trim() : undefined;

  let detail = target.join("\n");
  const cmd = el("div", "ask-cmd");
  cmd.textContent = detail;
  wrap.appendChild(cmd);
  if (desc) {
    const d = el("div", "ask-body-desc");
    d.textContent = desc;
    wrap.appendChild(d);
  }
  return wrap;
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
    if (vscodeApi) vscodeApi.postMessage({ type: "imgRequest", path: p });
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
function userImage(im: { src: string; path?: string }): HTMLElement {
  const fig = el("span", "user-img-wrap");
  if (im.src.startsWith("path:")) {
    fig.appendChild(buildPathImg(im.src.slice(5)));   // host reads it → real thumbnail; chip until then / on failure
  } else {
    const img = document.createElement("img"); img.className = "user-img"; img.src = im.src; img.loading = "lazy";
    fig.appendChild(img);
  }
  if (im.path) fig.appendChild(imgCaption(im.path));
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

function renderEvent(ev: ChatEvent, prevEpoch?: number | null, worked?: number | null): HTMLElement {
  const turn = renderEventInner(ev);
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
    timer = setTimeout(() => { timer = undefined; if (activeId) vscodeApi?.postMessage({ type: "dotHover", sid: activeId, uuid, t, tlId }); }, 120);
  });
  hoverTarget.addEventListener("mouseleave", () => {
    if (timer) { clearTimeout(timer); timer = undefined; return; } // never fired — nothing to clear
    vscodeApi?.postMessage({ type: "dotHover" });
  });
  if (dot) {
    dot.classList.add("dot-nav");
    dot.title = "click: open the feed card · hover: highlight on the timeline + feed";
    dot.addEventListener("click", (e) => {
      e.stopPropagation();
      if (activeId) vscodeApi?.postMessage({ type: "dotOpen", sid: activeId, uuid, t });
    });
  }
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
// data-hm; restampMarkers() may later light it up if too much space has gone unstamped.
// data-hard marks the markerLabel-assigned stamps, which the spacing pass never touches.
function timeMarker(epoch: number, prevEpoch: number | null): HTMLElement {
  const { text, day, hm, date } = markerLabel(epoch, prevEpoch, Date.now());
  const m = el("div", "time-marker");
  if (day) m.classList.add("day");
  m.dataset.hm = hm;
  if (text) {
    m.dataset.hard = "1";
    if (day && date) {
      // Two lines: the date floats on its own row ABOVE, the time stays on the dot's row —
      // a combined "Yesterday · 21:24" overruns the 58px gutter and collides with the dot.
      const dd = el("span", "tm-date"); dd.textContent = date; m.appendChild(dd);
      const tt = el("span", "tm-time"); tt.textContent = hm; m.appendChild(tt);
    } else {
      m.textContent = text;
    }
  }
  return m;
}

// Post-layout spacing pass: minute-change stamps alone can leave a long unstamped scroll
// when many turns share a minute. After render we measure each timed turn's vertical
// position and reveal a suppressed stamp wherever >6 one-line rows have passed without one
// (the user 2026-06-12). Markers are absolutely-positioned in the gutter, so toggling their
// text never reflows the rail — the measurement is stable and the pass is idempotent
// (soft reveals are cleared and recomputed each run; data-hard stamps are left alone).
function restampMarkers(root: HTMLElement): void {
  const ms: HTMLElement[] = [];
  const ys: number[] = [];
  const hard: boolean[] = [];
  let prevY: number | null = null;
  let oneRow = Infinity;
  for (const t of Array.from(root.children) as HTMLElement[]) {
    const m = t.firstChild as HTMLElement | null;
    if (!m || m.nodeType !== 1 || !m.classList.contains("time-marker")) continue;
    const y = t.getBoundingClientRect().top;
    if (prevY != null) oneRow = Math.min(oneRow, y - prevY);
    prevY = y;
    if (m.dataset.hard !== "1") { m.textContent = ""; m.classList.remove("auto"); } // reset soft reveal
    ms.push(m); ys.push(y); hard.push(m.dataset.hard === "1");
  }
  if (!ms.length) return;
  if (!isFinite(oneRow) || oneRow <= 0) oneRow = 24;       // single row / degenerate → a sane default
  oneRow = Math.max(18, Math.min(oneRow, 80));             // clamp against noisy extremes
  const show = chooseStamps(ys, hard, oneRow, 6);
  for (let i = 0; i < ms.length; i++) {
    if (!hard[i] && show[i]) { ms[i].textContent = ms[i].dataset.hm || ""; ms[i].classList.add("auto"); }
  }
}

// Debounced rAF wrapper — coalesces the many syncView calls of a busy tail into one
// measure-and-restamp of the active view per frame.
let restampPending = false;
function scheduleRestamp(): void {
  if (restampPending) return;
  restampPending = true;
  requestAnimationFrame(() => {
    restampPending = false;
    const v = activeId ? views.get(activeId) : null;
    if (v) restampMarkers(v.el);
  });
}

function renderEventInner(ev: ChatEvent): HTMLElement {
  if (ev.kind === "system") return renderSystem(ev);
  if (ev.kind === "user") {
    // Three flavors of a "user-role" turn: a GENUINE typed prompt → the blue right-aligned bubble; a
    // message romp INJECTED (a feed nudge / follow-up — ev.romp) → a GRAY right-aligned bubble with a
    // "romp" tag, so it's clear romp (not you) sent it (the user 2026-06-19); everything else harness-
    // injected (compact summary, /command stdout, system reminders) → a neutral left note box.
    const romp = !!ev.romp;
    const injected = !ev.human && !romp;
    const turn = el("div", "turn turn-user" + (romp ? " romp" : injected ? " injected" : ""));
    // Prompts ride the rail like every other turn: their own dot + a left-gutter HH:MM marker (added in
    // renderEvent). Genuine prompts get the solid blue dot; a romp injection a gray dot; harness notes the
    // hollow ring used by assistant turns.
    turn.appendChild(dot(romp ? "romp" : injected ? "ring" : "user"));
    // a TYPED follow-up (resumed a goal) → a compact "↩ Follow-up · <goal>" header, the romp goal-context
    // quote + markers already stripped server-side. Same header the pending queued render uses (consistency).
    if (ev.followUp && !romp) turn.appendChild(followUpHeader(ev.goal));
    const hasImgs = !!(ev.images && ev.images.length);
    if (ev.md || hasImgs) {
      if (romp) {
        // every romp bubble carries the "romp" tag; the swirl LOGO is drawn ONLY on an AUTO-nudge (ev.rompAuto)
        // — NOT a Nudge-button click / typed follow-up, which are your actions (the user 2026-06-23). Served at
        // /media on the web dashboard; in a sandbox without it the img self-removes (alt stays empty).
        const tag = el("div", "romp-tag");
        if (ev.rompAuto) {
          const logo = el("img", "romp-tag-logo") as HTMLImageElement;
          logo.src = "/media/romp-swirl-glyph.svg"; logo.alt = ""; logo.onerror = () => logo.remove();
          tag.appendChild(logo);
        }
        tag.appendChild(document.createTextNode("romp"));
        turn.appendChild(tag);
      }
      const bubble = el("div", (romp ? "romp-bubble" : injected ? "user-note" : "user-bubble") + " md");
      // A slash COMMAND you sent reads as a special keyword, not prose (the user 2026-06-29): render the leading
      // "/cmd" token as a monospace chip, with any arguments after it as plain text. Genuine human bubbles only;
      // "/cmd" must be a WHOLE leading token (followed by a space or end) so a "/Users/…" path is never chipped.
      const cmd = (!romp && !injected && ev.md) ? ev.md.match(/^(\/[A-Za-z][\w-]*)(?=\s|$)([\s\S]*)$/) : null;
      if (cmd) {
        const chip = el("span", "slash-cmd-chip"); chip.textContent = cmd[1];
        bubble.appendChild(chip);
        const rest = cmd[2].replace(/^\s+/, "");
        if (rest) { const args = el("span", "slash-cmd-args"); args.textContent = rest; bubble.appendChild(args); }
      } else if (ev.md) {
        bubble.innerHTML = md(ev.md);
      }
      // images, IN the bubble (part of his message): thumbnail + open/copy caption;
      // a literal path in the typed text becomes the same open-link inline.
      if (ev.images) {
        linkifyImgPaths(bubble, ev.images.map((im) => im.path).filter((p): p is string => !!p));
        for (const im of ev.images) bubble.appendChild(userImage(im));
      }
      turn.appendChild(bubble);
    }
    if (ev.reminders && ev.reminders.length) {
      const body = el("div", "reminder-body");
      for (const r of ev.reminders) body.appendChild(preEl(r));
      const n = ev.reminders.length;
      const f = foldable(`ⓘ ${n} system reminder${n > 1 ? "s" : ""}`, body, ev.uuid ? "rem:" + ev.uuid : undefined);
      f.classList.add("reminder-fold");
      turn.appendChild(f);
    }
    return turn;
  }
  if (ev.kind === "assistant") {
    const turn = el("div", "turn turn-assistant");
    turn.appendChild(dot("ring"));
    const body = el("div", "assistant md");
    body.innerHTML = md(ev.md);
    highlight(body);
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
  if (ev.kind === "todo") return renderTodo(ev);
  if (ev.kind === "queued") return renderQueued(ev);
  if (ev.kind === "apiError") return renderApiError(ev);
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
// Make `elem` open the local folder `cwd` with the configured opener on click (the user 2026-06-27): used
// EVERYWHERE a folder location is shown (statusline, the System-context Directory row, …). Click-safe — the
// action rides a data-act caught by the document-level openFolder delegate, so it works under any re-rendering
// surface without per-node handlers.
function asFolderLink(elem: HTMLElement, cwd: string): void {
  if (!cwd) return;
  elem.dataset.act = "openFolder";
  elem.dataset.cwd = cwd;
  elem.classList.add("folder-link");
  elem.title = cwd + "  ·  click to open this folder";
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
  if (ev.version) rows.push(["Claude Code", ev.version]);
  if (rows.length) {
    const grid = el("div", "sys-meta");
    for (const [k, val] of rows) {
      const ke = el("span", "sys-key"); ke.textContent = k; grid.appendChild(ke);
      const ve = el("span", "sys-val"); ve.textContent = val;
      if (k === "Directory") asFolderLink(ve, val);   // the cwd path → click to open the folder
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
function renderCompact(_ev: Extract<ChatEvent, { kind: "compact" }>): HTMLElement {
  const turn = el("div", "turn turn-compact");
  turn.appendChild(dot("ring"));
  const line = el("div", "compact-line");
  line.textContent = "✦ Compacted";
  line.title = "the conversation was compacted here";
  turn.appendChild(line);
  return turn;
}

// A compact "Follow-up" header above a message that resumed a goal (the user 2026-06-27): a ↩ glyph + the
// goal title (when known), muted, so a follow-up reads as such WITHOUT dumping romp's goal-context quote into
// the bubble. Shared by landed user turns + pending queued messages so the two render consistently.
function followUpHeader(goal?: string): HTMLElement {
  const h = el("div", "followup-tag");
  h.appendChild(document.createTextNode("↩ Follow-up"));
  if (goal) { const g = el("span", "followup-goal"); g.textContent = goal; h.appendChild(g); }
  return h;
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

// Pending queued messages — the user's inputs submitted while the session was still working, not yet
// processed. Rendered at the bottom (closest to the composer) as faint right-aligned "you" bubbles, the SAME
// way a landed message renders (markdown, follow-ups cleaned of the romp goal-context + markers, with the
// compact Follow-up header) so a pending message looks like what it'll become (the user 2026-06-27).
function renderQueued(ev: Extract<ChatEvent, { kind: "queued" }>): HTMLElement {
  const turn = el("div", "turn turn-queued");
  const n = ev.texts.length;
  const head = el("div", "queued-head");
  head.appendChild(hourglassIcon());
  const label = el("span"); label.textContent = `${n} queued message${n === 1 ? "" : "s"}`;
  head.appendChild(label);
  turn.appendChild(head);
  for (const t of ev.texts) {
    if (t.followUp) turn.appendChild(followUpHeader(t.goal));
    const bubble = el("div", "queued-bubble md" + (t.cancelable ? " cancelable" : ""));
    bubble.innerHTML = md(t.md);
    // CANCELABLE (SDK queue, romp owns it): click a still-queued message to pull it BACK OUT — cancels it
    // and drops its text into the composer to re-edit/re-send (the user 2026-06-27). Hover highlight + a
    // tooltip advertise that it's clickable. tmux queues aren't cancelable (Claude Code owns them), so those
    // bubbles render plain.
    if (t.cancelable && t.idx !== undefined) {
      bubble.title = "click to cancel this queued message and move it back to the composer";
      bubble.addEventListener("click", () => {
        if (activeId && vscodeApi) vscodeApi.postMessage({ type: "cancelQueued", id: activeId, idx: t.idx });
        restoreToComposer(t.md);
        bubble.remove();                                   // optimistic; the next push rebuilds without it
      });
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
  const retry = el("button", "apierror-retry") as HTMLButtonElement;
  retry.textContent = "Retry now";
  retry.title = "send “retry” into this session right now (also resets the auto-retry countdown)";
  retry.addEventListener("click", () => {
    if (vscodeApi) vscodeApi.postMessage({ type: "apiRetry", id: activeId });
    if (activeId) apiRetryNext.set(activeId, Date.now() + API_RETRY_MS);   // restart the countdown
  });
  head.appendChild(retry);
  // "Stop retrying" pauses THIS error's auto-retry loop (the user 2026-06-24) — otherwise it retries forever
  // with no off-switch. Per-instance: re-arms automatically once the session recovers. You can still Retry now.
  const paused = activeId ? retryPaused.has(activeId) : false;
  if (paused) countdown.textContent = "auto-retry off";
  const stop = el("button", "apierror-stop") as HTMLButtonElement;
  stop.textContent = paused ? "Resume" : "Stop retrying";
  stop.title = paused ? "resume auto-retrying this session"
    : "stop the auto-retry loop for this error — Retry now still works; it re-arms when the session recovers";
  stop.addEventListener("click", () => {
    const id = activeId; if (!id) return;
    if (retryPaused.has(id)) { retryPaused.delete(id); apiRetryNext.set(id, Date.now() + API_RETRY_MS); stop.textContent = "Stop retrying"; countdown.textContent = "retrying soon…"; }
    else { retryPaused.add(id); stop.textContent = "Resume"; countdown.textContent = "auto-retry off"; }
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
// Sessions whose auto-retry the user PAUSED via "Stop retrying" (the user 2026-06-24). Per-instance: it's
// cleared the moment the session recovers (no longer blocked → a message got through), so the NEXT API error
// auto-retries again. While paused the session stays blocked; "Retry now" + sending a message still work.
const retryPaused = new Set<string>();
function apiRetryTick(): void {
  const now = Date.now();
  const blocked = new Set<string>();
  sessions.forEach((s, id) => { if (s.status.state === "blocked") blocked.add(id); });
  apiRetryNext.forEach((_, id) => { if (!blocked.has(id)) apiRetryNext.delete(id); });   // recovered → stop
  retryPaused.forEach((id) => { if (!blocked.has(id)) retryPaused.delete(id); });        // recovered → re-arm auto-retry
  blocked.forEach((id) => {
    if (retryPaused.has(id)) return;                                                     // user stopped retrying this one
    if (!apiRetryNext.has(id)) apiRetryNext.set(id, now + API_RETRY_MS);
    if (now >= (apiRetryNext.get(id) as number)) {
      if (vscodeApi) vscodeApi.postMessage({ type: "apiRetry", id });
      apiRetryNext.set(id, now + API_RETRY_MS);                                          // reset the countdown
    }
  });
  // live "retrying in Ns" on the active session's card, if it's the blocked one being viewed
  const cd = document.querySelector(".apierror-countdown") as HTMLElement | null;
  if (cd) {
    if (activeId && retryPaused.has(activeId)) cd.textContent = "auto-retry off";
    else { const at = activeId ? apiRetryNext.get(activeId) : undefined;
           cd.textContent = at ? `retrying in ${Math.max(0, Math.ceil((at - now) / 1000))}s` : "retrying soon…"; }
  }
}
setInterval(apiRetryTick, 1000);

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
  } else if (ev.diff) {
    // Edit/MultiEdit: "+add −del" on the head line; the red/green diff hangs below, hidden.
    let add = 0, del = 0;
    for (const l of ev.diff.split("\n")) { if (l[0] === "+") add++; else if (l[0] === "-") del++; }
    const pre = diffPre(ev.diff);
    inlineFold(head, turn, `+${add} −${del}`, pre, fkey);
  } else if (ev.name === "Read") {
    if (ev.output) inlineFold(head, turn, `${countLines(ev.output)} lines`, preEl(ev.output), fkey);
  } else if (!ack && (ev.input || ev.output)) {
    const signal = ev.name === "Task" || ev.name === "Agent";
    if (signal) {
      // Subagent (Task/Agent) = a delegated mini-conversation. Collapse the WHOLE dispatch to ONE line, like
      // Bash/Read (the user 2026-06-22): the PROMPT and the agent's REPORT both tuck below the head behind a
      // single toggle, hidden until clicked. The report is the meatier half, so the head summary is its line
      // count once it's back (else just "prompt"). The report still renders as a faded, green-edged
      // sub-transcript when expanded (the user 2026-06-14: not a big text box — and now not a big block).
      const body = el("div", "agent-fold");
      if (ev.input) {
        let promptText = ev.input;   // ev.input is the tool's full JSON; show just the prompt the agent was given
        try { const o = JSON.parse(ev.input); if (o && typeof o.prompt === "string") promptText = o.prompt; } catch { /* truncated JSON → show raw */ }
        const lab = el("div", "agent-fold-label"); lab.textContent = "prompt";
        body.appendChild(lab); body.appendChild(preEl(promptText));
      }
      if (ev.output) {
        const lab = el("div", "agent-fold-label"); lab.textContent = "report";
        const report = el("div", "agent-report md"); report.innerHTML = md(ev.output); highlight(report);
        body.appendChild(lab); body.appendChild(report);
      }
      // running (no report yet) reads as "running…", a clear in-progress state; once it reports → its line count
      const summary = ev.output ? `report · ${countLines(ev.output)} lines` : "running…";
      inlineFold(head, turn, summary, body, fkey ? fkey + ":agent" : undefined);
    } else if (!ev.output) {
      const io = el("div", "tool-io"); if (ev.input) io.appendChild(ioRow("IN", ev.input, false)); turn.appendChild(io);
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
// One-line summary for a postal card: the incoming Haiku caption, else the first non-empty line of the
// body (sent mail carries no caption), truncated. The full message lives behind a click-to-expand.
function postalServiceSummary(ev: Extract<ChatEvent, { kind: "postal-service" }>): string {
  const cap = ev.summary && ev.summary.trim();
  if (cap) return cap;
  const first = (ev.body || "").split("\n").map((s) => s.trim()).find(Boolean) || "";
  return first.length > 100 ? first.slice(0, 99).trimEnd() + "…" : first;
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
  // the romp swirl marks this as a romp-postal-service message (the user 2026-06-23: postal is "from romp" too)
  const rlogo = el("img", "postal-service-romp-logo") as HTMLImageElement;
  rlogo.src = "/media/romp-swirl-glyph.svg"; rlogo.alt = ""; rlogo.title = "Romp Postal Service message"; rlogo.onerror = () => rlogo.remove();
  head.appendChild(rlogo);
  head.appendChild(arrow);
  head.appendChild(verb);
  head.appendChild(peer);
  setPeerDot(peer, workingSet.has(ev.peer));   // working dot before the peer name if that session is working

  // interaction-type chip (delegation / coordination / ask / question / FYI), from the leading intent token
  const intent = postalServiceIntent(ev.body);
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
    sum.title = "click to expand the full message";
    sum.addEventListener("click", () => {
      const open = body.classList.toggle("expanded");
      caret.textContent = open ? "▾" : "▸";
    });
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

// Persist the current full tab order to the shared store (host writes session-order.json → the timeline reads
// the same file). Called only after a drag — the one client action that changes the order.
function commitTabOrder() {
  if (vscodeApi) vscodeApi.postMessage({ type: "reorderTabs", order: order.slice() });
}
// Apply the kernel's authoritative tab order (its tabOrder push, also re-sent after a timeline drag).
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
  const next = reconcileTabOrder(kernelOrder, order, (id) => sessions.has(id) || tabMeta.has(id));
  order.length = 0;
  for (const id of next) order.push(id);
  renderTabs();
}
let draggedId: string | null = null;
function reorderTo(dragId: string, targetId: string, after: boolean) {
  const di = order.indexOf(dragId);
  if (di < 0) return;
  order.splice(di, 1);
  const ti = order.indexOf(targetId);
  if (ti < 0) order.push(dragId);
  else order.splice(after ? ti + 1 : ti, 0, dragId);
  commitTabOrder();
  renderTabs();
}

// Rich tab hover tooltip (the user 2026-06-23): a CUSTOM DOM tooltip (a native `title` can't colour/bold).
// Shows the backend BOLD in the session's own romp identity colour, the full directory path, the git
// branch, mode / model / effort, the context battery, a labelled "Summary" row, and a labelled "Latest"
// row = the collapsed ledger's current-top-goal recency-coloured "(Xm ago)". One shared element,
// repositioned under the hovered tab and clamped on-screen.
let tabTipEl: HTMLElement | null = null;
function hideTabTip(): void { if (tabTipEl) tabTipEl.style.display = "none"; }
function showTabTip(tab: HTMLElement, s: Session): void {
  if (!tabTipEl) { tabTipEl = el("div", "tab-tip"); document.body.appendChild(tabTipEl); }
  const tip = tabTipEl;
  tip.replaceChildren();
  const now = Date.now() / 1000;
  // backend, BOLD, coloured BY BACKEND — tmux → green, SDK → blue, the canonical romp _palette shades
  // (the user 2026-06-23: thematic consistency over the session's identity colour, which v2 had used)
  const be = s.status.backend;
  if (be === "sdk" || be === "tmux") {
    const b = el("div", "tab-tip-be");
    b.textContent = (be === "sdk" ? "SDK" : "tmux") + " backend";
    b.style.color = be === "tmux" ? "#54B204" : "#1EA1EB";
    tip.appendChild(b);
  }
  if (s.cwd) { const d = el("div", "tab-tip-path"); d.textContent = s.cwd; tip.appendChild(d); }
  // labelled rows: git branch (from the system-context event) + mode / model / effort
  const sys = s.events.find((e) => e.kind === "system") as Extract<ChatEvent, { kind: "system" }> | undefined;
  const rows: Array<[string, string]> = [];
  if (sys?.gitBranch) rows.push(["Branch", sys.gitBranch]);
  if (s.status.mode) rows.push(["Mode", prettyMode(s.status.mode)]);
  if (s.status.model) rows.push(["Model", s.status.model]);
  if (s.status.effort) rows.push(["Effort", s.status.effort]);
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
  // ledger rows, LABELLED + aligned with the rows above (the user 2026-06-23 v3): the summary, then the
  // collapsed ledger's current-top-goal with its recency-coloured "(Xm ago)" — the same line the ledger
  // shows when collapsed (currentTopGoal + nodeRecency, via the same stamp the active ledger uses).
  const lg = ledgers.get(s.id);
  if (lg?.summary) {
    const r = el("div", "tab-tip-row");
    const k = el("span", "tab-tip-k"); k.textContent = "Summary";
    const v = el("span", "tab-tip-v"); v.textContent = lg.summary;
    r.appendChild(k); r.appendChild(v); tip.appendChild(r);
  }
  if (lg?.tree && lg.tree.length) {
    // The last few things this session worked on (the user 2026-06-24): the up-to-5 most-recently-touched
    // ledger nodes, each in its own recency colour with a "(Xm ago)" time — replaces the single "Latest"
    // line. Sorted by each node's OWN recency (mt ?? t), so it's the actual recent work items, not umbrella
    // tops floated up by a rolled-up subtree recency.
    const recent = lg.tree
      .filter((n) => (n.text || "").trim() && !n.cleared)
      .map((n) => ({ n, t: (n.mt ?? n.t) || 0 }))
      .filter((x) => x.t > 0)
      .sort((a, b) => b.t - a.t)
      .slice(0, 5);
    if (recent.length) {
      const r = el("div", "tab-tip-row tab-tip-recent");
      const k = el("span", "tab-tip-k"); k.textContent = "Recent";
      const list = el("div", "tab-tip-recent-list");
      for (const { n, t } of recent) {
        const item = el("div", "tab-tip-recent-item");
        const txt = el("span"); txt.textContent = n.text;
        const ago = el("span", "tab-tip-ago"); ago.textContent = " (" + agehms(now - t) + " ago)";
        item.appendChild(txt); item.appendChild(ago);
        item.style.color = ageColorReadable(now - t);          // text + time both in the node's recency colour
        list.appendChild(item);
      }
      r.appendChild(k); r.appendChild(list); tip.appendChild(r);
    }
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
// A loading PLACEHOLDER tab (the user 2026-06-26): name + identity color from the kernel's tabOrder push,
// shown while the session's build_session is still in flight so the strip's full width is reserved up front
// (no one-by-one pop-in). Non-interactive — no select/close/drag — until the real session arrives and
// renderTabs swaps in the full tab. A gentle pulse (.tab-placeholder) reads as "loading".
function makePlaceholderTab(id: string): HTMLElement {
  const meta = tabMeta.get(id);
  const tab = el("div", "tab tab-placeholder");
  tab.dataset.id = id;
  if (meta?.color) {
    tab.style.setProperty("--chip-bg", meta.color.bg);
    tab.style.setProperty("--chip-fg", meta.color.fg);
    tab.classList.add("colored");
  }
  const label = el("span", "tab-label");
  label.textContent = meta?.name || "…";
  tab.appendChild(label);
  return tab;
}

function renderTabs() {
  if (renameActive) { renderPendingAfterRename = true; return; }
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
  const ids: string[] = [];
  const seen = new Set<string>();
  for (const id of order) { if (!seen.has(id)) { seen.add(id); ids.push(id); } }
  for (const id of tabMeta.keys()) { if (!seen.has(id)) { seen.add(id); ids.push(id); } }   // any pushed tab not yet in `order` (placeholder)
  for (const id of ids) {
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
    // "blocked" is an API error. A "prompt is too long" one is on YOU (compact) → alarm-red dashed; a TRANSIENT
    // API error is auto-retrying and needs no attention → the amber retrying treatment, not red (the user 2026-06-29).
    else if (st === "blocked") tab.classList.add(s.status.apiTooLong ? "tab-blocked" : "tab-retrying");
    else if (st === "awaiting") tab.classList.add("tab-awaiting");
    else if (st === "retrying") tab.classList.add("tab-retrying");       // amber: soft-blocked on an API auto-retry
    else if (st === "compacting") tab.classList.add("tab-compacting");
    else if (st === "closed") tab.classList.add("tab-closed");       // dead session: read-only, struck-through label
    if (s.status.faded) tab.classList.add("at-rest");
    // WORKING shows a yellow dot; BLOCKED (API error) gets NO dot — the dashed red tab highlight instead
    // (the user 2026-06-16).
    if (st === "working") tab.appendChild(el("span", "tab-dot"));
    // compacting → a tiny animated compaction bar before the name (the tab gets no outline for this state,
    // so the bar IS the cue). A teal fill whose right edge slides left and loops — the same "compression"
    // motion as the statusline ctx-scan bar (.ctx-compress), miniaturised. Replaces the static ⇲ glyph the
    // user disliked (2026-06-24): motion reads as a transient PROCESS, not a status colour. Compacting can't
    // coincide with working, so no dot clash.
    if (st === "compacting") {
      const ci = el("span", "tab-compacting-bar");
      ci.appendChild(el("span", "tab-compacting-fill"));
      ci.title = "compacting — compressing the conversation to free up context";
      tab.appendChild(ci);
    }
    const label = el("span", "tab-label");
    label.textContent = s.name;
    if (s.status.faded && id !== activeId && s.color) {
      const full = s.color.bg;
      label.style.color = fadedColor(full);
      // hover un-fades the name to its full (readable) identity color, reverting on leave
      tab.addEventListener("mouseenter", () => { label.style.color = full; });
      tab.addEventListener("mouseleave", () => { label.style.color = fadedColor(full); });
    }
    tab.appendChild(label);
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
    close.title = dead ? "Close tab" : "Close tab (or end the session)";
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
    tab.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); showTabMenu(e, tab, label, id); });
    bar.appendChild(tab);
  }
  const add = el("div", "tab tab-add");
  add.textContent = "+";
  add.title = "Open a session";
  add.addEventListener("click", () => openPicker());
  bar.appendChild(add);
  // Restore tab-mode focus if a tab held it before this rebuild (see the top of renderTabs).
  if (refocusTab) focusActiveTab();
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
  if (activeId) { drafts.set(activeId, ta.value); persistDrafts(); }
}

function copyToClipboard(text: string) {
  navigator.clipboard?.writeText(text).catch(() => { try { document.execCommand("copy"); } catch { /* best effort */ } });
}
// Toggle a per-session view flag (feed mute / postal isolation) — the SAME message the timeline lane toggles
// send, persisted + re-broadcast by the kernel. Optimistically update the local copy so reopening the menu
// reflects it before the next push (the kernel reconciles).
function setSessionFlag(id: string, flag: "hideFromFeed" | "postalServiceOff", value: boolean) {
  const s = sessions.get(id);
  if (s) (s as Record<string, unknown>)[flag] = value;
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
  if (vscodeApi) vscodeApi.postMessage({ type: "setSessionColor", id, bg });
}

// Small inline-SVG icon for the tab menu's toggle items (trusted constant markup; `off` slashes + dims it,
// matching the timeline lane toggles). 16-unit viewBox; currentColor so .ctx-icon/.off set the tint.
function ctxIcon(kind: "feed" | "mail", off: boolean): HTMLElement {
  const span = el("span", "ctx-icon" + (off ? " off" : ""));
  const slash = off ? '<line x1="1.6" y1="14.4" x2="14.4" y2="1.6"/>' : "";
  const body = kind === "feed"
    ? '<circle cx="8" cy="8" r="6"/><path d="M5 8.3 L7.2 10.7 L11.4 5.3"/>'              // circle + check (on the feed)
    : '<rect x="2" y="4" width="12" height="8" rx="1.5"/><path d="M2.5 5 L8 9 L13.5 5"/>';  // envelope (on the postal service)
  span.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '
    + 'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' + body + slash + "</svg>";
  return span;
}

function showTabMenu(e: MouseEvent, tab: HTMLElement, label: HTMLElement, id: string) {
  dismissTabMenu();
  const menu = el("div", "ctx-menu");
  const rename = el("div", "ctx-item");
  rename.textContent = "Rename";
  rename.addEventListener("click", (ev) => { ev.stopPropagation(); dismissTabMenu(); startTabRename(tab, label, id); });
  menu.appendChild(rename);
  // Feed + Mail per-session toggles (the user 2026-06-26) — the same controls as the timeline lane's feed
  // checkbox + postal mailbox, here as icon + label + a faint "what it does" sub-line. State from the session.
  const s = sessions.get(id);
  const offFeed = !!(s && s.hideFromFeed);
  const offMail = !!(s && s.postalServiceOff);
  menu.appendChild(el("div", "ctx-sep"));
  const toggle = (kind: "feed" | "mail", off: boolean, lab: string, sub: string, fn: () => void) => {
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
window.addEventListener("mousedown", (e) => { if (ctxMenuEl && !ctxMenuEl.contains(e.target as Node)) dismissTabMenu(); }, true);
window.addEventListener("keydown", (e) => { if (e.key === "Escape") dismissTabMenu(); }, true);
window.addEventListener("scroll", dismissTabMenu, true);
window.addEventListener("blur", () => dismissTabMenu());

// "Rename" (tab context menu): swap the tab's label for an inline input. Enter
// or clicking away commits (the host renames the tmux session and confirms with
// a "renamed" message — the label only changes once that lands), Esc cancels.
function startTabRename(tab: HTMLElement, label: HTMLElement, id: string) {
  const s = sessions.get(id);
  if (!s || tab.querySelector(".tab-rename")) return;
  const input = document.createElement("input") as HTMLInputElement;
  input.className = "tab-rename";
  input.value = s.name;
  input.spellcheck = false;
  input.size = Math.max(s.name.length, 4);
  renameActive = true;
  tab.draggable = false;            // dragging would eat the text selection
  label.style.display = "none";
  label.after(input);
  let finished = false;
  const done = (commit: boolean) => {
    if (finished) return;
    finished = true;
    const v = input.value.trim();
    input.remove();
    label.style.display = "";
    tab.draggable = true;
    renameActive = false;
    if (renderPendingAfterRename) { renderPendingAfterRename = false; renderTabs(); }
    if (commit && v && v !== s.name && vscodeApi) vscodeApi.postMessage({ type: "renameSession", id, name: v });
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
// AskUserQuestion picker is up the composer is hidden and the PICKER CARD owns the keyboard (↑/↓ step the
// options, Enter confirms), so focus that; otherwise focus the message box (the user 2026-06-27). Returns
// whether it focused something (so the caller can preventDefault only then).
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

// "Opening session…" modal — shown the instant the user creates a session and
// dismissed when its tab actually arrives (the kernel spawn → tmux → first
// transcript poll has a visible delay; this is the "something is happening" cue).
let pendingNewSession: string | null = null;
let openingTimer: ReturnType<typeof setTimeout> | undefined;
function showOpeningModal(name: string) {
  hideOpeningModal();
  pendingNewSession = name;
  const overlay = el("div", "picker-overlay opening-overlay");
  overlay.id = "opening";
  overlay.style.display = "flex";
  const box = el("div", "picker-box opening-box");
  const title = el("div", "opening-title"); title.textContent = "Opening session";
  const nm = el("div", "opening-name"); nm.textContent = name;
  const dots = el("div", "opening-dots"); dots.append(el("span"), el("span"), el("span"));
  box.append(title, nm, dots);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  // safety net: never strand the modal if the session never materializes (spawn failed)
  openingTimer = setTimeout(hideOpeningModal, 30000);
}
function hideOpeningModal() {
  pendingNewSession = null;
  if (openingTimer) { clearTimeout(openingTimer); openingTimer = undefined; }
  document.getElementById("opening")?.remove();
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
    search.addEventListener("input", () => { filterPicker(search.value); pickerError(null); });
    const errLine = el("div", "picker-error"); errLine.id = "picker-error";
    const list = el("div", "picker-list"); list.id = "picker-list";
    // hover and keyboard share one "active" row
    list.addEventListener("mouseover", (e) => {
      const row = (e.target as HTMLElement).closest(".picker-row");
      if (row) setActiveRow(row as HTMLElement);
    });
    // Directory for a NEW session — fixed once the session starts, so it's chosen here. Prefilled with the
    // gear's "Default directory" on open; recent dirs autocomplete from the datalist; the kernel expands
    // ~ / $VARs and validates it exists. Hidden in pick-mode (choosing an existing session).
    const dirWrap = el("div", "picker-dir");
    const dirInput = el("input", "picker-dir-input") as HTMLInputElement;
    dirInput.id = "picker-dir";
    dirInput.spellcheck = false;
    dirInput.placeholder = "New-session directory (blank = default)";
    dirInput.title = "Working directory for a NEW session — fixed once it starts. Blank uses the kernel's default. ~ and $VARs expand; recent dirs autocomplete.";
    dirInput.setAttribute("list", "picker-dir-list");
    const dirList = document.createElement("datalist"); dirList.id = "picker-dir-list";
    const browseBtn = el("button", "picker-browse") as HTMLButtonElement;
    browseBtn.type = "button"; browseBtn.textContent = "Browse…";
    browseBtn.title = "Pick a folder with the native macOS dialog (opens on the kernel's machine — host-local)";
    browseBtn.addEventListener("click", () => { if (vscodeApi) vscodeApi.postMessage({ type: "browseDir" }); });
    dirWrap.appendChild(dirInput); dirWrap.appendChild(dirList); dirWrap.appendChild(browseBtn);
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
    beWrap.append(beLabel, mkBe("tmux", "tmux", "Drives a real terminal pane (tmux)."),
                  mkBe("sdk", "SDK", "Runs headless via the Claude Agent SDK."));
    const actions = el("div", "picker-actions");
    const newSess = el("button", "picker-action");
    newSess.id = "picker-new-btn";
    newSess.textContent = "✛ New session";
    newSess.title = "create a fresh romp session, named by the search box, and open it as a tab";
    newSess.addEventListener("click", () => {
      // The search box doubles as the name field — no native dialog.
      const name = search.value.trim();
      if (!name) { pickerError("Type the new session's name in the box above first."); search.focus(); return; }
      if (!/^[A-Za-z0-9._-]+$/.test(name)) { pickerError("Session names: letters, digits, . _ - only."); search.focus(); return; }
      // backend: this picker's toggle (defaults to the gear's "Default backend", overridable per session)
      const beSel = beWrap.querySelector(".picker-be-opt.sel") as HTMLElement | null;
      if (vscodeApi) vscodeApi.postMessage({ type: "createSession", name, backend: beSel?.dataset.be || loadSettings().backend, dir: dirInput.value.trim() });
      closePicker();
      showOpeningModal(name);   // "Opening…" cue until the new tab arrives (see upsert)
    });
    const openAll = el("button", "picker-action");
    openAll.textContent = "↗ Open all running sessions";
    openAll.addEventListener("click", () => {
      if (vscodeApi) vscodeApi.postMessage({ type: "openAll" });
      closePicker();
    });
    actions.appendChild(newSess);
    actions.appendChild(openAll);
    box.appendChild(search);
    box.appendChild(errLine);
    box.appendChild(list);
    box.appendChild(dirWrap);
    box.appendChild(beWrap);
    box.appendChild(actions);
    overlay.appendChild(box);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closePicker(); });
    document.body.appendChild(overlay);
    document.addEventListener("keydown", pickerKey);
  }
  overlay.style.display = "flex";
  const actions = overlay.querySelector(".picker-actions") as HTMLElement | null;
  if (actions) actions.style.display = pick ? "none" : "";
  const dirWrap = overlay.querySelector(".picker-dir") as HTMLElement | null;
  if (dirWrap) dirWrap.style.display = pick ? "none" : "";   // dir only matters when creating, not picking
  const beWrapEl = overlay.querySelector(".picker-backend") as HTMLElement | null;
  if (beWrapEl) {   // reset the backend toggle to the gear default each open (overridable for this session)
    beWrapEl.style.display = pick ? "none" : "";
    const def = loadSettings().backend || "tmux";
    beWrapEl.querySelectorAll(".picker-be-opt").forEach((x) => x.classList.toggle("sel", (x as HTMLElement).dataset.be === def));
  }
  const di = document.getElementById("picker-dir") as HTMLInputElement | null;
  if (di) di.value = kernelDefaultDir || loadSettings().defaultDir || "";   // the kernel's persisted default (file→env) wins; localStorage is a same-tab cache
  const s = document.getElementById("picker-search") as HTMLInputElement | null;
  if (s) { s.value = ""; s.placeholder = prompt || "Search sessions, or type a new session's name…"; s.focus(); }
  filterPicker(""); // reset row visibility and disarm the New-session button from a prior open
  pickerError(null);
  if (vscodeApi) vscodeApi.postMessage({ type: "requestSessions" });
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

function closePicker() {
  const o = document.getElementById("picker");
  if (o) o.style.display = "none";
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
}

function moveActive(delta: number) {
  const rows = pickerRows();
  if (!rows.length) return;
  const cur = rows.findIndex((r) => r.classList.contains("active"));
  const next = cur < 0 ? (delta > 0 ? 0 : rows.length - 1) : (cur + delta + rows.length) % rows.length;
  setActiveRow(rows[next]);
}

function pickerKey(e: KeyboardEvent) {
  const o = document.getElementById("picker");
  if (!o || o.style.display === "none") return;
  if (e.key === "Escape") closePicker();
  else if (e.key === "ArrowDown") { e.preventDefault(); moveActive(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveActive(-1); }
  else if (e.key === "Enter") {
    e.preventDefault();
    const active = document.querySelector("#picker-list .picker-row.active:not(.hidden)") as HTMLElement | null;
    const target = active ?? pickerRows()[0];
    if (target) { target.click(); return; }
    // No matching session row — if the New-session button is armed (unique
    // name typed), Enter creates it.
    const btn = document.getElementById("picker-new-btn");
    if (btn?.classList.contains("active")) btn.click();
  }
}

// The kernel's real default new-session directory (its serve cwd, ~-ified), from the sessionList payload —
// prefilled into the dir field when there's no gear default, so "the default path is written in there".
let kernelDefaultDir = "";
function renderPicker(items: any[]) {
  const list = document.getElementById("picker-list");
  if (!list) return;
  list.replaceChildren();
  for (const it of items) {
    const row = el("div", "picker-row" + (it.running ? " running" : ""));
    row.dataset.search = (it.name + " " + (it.summary || "")).toLowerCase();
    const top = el("div", "picker-row-top");
    const name = el("span", "picker-name");
    name.textContent = it.name;
    if (it.color && it.color.bg) name.style.color = it.color.bg;
    const time = el("span", "picker-time");
    time.textContent = it.running ? "running" : it.time;
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
    list.appendChild(row);
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
  // Recent dirs → the new-session field's autocomplete (unique, non-empty, in list order).
  const dl = document.getElementById("picker-dir-list");
  if (dl) {
    dl.replaceChildren();
    const seen = new Set<string>();
    for (const it of items) {
      const d = (it.dir || "").trim();
      if (d && !seen.has(d)) { seen.add(d); const o = document.createElement("option"); o.value = d; dl.appendChild(o); }
    }
  }
  // Prefill the dir field with the kernel's real default path once it arrives (only if untouched + no gear
  // default) — so the actual default is written in there as an editable starting point (the user 2026-06-23).
  const di = document.getElementById("picker-dir") as HTMLInputElement | null;
  if (di && !di.value) di.value = kernelDefaultDir || loadSettings().defaultDir || "";
  // Re-apply the current filter (the list may refresh while the user is mid-
  // type) — it also sets the active row / arms the New-session button.
  const s = document.getElementById("picker-search") as HTMLInputElement | null;
  filterPicker(s?.value || "");
}

function filterPicker(q: string) {
  const query = q.toLowerCase();
  document.querySelectorAll("#picker-list .picker-row").forEach((r) => {
    const row = r as HTMLElement;
    const hit = !query || (row.dataset.search || "").includes(query);
    row.classList.toggle("hidden", !hit);
  });
  setActiveRow(pickerRows()[0] ?? null); // keep the highlight on the top of the filtered list
  // A name that matches NO session is a new one: move the highlight to the
  // "✛ New session" button so a bare Enter creates it (mirrors how the first
  // matching row is auto-selected when there ARE matches).
  const btn = document.getElementById("picker-new-btn");
  if (btn) {
    const actionsShown = (btn.closest(".picker-actions") as HTMLElement | null)?.style.display !== "none";
    btn.classList.toggle("active", actionsShown && !!q.trim() && pickerRows().length === 0);
  }
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
  let target = (v?.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`)
                || v?.el.querySelector(`.turn[data-mid="${cssEscape(uuid)}"]`)) as HTMLElement | null;
  // Deep-link into history the window doesn't currently cover (the head/tail folded into a spacer): find the
  // event, render a fresh window AROUND its unit, then re-query — the "load it when you jump there" behaviour.
  // (No match anywhere → genuinely off the active path; stash for the next render pass.)
  if (!target && v && activeId) {
    const s = sessions.get(activeId);
    const idx = s ? s.events.findIndex((e) => e.uuid === uuid || (e as { mid?: string }).mid === uuid) : -1;
    if (s && idx >= 0) {
      const items = displayItems(s);
      let u = items.findIndex((it) => it.kind === "toolgroup" ? it.indices.includes(idx) : it.index === idx);
      if (u < 0) u = Math.max(0, items.findIndex((it) => itemFirstEvent(it) >= idx));
      const working = s.status.state === "working" || s.status.state === "compacting";
      renderWindowItems(v, s, items, Math.max(0, u - WINDOW_RADIUS), Math.min(items.length, u + WINDOW_RADIUS), working);
      target = (v.el.querySelector(`.turn[data-uuid="${cssEscape(uuid)}"]`)
                || v.el.querySelector(`.turn[data-mid="${cssEscape(uuid)}"]`)) as HTMLElement | null;
    } else if (s && (s.headFrom ?? 0) > 0) {
      // The anchor is OLDER than the resident tail — the chat ships only WIRE_TAIL events and streams older
      // history in on demand, so a deep-link to a message past the tail had nothing to match and honest-failed
      // with "couldn't locate" even though it's in the transcript (the user 2026-06-27). Fetch the next older
      // chunk re-anchored on THIS uuid; chatHead lands on it when it arrives, and if it's STILL further back
      // this same branch fires again — a fetch-until-resident loop that terminates when headFrom reaches 0.
      if (fetchOlderForAnchor(activeId, uuid)) {
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
    if (!only || !only.classList?.contains("tx-empty")) {
      while (v.el.firstChild) v.el.removeChild(v.el.firstChild);
      const ph = el("div", "tx-empty"); ph.textContent = "No messages yet."; v.el.appendChild(ph);
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
  // A fresh build / rewind shows just the TAIL window (bounded → instant switch + small DOM).
  if (firstBuild || rewind) {
    renderWindowItems(v, s, items, Math.max(0, total - WINDOW_TAIL), total, working); v.stale = false; return v;
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
  const hasSpacer = (v.winStart ?? 0) > 0 ? 1 : 0;
  let from = Math.min(v.rendered, Math.max(0, len - TAIL_RECHECK));
  from = Math.max(from, v.winStart ?? 0);
  const keep = hasSpacer + (from - (v.winStart ?? 0));
  while (v.el.childNodes.length > keep) v.el.removeChild(v.el.lastChild as ChildNode);
  for (let i = from; i < len; i++) {
    const node = renderEvent(s.events[i], prevTimedEpoch(s.events, i), turnWorkedSecs(s.events, i, working));
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

// Append one display unit's DOM to v.el (a turn, or a folded toolgroup + its expansion), tagging every node
// with data-unit = u for the scroll↔unit map. Returns the advanced prevEpoch.
function appendItem(v: View, s: Session, items: DisplayItem[], u: number, prevEpoch: number | null, working: boolean): number | null {
  const it = items[u];
  const tag = (node: HTMLElement): HTMLElement => { node.dataset.unit = String(u); return node; };
  const adv = (i: number) => { const ep = eventEpoch(s.events[i]); if (ep != null) prevEpoch = ep; };
  if (it.kind === "toolgroup") {
    const first = s.events[it.indices[0]];
    const key = toolGroupKey(first);
    const tools = it.indices.map((i) => s.events[i]) as Extract<ChatEvent, { kind: "tool" }>[];
    const open = expandedGroups.has(key);
    v.el.appendChild(tag(renderToolGroup(tools, prevEpoch, key, open)));
    adv(it.indices[0]);
    if (open) {   // the original contiguous span (tools + any thinking between), each as its normal turn
      const start = it.indices[0], end = it.indices[it.indices.length - 1];
      for (let i = start; i <= end; i++) {
        const child = renderEvent(s.events[i], prevEpoch, turnWorkedSecs(s.events, i, working));
        child.classList.add("tg-child"); if (i === end) child.classList.add("tg-last");
        v.el.appendChild(tag(child)); adv(i);
      }
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
  scheduleRestamp();
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
// inherit the original prompt's elapsed (the user 2026-06-22: "worked 23m" for a
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
    const closed = s.status.state === "closed";
    composer.disabled = closed;
    composer.placeholder = closed ? "Session closed — read-only" : "Message this session…  (⏎ send · ⇧⏎ newline)";
    const sendBtn = document.getElementById("composer-send") as HTMLButtonElement | null;
    if (sendBtn) sendBtn.disabled = closed;   // read-only session → the explicit send button is dead too
  }
  // tint the whole-window border with the active session's identity color
  if (s.color && s.color.bg) document.body.style.setProperty("--active-accent", s.color.bg);
  else document.body.style.removeProperty("--active-accent");
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
function landActive(content: HTMLElement | null, v: View): void {
  if (!content) return;
  sizeSpacers(v);  // the view is now VISIBLE (display set in showActive), so the spacers get a real height
                   // measurement — a tab pre-built while display:none could only fall back until now
  const att = { anchor: pendingAnchor, t: pendingAnchorT, kind: pendingAnchorKind };   // this pass's landing attempt, for diagnostics
  if (att.anchor || att.t != null) landTrail = [];
  let scrolled = pendingAnchor ? scrollToAnchor(pendingAnchor) : false;
  // BY-ID landing ONLY (the user 2026-06-20 — "shrink the 29%, then remove the time fallback"). TIER 1, by id:
  // a card TITLE / node text sends promptAnchorUuid, which lands the originating MESSAGE — a user turn OR a
  // peer's postal card (scrollToAnchor's kind guard now accepts both). That covers the ~71% of cards that
  // resolve PLUS the peer-opener slice the guard used to refuse into the time fallback. The rest mint from an
  // autonomous/continuation segment (no opener) or a turn pruned/compacted off the active path — genuinely
  // unanchorable — so they honest-fail with a toast rather than a clock-nearest guess (which often landed on
  // an unrelated turn anyway — the 'retry'-message bug). The old time tier-2 (scrollToNearestT) is GONE: the
  // last time-based navigation removed, per "no time heuristics". WORK/REPLY intent never had a tier-2 either.
  pendingAnchor = null; pendingAnchorIntent = null; pendingAnchorT = null; pendingAnchorKind = null;
  // Diagnostics: log every landing attempt; a deep-link that couldn't resolve announces itself loudly
  // instead of impersonating a successful jump.
  if (att.anchor || att.t != null) {
    vscodeApi?.postMessage({
      type: "locateDiag", id: activeId, ok: scrolled, trail: landTrail.slice(),
      anchor: att.anchor ?? undefined, anchorT: att.t ?? undefined, kind: att.kind ?? undefined,
    });
    if (!scrolled && !anchorPendingOlder) landToast("couldn't locate this in the transcript");  // fetching older history → chatHead re-lands, no false "couldn't locate"
  }
  if (!scrolled) {
    if (!v.shown || v.stick) content.scrollTop = content.scrollHeight;
    else content.scrollTop = v.scrollTop;
  }
  v.shown = true;
  scheduleRestamp();
}

// Live tail-append to the ACTIVE view. At the bottom → follow it. Scrolled UP reading → keep the viewport
// exactly where it is: a new message must NOT move what you're looking at (the user 2026-06-25 — incoming
// messages were jumping the view "backwards"; the compact path FULL-REBUILDS on append, clearing the DOM and
// resetting scrollTop). Pass atBottom=false so the rebuild keeps winStart (content above the viewport
// unchanged), then restore the exact scrollTop.
function appendActive() {
  const content = document.getElementById("content");
  if (!content || !activeId) { showActive(); return; }
  const stick = nearBottom(content);
  const before = content.scrollTop;
  syncView(activeId, stick);
  updateStatusline();
  if (stick) content.scrollTop = content.scrollHeight;
  else content.scrollTop = before;   // appended content is BELOW the viewport → its position is unchanged
  scheduleRestamp();
}

// Row heights change when the pane is resized (text re-wraps), so the spacing-based
// stamps must be recomputed against the new layout.
window.addEventListener("resize", scheduleRestamp);
// Reposition/repaint the overview ruler whenever the window OR the #content box changes (the ledger or
// live-ask strip showing, the composer growing, a resize) — the ruler maps content-space → ruler-space, so
// a plain scroll needs no repaint, but its viewport box and scrollHeight can move under it (link_audit's #4).
window.addEventListener("resize", paintGlowRuler);
if (typeof ResizeObserver === "function") {
  const ro = new ResizeObserver(() => paintGlowRuler());
  const c = document.getElementById("content");
  if (c) ro.observe(c);
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
      scheduleRestamp();
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
// matches the feed and the terminal `romp -f`. The bullet TEXT stays at default brightness; only the
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
// Recency for the ledger: take the shared hawaii ramp color (same hue progression
// as the terminal `romp -f` feed) but remap its LIGHTNESS into a legible band so
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

// FLIP the collapsed goal text into its pinned row, then fade the title + the other rows in AROUND it
// (the user 2026-06-18) — so the compact line visibly settles where it belongs. Fully guarded: any missing
// piece, no movement, or prefers-reduced-motion → it just shows, no animation.
function morphLedgerExpand(host: HTMLElement, wrap: HTMLElement, from: { left: number; top: number }) {
  if (typeof requestAnimationFrame !== "function") return;
  try { if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) return; } catch { /* ignore */ }
  const curRow = wrap.querySelector(".ledger-tnode.ledger-curtop") as HTMLElement | null;
  const curText = curRow?.querySelector(".ledger-ttext") as HTMLElement | null;
  if (!curText || !curText.getBoundingClientRect) return;
  const to = curText.getBoundingClientRect();
  const dx = from.left - to.left, dy = from.top - to.top;
  if (!dx && !dy) return;
  // hide EVERYTHING except the gliding text — including the curTop row's OWN checkbox / time / caret, so
  // nothing but the text moves until it's home; it ALL fades in only after the morph lands (the user 2026-06-18).
  const fade: HTMLElement[] = [];
  const sumEl = host.querySelector(".ledger-summary") as HTMLElement | null;
  if (sumEl) fade.push(sumEl);
  wrap.querySelectorAll(".ledger-tnode").forEach((r) => {
    const row = r as HTMLElement;
    if (row !== curRow) { fade.push(row); return; }
    Array.from(row.children).forEach((c) => { if (c !== curText) fade.push(c as HTMLElement); });   // curRow's mark/time/caret
  });
  fade.forEach((e) => { e.style.opacity = "0"; });
  // The collapsed line sits ABOVE the scroll-clipped tree, so animating the real text would either get
  // clipped mid-flight OR (if we un-clip the tree) flash the whole uncropped list — the bug the user hit.
  // Instead glide a CLONE in a non-clipped fixed layer; the real text stays hidden in its (still-cropped)
  // row and is revealed the instant the clone lands (the user 2026-06-18).
  const clone = curText.cloneNode(true) as HTMLElement;
  let cs: CSSStyleDeclaration | null = null;
  try { cs = (typeof getComputedStyle === "function") ? getComputedStyle(curText) : null; } catch { /* ignore */ }
  clone.style.cssText = "";
  clone.style.position = "fixed"; clone.style.left = to.left + "px"; clone.style.top = to.top + "px";
  clone.style.width = to.width + "px"; clone.style.margin = "0"; clone.style.zIndex = "9999"; clone.style.pointerEvents = "none";
  if (cs) { clone.style.font = cs.font; clone.style.color = cs.color; clone.style.fontWeight = cs.fontWeight; clone.style.letterSpacing = cs.letterSpacing; }
  clone.style.transformOrigin = "left top";
  clone.style.transition = "none";
  clone.style.transform = `translate(${dx}px, ${dy}px)`;   // INVERT: start at the collapsed line's spot
  document.body.appendChild(clone);   // body = never scroll-clipped
  curText.style.opacity = "0";        // hide the real text while the clone flies
  void clone.offsetWidth;             // commit the FROM transform
  // PLAY: glide to the real slot; the rest fades in the INSTANT it lands (delay == glide duration, no pause)
  clone.style.transition = "transform 0.45s cubic-bezier(0.22, 0.61, 0.36, 1)";
  clone.style.transform = "translate(0px, 0px)";
  fade.forEach((e) => { e.style.transition = "opacity 0.2s ease 0.45s"; e.style.opacity = ""; });
  setTimeout(() => { clone.remove(); curText.style.opacity = ""; }, 470);   // swap clone → real text when it lands
  setTimeout(() => { fade.forEach((e) => { e.style.transition = ""; e.style.opacity = ""; }); }, 720);
}

// Reverse of the above (the user 2026-06-18): on COLLAPSE the tree is gone, so just GLIDE the now-compact
// summary line UP from where the curTop row's text sat in the expanded tree — the goal text "collects back"
// into the collapsed line. The summary lives in the head (not the scroll-clipped tree), so no overflow tweak.
function morphLedgerCollapse(sumEl: HTMLElement, from: { left: number; top: number }) {
  if (typeof requestAnimationFrame !== "function") return;
  try { if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) return; } catch { /* ignore */ }
  if (!sumEl.getBoundingClientRect) return;
  const to = sumEl.getBoundingClientRect();
  const dx = from.left - to.left, dy = from.top - to.top;
  if (!dx && !dy) return;
  sumEl.style.transition = "none";
  sumEl.style.transformOrigin = "left top";
  sumEl.style.transform = `translate(${dx}px, ${dy}px)`;
  void sumEl.offsetWidth;
  sumEl.style.transition = "transform 0.42s cubic-bezier(0.22, 0.61, 0.36, 1)";
  sumEl.style.transform = "translate(0px, 0px)";
  setTimeout(() => { for (const p of ["transition", "transform", "transformOrigin"]) (sumEl.style as any)[p] = ""; }, 600);
}

// (Relevance categorization — colored labels + filter checkboxes — was removed
// from the ledger per the user; that lives in the FEED panel now. The ledger keeps
// the plain newest-first bullets, no "·", live-refresh.)
// A small dim section header inside the overview (goals / working on / done).
function ledgerLabel(text: string): HTMLElement {
  const lab = el("div", "ledger-label");
  lab.textContent = text;
  return lab;
}

// The ONE goal the collapsed ledger shows: the depth-0 root on the active path (its subtree holds the
// `current` node), else the freshest unfinished root, else the first root. Shared by renderLedger (the
// collapsed line + the pinned-on-expand row) and refreshLedgerAges (ticking the collapsed line's time).
function currentTopGoal(tree: LedgerTreeNode[]): LedgerTreeNode | null {
  const roots0 = tree.filter((n) => n.depth === 0);
  return roots0.find((r) => r.current || r.onpath)
    || roots0.filter((r) => !r.done).sort((a, b) => ((b.mt ?? b.t) || 0) - ((a.mt ?? a.t) || 0))[0]
    || roots0[0] || null;
}

// The recency a node DISPLAYS / sorts by: its subtree-rolled-up freshness (_rec, stamped below), falling
// back to its own mt/t before the stamp exists. So a parent reflects the most recent activity ANYWHERE in
// its subtree, not just its own last-touch (the user 2026-06-22).
function nodeRecency(n: LedgerTreeNode): number { return (n._rec ?? n.mt ?? n.t) || 0; }

// Roll the freshest activity up to every node: _rec = max(own mt/t, every descendant's, and — for the live
// CURRENT node — the working timer cur.t). The bug it fixes: a top goal whose deep child was active 12m ago
// read "(2d ago)" because the label used the node's OWN mt while the ordering already used subtree-max — so
// the freshest row sorted to the top yet showed a stale time (the user 2026-06-22). All timestamps, so it's
// clock-invariant: stamped once per full render, reused by refreshLedgerAges's same-content ticks.
function stampSubtreeRecency(tree: LedgerTreeNode[], cur: LedgerBullet | null): void {
  const byId = new Map(tree.map((n) => [n.id, n] as const));
  const eff = (n: LedgerTreeNode) => (n.current && cur && cur.t) ? Math.max(cur.t, (n.mt ?? n.t) || 0) : ((n.mt ?? n.t) || 0);
  const inflight = new Set<string>();
  const calc = (n: LedgerTreeNode): number => {
    if (n._rec != null) return n._rec;
    if (inflight.has(n.id)) return eff(n);   // cycle guard (a malformed graph can't hang the render)
    inflight.add(n.id);
    let r = eff(n);
    for (const cid of n.children || []) { const c = byId.get(cid); if (c) r = Math.max(r, calc(c)); }
    n._rec = r;
    return r;
  };
  for (const n of tree) n._rec = undefined;   // fresh stamp each full render (a new payload reuses no objects, but be safe)
  for (const n of tree) calc(n);
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
  const car = el("span", "bg-caret"); car.textContent = open ? "▾" : "▸"; head.appendChild(car);
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

// A tree node's right-side time: the CURRENT node shows its live elapsed "(Xm)" (how long it's been
// worked on, from the in-progress turn's start); a DONE node shows when it finished "(Xm ago)". Both
// recency-tinted. Open non-current nodes show nothing. Factored out so refreshLedgerAges ticks it too.
function setTnodeTime(time: HTMLElement, n: LedgerTreeNode, cur: LedgerBullet | null, now: number) {
  if (n.current && cur && cur.t) {
    time.textContent = `(${agehms(now - cur.t)})`; time.style.color = ageColorReadable(now - cur.t);
  } else if (n.done && nodeRecency(n)) {
    // a finished task's "(Xm ago)" is time since the freshest activity in ITS SUBTREE (rolled-up recency,
    // the user 2026-06-22) — so a done parent reflects deep child work, not just its own resolution mt.
    const dt = nodeRecency(n);
    time.textContent = `(${agehms(now - dt)} ago)`; time.style.color = ageColorReadable(now - dt);
  } else {
    time.textContent = "";
  }
}

// Same-content tick: refresh the existing bullets' "Xm ago" ages + recency colors
// in place (rows kept alive so a hover/click survives). Order matches bullets[0..8).
function refreshLedgerAges(host: HTMLElement, l: Ledger, now: number) {
  const tree = l.tree || [];
  const bullets = l.bullets || [];
  // title hue tracks the freshest activity across the whole overview
  const newestT = Math.max(l.current && l.current.t ? l.current.t : 0,
    ...tree.map((n) => n.t || 0), ...bullets.map((b) => b.t || 0));
  const sum = host.querySelector(".ledger-summary") as HTMLElement | null;
  if (sum && newestT) sum.style.color = ageColorReadable(now - newestT);
  // the collapsed line's "(Xm)" time ticks with the clock too (present only while collapsed)
  const ctime = sum ? (sum.querySelector(".ledger-summary-time") as HTMLElement | null) : null;
  if (ctime) { const ct = currentTopGoal(tree); if (ct) setTnodeTime(ctime, ct, l.current || null, now); }
  // tree node times ("(Xm)" live current + "(Xm ago)" done) tick with the wall clock
  host.querySelectorAll(".ledger-tnode").forEach((row, i) => {
    const n = tree[i]; const time = row.querySelector(".ledger-ttime") as HTMLElement | null;
    if (n && time) setTnodeTime(time, n, l.current || null, now);
    // keep a done item's text colour in step with its (recency-tinted) time as the clock ticks
    const txt = row.querySelector(".ledger-ttext") as HTMLElement | null;
    if (n && txt && n.done && nodeRecency(n)) txt.style.color = ageColorReadable(now - nodeRecency(n));
  });
  // fallback bullets (goal-less sessions)
  const bs = bullets.slice(0, LEDGER_BULLET_CAP);
  host.querySelectorAll(".ledger-bullet").forEach((row, i) => {
    const b = bs[i]; if (!b) return;
    const col = b.t ? ageColorReadable(now - b.t) : "";
    const age = row.querySelector(".ledger-bullet-age") as HTMLElement | null;
    const txt = row.querySelector(".ledger-bullet-text") as HTMLElement | null;
    if (age) { age.textContent = b.t ? `${agehms(now - b.t)} ago` : ""; if (col) age.style.color = col; }
    if (txt && col) txt.style.color = col;
  });
}

// Wire a ledger bullet exactly like a goal-tree zone: hover (120ms intent debounce)
// → transient timeline highlight of the bullet's event; leave → clear; click →
// land on the bullet's turn IN THE CHAT by uuid. b.id is the turn's atom uuid
// (build_session), a real .turn[data-uuid] — so scroll to it directly, no host
// round-trip. (The old `ledgerLocate` host message was never handled — a dead
// click — and would have been time-based anyway; the user 2026-06-19.)
function wireBulletNav(row: HTMLElement, b: LedgerBullet) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  row.title = b.id ? "jump to this message" : "jump to this on the timeline";
  row.addEventListener("mouseenter", () => {
    timer = setTimeout(() => { timer = undefined; vscodeApi?.postMessage({ type: "ledgerHover", id: b.id, tlId: b.tlId }); }, 120);
  });
  row.addEventListener("mouseleave", () => {
    if (timer) { clearTimeout(timer); timer = undefined; }
    vscodeApi?.postMessage({ type: "ledgerHover", id: null });
  });
  row.addEventListener("click", () => {
    if (!b.id) return;
    pendingAnchorIntent = null;                 // a bullet has no kind intent (captioned turn, user or assistant)
    if (!scrollToAnchor(b.id) && !anchorPendingOlder) landToast("couldn't locate this in the transcript");  // fetching older → re-lands on chatHead
  });
}

// ---- live "awaiting your input" widgets (structured: radio / checkbox / submit / text) ----

function setLiveAsk(id: string, ask: ParsedAsk | null) {
  liveAsks.set(id, ask);
  if (id === activeId) renderLiveAsk();
}
function clearLiveAsk(id: string) {
  if (liveAsks.delete(id) && id === activeId) renderLiveAsk();
}

let sendingTimer: ReturnType<typeof setTimeout> | undefined;
// Local UI highlight for the single-select card (↑/↓); the actual selection is the
// delta send on confirm. Keyed so incidental re-posts of the same prompt keep it.
let liveAskFocus = 0;
let liveAskFocusKey = "";

// Render the widget matching the active session's pending prompt. It lives at the BOTTOM of the transcript
// (the last child of #content) and scrolls WITH the chat history (the user 2026-06-27) — so a tall picker
// never buries the context above it; scroll up and the question's context is still right there. It still takes
// over the message box (the composer hides). single → radio rows, multi → checkboxes + Submit/Cancel,
// submit → review + action buttons, null → free-text input.
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
  if (!activeId || !liveAsks.has(activeId)) {
    host.style.display = "none";
    liveTextValue = "";
    if (footer) footer.style.display = ""; // restore the message box
    return;
  }
  host.style.display = "";
  if (footer) footer.style.display = "none"; // the prompt takes over the message box
  const ask = liveAsks.get(activeId) ?? null;
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
  if (ask.options.some((o) => isTypeSomething(o.label))) {
    const row = el("div", "ask-custom");
    const plus = el("span", "ask-custom-plus"); plus.textContent = "+"; row.appendChild(plus);
    const inp = document.createElement("input");
    inp.type = "text"; inp.className = "ask-custom-input"; inp.placeholder = "add your own answer…";
    inp.value = liveTextValue;
    inp.addEventListener("input", () => { liveTextValue = inp.value; });
    // stop ALL keys from bubbling to onSingleKey (digits would jump-confirm rows)
    inp.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); const v = inp.value.trim(); if (v) addCustomLiveAsk(v); }
    });
    wirePasteFallback(inp);
    row.appendChild(inp);
    card.appendChild(row);
  }
  card.tabIndex = 0;
  card.addEventListener("keydown", onSingleKey);
  card.focus({ preventScroll: true });
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
  // Inline custom-answer field — only while the TUI still offers a Type-something slot.
  if (ask.options.some((o) => isTypeSomething(o.label))) {
    const row = el("div", "ask-custom");
    const plus = el("span", "ask-custom-plus"); plus.textContent = "+"; row.appendChild(plus);
    const inp = document.createElement("input");
    inp.type = "text"; inp.className = "ask-custom-input"; inp.placeholder = "add your own answer…";
    inp.value = liveTextValue;
    inp.addEventListener("input", () => { liveTextValue = inp.value; });
    // stop card-level arrow/Space/Enter nav from firing while typing a custom answer
    inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); const v = inp.value.trim(); if (v) addCustomLiveAsk(v); } });
    wirePasteFallback(inp);
    row.appendChild(inp);
    card.appendChild(row);
  }
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
  card.focus({ preventScroll: true });
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
  const input = document.createElement("input");
  input.type = "text"; input.className = "ask-text-input"; input.placeholder = "…or, if it’s a text prompt, type here + Enter";
  input.value = liveTextValue;
  input.addEventListener("input", () => { liveTextValue = input.value; });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); const v = input.value.trim(); if (v) sendTextLiveAsk(v); }
  });
  wirePasteFallback(input);
  card.appendChild(input);
  const len = input.value.length; try { input.setSelectionRange(len, len); } catch { /* ignore */ }
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
type MetaKind = "mode" | "model" | "effort";
const MODEL_CHOICES: { label: string; value: string }[] = [
  { label: "Fable", value: "fable" },
  { label: "Opus", value: "opus" },
  { label: "Sonnet", value: "sonnet" },
  { label: "Haiku", value: "haiku" },
  { label: "Default", value: "default" },
];
const EFFORT_CHOICES: { label: string; value: string }[] =
  ["low", "medium", "high", "xhigh", "max"].map((v) => ({ label: v, value: v }));
// Permission mode: the shift+tab cycle (no slash command), so the picker offers the three cycle modes;
// the host sets them by sending shift+tab the right number of times (the user 2026-06-16).
const MODE_CHOICES: { label: string; value: string }[] = [
  { label: "Normal", value: "default" },
  { label: "Accept edits", value: "acceptEdits" },
  { label: "Auto", value: "auto" },
  { label: "Plan", value: "plan" },
];
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
  mode: MODE_CHOICES, model: MODEL_CHOICES, effort: EFFORT_CHOICES,
};
// the live value of a meta kind for the active session
function metaCurrent(kind: MetaKind, st: Status): string {
  return (kind === "model" ? st.model : kind === "effort" ? st.effort : st.mode) || "";
}

// Is this menu entry the session's current value? Effort matches exactly; the
// model var holds a display name ("Opus 4.8"), so match on the leading word.
function isCurrentMeta(kind: MetaKind, st: Status, value: string): boolean {
  if (kind === "effort") return (st.effort || "").toLowerCase() === value;
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
    : "change permission mode (shift+tab cycle)";
  btn.addEventListener("click", (e) => { e.stopPropagation(); toggleMetaMenu(kind, btn); });
  return btn;
}

// Build or refresh the model/effort buttons inside #spinner-meta. Called from
// updateStatusline (fresh container) and the 1s ticker (label refresh in place).
function syncMetaControls(meta: HTMLElement, st: Status) {
  // order left→right: mode · model · effort — the mode selector sits LEFT of the model name (the user 2026-06-16)
  const want = [st.mode ? "mode" : "", st.model ? "model" : "", st.effort ? "effort" : ""].filter(Boolean).join();
  const btns = Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[];
  if (btns.map((b) => b.dataset.kind).join() !== want) {
    meta.replaceChildren();
    if (st.mode) meta.appendChild(metaButton("mode", prettyMode(st.mode)));
    if (st.model) meta.appendChild(metaButton("model", st.model));
    if (st.effort) meta.appendChild(metaButton("effort", st.effort));
  }
  for (const b of Array.from(meta.querySelectorAll(".meta-btn")) as HTMLElement[]) {
    const kind = b.dataset.kind as MetaKind;
    const disp = kind === "mode" ? prettyMode(st.mode) : metaCurrent(kind, st);
    const label = b.querySelector(".meta-label") as HTMLElement | null;
    if (label && label.textContent !== disp) label.textContent = disp;
    b.classList.toggle("meta-pending", isMetaPending(kind, st));
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
        vscodeApi.postMessage({ type: kind === "model" ? "setModel" : kind === "effort" ? "setEffort" : "setMode", id: activeId, value: c.value });
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
    return;
  }
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
  working: "WORKING", ready: "READY", awaiting: "BLOCKED",
  idle: "IDLE", closed: "CLOSED", compacting: "COMPACTING", blocked: "API ERROR",
  retrying: "API retrying…",   // a live session stalled on an API rate-limit/overload auto-retry (api 2026-06-23)
};

// A stop/interrupt button that lives beside the state badge in the statusline (the user 2026-06-19):
// it sends the SAME interrupt the composer's Ctrl+C does (host → Esc into the pane) — a less fiddly way
// to halt a run than Ctrl+C in this surface. It ONLY renders while the session is busy (working/
// compacting) — there's nothing to interrupt otherwise, so it's not drawn at all (the user 2026-06-19);
// updateStatusline omits it in every idle state. A neutral white square; hovering reveals the red stop tint.
function stopButton(): HTMLElement {
  const btn = el("button", "stop-btn");
  (btn as HTMLButtonElement).type = "button";
  btn.title = "Stop — interrupt this session (same as Ctrl+C)";
  btn.setAttribute("aria-label", "Interrupt session");
  btn.appendChild(el("span", "stop-icon"));   // a filled square (CSS), the universal stop glyph
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!activeId || !vscodeApi) return;
    vscodeApi.postMessage({ type: "interrupt", id: activeId });
    btn.classList.add("stop-flash");
    window.setTimeout(() => btn.classList.remove("stop-flash"), 400);
  });
  return btn;
}

function updateStatusline() {
  const sl = document.getElementById("statusline");
  const s = activeId ? sessions.get(activeId) : null;
  if (!sl || !s) return;
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
  } else if (s.status.state === "compacting") {
    const c = el("span", "compacting-line");
    c.textContent = "⟳ Compacting context…";
    sl.appendChild(c);
  } else {
    const chip = el("span", `chip chip-${s.status.state}`);
    chip.textContent = CHIP_LABEL[s.status.state] ?? s.status.state.toUpperCase();
    sl.appendChild(chip);
  }
  // stop/interrupt button, right beside the state badge — ONLY while busy (working/compacting); omitted
  // entirely in idle states (there's nothing to interrupt) — the user 2026-06-19.
  if (s.status.state === "working" || s.status.state === "compacting") sl.appendChild(stopButton());
  // The session's working directory (fixed at creation), leading the right-side cluster — just left of the
  // mode/model/effort controls (the user 2026-06-23). Basename only; full path on hover. It carries the
  // right-justify margin so it anchors the cluster; empty (rare, no cwd) it's a zero-width spacer.
  const dir = el("span", "status-dir");
  if (s.cwd) {
    dir.textContent = "📁 " + (s.cwd.replace(/\/+$/, "").split("/").pop() || s.cwd);
    // Click → run the configured folder opener for this dir (default: the OS opener — Finder / xdg-open —
    // overridable via ROMP_OPEN_FOLDER or ~/.config/romp/open-folder, e.g. open in Ghostty). asFolderLink wires
    // the data-act caught by the document-level openFolder delegate, so the per-push rebuild can't drop it.
    asFolderLink(dir, s.cwd);
  }
  sl.appendChild(dir);
  // The session's git branch, just right of the dir — only when known and only if the user hasn't hidden it
  // (Settings → "Show git branch", on by default — the user 2026-06-23). Pulled from the system-context event.
  if (loadSettings().showBranch !== false) {
    const sys = s.events.find((e) => e.kind === "system") as Extract<ChatEvent, { kind: "system" }> | undefined;
    if (sys?.gitBranch) {
      const br = el("span", "status-branch");
      br.textContent = "⎇ " + sys.gitBranch;
      br.title = "git branch: " + sys.gitBranch;
      sl.appendChild(br);
    }
  }
  const meta = el("span", "spinner-meta");
  meta.id = "spinner-meta";
  syncMetaControls(meta, s.status);
  sl.appendChild(meta);
  const bar = ctxBar();
  setCtxBar(bar, s.status.ctx, s.status.state === "compacting", s.status.ctxColor);
  sl.appendChild(bar);
}

// Unsent composer text, per session — a draft belongs to the tab it was typed
// in: switching away stashes it (the box empties for the new tab's own draft),
// switching back restores it.
const drafts = new Map<string, string>();

// Persist drafts across a full RELOAD (the user 2026-06-25: a half-typed message must survive a refresh, not
// only a tab switch). The Map is in-memory, so mirror it into the webview's persisted state — the same store
// that remembers the active tab — and reload it at startup. restoreActiveDraftOnce() drops the active tab's
// draft back into the box ONE time after load, and only when the box is empty, so it never clobbers live typing.
function persistDrafts(): void {
  try { vscodeApi?.setState?.({ ...(vscodeApi.getState?.() || {}), drafts: Object.fromEntries(drafts) }); } catch { /* ignore */ }
}
try {
  const saved = ((vscodeApi?.getState?.() || {}) as any).drafts;
  if (saved && typeof saved === "object") for (const [k, v] of Object.entries(saved)) if (typeof v === "string") drafts.set(k, v);
} catch { /* ignore */ }
let draftsRestored = false;
function restoreActiveDraftOnce(): void {
  if (draftsRestored) return;
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta || !activeId) return;            // wait until the active tab is established after load
  draftsRestored = true;
  if (!ta.value) { const d = drafts.get(activeId); if (d) { ta.value = d; growComposer(ta); } }
}

function setActive(id: string, anchor?: string, anchorT?: number, anchorKind?: string) {
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
    }
    ta.value = drafts.get(id) ?? "";
    growComposer(ta);
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

function upsert(msg: any) {
  const existed = sessions.has(msg.id);
  const prev = sessions.get(msg.id);
  const s: Session = {
    id: msg.id,
    name: msg.name,
    color: msg.color || null,
    events: msg.events || (prev ? prev.events : []),
    status: msg.status || (prev ? prev.status : { state: "idle", sinceEpoch: null }),
    firstSeen: msg.firstSeen ?? (prev ? prev.firstSeen : undefined),
    cwd: msg.cwd ?? (prev ? prev.cwd : ""),
    // A trimmed full send carries headFrom/headTotal; a whole-transcript send omits them (headFrom 0).
    headFrom: msg.headFrom ?? 0,
    headTotal: msg.headTotal ?? ((msg.events || (prev ? prev.events : [])).length),
    bgTasks: ("bgTasks" in msg) ? msg.bgTasks : (prev ? prev.bgTasks : undefined),
    hideFromFeed: ("hideFromFeed" in msg) ? !!msg.hideFromFeed : (prev ? prev.hideFromFeed : undefined),
    postalServiceOff: ("postalServiceOff" in msg) ? !!msg.postalServiceOff : (prev ? prev.postalServiceOff : undefined),
  };
  sessions.set(msg.id, s);
  // The kernel re-sends the FULL "session" payload on every push. Distinguish an APPEND (more turns
  // on the SAME transcript — the common case) from a FORK (the tab re-pointed onto a NEW transcript,
  // events replaced wholesale, e.g. a /clear-style fork). Only a FORK drops the cached DOM and
  // rebuilds; an append lets syncView add just the new turns AND keeps the user's scroll position —
  // so new content no longer snaps the view to the bottom (the user 2026-06-15). Fork = the
  // transcript identity (first event's uuid) changed; an append keeps it.
  const forked = !!(existed && msg.events && prev && prev.events.length && msg.events.length
                    && firstUuid(msg.events) !== firstUuid(prev.events));
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
  if (msg.id === activeId) { if (existed && !forked) appendActive(); else showActive(); renderBgTasks(); }
  // A non-active session's view is left to sync lazily when it's next shown.
  // The session the user just created has arrived → drop the "Opening…" cue and
  // focus its fresh tab (the whole point of opening it).
  if (!existed && pendingNewSession && msg.name === pendingNewSession) {
    hideOpeningModal();
    setActive(msg.id);
  }
  schedulePrebuild(); // startup + new content: build the off-screen tabs in idle so they open instantly
}

function update(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) return;
  s.events = msg.events || s.events;
  s.status = msg.status || s.status;
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
function chatTail(msg: any) {
  const s = sessions.get(msg.id);
  if (!s) return;                                  // no base yet → ignore; a full session must arrive first
  // msg.from is a GLOBAL transcript index; the resident events are the tail [headFrom, …) → map to local.
  const from = (msg.from | 0) - (s.headFrom || 0);
  if (from < 0 || from > s.events.length) return;  // below the loaded head, or a gap → wait for the next full
  s.events.length = from;                          // drop the (now superseded) tail...
  for (const e of (msg.events || [])) s.events.push(e);   // ...and append the freshly-changed suffix
  if (typeof msg.total === "number") s.headTotal = msg.total;
  if (msg.status) s.status = msg.status;
  if ("ledger" in msg) ledgers.set(msg.id, msg.ledger ?? null);
  renderTabs();
  if (msg.id === activeId) {
    const v = views.get(msg.id);
    if (v) v.rendered = Math.min(v.rendered, from);   // repaint from the exact changed point (catches a tool fill)
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
function chatHead(msg: any) {
  loadingOlder.delete(msg.id);
  hideLoadingPill();
  const s = sessions.get(msg.id);
  if (!s) { pendingOlderAnchor.delete(msg.id); return; }
  const before = msg.before | 0, from = msg.from | 0;
  if (before !== (s.headFrom ?? 0)) { pendingOlderAnchor.delete(msg.id); return; }   // stale / overlapping → ignore
  const older = (msg.events || []) as ChatEvent[];
  if (older.length) s.events = older.concat(s.events);
  s.headFrom = from;
  const v = views.get(msg.id);
  if (msg.id !== activeId) { pendingOlderAnchor.delete(msg.id); if (v) v.stale = true; return; }
  // re-anchor: reset the active view so it re-windows around the saved row (now further down s.events), and
  // land on it (deep-link path) — the prepended older content sits above, off-screen, ready to scroll into.
  if (v) { v.rendered = 0; v.winStart = 0; v.winEnd = 0; v.avgTurnH = undefined; v.spacerCount = undefined; v.spacerCountBot = undefined; v.unitTotal = undefined; }
  const anchorUuid = pendingOlderAnchor.get(msg.id);
  pendingOlderAnchor.delete(msg.id);
  if (anchorUuid) { pendingAnchor = anchorUuid; pendingAnchorIntent = null; pendingAnchorT = null; pendingAnchorKind = null; }
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
  loadingOlder.add(sid);
  showLoadingPill();
  vscodeApi?.postMessage({ type: "loadOlder", id: sid, before: s.headFrom });
  return true;
}

// Ask the kernel for the chunk of history just before the resident tail. Anchors on the current top row so
// chatHead can land the user back on it after prepending.
function requestOlder(sid: string, v: View, _content: HTMLElement): void {
  const s = sessions.get(sid);
  if (!s || (s.headFrom ?? 0) <= 0 || loadingOlder.has(sid)) return;
  const firstTurn = v.el.querySelector(".turn[data-uuid]") as HTMLElement | null;
  const anchor = firstTurn?.dataset.uuid || (s.events[0] as { uuid?: string } | undefined)?.uuid;
  if (anchor) pendingOlderAnchor.set(sid, anchor);
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

// Drop a session from the panel + reselect another tab, NOW (the user 2026-06-24): used both by the kernel's
// `closed` event AND optimistically the instant you Close tab / End session — otherwise the reselect waited on
// that round-trip while the tab bar already updated, leaving you on the CLOSED session's stale content.
function dismissSession(id: string): void {
  sessions.delete(id);
  liveAsks.delete(id);
  ledgers.delete(id);
  drafts.delete(id); persistDrafts();
  const v = views.get(id);
  if (v) { v.el.remove(); views.delete(id); }
  const oi = order.indexOf(id); if (oi >= 0) order.splice(oi, 1);
  const mi = mru.indexOf(id); if (mi >= 0) mru.splice(mi, 1);
  renderTabs();                          // tab removed from `order` above → repaint without it
  if (activeId === id) {
    activeId = mru[0] || null; // MRU: return to the previously-active tab, not the positional neighbor
    const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
    if (ta) { ta.value = (activeId && drafts.get(activeId)) || ""; growComposer(ta); }
    showActive();
  }
}

window.addEventListener("message", (e: MessageEvent) => {
  const m = e.data;
  if (!m) return;
  if (m.type === "session") upsert(m);
  else if (m.type === "chatTail") chatTail(m);
  else if (m.type === "chatHead") chatHead(m);
  else if (m.type === "update") update(m);
  else if (m.type === "status") statusOnly(m);
  else if (m.type === "focus") setActive(m.id, m.anchor, typeof m.anchorT === "number" ? m.anchorT : undefined, typeof m.anchorKind === "string" ? m.anchorKind : undefined);
  else if (m.type === "nextTab") cycleTab(1);
  else if (m.type === "prevTab") cycleTab(-1);
  else if (m.type === "sessionList") { if (typeof m.defaultDir === "string") kernelDefaultDir = m.defaultDir; renderPicker(m.items || []); }
  else if (m.type === "browseResult" && typeof m.path === "string") {   // native Browse dialog returned a folder
    if (m.target === "gear") {                                          // the gear's "Default directory" Browse
      const gd = document.getElementById("rs-defaultdir") as HTMLInputElement | null;
      if (gd) { gd.value = m.path; gd.dispatchEvent(new Event("change")); }   // fire the gear's change → persist kernel-side
    } else {
      const di = document.getElementById("picker-dir") as HTMLInputElement | null;
      if (di) { di.value = m.path; di.focus(); }
    }
  }
  else if (m.type === "openPicker") openPicker(!!m.pick, m.prompt, !!m.allowNew);
  // The host asks US to confirm (in-page, no native dialogs): ending a live
  // session on tab-close, and reviving a dead one on open.
  else if (m.type === "confirmClose" && m.id) {
    const nm = String(m.name || "");
    showConfirm(`End “${nm}”?`,
      "“Close tab” just removes it from this panel and leaves the session running. “End session” shuts it down (the transcript stays on disk).",
      [{ label: "Close tab", value: "close" }, { label: "End session", value: "end", danger: true }, { label: "Cancel", value: "" }],
      (v) => {
        if (v === "close") vscodeApi?.postMessage({ type: "closeTab", id: m.id });
        // End session = shut it down AND remove the tab (the user 2026-06-16: an explicitly-ended session
        // shouldn't linger as a struck-through read-only tab — that's only for sessions that die on their
        // own). closeTab must durably dismiss it so the death event doesn't re-add the struck tab.
        else if (v === "end") { vscodeApi?.postMessage({ type: "endSession", id: m.id }); vscodeApi?.postMessage({ type: "closeTab", id: m.id }); }
      });
  }
  else if (m.type === "confirmRevive" && m.id) {
    const nm = String(m.name || "");
    showConfirm(`“${nm}” is closed — revive it?`,
      "Revive restarts the session and resumes its conversation. Read-only just shows the transcript.",
      [{ label: "Revive", value: "revive" }, { label: "View read-only", value: "ro" }, { label: "Cancel", value: "" }],
      (v) => {
        if (v === "revive") vscodeApi?.postMessage({ type: "reviveSession", id: m.id });
        else if (v === "ro") vscodeApi?.postMessage({ type: "viewReadOnly", id: m.id });
      });
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
  else if (m.type === "droppedPath" && typeof m.path === "string") insertComposerText(m.path);
  else if (m.type === "closed") dismissSession(m.id);   // a session died on its own (or the kernel confirms our close)
});

// Tick the working timer (the chip color-pulse is pure CSS) and keep the model/ctx
// meta fresh as status updates land.
setInterval(() => {
  const s = activeId ? sessions.get(activeId) : null;
  if (!s) return;
  if (s.status.state === "working") {
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
function growComposer(ta: HTMLTextAreaElement) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
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
    const text = ta.value.trim();
    if (!text || !activeId) return;
    lastSent.set(activeId, text);   // remembered for a possible Ctrl+C restore
    if (vscodeApi) vscodeApi.postMessage({ type: "sendMessage", id: activeId, text });
    drafts.delete(activeId); persistDrafts();   // sent — no draft to restore on a later switch-back
    ta.value = "";
    ta.style.height = "";
  };
  // an explicit send button on the right of the box (touch devices have no easy ⏎; desktop gets a click
  // affordance too). mousedown, not click, so the textarea keeps focus and a follow-up keeps typing.
  const sendBtn = document.getElementById("composer-send") as HTMLButtonElement | null;
  sendBtn?.addEventListener("mousedown", (e) => { e.preventDefault(); sendComposer(); ta.focus(); });

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
  const loadCmds = (sid: string, then?: () => void) => {
    fetch("/commands?sid=" + encodeURIComponent(sid), { cache: "no-store" })
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
  const closeSlash = () => { if (pop) { pop.remove(); pop = null; } if (slashPoll) { clearTimeout(slashPoll); slashPoll = undefined; } };
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
      const row = document.createElement("div"); row.className = "slash-row" + (i === sel ? " sel" : "");
      const nm = document.createElement("span"); nm.className = "slash-name"; nm.textContent = "/" + c.name;
      if (c.argumentHint) { const a = document.createElement("span"); a.className = "slash-arg"; a.textContent = " " + c.argumentHint; nm.appendChild(a); }
      const ds = document.createElement("span"); ds.className = "slash-desc"; ds.textContent = c.description || "";
      row.append(nm, ds);
      row.addEventListener("mousedown", (ev) => { ev.preventDefault(); pickSlash(c); });   // mousedown keeps focus
      row.addEventListener("mousemove", () => { if (sel !== i) { sel = i; paintSlash(); } });
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
    if (e.key === "Escape") { e.preventDefault(); slashDismissed = true; closeSlash(); return true; }   // stays dismissed until the "/" is cleared
    return false;
  };
  ta.addEventListener("focus", () => { if (slashSid !== (activeId || "")) loadCmds(activeId || ""); });   // pre-warm the cache before "/"
  ta.addEventListener("blur", () => window.setTimeout(closeSlash, 120));   // close when leaving (a row's mousedown keeps focus, so it fires only on a real leave)
  window.addEventListener("resize", positionSlash);

  ta.addEventListener("keydown", (e) => {
    if (slashKey(e)) return;   // the slash menu owns ↑/↓/⏎/Tab/Esc while it's open
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
      // Escape leaves the chat box for "tab mode" — focus the active tab so ←/→ switch sessions (the user
      // 2026-06-25). Enter on a tab drops back in (onTabKey). Any draft text stays in the box, untouched.
      e.preventDefault();
      focusActiveTab();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
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
    if (activeId) { if (ta.value) drafts.set(activeId, ta.value); else drafts.delete(activeId); persistDrafts(); }
  });

  // Drag a file onto the box → insert its PATH at the cursor. NOTE: VS Code's
  // workbench drop overlay captures plain external file drags over any editor
  // group ("drop to open", which is why a bare drop opened the PNG) before the
  // webview sees them — hold SHIFT while dropping to suppress the overlay and
  // hand the drop here. Pasting (below) is overlay-free and covers the same
  // need. Best path source first: File.path (Electron, when exposed), then
  // text/uri-list file:// entries (explorer drags), else the bytes go to the
  // host, which saves them and posts the saved path back ("droppedPath") —
  // sandboxed webviews expose NO filesystem path for OS drags, only content.
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
    const uris = (dt.getData("text/uri-list") || "").split(/\r?\n/).filter((u) => u && !u.startsWith("#"));
    const fromUri = (u: string) => insertComposerText(decodeURIComponent(u.replace(/^file:\/\//, "")));
    const files = Array.from(dt.files || []);
    if (!files.length) { for (const u of uris) if (u.startsWith("file://")) fromUri(u); return; }
    files.forEach((f, i) => {
      const p = (f as any).path as string | undefined;
      if (p) { insertComposerText(p); return; }
      if (uris[i] && uris[i].startsWith("file://")) { fromUri(uris[i]); return; }
      shipFileToHost(f);
    });
  });

  // Cmd+V a copied file (Finder "Copy") or a clipboard screenshot → insert its
  // path, same pipeline as drops. Plain text pastes have no files on the
  // clipboard and keep the default behavior.
  ta.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (!files.length) return;
    e.preventDefault();
    files.forEach((f) => {
      const p = (f as any).path as string | undefined;
      if (p) insertComposerText(p);
      else shipFileToHost(f);
    });
  });
  wirePasteFallback(ta); // belt-and-braces: native paste disarms it, so no double-insert

  // The bulletproof path: 📎 asks the host to run a native open dialog (no
  // workbench drop overlay to fight) and the picked path comes back as
  // droppedPath → insertComposerText. Mousedown (not click) so the textarea
  // keeps focus and the path lands at the existing cursor.
  //
  // TOUCH devices (phone/tablet on the web dashboard) can't use the host dialog:
  // it pops on the DESKTOP running the kernel, not the phone. So 📎 instead opens
  // the phone's own photo picker (a hidden <input type=file accept=image/*>), and
  // the chosen image's bytes ship to the host (shipFileToHost → dropFile), which
  // saves them under ~/.local/state/romp/drops/ and posts the saved path back
  // (droppedPath) for insertion — a screenshot reaches the session with no
  // AirDrop/path gymnastics (the user 2026-06-17).
  const attach = document.getElementById("composer-attach") as HTMLButtonElement | null;
  const isTouch = () => window.matchMedia("(pointer:coarse)").matches;
  const filePicker = document.createElement("input");
  filePicker.type = "file";
  filePicker.accept = "image/*";
  filePicker.style.display = "none";
  filePicker.addEventListener("change", () => {
    Array.from(filePicker.files || []).forEach((f) => shipFileToHost(f));
    filePicker.value = ""; // let the same file be picked again
  });
  document.body.appendChild(filePicker);
  // touch: open the phone's photo picker — must fire from a real click gesture (iOS)
  attach?.addEventListener("click", (e) => { if (isTouch()) { e.preventDefault(); filePicker.click(); } });
  // desktop: native host dialog; mousedown keeps the textarea focused for cursor-position insert
  attach?.addEventListener("mousedown", (e) => {
    if (isTouch()) return;
    e.preventDefault();
    vscodeApi?.postMessage({ type: "pickFile" });
  });
}

// No filesystem path available for a dropped/pasted file → ship the bytes to
// the host, which saves them under ~/.local/state/romp/drops/ and posts back
// {type:"droppedPath", path} for insertion.
function shipFileToHost(f: File) {
  if (f.size > 50 * 1024 * 1024) return;   // too big to ship over postMessage
  const reader = new FileReader();
  reader.onload = () => {
    const b64 = String(reader.result || "").split(",")[1] || "";
    if (b64 && vscodeApi) vscodeApi.postMessage({ type: "dropFile", name: f.name || "pasted.png", b64 });
  };
  reader.readAsDataURL(f);
}

// Insert text into the composer at the cursor, with whitespace separation on
// both sides so a dropped path never fuses with surrounding words.
function insertComposerText(text: string) {
  const ta = document.getElementById("composer-input") as HTMLTextAreaElement | null;
  if (!ta || !text) return;
  const s = ta.selectionStart ?? ta.value.length, epos = ta.selectionEnd ?? ta.value.length;
  const before = ta.value.slice(0, s), after = ta.value.slice(epos);
  const sep = before && !/\s$/.test(before) ? " " : "";
  ta.value = before + sep + text + (after && !/^\s/.test(after) ? " " : "") + after;
  const pos = (before + sep + text).length;
  ta.selectionStart = ta.selectionEnd = Math.min(pos, ta.value.length);
  growComposer(ta);
  ta.focus();
}

// ---- settings: the gear + modal live on the TIMELINE now (the user 2026-06-14). The chat just
// CONSUMES the shared 'romp:settings' (compact mode) — applying a change made there, in a same-origin
// tab, live via the storage event; and reading it at startup. ----
function setupSettings(): void {
  onExternalSettingsChange((s) => { settings = s; rerenderAll(); });
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
  });
})();
(() => {
  // ONE openFolder delegate for the WHOLE chat (the user 2026-06-27): installed on document.body so EVERY place
  // that shows a local folder — the statusline 📁, the System-context "Directory" row, anywhere asFolderLink is
  // applied — opens that folder on click. Body is stable across every per-push rebuild, so a click is never
  // dropped mid-press. (Only elements carrying data-act="openFolder" are matched; nothing else is affected.)
  delegate(document.body, {
    openFolder: (el) => { const cwd = el.dataset.cwd; if (cwd && vscodeApi) vscodeApi.postMessage({ type: "openFolder", cwd }); },
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
      if (el.dataset.dead === "1") { vscodeApi.postMessage({ type: "closeTab", id }); return; }   // dead → just drop the read-only tab
      // LIVE session: show the End/Close confirm IMMEDIATELY, client-side — NOT via a closeSession→confirmClose
      // kernel round-trip, which made the ✕ feel unresponsive (and sometimes never opened the modal when the
      // kernel was busy). The dialog is static; the kernel doesn't need to decide it (the user 2026-06-24).
      const nm = sessions.get(id)?.name || "";
      showConfirm(`End “${nm}”?`,
        "“Close tab” just removes it from this panel and leaves the session running. “End session” shuts it down (the transcript stays on disk).",
        [{ label: "Close tab", value: "close" }, { label: "End session", value: "end", danger: true }, { label: "Cancel", value: "" }],
        (v) => {
          if (v !== "close" && v !== "end") return;   // Cancel → nothing
          if (v === "end") vscodeApi?.postMessage({ type: "endSession", id });
          vscodeApi?.postMessage({ type: "closeTab", id });
          dismissSession(id);   // drop the tab + reselect NOW (don't wait for the kernel's closed/push → no stale content)
        });
    },
  });
})();
// right-click a selection in the transcript → Reply (quote it) / Copy
document.getElementById("content")?.addEventListener("contextmenu", showSelectionMenu);
if (vscodeApi) vscodeApi.postMessage({ type: "ready" });
