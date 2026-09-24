// Click-to-cite (the user 2026-07-01): clicking a feed card's summary or a sub-goal into the chat seeds a
// dismissible "citation" chip in the composer. Sending WITH the chip routes as a follow-up (askFollowUp) so
// the goal's context rides along and the goal reopens (done→working, unless cleared); the chip is dismissible
// by its ✕ or by Backspace at the very start of the box ("like a character"). No jsdom for this renderer, so
// pin the wiring at source (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const STAGED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "staged-messages.ts"), "utf8");   // quoteReplyBody lives here
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKELETON = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");
const FILEVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "file-view.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const FEEDCSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the composer has a chip strip above the textarea", () => {
  assert.match(SKELETON, /<div id="composer-chips" style="display:none"><\/div><textarea id="composer-input"/);
  // flex: 1 1 100% claims a full-width row of its own above the Signal-style compose row (2026-07-30)
  assert.match(CSS, /#composer-chips \{ flex: 1 1 100%; display: flex/);
  assert.match(CSS, /\.composer-chip \{/);
});

test("a focus message carrying a cite seeds the composer citation", () => {
  // the kernel attaches cite:{itemId,title} to the chat focus when a card click resolves to a live goal
  assert.match(RENDER, /if \(m\.cite && typeof m\.cite\.itemId === "string" && typeof m\.cite\.title === "string"\) setCitation\(m\.id, \{ itemId: m\.cite\.itemId, title: m\.cite\.title \}\);/);
  assert.match(RENDER, /const composerCitations = new Map<string, Citation\[\]>\(\);/);   // a LIST — quote chips stack (the user 2026-08-04)
  assert.match(RENDER, /function setCitation\(id: string, cite: Citation\): void/);
});

test("the chip renders a pill with the cited title + a dismiss ✕", () => {
  assert.match(RENDER, /function renderComposerChips\(id: string \| null\): void/);
  assert.match(RENDER, /el\("div", "composer-chip"\)/);
  assert.match(RENDER, /el\("span", "composer-chip-label"\); label\.textContent = cite\.title;/);
  assert.match(RENDER, /el\("button", "composer-chip-x"\)/);
  // ✕ dismisses but stops the click from also opening the audit preview
  assert.match(RENDER, /x\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); if \(id\) removeCitation\(id, i\); \}\);/);   // each chip dismisses ITSELF
});

test("clicking the chip opens an audit preview of the exact prompt from /followup-preview (the user 2026-07-01)", () => {
  assert.match(RENDER, /chip\.addEventListener\("click", \(\) => \{ if \(id\) openCitePreview\(id, chip\); \}\);/);
  assert.match(RENDER, /function openCitePreview\(id: string, anchor: HTMLElement\): void/);
  // fetches the REAL wrapped body (kernel _followup_body) with the current draft substituted, escaped as text
  assert.match(RENDER, /"\/followup-preview\?itemId=" \+ encodeURIComponent\(goalId\) \+ "&text=" \+ encodeURIComponent\(draft\)/);
  assert.match(RENDER, /el\("pre", "cite-preview-body"\)/);
  assert.match(RENDER, /body\.textContent = \(d && typeof d\.body === "string" && d\.body\)/);
  // Esc / outside-click / re-render close it
  assert.match(RENDER, /function closeCitePreview\(\): void/);
  assert.match(RENDER, /if \(e\.key === "Escape"\) \{ e\.preventDefault\(\); closeCitePreview\(\); \}/);
});

test("Backspace at the start of the box deletes the citation like a character", () => {
  assert.match(RENDER, /e\.key === "Backspace" && !e\.metaKey && !e\.ctrlKey && ta\.selectionStart === 0 && ta\.selectionEnd === 0\s*\n\s*&& activeId && composerCitations\.has\(activeId\)/);
  assert.match(RENDER, /removeCitation\(activeId\);/);
});

test("sending with a GOAL citation routes as an askFollowUp (reopen) and consumes the chip", () => {
  // the three routing branches live in routeUserMessage since the staged flush (2026-08-15) — ONE
  // owner for the live send and the staged release; deliver hands the typed message to flushStaged, which
  // routes every post of the release through it with sid (the active session) as the sid
  assert.match(RENDER, /const cites = composerCitations\.get\(activeId\);/);
  assert.match(RENDER, /flushStaged\(sid, \{ text, cites, imgPaths: attached\.filter\(\(p\) => previewKind\(p\) === "img"\), paths: attached \}\);/);   // + the echo's thumbnail paths (2026-08-25)
  assert.match(RENDER, /if \(goalCite\?\.itemId\) \{ vscodeApi\.postMessage\(\{ type: "askFollowUp", itemId: goalCite\.itemId, text, sid, qid, \.\.\.att \}\); registerOptimistic\(sid, text, imgPaths, qid, paths\); \}/);
  // the plain branch registers for every send, /clear included, passing the boundary-end flag (true for a non-Codex /clear)
  assert.match(RENDER, /else \{ vscodeApi\.postMessage\(\{ type: "sendMessage", id: sid, text, qid, \.\.\.att \}\); registerOptimistic\(sid, text, imgPaths, qid, paths, isClearCmd\(text\) && liveSession\(sid\)\?\.status\?\.backend !== "codex"\); \}/);
  assert.match(RENDER, /if \(cites\) \{ composerCitations\.delete\(activeId\); renderComposerChips\(activeId\); \}/);
});

test("a citation follow-up carries its SID, so a reply to a REMOTE card reaches that card's kernel", () => {
  // The user 2026-07-29: replies typed into a remote session's chat vanished on Enter — box cleared, no
  // provisional bubble, nothing on the far side. federation.routeOutbound picks the owning kernel from
  // `id`/`sid` ONLY, and an itemId can never join that list: it is "‹sid›:‹goalId›", so hostOf() would read
  // the session uuid as a host name. With no sid the message routed to the LOCAL kernel, which derives the
  // sid from the itemId, owns no such session, and hands it to tmux by uuid — dropped in silence. The card
  // still flashed to Working (the kernel's cardPredict fires before any of that) and snapped back on the
  // ok:false ack, so the only visible trace was a bounce.
  assert.match(RENDER, /if \(goalCite\?\.itemId\) \{ vscodeApi\.postMessage\(\{ type: "askFollowUp", itemId: goalCite\.itemId, text, sid, qid, \.\.\.att \}\); registerOptimistic\(sid, text, imgPaths, qid, paths\); \}/);
  assert.match(RENDER, /const sid = activeId;   \/\/ the session this send \(and any confirm below\) was armed for/);   // deliver's sid IS the active session
  assert.match(RENDER, /flushStaged\(sid, \{ text, cites, imgPaths: attached\.filter\(\(p\) => previewKind\(p\) === "img"\), paths: attached \}\);/);
  // every OTHER card-addressed op already routes this way — the citation follow-up was the lone omission
  assert.match(FEED, /type: "askClear", itemId: it\.itemId, sid: it\.sid/);
  assert.match(FEED, /type: "askFollowUp", itemId: tgt \? tgt\.itemId : fbId, title: tgt \? tgt\.title : fbTitle, text: txt, sid: fbSid/);
});

test("a citation survives a RELOAD but is dropped on tab SWITCH (the user 2026-07-01)", () => {
  // persisted so a mid-reply reload keeps the chip
  assert.match(RENDER, /citations: Object\.fromEntries\(composerCitations\)/);
  assert.match(RENDER, /const savedCites = \(\(vscodeApi\?\.getState\?\.\(\) \|\| \{\}\) as any\)\.citations;/);
  assert.match(RENDER, /renderComposerChips\(activeId\);\s*\/\/ a citation persisted across the reload/);
  // but switching AWAY from a tab abandons its chip (a "reply right now" intent)
  assert.match(RENDER, /if \(ta\.value\) drafts\.set\(activeId, ta\.value\); else drafts\.delete\(activeId\);\s*\n[\s\S]*?composerCitations\.delete\(activeId\);/);
});

test("clearing a card drops any composer chip pointing INTO it (the user 2026-07-01)", () => {
  // the kernel pushes dropCitation{itemId, itemIds: the card's whole subtree} on a single clear — a chip
  // can cite a SUB-goal (wireNodeZones sends the clicked node's id) — and dropCitationsAll on Clear-all
  assert.match(RENDER, /m\.type === "dropCitation" && typeof m\.itemId === "string"\) dropCitationByItem\(m\.itemId, Array\.isArray\(m\.itemIds\)/);
  assert.match(RENDER, /m\.type === "dropCitationsAll"\) \{[\s\S]*?composerCitations\.clear\(\); persistDrafts\(\); renderComposerChips\(activeId\);/);
  // dropCitationByItem removes every chip citing the card OR any node under it
  assert.match(RENDER, /function dropCitationByItem\(itemId: string, itemIds\?: string\[\]\): void/);
  assert.match(RENDER, /const gone = new Set\(itemIds && itemIds\.length \? itemIds : \[itemId\]\);/);
  assert.match(RENDER, /const kept = list\.filter\(\(c\) => !\(c\.itemId && gone\.has\(c\.itemId\)\)\)/);   // quote chips cite no goal — a card clear never drops them
});

test("a sub-goal click cites ITSELF, not the card's top goal (the user 2026-07-01)", () => {
  // wireNodeZones posts the clicked node's own id as showOnTimeline.itemId — the kernel's _cite_for then
  // seeds the chip (title + audit preview) from THAT node, so the chip context is specific, never the
  // generic top-goal quote. The kernel uses itemId only for the citation; navigation is anchorUuid-based.
  assert.match(FEED, /const navId = node\.id \|\| it\.turnId;/);
  assert.doesNotMatch(FEED, /const navId = node\.kind === "handoff" \? node\.id : it\.turnId;/);
});

test("highlighting transcript text seeds a QUOTE chip — the same chip, reply-context flavored (the user 2026-07-13)", () => {
  // two flavors on one Citation: a goal chip (itemId) or a quote chip (quote [+ the turn's uuid])
  assert.match(RENDER, /interface Citation \{ itemId\?: string; title: string; quote\?: string; uuid\?: string \| null; src\?: string \}/);
  // event-based on selectionchange; BOTH endpoints must sit inside transcript turns, so composer/tab
  // selections never seed; a collapse never clears (clicking into the composer must not eat the chip).
  // The qualification lives in transcriptSelection(), shared with the Enter-to-reply shortcut so the
  // two can never disagree on what counts as a transcript selection.
  assert.match(RENDER, /function transcriptSelection\(\): \{ text: string; uuid: string \| null \} \| null/);
  assert.match(RENDER, /if \(!sel \|\| !sel\.rangeCount\) return null;/);
  assert.match(RENDER, /const a = turnOf\(r\.startContainer\), f = turnOf\(r\.endContainer\);/);
  assert.match(RENDER, /if \(!a \|\| !f\) return null;/);
  assert.match(RENDER, /document\.addEventListener\("selectionchange", \(\) => \{/);
  assert.match(RENDER, /if \(!q\) return;\s*\n\s*seedTranscriptQuote\(activeId, q\.text, q\.uuid\);/);   // never clears chips, never touches the gesture
  // seeding NEVER focuses the composer — a focus steal would collapse the selection mid-drag
  const seeder = RENDER.split("function seedTranscriptQuote(")[1].split("\n}")[0];
  assert.doesNotMatch(seeder, /focusComposer/);
  assert.match(RENDER, /quote: quote\.slice\(0, QUOTE_CAP\)/);   // mkQuoteCitation bounds every chip
});

test("⌘-selecting another piece of text ADDS a context below the held ones (the user 2026-08-04)", () => {
  // The deciding event is the mousedown that STARTS the selection gesture: plain → replace (the pre-stack
  // behavior), ⌘/Ctrl → add. Shift is deliberately left to the browser (it extends the live selection, and
  // the live gesture's chip follows), so a shift-mousedown neither resets the gesture nor re-reads the key.
  assert.match(RENDER, /document\.addEventListener\("mousedown", \(e\) => \{\s*\n\s*if \(e\.shiftKey\) return;[\s\S]*?quoteAddHeld = e\.metaKey \|\| e\.ctrlKey;\s*\n\s*quoteSeedIdx = null;\s*\n\}, true\);/);
  // One gesture owns one chip BY INDEX: selectionchange fires dozens of times mid-drag, so the live
  // gesture WRITES THROUGH to the chip it owns; only a new gesture appends (⌘) or replaces (plain).
  // Without the write-through a single ⌘-drag would spray a chip per selectionchange event.
  const seeder = RENDER.split("function seedTranscriptQuote(")[1].split("\n}")[0];
  assert.match(seeder, /let idx = quoteSeedIdx != null && quoteSeedIdx < list\.length \? quoteSeedIdx : null;/);
  assert.match(seeder, /if \(quoteAddHeld && list\.length\) idx = list\.length;/);
  assert.match(seeder, /else \{ list\.length = 0; idx = 0; \}/);
  // flavors never mix: any quote seed drops a goal chip (the send routes goal XOR quotes)
  assert.match(seeder, /\.filter\(\(c\) => !c\.itemId\)/);
  // chips wrap in rows and the Stage button rides the end of the LAST row — never alone on its own
  // line (the user 2026-08-25, screenshot: the old column layout wrapped Stage beneath a wide chip)
  assert.match(CSS, /#composer-chips \{ flex: 1 1 100%; display: flex; flex-flow: row wrap; align-items: center/);
  // the room beside the last chip is GUARANTEED: every chip cedes the Stage button's footprint,
  // ellipsizing sooner rather than pushing Stage to wrap; the edit pill (no Stage) keeps the full row
  assert.match(CSS, /max-width: calc\(100% - 64px\);/);
  assert.match(CSS, /\.composer-chip-edit \{ max-width: 100%; \}/);
  // Backspace-at-start eats the NEWEST chip first, one per press
  assert.match(RENDER, /list\.splice\(idx == null \? list\.length - 1 : idx, 1\);/);
  // the persisted state restores a LIST, and still accepts the pre-stack single-object form
  assert.match(RENDER, /for \(const c of \(Array\.isArray\(v\) \? v : \[v\]\) as any\[\]\)/);
});

test("discontiguous ⌘-ranges become separate chips — the seed reads the ACTIVE range, never sel.toString() (the user 2026-08-04)", () => {
  // The browser's own discontiguous selection keeps the earlier highlight live as its own range while a
  // new one is dragged; sel.toString() CONCATENATES every range, so seeding from it merged two sections
  // into one chip. The seed reads the newest range alone — the earlier ranges already own their chips
  // from their own gestures — with endpoints from the range's containers (anchor/focus describe only the
  // last-modified range, and flip on a backwards drag).
  assert.match(RENDER, /const r = sel\.getRangeAt\(sel\.rangeCount - 1\);/);
  assert.match(RENDER, /if \(r\.collapsed\) return null;/);
  assert.match(RENDER, /const text = r\.toString\(\)\.trim\(\);/);
  const fn = RENDER.split("function transcriptSelection(")[1].split("\n}")[0];
  assert.doesNotMatch(fn, /sel\.toString/, "the concatenating read is gone");
  assert.doesNotMatch(fn, /sel\.anchorNode|sel\.focusNode/, "endpoints come from the active range");
});

test("one ⌘-drag never lists the same context twice (the user 2026-08-04)", () => {
  // The reported bug: a mid-drag selectionchange tick can momentarily fail to qualify (the cursor crossing
  // the gap between two turns puts an endpoint outside any .turn). Ending the gesture on that tick made the
  // next qualifying tick APPEND AGAIN — one ⌘-drag produced two copies of the same context. Plain select
  // masked the identical flicker because its re-seed replaces. So:
  // (1) a non-qualifying tick leaves the gesture alone — ONLY a mousedown ends it…
  const listener = RENDER.split('document.addEventListener("selectionchange"')[1].split("});")[0];
  assert.doesNotMatch(listener, /quoteSeedIdx/, "the selectionchange listener never touches the gesture");
  // (2) …and identical text collapses regardless of the path that re-cited it (a repeated double-click,
  // a drag re-traced after a transcript rebuild killed the selection) — the gesture's own chip survives.
  const seeder = RENDER.split("function seedTranscriptQuote(")[1].split("\n}")[0];
  assert.match(seeder, /if \(i !== idx && list\[i\]\.quote === chip\.quote\) \{\s*\n\s*list\.splice\(i, 1\);\s*\n\s*if \(i < idx\) idx--;/);
});

test("Enter with a live transcript selection drops into the message box with the quote as context (the user 2026-08-04)", () => {
  // the window Enter handler checks the selection BEFORE the bare-area gate: the mousedown that made the
  // selection may have landed focus on a fold head or a button, which the ae===body gate below refuses —
  // and re-seeding at Enter makes the chip exactly what's selected at that moment
  assert.match(RENDER, /const q = transcriptSelection\(\);\s*\n\s*if \(q && activeId\) \{\s*\n\s*e\.preventDefault\(\);\s*\n\s*seedTranscriptQuote\(activeId, q\.text, q\.uuid\);\s*\n\s*focusComposer\(\);\s*\n\s*return;/);
  // the bare-area fallback (Enter with no selection — the user 2026-06-26) survives untouched below it
  // (T236: while the hand-over note holds the box, this default stands down — see draft-teardown.test.ts)
  assert.match(RENDER, /if \(ae && ae !== document\.body\) return;\s*\n\s*if \(composerNoteHolds\(\)\) return;[^\n]*\n\s*if \(focusComposerOrAsk\(\)\) e\.preventDefault\(\);/);
});

test("closing a session clears its composer reply context — chip, draft, and edit pill (the user 2026-08-04)", () => {
  const body = RENDER.match(/function dismissSession\(id: string, why: DismissWhy, doomed\?: ReadonlySet<string>\): void \{[\s\S]*?\n\}/);
  assert.ok(body, "dismissSession not found");
  // the maps: the draft, the citation chip, and any pending edit mode all die with the session
  assert.match(body![0], /drafts\.delete\(id\); composerCitations\.delete\(id\); composerEdits\.delete\(id\); composerFiles\.delete\(id\); persistDrafts\(\);/);
  // …and when the CLOSED session was the active one, the shared chip strip is repainted for the newly
  // selected tab. Without this the dead session's chip lingered in the strip — and its ✕, bound to the
  // dead id whose map entry is already gone, early-returned in removeCitation, so the stale chip could
  // not even be dismissed by hand. (The repaint rides loadComposerFor since T236 — chips, thumbnails,
  // staged stack and draft in one loader, shared with the only-tab adoption.)
  assert.match(body![0], /loadComposerFor\(activeId\);[\s\S]*?showActive\(\);/);
});

test("quote chips send a plain message wrapped by quoteReplyBody — never askFollowUp (no goal to reopen)", () => {
  assert.match(RENDER, /if \(goalCite\?\.itemId\) \{ vscodeApi\.postMessage\(\{ type: "askFollowUp", itemId: goalCite\.itemId, text, sid, qid, \.\.\.att \}\); registerOptimistic\(sid, text, imgPaths, qid, paths\); \}/);
  // the quote branch echoes the COMPOSED body — byte-identical to what lands, so the reconcile's
  // includes() match is exact (the user 2026-08-23, whose quoted sends painted nothing until the
  // kernel round-tripped while plain sends painted instantly)
  assert.match(RENDER, /else if \(quoteCites\.length\) \{ const body = quoteReplyBody\(quoteCites, text\); vscodeApi\.postMessage\(\{ type: "sendMessage", id: sid, text: body, qid, \.\.\.att \}\); registerOptimistic\(sid, body, imgPaths, qid, paths\); \}/);
  // the wrap: one section per stacked chip (lead-in + the highlighted text as a markdown quote block), in
  // strip order, then the typed message — a single chip composes byte-identically to the pre-stack form
  // a context-only body (staged with an empty box) carries no dangling blank tail. The function lives in
  // staged-messages.ts now (the staged release composes from it too) and its test executes it.
  assert.match(STAGED, /return quoted && text \? quoted \+ "\\n\\n" \+ text : quoted \|\| text;/);
  // the chip's audit preview shows the SAME composed body — the whole outgoing message, every stacked
  // quote, whichever chip was clicked — client-side (no /followup-preview fetch)
  assert.match(RENDER, /body\.textContent = quoteReplyBody\(cites\.filter\(\(c\) => c\.quote\), draft \|\| "\(your message\)"\);/);
  // a quote chip wears the typographic quote mark; the goal chip keeps ↩
  assert.match(RENDER, /mark\.textContent = cite\.quote \? "“" : "↩";/);
});

test("context stages ALONE, and the chips strip carries the visible Stage button AFTER the chips", () => {
  // the user 2026-08-23: nobody discovers ⌘⏎ on their own. Select a passage → the chip appears with
  // a Stage button; press it (or ⌘⏎) with an EMPTY box and just the context stages — repeat, then
  // one typed message flushes the whole run. The button lives in the chips strip, so it exists
  // exactly while context is held and vanishes with it. MOVED 2026-08-24: it now sits immediately
  // AFTER the context chip(s) it acts on — right-justified it floated detached and its meaning
  // didn't read — so the DOM appends it after the cites loop and the CSS drops the absolute pin.
  assert.match(RENDER, /if \(!typed && !\(composerCitations\.get\(activeId\) \|\| \[\]\)\.some\(\(c\) => c\.quote\)\) return;/,
    "empty box + a quote chip is stageable; empty box + nothing is not");
  assert.match(RENDER, /fireStage = \(\) => stageComposer\(\);/, "the strip's door into the composer closure");
  assert.match(RENDER, /const st = el\("button", "composer-stage-btn"\) as HTMLButtonElement;/);
  // DOM order: chips loop first, then the button — adjacency is the point of the move
  const loop = RENDER.indexOf("cites.forEach((cite, i) => {");
  const btn = RENDER.indexOf('el("button", "composer-stage-btn")');
  assert.ok(loop >= 0 && btn > loop, "the Stage button is appended AFTER the context chips");
  // neutral at rest (the user 2026-08-23): an accent outline beside the accent-blue chips read as
  // already-pressed. Rest = the button family's dress; the accent appears only on hover.
  // T141 (the user 2026-08-28): buttons are consistent — the feed word-buttons' rest exactly:
  // dark ground (transparent over the page), the feed's --card-border hairline (mirrored token)
  assert.match(CSS, /\.composer-stage-btn \{ background: transparent;[^\n]*\n\s*border: 1px solid var\(--card-border\); color: var\(--dim\);/);
  assert.match(CSS, /--card-border: rgba\(255, 255, 255, 0\.10\);/, "byte-equal mirror of feed.css --card-border");
  assert.match(CSS, /\.composer-stage-btn:hover \{ border-color: var\(--accent\); color: var\(--accent\); background: var\(--accent-wash\); \}/);   // the feed word-button hover (2026-08-25)
  assert.doesNotMatch(CSS, /\.composer-stage-btn \{ position: absolute/,
    "no absolute pin — the button flows in the strip, immediately after what it acts on");
  assert.match(RENDER, /st\.title = "hold this context \(and anything typed\) for one combined send later — ⌘⏎ does the same";/);
  assert.match(RENDER, /label\.textContent = s\.text \|\| "\(context only — sends with your message\)";/,
    "a context-only staged row says what it is");
});

test("Quote is the chip, and only the chip — the in-box blockquote form is gone", () => {
  // the user 2026-08-23, consolidating the three verbs by removal: selecting already seeds the chip,
  // so the menu item just focuses the composer. No inserter, no chip dedup, nothing to double-send.
  assert.doesNotMatch(RENDER, /quoteSelectionIntoComposer/);
  assert.match(RENDER, /mk\("Quote", \(\) => \{ \(document\.getElementById\("composer-input"\) as HTMLTextAreaElement \| null\)\?\.focus\(\); \}\);/);
});

test("a VS Code EDITOR highlight seeds the same chip, labeled + wrapped with its file:lines origin (the user 2026-07-13)", () => {
  // the extension host posts editorSelection {text, src} on onDidChangeTextEditorSelection (see
  // vscode-extension/src editor-selection pins); the webview seeds the quote chip from it
  assert.match(RENDER, /m\.type === "editorSelection" && typeof m\.text === "string" && m\.text\.trim\(\)/);
  // the FILE VIEWER posts this shape with a sid, which wins over activeId (file-view.test.ts pins that)
  assert.match(RENDER, /seedEditorQuote\(to, m\.text, typeof m\.src === "string" \? m\.src : undefined\);/);
  // the editor's highlight owns ONE chip: a cursor move updates it in place, never wiping stacked
  // transcript quotes beside it; absent, it appends below them (the user 2026-08-04)
  assert.match(RENDER, /const i = list\.findIndex\(\(c\) => !!c\.src\);\s*\n\s*if \(i >= 0\) list\[i\] = chip; else list\.push\(chip\);/);
  // the chip title leads with the origin; the wrap lead-in points at the code, not the conversation
  assert.match(RENDER, /const title = \(src \? src \+ " — " \+ snip : snip\)\.slice\(0, 140\);/);
  assert.match(STAGED, /const lead = c\.src \? "Replying to this highlighted code \(" \+ c\.src \+ "\):" : "Replying to this part of the conversation:";/);
});

test("deselecting in the editor (editorSelectionCleared) drops the editor chip, scoped + focus-safe (the user 2026-07-14)", () => {
  // the host posts editorSelectionCleared on a collapse; the webview drops the chip that highlight seeded
  assert.match(RENDER, /m\.type === "editorSelectionCleared"\) clearEditorCitation\(activeId\);/);
  assert.match(RENDER, /function clearEditorCitation\(id: string \| null\): void/);
  const fn = RENDER.split("function clearEditorCitation(")[1].split("\n}")[0];
  // ONLY the editor-seeded chip (it alone carries src) — transcript-quote and goal chips are left alone
  assert.match(fn, /const kept = list \? list\.filter\(\(c\) => !c\.src\) : \[\];/);
  assert.match(fn, /if \(!list \|\| kept\.length === list\.length\) return;/);
  // an in-progress reply keeps its quote: bail when the active composer has typed text
  assert.match(fn, /if \(id === activeId && ta && ta\.value\.trim\(\)\) return;/);
  // clears state + re-renders, but NEVER steals focus back to the composer (the user is in the editor)
  assert.match(fn, /if \(kept\.length\) composerCitations\.set\(id, kept\); else composerCitations\.delete\(id\);/);
  assert.doesNotMatch(fn, /focusComposer/);
});

test("the FILE VIEWER owns its own selection menu, so every pane that mounts it can comment", () => {
  // It lived in render.ts, which is the CHAT bundle only — the file browser opens the same viewer in
  // the FEED pane, whose bundle never loaded a line of it, so commenting a doc worked on one surface
  // and silently did nothing on the other. file-view.ts is imported by both.
  assert.match(FILEVIEW, /box\.addEventListener\("contextmenu", \(ev: MouseEvent\) => \{/);
  // the shared card: placed, dismissed on Escape, scroll or blur, and reachable from the keyboard
  assert.match(FILEVIEW, /openContextMenu\(ev\.clientX, ev\.clientY, \[\n\s*\{ label: stagesNotes\(\) \? "Stage" : "Comment", pick: \(\) => openCommentBox\(picked, ev\.clientX, ev\.clientY, marked\) \},/,
    "Stage where the pane has a composer, Comment where it opens a thread");
  assert.match(FILEVIEW, /\], \{ className: "fileview-ctx" \}\);/);
  assert.match(FILEVIEW, /closeContextMenu\(\);\s+\/\/ through the builder, so its Escape listener goes too/);
  assert.match(FILEVIEW, /if \(editing \|\| !sid\) return;/, "an edit gesture, or no session to hang a thread on");
  // routed to the sid the file was opened FOR, matching the quote chip's rule — never the active tab
  assert.match(FILEVIEW, /toHost\(\{ romp: "stageNote", sid: s, text: body, exact: picked, src, createId \}\);/);
  // the label comes from what the viewer shows and the send goes at once: a close can't strand the words
  assert.match(FILEVIEW, /const src = quoteSrcLabel\(path, viewText\(\), picked\);\n\s*if \(hasComposer\) \{\n\s*toHost\(\{ romp: "stageNote"/);
  assert.doesNotMatch(FILEVIEW, /if \(!cmtHooks\.has\(createId\)\) return;/);
  // the box paints only on the answer, never on the post
  assert.doesNotMatch(FILEVIEW, /romp: "stageNote"[\s\S]{0,400}?send\.textContent = "Saved";/);
  // the composer-less pane keeps the kernel path: a thread is the only place a note can land there
  assert.match(FILEVIEW, /post\(\{ type: "commentCreate", id: s, uuid: "", exact: picked, text: body, src, createId \}\);/);
  assert.match(FILEVIEW, /const createId = mintCreateId\(\);/, "one id per send, echoed back to settle this box");
  assert.match(FILEVIEW, /if \(ev\.button > 0 \|\| ev\.ctrlKey\) return;/, "the right-click's release seeds no quote chip");
  // the chat bundle keeps the TRANSCRIPT menu and nothing else — one owner per surface, no duplicate
  assert.doesNotMatch(RENDER, /fileViewSelection/);
  assert.match(RENDER, /if \(!content \|\| !sel \|\| !sel\.anchorNode \|\| !content\.contains\(sel\.anchorNode\) \|\| !text\.trim\(\)\) return;/);
  assert.match(RENDER, /document\.getElementById\("content"\)\?\.addEventListener\("contextmenu", showSelectionMenu\);/);
  // both sheets carry the skin: the viewer renders in the chat pane and the feed pane
  for (const sheet of [CSS, FEEDCSS]) {
    assert.match(sheet, /\.ctx-menu\.fileview-ctx \{ z-index: 1250; \}/);
    assert.match(sheet, /\.fileview-cmt \{[^}]*max-width: calc\(100vw - 8px\);[^}]*box-shadow: var\(--shadow-menu\);/, "no floorless clamp, the theme's shadow");
    assert.match(sheet, /\.fileview-cmt-mark \{\n\s*background: color-mix\(in srgb, var\(--accent/, "the mark follows the accent");
  }
});

test("each comment box settles by its own createId", () => {
  // A single slot let a second box sent before the first's ack take that ack, leaving the first on Sending.
  assert.match(FILEVIEW, /const cmtHooks = new Map<string, CmtHooks>\(\);/);
  assert.match(FILEVIEW, /m\.type === "commentCreated" && !m\.uuid && typeof m\.createId === "string" && cmtHooks\.has\(m\.createId\)\n\s*&& cmtHooks\.get\(m\.createId\)!\.sid === m\.id\) \{/,
    "an EMPTY anchor uuid marks the ack as the file viewer's, the createId as this box's");
  assert.match(FILEVIEW, /m\.type === "commentCreateFailed" && !m\.uuid && typeof m\.createId === "string" && cmtHooks\.has\(m\.createId\)/);
  assert.match(FILEVIEW, /m\.romp === "noteStaged" && typeof m\.createId === "string" && cmtHooks\.get\(m\.createId\)\?\.sid === m\.sid/);
  assert.match(RENDER, /toViewer\(\{ romp: "noteStaged", sid, createId \}\);/);
  // a host drop or a socket drop fails every box still waiting on the host, never a staged note
  assert.match(FILEVIEW, /for \(const \[id, h\] of Array\.from\(cmtHooks\)\) if \(h\.viaHost\) \{ cmtHooks\.delete\(id\); h\.failed\(why\); \}/);
  assert.match(FILEVIEW, /m\.type === "warn" && typeof m\.sid !== "string" && \(editHooks \|\| cmtHooks\.size\)/);
  // the draft survives a refusal, and a box closed mid-send reopens with it
  assert.match(FILEVIEW, /send\.disabled = false; send\.textContent = verb;/);
  assert.match(FILEVIEW, /if \(!pop\.isConnected\) \{ openCommentBox\(picked, x, y, marked, body, why, false\); return; \}/);
  // a start that fails after the ack re-marks the passage on retry, and the late reopen takes no focus
  assert.match(FILEVIEW, /const again = unmark\(mark\) \|\| marked;/);
  assert.match(FILEVIEW, /openCommentBox\(picked, x, y, again, body, reason, false\);/);
  assert.match(FILEVIEW, /if \(takeFocus\) ta\.focus\(\);/);
  assert.match(FILEVIEW, /cmtHooks\.clear\(\);\s+\/\/ a verdict landing after the close paints nothing/);
});

test("the viewer and its host talk over DOM events, not window messages", () => {
  // A window message also reached the chat's kernel-frame handler, spending a parked image's retries.
  assert.match(FILEVIEW, /window\.dispatchEvent\(new CustomEvent\(VIEWER_TO_HOST, \{ detail \}\)\);/);
  assert.match(RENDER, /window\.dispatchEvent\(new CustomEvent\(HOST_TO_VIEWER, \{ detail \}\)\);/);
  assert.match(RENDER, /window\.addEventListener\(VIEWER_TO_HOST, \(e: Event\) => \{/);
  assert.match(FILEVIEW, /window\.addEventListener\(HOST_TO_VIEWER, \(e: Event\) => \{/);
  assert.doesNotMatch(FILEVIEW, /postMessage\(\{ romp: "(stageNote|submitComposer|composerPendingAsk)"/);
  assert.doesNotMatch(RENDER, /postMessage\(\{ romp: "(noteStaged|composerPending)"/);
});

test("the viewer's Submit sends the staged notes, without leaving the modal", () => {
  assert.match(FILEVIEW, /submit\.addEventListener\("click", \(\) => \{ if \(sid\) toHost\(\{ romp: "submitComposer", sid \}\); \}\);/);
  assert.match(FILEVIEW, /if \(sid\) toHost\(\{ romp: "composerPendingAsk", sid \}\);/);
  assert.match(FILEVIEW, /submit\.hidden = n < 1;/);
  assert.match(RENDER, /if \(m\.romp === "submitComposer"\) submitComposerPending\(m\.sid\);/);
  // a loose chip is a selection, not a note: the count and the send are the staged run alone
  assert.match(RENDER, /function composerPendingCount\(sid: string \| null\): number \{\n  return sid \? stagedMsgs\.list\(sid\)\.length : 0;\n\}/);
  assert.match(RENDER, /const sent = flushStaged\(sid\);\n  notifyComposerPending\(sid\);/);
  assert.match(RENDER, /if \(hostIsDown\(sid\) \|\| isProvisionalId\(sid\)\) \{\n    warnToast\(/);
  assert.match(RENDER, /const strip = document\.getElementById\("composer-staged"\);\n  if \(!strip\) return;\n  notifyComposerPending\(id\);/);
  // staging a note drops the loose chip its selection seeded, so it never rides a later send twice
  assert.match(RENDER, /if \(cite\.quote\) dropSeededQuote\(sid, cite\.quote\);/);
});

test("the viewer's menu and comment box outrank the viewer they open over", () => {
  // Carrying .ctx-menu's 100-family z-index shipped the menu UNDERNEATH #romp-fileview (1200): both mount
  // on document.body as its siblings, so the overlay painted over them and a right-click on a selected
  // passage did nothing visible — preventDefault had already eaten the browser's menu (the user 2026-09-21).
  // Pinned as a comparison, not a literal, so a future overlay bump fails here instead of in the viewer.
  const zIndexOf = (sheet: string, selector: string): number => {
    const rule = new RegExp(`\\${selector} \\{[^}]*?z-index: (\\d+)`).exec(sheet);
    assert.ok(rule, `${selector} declares a z-index`);
    return Number(rule[1]);
  };
  for (const sheet of [CSS, FEEDCSS]) {
    const overlay = zIndexOf(sheet, "#romp-fileview");
    assert.ok(zIndexOf(sheet, ".fileview-ctx") > overlay, "the selection menu paints over the viewer");
    assert.ok(zIndexOf(sheet, ".fileview-cmt") > zIndexOf(CSS, "#warn-toasts"), "a toast never covers the box");
    assert.ok(zIndexOf(sheet, ".fileview-cmt") > overlay, "the comment box paints over the viewer");
  }
  // the warn toast too: a refusal raised by a gesture INSIDE the viewer is the case that needs reading
  assert.ok(zIndexOf(CSS, "#warn-toasts") > zIndexOf(CSS, "#romp-fileview"));
});

// ── select → TYPE → ⌘⏎ (the user 2026-09-02): typing needs no click into the box ─────────────────
test("an unclaimed printable keystroke drops the cursor into the composer — natively, never synthesized", () => {
  const mark = RENDER.indexOf("// SELECT → TYPE → ⌘⏎");
  assert.ok(mark > 0, "the type-to-focus handler exists");
  const at = RENDER.indexOf('window.addEventListener("keydown"', mark);   // the CODE, past the design comment
  assert.ok(at > mark, "the handler follows its design note");
  const end = RENDER.indexOf("ta.focus({ preventScroll: true })", at);
  assert.ok(end > at, "the redirect focuses the box without jolting the transcript");
  const block = RENDER.slice(at, end);
  // gates, in the order the hazards were mapped: upstream handlers, chords, IME, non-printables,
  // Space (ask-card toggle / scroll) — the KEY-specific ones stay in the handler…
  assert.match(block, /if \(e\.defaultPrevented \|\| e\.altKey \|\| e\.ctrlKey \|\| e\.metaKey\) return;/);
  assert.match(block, /if \(e\.isComposing \|\| e\.keyCode === 229\) return;/);
  assert.match(block, /if \(e\.key\.length !== 1 \|\| e\.key === " "\) return;/);
  // …and the rest — a missing or read-only box, key repeat, typing targets, the live-ask card's
  // number keys, open menus/dialogs, the full-pane surfaces — live in typeFromAnywhereTarget, the
  // ONE gate list this handler shares with paste-to-focus (2026-09-05; composer-paste-focus.test.ts)
  assert.match(block, /const ta = typeFromAnywhereTarget\(e\);\s*\n\s*if \(!ta\) return;/);
  const gates = RENDER.split("function typeFromAnywhereTarget(")[1].split("\n}")[0];
  assert.match(gates, /if \(!ta \|\| ta\.disabled \|\| document\.activeElement === ta\) return null;/);
  assert.match(gates, /if \(isTypingTarget\(e\.target\) \|\| isTypingTarget\(document\.activeElement\)\) return null;/);
  // the pane's own modals and meta menus own their keys, and a dropdown's type-ahead is typing: a
  // letter typed in the settings modal must never land in the hidden draft (review 2026-09-02)
  assert.match(gates, /document\.querySelector\("#rsettings:not\(\[hidden\]\), #ra-back:not\(\[hidden\]\), #rkeys-back, \.meta-menu"\)/);
  assert.match(RENDER, /elm\.tagName === "SELECT"/, "SELECT is a typing target");
  assert.match(gates, /if \(activeId && liveAsks\.has\(activeId\)\) return null;/);
  assert.match(gates, /if \(ctxMenuEl \|\| document\.querySelector\("\.picker-overlay"\)\) return null;/);
  assert.match(gates, /romp-fileview[\s\S]*romp-filebrowse[\s\S]*romp-lightbox/);
  // NEVER preventDefault: the point is that the native keystroke inserts into the newly focused
  // box, so the composer's own input bookkeeping (draft, slash menu) sees ordinary typing
  assert.doesNotMatch(block, /preventDefault/);
  // …and the armed quote chip survives the focus (a collapse never clears it), so ⌘⏎ stages
  // selection+typing exactly as if the user had clicked in: the existing stage pins above cover it
});
