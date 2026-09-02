#!/usr/bin/env python3
"""The shell's LIGHT theme (body.theme-light) — pins for the landing page's warm-light skin.

The landing <body> gets class `theme-light` from its inline theme reader when the user
picks the light theme; the shell's <style> must then carry body.theme-light overrides
for the chrome (page/iframe backgrounds, pane rail, gutters, rail toggles), and each of
the three top-center banner CSS strings must carry a body.theme-light block of its own.
With the class absent none of these rules match, so dark rendering is untouched —
they are pure additive strings, which is what this test pins.

Synthetic only — reads module-level strings and the rendered landing HTML; no state.
"""
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic load, mirroring tests/test_kernel_cors.py.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()


class LightShell(unittest.TestCase):
    def setUp(self):
        self.html = km._landing()

    def test_page_background_goes_light(self):
        # the body override + the :has canvas rule for the html element
        self.assertIn("body.theme-light{--accent:#C2410C;--accent-fg:#FFF8F2;background:#F1EAE2}", self.html)
        self.assertIn("html:has(> body.theme-light){background:#F1EAE2}", self.html)

    def test_iframe_background_goes_light_scoped(self):
        # scoped to the class — the lifted modal iframes keep their id-scoped transparency
        self.assertIn("body.theme-light iframe{background:#F1EAE2}", self.html)

    def test_pane_rail_goes_light(self):
        self.assertIn("body.theme-light .pane-rail{background:#E7DED2;border-top-color:#DCD2C4}", self.html)

    def test_log_clear_hover_and_mtabs_divider_wear_the_warm_set(self):
        # the 2026-08-31 warm resweep missed these two: the old #E3DFD3 sat on the swept #E7DED2
        # ground at 1.001:1 — an invisible hover and a vanished divider
        self.assertIn("body.theme-light #rerr-clear:hover{background:#DED2C2;color:#1F1E1D}", self.html)
        self.assertIn("body.theme-light #mtabs .mtabs-div{background:#DCD2C4}", self.html)

    def test_gutters_go_light(self):
        self.assertIn("body.theme-light .gv{", self.html)
        self.assertIn("body.theme-light .gh{", self.html)
        self.assertIn("rgba(0,0,0,0.14)", self.html)

    def test_rail_toggle_on_goes_clay(self):
        # color restated too (2026-09-02): the light .rail-btn recolor outspecifies the bare
        # `.rail-btn.on`, so without it a selected toggle's TEXT stayed the resting grey
        self.assertIn(
            "body.theme-light .rail-btn.on{color:var(--accent);background:rgba(194,65,12,0.10);border-color:rgba(194,65,12,0.35)}",
            self.html)
        # same clash, the icon row: the bell's / network glyph's on-state was silenced entirely
        self.assertIn("body.theme-light .rail-act.on{color:var(--accent)}", self.html)

    def test_banner_css_strings_carry_light_blocks(self):
        for css in (km._STALE_CSS, km._UPD_CSS, km._RDRIFT_CSS):
            self.assertIn("body.theme-light ", css)
            self.assertIn("background:#FFFFFF", css)
            self.assertIn("rgba(0,0,0,0.12)", css)
            self.assertIn("color:#1F1E1D", css)

    def test_loader_dots_go_clay_under_light(self):
        self.assertIn("body.theme-light .rl-dots i{background:#C2410C}", km._LOADER_CSS)


if __name__ == "__main__":
    unittest.main()
