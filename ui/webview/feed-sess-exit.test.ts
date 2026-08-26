// The grouped session header leaves as ONE MOTION with its run's last card (the user 2026-08-24,
// whose recording showed the header popping out a frame after the card finished fading): the exit
// wears the card-dismiss family, DOM removal keys on animationend with a can't-trap backstop, a run
// that still has cards keeps its header rock-steady, and reduced motion removes at once. Source
// pins (feed.ts has no jsdom harness — the repo convention); the motion itself verified headlessly
// with tools/ui-verify (steady / mid-exit / end frames).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("a header being removed exits ANIMATED; anything else still removes cold", () => {
  const sweep = FEED.slice(FEED.indexOf("for (const [k, c] of existing) {"), FEED.indexOf("let cur: ChildNode | null"));
  assert.ok(sweep.includes('if (c.classList.contains("sess-exit")) continue;'),
    "a ghost mid-exit is left to its own end event — never re-removed");
  assert.ok(sweep.includes('if (k.startsWith("s:")) startSessHeadExit(k, c);'),
    "headers take the animated exit");
  assert.ok(sweep.includes("else c.remove();"), "cards and groups keep their existing removal paths");
});

test("the exit un-keys FIRST, removes on the END EVENT, backstops, and honors reduced motion", () => {
  const ex = FEED.slice(FEED.indexOf("function startSessHeadExit"), FEED.indexOf("function dressHeaderIfLast"));
  assert.ok(ex.includes("if (sessHeadEls.get(key) === head) sessHeadEls.delete(key);"),
    "a reappearing run mints a FRESH header while the ghost finishes");
  assert.ok(ex.includes('head.dataset.key = "x:" + (++sessGhostSeq);'),
    "tombstone-keyed: survives the reconcile's unkeyed-child sweep, never desired again");
  assert.ok(ex.includes('head.addEventListener("animationend", done, { once: true });'),
    "removal keys on the event, not a timer");
  assert.ok(ex.includes("window.setTimeout(done, 600);"), "…with the standard can't-trap backstop");
  assert.ok(ex.includes('window.matchMedia("(prefers-reduced-motion: reduce)").matches'),
    "reduced motion removes at once — no animation ever plays, so no event to wait on");
});

test("the CONJUNCTION: the run's last card takes its header with it, at the same click", () => {
  assert.match(FEED, /dressHeaderIfLast\(card, it\.sid\);/, "ask-card clears join the motion");
  assert.match(FEED, /dressHeaderIfLast\(card, cur\.sid\);/, "group-card clears too — a group is one session's turn");
  const dh = FEED.slice(FEED.indexOf("function dressHeaderIfLast"), FEED.indexOf("function reconcileCol"));
  assert.ok(dh.includes("if (!feedPrefs().grouped) return;"), "grouped mode only — flat mode has no headers");
  assert.ok(dh.includes('if (!head || head.getAttribute("data-fsid") !== sid || head.classList.contains("sess-exit")) return;'),
    "the walk stops at the run's OWN header, once");
  // rock-steady: any other live member of the run vetoes the dress — a middle card leaving
  // never touches the header
  assert.ok(dh.includes('if (n !== card && n.classList.contains("fitem") && !n.classList.contains("dismissing")) return;'),
    "a run that lives on keeps its header untouched");
});

test("the dress is the card-dismiss family: fade + shrink + height collapse, fill held, reduced-motion block", () => {
  assert.match(CSS, /\.feed-sess-head\.sess-exit \{ animation: sess-exit 0\.18s ease forwards; overflow: hidden; pointer-events: none; \}/,
    "same 0.18s ease the card exit wears (fask-dismiss)");
  assert.match(CSS, /@keyframes sess-exit \{\s*\n\s*from \{ opacity: 1; transform: scale\(1\); max-height: 60px; \}\s*\n\s*to\s+\{ opacity: 0; transform: scale\(0\.9\); max-height: 0; margin: 0; padding: 0; \}/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.feed-sess-head\.sess-exit \{ animation: none; opacity: 0; \} \}/,
    "the loading-cues convention: no motion, the end state stated");
});
