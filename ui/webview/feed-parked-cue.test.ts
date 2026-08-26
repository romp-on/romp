// The PARKED row cue on the feed (the user 2026-08-24 audit): a queued ask silently sat 40 minutes
// while the same card's younger items were dispatched past it, and nothing on the surface said so.
// The kernel ships a per-row `parked: {n}` (kernel _parked_rows: leapfrogged by n younger siblings'
// handoff edges, retiring on the row's own delegation edge or any witnessed verdict); the feed wears
// it as a quiet one-word tag on the checklist row and the modal row — the cleared tag's exact idiom,
// dim by design, never the trouble-chip family — plus a dim " · N parked" suffix on the sub-goals
// button as the card-level gist (progressive disclosure: the button that shows the rows IS the
// click). Source pins, the feed-panel idiom; the kernel truth table lives in tests/test_parked_cue.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const FEED = W("feed.ts");
const FEEDCSS = W("feed.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("kernel: the row field is additive and gated on the OPEN render state", () => {
  assert.match(KERNEL, /"parked": \(\{"n": parked_rows\[nid\]\} if \(st == "open" and nid in parked_rows\) else None\)/);
  assert.match(KERNEL, /parked_rows = _parked_rows\(nodes, children\)/);
  assert.match(FEED, /parked\?: \{ n: number \} \| null;/, "additive on the type — old payloads render as before");
});

test("the tag is the cleared tag's quiet idiom, on both the checklist row and the modal row", () => {
  assert.match(FEED, /function parkedTag\(n: number\): HTMLElement \{/);
  assert.match(FEED, /tag\.textContent = "parked";/);
  assert.match(FEED, /if \(s\.parked && s\.parked\.n && !s\.cleared\) row\.appendChild\(parkedTag\(s\.parked\.n\)\);/,
    "card checklist row");
  assert.match(FEED, /if \(node\.parked && node\.parked\.n && !node\.cleared\) line\.appendChild\(parkedTag\(node\.parked\.n\)\);/,
    "modal tree row");
  assert.match(FEEDCSS, /\.fparked-tag \{ flex: 0 0 auto; font-size: 0\.92em; color: var\(--dim\);/,
    "same metrics as .fcleared-tag — no new font size");
});

test("the card-level gist is a dim suffix on the sub-goals button, not a trouble chip", () => {
  // the count mirrors the checklist walk the button toggles — stops at handoff nodes and the root —
  // so the suffix never advertises rows no expansion of the checklist reveals (review 2026-08-24)
  assert.match(FEED, /if \(!n \|\| n\.kind === "handoff" \|\| pseen\.has\(n\.id\)\) return;/);
  assert.match(FEED, /if \(n\.parked && n\.parked\.n\) parkedCount\+\+;/);
  assert.match(FEED, /pk\.textContent = " · " \+ parkedCount \+ " parked";/);
  assert.match(FEEDCSS, /\.fask-subparked \{ opacity: 0\.6; font-weight: 400; \}/,
    "the waiting-chip's sub-line treatment — the sub-goal count stays the loudest word");
});

test("a parked mint/retire re-renders an open modal — the tag rides treeSig", () => {
  // tops are siblings, so ANOTHER top's dispatch can mint/retire a root's tag with nothing else in
  // this tree changing; without the sig component an open group modal kept the stale tag
  assert.match(FEED, /\+ \(n\.parked && n\.parked\.n \? "p" \+ n\.parked\.n : ""\)/);
});
