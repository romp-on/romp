// The file viewer — a big modal over the CHAT pane (the user 2026-08-15: the first cut filled the
// FEED pane, and reading a file cost the cards; the click came out of the chat, so the file presents
// over the chat, ~95% of the pane behind a dimmed backdrop, ✕ top right — and the feed is never touched).
//
// Clicking a file path in the chat used to post `openFile`, which the kernel served by running an
// opener on ITS machine (the user 2026-08-08). Read the dashboard from another device — a laptop
// across the internet, a phone — and that is the wrong screen entirely; on a kernel with no desktop it
// did nothing at all, silently, which is how the user found it. The only place a file can actually be
// shown is the browser you are looking at, so the bytes come over the same `/file` route the image
// previews already use (federation-aware via fileUrl, so a remote session's file is relayed from the
// host that owns it).
//
// Living in the CHAT page also removes a whole relay: the click and the viewer are the same document
// now, so there is no shell forwarding, no feed-pane bring-forward/put-back, and the standalone /chat
// page views files exactly like the framed one. The module stays pane-agnostic on purpose: the file
// BROWSER (file-browse.ts, feed bundle) opens files through this same viewer in the FEED document, so
// whichever bundle imports it gets the identical modal.
import hljs from "highlight.js/lib/core";
import { marked, type Tokens } from "marked";
import { sanitizeMd } from "./md-sanitize";
import { hostOf, bareId, hostNameNodes } from "./host-prefix";
import { fileUrl } from "./preview";
import { ICON_DOWNLOAD, ICON_COPY, ICON_EDIT, ICON_ZOOM, ICON_CHECK, ICON_CROSS } from "./icons";   // the bar's glyphs (T367)
import { openPdfTab, wantsOwnTab } from "./preview";   // a PDF's own tab, and the gesture that asks for it
import { openFileTab, canPreview } from "./preview";   // any file's own tab, for the links inside a shown file, and the web-vs-webview test
import { kernelUrl } from "./media";
import { quoteSrcLabel } from "./docreview";
import { mintCreateId } from "./comments";   // one id per send gesture, so a same-words comment is its own thread
import { openContextMenu, closeContextMenu, placeMenu } from "./ctx-menu";   // the one menu builder: placed, dismissed, keyboard-reachable
import { linkifyFileText, linkMarkdownAnchors, viewerWalkTokens, fragmentTarget, URL_LINK_CLASS, FRAG_LINK_CLASS } from "./file-view-links";
import { selectionOpenIn } from "./path-links";
// eslint-disable-next-line @typescript-eslint/no-var-requires
const gclock = require("./gesture-clock.js");   // the gesture clock every settings post stamps through
import { delegate } from "./actions";
import { resolveDocRelative, joinDocPath, urlTitleParts, headingSlug, uniqueSlugs } from "./md-links";
import { mdWikiExtensions } from "./md-wiki";   // the same [[wikilink]] and callout grammar the chat renders (T351)
import { readTextCapped, overCapWords, settleUrlResponse } from "./capped-read";
import { wrapCodeLines, addCopyBtn } from "./code-block";   // a fence's per-line rows and Copy button, the chat's own
import { fenceCopyQueue, type Fence } from "./fence-source";   // what Copy copies: the fence's text as the file holds it, tabs and all
import "./viewer-grammars";   // six more grammars for a viewed file, registered on the bundle's hljs core (rust, go, c, java, sql, toml)

// hljs is registered per-bundle. Same language set (and grammar registrations) the chat's fence
// highlighting uses, dup-guarded, so importing this module alongside render.ts costs nothing.
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

for (const [name, lang] of Object.entries({
  bash, sh: bash, shell: bash, python, py: python, javascript, js: javascript,
  typescript, ts: typescript, json, xml, html: xml, css: cssLang, markdown, md: markdown,
  diff, yaml, yml: yaml,
})) {
  try { hljs.registerLanguage(name, lang as any); } catch { /* dup alias */ }
}

// Extension → the hljs language to force. Anything absent is shown unhighlighted rather than guessed:
// highlightAuto on a config file or a log picks a language at random and paints it misleadingly, and a
// wrong highlight reads as information the file does not contain.
const LANG: Record<string, string> = {
  py: "python", pyi: "python", js: "javascript", jsx: "javascript", mjs: "javascript",
  cjs: "javascript", ts: "typescript", tsx: "typescript", json: "json", jsonc: "json",
  yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash", bats: "bash",
  html: "xml", htm: "xml", xml: "xml", svg: "xml", vue: "xml", css: "css", scss: "css",
  md: "markdown", markdown: "markdown", diff: "diff", patch: "diff",
  rs: "rust", go: "go", c: "c", h: "c", java: "java", sql: "sql", toml: "ini", ini: "ini",   // viewer-grammars.ts (toml is hljs's ini grammar)
};

function langFor(path: string): string | null {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  return LANG[ext] || null;
}

// marked is a per-bundle singleton. render.ts makes the SAME calls with the SAME choices — GFM without
// hard breaks, strikethrough only on DOUBLE tildes (marked's stock GFM `del` tokenizer fires on a
// single ~, so prose between two "approximately" tildes renders struck through; GitHub itself only
// strikes ~~double~~) — so configuring here too is an idempotent no-op in the chat bundle, and keeps
// this module correct anywhere it's bundled without render.ts.
marked.setOptions({ gfm: true, breaks: false });
marked.use(...mdWikiExtensions);
marked.use({
  tokenizer: {
    del(src: string) {
      const m = /^~~(?=\S)([\s\S]*?\S)~~/.exec(src);
      if (!m) return undefined;
      return { type: "del", raw: m[0], text: m[1], tokens: (this as { lexer: { inlineTokens(s: string): unknown[] } }).lexer.inlineTokens(m[1]) };
    },
  },
} as Parameters<typeof marked.use>[0]);

// ── view-format preferences ────────────────────────────────────────────────────────────────────────
// The Raw ⇄ Rendered choice for markdown and the word-wrap toggle persist in localStorage, NOT a kernel
// file — per-browser view state, the same call feed-view-state.ts makes for the feed's open sections (it
// must survive a kernel restart without a round-trip to the thing that just restarted). RENDERED is the
// default for markdown (the user 2026-08-09); Raw stays one click away.
const FMT_KEY = "romp:fileviewFmt";
// wrap is GONE from the format state (the user 2026-08-24: "there doesn't need to be a button for
// that") — long lines always soft-wrap; a stored wrap key from the toggle era is simply ignored.
type FileViewFmt = { md: "rendered" | "raw" };

// Any malformed/foreign value reads as the defaults rather than throwing — a corrupt entry may cost the
// stored preference, never the viewer (feed-view-state's parseViewState contract).
function parseFmt(raw: string | null | undefined): FileViewFmt {
  const def: FileViewFmt = { md: "rendered" };
  if (!raw) return def;
  try {
    const o = JSON.parse(raw) as { md?: unknown };
    if (!o || typeof o !== "object") return def;
    return { md: o.md === "raw" ? "raw" : "rendered" };
  } catch { return def; }
}

function loadFmt(): FileViewFmt {
  try { return parseFmt(localStorage.getItem(FMT_KEY)); } catch { return parseFmt(null); }
}

function saveFmt(f: FileViewFmt): void {
  try { localStorage.setItem(FMT_KEY, JSON.stringify(f)); } catch { /* storage full */ }
}

function el(tag: string, cls?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

// ── the paint bracket ──────────────────────────────────────────────────────────────────────────────
// Runs one pass of the viewer as a timed frame of the page's performance collector (perf-telemetry.ts; the
// page publishes it as window.__rompPerf, federation.js on a kernel page and the pane's own bundle in VS Code),
// under the type `fileview:<why>`; one pass is timed today, `paint`, the text body painted anew, in the file
// view and the URL view alike. The viewer receives no frames of its own, so without this the cost of painting
// a large document (marked, the sanitizer, the highlight, the link pass) reached the pane's minute row only as
// a long animation frame attributed to whichever callback ran it, and `romp perf client` could not name the
// viewer.
// Counted under the pane that hosts the viewer (app chat or feed), with the main-thread-free sample the
// collector takes after an outermost bracket. No collector on the page (a page without one, a stand-in), or a
// slot holding something of another shape: the pass runs untimed, exactly as before.
function perfTimed<T>(why: string, fn: () => T): T {
  let p: any = null;
  try { p = typeof window !== "undefined" ? (window as any).__rompPerf : null; } catch { p = null; }
  return p && typeof p.timed === "function" ? p.timed("fileview:" + why, fn) : fn();
}

// ── text size (A−, A+, Ctrl/Cmd + wheel) ───────────────────────────────────────────────────────────
// The viewer's text sizes, as percentages of the page's own size: a FIXED table with ends, not a free
// multiplier, so the buttons, the wheel and the stored value all land on the same few sizes and a size can
// never run away. 100 is the default and leaves every size exactly as it was. The chosen step rides the
// viewer root as `data-fv-text`, and the sheets turn it into the ONE property the text views read
// (`--fv-scale`; the "text size and measure" block in styles.css and feed.css). Persisted like the
// Rendered ⇄ Raw choice above: per browser, in localStorage, under its own key, and any malformed or
// foreign value reads as the default (parseFmt's contract). The value stays a percentage, never a
// multiplier, so the stored text and the readout say the same thing.
export const TEXT_SIZES: readonly number[] = [70, 80, 90, 100, 115, 130, 150, 175, 200];
export const TEXT_SIZE_DEFAULT = 100;
const TEXT_SIZE_KEY = "romp:fileviewTextSize";
/** A stored value back to a step of the table; anything else (absent, garbage, a size the table does not
 *  hold, a multiplier) is the default, so a corrupt entry may cost the preference, never the viewer. */
export function parseTextSize(raw: string | null | undefined): number {
  const n = Number(raw ?? NaN);
  return TEXT_SIZES.includes(n) ? n : TEXT_SIZE_DEFAULT;
}
/** The step next to `pct` in `dir`, clamped at the table's ends (from 200, +1 answers 200). A `pct` the table
 *  does not hold (never stored, but the function is pure) steps from the default. */
export function stepTextSize(pct: number, dir: 1 | -1): number {
  const from = TEXT_SIZES.includes(pct) ? pct : TEXT_SIZE_DEFAULT;
  const i = TEXT_SIZES.indexOf(from) + dir;
  return TEXT_SIZES[Math.min(TEXT_SIZES.length - 1, Math.max(0, i))];
}
function loadTextSize(): number {
  try { return parseTextSize(localStorage.getItem(TEXT_SIZE_KEY)); } catch { return TEXT_SIZE_DEFAULT; }
}
function saveTextSize(pct: number): void {
  try { localStorage.setItem(TEXT_SIZE_KEY, String(pct)); } catch { /* storage full */ }
}
// Ctrl/Cmd + wheel over the text is the pointer's way to the same steps. A wheel notch is one event of about
// 100 pixels (Chrome) or a few LINES (Firefox, deltaMode 1); a trackpad pinch, which browsers report as a
// ctrlKey wheel, is a burst of events a few pixels each. Stepping once per event would run a pinch through
// the whole table in a moment, so the deltas are SUMMED: normalized to pixels, added up, and a step is taken
// each time the sum passes WHEEL_STEP_PX (then cleared); a change of direction clears it too, so a reversal
// does not first pay off the other way's remainder. Pure over the event's fields, so the summing is testable:
// `acc` is the running sum the caller keeps, `dir` the step to take now (0 for none). Events without the
// modifier are not the gesture (the caller lets them scroll) and never reach this.
export const WHEEL_STEP_PX = 40;
export function foldWheel(e: { deltaY: number; deltaMode: number }, acc: number): { acc: number; dir: 0 | 1 | -1 } {
  const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);   // lines and pages as pixels
  if (px === 0) return { acc, dir: 0 };
  const sum = acc !== 0 && Math.sign(acc) !== Math.sign(px) ? px : acc + px;         // a reversal starts over
  if (Math.abs(sum) < WHEEL_STEP_PX) return { acc: sum, dir: 0 };
  return { acc: 0, dir: sum < 0 ? 1 : -1 };                                           // wheel up is larger
}
// The control: A−, the current size as the reset between them, A+. Built once per open by BOTH viewers (a
// file on disk and a document opened from a link on the dashboard's own address), so the two honour one
// stored size and a step in either is kept for both. The readout is said only once the size is off the
// default (at 100% there is nothing to reset and nothing to say), but its SLOT is there from the start: the
// sheet empties it by visibility, not display, so neither A− nor A+ moves under the pointer when it fills
// after the first press, and the empty slot leaves the tab order. An end of the table is said with
// aria-disabled, not `disabled`: a button that disables under keyboard focus drops it (the ring would vanish
// on the press that reached the end), while an aria-disabled one keeps the focus, wears the sheet's disabled
// dress, and its press is the no-op set() already makes of a step to the size in force. The three hide until
// `textShowing` says a text body is up: each viewer's renderBody calls sync() on every paint, the first of them
// before the fetch, so the loader, a picture, a PDF and the editor never show them. Direct listeners are
// click-safe here: the buttons are built once and never rebuilt by a paint (the format toggles' idiom), and
// every press acknowledges in the same tick (the attribute, the readout, the dimmed end; the sheets reflow
// from the attribute alone).
function textSizeControl(root: HTMLElement, textShowing: () => boolean): { buttons: HTMLButtonElement[]; wrap: HTMLElement; trigger: HTMLButtonElement; menu: HTMLElement; sync: () => void; bindWheel: (body: HTMLElement) => void } {
  let pct = loadTextSize();
  const down = el("button", "fileview-btn fileview-size") as HTMLButtonElement;
  down.type = "button"; down.textContent = "A−"; down.title = "Smaller text (Ctrl/Cmd + wheel)";
  down.setAttribute("aria-label", "Smaller text");
  const reset = el("button", "fileview-btn fileview-size fileview-size-reset") as HTMLButtonElement;
  reset.type = "button"; reset.title = "Reset the text size";
  const up = el("button", "fileview-btn fileview-size") as HTMLButtonElement;
  up.type = "button"; up.textContent = "A+"; up.title = "Larger text (Ctrl/Cmd + wheel)";
  up.setAttribute("aria-label", "Larger text");
  const buttons = [down, reset, up];
  // T367 (the user 2026-09-12): the three ride a FLYOUT behind one zoom glyph, the bar's one control for the text
  // size (a magnifier with a plus; the words in its title and aria-label). The flyout wears the menu vocabulary
  // (the menu tokens, ui/CLAUDE.md) and opens under the glyph; the glyph again, Escape or a press outside closes
  // it (one document listener pair for every control, wired once), and it closes with the glyph when no text
  // shows. Built once per open with the buttons: click-safe, and the wheel binding is untouched.
  const wrap = el("span", "fileview-zoom");
  const trigger = el("button", "fileview-btn fileview-icon fileview-zoom-btn") as HTMLButtonElement;
  trigger.type = "button"; trigger.innerHTML = ICON_ZOOM; trigger.dataset.icon = "1";
  trigger.setAttribute("aria-label", "Text size");   // the title carries the size too, set by apply() below
  trigger.setAttribute("aria-haspopup", "true"); trigger.setAttribute("aria-expanded", "false");
  const menu = el("div", "fileview-zoom-menu");
  menu.hidden = true;
  menu.setAttribute("role", "group"); menu.setAttribute("aria-label", "Text size");
  for (const b of buttons) menu.appendChild(b);
  wrap.appendChild(trigger); wrap.appendChild(menu);
  const setOpen = (open: boolean) => {
    if (open && zoomOpen && zoomOpen.wrap !== wrap) zoomOpen.close();   // one flyout at a time, by rule (review)
    const focusInside = !open && !menu.hidden && !!document.activeElement && typeof menu.contains === "function" && menu.contains(document.activeElement);
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    trigger.classList.toggle("on", open);
    zoomOpen = open ? { wrap, close: () => setOpen(false) } : (zoomOpen && zoomOpen.wrap === wrap ? null : zoomOpen);
    if (focusInside) trigger.focus();   // Escape, or an outside press with the focus inside: back to the glyph, never left on a hidden button (review)
  };
  trigger.addEventListener("click", () => setOpen(menu.hidden));
  wireZoomDismiss();
  const atEnd = (b: HTMLButtonElement, end: boolean) => { if (end) b.setAttribute("aria-disabled", "true"); else b.removeAttribute("aria-disabled"); };
  // the property on the root, and the control's own state, from pct
  const apply = () => {
    root.dataset.fvText = String(pct);
    reset.textContent = pct + "%";
    trigger.title = "Text size " + pct + "% (Ctrl/Cmd + wheel)";   // the readout is a click away, so the hover says it (review)
    reset.setAttribute("aria-label", "Text size " + pct + "%, reset to " + TEXT_SIZE_DEFAULT + "%");
    reset.classList.toggle("fileview-size-default", pct === TEXT_SIZE_DEFAULT);   // the empty slot
    atEnd(down, pct === TEXT_SIZES[0]);
    atEnd(up, pct === TEXT_SIZES[TEXT_SIZES.length - 1]);
  };
  apply();                                                    // on the root before the bytes land: the first paint is at size
  // one step: store it and apply it; a step that changes nothing (an end of the table, a reset at the default) does nothing
  const set = (n: number) => { if (n === pct) return; pct = n; saveTextSize(n); apply(); };
  down.addEventListener("click", () => set(stepTextSize(pct, -1)));
  up.addEventListener("click", () => set(stepTextSize(pct, 1)));
  reset.addEventListener("click", () => set(TEXT_SIZE_DEFAULT));
  // Ctrl/Cmd + wheel over the BODY (not the bar): the browser's page zoom is the same gesture, so it is taken
  // over the viewer's text only, and only with the modifier held over a text body; a plain wheel scrolls as
  // ever, and the keyboard's Ctrl+plus/minus stays the browser's. Non-passive, so the page zoom can be
  // prevented; the summing is foldWheel's.
  let acc = 0;
  const bindWheel = (body: HTMLElement) => {
    body.addEventListener("wheel", (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey) || !textShowing()) return;
      e.preventDefault();
      const r = foldWheel(e, acc);
      acc = r.acc;
      if (r.dir) set(stepTextSize(pct, r.dir));
    }, { passive: false });
  };
  const sync = () => { const hide = !textShowing(); trigger.hidden = hide; if (hide) setOpen(false); };
  return { buttons, wrap, trigger, menu, sync, bindWheel };
}
// one text-size flyout open at a time, dismissed by Escape (in the capture phase, before the viewer's own Escape
// closes the file) or a press outside it; the pair of document listeners is wired once, for every control
let zoomOpen: { wrap: HTMLElement; close: () => void } | null = null;
let zoomDismissWired = false;
function wireZoomDismiss(): void {
  if (zoomDismissWired) return;
  zoomDismissWired = true;
  // a viewer closed with its flyout open (the close cross from the keyboard) leaves no reference behind that could
  // swallow the next viewer's Escape: closeFileView clears it, and a detached wrap is dropped here as well (review)
  const live = () => { if (zoomOpen && !zoomOpen.wrap.isConnected) zoomOpen = null; return zoomOpen; };
  document.addEventListener("mousedown", (e) => { const z = live(); if (z && !z.wrap.contains(e.target as Node)) z.close(); });
  document.addEventListener("keydown", (e) => { const z = live(); if (e.key === "Escape" && z) { z.close(); e.stopPropagation(); } }, true);
}

// The romp loader (swirl + wordmark + three pulsing accent dots), per the loading-state rule: the
// FIRST thing up on any wait, fading the instant real content lands. The viewer's earlier waits
// build this markup inline; the URL viewer reaches for it here.
function loaderEl(): HTMLElement {
  const load = el("div", "fileview-load");
  load.innerHTML = '<img src="/media/romp-swirl-glyph.svg" alt=""><span>romp</span>'
    + '<i class="fileview-dot"></i><i class="fileview-dot"></i><i class="fileview-dot"></i>';
  return load;
}

// The 2 MB body cap for a URL document — a MIRROR of the kernel's _TEXT_MAX_BYTES (kernel.py), which
// is what a local .md is already held to on the /file route. The URL viewer fetches from the browser,
// so no kernel ever sees the body; the cap is applied here — a declared Content-Length refuses before
// the body is read, and otherwise the streaming reader (capped-read.ts) counts the bytes as they
// arrive and cancels the source the moment they pass it. md-url-view.test.ts pins the two numbers equal.
const URL_TEXT_MAX_BYTES = 2 * 1024 * 1024;

// ── raw-mode editing (the file browser's slice 2, the user 2026-08-14) ─────────────────────────────
// The save op rides the WS poster the pane's boot hands initFileView; replies route back to the OPEN
// viewer through these module-level hooks (the viewer itself is a per-open closure).
let post: (m: Record<string, unknown>) => void = () => { /* bound by initFileView */ };

// ── the session the file was opened from (the user 2026-09-03) ────────────────────────────────────
// The viewer knows only a sid, and its openers mostly know no more: the relay branch initFileView
// keeps and the conflict Reload live in this module, the file browser hands over a bare sid. So the
// session's name and colour are RESOLVED from the sid here, through a lookup each hosting document
// registers once at boot beside initFileView (render.ts reads its tab set, feed.ts its session list).
// Unregistered, or a sid the document cannot name, the title bar carries no chip: an identity is
// looked up, never invented.
export interface FileViewIdentity { name: string; color: { bg: string; fg: string } | null }
let identityOf: (sid: string) => FileViewIdentity | null = () => null;
export function setFileViewIdentity(fn: typeof identityOf): void { identityOf = fn; }
/** The tail of a resolver's ladder when its lists hold no row for the sid — the kernel's own
 *  _peer_identity fallback: the sid's first 8 characters as an uncolored stub, a remote sid's `host:`
 *  kept in front so hostNameNodes still renders the host quiet. An empty sid names nothing. */
export function hostStub(sid: string): FileViewIdentity | null {
  const bare = bareId(sid);
  if (!bare) return null;
  const host = hostOf(sid);
  return { name: (host ? host + ":" : "") + bare.slice(0, 8), color: null };
}
let saveSeq = 0;
let editHooks: { reqId: number; saved: (mtimeNs: string) => void; failed: (err: string) => void } | null = null;
// The open comment box's verdict hooks, the same shape: a box that said "Sent" on the POST alone lied,
// because a refusal lands nowhere this overlay renders (the chat's warn toast sits under it, the feed
// page has no toasts). The ack is keyed on an EMPTY anchor uuid — a file passage has none, which is what
// tells it apart from the chat's own transcript-comment popover on the same channel.
// `viaHost` marks a note that went to the kernel as a comment thread, which only the composer-less
// feed pane does: a host-drop warn or a socket drop can fail THAT one, and must not touch a note
// staged locally, which never needed the connection.
// Keyed by the box's createId, so a second box sent before the first's ack can neither take nor strand
// the first's answer. A staged note (the chat pane) needs no connection and is keyed the same way.
type CmtHooks = { sid: string; viaHost: boolean; box: HTMLElement; landed: () => void; failed: (err: string) => void };
const cmtHooks = new Map<string, CmtHooks>();
// Kernel creates that showed Sent, by createId: the thread's session can still fail to start after the
// ack, and that failure takes the passage mark and the count back and reopens the words with the reason.
const landedCreates = new Map<string, (why: string) => void>();
// The open viewer's Submit button, fed by the host document's composerPending count. A pane with no
// composer never sends one, so the button never appears there.
let submitHooks: { sid: string; count: (n: number) => void } | null = null;
// The viewer and the chat document hosting it talk over DOM events, not window messages: a window message
// also reaches the chat's kernel-frame handler, which counts it as a frame and spends a parked image's retries.
export const VIEWER_TO_HOST = "romp:viewer-to-host";
export const HOST_TO_VIEWER = "romp:host-to-viewer";
function toHost(detail: Record<string, unknown>): void {
  window.dispatchEvent(new CustomEvent(VIEWER_TO_HOST, { detail }));
}
// Set by the open viewer: returns false to VETO a close (an editor holding unsaved changes asks
// first). The guard must live in closeFileView itself, because the browser overlay and the Escape
// handler both close through it without knowing an edit is in progress.
let closeGuard: (() => boolean) | null = null;
// ONE live media object URL at a time (images used to render as line-numbered mojibake; now an
// image/PDF view holds its bytes in an object URL). The URL is per-open state, but — like editHooks
// and gitHooks above — the teardown must be reachable from BOTH exits (closeFileView and the replace
// path), so the open viewer registers its URL here and each exit revokes it. Without the revoke
// every image view leaks its blob for the page's life.
let mediaUrlLive: string | null = null;
function dropMediaUrl(): void {
  if (mediaUrlLive) {
    try { URL.revokeObjectURL(mediaUrlLive); } catch { /* already gone */ }
    mediaUrlLive = null;
  }
}
// ONE in-flight URL read at a time, the same shape as mediaUrlLive: the URL viewer registers its
// AbortController here and BOTH exits (closeFileView and either replace path) abort it, so a modal
// torn down mid-body cancels its fetch and its stream — a stale read must never keep pulling bytes
// for a viewer that is gone.
let urlAbort: AbortController | null = null;
function dropUrlRead(): void {
  if (urlAbort) {
    try { urlAbort.abort(); } catch { /* already settled */ }
    urlAbort = null;
  }
}

// ── the body's content width, for the sheets ──────────────────────────────────────────────────────
// A table of the rendered document's own (a direct child of the .fileview-md root) may grow past the prose column, up to
// the body's content width less the root's inset (`.fileview-md > table` in both sheets reads --fv-body-w; the rule there
// says how). The value is the body's content width as its ResizeObserver reports it (the layout's own event, never a
// timer; a scrollbar's width is taken), written on EACH TOP-LEVEL TABLE rather than on the body it describes: the property
// is registered non-inherited (`@property --fv-body-w { inherits: false }`), so a write restyles the tables alone and not
// every node under the body, as a write to an inherited property on the body would. mdBlock rebuilds the root on every
// paint and no report follows a paint, so renderBody stamps the fresh tables itself (the returned function)
// with the width last reported; before the first report the property is unset and the sheet's fallback holds (the cap is
// the column). Absent ResizeObserver (a stand-in, an old engine) nothing is written and the fallback holds. One watch at a
// time: the next open, or the close, drops the last.
let dropWidthWatch: () => void = () => { /* no watch up */ };
function watchBodyWidth(body: HTMLElement): () => void {
  dropWidthWatch();
  let width = -1;                                      // the body's content width as last reported, -1 before the first report
  const stamp = (): void => {
    if (width < 0) return;
    const md = body.querySelector(".fileview-md");
    if (!md) return;
    for (const n of Array.from(md.children)) if (n.tagName === "TABLE") (n as HTMLElement).style.setProperty("--fv-body-w", width + "px");
  };
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver((entries) => {
      const w = entries.length ? entries[entries.length - 1].contentRect.width : body.clientWidth;
      if (w === width) return;
      width = w;
      stamp();
    });
    ro.observe(body);
    dropWidthWatch = () => { ro.disconnect(); dropWidthWatch = () => { /* dropped */ }; };
  }
  return stamp;
}

// ── viewer action registry (the user 2026-08-22) ── INTERNAL SEAM, no compatibility promise:
// reshape freely. Anything acting on the OPEN file declares itself here instead of hand-wiring into
// openFileView's action row, where every file-viewer change used to collide. mount() runs once per
// open and returns the action's element for the row (or null to sit this file out); an action that
// answers asynchronously (the GitHub link's kernel ask) mounts a placeholder and fills it in when its
// reply lands. Ordering is registration order, after the built-ins.
export interface FileViewActionCtx { path: string; sid: string | null; }
export interface FileViewAction { id: string; mount: (ctx: FileViewActionCtx) => HTMLElement | null; }
const fileViewActions: FileViewAction[] = [];
export function registerFileViewAction(a: FileViewAction): void {
  if (!fileViewActions.some((x) => x.id === a.id)) fileViewActions.push(a);
}

// ── the GitHub link (the user 2026-08-15) — the registry's first entry ─────────────────────────────
// One unit in the action row: the link, when the OWNING kernel's lazy fileGitLink ask resolves to a URL,
// and NOTHING otherwise (T367, the user 2026-09-12, who wanted the greyed link and its explanation gone
// from a file outside a repository: the 2026-09-05 always-fill-the-slot rule gave way to the tidier bar).
// A real URL is an anchor — the browser owns the new tab — with the full URL as its tooltip; a URL whose
// branch is not on origin stays an anchor, dashed, with the kernel's note in the tooltip and aria-label,
// since GitHub 404s it until the push. No URL (no repo, uncommitted, no GitHub origin, an older kernel)
// leaves the unit hidden: the verdict still rides the reply, it is just not rowed. The unit stays in the
// row hidden while the check is out, so the answer lands in place; aria-busy marks the pending unit in the
// DOM (the tests read it), and hidden it sits outside the accessibility tree, so no wait is announced for a
// link nobody sees yet. One question
// per open, reqId-guarded; a socket drop while it is out is the one thing that loses the reply, and the
// shim's reconnect event re-asks (initFileView), so the wait never outlives the socket. Exported for the
// DOM-shape test.
let gitSeq = 0;
let gitHooks: { reqId: number; apply: (url: string, reason: string) => void; ask: () => void } | null = null;
export const githubLinkAction: FileViewAction = {
  id: "github-link",
  mount({ path, sid }) {
    const unit = el("span", "fileview-gh");
    unit.hidden = true;
    unit.setAttribute("aria-busy", "true");
    const reqId = ++gitSeq;
    const ask = () => post({ type: "fileGitLink", path, sid: sid || undefined, reqId });
    gitHooks = {
      reqId,
      ask,
      apply: (url, reason) => {
        unit.removeAttribute("aria-busy");
        if (!url) { unit.replaceChildren(); unit.hidden = true; return; }   // nothing to link to: nothing shown
        const a = el("a", "fileview-btn") as HTMLAnchorElement;
        a.href = url; a.target = "_blank"; a.rel = "noopener";
        a.textContent = "GitHub ↗";
        a.title = reason ? url + "\n" + reason : url;       // the full URL one hover away, and the note with it
        a.setAttribute("aria-label", reason ? "GitHub: " + reason : "GitHub");
        if (reason) a.classList.add("fileview-gh-note");    // the branch is not on origin yet: dashed
        unit.replaceChildren(a);
        unit.hidden = false;
      },
    };
    ask();
    return unit;
  },
};
registerFileViewAction(githubLinkAction);

// ── quote a passage into the composer (the user 2026-08-23, the three-verbs consolidation) ────────
// Selecting text in the viewer seeds the SAME labeled quote chip a VS Code editor highlight does:
// the selection posts in the editorSelection shape to the composer's window — this document's, or
// the shell's chat pane (composerWindow below) — so render.ts's existing handler owns the chip end
// to end (no import cycle — the browseFiles precedent), labeled path:line via quoteSrcLabel. From
// there the flow is the chat's own: type a note (or none), Stage, keep going, send once. This
// REPLACED the viewer's separate review layer — the per-file comment store (romp:fileviewComments),
// the painted marks, and the one-shot Submit that assembled a message — because batching notes for
// one hand-off is exactly what quote chips + ⌘⏎ staging already do, and "comment" now means only
// the transcript's live threads.

// The retired store's data would otherwise sit in localStorage forever on every browser that
// ever commented — sweep it on load.
try { localStorage.removeItem("romp:fileviewComments"); } catch { /* storage may be denied */ }

// Where a quote seed lands (2026-09-03): the composer in THIS document when there is one (the
// chat-hosted viewer posts to its own window, and render.ts's editorSelection handler owns the chip
// end to end); otherwise the SHELL, when this document is framed by one. The feed (the file browser's
// document) hosts the viewer without a composer, and the shell forwards the seed into the chat pane
// (the editorSelection arm in kernel.py's landing shell). Before this, a selection in the feed-hosted
// viewer was dead air. No composer and no shell (a VS Code webview's cross-origin parent throws; a
// standalone pane has none) → null, and the gesture stands down without a fresh read. Presence is the
// DOM id, the Back button's import-free idiom (render.ts's inRompShell keys on the same node).
function composerWindow(): Window | null {
  if (document.getElementById("composer-input")) return window;
  try { if (window.parent !== window && window.parent.document.getElementById("chat-pane")) return window.parent; }
  catch { /* a cross-origin parent (VS Code) is not the romp shell */ }
  return null;
}

/** The comment box and the context menu, mounted on body rather than in the viewer, leave with it. */
function dropCommentOverlays(): void {
  closeContextMenu();                                  // through the builder, so its Escape listener goes too
  document.querySelectorAll?.(".fileview-cmt").forEach((n) => n.remove());
}

/** Every box still waiting on the host hears the same verdict: a host drop or a socket drop fails them all. */
function failHostBoxes(why: string): void {
  for (const [id, h] of Array.from(cmtHooks)) if (h.viaHost) { cmtHooks.delete(id); h.failed(why); }
}

export function closeFileView(): void {
  const wrap = document.getElementById("romp-fileview");
  if (!wrap) return;
  if (closeGuard && !closeGuard()) return;   // unsaved edits, and the user chose to keep them
  closeGuard = null;
  editHooks = null;
  cmtHooks.clear();                                    // a verdict landing after the close paints nothing
  landedCreates.clear();
  submitHooks = null;
  gitHooks = null;                                     // a reply landing after the close decorates nothing
  dropMediaUrl();                                      // an image/PDF view's bytes leave with the viewer
  dropUrlRead();                                       // …and a URL view's in-flight read is cancelled
  dropWidthWatch();                                    // …and the body's width watch (watchBodyWidth)
  dropCommentOverlays();
  if (zoomOpen) { zoomOpen.close(); zoomOpen = null; }   // the text-size flyout's reference leaves with the viewer (review: a keyboard close kept it, and the next viewer's first Escape was swallowed)
  wrap.remove();
  document.body.classList.remove("fileview-open");
}

const CMT_POP_WIDTH_PX = 360;
const CMT_POP_HEIGHT_PX = 200;
const CMT_QUOTE_MAX_CHARS = 240;
const CMT_SENT_CLOSE_MS = 700;
const CMT_MARK_TITLE_NOTE = "Noted: the passage rides a note for this file's session";
const CMT_MARK_TITLE_THREAD = "Commented: the passage opened a thread in this file's session";
const COMPOSER_STAGED_ID = "composer-staged";

/** A click on a file — a path in the chat, a file-browser row — WITH its gesture. A Cmd/Ctrl- or
 *  middle-click on a PDF (or Cmd/Ctrl+Enter on a file-browser row) opens the browser's own tab (preview.ts
 *  openPdfTab); a plain click opens the
 *  viewer below, like an image (the user 2026-09-07). Decided by EXTENSION, synchronously, inside the
 *  gesture: deciding on the fetched Content-Type would lose the gesture, and every browser would then
 *  block the tab. Every clicked file lands here, so this is the one place the choice lives; a relayed
 *  viewFile or a Reload has no gesture and opens the viewer directly. A BLOCKED popup falls through to
 *  the viewer, so the PDF is never unreachable, and a non-PDF is simply not the opener's business.
 *  `relay`: where a plain click goes when the hosting document routes it elsewhere (render.ts openPath
 *  handing the open to the shell for the Files pane): the gesture is still read first, so a modified click
 *  on a PDF takes its own tab whichever pane the plain click would have landed in. */
export function openFileClick(ev: MouseEvent | KeyboardEvent | null | undefined, path: string, sid?: string | null,
                              relay?: (path: string, sid: string | null, frag: string | null) => void, frag?: string | null): void {
  if (wantsOwnTab(ev) && openPdfTab(path, sid ?? null)) return;
  if (relay) { relay(path, sid ?? null, frag ?? null); return; }
  openFileView(path, sid, { frag: frag ?? null });   // `frag`: the section to land on (the chat's preview card, T351)
}

/** Show `path` in a modal over this pane. Re-opening replaces whatever is up — never stacks.
 *  `opts.line`: a line the open should show (a `path:12` link inside another file, file-view-links.ts): the code
 *  view scrolls its row into view once the text lands; a markdown file opens in its Raw view for THIS open (the
 *  Rendered view has no rows), without touching the saved preference.
 *  `opts.frag`: a sibling link's `#fragment` (`[see](report.md#results)`) to land on after the first rendered paint.
 *  Returns whether the open happened: false when the dirty-edit guard kept the previous viewer, so a caller
 *  that records the open (the Files pane's recent list) records only real ones. */
export function openFileView(path: string, sid?: string | null, opts?: { line?: number | null; frag?: string | null }): boolean {
  // The replace path bypasses closeFileView, so it needs the same dirty ask: opening file B over an
  // edited-but-unsaved file A must not silently eat A's buffer.
  if (document.getElementById("romp-fileview") && closeGuard && !closeGuard()) return false;
  closeGuard = null;
  editHooks = null;
  cmtHooks.clear();
  landedCreates.clear();
  dropCommentOverlays();                               // the old file's box goes too
  submitHooks = null;
  gitHooks = null;                                     // the replace path skips closeFileView — same drop
  dropMediaUrl();                                      // …and the old viewer's image bytes (the Reload path)
  dropUrlRead();                                       // …and a URL viewer's in-flight read, if that is what was up
  document.getElementById("romp-fileview")?.remove();
  // backdrop (the whole overlay carries the id every open/closed check targets) + the ~95% card.
  // The backdrop treatment matches the lightbox: dimmed, click outside the card closes, content
  // clicks don't (the user 2026-08-15: it must be obvious the chat is still right behind it).
  const wrap = el("div");
  wrap.id = "romp-fileview";
  wrap.onclick = (ev) => { if (ev.target === wrap) closeFileView(); };
  const box = el("div", "fileview");
  // A sibling link's `#fragment` (`[see](report.md#results)`: data-frag on the path link, file-view-links.ts
  // linkMarkdownAnchors) lands on its heading after the FIRST rendered paint — once; a Raw view has no ids to
  // land on, so the landing waits for the Rendered toggle rather than being spent (review find on #958, 2026-09-07).
  let pendingFrag: string | null = opts?.frag || null;
  document.body.classList.add("fileview-open");

  const bar = el("div", "fileview-bar");
  // BACK to the listing (the user 2026-08-24): a file opened FROM the browser overlays it with the
  // listing intact beneath (the one-directional stack above) — closing just the viewer IS the back.
  // The button renders only when a listing is actually underneath; a viewer opened from a path link
  // has nowhere to go back to and shows none. Import-free by design: presence is the DOM id (the
  // browser may not even be loaded in this document).
  if (document.getElementById("romp-filebrowse")) {
    const back = el("button", "fileview-btn fileview-back") as HTMLButtonElement;
    back.type = "button"; back.textContent = "‹ Files"; back.title = "Back to the file listing";
    back.addEventListener("click", () => closeFileView());
    bar.appendChild(back);
  }
  // Directory then basename as TWO elements, because only the directory may be truncated: the filename
  // is what identifies the file, so it never shrinks however deep the path is. (A single text node with
  // the rtl-ellipsis trick would truncate the right end — exactly the wrong half.)
  const name = el("div", "fileview-name");
  name.title = path;                                   // the full path, one hover away
  const cut = path.lastIndexOf("/");
  const dir = el("span", "fileview-dir");
  dir.textContent = cut >= 0 ? path.slice(0, cut + 1) : "";
  const base = el("span", "fileview-base");
  base.textContent = path.slice(cut + 1);
  if (cut >= 0) {
    // The discoverability path into the file BROWSER: the directory half of the title is a click
    // into its listing. Posted to our OWN window — initFileBrowse listens on the same channel the
    // shell relays into, so no import cycle between the two overlays.
    dir.classList.add("fileview-dir-link");
    dir.title = "Browse this file's folder";
    dir.addEventListener("click", () => {
      try { window.postMessage({ romp: "browseFiles", path: path.slice(0, cut) || "/", sid }, "*"); }
      catch { /* messaging our own window cannot really fail */ }
    });
  }
  name.appendChild(dir); name.appendChild(base);
  // The SESSION this file was opened from: a pill in the session's identity colour (the colour its
  // tab wears), "host:" quiet for a remote session (and marked while its link is down). Resolved
  // through the hosting document's registered lookup — no sid, or a sid it cannot name, and there is
  // no chip.
  const owner = sid ? identityOf(sid) : null;
  let sess: HTMLElement | null = null;
  if (owner) {
    sess = el("span", "fileview-sess");
    sess.replaceChildren(...hostNameNodes(owner.name, sid));
    if (owner.color) { sess.style.background = owner.color.bg; sess.style.color = owner.color.fg; }
    sess.title = "Opened from the " + owner.name + " session";
  }
  const acts = el("div", "fileview-acts");

  // A landed comment's trace, counted for THIS open: the passage mark below is painted into the rendered
  // DOM and a format toggle re-renders it away, so the count is the trace that survives the toggle. There
  // is no per-file comment store to read on open — a note lives in the session's composer, not beside the file.
  let commentsLanded = 0;
  const cmtCount = el("div", "fileview-cmt-count");
  cmtCount.hidden = true;
  // A pane with a composer stages notes; the composer-less feed pane opens threads, and the words say which.
  const stagesNotes = (): boolean => !!document.getElementById(COMPOSER_STAGED_ID);
  const noteCommentLanded = (delta = 1): void => {
    commentsLanded = Math.max(0, commentsLanded + delta);
    const noun = stagesNotes() ? "note" : "comment";
    cmtCount.textContent = commentsLanded + " " + noun + (commentsLanded === 1 ? "" : "s");
    cmtCount.title = stagesNotes() ? "Noted for this session while this file was open"
      : "Threads opened in this session while this file was open";
    cmtCount.hidden = commentsLanded === 0;
  };

  // Submit: sends what the composer is holding for this file's session without leaving the viewer —
  // reading a doc and sending the notes it produced used to mean scrolling back down to the chat. The
  // host document owns the send (and the count), so this is a poster and a label, nothing more. It
  // stays hidden until a count arrives, which is also how the FEED pane shows none: no chips there.
  const submit = el("button", "fileview-btn fileview-send") as HTMLButtonElement;
  submit.type = "button";
  submit.hidden = true;
  submit.title = "Send the notes staged for this session";
  submit.addEventListener("click", () => { if (sid) toHost({ romp: "submitComposer", sid }); });
  submitHooks = sid ? { sid, count: (n) => {
    submit.hidden = n < 1;
    submit.textContent = n === 1 ? "Submit 1 note" : "Submit " + n + " notes";
  } } : null;
  // Notes staged BEFORE this open still count, and the host only announces on a render of its own.
  if (sid) toHost({ romp: "composerPendingAsk", sid });

  // ── format toggles (the user 2026-08-09) ── A markdown file opens RENDERED, its Raw form one click
  // away; everything else keeps the code view, whose long lines the Wrap toggle can soft-wrap. Both
  // choices persist per browser (FMT_KEY above). These buttons are built once per open and never
  // re-rendered by kernel pushes — the viewer is a static overlay — so direct listeners are click-safe
  // here, same as Copy path below.
  const fmt = loadFmt();
  let text: string | null = null;             // set once the fetch lands; earlier clicks just save the pref
  let mtimeNs = "";                           // the file's mtime at load, NANOSECONDS AS A STRING —
  //   saveFile's conflict floor (ns because whole seconds let a same-second agent write slip the
  //   guard; a string because ~1.7e18 exceeds JS's safe-integer range and a number would round)
  let isText = false;                         // the kernel's verdicts (text/plain AND faithful UTF-8)
  // ── the media verdicts: a .png used to open as line-numbered mojibake — the fetch pipeline called
  // r.text() on ANY 200. All read from the KERNEL's Content-Type, never a client-side extension
  // re-test (the authoritative-source rule; the kernel derives the mime locally and the relay
  // re-derives it, so the header is a verdict, not an echo).
  let isImage = false;                        // image/* → one <img> at an object URL
  let isPdf = false;                          // application/pdf → the lightbox's iframe treatment
  let isSvgImage = false;                     // image/svg+xml exactly — unlocks the Source toggle
  let svgSource = false;                      // the SVG Source view is up (the highlighted XML)
  let svgText: string | null = null;          // the decoded SVG bytes, read once on first toggle
  let mediaBlob: Blob | null = null;          // the fetched bytes — the Source toggle decodes THESE
  let objUrl: string | null = null;           // this open's object URL (registered as mediaUrlLive)
  // The text the CURRENT view shows: the SVG Source view reads the decoded blob, every other text
  // view reads the fetch pipeline's text. The quote seed's failed-re-read fallback anchors against
  // THIS (a selection in the Source view must find its line in that XML; falling back to `text` —
  // null the whole time media mode is up — would strip every SVG quote's line label).
  const viewText = (): string | null => (svgSource && svgText !== null ? svgText : text);
  let editing = false;
  let dirty = false;
  let eolCRLF = false;                        // the file's dominant line ending — textareas normalize
  //   CRLF→LF on assignment, so an untouched CRLF file would otherwise save with every ending rewritten
  let ta: HTMLTextAreaElement | null = null;   // the FALLBACK surface (and the buffer pre-CodeMirror)
  let cm: { value(): string; focus(): void; destroy(): void } | null = null;   // the CodeMirror handle when mounted
  const bufValue = (): string | null => (cm ? cm.value() : ta ? ta.value : null);   // whichever surface owns the buffer
  const isMd = langFor(path) === "markdown";  // .md/.markdown — the only kind with a Rendered form
  // T367 (the user 2026-09-12): the row reads as GROUPS. The view group holds the Rendered|Raw pair (one
  // segmented control), the SVG Source toggle and the text-size glyph; the file group holds edit (and Save
  // and Cancel while editing), the GitHub link when it resolves, download and copy path; the close cross
  // stands alone at the end. Word buttons became glyphs with their words in the title and aria-label.
  const viewGroup = el("span", "fileview-group fileview-group-view");
  const fileGroup = el("span", "fileview-group fileview-group-file");
  const segBtns: Array<["rendered" | "raw", HTMLButtonElement]> = [];
  if (isMd) {
    const seg = el("span", "fileview-seg");                 // the pair joined: one hairline between, the outer corners rounded
    seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "Markdown view");
    for (const mode of ["rendered", "raw"] as const) {
      const b = el("button", "fileview-btn") as HTMLButtonElement;
      b.type = "button";
      b.textContent = mode === "rendered" ? "Rendered" : "Raw";
      b.title = mode === "rendered" ? "The prose the markdown means" : "The file's actual bytes";
      b.addEventListener("click", () => { fmt.md = mode; saveFmt(fmt); renderBody(); });
      segBtns.push([mode, b]);
      seg.appendChild(b);
    }
    viewGroup.appendChild(seg);
  }
  // ── text size ── A− and A+ step every text view through TEXT_SIZES, Ctrl/Cmd + wheel over the body
  // does the same, and the percentage between them (said once the size is off the default) is the reset;
  // textSizeControl above has the shape. Shown over a TEXT view only: the editor does not hold the body,
  // and the text the current view shows has landed (viewText: the fetch pipeline's text, or the decoded
  // XML while the SVG Source view is up; a picture or a PDF frame leaves both null). The text lands with
  // the kernel's Content-Type verdict, so a picture opened over a slow link never shows the control
  // beside the loader and then takes it away.
  const textShowing = (): boolean => !editing && viewText() !== null;
  const textSize = textSizeControl(box, textShowing);
  viewGroup.appendChild(textSize.wrap);          // the zoom glyph and its flyout (the three buttons inside)
  // ── the SVG Source toggle ── an SVG is served (and shown) as an image, but it IS also XML worth
  // reading; the toggle swaps in the existing highlighted-code view (langFor maps svg → xml) built
  // from the SAME fetched bytes — no second request. Appears only once an image/svg+xml body landed.
  const srcBtn = el("button", "fileview-btn") as HTMLButtonElement;
  srcBtn.type = "button"; srcBtn.textContent = "Source"; srcBtn.title = "The SVG's XML, highlighted";
  srcBtn.hidden = true;
  srcBtn.addEventListener("click", () => {
    if (svgText === null) {
      if (!mediaBlob) return;
      void mediaBlob.text().then((t) => { svgText = t; svgSource = true; renderBody(); });
      return;
    }
    svgSource = !svgSource;
    renderBody();
  });
  viewGroup.appendChild(srcBtn);

  // ── edit (the raw-mode slice) ── exactly what raw mode can show is what Edit can touch: the
  // button arms only when the kernel served text/plain WITH a Last-Modified to anchor the save's
  // conflict floor (an old remote kernel that mirrors neither gets no Edit rather than an unguarded
  // one). Markdown edits from its Raw view — what you edit is what raw shows.
  const editBtn = el("button", "fileview-btn") as HTMLButtonElement;
  editBtn.type = "button"; editBtn.innerHTML = ICON_EDIT; editBtn.classList.add("fileview-icon"); editBtn.dataset.icon = "1";
  editBtn.title = "Edit this file in place"; editBtn.setAttribute("aria-label", "Edit");
  editBtn.hidden = true;
  // The consent gate (the user 2026-08-22): editing is a kernel-side opt-in the SAVE ROUTE enforces —
  // this popup is where the one yes happens, and it broadcasts through the settings mesh so every
  // attached kernel's save route opens together (setFileEditing rides KERNEL_SETTING). The flag is
  // read fresh per click, never cached: another machine's gear may have flipped it meanwhile. A
  // decline changes nothing and is asked again next time — consent latches only on yes. If /version
  // is unreachable the popup still asks: the kernel-side gate refuses regardless, so the worst a
  // wrongly-granted yes here can do is draw one refused save with its plain-words error.
  async function editingAllowed(): Promise<boolean> {
    let on = false;
    try {
      const v = await (await fetch(kernelUrl("/version"), { cache: "no-store" })).json();
      on = !!v.fileEditing;
      gclock.learnAll(v.settingsGt);   // the same read teaches the clock every store's current stamp
    } catch { /* ask below */ }
    if (on) return true;
    if (!window.confirm(
      "Allow editing files from the dashboard?\n\n" +
      "Saves write straight to disk on the file's machine — and this applies on every machine " +
      "connected here. A session working in that folder is told when you edit under it.\n\n" +
      "You can turn this off later in the settings gear.")) return false;
    // gt = the consent's own click, stamped through the gesture clock (above every stamp the read
    // above reported): federation queues this per host across a down socket, and the kernel orders
    // applies by the stamp — a flush hours later must not outrank a newer gesture
    post({ type: "setFileEditing", enabled: true, gt: gclock.stamp("file-editing") });
    return true;
  }
  editBtn.addEventListener("click", () => {
    void editingAllowed().then((ok) => {
      if (!ok) return;
      if (isMd && fmt.md === "rendered") { fmt.md = "raw"; saveFmt(fmt); }
      enterEdit();
    });
  });
  const saveBtn = el("button", "fileview-btn") as HTMLButtonElement;
  saveBtn.type = "button"; saveBtn.textContent = "Save"; saveBtn.title = "Write the file (Ctrl/Cmd+S)";
  saveBtn.hidden = true;
  saveBtn.addEventListener("click", () => doSave());
  const cancelBtn = el("button", "fileview-btn") as HTMLButtonElement;
  cancelBtn.type = "button"; cancelBtn.textContent = "Cancel"; cancelBtn.title = "Leave edit mode";
  cancelBtn.hidden = true;
  cancelBtn.addEventListener("click", () => { if (confirmDiscard()) exitEdit(); });
  fileGroup.appendChild(editBtn); fileGroup.appendChild(saveBtn); fileGroup.appendChild(cancelBtn);
  // Registered actions render after the built-ins — the registry walk is the ONE place row
  // conventions live (see registerFileViewAction above). The GitHub link mounts here.
  for (const a of fileViewActions) {
    const n = a.mount({ path, sid: sid || null });
    if (n) fileGroup.appendChild(n);
  }

  // ── download (the user 2026-08-09) ── Any linked file can be SAVED, including everything the pane
  // cannot show: the kernel's ?download=1 serves anything on disk (the rationale lives with
  // _file_download in kernel.py). Same-origin and cookie-authed like the view fetch, and
  // federation-aware for free — fileUrl already routes a remote session's file through the relay.
  const dlUrl = fileUrl(path, sid) + "&download=1";
  const dl = el("button", "fileview-btn") as HTMLButtonElement;
  dl.type = "button"; dl.innerHTML = ICON_DOWNLOAD; dl.classList.add("fileview-icon"); dl.dataset.icon = "1";   // the tray glyph the lightbox wears (icons.ts)
  dl.title = "Download"; dl.setAttribute("aria-label", "Download");
  dl.addEventListener("click", () => startDownload(dlUrl, dl));
  fileGroup.appendChild(dl);

  // ── copy path (a glyph since T367) ── the acknowledgement is a glyph swap with the words in the tooltip and
  // aria-label: the press dims the button in the same tick (click-safe: every press acknowledges), then a check
  // and "Copied" for a moment, or a cross and "Copy failed" until the next press. No clipboard here (an insecure
  // origin drops navigator.clipboard) is said at once as the failure.
  const copy = el("button", "fileview-btn fileview-icon") as HTMLButtonElement;
  copy.type = "button"; copy.innerHTML = ICON_COPY; copy.dataset.icon = "1";
  copy.title = "Copy path"; copy.setAttribute("aria-label", "Copy path");
  let copyTimer: ReturnType<typeof setTimeout> | null = null;
  const copySay = (icon: string, word: string, cls: string, ms: number | null) => {
    copy.innerHTML = icon; copy.title = word; copy.setAttribute("aria-label", word);
    copy.classList.remove("ok", "err", "fileview-busy"); if (cls) copy.classList.add(cls);
    if (copyTimer) { clearTimeout(copyTimer); copyTimer = null; }
    if (ms !== null) copyTimer = setTimeout(() => copySay(ICON_COPY, "Copy path", "", null), ms);
  };
  copy.addEventListener("click", () => {
    copy.classList.add("fileview-busy");                     // the same-tick acknowledgement
    const w = navigator.clipboard?.writeText(path);
    if (!w) { copySay(ICON_CROSS, "Copy failed", "err", null); return; }
    w.then(() => copySay(ICON_CHECK, "Copied", "ok", 1200), () => copySay(ICON_CROSS, "Copy failed", "err", null));
  });
  const close = el("button", "fileview-btn fileview-close") as HTMLButtonElement;
  close.type = "button"; close.textContent = "✕"; close.title = "Close (Esc)";
  close.setAttribute("aria-label", "Close the file viewer");
  close.addEventListener("click", closeFileView);
  fileGroup.appendChild(copy);
  // the note count and Submit sit after the file group, so the row still reads view | file | notes | close
  acts.appendChild(viewGroup); acts.appendChild(fileGroup);
  acts.appendChild(cmtCount); acts.appendChild(submit); acts.appendChild(close);
  bar.appendChild(name); if (sess) bar.appendChild(sess); bar.appendChild(acts);

  const body = el("div", "fileview-body");
  const stampBodyWidth = watchBodyWidth(body);        // the body's content width, for a top-level table's cap (the sheets read --fv-body-w)
  textSize.bindWheel(body);                    // Ctrl/Cmd + wheel over the text steps the size (textSizeControl)
  // Links inside the file (file-view-links.ts): a rendered document's RELATIVE links (`[notes](./notes.md)`,
  // `[fig](plots/a.png)`) open the sibling file in this same viewer, its `[top](#evidence)` links land on their
  // heading, and the URLs and paths written in the text (a code view's rows, a rendered block) are links too. ONE
  // listener on the body reads every kind, installed once per open, so it is click-safe across the Rendered ⇄ Raw
  // swaps that rebuild the body's children. The gesture is the project's (the PDF and folder rule, preview.ts
  // wantsOwnTab): a PLAIN click acts inside the dashboard, and a Cmd/Ctrl-click or a middle-click opens the link in
  // a tab of its own. Plain: a path link opens the file here, with this viewer's session (a relative path was
  // already joined onto this file's directory at mark time; the kernel reads `~` and the session's machine); a URL
  // anchor opens itself (target _blank) and is left to the browser (in the chat document its capture-phase opener
  // takes it first, the same way); a section link scrolls to its target in this document, or does nothing where
  // there is none (its title says so), and never moves the hosting document. Modified: a path link opens in the
  // browser's own tab off the kernel's /file route (openFileTab; a blocked popup falls through to the viewer, so the
  // file is never unreachable), a URL anchor in a tab from here. A PLAIN click is not stopped: it goes on to the
  // document's own listeners (the feed's window listener that returns focus to the chat, the chat's menu closers).
  // A click that arrives with a selection open inside the body (the click that ends a press-drag-release inside a
  // path link, or a press held on a URL anchor and then dragged; a press on text collapses a selection first, so a
  // plain click never sees one, and the next click after such a selection opens) opens nothing; the chat's
  // capture-phase opener reads the same selection and yields too (path-links.ts selectionOpenIn). Enter or Space on a focused
  // path link is its click (path-links.ts, with a held Cmd/Ctrl carried) and lands here too. The chat's
  // document-level anchor delegate (render.ts) leaves an anchor with no href and a `#fragment` href alone, so those
  // clicks reach here in the chat document and in the feed document alike.
  const openUrlTab = (href: string) => {
    if (!href) return;
    if (canPreview()) window.open(href, "_blank", "noopener,noreferrer");   // the web dashboard: the browser's tab
    else post({ type: "openLink", href });                                  // the VS Code webview: the host's openExternal
  };
  const linkOf = (t: Element | null): HTMLElement | null => {
    const x = t && typeof t.closest === "function" ? t.closest(".file-uri-link, a." + URL_LINK_CLASS + ", a." + FRAG_LINK_CLASS) as HTMLElement | null : null;
    return x && body.contains(x) ? x : null;
  };
  const openLink = (x: HTMLElement, ev: MouseEvent) => {
    const own = wantsOwnTab(ev);
    if (x.classList.contains(FRAG_LINK_CLASS)) {                // a section of this document: this document's scroll, never the page's
      ev.preventDefault();
      // the rendered document's own headings, ids and named anchors, as mark time read them (scrollToFragment, over
      // the .fileview-md box through file-view-links.ts fragmentTarget): the viewer's chrome carries an id of its own
      // (the notice), and a lookup over the whole box would scroll to that on a colliding name
      scrollToFragment(body, x.getAttribute("href") || "");
      return;
    }
    const p = x.dataset.path;
    if (!p) {                                                    // the URL anchor
      if (!own) return;                                          // a plain click: the browser's own open
      ev.preventDefault(); ev.stopPropagation();                 // a modified one: one tab, from here
      openUrlTab(x.getAttribute("href") || "");
      return;
    }
    ev.preventDefault();
    if (own) {
      ev.stopPropagation();                                      // the modified click is the link's alone
      if (openFileTab(p, sid || null)) return;                   // its own tab; a blocked popup falls through to the viewer
    }
    const ln = Number(x.dataset.line);
    openFileView(p, sid || null, { line: ln > 0 ? ln : null, frag: x.dataset.frag || null });
  };
  body.addEventListener("click", (ev) => {
    const x = linkOf(ev.target as Element | null);
    if (!x) return;
    if (selectionOpenIn(box)) { ev.preventDefault(); return; }   // a drag-select ended on the link
    openLink(x, ev);
  });
  // The middle button: its press would start the browser's autoscroll on a path link (a span, unlike an anchor)
  // and swallow the auxclick, so the press is cancelled there; the auxclick is the link's own tab. A URL anchor's
  // middle-click is the browser's (it opens the href in a new tab itself), so neither listener touches one. A
  // section link's middle-click is this document's scroll, as its plain click is: the browser's own would open a
  // second copy of the hosting page at its URL plus the id, and a section of the shown file has no tab of its own.
  body.addEventListener("mousedown", (ev) => {
    const x = ev.button === 1 ? linkOf(ev.target as Element | null) : null;
    if (x && x.dataset.path) ev.preventDefault();
  });
  body.addEventListener("auxclick", (ev) => {
    if (ev.button !== 1) return;
    const x = linkOf(ev.target as Element | null);
    if (x && (x.dataset.path || x.classList.contains(FRAG_LINK_CLASS))) openLink(x, ev);
  });
  // A submit inside the body never navigates the pane's document. The sanitizer drops <form> and every
  // form control (md-sanitize.ts), so this is the backstop: a note's `<form action=...><button>` used to
  // take the pane's document to the action URL. One listener per open on the stable body, like the click
  // delegate above, so it survives every Rendered ⇄ Raw swap of the body's children.
  body.addEventListener("submit", (ev) => { ev.preventDefault(); });
  // Per the loading-state rule the first thing up is the romp loader, not a blank pane — a file coming
  // over an ssh tunnel to a phone is a real wait.
  const load = el("div", "fileview-load");
  load.innerHTML = '<img src="/media/romp-swirl-glyph.svg" alt=""><span>romp</span>'
    + '<i class="fileview-dot"></i><i class="fileview-dot"></i><i class="fileview-dot"></i>';
  body.appendChild(load);

  box.appendChild(bar); box.appendChild(body);
  wrap.appendChild(box);
  document.body.appendChild(wrap);

  // The edit-mode notice (a degraded editor, a refused save): one at a time, replacing the last, mounted as
  // a child of the card between the title bar and the body. It used to be prepended inside the body, above
  // an editor whose height is 100% of that same body, so the body's content was the bar plus the whole body:
  // the editor's bottom rows were cut off by the bar's height, and the body's own scroll carried the bar
  // out of view. As a row of the card (a column flex container; .fileview > .fileview-err keeps its height)
  // the body shrinks under it and the editor's 100% resolves against what is left. Nothing swaps the card's
  // children, so leaving edit mode removes the notice itself (exitEdit). Held by reference, THIS card's
  // notice and no other: a viewer that was replaced (Reload file re-opens fresh) keeps its keydown handler
  // and still runs its exitEdit on Escape, and a lookup by id from there would strip the live card's notice
  // while that card is still in edit mode.
  let note: HTMLElement | null = null;
  const noteBar = (msg: string): HTMLElement => {
    note?.remove();
    const bar2 = el("div", "fileview-err");
    bar2.id = "fileview-save-err";
    bar2.textContent = msg;
    box.insertBefore(bar2, body);
    note = bar2;
    return bar2;
  };

  // A 200 whose bytes will not DECODE — a zero-byte file, a mid-write/truncated image — fires the
  // img's error event and used to leave the browser's mute broken-image glyph: no reason, no way
  // out. This is the 413/415 pane idiom instead: plain words naming what happened, the path, and
  // the Download the view could not be. Keyed on the img's own error event, the exact deciding
  // signal (never a timer, never a byte sniff). The PDF iframe has no equivalent failure event —
  // the browser's viewer owns that surface and reports inside it — so this covers images only,
  // deliberately.
  const imgFailed = () => {
    if (!wrap.isConnected) return;              // settled after a close/replace — paint nothing
    const why = el("div", "fileview-err");
    why.textContent = "this image failed to decode — it may be mid-write or truncated";
    const hint = el("div", "fileview-err-hint");
    hint.textContent = path;
    why.appendChild(hint);
    const offer = el("button", "fileview-btn fileview-err-dl") as HTMLButtonElement;
    offer.type = "button"; offer.textContent = "Download";
    offer.title = "Save this file to your device";
    offer.addEventListener("click", () => startDownload(dlUrl, offer));
    why.appendChild(offer);
    body.replaceChildren(why);
  };

  // The view group takes no gap when every control in it is hidden (edit mode hides the format pair too). Its
  // state is derived from the controls' own hidden states, so it is read after the last of them is decided: at
  // the top of the paint, and again inside the media branch, which decides the Source button after that first
  // read. Over a picture the Source button is the group's one control (no format pair, the zoom glyph hidden),
  // so an SVG's first paint would otherwise leave the shown button inside a hidden group and the Source view
  // unreachable; a PNG or a PDF keeps every control hidden and the group hidden with them.
  const syncViewGroup = () => {
    viewGroup.hidden = !(segBtns.some(([, b]) => !b.hidden) || !textSize.trigger.hidden || !srcBtn.hidden);
  };
  // Chooses the body for the current prefs and syncs the buttons. The pressed state flips SYNCHRONOUSLY
  // in the click handler — the immediate acknowledgement ui/CLAUDE.md requires — and so does the content
  // swap, since the text is already in memory.
  const renderBody = () => {
    const rendered = isMd && fmt.md === "rendered";
    for (const [mode, b] of segBtns) {
      const on = fmt.md === mode;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
      b.hidden = editing;                       // format choices leave with edit mode; Save/Cancel own the bar
    }
    editBtn.hidden = editing || text === null || !isText || !mtimeNs;
    textSize.sync();                          // the text-size control follows every paint: shown over a text view only
    syncViewGroup();
    saveBtn.hidden = !editing;
    cancelBtn.hidden = !editing;
    if (isImage || isPdf) {
      // Media mode. The quote gesture gates off the RENDERED views only: a chip's label anchors to
      // text and an <img>/iframe body has none (affordance honesty: no real target, no affordance —
      // the mouseup seed below gates the same way). The SVG SOURCE view is a TEXT view — codeBlock
      // output, real text nodes — so selections there quote like any text view (a blanket media
      // gate would make an .svg's XML unquotable). Edit is already off through the isText arm
      // above; the md segs cannot exist (an .md is never served image/*); Download, Copy path, the
      // GitHub link, ✕ and the dir-link all keep working — none of them needs the text.
      srcBtn.hidden = !(isSvgImage && objUrl !== null);
      srcBtn.classList.toggle("on", svgSource);
      srcBtn.setAttribute("aria-pressed", String(svgSource));
      syncViewGroup();                        // the Source button was decided after the group's read above: the group follows it
      if (objUrl === null) return;            // the romp loader holds the body until the bytes land
      if (svgSource && svgText !== null) {
        body.replaceChildren(codeBlock(svgText, path, true));   // long lines always soft-wrap (the user 2026-08-24)
        return;
      }
      body.replaceChildren(isPdf ? pdfBlock(objUrl, path) : imgBlock(objUrl, path, imgFailed));
      return;
    }
    if (text === null || editing) return;   // loading, or the textarea owns the body right now
    perfTimed("paint", () => {                // the paint, as one fileview:paint frame of the page's collector (perfTimed above)
      if (text === null) return;              // never taken (the guard above): a let's narrowing does not reach into the closure
      body.replaceChildren(rendered ? mdBlock(text, { kind: "file", path, sid: sid || null }) : codeBlock(text, path, true));
      if (rendered) stampBodyWidth();         // a fresh root's tables take the width last reported (the property sits on the tables)
    });
    if (rendered && pendingFrag) {
      const h = pendingFrag; pendingFrag = null;
      requestAnimationFrame(() => { if (wrap.isConnected) scrollToFragment(body, h); });
    }   // long lines always soft-wrap (the user 2026-08-24)
  };

  // Selection → labeled quote chip (the user 2026-08-23): mouseup is the gesture's settle point.
  // The chip behaves exactly like a VS Code editor highlight's — one live source-labeled chip,
  // replaced by the next selection, persisting until sent, staged, or ✕'d — because it IS that
  // chip: render.ts's editorSelection handler seeds it. The post carries THIS viewer's sid, so the
  // chip lands in the session the file was opened FOR even if the active tab changed while the
  // modal was up (the 2026-08-19 routing rule: the gesture's session, never activeId-at-gesture).
  let seedSeq = 0;                                 // last gesture wins if two fresh reads race
  box.addEventListener("mouseup", (ev) => {
    if (editing) return;   // CodeMirror selections are edit gestures, not quotes
    if (ev.button > 0 || ev.ctrlKey) return;   // the right-click (or a Mac Ctrl+click) that opens the menu re-seeds nothing
    // A press on a title-bar CONTROL (A−, A+, the readout, Raw, Copy path, the GitHub link) settles no
    // selection: the mouseup lands on the button while a passage may still stand selected in the body,
    // and the seed below would re-read the file and re-seed the quote chip on every step of the text
    // size. The gate is the control under the lift, not the bar: a drag that starts in the body and is
    // released over the bar's path or its padding (the overshoot when selecting back to a file's first
    // line) is a selection like any other and settles.
    const at = ev.target as Element | null;
    if (at && bar.contains(at) && at.closest("button, a")) return;
    // No chip target reachable → no seed (the no-sink gating): the post would be dead air and the
    // label's fresh read dead work. The target is this document's composer (the chat-hosted viewer)
    // or, from a pane without one — the feed — the shell, which forwards the seed into the chat pane
    // (composerWindow above).
    const seedTarget = composerWindow();
    if (!seedTarget) return;
    // RENDERED media has no honest text to quote — an <img>/iframe body owns its own selection
    // surface; the SVG SOURCE view is a real text view and quotes like any other (renderBody's
    // media gate, same rule).
    if ((isImage || isPdf) && !(svgSource && svgText !== null)) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.anchorNode || !box.contains(sel.anchorNode)) return;
    const picked = sel.toString().trim();
    if (!picked) return;
    // The label's line is minted NOW, not at open: agents edit these same trees, so the open-time
    // snapshot's numbering may have quietly moved. Anchor against a fresh read; a FAILED re-read
    // falls back to the snapshot rather than fabricating drift nobody observed (the old Submit
    // guard's rule) — viewText, not text, because the SVG Source view's snapshot is the decoded
    // blob and `text` stays null in media mode. quoteSrcLabel itself degrades to the bare path
    // when the passage cannot be honestly found in whichever bytes it gets.
    const seq = ++seedSeq;
    fetch(fileUrl(path, sid), { cache: "no-store" })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .catch(() => viewText())
      .then((doc) => {
        if (seq !== seedSeq) return;
        try { seedTarget.postMessage({ type: "editorSelection", text: picked, sid: sid || undefined, src: quoteSrcLabel(path, doc, picked) }, "*"); }
        catch { /* messaging our own window or the same-origin shell cannot really fail */ }
      });
  });

  // Comment on a passage, owned by the viewer so every pane that mounts it gets the affordance — the
  // chat bundle and the feed bundle both bind `post`. Routes to the sid the file was opened for.
  box.addEventListener("contextmenu", (ev: MouseEvent) => {
    if (editing || !sid) return;
    const sel = window.getSelection();
    const picked = sel && !sel.isCollapsed && sel.anchorNode && box.contains(sel.anchorNode)
      ? sel.toString().trim() : "";
    if (!picked) return;
    ev.preventDefault();
    // The range is cloned HERE, not read back when the ack lands: by then the box has taken focus and
    // the selection is gone, so a mark painted later would have nothing to wrap.
    const marked = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
    // Stage where the pane has a composer (the note waits there for Submit), Comment where it opens a thread.
    openContextMenu(ev.clientX, ev.clientY, [
      { label: stagesNotes() ? "Stage" : "Comment", pick: () => openCommentBox(picked, ev.clientX, ev.clientY, marked) },
      { label: "Copy", pick: () => { try { void navigator.clipboard?.writeText(picked); } catch { /* no clipboard */ } } },
    ], { className: "fileview-ctx" });
  });

  /** Paint the commented passage, if the range can be wrapped: a selection crossing element boundaries
   *  (a sentence running into a list item) cannot be, and the count chip is the trace in that case. */
  function markCommentedRange(range: Range | null): HTMLElement | null {
    if (!range) return null;
    try {
      const mark = el("span", "fileview-cmt-mark");
      mark.title = stagesNotes() ? CMT_MARK_TITLE_NOTE : CMT_MARK_TITLE_THREAD;
      range.surroundContents(mark);
      return mark;
    } catch { return null; /* partially-selected nodes: the count chip carries the trace */ }
  }

  /** Take a painted mark back out, leaving its text where it was; returns a range over that text, so a
   *  retry can mark it again (the one the mark was painted from collapsed with the mark). */
  function unmark(mark: HTMLElement | null): Range | null {
    if (!mark?.parentNode) return null;
    const kids = Array.from(mark.childNodes);
    mark.replaceWith(...kids);
    if (!kids.length) return null;
    const r = document.createRange();
    r.setStartBefore(kids[0]); r.setEndAfter(kids[kids.length - 1]);
    return r;
  }

  /** The viewer's own note box, so commenting never depends on a composer being on screen. In a pane
   *  that HAS a composer the note stages there — instant, and it survives an unreachable host; the
   *  composer-less feed pane sends it to the kernel as a comment thread instead. Prints a refusal in
   *  place rather than on a toast the pane may not render. */
  function openCommentBox(picked: string, x: number, y: number, marked: Range | null, draft = "", why = "",
                          takeFocus = true): void {
    const pop = el("div", "fileview-cmt");
    const at = placeMenu(x, y, Math.min(CMT_POP_WIDTH_PX, window.innerWidth), CMT_POP_HEIGHT_PX, window.innerWidth, window.innerHeight);
    pop.style.left = at.left + "px";
    pop.style.top = at.top + "px";
    const quote = el("div", "fileview-cmt-quote");
    quote.textContent = picked.length > CMT_QUOTE_MAX_CHARS ? picked.slice(0, CMT_QUOTE_MAX_CHARS) + "…" : picked;
    const ta = el("textarea", "fileview-cmt-input") as HTMLTextAreaElement;
    ta.placeholder = "Comment on this passage…";
    const acts = el("div", "fileview-cmt-acts");
    const verb = stagesNotes() ? "Stage" : "Comment";
    const send = el("button", "fileview-btn") as HTMLButtonElement;
    send.type = "button"; send.textContent = verb;
    const cancel = el("button", "fileview-btn") as HTMLButtonElement;
    cancel.type = "button"; cancel.textContent = "Cancel";
    const err = el("div", "fileview-cmt-err");
    err.hidden = !why;
    err.textContent = why;
    ta.value = draft;
    const close = () => pop.remove();   // a send in flight still settles by its createId
    cancel.addEventListener("click", close);
    send.addEventListener("click", () => {
      const body = ta.value.trim();
      if (!body) { ta.focus(); return; }
      if (!sid) return;
      // Presence of the staged strip IS the composer test, the import-free idiom the Back button uses:
      // this file is bundled into panes that have no composer at all.
      const hasComposer = !!document.getElementById(COMPOSER_STAGED_ID);
      send.disabled = true; send.textContent = hasComposer ? "Saving…" : "Sending…";
      err.hidden = true;
      const createId = mintCreateId();
      const s = sid;
      cmtHooks.set(createId, {
        sid: s,
        viaHost: !hasComposer,
        box: pop,
        landed: () => {
          send.textContent = hasComposer ? "Saved" : "Sent";
          const mark = markCommentedRange(marked);
          noteCommentLanded();
          if (!hasComposer) {
            landedCreates.set(createId, (reason: string) => {
              const again = unmark(mark) || marked;
              noteCommentLanded(-1);
              openCommentBox(picked, x, y, again, body, reason, false);   // a late verdict takes no focus
            });
          }
          setTimeout(close, CMT_SENT_CLOSE_MS);
        },
        // The draft is KEPT and the send re-armed: the text is only in this box, so a refusal that
        // closed over it would destroy the thing the retry needs. A box closed mid-send reopens.
        failed: (why: string) => {
          if (!pop.isConnected) { openCommentBox(picked, x, y, marked, body, why, false); return; }
          send.disabled = false; send.textContent = verb;
          err.textContent = why;
          err.hidden = false;
          ta.focus();
        },
      });
      // Labelled from what the viewer shows, and sent at once: a fresh read here would hold the send open
      // on the network, and a close in that window would drop the words.
      const src = quoteSrcLabel(path, viewText(), picked);
      if (hasComposer) {
        toHost({ romp: "stageNote", sid: s, text: body, exact: picked, src, createId });
      } else {
        // An empty anchor asks the kernel to anchor the thread at the last chat event: a file passage has none.
        post({ type: "commentCreate", id: s, uuid: "", exact: picked, text: body, src, createId });
      }
    });
    ta.addEventListener("keydown", (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send.click(); }
    });
    acts.appendChild(send); acts.appendChild(cancel);
    pop.appendChild(quote); pop.appendChild(ta); pop.appendChild(err); pop.appendChild(acts);
    document.body.appendChild(pop);
    if (takeFocus) ta.focus();
  }

  // ── edit mode (the raw-mode slice) ── a plain textarea holding the raw bytes: an embedded editor
  // is a different project, and a textarea that keeps your changes beats a half-editor. The kernel's
  // mtime floor does the real safety work (agents edit these same trees — see _save_file).
  const confirmDiscard = (): boolean =>
    !editing || !dirty || window.confirm("Discard unsaved changes to " + path.slice(cut + 1) + "?");
  closeGuard = confirmDiscard;
  const norm = (s: string): string => s.replace(/\r\n/g, "\n");   // the textarea's own view of any text
  // The editing substrate is CodeMirror 6 (the user 2026-08-22), living in its OWN lazily-loaded
  // bundle so people who never edit download nothing (the main bundles import none of it — the
  // contract is the window global the chunk registers). The URL derives from the page's own running
  // bundle script — same directory, same ?v= cache token — so it resolves on the kernel pages and
  // the VS Code webview alike, and a rebuilt kernel always serves a matching chunk. A failed load
  // rejects ONCE and clears the latch so a later attempt retries fresh.
  let edChunk: Promise<{ mount: (host: HTMLElement, opts: object) => { value(): string; focus(): void; destroy(): void } }> | null = null;
  const editorChunk = () => edChunk || (edChunk = new Promise((res, rej) => {
    const w = window as any;
    if (w.__rompEditor) return res(w.__rompEditor);
    const self = Array.from(document.querySelectorAll("script[src]"))
      .map((n) => (n as HTMLScriptElement).src).find((u) => /\/(render|feed|files)\.js/.test(u));
    if (!self) return rej(new Error("no bundle script tag to derive the editor chunk URL from"));
    const sc = document.createElement("script");
    sc.src = self.replace(/\/(render|feed|files)\.js/, "/editor-chunk.js");
    sc.onload = () => { const e = (window as any).__rompEditor; e ? res(e) : rej(new Error("editor chunk loaded but did not register")); };
    sc.onerror = () => { edChunk = null; rej(new Error("the editor bundle failed to load")); };
    document.head.appendChild(sc);
  }));
  const enterFallback = () => {                 // the plain textarea: LOUD fallback, never a silent one
    ta = el("textarea", "fileview-editor") as HTMLTextAreaElement;
    ta.value = text!;                           // the browser normalizes CRLF→LF on assignment…
    ta.spellcheck = false;
    ta.addEventListener("input", () => { dirty = ta!.value !== norm(text!); });   // …so compare normalized
    ta.addEventListener("keydown", (e) => {     // the editor's own save chord; Esc falls through to onKey
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); doSave(); }
    });
    body.replaceChildren(ta);
    ta.focus();
  };
  let editSeq = 0;                              // stale chunk resolutions (edit left before load) no-op
  const enterEdit = () => {
    if (text === null || editing) return;
    editing = true; dirty = false;
    eolCRLF = /\r\n/.test(text);
    renderBody();
    // per the loading-state rule the chunk wait shows the romp loader, not a blank body
    const wait = el("div", "fileview-load");
    wait.innerHTML = '<img src="/media/romp-swirl-glyph.svg" alt=""><span>romp</span>'
      + '<i class="fileview-dot"></i><i class="fileview-dot"></i><i class="fileview-dot"></i>';
    body.replaceChildren(wait);
    const my = ++editSeq;
    editorChunk().then((ed) => {
      if (!editing || my !== editSeq) return;   // edit mode left (or re-entered) while the chunk loaded
      const host = el("div", "fileview-cm");
      body.replaceChildren(host);
      cm = ed.mount(host, {
        text: norm(text!), ext: path.slice(path.lastIndexOf(".") + 1),
        onChange: () => { dirty = cm!.value() !== norm(text!); },
        onSave: () => doSave(),
      });
      cm.focus();
    }).catch((err) => {
      if (!editing || my !== editSeq) return;
      enterFallback();
      // loud: say the editor is degraded, never pretend
      noteBar(String(err && (err as Error).message || err) + " — editing in the plain fallback editor.");
    });
  };
  const exitEdit = () => {
    editing = false; dirty = false; ta = null;
    cm?.destroy(); cm = null;
    editHooks = null;                           // a cancelled save's late ack must not touch a NEW session
    saveBtn.disabled = false; saveBtn.textContent = "Save";
    // the notice is a row of the card (noteBar), so the body swap below no longer takes it: a save that
    // landed, Cancel and Escape all leave through here, and none may leave a stale notice standing
    note?.remove(); note = null;
    renderBody();
  };
  const doSave = () => {
    const buf = bufValue();
    if (!editing || buf === null || saveBtn.disabled) return;
    if (!dirty) { exitEdit(); return; }         // nothing changed — leaving is the honest ack
    saveBtn.disabled = true; saveBtn.textContent = "Saving…";   // acknowledge before the round-trip
    // restore the file's own line endings — an untouched CRLF file must round-trip byte-identical
    const content = eolCRLF ? buf.replace(/\n/g, "\r\n") : buf;
    editHooks = {
      reqId: ++saveSeq,
      saved: (mtNs) => {
        mtimeNs = mtNs;
        text = content;
        // Keystrokes typed DURING the round-trip survive the ack (the review's in-flight-typing
        // finding): if the live buffer moved past the snapshot we saved, stay in edit mode with the
        // new baseline — never re-render over what the user is still typing.
        if (bufValue() !== null && bufValue() !== norm(content)) {
          dirty = true;
          saveBtn.disabled = false; saveBtn.textContent = "Save";
          return;
        }
        exitEdit();                             // re-renders the highlighted view from the saved bytes
      },
      failed: (err) => {
        saveBtn.disabled = false; saveBtn.textContent = "Save";
        // Loud, in place, and the BUFFER SURVIVES: the error bar sits above the body that holds the
        // textarea. A conflict (the disk moved: an agent wrote it) carries a Reload button, which re-opens
        // fresh behind the same discard confirm, so the user's edits are never thrown away silently (never
        // a merge UI).
        const bar2 = noteBar(err);
        if (/changed on disk/.test(err)) {
          const re = el("button", "fileview-btn fileview-err-dl") as HTMLButtonElement;
          re.type = "button"; re.textContent = "Reload file";
          re.title = "Fetch the file as it is now (asks before discarding your edits)";
          re.addEventListener("click", () => {
            if (!confirmDiscard()) return;
            dirty = false;                      // confirmed once — the replace guard must not ask twice
            openFileView(path, sid);
          });
          bar2.appendChild(re);
        }
      },
    };
    post({ type: "saveFile", path, sid: sid || undefined, content, baseMtimeNs: mtimeNs, reqId: saveSeq });
  };
  renderBody();   // buttons take their initial state now; the loader stays up until the fetch lands

  const onKey = (e: KeyboardEvent) => {
    if (!wrap.isConnected) { document.removeEventListener("keydown", onKey); return; }   // a replaced viewer's handler goes quietly
    if (e.key !== "Escape" || e.defaultPrevented || !document.getElementById("romp-fileview")) return;   // the comment box's Escape closes only the box
    e.preventDefault();
    if (editing) {                              // Escape peels edit mode first, never the whole viewer
      if (confirmDiscard()) exitEdit();
      return;
    }
    closeFileView();
    document.removeEventListener("keydown", onKey);
  };
  document.addEventListener("keydown", onKey);

  // The row for a 1-based line of the code view, scrolled to the middle; `pendingLine` is the open's `line`, spent
  // on the first text that lands (a Reload keeps the reader's place and does not scroll). A line past the end (a
  // stale `x.py:400` in a file that shrank) lands on the last row AND says so in the viewer's notice: a silent
  // landing on the wrong row would read as the file's truth.
  const scrollToLine = (n: number) => {
    const rows = body.querySelectorAll("code.hljs .fv-cl");
    if (!rows.length) return;
    if (n > rows.length) noteBar("Line " + n + " is past the end of this file, which has " + rows.length + (rows.length === 1 ? " line" : " lines") + "; showing the last line.");
    (rows[Math.min(Math.max(0, n - 1), rows.length - 1)] as HTMLElement).scrollIntoView({ block: "center" });
  };
  let pendingLine: number | null = opts && typeof opts.line === "number" && opts.line > 0 ? Math.floor(opts.line) : null;

  fetch(fileUrl(path, sid), { cache: "no-store" }).then((r): Promise<string | Blob> => {
    // Every failure says WHY, in the pane, rather than leaving a blank one: the kernel distinguishes
    // "not a type I serve" from "too big" from "not text after all", and that is exactly what the
    // person who clicked needs to know (a 413 names the size and the cap). The status rides along so
    // the catch below can tell "the file is there but I can't show it" from "there is no file".
    if (!r.ok) return r.text().then((t) => {
      throw Object.assign(new Error(t || ("HTTP " + r.status)), { status: r.status });
    });
    // Edit arms off the KERNEL's verdicts, never a client guess: text/plain AND a faithful UTF-8
    // round-trip (the latin-1 fallback re-decodes non-UTF-8 files — saving that back would rewrite
    // every non-ASCII byte, the review's executed repro), anchored by the ns mtime header (an old
    // kernel that sends neither simply gets no Edit button).
    isText = (r.headers.get("Content-Type") || "").startsWith("text/plain")
      && r.headers.get("X-Romp-Text-Utf8") !== "0";
    mtimeNs = r.headers.get("X-Romp-Mtime-Ns") || "";
    // Media branches on the SAME kernel verdict (an image 200 wears image/* and no X-Romp-Text-Utf8 —
    // tests/test_kernel_preview.py pins that contract server-side). The bytes below are the one fetch
    // either way: media takes them as a blob for an object URL, never a second request.
    const ct = r.headers.get("Content-Type") || "";
    isImage = ct.startsWith("image/");
    isPdf = ct.startsWith("application/pdf");
    isSvgImage = ct === "image/svg+xml";
    return isImage || isPdf ? r.blob() : r.text();
  }).then((t) => {
    if (!document.getElementById("romp-fileview")) return;    // closed while it was in flight
    if (t instanceof Blob) {
      // Minted only now — a viewer closed (above) or REPLACED mid-flight creates nothing to leak,
      // and never clobbers the new open's mediaUrlLive registration.
      if (!wrap.isConnected) return;
      mediaBlob = t;
      objUrl = URL.createObjectURL(t);
      mediaUrlLive = objUrl;                   // registered so close/replace can revoke (dropMediaUrl)
      renderBody();
      return;
    }
    text = t;
    // a line the link named: the Raw view for this open (unsaved: the preference stays), then the row
    if (pendingLine !== null && isMd && fmt.md === "rendered") fmt.md = "raw";
    renderBody();
    if (pendingLine !== null) { scrollToLine(pendingLine); pendingLine = null; }
  }).catch((err) => {
    if (!document.getElementById("romp-fileview")) return;
    const why = el("div", "fileview-err");
    const msg = String(err && err.message || err);
    why.textContent = msg;
    if (!msg.includes(path)) {
      // The kernel's 404/413/415 bodies name the RESOLVED path themselves now — the hint exists for
      // errors that don't (a network failure, an old kernel), not to say the same path twice.
      const hint = el("div", "fileview-err-hint");
      hint.textContent = path;
      why.appendChild(hint);
    }
    // A refusal-to-RENDER is not a dead end (ui/CLAUDE.md): when the file exists, the kernel's own
    // words are followed by the way out — the download the view could not be. A 404 stays offerless,
    // because offering to download a file that is not there would be a lie.
    if (offersDownload((err as { status?: number }).status)) {
      const offer = el("button", "fileview-btn fileview-err-dl") as HTMLButtonElement;
      offer.type = "button"; offer.textContent = "Download";
      offer.title = "Save this file to your device";
      offer.addEventListener("click", () => startDownload(dlUrl, offer));
      why.appendChild(offer);
    }
    body.replaceChildren(why);
  });
  return true;
}

// Which fetch failures still deserve a Download offer? Exactly the ones that mean the file EXISTS:
// 413 (too large to render) and 415 (on disk but not viewable — a .zip, a binary named like text).
// A 404 is genuinely missing, and gets nothing.
function offersDownload(status: number | undefined): boolean {
  return status === 413 || status === 415;
}

// ── URL mode (the user 2026-09-06) ─────────────────────────────────────────────────────────────────
// A chat message linking a markdown file on the dashboard's OWN origin (`https://<this host>/figs/
// run-1/evidence.md` — a published report, an evidence doc) used to open the raw text in a new tab.
// It presents here instead: same modal, same Rendered ⇄ Raw preference (FMT_KEY), same loader-first
// wait, same mdBlock — fetched by the BROWSER from the URL itself, with no kernel in the loop. Zero new
// kernel surface is the point: the kernel's /file relay is a preview relay and has stayed one on
// purpose (_remote_file's docstring), and a same-origin URL needs no relay — the browser already has
// the cookie. Cross-origin .md links are never routed here (render.ts checks isMarkdownUrl first).
//
// The shell is built here rather than threaded through openFileView because almost everything in that
// row is keyed on the KERNEL's reply — Edit on the text/plain + mtime verdicts, Download on the
// ?download=1 route, the GitHub link on a fileGitLink ask, ‹ Files on the browser overlay — and none
// of it exists for a URL. What both modes share is shared by construction: el/loaderEl, the format
// pref, mdBlock/codeBlock, closeFileView and the module-level teardown registrations.
export function openUrlView(href: string): void {
  // The same replace path as openFileView: an editor holding unsaved changes is asked first, and
  // every module-level registration the old viewer made is dropped before it is torn down.
  if (document.getElementById("romp-fileview") && closeGuard && !closeGuard()) return;
  closeGuard = null;
  editHooks = null;
  gitHooks = null;
  dropMediaUrl();
  dropUrlRead();                                       // a previous URL viewer's read stops pulling bytes
  document.getElementById("romp-fileview")?.remove();
  // THIS open's read, registered for the teardowns above (the mediaUrlLive pattern): the fetch and the
  // streaming body read both ride ctrl.signal, so a close or a replace mid-body cancels them.
  const ctrl = new AbortController();
  urlAbort = ctrl;
  const wrap = el("div");
  wrap.id = "romp-fileview";
  wrap.onclick = (ev) => { if (ev.target === wrap) closeFileView(); };
  const box = el("div", "fileview");
  document.body.classList.add("fileview-open");

  // Title: host/dir/ dimmed then the basename, the local viewer's two-element treatment — but the
  // directory half is NOT a browse link here: there is no listing to open for a URL. Drawn from the
  // clicked href now and RE-DRAWN from the response's URL once it lands (a redirect moves the document).
  const bar = el("div", "fileview-bar");
  const name = el("div", "fileview-name");
  name.title = href;                                   // the full URL, one hover away
  let parts = urlTitleParts(href);
  let loc = href;                                      // where the document LIVES: the response URL once it lands
  const dir = el("span", "fileview-dir");
  dir.textContent = parts.dir;
  const base = el("span", "fileview-base");
  base.textContent = parts.base;
  name.appendChild(dir); name.appendChild(base);
  const acts = el("div", "fileview-acts");

  const fmt = loadFmt();
  let text: string | null = null;
  const segBtns: Array<["rendered" | "raw", HTMLButtonElement]> = [];
  for (const mode of ["rendered", "raw"] as const) {
    const b = el("button", "fileview-btn") as HTMLButtonElement;
    b.type = "button";
    b.textContent = mode === "rendered" ? "Rendered" : "Raw";
    b.title = mode === "rendered" ? "The prose the markdown means" : "The document's actual bytes";
    b.addEventListener("click", () => { fmt.md = mode; saveFmt(fmt); renderBody(); });
    segBtns.push([mode, b]);
    acts.appendChild(b);
  }
  // the text-size control the local viewer has (textSizeControl): a document opened from a link honours
  // the same stored size as a file on disk, and a step here is kept for both
  const textSize = textSizeControl(box, () => text !== null);
  acts.appendChild(textSize.wrap);   // the zoom glyph and its flyout (T367)
  // The way OUT to the URL itself, in a new tab — an anchor wearing the button treatment, the
  // GitHub link's dress: the browser owns the tab. It is also every failure pane's exit below.
  // data-new-tab: this href IS a same-origin .md, exactly what the chat's anchor delegate routes
  // back into this viewer — the marker tells it this one click means the tab.
  const linkOut = (): HTMLAnchorElement => {
    const a = el("a", "fileview-btn fileview-gh") as HTMLAnchorElement;
    a.href = href; a.target = "_blank"; a.rel = "noopener";
    a.dataset.newTab = "1";
    a.textContent = "Open ↗"; a.title = "Open the URL in a new tab";
    return a;
  };
  acts.appendChild(linkOut());
  const copy = el("button", "fileview-btn") as HTMLButtonElement;
  copy.type = "button"; copy.textContent = "Copy URL"; copy.title = href;
  copy.addEventListener("click", () => {
    navigator.clipboard?.writeText(href).then(
      () => { copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy URL"; }, 1200); },
      () => { copy.textContent = "Copy failed"; });
  });
  const close = el("button", "fileview-btn fileview-close") as HTMLButtonElement;
  close.type = "button"; close.textContent = "✕"; close.title = "Close (Esc)";
  close.setAttribute("aria-label", "Close the file viewer");
  close.addEventListener("click", closeFileView);
  acts.appendChild(copy); acts.appendChild(close);
  bar.appendChild(name); bar.appendChild(acts);

  const body = el("div", "fileview-body");
  const stampBodyWidth = watchBodyWidth(body);        // the body's content width, for a top-level table's cap (the sheets read --fv-body-w)
  textSize.bindWheel(body);                            // Ctrl/Cmd + wheel over the text steps the size
  // In-document links land on their heading (mdBlock's fv-anchor stamp): one delegated listener, the
  // local viewer's pattern. No fv-open here — a URL document's sibling links are made absolute and
  // the chat's own anchor delegate routes them.
  delegate(body, {
    "fv-anchor": (a, ev) => { ev.preventDefault(); scrollToFragment(body, a.getAttribute("href") || ""); },
  });
  body.addEventListener("submit", (ev) => { ev.preventDefault(); });   // the local viewer's backstop (openFileView), same reason
  body.appendChild(loaderEl());                        // loader first; the fetch below replaces it
  box.appendChild(bar); box.appendChild(body);
  wrap.appendChild(box);
  document.body.appendChild(wrap);

  // The URL's own #fragment (`evidence.md#results`) lands after the FIRST RENDERED paint — once. A
  // Raw view has no heading ids, so a saved Raw preference does not SPEND the landing: it waits for
  // the Rendered toggle (review find on #958, 2026-09-07: landed was set before the mode check).
  let landed = false;
  const landFragment = () => {
    if (landed) return;
    let hash = "";
    try { hash = new URL(href).hash; } catch { /* not a URL — nothing to land on */ }
    if (!hash) { landed = true; return; }
    if (fmt.md !== "rendered") return;                 // nothing to land on yet; the next rendered paint tries again
    landed = true;
    requestAnimationFrame(() => { if (wrap.isConnected) scrollToFragment(body, hash); });
  };
  const renderBody = () => {
    for (const [mode, b] of segBtns) {
      const on = fmt.md === mode;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    }
    textSize.sync();                                   // shown once the document's text is up
    if (text === null) return;                         // the loader holds the body until the bytes land
    perfTimed("paint", () => {                         // the paint, as one fileview:paint frame of the page's collector (perfTimed above)
      if (text === null) return;                       // never taken (the guard above): a let's narrowing does not reach into the closure
      body.replaceChildren(fmt.md === "rendered"
        ? mdBlock(text, { kind: "url", href: loc })    // relative refs resolve against where it LIVES
        : codeBlock(text, parts.base, true));          // basename → langFor → markdown highlighting
      landFragment();                                  // after the paint, and only a rendered one lands (it schedules the scroll)
      if (fmt.md === "rendered") stampBodyWidth();     // a fresh root's tables take the width last reported
    });
  };
  renderBody();

  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || e.defaultPrevented || !document.getElementById("romp-fileview")) return;   // the comment box's Escape closes only the box
    e.preventDefault();
    closeFileView();
    document.removeEventListener("keydown", onKey);
  };
  document.addEventListener("keydown", onKey);

  // Every failure says WHY, in the pane, with the way out: never a console-only failure, never a
  // blank pane. The lines name the host and the URL rather than the viewer's mechanics.
  const fail = (words: string) => {
    if (!wrap.isConnected) return;                     // closed or replaced while in flight — paint nothing
    const why = el("div", "fileview-err");
    why.textContent = words;
    const hint = el("div", "fileview-err-hint");
    hint.textContent = href;
    why.appendChild(hint);
    why.appendChild(linkOut());
    body.replaceChildren(why);
  };
  const hostWord = (u: string) => urlTitleParts(u).dir.split("/")[0] || u;
  // Redraw the title from where the response actually CAME from: `/latest.md` → 302 →
  // `/reports/run-1/evidence.md` names the second, and relative figures resolve against it (loc feeds
  // mdBlock). The clicked href stays what Open ↗ and Copy URL hand back — the link the user was given.
  const relocate = (r: Response) => {
    loc = r.url || href;
    parts = urlTitleParts(loc);
    dir.textContent = parts.dir; base.textContent = parts.base; name.title = loc;
  };
  // Same-origin, cookie-authed, cache: no-store like every viewer fetch, on this open's abort signal.
  // mode: "same-origin" holds the rule through REDIRECTS too: the clicked URL passed isMarkdownUrl, but
  // a same-origin alias that 302s to a foreign host answering with a permissive CORS header would
  // otherwise be fetched and rendered with that host as the base for every relative figure (review
  // find on #958, 2026-09-07); the browser now rejects such a redirect and the .catch below says so.
  // No Content-Type sniffing beyond one refusal: the URL was intercepted because its PATH is markdown,
  // so the body is read as text and rendered as markdown whatever the server labelled it — except a
  // 200 labelled text/html, which is a web page standing in for the document (settleUrlResponse).
  fetch(href, { cache: "no-store", mode: "same-origin", signal: ctrl.signal }).then(async (r) => {
    // EVERY exit that stops short of consuming the body aborts this open's controller — once the
    // response has resolved that is what tears the transfer down (the review: a refused response's
    // bytes kept arriving for a modal already closed, because .finally had let go of the controller
    // and nothing had aborted it). settleUrlResponse fires the abort itself on each refusal verdict.
    if (!wrap.isConnected) { ctrl.abort(); return; }
    relocate(r);
    const v = settleUrlResponse(r, URL_TEXT_MAX_BYTES, () => ctrl.abort());
    if (v.kind === "http") { fail("HTTP " + v.status + " from " + hostWord(loc)); return; }
    if (v.kind === "not-document") { fail("the server answered with a web page, not a document (" + v.type + ")"); return; }
    if (v.kind === "declared-too-large") { fail(overCapWords(v.bytes, URL_TEXT_MAX_BYTES)); return; }
    if (v.kind === "no-body") { fail("this document could not be loaded from this page — the response carried no body"); return; }
    // Streamed under the cap: bytes counted as they arrive, the source cancelled the moment they pass
    // it (never the whole body buffered first), decoded as a stream so a codepoint split across two
    // chunks survives, and aborted with the viewer (ctrl.signal).
    const got = await readTextCapped(r.body!, URL_TEXT_MAX_BYTES, ctrl.signal);   // read verdict: the body is there
    if (!wrap.isConnected) { ctrl.abort(); return; }
    if ("tooLarge" in got) { ctrl.abort(); fail(overCapWords(null, URL_TEXT_MAX_BYTES)); return; }
    text = got.text;
    renderBody();                                      // paints, and lands the fragment if this paint is rendered
  }).catch((err) => {
    // Only the TEARDOWN's abort is silent (the modal is gone, or a refusal already painted its words);
    // an independently errored stream that merely wears the AbortError name still paints its failure.
    if (ctrl.signal.aborted) return;
    fail("this document could not be loaded from this page — " + String(err && (err as Error).message || err));
  }).finally(() => {
    if (urlAbort === ctrl) urlAbort = null;              // this read is over; a later open's registration stands
  });
}

// Kick the browser's downloader at `url` without touching the pane: a clicked <a download> starts a
// same-origin, cookie-authed request the BROWSER owns (its progress UI, its save location), and since
// the kernel answers with Content-Disposition: attachment the page never navigates — the viewer, the
// feed behind it, and the scroll position all stay put. The button acknowledges the click itself
// (ui/CLAUDE.md), because the browser's download UI can take a beat to appear over a slow tunnel.
function startDownload(url: string, btn: HTMLButtonElement): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";               // a hint; the kernel's attachment disposition is what actually decides
  document.body.appendChild(a);
  a.click();
  a.remove();
  // the acknowledgement: a glyph button says it in its tooltip and a busy dress (T367), a word button in its text
  if (btn.dataset.icon) {
    const was = btn.title;
    btn.title = "Downloading…"; btn.classList.add("fileview-busy");
    setTimeout(() => { btn.title = was; btn.classList.remove("fileview-busy"); }, 1500);
  } else {
    const was = btn.textContent;
    btn.textContent = "Downloading…";
    setTimeout(() => { btn.textContent = was; }, 1500);
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Wrap mode's numbering. The flat sibling gutter cannot survive soft-wrapping — one logical line becomes
// several visual lines and every number below it drifts — so wrap mode RESTRUCTURES instead of shipping a
// misaligned column: each logical line is its own row (.fv-cl) whose number is a CSS counter in ::before
// (the chat's .cl/.ct treatment, styles.css), so the numbers stay glued to their lines however tall a
// wrapped line grows, and being ::before content they still never copy with the code. hljs spans can
// cross newlines, so each row re-opens the spans the previous row left unclosed and closes its own —
// render.ts's wrapCodeLines balance walk.
function wrapNumberedHtml(html: string): string {
  const lines = html.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();   // a trailing newline is not a line
  let open: string[] = [];
  return lines.map((ln) => {
    const prefix = open.join("");
    const re = /<span[^>]*>|<\/span>/g; let m; const stack = open.slice();
    while ((m = re.exec(ln))) { if (m[0] === "</span>") stack.pop(); else stack.push(m[0]); }
    const suffix = "</span>".repeat(Math.max(0, stack.length));
    open = stack;
    return `<span class="fv-cl"><span class="fv-ct">${prefix}${ln}${suffix}</span></span>`;
  }).join("");
}

// Line-numbered <pre>. In the default (no-wrap) view the gutter is a sibling column rather than text in
// the same <pre>, so selecting the code and copying it does NOT drag the line numbers along with it; the
// wrap view keeps that copy-safety a different way (see wrapNumberedHtml above).
function codeBlock(text: string, path: string, wrapLines: boolean): HTMLElement {
  const wrap = el("div", "fileview-code");
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();   // a trailing newline is not a line
  const lang = langFor(path);
  let hl: string | null = null;
  if (lang) {
    try { hl = hljs.highlight(text, { language: lang }).value; }
    catch { hl = null; }                               // a broken grammar must never cost the content
  }
  const pre = el("pre", "fileview-pre");
  const code = el("code", "hljs");
  if (wrapLines) {
    pre.classList.add("fileview-wrap");
    code.innerHTML = wrapNumberedHtml(hl !== null ? hl : escapeHtml(text));
    linkifyFileText(code, path);   // URLs and paths in the text, on the DOM the highlight built (file-view-links.ts)
    pre.appendChild(code);
    wrap.appendChild(pre);
    return wrap;
  }
  const gutter = el("div", "fileview-gutter");
  gutter.textContent = lines.map((_, i) => String(i + 1)).join("\n");
  gutter.setAttribute("aria-hidden", "true");
  if (hl !== null) code.innerHTML = hl; else code.textContent = text;
  linkifyFileText(code, path);   // the same pass on the gutter layout, which no caller in the viewer asks for (every call passes wrapLines)
  pre.appendChild(code);
  wrap.appendChild(gutter); wrap.appendChild(pre);
  return wrap;
}

// Land an in-document fragment on its target. The fragment, as typed, percent-encoded or not, names an element
// of the RENDERED document (the .fileview-md box under `box`, never the viewer's chrome around it, whose notice wears
// an id of its own, and never the page's ids): an element with exactly that id, a GitHub-style `<a name>`, or a
// heading, through the slug the heading ids were minted with, so `#Evidence%20Results`, `#evidence-results` and
// `#Evidence Results` all find md-evidence-results (file-view-links.ts fragmentTarget is the one lookup; mark time
// reads it too). Nothing found → nothing happens: inert, never a scroll to the top and never a navigation. Both
// viewers land through here: the local one's section links and a sibling link's fragment, the URL one's fv-anchor
// links and the URL's own hash.
function scrollToFragment(box: HTMLElement, fragment: string): boolean {
  let frag = fragment.replace(/^#/, "");
  try { frag = decodeURIComponent(frag); } catch { /* a stray % — match the bytes as written */ }
  if (!frag) return false;
  const target = fragmentTarget(box.querySelector(".fileview-md") || box, frag);
  if (!target) return false;
  target.scrollIntoView({ block: "start" });
  return true;
}

// Where the rendered document LIVES, so its relative references can be resolved against it (the user
// 2026-09-06: a `![fig](fig.png)` in a viewed document pointed at the dashboard's root). Two homes:
//   • url  — the document was fetched from `href` by the browser (openUrlView); a relative src/href
//            resolves against that URL, exactly as it would have on the page itself.
//   • file — the document is `path` on the session's disk (openFileView); a relative image is the
//            sibling file over the kernel's /file route (fileUrl — federation-aware, never hand-built),
//            and a relative link opens the sibling in this same viewer.
// No location at all (a caller with nothing to say) leaves the markup as marked emitted it.
type MdDocLoc = { kind: "url"; href: string } | { kind: "file"; path: string; sid: string | null };

// Markdown rendered as the prose it means (the user 2026-08-09: Rendered is the default, Raw one click
// away). The file is arbitrary bytes off a disk and marked emits raw HTML verbatim, so, exactly like the
// chat's md() in render.ts, the output goes through the shared sanitizer (sanitizeMd, md-sanitize.ts)
// before it ever reaches the DOM: an <img onerror> or a javascript: href in a README must never run in
// the dashboard, and a README's <style>, form or fixed-positioned div must never reach the viewer's chrome.
function mdBlock(text: string, doc?: MdDocLoc): HTMLElement {
  const box = el("div", "fileview-md");
  const fences: Fence[] = [];                          // marked's code tokens in document order, for the fence pass's Copy (fence-source.ts)
  let rendered = true;                                 // false on the fallback: the bare text, with nothing added to it
  try {
    // The code tokens are collected as the parse walks them: the lexer expanded the file's leading tabs to spaces before
    // it cut them, and the fence pass below reads each fence's text back out of the file for its Copy button
    // (fence-source.ts). Handed to THIS parse only, so the chat's marked singleton learns nothing; a walkTokens an
    // extension put on the defaults runs as well, since per-call options replace rather than compose.
    // A link's destination is put in the form the sanitizer keeps BEFORE the HTML exists (file-view-links.ts
    // viewerWalkTokens: `notes.md:7` reads as a scheme to DOMPurify, `file:///a.md` is a scheme it refuses, and an
    // anchor it strips is a label nothing can sort afterwards). Handed to THIS parse only: the marked singleton is
    // the chat's too, and the chat's anchors must not learn the viewer's forms. A walkTokens an extension put on
    // the defaults runs as well: per-call options replace, not compose. The file kind's alone: a URL document has
    // no directory for `notes.md:7` to sit in, and its links resolve against the URL below.
    const base = marked.defaults.walkTokens;
    const dirty = marked.parse(text, { walkTokens: (t) => {
      if (t.type === "code") { const c = t as Tokens.Code; fences.push({ text: c.text, indented: c.codeBlockStyle === "indented" }); }
      if (doc && doc.kind === "file") viewerWalkTokens(t);
      if (base) void base.call(marked, t);
    } }) as string;
    // The one sanitizer the chat's md() uses too (md-sanitize.ts): html + svg (a note's own inline SVG), no
    // data-* (a document's `<span data-act="stopRetrying">` would otherwise bubble to render.ts's
    // document-level delegate and interrupt the active session; review find on #958, 2026-09-07), and
    // rules modelled on GitHub's for a note's own HTML: no <style>, no form controls, ids and names prefixed
    // user-content-, inline style reduced to its colours, no background attribute. The sanitized <body>'s
    // children are adopted as they are, no re-parse. The viewer's own marks (heading ids, the file kind's path
    // and section links, the URL kind's fv-anchor stamp) are set AFTER this sanitize, so they are unaffected and
    // never prefixed; an author's own id or name is read under its prefix (file-view-links.ts fragmentTarget).
    box.replaceChildren(...Array.from(sanitizeMd(dirty).childNodes));
  } catch {
    box.textContent = text;                            // a marked bug must never cost the content
    rendered = false;
  }
  // Relative references resolve against the DOCUMENT, after sanitisation (DOMPurify has already
  // dropped every dangerous scheme; what is left is either absolute — untouched — or relative to a
  // document the browser knows nothing about). getAttribute, never the .src/.href property: the
  // property is already resolved against the PAGE, which is the wrong base.
  //
  // Every heading gets an id first — marked 12 emits none, so a document's own `[top](#evidence)`
  // had nothing to land on. GitHub's slug (headingSlug, made unique in order by uniqueSlugs), and
  // PREFIXED `md-` on purpose: an unprefixed id="tabs" would dress a heading in the chat page's
  // #tabs CSS and shadow getElementById("tabs") for the page's own controls. Both modes, before the
  // anchors are sorted: a section link is live when its target is a heading, an element with that id
  // or a named anchor (file-view-links.ts fragmentTarget reads all three).
  const heads = Array.from(box.querySelectorAll("h1, h2, h3, h4, h5, h6")) as HTMLElement[];
  const slugs = uniqueSlugs(heads.map((h) => headingSlug(h.textContent || "")));
  heads.forEach((h, i) => { h.id = "md-" + slugs[i]; });
  // A task item wears GitHub's class: marked emits the checkbox as the li's first node with no hook on the li (inside its
  // first paragraph in a loose list), and the sheets' `li.task-list-item` rule drops the bullet that sat beside the box
  // and pulls the box into the gutter. After the sanitize (md-sanitize.ts keeps marked's checkbox as the one control a
  // note carries, and makes an author's enabled one disabled, so every box the stamp sees is inert), and only for a
  // checkbox that is the item's FIRST NODE: :first-child counts elements alone, so a checkbox an author's raw HTML puts
  // after the item's text (`- text then <input type="checkbox" disabled>`) matches the selector, and the previousSibling
  // check leaves it, and its item's bullet, where the file put them. An author who writes the class on an li of their
  // own, or a raw checkbox that opens an item, gets the same bullet-less item GitHub would give them.
  box.querySelectorAll('li > input[type="checkbox"]:first-child:disabled, li > p:first-child > input[type="checkbox"]:first-child:disabled').forEach((input) => {
    if (input.previousSibling) return;                 // text before the box: an author's checkbox mid-item, not a task item
    const li = input.closest("li");
    if (li) li.classList.add("task-list-item");
  });
  if (doc) {
    box.querySelectorAll("img[src]").forEach((node) => {
      const img = node as HTMLImageElement;
      const src = img.getAttribute("src") || "";
      if (doc.kind === "url") {
        const abs = resolveDocRelative(src, doc.href);
        if (abs !== src) img.setAttribute("src", abs);
      } else if (src && !/^[a-z][a-z0-9+.-]*:/i.test(src) && !src.startsWith("//")) {
        // Every path-shaped src — relative, absolute, ~-anchored — is a file on the session's disk;
        // only a scheme (http:, data:) or a protocol-relative URL names something the browser fetches.
        img.setAttribute("src", fileUrl(joinDocPath(doc.path, src), doc.sid));
      }
    });
  }
  if (doc && doc.kind === "url") {
    box.querySelectorAll("a[href]").forEach((node) => {
      const a = node as HTMLAnchorElement;
      const href = a.getAttribute("href") || "";
      if (!href || href.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(href)) return;   // in-document, or already absolute
      // Absolute now, so the chat's document-level anchor delegate sees a scheme: a same-origin
      // .md target opens in this viewer (isMarkdownUrl), everything else in a new tab.
      a.setAttribute("href", resolveDocRelative(href, doc.href));
    });
  }
  if (doc && doc.kind === "file") {
    // A file on the session's disk: its links are sorted by file-view-links.ts (linkMarkdownAnchors). A link to the
    // web opens a NEW tab: the viewer lives inside the chat pane's document, and letting a README link navigate it
    // away would silently eat the chat until a reload. A link whose target is a file relative to this one becomes a
    // path link that opens THAT file in the viewer (its `#fragment` or `:line` riding along); a section link
    // (`#results`) is the viewer's scroll; a target the sanitizer removed is a dead link that says why.
    if (rendered) linkMarkdownAnchors(box, doc.path);
  } else {
    // A URL document (openUrlView), or a caller with no location: links open a NEW tab, for the same reason. One
    // kind stays in the viewer: an IN-DOCUMENT `#fragment` link, which lands on its heading through the body's
    // delegated fv-anchor handler — a forced _blank on those opened a REAL tab at the chat page's own URL plus
    // the fragment (found live, 2026-09-06). A URL document's sibling links are absolute by now, and the chat's
    // own anchor delegate routes them (a same-origin .md back into the viewer).
    box.querySelectorAll("a[href]").forEach((node) => {
      const a = node as HTMLAnchorElement;
      if ((a.getAttribute("href") || "").startsWith("#")) { a.dataset.act = "fv-anchor"; return; }
      a.target = "_blank";
      a.rel = "noopener";
    });
  }
  // Fenced blocks: highlight only a language the fence NAMES and this bundle registers (the same no-guessing rule as
  // langFor; an unnamed block stays plain rather than being painted at random). Then, for EVERY fence, named or not, the
  // chat's own dress (code-block.ts): the per-line rows that number the lines and make a soft-wrap read distinctly from a
  // real newline, and the Copy button. Copy copies the fence's text AS THE FILE HOLDS IT (fence-source.ts, off the code
  // tokens the parse collected): the raw text captured here is read before the rows drop the newlines, but after marked's
  // lexer turned the file's leading tabs into four spaces each, so a Makefile recipe copied from the rendered text pasted
  // back with spaces; a fence the module does not find in the file copies the raw text as before.
  const copySources = fenceCopyQueue(text, fences);
  box.querySelectorAll("pre code").forEach((node) => {
    const codeEl = node as HTMLElement;
    const raw = codeEl.textContent || "";
    const pre = codeEl.parentElement;
    const host = pre && pre.tagName === "PRE" ? pre : null;
    const queued = copySources.get(raw);
    const toCopy = (queued && queued.length ? queued.shift() : null) ?? raw;
    const lang = (codeEl.className.match(/language-([\w-]+)/) || [])[1];
    if (lang && hljs.getLanguage(lang)) {
      try {
        codeEl.innerHTML = hljs.highlight(raw, { language: lang }).value;
        codeEl.classList.add("hljs");
      } catch { /* leave plain */ }
    }
    wrapCodeLines(codeEl);
    if (host) addCopyBtn(host, toCopy);
  });
  // URLs and paths written in the prose and the code blocks, after the highlight rewrote the blocks' markup
  // (a pass before it would be undone). marked already made the prose's URLs anchors; text inside one is skipped.
  // The fallback's bare text is left bare: it is the content and nothing else, which is that branch's promise.
  if (rendered && doc && doc.kind === "file") linkifyFileText(box, doc.path);
  return box;
}

// The image body: ONE <img> aimed at the object URL — never innerHTML, never an iframe. That is the
// whole SVG-safety story (an <img> never runs SVG scripts — the same surface the kernel's preview
// comments and the relay's local type re-derivation rely on), and for every other image it is simply
// the right element. Centered and capped like the lightbox's image (.romp-lightbox-img), so a huge
// plot fits the card and a small icon renders at its own size. Bytes that will not decode fire the
// img's error event into onDecodeFail (armed before src, so no event can slip past), where the open
// viewer swaps in its failure pane — the caller owns the pane; this stays a pure element builder.
function imgBlock(objUrl: string, path: string, onDecodeFail: () => void): HTMLElement {
  const box = el("div", "fileview-imgbox");
  const img = el("img", "fileview-img") as HTMLImageElement;
  img.addEventListener("error", onDecodeFail, { once: true });
  img.src = objUrl;
  img.alt = path;
  box.appendChild(img);
  return box;
}

// The PDF body mirrors the lightbox's treatment exactly (openLightbox's pdf arm, preview.ts): the
// browser's own viewer in a PLAIN iframe — className, src, title, nothing more — aimed at the
// already-fetched bytes instead of a second network fetch.
function pdfBlock(objUrl: string, path: string): HTMLElement {
  const frame = el("iframe", "fileview-frame") as HTMLIFrameElement;
  frame.src = objUrl;
  frame.title = path;
  return frame;
}

/** Bind the pane's WS poster and route saveFile + fileGitLink replies back to the open viewer.
 *  Called once, from the pane's boot (render.ts, feed.ts and files.ts: any document, one mechanism);
 *  every reply is reqId-guarded so one landing after a close or a replace-open touches nothing. The
 *  viewFile branch honors a shell's relay of a chat file-link click: the Files pane is its receiver
 *  (kernel.py's landing shell forwards the click there with the session's identity), and a document
 *  with a relay contract of its own passes `onRelay` and takes the relayed message whole instead of
 *  the plain open (files.ts caches the identity for its chip and keeps its recent list). */
export function initFileView(poster: (m: Record<string, unknown>) => void,
                             onRelay?: (m: { path: string; sid?: unknown; identity?: unknown; frag?: unknown }) => void): void {
  post = poster;
  window.addEventListener(HOST_TO_VIEWER, (e: Event) => {
    const m = (e as CustomEvent).detail || {};
    if (m.romp === "composerPending" && submitHooks && m.sid === submitHooks.sid) {
      submitHooks.count(Number(m.n) || 0);
    } else if (m.romp === "noteStaged" && typeof m.createId === "string" && cmtHooks.get(m.createId)?.sid === m.sid) {
      const h = cmtHooks.get(m.createId)!; cmtHooks.delete(m.createId);
      h.landed();
    }
  });
  window.addEventListener("message", (e: MessageEvent) => {
    const m = e.data;
    if (!m) return;
    if (m.romp === "viewFile" && typeof m.path === "string" && m.path) {
      if (onRelay) { onRelay(m); return; }   // this document's own contract (the Files pane) takes the message whole
      openFileView(m.path, typeof m.sid === "string" ? m.sid : null);
    } else if (m.type === "fileGitLink" && gitHooks && m.reqId === gitHooks.reqId) {
      const h = gitHooks; gitHooks = null;
      h.apply(String(m.url || ""), String(m.reason || ""));
    } else if (m.type === "fileSaved" && editHooks && m.reqId === editHooks.reqId) {
      const h = editHooks; editHooks = null;
      h.saved(String(m.mtimeNs || ""));
    } else if (m.type === "fileSaveFailed" && editHooks && m.reqId === editHooks.reqId) {
      const h = editHooks; editHooks = null;
      h.failed(String(m.error || "the save failed"));
    } else if (m.type === "commentCreated" && !m.uuid && typeof m.createId === "string" && cmtHooks.has(m.createId)
               && cmtHooks.get(m.createId)!.sid === m.id) {
      const h = cmtHooks.get(m.createId)!; cmtHooks.delete(m.createId);
      h.landed();
    } else if (m.type === "commentCreateFailed" && !m.uuid && m.createId && landedCreates.has(String(m.createId))) {
      const undo = landedCreates.get(String(m.createId))!;
      landedCreates.delete(String(m.createId));
      undo("the thread did not start, so it was removed: " + String(m.text || "no reason given"));
    } else if (m.type === "unknownOp" && m.op === "commentCreate") {
      failHostBoxes("this session's host runs an older romp that cannot take comments from the file viewer");
    } else if (m.type === "commentCreateFailed" && !m.uuid && typeof m.createId === "string" && cmtHooks.has(m.createId)
               && cmtHooks.get(m.createId)!.sid === m.id) {
      // A TRANSIENT refusal is anchor-parse lag, which retrying resolves — say so rather than
      // handing back the kernel's own wording for a state that is about to pass.
      const h = cmtHooks.get(m.createId)!; cmtHooks.delete(m.createId);
      h.failed(m.transient
        ? "the transcript is still catching up — send it again in a moment"
        : String(m.text || "the comment was refused"));
    } else if (m.type === "warn" && typeof m.sid !== "string" && (editHooks || cmtHooks.size)) {
      // A federation drop (the session's host unreachable) answers a saveFile with a warn instead
      // of a reply — the feed page renders no toasts, so without this the button spins forever
      // (the same hole the browse overlay closed for listDir). A refused comment thread arrives the
      // same way, carrying the reason the kernel refused it (an SDK-less session has nothing to fork).
      // A warn carrying a session id answers a send INTO that session (the kernel's refusal of a slash command a
      // Codex session cannot take, broadcast to every chat pane when no socket carried it), never this save or
      // this comment: mid-save it read as the save failing. A save's own failure names no session.
      if (editHooks) {
        const h = editHooks; editHooks = null;
        h.failed(String(m.text || "the session's host is not answering — the save was not sent"));
      } else {
        failHostBoxes(String(m.text || "the session's host is not answering — the comment was not sent"));
      }
    }
  });
  // A socket drop mid-save loses the ack, and the frame itself may or may not have reached the
  // kernel — the honest answer is to say exactly that and re-arm Save: a save that DID land will
  // refuse the retry as "changed on disk", and Reload resolves it from there. Keying on the shim's
  // own drop event (not a timer) is the house rule; the browse overlay set the precedent.
  window.addEventListener("romp:wsdown", () => {
    failHostBoxes("the connection dropped before the kernel ruled on this thread — it may or may "
      + "not have landed; check the session's threads before sending it again");
    if (!editHooks) return;
    const h = editHooks; editHooks = null;
    h.failed("the connection dropped mid-save — it may or may not have landed; "
      + "Save again once the connection returns (a save that DID land will refuse as changed-on-disk)");
  });
  // A drop while the GitHub ask is out loses its reply (the frame went; nothing re-sends it), and the
  // placeholder would pulse for the rest of the open. The socket's RETURN is the event that re-asks —
  // same reqId, so a first reply that was merely late and the second are one answer (the browse
  // overlay's re-ask on romp:wsup is the precedent). A read-only query: asking twice costs nothing.
  window.addEventListener("romp:wsup", () => { if (gitHooks) gitHooks.ask(); });
}
