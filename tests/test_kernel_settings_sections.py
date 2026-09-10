"""The Settings gear groups its rows into labelled SUBSECTIONS (the user 2026-06-24), re-cut
2026-07-12 (the user): the knobs that steer the fleet lead — Sessions (default directory, Auto
Nudge, backend), the judge model tiers, keyboard shortcuts — the day-to-day view prefs sit in the
middle (Chat, Sessions pane), and the cosmetic color pickers + the debug-only judge-visibility toggles
sink to the bottom, with the version footer last. (The Feed section is gone — its only row, the
global Colormap, lives under Colors now.)
"""
import os
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


class SettingsSectionsTest(unittest.TestCase):
    def test_the_subsection_headers_are_present_in_order(self):
        # The 2026-08-30 regrouping, the user's anchors fixed: login at the very top (Account),
        # Comments folded into Chat, session lifecycle apart from the panes, colors together,
        # judges low, updates + debug + version at the very bottom.
        h = _gear_src()
        self.assertIn("<div class='rs-sec rs-sec-first'>Account</div>", h)
        for sec in ("Sessions", "Chat", "Sessions pane", "Feed", "Appearance",   # Colors renamed 2026-08-28: it owns the overall Theme now
                    "Keyboard shortcuts", "Judges", "Updates & debug"):
            self.assertIn("<div class=rs-sec>%s</div>" % sec, h)
        order = [">Account<", ">Sessions<", ">Chat<", ">Sessions pane<", ">Feed<",
                 ">Appearance<", ">Keyboard shortcuts<", ">Judges<", ">Updates & debug<",
                 ">romp · version<"]
        idx = [h.index(t) for t in order]
        self.assertEqual(idx, sorted(idx), "sections in the 2026-08-30 order, version last")

    def test_each_setting_sits_under_the_right_section(self):
        h = _gear_src()
        # Account (the very top, the user 2026-08-30): the login row leads the gear
        self.assertTrue(h.index(">Account<") < h.index("id=rs-login-btn") < h.index(">Sessions<"))
        # Sessions (lifecycle): dir, backend, nudge, conserve, file editing — before Chat
        for rid in ("id=rs-defaultdir", "id=rs-backend", "id=rs-autonudge", "id=rs-suggestcompact",
                    "id=rs-conserve", "id=rs-thinksum", "id=rs-usertodos", "id=rs-fileedit"):
            self.assertTrue(h.index(">Sessions<") < h.index(rid) < h.index(">Chat<"), rid)
        # Chat: transcript prefs AND the comment defaults (comments are part of the chat)
        for rid in ("id=rs-compact", "id=rs-dense", "id=rs-branch", "id=rs-striprows", "id=rs-filelink", "id=rs-cmtmodel", "id=rs-cmtfast"):
            self.assertTrue(h.index(">Chat<") < h.index(rid) < h.index(">Sessions pane<"), rid)
        # Sessions pane, then Feed, then Colors
        self.assertTrue(h.index(">Sessions pane<") < h.index("id=rs-collapsegaps") < h.index(">Feed<"))
        self.assertTrue(h.index(">Feed<") < h.index("id=rs-feedcollapsed") < h.index(">Appearance<"))
        self.assertTrue(h.index(">Appearance<") < h.index("id=rs-cmap") < h.index(">Keyboard shortcuts<"))
        self.assertTrue(h.index(">Appearance<") < h.index("id=rs-pal") < h.index(">Keyboard shortcuts<"))
        # Judges sit low: the six dropdowns between Judges and the bottom group
        self.assertTrue(h.index(">Judges<") < h.index("id=rs-judgemodel") < h.index(">Updates & debug<"))
        self.assertTrue(h.index(">Judges<") < h.index("id=rs-indexeffort") < h.index(">Updates & debug<"))
        # Fast mode for the judges: a checkbox on the Triage model row, in the Judges section like the knobs it rides with
        self.assertTrue(h.index(">Judges<") < h.index("id=rs-judgefast") < h.index(">Updates & debug<"))
        self.assertNotIn("rs-oldest", h)
        # Updates & debug (the very bottom): auto-updates, the judge-SHOW toggles, analytics, version
        self.assertLess(h.index(">Updates & debug<"), h.index("id=rs-updates"))
        self.assertLess(h.index(">Updates & debug<"), h.index("id=rs-judges-index"))
        self.assertLess(h.index(">Updates & debug<"), h.index("id=rs-judges-triage"))
        self.assertLess(h.index("id=rs-judges-triage"), h.index("id=ra-open"))
        self.assertLess(h.index("id=ra-open"), h.index("id=rs-log-open"), "Open log is the section's last row (T290)")
        self.assertLess(h.index("id=rs-log-open"), h.index("id=rsver"), "version is the very bottom")
        self.assertNotIn("id=rs-debug", h)   # the single Debug toggle is gone
        # the judge toggles read as a DEBUG *show* control, not an on/off for the judges (the user 2026-06-30):
        # labels lead with "Show", and the sub spells out that it doesn't enable/disable them
        self.assertIn("<b>Show indexing judges</b>", h)
        self.assertIn("<b>Show triage judges</b>", h)
        self.assertIn("does NOT turn the judges on or off", h)

    def test_the_sdk_backend_is_labelled_plain_sdk(self):
        # the backends as the user reads them (T288, the user 2026-09-09): "Claude Code" (the default, no
        # qualifier — never "SDK" in copy a person reads), "Claude Code (tmux)", "Codex"
        h = _gear_src()
        self.assertIn("<option value=sdk>Claude Code</option><option value=tmux>Claude Code (tmux)</option><option value=codex>Codex</option>", h)
        self.assertNotIn("headless", h)
        self.assertNotIn(">SDK<", h)
        self.assertNotIn("SDK runs via", h)
        self.assertNotIn("new SDK session", h)
        # the tmux backend's offer: a checkbox in Updates & debug, off by default, stamped and propagated like the
        # judge knobs; the Default backend list follows it in the same modal (paintBackendOffer)
        self.assertTrue(h.index(">Updates & debug<") < h.index("id=rs-tmuxbackend") < h.index("id=rs-judges-index"))
        self.assertIn("<b>Enable Claude Code tmux backend <span class=rs-mixed hidden></span></b>", h)
        self.assertIn("id=rs-backend-note", h, "the sub-line that says a saved tmux default is set aside")

    def test_judge_rows_are_one_line_label_plus_picker(self):
        # label + picker share the line (the user 2026-07-12): nine .rs-jrow rows — six judge rows
        # since the distilling tier split out of triage (the user 2026-08-14), the judge concurrency
        # select (T277), plus the default-comment
        # model/effort pair (the user 2026-08-29), which reuses the same one-line layout — the select
        # right after the hover sub, no full-width select stacked under the label; the flex CSS
        # carries the layout. Each label carries the hidden mixed-state marker (the settings-sync work).
        h = _gear_src()
        self.assertEqual(h.count("rs-jrow"), 9)
        for sel in ("rs-judgemodel", "rs-judgeeffort", "rs-distillmodel", "rs-distilleffort",
                    "rs-indexmodel", "rs-indexeffort", "rs-judgeconc"):
            self.assertRegex(h, r"rs-jrow'><b>[^<]+<span class=rs-mixed hidden></span></b>"
                                r"<span class=rs-sub>[^<]*</span><select id=" + sel)
        self.assertIn("#rsettings .rs-jrow select {", _gear_css_src())

    def test_fast_mode_for_the_judges_sits_on_the_triage_model_row(self):
        # The user 2026-09-10: the box says Fast mode (the chat statusline's own word for it) and sits
        # with the model, after the Triage model picker, not on a row of its own under a paragraph. Its
        # label carries its own mixed mark; the row count above stays at nine (no .rs-jrow added).
        h = _gear_src()
        self.assertNotIn("Fast judging", h)
        row = h[h.index("<b>Triage model "):]
        row = row[:row.index("<b>Triage effort ")]
        self.assertIn("<select id=rs-judgemodel></select>", row)
        self.assertIn("<label class=rs-fastin id=rs-judgefast-wrap><input type=checkbox id=rs-judgefast>Fast mode"
                      "<span class=rs-mixed hidden></span>", row)
        self.assertLess(row.index("id=rs-judgemodel"), row.index("id=rs-judgefast"), "the box follows the picker")
        self.assertIn("<span class=rs-sub id=rs-judgefast-sub>", row, "a row hint like its neighbours', swapped by the gate")
        self.assertEqual(row.count("</div>"), 1, "one row: the box closes inside the Triage model row")
        # the one-line hint: the Opus-only condition and the premium, and where the pick goes
        hint = h[h.index("var JUDGEFAST_SUB = "):]
        hint = hint[:hint.index(";\n")]
        self.assertIn("Opus-only", hint)
        self.assertIn("premium", hint)
        self.assertIn("connected machine's kernel", hint, "the copy says the pick follows to the other machines")
        # greyed with the reason while no judge tier is on Opus (the box is inert then: the opt-in rides only Opus calls)
        self.assertIn("function judgeFastGate", h)
        self.assertIn("var JUDGEFAST_SUB_OFF = \"Fast mode is Opus-only, and this tier is not on Opus.", h)
        # T300: the same box follows the Distilling and Indexing pickers, each greyed on ITS tier's effective model
        for sel, tier in (("rs-distillmodel", "distillfast"), ("rs-indexmodel", "indexfast")):
            row = h[h.index("<select id=%s></select>" % sel):]
            row = row[:row.index("</div>")]
            self.assertIn("<label class=rs-fastin id=rs-%s-wrap><input type=checkbox id=rs-%s>Fast mode<span class=rs-mixed hidden></span>" % (tier, tier), row)
            self.assertIn("<span class=rs-sub id=rs-%s-sub>" % tier, row)
        self.assertIn("#rsettings .rs-fastin.rs-off {", _gear_css_src())

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

    def test_one_group_per_row_is_wired_to_the_shared_stripGroupRows_setting(self):
        # "One tag group per row in the tab strip": a Chat-section checkbox, default ON, persisted as
        # romp:settings.stripGroupRows. render.ts is the reader: the strip's row breaks and the trail's
        # boundary read it, and it rides the strip's rebuild signature so a flip repaints at once.
        self.assertIn("id=rs-striprows checked", _gear_src())
        self.assertIn("One tag group per row in the tab strip", _gear_src())
        self.assertEqual(_gear_src().count("stripGroupRows: true"), 2, "on in both of load()'s default literals")
        self.assertIn("s.stripGroupRows = sr.checked", _gear_src())
        self.assertIn("sr.checked = s.stripGroupRows !== false", _gear_src())

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
