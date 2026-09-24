// The file viewer — a modal over the CHAT pane (the user 2026-08-15; the first cut filled the FEED
// pane, and reading a file cost the cards). Clicking a file path in the chat used to post `openFile`,
// which the kernel served by running an opener on ITS OWN machine — the wrong screen when the
// dashboard is read from another device, and nothing at all on a kernel with no desktop, because the
// opener was macOS-only (the user 2026-08-08). The bytes have to reach the browser, so the click routes
// to a viewer fed by the same /file route the image previews use — now in the SAME document as the
// click, so the chat needs no shell relay. The FEED still hosts the viewer too: the file BROWSER
// (file-browse.ts) opens files through the same module in its own document, which is why the feed
// sheet mirrors the viewer CSS instead of dropping it. Source pins (no jsdom for these modules) +
// executed replicas of the pure helpers.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const VIEW = web("file-view.ts");
const RENDER = web("render.ts");
const FEED = web("feed.ts");
const FEED_CSS = web("feed.css");
const CHAT_CSS = web("styles.css");

test("openPath routes by HOST: the in-pane viewer modal on the web (or the Files pane, by the ladder), the editor in VS Code", () => {
  assert.match(RENDER, /function openPath\(path: string, sid\?: string \| null, ev\?: MouseEvent \| null, frag\?: string \| null\): void/);   // ev: the click, for a PDF's modified-click tab
  // web → the ladder decides at the click (file-route.ts fileLinkRoute, its table in file-route.test.ts): "here" opens
  // the viewer in THIS document through the gesture reader; "pane" hands a plain click to the shell for the Files pane
  assert.match(RENDER, /const route = fileLinkRoute\(window\.parent !== window, panesOn\.files === true, panesAvail\.files !== false\);/,
    "…and whether the Files control exists at all (its gear setting, T317): hidden, the pane road falls back to here");
  assert.match(RENDER, /openFileClick\(ev, path, to, route === "pane" \? \(\) => \{/);   // via the gesture reader: a plain click is openFileView or the relay (pdf-new-tab.test.ts)
  assert.match(RENDER, /import \{ openFileClick \} from "\.\/file-view";/);   // the gesture reader is the chat's only way in; openFileView is not imported
  assert.match(RENDER, /window\.parent\.postMessage\(\{ romp: "viewFile", path, sid: to, pane: "pane", frag: frag \|\| null,\n\s*identity: s && s\.name \? \{ name: s\.name, color: s\.color \?\? null \} : null \}, "\*"\);/);
  assert.equal((RENDER.match(/romp: "viewFile"/g) || []).length, 1, "one relay, aimed at the Files pane; the feed is never a file's target");
  assert.doesNotMatch(RENDER, /pane: "feed"/);
  // VS Code keeps the host editor
  assert.match(RENDER, /vscodeApi\.postMessage\(sid \? \{ type: "openFile", path, id: sid \} : \{ type: "openFile", path \}\);/);
});

test("every file-link surface in the chat goes through openPath — no direct openFile posts left", () => {
  for (const call of [/openPath\(path, null, e\);/, /openPath\(open, relative \? activeId : null, e, a\.dataset\.frag \|\| null\);/,
                      /openPath\(p, id \|\| null, e\);/]) assert.match(RENDER, call);   // each with its click (a PDF's modified-click tab)
  // the ONLY openFile postMessage left in render.ts is openPath's own fallback branch
  assert.equal((RENDER.match(/type: "openFile"/g) || []).length, 2,
               "both remaining mentions are the two arms of openPath's fallback");
});

test("the VIEWER's shell relay serves the Files pane only; the BROWSER's stays, and the feed pane is only juggled for it", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  // the viewer lives in the clicking document (the user 2026-08-15: a file view must never touch the feed), so
  // the shell forwards a viewFile click to ONE place, the Files pane, and only when the click names it; the
  // pane stays up, so the viewer has nothing to restore and nothing to announce. The arm itself is the kernel's
  // and is pinned and run in the Python lane (tests/test_files_pane.py Relay, tests/test_pane_state_broadcast.py)
  assert.doesNotMatch(KERNEL, /postMessage\(\{romp:'viewFile',path:m\.path,sid:m\.sid\},'\*'\)/, "no forward into the feed");
  assert.doesNotMatch(VIEW, /viewFileClosed/, "nothing to restore → nothing to announce");
  // the file BROWSER still lives in the FEED pane, so its ask still relays through the shell from
  // any pane, still turns a toggled-off feed on, and still restores it on browseClosed — that
  // machinery is the browser's, not the viewer's
  assert.match(KERNEL, /if\(m\.romp==='browseFiles'\)\{var bf=document\.getElementById\('f-feed'\);/);
  assert.match(KERNEL, /window\.__rompFeedWasOff=true;/);
  assert.match(KERNEL, /m\.romp==='browseClosed'/);
  assert.match(KERNEL, /window\.__rompMobileTab&&window\.__rompMobileTab\('feed'\)/, "phone: one pane at a time");
});

test("the viewer is a singleton MODAL over its pane: ~95% card, dimmed backdrop, ✕/Esc/backdrop close", () => {
  assert.match(VIEW, /document\.getElementById\("romp-fileview"\)\?\.remove\(\);/, "re-opening replaces, never stacks");
  // the backdrop closes on ITS OWN clicks only — content clicks don't (the lightbox contract)
  assert.match(VIEW, /wrap\.onclick = \(ev\) => \{ if \(ev\.target === wrap\) closeFileView\(\); \};/);
  assert.match(VIEW, /close\.addEventListener\("click", closeFileView\);/);
  assert.match(VIEW, /if \(e\.key !== "Escape" \|\| e\.defaultPrevented \|\| !document\.getElementById\("romp-fileview"\)\) return;/);
  // the panels treatment on the CHAT sheet: dimmed rgba(0,0,0,0.55) backdrop, the content behind visible
  assert.match(CHAT_CSS, /#romp-fileview \{ position: fixed; inset: 0; z-index: 1200; background: var\(--overlay-dim\);/);
  assert.match(CHAT_CSS, /\.fileview \{ width: 95%; height: 95%;/);
  // …and mirrored on the FEED sheet, which still hosts the viewer when the file BROWSER opens a file
  // (one treatment, two sheets — the hljs-palette precedent below)
  assert.match(FEED_CSS, /#romp-fileview \{ position: fixed; inset: 0;/);
  assert.match(FEED_CSS, /\.fileview \{ width: 95%; height: 95%;/);
  assert.match(FEED, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/,
    "the feed boots the listener with the WS poster (saves ride it — the raw-mode slice)");
});

// ── selection → quote chip (the user 2026-08-23, the three-verbs consolidation): the viewer's
// separate review layer (per-file comment store, marks, one-shot Submit — romp:fileviewComments +
// buildReviewMessage) is GONE. Selecting a passage now seeds the chat composer's own labeled quote
// chip, exactly like a VS Code editor highlight, and batching rides the chip + ⌘⏎ staging flow the
// chat already has. "Comment" means only the transcript's live threads now. ──

test("selecting in the viewer seeds the composer's editor chip — the editorSelection shape, path:line label", () => {
  // mouseup posts to the composer's window — this document's when it holds one (the browseFiles
  // precedent — no import cycle with render.ts), else the shell's chat pane (composerWindow, below) —
  // and render.ts's existing editorSelection handler owns the chip end to end
  // the handler reads the event's target since the text-size control: a press on a title-bar button with a passage
  // still selected in the body settles no selection (file-view-text-size.test.ts runs the gate)
  assert.match(VIEW, /box\.addEventListener\("mouseup", \(ev\) => \{/);
  assert.match(VIEW, /seedTarget\.postMessage\(\{ type: "editorSelection", text: picked, sid: sid \|\| undefined, src: quoteSrcLabel\(path, doc, picked\) \}, "\*"\);/);
  // a collapsed or out-of-viewer selection seeds nothing, and CodeMirror selections are edits
  assert.match(VIEW, /if \(!sel \|\| sel\.isCollapsed \|\| !sel\.anchorNode \|\| !box\.contains\(sel\.anchorNode\)\) return;/);
  assert.match(VIEW, /if \(editing\) return;/);
});

test("the chip lands in the session the file was opened FOR — the posted sid beats activeId-at-gesture", () => {
  // the modal stays up across a tab switch (nothing closes it on focus), so seeding into activeId
  // would put session A's path:line quote into session B's composer — the 2026-08-19 routing rule
  // the retired review layer already learned once. Host (VS Code) posts carry no sid → activeId.
  assert.match(RENDER, /const to = typeof m\.sid === "string" && m\.sid \? m\.sid : activeId;/);
  assert.match(RENDER, /if \(to\) seedEditorQuote\(to, m\.text, typeof m\.src === "string" \? m\.src : undefined\);/);
});

test("the label's line is minted against a FRESH read, and a failed re-read falls back to the snapshot", () => {
  // agents edit these same trees while you read: the open-time snapshot's numbering may have moved,
  // so the line is anchored at selection time — and a failed re-read must not fabricate drift
  // nobody observed (the retired Submit guard's rule), so it anchors the snapshot instead. The
  // snapshot is viewText, not text: the SVG Source view's snapshot is the decoded blob and `text`
  // stays null in media mode, so falling back to it would strip every SVG quote's line label.
  assert.match(VIEW, /const seq = \+\+seedSeq;/);
  assert.match(VIEW, /fetch\(fileUrl\(path, sid\), \{ cache: "no-store" \}\)\n\s*\.then\(\(r\) => \(r\.ok \? r\.text\(\) : Promise\.reject\(new Error\(String\(r\.status\)\)\)\)\)\n\s*\.catch\(\(\) => viewText\(\)\)/);
  assert.match(VIEW, /const viewText = \(\): string \| null => \(svgSource && svgText !== null \? svgText : text\);/);
  assert.match(VIEW, /if \(seq !== seedSeq\) return;/, "two racing reads: the last gesture wins");
});

test("a viewer whose document has no composer seeds THROUGH the shell: the feed reaches the chat's chip", () => {
  // The feed document has no composer, so a post to its own window landed unheard — the guide's
  // promise that any passage selected in the viewer lands in the composer was false for a file opened
  // from the feed's file browser, and each selection still paid the fresh read. So the TARGET is
  // resolved: this window when it holds the composer, else the same-origin shell, which forwards the
  // unchanged message into the chat pane. No composer and no shell (VS Code's cross-origin parent, a
  // standalone pane) still stands the gesture down before the fresh read (the no-sink gating).
  assert.match(VIEW, /function composerWindow\(\): Window \| null \{\n\s*if \(document\.getElementById\("composer-input"\)\) return window;\n\s*try \{ if \(window\.parent !== window && window\.parent\.document\.getElementById\("chat-pane"\)\) return window\.parent; \}\n\s*catch \{[^}]*\}\n\s*return null;\n\}/);
  assert.match(VIEW, /const seedTarget = composerWindow\(\);\n\s*if \(!seedTarget\) return;/);
  // the shell's arm: the SAME message, forwarded whole into the chat frame — sid intact, so the chip
  // lands in the session the file was opened for (the 2026-08-19 routing rule holds across documents)
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNEL, /if\(m\.type==='editorSelection'&&typeof m\.text==='string'\)\{var fc=document\.getElementById\('f-chat'\);[\s\S]{0,700}?try\{fc&&fc\.contentWindow&&fc\.contentWindow\.postMessage\(m,'\*'\);\}catch\(e\)\{\}\}/);
  // …and a chat pane toggled OFF is brought forward first, the browseFiles arm's rule for the feed: a chip
  // seeded into a hidden composer is a silent gesture (review fold on #970, 2026-09-07)
  assert.match(KERNEL, /if\(!document\.body\.classList\.contains\('po-chat'\)\)\{try\{window\.__rompPaneToggle&&window\.__rompPaneToggle\('chat',true\);\}catch\(e\)\{\}\}\n\s*try\{fc&&fc\.contentWindow&&fc\.contentWindow\.postMessage\(m,'\*'\);/);
  // …and the chat's existing window-message handler is the receiver: nothing new listens in feed.ts
  assert.match(RENDER, /else if \(m\.type === "editorSelection" && typeof m\.text === "string" && m\.text\.trim\(\)\) \{/);
  assert.doesNotMatch(FEED, /editorSelection/);
  // the review layer is gone from every module and both sheets, and the orphaned store is swept
  for (const source of [VIEW, RENDER, FEED, CHAT_CSS, FEED_CSS]) {
    assert.doesNotMatch(source, /setCommentSink|buildReviewMessage|fv-hl|fileview-submit/);
  }
  assert.match(VIEW, /localStorage\.removeItem\("romp:fileviewComments"\)/);
});

// executed: composerWindow's ladder, lifted from the source (a hand copy would drift), run against
// shimmed window/document pairs for each hosting situation
test("composerWindow, executed: own composer → the same-origin shell's chat pane → nothing", () => {
  const m = VIEW.match(/function composerWindow\(\): Window \| null \{[\s\S]*?\n\}/);
  assert.ok(m, "composerWindow found");
  const body = m![0].replace(/^function composerWindow\(\): Window \| null /, "");
  const run = new Function("window", "document", "return (function()" + body + ")();") as (w: unknown, d: unknown) => unknown;
  const doc = (ids: string[]) => ({ getElementById: (id: string) => (ids.includes(id) ? {} : null) });
  const self: any = {}; self.parent = self;
  assert.equal(run(self, doc(["composer-input"])), self, "the chat document: its own window");
  const shell = { document: doc(["chat-pane"]) };
  const framed = { parent: shell };
  assert.equal(run(framed, doc([])), shell, "a pane inside the shell: the shell, which forwards into the chat");
  assert.equal(run(framed, doc(["composer-input"])), framed, "a composer at hand always wins over the relay");
  assert.equal(run({ parent: { get document() { throw new Error("cross-origin"); } } }, doc([])), null,
    "VS Code's cross-origin parent is not the shell — the gesture stands down");
  assert.equal(run({ parent: { document: doc([]) } }, doc([])), null, "a same-origin parent that is not the shell");
  assert.equal(run(self, doc([])), null, "unframed and composer-less: nowhere to seed");
});

// executed: the body's width observer (watchBodyWidth), lifted from the source with its type annotations stripped (the
// composerWindow lift above; a hand copy would drift), run over a fake ResizeObserver and stand-in nodes. Both sheets'
// `.fileview-md > table` read --fv-body-w for a top-level table's cap and its shift into the gutters; what the function
// promises them is run here: nothing before the first report, the body's content width on EACH TOP-LEVEL TABLE (never a
// nested one, never the prose) after one, the same width on the fresh tables a paint brings (the returned stamp: mdBlock
// rebuilds the root and no report follows a paint), a repeated width no write, one watch at a time, and no watch at all
// without ResizeObserver.
test("watchBodyWidth, executed: --fv-body-w lands on each top-level table after a report and, through the stamp, on a fresh root's tables; a nested table and the prose get nothing; a repeated width writes nothing; the next watch and the drop disconnect; no ResizeObserver, no writes", () => {
  const head = "function watchBodyWidth(body: HTMLElement): () => void {";
  const at = VIEW.indexOf("let dropWidthWatch: () => void = ");
  const end = VIEW.indexOf("\n}\n", VIEW.indexOf(head, at)) + 3;
  assert.ok(at >= 0 && VIEW.indexOf(head, at) > at && end > at, "watchBodyWidth and its module-level drop found, the drop first");
  const js = VIEW.slice(at, end)
    .replace(/: \(\) => void/g, "")                                    // the let's type and the function's return type
    .replace("(body: HTMLElement)", "(body)")
    .replace("const stamp = (): void =>", "const stamp = () =>")
    .replace(/ as HTMLElement\)/g, ")");
  assert.doesNotMatch(js, /HTMLElement|: void/, "every annotation the lift knows is gone (a new one needs its strip here)");
  type Node = { tagName: string; className: string; children: Node[]; clientWidth: number; writes: number; style: { setProperty(k: string, v: string): void; getPropertyValue(k: string): string }; querySelector(sel: string): Node | null };
  const node = (tagName: string, className = "", children: Node[] = []): Node => {
    const props = new Map<string, string>();
    const n: Node = { tagName, className, children, clientWidth: 0, writes: 0,
      style: { setProperty: (k, v) => { props.set(k, v); n.writes++; }, getPropertyValue: (k) => props.get(k) ?? "" },
      querySelector: (sel) => { const walk = (m: Node): Node | null => { for (const c of m.children) { if (c.className === sel.slice(1)) return c; const d = walk(c); if (d) return d; } return null; }; return walk(n); } };
    return n;
  };
  type Rec = { cb: (entries: Array<{ contentRect: { width: number } }>) => void; targets: unknown[]; live: boolean };
  const observers: Rec[] = [];
  class FakeResizeObserver {
    private rec: Rec;
    constructor(cb: Rec["cb"]) { this.rec = { cb, targets: [], live: true }; observers.push(this.rec); }
    observe(t: unknown): void { this.rec.targets.push(t); }
    disconnect(): void { this.rec.live = false; }
  }
  const report = (entries: Array<{ contentRect: { width: number } }>) => { const live = observers.filter((o) => o.live); assert.equal(live.length, 1, "one live observer"); live[0].cb(entries); };
  const run = new Function("ResizeObserver", js + "\nreturn { watchBodyWidth, drop: () => dropWidthWatch() };") as (ro: unknown) => { watchBodyWidth: (body: Node) => () => void; drop: () => void };
  /** A rendered root as mdBlock leaves it: prose, a table inside a paragraph (as inside a quote or a list item), two top-level tables. */
  const fresh = () => { const nested = node("TABLE"); const p = node("P", "", [nested]); const t1 = node("TABLE"); const t2 = node("TABLE"); return { md: node("DIV", "fileview-md", [p, t1, t2]), p, nested, t1, t2 }; };
  const w = (n: Node) => n.style.getPropertyValue("--fv-body-w");
  const lib = run(FakeResizeObserver);
  let root = fresh();
  const body = node("DIV", "fileview-body", [root.md]);
  const stamp = lib.watchBodyWidth(body);
  assert.equal(observers.length, 1); assert.deepEqual(observers[0].targets, [body], "the body is what the observer watches");
  stamp();
  assert.equal(w(root.t1) + w(root.t2), "", "before the first report nothing is written: the sheet's fallback holds (the cap is the column)");
  report([{ contentRect: { width: 900 } }]);
  assert.equal(w(root.t1), "900px"); assert.equal(w(root.t2), "900px");
  assert.equal(w(root.nested), "", "a table inside a paragraph is not the document's own: it keeps the prose width");
  assert.equal(w(root.p), "", "the prose is never written to (the property is non-inherited; a write there would reach nothing anyway)");
  // a paint: mdBlock rebuilt the root, no report follows; renderBody's stamp writes the width last reported on the fresh tables
  root = fresh(); body.children = [root.md];
  assert.equal(w(root.t1), "", "a fresh root starts unset");
  stamp();
  assert.equal(w(root.t1), "900px"); assert.equal(w(root.t2), "900px"); assert.equal(w(root.nested), "");
  // a report of the width already held is a no-op: a root rebuilt between the two reports is not touched by it
  root = fresh(); body.children = [root.md];
  report([{ contentRect: { width: 900 } }]);
  assert.equal(w(root.t1), "", "a repeated width writes nothing");
  report([{ contentRect: { width: 700 } }]);
  assert.equal(w(root.t1), "700px"); assert.equal(w(root.t2), "700px");
  assert.equal(root.t1.writes, 1, "one write per change");
  // the last of a callback's entries is the newest; a callback with no entry reads the body itself
  report([{ contentRect: { width: 300 } }, { contentRect: { width: 800 } }]);
  assert.equal(w(root.t1), "800px", "the last of the entries is the width kept");
  body.clientWidth = 640; report([]);
  assert.equal(w(root.t1), "640px", "a callback with no entry reads the body's clientWidth");
  // one watch at a time: the next open's watch drops the last, and the close drops the watch up
  const body2 = node("DIV", "fileview-body", []);
  lib.watchBodyWidth(body2);
  assert.equal(observers[0].live, false, "the second watch disconnects the first observer");
  assert.equal(observers.length, 2); assert.deepEqual(observers[1].targets, [body2]);
  lib.drop();
  assert.equal(observers[1].live, false, "the drop disconnects");
  assert.doesNotThrow(() => lib.drop(), "a second drop is nothing to do");
  // no ResizeObserver (a stand-in, an old engine): no observer, and the stamp writes nothing
  const bare = run(undefined);
  root = fresh(); const body3 = node("DIV", "fileview-body", [root.md]);
  bare.watchBodyWidth(body3)();
  assert.equal(observers.length, 2, "no observer was made"); assert.equal(w(root.t1) + w(root.t2), "", "nothing written: the sheet's fallback holds");
});

test("the width watch is wired: each viewer opens one on the body it builds, each rendered paint stamps the fresh root's tables, and the close drops it", () => {
  const opens = VIEW.match(/const body = el\("div", "fileview-body"\);\n\s*const stampBodyWidth = watchBodyWidth\(body\);/g) || [];
  assert.equal(opens.length, 2, "openFileView and openUrlView each watch their body as they build it");
  assert.match(VIEW, /body\.replaceChildren\(rendered \? mdBlock\(text, \{ kind: "file", path, sid: sid \|\| null \}\) : codeBlock\(text, path, true\)\);\n\s*if \(rendered\) stampBodyWidth\(\);/, "openFileView: a rendered paint stamps its fresh tables (mdBlock rebuilt the root; no report follows a paint)");
  assert.match(VIEW, /landFragment\(\);[^\n]*\n\s*if \(fmt\.md === "rendered"\) stampBodyWidth\(\);/, "openUrlView: the same, after the fragment lands");
  const closeAt = VIEW.indexOf("export function closeFileView(): void {");
  const close = VIEW.slice(closeAt, VIEW.indexOf("\n}\n", closeAt));
  assert.match(close, /\n\s*dropWidthWatch\(\);/, "closeFileView drops the watch with the viewer");
});

test("it waits with the romp loader and fails with the kernel's own words, never a blank pane", () => {
  assert.match(VIEW, /romp-swirl-glyph\.svg/, "loading-state rule: the swirl goes up first");
  assert.match(VIEW, /fileview-dot/);
  // a 404/413/415 body IS the explanation (the 413 names the size and the cap) — show it, don't swallow
  // it. The status rides along since 2026-08-09, so the catch can decide whether to offer the download.
  assert.match(VIEW, /if \(!r\.ok\) return r\.text\(\)\.then\(\(t\) => \{\s*\n\s*throw Object\.assign\(new Error\(t \|\| \("HTTP " \+ r\.status\)\), \{ status: r\.status \}\);\s*\n\s*\}\);/);
  assert.match(VIEW, /const why = el\("div", "fileview-err"\);/);
  // a reply that lands after the user closed the viewer paints nothing
  assert.match(VIEW, /if \(!document\.getElementById\("romp-fileview"\)\) return;/);
});

test("it reuses fileUrl, so a REMOTE session's file is relayed from the host that owns it", () => {
  assert.match(VIEW, /import \{ fileUrl \} from "\.\/preview";/);
  assert.match(VIEW, /import \{ openPdfTab, wantsOwnTab \} from "\.\/preview";/);   // + the PDF tab opener and its gesture test (2026-09-06/07)
  assert.match(VIEW, /fetch\(fileUrl\(path, sid\), \{ cache: "no-store" \}\)/);
});

// executed: the extension→language map must never GUESS. highlightAuto on a config file or a log picks a
// language at random and paints it as information the file does not contain.
test("langFor maps known extensions and returns null rather than guessing", () => {
  const LANG: Record<string, string> = {
    py: "python", pyi: "python", js: "javascript", jsx: "javascript", mjs: "javascript",
    cjs: "javascript", ts: "typescript", tsx: "typescript", json: "json", jsonc: "json",
    yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash", bats: "bash",
    html: "xml", htm: "xml", xml: "xml", svg: "xml", vue: "xml", css: "css", scss: "css",
    md: "markdown", markdown: "markdown", diff: "diff", patch: "diff",
    rs: "rust", go: "go", c: "c", h: "c", java: "java", sql: "sql", toml: "ini", ini: "ini",   // viewer-grammars.ts
  };
  // the map above is the module's, row for row (a drift here is a drift in what a file opens as)
  const src = VIEW.slice(VIEW.indexOf("const LANG: Record<string, string> = {"), VIEW.indexOf("};", VIEW.indexOf("const LANG: Record<string, string> = {")));
  for (const [ext, lang] of Object.entries(LANG)) assert.match(src, new RegExp("\\b" + ext + ': "' + lang + '"'), ext + " is in file-view.ts's LANG");
  assert.match(VIEW, /^import "\.\/viewer-grammars";/m, "the six grammars the new rows name are registered by the module the viewer imports");
  const langFor = (p: string): string | null => LANG[p.slice(p.lastIndexOf(".") + 1).toLowerCase()] || null;
  assert.equal(langFor("kernel/kernel.py"), "python");
  assert.equal(langFor("ui/webview/render.TS"), "typescript");   // case-insensitive
  assert.equal(langFor("notes.md"), "markdown");
  assert.equal(langFor("src/main.rs"), "rust");
  assert.equal(langFor("Cargo.toml"), "ini", "toml is hljs's ini grammar");
  assert.equal(langFor("include/api.h"), "c");
  for (const p of ["server.log", "Makefile", "a.conf", "data.csv", "x.hs"]) {
    assert.equal(langFor(p), null, p + " has no registered grammar → plain, not a guess");
  }
  assert.doesNotMatch(VIEW, /hljs\.highlightAuto\(/, "auto-detection is what this map exists to avoid");
});

// ── formatting (the user 2026-08-09): the hljs palette, Raw ⇄ Rendered for markdown, and word wrap ──

// A. The viewer wraps every token in .hljs-* spans, and it renders in BOTH documents — the chat (file
// links) and the feed (the file browser) — so both sheets must carry the SAME palette (one treatment,
// two sheets — the .romp-acted precedent). This pins every rule in both and catches drift.
test("the hljs token palette lives in feed.css too, identical to the chat's", () => {
  const STYLES = CHAT_CSS;
  // tokenized 2026-09-02 (the light theme re-inks the same names; theme-parity.test.ts holds the
  // token set + its contrast in both themes) — the dark :root values are the exact hexes these
  // rules always carried: fg #d8c6a8, kw #c98a6a, str #9fb878, num #d4a36a, cmt #978f81 (raised from
  // #6f6a5f, which sat at 2.85:1 on a code block), title #e1c08d, meta #9a8f7a, attr #cdaf7e
  const rules = [
    /\.hljs \{ color: var\(--hl-fg\); background: transparent; \}/,
    /\.hljs-keyword, \.hljs-built_in, \.hljs-literal, \.hljs-type \{ color: var\(--hl-kw\); \}/,
    /\.hljs-string, \.hljs-attr, \.hljs-regexp \{ color: var\(--hl-str\); \}/,
    /\.hljs-number \{ color: var\(--hl-num\); \}/,
    /\.hljs-comment, \.hljs-quote \{ color: var\(--hl-cmt\); font-style: italic; \}/,
    /\.hljs-title, \.hljs-title\.function_, \.hljs-section \{ color: var\(--hl-title\); \}/,
    /\.hljs-name, \.hljs-tag \{ color: var\(--hl-kw\); \}/,
    /\.hljs-params, \.hljs-variable, \.hljs-property \{ color: var\(--hl-fg\); \}/,
    /\.hljs-meta \{ color: var\(--hl-meta\); \}/,
    /\.hljs-attribute \{ color: var\(--hl-attr\); \}/,
    /\.hljs-addition \{ color: var\(--hl-str\); \}/,
    /\.hljs-deletion \{ color: var\(--err\); \}/,
    /--hl-fg: #d8c6a8; --hl-kw: #c98a6a; --hl-str: #9fb878; --hl-num: #d4a36a;/,
    /--hl-cmt: #978f81; --hl-title: #e1c08d; --hl-meta: #9a8f7a; --hl-attr: #cdaf7e;/,   // --hl-cmt raised to 4.79:1 on a code block (was #6f6a5f, 2.85:1); theme-parity.test.ts holds the pair
  ];
  for (const r of rules) {
    assert.match(FEED_CSS, r, "feed.css is missing a palette rule: " + r.source);
    assert.match(STYLES, r, "styles.css drifted from the shared palette: " + r.source);
  }
});

// B, executed: the persisted view-format prefs. RENDERED is the markdown default (the user's explicit
// call, 2026-08-09) and any malformed stored value reads as the defaults — a corrupt entry may cost the
// preference, never the viewer (feed-view-state's parseViewState contract).
test("format prefs: rendered is the markdown default, and a corrupt entry reads as the defaults", () => {
  // wrap is GONE from the format state (the user 2026-08-24) — a stored wrap key from the toggle
  // era parses away silently
  type Fmt = { md: "rendered" | "raw" };
  const parseFmt = (raw: string | null): Fmt => {
    const def: Fmt = { md: "rendered" };
    if (!raw) return def;
    try {
      const o = JSON.parse(raw) as { md?: unknown };
      if (!o || typeof o !== "object") return def;
      return { md: o.md === "raw" ? "raw" : "rendered" };
    } catch { return def; }
  };
  assert.deepEqual(parseFmt(null), { md: "rendered" }, "first open: rendered");
  assert.deepEqual(parseFmt('{"md":"raw","wrap":true}'), { md: "raw" }, "the toggle-era wrap key parses away");
  assert.deepEqual(parseFmt("not json"), { md: "rendered" });
  assert.deepEqual(parseFmt('{"md":"purple","wrap":"yes"}'), { md: "rendered" },
                   "foreign values fall to the defaults field by field");
  // replica ↔ source
  assert.match(VIEW, /const def: FileViewFmt = \{ md: "rendered" \};/);
  assert.match(VIEW, /return \{ md: o\.md === "raw" \? "raw" : "rendered" \};/);
  // …and the prefs persist in localStorage, the feed-view-state call: per-BROWSER view state that must
  // survive a kernel restart without a round-trip to the thing that just restarted
  assert.match(VIEW, /const FMT_KEY = "romp:fileviewFmt";/);
  assert.match(VIEW, /localStorage\.getItem\(FMT_KEY\)/);
  assert.match(VIEW, /localStorage\.setItem\(FMT_KEY, JSON\.stringify\(f\)\)/);
});

// B: the toggle itself — markdown only, and the rendered path is sanitized. These are arbitrary bytes
// off a disk and marked emits raw HTML verbatim, so DOMPurify sits between it and the DOM, through the
// ONE sanitizer the chat's md() uses too (sanitizeMd, md-sanitize.ts); what it does to a file's own HTML
// is executed in headless Chromium by md-sanitize-browser.test.ts.
test("Raw ⇄ Rendered exists for markdown ONLY, and nothing reaches innerHTML unsanitized", () => {
  assert.match(VIEW, /const isMd = langFor\(path\) === "markdown";/);
  // the two buttons are built inside the isMd gate — a .py file shows no Rendered/Raw toggle
  assert.match(VIEW, /if \(isMd\) \{\s*\n\s*const seg = el\("span", "fileview-seg"\);[\s\S]{0,400}?for \(const mode of \["rendered", "raw"\] as const\)/,
    "the pair is built inside the isMd gate, into ONE segmented wrapper (T367)");
  assert.match(VIEW, /const rendered = isMd && fmt\.md === "rendered";/, "non-md never renders as prose");
  assert.match(VIEW, /import \{ sanitizeMd \} from "\.\/md-sanitize";/);
  assert.doesNotMatch(VIEW, /from "dompurify"/, "the viewer spells no profile of its own: every option comes through md-sanitize.ts");
  // the sanitized <body>'s children are adopted as they are (no re-parse of a serialized string)
  assert.match(VIEW, /box\.replaceChildren\(\.\.\.Array\.from\(sanitizeMd\(dirty\)\.childNodes\)\);/);
  // a README's links open a NEW tab rather than navigating the hosting pane's document away
  assert.match(VIEW, /target = "_blank"/);
  assert.match(VIEW, /rel = "noopener"/);
  // fenced blocks highlight only a NAMED, registered language (the same no-guessing rule as langFor); then EVERY fence,
  // named or not, gets the chat's rows and Copy (code-block.ts), Copy with the fence's text as the file holds it
  // (fence-source.ts), the rendered text when the fence was not found there (code-block.test.ts pins the pass)
  assert.match(VIEW, /if \(lang && hljs\.getLanguage\(lang\)\) \{/);
  assert.match(VIEW, /^import \{ wrapCodeLines, addCopyBtn \} from "\.\/code-block";/m);
  // the two calls close the forEach's callback, outside the language branch (indentation and the `});` anchor it there)
  assert.match(VIEW, /\n    wrapCodeLines\(codeEl\);\n    if \(host\) addCopyBtn\(host, toCopy\);\n  \}\);/);
  assert.match(VIEW, /const toCopy = \(queued && queued\.length \? queued\.shift\(\) : null\) \?\? raw;/);
  // the prose typography exists on BOTH sheets (the chat's .md block is the reference aesthetic)
  assert.match(FEED_CSS, /\.fileview-md \{/);
  assert.match(FEED_CSS, /\.fileview-md pre code \{/);
  assert.match(CHAT_CSS, /\.fileview-md \{/);
  assert.match(CHAT_CSS, /\.fileview-md pre code \{/);
  // toggles acknowledge in the same synchronous tick: click → save → renderBody, which flips .on
  assert.match(VIEW, /b\.addEventListener\("click", \(\) => \{ fmt\.md = mode; saveFmt\(fmt\); renderBody\(\); \}\);/);
  assert.match(VIEW, /b\.classList\.toggle\("on", on\);/);
  assert.match(FEED_CSS, /\.fileview-btn\.on \{ color: var\(--accent\); border-color: var\(--accent\);/);
  assert.match(CHAT_CSS, /\.fileview-btn\.on \{ color: var\(--accent\); border-color: var\(--accent\);/);
});

// C, executed: wrap mode's numbering. A flat gutter misaligns the moment one logical line wraps onto
// several visual lines, so wrap mode restructures — each logical line is a .fv-cl row numbered by a CSS
// counter — instead of shipping a drifting column. hljs spans can cross newlines, so each row must
// re-open what the previous row left unclosed (render.ts's wrapCodeLines balance walk).
test("wrap mode: per-line rows, spans rebalanced across newlines, no phantom trailing row", () => {
  const wrapNumberedHtml = (html: string): string => {
    const lines = html.split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    let open: string[] = [];
    return lines.map((ln) => {
      const prefix = open.join("");
      const re = /<span[^>]*>|<\/span>/g; let m; const stack = open.slice();
      while ((m = re.exec(ln))) { if (m[0] === "</span>") stack.pop(); else stack.push(m[0]); }
      const suffix = "</span>".repeat(Math.max(0, stack.length));
      open = stack;
      return `<span class="fv-cl"><span class="fv-ct">${prefix}${ln}${suffix}</span></span>`;
    }).join("");
  };
  // a string token spanning a newline: closed at the end of row 1, re-opened at the start of row 2
  const out = wrapNumberedHtml('<span class="hljs-string">"a\nb"</span>\nplain');
  const rows = out.split('<span class="fv-cl">').filter(Boolean);
  assert.equal(rows.length, 3, "three logical lines, three rows");
  for (const row of rows) {
    const opens = (row.match(/<span[^>]*>/g) || []).length;
    const closes = (row.match(/<\/span>/g) || []).length;
    // +1: the .fv-cl open itself was consumed as the split delimiter
    assert.equal(opens + 1, closes, "a row must close every span it opens: " + row);
  }
  assert.match(rows[0], /<span class="hljs-string">"a<\/span>/);
  assert.match(rows[1], /^<span class="fv-ct"><span class="hljs-string">b"<\/span>/);
  assert.equal((wrapNumberedHtml("a\n").match(/fv-cl/g) || []).length, 1,
               "a trailing newline is not a phantom row — same rule as the gutter");
  // replica ↔ source
  assert.match(VIEW, /return `<span class="fv-cl"><span class="fv-ct">\$\{prefix\}\$\{ln\}\$\{suffix\}<\/span><\/span>`;/);
});

// C: the toggle and the CSS that carries the honest gutter answer
test("long lines ALWAYS soft-wrap — the dedicated toggle button is gone (the user 2026-08-24)", () => {
  assert.doesNotMatch(VIEW, /wrapBtn/, "no wrap chrome anywhere in the modal");
  assert.match(VIEW, /codeBlock\(text, path, true\)/, "the pre view is born wrapped");
  // wrap mode returns BEFORE the sibling gutter is built — a misaligned column cannot exist
  assert.match(VIEW, /if \(wrapLines\) \{[\s\S]*?return wrap;\s*\}\s*const gutter = el\("div", "fileview-gutter"\);/);
  // plain files wrap too: no grammar → the text is HTML-escaped before the line walk
  assert.match(VIEW, /code\.innerHTML = wrapNumberedHtml\(hl !== null \? hl : escapeHtml\(text\)\);/);
  for (const SHEET of [FEED_CSS, CHAT_CSS]) {
    assert.match(SHEET, /\.fileview-pre\.fileview-wrap \{ white-space: pre-wrap/);
    assert.match(SHEET, /\.fileview-wrap \.fv-cl::before \{[\s\S]*?counter-increment: fvln/);
    assert.match(SHEET, /\.fileview-wrap \.fv-cl::before \{[\s\S]*?user-select: none/);
  }
});

test("a file opened FROM the listing offers the way back — close only the viewer, listing intact beneath", () => {
  // the one-directional stack: the browser sits beneath, so closing just the viewer IS the back;
  // presence-gated on the browser's DOM id (import-free), absent for path-link opens
  assert.match(VIEW, /if \(document\.getElementById\("romp-filebrowse"\)\) \{/);
  assert.match(VIEW, /back\.textContent = "‹ Files"; back\.title = "Back to the file listing";/);
  assert.match(VIEW, /back\.addEventListener\("click", \(\) => closeFileView\(\)\);/);
});

// ── download (the user 2026-08-09): any linked file can be SAVED, including everything the pane cannot
// show — the kernel's ?download=1 serves anything on disk (the rationale lives with _file_download in
// kernel.py: the view allowlists are a rendering choice, not a security boundary). ──

test("the title bar offers Download as the lightbox's tray glyph, in the file group beside Copy path, at the same-origin download URL (T367)", () => {
  // the URL is fileUrl + the download switch: same origin, cookie-authed, and federation-aware for
  // free — fileUrl already routes a remote session's file through the /remote/<host>/file relay
  assert.match(VIEW, /const dlUrl = fileUrl\(path, sid\) \+ "&download=1";/);
  assert.match(VIEW, /dl\.innerHTML = ICON_DOWNLOAD;/, "the one tray drawing (icons.ts), not a word");
  assert.match(VIEW, /dl\.title = "Download"; dl\.setAttribute\("aria-label", "Download"\);/, "the word rides the title and aria-label");
  assert.doesNotMatch(VIEW, /dl\.textContent = "Download";/);
  // in the file group beside Copy path (a glyph too), wearing the same button class
  assert.match(VIEW, /fileGroup\.appendChild\(dl\);/);
  assert.match(VIEW, /const copy = el\("button", "fileview-btn fileview-icon"\) as HTMLButtonElement;/);
  assert.match(VIEW, /fileGroup\.appendChild\(copy\);/);
  // the wider gaps apply only where groups exist: the close cross after a GROUP sibling, never the file browser's row or
  // the URL viewer's flat row (review)
  for (const css of [CHAT_CSS, FEED_CSS]) {
    assert.match(css, /\.fileview-acts > \.fileview-group \+ \.fileview-group, \.fileview-acts > \.fileview-group ~ \.fileview-close \{ margin-left: 10px; \}/);
    assert.doesNotMatch(css, /\.fileview-acts > \.fileview-close \{/, "an unscoped close margin would move the file browser's and the URL viewer's cross");
  }
  assert.match(VIEW, /const dl = el\("button", "fileview-btn"\) as HTMLButtonElement;/, "no new styling, no new font size");
});

test("startDownload hands the URL to the browser's downloader and never wipes the pane", () => {
  // an <a download> click: the BROWSER owns the request (its progress UI, its save location), and the
  // kernel's attachment disposition means the page never navigates — the viewer stays put
  assert.match(VIEW, /const a = document\.createElement\("a"\);\s*\n\s*a\.href = url;\s*\n\s*a\.download = "";/);
  assert.match(VIEW, /document\.body\.appendChild\(a\);\s*\n\s*a\.click\(\);\s*\n\s*a\.remove\(\);/);
  assert.doesNotMatch(VIEW, /location\.href\s*=/, "no navigation — a wiped pane is the failure mode this avoids");
  // …and the click acknowledges itself (ui/CLAUDE.md): the download UI can take a beat over a tunnel
  assert.match(VIEW, /btn\.textContent = "Downloading…";/);
});

// executed: which fetch failures still deserve a Download offer? Exactly the ones that mean the file
// EXISTS — 413 (too large to render) and 415 (on disk but not viewable: a .zip, a binary named like
// text). A 404 is genuinely missing, and offering to download it would be a lie.
test("offersDownload: 413 and 415 offer, 404 and everything else do not", () => {
  const offersDownload = (status: number | undefined): boolean => status === 413 || status === 415;
  assert.equal(offersDownload(413), true, "too big to render ≠ too big to save");
  assert.equal(offersDownload(415), true, "exists-but-unviewable is the case the button exists for");
  assert.equal(offersDownload(404), false, "genuinely missing → nothing to offer");
  assert.equal(offersDownload(403), false);
  assert.equal(offersDownload(undefined), false, "a network failure carries no status and no offer");
  // replica ↔ source
  assert.match(VIEW, /return status === 413 \|\| status === 415;/);
});

test("a refusal renders the kernel's words PLUS the way out — gated on offersDownload", () => {
  // the status rides the thrown error so the catch can tell "there but unshowable" from "not there"
  assert.match(VIEW, /throw Object\.assign\(new Error\(t \|\| \("HTTP " \+ r\.status\)\), \{ status: r\.status \}\);/);
  // the offer appends to the SAME error pane that shows the kernel's message — an offer, not a dead end
  assert.match(VIEW, /if \(offersDownload\(\(err as \{ status\?: number \}\)\.status\)\) \{/);
  assert.match(VIEW, /const offer = el\("button", "fileview-btn fileview-err-dl"\) as HTMLButtonElement;/);
  assert.match(VIEW, /why\.appendChild\(offer\);/);
  assert.match(VIEW, /offer\.addEventListener\("click", \(\) => startDownload\(dlUrl, offer\)\);/);
  assert.match(FEED_CSS, /\.fileview-err-dl \{ display: block; margin-top: 10px; \}/);
  assert.match(CHAT_CSS, /\.fileview-err-dl \{ display: block; margin-top: 10px; \}/);
});

test("Edit is consent-gated, and the gate is the KERNEL's flag, not the button (the user 2026-08-22)", () => {
  // the click asks the kernel's live flag first — never a cached copy, another machine may have flipped it
  assert.match(VIEW, /fetch\(kernelUrl\("\/version"\), \{ cache: "no-store" \}\)/);
  assert.match(VIEW, /\.fileEditing;/);
  // no flag → a plain-words popup; only a YES posts the opt-in, and it broadcasts (KERNEL_SETTING)
  // — stamped with the gesture's own time, so a copy queued for a down host and flushed hours
  // later can never outrank a newer pick at the kernel (the store orders applies by gt)
  assert.match(VIEW, /window\.confirm\(\s*\n?\s*"Allow editing files from the dashboard\?/);
  assert.match(VIEW, /post\(\{ type: "setFileEditing", enabled: true, gt: gclock\.stamp\("file-editing"\) \}\);/,
    "…minted through the gesture clock, above every stamp the /version read just reported");
  assert.match(VIEW, /const gclock = require\("\.\/gesture-clock\.js"\);/, "the viewer loads the clock");
  assert.match(VIEW, /gclock\.learnAll\(v\.settingsGt\);/, "the consent check's /version read teaches the clock");
  // the popup's promise of a gear off-switch is real, and the save route refuses server-side
  const GEAR = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.js"), "utf8");
  assert.ok(GEAR.includes("'setFileEditing'"), "the gear can turn it back off");
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(KERNEL, /if not _file_editing_on\(\):/);
  assert.match(KERNEL, /dashboard file editing is off on this machine/);
});

// ── inline images + PDFs: a clicked .png used to open as line-numbered mojibake — the fetch pipeline
// called r.text() unconditionally on any 200. The viewer branches on the KERNEL'S OWN Content-Type
// verdict (the authoritative-source rule; the kernel derives mime locally and the relay re-derives
// it, so the header is a verdict, never an echo — no client-side extension re-test), takes the
// already-fetched bytes as a blob (no second request), and renders media as media. ──

// executed: the routing replica — which body call a 200 gets, by the kernel's header alone
test("the media branch keys on the kernel's Content-Type verdict, never the extension", () => {
  const mediaKind = (ct: string): "img" | "pdf" | null =>
    ct.startsWith("image/") ? "img" : ct.startsWith("application/pdf") ? "pdf" : null;
  assert.equal(mediaKind("image/png"), "img");
  assert.equal(mediaKind("image/svg+xml"), "img", "SVG is an image here — the <img> surface");
  assert.equal(mediaKind("application/pdf"), "pdf");
  assert.equal(mediaKind("text/plain; charset=utf-8"), null, "text keeps the r.text() pipeline unchanged");
  assert.equal(mediaKind(""), null, "no header → the text path, exactly the pre-image behavior");
  // replica ↔ source: the flags read the kernel's header, and blob is taken ONLY for media
  assert.match(VIEW, /const ct = r\.headers\.get\("Content-Type"\) \|\| "";/);
  assert.match(VIEW, /isImage = ct\.startsWith\("image\/"\);/);
  assert.match(VIEW, /isPdf = ct\.startsWith\("application\/pdf"\);/);
  assert.match(VIEW, /return isImage \|\| isPdf \? r\.blob\(\) : r\.text\(\);/);
  // never a client-side extension re-test: preview.ts's extension probe stays out of this module
  assert.doesNotMatch(VIEW, /previewKind\(/);
  assert.doesNotMatch(VIEW, /IMG_EXT/);
});

test("a 200 image renders ONE <img> at an object URL; the quote gesture stays off RENDERED media", () => {
  const openFn = VIEW.split("export function openFileView")[1].split("function offersDownload")[0];
  // the blob becomes an object URL only AFTER the still-open/still-this-viewer checks — a viewer
  // closed or replaced mid-flight creates nothing to leak
  assert.ok(openFn.indexOf('if (!document.getElementById("romp-fileview")) return;')
            < openFn.indexOf("URL.createObjectURL"),
    "no URL is minted for a viewer that is already gone");
  assert.match(openFn, /if \(!wrap\.isConnected\) return;/);
  // renderBody's img/PDF arm renders and returns — an <img>/iframe body has no honest text to
  // quote (affordance honesty: no real target, no affordance), so the mouseup seed gates off
  // RENDERED media too. The SVG SOURCE view is the deliberate exception — a text view, covered by
  // the media-gate test below.
  const mediaBranch = VIEW.split("if (isImage || isPdf) {")[1].split("if (text === null || editing) return;")[0];
  const renderedArm = mediaBranch.slice(mediaBranch.indexOf("body.replaceChildren(isPdf"));
  assert.ok(renderedArm.length > 0, "the img/PDF render arm exists");
  assert.match(mediaBranch, /imgBlock\(objUrl, path, imgFailed\)/);
  assert.match(VIEW, /if \(\(isImage \|\| isPdf\) && !\(svgSource && svgText !== null\)\) return;/);
  // the romp loader holds the body until the bytes land (the loading-state rule)
  assert.match(mediaBranch, /if \(objUrl === null\) return;/);
  // the <img> itself: one element, src = the object URL, capped like the lightbox's image on BOTH
  // sheets (the viewer mounts in both documents — the .romp-acted precedent)
  const imgFn = VIEW.split("function imgBlock")[1].split("// The PDF body")[0];
  assert.match(imgFn, /el\("img", "fileview-img"\)/);
  assert.match(imgFn, /img\.src = objUrl;/);
  for (const SHEET of [FEED_CSS, CHAT_CSS]) {
    assert.match(SHEET, /\.fileview-img \{[^}]*object-fit: contain[^}]*\}/);
    assert.match(SHEET, /\.fileview-imgbox \{/);
  }
});

// ── media gating is RENDERED-media gating: the gate's rationale — "no honest text to quote" — is
// true of the img/PDF surfaces only. The SVG SOURCE view is codeBlock output, real text nodes, so
// a selection there seeds a labeled quote chip exactly as in any text view; a blanket media gate
// would make an .svg's XML unquotable. ──
test("the quote seed gates off RENDERED media only — the SVG Source view is a text view like any other", () => {
  // executed: the seed offer across the view states (the no-target gate holds throughout — a
  // reachable composer, own document or the chat's through the shell, is what makes a gesture)
  const seedable = (target: boolean, isImage: boolean, isPdf: boolean, srcView: boolean): boolean =>
    target && !((isImage || isPdf) && !srcView);
  assert.equal(seedable(true, true, false, true), true, "SVG Source view: the selection seeds a chip");
  assert.equal(seedable(true, true, false, false), false, "the img view has no honest text to quote");
  assert.equal(seedable(true, false, true, false), false, "the PDF iframe owns its own surface");
  assert.equal(seedable(false, true, false, true), false, "no composer reachable still gates everything off");
  assert.equal(seedable(true, false, false, false), true, "plain text views are untouched");
  // source: the media arm of the mouseup gate carves out the Source view, sitting AFTER the
  // no-target gate (whose pin lives in the through-the-shell test above)
  assert.match(VIEW, /if \(!seedTarget\) return;\n(\s*\/\/[^\n]*\n)*\s*if \(\(isImage \|\| isPdf\) && !\(svgSource && svgText !== null\)\) return;/);
  // anchoring reads the text THE VIEW SHOWS — the Source view's decoded XML, never the text
  // pipeline's null — so a quote on the XML earns its path:line label (viewText, pinned with the
  // fresh-read test above); renderBody's Source arm builds those text nodes through codeBlock
  const mediaBranch = VIEW.split("if (isImage || isPdf) {")[1].split("if (text === null || editing) return;")[0];
  const srcArm = (mediaBranch.split("if (svgSource && svgText !== null) {")[1] || "").split("\n      }")[0];
  assert.ok(srcArm, "the Source-view arm exists inside the media branch");
  assert.match(srcArm, /body\.replaceChildren\(codeBlock\(svgText, path, true\)\);/);
});

test("image mode hides Edit; Download, Copy path, GitHub, ✕ and the dir-link survive", () => {
  const mediaBranch = VIEW.split("if (isImage || isPdf) {")[1].split("if (text === null || editing) return;")[0];
  // (Wrap needs no hiding — the toggle button is gone everywhere, its stored key pinned away above)
  // Edit was ALREADY gated on the kernel's text verdict — an image/* response sets isText false, so
  // the existing arm hides it; both halves stay pinned (file-edit.test.ts pins the isText line too)
  assert.match(VIEW, /isText = \(r\.headers\.get\("Content-Type"\) \|\| ""\)\.startsWith\("text\/plain"\)/);
  assert.match(VIEW, /editBtn\.hidden = editing \|\| text === null \|\| !isText \|\| !mtimeNs;/);
  // the media branch touches NONE of the keepers — they are built unconditionally before the fetch
  // (the dir-link rides the title bar, outside renderBody entirely)
  for (const keeper of ["dl.", "copy.", "close.", "gh."])
    assert.ok(!mediaBranch.includes(keeper), keeper + " must not be re-hidden for images");
  // markdown's Rendered/Raw segs exist only for .md files (the isMd gate, pinned above), and an .md
  // is never served image/* — so the segs cannot coexist with an image body by construction
});

test("SVG renders via <img> ONLY — never innerHTML, never an iframe: its scripts must never run", () => {
  // the kernel serves .svg as image/svg+xml on purpose (an <img> never runs SVG scripts — kernel.py's
  // preview comment), and the relay re-derives the type locally; the viewer must keep that surface
  const imgFn = VIEW.split("function imgBlock")[1].split("// The PDF body")[0];
  assert.doesNotMatch(imgFn, /innerHTML/, "the XSS property: SVG bytes never become live DOM");
  assert.doesNotMatch(imgFn, /iframe/, "an iframed SVG is a document — scripts would run");
  assert.match(imgFn, /el\("img", "fileview-img"\)/);
  // THE MEDIA BRANCH ITSELF is the surface a mutation actually hits (a proven mutation: a
  // `body.innerHTML = svgText` swapped into the branch kept every test green — the innerHTML pin
  // above covers only imgBlock, and a codeBlock string pin matched its own commented-out corpse).
  // So the branch source is audited directly: no HTML-parsing sink of ANY kind, in code or comment.
  const mediaBranch = VIEW.split("if (isImage || isPdf) {")[1].split("if (text === null || editing) return;")[0];
  assert.ok(mediaBranch.length > 0, "media-branch anchors moved — re-anchor this extraction");
  assert.doesNotMatch(mediaBranch, /innerHTML/, "the XSS property, on the branch that holds the bytes");
  assert.doesNotMatch(mediaBranch, /insertAdjacentHTML/);
  assert.doesNotMatch(mediaBranch, /outerHTML|document\.write|DOMParser|createContextualFragment/);
  // …its only writes to the body element are replaceChildren of BUILT elements (the safe sink) —
  // a new sink added to the branch must show up here and be argued for
  const sinks = mediaBranch.match(/body\.\w+\s*[(=]/g) || [];
  assert.ok(sinks.length > 0 && sinks.every((s) => /^body\.replaceChildren\s*\($/.test(s)),
    "the media branch's only body writes are replaceChildren(...): " + JSON.stringify(sinks));
  // …and the Source toggle's codeBlock render — the same escape/highlight path every text file
  // takes (textContent / escapeHtml), never a parse into live DOM — is LIVE CODE, not a comment
  const live = mediaBranch.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
  assert.match(live, /body\.replaceChildren\(codeBlock\(svgText, path, true\)\)/,
    "the SVG Source view renders through codeBlock, uncommented (born wrapped, like every code view)");
  assert.match(VIEW, /isSvgImage = ct === "image\/svg\+xml";/, "the toggle keys on the kernel's verdict too");
  assert.match(VIEW, /mediaBlob\.text\(\)/, "the source view decodes the SAME fetched bytes — no second request");
});

// executed: the object-URL lifecycle — every teardown revokes
test("the object URL is revoked on close AND on replace-open — none leaks, one live at a time", () => {
  const sim = () => {
    let live: string | null = null;
    let seq = 0;
    const revoked: string[] = [];
    const dropMediaUrl = () => { if (live) { revoked.push(live); live = null; } };
    const openView = () => { dropMediaUrl(); live = "blob:" + ++seq; };  // replace-teardown, then the fetch lands
    const closeView = () => dropMediaUrl();
    openView();          // an image opens
    openView();          // Reload / a second click replaces it — blob:1 must go
    closeView();         // ✕ — blob:2 must go
    return { revoked, live };
  };
  const r = sim();
  assert.deepEqual(r.revoked, ["blob:1", "blob:2"], "the replaced URL AND the closed URL both go");
  assert.equal(r.live, null, "nothing outlives the viewer");
  // replica ↔ source: ONE module-level registration, dropped by BOTH exits (the editHooks/gitHooks
  // precedent), revoking through URL.revokeObjectURL
  assert.match(VIEW, /let mediaUrlLive: string \| null = null;/);
  assert.match(VIEW, /URL\.revokeObjectURL\(mediaUrlLive\)/);
  const closeFn = VIEW.split("export function closeFileView")[1].split("/** Show `path`")[0];
  assert.match(closeFn, /dropMediaUrl\(\);/, "close revokes");
  const openFn = VIEW.split("export function openFileView")[1].split("function offersDownload")[0];
  assert.match(openFn, /dropMediaUrl\(\);/, "the replace path revokes (the conflict Reload re-opens through here)");
  assert.ok(openFn.indexOf("dropMediaUrl();") < openFn.indexOf('document.getElementById("romp-fileview")?.remove();'),
    "…before the old viewer is torn down, beside the other module-level drops");
  assert.match(openFn, /mediaUrlLive = objUrl;/, "the minted URL is exactly what the teardowns revoke");
});

test("an oversize image lands on the kernel's words + Download — a 413 never reaches the media branch", () => {
  // executed: the pipeline model — !ok throws (status attached) BEFORE any Content-Type branching
  const route = (ok: boolean, ct: string): "error" | "img" | "pdf" | "text" => {
    if (!ok) return "error";
    return ct.startsWith("image/") ? "img" : ct.startsWith("application/pdf") ? "pdf" : "text";
  };
  assert.equal(route(false, "text/plain"), "error",
    "the kernel 413s an oversize image with a text/plain body naming the size and the cap");
  assert.equal(route(true, "image/png"), "img");
  const offersDownload = (status: number | undefined): boolean => status === 413 || status === 415;
  assert.equal(offersDownload(413), true, "…and the error pane still offers the Download the view could not be");
  // source ordering: the throw sits before the media flags are ever assigned
  assert.ok(VIEW.indexOf("if (!r.ok) return r.text().then((t) => {")
            < VIEW.indexOf('isImage = ct.startsWith("image/");'));
});

test("a PDF takes the lightbox's exact iframe treatment, aimed at the already-fetched blob", () => {
  const PREVIEW = web("preview.ts");
  // the reference: openLightbox's pdf arm is a PLAIN iframe — className, src, title, no sandbox
  // attributes to mirror or forget
  assert.match(PREVIEW, /frame\.className = "romp-lightbox-frame";\s*\n\s*frame\.src = fileUrl\(path, sid\);\s*\n\s*frame\.title = path;/);
  const pdfFn = VIEW.split("function pdfBlock")[1].split("/** Bind the pane's WS poster")[0];
  assert.match(pdfFn, /el\("iframe", "fileview-frame"\)/);
  assert.match(pdfFn, /frame\.src = objUrl;/, "the blob URL — the bytes were already fetched once");
  assert.match(pdfFn, /frame\.title = path;/);
  assert.doesNotMatch(pdfFn, /sandbox/, "the lightbox sets none; inventing one here would be a different surface");
  const mediaBranch = VIEW.split("if (isImage || isPdf) {")[1].split("if (text === null || editing) return;")[0];
  assert.match(mediaBranch, /isPdf \? pdfBlock\(objUrl, path\) : imgBlock\(objUrl, path, imgFailed\)/);
  for (const SHEET of [FEED_CSS, CHAT_CSS]) assert.match(SHEET, /\.fileview-frame \{[^}]*height: 100%/);
});

// ── decode failure: a zero-byte or mid-write/truncated image is a 200 whose BYTES will not decode —
// the browser fires the img's error event and used to leave its mute broken-image glyph: no reason,
// no way out. The viewer answers with the 413/415 pane idiom instead: plain words naming what
// happened, the path, and the Download the view could not be. The img's own error event is the
// exact deciding signal (never a timer, never a byte sniff). The PDF iframe has NO equivalent
// failure event — the browser's own viewer owns that surface and reports inside it — so this
// covers images only, deliberately. ──

test("an image 200 that fails to DECODE swaps to the failure pane: plain words + Download, never a mute glyph", () => {
  // executed: the handler's continuation — an object URL of garbage bytes fires `error` once, and
  // the pane replaces the glyph; a decodable image never invokes it
  const sim = (decodes: boolean) => {
    let pane: string[] = [];
    let armed: (() => void) | null = null;
    const imgBlock = (onDecodeFail: () => void): string => { armed = onDecodeFail; return "img"; };
    const imgFailed = () => { pane = ["this image failed to decode — it may be mid-write or truncated", "Download"]; };
    pane = [imgBlock(imgFailed)];              // the media branch renders the img, handler armed
    if (!decodes) armed!();                    // garbage bytes: the browser fires the img's error event
    return pane;
  };
  assert.deepEqual(sim(true), ["img"], "a decodable image just shows");
  assert.deepEqual(sim(false),
    ["this image failed to decode — it may be mid-write or truncated", "Download"],
    "garbage bytes land on words + the way out");
  // source: the handler rides the img itself, armed BEFORE src so no event can slip past it
  const imgFn = VIEW.split("function imgBlock")[1].split("// The PDF body")[0];
  assert.match(imgFn, /^\(objUrl: string, path: string, onDecodeFail: \(\) => void\)/);
  assert.match(imgFn, /img\.addEventListener\("error", onDecodeFail, \{ once: true \}\);\s*\n\s*img\.src = objUrl;/);
  // …and the continuation builds the EXACT failure idiom the 413/415 catch renders: fileview-err
  // words + the path hint + the fileview-err-dl Download wired through startDownload
  const openFn = VIEW.split("export function openFileView")[1].split("function offersDownload")[0];
  const failFn = (openFn.split("const imgFailed = ")[1] || "").split("\n  };")[0];
  assert.ok(failFn, "imgFailed lives in the open viewer's closure — it needs body and dlUrl");
  assert.match(failFn, /el\("div", "fileview-err"\)/);
  assert.match(failFn, /failed to decode/);
  assert.match(failFn, /mid-write or truncated/);
  assert.match(failFn, /el\("div", "fileview-err-hint"\)/);
  assert.match(failFn, /hint\.textContent = path;/);
  assert.match(failFn, /el\("button", "fileview-btn fileview-err-dl"\)/);
  assert.match(failFn, /startDownload\(dlUrl, offer\)/);
  assert.match(failFn, /body\.replaceChildren\(why\);/);
  // a decode failure that settles after the viewer was closed or replaced paints nothing
  assert.match(failFn, /if \(!wrap\.isConnected\) return;/);
  // the PDF arm stays bare — an iframe fires no decode-failure event to key on
  const pdfFn = VIEW.split("function pdfBlock")[1].split("/** Bind the pane's WS poster")[0];
  assert.doesNotMatch(pdfFn, /addEventListener/, "no synthetic failure signal invented for the iframe");
});

// executed: the gutter is a SIBLING of the code, so selecting the code copies it without line numbers
test("the line gutter numbers every line and drops a trailing newline's phantom line", () => {
  const lines = (text: string): string[] => {
    const l = text.split("\n");
    if (l.length && l[l.length - 1] === "") l.pop();
    return l;
  };
  assert.deepEqual(lines("a\nb\nc\n").length, 3, "a trailing newline is not a fourth line");
  assert.deepEqual(lines("a\nb\nc").length, 3);
  assert.deepEqual(lines(""), []);
  assert.match(VIEW, /gutter\.textContent = lines\.map\(\(_, i\) => String\(i \+ 1\)\)\.join\("\\n"\);/);
  assert.match(VIEW, /wrap\.appendChild\(gutter\); wrap\.appendChild\(pre\);/, "sibling, not inside the pre");
  assert.match(FEED_CSS, /\.fileview-gutter \{[\s\S]*?user-select: none;/);
  assert.match(CHAT_CSS, /\.fileview-gutter \{[\s\S]*?user-select: none;/);
});

// ── the session chip (the user 2026-09-03): the title bar names the session the file was opened
// from. The viewer knows only a sid — and its openers mostly know no more (the relay branch and the
// conflict Reload live inside this module, the file browser hands over a bare sid) — so the identity
// is RESOLVED from the sid through a lookup each hosting document registers once at boot. The DOM
// build itself runs for real in fileview-chip.test.ts; these are the source pins. ──

test("the title bar carries a session chip resolved from the sid — never invented, absent when unknown", () => {
  assert.match(VIEW, /import \{ hostOf, bareId, hostNameNodes \} from "\.\/host-prefix";/);
  assert.match(VIEW, /export interface FileViewIdentity \{ name: string; color: \{ bg: string; fg: string \} \| null \}/);
  assert.match(VIEW, /let identityOf: \(sid: string\) => FileViewIdentity \| null = \(\) => null;/,
    "unregistered → nothing to show, not a guess");
  assert.match(VIEW, /export function setFileViewIdentity\(fn: typeof identityOf\): void \{ identityOf = fn; \}/);
  const openFn = VIEW.split("export function openFileView")[1].split("function offersDownload")[0];
  assert.match(openFn, /const owner = sid \? identityOf\(sid\) : null;/, "no sid → the resolver is not even asked");
  assert.match(openFn, /if \(owner\) \{\n\s*sess = el\("span", "fileview-sess"\);/, "no identity → no chip element at all");
  assert.match(openFn, /sess\.replaceChildren\(\.\.\.hostNameNodes\(owner\.name, sid\)\);/, "host: quiet for a remote session");
  assert.match(openFn, /if \(owner\.color\) \{ sess\.style\.background = owner\.color\.bg; sess\.style\.color = owner\.color\.fg; \}/,
    "the session's identity colour, inline — an uncolored stub keeps the sheet's neutral pill");
  assert.match(openFn, /sess\.title = "Opened from the " \+ owner\.name \+ " session";/,
    "capitalized like this bar's other tooltips; 'session' so a name like web is not read as a place");
  assert.match(openFn, /bar\.appendChild\(name\); if \(sess\) bar\.appendChild\(sess\); bar\.appendChild\(acts\);/,
    "between the path and the actions");
  // the signatures every opener and the relay pin depend on: the open answers whether it happened (a
  // dirty-edit veto is false, so the Files pane's recent list records only real opens), and the listener
  // takes an optional relay contract (files.ts takes the shell's relay whole)
  assert.match(VIEW, /export function openFileView\(path: string, sid\?: string \| null, opts\?: \{ line\?: number \| null; frag\?: string \| null \}\): boolean \{/);   // frag: a sibling link's fragment lands after the render
  assert.match(VIEW, /export function initFileView\(poster: \(m: Record<string, unknown>\) => void,\n\s*onRelay\?: \(m: \{ path: string; sid\?: unknown; identity\?: unknown; frag\?: unknown \}\) => void\): void \{/);
});

test("both hosting documents register a resolver beside their initFileView boot", () => {
  // the chat document: the tab set, the way renderTabs names a tab (the session first, then the
  // kernel's tab meta, which keeps a dormant session's name and colour)
  assert.match(RENDER, /import \{ initFileView, setFileViewIdentity, hostStub \} from "\.\/file-view";/);
  assert.match(RENDER, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);\n(\/\/.*\n)*setFileViewIdentity\(\(id\) => \{\n\s*const s = sessions\.get\(id\) \?\? tabMeta\.get\(id\);\n\s*return s && s\.name \? \{ name: s\.name, color: s\.color \?\? null \} : hostStub\(id\);\n\}\);/);
  // the feed document: its session list (the same tab set, relayed per frame), else a card carrying
  // the session's name and colour — never sessionColors, which is keyed by NAME, not sid
  assert.match(FEED, /import \{ initFileView, setFileViewIdentity, hostStub \} from "\.\/file-view";/);
  assert.match(FEED, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);.*\n(\/\/.*\n)*setFileViewIdentity\(\(id\) => \{\n\s*const s = sessionsMeta\.find\(\(x\) => x\.sid === id\) \?\? asks\.find\(\(a\) => a\.sid === id\);\n\s*return s && s\.name \? \{ name: s\.name, color: s\.color \?\? null \} : hostStub\(id\);\n\}\);/);
  const feedReg = FEED.split("setFileViewIdentity(")[1].split("});")[0];
  assert.doesNotMatch(feedReg, /sessionColors/, "a name-keyed index cannot answer a sid");
});

// executed: the ladder each document's resolver runs — its own lists, then the kernel's
// _peer_identity fallback (a remote sid's host + the sid's first 8 characters, uncolored), then no
// chip at all. Synthetic rows: the notes-api world, TESTHOST for the remote kernel.
test("resolver ladder: a named session, then a host-prefixed 8-char stub, then no chip", () => {
  type Id = { name: string; color: { bg: string; fg: string } | null };
  const hostOf = (id: string) => { const i = id.indexOf(":"); return i > 0 ? id.slice(0, i) : ""; };
  const bareId = (id: string) => { const i = id.indexOf(":"); return i > 0 ? id.slice(i + 1) : id; };
  const hostStub = (sid: string): Id | null => {
    const bare = bareId(sid);
    if (!bare) return null;
    const host = hostOf(sid);
    return { name: (host ? host + ":" : "") + bare.slice(0, 8), color: null };
  };
  const WEB = "11111111-2222-3333-4444-555555555555";
  const API = "22222222-3333-4444-5555-666666666666";
  const TESTS = "33333333-4444-5555-6666-777777777777";
  const rows = new Map<string, Id>([
    [WEB, { name: "web", color: { bg: "#3a7bd5", fg: "#ffffff" } }],
    ["TESTHOST:" + API, { name: "TESTHOST:api", color: { bg: "#d53a7b", fg: "#ffffff" } }],   // federation prefixes sid AND name
    [TESTS, { name: "", color: null }],                                                        // a placeholder tab, name not yet known
  ]);
  const resolve = (id: string): Id | null => {
    const s = rows.get(id);
    return s && s.name ? { name: s.name, color: s.color ?? null } : hostStub(id);
  };
  assert.deepEqual(resolve(WEB), { name: "web", color: { bg: "#3a7bd5", fg: "#ffffff" } });
  assert.deepEqual(resolve("TESTHOST:" + API), { name: "TESTHOST:api", color: { bg: "#d53a7b", fg: "#ffffff" } },
    "a remote row keeps its host: prefix — hostNameNodes renders it as quiet metadata");
  assert.deepEqual(resolve("44444444-5555-6666-7777-888888888888"), { name: "44444444", color: null },
    "an unknown local sid → the kernel's 8-character stub, uncolored");
  assert.deepEqual(resolve("TESTHOST:44444444-5555-6666-7777-888888888888"), { name: "TESTHOST:44444444", color: null },
    "an unknown remote sid → host: + stub, so the host still reads as metadata");
  assert.deepEqual(resolve(TESTS), { name: "33333333", color: null },
    "a row with no name yet is not a name — the stub, never an empty chip");
  assert.equal(resolve(""), null, "no sid → no chip");
  assert.equal(resolve("TESTHOST:"), null, "a host with no sid names nothing");
  // replica ↔ source
  assert.match(VIEW, /export function hostStub\(sid: string\): FileViewIdentity \| null \{\n\s*const bare = bareId\(sid\);\n\s*if \(!bare\) return null;\n\s*const host = hostOf\(sid\);\n\s*return \{ name: \(host \? host \+ ":" : ""\) \+ bare\.slice\(0, 8\), color: null \};\n\}/);
});

test("the chip's dress is in BOTH sheets: a fixed-width pill that never yields to the path", () => {
  for (const css of [CHAT_CSS, FEED_CSS]) {
    // A BLOCK container, not inline-flex: text-overflow acts on block containers only — on a flex container
    // the text sits in an anonymous flex item the property cannot reach, and an over-long name hard-clipped
    // at max-width (the review of #970). 0.82em of --fs, the size the bar's buttons wear, so the chip scales
    // with its neighbours; a px value did not.
    assert.match(css, /\.fileview-sess \{ flex: 0 0 auto; display: block; max-width: 38%;[^}]*font-size: 0\.82em;[^}]*overflow: hidden; white-space: nowrap; text-overflow: ellipsis;/);
    const sess = css.slice(css.indexOf(".fileview-sess {"), css.indexOf("}", css.indexOf(".fileview-sess {")));
    assert.doesNotMatch(sess, /inline-flex|align-items|font-size: [\d.]+px/, "no flex container around the text, no px size");
    // color:inherit so the host: token takes the pill's own fg — the global .host-prefix{color:var(--dim)}
    // otherwise wins over the inline foreground and the token is near-invisible on a coloured pill
    // (~1:1 contrast for a remote session's chip). opacity keeps it quiet without dimming to gray.
    assert.match(css, /\.fileview-sess \.host-prefix \{ color: inherit; opacity: 0\.75; \}/, "the host: token uses the pill's fg, quiet");
  }
});

// executed: openFileView's verdict, the head of the function lifted from file-view.ts (plain JS up to the guard's
// reset) and run over a document that does or does not hold a viewer and a close guard that does or does not
// veto. The Files pane's recent list records an open only when this answers true (files.ts openHere), so a
// dirty-edit veto that answered true would list a file that never opened.
test("openFileView answers false when the dirty-edit guard keeps the previous viewer, and falls through otherwise", () => {
  const at = VIEW.indexOf("export function openFileView(");
  const head = VIEW.slice(VIEW.indexOf("): boolean {", at) + "): boolean {".length, VIEW.indexOf("closeGuard = null;", at) + "closeGuard = null;".length);
  assert.match(head, /if \(document\.getElementById\("romp-fileview"\) && closeGuard && !closeGuard\(\)\) return false;/);
  const run = (viewerUp: boolean, guard: (() => boolean) | null) => {
    let asked = 0;
    const document = { getElementById: (id: string) => (viewerUp && id === "romp-fileview" ? {} : null) };
    const closeGuard = guard ? () => { asked++; return guard(); } : null;
    const out = (new Function("document", "closeGuard", "return (function () {" + head + " return { through: true, guard: closeGuard }; })();") as
      (d: unknown, g: unknown) => false | { through: true; guard: unknown })(document, closeGuard);
    return { out, asked };
  };
  const veto = run(true, () => false);
  assert.equal(veto.out, false, "a viewer up whose guard refuses: the open did not happen");
  assert.equal(veto.asked, 1, "the guard was asked once");
  const ok = run(true, () => true);
  assert.deepEqual(ok.out, { through: true, guard: null }, "the guard allowed it: the head falls through and the guard is dropped for the new viewer");
  const none = run(false, () => false);
  assert.deepEqual(none.out, { through: true, guard: null }, "no viewer up: nothing to ask, the guard is not consulted");
  assert.equal(none.asked, 0);
  assert.deepEqual(run(true, null).out, { through: true, guard: null }, "a viewer with no guard (nothing edited) is replaced");
  // and the other end of the function: a completed open answers true
  assert.match(VIEW, /body\.replaceChildren\(why\);\n\s*\}\);\n\s*return true;\n\}/, "openFileView ends by answering true");
});
