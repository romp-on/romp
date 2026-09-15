#!/usr/bin/env python3
"""Chat columns (the user 2026-09-08: several sessions open at once instead of tabbing through them; reworked
2026-09-11 into columns that PARTITION the sessions). The dashboard shell can hold N chat COLUMNS: every column
past the first is a client-made twin of #chat-pane around an iframe at /chat?col=N&skeleton=1 (_LANDING_SPLIT_JS),
a full chat page (its own socket, state blob, drafts and scroll) FILTERED by one shell-owned fact: which sessions
each later column holds, persisted per browser as {v:2, cols:[{n, ids}]}; the first column holds the rest. Two
halves are checked here:

  * SOURCE PINS against the served shell + shim: the column id reaches the state key and the connect query,
    the shell's bridges (picker lift, editor selection, the bell's "session in front", the Log's connection
    tracking, Alt+Arrow, Escape, the gutters) know about later columns, the rail carries no split button, the
    kernel stamps the column and serves a later column as a skeleton client, the partition's functions exist
    and the owner lookup reads no pane's DOM, the parked reveal is addressed and forwarded.
  * EXECUTED: the real _LANDING_SPLIT_JS runs in node against a DOM stub (the test_error_center.py pattern)
    and the whole story is driven — the v1 migration, a move to a new column (the store, the halves, the blob
    seed, no focus posted), a move between columns closing the emptied one, the owner lookup, the emptiness
    message, another dashboard tab's write reconciled, the cross returning sessions home with their drafts,
    drafts travelling on a move, a created session claimed once and never stolen, the restore's seeding, the
    cap, and nothing on the phone — and THE DRAG (DragZonesExecute): a tab drag's zones mounted and unmounted, the
    rectangle's geometry and cue, the drops calling the one mutation, the refused edge at the cap — and THE ROWS
    (RowsExecute, the user 2026-09-15): a drop on the chat area's bottom zone opening a second row of columns under the
    first, the bottom row's own right edge, every arrangement of the four columns over two rows, the store's row and
    rowSplit fields (present only while a bottom row exists: without one the bytes are the shape above), the fold of an
    emptied bottom row, the walk's order across rows, the halving within a row, the row gutter's share, and another
    dashboard's rows reconciled.

Synthetic only — invented sids, no network, no real DOM.
"""
import inspect
import json
import os
import re
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_split", os.path.join(BIN, "romp-kernel"))

WEB = "11111111-2222-3333-4444-555555555501"
API = "11111111-2222-3333-4444-555555555502"
TESTS = "11111111-2222-3333-4444-555555555503"
X = "11111111-2222-3333-4444-555555555509"


class SplitSourcePins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = km._landing()
        cls.shim = km._shim("chat", 1)

    def test_the_split_script_is_served_after_the_pane_controller_it_leans_on(self):
        self.assertIn("_LANDING_SPLIT_JS", vars(km), "a module-level _JS constant: test_inline_js_parses checks it parses")
        self.assertIn("var CK='romp-chat-cols'", self.html)
        self.assertGreater(self.html.index("var CK='romp-chat-cols'"), self.html.index("window.__rompPaneToggle=togglePane"),
                           "the split runs after the controller: a restored column registers with the gutters and takes a fair "
                           "grow at boot, and a move to a new column from a hidden chat group calls __rompPaneToggle")
        self.assertIn("seed(n,sid);f.src='/chat?col='+n+'&skeleton=1';", km._LANDING_SPLIT_JS,
                      "every later column is /chat?col=N, dialled as a skeleton client of the session its seeded blob names (2026-09-11)")

    def test_the_shim_keys_each_column_s_state_and_names_it_on_the_connect(self):
        # the column comes from the URL; the first column ("" or 1) keeps the unsuffixed key it always had, so no
        # one's persisted active tab, drafts or scroll moves
        self.assertIn('var COL=new URLSearchParams(location.search).get("col")||"";if(COL==="1")COL="";', self.shim)
        self.assertIn('var SK="romp-vscode-state-chat"+(COL?":"+COL:"");', self.shim)
        self.assertIn('(COL?"&col="+encodeURIComponent(COL):"")', self.shim, "the kernel can tell the columns apart in its logs")
        # the ?active= connect hint reads the SAME key, so each column redials with ITS tab first
        self.assertIn('var st0=JSON.parse(localStorage.getItem(SK)||"null");active=(st0&&st0.activeId)||"";', self.shim)
        # …and a later column's FIRST dial has the hint too (2026-09-11): the shell seeds that very key with the session
        # the column opens on before the frame exists, and the frame's src says skeleton=1, which the shim puts on the
        # connect query, so the kernel serves the hinted session whole and the rest as skeleton tabs
        self.assertIn("var BK='romp-vscode-state-chat:';", km._LANDING_SPLIT_JS, "the shell's seed key is the shim's SK for /chat?col=N")
        # the two key strings are ONE string: the shell's prefix is the shim's key plus the shim's separator
        shim_key = re.search(r'var SK="([^"]+)"\+\(COL\?"(:)"\+COL:""\);', self.shim)
        shell_key = re.search(r"var BK='([^']+)';", km._LANDING_SPLIT_JS)
        self.assertEqual(shim_key.group(1) + shim_key.group(2), shell_key.group(1))
        self.assertIn("st.activeId=sid;try{localStorage.setItem(BK+n,JSON.stringify(st));}catch(e){}}", km._LANDING_SPLIT_JS)
        self.assertIn('var SKEL=new URLSearchParams(location.search).get("skeleton")==="1";', self.shim)
        self.assertIn('+((SKEL||(RESTART_DIET&&!everConnected))?"&skeleton=1":"")+(APP==="fleet"?"&provrows=1":""));', self.shim,
                      "the term follows the column; the Outline's capability term (empty for a chat column, plans/outline-pane-provisional-row.md) closes the connect query")
        self.assertIn('+((everConnected&&bundleReady&&readyAcked&&!readyQueued)?"&reconnect=1&proto="+readyProto:"")', self.shim,
                      "the redial gate is untouched (tests/test_pane_shim_return.py runs it)")

    def test_the_kernel_stamps_the_column_on_the_client(self):
        src = inspect.getsource(km.Handler)
        self.assertIn('col = (q.get("col") or [""])[0]', src)
        self.assertIn('if col:\n            client["col"] = col', src)
        with open(os.path.join(BIN, "romp-kernel"), encoding="utf-8") as fh:
            self.assertIn("(wid=%s col=%s slot=%s queued=%dB frame=%dB)", fh.read(), "the drop log names the column")

    def test_a_later_column_s_skeleton_dial_survives_the_ready_arm_s_reset(self):
        # The handshake records both flags: `reconnect` so the pusher cycle that lands before the bundle's ready serves
        # the skeleton set (one full, not the board) into a document that may not hear it; `skeletonOnReady` so the
        # ready arm, whose _client_reset_chat_base pops `reconnect` with the set, can re-arm it for the connect push
        # the page CAN hear (tests/test_chat_skeleton_reconnect.py runs the whole cycle)
        src = inspect.getsource(km.Handler)
        self.assertIn('skeleton = (q.get("skeleton") or [""])[0] == "1"', src)
        self.assertIn('if skeleton:', src)
        # …the survivor only for a FIRST dial: a later column's REDIAL carries skeleton=1 too (the shim reads it off the
        # address on every dial) and its page said ready on an earlier socket, so no arm would ever pop the flag, and
        # armed it left the client unstamped for the page's life (review find 2026-09-11; test_chat_skeleton_reconnect
        # test_09's fourth dial and test_11_c run it)
        self.assertIn('client["reconnect"] = True\n            client["dietSkeleton"] = True\n            if not reconnect:\n                client["skeletonOnReady"] = True', src)   # one string again (2026-09-15): the arm's adjacency, the durable diet marker between the reconnect line and the skeletonOnReady block
        # a pre-ready skeleton client is sent no session frame: the ready arm's connect push is the one full (the strip and
        # the statuses still go; review find 2026-09-11: the full crossed the wire twice per open)
        # …and none while `reconnect` is ARMED either (2026-09-12): a client with the flag has no set yet, and a pusher
        # iteration landing in the ready arm's gap (the set popped, the flag re-armed, the arm's own resolve still ahead)
        # sent a full for a skeleton tab (test_chat_skeleton_reconnect test_11_d runs the gap)
        so = inspect.getsource(km._send_chat_or_status)
        guard = 'if c.get("skeletonOnReady") or c.get("reconnect"):'
        self.assertIn(guard, so)
        self.assertLess(so.index('if sid in (c.get("skeleton") or ()):'), so.index(guard))
        self.assertLess(so.index(guard), so.index('return _send_chat_locked(c, m, ms, change_from, led_changed)'))
        # the flag is re-armed INSIDE the reset, under its lock and right behind its pop of `reconnect` (2026-09-12: as the
        # arm's own two statements past the reset there was an instant with neither flag set), and the reset precedes the
        # arm's connect push
        rs = inspect.getsource(km._client_reset_chat_base)
        self.assertIn('if client.pop("skeletonOnReady", False):\n            client["reconnect"] = True', rs)
        self.assertLess(rs.index("with _client_lock(client):"), rs.index('client.pop("reconnect", None)'))
        self.assertLess(rs.index('client.pop("reconnect", None)'), rs.index('client.pop("skeletonOnReady", False)'))
        i = src.index('msg.get("type") == "ready"')
        body = src[i:i + 3500]
        self.assertNotIn('client.pop("skeletonOnReady"', body, "the arm's own pop and re-arm are gone: the reset's lock holds both")
        self.assertLess(body.index("_client_reset_chat_base(client)"), body.index("self._push_one(client)"))
        # a pre-ready pop (the flag still set) neither stamps the client ready nor lets its caller consume a parked reveal:
        # _reveal_request aims taps at stamped clients, and this page has no listener yet
        rr = inspect.getsource(km._resolve_reconnect)
        self.assertIn('fresh = bool(c.get("skeletonOnReady"))', rr)
        self.assertIn('if not fresh:', rr)
        self.assertLess(rr.index('c.pop("reconnect", False)'), rr.index('fresh = bool(c.get("skeletonOnReady"))'))
        self.assertLess(rr.index('if not fresh:'), rr.index('c["ready"] = True'))
        self.assertEqual(rr.count("return not fresh"), 2, "the no-hint return and the set's return both say whether a REDIAL popped")
        self.assertNotIn("return True", rr)
        # the hand-over is GONE: the seeded blob's activeId is the page's wantActive, so no focus is posted into a NEW frame;
        # the one load listener a new frame may carry hands over the moved tab's drafts, never a focus
        split = km._LANDING_SPLIT_JS
        self.assertNotIn("own:true", split)
        self.assertIn("if(state)f.addEventListener('load',function(){adopt(f,sid,state);state=null;});", split)
        self.assertEqual(split.count("addEventListener('load'"), 1)

    def test_the_rail_carries_no_split_button_and_the_menu_no_door(self):
        # a tab is placed by dragging it (the drop zones) or from the palette; a bottom-bar button for it read as
        # clutter (the user 2026-09-08), so the rail and the mobile bar carry none, and the tab menu's item and its
        # openSplit message went with the drag (2026-09-11)
        self.assertNotIn("rail-split", self.html)
        self.assertNotIn("rail-split", km._LANDING_SPLIT_JS)
        self.assertNotIn("openSplit", km._LANDING_SPLIT_JS, "no listener for the menu's ask: the drop zones, the palette and the cross are __rompMoveTab's doors")

    def test_the_drag_s_zones_and_rectangle_are_served_with_their_dress_and_the_light_twin(self):
        split = km._LANDING_SPLIT_JS
        # the page's message is the whole input: the sid rides it, nothing is read from dataTransfer; the geometry is two
        # pure functions; every transition is a pointer crossing
        self.assertIn("if(m.romp==='tabDrag'){", split)
        self.assertNotIn("dataTransfer.getData", split)
        for needle in ["function edgeWidth(w){return Math.max(72,Math.min(180,0.2*w));}",
                       "function ghostRect(pane,rowRect){return {top:rowRect.top,height:rowRect.height,left:pane.left+pane.width/2,width:pane.width/2};}",
                       "function mountZones(){", "function unmountZones(){", "ghost=document.getElementById('col-ghost')",
                       "ghost.textContent=refused?'Four columns at most':drag.name;"]:
            self.assertIn(needle, split, needle)
        self.assertNotIn("setTimeout", split, "nothing is timed")
        # the rectangle's element beside the divider drag's landing line: a child of .col, never a flex item of the row
        self.assertIn("<div id=gv-ghost></div><div id=col-ghost></div><div id=gv-ghost-h></div>", self.html)
        # the zones ride the panes (position:relative) above the iframe and the cross (z 7); the edge above the column zone
        self.assertIn(".col-drop{position:absolute;inset:0;z-index:8}", self.html)
        self.assertIn(".col-drop.col-drop-edge{left:auto;z-index:9}", self.html)
        # the bottom zone (the chat rows, 2026-09-15) rides the chat area along its bottom edge, its height set inline
        self.assertIn(".col-drop.col-drop-bottom{top:auto;z-index:9}", self.html)
        self.assertIn("#chat-area{flex:var(--g-chat,60) 1 0;display:flex;flex-direction:column;position:relative;", self.html, "position:relative: the bottom zone's box")
        for needle in ["function bottomRect(){", "function zoneRect(z){if(z.classList.contains('col-drop-bottom'))return bottomRect();",
                       "var a=area.getBoundingClientRect(),h=(a.height-7)/2;return {top:a.top+a.height-h,height:h,left:a.left,width:a.width};",   # the new row's rectangle is the box the half split opens, gutter and all (review polish 2026-09-15)
                       "function making(z){return z.classList.contains('col-drop-edge')||z.classList.contains('col-drop-bottom');}",
                       "var b=zone(area,'col-drop-bottom',null,function(sid){if(b.getAttribute('data-refused'))refuse();else moveTab(sid,'below');});"]:
            self.assertIn(needle, split, needle)
        # the dress: the accent wash (the value --accent-wash resolves to in styles.css) inside a 2 px ring through the token
        self.assertIn(".col-drop.over,#col-ghost{background:rgba(156,210,255,0.12);box-shadow:inset 0 0 0 2px var(--accent,#9cd2ff)}", self.html)
        self.assertIn("#col-ghost{display:none;position:fixed;pointer-events:none;z-index:40;align-items:center;justify-content:center;", self.html)
        self.assertIn("font:600 11px 'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#8a8a8a;letter-spacing:.04em}", self.html)
        self.assertIn("#col-ghost.on{display:flex}", self.html)
        self.assertIn("#col-ghost.refused{background:transparent;box-shadow:inset 0 0 0 1px var(--accent,#9cd2ff)}", self.html)
        # the light twin beside the cross's, and the refused state restated at the specificity that wins the cascade
        self.assertIn("body.theme-light #col-ghost,body.theme-light .col-drop.over{background:rgba(194,65,12,0.10)}", self.html)
        self.assertIn("body.theme-light #col-ghost.refused{background:transparent}", self.html)
        self.assertLess(self.html.index("body.theme-light #col-ghost,"), self.html.index("body.theme-light #col-ghost.refused"))
        # …and the rectangle's line takes the rail's LIGHT label colour under the light theme (review find 2026-09-11: it
        # kept the dark theme's grey on the cream wash)
        self.assertIn("body.theme-light .rail-btn{color:#5D574E}", self.html)
        self.assertIn("body.theme-light #col-ghost{color:#5D574E}", self.html)
        # a body class toggled for the gesture that nothing read is gone (review find 2026-09-11)
        self.assertNotIn("tabdrag", split)
        self.assertNotIn("tabdrag", self.html)

    def test_the_css_hides_columns_with_the_chat_group_lifts_by_class_and_never_shows_them_on_the_phone(self):
        self.assertIn("body:not(.po-chat) .chat-col,body:not(.po-chat) .gv-chat{display:none}", self.html)
        self.assertIn("body.picker-open .pane.lifted{display:block!important}", self.html)
        self.assertIn("body.picker-open iframe.lifted{display:block;position:fixed;left:0;right:0;top:0;height:var(--app-h,100dvh);z-index:200;background:transparent}", self.html)
        self.assertNotIn("body.picker-open #f-chat{", self.html, "the lift is by class now — a later column's picker lifts THAT column")
        self.assertIn(".chat-col>.col-x{position:absolute;top:4px;right:6px;z-index:7;", self.html)
        mobile = self.html[self.html.index("@media (max-width:820px),(pointer:coarse) and (max-width:1024px){"):]
        self.assertIn(".chat-col,.gv-chat{display:none!important}", mobile)
        # the chat area's wrapper and its top row dissolve on the phone like the pane wrappers, so #f-chat tiles as before; the
        # bottom row and the row gutter never show there (the chat rows, 2026-09-15)
        self.assertIn("#chat-area,#chat-row-1{display:contents!important}#chat-row-2,#gv-rows{display:none!important}", mobile)
        self.assertIn("#chat-area:not(.rows) #chat-row-2,#chat-area:not(.rows) #gv-rows{display:none}", self.html, "on the desktop the bottom row shows only while a column is in it")

    def test_the_shell_s_bridges_know_about_later_columns(self):
        # the picker lift marks the ASKING frame (e.source) and its pane
        js = km._LANDING_SETTINGS_JS
        self.assertIn("var lf=(window.__rompFrameOfWin&&window.__rompFrameOfWin(e.source))||document.getElementById('f-chat');", js)
        self.assertIn("Array.prototype.forEach.call(document.querySelectorAll('.lifted'),function(el){el.classList.remove('lifted');});", js)
        self.assertIn("if(m.on&&lf){lf.classList.add('lifted');if(lf.parentElement)lf.parentElement.classList.add('lifted');}", js)
        self.assertIn("document.body.classList.toggle('picker-open',!!m.on);}", js)
        # a passage selected in the feed's viewer lands in the column holding that session, else the one last used
        self.assertIn("fc=(window.__rompChatTarget&&window.__rompChatTarget(m.sid))||fc;", js)
        # the gear's close hands the keyboard back to the column last worked in, else the first (2026-09-11)
        self.assertIn("if(!m.on){var fid=(window.__rompFocusedChatId&&window.__rompFocusedChatId())||'f-chat';"
                      "var fc=document.getElementById(fid)||document.getElementById('f-chat');", js)
        # the bell's "session in front" reads the column last worked in
        self.assertIn("var fid=window.__rompFocusedChatId&&window.__rompFocusedChatId();", km._LANDING_PUSH_JS)
        # the Log tracks a split column's socket under its own key — never masking the first column's
        errs = km._LANDING_ERRS_JS
        self.assertIn("var st={},stc={};", errs)
        self.assertIn("var col=(m.app==='chat'&&window.__rompColOf)?window.__rompColOf(e.source):'';", errs)
        self.assertIn("if(col){var sc=(m.state==='up')?'up':'down',pc=stc[col];stc[col]=sc;", errs)
        self.assertIn("for(var c in stc){if(stc[c]==='down'&&shown('chat'))return true;}", errs)
        self.assertIn("window.__rompColGone=function(c){delete stc[String(c)];paint();};", errs)
        # the first column's tracking is byte-for-byte what it was
        self.assertIn("var s=(m.state==='up')?'up':'down',prev=st[m.app];st[m.app]=s;", errs)
        # Alt+Arrow walks every chat column, the ring follows, later columns are wired as they are made
        focus = km._LANDING_FOCUS_JS
        self.assertIn("window.__rompFocusedChatId=function(){return document.getElementById(lastChat)?lastChat:'f-chat';};", focus)
        self.assertIn("window.__rompWireFocus=function(f){f.addEventListener('load',function(){wire(f);});wire(f);};", focus)
        self.assertIn("function paneOf(id){return PANE[id]||(window.__rompChatPaneOf?window.__rompChatPaneOf(id):null);}", focus)
        self.assertIn("window.__rompWireEsc=function(f){", km._LANDING_ESC_JS)
        # the gutters: later columns register, gv-a/gv-b's left neighbour is the rightmost chat column
        gut = km._LANDING_JS
        self.assertIn("window.__rompRegisterPane=function(id,k){cancelGesture();KEYS[id]=k;if(PANES.indexOf(id)<0)PANES.splice(PANES.indexOf('fleet-pane'),0,id);};", gut)   # a pane made under a drag ends the drag first (2026-09-15)
        self.assertIn("window.__rompUnregisterPane=function(id){", gut)
        # the chat AREA wears the outer weight the first pane wore; the first pane has an inner one against its row's columns (the chat rows, 2026-09-15)
        # an explicit map for the fixed panes, a split column's registration, and NONE for any other id (the seventh pass: an
        # unregistered pane fell to 'files'); setGrow on no key is a no-op
        self.assertIn("var FIXED={'chat-area':'chat','chat-pane':'chat1','fleet-pane':'fleet','feed-pane':'feed','files-pane':'files'};", gut)
        self.assertIn("function key(id){return KEYS[id]||FIXED[id]||null;}", gut); self.assertIn("window.__rompPaneKey=key;", gut)
        self.assertIn("function setGrow(k,v){if(!k)return;grow[k]=v;row.style.setProperty('--g-'+k,v);}", gut)
        self.assertNotIn("?'feed':'files'", gut)
        self.assertIn("function lastChat(){return 'chat-area';}", gut)
        self.assertIn("window.__rompRowGutter=function(gid,topId,botId,apply){", gut, "the row gutter rides the same drag code, reporting the top row's share")
        # a pre-rows pane store (no chat1): the area's weight is set once from the top-row columns the split restored, in the
        # pixels of the pre-rows layout (review find 2026-09-15; tests/test_pane_gutters.py runs it)
        self.assertIn("window.__rompSeedAreaWeight=function(){if(ingest()||!legacy)return false;if(!shown('chat-pane'))return false;return upgrade(false);};", gut)
        # …no snapshot (review find 2026-09-15, fourth pass): while the store is pre-rows-shaped this shell keeps the PRE-ROWS fair grow
        # (the old PANES.filter(shown), the chat area no pane of it), the upgrade reads the LIVE roster and weights when it runs, a peer's
        # upgraded store is adopted rather than written over, and the store keeps its pre-rows shape meanwhile
        self.assertNotIn("legacyGrow", gut); self.assertNotIn("legacyKeys", gut)
        self.assertIn("function oldFair(){var v=PANES.filter(function(id){return id!=='chat-area'&&shown(id);}).map(function(id){return grow[key(id)];}).filter(finite);return v.length?v.reduce(function(a,b){return a+b;},0)/v.length:50;}", gut)
        # a peer's upgrade is INGESTED at the event and FIRST in every entry point that reads or changes a weight, never from persist
        # (review find 2026-09-15, fifth pass: adopting at the final write threw away the very action that wrote)
        self.assertNotIn("sync(", gut)
        self.assertIn("window.addEventListener('storage',function(e){if(e&&e.key===GK)ingest();});", gut, "the peer's write is the event")
        self.assertIn("window.__rompGrowFair=function(k){if(k==='timeline')return;cancelGesture();ingest();", gut, "the rail's fair grow: a drag ended first, a peer's upgrade ingested, then the CURRENT rule (or the pre-rows one while still legacy)")
        self.assertIn("if(legacy){if(k==='chat'){upgrade(true);return;}setGrow(k,oldFair());persist();return;}", gut, "the pre-rows rule while legacy; the chat pane coming on is the upgrade's moment")
        self.assertIn("window.__rompGrowFairIfNew=function(k){ingest();if(typeof grow[k]==='number'&&isFinite(grow[k])){setGrow(k,grow[k]);return;}window.__rompGrowFair(k);};", gut, "a restored or peer-made column keeps a published weight")
        self.assertIn("window.__rompUnregisterPane=function(id){cancelGesture();ingest();var k=KEYS[id];", gut, "unregister: a drag ended first, a peer's upgrade ingested, the key deleted, then the write carries the deletion")
        self.assertIn("window.__rompSplitGrow=function(leftId,newKey){cancelGesture();ingest();", gut); self.assertIn("window.__rompSplitShrink=function(leftId,goneId){cancelGesture();ingest();", gut)
        # a drag is ONE TRANSACTION (the sixth pass): a peer's write during it is only noted, endGesture is the one exit for every gutter,
        # a commit rebases the pair against the ingested siblings so the divider lands where the line was, a cancel restores and writes nothing
        self.assertNotIn("held", gut)
        self.assertIn("var gesture=null;", gut); self.assertNotIn("ingestDue", gut)
        # the store on disk is the ONE source (the eighth pass): the listener re-reads it, never the event's own value — two current
        # writes queued behind a slow page had the older adopted and the newer written over; a re-read is never staler than the event
        self.assertIn("function ingest(){if(!legacy)return false;if(gesture)return false;var cur=null;try{cur=JSON.parse(localStorage.getItem(GK)||'null');}catch(e){}", gut, "nothing applied while a gesture stands; the store re-read")
        self.assertIn("window.addEventListener('storage',function(e){if(e&&e.key===GK)ingest();});", gut, "the peer's write is the event; the store as it stands is what is adopted")
        self.assertNotIn("newValue", gut)
        self.assertIn("function endGesture(commit){var g=gesture;if(!g)return;gesture=null;", gut)
        self.assertIn("window.removeEventListener('mousemove',g.mv);window.removeEventListener('mouseup',g.up);", gut, "the gesture's listeners go at its end")
        self.assertIn("if(g.vert){if(commit&&g.apply)g.apply(g.nL/g.sum);ingest();return;}", gut)
        self.assertIn("var intact=commit&&!!g.L.isConnected&&!!g.R.isConnected&&key(g.L.id)===g.kL&&key(g.R.id)===g.kR;", gut, "a commit needs the pair as pressed: present and under the press-time keys")
        self.assertIn("if(!intact){for(var k in g.w0)setGrow(k,g.w0[k]);ingest();return;}", gut, "a cancel: the press-time weights back, nothing written, then whatever the store holds")
        self.assertIn("if(ingest()&&a+b<g.W)g.others.forEach(function(k){if(finite(grow[k]))so+=grow[k];});", gut, "the release ingests UNCONDITIONALLY, then rebases against what it adopted")
        self.assertIn("if(so>0){var pair=so*(a+b)/(g.W-a-b);setGrow(g.kL,pair*a/(a+b));setGrow(g.kR,pair*b/(a+b));}else{setGrow(g.kL,a);setGrow(g.kR,b);}", gut, "the rebase; the plain write with nothing adopted; the press-time keys")
        self.assertIn("var kL=key(L.id),kR=key(R.id);if(!vert&&(!kL||!kR))return;", gut, "a pane gutter's pair must be registered panes")
        # a structural change ends a drag first: the pane-weight script's own mutations, the split's and the rail's
        self.assertIn("function cancelGesture(){if(gesture)endGesture(false);}", gut); self.assertIn("window.__rompCancelGesture=cancelGesture;", gut)
        for site in ["window.__rompRegisterPane=function(id,k){cancelGesture();", "window.__rompUnregisterPane=function(id){cancelGesture();ingest();",
                     "window.__rompGrowFair=function(k){if(k==='timeline')return;cancelGesture();ingest();", "window.__rompSplitGrow=function(leftId,newKey){cancelGesture();ingest();",
                     "window.__rompSplitShrink=function(leftId,goneId){cancelGesture();ingest();"]:
            self.assertIn(site, gut, "a structural site ends the drag first: " + site)
        for site in ["function make(n,sid,state){var have=document.getElementById(frameId(n));if(have)return have;cancelDrag();", "function moveTab(sid,to){if(typeof sid!=='string'||!sid)return null;cancelDrag();",
                     "function close(n,keep){var i=idx(n);if(i<0)return;cancelDrag();", "function reconcile(r){cancelDrag();var next=r.cols;"]:
            self.assertIn(site, km._LANDING_SPLIT_JS, "the split's structural site ends the drag first: " + site)
        self.assertIn("function cancelDrag(){try{if(window.__rompCancelGesture)window.__rompCancelGesture();}catch(e){}}", km._LANDING_SPLIT_JS)
        self.assertIn("if(window.__rompCancelGesture)window.__rompCancelGesture();   // a pane coming or going under a gutter drag ends the drag first", km._LANDING_COLLAPSE_JS)
        # persist is write-only, and refuses (says so) rather than write a pre-rows shape over a peer's upgrade; the write is exported for that guard's test alone
        self.assertIn("function persist(){if(legacy){var cur=null;try{cur=JSON.parse(localStorage.getItem(GK)||'null');}catch(e){}", gut)
        self.assertIn("if(cur&&finite(cur.chat1)){try{console.warn(", gut); self.assertIn("window.__rompPersistGrow=persist;", gut)
        for src in ["window.addEventListener('pointercancel',function(){endGesture(false);});", "window.addEventListener('blur',function(){endGesture(false);});",
                    "document.addEventListener('visibilitychange',function(){if(document.visibilityState==='hidden')endGesture(false);});", "if(gesture)endGesture(false);   // a new press while one stands"]:
            self.assertIn(src, gut, "a cancel source: " + src)
        self.assertIn("var w0=Object.assign({},grow),others=[],W=0;", gut, "the press-time weights, before the press's own normalisation")
        self.assertIn("function mv(ev){if(gesture!==g)return;", gut); self.assertIn("function up(){if(gesture!==g)return;endGesture(true);}", gut, "a stale release is a no-op")
        self.assertIn("window.__rompRowGutter=function(gid,topId,botId,apply){gutter(gid,function(){return topId;},botId,true,apply);};", gut, "the row gutter is the same gutter(): the same transaction")
        self.assertIn("function upgrade(showing){if(ingest()||!legacy)return false;", gut)
        self.assertIn("window.__rompGrowLegacy=function(){return legacy;};", gut)
        self.assertIn("var cols=PANES.filter(function(id){var e=document.getElementById(id);return !!KEYS[id]&&!!e&&e.parentElement===host&&finite(grow[KEYS[id]]);}).map(function(id){return KEYS[id];});", gut, "the live roster: registered, present, in the first pane's row")
        self.assertIn("if(showing)setGrow('chat1',oldFair());", gut)
        self.assertIn("if(!cur||!finite(cur.chat1))return false;legacy=false;for(var k in cur){if(finite(cur[k]))setGrow(k,cur[k]);}return true;}", gut, "a current-shape store is adopted whole")
        self.assertIn("var o=Object.assign({},grow);delete o.chat1;try{localStorage.setItem(GK,JSON.stringify(o));}catch(e){}return;}", gut, "persist writes what its caller produced: the pre-rows shape while legacy, nothing ingested here")
        self.assertIn("if(window.__rompSeedAreaWeight)window.__rompSeedAreaWeight();", km._LANDING_SPLIT_JS, "called at the split's boot, no arguments: the roster is live")
        boot = km._LANDING_SPLIT_JS[km._LANDING_SPLIT_JS.index("try{if(!mobile()){var r0=read();"):]
        self.assertLess(boot.index("cols.forEach(function(c){make(c.n,seedFor(c),null);});"), boot.index("__rompSeedAreaWeight();"), "…after they are made and registered")
        self.assertIn("window.__rompGutter=gutter;", gut)
        self.assertIn("gutter('gv-a',function(){return lastChat();},'fleet-pane');", gut)
        # a pane with no grow yet never averages in as NaN (the first split opened 0px wide — review find 2026-09-08),
        # and a column keeps the width it was dragged to across reloads
        self.assertIn(".filter(function(g){return typeof g==='number'&&isFinite(g);});", gut)
        self.assertIn("window.__rompGrowFairIfNew=function(k){ingest();if(typeof grow[k]==='number'&&isFinite(grow[k])){setGrow(k,grow[k]);return;}window.__rompGrowFair(k);};", gut)   # a peer's upgrade ingested first (2026-09-15)
        # …and a new column takes HALF the rightmost one (2026-09-11), through the gutters' own normalisation
        # (tests/test_pane_gutters.py runs it); the split calls it with the rightmost pane and the new key
        self.assertIn("window.__rompSplitGrow=function(leftId,newKey){", gut)
        self.assertIn("window.__rompSplitShrink=function(leftId,goneId){", gut, "…and its twin: a closing column's pixels go to the column on its left (review find 2026-09-11)")
        split = km._LANDING_SPLIT_JS
        self.assertIn("var state=take(src,sid),n=nextNumber(),lp=lastPaneIn(nr);", split)
        self.assertIn("if(lp&&window.__rompSplitGrow)window.__rompSplitGrow(lp,'chat'+n);", split, "the rightmost pane of the TARGET row is halved; the bottom row's first column has nothing to halve")
        self.assertIn("if(window.__rompGrowFairIfNew)window.__rompGrowFairIfNew('chat'+n);", split)
        # a refused move says why (the acknowledgement rule), and the tab menu can ask first
        self.assertIn("function canSplit(){return !mobile()&&cols.length+1<MAX;}", split)
        self.assertIn("window.__rompCanSplit=canSplit;", split)
        self.assertIn("Four chat columns at most", split)
        self.assertIn("The phone shows one pane at a time", split)
        self.assertIn("This session is already alone in its column.", split)
        # no hand-over focus (2026-09-11): the seeded blob names the tab, and a later reload of the frame keeps the tab
        # its own state names — the same key, written by the page itself from then on
        self.assertNotIn("{once:true}", split)
        self.assertNotIn("own:true", split)

    def test_the_partition_s_functions_exist_and_the_owner_lookup_reads_no_pane_s_dom(self):
        split = km._LANDING_SPLIT_JS
        # the store's shape and its one-shot migration
        # …row:2 on a bottom-row entry and a top-level rowSplit ONLY while a bottom row exists (the chat rows, 2026-09-15), so a
        # browser that never stacks writes the bytes it always did (RowsExecute compares them)
        self.assertIn("function save(){var o={v:2,cols:cols.map(function(c){var e={n:c.n,ids:c.ids.slice()};if(c.row===2)e.row=2;return e;})};if(hasRow2())o.rowSplit=rowSplit;", split)
        self.assertIn("if(Array.isArray(raw)){migrated=true;", split, "a v1 array of numbers is read once more…")
        self.assertIn("if(r0.migrated)save();", split, "…and written back in the new shape")
        # the three pure readers, the sets the pages read, the one mutation, the claim
        for needle in ["function ownerOf(sid){", "function sets(){", "function nextNumber(){",
                       "window.__rompChatSets=function(){return mobile()?null:sets();};",
                       "window.__rompCanSplit=canSplit;window.__rompMoveTab=moveTab;",
                       "window.__rompClaimSession=function(sid,col){", "function moveTab(sid,to){"]:
            self.assertIn(needle, split, needle)
        # the owner lookup is ONE lookup: target() reads the entries, never a pane's active tab; the DOM read survives
        # for the palette's "move this session" alone
        target = re.search(r"function target\(sid\)\{.*?\}\n", split).group(0)
        self.assertNotIn("activeIn(", target)
        self.assertIn("frameOfCol(ownerOf(sid))", target)
        self.assertEqual(split.count("activeIn("), 2, "defined once, called once (the palette's move of the focused column's tab)")
        self.assertIn("activeIn(focused())", split)
        # the emptiness message, the drafts hand-off and the other dashboard tab's write
        self.assertIn("m.romp==='colEmpty'&&Array.isArray(m.gone)", split)
        self.assertIn("f.contentWindow.postMessage({romp:'adopt',sid:sid,state:state},'*');", split)
        self.assertIn("__rompTakeSessionState", split)
        # the page's two answers the shell asks for before it moves a tab or closes a column (review finds 2026-09-11), and
        # the one refusal line each; the movable check at the top of the one mutation, the busy check where a column's
        # last listed member would leave and where a column is closed by hand (a reconcile of another tab's write is not)
        for needle in ["function movable(f,sid){", "function busy(f){", "function loaded(f){",
                       "var why=refusal(src,sid);if(why==='locked')return notify(LOCKED);if(why||!movable(src,sid))return notify('Only an open session can be moved between columns.');",
                       "var BUSY='A session is still being created in this column.';",
                       "var se2=entry(from);if(se2&&se2.ids.length===1&&busy(src))return notify(BUSY);",
                       "if(!keep&&busy(f)){notify(BUSY);return;}"]:
            self.assertIn(needle, split, needle)
        mt = split[split.index("function moveTab(sid,to){"):split.index("function close(n,keep){")]
        self.assertLess(mt.index("var why=refusal(src,sid);"), mt.index("var nr=to==='new'?1:to==='below'?2:0;"), "refused before anything is taken or grown (the reason read first, T395)")
        # a new column in the row the session is already alone in is the refused twin; one in the other row is a move (the rows)
        self.assertIn("if(se&&se.ids.length===1&&rowOf(from)===nr)return notify('This session is already alone in its column.');", mt)
        # …and a lone member leaving for the OTHER row is refused over a create in flight BEFORE anything is taken, grown or
        # written (review find 2026-09-15: a close refused after the write left an empty entry persisted, and another
        # dashboard's sanitised write then closed the column here past the check, the creation's text with it)
        busy_line = "if(se&&se.ids.length===1&&busy(src))return notify(BUSY);"
        self.assertIn(busy_line, mt)
        nb = mt.index("if(nr){"); nr_branch = mt[nb:mt.index("var tn=Number(to);")]
        self.assertIn(busy_line, nr_branch)
        for later in ("if(!canSplit())return refuse();", "var state=take(src,sid)", "__rompSplitGrow(lp,'chat'+n)", "unlist(sid)", "save();"):
            self.assertLess(nr_branch.index(busy_line), nr_branch.index(later), "the busy refusal comes before " + later)
        self.assertIn("if(nr===2&&!hasRow2())rowSplit=0.5;", nr_branch, "a bottom row opens at the half the rectangle promised")
        self.assertIn("var left=unlist(sid);cols.push({n:n,ids:[sid],row:nr});save();", mt)
        self.assertIn("var nf=make(n,sid,state);if(left)close(left);", mt, "the origin emptied by a move to the other row closes")
        self.assertLess(mt.index("busy(src)"), mt.index("var st=take(src,sid)"), "refused before the hand-off")
        # a column closed for emptiness tells the first column which of its gone ids the page's own cross removed, ahead of
        # the store write; nothing else is held (the vanishing tab, 2026-09-12)
        self.assertIn("var crossed=Array.isArray(m.crossed)?gone.filter(function(id){return m.crossed.indexOf(id)>=0;}):[];", split)
        self.assertIn("home.contentWindow.postMessage({romp:'closing',ids:crossed},'*');", split)
        self.assertNotIn("{romp:'closing',ids:gone}", split)
        ce = split[split.index("if(m.romp==='colEmpty'"):split.index("if(m.romp==='orphanState'")]
        self.assertLess(ce.index("{romp:'closing'"), ce.index("close(en.n)"))
        # …and orphaned state offered by a page is handed to the owner's page when that page can hear it
        self.assertIn("if(m.romp==='orphanState'&&Array.isArray(m.sids)){", split)
        self.assertIn("if(t&&t!==sf&&loaded(t))adopt(t,sid,take(sf,sid));", split)
        # a closing column's width goes to the column on its left before its key is dropped (the halving's twin)
        cl = split[split.index("function close(n,keep){"):split.index("function closeFocused(){")]
        self.assertIn("if(left&&window.__rompSplitShrink)window.__rompSplitShrink(left,paneId(n));", cl, "the bottom row's first column has no left neighbour to hand its width to")
        self.assertLess(cl.index("__rompSplitShrink(left,paneId(n))"), cl.index("__rompUnregisterPane(paneId(n))"))
        self.assertLess(cl.index("var left=prevIn(n),fs=frames(),fi=fs.indexOf(f),pf=fi>0?fs[fi-1]:home;"), cl.index("cols.splice(i,1);"), "the left pane in its row and the frame before it in the walk are read before the entry goes")
        self.assertIn("if(p)p.remove();if(g)g.remove();tidy();if(!hasRow2())rowSplit=0.5;applyRows();", cl, "a closed column may leave the bottom row empty (the fold: the share is forgotten with the row) or gutter-first (tidied)")
        self.assertIn("window.addEventListener('storage',function(e){if(!e||e.key!==CK||mobile())return;var r=read();if(!r.migrated)reconcile(r);});", split)
        # the rows' own readers and the gutter between them, wired once through the pane gutters' drag code
        for needle in ["function rowOf(n){", "function rowCols(r){", "function hasRow2(){", "function lastPaneIn(r){", "function prevIn(n){",
                       "function applyRows(){area.classList.toggle('rows',hasRow2());area.style.setProperty('--rs1',Math.round(rowSplit*1000)/10);area.style.setProperty('--rs2',Math.round((1-rowSplit)*1000)/10);}",   # weights out of 100: grow factors summing below 1 hand out only that fraction of the free space (served find 2026-09-15: a lone top row on 0.5 filled half the area)
                       "if(window.__rompRowGutter)window.__rompRowGutter('gv-rows','chat-row-1','chat-row-2',function(s){rowSplit=Math.round(s*1000)/1000;applyRows();save();});",
                       "window.__rompSplitChatBelow=function(sid){"]:
            self.assertIn(needle, split, needle)
        # the cross's title reads as what it does now
        self.assertIn("x.title='Close this column';", split)

    def test_the_parked_push_tap_reveal_is_addressed_to_the_client_that_consumes_it_and_forwarded_by_the_page(self):
        # one chat client consumes the parked tap (_consume_pending_reveal), so it rides `own`; under the partition the
        # consuming page hands a tap for a session another column holds to that column, without `own` (render.ts)
        src = inspect.getsource(km._consume_pending_reveal)
        self.assertIn('m["own"] = True', src)
        self.assertIn("posts the same message WITHOUT `own` into", src)
        self.assertIn("one hop by construction", src)
        self.assertNotIn("a state the split allows on purpose", src, "two columns on one session is no longer a state the split allows")
        self.assertIn("client[\"send\"](json.dumps(m))", src)


# ── the split script, RUN ────────────────────────────────────────────────────────────────────────────
HARNESS = r"""
'use strict';
let STORE = {};
const CALLS = { register: [], unregister: [], growFair: [], splitGrow: [], splitShrink: [], gutter: [], rowGutter: [], seedArea: [], wireFocus: [], wireEsc: [], colGone: [], events: [], posted: [], focus: [], notify: [], toggle: [], taken: [], sets: [] };
let ROWAPPLY = null;      // the row gutter's release hook the split hands __rompRowGutter (the rows, 2026-09-15)
let SEQ = [];             // the order of the shell's side effects across stubs (a store write, a post, a grow, a key drop)
let UNMOVABLE = new Set(); // ids the pages answer "not a session a column can hold" for (a create in flight, a viewer)
let LOCKED_SIDS = new Set(); // ids whose page answers 'locked' (the tab lock, T395): the toast names the gear's menu (T405)
let BUSY = {};            // frame id → whether that page reports a create in flight
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = String(v); CALLS.sets.push(k); SEQ.push('set:' + k); }, removeItem: (k) => { delete STORE[k]; } };
let BODY_CLASSES = new Set(['po-chat', 'po-feed', 'po-timeline']);
let FOCUSED = 'f-chat';   // what the shell's focus script would report as the column last worked in
let MOBILE = false;       // whether #mtabs is displayed (the phone layout)
let BYID = {};
let WL = {};
let TAKE = {};            // frame id → sid → what that page holds for the session (its __rompTakeSessionState answer)
function mkEl(tag) {
  const el = {
    tagName: tag, className: '', title: '', textContent: '', src: '', parentElement: null, _id: '',
    style: { flex: '', _props: {}, setProperty(k, v) { this._props[k] = v; }, removeProperty(k) { delete this._props[k]; } },
    _attrs: {}, _ls: {}, children: [], _active: '',
    _rect: { left: 0, top: 0, width: 0, height: 0 }, getBoundingClientRect() { return Object.assign({}, this._rect); },
    contains(n) { return n === this || this.children.some((c) => c.contains && c.contains(n)); },
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    appendChild(c) { c.parentElement = this; this.children.push(c); return c; },
    insertBefore(c, ref) { c.parentElement = this; const i = this.children.indexOf(ref); if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; },
    remove() { const p = this.parentElement; if (p) { const i = p.children.indexOf(this); if (i >= 0) p.children.splice(i, 1); }
      const drop = (n) => { if (n._id) delete BYID[n._id]; n.children.forEach(drop); }; drop(this); this.parentElement = null; },
    addEventListener(k, f, opts) { (this._ls[k] = this._ls[k] || []).push({ f, once: !!(opts && opts.once) }); },
    fire(k, ev) { const ls = (this._ls[k] || []).slice(); this._ls[k] = ls.filter((l) => !l.once); ls.forEach((l) => l.f(ev || { stopPropagation() {} })); },
  };
  Object.defineProperty(el, 'id', { get() { return this._id; }, set(v) { if (this._id) delete BYID[this._id]; this._id = String(v); if (v) BYID[v] = this; } });
  const cls = () => el.className.split(/\s+/).filter(Boolean);
  el.classList = {
    contains: (c) => cls().includes(c),
    add: (...cs) => { const l = cls(); cs.forEach((c) => { if (!l.includes(c)) l.push(c); }); el.className = l.join(' '); },
    remove: (...cs) => { el.className = cls().filter((c) => !cs.includes(c)).join(' '); },
    toggle: (c, force) => { const want = force === undefined ? !cls().includes(c) : !!force; if (want) el.classList.add(c); else el.classList.remove(c); return want; },
  };
  if (tag === 'iframe') {
    el.contentWindow = {
      postMessage(m) { CALLS.posted.push({ id: el.id, m }); SEQ.push('post:' + el.id + ':' + (m.romp || m.type)); }, focus() { CALLS.focus.push(el.id); },
      __rompTakeSessionState(sid) { const held = TAKE[el.id] && TAKE[el.id][sid]; CALLS.taken.push([el.id, sid, !!held]); if (!held) return null; delete TAKE[el.id][sid]; return held; },
      __rompMovableSession(sid) { return !UNMOVABLE.has(sid) && !LOCKED_SIDS.has(sid); },
      __rompMoveRefusal(sid) { return LOCKED_SIDS.has(sid) ? 'locked' : UNMOVABLE.has(sid) ? 'not-open' : ''; },
      __rompColumnBusy() { return !!BUSY[el.id]; },
    };
    el.contentDocument = { querySelector(sel) { return (sel === '#tabs .tab.active[data-id]' && el._active) ? { getAttribute: () => el._active } : null; } };
  }
  return el;
}
let ROW = null;
global.document = {
  body: { classList: { contains: (c) => BODY_CLASSES.has(c), add: (c) => BODY_CLASSES.add(c), remove: (c) => BODY_CLASSES.delete(c) } },
  querySelector(sel) { return sel === '.row' ? ROW : null; },
  getElementById(id) { return BYID[id] || null; },
  createElement(tag) { return mkEl(tag); },
};
global.getComputedStyle = (el) => ({ display: el === BYID['mtabs'] ? (MOBILE ? 'flex' : 'none') : 'block' });
global.window = global;
global.addEventListener = (t, f) => { (WL[t] = WL[t] || []).push(f); };
global.dispatchEvent = (ev) => { CALLS.events.push(ev); (WL[ev.type] || []).forEach((f) => f(ev)); return true; };
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = (o || {}).detail; } };
global.__rompRegisterPane = (id, k) => CALLS.register.push([id, k]);
global.__rompUnregisterPane = (id) => { CALLS.unregister.push(id); SEQ.push('unregister:' + id); };
global.__rompSplitShrink = (left, gone) => { CALLS.splitShrink.push([left, gone]); SEQ.push('shrink:' + gone); return true; };   // the gutters' hand-back (tests/test_pane_gutters.py runs the real one)
global.__rompGrowFair = (k) => CALLS.growFair.push('fair:' + k);
global.__rompGrowFairIfNew = (k) => CALLS.growFair.push(k);   // what the split calls: fair only when the store holds nothing
global.__rompSplitGrow = (left, key) => { CALLS.splitGrow.push([left, key]); return true; };   // the gutters' halving (tests/test_pane_gutters.py runs the real one)
global.__rompNotify = (kind, text) => CALLS.notify.push([kind, text]);
global.__rompPaneToggle = (k, to) => CALLS.toggle.push([k, to]);
global.__rompGutter = (gid, leftPick, rightId) => CALLS.gutter.push({ gid, leftPick, rightId });
global.__rompRowGutter = (gid, top, bot, apply) => { CALLS.rowGutter.push({ gid, top, bot }); ROWAPPLY = apply; };
global.__rompSeedAreaWeight = () => { CALLS.seedArea.push(true); return true; };   // the pre-rows store's upgrade hook (tests/test_pane_gutters.py runs the real one)
global.__rompWireFocus = (f) => CALLS.wireFocus.push(f.id);
global.__rompWireEsc = (f) => CALLS.wireEsc.push(f.id);
global.__rompColGone = (c) => CALLS.colGone.push(c);
global.__rompFocusedChatId = () => FOCUSED;
function boot(store, mobile) {
  STORE = Object.assign({}, store || {}); MOBILE = !!mobile; BYID = {}; WL = {}; TAKE = {}; FOCUSED = 'f-chat'; BODY_CLASSES = new Set(['po-chat', 'po-feed', 'po-timeline']);
  SEQ = []; UNMOVABLE = new Set(); LOCKED_SIDS = new Set(); BUSY = {}; ROWAPPLY = null;
  for (const k in CALLS) CALLS[k] = [];
  ROW = mkEl('div'); ROW.className = 'row';
  // the served markup (the chat rows, 2026-09-15): #chat-area wraps the top row (the first pane in it), the row gutter and the empty bottom row
  const area = mkEl('div'); area.id = 'chat-area';
  const r1 = mkEl('div'); r1.id = 'chat-row-1'; r1.className = 'chat-row';
  const cp = mkEl('div'); cp.id = 'chat-pane'; cp.className = 'pane'; const fc = mkEl('iframe'); fc.id = 'f-chat'; cp.appendChild(fc); r1.appendChild(cp);
  const gvr = mkEl('div'); gvr.id = 'gv-rows'; gvr.className = 'gh';
  const r2 = mkEl('div'); r2.id = 'chat-row-2'; r2.className = 'chat-row';
  area.appendChild(r1); area.appendChild(gvr); area.appendChild(r2); ROW.appendChild(area);
  const gva = mkEl('div'); gva.id = 'gv-a'; ROW.appendChild(gva);
  const fp = mkEl('div'); fp.id = 'fleet-pane'; ROW.appendChild(fp);
  const gvb = mkEl('div'); gvb.id = 'gv-b'; ROW.appendChild(gvb);
  const fd = mkEl('div'); fd.id = 'feed-pane'; ROW.appendChild(fd);
  const mt = mkEl('nav'); mt.id = 'mtabs';
  const cg = mkEl('div'); cg.id = 'col-ghost';   // the rectangle's element: a sibling of the row in the served markup, never a flex item of it
  ROW._rect = { left: 0, top: 30, width: 1400, height: 800 };
  area._rect = { left: 0, top: 30, width: 1400, height: 800 }; r1._rect = { left: 0, top: 30, width: 1400, height: 800 }; r2._rect = { left: 0, top: 830, width: 1400, height: 0 };   // one row: the top row is the area
  (0, eval)(SPLIT_JS);
}
const SPLIT_JS = __SPLIT_JS__;
function order() { return BYID['chat-row-1'].children.map((c) => c.id); }    // the top row, left to right
function order2() { return BYID['chat-row-2'].children.map((c) => c.id); }   // the bottom row
function outer() { return ROW.children.map((c) => c.id); }                   // the shell's row: the chat area, then the other panes
function areaState() { const a = BYID['chat-area']; return { cls: a.className, rs1: a.style._props['--rs1'], rs2: a.style._props['--rs2'] }; }
function ids() { return window.__rompChatFrameIds(); }
function cols() { return JSON.parse(STORE['romp-chat-cols'] || 'null'); }
function blob(n) { return JSON.parse(STORE['romp-vscode-state-chat:' + n] || 'null'); }
function saves() { return CALLS.sets.filter((k) => k === 'romp-chat-cols').length; }
function tgt(sid) { const f = window.__rompChatTarget(sid); return f ? f.id : null; }
function crossOf(fid) { return BYID[fid].parentElement.children.filter((c) => c.className === 'col-x')[0]; }
function msg(data, fromId) { window.dispatchEvent({ type: 'message', data, source: fromId ? BYID[fromId].contentWindow : null }); }
"""

DRIVER = r"""
const out = {};
const WEB = '11111111-2222-3333-4444-555555555501', API = '11111111-2222-3333-4444-555555555502', TESTS = '11111111-2222-3333-4444-555555555503', X = '11111111-2222-3333-4444-555555555509';
// A) a fresh desktop dashboard: one column, nothing made, every session the first column's
boot({}, false);
out.fresh = { ids: ids(), order: order(), order2: order2(), outer: outer(), area: areaState(), rowGutter: CALLS.rowGutter.slice(), lastPane: window.__rompLastChatPane(), sets: window.__rompChatSets(), stored: STORE['romp-chat-cols'] || null,
              targetUnknown: tgt(X), targetNone: tgt(''), saves: saves() };
// B) the v1 store (a bare array of column numbers) migrates once: a number whose blob names a session becomes that
//    session's column; a number with no session is dropped; the v2 shape is written back
boot({ 'romp-chat-cols': '[2]', 'romp-vscode-state-chat:2': JSON.stringify({ activeId: WEB, scroll: 4 }) }, false);
out.migrated = { ids: ids(), stored: cols(), blob2: blob(2), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), saves: saves(), srcs: [BYID['f-chat-2'].src] };
boot({ 'romp-chat-cols': '[2,5]', 'romp-vscode-state-chat:2': JSON.stringify({ activeId: WEB }), 'romp-vscode-state-chat:5': '{}' }, false);
out.migratedDrop = { ids: ids(), stored: cols() };
// C) the moves. A session to a NEW column: the store, the halves, the blob seed, no focus posted, the ring there
boot({}, false);
const f2 = window.__rompMoveTab(API, 'new'); f2.fire('load'); f2.fire('load');
out.moved = { id: f2.id, src: f2.src, col: f2.getAttribute('data-col'), pane: f2.parentElement.id, paneCls: f2.parentElement.className, flex: f2.parentElement.style.flex,
              order: order(), stored: cols(), sets: window.__rompChatSets(), blob2: blob(2), splitGrow: CALLS.splitGrow.slice(), growFair: CALLS.growFair.slice(),
              register: CALLS.register.slice(), wireFocus: CALLS.wireFocus.slice(), wireEsc: CALLS.wireEsc.slice(),
              gutter: CALLS.gutter.map((g) => ({ gid: g.gid, left: g.leftPick(), right: g.rightId })),
              event: CALLS.events.filter((e) => e.type === 'romp-chat-cols').map((e) => ({ col: e.detail.col, open: e.detail.open, frame: e.detail.frame && e.detail.frame.id })),
              posted: CALLS.posted.slice(), focused: CALLS.focus.slice(), taken: CALLS.taken.slice(), crossTitle: crossOf('f-chat-2').title,
              targetApi: tgt(API), targetTests: tgt(TESTS), canSplit: window.__rompCanSplit(), ids: ids(), lastPane: window.__rompLastChatPane(),
              paneOf: window.__rompChatPaneOf('f-chat-2'), colOf: window.__rompColOf(f2.contentWindow), frameOfWin: window.__rompFrameOfWin(f2.contentWindow) === f2 };
// a second new column halves the RIGHTMOST one; then a move BETWEEN later columns closes the emptied origin
const f3 = window.__rompMoveTab(TESTS, 'new');
out.third = { id: f3.id, splitGrow: CALLS.splitGrow.slice(), stored: cols(), gutterLeft3: CALLS.gutter.filter((g) => g.gid === 'gv-chat-3')[0].leftPick(), order: order() };
CALLS.posted = []; CALLS.focus = []; CALLS.unregister = []; CALLS.colGone = []; CALLS.taken = [];
const t3 = window.__rompMoveTab(API, 3);
out.movedInto3 = { target: t3 && t3.id, ids: ids(), order: order(), stored: cols(), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), focused: CALLS.focus.slice(),
                   unregister: CALLS.unregister.slice(), colGone: CALLS.colGone.slice(), taken: CALLS.taken.slice(),
                   targetApi: tgt(API), targetTests: tgt(TESTS), targetWeb: tgt(WEB),
                   closedEvent: CALLS.events.filter((e) => e.type === 'romp-chat-cols' && e.detail.open === false).map((e) => e.detail.col) };
FOCUSED = 'f-chat-77'; out.movedInto3.stale = tgt('');   // a stale focus id (its column is gone): the first column
FOCUSED = 'f-chat-3'; out.movedInto3.focusedNone = tgt('');   // no session named: the column last worked in
FOCUSED = 'f-chat';
// …and HOME (the first column, which derives): the origin keeps its other member
CALLS.posted = []; CALLS.focus = [];
const t1 = window.__rompMoveTab(API, 1);
out.movedHome = { target: t1 && t1.id, ids: ids(), stored: cols(), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), focused: CALLS.focus.slice(), targetApi: tgt(API) };
// refusals, each with its line or a null: alone in its column, no such column, no session, already there
CALLS.notify = []; CALLS.posted = []; CALLS.sets = [];
out.refused = { alone: window.__rompMoveTab(TESTS, 'new'), noSuch: window.__rompMoveTab(API, 7), noSid: window.__rompMoveTab('', 'new'),
                same: (window.__rompMoveTab(TESTS, 3) || {}).id, sameHome: (window.__rompMoveTab(API, 1) || {}).id,
                notify: CALLS.notify.slice(), stored: cols(), posted: CALLS.posted.length, saves: saves() };
// the palette's move: nothing active says so; the focused column's active tab moves to a new column (the lowest free number)
CALLS.notify = []; BYID['f-chat']._active = '';
out.paletteNone = { r: window.__rompSplitChat(), notify: CALLS.notify.slice() };
BYID['f-chat']._active = WEB; CALLS.notify = []; BODY_CLASSES.delete('po-chat'); CALLS.toggle = [];
const fp = window.__rompSplitChat();
out.paletteMove = { id: fp && fp.id, stored: cols(), notify: CALLS.notify.slice(), toggle: CALLS.toggle.slice(), blob2: blob(2) };
BODY_CLASSES.add('po-chat');
// D) EMPTINESS: a column's page says which members the kernel no longer lists; the entry is pruned, and closes only when empty
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 2);
CALLS.sets = []; CALLS.unregister = [];
msg({ romp: 'colEmpty', gone: [TESTS] }, 'f-chat-2');
out.colEmptyPartial = { ids: ids(), stored: cols(), saves: saves() };
msg({ romp: 'colEmpty', gone: [API] }, 'f-chat');   // from a page with no entry (the first column): ignored
out.colEmptyIgnored = { ids: ids(), stored: cols() };
window.__rompMoveTab(X, 2);   // a member added meanwhile…
msg({ romp: 'colEmpty', gone: [API] }, 'f-chat-2');   // …keeps the column open when the others go
out.colEmptyKept = { ids: ids(), stored: cols() };
msg({ romp: 'colEmpty', gone: [X] }, 'f-chat-2');
out.colEmptyAll = { ids: ids(), stored: cols(), unregister: CALLS.unregister.slice() };
// E) another dashboard tab's write reconciles: what it added is made (seeded like a restore), what it dropped closes, nothing is written back
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] }) }, false);
CALLS.sets = []; CALLS.posted = [];
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }, { n: 4, ids: [API, TESTS] }] });
STORE['romp-vscode-state-chat:4'] = JSON.stringify({ activeId: TESTS, scroll: 9 });
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.reconciled = { ids: ids(), order: order(), sets: window.__rompChatSets(), saves: saves(), blob4: blob(4), stored: STORE['romp-chat-cols'], posted: CALLS.posted.slice() };
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [{ n: 4, ids: [API, TESTS] }] });
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.reconciledClose = { ids: ids(), sets: window.__rompChatSets(), saves: saves(), unregister: CALLS.unregister.slice() };
window.dispatchEvent({ type: 'storage', key: 'romp-pane-grow' });   // another key: nothing
out.reconciledOther = { ids: ids() };
// F) the CROSS returns the column's sessions home, drafts and all
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 2);
TAKE['f-chat-2'] = { [API]: { draft: 'a draft for api', citations: [], files: [], staged: [] } };
CALLS.posted = []; CALLS.taken = [];
crossOf('f-chat-2').fire('click', { stopPropagation() {} });
out.cross = { ids: ids(), stored: cols(), sets: window.__rompChatSets(), posted: CALLS.posted.slice(), taken: CALLS.taken.slice(), targetApi: tgt(API), targetTests: tgt(TESTS) };
// G) DRAFTS TRAVEL on a move: to a new column on its load, once; into an open column at once, ahead of the focus
boot({}, false);
TAKE['f-chat'] = { [TESTS]: { draft: 'typed in column one', citations: [{ title: 'a card' }], files: ['/tmp/a.png'], staged: [] } };
CALLS.posted = []; CALLS.taken = [];
const fn2 = window.__rompMoveTab(TESTS, 'new');
out.draftsNew = { id: fn2.id, postedBeforeLoad: CALLS.posted.slice(), taken: CALLS.taken.slice() };
fn2.fire('load'); out.draftsNew.postedAfterLoad = CALLS.posted.slice();
fn2.fire('load'); out.draftsNew.postedAfterSecondLoad = CALLS.posted.length;
const fo = window.__rompMoveTab(WEB, 'new');   // column 3, holding WEB
CALLS.posted = []; CALLS.taken = [];
TAKE['f-chat-2'] = { [TESTS]: { draft: 'more', citations: [], files: [], staged: [{ text: 's', cites: [] }] } };
const to3 = window.__rompMoveTab(TESTS, 3);
out.draftsOpen = { target: to3 && to3.id, posted: CALLS.posted.slice(), taken: CALLS.taken.slice(), ids: ids(), stored: cols() };
// H) a session CREATED from a later column is claimed for it, once, and never stolen from a column that lists it
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(WEB, 'new');
out.claim = { first: window.__rompClaimSession(X, 2), again: window.__rompClaimSession(X, 2), steal: window.__rompClaimSession(X, 3), listed: window.__rompClaimSession(API, 3),
              noSid: window.__rompClaimSession('', 2), noCol: window.__rompClaimSession(TESTS, 9), stored: cols(), targetX: tgt(X) };
// I) the RESTORE seeds each column's blob with the tab it names when that is still a member, else its first member
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB, API] }, { n: 5, ids: [TESTS] }] }), 'romp-vscode-state-chat:2': JSON.stringify({ activeId: API, scroll: 3 }) }, false);
out.restored = { ids: ids(), order: order(), blob2: blob(2), blob5: blob(5), posted: CALLS.posted.slice(), sets: window.__rompChatSets(), saves: saves(),
                 srcs: [BYID['f-chat-2'].src, BYID['f-chat-5'].src], lastPane: window.__rompLastChatPane(), growFair: CALLS.growFair.slice(), splitGrow: CALLS.splitGrow.slice(),
                 nextNumber: (window.__rompMoveTab(X, 'new') || {}).id };
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB, API] }] }), 'romp-vscode-state-chat:2': JSON.stringify({ activeId: TESTS }) }, false);
out.restoredMovedAway = { blob2: blob(2) };
// a corrupt store: nothing made, nothing thrown
boot({ 'romp-chat-cols': 'not json{' }, false);
out.corrupt = { ids: ids(), stored: STORE['romp-chat-cols'] };
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [] }, { n: 'x', ids: [WEB] }, { n: 3, ids: [API, API] }, { n: 4, ids: [API] }] }) }, false);
out.sanitised = { ids: ids(), sets: window.__rompChatSets() };
// J) four columns at most
boot({}, false);
window.__rompMoveTab(WEB, 'new'); window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new');
CALLS.notify = [];
out.cap = { ids: ids(), fourth: window.__rompMoveTab(X, 'new'), notify: CALLS.notify.slice(), canSplit: window.__rompCanSplit(), stored: cols() };
// K) the phone: nothing is restored, the one chat filters nothing, a move is refused, the store keeps the desktop's arrangement
const PHONE_STORE = JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] });
boot({ 'romp-chat-cols': PHONE_STORE }, true);
out.mobile = { ids: ids(), sets: window.__rompChatSets(), moved: window.__rompMoveTab(WEB, 'new'), movedBelow: window.__rompMoveTab(WEB, 'below'), order: order(), order2: order2(), outer: outer(), area: areaState(), notify: CALLS.notify.slice(), canSplit: window.__rompCanSplit(),
               stored: STORE['romp-chat-cols'], storedWas: PHONE_STORE, saves: saves(), target: tgt(WEB) };
// L) REFUSALS the page decides (review finds 2026-09-11): an id no column can hold (a create in flight, a sub-agent viewer)
//    is refused at the one mutation with a line and nothing changes, from the palette too; a column whose last listed
//    member would leave over a create in flight keeps it — from a move, the cross and the palette's close alike — while a
//    column with two members lets one go, and the create resolving frees it; another dashboard tab's write is the truth
boot({}, false);
window.__rompMoveTab(API, 'new');
const PROV = 'new-abc123', VIEWER = WEB + '/agent/a1';
UNMOVABLE.add(PROV); UNMOVABLE.add(VIEWER);
CALLS.notify = []; CALLS.sets = []; CALLS.taken = []; CALLS.splitGrow = [];
out.unmovable = { prov: window.__rompMoveTab(PROV, 'new'), provInto2: window.__rompMoveTab(PROV, 2), viewer: window.__rompMoveTab(VIEWER, 'new'),
                  notify: CALLS.notify.slice(), stored: cols(), ids: ids(), saves: saves(), taken: CALLS.taken.slice(), grown: CALLS.splitGrow.slice() };
BYID['f-chat']._active = PROV; CALLS.notify = [];
out.unmovable.palette = { r: window.__rompSplitChat(), notify: CALLS.notify.slice(), stored: cols(), ids: ids() };
BYID['f-chat']._active = '';
// the tab lock (T395 round one): the page answers 'locked', and the toast names the gear's menu (T405), not "an open session"
LOCKED_SIDS.add(API); CALLS.notify = [];
out.locked = { r: window.__rompMoveTab(API, 2), notify: CALLS.notify.slice(), stored: cols(), ids: ids() };
LOCKED_SIDS = new Set();
// an OLDER chat bundle (no __rompMoveRefusal on its frame): the shell falls to the movable question and its one line (round two, LOW 3)
const olderWin = BYID['f-chat'].contentWindow, refusalFn = olderWin.__rompMoveRefusal;
delete olderWin.__rompMoveRefusal; UNMOVABLE.add(PROV); CALLS.notify = [];
out.older = { r: window.__rompMoveTab(PROV, 'new'), notify: CALLS.notify.slice(), hasField: typeof olderWin.__rompMoveRefusal };
olderWin.__rompMoveRefusal = refusalFn; UNMOVABLE = new Set();
BUSY['f-chat-2'] = true; CALLS.notify = []; CALLS.sets = []; CALLS.taken = []; CALLS.unregister = [];
out.busy = { home: window.__rompMoveTab(API, 1), ids: ids(), stored: cols(), notify: CALLS.notify.slice(), saves: saves(), taken: CALLS.taken.slice() };
crossOf('f-chat-2').fire('click', { stopPropagation() {} });
out.busy.cross = { ids: ids(), stored: cols(), notify: CALLS.notify.slice(), unregister: CALLS.unregister.slice(), taken: CALLS.taken.slice() };
window.__rompCloseSplit(2);
out.busy.palette = { ids: ids(), notify: CALLS.notify.length };
window.__rompMoveTab(TESTS, 2); CALLS.notify = [];
out.busy.twoMembers = { home: (window.__rompMoveTab(TESTS, 1) || {}).id, stored: cols(), notify: CALLS.notify.slice(), ids: ids() };
BUSY['f-chat-2'] = false; CALLS.notify = [];
out.busy.thenFree = { home: (window.__rompMoveTab(API, 1) || {}).id, ids: ids(), stored: cols(), notify: CALLS.notify.slice() };
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] }) }, false);
BUSY['f-chat-2'] = true; STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [] }); CALLS.notify = [];
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.busy.reconciled = { ids: ids(), notify: CALLS.notify.slice() };
// M) a colEmpty that closes a column tells the first column which of its gone ids the page's own cross removed (crossed),
//    ahead of the store write; a prune that leaves members says nothing; a gone id nobody crossed is not held (it is the
//    first column's the moment its strip repaints); a crossed id the entry did not hold is ignored
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 2);
CALLS.posted = []; SEQ = [];
msg({ romp: 'colEmpty', gone: [TESTS], crossed: [TESTS] }, 'f-chat-2');
out.closing = { partial: CALLS.posted.slice() };
CALLS.posted = []; SEQ = [];
msg({ romp: 'colEmpty', gone: [API, X], crossed: [API, X] }, 'f-chat-2');
out.closing.all = { posted: CALLS.posted.slice(), seq: SEQ.filter((x) => x.indexOf('post:') === 0 || x === 'set:romp-chat-cols'), ids: ids(), stored: cols() };
boot({}, false);
window.__rompMoveTab(API, 'new');
CALLS.posted = [];
msg({ romp: 'colEmpty', gone: [API] }, 'f-chat-2');   // no cross named (a page misled by a stale frame, an older page): the column closes, nothing is held
out.closing.uncrossed = { posted: CALLS.posted.filter((p) => p.m && p.m.romp === 'closing'), ids: ids(), stored: cols() };
boot({}, false);
window.__rompMoveTab(API, 'new');
CALLS.posted = [];
msg({ romp: 'colEmpty', gone: [API], crossed: [] }, 'f-chat-2');
out.closing.emptyCross = { posted: CALLS.posted.filter((p) => p.m && p.m.romp === 'closing'), ids: ids(), stored: cols() };
// N) ORPHANED STATE: a page's offer of state for sessions it does not show is taken from it and handed to the owner's page
//    when that page can hear it; its own member and junk are skipped; a target not yet evaluated leaves the state where it is
boot({ 'romp-chat-cols': '[2]', 'romp-vscode-state-chat:2': JSON.stringify({ activeId: WEB, drafts: { [WEB]: 'a', [API]: 'b' } }) }, false);
window.__rompMoveTab(TESTS, 'new');   // column 3 holds TESTS
TAKE['f-chat-2'] = { [API]: { draft: 'b', citations: [], files: [], staged: [] }, [TESTS]: { draft: 't', citations: [], files: [], staged: [] }, [WEB]: { draft: 'a', citations: [], files: [], staged: [] } };
CALLS.posted = []; CALLS.taken = [];
msg({ romp: 'orphanState', sids: [API, TESTS, WEB, '', 7] }, 'f-chat-2');
out.orphan = { posted: CALLS.posted.slice(), taken: CALLS.taken.slice(), left: Object.keys(TAKE['f-chat-2']), stored: cols() };
delete BYID['f-chat'].contentWindow.__rompTakeSessionState;   // the first column's bundle has not evaluated: it cannot hear an adopt
TAKE['f-chat-2'] = { [API]: { draft: 'b', citations: [], files: [], staged: [] } };
CALLS.posted = []; CALLS.taken = [];
msg({ romp: 'orphanState', sids: [API] }, 'f-chat-2');
out.orphan.unloaded = { posted: CALLS.posted.slice(), taken: CALLS.taken.slice(), left: Object.keys(TAKE['f-chat-2']) };
msg({ romp: 'orphanState', sids: [API] });   // from no chat column: nothing
out.orphan.unknown = CALLS.posted.length;
// O) a closing column's width goes to the column on its left, measured before its key is dropped
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new');
CALLS.splitShrink = []; CALLS.unregister = []; SEQ = [];
window.__rompCloseSplit(3);
out.shrink = { third: CALLS.splitShrink.slice(), unregister: CALLS.unregister.slice(), seq: SEQ.filter((x) => x.indexOf('set:') !== 0) };
CALLS.splitShrink = [];
window.__rompCloseSplit(2);
out.shrink.second = CALLS.splitShrink.slice();
console.log(JSON.stringify(out));
"""


class SplitExecutes(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        script = HARNESS.replace("__SPLIT_JS__", json.dumps(km._LANDING_SPLIT_JS)) + DRIVER
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the split's JS threw: " + r.stderr[:1200]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_fresh_dashboard_has_one_column_that_holds_everything(self):
        o = self.out["fresh"]
        self.assertEqual(o["ids"], ["f-chat"])
        self.assertEqual(o["order"], ["chat-pane"], "the top row holds the first pane alone")
        self.assertEqual(o["order2"], [], "no bottom row")
        self.assertEqual(o["outer"], ["chat-area", "gv-a", "fleet-pane", "gv-b", "feed-pane"], "the shell's row: the chat area in the first pane's place")
        self.assertEqual(o["area"], {"cls": "", "rs1": 50, "rs2": 50}, "no .rows class: the bottom row and its gutter stay hidden; the half is the default share, as weights out of 100")
        self.assertEqual(o["rowGutter"], [{"gid": "gv-rows", "top": "chat-row-1", "bot": "chat-row-2"}], "the row gutter is wired once, at boot")
        self.assertEqual(o["lastPane"], "chat-pane")
        self.assertEqual(o["sets"], {}, "no later columns: the pages filter nothing")
        self.assertIsNone(o["stored"], "nothing written until something changes")
        self.assertEqual(o["saves"], 0)
        self.assertEqual(o["targetUnknown"], "f-chat", "a session no entry lists is the first column's")
        self.assertEqual(o["targetNone"], "f-chat", "no session named: the column last worked in, here the first")

    def test_a_v1_store_migrates_once_to_the_sessions_the_blobs_named(self):
        m = self.out["migrated"]
        self.assertEqual(m["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(m["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}]}, "written back in the new shape")
        self.assertEqual(m["saves"], 1, "…once")
        self.assertEqual(m["sets"], {"2": [WEB]})
        self.assertEqual(m["blob2"], {"activeId": WEB, "scroll": 4}, "the blob is re-seeded with the same tab: every other field kept")
        self.assertEqual(m["posted"], [], "no focus, no adopt: the blob names the tab")
        self.assertEqual(m["srcs"], ["/chat?col=2&skeleton=1"])
        d = self.out["migratedDrop"]
        self.assertEqual(d["ids"], ["f-chat", "f-chat-2"], "a number whose blob names no session is dropped")
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}]})

    def test_a_move_to_a_new_column_makes_a_wired_column_holding_the_session_alone(self):
        o = self.out["moved"]
        self.assertEqual(o["id"], "f-chat-2")
        self.assertEqual(o["src"], "/chat?col=2&skeleton=1", "its own state blob + connect params (the shim); a skeleton client of its session")
        self.assertEqual(o["col"], "2")
        self.assertEqual(o["pane"], "chat-pane-2")
        self.assertEqual(o["paneCls"], "pane chat-col")
        self.assertEqual(o["flex"], "var(--g-chat2,60) 1 0", "its own grow var, the gutters' store")
        self.assertEqual(o["order"], ["chat-pane", "gv-chat-2", "chat-pane-2"], "appended to the top row behind its gutter")
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "no row, no rowSplit: the shape from before the rows")
        self.assertEqual(o["sets"], {"2": [API]}, "what every column page filters by")
        self.assertEqual(o["blob2"], {"activeId": API}, "the blob names the session BEFORE the frame exists: the shim's hint, the page's wantActive")
        self.assertEqual(o["splitGrow"], [["chat-pane", "chat2"]], "the rightmost column and the new one each take half its width")
        self.assertEqual(o["growFair"], ["chat2"], "then the store-aware hook finds the half and keeps it")
        self.assertEqual(o["register"], [["chat-pane-2", "chat2"]])
        self.assertEqual(o["wireFocus"], ["f-chat-2"])
        self.assertEqual(o["wireEsc"], ["f-chat-2"])
        self.assertEqual(o["gutter"], [{"gid": "gv-chat-2", "left": "chat-pane", "right": "chat-pane-2"}])
        self.assertEqual(o["event"], [{"col": 2, "open": True, "frame": "f-chat-2"}], "palette-main wires its chords on this")
        self.assertEqual(o["posted"], [], "no hand-over focus and, with nothing held for the session, no adopt — though load fired twice")
        self.assertEqual(o["taken"], [["f-chat", API, False]], "the source page was asked for the session's drafts, once")
        self.assertEqual(o["focused"], ["f-chat-2"], "the new column takes the keyboard")
        self.assertEqual(o["crossTitle"], "Close this column")
        self.assertEqual(o["targetApi"], "f-chat-2", "a focus for the session belongs to the column holding it")
        self.assertEqual(o["targetTests"], "f-chat", "…and one for a session no entry lists to the first column")
        self.assertTrue(o["canSplit"])
        self.assertEqual(o["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(o["lastPane"], "chat-pane-2", "gv-a's left neighbour is now the new column")
        self.assertEqual(o["paneOf"], "chat-pane-2")
        self.assertEqual(o["colOf"], "2")
        self.assertTrue(o["frameOfWin"])

    def test_a_move_between_columns_lands_in_the_target_and_closes_an_emptied_origin(self):
        t = self.out["third"]
        self.assertEqual(t["id"], "f-chat-3")
        self.assertEqual(t["splitGrow"], [["chat-pane", "chat2"], ["chat-pane-2", "chat3"]], "the second new column halves the RIGHTMOST column")
        self.assertEqual(t["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}, {"n": 3, "ids": [TESTS]}]})
        self.assertEqual(t["order"], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-3", "chat-pane-3"])
        self.assertEqual(t["gutterLeft3"], "chat-pane-2")
        m = self.out["movedInto3"]
        self.assertEqual(m["target"], "f-chat-3", "the target's iframe comes back")
        self.assertEqual(m["ids"], ["f-chat", "f-chat-3"], "column 2 held only the moved session: it closed")
        self.assertEqual(m["order"], ["chat-pane", "gv-chat-3", "chat-pane-3"])
        self.assertEqual(m["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS, API]}]}, "the id joins the target's entry; the emptied entry is gone whole")
        self.assertEqual(m["sets"], {"3": [TESTS, API]})
        self.assertEqual(m["posted"], [{"id": "f-chat-3", "m": {"type": "focus", "id": API}}], "a plain focus into the target: it is the owner now, so its own gate takes it; no `own`")
        self.assertEqual(m["taken"], [["f-chat-2", API, False]], "the drafts were asked of the SOURCE column")
        self.assertEqual(m["unregister"], ["chat-pane-2"], "the closed column's grow leaves the store")
        self.assertEqual(m["colGone"], ["2"], "the Log drops its connection state")
        self.assertEqual(m["closedEvent"], [2])
        self.assertEqual(m["focused"][-1], "f-chat-3", "the ring ends on the target, not on the closed origin's neighbour")
        self.assertEqual(m["targetApi"], "f-chat-3")
        self.assertEqual(m["targetTests"], "f-chat-3")
        self.assertEqual(m["targetWeb"], "f-chat")
        self.assertEqual(m["stale"], "f-chat", "a stale focus id falls back to the first column")
        self.assertEqual(m["focusedNone"], "f-chat-3", "no session named: the column last worked in")
        h = self.out["movedHome"]
        self.assertEqual(h["target"], "f-chat", "the first column derives: the id simply leaves its entry")
        self.assertEqual(h["ids"], ["f-chat", "f-chat-3"], "the origin keeps its other member")
        self.assertEqual(h["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS]}]})
        self.assertEqual(h["sets"], {"3": [TESTS]})
        self.assertEqual(h["posted"], [{"id": "f-chat", "m": {"type": "focus", "id": API}}])
        self.assertEqual(h["focused"], ["f-chat"])
        self.assertEqual(h["targetApi"], "f-chat")

    def test_a_refused_move_says_why_or_changes_nothing(self):
        r = self.out["refused"]
        self.assertIsNone(r["alone"], "a session alone in a later column has nowhere new to go")
        self.assertIsNone(r["noSuch"], "no such column")
        self.assertIsNone(r["noSid"])
        self.assertEqual(r["same"], "f-chat-3", "already there: the owner's frame, nothing moves")
        self.assertEqual(r["sameHome"], "f-chat")
        self.assertEqual(r["notify"], [["warn", "This session is already alone in its column."]], "the one refusal that is a gesture with nothing to do says so")
        self.assertEqual(r["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS]}]}, "the store is untouched")
        self.assertEqual(r["posted"], 0)
        self.assertEqual(r["saves"], 0, "no write for a refusal or a no-op")
        p = self.out["paletteNone"]
        self.assertIsNone(p["r"])
        self.assertEqual(p["notify"], [["warn", "No session is open in this column to move."]])
        q = self.out["paletteMove"]
        self.assertEqual(q["id"], "f-chat-2", "the focused column's active tab moves to a new column, the lowest free number")
        self.assertEqual(q["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS]}, {"n": 2, "ids": [WEB]}]}, "row order, not number order")
        self.assertEqual(q["notify"], [])
        self.assertEqual(q["toggle"], [["chat", True]], "a hidden chat group is brought forward first")
        self.assertEqual(q["blob2"], {"activeId": WEB})

    def test_a_column_s_emptiness_prunes_its_entry_and_closes_it_only_when_nothing_is_left(self):
        p = self.out["colEmptyPartial"]
        self.assertEqual(p["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(p["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "the gone id leaves the entry")
        self.assertEqual(p["saves"], 1)
        self.assertEqual(self.out["colEmptyIgnored"]["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "a page with no entry says nothing that changes anything")
        k = self.out["colEmptyKept"]
        self.assertEqual(k["ids"], ["f-chat", "f-chat-2"], "a member added meanwhile keeps the column open")
        self.assertEqual(k["stored"], {"v": 2, "cols": [{"n": 2, "ids": [X]}]})
        a = self.out["colEmptyAll"]
        self.assertEqual(a["ids"], ["f-chat"], "the last member gone: the column closes")
        self.assertEqual(a["stored"], {"v": 2, "cols": []})
        self.assertEqual(a["unregister"], ["chat-pane-2"])

    def test_another_dashboard_tab_s_write_is_reconciled_without_writing_back(self):
        r = self.out["reconciled"]
        self.assertEqual(r["ids"], ["f-chat", "f-chat-2", "f-chat-4"], "the column the other tab added is made here")
        self.assertEqual(r["order"][:5], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(r["sets"], {"2": [WEB], "4": [API, TESTS]})
        self.assertEqual(r["saves"], 0, "the other tab's store is the truth: nothing is written back")
        self.assertEqual(r["blob4"], {"activeId": TESTS, "scroll": 9}, "seeded like a restore: the blob's tab, a member, stands")
        self.assertEqual(r["posted"], [])
        c = self.out["reconciledClose"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-4"], "the column the other tab dropped closes here")
        self.assertEqual(c["sets"], {"4": [API, TESTS]})
        self.assertEqual(c["saves"], 0)
        self.assertEqual(c["unregister"], ["chat-pane-2"])
        self.assertEqual(self.out["reconciledOther"]["ids"], ["f-chat", "f-chat-4"], "another key's event changes nothing")

    def test_the_cross_returns_the_column_s_sessions_to_the_first_column_with_their_drafts(self):
        c = self.out["cross"]
        self.assertEqual(c["ids"], ["f-chat"])
        self.assertEqual(c["stored"], {"v": 2, "cols": []}, "the entry goes whole: the first column derives both sessions")
        self.assertEqual(c["sets"], {})
        self.assertEqual(c["taken"], [["f-chat-2", API, True], ["f-chat-2", TESTS, False]], "the closing page is asked for each member's drafts")
        self.assertEqual(c["posted"], [{"id": "f-chat", "m": {"romp": "adopt", "sid": API, "state": {"draft": "a draft for api", "citations": [], "files": [], "staged": []}}}],
                         "what it held lands in the first column's page; a session with nothing held posts nothing")
        self.assertEqual(c["targetApi"], "f-chat")
        self.assertEqual(c["targetTests"], "f-chat")

    def test_drafts_travel_with_a_moved_tab(self):
        n = self.out["draftsNew"]
        self.assertEqual(n["id"], "f-chat-2")
        self.assertEqual(n["taken"], [["f-chat", TESTS, True]], "taken from the source, synchronously, before the frame exists")
        self.assertEqual(n["postedBeforeLoad"], [], "a new frame cannot hear yet")
        self.assertEqual(n["postedAfterLoad"], [{"id": "f-chat-2", "m": {"romp": "adopt", "sid": TESTS,
                                                  "state": {"draft": "typed in column one", "citations": [{"title": "a card"}], "files": ["/tmp/a.png"], "staged": []}}}],
                         "…and adopts on its load")
        self.assertEqual(n["postedAfterSecondLoad"], 1, "once: a later reload of the frame has them in its own blob")
        o = self.out["draftsOpen"]
        self.assertEqual(o["target"], "f-chat-3")
        self.assertEqual(o["taken"], [["f-chat-2", TESTS, True]])
        self.assertEqual(o["posted"], [{"id": "f-chat-3", "m": {"romp": "adopt", "sid": TESTS, "state": {"draft": "more", "citations": [], "files": [], "staged": [{"text": "s", "cites": []}]}}},
                                       {"id": "f-chat-3", "m": {"type": "focus", "id": TESTS}}], "an open column adopts at once, ahead of the focus that shows the tab")
        self.assertEqual(o["ids"], ["f-chat", "f-chat-3"], "the origin, left empty, closed")
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 3, "ids": [WEB, TESTS]}]})

    def test_a_created_session_is_claimed_for_its_column_once_and_never_stolen(self):
        c = self.out["claim"]
        self.assertTrue(c["first"], "a session no entry lists joins the creating column")
        self.assertFalse(c["again"], "…once")
        self.assertFalse(c["steal"], "a session another column lists is never taken")
        self.assertFalse(c["listed"])
        self.assertFalse(c["noSid"])
        self.assertFalse(c["noCol"])
        self.assertEqual(c["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API, X]}, {"n": 3, "ids": [WEB]}]})
        self.assertEqual(c["targetX"], "f-chat-2")

    def test_the_restore_seeds_each_column_with_a_member_and_reads_a_bad_store_forgivingly(self):
        r = self.out["restored"]
        self.assertEqual(r["ids"], ["f-chat", "f-chat-2", "f-chat-5"])
        self.assertEqual(r["order"][:5], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-5", "chat-pane-5"])
        self.assertEqual(r["blob2"], {"activeId": API, "scroll": 3}, "the blob's tab is still a member: it stands, every other field kept")
        self.assertEqual(r["blob5"], {"activeId": TESTS}, "no blob: seeded with the first member")
        self.assertEqual(r["srcs"], ["/chat?col=2&skeleton=1", "/chat?col=5&skeleton=1"], "a restored column dials as a skeleton client of the seeded tab")
        self.assertEqual(r["posted"], [], "no focus, no adopt at a restore")
        self.assertEqual(r["sets"], {"2": [WEB, API], "5": [TESTS]})
        self.assertEqual(r["saves"], 0, "a v2 store is not rewritten at boot")
        self.assertEqual(r["growFair"], ["chat2", "chat5"], "a restored column takes its stored width, else the fair average")
        self.assertEqual(r["splitGrow"], [], "…never the halving, which is a move's")
        self.assertEqual(r["lastPane"], "chat-pane-5")
        self.assertEqual(r["nextNumber"], "f-chat-3", "a new column takes the lowest free number")
        self.assertEqual(self.out["restoredMovedAway"]["blob2"], {"activeId": WEB}, "the blob's tab was moved away while the browser was closed: the first member")
        self.assertEqual(self.out["corrupt"]["ids"], ["f-chat"], "a corrupt store makes nothing and throws nothing")
        s = self.out["sanitised"]
        self.assertEqual(s["ids"], ["f-chat", "f-chat-3"], "an empty entry, a non-numeric number and an entry whose ids were all claimed earlier are dropped")
        self.assertEqual(s["sets"], {"3": [API]}, "an id is in one entry, once")

    def test_four_columns_at_most(self):
        c = self.out["cap"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-2", "f-chat-3", "f-chat-4"])
        self.assertIsNone(c["fourth"])
        self.assertEqual(c["notify"], [["warn", "Four chat columns at most — close one to open another."]], "a refused move says why")
        self.assertFalse(c["canSplit"], "…and the tab menu can ask before offering the item")
        self.assertEqual(c["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}, {"n": 3, "ids": [API]}, {"n": 4, "ids": [TESTS]}]})

    def test_an_id_no_column_can_hold_is_refused_at_the_one_mutation_with_a_line(self):
        # a create in flight and a sub-agent viewer carry data-id on the strip, and the palette's DOM read can name them
        # (review find 2026-09-11: a column opened on a provisional id the kernel does not know flashed open and shut)
        u = self.out["unmovable"]
        self.assertIsNone(u["prov"]); self.assertIsNone(u["provInto2"]); self.assertIsNone(u["viewer"])
        self.assertEqual(u["notify"], [["warn", "Only an open session can be moved between columns."]] * 3, "each refusal says why")
        self.assertEqual(u["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "the store is untouched")
        self.assertEqual(u["ids"], ["f-chat", "f-chat-2"], "no column opened")
        self.assertEqual(u["saves"], 0); self.assertEqual(u["grown"], [], "nothing halved")
        self.assertEqual(u["taken"], [], "no page was asked for drafts: refused before the hand-off")
        p = u["palette"]
        self.assertIsNone(p["r"], "the palette's move of a create in flight (the active tab) is the same refusal")
        self.assertEqual(p["notify"], [["warn", "Only an open session can be moved between columns."]])
        self.assertEqual(p["stored"], u["stored"]); self.assertEqual(p["ids"], ["f-chat", "f-chat-2"])

    def test_a_locked_page_refuses_the_move_with_a_toast_that_names_the_gears_menu(self):
        # T395 round one (MEDIUM 2): the page's answer carries its reason; a lock is not "not an open session", and the toast
        # says the way back
        l = self.out["locked"]
        self.assertIsNone(l["r"])
        self.assertEqual(l["notify"], [["warn", "The tabs are locked: unlock them in the settings (Chat, Tab strip) to move this session."]])   # T415: the lock is a switch in the settings
        self.assertEqual(l["stored"], self.out["unmovable"]["stored"], "the store is untouched")
        self.assertEqual(l["ids"], self.out["unmovable"]["ids"], "no column opened")

    def test_an_older_bundle_without_the_refusal_field_falls_to_the_movable_question(self):
        # round two, LOW 3: a chat page from before the refusal field answers only the movable question, and the shell's one line stands
        o = self.out["older"]
        self.assertEqual(o["hasField"], "undefined", "the frame was minted without the field")
        self.assertIsNone(o["r"])
        self.assertEqual(o["notify"], [["warn", "Only an open session can be moved between columns."]])

    def test_a_column_with_a_create_in_flight_keeps_its_last_member_and_stays_open(self):
        # its queued text and draft would die with the document (review find 2026-09-11): the move that would empty it,
        # its cross and the palette's close are refused with the line; a column with two members lets one go; the create
        # resolving frees it; another dashboard tab's write is the truth and is not refused
        b = self.out["busy"]
        self.assertIsNone(b["home"], "the move that would empty the column is refused")
        self.assertEqual(b["notify"], [["warn", "A session is still being created in this column."]])
        self.assertEqual(b["ids"], ["f-chat", "f-chat-2"]); self.assertEqual(b["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]})
        self.assertEqual(b["saves"], 0); self.assertEqual(b["taken"], [], "nothing was taken from the page: refused before the hand-off")
        c = b["cross"]
        self.assertEqual(c["ids"], ["f-chat", "f-chat-2"], "the cross is refused too: the column would die with the create")
        self.assertEqual(c["notify"], [["warn", "A session is still being created in this column."]] * 2)
        self.assertEqual(c["unregister"], []); self.assertEqual(c["taken"], [])
        self.assertEqual(b["palette"], {"ids": ["f-chat", "f-chat-2"], "notify": 3}, "…and the palette's close")
        t = b["twoMembers"]
        self.assertEqual(t["home"], "f-chat", "with two members one may leave: the column stays")
        self.assertEqual(t["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}); self.assertEqual(t["notify"], []); self.assertEqual(t["ids"], ["f-chat", "f-chat-2"])
        f = b["thenFree"]
        self.assertEqual(f["home"], "f-chat", "the create resolved: the last member leaves and the column closes")
        self.assertEqual(f["ids"], ["f-chat"]); self.assertEqual(f["stored"], {"v": 2, "cols": []}); self.assertEqual(f["notify"], [])
        self.assertEqual(b["reconciled"], {"ids": ["f-chat"], "notify": []}, "another dashboard tab's write closes a busy column all the same, and says nothing")

    def test_a_column_closed_for_emptiness_tells_the_first_column_which_ids_are_on_their_way_home(self):
        # the kernel may still list a member closed from its own cross for a push or two, and the first column would draw
        # its tab until then (review find 2026-09-11): the CROSSED ids ride ahead of the store write, so the first column's
        # page holds them back (closingTabs) until the kernel's strip omits them. Only those: the backstop behind that hold
        # toasts "Couldn't close", right for a refused cross and wrong for anything else — a hold over a session the kernel
        # still listed hid the tab for fifteen seconds and toasted a close nobody asked for (the vanishing tab, 2026-09-12)
        c = self.out["closing"]
        self.assertEqual(c["partial"], [], "a prune that leaves members posts nothing")
        a = c["all"]
        self.assertEqual(a["posted"], [{"id": "f-chat", "m": {"romp": "closing", "ids": [API]}}], "the entry's members the page reported crossed, never an id it did not hold")
        self.assertEqual(a["seq"], ["post:f-chat:closing", "set:romp-chat-cols"], "the message is queued ahead of the store write's storage event")
        self.assertEqual(a["ids"], ["f-chat"]); self.assertEqual(a["stored"], {"v": 2, "cols": []})
        for key in ("uncrossed", "emptyCross"):
            u = c[key]
            self.assertEqual(u["posted"], [], "%s: a gone id nobody crossed is not held — the first column shows it the moment its strip repaints" % key)
            self.assertEqual(u["ids"], ["f-chat"], "%s: the column still closes" % key); self.assertEqual(u["stored"], {"v": 2, "cols": []})

    def test_orphaned_state_is_handed_to_the_column_that_shows_the_session_when_its_page_can_hear_it(self):
        # a v1 column's blob held drafts for many sessions and the migration keeps one (review find 2026-09-11): the page
        # offers the rest, each is taken from it and handed to its owner's page — the first column, or a later one
        o = self.out["orphan"]
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}, {"n": 3, "ids": [TESTS]}]})
        self.assertEqual(o["taken"], [["f-chat-2", API, True], ["f-chat-2", TESTS, True]], "taken from the offering page for the sids it does not show; its own member and junk are skipped")
        self.assertEqual(o["posted"], [{"id": "f-chat", "m": {"romp": "adopt", "sid": API, "state": {"draft": "b", "citations": [], "files": [], "staged": []}}},
                                       {"id": "f-chat-3", "m": {"romp": "adopt", "sid": TESTS, "state": {"draft": "t", "citations": [], "files": [], "staged": []}}}],
                         "each lands in the column that shows the session")
        self.assertEqual(o["left"], [WEB], "the page keeps what it shows")
        u = o["unloaded"]
        self.assertEqual(u["taken"], [], "a target whose page has not evaluated cannot hear an adopt: nothing is taken, the offer repeats on the next render")
        self.assertEqual(u["posted"], []); self.assertEqual(u["left"], [API])
        self.assertEqual(o["unknown"], 0, "an offer from no chat column changes nothing")

    def test_a_closing_column_hands_its_width_to_the_column_on_its_left_before_its_key_goes(self):
        # the halving's twin (review find 2026-09-11: with only the key deleted, the freed pixels went to every pane by
        # weight, and a tab dragged out and back narrowed the chat by a third each round trip)
        s = self.out["shrink"]
        self.assertEqual(s["third"], [["chat-pane-2", "chat-pane-3"]], "column 3's pixels go to column 2, its left neighbour")
        self.assertEqual(s["unregister"], ["chat-pane-3"])
        self.assertEqual(s["seq"], ["shrink:chat-pane-3", "unregister:chat-pane-3"], "measured while the pane is still registered and in the row, then the key goes")
        self.assertEqual(s["second"], [["chat-pane", "chat-pane-2"]], "the first later column's pixels go to the first column")

    def test_the_phone_never_splits_and_filters_nothing(self):
        m = self.out["mobile"]
        self.assertEqual(m["ids"], ["f-chat"])
        self.assertIsNone(m["sets"], "null sets: the one chat shows everything")
        self.assertIsNone(m["moved"]); self.assertIsNone(m["movedBelow"])
        self.assertEqual(m["notify"], [["warn", "The phone shows one pane at a time — no split here."]] * 2, "a move to a new column, right or below, is refused with the phone's line")
        self.assertFalse(m["canSplit"])
        self.assertEqual(m["order"], ["chat-pane"]); self.assertEqual(m["order2"], [], "the phone never opens a bottom row")
        self.assertEqual(m["outer"], ["chat-area", "gv-a", "fleet-pane", "gv-b", "feed-pane"])
        self.assertEqual(m["area"]["cls"], "", "…nor shows one restored from the store")
        self.assertEqual(m["stored"], m["storedWas"], "the desktop's arrangement stays in the store")
        self.assertEqual(m["saves"], 0)
        self.assertEqual(m["target"], "f-chat")


# ── THE DRAG, RUN: the zones a tab drag mounts, the rectangle, the cue, the drops ─────────────────────────────────
DRAG_DRIVER = r"""
const out = {};
const WEB = '11111111-2222-3333-4444-555555555501', API = '11111111-2222-3333-4444-555555555502', TESTS = '11111111-2222-3333-4444-555555555503', X = '11111111-2222-3333-4444-555555555509';
const EV = () => { const ev = { prevented: false, dataTransfer: {}, relatedTarget: null, preventDefault() { ev.prevented = true; }, stopPropagation() {} }; return ev; };
const isZone = (c) => c.className.split(' ').includes('col-drop');
const isEdge = (c) => c.className.split(' ').includes('col-drop-edge');
const paneIds = () => ['chat-pane'].concat(window.__rompChatFrameIds().filter((f) => f !== 'f-chat').map(window.__rompChatPaneOf));
const isBottom = (c) => c.className.split(' ').includes('col-drop-bottom');
function zonesOf(pid) { const p = BYID[pid]; return p ? p.children.filter(isZone).map((z) => ({ cls: z.className, col: z.getAttribute('data-col'), row: z.getAttribute('data-row'), refused: z.getAttribute('data-refused'), width: z.style.width || '', height: z.style.height || '', top: z.style.top || '' })) : null; }
function zoneIn(pid, edge) { return (BYID[pid] ? BYID[pid].children : []).find((c) => isZone(c) && isEdge(c) === !!edge) || null; }
function bottomZone() { return BYID['chat-area'].children.find(isBottom) || null; }   // the bottom zone rides the chat area, not a pane
function allZones() { const o = {}; paneIds().forEach((pid) => { o[pid] = zonesOf(pid); }); o['chat-area'] = zonesOf('chat-area'); return o; }
function ghost() { const g = BYID['col-ghost']; return { cls: g.className, text: g.textContent, top: g.style.top || '', height: g.style.height || '', left: g.style.left || '', width: g.style.width || '' }; }
// the panes side by side, w px each behind 7 px gutters, in a row 800 px tall starting 30 px down
function rects(w) { w = w || 400; let x = 0; paneIds().forEach((pid) => { const p = BYID[pid]; if (p) { p._rect = { left: x, top: 30, width: w, height: 800 }; x += w + 7; } }); }
function on(sid, name, fromFid, stripH) { msg({ romp: 'tabDrag', on: true, sid, name, stripH: stripH === undefined ? 38 : stripH }, fromFid); }
function off() { msg({ romp: 'tabDrag', on: false }); }
function fire(z, kind, extra) { const ev = Object.assign(EV(), extra || {}); z.fire(kind, ev); return ev; }
// A) from the FIRST column with columns 2 and 3 open: a column zone on panes 2 and 3, the edge on pane 3 from its top, none on pane 1
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new'); rects();
on(WEB, 'web', 'f-chat');
out.fromFirst = { zones: allZones(), ghost: ghost() };
// the cue: .over on the column zone under the pointer alone; a leave whose relatedTarget is inside the zone is not a leave; a leave clears
const z2 = zoneIn('chat-pane-2', false), z3 = zoneIn('chat-pane-3', false), e3 = zoneIn('chat-pane-3', true);
const enter2 = fire(z2, 'dragenter');
out.overOne = { prevented: enter2.prevented, z2: z2.className, z3: z3.className, ghost: ghost() };
const over2 = fire(z2, 'dragover'); out.overOne.dropEffect = over2.dataTransfer.dropEffect; out.overOne.overPrevented = over2.prevented;
fire(z2, 'dragleave', { relatedTarget: z2 }); out.overOne.stillOver = z2.className;
fire(z2, 'dragleave'); out.overOne.afterLeave = z2.className;
// the edge: the rectangle at the right half of the rightmost pane, the row's height, the name as its line; the leave hides it
fire(e3, 'dragenter'); out.edgeCue = { ghost: ghost(), z3: z3.className, paneRect: BYID['chat-pane-3']._rect };
fire(e3, 'dragleave'); out.edgeCue.afterLeave = ghost();
// the drop on the edge: a new column holding the dragged session, everything unmounted; the page's dragend after it has nothing left to do
CALLS.notify = [];
fire(e3, 'dragenter'); const dropEv = fire(e3, 'drop');
out.dropEdge = { prevented: dropEv.prevented, ids: ids(), stored: cols(), zones: allZones(), ghost: ghost(), notify: CALLS.notify.slice(), blob4: blob(4), targetWeb: tgt(WEB) };
off(); out.dropEdge.afterOff = { zones: allZones(), ghost: ghost() };
// B) from the RIGHTMOST column (3, holding two): the edge sits on pane 3 under its strip and pane 3 gets no column zone; off unmounts; a second on re-mounts cleanly
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new'); window.__rompMoveTab(X, 3); rects();
on(TESTS, 'tests', 'f-chat-3', 44);
out.fromLast = { zones: allZones() };
off(); out.fromLast.afterOff = { zones: allZones(), ghost: ghost() };
on(TESTS, 'tests', 'f-chat-3', 44); on(TESTS, 'tests', 'f-chat-3', 44);
out.fromLast.remounted = allZones(); off();
// C) from a later column holding ONLY the dragged session: no edge zone anywhere (a new column would twin the origin); a drop on pane 3's zone moves it there and the emptied origin closes
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new'); rects();
on(API, 'api', 'f-chat-2');
out.alone = { zones: allZones() };
fire(zoneIn('chat-pane-3', false), 'drop');
out.alone.dropped = { ids: ids(), stored: cols(), zones: allZones() };
// D) a drop on the FIRST column's zone: home (the first column derives); the origin keeps its other member
boot({}, false);
window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 2); rects();
on(API, 'api', 'f-chat-2');
fire(zoneIn('chat-pane', false), 'dragenter'); out.home = { over: zoneIn('chat-pane', false).className, zones: allZones() };
fire(zoneIn('chat-pane', false), 'drop');
out.home.dropped = { ids: ids(), stored: cols(), zones: allZones(), targetApi: tgt(API) };
// E) from the first column onto column 2's zone (pane 2 is also the rightmost: it carries both zones, the edge from its top)
boot({}, false);
window.__rompMoveTab(API, 'new'); rects();
on(WEB, 'web', 'f-chat');
out.into2 = { zones: allZones() };
fire(zoneIn('chat-pane-2', false), 'drop');
out.into2.dropped = { ids: ids(), stored: cols(), zones: allZones() };
// F) at the CAP: the edge is mounted refused; the rectangle wears .refused and says so; a drop notifies and changes nothing
boot({}, false);
window.__rompMoveTab(WEB, 'new'); window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'new'); rects();
on(X, 'x', 'f-chat');
const e4 = zoneIn('chat-pane-4', true);
out.cap = { zones: allZones(), refused: e4 && e4.getAttribute('data-refused') };
fire(e4, 'dragenter'); out.cap.ghost = ghost();
CALLS.notify = []; CALLS.sets = [];
fire(e4, 'drop');
out.cap.dropped = { ids: ids(), stored: cols(), notify: CALLS.notify.slice(), saves: saves(), zones: allZones(), ghost: ghost() };
// G) the geometry: the edge a fifth of the pane, 72 to 180 px; the rectangle the pane's right half at the row's height
boot({}, false); window.__rompMoveTab(API, 'new');
out.geometry = {};
[200, 400, 1000].forEach((w) => { rects(w); on(WEB, 'web', 'f-chat'); const e = zoneIn('chat-pane-2', true); fire(e, 'dragenter');
  out.geometry[w] = { edgeWidth: e.style.width, edgeTop: e.style.top, ghost: ghost(), paneLeft: BYID['chat-pane-2']._rect.left }; off(); });
// H) one column: the first is the source AND the rightmost — the edge alone, under its strip; a drop opens column 2
boot({}, false); rects();
on(WEB, 'web', 'f-chat', 38);
out.single = { zones: allZones() };
fire(zoneIn('chat-pane', true), 'drop');
out.single.dropped = { ids: ids(), stored: cols() };
// I) the phone mounts nothing; a message from no chat column, or with no sid, mounts nothing
boot({}, true);
on(WEB, 'web', 'f-chat');
out.phone = { zones: zonesOf('chat-pane'), bottom: zonesOf('chat-area'), body: Array.from(BODY_CLASSES).sort() };
boot({}, false); rects();
msg({ romp: 'tabDrag', on: true, sid: WEB, name: 'web', stripH: 38 });
out.unknown = { zones: zonesOf('chat-pane') };
on('', 'web', 'f-chat'); out.unknown.noSid = zonesOf('chat-pane');
console.log(JSON.stringify(out));
"""


class DragZonesExecute(unittest.TestCase):
    """The drag (the user 2026-09-11, who asked for a tab dragged to the right edge to make a column and onto another
    column to move it): the real _LANDING_SPLIT_JS runs against the DOM stub and the zones are driven with synthetic
    drag events, which the shell accepts because it reads nothing from dataTransfer — the dragged sid rides the page's
    tabDrag message. Synthetic only."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        script = HARNESS.replace("__SPLIT_JS__", json.dumps(km._LANDING_SPLIT_JS)) + DRAG_DRIVER
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the drag's JS threw: " + r.stderr[:1200]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    @staticmethod
    def _col(col, **kw):
        return dict({"cls": "col-drop", "col": col, "row": None, "refused": None, "width": "", "height": "", "top": ""}, **kw)

    @staticmethod
    def _edge(top, width="80px", refused=None, row="1"):
        return {"cls": "col-drop col-drop-edge", "col": None, "row": row, "refused": refused, "width": width, "height": "", "top": top}

    @staticmethod
    def _bottom(height="160px", refused=None):   # a fifth of the last row's 800 px, on the chat area (the chat rows, 2026-09-15)
        return {"cls": "col-drop col-drop-bottom", "col": None, "row": None, "refused": refused, "width": "", "height": height, "top": ""}

    def test_a_drag_from_the_first_column_mounts_a_column_zone_on_every_other_pane_and_the_edge_on_the_rightmost(self):
        o = self.out["fromFirst"]
        self.assertEqual(o["zones"], {"chat-pane": [], "chat-pane-2": [self._col("2")], "chat-pane-3": [self._col("3"), self._edge("0px")], "chat-area": [self._bottom()]},
                         "no zone on the source; a whole-pane zone on the others; the edge from the rightmost pane's top, a fifth of 400 px; the bottom zone along the area's bottom edge (the rows)")
        self.assertEqual(o["ghost"], {"cls": "", "text": "", "top": "", "height": "", "left": "", "width": ""}, "the rectangle waits for the edge")

    def test_the_cue_marks_the_zone_under_the_pointer_alone_and_a_leave_clears_it(self):
        o = self.out["overOne"]
        self.assertTrue(o["prevented"], "dragenter is accepted (preventDefault) so the drop can land")
        self.assertEqual(o["z2"], "col-drop over", "the zone under the pointer wears .over…")
        self.assertEqual(o["z3"], "col-drop", "…alone")
        self.assertEqual(o["ghost"]["cls"], "", "a column zone never shows the rectangle")
        self.assertEqual(o["dropEffect"], "move"); self.assertTrue(o["overPrevented"])
        self.assertEqual(o["stillOver"], "col-drop over", "a crossing inside the zone's own subtree is not a leave")
        self.assertEqual(o["afterLeave"], "col-drop", "the leave clears the cue")

    def test_the_edge_shows_the_rectangle_at_the_right_half_of_the_rightmost_pane_with_the_name_as_its_line(self):
        o = self.out["edgeCue"]
        r = o["paneRect"]
        self.assertEqual(o["ghost"], {"cls": "on", "text": "web", "top": "30px", "height": "800px",
                                      "left": "%dpx" % (r["left"] + r["width"] / 2), "width": "%dpx" % (r["width"] / 2)},
                         "top and height from the row, left and width the pane's right half — what the drop produces; the session's name, no verb")
        self.assertEqual(o["z3"], "col-drop", "the column zone under the edge takes no cue of its own")
        self.assertEqual(o["afterLeave"]["cls"], "", "the leave hides the rectangle")

    def test_a_drop_on_the_edge_opens_a_new_column_on_the_session_and_unmounts_everything(self):
        o = self.out["dropEdge"]
        self.assertTrue(o["prevented"], "the drop is taken (no navigation)")
        self.assertEqual(o["ids"], ["f-chat", "f-chat-2", "f-chat-3", "f-chat-4"], "__rompMoveTab(sid, 'new'): the lowest free number")
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}, {"n": 3, "ids": [TESTS]}, {"n": 4, "ids": [WEB]}]})
        self.assertEqual(o["blob4"], {"activeId": WEB}, "the new column opens on the dragged session")
        self.assertEqual(o["targetWeb"], "f-chat-4")
        self.assertEqual(o["notify"], [])
        self.assertEqual(o["zones"], {"chat-pane": [], "chat-pane-2": [], "chat-pane-3": [], "chat-pane-4": [], "chat-area": []}, "every zone unmounted at the drop")
        self.assertEqual(o["ghost"]["cls"], "", "the rectangle hidden at the drop"); self.assertEqual(o["ghost"]["text"], "")
        self.assertEqual(o["afterOff"]["zones"], o["zones"], "the page's dragend after the drop finds nothing left to unmount")
        self.assertEqual(o["afterOff"]["ghost"]["cls"], "")

    def test_a_drag_from_the_rightmost_column_puts_the_edge_under_its_strip_and_no_column_zone_on_it(self):
        o = self.out["fromLast"]
        self.assertEqual(o["zones"], {"chat-pane": [self._col("")], "chat-pane-2": [self._col("2")], "chat-pane-3": [self._edge("44px")], "chat-area": [self._bottom()]},
                         "the first column's zone carries data-col=''; the source pane has the edge alone, from the strip's bottom (its strip stays reorder territory)")
        self.assertEqual(o["afterOff"]["zones"], {"chat-pane": [], "chat-pane-2": [], "chat-pane-3": [], "chat-area": []}, "tabDrag off unmounts everything")
        self.assertEqual(o["afterOff"]["ghost"]["cls"], "")
        self.assertEqual(o["remounted"], o["zones"], "a second on (twice, even) re-mounts cleanly: never doubled")

    def test_a_later_column_holding_only_the_dragged_session_gets_no_edge_and_its_drop_on_another_column_closes_it(self):
        o = self.out["alone"]
        self.assertEqual(o["zones"], {"chat-pane": [self._col("")], "chat-pane-2": [], "chat-pane-3": [self._col("3")], "chat-area": [self._bottom()]},
                         "no edge in its row: a new column there would twin the origin and the origin would close; the bottom zone stands, since a row below is a move (RowsExecute)")
        d = o["dropped"]
        self.assertEqual(d["ids"], ["f-chat", "f-chat-3"], "__rompMoveTab(sid, 3): the emptied origin closed")
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS, API]}]})
        self.assertEqual(d["zones"], {"chat-pane": [], "chat-pane-3": [], "chat-area": []})

    def test_a_drop_on_the_first_column_s_zone_brings_the_session_home(self):
        o = self.out["home"]
        self.assertEqual(o["over"], "col-drop over")
        d = o["dropped"]
        self.assertEqual(d["ids"], ["f-chat", "f-chat-2"], "__rompMoveTab(sid, 1): the origin keeps its other member")
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [TESTS]}]})
        self.assertEqual(d["targetApi"], "f-chat"); self.assertEqual(d["zones"], {"chat-pane": [], "chat-pane-2": [], "chat-area": []})

    def test_the_rightmost_pane_that_is_not_the_source_carries_both_zones_and_a_drop_on_its_column_zone_moves_there(self):
        o = self.out["into2"]
        self.assertEqual(o["zones"], {"chat-pane": [], "chat-pane-2": [self._col("2"), self._edge("0px")], "chat-area": [self._bottom()]}, "the edge above the column zone (z 9 over z 8), from the top")
        self.assertEqual(o["dropped"]["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API, WEB]}]}, "__rompMoveTab(sid, 2)")
        self.assertEqual(o["dropped"]["ids"], ["f-chat", "f-chat-2"])

    def test_at_the_cap_the_edge_is_refused_the_rectangle_says_so_and_a_drop_changes_nothing(self):
        o = self.out["cap"]
        self.assertEqual(o["refused"], "1", "mounted with data-refused")
        self.assertEqual(o["zones"]["chat-pane-4"], [self._col("4"), self._edge("0px", refused="1")])
        self.assertEqual(o["zones"]["chat-area"], [self._bottom(refused="1")], "the bottom zone is refused at the cap too: a row below is a fifth column")
        self.assertEqual(sorted(o["ghost"]["cls"].split()), ["on", "refused"]); self.assertEqual(o["ghost"]["text"], "Four columns at most")
        d = o["dropped"]
        self.assertEqual(d["ids"], ["f-chat", "f-chat-2", "f-chat-3", "f-chat-4"], "nothing opened")
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}, {"n": 3, "ids": [API]}, {"n": 4, "ids": [TESTS]}]}, "nothing moved")
        self.assertEqual(d["notify"], [["warn", "Four chat columns at most — close one to open another."]], "the existing refusal, said once")
        self.assertEqual(d["saves"], 0)
        self.assertEqual(d["zones"], {"chat-pane": [], "chat-pane-2": [], "chat-pane-3": [], "chat-pane-4": [], "chat-area": []}); self.assertEqual(d["ghost"]["cls"], "")

    def test_the_geometry_is_a_fifth_of_the_pane_clamped_and_the_pane_s_right_half(self):
        g = self.out["geometry"]
        self.assertEqual(g["200"]["edgeWidth"], "72px", "the floor: 0.2 × 200 = 40 → 72")
        self.assertEqual(g["400"]["edgeWidth"], "80px", "a fifth")
        self.assertEqual(g["1000"]["edgeWidth"], "180px", "the ceiling: 0.2 × 1000 = 200 → 180")
        for w in ("200", "400", "1000"):
            self.assertEqual(g[w]["edgeTop"], "0px", "not the source: from the top")
            half = int(w) / 2
            self.assertEqual(g[w]["ghost"], {"cls": "on", "text": "web", "top": "30px", "height": "800px", "left": "%gpx" % (g[w]["paneLeft"] + half), "width": "%gpx" % half})

    def test_one_column_is_both_source_and_rightmost_so_the_edge_alone_sits_under_its_strip(self):
        o = self.out["single"]
        self.assertEqual(o["zones"], {"chat-pane": [self._edge("38px")], "chat-area": [self._bottom()]}, "stripH from the page's message; the bottom zone beside it")
        self.assertEqual(o["dropped"]["ids"], ["f-chat", "f-chat-2"]); self.assertEqual(o["dropped"]["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}]})

    def test_the_phone_and_a_message_from_no_chat_column_mount_nothing(self):
        self.assertEqual(self.out["phone"], {"zones": [], "bottom": [], "body": ["po-chat", "po-feed", "po-timeline"]}, "no zone, and no body class for the gesture (nothing read one; review find 2026-09-11)")
        self.assertEqual(self.out["unknown"], {"zones": [], "noSid": []})


ROWS_DRIVER = r"""
const out = {};
const WEB = '11111111-2222-3333-4444-555555555501', API = '11111111-2222-3333-4444-555555555502', TESTS = '11111111-2222-3333-4444-555555555503', X = '11111111-2222-3333-4444-555555555509';
const EV = () => { const ev = { prevented: false, dataTransfer: {}, relatedTarget: null, preventDefault() { ev.prevented = true; }, stopPropagation() {} }; return ev; };
const isZone = (c) => c.className.split(' ').includes('col-drop');
const isEdge = (c) => c.className.split(' ').includes('col-drop-edge');
const isBottom = (c) => c.className.split(' ').includes('col-drop-bottom');
function zonesOf(pid) { const p = BYID[pid]; return p ? p.children.filter(isZone).map((z) => ({ cls: z.className, col: z.getAttribute('data-col'), row: z.getAttribute('data-row'), refused: z.getAttribute('data-refused'), width: z.style.width || '', height: z.style.height || '', top: z.style.top || '' })) : null; }
function zoneIn(pid, edge) { return (BYID[pid] ? BYID[pid].children : []).find((c) => isZone(c) && isEdge(c) === !!edge) || null; }
function bottomZone() { return BYID['chat-area'].children.find(isBottom) || null; }
function paneIds() { return ['chat-pane'].concat(window.__rompChatFrameIds().filter((f) => f !== 'f-chat').map(window.__rompChatPaneOf)); }
function allZones() { const o = {}; paneIds().forEach((pid) => { o[pid] = zonesOf(pid); }); o['chat-area'] = zonesOf('chat-area'); return o; }
function ghost() { const g = BYID['col-ghost']; return { cls: g.className, text: g.textContent, top: g.style.top || '', height: g.style.height || '', left: g.style.left || '', width: g.style.width || '' }; }
function rows() { return { top: order(), bottom: order2(), area: areaState(), ids: ids(), stored: cols(), bytes: STORE['romp-chat-cols'] || null }; }
// the geometry the browser would lay out: the area 1400 x 800 from (0, 30); with a bottom row, the top row 400 tall from
// 30, the 7 px gutter, the bottom row 400 tall from 437; each row's panes side by side, w px each behind 7 px gutters
function lay(w) { w = w || 400; const has2 = order2().length > 0; const a = BYID['chat-area'], r1 = BYID['chat-row-1'], r2 = BYID['chat-row-2'];
  a._rect = { left: 0, top: 30, width: 1400, height: 800 };
  r1._rect = has2 ? { left: 0, top: 30, width: 1400, height: 400 } : { left: 0, top: 30, width: 1400, height: 800 };
  r2._rect = has2 ? { left: 0, top: 437, width: 1400, height: 400 } : { left: 0, top: 830, width: 1400, height: 0 };
  [r1, r2].forEach((r) => { let x = 0; r.children.forEach((c) => { if (c.className.indexOf('pane') >= 0) { c._rect = { left: x, top: r._rect.top, width: w, height: r._rect.height }; x += w + 7; } }); }); }
function on(sid, name, fromFid, stripH) { lay(); msg({ romp: 'tabDrag', on: true, sid, name, stripH: stripH === undefined ? 38 : stripH }, fromFid); }
function off() { msg({ romp: 'tabDrag', on: false }); }
function fire(z, kind, extra) { const ev = Object.assign(EV(), extra || {}); z.fire(kind, ev); return ev; }
// A) ONE column, a tab dragged to the BOTTOM zone: the zones (the top row's edge under the source strip, the bottom zone a fifth of the
//    area's height), the rectangle the area's bottom half, and the drop opening the bottom row on the session
boot({}, false);
on(API, 'api', 'f-chat');
out.one = { zones: allZones() };
fire(bottomZone(), 'dragenter'); out.one.ghost = ghost();
fire(bottomZone(), 'dragleave'); out.one.afterLeave = ghost();
fire(bottomZone(), 'dragenter'); const dropEv = fire(bottomZone(), 'drop');
out.one.dropped = Object.assign(rows(), { prevented: dropEv.prevented, zones: allZones(), ghost: ghost(), splitGrow: CALLS.splitGrow.slice(), growFair: CALLS.growFair.slice(), gutter: CALLS.gutter.map((g) => g.gid),
  register: CALLS.register.slice(), rowOf2: window.__rompChatRowOf(2), rowOf1: window.__rompChatRowOf(1), rowOfFirst: window.__rompChatRowOf(''), blob2: blob(2), focused: CALLS.focus.slice(), notify: CALLS.notify.slice(), lastPane: window.__rompLastChatPane(), targetApi: tgt(API) });
off();
// B) a second tab to the BOTTOM ROW's right edge: the zones over two rows (the bottom zone now a fifth of the bottom row's height), the
//    rectangle the right half of the bottom row's rightmost pane at THAT row's height (the bottom zone promises the same, since it
//    appends to the row), and the drop making a column beside the first, halving it
on(TESTS, 'tests', 'f-chat');
out.two = { zones: allZones() };
const e2 = zoneIn('chat-pane-2', true);
fire(e2, 'dragenter'); out.two.ghostEdge = ghost(); fire(e2, 'dragleave');
fire(bottomZone(), 'dragenter'); out.two.ghostBottom = ghost(); fire(bottomZone(), 'dragleave');
CALLS.splitGrow = []; CALLS.gutter = [];
fire(e2, 'dragenter'); fire(e2, 'drop');
out.two.dropped = Object.assign(rows(), { splitGrow: CALLS.splitGrow.slice(), gutter: CALLS.gutter.map((g) => ({ gid: g.gid, left: g.leftPick(), right: g.rightId })) });
off();
// C) a third to the TOP row's right edge: 2 x 2; the walk is the top row then the bottom
on(WEB, 'web', 'f-chat');
out.four = { zones: allZones() };
const e1 = zoneIn('chat-pane', true);
fire(e1, 'dragenter'); out.four.ghost = ghost();
CALLS.splitGrow = [];
fire(e1, 'drop');
out.four.dropped = Object.assign(rows(), { splitGrow: CALLS.splitGrow.slice(), canSplit: window.__rompCanSplit() });
off();
// D) the CAP over two rows: every making zone refused, the rectangle says so on each, a drop notifies once and changes nothing; the
//    palette's two moves are refused too
on(X, 'x', 'f-chat');
out.cap = { zones: allZones() };
fire(bottomZone(), 'dragenter'); out.cap.ghostBottom = ghost(); fire(bottomZone(), 'dragleave');
fire(zoneIn('chat-pane-3', true), 'dragenter'); out.cap.ghostEdge2 = ghost(); fire(zoneIn('chat-pane-3', true), 'dragleave');
CALLS.notify = []; CALLS.sets = [];
fire(bottomZone(), 'dragenter'); fire(bottomZone(), 'drop');
out.cap.dropped = Object.assign(rows(), { notify: CALLS.notify.slice(), saves: saves(), zones: allZones() });
off();
CALLS.notify = [];
out.cap.palette = { below: window.__rompMoveTab(X, 'below'), right: window.__rompMoveTab(X, 'new'), notify: CALLS.notify.slice(), stored: cols() };
// E) the FOLD: the bottom row's members go home one by one. The first column of the bottom row closing tidies the gutter the second
//    carried, hands no width left (nothing on its left) and rings the frame before it in the walk (the top row's last); the last
//    member gone folds the row: the class off, rowSplit gone, the bytes the shape from before the rows
CALLS.splitShrink = []; CALLS.focus = []; CALLS.unregister = [];
window.__rompMoveTab(API, 1);
out.fold = { first: Object.assign(rows(), { splitShrink: CALLS.splitShrink.slice(), focus: CALLS.focus.slice(), unregister: CALLS.unregister.slice() }) };
CALLS.splitShrink = [];
window.__rompMoveTab(TESTS, 1);
out.fold.last = Object.assign(rows(), { splitShrink: CALLS.splitShrink.slice(), sameBytesAs: JSON.stringify({ v: 2, cols: [{ n: 4, ids: [WEB] }] }) });
// F) the PALETTE's move below: the focused column's active tab; then the ALONE rule across rows — a session alone in the bottom row
//    cannot go "below" (a twin) but can go to a new column at the top row's right (the origin closes, the row folds), and one alone in
//    the top row can go below (the origin closes, its width to the pane on its left)
BYID['f-chat']._active = TESTS; CALLS.notify = [];
const fb = window.__rompSplitChatBelow();
out.palette = Object.assign(rows(), { id: fb && fb.id, notify: CALLS.notify.slice(), blob2: blob(2) });
BYID['f-chat']._active = ''; CALLS.notify = []; CALLS.sets = [];
out.alone = { belowAgain: window.__rompMoveTab(TESTS, 'below'), notify: CALLS.notify.slice(), saves: saves() };
CALLS.splitGrow = []; CALLS.splitShrink = []; CALLS.unregister = [];
const fr = window.__rompMoveTab(TESTS, 'new');
out.alone.toTop = Object.assign(rows(), { id: fr && fr.id, splitGrow: CALLS.splitGrow.slice(), splitShrink: CALLS.splitShrink.slice(), unregister: CALLS.unregister.slice() });
CALLS.splitGrow = []; CALLS.splitShrink = []; CALLS.unregister = [];
const fd = window.__rompMoveTab(TESTS, 'below');
out.alone.toBottom = Object.assign(rows(), { id: fd && fd.id, splitGrow: CALLS.splitGrow.slice(), splitShrink: CALLS.splitShrink.slice(), unregister: CALLS.unregister.slice() });
// G) the ZONES under the alone rule: from the column alone in the bottom row, no edge in that row and no bottom zone, the top row's
//    edge stands; from the column alone in the top row, no edge there, the bottom row's edge and the bottom zone stand
on(TESTS, 'tests', 'f-chat-2');
out.aloneZones = { fromBottom: allZones() }; off();
on(WEB, 'web', 'f-chat-4');
out.aloneZones.fromTop = allZones(); off();
// H) 3 + 1 and 1 + 3, by the palette's two moves
boot({}, false);
window.__rompMoveTab(WEB, 'new'); window.__rompMoveTab(API, 'new'); window.__rompMoveTab(TESTS, 'below');
out.threeOne = rows();
boot({}, false); CALLS.splitGrow = [];
window.__rompMoveTab(WEB, 'below'); window.__rompMoveTab(API, 'below'); window.__rompMoveTab(TESTS, 'below');
out.oneThree = Object.assign(rows(), { splitGrow: CALLS.splitGrow.slice(), gutters: CALLS.gutter.map((g) => ({ gid: g.gid, left: g.leftPick() })) });
// I) the RESTORE: a store with rows comes back in its rows at its share; one from before the rows is all top; junk shares fall to the half
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }, { n: 3, ids: [API, TESTS], row: 2 }], rowSplit: 0.7 }) }, false);
out.restored = Object.assign(rows(), { saves: saves(), rowOf3: window.__rompChatRowOf(3), rowOf2: window.__rompChatRowOf(2), gutters: CALLS.gutter.map((g) => g.gid) });
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }, { n: 3, ids: [API] }] }) }, false);
out.restoredFlat = Object.assign(rows(), { saves: saves() });
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB], row: 2 }], rowSplit: 1.5 }) }, false);
out.restoredJunk = { area: areaState(), bottom: order2() };
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB], row: 2 }], rowSplit: 'x' }) }, false);
out.restoredJunk.str = areaState();
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB], row: 3 }] }) }, false);
out.restoredJunk.row3 = rows();
// J) another dashboard tab's write with rows: a column it moved to the other row is remade there (its frame cannot change rows in
//    place), one it added lands in its row, the share follows, nothing is written back; the row folds when its write empties it
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] }) }, false);
CALLS.sets = []; CALLS.unregister = []; CALLS.register = [];
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB], row: 2 }, { n: 4, ids: [API, TESTS] }], rowSplit: 0.4 });
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.reconciled = Object.assign(rows(), { saves: saves(), unregister: CALLS.unregister.slice(), register: CALLS.register.slice(), src2: BYID['f-chat-2'].src });
STORE['romp-chat-cols'] = JSON.stringify({ v: 2, cols: [{ n: 4, ids: [API, TESTS] }] });
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.reconciled.folded = Object.assign(rows(), { saves: saves() });
// K) the ROW GUTTER's release: the share it reports is rounded, applied and persisted while a bottom row exists; with none it is applied
//    (harmless) but never written, since the store carries no rowSplit without a row
boot({}, false);
window.__rompMoveTab(API, 'below');
CALLS.sets = [];
ROWAPPLY(0.62345);
out.gutter = { area: areaState(), stored: cols(), saves: saves() };
window.__rompMoveTab(API, 1);
CALLS.sets = [];
ROWAPPLY(0.3);
out.gutter.noRow = { area: areaState(), stored: cols(), saves: saves(), bytes: STORE['romp-chat-cols'] };
// L) the EMPTINESS message from the bottom row's last column folds the row like the cross does
boot({}, false);
window.__rompMoveTab(API, 'below'); window.__rompMoveTab(TESTS, 'below');
crossOf('f-chat-3').fire('click', { stopPropagation() {} });
out.emptied = { afterCross: rows() };
msg({ romp: 'colEmpty', gone: [API] }, 'f-chat-2');
out.emptied.afterEmpty = rows();
// N) the BUSY guard across rows (review find 2026-09-15): a later column holding ONE listed session over a create in flight
//    refuses that session a new column in the OTHER row — from the palette and from the bottom zone's drop — before anything is
//    taken, grown or written; another dashboard's write of the same store then closes nothing; a column with two listed members
//    lets one go; and the same from the bottom row towards the top
boot({}, false);
window.__rompMoveTab(API, 'new'); BUSY['f-chat-2'] = true;
CALLS.notify = []; CALLS.sets = []; CALLS.taken = []; CALLS.splitGrow = []; CALLS.unregister = [];
out.busyAcross = { below: window.__rompMoveTab(API, 'below'), notify: CALLS.notify.slice(), saves: saves(), taken: CALLS.taken.slice(), splitGrow: CALLS.splitGrow.slice(), stored: cols(), ids: ids(), bottom: order2(), area: areaState() };
on(API, 'api', 'f-chat-2');
out.busyAcross.zones = allZones();
fire(bottomZone(), 'dragenter'); fire(bottomZone(), 'drop'); off();
out.busyAcross.dropped = { notify: CALLS.notify.slice(), saves: saves(), stored: cols(), ids: ids(), bottom: order2(), zones: allZones() };
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });   // another dashboard's write of the same (sanitised) store: the truth already
out.busyAcross.otherTab = { ids: ids(), unregister: CALLS.unregister.slice(), saves: saves(), stored: cols() };
window.__rompMoveTab(TESTS, 2); CALLS.notify = [];
out.busyAcross.twoMembers = { below: (window.__rompMoveTab(TESTS, 'below') || {}).id, notify: CALLS.notify.slice(), stored: cols(), ids: ids() };
boot({}, false);
window.__rompMoveTab(API, 'below'); BUSY['f-chat-2'] = true;
CALLS.notify = []; CALLS.sets = []; CALLS.taken = []; CALLS.splitGrow = []; CALLS.unregister = [];
out.busyAcross.up = { right: window.__rompMoveTab(API, 'new'), notify: CALLS.notify.slice(), saves: saves(), taken: CALLS.taken.slice(), splitGrow: CALLS.splitGrow.slice(), stored: cols(), ids: ids(), bottom: order2(), area: areaState() };
window.dispatchEvent({ type: 'storage', key: 'romp-chat-cols' });
out.busyAcross.up.otherTab = { ids: ids(), unregister: CALLS.unregister.slice(), stored: cols() };
// O) the SHARE is forgotten with the row (review find 2026-09-15): resized, folded, reopened — the rectangle's half is what the
//    drop produces, in memory and in the store
boot({}, false);
window.__rompMoveTab(API, 'below'); ROWAPPLY(0.7);
out.reopen = { resized: { area: areaState(), stored: cols() } };
window.__rompMoveTab(API, 1);
out.reopen.folded = { area: areaState(), stored: cols(), bytes: STORE['romp-chat-cols'] };
on(TESTS, 'tests', 'f-chat');
fire(bottomZone(), 'dragenter'); out.reopen.ghost = ghost(); fire(bottomZone(), 'drop'); off();
out.reopen.reopened = { area: areaState(), stored: cols(), bottom: order2() };
// …and a store carrying a share with NO bottom row (junk) opens the row at the half too
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }], rowSplit: 0.8 }) }, false);
out.reopen.junkShare = { before: areaState() };
window.__rompMoveTab(API, 'below');
out.reopen.junkShare.after = { area: areaState(), stored: cols() };
// P) the pre-rows pane store's upgrade hook is called once at boot, after every column is made and registered (it reads the live
//    roster itself); not on the phone
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }, { n: 3, ids: [API], row: 2 }, { n: 4, ids: [TESTS] }], rowSplit: 0.5 }) }, false);
out.seedArea = { calls: CALLS.seedArea.slice(), registered: CALLS.register.map((r) => r[0]) };
boot({}, false); out.seedArea.fresh = CALLS.seedArea.slice();
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB] }] }) }, true); out.seedArea.phone = CALLS.seedArea.slice();
// M) the phone restores no rows and refuses the move below
boot({ 'romp-chat-cols': JSON.stringify({ v: 2, cols: [{ n: 2, ids: [WEB], row: 2 }], rowSplit: 0.5 }) }, true);
CALLS.notify = [];
out.phone = Object.assign(rows(), { below: window.__rompMoveTab(WEB, 'below'), notify: CALLS.notify.slice(), bytes: STORE['romp-chat-cols'] });
console.log(JSON.stringify(out));
"""


class RowsExecute(unittest.TestCase):
    """THE ROWS (the user 2026-09-15): the real _LANDING_SPLIT_JS against the DOM stub, whose #chat-area holds the two chat
    rows and the gutter between them, driven through the zones, the one mutation and the store. Synthetic only."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        script = HARNESS.replace("__SPLIT_JS__", json.dumps(km._LANDING_SPLIT_JS)) + ROWS_DRIVER
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the rows' JS threw: " + r.stderr[:1500]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    @staticmethod
    def _col(col, **kw):
        return dict({"cls": "col-drop", "col": col, "row": None, "refused": None, "width": "", "height": "", "top": ""}, **kw)

    @staticmethod
    def _edge(top, row, width="80px", refused=None):
        return {"cls": "col-drop col-drop-edge", "col": None, "row": row, "refused": refused, "width": width, "height": "", "top": top}

    @staticmethod
    def _bottom(height, refused=None):
        return {"cls": "col-drop col-drop-bottom", "col": None, "row": None, "refused": refused, "width": "", "height": height, "top": ""}

    def test_a_tab_dropped_on_the_bottom_zone_opens_the_bottom_row_on_it(self):
        o = self.out["one"]
        self.assertEqual(o["zones"], {"chat-pane": [self._edge("38px", "1")], "chat-area": [self._bottom("160px")]},
                         "one column: the top row's edge under the source strip, and the bottom zone a fifth of the area's 800 px")
        self.assertEqual(o["ghost"], {"cls": "on", "text": "api", "top": "433.5px", "height": "396.5px", "left": "0px", "width": "1400px"},
                         "the rectangle is the box the half split opens: the area's bottom half less the 7 px row gutter's share (800 - 7) / 2 from 30 + 800 - 396.5")
        self.assertEqual(o["afterLeave"]["cls"], "")
        d = o["dropped"]
        self.assertTrue(d["prevented"])
        self.assertEqual(d["ids"], ["f-chat", "f-chat-2"])
        self.assertEqual(d["top"], ["chat-pane"]); self.assertEqual(d["bottom"], ["chat-pane-2"], "the bottom row's first column has no gutter ahead of it")
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}], "rowSplit": 0.5}, "row:2 on the entry, the half as the rows' share")
        self.assertEqual(d["area"], {"cls": "rows", "rs1": 50, "rs2": 50}, "the class shows the row and its gutter; the two rows share the height by the split (weights out of 100)")
        self.assertEqual(d["splitGrow"], [], "nothing to halve: the column has the row to itself")
        self.assertEqual(d["growFair"], ["chat2"]); self.assertEqual(d["register"], [["chat-pane-2", "chat2"]])
        self.assertEqual(d["gutter"], [], "no chat|chat gutter wired for it")
        self.assertEqual(d["rowOf2"], 2); self.assertEqual(d["rowOf1"], 1); self.assertEqual(d["rowOfFirst"], 1)
        self.assertEqual(d["blob2"], {"activeId": API}, "opened on the session, like a column at the right")
        self.assertEqual(d["focused"], ["f-chat-2"]); self.assertEqual(d["notify"], [])
        self.assertEqual(d["lastPane"], "chat-pane", "the top row's rightmost pane is still the first: the new column is below, not beside")
        self.assertEqual(d["targetApi"], "f-chat-2")
        self.assertEqual(d["zones"], {"chat-pane": [], "chat-pane-2": [], "chat-area": []}); self.assertEqual(d["ghost"]["cls"], "")

    def test_a_tab_dropped_on_the_bottom_row_s_right_edge_adds_a_column_beside_its_first(self):
        o = self.out["two"]
        self.assertEqual(o["zones"], {"chat-pane": [self._edge("38px", "1")], "chat-pane-2": [self._col("2"), self._edge("0px", "2")], "chat-area": [self._bottom("80px")]},
                         "an edge per row (the bottom row's from its pane's top: not the source); the bottom zone a fifth of the bottom row's 400 px")
        self.assertEqual(o["ghostEdge"], {"cls": "on", "text": "tests", "top": "437px", "height": "400px", "left": "200px", "width": "200px"},
                         "the right half of the bottom row's rightmost pane, at THAT row's top and height")
        self.assertEqual(o["ghostBottom"], o["ghostEdge"], "with the bottom row open the bottom zone appends to it, so it promises the same rectangle")
        d = o["dropped"]
        self.assertEqual(d["bottom"], ["chat-pane-2", "gv-chat-3", "chat-pane-3"]); self.assertEqual(d["top"], ["chat-pane"])
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}, {"n": 3, "ids": [TESTS], "row": 2}], "rowSplit": 0.5})
        self.assertEqual(d["splitGrow"], [["chat-pane-2", "chat3"]], "the bottom row's rightmost column and the new one each take half its width")
        self.assertEqual(d["gutter"], [{"gid": "gv-chat-3", "left": "chat-pane-2", "right": "chat-pane-3"}])
        self.assertEqual(d["ids"], ["f-chat", "f-chat-2", "f-chat-3"])

    def test_a_tab_dropped_on_the_top_row_s_right_edge_completes_two_by_two_and_the_walk_is_top_then_bottom(self):
        o = self.out["four"]
        self.assertEqual(o["zones"]["chat-pane"], [self._edge("38px", "1")])
        self.assertEqual(o["zones"]["chat-pane-3"], [self._col("3"), self._edge("0px", "2")], "the bottom row's edge is on its rightmost pane, now column 3")
        self.assertEqual(o["zones"]["chat-pane-2"], [self._col("2")])
        self.assertEqual(o["ghost"], {"cls": "on", "text": "web", "top": "30px", "height": "400px", "left": "200px", "width": "200px"}, "the top row's rectangle at the top row's height")
        d = o["dropped"]
        self.assertEqual(d["top"], ["chat-pane", "gv-chat-4", "chat-pane-4"]); self.assertEqual(d["bottom"], ["chat-pane-2", "gv-chat-3", "chat-pane-3"])
        self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}, {"n": 3, "ids": [TESTS], "row": 2}, {"n": 4, "ids": [WEB]}], "rowSplit": 0.5},
                         "store order is the order of making; a top-row entry carries no row")
        self.assertEqual(d["splitGrow"], [["chat-pane", "chat4"]], "the TOP row's rightmost pane is halved, not the bottom's")
        self.assertEqual(d["ids"], ["f-chat", "f-chat-4", "f-chat-2", "f-chat-3"], "the walk: the top row left to right, then the bottom row")
        self.assertFalse(d["canSplit"], "four columns: the cap")

    def test_the_cap_refuses_every_making_zone_and_both_palette_moves(self):
        o = self.out["cap"]
        self.assertEqual(o["zones"]["chat-pane-4"], [self._col("4"), self._edge("0px", "1", refused="1")])
        self.assertEqual(o["zones"]["chat-pane-3"], [self._col("3"), self._edge("0px", "2", refused="1")])
        self.assertEqual(o["zones"]["chat-area"], [self._bottom("80px", refused="1")])
        for g in (o["ghostBottom"], o["ghostEdge2"]):
            self.assertEqual(sorted(g["cls"].split()), ["on", "refused"]); self.assertEqual(g["text"], "Four columns at most")
        d = o["dropped"]
        self.assertEqual(d["notify"], [["warn", "Four chat columns at most — close one to open another."]]); self.assertEqual(d["saves"], 0)
        self.assertEqual(d["ids"], ["f-chat", "f-chat-4", "f-chat-2", "f-chat-3"]); self.assertEqual(d["zones"]["chat-area"], [])
        p = o["palette"]
        self.assertIsNone(p["below"]); self.assertIsNone(p["right"])
        self.assertEqual(p["notify"], [["warn", "Four chat columns at most — close one to open another."]] * 2)

    def test_the_bottom_row_folds_when_its_last_column_goes_and_the_store_is_the_shape_from_before_the_rows(self):
        f = self.out["fold"]["first"]
        self.assertEqual(f["bottom"], ["chat-pane-3"], "the second column's gutter went with the first: nothing is on its left now")
        self.assertEqual(f["splitShrink"], [], "the bottom row's first column had no pane on its left to hand its width to")
        self.assertEqual(f["unregister"], ["chat-pane-2"])
        self.assertEqual(f["focus"], ["f-chat-4", "f-chat"], "the ring goes to the frame before it in the walk (the top row's last), then to the move's target")
        self.assertEqual(f["area"]["cls"], "rows", "a member remains: the row stands")
        self.assertEqual(f["stored"], {"v": 2, "cols": [{"n": 3, "ids": [TESTS], "row": 2}, {"n": 4, "ids": [WEB]}], "rowSplit": 0.5})
        l = self.out["fold"]["last"]
        self.assertEqual(l["bottom"], []); self.assertEqual(l["top"], ["chat-pane", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(l["area"], {"cls": "", "rs1": 50, "rs2": 50}, "the class is off: the row and its gutter hide, the top row takes the height")
        self.assertEqual(l["stored"], {"v": 2, "cols": [{"n": 4, "ids": [WEB]}]}, "no row key, no rowSplit")
        self.assertEqual(l["bytes"], l["sameBytesAs"], "BYTE-IDENTICAL to the store a browser that never stacked writes")
        self.assertEqual(l["splitShrink"], [])

    def test_the_palette_moves_below_and_a_session_alone_in_a_row_can_only_go_to_the_other_row(self):
        p = self.out["palette"]
        self.assertEqual(p["id"], "f-chat-2", "the lowest free number, in the bottom row")
        self.assertEqual(p["stored"], {"v": 2, "cols": [{"n": 4, "ids": [WEB]}, {"n": 2, "ids": [TESTS], "row": 2}], "rowSplit": 0.5})
        self.assertEqual(p["bottom"], ["chat-pane-2"]); self.assertEqual(p["notify"], []); self.assertEqual(p["blob2"], {"activeId": TESTS})
        a = self.out["alone"]
        self.assertIsNone(a["belowAgain"]); self.assertEqual(a["notify"], [["warn", "This session is already alone in its column."]], "below again would twin it in its own row")
        self.assertEqual(a["saves"], 0)
        t = a["toTop"]
        self.assertEqual(t["id"], "f-chat-3", "a new column at the top row's right: the lowest free number")
        self.assertEqual(t["top"], ["chat-pane", "gv-chat-4", "chat-pane-4", "gv-chat-3", "chat-pane-3"]); self.assertEqual(t["bottom"], [])
        self.assertEqual(t["stored"], {"v": 2, "cols": [{"n": 4, "ids": [WEB]}, {"n": 3, "ids": [TESTS]}]}, "the emptied origin is gone and with it the row: no rowSplit")
        self.assertEqual(t["area"]["cls"], "")
        self.assertEqual(t["splitGrow"], [["chat-pane-4", "chat3"]], "halved against the top row's rightmost")
        self.assertEqual(t["splitShrink"], [], "the origin was the bottom row's only column: nothing on its left")
        self.assertEqual(t["unregister"], ["chat-pane-2"])
        b = a["toBottom"]
        self.assertEqual(b["id"], "f-chat-2"); self.assertEqual(b["bottom"], ["chat-pane-2"]); self.assertEqual(b["top"], ["chat-pane", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(b["stored"], {"v": 2, "cols": [{"n": 4, "ids": [WEB]}, {"n": 2, "ids": [TESTS], "row": 2}], "rowSplit": 0.5})
        self.assertEqual(b["splitGrow"], [], "the bottom row was empty: nothing to halve")
        self.assertEqual(b["splitShrink"], [["chat-pane-4", "chat-pane-3"]], "the emptied top-row origin hands its width to the pane on its left")
        self.assertEqual(b["unregister"], ["chat-pane-3"])

    def test_the_zones_under_the_alone_rule_stand_only_where_a_drop_moves_the_session_across_rows(self):
        o = self.out["aloneZones"]
        self.assertEqual(o["fromBottom"], {"chat-pane": [self._col("")], "chat-pane-4": [self._col("4"), self._edge("0px", "1")], "chat-pane-2": [], "chat-area": []},
                         "alone in the bottom row: no edge there and no bottom zone (both twins); the top row's edge, on its rightmost pane, is a move")
        self.assertEqual(o["fromTop"], {"chat-pane": [self._col("")], "chat-pane-4": [], "chat-pane-2": [self._col("2"), self._edge("0px", "2")], "chat-area": [self._bottom("80px")]},
                         "alone in the top row: no edge there; the bottom row's edge and the bottom zone move it down")

    def test_three_over_one_and_one_over_three(self):
        t = self.out["threeOne"]
        self.assertEqual(t["top"], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-3", "chat-pane-3"]); self.assertEqual(t["bottom"], ["chat-pane-4"])
        self.assertEqual(t["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB]}, {"n": 3, "ids": [API]}, {"n": 4, "ids": [TESTS], "row": 2}], "rowSplit": 0.5})
        self.assertEqual(t["ids"], ["f-chat", "f-chat-2", "f-chat-3", "f-chat-4"])
        o = self.out["oneThree"]
        self.assertEqual(o["top"], ["chat-pane"]); self.assertEqual(o["bottom"], ["chat-pane-2", "gv-chat-3", "chat-pane-3", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [WEB], "row": 2}, {"n": 3, "ids": [API], "row": 2}, {"n": 4, "ids": [TESTS], "row": 2}], "rowSplit": 0.5})
        self.assertEqual(o["splitGrow"], [["chat-pane-2", "chat3"], ["chat-pane-3", "chat4"]], "each halves the bottom row's rightmost")
        self.assertEqual(o["gutters"], [{"gid": "gv-chat-3", "left": "chat-pane-2"}, {"gid": "gv-chat-4", "left": "chat-pane-3"}])

    def test_the_restore_places_the_rows_and_reads_a_store_from_before_the_rows_as_all_top(self):
        r = self.out["restored"]
        self.assertEqual(r["top"], ["chat-pane", "gv-chat-2", "chat-pane-2"]); self.assertEqual(r["bottom"], ["chat-pane-3"])
        self.assertEqual(r["ids"], ["f-chat", "f-chat-2", "f-chat-3"]); self.assertEqual(r["rowOf3"], 2); self.assertEqual(r["rowOf2"], 1)
        self.assertEqual(r["area"]["cls"], "rows"); self.assertEqual(r["area"]["rs1"], 70); self.assertEqual(r["area"]["rs2"], 30)
        self.assertEqual(r["saves"], 0, "a restore writes nothing"); self.assertEqual(r["gutters"], ["gv-chat-2"])
        f = self.out["restoredFlat"]
        self.assertEqual(f["top"], ["chat-pane", "gv-chat-2", "chat-pane-2", "gv-chat-3", "chat-pane-3"]); self.assertEqual(f["bottom"], [])
        self.assertEqual(f["area"], {"cls": "", "rs1": 50, "rs2": 50}); self.assertEqual(f["saves"], 0)
        j = self.out["restoredJunk"]
        self.assertEqual(j["bottom"], ["chat-pane-2"]); self.assertEqual(j["area"], {"cls": "rows", "rs1": 50, "rs2": 50}, "a share outside (0, 1) falls to the half")
        self.assertEqual(j["str"], {"cls": "rows", "rs1": 50, "rs2": 50}, "…as does a share that is not a number")
        self.assertEqual(j["row3"]["top"], ["chat-pane", "gv-chat-2", "chat-pane-2"], "a row that is not 2 is the top")

    def test_another_dashboard_s_rows_are_reconciled_without_writing_back(self):
        r = self.out["reconciled"]
        self.assertEqual(r["top"], ["chat-pane", "gv-chat-4", "chat-pane-4"]); self.assertEqual(r["bottom"], ["chat-pane-2"])
        self.assertEqual(r["unregister"], ["chat-pane-2"], "the column the other tab moved down closed in the top row…")
        self.assertEqual(r["register"], [["chat-pane-2", "chat2"], ["chat-pane-4", "chat4"]], "…and was remade in the bottom row, then the added column in the top")
        self.assertEqual(r["src2"], "/chat?col=2&skeleton=1")
        self.assertEqual(r["area"]["cls"], "rows"); self.assertEqual(r["area"]["rs1"], 40); self.assertEqual(r["area"]["rs2"], 60)
        self.assertEqual(r["saves"], 0, "the other tab's store is the truth")
        f = r["folded"]
        self.assertEqual(f["bottom"], []); self.assertEqual(f["top"], ["chat-pane", "gv-chat-4", "chat-pane-4"])
        self.assertEqual(f["area"], {"cls": "", "rs1": 50, "rs2": 50}, "its write emptied the row: folded, the share back at the default")
        self.assertEqual(f["saves"], 0)

    def test_the_row_gutter_s_release_persists_a_rounded_share_only_while_a_bottom_row_exists(self):
        g = self.out["gutter"]
        self.assertEqual(g["area"]["rs1"], 62.3); self.assertEqual(g["area"]["rs2"], 37.7, "the share as weights out of 100, one decimal")
        self.assertEqual(g["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}], "rowSplit": 0.623}); self.assertEqual(g["saves"], 1)
        n = g["noRow"]
        self.assertEqual(n["area"], {"cls": "", "rs1": 30, "rs2": 70})
        self.assertEqual(n["stored"], {"v": 2, "cols": []}); self.assertEqual(n["bytes"], json.dumps({"v": 2, "cols": []}, separators=(",", ":")), "no rowSplit without a row")

    def test_the_cross_and_the_emptiness_message_fold_the_bottom_row(self):
        e = self.out["emptied"]
        self.assertEqual(e["afterCross"]["bottom"], ["chat-pane-2"]); self.assertEqual(e["afterCross"]["area"]["cls"], "rows")
        self.assertEqual(e["afterCross"]["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}], "rowSplit": 0.5})
        self.assertEqual(e["afterEmpty"]["bottom"], []); self.assertEqual(e["afterEmpty"]["area"]["cls"], "")
        self.assertEqual(e["afterEmpty"]["stored"], {"v": 2, "cols": []})

    def test_a_lone_member_over_a_create_in_flight_is_refused_the_other_row_before_anything_changes(self):
        b = self.out["busyAcross"]
        self.assertIsNone(b["below"]); self.assertEqual(b["notify"], [["warn", "A session is still being created in this column."]], "the existing notice")
        self.assertEqual(b["saves"], 0, "no store write"); self.assertEqual(b["taken"], [], "no state taken from the source"); self.assertEqual(b["splitGrow"], [], "nothing grown")
        self.assertEqual(b["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}, "the entry stands, whole"); self.assertEqual(b["ids"], ["f-chat", "f-chat-2"]); self.assertEqual(b["bottom"], [])
        self.assertEqual(b["area"]["cls"], "", "no bottom row opened")
        self.assertEqual(b["zones"]["chat-area"], [self._bottom("160px")], "the bottom zone is mounted (the busy answer is the drop's, not the mount's)…")
        d = b["dropped"]
        self.assertEqual(d["notify"], [["warn", "A session is still being created in this column."]] * 2, "…and the drop is refused with the same notice")
        self.assertEqual(d["saves"], 0); self.assertEqual(d["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]}); self.assertEqual(d["ids"], ["f-chat", "f-chat-2"]); self.assertEqual(d["bottom"], [])
        self.assertEqual(d["zones"], {"chat-pane": [], "chat-pane-2": [], "chat-area": []}, "the zones go with the drop")
        o = b["otherTab"]
        self.assertEqual(o["unregister"], [], "another dashboard's write of the same store closes nothing: no empty entry was ever persisted")
        self.assertEqual(o["ids"], ["f-chat", "f-chat-2"]); self.assertEqual(o["saves"], 0); self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}]})
        t = b["twoMembers"]
        self.assertEqual(t["below"], "f-chat-3", "a column with two listed members lets one go below over the create")
        self.assertEqual(t["notify"], []); self.assertEqual(t["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API]}, {"n": 3, "ids": [TESTS], "row": 2}], "rowSplit": 0.5})
        u = b["up"]
        self.assertIsNone(u["right"]); self.assertEqual(u["notify"], [["warn", "A session is still being created in this column."]], "the same guard from the bottom row towards the top")
        self.assertEqual(u["saves"], 0); self.assertEqual(u["taken"], []); self.assertEqual(u["splitGrow"], [])
        self.assertEqual(u["stored"], {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}], "rowSplit": 0.5}); self.assertEqual(u["ids"], ["f-chat", "f-chat-2"]); self.assertEqual(u["bottom"], ["chat-pane-2"])
        self.assertEqual(u["area"]["cls"], "rows", "the bottom row stands")
        self.assertEqual(u["otherTab"], {"ids": ["f-chat", "f-chat-2"], "unregister": [], "stored": {"v": 2, "cols": [{"n": 2, "ids": [API], "row": 2}], "rowSplit": 0.5}})

    def test_the_share_is_forgotten_with_the_row_so_the_rectangle_s_half_is_what_a_reopen_produces(self):
        r = self.out["reopen"]
        self.assertEqual(r["resized"]["area"]["rs1"], 70); self.assertEqual(r["resized"]["stored"]["rowSplit"], 0.7)
        f = r["folded"]
        self.assertEqual(f["area"], {"cls": "", "rs1": 50, "rs2": 50}, "the fold forgets the share in memory too, not only in the store")
        self.assertEqual(f["stored"], {"v": 2, "cols": []}); self.assertEqual(f["bytes"], json.dumps({"v": 2, "cols": []}, separators=(",", ":")))
        self.assertEqual(r["ghost"], {"cls": "on", "text": "tests", "top": "433.5px", "height": "396.5px", "left": "0px", "width": "1400px"}, "the rectangle: the box the half split opens")
        o = r["reopened"]
        self.assertEqual(o["area"], {"cls": "rows", "rs1": 50, "rs2": 50}, "…and the row opens at the half, not the 0.7 of the folded row")
        self.assertEqual(o["stored"], {"v": 2, "cols": [{"n": 2, "ids": [TESTS], "row": 2}], "rowSplit": 0.5}); self.assertEqual(o["bottom"], ["chat-pane-2"])
        j = r["junkShare"]
        self.assertEqual(j["before"]["cls"], ""); self.assertEqual(j["after"]["area"], {"cls": "rows", "rs1": 50, "rs2": 50}, "a stray share in a store with no bottom row does not shape the row it opens")
        self.assertEqual(j["after"]["stored"]["rowSplit"], 0.5)

    def test_the_pre_rows_pane_store_s_upgrade_hook_is_called_once_after_the_columns_are_made(self):
        s = self.out["seedArea"]
        self.assertEqual(s["calls"], [True], "once, at boot")
        self.assertEqual(s["registered"], ["chat-pane-2", "chat-pane-3", "chat-pane-4"], "…after every column is made and registered (the hook reads the live roster)")
        self.assertEqual(s["fresh"], [True], "no columns: called all the same (the hook then converts the one pane's weight alone)")
        self.assertEqual(s["phone"], [], "not on the phone: nothing is restored there")

    def test_the_phone_restores_no_rows_and_refuses_the_move_below(self):
        p = self.out["phone"]
        self.assertEqual(p["ids"], ["f-chat"]); self.assertEqual(p["bottom"], []); self.assertEqual(p["area"]["cls"], "")
        self.assertIsNone(p["below"]); self.assertEqual(p["notify"], [["warn", "The phone shows one pane at a time — no split here."]])
        self.assertEqual(json.loads(p["bytes"]), {"v": 2, "cols": [{"n": 2, "ids": [WEB], "row": 2}], "rowSplit": 0.5}, "the desktop's arrangement stays in the store")


if __name__ == "__main__":
    unittest.main()
