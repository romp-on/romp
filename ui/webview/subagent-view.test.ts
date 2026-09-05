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
import { subTabId, isSubId, subParts, subLabel, gistLines, elapsedSince, subHeadParts, openIconSvg, pinIconSvg, SUB_SEP } from "../../ui/webview/subagent-view";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
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

test("gistLines: newest LAST, the head vocabulary per row, count + elapsed trailing the last row only", () => {
  const now = Date.parse("2026-09-05T10:01:00.000Z");
  const g = { recent: [{ tool: "Bash", desc: "run the api tests" }, { tool: "Grep", desc: "def test_" }, { tool: "Read", desc: "/tmp/notes-api/api/notes.py" }],
              calls: 12, since: "2026-09-05T10:00:20.000Z", last: "2026-09-05T10:00:58.000Z" };
  const lines = gistLines(g, now);
  assert.equal(lines.length, 3);
  assert.deepEqual(lines.map((l) => l.tool), ["Bash", "Grep", "Read"]);
  assert.equal(lines[0].meta, "", "only the last row carries the count/elapsed");
  assert.equal(lines[1].meta, "");
  assert.equal(lines[2].meta, "· 12 tool calls · 40s");
  // one call reads singular; a longer run reads m/s like the statusline timer
  assert.equal(gistLines({ recent: [{ tool: "Read", desc: "x" }], calls: 1, since: "2026-09-05T09:58:55.000Z" }, now)[0].meta, "· 1 tool call · 2m 5s");
  // more than three recent rows are clipped to the newest three (defensive: the kernel ships three)
  assert.equal(gistLines({ recent: [1, 2, 3, 4].map((i) => ({ tool: "T" + i, desc: "" })), calls: 4 }, now).map((l) => l.tool).join(","), "T2,T3,T4");
  // no gist / nothing recent → nothing to draw (the caller renders no box)
  assert.deepEqual(gistLines(null, now), []);
  assert.deepEqual(gistLines({ recent: [], calls: 0 }, now), []);
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
test("the arrow rides the Agent/Task head ONLY when the event carries an agentId, and the preview only with a gist", () => {
  assert.match(RENDER, /if \(\(ev\.name === "Task" \|\| ev\.name === "Agent"\) && ev\.agentId\) \{[\s\S]{0,700}?head\.appendChild\(agentOpenButton\(ev\.agentId, ev\.uuid \|\| null, renderingOwnerSid \|\| renderingSid \|\| null\)\);\s*\n\s*if \(ev\.agentGist\) head\.insertAdjacentElement\("afterend", renderAgentGist\(ev\.agentGist\)\);/);
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
  assert.match(RENDER, /function renderAgentGist\(g: AgentGist\): HTMLElement \{[\s\S]{0,300}?for \(const line of gistLines\(g, Date\.now\(\)\)\) \{/);
  assert.match(RENDER, /const row = el\("div", "agent-gist-row"\);[\s\S]{0,400}?"agent-gist-tool"[\s\S]{0,200}?"agent-gist-desc"[\s\S]{0,200}?"agent-gist-meta"/);
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

test("the viewer is READ-ONLY: composer and send disabled, its own placeholder, no meta menus in the statusline", () => {
  assert.match(RENDER, /const viewer = !!s\.sub;[^\n]*\n\s*composer\.disabled = closed \|\| viewer;/);
  assert.match(RENDER, /if \(viewer\) composer\.placeholder = "Subagent transcript — read-only";/);
  assert.match(RENDER, /if \(sendBtn\) sendBtn\.disabled = closed \|\| viewer;/);
  assert.match(RENDER, /if \(s\.sub\) \{[\s\S]{0,400}?ro\.textContent = "read-only · a subagent's transcript";[\s\S]{0,100}?return;/);
  // the tab: no drag (a reorder would post the id into the kernel's order), no rename menu, ✕ = Close tab
  assert.match(RENDER, /tab\.draggable = !s\.sub;/);
  assert.match(RENDER, /if \(!s\.sub\) tab\.addEventListener\("contextmenu"/);
  assert.match(RENDER, /close\.title = dead \|\| s\.sub \? "Close tab" : "End session";/);
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
  assert.match(CSS, /#sub-head \{ position: sticky; top: 0;[^}]*font-size: 0\.86em;/);
});

test("frames: events replace in place through appendActive (the chat's scroll rule); error → the sentence in the pane; loader first", () => {
  assert.match(RENDER, /else if \(m\.type === "subagent"\) applySubagentFrame\(m\);/);
  assert.match(RENDER, /function applySubagentFrame\(m: any\): void \{[\s\S]{0,1500}?if \(activeId === id\) \{[\s\S]{0,900}?else appendActive\(\);\s*\n\s*renderSubHead\(\);/);
  // the FIRST content lands at the newest end like a fresh tab; later frames append and keep the reader's spot
  assert.match(RENDER, /const first = !v \|\| v\.rendered === 0;[\s\S]{0,600}?if \(first\) \{ if \(v\) \{ v\.stick = true; v\.rendered = 0; \} showActive\(\); \}/);
  // a frame for a viewer that is gone tells the kernel to stop pushing
  assert.match(RENDER, /if \(!s \|\| !s\.sub\) \{ vscodeApi\?\.postMessage\(\{ type: "closeSubagent", id: parentId, agentId \}\); return; \}/);
  // the empty-transcript placeholder: error sentence (loud), else the romp loader until the first frame
  assert.match(RENDER, /\} else if \(s\.sub && s\.sub\.error\) \{[\s\S]{0,200}?ph\.textContent = s\.sub\.error;/);
  assert.match(RENDER, /\} else if \(s\.sub && !s\.sub\.loaded\) \{[\s\S]{0,300}?ph\.appendChild\(rompLoaderInner\("opening the agent's transcript…"\)\);/);
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
