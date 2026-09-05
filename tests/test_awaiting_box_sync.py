#!/usr/bin/env python3
"""T225 (the user 2026-09-02): the awaiting box appears the moment the chip says "Awaiting".

Convicted at the source: the kernel assembles the chip state and the box's fields in ONE status
payload and ships a status-only change to a caught-up client as a chatTail DELTA (empty event suffix +
full status; _send_chat). The client's chatTail handler assigned the status and repainted tabs and
statusline — the chip — but rendered the box (renderBgTasks → renderAwaitWhy) only from the full-session
frame path, which a quiet session never sends. Measured here before the fix: the chip read "Awaiting
agents" and the box never appeared in 40s; with the fix, the same frame renders both.

Two guards:
  * SourcePins — CI-safe: every status-carrying handler re-renders the box when the awaiting fields change,
    and the number-agreeing word rides one count (the webview-side twins live in
    ui/webview/awaiting-box-sync.test.ts and spin-caption.test.ts).
  * ServedSync — the executed guard: a hermetic kernel, the real /chat page, MutationObservers on the
    chip and the box; a SYNTHETIC awaiting overlay row is appended to states/<sid>.jsonl — the reader's
    source 1, in the shape sdk_backend.append_awaiting writes plus the optional kind + count fields the
    reader accepts (no live producer writes those two today; the row exercises the reader → chip → box
    path, the same path a real producer's row would take) — and the box must show within one frame of
    the chip; then an awaiting:false row must clear both together. Skips LOUDLY without the extension deps or a playwright browser
    (CI installs none).

All fixtures synthetic.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")

RENDER = open(os.path.join(ROOT, "ui", "webview", "render.ts")).read()

SID = "aaaaaaaa-1111-2222-3333-444444444444"


class SourcePins(unittest.TestCase):
    def test_every_status_carrying_frame_renders_the_box_on_an_awaiting_change(self):
        # chatTail is the frame a status-only change rides to a caught-up client (kernel _send_chat: empty
        # suffix + full status); `update` and the host-side `status` frame assign status the same way
        for fn in ("function chatTail(msg: any) {", "function update(msg: any) {", "function statusOnly(msg: any) {"):
            body = RENDER.split(fn)[1].split("\n}")[0]
            self.assertIn("const before = awaitKey(s.status);", body, fn)
            self.assertIn("if (awaitKey(s.status) !== before) renderBgTasks();", body, fn)

    def test_the_chip_and_the_gist_agree_in_number_from_one_count(self):
        # since slice 2 (plans/subagent-transcripts.md, 2026-09-05) the ONE rule is awaitWord: the kernel's
        # kind + count + the awaited ROWS word the chip and the gist alike ("agent", "3 agents", "4" for mixed)
        self.assertIn("const chipWord = awaitWord(s.status.awaitingKind, s.status.awaitingCount, chipItems);", RENDER)
        self.assertIn("const word = awaitWord(s!.status.awaitingKind, s!.status.awaitingCount, items);", RENDER)


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
const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForSelector("#statusline .chip", { timeout: 20000 });
await page.waitForTimeout(800);
await page.evaluate(() => {
  const w = window; w.__t = { chipOn: null, boxOn: null, chipOff: null, boxOff: null, chipText: "", boxText: "" };
  const chipRead = () => (document.querySelector("#statusline .chip")?.textContent || "");
  const box = () => document.getElementById("bg-tasks");
  const boxVisible = () => { const b = box(); return !!b && b.style.display !== "none" && (b.textContent || "").includes("Awaiting"); };
  const tick = () => {
    const c = chipRead(); const now = performance.now();
    if (/Awaiting/.test(c)) { if (w.__t.chipOn === null) { w.__t.chipOn = now; w.__t.chipText = c; } }
    else if (w.__t.chipOn !== null && w.__t.chipOff === null) w.__t.chipOff = now;
    if (boxVisible()) { if (w.__t.boxOn === null) { w.__t.boxOn = now; w.__t.boxText = (box().textContent || "").slice(0, 160); } }
    else if (w.__t.boxOn !== null && w.__t.boxOff === null) w.__t.boxOff = now;
  };
  new MutationObserver(tick).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
});
// a synthetic overlay row for the reader's source 1: append_awaiting's shape plus the optional kind + count
// fields the reader accepts (no live producer writes those two today — this drives the reader's path)
fs.appendFileSync(cfg.states, JSON.stringify({ t: Math.floor(Date.now() / 1000), awaiting: true, kind: "agents",
  count: 1, why: "1 background agent still working" }) + "\n");
await page.waitForFunction(() => window.__t.chipOn !== null, null, { timeout: 30000 }).catch(() => {});
await page.waitForFunction(() => window.__t.boxOn !== null, null, { timeout: 15000 }).catch(() => {});
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-awaiting.png" });
// click the box head: the gist must open into the details (never a dead end)
const head = page.locator("#bg-tasks .bg-fold-head").first();
let detail = false;
if (await head.count()) { await head.click(); await page.waitForTimeout(300); detail = (await page.locator("#bg-tasks .bg-await-detail").count()) > 0; }
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-expanded.png" });
fs.appendFileSync(cfg.states, JSON.stringify({ t: Math.floor(Date.now() / 1000), awaiting: false }) + "\n");
await page.waitForFunction(() => window.__t.chipOff !== null, null, { timeout: 30000 }).catch(() => {});
await page.waitForFunction(() => window.__t.boxOff !== null, null, { timeout: 15000 }).catch(() => {});
const all = await page.evaluate(() => window.__t);
const r = (a, b) => (a === null || b === null) ? null : Math.round(b - a);
fs.writeSync(1, "RESULT:" + JSON.stringify({ chipText: all.chipText, boxText: all.boxText, detail,
  boxLagMs: r(all.chipOn, all.boxOn), boxAppeared: all.boxOn !== null, chipAppeared: all.chipOn !== null,
  clearLagMs: r(all.chipOff, all.boxOff), boxCleared: all.boxOff !== null, chipCleared: all.chipOff !== null }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedSync(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served sync needs them")
        cls.lab = tempfile.mkdtemp(prefix="awaiting-sync-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        shutil.copytree(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", cwd.replace("/", "-"))
        os.makedirs(proj, exist_ok=True)
        # a CLOSED turn: an open one would invite the boot reconcile to resume it — no real CLI here
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-555555555555", "parentUuid": None,
                        "timestamp": "2026-09-02T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": "hello there"}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": "22222222-3333-4444-5555-666666666666",
                        "parentUuid": "11111111-2222-3333-4444-555555555555",
                        "timestamp": "2026-09-02T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-fable-5-1",
                                    "content": [{"type": "text", "text": "hi from the lab"}],
                                    "stop_reason": "end_turn"}}) + "\n")
        cls.port = _free_port()
        cls.token = "testtok-awaitsync"
        env = dict(os.environ, XDG_STATE_HOME=os.path.join(cls.lab, "xdg"), CLAUDE_CONFIG_DIR=claude,
                   ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1", ROMP_SERVE_TOKEN=cls.token,
                   ROMP_KERNEL_PORT=str(cls.port), ROMP_DIST_DIR=dist,
                   ROMP_MODEL_CATALOG="off")   # hermetic: never reach the network
        env.pop("ROMP_STATE_DIR", None)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_the_box_shows_within_one_frame_of_the_chip_and_clears_with_it(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
                       "states": os.path.join(self.state, "states", SID + ".jsonl"),
                       "shots": os.environ.get("AWAITING_SYNC_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served sync needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertTrue(r["chipAppeared"], "the overlay row must flip the chip: %r" % r)
        self.assertEqual(r["chipText"], "Awaiting agent", "one agent → singular (T225 rider): %r" % r)
        # THE FIX: the box renders from the SAME status frame — pre-fix it never appeared here at all
        self.assertTrue(r["boxAppeared"], "the box must appear with the chip — pre-fix it waited for a full frame: %r" % r)
        self.assertLessEqual(r["boxLagMs"], 250, "same frame, not the next full push: %r" % r)
        self.assertIn("Awaiting agent · 1 background agent still working", r["boxText"], "the gist agrees in number and carries the why: %r" % r)
        self.assertTrue(r["detail"], "clicking the gist opens the details — never a dead end: %r" % r)
        # the other direction: awaiting:false clears both in the same frame
        self.assertTrue(r["chipCleared"] and r["boxCleared"], "chip cleared ⇒ box gone: %r" % r)
        self.assertLessEqual(r["clearLagMs"], 250, "cleared together: %r" % r)


if __name__ == "__main__":
    unittest.main()
