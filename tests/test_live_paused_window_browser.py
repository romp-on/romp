#!/usr/bin/env python3
"""T366 (the user 2026-09-12): the chat's "live updates are paused" strip appeared while they scrolled DOWN toward
the bottom of a live session. The landing audit named the pass behind it: a NAVIGATION frame (a card's or a lane's,
carrying a message's uuid and its time far back in history) whose window reply landed in the middle of a flick, so the
jump read as the scroll pausing the page; two page requests later the reader reached the tail and the strip went away.
Two things follow. The strip now names such a detach ("Showing the message from <clock> you opened; live updates are
paused") instead of the plain sentence. And the one window ask that is NO navigation, the re-land of the reader's own
row across a rebuild, which would produce the same landing with nothing to name, is refused: it never detaches an
attached reader. Whether a landing that arrives after the reader has scrolled since the click should still land is
held for the user.

The served guard drives the real /chat page over a transcript longer than the wire tail (older history stays on the
server) and lands proto-2 window frames on it through window.postMessage, the kernel's own frame shape:

  1. a reader attached to the live run, scrolled above the bottom, receives a window NOBODY navigated to (no
     request of theirs is in flight) whose verdict would detach: the window is not adopted, no strip shows, the
     resident tail stays on screen, and the client asks the kernel to re-base it on the tail (needFull, reattach).
     Red on main: the window replaced the run and the strip showed.
  2. the same page when the reader DID navigate (a focus frame with an anchor and its time into history the page does
     not hold, a card's road): the client asks for the window, the reply lands and detaches, and the strip names the
     jump and the opened message's time instead of the plain sentence: the rule refuses only the window nobody asked
     for.
  3. a one-write jump to the top of the resident run asks for older history exactly once, and the downward flick that
     follows asks for none: the direction is the reader's own gesture, never the page's compensating write (verifier
     medium 2: the re-window's write read as downward and refused the ask the base always made).

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none); the executed rules and the
wiring pins ride ui/webview/chat-window.test.ts. All fixtures synthetic.
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
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID = "aaaaaaaa-1111-2222-3333-444444444444"
TURNS = 320   # 640 events: past the wire tail, so older history stays on the server and the page's run is a tail
TOOL_TURN = 40   # the turn whose reply opens with four tool calls and ends with the words (T386's card-anchor road)
TOOL_TURN_TEXT = ("The four checks passed. Two questions for you: which bound do we keep for the retry curve, "
                  "and do we drop the second plot?")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER_HEAD = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(cfg.launch || {}); }   // a lab may ask for classic scrollbars (the settle lab's drag road): Playwright hides them headless by default
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
// every frame the page sends its kernel, by type: the window asks, the older asks and the re-attach ask are the evidence
await page.addInitScript(() => {
  const send = WebSocket.prototype.send;
  window.__sent = [];
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && m.type) window.__sent.push(m); } catch (e) { /* not a frame */ }
    return send.call(this, d);
  };
});
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForFunction(() => document.querySelectorAll("#content .turn[data-uuid]").length >= 40, null, { timeout: 30000 });
await page.waitForTimeout(500);
const state = () => page.evaluate(() => {
  const c = document.getElementById("content");
  const strip = document.getElementById("live-paused");
  // the run's rendered EVENT turns: a notice unit at the tail (an api-error note) carries a word, not a uuid
  const turns = Array.from(document.querySelectorAll("#content .turn[data-uuid]")).filter((t) => /^[0-9a-f-]{36}$/.test(t.dataset.uuid));
  return { top: c.scrollTop, sh: c.scrollHeight, ch: c.clientHeight,
           atBottom: c.scrollHeight - c.scrollTop - c.clientHeight <= 2,
           strip: !!strip && !strip.hidden && getComputedStyle(strip).display !== "none",   // fixed-position: no offsetParent to read
           firstUuid: turns.length ? turns[0].dataset.uuid : null, lastUuid: turns.length ? turns[turns.length - 1].dataset.uuid : null,
           turns: turns.length,
           sent: window.__sent.map((m) => m.type + (m.why ? ":" + m.why : "") + (m.what ? ":" + m.what + (m.data && m.data.writer ? ":" + m.data.writer : "") + (m.what === "windowask" && m.data ? ":nav=" + m.data.nav + ":reland=" + m.data.reland : "") : "")) };
});
const sentOf = (type) => page.evaluate((t) => window.__sent.filter((m) => m.type === t).length, type);
const boot = await state();
if (!boot.atBottom) { console.error("the page did not land at the bottom: " + JSON.stringify(boot)); process.exit(1); }
// OLDER events of the transcript itself (turns k0..k1, none resident: the page holds the tail), in the wire's shape, so a
// window around them does not overlap the run and the kernel can place any page ask that follows them
const pad = (n) => String(n).padStart(12, "0");
const older = (k0, k1) => Array.from({ length: k1 - k0 }, (_, i) => k0 + i).flatMap((k) => [
  { uuid: "11111111-2222-3333-4444-" + pad(2 * k), kind: "user", md: "question number " + k + " about the notes api", ts: new Date((cfg.base + 2 * k) * 1000).toISOString() },
  { uuid: "22222222-3333-4444-5555-" + pad(2 * k + 1), kind: "assistant", md: "Answer " + k + ": the handler reads the note by id and returns it.", ts: new Date((cfg.base + 2 * k + 1) * 1000).toISOString() }]);
"""

# road 1 and road 2 in one page: the unasked window first (the reader attached, above the bottom), then the deep link
DRIVER_LANDING = DRIVER_HEAD + r"""
// the reader scrolls up a screen and a half: attached (nothing asked), above the bottom
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollTop - Math.round(c.clientHeight * 1.5); });
await page.waitForTimeout(400);
const before = await state();
const olderAsksBefore = await sentOf("loadOlder");
const fullBefore = await sentOf("needFull");
// a window NOBODY asked for lands: the kernel's frame shape, a verdict that would detach (nothing of the run in it,
// more after it, not connected to the client's base); sixty turns, so an adopted window overflows the viewport and no
// edge check walks it back to the tail on its own
await page.evaluate((frame) => window.postMessage(frame, "*"), {
  type: "chatWindow", id: cfg.sid, anchor: "11111111-2222-3333-4444-" + pad(2 * 60), events: older(30, 90),
  moreBefore: true, moreAfter: true, connected: false });
await page.waitForTimeout(800);
const after = await state();
const fullAfter = await sentOf("needFull");
const reattach = await page.evaluate(() => window.__sent.filter((m) => m.type === "needFull" && m.why === "reattach").length);
// road 2: the reader NAVIGATES into history the page does not hold (a focus frame with an anchor kind, the deep-link
// road): the page asks for a window around it and the kernel's reply lands
const deep = cfg.deepUuid;
await page.evaluate((frame) => window.postMessage(frame, "*"), { type: "focus", id: cfg.sid, anchor: deep, anchorT: cfg.deepT });
try { await page.waitForFunction((u) => !!document.querySelector('#content .turn[data-uuid="' + u + '"]'), deep, { timeout: 20000 }); }
catch (e) { const st = await state(); console.error("the deep link never landed: " + JSON.stringify(st)); process.exit(1); }
await page.waitForTimeout(600);
const navigated = await state();
navigated.stripText = await page.evaluate(() => (document.querySelector("#live-paused .live-paused-text") || {}).textContent || "");
const windowAsks = await sentOf("loadAround");
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-landing.png" });
fs.writeSync(1, "RESULT:" + JSON.stringify({ boot, before, after, navigated, olderAsksBefore, fullBefore, fullAfter, reattach, windowAsks }) + "\n");
await browser.close();
process.exit(0);
"""

# road 3: a fast downward flick from the top band of the resident run
DRIVER_FLICK = DRIVER_HEAD + r"""
// to the top band of the resident run in ONE write (an upward move: the one older ask it may make is allowed), then
// wait for that ask's reply to settle the view
const olderAtBoot = await sentOf("loadOlder");
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = 1; });
await page.waitForTimeout(1500);
const settled = await state();
const olderAfterUp = await sentOf("loadOlder");
// the flick DOWN: forty wheel steps over the transcript, each a downward move, from wherever the reply left the reader
const box = await page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
await page.mouse.move(box.x, box.y);
for (let i = 0; i < 40; i++) { await page.mouse.wheel(0, 240); await page.waitForTimeout(16); }
await page.waitForTimeout(600);
const flicked = await state();
const olderAfterDown = await sentOf("loadOlder");
fs.writeSync(1, "RESULT:" + JSON.stringify({ boot, settled, flicked, olderDuringJump: olderAfterUp - olderAtBoot, olderDuringFlick: olderAfterDown - olderAfterUp }) + "\n");
await browser.close();
process.exit(0);
"""


class WindowLab(unittest.TestCase):
    """The boot: a hermetic kernel over a synthetic transcript longer than the wire tail, the real /chat page served
    from a copy of the built bundle. Subclassed by this module's tests and by the landing lab (T386,
    tests/test_landing_settles_browser.py); carries no tests of its own."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="live-paused-window-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "session-hosts").write_text("off\n")   # a lab root of its own: no session host (T348)
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # TURNS user/assistant pairs, one a second, ending in the past: past the wire tail, so the page holds a tail
        recs, prev = [], None
        base = 1789000000
        for k in range(TURNS):
            u = "11111111-2222-3333-4444-%012d" % (2 * k)
            a = "22222222-3333-4444-5555-%012d" % (2 * k + 1)
            tu = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base + 2 * k))
            ta = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base + 2 * k + 1))
            recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": tu, "sessionId": SID,
                         "message": {"role": "user", "content": "question number %d about the notes api" % k}})
            text = "Answer %d: the handler reads the note by id and returns it." % k
            pa = u
            if k == TOOL_TURN:
                # one turn whose FIRST atoms are tool calls (four, like a card's anchor on a Bash) and whose words come last
                # (T386): a card anchored on the first atom must land the reader on the words, not on the tool group
                for i in range(4):
                    tu_id = "toolu_%03d_%d" % (k, i)
                    tuu = "44444444-5555-6666-7777-%012d" % (10 * k + i)
                    tru = "55555555-6666-7777-8888-%012d" % (10 * k + i)
                    recs.append({"type": "assistant", "uuid": tuu, "parentUuid": pa, "timestamp": ta, "sessionId": SID,
                                 "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                                             "content": [{"type": "tool_use", "id": tu_id, "name": "Bash", "input": {"command": "true # check %d" % i}}]}})
                    recs.append({"type": "user", "uuid": tru, "parentUuid": tuu, "timestamp": ta, "sessionId": SID,
                                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu_id, "content": "ok"}]},
                                 "toolUseResult": {"stdout": "ok", "stderr": "", "interrupted": False, "isImage": False}})
                    pa = tru
                text = TOOL_TURN_TEXT
            recs.append({"type": "assistant", "uuid": a, "parentUuid": pa, "timestamp": ta, "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": text}]}})
            prev = a
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.transcript = os.path.join(proj, SID + ".jsonl")      # a lab that appends a live turn writes here (T386)
        cls.deep_uuid = "11111111-2222-3333-4444-%012d" % 20   # the eleventh question: far above the tail the page holds
        cls.deep_t = base + 20                                     # …and its time, as a card's focus frame carries it
        cls.tool_uuid = "44444444-5555-6666-7777-%012d" % (10 * TOOL_TURN)   # the tool turn's FIRST atom: a card's anchor (T386)
        cls.tool_t = base + 2 * TOOL_TURN + 1
        cls.tool_quote = "which bound do we keep for the retry curve"
        cls.base = base
        cls.port = _free_port()
        cls.token = "testtok-livepaused"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
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

    def _drive(self, script, name, extra=None):
        cfg = os.path.join(self.lab, name + ".json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID,
                       "deepUuid": self.deep_uuid, "deepT": self.deep_t, "base": self.base, "transcript": self.transcript,
                       "toolUuid": self.tool_uuid, "toolT": self.tool_t, "toolQuote": self.tool_quote,
                       "shots": os.environ.get("LIVE_PAUSED_SHOTS", ""), **(extra or {})}, f)
        driver = os.path.join(self.lab, name + ".mjs")
        with open(driver, "w") as f:
            f.write(script)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])


class ServedWindowLanding(WindowLab):
    def test_a_window_nobody_asked_for_never_detaches_an_attached_reader_and_a_deep_link_still_lands(self):
        r = self._drive(DRIVER_LANDING, "landing")
        print("RESULT:" + json.dumps(r), file=sys.stderr)   # the whole measurement rides a failure's captured stderr
        before, after, nav = r["before"], r["after"], r["navigated"]
        self.assertFalse(before["strip"], "attached before the window: no strip: %r" % before)
        self.assertFalse(before["atBottom"], "the reader is above the bottom: %r" % before)
        # road 1: the unasked window
        self.assertFalse(after["strip"], "a window nobody asked for showed the paused strip: %r" % after)
        self.assertEqual(after["lastUuid"], before["lastUuid"], "the resident tail left the screen: %r → %r" % (before["lastUuid"], after["lastUuid"]))
        self.assertNotEqual(after["firstUuid"], "11111111-2222-3333-4444-%012d" % 60, "the window's events were adopted: %r" % after)
        self.assertEqual(after["turns"], before["turns"], "the rendered run changed under the reader: %r" % after)
        self.assertGreaterEqual(r["reattach"], 1, "the kernel was not asked to re-base this client on the tail (needFull reattach): %r" % after["sent"])
        self.assertEqual(r["fullAfter"] - r["fullBefore"], r["reattach"], "the only full ask the window caused is the re-attach: %r" % after["sent"])
        # road 2: the reader's own navigation lands, detaches and says so
        self.assertGreaterEqual(r["windowAsks"], 1, "the deep link asked for no window: %r" % nav["sent"])
        self.assertTrue(nav["strip"], "a window the reader navigated to must land and show the strip: %r" % nav)
        self.assertTrue(nav["stripText"].startswith("Showing the message from ") and nav["stripText"].endswith(" you opened; live updates are paused."),
                        "the strip names the navigation and the opened message's time: %r" % nav["stripText"])

    def test_a_jump_to_the_top_asks_once_and_the_downward_flick_asks_for_no_older_history(self):
        r = self._drive(DRIVER_FLICK, "flick")
        print("RESULT:" + json.dumps(r), file=sys.stderr)
        self.assertEqual(r["olderDuringJump"], 1, "a one-write jump to the top asks once at the head, as the base did: %r" % r)
        self.assertEqual(r["olderDuringFlick"], 0, "the downward flick asked for older history: %r" % r)
        self.assertGreater(r["flicked"]["top"], r["settled"]["top"], "the flick moved down: %r" % r)


if __name__ == "__main__":
    unittest.main()
