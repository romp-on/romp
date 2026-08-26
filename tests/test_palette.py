"""romp_palette — the selectable session-identity palettes (the user 2026-07-12).

The module is the single source of truth for every identity-color assigner: the kernel and the
SDK backend import it; bin/romp (shell) reads the kernel-maintained STATE/palette-colors mirror,
keeping only a FALLBACK copy of the default set, which these tests pin to the module so the two
can't drift. Curation invariant: every choosable set is 9 mid-tone, mutually distinct colors
(Crameri "S" palettes ship near-black/near-white entries for paper; those must never reach a tab).
"""
import os
import re
import tempfile
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pal = SourceFileLoader("romp_palette", os.path.join(BIN, "romp_palette.py")).load_module()

ROMP_BG = ["#1EA1EB", "#54B204", "#4EA8A9", "#DD42FF", "#E87221",
           "#98998A", "#F85B5A", "#F9D849", "#9088F0"]


def _lum(h):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return (0.2126 * ch(int(h[1:3], 16)) + 0.7152 * ch(int(h[3:5], 16))
            + 0.0722 * ch(int(h[5:7], 16)))


class PaletteShapes(unittest.TestCase):
    def test_every_palette_is_nine_unique_hex_colors_with_fg_words(self):
        for name, p in pal.PALETTES.items():
            self.assertEqual(len(p["bg"]), 9, name)
            self.assertEqual(len(p["fg"]), 9, name)
            self.assertEqual(len(set(p["bg"])), 9, "%s: duplicate swatch" % name)
            self.assertTrue(p["label"], name)
            for bg in p["bg"]:
                self.assertRegex(bg, r"^#[0-9A-F]{6}$", name)
            for fg in p["fg"]:
                self.assertIn(fg, ("black", "white"), name)

    def test_romp_is_the_default_and_leads_the_picker(self):
        self.assertEqual(pal.DEFAULT, "romp")
        self.assertEqual(next(iter(pal.PALETTES)), "romp")
        self.assertEqual(pal.PALETTES["romp"]["bg"], ROMP_BG, "the historical set is the default, unchanged")

    def test_pastel_is_the_soft_all_black_text_set(self):
        # the 2026-08-26 addition: a high-lightness identity set for anyone who finds the saturated
        # palettes loud across many tabs — soft by construction, so every fg must be black
        self.assertIn("pastel", pal.PALETTES)
        self.assertEqual(pal.PALETTES["pastel"]["fg"], ["black"] * 9)

    def test_crameri_and_cmocean_sets_are_curated_mid_tone(self):
        # The raw Crameri "S" orderings include near-black (#011959) and near-white entries — built for
        # white paper, unusable as identity chrome on the dark UI. Every shipped color must be mid-tone.
        for name, p in pal.PALETTES.items():
            for bg in p["bg"]:
                self.assertGreater(_lum(bg), 0.05, "%s %s: too dark for a session tab" % (name, bg))
                self.assertLess(_lum(bg), 0.72, "%s %s: too light for a session tab" % (name, bg))

    def test_fg_words_are_readable_on_their_swatch(self):
        # loose WCAG floor — the sets are hand-tuned (like the original), but never unreadable
        for name, p in pal.PALETTES.items():
            for bg, fg in zip(p["bg"], p["fg"]):
                L = _lum(bg)
                contrast = (L + 0.05) / 0.05 if fg == "black" else 1.05 / (L + 0.05)
                self.assertGreaterEqual(contrast, 2.2, "%s: %s text on %s" % (name, fg, bg))


class PaletteLookups(unittest.TestCase):
    def test_find_names_the_owning_palette_and_slot(self):
        self.assertEqual(pal.find("#1EA1EB"), ("romp", 0))
        self.assertEqual(pal.find(pal.PALETTES["phase"]["bg"][3]), ("phase", 3))
        self.assertIsNone(pal.find("#ABCDEF"))

    def test_fg_for_reads_the_owning_slot_or_defaults_white(self):
        self.assertEqual(pal.fg_for("#54B204"), "black")
        self.assertEqual(pal.fg_for("#ABCDEF"), "white")

    def test_colors_and_fgs_fall_back_to_the_default_set(self):
        self.assertEqual(pal.colors("no-such-palette"), ROMP_BG)
        self.assertEqual(len(pal.fgs("no-such-palette")), 9)


class ActiveName(unittest.TestCase):
    def test_missing_unknown_and_valid_choices(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(pal.active_name(td), "romp", "unset → default")
            (Path(td) / "palette").write_text("phase\n")
            self.assertEqual(pal.active_name(td), "phase")
            (Path(td) / "palette").write_text("garbage")
            self.assertEqual(pal.active_name(td), "romp", "unknown name → default")


class ShellFallbackSync(unittest.TestCase):
    def test_bin_romp_fallback_matches_the_default_palette(self):
        # bin/romp normally assigns from the kernel's STATE/palette-colors mirror; its hardcoded
        # FALLBACK (kernel never booted) must stay byte-identical to the module's default set.
        src = Path(BIN, "romp").read_text()
        m = re.search(r"_palette=\(\n(.*?)\)\n\s*_fg=\(\n(.*?)\)", src, re.S)
        self.assertIsNotNone(m, "bin/romp fallback palette block not found")
        self.assertEqual(re.findall(r'"(#[0-9A-F]{6})"', m.group(1)), ROMP_BG)
        self.assertEqual(re.findall(r'"(black|white)"', m.group(2)), pal.PALETTES["romp"]["fg"])

    def test_bin_romp_reads_the_kernel_mirror_first(self):
        src = Path(BIN, "romp").read_text()
        self.assertIn("palette-colors", src, "the launcher assigns from the kernel-maintained mirror")


if __name__ == "__main__":
    unittest.main()
