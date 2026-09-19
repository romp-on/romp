// Expand/collapse state must SURVIVE the incremental re-render a send/turn triggers (the user 2026-06-19):
// a short transcript rebuilds from index 0, a long one re-renders from the first changed event, so a
// DOM-only `.open`/`.expanded` silently snaps shut whatever the user had opened (the reported bug: expand
// the system-context card, type, hit ⏎ → it collapses). We persist open-state in a module Set keyed by a
// stable id and reapply on rebuild. No jsdom harness for the renderer, so pin the wiring at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("there is a persisted-fold registry with apply/remember helpers", () => {
  assert.match(RENDER, /const openFolds = new Set<string>\(\);/);
  assert.match(RENDER, /function applyFold\(target: HTMLElement, cls: string, key\?: string\)/);
  assert.match(RENDER, /function rememberFold\(target: HTMLElement, cls: string, key\?: string\)/);
  // apply reinstates on (re)build; remember toggles + records the new state
  assert.match(RENDER, /if \(key && openFolds\.has\(key\)\) target\.classList\.add\(cls\)/);
  assert.match(RENDER, /if \(open\) openFolds\.add\(key\); else openFolds\.delete\(key\)/);
});

test("the system-context card persists per session (keyed by renderingSid)", () => {
  assert.match(RENDER, /renderingSid = id;/, "syncView records which session it's building");
  assert.match(RENDER, /const key = renderingSid \? "sysctx:" \+ renderingSid : undefined;/);
  // 2026-09-08 (the notice-vocabulary pass): the key rides the ONE builder — notice() folds through openFolds
  // ("notice:sysctx:<sid>") and the body delegate's noticetoggle remembers the flip
  assert.match(RENDER, /gist: "System context", meta: bits\.join\(" · "\) \|\| undefined,\s*\n\s*body, key, nested: true,/);
  assert.match(RENDER, /applyFold\(card, "notice-open", fkey\)/);
  assert.match(RENDER, /rememberFold\(card, "notice-open", el\.dataset\.nkey \|\| undefined\)/);
});

test("foldable/inlineFold take a stable key and route through the persisted helpers", () => {
  assert.match(RENDER, /function foldable\(label: string, content: HTMLElement, key\?: string\)/);
  assert.match(RENDER, /function inlineFold\(head: HTMLElement, turn: HTMLElement, label: string \| HTMLElement, content: HTMLElement, key\?: string\)/, "a string or an element label (an edit's coloured totals, 2026-09-18)");
  assert.doesNotMatch(RENDER, /function ioClamp\(/, "ioClamp is gone — errors now fold onto the head like every other tool");
});

test("the other collapsibles pass stable keys (reminders, tool folds, thinking)", () => {
  assert.match(RENDER, /"rem:" \+ ev\.uuid/, "system reminders key on the user turn uuid");
  assert.match(RENDER, /const fkey = ev\.uuid \? "tool:" \+ ev\.uuid : undefined;/, "tool folds key on the tool uuid");
  assert.match(RENDER, /"think:" \+ ev\.uuid/, "thinking clamp keys on the thinking uuid");
  // the whole agent dispatch (prompt, tool calls, report) collapses under ONE fold with one stable key,
  // so a single click expands all of it together (the user 2026-06-22). The key is the tool's own fkey:
  // the ":agent"-suffixed sub-keys went with the nested prompt/report boxes (2026-09-05 — everything
  // inside the fold is open now, so there is nothing left to key separately).
  assert.match(RENDER, /inlineFold\(head, turn, label, body, fkey\);/);
  assert.doesNotMatch(RENDER, /fkey \+ ":agent"/, "no nested agent sub-folds to key");
});
