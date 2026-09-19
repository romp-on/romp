"""Connection-status reporting (the user 2026-06-27): a real network drop used to blind-reload each pane
iframe into a dead page, so the dashboard silently froze (the timeline "stopped moving") with no explanation.
Now each pane reports its WebSocket state to the shell, and the panes RECONNECT (retry) instead of
blind-reloading — reloading to resync only once the socket is actually back.

The shell surface changed on 2026-07-27: the fixed top "Disconnected — reconnecting…" banner is gone —
connection drops now log entries in the shell's NOTIFICATION CENTER (the bell in the bottom bar, red while
anything is unread or a visible pane is down), whose behavioral tests live in test_error_center.py. The
source pins here cover the pane shim and the shell's wiring of the center; the hidden-pane test runs the
served paneHidden() under node."""
import inspect
import json
import os
import re
import shutil
import subprocess
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class DisconnectBanner(unittest.TestCase):
    def test_pane_shim_reports_state_and_reconnects(self):
        js = km._shim("chat")
        # reports up/down to the shell
        self.assertIn('postMessage({romp:"wsState",app:APP,state:s}', js)
        self.assertIn('ws.onopen=function(){lastRecv=Date.now();openT=lastRecv;openSock=this;netState("up");', js)   # lastRecv stamp → heartbeat watchdog; openT/openSock → the close rule + ws
        self.assertIn('ws.onclose=function(ev){netState("down");', js)   # reconnects on close (wsdown loader + retry follow)
        # T217: an ANNOUNCED death redials tight; since 2026-09-07 so does a close landing within STALE_MS
        # of a foreground (the FIN a frozen tab thawed into); the blind 1.5s stays for unannounced drops
        self.assertIn("var inWin=Date.now()-foregroundedAt<STALE_MS,d=1500;", js)
        self.assertIn("if(inWin){d=eagerDial?0:250;eagerDial=false;}", js)   # the FIRST close after a foreground redials now; every further one in the window waits 250 ms
        self.assertIn("if(restartAnnounced&&Date.now()-restartAnnounced<30000)d=Math.min(d,250);", js)
        self.assertIn("setTimeout(connect,d);", js)
        self.assertIn("ws.onerror=function(){try{ws.close();}catch(e){}};", js)
        # a RECONNECT no longer silently reloads (the user 2026-07-05): it PROMPTS via raiseStale, and the fresh
        # socket resyncs live. The old auto-reload-on-reopen is gone.
        # the reconnect ALSO fires romp:wsup, which is what takes the pane loader back down
        # (test_pane_loader_reconnect.py owns that half)
        # …and ARMS the retire (freshPending) so the prompt clears itself when the resync frame lands
        # (the user 2026-08-01) — see test_the_connection_prompt_retires_when_the_resync_lands
        self.assertIn('if(wasReconn){var ann=restartAnnounced&&Date.now()-restartAnnounced<30000;'
                      'restartAnnounced=0;', js)   # T217: the announced-restart latch spends inside the gate
        self.assertIn('if(!ann)armStale(pendingWhy||"reconnect");', js)
        self.assertIn('try{window.dispatchEvent(new Event("romp:wsup"));}catch(e){}\nenqueue({type:"wsup"});}', js)
        self.assertNotIn("if(everConnected){location.reload();return;}", js,
                         "the silent auto-reload-on-reconnect is replaced by a reload PROMPT")
        self.assertNotIn("ws.onclose=function(){setTimeout(function(){location.reload();},1500);};", js,
                         "the old blind-reload-on-close is gone")

    def test_shim_prompts_reload_on_reconnect_and_stale_foreground(self):
        js = km._shim("feed")
        # raiseStale routes to the shell's #rstale banner when embedded (a pane iframe), else self-injects.
        self.assertIn('window.parent.postMessage({romp:"wsStale"}', js, "embedded pane hands off to the shell banner")
        self.assertIn("function selfStale()", js, "standalone page (no shell) gets its own reload bar")
        self.assertIn("romp-stale-self", js)
        # visibility fast-path: a foregrounded tab whose socket is dead/quiet forces a reconnect to resync and
        # hands that reconnect its reason; the reconnect's arm then follows the same two events as any other
        # (a keepalive or a close before the resync), and the resync frame disarms it (the user 2026-08-01).
        self.assertIn('document.addEventListener("visibilitychange"', js)
        # 2026-09-07: the handler latches foregroundedAt, names its verdict (`stale`, filed as the return
        # breadcrumb's decision) and only then hands the reconnect its reason — the test itself is unchanged
        self.assertIn("var stale=!ws||ws.readyState!==1||Date.now()-lastRecv>STALE_MS;", js)
        self.assertIn('if(!stale){returnRow=row;returnDiag("return",row);return;}', js)   # held: a FIN queued in the thaw burst re-files it
        self.assertIn('pendingWhy="foreground";freshPending=true;', js)

    def test_a_hidden_pane_never_raises_the_stale_banner(self):
        # the user 2026-08-15, on the phone: the mobile shell shows ONE pane, hiding the rest with
        # display:none; iOS throttles the hidden iframes' JS, so each hidden pane's watchdog kept
        # force-closing its own healthy socket and re-raising the banner every ~45s over a dashboard
        # that was visibly working. raiseStale asks paneHidden() at raise time (no event exists for a CSS
        # display flip) and stays silent while hidden; a pane shown while genuinely stale re-raises within
        # one watchdog tick, now visible.
        # Whether the user can see a pane has TWO witnesses, and paneHidden() reads their union. The shim's
        # own witness is the zero-viewport probe (a display:none iframe has a zero viewport), right for a pane
        # hidden since load in any browser and in Firefox always. In Chromium a display:none iframe keeps the
        # size of its last show, so once the user has looked at a pane (the phone shell's every tab switch)
        # the probe reads it as shown; the pane's own visibility code sees that case and publishes
        # document.hidden OR its IntersectionObserver's last word as window.__rompPaneHidden, a boolean
        # (ui/webview/paint-gate.ts publishPaneHidden for the feed and Outline panes, ui/webview/chat-visibility.ts
        # for the chat page, the timeline's _publishPaneHidden), on its own events and never on a timer. Firefox
        # does not run an IntersectionObserver inside a display:none frame, so there the word can go stale while
        # the probe is right: hidden is EITHER witness, never the word first. The served function runs here
        # under node over window stand-ins; tests/test_pane_hidden_word_browser.py drives real browsers.
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        js = km._shim("feed")
        m = re.search(r"^function paneHidden\(\)\{[^\n]*$", js, re.M)
        self.assertIsNotNone(m, "the shim's paneHidden is one line of the served shim")
        fx = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, fx, ignore_errors=True)
        with open(os.path.join(fx, "run.js"), "w") as f:
            f.write(r"""
var vm = require("vm"), fs = require("fs");
var fn = fs.readFileSync(process.argv[2], "utf8");
function verdict(framed, iw, ih, word) {
  var win = { innerWidth: iw, innerHeight: ih };
  win.parent = framed ? {} : win;
  if (word !== undefined) win.__rompPaneHidden = word;
  return vm.runInNewContext(fn + "\npaneHidden();", { window: win });
}
process.stdout.write(JSON.stringify({
  neverShown: verdict(true, 0, 0, undefined),
  shown: verdict(true, 600, 400, undefined),
  wordHidden: verdict(true, 600, 400, true),
  wordShown: verdict(true, 600, 400, false),
  staleWord: verdict(true, 0, 0, false),
  standaloneWord: verdict(false, 600, 400, true),
  standaloneNoWord: verdict(false, 0, 0, undefined),
  nonBoolean: verdict(true, 600, 400, "yes"),
}));
""")
        with open(os.path.join(fx, "paneHidden.js"), "w") as f:
            f.write(m.group(0))
        r = subprocess.run([node, os.path.join(fx, "run.js"), os.path.join(fx, "paneHidden.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        v = json.loads(r.stdout)
        self.assertTrue(v["neverShown"], "a framed pane with a zero viewport: hidden since load, by the probe")
        self.assertFalse(v["shown"], "a framed pane with a viewport and no word: shown")
        self.assertTrue(v["wordHidden"], "a viewport and a word of true: hidden after a first show (Chromium keeps the iframe's size)")
        self.assertFalse(v["wordShown"], "a viewport and a word of false: shown")
        self.assertTrue(v["staleWord"], "a zero viewport and a stale word of false: hidden (Firefox zeroes the viewport and stalls the observer)")
        self.assertTrue(v["standaloneWord"], "a standalone page's word is its tab's hiding; the probe never applies there")
        self.assertFalse(v["standaloneNoWord"], "a standalone page with no word: never hidden by the probe")
        self.assertFalse(v["nonBoolean"], "only a boolean true is the word")
        self.assertIn('function raiseStale(why){if(paneHidden()){staleDiag("stale-suppressed-hidden",why);return;}', js,
                      "the visibility gate is at RAISE time, so hidden panes reconnect silently")
        # the other end of the word: the pane's shared publisher writes the flag under the name read above, on events
        gate = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "paint-gate.ts"), encoding="utf-8").read()
        self.assertIn("export function publishPaneHidden(", gate, "the publisher, by name, in the pane's paint gate")
        self.assertRegex(gate, r"\.__rompPaneHidden = ", "publishing the flag the shim reads, under that exact name")
        self.assertNotIn("setInterval", gate, "published on the gate's own events, never a timer")

    def test_every_stale_raise_leaves_a_breadcrumb_naming_pane_and_path(self):
        # the user 2026-08-15: the flapping-banner repro was Chrome-on-Android after an iOS-shaped
        # diagnosis — the next report must carry recorded evidence. Every raise (and every hidden-pane
        # suppression, and every watchdog force-close) posts a clientDiag with the pane, the arming
        # path (reconnect/foreground), the socket state and the quiet gap; send() queues while the
        # socket is down, so the breadcrumb survives the drop it describes.
        js = km._shim("feed")
        self.assertIn('send({type:"clientDiag",surface:"pane-shim",what:what,', js)
        self.assertIn('staleDiag("stale-raise",why);', js)
        self.assertIn('staleDiag("stale-suppressed-hidden",why);', js)
        self.assertIn('staleDiag("watchdog-close","quiet");', js)
        self.assertIn('armStale(pendingWhy||"reconnect");', js)
        self.assertIn('pendingWhy="foreground";', js)
        # …and every close the BROWSER reports leaves one too, with the close code/reason and the socket's
        # age: a kernel-side drop (1006, no reason), a clean restart and a proxy timeout were
        # indistinguishable. (A socket the shim ABANDONS leaves none — abandon() disowns its onclose; the
        # watchdog-close row above went down the quiet socket before the abandon, and an armed socket's
        # "-quiet" raise rides the redial.)
        self.assertIn('if(openSock===this){armFresh();try{send({type:"clientDiag",surface:"pane-shim",what:"wsclose",data:{app:APP,code:ev?ev.code:-1,'
                      'reason:(ev&&ev.reason)||"",wasClean:!!(ev&&ev.wasClean),'
                      'sinceOpenMs:openT?Date.now()-openT:-1,quietMs:lastRecv?Date.now()-lastRecv:-1,everConnected:everConnected,'
                      'bundleReady:bundleReady,readyAcked:readyAcked,readyQueued:readyQueued}', js,
                      "the row carries the dial term's inputs at the close (everConnected is true on every such row, so alone it "
                      "told a declared redial from nothing); with the kernel's per-row stamp of what the redial then dialed, "
                      "the log names the redial's kind (tests/test_client_diag_reconnect_stamp.py runs the shapes)")
        # …for a socket that OPENED. A handshake that never opened fires onclose too — every redial of an
        # outage, ~19k in 8 h — and those are counted and reported as ONE row on the next open, never queued
        # one by one; queued breadcrumbs are capped besides. pane-shim-stale.test.ts runs both.
        self.assertIn('else{if(!failedConnects)firstFailT=Date.now();failedConnects++;}', js)
        self.assertIn('if(failedConnects){send({type:"clientDiag",surface:"pane-shim",what:"wsconnfail",data:{app:APP,attempts:failedConnects,firstFailMs:Date.now()-firstFailT}});failedConnects=0;firstFailT=0;}', js)
        self.assertIn('if(m&&m.type==="clientDiag"){if(queuedDiag>=DIAG_QUEUE_MAX)return;queuedDiag++;}', js)

    def test_shim_reconnect_loop_cannot_die(self):
        # The retry chain used to hang entirely off onclose, and the watchdog only ever closed OPEN
        # sockets — so an attempt the browser held in CONNECTING (Firefox delays re-admitting a
        # recently-failed endpoint after a kernel restart) had NOTHING driving it forward, and the
        # "Disconnected — reconnecting…" banner sat until a manual refresh (the user 2026-07-21).
        js = km._shim("chat")
        # connect() is idempotent (one live attempt at a time) and stamps its start time
        self.assertIn("function connect(){if(ws&&(ws.readyState===0||ws.readyState===1))return;", js)
        self.assertIn("connT=Date.now();", js)
        # the watchdog handles EVERY socket state: half-open OPEN, stuck CONNECTING, and lost-timer CLOSED.
        # A quiet OPEN socket is ABANDONED and redialed in the same tick (2026-09-02): close() alone
        # starts a closing handshake a dead far side never answers, and the browser holds CLOSING ~60s
        # before onclose — the audited phone panes came back 64s after their own watchdog-close. The bound is
        # PROVISIONAL_MS while a resumed keep awaits its confirming frame, STALE_MS otherwise (review find, 2026-09-08).
        self.assertIn('if(ws.readyState===1){var bound=resumeProvisional?PROVISIONAL_MS:STALE_MS;if(everConnected&&Date.now()-lastRecv>bound){staleDiag("watchdog-close","quiet");abandon();connect();}return;}', js)
        self.assertIn("function abandon(){var d=ws;if(!d)return;d.onopen=d.onmessage=d.onclose=d.onerror=null;try{d.close();}catch(e){}ws=null;", js)
        self.assertIn('netState("down");try{window.dispatchEvent(new Event("romp:wsdown"));}catch(e){}}', js,
                      "the abandoned socket's onclose is disowned, so abandon() itself does what onclose did (banner + close rule + loader)")
        self.assertIn("if(ws.readyState===0&&Date.now()-connT>15000){try{ws.close();}catch(e){}return;}", js)
        self.assertIn("if(ws.readyState===3&&Date.now()-connT>8000){connect();}", js)
        # the foregrounding fast-path abandons an OPEN-but-quiet socket and redials it NOW (same 60s
        # CLOSING stall otherwise), aborts a stuck CONNECTING one, and re-dials a closed one
        self.assertIn("if(ws&&ws.readyState===1)abandon();else{try{if(ws&&ws.readyState===0)ws.close();}catch(e){}}", js)
        self.assertIn("if(!ws||ws.readyState===3)connect();", js)

    def test_error_popover_has_no_reload_button(self):
        # the popover briefly inherited the old offline banner's Reload button; the user called it
        # redundant next to the rail's own restart control and a plain browser refresh (2026-07-27),
        # so it is gone — and nothing in the popover reloads the page on its own.
        land = inspect.getsource(km._landing)
        self.assertNotIn("rerr-reload", land)
        self.assertNotIn("location.reload", km._LANDING_ERRS_JS)

    def test_shell_rstale_banner_shows_on_ws_stale_message(self):
        # the #rstale reload banner (formerly build-drift only) now ALSO shows on a pane's wsStale post, with a
        # connection-specific message, latched so the /version poll can't clear it out from under the user.
        self.assertIn("m.romp==='wsStale'", km._STALE_JS)
        self.assertIn("connStale=true", km._STALE_JS)
        self.assertIn("served<=loaded&&!connStale", km._STALE_JS, "the version poll must not hide a live-conn prompt")
        self.assertIn("connStale=false", km._STALE_JS, "Dismiss clears the latch")

    def test_shell_banner_words_a_build_raise_as_a_build(self):
        # a pane's build-drift raise rides the same wsStale channel tagged build:1 → the shell shows
        # BUILDMSG (not the connection wording), and latches it so its own /version poll — whose token
        # may be current — can't clear the prompt out from under the stale pane (the user 2026-07-13)
        # T265 (the user 2026-09-08): a build:1 is handed to the reload core; since 2026-09-16 the core is asked
        # for the authoritative /version reading, which it OFFERS (the same box paints the offer); the BUILDMSG
        # wording shows only where the core is absent
        self.assertIn("if(m&&m.romp==='wsStale'){if(m.build){if(RL)RL.checkBoot();else buildStale=true;}else connStale=true;paint();}", km._STALE_JS)
        self.assertIn("!connStale&&!buildStale", km._STALE_JS, "the poll's clear respects both latches")
        self.assertIn("connStale=false;buildStale=false;paint();", km._STALE_JS, "Dismiss clears both")

    def test_the_connection_prompt_retires_when_the_resync_lands(self):
        """The prompt must go away on its own once what it warns about is over (the user 2026-08-01).

        It popped on nearly every dashboard open — a pane whose socket dropped-and-reconnected raises it —
        and then sat there offering a reload for staleness the kernel's connect-time push had already
        healed in the background. So the retire keys on that push: the FIRST non-keepalive frame after a
        reconnect IS the resync, and it clears the prompt. An event, not a timer, and not a silent
        auto-reload — the reload prompt still stands whenever the resync genuinely never arrives."""
        js = km._shim("feed", 7777)
        self.assertIn("freshPending=true", js, "a reconnect arms the retire")
        # …and the prompt is ARMED, not shown (the user 2026-08-01): raising it at once made it flash up
        # and straight back down on nearly every dashboard open, since the resync lands within a beat.
        # The arm is EVENT-keyed (it was a 1s timer, which fired a beat before the resync on nearly every
        # reconnect once frames grew): it raises on the SECOND KEEPALIVE arriving while the resync is still
        # pending — one full heartbeat period on this socket with the kernel alive, talking to it, and not
        # resyncing it; a single keepalive can be a beat queued at accept, ahead of the resync frame — or on
        # the reconnected socket CLOSING again before its resync — or being ABANDONED by the shim before it (the
        # watchdog's tick or the foreground path, on a socket the kernel accepted and never spoke on: abandon()
        # disowns the socket's onclose, so it runs the close rule itself; without that, every silent cycle
        # re-armed from zero and the prompt never came). Nothing else. pane-shim-stale.test.ts RUNS the rule;
        # these pins hold its text.
        self.assertIn("function armStale(why){if(NOSTALE)return;stalePending=why;staleKa=0;}", js,
                      "arming records the path, shows nothing (a page with no pushed view, NOSTALE, never arms: the Files pane)")
        self.assertIn("var NOSTALE=false;", js, "every pushed pane keeps the arm")
        self.assertNotIn("setTimeout(function(){staleTimer=0;raiseStale(why);},1000)", js, "the timer is gone")
        self.assertNotIn("staleTimer", js)
        self.assertIn('if(stalePending&&++staleKa>=2){var sw=stalePending;stalePending="";raiseStale(sw);}', js,
                      "the second keepalive on the reconnected socket, resync still pending → raise")
        self.assertIn('if(stalePending&&openSock===this){var cw=stalePending;stalePending="";raiseStale(cw+"-closed");}', js,
                      "the reconnected socket closing before its resync → raise")
        self.assertIn('if(stalePending&&openSock===d){var qw=stalePending;stalePending="";raiseStale(qw+"-quiet");}', js,
                      "the reconnected socket abandoned as quiet before its resync → raise (the disowned onclose cannot)")
        self.assertIn('if(!ann)armStale(pendingWhy||"reconnect");', js,
                      "the reconnect ARMS it — except the one an ANNOUNCED restart already explained (T217)")
        self.assertNotIn("if(wasReconn){raiseStale();", js, "…and never raises it outright")
        self.assertIn('function clearStale(){stalePending="";', js,
                      "the resync disarms it, so it never appears at all")
        self.assertIn("if(freshPending){freshPending=false;window.__rompFreshPending=false;clearStale();try{if(window.__rompReload)window.__rompReload.ended();}catch(e){}}", js,   # the reload core's fresh hold ends here too (invisible restarts, 2026-09-14)
                      "the first real frame after it fires the retire")
        # keepalives must NOT count as a resync — the ka branch returns before the retire line
        self.assertLess(js.index('msg.type==="ka"'), js.index("if(freshPending)"),
                        "the keepalive early-return sits ahead of the retire")
        self.assertIn('window.parent.postMessage({romp:"wsFresh"}', js, "embedded pane tells the shell")
        # …and the shell drops the connection prompt on it, while a BUILD offer survives (a resync
        # delivers state, never new code — only a reload answers that one): the painter shows the offer again
        self.assertIn("m.romp==='wsFresh'", km._STALE_JS)
        self.assertIn("else if(m&&m.romp==='wsFresh'){connStale=false;paint();}", km._STALE_JS)
        self.assertIn("else if(offer){box.classList.add('offer');dm.textContent='Not now';show(offer.text);}", km._STALE_JS)

    def test_the_foregrounded_tab_path_arms_the_same_way(self):
        # a tab foregrounded onto a dead socket forces a reconnect and used to prompt immediately; that
        # reconnect resyncs like any other, so it arms the same window and disarms on the same frame
        js = km._shim("chat", 7777)
        # 2026-09-07: the handler latches foregroundedAt, names its verdict (`stale`, filed as the return
        # breadcrumb's decision) and only then hands the reconnect its reason — the test itself is unchanged
        self.assertIn("var stale=!ws||ws.readyState!==1||Date.now()-lastRecv>STALE_MS;", js)
        self.assertIn('if(!stale){returnRow=row;returnDiag("return",row);return;}', js)   # held: a FIN queued in the thaw burst re-files it
        self.assertIn('pendingWhy="foreground";freshPending=true;', js)
        self.assertNotIn("STALE_MS){raiseStale();", js)

    def test_a_standalone_page_retires_only_its_connection_bar(self):
        # the shell-less case (feed/timeline opened directly) self-injects the same prompt; the retire must
        # remove ONLY the connection one, so a build-drift bar is never cleared by a mere resync
        js = km._shim("timeline", 7777)
        self.assertIn('b.dataset.kind=kind||"conn"', js, "the self-injected bar records which prompt it is")
        self.assertIn('selfBar("romp lost the live connection, so what you see may be stale.","conn")', js)
        self.assertIn('selfBar("A newer romp build is available.","build")', js)
        self.assertIn('if(b&&b.dataset.kind==="conn"){b.remove();', js, "only the conn bar retires")
        self.assertIn('if(RO&&RO.offer&&RO.offered())RO.offer(RO.offered());', js, "…and a reload offer that yielded its slot to it comes back (2026-09-16)")

    def test_timeline_page_uses_the_shared_shim(self):
        # The timeline used to hand-roll its own WebSocket in _TIMELINE_BOOT (a copy of _shim's
        # connect/queue/watchdog) — which bypassed the federation manager, so remote hosts' lanes never
        # reached the pane. Now the page carries the SAME shim as every other pane (state reporting,
        # reconnect, reload-on-reopen all come from it) + the federation bundle; the boot only adapts
        # window "message" events to panel.update/applyBars and posts actions via acquireVsCodeApi.
        self.assertNotIn("new WebSocket", km._TIMELINE_BOOT, "the boot owns no socket of its own")
        page = km._timeline_page()
        self.assertIn('postMessage({romp:"wsState",app:APP,state:s}', page, "the shared shim reports state")
        self.assertIn('/ws?app=timeline', page, "the shim is instantiated for the timeline app")
        self.assertIn("/dist/federation.js", page, "the multi-kernel manager rides the page")
        self.assertIn('window.addEventListener("message"', km._TIMELINE_BOOT)
        self.assertIn("panel.update(m.data)", km._TIMELINE_BOOT)

    def test_shell_mounts_the_notification_center(self):
        # the shell listens for pane wsState posts and routes drops into the notification center — the
        # old fixed top banner is GONE (it got in the way, the user 2026-07-27)
        self.assertIn("var s=(m.state==='up')?'up':'down',prev=st[m.app];st[m.app]=s;", km._LANDING_ERRS_JS)
        land = inspect.getsource(km._landing)
        self.assertNotIn("id=rail-errs", land, "the Log's opener left the bottom bar's action cluster (T290): it opens from the gear")
        self.assertIn("if(m.romp==='openLog'&&window.__rompOpenErrs)window.__rompOpenErrs();", km._LANDING_SETTINGS_JS,
                      "the gear's Open log reaches the shell's panel")
        self.assertIn("if(!back||!list)return;", km._LANDING_ERRS_JS, "the center's script stands without the bar icon")
        self.assertIn("id=rerr-back", land, "the popover backdrop is in the shell body")
        self.assertIn("_LANDING_ERRS_JS", land, "the center's script is injected into the shell")
        self.assertNotIn("id=romp-offline", land, "the top banner element is gone")
        self.assertNotIn("Disconnected — reconnecting…", land)

    def test_only_visible_panes_count(self):
        # a pane toggled OFF still holds a live socket (the Fleet pane is hidden by default, its iframe always
        # loaded), so a blip on a pane you can't even see must NOT log an error / redden the bell while the
        # chat pane you interact through is up (the user 2026-07-06). Gate on the pane-enabled body class
        # the toggle sets (po-chat/po-feed/po-timeline/po-fleet), and re-check when panes toggle.
        js = km._LANDING_ERRS_JS
        self.assertIn("function shown(k){return document.body.classList.contains('po-'+k);}", js)
        self.assertIn("if(st[k]==='down'&&shown(k))return true;", js, "a down pane counts only while visible")
        self.assertIn("shown(m.app)", js, "and only a visible pane's drop logs an entry")
        self.assertIn("window.addEventListener('romp-panes',paint)", js, "re-check the cue on pane toggle")
        # the pane toggle actually fires the romp-panes event this listens for, and both scripts ride the shell
        self.assertIn("new Event('romp-panes')", km._LANDING_COLLAPSE_JS)
        land = inspect.getsource(km._landing)
        self.assertIn("_LANDING_COLLAPSE_JS", land)
        self.assertIn("_LANDING_ERRS_JS", land)


if __name__ == "__main__":
    unittest.main()
