// A Codex session never receives a slash command as prose (2026-09-19): the kernel refuses it before any backend sees it
// and answers with a warn that names the session (sid) and, for a composer press, the copy (qid). The chat's half: the
// optimistic bubble the press drew ends on that warn (no echo will ever land for it), the words go back into an EMPTY
// composer, and a warn naming a session is never read as a create's verdict, whatever is in flight. The battery that cannot
// compact says why in its tooltip (chat and timeline) and its click does nothing. Pure functions executed; the chat renderer
// has no jsdom harness, so its wiring is pinned at the source. Synthetic values only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { refusedRestoreText, newPending } from "./send-pending";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const RENDER = read("ui", "webview", "render.ts");
const TL = read("ui", "romp-timeline-view.js");
const KERNEL = read("kernel", "kernel.py");

test("the words a refused send puts back: the dropped entry's text into an empty box, never over a draft, nothing when no entry was dropped", () => {
  assert.equal(refusedRestoreText(newPending("/clear"), ""), "/clear");
  assert.equal(refusedRestoreText(newPending("/clear"), "a draft"), null, "a draft in progress is never overwritten");
  assert.equal(refusedRestoreText(newPending("/clear"), "   "), "/clear", "whitespace is an empty box");
  assert.equal(refusedRestoreText(undefined, ""), null, "an entry a push already retired restores nothing");
});

test("the warn branch keys on the session first: a sid-bearing warn toasts (and retires the press's bubble only when the frame names it); the provisional arm is the else of that branch", () => {
  const i = RENDER.indexOf('else if (m.type === "warn" && typeof m.text === "string" && m.text) {');
  assert.ok(i > 0, "the chat page handles the frame");
  const arm = RENDER.slice(i, RENDER.indexOf('else if (m.type === "spendCeiling"', i));
  assert.match(arm, /^else if \(m\.type === "warn" && typeof m\.text === "string" && m\.text\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(typeof m\.sid === "string" && m\.sid\) \{/,
    "the sid alone opens the session-scoped path: a battery click's or a POST route's refusal carries no qid and must never fall to the create arm");
  assert.match(arm, /if \(typeof m\.qid === "string" && m\.qid\) \{[\s\S]{0,900}dropPending\(list, "", undefined, m\.qid\)/, "the entry is dropped by id alone (the text argument is inert)");
  assert.match(arm, /if \(list\.length\) pendingSent\.set\(m\.sid, list\); else pendingSent\.delete\(m\.sid\);/);
  assert.match(arm, /const s = sessions\.get\(m\.sid\); if \(s\) reconcileOptimistic\(s\);/);
  assert.match(arm, /refusedRestoreText\(dropped, ta\.value\)/);
  assert.match(arm, /ta\.dispatchEvent\(new Event\("input", \{ bubbles: true \}\)\)/, "the cancelResult restore's idiom: the draft listener re-persists it");
  assert.match(arm, /warnToast\(back !== null \? m\.text \+ " It's back in the box\." : m\.text\);/);
  assert.match(arm, /\}\s*\n(?:\s*\/\/[^\n]*\n)*\s*else if \(provisionalId\) failProvisional\(m\.text\); else warnToast\(m\.text\);/,
    "a create's verdict is read only from a warn that names no session");
  const sidAt = arm.indexOf('if (typeof m.sid === "string" && m.sid) {');
  const provAt = arm.indexOf("failProvisional(m.text)");
  assert.ok(sidAt > 0 && provAt > sidAt, "the session branch is tested before the create arm");
  assert.equal(arm.split("failProvisional(").length - 1, 1, "one create arm, after the session branch");
  assert.match(RENDER, /import \{[^}]*\brefusedRestoreText\b[^}]*\} from "\.\/send-pending";/);
});

test("the chat battery for a Codex session: marked inert with the reason where the chat fills it (so the reused bar follows a tab switch), the builder line unchanged, the click declined", () => {
  assert.match(RENDER, /const CODEX_NO_COMPACT = "this session runs in Codex, which has no \/compact";/);
  assert.match(RENDER, /^function ctxBar\(\): HTMLElement \{ const bar = buildCtxBar\(compactActiveSession\); bar\.id = "ctx-bar"; return bar; \}/m,
    "the builder keeps its id and its click: the mark is applied where the status fills the bar, not at construction");
  assert.match(RENDER, /function markCtxBarFor\(bar: HTMLElement, st: Status\): void \{\s*\n\s*if \(st\.backend === "codex"\) \{ delete bar\.dataset\.compacts; bar\.dataset\.inertWhy = CODEX_NO_COMPACT; \}\s*\n\s*else if \(bar\.dataset\.inertWhy\) \{ delete bar\.dataset\.inertWhy; bar\.dataset\.compacts = "1"; \}/,
    "a Codex status marks the bar inert with the reason; any other status lifts the mark (the one live bar is reused across tabs)");
  assert.match(RENDER, /function setCtxBar\(bar: HTMLElement, ctxStr: string \| undefined, compacting = false, ctxColor\?: number\[\], ctxOver = false, st\?: Status\): void \{\s*\n\s*if \(st\) markCtxBarFor\(bar, st\);/);
  const fills = RENDER.match(/setCtxBar\(bar, s\.status\.ctx, s\.status\.state === "compacting", pickTone\(s\.status\.ctxColor, s\.status\.ctxTone\), s\.status\.ctxOver, s\.status\);/g) || [];
  assert.equal(fills.length, 3, "the statusline build, the tab tooltip and the light in-place refresh all hand the status in");
  const c = RENDER.indexOf("function compactActiveSession(bar: HTMLElement): void {");
  assert.ok(c > 0);
  const body = RENDER.slice(c, RENDER.indexOf("\n}\n", c));
  assert.match(body, /if \(s\.status\.backend === "codex"\) return;/, "no post, no click cue for a click that cannot compact");
  assert.ok(body.indexOf('if (s.status.backend === "codex") return;') < body.indexOf('vscodeApi.postMessage({ type: "compactSession"'), "declined before the post");
});

test("the timeline battery on a Codex lane: an inert cursor, the reason in the tooltip, no stamp and no post on press", () => {
  assert.match(TL, /hit\.style\.cursor = s\.backend === 'codex' \? 'default' : 'pointer';/);
  assert.match(TL, /s\.backend === 'codex' \? 'this session runs in Codex, which has no \/compact' : 'click to \/compact this session'/);
  assert.match(TL, /if \(e\.button !== 0\) return;\s*\n\s*if \(s\.backend === 'codex'\) return;/, "declined right after the button test, before the stamp and the post");
});

test("the kernel-served shell's bell explains the refused kind's new tenants: a slash command a session has no such command for, and a clear a Codex session could not run", () => {
  assert.match(KERNEL, /refused:"[^"]*Or a slash command sent to a session that has no such command \(a Codex session has no \/compact\): nothing was sent, and the entry names it\. Or a \/clear a Codex session could not run/);
});
