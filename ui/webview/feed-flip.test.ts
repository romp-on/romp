import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { flipNeeded } from "./feed-flip";

const M = (o: Record<string, string>) => new Map(Object.entries(o));

test("no FLIP when every card kept its column (the everyday in-place update)", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks", "a:2": "completed" }), M({ "a:1": "asks", "a:2": "completed" })), false);
});
test("a card that changed column needs the pass", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks", "a:2": "asks" }), M({ "a:1": "asks", "a:2": "needsInput" })), true);
});
test("a card that moved within its column needs the pass too (the user 2026-06-29: in-column shifters glide)", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks:0", "a:2": "asks:1" }), M({ "a:1": "asks:1", "a:2": "asks:0" })), true);
  assert.equal(flipNeeded(M({ "a:1": "asks:0", "a:2": "asks:1" }), M({ "a:1": "asks:0", "a:2": "asks:1" })), false);
});
test("a card that appeared or left needs the pass (its neighbours shift)", () => {
  assert.equal(flipNeeded(M({ "a:1": "asks" }), M({ "a:1": "asks", "a:2": "asks" })), true);
  assert.equal(flipNeeded(M({ "a:1": "asks", "a:2": "asks" }), M({ "a:1": "asks" })), true);
  assert.equal(flipNeeded(M({ "a:1": "asks" }), M({ "a:9": "asks" })), true, "same count, different card");
});
test("the first paint never flies", () => {
  assert.equal(flipNeeded(new Map(), M({ "a:1": "asks" })), false);
});

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
test("render() gates both forced layouts on the flip decision, and remembers the columns it painted", () => {
  assert.match(SRC, /const nextCols = columnsOf\(buckets\);\n\s*const needFlip = flipNeeded\(prevCols, nextCols\);\n\s*prevCols = nextCols;\n\s*const flipFirst = needFlip \? captureCardRects\(cols\) : new Map<string, FlipState>\(\);/);
  assert.match(SRC, /if \(needFlip\) flyColumnChanges\(flipFirst, cols\);/);
  // columnsOf keys every entry kind (a header per column, so one that changes column counts as moved) and
  // records column AND position, so the gate sees reorders; the keys only need to be consistent with
  // themselves across renders — they are never looked up against reconcileCol's DOM keys
  assert.match(SRC, /const key = e\.kind === "ask" \? "a:" \+ e\.ask\.itemId : e\.kind === "group" \? "g:" \+ e\.group\.turnId : "s:" \+ col \+ ":" \+ e\.sid;/);
  assert.match(SRC, /m\.set\(key, col \+ ":" \+ i\);/);
});
test("the fly reads every rect before it writes any transform", () => {
  const body = /function flyColumnChanges\([\s\S]*?\n\}/.exec(SRC)![0];
  const firstWrite = body.indexOf("c.style.transform = ");
  const lastRead = body.lastIndexOf("getBoundingClientRect()");
  assert.ok(firstWrite > 0 && lastRead > 0 && lastRead < firstWrite, "all reads precede the first write");
  assert.match(body, /const moves: \{ c: HTMLElement; dx: number; dy: number; crossed: boolean \}\[\] = \[\];/);
});

test("a card whose data and display state did not change is not repainted", () => {
  assert.match(SRC, /function cardPaintKey\(it: AskItem\): string \{\n\s*return JSON\.stringify\(it\) \+ "\|"/);
  assert.match(SRC, /const pk = cardPaintKey\(it\);[\s\S]*?if \(a\._paintKey === pk && !card\.querySelector\("button\[disabled\]"\)\) return;/,
    "a latched (disabled) button always repaints: the next paint is what re-enables it");
  // the inputs every card reads that live outside its item: prefs, the status sets + self host, the clock
  assert.match(SRC, /\+ "\|" \+ paintEpoch \+ "\|" \+ Math\.floor\(Date\.now\(\) \/ 15000\);/);
  assert.match(SRC, /function onSettingsChanged\(\): void \{\n\s*paintEpoch\+\+;/);
  assert.match(SRC, /function noteStatusInputs\(\): void \{\n\s*const sig = \[\.\.\.workingSet\]\.sort\(\)\.join\(","\) \+ "\|" \+ \[\.\.\.awaitingSet\][\s\S]*?\[\.\.\.unknownSet\][\s\S]*?feedSelfHost;\n\s*if \(sig !== statusSig\) \{ statusSig = sig; paintEpoch\+\+; \}/);
  assert.match(SRC, /unknownSet = new Set\([\s\S]*?\n\s*noteStatusInputs\(\);/, "the payload handler notes the sets right after setting them");
  // the display-side inputs the paint reads are part of the key (a hover, a pin, a pending bell, a done tick)
  assert.match(SRC, /hoverAskId \?\? pinnedAskId/);
  assert.match(SRC, /pendingNotify\.has\(it\.itemId\)/);
  assert.match(SRC, /\[\.\.\.pendingDone\]\.join\(","\)/, "an optimistic done tick anywhere repaints every card: the set is usually empty");
});
