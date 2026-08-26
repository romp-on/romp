// Silent-failure surfacing (the user 2026-07-10): creating a session on an unreachable remote host
// (TESTHOST, kernel down behind an alive ssh tunnel) produced NO feedback — the kernel's `warn` messages
// had no webview handler, and federation dropped any outbound message routed to a host whose socket
// wasn't open. Now every kernel `warn` toasts on the right, and a dropped route synthesizes a local
// warn naming the host and the action. No jsdom harness for these renderers, so pin the wiring at the
// source (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const FEDERATION = ui("webview", "federation.ts");
const STYLES = ui("webview", "styles.css");

test("render handles kernel warn messages with a toast", () => {
  // (2026-07-30: a warn arriving while a create is in flight IS that create's verdict, so it takes the
  // dialog and retires the provisional tab. Every other warn still gets the toast.)
  assert.match(RENDER, /if \(provisionalId\) failProvisional\(m\.text\); else warnToast\(m\.text\);/);
  assert.match(RENDER, /function warnToast\(msg: string\)/);
});

test("warn toasts stack on the right and dismiss on click", () => {
  assert.match(RENDER, /getElementById\("warn-toasts"\)/);
  assert.match(STYLES, /#warn-toasts \{\s*\n\s*position: fixed; top: 44px; right: 14px;/);
  assert.match(STYLES, /\.warn-toast \{/);
});

test("the family dismissal: a visible ✕, Escape clears the stack, click-safe by construction", () => {
  // the user 2026-08-25: the notice floats over the tab strip and "gets in the way" — useful copy,
  // no visible way out. ONE shared treatment on the family renderer, not per-mint-site ✕s.
  assert.match(RENDER, /const x = el\("button", "warn-toast-x"\);/);
  assert.match(RENDER, /x\.setAttribute\("aria-label", "Dismiss"\);/);
  // dismissal rides ONE delegated handler on the stable container (created once, toasts appended —
  // the standing click-safety rule), so the ✕ AND the toast body both dismiss, and removal is the
  // immediate acknowledgement
  assert.match(RENDER, /box\.addEventListener\("click", \(e\) => \{\s*\n\s*\(e\.target as HTMLElement \| null\)\?\.closest\("\.warn-toast"\)\?\.remove\(\);/);
  // Escape clears the stack, additively — it never stops propagation, so no other surface loses the key
  assert.match(RENDER, /if \(e\.key === "Escape"\) for \(const w of Array\.from\(box!\.children\)\) w\.remove\(\);/);
  assert.doesNotMatch(RENDER.split('if (e.key === "Escape") for (const w of')[1].slice(0, 120), /stopPropagation/);
  // the ✕ wears the chip-✕ dress: dim at rest, separate, hover-brightens
  assert.match(STYLES, /\.warn-toast-x \{\s*\n\s*flex: 0 0 auto; border: none; background: none; cursor: pointer; color: var\(--dim\);/);
  assert.match(STYLES, /\.warn-toast-x:hover \{ color: var\(--fg\); background: rgba\(255, 255, 255, 0\.08\); \}/);
  // the auto-fade stays — dismissal is ADDITIVE to the timeout, a user gesture always beats the event
  assert.match(RENDER, /setTimeout\(\(\) => t\.classList\.add\("fade"\), 11000\);/);
});

test("the specimen's copy is untouched — only the trap was the bug", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.ok(KERNEL.includes("that message predates the last context compaction; only newer passages can open a thread"),
    "the comment-thread refusal keeps its exact wording — the user likes the information");
});

test("federation surfaces a dropped route instead of vanishing it", () => {
  // both send sites carry the else — the undoClear fast path and the main route loop
  const drops = FEDERATION.match(/else this\.dropWarn\(/g) || [];
  assert.equal(drops.length, 2, "every not-open host socket send has a dropWarn else");
  assert.match(FEDERATION, /private dropWarn\(host: string, msg: any\)/);
  assert.match(FEDERATION, /was not delivered/);
});
