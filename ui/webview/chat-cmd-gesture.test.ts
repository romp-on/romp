// Durable command-gesture chip in the chat (the user 2026-08-14): the synthesized /model-/effort-/auth chip
// lives in the backend's _live tail and stale_cmd prunes it on the next human turn — the user's own gesture
// vanished from their side of the history while the left-rail applied note stayed. The kernel now writes a
// durable {"t","cmdGesture"} marker at the request moment and interleaves a `cmdGesture` event once the live
// chip retires; render.ts draws it in the SAME dress as a live command turn (turn-user flex-end → the user's
// side, ✦ + the shared mono chip), so the live→durable swap is invisible. Source pins (no jsdom for the chat
// renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("cmdGesture is a ChatEvent, dispatched to its own renderer", () => {
  assert.match(RENDER, /kind: "cmdGesture"; cmd: string; ts\?: string; uuid\?: string/);
  assert.match(RENDER, /ev\.kind === "cmdGesture"\) return renderCmdGesture\(ev\)/);
});

test("renderCmdGesture wears the live command turn's exact dress — right side, ✦ + shared chip", () => {
  const body = RENDER.slice(RENDER.indexOf("function renderCmdGesture"));
  assert.match(body, /el\("div", "turn turn-user turn-cmd"\)/);   // .turn-user's flex-end → the user's side
  assert.match(body, /dot\("user"\)/);                            // same solid rail dot as a live prompt
  assert.match(body, /el\("div", "user-bubble md cmd-row"\)/);
  assert.match(body, /renderSlashCmd\(bubble, ev\.cmd\)/);        // the SHARED chip renderer, not a copy
  assert.match(body, /bubble\.textContent = ev\.cmd/);            // non-command text still shows, never drops
});

test("no edit/delete/fork affordances — a gesture is not a rewindable message", () => {
  const body = RENDER.slice(RENDER.indexOf("function renderCmdGesture"),
                            RENDER.indexOf("function", RENDER.indexOf("function renderCmdGesture") + 10));
  assert.doesNotMatch(body, /msg-edit|msg-del|msg-fork/);
});

test("the gesture chip's ink is the outgoing-bubble blue on the chat's own ground", () => {
  // the user 2026-08-14: the --code-bg terracotta tint read as a stray red — the chip now borrows
  // .user-bubble's #2b6cef for border + text so the gesture still reads as "yours" without the bubble
  assert.match(CSS, /\.user-bubble\.cmd-row \.slash-cmd-chip \{ background: var\(--bg\); color: var\(--you\);/);
  assert.match(CSS, /\.user-bubble\.cmd-row \.slash-cmd-chip \{[^}]*border-color: var\(--you\)/);
});
