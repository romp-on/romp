// An explicit send button on the right of the composer (the user 2026-06-17), in addition to ⏎ — both go
// through one sendComposer() path. Touch devices have no easy Enter; desktop gets a click affordance too.
// Source-level pin (no jsdom for the chat renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKELETON = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");

test("the composer markup includes a send button to the right of 📎", () => {
  assert.match(SKELETON, /<button id="composer-send"[^>]*aria-label="Send">/);
  // 📎 then send, so send is rightmost
  assert.match(SKELETON, /id="composer-attach"[\s\S]*id="composer-send"/);
});

test("⏎ and the send button share ONE sendComposer() path", () => {
  assert.match(RENDER, /const sendComposer = \(opts\?: \{ pastShipGate\?: boolean \}\) => \{/);   // the opts are the ship gate's re-entry door (composer-ship-gate.test.ts)
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "sendMessage", id: sid, text \}\)/);   // routeUserMessage — one routing owner since the staged flush (2026-08-15)
  // Enter calls it (desktop only — the mobile guard is asserted separately below)
  assert.match(RENDER, /if \(e\.key === "Enter" && !e\.shiftKey && !isCoarsePointer\(\)\) \{\s*e\.preventDefault\(\);\s*sendComposer\(\);/);
  // the button calls it (mousedown keeps textarea focus on desktop; on a phone it blurs so the keyboard
  // collapses and the box drops back to the bottom — see composer-send-blur.test.ts)
  assert.match(RENDER, /sendBtn\?\.addEventListener\("mousedown", \(e\) => \{ e\.preventDefault\(\); sendComposer\(\); if \(isCoarsePointer\(\)\) ta\.blur\(\); else ta\.focus\(\); \}\)/);
});

test("on a phone (coarse pointer) Enter is a newline, not send — the Send button is the only send (the user 2026-07-15)", () => {
  // mobile keyboards often can't do Shift+Enter, and the software return key should just return. The Enter-to-send
  // path is gated on !isCoarsePointer(), so on touch Enter falls through to the textarea's native newline; sending
  // is the explicit Send button (its mousedown handler is unguarded, so it still sends on touch).
  assert.match(RENDER, /function isCoarsePointer\(\)/);
  assert.match(RENDER, /matchMedia\("\(pointer:coarse\)"\)\.matches/);
  assert.match(RENDER, /e\.key === "Enter" && !e\.shiftKey && !isCoarsePointer\(\)/);
  // the resting placeholder drops every hint on mobile (⏎/⇧⏎ is wrong there, and even the "/" hint
  // wrapped and clipped the one-line pill)
  assert.match(RENDER, /function composerRestingPlaceholder\(\)/);
  assert.match(RENDER, /if \(isCoarsePointer\(\)\) return "Message this session…";/);
  // …and on a NARROW desktop pane (the user 2026-08-26: the full hint wrapped onto a clipped second
  // line) the resting hint adapts to the box's width: under 620px it keeps only the core prompt +
  // the one undiscoverable key. Re-fitted event-based on resize, and ONLY while a resting form is
  // showing — a picker's "add your own answer…" or the closed-session notice is never clobbered.
  assert.match(RENDER, /if \(ta && ta\.clientWidth > 0 && ta\.clientWidth < 620\) return "Message this session…  \(\/ for commands\)";/);
  assert.match(RENDER, /if \(ta\.placeholder\.startsWith\("Message this session…"\)\) ta\.placeholder = composerRestingPlaceholder\(\);/);
  assert.match(RENDER, /\}\)\.observe\(ta\);/, "the re-fit is a ResizeObserver, not a poll");
});

test("on a phone the resting box is ONE line — the Signal-style pill — with a placeholder that fits it (the user 2026-07-30)", () => {
  // the old two-line floor existed only to show a wrapped placeholder; the placeholder is short now, so
  // the floor is gone and the empty composer rests as a single-line pill between the two circles
  assert.doesNotMatch(CSS, /min-height: calc\(2\.8em/);
  assert.match(CSS, /@media \(pointer: coarse\) \{[\s\S]*?#composer-input \{ min-height: 40px;/);
});

test("⏎ jumps focus to the tab bar after sending so ←/→ switch sessions (the user 2026-06-25)", () => {
  // after the Enter-send, focusActiveTab() moves focus off the composer onto the active tab, so the next
  // ←/→ hits onTabKey (tab switch) instead of the textarea caret. The send BUTTON keeps composer focus.
  assert.match(RENDER, /sendComposer\(\);\s*focusActiveTab\(\);/);
  assert.match(RENDER, /function focusActiveTab\(\)/);
});

test("Escape ↔ Enter toggle focus between the chat box and the tab bar (the user 2026-06-25)", () => {
  // Escape in the composer → tab mode (focus the active tab, ←/→ switch sessions); a draft is untouched.
  assert.match(RENDER, /if \(e\.key === "Escape"\) \{[\s\S]*?focusActiveTab\(\);[\s\S]*?return;/);
  // Enter on a focused tab (onTabKey) → drop back into the chat box of the selected session
  assert.match(RENDER, /else if \(e\.key === "Enter"\) \{[\s\S]*?getElementById\("composer-input"\)[\s\S]*?\?\.focus\(\);/);
});

test("the send button is disabled on a closed (read-only) session", () => {
  assert.match(RENDER, /if \(sendBtn\) sendBtn\.disabled = closed/);
});

test("send sits on the input's right, the paperclip on its left — flex order, not offsets", () => {
  // re-laid 2026-07-30 as the Signal-style compose row; the circle sizing and the touch layout live in
  // composer-buttons.test.ts
  assert.match(CSS, /#composer-attach, \.cmt-attach \{ color: var\(--accent\); opacity: 0\.8; order: 1; \}/);
  assert.match(CSS, /#composer-send, \.cmt-send \{ order: 3; \}/);
});

test("the composer sits tight to the bottom — no wasted gap below it (the user 2026-06-23)", () => {
  // the bottom padding was trimmed 12px → 6px so the box hugs the pane's bottom; the buttons ride the
  // row's bottom edge by flex (align-items: flex-end), with no offsets to keep in step any more.
  assert.match(CSS, /#composer \{[^}]*padding: 8px 24px 6px;/);
  assert.match(CSS, /#composer \{[^}]*align-items: flex-end;/);
});

test("focusing a tab (after ⏎-send) draws NO white UA focus ring around its colored border (the user 2026-06-25)", () => {
  // ⏎-send / Escape move focus onto the active tab; the base .tab rule sets outline:none so the browser's
  // default focus outline doesn't draw a redundant white ring around the identity-colored border.
  assert.match(CSS, /\.tab \{[^}]*outline: none;[^}]*\}/);
  // the dashed STATE outlines stay (higher specificity than the base .tab rule, so outline:none can't kill them)
  assert.match(CSS, /\.tab\.tab-awaiting, \.tab\.tab-blocked, \.tab\.tab-retrying \{ outline: 2px dashed/);
});

test("a staged chip clips IN BOUNDS with an ellipsis and expands on click (the user 2026-08-15)", () => {
  const STYLES = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  // the flex label could never shrink (no min-width:0), so long texts ran off the pane edge with no
  // ellipsis; expanded, the same label wraps to the full text — the context-fold idiom
  assert.match(STYLES, /\.staged-chip \.composer-chip-label \{ flex: 1 1 auto; max-width: 100%; min-width: 0; \}/);
  assert.match(STYLES, /\.staged-chip\.open \.staged-row \.composer-chip-label \{ white-space: pre-wrap; overflow: visible; \}/);
  // the affordance is visibly CHROME, not message text: dim, parenthesized, at the line's end
  assert.match(STYLES, /\.staged-expand \{ flex: 0 0 auto; color: var\(--dim\); font-size: 0\.85em; \}/);
  assert.match(RENDER, /hint\.textContent = open \? "\(collapse\)" : "\(click to expand\)";/);   // the tail names the gesture (the user 2026-08-16)
  // expansion survives the strip re-render (keyed set), and the discard ✕ does not toggle the fold
  assert.match(RENDER, /const stagedOpen = new Set<string>\(\);/);
  assert.match(RENDER, /x\.addEventListener\("click", \(ev\) => \{ ev\.stopPropagation\(\);/);
  // each staged reply carries its CONTEXT inside the dotted box: one chip per quote, independently
  // expandable (its click never toggles the reply's own fold), keyed sid:i:j
  assert.match(RENDER, /const quotes = \(s\.cites as Citation\[\]\)\.filter\(\(c\) => c && c\.quote\);/);
  assert.match(RENDER, /const ck = id \+ ":" \+ i \+ ":" \+ j;/);
  // the context keeps the composer's blue citation-pill look inside the staged box
  assert.match(RENDER, /el\("div", "composer-chip staged-cite"/);
  assert.match(STYLES, /\.staged-cite \{ min-width: 0; max-width: 100%; cursor: pointer; \}/);
  assert.match(RENDER, /cite\.addEventListener\("click", \(ev\) => \{\s*\n\s*ev\.stopPropagation\(\);/);
  assert.match(STYLES, /\.staged-cite\.open \.composer-chip-label \{ white-space: pre-wrap; overflow: visible; \}/);
});

