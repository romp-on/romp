// The jump cluster's wiring (the user 2026-09-24): the DOM half in render.ts, the sheet, the shell's commands and keys.
// The target selection is jump-nav.test.ts (by execution); here the source pins, the repo convention for renderers:
// body-level and click-safe, every move through scrollToAnchor (the old reply chips' and the rail's one landing road),
// the unread semantics the chips had, older history asked for through the page's own loaders, the go-to-bottom chip's
// slot held, the keys registered as commands and free of every other binding, the quiet dress. Synthetic only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { DEFAULT_CHORDS } from "./commands";
import { builtInOwner, resolveChord } from "./keybindings";
import { MOVES } from "./jump-nav";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", f), "utf8");
const RENDER = read("ui/webview/render.ts");
const CSS = read("ui/webview/styles.css");
const PALETTE = read("ui/webview/palette-main.ts");
const BLOCK = RENDER.slice(RENDER.indexOf('const jumpCluster = el("div", "jump-cluster");'), RENDER.indexOf("// The per-view saved spot FOLLOWS the reader"));
const fn = (name: string): string => { const at = BLOCK.indexOf("function " + name + "("); assert.ok(at >= 0, name + " exists"); return BLOCK.slice(at, BLOCK.indexOf("\n}\n", at) + 2); };
const UPDATE = fn("updateJumpCluster");
const RUN = fn("jumpMove");
const LAND = fn("landThread");

test("the old reply chips are gone: one arrow system, not two", () => {
  assert.doesNotMatch(RENDER, /reply-chips|reply-chip|updateReplyChips|replyjump/, "no chip box, no chip, no chip update, no chip click");
  assert.doesNotMatch(CSS, /#reply-chips|\.reply-chip/, "no chip dress left in the sheet");
});

test("body-level, created once, updated in place, one delegated listener: click-safe by construction", () => {
  assert.ok(BLOCK.length > 0, "the block sits after the go-to-bottom chip's wiring");
  assert.match(BLOCK, /jumpCluster\.id = "jump-cluster";/);
  assert.match(BLOCK, /document\.body\.appendChild\(jumpCluster\);/);
  assert.match(BLOCK, /b\.dataset\.act = "jcjump";\s*\n\s*b\.dataset\.move = move;/, "every control routes by data-act and names its move");
  // three capsules, top to bottom, each built once: up chevron, icon, down chevron
  assert.match(BLOCK, /const jcUnread = jcGroup\("jc-unread", "Unread replies"\);\s*\n\s*const jcCmt = jcGroup\("jc-cmt", "Comments"\);\s*\n\s*const jcMine = jcGroup\("jc-mine", "Your messages"\);/,
    "the capsules in the column's order, each with a label naming what it walks");
  assert.match(BLOCK, /g\.setAttribute\("role", "group"\);\s*\n\s*g\.setAttribute\("aria-label", label\);/);
  assert.match(BLOCK, /const up = jcBtn\(g, prev, "jc-btn", jcChevron\(true\)\);\s*\n\s*jcIcon\(g, icon\);\s*\n\s*return \[up, jcBtn\(g, next, "jc-btn", jcChevron\(false\)\)\];/, "up, icon, down");
  assert.match(BLOCK, /jcPair\(jcUnread, "prevUnread", "nextUnread", "unread"\)/);
  assert.match(BLOCK, /jcPair\(jcCmt, "prevComment", "nextComment", "comment"\)/);
  assert.match(BLOCK, /jcPair\(jcMine, "prevMine", "nextMine", "mine"\)/);
  assert.equal((BLOCK.match(/delegate\(jumpCluster, \{/g) || []).length, 1, "ONE delegated listener on the stable box");
  assert.doesNotMatch(UPDATE, /replaceChildren|createElement|innerHTML|appendChild/, "the update never rebuilds a control");
  assert.match(UPDATE, /if \(sig === jumpClusterSig\) return;/, "a signature skips unchanged paints");
  assert.doesNotMatch(BLOCK, /setTimeout|setInterval|requestAnimationFrame/, "nothing time-based in the whole block");
});

test("each capsule's icon: the house 16-unit line style, in the capsule's colour, hidden from the pointer and from readers", () => {
  const icon = BLOCK.slice(BLOCK.indexOf("const jcIcon = "), BLOCK.indexOf("};", BLOCK.indexOf("const jcIcon = ")));
  assert.match(icon, /i\.setAttribute\("aria-hidden", "true"\);/, "the capsule's label says it in words");
  assert.match(icon, /'<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '\s*\n\s*\+ 'stroke-width="1\.4" stroke-linecap="round" stroke-linejoin="round">'/, "ctxIcon's and the notices' line style");
  assert.match(BLOCK, /mine: '<circle cx="8" cy="5\.2" r="2\.6"\/>/, "a person for your messages");
  assert.match(BLOCK, /comment: JC_BUBBLE,/, "a speech bubble for comments");
  assert.match(BLOCK, /unread: JC_BUBBLE \+ '<circle cx="12\.6" cy="3\.4" r="2\.2" fill="currentColor" stroke="none"\/>'/, "the bubble with a filled dot for unread replies");
  assert.match(CSS, /\.jc-icon \{ height: 18px; display: flex; align-items: center; justify-content: center; pointer-events: none; \}/, "not a button: the chevrons are");
  assert.match(CSS, /\.jc-btn \{\s*\n\s*height: 22px; padding: 0; border: 0; background: none; cursor: pointer; color: inherit;/, "chevrons and icon take the capsule's colour");
});

test("every move lands through scrollToAnchor; a thread lands the chips' way and never marks it read", () => {
  assert.match(RUN, /else \{ flashedAnchor = null; scrollToAnchor\(hit\.uuid\); \}/, "your messages: the notch's route, one flash");
  assert.match(RUN, /if \(kind !== "mine" && hit\.tid\) landThread\(sid, hit\.tid, hit\.uuid\);/, "a thread, from either comment capsule");
  assert.match(RUN, /const kind = move === "prevMine" \|\| move === "nextMine" \? "mine" : move === "prevComment" \|\| move === "nextComment" \? "comment" : "unread";/);
  assert.match(LAND, /flashedAnchor = null;[^\n]*\n\s*if \(scrollToAnchor\(uuid\)\) \{/);
  assert.match(LAND, /applyCommentMarks\(sid\);/, "a re-windowed anchor turn gets its highlight back before the pulse");
  assert.match(LAND, /if \(m\) flash\(m\);/, "the mark itself pulses (.romp-acted)");
  assert.match(fn("runJump"), /jumpMove\(move\);\s*\n\s*updateJumpCluster\(\);/, "the cluster re-reads its stops after every move: a clamped landing fires no scroll event");
  for (const [name, src] of [["jumpMove", RUN], ["landThread", LAND]] as const) {
    assert.doesNotMatch(src, /"commentSeen"|openCommentPopover\(|\.unread = false/, name + ": reading a reply is opening its thread");
    assert.doesNotMatch(src, /scrollTop =|scrollBy|scrollIntoView|writeScroll|scrollContentBy/, name + ": never pixel arithmetic, the uuid route only");
  }
});

test("unread means what the chips meant: the kernel's bit on an open thread; the count off screen, placed the chips' way", () => {
  const ready = fn("readyAround");
  assert.match(ready, /\(commentThreads\.get\(s\.id\) \|\| \[\]\)\.filter\(isReplyReady\)/);
  assert.match(ready, /\.\.\.placeMark\(r\.top - cr\.top, r\.bottom - cr\.top, H\)/, "a rendered mark by its own box against the viewport");
  assert.match(ready, /\.\.\.placeWindowed\(idx >= 0 \? evUnit\[idx\] : -1, v\.winStart, v\.winEnd \?\? v\.winStart\)/);
  assert.match(ready, /return readyChips\(marks\);/);
  assert.match(UPDATE, /const unreadHere = threads\.some\(isReplyReady\);/, "the unread capsule shows while any thread's reply waits");
  assert.match(UPDATE, /const n = unreadHere \? unreadCount\(readyAround\(s, v, c\)\) : 0;/, "the count: the chips' number");
  assert.match(UPDATE, /jcUnread\.hidden = !unreadHere;/);
  assert.match(UPDATE, /jcCount\.hidden = n === 0;/, "every unread thread in view: no number, as the chips hid at 0");
  // the arrows walk the unread threads, in view or not (jump-nav.test.ts: the one on screen below a landing is next)
  assert.match(fn("navStops"), /\.filter\(\(t\) => kind === "comment" \|\| isReplyReady\(t\.th\)\)/);
  assert.doesNotMatch(BLOCK, /unreadNext|unreadDir/, "no direction memory: explicit arrows replaced it");
  assert.match(BLOCK, /jumpCluster\.appendChild\(jcCount\);/, "the count badge rides the cluster box, over the unread capsule's corner");
});

test("older history is asked for through the page's own loaders, and the move resumes when it lands", () => {
  assert.match(RUN, /if \("gap" in hit\.load\) requestTurns\(sid, hit\.load\.gap, dir === "above" \? "bottom" : "top"\);\s*\n\s*else requestOlder\(sid, v, c\);/,
    "a gap's page ask, or the scroll-back's older ask (both keep the reader's row where it is): no second loader");
  assert.match(RUN, /navResume = asked \? \{ sid, move \} : null;/, "nothing asked, nothing owed");
  assert.match(UPDATE, /if \(navResume && navResume\.sid === activeId && !loadingOlder\.has\(activeId!\) && pendingBuildRaf == null/,
    "resumed once nothing is on the wire and the view is built");
  const stops = fn("navStops");
  assert.match(stops, /else if \(olderOnServer\(s\)\) stops\.push\(\{ uuid: "", load: \{ head: true \}, place: \{ dir: "above", dist: HEAD_DIST \} \}\);/);
  assert.match(stops, /load: \{ gap: \{ lo: it\.lo, hi: it\.hi \} \}/);
});

test("the reader's position: a landing the scroll clamped at the bottom while it shows, else the viewport (its bottom at the bottom)", () => {
  const stops = fn("navStops");
  assert.match(stops, /if \(navCursor && \(navCursor\.sid !== s\.id \|\| navCursor\.mine !== own\.length\)\) navCursor = null;/, "a new message of theirs, or another tab, ends it");
  assert.match(stops, /if \(navCursor && atBottom\(content\)\) \{/, "only at the bottom, where the scroll could not top-align the target");
  assert.match(stops, /if \(r && i >= 0 && evUnit\[i\] >= 0 && r\.bottom > cr\.top && r\.top < cr\.bottom\) cur = \{ unit: evUnit\[i\], rank: navCursor\.rank \};/, "…and its target still on screen");
  assert.match(stops, /const bottom = !cur && atBottom\(content\);/, "the go-to-bottom chip's own at-bottom read");
  assert.match(stops, /const line = bottom \? content\.clientHeight : 0, here = bottom \? 0 : HERE_PX;/);
  assert.match(UPDATE, /if \(navResume && navResume\.sid !== activeId\) navResume = null;/, "a tab left mid-load is not jumped on the way back");
  // the reader's own messages are the notches' filter with the "user" verdict only
  assert.match(fn("ownTurnIndices"), /if \(ev\.kind !== "user" \|\| !ev\.uuid \|\| senderKind\(ev\) !== "user"\) continue;/);
  assert.match(fn("ownTurnIndices"), /if \(!md \|\| ev\.canned === "continue" \|\| SLASH_CMD_RE\.test\(md\)\) continue;/, "gestures are doings, not messages");
});

test("the events it rides are the chips' events: the go-to-bottom update, applyCommentMarks' tail, the pane's size", () => {
  assert.match(RENDER, /if \(!liveSession\(activeId\)\) \{ jumpBtn\.hidden = true; updateJumpCluster\(\); return; \}/);
  assert.match(RENDER, /if \(off\) jumpBtn\.style\.bottom = [^\n]*\n\s*updateJumpCluster\(\);/, "updateJumpBtn's tail");
  assert.match(RENDER, /paintCommentOutlines\(sid\);[^\n]*\n[^\n]*\n[^\n]*\n\s*if \(sid === activeId\) updateJumpCluster\(\);\s*\n\s*reopenCommentForReload\(sid\);/, "applyCommentMarks' tail");
  assert.match(RENDER, /if \(!threads\.length\) \{ paintCommentOutlines\(sid\); if \(sid === activeId\) updateJumpCluster\(\); reopenCommentForReload\(sid\); return; \}/);
  assert.match(BLOCK, /new ResizeObserver\(updateJumpCluster\)\.observe\(c\);/);
});

test("quiet by default: shown only while the transcript overflows, each capsule only while it has somewhere to go", () => {
  assert.match(UPDATE, /if \(!c \|\| !s \|\| !v \|\| H <= 0 \|\| s\.sub \|\| c\.scrollHeight <= H \+ 2\) \{ jumpCluster\.hidden = true; jumpClusterSig = ""; return; \}/,
    "no pane, the subagent viewer, or a transcript that fits on screen: no cluster");
  assert.match(UPDATE, /jcMine\.hidden = !mineHere;/);
  assert.match(UPDATE, /jcCmt\.hidden = !threadsHere;/, "the comment capsule only while the session has comment threads");
  assert.match(UPDATE, /for \(const m of MOVES\) jcButtons\[m\]\.disabled = !able\[m\];/, "a direction with nothing that way is disabled, never removed: the capsule keeps its shape");
  assert.match(UPDATE, /const bottom = Math\.max\(0, window\.innerHeight - c\.getBoundingClientRect\(\)\.bottom\) \+ 8;/, "the go-to-bottom chip's own measure");
  assert.match(RENDER, /jumpBtn\.style\.bottom = \(Math\.max\(0, window\.innerHeight - c\.getBoundingClientRect\(\)\.bottom\) \+ 8\) \+ "px";/);
});

// ── keys ──────────────────────────────────────────────────────────────────────────────────────────
test("each move is a command with a default key, rebindable, and free of every other binding", () => {
  const want: Record<string, string> = { "chat.prevMine": "Ctrl+Alt+ArrowUp", "chat.nextMine": "Ctrl+Alt+ArrowDown",
    "chat.prevComment": "Ctrl+Alt+Shift+ArrowUp", "chat.nextComment": "Ctrl+Alt+Shift+ArrowDown",
    "chat.prevUnread": "Ctrl+Alt+PageUp", "chat.nextUnread": "Ctrl+Alt+PageDown" };
  for (const [id, chord] of Object.entries(want)) {
    assert.equal(DEFAULT_CHORDS[id], chord, id);
    for (const mac of [false, true]) {
      assert.equal(builtInOwner(chord, mac), null, id + ": no built-in owns " + chord + (mac ? " on a Mac" : ""));
      const clash = Object.entries(DEFAULT_CHORDS).filter(([o, c]) => o !== id && resolveChord(c, mac) === resolveChord(chord, mac));
      assert.deepEqual(clash, [], id + ": no other command's default");
    }
    assert.ok(PALETTE.includes('["' + id + '", "'), id + " is registered in the shell's palette");
  }
  assert.equal(Object.keys(want).length, MOVES.length);
  assert.match(PALETTE, /postMessage\(\{ romp: "chatJump", move: id\.slice\(5\) \}, "\*"\)/, "from the shell, the move is posted to the chat");
  assert.match(RENDER, /if \(m\.romp === "chatJump"\) \{ if \(\(MOVES as readonly string\[\]\)\.includes\(m\.move\)\) runJump\(m\.move as Move\); return; \}/);
  // with focus in the chat, its own capture handler answers, reading the same overrides per press (the chat.navBack pattern)
  assert.match(BLOCK, /const m = MOVES\.find\(\(mv\) => ch === effectiveChord\(jumpCommand\(mv\), DEFAULT_CHORDS\[jumpCommand\(mv\)\], ov, mac\)\);/);
  assert.match(BLOCK, /e\.preventDefault\(\); e\.stopPropagation\(\);\s*\n\s*if \(!jumpCluster\.hidden && !jcButtons\[m\]\.disabled\) flash\(jcButtons\[m\]\);/, "a key pulses its button, the click's acknowledgement");
  // the tooltips name the current key and follow a rebind
  assert.match(BLOCK, /const t = titleWithKey\(MOVE_TITLE\[m\], jumpCommand\(m\)\);/);
  assert.match(BLOCK, /window\.addEventListener\(KEYS_EVENT, dressJumpTips\);/);
});

// ── the sheet ─────────────────────────────────────────────────────────────────────────────────────
const rule = (sel: string): string => { const at = CSS.indexOf("\n" + sel + " {"); assert.ok(at >= 0, sel); return CSS.slice(at + 1, CSS.indexOf("}", at)); };   // the rule that opens a line
const coarse = CSS.slice(CSS.indexOf("@media (pointer: coarse) {\n  #jump-cluster"), CSS.indexOf("\n}\n", CSS.indexOf("@media (pointer: coarse) {\n  #jump-cluster")));

// the cluster's geometry tokens, desktop and phone: the column's width, the gap between capsules, the go-to-bottom chip's height
const tokens = (src: string): Record<string, number> => {
  const m = src.match(/#jump-cluster, #jump-bottom \{ ([^}]*)\}/);
  assert.ok(m, "the tokens rule");
  return Object.fromEntries(Array.from(m![1].matchAll(/--(jc-[a-z-]+): (\d+)px;/g), (t) => [t[1], Number(t[2])]));
};

test("one narrow column above the go-to-bottom chip: its left, its slot held below, on both layouts", () => {
  const box = rule("#jump-cluster");
  assert.match(box, /position: fixed; left: 14px; z-index: 40;/, "the chip's left and layer");
  assert.match(box, /display: flex; flex-direction: column; align-items: flex-start; gap: var\(--jc-gap\);/);
  assert.match(box, /margin-bottom: calc\(var\(--jc-chip-h\) \+ var\(--jc-gap\)\);/, "the chip's height and the capsules' own gap, held whether it shows or not");
  const group = rule(".jc-group");
  assert.match(group, /display: flex; flex-direction: column; width: var\(--jc-w\);/, "each capsule stacks up above down, the column's width");
  const desk = tokens(CSS);
  assert.deepEqual(desk, { "jc-w": 24, "jc-gap": 6, "jc-chip-h": 30 }, "24 wide, 6 apart, the chip 30 tall");
  assert.match(rule(".jc-btn"), /height: 22px;/, "desktop buttons 22×22 inside the hairline");
  // the phone: the strip stays thin; the touch size comes from height, for the arrows and the chip alike
  const phone = tokens(coarse);
  assert.deepEqual(phone, { "jc-w": 34, "jc-chip-h": 42 }, "34 wide, the chip 42 tall; the gap is the desktop's");
  const ph = Number(coarse.match(/\.jc-btn \{ height: (\d+)px; \}/)![1]);
  assert.equal(ph, 40, "each arrow 32×40 inside the hairline");
  assert.ok(ph > phone["jc-w"] - 2, "…taller than wide: height, not width, gets the touch size");
  assert.ok(phone["jc-chip-h"] - 2 >= 40, "the chip's touch size from height too: at least 40 inside the hairline");
  assert.ok(phone["jc-chip-h"] > phone["jc-w"] && desk["jc-chip-h"] > desk["jc-w"], "a short capsule, a touch taller than wide, on both layouts");
  assert.match(coarse, /\.jc-icon \{ height: 24px; \}/);
  assert.doesNotMatch(coarse, /#jump-cluster \{ margin-bottom:|\.jc-group \{ width:/, "the phone restates no geometry the tokens carry");
});

test("the go-to-bottom chip is the column's foot: its width, its gap, its dress and its glyph, each from one source", () => {
  const bare = (r: string): string => r.replace(/\/\*[\s\S]*?\*\//g, "");   // declarations only, never a comment's words
  const chip = bare(rule("#jump-bottom"));
  assert.match(chip, /width: var\(--jc-w\); height: var\(--jc-chip-h\);/, "the column's width token, never a pixel width of its own");
  assert.match(chip, /color: var\(--fg\);/, "neutral ink: it is none of the three kinds");
  assert.doesNotMatch(chip, /background|border|box-shadow|radius/, "no dress of its own: the shared rule is the only one");
  assert.doesNotMatch(bare(rule(".jc-group")), /background|border|box-shadow|radius/, "…and the capsule has none of its own either");
  // the glyph: the arrows' size, resting glow, lit state and phone size, in the same selector lists
  assert.match(CSS, /\n\.jc-btn svg, \.jc-icon svg, #jump-bottom svg \{ width: 14px; height: 14px; \}/);
  assert.match(CSS, /\n\.jc-btn svg, \.jc-icon svg, \.jc-count, #jump-bottom svg \{ opacity: 0\.55; transition: opacity 120ms ease; \}/);
  assert.match(coarse, /\.jc-btn svg, \.jc-icon svg, #jump-bottom svg \{ width: 16px; height: 16px; \}/);
  assert.match(coarse, /\.jc-btn svg, \.jc-icon svg, \.jc-count, #jump-bottom svg \{ opacity: 0\.8; \}/);
  assert.match(CSS, /@media \(prefers-reduced-motion: reduce\) \{ \.jc-btn svg, \.jc-icon svg, \.jc-count, #jump-bottom svg \{ transition: none; \} \}/);
  // the chevron itself: the same builder the capsule arrows use
  assert.match(RENDER, /jumpBtn\.innerHTML = jcChevron\(false\);/);
});

test("one dress for the capsules and the chip, each capsule in the colour of the rail marks it walks, the count in the unread halo's red", () => {
  const card = rule("#jump-bottom, .jc-group");
  for (const tok of ["background: var(--vscode-menu-background, var(--surface-raised))", "border: 1px solid var(--menu-border)", "box-shadow: var(--shadow-toast)", "border-radius: var(--radius-pill)"]) {
    assert.ok(card.includes(tok), "the capsules and the go-to-bottom chip share " + tok);
  }
  assert.match(CSS, /\.jc-unread \{ color: var\(--st-awaiting-bg\); \}/, "unread replies: the needs-you red of the unread halo and outline");
  assert.match(CSS, /\.jc-mine \{ color: var\(--you\); \}/, "your messages: the turn notches' blue");
  assert.match(CSS, /\.jc-cmt \{ color: var\(--cmt-hl-outline\); \}/, "comments: the ticks' ink");
  const count = rule(".jc-count");
  assert.match(count, /position: absolute; top: -6px; left: -9px;/, "on the unread capsule's upper-left corner, over the empty margin: the column is not widened");
  assert.match(count, /pointer-events: none;/, "it covers no button");
  assert.match(count, /background: var\(--st-awaiting-bg\); color: var\(--st-awaiting-fg\);/);
  assert.match(count, /font-size: 12px;/, "the bottom chrome's 12px");
  assert.match(count, /box-shadow: 0 0 0 1\.5px var\(--bg\);/, "the unread tick's page-coloured gap");
  const block = CSS.slice(CSS.indexOf("#jump-cluster {"), CSS.indexOf("#seek-note .seek-note-x {"));
  assert.doesNotMatch(block.replace(/\/\*[\s\S]*?\*\//g, ""), /#[0-9a-fA-F]{3,6}\b/, "tokens only, no raw hex");
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  for (const tok of ["--you", "--cmt-hl-outline", "--menu-hover", "--accent", "--bg"]) assert.match(light, new RegExp(tok + ":"), "the light theme defines " + tok);
  assert.match(CSS, /--st-awaiting-bg: #c0392b;--st-awaiting-fg: #ffffff;/, "the status pair, one value in both themes");
});

test("quiet until hovered or focused; the phone, with no hover, rests brighter; focus wears the accent", () => {
  assert.match(CSS, /\.jc-btn svg, \.jc-icon svg, \.jc-count, #jump-bottom svg \{ opacity: 0\.55; transition: opacity 120ms ease; \}/, "the go-to-bottom chip's glyph rests with them");
  assert.match(CSS, /#jump-cluster:hover \.jc-btn svg, #jump-cluster:focus-within \.jc-btn svg, #jump-cluster:hover \.jc-icon svg, #jump-cluster:focus-within \.jc-icon svg,\s*\n#jump-cluster:hover \.jc-count, #jump-cluster:focus-within \.jc-count, #jump-bottom:hover svg, #jump-bottom:focus-visible svg \{ opacity: 1; \}/);
  assert.match(CSS, /#jump-cluster \.jc-btn:disabled svg \{ opacity: 0\.2; \}/);
  assert.match(coarse, /\.jc-btn svg, \.jc-icon svg, \.jc-count, #jump-bottom svg \{ opacity: 0\.8; \}/);
  assert.match(CSS, /\.jc-btn:focus-visible, #jump-bottom:focus-visible \{ outline: 1\.5px solid var\(--accent\); outline-offset: -2px; \}/);
  assert.match(CSS, /#jump-cluster\[hidden\], #jump-cluster \.jc-group\[hidden\], #jump-cluster \.jc-count\[hidden\] \{ display: none; \}/, "author display:flex defeats [hidden]");
});

test("the ui-verify fixture mirrors the builders class for class (synthetic, notes-api)", () => {
  const fx = read("tools/ui-verify/fixtures/jump-cluster-chat.html");
  assert.match(fx, /<div class="jump-cluster" id="jump-cluster"/);
  assert.match(fx, /<div class="jc-group jc-unread" role="group" aria-label="Unread replies">/);
  assert.match(fx, /<div class="jc-group jc-cmt" role="group" aria-label="Comments">/);
  assert.match(fx, /<div class="jc-group jc-mine" role="group" aria-label="Your messages">/);
  assert.match(fx, /<span class="jc-icon" aria-hidden="true">/);
  assert.match(fx, /<span class="jc-count"/);
  assert.match(fx, /11111111-2222-4333-8444-/, "placeholder ids");
  // the chip's chevron is the builder's down chevron, byte for byte
  const chev = fx.match(/<button id="jump-bottom"[^>]*>(<svg[\s\S]*?<\/svg>)<\/button>/);
  assert.ok(chev, "the fixture carries the go-to-bottom chip");
  assert.equal(chev![1], '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9.5 12 15.5 18 9.5"/></svg>');
});
