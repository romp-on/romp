// Replies ready (the user 2026-09-08): a reply lands on a comment you scrolled away from and you forget to
// come back. Two chips stacked over the go-to-bottom chip — "↑ 2 replies unread" / "↓ 1 reply unread" —
// count the unread landed replies whose marks sit wholly above / below the viewport; a click lands the
// nearest one in that direction through the chat's own scroll-to-uuid route and OPENS its thread, which is
// what marks it read (the user 2026-09-10: until then the chip only brought you to the mark and left you a
// second click to reach the reply). The pure half (reply-ready.ts) is driven behaviorally; the
// render.ts / CSS wiring is pinned at the source (no jsdom harness for the renderers — the repo
// convention). Synthetic text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { isReplyReady, placeMark, placeWindowed, readyChips, firstLine, replyLine, replyWord, chipLabel, chipTip, chipAria,
         WINDOWED_OUT, type ReadyMark } from "./reply-ready";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const mark = (tid: string, dir: "above" | "below" | "in", dist: number, line = "q " + tid): ReadyMark =>
  ({ tid, uuid: "u-" + tid, line, dir, dist });

// ── the predicate ─────────────────────────────────────────────────────────────────────────────────
test("a reply is ready when the kernel's unread bit sits on an OPEN thread — the mark's own .unread rule", () => {
  assert.equal(isReplyReady({ unread: true, status: "open" }), true);
  assert.equal(isReplyReady({ unread: false, status: "open" }), false, "read");
  assert.equal(isReplyReady({ unread: true, status: "resolved" }), false, "a resolved thread's stale bit waits on nobody");
  assert.equal(isReplyReady({ unread: true, status: "merged" }), false);
  assert.equal(isReplyReady({ unread: true, status: "promoted" }), false);
  // the same expression the mark and the rail tick read, so the chips can never count a mark that shows no ring
  assert.match(RENDER, /m\.classList\.toggle\("unread", !!th\.unread && th\.status === "open"\);/);
  assert.match(RENDER, /\(th\.unread && th\.status === "open" \? " unread" : ""\)/);
});

// ── placement against the viewport ────────────────────────────────────────────────────────────────
test("a mark wholly above the top edge is above, wholly below the bottom edge is below, anything on screen is in", () => {
  const H = 600;
  assert.deepEqual(placeMark(-80, -60, H), { dir: "above", dist: 60 });
  assert.deepEqual(placeMark(-20, 0, H), { dir: "above", dist: 0 }, "its bottom exactly at the edge: not one pixel visible");
  assert.deepEqual(placeMark(650, 670, H), { dir: "below", dist: 50 });
  assert.deepEqual(placeMark(600, 620, H), { dir: "below", dist: 0 }, "its top exactly at the bottom edge");
  assert.deepEqual(placeMark(100, 120, H), { dir: "in", dist: 0 });
  assert.deepEqual(placeMark(-10, 10, H), { dir: "in", dist: 0 }, "partly visible at the top counts as seen");
  assert.deepEqual(placeMark(590, 610, H), { dir: "in", dist: 0 }, "partly visible at the bottom counts as seen");
  assert.deepEqual(placeMark(-100, 700, H), { dir: "in", dist: 0 }, "taller than the viewport, spanning it");
});

test("a windowed-out anchor takes its direction from event order and a distance tier past every pixel", () => {
  // the rendered window spans units [10, 30)
  assert.deepEqual(placeWindowed(4, 10, 30), { dir: "above", dist: WINDOWED_OUT + 6 });
  assert.deepEqual(placeWindowed(9, 10, 30), { dir: "above", dist: WINDOWED_OUT + 1 }, "just before the window");
  assert.deepEqual(placeWindowed(30, 10, 30), { dir: "below", dist: WINDOWED_OUT + 1 }, "the first unit after the window");
  assert.deepEqual(placeWindowed(45, 10, 30), { dir: "below", dist: WINDOWED_OUT + 16 });
  assert.deepEqual(placeWindowed(-1, 10, 30), { dir: "above", dist: WINDOWED_OUT * 2 }, "older than the resident head: above, farthest");
  assert.deepEqual(placeWindowed(15, 10, 30), { dir: "in", dist: 0 }, "inside the window with no turn of its own (a folded run): on screen in folded form");
  assert.ok(WINDOWED_OUT > 1e6, "no screen is a million pixels tall — every rendered mark is nearer than a windowed-out one");
});

// ── the chips: presence, counts, nearest ─────────────────────────────────────────────────────────
test("above only / below only / both / none — a chip exists only while its count is > 0", () => {
  assert.deepEqual(readyChips([]), { above: null, below: null });
  const a = readyChips([mark("a", "above", 100), mark("b", "above", 40)]);
  assert.equal(a.above?.count, 2); assert.equal(a.below, null);
  const b = readyChips([mark("c", "below", 300)]);
  assert.equal(b.above, null); assert.equal(b.below?.count, 1);
  const both = readyChips([mark("a", "above", 100), mark("c", "below", 300), mark("d", "below", 20)]);
  assert.equal(both.above?.count, 1); assert.equal(both.below?.count, 2);
});

test("a mark inside the viewport counts in NEITHER direction", () => {
  const r = readyChips([mark("a", "above", 100), mark("v", "in", 0), mark("w", "in", 0), mark("c", "below", 50)]);
  assert.equal(r.above?.count, 1);
  assert.equal(r.below?.count, 1);
  assert.deepEqual(readyChips([mark("v", "in", 0)]), { above: null, below: null }, "the one unread reply is on screen: no chips at all");
});

test("the nearest per direction is the least distance; a rendered mark beats a windowed-out one; ties keep the earlier", () => {
  const r = readyChips([mark("far", "above", 900), mark("near", "above", 12), mark("out", "above", WINDOWED_OUT + 1),
                        mark("b1", "below", 30), mark("b2", "below", 30)]);
  assert.equal(r.above?.nearest.tid, "near");
  assert.equal(r.above?.count, 3, "windowed-out marks still count");
  assert.equal(r.below?.nearest.tid, "b1", "the tie keeps the earlier (transcript order)");
  const onlyOut = readyChips([mark("o2", "above", WINDOWED_OUT + 9), mark("o1", "above", WINDOWED_OUT + 2)]);
  assert.equal(onlyOut.above?.nearest.tid, "o1", "windowed-out marks order among themselves by units from the window edge");
});

// ── copy: label, tip, aria ─────────────────────────────────────────────────────────────────────────
test("the label is the arrow's phrase in the user's words, singular handled — 'reply', never 'comment'", () => {
  assert.equal(replyWord(1), "1 reply unread");
  assert.equal(replyWord(2), "2 replies unread");
  assert.equal(chipLabel(3), "3 replies unread");
  assert.doesNotMatch(chipLabel(1) + chipLabel(2), /comment|thread|card|board/, "no romp nouns, and the unread thing is the ANSWER to their comment");
});

test("the tip says how many wait in that direction and names the nearest by the first line of what you said there", () => {
  assert.equal(chipTip("above", 2, "keep the old header behind a flag"), "2 replies unread above · nearest: keep the old header behind a flag");
  assert.equal(chipTip("below", 1, "ping me when CI is green"), "1 reply unread below · nearest: ping me when CI is green");
  assert.equal(chipTip("below", 1, ""), "1 reply unread below", "no line, no dangling 'nearest:'");
  assert.equal(chipAria("above", 2), "2 replies unread above — go to the nearest");
});

test("firstLine takes the first non-empty line and clips long ones with an ellipsis", () => {
  assert.equal(firstLine("\n\n  keep the flag  \nsecond line"), "keep the flag");
  assert.equal(firstLine(""), "");
  const long = "a".repeat(80);
  const clipped = firstLine(long);
  assert.equal(clipped.length, 60);
  assert.ok(clipped.endsWith("…"));
  assert.equal(firstLine("exactly sixty characters long text ........................"), "exactly sixty characters long text ........................", "at the cap: untouched");
  assert.equal(firstLine("word word word word", 11), "word word…", "clips at the cap and trims the trailing space before the ellipsis");
});

test("replyLine names the thread by your LAST message in it (what the landed reply answers), else its name, else the passage", () => {
  const msgs = [{ who: "you" as const, text: "first ask\nmore", t: 1 }, { who: "agent" as const, text: "answer", t: 2 },
                { who: "you" as const, text: "follow-up ask", t: 3 }, { who: "agent" as const, text: "landed", t: 4 }];
  assert.equal(replyLine({ msgs, name: "web-comment-1", exact: "the passage" }), "follow-up ask");
  assert.equal(replyLine({ msgs: [], name: "web-comment-1", exact: "the passage" }), "web-comment-1");
  assert.equal(replyLine({ msgs: [], name: "", exact: "the passage" }), "the passage");
});

// ── render.ts wiring (source pins) ────────────────────────────────────────────────────────────────
const BLOCK = RENDER.slice(RENDER.indexOf('const replyChips = el("div", "reply-chips");'), RENDER.indexOf("// The per-view saved spot FOLLOWS the reader"));
const UPDATE = BLOCK.slice(BLOCK.indexOf("function updateReplyChips(): void {"), BLOCK.indexOf("\n{\n  const c = document.getElementById(\"content\");"));
const CLICK = BLOCK.slice(BLOCK.indexOf("replyjump: (elx) => {"), BLOCK.indexOf("if (typeof ResizeObserver"));

test("the box lives on BODY, created once, and the two chips update IN PLACE — click-safety is structural", () => {
  assert.ok(BLOCK.length > 0, "the block is where the test expects it (after the jump chip's wiring)");
  assert.match(BLOCK, /replyChips\.id = "reply-chips";/);
  assert.match(BLOCK, /document\.body\.appendChild\(replyChips\);/);
  assert.match(BLOCK, /const replyAbove = replyChip\("above"\);\s*\n\s*const replyBelow = replyChip\("below"\);/, "two chips, made once");
  assert.match(BLOCK, /b\.dataset\.act = "replyjump";/);
  assert.match(BLOCK, /b\.dataset\.dir = dir;/);
  assert.match(BLOCK, /b\.id = "reply-" \+ dir;/);
  assert.equal((BLOCK.match(/delegate\(replyChips, \{/g) || []).length, 1, "ONE delegated listener on the stable box");
  assert.doesNotMatch(UPDATE, /replaceChildren|createElement|innerHTML|appendChild/, "the update never rebuilds a chip — label, tip and target swap in place");
  assert.match(BLOCK, /b\.dataset\.tid = chip\.nearest\.tid;\s*\n\s*b\.dataset\.uuid = chip\.nearest\.uuid;/,
    "…the target rides the chip's data-attrs, read at click time");
});

test("the dress is the jump chip's: setTip for the tooltip, an aria-label, the chevron glyph, the phrase as the label", () => {
  assert.match(BLOCK, /setTip\(b, chipTip\(dir, chip\.count, chip\.nearest\.line\)\);/, "the one styled tip (never a native title)");
  assert.match(BLOCK, /b\.setAttribute\("aria-label", chipAria\(dir, chip\.count\)\);/);
  assert.match(BLOCK, /\.textContent = chipLabel\(chip\.count\);/);
  assert.match(BLOCK, /"6 14\.5 12 8\.5 18 14\.5" : "6 9\.5 12 15\.5 18 9\.5"/, "the jump chip's stemless chevron, turned up or down");
  assert.doesNotMatch(BLOCK, /\.title = /, "no native tooltip beside the styled one");
});

test("counts derive from the kernel's unread set ∩ the active transcript; the viewer and a hidden pane show none", () => {
  assert.match(UPDATE, /\(commentThreads\.get\(activeId\) \|\| \[\]\)\.filter\(isReplyReady\)/, "the kernel's bit, the mark's own rule");
  assert.match(UPDATE, /if \(!c \|\| !s \|\| !v \|\| H <= 0 \|\| s\.sub \|\| !ready\.length\) \{ replyChips\.hidden = true; replyChipSig = ""; return; \}/,
    "no pane / the read-only subagent viewer / nothing unread → no chips");
  // a rendered mark is placed by its own box (the turn's when the text drifted past re-matching); a windowed-out
  // anchor by event order against the rendered window — both through the pure placement
  assert.match(UPDATE, /const node = \(turn\.querySelector\(`mark\.cmt-hl\[data-tid="\$\{cssEscape\(th\.tid\)\}"\]`\) as HTMLElement \| null\) \|\| turn;/);
  assert.match(UPDATE, /\.\.\.placeMark\(r\.top - cr\.top, r\.bottom - cr\.top, H\)/);
  assert.match(UPDATE, /\.\.\.placeWindowed\(idx >= 0 \? evUnit\[idx\] : -1, v\.winStart, v\.winEnd \?\? v\.winStart\)/);
  assert.match(UPDATE, /const \{ above, below \} = readyChips\(marks\);/);
  assert.match(UPDATE, /replyChips\.hidden = !above && !below;/);
});

test("every input is an event the chat already listens for — no timers, no polling", () => {
  assert.doesNotMatch(BLOCK, /setTimeout|setInterval|requestAnimationFrame/, "nothing time-based in the whole block");
  // the jump chip's own update (scroll, resize, content ResizeObserver, tab switch, append) ends by re-placing the chips
  assert.match(RENDER, /if \(off\) jumpBtn\.style\.bottom = [^\n]*\n\s*updateReplyChips\(\);/, "updateJumpBtn's tail");
  assert.match(RENDER, /c\.addEventListener\("scroll", updateJumpBtn, \{ passive: true \}\);/, "…and that update rides the passive scroll listener");
  // the comments frame and every transcript rebuild re-anchor the marks, then recount (the marks are what gets measured)
  assert.match(RENDER, /turn\.classList\.toggle\("cmt-rail-unread", [^\n]*\n\s*\}\s*\n[^\n]*\n[^\n]*\n\s*if \(sid === activeId\) updateReplyChips\(\);\s*\n\}/, "applyCommentMarks' tail");
  assert.match(RENDER, /if \(!threads\.length\) \{ if \(sid === activeId\) updateReplyChips\(\); return; \}/, "…and its no-threads early return drops the chips");
  assert.match(BLOCK, /new ResizeObserver\(updateReplyChips\)\.observe\(c\);/, "a pane measuring 0 drops the chips like the jump chip");
  assert.match(UPDATE, /if \(sig === replyChipSig\) return;/, "a signature skips unchanged paints (pure scrolls do no DOM work)");
});

test("click = the chat's own scroll-to-uuid landing + one pulse on the mark, then the thread OPENS — which is what marks it read", () => {
  // WHY the open (the user 2026-09-10): the chip's job is to get you to the reply, and the reply lives in the
  // thread's popover, not on the mark — landing on the mark and stopping handed you a second click, and that
  // click (on nested marks, #1249) could open the wrong thread. Opening goes through openCommentPopover, the
  // ONE clearer of unread (its optimistic drop + commentSeen), so the chip's count falls by the thread it
  // opened and nothing here writes the bit itself.
  assert.match(CLICK, /flashedAnchor = null;/, "fresh navigation → landOn's one flash, the tick's and the notch's route");
  assert.match(CLICK, /if \(scrollToAnchor\(uuid\)\) \{/);
  assert.match(CLICK, /applyCommentMarks\(activeId\);/, "a re-windowed anchor turn gets its highlight back before the pulse");
  assert.match(CLICK, /if \(m\) flash\(m\);/, "the mark itself pulses (.romp-acted)");
  assert.match(CLICK, /openCommentPopover\(activeId, tid\);/, "opens the thread the chip named, by tid — the popover's geometry is fixed, no click point needed");
  assert.doesNotMatch(CLICK, /"commentSeen"|\.unread = false/, "the bit keeps its ONE clearer (openCommentPopover); the chip never writes it directly");
  assert.doesNotMatch(CLICK, /scrollTop =|scrollBy|scrollIntoView/, "never pixel arithmetic — the uuid route only");
  // the open does not hang on the landing: an anchor the route could not scroll to (windowed out, an older-history
  // fetch pending) still gets its reply shown — the open sits AFTER the scroll block, outside it
  const scrollBlock = CLICK.indexOf("if (scrollToAnchor(uuid)) {");
  const scrollEnd = CLICK.indexOf("\n        }", scrollBlock);
  assert.ok(scrollBlock > 0 && scrollEnd > scrollBlock, "the scroll block is where the test expects it");
  assert.ok(CLICK.indexOf("openCommentPopover(activeId, tid);") > scrollEnd, "the open follows the scroll block unconditionally");
});

test("the chips stack OVER the jump chip in its slot: same bottom measure, lifted by the jump chip's real height while it shows", () => {
  assert.match(UPDATE, /const bottom = Math\.max\(0, window\.innerHeight - cr\.bottom\) \+ 8 \+ \(jumpBtn\.hidden \? 0 : jumpBtn\.offsetHeight \+ 6\);/);
  assert.match(RENDER, /jumpBtn\.style\.bottom = \(Math\.max\(0, window\.innerHeight - c\.getBoundingClientRect\(\)\.bottom\) \+ 8\) \+ "px";/, "the jump chip's own measure — the same +8");
  assert.match(UPDATE, /replyChips\.style\.bottom = bottom \+ "px";/);
  assert.match(CSS, /#reply-chips \{ position: fixed; left: 14px; z-index: 40; display: flex; flex-direction: column; align-items: flex-start; gap: 6px; \}/,
    "the jump chip's left and z-index; a column, so ↑ sits above ↓");
});

// ── CSS: the jump chip's dress, token for token ───────────────────────────────────────────────────
const JUMP = CSS.slice(CSS.indexOf("#jump-bottom {"), CSS.indexOf("#jump-bottom:hover"));
const CHIP = CSS.slice(CSS.indexOf(".reply-chip {"), CSS.indexOf(".reply-chip:hover"));

test("the reply chip wears #jump-bottom's tokens — background, border, shadow, radius, height, resting colour", () => {
  for (const tok of ["var(--vscode-menu-background, var(--surface-raised))", "color: var(--dim)", "border: 1px solid var(--menu-border)",
                     "box-shadow: var(--shadow-toast)", "border-radius: var(--radius-pill)", "height: 22px"]) {
    assert.ok(JUMP.includes(tok), "jump chip has " + tok);
    assert.ok(CHIP.includes(tok), "reply chip has " + tok);
  }
  assert.match(CSS, /\.reply-chip:hover \{ border-color: var\(--accent\); color: var\(--accent\); \}/, "the same hover");
  assert.match(CSS, /\.reply-chip\[hidden\] \{ display: none; \}/, "author display:flex defeats [hidden] — the jump chip's lesson");
  assert.match(CSS, /@media \(pointer: coarse\) \{ \.reply-chip \{ height: 32px;/, "the phone half grows with the jump chip's 32px");
  assert.doesNotMatch(CHIP, /#[0-9a-fA-F]{3,6}\b/, "tokens only, no raw hex");
});

test("one type size, reused: the chip's 12px is the seek-note's, the size this bottom-of-pane chrome already wears", () => {
  assert.match(CHIP, /font: inherit; font-size: 12px;/, "the page font (a button does not inherit it) at the surface's own size");
  const seek = CSS.slice(CSS.indexOf("#seek-note {"), CSS.indexOf("#seek-note .seek-note-label"));
  assert.match(seek, /font-size: 12px;/);
  assert.equal((CHIP.match(/font-size/g) || []).length, 1, "no nested em ladder");
});

test("both themes resolve every token the chip and the ring read", () => {
  const dark = CSS.split("body.theme-light {")[0];
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  for (const tok of ["--surface-raised", "--dim", "--menu-border", "--shadow-toast", "--radius-pill", "--accent", "--st-awaiting-bg", "--cmt-hl"]) {
    assert.match(dark, new RegExp(tok.replace(/-/g, "\\-") + ":"), "dark defines " + tok);
    assert.match(light, new RegExp(tok.replace(/-/g, "\\-") + ":"), "light defines " + tok);
  }
});

// ── the fixture keeps the fixed-position story honest ─────────────────────────────────────────────
test("the ui-verify fixture mirrors the builders class for class (synthetic, notes-api)", () => {
  const fx = fs.readFileSync(path.resolve(process.cwd(), "..", "tools", "ui-verify", "fixtures", "reply-ready-chat.html"), "utf8");
  assert.match(fx, /id="reply-chips"/);
  assert.match(fx, /class="reply-chip" id="reply-above" data-act="replyjump" data-dir="above"/);
  assert.match(fx, /class="reply-chip-label">1 reply unread</);
  assert.match(fx, /mark class="cmt-hl unread hl-first hl-last"/);
  assert.match(fx, /11111111-2222-4333-8444-/, "placeholder ids");
});
