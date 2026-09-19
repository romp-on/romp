#!/usr/bin/env python3
"""REORDER, the divider and the previews for both widget sections (the user's additions to T409, 2026-09-13), on the served
dashboard: a hermetic kernel serves the landing with two synthetic notes-api sessions (web and api, TESTHOST, the web
session's context at 62 percent so its context bar shows and a hot key assigned so its keycap shows, the transcripts stamped with
the branch search-module). In the
Chat tab's Tab widgets section every widget row carries a grip; a fixed divider row named for the session name stands
between the before-the-name rows and the after-the-name rows; dragging the Context bar row above the divider (pointer
events on the grip) stores the order whole with the divider's id in it, moves the context bar BEFORE the name on the
chat frame's strip live, and redraws the section's preview tab the same way; the arrow keys on a focused grip move a row
one place through the same rule; Escape during a drag restores the order and writes nothing. In the Status line section
dragging the Git branch row above the Folder row stores the order and the chat frame's line shows the branch before the
folder, the preview line with it. A store from before the reorder (no order) renders registration order until the user
drags. STATUSLINE_SHOTS=<prefix> writes <prefix>-reorder-<theme>.png of the Chat tab's two sections with their previews.
Skips LOUDLY without the extension deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both).
Synthetic throughout: placeholder sids, TESTHOST, invented text.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment

NAMES = ["web", "api"]
SIDS = {n: "%s-1111-2222-3333-444444444444" % (chr(ord("a") + i) * 8) for i, n in enumerate(NAMES)}
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff")]
BRANCH = "search-module"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = {};
const openShell = async (ctx) => {
  const page = await ctx.newPage();
  await page.goto(cfg.url);
  await page.waitForSelector("#rail-gear", { timeout: 20000 });
  let chatF = page.frames().find((f) => f.url().includes("/chat"));
  for (let i = 0; i < 100 && !chatF; i++) { await page.waitForTimeout(100); chatF = page.frames().find((f) => f.url().includes("/chat")); }
  if (!chatF) { console.error("no chat frame"); process.exit(1); }
  await chatF.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await chatF.click('#tabs .tab[data-id="' + cfg.sidWeb + '"]');
  await chatF.waitForSelector("#statusline .sl-right #spinner-meta", { timeout: 20000 });
  await chatF.waitForTimeout(400);
  return { page, chatF };
};
const settingsFrame = async (page, section) => {
  await page.evaluate((s) => window.__rompOpenSettings("chat", s), section);
  await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  let setF = page.frames().find((f) => f.url().includes("/settings"));
  for (let i = 0; i < 50 && !setF; i++) { await page.waitForTimeout(100); setF = page.frames().find((f) => f.url().includes("/settings")); }
  await setF.waitForSelector("#rs-widgets .rs-widget", { timeout: 15000 });
  await setF.waitForSelector("#rs-swidgets .rs-widget", { timeout: 15000 });
  await setF.evaluate(() => (document.fonts && document.fonts.ready) || null).catch(() => {});
  await setF.waitForTimeout(300);
  return setF;
};
// the strip's web tab: its children's classes in order (the context bar's side of the name is the fact)
const readStrip = (chatF) => chatF.evaluate(([sid]) => {
  const t = document.querySelector('#tabs .tab[data-id="' + sid + '"]');
  const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
  const sl = document.getElementById("statusline"), right = sl.querySelector(".sl-right");
  return { tab: t ? Array.from(t.children).map((c) => c.className) : null,
           right: right ? Array.from(right.children).map((c) => c.className) : null,
           store: { tabOrder: s.tabWidgets ? s.tabWidgets.order : "absent", statusOrder: s.statusWidgets ? s.statusWidgets.order : "absent" } };
}, [cfg.sidWeb]);
const readPanel = (setF) => setF.evaluate(() => {
  const rows = (host) => Array.from(document.querySelectorAll(host + " > .rs-widget")).map((r) => ({
    id: r.dataset.widget || null, divider: r.dataset.divider || null, role: r.getAttribute("role"), aria: r.getAttribute("aria-label"),
    grip: (() => { const g = r.querySelector(".rs-grip"); return g ? { text: g.textContent, aria: g.getAttribute("aria-label"), title: g.title } : null; })(),
    label: (r.querySelector(".rs-widget-name b") || r.querySelector(".rs-divider-label") || {}).textContent || "",
    hasSwitch: !!r.querySelector(".rs-switch"),
    checked: (() => { const sw = r.querySelector(".rs-switch"); return sw ? sw.getAttribute("aria-checked") : null; })() }));
  const preview = (host) => { const title = document.querySelector(host).nextElementSibling; const p = title && title.nextElementSibling;   // T415 part two: the caption is a title above the box
    if (!title || !title.classList.contains("rs-preview-title") || !p || !p.classList.contains("rs-preview")) return null;
    const body = p.querySelector(".rs-preview-body").firstElementChild;
    return { label: title.textContent, cls: body ? body.className : null,
             kids: body ? Array.from(body.children).map((c) => c.className + (c.className === "tab-label" || c.className.indexOf("chip") >= 0 ? ":" + c.textContent : "")) : null,
             right: body && body.querySelector(".rs-sl-right") ? Array.from(body.querySelector(".rs-sl-right").children).map((c) => c.className) : null }; };
  const gridCols = getComputedStyle(document.getElementById("rs-widgets")).gridTemplateColumns;
  const liveEl = document.getElementById("rs-widget-live");
  const previewFolder = document.querySelector("#rs-swidgets + .rs-preview-title + .rs-preview .status-dir");   // the title sits between the rows and the box (T415 part two)
  return { tabRows: rows("#rs-widgets"), statusRows: rows("#rs-swidgets"), tabPreview: preview("#rs-widgets"), statusPreview: preview("#rs-swidgets"), gridCols,
           live: liveEl ? { text: liveEl.textContent, polite: liveEl.getAttribute("aria-live") } : null,
           previewFolder: previewFolder ? { act: previewFolder.dataset.act || null, cls: previewFolder.className, title: previewFolder.title } : null,
           landed: (document.querySelector("#rsettings .rs-card") || {}).getAttribute ? document.querySelector("#rsettings .rs-card").getAttribute("data-section-landed") : null };
});
// a drag by the grip: the pointer down on the grip, moved in steps to the target row's upper half (or the top), released
const dragRow = async (page, setF, host, id, targetId, place) => {
  const g = await setF.evaluate(([host, id, targetId]) => {
    const row = document.querySelector(host + ' > .rs-widget[data-widget="' + id + '"]'), grip = row.querySelector(".rs-grip");
    const tgt = document.querySelector(host + ' > .rs-widget[data-widget="' + targetId + '"], ' + host + ' > .rs-widget[data-divider="' + targetId + '"]');
    const gb = grip.getBoundingClientRect(), tb = tgt.getBoundingClientRect();
    return { gx: gb.left + gb.width / 2, gy: gb.top + gb.height / 2, tTop: tb.top, tBottom: tb.bottom, tH: tb.height };
  }, [host, id, targetId]);
  const fr = await page.evaluate(() => { const f = document.getElementById("f-settings").getBoundingClientRect(); return { x: f.left, y: f.top }; });
  const y = place === "above" ? g.tTop + 2 : g.tBottom - 2;   // above: the upper edge of the target (its midpoint not passed); below: past its midpoint
  await page.mouse.move(fr.x + g.gx, fr.y + g.gy);
  await page.mouse.down();
  await page.mouse.move(fr.x + g.gx, fr.y + g.gy + (y > g.gy ? 6 : -6), { steps: 3 });
  await page.mouse.move(fr.x + g.gx, fr.y + y, { steps: 8 });
  await page.waitForTimeout(100);
  await page.mouse.up();
  await setF.waitForTimeout(500);
};
// ── the main context ── (the web session carries a hot key, so the keycap widget has something to draw on the strip)
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await ctx.addInitScript(([sid]) => {
  try { localStorage.setItem("romp:tabkeys", JSON.stringify({ [sid]: "web" })); localStorage.setItem("romp:keys", JSON.stringify({ ["session.hotkey." + sid]: "Ctrl+Shift+1" })); } catch (e) {}
}, [cfg.sidWeb]);
const { page, chatF } = await openShell(ctx);
out.strip0 = await readStrip(chatF);
const setF = await settingsFrame(page, "tabwidgets");
out.panel0 = await readPanel(setF);
// 1. the Context bar dragged above the divider: before the name on the strip, the order stored whole
await dragRow(page, setF, "#rs-widgets", "ctx", "dot", "above");
out.ctxBefore = { panel: await readPanel(setF), strip: await readStrip(chatF) };
// 2. Escape during a drag: the rows go back, nothing written
{
  const before = await readStrip(chatF);
  const g = await setF.evaluate(() => { const grip = document.querySelector('#rs-widgets > .rs-widget[data-widget="hotkey"] .rs-grip'); const b = grip.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; });
  const fr = await page.evaluate(() => { const f = document.getElementById("f-settings").getBoundingClientRect(); return { x: f.left, y: f.top }; });
  // one row up, inside the card: a release on the shell's backdrop outside the card closes the settings on its own, which is
  // not the drag's doing (the earlier reading parked the pointer 120 px up, on the backdrop)
  await page.mouse.move(fr.x + g.x, fr.y + g.y); await page.mouse.down(); await page.mouse.move(fr.x + g.x, fr.y + g.y - 60, { steps: 8 });
  out.escMid = await readPanel(setF);
  out.escFocus = { frame: await setF.evaluate(() => ({ hasFocus: document.hasFocus(), active: document.activeElement ? (document.activeElement.getAttribute("aria-label") || document.activeElement.tagName) : null })),
                   page: await page.evaluate(() => document.activeElement ? document.activeElement.tagName + "#" + document.activeElement.id : null) };
  await setF.evaluate(() => { const w = window; w.__keys = []; for (const t of ["keydown", "keyup"]) { document.addEventListener(t, (e) => w.__keys.push([t, "frame-capture", e.key, e.defaultPrevented]), true); document.addEventListener(t, (e) => w.__keys.push([t, "frame-bubble", e.key, e.defaultPrevented])); } });
  await page.evaluate(() => { const w = window; w.__keys = []; for (const t of ["keydown", "keyup"]) { document.addEventListener(t, (e) => w.__keys.push([t, "page-capture", e.key]), true); } });
  const openBeforeKey = await page.evaluate(() => document.body.classList.contains("settings-open"));
  await page.keyboard.down("Escape");
  const openAfterDown = await page.evaluate(() => document.body.classList.contains("settings-open"));
  await page.keyboard.up("Escape");
  const openAfterUp = await page.evaluate(() => document.body.classList.contains("settings-open"));
  out.escKeys = { openBeforeKey, openAfterDown, openAfterUp, frame: await setF.evaluate(() => window.__keys), page: await page.evaluate(() => window.__keys) };
  await setF.evaluate(() => { const w = window; w.__evs = []; const p = document.getElementById("rsettings"); for (const t of ["pointerup", "mouseup", "click"]) document.addEventListener(t, (e) => w.__evs.push([t, e.target && (e.target.tagName + "." + (e.target.className || "").toString().slice(0, 30)), p.hidden]), true); });
  await page.evaluate(() => { const w = window; w.__evs = []; w.__msgs = []; for (const t of ["pointerup", "mouseup", "click"]) document.addEventListener(t, (e) => w.__evs.push([t, e.target && e.target.tagName]), true); window.addEventListener("message", (e) => { try { w.__msgs.push(JSON.stringify(e.data).slice(0, 80)); } catch (x) {} }); });
  await page.mouse.up(); await setF.waitForTimeout(400);
  out.escRelease = { frame: await setF.evaluate(() => window.__evs), page: await page.evaluate(() => window.__evs), msgs: await page.evaluate(() => window.__msgs), hidden: await setF.evaluate(() => document.getElementById("rsettings").hidden) };
  out.escAfter = { panel: await readPanel(setF), strip: await readStrip(chatF), before,
                   shellOpen: await page.evaluate(() => document.body.classList.contains("settings-open")) };   // the panel's own Escape-to-close must not have fired
}
// 3. the keyboard road: the hot key's grip focused, ArrowUp moves it one place (above the dot, still after the name)
await setF.focus('#rs-widgets > .rs-widget[data-widget="hotkey"] .rs-grip');
await page.keyboard.press("ArrowUp"); await setF.waitForTimeout(400);   // the page's keyboard reaches the focused grip inside the frame
out.keyUp = { panel: await readPanel(setF), strip: await readStrip(chatF), focused: await setF.evaluate(() => (document.activeElement && document.activeElement.getAttribute("aria-label")) || null) };
// 3b. round two, medium 1: a switch toggled by Space keeps the focus through the paint, and a second Space toggles back
await setF.focus('#rs-widgets > .rs-widget[data-widget="ctx"] .rs-switch');
await page.keyboard.press("Space"); await setF.waitForTimeout(400);
const focusRead = () => setF.evaluate(() => { const a = document.activeElement; const sw = document.querySelector('#rs-widgets > .rs-widget[data-widget="ctx"] .rs-switch');
  return { active: a ? (a.getAttribute("aria-label") || a.tagName) : null, checked: sw.getAttribute("aria-checked") }; });
out.spaceOnce = await focusRead();
await page.keyboard.press("Space"); await setF.waitForTimeout(400);
out.spaceTwice = await focusRead();
// 3c. round two, medium 2: the first keyboard activation after a MOUSE drag lands (the drag's click swallow is the release's own)
await dragRow(page, setF, "#rs-widgets", "hotkey", "dot", "above");   // a real drag first
const storeBefore = await readStrip(chatF);
await setF.focus('#rs-widgets > .rs-widget[data-widget="dot"] .rs-switch');
await page.keyboard.press("Space"); await setF.waitForTimeout(400);
out.afterDragSpace = { before: storeBefore, after: await readStrip(chatF), panel: await readPanel(setF) };
await setF.focus('#rs-widgets > .rs-widget[data-widget="dot"] .rs-switch'); await page.keyboard.press("Space"); await setF.waitForTimeout(300);   // the dot back on
// 3d. round three, the medium: a pointercancel-ended drag leaves no document listener behind and eats no later click. A
// cancel delivers no pointerup for its pointer, so a late-release listener armed on that path waits for the mouse's next
// release, whose id in Chromium is always 1: the drag here is synthetic under id 1 (down on the grip, a move, a cancel on
// the document), the ledger counts the document's pointerup listeners by identity, and a REAL click on a switch follows
await setF.evaluate(() => { const S = new Set(); const a = document.addEventListener.bind(document), r = document.removeEventListener.bind(document);
  window.__puOpen = () => S.size;
  document.addEventListener = function (t, fn, o) { if (t === "pointerup") S.add(fn); return a(t, fn, o); };
  document.removeEventListener = function (t, fn, o) { if (t === "pointerup") S.delete(fn); return r(t, fn, o); }; });
const cancelBefore = await readPanel(setF);
await setF.evaluate(() => { const grip = document.querySelector('#rs-widgets > .rs-widget[data-widget="ctx"] .rs-grip'); const b = grip.getBoundingClientRect();
  const next = document.querySelector('#rs-widgets > .rs-widget[data-widget="hotkey"]').getBoundingClientRect();   // past the next row's midpoint: the row moves live
  grip.dispatchEvent(new PointerEvent("pointerdown", { pointerId: 1, pointerType: "mouse", button: 0, bubbles: true, clientX: b.left + 4, clientY: b.top + b.height / 2 }));
  document.dispatchEvent(new PointerEvent("pointermove", { pointerId: 1, pointerType: "mouse", bubbles: true, clientX: b.left + 4, clientY: next.bottom - 2 })); });
await setF.waitForTimeout(100);
const midDrag = { open: await setF.evaluate(() => window.__puOpen()), rows: (await readPanel(setF)).tabRows.map((r) => r.id || r.divider) };
await setF.evaluate(() => document.dispatchEvent(new PointerEvent("pointercancel", { pointerId: 1, pointerType: "mouse", bubbles: true })));
await setF.waitForTimeout(150);
const afterCancel = { open: await setF.evaluate(() => window.__puOpen()), rows: (await readPanel(setF)).tabRows.map((r) => r.id || r.divider) };
const swb = await setF.evaluate(() => { const b = document.querySelector('#rs-widgets > .rs-widget[data-widget="ctx"] .rs-switch').getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; });
const frS = await page.evaluate(() => { const f = document.getElementById("f-settings").getBoundingClientRect(); return { x: f.left, y: f.top }; });
await page.mouse.click(frS.x + swb.x, frS.y + swb.y); await setF.waitForTimeout(400);   // the next real release anywhere, and its click
const afterClick = await readPanel(setF);
out.cancelLeak = { midDrag, afterCancel, rowsBefore: cancelBefore.tabRows.map((r) => r.id || r.divider), openAfterClick: await setF.evaluate(() => window.__puOpen()),
                   ctxChecked: afterClick.tabRows.filter((r) => r.id === "ctx").map((r) => r.checked)[0], strip: await readStrip(chatF) };
await page.mouse.click(frS.x + swb.x, frS.y + swb.y); await setF.waitForTimeout(300);   // the bar back on
// 3e. the panel closed by script under a held pointer (part two's read, a pre-existing gap): closeSettings ends the drag
// in flight: rows restored, listeners gone, nothing armed. Before, the release never reached the hidden frame's document,
// so the drag's listeners survived the reopen, the next real click was eaten, and a later move plus release stored an order
const closeBefore = { rows: (await readPanel(setF)).tabRows.map((r) => r.id || r.divider), store: await readStrip(chatF) };
await setF.evaluate(() => { const grip = document.querySelector('#rs-widgets > .rs-widget[data-widget="ctx"] .rs-grip'); const b = grip.getBoundingClientRect();
  const next = document.querySelector('#rs-widgets > .rs-widget[data-widget="hotkey"]').getBoundingClientRect();
  grip.dispatchEvent(new PointerEvent("pointerdown", { pointerId: 1, pointerType: "mouse", button: 0, bubbles: true, clientX: b.left + 4, clientY: b.top + b.height / 2 }));
  document.dispatchEvent(new PointerEvent("pointermove", { pointerId: 1, pointerType: "mouse", bubbles: true, clientX: b.left + 4, clientY: next.bottom - 2 })); });
await setF.waitForTimeout(100);
const closeMid = { open: await setF.evaluate(() => window.__puOpen()), rows: (await readPanel(setF)).tabRows.map((r) => r.id || r.divider) };
await page.evaluate(() => window.__rompOpenSettings());   // the scripted close: the opener with no tab toggles the open panel shut
await page.waitForFunction(() => !document.body.classList.contains("settings-open"), null, { timeout: 5000 }).catch(() => {});
await setF.waitForTimeout(150);
const closedOpen = await setF.evaluate(() => window.__puOpen());
await page.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets"));
await setF.waitForSelector("#rs-widgets .rs-widget", { timeout: 15000 }); await setF.waitForTimeout(300);
const reopened = { rows: (await readPanel(setF)).tabRows.map((r) => r.id || r.divider), store: await readStrip(chatF) };
await page.mouse.click(frS.x + swb.x, frS.y + swb.y); await setF.waitForTimeout(400);   // a real click on the Context bar's switch
const clickLanded = (await readPanel(setF)).tabRows.filter((r) => r.id === "ctx").map((r) => r.checked)[0];
await page.mouse.click(frS.x + swb.x, frS.y + swb.y); await setF.waitForTimeout(300);   // the bar back on
await setF.evaluate(() => { const next = document.querySelector('#rs-widgets > .rs-widget[data-widget="dot"]').getBoundingClientRect();   // a later move and release under the same pointer id: a surviving drag would take them
  document.dispatchEvent(new PointerEvent("pointermove", { pointerId: 1, pointerType: "mouse", bubbles: true, clientX: next.left + 4, clientY: next.bottom - 2 }));
  document.dispatchEvent(new PointerEvent("pointerup", { pointerId: 1, pointerType: "mouse", bubbles: true, clientX: next.left + 4, clientY: next.bottom - 2 })); });
await setF.waitForTimeout(300);
out.closeMidDrag = { before: closeBefore, mid: closeMid, closedOpen, reopened, clickLanded, after: { rows: (await readPanel(setF)).tabRows.map((r) => r.id || r.divider), store: await readStrip(chatF), open: await setF.evaluate(() => window.__puOpen()) } };
// 3f. the Token usage opener hides the card too, so it must end a drag in flight the same way (the migration read's low 2)
const rowsNow = async () => (await readPanel(setF)).tabRows.map((r) => r.id || r.divider);
const puOpen = () => setF.evaluate(() => window.__puOpen());
const synthDrag = (gripId, pastId, pid) => setF.evaluate(([gripId, pastId, pid]) => { const grip = document.querySelector('#rs-widgets > .rs-widget[data-widget="' + gripId + '"] .rs-grip'); const b = grip.getBoundingClientRect();
  const past = document.querySelector('#rs-widgets > .rs-widget[data-widget="' + pastId + '"], #rs-widgets > .rs-widget[data-divider="' + pastId + '"]').getBoundingClientRect();
  grip.dispatchEvent(new PointerEvent("pointerdown", { pointerId: pid, pointerType: "mouse", button: 0, bubbles: true, clientX: b.left + 4, clientY: b.top + b.height / 2 }));
  document.dispatchEvent(new PointerEvent("pointermove", { pointerId: pid, pointerType: "mouse", bubbles: true, clientX: b.left + 4, clientY: past.bottom - 2 })); }, [gripId, pastId, pid]);
const strayMoveUp = (pid, overId) => setF.evaluate(([pid, overId]) => { const over = document.querySelector('#rs-widgets > .rs-widget[data-widget="' + overId + '"]').getBoundingClientRect();
  document.dispatchEvent(new PointerEvent("pointermove", { pointerId: pid, pointerType: "mouse", bubbles: true, clientX: over.left + 4, clientY: over.bottom - 2 }));
  document.dispatchEvent(new PointerEvent("pointerup", { pointerId: pid, pointerType: "mouse", bubbles: true, clientX: over.left + 4, clientY: over.bottom - 2 })); }, [pid, overId]);
const raBefore = { rows: await rowsNow(), store: await readStrip(chatF) };
await synthDrag("ctx", "hotkey", 1); await setF.waitForTimeout(100);
const raMid = { open: await puOpen(), rows: await rowsNow() };
await setF.evaluate(() => document.getElementById("ra-open").click()); await setF.waitForTimeout(250);   // the button lives on another tab: a scripted click, as the reviewer reached it
const raOpened = { open: await puOpen(), cardHidden: await setF.evaluate(() => document.getElementById("rsettings").hidden), backShown: await setF.evaluate(() => !document.getElementById("ranalytics-back").hidden) };
await setF.evaluate(() => { document.getElementById("ranalytics-back").hidden = true; });   // the panel's own close leaves the card hidden: the gear reopens below
await page.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets"));
await setF.waitForSelector("#rs-widgets .rs-widget", { timeout: 15000 }); await setF.waitForTimeout(300);
const raReopened = { rows: await rowsNow(), store: await readStrip(chatF) };
await strayMoveUp(1, "dot"); await setF.waitForTimeout(300);
out.tokenUsageMidDrag = { before: raBefore, mid: raMid, opened: raOpened, reopened: raReopened, after: { rows: await rowsNow(), store: await readStrip(chatF), open: await puOpen() } };
// 3g. two drags in flight, two pointers on two grips: one close ends them both (the abort hook was a single slot)
const twoBefore = { rows: await rowsNow(), store: await readStrip(chatF) };
await synthDrag("ctx", "hotkey", 7); await setF.waitForTimeout(80);
await synthDrag("dot", "name", 8); await setF.waitForTimeout(100);
const twoMid = { open: await puOpen(), rows: await rowsNow() };
await page.evaluate(() => window.__rompOpenSettings());
await page.waitForFunction(() => !document.body.classList.contains("settings-open"), null, { timeout: 5000 }).catch(() => {});
await setF.waitForTimeout(150);
const twoClosed = await puOpen();
await page.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets"));
await setF.waitForSelector("#rs-widgets .rs-widget", { timeout: 15000 }); await setF.waitForTimeout(300);
const twoReopened = { rows: await rowsNow(), store: await readStrip(chatF) };
await strayMoveUp(7, "dot"); await strayMoveUp(8, "hotkey"); await setF.waitForTimeout(300);
out.twoDragsClose = { before: twoBefore, mid: twoMid, closedOpen: twoClosed, reopened: twoReopened, after: { rows: await rowsNow(), store: await readStrip(chatF), open: await puOpen() } };
// 4. the Status line section: the branch dragged above the folder
await page.evaluate(() => window.__rompOpenSettings("chat", "statusline"));
await setF.waitForSelector('#rsettings .rs-card[data-section-landed="statusline"]', { timeout: 10000 }).catch(() => {});
await dragRow(page, setF, "#rs-swidgets", "branch", "folder", "above");
out.branchFirst = { panel: await readPanel(setF), strip: await readStrip(chatF) };
// 4b. round two, low 3: a status-line row stays in its slot's group: the session name (left) dragged below the folder (right)
// snaps back and writes nothing; ArrowDown on its grip is refused the same way
await dragRow(page, setF, "#rs-swidgets", "name", "folder", "below");
out.nameHeld = { panel: await readPanel(setF), strip: await readStrip(chatF) };
await setF.focus('#rs-swidgets > .rs-widget[data-widget="name"] .rs-grip');
await page.keyboard.press("ArrowDown"); await setF.waitForTimeout(400);
out.nameKeyHeld = { panel: await readPanel(setF), strip: await readStrip(chatF) };
// screenshots: the Chat tab scrolled to Tab widgets, both sections with their previews in view, dark then light
await page.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets"));
await setF.waitForSelector('#rsettings .rs-card[data-section-landed="tabwidgets"]', { timeout: 10000 }).catch(() => {});
for (const theme of ["dark", "light"]) {
  for (const f of [page, chatF, setF]) await f.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme).catch(() => {});
  await page.mouse.move(5, 5); await page.waitForTimeout(600);
  if (!cfg.shots) continue;
  const card = await setF.evaluate(() => { const b = document.querySelector("#rsettings .rs-card").getBoundingClientRect(); return { x: b.left, y: b.top, width: b.width, height: b.height }; });
  const fr = await page.evaluate(() => { const f = document.getElementById("f-settings").getBoundingClientRect(); return { x: f.left, y: f.top }; });
  await page.screenshot({ path: cfg.shots + "-reorder-" + theme + ".png", clip: { x: fr.x + card.x, y: fr.y + card.y, width: card.width, height: card.height } });
}
await page.close(); await ctx.close();
// ── a store from before the reorder: prefs without an order render registration order until the user drags ──
{
  const c2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await c2.addInitScript(() => { try { localStorage.setItem("romp:settings", JSON.stringify({ compact: true, tabWidgets: { on: { ctx: false }, order: [], opts: {} } })); } catch (e) {} });
  const { page: p2, chatF: cf } = await openShell(c2);
  const sf = await settingsFrame(p2, "tabwidgets");
  out.legacy = { panel: await readPanel(sf), strip: await readStrip(cf) };
  await p2.close(); await c2.close();
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
console.log("RESULT: ok");
await browser.close();
process.exit(0);
"""


class ServedWidgetReorder(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
            cls._run()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box: the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="widget-reorder-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, name in enumerate(NAMES):
            sid = SIDS[name]
            bg, fg = PALETTE[i]
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5", **({"liveCtx": 62} if name == "web" else {})}))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid, "cwd": cwd, "gitBranch": BRANCH,
                     "message": {"role": "user", "content": "what does the %s session do in notes-api?" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid, "cwd": cwd, "gitBranch": BRANCH,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "It keeps the %s side of the notes-api tidy." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-reorder"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def _run(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        outp = os.path.join(cls.lab, "out.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": outp, "sidWeb": SIDS["web"],
                       "shots": os.environ.get("STATUSLINE_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served guard needs one")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        cls.out = json.load(open(outp))
        dump = os.environ.get("STATUSLINE_DUMP")
        if dump:
            with open(dump, "w") as f:
                json.dump(cls.out, f, indent=1)

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill(); k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @staticmethod
    def _ids(rows):
        return [r["id"] or r["divider"] for r in rows]

    @staticmethod
    def _at(classes, prefix):
        """The index of the first child whose class list starts with `prefix` (the strip's dot wears its state after "tab-dot")."""
        for i, c in enumerate(classes):
            if c == prefix or c.startswith(prefix + " "):
                return i
        raise AssertionError("%r not among %r" % (prefix, classes))

    def test_every_widget_row_has_a_grip_and_the_tab_widgets_carry_the_divider_between_the_sides(self):
        p = self.out["panel0"]
        self.assertEqual(self._ids(p["tabRows"]), ["dot", "name", "ctx", "hotkey"], "registration order with the divider between the sides")
        self.assertEqual(self._ids(p["statusRows"]), ["name", "folder", "branch", "host"])
        for r in p["tabRows"] + p["statusRows"]:
            if r["divider"]:
                self.assertEqual((r["role"], r["aria"], r["label"], r["grip"], r["hasSwitch"]), ("separator", "Session name", "Session name", None, False), "the divider: a separator named for the name's place, no grip, no switch: %r" % r)
            else:
                self.assertEqual(r["grip"]["text"], "⠿", "the six-dot grip: %r" % r)
                self.assertEqual(r["grip"]["aria"], "Drag to reorder: " + r["label"])
                self.assertIn("arrow keys", r["grip"]["title"])
        self.assertTrue(p["gridCols"].startswith("18px "), "the grip's column leads the grid: %r" % p["gridCols"])
        self.assertEqual(self.out["strip0"]["store"], {"tabOrder": "absent", "statusOrder": "absent"}, "nothing written until the user drags")

    def test_the_previews_draw_the_surfaces_from_the_current_choices(self):
        p = self.out["panel0"]
        self.assertEqual(p["tabPreview"]["label"], "Preview"); self.assertEqual(p["statusPreview"]["label"], "Preview")
        self.assertEqual(p["tabPreview"]["cls"], "tab colored", "the demo tab wears the identity colour class, as a real tab does (T415 part two)")
        self.assertEqual(p["tabPreview"]["kids"], ["tab-dot", "tab-label:session_name", "tab-ctx", "tab-key"], "the dot before the name, the context bar and the hot key after it, over the demo status")
        self.assertEqual(p["statusPreview"]["cls"], "rs-sl")
        self.assertEqual(p["statusPreview"]["kids"][:1], ["chip rs-sl-chip:Ready"], "no session name by default, the state chip leads")
        self.assertEqual(p["statusPreview"]["right"], ["status-dir", "status-branch", "spinner-meta", "ctx-bar"], "folder, branch, then the controls and the battery; the preview's folder inert")

    def test_dragging_the_context_bar_above_the_divider_puts_it_before_the_name_on_the_strip_live_and_stores_the_order_whole(self):
        c = self.out["ctxBefore"]
        self.assertEqual(self._ids(c["panel"]["tabRows"]), ["ctx", "dot", "name", "hotkey"], "the row landed above the dot, on the name's other side")
        self.assertEqual(c["strip"]["store"]["tabOrder"], ["ctx", "dot", "name", "hotkey"], "the order stored whole, the divider's id in it")
        tab = c["strip"]["tab"]
        self.assertLess(self._at(tab, "tab-ctx"), self._at(tab, "tab-dot"), "the context bar leads the before side on the chat frame's strip: %r" % tab)
        self.assertLess(self._at(tab, "tab-dot"), self._at(tab, "tab-label"), "...the dot still before the name, its own side unchanged: %r" % tab)
        self.assertEqual(c["panel"]["tabPreview"]["kids"], ["tab-ctx", "tab-dot", "tab-label:session_name", "tab-key"], "the preview follows")

    def test_escape_during_a_drag_restores_the_rows_and_writes_nothing(self):
        e = self.out["escAfter"]
        self.assertNotEqual(self._ids(self.out["escMid"]["tabRows"]), self._ids(e["panel"]["tabRows"]), "the row had moved mid-drag: %r" % self.out["escMid"]["tabRows"])
        self.assertEqual(self._ids(e["panel"]["tabRows"]), ["ctx", "dot", "name", "hotkey"], "back where it was")
        self.assertEqual(e["strip"]["store"]["tabOrder"], e["before"]["store"]["tabOrder"], "nothing written")
        self.assertEqual(e["strip"]["tab"], e["before"]["tab"], "the strip unchanged")
        self.assertTrue(e["shellOpen"], "the settings stayed open: the drag's Escape went no further (release: %r)" % (self.out.get("escRelease"),))

    def test_the_arrow_keys_move_a_row_one_place_and_keep_the_focus_on_its_grip(self):
        k = self.out["keyUp"]
        self.assertEqual(self._ids(k["panel"]["tabRows"]), ["ctx", "dot", "hotkey", "name"], "ArrowUp took the hot key above the divider: before the name now")
        self.assertEqual(k["strip"]["store"]["tabOrder"], ["ctx", "dot", "hotkey", "name"])
        self.assertEqual(k["focused"], "Drag to reorder: Hot key", "the grip keeps the focus for the next key")
        tab = k["strip"]["tab"]
        self.assertLess(self._at(tab, "tab-key"), self._at(tab, "tab-label"), "the keycap before the name on the strip: %r" % tab)

    def test_a_switch_toggled_by_space_keeps_its_focus_through_the_paint_and_toggles_back(self):
        one, two = self.out["spaceOnce"], self.out["spaceTwice"]
        self.assertEqual((one["active"], one["checked"]), ("Context bar", "false"), "the first Space toggled the bar off and the switch kept the focus: %r" % one)
        self.assertEqual((two["active"], two["checked"]), ("Context bar", "true"), "the second Space toggled it back: %r" % two)

    def test_the_first_keyboard_activation_after_a_mouse_drag_lands(self):
        a = self.out["afterDragSpace"]
        dot_before = a["before"]["tab"]; dot_after = a["after"]["tab"]
        self.assertTrue(any(c.startswith("tab-dot") for c in dot_before), "the dot was on the strip before: %r" % dot_before)
        self.assertFalse(any(c.startswith("tab-dot") for c in dot_after), "Space on the dot's switch right after a drag switched it off: the swallow took no keyboard click: %r" % dot_after)
        self.assertEqual([r["checked"] for r in a["panel"]["tabRows"] if r["id"] == "dot"], ["false"])

    def test_a_status_line_row_stays_in_its_slot_a_drag_or_a_key_across_is_refused(self):
        h = self.out["nameHeld"]
        self.assertEqual(self._ids(h["panel"]["statusRows"]), ["name", "branch", "folder", "host"], "the name row snapped back above the right slot's rows")
        self.assertEqual(h["strip"]["store"]["statusOrder"], ["name", "branch", "folder", "host"], "nothing new written")
        k = self.out["nameKeyHeld"]
        self.assertEqual(self._ids(k["panel"]["statusRows"]), ["name", "branch", "folder", "host"], "ArrowDown at the slot's edge is refused")
        self.assertEqual(k["strip"]["store"]["statusOrder"], ["name", "branch", "folder", "host"])

    def test_a_cancelled_drag_leaves_no_release_listener_behind_and_the_next_click_lands(self):
        c = self.out["cancelLeak"]
        self.assertEqual(c["midDrag"]["open"], 1, "during the drag the document holds the drag's own release listener: %r" % c["midDrag"])
        self.assertEqual(c["afterCancel"]["open"], 0, "the cancel path arms nothing: a cancelled pointer fires no click, so no late-release listener waits for the mouse's next release (%r)" % c["afterCancel"])
        self.assertEqual(c["ctxChecked"], "false", "the next real click landed on the switch: nothing swallowed it")
        self.assertNotEqual(c["midDrag"]["rows"], c["rowsBefore"], "the synthetic move carried the row past a midpoint: %r" % (c["midDrag"]["rows"],))
        self.assertEqual(c["afterCancel"]["rows"], c["rowsBefore"], "the cancel restored the rows")
        self.assertFalse(any(cl.startswith("ctx") for cl in c["strip"]["tab"]), "and the strip followed: %r" % c["strip"]["tab"])
        self.assertEqual(c["openAfterClick"], 0)

    def test_closing_the_panel_under_a_held_pointer_ends_the_drag_restores_the_rows_and_arms_nothing(self):
        c = self.out["closeMidDrag"]
        self.assertEqual(c["mid"]["open"], 1, "the drag was on when the panel closed: %r" % c["mid"])
        self.assertNotEqual(c["mid"]["rows"], c["before"]["rows"], "and its row had moved live")
        self.assertEqual(c["closedOpen"], 0, "closeSettings ended the drag: no release listener survives the close")
        self.assertEqual(c["reopened"]["rows"], c["before"]["rows"], "the rows came back in the stored order")
        self.assertEqual(c["reopened"]["store"]["store"], c["before"]["store"]["store"], "nothing stored by the close")
        self.assertEqual(c["clickLanded"], "false", "the next real click landed on the switch")
        self.assertEqual(c["after"]["rows"], c["before"]["rows"], "a later move and release under the same pointer id moved nothing: the drag is over")
        self.assertEqual(c["after"]["store"]["store"], c["before"]["store"]["store"], "and stored nothing")
        self.assertEqual(c["after"]["open"], 0)

    def test_the_token_usage_opener_ends_a_drag_in_flight_too(self):
        c = self.out["tokenUsageMidDrag"]
        self.assertEqual(c["mid"]["open"], 1, "the drag was on: %r" % c["mid"]); self.assertNotEqual(c["mid"]["rows"], c["before"]["rows"], "and its row had moved live")
        self.assertEqual((c["opened"]["cardHidden"], c["opened"]["backShown"]), (True, True), "the Token usage panel replaced the card: %r" % c["opened"])
        self.assertEqual(c["opened"]["open"], 0, "the opener ended the drag: no release listener survives the card's hide (the migration read's low 2)")
        self.assertEqual(c["reopened"]["rows"], c["before"]["rows"], "the rows came back in the stored order")
        self.assertEqual(c["reopened"]["store"]["store"], c["before"]["store"]["store"], "nothing stored")
        self.assertEqual(c["after"]["rows"], c["before"]["rows"], "a later move and release under the drag's pointer id moved nothing")
        self.assertEqual(c["after"]["store"]["store"], c["before"]["store"]["store"]); self.assertEqual(c["after"]["open"], 0)

    def test_two_drags_in_flight_end_together_on_a_close(self):
        c = self.out["twoDragsClose"]
        self.assertEqual(c["mid"]["open"], 2, "two pointers, two drags, two release listeners: %r" % c["mid"]); self.assertNotEqual(c["mid"]["rows"], c["before"]["rows"])
        self.assertEqual(c["closedOpen"], 0, "one close ends every drag in flight, not the last one pressed")
        self.assertEqual(c["reopened"]["rows"], c["before"]["rows"], "both rows back in the stored order")
        self.assertEqual(c["reopened"]["store"]["store"], c["before"]["store"]["store"])
        self.assertEqual(c["after"]["rows"], c["before"]["rows"], "later moves and releases under either pointer id moved nothing: %r" % (c["after"]["rows"],))
        self.assertEqual(c["after"]["store"]["store"], c["before"]["store"]["store"]); self.assertEqual(c["after"]["open"], 0)

    def test_every_move_speaks_through_the_live_region_and_the_previews_are_inert(self):
        c = self.out["ctxBefore"]["panel"]
        self.assertEqual(c["live"], {"text": "Context bar moved to position 1 of 3, before the session name", "polite": "polite"}, "the drag's move announced: %r" % c["live"])
        k = self.out["keyUp"]["panel"]
        self.assertEqual(k["live"]["text"], "Hot key moved to position 3 of 3, before the session name", "the key's move announced: %r" % k["live"])
        b = self.out["branchFirst"]["panel"]
        self.assertEqual(b["live"]["text"], "Git branch moved to position 1 of 3 in the right slot", "the status line counts within the slot and names it (round three, low 2): %r" % b["live"])
        h = self.out["nameHeld"]["panel"]
        self.assertEqual(h["live"]["text"], "Session name stays in the left slot", "a refused drag is announced (round three, low 1): %r" % h["live"])
        k2 = self.out["nameKeyHeld"]["panel"]
        self.assertEqual(k2["live"]["text"], "Session name stays in the left slot", "a refused key too: %r" % k2["live"])
        pf = self.out["panel0"]["previewFolder"]
        self.assertEqual((pf["act"], pf["cls"]), (None, "status-dir"), "the preview's folder carries no click act and no link dress: %r" % pf)
        self.assertIn("notes-api", pf["title"])

    def test_dragging_the_branch_above_the_folder_reorders_the_line_live_and_the_preview_with_it(self):
        b = self.out["branchFirst"]
        self.assertEqual(self._ids(b["panel"]["statusRows"]), ["name", "branch", "folder", "host"])
        self.assertEqual(b["strip"]["store"]["statusOrder"], ["name", "branch", "folder", "host"])
        self.assertEqual(b["strip"]["right"][:3], ["status-branch", "status-dir folder-link", "spinner-meta"], "the branch leads the line's right cluster now: %r" % b["strip"]["right"])
        self.assertEqual(b["panel"]["statusPreview"]["right"][:2], ["status-branch", "status-dir"])

    def test_a_store_from_before_the_reorder_renders_registration_order_until_the_user_drags(self):
        L = self.out["legacy"]
        self.assertEqual(self._ids(L["panel"]["tabRows"]), ["dot", "name", "ctx", "hotkey"])
        self.assertEqual(L["strip"]["store"]["tabOrder"], [], "the empty order stays as it was")
        tab = L["strip"]["tab"]
        self.assertLess(self._at(tab, "tab-dot"), self._at(tab, "tab-label"))
        self.assertNotIn("tab-ctx", tab, "the store's own switch (the context bar off) still holds")


if __name__ == "__main__":
    unittest.main()
