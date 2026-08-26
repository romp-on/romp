// The comment thread's third exit (the user 2026-08-23): MERGE folds the discussion back into the
// parent session. The kernel executes the flow (tests/test_comment_merge.py + the injected-voice
// sweep); these are the UI's source pins: the button, the delegated click-safe handler with its
// instant ack, and the merged status wearing resolved's dim everywhere it renders.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const TYPES = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "comments.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("the Merge button rides the actions row, delegated, acknowledging before the round-trip", () => {
  assert.match(RENDER, /mg\.textContent = "Merge";/);
  assert.match(RENDER, /mg\.dataset\.act = "cmtmerge";/);
  assert.match(RENDER, /cmtmerge: \(elx\) => \{/);
  assert.match(RENDER, /elx\.textContent = "Merging…";/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "commentMerge", id: cur\.sid, tid: cur\.th\.tid \}\);/);
});

test("merged is a first-class status: title, dim, rail, and a closed composer", () => {
  assert.match(TYPES, /"merging" \| "merged"/);
  assert.match(RENDER, /: th!\.status === "merged" \? nm \+ " \(merged into the session\)"/);
  assert.match(RENDER, /t\.status === "open" \|\| t\.status === "resolved" \|\| t\.status === "merged"/);
  assert.match(RENDER, /th\.status === "resolved" \|\| th\.status === "merged" \? " resolved" : ""/);
  assert.match(RENDER, /Merged — its outcome lives in the session now/);
});

test("kernel: the op is registered, latched like promote, and the handoff is voice-ruled", () => {
  assert.match(KERNEL, /"commentMerge"\)/);
  assert.match(KERNEL, /elif t == "commentMerge" and msg\.get\("tid"\):/);
  assert.match(KERNEL, /def _comment_merge\(parent_sid, tid\):/);
  assert.match(KERNEL, /_comment_update_if\(parent_sid, tid, \("open", "resolved"\), status="merging"\)/);
  assert.match(KERNEL, /MERGE_BODY_CAP = 6000/);
  // the reply CAS never accepts a merged thread, so a merged discussion cannot silently reopen
  assert.match(KERNEL, /this thread was already merged back into the session\./);
});
