// File-preview core (the user 2026-07-08, re-homed from feed-artifacts.test.ts when the feed's
// artifact strips were removed 2026-08-14; the strips came back 2026-08-19 on this fork and
// feed-artifacts.test.ts owns their pins again, so this file stays the CHAT-surface core): a path
// mentioned in the CHAT gets an inline render, bytes ride the kernel's /file endpoint. Source pins
// over preview.ts / render.ts / styles.css / the kernel (render.ts has import-time DOM side effects,
// so no test imports it as a module — see distill-background.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const PREVIEW = fs.readFileSync(path.join(UI, "preview.ts"), "utf8");
const CHAT_CSS = fs.readFileSync(path.join(UI, "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("preview.ts: kind classification, kernel /file URL, and hide-but-heal unverified renders", () => {
  // the client's renderable-extension list must stay in step with the kernel's _PREVIEW_MIME
  assert.match(PREVIEW, /IMG_EXT = new Set\(\["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"\]\)/);
  assert.match(KERNEL, /_PREVIEW_MIME = dict\(_IMG_MIME, \*\*\{"\.pdf": "application\/pdf"\}\)/,
               "kernel allowlist = image mimes + pdf; the client mirrors it");
  assert.match(PREVIEW, /if \(ext === "pdf"\) return "pdf";/);
  // bytes come from the kernel's /file endpoint, path percent-encoded, sid for cwd-relative
  // resolution — routed through the /remote/<host>/file relay when the sid wears a host prefix
  // (a federated session's file lives on the remote disk; behavior pinned in preview-remote.test.ts)
  assert.match(PREVIEW, /base \+ "\?path=" \+ encodeURIComponent\(path\) \+ \(bare \? "&sid=" \+ encodeURIComponent\(bare\) : ""\)/);
  assert.match(PREVIEW, /"\/remote\/" \+ encodeURIComponent\(host\) \+ "\/file"/);
  // web dashboard only: the VS Code webview can't reach the kernel origin from an <img>
  assert.match(PREVIEW, /location\.protocol === "http:" \|\| location\.protocol === "https:"/);
  // an UNVERIFIED render the kernel can't serve HIDES itself but stays HEALABLE (2026-08-24; it
  // used to remove itself permanently — no dead chips, but also no recovery until a send re-rendered
  // the turn). Kernel-VERIFIED paths keep the visible retry chip — chat-inline-preview.test.ts.
  assert.match(PREVIEW, /if \(!verified\) box\.style\.display = "none";/);
  assert.match(PREVIEW, /box\.style\.display = "none"; failedPreviews\.set\(box, probe\);/);
  assert.match(KERNEL, /if p == "\/file":/, "the preview bytes endpoint exists");
  assert.match(KERNEL, /def do_HEAD\(self\):/, "HEAD probe for chips that can't self-verify like an <img>");
});

test("preview.ts: lightbox is a singleton overlay — img or native-viewer iframe, Esc/backdrop closes", () => {
  assert.match(PREVIEW, /document\.getElementById\("romp-lightbox"\)\?\.remove\(\);/, "reopening replaces, never stacks");
  assert.match(PREVIEW, /frame\.src = fileUrl\(path, sid\);/, "pdf → iframe → the browser's own viewer");
  assert.match(PREVIEW, /if \(ev\.key === "Escape"\)/);
  assert.match(PREVIEW, /wrap\.onclick = \(ev\) => \{ if \(ev\.target === wrap\) dismiss\(\); \};/, "backdrop closes; content clicks don't");
});

test("chat: a mentioned image/PDF grows a FULL render at its mention, deduped and capped", () => {
  // (the user 2026-07-20: full renders replaced the 2026-07-08 thumbnails in the chat; the user
  // 2026-08-15: figures moved from a tail strip to the mentioning block —
  // chat-inline-preview.test.ts pins the placement shape)
  assert.match(RENDER, /import \{ previewKind, previewFull, canPreview, fileUrl, retryFailedPreviews, refreshSettledPreviews, installMdImgHeal \} from "\.\/preview";/);
  assert.match(RENDER, /if \(previewKind\(open\) && !previewable\.includes\(open\) && !\(skipThumbs && skipThumbs\.includes\(open\)\)\) \{/, "collected while linkifying — same detection, no second regex pass; an in-bubble image never re-renders");
  assert.match(RENDER, /if \(previewable\.length\) \{/, "both surfaces now — VS Code images ride the host data-URL flow");
  assert.match(RENDER, /previewable\.slice\(0, 4\)/, "capped so a directory listing doesn't wallpaper the chat");
  assert.match(RENDER, /previewFull\(p, renderingOwnerSid \?\? activeId, kernelVerified\.has\(p\), \(pathPins \|\| \{\}\)\[p\]\)/, "URLs bake the OWNING session's id — a background build must never capture activeId; verified paths fail loudly");
});

test("the chat sheet carries the lightbox + preview styles (the feed sheet carries its own)", () => {
  assert.match(CHAT_CSS, /#romp-lightbox \{ position: fixed; inset: 0; z-index: 1300;/);
  assert.match(CHAT_CSS, /\.path-thumb-tag \{ font-size: 0\.74em;/, "the PDF card's label (previewFull)");
  assert.match(CHAT_CSS, /\.path-full-img \{ display: block; max-width: 100%;/, "the full render's image scale");
  assert.match(CHAT_CSS, /\.path-thumbs \{ display: flex; flex-wrap: wrap;/, "the chat strip container");
});

test("the lightbox offers a download beside the close, saving the same bytes it shows", () => {
  // the user 2026-08-19: full-screen images need a save affordance. An anchor with the download
  // attribute, carrying the SAME pinned url the lightbox <img> renders — so a re-generated file
  // can't swap the image between viewing and saving — dressed exactly like the ✕ beside it.
  const at = PREVIEW.indexOf("export function openLightbox");
  const body = PREVIEW.slice(at, PREVIEW.indexOf("\n}", at));
  assert.ok(body.indexOf('dl.href = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "")') > 0,
    "the download url matches the shown image, pin included");
  assert.ok(body.indexOf('dl.download = path.slice(path.lastIndexOf("/") + 1) || "image"') > 0,
    "saved under the file's own basename");
  assert.ok(body.indexOf("dl.onclick = (ev) => ev.stopPropagation()") > 0,
    "saving must not also dismiss the lightbox");
  assert.ok(body.indexOf('bar.append(name, dl, close)') > 0, "between the filename and the ✕");
  // an inline TRAY SVG, not a codepoint: "⭳" (U+2B73) has no coverage in the mac system fonts and
  // rendered as a tofu box (the user 2026-08-19). Same stroke family as the composer's buttons.
  assert.ok(body.indexOf('<polyline points="7 10 12 15 17 10"/>') > 0, "the arrow-into-tray glyph");
  assert.ok(body.indexOf("\u2b73") < 0 && body.indexOf("⭳") < 0, "the uncovered codepoint is gone");
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.romp-lightbox-dl \{ font: inherit; font-size: 0\.86em;/,
    "one control vocabulary — the same chip dress as the close beside it");
});
