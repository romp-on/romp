#!/usr/bin/env python3
"""Staging over a question picker, executed in a real browser (the user 2026-09-25, who wanted ⌘⏎ to stage whatever the
typing order, and the box to go straight back to answering the picker).

While a session waits on a question with a free-text slot, the message box is the question's "add your own answer"
field. ⌘⏎ (Ctrl+⏎) stages what is typed instead of sending it, and it used to refuse there with a toast, but only for a
draft begun AFTER the question arrived: a draft already under way staged fine. The user's call: ⌘⏎ always stages, the
emptied box is the answer field again, and the staged items wait through the answer (they do not ride inside it) and go
with the next ordinary message or Send now, after the answer.

The unit tests (ui/webview/reload-notices.test.ts, staged-list-cap.test.ts) execute the lifted closures; this drives the
real /chat page against a hermetic kernel. The question is the kernel's own frame (askLive, the shape the SDK backend
pushes), dispatched on the page's frame listener; the kernel's own picker frames for the session are held back so it
cannot clear the lab's question mid-run. Every frame the page sends about the question or a message is recorded and
swallowed at the socket, so nothing reaches the kernel's (absent) CLI. Asserted, in order:
  1. question up, a line typed, ⌘⏎: one staged item, the box empty and still answering (tint and placeholder), no toast;
  2. an answer typed, ⏎: the answer goes to the question's free-text path (addCustomAsk), the staged item stays, and a
     click on an option (answerAsk) leaves it too;
  3. the strip's label says "after you answer" while the question is up and the plain words once it is answered, and
     again when a new question arrives, without the strip being rebuilt by anything else;
  4. an empty ⏎ with the question up releases the staged item as an ordinary message (it queues behind the answer);
  5. a draft begun BEFORE the question arrived (the box is not the answer field then) stages too, and the emptied box
     turns into the answer field.
SHOT_DIR=<dir> (optionally SHOT_NAME=<stem>) saves the composer area right after the first ⌘⏎.

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none); it executes on any dev box with the
extension installed. All fixtures synthetic (the notes-api demo domain).
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)

SID = "aaaaaaaa-5555-6666-7777-888888888888"
STAGED = "check the migration notes first"
ANSWER = "Postgres, with the pooled driver"
EARLY = "a note written before the question"
LABEL_PICKER = "1 staged — sends with your next message, after you answer"
LABEL_PLAIN = "1 staged — sends with your next message"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 900, height: 640 } });
page.on("pageerror", (e) => console.error("pageerror: " + e));
await page.addInitScript((sid) => {
  window.__out = [];
  // the lab owns the session's question: the kernel's own askLive/askLiveClear for it are held back (the lab's carry __lab)
  window.addEventListener("message", (e) => { const m = e.data;
    if (m && (m.type === "askLive" || m.type === "askLiveClear") && m.id === sid && !m.__lab) e.stopImmediatePropagation(); }, true);
  // what the page sends about the question or a message is recorded and swallowed: no CLI stands behind the session
  const KEEP = ["addCustomAsk", "askText", "answerAsk", "toggleAsk", "submitAsk", "sendMessage", "followUp"];
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && KEEP.includes(m.type)) { window.__out.push({ type: m.type, text: m.text, target: m.target }); return; } } catch (e) {}
    return orig.call(this, d);
  };
}, cfg.sid);

const ASK = { kind: "single", header: "Database", question: "Which database should the notes-api use?",
  options: [{ n: 1, label: "Postgres", selected: true }, { n: 2, label: "SQLite", selected: false }, { n: 3, label: "Type something.", selected: false }],
  cursor: 1, cursorFound: true, multiSelect: false, sig: "lab-1" };
const lab = (m) => page.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: { ...m, __lab: true } })); }, m);
const STATE = () => { const ta = document.getElementById("composer-input"); const strip = document.getElementById("composer-staged");
  const shown = !!strip && strip.style.display !== "none";
  return { value: ta.value, answering: ta.classList.contains("answering"), placeholder: ta.placeholder,
    staged: shown ? strip.querySelectorAll(".staged-chip").length : 0, label: shown ? (strip.querySelector(".staged-lbl")?.textContent || "") : "",
    title: shown ? (strip.querySelector(".staged-lbl")?.title || "") : "",
    toasts: [...document.querySelectorAll("#warn-toasts .warn-toast")].map((t) => t.textContent),
    picker: !!document.querySelector("#live-ask .ask-card"), out: window.__out.slice() }; };
const state = () => page.evaluate(STATE);
const settle = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
const intoBox = async () => { await page.click("#composer-input"); };

await page.goto(cfg.chat);
await page.waitForSelector("#content .turn", { timeout: 20000 });
const out = {};

// 1. question up, a line typed after it, ⌘⏎
await lab({ type: "askLive", id: cfg.sid, ask: ASK });
await page.waitForSelector("#live-ask .ask-card", { timeout: 5000 });
out.up = await state();
await intoBox();
await page.keyboard.type(cfg.staged);
await page.keyboard.press("Control+Enter");
await settle();
out.staged = await state();
if (cfg.shot) {
  fs.mkdirSync(path.dirname(cfg.shot), { recursive: true });
  await page.screenshot({ path: cfg.shot });
}

// 2. an answer typed into the box, ⏎; then a click on an option
await intoBox();
await page.keyboard.type(cfg.answer);
await page.keyboard.press("Enter");
await settle();
out.answered = await state();
await page.click("#live-ask .ask-live-opt >> nth=1");
await settle();
out.clicked = await state();

// 3. the question is answered (the kernel clears it), then a new one arrives
await lab({ type: "askLiveClear", id: cfg.sid });
await settle();
out.cleared = await state();
await lab({ type: "askLive", id: cfg.sid, ask: { ...ASK, sig: "lab-2" } });
await page.waitForSelector("#live-ask .ask-card", { timeout: 5000 });
out.again = await state();

// 4. an empty ⏎ with the question up releases the staged item as an ordinary message
await intoBox();
await page.keyboard.press("Enter");
await settle();
out.released = await state();

// 5. a draft begun BEFORE the question arrived stages too, and the emptied box becomes the answer field
await lab({ type: "askLiveClear", id: cfg.sid });
await settle();
await intoBox();
await page.keyboard.type(cfg.early);
// the two stamps are Date.now() values, and a draft started in the same ms as the question is not "before" it: the
// question goes up once the page's clock has moved past the moment the typing ended
const typedAt = await page.evaluate(() => Date.now());
await page.waitForFunction((t) => Date.now() > t, typedAt);
await lab({ type: "askLive", id: cfg.sid, ask: { ...ASK, sig: "lab-3" } });
await page.waitForSelector("#live-ask .ask-card", { timeout: 5000 });
out.early = await state();
await intoBox();
await page.keyboard.press("Control+Enter");
await settle();
out.earlyStaged = await state();

fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedStageOverPicker(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served staging lab needs them")
        cls.lab = tempfile.mkdtemp(prefix="stage-over-picker-")
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
        Path(cls.state, "session-hosts").write_text("off\n")   # a lab root writes its own hosts off (the conftest rule)
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        claude = os.path.join(cls.lab, "claude")
        # every non-alphanumeric char of the realpath becomes '-' in Claude's project dir name (jd._proj_dir)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a CLOSED turn: an open one would invite the boot reconcile to resume it — no real CLI here
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-555555555555", "parentUuid": None,
                        "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": "Pick a database for the notes-api and wire it up."}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": "22222222-3333-4444-5555-666666666666",
                        "parentUuid": "11111111-2222-3333-4444-555555555555",
                        "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-fable-5-1",
                                    "content": [{"type": "text", "text": "Two good fits for the notes-api; I need your call."}],
                                    "stop_reason": "end_turn"}}) + "\n")
        cls.port = _free_port()
        cls.token = "testtok-stagepicker"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = open(os.path.join(cls.lab, "kernel.log"), "w")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=cls.klog, stderr=subprocess.STDOUT, env=env)
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
        cls.out = cls._drive()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        if getattr(cls, "klog", None):
            cls.klog.close()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        shot = ""
        if os.environ.get("SHOT_DIR"):
            shot = os.path.join(os.environ["SHOT_DIR"], os.environ.get("SHOT_NAME", "stage-over-picker") + ".png")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "sid": SID,
                       "staged": STAGED, "answer": ANSWER, "early": EARLY, "shot": shot}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served staging lab needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        return json.loads(line[len("RESULT:"):])

    def _answering(self, s, why):
        self.assertTrue(s["answering"], "%s: the box wears the answering tint: %r" % (why, s))
        self.assertTrue(s["placeholder"].startswith("add your own answer"), "%s: the answer placeholder: %r" % (why, s))

    def test_1_ctrl_enter_stages_over_the_picker_and_the_box_is_the_answer_field_again(self):
        up, st = self.out["up"], self.out["staged"]
        self.assertTrue(up["picker"], "the lab's question is on the page: %r" % up)
        self._answering(up, "question up, box empty")
        self.assertEqual(st["staged"], 1, "⌘⏎ staged the line: %r" % st)
        self.assertEqual(st["value"], "", "the box emptied into the staged item: %r" % st)
        self._answering(st, "after staging")
        self.assertEqual(st["toasts"], [], "no refusal toast: %r" % st)
        self.assertEqual(st["out"], [], "staging sends nothing: %r" % st)

    def test_2_an_answer_through_the_box_or_a_click_leaves_the_staged_item_held(self):
        a, c = self.out["answered"], self.out["clicked"]
        self.assertEqual(a["out"], [{"type": "addCustomAsk", "text": ANSWER}],
                         "the typed answer went to the question's free-text slot, alone: %r" % a)
        self.assertEqual(a["staged"], 1, "the staged item waits through the answer: %r" % a)
        self.assertEqual(a["value"], "", a)
        self.assertEqual(c["out"][-1], {"type": "answerAsk", "target": 2}, "the click answered with the option: %r" % c)
        self.assertEqual(len(c["out"]), 2, "and sent nothing else: %r" % c)
        self.assertEqual(c["staged"], 1, "the staged item waits through a clicked answer too: %r" % c)

    def test_3_the_label_says_after_you_answer_only_while_a_question_is_up(self):
        st, cl, ag = self.out["staged"], self.out["cleared"], self.out["again"]
        self.assertEqual(st["label"], LABEL_PICKER, st)
        self.assertEqual(self.out["answered"]["label"], LABEL_PICKER, "still up until the kernel clears it")
        self.assertIn("held until you answer it", st["title"], "the tooltip names the hold: %r" % st["title"])
        self.assertFalse(cl["picker"], cl)
        self.assertEqual(cl["label"], LABEL_PLAIN, "answered: the plain words: %r" % cl)
        self.assertFalse(cl["answering"], "no question, no answering tint: %r" % cl)
        self.assertEqual(ag["label"], LABEL_PICKER, "a new question relabels the strip on its arrival: %r" % ag)

    def test_4_an_empty_enter_with_the_question_up_releases_the_staged_item_as_a_message(self):
        r = self.out["released"]
        sends = [m for m in r["out"] if m["type"] == "sendMessage"]
        self.assertEqual(len(sends), 1, r)
        self.assertEqual(sends[0]["text"], STAGED, "the staged line went as an ordinary message: %r" % r)
        self.assertEqual(r["staged"], 0, r)

    def test_5_a_draft_begun_before_the_question_stages_and_the_box_turns_into_the_answer_field(self):
        e, es = self.out["early"], self.out["earlyStaged"]
        self.assertTrue(e["picker"], e)
        self.assertEqual(e["value"], EARLY, e)
        self.assertFalse(e["answering"], "a draft begun before the question is a message, not an answer: %r" % e)
        self.assertEqual(es["staged"], 1, "it stages: %r" % es)
        self.assertEqual(es["value"], "", es)
        self._answering(es, "the emptied box after staging the early draft")
        self.assertEqual(es["toasts"], [], es)


if __name__ == "__main__":
    unittest.main()
