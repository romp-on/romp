#!/usr/bin/env python3
"""T247, the served-page pins: the landing page is served from a hermetic kernel module (its own
_landing() HTML, /spend/detail and /usage/fleet answered from a synthetic ledger — placeholder uuids,
TESTHOST, the notes-api demo sessions), and a real browser clicks the usage readout. Pins the modal's
open/close contract (click opens over the 0.55 backdrop; Escape and a backdrop tap close), the table
and the stacked histogram with its unattributed stack, the toggles, and the segment tooltip. Skips
LOUDLY without a playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules holding it (CI does
not; this is the maintainer's screenshot harness too — pass ROMP_SHOTS=<path-prefix> for PNGs)."""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_spendmodal", os.path.join(BIN, "romp-kernel"))

import sys
sys.path.insert(0, HERE)
import test_spend_detail as _sd   # noqa: E402  the same synthetic world (the module, not its classes:
#                                   an imported TestCase would be collected here a second time)
write_ledger, WEB, API, TESTS = _sd.write_ledger, _sd.WEB, _sd.API, _sd.TESTS


def _node_path():
    for cand in (os.environ.get("ROMP_PLAYWRIGHT_NODE_PATH", ""), os.path.join(ROOT, "vscode-extension", "node_modules")):
        if cand and os.path.isdir(os.path.join(cand, "playwright")):
            return cand
    return ""


class SpendModalServed(unittest.TestCase):
    def setUp(self):
        np = _node_path()
        if not np:
            self.skipTest("no playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules that holds it")
        self.node_path = np
        self.td = tempfile.TemporaryDirectory()
        state = Path(self.td.name)
        self._saved = (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host,
                       km._claude_account, km._auth_key_present)
        km.jd.STATE = state
        km.NAMES = state / "names"
        km.NAMES.mkdir()
        (km.NAMES / WEB).write_text("web\t/tmp/notes-api\t#1EA1EB\t#ffffff\n")
        (km.NAMES / API).write_text("api\t/tmp/notes-api\t#54B204\t#ffffff\n")
        (km.NAMES / TESTS).write_text("tests\t/tmp/notes-api\t#4EA8A9\t#ffffff\n")
        km._live_names = lambda tm: {"web": WEB, "api": API}
        km._live_map = lambda: []
        km._self_host = lambda: "TESTHOST"
        km._claude_account = lambda: ""
        km._auth_key_present = lambda: True
        # the KERNEL's clock is the fixture's too (2026-10-09). /spend/detail is served at the fixture's NOW
        # below, but /usage and /usage/fleet come from km._usage(), whose spend windows (_spend_windows: five
        # hours, seven days, the month) read time.time(). On the wall clock the anchored ledger slid out of
        # those windows (out of the month on 2026-10-01, out of the rolling 30 days on 2026-10-07), the usage
        # rows above the chart shrank, the chart rose, and the short-window tooltip found room below the
        # pointer: the pin that it goes above the chart failed on every CI run with no change in the repo.
        # The swap tests/test_spend_windows.py makes, kept whole: every attribute is the real module's;
        # time() and time_ns() read the fixture's afternoon plus the seconds since here, so a kernel wait
        # still ends
        real_time, t0 = km.time, time.time()
        clock = types.ModuleType("time")
        clock.__dict__.update({k: getattr(real_time, k) for k in dir(real_time) if not k.startswith("__")})
        clock.time = lambda: _sd.NOW + (real_time.time() - t0)
        clock.time_ns = lambda: int(clock.time() * 1e9)
        km.time = clock
        self.addCleanup(setattr, km, "time", real_time)
        (state / "usage.json").write_text(json.dumps({"apiKey": True}))
        write_ledger(state, extra_sids=20)     # enough sessions to scroll the pane (T247e)
        (state / "session-order.json").write_text(json.dumps([API, WEB]))   # the tab strip's order: api before web (T247f)
        # T247g: two tags — "team" (web + api) and "ops" (api + the peer's worker): api is under both
        (state / "timeline-views.json").write_text(json.dumps({"active": "all", "tags": [
            {"id": "g1", "name": "team", "color": "#C2410C", "members": [WEB, API]},
            {"id": "g2", "name": "ops", "color": "#0F766E", "members": [API, "PEERHOST:22222222-3333-4444-5555-000000000001"]}]}))
        # T247c: a two-host world — a peer kernel whose sessions merge in, and an older peer reported by name
        self._saved_remotes = dict(km._remotes)
        km._remotes.clear()
        sd = _sd.SpendDetail("test_the_route_and_the_shell_are_wired")
        off = int((time.localtime(_sd.NOW).tm_gmtoff or 0) // 60) + 180
        peer_port = sd._peer_server.__func__(self, sd._peer_payload.__func__(self, off))
        km._remotes["PEERHOST"] = {"host": "PEERHOST", "status": "up", "local_port": peer_port, "token": "peer-tok",
                                   "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}
        km._remotes["OLDHOST"] = {"host": "OLDHOST", "status": "up", "local_port": sd._peer_server.__func__(self, status=404), "token": "peer-tok",
                                  "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}
        html = km._landing().encode()
        blank = b"<!doctype html><html><body style='margin:0;background:#1e1e1e'></body></html>"

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                p = self.path.split("?", 1)[0]
                if p == "/":
                    return self._out(200, html, "text/html; charset=utf-8")
                if p == "/spend/detail":
                    # the fixture's OWN clock (review find: served with the wall clock, the 192-hour window
                    # slid off the anchored ledger within days and the hourly pins became a time bomb)
                    return self._out(200, json.dumps(km._spend_detail(now=_sd.NOW)).encode(), "application/json")
                if p == "/usage/fleet":
                    return self._out(200, json.dumps({"rows": [{"host": "", "acct": "", "usage": km._usage()}],
                                                      "host": "TESTHOST"}).encode(), "application/json")
                if p == "/usage":
                    return self._out(200, json.dumps(km._usage() or {}).encode(), "application/json")
                if p.startswith("/media/") or p.startswith("/dist/"):
                    f = os.path.join(ROOT, p.lstrip("/"))
                    if p == "/dist/styles.css" and not os.path.isfile(f):
                        f = os.path.join(ROOT, "ui", "webview", "styles.css")   # the source, when no build ran here
                    if os.path.isfile(f):
                        ct = "image/svg+xml" if f.endswith(".svg") else "application/javascript" if f.endswith(".js") else "application/octet-stream"
                        return self._out(200, open(f, "rb").read(), ct)
                    return self._out(404, b"", "text/plain")
                if p in ("/chat", "/feed", "/timeline", "/fleet", "/dashboard"):
                    return self._out(200, blank, "text/html; charset=utf-8")
                return self._out(200, b"{}", "application/json")

            def do_POST(self):
                return self._out(200, b"{}", "application/json")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        if hasattr(self, "srv"):
            self.srv.shutdown()
            self.srv.server_close()
        if hasattr(self, "_saved"):
            (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host,
             km._claude_account, km._auth_key_present) = self._saved
            km._remotes.clear()
            km._remotes.update(getattr(self, "_saved_remotes", {}))
            self.td.cleanup()

    def test_click_opens_the_modal_with_table_and_stacked_histogram_and_escape_closes_it(self):
        # the page's usage answer reads the fixture's clock (setUp's swap): every window holds the anchored
        # ledger's spend. On the wall clock all three read zero from 2026-10-07 on, and the layout pins below
        # drifted with the calendar instead of the code
        sp = (km._usage() or {}).get("spend") or {}
        self.assertTrue(all((sp.get(w) or {}).get("usd", 0) > 0 for w in ("fiveHour", "sevenDay", "month")),
                        "the usage windows read the fixture's clock, not the wall clock: " + json.dumps(sp))
        shots = os.environ.get("ROMP_SHOTS", "")
        env = dict(os.environ, NODE_PATH=self.node_path)
        r = subprocess.run(["node", os.path.join(HERE, "spend_modal_headless.js"), "http://127.0.0.1:%d/" % self.port, shots],
                           capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(r.returncode, 0, "the driver failed:\n" + r.stderr[-3000:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(o["hiddenBefore"], "closed until the click")
        # T247d: the desktop hover ends in the affordance line, in the footnote style; the phone panel does not carry it
        self.assertEqual(o["hoverHint"]["text"], "Click for the full breakdown by session.", o["hoverHint"])
        self.assertEqual((o["hoverHint"]["cls"], o["hoverHint"]["font"], o["hoverHint"]["opacity"]), ("ru-tip-hint", "10px", "0.55"), o["hoverHint"])
        self.assertIs(o["mobile"]["panelHint"], False, "the phone panel keeps its button and no click hint")
        self.assertTrue(o["loaderSeen"], "the loader (or the content) is up the instant the modal opens")
        self.assertEqual(o["out"]["backdrop"], "rgba(0, 0, 0, 0.55)", "the panel rule's backdrop")
        self.assertEqual(o["out"]["head"], "API spend · 2 machines", "the header names the machines when several contribute (review find)")
        rows = o["out"]["rows"]
        # T247c: two hosts contribute, so every row names its host in the tab strip's host-prefix voice. T353: the
        # list follows the chart's range, seven days by hour at open, and sorts by the spend IN that range: web
        # (an hourly dollar over the ledger's 60 hours), then api, then the peer's worker
        self.assertTrue(rows[0].startswith("TESTHOST:web"), rows[0])
        self.assertTrue(rows[1].startswith("TESTHOST:api"), rows[1])
        self.assertTrue(rows[2].startswith("PEERHOST:worker"), rows[2])
        self.assertTrue(any("OLDHOST" in n and "older build" in n for n in o["out"]["notes"]), o["out"]["notes"])
        self.assertFalse(any("this machine only" in n for n in o["out"]["notes"]))
        self.assertTrue(any("aligned by clock time" in n for n in o["out"]["notes"]), "hosts in different zones: the timezone rule is stated")
        # T247e: the chart comes first, has no legend; the list is a scroll pane of EVERY session under a
        # sticky header, each row its tab title in its identity color, no swatch, no fold
        self.assertTrue(o["out"]["chartFirst"], "the chart section precedes the session list")
        self.assertEqual(o["out"]["legendNodes"], 0, "no legend")
        self.assertEqual(o["out"]["swatches"], 0, "no swatch in a session row")
        # T353: the seven days shown carry the three hourly-ledger sessions, the peer's worker and the unattributed
        # hour; the twenty filler sessions spend by the day only, so they appear once the 90-day range is shown
        self.assertEqual(len(rows), 5, "the sessions with spend in the range shown: " + " | ".join(rows))
        self.assertEqual(len(o["days"]["rows"]), 3 + 20 + 1 + 1, "the 90-day range lists every session across both hosts, plus the unattributed row")
        self.assertEqual(o["out"]["title"]["color"], "rgb(30, 161, 235)", "web's title wears its identity color")
        self.assertEqual(o["out"]["title"]["weight"], "600")
        self.assertEqual(o["out"]["title"]["prefix"], "TESTHOST:")
        self.assertTrue(o["days"]["pane"]["scrolls"], "the full list scrolls in its pane (T247e): " + str(o["days"]["pane"]))
        self.assertEqual(o["out"]["pane"]["sticky"], "sticky")
        self.assertEqual(o["out"]["pane"]["thOpacity"], "1", "the sticky header occludes: muted by color, not opacity (review find)")
        self.assertFalse(o["out"]["pane"]["panelScrolls"], "the card itself does not scroll at 820px: the pane takes the room under the chart (review find)")
        self.assertFalse(o["days"]["pane"]["panelScrolls"], "nor with the full list: the pane scrolls, not the card")
        # T247f: "your order" — the strip's order (api before web from session-order.json, then the peer's own
        # order), unknown sessions trailing by spend; the chart's bottom stack follows; a viewer arrangement
        # (the strip's localStorage key) reorders both; the choice persists with the other toggles
        yo = o["yourOrder"]
        self.assertTrue(yo["rows"][0].startswith("TESTHOST:api") and yo["rows"][1].startswith("TESTHOST:web"), yo["rows"][:3])
        self.assertTrue(yo["rows"][2].startswith("PEERHOST:worker"), yo["rows"][:3])
        self.assertTrue(yo["rows"][3].startswith("TESTHOST:tests"), "sessions the order does not know trail, by spend: " + yo["rows"][3])
        self.assertEqual(yo["bottomFill"], "#54B204", "the chart's bottom stack is the first row (api)")
        self.assertTrue(yo["viewRows"][0].startswith("TESTHOST:web"), "the viewer's own arrangement wins over the seed: " + yo["viewRows"][0])
        self.assertEqual(yo["prefs"].get("order"), "yours")
        self.assertTrue(yo["pressed"])
        self.assertTrue(o["mobile"]["persisted"]["pressed"], "the persisted choice is read back on a reload (review find: only the write was pinned)")
        # T247g: merge by tag — one row per tag (summed, tag color), untagged sessions as themselves, a session
        # under two tags counted in each with the footer saying so; the stacks follow; "1 day" is 24 hourly buckets
        mg = o["merge"]
        self.assertTrue(mg["rows"][0].startswith("team · 2 sessions"), mg["rows"][:3])
        self.assertTrue(mg["rows"][1].startswith("ops · 2 sessions"), mg["rows"][:3])
        self.assertFalse(any(r.startswith("TESTHOST:web") or r.startswith("TESTHOST:api") or r.startswith("PEERHOST:worker") for r in mg["rows"]), "tagged sessions merge away")
        # the merged rows are read in the 90-day range (the driver left it there), where the fixture's whole ledger
        # lies: the sums over that range are the ledger's totals (T353: the list follows the chart's range)
        self.assertIn("$1200", mg["rows"][0].replace(",", ""), "team = web 960 + api 240: " + mg["rows"][0])
        self.assertIn("$540", mg["rows"][1].replace(",", ""), "ops = api 240 + worker 300: " + mg["rows"][1])
        self.assertEqual(mg["tagColor"], "rgb(194, 65, 12)", "the tag row wears the tag store's color")
        self.assertEqual((mg["tagBorder"], mg["tagWeight"]), ("rgb(194, 65, 12)", "400"), "…as the one tag chip: its colour on a thin border, never bold (T321)")
        self.assertTrue(any("1 session carries several tags" in n for n in mg["notes"]), mg["notes"])
        self.assertIn("team", mg["stackNames"]); self.assertIn("ops", mg["stackNames"])
        self.assertLessEqual(mg["dayBuckets"], 24, "1 day · by hour = the last 24 hourly buckets")
        self.assertTrue(any(":00" in x for x in mg["dayLabels"]), mg["dayLabels"])
        self.assertEqual(mg["prefs"].get("merge"), True)
        self.assertTrue(o["mobile"]["persisted"]["first"].strip().startswith("TESTHOST:api"), o["mobile"]["persisted"])
        self.assertTrue(any("tests" in r and "not running" in r for r in rows), "a dead session keeps its name, dimmed")
        self.assertTrue(any(r.strip().startswith("unattributed") for r in rows), "pre-attribution spend is a row of its own (its hatch mark leads)")
        self.assertGreaterEqual(o["out"]["deadRows"], 2)
        self.assertGreater(o["days"]["segs"], 24 * 30, "every session's daily bars, no fold")
        self.assertGreater(o["out"]["segs"], 60)
        self.assertGreaterEqual(o["out"]["hatched"], 1, "the unattributed stack wears the hatch, not a hue")
        self.assertIn("1 hour", o["out"]["windows"], "the same window rows the hover shows")
        self.assertIn("1 month", "".join(o["out"]["windows"]))
        self.assertTrue(o["out"]["ylabels"] and o["out"]["ylabels"][0].startswith("$"), o["out"]["ylabels"])
        self.assertTrue(o["out"]["xlabels"], "weekday initials at the ledger's midnights")
        self.assertTrue(o["days"]["segs"] > 0 and not o["days"]["ylabels"][0].startswith("$"), "tokens on the toggle")
        self.assertTrue(any("/" in x for x in o["days"]["xlabels"]), "date labels on the daily range")
        self.assertIn("·", o["tip"], "the bucket tooltip's header reads total · other measure · stamp")
        # T293 (the user 2026-09-09): the crosshair. Hovering the chart clear of every bar draws the hairline at the
        # pointer's bucket and a stamp in words; the tooltip lists that bucket's sessions in spend order in their stack
        # colours; over a bar the same box names the bar's session on its emphasised row; the marks take no pointer
        # events, move nothing under the pointer, and leave with it
        xh = o["xh"]
        self.assertTrue(xh["lineShown"] and xh["stampShown"] and xh["tipShown"], xh)
        self.assertRegex(xh["stamp"], r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} \d{1,2}, \d{1,2} (AM|PM)$")
        self.assertIn(xh["stamp"], xh["head"], "the tooltip's header carries the stamp")
        self.assertTrue(xh["head"].startswith("$"), "the bucket's total leads: " + xh["head"])
        self.assertGreaterEqual(len(xh["rows"]), 2, xh["rows"])
        def _num(s):   # fmtTok's three-significant-figure spelling: 480k, 96.0k, 1.00k, 1.2M
            m = re.match(r"^([\d.]+)([kMB]?)$", s.replace(",", "").strip())
            self.assertIsNotNone(m, s)
            return float(m.group(1)) * {"": 1, "k": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
        self.assertTrue(all(r["dot"] for r in xh["rows"]), "every row wears its stack's colour")
        self.assertFalse(any(r["on"] for r in xh["rows"]), "no bar under the pointer, no emphasised row")
        self.assertEqual((xh["lineInert"], xh["stampInert"], xh["tipInert"]), ("none", "none", "none"), "pointer-inert marks")
        self.assertEqual(xh["stampFont"], "10px", "the surface's annotation size")
        self.assertEqual(xh["chartH"], o["hBefore"], "nothing moves under the pointer")
        self.assertNotEqual(xh["movedX1"], xh["x1"], "the line follows the pointer to another bucket")
        self.assertTrue(xh["flip"], "near the right edge the stamp sits to the line's left")
        self.assertFalse(xh["flipLeft"], "…and at the first bucket to its right")
        self.assertGreaterEqual(xh["stampLeft"], xh["gyRight"], "the stamp starts past the ceiling label, never over it (review find)")
        self.assertEqual(o["afterResize"], {"tip": "none", "line": "none", "stamp": "none"}, "a rebuild under a still pointer takes all three down (review find)")
        self.assertEqual(o["xhAfter"], {"line": "none", "stamp": "none", "tip": "none"}, "all three leave with the pointer")
        # the middle range is 7 days: the label, and seven local midnights on the axis (192 hours carried eight). The
        # fixture's NOW is 15:30 local and the keys are the recorder's local hours, so the 168-hour tail holds seven T00
        # keys in any zone whose clock change falls at 1-3 AM; a zone that springs forward AT midnight would drop one:
        # the runner's zone, not the code, is what this pin assumes
        self.assertEqual(o["week"]["label"], "7 days · by hour")
        self.assertEqual(len(o["out"]["xlabels"]), 7, o["out"]["xlabels"])
        # the 1-day view stamps hours too; the 90-day view stamps dates and folds dozens of sessions into one line
        self.assertRegex(o["xhDay"]["stamp"], r", \d{1,2} (AM|PM)$")
        self.assertTrue(o["xhDay"]["tipShown"], o["xhDay"])
        xd = o["xhDays"]
        self.assertRegex(xd["stamp"], r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} \d{1,2}$")
        self.assertEqual(len(xd["rows"]), 6, "the top several: " + json.dumps(xd["rows"]))
        self.assertRegex(xd["more"] or "", r"^\+\d+ more$")
        tv = [_num(r["v"]) for r in xd["rows"]]
        self.assertEqual(tv, sorted(tv, reverse=True), "spend order: " + json.dumps(xd["rows"]))
        self.assertGreater(tv[0], tv[-1], "rows whose values differ, so the order pin can fail (review find: the hourly rows tied at $1)")
        # a short window: with no room below the pointer the box goes above the chart, never over the stamp
        xs = o["xhShort"]
        self.assertTrue(xs["tipShown"] and xs["stampShown"], xs)
        tr, sr = xs["tipRect"], xs["stampRect"]
        self.assertLess(tr["bottom"], sr["top"], "the box sits above the chart, clear of the stamp: " + json.dumps({"tip": tr, "stamp": sr}))
        # over a bar: the same box with the bar's row emphasised and named, the line still up
        self.assertTrue(o["tipOn"]["name"], o["tipOn"])
        self.assertTrue(o["tipOn"]["lineShown"])
        self.assertTrue(o["hiddenAfter"], "Escape closes it")
        self.assertTrue(o["hiddenAfterTap"], "a backdrop tap closes it")
        self.assertFalse(o["hiddenAfterDrag"], "…but a drag-select that ends over the backdrop does not (review find)")
        self.assertTrue(o["mobile"]["railHidden"] and o["mobile"]["panelOpened"] and o["mobile"]["modalOpened"],
                        "on a phone the Usage panel's 'By session' button is the door (review find): " + json.dumps(o["mobile"]))
        self.assertEqual(o["lightErr"], "rgb(154, 51, 36)", "the error line has a light-theme step")
        # T247b: the pressed toggle in the light theme wears the accent chip with --accent-fg text and no hairline
        self.assertEqual(o["lightBtn"], {"color": "rgb(255, 248, 242)", "border": "rgba(0, 0, 0, 0)", "bg": "rgb(194, 65, 12)"}, o["lightBtn"])
        # T247b: dim once — the annotation inside a dead row is at the row's level
        self.assertEqual(o["dim"]["row"], "0.55", o["dim"])
        self.assertEqual(o["dim"]["ann"], "1", "no second dimming inside a dimmed row")
        # T247b: the loader's backstop lands on the error + retry path, and names the timeout (the AbortError
        # mapping is pinned, not just the shared prefix — review find); a re-open over a pending fetch keeps
        # the loader up: the superseded fetch's abort paints nothing (review find)
        self.assertTrue(o["timeout"]["err"] and "no answer from the kernel after 1 s" in o["timeout"]["err"], o["timeout"])
        self.assertTrue(o["timeout"]["retry"])
        self.assertEqual(o["timeout"]["early"], {"err": False, "loader": True}, o["timeout"])
        # T247b: the phone's door is a real button — the hover's size, full opacity, not an annotation
        self.assertEqual(o["mobile"]["btn"]["font"], "11px", o["mobile"]["btn"])
        self.assertEqual(o["mobile"]["btn"]["opacity"], "1", o["mobile"]["btn"])
        self.assertIs(o["mobile"]["btn"]["inAge"], False)
        own = [e for e in o["errs"] if "rsp" in e or "Spend" in e or "spend" in e]
        self.assertEqual(own, [], "no page errors from the modal's own code")


if __name__ == "__main__":
    unittest.main()
