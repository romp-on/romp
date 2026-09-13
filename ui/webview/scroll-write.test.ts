// T262 (the user 2026-09-08): a view that "jumps up slightly on my scroll", on a page whose bundle state nobody can
// see. Every programmatic write to #content.scrollTop rides ONE helper (render.ts writeScroll) that files a
// client-diag breadcrumb naming its writer, and the scroll listener files a gesture marker for every scroll the
// writes do not explain — so the next occurrence is convictable from the laptop's client-diag.jsonl. Executed
// budget + classifier + row shape; render.ts pins: the helper, every writer name, and NO raw write left. Red on main.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { ScrollDiagBudget, classifyScroll, scrollWriteRow, SCROLL_DIAG_CAP_PER_MINUTE } from "./scroll-write";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const SID = "TESTHOST:11111111-2222-4333-8444-000000000262";

test("the budget sends up to the cap per session per minute, says 'cap' once, drops the rest, and rolls with the minute", () => {
  const b = new ScrollDiagBudget(3);
  const t0 = 1_700_000_000_000;
  assert.deepEqual([b.take(SID, "scrollwrite", t0), b.take(SID, "scrollwrite", t0 + 10), b.take(SID, "scrollwrite", t0 + 20)], ["send", "send", "send"]);
  assert.equal(b.take(SID, "scrollwrite", t0 + 30), "cap", "the (cap+1)th files one capped row");
  assert.equal(b.take(SID, "scrollwrite", t0 + 40), "drop");
  assert.equal(b.take(SID, "scrollgesture", t0 + 40), "send", "kinds are budgeted apart");
  assert.equal(b.take("other", "scrollwrite", t0 + 40), "send", "sessions are budgeted apart");
  assert.equal(b.take(SID, "scrollwrite", t0 + 60_000), "send", "the next minute starts fresh");
  assert.equal(SCROLL_DIAG_CAP_PER_MINUTE, 40);
});

test("a scroll event within a pixel of the last programmatic write is its echo; anything else is a gesture", () => {
  assert.equal(classifyScroll(1200, 1200), "write-echo");
  assert.equal(classifyScroll(1200.5, 1200), "write-echo", "sub-pixel rounding");
  assert.equal(classifyScroll(1230, 1200), "gesture");
  assert.equal(classifyScroll(1200, null), "gesture", "no write pending: the user did it");
  // the marker is one-shot (verifier low, T366 round two): set only by a write that MOVED the view, consumed by the first
  // event after it whatever that event was, so a gesture that lands within a pixel of an old write's target is a gesture
  assert.match(RENDER, /const after = content\.scrollTop;\s*\n\s*if \(after !== before\) lastScrollWriteAfter = after;/, "a write that did not move owes no echo");
  assert.match(RENDER, /const cls = classifyScroll\(c\.scrollTop, lastScrollWriteAfter\);[\s\S]{0,900}?\n\s*lastScrollWriteAfter = null;/, "the first event after the write consumes the marker, echo or not");
});

test("the row names the writer and carries before/after/delta/stick, gesture:false", () => {
  assert.deepEqual(scrollWriteRow(SID, "append-stick", 100, 340, true, 5000, 600),
                   { sid: SID, writer: "append-stick", before: 100, after: 340, delta: 240, stick: true, gesture: false, sh: 5000, ch: 600 });
  // T262e: every row carries #content's scrollHeight/clientHeight, so an UNWRITTEN move in the journal can be told
  // apart: a clamp after the tail shrank (sh dropped, top == sh - ch) vs the browser's anchoring (sh unchanged)
  assert.deepEqual(scrollWriteRow(SID, "land-saved", 0, 10, false), { sid: SID, writer: "land-saved", before: 0, after: 10, delta: 10, stick: false, gesture: false, sh: 0, ch: 0 });
});

test("render.ts: one helper writes #content.scrollTop, files the row only when the view moved, and no raw write remains", () => {
  assert.match(RENDER, /import \{ ScrollDiagBudget, classifyScroll, scrollWriteRow, tailChangeRow, tailLabel, spacerRow, readScrollDiagCap, summarizeTailMutations, tailMutRow, unitChangeRow, unitChanges, boxChanges, boxLabel, BOX_FROM_TAIL \} from "\.\/scroll-write";/);
  assert.match(RENDER, /function writeScroll\(content: HTMLElement, top: number, writer: string, stick = false\): void \{\s*\n\s*const before = content\.scrollTop;\s*\n\s*content\.scrollTop = top;\s*\n\s*const after = content\.scrollTop;\s*\n\s*if \(after !== before\) lastScrollWriteAfter = after;[^\n]*\n\s*lastKnownSh = content\.scrollHeight;\s*\n\s*if \(after !== before\) scrollDiagRow\("scrollwrite", scrollWriteRow\(activeId \|\| "", writer, before, after, stick, content\.scrollHeight, content\.clientHeight\)\);/);
  // the breadcrumb rides the existing clientDiag path, capped, with one capped row at the cap
  assert.match(RENDER, /\{ type: "clientDiag", surface: "chat", what: kind \+ "-capped", data: \{ sid: activeId \|\| "", perMinute: scrollDiagCap \} \}/);   // the cap the page runs with (T262j: configurable)
  assert.match(RENDER, /\{ type: "clientDiag", surface: "chat", what: kind, data \}/);
  // every writer is named
  for (const w of ["optimistic-send", "keep-offset", "toolgroup-toggle", "land-bottom", "land-saved", "anchor-restore",
                   "append-stick", "append-raw", "jump-button", "box-resize", "tabbar-drag", "rewindow", "liveask-reveal",
                   "nav-history", "focus-live"]) {
    assert.match(RENDER, new RegExp('writeScroll\\((?:content|c), [^;]*"' + w + '"'), "writer " + w + " goes through the helper");
  }
  // no raw #content write outside the helper (menus, the editor box and the per-view bookkeeping are not the transcript)
  const raw = RENDER.split("\n").filter((l) => /\b(content|c)\.scrollTop (=|\+=|-=) /.test(l) && !/writeScroll|const |let /.test(l));
  assert.deepEqual(raw.map((l) => l.trim()), ["content.scrollTop = top;"], "the only assignment is the helper's own");
  // the gesture marker: a write's echo is consumed, anything else files a gesture row
  assert.match(RENDER, /const cls = classifyScroll\(c\.scrollTop, lastScrollWriteAfter\);\s*\n\s*const gv = activeId \? views\.get\(activeId\) : null;\s*\n\s*if \(gv\) gv\.gestureScroll = cls === "gesture";[^\n]*\n\s*if \(cls === "gesture"\) \{ if \(gestureEvidence\(settleLastInput, Date\.now\(\), settleScrollerHeld\)\) settleGesture\(\); else settleSample\(\); \}[^\n]*\n(?:\s*\/\/[^\n]*\n){3}\s*lastScrollWriteAfter = null;[^\n]*\n\s*if \(cls !== "write-echo"\) scrollDiagRow\("scrollgesture", \{ sid: activeId \|\| "", top: c\.scrollTop, gesture: true, sh: c\.scrollHeight, ch: c\.clientHeight \}\);/, "the scroll nobody's code asked for is the user's; a write's echo is consumed, never filed (the classification is read once and marks the view for the edge check, T366)");
});
