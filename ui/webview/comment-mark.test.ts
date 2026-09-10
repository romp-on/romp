// The comment mark's UNREAD ring (the user 2026-09-08): a landed reply you have not opened wears the SAME
// dashed ring the tab strip puts on a session that needs you — one idiom for "this waits on you" — in
// place of the 2026-08-23 yellow corner dot. The fill keeps saying pending vs landed exactly as before
// (T237: the green .busy wash = a reply still being written, the 45% yellow .unread tier = landed); the
// ring is the only new element, and only on unread. Source pins (no jsdom harness for the renderers).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const RING = /mark\.cmt-hl\.unread \{ outline: 1\.5px dashed var\(--st-awaiting-bg\); outline-offset: 1px; \}/;

test("an unread mark wears the tab strip's needs-you ring: the same token, the same dash, scaled to a text run", () => {
  assert.match(CSS, RING);
  // the tab's ring: --state resolves to --st-awaiting-bg for a session waiting on you, drawn as a dashed outline
  assert.match(CSS, /\.tab\.tab-awaiting \{ --state: var\(--st-awaiting-bg\); \}/);
  assert.match(CSS, /\.tab\.tab-awaiting, \.tab\.tab-blocked, \.tab\.tab-retrying \{ outline: 2px dashed var\(--state\); outline-offset: -2px; \}/);
  // scaled: thinner (the dash and gap shorten with the width) and offset OUTWARD so it never crosses the glyphs
  const ring = CSS.match(RING)![0];
  assert.match(ring, /outline: 1\.5px dashed/);
  assert.match(ring, /outline-offset: 1px/);
  assert.doesNotMatch(ring, /#[0-9a-fA-F]{3,6}\b|border|box-shadow/, "the token, as an outline — nothing reflows when it comes and goes");
});

test("read marks wear no ring, and the fill tiers are byte-untouched — the colour still says pending vs landed", () => {
  const base = CSS.slice(CSS.indexOf("mark.cmt-hl {"), CSS.indexOf("mark.cmt-hl.unread {"));
  assert.doesNotMatch(base, /outline/, "the base (read) mark: no ring");
  assert.match(CSS, /mark\.cmt-hl\.unread \{ background: color-mix\(in srgb, var\(--cmt-hl\) 45%, transparent\); \}/, "landed = the 45% yellow tier (T237's pin)");
  assert.match(CSS, /mark\.cmt-hl\.busy \{\s*\n\s*background-color: color-mix\(in srgb, var\(--st-awaitbg-bg\) 24%, transparent\);/, "pending = the green wash");
  assert.match(CSS, /mark\.cmt-hl\.resolved \{ background: rgba\(255, 255, 255, 0\.08\); \}/);
  assert.match(CSS, /mark\.cmt-hl:hover \{ background: color-mix\(in srgb, var\(--cmt-hl\) 58%, transparent\); \}/);
});

test("the yellow corner dot is gone root and branch", () => {
  assert.doesNotMatch(CSS, /mark\.cmt-hl\.unread\.hl-last::after/);
  assert.doesNotMatch(CSS, /mark\.cmt-hl \{[^}]*position: relative;/s, "nothing left for the mark to anchor");
  // the segment classes stay: the radius still sits only on the run's outer ends
  assert.match(CSS, /mark\.cmt-hl\.hl-first \{ border-top-left-radius: 2px; border-bottom-left-radius: 2px; \}/);
  assert.match(CSS, /mark\.cmt-hl\.hl-last \{ border-top-right-radius: 2px; border-bottom-right-radius: 2px; \}/);
  assert.match(RENDER, /segs\[i\]\.classList\.toggle\("hl-last", i === segs\.length - 1\);/);
});

test("unread is the kernel's bit on an open thread, cleared by opening the thread — the ring follows it, nothing else", () => {
  assert.match(RENDER, /m\.classList\.toggle\("unread", !!th\.unread && th\.status === "open"\);/);
  assert.match(RENDER, /if \(th\) th\.unread = false;\s*\/\/ optimistic; the kernel's watermark reconciles/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "commentSeen", id: sid, tid \}\);/);
  // the reply chips' click OPENS the thread (the user 2026-09-10; reply-ready.test.ts pins the handler) and
  // that open is the one clearer — the chip itself never writes the bit
  const at = RENDER.indexOf("replyjump: (elx) => {");
  assert.ok(at > 0, "the chip's click handler exists");
  const click = RENDER.slice(at, RENDER.indexOf("new ResizeObserver(updateReplyChips)", at));
  assert.match(click, /openCommentPopover\(activeId, tid\);/, "the chip reaches the bit only through the thread's open");
  assert.doesNotMatch(click, /"commentSeen"|\.unread = false/);
});

test("both themes define the ring's token, in the red that means 'waiting on you' in each", () => {
  const dark = CSS.split("body.theme-light {")[0];
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  assert.match(dark, /--st-awaiting-bg: #c0392b;/);
  assert.match(light, /--st-awaiting-bg: #c0392b;/);
});
