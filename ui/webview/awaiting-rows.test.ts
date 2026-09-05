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
         type AwaitRow } from "./spin-caption";

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
  assert.match(branch, /const chip = el\("button", "chip chip-awaitingBg chip-btn"\) as HTMLButtonElement;/);
  assert.match(branch, /chip\.type = "button";/);
  assert.match(branch, /chip\.dataset\.act = "awaitingChip";/, "keyed for the delegate — never a listener on the rebuilt node");
  // the tooltip: the per-kind breakdown, the kernel's why, and what the click does (setTip, one line each)
  assert.match(branch, /setTip\(chip, \[awaitBreakdown\(chipItems\), s\.status\.awaitingWhy \|\| "idle, waiting on background work it dispatched",\s*\n\s*"click to see what it's waiting on"\]\.filter\(Boolean\)\.join\("\\n"\)\);/);
  assert.doesNotMatch(branch, /chip\.title =/, "styled tip only — never the native title beside it");
  // installed ONCE on #statusline (updateStatusline rebuilds its children every push); the handler opens the
  // box's own fold state, re-renders it, and scrolls it into view
  assert.match(RENDER, /const sl = document\.getElementById\("statusline"\);\s*\n\s*if \(!sl\) return;\s*\n\s*delegate\(sl, \{\s*\n\s*"awaitingChip": \(\) => \{\s*\n\s*if \(!activeId\) return;\s*\n\s*bgFoldOpen\.add\(activeId\);[^\n]*\n\s*renderBgTasks\(\);\s*\n\s*document\.getElementById\("bg-tasks"\)\?\.scrollIntoView\(\{ block: "nearest" \}\);/);
  // a button's UA chrome is reset so it wears the chip exactly; hover/active feedback; the .romp-acted pulse is the delegate's
  assert.match(STYLES, /button\.chip \{ font-family: inherit; line-height: normal; border: 0; cursor: pointer;/);
  assert.match(STYLES, /button\.chip:hover \{ filter: brightness\(1\.08\); \}/);
  assert.match(STYLES, /\.romp-acted \{ animation: romp-acted-pulse/);
});

test("the chip's label: a single named peer keeps its coloured name; every other wait wears awaitWord", () => {
  const branch = RENDER.split('state === "awaitingBg") {')[1].split("} else if")[0];
  assert.match(branch, /if \(chipPeers\.length && groupRows\(chipItems\)\.every\(\(g\) => g\.kind === "peer"\)\) \{/, "the name path only when every row is a peer — a peer beside an agent is a mixed wait");
  assert.match(branch, /\} else chip\.append\(chipWord \|\| chipPeers\.length \+ " peers"\);/);
  assert.match(branch, /\} else chip\.textContent = CHIP_LABEL\.awaitingBg \+ \(chipWord \? " " \+ chipWord : ""\);/);
});

// --- the box: rows grouped by kind, per-kind affordances ------------------------------------------------
test("the box groups the rows by kind, headers only when more than one group shows, tracked services trailing", () => {
  const body = RENDER.split("function renderAwaitWhy(")[1].split("\nfunction ")[0];
  assert.match(body, /const groups = groupRows\(items\);/);
  assert.match(body, /const leftovers = tasks\.filter\(\(t\) => !itemIds\.has\(t\.id\)\);/, "tracked tasks the wait does not name still list");
  assert.match(body, /const headers = groups\.length \+ \(leftovers\.length \? 1 : 0\) >= 2;/);
  assert.match(body, /if \(headers\) \{ const gh = el\("div", "bg-group-head"\); gh\.textContent = GROUP_TITLE\[g\.kind\] \|\| "Other"; list\.appendChild\(gh\); \}/);
  assert.match(body, /if \(headers\) \{ const gh = el\("div", "bg-group-head"\); gh\.textContent = "Background tasks"; list\.appendChild\(gh\); \}/);
  // the header: mixed → "Awaiting <n> · <breakdown>"; one kind → the sentence as before
  assert.match(body, /\} else if \(groups\.length > 1\) \{\s*\n\s*lab\.textContent = "Awaiting " \+ word \+ " · " \+ awaitBreakdown\(items\);/);
  // the no-rows fallback still expands to the full sentence — never a dead end
  assert.match(body, /if \(!groups\.length && !leftovers\.length\) \{[\s\S]*?const w = el\("div", "bg-await-why"\); w\.textContent = why;/);
  // the header vocabulary is .bg-status's (10px uppercase), dim
  assert.match(STYLES, /\.bg-group-head \{ flex: 0 0 auto; padding: 6px 9px 2px; font-size: 10px; text-transform: uppercase; letter-spacing: \.04em; font-weight: 600; color: var\(--dim\); \}/);
});

test("per-kind affordances on ONE row shape: agent → arrow + Stop; command → output fold + Stop; watch → armed-since + Cancel", () => {
  const spec = RENDER.split("function awaitRowSpec(")[1].split("\nfunction ")[0];
  assert.match(spec, /if \(it\.kind === "agents"\) \{[\s\S]*?agentId: it\.agentId \|\| \(tracked && tracked\.agentId\) \|\| null[\s\S]*?stopId: running \? tracked!\.id : null[\s\S]*?output: null \};/,
    "an agent row: the arrow's id, Stop while its launch is live, NO output tail (its file is the transcript)");
  assert.match(spec, /if \(it\.kind === "commands"\) \{[\s\S]*?stopId: running \? tracked!\.id : null[\s\S]*?output: tracked \? \(tracked\.output \|\| "\(no output captured\)"\) : null \};/);
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
  assert.match(KERNEL, /"awaitingItems": \(list\(\(_aw or \{\}\)\.get\("items"\) or \[\]\) if awaiting_why else \[\]\),/, "chat status");
  assert.match(KERNEL, /"awaitingItems": \(list\(\(_aw_bg or \{\}\)\.get\("items"\) or \[\]\) if awaiting_bg else \[\]\),/, "timeline lane");
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
