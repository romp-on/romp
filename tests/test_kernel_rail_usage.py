"""The Claude /usage rate-limit bars moved from the timeline toolbar to the left RAIL (the user 2026-06-26),
to shrink the timeline. They live in a different document (the shell), so the timeline iframe POSTS its usage
data to the shell ({romp:'usage'}) and the shell renders compact vertical bar-pairs (used % colored + elapsed
% slate) under the refresh button, with the full detail on hover. Standalone (Obsidian) keeps its own copy.
"""
import inspect
import os
import pathlib
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


class RailUsage(unittest.TestCase):
    def setUp(self):
        self.html = km._landing()

    def test_the_rail_usage_leads_the_scroll_group_with_refresh_and_settings_pinned_right(self):
        # the user 2026-06-26/27; bottom bar 2026-07-05: usage sits in the scrollable group (after the toggles);
        # refresh + network + settings are in the FIXED .rail-acts pinned to the RIGHT, settings (⛭ rail-gear) last.
        self.assertIn("id=rail-usage", self.html, "a usage container sits in the rail")
        self.assertLess(self.html.index("id=rail-usage"), self.html.index("id=rail-refresh"),
                        "usage comes before refresh (scroll group precedes the actions)")
        self.assertLess(self.html.index("id=rail-refresh"), self.html.index("id=rail-gear"),
                        "refresh is before settings (settings is the far-right action)")
        self.assertIn(".rail-acts{flex:0 0 auto;display:flex;flex-direction:row;align-items:center;gap:6px;margin-left:auto}",
                      self.html, "the action group is pinned to the right of the bottom bar")
        self.assertIn(".ru-track{", self.html, "the horizontal bar track styling")
        self.assertIn(".ru-fill{", self.html, "the colored used-% fill styling")
        self.assertIn(".ru-pct{", self.html, "the percentage readout styling")

    def test_the_shell_renders_the_posted_usage_colormapped_with_a_hover_panel(self):
        self.assertIn("romp==='usage'", self.html, "the shell listens for the timeline's usage post")
        for win in ("fiveHour", "sevenDay"):
            self.assertIn(win, self.html, "renders both rate-limit windows")
        # the used bar wears the SELECTED COLORMAP colour (server-computed in _usage, read here as seg.color)
        self.assertIn("seg.color", self.html, "the used bar is colored by the selected colormap")
        self.assertIn("cm.ramp(pct / 100.0, stops)", inspect.getsource(km._usage),
                      "_usage maps used-% onto the global colormap")
        # ONE shared hover PANEL for BOTH windows (the user 2026-06-26): it reproduces the used/elapsed bars
        # that used to sit under the timeline, with the reset countdown, and NO explanatory prose.
        self.assertIn("#ru-tip{", self.html, "a styled hover tooltip panel")
        self.assertIn("resets in", self.html, "the panel includes the reset countdown")

    def test_each_usage_window_has_two_stacked_horizontal_bars(self):
        # the user 2026-07-05: each window is one ROW — an expanded label, then TWO stacked horizontal tracks
        # (used-% colormap fill OVER elapsed-% slate fill), then the used-% readout. Bars are ~75% as wide (54px).
        self.assertIn(".ru-w{display:flex;flex-direction:row;align-items:center;gap:7px;cursor:default}", self.html)
        self.assertIn(".ru-bars{display:flex;flex-direction:column;gap:2px", self.html, "the two bars stack vertically")
        self.assertIn(".ru-track{position:relative;width:54px;height:5px", self.html, "narrower horizontal track")
        self.assertIn(".ru-fill{position:absolute;left:0;top:0;height:100%", self.html, "the fill grows in WIDTH")
        self.assertNotIn(".ru-mark{", self.html, "the single-track pace tick is gone (two bars now)")
        # both fills render: used-% (colormap col) on top, elapsed-% (slate #6b7a8c) below. Since
        # 2026-08-08 the drawn values come from the AGGREGATE (the worst known reading per window
        # across every host — aggBarsHTML's `best`), never per-host rows.
        self.assertIn("ru-fill style=\"width:'+best.pct+'%;background:'+best.col", self.html, "the used-% bar")
        self.assertIn("ru-fill style=\"width:'+(best.tp||0)+'%;background:#6b7a8c", self.html, "the elapsed-% bar below it")
        # order within a window: label, then the bars, then % — all inline
        # anchor past the spend chip (it wears ru-name/ru-pct too, with no bars — the user 2026-08-04):
        # the slice must start at a WINDOW row, whose label is built from the WINS table
        one = self.html[self.html.index("<div class=ru-name>'+w"):]
        self.assertLess(one.index("ru-name"), one.index("ru-bars"))
        self.assertLess(one.index("ru-bars"), one.index("ru-pct"))
        # expanded labels use the 5th WINS field (plenty of horizontal room)
        self.assertIn("'5 hours'", self.html)
        self.assertIn("'7 days'", self.html)

    def test_the_usage_tooltip_is_one_shared_panel_reproducing_both_windows_bars(self):
        # a SINGLE tooltip on the whole rail-usage area (mouseenter on el), not a per-window panel
        self.assertIn("el.addEventListener('mouseenter',showTip)", self.html, "one shared tooltip for the area")
        self.assertIn("['fiveHour','sevenDay','fable'].filter", self.html, "the tooltip covers ALL windows at once")
        # it reproduces the used + elapsed bars (the exact set that used to sit under the timeline)
        self.assertIn("ru-tip-track", self.html, "horizontal used/elapsed bars in the tooltip")
        self.assertIn(">used<", self.html)
        self.assertIn(">elapsed<", self.html)
        # and drops the old explanatory prose ("...rate-limit window") — no extra stuff
        self.assertNotIn("rate-limit window", self.html, "no explanatory prose, just the bars + %")

    def test_the_spend_section_owns_its_age_and_never_speaks_for_rate_limits(self):
        # Pins from the 2026-08-24 spend-staleness screenshot — minus the telemetry note, which the
        # user later the same day had DELETED entirely (they know which machines are key-only; no
        # notice about rate limits that don't apply):
        # 1. the notice is gone from the whole page;
        # 2. the spend section ends with its OWN age line, from the newest contributor's
        #    last-record moment (event time, not a poll time);
        # 3. a FAILED fleet pull re-renders from the cached rows, so every age line keeps climbing
        #    instead of freezing at a quietly lying "3m ago".
        self.assertNotIn("rate-limit telemetry unavailable", self.html)
        self.assertNotIn("_telemUnavail", self.html)
        self.assertIn("if(sAt)h+='<div class=ru-tip-age>last charge recorded '+fmtAgo(sAt)+'</div>';", self.html)
        self.assertIn("if(typeof u.spendAt==='number')det._spendAt=u.spendAt;", self.html)
        self.assertIn("pullFleet().then(done,function(){if(ROWS.length)renderRows(ROWS,SELF);done();});", self.html)
        # the kernel side: the payload stamps spend.json's own mtime on both spend-attaching arms
        ksrc = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertEqual(ksrc.count('out["spendAt"] = sa'), 2, "the key-only arm and the mixed-host arm")

    def test_the_tooltip_shows_the_snapshots_age(self):
        # "updated ... ago" (the user 2026-07-02): the bars lagged the CLI's own /usage with no cue the reading
        # was old — usage.json refreshes only when a statusline render or a rate-limit event produces a NEW
        # reading, so staleness must be VISIBLE, not silent. The footer formats usage.json's `t`.
        # (2026-07-30: the snapshot time moved into the per-account detail object; 2026-08-08: the
        # detail is per HOST now, one object per fleet row — the footer itself is unchanged.)
        self.assertIn("det._t=(typeof r.usage.t==='number')?r.usage.t:null", self.html,
                      "the renderer keeps each host's snapshot time")
        self.assertIn("ru-tip-age", self.html, "the tooltip carries an age footer")
        self.assertIn("updated '+fmtAgo(d._t)", self.html, "formatted as 'updated ... ago'")
        self.assertIn("function fmtAgo(ep)", self.html)

    def test_the_fable_window_is_a_third_bar_everywhere(self):
        # the included Fable 5 weekly allowance (the user 2026-07-02): a third bar in the rail, a third
        # window in the tooltip, and a third pair on the timeline's standalone toolbar copy
        import inspect
        self.assertIn("['fable',7*86400,'Fable 5']", self.html, "the rail renders a Fable 5 bar (its ONE display name — the user 2026-08-09)")
        self.assertIn("['fiveHour','sevenDay','fable'].filter", self.html, "the tooltip covers it")
        self.assertIn('"fable": fable', inspect.getsource(km._usage), "_usage serves the fable window")
        tv = (pathlib.Path(BIN).parent / "ui" / "romp-timeline-view.js").read_text()
        self.assertIn("mkUsageBar('fable', 'Fable 5', 7 * 86400)", tv)
        self.assertIn("apply('fable', usage.fable, 'Fable 5 (7d)')", tv)

    def test_a_rolled_window_reads_unknown_not_zero(self):
        # the user 2026-07-31: a second account's bars sat at a confident 0% because that machine's
        # kernel had no live session to ask, so its snapshot predated its own reset — an unreadable
        # window drawn as an empty one. A reading whose window has already reset describes a window
        # nobody is in any more: it is UNKNOWN. The last-known fill stays but FADES, the readout is
        # '?', the elapsed (pace) bar is withheld, and the tooltip dates the gap.
        self.assertIn("var rolled=!!(seg.resetsAt&&nowS>seg.resetsAt),pct=Math.max(0,Math.min(100,seg.pct||0));",
                      self.html, "the reading survives the roll — it is last-known, not zeroed")
        self.assertIn("var tp=(!rolled&&seg.resetsAt&&w[1])", self.html,
                      "no pace bar against a window that already ended")
        # NOT DRAWN at all (the user 2026-08-13; supersedes the 2026-07-31 '?' slot): the bar shows
        # only what we know — an all-unknown window contributes no row. The last-known number keeps
        # living in the tooltip, labelled as such (the pins below).
        self.assertIn("if(d.unk)return;", self.html,
                      "a rolled reading never competes as a value in the aggregate")
        self.assertIn("if(!best)return;", self.html, "no known reading → no row at all")
        self.assertNotIn("ru-qmark", self.html, "the '?' slot is gone from the bar")
        self.assertNotIn(".ru-unk .ru-fill{opacity:.3}", self.html, "no faded fill on the face any more")
        self.assertIn("<span class=ru-tip-k>last known</span>", self.html,
                      "the hover is the ONE place the stale number appears, and it says what it is")
        self.assertIn("window reset '+esc(v.ago)", self.html, "the tooltip dates how stale the reading is")
        # "no reading since" carries the whole fact; "current usage unknown" restated what the '?' and
        # the "last known" label already say (the user 2026-08-08 de-inking pass)
        self.assertIn("; no reading since", self.html)
        self.assertNotIn("current usage unknown", self.html)
        # the STANDALONE timeline copy (Obsidian) says the same thing — one rule, every surface
        tv = (pathlib.Path(BIN).parent / "ui" / "romp-timeline-view.js").read_text()
        self.assertIn("const rolled = !!(seg.resetsAt && nowS > seg.resetsAt);", tv)
        self.assertIn("b.usage.txt.textContent = rolled ? '?' : pct + '%';", tv)
        self.assertIn("if (track) track.style.display = rolled ? 'none' : '';", tv,
                      "the bar itself is hidden, not faded")
        self.assertIn("; no reading since (last known ", tv)
        self.assertNotIn("current usage unknown", tv)

    def test_the_timeline_forwards_usage_to_the_shell_and_hides_its_own_copy_when_embedded(self):
        tv = (pathlib.Path(BIN).parent / "ui" / "romp-timeline-view.js").read_text()
        # only when embedded (window.parent !== window) — standalone/Obsidian keeps drawing them locally
        self.assertIn("window.parent !== window", tv)
        self.assertIn("romp: 'usage'", tv, "the timeline posts its usage data to the shell")
        self.assertIn("this._usageWrap.style.display = 'none'", tv, "and hides its own toolbar copy")


if __name__ == "__main__":
    unittest.main()
