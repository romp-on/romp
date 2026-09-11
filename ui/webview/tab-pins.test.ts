// Pinned tabs (the user 2026-09-10): a pinned tab keeps its SLOT through every rewrite of the order — a drag of
// another tab, a kernel push, an arrangement re-emission, a reload — is not draggable itself, and wears a folded
// corner. The pure rules (tab-pins.ts) are executed; the pane's hold, paint, menu row and repaint (render.ts) and
// the fold's CSS are pinned at source, the repo's convention where there is no DOM (tab-strip-skip-exec.test.ts
// drives the paint over a fake element tree). Synthetic sids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { TABPINS_KEY, loadTabPins, setTabPinned, placePinned, adoptPinSlots, prunePins } from "./tab-pins";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = read("render.ts");
const CSS = read("styles.css");
const A = "11111111-2222-4333-8444-000000000601", B = "11111111-2222-4333-8444-000000000602";
const C = "11111111-2222-4333-8444-000000000603", D = "11111111-2222-4333-8444-000000000604";
const pins = (...xs: Array<[string, number]>) => new Map(xs);

function store(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init));
  return { getItem: (k: string) => (m.has(k) ? m.get(k)! : null), setItem: (k: string, v: string) => { m.set(k, v); }, raw: (k: string) => m.get(k) };
}

test("a pin is a session AT a slot, persisted per browser; a corrupt or foreign value reads as no pins", () => {
  const s = store();
  assert.deepEqual(loadTabPins(s), new Map());
  assert.deepEqual(setTabPinned(s, A, true, 2), pins([A, 2]));
  assert.deepEqual(setTabPinned(s, B, true, 0), pins([A, 2], [B, 0]));
  assert.equal(s.raw(TABPINS_KEY), JSON.stringify({ [A]: 2, [B]: 0 }), "stored as sid → slot");
  assert.deepEqual(setTabPinned(s, A, false), pins([B, 0]));
  assert.deepEqual(setTabPinned(s, A, false), pins([B, 0]), "unpinning an unpinned tab changes nothing");
  assert.equal(TABPINS_KEY, "romp:tabpins");
  assert.deepEqual(loadTabPins(store({ [TABPINS_KEY]: "nonsense" })), new Map());
  assert.deepEqual(loadTabPins(store({ [TABPINS_KEY]: JSON.stringify({ [A]: "x", [B]: 1.7, [C]: null }) })), pins([B, 1]), "a slot is a whole number");
});

test("the store's first shape — a bare list — reads as pinned wherever they are now, and adopts real slots against the order", () => {
  const legacy = loadTabPins(store({ [TABPINS_KEY]: JSON.stringify([A, "", 3, B]) }));
  assert.deepEqual(legacy, pins([A, -1], [B, -1]));
  assert.deepEqual(adoptPinSlots(legacy, [C, A, B]), pins([A, 1], [B, 2]), "the index each has now");
  assert.equal(adoptPinSlots(pins([A, 1], [B, 2]), [C, A, B]), null, "every pin already has its slot: nothing to write");
  assert.deepEqual(adoptPinSlots(pins([A, -1], [B, 4]), [B, A]), pins([A, 1], [B, 4]), "only the slotless adopt; a pin the order lacks waits");
  assert.equal(adoptPinSlots(pins([A, -1]), [B, C]), null);
});

test("placePinned puts each pinned id at its slot whatever order the rest arrive in", () => {
  // B holds slot 1: the others are dragged, pushed or merged around it
  assert.deepEqual(placePinned([B, C, D, A], pins([B, 1])), [C, B, D, A]);
  assert.deepEqual(placePinned([D, A, B, C], pins([B, 1])), [D, B, A, C]);
  assert.deepEqual(placePinned([A, C, B, D], pins([B, 1])), [A, B, C, D], "a drop on the pinned tab's near side lands past it");
  assert.deepEqual(placePinned([A, B, C, D], pins([B, 1])), [A, B, C, D], "already there: unchanged");
  assert.deepEqual(placePinned([A, C, D, B], pins([B, 1])), [A, B, C, D], "an arrangement that moved the pinned tab is corrected");
  // a reload or a kernel list that puts it first: back to its slot
  assert.deepEqual(placePinned([B, A, C], pins([B, 1])), [A, B, C]);
  // nothing pinned, or a pin the order does not carry: the order stands
  assert.deepEqual(placePinned([C, A, B], new Map()), [C, A, B]);
  assert.deepEqual(placePinned([C, A, B], pins([D, 0])), [C, A, B]);
  // a slotless pin (the first shape) holds wherever it is now
  assert.deepEqual(placePinned([C, A, B], pins([A, -1])), [C, A, B]);
});

test("placePinned: several pins hold together, a slot past the end trails, a slot at 0 leads, ties break by current order", () => {
  assert.deepEqual(placePinned([A, C, D, B], pins([A, 0], [C, 2])), [A, D, C, B]);
  assert.deepEqual(placePinned([C, A, B], pins([C, 2])), [A, B, C], "a pin at the last slot stays last");
  assert.deepEqual(placePinned([D, C], pins([D, 3])), [C, D], "a slot past the end trails");
  assert.deepEqual(placePinned([A, B, C], pins([C, 0])), [C, A, B]);
  assert.deepEqual(placePinned([A, B, C, D], pins([C, 1], [D, 1])), [A, C, D, B], "two pins on one slot: the one earlier in the order takes it, the other follows");
  assert.deepEqual(placePinned([A, B, C, D], pins([B, 1], [D, 3])), [A, B, C, D]);
  assert.deepEqual(placePinned([D, C, B, A], pins([B, 1], [D, 3])), [C, B, A, D]);
});

test("prunePins drops pins whose session left the strip — never on an empty strip — and says when nothing changed", () => {
  assert.equal(prunePins(pins([A, 0], [B, 1]), [A, B, C]), null);
  assert.deepEqual(prunePins(pins([A, 0], [B, 1]), [B, C]), pins([B, 1]));
  assert.equal(prunePins(pins([A, 0], [B, 1]), []), null, "no strip yet is not a strip with nothing on it");
  assert.equal(prunePins(new Map(), [A]), null);
});

test("render.ts: pinned tabs are put back at their slots after EVERY rewrite of the order — a push, a drag, a pin heard — and the arrangement is not written back", () => {
  assert.match(RENDER, /import \{ TABPINS_KEY, TABPINS_EVENT, loadTabPins, writeTabPins, setTabPinned, placePinned, adoptPinSlots, prunePins \} from "\.\/tab-pins";/);
  // the hold: adopt slotless pins, place, and only this column's order changes (no commitTabOrder here)
  const hold = RENDER.slice(RENDER.indexOf("function holdPinnedSlots(): boolean {"), RENDER.indexOf("function syncTabPinsWithStrip"));
  assert.match(hold, /const adopted = adoptPinSlots\(pins, order\);\n\s*if \(adopted\) writeTabPins\(localStorage, adopted\);\n\s*const held = placePinned\(order, adopted \|\| pins\);/);
  assert.match(hold, /order\.length = 0;\n\s*for \(const id of held\) order\.push\(id\);\n\s*tabPinJustHeld = true;\n\s*return true;/);
  assert.doesNotMatch(hold, /commitTabOrder|writeViewOrder/, "the shared arrangement is not written: the columns are not forced onto one order (the user 2026-09-10)");
  // after a kernel push / re-emission rebuilt the order, before the paint
  assert.match(RENDER, /for \(const id of kernelOrder\) kernelListed\.add\(id\);\n\s*holdPinnedSlots\(\);\n\s*renderTabs\(\);\n\s*syncTabKeysWithStrip\(\);\n\s*syncTabPinsWithStrip\(\);\n\}/);
  // after a drag spliced it, ahead of the drag's own commit
  assert.match(RENDER, /function reorderTo\(dragId: string, targetId: string, after: boolean\)(?:: boolean)? \{[^\n]*\n(?:\s*if \(fedMissing\) return(?: false)?;[^\n]*\n)?\s*const di = order\.indexOf\(dragId\);\n\s*if \(di < 0\) return(?: false)?;\n\s*order\.splice\(di, 1\);[\s\S]*?holdPinnedSlots\(\);[^\n]*\n\s*tabDragJustCommitted = true;[^\n]*\n\s*commitTabOrder\(\);\n\s*renderTabs\(\);/);   // main's manager-missing guard leads and the drop reads its boolean (2026-09-10)
  // when a pin is set here or heard from a sibling column
  assert.match(RENDER, /window\.addEventListener\("storage", \(e\) => \{ if \(e\.key === TABPINS_KEY\) \{ holdPinnedSlots\(\); renderTabs\(\); \} \}\);\n\s*window\.addEventListener\(TABPINS_EVENT, \(\) => \{ holdPinnedSlots\(\); renderTabs\(\); \}\);/);
  // the order audit files it as explained, like a drag
  assert.match(RENDER, /stack, drag: tabDragJustCommitted, pin: tabPinJustHeld \};/);
  assert.match(RENDER, /tabDragJustCommitted = false;\n\s*tabPinJustHeld = false;\n\s*lastTabIds = ids\.slice\(\);/);
  // pins of departed tabs are dropped
  assert.match(RENDER, /function syncTabPinsWithStrip\(\): void \{\n\s*const kept = prunePins\(loadTabPins\(localStorage\), order\);\n\s*if \(kept\) writeTabPins\(localStorage, kept\);/);
});

test("render.ts: a pinned tab is not draggable, wears the pushpin after its hot key, and is in the strip's signature", () => {
  assert.match(RENDER, /const pins = loadTabPins\(localStorage\);[^\n]*\n(?:[^\n]*\n)?\s*const stripSig = JSON\.stringify\(\[/, "read once per render, before the signature");
  assert.match(RENDER, /tabChord\(id, keyOverrides, IS_MAC\), pins\.has\(id\)\]/, "an input the strip paints is in its signature");
  assert.match(RENDER, /const pinned = pins\.has\(id\);\n\s*if \(pinned\) tab\.classList\.add\("pinned"\);/);
  // the glyph (the user 2026-09-11, replacing the folded corner): the menu's pushpin, after the hot key, named for a screen reader
  const glyph = RENDER.indexOf('if (pinned) { const pn = el("span", "tab-pin"); pn.innerHTML = pinSvg(11); pn.title = "Pinned — it stays where it is"; pn.setAttribute("role", "img"); pn.setAttribute("aria-label", pn.title); tab.appendChild(pn); }');
  assert.ok(glyph > 0, "the pin glyph is appended");
  assert.ok(glyph > RENDER.indexOf('if (hk) { const k = el("span", "tab-key");'), "…after the hot key badge");
  assert.match(RENDER, /^const PIN_PATHS = '<path d="M6\.2 2\.5 H9\.8 L9\.3 6\.4 L11\.4 8\.4 V9\.4 H4\.6 V8\.4 L6\.7 6\.4 Z"\/><line x1="8" y1="9\.4" x2="8" y2="13\.5"\/>';/m, "one drawing");
  assert.match(RENDER, /^function pinSvg\(size: number\): string \{/m);
  assert.doesNotMatch(RENDER, /tab-fold/, "the folded corner is gone");
  assert.match(RENDER, /tab\.draggable = !s\.sub && !pinned(?: && !fedMissing)?;/, "the pinned tab does not start a drag (nor any tab on a page without its manager — main, 2026-09-10)");
});

test("render.ts: the tab menu pins AT the tab's slot and unpins, with the sub-line saying what a pin does", () => {
  const i = RENDER.indexOf('l.textContent = on ? "Unpin tab" : "Pin tab";');
  assert.ok(i > 0, "the row exists");
  const block = RENDER.slice(RENDER.lastIndexOf("// Pin (the user 2026-09-10)", i), RENDER.indexOf("menu.appendChild(pin);", i));
  assert.match(block, /const on = loadTabPins\(localStorage\)\.has\(id\);/);
  assert.match(block, /ctxIcon\("pin", on\)/, "the icon, slashed while pinned (the row unpins)");
  assert.match(block, /sb\.textContent = on \? "it can be dragged again" : "it keeps this slot whatever else moves — drags flow around it";/);
  assert.match(block, /pin\.addEventListener\("click", \(ev\) => \{ ev\.stopPropagation\(\); dismissTabMenu\(\); setTabPinned\(localStorage, id, !on, order\.indexOf\(id\)\); \}\);/,
    "the click SETS the state the row showed, at the slot the tab has now");
  assert.ok(i > RENDER.indexOf('l.textContent = "Open in new split"') && i < RENDER.indexOf('l.textContent = cur ? "Update hot key…" : "Hot key…"'),
    "between Open in new split and Hot key…, with the session controls");
  assert.match(RENDER, /kind === "pin"\n\s*\? PIN_PATHS/, "the menu's Pin row draws the same pushpin the pinned tab wears");
});

test("styles.css: the pushpin sits in the tab's run, dim like the keycap, brighter with the tab; no fold rules remain", () => {
  assert.match(CSS, /\.tab-pin \{ flex: 0 0 auto; display: inline-flex; color: var\(--dim\); \}/);
  assert.match(CSS, /\.tab\.active \.tab-pin, \.tab:hover \.tab-pin \{ color: var\(--fg\); \}/);
  assert.doesNotMatch(CSS, /tab-fold|\.tab\.pinned \{ padding-right/, "the corner flap and its padding are gone");
});
