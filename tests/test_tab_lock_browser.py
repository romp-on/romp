#!/usr/bin/env python3
"""THE TAB LOCK (T395, the user 2026-09-12) on the served chat page and, for the Sessions pane, the landing: a hermetic kernel serves six synthetic notes-api
sessions (TESTHOST, one flat row of tabs); the strip carries a padlock button right after the + tab and before the tags
box, in a box of the tags box's height. A REAL mouse drag (page.mouse down, a run of moves across the strip, up over the
target) with the lock OFF moves the tab; the lock pressed, the same drag moves nothing, the tabs are not draggable, the
box wears the accent with no fill, the setting persists across a reload; pressed again, the drag moves the tab once more.
Round one: a keyboard press on the lock keeps the focus on the lock across the strip's rebuild; and the Sessions pane
(the landing's timeline, which shares the order) refuses a lane drag while locked, its lanes without the grab cursor and
saying why, and moves the lane once unlocked.

TAB_LOCK_DIST=<dir> serves another tree's UI bundle (the red run's before); TAB_LOCK_SHOTS=<prefix> writes
<prefix>-locked-<theme>.png; TAB_LOCK_DUMP=<path> writes the whole measurement. Skips LOUDLY without the extension deps or
a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a failure). Synthetic
throughout: placeholder sids, TESTHOST, invented text.
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

NAMES = ["web", "api", "deploy", "tests", "docs", "auth"]
SIDS = {n: "%s-1111-2222-3333-444444444444" % (chr(ord("a") + i) * 8) for i, n in enumerate(NAMES)}
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff"), ("#54B204", "#ffffff"), ("#c98cff", "#1a0c2e"),
           ("#e5a50a", "#1a1200"), ("#4EC9B0", "#00201a")]


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
const ctx = await browser.newContext({ viewport: { width: 1400, height: 700 } });   // one explicit context: the shell page below shares its store
const page = await ctx.newPage();
await page.addInitScript(() => {   // the gesture's log: every drag event, so a case that moved nothing says whether a drag ever began
  window.__log = [];
  for (const k of ["dragstart", "drop", "dragend"]) window.addEventListener(k, (e) => window.__log.push({ k, prevented: e.defaultPrevented }));
});
const settle = async () => {
  await page.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
  await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await page.waitForTimeout(600);
};
await page.goto(cfg.chat);
await settle();
// the strip: the tabs in DOM order with their rects, the lock box and its dress, the store
const layout = () => page.evaluate(() => {
  const bar = document.getElementById("tabs"); const b = bar.getBoundingClientRect();
  const r1 = (v) => Math.round(v * 10) / 10;
  const tabs = Array.from(bar.querySelectorAll(".tab[data-id]")).map((t) => { const r = t.getBoundingClientRect();
    return { id: t.dataset.id, name: (t.querySelector(".tab-label") || t).textContent.trim(), left: r1(r.left - b.left), top: r1(r.top - b.top), w: r1(r.width), h: r1(r.height), draggable: !!t.draggable }; });
  const box = bar.querySelector(".tab-lockbox"); const btn = box && box.querySelector(".tab-lock");
  const probe = document.createElement("span"); probe.style.color = "var(--accent)"; document.body.appendChild(probe);
  const accent = getComputedStyle(probe).color; probe.remove();
  const add = bar.querySelector(".tab-add"), tagBox = bar.querySelector(".tab-tagbox"), tagBtn = bar.querySelector(".tab-tagbox .tab-tagfilter");
  const tb = tagBtn ? { w: r1(tagBtn.getBoundingClientRect().width), h: r1(tagBtn.getBoundingClientRect().height), border: getComputedStyle(tagBtn).borderTopColor, radius: getComputedStyle(tagBtn).borderRadius } : null;
  let s = {}; try { s = JSON.parse(localStorage.getItem("romp:settings") || "{}") || {}; } catch (e) {}
  const lock = box ? { present: true, prev: box.previousElementSibling ? box.previousElementSibling.className : null, next: box.nextElementSibling ? box.nextElementSibling.className : null,
                       boxH: r1(box.getBoundingClientRect().height), addH: add ? r1(add.getBoundingClientRect().height) : null, tagBoxH: tagBox ? r1(tagBox.getBoundingClientRect().height) : null,
                       on: btn.classList.contains("on"), pressed: btn.getAttribute("aria-pressed"), label: btn.getAttribute("aria-label"), title: btn.title, svg: !!btn.querySelector("svg"),
                       shackle: (btn.querySelector("path") || {}).getAttribute ? btn.querySelector("path").getAttribute("d") : null,
                       color: getComputedStyle(btn).color, bg: getComputedStyle(btn).backgroundColor, border: getComputedStyle(btn).borderTopColor, radius: getComputedStyle(btn).borderRadius,
                       w: r1(btn.getBoundingClientRect().width), h: r1(btn.getBoundingClientRect().height) }
                   : { present: false };
  return { bar: { left: b.left, top: b.top }, tabs, order: tabs.map((t) => t.id), lock, tagBtn: tb, accent, store: { tabsLocked: "tabsLocked" in s ? s.tabsLocked : "absent" } };
});
// drag the tab at index `from` and release over the left part of the tab at index `to` (the reorder lab's gesture)
async function drag(label) {
  const pre = await layout();
  const src = pre.tabs[2], tgt = pre.tabs[0];
  const sx = pre.bar.left + src.left + src.w / 2, sy = pre.bar.top + src.top + src.h / 2;
  const tx = pre.bar.left + tgt.left + tgt.w * 0.25, ty = pre.bar.top + tgt.top + tgt.h / 2;
  await page.evaluate(() => { window.__log = []; });
  await page.mouse.move(sx, sy); await page.mouse.down();
  await page.mouse.move(sx + 4, sy + 1, { steps: 2 });
  await page.mouse.move(tx, ty, { steps: 16 });
  for (let i = 0; i < 3; i++) await page.mouse.move(tx, ty);
  await page.mouse.up();
  await page.waitForTimeout(500);
  await page.mouse.move(900, 600);
  const post = await layout();
  const log = await page.evaluate(() => window.__log);
  return { label, dragged: src.name, preOrder: pre.order.map((id) => pre.tabs.find((t) => t.id === id).name), postOrder: post.order.map((id) => post.tabs.find((t) => t.id === id).name),
           moved: pre.order.join() !== post.order.join(), landedFirst: post.order[0] === src.id, srcDraggable: src.draggable,
           ev: { dragstart: log.filter((e) => e.k === "dragstart").length, drop: log.filter((e) => e.k === "drop").length, dragend: log.filter((e) => e.k === "dragend").length } };
}
const out = { start: await layout() };
out.unlockedDrag = await drag("unlocked: the 3rd tab to the first slot");
const press = async () => { if (!(await layout()).lock.present) return false; await page.click("#tabs .tab-lockbox .tab-lock"); await page.waitForTimeout(400); return true; };
out.pressed = await press();
out.locked = await layout();
await page.reload(); await settle();                      // the setting is the browser's: a fresh page comes up locked
out.afterReload = await layout();
out.lockedDrag = await drag("locked: the same drag");
// a KEYBOARD press on the lock (round one, LOW 2): the strip rebuilds, and the focus stays on the lock, not the active tab
const activeCls = () => page.evaluate(() => (document.activeElement && document.activeElement.className) || "");
await page.focus("#tabs .tab-lockbox .tab-lock"); await page.keyboard.press("Enter"); await page.waitForTimeout(400);
out.keyToggle = { active: await activeCls(), store: (await layout()).store };
await page.keyboard.press("Enter"); await page.waitForTimeout(400);   // and back to locked for the scenes below
out.keyToggleBack = { active: await activeCls(), store: (await layout()).store };
// THE SESSIONS PANE (round one, MEDIUM 1): the landing's timeline shares the order with the strip; its lane drag is held too
const shell = await ctx.newPage(); await shell.goto(cfg.landing);   // the same context: the lock's store is shared
await shell.waitForSelector("#rail-gear", { timeout: 20000 });
let tl = shell.frames().find((f) => f.url().includes("/timeline"));
for (let i = 0; i < 100 && !tl; i++) { await shell.waitForTimeout(100); tl = shell.frames().find((f) => f.url().includes("/timeline")); }
await tl.waitForFunction((names) => Array.from(document.querySelectorAll("svg text")).filter((t) => names.includes(t.textContent.trim())).length >= names.length, cfg.names, { timeout: 30000 });
await shell.waitForTimeout(600);
const lanes = () => tl.evaluate((names) => {
  const labels = Array.from(document.querySelectorAll("svg text")).filter((t) => names.includes(t.textContent.trim()))
    .map((t) => { const r = t.getBoundingClientRect(); return { name: t.textContent.trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 }; }).sort((a, b) => a.y - b.y);
  const rects = Array.from(document.querySelectorAll("svg rect"));
  return { labels, grabRects: rects.filter((q) => q.style.cursor === "grab").length, titles: Array.from(document.querySelectorAll("svg rect > title")).map((q) => q.textContent) };
}, cfg.names);
const frameBox = await shell.evaluate(() => { const f = document.getElementById("f-timeline").getBoundingClientRect(); return { x: f.left, y: f.top }; });
async function laneDrag(label) {
  const pre = await lanes();
  if (pre.labels.length < 3) return { label, skipped: "lanes: " + pre.labels.length };
  const src = pre.labels[2], tgt = pre.labels[0];
  await shell.mouse.move(frameBox.x + src.x, frameBox.y + src.y); await shell.mouse.down();
  await shell.mouse.move(frameBox.x + src.x, frameBox.y + src.y - 8, { steps: 3 });     // past the axis threshold, vertically
  await shell.mouse.move(frameBox.x + tgt.x, frameBox.y + tgt.y - 4, { steps: 12 });
  await shell.mouse.up(); await shell.waitForTimeout(900);
  const post = await lanes();
  return { label, dragged: src.name, preOrder: pre.labels.map((l) => l.name), postOrder: post.labels.map((l) => l.name),
           moved: pre.labels.map((l) => l.name).join() !== post.labels.map((l) => l.name).join(), grabRectsPre: pre.grabRects, titlesPre: pre.titles };
}
out.tlLocked = await laneDrag("locked: the 3rd lane to the top");
// the screenshots: the strip with the lock on, dark then light
for (const theme of ["dark", "light"]) {
  await page.evaluate((t) => document.body.classList.toggle("theme-light", t === "light"), theme);
  await page.waitForTimeout(200);
  if (cfg.shots) {
    const bar = await page.evaluate(() => { const b = document.getElementById("tabbar").getBoundingClientRect(); return { x: 0, y: Math.max(0, b.top - 4), width: Math.min(window.innerWidth, 900), height: b.height + 8 }; });
    await page.screenshot({ path: cfg.shots + "-locked-" + theme + ".png", clip: bar });
  }
}
await page.evaluate(() => document.body.classList.remove("theme-light"));
out.pressedAgain = await press();
out.unlocked = await layout();
out.unlockedAgainDrag = await drag("unlocked again: the same drag");
out.tlUnlocked = await laneDrag("unlocked: the 3rd lane to the top");   // the timeline reads the same store: the drag moves the lane
await shell.close();
fs.writeFileSync(cfg.out, JSON.stringify(out));
console.log("RESULT: ok");
await browser.close();
"""


class ServedTabLock(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="tab-lock-")
        before = os.environ.get("TAB_LOCK_DIST", "")
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
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # this lab mints its own state root: no session host (repo rule)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, name in enumerate(NAMES):
            sid = SIDS[name]
            bg, fg = PALETTE[i % len(PALETTE)]
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
        cls.port, cls.token = _free_port(), "testtok-tablock"
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
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill(); k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

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
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "landing": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token),
                       "count": len(NAMES), "names": NAMES, "out": out, "shots": os.environ.get("TAB_LOCK_SHOTS", "")}, f)
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
        if os.environ.get("TAB_LOCK_DUMP"):
            Path(os.environ["TAB_LOCK_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def test_the_lock_sits_after_the_plus_tab_and_before_the_tags_box_in_a_box_of_the_tags_boxs_height(self):
        s = self._run()["start"]
        table = "\n  " + json.dumps(s["lock"])
        self.assertTrue(s["lock"]["present"], "the strip carries the lock box" + table)
        lk = s["lock"]
        self.assertEqual(lk["prev"], "tab tab-add", "right after the + tab" + table)
        self.assertEqual(lk["next"], "tab-tagbox", "before the tags box" + table)
        self.assertGreaterEqual(lk["boxH"], 31 - 0.5, "the tags box's floor" + table)
        self.assertLessEqual(abs(lk["boxH"] - lk["tagBoxH"]), 0.5, "the same height as the tags box" + table)
        self.assertTrue(lk["svg"], "the padlock glyph" + table)
        self.assertEqual(lk["shackle"], "M9.4 6.2 V5.3 A2.4 2.4 0 0 1 13.6 3.7", "unlocked: the shackle swung out (the Sessions pane's drawing)" + table)
        self.assertEqual((lk["label"], lk["pressed"], lk["on"]), ("Lock tabs", "false", False), table)
        self.assertEqual(lk["radius"], "6px", "a little rounded box" + table)
        # round two, LOW 1: like the tags thing beside it, measured side by side: the same border colour and the same box, to the pixel
        tb = s["tagBtn"]; table2 = "\n  lock=" + json.dumps({k: lk[k] for k in ("w", "h", "border", "radius")}) + " tag=" + json.dumps(tb)
        self.assertIsNotNone(tb, "the tag button beside it" + table2)
        self.assertEqual((lk["w"], lk["h"]), (tb["w"], tb["h"]), "the same box as the tag button" + table2)
        self.assertEqual(lk["border"], tb["border"], "the same border colour as the tag button (one source)" + table2)
        self.assertEqual(lk["radius"], tb["radius"], table2)
        self.assertNotEqual(lk["color"], s["accent"], "gray at rest, not the accent" + table)
        self.assertEqual(s["store"]["tabsLocked"], "absent", "nothing written until pressed" + table)

    def test_locked_the_drag_moves_nothing_and_unlocked_the_same_drag_moves_the_tab(self):
        r = self._run()
        u = r["unlockedDrag"]
        self.assertTrue(u["moved"] and u["landedFirst"], "the control: with the lock off the 3rd tab lands first: " + json.dumps(u))
        self.assertTrue(r["pressed"], "the lock was pressed: " + json.dumps(r["locked"]["lock"]))
        lk = r["locked"]
        self.assertEqual((lk["lock"]["on"], lk["lock"]["pressed"], lk["store"]["tabsLocked"]), (True, "true", True), json.dumps(lk["lock"]) + json.dumps(lk["store"]))
        self.assertTrue(all(not t["draggable"] for t in lk["tabs"]), "no tab is draggable while locked: " + json.dumps(lk["tabs"]))
        ar = r["afterReload"]
        self.assertEqual((ar["lock"]["on"], ar["store"]["tabsLocked"]), (True, True), "the setting is the browser's: a fresh page comes up locked: " + json.dumps(ar["lock"]))
        d = r["lockedDrag"]
        self.assertFalse(d["moved"], "locked: the same drag moves nothing: " + json.dumps(d))
        self.assertEqual(d["preOrder"], d["postOrder"], json.dumps(d))
        self.assertEqual(d["ev"]["dragstart"], 0, "no drag ever began (the tab is not draggable): " + json.dumps(d["ev"]))
        self.assertTrue(r["pressedAgain"])
        un = r["unlocked"]
        self.assertEqual((un["lock"]["on"], un["lock"]["pressed"], un["store"]["tabsLocked"]), (False, "false", False), json.dumps(un["lock"]))
        self.assertTrue(all(t["draggable"] for t in un["tabs"]), json.dumps(un["tabs"]))
        a = r["unlockedAgainDrag"]
        self.assertTrue(a["moved"] and a["landedFirst"], "unlocked again: the drag moves the tab: " + json.dumps(a))

    def test_a_keyboard_press_on_the_lock_keeps_the_focus_on_the_lock(self):
        # round one, LOW 2: the rebuild used to treat any focus inside the strip as a held tab and threw it onto the active tab
        r = self._run()
        self.assertIn("tab-lock", r["keyToggle"]["active"], "after Enter the lock still has the focus: " + json.dumps(r["keyToggle"]))
        self.assertEqual(r["keyToggle"]["store"]["tabsLocked"], False, "Enter toggled it (unlocked)")
        self.assertIn("tab-lock", r["keyToggleBack"]["active"], json.dumps(r["keyToggleBack"]))
        self.assertEqual(r["keyToggleBack"]["store"]["tabsLocked"], True, "and back")

    def test_the_sessions_pane_lane_drag_is_held_by_the_lock_and_moves_once_unlocked(self):
        # round one, MEDIUM 1: the timeline shares the order with the strip, so its lane drag wrote a new order while every tab read
        # draggable false
        r = self._run()
        lk, un = r["tlLocked"], r["tlUnlocked"]
        self.assertNotIn("skipped", lk, json.dumps(lk)); self.assertNotIn("skipped", un, json.dumps(un))
        self.assertFalse(lk["moved"], "locked: the lane drag moves nothing: " + json.dumps(lk))
        self.assertEqual(lk["grabRectsPre"], 0, "locked: no lane offers the grab cursor: " + json.dumps(lk))
        self.assertTrue(any("locked" in t for t in lk["titlesPre"]), "locked: the lanes say why on hover: " + json.dumps(lk["titlesPre"][:3]))
        self.assertTrue(un["moved"], "unlocked: the same drag moves the lane: " + json.dumps(un))
        self.assertEqual(un["postOrder"][0], un["dragged"], json.dumps(un))
        self.assertGreater(un["grabRectsPre"], 0, "unlocked: the lanes offer the grab cursor again")

    def test_the_locked_dress_is_the_accent_on_glyph_and_outline_with_no_fill(self):
        r = self._run()
        lk = r["locked"]["lock"]
        table = "\n  " + json.dumps(lk) + " accent=" + r["locked"]["accent"]
        self.assertTrue(lk.get("present"), table)
        self.assertEqual(lk["color"], r["locked"]["accent"], "the glyph wears the accent" + table)
        self.assertEqual(lk["border"], r["locked"]["accent"], "…and the outline" + table)
        self.assertEqual(lk["bg"], "rgba(0, 0, 0, 0)", "no colour fill" + table)
        self.assertEqual(lk["shackle"], "M4.8 6.2 V4.4 a2.2 2.2 0 0 1 4.4 0 V6.2", "locked: the shackle seated" + table)
        self.assertIn("locked", lk["title"], table)


if __name__ == "__main__":
    unittest.main()
