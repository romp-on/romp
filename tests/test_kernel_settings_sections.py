"""The Settings gear groups its rows into labelled SUBSECTIONS (the user 2026-06-24), re-cut
2026-07-12 (the user): the knobs that steer the fleet lead — Sessions (default directory, Auto
Nudge, backend), the judge model tiers, keyboard shortcuts — the day-to-day view prefs sit in the
middle (Chat, Sessions pane), and the cosmetic color pickers + the debug-only judge-visibility toggles
sink to the bottom, with the version footer last. (The Feed section is gone — its only row, the
global Colormap, lives under Colors now.)
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


class SettingsSectionsTest(unittest.TestCase):
    def test_the_subsection_headers_are_present_in_order(self):
        h = _gear_src()
        self.assertIn("<div class='rs-sec rs-sec-first'>Sessions</div>", h)
        for sec in ("Judges", "Keyboard shortcuts", "Chat", "Feed", "Sessions pane", "Colors", "Debug"):
            self.assertIn("<div class=rs-sec>%s</div>" % sec, h)
        # (The 2026-07-12 "Feed dissolved into Colors" rule ended 2026-08-18: the Feed section is back,
        # carrying the collapse-by-default checkbox moved off the feed footer.)
        order = [">Sessions<", ">Judges<", ">Keyboard shortcuts<", ">Chat<",
                 ">Feed<", ">Sessions pane<", ">Colors<", ">Debug<", ">romp · version<"]
        idx = [h.index(t) for t in order]
        self.assertEqual(idx, sorted(idx), "sections in the 2026-07-12 order, version last")

    def test_each_setting_sits_under_the_right_section(self):
        h = _gear_src()
        # Sessions (top): default dir + auto nudge + backend before the Judges header
        self.assertTrue(h.index(">Sessions<") < h.index("id=rs-defaultdir") < h.index(">Judges<"))
        self.assertTrue(h.index(">Sessions<") < h.index("id=rs-autonudge") < h.index(">Judges<"))
        self.assertTrue(h.index(">Sessions<") < h.index("id=rs-backend") < h.index(">Judges<"))
        # Judges (top): the four model/effort dropdowns — the SHOW toggles are NOT here anymore
        self.assertTrue(h.index(">Judges<") < h.index("id=rs-judgemodel") < h.index(">Keyboard shortcuts<"))
        self.assertTrue(h.index(">Judges<") < h.index("id=rs-indexeffort") < h.index(">Keyboard shortcuts<"))
        # Chat (middle): compact + branch between Chat and the Sessions-pane section
        self.assertTrue(h.index(">Chat<") < h.index("id=rs-compact") < h.index(">Sessions pane<"))
        self.assertTrue(h.index(">Chat<") < h.index("id=rs-branch") < h.index(">Sessions pane<"))
        # Sessions pane (middle towards the bottom): collapse idle gaps before the Colors header
        self.assertTrue(h.index(">Sessions pane<") < h.index("id=rs-collapsegaps") < h.index(">Colors<"))
        # Colors (bottom): the global colormap + the session palette between Colors and Debug
        self.assertTrue(h.index(">Colors<") < h.index("id=rs-cmap") < h.index(">Debug<"))
        self.assertTrue(h.index(">Colors<") < h.index("id=rs-pal") < h.index(">Debug<"))
        self.assertNotIn("rs-oldest", h)
        # Debug (bottom): the judge-set SHOW toggles after Debug; analytics + version after them
        self.assertLess(h.index(">Debug<"), h.index("id=rs-judges-index"))
        self.assertLess(h.index(">Debug<"), h.index("id=rs-judges-triage"))
        self.assertLess(h.index("id=rs-judges-triage"), h.index("id=ra-open"))
        self.assertLess(h.index("id=ra-open"), h.index("id=rsver"), "version is the very bottom")
        self.assertNotIn("id=rs-debug", h)   # the single Debug toggle is gone
        # the judge toggles read as a DEBUG *show* control, not an on/off for the judges (the user 2026-06-30):
        # labels lead with "Show", and the sub spells out that it doesn't enable/disable them
        self.assertIn("<b>Show indexing judges</b>", h)
        self.assertIn("<b>Show triage judges</b>", h)
        self.assertIn("does NOT turn the judges on or off", h)

    def test_the_sdk_backend_is_labelled_plain_sdk(self):
        # "SDK", not "SDK (headless)" (the user 2026-07-12): it drives the same full chat UI
        h = _gear_src()
        self.assertIn("<option value=sdk>SDK</option>", h)
        self.assertNotIn("headless", h)

    def test_judge_rows_are_one_line_label_plus_picker(self):
        # label + picker share the line (the user 2026-07-12): six .rs-jrow rows since the distilling
        # tier split out of triage (the user 2026-08-14), the select right after the hover sub, no
        # full-width select stacked under the label; the flex CSS carries the layout. Each label now
        # carries the hidden mixed-state marker (the settings-sync work, same day).
        h = _gear_src()
        self.assertEqual(h.count("rs-jrow"), 6)
        for sel in ("rs-judgemodel", "rs-judgeeffort", "rs-distillmodel", "rs-distilleffort",
                    "rs-indexmodel", "rs-indexeffort"):
            self.assertRegex(h, r"rs-jrow'><b>[^<]+<span class=rs-mixed hidden></span></b>"
                                r"<span class=rs-sub>[^<]*</span><select id=" + sel)
        self.assertIn("#rsettings .rs-jrow select {", _gear_css_src())

    def test_collapse_gaps_is_wired_to_the_shared_collapseGaps_setting(self):
        # the gear JS persists/loads romp:settings.collapseGaps; the timeline reads it (see romp-timeline-view.js)
        self.assertIn("collapseGaps: true", _gear_src())
        self.assertIn("s.collapseGaps = cg.checked", _gear_src())

    def test_show_active_only_is_wired_to_the_shared_activeOnly_setting(self):
        # "Show active sessions only" (the user 2026-08-12): a Timeline-section checkbox, default ON,
        # persisted as romp:settings.activeOnly; the timeline hides lanes with no activity in the
        # visible window and re-shows them when zoom/pan reaches their work (romp-timeline-view.js).
        self.assertIn("id=rs-activeonly checked", _gear_src())
        self.assertIn("activeOnly: true", _gear_src())
        self.assertIn("s.activeOnly = ao.checked", _gear_src())
        self.assertIn("ao.checked = s.activeOnly !== false", _gear_src())

    def test_section_header_styling_exists(self):
        self.assertIn("#rsettings .rs-sec {", _gear_css_src())
        self.assertIn("#rsettings .rs-sec-first { border-top: 0;", _gear_css_src())

    def test_oldest_first_toggle_is_gone(self):
        # the feed is always oldest-at-top now → no checkbox, no wiring (the user 2026-06-27)
        self.assertNotIn("rs-oldest", _gear_src())
        self.assertNotIn("oldestFirst", _gear_src())


if __name__ == "__main__":
    unittest.main()


# The gear moved from kernel-inline strings into the shared feed bundle
# (2026-07-13): ui/webview/gear.js is the single source both hosts render, so
# the gear pins read THAT file (and feed.css for its styling).
def _gear_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()


def _gear_css_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.css").read_text()
