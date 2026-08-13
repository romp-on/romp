"""Pane rail in the shell (the user 2026-06-24; rotated to a BOTTOM BAR, the user 2026-07-05).

ONE thin toolbar holding Chat / Timeline / Outline / Feed toggles. It began as a vertical strip on the far
left; it now runs HORIZONTALLY across the bottom of .col, BELOW the timeline band (its last child). Each pane
is an independent binary on/off, in a fixed, user-chosen order (Chat, Timeline, Outline, Feed — the user
2026-07-05, independent of the panes' layout order), and any subset (or none, or all) can be shown at once.
Fleet/Outline is its OWN pane (no longer an overlay swapped inside the chat pane).
Source-level pin against km._landing().
"""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


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
        self.assertIn("<div class=rail-btn data-pane=timeline>Timeline</div>", self.html)
        # Chat before Timeline before Outline(fleet) before Feed in the rail (fixed user-chosen order)
        idxs = [self.html.index("data-pane=" + k) for k in ("chat", "timeline", "fleet", "feed")]
        self.assertEqual(idxs, sorted(idxs), "rail order must be Chat, Timeline, Outline, Feed")
        # the old per-pane strips + the show-fleet swap + the timeline minimize bar are gone
        self.assertNotIn("pane-strip", self.html)
        self.assertNotIn("strip-toggle", self.html)
        self.assertNotIn("show-fleet", self.html)
        self.assertNotIn("tl-collapse", self.html)
        self.assertNotIn("cc-tl", self.html)

    def test_three_top_panes_in_fixed_order_then_the_timeline_band(self):
        # the TOP row is chat | gv-a | fleet | gv-b | feed; the timeline is the bottom band (gh + #tl-pane) AFTER
        # the row closes — so the DOM order is row panes first, then the gh gutter, then #tl-pane.
        order = ["id=chat-pane", "id=gv-a", "id=fleet-pane", "id=gv-b", "id=feed-pane", "id=gv-c", "id=hive-pane", "id=gh", "id=tl-pane"]
        idxs = [self.html.index(tok) for tok in order]
        self.assertEqual(idxs, sorted(idxs), "row panes, then the gh gutter, then the timeline band")
        # the pane rail is the BOTTOM BAR (the user 2026-07-05): LAST child of .col, AFTER the timeline band —
        # no longer the first child of .row. So its markup falls after #tl-pane.
        self.assertGreater(self.html.index("class=pane-rail"), self.html.index("id=tl-pane"),
                           "the rail runs across the bottom, below the timeline")
        # each pane/band is shown/hidden independently by its own body.po-* class
        self.assertIn("body:not(.po-chat) #chat-pane{display:none}", self.html)
        self.assertIn("body:not(.po-fleet) #fleet-pane{display:none}", self.html)
        self.assertIn("body:not(.po-feed) #feed-pane{display:none}", self.html)
        self.assertIn("body:not(.po-timeline) #gh,body:not(.po-timeline) #tl-pane{display:none}", self.html)

    def test_default_layout_is_chat_feed_timeline(self):
        # default: Chat + Feed + Timeline on, Fleet off (the user 2026-06-25; inlined on <body> for first paint)
        self.assertIn("<body class='po-chat po-feed po-timeline'>", self.html)

    def test_gutters_show_only_between_two_visible_panes(self):
        # gv-a sits chat|fleet → only when BOTH are shown
        self.assertIn("body:not(.po-chat) #gv-a,body:not(.po-fleet) #gv-a{display:none}", self.html)
        # gv-b sits (fleet|chat)|feed → it doubles as the chat|feed gutter when fleet is off, so it hides only
        # when feed is off OR neither chat nor fleet is on (feed would then be the lone pane)
        self.assertIn("body:not(.po-feed) #gv-b,body:not(.po-chat):not(.po-fleet) #gv-b{display:none}", self.html)

    def test_lit_rail_button_is_the_romp_accent(self):
        # the shell defines the accent locally (it loads no styles.css) and the ON toggle uses it
        self.assertIn(":root{--accent:#9cd2ff", self.html)
        self.assertIn(".rail-btn.on{color:var(--accent)", self.html)

    def test_rail_drives_a_persisted_pane_controller_exposed_for_the_legacy_toggle(self):
        # the controller toggles po-* from the rail, persists the set, and exposes __rompPaneToggle so the
        # legacy {romp:'toggleFleet'} postMessage routes through the same path
        self.assertIn("var PK='romp-panes',po={chat:true,fleet:false,feed:true,hive:false,timeline:true}", self.html)
        self.assertIn("window.__rompPaneToggle=togglePane", self.html)
        self.assertIn("togglePane(b.getAttribute('data-pane'))", self.html)
        self.assertIn("document.body.classList.toggle('po-chat',!!po.chat)", self.html)
        # ?panes=chat,fleet bookmarks an explicit set
        self.assertIn("get('panes')", self.html)

    def test_panes_are_resizable_by_flex_grow_persisted_per_pane(self):
        # each pane grows by a per-pane var the gutters write; the drag normalises visible panes to their px
        # widths first (so it shifts only the pair it sits between) and persists the grows across reloads
        self.assertIn("#chat-pane{flex:var(--g-chat,60) 1 0}#fleet-pane{flex:var(--g-fleet,34) 1 0}#feed-pane{flex:var(--g-feed,40) 1 0}", self.html)
        self.assertNotIn("--g-timeline", self.html)              # timeline is the fixed-height band, not a row grow
        self.assertIn("var GK='romp-pane-grow'", self.html)
        self.assertIn("setGrow(key(id),document.getElementById(id).offsetWidth)", self.html)
        self.assertIn("localStorage.setItem(GK,JSON.stringify(grow))", self.html)
        # gv-b picks its left neighbour live: fleet when shown, else chat (so it's the chat|feed gutter too)
        self.assertIn("document.body.classList.contains('po-fleet')?'fleet-pane':'chat-pane'", self.html)

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
        self.assertIn("var dw=t.outOfDate?(' \\u00b7 '+(t.status==='up'?'':'last known ')+driftWord(t)):''", self.html)
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
        self.assertIn("#chat-pane,#fleet-pane,#feed-pane,#hive-pane,#tl-pane{display:contents!important}", self.html)
        # the Outline (fleet) is a mobile TAB now, no longer desktop-only (the user 2026-07-11)
        self.assertNotIn("#fleet-pane{display:none!important}", self.html)
        self.assertIn("body[data-tab=timeline] .row{display:none}", self.html)   # timeline tab → band fills


if __name__ == "__main__":
    unittest.main()
