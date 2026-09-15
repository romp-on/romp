#!/usr/bin/env python3
"""The chat rows, the SERVED leg (the user 2026-09-15: a second row of chat columns). The dashboard's chat columns
live inside a static #chat-area wrapper holding two flex rows (#chat-row-1 around the first pane, #chat-row-2 for the
bottom row) and the #gv-rows gutter between them; the split script (_LANDING_SPLIT_JS) appends a later column into
the row its store entry names, shows the bottom row while a column is in it, and keeps the rows' share as rowSplit.

The node-side test (tests/test_chat_split.py RowsExecute) drives the split script against a DOM stub; this one drives
the REAL page: a hermetic kernel serves the dashboard with FIVE synthetic sessions, a headless browser opens it once at
1440 x 900, and ONE driver run walks the story in order with real pointer drags, each step landing in its own assertion:
  1. B's tab dragged from the first column to the chat area's BOTTOM edge: the bottom zone mounts on the page's tabDrag
     message, the rectangle shows the area's bottom half with B's name, and the drop opens the bottom row on B —
     #chat-row-2 visible with one column, the two rows' boxes stacked without overlap at about half the height each,
     the store carrying row:2 and rowSplit 0.5 — while the first pane's document is the SAME document (a nonce stamped
     on it before the drag is still there: the wrapper was in the markup from the first paint, nothing re-parented #f-chat);
  2. C's tab dragged to the BOTTOM ROW's right edge: its rectangle the right half of the bottom row's column at that
     row's height, and the drop a second column in the bottom row, one over two;
  3. D's tab dragged to the TOP row's right edge: two over two — the top row's panes share one top and height, the bottom
     row's another, no pane overlaps another — and the walk (__rompChatFrameIds) is the top row then the bottom;
  4. the CAP: E's drag mounts every making zone refused, the rectangle over the bottom zone says so, and the drop opens
     nothing and moves nothing;
  5. the ROW GUTTER dragged 120 px down: rowSplit changes and the rows' heights follow it; a reload restores the
     arrangement at that share;
  6. the FOLD: the bottom row's two sessions moved home one by one — the row stands on the second, folds on the last:
     #chat-row-2 and #gv-rows hidden, the top row at the area's full height, the store the bytes a never-stacked browser
     writes ({v:2, cols:[{n:4, ids:[D]}]}), the first pane's document still the one stamped after the reload;
  6b. the BUSY guard across rows (review find 2026-09-15): D alone in the top row's second column, its page reporting a
     create in flight (the page's __rompColumnBusy answer, stubbed on its window), dragged to the bottom zone: refused with
     the existing notice, no row opens, the store's bytes are unchanged and D is still in its column;
  6c. the SHARE is forgotten with the row (review find 2026-09-15): after step 5's resize and step 6's fold, B dragged to
     the bottom edge again shows the area's bottom half and OPENS the row at that half — the rectangle's box and the row's
     agree — where the folded row's share had reopened it under a half-height rectangle; folded again;
  7. back to one: the last column's close leaves {v:2, cols:[]} and the first column listing every session;
  8. the UPGRADE of a pre-rows pane store (review find 2026-09-15): legacy one-, two-, three- and four-column stores with
     persisted weights (chat, chatN, feed — no chat1) reloaded into the rows shell render every chat column and the feed at
     the width the pre-rows shell gave them (one row, a gutter between each pair), within a pixel; the store then carries
     chat1 and the area's weight, and a second reload renders the same; and with the CHAT PANE OFF (the rail's Chat, before
     the reload) a legacy store with a missing column weight resolves it over the visible feed, keeps its pre-rows shape while
     the pane is hidden, and on the rail's Chat lays out as the old shell did — the first pane fair-grown over the feed at the
     show, equal columns — a reload after keeping it; and what changes while the upgrade WAITS reaches it (the fourth pass): the
     outline revealed from the rail before the chat pane shows lays out as four equal panes, the restored column closed from
     the palette before the chat pane shows leaves two halves and no weight for it in the store; and a PEER's upgrade (the fifth
     pass: a second page of the same browser, sharing the store) is ingested at its storage event, so this page's first action
     after it — the rail's Outline — fair-grows as a fresh current page does, and the palette's close leaves no chat2; and a
     gutter DRAG is one transaction (the sixth pass): a peer's upgrade landing mid-drag moves nothing under the hand, the
     release lands the divider where the line was and carries the peer's weights, and a drag abandoned to the window's blur
     writes nothing; and a peer CLOSING A COLUMN under a live drag (the seventh pass) ends the drag first — a pending page
     takes the peer's upgraded store in and its release writes nothing, and a current page dragging the gutter beside the
     closed column leaves the files pane untouched;
  9. the whole story runs in under two and a half minutes (the driver waits on conditions, never on fixed sleeps).
Screenshots of the 1 + 1 stack and the 2 x 2, dark and light, land in the directory ROMP_ROWS_SHOTS names (default
/tmp/chat-rows-shots) for a human look. Skips LOUDLY when the extension deps or a playwright browser are absent (CI
installs none). Synthetic only: placeholder sids, invented notes-api prompt text, no real session data."""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
from romp_load import load_source
from tests.dist_copy import copy_dist
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# the kernel refuses to boot with a retired key variable or a 1Password name in its environment (kernel/credentials.py
# check_boot_environment): the lab's kernel env is scrubbed by the kernel's own rule, read from the module itself
_cred = load_source("romp_credentials_rows_served", os.path.join(ROOT, "kernel", "credentials.py"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")

SID_A = "11111111-2222-4333-8444-000000000401"   # "web": the first column's session
SID_B = "11111111-2222-4333-8444-000000000402"   # "api": the bottom row opens on it
SID_C = "11111111-2222-4333-8444-000000000403"   # "tests": the bottom row's second column
SID_D = "11111111-2222-4333-8444-000000000404"   # "docs": the top row's second column
SID_E = "11111111-2222-4333-8444-000000000405"   # "lint": the fifth, refused at the cap
SESSIONS = [(SID_A, "web", 1), (SID_B, "api", 2), (SID_C, "tests", 3), (SID_D, "docs", 4), (SID_E, "lint", 5)]
SLACK_PX = 40
SHOTS = os.environ.get("ROMP_ROWS_SHOTS", "/tmp/chat-rows-shots")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(sid, tag, cwd, pairs):
    """`pairs` CLOSED user/assistant turns for `sid` (an OPEN turn would invite the boot reconcile to resume
    it); `tag` keeps the sessions' message uuids apart."""
    out, parent, t = [], None, 1_700_000_000
    filler = ["The ranking pass reads its weights from the notes-api config now.",
              "Tokenizer edge cases (hyphens, quotes) are covered by the new fixture set.",
              "Index rebuild time is dominated by the stemmer; caching its table halves it."]
    for i in range(pairs):
        u = "11111111-2222-4333-8444-%02x00000c%04x" % (tag, i)
        a = "11111111-2222-4333-8444-%02x00000d%04x" % (tag, i)
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": sid, "cwd": cwd,
                    "message": {"role": "user", "content": "please keep going with the search module notes (part %d)" % (i + 1)}})
        body = "\n\n".join(["Note %d." % (i + 1)] + [filler[(i + k) % len(filler)] for k in range(2)])
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": sid, "cwd": cwd,
                    "message": {"id": "msg_rows_%d_%04d" % (tag, i), "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                                "content": [{"type": "text", "text": body}], "stop_reason": "end_turn"}})
        parent = a
    return "\n".join(json.dumps(r) for r in out) + "\n"


# The chat iframes are SAME-ORIGIN with the shell, so every probe reads a column's document from the shell
# context (document.getElementById(fid).contentDocument): no frame handles that a navigation could tear down.
DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });   // an explicit context: step 8d opens peer pages in it, sharing this page's localStorage
const page = await context.newPage();
const out = { t0: Date.now() };
const die = async (why) => {
  out.ms = Date.now() - out.t0;
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};
const T = 15000;
const waitFn = async (fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await die(why + " (" + String(e).split("\n")[0] + ")"); });
const waitTabs = (fid, sids) => waitFn(([fid, sids]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id);
  return sids.every((s) => ids.includes(s));
}, [fid, sids], fid + " never showed tabs " + sids.join(","));
const waitNoTabs = (fid, sids) => waitFn(([fid, sids]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id);
  return ids.length > 0 && sids.every((s) => !ids.includes(s));
}, [fid, sids], fid + " still lists " + sids.join(","));
const waitActive = (fid, sid) => waitFn(([fid, sid]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid;
}, [fid, sid], fid + " never activated " + sid);
const waitBootGone = () => waitFn(() => !document.getElementById("romp-boot"), null, "boot splash never cleared");
const waitGone = (id) => waitFn((id) => !document.getElementById(id), id, id + " never left the row");
const waitDraggable = (fid, sid) => waitFn(([fid, sid]) => { const f = document.getElementById(fid); const d = f && f.contentDocument; const t = d && d.querySelector('#tabs .tab[data-id="' + sid + '"]'); return !!(t && t.draggable); }, [fid, sid], sid + "'s tab in " + fid + " never became draggable");
const activeIn = (fid) => page.evaluate((fid) => { const f = document.getElementById(fid); const d = f && f.contentDocument; const t = d && d.querySelector("#tabs .tab.active[data-id]"); return t ? t.dataset.id : null; }, fid);
const tabsIn = (fid) => page.evaluate((fid) => { const f = document.getElementById(fid); const d = f && f.contentDocument; return d ? Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id) : null; }, fid);
const clickTab = async (fid, sid) => { const fr = await (await page.$("#" + fid)).contentFrame(); await fr.locator('#tabs .tab[data-id="' + sid + '"]').first().click(); };
const store = () => page.evaluate(() => localStorage.getItem("romp-chat-cols"));
const frames = () => page.evaluate(() => window.__rompChatFrameIds());
// the first pane's DOCUMENT identity: a nonce stamped on its document element; a reload (or a re-parenting of the iframe,
// which reloads it) gives a fresh document without it
const stamp = () => page.evaluate(() => { const d = document.getElementById("f-chat").contentDocument; const n = "n-" + Math.random().toString(36).slice(2); d.documentElement.setAttribute("data-rows-nonce", n); return n; });
const nonce = () => page.evaluate(() => { const d = document.getElementById("f-chat").contentDocument; return d ? d.documentElement.getAttribute("data-rows-nonce") : null; });
// the boxes the browser laid out: the chat area, its rows and gutter, every chat pane (null when absent), with the computed display
const geometry = () => page.evaluate(() => {
  const r = (id) => { const e = document.getElementById(id); if (!e) return null; const b = e.getBoundingClientRect(); return { left: b.left, top: b.top, width: b.width, height: b.height, right: b.right, bottom: b.bottom, display: getComputedStyle(e).display }; };
  return { area: r("chat-area"), row1: r("chat-row-1"), row2: r("chat-row-2"), gvRows: r("gv-rows"), p1: r("chat-pane"), p2: r("chat-pane-2"), p3: r("chat-pane-3"), p4: r("chat-pane-4"), cls: document.getElementById("chat-area").className, frames: window.__rompChatFrameIds(), cols: localStorage.getItem("romp-chat-cols") };
});
const rectIn = (fid, sel) => page.evaluate(([fid, sel]) => {
  const f = document.getElementById(fid); const fr = f.getBoundingClientRect(); const el = f.contentDocument.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect(); return { x: fr.left + r.left, y: fr.top + r.top, w: r.width, h: r.height };
}, [fid, sel]);
// a REAL drag of a tab: the pointer presses on it and moves past the threshold, so the page's dragstart fires (its
// tabDrag message mounts the shell's zones); moves carry the drag over a zone (Chromium's intercepted drag dispatches
// dragenter/dragover there); the release drops
const dragStart = async (fid, sid) => {
  await waitDraggable(fid, sid);
  const t = await rectIn(fid, '#tabs .tab[data-id="' + sid + '"]');
  if (!t) await die("no tab for " + sid + " in " + fid);
  await page.mouse.move(t.x + t.w / 2, t.y + t.h / 2);
  await page.mouse.down();
  await page.mouse.move(t.x + t.w / 2 + 24, t.y + t.h / 2 + 6, { steps: 4 });
};
const zones = () => page.evaluate(() => Array.from(document.querySelectorAll(".col-drop")).map((z) => {
  const r = z.getBoundingClientRect();
  return { cls: z.className, parent: z.parentElement.id, col: z.getAttribute("data-col"), row: z.getAttribute("data-row"), refused: z.getAttribute("data-refused"), left: r.left, top: r.top, width: r.width, height: r.height };
}));
const ghost = () => page.evaluate(() => { const g = document.getElementById("col-ghost"); const r = g.getBoundingClientRect(); return { cls: g.className, text: g.textContent, left: r.left, top: r.top, width: r.width, height: r.height, display: getComputedStyle(g).display }; });
const overZone = async (z) => {
  await page.mouse.move(z.left + z.width / 2, z.top + z.height / 2, { steps: 8 });
  await waitFn(() => document.getElementById("col-ghost").classList.contains("on"), null, "the rectangle never showed over the zone " + z.cls);
  return ghost();
};
// the screenshots: the shell and every pane read the theme from romp:settings (the shell on its own event, the panes on the storage event)
fs.mkdirSync(cfg.shots, { recursive: true });
const theme = async (t) => {
  await page.evaluate((t) => { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}"); s.theme = t; localStorage.setItem("romp:settings", JSON.stringify(s)); window.dispatchEvent(new Event("romp:settings")); }, t);
  await page.waitForFunction((light) => {
    if (document.body.classList.contains("theme-light") !== light) return false;
    return window.__rompChatFrameIds().every((id) => { const d = document.getElementById(id).contentDocument; return !!d && d.body && d.body.classList.contains("theme-light") === light; });
  }, t === "yatharth-light", { timeout: T }).catch(() => {});
};
const shots = async (name) => {
  await page.screenshot({ path: cfg.shots + "/" + name + "-dark.png" });
  await theme("yatharth-light"); await page.screenshot({ path: cfg.shots + "/" + name + "-light.png" }); await theme("classic");
  out.shots = (out.shots || []).concat([name + "-dark.png", name + "-light.png"]);
};

// ---- load: every session is a tab in the first column; the first column shows A ----
await page.goto(cfg.url);
await waitTabs("f-chat", [cfg.a, cfg.b, cfg.c, cfg.d, cfg.e]);
await waitBootGone();
if ((await activeIn("f-chat")) !== cfg.a) { await clickTab("f-chat", cfg.a); await waitActive("f-chat", cfg.a); }
out.before = Object.assign(await geometry(), { nonce: await stamp() });

// ---- 1. B to the BOTTOM edge: the bottom row opens on it ----
await dragStart("f-chat", cfg.b);
await waitFn(() => !!document.querySelector(".col-drop.col-drop-bottom"), null, "the bottom zone never mounted for B's drag");
out.s1 = { zones: await zones() };
const bottom1 = out.s1.zones.find((z) => z.cls.includes("col-drop-bottom"));
out.s1.ghost = await overZone(bottom1);
await page.mouse.up();
await waitTabs("f-chat-2", [cfg.b]); await waitActive("f-chat-2", cfg.b); await waitNoTabs("f-chat", [cfg.b]);
out.s1.after = Object.assign(await geometry(), { nonce: await nonce(), col1Tabs: await tabsIn("f-chat"), col2Tabs: await tabsIn("f-chat-2"), zonesLeft: (await zones()).length, ghostAfter: await ghost() });
await shots("stack-1-1");

// ---- 2. C to the BOTTOM ROW's right edge: a second column beside B ----
await dragStart("f-chat", cfg.c);
await waitFn(() => !!document.querySelector('.col-drop.col-drop-edge[data-row="2"]'), null, "the bottom row's edge never mounted for C's drag");
out.s2 = { zones: await zones() };
const edge2 = out.s2.zones.find((z) => z.cls.includes("col-drop-edge") && z.row === "2");
out.s2.ghost = await overZone(edge2);
await page.mouse.up();
await waitTabs("f-chat-3", [cfg.c]); await waitActive("f-chat-3", cfg.c); await waitNoTabs("f-chat", [cfg.c]);
out.s2.after = Object.assign(await geometry(), { nonce: await nonce() });

// ---- 3. D to the TOP row's right edge: two over two ----
await dragStart("f-chat", cfg.d);
await waitFn(() => !!document.querySelector('.col-drop.col-drop-edge[data-row="1"]'), null, "the top row's edge never mounted for D's drag");
out.s3 = { zones: await zones() };
const edge1 = out.s3.zones.find((z) => z.cls.includes("col-drop-edge") && z.row === "1");
out.s3.ghost = await overZone(edge1);
await page.mouse.up();
await waitTabs("f-chat-4", [cfg.d]); await waitActive("f-chat-4", cfg.d); await waitNoTabs("f-chat", [cfg.d]);
out.s3.after = Object.assign(await geometry(), { nonce: await nonce() });
await shots("grid-2-2");

// ---- 4. the CAP: E's drag mounts every making zone refused ----
await dragStart("f-chat", cfg.e);
await waitFn(() => !!document.querySelector(".col-drop.col-drop-bottom"), null, "the bottom zone never mounted for E's drag");
out.s4 = { zones: await zones() };
const bottom4 = out.s4.zones.find((z) => z.cls.includes("col-drop-bottom"));
out.s4.ghost = await overZone(bottom4);
await page.mouse.up();
await waitFn(() => document.querySelectorAll(".col-drop").length === 0, null, "the zones never unmounted after the refused drop");
out.s4.after = Object.assign(await geometry(), { nonce: await nonce(), col1Tabs: await tabsIn("f-chat") });

// ---- 5. the ROW GUTTER dragged 120 px down, then a reload ----
const gutterBefore = await geometry();
const gr = gutterBefore.gvRows;
await page.mouse.move(gr.left + gr.width / 2, gr.top + gr.height / 2);
await page.mouse.down();
await page.mouse.move(gr.left + gr.width / 2, gr.top + gr.height / 2 + 120, { steps: 10 });
await page.mouse.up();
await waitFn(() => { const s = JSON.parse(localStorage.getItem("romp-chat-cols") || "{}"); return typeof s.rowSplit === "number" && Math.abs(s.rowSplit - 0.5) > 0.02; }, null, "rowSplit never changed after the gutter drag");
out.s5 = { before: gutterBefore, after: Object.assign(await geometry(), { nonce: await nonce() }) };
await page.reload();
await waitTabs("f-chat", [cfg.a, cfg.e]); await waitTabs("f-chat-2", [cfg.b]); await waitTabs("f-chat-3", [cfg.c]); await waitTabs("f-chat-4", [cfg.d]);
await waitBootGone();
out.s5.reloaded = Object.assign(await geometry(), { nonceGone: await nonce() });
out.s5.nonce2 = await stamp();

// ---- 6. the FOLD: B then C home ----
await page.evaluate((sid) => window.__rompMoveTab(sid, 1), cfg.b);
await waitGone("chat-pane-2"); await waitTabs("f-chat", [cfg.a, cfg.b, cfg.e]);
out.s6 = { onB: Object.assign(await geometry(), { nonce: await nonce() }) };
await page.evaluate((sid) => window.__rompMoveTab(sid, 1), cfg.c);
await waitGone("chat-pane-3"); await waitTabs("f-chat", [cfg.a, cfg.b, cfg.c, cfg.e]);
await waitFn(() => !document.getElementById("chat-area").classList.contains("rows"), null, "the bottom row never folded");
out.s6.onC = Object.assign(await geometry(), { nonce: await nonce(), bytes: await store() });

// ---- 6b. the busy guard across rows: D alone in column 4 over a create in flight, dragged to the bottom zone ----
await page.evaluate(() => { const w = document.getElementById("f-chat-4").contentWindow; w.__rompRowsBusyOrig = w.__rompColumnBusy; w.__rompColumnBusy = () => true; });
const bytesBeforeBusy = await store();
await dragStart("f-chat-4", cfg.d);
await waitFn(() => !!document.querySelector(".col-drop.col-drop-bottom"), null, "the bottom zone never mounted for D's drag (alone in the top row: a row below is a move)");
out.s6b = { zones: await zones() };
const bottomBusy = out.s6b.zones.find((z) => z.cls.includes("col-drop-bottom"));
out.s6b.ghost = await overZone(bottomBusy);
await page.mouse.up();
await waitFn(() => document.querySelectorAll(".col-drop").length === 0, null, "the zones never unmounted after the refused drop");
out.s6b.after = Object.assign(await geometry(), { nonce: await nonce(), bytesBefore: bytesBeforeBusy, bytes: await store(), col4Tabs: await tabsIn("f-chat-4"), col1Tabs: await tabsIn("f-chat") });
await page.evaluate(() => { const w = document.getElementById("f-chat-4").contentWindow; w.__rompColumnBusy = w.__rompRowsBusyOrig; delete w.__rompRowsBusyOrig; });

// ---- 6c. the share is forgotten with the row: B to the bottom edge again, the rectangle's half is the row ----
await dragStart("f-chat", cfg.b);
await waitFn(() => !!document.querySelector(".col-drop.col-drop-bottom"), null, "the bottom zone never mounted for B's second drag");
const bottomAgain = (await zones()).find((z) => z.cls.includes("col-drop-bottom"));
out.s6c = { ghost: await overZone(bottomAgain), areaBefore: (await geometry()).area };
await page.mouse.up();
await waitTabs("f-chat-2", [cfg.b]); await waitActive("f-chat-2", cfg.b);
out.s6c.after = Object.assign(await geometry(), { nonce: await nonce() });
await page.evaluate((sid) => window.__rompMoveTab(sid, 1), cfg.b);
await waitGone("chat-pane-2"); await waitFn(() => !document.getElementById("chat-area").classList.contains("rows"), null, "the bottom row never folded again");

// ---- 7. back to one ----
await page.evaluate(() => window.__rompCloseSplit(4));
await waitGone("chat-pane-4"); await waitTabs("f-chat", [cfg.a, cfg.b, cfg.c, cfg.d, cfg.e]);
out.s7 = Object.assign(await geometry(), { nonce: await nonce(), col1Tabs: await tabsIn("f-chat") });

// ---- 8. the upgrade of a pre-rows pane store: legacy stores reload at the widths the pre-rows shell gave them ----
const widthsNow = () => page.evaluate(() => {
  const w = (id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; };
  return { row: w("chat-area") === null ? null : document.querySelector(".row").getBoundingClientRect().width, area: w("chat-area"), chat1: w("chat-pane"), chat2: w("chat-pane-2"), chat3: w("chat-pane-3"), chat4: w("chat-pane-4"), feed: w("feed-pane"), fleet: w("fleet-pane"),
           grow: JSON.parse(localStorage.getItem("romp-pane-grow") || "null"), frames: window.__rompChatFrameIds() };
});
const LEGACY = [
  { name: "one", grow: { chat: 700, fleet: 34, feed: 300, files: 40 }, cols: [] },
  { name: "two", grow: { chat: 640, fleet: 34, feed: 400, chat2: 400 }, cols: [{ n: 2, ids: [cfg.b] }] },
  { name: "three", grow: { chat: 500, chat2: 300, chat3: 200, fleet: 34, feed: 400 }, cols: [{ n: 2, ids: [cfg.b] }, { n: 3, ids: [cfg.c] }] },
  { name: "four", grow: { chat: 400, chat2: 300, chat3: 200, chat4: 100, fleet: 34, feed: 300 }, cols: [{ n: 2, ids: [cfg.b] }, { n: 3, ids: [cfg.c] }, { n: 4, ids: [cfg.d] }] },
  // a restored column with NO stored weight (review find 2026-09-15), two of them, and an empty legacy object
  { name: "missing-one", grow: { chat: 640, fleet: 34, feed: 400 }, cols: [{ n: 2, ids: [cfg.b] }] },
  { name: "missing-two", grow: { chat: 640, fleet: 34, feed: 400 }, cols: [{ n: 2, ids: [cfg.b] }, { n: 3, ids: [cfg.c] }] },
  { name: "empty", grow: {}, cols: [{ n: 2, ids: [cfg.b] }, { n: 3, ids: [cfg.c] }] },
];
out.s8 = {};
for (const c of LEGACY) {
  await page.evaluate(([grow, cols]) => { localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols })); }, [c.grow, c.cols]);
  await page.reload();
  await waitTabs("f-chat", [cfg.a]);
  for (const col of c.cols) await waitTabs("f-chat-" + col.n, col.ids);
  await waitBootGone();
  const first = await widthsNow();
  await page.reload();
  await waitTabs("f-chat", [cfg.a]);
  for (const col of c.cols) await waitTabs("f-chat-" + col.n, col.ids);
  await waitBootGone();
  out.s8[c.name] = { first, second: await widthsNow() };
}
// …and the chat pane OFF at the restore (review find 2026-09-15, third pass): the rail's Chat before the reload, the legacy
// store with a missing column weight, the reload (the upgrade waits), the rail's Chat again (the upgrade runs), a reload after
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => !document.body.classList.contains("po-chat"), null, "the rail never hid the chat pane");
await page.evaluate(([grow, cols]) => { localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols })); }, [{ chat: 640, fleet: 34, feed: 400 }, [{ n: 2, ids: [cfg.b] }]]);
await page.reload();
await waitBootGone();
await waitFn(() => !document.body.classList.contains("po-chat") && !!window.__rompChatFrameIds && window.__rompChatFrameIds().length === 2, null, "column 2 never restored while the chat pane was hidden");
const hidden = await widthsNow();
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => document.body.classList.contains("po-chat"), null, "the rail never showed the chat pane");
await waitTabs("f-chat", [cfg.a]); await waitTabs("f-chat-2", [cfg.b]);
const shownFirst = await widthsNow();
await page.reload();
await waitTabs("f-chat", [cfg.a]); await waitTabs("f-chat-2", [cfg.b]); await waitBootGone();
out.s8["hidden-chat"] = { hidden, first: shownFirst, second: await widthsNow() };
// …the outline REVEALED from the rail while the upgrade waits (the fourth pass): its fair grow is the pre-rows rule over the
// feed alone, and the chat pane's show then lays out four equal panes
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => !document.body.classList.contains("po-chat"), null, "the rail never hid the chat pane (reveal case)");
await page.evaluate(([grow, cols]) => { localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols })); }, [{ chat: 640, fleet: 34, feed: 400 }, [{ n: 2, ids: [cfg.b] }]]);
await page.reload();
await waitBootGone();
await waitFn(() => !document.body.classList.contains("po-chat") && !!window.__rompChatFrameIds && window.__rompChatFrameIds().length === 2, null, "column 2 never restored while hidden (reveal case)");
await page.click(".rail-btn[data-pane=fleet]");
await waitFn(() => document.body.classList.contains("po-fleet"), null, "the rail never showed the outline");
const revealedPending = await widthsNow();
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => document.body.classList.contains("po-chat"), null, "the rail never showed the chat pane (reveal case)");
await waitTabs("f-chat", [cfg.a]); await waitTabs("f-chat-2", [cfg.b]);
const revealedShown = await widthsNow();
await page.reload();
await waitTabs("f-chat", [cfg.a]); await waitTabs("f-chat-2", [cfg.b]); await waitBootGone();
out.s8["hidden-chat-reveal"] = { pending: revealedPending, first: revealedShown, second: await widthsNow() };
await page.click(".rail-btn[data-pane=fleet]");
await waitFn(() => !document.body.classList.contains("po-fleet"), null, "the rail never hid the outline again");
// …and the restored column CLOSED from the palette while the upgrade waits: Close this column falls to the last split column
// when the first frame holds the focus; the show then lays out the first pane and the feed as two halves, no chat2 anywhere
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => !document.body.classList.contains("po-chat"), null, "the rail never hid the chat pane (close case)");
await page.evaluate(([grow, cols]) => { localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols })); }, [{ chat: 640, fleet: 34, feed: 400 }, [{ n: 2, ids: [cfg.b] }]]);
await page.reload();
await waitBootGone();
await waitFn(() => !document.body.classList.contains("po-chat") && !!window.__rompChatFrameIds && window.__rompChatFrameIds().length === 2, null, "column 2 never restored while hidden (close case)");
const feedBox = await page.evaluate(() => { const r = document.getElementById("f-feed").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + Math.min(r.height / 2, 200) }; });
await page.mouse.click(feedBox.x, feedBox.y);   // the keyboard into the feed's document: the palette's chord is wired in every pane
await page.keyboard.press("Control+P");
await waitFn(() => { const b = document.getElementById("rpal-back"); return !!b && !b.hidden; }, null, "the palette never opened from the feed pane");
await page.keyboard.type("Close this column");
await waitFn(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return !!r && /Close this column/.test(r.textContent || ""); }, null, "the palette never matched Close this column");
await page.keyboard.press("Enter");
await waitFn(() => { const b = document.getElementById("rpal-back"); return !!b && b.hidden; }, null, "the palette never closed on Enter");
await waitGone("chat-pane-2");
const closedPending = await widthsNow();
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => document.body.classList.contains("po-chat"), null, "the rail never showed the chat pane (close case)");
await waitTabs("f-chat", [cfg.a, cfg.b]);
const closedShown = await widthsNow();
await page.reload();
await waitTabs("f-chat", [cfg.a, cfg.b]); await waitBootGone();
out.s8["hidden-chat-close"] = { pending: closedPending, first: closedShown, second: await widthsNow() };
// ---- 8d. a PEER upgrades while this page waits (the fifth pass): page A, in the same browser context (one localStorage), shows its
//      Chat pane and upgrades the store; this page ingests at the storage event; its FIRST action — the rail's Outline — fair-grows the
//      pane exactly as page C, fresh from the upgraded store with the same panes on screen; then the palette's close leaves no chat2
const inlineVar = (pg, k) => pg.evaluate((k) => parseFloat(document.querySelector(".row").style.getPropertyValue("--g-" + k)), k);
const legacyOf = (pg) => pg.evaluate(() => window.__rompGrowLegacy && window.__rompGrowLegacy());
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => !document.body.classList.contains("po-chat"), null, "the rail never hid the chat pane (peer case)");
await page.evaluate(([grow, cols]) => { localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols })); }, [{ chat: 640, fleet: 34, feed: 400 }, [{ n: 2, ids: [cfg.b] }]]);
await page.reload();
await waitBootGone();
await waitFn(() => !document.body.classList.contains("po-chat") && !!window.__rompChatFrameIds && window.__rompChatFrameIds().length === 2 && window.__rompGrowLegacy() === true, null, "this page never came up pending (peer case)");
const pageA = await context.newPage();
const dieA = async (why) => { try { await pageA.close(); } catch (e) { /* */ } await die(why); };
await pageA.goto(cfg.url);
await pageA.waitForFunction(() => !document.getElementById("romp-boot") && !!window.__rompGrowLegacy && window.__rompGrowLegacy() === true && !document.body.classList.contains("po-chat") && window.__rompChatFrameIds().length === 2, null, { timeout: T }).catch(async (e) => { await dieA("page A never came up pending: " + String(e).split("\n")[0]); });
await pageA.click(".rail-btn[data-pane=chat]");
await pageA.waitForFunction(() => document.body.classList.contains("po-chat") && window.__rompGrowLegacy() === false, null, { timeout: T }).catch(async (e) => { await dieA("page A never upgraded: " + String(e).split("\n")[0]); });
const upgraded = await pageA.evaluate(() => JSON.parse(localStorage.getItem("romp-pane-grow")));
await waitFn(() => window.__rompGrowLegacy() === false, null, "this page never ingested the peer's upgrade at its storage event");
const ingested = { chat1: await inlineVar(page, "chat1"), chat: await inlineVar(page, "chat"), feed: await inlineVar(page, "feed"), chatHidden: await page.evaluate(() => !document.body.classList.contains("po-chat")) };
await pageA.click(".rail-btn[data-pane=chat]");   // A hides its chat pane again, so a fresh page boots with the panes this one shows
await pageA.waitForFunction(() => !document.body.classList.contains("po-chat"), null, { timeout: T }).catch(async (e) => { await dieA("page A never hid its chat pane again: " + String(e).split("\n")[0]); });
const pageC = await context.newPage();
const dieC = async (why) => { try { await pageA.close(); await pageC.close(); } catch (e) { /* */ } await die(why); };
await pageC.goto(cfg.url);
await pageC.waitForFunction(() => !document.getElementById("romp-boot") && !!window.__rompGrowLegacy && window.__rompGrowLegacy() === false && !document.body.classList.contains("po-chat") && !document.body.classList.contains("po-fleet") && window.__rompChatFrameIds().length === 2, null, { timeout: T }).catch(async (e) => { await dieC("page C never came up current with the chat and the outline hidden: " + String(e).split("\n")[0]); });
await pageC.click(".rail-btn[data-pane=fleet]");
await pageC.waitForFunction(() => document.body.classList.contains("po-fleet"), null, { timeout: T }).catch(async (e) => { await dieC("page C never showed the outline: " + String(e).split("\n")[0]); });
const fleetC = await inlineVar(pageC, "fleet");
await page.click(".rail-btn[data-pane=fleet]");
await waitFn(() => document.body.classList.contains("po-fleet"), null, "the rail never showed the outline (peer case)");
const fleetB = await inlineVar(page, "fleet");
await pageA.close(); await pageC.close();
const feedBox2 = await page.evaluate(() => { const r = document.getElementById("f-feed").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + Math.min(r.height / 2, 200) }; });
await page.mouse.click(feedBox2.x, feedBox2.y);
await page.keyboard.press("Control+P");
await waitFn(() => { const b = document.getElementById("rpal-back"); return !!b && !b.hidden; }, null, "the palette never opened from the feed pane (peer case)");
await page.keyboard.type("Close this column");
await waitFn(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return !!r && /Close this column/.test(r.textContent || ""); }, null, "the palette never matched Close this column (peer case)");
await page.keyboard.press("Enter");
await waitFn(() => { const b = document.getElementById("rpal-back"); return !!b && b.hidden; }, null, "the palette never closed on Enter (peer case)");
await waitGone("chat-pane-2");
out.s8d = { upgraded, ingested, fleetB, fleetC, afterClose: await widthsNow(), legacyAfter: await legacyOf(page) };
await page.click(".rail-btn[data-pane=fleet]");
await waitFn(() => !document.body.classList.contains("po-fleet"), null, "the rail never hid the outline again (peer case)");
// ---- 8e. a DRAG is one transaction (the sixth pass): the outline, the feed and the files pane shown with the chat pane off, this
//      page pending; the outline | feed gutter dragged; a peer page writes an upgraded store with the files weight changed between two
//      pointer moves — nothing moves under the hand — and the release lands the divider within a pixel of the line while the store
//      carries the peer's files weight; then a drag abandoned to the window's blur puts everything back and writes nothing
const boxOf = (id) => page.evaluate((id) => { const e = document.getElementById(id); if (!e) return null; const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, width: r.width, top: r.top, height: r.height }; }, id);
const boxes = async () => ({ fleet: await boxOf("fleet-pane"), feed: await boxOf("feed-pane"), files: await boxOf("files-pane"), gvb: await boxOf("gv-b") });
await page.evaluate(([grow, cols]) => {
  localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols }));
  const st = JSON.parse(localStorage.getItem("romp:settings") || "{}"); st.showFilesControl = true; localStorage.setItem("romp:settings", JSON.stringify(st));
  localStorage.setItem("romp-panes", JSON.stringify({ chat: false, fleet: true, feed: true, timeline: true, files: true }));
}, [{ chat: 640, fleet: 300, feed: 400, files: 300 }, [{ n: 2, ids: [cfg.b] }]]);
await page.reload();
await waitBootGone();
await waitFn(() => !document.body.classList.contains("po-chat") && document.body.classList.contains("po-fleet") && document.body.classList.contains("po-files") && !!window.__rompGrowLegacy && window.__rompGrowLegacy() === true && window.__rompChatFrameIds().length === 2, null, "this page never came up pending with the outline, the feed and the files pane shown");
const pageP = await context.newPage();   // the peer: booted first, so its own boot writes land before the drag
const dieP = async (why) => { try { await pageP.close(); } catch (e) { /* */ } await die(why); };
await pageP.goto(cfg.url);
await pageP.waitForFunction(() => !document.getElementById("romp-boot") && !!window.__rompGrowLegacy, null, { timeout: T }).catch(async (e) => { await dieP("the peer page never booted: " + String(e).split("\n")[0]); });
const pre = await boxes();
const g8 = pre.gvb;
await page.mouse.move(g8.left + g8.width / 2, g8.top + g8.height / 2);
await page.mouse.down();
await page.mouse.move(g8.left + g8.width / 2 + 50, g8.top + g8.height / 2, { steps: 5 });
const midBoxes = await boxes();
await pageP.evaluate(() => localStorage.setItem("romp-pane-grow", JSON.stringify({ chat: 669, chat1: 331, chat2: 331, fleet: 34, feed: 331, files: 900 })));   // the peer's upgrade, mid-drag
await page.mouse.move(g8.left + g8.width / 2 + 50, g8.top + g8.height / 2 + 1, { steps: 3 });   // another move after the write: still nothing under the hand
const afterWrite = { boxes: await boxes(), legacy: await legacyOf(page), ghostLeft: await page.evaluate(() => parseFloat(document.getElementById("gv-ghost").style.left)), drag: await page.evaluate(() => document.body.classList.contains("drag")) };
await page.mouse.up();
await waitFn(() => { const s = JSON.parse(localStorage.getItem("romp-pane-grow") || "{}"); return "chat1" in s && s.files === 900; }, null, "the release never wrote the merged store");
const released = { boxes: await boxes(), store: await page.evaluate(() => JSON.parse(localStorage.getItem("romp-pane-grow"))), legacy: await legacyOf(page), drag: await page.evaluate(() => document.body.classList.contains("drag")) };
// …and a drag abandoned to the window's blur
const beforeAbandon = { boxes: await boxes(), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")) };
const g9 = beforeAbandon.boxes.gvb;
await page.mouse.move(g9.left + g9.width / 2, g9.top + g9.height / 2);
await page.mouse.down();
await page.mouse.move(g9.left + g9.width / 2 + 40, g9.top + g9.height / 2, { steps: 4 });
const abandonMid = { drag: await page.evaluate(() => document.body.classList.contains("drag")), ghost: await page.evaluate(() => getComputedStyle(document.getElementById("gv-ghost")).display) };
await page.evaluate(() => window.dispatchEvent(new Event("blur")));
await waitFn(() => !document.body.classList.contains("drag"), null, "the blur never ended the drag");
const abandoned = { boxes: await boxes(), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")), ghost: await page.evaluate(() => getComputedStyle(document.getElementById("gv-ghost")).display) };
await page.mouse.up();   // the stale release
const afterStale = { boxes: await boxes(), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")) };
await pageP.close();
out.s8e = { pre, midBoxes, afterWrite, released, beforeAbandon, abandonMid, abandoned, afterStale };
// ---- 8f. a PEER CLOSES A COLUMN under a live drag (the seventh pass). A (this page) pending with the column restored and its chat
//      pane off holds the outline | feed gutter; B (same context) shows its chat pane (upgrading the store) and closes the column from
//      its palette: A's drag ends at the arrangement's arrival, A takes B's store in, and A's release writes nothing — the store
//      current, chat2 gone, the drag's +50 never landed. Then the mirror on a current page: the gutter beside column 2 held while B
//      closes column 2 — the files pane is untouched.
await page.evaluate(([grow, cols]) => {
  localStorage.setItem("romp-pane-grow", JSON.stringify(grow)); localStorage.setItem("romp-chat-cols", JSON.stringify({ v: 2, cols }));
  localStorage.setItem("romp-panes", JSON.stringify({ chat: false, fleet: true, feed: true, timeline: true, files: true }));
}, [{ chat: 640, fleet: 300, feed: 400, files: 300 }, [{ n: 2, ids: [cfg.b] }]]);
await page.reload(); await waitBootGone();
await waitFn(() => !document.body.classList.contains("po-chat") && window.__rompGrowLegacy() === true && window.__rompChatFrameIds().length === 2, null, "A never came up pending (close-under-drag case)");
const pageB = await context.newPage();
const dieB = async (why) => { try { await pageB.close(); } catch (e) { /* */ } await die(why); };
await pageB.goto(cfg.url);
await pageB.waitForFunction(() => !document.getElementById("romp-boot") && !!window.__rompGrowLegacy && window.__rompChatFrameIds().length === 2 && !document.body.classList.contains("po-chat"), null, { timeout: T }).catch(async (e) => { await dieB("B never came up with the column: " + String(e).split("\n")[0]); });
const preF = await boxes(); const gF = preF.gvb;
await page.mouse.move(gF.left + gF.width / 2, gF.top + gF.height / 2); await page.mouse.down();
await page.mouse.move(gF.left + gF.width / 2 + 50, gF.top + gF.height / 2, { steps: 5 });
const heldF = { drag: await page.evaluate(() => document.body.classList.contains("drag")), ghostLeft: await page.evaluate(() => parseFloat(document.getElementById("gv-ghost").style.left)) };
await pageB.click(".rail-btn[data-pane=chat]");   // B shows its chat pane: the upgrade, written while A holds the gutter
await pageB.waitForFunction(() => document.body.classList.contains("po-chat") && window.__rompGrowLegacy() === false, null, { timeout: T }).catch(async (e) => { await dieB("B never upgraded: " + String(e).split("\n")[0]); });
await pageB.waitForFunction(([sid]) => { const d = document.getElementById("f-chat-2").contentDocument; return !!d && !!d.querySelector('#tabs .tab[data-id="' + sid + '"]'); }, [cfg.b], { timeout: T }).catch(async (e) => { await dieB("B's column never showed its tab: " + String(e).split("\n")[0]); });
const chatBoxB = await pageB.evaluate(() => { const f = document.getElementById("f-chat"); const r = f.getBoundingClientRect(); const c = f.contentDocument.getElementById("content").getBoundingClientRect(); return { x: r.left + c.left + c.width / 2, y: r.top + c.top + Math.min(c.height / 2, 120) }; });
await pageB.mouse.click(chatBoxB.x, chatBoxB.y);
await pageB.keyboard.press("Control+P");
await pageB.waitForFunction(() => { const b = document.getElementById("rpal-back"); return !!b && !b.hidden; }, null, { timeout: T }).catch(async (e) => { await dieB("B's palette never opened: " + String(e).split("\n")[0]); });
await pageB.keyboard.type("Close this column");
await pageB.waitForFunction(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return !!r && /Close this column/.test(r.textContent || ""); }, null, { timeout: T }).catch(async (e) => { await dieB("B's palette never matched: " + String(e).split("\n")[0]); });
await pageB.keyboard.press("Enter");
await pageB.waitForFunction(() => !document.getElementById("chat-pane-2"), null, { timeout: T }).catch(async (e) => { await dieB("B never closed the column: " + String(e).split("\n")[0]); });
await waitFn(() => window.__rompChatFrameIds().length === 1 && !document.body.classList.contains("drag"), null, "A never closed column 2 on B's arrangement, or never ended its drag");
const cancelledF = { boxes: await boxes(), legacy: await legacyOf(page), store: await page.evaluate(() => JSON.parse(localStorage.getItem("romp-pane-grow"))), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")), frames: await page.evaluate(() => window.__rompChatFrameIds()) };
await page.mouse.up();   // the stale release
const releasedF = { boxes: await boxes(), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")), legacy: await legacyOf(page) };
// the mirror: A current now, its chat pane on, a column of its own; the gutter beside that column held while B closes it
await page.click(".rail-btn[data-pane=chat]");
await waitFn(() => document.body.classList.contains("po-chat"), null, "the rail never showed A's chat pane (mirror)");
await page.evaluate((sid) => window.__rompMoveTab(sid, "new"), cfg.c);
await waitTabs("f-chat-2", [cfg.c]);
await pageB.waitForFunction(() => window.__rompChatFrameIds().length === 2, null, { timeout: T }).catch(async (e) => { await dieB("B never made A's column (mirror): " + String(e).split("\n")[0]); });
const filesBefore = { v: await inlineVar(page, "files"), store: await page.evaluate(() => JSON.parse(localStorage.getItem("romp-pane-grow")).files) };
const gM = await boxOf("gv-chat-2");
await page.mouse.move(gM.left + gM.width / 2, gM.top + gM.height / 2); await page.mouse.down();
await page.mouse.move(gM.left + gM.width / 2 + 60, gM.top + gM.height / 2, { steps: 5 });
const heldM = { drag: await page.evaluate(() => document.body.classList.contains("drag")) };
await pageB.evaluate(() => window.__rompCloseSplit(2));
await pageB.waitForFunction(() => window.__rompChatFrameIds().length === 1, null, { timeout: T }).catch(async (e) => { await dieB("B never closed column 2 (mirror): " + String(e).split("\n")[0]); });
await waitFn(() => window.__rompChatFrameIds().length === 1 && !document.body.classList.contains("drag"), null, "A never closed column 2 on B's close, or never ended its drag (mirror)");
const mirrorCancelled = { filesV: await inlineVar(page, "files"), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")) };
await page.mouse.up();
const mirror = { held: heldM, cancelled: mirrorCancelled, filesV: await inlineVar(page, "files"), store: await page.evaluate(() => JSON.parse(localStorage.getItem("romp-pane-grow"))), frames: await page.evaluate(() => window.__rompChatFrameIds()), bytes: await page.evaluate(() => localStorage.getItem("romp-pane-grow")) };
await pageB.close();
out.s8f = { pre: preF, held: heldF, cancelled: cancelledF, released: releasedF, filesBefore, mirror };
out.ms = Date.now() - out.t0;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
"""


class ServedChatRows(unittest.TestCase):
    """One kernel, one page, one driver run in setUpClass; each method asserts one step of the shared result."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="chat-rows-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # skips a concurrent build's staging files (tests/dist_copy.py)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(state, "names"), exist_ok=True)
        os.makedirs(os.path.join(state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # five synthetic SDK sessions so the chat page has five tabs; their transcripts hold only CLOSED turns, so the boot
        # reconcile never resumes any and no CLI is ever spawned
        for sid, name, tag in SESSIONS:
            Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, tag, cwd, 6))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        cls.port = _free_port()
        cls.token = "testtok-chatrows"
        cls.env = dict(os.environ,
                       XDG_STATE_HOME=os.path.join(cls.lab, "xdg"),
                       CLAUDE_CONFIG_DIR=claude,
                       ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1",
                       ROMP_SERVE_TOKEN=cls.token, ROMP_KERNEL_PORT=str(cls.port),
                       ROMP_DIST_DIR=dist, ROMP_MODEL_CATALOG="off",
                       ROMP_POSTAL_PORT=str(_free_port()), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1")
        cls.env.pop("ROMP_STATE_DIR", None)
        for k in ("ROMP_MANAGER_PID", "ROMP_SUPERVISED", "ROMP_SID", "ROMP_SESSION_NAME"):
            cls.env.pop(k, None)
        for k in [k for k in cls.env if k in _cred.RETIRED_VARS or _cred.is_op_env_name(k)]:   # the kernel's own boot rule (module top)
            cls.env.pop(k, None)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=cls.env)
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
        cls.result, cls.driver_error = None, None
        cls._drive()

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token),
                       "a": SID_A, "b": SID_B, "c": SID_C, "d": SID_D, "e": SID_E, "shots": SHOTS}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        t0 = time.monotonic()
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode()
            cls.driver_error = "driver timed out; partial output:\n%s" % so
            return
        cls.driver_s = time.monotonic() - t0
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served leg needs one (CI installs none)")
        if p.returncode != 0:
            cls.driver_error = "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
            return
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            cls.driver_error = "driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:]
            return
        r = json.loads(line[len("RESULT:"):])
        if "died" in r:
            cls.driver_error = "driver aborted early: %s\n%s" % (r["died"], json.dumps(r, indent=1)[-3000:])
            return
        cls.result = r

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            try:
                os.kill(k.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _r(self):
        if self.driver_error:
            tail = ""
            try:
                with open(self.klog) as fh:
                    tail = "\nkernel log tail:\n" + fh.read()[-2000:]
            except OSError:
                pass
            self.fail(self.driver_error + tail)
        return self.result

    # ── helpers over the geometry snapshots ─────────────────────────────────────────────────────────────
    @staticmethod
    def _overlap(a, b):
        return a["left"] < b["right"] and b["left"] < a["right"] and a["top"] < b["bottom"] and b["top"] < a["bottom"]

    def _assert_rows_stacked(self, g, where):
        r1, r2 = g["row1"], g["row2"]
        self.assertEqual(g["cls"], "rows", where + ": the area wears .rows while a column is in the bottom row")
        self.assertNotEqual(r2["display"], "none", where + ": the bottom row is shown")
        self.assertNotEqual(g["gvRows"]["display"], "none", where + ": the row gutter is shown")
        self.assertGreater(r2["height"], 100, where + ": the bottom row has real height: %r" % r2)
        self.assertLessEqual(r1["bottom"], r2["top"], where + ": the rows do not overlap: %r / %r" % (r1, r2))
        self.assertLessEqual(abs(g["gvRows"]["top"] - r1["bottom"]), 1, where + ": the gutter sits under the top row")
        self.assertLessEqual(abs(r2["top"] - g["gvRows"]["bottom"]), 1, where + ": …and over the bottom row")
        self.assertLessEqual(abs(r1["left"] - r2["left"]), 1); self.assertLessEqual(abs(r1["width"] - r2["width"]), 1, where + ": the rows share the area's width")

    def test_0_the_page_opens_on_one_column_with_no_bottom_row(self):
        r = self._r()
        g = r["before"]
        self.assertEqual(g["cls"], "", "no .rows: the bottom row and its gutter hidden")
        self.assertEqual(g["row2"]["display"], "none"); self.assertEqual(g["gvRows"]["display"], "none")
        self.assertLessEqual(abs(g["row1"]["height"] - g["area"]["height"]), 1, "the top row fills the area: %r vs %r" % (g["row1"], g["area"]))
        self.assertLessEqual(abs(g["p1"]["height"] - g["area"]["height"]), 1, "…and the first pane the row")
        self.assertEqual(g["frames"], ["f-chat"]); self.assertIsNone(g["cols"], "nothing written before a gesture")
        self.assertTrue(g["nonce"].startswith("n-"))

    def test_1_a_tab_dragged_to_the_bottom_edge_opens_the_bottom_row_on_it_without_reloading_the_first_pane(self):
        r = self._r()
        s, before = r["s1"], r["before"]
        bottom = [z for z in s["zones"] if "col-drop-bottom" in z["cls"]]
        self.assertEqual(len(bottom), 1, "one bottom zone, on the chat area: %r" % s["zones"])
        self.assertEqual(bottom[0]["parent"], "chat-area")
        self.assertLessEqual(abs(bottom[0]["left"] - before["area"]["left"]), 1); self.assertLessEqual(abs(bottom[0]["width"] - before["area"]["width"]), 1, "the zone spans the area's width")
        self.assertLessEqual(abs((bottom[0]["top"] + bottom[0]["height"]) - before["area"]["bottom"]), 1, "…along its bottom edge")
        self.assertLessEqual(abs(bottom[0]["height"] - max(72, min(180, 0.2 * before["row1"]["height"]))), 1, "a fifth of the (only) row's height, clamped")
        edges = [z for z in s["zones"] if "col-drop-edge" in z["cls"]]
        self.assertEqual([z["row"] for z in edges], ["1"], "one column: the top row's edge alone beside it")
        gh = s["ghost"]
        self.assertEqual(gh["cls"], "on"); self.assertEqual(gh["text"], "api", "the dragged session's name, no verb")
        want_h = (before["area"]["height"] - 7) / 2
        self.assertLessEqual(abs(gh["height"] - want_h), 1, "the rectangle is the box the half split opens: the bottom half less the row gutter's share: %r vs %r" % (gh, before["area"]))
        self.assertLessEqual(abs(gh["top"] - (before["area"]["bottom"] - want_h)), 1)
        self.assertLessEqual(abs(gh["left"] - before["area"]["left"]), 1); self.assertLessEqual(abs(gh["width"] - before["area"]["width"]), 1)
        a = s["after"]
        self.assertEqual(a["frames"], ["f-chat", "f-chat-2"])
        self.assertEqual(json.loads(a["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B], "row": 2}], "rowSplit": 0.5}, "row:2 on the entry, the half as the share")
        self._assert_rows_stacked(a, "after the drop")
        self.assertLessEqual(abs(a["row1"]["height"] - a["row2"]["height"]), 2, "at rowSplit 0.5 the rows are the same height: %r / %r" % (a["row1"], a["row2"]))
        self.assertLessEqual(abs(a["p2"]["top"] - a["row2"]["top"]), 1, "the new column is in the bottom row: %r" % a["p2"])
        self.assertLessEqual(abs(a["p2"]["width"] - a["row2"]["width"]), 1, "…and has the row to itself (no gutter ahead of it)")
        self.assertLessEqual(abs(a["p1"]["height"] - a["row1"]["height"]), 1, "the first pane now fills the top row only")
        self.assertLessEqual(abs(a["row2"]["top"] - gh["top"]), 1, "the row IS the rectangle: it starts where the rectangle did: %r vs %r" % (a["row2"], gh))
        self.assertLessEqual(abs(a["row2"]["height"] - gh["height"]), 1, "…and is as tall")
        self.assertEqual(a["nonce"], before["nonce"], "the first pane's document is the SAME document: nothing re-parented #f-chat")
        self.assertEqual(a["col2Tabs"], [SID_B]); self.assertNotIn(SID_B, a["col1Tabs"])
        self.assertEqual(a["zonesLeft"], 0, "every zone unmounted at the drop"); self.assertEqual(a["ghostAfter"]["display"], "none")

    def test_2_a_tab_dragged_to_the_bottom_row_s_right_edge_adds_a_column_beside_its_first(self):
        r = self._r()
        s, prev = r["s2"], r["s1"]["after"]
        rows = sorted(z["row"] for z in s["zones"] if "col-drop-edge" in z["cls"])
        self.assertEqual(rows, ["1", "2"], "an edge per row: %r" % s["zones"])
        e2 = next(z for z in s["zones"] if "col-drop-edge" in z["cls"] and z["row"] == "2")
        self.assertEqual(e2["parent"], "chat-pane-2", "the bottom row's edge rides its rightmost pane")
        self.assertLessEqual(abs(e2["left"] + e2["width"] - prev["p2"]["right"]), 1, "…at its right")
        gh = s["ghost"]
        self.assertEqual(gh["text"], "tests")
        self.assertLessEqual(abs(gh["top"] - prev["row2"]["top"]), 2, "the rectangle is at the bottom row's top: %r vs %r" % (gh, prev["row2"]))
        self.assertLessEqual(abs(gh["height"] - prev["row2"]["height"]), 2, "…and the bottom row's height")
        self.assertLessEqual(abs(gh["left"] - (prev["p2"]["left"] + prev["p2"]["width"] / 2)), 2, "…the right half of the bottom row's column")
        a = s["after"]
        self.assertEqual(a["frames"], ["f-chat", "f-chat-2", "f-chat-3"])
        self.assertEqual(json.loads(a["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B], "row": 2}, {"n": 3, "ids": [SID_C], "row": 2}], "rowSplit": 0.5})
        self._assert_rows_stacked(a, "one over two")
        self.assertLessEqual(abs(a["p3"]["top"] - a["row2"]["top"]), 1, "column 3 is in the bottom row")
        self.assertLessEqual(abs(a["p2"]["right"] + 7 - a["p3"]["left"]), 1, "…beside column 2 behind a 7 px gutter: %r / %r" % (a["p2"], a["p3"]))
        half = (prev["p2"]["width"] - 7) / 2
        self.assertLessEqual(abs(a["p2"]["width"] - half), SLACK_PX, "column 2 is about half its old width: %r" % a["p2"])
        self.assertLessEqual(abs(a["p3"]["width"] - half), SLACK_PX, "…and so is the new column")
        self.assertLessEqual(abs(a["p1"]["width"] - prev["p1"]["width"]), 1, "the top row's pane is untouched by a halving in the bottom row")
        self.assertEqual(a["nonce"], r["before"]["nonce"])

    def test_3_a_tab_dragged_to_the_top_row_s_right_edge_completes_two_over_two(self):
        r = self._r()
        s, prev = r["s3"], r["s2"]["after"]
        e1 = next(z for z in s["zones"] if "col-drop-edge" in z["cls"] and z["row"] == "1")
        self.assertEqual(e1["parent"], "chat-pane", "the top row's edge rides the first pane, its rightmost")
        gh = s["ghost"]
        self.assertEqual(gh["text"], "docs")
        self.assertLessEqual(abs(gh["top"] - prev["row1"]["top"]), 2); self.assertLessEqual(abs(gh["height"] - prev["row1"]["height"]), 2, "the rectangle at the top row's height, not the area's")
        a = s["after"]
        self.assertEqual(a["frames"], ["f-chat", "f-chat-4", "f-chat-2", "f-chat-3"], "the walk: the top row left to right, then the bottom row")
        self.assertEqual(json.loads(a["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B], "row": 2}, {"n": 3, "ids": [SID_C], "row": 2}, {"n": 4, "ids": [SID_D]}], "rowSplit": 0.5})
        self._assert_rows_stacked(a, "two over two")
        for k in ("p1", "p4"):
            self.assertLessEqual(abs(a[k]["top"] - a["row1"]["top"]), 1, k + " is in the top row"); self.assertLessEqual(abs(a[k]["height"] - a["row1"]["height"]), 1)
        for k in ("p2", "p3"):
            self.assertLessEqual(abs(a[k]["top"] - a["row2"]["top"]), 1, k + " is in the bottom row"); self.assertLessEqual(abs(a[k]["height"] - a["row2"]["height"]), 1)
        panes = [a[k] for k in ("p1", "p2", "p3", "p4")]
        for i in range(4):
            for j in range(i + 1, 4):
                self.assertFalse(self._overlap(panes[i], panes[j]), "panes %d and %d overlap: %r / %r" % (i, j, panes[i], panes[j]))
        self.assertLessEqual(abs(a["p1"]["right"] + 7 - a["p4"]["left"]), 1, "column 4 beside the first pane behind a 7 px gutter")
        half = (prev["p1"]["width"] - 7) / 2
        self.assertLessEqual(abs(a["p1"]["width"] - half), SLACK_PX); self.assertLessEqual(abs(a["p4"]["width"] - half), SLACK_PX, "the top row's halving")
        self.assertLessEqual(abs(a["p2"]["width"] - prev["p2"]["width"]), 1, "the bottom row is untouched by a halving in the top row")
        self.assertEqual(a["nonce"], r["before"]["nonce"])

    def test_4_at_the_cap_every_making_zone_is_refused_and_a_drop_changes_nothing(self):
        r = self._r()
        s, prev = r["s4"], r["s3"]["after"]
        making = [z for z in s["zones"] if "col-drop-edge" in z["cls"] or "col-drop-bottom" in z["cls"]]
        self.assertEqual(len(making), 3, "the two edges and the bottom zone: %r" % s["zones"])
        self.assertTrue(all(z["refused"] == "1" for z in making), "every making zone is mounted refused: %r" % making)
        self.assertEqual(sorted(s["ghost"]["cls"].split()), ["on", "refused"]); self.assertEqual(s["ghost"]["text"], "Four columns at most")
        a = s["after"]
        self.assertEqual(a["frames"], prev["frames"], "nothing opened"); self.assertEqual(a["cols"], prev["cols"], "nothing moved")
        self.assertIn(SID_E, a["col1Tabs"], "E is still the first column's")
        self.assertEqual(a["nonce"], r["before"]["nonce"])

    def test_5_the_row_gutter_changes_the_share_and_a_reload_restores_it(self):
        r = self._r()
        s = r["s5"]
        b, a = s["before"], s["after"]
        split = json.loads(a["cols"])["rowSplit"]
        self.assertGreater(split, 0.5, "dragged down: the top row's share grew: %r" % split)
        self.assertLess(split, 1)
        total = a["row1"]["height"] + a["row2"]["height"]
        self.assertLessEqual(abs(a["row1"]["height"] / total - split), 0.02, "the rows' heights follow the share: %r / %r vs %r" % (a["row1"], a["row2"], split))
        self.assertLessEqual(abs(a["row1"]["height"] - (b["row1"]["height"] + 120)), SLACK_PX, "about the drag: %r -> %r" % (b["row1"], a["row1"]))
        self.assertLessEqual(abs(a["area"]["height"] - b["area"]["height"]), 1, "the area itself did not move")
        self.assertEqual(a["nonce"], r["before"]["nonce"], "a gutter drag reloads nothing")
        rl = s["reloaded"]
        self.assertEqual(json.loads(rl["cols"]), json.loads(a["cols"]), "the store survives the reload as written")
        self.assertEqual(rl["frames"], ["f-chat", "f-chat-4", "f-chat-2", "f-chat-3"], "the arrangement comes back in its rows")
        self._assert_rows_stacked(rl, "after the reload")
        total2 = rl["row1"]["height"] + rl["row2"]["height"]
        self.assertLessEqual(abs(rl["row1"]["height"] / total2 - split), 0.02, "…at the dragged share")
        self.assertIsNone(rl["nonceGone"], "a reload is a fresh document (the nonce is gone), so the steps after it stamp anew")
        self.assertTrue(s["nonce2"].startswith("n-"))

    def test_6_the_bottom_row_folds_when_its_last_column_goes_and_the_store_is_the_shape_from_before_the_rows(self):
        r = self._r()
        s = r["s6"]
        onB = s["onB"]
        self.assertEqual(onB["frames"], ["f-chat", "f-chat-4", "f-chat-3"]); self.assertIsNone(onB["p2"])
        self._assert_rows_stacked(onB, "one member left in the bottom row")
        self.assertLessEqual(abs(onB["p3"]["width"] - onB["row2"]["width"]), 1, "the remaining column takes the row (its gutter went with the closed neighbour)")
        self.assertLessEqual(abs(onB["p3"]["left"] - onB["row2"]["left"]), 1)
        self.assertEqual(onB["nonce"], r["s5"]["nonce2"])
        onC = s["onC"]
        self.assertEqual(onC["cls"], "", "the row folded: no .rows")
        self.assertEqual(onC["row2"]["display"], "none"); self.assertEqual(onC["gvRows"]["display"], "none")
        self.assertLessEqual(abs(onC["row1"]["height"] - onC["area"]["height"]), 1, "the top row takes the full height: %r vs %r" % (onC["row1"], onC["area"]))
        self.assertLessEqual(abs(onC["p1"]["height"] - onC["area"]["height"]), 1); self.assertLessEqual(abs(onC["p4"]["height"] - onC["area"]["height"]), 1)
        self.assertEqual(onC["frames"], ["f-chat", "f-chat-4"])
        self.assertEqual(onC["bytes"], json.dumps({"v": 2, "cols": [{"n": 4, "ids": [SID_D]}]}, separators=(",", ":")),
                         "BYTE-IDENTICAL to what a browser that never stacked writes: no row, no rowSplit")
        self.assertEqual(onC["nonce"], r["s5"]["nonce2"], "the fold re-parents nothing either")

    def test_6b_a_lone_member_over_a_create_in_flight_is_refused_the_other_row_by_a_real_drop(self):
        r = self._r()
        s, prev = r["s6b"], r["s6"]["onC"]
        bottom = [z for z in s["zones"] if "col-drop-bottom" in z["cls"]]
        self.assertEqual(len(bottom), 1, "the bottom zone mounts for a session alone in the top row (a row below is a move): %r" % s["zones"])
        self.assertEqual(s["ghost"]["text"], "docs", "the rectangle shows as for any drag: the busy answer is the drop's, not the mount's")
        a = s["after"]
        self.assertEqual(a["cls"], "", "no bottom row opened"); self.assertEqual(a["row2"]["display"], "none")
        self.assertEqual(a["frames"], ["f-chat", "f-chat-4"], "no column made")
        self.assertEqual(a["bytes"], a["bytesBefore"], "the store's bytes are untouched: no empty entry persisted for another dashboard to close past the check")
        self.assertEqual(a["bytes"], prev["bytes"])
        self.assertEqual(a["col4Tabs"], [SID_D], "D is still in its column, the page holding its creation intact")
        self.assertNotIn(SID_D, a["col1Tabs"])
        self.assertEqual(a["nonce"], r["s5"]["nonce2"])

    def test_6c_the_rectangle_s_half_is_the_row_a_reopen_produces_after_a_resize_and_a_fold(self):
        r = self._r()
        s = r["s6c"]
        gh, area = s["ghost"], s["areaBefore"]
        want_h = (area["height"] - 7) / 2
        self.assertLessEqual(abs(gh["height"] - want_h), 1, "the rectangle is the box the half split opens: %r vs %r" % (gh, area))
        self.assertLessEqual(abs(gh["top"] - (area["bottom"] - want_h)), 1)
        a = s["after"]
        self._assert_rows_stacked(a, "reopened")
        self.assertEqual(json.loads(a["cols"])["rowSplit"], 0.5, "the row opens at the half, not at step 5's dragged share: %r" % a["cols"])
        # the row's box IS the rectangle's: the rectangle accounts for the 7 px gutter between the rows (review polish 2026-09-15)
        self.assertLessEqual(abs(a["row2"]["top"] - gh["top"]), 1, "the row starts where the rectangle did: %r vs %r" % (a["row2"], gh))
        self.assertLessEqual(abs(a["row2"]["height"] - gh["height"]), 1, "…and is as tall: %r vs %r" % (a["row2"], gh))
        self.assertLessEqual(abs(a["row1"]["height"] - a["row2"]["height"]), 2, "the two rows share the height evenly again")
        self.assertEqual(a["nonce"], r["s5"]["nonce2"])

    def test_7_back_to_one_column(self):
        r = self._r()
        s = r["s7"]
        self.assertEqual(s["frames"], ["f-chat"]); self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": []})
        self.assertEqual(s["cls"], ""); self.assertLessEqual(abs(s["p1"]["width"] - s["row1"]["width"]), 1, "the first pane has the row again")
        self.assertEqual(sorted(s["col1Tabs"]), sorted([SID_A, SID_B, SID_C, SID_D, SID_E]))
        self.assertEqual(s["nonce"], r["s5"]["nonce2"])

    @staticmethod
    def _pre_rows_weights(stored, cols, visible=("feed",), chat_shown=True, reveal=(), closed=()):
        """The weights the PRE-ROWS shell laid the row out by, and the panes of that row in order: the store's weights, its
        defaults for keys the store lacks, and for a restored column with no stored weight its fair grow as it was made — the
        mean of the panes then ON SCREEN (the first pane and the columns made before it while the chat pane was shown, plus the
        `visible` outer panes), in restoration order, 50 with nothing on screen. Then what happened while the chat pane stayed
        hidden: an outer pane the rail revealed (`reveal`) took the same fair grow over what was on screen and joined it; a
        column closed (`closed`) left the row and the store. With the chat pane hidden at the restore, the rail's Chat fair-grew
        the first pane over the visible outer panes (the chat panes still hidden) before showing it."""
        w = dict({"chat": 60, "fleet": 34, "feed": 40, "files": 40}, **stored)
        mean = lambda v: (sum(v) / len(v)) if v else 50
        made, vis = ["chat"], list(visible)
        for k in cols:
            if k not in w:
                w[k] = mean([w[j] for j in (made if chat_shown else []) + vis])
            made.append(k)
        for k in reveal:
            w[k] = mean([w[j] for j in (made if chat_shown else []) + vis])
            vis.append(k)
        for k in closed:
            made.remove(k)
            w.pop(k, None)
        if not chat_shown:
            w["chat"] = mean([w[j] for j in vis])
        return w, made + vis

    def test_8_a_pre_rows_pane_store_reloads_at_the_widths_the_pre_rows_shell_gave_it(self):
        r = self._r()
        cases = {"one": ([], {"chat": 700, "feed": 300}), "two": (["chat2"], {"chat": 640, "chat2": 400, "feed": 400}),
                 "three": (["chat2", "chat3"], {"chat": 500, "chat2": 300, "chat3": 200, "feed": 400}),
                 "four": (["chat2", "chat3", "chat4"], {"chat": 400, "chat2": 300, "chat3": 200, "chat4": 100, "feed": 300}),
                 # the missing-weight cases: chat2 resolves to (640 + 400) / 2 = 520, chat3 to (640 + 520 + 400) / 3 = 520; the empty
                 # object to the pre-rows defaults, both columns at (60 + 40) / 2 = 50
                 "missing-one": (["chat2"], {"chat": 640, "feed": 400}), "missing-two": (["chat2", "chat3"], {"chat": 640, "feed": 400}),
                 "empty": (["chat2", "chat3"], {}),
                 # the chat pane OFF at the restore: chat2 resolves over the visible feed alone, 400, and the rail's Chat fair-grows
                 # the first pane to the feed's 400 before showing it: three equal columns
                 "hidden-chat": (["chat2"], {"chat": 640, "feed": 400}),
                 # …and what changed while the upgrade waited (the fourth pass): the outline revealed (its fair grow the feed's 400, then
                 # the first pane's (400 + 400) / 2 at the show: four equal panes), or the column closed (two halves, no chat2)
                 "hidden-chat-reveal": (["chat2"], {"chat": 640, "feed": 400}), "hidden-chat-close": (["chat2"], {"chat": 640, "feed": 400})}
        extras = {"hidden-chat-reveal": {"reveal": ("fleet",)}, "hidden-chat-close": {"closed": ("chat2",)}}
        for name, (cols, stored) in cases.items():
            weights, items = self._pre_rows_weights(stored, cols, visible=("feed",), chat_shown=not name.startswith("hidden-chat"), **extras.get(name, {}))
            shown_cols = [k for k in cols if k in weights]
            s = r["s8"][name]
            for which in ("first", "second"):
                m = s[which]
                self.assertEqual(m["frames"], ["f-chat"] + ["f-chat-" + k[4:] for k in shown_cols], "%s/%s: the legacy columns are restored: %r" % (name, which, m["frames"]))
                # the pre-rows shell: one row of the panes on screen (chat, its columns, the outer panes shown), the row's width less a 7 px gutter per pair
                total = sum(weights[k] for k in items)
                avail = m["row"] - 7 * (len(items) - 1)
                want = {k: avail * weights[k] / total for k in items}
                got = {"chat": m["chat1"], "feed": m["feed"], "fleet": m["fleet"]}
                for k in shown_cols:
                    got[k] = m[k]
                for k in items:
                    self.assertIsNotNone(got[k], "%s/%s: %s is on screen" % (name, which, k))
                    self.assertLessEqual(abs(got[k] - want[k]), 1, "%s/%s: %s renders at %.2f px where the pre-rows shell gave %.2f (row %.1f): %r" % (name, which, k, got[k], want[k], m["row"], m))
            g = s["first"]["grow"]
            self.assertIn("chat1", g, "%s: the store carries chat1 once the upgrade has run" % name)
            self.assertLessEqual(abs(g["chat1"] - s["first"]["chat1"]), 1, "%s: the first pane's inner weight is its pixels" % name)
            self.assertLessEqual(abs(g["chat"] - s["first"]["area"]), 1, "%s: the area's weight is its pixels (the columns plus their gutters)" % name)
            self.assertEqual(s["second"]["grow"], g, "%s: the second boot is not an upgrade: the store is as the first left it" % name)

    def test_8b_with_the_chat_pane_off_the_upgrade_waits_for_the_pane_to_show(self):
        r = self._r()
        h = r["s8"]["hidden-chat"]["hidden"]
        self.assertEqual(h["frames"], ["f-chat", "f-chat-2"], "the column is restored while the chat pane is hidden")
        self.assertEqual(h["area"], 0, "…off screen"); self.assertEqual(h["chat2"], 0)
        g = h["grow"]
        self.assertNotIn("chat1", g, "the store keeps its pre-rows shape while the upgrade waits: no chat1")
        self.assertEqual(g["chat2"], 400, "the missing weight resolved over what was on screen — the feed alone — and persisted, as the old shell did")
        self.assertEqual(g["chat"], 640, "the first pane's weight untouched until the show")
        f = r["s8"]["hidden-chat"]["first"]
        self.assertLessEqual(abs(f["chat1"] - f["chat2"]), 1, "shown: equal columns (the first pane fair-grown to the feed's 400 at the show): %r" % f)
        self.assertLessEqual(abs(f["chat1"] - f["feed"]), 1)
        self.assertIn("chat1", f["grow"], "finalised at the show")

    def test_8c_what_changes_while_the_upgrade_waits_reaches_it(self):
        r = self._r()
        rv = r["s8"]["hidden-chat-reveal"]
        self.assertEqual(rv["pending"]["frames"], ["f-chat", "f-chat-2"]); self.assertNotIn("chat1", rv["pending"]["grow"], "still pending after the outline's reveal")
        self.assertEqual(rv["pending"]["grow"]["fleet"], 400, "the outline revealed from the rail took the pre-rows fair grow: the feed's 400 (the chat panes hidden), persisted")
        f = rv["first"]
        for k in ("chat2", "fleet", "feed"):
            self.assertLessEqual(abs(f["chat1"] - f[k]), 1, "shown: four equal panes, the first fair-grown to (400 + 400) / 2 at the show: %r" % f)
        self.assertLessEqual(abs(f["fleet"] - (f["row"] - 21) / 4), 1)
        self.assertEqual(f["grow"]["fleet"], rv["second"]["grow"]["fleet"], "a reload keeps the outline's live weight")
        cl = r["s8"]["hidden-chat-close"]
        self.assertEqual(cl["pending"]["frames"], ["f-chat"], "the palette's Close this column closed the restored column while hidden")
        self.assertNotIn("chat2", cl["pending"]["grow"], "…and its weight left the store at once"); self.assertNotIn("chat1", cl["pending"]["grow"], "still pending")
        f = cl["first"]
        self.assertLessEqual(abs(f["chat1"] - f["feed"]), 1, "shown: two halves: %r" % f); self.assertIsNone(f["chat2"])
        self.assertNotIn("chat2", f["grow"], "no phantom weight for a column that no longer exists"); self.assertIn("chat1", f["grow"])
        self.assertNotIn("chat2", cl["second"]["grow"])

    def test_8d_a_peer_s_upgrade_is_ingested_at_its_event_and_the_first_local_action_starts_from_it(self):
        r = self._r()
        s = r["s8d"]
        u = s["upgraded"]
        self.assertIn("chat1", u, "page A upgraded the shared store: %r" % u)
        i = s["ingested"]
        self.assertTrue(i["chatHidden"], "this page's chat pane stayed hidden: the ingest was the storage event's, not a show's")
        self.assertLessEqual(abs(i["chat1"] - u["chat1"]), 1e-6, "this page adopted the peer's first-pane weight at the event: %r vs %r" % (i, u))
        self.assertLessEqual(abs(i["chat"] - u["chat"]), 1e-6); self.assertLessEqual(abs(i["feed"] - u["feed"]), 1e-6)
        self.assertLessEqual(abs(s["fleetB"] - s["fleetC"]), 1e-6, "the first action after the ingest, the rail's Outline, fair-grows exactly as a fresh current page with the same panes on screen: %r vs %r" % (s["fleetB"], s["fleetC"]))
        self.assertNotEqual(s["fleetB"], 34, "…not the peer's stale weight for a pane it never showed")
        a = s["afterClose"]
        self.assertEqual(a["frames"], ["f-chat"], "the palette's Close this column closed the restored column")
        self.assertNotIn("chat2", a["grow"], "…and the store has no chat2: the write carried the deletion over the peer's store")
        self.assertIn("chat1", a["grow"], "…in its current shape"); self.assertFalse(s["legacyAfter"])

    def test_8e_a_drag_is_one_transaction_a_peer_s_write_mid_drag_waits_for_the_release_and_an_abandoned_drag_writes_nothing(self):
        r = self._r()
        s = r["s8e"]
        aw = s["afterWrite"]
        self.assertTrue(aw["drag"]); self.assertTrue(aw["legacy"], "the peer's write mid-drag is only noted: this page is still pending under the hand")
        for k in ("fleet", "feed", "files"):
            self.assertLessEqual(abs(aw["boxes"][k]["left"] - s["midBoxes"][k]["left"]), 0.5, "%s did not move under the hand: %r vs %r" % (k, aw["boxes"][k], s["midBoxes"][k]))
            self.assertLessEqual(abs(aw["boxes"][k]["width"] - s["midBoxes"][k]["width"]), 0.5)
        rl = s["released"]
        self.assertFalse(rl["legacy"], "the release ingested the peer's store"); self.assertFalse(rl["drag"])
        self.assertLessEqual(abs(rl["boxes"]["gvb"]["left"] - aw["ghostLeft"]), 1, "the divider landed where the line was: %r vs %r" % (rl["boxes"]["gvb"], aw["ghostLeft"]))
        self.assertLessEqual(abs(rl["boxes"]["fleet"]["width"] - (s["pre"]["fleet"]["width"] + 50)), SLACK_PX, "about the drag: %r -> %r" % (s["pre"]["fleet"], rl["boxes"]["fleet"]))
        self.assertLessEqual(abs(rl["boxes"]["files"]["width"] - s["pre"]["files"]["width"]), 1, "the files pane keeps its pixels: only the dragged divider moved")
        self.assertEqual(rl["store"]["files"], 900, "the store carries the peer's files weight"); self.assertIn("chat1", rl["store"])
        self.assertEqual(rl["store"]["chat1"], 331)
        ab = s["abandoned"]
        self.assertTrue(s["abandonMid"]["drag"]); self.assertEqual(s["abandonMid"]["ghost"], "block")
        self.assertEqual(ab["bytes"], s["beforeAbandon"]["bytes"], "a drag abandoned to the window's blur writes nothing"); self.assertEqual(ab["ghost"], "none")
        for k in ("fleet", "feed", "files", "gvb"):
            self.assertLessEqual(abs(ab["boxes"][k]["left"] - s["beforeAbandon"]["boxes"][k]["left"]), 0.5, "%s is back where it was: %r vs %r" % (k, ab["boxes"][k], s["beforeAbandon"]["boxes"][k]))
        self.assertEqual(s["afterStale"]["bytes"], ab["bytes"], "the stale release writes nothing"); self.assertLessEqual(abs(s["afterStale"]["boxes"]["gvb"]["left"] - ab["boxes"]["gvb"]["left"]), 0.5)

    def test_8f_a_peer_closing_a_column_under_a_live_drag_ends_the_drag_and_the_release_writes_nothing(self):
        r = self._r()
        s = r["s8f"]
        self.assertTrue(s["held"]["drag"], "A held the gutter")
        c = s["cancelled"]
        self.assertEqual(c["frames"], ["f-chat"], "B's close reached A"); self.assertFalse(c["legacy"], "A took B's upgraded store in at the drag's end")
        self.assertIn("chat1", c["store"], "the store is current-shaped…"); self.assertNotIn("chat2", c["store"], "…without the closed column")
        self.assertGreater(abs(c["boxes"]["gvb"]["left"] - s["held"]["ghostLeft"]), 5, "the drag's +50 never landed: the divider is not where the line was: %r vs %r" % (c["boxes"]["gvb"], s["held"]["ghostLeft"]))
        ratio_seen = c["boxes"]["fleet"]["width"] / c["boxes"]["feed"]["width"]; ratio_store = c["store"]["fleet"] / c["store"]["feed"]
        self.assertLessEqual(abs(ratio_seen - ratio_store) / ratio_store, 0.02, "the panes stand at the store's (the peer's) proportions: %r vs %r" % (c["boxes"], c["store"]))
        rl = s["released"]
        self.assertEqual(rl["bytes"], c["bytes"], "A's stale release wrote nothing"); self.assertFalse(rl["legacy"])
        for k in ("fleet", "feed", "files", "gvb"):
            self.assertLessEqual(abs(rl["boxes"][k]["left"] - c["boxes"][k]["left"]), 0.5, "%s did not move on the stale release" % k)
        m = s["mirror"]
        self.assertTrue(m["held"]["drag"]); self.assertEqual(m["frames"], ["f-chat"], "B's close reached A (mirror)")
        self.assertLessEqual(abs(m["cancelled"]["filesV"] - s["filesBefore"]["v"]), 1e-6, "the files pane's weight is untouched by the close under the drag: %r vs %r" % (m["cancelled"]["filesV"], s["filesBefore"]["v"]))
        self.assertLessEqual(abs(m["filesV"] - s["filesBefore"]["v"]), 1e-6, "…and by the stale release"); self.assertLessEqual(abs(m["store"]["files"] - s["filesBefore"]["store"]), 1e-6)
        self.assertEqual(m["bytes"], m["cancelled"]["bytes"], "the stale release wrote nothing (mirror)"); self.assertNotIn("chat2", m["store"])

    def test_9_the_whole_story_runs_in_under_two_and_a_half_minutes_and_left_its_screenshots(self):
        r = self._r()
        self.assertLess(r["ms"], 240_000, "the driver waits on conditions, never on fixed sleeps: %d ms" % r["ms"])
        self.assertEqual(r.get("shots"), ["stack-1-1-dark.png", "stack-1-1-light.png", "grid-2-2-dark.png", "grid-2-2-light.png"])
        for name in r["shots"]:
            self.assertTrue(os.path.getsize(os.path.join(SHOTS, name)) > 10_000, name + " is a real screenshot")


if __name__ == "__main__":
    unittest.main()
