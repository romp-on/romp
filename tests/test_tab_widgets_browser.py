#!/usr/bin/env python3
"""The tab-title WIDGETS and the settings panel in TABS (T379, the user 2026-09-12), on the served dashboard: a hermetic kernel
serves the landing with two synthetic notes-api sessions (web and api, idle, TESTHOST); the chat frame's strip carries the
widgets (the dot slot on every tab, the hot-key keycap on the tab whose hot key the tab hot-key store names) and the
tab-widgets gear glyph at the strip's right end; the glyph opens the settings frame on the Chat tab scrolled to its Tab
widgets section (through the shell's relay; the user's amendment 2026-09-12: no tab of their own), where each registered widget is a row with a live demo drawn by the strip's own render, a sliding switch and its
options; a switch or an option written there reaches the chat frame's strip live (the storage event) and the store's
tabCtx mirror; the last tab is remembered; the pills hide every other pane.

TAB_WIDGETS_DIST=<dir> serves another tree's UI bundle (the red run's before); TAB_WIDGETS_SHOTS=<prefix> writes
<prefix>-strip-<theme>.png and <prefix>-settings-<theme>.png; TAB_WIDGETS_DUMP=<path> writes the whole measurement. Skips LOUDLY
without the extension deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip
there is a failure). Synthetic throughout: placeholder sids, TESTHOST, invented text.
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
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

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
const ctx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
// the tab hot-key store shape (the hot key widget reads it): web is in the set, its chord Ctrl+Shift+1
await ctx.addInitScript(([sid]) => {
  try { localStorage.setItem("romp:tabkeys", JSON.stringify({ [sid]: "web" })); localStorage.setItem("romp:keys", JSON.stringify({ ["session.hotkey." + sid]: "Ctrl+Shift+1" })); } catch (e) {}
}, [cfg.sidWeb]);
const page = await ctx.newPage();
await page.goto(cfg.url);
await page.waitForSelector("#rail-gear", { timeout: 20000 });
const chat = () => page.frames().find((f) => f.url().includes("/chat"));
await page.waitForFunction(() => true);
let chatF = chat();
for (let i = 0; i < 100 && !chatF; i++) { await page.waitForTimeout(100); chatF = chat(); }
if (!chatF) { console.error("no chat frame"); process.exit(1); }
await chatF.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
await chatF.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
await chatF.waitForTimeout(500);
const readStrip = () => chatF.evaluate(([sidWeb]) => {
  const rect = (e) => { const b = e.getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top, bottom: b.bottom, w: b.width, h: b.height }; };
  const tabs = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => ({
    id: t.dataset.id, name: (t.querySelector(".tab-label") || {}).textContent || "",
    children: Array.from(t.children).map((c) => c.className),
    dot: (() => { const d = t.querySelector(".tab-dot"); if (!d) return null; const cs = getComputedStyle(d); return { cls: d.className, visibility: cs.visibility, opacity: cs.opacity, bg: cs.backgroundColor, w: d.getBoundingClientRect().width }; })(),
    key: (() => { const k = t.querySelector(".tab-key"); return k ? { text: k.textContent, title: k.title, w: k.getBoundingClientRect().width } : null; })(),
    ctx: !!t.querySelector(".tab-ctx"),
  }));
  const gear = document.querySelector("#tabs .tab-tagbox .tab-widgets-gear");
  const box = document.querySelector("#tabs .tab-tagbox");
  const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
  return { tabs, web: tabs.find((t) => t.id === sidWeb), gear: gear ? { title: gear.title, aria: gear.getAttribute("aria-label"), svg: !!gear.querySelector("svg"), rect: rect(gear), inBox: gear.parentElement === box, boxH: box.getBoundingClientRect().height } : null,
           store: { tabWidgets: s.tabWidgets || null, tabCtx: s.tabCtx || null } };
}, [cfg.sidWeb]);
const out = {};
out.strip0 = await readStrip();
// the glyph opens the settings frame on the Chat tab, scrolled to its Tab widgets section, through the shell
const settingsOpen = () => page.evaluate(() => document.body.classList.contains("settings-open"));
if (out.strip0.gear) await chatF.click("#tabs .tab-tagbox .tab-widgets-gear");
await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
out.shellOpen = await settingsOpen();
let setF = page.frames().find((f) => f.url().includes("/settings"));
for (let i = 0; i < 50 && !setF; i++) { await page.waitForTimeout(100); setF = page.frames().find((f) => f.url().includes("/settings")); }
if (!setF) {   // no settings frame opened (the red run's before: no glyph, no ask): every later reading is an honest empty, so each test fails on its own assertion
  const none = { open: false, pills: [], panes: [], rows: [], remembered: null, section: null };
  Object.assign(out, { panel0: none, afterCtxOff: { panel: none, strip: out.strip0 }, dotOpt: { present: false, picked: false, labels: [] }, afterGrey: { panel: none, strip: out.strip0 },
                       afterKeyOff: { strip: out.strip0 }, feedPane: none, pillBack: none, reask: none, afterEscape: { shellOpen: false, panel: none }, reopen: none, legacy: {}, tall: { open: null, reask: null } });
  fs.writeFileSync(cfg.out, JSON.stringify(out)); console.log("RESULT: ok"); await browser.close(); process.exit(0);
}
await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 }).catch(() => {});
await setF.evaluate(() => (document.fonts && document.fonts.ready) || null).catch(() => {});   // the layout the measurement reads is the settled one (a late web font moved the head on a slow runner)
// the landing's own mark, never a delay: the gear stamps the card when the section scroll lands (CI, 2026-09-13: a fixed wait read before it)
const landed = (frame) => frame.waitForSelector('#rsettings .rs-card[data-section-landed="tabwidgets"]', { timeout: 10000 }).then(() => true).catch(() => false);
out.landed0 = await landed(setF);
await setF.waitForTimeout(150);
const readPanel = () => setF.evaluate(() => {
  const p = document.getElementById("rsettings");
  if (!p || p.hidden) return { open: false };
  const pills = Array.from(document.querySelectorAll("#rsettings .rs-tab")).map((b) => ({ tab: b.dataset.tab, text: b.textContent, on: b.classList.contains("on"), selected: b.getAttribute("aria-selected") }));
  const panes = Array.from(document.querySelectorAll("#rsettings .rs-pane")).map((pn) => ({ pane: pn.dataset.pane, hidden: pn.hidden, display: getComputedStyle(pn).display, rows: pn.querySelectorAll(".rs-row, .rs-widget").length }));
  const rows = Array.from(document.querySelectorAll("#rs-widgets .rs-widget")).map((r) => {
    const sw = r.querySelector(".rs-switch"); const cs = getComputedStyle(sw); const knob = getComputedStyle(sw, "::after");
    const demo = r.querySelector(".rs-widget-demo .tab"); const desc = r.querySelector(".rs-widget-name .rs-sub, .rs-widget-name span");
    return { id: r.dataset.widget, label: r.querySelector(".rs-widget-name b").textContent, desc: desc.textContent,
             swLeft: sw.getBoundingClientRect().left, descDisplay: getComputedStyle(desc).display,
             sw: { role: sw.getAttribute("role"), checked: sw.getAttribute("aria-checked"), on: sw.classList.contains("on"), w: sw.getBoundingClientRect().width, h: sw.getBoundingClientRect().height, radius: cs.borderRadius, knobLeft: knob.left, bg: cs.backgroundColor },
             demo: demo ? Array.from(demo.children).map((c) => ({ cls: c.className, text: c.textContent, title: c.title || "" })) : null,
             opts: Array.from(r.querySelectorAll(".rs-widget-opt")).map((o) => ({ key: o.dataset.opt, label: o.title, current: (o.querySelector("button") || {}).textContent || "" })) };
  });
  // the SECTION: the Tab widgets head against the card's box and scroll (the gear's ask scrolls the card so the head sits under the padding)
  const card = document.querySelector("#rsettings .rs-card"); const sec = document.querySelector('#rsettings .rs-sec[data-section="tabwidgets"]');
  const cr = card.getBoundingClientRect(); const sr = sec ? sec.getBoundingClientRect() : null;
  const section = sec ? { top: sr.top, cardTop: cr.top, cardBottom: cr.bottom, pad: parseFloat(getComputedStyle(card).paddingTop), scrollTop: card.scrollTop, overflow: card.scrollHeight - card.clientHeight,
                          inChat: !!sec.closest('.rs-pane[data-pane="chat"]'), paneHidden: sec.closest(".rs-pane").hidden,
                          room: parseFloat(getComputedStyle(sec.closest(".rs-pane")).paddingBottom) || 0, cardH: cr.height, viewportH: window.innerHeight } : null;
  return { open: true, pills, panes, rows, remembered: localStorage.getItem("romp:settingsTab"), section };
});
out.panel0 = await readPanel();
// the Context bar's switch off: the store's prefs and mirror, the chat's strip on the storage event
const flip = async (id) => { await setF.click('#rs-widgets .rs-widget[data-widget="' + id + '"] .rs-switch'); await setF.waitForTimeout(400); };
await flip("ctx");
out.afterCtxOff = { panel: await readPanel(), strip: await readStrip() };
await flip("ctx");
// the dot's option: a grey dot when idle (the tabs are idle here), through the house picker
out.dotOpt = await setF.evaluate(async () => {
  const wrap = document.querySelector('#rs-widgets .rs-widget[data-widget="dot"] .rs-widget-opt[data-opt="idle"]');
  if (!wrap) return { present: false };
  wrap.querySelector("button").click();
  await new Promise((r) => setTimeout(r, 100));
  const row = Array.from(wrap.querySelectorAll("[data-wopt-dot-idle]")).find((r) => r.getAttribute("data-wopt-dot-idle") === "grey");
  const labels = Array.from(wrap.querySelectorAll("[data-wopt-dot-idle]")).map((r) => r.textContent.replace(/\u2713/g, "").trim());   // the current row carries the house picker's check glyph
  if (row) row.click();
  await new Promise((r) => setTimeout(r, 400));
  return { present: true, labels, picked: !!row };
});
out.afterGrey = { panel: await readPanel(), strip: await readStrip() };
// the hot key widget off: the keycap leaves web's tab
await flip("hotkey");
out.afterKeyOff = { strip: await readStrip() };
await flip("hotkey");
// the pills: Feed hides Chat; Escape closes; the next open remembers the tab
await setF.click('#rsettings .rs-tab[data-tab="feed"]'); await setF.waitForTimeout(150);
out.feedPane = await readPanel();
// a pill round trip (Feed, then Chat by its pill): the Chat pane comes back at its top with no section room left behind
await setF.click('#rsettings .rs-tab[data-tab="chat"]'); await setF.waitForTimeout(150);
out.pillBack = await readPanel();
// an ask on an OPEN panel (through the shell's relay, the path the glyph's message takes; the lifted settings iframe covers the
// strip while the panel is open, so the glyph itself is not reachable by a pointer then): switches back to Chat and scrolls
await page.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets")); out.landedReask = await landed(setF); await setF.waitForTimeout(150);
out.reask = await readPanel();
// the screenshots: the strip with the glyph and the Chat tab at its Tab widgets section, dark then light
const shot = async (theme) => {
  await page.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
  await chatF.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
  await setF.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
  await page.waitForTimeout(200);
  if (!cfg.shots) return;
  const card = await setF.evaluate(() => { const b = document.querySelector("#rsettings .rs-card").getBoundingClientRect(); return { x: b.left, y: b.top, width: b.width, height: b.height }; });   // the whole card: the scrolled Tab widgets section sits in its lower part
  const fr = await page.evaluate(() => { const f = document.getElementById("f-settings").getBoundingClientRect(); return { x: f.left, y: f.top }; });
  await page.screenshot({ path: cfg.shots + "-settings-" + theme + ".png", clip: { x: fr.x + card.x, y: fr.y + card.y, width: card.width, height: card.height } });
};
await shot("dark"); await shot("light");
await page.evaluate(() => document.body.classList.remove("theme-light")); await setF.evaluate(() => document.body.classList.remove("theme-light")); await chatF.evaluate(() => document.body.classList.remove("theme-light"));
await page.keyboard.press("Escape"); await page.waitForTimeout(300);
out.afterEscape = { shellOpen: await settingsOpen(), panel: await readPanel() };
// the strip shot with the panel closed
for (const theme of ["dark", "light"]) {
  await chatF.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
  await page.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
  await page.waitForTimeout(150);
  if (cfg.shots) {
    const bar = await chatF.evaluate(() => { const b = document.getElementById("tabbar").getBoundingClientRect(); return { x: 0, y: Math.max(0, b.top - 4), width: Math.min(window.innerWidth, 900), height: b.height + 8 }; });
    const fr = await page.evaluate(() => { const f = document.getElementById("f-chat").getBoundingClientRect(); return { x: f.left, y: f.top }; });
    await page.screenshot({ path: cfg.shots + "-strip-" + theme + ".png", clip: { x: fr.x + bar.x, y: fr.y + bar.y, width: bar.width, height: bar.height } });
  }
}
await chatF.evaluate(() => document.body.classList.remove("theme-light")); await page.evaluate(() => document.body.classList.remove("theme-light"));
// reopen from the rail's gear: the remembered tab (Chat) comes up
await page.click("#rail-gear");
await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 10000 }).catch(() => {});
await setF.waitForTimeout(300);
out.reopen = await readPanel();
await page.close(); await ctx.close();
// LEGACY STORES (round one, HIGH): a browser from before the widgets holds tabCtx and no tabWidgets. The panel must read the
// context bar's row from tabCtx (always: on at Always; never: off), and a save of an UNRELATED setting (Compact transcript)
// must leave the store's tabWidgets absent and tabCtx as it was, the strip unchanged. One fresh context per mode.
out.legacy = {};
for (const mode of ["always", "never"]) {
  const c2 = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  await c2.addInitScript(([m]) => { try { localStorage.setItem("romp:settings", JSON.stringify({ compact: true, tabCtx: m })); } catch (e) {} }, [mode]);
  const p2 = await c2.newPage();
  await p2.goto(cfg.url);
  await p2.waitForSelector("#rail-gear", { timeout: 20000 });
  let cf = p2.frames().find((f) => f.url().includes("/chat"));
  for (let i = 0; i < 100 && !cf; i++) { await p2.waitForTimeout(100); cf = p2.frames().find((f) => f.url().includes("/chat")); }
  await cf.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await cf.waitForTimeout(400);
  const strip = () => cf.evaluate(() => { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}");
    return { ctx: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => !!t.querySelector(".tab-ctx")), store: { tabWidgets: "tabWidgets" in s ? s.tabWidgets : "absent", tabCtx: s.tabCtx, compact: s.compact } }; });
  const before = await strip();
  // the shell's state before the ask is recorded (a lifted settings iframe would take the strip's pointer events); the ask
  // itself goes through the shell's own relay, the path the glyph's message takes, so this scene reads the panel whatever
  // the shell's pointer state is
  const shellBefore = await p2.evaluate(() => { const f = document.getElementById("f-settings"); return { cls: document.body.className, fSrc: f ? f.getAttribute("src") : null, fDisplay: f ? getComputedStyle(f).display : null }; });
  await p2.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets"));
  await p2.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  let sf = p2.frames().find((f) => f.url().includes("/settings"));
  for (let i = 0; i < 50 && !sf; i++) { await p2.waitForTimeout(100); sf = p2.frames().find((f) => f.url().includes("/settings")); }
  await sf.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 }).catch(() => {});
  await sf.waitForTimeout(300);
  const row = () => sf.evaluate(() => { const r = document.querySelector('#rs-widgets .rs-widget[data-widget="ctx"]'); if (!r) return null;
    const sw = r.querySelector(".rs-switch"); const o = r.querySelector('.rs-widget-opt[data-opt="show"] button');
    return { checked: sw.getAttribute("aria-checked"), show: o ? o.textContent.replace(/\u25be/g, "").trim() : null }; });
  const panelBefore = await row();
  await sf.click("#rs-compact"); await sf.waitForTimeout(500);   // an unrelated setting's save
  out.legacy[mode] = { before, shellBefore, panelBefore, after: await strip(), panelAfter: await row() };
  await p2.close(); await c2.close();
}
// a TALL window (the follow-up's round one, MEDIUM): below its cap the card grows under any room added, so the head stopped
// short (152px off at 1200px); the room is sized to the cap now. The glyph's open and a re-ask, measured at 1200 by 1200.
{
  const c3 = await browser.newContext({ viewport: { width: 1200, height: 1200 } });
  const p3 = await c3.newPage(); await p3.goto(cfg.url); await p3.waitForSelector("#rail-gear", { timeout: 20000 });
  let cf3 = p3.frames().find((f) => f.url().includes("/chat"));
  for (let i = 0; i < 100 && !cf3; i++) { await p3.waitForTimeout(100); cf3 = p3.frames().find((f) => f.url().includes("/chat")); }
  await cf3.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await p3.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets"));
  await p3.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
  let sf3 = p3.frames().find((f) => f.url().includes("/settings"));
  for (let i = 0; i < 50 && !sf3; i++) { await p3.waitForTimeout(100); sf3 = p3.frames().find((f) => f.url().includes("/settings")); }
  await sf3.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 }).catch(() => {});
  await sf3.evaluate(() => (document.fonts && document.fonts.ready) || null).catch(() => {});
  out.tallLanded = await landed(sf3); await sf3.waitForTimeout(150);
  const readSec = () => sf3.evaluate(() => { const card = document.querySelector("#rsettings .rs-card"); const sec = document.querySelector('#rsettings .rs-sec[data-section="tabwidgets"]');
    if (!card || !sec) return null; const cr = card.getBoundingClientRect(), sr = sec.getBoundingClientRect();
    return { top: sr.top, cardTop: cr.top, cardBottom: cr.bottom, pad: parseFloat(getComputedStyle(card).paddingTop), scrollTop: card.scrollTop, overflow: card.scrollHeight - card.clientHeight,
             room: parseFloat(getComputedStyle(sec.closest(".rs-pane")).paddingBottom) || 0, cardH: cr.height, viewportH: window.innerHeight, inChat: true, paneHidden: sec.closest(".rs-pane").hidden }; });
  out.tall = { open: await readSec() };
  await p3.evaluate(() => window.__rompOpenSettings("chat", "tabwidgets")); await landed(sf3); await sf3.waitForTimeout(150);
  out.tall.reask = await readSec();
  await p3.close(); await c3.close();
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
console.log("RESULT: ok");
"""


class ServedTabWidgets(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="tab-widgets-")
        before = os.environ.get("TAB_WIDGETS_DIST", "")
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
                 "model": "claude-opus-5", "liveModel": "Opus 5",
                 **({"liveCtx": 62} if name == "web" else {})}))   # web carries a context percentage, so the context bar has something to show (round two, LOW 6)
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
                       "shots": os.environ.get("TAB_WIDGETS_SHOTS", "")}, f)
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
        if os.environ.get("TAB_WIDGETS_DUMP"):
            Path(os.environ["TAB_WIDGETS_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def test_the_strip_carries_the_widgets_and_the_gear_glyph_in_the_tag_box(self):
        s = self._run()["strip0"]
        table = "\n  " + json.dumps(s)[:1200]
        self.assertEqual(len(s["tabs"]), 2, table)
        for t in s["tabs"]:
            self.assertIsNotNone(t["dot"], "every tab carries the dot slot (T262g)" + table)
            self.assertEqual(t["dot"]["visibility"], "hidden", "idle: the slot is laid out and hidden" + table)
            self.assertEqual(t["children"][0], t["dot"]["cls"], "the dot is the tab's first child (the before-the-name slot)" + table)
        web = s["web"]
        self.assertIsNotNone(web["key"], "web's hot key (the tab hot-key store) renders as a keycap" + table)
        self.assertEqual(web["key"]["text"], "⌃⇧1", table)
        self.assertGreater(web["key"]["w"], 10, table)
        self.assertTrue(web["ctx"], "web carries a context percentage (62): the bar shows from half full" + table)
        self.assertEqual(web["children"].index("tab-ctx"), web["children"].index("tab-label") + 1, "the bar follows the name" + table)
        self.assertEqual(web["children"].index("tab-key"), web["children"].index("tab-ctx") + 1, "the keycap follows the bar (registration order)" + table)
        api = next(t for t in s["tabs"] if t["name"].endswith("api"))
        self.assertIsNone(api["key"], "no hot key assigned: no keycap" + table)
        self.assertIsNotNone(s["gear"], "the glyph is in the strip (the chat sits in the shell, so a gear can be reached)" + table)
        self.assertEqual((s["gear"]["title"], s["gear"]["aria"], s["gear"]["svg"], s["gear"]["inBox"]), ("Tab widgets…", "Tab widgets", True, True), table)
        self.assertLessEqual(s["gear"]["rect"]["h"], s["gear"]["boxH"] + 0.5, "it takes no extra height beyond the tag box" + table)

    def _assert_scrolled_to_the_section(self, p, table):
        # the section head sits inside the card's visible box, under its padding, unless the card ran out of scroll first
        sec = p["section"]
        table = "\n  section=" + json.dumps(sec) + table   # the numbers first: the panel's table is long and cut
        self.assertIsNotNone(sec, "the Tab widgets head carries the section anchor" + table)
        self.assertTrue(sec["inChat"] and not sec["paneHidden"], "the section is in the Chat pane, which is shown" + table)
        self.assertGreaterEqual(sec["top"], sec["cardTop"] - 0.5, "the head is not above the card's box" + table)
        self.assertLess(sec["top"], sec["cardBottom"], "the head is inside the card's box" + table)
        if sec["overflow"] > 0:
            self.assertGreater(sec["scrollTop"], 0, "the card scrolled" + table)
            # round two, LOW 1: the head sits under the card's padding, always: the pane gains room at its end for it
            self.assertLess(abs(sec["top"] - (sec["cardTop"] + sec["pad"])), 3, "the head sits under the card's padding" + table)

    def test_the_glyph_opens_the_settings_on_the_chat_tab_scrolled_to_the_tab_widgets_section_with_the_other_panes_hidden(self):
        r = self._run()
        self.assertTrue(r["shellOpen"], "the shell lifted the settings frame")
        p = r["panel0"]
        table = "\n  " + json.dumps(p)[:1500]
        self.assertTrue(p["open"], table)
        self.assertEqual([x["tab"] for x in p["pills"]], ["chat", "feed", "sessions", "automatic", "appearance", "system"], "six pills: no Tabs tab (the user's amendment)" + table)
        self.assertEqual([x["text"] for x in p["pills"]], ["Chat", "Feed", "Sessions", "Automatic", "Appearance", "System"], table)
        self.assertEqual([x["on"] for x in p["pills"]], [True, False, False, False, False, False], "the Chat pill is on" + table)
        self.assertEqual([x["selected"] for x in p["pills"]], ["true", "false", "false", "false", "false", "false"], table)
        shown = [x for x in p["panes"] if x["display"] != "none"]
        self.assertEqual([x["pane"] for x in shown], ["chat"], "one pane painted" + table)
        self.assertTrue(all(x["rows"] > 0 for x in p["panes"]), "every pane holds rows" + table)
        self.assertEqual(p["remembered"], "chat", table)
        self.assertTrue(r["landed0"], "the gear marked the landing before the measurement (the lab waits for the event, never a delay)")
        self._assert_scrolled_to_the_section(p, table)

    def test_each_widget_row_shows_a_live_demo_drawn_by_the_strips_render_a_sliding_switch_and_its_options(self):
        p = self._run()["panel0"]
        self.assertTrue(p["open"], "the panel opened: " + json.dumps(p)[:300])
        rows = p["rows"]
        table = "\n  " + json.dumps(rows)[:2000]
        self.assertEqual([r["id"] for r in rows], ["dot", "ctx", "hotkey"], "registration order: the dot, the bar, the hot key" + table)
        self.assertEqual([r["label"] for r in rows], ["Status dot", "Context bar", "Hot key"], table)
        for r in rows:
            self.assertEqual((r["sw"]["role"], r["sw"]["checked"], r["sw"]["on"]), ("switch", "true", True), r["id"] + " is on by default" + table)
            self.assertEqual(r["sw"]["radius"], "999px", "the sliding toggle, a pill" + table)
            self.assertGreater(r["sw"]["w"], r["sw"]["h"], table)
            self.assertEqual(r["sw"]["knobLeft"], "18px", "on: the knob sits right" + table)
            self.assertTrue(r["desc"], "a one-line description" + table)
        dot, ctx, key = rows
        self.assertEqual([c["cls"] for c in dot["demo"]], ["tab-dot", "tab-label"], "the demo: a working session's gold dot before the name" + table)
        self.assertEqual(dot["demo"][0]["title"], "working — a turn is running right now", table)
        self.assertEqual([c["cls"] for c in ctx["demo"]], ["tab-label", "tab-ctx"], "the bar after the name" + table)
        self.assertEqual([c["cls"] for c in key["demo"]], ["tab-label", "tab-key"], table)
        self.assertEqual(key["demo"][1]["text"], "⌃⇧1", "the demo keycap" + table)
        self.assertEqual([[o["key"] for o in r["opts"]] for r in rows], [["idle"], ["show"], []], "the dot's idle option, the bar's show option, the hot key none" + table)
        self.assertEqual(dot["opts"][0]["current"].replace("▾", "").strip(), "Hide when idle", table)
        self.assertEqual(ctx["opts"][0]["current"].replace("▾", "").strip(), "From 50% full", table)
        # round one, LOW 2: one grid across the rows, so every switch starts at the same x; the description sits behind the hover popover
        self.assertEqual(len({round(r["swLeft"]) for r in rows}), 1, "the switches line up down the list" + table)
        self.assertEqual([r["descDisplay"] for r in rows], ["none"] * 3, "the descriptions are hover popovers at rest, the panel's idiom" + table)

    def test_a_store_from_before_the_widgets_reads_its_gauge_setting_and_an_unrelated_save_leaves_it_alone(self):
        # round one, HIGH: an injected default for tabWidgets won over tabCtx (the row read on at 50 percent whatever the user had
        # chosen) and a save of ANY setting wrote the empty prefs and rewrote the mirror
        lg = self._run()["legacy"]
        for mode, checked, show in (("always", "true", "Always"), ("never", "false", "From 50% full")):
            sc = lg.get(mode)
            table = "\n  " + json.dumps(sc)[:1500]
            self.assertIsNotNone(sc, "the legacy scene ran" + table)
            self.assertEqual(sc["before"]["store"], {"tabWidgets": "absent", "tabCtx": mode, "compact": True}, mode + ": the seeded store" + table)
            self.assertEqual((sc["panelBefore"]["checked"], sc["panelBefore"]["show"]), (checked, show), mode + ": the row reads the older setting" + table)
            self.assertEqual(sc["after"]["store"], {"tabWidgets": "absent", "tabCtx": mode, "compact": False}, mode + ": Compact saved; the widgets key still absent, the mirror untouched" + table)
            self.assertEqual(sc["after"]["ctx"], sc["before"]["ctx"], mode + ": the strip unchanged" + table)
            self.assertEqual(any(sc["before"]["ctx"]), mode == "always", mode + ": the seeded percentage shows exactly when the older setting says so (the assertion above is not vacuous)" + table)
            self.assertEqual(sc["panelAfter"], sc["panelBefore"], mode + ": the row unchanged" + table)

    def test_a_switch_writes_the_prefs_and_the_mirror_and_the_strip_follows_live(self):
        r = self._run()
        a = r["afterCtxOff"]
        self.assertTrue(a["panel"]["open"], "the panel opened: " + json.dumps(a["panel"])[:300])
        table = "\n  " + json.dumps(a["strip"]["store"]) + " " + json.dumps([x["sw"]["checked"] for x in a["panel"]["rows"]])
        self.assertEqual([x["sw"]["checked"] for x in a["panel"]["rows"]], ["true", "false", "true"], "the Context bar's switch is off" + table)
        self.assertEqual(a["strip"]["store"]["tabWidgets"]["on"], {"ctx": False}, "the store's prefs" + table)
        self.assertEqual(a["strip"]["store"]["tabCtx"], "never", "…and the older key mirrors it, for older readers" + table)
        self.assertFalse(a["strip"]["web"]["ctx"], "the bar left web's tab live" + table)
        k = r["afterKeyOff"]["strip"]
        self.assertIsNone(k["web"]["key"], "the hot key widget off: the keycap left web's tab, live, through the storage event: " + json.dumps(k["web"]))
        self.assertEqual(k["store"]["tabWidgets"]["on"], {"ctx": True, "hotkey": False}, "the bar's flag was written back on, the hot key's off: " + json.dumps(k["store"]))
        g = r["afterGrey"]
        self.assertTrue(r["dotOpt"]["present"] and r["dotOpt"]["picked"], json.dumps(r["dotOpt"]))
        self.assertEqual(r["dotOpt"]["labels"], ["Hide when idle", "Grey dot when idle"], "the option's two choices as a house picker")
        self.assertEqual(g["strip"]["store"]["tabWidgets"]["opts"], {"dot": {"idle": "grey"}}, json.dumps(g["strip"]["store"]))
        for t in g["strip"]["tabs"]:
            self.assertEqual(t["dot"]["cls"], "tab-dot idle", "the idle tabs wear the quiet grey dot now: " + json.dumps(t["dot"]))
            self.assertEqual(t["dot"]["visibility"], "visible", json.dumps(t["dot"]))
            self.assertLess(float(t["dot"]["opacity"]), 0.6, "quiet" + json.dumps(t["dot"]))
        self.assertEqual(g["panel"]["rows"][0]["demo"][0]["cls"], "tab-dot", "the demo is a working session: its dot stays gold whatever the idle option")
        self.assertEqual(g["panel"]["rows"][0]["opts"][0]["current"].replace("▾", "").strip(), "Grey dot when idle")

    def test_the_pills_switch_panes_escape_closes_and_the_next_open_remembers_the_tab(self):
        r = self._run()
        c = r["feedPane"]
        self.assertTrue(c["open"], "the panel opened: " + json.dumps(c)[:300])
        shown = [x["pane"] for x in c["panes"] if x["display"] != "none"]
        self.assertEqual(shown, ["feed"], json.dumps(c["panes"]))
        self.assertEqual(c["remembered"], "feed")
        self.assertTrue(c["section"]["paneHidden"], "the Tab widgets section is in the hidden Chat pane now")
        # the follow-up's round one, LOW 1: a pill round trip (Feed, then Chat by its pill) leaves no section room on the Chat pane
        pb = r["pillBack"]
        self.assertEqual([x["pane"] for x in pb["panes"] if x["display"] != "none"], ["chat"], json.dumps(pb["panes"]))
        self.assertEqual(pb["section"]["room"], 0, "the Chat pane comes back with no room left behind: " + json.dumps(pb["section"]))
        self.assertEqual(pb["section"]["scrollTop"], 0, "…at its top: " + json.dumps(pb["section"]))
        a = r["reask"]
        self.assertEqual([x["pane"] for x in a["panes"] if x["display"] != "none"], ["chat"], "the glyph on an open panel switches back to Chat: " + json.dumps(a["panes"]))
        self._assert_scrolled_to_the_section(a, "\n  " + json.dumps(a["section"]))
        self.assertFalse(r["afterEscape"]["shellOpen"], "Escape closed the settings (the shell's chain)")
        self.assertFalse(r["afterEscape"]["panel"]["open"])
        ro = r["reopen"]
        self.assertTrue(ro["open"], "the rail's gear reopened it")
        self.assertEqual([x["pane"] for x in ro["panes"] if x["display"] != "none"], ["chat"], "…on the remembered tab (Chat was picked last)")
        # round two, LOW 2 and 7: a plain open (no section) starts at the card's top, and the earlier ask's observer never fires again
        self.assertEqual(ro["section"]["scrollTop"], 0, "a plain open resets the card; no stale section scroll: " + json.dumps(ro["section"]))

    def test_a_tall_window_lands_the_head_under_the_padding_too(self):
        # the follow-up's round one, MEDIUM: below its cap the card grew under the room and the head stopped 152px short at a
        # 1200px window; the room is sized to the card's cap now, so the first open and a re-ask land the head at the top
        t = self._run()["tall"]
        for k in ("open", "reask"):
            sec = t[k]
            table = "\n  " + json.dumps(sec)
            self.assertIsNotNone(sec, k + ": the tall scene ran" + table)
            self.assertGreaterEqual(sec["viewportH"], 1200, table)
            self.assertLess(abs(sec["top"] - (sec["cardTop"] + sec["pad"])), 3, k + ": the head sits under the card's padding at 1200px" + table)


if __name__ == "__main__":
    unittest.main()
