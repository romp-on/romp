#!/usr/bin/env python3
"""Cross-pane keyboard navigation (the user 2026-07-01), phase 1: Shift+Arrow jumps keyboard focus between the
shell's VISIBLE panes — spatially (Shift-Left/Right along the Chat/Outline/Feed columns, Shift-Down into the
timeline band, Shift-Up back out). Iframes can't focus each other, so this lives in the parent shell
(_LANDING_FOCUS_JS), which focuses the target iframe and posts {romp:'paneFocus'} so it can arm its own nav.
Source-pin the wiring (no headless DOM for the shell)."""
import os
import unittest
from importlib.machinery import SourceFileLoader
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_pn", os.path.join(BIN, "romp-kernel")).load_module()

JS = km._LANDING_FOCUS_JS


class PaneNav(unittest.TestCase):
    def test_spatial_layout_constants(self):
        self.assertIn("var COLS=['f-chat','f-fleet','f-feed','f-hive']", JS, "the three side-by-side columns, left->right")
        self.assertIn("var TL='f-timeline'", JS, "the timeline is the bottom band")

    def test_alt_arrow_is_the_trigger_only_outside_text_fields(self):
        # Alt(Option)+Arrow — NOT Shift (selects text), NOT Ctrl/Cmd (macOS Spaces / browser back-forward)
        self.assertIn("if(!e.altKey||e.shiftKey||e.ctrlKey||e.metaKey)return;", JS, "Alt+Arrow only (no other mods)")
        self.assertIn("{ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'}", JS)
        # inside a textarea/input Alt+Arrow must still word-jump, not move panes
        self.assertIn("if(editable(e.target))return;", JS)
        self.assertIn("tag==='textarea'||tag==='input'||tag==='select'||t.isContentEditable", JS)

    def test_move_is_spatial_and_skips_hidden_panes(self):
        self.assertIn("function moveFocus(dir)", JS)
        self.assertIn("function visCols(){return COLS.filter(paneVisible);}", JS, "only VISIBLE columns are traversed")
        self.assertIn("getComputedStyle(el).display!=='none'", JS, "hidden panes (display:none) are skipped")
        # left/right along columns, down into the timeline, up back out of it
        self.assertIn("if(dir==='left'){if(i>0)focusPane(cols[i-1],dir);}", JS)
        self.assertIn("else if(dir==='right'){if(i>=0&&i<cols.length-1)focusPane(cols[i+1],dir);}", JS)
        self.assertIn("else if(dir==='down'){if(paneVisible(TL))focusPane(TL,dir);}", JS)
        self.assertIn("if(curFocus===TL){", JS)

    def test_focus_goes_through_the_parent_and_notifies_the_pane(self):
        # the shell focuses the target iframe (siblings can't) + rings it + tells it to arm intra-pane nav
        self.assertIn("f.contentWindow.focus();", JS)
        self.assertIn("f.contentWindow.postMessage({romp:'paneFocus',dir:dir||'',from:'shell'},'*');", JS)
        # the handler is wired on each iframe's document in CAPTURE so it beats the pane's own key handlers
        self.assertIn("d.addEventListener('keydown',onKey,true);", JS)
        # AND on the top-level shell document, so the shortcut works when focus sits on the shell itself
        # (a keydown doesn't cross the iframe boundary) — else Alt+Left fell through to Firefox Back
        self.assertIn("document.addEventListener('keydown',onKey,true);", JS)


if __name__ == "__main__":
    unittest.main()
