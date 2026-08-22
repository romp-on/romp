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

test("openPath routes by HOST: the in-pane viewer modal on the web, the editor in VS Code", () => {
  assert.match(RENDER, /function openPath\(path: string, sid\?: string \| null\): void/);
  // web → the viewer opens in THIS document, framed or standalone alike — no shell relay, no fallback
  assert.match(RENDER, /openFileView\(path, sid \|\| activeId \|\| null\);/);
  // (the import also carries setCommentSink since 2026-08-14 — the review layer's way back to the composer)
  assert.match(RENDER, /import \{ openFileView, setCommentSink \} from "\.\/file-view";/);
  assert.doesNotMatch(RENDER, /romp: "viewFile"/, "the chat→shell→feed relay is gone");
  // VS Code keeps the host editor
  assert.match(RENDER, /vscodeApi\.postMessage\(sid \? \{ type: "openFile", path, id: sid \} : \{ type: "openFile", path \}\);/);
});

test("every file-link surface in the chat goes through openPath — no direct openFile posts left", () => {
  for (const call of [/openPath\(path\);/, /openPath\(open, relative \? activeId : null\);/,
                      /openPath\(p, id \|\| null\);/]) assert.match(RENDER, call);
  // the ONLY openFile postMessage left in render.ts is openPath's own fallback branch
  assert.equal((RENDER.match(/type: "openFile"/g) || []).length, 2,
               "both remaining mentions are the two arms of openPath's fallback");
});

test("the VIEWER's shell relay is gone; the BROWSER's stays — the feed pane is only juggled for it", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  // the viewer lives in the clicking document now, so the shell no longer forwards viewFile clicks
  // (the user 2026-08-15: a file view must never touch the feed) — and the viewer, being a modal over
  // whatever pane opened it, has nothing to restore and nothing to announce
  assert.doesNotMatch(KERNEL, /m\.romp==='viewFile'/);
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
  assert.match(VIEW, /if \(e\.key !== "Escape" \|\| !document\.getElementById\("romp-fileview"\)\) return;/);
  // the panels treatment on the CHAT sheet: dimmed rgba(0,0,0,0.55) backdrop, the content behind visible
  assert.match(CHAT_CSS, /#romp-fileview \{ position: fixed; inset: 0; z-index: 1200; background: rgba\(0, 0, 0, 0\.55\);/);
  assert.match(CHAT_CSS, /\.fileview \{ width: 95%; height: 95%;/);
  // …and mirrored on the FEED sheet, which still hosts the viewer when the file BROWSER opens a file
  // (one treatment, two sheets — the hljs-palette precedent below)
  assert.match(FEED_CSS, /#romp-fileview \{ position: fixed; inset: 0;/);
  assert.match(FEED_CSS, /\.fileview \{ width: 95%; height: 95%;/);
  assert.match(FEED, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\)\);/,
    "the feed boots the listener with the WS poster (saves ride it — the raw-mode slice)");
});

test("the FEED-hosted viewer registers no comment sink, so the review layer gates itself off", () => {
  // the review layer's Submit drafts into the CHAT composer via a sink render.ts registers; the feed
  // hosts the same viewer without one, so every comment affordance must gate on the sink or Submit is
  // a dead button in that document (2026-08-19) — no real target, no affordance
  assert.doesNotMatch(FEED, /setCommentSink/, "no composer in the feed to draft into");
  assert.match(RENDER, /setCommentSink\(\(sid, text\) => \{/, "the chat bundle keeps the full behavior");
  assert.match(VIEW, /if \(!commentSink\) return;/, "the gate exists in the viewer itself");
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
  };
  const langFor = (p: string): string | null => LANG[p.slice(p.lastIndexOf(".") + 1).toLowerCase()] || null;
  assert.equal(langFor("kernel/kernel.py"), "python");
  assert.equal(langFor("ui/webview/render.TS"), "typescript");   // case-insensitive
  assert.equal(langFor("notes.md"), "markdown");
  for (const p of ["server.log", "Makefile", "a.conf", "data.csv", "x.rs"]) {
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
  const rules = [
    /\.hljs \{ color: #d8c6a8; background: transparent; \}/,
    /\.hljs-keyword, \.hljs-built_in, \.hljs-literal, \.hljs-type \{ color: #c98a6a; \}/,
    /\.hljs-string, \.hljs-attr, \.hljs-regexp \{ color: #9fb878; \}/,
    /\.hljs-number \{ color: #d4a36a; \}/,
    /\.hljs-comment, \.hljs-quote \{ color: #6f6a5f; font-style: italic; \}/,
    /\.hljs-title, \.hljs-title\.function_, \.hljs-section \{ color: #e1c08d; \}/,
    /\.hljs-name, \.hljs-tag \{ color: #c98a6a; \}/,
    /\.hljs-params, \.hljs-variable, \.hljs-property \{ color: #d8c6a8; \}/,
    /\.hljs-meta \{ color: #9a8f7a; \}/,
    /\.hljs-attribute \{ color: #cdaf7e; \}/,
    /\.hljs-addition \{ color: #9fb878; \}/,
    /\.hljs-deletion \{ color: var\(--err\); \}/,
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
  type Fmt = { md: "rendered" | "raw"; wrap: boolean };
  const parseFmt = (raw: string | null): Fmt => {
    const def: Fmt = { md: "rendered", wrap: false };
    if (!raw) return def;
    try {
      const o = JSON.parse(raw) as { md?: unknown; wrap?: unknown };
      if (!o || typeof o !== "object") return def;
      return { md: o.md === "raw" ? "raw" : "rendered", wrap: o.wrap === true };
    } catch { return def; }
  };
  assert.deepEqual(parseFmt(null), { md: "rendered", wrap: false }, "first open: rendered, unwrapped");
  assert.deepEqual(parseFmt('{"md":"raw","wrap":true}'), { md: "raw", wrap: true }, "the round-trip");
  assert.deepEqual(parseFmt("not json"), { md: "rendered", wrap: false });
  assert.deepEqual(parseFmt('{"md":"purple","wrap":"yes"}'), { md: "rendered", wrap: false },
                   "foreign values fall to the defaults field by field");
  // replica ↔ source
  assert.match(VIEW, /const def: FileViewFmt = \{ md: "rendered", wrap: false \};/);
  assert.match(VIEW, /return \{ md: o\.md === "raw" \? "raw" : "rendered", wrap: o\.wrap === true \};/);
  // …and the prefs persist in localStorage, the feed-view-state call: per-BROWSER view state that must
  // survive a kernel restart without a round-trip to the thing that just restarted
  assert.match(VIEW, /const FMT_KEY = "romp:fileviewFmt";/);
  assert.match(VIEW, /localStorage\.getItem\(FMT_KEY\)/);
  assert.match(VIEW, /localStorage\.setItem\(FMT_KEY, JSON\.stringify\(f\)\)/);
});

// B: the toggle itself — markdown only, and the rendered path is sanitized. These are arbitrary bytes
// off a disk and marked emits raw HTML verbatim, so DOMPurify sits between it and .innerHTML with the
// same profile the chat's md() uses (render.ts).
test("Raw ⇄ Rendered exists for markdown ONLY, and nothing reaches innerHTML unsanitized", () => {
  assert.match(VIEW, /const isMd = langFor\(path\) === "markdown";/);
  // the two buttons are built inside the isMd gate — a .py file shows no Rendered/Raw toggle
  assert.match(VIEW, /if \(isMd\) \{\s*\n\s*for \(const mode of \["rendered", "raw"\] as const\)/);
  assert.match(VIEW, /const rendered = isMd && fmt\.md === "rendered";/, "non-md never renders as prose");
  assert.match(VIEW, /import DOMPurify from "dompurify";/);
  // html + svg, in lockstep with the chat's md(): KaTeX draws stretchy glyphs as inline <svg>
  assert.match(VIEW, /box\.innerHTML = DOMPurify\.sanitize\(dirty, \{ USE_PROFILES: \{ html: true, svg: true \}, ADD_DATA_URI_TAGS: \["img"\] \}\);/);
  // a README's links open a NEW tab rather than navigating the hosting pane's document away
  assert.match(VIEW, /target = "_blank"/);
  assert.match(VIEW, /rel = "noopener"/);
  // fenced blocks highlight only a NAMED, registered language — same no-guessing rule as langFor
  assert.match(VIEW, /if \(!lang \|\| !hljs\.getLanguage\(lang\)\) return;/);
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
test("the Wrap toggle persists, hides with rendered prose, and its numbers still never copy", () => {
  assert.match(VIEW, /wrapBtn\.addEventListener\("click", \(\) => \{ fmt\.wrap = !fmt\.wrap; saveFmt\(fmt\); renderBody\(\); \}\);/);
  // wrap mode returns BEFORE the sibling gutter is built — a misaligned column cannot exist
  assert.match(VIEW, /if \(wrapLines\) \{[\s\S]*?return wrap;\s*\}\s*const gutter = el\("div", "fileview-gutter"\);/);
  // plain files wrap too: no grammar → the text is HTML-escaped before the line walk
  assert.match(VIEW, /code\.innerHTML = wrapNumberedHtml\(hl !== null \? hl : escapeHtml\(text\)\);/);
  for (const SHEET of [FEED_CSS, CHAT_CSS]) {
    assert.match(SHEET, /\.fileview-pre\.fileview-wrap \{ white-space: pre-wrap/);
    assert.match(SHEET, /\.fileview-wrap \.fv-cl::before \{[\s\S]*?counter-increment: fvln/);
    assert.match(SHEET, /\.fileview-wrap \.fv-cl::before \{[\s\S]*?user-select: none/);
  }
  // wrap governs the pre view only — rendered prose always wraps — so the button leaves with it
  // (and with edit mode, whose textarea has no wrap toggle to govern — the raw-mode slice)
  assert.match(VIEW, /wrapBtn\.hidden = rendered \|\| editing;/);
  assert.match(VIEW, /wrapBtn\.classList\.toggle\("on", fmt\.wrap\);/, "pressed state flips synchronously");
});

// ── download (the user 2026-08-09): any linked file can be SAVED, including everything the pane cannot
// show — the kernel's ?download=1 serves anything on disk (the rationale lives with _file_download in
// kernel.py: the view allowlists are a rendering choice, not a security boundary). ──

test("the title bar offers Download next to Copy path, at the same-origin download URL", () => {
  // the URL is fileUrl + the download switch: same origin, cookie-authed, and federation-aware for
  // free — fileUrl already routes a remote session's file through the /remote/<host>/file relay
  assert.match(VIEW, /const dlUrl = fileUrl\(path, sid\) \+ "&download=1";/);
  assert.match(VIEW, /dl\.textContent = "Download";/);
  // next to Copy path: appended into the same acts bar, wearing the same button class
  assert.match(VIEW, /acts\.appendChild\(dl\);\n\n  const copy = el\("button", "fileview-btn"\)/);
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
