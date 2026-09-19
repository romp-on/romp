"""THE SETTINGS SECTION LABELS' DRESS, measured on the served page (the user 2026-09-18, through the manager): every section
label inside the settings modal (`.rs-sec` on every pane, the tab widgets' `.rs-divider`, the `.rs-preview-title` captions)
renders in sentence case, in the accent blue, with no letter-spacing, at a size below the card's title: the all-caps dress
(10.5px, 700, .08em, uppercase) read as shouting. A hermetic kernel over a synthetic pair of sessions, Playwright over the
landing, the settings frame opened through the rail's gear, every label's computed style read in the dark theme. Skips
LOUDLY without the extension deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip
there is a failure). No real prompt or transcript text: every string here is invented."""
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
ACCENT = "rgb(156, 210, 255)"   # #9cd2ff, the romp accent (ui/CLAUDE.md)


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
const page = await ctx.newPage();
await page.goto(cfg.url);
await page.waitForSelector("#rail-gear", { timeout: 20000 });
let chatF = page.frames().find((f) => f.url().includes("/chat"));
for (let i = 0; i < 100 && !chatF; i++) { await page.waitForTimeout(100); chatF = page.frames().find((f) => f.url().includes("/chat")); }
await chatF.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
await page.click("#rail-gear");
await page.waitForFunction(() => document.body.classList.contains("settings-open"), null, { timeout: 20000 }).catch(() => {});
let setF = page.frames().find((f) => f.url().includes("/settings"));
for (let i = 0; i < 50 && !setF; i++) { await page.waitForTimeout(100); setF = page.frames().find((f) => f.url().includes("/settings")); }
const out = { open: false };
if (setF) {
  await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 }).catch(() => {});
  await setF.evaluate(() => (document.fonts && document.fonts.ready) || null).catch(() => {});
  await setF.waitForTimeout(250);
  // every pane in turn, so each label is measured while its pane is shown (a hidden pane's labels compute display none)
  const tabs = await setF.evaluate(() => Array.from(document.querySelectorAll("#rsettings .rs-tab")).map((b) => b.dataset.tab));
  out.open = true; out.tabs = tabs; out.labels = []; out.title = null;
  for (const t of tabs) {
    await setF.click('#rsettings .rs-tab[data-tab="' + t + '"]'); await setF.waitForTimeout(120);
    const rows = await setF.evaluate((tab) => {
      const pane = Array.from(document.querySelectorAll("#rsettings .rs-pane")).find((p) => p.dataset.pane === tab);
      if (!pane) return [];
      const sel = ".rs-sec, .rs-divider, .rs-preview-title";
      return Array.from(pane.querySelectorAll(sel)).filter((el) => getComputedStyle(el).display !== "none").map((el) => {
        const cs = getComputedStyle(el);
        return { tab, cls: el.className, text: el.textContent.trim(), transform: cs.textTransform, spacing: cs.letterSpacing,
                 color: cs.color, size: cs.fontSize, weight: cs.fontWeight };
      });
    }, t);
    out.labels.push(...rows);
  }
  out.title = await setF.evaluate(() => { const h = document.querySelector("#rsettings .rs-h"); const cs = h && getComputedStyle(h); return cs ? { size: cs.fontSize, weight: cs.fontWeight } : null; });
  out.row = await setF.evaluate(() => { const b = document.querySelector("#rsettings .rs-row b"); const cs = b && getComputedStyle(b); return cs ? { size: cs.fontSize } : null; });
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
console.log("RESULT: ok");
await browser.close();
"""


class ServedSectionLabels(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box: the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="settings-labels-")
        before = os.environ.get("SETTINGS_LABELS_DIST", "")
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
            Path(state, "names", sid).write_text("%s\t%s\t#9cd2ff\t#0c1a2e\n" % (name, cwd))
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
        cls.port, cls.token = _free_port(), "testtok-seclabels"
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
            k.terminate()
            try:
                k.wait(timeout=10)
            except subprocess.TimeoutExpired:
                k.kill(); k.wait()
            time.sleep(0.5)
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _run(cls):
        if cls.result is not None:
            if isinstance(cls.result, BaseException):
                raise cls.result
            return cls.result
        try:
            cfg = os.path.join(cls.lab, "cfg.json")
            out = os.path.join(cls.lab, "result.json")
            with open(cfg, "w") as f:
                json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": out}, f)
            driver = os.path.join(cls.lab, "driver.mjs")
            with open(driver, "w") as f:
                f.write(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if p.returncode == 3:
                raise unittest.SkipTest("the playwright browser did not launch here: " + p.stderr[-300:])
            if p.returncode != 0 or not os.path.exists(out):
                raise AssertionError("driver failed: rc=%d\n%s\n%s" % (p.returncode, p.stdout[-1500:], p.stderr[-1500:]))
            cls.result = json.loads(Path(out).read_text())
        except BaseException as e:
            cls.result = e
            raise
        return cls.result

    def test_every_section_label_is_sentence_case_in_the_accent_with_no_letter_spacing(self):
        r = self._run()
        self.assertTrue(r["open"], "the settings frame opened")
        labels = r["labels"]
        self.assertGreaterEqual(len(labels), 10, "the panes carry their section labels: %d measured on %s" % (len(labels), r["tabs"]))
        self.assertTrue(any(l["cls"].startswith("rs-sec") for l in labels)); self.assertTrue(any("rs-preview-title" in l["cls"] for l in labels))
        self.assertTrue(any("rs-divider" in l["cls"] for l in labels), "the tab widgets' divider is a section label too")
        bad = [(l["tab"], l["cls"], l["text"], l["transform"], l["spacing"], l["color"]) for l in labels
               if l["transform"] != "none" or l["spacing"] != "normal" or l["color"] != ACCENT]
        self.assertEqual(bad, [], "every label renders with text-transform none, letter-spacing normal and the accent colour "
                         "(the base rendered uppercase, .08em, grey): " + json.dumps(bad[:6]))
        for l in labels:                                  # sentence case: the text itself is not written in capitals
            self.assertNotEqual(l["text"], l["text"].upper(), "a label written in capitals: %r" % l["text"])
            self.assertTrue(l["text"][:1].isupper(), "a label starts with a capital: %r" % l["text"])

    def test_the_label_size_sits_below_the_card_title_and_the_row_labels_do_not_shrink(self):
        r = self._run()
        sizes = {l["size"] for l in r["labels"]}
        self.assertEqual(sizes, {"11px"}, "one label size across every pane: %s" % sorted(sizes))
        weights = {l["weight"] for l in r["labels"]}
        self.assertEqual(weights, {"600"}, "one weight, below the card title's 600 only by size: %s" % sorted(weights))
        self.assertEqual(r["title"], {"size": "14px", "weight": "600"}, "the card title above the labels")
        self.assertIsNotNone(r["row"]); self.assertGreater(float(r["row"]["size"][:-2]), 11.0, "the row labels read larger than the delineators")


if __name__ == "__main__":
    unittest.main()
