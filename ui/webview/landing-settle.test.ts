// The settle rule for a deep-link landing (landing-settle.ts), executed; and the render.ts wiring pins (T386 stage 1, with the
// verifier's round one folded in: the gesture verdict ends the settle, a superseded landing files its row, the scroll clamp is
// accounted for, the tool-group head walks to the words, the tolerance is capped, the aligned element flashes, text atoms are
// searched first, a hidden view is a detached one, two named backstop timers; round two: a gesture needs the reader's input behind it,
// the settle observes through its whole window).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { SETTLE_MS, settleStep, settleRowFields, withinRow } from "./landing-settle";
import * as LS from "./landing-settle";   // the round-one exports, read by name: the file still builds against the head before them, and
                                          // the pins on them alone go red there (a named import of a missing export fails the whole build)

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("within its own row: a row's height, never under a few pixels", () => {
  assert.equal(withinRow(0, 40), true);
  assert.equal(withinRow(-40, 40), true);
  assert.equal(withinRow(41, 40), false);
  assert.equal(withinRow(7, 0), true, "a zero-height row still tolerates rounding");
  assert.equal(withinRow(9, 0), false);
});

test("quiet samples WAIT for the window's end (round two, low 1); a sample off the row asks for a re-land; the reader's gesture ends it", () => {
  assert.equal(settleStep([], 40, false, 0), "wait", "nothing measured yet");
  assert.equal(settleStep([{ at: 0, dist: 0 }], 40, false, 10), "wait");
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 60, dist: 2 }], 40, false, 70), "wait", "two adjacent quiet samples used to settle here, 60 ms in; a later displacement then went unsampled");
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 250, dist: 2 }, { at: 700, dist: 0 }], 40, false, 720), "wait", "still observing: the window is open");
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 250, dist: 190 }], 40, false, 260), "realign", "the page moved the target: put it back");
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 250, dist: 190 }, { at: 300, dist: 1 }], 40, false, 320), "wait", "back on the row: keep observing");
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 250, dist: 190 }], 40, true, 260), "gave-up", "the reader scrolled: theirs now");
  assert.equal((LS as Record<string, unknown>).SETTLE_QUIET, undefined, "no early-settle count remains");
});

test("a gesture needs the reader's input behind it (round two, medium): an input within SETTLE_INPUT_MS before the scroll; none, or a stale one, is a sample", () => {
  assert.equal(LS.SETTLE_INPUT_MS, 120, "a scroll follows its input within a frame or two; the window bounds staleness");
  assert.equal(LS.gestureEvidence(1000, 1050), true, "a wheel 50 ms before the scroll");
  assert.equal(LS.gestureEvidence(1000, 1120), true, "at the window's edge");
  assert.equal(LS.gestureEvidence(1000, 1121), false, "stale: the input was for an earlier scroll");
  assert.equal(LS.gestureEvidence(0, 1050), false, "no input ever seen: the browser's anchoring or another mover");
  assert.equal(LS.gestureEvidence(null, 1050), false);
  assert.equal(LS.gestureEvidence(2000, 1050), false, "an input after the scroll is not its cause");
  // round three: the pointer HELD on the scroller (a scrollbar thumb drag: one pointerdown, no moves, scrolls until the release)
  assert.equal(LS.gestureEvidence(0, 1050, true), true, "held on the scroller: the reader's, whatever the clock says");
  assert.equal(LS.gestureEvidence(1000, 1900, true), true, "…a pause past the window before the first movement changes nothing");
  assert.equal(LS.gestureEvidence(1000, 1900, false), false, "released: the timed window rules again");
  assert.equal(LS.scrollerGrab(true, 10, 10, 800, 600), true, "a press whose target is the scroller element itself (the gutter is its own box)");
  assert.equal(LS.scrollerGrab(false, 805, 100, 800, 600), true, "…or whose offset lies in the vertical scrollbar's gutter");
  assert.equal(LS.scrollerGrab(false, 100, 605, 800, 600), true, "…or the horizontal one's");
  assert.equal(LS.scrollerGrab(false, 100, 100, 800, 600), false, "a press on the content's children is a drag inside the content: the timed rule");
  // round four: the reader's input that arrives as a WRITE of #content (the classifier never sees it as a gesture)
  assert.deepEqual([...LS.READER_WRITERS].sort(), ["focus-live", "jump-button", "key-nav", "nav-history", "section-link", "wheel-scale"], "the reader's own writers, derived from the census (round five)");
  for (const w of ["nav-history", "section-link", "focus-live"]) assert.equal(LS.writerIsReader(w), true, w + " is the reader's: a chord, a link click, the live-tail chip (round five)");
  assert.equal(LS.writerIsReader("key-nav"), true, "an arrow key's step is the reader's takeover");
  assert.equal(LS.writerIsReader("wheel-scale"), true);
  assert.equal(LS.writerIsReader("land-realign"), false, "the landing's own writers are never a takeover");
  assert.equal(LS.writerIsReader("rewindow"), false, "the page's movers are samples");
  assert.equal(LS.writerIsReader("append-stick"), false);
  assert.equal(LS.writerIsReader("never-named"), false, "an unlisted writer is no takeover");
});

test("the census names every writer render.ts gives writeScroll, and nothing else: a new writer cannot land unclassified (round five)", () => {
  // every writer literal, read out of render.ts the way scroll-write.test.ts reads the raw-write ban: the last string of each
  // writeScroll, scrollContentBy or scrollElInto call that is not an alignment word, and of the landing's own two wrappers
  // (landOn's local land(), settleLand()), which pass their writer through
  const literals = new Set<string>();
  const re = /\b(?:writeScroll|scrollContentBy|scrollElInto|land|settleLand)\(([^;]*?)\);/g;
  for (let m = re.exec(RENDER); m; m = re.exec(RENDER)) {
    // the writer is the call's LAST argument (a stick flag may follow it); a call passing a variable names no literal here
    const last = /(?:^|,)\s*"([a-z-]+)"(?:,\s*(?:true|false))?\s*$/.exec(m[1]);
    if (last && !["start", "center", "nearest"].includes(last[1])) literals.add(last[1]);
  }
  assert.ok(literals.size >= 20, "the writer literals found in render.ts: " + [...literals].sort().join(", "));
  for (const w of literals) assert.ok(w in LS.WRITER_CLASS, "unclassified writer in render.ts: " + w);
  for (const w of Object.keys(LS.WRITER_CLASS)) assert.ok(literals.has(w), "a census entry render.ts no longer writes: " + w);
  assert.deepEqual([...literals].sort(), Object.keys(LS.WRITER_CLASS).sort(), "the census IS the set of writers");
});

test("the window's end files the landing as it stands: settled within the row, else unsettled, never held forever", () => {
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 1200, dist: 3 }], 40, false, SETTLE_MS), "settled");
  assert.equal(settleStep([{ at: 0, dist: 0 }, { at: 1200, dist: 300 }], 40, false, SETTLE_MS), "unsettled", "off the row at the end: a miss, on the record");
  assert.equal(settleStep([{ at: 0, dist: 300 }], 40, false, SETTLE_MS + 50), "unsettled");
  assert.equal(SETTLE_MS, 1200, "the span landOn already re-aligned within");
  assert.equal(LS.SETTLE_FIRST_PAINT_MS, 250, "the one early backstop: the first paint after the landing's own render");
  assert.equal(LS.SETTLE_ROW_VIEWPORT_CAP, 0.25, "the row is at most a quarter of the viewport");
});

test("the row's fields: the last distance rounded, settled by the step; a gave-up landing within the row is not a miss", () => {
  assert.deepEqual(settleRowFields("settled", [{ at: 0, dist: 0 }, { at: 250, dist: 2.4 }], 40), { dist: 2, settled: true });
  assert.deepEqual(settleRowFields("unsettled", [{ at: 0, dist: 0 }, { at: 1200, dist: 300.6 }], 40), { dist: 301, settled: false });
  assert.deepEqual(settleRowFields("gave-up", [{ at: 0, dist: 0 }, { at: 100, dist: 5 }], 40), { dist: 5, settled: true, gesture: true }, "the takeover MARK (round three, low 3): the audit tells an abandoned landing from a held one");
  assert.deepEqual(settleRowFields("gave-up", [{ at: 0, dist: 0 }, { at: 100, dist: 500 }], 40), { dist: 500, settled: false, gesture: true });
  assert.deepEqual(settleRowFields("gave-up", [], 40), { dist: null, settled: false, gesture: true }, "a takeover before any measurement still wears the mark");
  assert.deepEqual(settleRowFields("wait", [], 40), { dist: null, settled: false }, "never measured");
  assert.equal("gesture" in settleRowFields("settled", [{ at: 0, dist: 0 }], 40), false, "a landing that held wears no mark");
});

test("the scroll clamp: how far short of the viewport top a target near the tail must stop (medium 3)", () => {
  assert.equal(LS.reachableOffset(5000, 9000, 600), 0, "a target well inside the scroll range reaches the top");
  assert.equal(LS.reachableOffset(8400, 9000, 600), 0, "…up to the very last reachable scroll top");
  assert.equal(LS.reachableOffset(8493, 9000, 600), 93, "93 px short: the clamp stops the scroller at 8400");
  assert.equal(LS.reachableOffset(100, 500, 600), 100, "a transcript shorter than the viewport: the target sits where it is");
  assert.equal(LS.reachableOffset(8450.6, 9000, 600), 51, "rounded to the pixel");
});

test("render.ts wiring: landOn ends follow mode, feeds the rule from the page's own events, files the row at settle time, and the walk-forward waits", () => {
  assert.match(RENDER, /import \{ SETTLE_MS, SETTLE_FIRST_PAINT_MS, SETTLE_ROW_VIEWPORT_CAP, settleStep, settleRowFields, reachableOffset, gestureEvidence, scrollerGrab, writerIsReader, type SettleSample \} from "\.\/landing-settle";/);
  assert.match(RENDER, /if \(c && v\) v\.stick = atBottom\(c\); \}/, "a landing ends follow mode unless it put the reader at the bottom (the tail-shrink snap otherwise undoes it)");
  assert.match(RENDER, /const landSettle = \{ turn: target, at, uuid: flashKey \?\? null, quote: quote \?\? null, rowH: settleRowHeight\(at\), samples: \[\] as SettleSample\[\],/, "one settle in flight per landing, with what re-finds its target");
  assert.match(RENDER, /ro\.observe\(at\); if \(at !== target\) ro\.observe\(target\);/, "the aligned element's box, and the turn's");
  assert.match(RENDER, /for \(const sp of Array\.from\(v\.el\.querySelectorAll\("\.tx-spacer"\)\)\) ro\.observe\(sp\);/, "the view's spacers: their size from estimate to measurement");
  assert.match(RENDER, /function settleTick\(\): void \{/);
  assert.match(RENDER, /const step = settleStep\(s\.samples, s\.rowH, s\.gesture, Date\.now\(\) - s\.start\);/);
  assert.match(RENDER, /if \(step === "realign"\) \{ settleLand\(s, "land-realign"\); return; \}/, "a re-land is a write of the landing's own, attributed");
  assert.match(RENDER, /settleFinish\(s, settleRowFields\(step, s\.samples, s\.rowH\)\);/);
  assert.match(RENDER, /if \(s\.row\) vscodeApi\?\.postMessage\(\{ \.\.\.s\.row, \.\.\.fields, \.\.\.\(s\.clamp \? \{ clamp: s\.clamp \} : \{\}\) \}\);/, "the deferred row goes out with the measurement and the clamp when one applied");
  assert.match(RENDER, /if \(after !== before && landSettling && !landSettling\.done && writer !== "land-on" && writer !== "land-realign"\) \{ if \(writerIsReader\(writer\)\) settleGesture\(\); else settleSample\(\); \}/,
    "the reader's own writers take the landing over; every other writer's move is a sample (round four)");
  assert.match(RENDER, /settleLastInput = 0; settleScrollerHeld = false;/, "landOn clears the timed evidence and the hold (rounds three and four)");
  assert.match(RENDER, /window\.addEventListener\("blur", \(\) => \{ settleScrollerHeld = false; \}\);\s*\n\s*document\.addEventListener\("visibilitychange", \(\) => \{ settleScrollerHeld = false; \}\);/, "the hold ends with the page's focus or visibility (round four, low 1)");
  assert.match(RENDER, /if \(after !== before && landSettling && !landSettling\.done && writer !== "land-on" && writer !== "land-realign"\) \{/, "another writer's move during the settle is a sample, so the rule re-lands");
  assert.match(RENDER, /if \(scrolled && landSettling && !landSettling\.done && landTrail\[landTrail\.length - 1\] === "pointer-exact"\) \{ landSettling\.row = row; settleSample\(\); \}[^\n]*\n\s*else vscodeApi\?\.postMessage\(row\);/, "an exact landing's row waits for the settle; every other outcome files at once");
  assert.match(RENDER, /if \(landSettling && !landSettling\.done\) \{ afterSettle\.push\(\(\) => edgeCheckAfterWindow\(sid\)\); return; \}/, "the walk-forward of a detached window that fits waits for the landing to settle");
  assert.match(RENDER, /pendingAnchorT = ask\?\.t \?\? null; pendingAnchorKind = ask\?\.kind \?\? null;/, "the click's time and kind ride through the window's adoption");
});

test("render.ts wiring, round one: the gesture verdict ends the settle by any input; a superseded landing files its row; the clamp is measured", () => {
  // medium 1: the scroll listener's classifier verdict, not a wheel or key listener alone
  assert.match(RENDER, /if \(cls === "gesture"\) \{ if \(gestureEvidence\(settleLastInput, Date\.now\(\), settleScrollerHeld\)\) settleGesture\(\); else settleSample\(\); \}/,
    "the classifier's gesture verdict ends the settle only with the reader's input behind it, timed or the pointer held on the scroller; without, the scroll is a sample (rounds two and three)");
  assert.doesNotMatch(RENDER, /window\.addEventListener\("wheel", settleGesture/, "no wheel-only listener: a scrollbar drag and a touch swipe count too");
  assert.doesNotMatch(RENDER, /addEventListener\("keydown", settleGesture\)/, "a key is evidence for the scroll it causes, not a takeover by itself (round two)");
  assert.match(RENDER, /for \(const ev of \["pointerdown", "pointermove", "pointerup", "pointercancel", "touchstart", "touchmove", "wheel", "keydown"\]\) window\.addEventListener\(ev, settleInput, \{ capture: true, passive: true \}\);/,
    "the reader's hand on the scroller: pointer, touch, wheel and key, recorded as the time of the last input; the release ends a hold");
  const inp = RENDER.slice(RENDER.indexOf("function settleInput(e: Event): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function settleInput(e: Event): void {")));
  assert.match(inp, /if \(e\.type === "pointermove" && !\(e as PointerEvent\)\.buttons\) return;/, "a hover is not a hand on the scroller; a drag inside the content is");
  assert.match(inp, /if \(e\.type === "pointerup" \|\| e\.type === "pointercancel"\) \{ settleScrollerHeld = false; return; \}/, "the release ends the hold (round three)");
  assert.match(inp, /if \(scrollerGrab\(e\.target === c, pe\.clientX - cr\.left, pe\.clientY - cr\.top, c\.clientWidth, c\.clientHeight\)\) settleScrollerHeld = true;/, "a press on the scroller itself or in its gutter starts the hold");
  assert.match(RENDER, /settleLastInput = 0; settleScrollerHeld = false;   \/\/ the input that caused this landing/, "landOn clears the timed evidence and the hold: the click that landed is not a takeover (rounds three and four)");
  assert.match(RENDER, /gesture: undefined, settled: false, superseded: true/, "a superseded row wears no takeover mark");
  assert.match(inp, /if \(!c \|\| !\(e\.target instanceof Node\) \|\| !c\.contains\(e\.target\)\) return;/, "on the scroller and its scrollbar only");
  assert.match(inp, /a\.tagName === "TEXTAREA" \|\| a\.tagName === "INPUT" \|\| \(a as HTMLElement\)\.isContentEditable/, "typing in a field scrolls the field, never #content");
  assert.match(RENDER, /settleLastInput = Date\.now\(\);/);
  assert.doesNotMatch(RENDER, /re-align whenever the bar\/ledger actually resizes, plus two\n\/\/ timed retries/, "landOn's comment describes the settle, not the wheel cancel and two retries (round two, low 4)");
  // medium 2: the superseded row
  assert.match(RENDER, /function settleSupersede\(s: NonNullable<typeof landSettling>\): void \{\s*\n\s*settleEnd\(s\);\s*\n\s*if \(s\.row\) vscodeApi\?\.postMessage\(\{ \.\.\.s\.row, \.\.\.settleRowFields\("gave-up", s\.samples, s\.rowH\), gesture: undefined, settled: false, superseded: true,/);
  assert.match(RENDER, /if \(landSettling\) settleSupersede\(landSettling\);/, "a newer landing files the older's row, marked");
  // medium 3: the clamp
  assert.match(RENDER, /const floor = reachableOffset\(r\.top - cr\.top \+ c\.scrollTop, c\.scrollHeight, c\.clientHeight\);\s*\n\s*s\.clamp = floor;\s*\n\s*s\.samples\.push\(\{ at: Date\.now\(\) - s\.start, dist: \(r\.top - cr\.top\) - floor \}\);/);
  // low 1: the tolerance, capped and re-measured
  assert.match(RENDER, /return Math\.max\(8, Math\.min\(at\.getBoundingClientRect\(\)\.height, \(c \? c\.clientHeight : 600\) \* SETTLE_ROW_VIEWPORT_CAP\)\);/);
  assert.match(RENDER, /s\.rowH = settleRowHeight\(s\.at\);/, "re-measured when the settle swaps its element");
  // low 2: the aligned element flashes
  assert.match(RENDER, /at\.classList\.add\("anchor-flash"\);/);
  assert.match(CSS, /^\.anchor-flash \{ animation: anchor-flash 1\.6s ease-out; border-radius: 6px; \}/m, "the flash rule matches the aligned element, a turn or the words inside it");
  // low 4: a hidden view is a detached one
  assert.match(RENDER, /if \(s\.at\.isConnected && s\.at\.getClientRects\(\)\.length\) return s\.at;/);
  assert.match(RENDER, /if \(!turn \|\| !turn\.getClientRects\(\)\.length\) return null;/);
  // low 5: two named backstops
  assert.match(RENDER, /landSettle\.timers\.push\(window\.setTimeout\(settleSample, SETTLE_FIRST_PAINT_MS\), window\.setTimeout\(settleSample, SETTLE_MS \+ 20\)\);\s*\n\s*\/\//,
    "no sample inside landOn itself (round three, low 1)");
  assert.match(RENDER, /landTrail\[landTrail\.length - 1\] === "pointer-exact"\) \{ landSettling\.row = row; settleSample\(\); \}/,
    "the row attached, then the landing's own first sample, so a takeover in the first frames files the landing as it stood and an unmeasurable one files settled false");
});

test("render.ts wiring: the settle re-finds its target by uuid after a rebuild replaced the DOM, and gives up honestly when the turn is gone", () => {
  assert.match(RENDER, /function settleResolve\(s: NonNullable<typeof landSettling>\): HTMLElement \| null \{/);
  assert.match(RENDER, /const turn = s\.uuid \? findTurnEl\(s\.uuid\) : null;/, "a detached box measures as zeros: re-find, never measure it");
  assert.match(RENDER, /s\.at = \(s\.quote \? highlightCiteSpan\(turn, s\.quote\) : null\) \?\? firstTextAtomBelow\(turn\) \?\? turn;/, "the same alignment as the landing's, re-derived");
  assert.match(RENDER, /if \(!at\) \{ settleFinish\(s, \{ dist: null, settled: false \}\); return; \}/, "the turn left the view: the row says so");
  assert.match(RENDER, /function findTurnEl\(uuid: string\): HTMLElement \| null \{/);
});

test("render.ts wiring: a card anchored on a turn's first atom lands on the quoted words, or the turn's text atom, not the tool group", () => {
  assert.match(RENDER, /const quote = pendingAnchorQuote; pendingAnchorQuote = null;\s*\n\s*const quoteEl = quote \? highlightCiteSpan\(target, quote\) : null;\s*\n\s*landOn\(target, uuid, quoteEl \?\? firstTextAtomBelow\(target\), quote\);/);
  assert.match(RENDER, /function highlightCiteSpan\(target: HTMLElement, quote: string\): HTMLElement \| null \{/, "the highlight returns the element the sentence starts in");
  // low 3: text atoms first, then tool and thinking atoms; the alignment is the top, one rule for every landing
  assert.match(RENDER, /const ordered = \[\.\.\.atoms\.filter\(\(a\) => !isToolOrThinkingAtom\(a\)\), \.\.\.atoms\.filter\(\(a\) => isToolOrThinkingAtom\(a\)\)\];/);
  assert.match(RENDER, /return range\.startContainer\.parentElement;/);
  // medium 5: the group head counts as a tool atom
  assert.match(RENDER, /function isToolOrThinkingAtom\(n: Element\): boolean \{\s*\n\s*return n\.classList\.contains\("turn-tool"\) \|\| n\.classList\.contains\("turn-toolgroup"\) \|\| n\.classList\.contains\("turn-thinking"\);/);
  assert.match(RENDER, /function firstTextAtomBelow\(target: HTMLElement\): HTMLElement \| null \{\s*\n\s*if \(!isToolOrThinkingAtom\(target\)\) return null;/);
  assert.match(RENDER, /function turnAtomsOf\(target: HTMLElement\): HTMLElement\[\] \{/);
  assert.match(RENDER, /if \(!\(n instanceof HTMLElement\) \|\| !n\.classList\.contains\("turn"\) \|\| n\.classList\.contains\("turn-user"\)\) break;/, "the turn ends at the next user turn");
  assert.match(RENDER, /function landOn\(target: HTMLElement, flashKey\?: string, alignOn\?: HTMLElement \| null, quote\?: string \| null\) \{/);
  assert.match(RENDER, /const at = alignOn \?\? target;/);
});
