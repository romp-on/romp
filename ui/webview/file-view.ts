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
import { marked } from "marked";
import DOMPurify from "dompurify";
import { fileUrl } from "./preview";
import { kernelUrl } from "./media";
import { quoteSrcLabel } from "./docreview";

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

// ── raw-mode editing (the file browser's slice 2, the user 2026-08-14) ─────────────────────────────
// The save op rides the WS poster the pane's boot hands initFileView; replies route back to the OPEN
// viewer through these module-level hooks (the viewer itself is a per-open closure).
let post: (m: Record<string, unknown>) => void = () => { /* bound by initFileView */ };
let saveSeq = 0;
let editHooks: { reqId: number; saved: (mtimeNs: string) => void; failed: (err: string) => void } | null = null;
// Set by the open viewer: returns false to VETO a close (an editor holding unsaved changes asks
// first). The guard must live in closeFileView itself, because the browser overlay and the Escape
// handler both close through it without knowing an edit is in progress.
let closeGuard: (() => boolean) | null = null;

// ── viewer action registry (the user 2026-08-22) ── INTERNAL SEAM, no compatibility promise:
// reshape freely. Anything acting on the OPEN file declares itself here instead of hand-wiring into
// openFileView's action row, where every file-viewer change used to collide. mount() runs once per
// open and returns the action's element for the row (or null to sit this file out); an action that
// answers asynchronously (the GitHub link's kernel ask) mounts hidden and reveals itself when its
// reply lands. Ordering is registration order, after the built-ins.
export interface FileViewActionCtx { path: string; sid: string | null; }
export interface FileViewAction { id: string; mount: (ctx: FileViewActionCtx) => HTMLElement | null; }
const fileViewActions: FileViewAction[] = [];
export function registerFileViewAction(a: FileViewAction): void {
  if (!fileViewActions.some((x) => x.id === a.id)) fileViewActions.push(a);
}

// ── the GitHub link (the user 2026-08-15) — the registry's first entry ─────────────────────────────
// An anchor, not a button: the browser owns opening a new tab. Hidden until the OWNING kernel answers
// the lazy fileGitLink ask with a real URL — an untracked file, a non-repo path, or a non-GitHub
// origin all honestly have no link, and it simply never appears. One question per open, reqId-guarded.
let gitSeq = 0;
let gitHooks: { reqId: number; apply: (url: string) => void } | null = null;
registerFileViewAction({
  id: "github-link",
  mount({ path, sid }) {
    const gh = el("a", "fileview-btn fileview-gh") as HTMLAnchorElement;
    gh.textContent = "GitHub ↗";
    gh.target = "_blank"; gh.rel = "noopener";
    gh.hidden = true;
    gitHooks = {
      reqId: ++gitSeq,
      apply: (url) => {
        if (!url) return;
        gh.href = url;
        gh.title = url;                            // the full URL one hover away
        gh.hidden = false;
      },
    };
    post({ type: "fileGitLink", path, sid: sid || undefined, reqId: gitSeq });
    return gh;
  },
});

// ── quote a passage into the composer (the user 2026-08-23, the three-verbs consolidation) ────────
// Selecting text in the viewer seeds the SAME labeled quote chip a VS Code editor highlight does:
// the selection posts to our own window in the editorSelection shape, so render.ts's existing
// handler owns the chip end to end (no import cycle — the browseFiles precedent), labeled path:line
// via quoteSrcLabel. From there the flow is the chat's own: type a note (or none), Stage, keep
// going, send once. This REPLACED the viewer's separate review layer — the per-file comment store
// (romp:fileviewComments), the painted marks, and the one-shot Submit that assembled a message —
// because batching notes for one hand-off is exactly what quote chips + ⌘⏎ staging already do,
// and "comment" now means only the transcript's live threads.

// The retired store's data would otherwise sit in localStorage forever on every browser that
// ever commented — sweep it on load.
try { localStorage.removeItem("romp:fileviewComments"); } catch { /* storage may be denied */ }

export function closeFileView(): void {
  const wrap = document.getElementById("romp-fileview");
  if (!wrap) return;
  if (closeGuard && !closeGuard()) return;   // unsaved edits, and the user chose to keep them
  closeGuard = null;
  editHooks = null;
  gitHooks = null;                                     // a reply landing after the close decorates nothing
  wrap.remove();
  document.body.classList.remove("fileview-open");
}

/** Show `path` in a modal over this pane. Re-opening replaces whatever is up — never stacks. */
export function openFileView(path: string, sid?: string | null): void {
  // The replace path bypasses closeFileView, so it needs the same dirty ask: opening file B over an
  // edited-but-unsaved file A must not silently eat A's buffer.
  if (document.getElementById("romp-fileview") && closeGuard && !closeGuard()) return;
  closeGuard = null;
  editHooks = null;
  gitHooks = null;                                     // the replace path skips closeFileView — same drop
  document.getElementById("romp-fileview")?.remove();
  // backdrop (the whole overlay carries the id every open/closed check targets) + the ~95% card.
  // The backdrop treatment matches the lightbox: dimmed, click outside the card closes, content
  // clicks don't (the user 2026-08-15: it must be obvious the chat is still right behind it).
  const wrap = el("div");
  wrap.id = "romp-fileview";
  wrap.onclick = (ev) => { if (ev.target === wrap) closeFileView(); };
  const box = el("div", "fileview");
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
  const acts = el("div", "fileview-acts");

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
  let editing = false;
  let dirty = false;
  let eolCRLF = false;                        // the file's dominant line ending — textareas normalize
  //   CRLF→LF on assignment, so an untouched CRLF file would otherwise save with every ending rewritten
  let ta: HTMLTextAreaElement | null = null;   // the FALLBACK surface (and the buffer pre-CodeMirror)
  let cm: { value(): string; focus(): void; destroy(): void } | null = null;   // the CodeMirror handle when mounted
  const bufValue = (): string | null => (cm ? cm.value() : ta ? ta.value : null);   // whichever surface owns the buffer
  const isMd = langFor(path) === "markdown";  // .md/.markdown — the only kind with a Rendered form
  const segBtns: Array<["rendered" | "raw", HTMLButtonElement]> = [];
  if (isMd) {
    for (const mode of ["rendered", "raw"] as const) {
      const b = el("button", "fileview-btn") as HTMLButtonElement;
      b.type = "button";
      b.textContent = mode === "rendered" ? "Rendered" : "Raw";
      b.title = mode === "rendered" ? "The prose the markdown means" : "The file's actual bytes";
      b.addEventListener("click", () => { fmt.md = mode; saveFmt(fmt); renderBody(); });
      segBtns.push([mode, b]);
      acts.appendChild(b);
    }
  }

  // ── edit (the raw-mode slice) ── exactly what raw mode can show is what Edit can touch: the
  // button arms only when the kernel served text/plain WITH a Last-Modified to anchor the save's
  // conflict floor (an old remote kernel that mirrors neither gets no Edit rather than an unguarded
  // one). Markdown edits from its Raw view — what you edit is what raw shows.
  const editBtn = el("button", "fileview-btn") as HTMLButtonElement;
  editBtn.type = "button"; editBtn.textContent = "Edit"; editBtn.title = "Edit this file in place";
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
    try { on = !!(await (await fetch(kernelUrl("/version"), { cache: "no-store" })).json()).fileEditing; } catch { /* ask below */ }
    if (on) return true;
    if (!window.confirm(
      "Allow editing files from the dashboard?\n\n" +
      "Saves write straight to disk on the file's machine — and this applies on every machine " +
      "connected here. A session working in that folder is told when you edit under it.\n\n" +
      "You can turn this off later in the settings gear.")) return false;
    post({ type: "setFileEditing", enabled: true });
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
  acts.appendChild(editBtn); acts.appendChild(saveBtn); acts.appendChild(cancelBtn);
  // Registered actions render after the built-ins — the registry walk is the ONE place row
  // conventions live (see registerFileViewAction above). The GitHub link mounts here.
  for (const a of fileViewActions) {
    const n = a.mount({ path, sid: sid || null });
    if (n) acts.appendChild(n);
  }

  // ── download (the user 2026-08-09) ── Any linked file can be SAVED, including everything the pane
  // cannot show: the kernel's ?download=1 serves anything on disk (the rationale lives with
  // _file_download in kernel.py). Same-origin and cookie-authed like the view fetch, and
  // federation-aware for free — fileUrl already routes a remote session's file through the relay.
  const dlUrl = fileUrl(path, sid) + "&download=1";
  const dl = el("button", "fileview-btn") as HTMLButtonElement;
  dl.type = "button"; dl.textContent = "Download"; dl.title = "Save this file to your device";
  dl.addEventListener("click", () => startDownload(dlUrl, dl));
  acts.appendChild(dl);

  const copy = el("button", "fileview-btn") as HTMLButtonElement;
  copy.type = "button"; copy.textContent = "Copy path"; copy.title = path;
  copy.addEventListener("click", () => {
    navigator.clipboard?.writeText(path).then(
      () => { copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy path"; }, 1200); },
      () => { copy.textContent = "Copy failed"; });
  });
  const close = el("button", "fileview-btn fileview-close") as HTMLButtonElement;
  close.type = "button"; close.textContent = "✕"; close.title = "Close (Esc)";
  close.setAttribute("aria-label", "Close the file viewer");
  close.addEventListener("click", closeFileView);
  acts.appendChild(copy); acts.appendChild(close);
  bar.appendChild(name); bar.appendChild(acts);

  const body = el("div", "fileview-body");
  // Per the loading-state rule the first thing up is the romp loader, not a blank pane — a file coming
  // over an ssh tunnel to a phone is a real wait.
  const load = el("div", "fileview-load");
  load.innerHTML = '<img src="/media/romp-swirl-glyph.svg" alt=""><span>romp</span>'
    + '<i class="fileview-dot"></i><i class="fileview-dot"></i><i class="fileview-dot"></i>';
  body.appendChild(load);

  box.appendChild(bar); box.appendChild(body);
  wrap.appendChild(box);
  document.body.appendChild(wrap);

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
    saveBtn.hidden = !editing;
    cancelBtn.hidden = !editing;
    if (text === null || editing) return;   // loading, or the textarea owns the body right now
    body.replaceChildren(rendered ? mdBlock(text) : codeBlock(text, path, true));   // long lines always soft-wrap (the user 2026-08-24)
  };

  // Selection → labeled quote chip (the user 2026-08-23): mouseup is the gesture's settle point.
  // The chip behaves exactly like a VS Code editor highlight's — one live source-labeled chip,
  // replaced by the next selection, persisting until sent, staged, or ✕'d — because it IS that
  // chip: render.ts's editorSelection handler seeds it. The post carries THIS viewer's sid, so the
  // chip lands in the session the file was opened FOR even if the active tab changed while the
  // modal was up (the 2026-08-19 routing rule: the gesture's session, never activeId-at-gesture).
  let seedSeq = 0;                                 // last gesture wins if two fresh reads race
  box.addEventListener("mouseup", () => {
    if (editing) return;   // CodeMirror selections are edit gestures, not quotes
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.anchorNode || !box.contains(sel.anchorNode)) return;
    const picked = sel.toString().trim();
    if (!picked) return;
    // The label's line is minted NOW, not at open: agents edit these same trees, so the open-time
    // snapshot's numbering may have quietly moved. Anchor against a fresh read; a FAILED re-read
    // falls back to the snapshot rather than fabricating drift nobody observed (the old Submit
    // guard's rule). quoteSrcLabel itself degrades to the bare path when the passage cannot be
    // honestly found in whichever bytes it gets.
    const seq = ++seedSeq;
    fetch(fileUrl(path, sid), { cache: "no-store" })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .catch(() => text)
      .then((doc) => {
        if (seq !== seedSeq) return;
        try { window.postMessage({ type: "editorSelection", text: picked, sid: sid || undefined, src: quoteSrcLabel(path, doc, picked) }, "*"); }
        catch { /* messaging our own window cannot really fail */ }
      });
  });

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
      .map((n) => (n as HTMLScriptElement).src).find((u) => /\/(render|feed)\.js/.test(u));
    if (!self) return rej(new Error("no bundle script tag to derive the editor chunk URL from"));
    const sc = document.createElement("script");
    sc.src = self.replace(/\/(render|feed)\.js/, "/editor-chunk.js");
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
      document.getElementById("fileview-save-err")?.remove();
      const bar2 = el("div", "fileview-err");   // loud: say the editor is degraded, never pretend
      bar2.id = "fileview-save-err";
      bar2.textContent = String(err && (err as Error).message || err) + " — editing in the plain fallback editor.";
      enterFallback();
      body.prepend(bar2);
    });
  };
  const exitEdit = () => {
    editing = false; dirty = false; ta = null;
    cm?.destroy(); cm = null;
    editHooks = null;                           // a cancelled save's late ack must not touch a NEW session
    saveBtn.disabled = false; saveBtn.textContent = "Save";
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
        // Loud, in place, and the BUFFER SURVIVES: the error bar sits above the textarea. A conflict
        // (the disk moved — an agent wrote it) offers Reload, which re-opens fresh — behind the same
        // discard confirm, so the user's edits are never thrown away silently (never a merge UI).
        document.getElementById("fileview-save-err")?.remove();
        const bar2 = el("div", "fileview-err");
        bar2.id = "fileview-save-err";
        bar2.textContent = err;
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
        body.prepend(bar2);
      },
    };
    post({ type: "saveFile", path, sid: sid || undefined, content, baseMtimeNs: mtimeNs, reqId: saveSeq });
  };
  renderBody();   // buttons take their initial state now; the loader stays up until the fetch lands

  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || !document.getElementById("romp-fileview")) return;
    e.preventDefault();
    if (editing) {                              // Escape peels edit mode first, never the whole viewer
      if (confirmDiscard()) exitEdit();
      return;
    }
    closeFileView();
    document.removeEventListener("keydown", onKey);
  };
  document.addEventListener("keydown", onKey);

  fetch(fileUrl(path, sid), { cache: "no-store" }).then((r) => {
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
    return r.text();
  }).then((t) => {
    if (!document.getElementById("romp-fileview")) return;    // closed while it was in flight
    text = t;
    renderBody();
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
}

// Which fetch failures still deserve a Download offer? Exactly the ones that mean the file EXISTS:
// 413 (too large to render) and 415 (on disk but not viewable — a .zip, a binary named like text).
// A 404 is genuinely missing, and gets nothing.
function offersDownload(status: number | undefined): boolean {
  return status === 413 || status === 415;
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
  const was = btn.textContent;
  btn.textContent = "Downloading…";
  setTimeout(() => { btn.textContent = was; }, 1500);
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
    pre.appendChild(code);
    wrap.appendChild(pre);
    return wrap;
  }
  const gutter = el("div", "fileview-gutter");
  gutter.textContent = lines.map((_, i) => String(i + 1)).join("\n");
  gutter.setAttribute("aria-hidden", "true");
  if (hl !== null) code.innerHTML = hl; else code.textContent = text;
  pre.appendChild(code);
  wrap.appendChild(gutter); wrap.appendChild(pre);
  return wrap;
}

// Markdown rendered as the prose it means (the user 2026-08-09: Rendered is the default, Raw one click
// away). The file is arbitrary bytes off a disk and marked emits raw HTML verbatim, so — exactly like the
// chat's md() in render.ts — the output goes through DOMPurify before it ever reaches .innerHTML: an
// <img onerror> or a javascript: href in a README must never run in the dashboard.
function mdBlock(text: string): HTMLElement {
  const box = el("div", "fileview-md");
  try {
    const dirty = marked.parse(text) as string;
    // html + svg, in lockstep with the chat's md(): KaTeX draws stretchy glyphs (\sqrt radicals,
    // wide accents) as inline <svg> even in html output, and the html-only profile ate them.
    box.innerHTML = DOMPurify.sanitize(dirty, { USE_PROFILES: { html: true, svg: true }, ADD_DATA_URI_TAGS: ["img"] });
  } catch {
    box.textContent = text;                            // a marked bug must never cost the content
  }
  // Links open a NEW tab: the viewer lives inside the chat pane's document, and letting a README link
  // navigate it away would silently eat the chat until a reload.
  box.querySelectorAll("a[href]").forEach((a) => {
    (a as HTMLAnchorElement).target = "_blank";
    (a as HTMLAnchorElement).rel = "noopener";
  });
  // Fenced blocks: highlight only a language the fence NAMES and this bundle registers — the same
  // no-guessing rule as langFor; an unnamed block stays plain rather than being painted at random.
  box.querySelectorAll("pre code").forEach((node) => {
    const codeEl = node as HTMLElement;
    const lang = (codeEl.className.match(/language-([\w-]+)/) || [])[1];
    if (!lang || !hljs.getLanguage(lang)) return;
    try {
      codeEl.innerHTML = hljs.highlight(codeEl.textContent || "", { language: lang }).value;
      codeEl.classList.add("hljs");
    } catch { /* leave plain */ }
  });
  return box;
}

/** Bind the pane's WS poster and route saveFile + fileGitLink replies back to the open viewer.
 *  Called once, from the pane's boot (render.ts and feed.ts today — either document, one mechanism);
 *  every reply is reqId-guarded so one landing after a close or a replace-open touches nothing. The
 *  viewFile branch honors a shell's relay of a chat file-link click — nothing sends it since the
 *  viewer moved into the chat document, but a not-yet-reloaded shell page still might, and honoring
 *  it costs nothing. */
export function initFileView(poster: (m: Record<string, unknown>) => void): void {
  post = poster;
  window.addEventListener("message", (e: MessageEvent) => {
    const m = e.data;
    if (!m) return;
    if (m.romp === "viewFile" && typeof m.path === "string" && m.path) {
      openFileView(m.path, typeof m.sid === "string" ? m.sid : null);
    } else if (m.type === "fileGitLink" && gitHooks && m.reqId === gitHooks.reqId) {
      const h = gitHooks; gitHooks = null;
      h.apply(String(m.url || ""));
    } else if (m.type === "fileSaved" && editHooks && m.reqId === editHooks.reqId) {
      const h = editHooks; editHooks = null;
      h.saved(String(m.mtimeNs || ""));
    } else if (m.type === "fileSaveFailed" && editHooks && m.reqId === editHooks.reqId) {
      const h = editHooks; editHooks = null;
      h.failed(String(m.error || "the save failed"));
    } else if (m.type === "warn" && editHooks) {
      // A federation drop (the session's host unreachable) answers a saveFile with a warn instead
      // of a reply — the feed page renders no toasts, so without this the button spins forever
      // (the same hole the browse overlay closed for listDir).
      const h = editHooks; editHooks = null;
      h.failed(String(m.text || "the session's host is not answering — the save was not sent"));
    }
  });
  // A socket drop mid-save loses the ack, and the frame itself may or may not have reached the
  // kernel — the honest answer is to say exactly that and re-arm Save: a save that DID land will
  // refuse the retry as "changed on disk", and Reload resolves it from there. Keying on the shim's
  // own drop event (not a timer) is the house rule; the browse overlay set the precedent.
  window.addEventListener("romp:wsdown", () => {
    if (!editHooks) return;
    const h = editHooks; editHooks = null;
    h.failed("the connection dropped mid-save — it may or may not have landed; "
      + "Save again once the connection returns (a save that DID land will refuse as changed-on-disk)");
  });
}
