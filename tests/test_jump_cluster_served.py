#!/usr/bin/env python3
"""The chat's jump cluster on the real /chat page (the user 2026-09-24, paraphrased: after new content arrives they lose
their place and want to get back to their last message and read everything since, and reach comments without hunting on
the rail). The cluster in the pane's bottom-left corner steps through the reader's own messages and the comment threads,
and its badge (which replaced the "replies unread" chips) jumps to the next unread reply.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): 150 exchanges, more than one wire tail
(250 events), so the page opens holding exchanges 25..149 with the older ones on the server, and renders only the last 80
units of those (the rest in a spacer). Four comment threads: one on reply 3 (history the page has not loaded; unread), one
on reply 25 (loaded, outside the rendered window; read), one on reply 90 (resolved), one on reply 140 (unread). A second
session, api, is two lines long. Asserted in the browser, each move by the deep-link flash landing on the expected turn:
  from the bottom, previous lands on the last message of the reader's own, previous again on the one before, next back;
  previous comment walks 140, 90 (outside the rendered window), 25 (outside it too); from reply 25, previous message
  lands on prompt 25 and previous again on prompt 24, which the page did not hold: the move asks for that history
  through the page's own gap loader and lands once it arrives; previous comment then lands reply 3, fetched by the
  landing road; the badge counts the unread replies off screen and lands the next one without marking it read; the keys
  (Ctrl+Alt+Up, Ctrl+Alt+Shift+Up) do what the buttons do; the api tab, whose transcript fits, shows no cluster.
With JUMP_SHOTS=<dir> the driver writes the cluster in the dark theme, the light theme and the phone layout (a coarse
pointer). With JUMP_SHOTS_ONLY=1 it writes the shots after load and stops, for a before shot of an older build. Skips
LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose,
host TESTHOST)."""
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

WEB = "aaaaaaaa-1111-2222-3333-888888888888"
API = "aaaaaaaa-1111-2222-3333-999999999999"
N = 150
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
THREADS = [("tid-a", 3, "unread"), ("tid-b", 25, "read"), ("tid-d", 90, "resolved"), ("tid-c", 140, "unread")]
THREAD_SID = "dddddddd-1111-2222-3333-%012d"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def exact(k):
    return "The notes-api export for step %d streams rows through a bounded buffer." % k


def reply_body(k):
    return exact(k) + "\n\nStep %d also keeps the header written once, and the retry cap stays at two minutes." % k


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const U = (k) => cfg.userUuid.replace("%012d", String(k).padStart(12, "0"));
const R = (k) => cfg.replyUuid.replace("%012d", String(k).padStart(12, "0"));
const out = { steps: {} };
const ctx = await browser.newContext({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
await page.addInitScript(({ sid, n }) => {
  window.__cmtFrames = 0;
  window.addEventListener("message", (e) => { const m = e.data; if (m && m.type === "comments" && m.id === sid && (m.threads || []).length >= n) window.__cmtFrames++; }, true);
}, { sid: cfg.sid, n: cfg.threads });
const settle = async (sel) => {
  await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
  await page.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
  const ticks = () => page.evaluate((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.minTicks);
  if (!(await ticks())) { const n0 = await page.evaluate(() => window.__cmtFrames); await page.waitForFunction((n) => window.__cmtFrames > n, n0, { timeout: 45000 }).catch(() => {}); }
  await page.waitForFunction((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.minTicks, { timeout: 30000 }).catch(() => {});
  if (sel) await page.waitForSelector(sel, { timeout: 30000 });
  await page.waitForTimeout(500);
};
const shoot = async (p, name) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await p.mouse.move(500, 200);
  await p.waitForTimeout(200);
  await p.screenshot({ path: cfg.shots + "/" + name + ".png" });
  const c = await p.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { l: r.left, b: r.bottom }; });
  await p.screenshot({ path: cfg.shots + "/" + name + "-corner.png", clip: { x: Math.max(0, c.l), y: Math.max(0, c.b - 200), width: 260, height: 200 } });
};
const openWeb = async (p) => {   // the web tab is the long one; the strip may open on either (and the phone folds the strip away)
  await p.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
  await p.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
};
await page.goto(cfg.chat);
await openWeb(page);
await settle(cfg.shotsOnly ? null : "#jump-cluster:not([hidden])");
if (cfg.shotsOnly) {
  await shoot(page, "dark");
  await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
  await shoot(page, "light");
  await page.evaluate(() => document.body.classList.remove("theme-light"));
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  const pp = await phone.newPage(); await pp.goto(cfg.chat); await openWeb(pp);
  await pp.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 }); await pp.waitForTimeout(4000);
  if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone.png" });
  fs.writeSync(1, "RESULT:" + JSON.stringify({ shotsOnly: true }) + "\n"); await browser.close(); process.exit(0);
}
// every deep-link flash as it lands: the uuid of the turn wearing it (the flash is a 1.6s class; a late wait can miss it)
await page.evaluate(() => {
  window.__flashed = [];
  new MutationObserver((recs) => { for (const r of recs) {
    const el = r.target;
    if (r.attributeName === "class" && el.classList && el.classList.contains("anchor-flash")) { const t = el.closest(".turn"); window.__flashed.push(t ? t.dataset.uuid : null); }
  } }).observe(document.getElementById("content"), { subtree: true, attributes: true, attributeFilter: ["class"] });
});
const state = () => page.evaluate(() => {
  const box = document.getElementById("jump-cluster");
  const b = (m) => { const e = box.querySelector(`[data-move="${m}"]`); return e ? { hidden: e.hidden || e.offsetParent === null, disabled: !!e.disabled, text: e.textContent.trim(), aria: e.getAttribute("aria-label") } : null; };
  const r = box.getBoundingClientRect(), c = document.getElementById("content").getBoundingClientRect();
  const jb = document.getElementById("jump-bottom"), jr = jb.getBoundingClientRect();
  return { hidden: box.hidden, prevMine: b("prevMine"), nextMine: b("nextMine"), prevComment: b("prevComment"), nextComment: b("nextComment"), badge: b("nextUnread"),
           cmtPill: !box.querySelector(".jc-cmt").hidden, rect: { l: r.left, t: r.top, r: r.right, b: r.bottom }, content: { l: c.left, b: c.bottom },
           jump: { hidden: jb.hidden, l: jr.left, t: jr.top, r: jr.right, b: jr.bottom }, chips: !!document.getElementById("reply-chips") };
});
const move = async (name, how, want, ms = 15000) => {
  await page.evaluate(() => { window.__flashed = []; });
  if (how === "click") await page.click(`#jump-cluster [data-move="${name}"]`);
  else await page.keyboard.press(how);
  let ok = true;
  try { await page.waitForFunction((u) => window.__flashed.includes(u), want, { timeout: ms }); } catch (e) { ok = false; }
  await page.waitForTimeout(350);   // the landing's settle
  const got = await page.evaluate((u) => { const c = document.getElementById("content").getBoundingClientRect(); const t = document.querySelector(`.turn[data-uuid="${u}"]`);
    const r = t ? t.getBoundingClientRect() : null; return { flashed: window.__flashed.slice(), inView: !!r && r.bottom > c.top && r.top < c.bottom, top: r ? Math.round(r.top - c.top) : null }; }, want);
  out.steps[name + ":" + how + ":" + want.slice(-4)] = { ok, want, ...got };
  return ok;
};
out.start = await state();
await shoot(page, "dark");
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
await shoot(page, "light");
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(200);
// your own messages, from the bottom
out.order = [];
const seq = [
  ["prevMine", "click", U(149)], ["prevMine", "click", U(148)], ["nextMine", "click", U(149)],
  ["prevComment", "click", R(140)], ["prevComment", "click", R(90)], ["prevComment", "click", R(25)],
  ["prevMine", "click", U(25)], ["prevMine", "click", U(24), 40000],
  ["prevComment", "click", R(3), 40000],
];
for (const [m, how, want, ms] of seq) { out.order.push(m + ">" + want.slice(-4)); if (!(await move(m, how, want, ms))) break; }
out.atOld = await state();
out.readBefore = await page.evaluate(() => { const m = document.querySelector('mark.cmt-hl[data-tid="tid-c"]'); return m ? m.classList.contains("unread") : null; });
if (Object.values(out.steps).every((s) => s.ok)) {
  await move("nextUnread", "click", R(140));
  out.afterBadge = await page.evaluate(() => { const m = document.querySelector('mark.cmt-hl[data-tid="tid-c"]'); return { unread: m ? m.classList.contains("unread") : null, pop: !!document.getElementById("cmt-pop") }; });
  await page.mouse.move(500, 300);
  await move("prevMine", "Control+Alt+ArrowUp", U(140));
  await move("prevComment", "Control+Alt+Shift+ArrowUp", R(90));
}
// the api tab: a transcript that fits shows no cluster
await page.evaluate((sid) => { const t = document.querySelector(`#tabs .tab[data-id="${sid}"]`); if (t) t.click(); }, cfg.api);
await page.waitForTimeout(1500);
out.api = await page.evaluate(() => ({ hidden: document.getElementById("jump-cluster").hidden, fits: (() => { const c = document.getElementById("content"); return c.scrollHeight <= c.clientHeight + 2; })() }));
// the phone layout: a coarse pointer, touch-sized pills in the chip's column
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
const pp = await phone.newPage();
await pp.goto(cfg.chat);
await openWeb(pp);
await pp.waitForSelector("#jump-cluster:not([hidden])", { timeout: 60000 }).catch(() => {});
await pp.waitForTimeout(1500);
out.phone = await pp.evaluate(() => {
  const box = document.getElementById("jump-cluster"); const g = box.querySelector(".jc-mine"), b = box.querySelector(".jc-mine .jc-btn");
  const jb = document.getElementById("jump-bottom");
  return { hidden: box.hidden, coarse: matchMedia("(pointer: coarse)").matches, pill: { w: g.offsetWidth, h: g.offsetHeight }, btn: { w: b.offsetWidth, h: b.offsetHeight }, chipW: jb.offsetWidth };
});
if (cfg.shots) { await pp.screenshot({ path: cfg.shots + "/phone.png" }); const c = await pp.evaluate(() => document.getElementById("content").getBoundingClientRect().bottom);
  await pp.screenshot({ path: cfg.shots + "/phone-corner.png", clip: { x: 0, y: Math.max(0, c - 220), width: 220, height: 220 } }); }
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedJumpCluster(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="jump-cluster-")
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
        fakebin = os.path.join(cls.lab, "fakebin")   # the machine's user manager is not the lab's
        os.makedirs(fakebin)
        for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
            Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
            os.chmod(os.path.join(fakebin, tool), 0o755)
        os.makedirs(cwd, exist_ok=True)
        for sid, name, colour in ((WEB, "web", "#9cd2ff\t#0c1a2e"), (API, "api", "#1EA1EB\t#ffffff")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\n" % (name, cwd, colour))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3 * 86400
        recs, prev = [], None
        for k in range(N):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "timestamp": iso(t0 + 600 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": WEB, "message": {"role": "user", "content": "notes-api: step %d, how does the export look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 600 * k + 30), "uuid": a, "parentUuid": u, "sessionId": WEB,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply_body(k)}]}})
            prev = a
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        short = [{"type": "user", "timestamp": iso(t0), "uuid": "a-u1", "parentUuid": None, "promptSource": "typed", "sessionId": API,
                  "message": {"role": "user", "content": "notes-api: is the api healthy?"}},
                 {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a-a1", "parentUuid": "a-u1", "sessionId": API,
                  "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Yes, all green."}]}}]
        Path(proj, API + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in short))
        rows = []
        for i, (tid, k, st) in enumerate(THREADS):
            tsid = THREAD_SID % (i + 1)
            anchor = REPLY_UUID % k
            t = t0 + 600 * N + 60 * i
            hist = [dict(r, sessionId=tsid) for r in recs[:2 * k + 2]]
            hist += [{"type": "user", "timestamp": iso(t), "uuid": "c%d" % i, "parentUuid": anchor, "promptSource": "typed",
                      "sessionId": tsid, "message": {"role": "user", "content": "why a bounded buffer at step %d?" % k}},
                     {"type": "assistant", "timestamp": iso(t + 20), "uuid": "r%d" % i, "parentUuid": "c%d" % i, "sessionId": tsid,
                      "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "So a slow client never holds the whole export in memory."}]}}]
            Path(proj, tsid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
            Path(state, "sdk", tsid + ".json").write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": cwd, "lastSid": tsid, "threadOf": WEB, "forkAt": anchor,
                 "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
            row = {"tid": tid, "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor, "cutUuid": anchor,
                   "exact": exact(k), "status": "resolved" if st == "resolved" else "open", "createdT": t}
            if st != "unread":
                row["lastSeenT"] = int(time.time()) + 86400
            rows.append(row)
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-jumpcluster"
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

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB, "api": API,
                       "threads": len(THREADS), "minTicks": 3, "userUuid": USER_UUID, "replyUuid": REPLY_UUID,
                       "shots": os.environ.get("JUMP_SHOTS", ""), "shotsOnly": os.environ.get("JUMP_SHOTS_ONLY") == "1"}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=480,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def test_each_control_lands_its_turn_including_history_the_page_did_not_hold(self):
        r = self._drive()
        if r.get("shotsOnly"):
            self.skipTest("optional: JUMP_SHOTS_ONLY wrote the screenshots and ran no assertions")
        s0 = r["start"]
        # at the bottom of a long transcript: the cluster shows in the chip's column above the chip's slot, the old chips are gone
        self.assertFalse(s0["hidden"], s0)
        self.assertFalse(s0["chips"], "the reply chips are gone: %r" % s0)
        self.assertFalse(s0["prevMine"]["disabled"], s0)
        self.assertTrue(s0["nextMine"]["disabled"], "at the bottom nothing of yours is below: %r" % s0)
        self.assertTrue(s0["cmtPill"], "the session has comment threads: %r" % s0)
        self.assertFalse(s0["prevComment"]["disabled"], s0)
        self.assertTrue(s0["nextComment"]["disabled"], s0)
        self.assertFalse(s0["badge"]["hidden"], "two unread replies wait off screen: %r" % s0)
        self.assertEqual(s0["badge"]["text"], "2", s0)
        self.assertEqual(s0["badge"]["aria"], "2 replies unread, go to the next one above", s0)
        self.assertTrue(s0["jump"]["hidden"], "at the bottom the go-to-bottom chip is down: %r" % s0)
        self.assertEqual(s0["rect"]["l"], s0["content"]["l"] + 14, "the chip's column (its left: 14px): %r" % s0)
        self.assertLess(abs(s0["rect"]["b"] - (s0["content"]["b"] - 8 - 22 - 6)), 1, "the chip's slot held below, though the chip is down: %r" % s0)
        # every move landed its turn (the deep-link flash on the expected uuid), in order
        for name, st in r["steps"].items():
            self.assertTrue(st["ok"], "%s did not land %s: %r (order %r)" % (name, st["want"], st, r["order"]))
            self.assertTrue(st["inView"], "%s: the landed turn is in view: %r" % (name, st))
        self.assertEqual(len(r["steps"]), 12, "all twelve moves ran: %r" % list(r["steps"]))
        # prompt 24 was not in the page when its move was pressed (the head gap); the move loaded it and landed
        u24 = [s for k, s in r["steps"].items() if k.startswith("prevMine:click:") and s["want"].endswith("000024")][0]
        self.assertTrue(u24["ok"], u24)
        # at reply 3 the chip is up, and the cluster did not move for it: the chip's slot was held, and they do not overlap
        s1 = r["atOld"]
        self.assertFalse(s1["jump"]["hidden"], s1)
        self.assertEqual(s1["rect"], s0["rect"], "the cluster never moves as the chip comes and goes: %r vs %r" % (s1["rect"], s0["rect"]))
        self.assertLessEqual(s1["rect"]["b"] + 6, s1["jump"]["t"] + 0.5, "the stack gap above the chip: %r" % s1)
        self.assertEqual((s1["jump"]["l"], s1["jump"]["r"] - s1["jump"]["l"]), (s1["rect"]["l"], 40), "one column, the chip's 40px: %r" % s1)
        self.assertTrue(s1["prevComment"]["disabled"] and not s1["nextComment"]["disabled"], "reply 3 is the first comment: %r" % s1)
        # the badge: reply 3's own unread thread is on screen (counted in neither direction); the one on reply 140 waits below
        self.assertEqual(s1["badge"]["text"], "1", s1)
        self.assertEqual(s1["badge"]["aria"], "1 reply unread, go to the next one below", s1)
        # …and its press landed reply 140 without marking the thread read (opening the thread does)
        self.assertEqual(r["afterBadge"], {"unread": True, "pop": False}, "the badge brings you to the reply; reading it is opening the thread")
        # the api tab: its transcript fits, so no cluster
        self.assertTrue(r["api"]["fits"], r["api"])
        self.assertTrue(r["api"]["hidden"], "no cluster over a transcript that fits: %r" % r["api"])
        # the phone: a coarse pointer makes the pills the chip's 64x32, halves 31 wide
        ph = r["phone"]
        self.assertTrue(ph["coarse"], ph)
        self.assertFalse(ph["hidden"], ph)
        self.assertEqual(ph["pill"], {"w": 64, "h": 32}, "the go-to-bottom chip's phone size: %r" % ph)
        self.assertEqual(ph["btn"]["w"], 31, ph)


if __name__ == "__main__":
    unittest.main()
