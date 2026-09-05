// The distinct AWAITING state (the user 2026-07-13, who wanted to differentiate working from awaiting): a session
// whose main thread is idle but waiting on background work it dispatched no longer folds into "working".
// The kernel's shared _session_chip emits `awaitingBg`; the chat chip says "Awaiting" in the romp brand
// GREEN (--st-awaitbg-bg #54B204, the swirl's green arm — distinct from Working's gold), and the little
// dots match the chip's color everywhere: the chat tab dot and the feed's fwork-dot (cards, group cards,
// modal headers, grouped-mode session headers). ("awaiting" the chip state = a live permission/picker
// prompt, on YOU — a different concept; the Bg suffix dodges that name.) Source pins (no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = W("render.ts");
const STYLES = W("styles.css");
const FEED = W("feed.ts");
const FEEDCSS = W("feed.css");
const FED = W("federation.ts");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("the chat chip knows awaitingBg: its own await-green chip, label 'Awaiting', with the elapsed timer", () => {
  assert.match(RENDER, /"awaiting" \| "awaitingBg" \|/);           // the ChipState union carries both meanings
  assert.match(RENDER, /awaitingBg: "Awaiting",/);                 // CHIP_LABEL
  // its own statusline branch: await-green chip + the wait's clock — but NO pulse (nothing computing here)
  assert.match(RENDER, /\} else if \(s\.status\.state === "awaitingBg"\) \{[\s\S]*?chip chip-awaitingBg[\s\S]*?timer\.id = "work-timer";/);
  assert.doesNotMatch(RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0], /chip-pulse/);
  // the ticking clock covers it, same as working
  assert.match(RENDER, /if \(s\.status\.state === "working" \|\| s\.status\.state === "awaitingBg"\) \{\s*\n\s*const timer = document\.getElementById\("work-timer"\);/);
  // no stop button — the main thread is idle, there's nothing to interrupt
  assert.doesNotMatch(RENDER, /awaitingBg[^\n]*stopButton|stopButton[^\n]*awaitingBg/);
});

test("the chat tab dot matches the chip: await-green for awaitingBg, yellow for working", () => {
  assert.match(RENDER, /if \(st === "working"\) tab\.appendChild\(el\("span", "tab-dot"\)\);\s*\n\s*else if \(st === "awaitingBg"\) tab\.appendChild\(el\("span", "tab-dot await"\)\);/);
  assert.match(STYLES, /--st-awaitbg-bg: #54B204; --st-awaitbg-fg: #0c1a00;/);
  assert.match(STYLES, /\.chip-awaitingBg \{ background: var\(--st-awaitbg-bg\); color: var\(--st-awaitbg-fg\); \}/);
  assert.match(STYLES, /\.tab-dot\.await \{ background: var\(--st-awaitbg-bg\); \}/);
});

test("the feed dot matches too: dotFor picks work/await per name, the dot retints in place", () => {
  // the kernel's feed payload carries the awaiting name list beside working; federation merges + prefixes it
  assert.match(KERNEL, /"working": working, "awaiting": awaiting,/);
  assert.match(KERNEL, /if sess_awaiting_why and not who_working:\s*\n\s*awaiting\.append\(name\)/);
  assert.match(KERNEL, /\{"type": "working", "names": feed\["working"\],\s*\n\s*"awaiting": feed\.get\("awaiting"\) or \[\]\}/);
  assert.match(FEED, /awaitingSet = new Set\(Array\.isArray\(m\.awaiting\) \? m\.awaiting : \[\]\);/);
  // dotFor still ranks work over await; the unreadable-state quarter follows (feed-status-pips.test.ts)
  assert.match(FEED, /workingSet\.has\(name\) \? "work" : awaitingSet\.has\(name\) \? "await"/);
  // an existing dot RETINTS when the state flips, instead of only add/remove (now via paint(), which
  // carries the tooltip too — see feed-status-pips.test.ts)
  assert.match(FEED, /else if \(st && has\) paint\(prev!\);/);
  assert.match(FEED, /d\.classList\.toggle\(k, st === k\);/);
  // every name-dot site routes through dotFor: cards, group cards, both modal headers, grouped
  // headers, and the session-filter button (2026-08-08; its menu rows route via setWorkDot(label,…))
  assert.equal((FEED.match(/setWorkDot\((?:a\._name|agent|nm), dotFor\(/g) || []).length, 6);
  assert.match(FEEDCSS, /\.fwork-dot\.await \{ background: #54B204; \}/);
  assert.match(FED, /const ARRAY_ID = \["order", "names", "working", "awaiting", "stateUnknown"\];/);
  assert.match(FED, /if \(Array\.isArray\(f\.awaiting\)\) merged\.awaiting\.push\(\.\.\.f\.awaiting\);/);
});

test("the kernel split happens in the ONE shared derivation (_session_chip), not per surface", () => {
  assert.match(KERNEL, /"working" if open_now else\n/);
  assert.match(KERNEL, /"awaitingBg" if awaiting_why else "ready"\)/);
});

test("the awaiting WHY lives in the background box, not the statusline (the user 2026-08-13, twice)", () => {
  // the kernel ships the why + the live awaited task descriptions in the chat status payload…
  assert.match(KERNEL, /"awaitingWhy": awaiting_why or None,/);
  assert.match(KERNEL, /"awaitingTasks": \(\(\(_awaiting_task_descs\(sid, sess\["path"\]\) or[\s\S]{0,80}?\.get\("tasks"\) or \[\]\)\) if awaiting_why else \[\]\),/);   // watch descs ride when no bg-task descs exist (2026-08-30)
  // …plus WHAT the wait is on, as data (jd.AWAIT_KINDS; the user 2026-08-15) — on the chat status,
  // the timeline lane, and the feed card's awaiting object alike, so every surface words one fact
  assert.match(KERNEL, /"awaitingKind": awaiting_kind,/);
  assert.match(KERNEL, /"kind": await_kind,/);
  // …the statusline branch stays chip + clock ONLY — the reason line PR #350 put beside the chip
  // crowded the composer area, and the user moved it the same day
  const branch = RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0];
  assert.doesNotMatch(branch, /sl-await-why/);
  assert.doesNotMatch(STYLES, /sl-await-why/);
  // …and the #bg-tasks box renders it — since slice 2 (2026-09-05) whenever a wait exists, the tracked
  // tasks joining its rows: the same fold treatment (await-green dot, verb-stripped header), expanding
  // to the awaited ROWS grouped by kind, or to the full why when the kernel names none, and a
  // plain-words note on what the state means. Stop rides only a row backed by a LIVE tracked task
  // (bgRow's stopId) — an untracked wait (a peer's PR, a watch) still has no process to kill.
  assert.match(RENDER, /if \(why \|\| !count \|\| !tasks\.length\) \{ renderAwaitWhy\(host, s \|\| null, tasks\); return; \}/);
  assert.match(RENDER, /"bg-fold-head bg-await"/);
  assert.match(RENDER, /lab\.textContent = "Awaiting" \+ \(word \? " " \+ word : ""\) \+ " · " \+ why\.replace\(\/\^\(waiting on\|awaiting\)\\s\+\/i, ""\)/);
  // …the kind word rides the visible label (the user 2026-08-15) — tooltips are dead on the touch PWA
  assert.match(RENDER, /const word = awaitWord\(s!\.status\.awaitingKind, s!\.status\.awaitingCount, items\);/);
  assert.match(RENDER, /chip-awaiting-" \+ \(s\.status\.awaitingKind \|\| "untyped"\)/);
  assert.match(RENDER, /if \(descs\.length > 1\)/);   // the no-rows fallback lists the legacy descriptions only when there are several
  assert.match(RENDER, /bg-await-note/);
  assert.match(RENDER, /stopId: running \? tracked!\.id : null/);
  assert.match(RENDER, /return \{ id, status: "armed", caption: "armed", label: it\.label \|\| "a watch", since: it\.since,\s*\n\s*watchId: it\.watchId \|\| null, command: it\.detail \|\| null \};/, "a watch row: Cancel when the kernel has a handle, never Stop");
  assert.match(STYLES, /\.bg-fold-head\.bg-await \{ --bgt: var\(--st-awaitbg-bg\); \}/);
});

test("the awaited tasks wear the chip's green outline — exact launch-id match; dots keep status meaning", () => {
  // kernel: the awaited LAUNCH IDS ride the status payload beside the descriptions (the user
  // 2026-08-19), from the same _bg_split set, so outline and chip can never disagree
  assert.match(KERNEL, /"awaitingTaskIds": \(_awaiting_task_ids\(sid, sess\["path"\]\) if awaiting_why else \[\]\),/);
  assert.match(KERNEL, /def _awaiting_task_ids\(sid, path\):/);
  assert.match(KERNEL, /return \[t\["tid"\] for t in awaited if t\.get\("tid"\)\]/);
  // client: rows are marked from awaitingTaskIds only while the chip is awaitingBg…
  assert.match(RENDER, /awaitingTaskIds\?: string\[\];/);
  assert.match(RENDER, /const awaited = new Set<string>\(s!\.status\.awaitingTaskIds \|\| \[\]\);/);   // ids' presence, never the chip state (2026-08-30: awaited things show even while working)
  assert.match(RENDER, /host\.classList\.toggle\("bg-awaited", tasks\.some\(\(t\) => awaited\.has\(t\.id\)\)\);/);
  assert.match(RENDER, /bgRow\(taskRowSpec\(t, awaited\.has\(t\.id\)\), sid\)/);   // the row spec carries the match (slice 2's one row renderer)
  assert.match(RENDER, /\(t\.awaited \? " bg-awaited" : ""\)/);
  // …the untracked-wait box (renderAwaitWhy) IS the awaited thing, so it wears the border whole
  assert.match(RENDER, /host\.classList\.add\("bg-awaited"\);/);
  // the outline is the chip's await-green — the border/outline only; the status DOT rules are untouched
  assert.match(STYLES, /#bg-tasks\.bg-awaited \{ border-color: var\(--st-awaitbg-bg\); \}/);
  assert.match(STYLES, /\.bg-task\.bg-awaited \{ box-shadow: inset 0 0 0 1px var\(--st-awaitbg-bg\); border-radius: 5px; \}/);
  assert.match(STYLES, /\.bg-task \{ --bgt: var\(--st-working-bg\); \}/);
});

test("awaited things show even while WORKING, and kernel watches feed the box (the user 2026-08-30)", () => {
  // Their requirement, paraphrased: even mid-turn, anything the session awaits — jobs, watches,
  // pending externals — shows at the chat bottom in the green box. Three legs, pinned where they live:
  // 1. the box keys on awaited CONTENT, never the chip state (the old state gate was the defect)
  assert.match(RENDER, /const why = \(s && \(s\.status\.awaitingWhy \|\| ""\)\.trim\(\)\) \|\| "";/);
  assert.doesNotMatch(RENDER.split("function renderAwaitWhy")[1].split("\n}")[0],
    /state === "awaitingBg" &&/);
  // …with the fold's plain-words note adapting to the chip: idle keeps the historic sentence,
  // working says the session keeps going and is told when the wait lands
  assert.match(RENDER, /s!\.status\.state === "awaitingBg"\s*\n\s*\? "The session is idle until this finishes/);
  assert.match(RENDER, /: "The session keeps working meanwhile; it's told when this lands\."/);
  // 2. kernel watches are an awaiting SOURCE (idle: flips the chip like any wait; the rows are
  // kernel-owned and event-true at both ends — armed at registration, cleared on fire/cancel)
  assert.match(KERNEL, /def _watch_awaiting\(sid\):/);
  // …COMBINED with the live agents and pending launches since slice 2 (2026-09-05) — no source
  // short-circuits another; the one answer is derived from every row
  assert.match(KERNEL, /combined = _awaiting_from_items\(agents, commands, _watch_awaiting\(sid\)\)\s*\n\s*if combined:\s*\n\s*return combined/);
  // 3. mid-turn, the CONTENT rides the payload while the shared state formula stays untouched —
  // the chip keeps reading working, the box renders from the fields
  assert.match(KERNEL, /if not _aw and open_now:\s*\n\s*_aw = _watch_awaiting\(sid\)/);
  assert.match(KERNEL, /"working" if open_now else\n/);   // the state formula's ordering is intact
});

test("the timeline lane's awaitingBg why reads the SAME working signal as its badge (same input, 2026-07-03 rule)", () => {
  // the skeleton build's raw-snapshot open_now fed _session_awaiting while the chip read the event
  // model — a lane badge could say Awaiting with a null why beside it (audited live 2026-08-13)
  assert.match(KERNEL, /aw_open = _session_working\(comp_sess\["turns"\]\) if comp_sess is not None else open_now/);
  assert.match(KERNEL, /_session_awaiting\(sid, s\["path"\], not aw_open, stamp=True\) if live else None/);
  // the awaiting stretch's hover labels the wait with the state's one word
  const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
  assert.match(TL, /– awaiting…/);
  assert.doesNotMatch(TL, /– waiting…/);
});
