// The Awaiting chip is a button, and what it waits on shows as ROWS grouped by kind
// (plans/subagent-transcripts.md slice 2, the user 2026-09-05).
//
// Before: the statusline's "Awaiting <word>" chip was a plain span — the one status word on the pane
// you could not click through — and the word came from whichever kernel source spoke first (live
// subagents → "agents"; the pending launches → "agents" only if EVERY row was an agent, else the generic
// "tasks", so a shell command plus an agent read "Awaiting 2 tasks" with the agent silently absorbed;
// armed watches → the mystery word "jobs"). Now every awaited thing is its own row (kernel
// awaitingItems: {kind, id, label, since, agentId?, detail?, watchId?}), the chip words itself from
// the rows by ONE rule shared with the box gist and the feed pill (awaitWord), the chip opens the box,
// and the box lists the rows under small dim group headers when more than one kind is present —
// agent rows with the open-transcript arrow + Stop, command rows with the output fold + Stop, watch
// rows with the label, how long armed, and Cancel when the kernel has a handle.
//
// The label rules are EXECUTED (spin-caption exports them); the DOM wiring is pinned at the source
// (no jsdom for the chat renderer), the same harness every other awaiting pin uses.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { awaitWord, awaitBreakdown, groupRows, rowWord, kindWord, spinFor, GROUP_TITLE, ROW_KIND_OF_LEGACY,
         flattenWaits, rowIds, agentRowOf, waitsNote, type AwaitRow } from "./spin-caption";
import { subWaitTail } from "./subagent-view";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = W("render.ts");
const FEED = W("feed.ts");
const STYLES = W("styles.css");
const FEEDCSS = W("feed.css");
const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

const agent = (label: string, agentId = "a0123456789abcdef"): AwaitRow => ({ kind: "agents", id: "tu_" + label, label, since: 100, agentId });
const command = (label: string): AwaitRow => ({ kind: "commands", id: "b_" + label, label, since: 120 });
const watch = (label: string, watchId: string | null = "w1"): AwaitRow => ({ kind: "watches", id: "watch:" + label, label, since: 90, detail: "test -f /tmp/" + label, watchId });
const peer = (name: string): AwaitRow => ({ kind: "peer", id: "peer:" + name, label: name });

// --- the label rules, executed ------------------------------------------------------------------------
test("awaitWord: one row reads its kind's word; several of one kind read the count and the word", () => {
  assert.equal(awaitWord("agents", 1, [agent("map the parser")]), "agent");
  assert.equal(awaitWord("task", 1, [command("build the docs")]), "command");
  assert.equal(awaitWord("job", 1, [watch("the CI run")]), "watch");
  assert.equal(awaitWord("peer", 1, [peer("web")]), "peer", "a single peer's word; the surfaces swap in the peer's own name");
  assert.equal(awaitWord(null, null, [{ kind: "timer", id: "t1", label: "20 minutes" }]), "timer");
  assert.equal(awaitWord("agents", 3, [agent("a"), agent("b"), agent("c")]), "3 agents");
  assert.equal(awaitWord("task", 2, [command("a"), command("b")]), "2 commands");
  assert.equal(awaitWord("job", 2, [watch("a"), watch("b")]), "2 watches");
});

test("awaitWord: several KINDS read the bare number — the breakdown is the tooltip's job", () => {
  const rows = [agent("a"), agent("b"), command("c"), watch("d")];
  assert.equal(awaitWord("mixed", 4, rows), "4");
  assert.equal(awaitBreakdown(rows), "2 agents · 1 command · 1 watch");
  assert.equal(awaitBreakdown([command("c")]), "1 command");
  assert.equal(awaitBreakdown([]), "");
});

test("awaitWord: with no rows (an older kernel, a judge stamp) the legacy kind + count still word it", () => {
  assert.equal(awaitWord("agents", 1, []), "agent");
  assert.equal(awaitWord("agents", 2, []), "2 agents");
  assert.equal(awaitWord("task", null, []), "command", "no count → the kind's default word, no number");
  assert.equal(awaitWord("job", 2, null), "2 watches");
  assert.equal(awaitWord("mixed", 4, []), "4", "a mixed overlay row with a count → the number");
  assert.equal(awaitWord("mixed", null, []), "", "…and with none, no word at all: the chip reads plain Awaiting");
  assert.equal(awaitWord(null, null, []), "agents", "kindless keeps the historic default");
});

test("groupRows: display order agents → commands → watches → peers → timers; unknown kinds kept, never dropped", () => {
  const g = groupRows([peer("web"), watch("w"), command("c"), agent("a"), { kind: "future", id: "f", label: "x" }]);
  assert.deepEqual(g.map((x) => x.kind), ["agents", "commands", "watches", "peer", "other"]);
  assert.deepEqual(groupRows([]), []);
  assert.deepEqual(groupRows(null), []);
  for (const k of ["agents", "commands", "watches", "peer", "timer"]) assert.ok(GROUP_TITLE[k], "every group has a header title: " + k);
  assert.equal(rowWord("watches", 1), "watch"); assert.equal(rowWord("watches", 2), "watches");
  assert.equal(ROW_KIND_OF_LEGACY.task, "commands"); assert.equal(ROW_KIND_OF_LEGACY.job, "watches");
});

test("the three word maps agree on every legacy kind × count: kindWord, the timeline twin, and awaitWord with no rows", () => {
  const table = TL.match(/const KIND_WORD = \{[^}]*\};/);
  const fn = TL.match(/function tlKindWord\(kind, count\) \{[\s\S]*?\n\}/);
  const suffix = TL.match(/function tlAwaitSuffix\(kind, count\) \{[\s\S]*?\n\}/);
  assert.ok(table && fn && suffix, "the timeline carries the table, tlKindWord and tlAwaitSuffix");
  const tl = new Function(table![0] + "\n" + fn![0] + "\nreturn tlKindWord;")() as (k: unknown, c: unknown) => string;
  const tlSuffix = new Function(table![0] + "\n" + fn![0] + "\n" + suffix![0] + "\nreturn tlAwaitSuffix;")() as (k: unknown, c: unknown) => string;
  for (const k of ["agents", "task", "job", "peer", "timer", "mixed", "", null, "nonsense"]) {
    for (const c of [null, 0, 1, 2, 5]) {
      assert.equal(tl(k, c), kindWord(k as any, c as any), `twin: kind=${String(k)} count=${String(c)}`);
      const w = kindWord(k as any, c as any);
      // awaitWord with no rows is kindWord with the count in front once it is known and plural
      const expect = (typeof c === "number" && c > 1 && w) ? c + " " + w : (k === "mixed" ? (c ? String(c) : "") : w);
      assert.equal(awaitWord(k as any, c as any, []), expect, `awaitWord: kind=${String(k)} count=${String(c)}`);
    }
  }
  assert.equal(tlSuffix("job", 1), " watch");
  assert.equal(tlSuffix("mixed", 4), " 4", "the lane badge shows a mixed wait's count");
  assert.equal(tlSuffix("mixed", null), "");
  assert.equal(tlSuffix(null, 3), "", "an older kernel ships no kind → plain Awaiting, as before");
});

test("the card caption stands down under rows of ANY kind, and still speaks when the kernel names none", () => {
  const under = spinFor({ awaiting: { why: "1 background agent still working", kind: "agents", count: 1, items: [agent("a")] }, column: "working", sessState: "quiet" }, false, false);
  assert.equal(under.caption, null, "the pill + its open list are the one awaiting read (the 2026-08-23 rule, now for every enumerable wait)");
  const alone = spinFor({ awaiting: { why: "waiting on the test suite", kind: "task", items: [] }, column: "working" }, false, false);
  assert.equal(alone.caption, "Waiting on the test suite", "a judge stamp names no rows → the caption is the only place its why shows");
});

// --- the chat statusline chip -------------------------------------------------------------------------
test("the Awaiting chip is a BUTTON on the stable statusline delegate, acknowledged, that opens the box", () => {
  const branch = RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0];
  // the chip itself is the SHARED status chip since T322b (status-chip.ts): the bar asks for the button form and adds its own class
  assert.match(branch, /const chip = statusChip\(chipWords\(s\.status\), "button"\) as HTMLButtonElement;/);
  assert.match(branch, /chip\.classList\.add\("chip-btn"\);/);
  assert.match(branch, /chip\.type = "button";/);
  assert.match(branch, /chip\.dataset\.act = "awaitingChip";/, "keyed for the delegate — never a listener on the rebuilt node");
  // the tooltip: the per-kind breakdown, the kernel's why, and what the click does (setTip, one line each)
  assert.match(branch, /setTip\(chip, \[awaitBreakdown\(chipItems\), s\.status\.awaitingWhy \|\| "idle, waiting on background work it dispatched",\s*\n\s*"click to see what it's waiting on"\]\.filter\(Boolean\)\.join\("\\n"\)\);/);
  assert.doesNotMatch(branch, /chip\.title =/, "styled tip only — never the native title beside it");
  // installed ONCE on #statusline (updateStatusline rebuilds its children every push); the handler opens the
  // box's own fold state, re-renders it, and scrolls it into view
  assert.match(RENDER, /const sl = document\.getElementById\("statusline"\);\s*\n\s*if \(!sl\) return;\s*\n\s*delegate\(sl, \{\s*\n\s*"awaitingChip": \(\) => \{\s*\n\s*if \(!activeId\) return;\s*\n\s*openFolds\.add\("bgfold:" \+ activeId\);[^\n]*\n\s*renderBgTasks\(\);\s*\n\s*document\.getElementById\("bg-tasks"\)\?\.scrollIntoView\(\{ block: "nearest" \}\);/);
  // a button's UA chrome is reset so it wears the chip exactly; hover/active feedback; the .romp-acted pulse is the delegate's
  assert.match(STYLES, /button\.chip \{ font-family: inherit; line-height: normal; border: 0; cursor: pointer;/);
  assert.match(STYLES, /button\.chip:hover \{ filter: brightness\(1\.08\); \}/);
  assert.match(STYLES, /\.romp-acted \{ animation: romp-acted-pulse/);
});

test("the chip's label: a single named peer keeps its coloured name; every other wait wears awaitWord", () => {
  // the rule moved to status-chip.ts with T322b (the tag overview's rows wear the same chip); the bar builds from it
  const CHIP = W("status-chip.ts");
  assert.match(CHIP, /const word = awaitWord\(st\.awaitingKind, st\.awaitingCount, items\);/);
  assert.match(CHIP, /if \(peers\.length && groupRows\(items\)\.every\(\(g\) => g\.kind === "peer"\)\) \{/, "the name path only when every row is a peer — a peer beside an agent is a mixed wait");
  assert.match(CHIP, /return \{ state, text: head \+ " " \+ \(word \|\| peers\.length \+ " peers"\), peer: null \};/);
  assert.match(CHIP, /return \{ state, text: head \+ \(word \? " " \+ word : ""\), peer: null \};/);
  assert.match(RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0], /statusChip\(chipWords\(s\.status\), "button"\)/);
});

// --- the box: rows grouped by kind, per-kind affordances ------------------------------------------------
test("the box groups the rows by kind, headers only when more than one group shows, tracked services trailing", () => {
  // since 2026-09-06 the one renderer is renderBgTasks (renderAwaitWhy folded into it — see the
  // "one presentation" tests below); the grouping is unchanged
  const body = RENDER.split("function renderBgTasks(")[1].split("\nfunction ")[0];
  assert.match(body, /const groups = groupRows\(items\);/);
  assert.match(body, /const leftovers = tasks\.filter\(\(t\) => !itemIds\.has\(t\.id\)\);/, "tracked tasks the wait does not name still list");
  assert.match(body, /const headers = sections\.length >= 2;/, "the sections: the kinds the rows bring, and the kinds only a kept row brings (T394)");
  assert.match(body, /if \(headers\) \{ const gh = el\("div", "bg-group-head"\); gh\.textContent = GROUP_TITLE\[g\.kind\] \|\| "Other"; list\.appendChild\(gh\); \}/);
  // no trailing section for the tracked tasks the wait does not name (T394, the user 2026-09-12: its title said nothing of why
  // the rows were there): each lists in its KIND's section, after the awaited rows, dimmed, the judge's verdict as a suffix
  assert.doesNotMatch(RENDER, /BG_LEFTOVER_TITLE|"Also running"|"Background tasks"/, "no section of its own, under either of its old names");
  assert.match(body, /for \(const row of kept\) if \(row\.kind === g\.kind\) list\.appendChild\(bgRow\(row, sid\)\);/);
  // the header: mixed → "Awaiting <n> · <breakdown of every listed row, then the kept subset>"; one kind → the sentence as before, the kept count after it
  assert.match(body, /\} else if \(groups\.length > 1\) \{\s*\n\s*lab\.textContent = "Awaiting " \+ word \+ " · " \+ listBreakdown\(counted, keptN\);/);
  assert.match(body, /lab\.textContent = "Awaiting" \+ \(word \? " " \+ word : ""\) \+ " · " \+ why\.replace\(\/\^\(waiting on\|awaiting\)\\s\+\/i, ""\) \+ \(kept\.length \? " · " \+ listBreakdown\(counted, keptN\) : ""\);/);
  // the no-rows fallback still expands to the full sentence — never a dead end
  assert.match(body, /if \(!groups\.length && !leftovers\.length\) \{[\s\S]*?const w = el\("div", "bg-await-why"\); w\.textContent = why;/);
  // the header vocabulary is .bg-status's — the notice SOURCE-label rung (0.72em uppercase, 2026-09-08; was 10px), dim
  assert.match(STYLES, /\.bg-group-head \{ flex: 0 0 auto; padding: 6px 9px 2px; font-size: 0\.72em; text-transform: uppercase; letter-spacing: \.06em; font-weight: 600; color: var\(--dim\); \}/);
});

test("per-kind affordances on ONE row shape: agent → arrow + Stop; command → output fold + Stop; watch → armed-since + Cancel", () => {
  const spec = RENDER.split("function awaitRowSpec(")[1].split("\nfunction ")[0];
  // (2026-09-10: Stop's handle is computed once above the kind branches — the tracked task's id while it runs,
  // else the row's own id when the kernel marks it stoppable — so the branches carry the one `stopId`)
  assert.match(spec, /const stopId = running \? tracked!\.id : \(it\.stoppable && id \? id : null\);/);
  assert.match(spec, /if \(it\.kind === "agents"\) \{[\s\S]*?agentId: it\.agentId \|\| \(tracked && tracked\.agentId\) \|\| null[\s\S]*?stopId, command[\s\S]*?output: null \};/,
    "an agent row: the arrow's id, Stop while its launch is live, NO output tail (its file is the transcript)");
  assert.match(spec, /if \(it\.kind === "commands"\) \{[\s\S]*?stopId, command[\s\S]*?output: tracked \? \(tracked\.output \|\| "\(no output captured\)"\) : null \};/);
  assert.match(spec, /if \(it\.kind === "watches"\) \{[\s\S]*?status: "armed", caption: "armed"[\s\S]*?watchId: it\.watchId \|\| null, command: it\.detail \|\| null \};/);
  assert.match(spec, /if \(it\.kind === "peer"\) \{[\s\S]*?peer: peerByName\.get\(it\.label \|\| ""\) \|\| null \};/);
  const row = RENDER.split("function bgRow(")[1].split("\nfunction ")[0];
  assert.match(row, /if \(t\.agentId\) \{[\s\S]*?const open = agentOpenButton\(t\.agentId, null, sid\);\s*\n\s*open\.classList\.add\("bg-open-agent"\);/);
  assert.match(row, /if \(t\.since && t\.since > 0\) \{[\s\S]*?w\.dataset\.since = String\(t\.since\);[\s\S]*?workingFor\(Date\.now\(\) \/ 1000 - t\.since\)/, "how long the row has been waited on, from its OWN event time");
  assert.match(row, /if \(t\.watchId\) \{[\s\S]*?cancel\.dataset\.act = "bg-cancel-watch"; cancel\.dataset\.id = t\.watchId;[\s\S]*?cancel\.textContent = "Cancel"; setTip\(cancel, "cancel this watch"\);/);
  assert.match(row, /const foldable = !!\(t\.command \|\| t\.output\);/);
  assert.match(row, /if \(foldable\) \{ rh\.dataset\.act = "bg-toggle"; rh\.dataset\.id = t\.id; \}/, "a row with nothing to unfold is not a toggle");
  // the per-row clocks tick with the statusline timer (the box re-renders only on new fields)
  assert.match(RENDER, /document\.querySelectorAll<HTMLElement>\("#bg-tasks \.bg-since\[data-since\]"\)/);
  // armed watches and peer/timer waits wear the await-green dot: nothing is computing HERE
  assert.match(STYLES, /\.bg-task\.bg-armed, \.bg-task\.bg-waiting \{ --bgt: var\(--st-awaitbg-bg\); \}/);
});

test("Cancel rides the box's stable delegate, acknowledges, and reaches the kernel's one cancel_watch", () => {
  assert.match(RENDER, /"bg-cancel-watch": \(el\) => \{[\s\S]*?btn\.disabled = true; btn\.textContent = "Cancelling…";[\s\S]*?vscodeApi\?\.postMessage\(\{ type: "cancelWatch", id: activeId, watchId: id \}\);/);
  // kernel: the SAME cancel_watch `romp watch --cancel` and POST /watch {"cancel"} reach; LOUD on a miss
  assert.match(KERNEL, /if msg and msg\.get\("type"\) == "cancelWatch" and msg\.get\("watchId"\):/);
  assert.match(KERNEL, /if not cancel_watch\(str\(msg\["watchId"\]\)\.strip\(\)\):\s*\n\s*client\["send"\]\(json\.dumps\(\{"type": "warn",/);
  // a PR watch has no early-retire path today → the kernel ships no watchId → no button (bgRow keys on it)
  assert.match(KERNEL, /a PR watch has no cancel path \(nothing retires a pr-watch early today\), so\s*\n\s*it carries no watchId and the box offers no button for it/);
});

test("the box re-renders on a rows change like any other awaiting field, and every surface ships the rows", () => {
  const key = RENDER.split("function awaitKey(")[1].split("\n}")[0];
  assert.ok(key.includes("st.awaitingItems"), "awaitingItems is in the await key");
  // pins changed 2026-09-06: the session-scoped surfaces ship the rows in BOTH turn states (the wait's own rows
  // idle-awaiting, everything in flight otherwise — _awaiting_items_payload), not gated on awaiting_why
  assert.match(KERNEL, /_aw_items = _awaiting_items_payload\(_aw, sid, sess\["path"\], live_map\)[\s\S]*?"awaitingItems": _aw_items,/, "chat status");
  assert.match(KERNEL, /"awaitingItems": \(_awaiting_items_payload\(_aw_bg, sid, s\["path"\], live_map\) if live else \[\]\),/, "timeline lane");
  assert.match(KERNEL, /"items": await_items,/, "the goal card");
  assert.match(KERNEL, /"items": list\(items or \[\]\),/, "the placeholder card");
});

// --- the feed pill ------------------------------------------------------------------------------------
test("the feed pill shows for ANY wait with rows, words itself by the same rule, and lists the rows grouped", () => {
  const sec = FEED.split("function applySections(")[1].split("\nfunction ")[0];
  assert.match(sec, /const awItems: AwaitRow\[\] = \(\(it\.awaiting && it\.awaiting\.items\) \|\| \[\]\)\.filter\(\(r\) => r && r\.kind\);/);
  assert.match(sec, /const taskRows: AwaitRow\[\] = awItems\.length \? awItems\s*\n\s*: taskList\.map\(\(d\) => \(\{ kind: ROW_KIND_OF_LEGACY\[awKind\] \|\| "commands", label: d \}\)\);/,
    "an older kernel's descriptions read as rows of the legacy kind's group");
  assert.match(sec, /const hasTasks = taskRows\.length > 0;/);
  assert.match(sec, /taskBtn\.style\.display = hasTasks \? "" : "none";/, "rows of any kind → the pill; no rows → none (the caption speaks)");
  assert.match(sec, /const pillWord = awaitWord\(awKind, \(it\.awaiting && it\.awaiting\.count\) \?\? taskRows\.length, taskRows\);/);
  assert.match(sec, /if \(pillPeers\.length === 1 && taskRows\.every\(\(r\) => r\.kind === "peer"\)\) \{/, "a single named peer → its name in identity colour");
  // the expansion: grouped, dim headers only when more than one group, labels only (the chat box carries the controls)
  assert.match(sec, /if \(choice === "tasks"\) \{[\s\S]*?const groups = groupRows\(taskRows\);[\s\S]*?if \(groups\.length > 1\) \{ const gh = el\("div", "ftask-group"\); gh\.textContent = GROUP_TITLE\[g\.kind\] \|\| "Other"; cl\.appendChild\(gh\); \}/);
  assert.match(sec, /\} else txt\.textContent = r\.label \|\| r\.kind;/);
  assert.doesNotMatch(sec.split('if (choice === "tasks") {')[1].split("return;")[0], /bg-stop|Cancel|agentOpenButton/, "labels only on the card");
  assert.match(FEEDCSS, /\.ftask-group \{ font-size: 0\.72em; text-transform: uppercase; letter-spacing: 0\.06em; color: var\(--dim\); margin: 4px 0 1px; \}/);
});

test("every row — agent, command, watch, peer — is ONE line: label, then arrow · elapsed · status · Stop/Cancel · caret, inline (the user 2026-09-05)", () => {
  // The cluster used to sit at the row's right edge (the label grew to fill); on a wide desktop the user
  // missed it. One row builder (bgRow) serves every kind, so the DOM order pinned here IS the reading
  // order for all of them, and styles.css's .bg-sum (0 1 auto, min-width 0) keeps the cluster right after
  // the label — bg-tasks-layout.test.ts pins the CSS half.
  const row = RENDER.split("function bgRow(")[1].split("\nfunction ")[0];
  const at = (s: string) => { const i = row.indexOf(s); assert.ok(i >= 0, "found " + s); return i; };
  const order = [at('el("span", "bg-sum")'), at('open.classList.add("bg-open-agent")'), at('el("span", "bg-since")'),
                 at('el("span", "bg-status")'), at('el("button", "bg-stop")'), at('el("button", "bg-stop bg-cancel")'),
                 at('el("span", "bg-caret")')];
  assert.deepEqual([...order].sort((a, b) => a - b), order, "label → arrow → elapsed → status → Stop → Cancel → caret");
  assert.match(STYLES, /\.bg-sum \{ flex: 0 1 auto; min-width: 0;/);
});

// --- one presentation in both turn states (the user 2026-09-06) -------------------------------------------
// Seen live: a session with two background agents and a background command showed the grouped rows while
// idle ("Awaiting 3 · 2 agents · 1 command", the idle note); the moment the user sent a message the view
// vanished, and it came back when the turn ended. The kernel shipped the rows only while idle (alongside the
// chip's idle-only Awaiting), so mid-turn the client fell to the legacy "N background tasks" list built from
// s.bgTasks — two presentations of one set of facts, swapped at every turn boundary. Now the rows ride in both
// states and ONE renderer draws them; only the header follows awaitingWhy (idle-and-waiting ⇔ the chip's
// Awaiting). The chip itself still flips exactly when it did.
test("ONE renderer: the grouped rows render whenever rows exist, idle or not; the legacy count list and its words are gone", () => {
  const body = RENDER.split("function renderBgTasks(")[1].split("\nfunction ")[0];
  assert.ok(!RENDER.includes("function renderAwaitWhy"), "the two-branch split is gone — one code path");
  assert.doesNotMatch(body, /background tasks/i, "the legacy header string is gone from the renderer");
  assert.doesNotMatch(RENDER, /count \+ " background tasks"/);
  assert.doesNotMatch(RENDER, /"Background task · "/);
  // the render gate is CONTENT: rows (awaitingItems, shipped in both states), a wait's why, or tracked tasks
  assert.match(body, /const items = \(\(s && s\.status\.awaitingItems\) \|\| \[\]\)\.filter\(\(it\) => it && it\.kind\);/);
  assert.match(body, /if \(!s \|\| !activeId \|\| \(!why && !items\.length && !tasks\.length\)\) \{ host\.style\.display = "none"; host\.classList\.remove\("bg-awaited"\); return; \}/);
  // the same row renderer for every row in either state — tracked tasks still lend the command rows their
  // output tail and Stop handle (awaitRowSpec's `tracked`), and list on their own when the wait names none
  assert.match(body, /const taskById = new Map<string, BgTask>\(tasks\.map\(\(t\) => \[t\.id, t\]\)\);/);
  assert.match(body, /for \(const it of g\.rows\) \{\s*list\.appendChild\(bgRow\(awaitRowSpec\(it, taskById\.get\(it\.id \|\| ""\), peerByName\), sid\)\);/);   // the loop is a block since 2026-09-10 (each agent row's nested waits follow it)
  assert.match(body, /const kept = leftovers\.map\(\(t\) => taskRowSpec\(t, awaited\.has\(t\.id\), services\.has\(t\.id\)\)\);/, "the tracked tasks the wait does not name, as rows of their kind, the judge's verdict riding in (T394)");
  // the awaited outline keys on the wait / the awaited ids' presence as before — never the chip state
  assert.match(body, /host\.classList\.toggle\("bg-awaited", !!why \|\| tasks\.some\(\(t\) => awaited\.has\(t\.id\)\)\);/);
  assert.doesNotMatch(body, /status\.state/, "nothing in the renderer reads the chip state");
});

test("the header follows the wait: idle → 'Awaiting …' + the idle note; working → 'In the background · <breakdown>' and NO note", () => {
  const body = RENDER.split("function renderBgTasks(")[1].split("\nfunction ")[0];
  assert.match(body, /if \(why\) \{[\s\S]*?const word = awaitWord\(s\.status\.awaitingKind, s\.status\.awaitingCount, items\);/, "idle: today's label, agreeing in number with the chip");
  assert.match(body, /\} else \{[\s\S]*?lab\.textContent = "In the background · " \+ listBreakdown\(counted, keptN\);/,
    "working: the same rows, worded as what they are; the header counts every top-level row the list shows, then the kept subset (T394)");
  // the idle note is appended under a wait only — once at the end of the list, once in the no-rows fallback
  assert.match(body, /if \(why\) list\.appendChild\(bgIdleNote\(\)\);/);
  assert.match(body, /det\.appendChild\(bgIdleNote\(\)\);/);
  assert.equal((body.match(/bgIdleNote\(\)/g) || []).length, 2, "no other note anywhere in the renderer");
  assert.doesNotMatch(RENDER, /keeps working meanwhile/, "the working-state sentence is gone: 'In the background' says it");
  assert.match(RENDER, /function bgIdleNote\(\): HTMLElement \{[\s\S]*?"The session is idle until this finishes; it picks back up on its own when the result lands\."/);
  // the header dot: await-green under a wait (like the chip), else the worst tracked status — a failed task
  // stays glanceable while collapsed, running-yellow otherwise (never completed-blue for a box of live rows)
  assert.match(body, /const head = el\("div", "bg-fold-head " \+ \(why \? "bg-await" : "bg-" \+ worst\) \+ \(open \? " open" : ""\)\);/);
  assert.match(body, /const worst = tasks\.reduce\(\(w, t\) => \(BG_RANK\[t\.status\] \|\| 0\) > \(BG_RANK\[w\] \|\| 0\) \? t\.status : w, tasks\.length \? \(tasks\[0\]\.status \|\| "running"\) : "running"\);/);
  // the words, EXECUTED: the same rows word both headers, singular and plural
  const rows = [agent("a"), agent("b"), command("c")];
  assert.equal("Awaiting " + awaitWord("mixed", 3, rows) + " · " + awaitBreakdown(rows), "Awaiting 3 · 2 agents · 1 command");
  assert.equal("In the background · " + awaitBreakdown(rows), "In the background · 2 agents · 1 command");
  assert.equal("In the background · " + awaitBreakdown([agent("a")]), "In the background · 1 agent");
  assert.equal("In the background · " + awaitBreakdown([command("c"), command("d")]), "In the background · 2 commands");
  assert.equal("In the background · " + awaitBreakdown([watch("w")]), "In the background · 1 watch", "an armed watch shows mid-turn too (the 2026-08-30 rule, through the rows)");
  assert.equal("In the background · " + awaitBreakdown([{ kind: "commands", id: "b1", label: "serve the docs" }]), "In the background · 1 command");
});

test("the box's fold state survives the idle↔working flip: the renderer only READS the fold; the two clicks are its only writers", () => {
  // 2026-09-08 (the notice-vocabulary pass): the fold lives in openFolds under "bgfold:<sid>" — the ONE fold store
  // (bgFoldOpen was one of four); the renderer reads, the header toggle and the chip click write
  const body = RENDER.split("function renderBgTasks(")[1].split("\nfunction ")[0];
  assert.match(body, /const open = openFolds\.has\("bgfold:" \+ sid\);/);
  assert.doesNotMatch(body, /openFolds\.(add|delete|clear)\(/, "the renderer never writes the fold state");
  const writers = (RENDER.match(/openFolds\.(add|delete)\("bgfold:" \+ [^)]*\)/g) || []).sort();
  assert.deepEqual(writers, ['openFolds.add("bgfold:" + activeId)', 'openFolds.add("bgfold:" + id)', 'openFolds.delete("bgfold:" + id)'],
    "the header toggle and the chip click, nothing else — a status-only frame that flips awaitingWhy re-renders through awaitKey and finds the fold as it was");
  const key = RENDER.split("function awaitKey(")[1].split("\n}")[0];
  assert.ok(key.includes("st.awaitingWhy") && key.includes("st.awaitingItems"), "the flip and the rows both re-render the box");
  // …and the kernel ships the SAME rows in both states, so only the header changes on the flip
  assert.match(KERNEL, /def _awaiting_live_rows\(sid, path, live\):/);
});

// --- vocabulary: the plain words everywhere, and no card moves --------------------------------------------
test("the user-visible words are agent / command / watch / <peer> / timer; the kernel's why sentences match", () => {
  assert.match(KERNEL, /return "%d background command%s" % \(n, "" if n == 1 else "s"\)/);
  assert.match(KERNEL, /return "%d armed watch%s" % \(n, "" if n == 1 else "es"\)/);
  assert.match(KERNEL, /why = \("waiting on a background command%s" % \(\(": " \+ d0\) if d0 else ""\) if n == 1 else\s*\n\s*"waiting on %d background commands%s"/);
  assert.match(KERNEL, /"waiting on %d armed watches — %s, …"/);
  assert.doesNotMatch(KERNEL.split("def _awaiting_from_items")[1].split("\ndef ")[0], /background task/, "the collapse word is gone from the derived sentences");
  // the state formula that MOVES a card/chip is untouched: awaiting still keys on awaiting_why alone
  assert.match(KERNEL, /"awaitingBg" if awaiting_why else "ready"\)/);
});

// --- nested waits (2026-09-10): what an awaited agent is ITSELF waiting on ---------------------------------
// Seen live: a session running ONE background agent read "Awaiting 2 · 1 agent · 1 command" — the command was
// a test chunk the AGENT had launched (Claude Code keeps one task list per session, so it registered under the
// parent). The user wants the top level to count what the session itself waits on ("Awaiting 1 · 1 agent") and
// the agent's row to show, nested beneath it, what the agent in turn waits on. The kernel ships that as `waits`
// on the agent row (kernel _awaiting_nest); every count and word here reads the top level alone.
const SUB_CMD: AwaitRow = { kind: "commands", id: "b_chunk", label: "run the parser test chunk", since: 130, stoppable: true };
const SUB_AGENT: AwaitRow = { kind: "agents", id: "tu_inner", label: "check the fixture loader", since: 140, agentId: "a2222222222222222",
                              waits: [{ kind: "commands", id: "b_inner", label: "grep the fixtures", since: 150 }] };
const NESTED: AwaitRow = { ...agent("map the parser"), waits: [SUB_CMD, SUB_AGENT] };

test("the top level counts what the SESSION waits on: one agent with nested waits reads 'agent' / '1 agent', never 2", () => {
  assert.equal(awaitWord("agents", 1, [NESTED]), "agent");
  assert.equal(awaitBreakdown([NESTED]), "1 agent", "the breakdown never descends into waits");
  assert.deepEqual(groupRows([NESTED]).map((g) => g.kind), ["agents"], "no Commands group from a nested command");
});

test("flattenWaits / rowIds reach every nested level, so a tracked task named by an agent's wait is not a leftover", () => {
  assert.deepEqual(flattenWaits([NESTED]).map((r) => r.id), ["tu_map the parser", "b_chunk", "tu_inner", "b_inner"]);
  assert.deepEqual([...rowIds([NESTED])].sort(), ["b_chunk", "b_inner", "tu_inner", "tu_map the parser"]);
  assert.deepEqual(flattenWaits(null), []);
});

test("waitsNote: one wait → its label; several → the breakdown; deeper levels are counted, not dropped", () => {
  assert.equal(waitsNote(SUB_AGENT), "grep the fixtures", "a single nested wait is named");
  assert.equal(waitsNote(NESTED), "1 agent · 2 commands", "the agent's own command, the nested agent and ITS command — the level the box does not draw still counts");
  assert.equal(waitsNote(SUB_CMD), "");
  assert.equal(waitsNote(null), "");
  assert.equal(waitsNote({ kind: "agents", id: "x", waits: [{ kind: "commands", id: "y" }] }), "command", "a label-less wait falls to its kind's word");
});

test("agentRowOf finds an agent at the top level or nested under another", () => {
  assert.equal(agentRowOf([NESTED], "a0123456789abcdef"), NESTED);
  assert.equal(agentRowOf([NESTED], "a2222222222222222"), SUB_AGENT);
  assert.equal(agentRowOf([NESTED], "a9999999999999999"), undefined);
  assert.equal(agentRowOf([NESTED], null), undefined);
});

test("the subagent viewer's header tail reads the PARENT's rows: '· waiting on <label>' while running, nothing finished", () => {
  assert.equal(subWaitTail(true, [NESTED], "a2222222222222222"), "waiting on grep the fixtures");
  assert.equal(subWaitTail(true, [NESTED], "a0123456789abcdef"), "waiting on 1 agent · 2 commands");
  assert.equal(subWaitTail(false, [NESTED], "a2222222222222222"), "", "a finished agent waits on nothing, whatever a stale row says");
  assert.equal(subWaitTail(true, [agent("plain")], "a0123456789abcdef"), "", "no waits → no tail");
  assert.match(RENDER, /const tail = subWaitTail\(s\.sub\.running, liveSession\(s\.sub\.parentId\)\?\.status\.awaitingItems, s\.sub\.agentId\)/,
               "renderSubHead reads the parent session's awaitingItems — the same rows the box draws");
  assert.match(RENDER, /if \(a && a\.sub && a\.sub\.parentId === sid\) renderSubHead\(\);/, "the header re-renders when the parent's awaited fields change");
  assert.equal((RENDER.match(/if \(awaitKey\(s\.status\) !== before\) awaitChanged\(msg\.id\);/g) || []).length, 3,
               "every status-bearing frame path (full, tail, status-only) goes through awaitChanged");
  assert.ok(STYLES.includes(".sub-head-waits {"), "the tail's own class");
});

test("the box draws an agent's waits as indented sub-rows in the SAME row vocabulary, 'waiting on' leading the first", () => {
  // the sub-row loop sits under each agent row, inside the group loop — never a group of its own
  assert.match(RENDER, /const waits = \(it\.waits \|\| \[\]\)\.filter\(\(w\) => w && w\.kind\);\s*waits\.forEach\(\(w, i\) => \{\s*const spec = awaitRowSpec\(w, taskById\.get\(w\.id \|\| ""\), peerByName\);\s*spec\.sub = i === 0 \? "first" : "rest";\s*spec\.deeper = waitsNote\(w\) \|\| null;\s*list\.appendChild\(bgRow\(spec, sid\)\);/,
               "each wait is a bgRow from the same awaitRowSpec (dot · label · elapsed · STATUS · Stop), marked sub");
  assert.match(RENDER, /sub\?: "first" \| "rest" \| null;/, "the spec's sub marker");
  assert.match(RENDER, /const on = el\("span", "bg-waits-on" \+ \(t\.sub === "first" \? "" : " bg-waits-blank"\)\);\s*on\.textContent = t\.sub === "first" \? "waiting on" : "";/,
               "the small dim label on the first sub-row; a blank twin on the rest so the dots align");
  assert.match(RENDER, /\(t\.sub \? " bg-sub" : ""\)/, "the sub-row class (the indent)");
  assert.match(RENDER, /dp\.textContent = "· waiting on " \+ t\.deeper;/, "a sub-row with waits of its own counts them in its label");
  assert.ok(STYLES.includes(".bg-task.bg-sub > .bg-head { padding-left: 24px; }"), "indented under the agent");
  assert.match(STYLES, /\.bg-waits-on \{[^}]*font-size: 0\.72em;[^}]*text-transform: uppercase;/, "the label rung .bg-status / .bg-group-head wear — no new font size");
  assert.match(STYLES, /\.bg-deeper \{[^}]*font-size: 0\.82em;/, "the meta rung, like .bg-since");
  assert.doesNotMatch(STYLES.match(/\.bg-waits-on \{[^}]*\}/)![0], /#[0-9a-fA-F]{3,6}/, "tokens, never hex");
});

test("the header and the chip count the top level only: the breakdown reads `items`, and nested ids still keep their tracked task from listing twice as a leftover of its kind", () => {
  assert.match(RENDER, /lab\.textContent = "Awaiting " \+ word \+ " · " \+ listBreakdown\(counted, keptN\);/, "the mixed header's breakdown is every listed row by kind, then how many wear the verdict (T394)");
  assert.match(RENDER, /const word = awaitWord\(s\.status\.awaitingKind, s\.status\.awaitingCount, items\);/, "the header word: top-level rows + the kernel's top-level count");
  assert.match(RENDER, /const itemIds = rowIds\(items\);/, "a task an agent's wait names is named, not a leftover");
});

test("Stop rides a row the kernel marks stoppable even with no tracked task — a subagent's own command is never tracked", () => {
  assert.match(RENDER, /const stopId = running \? tracked!\.id : \(it\.stoppable && id \? id : null\);/);
  assert.match(KERNEL, /"stoppable": True\}\s*# a lifecycle-set task: stop_task resolves its id/, "the kernel marks lifecycle-set rows");
  assert.match(KERNEL, /def _awaiting_nest\(agents, commands, cmd_owner, path\):/, "the kernel's nesting step");
  assert.match(KERNEL, /agents, commands = _awaiting_nest\(agents, commands, cmd_owner, path\)/, "…applied to the live rows both turn states read");
});

test("the feed pill's list nests the same way: indented sub-rows, 'waiting on' on the first, deeper waits counted", () => {
  assert.match(FEED, /const taskRow = \(r: AwaitRow, sub: "first" \| "rest" \| null\): HTMLElement => \{/);
  assert.match(FEED, /\(\(r\.waits \|\| \[\]\)\.filter\(\(w\) => w && w\.kind\)\)\.forEach\(\(w, i\) => cl\.appendChild\(taskRow\(w, i === 0 \? "first" : "rest"\)\)\);/);
  assert.match(FEED, /on\.textContent = sub === "first" \? "waiting on" : "";/);
  assert.match(FEED, /dp\.textContent = " · waiting on " \+ deeper;/);
  assert.ok(FEEDCSS.includes(".fcheck.ftask-sub { padding-left: 1.2em; }"));
  assert.match(FEEDCSS, /\.ftask-waits-on \{[^}]*font-size: 0\.72em;/, "the .ftask-group rung");
  assert.match(FEED, /const pillWord = awaitWord\(awKind, \(it\.awaiting && it\.awaiting\.count\) \?\? taskRows\.length, taskRows\);/, "the pill word reads the top-level rows + count");
});

test("the timeline's word reads the kernel's top-level count alone — a nested wait never reaches it", () => {
  assert.match(TL, /label: 'Awaiting' \+ tlAwaitSuffix\(s\.awaitingKind, s\.awaitingCount\)/);
  assert.doesNotMatch(TL, /awaitingItems|\.waits\b/, "the lane reads no rows at all: awaitingCount is computed from the top level in the kernel");
});
