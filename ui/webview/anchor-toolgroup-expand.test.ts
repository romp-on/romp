// A deep-link anchor must land on ANY uuid the kernel or timeline emits — including one folded
// inside a collapsed tool run (the user 2026-07-16: clicking a Blocked card anchored to its
// session's pending AskUserQuestion tool atom toasted "couldn't locate this in the transcript";
// locate-audit showed pointer-not-rendered with the atom sitting right behind the fold). The
// collapsed toolgroup line carries only the run's FIRST uuid (renderToolGroup), so scrollToAnchor's
// window re-render alone can never surface a mid-run member: it must EXPAND the run first. And an
// ANSWERED AskUserQuestion turn is anchored by its answer line's resultUuid — a uuid no event
// carries as its own — so the recovery lookup must match resultUuid too. The chat renderer has no
// jsdom harness, so — like the other render-*.test.ts — pin the behavior at the source, plus an
// executed mirror of the expand decision.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("scrollToAnchor's recovery lookup matches resultUuid (the answered-ask anchor) too", () => {
  assert.match(RENDER,
    /findIndex\(\(e\) => e\.uuid === uuid \|\| \(e as \{ mid\?: string \}\)\.mid === uuid\s*\|\| \(e as \{ orphanOf\?: string \}\)\.orphanOf === uuid\s*\|\| \(e as \{ resultUuid\?: string \}\)\.resultUuid === uuid\s*\|\| \(\(\(e as \{ settleUuids\?: string\[\] \}\)\.settleUuids \|\| \[\]\)\.includes\(uuid\)\)\)/,
    "the events lookup must resolve every uuid renderEvent can stamp as data-uuid or data-uuids");
});

test("an anchor inside a collapsed tool run expands the run before the window re-render", () => {
  // the expansion must key on the EXACT item that contains the event index — a nearest-unit
  // fallback hit is a different unit and must not pop a stranger's fold open
  assert.match(RENDER,
    /if \(hit && hit\.kind === "toolgroup" && hit\.indices\.includes\(idx\)\)\s*\n\s*openFolds\.add\(toolGroupKey\(s\.events\[hit\.indices\[0\]\]\)\);/,
    "scrollToAnchor expands the collapsed run holding the anchor, keyed like toggleToolGroup (openFolds since 2026-09-08)");
  const expandAt = RENDER.indexOf("openFolds.add(toolGroupKey(s.events[hit.indices[0]]));");
  const rerenderAt = RENDER.indexOf("renderWindowItems(v, s, items, Math.max(0, u - WINDOW_RADIUS)");
  assert.ok(expandAt >= 0 && rerenderAt >= 0 && expandAt < rerenderAt,
    "the expansion lands BEFORE the window re-render, so the re-query can find the member's turn");
});

test("the collapsed toolgroup line really does carry only the run's first uuid (why expansion is needed)", () => {
  assert.match(RENDER, /const anchorUuid = tools\[0\]\.uuid \?\? null;\s*\n\s*if \(anchorUuid\) turn\.dataset\.uuid = anchorUuid;/,
    "renderToolGroup stamps tools[0].uuid only — mid-run members are unreachable while folded");
});

// executed mirror of the expand decision: expand exactly when the resolved display item is a
// toolgroup whose indices contain the event index; the nearest-unit fallback never expands.
test("expand decision: exact toolgroup hit expands, nearest-unit fallback does not", () => {
  type Item = { kind: "toolgroup"; indices: number[] } | { kind: "turn"; index: number };
  const itemFirstEvent = (it: Item): number => it.kind === "toolgroup" ? it.indices[0] : it.index;
  const decide = (items: Item[], idx: number): { u: number; expand: boolean } => {
    let u = items.findIndex((it) => it.kind === "toolgroup" ? it.indices.includes(idx) : it.index === idx);
    if (u < 0) u = Math.max(0, items.findIndex((it) => itemFirstEvent(it) >= idx));
    const hit = items[u];
    return { u, expand: !!hit && hit.kind === "toolgroup" && hit.indices.includes(idx) };
  };
  const items: Item[] = [{ kind: "turn", index: 0 }, { kind: "toolgroup", indices: [1, 2, 3] }, { kind: "turn", index: 5 }];
  assert.deepEqual(decide(items, 2), { u: 1, expand: true }, "a mid-run member expands its own run");
  assert.deepEqual(decide(items, 1), { u: 1, expand: true }, "the first member expands too (its line uuid may be an answer resultUuid)");
  assert.deepEqual(decide(items, 0), { u: 0, expand: false }, "a plain turn never expands anything");
  assert.deepEqual(decide(items, 4), { u: 2, expand: false }, "a gap index resolves by nearest unit and must NOT pop a stranger's fold");
});


test("T418: the group head speaks by action (actionHead) with the edits' totals in the diff colours, and a tool row's label is the model's description or the derived phrase (toolRowLabel)", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /const parts = actionParts\(tools\);\s*\n\s*line\.appendChild\(document\.createTextNode\(" "\)\);\s*\n\s*const w = el\("span", "toolgroup-head"\); w\.textContent = parts\.text; line\.appendChild\(w\);[^\n]*\n\s*appendTotals\(line, parts\.add, parts\.del\);/,
    "the collapsed group head is the action phrases, the summed totals appended once");
  assert.match(RENDER, /if \(ev\.file\) head\.appendChild\(fileLink\(ev\.file\)\);/, "every event with a file keeps its link (round two, medium 2)");
  assert.doesNotMatch(RENDER, /lbl\.totals/, "the head carries no totals span: an edit's totals are the diff fold's toggle, once per row, and a failed edit prints none (round three, low a and d)");
  assert.match(RENDER, /a FAILED edit's error fold\s*\n\s*\/\/ replaces that fold and prints no totals: a failed edit changed nothing/, "the rule for a failed edit is stated where the head is built");
  assert.match(RENDER, /inlineFold\(head, turn, diffTotals\(add, del\), pre, fkey\);/, "the fold's toggle in the approved shape (+A -R, a hyphen minus), dressed in the diff colours like the folded summary (2026-09-18)");
  assert.match(RENDER, /const lbl = toolRowLabel\(ev\);\s*\n\s*const name = el\("span", "tool-label" \+ \(lbl\.code \? " tool-label-code" : ""\)\); name\.textContent = lbl\.text;/,
    "the row's label is the description or the derived phrase; a bare command wears the code face");
  assert.match(RENDER, /if \(lbl\.secondary\) \{ const c = el\("span", "tool-name tool-secondary"\); c\.textContent = lbl\.secondary; head\.appendChild\(c\); \}/, "the tool's name is secondary, only where no phrase names the action");
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.tool-plus \{ color: var\(--green\); \}\s*\n\.tool-minus \{ color: var\(--err\); \}/, "the totals wear the diff colours");
  assert.match(CSS, /\.tool-err \.tool-name, \.tool-err \.tool-label \{ color: var\(--err\); \}/, "a failed row's label stays red");
});
