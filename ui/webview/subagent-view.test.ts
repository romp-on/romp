// Subagent transcripts (plans/subagent-transcripts.md, 2026-09-05): the UI half. The pure module
// (ids, labels, the running preview's rows, the elapsed clock) is exercised directly; the DOM wiring in
// render.ts has no jsdom harness, so — like every other webview test — it is pinned at the source level:
// the arrow appears only with an agentId, the preview lives INSIDE the tool turn (so a collapsed compact
// run hides it), the viewer is a peek tab through the chat's own derivation (chatVisible answers
// pinnedSubs), read-only, with a header back-link + pin, an error sentence, a truncated note.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { subTabId, isSubId, subParts, subLabel, gistLines, stepLines, stepsNote, agentFoldLabel, elapsedSince, subHeadParts, openIconSvg, pinIconSvg, SUB_SEP, PREVIEW_ROWS } from "../../ui/webview/subagent-view";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const PLACEHOLDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "pane-placeholder.ts"), "utf8");   // the empty pane's placeholder, by kind (T355)
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

// ── the pure module ─────────────────────────────────────────────────────────────────────────────
test("the viewer tab id is the parent id plus a COLON-FREE suffix, so hostOf(subId) === hostOf(parentId)", () => {
  const local = subTabId("11111111-2222-3333-4444-555555555555", "a1111111111111111");
  assert.equal(local, "11111111-2222-3333-4444-555555555555/agent/a1111111111111111");
  assert.ok(isSubId(local));
  assert.deepEqual(subParts(local), { parentId: "11111111-2222-3333-4444-555555555555", agentId: "a1111111111111111" });
  // federation: the parent id already carries "host:" — the first colon stays the host marker
  const remote = subTabId("TESTHOST:11111111-2222-3333-4444-555555555555", "a1111111111111111");
  assert.equal(remote.indexOf(":"), "TESTHOST".length, "the only colon is the host marker");
  assert.equal(subParts(remote)!.parentId, "TESTHOST:11111111-2222-3333-4444-555555555555");
  assert.ok(!SUB_SEP.includes(":"), "the suffix never introduces a colon");
  // not a viewer id
  assert.ok(!isSubId("11111111-2222-3333-4444-555555555555"));
  assert.equal(subParts("11111111-2222-3333-4444-555555555555"), null);
  assert.equal(subParts("/agent/a1"), null, "an empty parent is not a viewer id");
  assert.equal(subParts("x/agent/"), null, "an empty agent id is not a viewer id");
});

test("the tab label is the description (clipped), else the agent type, never the hex id", () => {
  assert.equal(subLabel({ description: "check the api tests", agentType: "general-purpose" }), "check the api tests");
  assert.equal(subLabel({ description: "", agentType: "Explore" }), "Explore");
  assert.equal(subLabel(null), "subagent");
  const long = subLabel({ description: "run the whole notes-api suite and write up every failure" });
  assert.ok(long.length <= 28 && long.endsWith("…"), long);
});

// The preview's rows come from the STEPS now (2026-09-05: the kernel ships every call as agentSteps and
// the fold lists them all, so the three-row preview is steps.slice(-3) client-side — the old
// `gist.recent` field is gone from the wire). The pins below moved from `recent` to `steps` for that.
const STEPS = [{ tool: "Read", desc: "/tmp/notes-api/tests/test_api.py" }, { tool: "Bash", desc: "run the api tests" },
               { tool: "Grep", desc: "def test_" }, { tool: "Read", desc: "/tmp/notes-api/api/notes.py" }];
const CLOCK = { calls: 12, since: "2026-09-05T10:00:20.000Z", last: "2026-09-05T10:00:58.000Z" };

test("gistLines: the newest THREE steps, newest LAST, the head vocabulary per row, count + elapsed trailing the last row only", () => {
  const now = Date.parse("2026-09-05T10:01:00.000Z");
  const lines = gistLines(STEPS, CLOCK, now);
  assert.equal(PREVIEW_ROWS, 3);
  assert.equal(lines.length, 3, "four steps → the newest three");
  assert.deepEqual(lines.map((l) => l.tool), ["Bash", "Grep", "Read"]);
  assert.equal(lines[0].meta, "", "only the last row carries the count/elapsed");
  assert.equal(lines[1].meta, "");
  assert.equal(lines[2].meta, "· 12 tool calls · 40s");
  // one call reads singular; a longer run reads m/s like the statusline timer
  assert.equal(gistLines([{ tool: "Read", desc: "x" }], { calls: 1, since: "2026-09-05T09:58:55.000Z" }, now)[0].meta, "· 1 tool call · 2m 5s");
  // no clock (the agent finished) / no steps → nothing to draw (the caller renders no preview)
  assert.deepEqual(gistLines(STEPS, null, now), [], "a finished agent has no preview");
  assert.deepEqual(gistLines([], CLOCK, now), []);
  assert.deepEqual(gistLines(null, CLOCK, now), []);
});

test("stepLines: EVERY step in order for the fold; the elapsed alone trails the last row while running, nothing when finished", () => {
  const now = Date.parse("2026-09-05T10:01:00.000Z");
  const running = stepLines(STEPS, CLOCK, now);
  assert.equal(running.length, 4);
  assert.deepEqual(running.map((l) => l.tool), ["Read", "Bash", "Grep", "Read"], "oldest first, newest LAST");
  assert.deepEqual(running.map((l) => l.meta), ["", "", "", "· 40s"], "the count lives in the fold label, not the row");
  const finished = stepLines(STEPS, null, now);
  assert.deepEqual(finished.map((l) => l.meta), ["", "", "", ""]);
  assert.equal(finished[1].desc, "run the api tests");
  assert.deepEqual(stepLines([], CLOCK, now), []);
  assert.deepEqual(stepLines(undefined, null, now), []);
});

test("stepsNote: names the calls the kernel's cap cut, singular handled, empty when every call is on the wire", () => {
  assert.equal(stepsNote(200, 257), "57 earlier calls not shown");
  assert.equal(stepsNote(200, 201), "1 earlier call not shown");
  assert.equal(stepsNote(4, 4), "");
  assert.equal(stepsNote(4, undefined), "");
  assert.equal(stepsNote(4, 3), "", "never negative");
});

test("agentFoldLabel: running 'prompt · N tool calls'; finished adds '· report · N lines'; zero calls omits the part; singulars", () => {
  assert.equal(agentFoldLabel({ stepsTotal: 12, reportLines: null }), "prompt · 12 tool calls");
  assert.equal(agentFoldLabel({ stepsTotal: 12, reportLines: 3 }), "prompt · 12 tool calls · report · 3 lines");
  assert.equal(agentFoldLabel({ stepsTotal: 0, reportLines: null }), "prompt");
  assert.equal(agentFoldLabel({ stepsTotal: 0, reportLines: 1 }), "prompt · report · 1 line");
  assert.equal(agentFoldLabel({ stepsTotal: 1, reportLines: 1 }), "prompt · 1 tool call · report · 1 line");
  assert.equal(agentFoldLabel({ reportLines: null }), "prompt", "no steps shipped at all reads like zero");
});

test("elapsedSince prints the statusline timer's shapes and is empty for an unreadable stamp", () => {
  const now = Date.parse("2026-09-05T12:00:00.000Z");
  assert.equal(elapsedSince("2026-09-05T11:59:30.000Z", now), "30s");
  assert.equal(elapsedSince("2026-09-05T11:57:55.000Z", now), "2m 5s");
  assert.equal(elapsedSince("2026-09-05T10:57:00.000Z", now), "1h 3m");
  assert.equal(elapsedSince("garbage", now), "");
  assert.equal(elapsedSince(null, now), "");
});

test("the header parts: type falls back to 'agent'; state is running|finished", () => {
  assert.deepEqual(subHeadParts({ agentType: "Explore" }, true), { type: "Explore", state: "running" });
  assert.deepEqual(subHeadParts(null, false), { type: "agent", state: "finished" });
});

test("both icons wear the house line-icon style: 16-unit viewBox, currentColor, stroke 1.4, round caps", () => {
  for (const svg of [openIconSvg(), pinIconSvg()]) {
    assert.match(svg, /viewBox="0 0 16 16"/);
    assert.match(svg, /stroke="currentColor"/);
    assert.match(svg, /stroke-width="1\.4"/);
    assert.match(svg, /stroke-linecap="round" stroke-linejoin="round"/);
    assert.doesNotMatch(svg, /#[0-9a-fA-F]{3,6}/, "no hardcoded colour — the button's CSS tints it");
  }
});

// ── render.ts wiring (source pins) ──────────────────────────────────────────────────────────────
test("the arrow rides the Agent/Task head whenever the event carries an agentId — running OR finished; the preview only with the clock AND a closed fold", () => {
  // the arrow: gated on agentId alone — a FINISHED agent (output landed, no clock) still carries it, so
  // its whole transcript stays one click away after the fact (the user asked, 2026-09-05)
  const block = (RENDER.match(/if \(\(ev\.name === "Task" \|\| ev\.name === "Agent"\) && ev\.agentId\) \{[\s\S]*?\n  \}\n  return turn;/) || [""])[0];
  assert.ok(block.length > 200, "found the arrow/preview block");
  assert.match(block, /head\.appendChild\(agentOpenButton\(ev\.agentId, ev\.uuid \|\| null, renderingOwnerSid \|\| renderingSid \|\| null\)\);/);
  assert.doesNotMatch(block.split("agentOpenButton")[0], /agentGist|agentRunning|ev\.output/, "nothing about run-state gates the arrow");
  // the preview (2026-09-05): shown while the kernel ships the clock (running) AND the head's fold is
  // CLOSED — an open fold lists every call, so the preview steps aside. The fold's state is read from
  // openFolds under the same fkey the fold persists with; no new state.
  assert.match(block, /if \(ev\.agentGist && !\(fkey && openFolds\.has\(fkey\)\)\) head\.insertAdjacentElement\("afterend", renderAgentGist\(ev\.agentSteps, ev\.agentGist\)\);/);
  // …and the CSS twin hides it the instant the fold opens, before the next push rebuilds the turn
  assert.match(CSS, /\.turn-tool\.fold-open > \.agent-preview \{ display: none; \}/);
  assert.match(RENDER, /const box = el\("div", "agent-gist agent-preview"\);/);
  // the running-dot rule the older pin holds is untouched: the kernel clears a running background
  // agent's output, so "no output" still reads as running
  assert.match(RENDER, /const agentRunning = \(ev\.name === "Task" \|\| ev\.name === "Agent"\) && !ev\.output && !ev\.isError;/);
});

test("the arrow is click-safe: a data-act on the stable body delegate, a setTip tooltip, the house icon", () => {
  assert.match(RENDER, /function agentOpenButton\(agentId: string, anchorUuid: string \| null, ownerSid: string \| null\): HTMLElement \{[\s\S]{0,600}?b\.dataset\.act = "openSubagent";[\s\S]{0,400}?b\.innerHTML = openIconSvg\(\);[\s\S]{0,200}?setTip\(b, "open transcript"\);/);
  assert.match(RENDER, /delegate\(document\.body, \{[\s\S]{0,600}?openSubagent: \(el\) => \{/);
  assert.match(RENDER, /subParent: \(el\) => \{[\s\S]{0,200}?setActive\(sid, el\.dataset\.uuid \|\| undefined\);/);
  assert.match(RENDER, /pinSubagent: \(el\) => \{[\s\S]{0,300}?assertPeekFor\(id\);/);
  assert.doesNotMatch(RENDER, /tool-open-agent"\)[\s\S]{0,300}?addEventListener\("click"/, "never a per-node click handler on a rebuilt head");
});

test("the preview renders INSIDE the tool turn in the fold-toggle's size — a collapsed compact run hides it; the collapsed line never draws it", () => {
  // (2026-09-05) the preview's rows come from the steps: renderAgentGist takes (steps, clock) and both it
  // and the fold's full list append rows through ONE builder, so the two read alike
  assert.match(RENDER, /function renderAgentGist\(steps: AgentGistRow\[\] \| undefined, g: AgentGist\): HTMLElement \{[\s\S]{0,300}?appendGistRows\(box, gistLines\(steps, g, Date\.now\(\)\)\);/);
  assert.match(RENDER, /function appendGistRows\(box: HTMLElement, lines: GistLine\[\]\): void \{[\s\S]{0,200}?const row = el\("div", "agent-gist-row"\);[\s\S]{0,400}?"agent-gist-tool"[\s\S]{0,200}?"agent-gist-desc"[\s\S]{0,200}?"agent-gist-meta"/);
  // the collapsed toolgroup line knows nothing of the gist — only the per-tool renderTool (run when expanded) does
  const group = (RENDER.match(/function renderToolGroup\([\s\S]*?\n\}\n/) || [""])[0];
  assert.ok(group.length > 200, "found renderToolGroup");
  assert.doesNotMatch(group, /agentGist|renderAgentGist/);
  assert.match(CSS, /\.agent-gist \{[^}]*font-size: 0\.86em;/);
  assert.match(CSS, /\.tool-fold-toggle \{[^}]*font-size: 0\.86em;/, "the SAME size the tool head already uses");
  assert.match(CSS, /\.agent-gist-desc \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
  assert.match(CSS, /\.agent-gist-meta \{[^}]*white-space: nowrap;/, "the trailing count/elapsed always fits — the desc shrinks");
});

test("the bg-tasks box puts the same arrow on an AGENT row only (shell rows keep their treatment)", () => {
  assert.match(RENDER, /if \(t\.agentId\) \{[\s\S]{0,500}?const open = agentOpenButton\(t\.agentId, null, sid\);\s*\n\s*open\.classList\.add\("bg-open-agent"\);/);
  assert.match(RENDER, /interface BgTask \{[^}]*agentId\?: string;/);
});

test("the viewer is a PEEK through the chat's own derivation: chatVisible answers pinnedSubs for a viewer id", () => {
  assert.match(RENDER, /function chatVisible\(id: string\): boolean \{[\s\S]{0,600}?if \(isSubId\(id\)\) return pinnedSubs\.has\(id\);/);
  assert.match(RENDER, /const pinnedSubs = new Set<string>\(\);/);
  // opening: a client-only pseudo-session in sessions/order, the kernel asked once, then setActive
  assert.match(RENDER, /function openSubagentView\(parentId: string, agentId: string, anchorUuid: string \| null\): void \{[\s\S]{0,1200}?vscodeApi\?\.postMessage\(\{ type: "openSubagent", id: parentId, agentId \}\);[\s\S]{0,200}?setActive\(id\);/);
  // the peek closes on the next activation (pruneSubViews in setActive), telling the kernel to stop
  assert.match(RENDER, /assertPeekFor\(id\);\s*\n\s*pruneSubViews\(id\);/);
  assert.match(RENDER, /function closeSubagentView\(id: string\): void \{[\s\S]{0,300}?postMessage\(\{ type: "closeSubagent", id: p\.parentId, agentId: p\.agentId \}\);[\s\S]{0,100}?pinnedSubs\.delete\(id\);[\s\S]{0,50}?dismissSession\(id, "close"\);/);
  assert.match(RENDER, /if \(id !== keep && isSubId\(id\) && !pinnedSubs\.has\(id\)\) closeSubagentView\(id\);/);
  // a pinned viewer does not survive a reload in this slice — stated where the set lives
  assert.match(RENDER, /a pinned viewer does NOT survive\s*\n?\s*\/\/ a reload in this slice/);
});

test("the viewer is READ-ONLY: the message box is hidden, send disabled, one dim statusline line, no meta menus", () => {
  assert.match(RENDER, /const viewer = !!s\.sub;[^\n]*\n\s*composer\.disabled = closed \|\| viewer;/);
  // ONE read-only cue: the message box (input + send) is hidden outright in the viewer; the statusline
  // line below carries the word — never both (the placeholder once doubled it, 2026-09-05)
  assert.match(RENDER, /if \(composerBox\) composerBox\.style\.display = viewer \? "none" : "";/);
  assert.doesNotMatch(RENDER, /Subagent transcript — read-only/);
  assert.match(RENDER, /if \(sendBtn\) sendBtn\.disabled = closed \|\| viewer;/);
  assert.match(RENDER, /if \(s\.sub\) \{[\s\S]{0,400}?ro\.textContent = "read-only · a subagent's transcript";[\s\S]{0,100}?return;/);
  // the tab: no drag (a reorder would post the id into the kernel's order), no rename menu, ✕ = Close tab
  assert.match(RENDER, /tab\.draggable = !s\.sub && !fedMissing && !isProvisionalId\(id\) && !settings\.tabsLocked;/);   // …nor on a page without its manager (2026-09-10), nor a create in flight (the chat split, 2026-09-11)
  assert.match(RENDER, /if \(!s\.sub\) tab\.addEventListener\("contextmenu"/);
  assert.match(RENDER, /close\.title = dead \|\| s\.sub \? "Close tab" : copies > 1 \? "End session \(it is the one session, shown in every group it is tagged with\)" : "End session";/);
  assert.match(RENDER, /if \(id && isSubId\(id\)\) \{ closeSubagentView\(id\); return; \}/);
});

test("the header: 'subagent of <parent>' links back to the launch (setActive + the head's uuid), type, state, pin", () => {
  assert.match(RENDER, /function renderSubHead\(\): void \{[\s\S]{0,2500}?kicker\.textContent = "subagent of";[\s\S]{0,600}?link\.dataset\.act = "subParent"; link\.dataset\.sid = s\.sub\.parentId;[\s\S]{0,100}?if \(s\.sub\.anchorUuid\) link\.dataset\.uuid = s\.sub\.anchorUuid;/);
  assert.match(RENDER, /link\.replaceChildren\(\.\.\.hostNameNodes\(parentName, s\.sub\.parentId\)\);/, "the house session-reference idiom");
  assert.match(RENDER, /const parts = subHeadParts\(s\.sub\.meta, s\.sub\.running\);/);
  assert.match(RENDER, /pin\.dataset\.act = "pinSubagent"; pin\.dataset\.id = s\.id;\s*\n\s*pin\.innerHTML = pinIconSvg\(\);/);
  assert.match(RENDER, /setTip\(pin, pinnedSubs\.has\(s\.id\) \? "kept — click to let this tab close on its own" : "keep this tab"\);/);
  // the truncated note, one line, only when the kernel cut the tail and there is no error
  assert.match(RENDER, /if \(s\.sub\.truncated && !s\.sub\.error\) \{[\s\S]{0,200}?note\.textContent = "earlier part not shown";/);
  // the header lives in #content and is removed for every real session
  assert.match(RENDER, /if \(!s \|\| !s\.sub\) \{ if \(host\) host\.remove\(\); return; \}/);
  assert.match(CSS, /#sub-head \{ position: sticky; top: 0;[^}]*font-size: 0\.82em;/);   // the notice META rung (2026-09-08; was 0.86em)
});

test("frames: events replace in place through appendActive (the chat's scroll rule); error → the sentence in the pane; loader first", () => {
  assert.match(RENDER, /else if \(m\.type === "subagent"\) applySubagentFrame\(m\);/);
  assert.match(RENDER, /function applySubagentFrame\(m: any\): void \{[\s\S]{0,1800}?if \(activeId === id\) \{[\s\S]{0,2600}?else appendActive\(\);\s*\n\s*renderSubHead\(\);/);
  // a same-length frame is NOT a no-op: the diff lowers v.rendered to the first changed event, and a slid
  // tail (the cap) or a flipped truncation flag is a full rebuild (review fold on #935, 2026-09-07)
  assert.match(RENDER, /const slid = prevEvents\.length > 0 && ident\(prevEvents\[0\]\) !== ident\(s\.events\[0\]\);/);
  assert.match(RENDER, /if \(slid \|\| prevTruncated !== s\.sub\.truncated\) \{\s*\n\s*v\.stale = true; v\.rendered = 0;/);
  assert.match(RENDER, /v\.rendered = Math\.min\(v\.rendered, idx\);/);
  // the FIRST content lands at the newest end like a fresh tab; later frames append and keep the reader's spot
  assert.match(RENDER, /const first = !v \|\| v\.rendered === 0;[\s\S]{0,2400}?if \(first\) \{ if \(v\) \{ v\.stick = true; v\.rendered = 0; \} showActive\(\); \}/);
  // a frame for a viewer that is gone tells the kernel to stop pushing
  assert.match(RENDER, /if \(!s \|\| !s\.sub\) \{ vscodeApi\?\.postMessage\(\{ type: "closeSubagent", id: parentId, agentId \}\); return; \}/);
  // the empty-transcript placeholder: error sentence (loud), else the romp loader until the first frame
  // the placeholder by kind (pane-placeholder.ts, T355): the error sentence, the loader while the frame is in flight
  assert.match(PLACEHOLDER, /if \(st\.sub\.error\) return "sub-error";[\s\S]{0,200}?if \(!st\.sub\.loaded\) return "sub-loading";/);
  assert.match(PLACEHOLDER, /case "sub-error":[\s\S]{0,200}?ph\.textContent = ctx\.text\.error \|\| "";/);
  assert.match(PLACEHOLDER, /case "sub-loading":[\s\S]{0,300}?ph\.appendChild\(ctx\.loader\("opening the agent's transcript…"\)\);/);
  assert.match(RENDER, /text: \{ error: s\.sub\?\.error, failedRevive: failedRevives\.get\(id\), stall: subagentStallText\(\), sessionName: s\.name \},/);
  assert.match(RENDER, /el, loader: rompLoaderInner, button: \(\) => document\.createElement\("button"\)/);
  // file/preview URLs bake the PARENT's id for a viewer
  assert.match(RENDER, /const subOf = subParts\(id\);\s*\n\s*if \(subOf\) renderingOwnerSid = subOf\.parentId;/);
});

test("the new CSS uses theme tokens only — no raw hex in the subagent rules", () => {
  const block = (CSS.match(/\/\* ── SUBAGENT TRANSCRIPTS[\s\S]*?\.sub-status-line \{[^}]*\}\n/) || [""])[0];
  assert.ok(block.length > 500, "found the subagent CSS block");
  assert.doesNotMatch(block, /#[0-9a-fA-F]{3,6}\b/);
  for (const tok of ["--dim", "--accent", "--accent-wash", "--bg", "--box-border", "--st-working-bg", "--st-ready-bg"]) {
    assert.ok(block.includes("var(" + tok + ")"), "uses " + tok);
  }
});
