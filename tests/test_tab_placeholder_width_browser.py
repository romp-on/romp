"""The loading placeholder tab keeps its final width (plans/tab-placeholder-width.md, the user 2026-09-19): a hermetic kernel over
six synthetic sessions in the notes-api demo world, the real chat page served from a copy of the built bundle, driven by
Playwright. An observer installed before the page's scripts records every placeholder tab at the moment it is inserted (its
width, its status dot slot's rectangle, the swirl inside the slot) and, once every tab has loaded, the same tab's width and dot
rectangle: equal within a pixel; the swirl visible while loading and gone after; the dot slot's later state painting where the
swirl was. Synthetic only (placeholder ids, invented names)."""
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.dist_copy import copy_dist  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab  # noqa: E402  the lab kernel's environment

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
try { browser = await chromium.launch({}); } catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
const errors = []; page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
// the observer runs before the page's scripts: every placeholder tab is measured the instant it is inserted (the layout forced
// by the read), so a short loading window still leaves a record
await page.addInitScript(() => {
  window.__ph = {};
  const rect = (e) => { if (!e) return null; const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, w: r.width, h: r.height }; };
  const note = (t) => { const id = t.dataset.id; if (!id || window.__ph[id]) return;
    const dot = t.querySelector(".tab-dot"), sw = t.querySelector(".tab-ph-swirl");
    window.__ph[id] = { w: rect(t).w, dot: rect(dot), swirl: !!sw, swirlShown: sw ? (getComputedStyle(sw).display !== "none" && getComputedStyle(sw).visibility !== "hidden" && rect(sw).w > 0) : false,
                        swirlInSlot: !!(sw && dot && dot.contains(sw)), swirlRect: rect(sw), label: (t.querySelector(".tab-label") || {}).textContent || "" }; };
  const scan = (n) => { if (!(n instanceof Element)) return; if (n.matches && n.matches(".tab.tab-placeholder")) note(n); for (const t of n.querySelectorAll ? n.querySelectorAll(".tab.tab-placeholder") : []) note(t); };
  new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) scan(n); }).observe(document, { childList: true, subtree: true });   // the init script runs before the document has its root element: watch the document node itself
});
await page.goto(cfg.chat);
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n && document.querySelectorAll("#tabs .tab.tab-placeholder").length === 0, cfg.count, { timeout: 60000 });
await page.waitForTimeout(800);   // the strip at rest
const loaded = await page.evaluate(() => {
  const rect = (e) => { if (!e) return null; const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, w: r.width, h: r.height }; };
  const out = {};
  for (const t of document.querySelectorAll("#tabs .tab[data-id]")) out[t.dataset.id] = { w: rect(t).w, dot: rect(t.querySelector(".tab-dot")), dotClass: (t.querySelector(".tab-dot") || {}).className || null,
    swirls: t.querySelectorAll(".tab-ph-swirl").length, cls: t.className };
  return { tabs: out, swirlsInStrip: document.querySelectorAll("#tabs .tab-ph-swirl").length };
});
const ph = await page.evaluate(() => window.__ph);
process.stdout.write("RESULT:" + JSON.stringify({ ph, loaded, errors }) + "\n");
await browser.close();
"""


class TabPlaceholderWidthServed(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here): the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="tab-ph-width-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
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
        cls.port, cls.token = _free_port(), "testtok-tabph"
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
            cls.kernel.kill()
            cls._skip("hermetic kernel never served /healthz here")
        cls._r = None

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _result(self):
        if getattr(type(self), "_fail", None):
            self.fail(type(self)._fail)
        if self._r is None:
            cfg = os.path.join(self.lab, "tabph.json")
            base = "http://127.0.0.1:%d" % self.port
            with open(cfg, "w") as f:
                json.dump({"chat": base + "/chat?token=" + self.token, "token": self.token, "count": len(NAMES)}, f)   # the chat page, where the strip lives
            driver = os.path.join(self.lab, "tabph.mjs")
            Path(driver).write_text(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=400,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if "browser-launch-failed" in p.stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            if not line:
                type(self)._fail = "the driver produced no RESULT (stderr: %s; kernel: %s)" % (p.stderr[-2000:], open(self.klog).read()[-1500:])
                self.fail(type(self)._fail)
            type(self)._r = json.loads(line[len("RESULT:"):])
        return self._r

    def test_a_loading_tab_is_as_wide_as_the_loaded_one_and_its_swirl_rides_the_dot_slot_then_yields_to_the_dot(self):
        r = self._result()
        self.assertEqual(r["errors"], [], "no page error")
        ph, loaded = r["ph"], r["loaded"]["tabs"]
        self.assertGreaterEqual(len(ph), 1, "at least one placeholder tab was observed while loading (the lab cannot judge widths otherwise): %r" % list(loaded))
        for sid, p in ph.items():
            self.assertIn(sid, loaded, "the placeholder's session loaded: %s" % sid)
            self.assertLessEqual(abs(p["w"] - loaded[sid]["w"]), 1.0, "the tab keeps its width from the first paint (%s): loading %.1f, loaded %.1f" % (p["label"], p["w"], loaded[sid]["w"]))
            self.assertTrue(p["swirl"] and p["swirlShown"], "the swirl was visible while loading (%s): %r" % (p["label"], p))
            self.assertTrue(p["swirlInSlot"], "the swirl rode the status dot's slot (%s)" % p["label"])
            self.assertIsNotNone(p["dot"], "the loading tab had the dot slot (%s)" % p["label"])
            self.assertIsNotNone(loaded[sid]["dot"], "the loaded tab has the dot slot (%s)" % p["label"])
            for k in ("l", "w"):
                self.assertLessEqual(abs(p["dot"][k] - loaded[sid]["dot"][k]), 1.0, "the dot slot's later state paints where the swirl was (%s, %s): %r vs %r" % (p["label"], k, p["dot"], loaded[sid]["dot"]))
            self.assertEqual(loaded[sid]["swirls"], 0, "the swirl is gone once loaded (%s)" % p["label"])
        self.assertEqual(r["loaded"]["swirlsInStrip"], 0, "no swirl left anywhere in the strip")


if __name__ == "__main__":
    unittest.main()
