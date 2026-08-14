// Artifact previews (the user 2026-07-08): a completed goal that PRODUCED files (a plot, a PDF report)
// shows "N artifacts" at the bottom of its summary; the card modal renders them as click-to-expand
// previews; a path mentioned in the CHAT gets an inline thumbnail. Source pins over preview.ts /
// feed.ts / render.ts / both css sheets (render.ts has import-time DOM side effects, so no test
// imports it as a module — see distill-background.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const FEED = fs.readFileSync(path.join(UI, "feed.ts"), "utf8");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const PREVIEW = fs.readFileSync(path.join(UI, "preview.ts"), "utf8");
const FEED_CSS = fs.readFileSync(path.join(UI, "feed.css"), "utf8");
const CHAT_CSS = fs.readFileSync(path.join(UI, "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("preview.ts: kind classification, kernel /file URL, and self-removing thumbs", () => {
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
  // a thumb the kernel can't serve REMOVES ITSELF (no dead chips): img onerror, pdf HEAD probe
  assert.match(PREVIEW, /img\.onerror = \(\) => box\.remove\(\);/);
  assert.match(PREVIEW, /fetch\(fileUrl\(path, sid\), \{ method: "HEAD" \}\)\.then\(\(r\) => \{ if \(!r\.ok\) box\.remove\(\); \}\)\.catch\(\(\) => box\.remove\(\)\)/);
});

test("preview.ts: lightbox is a singleton overlay — img or native-viewer iframe, Esc/backdrop closes", () => {
  assert.match(PREVIEW, /document\.getElementById\("romp-lightbox"\)\?\.remove\(\);/, "reopening replaces, never stacks");
  assert.match(PREVIEW, /frame\.src = fileUrl\(path, sid\);/, "pdf → iframe → the browser's own viewer");
  assert.match(PREVIEW, /if \(ev\.key === "Escape"\)/);
  assert.match(PREVIEW, /wrap\.onclick = \(ev\) => \{ if \(ev\.target === wrap\) dismiss\(\); \};/, "backdrop closes; content clicks don't");
});

test("chat: a mentioned image/PDF grows a FULL-render strip under the message, deduped and capped", () => {
  // (the user 2026-07-20: full renders replaced the 2026-07-08 thumbnails in the CHAT — the feed's
  // artifact strips below keep previewThumb; chat-inline-preview.test.ts pins the full-render shape)
  assert.match(RENDER, /import \{ previewKind, previewFull, canPreview, fileUrl \} from "\.\/preview";/);
  assert.match(RENDER, /if \(previewKind\(open\) && !previewable\.includes\(open\) && !\(skipThumbs && skipThumbs\.includes\(open\)\)\) previewable\.push\(open\);/, "collected while linkifying — same detection, no second regex pass; an in-bubble image never re-renders");
  assert.match(RENDER, /if \(previewable\.length\) \{/, "both surfaces now — VS Code images ride the host data-URL flow");
  assert.match(RENDER, /previewable\.slice\(0, 4\)/, "capped so a directory listing doesn't wallpaper the chat");
  assert.match(RENDER, /previewFull\(p, activeId\)/, "relative paths resolve against the ACTIVE session's cwd, as openPathLink");
});

test("feed card: 'N artifacts' rides the bottom of the summary section and opens the modal", () => {
  assert.match(FEED, /artifacts\?: string\[\] \| null;/, "AskItem carries the kernel's existence-filtered list");
  assert.match(FEED, /const artline = el\("div", "fask-artline nav"\); artline\.style\.display = "none";/);
  assert.match(FEED, /if \(choice === "summary" && arts\.length\) \{/, "shows only with the summary section open");
  assert.match(FEED, /arts\.length === 1 \? "1 artifact" : arts\.length \+ " artifacts"/);
  assert.match(FEED, /artline\.onclick = \(ev: Event\) => \{ ev\.stopPropagation\(\); fullscreenAskId = it\.itemId; renderModal\(\); \};/);
  assert.match(FEED_CSS, /\.fask-artline \{ font-size: 0\.86em;/, "same size as the summary body it sits in");
});

test("feed modal: artifacts strip below the tree — previews on the web, open-the-file chips in VS Code", () => {
  assert.match(FEED, /applyModalArtifacts\(body, it\);/, "wired in the single-ask modal branch");
  assert.match(FEED, /const sig = arts\.join\("\\n"\);\n  if \(strip && \(strip as any\)\._sig === sig\) return;/, "sig-guarded so a kernel repush doesn't re-fetch every thumb");
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "openFile", path: p, id: it\.sid \}\);/, "no-preview fallback still opens the file");
  // a REMOTE card's artifact must open on the VIEWER's screen (the user 2026-08-14: a devbox
  // file click did nothing — `open` ran on the headless owning kernel): web origin + host-
  // prefixed sid → the /remote/<host>/file relay in a new tab, local cards keep the native open
  assert.match(FEED, /if \(hostOf\(it\.sid\) && \(location\.protocol === "http:" \|\| location\.protocol === "https:"\)\) \{\n\s*window\.open\(fileUrl\(p, it\.sid\), "_blank", "noopener,noreferrer"\);/);
  assert.match(FEED_CSS, /\.fmodal-arts \{ margin-top: 12px;/);
});

test("kernel: distiller artifacts are existence-filtered and shipped on the feed item", () => {
  assert.match(KERNEL, /def _feed_artifacts\(paths, sid\):/);
  assert.match(KERNEL, /"artifacts": _feed_artifacts\(nodes\[nid\]\.get\("artifacts"\), fsid\)/, "build_feed ships the verified list");
  assert.match(KERNEL, /if p == "\/file":/, "the preview bytes endpoint exists");
  assert.match(KERNEL, /def do_HEAD\(self\):/, "HEAD probe for chips that can't self-verify like an <img>");
});

test("the lightbox + thumb styles exist in BOTH sheets (each page loads only its own css)", () => {
  for (const css of [FEED_CSS, CHAT_CSS]) {
    assert.match(css, /#romp-lightbox \{ position: fixed; inset: 0; z-index: 1300;/);
    assert.match(css, /\.path-thumb \{ display: inline-flex;/);
    assert.match(css, /\.path-thumb-img \{ display: block; max-width: 220px; max-height: 140px;/);
  }
  assert.match(CHAT_CSS, /\.path-thumbs \{ display: flex; flex-wrap: wrap;/, "the chat strip container");
});
