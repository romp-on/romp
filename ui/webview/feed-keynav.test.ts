// Feed keyboard navigation (the user 2026-07-01), phase 2: when the shell hands the feed keyboard focus
// ({romp:'paneFocus'} via Alt+Arrow), plain Arrow keys move a cursor over cards, Enter drops INTO a card and
// steps its clickable elements, Enter activates one (a real click), Escape steps back out. Every highlight +
// action REUSES the mouse hover/click path so the two can't drift. Source pins (no jsdom for the feed renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the shell's paneFocus arms card nav; blur/Escape disarms it", () => {
  assert.match(FEED, /if \(m\.romp === "paneFocus"\) \{ kbEnterCards\(\); return; \}/);
  assert.match(FEED, /window\.addEventListener\("blur", \(\) => \{ if \(kbMode\) kbExit\(\); \}\)/);
  // focus stays in the feed while navigating (don't bounce it back to chat)
  assert.match(FEED, /function feedWantsKeys\(t: EventTarget \| null\): boolean \{\s*\n\s*if \(kbMode\) return true;/);
});

test("the card cursor reuses the mouse-hover highlight path (applyFocus + showAskPath)", () => {
  assert.match(FEED, /function kbSelectCard\(el: HTMLElement \| null\): void \{/);
  assert.match(FEED, /hoverAskId = id; applyFocus\(\);/, "the SAME white .focused ring the hover shows");
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "showAskPath", itemId: id, sid: sidOfItem\(id\), locate: false \}\);/, "same lit timeline journey");
});

test("Enter descends into a card; arrows step its elements; Enter activates via a real click", () => {
  // section pills + the bell joined the set with the Tab scope (the user 2026-08-24) — every
  // visible control on a card cycles
  assert.match(FEED, /const KB_EL_SEL = "\.fcard-title\.nav,\.fask-distill-link,\.fname,\.fask-apiRetry,\.fask-revive,\.fdismiss,\.fask-secbtn,\.fask-bellbtn,\.fcheck \.lz-nav,\.fask-delegation";/);
  assert.match(FEED, /function kbEnterCard\(\): void \{/);
  // element highlight dispatches the element's OWN hover event (zone .lz-hl + timeline), not a reimplementation
  assert.match(FEED, /el\.dispatchEvent\(new MouseEvent\("mouseenter"\)\);/);
  // Enter on an element is EXACTLY a mouse click on it
  assert.match(FEED, /else if \(k === "Enter"\) \{ e\.preventDefault\(\); kbEls\[kbElIdx\]\?\.click\(\); \}/);
  // Escape steps out one level (card -> cards), not all the way
  assert.match(FEED, /function kbExitCard\(\): void \{[\s\S]*?kbMode = "cards"; \}/);
});

test("Alt/Ctrl/Cmd + Arrow are left for the shell (pane move), only plain arrows drive card nav", () => {
  assert.match(FEED, /if \(e\.altKey \|\| e\.ctrlKey \|\| e\.metaKey\) return;/);
  // the modal owns keys while open
  assert.match(FEED, /if \(document\.getElementById\("feed-modal"\)\) return;/);
});

test("the element cursor has a visible accent ring", () => {
  assert.match(CSS, /\.kbd-focus \{ outline: 2px solid var\(--accent/);
  assert.match(FEED, /el\.classList\.add\("kbd-focus"\);/);
});
