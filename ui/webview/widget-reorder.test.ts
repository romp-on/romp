// REORDER, the divider and the previews for both widget sections (the user's additions to T409, 2026-09-13). The order
// rules execute in tab-widgets.test.ts (widgetSlot, tabListOrder, moveId); the gear's wiring has no DOM harness, so it
// is pinned at source: one grip per widget row with an accessible name, pointer events with one capture per drag and
// Escape cancelling, the arrow keys on the focused grip through the same moveId rule, the order stored WHOLE (the
// divider's id included) by one writer, the Tab widgets divider as a separator with the name's place in subtle text
// (never a fake name), and a preview under each section's rows drawn by the surfaces' own composers over the demo
// records.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const W = path.resolve(process.cwd(), "..", "ui", "webview");
const GEAR = fs.readFileSync(path.join(W, "gear.js"), "utf8");
const GEAR_CSS = fs.readFileSync(path.join(W, "gear.css"), "utf8");
const SECTION = GEAR.slice(GEAR.indexOf("  function widgetSection(cfg) {"), GEAR.indexOf("  // the tab widgets: settings.tabWidgets with tabCtx as the mirror"));

test("every widget row carries a grip with an accessible name; the grip is the drag handle and the keyboard handle", () => {
  assert.match(SECTION, /var grip = document\.createElement\('button'\); grip\.type = 'button'; grip\.className = 'rs-grip'; grip\.textContent = '⠿';/, "the six-dot grip glyph");
  assert.match(SECTION, /grip\.setAttribute\('aria-label', 'Drag to reorder: ' \+ w\.label\); grip\.title = 'Drag to reorder, or press the arrow keys';/);
  assert.match(SECTION, /row\.appendChild\(grip\); row\.appendChild\(demo\); row\.appendChild\(name\); row\.appendChild\(sw\); row\.appendChild\(opts\);/, "the grip leads the row");
  assert.match(SECTION, /wireGrip\(grip, row, w\.id\);/);
});

test("the drag: pointer events heard on the document (a capture on the grip would end with the row's first move), the row moved live past the midpoints of the others, the drop stored whole, Escape and a cancel restore", () => {
  assert.match(SECTION, /grip\.addEventListener\('pointerdown', function \(e\) \{\s*\n\s*if \(e\.button !== 0\) return;/, "the primary button only");
  assert.doesNotMatch(SECTION, /setPointerCapture/, "no capture on the grip: re-inserting the row releases it and the drag stops after one step (the lab caught it)");
  assert.match(SECTION, /document\.addEventListener\('pointermove', move\); document\.addEventListener\('pointerup', end\); document\.addEventListener\('pointercancel', cancel\); document\.addEventListener\('keydown', esc, true\);/, "mouse, pen and touch are one road: pointer events, on the document for the drag's life; Escape heard in the capture phase");
  assert.doesNotMatch(SECTION, /if \(e\.button !== 0\) return;\s*\n\s*e\.preventDefault\(\);/, "no preventDefault on the grip's pointerdown: the grip takes the focus, so the frame hears Escape and the arrow keys");
  assert.match(SECTION, /if \(y > b\.top \+ b\.height \/ 2\) after = r;/, "the midpoint rule");
  assert.match(SECTION, /if \(!keepsGroup\(tentative, before\)\) \{ blocked = true; return; \}[^\n]*\n\s*cfg\.host\.insertBefore\(row, wantBefore\);/, "the row moves live, never rebuilt, and never out of its group");
  assert.match(SECTION, /var now = currentList\(\);\s*\n\s*if \(blocked && now\.join\(\) === before\.join\(\)\) \{ nudge\(row\); refused\(id\); \}\s*\n\s*if \(now\.join\(\) !== before\.join\(\)\) commit\(now, id\); else paint\(\);/, "the drop commits the visual order whole; a drop in place writes nothing; a refused move nudges");
  // round two: only the drag's own pointer moves, ends or cancels it (a second finger's release must not commit the drag)
  assert.match(SECTION, /var pid = e\.pointerId, before = currentList\(\), blocked = false;/);
  assert.match(SECTION, /var move = function \(ev\) \{\s*\n\s*if \(ev\.pointerId !== pid\) return;/);
  assert.match(SECTION, /var end = function \(ev\) \{\s*\n\s*if \(ev && ev\.pointerId !== undefined && ev\.pointerId !== pid\) return;/);
  assert.match(SECTION, /var esc = function \(ev\) \{ if \(ev\.key === 'Escape'\) \{ ev\.preventDefault\(\); ev\.stopPropagation\(\); placeRows\(before\); end\(\); \} \};/, "Escape restores the order, ends the drag and goes no further (the panel stays open)");
  assert.match(SECTION, /var cancel = function \(ev\) \{ if \(ev\.pointerId !== pid\) return; placeRows\(before\); end\(ev\); \};/, "a pointercancel restores too (the drag's own pointer's)");
  // round two: the guard compares the RUNS of groups against the list the move started from, so a group of one row
  // cannot walk into the other slot (the moved row's own contiguity proved nothing)
  assert.match(SECTION, /function keepsGroup\(list, base\) \{ return !cfg\.group \|\| runs\(list\) === runs\(base\); \}/);
  assert.match(SECTION, /document\.removeEventListener\('pointermove', move\); document\.removeEventListener\('pointerup', end\); document\.removeEventListener\('pointercancel', cancel\); document\.removeEventListener\('keydown', esc, true\);/, "every listener a drag adds, it removes");
  assert.match(GEAR_CSS, /#rsettings \.rs-grip \{[^}]*cursor: grab; touch-action: none;/, "touch-action none, so a finger drags the row and not the page");
  // the shell wires its own Escape handler into the settings frame ahead of the drag's, and asks the gear's close hook: a drag in
  // flight answers no there, one Escape level at a time (the lab caught the panel closing under Escape mid-drag)
  assert.match(GEAR, /var widgetDrag = false;/);
  assert.match(SECTION, /row\.classList\.add\('rs-dragging'\); widgetDrag = true;/);
  assert.match(SECTION, /row\.classList\.remove\('rs-dragging'\); dragAborts = dragAborts\.filter/);
  assert.match(GEAR, /window\.__rompSettingsClose = function \(\) \{ if \(raBack && !raBack\.hidden\) \{ raHide\(\); return true; \} if \(p\.hidden \|\| \(lgM && !lgM\.hidden\) \|\| openHousePick \|\| widgetDrag\) return false; closeSettings\(\); return true; \};/);
  // the click the release synthesizes is the drag's: swallowed once in the capture phase, for the release's own POINTER click
  // alone (a keyboard's click has detail 0 and no pointer type, round two: the next Space on a switch lands), disarmed by
  // that click, by the next press or key, or one frame on; an Escape-ended drag arms it at the release still to come
  assert.match(SECTION, /var swallow = function \(ce\) \{ if \(ce\.detail === 0 && !ce\.pointerType\) return; ce\.stopImmediatePropagation\(\); ce\.preventDefault\(\); disarm\(\); \};/, "immediate: the click targets the document, where the panel's click-outside listener sits beside the swallow; a keyboard click passes");
  assert.match(SECTION, /document\.addEventListener\('click', swallow, true\); document\.addEventListener\('pointerdown', disarm, true\); document\.addEventListener\('keydown', disarm, true\);\s*\n\s*requestAnimationFrame\(disarm\);/);
  assert.match(SECTION, /if \(ev && ev\.type === 'pointerup'\) armSwallow\(\);\s*\n\s*else if \(!ev\) \{/, "only an Escape-ended drag waits for the release still to come; a cancel arms nothing (round three, the medium: the lab's ledger holds the cancel path at zero)");
  assert.match(SECTION, /var lateUp = function \(up\) \{ if \(up\.pointerId !== pid\) return; document\.removeEventListener\('pointerup', lateUp, true\); document\.removeEventListener\('pointercancel', lateUp, true\); if \(up\.type === 'pointerup'\) armSwallow\(\); \};/);
  assert.match(SECTION, /document\.addEventListener\('pointerup', lateUp, true\); document\.addEventListener\('pointercancel', lateUp, true\);/, "the late listener leaves with its pointer, released or cancelled");
  // the WHOLE pointerdown handler, comments stripped: a flag re-declared anywhere in it (the reviewer's executed counterexample,
  // on its own line, passed the one-line anchor) is caught, and a comment carrying the word is not (round three, low 5; the
  // migration read's low 1)
  const HANDLER = SECTION.slice(SECTION.indexOf("grip.addEventListener('pointerdown', function (e) {"), SECTION.indexOf("    function build() {")).replace(/\/\/[^\n]*/g, "");
  assert.ok(HANDLER.length > 2000 && HANDLER.length < 12000, "the handler slice located (a sanity bound)");
  assert.doesNotMatch(HANDLER, /\breleased\b/, "no dead variable anywhere in the drag handler");
  // the panel closing under a held pointer ends the drag (part two's third read): one hook, set at the press, cleared at the
  // end, called first by closeSettings; the lab's ledger reads zero after a scripted close
  // every drag in flight has its teardown in one list; whatever hides the card ends them all (the migration read's low 2: the
  // single slot tore down only the last drag pressed, and the Token usage opener hid the card with a bare write)
  assert.match(GEAR, /var dragAborts = \[\];/);
  assert.match(GEAR, /function endDrags\(\) \{ dragAborts\.slice\(\)\.forEach\(function \(f\) \{ f\(\); \}\); \}/, "a copy walked: each teardown removes itself from the list");
  assert.match(SECTION, /var abort = function \(\) \{ placeRows\(before\); end\(\{ type: 'abort', pointerId: pid \}\); \};\s*\n\s*dragAborts\.push\(abort\);/, "the teardown is a cancel's: rows back, listeners off, and the abort type arms nothing");
  assert.match(SECTION, /dragAborts = dragAborts\.filter\(function \(f\) \{ return f !== abort; \}\); widgetDrag = dragAborts\.length > 0;/, "removed at every ending; the flag the shell's Escape asks follows the list");
  assert.match(GEAR, /function closeSettings\(\) \{ endDrags\(\);/);
  assert.match(GEAR, /raOpen\.onclick = function \(e\) \{ e\.stopPropagation\(\); endDrags\(\); raBack\.hidden = false; p\.hidden = true;/, "the Token usage opener ends the drags before it hides the card");
  const HIDES = (GEAR.match(/p\.hidden = true/g) || []).length;
  assert.equal(HIDES, 2, "two places hide the card (closeSettings and the Token usage opener), and both end the drags first");
});

test("the keyboard road: ArrowUp and ArrowDown on the focused grip move the row one place through the same moveId rule, and the grip keeps the focus", () => {
  assert.match(SECTION, /grip\.addEventListener\('keydown', function \(e\) \{\s*\n\s*if \(e\.key !== 'ArrowUp' && e\.key !== 'ArrowDown'\) return;\s*\n\s*e\.preventDefault\(\);/);
  assert.match(SECTION, /var list = currentList\(\), i = list\.indexOf\(id\), to = e\.key === 'ArrowUp' \? i - 1 : i \+ 1;\s*\n\s*if \(i < 0 \|\| to < 0 \|\| to >= list\.length\) return;\s*\n\s*var next = WP\.moveId\(list, id, to\);\s*\n\s*if \(!keepsGroup\(next, list\)\) \{ nudge\(row\); refused\(id\); return; \}\s*\n\s*commit\(next, id\);\s*\n\s*grip\.focus\(\);/, "a key that would leave the group nudges instead");
  assert.match(GEAR, /var WP = require\('\.\/widget-prefs\.ts'\);/);
});

test("the order is stored whole by one writer, the divider's id among the tab widgets' ids; the rows follow the stored order on every paint without a rebuild", () => {
  assert.match(SECTION, /function commit\(list, movedId\) \{ var prefs = cfg\.prefs\(load\(\)\); prefs\.order = list; cfg\.save\(prefs\); if \(movedId\) announce\(list, movedId\); \}/);
  assert.equal((SECTION.match(/prefs\.order = /g) || []).length, 1, "one writer of the order");
  assert.match(SECTION, /function currentList\(\) \{ return Array\.from\(cfg\.host\.children\)\.map\(idOf\)\.filter\(Boolean\); \}/, "the visual list reads widgets and the divider alike");
  // round two, the regression: placeRows moves only a row out of place (appendChild re-inserts a node already in place and
  // blurs the focused switch inside it); a focus a move did take is given back
  assert.match(SECTION, /var want = prev \? prev\.nextSibling : cfg\.host\.firstChild;\s*\n\s*if \(want !== r\) cfg\.host\.insertBefore\(r, want\);\s*\n\s*prev = r;/);
  assert.doesNotMatch(SECTION.slice(SECTION.indexOf("function placeRows"), SECTION.indexOf("function nudge")), /appendChild/, "no unconditional append in placeRows");
  assert.match(SECTION, /var focused = document\.activeElement && cfg\.host\.contains\(document\.activeElement\) \? document\.activeElement : null;\s*\n\s*placeRows\(cfg\.order\(prefs\)\);[^\n]*\n\s*if \(focused && document\.activeElement !== focused && focused\.isConnected\) focused\.focus\(\);/);
  assert.match(GEAR, /order: TW\.tabListOrder, divider: \{ id: TW\.NAME_DIVIDER, label: 'Session name' \}, group: null,/, "the tab section's order carries the divider and knows no groups");
  assert.match(GEAR, /order: SW\.statusListOrder, divider: null,\s*\n\s*group: function \(id\) \{ var w = SW\.statusWidget\(id\); return w \? w\.slot : null; \},/, "the status line has no divider: its rows stay in their slot's group");
  // round two: the polite live region every move speaks through, position and (tab widgets) the side of the name
  assert.match(SECTION, /live\.setAttribute\('aria-live', 'polite'\)/);
  assert.match(SECTION, /liveRegion\(\)\.textContent = labelOf\(id\) \+ ' moved to position ' \+ n \+ ' of ' \+ ids\.length \+ side \+ where;/);
  // round three: the position counted within the row's slot and the slot named (low 2); a refused move spoken (low 1)
  assert.match(SECTION, /return !\(cfg\.divider && x === cfg\.divider\.id\) && \(!cfg\.group \|\| cfg\.group\(x\) === g\);/);
  assert.match(SECTION, /var where = cfg\.group \? ' in the ' \+ cfg\.groupLabel\(g\) : '';/);
  assert.match(SECTION, /function refused\(id\) \{ liveRegion\(\)\.textContent = labelOf\(id\) \+ ' stays in the ' \+ cfg\.groupLabel\(cfg\.group\(id\)\); \}/);
  assert.match(SECTION, /\{ nudge\(row\); refused\(id\); return; \}/, "the key's refusal");
  assert.match(SECTION, /\{ nudge\(row\); refused\(id\); \}/, "the drop's refusal");
  assert.match(GEAR, /groupLabel: function \(g\) \{ return g \+ ' slot'; \},/);
  assert.match(GEAR_CSS, /#rsettings \.rs-live \{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect\(0 0 0 0\);/);
  assert.match(GEAR_CSS, /#rsettings \.rs-widget\.rs-nudge \{ animation: rs-nudge 0\.3s ease; \}/);
  // round two, low 7: the gear's own save normalizes both keys when the store carries them, never injecting either
  assert.match(GEAR, /if \('tabWidgets' in s\) \{ s\.tabWidgets = TW\.tabWidgetPrefs\(s\.tabWidgets, s\.tabCtx\); s\.tabCtx = TW\.tabCtxOfPrefs\(s\.tabWidgets\); \}/);
  assert.match(GEAR, /if \('statusWidgets' in s\) \{ s\.statusWidgets = SW\.statusWidgetPrefs\(s\.statusWidgets\); var m2 = SW\.legacyOfStatusPrefs\(s\.statusWidgets\);/);
  // round two, low 4: a preview is inert
  assert.match(GEAR, /return SW\.makeInert\(line\);/);
});

test("the Tab widgets divider is a separator named for the session name's place, a line with subtle text and never a fake name", () => {
  assert.match(SECTION, /dividerRow = document\.createElement\('div'\); dividerRow\.className = 'rs-widget rs-divider'; dividerRow\.setAttribute\('data-divider', cfg\.divider\.id\);/);
  assert.match(SECTION, /dividerRow\.setAttribute\('role', 'separator'\); dividerRow\.setAttribute\('aria-label', cfg\.divider\.label\);/);
  assert.match(SECTION, /lbl\.className = 'rs-divider-label'; lbl\.textContent = cfg\.divider\.label;/);
  assert.doesNotMatch(SECTION, /dividerRow\.appendChild\(grip\)|rs-divider[^\n]*rs-switch/, "no grip and no switch on the divider: it is not a widget");
  assert.match(GEAR_CSS, /#rsettings \.rs-divider::before, #rsettings \.rs-divider::after \{ content: ""; flex: 1 1 auto; height: 2px; background: var\(--hairline\);/, "a thicker line either side of the words");
  assert.match(GEAR_CSS, /#rsettings \.rs-divider \{[^}]*color: var\(--accent, #9cd2ff\); font-size: 11px; font-weight: 600;/, "small text in the section-label dress, not a rendered name");
  assert.doesNotMatch(GEAR_CSS, /\.rs-divider \{[^}]*(uppercase|letter-spacing)/);
});

test("the previews are drawn by the surfaces' own composers over the demo records, under each section's rows, repainted with every paint", () => {
  assert.match(SECTION, /var box = document\.createElement\('div'\); box\.className = 'rs-preview';/);
  assert.match(SECTION, /cfg\.host\.parentNode\.insertBefore\(title, cfg\.host\.nextSibling\);\s*\n\s*cfg\.host\.parentNode\.insertBefore\(box, title\.nextSibling\);/, "right under the rows: the title, then the box (T415 part two)");
  assert.match(SECTION, /if \(previewBody\) \{ var pv = cfg\.preview\(prefs\); if \(pv\) previewBody\.replaceChildren\(pv\); else previewBody\.replaceChildren\(\); \}/, "repainted in place on every paint");
  // the tab preview: a tab as the strip draws it, both slots around the name, through the strip's own composer
  assert.match(GEAR, /TW\.composeTabWidgets\(tab, 'before', TW\.DEMO_SID, TW\.DEMO_STATUS, prefs\);\s*\n\s*tab\.appendChild\(demoLabel\(\)\);/, "the name between the slots, the demo record's in its identity colour (T415 part two)");
  assert.match(GEAR, /TW\.composeTabWidgets\(tab, 'after', TW\.DEMO_SID, TW\.DEMO_STATUS, prefs\);/, "the after slot follows the name");
  // the status preview: the left slot, the state chip, the right slot ahead of the controls, through the line's own composer
  assert.match(GEAR, /SW\.composeStatusWidgets\(line, 'left', SW\.DEMO_RECORD, prefs\);/);
  assert.match(GEAR, /chip\.className = 'chip rs-sl-chip'; chip\.textContent = 'Ready';/);
  assert.match(GEAR, /SW\.composeStatusWidgets\(right, 'right', SW\.DEMO_RECORD, prefs\);/);
  assert.match(GEAR, /SC\.syncMetaControls\(meta, st, null, \{\}\); right\.appendChild\(meta\);\s*\n\s*var bar = SC\.ctxBar\(\); SC\.setCtxBar\(bar, st\.ctx, false, SC\.pickTone\(st\.ctxColor, st\.ctxTone\), false\); right\.appendChild\(bar\);/, "the fixed controls, drawn by the line's own renderer over the demo status, the battery's tone picked by theme (T415 part two, round three)");
  assert.match(GEAR_CSS, /#rsettings \.rs-preview-title \{[^}]*color: var\(--accent, #9cd2ff\); font-size: 11px; font-weight: 600;/);
});

// THE RINGS (2026-09-14): a section built with reorder: false has no grip and wires no drag; its rows keep the grid's
// columns through an empty cell in the grip's column. The rings' order is the precedence and the registry's, never dragged.
test("reorder: false builds no grip and wires no drag; the empty cell keeps the columns", () => {
  assert.match(SECTION, /if \(cfg\.reorder === false\) \{ grip = document\.createElement\('span'\); grip\.className = 'rs-grip-none'; \}/, "the grip's cell is an empty span");
  assert.match(SECTION, /if \(cfg\.reorder !== false\) wireGrip\(grip, row, w\.id\);/, "wireGrip is skipped: no pointer drag, no arrow keys");
  assert.ok(SECTION.indexOf("if (cfg.reorder === false) { grip = ") < SECTION.indexOf("row.appendChild(grip); row.appendChild(demo);"), "the swap happens before the row is assembled");
  assert.match(GEAR_CSS, /#rsettings \.rs-grip-none \{ width: 18px; height: 22px; \}/, "the same width as the grip, so the demo column lines up with the title rows'");
  assert.match(GEAR, /var ringSection = widgetSection\(\{[\s\S]*?reorder: false,/, "the rings are the section that does not reorder");
  assert.doesNotMatch(GEAR.slice(GEAR.indexOf("var tabSection = widgetSection({"), GEAR.indexOf("var ringSection = widgetSection({")), /reorder: false/, "the title rows still drag");
  assert.doesNotMatch(GEAR.slice(GEAR.indexOf("var statusSection = widgetSection({")), /reorder: false/, "…and so do the status line's");
});
