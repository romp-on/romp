#!/usr/bin/env python3
"""T386 stage 1 (the user 2026-09-12): a card click deep into history did not land the first time; a second click
was needed. The landing audit (the kernel serving the session files one row per landing attempt) showed the shape
three times: the click asks for a window around the anchor (ok false, pointer-fetch-window, the anchor's time on the
row), the window lands two seconds later and files ok true pointer-exact with the time NULL, and a second click
seconds later lands the same anchor for real. pointer-exact asserted only that the anchor's turn was found and one
scroll write issued (landOn re-aligned for 1.2 s on the tab bar and the ledger box resizing, never on the transcript's
own moves), so the row could call a landing good that the reader never saw. This lab's first run on upstream/main
named the mover in the page's own scroll-write ledger: the window's REPLACE of the run shrank the transcript and the
follow-mode snap (tail-shrink) wrote the reader back to the bottom, because the landing had left follow mode on.

The served lab drives the real /chat page over a transcript longer than the wire tail (the T366 lab's boot) and
NAVIGATES into history the page does not hold, as a card's focus frame does, with the anchor and its time. It reads
what the page itself records: the landing row (locateDiag), the target's offset from the viewport top at the row and
over the next 1.5 s, and the scroll-write ledger (every programmatic write of #content names its writer, T262j), and
lets a LIVE turn land in the tail while the reader is on the landed message. A second road (the manager's datum of the
same day) clicks a card anchored on a turn's FIRST atom, a tool call inside a collapsed group of four, quoting words
that sit atoms later: the landing must align on the words, not the group. Three claims over one drive:

  1. the landing row keeps the click's time (red before the fix: the window's adoption reset it) and says whether the
     landing SETTLED, with the target's distance from the viewport top at settle time (red before: no such fields);
  2. the view holds: the target stays within its own row of the viewport top through the settle window and through
     the live turn's arrival, and no write but the landing's own follows it (red before: tail-shrink);
  3. the card anchored on a tool call lands the reader on the quoted words (red before: on the tool group).

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none). All fixtures synthetic.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_live_paused_window_browser import DRIVER_HEAD, WindowLab, SID, TURNS   # noqa: E402  the shared boot and the page's state probe

LIVE_U = "33333333-4444-5555-6666-000000000001"   # the live turn appended mid-landing: synthetic uuids
LIVE_A = "33333333-4444-5555-6666-000000000002"

DRIVER = DRIVER_HEAD + r"""
const deep = cfg.deepUuid;
const ledger = () => page.evaluate(() => window.__sent.filter((m) => m.type === "clientDiag" && m.what === "scrollwrite")
  .map((m) => ({ writer: m.data.writer, before: m.data.before, after: m.data.after })));
const rows = () => page.evaluate(() => window.__sent.filter((m) => m.type === "locateDiag")
  .map((m) => ({ ok: m.ok, trail: m.trail, anchor: m.anchor || null, anchorT: m.anchorT === undefined ? null : m.anchorT, dist: m.dist === undefined ? null : m.dist,
                 settled: m.settled === undefined ? null : m.settled, superseded: m.superseded === undefined ? null : m.superseded, clamp: m.clamp === undefined ? null : m.clamp, gesture: m.gesture === undefined ? null : m.gesture })));
// an element's box against the viewport top, and its own height (the row it must stay within)
const boxOf = (sel) => page.evaluate((s) => {
  const c = document.getElementById("content"); const t = document.querySelector(s);
  if (!t) return null; const r = t.getBoundingClientRect(); return { top: Math.round(r.top - c.getBoundingClientRect().top), h: Math.round(r.height) };
}, sel);
const offset = () => boxOf('#content .turn[data-uuid="' + deep + '"]');
const writesBefore = (await ledger()).length;
const q = (k) => "11111111-2222-3333-4444-" + pad(2 * k);   // the k-th question's uuid (the boot's formula; `pad` is the driver head's)
// ROAD 6 (round one, medium 3; run first, see below): a landing within a viewport of the tail: the scroll clamp stops the target short of the top,
// and the row says so (settled, with the clamp) instead of a miss
const rowsBefore6 = (await rows()).length;
const tailQ = q(cfg.turns - 1);
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: tailQ, anchorT: cfg.base + 2 * (cfg.turns - 1) });
// run FIRST, while the page is still attached to the tail run it booted with: a landing on a turn the page holds is exact;
// after a window road the page is detached and the same focus asks the kernel for a window instead. The exact landing's
// row goes out when its settle ends, so wait for THAT row, bounded, not a fixed pause
try { await page.waitForFunction((u) => window.__sent.some((m) => m.type === "locateDiag" && m.anchor === u && Array.isArray(m.trail) && m.trail[m.trail.length - 1] === "pointer-exact"), tailQ, { timeout: 15000 }); }
catch (e) { /* rows6 says what happened */ }
await page.waitForTimeout(200);
const tail6 = await boxOf('#content .turn[data-uuid="' + tailQ + '"]');
const rows6 = (await rows()).slice(rowsBefore6);
const scroll6 = await page.evaluate(() => { const c = document.getElementById("content"); return { top: c.scrollTop, max: c.scrollHeight - c.clientHeight }; });
const rowsBefore1 = (await rows()).length;   // road 6 ran first: its rows are not road 1's
// ROAD 1: the reader NAVIGATES into history the page does not hold: a card's focus frame, the anchor and its time
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: deep, anchorT: cfg.deepT });
try { await page.waitForFunction((u) => !!document.querySelector('#content .turn[data-uuid="' + u + '"]'), deep, { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the deep link never landed: " + JSON.stringify(st)); process.exit(1); }
const o0 = await offset();
await page.waitForTimeout(300); const o300 = await offset();
await page.waitForTimeout(400); const o700 = await offset();
const rowsAtLand = (await rows()).slice(rowsBefore1);
// a LIVE turn lands in the tail while the reader is on the landed message: the transcript grows, the event the pusher
// wakes on, exactly as a live session's does under a reader deep in its history
const now = new Date();
fs.appendFileSync(cfg.transcript,
  JSON.stringify({ type: "user", uuid: cfg.liveU, parentUuid: null, timestamp: now.toISOString(), sessionId: cfg.sid,
                   message: { role: "user", content: "and one more question, live, about the notes api" } }) + "\n" +
  JSON.stringify({ type: "assistant", uuid: cfg.liveA, parentUuid: cfg.liveU, timestamp: new Date(now.getTime() + 1000).toISOString(), sessionId: cfg.sid,
                   message: { role: "assistant", model: "claude-fable-5-1", stop_reason: "end_turn", content: [{ type: "text", text: "Live answer: the handler reads the note by id and returns it." }] } }) + "\n");
// the live turn reaches the page only if the kernel sends this client a tail; either way the reader's view is measured
let liveArrived = true;
try { await page.waitForFunction((u) => !!document.querySelector('#content .turn[data-uuid="' + u + '"]'), cfg.liveA, { timeout: 8000 }); }
catch (e) { liveArrived = false; }
await page.waitForTimeout(500);
const oLive = await offset();
await page.waitForTimeout(800);
const oLate = await offset();
const rowsAll = (await rows()).slice(rowsBefore1);
const writes = (await ledger()).slice(writesBefore);
// ROAD 2 (the manager's datum, T386): a card anchored on a turn's FIRST atom, a tool call inside a collapsed group of four,
// quoting words that sit atoms later: the landing aligns on the quoted words, not on the tool group
const rowsBefore2 = (await rows()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: cfg.toolUuid, anchorT: cfg.toolT, anchorQuote: cfg.toolQuote });
try { await page.waitForFunction((q) => Array.from(document.querySelectorAll("#content .turn-assistant .assistant.md p")).some((e) => (e.textContent || "").includes(q)), cfg.toolQuote, { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the tool-turn card never landed: " + JSON.stringify(st)); process.exit(1); }
await page.waitForTimeout(1500);
const quoted = await page.evaluate((q) => {
  const c = document.getElementById("content");
  const e = Array.from(document.querySelectorAll("#content .turn-assistant .assistant.md p")).find((x) => (x.textContent || "").includes(q));
  if (!e) return null; const r = e.getBoundingClientRect(); return { top: Math.round(r.top - c.getBoundingClientRect().top), h: Math.round(r.height) };
}, cfg.toolQuote);
const anchorBox = await boxOf('#content .turn[data-uuid="' + cfg.toolUuid + '"]');
const rows2 = (await rows()).slice(rowsBefore2);
// ROAD 3 (round one, medium 5): the same tool-anchored card with NO quote: the landing aligns on the turn's first text atom
// below the group head, never on the group
const rowsBefore3 = (await rows()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(10), anchorT: cfg.base + 20 });   // step away first (a resident turn)
await page.waitForTimeout(1500);
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: cfg.toolUuid, anchorT: cfg.toolT });
await page.waitForTimeout(1500);
const words3 = await page.evaluate((qt) => {
  const c = document.getElementById("content");
  const e = Array.from(document.querySelectorAll("#content .turn-assistant .assistant.md")).find((x) => (x.textContent || "").includes(qt));
  if (!e) return null; const r = e.getBoundingClientRect(); return { top: Math.round(r.top - c.getBoundingClientRect().top), h: Math.round(r.height) };
}, cfg.toolQuote);
const anchor3 = await boxOf('#content .turn[data-uuid="' + cfg.toolUuid + '"]');
const anchor3cls = await page.evaluate((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); return t ? t.className : null; }, cfg.toolUuid);
const rows3 = (await rows()).slice(rowsBefore3);
// ROAD 4 (round one, medium 1): the reader takes over during the settle by a move the scroll classifier reads as a gesture
// (a scrollbar drag or a touch swipe write no wheel event): the landing yields, the view stays where the reader put it
const rowsBefore4 = (await rows()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(30), anchorT: cfg.base + 60 });
try { await page.waitForFunction((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return false; const c = document.getElementById("content"); return Math.abs(t.getBoundingClientRect().top - c.getBoundingClientRect().top) < 40; }, q(30), { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the gesture road's landing never arrived: " + JSON.stringify(st)); process.exit(1); }
// the reader's own wheel, right after the landing arrives, INSIDE the settle window (round two, low 3: a 300 ms pause used to
// fall past the early settle at the base, so the road was green there); a real input event, as the settle now requires
// (a wheel event on the scroller, then the scroll it causes: the pair the page sees from a real wheel; the headless mouse's own
// wheel reached no scroller here)
const rowsBeforeWheel = (await rows()).slice(rowsBefore4);   // the settle is still open: its row is not filed yet (the window's end is ~1.2 s away)
const wheelAt = Date.now();
const moved4 = await page.evaluate(() => { const c = document.getElementById("content");
  c.dispatchEvent(new WheelEvent("wheel", { deltaY: 900, bubbles: true, cancelable: true })); c.scrollTop = c.scrollTop + 900; return c.scrollTop; });
// the takeover files the row AT the gesture, long before the window's end: how soon the exact row appears after the wheel
let rowAfterWheelMs = null;
try { await page.waitForFunction((u) => window.__sent.some((m) => m.type === "locateDiag" && m.anchor === u && Array.isArray(m.trail) && m.trail[m.trail.length - 1] === "pointer-exact"), q(30), { timeout: 1500 }); rowAfterWheelMs = Date.now() - wheelAt; }
catch (e) { /* rows4 says what happened */ }
await page.waitForTimeout(250);
await page.waitForTimeout(1400);
const after4 = await page.evaluate(() => document.getElementById("content").scrollTop);
const writes4 = (await ledger()).filter((w) => w.before === moved4 || w.after === moved4 || (Math.abs(w.before - moved4) < 4));
const rows4 = (await rows()).slice(rowsBefore4);
// ROAD 5 (round one, medium 2): two clicks 120 ms apart file TWO rows, the first superseded
const rowsBefore5 = (await rows()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(50), anchorT: cfg.base + 100 });
await page.waitForTimeout(120);
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(52), anchorT: cfg.base + 104 });
await page.waitForTimeout(2500);
const rows5 = (await rows()).slice(rowsBefore5);
// ROAD 10 (round four, medium): the reader's arrow keys. The key handler scrolls the pane through the write helper (writer key-nav),
// so the scroll is a write echo the classifier never calls a gesture; the write rule read it as another mover's and land-realign
// wrote three steps back. A reader-driven writer is the reader's takeover.
const rowsBefore10 = (await rows()).length; const ledgerBefore10 = (await ledger()).length;   // the road's own slice (round five, low)
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(36), anchorT: cfg.base + 72 });
try { await page.waitForFunction((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return false; const c = document.getElementById("content"); return Math.abs(t.getBoundingClientRect().top - c.getBoundingClientRect().top) < 40; }, q(36), { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the key road's landing never arrived: " + JSON.stringify(st)); process.exit(1); }
const landed10 = await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); return document.getElementById("content").scrollTop; });   // the keys go to the page, not the composer
for (let i = 0; i < 3; i++) { await page.keyboard.press("ArrowDown"); await page.waitForTimeout(100); }
await page.waitForTimeout(1400);
const after10 = await page.evaluate(() => document.getElementById("content").scrollTop);
const writes10 = (await ledger()).slice(ledgerBefore10).filter((wr) => wr.writer === "land-realign");
const keyWrites10 = (await ledger()).slice(ledgerBefore10).filter((wr) => wr.writer === "key-nav").length;
const rows10 = (await rows()).slice(rowsBefore10);
// ROAD 11 (round five, medium): the history chord. Ctrl+M goes back in the chat's own navigation history through the write helper
// (writer nav-history); the census makes it the reader's takeover
const rowsBefore11 = (await rows()).length; const ledgerBefore11 = (await ledger()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(38), anchorT: cfg.base + 76 });
try { await page.waitForFunction((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return false; const c = document.getElementById("content"); return Math.abs(t.getBoundingClientRect().top - c.getBoundingClientRect().top) < 40; }, q(38), { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the chord road's landing never arrived: " + JSON.stringify(st)); process.exit(1); }
const landed11 = await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); return document.getElementById("content").scrollTop; });
await page.keyboard.press("Control+m");
await page.waitForTimeout(1400);
const after11 = await page.evaluate(() => document.getElementById("content").scrollTop);
const navWrites11 = (await ledger()).slice(ledgerBefore11).filter((wr) => wr.writer === "nav-history");
const nav11 = navWrites11.length; const navTo11 = nav11 ? navWrites11[nav11 - 1].after : null;   // where the chord sent the view
const writes11 = (await ledger()).slice(ledgerBefore11).filter((wr) => wr.writer === "land-realign");
const rows11 = (await rows()).slice(rowsBefore11);
// ROAD 12 (round five, medium): a fragment link inside a message. A real click on a link planted in the landed turn's body, pointing
// at a later turn's body by id, scrolls the pane through the write helper (writer section-link): the reader's takeover
const rowsBefore12 = (await rows()).length; const ledgerBefore12 = (await ledger()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(42), anchorT: cfg.base + 84 });
try { await page.waitForFunction((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return false; const c = document.getElementById("content"); return Math.abs(t.getBoundingClientRect().top - c.getBoundingClientRect().top) < 40; }, q(42), { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the link road's landing never arrived: " + JSON.stringify(st)); process.exit(1); }
const planted12 = await page.evaluate(([from, to]) => { const src = document.querySelector('#content .turn[data-uuid="' + from + '"] .md'); const dst = document.querySelector('#content .turn[data-uuid="' + to + '"] .md');
  if (!src || !dst) return null; dst.id = "lab-frag"; const a = document.createElement("a"); a.href = "#lab-frag"; a.className = "lab-frag"; a.textContent = "further down"; src.appendChild(a);
  return { landed: document.getElementById("content").scrollTop, srcOk: true }; }, [q(42), q(44)]);
if (!planted12) { console.error("the link road found no message bodies to plant in"); process.exit(1); }
await page.click(".lab-frag");
await page.waitForTimeout(1400);
const after12 = await page.evaluate(() => document.getElementById("content").scrollTop);
const link12 = (await ledger()).slice(ledgerBefore12).filter((wr) => wr.writer === "section-link").length;
const writes12 = (await ledger()).slice(ledgerBefore12).filter((wr) => wr.writer === "land-realign");
const rows12 = (await rows()).slice(rowsBefore12);
// ROAD 8 (round two, medium): the browser's own scroll anchoring during the settle. A 400 px node inserted ABOVE the viewport
// in the landing's frame moves scrollTop with no write and no input; the classifier calls that a gesture, and the settle used to
// end on it and file the landing settled false, dist null. With the reader's input as the evidence a gesture needs, it is a
// sample: the landing stays exact and its row says settled. Last, since the node stays in the DOM.
// The target sits INSIDE the run the earlier roads left resident and away from its edges: a landing at the run's end asks for the
// newer side and a gap reply replaces the run with the tail; one outside the run asks the kernel for a window, and neither is this
// road's subject (the windowing stage 2 reworks both). The page re-renders the resident run around the target on its own.
const rowsBefore8 = (await rows()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(40), anchorT: cfg.base + 80 });
try { await page.waitForFunction((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return false; const c = document.getElementById("content"); return Math.abs(t.getBoundingClientRect().top - c.getBoundingClientRect().top) < 40; }, q(40), { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the anchoring road's landing never arrived: " + JSON.stringify(st)); process.exit(1); }
const shift8 = await page.evaluate(() => { const c = document.getElementById("content"); const before = c.scrollTop;
  const d = document.createElement("div"); d.className = "lab-anchoring-probe"; d.style.height = "400px"; c.insertBefore(d, c.firstElementChild); return { before, after: c.scrollTop }; });
try { await page.waitForFunction((u, n) => window.__sent.filter((m) => m.type === "locateDiag" && m.anchor === u && Array.isArray(m.trail) && m.trail[m.trail.length - 1] === "pointer-exact").length > 0, q(40), { timeout: 6000 }); }
catch (e) { /* rows8 says what happened */ }
await page.waitForTimeout(200);
const rows8 = (await rows()).slice(rowsBefore8);
const box8 = await boxOf('#content .turn[data-uuid="' + q(40) + '"]');
// ROAD 9 (round three, medium; LAST, since a drag may run the view toward the run's end): a scrollbar THUMB drag. The reader presses on the scroller itself (the gutter is the scroller's own
// box), pauses past the timed window, then drags: the browser fires scroll events with NO pointer moves at all until the release.
// The timed evidence alone read those scrolls as samples and land-realign wrote the reader back; the hold on the scroller is the
// evidence now, whatever the clock says.
const rowsBefore9 = (await rows()).length;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: q(45), anchorT: cfg.base + 90 });
try { await page.waitForFunction((u) => { const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return false; const c = document.getElementById("content"); return Math.abs(t.getBoundingClientRect().top - c.getBoundingClientRect().top) < 40; }, q(45), { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the drag road's landing never arrived: " + JSON.stringify(st)); process.exit(1); }
const landed9 = await page.evaluate(() => document.getElementById("content").scrollTop);
// a REAL press on the scrollbar thumb (round four, low 2: a dispatched pointerdown proved the wiring, not Chromium's delivery). The
// notch pads over the track are made passive for this road: where notches are dense the thumb is grabbable only in the gaps
// (styles.css records that trade-off, kept small on purpose), and the road is about the grab, not the notches
await page.addStyleTag({ content: ".scroll-marks .scroll-mark { pointer-events: none !important; }" });
const grab9 = await page.evaluate(() => { const c = document.getElementById("content"); const r = c.getBoundingClientRect(); const gutter = c.offsetWidth - c.clientWidth;
  const frac = c.scrollTop / c.scrollHeight, thumb = (c.clientHeight / c.scrollHeight) * c.clientHeight;
  return { gutter, x: r.right - gutter / 2, y: r.top + frac * c.clientHeight + thumb / 2 }; });
if (!(grab9.gutter > 0)) { console.error("no scrollbar gutter: the drag road needs classic scrollbars (cfg.launch): " + JSON.stringify(grab9)); process.exit(1); }
await page.mouse.move(grab9.x, grab9.y); await page.mouse.down();
await page.waitForTimeout(300);   // the pause past SETTLE_INPUT_MS before the first movement
await page.mouse.move(grab9.x, grab9.y + 10, { steps: 3 }); await page.waitForTimeout(120);   // a small thumb move: a long drag ran the view off the resident run's end (a re-attach, stage 2's subject)
const moved9 = await page.evaluate(() => document.getElementById("content").scrollTop);
await page.mouse.move(grab9.x, grab9.y + 20, { steps: 3 }); await page.waitForTimeout(200);
const moved9b = await page.evaluate(() => document.getElementById("content").scrollTop);
await page.mouse.up();
await page.waitForTimeout(1400);
const after9 = await page.evaluate(() => document.getElementById("content").scrollTop);
const writes9 = (await ledger()).filter((w) => w.writer === "land-realign" && (Math.abs(w.before - moved9) < 4 || Math.abs(w.before - moved9b) < 4));
const rows9 = (await rows()).slice(rowsBefore9);
// the anchor's place in the DOM: its ancestors up to #content and the siblings that follow it (the turn's atoms as rendered),
// and whether the page can highlight at all; the diagnosis when the words are not at the top
const dom2 = await page.evaluate((u) => {
  const t = document.querySelector('#content .turn[data-uuid="' + u + '"]'); if (!t) return null;
  const chain = []; for (let n = t.parentElement; n && n.id !== "content"; n = n.parentElement) chain.push(n.className || n.tagName.toLowerCase());
  const sibs = []; for (let n = t.nextElementSibling, i = 0; n && i < 7; n = n.nextElementSibling, i++) sibs.push((n.className || n.tagName.toLowerCase()) + " | " + (n.textContent || "").trim().slice(0, 40));
  const c = document.getElementById("content"); const cr = c.getBoundingClientRect();
  const atTop = document.elementFromPoint(cr.left + cr.width / 2, cr.top + 4);
  return { chain, sibs, highlights: !!(CSS && CSS.highlights) && typeof Highlight !== "undefined", atTop: atTop ? (atTop.className || atTop.tagName.toLowerCase()) + " | " + (atTop.textContent || "").trim().slice(0, 40) : null,
           writesAfter: window.__sent.filter((m) => m.type === "clientDiag" && m.what === "scrollwrite").slice(-6).map((m) => m.data.writer + ":" + m.data.before + ">" + m.data.after) };
}, cfg.toolUuid);
const st = await state();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-settled.png" });
await browser.close();
process.stdout.write("RESULT:" + JSON.stringify({ o0, o300, o700, oLive, oLate, liveArrived, rowsAtLand, rowsAll, writes, quoted, anchorBox, rows2, dom2,
  words3, anchor3, anchor3cls, rows3, rowsBeforeWheel, rowAfterWheelMs, moved4, after4, writes4, rows4, rows5, landed9, moved9, moved9b, after9, writes9, rows9, grab9, landed10, after10, writes10, keyWrites10, rows10, landed11, after11, nav11, navTo11, writes11, rows11, planted12, after12, link12, writes12, rows12, tail6, rows6, scroll6, rows8, shift8, box8, after: st }) + "\n", () => process.exit(0));
"""


class ServedLandingSettles(WindowLab):
    _r = None

    def _result(self):
        """One drive per class: the tests read the same landings (a second drive would land on a page that holds the
        windows already, a different road)."""
        cls = type(self)
        if cls._r is None:
            cls._r = self._drive(DRIVER, "settles", extra={"liveU": LIVE_U, "liveA": LIVE_A, "turns": TURNS,
                                                    # classic scrollbars, for the drag road's REAL press on the thumb (Playwright hides them headless by default)
                                                    "launch": {"ignoreDefaultArgs": ["--hide-scrollbars"]}})
            print("RESULT:" + json.dumps(cls._r), file=sys.stderr)   # the whole measurement rides a failure's captured stderr
        return cls._r

    def test_the_landing_row_keeps_the_clicks_time_and_says_whether_the_landing_settled(self):
        r = self._result()
        # the exact row is FILED when the settle ends, at the window's end (round two, low 1): none within the landing's first
        # moments, one by the time the road's later probes ran
        early = [x for x in r["rowsAtLand"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(early, [], "no exact row before the settle window ran out: %r" % r["rowsAtLand"])
        land = [x for x in r["rowsAll"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(land), 1, "one exact landing row for the navigation: %r" % r["rowsAll"])
        row = land[0]
        self.assertEqual(row["anchorT"], self.deep_t, "the row keeps the click's time through the window's adoption: %r" % row)
        self.assertIsNotNone(row["settled"], "the row says whether the landing settled: %r" % row)
        self.assertIsNotNone(row["dist"], "…and the target's distance from the viewport top at settle time: %r" % row)
        self.assertTrue(row["settled"], "the landing settled: %r" % row)
        self.assertLessEqual(abs(row["dist"]), max(8, r["oLate"]["h"] if r["oLate"] else 8), "the recorded distance is within the target's own row: %r" % row)

    def test_the_view_holds_on_the_landed_message_through_the_settle_window_and_a_live_turn(self):
        r = self._result()
        for k in ("o0", "o300", "o700", "oLive", "oLate"):
            self.assertIsNotNone(r[k], "the target stayed resident at %s: %r" % (k, r["after"]))
        h = r["o0"]["h"]
        moved = ", ".join("%s: %s → %s" % (w["writer"], w["before"], w["after"]) for w in r["writes"]) or "no write after the landing"
        for k in ("o300", "o700", "oLive", "oLate"):
            self.assertLessEqual(abs(r[k]["top"]), max(8, h), "the target left the viewport top by %s (%r); the writes after the landing: %s; live turn arrived: %s"
                                 % (k, r[k], moved, r["liveArrived"]))
        # the writes that followed the landing are the landing's own (its re-aligns), never a rebuild's placement
        strangers = [w["writer"] for w in r["writes"] if w["writer"] not in ("land-on", "land-realign")]
        self.assertEqual(strangers, [], "a write other than the landing's moved the view after it: %s" % moved)

    def test_a_card_anchored_on_a_tool_call_lands_on_the_words_it_quotes_not_on_the_tool_group(self):
        r = self._result()
        self.assertIsNotNone(r["quoted"], "the quoted words are rendered: %r" % r["after"])
        self.assertIsNotNone(r["anchorBox"], "the anchor's tool turn is rendered: %r" % r["after"])
        q, a = r["quoted"], r["anchorBox"]
        self.assertLessEqual(abs(q["top"]), max(8, q["h"]), "the quoted words sit at the viewport top, within their own row; the tool turn: %r, the words: %r" % (a, q))
        self.assertLess(a["top"], q["top"], "the tool group stands above the words, off the top: %r vs %r" % (a, q))
        land = [x for x in r["rows2"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(land), 1, "one exact landing row for the card: %r" % r["rows2"])
        self.assertTrue(land[0]["settled"], "…settled on the words: %r" % land[0])

    def test_the_same_card_without_a_quote_lands_on_the_turns_first_text_atom_not_on_the_tool_group(self):
        # round one, medium 5: a grouped run of tool calls renders as ONE group head carrying the first tool's uuid, and the
        # no-quote fallback must walk from that head to the words
        r = self._result()
        self.assertIsNotNone(r["words3"], "the words are rendered: %r" % r["after"])
        self.assertIn("turn-toolgroup", r["anchor3cls"] or "", "the anchor resolves to the tool group's head: %r" % r["anchor3cls"])
        w, a = r["words3"], r["anchor3"]
        self.assertLessEqual(abs(w["top"]), max(8, w["h"]), "the words sit at the viewport top without a quote; the group: %r, the words: %r" % (a, w))
        self.assertLess(a["top"], w["top"], "the group stands above the words")

    def test_a_reader_taking_over_during_the_settle_by_any_gesture_keeps_the_view_and_two_quick_clicks_file_two_rows(self):
        r = self._result()
        # medium 1: the move the classifier reads as a gesture (no wheel event) is respected: no re-land undoes it
        self.assertLessEqual(abs(r["after4"] - r["moved4"]), 60, "the view stayed where the reader put it (moved to %s, now %s); writes near the move: %r" % (r["moved4"], r["after4"], r["writes4"]))
        realigns = [w for w in r["writes4"] if w["writer"] == "land-realign" and w["before"] == r["moved4"]]
        self.assertEqual(realigns, [], "no re-land wrote the reader's move back: %r" % r["writes4"])
        # the wheel came INSIDE the settle window (round two, low 3): no row stood before it, and the takeover filed the row at the
        # gesture, not at the window's end ~1 s later. The row carries the landing as it stood when the reader took over: the target
        # on its row (the reader's own move is not a miss), as the rule files a gave-up landing
        early = [x for x in r["rowsBeforeWheel"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(early, [], "the settle was still open when the reader wheeled: %r" % r["rowsBeforeWheel"])
        self.assertIsNotNone(r["rowAfterWheelMs"], "the wheel ended the settle: its row appeared: %r" % r["rows4"])
        self.assertLess(r["rowAfterWheelMs"], 600, "…at the gesture, not at the window's end: %s ms after the wheel" % r["rowAfterWheelMs"])
        taken = [x for x in r["rows4"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(taken), 1, "one exact row for the gesture road's landing: %r" % r["rows4"])
        self.assertTrue(taken[0]["settled"], "the target sat on its row when the reader took over: %r" % taken[0])
        self.assertTrue(taken[0].get("gesture"), "…and the row says the reader took it over (round three, low 3): %r" % taken[0])
        # medium 2: two landings inside one settle window are two rows, the first marked superseded
        exact = [x for x in r["rows5"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(exact), 2, "two exact rows for two clicks: %r" % r["rows5"])
        self.assertTrue(exact[0]["superseded"], "the first row carries the superseded mark: %r" % exact[0])
        self.assertFalse(exact[0]["settled"], "…and is not settled: %r" % exact[0])
        self.assertFalse(exact[1]["superseded"], "the second is the landing that stood: %r" % exact[1])

    def test_a_landing_within_a_viewport_of_the_tail_settles_against_the_spot_the_clamp_allows(self):
        # round one, medium 3
        r = self._result()
        exact = [x for x in r["rows6"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(exact), 1, "one exact row for the tail landing: %r" % r["rows6"])
        row = exact[0]
        self.assertGreaterEqual(r["scroll6"]["top"], r["scroll6"]["max"] - 2, "the view is at the bottom, where the clamp stops it: %r" % r["scroll6"])
        self.assertGreater(r["tail6"]["top"], 40, "the target sits below the viewport top, as it must near the tail: %r" % r["tail6"])
        self.assertTrue(row["settled"], "the landing is settled against the reachable spot: %r" % row)
        self.assertGreater(row["clamp"] or 0, 0, "…and the row names the clamp: %r" % row)
        self.assertLessEqual(abs(row["dist"] or 0), 24, "the distance from the reachable spot is within a row: %r" % row)

    def test_the_browsers_own_scroll_anchoring_during_the_settle_is_a_sample_not_a_takeover(self):
        # round two, medium: a node inserted above the viewport in the landing's frame moves scrollTop with no input
        r = self._result()
        self.assertGreaterEqual(r["shift8"]["after"] - r["shift8"]["before"], 300, "the insertion moved the scroller by the browser's anchoring: %r" % r["shift8"])
        exact = [x for x in r["rows8"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(exact), 1, "one exact row for the landing: %r" % r["rows8"])
        self.assertTrue(exact[0]["settled"], "the landing was and stayed exact: settled, not a takeover: %r" % exact[0])
        self.assertIsNotNone(exact[0]["dist"], "the settle measured the target: %r" % exact[0])
        self.assertLessEqual(abs(exact[0]["dist"]), 24, "the target on its row at the settle's end: %r" % exact[0])
        self.assertLessEqual(abs(r["box8"]["top"]), 40, "and on the screen the target still sits at the top: %r" % r["box8"])

    def test_a_scrollbar_thumb_drag_with_a_pause_before_the_first_movement_is_the_readers_and_the_view_holds(self):
        # round three, medium; round four, low 2: a REAL press on the thumb, a 300 ms pause, then the drag; the release
        r = self._result()
        self.assertGreater(r["grab9"]["gutter"], 0, "classic scrollbars for the real press: %r" % r["grab9"])
        self.assertGreater(r["moved9"], r["landed9"] + 100, "the drag moved the view: %s from %s" % (r["moved9"], r["landed9"]))
        self.assertLessEqual(abs(r["after9"] - r["moved9b"]), 60, "the view stayed where the drag left it (%s), now %s; re-lands near the drag: %r" % (r["moved9b"], r["after9"], r["writes9"]))
        self.assertEqual(r["writes9"], [], "no land-realign wrote the drag back: %r" % r["writes9"])
        taken = [x for x in r["rows9"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(taken), 1, "one exact row for the drag road's landing: %r" % r["rows9"])
        self.assertTrue(taken[0].get("gesture"), "the row says the reader took the landing over: %r" % taken[0])

    def test_the_readers_arrow_keys_during_the_settle_are_their_takeover_not_another_writers_move(self):
        # round four, medium: three ArrowDown presses inside the window; the view ends down, no land-realign, the row marked
        r = self._result()
        self.assertGreaterEqual(r["keyWrites10"], 3, "the keys wrote the pane (key-nav): %s writes" % r["keyWrites10"])
        self.assertGreaterEqual(r["after10"] - r["landed10"], 40, "the arrow keys moved the view down from the landing: %s from %s" % (r["after10"], r["landed10"]))
        self.assertEqual(r["writes10"], [], "no land-realign wrote the reader's steps back: %r" % r["writes10"])
        taken = [x for x in r["rows10"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(taken), 1, "one exact row for the key road's landing: %r" % r["rows10"])
        self.assertTrue(taken[0].get("gesture"), "the row says the reader took the landing over: %r" % taken[0])

    def test_the_history_chord_during_the_settle_is_the_readers_takeover(self):
        # round five, medium: Ctrl+M inside the window goes back in the chat's history through the write helper (nav-history)
        r = self._result()
        self.assertGreaterEqual(r["nav11"], 1, "the chord wrote the pane (nav-history): %s writes" % r["nav11"])
        # the history spot may sit near the landing (the road before landed close by): the proof is that the view ENDS where the chord's
        # own write put it and did not return to the landing, not how far that is
        self.assertNotEqual(r["after11"], r["landed11"], "the view left the landing: %s" % r["after11"])
        self.assertLessEqual(abs(r["after11"] - (r["navTo11"] if r["navTo11"] is not None else -1)), 2, "the view ends where the chord's write put it (%s), now %s" % (r["navTo11"], r["after11"]))
        self.assertEqual(r["writes11"], [], "no land-realign wrote the reader's history step back: %r" % r["writes11"])
        taken = [x for x in r["rows11"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(taken), 1, "one exact row for the chord road's landing: %r" % r["rows11"])
        self.assertTrue(taken[0].get("gesture"), "the row says the reader took the landing over: %r" % taken[0])

    def test_a_click_on_a_fragment_link_inside_a_message_during_the_settle_is_the_readers_takeover(self):
        # round five, medium: a real click on a link planted in the landed body, pointing at a later turn's body (section-link)
        r = self._result()
        self.assertIsNotNone(r["planted12"], "the link and its target were planted in two message bodies")
        self.assertGreaterEqual(r["link12"], 1, "the click wrote the pane (section-link): %s writes" % r["link12"])
        self.assertGreater(r["after12"] - r["planted12"]["landed"], 100, "the view ends at the link's target, below the landing: %s from %s" % (r["after12"], r["planted12"]["landed"]))
        self.assertEqual(r["writes12"], [], "no land-realign wrote the reader's link step back: %r" % r["writes12"])
        taken = [x for x in r["rows12"] if x["ok"] and x["trail"] and x["trail"][-1] == "pointer-exact"]
        self.assertEqual(len(taken), 1, "one exact row for the link road's landing: %r" % r["rows12"])
        self.assertTrue(taken[0].get("gesture"), "the row says the reader took the landing over: %r" % taken[0])


if __name__ == "__main__":
    unittest.main()
