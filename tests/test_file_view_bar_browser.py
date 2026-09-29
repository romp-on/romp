#!/usr/bin/env python3
"""The file viewer's title bar, tidied (T367; the user 2026-09-12, whose bar for a markdown file read Rendered, Raw,
A minus, a readout, A plus, Edit, "not in a git repository", a greyed GitHub link, Download, Copy path and a close
cross on its own row). Now: the Rendered|Raw pair is ONE segmented control; the text size is one zoom glyph opening a
flyout that holds A minus, the readout and A plus; edit, download and copy path are glyphs whose words ride their
title and aria-label; the GitHub link shows only when it resolves (a file outside a repository shows neither the link
nor the explanation); the controls sit in a view group and a file group, the close cross alone at the end; copy path
acknowledges with a glyph swap.

This lab drives the real /files page of a hermetic kernel (synthetic fixtures only: the notes-api demo world, host
TESTHOST, placeholder sids) and opens four files through the pane's own relay (the romp viewFile message the shell
sends): a markdown file committed in a repo whose origin is served from a LOCAL bare repo through a stand-in ssh (the
GitHub-link tests' idiom, no network), a markdown file OUTSIDE any repository, a small PNG and a Python file. For each
it reads the bar: every control's tag, words (title, aria-label, text), group, hidden state and rect. Asserted: the
GitHub unit is hidden with no control for the file outside a repo and is one anchor for the repo file; the download
control is the lightbox tray glyph (the same path data) with its words; the segment pair's two buttons are adjacent
siblings in one group whose rects touch; the zoom glyph opens a flyout holding the three size buttons and Escape
closes it; copy path acknowledges in the same tick and settles to Copied or Copy failed. FILE_BAR_DIST=<dir> serves
another tree's UI bundle (the red run's before); FILE_BAR_SHOTS=<prefix> writes <prefix>-<file>-<theme>.png of the
viewer's top for the four files in both themes; FILE_BAR_DUMP=<path> writes the whole measurement. Skips LOUDLY without the extension deps or a Playwright browser, and
never otherwise (CI turns a skip in a served module into a failure)."""
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's
from git_fixture import git, init_repo   # noqa: E402

SID = "aaaaaaaa-1111-2222-3333-444444444444"
IDENT = ("t@testhost", "t")
GUIDE = "# Notes API guide\n\nThe web session keeps the notes-api tidy.\n\n## Fold rules\n\nOne rule per line.\n"
APP_PY = "def notes():\n    return [\"a\", \"b\"]\n"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _png(w=24, h=16, rgb=(84, 178, 4)):
    """A tiny opaque PNG, assembled at run time (no binary fixture in the repo)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const ctx = await browser.newContext({ viewport: { width: 1100, height: 520 } });
await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: cfg.origin });
const page = await ctx.newPage();
await page.goto(cfg.files);
await page.waitForFunction(() => document.readyState === "complete", null, { timeout: 30000 });
await page.waitForTimeout(400);
const bar = (label) => page.evaluate((label) => {
  const r1 = (v) => Math.round(v * 10) / 10;
  const rect = (e) => { const b = e.getBoundingClientRect(); return { top: r1(b.top), bottom: r1(b.bottom), left: r1(b.left), right: r1(b.right), w: r1(b.width), h: r1(b.height) }; };
  const box = document.querySelector("#romp-fileview .fileview"); const barEl = box && box.querySelector(".fileview-bar");
  if (!barEl) return { label, missing: true };
  const acts = barEl.querySelector(".fileview-acts");
  const groupOf = (e) => { const g = e.closest(".fileview-group"); return g ? (g.classList.contains("fileview-group-view") ? "view" : "file") : (e.closest(".fileview-zoom-menu") ? "zoom-menu" : "row"); };
  const controls = Array.from(acts.querySelectorAll("button, a")).map((e) => ({
    tag: e.tagName.toLowerCase(), text: (e.textContent || "").trim(), title: e.title || "", aria: e.getAttribute("aria-label") || "",
    cls: e.className, hidden: e.hidden || getComputedStyle(e).display === "none", hiddenAttr: e.hidden, display: getComputedStyle(e).display, group: groupOf(e), svg: !!e.querySelector("svg"),
    svgPaths: Array.from(e.querySelectorAll("svg *")).map((n) => n.outerHTML).join(""), rect: rect(e), href: e.href || "" }));
  const gh = acts.querySelector(".fileview-gh");
  const seg = acts.querySelector(".fileview-seg");
  const segBtns = seg ? Array.from(seg.children).map((e) => ({ text: e.textContent.trim(), rect: rect(e), radius: getComputedStyle(e).borderRadius, on: e.classList.contains("on") })) : [];
  return { label, controls, bar: rect(barEl), box: rect(box),
    gh: gh ? { hidden: gh.hidden, busy: gh.getAttribute("aria-busy"), children: gh.children.length, text: gh.textContent.trim(), display: getComputedStyle(gh).display, w: gh.getBoundingClientRect().width } : null,
    seg: seg ? { group: groupOf(seg), sameParent: seg.children.length === 2 && seg.children[0].nextElementSibling === seg.children[1], btns: segBtns } : null,
    groups: Array.from(acts.children).map((c) => c.className + (c.hidden ? "[hidden]" : "")),
    ghWhy: !!acts.querySelector(".fileview-gh-why") };
}, label);
const open = async (path) => {
  await page.evaluate(([path, sid]) => { document.getElementById("romp-fileview")?.remove(); window.postMessage({ romp: "viewFile", path, sid }, "*"); }, [path, cfg.sid]);
  await page.waitForSelector("#romp-fileview .fileview-bar", { timeout: 15000 });
  await page.waitForFunction(() => { const g = document.querySelector("#romp-fileview .fileview-gh"); return !g || g.getAttribute("aria-busy") !== "true"; }, null, { timeout: 15000 });   // the kernel's link verdict landed
  await page.waitForFunction(() => !document.querySelector("#romp-fileview .fileview-load"), null, { timeout: 15000 });
  await page.waitForTimeout(250);
};
const out = { files: {} };
for (const f of cfg.files_list) {
  await open(f.path);
  const m = await bar(f.label);
  // the zoom flyout: the glyph opens it, the three buttons are inside, Escape closes it
  m.zoom = await page.evaluate(() => {
    const t = document.querySelector("#romp-fileview .fileview-zoom-btn"); const menu = document.querySelector("#romp-fileview .fileview-zoom-menu");
    if (!t || !menu) return { present: false };
    const before = { hidden: menu.hidden, expanded: t.getAttribute("aria-expanded") };
    if (t.hidden) return { present: true, triggerHidden: true, before };
    t.click();
    const r = (e) => { const b = e.getBoundingClientRect(); return { top: b.top, left: b.left, right: b.right, bottom: b.bottom }; };
    const open = { hidden: menu.hidden, expanded: t.getAttribute("aria-expanded"), labels: Array.from(menu.querySelectorAll("button")).map((b) => b.getAttribute("aria-label") || b.textContent), rect: r(menu), trigger: r(t),
                   display: getComputedStyle(menu).display, bg: getComputedStyle(menu).backgroundColor, readout: menu.querySelector(".fileview-size-reset").textContent };
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const after = { hidden: menu.hidden, expanded: t.getAttribute("aria-expanded"), viewerUp: !!document.getElementById("romp-fileview") };
    return { present: true, triggerHidden: false, before, open, after };
  });
  // copy path: the press acknowledges in the same tick and settles
  m.copy = await page.evaluate(async () => {
    const c = Array.from(document.querySelectorAll("#romp-fileview .fileview-acts button")).find((b) => b.getAttribute("aria-label") === "Copy path");
    if (!c) return { present: false };
    const glyph0 = c.innerHTML;
    c.click();
    const sameTick = { busy: c.classList.contains("fileview-busy") || c.classList.contains("ok") || c.classList.contains("err") };
    await new Promise((r) => setTimeout(r, 150));
    const settled = { title: c.title, aria: c.getAttribute("aria-label"), ok: c.classList.contains("ok"), err: c.classList.contains("err"), glyphChanged: c.innerHTML !== glyph0 };
    let text = null; try { text = await navigator.clipboard.readText(); } catch (e) { text = "unreadable: " + e; }
    await new Promise((r) => setTimeout(r, 1300));
    const restored = { title: c.title, glyphBack: c.innerHTML === glyph0 };
    return { present: true, sameTick, settled, clipboard: text, restored };
  });
  out.files[f.label] = m;
  if (cfg.shots) {
    const clip = await page.evaluate(() => { const b = document.querySelector("#romp-fileview .fileview").getBoundingClientRect(); return { x: Math.max(0, b.left - 4), y: Math.max(0, b.top - 4), width: Math.min(window.innerWidth, b.width + 8), height: 150 }; });
    await page.screenshot({ path: cfg.shots + "-" + f.label + "-dark.png", clip });
    await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(120);
    await page.screenshot({ path: cfg.shots + "-" + f.label + "-light.png", clip });
    await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(60);
  }
}
// the keyboard road (review): Enter on the glyph opens the flyout, Tab moves inside, Escape closes it and returns the focus
// to the glyph with the viewer still up; then a viewer closed from the keyboard with its flyout OPEN (the cross focused, Enter)
// must leave no reference behind: Escape on the next file closes that viewer at once
await open(cfg.files_list[3].path);
await page.focus("#romp-fileview .fileview-zoom-btn"); await page.keyboard.press("Enter");
const k1 = await page.evaluate(() => ({ open: !document.querySelector("#romp-fileview .fileview-zoom-menu").hidden, expanded: document.querySelector("#romp-fileview .fileview-zoom-btn").getAttribute("aria-expanded") }));
await page.keyboard.press("Tab");
const k2 = await page.evaluate(() => ({ inside: !!(document.activeElement && document.activeElement.closest(".fileview-zoom-menu")), label: document.activeElement ? document.activeElement.getAttribute("aria-label") : null }));
await page.keyboard.press("Escape");
const k3 = await page.evaluate(() => ({ closed: document.querySelector("#romp-fileview .fileview-zoom-menu").hidden, focusOnGlyph: document.activeElement === document.querySelector("#romp-fileview .fileview-zoom-btn"), viewerUp: !!document.getElementById("romp-fileview") }));
await page.focus("#romp-fileview .fileview-zoom-btn"); await page.keyboard.press("Enter");
await page.focus("#romp-fileview .fileview-close"); await page.keyboard.press("Enter");
await page.waitForTimeout(100);
const k4 = await page.evaluate(() => ({ viewerGone: !document.getElementById("romp-fileview") }));
await open(cfg.files_list[0].path);
await page.keyboard.press("Escape");
await page.waitForTimeout(150);
const k5 = await page.evaluate(() => ({ viewerGone: !document.getElementById("romp-fileview") }));
out.keys = { k1, k2, k3, k4, k5 };
fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
console.log("RESULT: ok");
"""


class ServedFileViewBar(unittest.TestCase):
    maxDiff = None
    result = None

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
        cls.lab = tempfile.mkdtemp(prefix="file-bar-")
        before = os.environ.get("FILE_BAR_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own session-hosts off (the conftest rule)
        os.makedirs(cwd, exist_ok=True)
        # the repo: a GitHub-shaped origin served from a LOCAL bare repo through a stand-in ssh (test_file_github's idiom)
        genv = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        origin_root = os.path.join(cls.lab, "origin")
        bare = os.path.join(origin_root, "TESTORG", "notes-api.git")
        os.makedirs(bare)
        init_repo(bare, "-q", "--bare", ident=IDENT, env=genv)
        stand_in = os.path.join(origin_root, "stand-in-ssh")
        Path(stand_in).write_text('#!/bin/sh\nfor a in "$@"; do cmd=$a; done\ncd "%s" && eval "$cmd"\n' % origin_root)
        os.chmod(stand_in, 0o755)
        genv["GIT_SSH_COMMAND"] = stand_in
        init_repo(cwd, "-q", "-b", "main", ident=IDENT, env=genv)
        os.makedirs(os.path.join(cwd, "docs"), exist_ok=True)
        os.makedirs(os.path.join(cwd, "src"), exist_ok=True)
        Path(cwd, "docs", "guide.md").write_text(GUIDE)
        Path(cwd, "src", "app.py").write_text(APP_PY)
        Path(cwd, "docs", "figure.png").write_bytes(_png())
        git(cwd, "add", ".", ident=IDENT, env=genv)
        git(cwd, "commit", "-q", "-m", "the notes-api guide, app and figure", ident=IDENT, env=genv)
        git(cwd, "remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", ident=IDENT, env=genv)
        git(cwd, "push", "-q", "origin", "main", ident=IDENT, env=genv)
        outside = os.path.join(cls.lab, "outside")
        os.makedirs(outside)
        Path(outside, "notes.md").write_text("# Outside\n\nnot under any repository\n")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        Path(state, "names", SID).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        recs = [{"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": SID,
                 "message": {"role": "user", "content": "what does the web session do in notes-api?"}},
                {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": SID,
                 "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "It keeps the web side of the notes-api tidy."}]}}]
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.files = [{"label": "md-repo", "path": os.path.join(cwd, "docs", "guide.md")},
                     {"label": "md-outside", "path": os.path.join(outside, "notes.md")},
                     {"label": "image", "path": os.path.join(cwd, "docs", "figure.png")},
                     {"label": "code", "path": os.path.join(cwd, "src", "app.py")}]
        cls.port, cls.token = _free_port(), "testtok-filebar"
        # the kernel's git must reach the stand-in origin too: its ls-remote check answers from the local bare repo
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              GIT_SSH_COMMAND=stand_in, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
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
            k.kill(); k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _run(cls):
        if cls.result is not None:
            if isinstance(cls.result, BaseException):
                raise cls.result
            return cls.result
        try:
            cls.result = cls._drive()
        except BaseException as e:
            cls.result = e
            raise
        return cls.result

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        origin = "http://127.0.0.1:%d" % cls.port
        with open(cfg, "w") as f:
            json.dump({"files": "%s/files?token=%s" % (origin, cls.token), "origin": origin, "sid": SID, "files_list": cls.files,
                       "out": out, "shots": os.environ.get("FILE_BAR_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        result = json.loads(Path(out).read_text())
        if os.environ.get("FILE_BAR_DUMP"):   # a path: the whole measurement, for reading the cases side by side
            Path(os.environ["FILE_BAR_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def _file(self, label):
        r = self._run()
        m = r["files"].get(label)
        self.assertIsNotNone(m, "no such file among %r" % list(r["files"]))
        self.assertFalse(m.get("missing"), "the viewer opened the file: " + json.dumps(m)[:300])
        return m

    @staticmethod
    def _ctl(m, aria):
        return next((c for c in m["controls"] if c["aria"] == aria or c["text"] == aria), None)

    # ── the GitHub unit: only when it resolves ──
    def test_a_file_outside_a_repository_shows_neither_the_link_nor_an_explanation(self):
        m = self._file("md-outside")
        table = "\n  " + json.dumps(m["controls"], indent=None)[:1500]
        self.assertFalse(m["ghWhy"], "no caption text in the bar" + table)
        gh = m["gh"]
        self.assertTrue(gh is None or (gh["hidden"] and gh["children"] == 0), "the unit is hidden and empty: " + json.dumps(gh) + table)
        if gh is not None:   # and out of the row's flow: a live inline-flex item would eat a gap between the pencil and Download (review)
            self.assertEqual((gh["display"], gh["w"]), ("none", 0), "the hidden unit still takes room: " + json.dumps(gh))
        self.assertFalse(any(c["text"].startswith("GitHub") and not c["hidden"] for c in m["controls"]), "no GitHub control" + table)
        self.assertFalse(any("not in a git repository" in c["text"] for c in m["controls"]) or "not in a git repository" in json.dumps(m["groups"]), table)

    def test_a_committed_file_in_a_repo_with_a_github_origin_shows_one_link(self):
        m = self._file("md-repo")
        links = [c for c in m["controls"] if c["tag"] == "a" and c["text"] == "GitHub ↗" and not c["hidden"]]
        self.assertEqual(len(links), 1, json.dumps(m["controls"])[:1500])
        self.assertIn("github.com/TESTORG/notes-api/blob/main/docs/guide.md", links[0]["href"])
        self.assertEqual(links[0]["group"], "file", "the link sits in the file group")
        self.assertFalse(m["ghWhy"], "no caption: the note, when any, rides the tooltip")

    # ── the glyphs ──
    def test_download_is_the_lightbox_tray_glyph_with_its_words(self):
        m = self._file("code")
        dl = self._ctl(m, "Download")
        self.assertIsNotNone(dl, json.dumps(m["controls"])[:1500])
        self.assertTrue(dl["svg"], "a glyph, not a word: " + json.dumps(dl))
        self.assertEqual(dl["text"], "", "no word in the button")
        self.assertIn('d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"', dl["svgPaths"], "the tray's own path data (icons.ts, the lightbox's glyph)")
        self.assertEqual((dl["title"], dl["aria"]), ("Download", "Download"))
        self.assertEqual(dl["group"], "file")
        for aria in ("Edit", "Copy path"):
            c = self._ctl(m, aria)
            self.assertIsNotNone(c, aria)
            self.assertTrue(c["svg"] and c["text"] == "" and c["aria"] == aria, aria + ": " + json.dumps(c))
            self.assertEqual(c["group"], "file", aria)

    # ── the groups and the segment pair ──
    def test_the_rendered_raw_pair_is_one_adjacent_segmented_control_in_the_view_group(self):
        m = self._file("md-repo")
        seg = m["seg"]
        self.assertIsNotNone(seg, json.dumps(m)[:800])
        self.assertTrue(seg["sameParent"], "two buttons, adjacent siblings in one wrapper: " + json.dumps(seg))
        self.assertEqual([b["text"] for b in seg["btns"]], ["Rendered", "Raw"])
        a, b = seg["btns"][0]["rect"], seg["btns"][1]["rect"]
        self.assertLessEqual(abs(b["left"] - a["right"]), 1.5, "the pair touches (one shared hairline): " + json.dumps(seg))
        self.assertEqual(seg["group"], "view")
        self.assertTrue(seg["btns"][0]["radius"].startswith("6px 0px 0px 6px") or seg["btns"][0]["radius"] == "6px 0px 0px 6px", seg["btns"][0]["radius"])
        # the row's order: the view group, the file group, the note pair, the close cross
        kinds = [g.split("[")[0] for g in m["groups"]]
        self.assertEqual(kinds, ["fileview-group fileview-group-view", "fileview-group fileview-group-file",
                                 "fileview-cmt-count", "fileview-btn fileview-send",
                                 "fileview-btn fileview-close"], json.dumps(m["groups"]))
        # and the pair is QUIET until notes exist: a file opened with none staged shows neither
        for kind in ("fileview-cmt-count", "fileview-btn fileview-send"):
            self.assertIn(kind + "[hidden]", m["groups"], json.dumps(m["groups"]))

    def test_an_image_has_no_view_controls_and_the_view_group_takes_no_room(self):
        m = self._file("image")
        self.assertIn("fileview-group fileview-group-view[hidden]", m["groups"], json.dumps(m["groups"]))
        self.assertIsNone(m["seg"])
        self.assertTrue(m["zoom"]["present"] and m["zoom"]["triggerHidden"], "no text: no zoom glyph: " + json.dumps(m["zoom"]))
        # a hidden glyph must not PAINT: an author display on the button beat the browser's [hidden] rule once (the lab's own
        # image screenshot showed the pencil and the magnifier), so the computed display is what is pinned
        for c in m["controls"]:
            if c["hiddenAttr"]:
                self.assertEqual(c["display"], "none", "hidden but painted: " + json.dumps(c))
                self.assertEqual((c["rect"]["w"], c["rect"]["h"]), (0, 0), "hidden but taking room: " + json.dumps(c))
        for aria in ("Edit", "Text size"):
            c = self._ctl(m, aria)
            self.assertTrue(c and c["hiddenAttr"] and c["display"] == "none", aria + " hidden for a picture: " + json.dumps(c))

    # ── the zoom flyout ──
    def test_the_zoom_glyph_opens_a_flyout_with_the_three_size_buttons_and_escape_closes_it(self):
        m = self._file("code")
        z = m["zoom"]
        self.assertTrue(z["present"] and not z["triggerHidden"], json.dumps(z))
        self.assertEqual((z["before"]["hidden"], z["before"]["expanded"]), (True, "false"))
        self.assertEqual((z["open"]["hidden"], z["open"]["expanded"], z["open"]["display"]), (False, "true", "flex"), json.dumps(z["open"]))
        self.assertEqual(z["open"]["labels"], ["Smaller text", "Text size 100%, reset to 100%", "Larger text"], json.dumps(z["open"]))
        self.assertEqual(z["open"]["readout"], "100%", "the readout reads the size, dimmed at the default")
        self.assertGreaterEqual(z["open"]["rect"]["top"], z["open"]["trigger"]["bottom"], "the flyout hangs under the glyph")
        self.assertNotEqual(z["open"]["bg"], "rgba(0, 0, 0, 0)", "the menu surface (the menu tokens)")
        self.assertEqual((z["after"]["hidden"], z["after"]["expanded"], z["after"]["viewerUp"]), (True, "false", True), "Escape closes the flyout and leaves the viewer up: " + json.dumps(z["after"]))

    # ── the keyboard road ──
    def test_keyboard_enter_opens_the_flyout_escape_closes_it_with_the_focus_back_on_the_glyph(self):
        k = self._run()["keys"]
        self.assertEqual((k["k1"]["open"], k["k1"]["expanded"]), (True, "true"), json.dumps(k))
        self.assertTrue(k["k2"]["inside"], "Tab moves into the flyout: " + json.dumps(k["k2"]))
        self.assertEqual(k["k2"]["label"], "Smaller text")
        self.assertEqual((k["k3"]["closed"], k["k3"]["viewerUp"]), (True, True), json.dumps(k["k3"]))
        self.assertTrue(k["k3"]["focusOnGlyph"], "Escape returns the focus to the glyph, never leaves it on a hidden button (review): " + json.dumps(k["k3"]))

    def test_a_viewer_closed_from_the_keyboard_with_its_flyout_open_leaves_no_stale_flyout_to_swallow_the_next_escape(self):
        k = self._run()["keys"]
        self.assertTrue(k["k4"]["viewerGone"], "Enter on the close cross closes the viewer: " + json.dumps(k["k4"]))
        self.assertTrue(k["k5"]["viewerGone"], "Escape on the NEXT file closes that viewer at once; a stale flyout reference swallowed it before (review): " + json.dumps(k["k5"]))

    # ── copy path acknowledges ──
    def test_copy_path_acknowledges_in_the_same_tick_and_settles_to_copied_or_copy_failed(self):
        m = self._file("md-repo")
        c = m["copy"]
        self.assertTrue(c["present"], json.dumps(c))
        self.assertTrue(c["sameTick"]["busy"], "the press is acknowledged before the clipboard answers: " + json.dumps(c))
        self.assertIn(c["settled"]["title"], ("Copied", "Copy failed"), json.dumps(c))
        self.assertEqual(c["settled"]["aria"], c["settled"]["title"])
        self.assertTrue(c["settled"]["glyphChanged"], "a glyph swap, not a word")
        if c["settled"]["title"] == "Copied":
            self.assertTrue(c["settled"]["ok"])
            self.assertEqual(c["clipboard"], self.files[0]["path"], "the path itself reached the clipboard")
            self.assertEqual((c["restored"]["title"], c["restored"]["glyphBack"]), ("Copy path", True), "and the control restores itself")


if __name__ == "__main__":
    unittest.main()
