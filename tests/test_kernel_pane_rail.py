"""Pane rail in the shell (the user 2026-06-24; rotated to a BOTTOM BAR, the user 2026-07-05).

ONE thin toolbar holding Chat / Timeline / Outline / Feed toggles. It began as a vertical strip on the far
left; it now runs HORIZONTALLY across the bottom of .col, BELOW the timeline band (its last child). Each pane
is an independent binary on/off, in a fixed, user-chosen order (Chat, Timeline, Outline, Feed — the user
2026-07-05, independent of the panes' layout order), and any subset (or none, or all) can be shown at once.
Fleet/Outline is its OWN pane (no longer an overlay swapped inside the chat pane).
Source-level pin against km._landing().
"""
import os
import re
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


class PaneRailTest(unittest.TestCase):
    def setUp(self):
        self.html = km._landing()

    def test_bottom_bar_rail_holds_chat_timeline_fleet_feed_toggles_in_fixed_order(self):
        # one thin toolbar (now the bottom bar); FOUR toggle buttons, in a user-chosen order (the user 2026-07-05)
        self.assertIn("<div class=pane-rail>", self.html)
        self.assertIn("<div class=rail-btn data-pane=chat>Chat</div>", self.html)
        # the by-session view is labelled "Outline" (the user 2026-06-29); the data-pane KEY stays 'fleet' internally
        self.assertIn("<div class=rail-btn data-pane=fleet>Outline</div>", self.html)
        self.assertIn("<div class=rail-btn data-pane=feed>Feed</div>", self.html)
        # the LABEL is Sessions (the user 2026-08-24: the pane outgrew "Timeline" — filter, tags, lane
        # controls); the data-pane key stays 'timeline', the fleet/Outline precedent
        self.assertIn("<div class=rail-btn data-pane=timeline>Sessions</div>", self.html)
        # Chat before Timeline before Outline(fleet) before Feed in the rail (fixed user-chosen order)
        idxs = [self.html.index("data-pane=" + k) for k in ("chat", "timeline", "fleet", "feed")]
        self.assertEqual(idxs, sorted(idxs), "rail order must be Chat, Timeline, Outline, Feed")
        # the old per-pane strips + the show-fleet swap + the timeline minimize bar are gone
        self.assertNotIn("pane-strip", self.html)
        self.assertNotIn("strip-toggle", self.html)
        self.assertNotIn("show-fleet", self.html)
        self.assertNotIn("tl-collapse", self.html)
        self.assertNotIn("cc-tl", self.html)

    def test_four_top_panes_in_fixed_order_then_the_timeline_band(self):
        # the TOP row is chat | gv-a | fleet (the Outline) | gv-b | feed | gv-c | files; the timeline is the bottom band (gh +
        # #tl-pane) AFTER the row closes, so the DOM order is row panes first, then the gh gutter, then #tl-pane.
        # …and the chat panes sit inside the static #chat-area wrapper (the chat rows, 2026-09-15): the top row around the
        # first pane, then the row gutter, then the empty bottom row, all in the markup before gv-a
        order = ["id=chat-area", "id=chat-row-1", "id=chat-pane", "id=gv-rows", "id=chat-row-2", "id=gv-a", "id=fleet-pane", "id=gv-b", "id=feed-pane", "id=gv-c", "id=files-pane", "id=gh", "id=tl-pane"]
        idxs = [self.html.index(tok) for tok in order]
        self.assertEqual(idxs, sorted(idxs), "row panes, then the gh gutter, then the timeline band")
        self.assertNotIn("id=gv-d", self.html)                   # no 5th-pane gutter
        # the pane rail is the BOTTOM BAR (the user 2026-07-05): LAST child of .col, AFTER the timeline band —
        # no longer the first child of .row. So its markup falls after #tl-pane.
        self.assertGreater(self.html.index("class=pane-rail"), self.html.index("id=tl-pane"),
                           "the rail runs across the bottom, below the timeline")
        # each pane/band is shown/hidden independently by its own body.po-* class
        self.assertIn("body:not(.po-chat) #chat-pane{display:none}", self.html)
        self.assertIn("body:not(.po-fleet) #fleet-pane{display:none}", self.html)
        self.assertIn("body:not(.po-feed) #feed-pane{display:none}", self.html)
        self.assertIn("body:not(.po-files) #files-pane{display:none}", self.html)
        self.assertIn("body:not(.po-timeline) #gh,body:not(.po-timeline) #tl-pane{display:none}", self.html)

    def test_default_layout_is_chat_feed_timeline(self):
        # default: Chat + Feed + Timeline on, Fleet off (the user 2026-06-25; inlined on <body> for first paint)
        self.assertIn("<body class='po-chat po-feed po-timeline'>", self.html)

    def test_the_optional_panes_are_served_unloaded_and_the_controller_reads_the_gear(self):
        # The gear's Panes section (the user 2026-09-10): Sessions, the Outline and the Feed can be hidden from
        # this browser's dashboard altogether, per browser (romp:settings.panes). The markup carries data-src
        # for those three, the controller copies it to src for a pane this browser shows and hides the rail
        # button and phone tab of one it does not; the chat (required) and the Files pane (its own rail
        # toggle) keep src. The behaviour runs under node in tests/test_pane_state_broadcast.py OptionalPanes.
        for k in ("fleet", "feed", "timeline"):
            self.assertIn("<iframe id=f-%s data-src=/%s>" % (k, k), self.html)
        self.assertIn("<iframe id=f-chat class=m-on src=/chat>", self.html)
        self.assertIn("<iframe id=f-files src=/files>", self.html)
        self.assertIn(".rail-btn[hidden]{display:none}", self.html, "the controller's hidden must beat .rail-btn's display:flex")
        self.assertIn("var ALL=KEYS.slice(),OPT=['timeline','fleet','feed'],SK='romp:settings';", self.html)
        # reconcile(live): the boot call keeps a shown pane's stored rail flag; the storage listener's call brings a
        # pane the gear just turned on ON SCREEN (the row promises the column back, not its button alone)
        self.assertIn("function reconcile(live){", self.html)
        self.assertIn("reconcile(true);apply();", self.html)
        # the default body class still ships chat+feed+timeline; the controller reconciles before its first apply
        self.assertIn("<body class='po-chat po-feed po-timeline'>", self.html)

    def test_gutters_show_only_between_two_visible_panes(self):
        # gv-a sits chat|fleet → only when BOTH are shown
        self.assertIn("body:not(.po-chat) #gv-a,body:not(.po-fleet) #gv-a{display:none}", self.html)
        # gv-b sits (fleet|chat)|feed → it doubles as the chat|feed gutter when fleet is off, so it hides only
        # when feed is off OR neither chat nor fleet is on (feed would then be the lone pane)
        self.assertIn("body:not(.po-feed) #gv-b,body:not(.po-chat):not(.po-fleet) #gv-b{display:none}", self.html)
        # gv-c sits (feed|fleet|chat)|files: hidden when files is off, or when no column is shown to its left
        self.assertIn("body:not(.po-files) #gv-c,body:not(.po-chat):not(.po-fleet):not(.po-feed) #gv-c{display:none}", self.html)

    def test_lit_rail_button_is_the_romp_accent(self):
        # the shell defines the accent locally (it loads no styles.css) and the ON toggle uses it
        self.assertIn(":root{--accent:#9cd2ff", self.html)
        self.assertIn(".rail-btn.on{color:var(--accent)", self.html)

    def test_rail_drives_a_persisted_pane_controller_exposed_for_the_legacy_toggle(self):
        # the controller toggles po-* from the rail, persists the set, and exposes __rompPaneToggle so the
        # legacy {romp:'toggleFleet'} postMessage routes through the same path
        self.assertIn("var PK='romp-panes',po={chat:true,fleet:false,feed:true,timeline:true,files:false}", self.html)
        self.assertIn("window.__rompPaneToggle=togglePane", self.html)
        self.assertIn("togglePane(b.getAttribute('data-pane'))", self.html)
        self.assertIn("document.body.classList.toggle('po-chat',!!po.chat)", self.html)
        # ?panes=chat,fleet bookmarks an explicit set
        self.assertIn("get('panes')", self.html)

    def test_panes_are_resizable_by_flex_grow_persisted_per_pane(self):
        # each pane grows by a per-pane var the gutters write; the drag normalises visible panes to their px
        # widths first (so it shifts only the pair it sits between) and persists the grows across reloads
        # the chat AREA takes the outer row's --g-chat weight (the chat rows, 2026-09-15); the first pane inside it grows by an
        # inner weight of its own, --g-chat1, against the top row's split columns
        self.assertIn("#chat-area{flex:var(--g-chat,60) 1 0;display:flex;flex-direction:column;position:relative;min-width:0;min-height:0}", self.html)
        self.assertIn("#chat-pane{flex:var(--g-chat1,60) 1 0}#fleet-pane{flex:var(--g-fleet,34) 1 0}#feed-pane{flex:var(--g-feed,40) 1 0}#files-pane{flex:var(--g-files,40) 1 0}", self.html)
        self.assertIn("body:not(.po-chat) #chat-area{display:none}", self.html, "the chat toggle hides the whole area, rows and all")
        self.assertNotIn("--g-timeline", self.html)              # timeline is the fixed-height band, not a row grow
        self.assertIn("var GK='romp-pane-grow'", self.html)
        # two passes (2026-09-08): every shown width is READ before any grow is written — a write re-flows the row,
        # and a read after it came back at a mixed scale, ballooning the first column on a fresh browser's first drag.
        # Over the grabbed pane's SIBLINGS (the chat rows, 2026-09-15): the shown panes sharing its container, so a
        # chat|chat grab inside a row never writes the outer row's weights, nor an outer grab a row's
        self.assertIn("function normalise(ids){var px={};ids.forEach(function(id){px[id]=document.getElementById(id).offsetWidth;});Object.keys(px).forEach(function(id){setGrow(key(id),px[id]);});return px;}", self.html)
        self.assertIn("function sibs(id){var el=document.getElementById(id),par=el?el.parentElement:null;return PANES.filter(function(p){var e=document.getElementById(p);return !!e&&shown(p)&&e.parentElement===par;});}", self.html)
        self.assertIn("if(!vert){var ids=sibs(L.id),px=normalise(ids);ids.forEach(function(id){W+=px[id];if(id!==L.id&&id!==R.id)others.push(key(id));});}", self.html)   # the press: the container to px, its width and the pair's siblings kept for the release (2026-09-15)
        self.assertIn("function endGesture(commit){var g=gesture;if(!g)return;gesture=null;", self.html)   # one exit for every gutter drag
        # the one write of the store (persist): the pre-rows shape — no chat1 — while a pre-rows store's upgrade waits for the chat pane
        # to show; a peer's upgrade is ingested at its storage event and first in every entry point, never here
        self.assertIn("function persist(){if(legacy){var cur=null;try{cur=JSON.parse(localStorage.getItem(GK)||'null');}catch(e){}", self.html)
        self.assertIn("var o=Object.assign({},grow);delete o.chat1;try{localStorage.setItem(GK,JSON.stringify(o));}catch(e){}return;}", self.html)   # …and refuses, saying so, rather than write that shape over a store a peer has upgraded (2026-09-15)
        self.assertIn("window.addEventListener('storage',function(e){if(e&&e.key===GK)ingest();});", self.html)   # the store as it stands is what is adopted, never the event's own value (2026-09-15, eighth pass)
        # gv-b picks its left neighbour live: the outline (fleet) when shown, else the chat AREA (so it's the chat|feed
        # gutter too; lastChat() is #chat-area, the wrapper every chat column lives in — the chat rows, 2026-09-15)
        self.assertIn("document.body.classList.contains('po-fleet')?'fleet-pane':lastChat()", self.html)
        self.assertIn("gutter('gv-a',function(){return lastChat();},'fleet-pane')", self.html)

    def test_the_shell_serves_the_landing_line_of_a_divider_drag(self):
        # a divider drag moves a line over the row and the panes take their widths once, at release (the drag
        # itself runs in tests/test_pane_gutter_drag.py); the served shell carries the line's element and its
        # rule: hidden until a drag shows it, fixed so its left is a viewport coordinate, the gutter's width,
        # never a hit target (so the gutter under it keeps its :hover at the grab), and above the focus ring
        # (.pane-focused::after is z-index 6) so a focused pane does not cover it
        self.assertIn("<div id=gv-ghost></div>", self.html)
        self.assertIn("#gv-ghost{display:none;position:fixed;width:7px;pointer-events:none;z-index:40;", self.html)
        # a child of .col right after the row closes (the files pane's close, then the row's) and before the
        # timeline's gutter: fixed, so a flex item of neither
        self.assertIn("<iframe id=f-files src=/files></iframe></div></div><div id=gv-ghost></div>", self.html)
        self.assertLess(self.html.index("<div id=gv-ghost></div>"), self.html.index("<div class=gh id=gh></div>"))

    def test_timeline_is_the_rail_toggled_bottom_band(self):
        # the timeline is a full-width BAND below the pane row (the user 2026-06-25), toggled by the rail's
        # Timeline button (po-timeline) — NOT a 4th vertical pane and NOT the old always-on band with a minimize
        # button. .col is a flex column: the .row of panes, then the gh gutter, then the band.
        # Pinned as the flex-column STRUCTURE this test is about, not as the whole declaration list: it
        # broke on an unrelated right-edge padding (the user 2026-07-23) that says nothing about where the
        # band sits. test_kernel_mobile owns that strip.
        self.assertIn(".col{display:flex;flex-direction:column;height:100%", self.html)
        self.assertIn("#tl-pane{flex:0 0 var(--tl,200px)}", self.html)          # a fixed-height bottom band
        self.assertIn("body:not(.po-timeline) #gh,body:not(.po-timeline) #tl-pane{display:none}", self.html)
        self.assertIn("<div class=gh id=gh></div>", self.html)                  # the row-resize gutter is back
        self.assertNotIn("tl-collapse", self.html)               # no minimize button (the rail toggle drives it)
        self.assertNotIn("cc-tl", self.html)
        # the band auto-fits its content and re-fits when the toggle turns it on
        self.assertIn("col.style.setProperty('--tl'", self.html)
        self.assertIn("window.addEventListener('romp-panes',autosize)", self.html)

    def test_rail_actions_are_refresh_then_network_then_gear(self):
        # the bottom rail-acts group: ↻ refresh, the network (remote-kernels) icon, then the ⛭ settings gear.
        # The standalone ? help button is gone — shortcuts moved INTO settings (the user 2026-06-30).
        self.assertIn("id=rail-refresh", self.html)
        self.assertIn("id=rail-net", self.html)
        self.assertIn("id=rail-gear", self.html)
        self.assertNotIn("id=rail-help", self.html)
        idxs = [self.html.index("id=" + k) for k in ("rail-refresh", "rail-net", "rail-gear")]
        self.assertEqual(idxs, sorted(idxs), "rail actions order: refresh, network, gear")
        # the gear is the bigger ⛭ (gear-without-hub) the user restored — NOT the thinner ⚙
        self.assertIn("aria-label=Settings>⛭</div>", self.html)
        self.assertNotIn("⚙", self.html)
        # …and it is sized UP from the shared .rail-act 15px (the user 2026-07-28): a text glyph at 15px
        # drew visibly smaller than the 17-18px svg icons beside it, so the row looked ragged.
        self.assertIn("#rail-gear{font-size:19px}", self.html)

    def test_the_rail_hover_names_each_host_s_drift_without_opening_the_panel(self):
        # the red node says SOMETHING is out of step; the hover says what, by how much, and for which
        # host (the user 2026-07-29). ONE definition of the wording, worn by the panel row and the
        # tooltip alike — two spellings of the same drift would eventually disagree.
        self.assertIn("function driftWord(t){", self.html)
        self.assertIn("down=bb>0?('behind '+bb):''", self.html)   # said in words since 2026-07-30
        self.assertIn("up=ab>0?('ahead '+ab):''", self.html)
        self.assertIn("var dw=t.outOfDate?(' \\u00b7 '+(t.status==='up'?'':'last known ')+driftWord(t)):(t.restartPending?' \\u00b7 running older code':'')", self.html)
        self.assertIn("+dw+", self.html, "the per-host tooltip line carries it")
        # the panel row reads the same functions rather than re-deriving the words. Since 2026-07-30 it
        # leads with the BUILD (release + commit) and puts the distance in parentheses after it — a bare
        # sha meant nothing without the reader's own sha memorised — so it takes the counts directly.
        self.assertIn("if(t.outOfDate){var w=driftWord(t),ar=driftCounts(t);", self.html)
        self.assertIn("function buildWord(v,s)", self.html)

    def test_network_icon_lights_accent_when_a_remote_is_connected(self):
        # the remote-kernels icon goes accent-blue (.on) while a tunnel is up, driven by the /tunnels poll
        self.assertIn("id=rail-net", self.html)
        self.assertIn(".rail-act.on{color:var(--accent)}", self.html)
        self.assertIn("icon.classList.toggle('on'", self.html)

    def test_network_icon_marches_while_a_tunnel_is_mid_attach(self):
        # the motion cue (the user 2026-07-12): while any tunnel is authorizing/connecting/starting the
        # glyph turns accent and its connector dashes MARCH — class-driven off the same /tunnels poll
        # (event-based: it clears the moment every tunnel settles), armed optimistically on Attach click
        # so the icon moves the instant the user acts. The mobile Net button carries the same classes.
        self.assertIn("@keyframes rnet-march", self.html)
        self.assertIn(".rail-act.busy svg path,#mtabs .mact.busy svg path{stroke-dasharray:3 3;", self.html)
        self.assertIn("icon.classList.toggle('busy',busy)", self.html)
        # an automatic remote update in flight ALSO marches the icon (the user 2026-07-24): that background
        # push replaced a mid-screen prompt, so the motion is how it announces itself
        self.assertIn("paintIcon(ts.some(function(t){return t.status==='up';}),busy||!!pushing.length,fleetNodes(ts))",
                      self.html)
        self.assertIn("icon.classList.add('busy')", self.html, "Attach click arms the motion before the poll")
        self.assertIn("#mtabs .mact[data-act=net]", self.html, "the mobile Net button mirrors on/busy")

    def test_keyboard_shortcuts_live_in_the_settings_modal(self):
        # folded into settings (the user 2026-06-30): no standalone ? modal in the shell anymore
        self.assertNotIn("id=rhelp-overlay", self.html)
        self.assertNotIn("id=rail-help", self.html)
        # the section is a LINK now (the user 2026-08-09): the configurable shortcuts dialog
        # (ui/webview/shortcuts-modal.ts) is the one home for the whole list — record, conflicts,
        # reset — and the gear row just opens it (VS Code's row points at its own keybindings editor
        # instead). The old static list is gone with its stale-per-surface copies, Enter row first.
        import pathlib
        gear = (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()
        self.assertIn(">Keyboard shortcuts</div>", gear)
        self.assertIn("Customize shortcuts…", gear)
        self.assertIn("'openKeys'", gear)
        self.assertNotIn("Send message", gear, "the Enter row is gone — a typing key nobody looks up")
        self.assertNotIn("<kbd>Enter</kbd>", gear)
        self.assertNotIn("Slash-command menu", gear)
        self.assertNotIn("Question picker", gear)

    def test_rail_and_fleet_pane_are_hidden_on_mobile(self):
        # mobile shows one pane at a time via the bottom tab bar, not the rail; the desktop po-* pane-hiding
        # must NOT leak in (the tab bar governs), so chat/feed/timeline panes are forced back to display:contents
        self.assertIn(".gv,.gh,.pane-rail{display:none}", self.html)
        self.assertIn("#chat-pane,#fleet-pane,#feed-pane,#files-pane,#tl-pane{display:contents!important}", self.html)
        # the Outline (fleet) is a mobile TAB now, no longer desktop-only (the user 2026-07-11)
        self.assertNotIn("#fleet-pane{display:none!important}", self.html)
        self.assertIn("body[data-tab=timeline] .row{display:none}", self.html)   # timeline tab → band fills


class ApiHealthCell(unittest.TestCase):
    """The bottom bar's API health cell: a sibling of #rail-usage, painted from the kernel's apiHealth push by
    _LANDING_APIH_JS (tests/test_api_health_rail.py covers the frame and the detail's content). Source-level
    placement and styling pins against km._landing()."""

    def setUp(self):
        self.html = km._landing()

    def test_the_cell_follows_the_usage_cell_inside_the_scroll_group(self):
        i_usage, i_api, i_acts = (self.html.index(k) for k in ("id=rail-usage", "id=rail-api", "class=rail-acts"))
        self.assertLess(i_usage, i_api, "right of the usage cell")
        self.assertLess(i_api, i_acts, "inside .rail-scroll, before the pinned actions")
        self.assertGreater(i_api, self.html.index("<div class=rail-scroll>"))

    def test_the_cell_ships_hidden_as_a_dot_alone(self):
        # T301 (the user 2026-09-10): no second API word and no "ok"; the dot moves into the spend readout's slot
        # (.ah-slot, right after the readout's own API label) whenever that readout renders
        tag = ('<div id=rail-api class="ru-w ru-ah" hidden role=button tabindex=0 aria-label="API health" data-dot=fine>'
               '<i class=ah-dot></i></div>')
        self.assertTrue(tag in self.html, "the cell's markup: hidden, a keyboard button, a dot and nothing else")
        self.assertNotIn(".ah-text", self.html, "no word beside the dot")
        self.assertIn("<div class=ru-name>API</div><span class=ah-slot></span>", self.html, "the readout's slot for the dot")
        self.assertIn(".ah-slot{display:contents}", self.html)   # no flex item of its own: a hidden dot costs no gap
        tag = re.search(r"<div id=rail-api[^>]*>", self.html).group(0)
        self.assertNotIn("title", tag, "the rail's no-title rule: the detail is the one hover surface")
        self.assertNotIn("data-keycmd", tag, "no palette command yet")

    def test_the_hidden_attribute_beats_the_rail_s_own_display_rule(self):
        # The UA's [hidden]{display:none} loses to ANY author display rule, and .ru-w{display:flex} is one, so
        # without this author rule the cell would show a gray 'API ok' from page load, and forever on a kernel
        # that never sends a frame (the #mtabs button[hidden] idiom in the same stylesheet).
        self.assertTrue(".ru-w{display:flex;" in self.html, "the author display rule the attribute must beat")
        self.assertTrue("#rail-api[hidden]{display:none}" in self.html, "no author [hidden] rule for #rail-api")

    def test_the_cell_adds_no_font_size_and_sits_in_the_readout_s_slot(self):
        self.assertNotIn(".ah-text", self.html, "T301: no word on the rail, so no font rule for one")
        self.assertIn("#rail-api{cursor:pointer;margin:0 1px;padding:4px 2px}", self.html, "a 15 px hit target around a 7 px dot")

    def test_the_dot_wears_the_accent_when_fine_and_the_status_tokens_otherwise(self):
        # T301 (the user 2026-09-10): the fine dot IS the romp accent; errors the blocked red; quiet the label gray;
        # every colour through a token with a fallback for a var-less harness
        self.assertIn(".ah-dot{width:7px;height:7px;border-radius:50%;background:var(--dim,#9aa4ad);opacity:.55;flex:0 0 auto}", self.html)
        self.assertIn("#rail-api[data-dot=fine] .ah-dot,.ah-dot[data-dot=fine]{background:var(--accent,#9cd2ff);opacity:1}", self.html)
        self.assertIn("#rail-api[data-dot=errors] .ah-dot,.ah-dot[data-dot=errors]{background:var(--st-blocked-bg,#e5484d);opacity:1}", self.html)
        self.assertIn("#rail-api[data-dot=quiet] .ah-dot,.ah-dot[data-dot=quiet]{background:var(--dim,#9aa4ad);opacity:.55}", self.html)
        self.assertNotIn("data-state=degraded", self.html, "the machine's words are not colours any more")

    def test_the_light_theme_keeps_the_ok_dot_visible_and_the_state_dots_their_colors(self):
        # the dark label gray at .55 blends into the light rail; the light label color keeps the glyph. Scoped to
        # the ok state: a bare `body.theme-light .ah-dot` (0,2,1) would outrank the detail's `.ah-dot[data-state=…]`
        # rules (0,2,0), and the card's headline dot would lose its amber and red. The History head shows the signal's
        # quiet states (healthy, unknown) with the same glyph, so the rule names them too (the base gray falls to
        # about 1.6:1 on the white tip)
        # T301: the quiet dot alone needs the light override (fine and errors read on white as they are)
        self.assertTrue("body.theme-light #rail-api[data-dot=quiet] .ah-dot,body.theme-light .ah-dot[data-dot=quiet]{background:#5D574E}" in self.html,
                        "the light override names the quiet state, on the rail and in the popup alike")
        self.assertNotIn("body.theme-light .ah-dot{", self.html, "no bare light rule on the dot")
        self.assertNotIn("body.theme-light .ah-dot[data-dot=errors]", self.html, "the state rules are not restated per theme")

    def test_the_shell_socket_carries_the_dashboard_s_wid_minted_before_it_connects(self):
        # the detail's openSession rides the shell socket; with no wid on it the kernel's reveal would fall to the
        # broadcast and every open dashboard's chat would switch (_reveal_chat_for)
        js = km._LANDING_MOBILE_JS
        self.assertIn("var ws=new WebSocket(proto+location.host+'/ws?app=shell&wid='+encodeURIComponent(wid()));", js)
        mint = "sessionStorage.setItem('romp:wid'"
        self.assertLess(self.html.index(mint), self.html.index("'/ws?app=shell&wid='"), "minted before the shell socket connects")

    def test_an_emptied_usage_cell_collapses_its_gap(self):
        # renderRows empties #rail-usage on a login-only machine; as a zero-width flex item it would still pay the
        # scroll group's gap on both sides (28px to the API cell instead of 16px)
        self.assertTrue("#rail-usage:empty{display:none}" in self.html, "the emptied cell must leave the flex flow")

    def test_the_detail_shares_the_usage_tip_s_skin_and_backdrop(self):
        self.assertIn("#ah-tip,#ru-tip{position:fixed", self.html)
        self.assertIn("#ah-tip.ru-modal,#ru-tip.ru-modal{", self.html)
        self.assertIn("body.theme-light #ah-tip,body.theme-light #ru-tip{", self.html)
        self.assertIn("tip.id='ah-tip'", self.html)
        self.assertIn("document.getElementById('ru-back')", km._LANDING_APIH_JS)

    def test_the_shell_socket_routes_the_frame_and_escape_closes_the_detail_first(self):
        self.assertIn("else if(m&&m.type==='apiHealth'&&window.__rompApiHealth)window.__rompApiHealth(m);", self.html)
        self.assertIn("window.__rompShellSend=function(o)", self.html)
        esc = km._LANDING_ESC_JS
        self.assertLess(esc.index("__rompApiClose"), esc.index("__rompUsageClose"), "both ride #ru-back; the detail's hook is checked first")
        self.assertIn("if(ru&&ru.classList.contains('on')&&window.__rompApiClose){window.__rompApiClose();closed=true;}", esc)

    def test_the_shell_socket_routes_a_refused_press_to_the_notification_center(self):
        # the detail's pause button sends setGlobalRetryPaused on this socket, and a press the kernel refused (the
        # pause file could not be read; nothing was changed) is answered with a warn frame on the same socket. A
        # dispatcher without this branch discarded it: the answering frame's moved seq un-acknowledged the button
        # with no reason anywhere, and the press read as ignored. The chat page toasts its own warn frames already.
        self.assertIn("else if(m&&m.type==='warn'&&typeof m.text==='string'&&m.text&&window.__rompNotify)"
                      "window.__rompNotify('warn',m.text);", self.html)
        self.assertIn("window.__rompNotify=function(kind,text,tgt)", self.html, "the center the branch feeds is on this page")

    def test_the_cell_s_script_loads_after_the_usage_script_it_borrows_the_backdrop_from(self):
        self.assertLess(self.html.index("getElementById('rail-usage')"), self.html.index("getElementById('rail-api')"))


class ShellSocketIdentity(unittest.TestCase):
    """A reveal the kernel answers to the shell's own op (the API detail's openSession) reaches the asking
    dashboard alone, since the shell client carries its wid the way a feed pane's does. Synthetic clients; no
    sockets."""

    def _client(self, app, wid):
        return {"app": app, "wid": wid, "alive": True, "send": lambda s, w=wid, a=app: self.sink.append((a, w))}

    def setUp(self):
        self.sink = []
        self._saved = list(km._clients)
        km._clients[:] = [self._client("chat", "win-A"), self._client("chat", "win-B"),
                          self._client("shell", "win-A"), self._client("shell", "win-B"), self._client("feed", "win-A")]

    def tearDown(self):
        km._clients[:] = self._saved

    def test_a_shell_client_with_a_wid_moves_its_own_dashboard_s_chat_only(self):
        km._reveal_chat_for({"app": "shell", "wid": "win-A"}, {"type": "focus", "id": "s1"})
        self.assertEqual(sorted(self.sink), [("chat", "win-A"), ("shell", "win-A")])

    def test_a_shell_client_without_a_wid_still_broadcasts(self):
        # an older shell page, or a browser with no sessionStorage: today's behavior, not silence
        km._reveal_chat_for({"app": "shell", "wid": ""}, {"type": "focus", "id": "s1"})
        self.assertEqual(sorted(w for a, w in self.sink if a == "chat"), ["win-A", "win-B"])

    def test_the_open_session_op_hands_the_asking_client_to_the_router(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        i = src.index('msg.get("type") == "openSession" and msg.get("id")')
        self.assertIn('_open_or_revive(msg["id"], live=bool(msg.get("live")), client=client)', src[i:i + 600])


if __name__ == "__main__":
    unittest.main()
