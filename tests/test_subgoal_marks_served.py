#!/usr/bin/env python3
"""The feed card's inline sub-goal checklist, measured on the real /feed page (2026-09-18, the user's two screenshots): the
marks sit at ONE height on every row whether or not the row's triangle span holds a glyph, at 1x and at 2x; the fold row's
mark keeps its glyph and its place when the fold opens; and the rows behind the "N reviewed earlier" fold render one level
under the fold row, never flush with the fresh rows.

The check that moved: the fold row's expanded state was the class "open", which is also the not-done STATUS class, so the
open fold's mark fell under .fcheck.open .fcheck-mark (the hollow 13px ring) and its ✓ glyph sat low inside the ring, a
checkmark that moved down in its box the moment the fold was opened. MEASURED, not read: the triangle's font metrics were
the first suspect, and the same lab at the base showed every sub-goal row's mark at one height with or without a triangle
(1.86 px from the row's top at both scales) while the fold row's mark changed shape (15.4 px tall closed, the 13 px ring
open); the expanded state is its own class now, and the triangle invariant stays pinned here as the layout Chromium computes.

Indent: the fold's kids were walked at depth 0 (the same depth as the fresh rows), so they rendered flush with them instead
of under the fold row that is their visual parent; they walk at depth 1 now.

A served lab, not a source pin: the measurement is the layout Chromium computes. The payload is synthetic (the notes-api
demo world, placeholder ids) and delivered straight to the page the way the feed labs do (no goal store, no judge).
Skips LOUDLY without the extension deps or a Playwright browser, and for nothing else (ROMP_SERVED_TESTS_REQUIRE=1 turns
the skips red where the browser is installed); a build or kernel failure is a failure."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment

SID_WEB = "aaaaaaaa-1111-2222-3333-777777777777"
ROOT_ID = "cccccccc-1111-2222-3333-000000000021"
TREE_INDENT_EM = 1.4   # feed.ts TREE_INDENT_EM: one level of the modal outline's indent


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
const errors = [];
const now = Math.floor(Date.now() / 1000);
// the card's tree: the root, then PAIRS of direct children in each mark state, one with a child (a ▶ triangle) and one
// without; two more reviewed earlier (one with a child), behind the fold
const node = (id, text, status, children, extra) => Object.assign({ id, kind: "ask", text, who: "web", whoSid: cfg.web, whoColor: null,
  status, t: now - 600, last: now - 60, children }, extra || {});
const R = cfg.root;
const tree = [
  node(R, "notes-api: draft the search index", "open", [R + "-a", R + "-b", R + "-e", R + "-g", R + "-f", R + "-h", R + "-c", R + "-d", R + "-x"]),
  node(R + "-a", "index the note titles", "done", [R + "-a1"]),          // done, WITH a triangle
  node(R + "-a1", "lower-case the titles first", "done", []),
  node(R + "-b", "store the token positions", "done", []),               // done, no triangle
  node(R + "-e", "rank by recency", "open", [R + "-e1"]),                 // open, WITH a triangle
  node(R + "-e1", "read the note's mtime", "open", []),
  node(R + "-g", "cap the result page", "open", []),                      // open, no triangle
  node(R + "-f", "pick the stemmer", "question", [R + "-f1"]),            // blocked, WITH a triangle
  node(R + "-f1", "compare two stemmers", "question", []),
  node(R + "-h", "choose the tie-break", "question", []),                 // blocked, no triangle
  node(R + "-c", "write the index schema", "done", [R + "-c1"], { reviewedEarlier: true }),   // reviewed earlier, WITH a triangle
  node(R + "-c1", "list the schema's columns", "done", []),
  node(R + "-d", "seed the demo notes", "done", [], { reviewedEarlier: true }),                // reviewed earlier, no triangle
  // a reviewed HANDOFF child: delegations render in their own section, never as a checklist row, so the fold's label must not
  // count it (2026-09-18: "3 reviewed earlier" opened to two rows)
  node(R + "-x", "\u21aa delegated to api: review the index", "done", [], { kind: "handoff", who: "api", reviewedEarlier: true })];
const payload = { type: "feed", asks: [{ itemId: R, sid: cfg.web, name: "web", color: { bg: "#1EA1EB", fg: "#ffffff" },
    text: "notes-api: draft the search index", t: now - 600, live: true, turnId: "turn-21", trgb: [30, 161, 235], column: "working", tree }],
  sessions: [{ sid: cfg.web, name: "web" }], order: [cfg.web] };
const results = {};
for (const dsf of [1, 2]) {
  const context = await browser.newContext({ deviceScaleFactor: dsf, viewport: { width: 1100, height: 760 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push("dsf" + dsf + ": " + String(e && e.stack || e).slice(0, 400)));
  page.on("console", (m) => { if (m.type() === "error") errors.push("dsf" + dsf + " console: " + m.text().slice(0, 300)); });
  await page.goto(cfg.feed);
  await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
  await page.waitForTimeout(600);
  await page.evaluate((m) => new Promise((res) => { window.dispatchEvent(new MessageEvent("message", { data: m })); requestAnimationFrame(() => res(null)); }), payload);
  await page.waitForSelector(`#feed-cols [data-key="a:${R}"]`, { timeout: 10000 });
  await page.mouse.move(2, 2); await page.waitForTimeout(120);
  // open the Sub-goals section through its own button, as the user does
  const clickSub = () => page.evaluate((R) => {
    const card = document.querySelector(`#feed-cols [data-key="a:${R}"]`);
    const btn = Array.from(card.querySelectorAll(".fask-secbtn")).find((b) => /sub-goal/.test(b.textContent || "") && b.style.display !== "none");
    if (!btn) return "no sub-goals button";
    btn.click(); return btn.textContent;
  }, R);
  const subLabel = await clickSub();
  await page.waitForTimeout(150);
  const rows = () => page.evaluate((R) => {
    const card = document.querySelector(`#feed-cols [data-key="a:${R}"]`);
    const cl = card.querySelector(".fask-checklist");
    const fs = parseFloat(getComputedStyle(cl).fontSize);
    return { fontSize: fs, rows: Array.from(cl.querySelectorAll(".fcheck")).map((row) => {
      const rr = row.getBoundingClientRect();
      const tri = row.querySelector(".fcheck-tri"), mark = row.querySelector(".fcheck-mark"), txt = row.querySelector(".fcheck-text");
      const mr = mark.getBoundingClientRect(), tr = tri.getBoundingClientRect(), xr = txt.getBoundingClientRect();
      const rowFs = parseFloat(getComputedStyle(row).fontSize);
      return { cls: row.className, tri: tri.textContent, text: txt.textContent, rowFontSize: rowFs,
               padLeft: parseFloat(getComputedStyle(row).paddingLeft), rowH: rr.height,
               markTop: mr.top - rr.top, markH: mr.height, markMid: (mr.top + mr.height / 2) - rr.top,
               triTop: tr.top - rr.top, triH: tr.height, textTop: xr.top - rr.top, textH: xr.height };
    }) };
  }, R);
  const fresh = await rows();
  // open the reviewed-earlier fold by its row, as the user does
  const folded = await page.evaluate((R) => {
    const card = document.querySelector(`#feed-cols [data-key="a:${R}"]`);
    const f = card.querySelector(".fcheck.freviewed"); if (!f) return "no fold row"; f.click(); return f.textContent;
  }, R);
  await page.waitForTimeout(150);
  const opened = await rows();
  if (cfg.shots) await page.screenshot({ path: `${cfg.shots}/subgoal-marks-${dsf}x.png` });
  results["dsf" + dsf] = { subLabel, folded, fresh, opened };
  await context.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify({ results, errors }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedSubgoalMarks(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="subgoalmarks-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "session-hosts"), "w") as fh:   # a lab root of its own pins the hosts OFF (CLAUDE.md 2026-09-11)
            fh.write("off\n")
        cls.port = _free_port()
        cls.token = "testtok-subgoalmarks"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(os.path.join(cls.lab, "kernel.log"), "w"), stderr=subprocess.STDOUT, env=env)
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
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (cls.port, cls.token), "web": SID_WEB, "root": ROOT_ID,
                       "shots": os.environ.get("SUBGOAL_MARKS_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served guard needs one (CI installs none)")
        assert p.returncode == 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        assert line, "driver printed no result:\n" + p.stdout[-3000:]
        cls.r = json.loads(line[len("RESULT:"):])
        if os.environ.get("SUBGOAL_MARKS_SHOTS"):   # the raw measurements beside the screenshots, for a read by hand
            with open(os.path.join(os.environ["SUBGOAL_MARKS_SHOTS"], "result.json"), "w") as f:
                json.dump(cls.r, f, indent=1)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @staticmethod
    def _row(rows, text):
        return next(r for r in rows if r["text"] == text)

    def test_the_page_threw_nothing_and_the_section_opened(self):
        self.assertEqual(self.r["errors"], [], self.r["errors"])
        for dsf in ("dsf1", "dsf2"):
            d = self.r["results"][dsf]
            self.assertIn("sub-goals", d["subLabel"], "%s: the button reads the direct count: %r" % (dsf, d["subLabel"]))
            self.assertEqual(d["subLabel"].strip(), "8 sub-goals", "%s: eight direct children, none a handoff" % dsf)
            self.assertIn("2 reviewed earlier", d["folded"], "%s: the fold row counts the rows it opens to, not the reviewed handoff the walk never renders: %r" % (dsf, d["folded"]))
            self.assertEqual([r["text"] for r in d["fresh"]["rows"]],
                             ["index the note titles", "store the token positions", "rank by recency", "cap the result page",
                              "pick the stemmer", "choose the tie-break", "2 reviewed earlier"],
                             "%s: the fresh rows in tree order, then the fold row, nothing behind it yet" % dsf)
            self.assertEqual([r["text"] for r in d["opened"]["rows"]][-2:], ["write the index schema", "seed the demo notes"],
                             "%s: the fold opens in place, its two kids after it" % dsf)
            self.assertEqual(len(d["opened"]["rows"]) - len(d["fresh"]["rows"]), 2, "%s: the open fold adds exactly the rows its label counted" % dsf)
            self.assertFalse(any("delegated to" in r["text"] for r in d["opened"]["rows"]), "%s: the handoff is no checklist row" % dsf)

    def test_the_mark_sits_at_one_height_with_and_without_a_triangle_at_1x_and_2x(self):
        # per mark state, the row WITH a triangle glyph against its twin WITHOUT: the mark's top and its centre within the row
        # agree to within half a device pixel, and the rows are the same height (a triangle's font metrics reach neither)
        pairs = [("done", "index the note titles", "store the token positions"),
                 ("open", "rank by recency", "cap the result page"),
                 ("question", "pick the stemmer", "choose the tie-break")]
        for dsf, scale in (("dsf1", 1.0), ("dsf2", 2.0)):
            rows = self.r["results"][dsf]["fresh"]["rows"]
            tol = 0.5 / scale   # half a device pixel, in CSS pixels
            for state, with_tri, without in pairs:
                a, b = self._row(rows, with_tri), self._row(rows, without)
                self.assertNotEqual(a["tri"], "", "%s: the %s row with a child shows its triangle" % (dsf, state))
                self.assertEqual(b["tri"], "", "%s: the %s row without a child shows none" % (dsf, state))
                self.assertIn(state, a["cls"]); self.assertIn(state, b["cls"])
                self.assertAlmostEqual(a["markTop"], b["markTop"], delta=tol,
                                       msg="%s %s: the mark's top moved with the triangle: %.3f (▶) against %.3f (none), rows %.3f and %.3f tall"
                                           % (dsf, state, a["markTop"], b["markTop"], a["rowH"], b["rowH"]))
                self.assertAlmostEqual(a["markMid"], b["markMid"], delta=tol, msg="%s %s: the mark's centre moved with the triangle" % (dsf, state))
                self.assertAlmostEqual(a["rowH"], b["rowH"], delta=tol, msg="%s %s: the triangle changed the row's height" % (dsf, state))
            # …and across states the DISC marks (13px done, 13px question ring, 13px open ring) share one centre line
            mids = [self._row(rows, t)["markMid"] for _, t, _ in pairs] + [self._row(rows, t)["markMid"] for _, _, t in pairs]
            self.assertLess(max(mids) - min(mids), 1.0 + tol, "%s: the three mark states centre on one line: %r" % (dsf, mids))

    def test_the_marks_centre_on_the_texts_first_line(self):
        # the mark is centred on the text's FIRST line box (not the row, which grows with a wrapped text): its centre sits at
        # half the line box (line-height 1.4 of the row's font) from the row's top, on every row, at both scales
        for dsf, scale in (("dsf1", 1.0), ("dsf2", 2.0)):
            for r in self.r["results"][dsf]["opened"]["rows"]:
                line = 1.4 * r["rowFontSize"]
                self.assertAlmostEqual(r["markMid"], line / 2, delta=1.0 / scale + 0.5,
                                       msg="%s: %r: the mark's centre %.2f is not the first line's %.2f (row font %.2f)" % (dsf, r["text"], r["markMid"], line / 2, r["rowFontSize"]))

    def test_the_fold_rows_mark_keeps_its_glyph_and_its_place_when_the_fold_opens(self):
        # the user's "checkmark moves down in its box": the open fold row wore the not-done STATUS class "open" (its expanded
        # state shared the name), so .fcheck.open .fcheck-mark drew the hollow 13px ring around its ✓ glyph, which sat low
        # inside it. Closed against open: the same glyph box, the same centre, and no status class on the fold row
        for dsf, scale in (("dsf1", 1.0), ("dsf2", 2.0)):
            d = self.r["results"][dsf]
            closed = self._row(d["fresh"]["rows"], "2 reviewed earlier")
            opened = self._row(d["opened"]["rows"], "2 reviewed earlier")
            self.assertEqual((closed["tri"], opened["tri"]), ("▶", "▼"), "%s: the fold's triangle follows its state" % dsf)
            for r in (closed, opened):
                self.assertFalse({"done", "open", "question", "cleared"} & set(r["cls"].split()),
                                 "%s: the fold row wears a status class: %r" % (dsf, r["cls"]))
            self.assertAlmostEqual(closed["markH"], opened["markH"], delta=0.5 / scale,
                                   msg="%s: the fold's mark changed shape on open: %.2f tall closed, %.2f open (the ring)" % (dsf, closed["markH"], opened["markH"]))
            self.assertAlmostEqual(closed["markMid"], opened["markMid"], delta=0.5 / scale,
                                   msg="%s: the fold's mark moved on open: centre %.2f closed, %.2f open" % (dsf, closed["markMid"], opened["markMid"]))
            self.assertAlmostEqual(closed["rowH"], opened["rowH"], delta=0.5 / scale, msg="%s: the fold row's height changed on open" % dsf)

    def test_the_folds_kids_render_one_level_under_the_fold_row(self):
        for dsf in ("dsf1", "dsf2"):
            d = self.r["results"][dsf]
            fresh = [r for r in d["opened"]["rows"] if r["text"] in ("index the note titles", "store the token positions")]
            fold = self._row(d["opened"]["rows"], "2 reviewed earlier")
            kids = [self._row(d["opened"]["rows"], t) for t in ("write the index schema", "seed the demo notes")]
            for r in fresh + [fold]:
                self.assertEqual(r["padLeft"], 0, "%s: a fresh row and the fold row sit at depth 0: %r" % (dsf, r["text"]))
            for k in kids:
                self.assertAlmostEqual(k["padLeft"], TREE_INDENT_EM * k["rowFontSize"], delta=0.6,
                                       msg="%s: the fold's kid %r is not one level under the fold row (padding %.2f, one level %.2f)"
                                           % (dsf, k["text"], k["padLeft"], TREE_INDENT_EM * k["rowFontSize"]))
            self.assertNotEqual(kids[0]["tri"], "", "%s: the reviewed kid with a child keeps its triangle" % dsf)
            self.assertEqual(kids[1]["tri"], "", "%s: the reviewed kid without a child has none" % dsf)
            self.assertEqual(fold["tri"], "▼", "%s: the open fold shows ▼" % dsf)


if __name__ == "__main__":
    unittest.main()
