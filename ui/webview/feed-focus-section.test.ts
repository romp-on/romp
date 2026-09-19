// The FOCUSED SESSION section (T347, the user 2026-09-11, who wanted the focused session's cards on top): when a
// tab has focus in the chat pane, the feed shows that session's cards ABOVE a divider — the board's three columns,
// a miniature of the feed for one session, headed by its name — while the board below stays exactly as it is.
// OFF by default; the View menu's fourth row switches it; the kernel relays the chat's active tab to the feed as
// {type:"activeChat", id}. This file pins the wiring in feed.ts and feed.css at the source (feed.ts has no jsdom
// harness, the repo convention); the pure pick (feed-focus.ts) runs directly in feed-focus-entries.test.ts, and the
// switch's persistence (FeedViewState.focused) in feed-view-state.test.ts. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

// ── the switch: the View menu's fourth row ───────────────────────────────────────────────────────────
test("the fourth row reads 'Show focused session', a ✓ row after Group by session, and flips the persisted switch", () => {
  const groupAt = FEED.indexOf('set(2, "Group by session');
  const focusAt = FEED.indexOf('set(3, "Show focused session"');
  assert.ok(groupAt > 0 && focusAt > groupAt, "fourth, after the grouping row");
  assert.match(FEED, /if \(rows\.length !== 4\) return;/, "paintViewMenu syncs four rows");
  assert.match(FEED, /mk\(true, \(\) => \{ showFocused = !showFocused; persistViewState\(\); render\(\); \}\);/,
    "a menuitemcheckbox row (mk(true)) that flips the switch, persists and re-renders — no shared pref");
  assert.match(FEED, /set\(3, "Show focused session", \{\s*\n\s*current: showFocused,/);
  assert.doesNotMatch(FEED, /setViewPref\("focused"/, "never romp:settings: the gear and the other panes have nothing to read");
});

test("the switch is the feed's own view state under `focused`: OFF unless a blob saved it on", () => {
  assert.match(FEED, /let showFocused = false;/);
  assert.match(FEED, /showFocused = st\.focused;/, "hydrated with the rest of the view state");
  assert.match(FEED, /order: colOrder\.slice\(\), focused: showFocused,\s*\n\s*focusOrder: focusOrder\.slice\(\), focusW: \{ \.\.\.focusW \}, focusCols: \[\.\.\.collapsedFocusCols\], focusFolded \};/,
    "currentViewState carries it (and the section's own block layout and fold, T410), so persistViewState writes it");
});

// ── the event: the kernel's activeChat frame ─────────────────────────────────────────────────────────
test("the activeChat frame sets the focused sid and re-renders; nothing in the section runs on a clock", () => {
  const at = FEED.indexOf('} else if (m.type === "activeChat") {');
  assert.ok(at > 0, "the feed handles the kernel's relay of the chat pane's active tab");
  const branch = FEED.slice(at, FEED.indexOf('} else if (m.type === "hoverCards") {', at));
  assert.match(branch, /const id = typeof m\.id === "string" && m\.id \? m\.id : null;[\s\S]*?\n\s*focusedSid = id;/, "the frame's id, or null when no tab has focus (read once: the T416 pending record checks it first)");
  assert.match(branch, /if \(!showFocused\) return;\s*\n\s*if \(freezeKey \|\| tabScopeKey\) \{ focusStale = true; return; \}\s*\n\s*render\(\);/,
    "off: nothing to paint; held under the pointer: paint on the release; else the frame IS the render");
  assert.doesNotMatch(branch, /setTimeout|setInterval|requestAnimationFrame/);
  const section = FEED.slice(FEED.indexOf("function ensureFocusSection("), FEED.indexOf("// ── FLIP: animate a card FLYING"));
  assert.ok(section.includes("function removeFocusSection(): void {"), "the slice spans the whole section code");
  assert.doesNotMatch(section, /setTimeout|setInterval|requestAnimationFrame|Date\.now/, "event-based: no timer, no clock");
});

test("a tab switch that lands while a card is held under the pointer paints on the release (the hover-freeze contract)", () => {
  assert.match(FEED, /let focusStale = false;/);
  const flush = FEED.slice(FEED.indexOf("function flushFreeze(): void {"), FEED.indexOf('window.addEventListener("blur", () => { releaseTabScope();'));
  assert.match(flush, /if \(m\) applyFeedPayload\(m\);[^\n]*\n\s*else if \(focusStale\) render\(\);[^\n]*\n\s*focusStale = false;/,
    "the release renders the section when no payload was queued; a queued payload's render covers it");
});

// ── the section: its own elements, above the board, the board untouched ─────────────────────────────
test("#feed-focus sits directly before #feed-cols: head, empty line, the three columns, then the rule as its sibling", () => {
  assert.match(FEED, /sec\.id = "feed-focus";/);
  assert.match(FEED, /if \(board && \(sec\.nextSibling !== rule \|\| rule\.nextSibling !== board\)\) \{ list\.insertBefore\(sec, board\); list\.insertBefore\(rule, board\); applyColStack\(\); \}/,
    "ensure-once, the section then its rule kept right above #feed-cols across renders (the rule under the box, the user 2026-09-19); a fresh section takes the board's column order");
  for (const mint of ['el("div", "feed-focus-head")', 'el("button", "feed-focus-fold")', 'el("a", "fname")', 'el("span", "feed-focus-caret")',
                      'el("span", "feed-col-count feed-focus-count")', 'el("div", "feed-focus-empty")',
                      'el("div", "feed-cols feed-focus-cols")', 'el("hr", "feed-focus-divider")']) {
    assert.ok(FEED.includes(mint), "builds " + mint);
  }
  assert.doesNotMatch(FEED, /feed-focus-cap|textContent = "focused"/, "the small cap 'focused' is gone: the label says it (T410)");
  // T410 (the user 2026-09-14: no grip; the heads above and below the divider read the same): the section's heads
  // carry the board's chip as the drag handle, a fold caret and the count — and each block a resize gutter; all
  // section-bound. The chip also takes focus for the arrow keys.
  assert.doesNotMatch(FEED, /drag-grip|wireGripKeys|"⠿"/, "no grip anywhere: the chip is the handle, as on the board (the user's word)");
  assert.match(FEED, /el\("span", "feed-col-name fcol-chip fcol-chip-" \+ chip\); name\.textContent = label;\s*\n\s*name\.tabIndex = 0; name\.title = "Drag to reorder, or press the arrow keys"; name\.setAttribute\("aria-keyshortcuts", "ArrowLeft ArrowRight"\);\s*\n\s*const fold = el\("button", "fcol-fold"\); fold\.dataset\.label = label;/,
    "the board's chip, focusable and promising the keys, then the section's own fold caret");
  assert.match(FEED, /h\.append\(name, fold, count\);\s*\n\s*wireColDrag\(name, col, key, FOCUS_SLOTS\);[^\n]*\n\s*wireBlockKeys\(name, key\);/,
    "chip, caret, count; the chip drags with the board's mechanics, bound to the section's container, and its keys move the block");
  // a block with no cards wears col-empty (single column hides it whole; side by side its head stands), and the
  // quiet line is the no-focus state alone: nothing is said under a head (the user 2026-09-14)
  assert.match(FEED, /lists\[k\]\.parentElement\?\.classList\.toggle\("col-empty", n === 0\);/, "a block with no cards is marked, per render");
  assert.doesNotMatch(FEED, /who\.name \+ " has no cards"/, "no 'has no cards' text: a focused session with no cards shows its label and its blocks");
  assert.match(FEED, /\(sec\._empty as HTMLElement\)\.style\.display = focusedSid \? "none" : "";\s*\n\s*\(sec\._cols as HTMLElement\)\.style\.display = focusedSid && !folded \? "" : "none";/,
    "the quiet line only without a focus; the blocks whenever a session is focused and the section is open, cards or none");
  assert.match(FEED, /const gutter = el\("div", "focus-gutter"\);[^\n]*\n\s*wireFocusGutter\(gutter, key\);\s*\n\s*col\.append\(h, body, gutter\);/,
    "the resize gutter rides the block, outside the card list the reconcile owns");
  assert.match(FEED, /if \(collapsedFocusCols\.has\(key\)\) collapsedFocusCols\.delete\(key\); else collapsedFocusCols\.add\(key\);\s*\n\s*applyFocusLayout\(\);\s*\n\s*persistViewState\(\);/,
    "the caret folds the section's block under its own state, persisted");
  // in render(): the pick is taken before grouping (a folded thread below must not empty the section), the
  // section is painted before the board's reconcile, and the board's own reconcile is what it always was
  const pickAt = FEED.indexOf("const focusBuckets = showFocused ? focusedEntries(buckets, focusedSid, entrySid) : null;");
  const groupAt = FEED.indexOf("if (feedPrefs().grouped) {", pickAt);
  const callAt = FEED.indexOf("if (focusBuckets) renderFocusSection(list, focusBuckets, gate); else removeFocusSection();");
  const flipAt = FEED.indexOf("const flipFirst = needFlip ? captureCardRects(cols) : new Map<string, FlipState>();");
  const boardAt = FEED.indexOf("reconcileCol(cols.asks, buckets.asks, desired, gate);");
  assert.ok(pickAt > 0 && groupAt > pickAt && callAt > groupAt && flipAt > callAt && boardAt > flipAt,
    "pick → grouping → section → the board's FLIP capture → the board's reconcile");
  // the section sits ABOVE the board, so it must have settled before the board's First rects are read: a capture
  // taken before it would have every card below glide by the section's height change on top of its own move
  assert.ok(FEED.indexOf("const gate: GateEnv = {") < flipAt, "the gate the section needs is built before the capture");
});

test("the section's cards are SECOND elements: its own caches under 'f:' keys, the board's caches untouched", () => {
  const sec = FEED.slice(FEED.indexOf("function ensureFocusSection("), FEED.indexOf("// ── FLIP: animate a card FLYING"));
  assert.match(sec, /key = "f:a:" \+ e\.ask\.itemId;/);
  assert.match(sec, /key = "f:g:" \+ e\.group\.turnId;/);
  assert.match(sec, /card = fsAskEls\.get\(e\.ask\.itemId\) \|\| makeAskCard\(e\.ask\);\s*\n\s*card\.dataset\.key = key;/, "the builder's bare key is re-stamped with the section's");
  assert.match(sec, /card = fsGroupEls\.get\(e\.group\.turnId\) \|\| makeGroupCard\(e\.group\);\s*\n\s*card\.dataset\.key = key;/);
  assert.doesNotMatch(sec, /\baskEls\b|\bgroupEls\b/, "never the board's caches — no card below moves because of the section");
  // the same update gate as the board (feed-card-gate.ts)
  assert.match(sec, /const ik = cardInputsKey\(e\.ask, gate\);\s*\n\s*if \(cardNeedsUpdate\(card as any, e\.ask, ik\)\) \{ updateAskCard\(card, e\.ask\); \(card as any\)\._ik = ik; \}/);
  assert.match(sec, /function removeFocusSection\(\): void \{\s*\n\s*const sec = document\.getElementById\("feed-focus"\) as any;\s*\n\s*if \(sec\) \{ \(sec\._rule as HTMLElement \| undefined\)\?\.remove\(\); sec\.remove\(\); \}[^\n]*\n\s*fsAskEls\.clear\(\); fsGroupEls\.clear\(\);/,
    "the section leaves with its rule, which is its sibling since the divider moved under the box (the user 2026-09-19)");
  // the rule is built with the section and placed after it, before the board, every render
  assert.match(sec, /sec\.append\(head, empty, cols\);\s*\n\s*\(sec as any\)\._rule = rule;/, "the rule is not a child of the section");
  assert.match(sec, /if \(board && \(sec\.nextSibling !== rule \|\| rule\.nextSibling !== board\)\) \{ list\.insertBefore\(sec, board\); list\.insertBefore\(rule, board\); applyColStack\(\); \}/,
    "section, rule, board: re-placed together when either is out of order");
  // both copies of a card light together, hold the same latches, and tick the same ages
  assert.match(FEED, /for \(const \[id, card\] of fsAskEls\) card\.classList\.toggle\("focused", id === eff\);/);
  assert.equal((FEED.match(/for \(const card of \[\.\.\.askEls\.values\(\), \.\.\.fsAskEls\.values\(\)\]\)/g) || []).length, 2, "rearmLatches and livePass walk both");
});

test("the board's own column lookups are scoped to #feed-cols now that the section carries the same classes", () => {
  assert.match(FEED, /document\.querySelector<HTMLElement>\("#feed-cols \.feed-col\.col-" \+ key\)/, "applyColStack folds the board's column");
  assert.match(FEED, /const twin = document\.querySelector<HTMLElement>\("#feed-focus \.feed-col\.col-" \+ key\);/, "…the section's twin is applyFocusLayout's to paint (its own order, or the board's while it follows), never the board's write");
  assert.match(FEED, /else col\.style\.removeProperty\("--col-order"\);/, "the board's own statement stands as feed-col-fold.test.ts pins it");
  assert.match(FEED, /document\.querySelector<HTMLElement>\("#feed-cols \.feed-col\.col-" \+ k\)/, "the board drag's element lookup reads the board (BOARD_SLOTS.col)");
  assert.match(FEED, /const oc = slots\.col\(other\);/, "the drag walks its OWN container's elements: the board's columns for its chips, the section's blocks for theirs (T410)");
  // review round two, medium 1: the slot math walks VISIBLE blocks only (a hidden block's rect is all zeros)
  assert.match(FEED, /function visibleKeys\(order: string\[\], colOf: \(k: string\) => HTMLElement \| null\): string\[\] \{/, "the visible-blocks filter");
  assert.match(FEED, /return r\.width > 0 \|\| r\.height > 0;/, "display: none is the zero rect; a folded block still has its head");
  assert.match(FEED, /const visible = visibleKeys\(order, slots\.col\);\s*\n\s*const from = visible\.indexOf\(key\);/, "the drag's from-slot is the block's place among the visible ones");
  assert.match(FEED, /for \(const other of visible\) \{/, "…and its midpoint walk visits the visible blocks alone");
  // review round two: the pointerdown's default stands, so a focusable chip is focused NATIVELY (pointer-initiated: no
  // :focus-visible ring, no scroll-into-view); a drag ends with the chip blurred, a plain click keeps its focus
  const drag = FEED.slice(FEED.indexOf("function wireColDrag("), FEED.indexOf("function ensureCols("));
  assert.doesNotMatch(drag, /down\.preventDefault\(\)|chip\.focus\(\)/, "no prevented pointerdown and no scripted focus on the drag handle");
  assert.match(drag, /down\.stopPropagation\(\);\s*\n\s*chip\.setPointerCapture\(down\.pointerId\);/, "capture, without preventing the default");
  assert.match(drag, /let dragged = false;/);
  assert.match(FEED, /^const DRAG_SLOP_PX = 4;/m, "a drag is a pointer that moved past a small slop; under it a slipping click stays a click (round three)");
  assert.match(drag, /col\.style\.transform = translate\(pos\(ev\) - start - slotShift\);\s*\n\s*if \(Math\.abs\(pos\(ev\) - start\) > DRAG_SLOP_PX\) dragged = true;/, "a pointer past the slop is a drag");
  assert.match(drag, /if \(down\.button !== 0\) return;/, "the primary button (a touch, a pen) arms the drag; a right press arms nothing");
  assert.match(drag, /if \(dragged && chip\.tabIndex >= 0 && document\.activeElement === chip\) chip\.blur\(\);/, "a drag gives the focus back; the arrow keys return to the card cursor");
  assert.match(CSS, /\.feed-col-head \.fcol-chip \{ cursor: grab; touch-action: none; user-select: none; \}/, "selection and touch are the CSS's to hold, so the default may stand");
  assert.match(FEED, /applyOrderFlip\(placeVisible\(order, visible, nv\)\);/, "the re-slot keeps hidden blocks in their relative places");
  assert.match(FEED, /col: \(k\) => document\.querySelector<HTMLElement>\("#feed-focus \.feed-col\.col-" \+ k\),/, "FOCUS_SLOTS reads the section");
  assert.match(FEED, /put\(document\.querySelector\("#feed-cols \.feed-col\.col-" \+ key \+ " \.feed-col-head"\), d\.cols\[key\]\);/, "the freeze badges land on the board's heads");
  assert.doesNotMatch(FEED, /querySelector<HTMLElement>\("\.feed-col\.col-"/, "no bare column query survives to land on the section's copy first");
  // a copy's key reads as the card's own identity for the hover-freeze heal (a hovered copy holds the gate)
  assert.match(FEED, /if \(key\.startsWith\("f:"\)\) key = key\.slice\(2\);/);
  // …and the keyboard cursor walks the BOARD's cards: a walk over both would interleave a copy with its card below
  assert.match(FEED, /querySelectorAll<HTMLElement>\("#feed-cols \.fitem:not\(\.dismissing\)"\)/, "kbCardEls reads #feed-cols");
  assert.doesNotMatch(FEED, /querySelectorAll<HTMLElement>\("\.feed-cols \.fitem:not\(\.dismissing\)"\)/);
});

test("the head: the session's dot and name, its identity colour, opening the session like a run header does", () => {
  assert.match(FEED, /nm\.replaceChildren\(\.\.\.hostNameNodes\(who\.name, sid\)\)/, "a remote session's host: prefix stays quiet metadata");
  assert.match(FEED, /nm\.style\.color = who\.color \? who\.color\.bg : "";/);
  assert.match(FEED, /nm\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openOrReviveSession\(sid, who\.live, who\.name\); \};/);
  assert.match(FEED, /setWorkDot\(nm, dotFor\(who\.name\)\);/);
  assert.match(FEED, /const s = sessionsMeta\.find\(\(x\) => x\.sid === sid\);\s*\n\s*const a = asks\.find\(\(x\) => x\.sid === sid\);/,
    "the name from this pane's session list, else a card, else the kernel's stub");
});

test("the two quiet states, only while the switch is on: no tab focused; a focused session with no cards", () => {
  assert.match(FEED, /setText\(empty, "No session is focused in the chat"\);/);
  assert.doesNotMatch(FEED, /who\.name \+ " has no cards"/, "the second quiet state is gone (the user 2026-09-14): a focused session with no cards shows its label and its blocks");
  assert.match(FEED, /head\.style\.display = sid \? "" : "none";\s*\n\s*sec\._total = total;\s*\n\s*paintFocusFold\(\);/,
    "no sid: the line stands in for the head; the blocks' and the line's displays are the fold's to paint (T410)");
  assert.match(FEED, /const folded = !!focusedSid && focusFolded;\s*\n\s*sec\.classList\.toggle\("folded", folded\);/, "the fold is a focused session's alone");
  assert.match(FEED, /\(sec\._empty as HTMLElement\)\.style\.display = focusedSid \? "none" : "";\s*\n\s*\(sec\._cols as HTMLElement\)\.style\.display = focusedSid && !folded \? "" : "none";/,
    "no sid: the line, whatever the fold says; a focused session: the blocks (cards or none) unless folded to the label");
  assert.match(FEED, /removeFocusSection\(\);   \/\/ an empty board is the wordmark alone/, "an empty board shows the wordmark, not the section");
});

test("Clear, its 180 ms finish and Undo resolve by ITEM across both copies, never by one element (the review of T347)", () => {
  // Clear on the section's copy used to run the board builder's handler, whose finish guarded on the BOARD's
  // element (askEls.get(id) === card): the copy stayed, dropDismissed never ran, and Undo stripped .dismissing
  // from the board's element alone. Every element carrying the item dismisses and finalizes together.
  assert.match(FEED, /function cardTwins\(itemId: string\): HTMLElement\[\] \{[\s\S]*?askEls\.get\(itemId\)[\s\S]*?fsAskEls\.get\(itemId\)/, "the board's element and the section's copy");
  assert.match(FEED, /function groupTwins\(turnId: string\): HTMLElement\[\] \{[\s\S]*?groupEls\.get\(turnId\)[\s\S]*?fsGroupEls\.get\(turnId\)/);
  assert.match(FEED, /for \(const c of cardTwins\(it\.itemId\)\) c\.classList\.add\("dismissing"\);\s*\n\s*vscodeApi\?\.postMessage\(\{ type: "askClear"/, "the ask card's Clear marks both copies");
  assert.match(FEED, /const twins = cardTwins\(it\.itemId\)\.filter\(\(c\) => c\.classList\.contains\("dismissing"\)\);[\s\S]*?if \(fsAskEls\.get\(it\.itemId\) === c\) fsAskEls\.delete\(it\.itemId\); \}\s*\n\s*dropDismissed\(\[it\.itemId\]\);/, "the finish removes every copy still dismissing and forgets both caches");
  assert.doesNotMatch(FEED, /if \(askEls\.get\(it\.itemId\) === card && card\.classList\.contains\("dismissing"\)\)/, "the element-identity guard is gone from the ask card's finish");
  assert.match(FEED, /for \(const c of groupTwins\(cur\.turnId\)\) c\.classList\.add\("dismissing"\);/, "…and the group card's");
  assert.match(FEED, /for \(const c of cardTwins\(it\.itemId\)\) c\.classList\.remove\("dismissing"\);/, "Undo restores both copies");
  assert.match(FEED, /const f = fsAskEls\.get\(m\.itemId\);[\s\S]*?leaving\.push\(\[f, \(\) => fsAskEls\.get\(m\.itemId\) === f, \(\) => fsAskEls\.delete\(m\.itemId\)\]\);/, "the session-wide Clear takes the copies along");
  assert.match(FEED, /for \(const \[tid, g\] of Array\.from\(fsGroupEls\)\) \{/, "…group copies too");
});

test("cross-pane hover and reveal reach the copies: card-key strips the section's prefix, the keyboard cursor likewise", () => {
  const CK = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "card-key.ts"), "utf8");
  assert.match(CK, /const FOCUS = \/\^f:\/;/);
  assert.match(CK, /const board = domKey\.replace\(FOCUS, ""\);/);
  assert.match(FEED, /if \(key\.startsWith\("f:"\)\) key = key\.slice\(2\);/, "kbHoverId strips it the same way");
});

test("the three follow-ups after the review: Tab keeps the copy, Clear from a copy dresses the board's header, a pill repaints both copies", () => {
  // (a) the keyboard scope remembers which twin it holds and re-finds that one; Tab from a hovered copy lands in the copy
  assert.match(FEED, /let tabScopeCopy = false;/);
  assert.match(FEED, /tabScopeCopy = isFocusCopy\(card\);/, "set where the scope is taken");
  assert.equal((FEED.match(/cardElByKey\(tabScopeKey, tabScopeCopy\)/g) || []).length, 3, "every reader of the scope re-finds the same twin (Tab, Enter, the render-tail restore)");
  assert.match(FEED, /return document\.querySelector<HTMLElement>\('\[data-key="' \+ \(copy \? "f:" \+ k : k\) \+ '"\]'\);/);
  // (b) Clear from the copy still gives the board run's header its one-motion exit (the section has no run headers)
  assert.match(FEED, /dressHeaderIfLast\(askEls\.get\(it\.itemId\) \?\? card, it\.sid\);/);
  // (c) a section pill picked on either copy repaints both: the disclosure is the card's
  assert.match(FEED, /const twins = cardTwins\(id\);\s*\n\s*if \(twins\.length\) \{ for \(const c of twins\) applySections\(c as any, \(c as any\)\._it \?\? it, distillShown\); \}/);
});

// ── feed.css: the section's rules, through the variables ─────────────────────────────────────────────
test("feed.css: #feed-focus, the head, the caption, the rule and the empty line exist, var() only", () => {
  assert.match(CSS, /#feed-focus \{ display: flex; flex-direction: column; gap: 8px; background: var\(--accent-tint\); border-radius: 8px; padding: 6px 8px; \}/,
    "the whole region on the very faint accent tint, a small radius, even padding around the cards (the user 2026-09-18; the divider outside since 2026-09-19)");
  assert.match(CSS, /\.feed-focus-cols \.feed-col-head \{ position: static; background: transparent; \}/,
    "a column head inside the box paints no ground, so the tint runs across the row (the user 2026-09-19)");
  assert.match(CSS, /--accent-tint: rgba\(156, 210, 255, 0\.04\);/, "the tint token beside the wash, a third of its alpha, in the dark block");
  assert.match(CSS, /--accent-tint: rgba\(194, 65, 12, 0\.04\);/, "…and re-inked in the light block");
  assert.doesNotMatch(CSS, /#feed-focus \{[^}]*rgba\(/, "the region's ground is the token, never a literal colour");
  assert.match(CSS, /\.feed-focus-head \{ display: flex; flex-wrap: nowrap;[^}]*font-size: 0\.72em; font-weight: 600; cursor: pointer; \}/,
    "the label (T410): ONE line (nowrap, so the name's clamp can act: review round two), the board's column heads' size, the session headers' weight, no new size; the whole row folds on click");
  assert.match(CSS, /\.feed-focus-head \.fname \{ font-size: calc\(1em \/ 0\.72\); font-weight: 600;/, "the name as a session name below: the headers' size, bold (its identity colour is set inline)");
  assert.match(CSS, /\.feed-focus-fold \{[^}]*font: inherit; color: var\(--dim\);[^}]*\}/, "the label text: a button in the head's font, dim like the column heads");
  assert.match(CSS, /\.feed-focus-caret \{[^}]*font-size: calc\(1em \/ 0\.72\); font-weight: 400; line-height: 1;/, "the caret at the block carets' compensated size");
  assert.doesNotMatch(CSS, /feed-focus-cap/, "the cap's rule went with the cap");
  assert.match(CSS, /\.feed-focus-head \.fname \{[^}]*flex: 0 1 auto; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;/,
    "the label's name clamps like a card's (.fask-id .fname), so a long name shrinks and the caret keeps the end of the line (T410 review)");
  assert.doesNotMatch(CSS, /\.feed-focus-cols \.feed-col-head \.fcol-chip \{ cursor: default; \}|drag-grip/, "the section's chips drag as the board's do (the base grab cursor stands; no grip rule)");
  assert.match(CSS, /\.feed-focus-cols \.fcol-chip:focus-visible \{ outline: 2px solid var\(--accent\); outline-offset: 2px; \}/, "a focused chip wears the accent ring: the keyboard's handle");
  assert.match(CSS, /\.feed-focus-head \.fname \{ font-size: calc\(1em \/ 0\.72\); font-weight: 600;/, "the name at the session headers' size (the feed's base, back up from the label's 0.72em) and weight (the user 2026-09-14)");
  assert.match(CSS, /\.focus-gutter \{ display: none; \}[\s\S]{0,800}\.feed-focus-cols \.feed-col\.col-empty \{ display: none; \}/,
    "single column (the stacked query): a block with no cards hides whole, chip and all; the rule lives under the query alone");
  assert.equal((CSS.match(/\.feed-focus-cols \.feed-col\.col-empty \{ display: none; \}/g) || []).length, 1, "…and nowhere else, so side by side the heads stand");
  assert.match(CSS, /\.feed-focus-divider \{ border: 0; border-top: 2px solid var\(--rule-strong\); margin: 8px 0 4px; \}/,
    "T410: a 2px rule in --rule-strong, a step up from the hairline");
  assert.ok(/--rule-strong:\s*rgba\(255, 255, 255, 0\.22\)/.test(CSS) && /--rule-strong:\s*rgba\(0, 0, 0, 0\.22\)/.test(CSS), "the token is defined in both themes");
  assert.match(CSS, /\.feed-focus-empty \{ color: var\(--dim\); font-size: 0\.82em; \}/);
  // every colour in the section's declarations is a var(); the one literal is that var()'s fallback
  const block = CSS.slice(CSS.indexOf("#feed-focus {"), CSS.indexOf(".feed-focus-empty {") + ".feed-focus-empty { color: var(--dim); font-size: 0.82em; }".length);
  const decls = (block.match(/\{[^}]*\}/g) || []).join("\n").replace(/var\([^)]*\)/g, "");
  assert.doesNotMatch(decls, /#[0-9a-fA-F]{3,8}\b|rgba?\(/, "no hex or rgb outside a var() fallback");
  assert.ok(/--menu-border\s*:/.test(CSS) && /--dim\s*:/.test(CSS), "the variables it reads are defined in this sheet (both themes)");
});

// ── T410 (the user 2026-09-13 / 2026-09-14): the LABEL, the chip's keys, the gutter floor, the freeze pair ──────
test("the label reads 'Current session: <name>' and folds the whole section; the name keeps its click and its title", () => {
  assert.match(FEED, /const fold = el\("button", "feed-focus-fold"\) as HTMLButtonElement; fold\.type = "button"; fold\.textContent = "Current session:";/,
    "the label text is a button: the section's fold control");
  assert.match(FEED, /fold\.setAttribute\("aria-expanded", "true"\); fold\.setAttribute\("aria-controls", "feed-focus-cols"\);/, "aria-expanded and what it controls");
  assert.match(FEED, /\(sec\._fold as HTMLElement\)\.setAttribute\("aria-label", "Current session: " \+ who\.name\);/, "the accessible name carries the session's name");
  assert.match(FEED, /const nm = el\("a", "fname"\); nm\.title = "open this session";/, "the name link and its hover title stay");
  assert.match(FEED, /nm\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openOrReviveSession\(sid, who\.live, who\.name\); \};/, "…and its click: open, or offer to revive");
  assert.match(FEED, /const caret = el\("span", "feed-focus-caret"\); caret\.textContent = "▾"; caret\.setAttribute\("aria-hidden", "true"\);/, "the block carets' glyph, decorative (the button carries the state)");
  assert.match(FEED, /head\.append\(fold, nm, ncount, caret\);/, "label, name, count, caret — the caret at the END of the label line (the user's word), the folded count between the name and it");
  assert.match(FEED, /head\.addEventListener\("click", \(ev\) => \{\s*\n\s*if \(\(ev\.target as HTMLElement\)\.closest\("\.fname"\)\) return;[^\n]*\n\s*focusFolded = !focusFolded;\s*\n\s*paintFocusFold\(\);\s*\n\s*persistViewState\(\);/,
    "a click anywhere on the row but the name flips focusFolded, paints and persists");
  assert.match(FEED, /\(sec\._caret as HTMLElement\)\.textContent = folded \? "▸" : "▾";\s*\n\s*\(sec\._fold as HTMLElement\)\.setAttribute\("aria-expanded", String\(!folded\)\);/, "the caret and aria-expanded read the state");
  assert.match(FEED, /setText\(count, folded && total \? String\(total\) : ""\);[^\n]*\n\s*count\.style\.display = folded && total \? "" : "none";/, "folded, the focused session's card count after the name (a number only when there are cards, the board's rule)");
  assert.match(FEED, /let focusFolded = false;/);
  assert.match(FEED, /focusFolded = st\.focusFolded;/, "hydrated with the rest");
  const VS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed-view-state.ts"), "utf8");
  assert.match(VS, /focusFolded: boolean;/);
  assert.match(VS, /focusFolded: o\.focusFolded === true/, "only the literal true folds");
});

test("the gutter's floor is 0.35 of a share, the pair's sum preserved", () => {
  assert.match(FEED, /const MIN_W = 0\.35;/);
  assert.match(FEED, /const w = Math\.min\(sum - MIN_W, Math\.max\(MIN_W, w0 \+ \(ev\.clientX - startX\) \* sum \/ px\)\);\s*\n\s*focusW = \{ \.\.\.focusW, \[key\]: w, \[next\]: sum - w \};/,
    "clamped between the floor and the pair's sum less the floor; the neighbour takes the rest");
});

test("the chip's arrow keys move the block one slot within the section, from the order on screen", () => {
  const fn = FEED.slice(FEED.indexOf("function wireBlockKeys("), FEED.indexOf("// Drag a section by its CATEGORY CHIP"));
  assert.match(fn, /const delta = e\.key === "ArrowLeft" \|\| e\.key === "ArrowUp" \? -1 : e\.key === "ArrowRight" \|\| e\.key === "ArrowDown" \? 1 : 0;/);
  assert.match(fn, /const fallback = FOCUS_SLOTS\.fallback\(vertical \? STACK_DEFAULT : ROW_DEFAULT\);\s*\n\s*const hadCustom = cur\.length === 3;\s*\n\s*const order = \(hadCustom \? cur : fallback\)\.slice\(\);/, "the section's own order, else what it follows — as the drag seeds itself");
  assert.match(fn, /e\.stopPropagation\(\);/, "the chip's key is the chip's alone: the card cursor's arrow keys must not also fire");
  // the no-trace rule per PROVENANCE (review round two): a key sequence out of a following state can be walked back to
  // nothing stored; an order pinned by drag is never cleared by a key press that lands on the fallback
  assert.match(fn, /if \(next\.join\(\) === fallback\.join\(\) && focusOrderByKeys\) \{\s*\n\s*FOCUS_SLOTS\.set\(\[\]\);\s*\n\s*focusOrderByKeys = false;/,
    "back on the followed arrangement: cleared on the flag alone (from a following state the order IS the fallback and a one-slot move always leaves it; round three dropped the unreachable disjunct)");
  assert.match(fn, /FOCUS_SLOTS\.set\(next\);\s*\n\s*if \(!hadCustom\) focusOrderByKeys = true;/, "a key press out of a following state mints an order the keys may clear again");
  assert.match(FEED, /^let focusOrderByKeys = false;/m, "the provenance flag beside the section's order; not persisted (a reload reads a stored order as pinned)");
  // round three: the flag drops only when a GESTURE changed a stored order, at the gesture's end, for either handle
  assert.match(FEED, /const startOrder = slots\.get\(\)\.slice\(\);/, "the press records what it started from");
  assert.match(FEED, /if \(slots\.get\(\)\.join\(\) !== startOrder\.join\(\)\) focusOrderByKeys = false;/, "…and the flag drops only when the release committed a different order (a click, or a there-and-back drag, keeps a key-minted order walkable)");
  assert.doesNotMatch(FEED, /if \(slots === FOCUS_SLOTS\) focusOrderByKeys = false;|colOrder = o; focusOrderByKeys = false;/, "no drop on every pointerup, none inside BOARD_SLOTS.set (a board drag re-slots mid-drag and may end where it started)");
  assert.match(fn, /const visible = visibleKeys\(order, FOCUS_SLOTS\.col\);\s*\n\s*const from = visible\.indexOf\(key\), to = from \+ delta;\s*\n\s*if \(from < 0 \|\| to < 0 \|\| to >= visible\.length\) return;/,
    "one slot among the blocks ON SCREEN: a hidden neighbour is skipped, never swapped behind (review round two)");
  assert.match(fn, /const next = placeVisible\(order, visible, nv\);/, "hidden blocks keep their relative places in the stored order");
  assert.match(fn, /if \(!hadCustom\) focusOrderByKeys = true;[^\n]*\n\s*\}\s*\n\s*persistViewState\(\);/, "written to the section's order and persisted at once");
  assert.doesNotMatch(fn, /BOARD_SLOTS|colOrder =/, "never the board's order");
});

test("the hover-freeze hold is the pair (key, which twin): leaving one twin never releases a hold or a scope taken on the other", () => {
  assert.match(FEED, /let freezeCopy = false;/);
  assert.match(FEED, /function freezeEnter\(key: string, copy = false\): void \{ freezeKey = key; freezeCopy = copy; \}/);
  const leave = FEED.slice(FEED.indexOf("function freezeLeave("), FEED.indexOf("let flushQueued = false;"));
  assert.match(leave, /if \(tabScopeKey === key && tabScopeCopy === copy\) releaseTabScope\(\);/, "the keyboard scope releases only for the twin it holds");
  assert.match(leave, /if \(freezeKey !== key \|\| freezeCopy !== copy\) return;/, "…and so does the hold");
  assert.equal((FEED.match(/freezeEnter\((it\.itemId|fkey), isFocusCopy\(card\)\)/g) || []).length, 2, "both card builders say which twin enters");
  assert.equal((FEED.match(/freezeLeave\((it\.itemId|fkey), isFocusCopy\(card\)\)/g) || []).length, 2, "…and which leaves");
  assert.match(FEED, /freezeCopy = isFocusCopy\(hov\);/, "the render-time re-derivation records the twin under the pointer");
  assert.match(FEED, /const selfCard = selfKey \? cardElByKey\(selfKey, freezeKey \? freezeCopy : tabScopeCopy\) : null;/, "paintFreezeBadges resolves the HELD twin's element for the self-note");
});
