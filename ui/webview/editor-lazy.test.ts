// The CodeMirror editing substrate (the user 2026-08-22): file-view's edit mode swaps the raw
// textarea for CodeMirror 6, loaded as its OWN on-demand bundle. The load-bearing constraints:
// the main bundles stay byte-stable (nothing imports the chunk — lazy discipline), the save path
// is untouched (same string to the same saveFile op behind the same consent gate + ns conflict
// floor), and byte fidelity survives round-trips (UTF-8-only arming, CRLF restore, no invented
// or stripped trailing newline). Pure units run the real langNameFor; the rest are source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { langNameFor } from "./editor-chunk";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const VIEW = W("file-view.ts");
const CHUNK = W("editor-chunk.ts");
const RENDER = W("render.ts");
const ESBUILD = fs.readFileSync(path.resolve(process.cwd(), "esbuild.js"), "utf8");

// ── lazy discipline: the chunk rides its own entry; no main-bundle source may import it ──────────

test("the chunk is its own esbuild entry, and no main-bundle source imports CodeMirror", () => {
  assert.match(ESBUILD, /"\.\.\/ui\/webview\/editor-chunk\.ts"/);
  // the contract is the window global, never an import — an import would drag CodeMirror into the
  // main render/feed bundles and break byte-stability for people who never edit
  assert.match(CHUNK, /if \(typeof window !== "undefined"\) \(window as any\)\.__rompEditor = \{ mount, langNameFor \};/);
  for (const f of ["file-view.ts", "render.ts", "feed.ts", "preview.ts"]) {
    assert.doesNotMatch(W(f), /@codemirror|from "\.\/editor-chunk"/,
      f + " must not import the editor — the lazy chunk is reached only via the window global");
  }
  // …and the test's own import of langNameFor is fine: tests bundle to out-tests, never to dist.
});

test("file-view loads the chunk from its own bundle's URL (same dir, same ?v= token), latch cleared on failure", () => {
  assert.match(VIEW, /\.find\(\(u\) => \/\\\/\(render\|feed\)\\\.js\/\.test\(u\)\)/);
  assert.match(VIEW, /sc\.src = self\.replace\(\/\\\/\(render\|feed\)\\\.js\/, "\/editor-chunk\.js"\);/);
  assert.match(VIEW, /sc\.onerror = \(\) => \{ edChunk = null; rej\(/,
    "a failed load clears the latch so a later edit retries fresh");
});

test("the chunk wait wears the romp loader, and a failed load falls back LOUDLY to the textarea", () => {
  assert.match(VIEW, /const wait = el\("div", "fileview-load"\);/);
  assert.match(VIEW, /editing in the plain fallback editor/);
  assert.match(VIEW, /const enterFallback = \(\) => \{/);
  assert.match(VIEW, /ta = el\("textarea", "fileview-editor"\) as HTMLTextAreaElement;/,
    "the textarea survives as the fallback surface");
  assert.match(VIEW, /if \(!editing \|\| my !== editSeq\) return;/,
    "a stale chunk resolution (edit left while loading) must not mount over the viewer");
});

// ── the save path is NOT the editor's: same string, same op, same guards ─────────────────────────

test("both surfaces hand the SAME string to the SAME saveFile op — the gate and floor are untouched", () => {
  assert.match(VIEW, /const bufValue = \(\): string \| null => \(cm \? cm\.value\(\) : ta \? ta\.value : null\);/);
  assert.match(VIEW, /const content = eolCRLF \? buf\.replace\(\/\\n\/g, "\\r\\n"\) : buf;/,
    "the CRLF restore stays file-view's, whichever surface owns the buffer");
  assert.match(VIEW, /post\(\{ type: "saveFile", path, sid: sid \|\| undefined, content, baseMtimeNs: mtimeNs, reqId: saveSeq \}\);/);
  // in-flight typing survives the ack from EITHER surface
  assert.match(VIEW, /if \(bufValue\(\) !== null && bufValue\(\) !== norm\(content\)\) \{/);
  // leaving edit mode releases the CodeMirror view
  assert.match(VIEW, /cm\?\.destroy\(\); cm = null;/);
});

test("the chunk is the text surface only: Mod-s routes to the caller's save, no save wiring of its own", () => {
  assert.match(CHUNK, /key: "Mod-s", run: \(\) => \{ opts\.onSave\(\); return true; \}/);
  // ban from CODE, not comments — the header legitimately NAMES the save op it must never touch
  const code = CHUNK.split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  assert.doesNotMatch(code, /saveFile|baseMtimeNs|fetch\(/);
  // local word completion only — no servers, per the no-LSP decision
  assert.match(CHUNK, /autocompletion\(\{ override: \[completeAnyWord\] \}\)/);
});

// ── byte fidelity: the mount contract and the curation, as executed logic ────────────────────────

test("langNameFor curates exactly the in-repo set, plain text otherwise", () => {
  assert.equal(langNameFor("ts"), "javascript");
  assert.equal(langNameFor("PY"), "python");
  assert.equal(langNameFor("bats"), "shell");
  assert.equal(langNameFor("yml"), "yaml");
  assert.equal(langNameFor("md"), "markdown");
  assert.equal(langNameFor("rs"), null, "uncurated extensions edit as plain text — never a guess");
  assert.equal(langNameFor(""), null);
});

test("the CRLF restore + trailing-newline behavior round-trips byte-identically", () => {
  // the exact expression doSave applies to the buffer (pinned above); executed here on both shapes
  const save = (buf: string, eolCRLF: boolean) => (eolCRLF ? buf.replace(/\n/g, "\r\n") : buf);
  const norm = (s: string) => s.replace(/\r\n/g, "\n");
  const crlf = "line one\r\nline two\r\n";
  assert.equal(save(norm(crlf), true), crlf, "an untouched CRLF file round-trips byte-identical");
  const noTail = "no trailing newline";
  assert.equal(save(norm(noTail), false), noTail, "no newline is invented at EOF");
  const tail = "kept\n";
  assert.equal(save(norm(tail), false), tail, "an existing trailing newline is kept");
});

test("edit arming stays the kernel's verdict: UTF-8-only and the ns mtime anchor", () => {
  assert.match(VIEW, /r\.headers\.get\("X-Romp-Text-Utf8"\) !== "0";/);
  assert.match(VIEW, /editBtn\.hidden = editing \|\| text === null \|\| !isText \|\| !mtimeNs;/);
});

// ── the theme stays the dashboard's own look ─────────────────────────────────────────────────────

test("the editor declares its font and reuses the panel palette — no new fonts or sizes", () => {
  assert.match(CHUNK, /fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"/);
  assert.match(CHUNK, /fontSize: "13px"/);
  assert.match(CHUNK, /rgba\(156, 210, 255, 0\.22\)/, "selection wears the romp accent, nothing new");
});
