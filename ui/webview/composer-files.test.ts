// Dropped-in files stay visible as little THUMBNAILS in the chat box (the user 2026-08-04): a file
// dragged, pasted, or picked into the composer lands in an attachment strip above the textarea — an
// image shows its pixels (same-origin /file bytes on the web; the host imgRequest data-URL flow in the
// VS Code webview), any other file wears a compact ext + name chip — instead of dumping a raw path
// string into the text. Attachments live the DRAFT lifecycle (survive tab switch + reload, die with the
// session, cleared on send), unlike citations, which a tab switch abandons. On send the paths ride the
// outgoing text as a trailing line, quoted when they contain spaces. No jsdom for this renderer →
// source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKELETON = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");

test("the composer has an attachment strip, its own row above the chips — on BOTH skeletons", () => {
  assert.match(SKELETON, /<div id="composer-files" style="display:none"><\/div><div id="composer-staged" style="display:none"><\/div><div id="composer-chips"/);
  // the WEB dashboard's page skeleton is the kernel's own HTML, not page-skeleton.ts — when only the
  // extension skeleton grew this div, a file dropped on the web surface attached invisibly, showing
  // nothing at all (the user 2026-08-04). The two skeletons must carry the strip in step — and the
  // staged strip (2026-08-15) sits between files and chips on both.
  assert.match(KERNEL, /<div id="composer"><div id="composer-files" style="display:none"><\/div>'/);
  assert.match(KERNEL, /<div id="composer-staged" style="display:none"><\/div>'/);
  assert.match(KERNEL, /<div id="composer-chips" style="display:none"><\/div>'/);
  assert.match(CSS, /#composer-files \{ flex: 1 1 100%; display: flex; flex-wrap: wrap/);
  assert.match(CSS, /\.composer-file-img \{ display: block; height: 46px/);
  // the ✕ rides the tab-close idiom: hidden until hover, no clutter on a quiet strip
  assert.match(CSS, /\.composer-file:hover \.composer-file-x \{ display: block; \}/);
});

test("every file arrival becomes an attachment, never raw path text in the box", () => {
  assert.match(RENDER, /const composerFiles = new Map<string, string\[\]>\(\);/);
  // the drop handler's three path sources all land in addComposerFile
  assert.match(RENDER, /const fromUri = \(u: string\) => addComposerFile\(activeId, decodeURIComponent\(u\.replace\(\/\^file:\\\/\\\/\/, ""\)\)\);/);
  assert.match(RENDER, /if \(p\) \{ addComposerFile\(activeId, p\); return; \}/);
  // paste-with-files and the host round-trip (dropped bytes, 📎 dialog, phone picker) too — the
  // paste's path branch is gated on LOCAL ownership (composer-attach.test.ts owns the remote rule)
  assert.match(RENDER, /if \(p && !hostOf\(activeId \|\| ""\)\) addComposerFile\(activeId, p\);\s*\n\s*else shipFileToHost\(f\);/);
  // the window spans the popover-owned branch first (an open comment popover claims its own
  // clip's ack; the COMPOSER path below it still always lands as an attachment)
  assert.match(RENDER, /m\.type === "droppedPath" && typeof m\.path === "string"\) \{[\s\S]{0,900}addComposerFile\(owner, m\.path\);/);
  // the old insert-at-cursor path is gone with its last caller
  assert.doesNotMatch(RENDER, /function insertComposerText/);
});

test("an image thumbnail renders per surface; other files wear an ext + name chip", () => {
  const fn = RENDER.slice(RENDER.indexOf("function renderComposerFiles("), RENDER.indexOf("function composerFileDoc("));
  // web: same-origin /file bytes; VS Code: the host imgRequest flow (buildPathImg) — the sandbox can't
  // reach the kernel origin, the same split every chat image rides
  assert.match(fn, /if \(canPreview\(\)\) \{/);
  assert.match(fn, /img\.src = fileUrl\(p, id\);/);
  assert.match(fn, /const w = buildPathImg\(p, id\);/);   // the composer's own session, in scope — never the render-owner global
  // name first, pixels when ready (the user 2026-08-04): the ext + name chip renders immediately and
  // the image swaps in on its own load event — a slow fetch never shows a blank box, a 404 keeps the chip
  assert.match(fn, /const doc = composerFileDoc\(p\);\s*\n\s*box\.appendChild\(doc\);/);
  assert.match(fn, /img\.addEventListener\("load", \(\) => doc\.replaceWith\(img\)\);/);
  // non-image: extension badge + basename, both TEXT (no glyphs)
  assert.match(RENDER, /ext\.textContent = \(dot > 0 \? p\.slice\(dot \+ 1\) : "file"\)\.slice\(0, 5\)\.toUpperCase\(\);/);
  assert.match(RENDER, /nm\.textContent = p\.split\("\/"\)\.pop\(\) \|\| p;/);
  // click opens the file — routed by openPath (VS Code editor / the feed pane's viewer on the web);
  // the ✕ removes exactly that attachment
  assert.match(fn, /openPath\(p, id \|\| null\);/);
  assert.match(fn, /if \(id\) removeComposerFile\(id, i\);/);
  // the same file dropped twice attaches once
  assert.match(RENDER, /if \(!list\.includes\(path\)\) list\.push\(path\);/);
});

test("attachments ride the send as a trailing line of paths, quoted when they hold spaces", () => {
  assert.match(RENDER, /const attached = composerFiles\.get\(activeId\) \|\| \[\];/);
  assert.match(RENDER, /if \(!typed && !attached\.length\) return;/);   // attachment-only sends are real sends
  assert.match(RENDER, /\(typed \? typed \+ "\\n" : ""\) \+ attached\.map\(\(p\) => \(\/\\s\/\.test\(p\) \? '"' \+ p \+ '"' : p\)\)\.join\(" "\)/);
  // consumed on delivery (the provisional queue path included) — the strip emptied into this message
  assert.match(RENDER, /if \(attached\.length\) \{ composerFiles\.delete\(sid\); if \(sid === activeId\) renderComposerFiles\(sid\); \}/);
  // a picker answer and an edit send only the TYPED words — attachments wait for the next normal send
  assert.match(RENDER, /const askRoute = typed \? composerAnswersAsk\(\) : null;/);
  assert.match(RENDER, /if \(!typed\) return;\s*\/\/ an edit sends the typed words/);
});

test("attachments live the DRAFT lifecycle: switch, reload, close", () => {
  // persisted beside drafts/citations/staged, restored as a list of strings
  assert.match(RENDER, /files: Object\.fromEntries\(composerFiles\),/);
  assert.match(RENDER, /const savedFiles = \(\(vscodeApi\?\.getState\?\.\(\) \|\| \{\}\) as any\)\.files;/);
  // a tab switch REPAINTS the strip (unlike citations, which the switch abandons); the staged
  // strip (2026-08-15) repaints in the same breath, between the chips and the files
  assert.match(RENDER, /renderComposerChips\(id\);   \/\/ the entering tab's own citation chip \(if any\)\s*\n\s*renderStagedStrip\(id\);[^\n]*\n\s*renderComposerFiles\(id\);/);
  // the post-reload restore paints it once the active tab is known
  assert.match(RENDER, /renderComposerFiles\(activeId\);   \/\/ attachments persisted across the reload/);
  // closing a session drops its attachments with its draft, and repaints for the new active tab
  assert.match(RENDER, /drafts\.delete\(id\); composerCitations\.delete\(id\); composerEdits\.delete\(id\); composerFiles\.delete\(id\); persistDrafts\(\);/);
  assert.match(RENDER, /renderComposerFiles\(activeId\);   \/\/ same for its attachment thumbnails/);
});
