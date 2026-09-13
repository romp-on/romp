// Cross-surface hover wiring (the user 2026-06-12). The chat↔feed↔timeline
// highlight join had asymmetric gaps. These pin the fixes at the SOURCE level —
// the wiring lives in DOM event handlers with no jsdom harness here, so (as the
// timeline-view tests do) we assert the handlers exist and are attached to the
// right element. The kernel side of the join (hover push, glow, nonce) moved to
// the Python kernel (bin/romp-kernel); it's covered by tests/test_kernel.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), ...p), "utf8");
const RENDER = read("..", "ui", "webview", "render.ts");
const FEED = read("..", "ui", "webview", "feed.ts");

// The user 2026-06-15: hovering the message TEXT must NOT light the timeline — only the rail DOT (the
// "timeline" gutter) does. The hover target is the DOT (turn fallback only when a turn has no dot).
test("chat hover is wired on the rail dot, not the whole turn", () => {
  assert.match(RENDER, /wireTurnHover\(turn, railDot/, "call site still passes turn + dot");
  assert.match(RENDER, /function wireTurnHover\(turn: HTMLElement, dot: HTMLElement \| null/);
  assert.match(RENDER, /const hoverTarget = dot \|\| turn/, "the hover target is the dot, falling back to turn");
  assert.match(RENDER, /hoverTarget\.addEventListener\("mouseenter"/, "hover (mouseenter) is on the dot");
  assert.match(RENDER, /hoverTarget\.addEventListener\("mouseleave"/);
});

test("the rail dot keeps the click (open the feed card), separate from turn hover", () => {
  // the click must be on the DOT, not the turn — clicking anywhere in a long work
  // turn shouldn't open the card.
  assert.match(RENDER, /dot\.addEventListener\("click"[\s\S]*?type: "dotOpen"/);
});

// #9b — the feed MODAL title, hovered, lights the originating message in the chat
// (and its timeline glyph). Assignable props (not addEventListener) so a re-render
// overwrites instead of stacking handlers; clears for modals with no chat anchor.
test("the feed modal title hover lights the originating chat message", () => {
  assert.match(FEED, /ttlEl\.onmouseenter = titleHoverId \? \(\) => hoverEmit\(titleHoverId\) : null/);
  assert.match(FEED, /ttlEl\.onmouseleave = titleHoverId \? \(\) => hoverEmit\(null\) : null/);
  assert.match(FEED, /titleHoverId = it\.turnId/);
  assert.match(FEED, /titleHoverId = grp\.turnId/);
});

// #11 — a timeline (or any) jump lands the target at the TOP of the viewport so
// you read DOWN into it, not centered with its start scrolled off.
test("landOn top-aligns the target (block:'start'), never centers", () => {
  const landOn = RENDER.slice(RENDER.indexOf("function landOn("));
  const body = landOn.slice(0, landOn.indexOf("\n}\n"));
  assert.match(body, /scrollElInto\(c, at, "start", writer\)/, "lands at the top — through the write helper, attributed (T262j); `at` is the aligned element, the turn or the words in it (T386)");
  assert.doesNotMatch(body, /block: "center"/, "must not center the landing");
});

// Ask preview — the live picker card reproduces the FOCUSED option's side-by-side TUI box (the user
// 2026-06-13), now FOCUS-AWARE so ↑/↓ swaps it (the user 2026-06-22): the focused option's OWN preview
// (per option; ParsedAsk.preview only from an older kernel). Rendered as a monospace <pre> via
// textContent (NEVER innerHTML: the pane text is untrusted terminal output), and REPLACED not appended.
test("the live ask card renders the focused option's preview as a monospace pre via textContent", () => {
  assert.match(RENDER, /renderAskPreview\(\);/);
  const fn = RENDER.slice(RENDER.indexOf("function renderAskPreview("));
  const body = fn.slice(0, fn.indexOf("\n}\n"));
  assert.match(body, /preview = \(o && o\.preview\) \|\| ask\.preview/, "focused option's own preview, else the scraped one");
  assert.match(body, /el\("pre", "ask-preview"\)/);
  assert.match(body, /pre\.textContent = preview/);
  assert.doesNotMatch(body, /innerHTML/, "untrusted pane text must never go through innerHTML");
});

// Diff colorization (the user 2026-06-27): an Edit/Write permission on the SDK backend sends
// previewKind:"diff", and the preview is rendered with per-line +/- coloring — still textContent per row,
// never innerHTML, so untrusted tool output can't inject markup.
test("a diff preview is colorized per line, still via textContent", () => {
  const fn = RENDER.slice(RENDER.indexOf("function renderAskPreview("));
  const body = fn.slice(0, fn.indexOf("\n}\n"));
  assert.match(body, /ask\.previewKind === "diff"/, "diff kind switches on colorized rendering");
  assert.match(body, /preview\.split\("\\n"\)\.map\(diffLineEl\)/, "splits into per-line rows");
  const dl = RENDER.slice(RENDER.indexOf("function diffLineEl("));
  const dbody = dl.slice(0, dl.indexOf("\n}\n"));
  assert.match(dbody, /startsWith\("\+"\)\s*\?\s*"diff-add"/);
  assert.match(dbody, /startsWith\("-"\)\s*\?\s*"diff-del"/);
  assert.match(dbody, /startsWith\("@@"\)\s*\?\s*"diff-hunk"/);
  assert.match(dbody, /row\.textContent =/, "each row's text via textContent");
  assert.doesNotMatch(dbody, /innerHTML/, "diff rows never use innerHTML");
});
