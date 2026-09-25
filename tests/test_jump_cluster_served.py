#!/usr/bin/env python3
"""The chat's jump cluster on the real /chat page (the user 2026-09-24, paraphrased: after new content arrives they lose
their place and want to get back to their last message and read everything since, and reach comments without hunting on
the rail). One narrow column in the pane's bottom-left corner, three capsules, each its up chevron, an icon and its down
chevron: the threads with an unread reply (and how many wait off screen; it replaced the "replies unread" chips), every
comment thread, and the reader's own messages.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): 150 exchanges, more than one wire tail
(250 events), so the page opens holding exchanges 25..149 with the older ones on the server, and renders only the last 80
units of those (the rest in a spacer). Five comment threads: on reply 3 (history the page has not loaded; unread), 25
(loaded, outside the rendered window; read), 90 (resolved), 140 (unread) and 145 (unread). A second session, api, is two
lines long. Asserted in the browser, each move by the deep-link flash landing on the expected turn:
  the column's layout (order, labels, icons, 24px wide, 204px tall, the chip's slot held below, left-aligned with it);
  from the bottom, previous lands on the last message of the reader's own, previous again on the one before, next back;
  previous comment walks 145, 140, 90 and 25 (the last two outside the rendered window); from reply 25, previous message
  lands on prompt 25 and previous again on prompt 24, which the page did not hold (asked for through the page's own gap
  loader, landed once it arrived); previous unread lands reply 3, fetched by the landing road; next unread (by key, then
  by click) walks 140 and 145, where none is left below; previous unread by key lands 140; neither marks a thread read;
  the count is the unread replies off screen; the keys for messages and comments do what the buttons do; the api tab,
  whose transcript fits, shows no cluster; on a 390x844 phone all three capsules show, 34px wide with 32x40 chevrons, and
  the column clears the message box. The go-to-bottom chip is the column's foot (the user 2026-09-25, paraphrased: the
  same width as the column, in its look): shown, it measures the column's width (24 on desktop, 34 on the phone), 30 and 42
  tall (the phone's 40 inside the hairline, the arrows' touch size), left-aligned, one capsule gap below the column, and
  the page computes the same surface, hairline, shadow and radius for it as for a capsule, in the dark theme and the
  light, with the arrows' chevron at their size, stroke and resting glow; scrolled up on the phone, column and chip
  together sit between the pane's top and the message box.
With JUMP_SHOTS=<dir> the driver writes the cluster in the dark theme, the light theme and the phone layout (a coarse
pointer), each also a screen up with the go-to-bottom chip showing (*-up-corner.png). With JUMP_SHOTS_ONLY=1 it writes
the shots after load and stops, for a before shot of an older build. Skips LOUDLY without the extension deps or a
Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose,
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
THREADS = [("tid-a", 3, "unread"), ("tid-b", 25, "read"), ("tid-d", 90, "resolved"), ("tid-c", 140, "unread"), ("tid-e", 145, "unread")]
THREAD_SID = "dddddddd-1111-2222-3333-%012d"
CHIP_H, PHONE_CHIP_H, GAP = 30, 42, 6   # the go-to-bottom chip's height on each layout, and the gap between capsules


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
  await p.screenshot({ path: cfg.shots + "/" + name + "-corner.png", clip: { x: Math.max(0, c.l), y: Math.max(0, c.b - 300), width: 260, height: 300 } });
};
// the go-to-bottom chip shows only off the bottom: a screen up puts it beside the column, and its own click brings the view
// back down (the follow re-entry, so a run that shot this carries on from the bottom as one that did not)
const scrollUp = async (p) => {
  await p.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = Math.max(0, c.scrollTop - 600); });
  await p.waitForSelector("#jump-bottom:not([hidden])", { timeout: 10000 }).catch(() => {});
  await p.waitForTimeout(400);
};
const backDown = async (p) => {
  await p.click("#jump-bottom").catch(() => {});
  await p.waitForSelector("#jump-bottom", { state: "hidden", timeout: 10000 }).catch(() => {});
  await p.waitForTimeout(300);
};
// the corner with the column and the chip both in frame, in each theme (the view already scrolled up)
const upCorner = async (p, prefix, themes) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await p.mouse.move(300, 60);
  for (const th of themes) {
    await p.evaluate((light) => document.body.classList.toggle("theme-light", light), th === "light");
    await p.waitForTimeout(300);
    const clip = await p.evaluate(() => {
      const a = document.getElementById("jump-cluster").getBoundingClientRect(), b = document.getElementById("jump-bottom").getBoundingClientRect();
      const top = Math.max(0, Math.min(a.top, b.top) - 28);
      return { x: 0, y: top, width: 240, height: Math.max(a.bottom, b.bottom) + 24 - top };
    });
    await p.screenshot({ path: cfg.shots + "/" + prefix + th + "-up-corner.png", clip });
  }
  await p.evaluate(() => document.body.classList.remove("theme-light"));
  await p.waitForTimeout(200);
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
  await scrollUp(page); await upCorner(page, "", ["dark", "light"]);
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  const pp = await phone.newPage(); await pp.goto(cfg.chat); await openWeb(pp);
  await pp.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 }); await pp.waitForTimeout(4000);
  if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone.png" });
  await scrollUp(pp); await upCorner(pp, "phone-", ["dark", "light"]);
  if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone-up.png" });
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
// the column as the page lays it out: each capsule's box, each control's state, the count, the chip below
const LAYOUT = () => {
  const box = document.getElementById("jump-cluster");
  const b = (m) => { const e = box.querySelector(`[data-move="${m}"]`); return e ? { disabled: !!e.disabled, aria: e.getAttribute("aria-label"), w: e.offsetWidth, h: e.offsetHeight } : null; };
  const g = (cls) => { const e = box.querySelector(cls); const r = e.getBoundingClientRect(); return { shown: !e.hidden && e.offsetParent !== null, l: r.left, t: r.top, w: r.width, h: r.height, label: e.getAttribute("aria-label"),
                        icon: !!e.querySelector(".jc-icon svg"), order: Array.from(e.children).map((k) => k.className.split(" ")[0]) }; };
  const r = box.getBoundingClientRect(), c = document.getElementById("content").getBoundingClientRect();
  const jb = document.getElementById("jump-bottom"), jr = jb.getBoundingClientRect();
  const cnt = box.querySelector(".jc-count"), cr = cnt.getBoundingClientRect();
  const comp = document.getElementById("composer"), kr = comp ? comp.getBoundingClientRect() : null;
  return { hidden: box.hidden, prevMine: b("prevMine"), nextMine: b("nextMine"), prevComment: b("prevComment"), nextComment: b("nextComment"),
           prevUnread: b("prevUnread"), nextUnread: b("nextUnread"),
           unread: g(".jc-unread"), cmt: g(".jc-cmt"), mine: g(".jc-mine"),
           count: { shown: !cnt.hidden, text: cnt.textContent.trim(), l: cr.left, t: cr.top, r: cr.right, pointer: getComputedStyle(cnt).pointerEvents },
           rect: { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }, content: { l: c.left, t: c.top, b: c.bottom },
           composerTop: kr ? kr.top : null, jump: { hidden: jb.hidden, l: jr.left, t: jr.top, r: jr.right, b: jr.bottom, w: jr.width, h: jr.height },
           chips: !!document.getElementById("reply-chips") };
};
// the go-to-bottom chip's dress beside a capsule's, as the page computes them in the current theme (the chip shown, the
// pointer elsewhere): the surface, the hairline, the shadow, the radius, and the chevron's size, stroke and resting glow
const DRESS = () => {
  const box = (e) => { const s = getComputedStyle(e); return { bg: s.backgroundColor, bw: s.borderTopWidth, bs: s.borderTopStyle, bc: s.borderTopColor, shadow: s.boxShadow, radius: s.borderTopLeftRadius }; };
  const glyph = (e) => { const r = e.getBoundingClientRect(), pl = e.querySelector("polyline");
    return { w: r.width, h: r.height, stroke: e.getAttribute("stroke-width"), vb: e.getAttribute("viewBox"), points: pl ? pl.getAttribute("points") : null, opacity: getComputedStyle(e).opacity }; };
  const jb = document.getElementById("jump-bottom"), g = document.querySelector("#jump-cluster .jc-group:not([hidden])");
  const down = document.querySelector('#jump-cluster .jc-group:not([hidden]) .jc-btn[data-move^="next"]:not(:disabled) svg');   // a live down arrow (a disabled one rests at 0.2)
  return { jump: box(jb), group: box(g), jumpGlyph: glyph(jb.querySelector("svg")), arrowGlyph: down ? glyph(down) : null };
};
const state = () => page.evaluate(LAYOUT);
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
if (cfg.shots) { await scrollUp(page); await upCorner(page, "", ["dark", "light"]); await backDown(page); }
out.order = [];
const readState = (tid) => page.evaluate((t) => { const m = document.querySelector(`mark.cmt-hl[data-tid="${t}"]`); return { unread: m ? m.classList.contains("unread") : null, pop: !!document.getElementById("cmt-pop") }; }, tid);
const seq = [
  // your own messages, from the bottom
  ["prevMine", "click", U(149)], ["prevMine", "click", U(148)], ["nextMine", "click", U(149)],
  // every comment: 145, 140, 90 and 25 (the last two outside the rendered window)
  ["prevComment", "click", R(145)], ["prevComment", "click", R(140)], ["prevComment", "click", R(90)], ["prevComment", "click", R(25)],
  // prompt 25, then prompt 24, which the page does not hold: loaded, then landed
  ["prevMine", "click", U(25)], ["prevMine", "click", U(24), 40000],
  // the unread replies: reply 3 is in history the page has not loaded (outside the loaded window), then down and back up
  ["prevUnread", "click", R(3), 40000], ["nextUnread", "Control+Alt+PageDown", R(140)], ["nextUnread", "click", R(145)],
];
for (const [m, how, want, ms] of seq) { out.order.push(m + ">" + want.slice(-4)); if (!(await move(m, how, want, ms))) break; }
if (Object.values(out.steps).every((s) => s.ok)) {
  out.atLast = await state();   // on reply 145: no unread thread is below it
  out.read145 = await readState("tid-e");
  await page.mouse.move(500, 300);
  await page.waitForTimeout(200);   // the glyphs' 120ms glow back to rest
  out.dress = { dark: await page.evaluate(DRESS) };
  await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
  out.dress.light = await page.evaluate(DRESS);
  await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(200);
  await move("prevUnread", "Control+Alt+PageUp", R(140));
  out.read140 = await readState("tid-c");
  await move("prevMine", "Control+Alt+ArrowUp", U(140));
  await move("prevComment", "Control+Alt+Shift+ArrowUp", R(90));
}
// the api tab: a transcript that fits shows no cluster
await page.evaluate((sid) => { const t = document.querySelector(`#tabs .tab[data-id="${sid}"]`); if (t) t.click(); }, cfg.api);
await page.waitForTimeout(1500);
out.api = await page.evaluate(() => ({ hidden: document.getElementById("jump-cluster").hidden, fits: (() => { const c = document.getElementById("content"); return c.scrollHeight <= c.clientHeight + 2; })() }));
// the phone layout: a coarse pointer, all three capsules showing (three replies wait unread at the bottom)
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
const pp = await phone.newPage();
await pp.goto(cfg.chat);
await openWeb(pp);
await pp.waitForSelector("#jump-cluster:not([hidden]) .jc-unread:not([hidden])", { timeout: 60000 }).catch(() => {});
await pp.waitForTimeout(1500);
out.phone = await pp.evaluate(LAYOUT);
out.phone.coarse = await pp.evaluate(() => matchMedia("(pointer: coarse)").matches);
if (cfg.shots) { await pp.screenshot({ path: cfg.shots + "/phone.png" }); const c = await pp.evaluate(() => document.getElementById("content").getBoundingClientRect().bottom);
  await pp.screenshot({ path: cfg.shots + "/phone-corner.png", clip: { x: 0, y: Math.max(0, c - 440), width: 200, height: 440 + 120 } }); }
// a screen up on the phone: the chip shows under the column, and the pair must still fit between the pane's top and the message box
await scrollUp(pp);
out.phoneUp = await pp.evaluate(LAYOUT);
out.phoneDress = { dark: await pp.evaluate(DRESS) };
await pp.evaluate(() => document.body.classList.add("theme-light")); await pp.waitForTimeout(300);
out.phoneDress.light = await pp.evaluate(DRESS);
await pp.evaluate(() => document.body.classList.remove("theme-light")); await pp.waitForTimeout(200);
await upCorner(pp, "phone-", ["dark", "light"]);
if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone-up.png" });
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
        # at the bottom of a long transcript: one narrow column of three capsules above the chip's slot, the old chips gone
        self.assertFalse(s0["hidden"], s0)
        self.assertFalse(s0["chips"], "the reply chips are gone: %r" % s0)
        for g, label in (("unread", "Unread replies"), ("cmt", "Comments"), ("mine", "Your messages")):
            self.assertTrue(s0[g]["shown"], "%s capsule shows: %r" % (g, s0[g]))
            self.assertEqual(s0[g]["label"], label, "each capsule names what it walks: %r" % s0[g])
            self.assertTrue(s0[g]["icon"], "an icon between its chevrons: %r" % s0[g])
            self.assertEqual(s0[g]["order"], ["jc-btn", "jc-icon", "jc-btn"], "up, icon, down: %r" % s0[g])
            self.assertEqual((s0[g]["l"], s0[g]["w"]), (14, 24), "left-aligned at the chip's 14px, 24px wide: %r" % s0[g])
            self.assertEqual(s0[g]["h"], 64, "a capsule: two 22px chevrons, an 18px icon row, the hairline: %r" % s0[g])
        self.assertLess(s0["unread"]["t"], s0["cmt"]["t"], "unread on top")
        self.assertLess(s0["cmt"]["t"], s0["mine"]["t"], "comments above your messages")
        self.assertEqual(s0["cmt"]["t"] - (s0["unread"]["t"] + s0["unread"]["h"]), 6, "a small gap between capsules")
        self.assertEqual((s0["rect"]["w"], s0["rect"]["h"]), (24, 204), "the column: 24 wide, three capsules 204 tall: %r" % s0["rect"])
        self.assertTrue(s0["jump"]["hidden"], "at the bottom the go-to-bottom chip is down: %r" % s0)
        self.assertLess(abs(s0["rect"]["b"] - (s0["content"]["b"] - 8 - CHIP_H - GAP)), 1, "the chip's slot held below, though the chip is down: %r" % s0)
        # the controls: at the bottom nothing of yours, no comment and no unread reply is below
        for m, dis in (("prevMine", False), ("nextMine", True), ("prevComment", False), ("nextComment", True), ("prevUnread", False), ("nextUnread", True)):
            self.assertEqual(s0[m]["disabled"], dis, "%s at the bottom: %r" % (m, s0[m]))
        self.assertEqual(s0["prevUnread"]["aria"], "Previous unread reply (Ctrl+Alt+PageUp)", "the tip names the key: %r" % s0["prevUnread"])
        self.assertEqual(s0["nextUnread"]["aria"], "Next unread reply (Ctrl+Alt+PageDown)", s0["nextUnread"])
        # the count: the three unread replies off screen (the chips' number), a badge over the empty margin, never in a finger's way
        self.assertEqual((s0["count"]["shown"], s0["count"]["text"]), (True, "3"), s0["count"])
        self.assertLess(s0["count"]["l"], s0["unread"]["l"], "on the capsule's upper-left corner, over the margin: %r" % s0["count"])
        self.assertLessEqual(s0["count"]["r"], s0["unread"]["l"] + s0["unread"]["w"], "the column is not widened: %r" % s0["count"])
        self.assertEqual(s0["count"]["pointer"], "none", s0["count"])
        # every move landed its turn (the deep-link flash on the expected uuid), in order
        for name, st in r["steps"].items():
            self.assertTrue(st["ok"], "%s did not land %s: %r (order %r)" % (name, st["want"], st, r["order"]))
            self.assertTrue(st["inView"], "%s: the landed turn is in view: %r" % (name, st))
        self.assertEqual(len(r["steps"]), 15, "all fifteen moves ran: %r" % list(r["steps"]))
        # on reply 145, the last unread thread: none left below, and the chip is up while the column did not move for it
        s1 = r["atLast"]
        self.assertTrue(s1["nextUnread"]["disabled"], "no unread reply is left below: %r" % s1["nextUnread"])
        self.assertFalse(s1["prevUnread"]["disabled"], s1)
        self.assertFalse(s1["jump"]["hidden"], s1)
        self.assertEqual((s1["rect"]["b"], s1["rect"]["l"]), (s0["rect"]["b"], s0["rect"]["l"]), "the column never moves as the chip comes and goes: %r vs %r" % (s1["rect"], s0["rect"]))
        self.assertLess(abs(s1["jump"]["t"] - s1["rect"]["b"] - GAP), 0.5, "the chip one capsule gap below the column: %r" % s1)
        self.assertEqual(s1["jump"]["l"], s1["rect"]["l"], "left-aligned with the chip: %r" % s1)
        # the chip is the column's foot: its width exactly, a short capsule a touch taller than wide
        self.assertEqual((s1["jump"]["w"], s1["jump"]["h"]), (s1["rect"]["w"], CHIP_H), "the chip: the column's width, %dpx tall: %r" % (CHIP_H, s1))
        self.assertEqual(s1["jump"]["w"], 24, s1)
        # one dress in either theme: the page computes the chip's surface, hairline, shadow and radius as a capsule's, and
        # its chevron is the arrows' own, at their size, stroke and resting glow
        for theme in ("dark", "light"):
            self._same_dress(r["dress"][theme], 14, "0.55", theme)
        self.assertNotEqual(r["dress"]["dark"]["jump"]["bg"], r["dress"]["light"]["jump"]["bg"], "the themes differ, so the match means something: %r" % r["dress"])
        # the unread arrows land a thread without marking it read (opening the thread does)
        self.assertEqual(r["read145"], {"unread": True, "pop": False}, "landing is not reading: %r" % r["read145"])
        self.assertEqual(r["read140"], {"unread": True, "pop": False}, r["read140"])
        # the api tab: its transcript fits, so no cluster
        self.assertTrue(r["api"]["fits"], r["api"])
        self.assertTrue(r["api"]["hidden"], "no cluster over a transcript that fits: %r" % r["api"])
        # the phone (390x844, a coarse pointer): all three capsules show, the strip stays thin, the touch size comes from
        # height, and the column clears the message box
        ph = r["phone"]
        self.assertTrue(ph["coarse"], ph)
        self.assertFalse(ph["hidden"], ph)
        for g in ("unread", "cmt", "mine"):
            self.assertTrue(ph[g]["shown"], "%s capsule on the phone: %r" % (g, ph[g]))
            self.assertEqual(ph[g]["w"], 34, "the phone column: 34px: %r" % ph[g])
        for m in ("prevUnread", "nextUnread", "prevComment", "nextComment", "prevMine", "nextMine"):
            self.assertEqual((ph[m]["w"], ph[m]["h"]), (32, 40), "each chevron 32x40 on the phone: %s %r" % (m, ph[m]))
        self.assertEqual(ph["rect"]["h"], 330, "the phone column: three 106px capsules and two gaps: %r" % ph["rect"])
        self.assertGreaterEqual(ph["rect"]["t"], ph["content"]["t"], "inside the pane: %r" % ph)
        self.assertIsNotNone(ph["composerTop"], ph)
        self.assertLess(ph["rect"]["b"] + 8 + PHONE_CHIP_H + GAP, ph["composerTop"] + 1, "the column and the chip's slot clear the message box: %r" % ph)
        self.assertLessEqual(ph["rect"]["b"], ph["content"]["b"] - 8 - PHONE_CHIP_H - GAP + 1, ph)
        # a screen up on the phone: the chip shows, the column's width, its touch size from height, one gap below the column,
        # and column and chip together sit inside the pane, clear of the message box
        pu = r["phoneUp"]
        self.assertFalse(pu["jump"]["hidden"], "scrolled up, the chip shows: %r" % pu)
        self.assertFalse(pu["hidden"], pu)
        self.assertEqual((pu["jump"]["w"], pu["jump"]["h"]), (pu["rect"]["w"], PHONE_CHIP_H), "the chip on the phone: the column's width, %dpx tall: %r" % (PHONE_CHIP_H, pu))
        self.assertEqual(pu["jump"]["w"], 34, pu)
        self.assertGreaterEqual(pu["jump"]["h"] - 2, 40, "at least 40px of touch height inside the hairline, as the arrows: %r" % pu["jump"])
        self.assertEqual(pu["jump"]["l"], pu["rect"]["l"], "left-aligned with the column: %r" % pu)
        self.assertLess(abs(pu["jump"]["t"] - pu["rect"]["b"] - GAP), 0.5, "one capsule gap below the column: %r" % pu)
        self.assertEqual((pu["rect"]["b"], pu["rect"]["h"]), (ph["rect"]["b"], ph["rect"]["h"]), "the column did not move as the chip came up: %r vs %r" % (pu["rect"], ph["rect"]))
        self.assertGreaterEqual(pu["rect"]["t"], pu["content"]["t"], "the column's top inside the pane: %r" % pu)
        self.assertLessEqual(pu["jump"]["b"], pu["composerTop"], "the chip clears the message box: %r" % pu)
        self.assertLessEqual(pu["jump"]["b"], pu["content"]["b"], "…and the pane's bottom edge: %r" % pu)
        for theme in ("dark", "light"):
            self._same_dress(r["phoneDress"][theme], 16, "0.8", "phone " + theme)
        print("COLUMN desktop %sx%s chip %sx%s; phone %sx%s chip %sx%s, top clearance %.0f, message-box clearance %.0f" % (
            s0["rect"]["w"], s0["rect"]["h"], s1["jump"]["w"], s1["jump"]["h"], ph["rect"]["w"], ph["rect"]["h"], pu["jump"]["w"], pu["jump"]["h"],
            pu["rect"]["t"] - pu["content"]["t"], pu["composerTop"] - pu["jump"]["b"]))

    def _same_dress(self, d, glyph_px, rest, where):
        """The go-to-bottom chip and a capsule, as the page computed them: one surface, hairline, shadow and radius, and the
        chip's chevron the arrows' own at their size, stroke and resting glow."""
        for k in ("bg", "bw", "bs", "bc", "shadow", "radius"):
            self.assertEqual(d["jump"][k], d["group"][k], "%s: the chip's %s is the capsule's: %r" % (where, k, d))
        self.assertEqual(d["jump"]["bw"], "1px", "%s: a hairline: %r" % (where, d["jump"]))
        jg, ag = d["jumpGlyph"], d["arrowGlyph"]
        self.assertIsNotNone(ag, "%s: a live down arrow to compare with: %r" % (where, d))
        self.assertEqual((jg["w"], jg["h"]), (glyph_px, glyph_px), "%s: the chip's chevron at the arrows' %dpx: %r" % (where, glyph_px, d))
        for k in ("w", "h", "stroke", "vb", "points", "opacity"):
            self.assertEqual(jg[k], ag[k], "%s: the chip's chevron %s is a down arrow's: %r" % (where, k, d))
        self.assertEqual(jg["opacity"], rest, "%s: resting, as the arrows rest: %r" % (where, jg))

if __name__ == "__main__":
    unittest.main()
