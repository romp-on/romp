// A mentioned image/PDF renders FULL-SIZE in the chat (the user 2026-07-20, who wanted not even a thumbnail
// but a rendered image, similar to how it renders the user messages), absolute OR relative path —
// the kernel resolves a relative one against the session's cwd exactly like click-to-open. Per
// surface: web renders via previewFull (kernel /file bytes → <img> at the user-image scale / a PDF's
// native inline <iframe>, both self-removing when the kernel can't serve the path); the VS Code
// webview can't reach the kernel origin from an <img>, so images ride the SAME host data-URL flow the
// user-message pictures use (imgRequest, now carrying the session id) and PDFs keep the click-to-open
// link. The feed's artifact strips deliberately keep their compact thumbnails. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const PREVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "preview.ts"), "utf8");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("previewFull renders the image itself; a PDF is a click-to-view CARD, never an auto-loading frame", () => {
  assert.match(PREVIEW, /export function previewFull\(path: string, sid\?: string \| null\): HTMLElement \| null/);
  assert.match(PREVIEW, /img\.className = "path-full-img";/);
  assert.match(PREVIEW, /img\.onerror = \(\) => box\.remove\(\);/);
  // NO inline <iframe> for PDFs (2026-07-20): a browser set to "Download PDFs" saved a fresh copy on
  // EVERY chat re-render — the Downloads folder silently filled. The fetch must be user-initiated.
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  assert.doesNotMatch(pf, /createElement\("iframe"\)/, "no auto-loading PDF frame in the chat strip");
  assert.match(pf, /box\.classList\.add\("path-full-pdfcard"\);/);
  assert.match(pf, /box\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openLightbox\(path, sid\); \};/);
  // the HEAD probe (headers only — never a download) still removes a dead card
  assert.match(pf, /fetch\(fileUrl\(path, sid\), \{ method: "HEAD" \}\)/);
});

test("the chat strip uses the FULL render on web, and the host data-URL flow for images in VS Code", () => {
  assert.match(RENDER, /const full = canPreview\(\) \? previewFull\(p, activeId\)\s*\n\s*: previewKind\(p\) === "img" \? buildPathImg\(p\) : null;/);
  assert.doesNotMatch(RENDER, /previewThumb/, "the chat no longer renders mention thumbnails — full renders now");
});

test("a photo the agent READ renders under its tool row, remote sessions included", () => {
  // the user 2026-08-14: 'when it looked at photos, render them in the chat — even on the
  // devbox'. The Read tool branch reuses the mention flow wholesale: previewFull(path, sid)
  // → fileUrl's /remote/<host>/file relay for host-prefixed sids, buildPathImg for VS Code.
  assert.match(RENDER, /if \(readPath && previewKind\(readPath\) === "img"\) \{/);
  assert.match(RENDER, /const full = canPreview\(\) \? previewFull\(readPath, activeId\) : buildPathImg\(readPath\);/);
  assert.match(RENDER, /if \(o && typeof o\.file_path === "string"\) readPath = o\.file_path;/,
    "the path comes from the Read tool's own input, resolved like every other preview");
});

test("imgRequest carries the session id so RELATIVE mentioned paths resolve against the session cwd", () => {
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "imgRequest", path: p, id: activeId \}\);/);
  assert.match(KERNEL, /_img_data_url\(_resolve_open_path\(p, msg\.get\("id"\)\)\)/);
});

test("full-size images wear the user-image scale — one size per information type", () => {
  assert.match(CSS, /\.path-full-img \{[^}]*max-height: 320px/);
  assert.match(CSS, /\.user-img \{[^}]*max-height: 320px/);
  assert.match(CSS, /\.path-full-pdfcard \{/);
});

test("the feed's artifact strips keep their compact thumbnails (cards stay glanceable)", () => {
  assert.match(FEED, /previewThumb\(/);
  assert.match(PREVIEW, /export function previewThumb\(path: string, sid\?: string \| null\): HTMLElement \| null/);
});
