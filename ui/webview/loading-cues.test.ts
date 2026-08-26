// Two waiting states got the romp loader treatment (the user 2026-07-31):
//   1. A mentioned image whose bytes ride the ssh tunnel showed only its path for a beat, then the
//      picture "popped in" — now a mini spinning romp swirl holds the spot until the <img> load
//      event lands (event-based), memoized per URL so chat re-renders never re-flash it.
//   2. Undo clear with no cached batch is a full kernel round-trip and the button read dead — now
//      it wears an accent border + three pulsing accent dots, WITHOUT disabling (each further click
//      pops an older batch), cleared by the next feed payload with a timeout backstop.
// Source pins (no jsdom harness for these paths), like the other loader/tripwire tests.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const PREVIEW = fs.readFileSync(path.join(UI, "preview.ts"), "utf8");
const FEED = fs.readFileSync(path.join(UI, "feed.ts"), "utf8");
const FEED_CSS = fs.readFileSync(path.join(UI, "feed.css"), "utf8");
const CHAT_CSS = fs.readFileSync(path.join(UI, "styles.css"), "utf8");

test("previews: the mini swirl holds the spot until the load EVENT; first load of a URL only", () => {
  assert.match(PREVIEW, /const loadedOnce = new Set<string>\(\);/);
  assert.match(PREVIEW, /if \(loadedOnce\.has\(url\)\) return;/, "a URL that already loaded never re-spins");
  assert.match(PREVIEW, /spin\.src = mediaSrc\("romp-swirl-glyph\.svg"\);/, "mediaSrc — resolves on BOTH surfaces");
  assert.match(PREVIEW, /img\.addEventListener\("load", \(\) => \{\s*\n\s*loadedOnce\.add\(url\);\s*\n\s*spin\.remove\(\);/,
               "the cue clears on the load event, never a timer");
  // both builders wear it — the chat's full-size render and the feed modal's thumb
  const cues = PREVIEW.match(/withLoadCue\(box, img, url\);/g) || [];
  assert.equal(cues.length, 2, "previewThumb AND previewFull");
});

test("previews: the cue CSS lives in BOTH sheets (each page loads only its own — the .romp-acted precedent)", () => {
  for (const css of [FEED_CSS, CHAT_CSS]) {
    assert.match(css, /\.path-load-spin \{ display: block; width: 20px; height: 20px;/);
    assert.match(css, /@keyframes path-load-spin \{ to \{ transform: rotate\(-360deg\); \} \}/, "the reverse romp spin");
    assert.match(css, /\.path-img-loading \{ display: none; \}/, "the <img> takes the spot the instant it has pixels");
    assert.match(css, /prefers-reduced-motion: reduce\) \{ \.path-load-spin \{ animation: none; \} \}/);
  }
});

test("undo clear: the round-trip branch arms a busy cue that never disables the button", () => {
  // armed ONLY on the cache-miss branch — the optimistic restore's card appearing IS its feedback
  assert.match(FEED, /pendingCleared\.clear\(\);[\s\S]{0,700}b\.classList\.add\("undo-busy"\);/);
  assert.doesNotMatch(FEED, /feed-undoclear[\s\S]{0,2000}\.disabled = true/, "repeat clicks must keep popping older batches");
  assert.match(FEED, /undoBusyBackstop = window\.setTimeout\(clearUndoBusy, 6000\);/, "backstop — a lost push can't trap the cue");
});

test("undo clear: the cue clears on the NEXT feed payload (the event it waits for)", () => {
  assert.match(FEED, /if \(typeof m\.dismissedCount === "number"\) dismissedCount = m\.dismissedCount;\s*\n\s*clearUndoBusy\(\);/);
  assert.match(FEED, /function clearUndoBusy\(\): void \{/);
  assert.match(FEED, /b\.querySelector\("\.undo-dots"\)\?\.remove\(\);/);
});

test("undo clear: accent dots + accent border, reduced-motion safe (feed.css — the feed page's own sheet)", () => {
  assert.match(FEED_CSS, /#feed-undoclear\.undo-busy \{ border-color: var\(--accent\); \}/);
  assert.match(FEED_CSS, /#feed-undoclear \.undo-dots i \{ width: 4px; height: 4px; border-radius: 50%; background: var\(--accent\);/);
  assert.match(FEED_CSS, /@keyframes undo-dot \{ 0%, 100% \{ opacity: 0\.25; \} 40% \{ opacity: 1; \} \}/);
  assert.match(FEED_CSS, /prefers-reduced-motion: reduce\) \{ #feed-undoclear \.undo-dots i \{ animation: none; opacity: 0\.8; \} \}/,
    "the host-load strip wears the shared swirl now (its own reduced-motion rule) — the undo dots stand alone again");
});
