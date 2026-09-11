#!/usr/bin/env python3
"""Chat split screen, the SERVED leg (the user 2026-09-08, who wanted several sessions open side by side
instead of tabbing between them). The dashboard shell holds N chat columns: column 1 is #chat-pane / #f-chat,
every later one a client-made twin (_LANDING_SPLIT_JS) around an iframe at /chat?col=N with its own tab
strip, state blob, socket and grow weight, slotted before #gv-a behind a chat|chat gutter.

The node-side test (tests/test_chat_split.py) drives the split script against a DOM stub; this one drives the
REAL page: a hermetic kernel serves the dashboard with TWO synthetic sessions, a headless browser opens it
once, and ONE driver run walks the whole story in order, each step landing in its own assertion here:
  1. a split on session B opens a second column that is WIDE (the review find: the first split opened 0px
     because the new column's missing grow averaged in as NaN), in the right row slot, with a finite
     --g-chat2 and the column set persisted;
  2. the new column shows B (the shell's hand-over focus), column 1 keeps its tab, and __rompChatTarget
     routes a session-focus to the column showing it — or, for a session nobody shows, to the column the
     user last clicked in;
  3. a split opened ON a session column 1 already shows still takes it (own:true beats the "a column already
     shows it" arbitration), and closing that column drops it from the persisted set;
  4. dragging the chat|chat gutter moves width between the two columns and persists column 2's grow;
  5. column 2's new-session picker lifts ITS iframe and pane (.lifted), never column 1's, and unlifts on
     toggle;
  6. a reload brings both columns back, column 2 on B again, at the width the drag left it;
  7. closing column 2 leaves one column and an empty persisted set.
Skips LOUDLY when the extension deps or a playwright browser are absent (CI installs none). Synthetic only:
placeholder sids, invented notes-api prompt text, no real session data."""
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# the kernel refuses to boot with a retired key variable or a 1Password name in its environment (kernel/credentials.py
# check_boot_environment): the lab's kernel env is scrubbed by the kernel's own rule, read from the module itself
from romp_load import load_source
from tests.dist_copy import copy_dist
_cred = load_source("romp_credentials_served", os.path.join(ROOT, "kernel", "credentials.py"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

SID_A = "11111111-2222-4333-8444-000000000301"   # "web": column 1's session
SID_B = "11111111-2222-4333-8444-000000000302"   # "api": the session the split opens on
SID_X = "11111111-2222-4333-8444-000000000999"   # a session no column shows
DRAG_PX = 200
SLACK_PX = 40


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(sid, tag, cwd, pairs):
    """`pairs` CLOSED user/assistant turns for `sid` (an OPEN turn would invite the boot reconcile to resume
    it); `tag` keeps the two sessions' message uuids apart."""
    out, parent, t = [], None, 1_700_000_000
    filler = ["The ranking pass reads its weights from the notes-api config now.",
              "Tokenizer edge cases (hyphens, quotes) are covered by the new fixture set.",
              "Index rebuild time is dominated by the stemmer; caching its table halves it.",
              "The pagination cursor survives a re-sort because it encodes the sort key too."]
    for i in range(pairs):
        u = "11111111-2222-4333-8444-%02x00000a%04x" % (tag, i)
        a = "11111111-2222-4333-8444-%02x00000b%04x" % (tag, i)
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": sid, "cwd": cwd,
                    "message": {"role": "user", "content": "please keep going with the search module notes (part %d)" % (i + 1)}})
        body = "\n\n".join(["Note %d." % (i + 1)] + [filler[(i + k) % len(filler)] for k in range(3)])
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": sid, "cwd": cwd,
                    "message": {"id": "msg_lab_%d_%04d" % (tag, i), "type": "message", "role": "assistant", "model": "claude-sonnet-5",
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
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const out = { t0: Date.now() };
const die = async (why) => {
  out.ms = Date.now() - out.t0;
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};
const T = 15000;
const waitFn = async (fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await die(why + " (" + String(e).split("\n")[0] + ")"); });
// a column's tab strip holds every one of `sids`
const waitTabs = (fid, sids) => waitFn(([fid, sids]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id);
  return sids.every((s) => ids.includes(s));
}, [fid, sids], fid + " never showed tabs " + sids.join(","));
// a column's ACTIVE tab is `sid`
const waitActive = (fid, sid) => waitFn(([fid, sid]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid;
}, [fid, sid], fid + " never activated " + sid);
const waitBootGone = () => waitFn(() => !document.getElementById("romp-boot"), null, "boot splash never cleared");
const waitFocused = (fid) => waitFn((fid) => window.__rompFocusedChatId && window.__rompFocusedChatId() === fid, fid, fid + " never took the focus ring");
const activeIn = (fid) => page.evaluate((fid) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return t ? t.dataset.id : null;
}, fid);
const targetOf = (sid) => page.evaluate((sid) => { const f = window.__rompChatTarget(sid); return f ? f.id : null; }, sid);
const width = (id) => page.evaluate((id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; }, id);
const shell = () => page.evaluate(() => ({
  frameIds: window.__rompChatFrameIds(), cols: localStorage.getItem("romp-chat-cols"),
  rowKids: Array.from(document.querySelector(".row").children).map((e) => e.id || e.className),
  grow: JSON.parse(localStorage.getItem("romp-pane-grow") || "null"),
  lifted: Array.from(document.querySelectorAll(".lifted")).map((e) => e.id), pickerOpen: document.body.classList.contains("picker-open"),
}));
// the page rect of an element INSIDE a column, for page.mouse (the iframe's offset plus the element's own)
const rectIn = (fid, sel) => page.evaluate(([fid, sel]) => {
  const f = document.getElementById(fid); const fr = f.getBoundingClientRect(); const el = f.contentDocument.querySelector(sel);
  const r = el.getBoundingClientRect(); return { x: fr.left + r.left, y: fr.top + r.top, w: r.width, h: r.height };
}, [fid, sel]);
const clickIn = async (fid, sel) => { const r = await rectIn(fid, sel); await page.mouse.click(r.x + r.w / 2, r.y + Math.min(r.h / 2, 120)); };

// per-tab hot keys (2026-09-10): seed the shared bindings store before the first load — the shell registers
// "Switch to api" from romp:tabkeys at boot, and the chords ride romp:keys like every other binding
await page.addInitScript(([sidB]) => {
  if (!localStorage.getItem("romp:tabkeys")) {
    localStorage.setItem("romp:tabkeys", JSON.stringify({ [sidB]: "api" }));
    localStorage.setItem("romp:keys", JSON.stringify({ ["session.hotkey." + sidB]: "Alt+Shift+K", "chat.nextSplit": "Alt+Shift+J", "chat.prevSplit": "Alt+Shift+H" }));
  }
}, [cfg.sidB]);
// ---- load: both sessions are tabs in column 1; column 1 shows A ----
await page.goto(cfg.url);
await waitTabs("f-chat", [cfg.sidA, cfg.sidB]);
await waitBootGone();
if ((await activeIn("f-chat")) !== cfg.sidA) {
  const fr = await (await page.$("#f-chat")).contentFrame();
  await fr.locator('#tabs .tab[data-id="' + cfg.sidA + '"]').click();
  await waitActive("f-chat", cfg.sidA);
}
out.col1Before = await activeIn("f-chat");

// ---- 1. split on B: a second column, wide, before gv-a, persisted ----
out.s1 = await page.evaluate((sidB) => {
  const f = window.__rompSplitChat(sidB);
  const row = document.querySelector(".row"), kids = Array.from(row.children).map((e) => e.id);
  const w = (id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; };
  return { frameId: f && f.id, tag: f && f.tagName, src: f && f.getAttribute("src"), paneCol: (document.getElementById("chat-pane-2") || {}).getAttribute?.("data-col"),
           order: kids, gIdx: kids.indexOf("gv-chat-2"), pIdx: kids.indexOf("chat-pane-2"), aIdx: kids.indexOf("gv-a"),
           pane1W: w("chat-pane"), pane2W: w("chat-pane-2"), gChat2Inline: row.style.getPropertyValue("--g-chat2"),
           gChat2: getComputedStyle(row).getPropertyValue("--g-chat2"), cols: localStorage.getItem("romp-chat-cols") };
}, cfg.sidB);

// ---- 2. the new column shows B; targets route by the column showing the session, else the last-clicked ----
await waitTabs("f-chat-2", [cfg.sidA, cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
out.s2 = { col2Active: await activeIn("f-chat-2"), col1After: await activeIn("f-chat"),
           targetB: await targetOf(cfg.sidB), targetA: await targetOf(cfg.sidA) };
await clickIn("f-chat-2", "#content");
await waitFocused("f-chat-2");
out.s2.unknownAfterCol2 = await targetOf(cfg.sidX);
await clickIn("f-chat", "#content");
await waitFocused("f-chat");
out.s2.unknownAfterCol1 = await targetOf(cfg.sidX);
out.s2.col1AfterClicks = await activeIn("f-chat");

// ---- 2c. per-tab hot keys: the badge on B's tab; Alt+Shift+K goes to the column showing B; Alt+Shift+J cycles the
// columns; the tab menu's Hot key… opens the shell's recorder on that one row, a built-in chord is refused on the row,
// the pressed chord binds and the focus comes back to the asking pane; Remove prunes the command, Esc keeps it ----
const badgeOf = (fid, sid) => page.evaluate(([fid, sid]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector('#tabs .tab[data-id="' + sid + '"]'); const k = t && t.querySelector(".tab-key");
  return k ? { text: k.textContent, title: k.title, aria: k.getAttribute("aria-label") } : null;
}, [fid, sid]);
// where the keyboard is: the shell's active element (an iframe's id) and, inside that column, the element's id or tag
const whereFocus = (fid) => page.evaluate((fid) => {
  const d = document.getElementById(fid).contentDocument; const a = d.activeElement;
  return { shell: document.activeElement ? document.activeElement.id : null, pane: a ? (a.id || a.tagName) : null };
}, fid);
// open a tab's right-click menu in a column and pick the item whose label reads `label`
const pickTabMenu = async (fid, sid, label) => {
  const fr = await (await page.$("#" + fid)).contentFrame();
  await fr.locator('#tabs .tab[data-id="' + sid + '"]').click({ button: "right" });
  await waitFn((fid) => !!document.getElementById(fid).contentDocument.querySelector(".ctx-menu"), fid, fid + "'s tab menu never opened");
  const labels = await page.evaluate((fid) => Array.from(document.getElementById(fid).contentDocument.querySelectorAll(".ctx-menu .ctx-item-label, .ctx-menu .ctx-item")).map((l) => l.textContent), fid);
  if (!labels.includes(label)) await die(fid + "'s tab menu never offered " + label + " — it offered: " + JSON.stringify(labels));
  await page.evaluate(([fid, label]) => { const d = document.getElementById(fid).contentDocument;
    Array.from(d.querySelectorAll(".ctx-menu .ctx-item")).find((i) => { const l = i.querySelector(".ctx-item-label"); return l && l.textContent === label; }).click(); }, [fid, label]);
  return labels;
};
const dialogShown = () => waitFn(() => { const b = document.getElementById("rkeys-back"); return !!b && !b.hidden; }, null, "the recorder never opened");
const dialogGone = (why) => waitFn(() => { const b = document.getElementById("rkeys-back"); return !!b && b.hidden; }, null, why);
const hotkeyRows = () => page.evaluate(() => { window.__rompKeysOpen(); const rows = Array.from(document.querySelectorAll("#rkeys-list .rkeys-title")).map((t) => t.textContent).filter((t) => t.startsWith("Switch to ")); window.__rompKeysClose(); return rows; });
const stores = () => page.evaluate(() => ({ tabkeys: JSON.parse(localStorage.getItem("romp:tabkeys") || "{}"), keys: JSON.parse(localStorage.getItem("romp:keys") || "{}") }));
out.hk = { mac: await page.evaluate(() => /Mac|iP(hone|ad|od)/.test(navigator.platform || "")) };
out.hk.badgeB = await badgeOf("f-chat", cfg.sidB);          // column 1 shows A; B's tab there wears the badge
out.hk.badgeA = await badgeOf("f-chat", cfg.sidA);          // A has no hot key
await clickIn("f-chat", "#content"); await waitFocused("f-chat");
out.hk.registered = await page.evaluate(([sidB]) => window.__rompKeyHint("session.hotkey." + sidB), [cfg.sidB]);   // the shell knows the command and its chord
out.hk.pressedFrom = await whereFocus("f-chat");             // the press lands in column 1's document, outside any text box
await page.keyboard.press("Alt+Shift+K");                   // from column 1: B is showing in column 2 → the focus goes THERE
await waitFocused("f-chat-2");
out.hk.afterKey = { focused: await page.evaluate(() => window.__rompFocusedChatId()), col1: await activeIn("f-chat"), col2: await activeIn("f-chat-2") };
await page.keyboard.press("Alt+Shift+J");                   // cycle: column 2 → column 1
await waitFocused("f-chat");
out.hk.afterCycle1 = await page.evaluate(() => window.__rompFocusedChatId());
await page.keyboard.press("Alt+Shift+J");                   // and around again
await waitFocused("f-chat-2");
out.hk.afterCycle2 = await page.evaluate(() => window.__rompFocusedChatId());
// the real door: right-click A's tab in column 1, pick Hot key… → the pane asks the shell, whose recorder opens on that one row
out.hk.menu = await pickTabMenu("f-chat", cfg.sidA, "Hot key…");
await dialogShown();
out.hk.recorder = await page.evaluate(() => ({ heading: document.getElementById("rkeys-h").textContent, rows: document.querySelectorAll("#rkeys-list .rkeys-row").length,
  recording: document.querySelectorAll("#rkeys-list .rkeys-row.recording").length, filterHidden: document.getElementById("rkeys-in").hidden,
  builtInHidden: document.getElementById("rkeys-fixed").hidden }));
await page.keyboard.press("Alt+ArrowLeft");                 // a chord the pane-focus script owns: refused, and said
await waitFn(() => { const h = document.querySelector("#rkeys-list .rkeys-conflict"); return !!h && /built in/.test(h.textContent); }, null, "Alt+ArrowLeft was not refused on the row");
out.hk.refused = await page.evaluate(() => document.querySelector("#rkeys-list .rkeys-conflict").textContent);
await page.keyboard.press("Alt+Shift+L");                   // wherever the focus went, the chord records and the dialog closes
await dialogGone("the recorder never closed on the chord");
await waitFn(([fid, sid]) => { const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector('#tabs .tab[data-id="' + sid + '"]'); return !!(t && t.querySelector(".tab-key")); }, ["f-chat-2", cfg.sidA], "A's badge never painted");
out.hk.badgeAAfter = await badgeOf("f-chat-2", cfg.sidA);
out.hk.keysStore = (await stores()).keys;
await waitFn(() => document.activeElement && document.activeElement.id === "f-chat" && document.getElementById("f-chat").contentDocument.activeElement.id === "composer-input", null, "the focus never came back to the asking pane's composer");
out.hk.afterRecord = await whereFocus("f-chat");
// Remove (from column 2, where A is not even the active tab): the one "Update hot key…" row opens the recorder, whose
// Remove button makes the "" write; the shell drops the command, the set entry and the override — and both columns' badges go
out.hk.updateMenu = await pickTabMenu("f-chat-2", cfg.sidA, "Update hot key…");
await dialogShown();
await page.evaluate(() => { const b = Array.from(document.querySelectorAll("#rkeys-list .rkeys-row.recording .rkeys-act")).find((x) => x.textContent === "Remove"); if (!b) throw new Error("no Remove button on the bound row"); b.click(); });
await dialogGone("the recorder never closed on Remove");
await waitFn(([sid]) => { const has = (fid) => { const t = document.getElementById(fid).contentDocument.querySelector('#tabs .tab[data-id="' + sid + '"]'); return !!(t && t.querySelector(".tab-key")); };
  return !has("f-chat") && !has("f-chat-2") && !(sid in JSON.parse(localStorage.getItem("romp:tabkeys") || "{}")); }, [cfg.sidA], "Remove never took A's badge and set entry away");
out.hk.removed = { badgeCol1: await badgeOf("f-chat", cfg.sidA), badgeCol2: await badgeOf("f-chat-2", cfg.sidA), ...(await stores()), dialogRows: await hotkeyRows() };
// Esc on a re-recording keeps what was there: B's chord and its command stay, and the focus comes back
await pickTabMenu("f-chat", cfg.sidB, "Update hot key…");
await dialogShown();
await page.keyboard.press("Escape");
await dialogGone("Esc never closed the solo recorder");
await waitFn(() => document.activeElement && document.activeElement.id === "f-chat" && document.getElementById("f-chat").contentDocument.activeElement.id === "composer-input", null, "the focus never came back after Esc");
out.hk.cancelled = { afterCancel: await whereFocus("f-chat"), badgeB: await badgeOf("f-chat", cfg.sidB), ...(await stores()), dialogRows: await hotkeyRows() };   // the focus first: listing the dialog's rows opens (and closes) it

// ---- 2d. pin a tab (the user 2026-09-10): B's tab, pinned from its menu in column 1, wears the pushpin and is not draggable —
// in every column, since the pinned set is this browser's; Unpin from column 2's menu takes it back ----
const pinState = (fid, sid) => page.evaluate(([fid, sid]) => { const t = document.getElementById(fid).contentDocument.querySelector('#tabs .tab[data-id="' + sid + '"]');
  return t ? { pinned: t.classList.contains("pinned"), draggable: t.draggable, pin: !!t.querySelector(".tab-pin svg") } : null; }, [fid, sid]);
const pinnedIn = (fids, sid, want) => waitFn(([fids, sid, want]) => fids.every((fid) => { const t = document.getElementById(fid).contentDocument.querySelector('#tabs .tab[data-id="' + sid + '"]');
  return !!t && t.classList.contains("pinned") === want; }), [fids, sid, want], "B's tab never showed pinned=" + want + " in both columns");
const stripOrder = (fid) => page.evaluate((fid) => Array.from(document.getElementById(fid).contentDocument.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id), fid);
const rewriteArrangement = (ids) => page.evaluate((ids) => document.getElementById("f-chat").contentWindow.__rompWriteOrder(ids), ids);
const orderBefore = { col1: await stripOrder("f-chat"), col2: await stripOrder("f-chat-2") };   // whatever the kernel's order is: the pin holds THAT slot
await pickTabMenu("f-chat", cfg.sidB, "Pin tab");
await pinnedIn(["f-chat", "f-chat-2"], cfg.sidB, true);
out.pin = { col1: await pinState("f-chat", cfg.sidB), col2: await pinState("f-chat-2", cfg.sidB), a: await pinState("f-chat", cfg.sidA),
            store: JSON.parse(await page.evaluate(() => localStorage.getItem("romp:tabpins"))), orderBefore };
// the pinned tab keeps its SLOT whatever rewrites the order: the browser's arrangement is rewritten to the reverse (what a
// drag elsewhere, a merge or a reload could do) — every column re-derives its order from it and puts B back at its slot
const reversed = orderBefore.col1.slice().reverse();
await rewriteArrangement(reversed);
await waitFn(() => JSON.parse(localStorage.getItem("romp:vieworder") || "[]").length === 2, null, "the arrangement write never landed");
// give every column a chance to consume the rewrite (a kernel push re-derives the order too), then read the strips
await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
out.pin.orderHeld = { col1: await stripOrder("f-chat"), col2: await stripOrder("f-chat-2"), arrangement: JSON.parse(await page.evaluate(() => localStorage.getItem("romp:vieworder"))) };
out.pin.menu = await pickTabMenu("f-chat-2", cfg.sidB, "Unpin tab");
await pinnedIn(["f-chat", "f-chat-2"], cfg.sidB, false);
out.pin.after = { col1: await pinState("f-chat", cfg.sidB), col2: await pinState("f-chat-2", cfg.sidB), store: JSON.parse(await page.evaluate(() => localStorage.getItem("romp:tabpins"))) };
// with the pin gone the same rewrite takes effect in every column (the pin was what held the slot); then it is restored.
// The arrangement still holds the reverse from above (the hold never writes it back), and a storage value written
// unchanged raises no event in the sibling column — so restore first, then rewrite: two real changes
const stripsShow = (want, why) => waitFn((want) => ["f-chat", "f-chat-2"].every((fid) => JSON.stringify(Array.from(document.getElementById(fid).contentDocument.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id)) === JSON.stringify(want)), want, why);
await rewriteArrangement(orderBefore.col1);
await waitFn((want) => localStorage.getItem("romp:vieworder") === JSON.stringify(want), orderBefore.col1, "the restore never landed");
await rewriteArrangement(reversed);
await stripsShow(reversed, "unpinned, the rewritten arrangement never showed in both columns");
out.pin.orderReleased = { col1: await stripOrder("f-chat"), col2: await stripOrder("f-chat-2") };
await rewriteArrangement(orderBefore.col1);
await stripsShow(orderBefore.col1, "the arrangement never came back to where it started");

// ---- 2e. the session bell from the keyboard (the user 2026-09-11): the palette's "Toggle notifications for this
// session" flips the ACTIVE session's bell in the focused column — the same override the tab menu's bell row writes —
// a toast says so, the menu's row reads the other way in EVERY column (the other learns from the kernel's push), and
// the kernel's flags file carries the override ----
const peekTabMenu = async (fid, sid) => {   // the tab menu's labels, read and closed without picking anything
  const fr = await (await page.$("#" + fid)).contentFrame();
  await fr.locator('#tabs .tab[data-id="' + sid + '"]').click({ button: "right" });
  await waitFn((fid) => !!document.getElementById(fid).contentDocument.querySelector(".ctx-menu"), fid, fid + "'s tab menu never opened");
  const labels = await page.evaluate((fid) => Array.from(document.getElementById(fid).contentDocument.querySelectorAll(".ctx-menu .ctx-item-label")).map((l) => l.textContent), fid);
  await page.keyboard.press("Escape");
  await waitFn((fid) => !document.getElementById(fid).contentDocument.querySelector(".ctx-menu"), fid, fid + "'s tab menu never closed on Escape");
  return labels;
};
const bellLabel = async (fid, sid) => (await peekTabMenu(fid, sid)).find((l) => l === "Notify me" || l === "Stop notifying") || null;
const bellReads = async (fid, sid, want, why) => {   // the menu is a snapshot: re-peek until the kernel's push has landed the flag
  for (let i = 0; i < 40; i++) { if ((await bellLabel(fid, sid)) === want) return want; await page.waitForTimeout(250); }
  await die(why);
};
const runPalette = async (fid, query) => {   // the chord from inside a column's document; the shell's palette answers
  await clickIn(fid, "#content"); await waitFocused(fid);
  await page.keyboard.press("Control+P");
  await waitFn(() => { const b = document.getElementById("rpal-back"); return !!b && !b.hidden; }, null, "the palette never opened on Ctrl+P");
  await page.keyboard.type(query);
  await waitFn(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return !!r && /Toggle notifications for this session/.test(r.textContent || ""); }, null, "the palette never matched the bell command");
  await page.keyboard.press("Enter");
  await waitFn(() => { const b = document.getElementById("rpal-back"); return !!b && b.hidden; }, null, "the palette never closed on Enter");
};
const toasts = (fid) => page.evaluate((fid) => Array.from(document.getElementById(fid).contentDocument.querySelectorAll(".warn-toast-msg")).map((t) => t.textContent), fid);
out.bell = { before: await bellLabel("f-chat", cfg.sidA), beforeCol2: await bellLabel("f-chat-2", cfg.sidA) };
await runPalette("f-chat", "toggle notif");
await waitFn((fid) => Array.from(document.getElementById(fid).contentDocument.querySelectorAll(".warn-toast-msg")).some((t) => /Notifications enabled for/.test(t.textContent || "")), "f-chat", "no toast said the bell went on");
out.bell.toastOn = await toasts("f-chat");
out.bell.afterOn = await bellLabel("f-chat", cfg.sidA);                                       // this column: at once
out.bell.col2AfterOn = await bellReads("f-chat-2", cfg.sidA, "Stop notifying", "column 2 never learned the bell went on");   // the other: from the kernel
await runPalette("f-chat", "toggle notif");
await waitFn((fid) => Array.from(document.getElementById(fid).contentDocument.querySelectorAll(".warn-toast-msg")).some((t) => /Notifications disabled for/.test(t.textContent || "")), "f-chat", "no toast said the bell went off");
out.bell.toastOff = await toasts("f-chat");
out.bell.afterOff = await bellLabel("f-chat", cfg.sidA);
out.bell.col2AfterOff = await bellReads("f-chat-2", cfg.sidA, "Notify me", "column 2 never learned the bell went off");

// ---- 3. a split ON the session column 1 shows: the third column takes it anyway; close it ----
out.s3 = await page.evaluate((sidA) => { const f = window.__rompSplitChat(sidA); return { frameId: f && f.id, cols: localStorage.getItem("romp-chat-cols") }; }, cfg.sidA);
await waitTabs("f-chat-3", [cfg.sidA, cfg.sidB]);
await waitActive("f-chat-3", cfg.sidA);
out.s3.col3Active = await activeIn("f-chat-3");
out.s3.col1Still = await activeIn("f-chat");
out.s3.targetAWithThree = await targetOf(cfg.sidA);   // column 1 shows A too and comes first in the row
// three columns: next, next, next wraps to the first; previous wraps back to the last (the two commands are distinct)
await clickIn("f-chat", "#content"); await waitFocused("f-chat");
out.s3.cycle = [];
for (const [key, fid] of [["Alt+Shift+J", "f-chat-2"], ["Alt+Shift+J", "f-chat-3"], ["Alt+Shift+J", "f-chat"], ["Alt+Shift+H", "f-chat-3"]]) {
  await page.keyboard.press(key); await waitFocused(fid); out.s3.cycle.push(await page.evaluate(() => window.__rompFocusedChatId()));
}
await page.evaluate(() => window.__rompCloseSplit(3));
out.s3.after = await shell();
out.s3.pane3Gone = await page.evaluate(() => !document.getElementById("chat-pane-3") && !document.getElementById("gv-chat-3") && !document.getElementById("f-chat-3"));

// ---- 4. drag the chat|chat gutter right: column 1 grows, column 2 shrinks, the grow persists ----
out.s4 = { before1: await width("chat-pane"), before2: await width("chat-pane-2") };
const g = await (await page.$("#gv-chat-2")).boundingBox();
await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
await page.mouse.down();
out.s4.dragClass = await page.evaluate(() => document.body.classList.contains("drag"));
// the grab normalises every shown pane's grow to its px width: what the store holds the instant after mousedown
out.s4.growAtGrab = await page.evaluate(() => { const st = document.querySelector(".row").style; return { chat: parseFloat(st.getPropertyValue("--g-chat")), chat2: parseFloat(st.getPropertyValue("--g-chat2")), feed: parseFloat(st.getPropertyValue("--g-feed")) }; });
await page.mouse.move(g.x + g.width / 2 + cfg.dragPx, g.y + g.height / 2, { steps: 10 });
await page.mouse.up();
out.s4.after1 = await width("chat-pane"); out.s4.after2 = await width("chat-pane-2");
out.s4.dragClassAfter = await page.evaluate(() => document.body.classList.contains("drag"));
out.s4.grow = (await shell()).grow;

// ---- 5. the picker in column 2 lifts THAT column only; toggling it closed unlifts ----
await page.evaluate(() => document.getElementById("f-chat-2").contentWindow.postMessage({ type: "openPicker" }, "*"));
await waitFn(() => document.body.classList.contains("picker-open"), null, "the shell never lifted for column 2's picker");
out.s5 = { open: await shell() };
out.s5.open.pane2Lifted = await page.evaluate(() => document.getElementById("chat-pane-2").classList.contains("lifted"));
out.s5.open.frame2Lifted = await page.evaluate(() => document.getElementById("f-chat-2").classList.contains("lifted"));
out.s5.open.frame1Lifted = await page.evaluate(() => document.getElementById("f-chat").classList.contains("lifted"));
out.s5.open.pickerShown = await page.evaluate(() => { const d = document.getElementById("f-chat-2").contentDocument; const p = d && d.getElementById("picker"); return !!p && p.style.display !== "none"; });
await page.evaluate(() => document.getElementById("f-chat-2").contentWindow.postMessage({ type: "openPicker", toggle: true }, "*"));
await waitFn(() => !document.body.classList.contains("picker-open"), null, "the shell never released the lift");
out.s5.closed = await shell();

// ---- 6. reload: both columns return, column 2 on B, at the dragged width ----
await page.reload();
await waitTabs("f-chat", [cfg.sidA, cfg.sidB]);
await waitTabs("f-chat-2", [cfg.sidA, cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitBootGone();
out.s6 = await shell();
out.s6.col2Active = await activeIn("f-chat-2"); out.s6.col1Active = await activeIn("f-chat");
out.s6.pane1W = await width("chat-pane"); out.s6.pane2W = await width("chat-pane-2");

// ---- 7. close column 2: one column left, nothing persisted ----
await page.evaluate(() => window.__rompCloseSplit(2));
out.s7 = await shell();
out.s7.pane2Gone = await page.evaluate(() => !document.getElementById("chat-pane-2") && !document.getElementById("gv-chat-2") && !document.getElementById("f-chat-2"));
// one column, on A: B's hot key has no column showing B, so it switches the column last worked in TO B
await clickIn("f-chat", "#content"); await waitFocused("f-chat");
out.s7.beforeKey = await activeIn("f-chat");
await page.keyboard.press("Alt+Shift+K");
await waitActive("f-chat", cfg.sidB);
out.s7.switched = await activeIn("f-chat");
out.ms = Date.now() - out.t0;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedChatSplit(unittest.TestCase):
    """One kernel, one page, one driver run in setUpClass; each method asserts one step of the shared result."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="chat-split-")
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
        # two synthetic SDK sessions so the chat page has two tabs (the test_dashboard_reload_served lab shape,
        # doubled); their transcripts hold only CLOSED turns, so the boot reconcile never resumes either and no
        # CLI is ever spawned
        for sid, name, tag in ((SID_A, "web", 1), (SID_B, "api", 2)):
            Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, tag, cwd, 20))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        cls.port = _free_port()
        cls.token = "testtok-chatsplit"
        cls.env = dict(os.environ,
                       XDG_STATE_HOME=os.path.join(cls.lab, "xdg"),
                       CLAUDE_CONFIG_DIR=claude,
                       ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1",
                       ROMP_SERVE_TOKEN=cls.token, ROMP_KERNEL_PORT=str(cls.port),
                       ROMP_DIST_DIR=dist, ROMP_MODEL_CATALOG="off",
                       ROMP_TMUX_SOCKET="romp-chatsplit-%d" % cls.port,
                       # a postal bus of its own that is never started (the trio kernel_env gives every lab kernel):
                       # the kernel's boot-time ensure must never take the machine's fixed bus port (tests/test_hermetic_kernel_postal.py)
                       ROMP_POSTAL_PORT=str(_free_port()), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1")
        cls.env.pop("ROMP_STATE_DIR", None)
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
                       "sidA": SID_A, "sidB": SID_B, "sidX": SID_X, "dragPx": DRAG_PX}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        t0 = time.monotonic()
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
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
        subprocess.run(["tmux", "-L", getattr(cls, "env", {}).get("ROMP_TMUX_SOCKET", ""), "kill-server"], capture_output=True)
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

    def test_1_a_split_on_a_session_opens_a_wide_second_column_before_gv_a_and_persists(self):
        s = self._r()["s1"]
        self.assertEqual(s["tag"], "IFRAME", "__rompSplitChat returns the new column's iframe: %r" % s)
        self.assertEqual(s["frameId"], "f-chat-2")
        self.assertEqual(s["src"], "/chat?col=2", "every later column is /chat?col=N")
        self.assertEqual(s["paneCol"], "2")
        self.assertEqual(s["cols"], "[2]", "the open column set persists per browser")
        # row order: … chat-pane, gv-chat-2, chat-pane-2, gv-a …
        self.assertGreaterEqual(s["gIdx"], 0, s["order"])
        self.assertEqual(s["pIdx"], s["gIdx"] + 1, "the gutter sits directly ahead of its column: %r" % s["order"])
        self.assertLess(s["pIdx"], s["aIdx"], "the new column slots before gv-a: %r" % s["order"])
        self.assertEqual(s["order"][s["gIdx"] - 1], "chat-pane", "…and right after the first column: %r" % s["order"])
        # the review find (2026-09-08): the first split opened 0px wide because the new column's missing grow
        # averaged in as NaN. Both columns must be real widths the moment the split opens.
        self.assertGreater(s["pane1W"], 150, "column 1 keeps a real width: %r" % s)
        self.assertGreater(s["pane2W"], 150, "the new column opens at a real width, never a sliver: %r" % s)
        g = float(s["gChat2Inline"] or s["gChat2"] or "nan")
        self.assertTrue(g == g and abs(g) != float("inf"), "--g-chat2 on .row is a finite number: %r" % s)
        self.assertGreater(g, 0)

    def test_2_the_new_column_shows_its_session_and_focus_targets_route_by_column(self):
        r = self._r()
        s = r["s2"]
        self.assertEqual(r["col1Before"], SID_A, "the story starts with column 1 on A")
        self.assertEqual(s["col2Active"], SID_B, "the shell's hand-over focus lands the new column on B: %r" % s)
        self.assertEqual(s["col1After"], SID_A, "column 1's tab is untouched by the split: %r" % s)
        self.assertEqual(s["targetB"], "f-chat-2", "a focus for B belongs to the column showing B: %r" % s)
        self.assertEqual(s["targetA"], "f-chat", "a focus for A belongs to column 1: %r" % s)
        # a session no column shows goes to the column the user last worked in (click-tracked by the shell)
        self.assertEqual(s["unknownAfterCol2"], "f-chat-2", "after a click in column 2, an unshown session's focus goes there: %r" % s)
        self.assertEqual(s["unknownAfterCol1"], "f-chat", "…and back to column 1 after a click there: %r" % s)
        self.assertEqual(s["col1AfterClicks"], SID_A, "the focus clicks changed no tab")

    def test_3_a_split_on_a_session_already_shown_takes_it_and_closing_it_drops_it_from_the_set(self):
        s = self._r()["s3"]
        self.assertEqual(s["frameId"], "f-chat-3")
        self.assertEqual(s["cols"], "[2,3]")
        # own:true: the arbitration would otherwise hand A's focus to column 1, which already shows A
        self.assertEqual(s["col3Active"], SID_A, "the third column shows A even though column 1 does: %r" % s)
        self.assertEqual(s["col1Still"], SID_A)
        self.assertEqual(s["targetAWithThree"], "f-chat", "with two columns on A, the first in row order wins the target")
        self.assertEqual(s["cycle"], ["f-chat-2", "f-chat-3", "f-chat", "f-chat-3"], "next, next, next wraps; previous wraps back: %r" % s["cycle"])
        self.assertTrue(s["pane3Gone"], "__rompCloseSplit(3) removes the pane, its gutter and its iframe")
        self.assertEqual(s["after"]["cols"], "[2]", "the persisted set is back to column 2 alone: %r" % s["after"])
        self.assertEqual(s["after"]["frameIds"], ["f-chat", "f-chat-2"])
        self.assertNotIn("chat3", s["after"]["grow"] or {}, "a closed column's grow leaves the store: %r" % s["after"]["grow"])

    def test_4_dragging_the_chat_gutter_moves_width_between_the_columns_and_persists_it(self):
        """FAILS on 2026-09-08 (a product bug the served leg exposed; left asserting the right behaviour): the
        grab's normalisation writes each pane's grow and only THEN reads the next pane's offsetWidth, and the
        write forces a reflow at a mixed scale (chat at its px width, the others still at their small default
        numbers), so column 2 is recorded at about a fifth of its width and column 1 balloons before the pointer
        moves. Pre-existing in _LANDING_JS for the chat|feed gutter, but every fresh browser's FIRST drag after a
        split hits it, since the new column's fair grow sits next to the 60/40 defaults."""
        s = self._r()["s4"]
        self.assertTrue(s["dragClass"], "the grab arms the drag (body.drag makes the iframes let the pointer through)")
        self.assertFalse(s["dragClassAfter"], "…and the release disarms it")
        g0 = s["growAtGrab"]
        self.assertLessEqual(abs(g0["chat"] - s["before1"]), 2, "the grab records column 1 at its real width: %r" % s)
        self.assertLessEqual(abs(g0["chat2"] - s["before2"]), 2, "the grab records column 2 at its real width, not a half-relaid one: %r" % s)
        d1, d2 = s["after1"] - s["before1"], s["after2"] - s["before2"]
        self.assertLessEqual(abs(d1 - DRAG_PX), SLACK_PX, "column 1 grows by about the drag: %r" % s)
        self.assertLessEqual(abs(d2 + DRAG_PX), SLACK_PX, "column 2 shrinks by about the drag: %r" % s)
        g = (s["grow"] or {}).get("chat2")
        self.assertIsInstance(g, (int, float), "romp-pane-grow carries column 2's weight: %r" % s["grow"])
        self.assertTrue(g == g and abs(g) != float("inf"))
        self.assertIsInstance((s["grow"] or {}).get("chat"), (int, float))

    def test_5_the_picker_in_column_2_lifts_that_column_only_and_unlifts_on_toggle(self):
        s = self._r()["s5"]
        o = s["open"]
        self.assertTrue(o["pickerOpen"], "body.picker-open while column 2's picker is up: %r" % o)
        self.assertTrue(o["pickerShown"], "the picker overlay is visible in column 2")
        self.assertTrue(o["frame2Lifted"], "the asking iframe wears .lifted: %r" % o)
        self.assertTrue(o["pane2Lifted"], "…and its pane: %r" % o)
        self.assertFalse(o["frame1Lifted"], "column 1 is NOT lifted: %r" % o)
        self.assertEqual(sorted(o["lifted"]), ["chat-pane-2", "f-chat-2"], o["lifted"])
        c = s["closed"]
        self.assertFalse(c["pickerOpen"], "toggle closes the picker and releases the lift: %r" % c)
        self.assertEqual(c["lifted"], [], "no .lifted remains: %r" % c)

    def test_6_a_reload_brings_both_columns_back_on_their_sessions_at_the_dragged_width(self):
        r = self._r()
        s = r["s6"]
        self.assertEqual(s["frameIds"], ["f-chat", "f-chat-2"], "both columns return after a reload: %r" % s)
        self.assertEqual(s["cols"], "[2]")
        self.assertEqual(s["col2Active"], SID_B, "column 2 finds its tab where it left it (its own state blob): %r" % s)
        self.assertEqual(s["col1Active"], SID_A)
        self.assertLessEqual(abs(s["pane2W"] - r["s4"]["after2"]), SLACK_PX,
                             "column 2 comes back at the width the drag left it: %r vs %r" % (s["pane2W"], r["s4"]["after2"]))
        self.assertLessEqual(abs(s["pane1W"] - r["s4"]["after1"]), SLACK_PX,
                             "…and so does column 1: %r vs %r" % (s["pane1W"], r["s4"]["after1"]))
        self.assertFalse(s["pickerOpen"]); self.assertEqual(s["lifted"], [])

    def test_7_closing_the_last_split_leaves_one_column_and_an_empty_set(self):
        s = self._r()["s7"]
        self.assertTrue(s["pane2Gone"], "__rompCloseSplit(2) removes the pane, its gutter and its iframe")
        self.assertEqual(s["frameIds"], ["f-chat"])
        self.assertEqual(s["cols"], "[]")
        self.assertNotIn("chat2", s["grow"] or {}, "the closed column's grow leaves the store: %r" % s["grow"])
        self.assertEqual(s["beforeKey"], SID_A)
        self.assertEqual(s["switched"], SID_B, "a hot key for a session no column shows switches the column last worked in to it")

    def test_8_the_whole_story_runs_in_well_under_half_a_minute(self):
        r = self._r()
        self.assertLess(r["ms"], 25000, "the driver waits on conditions, never on fixed sleeps: %d ms" % r["ms"])

    def test_per_tab_hot_keys_switch_to_the_column_showing_the_session_and_the_split_cycles(self):
        # the user 2026-09-10: a hot key per tab (set from its menu, shown minified on the tab) and a hot key that
        # cycles the focus between the split's columns; both live in the shared bindings store
        h = self._r()["hk"]
        full = (lambda k: "\u2325\u21e7" + k) if h["mac"] else (lambda k: "Alt+Shift+" + k)   # the platform's full spelling; the badge glyphs are the same everywhere
        self.assertEqual(h["badgeB"], {"text": "\u2325\u21e7K", "title": "hot key: " + full("K"), "aria": "hot key: " + full("K")}, "the badge, minified, its full spelling in the tooltip and the accessible name")
        self.assertEqual(h["registered"], full("K"), "the shell registered the tab's command from the store at boot, with its chord: %r" % h)
        self.assertIsNone(h["badgeA"], "no hot key, no badge")
        self.assertEqual(h["pressedFrom"]["shell"], "f-chat", "the chord is pressed with the keyboard inside column 1: the pane document's dispatcher carries it")
        self.assertNotIn(h["pressedFrom"]["pane"], ("composer-input", "TEXTAREA", "INPUT"), "…outside any text box")
        self.assertEqual(h["afterKey"]["focused"], "f-chat-2", "the chord goes to the column already showing the session: %r" % h)
        self.assertEqual(h["afterKey"]["col2"], SID_B); self.assertEqual(h["afterKey"]["col1"], SID_A, "…and column 1 keeps its own tab")
        self.assertEqual(h["afterCycle1"], "f-chat"); self.assertEqual(h["afterCycle2"], "f-chat-2")
        self.assertIn("Hot key…", h["menu"], "the tab menu offers it: %r" % h["menu"])
        r = h["recorder"]
        self.assertEqual(r["heading"], "Hot key for \u201cweb\u201d", "the pane's ask carries the session's name")
        self.assertEqual(r["rows"], 1); self.assertEqual(r["recording"], 1); self.assertTrue(r["filterHidden"]); self.assertTrue(r["builtInHidden"], "the built-in section is out of the way too")
        self.assertRegex(h["refused"], r"is built in \(move focus between panes\)", "a chord the pane-focus script owns is refused on the row: %r" % h["refused"])
        self.assertEqual(h["badgeAAfter"], {"text": "\u2325\u21e7L", "title": "hot key: " + full("L"), "aria": "hot key: " + full("L")}, "the recorded chord shows on the tab in every column")
        self.assertEqual(h["keysStore"]["session.hotkey." + SID_A], "Alt+Shift+L")
        self.assertEqual(h["afterRecord"], {"shell": "f-chat", "pane": "composer-input"}, "the dialog hands the keyboard back to the pane that asked, in its composer")
        rm = h["removed"]
        self.assertIsNone(rm["badgeCol1"]); self.assertIsNone(rm["badgeCol2"])
        self.assertNotIn(SID_A, rm["tabkeys"], "Remove takes the session out of the set: %r" % rm["tabkeys"])
        self.assertNotIn("session.hotkey." + SID_A, rm["keys"], "…and its override out of the store (no dead \"\" entry): %r" % rm["keys"])
        self.assertEqual(rm["dialogRows"], ["Switch to api"], "…and its row out of the dialog; B's stays")
        self.assertIn("Update hot key…", h["updateMenu"], "a bound tab's menu offers one row for both changing and removing: %r" % h["updateMenu"])
        self.assertNotIn("Remove hot key", h["updateMenu"]); self.assertNotIn("Change hot key…", h["updateMenu"])
        c = h["cancelled"]
        self.assertEqual(c["badgeB"]["text"], "\u2325\u21e7K", "Esc on a re-recording keeps the chord")
        self.assertIn(SID_B, c["tabkeys"]); self.assertEqual(c["keys"]["session.hotkey." + SID_B], "Alt+Shift+K"); self.assertEqual(c["dialogRows"], ["Switch to api"])
        self.assertEqual(c["afterCancel"], {"shell": "f-chat", "pane": "composer-input"}, "…and hands the keyboard back too")

    def test_the_session_bell_is_a_command_that_flips_the_active_sessions_flag_in_every_column(self):
        # the user 2026-09-11: notifications for the selected session on a key — the palette's command (bindable like any
        # other) writes the same per-session override the tab menu's bell row writes, and says what it did
        b = self._r()["bell"]
        self.assertEqual((b["before"], b["beforeCol2"]), ("Notify me", "Notify me"), "off to begin with: the lab's master is off and the session has no override")
        self.assertTrue(any(t == "Notifications enabled for web" for t in b["toastOn"]), "the toast names the session and the new state: %r" % b["toastOn"])
        self.assertEqual(b["afterOn"], "Stop notifying", "the tab menu reads the other way at once")
        self.assertEqual(b["col2AfterOn"], "Stop notifying", "…and in the other column, from the kernel's push: the flag reached the kernel")
        self.assertTrue(any(t == "Notifications disabled for web" for t in b["toastOff"]), b["toastOff"])
        self.assertEqual((b["afterOff"], b["col2AfterOff"]), ("Notify me", "Notify me"), "a second run turns it off again, everywhere")
        # the kernel's store, once the story has run: the override is off again — stored as such or dropped as the default
        p = os.path.join(self.lab, "xdg", "romp", "session-flags.json")
        flags = json.load(open(p)) if os.path.exists(p) else {}
        self.assertFalse((flags.get(SID_A) or {}).get("notify", False), "the override is off in the kernel's flags file: %r" % flags)

    def test_a_pinned_tab_wears_the_fold_and_is_not_draggable_in_every_column(self):
        # the user 2026-09-10: pin a tab so it stays where it is, shown as a folded corner; per browser, so every column agrees
        p = self._r()["pin"]
        for c in (p["col1"], p["col2"]):
            self.assertEqual(c, {"pinned": True, "draggable": False, "pin": True}, "pinned from column 1's menu, shown in both columns: %r" % p)
        self.assertEqual(p["a"], {"pinned": False, "draggable": True, "pin": False}, "the other tab is untouched")
        before = p["orderBefore"]["col1"]
        self.assertEqual(sorted(before), sorted([SID_A, SID_B])); self.assertEqual(p["orderBefore"]["col2"], before, "both columns start on the kernel's order")
        self.assertEqual(p["store"], {SID_B: before.index(SID_B)}, "pinned AT its slot")
        rev = list(reversed(before))
        self.assertEqual(p["orderHeld"]["arrangement"], rev, "the arrangement was rewritten to the reverse…")
        self.assertEqual(p["orderHeld"]["col1"], before, "…and column 1's strip kept B at its slot")
        self.assertEqual(p["orderHeld"]["col2"], before, "…and so did column 2's")
        self.assertIn("Unpin tab", p["menu"]); self.assertNotIn("Pin tab", p["menu"])
        for c in (p["after"]["col1"], p["after"]["col2"]):
            self.assertEqual(c, {"pinned": False, "draggable": True, "pin": False}, "unpinned from column 2's menu, gone in both: %r" % p["after"])
        self.assertEqual(p["after"]["store"], {})
        self.assertEqual(p["orderReleased"], {"col1": rev, "col2": rev}, "unpinned, the same rewrite moves the tab: the pin was what held it")



if __name__ == "__main__":
    unittest.main()
