#!/usr/bin/env python3
"""A message connector is hoverable along its WHOLE path (ui/romp-timeline-view.js).

The user (2026-07-21): "when I hover over the vertical part of the message connector it doesn't pop
up the tooltip — I have to hit the horizontal part or the dot itself", and an immediately-delivered
message is almost ENTIRELY vertical, so there was nothing else to aim at.

Cause was z-order, not geometry. The connector's invisible hit path was appended in the same pass that
drew the line, i.e. BEFORE the per-message arrival dots. A connector's vertical runs start and end AT
the lanes — exactly where those dots sit — so the dots covered most of the vertical and the hover
landed on whichever dot was on top instead of the connector.

The fix appends the hit paths in a final pass, after the arrival dots and before the prompt dots.
Nothing is lost by a hit path sitting over a message's own dot (same tooltip, same click, and the
connector's mouseenter grows the linked dot); the prompt dots are drawn afterwards so they keep their
own hover. These pin that ordering contract plus the hit-stroke shape, since a regression here is
invisible in review and only shows up as a tooltip that will not open.

Source-level pins (the repo's existing pattern for view-layer ordering): draw() has no test harness,
so the invariant is checked against the source rather than a fake DOM.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
VIEW_JS = os.path.join(os.path.dirname(HERE), "ui", "romp-timeline-view.js")


class TimelineMessageHover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(VIEW_JS, encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_hit_path_not_appended_in_the_connector_pass(self):
        """The connector pass builds the hit target but must NOT append it (that put it under the dots)."""
        # the PASS 1 connector body: from its marker to the dot helper that follows it
        start = self.src.index("PASS 1: connector line + highlight")
        end = self.src.index("dot helper: optional onClick")
        pass1 = self.src[start:end]
        self.assertIn("u.hit = hit", pass1, "the connector pass should hand its hit target to the final pass")
        self.assertNotIn(
            "svg.appendChild(hit)", pass1,
            "appending the hit path in the connector pass buries it under every dot drawn afterwards, "
            "which is exactly the vertical-run hover bug",
        )

    def test_hit_paths_are_appended_after_the_arrival_dots(self):
        """Z-order contract: arrival dots (PASS 2) → connector hit targets (PASS 3) → prompt dots."""
        i_dots = self.src.index("PASS 2: message arrival dots")
        i_hits = self.src.index("PASS 3: the connector hit targets")
        i_prompt = self.src.index("turn process-start (prompt) dots")
        self.assertLess(i_dots, i_hits, "hit targets must be appended AFTER the arrival dots to win the hover")
        self.assertLess(i_hits, i_prompt, "prompt dots are appended last so they keep their own hover")
        # the final pass actually appends the stashed hit targets
        tail = self.src[i_hits:i_prompt]
        self.assertRegex(tail, r"svg\.appendChild\(u\.hit\)", "the final pass must append each stashed hit path")

    def test_hit_stroke_is_wide_and_round_capped(self):
        """A short vertical run needs a generous, fully-covering target."""
        m = re.search(r"const MSG_HIT_W\s*=\s*(\d+)", self.src)
        self.assertIsNotNone(m, "MSG_HIT_W should name the connector hit width")
        self.assertGreaterEqual(int(m.group(1)), 14, "hit stroke must stay at least as generous as the old 14px")
        # the hit path itself: transparent stroke, MSG_HIT_W, round cap/join so it reaches the path ends
        hit_decl = re.search(r"const hit = el\('path',\s*\{[^}]*\}", self.src)
        self.assertIsNotNone(hit_decl, "the connector hit path declaration should be findable")
        decl = hit_decl.group(0)
        self.assertIn("MSG_HIT_W", decl)
        self.assertIn("'stroke-linecap': 'round'", decl, "butt caps stop the hit short of the path ends")
        self.assertIn("'stroke-linejoin': 'round'", decl)

    def test_leave_test_pads_by_the_hit_stroke(self):
        """The sweep must measure the owner's HIT extent, not its bare geometry box.

        getBoundingClientRect on an SVG path measures the outline and Chrome excludes the stroke, so a
        connector for an immediately-delivered message — a straight vertical line — reports a box 0px
        WIDE. Comparing the pointer against that box unpadded meant every sub-pixel twitch of a real
        hand read as "left the glyph" and hid the tip, which then only returned on the next redraw: the
        tip appeared, vanished, and came back only if the cursor was held perfectly still (the user
        2026-07-21). Firefox measures these boxes differently, hence no flicker there. Measured over a
        12s hold on a live timeline: 12 hide/show cycles and 704ms hidden before, 0 and 0ms after.
        """
        sweep = self.src[self.src.index("this._onTipSweep = (e) => {"):self.src.index("this.wrap.addEventListener('mousemove', this._onTipSweep);")]
        self.assertRegex(
            sweep, r"const pad = \(parseFloat\(o\.getAttribute && o\.getAttribute\('stroke-width'\)\) \|\| 0\) / 2",
            "the leave test needs the owner's stroke to know its real extent",
        )
        for edge in ("r.left - pad", "r.right + pad", "r.top - pad", "r.bottom + pad"):
            self.assertIn(edge, sweep, "every edge of the leave test must be padded by the hit stroke")

    def test_a_stale_tip_is_rebound_rather_than_left_or_dropped(self):
        """A redraw detaches the tip's owner; the tip must rebind to the fresh glyph, not wait for a draw."""
        sweep = self.src[self.src.index("this._onTipSweep = (e) => {"):self.src.index("this.wrap.addEventListener('mousemove', this._onTipSweep);")]
        self.assertIn(
            "if (!o || !o.isConnected) { this.hideTip(); this._rehover(); return; }", sweep,
            "a destroyed owner should rebind at once instead of waiting for the next redraw",
        )
        rehover = self.src[self.src.index("  _rehover() {"):self.src.index("  // Deep-link a click on a timeline item")]
        self.assertIn(
            "if (this.tip.classList.contains('show') && this._tipOwner && this._tipOwner.isConnected) return;", rehover,
            "skip only a tip whose owner SURVIVED the rebuild; a shown-but-detached tip is stale",
        )

    def test_hover_still_lights_the_connector_and_its_dot(self):
        """The hit target keeps co-highlighting the line + linked dot and stays re-armable after a redraw."""
        start = self.src.index("PASS 1: connector line + highlight")
        end = self.src.index("dot helper: optional onClick")
        pass1 = self.src[start:end]
        self.assertIn("hit.__tlHoverIn = mEnter", pass1, "the hit must stay re-armable after draw() rebuilds it")
        # the co-highlight routes through the overlap-hover set since 2026-08-24 (hovering lists
        # every message under the point): the grow lives in msgSetLight's one formula, which the
        # connector's mEnter reaches for its whole hovered set — the invariant (hovering the line
        # grows its arrival dot) holds through it
        self.assertRegex(pass1, r"msgSetLight\(u\.hoverSet, true\)", "hovering lights the hovered set")
        self.assertRegex(self.src, r"u\.dot\.setAttribute\('r', \(on \|\| u\.lit\) \? DOT_R \+ 2 : DOT_R\)",
                         "…whose one formula grows the arrival dot")


if __name__ == "__main__":
    unittest.main()
