#!/usr/bin/env python3
"""T414 (the user 2026-09-15): with the feed's focused-session section on, a click on a card's SUMMARY line that jumps
into another session (the chat's active tab changes, the kernel relays activeChat) scrolls the feed's box to the TOP,
so the section, which now shows that session's cards, is in view; the feed used to stay where the reader was, with the
cards it just switched to above the fold.

The rules (ui/webview/feed.ts, noteFocusJump / settleFocusScroll / scrollFeedTop): the summary click records the session
it jumps to; the activeChat frame for that session settles it with one plain scroll to the top after the section
repainted (no animated chase); a user scroll in between cancels it, and the scroll stands until the reader scrolls
again; a click on the session ALREADY focused changes no tab, so it scrolls to the top at once; with the section off
nothing changes; the same in the row and the single-column layouts.

The served lab drives the real /feed page from a hermetic kernel with a SYNTHETIC payload (the notes-api demo world:
`web` with eight Working cards so the list scrolls, `api` with one Completed card carrying a summary and its anchor), the
section switched on through the View menu, and walks these roads in one browser run:
  (a) row layout, web focused, the list scrolled 600 px down: a click on api's summary line posts the jump (showOnTimeline),
      the pointer parks off the card (the hover-freeze releases), the kernel's activeChat frame for api lands, and the
      list reads scrollTop 0 with the section's first block head inside the list's viewport;
  (b) the reader scrolls between the click and the frame: the pending scroll yields and the list stays where they put it;
  (c) a summary click on the session already focused (api, scrolled down): the top at once, no frame needed;
  (d) the section OFF: the same click and frame move the list by nothing;
  (e) the single-column layout (a 520 px viewport): road (a) again.
Screenshots with FEED_FOCUS_SCROLL_SHOTS=<path-prefix>: -1-row-dark/-light and -2-stacked-dark/-light, the list at the top
with the switched section in view. Optional, never a skip. Skips LOUDLY without the extension deps or a Playwright
browser, and for nothing else (ROMP_SERVED_TESTS_REQUIRE=1 turns those two red where the browser is installed); a build
or kernel failure is a failure. Source pins ride ui/webview/feed-focus-scroll.test.ts. All fixtures synthetic.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment

SID_WEB = "aaaaaaaa-1111-2222-3333-777777777777"
SID_API = "aaaaaaaa-1111-2222-3333-888888888888"
API_DONE = "cccccccc-1111-2222-3333-000000000021"
API_ANCHOR = "dddddddd-1111-2222-3333-000000000001"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text().slice(0, 300)); });
// capture what the page posts to its host (the jump is a showOnTimeline message)
await page.addInitScript(() => {
  window.__posted = [];
  let real;
  Object.defineProperty(window, "acquireVsCodeApi", {
    configurable: true,
    get() { return function () { const api = real ? real() : {}; const post = api.postMessage ? api.postMessage.bind(api) : () => {};
      api.postMessage = (m) => { window.__posted.push(m); return post(m); }; return api; }; },
    set(fn) { real = fn; },
  });
});
const ready = async () => {
  await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
  await page.waitForTimeout(600);
};
const now = Math.floor(Date.now() / 1000);
const ask = (itemId, sid, name, column, text, t, extra) => Object.assign({ itemId, sid, name, color: cfg.colors[name], text, t, live: true,
  turnId: "turn-" + itemId.slice(-2), trgb: [30, 161, 235], column, tree: [] }, extra || {});
const asks = [];
for (let i = 0; i < 8; i++) asks.push(ask("cccccccc-1111-2222-3333-0000000000" + String(10 + i), cfg.web, "web", "working", "notes-api: working step " + (i + 1), now - 60 * (i + 1)));
asks.push(ask(cfg.apiDone, cfg.api, "api", "completed", "notes-api: wire the tag filter", now - 120,
  { summary: "The tag filter is wired end to end; the list endpoint accepts a tag and returns only matching notes.", summaryAnchorUuid: cfg.apiAnchor }));
const payload = { type: "feed", asks, sessions: [{ sid: cfg.web, name: "web" }, { sid: cfg.api, name: "api" }], order: [cfg.web, cfg.api] };
const deliver = (m) => page.evaluate((m) => new Promise((res) => { window.dispatchEvent(new MessageEvent("message", { data: m })); requestAnimationFrame(() => res(null)); }), m);
const frame = () => page.evaluate(() => new Promise((res) => requestAnimationFrame(() => res(null))));
const park = async () => { await page.mouse.move(2, 2); await page.waitForTimeout(150); };
const state = () => page.evaluate(() => {
  const list = document.getElementById("feed-list"), sec = document.getElementById("feed-focus");
  const lr = list.getBoundingClientRect();
  const head = sec ? sec.querySelector(".feed-focus-cols .feed-col:not(.col-empty) .feed-col-head") : null;
  const hr = head ? head.getBoundingClientRect() : null;
  return { scrollTop: list.scrollTop, scrollHeight: list.scrollHeight, clientHeight: list.clientHeight,
           section: !!sec, name: sec ? (sec.querySelector(".feed-focus-head .fname") || {}).textContent || null : null,
           headInView: hr ? (hr.top >= lr.top - 0.5 && hr.bottom <= lr.bottom + 0.5) : null, headTop: hr ? hr.top - lr.top : null,
           posted: (window.__posted || []).filter((m) => m && m.type === "showOnTimeline").map((m) => m.sid) };
});
const scrollList = (top) => page.evaluate((top) => { const l = document.getElementById("feed-list"); l.scrollTop = top; return l.scrollTop; }, top);
const light = async (on) => { await page.evaluate((on) => { document.body.classList[on ? "add" : "remove"]("chat-theme-yatharth", "theme-light"); }, on); await page.waitForTimeout(250); };
const shotBoth = async (name) => { if (!cfg.shots) return; await page.screenshot({ path: `${cfg.shots}-${name}-dark.png` }); await light(true); await page.screenshot({ path: `${cfg.shots}-${name}-light.png` }); await light(false); };
const clickSummary = async () => { await page.click(`#feed-cols [data-key="a:${cfg.apiDone}"] .fask-distill`); await frame(); };
const out = {};
await page.goto(cfg.feed);
await ready();
await deliver(payload);
await page.waitForSelector(`#feed-cols [data-key="a:${cfg.apiDone}"]`, { timeout: 10000 });
await park();
// the section on, web focused
await page.click("#feed-viewbtn");
await page.waitForSelector(".feed-viewmenu .ctx-item", { timeout: 10000 });
await page.locator(".feed-viewmenu .ctx-item", { hasText: "Show focused session" }).click();
await page.waitForSelector(".feed-viewmenu", { state: "detached", timeout: 10000 });
await park();
await deliver({ type: "activeChat", id: cfg.web });
await frame(); await park();
out.start = await state();
// (a) scrolled 600 px down, the summary click jumps to api; the frame lands after the pointer parks
out.scrolledA = await scrollList(600);
await clickSummary();
out.afterClick = await state();
await park();
await deliver({ type: "activeChat", id: cfg.api });
await frame(); await park();
out.roadA = await state();
await shotBoth("1-row");
// (b) the reader scrolls between the click and the frame: the pending scroll yields
await deliver({ type: "activeChat", id: cfg.web });
await frame(); await park();
await scrollList(600);
await clickSummary();
await park();
out.scrolledB = await scrollList(300);   // the reader's own scroll (a real scroll event, outside the feed's guard)
await frame();
await deliver({ type: "activeChat", id: cfg.api });
await frame(); await park();
out.roadB = await state();
// (c) api already focused: a summary click on its own card scrolls to the top at once, no frame
await scrollList(600);
await clickSummary();
await frame();
out.roadC = await state();
await park();
// (d) the section OFF: the same click and frame move the list by nothing
await page.click("#feed-viewbtn");
await page.waitForSelector(".feed-viewmenu .ctx-item", { timeout: 10000 });
await page.locator(".feed-viewmenu .ctx-item", { hasText: "Show focused session" }).click();
await page.waitForSelector(".feed-viewmenu", { state: "detached", timeout: 10000 });
await park();
await deliver({ type: "activeChat", id: cfg.web });
await frame(); await park();
out.scrolledD = await scrollList(400);
await clickSummary();
await park();
await deliver({ type: "activeChat", id: cfg.api });
await frame(); await park();
out.roadD = await state();
// (e) single column: the section on again, web focused, scrolled, the jump to api
await page.click("#feed-viewbtn");
await page.waitForSelector(".feed-viewmenu .ctx-item", { timeout: 10000 });
await page.locator(".feed-viewmenu .ctx-item", { hasText: "Show focused session" }).click();
await page.waitForSelector(".feed-viewmenu", { state: "detached", timeout: 10000 });
await park();
await page.setViewportSize({ width: 520, height: 760 });
await deliver({ type: "activeChat", id: cfg.web });
await frame(); await park();
out.scrolledE = await scrollList(600);
await clickSummary();
await park();
await deliver({ type: "activeChat", id: cfg.api });
await frame(); await park();
out.roadE = await state();
await shotBoth("2-stacked");
out.errors = errors;
fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
process.exit(0);
"""


class ServedFocusedSectionJumpScroll(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="feedfocusscroll-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise AssertionError("esbuild failed here: " + (b.stderr or b.stdout)[-400:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "session-hosts"), "w") as fh:   # a lab root of its own pins the hosts OFF (CLAUDE.md 2026-09-11)
            fh.write("off\n")
        cls.port = _free_port()
        cls.token = "testtok-feedfocusscroll"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):   # bounded: 60 s of half-second probes
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            cls.kernel.wait()
            raise AssertionError("hermetic kernel never served /healthz here; log tail:\n" + open(cls.klog).read()[-1500:])

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_a_summary_click_that_switches_the_focused_session_scrolls_the_feed_to_the_top(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token),
                       "web": SID_WEB, "api": SID_API, "apiDone": API_DONE, "apiAnchor": API_ANCHOR,
                       "colors": {"web": {"bg": "#1EA1EB", "fg": "#ffffff"}, "api": {"bg": "#E0A526", "fg": "#000000"}},
                       "out": os.path.join(self.lab, "result.json"),
                       "shots": os.environ.get("FEED_FOCUS_SCROLL_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        out_path = os.path.join(self.lab, "result.json")
        self.assertTrue(os.path.exists(out_path), "driver wrote no result file:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        with open(out_path) as fh:
            r = json.load(fh)
        self.assertEqual(r.get("errors"), [], "the page threw nothing: %r" % r.get("errors"))
        st = r["start"]
        self.assertTrue(st["section"] and st["name"] == "web", "the section is on and web is focused: %r" % st)
        self.assertGreater(st["scrollHeight"], st["clientHeight"] + 600, "the world is tall enough for a 600 px scroll: %r" % st)
        # (a) the jump: posted, then the frame lands, then the top with the section's head in view
        self.assertEqual(r["scrolledA"], 600, "the list was scrolled 600 px down")
        self.assertEqual(r["afterClick"]["posted"], [SID_API], "the summary click posted the jump for api: %r" % r["afterClick"]["posted"])
        self.assertEqual(r["afterClick"]["scrollTop"], 600, "the click alone moves nothing: the frame is the event: %r" % r["afterClick"])
        a = r["roadA"]
        self.assertEqual((a["name"], a["scrollTop"], a["headInView"]), ("api", 0, True), "the frame for api: the section shows api, the list is at the top, the block head in view: %r" % a)
        # (b) a user scroll between the click and the frame: the pending scroll yields. The section shrinks when api's one
        # card replaces web's eight, so the browser clamps the reader's position to the shorter document: the list stays
        # where they put it as far as the content allows, and never goes to the top
        b = r["roadB"]
        self.assertEqual(r["scrolledB"], 300, "the reader scrolled to 300 px between the click and the frame")
        self.assertGreater(b["scrollTop"], 0, "the pending jump scroll yielded to the reader's scroll: %r" % b)
        self.assertEqual(b["scrollTop"], min(300, b["scrollHeight"] - b["clientHeight"]), "…and the list stands where they put it, clamped to the shorter document: %r" % b)
        # (c) the session already focused: the top at once
        self.assertEqual((r["roadC"]["name"], r["roadC"]["scrollTop"]), ("api", 0), "a summary click on the focused session's own card scrolls to the top at once: %r" % r["roadC"])
        # (d) the section off: nothing moves
        d = r["roadD"]
        self.assertGreater(r["scrolledD"], 0, "the list was scrolled with the section off")
        self.assertEqual((d["section"], d["scrollTop"]), (False, min(r["scrolledD"], d["scrollHeight"] - d["clientHeight"])), "with the section off the click and the frame move the list by nothing (a clamp aside): %r" % d)
        # (e) single column
        self.assertEqual(r["scrolledE"], 600)
        e = r["roadE"]
        self.assertEqual((e["name"], e["scrollTop"], e["headInView"]), ("api", 0, True), "single column: the same jump scroll: %r" % e)


if __name__ == "__main__":
    unittest.main()
