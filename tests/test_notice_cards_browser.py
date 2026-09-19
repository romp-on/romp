"""Notice cards on the served feed page (T370, plans/notice-cards.md, issue #1750): a hermetic kernel over one synthetic session
in the notes-api demo world, the real /feed page served from a copy of the built bundle, driven by Playwright. Roads: a notice
posted through POST /notice appears as a card (the producer label, the body rendered as markdown with a remote image stripped,
the attachment as an inline picture from the file route with its pin, the column by needsYou); the card's action button reaches
the kernel's noticeAction op and, refused here (no backend owns the lab session), re-arms with the reason toasted; Clear
dismisses the card and Undo restores it; a new revision under the same key re-shows after a dismissal under a new item id;
an expired notice leaves at the next build. Synthetic only (placeholder ids, invented text)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from tests.dist_copy import copy_dist  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab  # noqa: E402  the lab kernel's environment
from test_live_paused_window_browser import _free_port  # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"

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
const errors = []; page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
// the pane's frames, recorded at the federation manager's inbound (the merged frames never ride the window message event):
// each feed frame's notice item ids, and every noticeActionDone
await page.addInitScript(() => {
  window.__feedNotices = []; window.__nad = []; window.__toasts = [];
  document.addEventListener("DOMContentLoaded", () => new MutationObserver(() => { for (const t of document.querySelectorAll(".feed-toast")) { const x = t.textContent || ""; if (x && !window.__toasts.includes(x)) window.__toasts.push(x); } })
    .observe(document.documentElement, { childList: true, subtree: true, characterData: true }));
  window.__feedAll = [];
  const record = (m) => { if (!m) return; if (m.type === "feed" && Array.isArray(m.asks)) { window.__feedNotices.push(m.asks.filter((a) => a && a.notice).map((a) => a.itemId));
      window.__feedAll.push({ n: m.asks.length, notes: m.asks.filter((a) => a && String(a.itemId || "").startsWith("notice:notes:")).map((a) => a.itemId + "|" + a.column), keys: Object.keys(m).filter((k) => k !== "asks").slice(0, 12) }); }
    else if (m.type === "feed") window.__feedAll.push({ shape: Object.keys(m).slice(0, 12) });
    if (m.type === "noticeActionDone") window.__nad.push(m); };
  let fed = null;
  Object.defineProperty(window, "__rompFed", { configurable: true, get() { return fed; },
    set(v) { fed = v; if (v && typeof v.inbound === "function" && !v.__labWrapped) { const inb = v.inbound; v.__labWrapped = true; v.inbound = (h, m) => { record(m); return inb(h, m); }; } } });
  window.addEventListener("message", (e) => { if (!fed) record(e.data); });
});
const post = async (body) => { const r = await page.request.post(cfg.notice, { data: body, headers: { "X-Romp-Token": cfg.token } }); return await r.json(); };
const sel = (id) => '[data-key="a:' + id + '"]';
const cardFacts = (id) => page.evaluate((s) => { const c = document.querySelector(s); if (!c) return null;
  const q = (x) => c.querySelector(x); const cs = getComputedStyle(c);
  return { col: c.parentElement && c.parentElement.id, vis: c.offsetHeight > 0 && cs.display !== "none", prod: (q(".fask-nprod") || {}).textContent || "",
    bodyText: (q(".fask-nbody") || {}).textContent || "", bodyStrong: !!q(".fask-nbody strong"), bodyImgs: c.querySelectorAll(".fask-nbody img").length,
    img: (q(".fask-nimg") || {}).getAttribute ? q(".fask-nimg").getAttribute("src") : null, imgLoaded: q(".fask-nimg") ? (q(".fask-nimg").naturalWidth > 0) : null,
    actions: Array.from(c.querAll ? [] : c.querySelectorAll(".fask-nactions button")).map((b) => ({ label: b.textContent, disabled: b.disabled })),
    title: (q(".fcard-title") || {}).textContent || "" }; }, sel(id));
await page.goto(cfg.feed);
await page.waitForFunction(() => (window.__feedNotices || []).length >= 1, null, { timeout: 60000 });   // the first feed frame landed
// (1) a posted notice appears: an informational one under Completed, with the producer, the rendered body, the pinned picture
const r1 = await post({ id: cfg.sid, key: "figure", title: "A new version of the accuracy figure is ready", producer: "figure",
  body: "Regenerated after the sweep on **tests** finished.\n\n<img src=\"https://evil.example/x.png\">", attachment: cfg.png });
const id1 = "notice:" + cfg.sid + ":figure:" + (r1.notice || {}).rev;
await page.waitForSelector(sel(id1), { timeout: 60000 }).catch(() => {});
await page.waitForFunction((s) => { const i = document.querySelector(s + " .fask-nimg"); return i && i.complete; }, sel(id1), { timeout: 15000 }).catch(() => {});
const first = { post: r1, card: await cardFacts(id1) };
// (2) a needs-you notice with an action files under Blocked; its Send again reaches the kernel's noticeAction op, which runs
// the stored action through the one delivery door: the lab session is dormant with no backend to own it, so the kernel
// REFUSES with its reason, the button re-arms and the reason rides the toast, and the card stays (dismissOnAction needs a
// success; the success road is tests/test_notice_cards.py Actions, with the delivery door stubbed)
const r2 = await post({ id: cfg.sid, key: "dropped-sends", title: "1 message you typed before the restart was not re-sent", producer: "dropped-sends", needsYou: true,
  dismissOnAction: true, actions: [{ label: "Send again", route: "/send", body: { text: "please regenerate the figure" } }] });   // no target in a body: the card's own session receives it
const id2 = "notice:" + cfg.sid + ":dropped-sends:" + (r2.notice || {}).rev;
await page.waitForSelector(sel(id2), { timeout: 60000 }).catch(() => {});
const second = { card: await cardFacts(id2) };
// Capture the synchronous click latch before returning to Playwright (2026-09-16). A kernel reply or an ordinary
// feed push can already re-arm the button between page.click and a later page.evaluate; waiting for that transient
// state would miss it too. The real action still goes to the kernel, whose reply and re-arm are checked below.
const latched = await page.evaluate((s) => {
  const c = document.querySelector(s), b = c && c.querySelector(".fask-nactions button");
  if (!b) throw new Error("notice action button is absent");
  b.click();
  return { actions: Array.from(c.querySelectorAll(".fask-nactions button")).map((a) => ({ label: a.textContent, disabled: a.disabled })) };
}, sel(id2));
await page.waitForFunction((id) => (window.__nad || []).some((m) => m.itemId === id), id2, { timeout: 30000 }).catch(() => {});
const done2 = await page.evaluate((id) => (window.__nad || []).filter((m) => m.itemId === id), id2);
await page.waitForFunction((s) => { const b = document.querySelector(s + " .fask-nactions button"); return b && !b.disabled; }, sel(id2), { timeout: 15000 }).catch(() => {});
const rearmed = await cardFacts(id2);
const toasts = await page.evaluate(() => window.__toasts || []);
const stillThere = !!(await page.$(sel(id2)));
// (3) Clear dismisses the card, Undo restores it
await page.click(sel(id1) + " button.fdismiss:visible", { timeout: 5000 }).catch(() => {});
await page.waitForSelector(sel(id1), { state: "detached", timeout: 15000 }).catch(() => {});
const afterClear = !!(await page.$(sel(id1)));
const undo = page.getByRole("button", { name: /undo/i }).first();
let undone = null;
if (await undo.count()) { await undo.click(); await page.waitForSelector(sel(id1), { timeout: 15000 }).catch(() => {}); undone = !!(await page.$(sel(id1))); }
// (4) a new revision under the same key re-shows after a dismissal, under a new id
await page.click(sel(id1) + " button.fdismiss:visible", { timeout: 5000 }).catch(() => {});
await page.waitForSelector(sel(id1), { state: "detached", timeout: 15000 }).catch(() => {});
const r3 = await post({ id: cfg.sid, key: "figure", title: "A newer version of the accuracy figure is ready", producer: "figure" });
const id3 = "notice:" + cfg.sid + ":figure:" + (r3.notice || {}).rev;
await page.waitForSelector(sel(id3), { state: "attached", timeout: 60000 }).catch(() => {});
const revision = { rev: (r3.notice || {}).rev, oldBack: !!(await page.$(sel(id1))), newShown: !!(await page.$(sel(id3))) };
// (5) an expired notice leaves at the next build: post one that expires in 30 s (the lab's pusher lands a frame every few
// seconds), wait past the expiry, then post another (a build): the expired one is skipped and, by the sweep, archived
const now = Math.floor(Date.now() / 1000);
const r4 = await post({ id: cfg.sid, key: "soon", title: "A short-lived note", producer: "cli", expiresAt: now + 30 });
const id4 = "notice:" + cfg.sid + ":soon:" + (r4.notice || {}).rev;
// the frames are the truth here (the Completed column shows a bounded number of cards, so a further completed card may hold
// no element): a frame carrying the card, then, after its expiry and a build, frames without it
await page.waitForFunction((id) => (window.__feedNotices || []).some((f) => f.includes(id)), id4, { timeout: 60000 }).catch(() => {});
const soonShown = await page.evaluate((id) => (window.__feedNotices || []).some((f) => f.includes(id)), id4);
const framesAtExpiry = await page.evaluate(() => (window.__feedNotices || []).length);
const diag = { r4, frames: await page.evaluate(() => (window.__feedNotices || [])), keys: await page.evaluate(() => Array.from(document.querySelectorAll('[data-key^="a:notice:"]')).map((c) => c.getAttribute("data-key"))),
  counts: await page.evaluate(() => Object.fromEntries(Array.from(document.querySelectorAll('[id^="col-"][id$="-list"]')).map((c) => [c.id, c.children.length]))) };
await page.waitForTimeout(31000);
await post({ id: cfg.sid, key: "tick", title: "a later note", producer: "cli" });
await page.waitForFunction(([id, n]) => { const fr = window.__feedNotices || []; return fr.length > n && !fr[fr.length - 1].includes(id); }, [id4, framesAtExpiry], { timeout: 60000 }).catch(() => {});
const expiry = { shown: soonShown, goneAfterBuild: await page.evaluate((id) => { const fr = window.__feedNotices || []; return fr.length > 0 && !fr[fr.length - 1].includes(id); }, id4) };
// (6) a refused post over the route: a disallowed action route
const refused = await post({ id: cfg.sid, key: "bad", title: "x", actions: [{ label: "x", route: "/watch", body: {} }] });
// (7) an OWNER-LESS card (the user 2026-09-18): posted with neither id nor name, it heads its column under a Notes header that
// is plain text (no anchor, no title, no dead class, no click, no revive offer), with no session chip on the card; the reserved
// word as a NAME is refused like any name no session answers to (round two of PR 1831)
await page.mouse.move(2, 2);   // the pointer left resting on a card by the roads above holds every payload (the hover-freeze gate): release it
await page.waitForTimeout(300);
const r7 = await post({ key: "everyone", title: "Remember the standup moved", body: "to 10:30", producer: "cli" });
const id7 = "notice:notes:everyone:" + (r7.notice || {}).rev;
await page.waitForSelector(sel(id7), { state: "attached", timeout: 60000 }).catch(() => {});
const ownerless = await page.evaluate((s) => {
  const c = document.querySelector(s);
  if (!c) return { missing: true, inFrames: (window.__feedNotices || []).filter((f) => f.some((k) => k.startsWith("notice:notes:"))).length, frames: (window.__feedNotices || []).length,
                   completed: Array.from(document.querySelectorAll("#col-completed-list [data-key]")).map((n) => n.dataset.key).slice(0, 12),
                   heads: Array.from(document.querySelectorAll(".feed-sess-head")).map((h) => (h.parentNode && h.parentNode.id) + ":" + h.getAttribute("data-fsid")) };
  const col = c.parentNode; const cards = Array.from(col.children).filter((n) => n.dataset && n.dataset.key && n.dataset.key.startsWith("a:")).map((n) => n.dataset.key);
  const head = Array.from(col.querySelectorAll(".feed-sess-head")).find((h) => h.getAttribute("data-fsid") === "notes");
  const nm = head ? head._name : null;
  // a click on the header's name must open nothing: no dialog, no new Revive button (a dead session's card carries its own
  // Revive, so the count is compared before and after), and never the closed-session offer's words
  const revives = () => Array.from(document.querySelectorAll("button, .fbtn")).filter((b) => /revive/i.test(b.textContent || "")).length;
  const dialogsBefore = document.querySelectorAll("dialog[open], .fmodal, .modal").length; const revBefore = revives();
  if (nm) nm.click();
  const dialogsAfter = document.querySelectorAll("dialog[open], .fmodal, .modal").length;
  const revive = revives() !== revBefore || /is closed, revive it/i.test(document.body.textContent || "");
  return { col: col.id, cards, chipDisplay: getComputedStyle(c._name || c.querySelector(".fname") || c).display, headFound: !!head, headText: head ? head.textContent : null,
           nmTag: nm ? nm.tagName : null, nmClass: nm ? nm.className : null, nmTitle: nm ? nm.getAttribute("title") : null, nmDead: nm ? nm.classList.contains("dead") : null,
           nmClick: nm ? (nm.onclick === null) : null, dialogsBefore, dialogsAfter, reviveOffered: revive, grouped: (JSON.parse(localStorage.getItem("romp:settings") || "{}").grouped !== false) };
}, sel(id7));
const byName = await post({ name: "notes", key: "k", title: "t" });
process.stdout.write("RESULT:" + JSON.stringify({ first, second, latched, done2, stillThere, toasts, rearmed, afterClear, undone, revision, expiry, refused, errors, diag, r7, ownerless, byName }) + "\n");
await browser.close();
"""


class NoticeCardsServed(unittest.TestCase):
    maxDiff = None

    @classmethod
    def _skip(cls, why):
        if os.environ.get("ROMP_SERVED_TESTS_REQUIRE") == "1":
            raise AssertionError("ROMP_SERVED_TESTS_REQUIRE=1 but the served lab could not run: " + why)
        raise unittest.SkipTest(why)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            cls._skip("extension deps absent (npm ci not run here): the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="notice-cards-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            cls._skip("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(os.path.join(cwd, "figures"), exist_ok=True)
        Path(cls.state, "session-hosts").write_text("off\n")
        # a real, tiny PNG (1x1) so the browser's image decode says loaded
        cls.png = os.path.join(cwd, "figures", "accuracy.png")
        Path(cls.png).write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8cfc0000000030001"
            "5c8e2c2b0000000049454e44ae426082"))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(cls.state, "names", SID).write_text("web\t%s\t#1EA1EB\t#ffffff\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": False,   # dormant: no spawn here (the lab has no SDK)
             "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        t0 = int(time.time()) - 3600
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
        recs = [{"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": iso(t0), "sessionId": SID,
                 "message": {"role": "user", "content": "a question about the notes api"}},
                {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": iso(t0 + 2), "sessionId": SID,
                 "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "the notes api keeps its shape."}]}}]
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.port = _free_port()
        cls.token = "testtok-notices"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
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
        if getattr(type(self), "_fail", None):
            self.fail(type(self)._fail)
        if self._r is None:
            cfg = os.path.join(self.lab, "notices.json")
            base = "http://127.0.0.1:%d" % self.port
            with open(cfg, "w") as f:
                json.dump({"feed": base + "/feed?token=" + self.token, "notice": base + "/notice", "token": self.token, "sid": SID, "png": self.png}, f)
            driver = os.path.join(self.lab, "notices.mjs")
            Path(driver).write_text(DRIVER)
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=400,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
            if "browser-launch-failed" in p.stderr:
                self._skip("no playwright browser on this box")
            line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
            if line is None:
                type(self)._fail = "the driver produced no RESULT (stderr: %s; kernel: %s)" % (p.stderr[-2000:], open(self.klog).read()[-1500:])
                self.fail(type(self)._fail)
            type(self)._r = json.loads(line[len("RESULT:"):])
        print("NOTICES:", json.dumps(self._r), file=sys.stderr)
        return self._r

    def test_a_posted_notice_appears_with_its_producer_rendered_body_and_pinned_picture_under_completed(self):
        r = self._result()
        self.assertEqual(r["errors"], [], "no page error")
        self.assertTrue(r["first"]["post"].get("ok"), r["first"]["post"])
        c = r["first"]["card"]
        self.assertIsNotNone(c, "the card is on the board")
        self.assertEqual((c["col"], c["vis"], c["prod"]), ("col-completed-list", True, "via figure"))
        self.assertEqual(c["title"], "A new version of the accuracy figure is ready")
        self.assertTrue(c["bodyStrong"], "markdown rendered: the bold survives the sanitizer: %r" % c["bodyText"])
        self.assertEqual(c["bodyImgs"], 0, "the remote image in the body is stripped")
        self.assertIn("Regenerated after the sweep on tests finished.", c["bodyText"])
        self.assertIsNotNone(c["img"], "the allowed image attachment renders inline")
        self.assertIn("/file?path=", c["img"]); self.assertIn("&sid=" + SID, c["img"]); self.assertIn("&pin=", c["img"])
        self.assertTrue(c["imgLoaded"], "…and the browser decoded it from the file route: %r" % c["img"])

    def test_a_needs_you_notice_files_under_blocked_and_its_action_runs_kernel_side_and_a_refusal_re_arms_the_button(self):
        r = self._result()
        c = r["second"]["card"]
        self.assertEqual((c["col"], c["prod"]), ("col-needsInput-list", "via dropped-sends"))
        self.assertEqual(c["actions"], [{"label": "Send again", "disabled": False}])
        self.assertEqual(r["latched"]["actions"], [{"label": "Send again…", "disabled": True}], "latched on the click")
        self.assertEqual(len(r["done2"]), 1, "the kernel answered the gesture by the card's id: %r" % r["done2"])
        self.assertFalse(r["done2"][0]["ok"]); self.assertIn("no running backend owns", r["done2"][0]["error"], "the delivery door's own refusal for a session no backend owns")
        self.assertEqual(r["rearmed"]["actions"], [{"label": "Send again", "disabled": False}], "re-armed on the kernel's answer")
        self.assertTrue(any("refused" in t and "no running backend owns" in t for t in r["toasts"]), "the reason rode the toast: %r" % r["toasts"])
        self.assertTrue(r["stillThere"], "a refused action leaves the card (dismissOnAction needs a success)")

    def test_clear_dismisses_and_undo_restores_and_a_new_revision_re_shows_after_a_dismissal(self):
        r = self._result()
        self.assertFalse(r["afterClear"], "Clear took the card off the board")
        self.assertIsNotNone(r["undone"], "an Undo affordance was offered"); self.assertTrue(r["undone"], "…and it restored the card")
        self.assertEqual(r["revision"], {"rev": 2, "oldBack": False, "newShown": True}, "a revision under the same key shows under a new id though rev 1 was dismissed")

    def test_an_owner_less_card_heads_its_column_under_a_plain_notes_header_with_no_chip_and_the_reserved_name_is_refused(self):
        r = self._result()
        self.assertTrue(r["r7"].get("ok"), r["r7"]); self.assertEqual(r["r7"]["notice"]["sid"], "notes", "no id, no name: the reserved home")
        o = r["ownerless"]
        self.assertIsNotNone(o, "the owner-less card is on the board")
        self.assertEqual(o["col"], "col-completed-list")
        self.assertEqual(o["cards"][0], "a:notice:notes:everyone:%s" % r["r7"]["notice"]["rev"], "first in its column, above the session's cards: %r" % o["cards"])
        self.assertEqual(o["chipDisplay"], "none", "no session chip on the card")
        self.assertTrue(o["grouped"], "grouped mode is the default: the run has a header")
        self.assertTrue(o["headFound"], "the Notes run's header")
        self.assertIn("Notes", o["headText"] or "")
        self.assertEqual((o["nmTag"], o["nmClass"], o["nmTitle"], o["nmDead"], o["nmClick"]), ("SPAN", "fname-plain", None, False, True),
                         "plain text: a span, no title, no dead class, no click handler: %r" % o)
        self.assertEqual((o["dialogsBefore"], o["dialogsAfter"], o["reviveOffered"]), (0, 0, False), "a click on it opens nothing and offers no revive")
        self.assertEqual((r["byName"].get("ok"), r["byName"].get("error")), (False, 'no session answers to "notes"'), "the reserved word as a name is refused")

    def test_an_expired_notice_leaves_at_the_next_build_and_a_disallowed_action_is_refused_at_the_door(self):
        r = self._result()
        self.assertEqual(r["expiry"], {"shown": True, "goneAfterBuild": True})
        self.assertEqual(r["refused"].get("ok"), False); self.assertIn("not allowed", r["refused"].get("error", ""))


if __name__ == "__main__":
    unittest.main()
