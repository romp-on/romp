// Bare file:// URLs in a CHAT message (the user 2026-07-06): a link like
// file:///Users/me/analysis/trace.pdf pasted into a message should be clickable and open the file — marked
// doesn't autolink the file: scheme and DOMPurify strips it, so linkifyFileUris wraps them post-render into
// a clickable .file-uri-link that routes to the host opener. NOT applied to tool-use summaries. The renderer
// has no jsdom harness, so pin the wiring at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const LINKS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "path-links.ts"), "utf8");   // the matcher, lifted out of render.ts
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a bare file:// URL becomes a clickable .file-uri-link that opens the file in the host app", () => {
  assert.match(RENDER, /function linkifyFileUris\(root: HTMLElement, skipThumbs\?: string\[\], spacePaths\?: string\[\],\s*\n\s*pathLinks\?: Record<string, string>, pathPins\?: Record<string, string>\): void/);
  assert.match(LINKS, /el\("span", "file-uri-link"\)/);
  // clicking is ROUTED by openPath, never a blocked window.open(file://) — a file:// URI is absolute,
  // so it takes the shared openPathLink's no-session-id branch (no data-rel on the span; render.ts's
  // bindPathLink then sends no session id)
  assert.match(LINKS, /function fileUriLink\(uri: string\): HTMLElement \{ return openPathLink\(uri, fileUriToPath\(uri\)\); \}/);
  assert.match(RENDER, /openPath\(open, relative \? activeId : null, e\);/);
  // the URL is turned into a real filesystem path: scheme stripped, percent-decoded
  assert.match(LINKS, /const FILE_URI_RE = \/\^file:\\\/\\\/\(\?:localhost\)\?\(\?=\\\/\)\/i;/, "a LOCAL URI: an empty authority or localhost; file://host/x names another machine and stays prose");
  assert.match(LINKS, /decodeURIComponent\(p\)/);
});

test("linkify runs on chat message bodies (assistant reply + user bubble + nudge full text) and user-todo notes — never tool summaries", () => {
  assert.match(RENDER, /linkifyFileUris\(body, undefined, ev\.spacePaths, ev\.pathLinks, ev\.pathPins\)/);   // the assistant reply
  assert.match(RENDER, /linkifyFileUris\(bubble, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins\)/); // your own bubble (in-bubble images don't re-thumb)
  assert.match(RENDER, /linkifyFileUris\(full, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins\)/);   // a compact nudge's expanded full text (2026-07-17)
  // …plus a user todo's note (plans/user-todos.md): in the card's detail fold and quoted in the Reply
  // dialog — an agent's note about the user's own project, where a path should open like one in a
  // message. Shape-only (no per-message kernel verdict exists for a note), so the root alone is passed.
  assert.match(RENDER, /linkifyFileUris\(d\);/);
  assert.match(RENDER, /linkifyFileUris\(dd\);/);
  // exactly the definition + those five applications — so tool-use reports/summaries stay untouched
  const uses = RENDER.match(/linkifyFileUris\(/g) || [];
  assert.equal(uses.length, 6, "linkifyFileUris is defined once and applied to the three chat bodies + the two todo-note sites");
});

test("linkify works inside INLINE backticks (agents backtick paths), skips only fenced code + existing links, trims trailing punctuation", () => {
  // inline <code> is NOT skipped — a `file://…` path in backticks still linkifies; only fenced <pre> + links are skipped
  assert.match(LINKS, /export const DEAD_TEXT = "a, \.file-uri-link, svg";/);
  assert.match(LINKS, /const skip = opts && opts\.inPre \? DEAD_TEXT : DEAD_TEXT \+ ", pre";/, "the chat skips a link, an inline SVG and a fenced block; never inline code");
  assert.doesNotMatch(LINKS, /DEAD_TEXT \+ ", code/);
  assert.match(LINKS, /tok = tok\.slice\(0, tok\.length - trail\[0\]\.length\)/);
});

test(".file-uri-link is styled as a wrapping accent link", () => {
  assert.match(CSS, /\.file-uri-link \{[\s\S]*?cursor: pointer[\s\S]*?color: var\(--accent\)/);
  assert.match(CSS, /\.file-uri-link:hover \{ text-decoration: underline; \}/);
});
