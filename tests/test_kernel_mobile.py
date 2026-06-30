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
        for pane in ("data-pane=chat", "data-pane=feed", "data-pane=timeline"):
            self.assertIn(pane, html)

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
        # splitter + focus-ring + fleet-toggle + settings-fullscreen + mobile switcher + per-pane collapse
        # handles + the build-staleness banner + keyboard-shortcuts = 11.
        html = km._landing()
        self.assertEqual(html.count("<script>"), 11)

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
        # zeroes env() on Chrome, so it must NOT request cover. (The bar's safe-area padding is now gone
        # entirely too — see test_bottom_bar_has_no_safe_area_padding — since Firefox doesn't zero it.)
        html = km._landing()
        self.assertNotIn("viewport-fit=cover", html)
        self.assertIn("100dvh", html)            # still address-bar-aware
        self.assertIn("user-scalable=no", html)  # pinch-zoom governance preserved alongside the change

    def test_bottom_bar_has_no_safe_area_padding(self):
        # The user 2026-06-19 (Firefox/Android): the bar showed a dead slab below the labels in PORTRAIT
        # only. The Android nav bar is at the BOTTOM in portrait (env(safe-area-inset-bottom) > 0) and on
        # the SIDE in landscape (inset = 0), and Firefox — unlike Chrome — does NOT zero that inset without
        # viewport-fit=cover, so #mtabs's padding-bottom:env(safe-area-inset-bottom) rendered as a
        # portrait-only slab. Without cover the viewport already clears the nav bar, so the padding is
        # redundant and removed; the fixed bar simply sits at the visible bottom. No env() inset on the bar.
        html = km._landing()
        self.assertNotIn("env(safe-area-inset", html)
        self.assertIn("#mtabs{display:flex;position:fixed;left:0;right:0;bottom:0", html)


class TimelineTouchSurface(unittest.TestCase):
    def test_timeline_fits_svg_and_drops_overflow_scroller_on_touch(self):
        # regression: the view forces a >=640px SVG; on a ~390px phone the default overflow-x:auto turned
        # that into a native horizontal scroller that beat the one-finger pan gesture. On a touch device the
        # SVG must fit the screen (width:100%) with no overflow scroller, so the gesture owns horizontal pan.
        css = km.TIMELINE_CSS
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

    def test_picker_is_gated_on_touch_not_pane_width(self):
        # regression: the chat iframe is one of three desktop panes, so it's ALWAYS narrow. A bare
        # max-width breakpoint matched that narrow pane and swapped the mobile picker in on desktop,
        # replacing the real tab strip. The picker must trigger only on a touch device (pointer:coarse).
        css = km._CHAT_MOBILE_CSS
        self.assertIn("@media (pointer:coarse) and (max-width:1024px){", css)
        self.assertNotIn("(max-width:820px),", css)   # the width-only OR-clause that leaked onto desktop is gone

    def test_picker_routes_a_pick_and_wires_new_session_and_summary(self):
        js = km._CHAT_MOBILE_JS
        self.assertIn(".tab[data-id", js)             # a row tap clicks the real tab (render.js focuses it)
        self.assertIn("MutationObserver", js)         # re-syncs as tabs change
        self.assertIn(".tab-add", js)                 # + → open / new session
        self.assertIn(".tab-collapse", js)            # ▾ → toggle the summary


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
