// The chat statusline's FAST badge — a fourth meta control (mode · model · effort · fast) toggling the
// CLI's fast mode (/fast, Opus-only research preview). The badge exists only when the session REPORTS a
// fast state — the SDK init's fast_mode_state ("on"/"off"/"cooldown"), threaded kernel → status → badge —
// AND the model can run fast mode at all (fastAvailable: the CLI reports "off" with an EMPTY
// disabled_reason on a non-Opus session, verified 2026-08-10 against 2.1.226 on a fable session, so
// state alone would leave a dead toggle there). The label is ONE WORD (the user 2026-08-10, on a
// phone-width statusline) and the word carries the state: "Fast" on, "Slow" off (the user 2026-08-11 —
// tint alone didn't say which side the toggle was on). Picking On/Off posts setFast; the kernel delivers
// the literal "/fast on|off", which the SDK input stream interprets (its CLI descriptor is marked
// supportsNonInteractive, unlike /model).
// render.ts has no jsdom harness → source pins (kernel pins ride along, as in the other meta tests).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDK = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the fast badge appears only when the session reports a state AND the model can run it", () => {
  assert.match(RENDER, /fast\?: string;/);                                   // status carries it
  assert.match(RENDER, /type MetaKind = "mode" \| "model" \| "effort" \| "fast";/);   // billing moved to the tab menu (2026-08-09)
  assert.match(RENDER, /st\.fast && fastAvailable\(st\) \? st\.fast : ""/);  // no state / unsupported model → no badge
  assert.match(RENDER, /meta\.appendChild\(metaButton\("fast", prettyFast\(fast\), forSid\)\)/);   // sid-scoped: the popover statusline shares this builder (2026-08-25)
  // the availability gate: opus-family (or unknown/default — the account default may be Opus)
  const gate = RENDER.match(/function fastAvailable\(st: Status\): boolean \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(gate, /!m \|\| m === "default" \|\| m\.includes\("opus"\)/);
});

test("the badge label is one word, and the word carries the state: Fast on, Slow off", () => {
  const pf = RENDER.match(/function prettyFast[\s\S]*?\n\}/)?.[0] || "";
  assert.match(pf, /"Cooldown"/);
  assert.match(pf, /s === "on" \? "Fast" : "Slow"/);   // off is not a second "Fast" (the user 2026-08-11)
  assert.doesNotMatch(pf, /Fast on|Fast off/);
});

test("the picker speaks the badge's words — Fast/Slow, never On/Off — and posts setFast", () => {
  // one vocabulary for one toggle (the user 2026-08-11: a badge reading "Slow" opened a menu of
  // "On"/"Off"); the VALUES stay the wire's on/off — only the labels wear the badge's words
  assert.match(RENDER, /\{ label: "Fast", value: "on" \}/);
  assert.match(RENDER, /\{ label: "Slow", value: "off" \}/);
  assert.doesNotMatch(RENDER, /\{ label: "On", value: "on" \}/);
  assert.match(RENDER, /fast: FAST_CHOICES/);
  assert.match(RENDER, /kind === "fast" \? "setFast"/);                      // the pick posts the op
  assert.match(RENDER, /"on" \? "var\(--fast\)" : ""/);                      // ON tint, off/cooldown default
  assert.match(CSS, /--fast: #ff6a00;/);                                     // the CLI's own fastMode orange
});

test("the kernel threads fast_mode_state from the SDK init to the chat status", () => {
  assert.match(SDK, /d\.get\("fast_mode_state"\)/);                          // init is the authoritative source
  assert.match(SDK, /"fast": self\.fast/);                                   // snapshot carries it
  assert.match(KERNEL, /"fast": st\.get\("fast", ""\)/);                     // merged into the live map
  // a disabled_reason (org-gated / unsupported) hides the toggle rather than offering a dead control
  assert.match(KERNEL, /"fast": "" if tm\.get\("fastReason"\) else tm\.get\("fast", ""\)/);
});

test("a refusal answering the user's ask is loud — warn toast, ask cleared, badge restored", () => {
  // Behavior is covered in tests/test_sdk_backend.py; these pins keep the shape findable from the
  // badge's own test. Before this, the CLI's refusal (e.g. extra_usage_disabled) just hid the toggle
  // the user had JUST clicked — a silently vanishing button (the user 2026-08-11, on a phone).
  const adopt = SDK.match(/def _adopt_fast_state[\s\S]*?\n    async def /)?.[0] || "";
  assert.match(adopt, /refused_ask = bool\(reason\) and self\.fast_opt/);
  assert.match(adopt, /_FAST_REFUSALS/);                 // humanized reason in the toast
  assert.match(adopt, /"type": "warn"/);                 // fail loudly, not a vanishing control
  assert.match(adopt, /self\.request_reconnect\(\)/);    // flagless reconnect → the badge comes back
});

test("setFast is a drive op that parks like /model and /effort", () => {
  assert.match(KERNEL, /"setModel", "setEffort", "setMode", "setFast",/);    // in the drive-op allowlist
  assert.match(KERNEL, /def _set_fast_or_park\(be, sid, value\)/);
  assert.match(KERNEL, /op\[0\] in \("model", "effort", "fast"\)/);          // repeat pick replaces in place
  assert.match(KERNEL, /elif op\[0\] == "fast":/);                           // parked delivery branch
});
