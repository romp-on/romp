// The revive loader (the user 2026-07-05; PANE-LOCAL 2026-08-25): reviving a dead session takes
// seconds and the Revive click must acknowledge at once — but the loader used to cover the WHOLE
// window and block everything (the user: it should revive the tab immediately and show the loading
// thing on that session only, so you can switch to something else while it loads). Now the gesture
// mints/foregrounds the TAB immediately, the romp loader (.rl-* treatment) covers only that session's
// thread area while it is the active tab, and every other tab stays fully interactive. Cleared
// EVENT-based (the kernel's focus / reviveFailed), 60s backstop. Failure lands pane-local: the named
// error in the session's own placeholder + the dismissible warn-toast family. Source pin.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the Revive click acknowledges at once: post reviveSession AND show the loader", () => {
  assert.match(RENDER,
    /if \(v === "revive"\) \{ vscodeApi\?\.postMessage\(\{ type: "reviveSession", id: m\.id \}\); showReviveLoader\(m\.id, nm\); \}/);
});

test("the loader is the romp treatment: wordmark + swirl + pulsing dots + caption", () => {
  // ONE shared builder (rompLoaderInner) carries the anatomy — the revive loader and the comment
  // popover's boot both wear it, per the loading-states rule
  assert.match(RENDER, /function rompLoaderInner\(caption: string, opts\?: \{ wordmark\?: boolean \}\): HTMLElement/);   // parameterized, never forked (the popover drops the wordmark; everything else keeps it)
  assert.match(RENDER, /const word = el\("div", "rl-word"\)/, "the shared .rl-* anatomy");
  assert.match(RENDER, /swirl\.src = mediaSrc\("romp-swirl-o\.svg"\)/);
  assert.match(RENDER, /const dots = el\("div", "rl-dots"\)/);
  assert.match(RENDER, /o\.appendChild\(rompLoaderInner\(`reviving “\$\{name\}”…`\)\);/);
});

test("event-based clear: the kernel's focus for the reviving sid retires the loader", () => {
  assert.match(RENDER, /if \(revivePending && m\.id === revivePending\) clearReviveLoader\(\);/);
});

test("the tab mints immediately: stub session + order + setActive, before any waiting", () => {
  // the openProvisional idiom — "opening" is the designed vocabulary for a tab whose payload is coming
  assert.match(RENDER, /if \(!sessions\.has\(id\)\) \{\s*\n\s*sessions\.set\(id, \{ id, name, color: null, events: \[\], status: \{ state: "opening", sinceEpoch: Date\.now\(\) \} \}\);\s*\n\s*order\.push\(id\);/);
  const fn = RENDER.split("function showReviveLoader(")[1].split("\nfunction ")[0];
  assert.ok(fn.includes("renderTabs();"), "the tab is on the strip at once");
  assert.ok(fn.includes("setActive(id);"), "and foregrounded");
});

test("the loader is SESSION-LOCAL: over the thread area only, shown only while that tab is active", () => {
  // geometry: #content's measured box, never the window — the tab strip and composer stay live
  assert.match(RENDER, /if \(!c \|\| activeId !== revivePending\) \{ o\.style\.display = "none"; return; \}/);
  assert.match(RENDER, /const r = c\.getBoundingClientRect\(\);/);
  // switch-away-and-back mid-revive: showActive re-places on every tab switch and push re-render
  assert.match(RENDER, /placeReviveLoader\(\);   \/\/ session-local: shows over THIS pane only while the reviving tab is active/);
  // …and #content resizes (tab bar wrapping, ledger appearing) re-place it too
  assert.match(RENDER, /reviveRo = new ResizeObserver\(placeReviveLoader\);/);
  // the absence pin: no window-level blocker — the overlay never spans the window again
  assert.doesNotMatch(CSS, /#revive-loader \{ position: fixed; inset: 0;/);
});

test("failure is loud AND pane-local: named error in the session's placeholder + the dismissible toast", () => {
  assert.match(RENDER, /m\.type === "reviveFailed" && m\.id/);
  assert.match(RENDER, /reviveFailedLocal\(String\(m\.id\), String\(m\.name \|\| m\.id\), String\(m\.text \|\| "unknown error"\)\)/);
  assert.match(RENDER, /failedRevives\.set\(id, msg\);/);
  assert.match(RENDER, /warnToast\(msg\);/, "the warn-toast family — dismissible (✕ / Esc), never window-blocking");
  // the session's own pane says it: the empty-transcript placeholder wears the named failure
  assert.match(RENDER, /if \(failedRevives\.has\(id\)\) \{\s*\n\s*ph\.textContent = failedRevives\.get\(id\) \|\| "";\s*\n\s*ph\.classList\.add\("tx-revive-failed"\);/);
  assert.match(CSS, /\.tx-empty\.tx-revive-failed \{ color: var\(--vscode-errorForeground, #f48771\); \}/);
  // a fresh revive attempt clears the parked failure — the gesture beats the stale verdict
  assert.match(RENDER, /failedRevives\.delete\(id\);/);
});

test("a 60s backstop keeps the loader from trapping the user", () => {
  assert.match(RENDER, /reviveBackstop = window\.setTimeout\(/);
  assert.match(RENDER, /, 60000\)/);
});

test("the loader has styles: dimming backdrop + caption; the error-box family is gone", () => {
  assert.match(CSS, /#revive-loader \{ position: fixed; z-index: 65; display: flex;/);
  assert.match(CSS, /#revive-loader \.revive-cap \{/);
  assert.doesNotMatch(CSS, /revive-err/);
});

test("the .rl-* anatomy lives in styles.css itself — the VS Code chat page loads this sheet ALONE", () => {
  // kernel-served pages also inject _LOADER_CSS, but the VS Code webview never sees that block:
  // without these rules the revive loader rendered as an unstyled div stack there (2026-08-26)
  assert.match(CSS, /\.rl-in \{ display: flex; flex-direction: column; align-items: center; gap: 18px; \}/);
  assert.match(CSS, /\.rl-word \{ font-family: 'RompAnta', var\(--sans\);/);
  assert.match(CSS, /\.rl-o \{[^}]*animation: rl-spin 7s linear infinite/);
  assert.match(CSS, /\.rl-dots i \{[^}]*background: var\(--accent\)/);
  assert.match(CSS, /@keyframes rl-bnc/);
  assert.match(CSS, /@keyframes rl-spin/);
  assert.match(CSS, /@font-face \{ font-family: 'RompAnta'; src: url\(\.\.\/media\/Anta-Regular\.ttf\)/);
});
