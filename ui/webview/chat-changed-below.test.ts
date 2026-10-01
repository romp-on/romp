// A change ABOVE a proto-2 client's held tail run reaches the page as a chatTail carrying the change's key (`changedBelow`), not
// as a ~1.6 MB full frame of the tail run it already holds (kernel _send_chat_proto2, 2026-09-30; tests/test_chat_below_first.py
// drives the kernel side). The page's half: a history run that holds the key is re-asked by its span so the fresh page replaces
// the stale card in place; the tail run and a gap owe nothing. Source pins on the wiring; chat-regions.test.ts executes the lookup.
import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the kernel's tail names the change above the held run by its key, and the page reads that very field", () => {
  assert.match(KERNEL, /tail\["changedBelow"\] = below_key/, "the kernel's side of the field");
  assert.match(RENDER, /typeof msg\.changedBelow === "string" && s\.regions/, "the page reads it inside chatTail");
});

test("the page re-asks the HISTORY run holding the key by its span, once per span on the wire, through the gap asks' own road", () => {
  const i = RENDER.indexOf('typeof msg.changedBelow === "string"');
  assert.ok(i > 0);
  const block = RENDER.slice(i, RENDER.indexOf("\n  }\n", i) + 5);
  assert.match(block, /const stale = runHoldingKey\(s\.regions, msg\.changedBelow\);/, "the lookup is chat-regions' runHoldingKey (history runs only)");
  assert.match(block, /const busy = gapHasAsk\(msg\.id, \{ lo: stale\.lo, hi: stale\.hi \}\);\s*\n\s*if \(busy\) gapRedo\.set\(gapKey\(msg\.id, stale\.lo, stale\.hi\), \{ sid: msg\.id, lo: stale\.lo, hi: stale\.hi \}\);/,
               "an ask already on the wire is not doubled: the span is remembered for one follow-up when that reply lands");
  assert.doesNotMatch(block, /\} else \{/, "no else in the hook: chat-tail-repaint.test.ts slices the active branch up to the function's first `} else {`");
  assert.match(block, /gapLoading\.add\(gapKey\(msg\.id, stale\.lo, stale\.hi\)\);/, "filed as a page ask, so chatTurns' reply clears it");
  assert.match(block, /scrollDiagRow\("regionask", \{ sid: msg\.id, lo: stale\.lo, hi: stale\.hi, edge: "top", why: "changed-below"/, "the ask is a regionask row with its own why");
  assert.match(block, /vscodeApi\?\.postMessage\(\{ type: "loadTurns", id: msg\.id, lo: stale\.lo, hi: stale\.hi \}\);/, "the same loadTurns a gap asks with");
});

test("the hook runs after the delta is applied and before the awaited-fields check: the tail's own work comes first", () => {
  const i = RENDER.indexOf('typeof msg.changedBelow === "string"');
  const applied = RENDER.lastIndexOf("if (!regionsAbsorbTail(s)) requestFullSession(s.id, \"gap\");", i);
  const awaited = RENDER.indexOf("if (awaitKey(s.status) !== before) awaitChanged(msg.id);", i);
  assert.ok(applied > 0 && applied < i && i < awaited, "order: apply, then the re-ask, then the chip");
});

test("a reply frees its span and drains the deferred re-asks for the session, through the same road (2026-09-30)", () => {
  const i = RENDER.indexOf("function chatTurns(msg: any): void {");
  const head = RENDER.slice(i, i + 700);
  assert.match(head, /if \(span\) gapLoading\.delete\(gapKey\(msg\.id, span\[0\], span\[1\]\)\);[\s\S]*?if \(!s\) return;\s*\n\s*if \(span\) drainGapRedo\(msg\.id\);/,
               "the drain runs after the span is freed, for a session the page holds");
  const d = RENDER.indexOf("function drainGapRedo(sid: string): void {");
  const drain = RENDER.slice(d, RENDER.indexOf("\n}\n", d) + 3);
  assert.match(drain, /redoSpans\(gapRedo\.values\(\), sid, \(lo, hi\) => gapHasAsk\(sid, \{ lo, hi \}\)\)/, "chat-regions' pure rule decides which spans go");
  assert.match(drain, /gapRedo\.delete\(gapKey\(sid, sp\.lo, sp\.hi\)\);\s*\n\s*gapLoading\.add\(gapKey\(sid, sp\.lo, sp\.hi\)\);/, "each goes once: forgotten as deferred, filed as in flight");
  assert.match(drain, /why: "changed-below-redo"/, "its own why on the regionask row");
  assert.match(drain, /vscodeApi\?\.postMessage\(\{ type: "loadTurns", id: sid, lo: sp\.lo, hi: sp\.hi \}\);/);
});
