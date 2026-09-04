// A CODEX session's statusline menus speak Codex's vocabulary (docs/codex.md): the model/effort
// pickers read the /models payload's codex section, and its mode picker offers Sandboxed and
// Auto without exposing unsupported Claude modes. Source-pin over render.ts, the same style
// as picker-backend.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const TIMELINE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("the /models payload's codex section populates its own choice arrays (both surfaces)", () => {
  for (const src of [RENDER, TIMELINE]) {
    assert.match(src, /CODEX_MODEL_CHOICES/);
    assert.match(src, /CODEX_EFFORT_CHOICES/);
    assert.match(src, /d\.codex && Array\.isArray\(d\.codex\.models\)/);
    assert.match(src, /d\.codex && Array\.isArray\(d\.codex\.efforts\)/);
  }
});

test("menu construction picks the choice list by the session's backend", () => {
  assert.match(RENDER, /function metaChoices\(kind: MetaKind, st: Status\)/);
  assert.match(RENDER, /st\.backend === "codex"/);
  assert.match(RENDER, /for \(const c of metaChoices\(kind, s\.status\)\.filter\(/);
  assert.match(TIMELINE, /s\.backend === 'codex'/);
  assert.match(TIMELINE, /\? \(kind === 'model' \? CODEX_MODEL_CHOICES : CODEX_EFFORT_CHOICES\)/);
});

test("Codex offers only its supported modes and opens the mode picker", () => {
  const choices = RENDER.match(/const CODEX_MODE_CHOICES: MetaChoice\[\] = \[([\s\S]*?)\n\];/)![1];
  assert.deepEqual([...choices.matchAll(/value: "([^"]+)"/g)].map(m => m[1]), ["sandboxed", "auto"]);
  assert.match(RENDER, /if \(kind === "mode"\) return CODEX_MODE_CHOICES;/);
  assert.doesNotMatch(RENDER, /if \(kind === "mode" && s\.status\.backend === "codex"\) return;/);
  assert.match(RENDER, /case "sandboxed": return "Sandboxed";/);
});
