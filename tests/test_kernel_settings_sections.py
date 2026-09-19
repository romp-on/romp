"""The Settings gear groups its rows into labelled SUBSECTIONS (the user 2026-06-24), re-cut
2026-07-12 (the user): the knobs that steer the fleet lead — Sessions (default directory, Auto
Nudge, backend), the judge model tiers, keyboard shortcuts — the day-to-day view prefs sit in the
middle (Chat, Sessions pane), and the cosmetic color pickers + the debug-only judge-visibility toggles
sink to the bottom, with the version footer last. (The Feed section is gone — its only row, the
global Colormap, lives under Colors now.)
"""
import os
import unittest
from pathlib import Path
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
    """The panel is in TABS since T379 (the user 2026-09-12): seven pills (General, Chat, Feed, Sessions, Automation, Task
    tracking, Debug: T404's cut, the user 2026-09-13; Appearance is a section of General), one pane each (the tab widgets are a section of Chat); every row keeps its id and its key; each pane opens with a first section head and keeps its
    sub-heads in the approved order; the version footer stays last."""
    PANES = ("general", "chat", "feed", "sessions", "automation", "tasks", "debug")   # T404: Automation new, Appearance a General section

    def test_the_subsection_headers_are_present_in_order(self):
        h = _gear_src()
        self.assertLess(h.index("id=rs-tabs"), h.index("data-pane=general"), "the pills come first")
        for pane, heads in (("general", ["Account", "Panes", "Appearance", "Permissions", "This machine", "Keyboard shortcuts"]), ("chat", ["Display", "Comments", "Thinking", "Chat history", "Tab strip", "Tab widgets"]),
                            ("feed", ["Cards"]), ("sessions", ["New sessions"]), ("automation", ["Nudges", "Model"]), ("tasks", ["Task tracking", "Judges"]),   # Model: the two model switches (2026-09-17)
                            ("debug", ["Judging bands", "Diagnostics"])):
            p = _pane(h, pane)
            self.assertIn("<div class='rs-sec rs-sec-first'>%s</div>" % heads[0], p, pane + " opens with its first head")
            idx = [p.index(">%s<" % t) for t in heads]
            self.assertEqual(idx, sorted(idx), pane + ": sub-heads in order")
        self.assertIn("<div class=rs-sec id=rs-panes-sec>Panes</div>", h)   # the Panes head keeps its id (initGear hides it off the dashboard)
        # the tab widgets are a SECTION of Chat (the user 2026-09-12), its head the anchor the strip's gear opens the panel at; no Tabs tab
        self.assertIn("<div class='rs-sec' data-section=tabwidgets>Tab widgets</div>", _pane(h, "chat"))
        # T415 (the user 2026-09-14): the tab lock is a switch in its own small Tab strip section ABOVE Tab widgets, where the strip's gear lands
        self.assertIn("<div class='rs-sec' data-section=tabstrip>Tab strip</div>", _pane(h, "chat"))
        self.assertLess(_pane(h, "chat").index("data-section=tabstrip"), _pane(h, "chat").index("data-section=tabwidgets"))
        self.assertIn("id=rs-tablock", _pane(h, "chat"))
        self.assertNotIn("data-pane=tabs", h)
        self.assertLess(h.index(">Diagnostics<"), h.index(">romp · version<"), "version last")

    def test_each_setting_sits_under_the_right_section(self):
        h = _gear_src()
        where = {
            "general": ["rs-billing", "rs-login-btn", "rs-panes-sec", "rs-pane-timeline", "rs-pane-fleet", "rs-pane-feed", "rs-filesctl", "rs-theme", "rs-cmap", "rs-pal", "rs-fileedit", "rs-conserve", "rs-updates"],
            "chat": ["rs-compact", "rs-dense", "rs-chatscheme", "rs-striprows", "rs-cmtmodel", "rs-cmteffort", "rs-cmtfast", "rs-thinksum", "rs-widgets", "rs-swidgets"],
            "feed": ["rs-feedcollapsed"],
            "sessions": ["rs-defaultdir", "rs-backend"],
            "automation": ["rs-autonudge", "rs-suggestcompact", "rs-alwaysfast", "rs-retryupgrade"],   # the two model switches: kernel policies applied to sessions on the kernel's own initiative (2026-09-17)
            "tasks": ["rs-tasktrack", "rs-judgemodel", "rs-judgefast", "rs-judgeeffort", "rs-distillmodel", "rs-distillfast", "rs-distilleffort", "rs-indexmodel", "rs-indexfast", "rs-indexeffort", "rs-judgeconc"],
            "debug": ["rs-judges-index", "rs-judges-triage", "ra-open", "rs-log-open", "rsver"],
        }
        panes = {k: _pane(h, k) for k in self.PANES}
        for pane, ids in where.items():
            for rid in ids:
                homes = [k for k in self.PANES if ("id=%s " % rid) in panes[k] or ("id=%s>" % rid) in panes[k] or ("id=%s " % rid).rstrip() + "\n" in panes[k]]
                self.assertEqual(homes, [pane], "%s lives in %s alone (found in %r)" % (rid, pane, homes))
        # the keyboard-shortcuts rows ride the SHORTCUT_ROWS variable into the General pane (T400)
        self.assertIn("+ SHORTCUT_ROWS +", panes["general"])
        # Panes (the user 2026-09-10): three rows, one hint each, Sessions before Outline before Feed; the chat is required, Files keeps its rail toggle
        pn = panes["general"]   # the Panes section moved to General (T400)
        self.assertEqual(pn.count('<label class="rs-row rs-panes-row">'), 4)   # Sessions, Outline, Feed, and the Files row since the T404 tidy
        self.assertLess(pn.index("<b>Sessions</b>"), pn.index("<b>Outline</b>"))
        self.assertLess(pn.index("<b>Outline</b>"), pn.index("<b>Feed</b>"))
        self.assertNotIn("id=rs-pane-chat", h, "the chat is required")
        self.assertNotIn("id=rs-pane-files", h, "the Files pane keeps its rail toggle")
        # Automation (T404): the two nudges in their order, then the Model section with its two switches (2026-09-17); Task tracking: the judge tiers alone
        am = panes["automation"]
        self.assertTrue(am.index(">Nudges<") < am.index("id=rs-autonudge") < am.index("id=rs-suggestcompact") < am.index("<div class='rs-sec'>Model</div>") < am.index("id=rs-alwaysfast") < am.index("id=rs-retryupgrade"))
        au = panes["tasks"]
        self.assertTrue(au.index(">Task tracking<") < au.index("id=rs-tasktrack") < au.index(">Judges<") < au.index("id=rs-judgemodel") < au.index("id=rs-indexeffort") < au.index("id=rs-judgeconc"))   # the master switch first (T404 PR 2)
        for gone in ("id=rs-autonudge", "id=rs-conserve", "id=rs-thinksum", ">Sessions<"):
            self.assertNotIn(gone, au, gone + " left Task tracking (T404)")
        # General (T404): the login leads, then the panes with the Files control, Appearance, Permissions (Allow file editing), This machine
        # (Conserve memory, Updates install automatically), then the shortcuts; Debug: the judges' debug views, then Open log as the last
        # button before the version (T290)
        ge = panes["general"]
        self.assertTrue(ge.index(">Account<") < ge.index("id=rs-login-btn") < ge.index("id=rs-panes-sec") < ge.index("id=rs-pane-feed") < ge.index("id=rs-filesctl")
                        < ge.index("data-section=appearance>Appearance<") < ge.index("id=rs-theme") < ge.index("id=rs-pal") < ge.index(">Permissions<") < ge.index("id=rs-fileedit")
                        < ge.index(">This machine<") < ge.index("id=rs-conserve") < ge.index("id=rs-updates") < ge.index(">Keyboard shortcuts<"))
        self.assertIn("<b>Allow file editing</b>", ge)
        self.assertIn("<b>Updates install automatically <span class=rs-mixed hidden></span></b>", ge)
        # Chat (T404): Display (the transcript rows, the text scheme, the strip's one-group-per-row), Comments, Thinking, Tab widgets
        ch = panes["chat"]
        self.assertTrue(ch.index(">Display<") < ch.index("id=rs-compact") < ch.index("id=rs-dense") < ch.index("id=rs-chatscheme") < ch.index("id=rs-striprows") < ch.index(">Comments<")
                        < ch.index("id=rs-cmtmodel") < ch.index("id=rs-cmtfast") < ch.index(">Thinking<") < ch.index("id=rs-thinksum") < ch.index("data-section=tabstrip") < ch.index("id=rs-tablock") < ch.index("data-section=tabwidgets")
                        < ch.index("data-section=statusline"))   # the Status line section follows Tab widgets (T409); the badge and branch checkboxes left Display for it
        for gone in ("id=rs-badge", "id=rs-branch"):
            self.assertNotIn(gone, ch, gone + " left the Chat tab: the Status line section's rows are the controls (T409)")
        for gone in ("id=rs-filelink", "File links open in", "id=rs-activeonly", "id=rs-collapsegaps", ">Sessions pane<", "data-pane=appearance"):
            self.assertNotIn(gone, h, gone + " is gone from the gear (T404)")
        de = panes["debug"]
        self.assertTrue(de.index(">Judging bands<") < de.index("id=rs-judges-index") < de.index(">Diagnostics<") < de.index("id=ra-open") < de.index("id=rs-log-open") < de.index("id=rsver"))
        self.assertNotIn(">Updates<", de, "Updates went to General's This machine section (T404)")
        self.assertNotIn("rs-oldest", h)
        # the old Context gauge row is gone: its WHEN is the Context bar widget's option in the Chat tab's Tab widgets section
        self.assertNotIn("id=rs-tabctx", h)


    def test_the_sdk_backend_is_labelled_plain_sdk(self):
        # the backends as the user reads them (T288, the user 2026-09-09): "Claude Code" (the default, no
        # qualifier — never "SDK" in copy a person reads) and "Codex"; the terminal backend is no longer offered
        # (T331, the user 2026-09-10: the tmux backend is being removed), and its gear switch is gone
        h = _gear_src()
        self.assertIn("<option value=sdk>Claude Code</option><option value=codex>Codex</option>", h)
        self.assertNotIn("Claude Code (tmux)", h)
        self.assertNotIn("rs-tmuxbackend", h)
        self.assertNotIn("headless", h)
        self.assertNotIn(">SDK<", h)
        self.assertNotIn("SDK runs via", h)
        self.assertNotIn("new SDK session", h)
        # T331: the terminal backend's offer switch and the set-aside note are gone from the gear; the Default backend
        # select is static and painted from the saved preference through the shared rule (a retired value reads as
        # Claude Code)
        self.assertNotIn("Enable Claude Code tmux backend", h)
        self.assertNotIn("id=rs-backend-note", h)
        self.assertNotIn("paintBackendOffer", h)
        self.assertIn("bk.value = BN.effectiveDefaultBackend(load().backend);", h)

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
        css = _gear_css_src()
        self.assertIn("#rsettings .rs-fastin.rs-off {", css)
        # the greyed look fades the BOX alone: opacity on the whole label faded the hint span inside it too, and made
        # the label a stacking context the rows beneath paint over, so the reason the box was greyed read dim and overdrawn
        self.assertIn("#rsettings .rs-fastin.rs-off input { opacity: .4;", css)
        self.assertNotRegex(css, r"\.rs-fastin\.rs-off \{[^}]*opacity", "no opacity on the label: the hint inside it would fade with it")
        self.assertRegex(css, r"\.rs-fastin\.rs-off \{[^}]*color: var\(--text-faint", "the word greys by token, not by fading")

    def test_the_sessions_pane_carries_its_two_toggles_and_the_gear_has_no_rows_for_them(self):
        # T404 (the user 2026-09-13): "Collapse idle gaps" and "Show active sessions only" left settings; the Sessions pane's own
        # corner menu flips them (ui/romp-timeline-view.js), writing the same romp:settings keys the gear used to, and the pane
        # re-reads them on the storage event, so the keys and their defaults are unchanged
        gear = _gear_src()
        for gone in ("id=rs-collapsegaps", "id=rs-activeonly", "s.collapseGaps = cg.checked", "s.activeOnly = ao.checked", ">Sessions pane<"):
            self.assertNotIn(gone, gear, gone + " is gone from the gear")
        view = (Path(__file__).resolve().parents[1] / "ui" / "romp-timeline-view.js").read_text()
        self.assertIn("item('Collapse idle gaps', { current: !!this._collapseGaps, dim: true })", view)
        self.assertIn(".addEventListener('click', () => flip('collapseGaps', this._collapseGaps));", view)
        self.assertIn("item('Active sessions only', { current: !!this._activeOnly, dim: true })", view)
        self.assertIn(".addEventListener('click', () => flip('activeOnly', this._activeOnly));", view)
        self.assertIn("s[key] = !cur;", view)   # the flip writes romp:settings
        self.assertIn("localStorage.setItem('romp:settings', JSON.stringify(s));", view)
        self.assertIn("if (raw) this._collapseGaps = JSON.parse(raw).collapseGaps !== false;", view)   # default ON, a stored false opts out
        self.assertIn("if (raw) this._activeOnly = JSON.parse(raw).activeOnly !== false;", view)

    def test_one_group_per_row_is_wired_to_the_shared_stripGroupRows_setting(self):
        # "One tag group per row in the tab strip": a Chat-section checkbox, default ON, persisted as
        # romp:settings.stripGroupRows. render.ts is the reader: the strip's row breaks and the trail's
        # boundary read it, and it rides the strip's rebuild signature so a flip repaints at once.
        self.assertIn("id=rs-striprows checked", _gear_src())
        self.assertIn("One tag group per row in the tab strip", _gear_src())
        self.assertEqual(_gear_src().count("stripGroupRows: true"), 2, "on in both of load()'s default literals")
        self.assertIn("s.stripGroupRows = sr.checked", _gear_src())
        self.assertIn("sr.checked = s.stripGroupRows !== false", _gear_src())

    def test_the_panes_section_is_wired_to_the_shared_panes_setting_and_is_the_dashboards_own(self):
        # the three boxes rewrite romp:settings.panes as a whole set (a missing key reads as shown, settings.ts
        # paneSet), the modal's open fills them from it, and the section is hidden off the dashboard's own page
        # (VS Code's panels have no dashboard shell to hide a pane from)
        h = _gear_src()
        self.assertIn("pn = { timeline: document.getElementById('rs-pane-timeline'), fleet: document.getElementById('rs-pane-fleet'), feed: document.getElementById('rs-pane-feed') }", h)
        self.assertIn("function panesOf(s)", h)
        self.assertIn("p[k] = pn[k].checked; s.panes = p; save(s);", h)
        self.assertIn("pn[k].checked = p[k]; }); })(panesOf(s));", h)
        self.assertIn("if (!ownPage) Array.prototype.forEach.call(document.querySelectorAll('#rs-panes-sec,.rs-panes-row'), function (el) { el.hidden = true; });", h)
        self.assertIn("#rsettings .rs-row[hidden], #rsettings .rs-sec[hidden] { display: none; }", _gear_css_src())

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


def _pane(h, key):
    """The markup of one pane: from its opener to the next pane's (or the login modal)."""
    a = h.index("'<div class=rs-pane data-pane=%s hidden>' +" % key)
    rest = h[a + 10:]
    nxt = rest.find("'<div class=rs-pane data-pane=")
    end = h.index("'<div id=rs-login-modal hidden>' +") if nxt < 0 else a + 10 + nxt
    return h[a:end]


def _gear_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()


def _gear_css_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.css").read_text()
