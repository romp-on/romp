// A markdown link on the dashboard's OWN origin opens in the file viewer, rendered (the user
// 2026-09-06) — it used to open the raw text in a new tab. The viewer grew a URL mode: the browser
// fetches the URL itself (no kernel route, no proxy — the kernel's /file relay is a preview relay and
// stays one), mdBlock renders it with the shared Rendered ⇄ Raw preference, and relative figures and
// links inside the document resolve against the document rather than the page. Cross-origin .md links
// keep the new tab exactly. The review round added: a streamed, byte-counted body read cancelled with
// the viewer (capped-read.ts), redirect-aware document location, modifier clicks keeping the tab,
// heading ids + in-document fragment links that land instead of spawning a tab, and cap words that
// never read as an equal pair. No jsdom harness for these modules → source pins, plus the executed
// helpers in md-links.test.ts and capped-read.test.ts. Synthetic hosts/paths only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const web = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = web("render.ts");
const VIEW = web("file-view.ts");
const SANITIZE = web("md-sanitize.ts");   // the one sanitizer both md() and mdBlock call (sanitizeMd)
const CHAT_CSS = web("styles.css");
const FEED_CSS = web("feed.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
// the chat pane's own detail moved to the reference (CLAUDE.md "The documentation front pages")
const REF = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "reference.md"), "utf8");

// the chat's global anchor-click delegate (the same isolation chat-link-open.test.ts uses)
const HANDLER = (RENDER.match(/closest\?\.\("a\[href\]"\)[\s\S]*?\}, true\);/) || [""])[0];
// the URL viewer, from its export to the next top-level function
const URL_FN = (VIEW.split("export function openUrlView")[1] || "").split("// Kick the browser's downloader")[0];
// the markdown renderer
const MD_FN = (VIEW.split("function mdBlock(")[1] || "").split("// The image body:")[0];
// the local viewer (the split file-view.test.ts uses — openUrlView sits AFTER offersDownload so it
// never leaks into this slice)
const OPEN_FN = VIEW.split("export function openFileView")[1].split("function offersDownload")[0];
const CLOSE_FN = VIEW.split("export function closeFileView")[1].split("/** Show `path`")[0];

// ── 1. the interception: same-origin .md, unmodified primary click, BEFORE window.open, web only ──

const COND = 'if (!a.dataset.newTab && !e.ctrlKey && !e.metaKey && !e.shiftKey && isMarkdownUrl(href, location.origin)) { openUrlView(href); return; }';

test("the anchor delegate routes a same-origin .md href to the viewer BEFORE the new-tab window.open", () => {
  assert.ok(HANDLER, "found the anchor-click handler");
  assert.ok(HANDLER.includes(COND), "the exact interception condition");
  const viewer = HANDLER.indexOf("openUrlView(href)");
  const tab = HANDLER.indexOf('window.open(href, "_blank", "noopener,noreferrer")');
  assert.ok(viewer > -1 && tab > -1 && viewer < tab, "the viewer branch precedes window.open");
  // …inside the web arm: the check sits after the http/https protocol test
  const webArm = HANDLER.indexOf('location.protocol === "http:" || location.protocol === "https:"');
  assert.ok(webArm > -1 && webArm < viewer, "the .md branch lives in the web (http/https) arm");
  // the cross-origin path still reaches the exact new-tab call it always did
  assert.match(HANDLER, /window\.open\(href, "_blank", "noopener,noreferrer"\);/);
  // the VS Code branch is untouched — openLink still posts to the host, and never opens the viewer
  assert.match(HANDLER, /\} else if \(vscodeApi\) \{\s*\n\s*vscodeApi\.postMessage\(\{ type: "openLink", href \}\);/);
  const vsArm = HANDLER.slice(HANDLER.indexOf("} else if (vscodeApi) {"));
  assert.doesNotMatch(vsArm, /openUrlView/, "the webview cannot reach the kernel origin — no viewer there");
  // the helpers arrive on their own import lines (the openFileView import is pinned verbatim elsewhere)
  assert.match(RENDER, /import \{ openFileClick \} from "\.\/file-view";/);   // the chat opens files through the gesture reader (pdf-new-tab.test.ts)
  assert.match(RENDER, /import \{ openUrlView \} from "\.\/file-view";/);
  assert.match(RENDER, /import \{ isMarkdownUrl \} from "\.\/md-links";/);
});

test("a ctrl-, meta- or shift-click on a same-origin .md keeps the tab: the modifier test sits IN the .md branch", () => {
  const branch = HANDLER.slice(HANDLER.indexOf("if (!a.dataset.newTab"), HANDLER.indexOf("openUrlView(href)"));
  for (const mod of ["!e.ctrlKey", "!e.metaKey", "!e.shiftKey"]) assert.ok(branch.includes(mod), mod + " gates the viewer");
  // the fall-through is the SAME window.open the cross-origin path takes — one new-tab call, no second one
  assert.equal((HANDLER.match(/window\.open\(/g) || []).length, 1, "exactly one window.open in the delegate");
  // middle-click on a LINK is auxclick and is never intercepted: the anchor delegate has no auxclick
  // listener. The ONE auxclick listener in render.ts is onMiddleClick, on the path PILLS (spans, not
  // anchors), where a middle-click opens a PDF in a tab of its own — pdf-new-tab.test.ts pins it
  assert.doesNotMatch(HANDLER, /addEventListener\("auxclick"/);
  assert.equal((RENDER.match(/addEventListener\("auxclick"/g) || []).length, 2,
    "only onMiddleClick (path pills) and the composer ✕'s stopper — both on spans/buttons, never on an anchor");
  assert.match(REF, /ctrl- or ⌘-click still opens the file in\s+a tab/, "the reference says so");
});

test("the whole-backtick URL anchors (url-code-link) flow through the same delegate — no handler of their own", () => {
  const linkify = RENDER.split("function linkifyFileUris(")[1].split("const previewable")[0];
  assert.match(linkify, /a\.href = t;/, "an absolute http(s) href — the delegate sees a scheme");
  assert.match(linkify, /a\.className = "url-code-link";/);
  assert.doesNotMatch(linkify, /addEventListener\("click"|onclick|window\.open|openUrlView/,
    "the anchor carries no click logic; the document-level delegate decides viewer vs tab");
});

// ── 2. the URL viewer itself ──

test("openUrlView exists, fetches the given href from the browser (never fileUrl), and adds no kernel surface", () => {
  assert.match(VIEW, /export function openUrlView\(href: string\): void \{/);
  assert.ok(VIEW.indexOf("export function openUrlView") > VIEW.indexOf("function offersDownload"),
    "sits after offersDownload — outside the slice file-view.test.ts takes as openFileView's body");
  assert.doesNotMatch(OPEN_FN, /openUrlView|kind: "url"/, "the local viewer's body is untouched by URL mode");
  assert.match(URL_FN, /fetch\(href, \{ cache: "no-store", mode: "same-origin", signal: ctrl\.signal \}\)/,
    "same-origin MODE, so a redirect off the origin is refused rather than followed and rendered");
  assert.doesNotMatch(URL_FN, /fileUrl\(|kernelUrl\(|\/file\?|\/remote\//, "the URL is fetched as given — no kernel route, no relay");
  assert.doesNotMatch(URL_FN, /\bpost\(/, "nothing is asked of the kernel over the socket either");
  // …and the kernel gained no route for it
  assert.doesNotMatch(KERNEL, /\/(fetch-url|url-proxy|proxy-url|remote-url)\b/, "no URL relay route was added");
});

test("URL mode: the loader is up before the fetch, the body renders through mdBlock with the document's URL as base", () => {
  assert.match(VIEW, /function loaderEl\(\): HTMLElement \{/);
  assert.match(VIEW, /const load = el\("div", "fileview-load"\);\s*\n\s*load\.innerHTML = '<img src="\/media\/romp-swirl-glyph\.svg" alt=""><span>romp<\/span>'/);
  const loader = URL_FN.indexOf("body.appendChild(loaderEl());");
  const fetchAt = URL_FN.indexOf("fetch(href, { cache");
  assert.ok(loader > -1 && fetchAt > -1 && loader < fetchAt, "loader first, then the fetch replaces it");
  assert.match(URL_FN, /mdBlock\(text, \{ kind: "url", href: loc \}\)/, "the EFFECTIVE location, not the clicked href");
  assert.match(URL_FN, /codeBlock\(text, parts\.base, true\)/, "Raw is the same soft-wrapped code view, highlighted as markdown");
  assert.match(URL_FN, /if \(text === null\) return;/, "the loader holds the body until the bytes land");
});

test("redirects: the document LIVES at the response URL — mdBlock's base and the title follow it; Open ↗ and Copy URL keep the clicked link", () => {
  assert.match(URL_FN, /let loc = href;/);
  assert.match(URL_FN, /const relocate = \(r: Response\) => \{\s*\n\s*loc = r\.url \|\| href;\s*\n\s*parts = urlTitleParts\(loc\);\s*\n\s*dir\.textContent = parts\.dir; base\.textContent = parts\.base; name\.title = loc;/);
  // relocate runs on the response BEFORE any verdict branch — a 404's title names where it was looked for
  const rel = URL_FN.indexOf("relocate(r);");
  assert.ok(rel > -1 && rel < URL_FN.indexOf("settleUrlResponse(r,"), "relocate precedes the verdict");
  assert.match(URL_FN, /fail\("HTTP " \+ v\.status \+ " from " \+ hostWord\(loc\)\)/, "the status line names the effective host");
  // the clicked href is what the user was given: the link-out, the copy, and the failure hint keep it
  assert.match(URL_FN, /a\.href = href; a\.target = "_blank"; a\.rel = "noopener";/);
  assert.match(URL_FN, /navigator\.clipboard\?\.writeText\(href\)/);
  assert.match(URL_FN, /hint\.textContent = href;/);
  assert.doesNotMatch(URL_FN, /writeText\(loc\)|a\.href = loc/);
});

test("URL mode shares the Rendered ⇄ Raw preference (the same localStorage key) and acknowledges the toggle synchronously", () => {
  assert.match(URL_FN, /const fmt = loadFmt\(\);/);
  assert.match(URL_FN, /b\.addEventListener\("click", \(\) => \{ fmt\.md = mode; saveFmt\(fmt\); renderBody\(\); \}\);/);
  assert.match(URL_FN, /b\.classList\.toggle\("on", on\);/);
  assert.match(VIEW, /const FMT_KEY = "romp:fileviewFmt";/);
  assert.equal((VIEW.match(/localStorage\.(get|set)Item\(FMT_KEY/g) || []).length, 2, "one key, read and written in one place");
});

test("URL mode chrome: host/dir/ dimmed (not a browse link) + basename; Open ↗ link-out; Copy URL; ✕; Esc closes", () => {
  assert.match(URL_FN, /let parts = urlTitleParts\(href\);/);
  assert.match(URL_FN, /el\("span", "fileview-dir"\)[\s\S]*?dir\.textContent = parts\.dir;/);
  assert.match(URL_FN, /el\("span", "fileview-base"\)[\s\S]*?base\.textContent = parts\.base;/);
  assert.doesNotMatch(URL_FN, /fileview-dir-link|browseFiles/, "no file browser for a URL — the directory half is plain");
  // the link-out: an anchor in the button dress, new tab, noopener — the GitHub link's treatment
  assert.match(URL_FN, /el\("a", "fileview-btn fileview-gh"\)/);
  assert.match(URL_FN, /a\.textContent = "Open ↗";/);
  // …and it marks itself data-new-tab: its href IS the same-origin .md the delegate would otherwise
  // route straight back into this viewer — the marker is what makes the tab open
  assert.match(URL_FN, /a\.dataset\.newTab = "1";/);
  assert.ok(HANDLER.includes("!a.dataset.newTab &&"), "the delegate honours the marker before the .md check");
  // the marker rides ONLY the link-out: a rendered document's own anchors never carry it, so a
  // sibling .md link keeps navigating in place
  assert.doesNotMatch(MD_FN, /newTab|new-tab/);
  // Copy copies the URL (the local mode's Copy path equivalent), with the same feedback words
  assert.match(URL_FN, /copy\.textContent = "Copy URL";/);
  assert.match(URL_FN, /copy\.textContent = "Copied";/);
  assert.match(URL_FN, /copy\.textContent = "Copy failed";/);
  // ✕ and Esc close through the shared closeFileView
  assert.match(URL_FN, /el\("button", "fileview-btn fileview-close"\)/);
  assert.match(URL_FN, /close\.addEventListener\("click", closeFileView\);/);
  assert.match(URL_FN, /if \(e\.key !== "Escape" \|\| e\.defaultPrevented \|\| !document\.getElementById\("romp-fileview"\)\) return;/);
  // the same modal shell: the id every open/closed check targets, backdrop click closes, body class
  assert.match(URL_FN, /wrap\.id = "romp-fileview";/);
  assert.match(URL_FN, /wrap\.onclick = \(ev\) => \{ if \(ev\.target === wrap\) closeFileView\(\); \};/);
  assert.match(URL_FN, /document\.body\.classList\.add\("fileview-open"\);/);
});

test("URL mode has NO Edit / Save / Download / GitHub / ‹ Files — every one of those is keyed on a kernel reply", () => {
  assert.doesNotMatch(URL_FN, /textContent = "(Edit|Save|Cancel|Download|‹ Files|GitHub ↗)"/);
  assert.doesNotMatch(URL_FN, /fileViewActions|registerFileViewAction|fileGitLink|editingAllowed|enterEdit|startDownload/);
  assert.doesNotMatch(URL_FN, /headers\.get\("(X-Romp-[A-Za-z-]+|Content-Type)"\)/, "no kernel verdict headers are read — there is no kernel reply");
});

test("URL mode replaces an open viewer through the same guarded path (unsaved edits ask first; registrations drop)", () => {
  assert.match(URL_FN, /if \(document\.getElementById\("romp-fileview"\) && closeGuard && !closeGuard\(\)\) return;/);
  for (const drop of ["closeGuard = null;", "editHooks = null;", "gitHooks = null;", "dropMediaUrl();", "dropUrlRead();"])
    assert.ok(URL_FN.includes(drop), drop + " before the old viewer is torn down");
  assert.ok(URL_FN.indexOf("dropUrlRead();") < URL_FN.indexOf('document.getElementById("romp-fileview")?.remove();'));
});

// ── 3. the in-flight read is cancelled by EVERY teardown ──

test("the fetch and the body read ride one AbortController, registered module-level like mediaUrlLive", () => {
  assert.match(VIEW, /let urlAbort: AbortController \| null = null;\s*\nfunction dropUrlRead\(\): void \{\s*\n\s*if \(urlAbort\) \{\s*\n\s*try \{ urlAbort\.abort\(\); \} catch \{[^}]*\}\s*\n\s*urlAbort = null;/);
  // registered right after the old viewer is dropped, BEFORE any element is built
  assert.match(URL_FN, /const ctrl = new AbortController\(\);\s*\n\s*urlAbort = ctrl;/);
  assert.ok(URL_FN.indexOf("urlAbort = ctrl;") < URL_FN.indexOf('const wrap = el("div");'));
  assert.match(URL_FN, /signal: ctrl\.signal \}\)/, "the fetch is on the signal");
  assert.match(URL_FN, /readTextCapped\(r\.body!, URL_TEXT_MAX_BYTES, ctrl\.signal\)/, "…and so is the streaming read");
  // only the TEARDOWN's abort is silent — keyed on this open's signal, never on an error's name: an
  // independently errored stream that merely wears the AbortError name still paints its failure
  assert.match(URL_FN, /\.catch\(\(err\) => \{[\s\S]*?if \(ctrl\.signal\.aborted\) return;\s*\n\s*fail\("this document could not be loaded from this page — "/);
  assert.doesNotMatch(URL_FN, /name === "AbortError"/);
  // this read's registration is released when it is over — never a LATER open's
  assert.match(URL_FN, /\.finally\(\(\) => \{\s*\n\s*if \(urlAbort === ctrl\) urlAbort = null;/);
});

test("closeFileView and BOTH replace paths call dropUrlRead — a stale read never keeps pulling for a gone modal", () => {
  assert.match(CLOSE_FN, /dropMediaUrl\(\);[^\n]*\n\s*dropUrlRead\(\);/, "close: right beside the media-URL revoke");
  assert.match(OPEN_FN, /dropMediaUrl\(\);[^\n]*\n\s*dropUrlRead\(\);[^\n]*\n\s*document\.getElementById\("romp-fileview"\)\?\.remove\(\);/,
    "the local viewer's replace path: before the old viewer is torn down");
  assert.match(URL_FN, /dropMediaUrl\(\);\s*\n\s*dropUrlRead\(\);[^\n]*\n\s*document\.getElementById\("romp-fileview"\)\?\.remove\(\);/,
    "the URL viewer's own replace path too");
  assert.equal((VIEW.match(/dropUrlRead\(\);/g) || []).length, 3, "exactly the three exits");
});

// ── 4. loud failures, and the 2 MB cap mirrored from the kernel — streamed, not buffered ──

test("a non-OK status shows `HTTP <status> from <host>` in the pane, plus the link-out — never console-only", () => {
  assert.match(URL_FN, /if \(v\.kind === "http"\) \{ fail\("HTTP " \+ v\.status \+ " from " \+ hostWord\(loc\)\); return; \}/);
  const failFn = (URL_FN.split("const fail = ")[1] || "").split("\n  };")[0];
  assert.ok(failFn, "the failure pane builder exists");
  assert.match(failFn, /el\("div", "fileview-err"\)/);
  assert.match(failFn, /why\.textContent = words;/);
  assert.match(failFn, /el\("div", "fileview-err-hint"\)[\s\S]*?hint\.textContent = href;/, "the URL under the words");
  assert.match(failFn, /why\.appendChild\(linkOut\(\)\);/, "the way out: the URL in a new tab");
  assert.match(failFn, /body\.replaceChildren\(why\);/);
  assert.match(failFn, /if \(!wrap\.isConnected\) return;/, "a failure landing after a close paints nothing");
  assert.doesNotMatch(URL_FN, /console\.(log|warn|error)/);
});

test("a thrown fetch (network) says the document could not be loaded from this page, with the link-out", () => {
  assert.match(URL_FN, /fail\("this document could not be loaded from this page — " \+ String\(err && \(err as Error\)\.message \|\| err\)\);/);
});

test("the cap is the kernel's 2 MB: a declared Content-Length refuses first, then the body is STREAMED and counted in bytes", () => {
  assert.match(VIEW, /const URL_TEXT_MAX_BYTES = 2 \* 1024 \* 1024;/);
  assert.match(KERNEL, /^_TEXT_MAX_BYTES = 2 \* 1024 \* 1024/m, "the kernel's cap the viewer mirrors");
  assert.match(VIEW, /import \{ readTextCapped, overCapWords, settleUrlResponse \} from "\.\/capped-read";/);
  // the pre-read decision is the executed helper's (capped-read.test.ts), fed this open's abort as `stop`
  assert.match(URL_FN, /const v = settleUrlResponse\(r, URL_TEXT_MAX_BYTES, \(\) => ctrl\.abort\(\)\);/);
  assert.match(URL_FN, /if \(v\.kind === "declared-too-large"\) \{ fail\(overCapWords\(v\.bytes, URL_TEXT_MAX_BYTES\)\); return; \}/,
    "a known size is named against the limit");
  assert.match(URL_FN, /const got = await readTextCapped\(r\.body!, URL_TEXT_MAX_BYTES, ctrl\.signal\);/);
  assert.match(URL_FN, /if \("tooLarge" in got\) \{ ctrl\.abort\(\); fail\(overCapWords\(null, URL_TEXT_MAX_BYTES\)\); return; \}/,
    "the streaming refusal names no measured size");
  assert.match(URL_FN, /text = got\.text;/);
  // the buffering read is GONE: no r.text(), no code-unit .length check — and no header is read here
  // at all (the helper owns Content-Length; Content-Type is never sniffed)
  assert.doesNotMatch(URL_FN, /r\.text\(\)|t\.length|\.length > URL_TEXT_MAX_BYTES/);
  assert.doesNotMatch(URL_FN, /headers\.get\(/);
  // ordering: status → declared length → no-body → streamed read → refusal; the body is never pulled
  // past a refusal
  const status = URL_FN.indexOf('if (v.kind === "http")');
  const declared = URL_FN.indexOf('if (v.kind === "declared-too-large")');
  const noBody = URL_FN.indexOf('if (v.kind === "no-body")');
  const readBody = URL_FN.indexOf("await readTextCapped(");
  const tripped = URL_FN.indexOf('if ("tooLarge" in got)');
  assert.ok(status > -1 && status < declared && declared < noBody && noBody < readBody && readBody < tripped,
    "status, header cap, no-body, streamed read, streamed cap");
  // a response without a body stream fails loudly rather than pretending
  assert.match(URL_FN, /if \(v\.kind === "no-body"\) \{ fail\("this document could not be loaded from this page — the response carried no body"\); return; \}/);
});

test("EVERY exit that stops short of consuming the body aborts this open's controller — a refused response's transfer stops", () => {
  // the hole (measured live): a 404 / oversized-length refusal painted its words and returned, .finally
  // let go of the controller, and the body kept downloading for a modal already closed
  const thenBody = URL_FN.split(".then(async (r) => {")[1].split(".catch(")[0];
  assert.match(thenBody, /^\s*(\/\/[^\n]*\n\s*)*if \(!wrap\.isConnected\) \{ ctrl\.abort\(\); return; \}/, "closed before the response landed: abort");
  assert.match(thenBody, /settleUrlResponse\(r, URL_TEXT_MAX_BYTES, \(\) => ctrl\.abort\(\)\)/, "http / declared-too-large / no-body: the helper fires the abort");
  assert.match(thenBody, /await readTextCapped\(r\.body!, URL_TEXT_MAX_BYTES, ctrl\.signal\);[^\n]*\n\s*if \(!wrap\.isConnected\) \{ ctrl\.abort\(\); return; \}/, "closed while the body streamed: abort");
  assert.match(thenBody, /if \("tooLarge" in got\) \{ ctrl\.abort\(\);/, "the streaming trip: abort (the reader already cancelled its source; the signal makes it symmetric)");
  // three literal aborts in the closure + the one the helper fires = every early return; the success path has none
  assert.equal((thenBody.match(/ctrl\.abort\(\)/g) || []).length, 4);
  const success = thenBody.slice(thenBody.indexOf("text = got.text;"));
  assert.doesNotMatch(success, /ctrl\.abort/, "a consumed body has nothing left to stop");
  // every early `return` in the closure is preceded on its line by an abort or by a verdict branch
  // (whose abort the helper already fired)
  for (const line of thenBody.split("\n").filter((l) => /return;/.test(l))) {
    assert.ok(/ctrl\.abort\(\)|v\.kind ===/.test(line), "an early exit without an abort: " + line.trim());
  }
});

// ── 5. relative references inside the rendered document ──

test("mdBlock takes the document's location and rewrites relative img/src and a/href AFTER the sanitizer", () => {
  assert.match(VIEW, /type MdDocLoc = \{ kind: "url"; href: string \} \| \{ kind: "file"; path: string; sid: string \| null \};/);
  assert.match(VIEW, /function mdBlock\(text: string, doc\?: MdDocLoc\): HTMLElement \{/);
  const sanitize = MD_FN.indexOf("sanitizeMd(");
  const rewrite = MD_FN.indexOf("resolveDocRelative(");
  assert.ok(sanitize > -1 && rewrite > sanitize, "sanitise first; the rewrite only ever sees what the sanitizer kept");
  // the ATTRIBUTE, never the property — .src/.href are already resolved against the page (the wrong base)
  assert.match(MD_FN, /const src = img\.getAttribute\("src"\) \|\| "";/);
  assert.match(MD_FN, /const href = a\.getAttribute\("href"\) \|\| "";/);
  assert.doesNotMatch(MD_FN, /img\.src\b|a\.href\b/, "no property reads");
  // URL mode: both resolve against the document URL through the executed helper
  assert.match(MD_FN, /const abs = resolveDocRelative\(src, doc\.href\);\s*\n\s*if \(abs !== src\) img\.setAttribute\("src", abs\);/);
  assert.match(MD_FN, /a\.setAttribute\("href", resolveDocRelative\(href, doc\.href\)\);/);
  // in-document and already-absolute anchors are left alone by the resolver
  assert.match(MD_FN, /if \(!href \|\| href\.startsWith\("#"\) \|\| \/\^\[a-z\]\[a-z0-9\+\.-\]\*:\/i\.test\(href\)\) return;/);
  // the helpers arrive from the pure module
  assert.match(VIEW, /import \{ resolveDocRelative, joinDocPath, urlTitleParts, headingSlug, uniqueSlugs \} from "\.\/md-links";/);
});

test("local file mode: a relative image is the sibling over the kernel's /file route (fileUrl, never hand-built)", () => {
  assert.match(MD_FN, /img\.setAttribute\("src", fileUrl\(joinDocPath\(doc\.path, src\), doc\.sid\)\);/);
  // gated to path-shaped refs: a scheme (http:, data:) or a protocol-relative URL is the browser's
  assert.match(MD_FN, /else if \(src && !\/\^\[a-z\]\[a-z0-9\+\.-\]\*:\/i\.test\(src\) && !src\.startsWith\("\/\/"\)\) \{/);
  assert.doesNotMatch(MD_FN, /"\/file\?path="|\/remote\//, "the route is fileUrl's to build (federation-aware)");
  assert.match(VIEW, /import \{ fileUrl \} from "\.\/preview";/);
  // openFileView hands mdBlock its location
  assert.match(OPEN_FN, /mdBlock\(text, \{ kind: "file", path, sid: sid \|\| null \}\)/);
});

test("local file mode: a relative link opens the sibling in the viewer via ONE listener on the body (the module sorts the anchors)", () => {
  // the file kind's anchors are sorted by file-view-links.ts (linkMarkdownAnchors) AFTER the sanitize: a sibling target becomes a path
  // link on the anchor itself (data-path the resolved path, data-frag its own #fragment, the href off so the page never follows it),
  // a section link is the viewer's scroll, a stripped target is a dead link that says why (file-view-links.test.ts executes each)
  assert.match(MD_FN, /if \(doc && doc\.kind === "file"\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(rendered\) linkMarkdownAnchors\(box, doc\.path\);/, "the module sorts a file's anchors");
  assert.ok(MD_FN.indexOf("sanitizeMd(") < MD_FN.indexOf("linkMarkdownAnchors(box, doc.path)"), "after the sanitize, so a document's own data-* never reaches the page");
  assert.doesNotMatch(MD_FN, /"fv-open"/, "no stamp of its own: the shared path-link shape (path-links.ts markPathLink) on the anchor");
  // …the listener: ONE on the body, installed once per open (stable across the Rendered ⇄ Raw swaps that rebuild its children),
  // preventDefault, then openFileView with this sid, the link's line and its fragment
  assert.match(VIEW, /import \{ delegate \} from "\.\/actions";/, "the URL viewer keeps its delegate for fv-anchor");
  assert.match(OPEN_FN, /openFileView\(p, sid \|\| null, \{ line: ln > 0 \? ln : null, frag: x\.dataset\.frag \|\| null \}\);/,
    "the sibling opens in this viewer, for this sid, landing on its line or its fragment");
  assert.equal((OPEN_FN.match(/body\.addEventListener\("click"/g) || []).length, 1, "one listener per open, never in a render path");
  assert.ok(OPEN_FN.indexOf('body.addEventListener("click"') < OPEN_FN.indexOf("const renderBody ="), "installed before any render can run");
  assert.doesNotMatch(OPEN_FN, /delegate\(body/, "the local viewer's delegate is gone: its one listener sorts every link kind");
  // the chat's document-level delegate ignores scheme-less hrefs, so the click reaches the body listener
  assert.match(HANDLER, /if \(!\/\^\[a-z\]\[a-z0-9\+\.-\]\*:\/i\.test\(href\)\) return;/);
});

// ── 6. in-document fragments: heading ids, and `#links` that land instead of spawning a tab ──

test("every heading gets id=md-<slug> after sanitisation, in both modes (the md- prefix keeps the page's own ids and CSS out of it)", () => {
  assert.match(MD_FN, /const heads = Array\.from\(box\.querySelectorAll\("h1, h2, h3, h4, h5, h6"\)\) as HTMLElement\[\];\s*\n\s*const slugs = uniqueSlugs\(heads\.map\(\(h\) => headingSlug\(h\.textContent \|\| ""\)\)\);\s*\n\s*heads\.forEach\(\(h, i\) => \{ h\.id = "md-" \+ slugs\[i\]; \}\);/);
  const sanitize = MD_FN.indexOf("sanitizeMd(");
  const ids = MD_FN.indexOf('h.id = "md-"');
  const docGate = MD_FN.indexOf("if (doc) {");
  assert.ok(sanitize > 0 && sanitize < ids && ids < docGate, "after the sanitize, and OUTSIDE the doc gate: every mode, every caller");
  assert.match(MD_FN, /an unprefixed id="tabs" would dress a heading in the chat page's[\s\S]*?#tabs CSS and shadow getElementById\("tabs"\)/, "the prefix's reason is written down");
});

test("a `#fragment` anchor is stamped fv-anchor and gets NO _blank; every other anchor still does", () => {
  assert.match(MD_FN, /if \(\(a\.getAttribute\("href"\) \|\| ""\)\.startsWith\("#"\)\) \{ a\.dataset\.act = "fv-anchor"; return; \}\s*\n\s*a\.target = "_blank";\s*\n\s*a\.rel = "noopener";/);
  // the fragment branch is in the UNCONDITIONAL loop — a document with no location still lands its own links
  const finalLoop = MD_FN.slice(MD_FN.lastIndexOf('box.querySelectorAll("a[href]")'));
  assert.ok(finalLoop.includes('a.dataset.act = "fv-anchor"'), "stamped in the final, doc-independent pass");
  assert.ok(MD_FN.indexOf("} else {\n") > 0 && MD_FN.indexOf('box.querySelectorAll("a[href]")', MD_FN.indexOf('if (doc && doc.kind === "file") {')) > MD_FN.indexOf("} else {", MD_FN.indexOf('if (doc && doc.kind === "file") {')),
    "…and never runs over a file's anchors, which the module sorted in the other arm");
});

test("scrollToFragment: decode, slug, find md-<slug> inside THIS box, scrollIntoView; nothing found → inert", () => {
  const fn = VIEW.split("function scrollToFragment(")[1].split("\n}")[0];
  assert.match(fn, /let frag = fragment\.replace\(\/\^#\/, ""\);/);
  assert.match(fn, /try \{ frag = decodeURIComponent\(frag\); \} catch \{/);
  assert.match(fn, /if \(!frag\) return false;/);
  assert.match(fn, /const target = fragmentTarget\(box\.querySelector\("\.fileview-md"\) \|\| box, frag\);/, "the rendered document inside the box, never document.getElementById: an id, an <a name>, or the heading whose slug it is (file-view-links.ts fragmentTarget)");
  assert.match(fn, /if \(!target\) return false;/);
  assert.match(fn, /target\.scrollIntoView\(\{ block: "start" \}\);/);
  assert.doesNotMatch(fn, /location\.|document\.getElementById|window\.open/);
});

test("both viewers land a section link on the body: the URL viewer through its fv-anchor delegate, the local one through its link listener", () => {
  const H = /"fv-anchor": \(a, ev\) => \{ ev\.preventDefault\(\); scrollToFragment\(body, a\.getAttribute\("href"\) \|\| ""\); \},/;
  assert.match(OPEN_FN, /if \(x\.classList\.contains\(FRAG_LINK_CLASS\)\) \{[^\n]*\n\s*ev\.preventDefault\(\);\n(?:\s*\/\/[^\n]*\n)*\s*scrollToFragment\(body, x\.getAttribute\("href"\) \|\| ""\);/, "the local viewer's listener: a section link (file-view-links.ts FRAG_LINK_CLASS) is this document's scroll");
  assert.match(URL_FN, H, "the URL viewer installs its own delegate for it");
  assert.equal((URL_FN.match(/delegate\(body/g) || []).length, 1, "one listener per open");
  assert.ok(URL_FN.indexOf("delegate(body") < URL_FN.indexOf("const renderBody ="), "installed before any render can run");
  assert.doesNotMatch(URL_FN, /"fv-open"/, "no sibling-path links in URL mode — those are absolute and the chat's delegate routes them");
});

test("the opened URL's own #fragment lands after the FIRST rendered paint — once, and only with a rendered body", () => {
  assert.match(URL_FN, /let landed = false;\s*\n\s*const landFragment = \(\) => \{\s*\n\s*if \(landed\) return;\s*\n\s*let hash = "";/,
    "the landing is one-shot, but only SPENT by a rendered paint (a Raw view waits for the toggle)");
  assert.match(URL_FN, /if \(!hash\) \{ landed = true; return; \}\s*\n\s*if \(fmt\.md !== "rendered"\) return;[^\n]*\n\s*landed = true;/);
  assert.match(URL_FN, /try \{ hash = new URL\(href\)\.hash; \} catch \{/);
  assert.match(URL_FN, /landed = true;\s*\n\s*requestAnimationFrame\(\(\) => \{ if \(wrap\.isConnected\) scrollToFragment\(body, hash\); \}\);/);
  assert.match(URL_FN, /codeBlock\(text, parts\.base, true\)\);[^\n]*\n\s*landFragment\(\);/, "after the paint, inside renderBody — so a later Rendered toggle lands too");
  assert.doesNotMatch(URL_FN, /renderBody\(\);\s*\n\s*landFragment\(\);/, "no second, mode-blind landing after the bytes");
});

// ── 7. styling: no new rules — the URL viewer wears the viewer's existing chrome in BOTH sheets ──

test("every class the URL viewer uses is already declared in both sheets (nothing new to mirror)", () => {
  for (const head of ["#romp-fileview {", ".fileview {", ".fileview-bar {", ".fileview-name {", ".fileview-dir {",
    ".fileview-base {", ".fileview-acts {", ".fileview-btn {", "a.fileview-btn {", ".fileview-body {",
    ".fileview-err {", ".fileview-err-hint {", ".fileview-load {", ".fileview-md {"]) {
    assert.ok(CHAT_CSS.includes(head), head + " in styles.css");
    assert.ok(FEED_CSS.includes(head), head + " in feed.css");
  }
  assert.doesNotMatch(URL_FN, /style\.|cssText|innerHTML = '<style/, "no inline styling");
});

test("a landed heading sits a breath below the title bar: scroll-margin-top on h1–h6, byte-equal in both sheets, the bar's own 10px", () => {
  const RULE = ".fileview-md h1, .fileview-md h2, .fileview-md h3, .fileview-md h4, .fileview-md h5, .fileview-md h6 { scroll-margin-top: 10px; }";
  assert.ok(CHAT_CSS.includes(RULE), "styles.css");
  assert.ok(FEED_CSS.includes(RULE), "feed.css");
  // 10px is a spacing the viewer already wears (the bar's padding, the refusal's Download offset) — no new value
  assert.match(CHAT_CSS, /\.fileview-bar \{[^}]*padding: 7px 10px;/);
  assert.match(CHAT_CSS, /\.fileview-err-dl \{ display: block; margin-top: 10px; \}/);
  assert.doesNotMatch(RULE, /font-size|--/, "no font size, no token");
});

// ── review fold on #958 (2026-09-07): a web page is not a document, redirects stay home, data-* never rides ──

test("URL mode: a 200 labelled text/html is refused as a web page, with the way out; the read is same-origin through redirects", () => {
  assert.match(URL_FN, /if \(v\.kind === "not-document"\) \{ fail\("the server answered with a web page, not a document \(" \+ v\.type \+ "\)"\); return; \}/);
  assert.match(URL_FN, /mode: "same-origin"/, "a same-origin alias that 302s off the origin is refused, not rendered with the foreign base");
});

test("rendered markdown never carries data-* attributes into the page, in the viewer and in the chat alike", () => {
  // a document's or a message's raw HTML with data-act=\"stopRetrying\" would otherwise bubble to the
  // document-level delegate and interrupt the active session on a click
  // both go through sanitizeMd (md-sanitize.ts), whose one profile forbids data-*
  const sanitizes = (MD_FN.match(/sanitizeMd\([^)]*\)/g) || []);
  assert.equal(sanitizes.length, 1);
  assert.match(sanitizes[0], /sanitizeMd\(dirty\)/);
  const chatMd = (RENDER.split("function md(src: string, repo: string | null = prRepoFor()): string {")[1] || "").split("\nfunction ")[0];   // the signature carries the PR-link repo (pr-links.ts)
  assert.match(chatMd, /const clean = sanitizeMd\(dirty\);/);
  assert.match(SANITIZE, /export const MD_PURIFY: Config = \{[\s\S]*?ALLOW_DATA_ATTR: false,[\s\S]*?\};/, "the shared sanitizer's profile forbids data-*");
  assert.doesNotMatch(VIEW + RENDER, /ALLOW_DATA_ATTR|DOMPurify\.sanitize\(/, "neither caller spells a profile of its own");
  // the viewer's own marks are set AFTER the sanitize, so they are unaffected
  assert.ok(MD_FN.indexOf("sanitizeMd(") < MD_FN.indexOf("linkMarkdownAnchors(box, doc.path)"));
  assert.ok(MD_FN.indexOf("sanitizeMd(") < MD_FN.indexOf('a.dataset.act = "fv-anchor"'));
});

test("local file mode: a sibling link's #fragment lands after the first RENDERED paint, once", () => {
  assert.match(VIEW, /export function openFileView\(path: string, sid\?: string \| null, opts\?: \{ line\?: number \| null; frag\?: string \| null \}\): boolean \{/);
  assert.match(OPEN_FN, /let pendingFrag: string \| null = opts\?\.frag \|\| null;/);
  assert.match(OPEN_FN, /if \(rendered && pendingFrag\) \{\s*\n\s*const h = pendingFrag; pendingFrag = null;\s*\n\s*requestAnimationFrame\(\(\) => \{ if \(wrap\.isConnected\) scrollToFragment\(body, h\); \}\);/);
});
