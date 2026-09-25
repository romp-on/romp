#!/usr/bin/env python3
"""The chat's scrollbar in both themes and on both kinds of platform (the user 2026-09-25, on macOS in the light theme, who
could not see the transcript's scrollbar even while scrolling and wants the platform's show-while-scrolling bars).

Two causes, both in the page's sheets: the chat's thumb and track were white alphas chosen for the dark page, invisible on
the light theme's cream; and any ::-webkit-scrollbar styling makes Blink and WebKit drop the platform's overlay scrollbars
for classic always-on ones, so romp's styled bars overrode a macOS "show scroll bars when scrolling" preference everywhere.

The served guard drives the real /chat page from a hermetic kernel over a synthetic transcript (forty long exchanges in a
`web` session of notes-api), scrolled half way, in two browsers:
  * CLASSIC scrollbars (headless Chromium on Linux with Playwright's --hide-scrollbars dropped): no overlay class, the
    styled 10px bar, and in the LIGHT theme the thumb's painted pixels read against the page at the 3:1 non-text floor
    (on main they are a white alpha on cream, about 1.1:1); the dark thumb keeps its 2:1. Forcing the overlay class drops
    every custom rule: the bar falls back to Chromium's own 15px one.
  * OVERLAY scrollbars (the same browser with --enable-features=OverlayScrollbar, which draws overlay bars the way macOS
    does and, like macOS, gives them up for classic ones under any ::-webkit-scrollbar rule): the page's probe sets the
    overlay class, the chat's scroller takes no gutter (the platform's bar overlays it), and the probe re-measures on the
    window's focus and on visibilitychange.
In both themes the body carries the theme's color-scheme (the native bars follow it) and the root carries none: a dark page
shown in an iframe of a parent with the default scheme lets the parent through a transparent page (a root scheme would
paint an opaque backdrop there, the lifted panels' black-out).

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none); the CI-safe pins ride
ui/webview/scrollbar-overlay.test.ts and ui/webview/theme-parity.test.ts. Pass SCROLLBAR_SHOTS=<dir> for PNGs of the chat
scrolled half way in each theme and browser. All fixtures synthetic.
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
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID = "aaaaaaaa-1111-2222-3333-777777777777"
PROMPTS = 40
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"


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
if (cfg.shots) fs.mkdirSync(cfg.shots, { recursive: true });
const LAUNCH = {
  classic: { ignoreDefaultArgs: ["--hide-scrollbars"] },
  overlay: { ignoreDefaultArgs: ["--hide-scrollbars"], args: ["--enable-features=OverlayScrollbar"] },
};
const THEMES = { dark: "classic", light: "yatharth-light" };
// a PNG screenshot's pixels, decoded in the page through a canvas
const pixels = async (page, clip) => {
  const b64 = (await page.screenshot({ clip })).toString("base64");
  return page.evaluate(async (b64) => {
    const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
    const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
    const x = c.getContext("2d"); x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, img.width, img.height).data, out = [];
    for (let i = 0; i < d.length; i += 4) out.push([d[i], d[i + 1], d[i + 2]]);
    return out;
  }, b64);
};
const openChat = async (browser, theme) => {
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 640 }, deviceScaleFactor: 1 });
  await ctx.addInitScript((t) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, THEMES[theme]);
  const page = await ctx.newPage();
  await page.goto(cfg.chat);
  await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
  await page.waitForFunction(() => { const c = document.getElementById("content"); return c && c.scrollHeight > c.clientHeight * 4; }, null, { timeout: 20000 });
  await page.waitForTimeout(800);
  // half way up, by the reader's own gesture (a programmatic scrollTop is not a reader's, and the landing re-lands it)
  const box = await page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await page.mouse.move(box.x, box.y);
  for (let i = 0; i < 40; i++) {   // loop-ok: bounded, each step waited on
    const f = await page.evaluate(() => { const c = document.getElementById("content"); return c.scrollTop / Math.max(1, c.scrollHeight - c.clientHeight); });
    if (f <= 0.5) break;
    await page.mouse.wheel(0, -900);
    await page.waitForTimeout(120);
  }
  await page.waitForTimeout(600);
  return { ctx, page };
};
const survey = (page) => page.evaluate(() => {
  const c = document.getElementById("content"), r = c.getBoundingClientRect();
  return {
    overlayClass: document.documentElement.classList.contains("overlay-scrollbars"),
    light: document.body.classList.contains("theme-light"),
    gutter: c.offsetWidth - c.clientWidth,
    frac: c.scrollTop / Math.max(1, c.scrollHeight - c.clientHeight),
    bodyScheme: getComputedStyle(document.body).colorScheme,
    rootScheme: getComputedStyle(document.documentElement).colorScheme,
    page: getComputedStyle(document.body).backgroundColor,
    rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom },
  };
});
// the scrollbar column's colours: the overlays that ride the gutter (the notches, the glow ruler) hidden for the read
const barColours = async (page, s) => {
  await page.evaluate(() => document.querySelectorAll(".scroll-marks, .glow-ruler").forEach((e) => { e.style.visibility = "hidden"; }));
  const x = Math.floor(s.rect.right - Math.max(1, s.gutter) / 2);
  const col = await pixels(page, { x, y: Math.ceil(s.rect.top) + 2, width: 1, height: Math.floor(s.rect.bottom - s.rect.top) - 4 });
  await page.evaluate(() => document.querySelectorAll(".scroll-marks, .glow-ruler").forEach((e) => { e.style.visibility = ""; }));
  const counts = new Map();
  for (const p of col) { const k = p.join(","); counts.set(k, (counts.get(k) || 0) + 1); }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, n]) => ({ rgb: k.split(",").map(Number), n }));
};
const out = {};
for (const mode of ["classic", "overlay"]) {
  let browser;
  try { browser = await chromium.launch(LAUNCH[mode]); }
  catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
  out[mode] = {};
  for (const theme of ["dark", "light"]) {
    const { ctx, page } = await openChat(browser, theme);
    const r = { s: await survey(page) };
    if (cfg.shots) await page.screenshot({ path: `${cfg.shots}/${mode}-${theme}.png` });
    if (mode === "classic") {
      r.bar = await barColours(page, r.s);
      if (cfg.shots) await page.screenshot({ path: `${cfg.shots}/${mode}-${theme}-bar.png`,
        clip: { x: Math.floor(r.s.rect.right) - 60, y: Math.ceil(r.s.rect.top), width: 60, height: Math.floor(r.s.rect.bottom - r.s.rect.top) } });
      // forcing the overlay class drops every custom rule: the bar is the browser's own again
      await page.evaluate(() => document.documentElement.classList.add("overlay-scrollbars"));
      await page.waitForTimeout(100);
      r.forced = await survey(page);
      await page.evaluate(() => document.documentElement.classList.remove("overlay-scrollbars"));
    } else {
      // the platform's overlay bar shows while scrolling: catch it mid-gesture for the picture
      await page.mouse.wheel(0, 300);
      await page.waitForTimeout(150);
      if (cfg.shots) await page.screenshot({ path: `${cfg.shots}/${mode}-${theme}-scrolling.png` });
      // the probe re-measures on the window's focus and on the page coming back into view
      await page.evaluate(() => document.documentElement.classList.remove("overlay-scrollbars"));
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      r.afterFocus = await page.evaluate(() => document.documentElement.classList.contains("overlay-scrollbars"));
      await page.evaluate(() => document.documentElement.classList.remove("overlay-scrollbars"));
      await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
      r.afterVisible = await page.evaluate(() => document.documentElement.classList.contains("overlay-scrollbars"));
    }
    if (mode === "classic" && theme === "dark") {
      // the chat in an iframe of a parent with the default scheme, made transparent the way a lifted panel makes it: the
      // parent's red must show through (a root color-scheme would paint an opaque backdrop over it)
      await page.goto(cfg.origin + "/healthz");
      await page.evaluate((src) => { document.documentElement.style.background = "rgb(255, 0, 0)";
        document.body.innerHTML = `<iframe id=f src="${src}" style="border:0;width:400px;height:300px;background:transparent"></iframe>`; }, cfg.chat);
      const fr = await (await page.waitForSelector("#f")).contentFrame();
      await fr.waitForSelector("#content", { timeout: 20000 });
      await fr.evaluate(() => { document.documentElement.style.background = "transparent"; document.body.style.background = "transparent";
        const st = document.createElement("style"); st.textContent = "body > * { visibility: hidden !important; } body *, body::before { visibility: hidden !important; }"; document.head.appendChild(st); });
      await page.waitForTimeout(200);
      r.throughIframe = (await pixels(page, { x: 200, y: 150, width: 1, height: 1 }))[0];
      r.iframeBodyScheme = await fr.evaluate(() => getComputedStyle(document.body).colorScheme);
    }
    out[mode][theme] = r;
    await ctx.close();
  }
  await browser.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
process.exit(0);
"""


def _lum(rgb):
    def ch(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(css):
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css)
    return [int(m.group(i)) for i in (1, 2, 3)]


class ServedScrollbarThemes(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="scrollbar-themes-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        # the machine's user manager is not the lab's: these shadow systemctl and systemd-run on the lab kernel's PATH
        fakebin = os.path.join(cls.lab, "fakebin")
        os.makedirs(fakebin)
        for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
            Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
            os.chmod(os.path.join(fakebin, tool), 0o755)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = "2026-09-07T10:%02d:00.000Z"
        recs, prev = [], None
        for k in range(PROMPTS):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": t0 % k, "sessionId": SID,
                         "message": {"role": "user", "content": "notes-api: prompt %d, walk me through the retry curve" % k}})
            body = "\n\n".join("Paragraph %d of reply %d about the notes-api retry curve." % (i, k) for i in range(12))
            recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": t0 % k, "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": body}]}})
            prev = a
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.port, cls.token = _free_port(), "testtok-scrollbars"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST", ROMP_CLI_SCOPE="0")
        env["PATH"] = fakebin + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"),
                                      stderr=subprocess.STDOUT, env=env)
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
            k.kill()
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _run(cls):
        """One driver run for every case (two browsers, two themes each); its result is shared."""
        if cls.result is None:
            cfg = os.path.join(cls.lab, "cfg.json")
            origin = "http://127.0.0.1:%d" % cls.port
            with open(cfg, "w") as f:
                json.dump({"chat": "%s/chat?token=%s" % (origin, cls.token), "origin": origin,
                           "shots": os.environ.get("SCROLLBAR_SHOTS", "")}, f)
            driver = os.path.join(cls.lab, "driver.mjs")
            with open(driver, "w") as f:
                f.write(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=400,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if p.returncode == 3:
                raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
            if p.returncode != 0:
                raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            if line is None:
                raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
            cls.result = json.loads(line[len("RESULT:"):])
        return cls.result

    def _thumb(self, r):
        """The thumb's colour in the bar's column: the most painted colour that is not the track's (the most painted)."""
        bar = r["bar"]
        self.assertGreaterEqual(len(bar), 2, "the bar's column paints a track and a thumb: %r" % bar)
        return bar[0]["rgb"], bar[1]["rgb"]

    def test_classic_scrollbars_keep_the_styled_bar_and_the_light_thumb_reads_on_the_page(self):
        r = self._run()["classic"]
        for theme, floor in (("dark", 2.0), ("light", 2.9)):
            s = r[theme]["s"]
            self.assertEqual(s["light"], theme == "light", "%s: the theme applied: %r" % (theme, s))
            self.assertFalse(s["overlayClass"], "%s: a classic platform gets no overlay class: %r" % (theme, s))
            self.assertEqual(s["gutter"], 10, "%s: the styled 10px bar: %r" % (theme, s))
            self.assertTrue(0.2 <= s["frac"] <= 0.8, "%s: the chat is scrolled part way (fixture check): %r" % (theme, s))
            track, thumb = self._thumb(r[theme])
            page = _rgb(s["page"])
            # the thumb must read against the page it sits on, and against its own track (the node test holds the tokens
            # to 3:1 exactly; 2.9 here allows the rasteriser its rounding)
            c_page, c_track = _contrast(thumb, page), _contrast(thumb, track)
            self.assertGreaterEqual(c_page, floor, "%s: thumb %r on the page %r = %.2f:1 (bar column %r)" % (theme, thumb, page, c_page, r[theme]["bar"]))
            self.assertGreaterEqual(c_track, floor, "%s: thumb %r on its track %r = %.2f:1" % (theme, thumb, track, c_track))
        for theme in ("dark", "light"):
            # forcing the overlay class drops every custom rule: Chromium's own classic bar (15px here) comes back
            f = r[theme]["forced"]
            self.assertTrue(f["overlayClass"])
            self.assertNotEqual(f["gutter"], 10, "%s: under the overlay class the styled width no longer applies: %r" % (theme, f))
            self.assertEqual(f["gutter"], 15, "%s: the browser's own bar width: %r" % (theme, f))

    def test_overlay_scrollbars_are_the_platforms_own_and_the_probe_re_measures_on_focus_and_visibility(self):
        r = self._run()["overlay"]
        for theme in ("dark", "light"):
            s = r[theme]["s"]
            self.assertTrue(s["overlayClass"], "%s: the probe found overlay scrollbars and set the class: %r" % (theme, s))
            self.assertEqual(s["gutter"], 0, "%s: the platform's overlay bar takes no gutter (a styled bar took 10px): %r" % (theme, s))
            self.assertTrue(r[theme]["afterFocus"], "%s: the window's focus re-measured and restored the class" % theme)
            self.assertTrue(r[theme]["afterVisible"], "%s: visibilitychange re-measured and restored the class" % theme)

    def test_the_body_carries_the_themes_color_scheme_and_the_root_none(self):
        res = self._run()
        for mode in ("classic", "overlay"):
            for theme in ("dark", "light"):
                s = res[mode][theme]["s"]
                self.assertEqual(s["bodyScheme"], theme, "%s %s: the native bars follow the theme: %r" % (mode, theme, s))
                self.assertEqual(s["rootScheme"], "normal", "%s %s: no scheme on the root: %r" % (mode, theme, s))
        d = res["classic"]["dark"]
        self.assertEqual(d["iframeBodyScheme"], "dark", "the dark page in the iframe wears its scheme: %r" % d)
        self.assertEqual(d["throughIframe"], [255, 0, 0], "a transparent dark page in an iframe lets its parent through: %r" % d)


if __name__ == "__main__":
    unittest.main()
