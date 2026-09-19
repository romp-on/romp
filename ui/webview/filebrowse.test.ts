// The file BROWSER in the FEED pane (the user 2026-08-14): breadcrumb over one directory's entries,
// riding the listDir WS op with the dirComplete staleness protocol, opening files through the
// existing viewer. Source pins (no jsdom for these modules), the repo convention; the viewer's veto of a
// browse (a kept editor under openFileBrowse) runs for real over a DOM stand-in at the end of the file.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const BROWSE = web("file-browse.ts");
const VIEW = web("file-view.ts");
const RENDER = web("render.ts");
const FEED = web("feed.ts");
const FEED_CSS = web("feed.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the browser is the viewer's sibling MODAL, one z layer BENEATH it", () => {
  // beneath by design: a file opened from a listing overlays the listing, and closing it returns
  // there (the viewer is a MODAL since 2026-08-15). The browser joined the same centered-card
  // treatment 2026-09-04 (the user, superseding the 2026-08-24 pane takeover they came to find
  // odd): backdrop wears the id + the dim, the card wears the modal vocabulary.
  assert.match(FEED_CSS, /#romp-filebrowse \{ position: fixed; inset: 0; z-index: 890; background: var\(--overlay-dim\);/);
  assert.match(FEED_CSS, /\.filebrowse \{ width: min\(720px, 95%\); height: min\(760px, 95%\);/);
  assert.match(FEED_CSS, /#romp-fileview \{ position: fixed; inset: 0; z-index: 1200;/);
  assert.match(BROWSE, /wrap\.id = "romp-filebrowse";/);
  // backdrop clicks close; content clicks never do (the lightbox contract) — and a drag that STARTED inside
  // the card and ended over the dim is not a backdrop click: the close is armed on pointerdown and fires only
  // when both ends were on the dim (review fold on #924, 2026-09-07)
  assert.match(BROWSE, /wrap\.addEventListener\("pointerdown", \(e\) => \{ downOnDim = e\.target === wrap; \}\);/);
  assert.match(BROWSE, /wrap\.onclick = \(ev\) => \{ const close = downOnDim && ev\.target === wrap; downOnDim = false; if \(close\) closeFileBrowse\(\); \};/,
    "backdrop clicks close; content clicks and card-to-dim drags never do");
  assert.match(BROWSE, /document\.body\.classList\.add\("filebrowse-open"\);/);
});

test("the close contract is ownership-aware: the restore fires exactly once", () => {
  // the viewer is a modal over whatever document opened it (2026-08-15): it never touches the feed
  // pane, so it participates in NO restore protocol at all — no close message, nothing to suppress
  assert.doesNotMatch(VIEW, /viewFileClosed/, "nothing to restore → nothing to announce");
  // the browser is the ONE overlay that juggles the pane, so its close alone does the restore
  assert.match(BROWSE, /window\.parent\.postMessage\(\{ romp: "browseClosed" \}, "\*"\);/);
  assert.match(KERNEL, /if\(m\.romp==='browseClosed'&&window\.__rompFeedWasOff\)/);
});

test("the shell relays browseFiles: pane forward, remembered, phone tab", () => {
  assert.match(KERNEL, /if\(m\.romp==='browseFiles'\)\{var bf=document\.getElementById\('f-feed'\);/);
  const relay = KERNEL.split("if(m.romp==='browseFiles')")[1].split("if(m.romp==='browseClosed'")[0];
  assert.ok(relay.includes("window.__rompFeedWasOff=true;"), "a pane turned on for the browser is remembered");
  assert.ok(relay.includes("window.__rompMobileTab&&window.__rompMobileTab('feed')"), "phone: one pane at a time");
  assert.ok(relay.includes("postMessage({romp:'browseFiles',path:m.path,sid:m.sid}"), "forwarded into the feed iframe");
});

test("the listing rides the dirComplete staleness protocol: reqId stale-drop, in-flight coalescing", () => {
  assert.match(BROWSE, /post\(\{ type: "listDir", path, sid: curSid \|\| undefined, reqId: \+\+reqSeq, hidden: showHidden \}\);/);
  assert.match(BROWSE, /if \(m\.reqId !== reqSeq\) return;/);
  // no debounce: ONE in-flight ask, the newest navigation queued behind it — the round-trip is the pacing
  assert.match(BROWSE, /if \(inflight\) \{ queued = path; return; \}/);
  assert.match(BROWSE, /if \(queued !== null\) \{ const q = queued; queued = null; ask\(q\); return; \}/);
});

test("the kernel's listDir answers with the echo, the entries, and LOUD path-naming errors", () => {
  assert.match(KERNEL, /elif msg and msg\.get\("type"\) == "listDir":/);
  assert.match(KERNEL, /"type": "dirListing", "reqId": msg\.get\("reqId"\), "host": ""/);
  assert.match(KERNEL, /def _list_dir\(raw, sid=None, hidden=False, limit=DIR_LIST_MAX\):/);
  assert.match(KERNEL, /cannot list %s: not a directory/);
  assert.match(KERNEL, /DIR_LIST_MAX = 500/);
  // resolution is /file's own, so a listed path feeds the /file URL builder unchanged
  assert.match(KERNEL, /p = _resolve_open_path\(str\(raw or ""\), sid\)/);
});

test("waiting shows the romp loader, never a blank or a frozen listing", () => {
  assert.match(BROWSE, /el\("div", "fileview-load"\)/);
  assert.match(BROWSE, /romp-swirl-glyph\.svg/);
});

test("rows carry an honest verdict: download-only files are dimmed and download on click", () => {
  // server-side `viewable` (the same tables /file applies) marks the rows up front
  assert.match(BROWSE, /const dlOnly = en\.viewable === false;/);
  assert.match(BROWSE, /row\.dataset\.act = dlOnly \? "dl" : "file";/);
  assert.match(BROWSE, /if \(row\.dataset\.act === "dl"\) startDownload\(p\);/);
  assert.match(FEED_CSS, /\.fb-dlonly \.fb-name \{ color: var\(--dim\); \}/);
  // viewable files open through the EXISTING viewer — one leaf open action for the whole dashboard
  // with the row's click (a modified click on a PDF takes a browser tab), and the hosting document's own open
  // beneath the gesture when it has one (BrowseHost.openFile, browse-route.test.ts)
  assert.match(BROWSE, /if \(row\.dataset\.act === "file"\) \{ openFileClick\(ev, p, curSid, openPick \?\? undefined\); return; \}/);
});

test("clicks are delegated to stable roots and the cap is stated in-band", () => {
  // rows rebuild per navigation, so the listener lives on the persistent list container
  assert.match(BROWSE, /list\.addEventListener\("click", \(ev\) => \{/);
  assert.match(BROWSE, /crumbs\.addEventListener\("click", \(ev\) => \{/);
  assert.match(BROWSE, /entries — the rest aren't shown"/);
  assert.match(BROWSE, /"empty directory"/, "an empty dir says so — never a blank");
});

test("Escape closes the TOPMOST surface only, and Backspace walks up", () => {
  // the browser's key handler stands down while the viewer exists above it
  assert.match(BROWSE, /if \(document\.getElementById\("romp-fileview"\)\) return;/);
  assert.match(BROWSE, /if \(e\.key === "Escape"\) \{ e\.preventDefault\(\); closeFileBrowse\(\); return; \}/);
  assert.match(BROWSE, /if \(e\.key === "Backspace" \|\| e\.key === "ArrowLeft"\) \{/);
});

test("every entry point is gated to where the click can land, and posts the one shell message", () => {
  // chat: openBrowse walks the file link's ladder at the click (file-route.ts browseRoute, browse-route.test.ts):
  // in place over the chat for "here", handed up naming the Files pane for "pane", nothing in VS Code
  assert.match(RENDER, /const route = browseRouteNow\(\);\n\s*if \(route === "editor"\) return;/);
  assert.match(RENDER, /if \(route === "here"\) \{ openFileBrowse\(path \|\| "\.", to\); return; \}/);
  assert.match(RENDER, /window\.parent\.postMessage\(\{ romp: "browseFiles", path: path \|\| "\.", sid: to, pane: "pane",/, "the pane route names its target");
  assert.match(RENDER, /initFileBrowse\(\(m\) => vscodeApi\?\.postMessage\(m\), \{\n\s*shellRestore: false,/, "the chat hosts its own browser instance, under its own contract");
  // tab right-click menu row: bottom of the menu, behind a divider, icon + sub-description
  assert.match(RENDER, /label: "Browse files",/);
  // feed card menu row rides canPreview (web only — the VS Code webview can't reach the kernel
  // origin), and sends only the sid — the kernel resolves "." against the session's cwd authoritatively
  assert.match(FEED, /openFileBrowse\("\.", it\.sid\)/);
  assert.match(FEED, /if \(canPreview\(\)\) items\.push\(\{ label: "Browse files", pick: \(\) => openFileBrowse\("\.", it\.sid\) \}\);/);
});

test("the statusline folder link BROWSES on the web; OS-open lives on its right-click (the user 2026-08-14)", () => {
  const SW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "status-widgets.ts"), "utf8");   // the folder link rule lives beside the folder widget since T409 (folderLink); render.ts's asFolderLink delegates
  assert.match(SW, /elem\.dataset\.act = web \? "browseFiles" : "openFolder";/);   // pane-local browse needs no shell (2026-08-24)
  assert.match(RENDER, /function asFolderLink\(elem: HTMLElement, cwd: string, sid\?: string\): void \{\n\s*folderLink\(elem, cwd, sid\);/);
  assert.match(SW, /click to browse this folder/);
  // the demoted OS-open: one document-level contextmenu on folder links, posting the old openFolder
  assert.match(RENDER, /label: "Open folder window", sub: "on the machine the session runs on",/, "the folder link's right-click, on the shared builder too (v0.16.0)");
  assert.match(RENDER, /browseFiles: \(el\) => \{/, "the body delegate carries the new act");
});

test("the viewer's directory half is the click INTO the browser — no import cycle", () => {
  assert.match(VIEW, /dir\.classList\.add\("fileview-dir-link"\);/);
  // posted to our OWN window: initFileBrowse listens on the same channel the shell relays into
  assert.match(VIEW, /window\.postMessage\(\{ romp: "browseFiles", path: path\.slice\(0, cut\) \|\| "\/", sid \}, "\*"\);/);
  assert.match(BROWSE, /m\.romp === "browseFiles" && typeof m\.path === "string"/);
  assert.match(FEED_CSS, /\.fileview-dir-link \{ cursor: pointer; \}/);
});

test("the feed boots both overlays side by side", () => {
  assert.match(FEED, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/);
  assert.match(FEED, /initFileBrowse\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/);
});

// ── review-driven hardening (2026-08-14): the adversarial pass found eight real defects; these pins
// hold their fixes in place ──

test("opening the browser CLOSES an open viewer — the stack is one-directional", () => {
  // a browser painted under the opaque viewer was a dead click, and viewer-first registration made
  // one Escape close both overlays; closing the viewer at browse-open kills both failure modes
  assert.match(BROWSE, /import \{ closeFileView, openFileClick \} from "\.\/file-view";/);   // the row opens through the gesture reader only
  assert.match(BROWSE, /if \(document\.getElementById\("romp-fileview"\)\) closeFileView\(\);/);
});

test("closeFileBrowse unbinds the keydown handler and resets the protocol latch", () => {
  // a ✕-close sees no keydown, so lazily self-removing handlers stacked across reopens (double-moving
  // arrows); and a surviving inflight wedged the reopened browser behind a reply that never comes
  assert.match(BROWSE, /if \(onKeyRef\) \{ document\.removeEventListener\("keydown", onKeyRef\); onKeyRef = null; \}/);
  const close = BROWSE.split("export function closeFileBrowse")[1].split("function human")[0];
  assert.ok(close.includes("inflight = false;"), "the latch resets with the overlay");
  assert.ok(close.includes("queued = null;"));
  assert.ok(close.includes('if (document.getElementById("fb-ctx")) closeContextMenu();'), "a row menu never outlives its listing (closed through the shared builder)");
});

test("a reply un-blocks the protocol UNCONDITIONALLY, before the stale check (the completer's rule)", () => {
  assert.match(BROWSE, /inflight = false;\n  if \(queued !== null\) \{ const q = queued; queued = null; ask\(q\); return; \}\n  if \(m\.reqId !== reqSeq\) return;/);
});

test("a lost reply recovers on the socket's own events, and a federation drop fails loudly", () => {
  // the drop and the return are events the pane shim already dispatches — recovery keys on them,
  // never a timer; a remote host's tunnel being down answers with a warn instead of a dirListing
  assert.match(BROWSE, /window\.addEventListener\("romp:wsdown", \(\) => \{/);
  assert.match(BROWSE, /window\.addEventListener\("romp:wsup", \(\) => \{/);
  assert.match(BROWSE, /needResync = true;/);
  assert.match(BROWSE, /m\.type === "warn" && typeof m\.sid !== "string" && inflight && document\.getElementById\("romp-filebrowse"\)/, "only a warn naming NO session ends the listing: one that names a session answers a send into that session (a Codex slash refusal broadcast to every chat pane, 2026-09-19), never this listing");
});

test("Escape peels the TOPMOST layer: row menu, then viewer, then browser", () => {
  const key = BROWSE.split("const onKey =")[1].split("document.addEventListener(\"keydown\", onKey)")[0];
  assert.match(key, /if \(document\.getElementById\("fb-ctx"\) \|\| \(e\.key === "Escape" && e\.defaultPrevented\)\) return;/, "every key yields to an open row menu (round two of the tidy: the arrows once walked the listing under the card)");
  const ctxAt = key.indexOf('getElementById("fb-ctx")');
  const viewAt = key.indexOf('getElementById("romp-fileview")');
  const closeAt = key.indexOf("closeFileBrowse()");
  assert.ok(ctxAt >= 0 && viewAt > ctxAt && closeAt > viewAt, "menu before viewer before browser");
  assert.match(FEED_CSS, /#fb-ctx \{ z-index: 950; \}/, "the menu draws over both overlays, not under them");
});

test("a re-invoke resyncs the Hidden control with the state it claims to show", () => {
  assert.match(BROWSE, /const hb = document\.getElementById\("fb-hidden"\);/);
  assert.match(BROWSE, /hb\.classList\.remove\("on"\); hb\.setAttribute\("aria-pressed", "false"\);/);
});

test("the kernel's parent field is read: home is not a ceiling, and errors keep a walkable trail", () => {
  assert.match(BROWSE, /curParent = m\.parent \?\? null;/);
  assert.match(BROWSE, /fb-crumb fb-crumb-up/, "the way above a ~-rooted trail is a visible crumb");
  assert.match(BROWSE, /const up = cs\.length >= 2 \? cs\[cs\.length - 2\]\.dataset\.path : curParent;/);
  assert.match(BROWSE, /renderError\(m\.error, m\.base, m\.parent\);/);
  // …and the kernel ships base/parent on error replies so a FIRST open that fails still has crumbs
  assert.match(KERNEL, /err_ctx = \{"base": _tilde\(p\),/);
});

test("the row menu carries the plan's full vocabulary: Copy path / Download / Open folder", () => {
  assert.match(BROWSE, /add\("Copy path", \(\) => \{ navigator\.clipboard\?\.writeText\(path\); \}\);/);
  assert.match(BROWSE, /if \(!isDir\) add\("Download", \(\) => startDownload\(path\)\);/);
  assert.match(BROWSE, /add\("Open folder window", \(\) => \{/);
});

// ── the tab-menu restructure (the user 2026-08-24) ───────────────────────────────────────────────
test("Browse files sits at the BOTTOM of the tab menu, behind a divider, wearing icon + sub-description", () => {
  const at = RENDER.indexOf("function showTabMenu");
  const menuBody = RENDER.slice(at, RENDER.indexOf("ctxMenuEl = showMenuCard(menu,", at));
  const browseAt = menuBody.indexOf('label: "Browse files"');
  assert.ok(browseAt > 0, "the item exists");
  // nothing else is added to the menu after the Browse row: it is the last thing before the card is shown
  assert.doesNotMatch(menuBody.slice(browseAt), /addMenuItem\(menu|addMenuSep\(menu\)|menu\.appendChild\(/, "the last row");
  assert.ok(menuBody.lastIndexOf("addMenuSep(menu);") < browseAt
            && menuBody.slice(0, browseAt).trimEnd().includes("addMenuSep(menu);"),
    "a divider immediately precedes it — a different kind of thing");
  assert.match(menuBody.slice(browseAt - 400, browseAt), /ctxIcon\("folder", false\)/, "the folder icon");
  assert.match(menuBody, /sub: "the session's working tree, " \+ \(where === "pane" \? "in the Files pane" : "in a viewer over this chat"\),/,
    "the standard sub-description line, naming where the listing will open (browse-route.test.ts)");
});

// ── the viewer's veto, run FOR REAL: a browse click while the discard confirm keeps the viewer ──────────
// openFileBrowse closes a viewer that is up, and the viewer's dirty-edit guard can refuse: the person answered
// the discard confirm with cancel. The click then stands down whole: no browser overlay beneath the kept viewer, no
// listing asked for, no keydown handler left bound, and a listing already beneath the viewer left as it was.
// Run over the real openFileBrowse and openFileView against a DOM stand-in (the file-view-text-size.test.ts
// idiom: a tree, attributes and dataset, bubbling events, a small selector matcher, localStorage, a fetch
// answering the kernel's headers). Synthetic fixtures only: a notes-api world under /tmp, placeholder session ids.
class Ev {
  target: El | null = null;
  currentTarget: El | null = null;
  defaultPrevented = false;
  stopped = false;
  ctrlKey: boolean; metaKey: boolean; button: number; key: string;
  constructor(public type: string, init: { ctrlKey?: boolean; metaKey?: boolean; button?: number; key?: string } = {}) {
    this.ctrlKey = !!init.ctrlKey; this.metaKey = !!init.metaKey; this.button = init.button ?? 0; this.key = init.key ?? "";
  }
  preventDefault(): void { this.defaultPrevented = true; }
  stopPropagation(): void { this.stopped = true; }
}
type Listener = (ev: Ev) => void;
type Part = { tag: string; id: string; classes: string[]; attrs: Array<[string, string | null]>; known: boolean };
const kebab = (k: string) => k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
/** One compound selector (`tag#id.class[attr="v"]`); a shape the matcher does not know fits nothing. */
function part(s: string): Part {
  const m = /^([a-zA-Z][\w-]*|\*)?(#[\w-]+)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/.exec(s);
  if (!m) return { tag: "", id: "", classes: [], attrs: [], known: false };
  const attrs: Array<[string, string | null]> = [];
  let known = true;
  for (const a of m[4].match(/\[[^\]]+\]/g) || []) {
    const am = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(a);
    if (am) attrs.push([am[1], am[2] ?? null]); else known = false;
  }
  return { tag: (m[1] || "").toLowerCase(), id: m[2] ? m[2].slice(1) : "", classes: (m[3].match(/\.[\w-]+/g) || []).map((c) => c.slice(1)), attrs, known };
}
class El {
  parentNode: El | null = null;
  childNodes: Array<El | string> = [];
  title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1; innerHTML = "";
  href = ""; target = ""; rel = ""; spellcheck = true; value = ""; src = ""; alt = ""; download = "";
  style: Record<string, string> = {};
  onclick: ((ev: Ev) => void) | null = null;
  private attrs = new Map<string, string>();
  private listeners: Array<{ type: string; fn: Listener; once: boolean }> = [];
  constructor(public tagName: string) {}
  get id(): string { return this.attrs.get("id") ?? ""; }
  set id(v: string) { this.attrs.set("id", v); }
  get className(): string { return this.attrs.get("class") ?? ""; }
  set className(v: string) { this.attrs.set("class", v.trim()); }
  private get classes(): string[] { return this.className.split(/\s+/).filter(Boolean); }
  classList = {
    add: (...c: string[]) => { const s = new Set(this.classes); for (const x of c) s.add(x); this.className = [...s].join(" "); },
    remove: (...c: string[]) => { const s = new Set(this.classes); for (const x of c) s.delete(x); this.className = [...s].join(" "); },
    toggle: (c: string, on?: boolean): boolean => { const want = on ?? !this.classes.includes(c); if (want) this.classList.add(c); else this.classList.remove(c); return want; },
    contains: (c: string) => this.classes.includes(c),
  };
  /** data-* through dataset, the way the browser stamps its rows (camelCase to kebab-case, as the DOM does). */
  dataset: Record<string, string | undefined> = new Proxy({} as Record<string, string | undefined>, {
    get: (_, k) => this.attrs.get("data-" + kebab(String(k))),
    set: (_, k, v) => { this.attrs.set("data-" + kebab(String(k)), String(v)); return true; },
    has: (_, k) => this.attrs.has("data-" + kebab(String(k))),
    deleteProperty: (_, k) => { this.attrs.delete("data-" + kebab(String(k))); return true; },
  });
  get textContent(): string { return this.childNodes.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v: string) { this.replaceChildren(...(v === "" ? [] : [v])); }
  /** Element children only, in order. */
  get children(): El[] { return this.childNodes.filter((c): c is El => c instanceof El); }
  get isConnected(): boolean { let n: El = this; while (n.parentNode) n = n.parentNode; return n === docBody; }
  private adopt(c: El | string): void { if (c instanceof El) { c.remove(); c.parentNode = this; } }
  appendChild<T extends El>(c: T): T { this.adopt(c); this.childNodes.push(c); return c; }
  prepend(...cs: Array<El | string>): void { for (const c of cs) this.adopt(c); this.childNodes.unshift(...cs); }
  insertBefore<T extends El>(c: T, ref: El | null): T {
    if (ref && !this.childNodes.includes(ref)) throw new Error("insertBefore: the reference node is not a child of this node");
    this.adopt(c);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  replaceChildren(...cs: Array<El | string>): void {
    for (const c of this.childNodes) if (c instanceof El) c.parentNode = null;
    this.childNodes = [];
    for (const c of cs) this.adopt(c);
    this.childNodes = [...cs];
  }
  remove(): void {
    const p = this.parentNode;
    if (!p) return;
    const i = p.childNodes.indexOf(this);
    if (i >= 0) p.childNodes.splice(i, 1);
    this.parentNode = null;
  }
  contains(n: El | null): boolean { for (let x: El | null = n; x; x = x.parentNode) if (x === this) return true; return false; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(type: string, fn: Listener, opts?: boolean | { once?: boolean; passive?: boolean; capture?: boolean }): void {
    this.listeners.push({ type, fn, once: typeof opts === "object" && !!opts.once });
  }
  removeEventListener(type: string, fn: Listener): void { this.listeners = this.listeners.filter((l) => !(l.type === type && l.fn === fn)); }
  /** Bubble `ev` from this element to the root: each element's listeners in registration order, then its onclick for a click. */
  dispatchEvent(ev: Ev): boolean {
    ev.target = this;
    for (let n: El | null = this; n && !ev.stopped; n = n.parentNode) {
      ev.currentTarget = n;
      for (const l of n.listeners.slice()) {
        if (l.type !== ev.type) continue;
        if (l.once) n.listeners = n.listeners.filter((x) => x !== l);
        l.fn.call(n, ev);
      }
      if (ev.type === "click" && n.onclick) n.onclick(ev);
    }
    return !ev.defaultPrevented;
  }
  click(): void { this.dispatchEvent(new Ev("click")); }
  focus(): void { doc.activeElement = this; }
  scrollIntoView(): void { /* inert */ }
  private fits(p: Part): boolean {
    if (!p.known) return false;
    if (p.tag && p.tag !== "*" && p.tag !== this.tagName.toLowerCase()) return false;
    if (p.id && p.id !== this.id) return false;
    if (!p.classes.every((c) => this.classes.includes(c))) return false;
    return p.attrs.every(([a, v]) => this.attrs.has(a) && (v === null || this.attrs.get(a) === v));
  }
  /** Comma groups of descendant chains, each link a compound. */
  matches(sel: string): boolean {
    return sel.split(",").some((group) => {
      const chain = group.trim().split(/\s+/).filter(Boolean).map(part);
      if (!chain.length || !this.fits(chain[chain.length - 1])) return false;
      let k = chain.length - 2;
      for (let a = this.parentNode; a && k >= 0; a = a.parentNode) if (a.fits(chain[k])) k--;
      return k < 0;
    });
  }
  closest(sel: string): El | null { for (let n: El | null = this; n; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel: string): El[] {
    const out: El[] = [];
    const walk = (n: El) => { for (const c of n.children) { if (c.matches(sel)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel: string): El | null { return this.querySelectorAll(sel)[0] ?? null; }
}
const docBody = new El("body");
const docKeys: Listener[] = [];                  // the document's keydown handlers: the viewer's per open, the browser's per overlay
const doc = {
  body: docBody,
  activeElement: null as El | null,
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  getElementById: (id: string): El | null => docBody.querySelector("#" + id),
  querySelectorAll: (sel: string): El[] => docBody.querySelectorAll(sel),   // no bundle <script src>: the editor chunk cannot load, so Edit takes the plain textarea
  addEventListener: (type: string, fn: Listener) => { if (type === "keydown") docKeys.push(fn); },
  removeEventListener: (type: string, fn: Listener) => { const i = docKeys.indexOf(fn); if (i >= 0) docKeys.splice(i, 1); },
};
const win: any = new EventTarget();
win.parent = win;                                // no shell: the browser's close notice has nowhere to go
const confirms: string[] = [];                   // every discard ask the viewer put to the person
let confirmAnswer = true;                        // what the person answers: false keeps the viewer
win.confirm = (q: string) => { confirms.push(q); return confirmAnswer; };
win.getSelection = () => null;
win.postMessage = (m: unknown) => { win.dispatchEvent(new MessageEvent("message", { data: m })); };
(globalThis as any).window = win;
(globalThis as any).document = doc;
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); },
  removeItem: (k: string) => { store.delete(k); },
};

// ── fixtures: a notes-api world, synthetic throughout ──────────────────────────────────────────────
const SID = "66666666-7777-8888-9999-aaaaaaaaaaaa";
const OTHER_SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const ROOT = "/tmp/notes-api";
const APP = ROOT + "/src/app.py";
const SRC = ROOT + "/src";
const DOCS = ROOT + "/docs";
const PY = "def main():\n    return 0\n";
const TYPED = PY + "\nprint(main())\n";
// The two fetches an open makes: the file's bytes (text/plain, faithful UTF-8, an ns anchor, so Edit arms) and the
// consent read (/version says editing is already on, so no popup).
(globalThis as any).fetch = (url: string) => {
  if (url.startsWith("/version")) return Promise.resolve({ json: () => Promise.resolve({ fileEditing: true }) });
  const p = decodeURIComponent((/[?&]path=([^&]*)/.exec(url) || [])[1] || "");
  const headers = { get: (h: string) => (h === "Content-Type" ? "text/plain; charset=utf-8" : h === "X-Romp-Mtime-Ns" ? "1700000000000000000" : h === "X-Romp-Text-Utf8" ? "1" : null) };
  if (p !== APP) return Promise.resolve({ ok: false, status: 404, headers, text: () => Promise.resolve("no such file: " + p) });
  return Promise.resolve({ ok: true, status: 200, headers, text: () => Promise.resolve(PY) });
};
/** Let every pending promise chain run: the fetch settles and the editor chunk's rejection reaches its catch. */
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise<void>((r) => setImmediate(r)); };

const posted: Array<Record<string, unknown>> = [];   // what both overlays post to the kernel
const listDirs = () => posted.filter((m) => m.type === "listDir");
type Mods = { fv: typeof import("./file-view"); fb: typeof import("./file-browse") };
let bound: Promise<Mods> | null = null;
/** Both overlays, bound once to the same poster and the same window, as the feed's boot binds them. */
function overlays(): Promise<Mods> {
  if (!bound) bound = Promise.all([import("./file-view"), import("./file-browse")]).then(([fv, fb]) => {
    fv.initFileView((m) => posted.push(m));
    fb.initFileBrowse((m) => posted.push(m));
    return { fv, fb };
  });
  return bound;
}
/** The kernel's reply to the newest listDir asked: `names` under `base` (a trailing slash marks a directory). */
function answerListing(base: string, names: string[]): void {
  const asks = listDirs();
  const ask = asks[asks.length - 1];
  assert.ok(asks.length > 0, "a listDir was asked");
  const entries = names.map((n) => (n.endsWith("/")
    ? { name: n.slice(0, -1), isDir: true, isLink: false, size: 0, mtime: 1700000000 }
    : { name: n, isDir: false, isLink: false, size: 12, mtime: 1700000000, viewable: true }));
  win.dispatchEvent(new MessageEvent("message", { data: {
    type: "dirListing", reqId: ask.reqId, host: "", sid: ask.sid, base, parent: base.slice(0, base.lastIndexOf("/")) || "/",
    entries, total: entries.length, truncated: false,
  } }));
}
type Viewer = { wrap: El; body: El; btn: (label: string) => El };
/** The viewer up now, with its title-bar buttons by label. */
function viewerUp(): Viewer {
  const wrap = doc.getElementById("romp-fileview");
  assert.ok(wrap !== null, "a viewer is up");
  const card = wrap!.children[0];
  assert.equal(card.className, "fileview");
  const bar = card.children[0];
  assert.equal(bar.className, "fileview-bar");
  const body = card.children[card.children.length - 1];
  assert.equal(body.className, "fileview-body");
  const acts = bar.children.find((c) => c.classList.contains("fileview-acts"))!;
  const btn = (label: string) => { const walk = (n: El): El | undefined => { for (const c of n.children) { if (c.tagName === "button" && (c.textContent === label || c.getAttribute("aria-label") === label)) return c; const d = walk(c); if (d) return d; } return undefined; }; const b = walk(acts); assert.ok(b !== undefined, "the " + label + " button"); return b!; };   // T367: controls sit in groups, glyph buttons carry their word as aria-label
  return { wrap: wrap!, body, btn };
}
/** Click Edit (the editor chunk has no bundle to load from, so the plain textarea mounts) and type a line: the
 *  buffer is dirty, and the viewer's close guard now asks before letting the viewer go. */
async function editAndType(v: Viewer): Promise<El> {
  assert.equal(v.btn("Edit").hidden, false, "Edit armed off the kernel's text/plain + ns verdict");
  v.btn("Edit").click();
  await settle();
  const ta = v.body.children.find((x) => x.className === "fileview-editor");
  assert.ok(ta !== undefined, "the fallback textarea took the body");
  assert.equal(ta!.value, PY);
  ta!.value = TYPED;
  ta!.dispatchEvent(new Ev("input"));
  return ta!;
}
const rowsShown = () => doc.getElementById("fb-list")!.children.map((r) => r.className + ":" + r.textContent);
async function reset(): Promise<void> {
  const { fv, fb } = await overlays();
  confirmAnswer = true;
  fv.closeFileView();
  fb.closeFileBrowse();
  // closeFileBrowse resets the one-ask latch only when an overlay is up; a case that failed with an ask outstanding and
  // no overlay would otherwise hand the next case a wedged browser. A reply frame clears the latch before any stale
  // check (onListing), twice in case a navigation was queued behind the ask, so each case fails on its own account.
  for (let i = 0; i < 2; i++) win.dispatchEvent(new MessageEvent("message", { data: { type: "dirListing", reqId: -1 } }));
  posted.length = 0; confirms.length = 0; store.clear();
}

test("a browse click while the discard confirm keeps the viewer stands down whole: no overlay, no listing asked, no handler bound, no close notice to the shell, the edits kept; the next browse, edit mode left, opens as ever", async (t) => {
  const { fv, fb } = await overlays();
  // a shell frames this document and listens: the browser's close notice (browseClosed) is what its pane restore hangs on
  const shell: Array<Record<string, unknown>> = [];
  win.parent = { postMessage: (m: Record<string, unknown>) => { shell.push(m); } };
  t.after(() => { win.parent = win; });
  t.after(reset);
  fv.openFileView(APP, SID);
  await settle();
  const v = viewerUp();
  const ta = await editAndType(v);
  const keysBefore = docKeys.length;
  const askedBefore = listDirs().length;
  confirmAnswer = false;
  fb.openFileBrowse(SRC, SID);
  assert.equal(confirms.length, 1, "the viewer's guard asked once, and the person kept the edits");
  assert.equal(v.wrap.isConnected, true, "the viewer is kept");
  assert.equal(ta.value, TYPED, "with the typed line still in the editor");
  assert.equal(doc.getElementById("romp-filebrowse") === null, true, "no browser overlay beneath the kept viewer");
  assert.equal(docBody.classList.contains("filebrowse-open"), false, "the body does not read as browsing");
  assert.equal(listDirs().length, askedBefore, "no listing asked for a browser that is not up");
  assert.equal(docKeys.length, keysBefore, "no keydown handler left bound for a browser that is not up");
  assert.deepEqual(shell, [], "no close notice to the shell for a browser that never opened");
  // the person leaves edit mode (Cancel, edits discarded on purpose) and browses again: the viewer goes and the listing comes
  confirmAnswer = true;
  v.btn("Cancel").click();
  fb.openFileBrowse(SRC, SID);
  assert.equal(doc.getElementById("romp-fileview") === null, true, "the viewer, no longer holding edits, closed for the listing");
  assert.equal(doc.getElementById("romp-filebrowse") !== null, true, "the overlay is up");
  assert.equal(docBody.classList.contains("filebrowse-open"), true);
  assert.equal(listDirs().length, askedBefore + 1, "one listing asked");
  const ask = listDirs()[askedBefore];
  assert.deepEqual({ path: ask.path, sid: ask.sid, hidden: ask.hidden }, { path: SRC, sid: SID, hidden: false });
  assert.equal(docKeys.length, keysBefore + 1, "the browser's one keydown handler, bound once");
  // and a browser that WAS up tells the shell when it closes: the listening shell above is live, and the stand-down's silence was chosen
  fb.closeFileBrowse();
  assert.deepEqual(shell, [{ romp: "browseClosed" }], "one close notice, from the close of a browser that opened");
});

test("a browse click over a listing the kept viewer covers leaves that listing as it was: its rows, its crumbs, its Hidden state, its path and its session", async (t) => {
  const { fv, fb } = await overlays();
  t.after(reset);
  fb.openFileBrowse(SRC, SID);
  answerListing(SRC, ["app.py", "lib/"]);
  const overlay = doc.getElementById("romp-filebrowse");
  const hid = doc.getElementById("fb-hidden");
  assert.ok(overlay !== null && hid !== null, "the overlay and its Hidden control are up");
  hid!.click();                                                   // dotfiles too: the listing is re-asked
  assert.equal(hid!.getAttribute("aria-pressed"), "true");
  answerListing(SRC, [".env", "app.py", "lib/"]);
  const shown = rowsShown();
  assert.equal(shown.length, 3, "three rows under the crumbs");
  const crumbs = doc.getElementById("fb-crumbs")!.textContent;
  // a file opened FROM the listing, by its row's plain click, then edited: the viewer sits over the listing
  const row = doc.getElementById("fb-list")!.querySelectorAll('[data-act="file"]').find((r) => r.dataset.path === APP);
  assert.ok(row !== undefined, "the app.py row");
  row!.click();
  await settle();
  const v = viewerUp();
  await editAndType(v);
  const askedBefore = listDirs().length;
  const keysBefore = docKeys.length;
  confirmAnswer = false;
  fb.openFileBrowse(DOCS, OTHER_SID);                             // another folder, another session, while the viewer is kept
  assert.equal(confirms.length, 1, "the viewer's guard asked once");
  assert.equal(v.wrap.isConnected, true, "the viewer is kept");
  assert.equal(doc.getElementById("romp-filebrowse") === overlay, true, "the same overlay, still up beneath the viewer");
  assert.equal(listDirs().length, askedBefore, "no listing asked for the other folder");
  assert.deepEqual(rowsShown(), shown, "the rows beneath the viewer are as they were, not the loader");
  assert.equal(doc.getElementById("fb-crumbs")!.textContent, crumbs, "the crumbs too");
  assert.equal(hid!.getAttribute("aria-pressed"), "true", "Hidden still on");
  assert.equal(docKeys.length, keysBefore, "no second keydown handler for an overlay that was already up");
  // the person closes the viewer (edits discarded on purpose) and presses Hidden: THIS listing is re-asked, for THIS
  // session, with Hidden toggled off from on
  confirmAnswer = true;
  fv.closeFileView();
  assert.equal(doc.getElementById("romp-fileview") === null, true, "the viewer closed on the accepted confirm");
  hid!.click();
  const asks = listDirs();
  const last = asks[asks.length - 1];
  assert.equal(asks.length, askedBefore + 1, "one listing asked, by the Hidden press");
  assert.deepEqual({ path: last.path, sid: last.sid, hidden: last.hidden }, { path: SRC, sid: SID, hidden: false });
});

test("the discard confirm accepted: the browse goes through, the viewer closes and the listing is asked (the stand-down keys on the refusal, never on the edits alone)", async (t) => {
  const { fv, fb } = await overlays();
  t.after(reset);
  fv.openFileView(APP, SID);
  await settle();
  await editAndType(viewerUp());
  const askedBefore = listDirs().length;
  confirmAnswer = true;
  fb.openFileBrowse(SRC, SID);
  assert.equal(confirms.length, 1, "asked once");
  assert.equal(doc.getElementById("romp-fileview") === null, true, "the viewer closed");
  assert.equal(doc.getElementById("romp-filebrowse") !== null, true, "the overlay is up");
  assert.equal(listDirs().length, askedBefore + 1, "the listing asked");
  assert.equal(listDirs()[askedBefore].path, SRC);
});
