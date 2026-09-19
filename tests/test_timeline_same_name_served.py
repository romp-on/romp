#!/usr/bin/env python3
"""A remote session that shares a local session's NAME on the merged timeline (2026-09-18, the user's three screenshots):
the remote lane draws its own bars, a message from the local twin to the remote twin names the remote session with its host
prefix and draws its connector to the remote lane, and a compact addressed to the remote twin from the timeline reaches the
remote kernel, never the local twin.

Two hermetic kernels: a hub owning "web", and a checked-in TESTHOST owning "web" (the same name) and "api" (a control lane
on the same host). The hub's postal log carries one relayed send from its web to TESTHOST's web, in the row shape the bus
writes for a cross-host send (to_id the relay address "peer:<host>", toName "<host>:<name>", to_sid the recipient's stable
id). The hub's /timeline page in Chromium; the merged frames the federation manager hands the pane are captured through a
wrapped onFrame, and the SVG is read for each lane's bars and for the connector paths (data-tl-from, data-tl-to).

At the base (2026-09-18, measured here): the remote twin's lane drew its own five bars and the local twin its own six, the
merged turns keyed by prefixed sid, so the empty lane of the report did not reproduce with two same-version kernels; the bar
counts stay pinned as the guard. The message did: the hub kernel's row kept the relay address as toId and looked the display
name up by it, so the tooltip read "web → peer:TESTHOST", the merge could match no lane, and the sender's lane wore an
outgoing stub (the hook in the screenshot) while TESTHOST's copy of the same message drew a second, separate connector whose
tooltip named the recipient bare ("web → web"). The reverse leg: the lab kernels hold sends under a usage hold, so a compact
for a name parks as an op in the OWNING kernel's pending-ops.json under its sid: TESTHOST's for "TESTHOST:web", the hub's for
the bare "web".

Two kernels as subprocesses and a Chromium driver; no romp code is loaded in-process, so no state-isolation preamble.
Synthetic only: placeholder uuids, hostname TESTHOST, the notes-api world. Skips LOUDLY without the extension deps or a
Playwright browser, and for nothing else (ROMP_SERVED_TESTS_REQUIRE=1 turns the skips red where the browser is installed).
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
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment

HOST = "TESTHOST"
SID_L_WEB = "11111111-2222-4333-8444-000000000901"   # "web" on the hub
SID_R_WEB = "11111111-2222-4333-8444-000000000902"   # "web" on TESTHOST: the same-named twin
SID_R_API = "11111111-2222-4333-8444-000000000903"   # "api" on TESTHOST: the control lane
MID = "m-cross-1"                                    # the hub bus's id for the send
DMID = "m-cross-1-landed"                            # TESTHOST bus's id for the delivered copy (the read receipt's dmid)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turns(sid, tag, t0, n):
    """`n` CLOSED user/assistant turns for `sid`, one every four minutes from t0, inside the timeline's horizon."""
    out, parent = [], None
    for k in range(n):
        u, a = "%s-u%02d" % (tag, k), "%s-a%02d" % (tag, k)
        t = t0 + 240 * k
        out.append({"type": "user", "timestamp": _iso(t), "uuid": u, "parentUuid": parent, "sessionId": sid, "promptSource": "typed",
                    "message": {"role": "user", "content": "turn %d: what changed in the notes-api search?" % k}})
        out.append({"type": "assistant", "timestamp": _iso(t + 90), "uuid": a, "parentUuid": u, "sessionId": sid,
                    "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "The ranking pass reads its weights from the notes-api config now."}]}})
        parent = a
    return out


def _kernel(lab, name, port, token, sessions, postal_rows=()):
    """Boot one hermetic kernel: its own state root and dist, `sessions` [(sid, name, tag, turns)] with recent closed turns, and
    `postal_rows` appended to its timeline/messages.jsonl."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    cwd = os.path.join(lab, name, "proj")
    for d in ("names", "sdk", "states", "timeline"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own hosts off (the conftest rule)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends: the reverse leg's observable
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    now = int(time.time())
    for sid, sname, tag, n in sessions:
        Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (sname, cwd))
        Path(state, "sdk", sid + ".json").write_text(json.dumps(
            {"sid": sid, "name": sname, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in _turns(sid, tag, now - 3000, n)))
    if postal_rows:
        Path(state, "timeline", "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in postal_rows))
    env = _lab.kernel_env(os.path.join(lab, name), claude, os.path.join(lab, "dist"), port, token, ROMP_HOST_NAME=name.upper())
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "a"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log, state
        except Exception:
            time.sleep(0.5)
    proc.kill(); proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const ctx = await browser.newContext({ viewport: { width: 1400, height: 700 } });
// capture the MERGED frames the federation manager hands the pane: wrap onFrame the moment the manager publishes itself
await ctx.addInitScript(() => {
  window.__labFrames = [];
  let fed;
  Object.defineProperty(window, "__rompFed", {
    configurable: true,
    get() { return fed; },
    set(v) {
      if (v && typeof v.onFrame === "function") {
        const of = v.onFrame.bind(v);
        v.onFrame = (h) => of((ev) => { try { window.__labFrames.push(ev && ev.data); } catch (e) {} return h(ev); });
      }
      fed = v;
    },
  });
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
const out = { errors };
await page.goto(cfg.timeline);
const ids = cfg.ids;   // { lweb, rweb, rapi } as the merged board spells them
// wait (bounded) for the three lanes and a bars frame carrying turns for all three lane ids
const ready = () => page.evaluate((ids) => {
  const texts = Array.from(document.querySelectorAll("svg text")).map((t) => t.textContent);
  const bars = (window.__labFrames || []).filter((m) => m && m.type === "bars");
  const last = bars[bars.length - 1];
  const has = (k) => !!(last && last.turns && Array.isArray(last.turns[k]) && last.turns[k].length);
  return texts.indexOf("web") >= 0 && texts.indexOf("TESTHOST:web") >= 0 && texts.indexOf("TESTHOST:api") >= 0
    && has(ids.lweb) && has(ids.rapi) && (has(ids.rweb) || bars.length >= 3);
}, ids);
let ok = false;
for (let i = 0; i < 360; i++) { ok = await ready(); if (ok) break; await page.waitForTimeout(250); }
out.ready = ok;
await page.waitForTimeout(1500);   // the draw after the last frame
out.read = await page.evaluate((ids) => {
  const frames = window.__labFrames || [];
  const data = frames.filter((m) => m && m.type === "data").pop();
  const bars = frames.filter((m) => m && m.type === "bars").pop();
  const sessions = ((data && data.data && data.data.sessions) || []).map((s) => ({ id: s.id, name: s.name, host: s.host || "", live: !!s.live }));
  const turns = {}; for (const k of Object.keys((bars && bars.turns) || {})) turns[k] = (bars.turns[k] || []).length;
  const messages = ((bars && bars.messages) || (data && data.data && data.data.messages) || []).map((m) => ({ id: m.id, dmid: m.dmid || null, fromId: m.fromId, toId: m.toId, from: m.from, to: m.to, hasExec: !!m.hasExec, pending: !!m.pending }));
  const stubs = Array.from(document.querySelectorAll("path[data-tl-stub]")).map((p) => p.getAttribute("data-tl-stub"));
  // the SVG: lane label rows by name, bar rects bucketed to the nearest lane row by their vertical centre
  const lanes = Array.from(document.querySelectorAll("svg text")).filter((t) => ["web", "TESTHOST:web", "TESTHOST:api"].indexOf(t.textContent) >= 0 && t.getAttribute("font-weight") !== "400")
    .map((t) => ({ name: t.textContent, y: +t.getAttribute("y") }));
  const laneOf = (yc) => { let best = null, d = Infinity; for (const l of lanes) { const dd = Math.abs(l.y - yc); if (dd < d) { d = dd; best = l.name; } } return d <= 13 ? best : null; };
  const barCount = {}; for (const l of lanes) barCount[l.name] = 0;
  for (const r of Array.from(document.querySelectorAll("svg rect"))) {
    const h = +r.getAttribute("height"), w = +r.getAttribute("width"), y = +r.getAttribute("y");
    if (!(h > 3 && h < 14 && w > 4)) continue;          // a work bar: BAR_H tall, some width; not a chip, not a stub box, not a lane hit target
    const lane = laneOf(y + h / 2); if (lane) barCount[lane]++;
  }
  const connectors = Array.from(document.querySelectorAll("path[data-tl-from], path[data-tl-to]")).map((p) => ({ from: p.getAttribute("data-tl-from"), to: p.getAttribute("data-tl-to") }));
  return { sessions, turns, messages, lanes, barCount, connectors, stubs };
}, ids);
// the reverse leg, through the hooks the lane's own battery click and picker call (timeline-boot.ts installs them):
// a compact and a command addressed to the remote twin by the merged board's display name, then a compact to the
// local twin as the control
out.hooks = await page.evaluate(() => typeof window.__rompTimelineCompact === "function" && typeof window.__rompTimelineSendCommand === "function");
await page.evaluate(() => { window.__rompTimelineCompact("TESTHOST:web"); });
await page.waitForTimeout(800);
await page.evaluate(() => { window.__rompTimelineSendCommand("TESTHOST:web", "/effort high"); });
await page.waitForTimeout(800);
await page.evaluate(() => { window.__rompTimelineCompact("web"); });
await page.waitForTimeout(2000);
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/timeline-same-name.png" }); }
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedTimelineSameName(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="tl-same-name-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.rport, cls.rtoken = _free_port(), "testtok-remote-tl"
        cls.hport, cls.htoken = _free_port(), "testtok-hub-tl"
        now = int(time.time())
        landed = {"t": now - 880, "ev": "sent", "id": DMID, "from": "web", "from_id": SID_L_WEB, "to_id": SID_R_WEB,
                  "body": "Synthetic heads-up across hosts.", "kind": "coordinate", "from_host": "HUB", "originMid": MID}
        read = {"t": now - 840, "ev": "exec", "id": DMID}
        rp, cls.rlog, cls.rstate = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, [(SID_R_WEB, "web", "rw", 5), (SID_R_API, "api", "ra", 4)],
                                           postal_rows=[landed, read])
        cls.procs.append(rp)
        now = int(time.time())
        # the hub's web mailed TESTHOST's web through the relay, and TESTHOST's web read it: the rows each bus writes.
        # The hub's: the relay's sent row (to_id the relay address, toName and to_sid the recipient) and the read
        # receipt's exec row carrying the recipient's own delivery mid. TESTHOST's: its deliver() row under that
        # delivery mid, from_id the sender's bare sid, from_host the sender's host, and its own exec when read.
        body = "Synthetic heads-up across hosts."
        cross = {"t": now - 900, "ev": "sent", "id": MID, "from": "web", "from_id": SID_L_WEB, "to_id": "peer:" + HOST,
                 "toName": "%s:web" % HOST, "to_sid": SID_R_WEB, "body": body, "kind": "coordinate", "from_host": ""}
        receipt = {"t": now - 840, "ev": "exec", "id": MID, "dmid": DMID}
        hp, cls.hlog, cls.hstate = _kernel(cls.lab, "hub", cls.hport, cls.htoken, [(SID_L_WEB, "web", "lw", 6)], postal_rows=[cross, receipt])
        cls.procs.append(hp)
        body = json.dumps({"host": HOST, "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ans = json.loads(resp.read().decode())
        if not ans.get("ok"):
            raise unittest.SkipTest("the hub refused the check-in: %r" % ans)
        for _ in range(120):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (cls.hport, cls.htoken), timeout=3) as r2:
                    rows = json.loads(r2.read().decode()).get("tunnels") or []
            except Exception:
                rows = []
            row = next((t for t in rows if t.get("host") == HOST), None)
            if row and row.get("status") == "up" and row.get("hasToken"):
                break
            time.sleep(0.5)
        else:
            raise unittest.SkipTest("the hub never reported the checked-in peer up")
        cls._drive()
        cls.hub_ops = cls._ops(cls.hstate)
        cls.remote_ops = cls._ops(cls.rstate)

    @staticmethod
    def _ops(state):
        """The kernel's parked drive ops, sid -> [op...] (its pending-ops.json mirror), plus any drive refusals it recorded
        (undelivered.jsonl rows) under "undelivered", so a compact that reached the wrong kernel shows up whichever door it took."""
        out = {}
        p = Path(state, "pending-ops.json")
        try:
            out = json.loads(p.read_text()) if p.exists() else {}
        except Exception:
            out = {"unreadable": True}
        u = Path(state, "undelivered.jsonl")
        if u.exists():
            out["undelivered"] = [json.loads(ln) for ln in u.read_text().splitlines() if ln.strip()]
        return out

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"timeline": "http://127.0.0.1:%d/timeline?token=%s" % (cls.hport, cls.htoken),
                       "ids": {"lweb": SID_L_WEB, "rweb": "%s:%s" % (HOST, SID_R_WEB), "rapi": "%s:%s" % (HOST, SID_R_API)},
                       "shots": os.environ.get("TL_SAME_NAME_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served leg needs one (CI installs none)")
        assert p.returncode == 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        assert line, "driver printed no result:\n" + p.stdout[-3000:]
        cls.r = json.loads(line[len("RESULT:"):])
        if os.environ.get("TL_SAME_NAME_SHOTS"):
            with open(os.path.join(os.environ["TL_SAME_NAME_SHOTS"], "result.json"), "w") as f:
                json.dump({"r": cls.r, "hub_ops": cls._ops(cls.hstate), "remote_ops": cls._ops(cls.rstate)}, f, indent=1)

    @classmethod
    def tearDownClass(cls):
        for pr in getattr(cls, "procs", []):
            try:
                pr.kill(); pr.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_the_page_threw_nothing_and_every_lane_arrived(self):
        self.assertEqual(self.r["errors"], [], self.r["errors"])
        self.assertTrue(self.r["ready"], "the three lanes and their bars frames arrived: %r" % self.r["read"].get("lanes"))
        names = sorted(l["name"] for l in self.r["read"]["lanes"])
        self.assertEqual(names, ["TESTHOST:api", "TESTHOST:web", "web"], "one lane per session, the remote twin under its host prefix")
        ids = sorted(s["id"] for s in self.r["read"]["sessions"])
        self.assertEqual(ids, sorted([SID_L_WEB, "%s:%s" % (HOST, SID_R_WEB), "%s:%s" % (HOST, SID_R_API)]), "lane identity is the (prefixed) sid")

    def test_the_remote_twins_lane_draws_its_own_bars_and_the_local_twin_only_its_own(self):
        rd = self.r["read"]
        rweb, rapi = "%s:%s" % (HOST, SID_R_WEB), "%s:%s" % (HOST, SID_R_API)
        self.assertEqual((rd["turns"].get(SID_L_WEB), rd["turns"].get(rweb), rd["turns"].get(rapi)), (6, 5, 4),
                         "the merged bars carry each lane's own turns under its own id: %r" % rd["turns"])
        self.assertGreaterEqual(rd["barCount"]["TESTHOST:web"], 5, "the remote twin's lane draws its bars, not an empty row: %r" % rd["barCount"])
        self.assertGreaterEqual(rd["barCount"]["TESTHOST:api"], 4, "the control lane on the same host: %r" % rd["barCount"])
        self.assertLessEqual(rd["barCount"]["web"], 6 + 1, "the local twin draws only its own six (a live edge at most): %r" % rd["barCount"])
        self.assertGreaterEqual(rd["barCount"]["web"], 6)

    def test_a_message_to_the_remote_twin_names_it_with_its_host_and_draws_one_connector_to_its_lane(self):
        rd = self.r["read"]
        rweb = "%s:%s" % (HOST, SID_R_WEB)
        rows = [x for x in rd["messages"] if x["id"] in (MID, DMID)]
        self.assertEqual(len(rows), 1, "the two kernels' copies of the one message are ONE row on the merged board: %r" % rd["messages"])
        m = rows[0]
        self.assertEqual((m["fromId"], m["toId"]), (SID_L_WEB, rweb),
                         "the recipient is the remote twin's lane, never the relay address 'peer:%s': %r" % (HOST, m))
        self.assertEqual((m["from"], m["to"]), ("web", "%s:web" % HOST), "the recipient's display name carries its host, as the lane label does: %r" % m)
        self.assertEqual(rd["connectors"], [{"from": SID_L_WEB, "to": rweb}], "one full connector from the local twin to the remote twin's lane: %r" % rd["connectors"])
        self.assertEqual(rd["stubs"], [], "no hidden-counterpart stub hangs off either twin: both ends have lanes")

    def test_a_compact_addressed_to_the_remote_twin_reaches_the_remote_kernel_and_the_local_twin_is_untouched(self):
        # the lab kernels hold sends under a usage hold, so each compact parks as an op under the OWNING kernel's sid
        self.assertTrue(self.r.get("hooks"), "the served page installs the pane's compact and command hooks")
        self.assertNotIn("unreadable", self.remote_ops); self.assertNotIn("unreadable", self.hub_ops)
        self.assertNotIn("undelivered", self.remote_ops, "no drive op was refused on TESTHOST: %r" % self.remote_ops)
        self.assertNotIn("undelivered", self.hub_ops, "no drive op was refused on the hub: %r" % self.hub_ops)
        self.assertEqual(self.remote_ops.get(SID_R_WEB), [["compact"], ["effort", "high"]],
                         "TESTHOST parked the compact and the command for ITS web, in press order: %r" % self.remote_ops)
        self.assertNotIn(SID_R_API, self.remote_ops, "nothing parked for the other session on that host")
        self.assertEqual(self.hub_ops.get(SID_L_WEB), [["compact"]],
                         "the hub parked the control's one compact for its own web and nothing addressed to the twin: %r" % self.hub_ops)


if __name__ == "__main__":
    unittest.main()
