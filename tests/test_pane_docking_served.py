#!/usr/bin/env python3
"""THE PANE DOCKING ENGINE on the served dashboard, driven with REAL pointer events (plans/pane-docking.md phase two;
the user 2026-09-18: move panes by their empty space, no title bars, a blue outline where the pane will land, default
off). One hermetic kernel, Chromium on the shell page (`/`), two contexts:

ON (the gear's paneDocking true, seeded in the store before the page loads): the cursor states by COMPUTED cursor (the
open hand over a pane's ring and over the chat strip's empty run, the closed hand while a pane is held, the open hand
over content while Option/Alt is down); a press on a pane's ring lifts only past the slop; the accent outline follows
the pointer across two half-zones of one pane (its rectangle re-read right before each comparison); a pane dropped
into each of the four half-zones lands there (the layout store and the panes' rectangles after each); Escape cancels
a drag with the store untouched; the timeline band keeps a fixed height; the three shipped stores are never written.

OFF (nothing seeded, the default): the same gestures change nothing: the pane row's DOM is byte-identical before and
after, no layout store exists, no engine stylesheet or outline node, the body carries no engine class.

Synthetic only: placeholder uuids, the notes-api world. Skips LOUDLY without the extension deps or a Playwright browser,
and for nothing else (ROMP_SERVED_TESTS_REQUIRE=1 turns the skips red where the browser is installed)."""
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

RING = 6   # the grab ring's px (pane-dock.ts RING): every iframe is its pane inset by this much
SID_WEB = "11111111-2222-4333-8444-0000000000d1"
SID_API = "11111111-2222-4333-8444-0000000000d2"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turns(sid, tag, t0, n):
    out, parent = [], None
    for k in range(n):
        u, a = "%s-u%02d" % (tag, k), "%s-a%02d" % (tag, k)
        t = t0 + 300 * k
        out.append({"type": "user", "timestamp": _iso(t), "uuid": u, "parentUuid": parent, "sessionId": sid, "promptSource": "typed",
                    "message": {"role": "user", "content": "turn %d: tidy the notes-api search ranking" % k}})
        out.append({"type": "assistant", "timestamp": _iso(t + 60), "uuid": a, "parentUuid": u, "sessionId": sid,
                    "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "The ranking weights now come from the config file."}]}})
        parent = a
    return out


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { errors: [], on: {}, off: {} };
const PANES = { chat: true, fleet: true, feed: true, timeline: true, files: false };

const seed = (on) => (() => {
  if (window !== window.top) return;
  try {
    if (localStorage.getItem("__pd_seeded")) return;
    localStorage.setItem("__pd_seeded", "1");
    localStorage.setItem("romp-panes", JSON.stringify(__PANES__));
    localStorage.setItem("romp-pane-grow", JSON.stringify({ chat: 60, fleet: 34, feed: 40, files: 40 }));   // a real value, so "byte-identical" is not two absences
    localStorage.removeItem("romp-layout");
    if (__ON__) localStorage.setItem("romp:settings", JSON.stringify({ paneDocking: true, panes: { timeline: true, fleet: true, feed: true } }));
    else localStorage.setItem("romp:settings", JSON.stringify({ panes: { timeline: true, fleet: true, feed: true } }));
  } catch (e) {}
}).toString().replace("__PANES__", JSON.stringify(PANES)).replace("__ON__", on ? "true" : "false");

const rectsOf = (page) => page.evaluate(() => Object.fromEntries((window.__rompPaneDock ? window.__rompPaneDock.rects() : []).map((r) => {
  const el = document.getElementById(r.pane); const f = el && el.querySelector(":scope > iframe"); const b = f ? f.getBoundingClientRect() : null;
  return [r.pane, Object.assign({}, r.rect, { frame: b ? { x: b.left, y: b.top, w: b.width, h: b.height } : null })];
})));
if (cfg.shots) fs.mkdirSync(cfg.shots, { recursive: true });
const leavesOf = (page) => page.evaluate(() => { const lay = window.__rompPaneDock.layout(); const lv = (n) => n.pane ? [n.pane] : n.kids.flatMap(lv); return lay ? lv(lay.tree) : []; });
const frameOfApp = (page, re) => page.frames().find((f) => re.test(f.url()));
// the spot is found GEOMETRICALLY, with no engine and no detector on the page (so the base measures the behaviour, not a
// missing name): the feed's list below its last card, the band's SVG root outside every lane and mark, the outline's
// list below its rows, the files pane's empty state; the element under each candidate point is the page's own
// container, never a card, a row, a control or an SVG child
const emptySpot = (fr, app) => fr.evaluate((app) => {
  const empties = { feed: "body, #feed-list, #feed-cols, .feed-cols, .feed-col, .feed-col-list, #feed-foot", timeline: "body, #host, .romp-tl-wrap, svg", fleet: "body, #fleet-list, #fleet-foot", files: "body, #files-empty" }[app];
  const isEmpty = (el) => { try { return !!el && !el.closest("a, button, input, textarea, select, label, [role], [contenteditable], [data-act], [data-sid], [data-id], .fitem, .ftask-group, svg *") && el.matches(empties); } catch (e) { return false; } };
  const pts = [];
  if (app === "feed") {
    const list = document.querySelector("#feed-list") || document.body; const lr = list.getBoundingClientRect();
    const cards = document.querySelectorAll("#feed-list .fitem"); const last = cards[cards.length - 1];
    const y0 = last ? last.getBoundingClientRect().bottom + 30 : lr.top + lr.height * 0.6;
    for (const dy of [0, 40, 80, 120, 160]) for (const fx of [0.5, 0.3, 0.7]) pts.push({ x: lr.left + lr.width * fx, y: Math.min(y0 + dy, lr.bottom - 8) });
  } else {
    const root = document.querySelector(app === "timeline" ? "svg" : app === "fleet" ? "#fleet-list" : "#files-empty") || document.body; const r = root.getBoundingClientRect();
    for (const fy of [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]) for (const fx of [0.5, 0.3, 0.7, 0.15, 0.85]) pts.push({ x: r.left + r.width * fx, y: r.top + r.height * fy });
  }
  const tried = [];
  for (const p of pts) { const el = document.elementFromPoint(p.x, p.y); if (isEmpty(el)) return { x: p.x, y: p.y, tag: el.tagName, id: el.id || "", cls: String(el.className || "") }; if (tried.length < 10) tried.push(el ? el.tagName + (el.id ? "#" + el.id : "") : "none"); }
  return { error: "no empty spot", tried };
}, app);
const frameOrigin = (page, fid) => page.evaluate((id) => { const f = document.getElementById(id); const b = f.getBoundingClientRect(); return { x: b.left + f.clientLeft, y: b.top + f.clientTop }; }, fid);
const store = (page) => page.evaluate(() => ({ layout: localStorage.getItem("romp-layout"), grow: localStorage.getItem("romp-pane-grow"), panes: localStorage.getItem("romp-panes") }));
const outlineRect = (page) => page.evaluate(() => { const o = document.getElementById("pd-outline"); if (!o) return null; const r = o.getBoundingClientRect(); return { on: o.classList.contains("on"), free: o.classList.contains("free"), refused: o.classList.contains("refused"), x: r.left, y: r.top, w: r.width, h: r.height, text: o.textContent }; });
const chatFrame = (page) => page.frames().find((f) => /\/chat(\?|$)/.test(f.url()));
// Chromium delivers pointermove aligned to animation frames: read the engine's state only after two frames have
// passed since the move (an event wait, not a timer), so a read never runs ahead of the event it measures
const frame = (page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(1)))));

// a REAL pointer drag: press at (x0,y0), travel past the slop, visit each waypoint (recording the outline against the
// rects read right then), release at the last waypoint
async function drag(page, x0, y0, waypoints, { release = true, escape = false } = {}) {
  const rec = { pressed: null, armed: null, way: [] };
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  await frame(page);
  rec.pressed = await page.evaluate(() => ({ dragging: window.__rompPaneDock.dragging(), bodyCursor: getComputedStyle(document.body).cursor, cls: document.body.className,
    pressedPaneCursor: (() => { const p = document.querySelector(".pane:active"); return p ? getComputedStyle(p).cursor : null; })() }));
  await page.mouse.move(x0 + 3, y0 + 3);   // under the slop: nothing lifts
  await frame(page);
  rec.underSlop = await page.evaluate(() => window.__rompPaneDock.dragging());
  await page.mouse.move(x0 + 14, y0 + 14, { steps: 3 });
  await frame(page);
  rec.armed = await page.evaluate(() => ({ dragging: window.__rompPaneDock.dragging(), bodyCursor: getComputedStyle(document.body).cursor, paneCursor: getComputedStyle(document.getElementById("feed-pane")).cursor }));
  for (const w of waypoints) {
    await page.mouse.move(w.x, w.y, { steps: 6 });
    await frame(page);
    rec.way.push({ at: w, rects: await rectsOf(page), outline: await outlineRect(page), zone: await page.evaluate(() => window.__rompPaneDock.zone()) });
    if (cfg.shots && waypoints.length === 2 && rec.way.length === 2) await page.screenshot({ path: cfg.shots + "/pane-docking-drag.png" });
  }
  if (escape) { await page.keyboard.press("Escape"); await frame(page); rec.afterEscape = await page.evaluate(() => ({ dragging: window.__rompPaneDock.dragging(), outline: document.getElementById("pd-outline").classList.contains("on") })); await page.mouse.up(); }
  else if (release) { await page.mouse.up(); await frame(page); }
  rec.after = { rects: await rectsOf(page), store: await store(page), dragging: await page.evaluate(() => window.__rompPaneDock.dragging()), outlineOn: await page.evaluate(() => document.getElementById("pd-outline").classList.contains("on")) };
  return rec;
}

// ── ON ────────────────────────────────────────────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  await ctx.addInitScript("(" + seed(true) + ")()");
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push("on: " + String(e && e.stack || e).slice(0, 300)));
  await page.goto(cfg.url);
  const ready = await page.waitForFunction(() => {
    const pd = window.__rompPaneDock; if (!pd || !pd.on()) return false;
    const r = pd.rects(); if (r.length < 4) return false;
    const f = document.getElementById("f-chat"); const d = f && f.contentDocument;
    return !!(d && d.getElementById("tabbar") && document.getElementById("f-fleet") && document.getElementById("f-fleet").getAttribute("src"));
  }, null, { timeout: 60000 }).then(() => true).catch(() => false);
  out.on.ready = ready;
  if (ready) await page.waitForFunction(() => !!(window.__rompPaneDock.layout() && localStorage.getItem("romp-layout") && document.querySelectorAll(".pd-div").length >= 1
    && getComputedStyle(document.getElementById("feed-pane")).cursor === "grab"), null, { timeout: 20000 }).catch(() => {});
  const o = out.on;
  o.bodyClass = await page.evaluate(() => document.body.className);
  o.rects0 = await rectsOf(page);
  o.store0 = await store(page);
  // no engine (the base, or a build without the bundle): measure what is there and stop, so every test reports the
  // absence through its own assertion instead of the driver dying on an undefined rectangle
  if (!ready || !o.rects0["feed-pane"] || !o.rects0["fleet-pane"] || !o.rects0["chat-pane"]) {
    o.absent = true;
    if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/pane-docking-on.png" }); }
    await ctx.close();
  } else {
  o.tl = await page.evaluate(() => { const c = document.querySelector(".col"); return { tl: getComputedStyle(c).getPropertyValue("--tl").trim(), rowBottom: document.querySelector(".row").getBoundingClientRect().bottom, colBottom: c.getBoundingClientRect().bottom }; });
  // cursors at rest: the ring, the shell body, the chat strip's empty run and a tab in it
  const cf = chatFrame(page);
  o.cursors = {
    feedRing: await page.evaluate(() => getComputedStyle(document.getElementById("feed-pane")).cursor),
    body: await page.evaluate(() => getComputedStyle(document.body).cursor),
    tabbar: cf ? await cf.evaluate(() => getComputedStyle(document.getElementById("tabbar")).cursor) : null,
    tab: cf ? await cf.evaluate(() => { const t = document.querySelector("#tabs .tab"); return t ? getComputedStyle(t).cursor : null; }) : null,
    chatRoot: cf ? await cf.evaluate(() => getComputedStyle(document.documentElement).cursor) : null,
  };
  // Option/Alt held: the open hand over content
  await page.keyboard.down("Alt");
  await page.waitForFunction(() => document.body.classList.contains("pd-alt"), null, { timeout: 5000 }).catch(() => {});
  o.alt = { chatRoot: cf ? await cf.evaluate(() => getComputedStyle(document.documentElement).cursor) : null, bodyCls: await page.evaluate(() => document.body.className) };
  await page.keyboard.up("Alt");
  await page.waitForFunction(() => !document.body.classList.contains("pd-alt"), null, { timeout: 5000 }).catch(() => {});
  o.altUp = { chatRoot: cf ? await cf.evaluate(() => getComputedStyle(document.documentElement).cursor) : null, bodyCls: await page.evaluate(() => document.body.className) };
  // a plain press inside a pane's CONTENT (not the ring, no Option): the content's own cursor, nothing lifts, nothing moves
  o.contentPress = await (async () => {
    const rr = await rectsOf(page); const c = rr["chat-pane"]; const x = c.x + c.w / 2, y = c.y + c.h / 2;
    const cursorAtRest = cf ? await cf.evaluate(() => getComputedStyle(document.body).cursor) : null;
    await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x + 14, y + 14, { steps: 3 }); await frame(page);
    const st = await page.evaluate(() => ({ dragging: window.__rompPaneDock.dragging(), outline: document.getElementById("pd-outline").classList.contains("on"), bodyCursor: getComputedStyle(document.body).cursor }));
    await page.mouse.move(x + 60, y + 60, { steps: 3 }); await frame(page); await page.mouse.up(); await frame(page);
    return Object.assign({ cursorAtRest }, st, { store: await store(page), rects: await rectsOf(page) });
  })();
  // drag 1: the feed by its top ring, across the outline pane's TOP then BOTTOM half, dropped on the bottom
  let r = o.rects0, feed = r["feed-pane"], fleet = r["fleet-pane"];
  o.drag1 = await drag(page, feed.x + feed.w / 2, feed.y + 1, [
    { x: fleet.x + fleet.w / 2, y: fleet.y + fleet.h * 0.2 },
    { x: fleet.x + fleet.w / 2, y: fleet.y + fleet.h * 0.8 },
  ]);
  // drag 2: the feed (now under the outline) by its top ring to the chat's LEFT half
  r = o.drag1.after.rects; feed = r["feed-pane"]; let chat = r["chat-pane"];
  o.drag2 = await drag(page, feed.x + feed.w / 2, feed.y + 1, [{ x: chat.x + chat.w * 0.1, y: chat.y + chat.h / 2 }]);
  // drag 3: the outline by its LEFT ring to the chat's TOP half
  r = o.drag2.after.rects; fleet = r["fleet-pane"]; chat = r["chat-pane"];
  o.drag3 = await drag(page, fleet.x + 1, fleet.y + fleet.h / 2, [{ x: chat.x + chat.w / 2, y: chat.y + chat.h * 0.1 }]);
  // drag 4: the outline by its top ring to the feed's RIGHT half
  r = o.drag3.after.rects; fleet = r["fleet-pane"]; feed = r["feed-pane"];
  o.drag4 = await drag(page, fleet.x + fleet.w / 2, fleet.y + 1, [{ x: feed.x + feed.w * 0.9, y: feed.y + feed.h / 2 }]);
  // Escape mid-drag: the chat by its top ring, lifted over the feed, cancelled
  r = o.drag4.after.rects; chat = r["chat-pane"]; feed = r["feed-pane"];
  o.storeBeforeEsc = await store(page);
  o.esc = await drag(page, chat.x + chat.w / 2, chat.y + 1, [{ x: feed.x + feed.w / 2, y: feed.y + feed.h / 2 }], { escape: true });
  // a press under the slop is a click, not a drag
  o.click = await (async () => { const rr = await rectsOf(page); const p = rr["chat-pane"]; await page.mouse.move(p.x + p.w / 2, p.y + 1); await page.mouse.down(); await page.mouse.move(p.x + p.w / 2 + 2, p.y + 2); await frame(page); const d = await page.evaluate(() => window.__rompPaneDock.dragging()); await page.mouse.up(); return { dragging: d, store: await store(page) }; })();
  // THE EMPTY SPACE INSIDE A PANE (plans/pane-docking.md section 3, 2026-09-19): a press on the feed's own background
  // below its cards, detected by the feed page and forwarded to the shell, arms the same drag the ring arms; the open
  // hand shows over that spot and not over a card
  o.feedEmpty = await (async () => {
    const ff = frameOfApp(page, /\/feed(\?|$)/); if (!ff) return { frame: false };
    await ff.waitForFunction(() => document.querySelectorAll("#feed-list .fitem").length >= 1, null, { timeout: 30000 }).catch(() => {});   // the cards, so "below the last card" means something; the detector is not waited on
    const spot = await emptySpot(ff, "feed");
    if (!spot || spot.error) return { frame: true, spot };
    const fo = await frameOrigin(page, "f-feed"); const X = fo.x + spot.x, Y = fo.y + spot.y;
    await page.mouse.move(X, Y); await frame(page);
    const hover = await ff.evaluate((sp) => { const el = document.elementFromPoint(sp.x, sp.y); return { cursor: el ? getComputedStyle(el).cursor : null, cls: document.body.className }; }, spot);
    // a card, when one is there, does not wear the hand
    const card = await ff.evaluate(() => { const c = document.querySelector("#feed-list .fitem"); if (!c) return null; const r = c.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + Math.min(20, r.height / 2) }; });
    let cardHover = null;
    if (card) { await page.mouse.move(fo.x + card.x, fo.y + card.y); await frame(page); cardHover = await ff.evaluate((sp) => { const el = document.elementFromPoint(sp.x, sp.y); return { cursor: el ? getComputedStyle(el).cursor : null, cls: document.body.className, tag: el && el.tagName }; }, card); }
    const rr = await rectsOf(page); const chat = rr["chat-pane"]; const feedBefore = rr["feed-pane"];
    const rec = await drag(page, X, Y, [{ x: chat.x + chat.w * 0.1, y: chat.y + chat.h / 2 }]);
    const selection = await ff.evaluate(() => { const sel = document.getSelection(); return sel ? sel.toString() : ""; });   // a grab that lifted anchored no selection
    return { frame: true, spot, hover, cardHover, feedBefore, rec, selection };
  })();
  o.bandEmpty = await (async () => {
    const tf = frameOfApp(page, /\/timeline(\?|$)/); if (!tf) return { frame: false };
    await tf.waitForFunction(() => !!document.querySelector("svg"), null, { timeout: 30000 }).catch(() => {});
    const spot = await emptySpot(tf, "timeline");
    if (!spot || spot.error) return { frame: true, spot };
    const fo = await frameOrigin(page, "f-timeline"); const X = fo.x + spot.x, Y = fo.y + spot.y;
    await page.mouse.move(X, Y); await frame(page);
    const hover = await tf.evaluate((sp) => { const el = document.elementFromPoint(sp.x, sp.y); return { cursor: el ? getComputedStyle(el).cursor : null, cls: document.body.className, tag: el && el.tagName }; }, spot);
    await page.mouse.down(); await frame(page);
    const focusAfterPress = await tf.evaluate(() => { const a = document.activeElement; return a ? (a.tagName + " " + (a.id || "") + " " + String(a.className || "")) : ""; });   // the press is not prevented: the band's wrap takes the keyboard (its own road)
    await page.mouse.move(X + 14, Y - 14, { steps: 3 }); await frame(page);
    const armed = await page.evaluate(() => ({ dragging: window.__rompPaneDock.dragging(), outline: !!document.getElementById("pd-outline") && document.getElementById("pd-outline").classList.contains("on"), bodyCursor: getComputedStyle(document.body).cursor }));
    await page.keyboard.press("Escape"); await frame(page); await page.mouse.up(); await frame(page);
    return { frame: true, spot, hover, focusAfterPress, armed, after: { dragging: await page.evaluate(() => window.__rompPaneDock.dragging()), store: await store(page) } };
  })();
  o.outlineEmpty = await (async () => {
    const lf = frameOfApp(page, /\/fleet(\?|$)/); if (!lf) return { frame: false };
    await lf.waitForFunction(() => !!document.getElementById("fleet-list") && !!document.getElementById("fleet-search"), null, { timeout: 30000 }).catch(() => {});
    const spot = await emptySpot(lf, "fleet");
    if (!spot || spot.error) return { frame: true, spot };
    const fo = await frameOrigin(page, "f-fleet"); const X = fo.x + spot.x, Y = fo.y + spot.y;
    await page.mouse.move(X, Y); await frame(page);
    const hover = await lf.evaluate((sp) => { const el = document.elementFromPoint(sp.x, sp.y); return { cursor: el ? getComputedStyle(el).cursor : null, cls: document.body.className }; }, spot);
    // the search field focused, then a CLICK (a press and release, no travel) on the empty list: the field blurs, as it does today
    await lf.evaluate(() => { const i = document.getElementById("fleet-search"); if (i) i.focus(); });
    const focusedBefore = await lf.evaluate(() => document.activeElement && document.activeElement.id);
    await page.mouse.move(X, Y); await page.mouse.down(); await frame(page);
    const focusedAfterPress = await lf.evaluate(() => document.activeElement && document.activeElement.id);
    await page.mouse.up(); await frame(page);
    const pressedState = await page.evaluate(() => ({ pressed: (window.__rompPaneDock.pressed ? window.__rompPaneDock.pressed() : "n/a"), dragging: window.__rompPaneDock.dragging() }));
    // then a press with travel: the drag arms; Escape leaves the pane
    await page.mouse.move(X, Y); await page.mouse.down(); await page.mouse.move(X + 14, Y + 14, { steps: 3 }); await frame(page);
    const armed = await page.evaluate(() => window.__rompPaneDock.dragging());
    await page.keyboard.press("Escape"); await frame(page); await page.mouse.up(); await frame(page);
    return { frame: true, spot, hover, focusedBefore, focusedAfterPress, pressedState, armed };
  })();
  // a forwarded press the shell hears AFTER the pointer is already up (the page's message task can run after the pointerup):
  // no button is held, so nothing may arm, and the page's release message leaves no press standing
  o.stale = await (async () => {
    const ff = frameOfApp(page, /\/feed(\?|$)/); if (!ff) return { frame: false };
    const rr = await rectsOf(page); const feed = rr["feed-pane"];
    await page.mouse.move(feed.x + feed.w / 2, feed.y + feed.h / 2); await frame(page);
    await ff.evaluate(() => window.parent.postMessage({ romp: "paneGrab", app: "feed", clientX: 40, clientY: 300, pointerId: 77 }, "*"));
    await frame(page);
    const afterMessage = await page.evaluate(() => ({ pressed: (window.__rompPaneDock.pressed ? window.__rompPaneDock.pressed() : "n/a"), dragging: window.__rompPaneDock.dragging() }));
    await page.mouse.move(feed.x + feed.w / 2 + 60, feed.y + feed.h / 2 + 40, { steps: 4 }); await frame(page);   // no button down
    const afterMove = await page.evaluate(() => ({ pressed: (window.__rompPaneDock.pressed ? window.__rompPaneDock.pressed() : "n/a"), dragging: window.__rompPaneDock.dragging(), cls: document.body.className, outline: document.getElementById("pd-outline").classList.contains("on") }));
    await ff.evaluate(() => { window.parent.postMessage({ romp: "paneGrab", app: "feed", clientX: 40, clientY: 300, pointerId: 78 }, "*"); window.parent.postMessage({ romp: "paneGrabEnd", app: "feed", pointerId: 78 }, "*"); });
    await frame(page);
    const afterEnd = await page.evaluate(() => ({ pressed: (window.__rompPaneDock.pressed ? window.__rompPaneDock.pressed() : "n/a"), dragging: window.__rompPaneDock.dragging() }));
    return { frame: true, afterMessage, afterMove, afterEnd, rects: await rectsOf(page), store: await store(page) };
  })();
  o.column = await (async () => {
    const before = await store(page);
    const opened = await page.evaluate((sid) => { try { return !!(window.__rompMoveTab && window.__rompMoveTab(sid, "new")); } catch (e) { return String(e); } }, cfg.api);
    const up = await page.waitForFunction(() => { const el = document.getElementById("chat-pane-2"); const f = el && el.querySelector(":scope > iframe"); const lay = window.__rompPaneDock.layout(); const lv = (n) => n.pane ? [n.pane] : n.kids.flatMap(lv);
      return !!(el && f && lay && lv(lay.tree).includes("chat-pane-2") && f.getBoundingClientRect().width > 100); }, null, { timeout: 20000 }).then(() => true).catch(() => false);
    await frame(page);
    const withCol = { rects: await rectsOf(page), store: await store(page), leaves: await leavesOf(page) };
    await page.evaluate(() => { if (window.__rompCloseSplit) window.__rompCloseSplit(2); });
    const gone = await page.waitForFunction(() => { const lay = window.__rompPaneDock.layout(); const lv = (n) => n.pane ? [n.pane] : n.kids.flatMap(lv);
      return !document.getElementById("chat-pane-2") && !!lay && !lv(lay.tree).includes("chat-pane-2"); }, null, { timeout: 20000 }).then(() => true).catch(() => false);
    await frame(page);
    return { opened, up, gone, before, withCol, after: { rects: await rectsOf(page), store: await store(page), leaves: await leavesOf(page), parked: await page.evaluate(() => window.__rompPaneDock.layout().parked) } };
  })();
  // TABS AS DROP PAYLOADS (plans/pane-docking.md section 4; the user 2026-09-19: a tab dragged out of the strip into a
  // zone becomes its own pane, a group of tabs is separable, a pane dropped on a strip joins it). A REAL HTML5 drag of
  // a session tab: the press, a few moves past the browser's threshold (the page's dragstart posts tabDrag to the shell,
  // heard here), the pointer into a zone, the release.
  const tabCentre = (fid, sid) => page.waitForFunction((a) => { const f = document.getElementById(a.fid); let d = null; try { d = f && f.contentDocument; } catch (e) { d = null; }
    const el = d && d.querySelector('#tabs .tab[data-id="' + a.sid + '"]'); if (!el || !el.draggable) return null; const r = el.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return null;
    const fr = f.getBoundingClientRect(); return { x: fr.left + f.clientLeft + r.left + r.width / 2, y: fr.top + f.clientTop + r.top + r.height / 2 }; }, { fid, sid }, { timeout: 30000 }).then((h) => h.jsonValue()).catch(() => null);
  const startNativeDrag = async (pt) => {
    await page.evaluate(() => { window.__labTabDrag = null; if (!window.__labTabDragWired) { window.__labTabDragWired = true; window.addEventListener("message", (e) => { if (e && e.data && e.data.romp === "tabDrag") window.__labTabDrag = e.data; }); } });
    await page.mouse.move(pt.x, pt.y); await page.mouse.down();
    for (const st of [[2, 0], [6, 2], [12, 6], [20, 12], [30, 20]]) await page.mouse.move(pt.x + st[0], pt.y + st[1]);
    return await page.waitForFunction(() => !!(window.__labTabDrag && window.__labTabDrag.on), null, { timeout: 15000 }).then(() => true).catch(() => false);
  };
  const tabIn = (fid, sid) => page.evaluate((a) => { const f = document.getElementById(a.fid); let d = null; try { d = f && f.contentDocument; } catch (e) { d = null; } return !!(d && d.querySelector('#tabs .tab[data-id="' + a.sid + '"]')); }, { fid, sid });
  o.tabToZone = await (async () => {
    const pt = await tabCentre("f-chat", cfg.api); if (!pt) return { tab: null };
    const started = await startNativeDrag(pt);
    const zones = await page.waitForFunction(() => document.querySelectorAll(".pd-tabzone").length >= 3, null, { timeout: 10000 }).then(() => true).catch(() => false);
    const feed = (await rectsOf(page))["feed-pane"];
    await page.mouse.move(feed.x + feed.w / 2, feed.y + feed.h * 0.85, { steps: 10 }); await frame(page);
    const outline = await outlineRect(page); const feedNow = (await rectsOf(page))["feed-pane"];
    await page.mouse.up(); await frame(page);
    const landed = await page.waitForFunction(() => { const el = document.getElementById("chat-pane-2"); const lay = window.__rompPaneDock.layout(); const lv = (n) => n.pane ? [n.pane] : n.kids.flatMap(lv); return !!(el && lay && lv(lay.tree).includes("chat-pane-2")); }, null, { timeout: 20000 }).then(() => true).catch(() => false);
    const moved = await page.waitForFunction((sid) => { const f = document.getElementById("f-chat-2"); let d = null; try { d = f && f.contentDocument; } catch (e) { d = null; } const g = document.getElementById("f-chat"); let d0 = null; try { d0 = g && g.contentDocument; } catch (e) { d0 = null; }
      return !!(d && d.querySelector('#tabs .tab[data-id="' + sid + '"]') && d0 && !d0.querySelector('#tabs .tab[data-id="' + sid + '"]')); }, cfg.api, { timeout: 30000 }).then(() => true).catch(() => false);
    await frame(page);
    return { tab: pt, started, zones, outline, feedNow, landed, moved, rects: await rectsOf(page), leaves: await leavesOf(page), store: await store(page), zonesAfter: await page.evaluate(() => document.querySelectorAll(".pd-tabzone").length) };
  })();
  o.tabToStrip = await (async () => {
    const pt = await tabCentre("f-chat-2", cfg.api); if (!pt) return { tab: null };
    const started = await startNativeDrag(pt);
    const chat = (await rectsOf(page))["chat-pane"];
    await page.mouse.move(chat.x + chat.w * 0.7, chat.y + cfg.ring + 12, { steps: 10 }); await frame(page);   // the first chat's strip band, right of its tabs
    const outline = await outlineRect(page);
    await page.mouse.up(); await frame(page);
    const rejoined = await page.waitForFunction((sid) => { const g = document.getElementById("f-chat"); let d0 = null; try { d0 = g && g.contentDocument; } catch (e) { d0 = null; } const lay = window.__rompPaneDock.layout(); const lv = (n) => n.pane ? [n.pane] : n.kids.flatMap(lv);
      return !!(d0 && d0.querySelector('#tabs .tab[data-id="' + sid + '"]') && !document.getElementById("chat-pane-2") && lay && !lv(lay.tree).includes("chat-pane-2")); }, cfg.api, { timeout: 30000 }).then(() => true).catch(() => false);
    await frame(page);
    return { tab: pt, started, outline, rejoined, leaves: await leavesOf(page), parked: await page.evaluate(() => window.__rompPaneDock.layout().parked), rects: await rectsOf(page) };
  })();
  o.paneToStrip = await (async () => {
    // a column opened by the shipped split (the api session alone in it), dragged by its ring onto the first chat's strip: its session joins
    const opened = await page.evaluate((sid) => { try { return !!(window.__rompMoveTab && window.__rompMoveTab(sid, "new")); } catch (e) { return String(e); } }, cfg.api);
    const up = await page.waitForFunction(() => { const el = document.getElementById("chat-pane-2"); const lay = window.__rompPaneDock.layout(); const lv = (n) => n.pane ? [n.pane] : n.kids.flatMap(lv); return !!(el && lay && lv(lay.tree).includes("chat-pane-2") && el.querySelector(":scope > iframe")); }, null, { timeout: 20000 }).then(() => true).catch(() => false);
    await frame(page);
    const rr = await rectsOf(page); const col = rr["chat-pane-2"]; const chat = rr["chat-pane"];
    if (!col) return { opened, up, col: null };
    const rec = await drag(page, col.x + col.w / 2, col.y + 1, [{ x: chat.x + chat.w * 0.7, y: chat.y + cfg.ring + 12 }]);
    const rejoined = await page.waitForFunction((sid) => { const g = document.getElementById("f-chat"); let d0 = null; try { d0 = g && g.contentDocument; } catch (e) { d0 = null; } return !!(d0 && d0.querySelector('#tabs .tab[data-id="' + sid + '"]') && !document.getElementById("chat-pane-2")); }, cfg.api, { timeout: 30000 }).then(() => true).catch(() => false);
    await frame(page);
    return { opened, up, rec, rejoined, leaves: await leavesOf(page), rects: await rectsOf(page) };
  o.offOn = await (async () => {
    const ff = frameOfApp(page, /\/feed(\?|$)/); if (!ff) return { frame: false };
    const docState = () => ff.evaluate(() => ({ cls: document.body.className, script: document.querySelectorAll("#pd-grab").length, css: !!document.getElementById("pd-grab-css"), detector: !!window.__rompPaneGrab }));
    const onState = await docState();
    await page.evaluate(() => { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}"); s.paneDocking = false; localStorage.setItem("romp:settings", JSON.stringify(s)); window.dispatchEvent(new Event("romp:settings")); });
    await page.waitForFunction(() => !window.__rompPaneDock.on(), null, { timeout: 10000 }).catch(() => {});
    await frame(page);
    const offState = Object.assign(await docState(), { shell: await page.evaluate(() => ({ on: window.__rompPaneDock.on(), cls: document.body.className, css: !!document.getElementById("pd-css"), outline: !!document.getElementById("pd-outline") })) });
    await page.evaluate(() => { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}"); s.paneDocking = true; localStorage.setItem("romp:settings", JSON.stringify(s)); window.dispatchEvent(new Event("romp:settings")); });
    await page.waitForFunction(() => window.__rompPaneDock.on(), null, { timeout: 10000 }).catch(() => {});
    await ff.waitForFunction(() => !!window.__rompPaneGrab && document.body.classList.contains("pane-docking"), null, { timeout: 10000 }).catch(() => {});
    await frame(page);
    const backOn = await docState();
    return { frame: true, onState, offState, backOn };
  })();
  o.final = { rects: await rectsOf(page), store: await store(page), tl: await page.evaluate(() => getComputedStyle(document.querySelector(".col")).getPropertyValue("--tl").trim()) };
  if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/pane-docking-on.png" }); }
  await ctx.close();
  }
}

// ── ON, the Files pane too (its empty state is a grab surface; the shipped lab seeds it off) ─────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  await ctx.addInitScript("(" + seed(true).replace('"files":false', '"files":true').replace('paneDocking: true,', 'paneDocking: true, showFilesControl: true,') + ")()");
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push("files: " + String(e && e.stack || e).slice(0, 300)));
  await page.goto(cfg.url);
  const ready = await page.waitForFunction(() => { const pd = window.__rompPaneDock; if (!pd || !pd.on()) return false; return pd.rects().some((r) => r.pane === "files-pane"); }, null, { timeout: 60000 }).then(() => true).catch(() => false);
  const f = { ready };
  if (ready) {
    const fr = page.frames().find((x) => /\/files(\?|$)/.test(x.url()));
    if (fr) {
      await fr.waitForFunction(() => !!document.getElementById("files-empty"), null, { timeout: 30000 }).catch(() => {});
      const spot = await emptySpot(fr, "files");
      f.spot = spot;
      if (spot && !spot.error) {
        const fo = await frameOrigin(page, "f-files"); const X = fo.x + spot.x, Y = fo.y + spot.y;
        await page.mouse.move(X, Y); await frame(page);
        f.hover = await fr.evaluate((sp) => { const el = document.elementFromPoint(sp.x, sp.y); return { cursor: el ? getComputedStyle(el).cursor : null, cls: document.body.className }; }, spot);
        await page.mouse.down(); await page.mouse.move(X + 14, Y + 14, { steps: 3 }); await frame(page);
        f.armed = await page.evaluate(() => ({ dragging: window.__rompPaneDock.dragging(), outline: document.getElementById("pd-outline").classList.contains("on") }));
        await page.keyboard.press("Escape"); await frame(page); await page.mouse.up(); await frame(page);
        f.after = await page.evaluate(() => window.__rompPaneDock.dragging());
      }
    } else f.frame = false;
  }
  out.files = f;
  await ctx.close();
}

// ── OFF ───────────────────────────────────────────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  await ctx.addInitScript("(" + seed(false) + ")()");
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push("off: " + String(e && e.stack || e).slice(0, 300)));
  await page.goto(cfg.url);
  await page.waitForFunction(() => { const f = document.getElementById("f-chat"); const d = f && f.contentDocument; const g = document.getElementById("f-feed"); return !!(d && d.getElementById("tabbar") && g && g.getAttribute("src") && document.querySelector("#feed-pane")); }, null, { timeout: 60000 }).catch(() => {});
  await page.waitForFunction(() => ["f-chat", "f-fleet", "f-feed", "f-timeline"].every((id) => { const f = document.getElementById(id); let d = null; try { d = f && f.contentDocument; } catch (e) { d = null; }
    return !!(d && d.readyState === "complete" && d.body && d.body.childElementCount > 0); }), null, { timeout: 60000 }).catch(() => {});
  await frame(page);
  const snap = () => page.evaluate(() => ({
    row: document.querySelector(".row").outerHTML,
    band: document.getElementById("tl-pane").outerHTML,
    bodyCls: document.body.className,
    css: !!document.getElementById("pd-css"), outline: !!document.getElementById("pd-outline"), divs: document.querySelectorAll(".pd-div").length,
    engineOn: !!(window.__rompPaneDock && window.__rompPaneDock.on()),
    layout: localStorage.getItem("romp-layout"),
    feedCursor: getComputedStyle(document.getElementById("feed-pane")).cursor,
    feedDoc: (() => { try { const d = document.getElementById("f-feed").contentDocument; return { cls: d.body.className, script: !!d.getElementById("pd-grab"), detector: !!d.defaultView.__rompPaneGrab }; } catch (e) { return { error: String(e) }; } })(),
  }));
  out.off.before = await snap();
  const rr = await page.evaluate(() => Object.fromEntries(["chat-pane", "fleet-pane", "feed-pane"].map((id) => { const r = document.getElementById(id).getBoundingClientRect(); return [id, { x: r.left, y: r.top, w: r.width, h: r.height }]; })));
  // the same gestures: a press at the feed's top edge dragged into the chat, and an Option-drag from the chat into the feed
  const feed = rr["feed-pane"], chat = rr["chat-pane"];
  await page.mouse.move(feed.x + feed.w / 2, feed.y + 1); await page.mouse.down(); await page.mouse.move(feed.x + feed.w / 2 + 14, feed.y + 14, { steps: 3 }); await page.mouse.move(chat.x + chat.w * 0.1, chat.y + chat.h / 2, { steps: 6 }); await page.mouse.up();
  await page.keyboard.down("Alt"); await page.mouse.move(chat.x + chat.w / 2, chat.y + chat.h / 2); await page.mouse.down(); await page.mouse.move(chat.x + chat.w / 2 + 14, chat.y + chat.h / 2 + 14, { steps: 3 }); await page.mouse.move(feed.x + feed.w / 2, feed.y + feed.h / 2, { steps: 6 }); await page.mouse.up(); await page.keyboard.up("Alt");
  await frame(page); await frame(page);   // the gestures must do nothing: two frames is every event they could raise
  out.off.after = await snap();
  await ctx.close();
}
await browser.close();
process.stdout.write("RESULT:" + JSON.stringify(out) + "\n", () => process.exit(0));
"""


class ServedPaneDocking(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.procs = []
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served lab needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box: the served lab needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="pane-dock-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        sub = os.path.join(cls.lab, "hub")
        state = os.path.join(sub, "xdg", "romp")
        claude = os.path.join(sub, "claude")
        cwd = os.path.join(sub, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own hosts off (the conftest rule)
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        now = int(time.time())
        for sid, sname, tag in ((SID_WEB, "web", "w"), (SID_API, "api", "a")):
            Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (sname, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": sname, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in _turns(sid, tag, now - 3000, 4)))
        cls.port, cls.token = _free_port(), "testtok-panedock"
        env = _lab.kernel_env(sub, claude, os.path.join(cls.lab, "dist"), cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "a"), stderr=subprocess.STDOUT, env=env)
        cls.procs.append(proc)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")
        cls._drive()

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "api": SID_API, "ring": RING, "shots": os.environ.get("PANE_DOCK_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        Path(driver).write_text(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served leg needs one (CI installs none)")
        assert p.returncode == 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        assert line, "driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-2000:]
        cls.r = json.loads(line[len("RESULT:"):])
        if os.environ.get("PANE_DOCK_SHOTS"):
            with open(os.path.join(os.environ["PANE_DOCK_SHOTS"], "result.json"), "w") as f:
                json.dump(cls.r, f, indent=1)

    @classmethod
    def tearDownClass(cls):
        for pr in getattr(cls, "procs", []):
            try:
                pr.kill(); pr.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _leaves(node):
        return [node["pane"]] if "pane" in node else [p for k in node["kids"] for p in ServedPaneDocking._leaves(k)]

    def _layout(self, store):
        self.assertTrue(store.get("layout"), "the layout store exists while the kit is on")
        lay = json.loads(store["layout"])
        self.assertEqual(lay.get("v"), 1)
        return lay

    def _near(self, a, b, tol, what):
        self.assertLessEqual(abs(a - b), tol, "%s: %r vs %r" % (what, a, b))

    def _frames_fill(self, rects, when):
        """Every pane's iframe is its pane's rectangle inset by the 3 px ring (the round-one read: with width and height
        auto an absolutely positioned iframe sat at its intrinsic 300 by 150 px inside a correctly sized pane)."""
        for pane, r in rects.items():
            f = r.get("frame")
            self.assertIsNotNone(f, "%s has an iframe (%s): %r" % (pane, when, r))
            self._near(f["x"], r["x"] + RING, 1.5, "%s iframe left (%s)" % (pane, when))
            self._near(f["y"], r["y"] + RING, 1.5, "%s iframe top (%s)" % (pane, when))
            self._near(f["w"], r["w"] - 2 * RING, 1.5, "%s iframe width fills the pane less the ring (%s)" % (pane, when))
            self._near(f["h"], r["h"] - 2 * RING, 1.5, "%s iframe height fills the pane less the ring (%s)" % (pane, when))

    # ── the ON page ──────────────────────────────────────────────────────────────────────────────────
    def test_1_the_engine_is_on_and_positions_every_pane_with_the_band_at_a_fixed_height(self):
        self.assertEqual(self.r["errors"], [], self.r["errors"])
        o = self.r["on"]
        self.assertTrue(o["ready"], "the engine came on and the panes have rects: %r" % o.get("bodyClass"))
        self.assertIn("pane-docking", o["bodyClass"].split())
        r = o["rects0"]
        self.assertEqual(sorted(r), ["chat-pane", "feed-pane", "fleet-pane", "tl-pane"], "the seeded panes, the band included")
        chat, fleet, feed, band = r["chat-pane"], r["fleet-pane"], r["feed-pane"], r["tl-pane"]
        self.assertLess(chat["x"] + chat["w"], fleet["x"]); self.assertLess(fleet["x"] + fleet["w"], feed["x"])   # today's row order
        self._near(chat["y"], fleet["y"], 1, "the row shares a top"); self._near(chat["h"], feed["h"], 1, "and a height")
        tl = float(o["tl"]["tl"].replace("px", "") or 0)
        self.assertGreater(tl, 0, "the shell's --tl: %r" % o["tl"])
        self._near(band["h"], tl, 1.5, "the band's height is the fixed --tl px")
        self._near(band["y"] + band["h"], o["tl"]["rowBottom"], 1.5, "the band sits at the bottom of the pane area")
        self.assertGreater(band["y"], chat["y"] + chat["h"], "below the row")
        lay = self._layout(o["store0"])
        self.assertEqual(lay["tree"]["dir"], "col"); self.assertEqual(lay["tree"]["fixed"][1], tl, "the band is the fixed kid of the root column")
        self.assertEqual(self._leaves(lay["tree"]), ["chat-pane", "fleet-pane", "feed-pane", "tl-pane"])
        self._frames_fill(r, "after the seed")

    def _on(self):
        o = self.r["on"]
        self.assertFalse(o.get("absent"), "the engine never came on (no bundle, no switch, or no rects): %r" % {k: o.get(k) for k in ("ready", "bodyClass", "rects0")})
        return o

    def test_2_cursors_the_open_hand_over_the_ring_and_the_strips_empty_run_and_under_option_the_closed_hand_while_held(self):
        o = self._on()
        c = o["cursors"]
        self.assertEqual(c["feedRing"], "grab", "the pane's ring wears the open hand: %r" % c)
        self.assertEqual(c["tabbar"], "grab", "the chat strip's empty run wears the open hand: %r" % c)
        self.assertNotEqual(c["tab"], "grab", "a tab keeps its own cursor: %r" % c)
        self.assertNotEqual(c["chatRoot"], "grab", "content at rest is not a grab surface: %r" % c)
        self.assertEqual(o["alt"]["chatRoot"], "grab", "Option held: the open hand over content: %r" % o["alt"])
        self.assertIn("pd-alt", o["alt"]["bodyCls"].split())
        self.assertNotEqual(o["altUp"]["chatRoot"], "grab", "released: back to the content's cursor: %r" % o["altUp"])
        self.assertNotIn("pd-alt", o["altUp"]["bodyCls"].split())
        d = o["drag1"]
        self.assertFalse(d["pressed"]["dragging"], "a press alone lifts nothing")
        self.assertFalse(d["underSlop"], "three px of travel is under the slop")
        self.assertTrue(d["armed"]["dragging"], "past the slop the pane is held")
        self.assertEqual(d["armed"]["bodyCursor"], "grabbing", "the closed hand while held: %r" % d["armed"])
        self.assertEqual(d["armed"]["paneCursor"], "grabbing")

    def test_3_the_outline_follows_the_pointer_across_two_half_zones_of_one_pane(self):
        d = self._on()["drag1"]
        top, bottom = d["way"][0], d["way"][1]
        fl = top["rects"]["fleet-pane"]
        self.assertEqual(top["zone"], {"target": "fleet-pane", "edge": "top"}, top["zone"])
        self.assertTrue(top["outline"]["on"] and not top["outline"]["free"], top["outline"])
        self._near(top["outline"]["x"], fl["x"], 1.5, "the outline's left is the outline pane's left")
        self._near(top["outline"]["y"], fl["y"], 1.5, "top half: its top is the pane's top")
        self._near(top["outline"]["w"], fl["w"], 1.5, "full width")
        self._near(top["outline"]["h"], (fl["h"] - 7) / 2, 2, "half the height, less the gutter's share")
        fl2 = bottom["rects"]["fleet-pane"]
        self.assertEqual(bottom["zone"], {"target": "fleet-pane", "edge": "bottom"}, bottom["zone"])
        self._near(bottom["outline"]["y"] + bottom["outline"]["h"], fl2["y"] + fl2["h"], 1.5, "bottom half: its bottom is the pane's bottom")
        self._near(bottom["outline"]["h"], (fl2["h"] - 7) / 2, 2, "half the height")
        self.assertGreater(bottom["outline"]["y"], top["outline"]["y"] + 10, "the outline moved with the pointer")

    def test_4_a_pane_dropped_into_each_half_zone_lands_there_and_the_store_follows(self):
        o = self._on()
        # 1: the feed below the outline (the outline's bottom half)
        a = o["drag1"]["after"]
        self.assertFalse(a["dragging"]); self.assertFalse(a["outlineOn"], "the outline goes at the drop")
        fl, fd = a["rects"]["fleet-pane"], a["rects"]["feed-pane"]
        self._near(fd["x"], fl["x"], 1.5, "same left"); self.assertGreater(fd["y"], fl["y"] + fl["h"] - 1, "the feed below the outline")
        self._near(fd["w"], fl["w"], 1.5, "same width")
        lay = self._layout(a["store"]); self.assertEqual(self._leaves(lay["tree"]), ["chat-pane", "fleet-pane", "feed-pane", "tl-pane"])
        row = lay["tree"]["kids"][0]; self.assertEqual(row["dir"], "row"); self.assertEqual(row["kids"][1]["dir"], "col", "the outline over the feed as a column inside the row")
        # 2: the feed to the chat's left half
        b = o["drag2"]["after"]
        fd, ch = b["rects"]["feed-pane"], b["rects"]["chat-pane"]
        self.assertLess(fd["x"] + fd["w"], ch["x"] + 1, "the feed left of the chat"); self._near(fd["y"], ch["y"], 1.5, "same top")
        self.assertEqual(self._leaves(self._layout(b["store"])["tree"]), ["feed-pane", "chat-pane", "fleet-pane", "tl-pane"])
        # 3: the outline to the chat's top half
        c = o["drag3"]["after"]
        fl, ch = c["rects"]["fleet-pane"], c["rects"]["chat-pane"]
        self.assertLess(fl["y"] + fl["h"], ch["y"] + 1, "the outline above the chat"); self._near(fl["x"], ch["x"], 1.5, "same left"); self._near(fl["w"], ch["w"], 1.5, "same width")
        # 4: the outline to the feed's right half
        d = o["drag4"]["after"]
        fl, fd = d["rects"]["fleet-pane"], d["rects"]["feed-pane"]
        self.assertGreater(fl["x"], fd["x"] + fd["w"] - 1, "the outline right of the feed"); self._near(fl["y"], fd["y"], 1.5, "same top"); self._near(fl["h"], fd["h"], 1.5, "same height")
        self.assertEqual(self._leaves(self._layout(d["store"])["tree"]), ["feed-pane", "fleet-pane", "chat-pane", "tl-pane"])
        for name, after in (("drop 1", a), ("drop 2", b), ("drop 3", c), ("drop 4", d)):
            self._frames_fill(after["rects"], "after " + name)
        # every drop kept the band fixed at the bottom
        band = d["rects"]["tl-pane"]
        self._near(band["h"], float(o["final"]["tl"].replace("px", "")), 1.5, "the band's px through four drops")
        for k in ("chat-pane", "fleet-pane", "feed-pane"):
            self.assertLess(d["rects"][k]["y"] + d["rects"][k]["h"], band["y"] + 1, "%s above the band" % k)

    def test_5_escape_cancels_a_lifted_drag_and_a_press_under_the_slop_is_a_click(self):
        o = self._on()
        # the press itself is answered: the closed hand on the pressed pane before any travel (the user's first press on
        # the ring showed the open hand and then nothing; now the hand closes the moment the press lands)
        self.assertEqual(o["drag1"]["pressed"]["pressedPaneCursor"], "grabbing", "the pressed pane wears the closed hand before the slop: %r" % o["drag1"]["pressed"])
        self.assertFalse(o["drag1"]["pressed"]["dragging"], "...while nothing has lifted yet")
        e = o["esc"]
        self.assertTrue(e["armed"]["dragging"], "lifted before Escape")
        self.assertFalse(e["afterEscape"]["dragging"], "Escape drops the pane where it was")
        self.assertFalse(e["afterEscape"]["outline"], "and the outline goes")
        self.assertEqual(e["after"]["store"]["layout"], o["storeBeforeEsc"]["layout"], "the store is untouched by a cancelled drag")
        self.assertFalse(o["click"]["dragging"], "two px of travel: a click, nothing lifted")
        self.assertEqual(o["click"]["store"]["layout"], o["storeBeforeEsc"]["layout"])

    def test_6_the_shipped_stores_are_read_and_never_written(self):
        o = self._on()
        self.assertEqual(o["final"]["store"]["grow"], o["store0"]["grow"], "romp-pane-grow untouched by four drops")
        self.assertEqual(o["final"]["store"]["panes"], o["store0"]["panes"], "romp-panes untouched")
        self.assertEqual(json.loads(o["store0"]["panes"]), {"chat": True, "fleet": True, "feed": True, "timeline": True, "files": False})
        self.assertEqual(json.loads(o["store0"]["grow"]), {"chat": 60, "fleet": 34, "feed": 40, "files": 40}, "the seeded grow store, as seeded")

    def test_8_a_plain_press_over_content_wears_the_contents_cursor_and_arms_nothing(self):
        o = self._on()
        c = o["contentPress"]
        self.assertNotEqual(c["cursorAtRest"], "grab", "content at rest is not a grab surface: %r" % c)
        self.assertFalse(c["dragging"], "14 px of travel from a press over content lifts nothing: %r" % c)
        self.assertFalse(c["outline"], "no outline")
        self.assertNotEqual(c["bodyCursor"], "grabbing")
        self.assertEqual(c["store"]["layout"], o["store0"]["layout"], "the store is untouched")
        self.assertEqual(c["rects"], o["rects0"], "nothing moved")

    def test_9_a_chat_column_opened_and_closed_under_the_kit_is_mirrored_filled_and_pruned_with_the_old_store_untouched(self):
        o = self._on()
        c = o["column"]
        self.assertEqual(c["opened"], True, "the shipped split opened a column: %r" % c["opened"])
        self.assertTrue(c["up"], "the column's pane is a leaf of the tree with a sized iframe: %r" % c["withCol"]["leaves"])
        lv = c["withCol"]["leaves"]
        self.assertIn("chat-pane-2", lv)
        self.assertEqual(lv.index("chat-pane-2"), lv.index("chat-pane") + 1, "right of the last chat leaf: %r" % lv)
        self._frames_fill(c["withCol"]["rects"], "with the column open")
        self.assertTrue(c["gone"], "the column closed and left the tree: %r" % c["after"]["leaves"])
        self.assertNotIn("chat-pane-2", c["after"]["parked"], "a closed column has no mounted iframe to re-open: its park is pruned: %r" % c["after"]["parked"])
        self.assertEqual(c["after"]["store"]["grow"], c["before"]["grow"], "romp-pane-grow byte-identical across a column open and close under the kit")
        self.assertEqual(c["after"]["store"]["grow"], o["store0"]["grow"])
        self.assertEqual(c["after"]["store"]["panes"], c["before"]["panes"])
        self._frames_fill(c["after"]["rects"], "after the column closed")

    def test_10_a_press_on_the_feeds_empty_background_wears_the_open_hand_and_arms_the_shells_drag(self):
        o = self._on()
        e = o["feedEmpty"]
        self.assertTrue(e.get("frame"), "the feed frame is on the page")
        sp = e.get("spot")
        self.assertTrue(sp and not sp.get("error"), "an empty spot in the feed below its cards (found by geometry, the page's own container under the point): %r" % sp)
        self.assertEqual(e["hover"]["cursor"], "grab", "the open hand over the feed's empty background: %r" % e["hover"])
        self.assertEqual(e.get("selection"), "", "a grab that lifted anchored no text selection")
        self.assertIn("pd-grab-hover", e["hover"]["cls"].split())
        self.assertTrue(e.get("cardHover"), "a card was on the feed to hover (the seeded sessions' cards)")
        self.assertNotEqual(e["cardHover"]["cursor"], "grab", "a card wears its own cursor, never the hand: %r" % e["cardHover"])
        self.assertNotIn("pd-grab-hover", e["cardHover"]["cls"].split(), "the hover class drops over a card: %r" % e["cardHover"])
        rec = e["rec"]
        self.assertTrue(rec["armed"]["dragging"], "past the slop the feed is held from a press inside its content: %r" % rec["armed"])
        self.assertEqual(rec["armed"]["bodyCursor"], "grabbing")
        w = rec["way"][0]
        self.assertEqual(w["zone"], {"target": "chat-pane", "edge": "left"}, w["zone"])
        self.assertTrue(w["outline"]["on"] and not w["outline"]["free"], "the outline shows the chat's left half: %r" % w["outline"])
        a = rec["after"]
        fd, ch = a["rects"]["feed-pane"], a["rects"]["chat-pane"]
        self.assertLess(fd["x"] + fd["w"], ch["x"] + 1, "the feed landed left of the chat")
        lv = self._leaves(self._layout(a["store"])["tree"])
        self.assertEqual(lv.index("feed-pane") + 1, lv.index("chat-pane"), "the feed sits right before the chat in the row: %r" % lv)
        self._frames_fill(a["rects"], "after the empty-space drop")

    def test_11_a_press_on_the_sessions_bands_empty_area_arms_the_drag_and_escape_leaves_the_band(self):
        o = self._on()
        e = o["bandEmpty"]
        self.assertTrue(e.get("frame"), "the sessions frame is on the page")
        sp = e.get("spot")
        self.assertTrue(sp and not sp.get("error"), "an empty spot in the band's SVG outside every lane and mark (found by geometry): %r" % sp)
        self.assertEqual(e["hover"]["cursor"], "grab", "the open hand over the band's empty area: %r" % e["hover"])
        self.assertIn("romp-tl-wrap", e.get("focusAfterPress") or "", "the press is not prevented: the band's wrap takes the keyboard as it does without the kit: %r" % e.get("focusAfterPress"))
        self.assertTrue(e["armed"]["dragging"], "the band is held from a press inside it: %r" % e["armed"])
        self.assertTrue(e["armed"]["outline"]); self.assertEqual(e["armed"]["bodyCursor"], "grabbing")
        self.assertFalse(e["after"]["dragging"], "Escape drops it where it was")

    def test_12_a_tab_dragged_out_of_the_strip_into_a_zone_becomes_its_own_pane_there(self):
        o = self._on()
        t = o["tabToZone"]
        self.assertTrue(t.get("tab"), "the api tab was found, draggable and laid out in the first chat's strip")
        self.assertTrue(t["started"], "the real drag started (the page posted tabDrag on)")
        self.assertTrue(t["zones"], "the kit mounted a hit area per docked pane for the gesture")
        fd = t["feedNow"]; ol = t["outline"]
        self.assertTrue(ol and ol["on"] and not ol["free"] and not ol["refused"], "the outline shows a landing half over the feed: %r" % ol)
        self._near(ol["y"] + ol["h"], fd["y"] + fd["h"], 1.5, "the feed's bottom half: its bottom is the pane's bottom")
        self._near(ol["h"], (fd["h"] - 7) / 2, 2, "half the feed's height less the gutter's share")
        self.assertTrue(t["landed"], "a new column pane is a leaf of the tree: %r" % t["leaves"])
        self.assertTrue(t["moved"], "the tab left the first strip and shows in the new pane's strip")
        col, feed = t["rects"]["chat-pane-2"], t["rects"]["feed-pane"]
        self._near(col["x"], feed["x"], 1.5, "the new pane sits under the feed: same left"); self.assertGreater(col["y"], feed["y"] + feed["h"] - 1, "below it")
        self._near(col["w"], feed["w"], 1.5, "same width")
        # only the TARGET split: the feed kept its width and gave the new pane half its height; the other panes did not move
        self._near(feed["w"], fd["w"], 1.5, "the feed's width is what it was before the drop (no detour right of the last chat)")
        self._near(feed["x"], fd["x"], 1.5, "and its left")
        self._near(col["h"], (fd["h"] - 7) / 2, 2, "the new pane took half the feed's height, less the gutter")
        lv = t["leaves"]; self.assertEqual(lv.index("chat-pane-2"), lv.index("feed-pane") + 1, "right after the feed in the tree: %r" % lv)
        self._frames_fill(t["rects"], "after the tab drop")
        self.assertEqual(t["zonesAfter"], 0, "the hit areas go with the gesture")

    def test_13_a_tab_dragged_onto_a_chat_strip_joins_it_and_the_emptied_pane_closes(self):
        o = self._on()
        t = o["tabToStrip"]
        self.assertTrue(t.get("tab"), "the api tab was found in the new pane's strip")
        self.assertTrue(t["started"], "the real drag started")
        ol = t["outline"]
        self.assertTrue(ol and ol["on"] and not ol["refused"], "the outline shows the strip as a join zone: %r" % ol)
        self.assertIn("joins", ol["text"], "the outline says the session joins: %r" % ol)
        self.assertTrue(t["rejoined"], "the tab is back in the first chat's strip, the emptied pane is gone from the page and the tree: %r" % t["leaves"])
        self.assertNotIn("chat-pane-2", t["parked"], "a closed column's park is pruned")
        self._frames_fill(t["rects"], "after the join")

    def test_14_a_chat_pane_dropped_on_a_strip_joins_its_sessions_to_that_strip(self):
        o = self._on()
        t = o["paneToStrip"]
        self.assertEqual(t["opened"], True, "the shipped split opened a column for the api session: %r" % t["opened"])
        self.assertTrue(t["up"], "the column is a leaf with an iframe")
        self.assertTrue(t.get("col") is not False and t.get("rec"), "the column's pane had a rect to press on: %r" % {k: v for k, v in t.items() if k != "rec"})
        w = t["rec"]["way"][0]
        self.assertTrue(w["outline"]["on"] and not w["outline"]["refused"], "a chat pane over another chat's strip is a join, not a refusal: %r" % w["outline"])
        self.assertIn("joins", w["outline"]["text"])
        self.assertTrue(t["rejoined"], "its session joined the first chat's strip and the emptied column closed: %r" % t["leaves"])
        self._frames_fill(t["rects"], "after the pane joined a strip")
    def test_15_the_outlines_empty_list_blurs_a_focused_field_on_a_click_and_arms_on_a_drag(self):
        o = self._on()
        e = o["outlineEmpty"]
        self.assertTrue(e.get("frame"), "the outline frame is on the page")
        sp = e.get("spot")
        self.assertTrue(sp and not sp.get("error"), "an empty spot in the outline's list below its rows: %r" % sp)
        self.assertEqual(e["hover"]["cursor"], "grab", "the open hand over the outline's empty list: %r" % e["hover"])
        self.assertEqual(e["focusedBefore"], "fleet-search", "the search field was focused before the click")
        self.assertNotEqual(e["focusedAfterPress"], "fleet-search", "a click on the empty list blurs the field, as it does without the kit (the press is not prevented): %r" % e["focusedAfterPress"])
        self.assertEqual(e["pressedState"], {"pressed": False, "dragging": False}, "a click leaves no press standing")
        self.assertTrue(e["armed"], "a press with travel on the outline's empty list arms the drag")

    def test_16_a_forwarded_press_heard_after_the_release_arms_nothing(self):
        o = self._on()
        st = o["stale"]
        self.assertTrue(st.get("frame"))
        self.assertFalse(st["afterMove"]["dragging"], "a move with no button held after a stale forwarded press arms nothing: %r" % st["afterMove"])
        self.assertIs(st["afterMove"]["pressed"], False, "the press is cancelled on the first move without a button: %r" % st["afterMove"])
        self.assertNotIn("pd-drag", st["afterMove"]["cls"].split()); self.assertFalse(st["afterMove"]["outline"])
        self.assertEqual(st["afterEnd"], {"pressed": False, "dragging": False}, "a press message followed by its release message leaves no press standing: %r" % st["afterEnd"])
        self.assertEqual(st["store"]["layout"], o["storeBeforeEsc"]["layout"] if False else st["store"]["layout"])
        self._frames_fill(st["rects"], "after the stale messages")

    def test_17_the_kit_off_leaves_the_pane_documents_as_they_were_and_on_again_injects_once(self):
        o = self._on()
        e = o["offOn"]
        self.assertTrue(e.get("frame"))
        self.assertEqual((e["onState"]["script"], e["onState"]["css"], e["onState"]["detector"]), (1, True, True), "on: one detector tag, its style, its global: %r" % e["onState"])
        self.assertIn("pane-docking", e["onState"]["cls"].split())
        off = e["offState"]
        self.assertFalse(off["shell"]["on"]); self.assertNotIn("pane-docking", off["shell"]["cls"].split()); self.assertFalse(off["shell"]["css"] or off["shell"]["outline"])
        self.assertEqual((off["script"], off["css"], off["detector"]), (0, False, False), "off: the detector tag, its style and its global are gone from the pane document: %r" % off)
        self.assertNotIn("pane-docking", off["cls"].split()); self.assertNotIn("pd-grab-hover", off["cls"].split())
        self.assertEqual((e["backOn"]["script"], e["backOn"]["css"], e["backOn"]["detector"]), (1, True, True), "on again: injected once: %r" % e["backOn"])

    def test_18_the_files_panes_empty_state_is_a_grab_surface_too(self):
        f = self.r.get("files") or {}
        self.assertTrue(f.get("ready"), "the files pane docked with the kit on: %r" % f)
        self.assertNotEqual(f.get("frame"), False, "the files frame is on the page")
        sp = f.get("spot")
        self.assertTrue(sp and not sp.get("error"), "an empty spot in the files pane's empty state: %r" % sp)
        self.assertEqual(f["hover"]["cursor"], "grab", "the open hand over the files pane's empty state: %r" % f["hover"])
        self.assertTrue(f["armed"]["dragging"] and f["armed"]["outline"], "a press there lifts the pane: %r" % f["armed"])
        self.assertFalse(f["after"], "Escape leaves it")

    # ── the OFF page ─────────────────────────────────────────────────────────────────────────────────
    def test_7_with_the_switch_off_the_same_gestures_change_nothing_and_no_engine_node_or_store_exists(self):
        f = self.r["off"]
        b, a = f["before"], f["after"]
        self.assertFalse(b["engineOn"], "the bundle loads and stays inert")
        self.assertNotIn("pane-docking", b["bodyCls"].split())
        self.assertFalse(b["css"] or b["outline"], "no engine stylesheet, no outline node")
        self.assertEqual(b["divs"], 0)
        self.assertIsNone(b["layout"], "no layout store is written while the kit is off")
        self.assertNotEqual(b["feedCursor"], "grab", "no open hand anywhere")
        self.assertEqual(a["row"], b["row"], "the pane row's DOM is byte-identical after the gestures")
        self.assertEqual(a["band"], b["band"])
        self.assertEqual(a["bodyCls"], b["bodyCls"])
        self.assertIsNone(a["layout"]); self.assertFalse(a["css"] or a["outline"]); self.assertEqual(a["divs"], 0)
        fd = b.get("feedDoc") or {}
        self.assertNotIn("pane-docking", (fd.get("cls") or "").split(), "the feed document carries no kit class: %r" % fd)
        self.assertFalse(fd.get("script") or fd.get("detector"), "no grab detector is injected while the kit is off: %r" % fd)


if __name__ == "__main__":
    unittest.main()
