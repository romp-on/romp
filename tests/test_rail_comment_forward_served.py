#!/usr/bin/env python3
"""A comment-forward scroll rail on the real /chat page (the user 2026-09-24, who rarely jumps by their own turns on the
rail but often clicks comments, which were hard to pick out and to hit on a long transcript): a turn notch is SHORTER
(4px out from the edge), a comment tick LONGER (14px; unread 16) with a hit box larger still, and every comment keeps its
colour semantics and its click.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): forty prompts, each answered by a long
reply, and four comment threads on four replies spread down the transcript: one read, two with a landed reply never seen
(unread), one resolved. Asserted in the browser, measured by hit-testing (document.elementFromPoint over a grid around
each mark, so the ::before pad counts exactly as the pointer sees it): every notch paints 4×2 and every read tick 14×4 and
unread 16×6; every tick's hit area is larger than any notch's, and wider; the colours stay (the read tick's fill is the
comment ink, the unread halo the needs-you red, the resolved tick at 0.3); and a click in a tick's PAD, off its paint,
still does what a click on the tick always did: the chat lands on the commented reply (the deep-link flash) and the
thread opens. With RAIL_SHOTS=<dir> the driver writes the rail in the dark and the light theme (whole pane and a zoom of
the edge). Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs,
invented prose, host TESTHOST)."""
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-777777777777"
PROMPTS = 40
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
# four threads on four replies spread down the transcript: (tid, reply index, state)
THREADS = [("tid-1", 6, "read"), ("tid-2", 16, "unread"), ("tid-3", 26, "resolved"), ("tid-4", 34, "unread")]
THREAD_SID = "cccccccc-1111-2222-3333-%012d"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def exact(k):
    """The sentence a thread highlights in reply k: its own words, so the mark finds one place."""
    return "The notes-api retry curve for step %d flattens after the third attempt." % k


def reply_body(k):
    paras = ["Paragraph %d of reply %d about the notes-api sync loop." % (i, k) for i in range(20)]
    paras[3] = exact(k)
    return "\n\n".join(paras)


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });
// the ticks are a client-side join of the chat frame (the anchors) and the comments frame (the threads), which rides the
// full pusher cycle: count the comments frames carrying the seeded threads, before navigation (the outline lab's gate)
await page.addInitScript(({ sid, n }) => {
  window.__cmtFrames = 0;
  window.addEventListener("message", (e) => {
    const m = e.data; if (!m || m.type !== "comments" || m.id !== sid) return;
    if ((m.threads || []).length >= n) window.__cmtFrames++;
  }, true);
}, { sid: cfg.sid, n: cfg.threads });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForSelector("#content .turn", { timeout: 60000 });
const ticksHere = () => page.evaluate((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.threads);
if (!(await ticksHere())) {
  const n0 = await page.evaluate(() => window.__cmtFrames);
  await page.waitForFunction((n) => window.__cmtFrames > n, n0, { timeout: 45000 }).catch(() => {});
}
try { await page.waitForFunction((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n
        && document.querySelectorAll(".scroll-marks .scroll-mark").length >= 40, cfg.threads, { timeout: 30000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ ticks: document.querySelectorAll("#cmt-rail .cmt-tick").length,
    notches: document.querySelectorAll(".scroll-marks .scroll-mark").length, frames: window.__cmtFrames }));
  console.error("rail never painted: " + JSON.stringify(st)); process.exit(1);
}
await page.waitForTimeout(500);   // the rail's rAF pass after the last frame
// hit-test a grid around each mark: the points whose top element IS the mark (its ::before pad included, as the pointer
// sees it); the box is where they fall
const measure = () => page.evaluate(() => {
  const survey = (el) => {
    const r = el.getBoundingClientRect();
    let area = 0, l = Infinity, rr = -Infinity, t = Infinity, b = -Infinity;
    for (let y = Math.floor(r.top) - 14; y <= Math.ceil(r.bottom) + 14; y++) {
      for (let x = Math.floor(r.right) - 44; x <= Math.ceil(r.right) + 4; x++) {
        if (document.elementFromPoint(x + 0.5, y + 0.5) !== el) continue;
        area++; l = Math.min(l, x); rr = Math.max(rr, x + 1); t = Math.min(t, y); b = Math.max(b, y + 1);
      }
    }
    const cs = getComputedStyle(el);
    return { w: el.offsetWidth, h: el.offsetHeight, left: r.left, top: r.top, right: r.right, bottom: r.bottom,
             hit: { area, w: area ? rr - l : 0, h: area ? b - t : 0, l, t },
             bg: cs.backgroundColor, shadow: cs.boxShadow, opacity: cs.opacity, cls: el.className,
             tid: el.dataset.tid || null, uuid: el.dataset.uuid || null };
  };
  const content = document.getElementById("content").getBoundingClientRect();
  return { notches: Array.from(document.querySelectorAll(".scroll-marks .scroll-mark")).map(survey),
           ticks: Array.from(document.querySelectorAll("#cmt-rail .cmt-tick")).map(survey),
           paneRight: content.right, theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const shoot = async (name) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await page.mouse.move(400, 300);
  await page.waitForTimeout(150);
  await page.screenshot({ path: cfg.shots + "/rail-" + name + ".png" });
  const c = await page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { r: r.right, t: r.top, h: r.height }; });
  await page.screenshot({ path: cfg.shots + "/rail-" + name + "-zoom.png", clip: { x: c.r - 90, y: c.t, width: 90, height: c.h } });
};
const dark = await measure();
await shoot("dark");
await page.evaluate(() => document.body.classList.add("theme-light"));
await page.waitForTimeout(300);
const light = await measure();
await shoot("light");
await page.evaluate(() => document.body.classList.remove("theme-light"));
await page.waitForTimeout(200);
// a click in the read tick's PAD, off its paint (above it and further in), does what a click on the tick does
const target = dark.ticks.find((t) => t.tid === cfg.readTid);
const px = target.left - 2, py = target.top - 3;
const under = await page.evaluate(([x, y]) => { const e = document.elementFromPoint(x, y); return e ? { tid: e.dataset.tid || null, cls: e.className } : null; }, [px, py]);
// every deep-link flash the landing puts up is recorded as it lands (the flash is a 1.6s animation class, so a wait
// that starts late can miss it): the uuid of the turn that wears it, or holds the element that does
await page.evaluate(() => {
  window.__flashed = [];
  new MutationObserver((recs) => { for (const r of recs) {
    const el = r.target;
    if (r.attributeName === "class" && el.classList && el.classList.contains("anchor-flash")) {
      const t = el.closest(".turn"); window.__flashed.push(t ? t.dataset.uuid : null);
    }
  } }).observe(document.getElementById("content"), { subtree: true, attributes: true, attributeFilter: ["class"] });
});
await page.mouse.click(px, py);
let landed = null;
try {
  await page.waitForFunction((u) => window.__flashed.includes(u) && !!document.getElementById("cmt-pop"), target.uuid, { timeout: 15000 });
  landed = await page.evaluate((u) => { const c = document.getElementById("content").getBoundingClientRect();
    const t = document.querySelector(`.turn[data-uuid="${u}"]`); const r = t ? t.getBoundingClientRect() : null;
    return { pop: !!document.getElementById("cmt-pop"), inView: !!r && r.bottom > c.top && r.top < c.bottom, flashed: window.__flashed }; }, target.uuid);
} catch (e) {
  landed = await page.evaluate(() => ({ pop: !!document.getElementById("cmt-pop"), flashed: window.__flashed }));
  landed.error = String(e).slice(0, 160);
}
fs.writeSync(1, "RESULT:" + JSON.stringify({ dark, light, click: { px, py, under, landed } }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentForwardRail(unittest.TestCase):
    maxDiff = None

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
        cls.lab = tempfile.mkdtemp(prefix="rail-forward-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        # the machine's user manager is not the lab's: these shadow systemctl and systemd-run on the lab kernel's PATH
        fakebin = os.path.join(cls.lab, "fakebin")
        os.makedirs(fakebin)
        for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
            Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
            os.chmod(os.path.join(fakebin, tool), 0o755)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 7200
        recs, prev = [], None
        for k in range(PROMPTS):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "timestamp": iso(t0 + 60 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": WEB, "message": {"role": "user", "content": "notes-api: step %d, how does the retry curve look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 60 * k + 20), "uuid": a, "parentUuid": u, "sessionId": WEB,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply_body(k)}]}})
            prev = a
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        # each thread: a fork cut at its anchor reply (the copied history up to it, then the exchange), a reply landed;
        # read = seen after it landed (lastSeenT ahead), unread = never seen, resolved = closed
        rows = []
        for i, (tid, k, st) in enumerate(THREADS):
            tsid = THREAD_SID % (i + 1)
            anchor = REPLY_UUID % k
            t = t0 + 60 * PROMPTS + 60 * i
            hist = [dict(r, sessionId=tsid) for r in recs[:2 * k + 2]]
            hist += [{"type": "user", "timestamp": iso(t), "uuid": "c%d" % i, "parentUuid": anchor, "promptSource": "typed",
                      "sessionId": tsid, "message": {"role": "user", "content": "why does it flatten at step %d?" % k}},
                     {"type": "assistant", "timestamp": iso(t + 20), "uuid": "r%d" % i, "parentUuid": "c%d" % i, "sessionId": tsid,
                      "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "The backoff cap takes over there."}]}}]
            Path(proj, tsid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
            Path(state, "sdk", tsid + ".json").write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": cwd, "lastSid": tsid, "threadOf": WEB, "forkAt": anchor,
                 "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
            row = {"tid": tid, "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor, "cutUuid": anchor,
                   "exact": exact(k), "status": "resolved" if st == "resolved" else "open", "createdT": t}
            if st == "read":
                row["lastSeenT"] = int(time.time()) + 86400
            rows.append(row)
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-railforward"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        env["PATH"] = fakebin + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
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
            k.kill()
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_comments_stand_out_and_are_easier_to_hit_than_turns(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB,
                       "threads": len(THREADS), "readTid": "tid-1", "shots": os.environ.get("RAIL_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        RED = "rgb(192, 57, 43)"   # --st-awaiting-bg, the needs-you red, the same in both themes
        for theme, ink in (("dark", "rgb(255, 213, 74)"), ("light", "rgb(143, 106, 0)")):
            m = r[theme]
            self.assertEqual(m["theme"], theme)
            notches, ticks = m["notches"], m["ticks"]
            self.assertEqual(len(notches), PROMPTS, "one notch per prompt: %d" % len(notches))
            self.assertEqual(sorted(t["tid"] for t in ticks), [t[0] for t in THREADS], ticks)
            # the paint: a notch 4×2, a read tick 14×4, an unread one 16×6; the tick reaches further in than the notch
            for n in notches:
                self.assertEqual((n["w"], n["h"]), (4, 2), "a turn notch is shorter: %r" % n)
            by = {t["tid"]: t for t in ticks}
            state = {tid: st for tid, _, st in THREADS}
            for tid, t in by.items():
                want = (16, 6) if state[tid] == "unread" else (14, 4)
                self.assertEqual((t["w"], t["h"]), want, "%s (%s) paints %r" % (tid, state[tid], t))
                self.assertLess(t["left"], min(n["left"] for n in notches) - 8, "the comment reaches further in than any turn notch: %r" % t)
            # the HIT areas, as the pointer sees them: every tick's larger and wider than every notch's
            notch_hits = [n["hit"] for n in notches if n["hit"]["area"]]
            self.assertTrue(notch_hits, "the notches are hit-testable: %r" % notches[:3])
            big_notch = max(h["area"] for h in notch_hits)
            wide_notch = max(h["w"] for h in notch_hits)
            for tid, t in by.items():
                self.assertGreater(t["hit"]["area"], big_notch, "%s's hit area beats every notch's (%d px²): %r" % (tid, big_notch, t["hit"]))
                self.assertGreater(t["hit"]["w"], wide_notch, "%s's hit box is wider: %r vs %d" % (tid, t["hit"], wide_notch))
                self.assertGreaterEqual(t["hit"]["w"], 19, "%s: the paint plus 4px in and 1px out: %r" % (tid, t["hit"]))
                self.assertGreater(t["hit"]["h"], t["h"] + 4, "%s: padded above and below its paint: %r" % (tid, t["hit"]))
            # the colour semantics, unchanged: the read tick's fill is the comment ink; unread keeps the ink and wears
            # the needs-you halo; resolved fades to 0.3
            self.assertEqual(by["tid-1"]["bg"], ink, "%s: the read tick is the comment ink: %r" % (theme, by["tid-1"]))
            self.assertEqual(by["tid-1"]["shadow"], "none", by["tid-1"])
            for tid in ("tid-2", "tid-4"):
                self.assertIn("unread", by[tid]["cls"])
                self.assertEqual(by[tid]["bg"], ink, by[tid])
                self.assertRegex(by[tid]["shadow"], re.escape(RED) + r" 0px 0px 0px 3px", "%s: the needs-you halo: %r" % (tid, by[tid]))
            self.assertIn("resolved", by["tid-3"]["cls"])
            self.assertEqual(by["tid-3"]["opacity"], "0.3", by["tid-3"])
        # a click in the pad (off the paint) is the tick's click: the chat lands on the reply and the thread opens
        c = r["click"]
        self.assertEqual((c["under"] or {}).get("tid"), "tid-1", "the pad point hit-tests to the tick: %r" % c)
        self.assertNotIn("error", c["landed"], "landing: %r" % c)
        self.assertTrue(c["landed"]["pop"], "the thread opened: %r" % c)
        self.assertTrue(c["landed"]["inView"], "the commented reply is in view: %r" % c)


if __name__ == "__main__":
    unittest.main()
