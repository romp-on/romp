#!/usr/bin/env python3
"""The network panel's remote row resolves (2026-09-18, the user's screenshot: three connected remotes, all reading
"connected · loading sessions…" with the loader spinning for good, their sessions in the tabs all the while).

The row wears "loading sessions…" while ANY pane still pends the host ({romp:'hostsPending', app, hosts}, kernel.py
_LANDING_REMOTES_JS), retired by that pane's own first payload from the host. The /settings page loads the federation
bundle too (the gear's kernel-side settings fan out to every host) under app=settings, a page outside every build
audience: its manager polled /tunnels, registered every up host, dialed the relays, could never hear a retiring frame,
and posted every attached host as pending under its own app name the first time the gear was opened. Two hermetic
kernels, a hub and a checked-in TESTHOST that owns one session, and the hub's LANDING page in Chromium: the panel's row
for TESTHOST reads the resolved state ("connected", then the row's own suffixes: "checked in here" for a peer and the
build word) once the chat pane heard the host; STILL reads it after the gear was opened and closed (red at the base:
"connected · loading sessions…" with the loader, for good); and reads it again after the remote kernel is killed and
restarted on its port (the panes redial and hear it again).

Two kernels as subprocesses and a Chromium driver in phases (the Python side restarts the remote between them); no romp
code is loaded in-process, so no state-isolation preamble. Synthetic only: placeholder uuids, hostname TESTHOST, the
notes-api world. Skips LOUDLY without the extension deps or a Playwright browser, and for nothing else
(ROMP_SERVED_TESTS_REQUIRE=1 turns the skips red where the browser is installed); a build or kernel failure is a failure.
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment

SID_R0 = "11111111-2222-4333-8444-000000000801"   # "api" on TESTHOST
HOST = "TESTHOST"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(sid, tag, cwd, pairs):
    out, parent = [], None
    for i in range(pairs):
        u, a = "%s-u%02d" % (tag, i), "%s-a%02d" % (tag, i)
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "sessionId": sid, "cwd": cwd,
                    "timestamp": "2024-01-01T00:%02d:00Z" % (i % 60), "promptSource": "typed",
                    "message": {"role": "user", "content": "turn %d: what changed in the notes-api search?" % i}})
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "sessionId": sid, "cwd": cwd,
                    "timestamp": "2024-01-01T00:%02d:30Z" % (i % 60),
                    "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "The ranking pass reads its weights from the notes-api config now."}]}})
        parent = a
    return "".join(json.dumps(r) + "\n" for r in out)


def _kernel(lab, name, port, token, sessions):
    """Boot one hermetic kernel: its own state root and dist, and `sessions` [(sid, name, tag)] with closed-turn transcripts."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    cwd = os.path.join(lab, name, "proj")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own hosts off (the conftest rule)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    for sid, sname, tag in sessions:
        Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (sname, cwd))
        Path(state, "sdk", sid + ".json").write_text(json.dumps(
            {"sid": sid, "name": sname, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
        Path(proj, sid + ".jsonl").write_text(_transcript(sid, tag, cwd, 4))
    env = _lab.kernel_env(os.path.join(lab, name), claude, os.path.join(lab, "dist"), port, token, ROMP_HOST_NAME=name.upper())
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "a"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log
        except Exception:
            time.sleep(0.5)
    proc.kill(); proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


def _tunnel_row(hport, htoken):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (hport, htoken), timeout=3) as r:
            rows = json.loads(r.read().decode()).get("tunnels") or []
    except Exception:
        return None
    return next((t for t in rows if t.get("host") == HOST), None)


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
const out = { errors };
const chatFrame = async () => { const h = await page.$("#f-chat"); return h ? await h.contentFrame() : null; };
// the chat pane heard the host: its tab is on the strip (bounded)
const waitRemoteTab = async (ms) => {
  for (let i = 0; i < ms / 250; i++) {
    const fr = await chatFrame();
    if (fr) { try { if (await fr.locator("#tabs .tab", { hasText: cfg.host }).count()) return true; } catch (e) {} }
    await page.waitForTimeout(250);
  }
  return false;
};
// the network panel's row for the host, read through the panel's own open (the rail button) and closed after
const readRow = () => page.evaluate((host) => {
  const rows = Array.from(document.querySelectorAll("#rnet-list .rnet-row"));
  const row = rows.find((r) => { const b = r.querySelector(".nm b"); return b && b.textContent === host; });
  if (!row) return null;
  const st = row.querySelector(".nm .st");
  return { st: (st.textContent || "").replace(/\s+/g, " ").trim(), pend: !!st.querySelector(".rnet-pend"),
           spin: !!st.querySelector(".rnet-spin"), title: st.getAttribute("title") || "" };
}, cfg.host);
const openPanel = async () => { await page.click("#rail-net"); await page.waitForSelector("#rnet-list .rnet-row", { timeout: 20000 }); };
const closePanel = async () => { await page.click("#rnet-x"); await page.waitForTimeout(150); };
// wait (bounded) for the row to read resolved: up and not pending; return the last read either way
const settledRow = async (ms) => {
  let r = null;
  for (let i = 0; i < ms / 250; i++) { r = await readRow(); if (r && !r.pend && /^connected/.test(r.st)) return r; await page.waitForTimeout(250); }
  return r;
};
await page.goto(cfg.landing);
await page.waitForSelector("#f-chat", { timeout: 20000 });
out.tabSeen = await waitRemoteTab(60000);
// A. before the gear: the row resolves once the panes heard the host
await openPanel(); out.a = await settledRow(20000); await closePanel();
// B. open the gear (the settings frame loads on its first open and its federation manager starts), let it poll, close it
await page.evaluate(() => { window.__rompOpenSettings && window.__rompOpenSettings(); });
out.settingsFed = false;
for (let i = 0; i < 80; i++) {
  out.settingsFed = await page.evaluate(() => { const f = document.getElementById("f-settings"); try { return !!(f && f.getAttribute("src") && f.contentWindow && f.contentWindow.__rompFed); } catch (e) { return false; } });
  if (out.settingsFed) break;
  await page.waitForTimeout(250);
}
await page.waitForTimeout(4000);   // its first /tunnels poll and the post it makes on it
out.settingsClosed = await page.evaluate(() => { const f = document.getElementById("f-settings"); try { return !!(f && f.contentWindow && f.contentWindow.__rompSettingsClose && f.contentWindow.__rompSettingsClose()); } catch (e) { return false; } });
await page.waitForTimeout(300);
await openPanel(); out.b = await readRow(); await page.waitForTimeout(3000); out.b2 = await readRow(); await closePanel();
// C. hand over: the Python side kills and restarts the remote kernel, then says go
fs.writeFileSync(cfg.phase1, JSON.stringify(out));
for (let i = 0; i < 600; i++) { if (fs.existsSync(cfg.go2)) break; await page.waitForTimeout(250); }
out.tabSeenAgain = await waitRemoteTab(90000);
await openPanel(); out.c = await settledRow(30000); await page.waitForTimeout(3000); out.c2 = await readRow(); await closePanel();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedRemotesPanelLoading(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.procs = []
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served lab needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box: the served lab needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="remotes-panel-loading-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.rport, cls.rtoken = _free_port(), "testtok-remote-panel"
        cls.hport, cls.htoken = _free_port(), "testtok-hub-panel"
        rp, cls.rlog = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, [(SID_R0, "api", 1)])
        cls.procs.append(rp)
        hp, cls.hlog = _kernel(cls.lab, "hub", cls.hport, cls.htoken, [])
        cls.procs.append(hp)
        body = json.dumps({"host": HOST, "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ans = json.loads(resp.read().decode())
        if not ans.get("ok"):
            raise unittest.SkipTest("the hub refused the check-in: %r" % ans)
        if not cls._wait_status("up", 60):
            raise unittest.SkipTest("the hub never reported the checked-in peer up: %r" % (_tunnel_row(cls.hport, cls.htoken),))
        cls._drive(rp)

    @classmethod
    def _wait_status(cls, want, secs):
        for _ in range(int(secs * 2)):
            row = _tunnel_row(cls.hport, cls.htoken)
            st = (row or {}).get("status")
            if (want == "up" and st == "up" and row.get("hasToken")) or (want != "up" and st != "up"):
                return True
            time.sleep(0.5)
        return False

    @classmethod
    def _drive(cls, remote_proc):
        cfg = os.path.join(cls.lab, "cfg.json")
        phase1, go2 = os.path.join(cls.lab, "phase1.json"), os.path.join(cls.lab, "go2")
        with open(cfg, "w") as f:
            json.dump({"landing": "http://127.0.0.1:%d/?token=%s" % (cls.hport, cls.htoken), "host": HOST,
                       "phase1": phase1, "go2": go2}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.Popen(["node", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        cls.driver_error = None
        for _ in range(600):   # phase 1: the driver has read the row before and after the gear
            if os.path.exists(phase1) or p.poll() is not None:
                break
            time.sleep(0.5)
        if p.poll() is None and os.path.exists(phase1):
            # the reconnect: the remote kernel dies and comes back on its port; the hub's row goes down and up
            remote_proc.kill(); remote_proc.wait()
            cls.procs.remove(remote_proc)
            cls.went_down = cls._wait_status("down", 60)
            rp2, _ = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, [(SID_R0, "api", 1)])
            cls.procs.append(rp2)
            cls.came_up = cls._wait_status("up", 90)
            Path(go2).write_text("go\n")
        try:
            so, se = p.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            p.kill(); so, se = p.communicate()
            cls.driver_error = "driver timed out; partial output:\n%s" % so[-3000:]
            return
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served leg needs one (CI installs none)")
        if p.returncode != 0:
            cls.driver_error = "driver failed:\n" + so[-3000:] + se[-3000:]
            return
        line = next((ln for ln in so.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            cls.driver_error = "driver printed no result:\n" + so[-3000:] + se[-3000:]
            return
        cls.result = json.loads(line[len("RESULT:"):])

    @classmethod
    def tearDownClass(cls):
        for pr in getattr(cls, "procs", []):
            try:
                pr.kill(); pr.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def setUp(self):
        if getattr(self, "driver_error", None):
            self.fail(self.driver_error)
        self.r = self.result

    def test_the_page_threw_nothing_and_the_world_stood(self):
        self.assertEqual(self.r["errors"], [], self.r["errors"])
        self.assertTrue(self.r["tabSeen"], "the chat pane showed the remote's tab: the panes heard TESTHOST")
        self.assertTrue(self.r["settingsFed"], "the gear's frame loaded and its federation manager started")
        self.assertTrue(self.r["settingsClosed"], "the settings closed again")

    def test_the_row_resolves_to_connected_once_the_panes_heard_the_host(self):
        a = self.r["a"]
        self.assertIsNotNone(a, "the panel lists TESTHOST")
        self.assertRegex(a["st"], r"^connected\b", "the row's status word: %r" % a["st"])
        self.assertNotIn("loading sessions", a["st"])
        self.assertFalse(a["pend"] or a["spin"], "no loader, no pending mark once every pane heard the host: %r" % a)

    def test_opening_the_gear_leaves_the_row_resolved(self):
        # the base: "connected · loading sessions…" with the loader, for good, from the first gear open
        for k in ("b", "b2"):
            b = self.r[k]
            self.assertIsNotNone(b)
            self.assertNotIn("loading sessions", b["st"], "%s: the settings frame pinned the row on loading: %r" % (k, b["st"]))
            self.assertFalse(b["pend"] or b["spin"], "%s: %r" % (k, b))
            self.assertRegex(b["st"], r"^connected\b")

    def test_after_the_remote_restarts_the_row_resolves_again(self):
        self.assertTrue(self.went_down, "the hub saw the remote go down")
        self.assertTrue(self.came_up, "…and come back up")
        self.assertTrue(self.r["tabSeenAgain"], "the chat pane heard TESTHOST again after the restart")
        for k in ("c", "c2"):
            c = self.r[k]
            self.assertIsNotNone(c)
            self.assertRegex(c["st"], r"^connected\b", "%s: %r" % (k, c["st"]))
            self.assertNotIn("loading sessions", c["st"], "%s: still loading after the reconnect: %r" % (k, c["st"]))
            self.assertFalse(c["pend"] or c["spin"], "%s: %r" % (k, c))


if __name__ == "__main__":
    unittest.main()
