// A session with no turns yet has an EMPTY transcript, and the kernel re-sends the full events payload on
// every push — so nothing is streaming in to wait for. It must therefore show a "No messages yet."
// placeholder, NOT the deferred "Loading transcript…" hint, which would lie and (in compact mode, where
// rebuildCompact's cache guard short-circuits a zero-event view) stick on screen forever (the user 2026-06-19).
// The chat renderer has no jsdom harness, so — like tab-switch-defer.test.ts — pin it at the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("syncView shows a 'No messages yet.' placeholder for a zero-event transcript", () => {
  // the empty branch is the FIRST thing syncView does after resolving the session, so it covers every
  // entry point (sync show, deferred build, append, compact + non-compact) uniformly.
  assert.match(RENDER, /if \(s\.events\.length === 0\) \{/);
  // (2026-07-30: a PROVISIONAL tab takes the romp loader instead — it is not empty, it is starting —
  // so the placeholder text moved to the else branch. A real empty transcript reads exactly as before.)
  assert.match(RENDER, /el\("div", "tx-empty"\); v\.el\.appendChild\(ph\);/);
  assert.match(RENDER, /\} else ph\.textContent = "No messages yet\.";/);
  // idempotent: an already-present placeholder is left alone (no churn on repeated pushes that stay empty)
  assert.match(RENDER, /only\.classList\?\.contains\("tx-empty"\)/);
});

test("the empty branch resets render bookkeeping so the first real event clears the placeholder", () => {
  // v.rendered = 0 + v.stale = false + v.winStart = 0 → the next syncView with events does a fresh tail-window
  // build (firstBuild) that removes the placeholder node and builds the turns.
  assert.match(RENDER, /v\.rendered = 0; v\.stale = false; v\.winStart = 0; v\.winEnd = 0;\s*\n\s*return v;/);
});

test("a zero-event session never shows the 'Loading transcript…' hint", () => {
  // heavy is fronted by `s.events.length > 0`, so an empty session is never deferred and never reaches the
  // loading-hint block — it renders the placeholder synchronously via syncView.
  assert.match(RENDER, /const heavy = s\.events\.length > 0 && \(/);
  // the loading hint is still the text shown for a genuinely heavy (non-empty, first-visit) build
  assert.match(RENDER, /truly empty → the ROMP LOADER holds the spot/);   // the bare text hint became the standing loader treatment (2026-08-24)
});

test(".tx-empty is styled (dim, centered) like the loading hint it replaces", () => {
  assert.match(CSS, /\.tx-empty \{[^}]*color: var\(--dim\)[^}]*text-align: center/);
});
