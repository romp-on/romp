#!/usr/bin/env python3
"""The Files pane (app=files): the file viewer as a dashboard column of its own, beside Chat,
Sessions, Outline and Feed, off by default, with the open Files pane taking a file click (the file-links setting is gone since T404) and the pane routing a
chat file-link click into it. The kernel side, pinned here:

- the pane is not a feed consumer. The viewer is request/response (HTTP /file for the bytes; the
  saveFile and fileGitLink ops answer the sending client), so app=files is outside the feed audience
  and _push builds nothing for it; it counts only where a live pane must count, the conserve-memory
  viewer check.
- the /files page: the chat's styles.css for the viewer's dress, files-pane.css read live for the
  layout and the pane-resident variant (body.fileview-pane), no romp loader (an empty pane is not a
  loading state), the shim with the stale opt-out (a page that receives no pushed view never arms
  the shared "may be stale" prompt), federation.js before files.js.
- the shell: a fifth column after Feed, off by default, with its gutter, grow var, focus, Escape and
  mobile wiring, and the _PANE_ORDER label "Files"; the viewFile relay's pane arm, which brings the
  pane forward and forwards the click, identity included, into it. The arms are executed under node
  in tests/test_pane_state_broadcast.py; this module pins the served pages and the kernel's shape.
- the browseFiles relay's pane branch (BrowseRelay below, executed under node): a folder clicked in the
  chat while the Files pane is on screen, or while the gear names it, posts browseFiles up with
  pane:'pane', and the shell brings the pane forward and forwards the ask, identity included, the way
  the viewFile branch does; a browseFiles naming no pane still takes the feed's route.

Synthetic fixtures only (the notes-api demo world, placeholder sids); nothing here mints a goal.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from romp_load import load_source


def _has(tc, needle, text, msg=""):
    """assertIn without the dump: a failure names the needle, never a whole page or source file."""
    tc.assertTrue(needle in text, msg or ("missing: %r" % needle))


def _lacks(tc, needle, text, msg=""):
    tc.assertFalse(needle in text, msg or ("present: %r" % needle))

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
km = load_source("romp_kernel_fpane", os.path.join(BIN, "romp-kernel"))
SRC = open(os.path.join(BIN, "romp-kernel")).read()
UI = Path(ROOT) / "ui" / "webview"


class _Backend:
    """The conserve-memory pass's backend, with nothing running: the pass then only records
    whether a viewer is connected, which is the one fact these tests read."""

    def running_sids(self):
        return []

    def live_sessions(self):
        return {}

    def conserve_idle(self, sid):
        return True

    def conserve_close(self, sid):
        return False


class Plumbing(unittest.TestCase):
    """app=files is a viewer to the kernel, not a feed client: keepalives and its own op replies ride
    its socket, nothing is built for it, and it counts as a live dashboard for conserve-memory."""

    def test_files_is_outside_the_feed_audience(self):
        # the ONE question _push asks of its targets (want_feed) and the route asks of the connected set
        self.assertFalse(km._feed_audience([{"app": "files"}]))
        self.assertTrue(km._feed_audience([{"app": "feed"}]))
        self.assertTrue(km._feed_audience([{"app": "files"}, {"app": "chat"}]))
        push = SRC[SRC.index("def _push(targets"):]
        push = push[:push.index("\ndef ")]
        _lacks(self, '"files"', push, "_push names every app it builds for; the Files pane is not one")

    def test_the_socket_accepts_any_app_name(self):
        # the handshake has no allowlist, so app=files connects on a kernel exactly as the other panes do
        _has(self, 'app = (q.get("app") or ["chat"])[0]', SRC)

    def test_an_open_files_pane_counts_as_a_viewer_for_conserve_memory(self):
        # executed: the pass stamps the last-viewer clock when a Files pane is the only client; a socket
        # with no pane behind it (the shell's) does not
        def tick(app, now):
            clients = [{"app": app, "alive": True}]
            with mock.patch.object(km, "_conserve_on", lambda: True), \
                 mock.patch.object(km, "_sdk", lambda: _Backend()), \
                 mock.patch.object(km, "_views_client", lambda: {}), \
                 mock.patch.object(km, "_clients", clients), \
                 mock.patch.object(km, "_conserve_last_viewer", [0]) as last:
                km._conserve_tick(now)
                return last[0]
        self.assertEqual(tick("files", 4242), 4242, "a Files pane is a viewer")
        self.assertEqual(tick("feed", 4243), 4243)
        self.assertEqual(tick("shell", 4244), 0, "the shell's own socket is not a pane")

    def test_the_page_carries_the_shared_dress_the_stale_opt_out_and_no_loader(self):
        page = km._files_page()
        _has(self, "app=files", page)
        _has(self, "var NOSTALE=true;", page, "the shim's stale opt-out is on for this page")
        _has(self, "/dist/styles.css", page)   # the viewer's .fileview-* dress
        _has(self, "<body class=fileview-pane>", page)   # keys the pane-resident variant
        _has(self, "<div id=files-empty></div>", page)
        _has(self, "/dist/federation.js", page)
        _has(self, "/dist/files.js", page)
        self.assertLess(page.index("/dist/federation.js"), page.index("/dist/files.js"), "manager before the bundle")
        _lacks(self, "id=pane-spin", page, "an empty pane is not a loading state")
        _lacks(self, "rel=manifest", page, "a pane, not an install target")
        _has(self, 'if p == "/files":', SRC)
        _has(self, "_files_page()", SRC)
        # the sheet is read live, like fleet-pane.css; a missing one fails loudly on the page, never blank
        css = (UI / "files-pane.css").read_text()
        _has(self, css.splitlines()[-1], page)
        with mock.patch.object(Path, "read_text", side_effect=OSError("gone")):
            _has(self, "needs the ui/ modules", km._files_page())

    def test_the_page_is_served_on_its_route(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/files?token=testtok" % srv.server_address[1], timeout=5) as r:
                self.assertEqual(r.status, 200)
                _has(self, "text/html", r.headers.get("Content-Type", ""))
                body = r.read().decode("utf-8", "replace")
        finally:
            srv.shutdown()
        _has(self, "<body class=fileview-pane>", body)
        _has(self, "/dist/files.js", body)
        _has(self, "body.fileview-pane #romp-fileview{", body, "files-pane.css is inlined live")

    def test_the_stale_opt_out_is_a_keyword_only_this_page_passes(self):
        """The shim arms the "connection lost, what you see may be stale" prompt on an unannounced
        reconnect and retires it on the kernel's connect-time push, the first non-keepalive frame.
        app=files gets no such push (nothing is built for it), so the arm would never clear and the
        second keepalive would raise the shell's shared banner after every unannounced reconnect, for a
        file fetched over HTTP on demand, which a dropped socket cannot make stale. The page renders the
        shim with no_stale=True, which bakes NOSTALE into the template so armStale and clearStale return
        early (the executed state machine is ui/webview/pane-shim-stale.test.ts). The build-drift prompt
        is a separate raise and stands. Every other pane has a live pushed view and keeps the arm."""
        on = km._shim("files", 1, no_stale=True)
        _has(self, "var NOSTALE=true;", on)
        _has(self, "function armStale(why){if(NOSTALE)return;stalePending=why;staleKa=0;}", on)
        _has(self, 'function clearStale(){stalePending="";   // armed but never shown → nothing to see\nif(NOSTALE)return;', on)
        _has(self, "function raiseBuild(){if(buildRaised)return;buildRaised=true;", on, "the build prompt is not gated")
        _has(self, "var NOSTALE=false;", km._shim("feed", 1), "the default keeps the arm")
        for page in (km._chat_page(), km._feed_page(), km._fleet_page(), km._timeline_page()):
            _has(self, "var NOSTALE=false;", page)
            _lacks(self, "var NOSTALE=true;", page)
        # the pages that pass it: this one and the settings page (the gear alone, no pushed view either;
        # tests/test_settings_page.py); the pane pages call the shim exactly as they did
        shims = re.findall(r'_shim\("(\w+)", v(?:, ([^)]*))?\)', SRC)
        self.assertEqual([app for app, kw in shims if "no_stale=True" in (kw or "")], ["files", "settings"])
        self.assertEqual(sorted(app for app, kw in shims), ["chat", "feed", "files", "fleet", "settings", "timeline"])

    def test_the_editor_chunk_derives_from_the_pages_own_bundle_tag(self):
        # file-view.ts loads its CodeMirror chunk from a URL rewritten off the page's running bundle
        # <script src>. The Files page's bundle must be one that derivation recognizes, or every Edit in
        # the pane rejects with the raw "no bundle script tag" error and falls to the textarea. The pattern
        # is lifted from the source and run against the tags this page emits.
        view = (UI / "file-view.ts").read_text()
        m = re.search(r"\.find\(\(u\) => /(.+?)/\.test\(u\)\)", view)
        self.assertIsNotNone(m, "the derivation's find literal is where the pin expects it")
        pat = re.compile(m.group(1))
        srcs = re.findall(r"<script src=([^ >]+)", km._files_page())
        self.assertTrue(srcs)
        hits = [s for s in srcs if pat.search("http://TESTHOST:1" + s)]   # the browser's absolute .src
        self.assertEqual(hits, [s for s in srcs if s.startswith("/dist/files.js?v=")], "the bundle, and only the bundle")

    def test_the_pane_resident_variant_lives_only_in_the_pane_sheet(self):
        # the modal variant is mirrored byte-equal in styles.css and feed.css (fileview-parity.test.ts);
        # the pane's override must not enter either, or the mirrors drift
        css = (UI / "files-pane.css").read_text()
        _has(self, "body.fileview-pane #romp-fileview{position:relative;inset:auto;flex:1 1 auto;min-height:0;background:none}", css)
        _has(self, "body.fileview-pane .fileview{width:100%;height:100%;border:0;border-radius:0;box-shadow:none}", css)
        for sheet in ("styles.css", "feed.css"):
            _lacks(self, "fileview-pane", (UI / sheet).read_text(), sheet)
        _lacks(self, "fleet", css.lower(), "no fleet vocabulary in the new sheet")


class Shell(unittest.TestCase):
    """The dashboard grows a fifth pane: the far-right column after Feed, OFF by default (the viewFile
    relay brings it forward when a click routes there), with its own gutter, grow var, and every
    hand-written pane list in the landing JS extended: focus ring, Alt+Arrow columns, Escape wiring, the
    Log's pane names, the mobile tab map, the pane controller. One label, "Files", from _PANE_ORDER."""

    def setUp(self):
        self.html = km._landing()

    def test_the_pane_is_in_the_one_ordering_last(self):
        self.assertEqual(km._PANE_ORDER[-1], ("files", "Files"))
        _has(self, "<div class=rail-btn data-pane=files>Files</div>", self.html)
        _has(self, "<button data-pane=files>Files</button>", self.html)

    def test_the_column_sits_after_the_feed_with_its_gutter_and_grow_var(self):
        flat = self.html.replace('"\n            "', "")
        _has(self, '<div class=gv id=gv-c></div><div class=pane id=files-pane><iframe id=f-files src=/files></iframe></div>', flat)
        self.assertLess(self.html.index("id=feed-pane"), self.html.index("id=gv-c"))
        self.assertLess(self.html.index("id=gv-c"), self.html.index("id=files-pane"))
        self.assertLess(self.html.index("id=files-pane"), self.html.index("id=gh"), "before the timeline band")
        _has(self, "#files-pane{flex:var(--g-files,40) 1 0}", self.html)
        _has(self, "body:not(.po-files) #files-pane{display:none}", self.html)
        # the gutter shows only between two visible panes: hidden when Files is off, or when no column sits to its left
        _has(self, "body:not(.po-files) #gv-c,body:not(.po-chat):not(.po-fleet):not(.po-feed) #gv-c{display:none}", self.html)

    def test_off_by_default_and_toggled_by_the_controller(self):
        _has(self, "<body class='po-chat po-feed po-timeline'>", self.html)   # not po-files
        _has(self, "po={chat:true,fleet:false,feed:true,timeline:true,files:false}", self.html)
        _has(self, "po={chat:false,fleet:false,feed:false,timeline:false,files:false}", self.html)   # the ?panes= reset
        _has(self, "document.body.classList.toggle('po-files',!!po.files)", self.html)
        _has(self, "files:'files pane'", self.html)   # the rail tooltip's words

    def test_every_pane_list_in_the_landing_js_names_it(self):
        _has(self, "'f-files':'files-pane'", km._LANDING_FOCUS_JS)
        _has(self, "var COLS=['f-chat','f-fleet','f-feed','f-files']", km._LANDING_FOCUS_JS)
        # the settings iframe (the gear's document, not a pane) rides the two keyboard lists with the panes
        _has(self, "['f-chat','f-fleet','f-feed','f-files','f-timeline','f-settings'].forEach", km._LANDING_ESC_JS)
        _has(self, "['f-chat','f-fleet','f-feed','f-files','f-timeline','f-settings'].forEach", km._LANDING_MOBILE_JS)
        # the Log's connection-lost label reads the one map, so the pane's row in _PANE_ORDER is the pin
        _has(self, "var PN=" + json.dumps(dict(km._PANE_ORDER)) + ";", km._LANDING_ERRS_JS)
        self.assertEqual(dict(km._PANE_ORDER).get("files"), "Files")
        _has(self, "files:document.getElementById('f-files')", km._LANDING_MOBILE_JS)
        # the chat area and the first pane are both registered (the chat rows, 2026-09-15): the area on the outer weight, the pane on its inner one
        _has(self, "var PANES=['chat-area','chat-pane','fleet-pane','feed-pane','files-pane'];", self.html)
        _has(self, "grow={chat:60,chat1:60,fleet:34,feed:40,files:40}", self.html)
        _has(self, "var FIXED={'chat-area':'chat','chat-pane':'chat1','fleet-pane':'fleet','feed-pane':'feed','files-pane':'files'};", self.html)   # the files pane's key, in the one map (no other id falls to it: 2026-09-15)
        # the chat side of gv-c is the chat AREA (the chat rows, 2026-09-15: lastChat() is 'chat-area', whatever columns it holds)
        _has(self, "gutter('gv-c',function(){var c=document.body.classList;return c.contains('po-feed')?'feed-pane':"
                      "c.contains('po-fleet')?'fleet-pane':lastChat();},'files-pane');", self.html)

    def test_the_files_controls_own_setting_hides_it_in_both_layouts(self):
        # T317 (the user 2026-09-10): the gear's Files row, "Files control in the dashboard bar" until T407 (romp:settings.showFilesControl,
        # hidden unless the store holds the literal true: OFF by default since T317b; a FRESH key, since the T317-era gear's
        # whole-object save left filesControl: true in any profile that touched a setting). The shell reads the gear's store key itself, hides the
        # rail's toggle and the phone's tab by one body class, closes an open pane on the same apply, refuses to
        # bring the pane forward, tells the panes it is unavailable, and the phone's switcher never shows a hidden
        # tab (a stored romp-mobile-tab, a relay). Executed under node in tests/test_pane_state_broadcast.py.
        _has(self, "body.no-files-control .rail-btn[data-pane=files],body.no-files-control #mtabs button[data-pane=files]{display:none}", self.html)
        js = km._LANDING_COLLAPSE_JS
        _has(self, "function filesCtl(){try{var st=JSON.parse(localStorage.getItem('romp:settings')||'null');return !!(st&&st.showFilesControl===true);}catch(e){return false;}}", js)
        _has(self, "function filesCtlM(){try{var st=JSON.parse(localStorage.getItem('romp:settings')||'null');return !!(st&&st.showFilesControl===true);}catch(e){return false;}}", km._LANDING_MOBILE_JS)   # the phone's read agrees: only the literal true
        self.assertNotIn("st.filesControl", js + km._LANDING_MOBILE_JS, "the T317-era key is never read: a whole-object save merged its true into profiles that never touched the box")
        _has(self, "document.body.classList.toggle('no-files-control',!ctl);", js)
        _has(self, "if(k==='files'&&!filesCtl())return;", js)
        _has(self, "return {romp:'panes',on:on,avail:{files:filesCtl()}};", js)
        _has(self, "window.addEventListener('storage',function(e){if(!e||!e.key||e.key===SK)reconcile(true);apply();});", js)   # the gear writes from another document: this is the event (a gear save re-reads the optional panes before the titles refresh)
        mob = km._LANDING_MOBILE_JS
        _has(self, "function show(p){if(p==='files'&&!filesCtlM())p='chat';", mob)
        # the gear's row, in General's Panes section since T404 (beside the file-links setting before, which is gone), UNCHECKED by default (T317b); the chat's route reads the word
        gear = (UI / "gear.js").read_text()
        _has(self, "<input type=checkbox id=rs-filesctl>", gear)
        self.assertNotIn("id=rs-filesctl checked", gear, "off by default: the box is not pre-checked")
        self.assertEqual(gear.count("showFilesControl: false, stripGroupRows"), 2, "the gear's load defaults (the assign and its catch) say off")
        _has(self, "delete o.filesControl; delete o.fileLinkPane; return o; } catch (e) {", gear)   # load() drops the T317-era key and the T404-era file-links key, so the next save leaves both behind
        # the box has a NAME OF ITS OWN in the gear's one var list (review find: a second `fc` shadowed the feed's
        # collapsed box, so the new row was dead and the feed box wrote this setting)
        _has(self, "fsc = document.getElementById('rs-filesctl')", gear)
        _has(self, "if (fsc) fsc.addEventListener('change', function () { var s = load(); s.showFilesControl = fsc.checked; save(s); });", gear)
        _has(self, "if (fsc) fsc.checked = (s.showFilesControl === true);", gear)
        self.assertEqual(gear.count("fc = document.getElementById("), 1, "fc is the feed's collapsed box alone")
        self.assertEqual(gear.count("fsc = document.getElementById("), 1)
        # the palette's entry for the pane is not listed while the control is hidden (re-read at every open)
        pal = (UI / "palette-main.ts").read_text()
        _has(self, 'when: key === "files" ? () => !document.body.classList.contains("no-files-control")', pal)
        _has(self, ': optional.has(key) ? () => loadSettings().panes[key as keyof PaneSet]', pal)   # the optional panes' own predicate (the gear's Panes section) shares the ternary
        _has(self, "filter((c) => !c.hidden && (!c.when || c.when()))", (UI / "palette.ts").read_text())
        # a ?panes= bookmark stays a view: the forced close is never written over the stored set
        _has(self, "if(!ctl&&po.files){po.files=false;if(qp===null)saveP();}", js)
        self.assertNotIn("id=rs-filelink", gear, "the file-links setting is gone (T404): the route follows the open Files pane")
        self.assertLess(gear.index("id=rs-pane-feed"), gear.index("id=rs-filesctl"), "the row sits in General's Panes section, after the three pane toggles (T404)")
        self.assertLess(gear.index("id=rs-filesctl"), gear.index("data-section=appearance>Appearance<"), "…before the Appearance section")
        render = (UI / "render.ts").read_text()
        _has(self, "fileLinkRoute(window.parent !== window, panesOn.files === true, panesAvail.files !== false)", render)   # no setting since T404
        # the hint in the pane stays true: it speaks of the pane being open or closed, never of the control (its third sentence,
        # which told a reader with the pane open to turn on the control that must already be on, went with T407)
        files = (UI / "files.ts").read_text()
        _has(self, 'hint.textContent = "While this pane is open, a file or folder clicked in the chat opens here. Closed, they open over the pane you clicked.";', files)
        self.assertNotIn("Files control in the dashboard bar", files, "the row is named Files now (T407)")

    def test_mobile_tab_and_the_palette_command(self):
        _has(self, "#chat-pane,#fleet-pane,#feed-pane,#files-pane,#tl-pane{display:contents!important}", self.html)
        _has(self, "#f-chat.m-on,#f-fleet.m-on,#f-feed.m-on,#f-files.m-on{display:block}", self.html)
        pal = (UI / "palette-main.ts").read_text()
        _has(self, '["files", "files"]', pal)
        _has(self, '"f-files"', pal)


class Relay(unittest.TestCase):
    """The shell's message listener gains a viewFile pane arm: a click routed to the Files pane brings
    that pane forward (desktop toggle, phone tab) and forwards the click, identity included, into
    #f-files. The pane stays up, so no restore is owed; the browseFiles arm the feed's browser rides is
    untouched. The arms run under node in tests/test_pane_state_broadcast.py; these pin the shape."""

    HEAD = "if(m.romp==='viewFile'&&m.pane==='pane'){var ff=document.getElementById('f-files');"

    @staticmethod
    def _code(js):
        return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))

    def test_the_pane_arm_brings_the_files_pane_forward_and_forwards_the_identity(self):
        js = km._LANDING_SETTINGS_JS
        _has(self, self.HEAD, js)
        self.assertEqual(js.count("m.romp==='viewFile'"), 1, "one viewFile arm in the shell, aimed at the Files pane")
        self.assertEqual(SRC.count("m.romp==='viewFile'"), 1, "and no other viewFile arm anywhere in the kernel")
        branch = self._code(js.split(self.HEAD)[1].split("if(m.romp==='filesViewerClosed')")[0])
        _has(self, "window.__rompPaneToggle&&window.__rompPaneToggle('files',true)", branch)
        # phone: the Files tab comes forward only in the mobile layout, and the tab the click came from is
        # remembered so the viewer's close puts the person back
        _has(self, "if(window.__rompMobileOn&&window.__rompMobileOn()){var cur=document.body.getAttribute('data-tab')||'chat';", branch)
        _has(self, "if(cur!=='files'){window.__rompFilesTabFrom=cur;window.__rompMobileTab&&window.__rompMobileTab('files');}", branch)
        _has(self, "postMessage({romp:'viewFile',path:m.path,sid:m.sid,identity:m.identity||null,frag:m.frag||null},'*')", branch)   # frag: the section a preview card's open names (T351)
        self.assertEqual(branch.count("postMessage("), 1, "one forward, carrying the whole click")
        for tok in ("__rompFeedWasOff", "'f-feed'", "browseClosed"):
            _lacks(self, tok, branch, tok + " belongs to the feed's browser route")

    def test_the_files_viewers_close_restores_the_remembered_tab_mobile_only(self):
        js = km._LANDING_SETTINGS_JS
        handler = ("if(m.romp==='filesViewerClosed'){var back=window.__rompFilesTabFrom;window.__rompFilesTabFrom=null;\n"
                   "  if(back&&window.__rompMobileOn&&window.__rompMobileOn()){try{window.__rompMobileTab&&window.__rompMobileTab(back);}catch(e){}}}")
        _has(self, handler, js)
        # the sender: files.ts posts the close EDGE up (executed in ui/webview/files.test.ts)
        files = (UI / "files.ts").read_text()
        _has(self, 'if (viewerUp && !up && window.parent !== window) window.parent.postMessage({ romp: "filesViewerClosed" }, "*");', files)

    def test_the_feeds_browse_relay_is_untouched(self):
        js = km._LANDING_SETTINGS_JS
        _has(self, "else if(m.romp==='browseFiles'){var bf=document.getElementById('f-feed');", js)
        _has(self, "try{window.__rompMobileTab&&window.__rompMobileTab('feed');}catch(e){}   // phone: one pane at a time", js)
        _has(self, "if(m.romp==='browseClosed'&&window.__rompFeedWasOff){window.__rompFeedWasOff=false;", js)

    def test_the_two_ends_agree_on_the_message(self):
        # the chat names its target and carries the session's identity (render.ts openPath, through the
        # gesture reader); the pane validates the identity and caches it per sid (files.ts)
        render = (UI / "render.ts").read_text()
        _has(self, 'window.parent.postMessage({ romp: "viewFile", path, sid: to, pane: "pane",', render)
        _has(self, "fileLinkRoute(window.parent !== window, panesOn.files === true, panesAvail.files !== false)", render)   # no setting since T404
        files = (UI / "files.ts").read_text()
        _has(self, "asIdentity(m.identity)", files)
        route = (UI / "file-route.ts").read_text()
        _has(self, "export function fileLinkRoute(framed: boolean, filesOpen: boolean, filesAvail: boolean = true): FileRoute {", route)   # no setting since T404

    def test_the_gear_and_the_guide_say_the_open_pane_wins(self):
        # T404: the file-links row is gone from the gear; the Files control row and the pane's own hint say the rule
        gear = (UI / "gear.js").read_text()
        _has(self, "closes the Files pane if it is open; file links then open over the pane you clicked.", gear)
        self.assertNotIn("<option value=chat>The pane you clicked</option>", gear, "the setting's options are gone")
        _has(self, "While this pane is open, a file or folder clicked in the chat opens here. Closed, they open over the pane you clicked.", (UI / "files.ts").read_text())
        guide = (Path(ROOT) / "docs" / "guide.md").read_text()
        _has(self, "### Files\n", guide)
        _has(self, "While the pane is open, a file link clicked in the chat opens in it.", guide.replace("\n", " "))
        self.assertLess(guide.index("### The outline"), guide.index("### Files"))
        self.assertLess(guide.index("### Files"), guide.index("## Automatic nudges"))


# ── the browseFiles relay's pane branch, executed ──────────────────────────────────────────────────
# The whole settings script runs under node against a fake window and document (the harness
# tests/test_pane_state_broadcast.py RelayArms uses, copied so this module loads on its own); the one message
# listener it registers is driven with the asks the chat posts, and what the shell forwards into each pane
# iframe, toggles and switches is read back. `mobile` answers __rompMobileOn; `tab` is the tab showing.
_ARMS_HARNESS = r"""
'use strict';
const LISTENERS = [], TOGGLES = [], TABS = [];
const POSTED = { 'f-files': [], 'f-feed': [], 'f-chat': [] };
let MOBILE = false, TAB = 'chat', FILES_READY = 'complete', FILES_LOADS = [];
const frame = (id) => ({ contentWindow: { postMessage: (m) => POSTED[id].push(JSON.parse(JSON.stringify(m))) },
  contentDocument: { get readyState() { return id === 'f-files' ? FILES_READY : 'complete'; } },
  addEventListener: (ev, f) => { if (ev === 'load' && id === 'f-files') FILES_LOADS.push(f); },
  removeEventListener: (ev, f) => { if (id === 'f-files') FILES_LOADS = FILES_LOADS.filter((g) => g !== f); } });
global.window = global;
global.addEventListener = (ev, f) => { if (ev === 'message') LISTENERS.push(f); };
global.__rompPaneToggle = (k, on) => TOGGLES.push([k, on]);
global.__rompMobileTab = (t) => TABS.push(t);
global.__rompMobileOn = () => MOBILE;
const stub = () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, appendChild() {}, setAttribute() {}, addEventListener() {}, remove() {} });
global.document = {
  body: { classList: { toggle() {}, contains: (c) => c === 'po-chat' || c === 'po-feed' || c === 'po-timeline' },
          getAttribute: (a) => (a === 'data-tab' ? TAB : null), appendChild() {} },
  getElementById: (id) => (id in POSTED ? frame(id) : null),
  createElement: stub, documentElement: { style: { setProperty() {} } },
  querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
};
global.localStorage = { getItem: () => null, setItem() {} };
global.sessionStorage = { getItem: () => 'wid1', setItem() {} };
global.location = { protocol: 'http:', host: 'TESTHOST:1', search: '', reload() {} };
global.fetch = () => new Promise(() => {});
global.setTimeout = () => 0; global.setInterval = () => 0; global.clearTimeout = () => {};
global.Event = class { constructor(t) { this.type = t; } };
"""
_BROWSE_DRIVER = r"""
const send = (m) => LISTENERS.forEach((f) => f({ data: m }));
const snap = () => ({ toggles: TOGGLES.slice(), tabs: TABS.slice(), files: POSTED['f-files'].slice(), feed: POSTED['f-feed'].slice(),
  chat: POSTED['f-chat'].slice(), from: window.__rompFilesTabFrom === undefined ? 'undef' : window.__rompFilesTabFrom,
  feedWasOff: window.__rompFeedWasOff === undefined ? 'undef' : window.__rompFeedWasOff });
const reset = () => { TOGGLES.length = 0; TABS.length = 0; for (const k in POSTED) POSTED[k].length = 0; delete window.__rompFilesTabFrom; delete window.__rompFeedWasOff; };
const SID = '__SID__';
const identity = { name: 'web', color: { bg: '#123456', fg: '#ffffff' } };
const out = { listeners: LISTENERS.length };
send({ romp: 'browseFiles', pane: 'pane', path: '/repo/notes-api', sid: SID, identity });
out.desktop = snap(); reset();
send({ romp: 'browseFiles', pane: 'pane', path: '.', sid: 'TESTHOST:' + SID });
out.bare = snap(); reset();
MOBILE = true; TAB = 'chat';
send({ romp: 'browseFiles', pane: 'pane', path: '/repo/notes-api', sid: SID, identity });
out.phone = snap();
send({ romp: 'filesViewerClosed' });
out.phoneClosed = snap();
send({ romp: 'filesViewerClosed' });
out.phoneClosedAgain = snap(); reset();
TAB = 'files';
send({ romp: 'browseFiles', pane: 'pane', path: '/repo/notes-api', sid: SID });
out.already = snap(); reset();
MOBILE = false;
send({ romp: 'browseFiles', path: '/repo/notes-api', sid: SID });
out.noPane = snap(); reset();
send({ romp: 'browseFiles', pane: 'feed', path: '/repo/notes-api', sid: SID, identity });
out.otherPane = snap(); reset();
// the Files page is still loading when the ask arrives: the forward waits for the iframe's load, once
FILES_READY = 'loading';
send({ romp: 'browseFiles', pane: 'pane', path: '/repo/notes-api', sid: SID, identity });
out.early = { toggles: TOGGLES.slice(), files: POSTED['f-files'].slice(), waiting: FILES_LOADS.length };
FILES_READY = 'complete'; FILES_LOADS.slice().forEach((f) => f());
out.loaded = { files: POSTED['f-files'].slice(), waiting: FILES_LOADS.length };
FILES_LOADS.slice().forEach((f) => f());
out.reloaded = { files: POSTED['f-files'].slice() }; reset();
// the viewFile pane branch beside it is untouched
send({ romp: 'viewFile', pane: 'pane', path: '/repo/notes-api/src/app.py', sid: SID, identity });
out.view = snap(); reset();
console.log(JSON.stringify(out));
"""


def _run_node(js):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    assert r.returncode == 0, "the shell script threw: " + r.stderr[:1200]
    return json.loads(r.stdout.strip().splitlines()[-1])


class BrowseRelay(unittest.TestCase):
    """The shell's browseFiles relay gains a pane branch: a folder clicked in the chat (the folder under the
    chat, the system-context card's Directory row, a tab menu's Browse files, a chat-hosted viewer's
    directory link) posts {romp:'browseFiles', pane, path, sid, identity} with the file link's own verdict
    (ui/webview/file-route.ts browseRoute, read at the click in render.ts openBrowse), and 'pane' brings the
    Files pane forward and forwards the ask, identity included, into #f-files, where files.ts opens the file
    browser as a column. The pane stays up, so none of the feed route's was-off / browseClosed restore
    applies; on a phone the pane's own close edge (filesViewerClosed) puts the person back, exactly as the
    viewFile pane branch does. A browseFiles naming no pane, or any other value, is the feed's as before.
    The arm runs here under node; the chat's end and the pane's contract run in
    ui/webview/browse-route.test.ts. Synthetic only: placeholder sids, the notes-api demo world, TESTHOST."""

    SID = "11111111-2222-3333-4444-555555555555"
    # …or a browse ask naming no pane while the Feed pane is off in this browser (the gear's Panes section, the user
    # 2026-09-10): the feed cannot be lifted, so the Files pane's arm takes it (tests/test_pane_state_broadcast.py RelayArms)
    HEAD = "if(m.romp==='browseFiles'&&(m.pane==='pane'||!feedHere())){var fb=document.getElementById('f-files');"
    FEED = "else if(m.romp==='browseFiles'){var bf=document.getElementById('f-feed');"
    IDENTITY = {"name": "web", "color": {"bg": "#123456", "fg": "#ffffff"}}

    @classmethod
    def setUpClass(cls):
        js = km._LANDING_SETTINGS_JS.replace("__ROMP_BOOT__", json.dumps("boot-1")).replace("__ROMP_LOADER__", json.dumps(""))
        cls.out = _run_node(_ARMS_HARNESS + js + _BROWSE_DRIVER.replace("__SID__", cls.SID))

    @staticmethod
    def _code(js):
        return "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))

    def test_the_pane_branch_brings_the_files_pane_forward_and_forwards_the_ask_with_the_identity(self):
        self.assertEqual(self.out["listeners"], 1, "the one message listener")
        d = self.out["desktop"]
        self.assertEqual(d["files"], [{"romp": "browseFiles", "path": "/repo/notes-api", "sid": self.SID, "identity": self.IDENTITY}],
                         "the ask, whole, into the Files pane")
        self.assertEqual(d["toggles"], [["files", True]], "the Files pane comes forward; the feed is not touched")
        self.assertEqual(d["feed"], [], "nothing reaches the feed")
        self.assertEqual(d["chat"], [])
        self.assertEqual(d["tabs"], [], "desktop: no mobile tab switch (the column is already visible)")
        self.assertEqual(d["from"], "undef", "and nothing to remember")
        self.assertEqual(d["feedWasOff"], "undef", "none of the feed route's restore is armed")
        # no identity on the ask: the forward carries null (never undefined), and the pane falls to the stub; a
        # remote session's prefixed sid rides through untouched, and "." (the session's cwd) is passed as is
        b = self.out["bare"]
        self.assertEqual(b["files"], [{"romp": "browseFiles", "path": ".", "sid": "TESTHOST:" + self.SID, "identity": None}])

    def test_phone_the_files_tab_comes_forward_and_the_panes_close_edge_puts_the_person_back_once(self):
        p = self.out["phone"]
        self.assertEqual(p["tabs"], ["files"])
        self.assertEqual(p["from"], "chat", "the tab the click came from is remembered")
        self.assertEqual(p["toggles"], [["files", True]], "the desktop bring-forward still runs (keeps po in step)")
        self.assertEqual(len(p["files"]), 1)
        c = self.out["phoneClosed"]
        self.assertEqual(c["tabs"], ["files", "chat"], "close: back to the remembered tab")
        self.assertIsNone(c["from"], "and the memory is consumed")
        self.assertEqual(self.out["phoneClosedAgain"]["tabs"], ["files", "chat"], "a second close with nothing remembered switches nothing")
        a = self.out["already"]
        self.assertEqual(a["tabs"], [], "already on the Files tab: nothing to switch")
        self.assertEqual(a["from"], "undef", "and nothing to remember")
        self.assertEqual(len(a["files"]), 1, "the ask is still forwarded")

    def test_a_browse_naming_no_pane_or_another_pane_takes_the_feeds_route_exactly_as_before(self):
        for key in ("noPane", "otherPane"):
            n = self.out[key]
            self.assertEqual(n["feed"], [{"romp": "browseFiles", "path": "/repo/notes-api", "sid": self.SID}], key + ": forwarded into the feed, path and sid only")
            self.assertEqual(n["files"], [], key + ": the Files pane hears nothing")
            self.assertEqual(n["tabs"], ["feed"], key + ": the feed's browser still switches a phone to the Feed tab")
            self.assertEqual(n["toggles"], [], key + ": the feed is on, so nothing to bring forward")
            self.assertEqual(n["from"], "undef", key + ": the Files route's memory is not touched")

    def test_an_ask_before_the_files_page_has_loaded_is_delivered_on_its_load_once(self):
        e = self.out["early"]
        self.assertEqual(e["toggles"], [["files", True]], "the pane still comes forward at once")
        self.assertEqual(e["files"], [], "nothing is posted into a document that cannot hear it yet")
        self.assertEqual(e["waiting"], 1, "one load listener holds the ask")
        l = self.out["loaded"]
        self.assertEqual(len(l["files"]), 1, "the load delivers it")
        self.assertEqual(l["files"][0]["path"], "/repo/notes-api")
        self.assertEqual(l["waiting"], 0, "and the listener is gone")
        self.assertEqual(len(self.out["reloaded"]["files"]), 1, "a later reload of the pane does not replay it")

    def test_the_view_file_pane_branch_beside_it_is_untouched(self):
        v = self.out["view"]
        self.assertEqual(v["files"], [{"romp": "viewFile", "path": "/repo/notes-api/src/app.py", "sid": self.SID, "identity": self.IDENTITY, "frag": None}])
        self.assertEqual(v["toggles"], [["files", True]])

    def test_the_pane_branch_precedes_the_feed_branch_and_names_no_feed_token(self):
        js = km._LANDING_SETTINGS_JS
        _has(self, self.HEAD, js)
        _has(self, self.FEED, js)
        self.assertLess(js.index(self.HEAD), js.index(self.FEED), "the pane branch first; the feed's is its else")
        self.assertEqual(js.count("m.romp==='browseFiles'"), 2, "the two branches, and no third")
        branch = self._code(js.split(self.HEAD)[1].split(self.FEED)[0])
        _has(self, "window.__rompPaneToggle&&window.__rompPaneToggle('files',true)", branch)
        _has(self, "if(curb!=='files'){window.__rompFilesTabFrom=curb;window.__rompMobileTab&&window.__rompMobileTab('files');}", branch)
        _has(self, "postMessage({romp:'browseFiles',path:m.path,sid:m.sid,identity:m.identity||null},'*')", branch)
        self.assertEqual(branch.count("postMessage("), 1, "one forward, carrying the whole ask")
        _has(self, "fb.addEventListener('load',onceb)", branch, "an early ask waits for the Files page's load")
        for tok in ("__rompFeedWasOff", "browseClosed", "'f-feed'", "__rompMobileTab('feed')"):
            _lacks(self, tok, branch, tok + " belongs to the feed route")
        # the feed branch's body is as it was: the lift, the remembered was-off flag, the phone tab, the forward
        feed = js.split(self.FEED)[1].split("if(m.type==='editorSelection'")[0]
        _has(self, "if(!document.body.classList.contains('po-feed')){window.__rompFeedWasOff=true;", feed)
        _has(self, "try{window.__rompMobileTab&&window.__rompMobileTab('feed');}catch(e){}   // phone: one pane at a time", feed)
        _has(self, "postMessage({romp:'browseFiles',path:m.path,sid:m.sid},'*')", feed)
        _lacks(self, "identity", self._code(feed), "the feed resolves its own identity")
        # the comment above the pane branch names the ladder, the gesture and the phone's way back
        lines = js.split(self.HEAD)[0].rstrip("\n").split("\n")
        start = len(lines)
        while start > 0 and lines[start - 1].lstrip().startswith("//"):
            start -= 1
        comment = "\n".join(lines[start:])
        for tok in ("file-route.ts", "gesture", "filesViewerClosed"):
            _has(self, tok, comment, "the comment names " + tok)

    def test_the_two_ends_agree_on_the_message(self):
        # the chat names its target and carries the session's identity (render.ts openBrowse); the pane caches the
        # identity, opens the browser, routes a pick through its own open, and owes the shell no browseClosed
        render = (UI / "render.ts").read_text()
        _has(self, 'window.parent.postMessage({ romp: "browseFiles", path: path || ".", sid: to, pane: "pane",', render)
        _has(self, "browseRoute(web, window.parent !== window, panesOn.files === true, panesAvail.files !== false)", render)   # no setting since T404
        files = (UI / "files.ts").read_text()
        _has(self, "shellRestore: false,", files)
        _has(self, "if (sid && id) identities.set(sid, id);", files)
        _has(self, 'openFileBrowse(m.path || ".", sid);', files)
        _has(self, "openFile: (p, sid) => openHere(p, sid, null),", files)
        browse = (UI / "file-browse.ts").read_text()
        _has(self, "if (!shellRestore) return;", browse)
        route = (UI / "file-route.ts").read_text()
        _has(self, "export function browseRoute(web: boolean, framed: boolean, filesOpen: boolean, filesAvail: boolean = true): BrowseRoute {", route)
        _has(self, 'export type BrowseRoute = FileRoute | "editor";', route)

    def test_the_gear_and_the_guide_name_the_folder(self):
        gear = (UI / "gear.js").read_text()
        self.assertNotIn("Where a file or folder clicked in the chat opens.", gear, "the file-links row is gone (T404)")
        _has(self, "closes the Files pane if it is open; file links then open over the pane you clicked.", gear)
        guide = (Path(ROOT) / "docs" / "guide.md").read_text().replace("\n", " ")
        _has(self, "open a listing of that folder by the same rule: in this pane while it is open, otherwise over the chat.", guide)
        _has(self, "Pick a file in the listing and it opens where the listing is.", guide)
        _has(self, "While the pane is open, a file link clicked in the chat opens in it.", guide, "the file sentence stands")


if __name__ == "__main__":
    unittest.main()
