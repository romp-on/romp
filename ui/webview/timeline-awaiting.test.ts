// Timeline AWAITING badge (the user 2026-07-01, working-state audit; recolored 2026-07-13): the lane
// shows a distinct AWAITING badge for "waiting on dispatched/background work". Originally the badge wore
// working-yellow; since the kernel's shared _session_chip split awaitingBg out of "working" (the user
// 2026-07-13: "differentiate working from awaiting") it wears its OWN await-green — the working gold's paler
// sibling — matching the chat chip and the tab/feed dots. The s.awaitingBg why-field stays the fallback
// key for a remote host on an older kernel (state still 'working' there).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { kindWord, KIND_WORD } from "./spin-caption";

const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("an awaitingBg lane renders an Awaiting badge in the romp brand green (the user 2026-07-22)", () => {
  // keyed on the chip state (the shared _session_chip split) OR the legacy why-field (older remote kernels)
  // the kind word agrees in NUMBER with the kernel's count (T228) — via the standalone twin of kindWord()
  // …through tlAwaitSuffix since slice 2 (2026-09-05): the twin's word when it has one, the bare count for a mixed wait
  assert.match(TL, /else if \(s\.state === 'awaitingBg' \|\| s\.awaitingBg\) m = \{ label: 'Awaiting' \+ tlAwaitSuffix\(s\.awaitingKind, s\.awaitingCount\), kind: 'awaitbg' \};/);
  assert.match(TL, /const w = kind \? tlKindWord\(kind, count\) : '';\s*\n\s*if \(w\) return ' ' \+ w;/);
  // brand green, matching --st-awaitbg-bg in styles.css (this file loads standalone, so the hex is mirrored)
  assert.match(TL, /awaitbg: \{ bg: '#54B204', fg: '#0c1a00' \}/);
  // an awaitingBg lane still reads ACTIVE (full opacity / ongoing treatment), like working/compacting/clearing
  assert.match(TL, /s\.state === 'awaitingBg' \|\| s\.state === 'compacting' \|\| s\.state === 'clearing';/);
});

test("precedence: blocked-on-you beats awaiting, awaiting beats Ready", () => {
  const blocked = TL.indexOf("m = { label: 'Blocked', kind: 'attention' }");
  const awaiting = TL.indexOf("label: 'Awaiting'");
  const ready = TL.indexOf("m = { label: 'Ready', kind: 'ready' }");
  assert.ok(blocked > 0 && awaiting > 0 && ready > 0, "all three badge branches exist");
  assert.ok(blocked < awaiting, "a hard block (on you) outranks the awaiting cue");
  assert.ok(awaiting < ready, "awaiting is checked before the plain Ready fallback");
});

test("needsInput maps to Blocked, and the legacy 'awaiting' name (an older remote kernel) still does too", () => {
  assert.match(TL, /s\.state === 'permission' \|\| s\.state === 'needsInput' \|\| s\.state === 'awaiting'\) m = \{ label: 'Blocked', kind: 'attention' \}/);
});

test("an idle awaitingBg lane draws a full-thickness FADED stretch (0.4 alpha), not a thin dash (the user 2026-07-13)", () => {
  // from the last work period's end to the live edge, lane-colored, at the work-bar thickness (BAR_H) but
  // faded to 0.4 — a faded continuation of the bar, not a thin dash, and never the solid ~0.9 work bar
  assert.match(TL, /if \(s\.live && s\.awaitingBg\) \{/);
  assert.match(TL, /el\('line', \{ x1: lx1, y1: y, x2: lx2, y2: y, stroke: s\.color, 'stroke-width': BAR_H,\s*\n\s*'stroke-linecap': 'round', opacity: 0\.4,/);
  assert.doesNotMatch(TL, /'stroke-dasharray': '5 4'/);   // the dash is gone
  // the hover lists the live task descriptions (kernel awaitingTasks), falling back to the why line
  assert.match(TL, /s\.awaitingTasks && s\.awaitingTasks\.length\) \? s\.awaitingTasks : \[s\.awaitingBg\]/);
  // hover bumps opacity (0.4 -> 0.6) + a slight grow, keeping the "faded/pending" read rather than going solid
  assert.match(TL, /ln\.setAttribute\('stroke-width', String\(BAR_H \+ 2\)\); ln\.setAttribute\('opacity', '0\.6'\); this\.showTip\(tip, e\);/);
  assert.match(TL, /ln\.setAttribute\('stroke-width', String\(BAR_H\)\); ln\.setAttribute\('opacity', '0\.4'\); this\.hideTip\(\);/);
  // the stretch keeps empty-row behaviors: drag to pan/reorder, click to select/open
  assert.match(TL, /wh\.addEventListener\('mousedown', \(e\) => this\._beginDrag\(s\.id, e\)\);/);
});

// --- T228 (the user's one-count rule): the lane badge words the kind from the SAME count as the chip -------
function tlKindWordFromSource(): (kind: unknown, count: unknown) => string {
  // this file runs standalone (Obsidian too) and cannot import spin-caption.ts, so it carries a RESOLVED
  // twin; execute that twin from its source text and hold it to the webview helper below
  const table = TL.match(/const KIND_WORD = \{[^}]*\};/);
  const fn = TL.match(/function tlKindWord\(kind, count\) \{[\s\S]*?\n\}/);
  assert.ok(table && fn, "the timeline carries the KIND_WORD table and the tlKindWord twin");
  return new Function(table![0] + "\n" + fn![0] + "\nreturn tlKindWord;")() as (kind: unknown, count: unknown) => string;
}

test("the timeline's tlKindWord twin agrees with spin-caption's kindWord on every kind × count", () => {
  const tl = tlKindWordFromSource();
  const kinds = [...Object.keys(KIND_WORD), "", null, undefined, "nonsense"];
  const counts = [null, undefined, 0, 1, 2, 7, NaN];
  for (const k of kinds) for (const c of counts) {
    assert.equal(tl(k, c), kindWord(k as any, c as any), `kind=${String(k)} count=${String(c)}`);
  }
  assert.equal(tl("agents", 1), "agent", "one awaited agent reads singular on the lane, as on the chip");
  assert.equal(tl("agents", 2), "agents");
  assert.equal(tl("task", 3), "commands", "the plain words of slice 2 (2026-09-05): a task row is a command");
  assert.equal(tl("job", 1), "watch");
  assert.equal(tl("job", 2), "watches");
  assert.equal(tl("mixed", 4), "", "a mixed wait has no word on the lane either");
  assert.equal(tl("agents", null), "agents", "an older kernel with no count keeps the historic plural");
});

test("the kernel's lane payload ships awaitingCount beside awaitingKind, from the same snapshot", () => {
  assert.match(KERNEL, /"awaitingKind": awaiting_kind,\s*\n\s*"awaitingCount": \(\(_aw_bg or \{\}\)\.get\("count"\) if isinstance\(\(_aw_bg or \{\}\)\.get\("count"\), int\) else None\),/);
});
