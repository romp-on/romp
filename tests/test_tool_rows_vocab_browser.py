"""T418 (the user 2026-09-14): the chat's tool rows read the way the desktop app's do. A hermetic kernel over a synthetic transcript
whose one tool turn holds eleven Bash commands with descriptions, two Writes, three Edits and four Reads; the real /chat page served
from a copy of the built bundle, driven by Playwright. Roads: the collapsed group head speaks by action with the edits' totals; the
group expands to rows labelled by the model's description or the derived phrase; a row expands to its command and output; the same
in the light theme; screenshots of the collapsed head and one expanded row go to the drops folder for the reviewers. Synthetic only."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.dist_copy import copy_dist  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab  # noqa: E402
from test_live_paused_window_browser import _free_port  # noqa: E402

SID = "aaaaaaaa-1111-2222-3333-444444444418"
DESCS = ["Verified the dashboard bundle and the venv exist", "Located the venv and confirmed the PATH line", "Resolved the state dir and verified the package",
         "Checked the browser openers on PATH", "Appended the PATH line to the shell rc", "Launched the manager in the background", "Loaded the tools",
         "Waited for the dashboard port to answer", "Read the manager log", "Listed the state directory", "Printed the versions"]
EDITS = [(12, 0), (20, 0), (5, 0)]   # (+added, -removed) per Edit: +37 -0 in all
DROPS = os.path.join(os.path.expanduser("~"), ".local", "state", "romp", "drops")

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(cfg.launch || {}); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
const pageEvents = [];
page.on("pageerror", (e) => pageEvents.push("pageerror:" + String(e).slice(0, 300)));
const painted = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 0)))));
const shot = async (name) => { if (cfg.drops) { try { await page.screenshot({ path: cfg.drops + "/romp_chat-T418-" + name + "-1200.png" }); } catch (e) { pageEvents.push("shot:" + e); } } };
const groupHead = () => page.evaluate(() => { const g = document.querySelector("#content .turn-toolgroup"); if (!g) return null; const line = g.querySelector(".toolgroup-line"); return { text: line ? line.textContent.replace(/\s+/g, " ").trim() : null, expanded: g.classList.contains("expanded"), head: (g.querySelector(".toolgroup-head") || {}).textContent || null, plus: (g.querySelector(".tool-plus") || {}).textContent || null, minus: (g.querySelector(".tool-minus") || {}).textContent || null,
  plusColor: g.querySelector(".tool-plus") ? getComputedStyle(g.querySelector(".tool-plus")).color : null, minusColor: g.querySelector(".tool-minus") ? getComputedStyle(g.querySelector(".tool-minus")).color : null }; });
const rows = () => page.evaluate(() => Array.from(document.querySelectorAll("#content .turn-tool")).map((t) => ({ label: (t.querySelector(".tool-label") || t.querySelector(".tool-name") || {}).textContent || null, code: !!t.querySelector(".tool-label-code"), path: (t.querySelector(".tool-head .tool-file") || {}).textContent || null, totals: (t.querySelector(".tool-head > .tool-totals") || {}).textContent || null, err: t.classList.contains("tool-err"), toggle: (t.querySelector(".tool-fold-toggle") || {}).textContent || null,
  togglePlus: (() => { const e = t.querySelector(".tool-fold-toggle .tool-plus"); return e ? { text: e.textContent, color: getComputedStyle(e).color } : null; })(), toggleMinus: (() => { const e = t.querySelector(".tool-fold-toggle .tool-minus"); return e ? { text: e.textContent, color: getComputedStyle(e).color } : null; })(),
  head: t.querySelector(".tool-head") ? t.querySelector(".tool-head").textContent.replace(/\s+/g, " ").trim() : null, name: (t.querySelector(".tool-name") || {}).textContent || null })));
const run = async (theme) => {
  await page.addInitScript((th) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: th })); } catch (e) {} }, theme);
  await page.goto(cfg.chat);
  await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
  await page.waitForFunction(() => !!document.querySelector("#content .turn-toolgroup"), null, { timeout: 30000 }).catch(() => {});
  await painted();
  const light = await page.evaluate(() => document.body.classList.contains("theme-light"));
  const collapsed = await groupHead();
  const loneRows = (await rows()).filter((r) => r.head && /^(Searched|Used a tool|Glob|LS|Grep)/.test(r.head));   // the three lone rows sit outside the group, rendered before it expands
  await page.evaluate(() => { const g = document.querySelector("#content .turn-toolgroup .toolgroup-line"); if (g) g.scrollIntoView({ block: "center" }); });
  await painted();
  await shot("head-collapsed-" + theme);
  // expand the group: the rows appear
  await page.evaluate(() => { const l = document.querySelector("#content .turn-toolgroup .toolgroup-line"); if (l) l.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
  await page.waitForFunction(() => document.querySelectorAll("#content .turn-tool").length >= 20, null, { timeout: 10000 }).catch(() => {});
  await painted();
  const expanded = await groupHead();
  const rowList = await rows();
  // scroll the first Edit row into view for the expanded screenshot (round two, medium 1: the shot shows an Edit row)
  await page.evaluate(() => { const ts = Array.from(document.querySelectorAll("#content .turn-tool")); const t = ts.find((x) => /^Edited /.test((x.querySelector(".tool-head") || {}).textContent || "")); if (t) t.scrollIntoView({ block: "center" }); });
  await painted();
  await shot("edit-row-" + theme);
  // expand the third Bash row's fold: the command and its output
  const opened = await page.evaluate(() => { const ts = Array.from(document.querySelectorAll("#content .turn-tool")); const t = ts[2]; if (!t) return null; const tg = t.querySelector(".tool-fold-toggle"); if (tg) tg.dispatchEvent(new MouseEvent("click", { bubbles: true })); return true; });
  await painted();
  const io = await page.evaluate(() => { const t = Array.from(document.querySelectorAll("#content .turn-tool"))[2]; if (!t) return null; const body = t.querySelector(".tool-fold-body, .tool-io"); const vis = body ? getComputedStyle(body).display !== "none" : false; return { visible: vis, text: body ? body.textContent.replace(/\s+/g, " ").trim().slice(0, 200) : null }; });
  await page.evaluate(() => { const t = Array.from(document.querySelectorAll("#content .turn-tool"))[2]; if (t) t.scrollIntoView({ block: "center" }); });
  await painted();
  await shot("row-expanded-" + theme);
  return { light, collapsed, expanded, rows: rowList, loneRows, opened, io };
};
const dark = await run("yatharth");
const lightRun = await run("yatharth-light");
process.stdout.write("RESULT:" + JSON.stringify({ dark, light: lightRun, pageEvents: pageEvents.slice(0, 6) }) + "\n");
await browser.close();
"""


class ToolRowsVocab(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here), which the served guard needs")
        cls.lab = tempfile.mkdtemp(prefix="tool-rows-vocab-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "session-hosts").write_text("off\n")
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True,
             "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        base = int(time.time()) - 3600
        recs, prev = [], None
        stamp = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(s))
        for k in range(3):   # three plain turns before the tool turn
            u, a = "11111111-2222-3333-4444-%012d" % (2 * k), "22222222-3333-4444-5555-%012d" % (2 * k + 1)
            recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": stamp(base + 10 * k), "sessionId": SID,
                         "message": {"role": "user", "content": "question %d about the notes api" % k}})
            recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": stamp(base + 10 * k + 1), "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "answer %d: the handler reads the note by id." % k}]}})
            prev = a
        # the tool turn: eleven commands with descriptions, two writes, three edits, four reads, then the words
        u = "11111111-2222-3333-4444-%012d" % 900
        recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": stamp(base + 100), "sessionId": SID,
                     "message": {"role": "user", "content": "install the sdk and verify the dashboard"}})
        pa = u
        tools = [("Bash", {"command": "true # step %d" % i, "description": DESCS[i]}, "ok %d" % i) for i in range(11)]
        tools += [("Write", {"file_path": cwd + "/notes/new-%d.md" % i, "content": "line one\nline two\nline three"}, "written") for i in range(2)]
        tools += [("Edit", {"file_path": cwd + "/src/module-%d.ts" % i, "old_string": "", "new_string": "\n".join("added %d" % j for j in range(EDITS[i][0]))}, "edited") for i in range(3)]
        tools += [("Read", {"file_path": cwd + "/src/read-%d.py" % i}, "contents %d" % i) for i in range(4)]
        for i, (name, inp, out) in enumerate(tools):
            tu_id = "toolu_418_%02d" % i
            tuu = "44444444-5555-6666-7777-%012d" % (100 + i)
            tru = "55555555-6666-7777-8888-%012d" % (100 + i)
            t = stamp(base + 101 + i)
            recs.append({"type": "assistant", "uuid": tuu, "parentUuid": pa, "timestamp": t, "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": tu_id, "name": name, "input": inp}]}})
            recs.append({"type": "user", "uuid": tru, "parentUuid": tuu, "timestamp": t, "sessionId": SID,
                         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu_id, "content": out}]},
                         "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})
            pa = tru
        a = "22222222-3333-4444-5555-%012d" % 901
        recs.append({"type": "assistant", "uuid": a, "parentUuid": pa, "timestamp": stamp(base + 130), "sessionId": SID,
                     "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "The install completed; the dashboard bundle and the venv are in place."}]}})
        prev = a
        # three LONE tool uses, each its own turn (round two, medium 2): a Glob with a path, an LS with a path, a Grep with a path keep their file link
        lone = [("Glob", {"pattern": "**/*.ts", "path": cwd + "/src"}, "src/a.ts\nsrc/b.ts"), ("LS", {"path": cwd + "/notes"}, "new-0.md\nnew-1.md"),
                ("Grep", {"pattern": "handler", "path": cwd + "/src"}, "src/a.ts:3: handler")]
        for j, (name, inp, out) in enumerate(lone):
            u = "11111111-2222-3333-4444-%012d" % (910 + j); tu_id = "toolu_418_lone_%d" % j
            tuu = "44444444-5555-6666-7777-%012d" % (200 + j); tru = "55555555-6666-7777-8888-%012d" % (200 + j); a = "22222222-3333-4444-5555-%012d" % (920 + j)
            recs.append({"type": "user", "uuid": u, "parentUuid": prev, "timestamp": stamp(base + 140 + 10 * j), "sessionId": SID, "message": {"role": "user", "content": "one more look %d" % j}})
            recs.append({"type": "assistant", "uuid": tuu, "parentUuid": u, "timestamp": stamp(base + 141 + 10 * j), "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": tu_id, "name": name, "input": inp}]}})
            recs.append({"type": "user", "uuid": tru, "parentUuid": tuu, "timestamp": stamp(base + 141 + 10 * j), "sessionId": SID,
                         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu_id, "content": out}]}, "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})
            recs.append({"type": "assistant", "uuid": a, "parentUuid": tru, "timestamp": stamp(base + 142 + 10 * j), "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn", "content": [{"type": "text", "text": "noted %d." % j}]}})
            prev = a
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.cwd = cwd
        cls.port = _free_port()
        cls.token = "testtok-toolrows"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
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
        if self._r is None:
            drops = DROPS if os.environ.get("T418_SHOTS", "1") == "1" and os.path.isdir(os.path.dirname(DROPS)) else ""
            if drops:
                os.makedirs(drops, exist_ok=True)
            cfg = os.path.join(self.lab, "rows.json")
            with open(cfg, "w") as f:
                json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID, "drops": drops}, f)
            driver = os.path.join(self.lab, "rows.mjs")
            Path(driver).write_text(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300, env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if "browser-launch-failed" in p.stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            self.assertIsNotNone(line, "the driver produced no RESULT (stderr: %s)" % p.stderr[-2000:])
            type(self)._r = json.loads(line[len("RESULT:"):])
        print("ROWS:", json.dumps(self._r), file=sys.stderr)
        return self._r

    def test_the_collapsed_group_head_speaks_by_action_with_the_edits_totals(self):
        r = self._result()
        for theme in ("dark", "light"):
            c = r[theme]["collapsed"]
            self.assertIsNotNone(c, "%s: a tool group rendered" % theme)
            self.assertFalse(c["expanded"], "%s: the group starts collapsed: %r" % (theme, c))
            self.assertEqual(c["head"], "Ran 11 commands, read 4 files, edited 3 files, created 2 files", "%s: the head speaks by action, ordered by count: %r" % (theme, c))
            self.assertEqual((c["plus"], c["minus"]), ("+37", "-0"), "%s: the edits' totals in the diff colours: %r" % (theme, c))
        self.assertFalse(r["dark"]["light"]); self.assertTrue(r["light"]["light"], "the light theme applied for the second run")

    def test_the_rows_read_the_models_description_or_the_derived_phrase_and_expand_to_the_command_and_output(self):
        r = self._result()
        d = r["dark"]
        self.assertTrue(d["expanded"]["expanded"], "the click expanded the group: %r" % d["expanded"])
        lone_heads = {x["head"] for x in d["loneRows"]}
        rows = [x for x in d["rows"] if x["head"] not in lone_heads]
        self.assertEqual(len(rows), 20, "twenty rows in the group: %r" % [x["label"] for x in rows])
        self.assertEqual([x["label"] for x in rows[:11]], DESCS, "the Bash rows read the model's descriptions")
        self.assertEqual(rows[11]["label"], "Created ", "a Write reads Created + the path: %r" % rows[11])
        self.assertTrue((rows[11]["path"] or "").endswith("notes/new-0.md"), "…the path as a link: %r" % rows[11])
        self.assertEqual(rows[13]["label"], "Edited ", "an Edit reads Edited + the path: %r" % rows[13])
        self.assertTrue((rows[13]["path"] or "").endswith("src/module-0.ts"), rows[13])
        self.assertEqual(rows[13]["head"].count("+12 -0"), 1, "round two, medium 1: the Edit row's head carries its numbers ONCE, the fold toggle being the totals: %r" % rows[13]["head"])
        self.assertIsNone(rows[13]["totals"], "…no second totals span beside the toggle: %r" % rows[13])
        self.assertEqual(rows[13]["toggle"], "+12 -0", "…the toggle in the approved shape, a hyphen minus: %r" % rows[13])
        # 2026-09-18: the expanded row's numbers wear the folded summary's dress (tool-plus green, tool-minus red, the same tokens),
        # measured: the toggle holds the two spans, each the colour the collapsed head's span of the same class computes to
        head_colors = (d["collapsed"]["plusColor"], d["collapsed"]["minusColor"])
        self.assertTrue(all(head_colors), "the collapsed head's totals are coloured: %r" % (head_colors,))
        self.assertNotEqual(head_colors[0], head_colors[1], "green and red are two colours")
        self.assertEqual((rows[13]["togglePlus"], rows[13]["toggleMinus"]),
                         ({"text": "+12", "color": head_colors[0]}, {"text": "-0", "color": head_colors[1]}),
                         "the row's toggle numbers wear the summary's classes and colours (the base printed plain dim text): %r" % rows[13])
        self.assertEqual(rows[16]["label"], "Read ", "a Read reads Read + the path: %r" % rows[16])
        self.assertTrue((rows[16]["path"] or "").endswith("src/read-0.py"), rows[16])
        self.assertTrue(all(not x["err"] for x in rows), "no row failed")
        self.assertTrue(d["io"] and d["io"]["visible"], "the third command's row expanded to its command and output: %r" % d["io"])
        self.assertIn("true # step 2", d["io"]["text"] or "", "…the command: %r" % d["io"])
        self.assertIn("ok 2", d["io"]["text"] or "", "…and its output: %r" % d["io"])

    def test_a_lone_glob_ls_and_grep_with_a_path_each_keep_their_file_link(self):
        # round two, medium 2: every event with a file keeps its link, as the base did; a label naming the path names it AS the link
        r = self._result()
        lone = r["dark"]["loneRows"]
        self.assertEqual(len(lone), 3, "three lone tool rows outside the group: %r" % lone)
        glob = next((x for x in lone if x["head"].startswith("Searched for **/*.ts in ")), None)
        self.assertIsNotNone(glob, "the lone Glob reads Searched for <pattern> in + the link: %r" % lone)
        self.assertTrue((glob["path"] or "").endswith("/src"), "…its path as the link: %r" % glob)
        self.assertEqual(glob["head"].count("/src"), 1, "…printed once: %r" % glob["head"])
        grep = next((x for x in lone if x["head"].startswith("Searched for handler in ")), None)
        self.assertIsNotNone(grep, "the lone Grep reads Searched for <pattern> in + the link: %r" % lone)
        self.assertTrue((grep["path"] or "").endswith("/src"), grep)
        ls = next((x for x in lone if x["name"] == "LS"), None)
        self.assertIsNotNone(ls, "the lone LS keeps its name as the secondary label: %r" % lone)
        self.assertTrue((ls["path"] or "").endswith("/notes"), "…and its file as the link: %r" % ls)
        self.assertTrue(ls["head"].startswith("Used a tool"), ls["head"])


if __name__ == "__main__":
    unittest.main()
