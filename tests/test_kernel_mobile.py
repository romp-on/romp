#!/usr/bin/env python3
"""Mobile shell: the combined landing page collapses to a one-pane-at-a-time tab switcher on a
narrow/touch viewport, and the kernel tells the shell to switch to Chat when a feed/timeline tap
brings the chat forward. Pure-HTML + routing asserts; no real session data.
"""
import os
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = SourceFileLoader("romp_kernel_mobile", os.path.join(BIN, "romp-kernel")).load_module()


class LandingShell(unittest.TestCase):
    def test_three_panes_are_addressable_iframes(self):
        html = km._landing()
        for fid in ("id=f-chat", "id=f-feed", "id=f-timeline"):
            self.assertIn(fid, html)

    def test_mobile_tabbar_one_button_per_pane(self):
        html = km._landing()
        self.assertIn("id=mtabs", html)
        for pane in ("data-pane=chat", "data-pane=fleet", "data-pane=feed", "data-pane=timeline"):
            self.assertIn(pane, html)

    def test_outline_is_a_mobile_tab_not_desktop_only(self):
        # the user 2026-07-11, who couldn't access the outline view in the mobile UI — the fleet pane was
        # explicitly desktop-only (#fleet-pane display:none!important, no tab, no switcher entry).
        html = km._landing()
        self.assertIn(">Outline</button>", html)                       # the tab exists, labeled Outline
        self.assertIn("#f-chat.m-on,#f-fleet.m-on,#f-feed.m-on{display:block}", html)   # ...and shows as the active pane
        self.assertNotIn("#fleet-pane{display:none!important}", html)  # the desktop-only exclusion is gone
        self.assertIn("fleet:document.getElementById('f-fleet')", km._LANDING_MOBILE_JS)
        # the chat header's Fleet pill / the fleet's back-to-chat (toggleFleet) is a tab switch on mobile
        self.assertIn("'toggleFleet'", km._LANDING_MOBILE_JS)

    def test_rail_actions_reachable_on_mobile(self):
        # the user 2026-07-11: settings / the network panel / usage stats were rail-only (the rail is
        # hidden on mobile). data-act buttons on the bar; each routes to the existing machinery.
        html = km._landing()
        for act in ("data-act=settings", "data-act=net", "data-act=usage", "data-act=restart"):
            self.assertIn(act, html)
        # ICONS, not words (the user 2026-07-11): settings wears the desktop rail's own gear glyph, net its
        # network-tree SVG; usage gets the theme's own motif — two stacked fill bars at different levels
        # the settings icon is the SAME gear the desktop rail uses (U+26ED ⛭), not the outlined star it had
        self.assertIn("data-act=settings aria-label=Settings title=Settings>⛭</button>", html)
        self.assertIn("id=rail-gear title=Settings aria-label=Settings>⛭</div>", html)  # matches the rail
        self.assertNotIn("&#9885;", html)                               # the old outlined-star glyph is gone
        self.assertIn("data-act=net aria-label='Remote kernels'", html)
        self.assertIn("<rect x='1' y='3' width='9' height='4' rx='1' fill='currentColor'/>", html)   # the used-bar fill
        self.assertNotIn(">Gear</button>", html)
        self.assertIn("{romp:'openSettings'}", km._LANDING_MOBILE_JS)   # same path as the desktop gear
        self.assertIn("__rompOpenNet", km._LANDING_MOBILE_JS)           # opens the shell's remotes panel
        self.assertIn("window.__rompOpenNet=open", km._LANDING_REMOTES_JS)
        self.assertIn("__rompUsagePanel", km._LANDING_MOBILE_JS)        # the tooltip's bars as a modal
        self.assertIn("window.__rompUsagePanel=function", km._LANDING_USAGE_JS)
        self.assertIn("#ru-tip.ru-modal", html)                         # centered placement for the panel
        # the lifted-fullscreen settings iframe must override the mobile display:none
        self.assertIn("body.settings-open #f-feed{display:block;position:fixed", html)

    def test_mobile_restart_button_reuses_the_rail_refresh_kernel_restart(self):
        # the user 2026-07-22: there was no restart-kernel affordance on mobile (the rail's own ↻ is hidden
        # there). Add a bar button that fires the SAME restart the rail does — factored to window.__rompRestart
        # (POST /restart, poll /healthz, reload) so both surfaces share one path, not a copy.
        html = km._landing()
        # …and it wears the SAME browser-style reload svg as the rail (the ↻ text glyph is gone, 2026-07-27)
        self.assertIn("data-act=restart aria-label='Restart kernel' title='Restart kernel'>" + km._REFRESH_SVG + "</button>", html)
        self.assertIn("window.__rompRestart=function", km._LANDING_SETTINGS_JS)   # the shared restart path
        self.assertIn("fetch('/restart',{method:'POST'})", km._LANDING_SETTINGS_JS)
        self.assertIn("rf.onclick=function(){rf.style.pointerEvents='none';rf.style.opacity='0.5';window.__rompRestart();}", km._LANDING_SETTINGS_JS)
        self.assertIn("restart:function(){try{window.__rompRestart", km._LANDING_MOBILE_JS)   # the bar routes to it

    def test_mobile_bar_reservation_collapses_while_the_keyboard_is_open(self):
        # the user 2026-07-22: focusing the composer opened the keyboard and left a dead black band between
        # the box and the keyboard — the fixed bar's reserved height (--mtabs-h) showing through while the
        # bar itself was hidden behind the keyboard. Collapse the reservation to 0 when the keyboard is open
        # (visual viewport much shorter than the layout viewport), restore it when the keyboard closes.
        js = km._LANDING_MOBILE_JS
        self.assertIn("function kbOpen(){var vv=window.visualViewport;return vv?(window.innerHeight-vv.height>120):false;}", js)
        self.assertIn("--mtabs-h',(kbOpen()?0:(bar.offsetHeight||0))+'px'", js)

    def test_usage_modal_dismisses_via_a_real_backdrop_not_a_document_click(self):
        # the user 2026-07-22: on mobile the Usage panel got STUCK — an outside tap landed on a content
        # iframe (a different document), so the shell's document-level click listener never fired and the
        # modal never closed. Fix: a real full-screen backdrop in the SHELL document (like the net panel's
        # #rnet-back) catches the tap, so any tap over it dismisses. The #ru-tip is pointer-events:none, so
        # a tap that visually lands on the panel still reaches the backdrop underneath and closes it.
        html, js = km._landing(), km._LANDING_USAGE_JS
        self.assertIn("ru-back", js)                              # the backdrop element is created
        self.assertIn("back.onclick=off", js)                    # a tap on the backdrop closes the modal
        self.assertIn("back.classList.add('on')", js)            # ...shown when the panel opens
        self.assertIn("#ru-back{position:fixed;inset:0", html)   # full-screen, in the shell document
        self.assertIn("#ru-back.on{display:block}", html)
        # the old broken mechanism (a document-level capture click listener) is gone
        self.assertNotIn("addEventListener('click',off,true)", js)

    def test_desktop_unchanged_tabbar_hidden_until_breakpoint(self):
        html = km._landing()
        self.assertIn("#mtabs{display:none}", html)   # hidden by default (desktop)
        self.assertIn("@media", html)                 # a breakpoint reveals it + collapses to one pane
        self.assertIn(".m-on{display:block}", html)   # the single active pane on mobile
        # the desktop shell is the flex pane row (chat | fleet | feed | timeline)
        self.assertIn(".col{display:flex", html)
        self.assertIn("src=/chat", html)
        self.assertIn("src=/feed", html)
        self.assertIn("src=/timeline", html)

    def test_the_shell_leaves_a_hair_of_slack_down_the_right_edge(self):
        # The panes tiled flush to the window, so whatever sat hard right inside one — a feed card's
        # controls, the timeline's lock padlock at the now-edge, the rail's right-pinned actions — was
        # pressed against the frame or clipped by it (the user 2026-07-23). .col is the one wrapper that
        # covers the pane row, the timeline band and the bottom rail together.
        html = km._landing()
        # height:100%, not 100vh: the body is what clips, and on iOS 100vh is the address-bar-collapsed
        # viewport, which pushed the rail out of the bottom (the user 2026-07-29)
        self.assertIn(".col{display:flex;flex-direction:column;height:100%;box-sizing:border-box;padding-right:3px}", html)
        # border-box, or the strip ADDS to the 100vh box and the shell overflows instead of insetting
        self.assertIn("box-sizing:border-box;padding-right:3px", html)

    def test_the_right_edge_strip_is_desktop_only(self):
        # One pane fills a phone screen, so a 3px sliver of backdrop down its edge reads as a rendering
        # fault, not as slack. The desktop longhand survives the media query unless it is named there.
        html = km._landing()
        i = html.index("@media (max-width:820px),(pointer:coarse)")
        mobile = html[i:i + 2000]
        self.assertIn("padding-right:0", mobile, "the mobile .col must cancel the desktop strip")

    def test_mobile_pane_has_explicit_height_not_auto(self):
        # regression: the mobile pane was sized with height:auto + bottom offset; mobile browsers read
        # height:auto on an iframe as "size to content" and collapse it (chat shrank to its tab bar).
        html = km._landing()
        self.assertIn("100dvh", html)                          # explicit, address-bar-aware viewport height
        self.assertNotIn("height:auto;display:none", html)     # the collapsing iframe rule is gone

    def test_shell_reserves_the_bar_height_so_it_cannot_cover_the_pane(self):
        # regression: a position:fixed bar overlapped the chat composer (which is why flex briefly replaced
        # it). The bar is fixed again — glued to the viewport bottom so no dead space can sit below it — but
        # now .col RESERVES the bar's measured height (--mtabs-h) as padding-bottom, so the iframes tile
        # ABOVE the bar and it can't cover the composer. One pane shows at a time, keyed off body[data-tab].
        html = km._landing()
        self.assertIn("padding-bottom:var(--mtabs-h", html)    # .col reserves the bar's height
        self.assertIn("--mtabs-h", km._LANDING_MOBILE_JS)      # ...measured from the live bar (offsetHeight)
        self.assertIn("#f-timeline.m-on{display:block}", html) # timeline is a mobile tab pane (it lives in the row now)
        self.assertIn("data-tab", km._LANDING_MOBILE_JS)       # show() marks the active pane on <body>

    def test_shell_reveal_listener_wired(self):
        html = km._landing()
        self.assertIn("app=shell", html)              # shell WS catches kernel reveals (feed/timeline tap)
        self.assertIn("'reveal'", html)               # ...and window reveals (timeline deep-link)

    def test_timeline_iframe_is_the_fourth_pane(self):
        # the timeline is its own rail-toggled pane now (the user 2026-06-24), not a bottom band: the iframe
        # carries id=f-timeline inside #tl-pane, and the old stale-id splitter bug must not regress.
        html = km._landing()
        self.assertIn("id=f-timeline", html)                      # the iframe carries this id
        self.assertIn("<div class=pane id=tl-pane><iframe id=f-timeline src=/timeline></iframe></div>", html)
        self.assertNotIn("getElementById('t')", km._LANDING_JS)   # the stale id is gone

    def test_mobile_switcher_is_isolated_in_its_own_script(self):
        # the switcher runs in a separate <script> so a splitter throw can't disable the tab bar. Each shell
        # behaviour gets its own isolated <script>: boot-splash + connection-status banner + rail-usage +
        # splitter + focus-ring + fleet-toggle + settings-fullscreen + mobile switcher + viewport-pin +
        # per-pane collapse handles + the build-staleness banner + the remote-drift push banner = 12
        # (the user 2026-06-23; + boot-splash + rail-usage 2026-06-26; + connection-status banner
        # 2026-06-27; + the visible-viewport pin; + the remote-drift banner 2026-07-04; + the head's
        # ios-standalone viewport flip and the push bell 2026-08-07, plans/ios-app.md).
        html = km._landing()
        self.assertEqual(html.count("<script>"), 14)

    def test_bottom_bar_is_text_only_and_compact(self):
        html = km._landing()
        self.assertNotIn("class=ic", html)                       # no icon spans — text labels only
        self.assertIn(">Chat</button>", html)                    # plain text label, no icon child
        self.assertIn("#mtabs{display:flex;position:fixed", html)  # glued to the viewport bottom; height still = its text + padding (no fixed height)

    def test_bottom_bar_is_fixed_to_viewport_so_no_dead_space_below_it(self):
        # The recurring bug (the user, through 2026-06-19): the bar was flex-placed at the bottom of a body
        # whose height is only a viewport ESTIMATE (100dvh, then --app-h); when the estimate under-shot the
        # painted area on Android Chrome, a dead slab appeared BELOW the Chat/Feed/Timeline labels. Gluing
        # the bar to the visible viewport bottom (position:fixed;bottom:0) makes "below the bar" impossible
        # by construction, whatever the height math does. .col reserves --mtabs-h so it can't cover content.
        html = km._landing()
        self.assertIn("#mtabs{display:flex;position:fixed;left:0;right:0;bottom:0", html)
        self.assertIn("padding-bottom:var(--mtabs-h", html)
        self.assertNotIn("#mtabs{flex:", html)                   # the bar is NOT itself a flex child anymore
        # the reservation is measured from the live bar, so it tracks the gesture-area inset exactly
        self.assertIn("setProperty('--mtabs-h'", km._LANDING_MOBILE_JS)
        self.assertIn("offsetHeight", km._LANDING_MOBILE_JS)

    def test_landing_disables_browser_pinch_zoom(self):
        # the top document governs pinch-zoom for the whole visual viewport (incl. the timeline iframe), so
        # it must disable page zoom or iOS page-zooms on a timeline pinch instead of running the gesture.
        html = km._landing()
        self.assertIn("user-scalable=no", html)
        self.assertIn("maximum-scale=1", html)

    def test_landing_avoids_viewport_fit_cover(self):
        # regression (the user 2026-06-17): viewport-fit=cover made Android Chrome report a non-zero
        # env(safe-area-inset-bottom) even though the viewport already sits above the nav bar, so #mtabs's
        # safe-area padding-bottom rendered as a dead slab below the Chat/Feed/Timeline labels (and cover
        # clipped the top under the status bar). The default viewport auto-insets clear of system UI and
        # zeroes env() on Chrome, so the STATIC meta must NOT request cover.
        #
        # Narrowed, not repealed, for the installable app (plans/ios-app.md; the user 2026-08-07): an iOS
        # home-screen app has no browser chrome keeping #mtabs off the home indicator, and iOS only
        # populates env() under cover — so the head script flips cover on AT RUNTIME, gated on
        # navigator.standalone, which is iOS-only and standalone-only. No Android browser can ever take
        # that branch, so the 2026-06-17 regression cannot recur through it.
        html = km._landing()
        self.assertIn("<meta name=viewport content='width=device-width,initial-scale=1,"
                      "maximum-scale=1,user-scalable=no'>", html)     # the static meta: no cover
        self.assertEqual(html.count("viewport-fit=cover"), 1)         # exactly the runtime flip…
        self.assertIn("if(navigator.standalone)", html)               # …behind the iOS-standalone gate
        self.assertLess(html.index("if(navigator.standalone)"), html.index("viewport-fit=cover"))
        self.assertIn("100dvh", html)            # still address-bar-aware
        self.assertIn("user-scalable=no", html)  # pinch-zoom governance preserved alongside the change

    def test_bottom_bar_has_no_safe_area_padding(self):
        # The user 2026-06-19 (Firefox/Android): the bar showed a dead slab below the labels in PORTRAIT
        # only. The Android nav bar is at the BOTTOM in portrait (env(safe-area-inset-bottom) > 0) and on
        # the SIDE in landscape (inset = 0), and Firefox — unlike Chrome — does NOT zero that inset without
        # viewport-fit=cover, so #mtabs's padding-bottom:env(safe-area-inset-bottom) rendered as a
        # portrait-only slab. Without cover the viewport already clears the nav bar, so the padding is
        # redundant on the BROWSER bar, which stays inset-free.
        #
        # The one sanctioned exception (plans/ios-app.md; the user 2026-08-07): installed on an iOS home
        # screen the browser chrome is gone and the bar must clear the home indicator, so a single rule
        # keyed on html.ios-standalone — a class set only under navigator.standalone, which no Android
        # browser exposes — reclaims the inset there and nowhere else.
        html = km._landing()
        self.assertEqual(html.count("env(safe-area-inset"), 1)        # exactly the standalone rule below
        self.assertIn("html.ios-standalone #mtabs{padding-bottom:env(safe-area-inset-bottom,0px)}", html)
        self.assertIn("#mtabs{display:flex;position:fixed;left:0;right:0;bottom:0", html)


class TimelineTouchSurface(unittest.TestCase):
    def test_timeline_fits_svg_and_drops_overflow_scroller_on_touch(self):
        # regression: the view forces a >=640px SVG; on a ~390px phone the default overflow-x:auto turned
        # that into a native horizontal scroller that beat the one-finger pan gesture. On a touch device the
        # SVG must fit the screen (width:100%) with no overflow scroller, so the gesture owns horizontal pan.
        # The wrapper styles moved to ui/webview/timeline-pane.css (shared with the VS Code view); the
        # kernel reads that file live, so pin the served page rather than a constant.
        css = km._timeline_page()
        self.assertIn("@media (pointer:coarse)", css)
        self.assertIn("overflow-x:hidden", css)
        self.assertIn("touch-action:pan-y", css)
        self.assertIn(".romp-tl-wrap svg{width:100%", css)


class ChatSessionPicker(unittest.TestCase):
    def test_chat_page_collapses_tabs_into_a_header_on_mobile(self):
        chat = km._chat_page()
        self.assertIn("#mhdr", chat)                        # the compact header replaces the tab strip
        self.assertIn("#tabbar #tabs{display:none}", chat)  # the wrapping multi-row tab strip is hidden
        self.assertIn("id='mcur'", km._CHAT_MOBILE_JS)      # current-session button that opens the list
        self.assertIn("id='mlist'", km._CHAT_MOBILE_JS)     # the dropdown list of sessions

    def test_desktop_hides_the_mobile_header_and_list(self):
        # regression: #mlist (a #tabbar sibling, not inside #mhdr) had no desktop rule, so on desktop it
        # rendered the session rows as plain text in the tab bar. Both must be hidden off-mobile.
        self.assertIn("#mhdr,#mlist{display:none}", km._CHAT_MOBILE_CSS)

    def test_picker_is_custom_colored_not_native_select(self):
        # a native <select> can't render the per-session identity colors, so the picker is our own element
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertNotIn("createElement('select')", js)   # not native
        self.assertIn("--chip-bg", js)                    # reads each session's identity color
        self.assertIn("#mcur.colored", css)               # the current button wears that color

    def test_a_remote_sessions_host_prefix_stays_quiet_metadata(self):
        # the user 2026-07-30, on a phone: "host:name" came through the picker painted whole in the
        # session's identity colour at full weight, where desktop renders the "host:" as quiet metadata
        # (host-prefix.ts: dim, italic, never bold, a step smaller). The cause was textContent flattening
        # the desktop label — which already carries a <span class="host-prefix"> — so the fix CLONES the
        # label's child nodes instead of re-deriving the split, keeping ONE definition of the treatment.
        js = km._CHAT_MOBILE_JS
        self.assertIn("function fillName(elm,s){", js)
        self.assertIn("s.lab.cloneNode(true).childNodes", js, "the nodes are cloned, not flattened")
        # ...and the clone's childNodes are SLICED before the walk: appending straight off that live
        # NodeList removes each node as it goes and skips every second one, which dropped the session
        # name and left a row reading just "host:" (caught in a browser, not by a source pin)
        self.assertIn("[].slice.call(s.lab.cloneNode(true).childNodes).forEach(", js)
        self.assertIn("lab:lab,", js, "read() carries the label element through")
        self.assertIn("fillName(nm,act);", js, "the current-session button too, not just the rows")
        self.assertNotIn("nm.textContent=act.name", js)
        self.assertNotIn("lbl.textContent=s.name", js)
        # the treatment itself is NOT re-declared here: the cloned span brings its class, and the chat
        # page loads the sheet that styles it (a second spelling would eventually disagree with desktop)
        self.assertNotIn("host-prefix", km._CHAT_MOBILE_CSS)
        self.assertIn("<link href=/dist/styles.css", km._chat_page())

    def test_picker_rows_match_desktop_colored_name_plus_status_dot(self):
        # the user 2026-07-22: on mobile the picker painted a per-session identity DOT (grey #666 when the
        # session had no color, and confusingly the identity color when it did) — desktop has no such dot.
        # Match desktop: the identity color tints the NAME text (inline, like the Fleet list / colored tab
        # label), and the dot MIRRORS the tab's own status dot — gold when working, GREEN when awaitingBg
        # (idle-waiting-on-bg-work, the .tab-dot.await), none otherwise.
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertIn("fillName(lbl,s);if(s.bg)lbl.style.color=s.bg;", js)   # identity color on the NAME
        self.assertIn("if(s.working){var wd=document.createElement('span');wd.className='workdot';", js)  # gold dot when working
        # awaitingBg is read off the desktop tab's own green dot (no tab-working class on an awaiting tab)
        self.assertIn("awaitbg:!!t.querySelector('.tab-dot.await')", js)
        self.assertIn("else if(s.awaitbg){var wd=document.createElement('span');wd.className='workdot await';", js)  # green dot when awaiting
        self.assertNotIn(".mrow .dot{", css)              # the old identity/grey dot is gone
        self.assertNotIn("dot.style.background=s.bg", js)  # ...and nothing paints identity onto a dot
        # the dots are the SAME status colors desktop uses (styles.css --st-working-bg gold, --st-awaitbg-bg green)
        self.assertIn(".mrow .workdot{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:var(--st-working-bg,#e0b020)}", css)
        self.assertIn(".mrow .workdot.await{background:var(--st-awaitbg-bg,#54B204)}", css)
        self.assertNotIn("'• ')+s.name", js)              # the '• ' text-bullet prefix on rows is gone
        # the current-session header uses the same gold/green status dot, not the text bullet either
        self.assertIn("#mcur .wd{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:var(--st-working-bg,#e0b020)}", css)
        self.assertIn("#mcur .wd.await{background:var(--st-awaitbg-bg,#54B204)}", css)
        self.assertIn("wd.style.display=(act&&(act.working||act.awaitbg))?'':'none'", js)
        self.assertIn("wd.classList.toggle('await',!!(act&&act.awaitbg&&!act.working))", js)
        self.assertNotIn("(act.working?'• ':'')", js)

    def test_current_session_title_is_bold_color_on_the_grey_chip(self):
        # the user 2026-07-22: the mobile current-session title reads as the identity color in BOLD on the
        # SAME grey chip as the +/madd button (#2a2a2a), with a hairline color border, not the color as a fill.
        css = km._CHAT_MOBILE_CSS
        self.assertIn("#mcur.colored{background:#2a2a2a;color:var(--cbg);border-color:var(--cbg)}", css)
        self.assertIn("#madd{flex:0 0 auto", css)                    # ...and the + button is that same grey
        self.assertIn("background:#2a2a2a;color:#bbbbbb", css)       # (the shared chip grey)
        self.assertIn("white-space:nowrap;font-weight:700}", css)   # the .nm name span is bold

    def test_no_pane_focus_ring_on_mobile(self):
        # the user 2026-07-22: only one pane shows at a time on mobile, so the "which pane is focused" ring
        # is meaningless — it just draws a blue border around the whole view. It's suppressed in the mobile
        # media query while the desktop grid (many panes at once) keeps it.
        html = km._landing()
        self.assertIn(".pane.pane-focused::after{display:none}", html)   # killed on mobile
        # the desktop ring itself still exists (outside the media query)
        self.assertIn(".pane.pane-focused::after{content:'';", html)

    def test_picker_is_gated_on_touch_not_pane_width(self):
        # regression: the chat iframe is one of three desktop panes, so it's ALWAYS narrow. A bare
        # max-width breakpoint matched that narrow pane and swapped the mobile picker in on desktop,
        # replacing the real tab strip. The picker must trigger only on a touch device (pointer:coarse).
        css = km._CHAT_MOBILE_CSS
        self.assertIn("@media (pointer:coarse) and (max-width:1024px){", css)
        self.assertNotIn("(max-width:820px),", css)   # the width-only OR-clause that leaked onto desktop is gone

    def test_picker_routes_a_pick_and_wires_new_session(self):
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertIn(".tab[data-id", js)             # a row tap clicks the real tab (render.js focuses it)
        self.assertIn("MutationObserver", js)         # re-syncs as tabs change
        self.assertIn(".tab-add", js)                 # + → open / new session
        # the dead "Toggle summary" button (#mcoll → .tab-collapse) is GONE (the user 2026-07-22): the
        # ledger/summary strip it toggled was retired, so .tab-collapse never existed in the DOM produced
        # by render.ts and the button did nothing. Nothing left to rewire it to, so it was removed.
        self.assertNotIn("mcoll", js)
        self.assertNotIn(".tab-collapse", js)
        self.assertNotIn("#mcoll", css)

    def test_picker_rows_have_an_end_session_control(self):
        # the user 2026-07-22: the mobile picker had no way to end a session (desktop has the tab x). Add a
        # per-row x that clicks the hidden desktop tab's own .tab-close, reusing its confirm dialog
        # (Close tab / End session / Cancel) + the endSession/closeTab plumbing — no new backend.
        js, css = km._CHAT_MOBILE_JS, km._CHAT_MOBILE_CSS
        self.assertIn("x.className='mclose'", js)
        self.assertIn("x.title='End session'", js)
        # it triggers the real tab's close x, and stops propagation so it doesn't also switch sessions
        self.assertIn("var c=rt&&rt.querySelector('.tab-close');if(c)c.click();", js)
        self.assertIn("e.stopPropagation();hide();", js)
        self.assertIn(".mrow .mclose{", css)


class RevealRouting(unittest.TestCase):
    def test_reveal_chat_focuses_chat_and_nudges_shell(self):
        sent = []
        orig = km._send_to_app
        km._send_to_app = lambda app, msg: sent.append((app, msg))
        try:
            km._reveal_chat({"type": "focus", "id": "s1"})
        finally:
            km._send_to_app = orig
        apps = [a for a, _ in sent]
        self.assertIn("chat", apps)                   # still focuses the chat clients (unchanged behavior)
        self.assertIn("shell", apps)                  # AND tells the mobile shell to show the Chat tab
        chat_msg = next(m for a, m in sent if a == "chat")
        shell_msg = next(m for a, m in sent if a == "shell")
        self.assertEqual(chat_msg["id"], "s1")        # the original focus payload is preserved verbatim
        self.assertEqual(shell_msg, {"type": "reveal", "pane": "chat"})


if __name__ == "__main__":
    unittest.main()
