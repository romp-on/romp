// A chat message's embedded image keeps its MENTION-TIME bytes (the user 2026-08-16): an agent
// re-generating a plot under the same filename used to rewrite the picture inside every older
// message that had embedded it — an <img src="/file?path=…"> re-reads the live file on every
// render. The kernel now snapshots a mentioned image at its pathLinks/spacePaths resolve latch
// (content-addressed, bounded store) and ships per-message pathPins as a SIBLING map — pathLinks
// values must stay strings, or an older client's string-gate would unlink every token. The embed
// and its lightbox request /file with the pin; the federation relay forwards the query untouched;
// an evicted pin falls back server-side to the live file. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const PREVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "preview.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("pathPins ride the chat events as a sibling map and thread into every linkify pass", () => {
  assert.match(RENDER, /pathLinks\?: Record<string, string>; pathPins\?: Record<string, string> \}\n  \| \{ kind: "assistant";/);
  assert.match(RENDER, /pathLinks\?: Record<string, string>, pathPins\?: Record<string, string>\): void/);
  const uses = RENDER.match(/linkifyFileUris\((?:body|bubble|full), [^)]*ev\.pathPins\)/g) || [];
  assert.equal(uses.length, 3, "all three chat bodies thread the pins");
});

test("the embed and its lightbox request the pinned bytes; unpinned surfaces stay live", () => {
  assert.match(RENDER, /previewFull\(p, renderingOwnerSid \?\? activeId, kernelVerified\.has\(p\), \(pathPins \|\| \{\}\)\[p\]\)/);
  assert.match(PREVIEW, /previewFull\(path: string, sid\?: string \| null, verified = false, pin\?: string\)/);
  assert.match(PREVIEW, /const url = fileUrl\(path, sid\) \+ \(pin \? "&pin=" \+ encodeURIComponent\(pin\) : ""\);/);
  assert.match(PREVIEW, /openLightbox\(path, sid, pin\); \};/, "the big view shows the same pixels the embed did");
  assert.match(PREVIEW, /img\.src = fileUrl\(path, sid\) \+ \(pin \? "&pin=" \+ encodeURIComponent\(pin\) : ""\);/);
});

test("the kernel pins at the resolve latch and serves pins shape-gated with live fallback", () => {
  assert.match(KERNEL, /def _pin_mention\(fp\):/);
  assert.match(KERNEL, /pin = _pin_for\(r, sid\)\s+# the resolve moment IS the mention-time snapshot/);
  assert.match(KERNEL, /ev\["pathPins"\] = pp/, "attached on both user and assistant events");
  assert.match(KERNEL, /_PIN_ID_RE = re\.compile\(r"\^\[0-9a-f\]\{64\}\\\.\[a-z0-9\]\{1,8\}\$"\)/);
  assert.match(KERNEL, /if pin and _PIN_ID_RE\.match\(pin\):/);
  assert.match(KERNEL, /if pf\.is_file\(\):\s*\n\s*fp = str\(pf\)/, "a missing blob falls through to the live file");
});
