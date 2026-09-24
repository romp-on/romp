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
  for (const m of MOVES) assert.match(BLOCK, new RegExp(m + ': jcBtn\\('), m + " is built once");
  assert.equal((BLOCK.match(/delegate\(jumpCluster, \{/g) || []).length, 1, "ONE delegated listener on the stable box");
  assert.doesNotMatch(UPDATE, /replaceChildren|createElement|innerHTML|appendChild/, "the update never rebuilds a control");
  assert.match(UPDATE, /if \(sig === jumpClusterSig\) return;/, "a signature skips unchanged paints");
  assert.doesNotMatch(BLOCK, /setTimeout|setInterval|requestAnimationFrame/, "nothing time-based in the whole block");
});

test("every move lands through scrollToAnchor; a comment's lands the chips' way and never marks the thread read", () => {
  assert.match(RUN, /else \{ flashedAnchor = null; scrollToAnchor\(hit\.uuid\); \}/, "your messages: the notch's route, one flash");
  assert.match(RUN, /if \(kind === "comment" && hit\.tid\) landThread\(sid, hit\.tid, hit\.uuid\);/);
  assert.match(RUN, /landThread\(sid, nx\.chip\.nearest\.tid, nx\.chip\.nearest\.uuid\);/, "the badge lands its thread the same way");
  assert.match(LAND, /flashedAnchor = null;[^\n]*\n\s*if \(scrollToAnchor\(uuid\)\) \{/);
  assert.match(LAND, /applyCommentMarks\(sid\);/, "a re-windowed anchor turn gets its highlight back before the pulse");
  assert.match(LAND, /if \(m\) flash\(m\);/, "the mark itself pulses (.romp-acted)");
  assert.match(fn("runJump"), /jumpMove\(move\);\s*\n\s*updateJumpCluster\(\);/, "the cluster re-reads its stops after every move: a clamped landing fires no scroll event");
  for (const [name, src] of [["jumpMove", RUN], ["landThread", LAND]] as const) {
    assert.doesNotMatch(src, /"commentSeen"|openCommentPopover\(|\.unread = false/, name + ": reading a reply is opening its thread");
    assert.doesNotMatch(src, /scrollTop =|scrollBy|scrollIntoView|writeScroll|scrollContentBy/, name + ": never pixel arithmetic, the uuid route only");
  }
});

test("unread means what the chips meant: the kernel's bit on an open thread, off screen, placed the chips' way", () => {
  const ready = fn("readyAround");
  assert.match(ready, /\(commentThreads\.get\(s\.id\) \|\| \[\]\)\.filter\(isReplyReady\)/);
  assert.match(ready, /\.\.\.placeMark\(r\.top - cr\.top, r\.bottom - cr\.top, H\)/, "a rendered mark by its own box against the viewport");
  assert.match(ready, /\.\.\.placeWindowed\(idx >= 0 \? evUnit\[idx\] : -1, v\.winStart, v\.winEnd \?\? v\.winStart\)/);
  assert.match(ready, /return readyChips\(marks\);/);
  assert.match(UPDATE, /const n = unreadCount\(chips\);/);
  assert.match(UPDATE, /badge\.hidden = !nx;/, "the badge shows only while a reply waits off screen");
  assert.match(RUN, /unreadNext\(readyAround\(s, v, c\), unreadDir && unreadDir\.sid === sid \? unreadDir\.dir : null\)/, "the badge continues the last press's direction");
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

test("quiet by default: shown only while the transcript overflows, each pill only while it has somewhere to go", () => {
  assert.match(UPDATE, /if \(!c \|\| !s \|\| !v \|\| H <= 0 \|\| s\.sub \|\| c\.scrollHeight <= H \+ 2\) \{ jumpCluster\.hidden = true; jumpClusterSig = ""; return; \}/,
    "no pane, the subagent viewer, or a transcript that fits on screen: no cluster");
  assert.match(UPDATE, /jcMine\.hidden = !mineHere;/);
  assert.match(UPDATE, /jcCmt\.hidden = !threadsHere;/, "the comment pill only while the session has comment threads");
  assert.match(UPDATE, /jcButtons\[m\]\.disabled = !able\[m\];/, "a direction with nothing that way is disabled, never removed: the pill keeps its shape");
  assert.match(UPDATE, /const bottom = Math\.max\(0, window\.innerHeight - c\.getBoundingClientRect\(\)\.bottom\) \+ 8;/, "the go-to-bottom chip's own measure");
  assert.match(RENDER, /jumpBtn\.style\.bottom = \(Math\.max\(0, window\.innerHeight - c\.getBoundingClientRect\(\)\.bottom\) \+ 8\) \+ "px";/);
});

// ── keys ──────────────────────────────────────────────────────────────────────────────────────────
test("each move is a command with a default key, rebindable, and free of every other binding", () => {
  const want: Record<string, string> = { "chat.prevMine": "Ctrl+Alt+ArrowUp", "chat.nextMine": "Ctrl+Alt+ArrowDown",
    "chat.prevComment": "Ctrl+Alt+Shift+ArrowUp", "chat.nextComment": "Ctrl+Alt+Shift+ArrowDown", "chat.nextUnread": "Ctrl+Alt+Enter" };
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
  assert.match(BLOCK, /e\.preventDefault\(\); e\.stopPropagation\(\);\s*\n\s*if \(!jumpCluster\.hidden && !jcButtons\[m\]\.hidden && !jcButtons\[m\]\.disabled\) flash\(jcButtons\[m\]\);/, "a key pulses its button, the click's acknowledgement");
  // the tooltips name the current key and follow a rebind
  assert.match(BLOCK, /const t = titleWithKey\(MOVE_TITLE\[m\], jumpCommand\(m\)\);/);
  assert.match(BLOCK, /window\.addEventListener\(KEYS_EVENT, dressJumpTips\);/);
});

// ── the sheet ─────────────────────────────────────────────────────────────────────────────────────
const rule = (sel: string): string => { const at = CSS.indexOf("\n" + sel + " {"); assert.ok(at >= 0, sel); return CSS.slice(at + 1, CSS.indexOf("}", at)); };   // the rule that opens a line
const coarse = CSS.slice(CSS.indexOf("@media (pointer: coarse) {\n  #jump-cluster"), CSS.indexOf("\n}\n", CSS.indexOf("@media (pointer: coarse) {\n  #jump-cluster")));

test("one column with the go-to-bottom chip: its left, its width, its slot held below, on both layouts", () => {
  const box = rule("#jump-cluster");
  assert.match(box, /position: fixed; left: 14px; z-index: 40;/, "the chip's left and layer");
  assert.match(box, /display: flex; flex-direction: column;/);
  const chip = rule("#jump-bottom");
  const chipH = Number(chip.match(/height: (\d+)px/)![1]), chipW = Number(chip.match(/width: (\d+)px/)![1]);
  assert.match(box, new RegExp("margin-bottom: " + (chipH + 6) + "px;"), "the chip's height and the stack gap, held whether it shows or not");
  const btnW = Number(rule(".jc-btn").match(/width: (\d+)px/)![1]);
  assert.equal(2 * btnW + 2, chipW, "a pill's two halves and hairline: the chip's width");
  assert.match(rule(".jc-group"), new RegExp("height: " + chipH + "px;"), "the chip's height");
  // the phone: the chip is 64×32 there; the pills follow, halves widened for the hit area
  const phoneChip = CSS.match(/@media \(pointer: coarse\) \{ #jump-bottom \{ width: (\d+)px; height: (\d+)px; \} \}/)!;
  assert.match(coarse, new RegExp("#jump-cluster \\{ margin-bottom: " + (Number(phoneChip[2]) + 6) + "px; \\}"));
  assert.match(coarse, new RegExp("\\.jc-group \\{ height: " + phoneChip[2] + "px; \\}"));
  const phoneBtn = Number(coarse.match(/\.jc-btn \{ width: (\d+)px; \}/)![1]);
  assert.equal(2 * phoneBtn + 2, Number(phoneChip[1]));
});

test("the chip's card, each pill in the colour of the rail marks it walks, the badge in the unread halo's red", () => {
  const card = rule(".jc-group");
  for (const tok of ["var(--vscode-menu-background, var(--surface-raised))", "border: 1px solid var(--menu-border)", "box-shadow: var(--shadow-toast)", "border-radius: var(--radius-pill)"]) {
    assert.ok(card.includes(tok), "the card has " + tok);
    assert.ok(rule("#jump-bottom").includes(tok), "…as the go-to-bottom chip does");
  }
  assert.match(CSS, /\.jc-mine \.jc-btn \{ color: var\(--you\); \}/, "your messages: the turn notches' blue");
  assert.match(CSS, /\.jc-cmt \.jc-btn \{ color: var\(--cmt-hl-outline\); \}/, "comments: the ticks' ink");
  const badge = rule(".jc-badge");
  assert.match(badge, /background: var\(--st-awaiting-bg\); color: var\(--st-awaiting-fg\);/, "the needs-you red the unread tick's halo wears");
  assert.match(badge, /font: inherit; font-size: 12px;/, "the bottom chrome's 12px, the page font");
  const block = CSS.slice(CSS.indexOf("#jump-cluster {"), CSS.indexOf("#seek-note .seek-note-x {"));
  assert.doesNotMatch(block.replace(/\/\*[\s\S]*?\*\//g, ""), /#[0-9a-fA-F]{3,6}\b/, "tokens only, no raw hex");
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  for (const tok of ["--you", "--cmt-hl-outline", "--menu-hover", "--accent"]) assert.match(light, new RegExp(tok + ":"), "the light theme defines " + tok);
  assert.match(CSS, /--st-awaiting-bg: #c0392b;--st-awaiting-fg: #ffffff;/, "the status pair, one value in both themes");
});

test("quiet until hovered or focused; the phone, with no hover, rests brighter; focus wears the accent", () => {
  assert.match(CSS, /\.jc-btn svg, \.jc-badge \{ opacity: 0\.55; transition: opacity 120ms ease; \}/);
  assert.match(CSS, /#jump-cluster:hover \.jc-btn svg, #jump-cluster:focus-within \.jc-btn svg, #jump-cluster:hover \.jc-badge, #jump-cluster:focus-within \.jc-badge \{ opacity: 1; \}/);
  assert.match(CSS, /#jump-cluster \.jc-btn:disabled svg \{ opacity: 0\.2; \}/);
  assert.match(coarse, /\.jc-btn svg, \.jc-badge \{ opacity: 0\.8; \}/);
  assert.match(CSS, /\.jc-btn:focus-visible, \.jc-badge:focus-visible \{ outline: 1\.5px solid var\(--accent\); outline-offset: -2px; \}/);
  assert.match(CSS, /#jump-cluster\[hidden\], #jump-cluster \.jc-group\[hidden\], #jump-cluster \.jc-badge\[hidden\] \{ display: none; \}/, "author display:flex defeats [hidden]");
});

test("the ui-verify fixture mirrors the builders class for class (synthetic, notes-api)", () => {
  const fx = read("tools/ui-verify/fixtures/jump-cluster-chat.html");
  assert.match(fx, /<div class="jump-cluster" id="jump-cluster"/);
  assert.match(fx, /<div class="jc-group jc-cmt" role="group" aria-label="Comments">/);
  assert.match(fx, /<button type="button" class="jc-badge" data-act="jcjump" data-move="nextUnread"/);
  assert.match(fx, /<div class="jc-group jc-mine" role="group" aria-label="Your messages">/);
  assert.match(fx, /11111111-2222-4333-8444-/, "placeholder ids");
});
