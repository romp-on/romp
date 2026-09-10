// THE notice (2026-09-08, the notice-vocabulary pass): everything that HAPPENED in a session — a background
// agent's report, a romp system notice, a compaction, a clear, an API error, a peer's mail, a teammate's message,
// an interrupt, an effort change, a model swap, the system context, the to-do list — is ONE element built by
// render.ts notice(spec). It replaced noticeCard and ~20 bespoke renderers that wore eight head sizes, seven radii,
// six chip dresses, four fold stores and eleven per-node click listeners. Source-level pins (no jsdom, the repo
// convention); the CSS census lives in notice-vocab.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const NOTICE = RENDER.split("function notice(spec: NoticeSpec): HTMLElement {")[1].split("\nfunction ")[0];

test("notice() is the one builder: a .turn.turn-notice with the severity class, a rail dot, and the .notice card", () => {
  assert.ok(NOTICE.length > 200, "notice(spec) is defined");
  assert.match(NOTICE, /const card = el\("div", "notice" \+ \(spec\.live \? " notice-live" : ""\) \+ \(spec\.nested \? " notice-nested" : ""\)\);/);
  assert.match(NOTICE, /const turn = el\("div", "turn turn-notice notice-sev-" \+ sev \+ \(boxed \? " notice-boxed" : ""\)/);
  // the severity rides the OUTERMOST element (the turn, or the card when nested) so a peer's inline colour can override it
  assert.match(NOTICE, /if \(spec\.rail\) \{ turn\.style\.setProperty\("--notice-rail", spec\.rail\); turn\.style\.setProperty\("--notice-dot", spec\.rail\); \}/);
  // the rail dot wears the severity — the swirl for romp, the severity colour otherwise
  assert.match(NOTICE, /const d = sev === "romp" \? dot\("romp"\) : dot\("ring"\);\s*\n\s*if \(sev !== "romp"\) d\.classList\.add\("notice-dot"\);/);
  assert.doesNotMatch(RENDER, /function noticeCard\(/, "the old builder is gone");
});

test("the head is ONE grammar: [caret] glyph SOURCE gist [meta] [acts] — a single flex row", () => {
  assert.match(NOTICE, /head\.appendChild\(typeof spec\.glyph === "string" \? noticeGlyph\(spec\.glyph\) : spec\.glyph\);/);
  assert.match(NOTICE, /const src = el\("span", "notice-src"\);/);
  assert.match(NOTICE, /const gist = typeof spec\.gist === "string" \? el\("span", "notice-gist"\) : spec\.gist;/);
  assert.match(NOTICE, /const meta = typeof spec\.meta === "string" \? el\("span", "notice-meta"\) : spec\.meta;/);
  assert.match(NOTICE, /const box = el\("span", "notice-acts"\); for \(const a of acts\) box\.appendChild\(a\);/);
  // the "why" rides the ONE tooltip, never a native title
  assert.match(NOTICE, /if \(spec\.tip\) setTip\(head, spec\.tip\);/);
  assert.doesNotMatch(NOTICE, /\.title = /);
  assert.match(CSS, /\.notice-head \{ display: flex; align-items: baseline; gap: 7px; min-width: 0; \}/);
});

test("DENSITY is mechanical: a body or actions → a card; head-only → .notice-slim (never chosen per kind)", () => {
  assert.match(NOTICE, /const boxed = collapsible \|\| acts\.length > 0;/);
  assert.match(NOTICE, /if \(!boxed\) card\.classList\.add\("notice-slim"\);/);
  assert.match(CSS, /\.notice\.notice-slim \{ background: none; border: 0; padding: 2px 0; \}/);
  // an empty body renders FLAT (gist == body): no caret, no body wrapper
  assert.match(NOTICE, /const body = spec\.body && spec\.body\.childNodes\.length \? spec\.body : null;/);
  assert.match(NOTICE, /const collapsible = !!body;/);
});

test("the fold: keyed in openFolds (notice:<key>), toggled by data-act=noticetoggle on the body delegate — ZERO listeners in the builder", () => {
  assert.match(NOTICE, /head\.dataset\.act = "noticetoggle";/);
  assert.match(NOTICE, /const fkey = spec\.key \? "notice:" \+ spec\.key : undefined;/);
  assert.match(NOTICE, /if \(fkey\) head\.dataset\.nkey = fkey;/);
  assert.match(NOTICE, /applyFold\(card, "notice-open", fkey\);/);
  assert.doesNotMatch(NOTICE, /addEventListener|\.onclick/, "no per-node listener — the tail rebuild destroyed them mid-press");
  // the handler on the stable document.body delegate, with the compact-run branch and the clear card's lazy fetch
  assert.match(RENDER, /noticetoggle: \(el\) => \{\s*\n\s*const gkey = el\.dataset\.gkey;\s*\n\s*if \(gkey\) \{ toggleToolGroup\(gkey\); return; \}\s*\n\s*const card = el\.closest\("\.notice"\) as HTMLElement \| null;/);
  assert.match(RENDER, /rememberFold\(card, "notice-open", el\.dataset\.nkey \|\| undefined\);/);
  assert.match(RENDER, /noticeOpened\(card, open\);/);
  assert.match(CSS, /\.notice-collapsible:not\(\.notice-open\) > \.notice-body \{ display: none; \}/);
});

test("open-by-default notices SEED openFolds once (the user's fold wins after): the live API error, the to-do, an owed question", () => {
  assert.match(RENDER, /const noticeSeeded = new Set<string>\(\);/);
  assert.match(NOTICE, /if \(spec\.open && fkey && !noticeSeeded\.has\(fkey\)\) \{ noticeSeeded\.add\(fkey\); openFolds\.add\(fkey\); \}/);
  assert.match(RENDER, /open: true, key: "apierr:" \+ \(ev\.uuid \|\| \(activeId \|\| ""\)\), cls: "turn-apierror"/);
  assert.match(RENDER, /let gist = ev\.tasks\.length \|\| !uts\.length \? `\$\{done\} of \$\{ev\.tasks\.length\} done` : `waiting on you · \$\{uts\.length\}`;/);
  assert.match(RENDER, /return notice\(\{ src: "to-do", glyph: "todo", sev, gist, body, key, open: true, cls: "turn-todo", tip \}\);/);
  assert.match(RENDER, /body, open: owed,/);
});

test("nested notices (inside the carrying user turn) are bare cards on the turn's rail with their OWN dot — no stair-step", () => {
  assert.match(NOTICE, /if \(spec\.nested\) \{\s*\n\s*card\.classList\.add\("notice-sev-" \+ sev\);[\s\S]*?return card;\s*\n\s*\}/);
  assert.match(CSS, /\.turn-user > \.notice-nested \{ align-self: stretch; \}/);
  assert.match(CSS, /\.turn-user > \.notice-nested::before \{ content: ""; position: absolute; left: -18px;/);
  assert.doesNotMatch(CSS, /\.turn-notice\.notice-romp/, "no right-aligned 72% romp card — direction belongs to the bubbles");
});

test("the agent report nests by DEFAULT (the carrying human turn passes no opts) and stands on its own rail only when renderInjected asks", () => {
  // carried from notice-card.test.ts (retired with noticeCard) when main's review of #1099 re-pinned it on
  // renderAgentNotif's OWN body: a lazy whole-file span onto any later `nested: true` had made the old line
  // vacuous (review find, 2026-09-09, on #1099); here the same shape, on the builder's spec
  const AGENT = RENDER.split("function renderAgentNotif(")[1].split("\nfunction ")[0];
  assert.match(AGENT, /opts: \{ nested\?: boolean; preamble\?: string \} = \{\}/, "nested unless told otherwise");
  assert.match(AGENT, /sev: failed \? "err" : "info", body: hasBody \? body : null, key, nested: opts\.nested !== false \}\);/);
  const INJ = RENDER.split("function renderInjected(")[1].split("\nfunction ")[0];
  assert.match(INJ, /renderAgentNotif\([^)]*nested: false/, "the notification-only record stands on its own rail");
});

test("one glyph per SOURCE, in the ctxIcon line-icon style; the romp swirl is the one non-stroke glyph", () => {
  for (const k of ["agent", "command", "system", "peer", "teammate", "api", "compaction", "clear", "branch", "session", "power", "question", "todo", "retry"]) {
    assert.match(RENDER, new RegExp("\\n  " + k + ": '<"), "NOTICE_GLYPHS." + k);
  }
  assert.match(RENDER, /function noticeGlyph\(kind: NoticeGlyphKind\): HTMLElement \{[\s\S]*?if \(kind === "romp"\) \{[\s\S]*?mediaSrc\("romp-swirl-glyph\.svg"\)/);
  assert.match(RENDER, /'<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" '\s*\n\s*\+ 'stroke-width="1\.4" stroke-linecap="round" stroke-linejoin="round">' \+ NOTICE_GLYPHS\[kind\]/);
  assert.match(CSS, /\.notice-glyph \{[^}]*width: 12px; height: 12px;/);
});

test("actions are data-act word buttons on the button vocabulary (noticeAct); the delegate acts and flash()es", () => {
  assert.match(RENDER, /function noticeAct\(label: string, act: string, tip\?: string\): HTMLButtonElement \{\s*\n\s*const b = el\("button", "notice-act"\) as HTMLButtonElement;/);
  assert.match(RENDER, /b\.dataset\.act = act;\s*\n\s*if \(tip\) setTip\(b, tip\);/);
  assert.match(CSS, /\.notice-act \{[^}]*font-size: var\(--btn-fs-sm\);[^}]*padding: var\(--btn-pad-sm\);/);
  for (const act of ["apiRetryNow", "dismissDialog", "stopAllRetries", "stopRetrying", "echorestore", "echodismiss", "todofold", "futoggle", "composerNoteX"]) {
    assert.match(RENDER, new RegExp("\\n    " + act + ": \\("), act + " is handled on the body delegate");
  }
});
