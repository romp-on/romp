// The file BROWSER in the FEED pane (the user 2026-08-14): breadcrumb over one directory's entries,
// riding the listDir WS op with the dirComplete staleness protocol, opening files through the
// existing viewer. Source pins (no jsdom for these modules), the repo convention.
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
  assert.match(BROWSE, /wrap\.onclick = \(ev\) => \{ if \(ev\.target === wrap\) closeFileBrowse\(\); \};/,
    "backdrop clicks close; content clicks never do (the lightbox contract)");
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
  assert.match(BROWSE, /if \(row\.dataset\.act === "file"\) \{ openFileView\(p, curSid\); return; \}/);
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
  // chat: openBrowse is PANE-LOCAL (2026-08-24 — it used to relay to the shell and open over the
  // FEED, the wrong pane): the browser opens over the chat that launched it, web-only, framed or not
  assert.match(RENDER, /openFileBrowse\(path \|\| "\.", sid \|\| activeId \|\| null\);/);
  assert.match(RENDER, /initFileBrowse\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/, "the chat hosts its own browser instance");
  assert.doesNotMatch(RENDER, /window\.parent\.postMessage\(\{ romp: "browseFiles"/, "no shell relay from the chat anymore");
  // tab right-click menu row: bottom of the menu, behind a divider, icon + sub-description
  assert.match(RENDER, /l\.textContent = "Browse files"; bodyEl\.appendChild\(l\);/);
  // feed card menu row rides canPreview (web only — the VS Code webview can't reach the kernel
  // origin), and sends only the sid — the kernel resolves "." against the session's cwd authoritatively
  assert.match(FEED, /openFileBrowse\("\.", it\.sid\);/);
  assert.match(FEED, /if \(canPreview\(\)\) \{\n    const browse = el\("div", "ctx-item"\);/);
});

test("the statusline folder link BROWSES on the web; OS-open lives on its right-click (the user 2026-08-14)", () => {
  assert.match(RENDER, /elem\.dataset\.act = web \? "browseFiles" : "openFolder";/);   // pane-local browse needs no shell (2026-08-24)
  assert.match(RENDER, /click to browse this folder/);
  // the demoted OS-open: one document-level contextmenu on folder links, posting the old openFolder
  assert.match(RENDER, /item\.textContent = "Open folder window";/);
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
  assert.match(BROWSE, /import \{ openFileView, closeFileView \} from "\.\/file-view";/);
  assert.match(BROWSE, /if \(document\.getElementById\("romp-fileview"\)\) closeFileView\(\);/);
});

test("closeFileBrowse unbinds the keydown handler and resets the protocol latch", () => {
  // a ✕-close sees no keydown, so lazily self-removing handlers stacked across reopens (double-moving
  // arrows); and a surviving inflight wedged the reopened browser behind a reply that never comes
  assert.match(BROWSE, /if \(onKeyRef\) \{ document\.removeEventListener\("keydown", onKeyRef\); onKeyRef = null; \}/);
  const close = BROWSE.split("export function closeFileBrowse")[1].split("function human")[0];
  assert.ok(close.includes("inflight = false;"), "the latch resets with the overlay");
  assert.ok(close.includes("queued = null;"));
  assert.ok(close.includes('document.getElementById("fb-ctx")?.remove();'), "a row menu never outlives its listing");
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
  assert.match(BROWSE, /m\.type === "warn" && inflight && document\.getElementById\("romp-filebrowse"\)/);
});

test("Escape peels the TOPMOST layer: row menu, then viewer, then browser", () => {
  const key = BROWSE.split("const onKey =")[1].split("document.addEventListener(\"keydown\", onKey)")[0];
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
  const menuBody = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  const browseAt = menuBody.indexOf('l.textContent = "Browse files"');
  assert.ok(browseAt > 0, "the item exists");
  // nothing else is appended to the menu after the Browse block — it is the last thing before mount
  assert.equal(menuBody.indexOf("menu.appendChild(", browseAt + 200) > 0 ? menuBody.slice(browseAt).match(/menu\.appendChild\(browse\);/) !== null : true, true);
  assert.ok(menuBody.lastIndexOf('menu.appendChild(el("div", "ctx-sep"));') < browseAt
            && menuBody.slice(0, browseAt).trimEnd().includes('menu.appendChild(el("div", "ctx-sep"));'),
    "a divider immediately precedes it — a different kind of thing");
  assert.match(menuBody.slice(browseAt - 400, browseAt), /ctxIcon\("folder", false\)/, "the folder icon");
  assert.match(menuBody, /sb\.textContent = "the session's working tree, in a viewer over this chat";/,
    "the standard sub-description line");
  // …and the Billing submenu (the previous last item) now sits ABOVE it
  assert.ok(menuBody.indexOf('l.textContent = "Billing"') < browseAt, "Browse is last");
});
