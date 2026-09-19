#!/usr/bin/env python3
"""The settings card's tabs RE-CUT (T400, the user 2026-09-12): General (the account, the panes, the keyboard shortcuts), Chat,
Feed, Sessions, Task tracking (Automatic renamed, the judges under it), Appearance, Debug (updates, the judges' debug views, the
token usage analytics, the log, the version; System dissolved into it). On the served dashboard: the pills and their order, each
moved row's new home (the pane that holds its id), the remembered tab round-tripping under the new names (an older remembered
automatic or system comes up on Task tracking or Debug, never a blank card, and is rewritten to the new name), and the General
and Debug tabs shot in both themes for the user's look.

SETTINGS_TABS_DIST=<dir> serves another tree's UI bundle (the red run's before); SETTINGS_TABS_SHOTS=<prefix> writes
<prefix>-general-<theme>.png and <prefix>-debug-<theme>.png; SETTINGS_TABS_DUMP=<path> writes the whole measurement. Skips LOUDLY
without the extension deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a
failure). Synthetic throughout: placeholder sids, TESTHOST, invented text.
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

NAMES = ["web", "api"]
SIDS = {n: "%s-1111-2222-3333-444444444444" % (chr(ord("a") + i) * 8) for i, n in enumerate(NAMES)}
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff")]


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
const MOVED = ["rs-billing", "rs-login-btn", "rs-panes-sec", "rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-keys-web", "rs-filesctl", "rs-theme", "rs-cmap", "rs-pal", "rs-fileedit", "rs-conserve", "rs-updates",
               "rs-compact", "rs-chatscheme", "rs-striprows", "rs-cmtmodel", "rs-thinksum", "rs-widgets", "rs-feedcollapsed", "rs-defaultdir", "rs-backend", "rs-autonudge", "rs-suggestcompact", "rs-tasktrack", "rs-judgemodel", "rs-judgeconc",
               "rs-judges-index", "rs-judges-triage", "ra-open", "rs-log-open", "rsver", "rs-filelink", "rs-activeonly", "rs-collapsegaps"];   // the last three must be GONE (T404)
// open the landing in a fresh context, seeded with a remembered tab when given; hand back the settings frame once the panel is up
async function openPanel(seedTab, ask) {
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  if (seedTab) await ctx.addInitScript((t) => { try { localStorage.setItem("romp:settingsTab", t); } catch (e) {} }, seedTab);
  const page = await ctx.newPage();
  await page.goto(cfg.url);
  await page.waitForSelector("#rail-gear", { timeout: 20000 });
  let chatF = page.frames().find((f) => f.url().includes("/chat"));
  for (let i = 0; i < 100 && !chatF; i++) { await page.waitForTimeout(100); chatF = page.frames().find((f) => f.url().includes("/chat")); }
  await chatF.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  if (ask) await page.evaluate((t) => window.__rompOpenSettings(t), ask); else await page.click("#rail-gear");   // the rail's gear: a plain open, the remembered tab
  await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  let setF = page.frames().find((f) => f.url().includes("/settings"));
  for (let i = 0; i < 50 && !setF; i++) { await page.waitForTimeout(100); setF = page.frames().find((f) => f.url().includes("/settings")); }
  if (!setF) return { ctx, page, setF: null };
  await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 }).catch(() => {});
  await setF.evaluate(() => (document.fonts && document.fonts.ready) || null).catch(() => {});
  await setF.waitForTimeout(250);
  return { ctx, page, setF };
}
const readPanel = (setF) => setF.evaluate((moved) => {
  const p = document.getElementById("rsettings");
  if (!p || p.hidden) return { open: false };
  const pills = Array.from(document.querySelectorAll("#rsettings .rs-tab")).map((b) => ({ tab: b.dataset.tab, text: b.textContent, on: b.classList.contains("on") }));
  const shown = Array.from(document.querySelectorAll("#rsettings .rs-pane")).filter((pn) => getComputedStyle(pn).display !== "none").map((pn) => pn.dataset.pane);
  const homes = {}; for (const id of moved) { const el = document.getElementById(id); homes[id] = el ? ((el.closest(".rs-pane") || {}).dataset || {}).pane || null : "missing"; }
  const heads = {}; for (const pn of document.querySelectorAll("#rsettings .rs-pane")) heads[pn.dataset.pane] = Array.from(pn.querySelectorAll(".rs-sec")).map((h) => h.textContent.trim());
  // the pill bar: one row when every pill shares the first pill's top; its width from the first pill's left edge to the last one's right
  const bar = document.getElementById("rs-tabs"); const rects = Array.from(bar.querySelectorAll(".rs-tab")).map((x) => x.getBoundingClientRect());
  const barInfo = { h: bar.getBoundingClientRect().height, rows: new Set(rects.map((r) => Math.round(r.top))).size, width: Math.round((rects[rects.length - 1].right - rects[0].left) * 10) / 10, available: Math.round(bar.getBoundingClientRect().width * 10) / 10, theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
  return { open: true, pills, shown, homes, heads, bar: barInfo, remembered: localStorage.getItem("romp:settingsTab") };
}, MOVED);
const out = {};
// 1. the glyph's ask for General: the pills, the homes, the heads; then the Debug pill; screenshots of both in both themes
{
  const { ctx, page, setF } = await openPanel(null, "general");
  if (!setF) { out.general = { open: false }; } else {
    out.general = await readPanel(setF);
    const shot = async (tab, theme) => {
      await setF.click('#rsettings .rs-tab[data-tab="' + tab + '"]'); await setF.waitForTimeout(150);
      for (const f of [page, setF]) await f.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
      await page.waitForTimeout(200);
      if (!cfg.shots) return;
      const card = await setF.evaluate(() => { const b = document.querySelector("#rsettings .rs-card").getBoundingClientRect(); return { x: b.left, y: b.top, width: b.width, height: b.height }; });
      const fr = await page.evaluate(() => { const f = document.getElementById("f-settings").getBoundingClientRect(); return { x: f.left, y: f.top }; });
      await page.screenshot({ path: cfg.shots + "-" + tab + "-" + theme + ".png", clip: { x: fr.x + card.x, y: fr.y + card.y, width: card.width, height: card.height } });
    };
    out.bar = {};
    // T407 / T408: the Panes rows' labels; the Automation rows' permanent lines (the Nudges and the Model rows) and the card's scroll box at rest and with the
    // pointer on the last Nudges row (a hover tooltip there ran past the card and scrolled it), in both themes, with a shot of the pane
    const readAutomation = () => setF.evaluate(() => {
      const card = document.querySelector("#rsettings .rs-card");
      const rows = ["rs-autonudge", "rs-suggestcompact", "rs-alwaysfast", "rs-retryupgrade"].map((id) => { const el = document.getElementById(id), row = el && el.closest("label"); const line = row && row.querySelector(".rs-line");
        return { id, label: row ? row.querySelector("b").textContent : null, line: line ? line.textContent : null, lineShown: !!line && getComputedStyle(line).display !== "none" && line.getBoundingClientRect().height > 0,
                 lineBelowLabel: !!line && line.getBoundingClientRect().top >= row.querySelector("b").getBoundingClientRect().bottom - 1,
                 hasSub: !!(row && row.querySelector(".rs-sub")), title: row ? row.getAttribute("title") : null, inputTitle: el ? el.getAttribute("title") : null }; });
      return { rows, card: { scrollHeight: card.scrollHeight, clientHeight: card.clientHeight, scrollable: card.scrollHeight > card.clientHeight + 1 } };
    });
    out.automationRows = {};
    for (const theme of ["dark", "light"]) {
      await shot("general", theme); out.bar[theme] = (await readPanel(setF)).bar; await shot("chat", theme); await shot("debug", theme);
      await shot("automation", theme);
      const rest = await readAutomation();
      await setF.hover("#rs-suggestcompact"); await setF.waitForTimeout(150);
      const hover = await readAutomation();
      await page.mouse.move(2, 2); await setF.waitForTimeout(100);   // the pointer off the rows (the mouse is the page's, not the frame's)
      out.automationRows[theme] = { rest, hoverCard: hover.card, theme };
    }
    out.panesLabels = await setF.evaluate(() => ["rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-filesctl"].map((id) => { const el = document.getElementById(id), row = el && el.closest("label"); return row ? row.querySelector("b").textContent : null; }));
    out.panesRowClasses = await setF.evaluate(() => ["rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-filesctl"].map((id) => { const el = document.getElementById(id), row = el && el.closest("label"); return row ? row.className : null; }));
    // the popover stays inside the card (the T408 read): four rows near their pane's bottom sent it past the card's edge, which grew a
    // scrollbar for it and clipped it; hovered, each must leave the card unscrollable with the popover inside the card's rect
    const hoverRow = async (tab, id) => {
      await setF.click('#rsettings .rs-tab[data-tab="' + tab + '"]'); await setF.waitForTimeout(120);
      await page.mouse.move(4, 4); await setF.waitForTimeout(60);
      await setF.hover(".rs-row:has(#" + id + "), label:has(#" + id + ")"); await setF.waitForTimeout(160);   // the ROW: two of the four controls are hidden selects
      const r = await setF.evaluate((rid) => { const el = document.getElementById(rid), row = el.closest(".rs-row") || el.closest("label") || el, card = document.querySelector("#rsettings .rs-card");
        const sub = row.querySelector(".rs-sub"); const sr = sub ? sub.getBoundingClientRect() : null, cr = card.getBoundingClientRect();
        return { id: rid, shown: !!sr && sr.height > 0, subTop: sr ? sr.top : null, subBottom: sr ? sr.bottom : null, cardTop: cr.top, cardBottom: cr.bottom, inside: !!sr && sr.top >= cr.top - 1 && sr.bottom <= cr.bottom + 1,
                 up: row.classList.contains("rs-up"), card: { scrollHeight: card.scrollHeight, clientHeight: card.clientHeight, scrollable: card.scrollHeight > card.clientHeight + 1 } }; }, id);
      await page.mouse.move(4, 4); await setF.waitForTimeout(60);
      return r;
    };
    out.hoverRows = {};
    for (const theme of ["dark", "light"]) {
      for (const f of [page, setF]) await f.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
      await page.waitForTimeout(150);
      out.hoverRows[theme] = [];
      for (const [tab, id] of [["feed", "rs-feedcollapsed"], ["sessions", "rs-backend"], ["tasks", "rs-indexeffort"], ["tasks", "rs-judgeconc"]]) out.hoverRows[theme].push(await hoverRow(tab, id));
    }
    for (const f of [page, setF]) await f.evaluate(() => document.body.classList.remove("theme-light"));
    // the Fast mode boxes (round two, the medium): their popover is the box's own, nested in the judge row; at a short window it ran
    // past the card's bottom because the rule read the row's hidden popover and returned early; the box carries rs-up itself now
    await page.setViewportSize({ width: 1200, height: 380 }); await page.waitForTimeout(200);
    await setF.click('#rsettings .rs-tab[data-tab="tasks"]'); await setF.waitForTimeout(150);
    out.fastBoxes = [];
    for (const id of ["rs-judgefast-wrap", "rs-distillfast-wrap", "rs-indexfast-wrap"]) {
      await page.mouse.move(4, 4); await setF.waitForTimeout(60);
      await setF.evaluate(() => { document.querySelector("#rsettings .rs-card").scrollTop = 0; }); await setF.waitForTimeout(80);   // the pane's natural scroll, where the read measured (centring a box gives it room below)
      const rest = await setF.evaluate(() => document.querySelector("#rsettings .rs-card").scrollHeight);
      await setF.hover("#" + id); await setF.waitForTimeout(160);
      const r = await setF.evaluate((i) => { const box = document.getElementById(i), sub = box.querySelector(".rs-sub"), card = document.querySelector("#rsettings .rs-card");
        const sr = sub.getBoundingClientRect(), cr = card.getBoundingClientRect(), br = box.getBoundingClientRect();
        return { id: i, shown: sr.height > 0, subTop: sr.top, subBottom: sr.bottom, cardTop: cr.top, cardBottom: cr.bottom, inside: sr.height > 0 && sr.top >= cr.top - 1 && sr.bottom <= cr.bottom + 1,
                 up: box.classList.contains("rs-up"), roomAbove: br.top - cr.top, roomBelow: cr.bottom - br.bottom, scrollHeight: card.scrollHeight, viewport: window.innerHeight }; }, id);
      r.scrollHeightRest = rest; out.fastBoxes.push(r);
    }
    // no room on either side (round three, the ruling): a 300 px window and the Thinking summaries popover, more room above than
    // below and neither enough; the popover stays BELOW, where the card's scroll reaches the clip (a top clip cannot be reached)
    await page.mouse.move(4, 4);
    await page.setViewportSize({ width: 1200, height: 300 }); await page.waitForTimeout(200);
    await setF.click('#rsettings .rs-tab[data-tab="chat"]'); await setF.waitForTimeout(150);
    await setF.evaluate(() => document.getElementById("rs-thinksum").closest("label").scrollIntoView({ block: "center" })); await setF.waitForTimeout(80);
    // the case wants MORE room above than below: a centred row sits within a pixel of even, and the Chat tab grew below this row
    // (the Status line section, T409) enough to tip it; two pixels of scroll back keep the premise the assertions state
    await setF.evaluate(() => { document.querySelector("#rsettings .rs-card").scrollTop -= 2; }); await setF.waitForTimeout(60);
    await setF.hover("label:has(#rs-thinksum)"); await setF.waitForTimeout(160);
    out.noRoom = await setF.evaluate(() => { const row = document.getElementById("rs-thinksum").closest("label"), sub = row.querySelector(".rs-sub"), card = document.querySelector("#rsettings .rs-card");
      const sr = sub.getBoundingClientRect(), cr = card.getBoundingClientRect(), rr = row.getBoundingClientRect();
      return { subHeight: sr.height, roomAbove: rr.top - cr.top, roomBelow: cr.bottom - rr.bottom, up: row.classList.contains("rs-up"), subTop: sr.top, subBottom: sr.bottom, cardTop: cr.top, cardBottom: cr.bottom, viewport: window.innerHeight }; });
    await page.mouse.move(4, 4);
    await page.setViewportSize({ width: 1200, height: 800 }); await page.waitForTimeout(200);
    // the off-dashboard hide's outcome (round two, low 4): the selector the hide uses takes the four Panes rows and their head; hidden,
    // each reads display none and height 0; shown again, display flex (its trigger is the VS Code host, ownPage false, not this page)
    out.panesHide = await setF.evaluate(() => {
      const els = Array.from(document.querySelectorAll("#rs-panes-sec,.rs-panes-row"));
      const rows = ["rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-filesctl"].map((id) => document.getElementById(id).closest("label"));
      const covered = rows.every((r) => els.includes(r));
      els.forEach((el) => { el.hidden = true; });
      const hidden = rows.map((r) => ({ display: getComputedStyle(r).display, height: r.getBoundingClientRect().height }));
      els.forEach((el) => { el.hidden = false; });
      const shown = rows.map((r) => getComputedStyle(r).display);
      return { count: els.length, covered, hidden, shown };
    });
    await setF.click('#rsettings .rs-tab[data-tab="debug"]'); await setF.waitForTimeout(150);
    out.debug = await readPanel(setF);
    await setF.click('#rsettings .rs-tab[data-tab="tasks"]'); await setF.waitForTimeout(150);
    out.tasks = await readPanel(setF);
    await setF.click('#rsettings .rs-tab[data-tab="automation"]'); await setF.waitForTimeout(150);
    out.automation = await readPanel(setF);
  }
  await page.close(); await ctx.close();
}
// 2. the remembered tab under the OLD names: a browser that last used Automatic or System comes up on Task tracking or Debug, and the store is rewritten
out.mapping = {};
for (const old of ["automatic", "system", "tabs", "appearance"]) {
  const { ctx, page, setF } = await openPanel(old, null);
  out.mapping[old] = setF ? await readPanel(setF) : { open: false };
  await page.close(); await ctx.close();
}
// 3. the Token usage panel's close returns to the card (the T409 tidy's read, a pre-existing gap): before, its close button,
// its backdrop and its Escape only hid the analytics layer while the card stayed hidden and nothing posted settings off, so
// the shell's transparent full-window frame kept covering the page: Escape dead (the shell asks the page, which answers
// no on a hidden card), every click landing on the invisible frame, only the palette or a reload recovering
{
  const { ctx, page, setF } = await openPanel(null, "debug");   // the Diagnostics section with the Token usage button is on the Debug pane
  let chatF = page.frames().find((f) => f.url().includes("/chat"));
  const shellRead = () => page.evaluate(() => { const f = document.getElementById("f-chat").getBoundingClientRect(); const el = document.elementFromPoint(f.left + f.width / 2, f.top + f.height / 2);
    return { settingsOpen: document.body.classList.contains("settings-open"), hit: el ? el.tagName + "#" + el.id : null }; });
  const frameRead = () => setF.evaluate(() => ({ cardHidden: document.getElementById("rsettings").hidden, backShown: !document.getElementById("ranalytics-back").hidden }));
  await setF.click("#ra-open");
  await setF.waitForFunction(() => !document.getElementById("ranalytics-back").hidden, null, { timeout: 5000 });
  await setF.waitForTimeout(200);
  const mid = { frame: await frameRead(), shell: await shellRead() };
  await setF.click("#ra-close"); await setF.waitForTimeout(250);
  const afterClose = { frame: await frameRead(), shell: await shellRead() };
  await page.keyboard.press("Escape"); await page.waitForTimeout(300);
  const afterEsc = { frame: await frameRead(), shell: await shellRead() };
  // a click that must land: another tab in the chat frame's strip becomes the active one
  const tgt = await chatF.evaluate(() => { const tabs = Array.from(document.querySelectorAll("#tabs .tab[data-id]")); const t = tabs.find((x) => !x.classList.contains("active")) || tabs[0];
    const b = t.getBoundingClientRect(); return { id: t.dataset.id, x: b.left + b.width / 2, y: b.top + b.height / 2, activeBefore: (document.querySelector("#tabs .tab.active") || {}).dataset ? document.querySelector("#tabs .tab.active").dataset.id : null }; });
  const fr = await page.evaluate(() => { const f = document.getElementById("f-chat").getBoundingClientRect(); return { x: f.left, y: f.top }; });
  await page.mouse.click(fr.x + tgt.x, fr.y + tgt.y); await page.waitForTimeout(500);
  const activeAfter = await chatF.evaluate(() => { const a = document.querySelector("#tabs .tab.active"); return a ? a.dataset.id : null; });
  out.tokenUsageClose = { mid, afterClose, afterEsc, click: { target: tgt.id, activeBefore: tgt.activeBefore, activeAfter, shell: await shellRead() } };
  // 3b. the panel's other roads (the read of that fix queued them, pre-existing): the shell's Escape chain had no entry for
  // the layer, so with the keyboard in the SHELL document Escape did nothing for the panel; nothing reset the layer, so an
  // open through the shell while the panel was up showed the card UNDER the layer; and the scene covered the close button only
  await page.evaluate(() => window.__rompOpenSettings("debug"));
  await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 10000 });
  await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 10000 }); await setF.waitForTimeout(200);
  // between roads: a known state whatever a road did at the base (the button road, which held there, takes a layer left up;
  // the card is reopened on the Debug pane, where the Token usage button lives)
  const reset = async () => {
    await setF.evaluate(() => { const b = document.getElementById("ranalytics-back"); if (b && !b.hidden) document.getElementById("ra-close").click(); });
    const open = await page.evaluate(() => document.body.classList.contains("settings-open"));
    const cardHidden = await setF.evaluate(() => document.getElementById("rsettings").hidden);
    if (!open || cardHidden) { await page.evaluate(() => window.__rompOpenSettings("debug")); await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 10000 }); }
    await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 10000 });
    await setF.click('#rsettings .rs-tab[data-tab="debug"]'); await setF.waitForTimeout(200);
  };
  const raUp = async () => { await reset(); await setF.click("#ra-open"); await setF.waitForFunction(() => !document.getElementById("ranalytics-back").hidden, null, { timeout: 5000 }); await setF.waitForTimeout(150); };
  // (a) the keyboard in the shell document: Escape reaches the panel through the shell's chain, one level (the layer down, the card back)
  await raUp();
  await page.evaluate(() => { const a = document.activeElement; if (a && a.blur) a.blur(); document.body.focus(); });
  await page.keyboard.press("Escape"); await page.waitForTimeout(300);
  const shellEsc = { frame: await frameRead(), shell: await shellRead() };
  // (b) an open through the shell while the panel is up: the card, never under the layer
  await raUp();
  await page.evaluate(() => window.__rompOpenSettings("general")); await page.waitForTimeout(300);
  const openUnder = { frame: await frameRead(), shell: await shellRead() };
  // (c) the backdrop click, and the panel's own Escape with the keyboard in the frame, then the next Escape closing the settings
  await raUp();
  await setF.click("#ranalytics-back", { position: { x: 4, y: 4 } }); await setF.waitForTimeout(250);
  const backdrop = { frame: await frameRead(), shell: await shellRead() };
  await raUp();
  await setF.focus("#ra-close"); await page.keyboard.press("Escape"); await page.waitForTimeout(300);
  const frameEsc = { frame: await frameRead(), shell: await shellRead() };
  await page.keyboard.press("Escape"); await page.waitForTimeout(300);
  const secondEsc = await shellRead();
  out.tokenUsageRoads = { shellEsc, openUnder, backdrop, frameEsc, secondEsc };
  await page.close(); await ctx.close();
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
console.log("RESULT: ok");
await browser.close();
"""


class ServedSettingsTabs(unittest.TestCase):
    maxDiff = None
    result = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="settings-tabs-")
        before = os.environ.get("SETTINGS_TABS_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own session-hosts off (the conftest rule)
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
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "what does the %s session do in notes-api?" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "It keeps the %s side of the notes-api tidy." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-tabwidgets"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.state = state
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
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.terminate()
            try:
                k.wait(timeout=10)
            except subprocess.TimeoutExpired:
                k.kill(); k.wait()
            time.sleep(0.5)
        lab = getattr(cls, "lab", "")
        shutil.rmtree(lab, ignore_errors=True)
        time.sleep(0.3)
        shutil.rmtree(lab, ignore_errors=True)

    @classmethod
    def _run(cls):
        if cls.result is not None:
            if isinstance(cls.result, BaseException):
                raise cls.result
            return cls.result
        try:
            cls.result = cls._drive()
        except BaseException as e:
            cls.result = e
            raise
        return cls.result

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": out, "sidWeb": SIDS["web"],
                       "shots": os.environ.get("SETTINGS_TABS_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        result = json.loads(Path(out).read_text())
        if os.environ.get("SETTINGS_TABS_DUMP"):
            Path(os.environ["SETTINGS_TABS_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def test_the_token_usage_panels_close_returns_to_the_card_so_escape_closes_the_settings_and_the_next_click_lands(self):
        r = self._run()["tokenUsageClose"]; t = "\n  " + json.dumps(r)
        self.assertEqual((r["mid"]["frame"]["cardHidden"], r["mid"]["frame"]["backShown"], r["mid"]["shell"]["settingsOpen"]), (True, True, True), "the Token usage panel up over the hidden card, the shell's overlay lifted" + t)
        self.assertEqual((r["afterClose"]["frame"]["cardHidden"], r["afterClose"]["frame"]["backShown"]), (False, False), "its close returns to the card: the card shows, the analytics layer is down" + t)
        self.assertTrue(r["afterClose"]["shell"]["settingsOpen"], "the overlay legitimately stays while the card shows" + t)
        self.assertFalse(r["afterEsc"]["shell"]["settingsOpen"], "Escape then closes the settings by the normal road: the shell released the overlay" + t)
        self.assertEqual(r["afterEsc"]["shell"]["hit"], "IFRAME#f-chat", "the chat frame is what a pointer meets at the page's centre, not the settings frame" + t)
        self.assertEqual(r["click"]["activeAfter"], r["click"]["target"], "the next click landed: the tab it hit is the active one" + t)
        self.assertNotEqual(r["click"]["activeAfter"], r["click"]["activeBefore"], "and it was a change" + t)

    def test_the_token_usage_panels_other_roads_the_shells_escape_an_open_while_it_is_up_the_backdrop_and_its_own_escape(self):
        r = self._run()["tokenUsageRoads"]; t = "\n  " + json.dumps(r)
        a = r["shellEsc"]
        self.assertEqual((a["frame"]["backShown"], a["frame"]["cardHidden"], a["shell"]["settingsOpen"]), (False, False, True), "with the keyboard in the shell document, one Escape takes the panel down and returns to the card: the shell's chain asks the page, which answers for the layer" + t)
        b = r["openUnder"]
        self.assertEqual((b["frame"]["backShown"], b["frame"]["cardHidden"]), (False, False), "an open through the shell while the panel is up lands on the card, never under the layer" + t)
        c = r["backdrop"]
        self.assertEqual((c["frame"]["backShown"], c["frame"]["cardHidden"]), (False, False), "the backdrop click returns to the card" + t)
        d = r["frameEsc"]
        self.assertEqual((d["frame"]["backShown"], d["frame"]["cardHidden"], d["shell"]["settingsOpen"]), (False, False, True), "the panel's Escape with the keyboard in the frame returns to the card, one level" + t)
        self.assertFalse(r["secondEsc"]["settingsOpen"], "the next Escape closes the settings" + t)
        self.assertEqual(r["secondEsc"]["hit"], "IFRAME#f-chat", "and the chat frame is under the page's centre again" + t)

    def test_the_pills_read_general_chat_feed_sessions_automation_task_tracking_debug_in_that_order(self):
        # T404 (the user 2026-09-13): Automation is new, Appearance folded into General
        g = self._run()["general"]
        table = "\n  " + json.dumps(g)[:1500]
        self.assertTrue(g["open"], table)
        self.assertEqual([x["tab"] for x in g["pills"]], ["general", "chat", "feed", "sessions", "automation", "tasks", "debug"], table)
        self.assertEqual([x["text"] for x in g["pills"]], ["General", "Chat", "Feed", "Sessions", "Automation", "Task tracking", "Debug"], table)
        self.assertEqual(g["shown"], ["general"], "the ask for General shows General alone" + table)
        self.assertEqual(g["heads"]["general"], ["Account", "Panes", "Appearance", "Permissions", "This machine", "Keyboard shortcuts"], table)
        self.assertEqual(g["heads"]["chat"], ["Display", "Comments", "Thinking", "Chat history", "Tab strip", "Tab widgets", "Status line"], "Transcript is Display; the text scheme and the strip row joined it; Thinking creates, so it is Chat's; the Status line section follows Tab widgets (T409)" + table)
        self.assertEqual(g["heads"]["debug"], ["Judging bands", "Diagnostics"], "Updates went to General" + table)
        self.assertEqual(g["heads"]["tasks"], ["Task tracking", "Judges"], "the master switch, then the judges (T404 PR 2)" + table)
        self.assertEqual(g["heads"]["automation"], ["Nudges", "Model"], "the two model switches sit after the Nudges: kernel policies applied to sessions on the kernel's own initiative (2026-09-17)" + table)
        self.assertEqual(g["heads"]["feed"], ["Cards"], table)
        self.assertEqual(g["heads"]["sessions"], ["New sessions"], "the Sessions-pane rows left settings: the pane carries them" + table)

    def test_the_files_row_reads_files_like_the_pane_rows_above_it(self):
        # T407 (the user 2026-09-13, a screenshot of the Panes section): the row read "Files control in the dashboard bar"
        r = self._run(); table = "\n  " + json.dumps(r.get("panesLabels"))
        self.assertEqual(r.get("panesLabels"), ["Sessions", "Outline", "Feed", "Files"], table)

    def test_the_off_dashboard_hide_takes_all_four_panes_rows(self):
        # round two, low 4: the outcome, not the class: hidden by the selector the hide uses, every row reads display none and height 0
        r = self._run(); h = r["panesHide"]; table = "\n  " + json.dumps(h) + " classes: " + json.dumps(r.get("panesRowClasses"))
        self.assertTrue(h["covered"], "the hide's selector reaches all four Panes rows (the Files row too since this tidy)" + table)
        self.assertEqual(h["count"], 5, "the head and the four rows, nothing else" + table)
        for x in h["hidden"]:
            self.assertEqual((x["display"], x["height"]), ("none", 0), "hidden: display none, height 0" + table)
        self.assertEqual(h["shown"], ["flex"] * 4, "shown again: display flex" + table)

    def test_the_fast_mode_boxes_popovers_stay_inside_the_card_at_a_short_window(self):
        # round two, the medium: the box's own popover, nested in the judge row, ran 15 px past the card at 380 px (48 at 300)
        fb = self._run()["fastBoxes"]; table = "\n  " + json.dumps(fb)
        self.assertEqual([x["id"] for x in fb], ["rs-judgefast-wrap", "rs-distillfast-wrap", "rs-indexfast-wrap"], table)
        for x in fb:
            self.assertLess(x["viewport"], 400, "a short window" + table)
            self.assertTrue(x["shown"], x["id"] + ": the box's own popover shows" + table)
            self.assertTrue(x["inside"], x["id"] + ": inside the card (%.1f to %.1f in %.1f to %.1f)" % (x["subTop"], x["subBottom"], x["cardTop"], x["cardBottom"]) + table)
            self.assertEqual(x["scrollHeight"], x["scrollHeightRest"], x["id"] + ": no scroll growth under the hover" + table)
        self.assertTrue(any(x["up"] for x in fb), "at least one opened above its box" + table)

    def test_with_no_room_on_either_side_the_popover_stays_below_where_the_card_scrolls_to_it(self):
        # round three, the manager's ruling: at 300 px the Thinking summaries popover fits neither side, with more room above than
        # below; it stays below (main's behaviour), because a bottom clip is reachable by the card's scroll and a top clip is not.
        # The executed cover of the dropped roomier-side clause: adding it back opens this one above
        n = self._run()["noRoom"]; table = "\n  " + json.dumps(n)
        self.assertLess(n["viewport"], 320, table)
        self.assertGreater(n["roomAbove"], n["roomBelow"], "the case: more room above than below" + table)
        self.assertLess(n["roomAbove"], n["subHeight"] + 2, "…and not enough above" + table)
        self.assertLess(n["roomBelow"], n["subHeight"], "…nor below" + table)
        self.assertFalse(n["up"], "neither side fits: below, as main had it, where the scroll reaches the clip" + table)
        self.assertGreaterEqual(n["subTop"], n["cardTop"] - 1, "the start of the text is inside the card" + table)
        self.assertGreater(n["subBottom"], n["cardBottom"], "the clip is at the bottom" + table)

    def test_a_row_near_its_panes_bottom_opens_its_popover_above_and_the_card_does_not_scroll(self):
        # the T408 read: Feed rs-feedcollapsed, Sessions rs-backend and Task tracking rs-indexeffort and rs-judgeconc sent their hover
        # popover 23 to 47 px past the card's bottom, and the card grew a scrollbar for it
        h = self._run()["hoverRows"]
        for theme in ("dark", "light"):
            rows = h[theme]; table = "\n  " + theme + ": " + json.dumps(rows)
            self.assertEqual([x["id"] for x in rows], ["rs-feedcollapsed", "rs-backend", "rs-indexeffort", "rs-judgeconc"], table)
            for x in rows:
                self.assertTrue(x["shown"], x["id"] + ": the popover shows on hover" + table)
                self.assertTrue(x["inside"], x["id"] + ": the popover stays inside the card (%.0f to %.0f in %.0f to %.0f)" % (x["subTop"], x["subBottom"], x["cardTop"], x["cardBottom"]) + table)
                self.assertFalse(x["card"]["scrollable"], x["id"] + ": the card grows no scrollbar for it" + table)
            self.assertTrue(any(x["up"] for x in rows), "at least one of the four opened above its row" + table)

    def test_the_automation_rows_carry_a_line_each_no_tooltip_and_the_card_does_not_scroll_at_rest_or_under_the_pointer(self):
        # T408 (the user 2026-09-13, a screenshot of the Automation tab): the pane scrolled over two rows because a hover tooltip
        # under the last row ran past the card's bottom (the card is the modal's one scroll box), and the tooltip was hard to see
        a = self._run()["automationRows"]
        for theme in ("dark", "light"):
            t = a[theme]; table = "\n  " + theme + ": " + json.dumps(t)
            rows = {r["id"]: r for r in t["rest"]["rows"]}
            self.assertEqual(rows["rs-autonudge"]["label"], "Auto Nudge", table)
            self.assertEqual(rows["rs-autonudge"]["line"], "When a session goes idle with its work still in progress and nothing awaited, nudge it once for a status update, on every connected machine.", table)
            self.assertEqual(rows["rs-suggestcompact"]["line"], "When a session has sat idle for an hour with a lot of context built up, suggest one /compact at a natural point, once per fill-up, on every connected machine.", table)
            # the two model switches (2026-09-17) take the tab's row shape too: a line each, no hover popover
            self.assertEqual(rows["rs-alwaysfast"]["line"], "Every Opus session runs Claude Code's fast mode, billed at a premium; a session you set to Slow stays slow.", table)
            self.assertEqual(rows["rs-retryupgrade"]["line"], "A session whose model fell back without a pick asks for its picked model again every ten minutes, once quiet, until it is back.", table)
            for rid in ("rs-autonudge", "rs-suggestcompact", "rs-alwaysfast", "rs-retryupgrade"):
                self.assertTrue(rows[rid]["lineShown"] and rows[rid]["lineBelowLabel"], rid + ": the line is on screen, under the label" + table)
                self.assertFalse(rows[rid]["hasSub"], rid + ": no hover tooltip" + table)
                self.assertIsNone(rows[rid]["title"], rid + ": no title on the row" + table)
                self.assertIsNone(rows[rid]["inputTitle"], rid + ": no title on the box" + table)
            self.assertFalse(t["rest"]["card"]["scrollable"], theme + ": no scrollbar with the Nudges rows, their lines and the Model rows" + table)
            self.assertFalse(t["hoverCard"]["scrollable"], theme + ": …nor with the pointer on the last Nudges row" + table)
            self.assertEqual(t["hoverCard"]["scrollHeight"], t["rest"]["card"]["scrollHeight"], theme + ": hovering adds nothing to the scroll box" + table)

    def test_the_seven_pills_sit_on_one_row_of_the_card_in_both_themes(self):
        # round one, LOW 1: at 10px of side padding the seven needed 528px against 518 available, and Debug alone dropped to a second
        # row (the bar 38 to 69px); at 8px they fit
        bar = self._run()["bar"]
        for theme in ("dark", "light"):
            bi = bar[theme]; table = "\n  " + theme + ": " + json.dumps(bi)
            self.assertEqual(bi["theme"], theme, table)
            self.assertEqual(bi["rows"], 1, theme + ": one row" + table)
            self.assertLess(bi["width"], bi["available"], theme + ": the pills fit the bar" + table)
            self.assertLess(bi["h"], 45, theme + ": a one-row bar" + table)

    def test_each_moved_row_lives_in_its_new_home_with_its_id_kept(self):
        g = self._run()["general"]
        self.assertTrue(g["open"], json.dumps(g)[:300])
        gen = ["rs-billing", "rs-login-btn", "rs-panes-sec", "rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-keys-web", "rs-filesctl", "rs-theme", "rs-cmap", "rs-pal", "rs-fileedit", "rs-conserve", "rs-updates"]
        chat = ["rs-compact", "rs-chatscheme", "rs-striprows", "rs-cmtmodel", "rs-thinksum", "rs-widgets"]
        expect = dict([(i, "general") for i in gen] + [(i, "chat") for i in chat] + [("rs-feedcollapsed", "feed"), ("rs-defaultdir", "sessions"), ("rs-backend", "sessions"),
                       ("rs-autonudge", "automation"), ("rs-suggestcompact", "automation"), ("rs-tasktrack", "tasks"), ("rs-judgemodel", "tasks"), ("rs-judgeconc", "tasks"),
                       ("rs-judges-index", "debug"), ("rs-judges-triage", "debug"), ("ra-open", "debug"), ("rs-log-open", "debug"), ("rsver", "debug"),
                       ("rs-filelink", "missing"), ("rs-activeonly", "missing"), ("rs-collapsegaps", "missing")])   # the three rows that left settings (T404): no element
        self.assertEqual(g["homes"], expect, "every id in its new home, none missing, the three gone: " + json.dumps(g["homes"]))

    def test_an_older_remembered_tab_comes_up_on_its_new_tab_and_is_rewritten_never_a_blank_card(self):
        m = self._run()["mapping"]
        for old, new in (("automatic", "tasks"), ("system", "debug"), ("tabs", "chat"), ("appearance", "general")):
            r = m[old]
            table = "\n  " + old + ": " + json.dumps(r)[:600]
            self.assertTrue(r["open"], "the rail's gear opened the panel" + table)
            self.assertEqual(r["shown"], [new], "the remembered " + old + " shows " + new + table)
            self.assertEqual([x["tab"] for x in r["pills"] if x["on"]], [new], "…its pill on" + table)
            self.assertEqual(r["remembered"], new, "the store now carries the new name" + table)

    def test_the_debug_and_task_tracking_pills_switch_to_their_panes(self):
        r = self._run()
        self.assertEqual(r["debug"]["shown"], ["debug"], json.dumps(r["debug"]["shown"]))
        self.assertEqual(r["debug"]["remembered"], "debug")
        self.assertEqual(r["tasks"]["shown"], ["tasks"], json.dumps(r["tasks"]["shown"]))
        self.assertEqual(r["automation"]["shown"], ["automation"], json.dumps(r["automation"]["shown"]))
        self.assertEqual(r["automation"]["remembered"], "automation")
        self.assertEqual(r["tasks"]["remembered"], "tasks")


if __name__ == "__main__":
    unittest.main()
