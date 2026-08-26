// CARD KEYBOARD SCOPE (the user 2026-08-24): Tab, pressed while a card is hovered (a click puts the
// pointer there too) or held by the keyboard card cursor, cycles ALL that card's visible controls,
// wrapping; Enter/Space activate; the accent .kbd-focus ring marks the stop; Escape or hover-away
// releases to normal page order. Focus survives the feed's 0.5s re-renders by LOGICAL control
// identity, and a focused card holds the hover-freeze payload gate. feed.ts has no jsdom harness →
// source pins (the repo convention); the gate composition is pinned in feed-freeze.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("scope entry: the hovered card (clicks hover too), else the keyboard card cursor", () => {
  assert.match(FEED, /if \(!card\) card = \(freezeKey \? cardElByKey\(freezeKey\) : null\) \|\| kbCardEl;/,
    "hover truth (freezeKey rides the card's own mouseenter) first, the kb cursor as the keyboard-only path");
  assert.match(FEED, /function cardElByKey\(key: string\): HTMLElement \| null/,
    "keys resolve through the same data-key vocabulary the reconcile writes");
  // typing in an input OUTSIDE the card keeps its normal Tab
  assert.match(FEED, /if \(ae && \/\^\(INPUT\|TEXTAREA\|SELECT\)\$\/\.test\(ae\.tagName\) && !card\.contains\(ae\)\) return;/);
});

test("the cycle covers every visible control in the card's own (visual) order, and WRAPS", () => {
  assert.match(FEED, /return Array\.from\(card\.querySelectorAll<HTMLElement>\(KB_EL_SEL\)\)\.filter\(\(e\) => e\.offsetParent !== null\);/,
    "one selector, DOM order — the card's reading order — visible controls only");
  // Shift+Tab is the SAME cycle reversed (the user 2026-08-24, verified + pinned): shiftKey picks the
  // decrement arm, both arms wrap, and the reverse path shares release/flush/restore with forward
  assert.match(FEED, /i = e\.shiftKey \? \(i <= 0 \? els\.length - 1 : i - 1\) : \(i >= els\.length - 1 \? 0 : i \+ 1\);/,
    "both directions wrap at the ends");
  assert.match(FEED, /\.fask-secbtn,\.fask-bellbtn/, "the section pills and the bell are in the set");
});

test("view pills SELECT on focus; action buttons stay inert until Enter/Space (the user 2026-08-24)", () => {
  // radio-group semantics for the SELECTORS only — .fask-secbtn switches what the card shows; every
  // other control is an ACTION (the safe default) and must never fire from mere focus
  assert.match(FEED, /if \(landed && landed\.matches\("\.fask-secbtn"\) && !landed\.classList\.contains\("on"\)\) landed\.click\(\);/);
  // exactly ONE application per user gesture: the click lives in the Tab branch, never in
  // tabScopeFocus — the render-tail restore calls tabScopeFocus, and a click there would re-apply
  // per re-render (select-on-focus itself triggers a render whose restore re-focuses the pill)
  const tsf = FEED.slice(FEED.indexOf("function tabScopeFocus"), FEED.indexOf("function releaseTabScope"));
  assert.ok(!tsf.includes(".click()"), "tabScopeFocus never clicks — the restore path rides it");
  const restore = FEED.slice(FEED.indexOf("// keyboard-scope focus restore"), FEED.indexOf("function feedWantsKeys"));
  assert.ok(!restore.includes(".click()"), "the restore path never clicks either");
  // the already-selected pill is a no-op on focus: pick() toggles a showing section OFF on click,
  // and cycling through the active pill must not flip the card's view closed
  assert.match(FEED, /!landed\.classList\.contains\("on"\)/);
});

test("Enter/Space activate — native controls natively, span controls by an exact synthetic click", () => {
  assert.match(FEED, /if \(ae\.matches\("button, a, input"\)\) return;   \/\/ native activation already fires the click/);
  assert.match(FEED, /ae\.click\(\);\s*[^\n]*\/\/ EXACTLY a mouse click on that control/);
  // non-native controls become focusable without joining the page's own Tab order
  assert.match(FEED, /if \(el2\.tabIndex < 0 && !el2\.matches\("button, a, input"\)\) el2\.tabIndex = -1;/);
  // the accent ring rides the shared .kbd-focus class (feed.css owns the style)
  assert.match(FEED, /el2\.classList\.add\("kbd-focus"\);/);
});

test("focus survives a re-render by LOGICAL identity — a rebuild must not eat keyboard focus", () => {
  const tail = FEED.slice(FEED.indexOf("// keyboard-scope focus restore"), FEED.indexOf("function feedWantsKeys"));
  assert.ok(tail.includes("let i = els.findIndex((e2) => ctrlSig(e2) === tabScopeSig!.sig);"),
    "identity first: class + label");
  assert.ok(tail.includes("if (i < 0) i = Math.min(tabScopeSig.idx, els.length - 1);"),
    "the old slot as the fallback when the control's identity changed");
  assert.ok(tail.includes("else releaseTabScope();"), "a card gone entirely releases cleanly");
  // a card detached WITH the ring (filtered out mid-scope) may be reattached by the reconcile later;
  // with no scope active any in-card ring is stale — the render tail strips it (kbMode owns its own)
  assert.match(FEED, /if \(!tabScopeKey && !kbMode\) document\.querySelectorAll\("\.fitem \.kbd-focus"\)/);
  assert.match(FEED, /function ctrlSig\(e: HTMLElement\): string \{\s*\n\s*return e\.className \+ "\|" \+ \(e\.getAttribute\("aria-label"\) \|\| e\.textContent \|\| ""\)\.trim\(\)\.slice\(0, 24\);/);
});

test("release: Escape anywhere, or the pointer leaving the card — back to normal page order", () => {
  assert.match(FEED, /if \(e\.key === "Escape" && tabScopeKey\) \{/);
  assert.match(FEED, /if \(tabScopeKey === key\) releaseTabScope\(\);   \/\/ hover-away releases the keyboard scope too/,
    "rides the same leave event the freeze uses");
  const relStart = FEED.indexOf("function releaseTabScope");
  const rel = FEED.slice(relStart, FEED.indexOf('window.addEventListener("keydown"', relStart));
  assert.ok(rel.includes('document.querySelectorAll(".kbd-focus").forEach((n) => n.classList.remove("kbd-focus"));'),
    "the ring comes off");
  assert.ok(rel.includes("if (!freezeKey) flushFreeze();"),
    "the scope held the payload gate — releasing applies whatever queued");
  // window blur is the scope's backstop too — otherwise a blurred pane with an armed scope would
  // queue payloads forever (the scope has no pointer to leave with)
  assert.match(FEED, /window\.addEventListener\("blur", \(\) => \{ releaseTabScope\(\); freezeKey = null; flushFreeze\(\); \}\);/);
});

test("the modal owns keys while open; the scoped handlers stop propagation so nothing double-fires", () => {
  const kd = FEED.slice(FEED.indexOf("// ── CARD KEYBOARD SCOPE"), FEED.indexOf("// The feed payload's full application"));
  assert.ok(kd.includes('if (document.getElementById("feed-modal")) return;'));
  assert.ok((kd.match(/e\.stopPropagation\(\);/g) || []).length >= 2, "Escape and Tab both stop propagation");
  assert.ok(kd.includes("}, true);"), "capture phase — ahead of the page's own key handling");
});
